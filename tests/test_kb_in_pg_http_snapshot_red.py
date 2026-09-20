"""知识库统一维护在 Postgres（cpq_kb）+ 技术工艺经 HTTP 快照读取：Spec / Red。

契约见 docs/specs/kb-in-pg-http-snapshot.md。今天的事实是反的：

  · 知识库 kb_* 只存在技术工艺本地 SQLite（tech_app/backend/config.py:181、
    tech_app/backend/storage/da_db.py:4）；CPQ 侧既没有 cpq_kb，也没有任何读接口；
  · 本地那份 da.db 的知识库整库为 0 行，匹配必然 0 候选 —— 9/14 冰箱项目的
    tech_app/tech_data/c7e71a8ece61/component_match.json 里写着 "library_size": 0，
    6 个零件全部 decision="new"、candidates=[]；
  · kb_repo 直接查询本地 SQLite，"桥断了"和"库里真没有可复用零件"产出的结果形状完全
    一样（都是"未匹配"），没有任何东西能把两者区分开；
  · 平台没有自动灌种子（da_seed / da_mock 只在 python -m 与测试里被调用），换一次数据
    目录知识库就归零，而且不报错。

本批建立的契约（摘要）：
  C1 PG schema cpq_kb：kb_* 20 张表 1:1 + kb_meta；
  C2 kb_version 是快照失效的唯一依据；
  C3 scripts/import_da_kb_to_pg.py：默认 dry-run，源库只读打开（不动 -wal/-shm），幂等，单事务；
  C4 GET /wf/tech/kb/snapshot（只认 X-Internal-Token，支持 since）：PG 挂要 503 而不是空表；
  C5 tech_app/backend/services/cpq_kb_client.py：只走 HTTP，失败抛 KbUnavailable；
  C6 kb_repo 数据源换成快照缓存，函数签名与判定口径（TOLERANCE/WEIGHTS/THRESHOLD）逐字不变；
  C7 快照不可用时必须抛错，不得产出 library_size: 0 的"未匹配"报告；
  C8 tech_app 不直连 PG；同一份数据下 PG 快照路径与 SQLite 路径逐零件结果一致。

验证方式：
  A. 源码契约（DDL 表名、路由、客户端、导入器、边界）；
  B. 导入器真跑：用夹具在临时目录重建源库 → `--dry-run --json`，校验统计与源文件未被改动；
  C. 子进程真起一体化服务（假 cpq_kb 存储），打 /wf/tech/kb/snapshot 的真请求；
  D. 子进程真起技术工艺侧：假 CPQ 快照服务 + 空 SQLite 知识库，验证取数走 HTTP 快照；
  E. 一致性：13 个既有零件走快照路径的命中必须与 SQLite 口径逐条一致（golden 夹具）。
"""
from __future__ import annotations

import functools
import hashlib
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs" / "specs" / "kb-in-pg-http-snapshot.md"
CPQ_KB_PY = ROOT / "cpq_kb.py"
CPQ_SUITE_PY = ROOT / "cpq_suite_server.py"
IMPORTER_PY = ROOT / "scripts" / "import_da_kb_to_pg.py"
KB_REPO_PY = ROOT / "tech_app" / "backend" / "storage" / "kb_repo.py"
KB_CLIENT_PY = ROOT / "tech_app" / "backend" / "services" / "cpq_kb_client.py"
COMPONENT_MATCH_PY = ROOT / "tech_app" / "backend" / "services" / "component_match.py"
DA_SCHEMA_SQL = ROOT / "tech_app" / "backend" / "storage" / "da_schema.sql"
SNAPSHOT_FIXTURE = ROOT / "tests" / "fixtures" / "cpq_kb_snapshot_20260916.json"
PARTS_FIXTURE = ROOT / "tests" / "fixtures" / "cpq_kb_parts_golden_20260916.json"

INTERNAL_TOKEN = "kb-internal-token-1"

# kb_* 全表（da_schema.sql 的 20 张）
KB_TABLES = (
    "kb_component", "kb_component_drawing", "kb_component_embedding",
    "kb_component_feature", "kb_component_param", "kb_cost_factor", "kb_cost_rate",
    "kb_equipment", "kb_equipment_class", "kb_inspection_item", "kb_material",
    "kb_material_price", "kb_material_property", "kb_process_param_template",
    "kb_process_route", "kb_process_route_step", "kb_process_step",
    "kb_standard_part", "kb_supplier", "kb_supplier_capability",
)


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def read_if_exists(path: pathlib.Path) -> str:
    return read(path) if path.exists() else ""


def snapshot_fixture() -> dict:
    return json.loads(read(SNAPSHOT_FIXTURE))


def parts_fixture() -> dict:
    return json.loads(read(PARTS_FIXTURE))


def child_python() -> str:
    """能同时 import fastapi/httpx/cpq_suite_server 的解释器（本机为 open-claude/.venv）。"""
    candidates = [str(ROOT / "open-claude" / ".venv" / "bin" / "python"),
                  sys.executable, shutil.which("python3"), shutil.which("python")]
    probe_code = ("import sys; sys.path.insert(0, %r); "
                  "import fastapi, httpx, cpq_suite_server" % str(ROOT))
    for candidate in candidates:
        if not candidate or not pathlib.Path(candidate).exists():
            continue
        probe = subprocess.run([candidate, "-c", probe_code], cwd=str(ROOT),
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate
    return ""


def run_child(source: str, *args: str, timeout: int = 300) -> dict:
    """把走查脚本落到临时目录，用带依赖的解释器在仓库根跑一次，取最后一行 JSON。"""
    python = child_python()
    if not python:
        raise unittest.SkipTest("没有能 import fastapi/httpx/cpq_suite_server 的解释器")
    script_dir = pathlib.Path(tempfile.mkdtemp(prefix="cpq-kb-script-"))
    script = script_dir / "child.py"
    script.write_text(source, encoding="utf-8")
    completed = subprocess.run([python, str(script), *args], cwd=str(ROOT),
                               capture_output=True, text=True, timeout=timeout)
    if completed.returncode != 0:
        raise AssertionError("走查子进程失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                             % (completed.returncode, completed.stdout[-3000:],
                                completed.stderr[-4000:]))
    return json.loads(completed.stdout.strip().splitlines()[-1])


def sha256_of(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_source_db(dest: pathlib.Path) -> dict:
    """把夹具还原成一份 SQLite 源库（模拟 da.db），供导入器红测使用。"""
    schema = read(DA_SCHEMA_SQL)
    snap = snapshot_fixture()
    conn = sqlite3.connect(str(dest))
    try:
        conn.executescript(schema)
        # 夹具是按表名顺序还原的，插入顺序不保证满足外键；这里只做数据搬运，关掉校验。
        conn.execute("PRAGMA foreign_keys = OFF")
        for table, rows in snap.items():
            if not rows:
                continue
            columns = sorted({key for row in rows for key in row})
            placeholders = ",".join("?" for _ in columns)
            sql = "INSERT INTO %s (%s) VALUES (%s)" % (table, ",".join(columns), placeholders)
            for row in rows:
                conn.execute(sql, [row.get(col) for col in columns])
        conn.commit()
    finally:
        conn.close()
    return snap


# --------------------------------------------------------------------------- #
# A. 源码契约
# --------------------------------------------------------------------------- #
class TestSourceContract(unittest.TestCase):
    def test_01_spec_exists_and_pins_the_contract(self):
        text = read_if_exists(SPEC)
        self.assertTrue(text, "缺少 docs/specs/kb-in-pg-http-snapshot.md")
        for token in ("cpq_kb", "kb_version", "/wf/tech/kb/snapshot",
                      "import_da_kb_to_pg.py", "cpq_kb_client.py", "KbUnavailable"):
            self.assertIn(token, text, "spec 未钉住 %s" % token)

    def test_02_cpq_kb_module_and_entry_points(self):
        text = read_if_exists(CPQ_KB_PY)
        self.assertTrue(text, "缺少 CPQ 侧 cpq_kb.py（PG schema/快照/导入）")
        for name in ("ensure_schema", "snapshot", "import_from_sqlite", "kb_version"):
            self.assertIn("def %s" % name, text, "cpq_kb.py 缺 %s" % name)
        self.assertIn("kb_meta", text, "cpq_kb.py 未建 kb_meta（版本表）")

    def test_03_cpq_kb_ddl_covers_all_kb_tables(self):
        text = read_if_exists(CPQ_KB_PY)
        self.assertTrue(text, "缺少 CPQ 侧 cpq_kb.py")
        missing = [t for t in KB_TABLES if t not in text]
        self.assertEqual([], missing, "cpq_kb DDL 缺表：%s" % missing)

    def test_04_suite_server_exposes_snapshot_route_with_internal_token(self):
        text = read(CPQ_SUITE_PY)
        self.assertIn("/wf/tech/kb/snapshot", text, "一体化服务没有 kb 快照路由")
        self.assertIn("X-Internal-Token", text)
        # 路由必须只认内部令牌：出现内部令牌校验的调用
        self.assertTrue(re.search(r"_internal_token_ok\s*\(", text),
                        "快照路由没有用内部令牌校验（fail-closed）")

    def test_05_tech_app_client_is_http_only_and_fails_loudly(self):
        text = read_if_exists(KB_CLIENT_PY)
        self.assertTrue(text, "缺少 tech_app/backend/services/cpq_kb_client.py")
        self.assertIn("X-Internal-Token", text)
        self.assertIn("CPQ_INTERNAL_TOKEN", text)
        self.assertIn("class KbUnavailable", text)
        self.assertIn("def fetch_snapshot", text)
        self.assertNotIn("psycopg", text, "技术工艺侧客户端不得直连 Postgres")

    def test_06_importer_exists_with_safe_defaults(self):
        text = read_if_exists(IMPORTER_PY)
        self.assertTrue(text, "缺少 scripts/import_da_kb_to_pg.py")
        self.assertIn("--confirm", text)
        self.assertTrue(re.search(r"dry[-_]run", text), "导入器缺少 dry-run 语义")
        self.assertTrue(re.search(r"mode=ro", text),
                        "导入器必须只读打开源库（mode=ro），否则会 checkpoint 并删掉 -wal/-shm")

    def test_07_kb_repo_no_longer_reads_local_sqlite(self):
        """读路径必须整体搬走：kb_repo 里不允许再出现任何 SELECT ... FROM kb_*。

        写路径（save_*）保留给遗留种子脚本 da_seed / da_seed_battery / da_mock
        （da_mock.py:1484-1561、da_seed.py:406-481 都在调 kb.save_*），运行时不走它。
        """
        offenders = [line.strip() for line in read(KB_REPO_PY).splitlines()
                     if "SELECT" in line.upper() and "kb_" in line]
        self.assertEqual([], offenders, "kb_repo 仍在用 SQL 读知识库：%s" % offenders[:3])
        text = read(KB_REPO_PY)
        self.assertIn("refresh_kb", text, "kb_repo 缺少快照缓存的刷新入口")
        self.assertTrue("fetch_snapshot" in text or "cpq_kb_client" in text,
                        "kb_repo 没有接上 CPQ 快照客户端")

    def test_08_scoring_rule_untouched(self):
        text = read(KB_REPO_PY)
        for token in ("ENVELOPE_TOLERANCE = 0.20", "MATCH_THRESHOLD = 0.35",
                      "WEIGHT_ENVELOPE = 0.25", "WEIGHT_PARAM = 0.35", "WEIGHT_FEATURE = 0.40"):
            self.assertIn(token, text, "判定口径被改动：%s" % token)

    def test_09_component_match_surfaces_kb_unavailable(self):
        text = read(COMPONENT_MATCH_PY)
        self.assertIn("KbUnavailable", text,
                      "匹配侧没有把'知识库不可用'与'未匹配'分开")

    def test_10_tech_app_never_imports_psycopg(self):
        offenders = []
        for path in (ROOT / "tech_app").rglob("*.py"):
            body = read_if_exists(path)
            if "import psycopg" in body:
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual([], offenders, "技术工艺不得直连 Postgres：%s" % offenders)


# --------------------------------------------------------------------------- #
# B. 导入器：dry-run 统计 + 源库只读
# --------------------------------------------------------------------------- #
class TestImporterDryRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.work = pathlib.Path(tempfile.mkdtemp(prefix="cpq-kb-import-"))
        cls.source = cls.work / "da.db"
        cls.snap = build_source_db(cls.source)
        cls.before_sha = sha256_of(cls.source)
        cls.before_mtime = cls.source.stat().st_mtime_ns
        cls.result = None
        cls.raw = ""
        python = child_python() or sys.executable
        if not IMPORTER_PY.exists():
            return
        completed = subprocess.run(
            [python, str(IMPORTER_PY), "--source", str(cls.source), "--dry-run", "--json"],
            cwd=str(ROOT), capture_output=True, text=True, timeout=180)
        cls.raw = (completed.stdout or "") + (completed.stderr or "")
        for line in reversed((completed.stdout or "").strip().splitlines()):
            try:
                cls.result = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    def test_11_importer_script_exists(self):
        self.assertTrue(IMPORTER_PY.exists(), "缺少 scripts/import_da_kb_to_pg.py")

    def test_12_dry_run_reports_every_table_and_rows(self):
        self.assertIsNotNone(self.result, "导入器没有输出 JSON 统计：%s" % self.raw[-800:])
        self.assertTrue(self.result.get("dry_run"), "未给 --confirm 时必须 dry_run=true")
        tables = self.result.get("tables") or {}
        expected = {t: len(self.snap.get(t) or []) for t in KB_TABLES}
        self.assertEqual(expected, {k: tables.get(k) for k in expected},
                         "导入器统计与源库不一致")
        self.assertEqual(613, self.result.get("rows"), "总行数应为 613")

    def test_13_source_file_untouched_by_dry_run(self):
        self.assertEqual(self.before_sha, sha256_of(self.source), "dry-run 改动了源库内容")
        self.assertEqual(self.before_mtime, self.source.stat().st_mtime_ns, "dry-run 改动了源库 mtime")
        self.assertFalse((self.work / "da.db-wal").exists() and
                         (self.work / "da.db-wal").stat().st_size > 0,
                         "dry-run 在源库上产生了 WAL（说明用了可写连接）")


# --------------------------------------------------------------------------- #
# C. 一体化服务：/wf/tech/kb/snapshot 真 HTTP
# --------------------------------------------------------------------------- #
CHILD_SUITE = r'''
import http.server
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request

root, fixture = sys.argv[1], sys.argv[2]
sys.path.insert(0, root)
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="cpq-kb-suite-")

import cpq_suite_server as suite

INTERNAL = "kb-internal-token-1"
os.environ["CPQ_INTERNAL_TOKEN"] = INTERNAL
suite.CPQ_INTERNAL_TOKEN = INTERNAL
suite.cpq_auth._backend = "pg"
suite.cpq_auth.whoami = lambda token: {"user_id": "1", "username": "root",
                                       "role_code": "admin", "role_name": "系统管理员"}

SNAP = json.loads(open(fixture, encoding="utf-8").read())
STATE = {"fail": False}


class FakeKb:
    SCHEMA = "cpq_kb"

    def kb_version(self):
        if STATE["fail"]:
            raise RuntimeError("连接 Postgres 失败：connection refused")
        return 7

    def snapshot(self, since=None):
        if STATE["fail"]:
            raise RuntimeError("连接 Postgres 失败：connection refused")
        if since is not None and str(since) == "7":
            return {"kb_version": 7, "unchanged": True, "tables": {}}
        return {"kb_version": 7, "unchanged": False, "tables": SNAP}


suite.cpq_kb = FakeKb()

server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), suite.Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()


def get(path, internal=None, token=None):
    request = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method="GET")
    if internal:
        request.add_header("X-Internal-Token", internal)
    if token:
        request.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        status = exc.code
    except Exception as exc:      # noqa: BLE001
        return {"status": -1, "text": "%s: %s" % (type(exc).__name__, exc)}
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        payload = {}
    return {"status": status, "text": raw[:800], "json": payload}


out = {"port": port}
anon = get("/wf/tech/kb/snapshot")
out["anon"] = {"status": anon["status"], "ok": anon.get("json", {}).get("ok")}
user_only = get("/wf/tech/kb/snapshot", token="some-user-token")
out["user_only"] = {"status": user_only["status"], "ok": user_only.get("json", {}).get("ok")}

full = get("/wf/tech/kb/snapshot", internal=INTERNAL)
payload = full.get("json", {})
tables = payload.get("tables") or {}
out["internal"] = {"status": full["status"], "ok": payload.get("ok"),
                   "kb_version": payload.get("kb_version"),
                   "tables": len(tables),
                   "rows": sum(len(v) for v in tables.values() if isinstance(v, list)),
                   "text": full["text"]}

same = get("/wf/tech/kb/snapshot?since=7", internal=INTERNAL)
sp = same.get("json", {})
out["unchanged"] = {"status": same["status"], "unchanged": sp.get("unchanged"),
                    "tables": len(sp.get("tables") or {}), "kb_version": sp.get("kb_version")}

STATE["fail"] = True
down = get("/wf/tech/kb/snapshot", internal=INTERNAL)
dp = down.get("json", {})
out["pg_down"] = {"status": down["status"], "ok": dp.get("ok"),
                  "error": str(dp.get("error") or "")[:200],
                  "tables": len(dp.get("tables") or {})}

print(json.dumps(out, ensure_ascii=False))
'''


class TestSnapshotEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.out = run_child(CHILD_SUITE, str(ROOT), str(SNAPSHOT_FIXTURE))

    def test_20_anonymous_request_rejected(self):
        self.assertIn(self.out["anon"]["status"], (401, 403),
                      "无票访问快照端点必须被拒：%s" % self.out["anon"])

    def test_21_user_token_alone_is_not_enough(self):
        self.assertIn(self.out["user_only"]["status"], (401, 403),
                      "快照端点只认内部令牌（后台刷新没有用户票）：%s" % self.out["user_only"])

    def test_22_internal_token_returns_full_snapshot(self):
        got = self.out["internal"]
        self.assertEqual(200, got["status"], "内部令牌应拿到 200：%s" % got)
        self.assertTrue(got["ok"])
        self.assertEqual(7, got["kb_version"])
        self.assertEqual(16, got["tables"], "应为 16 张有数据的 kb 表")
        self.assertEqual(613, got["rows"], "应为 613 行")

    def test_23_since_current_version_short_circuits(self):
        got = self.out["unchanged"]
        self.assertEqual(200, got["status"])
        self.assertTrue(got["unchanged"], "同版本必须返回 unchanged=true")
        self.assertEqual(0, got["tables"], "unchanged 时不应回传表数据")
        self.assertEqual(7, got["kb_version"])

    def test_24_pg_down_is_503_not_empty_kb(self):
        got = self.out["pg_down"]
        self.assertEqual(503, got["status"], "PG 不可用必须 503：%s" % got)
        self.assertFalse(got["ok"])
        self.assertTrue(got["error"], "503 必须带可读原因")
        self.assertEqual(0, got["tables"], "严禁把 PG 故障回成空知识库")


# --------------------------------------------------------------------------- #
# D/E. 技术工艺侧：快照取数 + 失败响亮 + golden 一致性
# --------------------------------------------------------------------------- #
CHILD_TECH = r'''
import http.server
import json
import os
import sys
import tempfile
import threading
import urllib.request

root, fixture, parts_file, mode = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
sys.path.insert(0, os.path.join(root, "tech_app"))

data_dir = tempfile.mkdtemp(prefix="cpq-kb-tech-")
os.environ["DATA_DIR"] = data_dir
os.environ["KB_DIR"] = os.path.join(data_dir, "kb")
os.environ["CPQ_INTERNAL_TOKEN"] = "kb-internal-token-1"

SNAP = json.loads(open(fixture, encoding="utf-8").read())
PARTS = json.loads(open(parts_file, encoding="utf-8").read())["parts"]
CALLS = []


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        CALLS.append(self.path)
        if mode == "down":
            body = b'{"ok": false, "error": "PG down"}'
            self.send_response(503)
        elif not self.path.startswith("/wf/tech/kb/snapshot"):
            body = b'{"ok": false, "error": "not found"}'
            self.send_response(404)
        else:
            body = json.dumps({"ok": True, "kb_version": 7, "unchanged": False,
                               "tables": SNAP}).encode("utf-8")
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["CPQ_AUTH_BASE_URL"] = "http://127.0.0.1:%d" % port

from backend.storage import kb_repo                      # noqa: E402
from backend.services import component_match as cm       # noqa: E402

out = {"port": port, "data_dir": data_dir}
try:
    comps = kb_repo.list_components(limit=1000)
    out["components"] = len(comps)
    out["codes"] = sorted(c["component_code"] for c in comps)[:25]
except BaseException as exc:                             # noqa: BLE001
    out["components_error"] = "%s: %s" % (type(exc).__name__, str(exc)[:200])

out["matches"] = []
for part in PARTS:
    try:
        rep = cm.match_part(part)
        out["matches"].append({"ir_id": part.get("ir_id"), "part_id": rep.get("part_id"),
                               "name": rep.get("part_name"), "decision": rep.get("decision"),
                               "component_code": rep.get("component_code"),
                               "score": rep.get("score"), "match_type": rep.get("match_type")})
    except BaseException as exc:                         # noqa: BLE001
        out["matches"].append({"ir_id": part.get("ir_id"), "part_id": part.get("part_id"),
                               "name": part.get("name"), "error": "%s: %s" % (
                                   type(exc).__name__, str(exc)[:200])})

out["http_calls"] = list(CALLS)
print(json.dumps(out, ensure_ascii=False))
'''


class TestTechAppKbSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ok = run_child(CHILD_TECH, str(ROOT), str(SNAPSHOT_FIXTURE),
                           str(PARTS_FIXTURE), "ok")
        cls.down = run_child(CHILD_TECH, str(ROOT), str(SNAPSHOT_FIXTURE),
                             str(PARTS_FIXTURE), "down")

    def test_30_components_come_from_the_snapshot_not_sqlite(self):
        self.assertNotIn("components_error", self.ok,
                         "读取零部件库失败：%s" % self.ok.get("components_error"))
        self.assertEqual(20, self.ok["components"],
                         "本地 SQLite 知识库为空时，快照路径必须给出 20 条（实际 %s）"
                         % self.ok["components"])

    def test_31_tech_app_actually_calls_the_snapshot_endpoint(self):
        calls = self.ok.get("http_calls") or []
        self.assertTrue(any("/wf/tech/kb/snapshot" in c for c in calls),
                        "技术工艺没有通过 HTTP 拉快照：%s" % calls[:5])

    def test_32_kb_unavailable_must_raise_not_report_empty(self):
        self.assertIn("components_error", self.down,
                      "快照不可用时必须抛错；现在返回了 %s 条（把故障伪装成了空库）"
                      % self.down.get("components"))

    def test_33_matching_results_match_the_sqlite_reference(self):
        golden = parts_fixture()["golden"]
        got = self.ok.get("matches") or []
        self.assertEqual(len(golden), len(got), "零件数不一致")
        mismatched = []
        for want, actual in zip(golden, got):
            if "error" in actual:
                mismatched.append((want, actual))
                continue
            for key in ("part_id", "decision", "component_code", "score", "match_type"):
                if want.get(key) != actual.get(key):
                    mismatched.append({"part": want["part_id"], "key": key,
                                       "want": want.get(key), "got": actual.get(key)})
        self.assertEqual([], mismatched,
                         "快照路径与旧 SQLite 口径不一致（前 5 条）：%s" % mismatched[:5])


if __name__ == "__main__":
    unittest.main(verbosity=2)

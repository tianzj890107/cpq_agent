"""几何 / 2D 结果的「逐件版本粒度」（第四批）：一个零件的改动不得连坐其他零件。

背景（只读代码检查 + 线上现象）：
  · main.py 的 GET /api/projects/{id} 用**整份** IR 哈希判断 geometry/drawings 是否过期，
    一旦不等就把整份文档置成 None —— 改 P-005 的一个尺寸/数量/名称，P-001 ~ P-004 已生成的
    STEP/STL/视图全部被判过期、整份 3D 视图清空；
  · main.py 的 _geom_for_part（工艺/成本取几何属性的入口）用同一个整份哈希，于是「别的零件被改」
    也会让这个零件的几何属性取不到；
  · store.save_ir() 每次保存 IR 都无条件 derived_results_stale = true；
  · 单件重生成只覆盖该零件条目，不写任何来源指纹，顶层整份哈希于是长期「不可信」。

契约见 docs/specs/tech-per-part-result-staleness.md：
  C1 逐件形状指纹 / 属性指纹 + 结果条目带 source_part_hash / source_attr_hash / generated_at；
  C2 读时装饰 stale / stale_reason / stale_attributes，既有字段一个不删；
  C3 整份隐藏只剩三种情况（来源替换 / 零件增删与结构变化 / legacy 判断不出）；
  C4 字段依赖表：只有 features 变化让该件几何过期；名称/数量/公差/型号什么都不失效；
     材料只让 mass_g 待重算；
  C5 _geom_for_part 逐件判断；C6 单件重生成只刷新该件、不动顶层哈希；
  C7 legacy 文档兜底；C8 前端按零件显示过期标记；C9 既有契约不放松。

验证方式：
  · 后端用带 fastapi/pydantic 的解释器（本机为 open-claude/.venv/bin/python）在子进程里
    真跑 TestClient + 真实 CadQuery（临时 DATA_DIR、假项目、不联网、不碰运行数据）；
  · 前端做源码契约断言（过期标记 + 静态资源版本号）。
"""
from __future__ import annotations

import functools
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


APP = read(FRONTEND / "app.js")
INDEX_HTML = read(FRONTEND / "index.html")
MAIN = read(ROOT / "tech_app/backend/main.py")


def def_block(text: str, marker: str) -> str:
    """从 marker 截到下一个顶层 def/class，避开 docstring 里的花括号。"""
    idx = text.find(marker)
    if idx < 0:
        return ""
    match = re.search(r"\n(?=(?:def|class|@)\s)", text[idx + len(marker):])
    end = idx + len(marker) + match.start() if match else len(text)
    return text[idx:end]


CHILD = r'''
import json
import os
import sys
import time
import types

data_dir, root = sys.argv[1], sys.argv[2]
os.environ["DATA_DIR"] = data_dir
os.environ["AUTH_ENABLED"] = "false"
sys.path.insert(0, root)
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("dotenv")
    _stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _stub

out = {}
try:
    import cadquery  # noqa: F401
    out["cadquery"] = True
except Exception:
    out["cadquery"] = False

if not out["cadquery"]:
    print(json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)

from starlette.testclient import TestClient

import tech_app.backend.main as main
from tech_app.backend.models.ir import DesignIR
from tech_app.backend.storage import store

client = TestClient(main.app, raise_server_exceptions=False)

# 5 个零件全部几何齐备 —— 这样 /generate 与 /drawings 在任何一批实现下都应成功，
# 本批要考察的是「改其中一个零件之后别的零件会不会连坐」。
PARTS = [
    {"part_id": "P-001", "name": "上盖", "quantity": 1,
     "features": [{"type": "box", "length": 108, "width": 56, "height": 13.25}]},
    {"part_id": "P-002", "name": "下壳", "quantity": 1,
     "features": [{"type": "box", "length": 108, "width": 56, "height": 13.25}]},
    {"part_id": "P-003", "name": "绝缘板", "quantity": 1,
     "features": [{"type": "plate", "length": 90, "width": 40, "thickness": 1.6}]},
    {"part_id": "P-004", "name": "支架", "quantity": 1,
     "features": [{"type": "box", "length": 70, "width": 40, "height": 10}]},
    {"part_id": "P-005", "name": "输出线缆组件", "model_no": "XT30", "quantity": 1,
     "features": [{"type": "box", "length": 60, "width": 40, "height": 20}]},
]

TERMINAL = {"succeeded", "failed", "partial", "interrupted", "cancelled", "stale"}


def new_project(tag, parts=None):
    pid = store.create_project(source_filename="电池图纸案例1.png", source_bytes=b"png",
                               note="per-part staleness probe " + tag, owner="tester")
    store.save_ir(pid, DesignIR(device_name="电池箱", design_intent="逐件过期探针",
                                parts=parts if parts is not None else PARTS).model_dump(),
                  stage="parsed")
    return pid


def poll(pid, task_id, timeout=120.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = client.get("/api/projects/%s/tasks/%s" % (pid, task_id)).json()
        if task.get("status") in TERMINAL:
            return task
        time.sleep(0.2)
    return {"status": "timeout", "error": "轮询超时"}


def generate_all_artifacts(pid):
    steps = {}
    for path, label in (("/generate", "3d"), ("/drawings", "2d")):
        response = client.post("/api/projects/%s%s" % (pid, path))
        data = response.json() if response.content else {}
        task_id = data.get("task_id") if isinstance(data, dict) else None
        steps[label] = poll(pid, task_id) if task_id else {"status": "no-task", "submit": data}
    return steps


def state(pid):
    """GET /api/projects/{id} 的过期视图 + 顶层 IR 哈希。"""
    data = client.get("/api/projects/%s" % pid).json()
    status = data.get("artifact_status") or {}
    geometry = data.get("geometry") or {}
    drawings = data.get("drawings") or {}
    return {
        "artifact_status": status,
        "geometry_none": data.get("geometry") is None,
        "drawings_none": data.get("drawings") is None,
        "geometry_parts": {p.get("part_id"): p for p in geometry.get("parts") or []},
        "drawings_parts": {p.get("part_id"): p for p in drawings.get("parts") or []},
        "geometry_top_hash": geometry.get("source_ir_hash"),
        "ir_hash": main._ir_snapshot(pid),
    }


def put_ir(pid, mutate):
    ir = store.load_ir(pid)
    mutate(ir)
    response = client.put("/api/projects/%s/ir" % pid, json=ir)
    if response.status_code != 200:
        return {"http": response.status_code, "detail": response.text[:200]}
    return state(pid)


def _part(ir, part_id):
    for part in ir.get("parts") or []:
        if part.get("part_id") == part_id:
            return part
    raise KeyError(part_id)


def case_probe():
    pid = new_project("main")
    result = {"project": pid, "generate": generate_all_artifacts(pid)}
    result["baseline"] = state(pid)

    # 逐个字段改 P-005，每次只看「别的零件有没有被连坐」。
    result["rename"] = put_ir(pid, lambda ir: _part(ir, "P-005").update({"name": "线束组件"}))
    result["quantity"] = put_ir(pid, lambda ir: _part(ir, "P-005").update({"quantity": 3}))
    result["material"] = put_ir(
        pid, lambda ir: _part(ir, "P-005").update({"material": {"spec": "UL1007", "density": 1.4}}))
    result["tolerance"] = put_ir(
        pid, lambda ir: _part(ir, "P-005").update({"tolerance_general": "ISO 2768-m"}))
    result["model_no"] = put_ir(pid, lambda ir: _part(ir, "P-005").update({"model_no": "XT60"}))
    result["before_dims"] = state(pid)
    result["dims"] = put_ir(
        pid, lambda ir: _part(ir, "P-005")["features"][0].update({"height": 21}))

    result["geom_for_part"] = {
        "P-001": main._geom_for_part(pid, "P-001"),
        "P-005": main._geom_for_part(pid, "P-005"),
    }

    hash_before_regen = (state(pid).get("geometry_top_hash"))
    response = client.post("/api/projects/%s/parts/P-005/regenerate" % pid)
    result["regenerate"] = {"http": response.status_code,
                            "body": response.json() if response.content else None,
                            "top_hash_before": hash_before_regen}
    result["after_regenerate"] = state(pid)
    return result


def case_structure_change():
    pid = new_project("structure")
    result = {"project": pid, "generate": generate_all_artifacts(pid)}
    result["baseline"] = state(pid)

    def drop(ir):
        ir["parts"] = [p for p in ir["parts"] if p.get("part_id") != "P-004"]

    result["after_delete"] = put_ir(pid, drop)
    return result


def case_legacy():
    pid = new_project("legacy-equal")
    # 老结果文档：只有顶层 source_ir_hash，没有任何逐件指纹。
    store.save_geometry_result(pid, {
        "parts": [{"part_id": "P-001", "name": "上盖", "ok": True,
                   "stl_url": "/api/projects/%s/geometry/P-001.stl" % pid,
                   "step_url": "/api/projects/%s/geometry/P-001.step" % pid,
                   "volume_mm3": 1.0, "mass_g": 1.0, "bbox": [1, 2, 3],
                   "warnings": [], "error": None}],
        "source_ir_hash": main._ir_snapshot(pid)})
    legacy_equal = state(pid)

    pid2 = new_project("legacy-mismatch")
    store.save_geometry_result(pid2, {
        "parts": [{"part_id": "P-001", "name": "上盖", "ok": True,
                   "stl_url": "/api/projects/%s/geometry/P-001.stl" % pid2,
                   "step_url": "/api/projects/%s/geometry/P-001.step" % pid2,
                   "volume_mm3": 1.0, "mass_g": 1.0, "bbox": [1, 2, 3],
                   "warnings": [], "error": None}],
        "source_ir_hash": "0" * 64})
    legacy_mismatch = state(pid2)
    return {"equal": legacy_equal, "mismatch": legacy_mismatch}


CASES = {"main": case_probe, "structure": case_structure_change, "legacy": case_legacy}
for name, fn in CASES.items():
    try:
        out[name] = fn()
    except Exception as exc:  # noqa: BLE001 — 单个场景失败不影响其它场景
        out[name] = {"probe_error": "%s: %s" % (type(exc).__name__, exc)}

print(json.dumps(out, ensure_ascii=False, default=str))
'''


def backend_python() -> str:
    candidates = [sys.executable, str(ROOT / "open-claude/.venv/bin/python"),
                  shutil.which("python3"), shutil.which("python")]
    for candidate in candidates:
        if not candidate or not pathlib.Path(candidate).exists():
            continue
        probe = subprocess.run([candidate, "-c", "import fastapi, pydantic"],
                               capture_output=True, text=True)
        if probe.returncode == 0:
            return candidate
    return ""


@functools.lru_cache(maxsize=None)
def probe() -> dict:
    python = backend_python()
    if not python:
        raise unittest.SkipTest("没有可 import fastapi/pydantic 的解释器，跳过逐件过期走查")
    data_dir = tempfile.mkdtemp(prefix="cpq-part-stale-data-")
    script_dir = tempfile.mkdtemp(prefix="cpq-part-stale-script-")
    try:
        script = pathlib.Path(script_dir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        completed = subprocess.run(
            [python, str(script), data_dir, str(ROOT)],
            capture_output=True, text=True, timeout=900, cwd=str(ROOT))
        if completed.returncode != 0:
            raise AssertionError(
                "逐件过期走查失败（returncode=%s）\nstdout:\n%s\nstderr:\n%s"
                % (completed.returncode, completed.stdout[-2000:], completed.stderr[-4000:]))
        return json.loads(completed.stdout.strip().splitlines()[-1])
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(script_dir, ignore_errors=True)


class PerPartStalenessBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = probe()

    def case(self, name: str) -> dict:
        value = self.data.get(name)
        self.assertIsInstance(value, dict, f"走查场景 {name} 缺失：{value!r}")
        self.assertNotIn("probe_error", value, f"走查场景 {name} 抛错：{value.get('probe_error')}")
        return value

    def status_of(self, snapshot: dict) -> dict:
        self.assertNotIn("http", snapshot, f"PUT /ir 失败：{snapshot}")
        status = snapshot.get("artifact_status")
        self.assertIsInstance(status, dict, f"GET 项目必须返回 artifact_status：{snapshot}")
        return status

    def stale_parts(self, snapshot: dict, key: str) -> list:
        return sorted(self.status_of(snapshot).get(key) or [])


class PerPartStalenessBackendRedTest(PerPartStalenessBase):
    """一个零件的字段改动不得连坐其他零件的几何 / 2D。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not cls.data.get("cadquery"):
            raise unittest.SkipTest("本机缺少 CadQuery，跳过逐件过期真实走查")

    # -------------------------------------------------- C1 逐件来源指纹
    def test_result_entries_carry_per_part_fingerprints(self):
        case = self.case("main")
        baseline = case["baseline"]
        for part_id in ("P-001", "P-005"):
            with self.subTest(part_id=part_id):
                entry = baseline["geometry_parts"].get(part_id) or {}
                self.assertTrue(str(entry.get("source_part_hash") or "").strip(),
                                f"{part_id} 的几何结果必须带逐件形状指纹：{entry}")
                self.assertTrue(str(entry.get("source_attr_hash") or "").strip(),
                                f"{part_id} 的几何结果必须带逐件属性指纹：{entry}")
                self.assertTrue(str(entry.get("generated_at") or "").strip(),
                                f"{part_id} 的几何结果必须带生成时间：{entry}")

    def test_shape_fingerprint_ignores_non_geometry_fields(self):
        case = self.case("main")
        baseline_hash = (case["baseline"]["geometry_parts"].get("P-005") or {}).get("source_part_hash")
        for step in ("rename", "quantity", "material", "tolerance", "model_no"):
            with self.subTest(step=step):
                entry = case[step]["geometry_parts"].get("P-005") or {}
                self.assertEqual(entry.get("source_part_hash"), baseline_hash,
                                 f"改 {step} 不得改动形状指纹：{entry}")
        after = case["dims"]["geometry_parts"].get("P-005") or {}
        self.assertNotEqual(after.get("source_part_hash"), baseline_hash,
                            "改尺寸必须改动该零件的形状指纹")

    # -------------------------------------------------- C4 字段依赖表
    def test_name_and_quantity_changes_do_not_stale_anything(self):
        case = self.case("main")
        for step in ("rename", "quantity", "tolerance", "model_no"):
            with self.subTest(step=step):
                snapshot = case[step]
                self.assertEqual(self.stale_parts(snapshot, "geometry_parts_stale"), [],
                                 f"改 {step} 不得让任何零件的 3D 过期：{snapshot['artifact_status']}")
                self.assertEqual(self.stale_parts(snapshot, "drawings_parts_stale"), [],
                                 f"改 {step} 不得让任何零件的 2D 过期：{snapshot['artifact_status']}")
                self.assertFalse(self.status_of(snapshot).get("geometry_stale"),
                                 f"改 {step} 不得把整份 3D 判为过期：{snapshot['artifact_status']}")

    def test_material_change_only_marks_mass_as_stale(self):
        case = self.case("main")
        snapshot = case["material"]
        status = self.status_of(snapshot)
        self.assertEqual(self.stale_parts(snapshot, "geometry_parts_stale"), [],
                         "改材料不该让 3D 结果过期（形状没变）")
        self.assertEqual(self.stale_parts(snapshot, "drawings_parts_stale"), [],
                         "改材料不该让 2D 结果过期")
        entry = snapshot["geometry_parts"].get("P-005") or {}
        self.assertFalse(entry.get("stale"), f"材料变化不该把条目判成整体过期：{entry}")
        self.assertIn("mass_g", (status.get("stale_attributes") or {}).get("P-005") or [],
                      f"改材料必须把该零件的质量属性标为待重算：{status}")

    def test_dimension_change_only_marks_that_part(self):
        case = self.case("main")
        snapshot = case["dims"]
        status = self.status_of(snapshot)
        self.assertEqual(self.stale_parts(snapshot, "geometry_parts_stale"), ["P-005"],
                         f"只有被改的 P-005 过期：{status}")
        self.assertEqual(self.stale_parts(snapshot, "drawings_parts_stale"), ["P-005"],
                         f"2D 同样只过期 P-005：{status}")
        self.assertFalse(status.get("geometry_stale"),
                         "还有 4 个零件的几何有效，整份结果不得被隐藏")
        self.assertFalse(snapshot["geometry_none"],
                         "整份 3D 结果不得被置空（过期的是零件，不是整份）")
        self.assertFalse(snapshot["drawings_none"], "整份 2D 结果不得被置空")
        entry = snapshot["geometry_parts"].get("P-005") or {}
        self.assertTrue(entry.get("stale"), f"P-005 条目必须标过期：{entry}")
        self.assertEqual(entry.get("stale_reason"), "geometry",
                         f"过期原因要说明是几何变了：{entry}")
        other = snapshot["geometry_parts"].get("P-001") or {}
        self.assertFalse(other.get("stale"), f"P-001 不得被连坐：{other}")
        self.assertTrue(other.get("step_url"), "未过期零件的下载链接必须保留")

    def test_expired_part_keeps_its_files(self):
        case = self.case("main")
        entry = case["dims"]["geometry_parts"].get("P-005") or {}
        self.assertTrue(entry.get("stl_url") and entry.get("step_url"),
                        "过期只是标记，文件还在，用户要能下载对比")
        drawings = case["dims"]["drawings_parts"].get("P-005") or {}
        self.assertTrue(drawings.get("views"), "2D 视图同理不得被清空")

    # -------------------------------------------------- C5 逐件判断
    def test_geom_for_part_is_per_part(self):
        case = self.case("main")
        outcome = case["geom_for_part"]
        self.assertIsNotNone(outcome.get("P-001"),
                             "P-001 自己没改，工艺/成本必须仍拿得到它的几何属性")
        self.assertIsNone(outcome.get("P-005"),
                          "P-005 尺寸已变，必须先重生成再取几何属性")

    # -------------------------------------------------- C6 单件重生成
    def test_regenerate_refreshes_only_that_part(self):
        case = self.case("main")
        before = case["dims"]["geometry_parts"]          # 尺寸已改、尚未重生成：P-005 还挂着旧指纹
        after = case["after_regenerate"]["geometry_parts"]
        stale_hash = (before.get("P-005") or {}).get("source_part_hash")
        refreshed = (after.get("P-005") or {}).get("source_part_hash")
        self.assertTrue(str(refreshed or "").strip(),
                        f"单件重生成必须把逐件指纹写回结果条目：{after.get('P-005')}")
        self.assertNotEqual(refreshed, stale_hash,
                            "尺寸已改，重生成后 P-005 的指纹必须刷新成当前形状，而不是继续挂着旧指纹")
        self.assertFalse((after.get("P-005") or {}).get("stale"),
                         f"重生成后 P-005 条目不得再标过期：{after.get('P-005')}")
        self.assertEqual(self.stale_parts(case["after_regenerate"], "geometry_parts_stale"), [],
                         "重生成后 P-005 不再过期")
        self.assertEqual(after.get("P-001"), before.get("P-001"),
                         "单件重生成不得改动 P-001 的条目（指纹、URL、数值一字不动）")
        self.assertEqual(case["regenerate"]["top_hash_before"],
                         case["after_regenerate"]["geometry_top_hash"],
                         "单件重生成不得刷新顶层整份哈希（逐件指纹才是判据）")

    # -------------------------------------------------- C3 结构变化仍整批过期
    def test_structure_change_still_hides_the_whole_document(self):
        case = self.case("structure")
        snapshot = case["after_delete"]
        status = self.status_of(snapshot)
        self.assertTrue(status.get("geometry_stale"),
                        f"删零件后逐件对不上号，必须整份过期：{status}")
        self.assertTrue(snapshot["geometry_none"], "整份过期时 geometry 仍按既有契约返回 null")

    # -------------------------------------------------- C7 legacy 兜底
    def test_legacy_document_with_matching_hash_stays_usable(self):
        case = self.case("legacy")
        snapshot = case["equal"]
        status = self.status_of(snapshot)
        self.assertFalse(snapshot["geometry_none"],
                         "老文档只有顶层哈希、但哈希一致时不得把结果判过期")
        self.assertTrue(status.get("legacy_fingerprints"),
                        f"必须显式告诉用户这是没有逐件指纹的老结果：{status}")

    def test_legacy_document_with_mismatched_hash_is_explained(self):
        case = self.case("legacy")
        snapshot = case["mismatch"]
        status = self.status_of(snapshot)
        self.assertTrue(status.get("geometry_stale"), f"哈希对不上必须判过期：{status}")
        reason = str(status.get("stale_reason") or "")
        self.assertTrue(reason.strip(), f"整份过期必须给出可读原因：{status}")
        self.assertRegex(reason, r"legacy|逐件|指纹|无法判断",
                         f"原因要说明是「老结果无法逐件判断」：{reason}")

    # -------------------------------------------------- C9 既有契约
    def test_existing_artifact_contract_is_preserved(self):
        case = self.case("main")
        status = self.status_of(case["baseline"])
        for key in ("geometry_stale", "drawings_stale"):
            with self.subTest(key=key):
                self.assertIn(key, status, f"既有 artifact_status 字段不得删除：{status}")
        entry = case["baseline"]["geometry_parts"].get("P-001") or {}
        for key in ("ok", "step_url", "stl_url", "volume_mm3", "mass_g", "bbox", "warnings", "error"):
            with self.subTest(key=key):
                self.assertIn(key, entry, f"既有结果字段不得缩水：{entry}")
        self.assertTrue(case["baseline"]["geometry_top_hash"],
                        "既有 source_ir_hash 字段不得删除")


class PerPartStalenessSourceRedTest(unittest.TestCase):
    """源码契约：路由与字段不得减少，前端要显示逐件过期标记。"""

    @classmethod
    def setUpClass(cls):
        cls.main = MAIN
        cls.app = APP

    def test_get_project_exposes_per_part_status(self):
        body = def_block(self.main, "def get_project(")
        self.assertTrue(body, "找不到 get_project()")
        for token in ("geometry_parts_stale", "drawings_parts_stale", "legacy_fingerprints"):
            with self.subTest(token=token):
                self.assertIn(token, body, f"GET 项目必须暴露逐件过期状态：{token}")
        self.assertIn("stale_attributes", body, "材料变化要能从状态里读出来")

    def test_geom_for_part_no_longer_uses_the_whole_ir_hash(self):
        body = def_block(self.main, "def _geom_for_part(")
        self.assertTrue(body, "找不到 _geom_for_part()")
        self.assertNotIn("_ir_snapshot", body,
                         "取单个零件的几何属性不得再用整份 IR 哈希判断")

    def test_writers_record_per_part_fingerprint(self):
        self.assertIn("source_part_hash", self.main,
                      "结果写入方必须给每个零件条目写来源指纹")
        regen = def_block(self.main, "def regenerate_part(")
        self.assertTrue(regen, "找不到 regenerate_part()")
        self.assertIn("_upsert_part", regen, "单件重生成仍只覆盖该零件条目")
        self.assertNotIn("generate_all", regen, "单件重生成不得退化成整批生成")

    def test_routes_are_untouched(self):
        for route in ('"/api/projects/{project_id}/generate"',
                      '"/api/projects/{project_id}/drawings"',
                      '"/api/projects/{project_id}/parts/{part_id}/regenerate"'):
            with self.subTest(route=route):
                self.assertIn(route, self.main, f"后端路由不得减少：{route}")

    def test_frontend_marks_expired_parts(self):
        self.assertRegex(self.app, r"结果已过期",
                         "零件清单/状态行必须显示「结果已过期」")
        self.assertRegex(self.app, r"质量待重算|质量待更新",
                         "材料变化导致质量待重算时必须给出可读提示")
        self.assertIn("artifact_status", self.app,
                      "前端必须读取后端给出的逐件过期状态")

    def test_app_js_asset_version_is_bumped(self):
        self.assertNotIn("app.js?v=20260914-split1", INDEX_HTML,
                         "本批改了 app.js，index.html 的 ?v= 必须同步 bump")


if __name__ == "__main__":
    unittest.main()

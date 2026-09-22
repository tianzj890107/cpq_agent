"""红测：业务部件结论的「口径四键」必须出现在内嵌面板上（`## 412`/`## 414` 的界面那一半）。

Spec：`docs/specs/packaging-business-part-conclusion-basis-in-panel.md`

现状缺口（代码级，都可指到行）：
  · `inline-analysis.js:157` 只消费业务清单漂移那四键，`size_source` / `size_source_ref` /
    `size_text` / `geometry` **一个消费者都没有**；
  · `generate()`（`:235`）只拿 `task.result` 覆盖 `state.plan` / `state.analysis`，而两条业务件路由的
    `job()` 返回值里根本没有那四键 → 生成完那一刻口径行无处可取。

纪律：`node -e` 抽 app.js 顶层具名函数真跑（纯函数）+ 源码守卫 + 假仓库 / 假模型真跑两条路由 +
`node --check`；不连 PG / 34、不发 HTTP、不写业务数据、不调模型。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend import main                                        # noqa: E402
from tech_app.backend.services import packaging_parts as parts           # noqa: E402

FRONTEND = ROOT / "tech_app" / "frontend"
APP_JS = FRONTEND / "app.js"
INLINE_JS = FRONTEND / "inline-analysis.js"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"

PID = "testpid00001"
CODE = "JWXR21-P01"
BIZ_ID = "biz:authority0001"
BIZ_HASH = "bizhash-1"
USER = {"username": "PE1", "role": "process_manager"}

UNBOUND_TEXT = ("按权威尺寸算的（300×200MM；来源：酒盒 报价资料.xlsx#Sheet1!B12）；"
                "这一件没有绑 CAD 几何。")
BOUND_TEXT = ("按权威尺寸算的（300×200MM；来源：酒盒 报价资料.xlsx#Sheet1!B12）；"
              "这一件另绑了几何件 DWG-P03，本结论有意按权威尺寸算。")

ROW = {"business_part_code": CODE, "name": "礼盒面纸",
       "authority": {"length_mm": 300.0, "width_mm": 200.0,
                     "material_text": "350G玖龙粉灰", "product_size_text": "300×200MM",
                     "process_text": "印刷→覆膜→模切",
                     "source": "酒盒 报价资料.xlsx#Sheet1!B12"},
       "geometry_binding": {"component_ids": [], "status": "unbound"}}

def _doc(**overrides):
    row = dict(ROW)
    row.update(overrides)
    return {"business_parts_id": BIZ_ID, "business_parts_hash": BIZ_HASH,
            "business_parts": [row], "geometry_evidence": {"components": []}}


EXTRACT_JS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function extract(name) {
  const at = src.indexOf("function " + name + "(");
  if (at < 0) return null;
  let i = src.indexOf("{", at);
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") { depth--; if (depth === 0) return src.slice(at, j + 1); }
  }
  return null;
}
const name = process.argv[3];
const mode = process.argv[4];
const fn = extract(name);
if (!fn) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
if (mode === "body") { console.log(JSON.stringify({ missing: false, body: fn })); process.exit(0); }
const cases = JSON.parse(mode);
eval(fn);
const out = [];
for (const args of cases) {
  const call = name + "(" + args.map(a => JSON.stringify(a)).join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""


def run_cases(name: str, cases):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name,
                           json.dumps(cases)], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(name: str) -> str:
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, "body"],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少具名函数 %s()（Spec §C1）" % name)
    return str(payload.get("body") or "")


def basis(payload):
    got = run_cases("packagingBusinessPartBasisNote", [[payload]])
    if got.get("missing"):
        raise AssertionError("app.js 缺少顶层纯函数 packagingBusinessPartBasisNote()（Spec §C1）")
    row = got["results"][0]
    if not row.get("ok"):
        raise AssertionError("packagingBusinessPartBasisNote() 抛异常：%s" % row.get("error"))
    return row.get("value") or {}


def _source(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


class _Patch:
    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []

    def __enter__(self):
        for owner, name, value in self.pairs:
            self.saved.append((owner, name, getattr(owner, name)))
            setattr(owner, name, value)
        return self

    def __exit__(self, *exc):
        for owner, name, value in reversed(self.saved):
            setattr(owner, name, value)
        return False


class _Backend:
    def __init__(self):
        self.docs = {}
        self.writes = 0

    def get_doc(self, project_id, key):
        return self.docs.get(key) or {}

    def put_doc(self, project_id, key, doc):
        self.writes += 1
        self.docs[key] = doc


class _Tasks:
    def __init__(self):
        self.calls = []
        self.result = None

    def submit(self, pid, name, job, *, dedup_key="", actor=""):
        self.calls.append({"pid": pid, "name": name, "dedup_key": dedup_key, "actor": actor})
        self.result = job()
        return "task-1"

    def report_progress(self, message):
        return None

    def current_task_id(self):
        return "task-1"


class _Plan:
    def __init__(self, data):
        self._data = dict(data)

    def model_dump(self):
        return dict(self._data)


PLAN = {"part_id": CODE, "part_name": "礼盒面纸", "steps": [{"step_no": 10, "name": "备料"}],
        "overall_note": ""}
LINE = {"formula_code": "PKG-C-MATERIAL", "amount": 1.2345, "unit": "元/件",
        "formula_source": "workbook", "rule_snapshot_version": "pkgcost-v1:1000",
        "assumptions": [], "gap": None}


def _post_cost(*, doc=None):
    backend, tasks = _Backend(), _Tasks()
    with _Patch((main, "_workflow_project", lambda *a, **k: None),
                (main, "tasks", tasks),
                (parts, "load_business_parts", lambda *a, **k: dict(doc or _doc())),
                (parts, "get_backend", lambda: backend),
                (main.packaging_cost, "compute_line", lambda kind, variables: dict(LINE)),
                (main.store, "load_requirement", lambda *a, **k: {"data": {}})):
        asyncio.run(main.packaging_business_part_cost(PID, CODE, 1, "", [], USER))
    return tasks.result or {}


def _post_process(*, doc=None):
    backend, tasks = _Backend(), _Tasks()

    def fake_outline(part, overall=None, geom=None, note="", attachments=None,
                     library="", lookup=None):
        return _Plan(PLAN), {"summary": {"reused": 0, "missing": 1}}

    with _Patch((main, "_workflow_project", lambda *a, **k: None),
                (main, "tasks", tasks),
                (parts, "load_business_parts", lambda *a, **k: dict(doc or _doc())),
                (parts, "get_backend", lambda: backend),
                (main.process, "outline_process", fake_outline),
                (main.process, "compute", lambda plan: {"step_count": 1})):
        asyncio.run(main.packaging_business_part_process(PID, CODE, "", [], USER))
    return tasks.result or {}


# --------------------------------------------------------------------------- #
# A 组：纯函数 `packagingBusinessPartBasisNote()`（Spec §C1）
# --------------------------------------------------------------------------- #
class ABasisNote(unittest.TestCase):
    def test_a1_authority_size_says_the_basis(self):
        got = basis({"size_source": "authority_dimensions", "size_text": "300×200MM",
                     "size_source_ref": "酒盒 报价资料.xlsx#Sheet1!B12", "geometry": "unbound"})
        self.assertEqual(UNBOUND_TEXT, got.get("text"), "口径那句话逐字（Spec §C1）")
        self.assertEqual("authority_unbound", got.get("level"))

    def test_a2_bound_geometry_appends_one_clause(self):
        got = basis({"size_source": "authority_dimensions", "size_text": "300×200MM",
                     "size_source_ref": "酒盒 报价资料.xlsx#Sheet1!B12",
                     "geometry": "bound:DWG-P03"})
        self.assertEqual(BOUND_TEXT, got.get("text"), "绑了几何件也只是追加一句（Spec §C1）")
        self.assertEqual("authority_bound", got.get("level"))
        self.assertEqual(1, got.get("text", "").count("DWG-P03"),
                         "编码只许出现一次（Spec §C1）")

    def test_a3_geometric_route_says_nothing(self):
        for payload in ({}, None, "nonsense", 7, {"size_source": ""},
                        {"size_source": "geometry_binding"},
                        {"size_source": "  ", "size_text": "300×200MM"}):
            got = basis(payload)
            self.assertEqual("", got.get("text"),
                             "几何件那条路一个字都不多说（Spec §C1）：%r" % (payload,))
            self.assertEqual("", got.get("level"))

    def test_a4_missing_evidence_only_drops_that_piece(self):
        got = basis({"size_source": "authority_dimensions", "geometry": "unbound"})
        self.assertEqual("按权威尺寸算的；这一件没有绑 CAD 几何。", got.get("text"),
                         "两段证据都缺时不留空括号（Spec §C1）")
        got = basis({"size_source": "authority_dimensions", "size_text": "300×200MM",
                     "geometry": "unbound"})
        self.assertEqual("按权威尺寸算的（300×200MM）；这一件没有绑 CAD 几何。", got.get("text"),
                         "缺来源就少一段（Spec §C1）")
        got = basis({"size_source": "authority_dimensions",
                     "size_source_ref": "xlsx#B12", "geometry": "unbound"})
        self.assertEqual("按权威尺寸算的（来源：xlsx#B12）；这一件没有绑 CAD 几何。",
                         got.get("text"), "缺尺寸原文就少一段（Spec §C1）")

    def test_a5_empty_or_odd_geometry_is_unbound(self):
        for geometry in ("", "   ", "unbound", None, 7, "bound:", "bound:   "):
            got = basis({"size_source": "authority_dimensions", "size_text": "300×200MM",
                         "geometry": geometry})
            self.assertEqual("authority_unbound", got.get("level"),
                             "空 / 认不出的 geometry 都按没绑几何说（Spec §C1）：%r" % (geometry,))

    def test_a6_levels_are_a_closed_set(self):
        for payload in ({}, {"size_source": "authority_dimensions", "geometry": "unbound"},
                        {"size_source": "authority_dimensions", "geometry": "bound:X"},
                        {"size_source": "x"}):
            self.assertIn(basis(payload).get("level") or "",
                          ("", "authority_unbound", "authority_bound"),
                          "level 是闭集（Spec §C1）：%r" % (payload,))

    def test_a7_never_throws_on_odd_payloads(self):
        for payload in ({"size_text": {"a": 1}}, {"size_source": [1]},
                        {"size_source": "authority_dimensions", "size_text": ["x"]},
                        {"size_source": "authority_dimensions", "geometry": {"a": 1}}):
            basis(payload)      # 不抛就算过

    def test_a8_pure_function_has_no_dom_or_io(self):
        body = function_body("packagingBusinessPartBasisNote")
        for token in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(token, body, "纯函数体内不许出现 %s（Spec §C1）" % token)


# --------------------------------------------------------------------------- #
# B 组：`inline-analysis.js` 接线（Spec §C2）
# --------------------------------------------------------------------------- #
class BWiredIntoInlineAnalysis(unittest.TestCase):
    def setUp(self):
        self.src = _source(INLINE_JS)

    def test_b1_load_computes_the_basis_once(self):
        self.assertEqual(
            1, self.src.count("state.basisNote = packagingBusinessPartBasisNote(data)"),
            "`load()` 里必须有一处把读回体算成 basisNote（Spec §C2）")
        self.assertIn("businessNote: null", self.src, "初始状态那一组要带上 basisNote（Spec §C2）")
        self.assertIn("basisNote: null", self.src, "初始状态要有 basisNote（Spec §C2）")

    def test_b2_generate_refreshes_it_without_a_second_read(self):
        self.assertEqual(
            1, self.src.count("state.basisNote = packagingBusinessPartBasisNote(result)"),
            "`generate()` 里必须有一处用任务结果刷新口径（Spec §C2）")
        self.assertNotIn("await load(state)", self.src.split("async function generate")[1][:2500],
                         "不许用「再读一次」冒充「生成完立刻可见」（Spec §C2）")

    def test_b3_row_is_rendered_only_when_there_is_text(self):
        proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(INLINE_JS),
                               "businessBasisRow", "body"],
                              capture_output=True, text=True, timeout=60)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertFalse(payload.get("missing"), "必须有 businessBasisRow()（Spec §C2）")
        body = str(payload.get("body") or "")
        self.assertIn("data-inline-basis-note", body, "节点带 level data 属性（Spec §C2）")
        self.assertIn("if (!note.text) return", body, "空文案一个节点都不渲染（Spec §C2）")

    def test_b4_both_renderers_call_it_right_after_the_business_row(self):
        for name in ("renderProcess", "renderCost"):
            proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(INLINE_JS), name, "body"],
                                  capture_output=True, text=True, timeout=60)
            body = str(json.loads(proc.stdout.strip().splitlines()[-1]).get("body") or "")
            self.assertIn("businessBasisRow(state)", body, "%s 要渲染这一行（Spec §C2）" % name)
            self.assertLess(body.index("businessIdentityRow(state)"),
                            body.index("businessBasisRow(state)"),
                            "业务清单漂移那句仍在它前面（Spec §C2）：%s" % name)

    def test_b5_node_check_passes(self):
        for path in (APP_JS, INLINE_JS):
            proc = subprocess.run(["node", "--check", str(path)],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(0, proc.returncode, "%s 语法必须通过：%s" % (path.name, proc.stderr[:400]))


# --------------------------------------------------------------------------- #
# C 组：两条业务件路由的任务返回值（Spec §C3）
# --------------------------------------------------------------------------- #
class CTaskResultsCarryTheBasis(unittest.TestCase):
    KEYS = ("size_source", "size_source_ref", "size_text", "geometry")

    def test_c1_cost_task_result_carries_the_four_keys(self):
        result = _post_cost()
        for key in self.KEYS:
            self.assertIn(key, result, "成本任务返回值必须带 %s（Spec §C3）" % key)
        self.assertEqual("authority_dimensions", result.get("size_source"))
        self.assertEqual("酒盒 报价资料.xlsx#Sheet1!B12", result.get("size_source_ref"))
        self.assertEqual("300×200MM", result.get("size_text"))
        self.assertEqual("unbound", result.get("geometry"))
        self.assertIn("analysis", result, "既有键一个不动（Spec §C3）")
        self.assertIn("summary", result)

    def test_c2_process_task_result_carries_the_four_keys(self):
        result = _post_process()
        for key in self.KEYS:
            self.assertIn(key, result, "工艺任务返回值必须带 %s（Spec §C3）" % key)
        self.assertEqual("authority_dimensions", result.get("size_source"))
        self.assertEqual("unbound", result.get("geometry"))
        for key in ("part_code", "part_id", "plan", "validation", "coverage"):
            self.assertIn(key, result, "既有键一个不动（Spec §C3）")

    def test_c3_bound_geometry_label_is_reported(self):
        doc = _doc(geometry_binding={"component_ids": ["c-1"], "status": "bound"})
        self.assertEqual("bound:c-1", _post_cost(doc=doc).get("geometry"),
                         "绑了分量就如实报 bound:<分量引用>（Spec §C3）")
        self.assertEqual("bound:c-1", _post_process(doc=doc).get("geometry"))

    def test_c4_geometric_routes_are_untouched(self):
        src = _source(MAIN_PY)
        for path in ('"/api/projects/{pid}/requirement/packaging-parts/{part_code}/cost"',
                     '"/api/projects/{pid}/requirement/packaging-parts/{part_code}/process"'):
            self.assertEqual(1, src.count(path), "几何那两条路径逐字不变（Spec §C4）")
        self.assertEqual(1, src.count("def packaging_part_cost("))
        self.assertEqual(1, src.count("def packaging_part_process("))

    def test_c5_parts_module_is_untouched(self):
        src = _source(PARTS_PY)
        for token in ("basisNote", "BasisNote"):
            self.assertNotIn(token, src, "本批不改 packaging_parts.py（Spec §C4）")

    def test_c6_frontend_route_count_unchanged(self):
        self.assertEqual(4, _source(APP_JS).count("packaging-business-parts/"),
                         "本批不新增业务件路由引用（Spec §C4）")


if __name__ == "__main__":
    unittest.main()

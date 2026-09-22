"""红测：业务部件没有几何时，材料费必须能按**权威尺寸**算出来。

Spec：`docs/specs/packaging-business-part-cost-by-authority-size.md`

现状缺口（代码级，都可指到行）：
  · `packaging_parts.processability()`（`:2700` 一带）第一道门槛是
    `outline_status == "closed"` → 业务件（没有几何）永远 409；
  · `main.py` 的单件成本路由（`PACKAGING_PART_COST_PATH`）先 `_packaging_part_row(pid, code)`
    （只认 `DWG-Pxx`）→ 业务编码 404；
  · 权威清单里的 `length_mm` / `width_mm` / `material_text`（`BUSINESS_AUTHORITY_KEYS` `:3291`）
    从来没被送进 `packaging_cost.compute_line()`。

纪律：只读源码 + 假仓库 / 假文档 / 假成本公式；不连 PG / SQLite 生产库、不发 HTTP、
不写文件、不落库、不写技术 IR。禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend import main                                        # noqa: E402
from tech_app.backend.services import packaging_parts as parts           # noqa: E402

PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"

PID = "testpid00001"
CODE = "JWXR21-P01"
BIZ_ID = "biz:authority0001"
BIZ_HASH = "bizhash-1"
USER = {"username": "PE1", "role": "process_manager"}

REJECT_CODES = ("PACKAGING_BUSINESS_PART_NOT_FOUND",
                "PACKAGING_BUSINESS_PART_SIZE_UNKNOWN",
                "PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN")
SIZE_SOURCES = ("authority_dimensions",)
INPUT_KEYS = ("ok", "code", "message", "missing_variables", "part_code", "name", "material_text",
              "gsm", "variables", "size_source", "size_source_ref", "size_text")

#: 一行业务部件（真样本形状：权威资料 + 未绑定几何）
ROW = {"business_part_code": CODE, "name": "礼盒面纸",
       "authority": {"length_mm": 300.0, "width_mm": 200.0,
                     "material_text": "350G玖龙粉灰", "product_size_text": "300×200MM",
                     "source": "酒盒 报价资料.xlsx#Sheet1!B12"},
       "geometry_binding": {"component_ids": [], "status": "unbound"}}

DOC = {"business_parts_id": BIZ_ID, "business_parts_hash": BIZ_HASH,
       "business_parts": [ROW], "geometry_evidence": {"components": []}}

LINE = {"formula_code": "PKG-C-MATERIAL", "amount": 1.2345, "unit": "元/件",
        "formula_source": "workbook", "rule_snapshot_version": "pkgcost-v1:1000",
        "assumptions": [], "gap": None}


class _Patch:
    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []

    def __enter__(self):
        for owner, name, value in self.pairs:
            self.saved.append((owner, name, getattr(owner, name, None) if hasattr(owner, name) else None))
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


def _post(*, doc=DOC, line=LINE, code=CODE, quantity=1):
    backend, tasks = _Backend(), _Tasks()
    seen = {}

    def fake_compute(kind, variables):
        seen["kind"], seen["variables"] = kind, dict(variables)
        return dict(line)

    with _Patch((main, "_workflow_project", lambda *a, **k: None),
                (main, "tasks", tasks),
                (parts, "load_business_parts", lambda *a, **k: dict(doc) if doc else None),
                (parts, "get_backend", lambda: backend),
                (main.packaging_cost, "compute_line", fake_compute),
                (main.store, "load_requirement", lambda *a, **k: {"data": {}})):
        body = asyncio.run(main.packaging_business_part_cost(
            PID, code, quantity, "", [], USER))
    return {"body": body, "backend": backend, "tasks": tasks, "seen": seen}


def _get(*, doc=DOC, code=CODE):
    backend = _Backend()
    backend.docs[parts.DOC_KEY_COST] = {"items": [{
        "part_code": code, "parts_id": "", "analysis": {"unit_cost": 1.2345},
        "summary": "业务部件 %s 的单件材料开料成本" % code,
        "size_source": "authority_dimensions", "size_source_ref": "xlsx#B12",
        "size_text": "300×200MM", "geometry": "unbound",
        "business_part_code": code, "business_parts_id": BIZ_ID,
        "source": {"computed_at": "2026-09-22 10:00:00"}}]}
    with _Patch((main, "_workflow_project", lambda *a, **k: None),
                (parts, "load_business_parts", lambda *a, **k: dict(doc) if doc else None),
                (parts, "get_backend", lambda: backend)):
        return main.get_packaging_business_part_cost(PID, code, USER)


def _inputs(*, row=ROW, requirement=None, quantity=1):
    return parts.business_cost_inputs(row, requirement=requirement, quantity=quantity)


def _source(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def _function_body(name: str) -> str:
    text = _source(PARTS_PY)
    match = re.search(r"^def %s\(.*?\n" % re.escape(name), text, re.M | re.S)
    if not match:
        raise AssertionError("packaging_parts.py 缺少具名函数 %s()（Spec §C1/§C2）" % name)
    lines = []
    for line in text[match.end():].splitlines():
        if not line.strip():
            lines.append(line)
            continue
        if not line.startswith((" ", "\t")):
            break
        lines.append(line)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# A 组：`business_cost_inputs()`（Spec §C1）
# --------------------------------------------------------------------------- #
class AAuthoritySizeInputs(unittest.TestCase):
    def test_a1_authority_size_becomes_the_cost_variables(self):
        got = _inputs()
        self.assertEqual(INPUT_KEYS, tuple(got.keys()),
                         "返回键必须固定十二个（Spec §C1）")
        self.assertIs(True, got.get("ok"), "权威尺寸齐了就该给输入（Spec §C1）")
        self.assertEqual({"cut_length": 300.0, "cut_width": 200.0, "gsm": 350,
                          "quote_quantity": 1}, got.get("variables"),
                         "尺寸取权威长度/宽度、克重取材料原文（Spec §C1）")
        self.assertEqual("authority_dimensions", got.get("size_source"),
                         "尺寸口径必须点名 authority_dimensions（Spec §C1）")
        self.assertEqual("酒盒 报价资料.xlsx#Sheet1!B12", got.get("size_source_ref"),
                         "留痕要指回工作簿那一格（Spec §C1）")
        self.assertEqual("300×200MM", got.get("size_text"), "尺寸原文一并带出（Spec §C1）")
        self.assertEqual(CODE, got.get("part_code"), "编码取业务部件编码（Spec §C1）")

    def test_a2_missing_authority_size_is_its_own_code_and_missing_variable(self):
        row = {"business_part_code": CODE,
               "authority": {"material_text": "350G玖龙粉灰"}}
        got = _inputs(row=row)
        self.assertIs(False, got.get("ok"), "没有权威尺寸不许硬算（Spec §C1）")
        self.assertEqual("PACKAGING_BUSINESS_PART_SIZE_UNKNOWN", got.get("code"))
        self.assertEqual(["authority_size"], got.get("missing_variables"))
        self.assertIn("几何映射", str(got.get("message")),
                      "要说清两条出路（确认几何映射 / 补录权威尺寸）（Spec §C1）")

    def test_a3_zero_or_unparsable_size_is_missing_too(self):
        for length, width in ((0, 200), (300, 0), ("", 200), (None, None), ("abc", "x")):
            row = {"business_part_code": CODE,
                   "authority": {"length_mm": length, "width_mm": width,
                                 "material_text": "350G玖龙粉灰"}}
            self.assertEqual("PACKAGING_BUSINESS_PART_SIZE_UNKNOWN", _inputs(row=row).get("code"),
                             "尺寸 ≤0 / 解析不出都算缺（Spec §C1）：%r × %r" % (length, width))

    def test_a4_missing_gsm_is_its_own_code(self):
        row = dict(ROW)
        row["authority"] = {"length_mm": 300.0, "width_mm": 200.0, "material_text": "双灰板"}
        got = _inputs(row=row)
        self.assertEqual("PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN", got.get("code"),
                         "材料原文里没有克重 → 材料那条码（Spec §C1）")
        self.assertEqual(["gsm"], got.get("missing_variables"))
        self.assertIn("克重", str(got.get("message")), "message 要点名缺的是克重")

    def test_a5_requirement_gsm_is_the_documented_fallback(self):
        row = dict(ROW)
        row["authority"] = {"length_mm": 300.0, "width_mm": 200.0, "material_text": "双灰板"}
        got = _inputs(row=row, requirement={"data": {"face_paper_gsm": 300}})
        self.assertIs(True, got.get("ok"), "需求整盒口径那条兜底必须留着（Spec §C1）")
        self.assertEqual(300, got.get("variables", {}).get("gsm"))

    def test_a6_missing_code_is_not_found(self):
        got = _inputs(row={"authority": {"length_mm": 1, "width_mm": 1}})
        self.assertEqual("PACKAGING_BUSINESS_PART_NOT_FOUND", got.get("code"))
        self.assertEqual([], got.get("missing_variables"), "没有编码不是缺变量")

    def test_a7_codes_and_size_sources_are_closed_sets(self):
        self.assertEqual(set(REJECT_CODES), set(getattr(parts, "BUSINESS_COST_REJECT_CODES", ())),
                         "拒绝码必须是闭集常量（Spec §C1）")
        self.assertEqual(set(SIZE_SOURCES), set(getattr(parts, "BUSINESS_COST_SIZE_SOURCES", ())),
                         "尺寸口径必须是闭集常量（Spec §C1）")
        for row in (ROW, {}, {'business_part_code': CODE, 'authority': {}}, None, 7, "x"):
            self.assertIn(_inputs(row=row).get("code") or "", ("",) + REJECT_CODES,
                          "任何输入只许给闭集里的码（Spec §C1）：%r" % (row,))

    def test_a8_never_throws_and_does_not_mutate(self):
        row = {"business_part_code": CODE, "authority": {"length_mm": [1], "width_mm": {"a": 1},
                                                         "material_text": 350}}
        before = repr(row)
        _inputs(row=row)          # 不抛就算过
        self.assertEqual(before, repr(row), "纯函数不许改入参（Spec §C1）")

    def test_a9_pure_function_has_no_io(self):
        body = _function_body("business_cost_inputs")
        for token in ("get_backend(", "put_doc(", "open(", "requests", "load_business_parts("):
            self.assertNotIn(token, body, "纯函数体内不许出现 %s（Spec §C1）" % token)

    def test_a10_quantity_is_at_least_one(self):
        self.assertEqual(5, _inputs(quantity=5).get("variables", {}).get("quote_quantity"))
        self.assertEqual(1, _inputs(quantity=0).get("variables", {}).get("quote_quantity"),
                         "批量 ≤0 一律按 1（Spec §C1）")


# --------------------------------------------------------------------------- #
# B 组：`business_cost_assumption()`（Spec §C2）
# --------------------------------------------------------------------------- #
class BAssumptionText(unittest.TestCase):
    def test_b1_the_sentence_says_authority_size_and_not_checked(self):
        text = parts.business_cost_assumption(_inputs())
        self.assertEqual("按权威尺寸（300×200 mm）算的材料开料，未与 CAD 几何核过", text,
                         "口径那句话逐字（Spec §C2）")

    def test_b2_bound_geometry_is_appended_not_replaced(self):
        text = parts.business_cost_assumption(_inputs(), geometry_part_code="DWG-P03")
        self.assertTrue(text.startswith("按权威尺寸（300×200 mm）算的材料开料，未与 CAD 几何核过"),
                        "前半句不许被改写（Spec §C2）")
        self.assertIn("另有闭合几何件（DWG-P03）", text, "并存时要说明是「有意」按权威尺寸算（Spec §C2）")

    def test_b3_no_verdict_means_no_sentence(self):
        self.assertEqual("", parts.business_cost_assumption({"ok": False}), "没结论就没有口径那句")
        self.assertEqual("", parts.business_cost_assumption(None))


# --------------------------------------------------------------------------- #
# C 组：POST 路由（Spec §C3）
# --------------------------------------------------------------------------- #
class CCostRoute(unittest.TestCase):
    def test_c1_unknown_part_is_404_with_our_code(self):
        with _Patch((main, "_workflow_project", lambda *a, **k: None),
                    (parts, "load_business_parts", lambda *a, **k: dict(DOC))):
            with self.assertRaises(main.HTTPException) as caught:
                asyncio.run(main.packaging_business_part_cost(PID, "NOPE-P99", 1, "", [], USER))
        self.assertEqual(404, caught.exception.status_code)
        self.assertEqual("PACKAGING_BUSINESS_PART_NOT_FOUND", caught.exception.detail.get("code"))

    def test_c2_missing_size_is_409_not_retryable(self):
        row = {"business_part_code": CODE, "authority": {"material_text": "350G玖龙粉灰"}}
        doc = dict(DOC, business_parts=[row])
        with _Patch((main, "_workflow_project", lambda *a, **k: None),
                    (main, "tasks", _Tasks()),
                    (parts, "load_business_parts", lambda *a, **k: doc),
                    (main.store, "load_requirement", lambda *a, **k: {"data": {}})):
            with self.assertRaises(main.HTTPException) as caught:
                asyncio.run(main.packaging_business_part_cost(PID, CODE, 1, "", [], USER))
        self.assertEqual(409, caught.exception.status_code, "缺前置条件一律 409（Spec §C3）")
        self.assertIs(False, caught.exception.detail.get("retryable"))
        self.assertEqual(["authority_size"], caught.exception.detail.get("missing_variables"))

    def test_c3_happy_path_uses_the_authority_variables(self):
        got = _post()
        self.assertEqual("material", got["seen"].get("kind"),
                         "复用既有材料公式那一支（Spec §C3）")
        self.assertEqual({"cut_length": 300.0, "cut_width": 200.0, "gsm": 350,
                          "quote_quantity": 1}, got["seen"].get("variables"),
                         "送给 compute_line 的必须是权威尺寸那四键（Spec §C3）")
        self.assertEqual("task-1", got["body"].get("task_id"), "与既有单件成本同一个任务形状")

    def test_c4_saved_row_is_a_business_conclusion_not_a_geometric_one(self):
        got = _post()
        saved = got["backend"].docs.get(parts.DOC_KEY_COST, {}).get("items", [{}])[0]
        self.assertEqual(CODE, saved.get("part_code"), "编码必须是业务部件编码（Spec §C3）")
        self.assertEqual("", saved.get("parts_id"),
                         "不许拿几何版本冒充：`parts_id` 必须是空串（Spec §C3）")
        self.assertEqual("authority_dimensions", saved.get("size_source"))
        self.assertEqual(BIZ_ID, saved.get("business_parts_id"), "业务清单版本也要落行（Spec §C3）")
        self.assertEqual(BIZ_HASH, saved.get("business_parts_hash"))
        self.assertEqual("unbound", saved.get("geometry"), "披露这一件没有几何（Spec §C3）")
        analysis = saved.get("analysis") if isinstance(saved.get("analysis"), dict) else {}
        self.assertEqual("按权威尺寸（300×200 mm）算的材料开料，未与 CAD 几何核过",
                         (analysis.get("assumptions") or [""])[0],
                         "口径那句话必须是结论的第一条 assumption（Spec §C3）")
        self.assertEqual("task-1", (saved.get("source") or {}).get("task_id"))
        self.assertEqual({}, saved.get("lookup"), "业务件没有知识库依据，不许装样子（Spec §C3）")

    def test_c5_does_not_touch_the_technical_ir(self):
        backend, tasks = _Backend(), _Tasks()

        def boom(*a, **k):        # pragma: no cover - 走到这里就是红
            raise AssertionError("业务件成本结论不许写技术 IR（Spec §C3）")

        with _Patch((main, "_workflow_project", lambda *a, **k: None),
                    (main, "tasks", tasks),
                    (parts, "load_business_parts", lambda *a, **k: dict(DOC)),
                    (parts, "get_backend", lambda: backend),
                    (main.packaging_cost, "compute_line",
                     lambda kind, variables: dict(LINE)),
                    (main.store, "load_requirement", lambda *a, **k: {"data": {}}),
                    (main.store, "save_ir", boom)):
            asyncio.run(main.packaging_business_part_cost(PID, CODE, 1, "", [], USER))
        self.assertEqual(1, backend.writes, "只落一版结论文档")


# --------------------------------------------------------------------------- #
# D 组：GET 路由（Spec §C4）
# --------------------------------------------------------------------------- #
class DCostReadback(unittest.TestCase):
    def test_d1_shape_matches_the_geometric_route(self):
        body = _get()
        for key in ("part_code", "analysis", "summary", "source", "parts_id", "stale",
                    "stale_reason", "size_source", "size_source_ref", "geometry"):
            self.assertIn(key, body, "读回体必须给 %s（Spec §C4）" % key)
        self.assertEqual("authority_dimensions", body.get("size_source"))
        self.assertEqual("unbound", body.get("geometry"))

    def test_d2_empty_state_is_200_not_404(self):
        backend = _Backend()
        with _Patch((main, "_workflow_project", lambda *a, **k: None),
                    (parts, "load_business_parts", lambda *a, **k: dict(DOC)),
                    (parts, "get_backend", lambda: backend)):
            body = main.get_packaging_business_part_cost(PID, CODE, USER)
        self.assertIsNone(body.get("analysis"), "没跑过就是空态，不许 404（Spec §C4）")
        self.assertEqual("", body.get("parts_id"), "空态七键照给（Spec §C4）")
        self.assertIn("business_stale_reason", body)

    def test_d3_unknown_part_is_404(self):
        with _Patch((main, "_workflow_project", lambda *a, **k: None),
                    (parts, "load_business_parts", lambda *a, **k: dict(DOC))):
            with self.assertRaises(main.HTTPException) as caught:
                main.get_packaging_business_part_cost(PID, "NOPE-P99", USER)
        self.assertEqual(404, caught.exception.status_code)
        self.assertEqual("PACKAGING_BUSINESS_PART_NOT_FOUND", caught.exception.detail.get("code"))


# --------------------------------------------------------------------------- #
# E 组（护栏）：几何那两条路与非成本范围一个字不改
# --------------------------------------------------------------------------- #
class EGuardrails(unittest.TestCase):
    def test_e1_geometric_gate_is_untouched(self):
        self.assertEqual(("PACKAGING_PART_NOT_CLOSED", "PACKAGING_PART_MATERIAL_UNKNOWN",
                          "PACKAGING_PART_NOT_FOUND"),
                         tuple(parts.PROCESS_REJECT_CODES),
                         "几何那三条码与顺序逐字不变（Spec §C5）")
        self.assertEqual("PACKAGING_PART_NOT_CLOSED",
                         parts.processability({"part_code": "DWG-P01",
                                               "outline_status": "open"})["code"],
                         "闭合门槛不许被本批放宽（Spec §C5）")

    def test_e2_geometric_routes_are_unchanged(self):
        src = _source(MAIN_PY)
        for path in ('"/api/projects/{pid}/requirement/packaging-parts/{part_code}/cost"',
                     '"/api/projects/{pid}/requirement/packaging-parts/{part_code}/process"'):
            self.assertEqual(1, src.count(path), "几何那两条路径逐字不变、各一处（Spec §C5）")

    def test_e3_cost_engine_is_untouched(self):
        src = _source(ROOT / "tech_app" / "backend" / "services" / "packaging_cost.py")
        self.assertNotIn("business_cost_inputs", src, "成本算法与费率一个字不改（Spec §C5）")

    def test_e4_no_frontend_change(self):
        # `## 412` 是后端批：这条原本是"前端一行不许动"的**批次级冻结**；`## 413` 按
        # Spec `packaging-business-part-size-cost-entry.md` 补上了面板入口，冻结随之**重指**
        # 为"前端只多出那一条声明的业务件成本请求"（重指不等于放宽：新入口只许有一处）。
        src = _source(ROOT / "tech_app" / "frontend" / "app.js")
        self.assertEqual(3, src.count("packaging-business-parts/"),
                         "多出来的那一条必须指向业务部件成本路由（Spec §C3）")
        self.assertEqual(1, src.count('id="packagingBusinessPartCostBySize"'),
                         "面板入口的按钮只许有一处（Spec §C2）")
        self.assertEqual(1, src.count('$("packagingBusinessPartCostBySize")'),
                         "按钮的绑定点也只许有一处（Spec §C2）")


if __name__ == "__main__":
    unittest.main()

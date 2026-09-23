"""红测：业务部件没有几何时，工序明细必须能按**权威清单原文**编出来。

Spec：`docs/specs/packaging-business-part-process-by-authority-route.md`

现状缺口（代码级，都可指到行）：
  · `packaging_parts.processability()`（`:2261`）第一道门槛 `outline_status == "closed"` ——
    业务件行上没有这个键 → 恒 `PACKAGING_PART_NOT_CLOSED`；
  · `main.py` 的单件工艺路由（`PACKAGING_PART_PROCESS_PATH` `:8326`）先 `_packaging_part_row(pid, code)`
    （只认零件文档里的 `DWG-Pxx`）→ 业务编码 404；
  · 权威清单的 `length_mm` / `width_mm` / `material_text` / `process_text`（`BUSINESS_AUTHORITY_KEYS`
    `:3291`）从来没被送进 `process.outline_process(part, ...)`。

纪律：只读源码 + 假仓库 / 假文档 / 假工艺模型；不连 PG / SQLite 生产库、不发 HTTP、不写文件、
不落库、不写技术 IR、不调模型。禁止为了让红测转绿而修改本文件。
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
PROCESS_PY = ROOT / "tech_app" / "backend" / "services" / "process.py"
COST_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_cost.py"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"

PID = "testpid00001"
CODE = "JWXR21-P01"
BIZ_ID = "biz:authority0001"
BIZ_HASH = "bizhash-1"
USER = {"username": "PE1", "role": "process_manager"}

REJECT_CODES = ("PACKAGING_BUSINESS_PART_NOT_FOUND",
                "PACKAGING_BUSINESS_PART_SIZE_UNKNOWN",
                "PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN")
SIZE_SOURCES = ("authority_dimensions",)
INPUT_KEYS = ("ok", "code", "message", "missing_variables", "part_code", "name",
              "material_text", "process_text", "grounding", "size_source",
              "size_source_ref", "size_length", "size_width", "size_text")

#: 一行业务部件（真样本形状：权威资料 + 未绑定几何 + 工艺路线原文）
ROW = {"business_part_code": CODE, "name": "礼盒面纸",
       "authority": {"length_mm": 300.0, "width_mm": 200.0,
                     "material_text": "350G玖龙粉灰", "product_size_text": "300×200MM",
                     "process_text": "印刷→覆膜→模切", "layout_text": "",
                     "note": "客户备注",
                     "source": "酒盒 报价资料.xlsx#Sheet1!B12"},
       "geometry_binding": {"component_ids": [], "status": "unbound"}}

DOC = {"business_parts_id": BIZ_ID, "business_parts_hash": BIZ_HASH,
       "business_parts": [ROW], "geometry_evidence": {"components": []}}

GROUNDING = ("尺寸原文：300×200MM；权威尺寸：300×200 mm；材料：350G玖龙粉灰；"
             "工艺路线：印刷→覆膜→模切；备注：客户备注")
ASSUMPTION = ("按权威清单的尺寸（300×200 mm）与材料原文编制工序，未与 CAD 几何核过："
              "没有展开轮廓、没有排样，料厚未知")
PLAN = {"part_id": CODE, "part_name": "礼盒面纸", "material": "350G玖龙粉灰",
        "steps": [{"step_no": 10, "name": "备料"}, {"step_no": 20, "name": "印刷"},
                  {"step_no": 30, "name": "覆膜"}, {"step_no": 40, "name": "模切"}],
        "overall_note": ""}
COVERAGE = {"summary": {"reused": 0, "missing": 4}}


class _Patch:
    """最小打桩：进/出各一次 setattr，退出时按相反顺序还原。"""

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
    """假工艺模型产物：只实现路由用得上的 `model_dump()`（本批不调模型）。"""

    def __init__(self, data):
        self._data = dict(data)

    def model_dump(self):
        return dict(self._data)


def _post(*, doc=DOC, code=CODE, note="", row=None):
    backend, tasks, seen = _Backend(), _Tasks(), {}
    if row is not None:
        doc = {"business_parts_id": BIZ_ID, "business_parts_hash": BIZ_HASH,
               "business_parts": [row], "geometry_evidence": {"components": []}}

    def fake_outline(part, overall=None, geom=None, note="", attachments=None,
                     library="", lookup=None):
        seen["part"] = part
        seen["overall"] = overall
        seen["geom"] = geom
        seen["note"] = note
        seen["attachments"] = attachments
        return _Plan(PLAN), dict(COVERAGE)

    def fake_compute(plan_dict):
        seen["computed"] = dict(plan_dict)
        return {"step_count": len(plan_dict.get("steps") or [])}

    with _Patch((main, "_workflow_project", lambda *a, **k: None),
                (main, "tasks", tasks),
                (parts, "load_business_parts", lambda *a, **k: dict(doc) if doc else None),
                (parts, "get_backend", lambda: backend),
                (main.process, "outline_process", fake_outline),
                (main.process, "compute", fake_compute)):
        body = asyncio.run(main.packaging_business_part_process(PID, code, note, [], USER))
    return {"body": body, "backend": backend, "tasks": tasks, "seen": seen}


def _get(*, doc=DOC, code=CODE, stored=None):
    backend = _Backend()
    if stored is not None:
        backend.docs[parts.DOC_KEY_PROCESS] = {"items": [stored]}
    with _Patch((main, "_workflow_project", lambda *a, **k: None),
                (parts, "load_business_parts", lambda *a, **k: dict(doc) if doc else None),
                (parts, "get_backend", lambda: backend)):
        body = main.get_packaging_business_part_process(PID, code, USER)
    return {"body": body, "backend": backend}


def _inputs(*, row=ROW):
    return parts.business_process_inputs(row)


def _source(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def _function_body(name: str) -> str:
    text = _source(PARTS_PY)
    match = re.search(r"^def %s\(.*?\n" % re.escape(name), text, re.M | re.S)
    if not match:
        raise AssertionError("packaging_parts.py 缺少具名函数 %s()（Spec §C1/§C2/§C3）" % name)
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
# A 组：`business_process_inputs()`（Spec §C1）
# --------------------------------------------------------------------------- #
class AAuthorityRouteInputs(unittest.TestCase):
    def test_a1_authority_route_becomes_the_process_inputs(self):
        got = _inputs()
        self.assertEqual(INPUT_KEYS, tuple(got.keys()),
                         "返回键必须固定十四个（Spec §C1）")
        self.assertEqual(300.0, got.get("size_length"), "两轴给数值，不留文本（Spec §C1）")
        self.assertEqual(200.0, got.get("size_width"))
        self.assertIs(True, got.get("ok"), "权威尺寸与材料齐了就该给输入（Spec §C1）")
        self.assertEqual(CODE, got.get("part_code"))
        self.assertEqual("礼盒面纸", got.get("name"))
        self.assertEqual("350G玖龙粉灰", got.get("material_text"))
        self.assertEqual("印刷→覆膜→模切", got.get("process_text"),
                         "工艺路线原文一并带出（Spec §C1）")
        self.assertEqual("authority_dimensions", got.get("size_source"),
                         "尺寸口径必须点名 authority_dimensions（Spec §C1）")
        self.assertEqual("酒盒 报价资料.xlsx#Sheet1!B12", got.get("size_source_ref"),
                         "留痕要指回工作簿那一格（Spec §C1）")
        self.assertEqual("300×200MM", got.get("size_text"), "尺寸原文一并带出（Spec §C1）")

    def test_a2_missing_authority_size_is_its_own_code_and_missing_variable(self):
        row = {"business_part_code": CODE,
               "authority": {"material_text": "350G玖龙粉灰"}}
        got = _inputs(row=row)
        self.assertIs(False, got.get("ok"), "没有权威尺寸不许硬排工艺（Spec §C1）")
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

    def test_a4_missing_material_text_is_its_own_code(self):
        row = {"business_part_code": CODE,
               "authority": {"length_mm": 300.0, "width_mm": 200.0, "material_text": "  "}}
        got = _inputs(row=row)
        self.assertEqual("PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN", got.get("code"),
                         "没有材料原文 → 材料那条码（Spec §C1）")
        self.assertEqual(["material"], got.get("missing_variables"))
        self.assertIn("材料原文", str(got.get("message")), "message 要点名缺的是材料原文")

    def test_a5_row_material_is_the_documented_fallback(self):
        row = {"business_part_code": CODE, "material": "350G灰板",
               "authority": {"length_mm": 300.0, "width_mm": 200.0}}
        got = _inputs(row=row)
        self.assertIs(True, got.get("ok"), "行上 material 那条兜底必须留着（Spec §C1）")
        self.assertEqual("350G灰板", got.get("material_text"))

    def test_a6_missing_code_is_not_found(self):
        got = _inputs(row={"authority": {"length_mm": 1, "width_mm": 1, "material_text": "x"}})
        self.assertEqual("PACKAGING_BUSINESS_PART_NOT_FOUND", got.get("code"))
        self.assertEqual([], got.get("missing_variables"), "没有编码不是缺变量")

    def test_a7_codes_and_size_sources_are_closed_sets(self):
        self.assertEqual(set(REJECT_CODES), set(getattr(parts, "BUSINESS_PROCESS_REJECT_CODES", ())),
                         "拒绝码必须是闭集常量（Spec §C1）")
        self.assertEqual(set(SIZE_SOURCES), set(getattr(parts, "BUSINESS_PROCESS_SIZE_SOURCES", ())),
                         "尺寸口径必须是闭集常量（Spec §C1）")
        for row in (ROW, {}, {'business_part_code': CODE, 'authority': {}}, None, 7, "x", []):
            self.assertIn(_inputs(row=row).get("code") or "", ("",) + REJECT_CODES,
                          "任何输入只许给闭集里的码（Spec §C1）：%r" % (row,))

    def test_a8_never_throws_and_does_not_mutate(self):
        row = {"business_part_code": CODE, "authority": {"length_mm": [1], "width_mm": {"a": 1},
                                                         "material_text": 350}}
        before = repr(row)
        _inputs(row=row)          # 不抛就算过
        self.assertEqual(before, repr(row), "纯函数不许改入参（Spec §C1）")

    def test_a9_pure_function_has_no_io(self):
        body = _function_body("business_process_inputs")
        for token in ("get_backend(", "put_doc(", "open(", "requests", "load_business_parts("):
            self.assertNotIn(token, body, "纯函数体内不许出现 %s（Spec §C1）" % token)

    def test_a10_grounding_is_the_authority_block_in_fixed_order(self):
        self.assertEqual(GROUNDING, _inputs().get("grounding"),
                         "权威原文块逐字、顺序固定（Spec §C1）")
        bare = {"business_part_code": CODE, "material": "350G灰板",
                "authority": {"length_mm": 300.0, "width_mm": 200.0}}
        self.assertEqual("权威尺寸：300×200 mm；材料：350G灰板", _inputs(row=bare).get("grounding"),
                         "只收非空项、不写空段（Spec §C1）")


# --------------------------------------------------------------------------- #
# B 组：`business_as_ir_part()`（Spec §C2）
# --------------------------------------------------------------------------- #
class BBusinessPartAdapter(unittest.TestCase):
    def test_b1_identity_comes_from_the_business_row(self):
        part = parts.business_as_ir_part(ROW)
        self.assertEqual(CODE, part.part_id, "part_id 取业务部件编码（Spec §C2）")
        self.assertEqual("礼盒面纸", part.name, "name 取行上 name（Spec §C2）")
        self.assertEqual(1, part.quantity)
        self.assertIsNone(part.role, "业务件没有装配角色，不许编（Spec §C2）")

    def test_b2_no_geometric_features_are_invented(self):
        part = parts.business_as_ir_part(ROW)
        self.assertEqual([], list(part.features),
                         "没有闭合轮廓也没有料厚 → 一个特征都不许造（Spec §C2）")

    def test_b3_material_is_the_authority_text(self):
        self.assertEqual("350G玖龙粉灰", parts.business_as_ir_part(ROW).material.spec)
        row = {"business_part_code": CODE, "authority": {}}
        self.assertIsNone(parts.business_as_ir_part(row).material,
                          "没有材料原文就是没材料，不许给默认材料（Spec §C2）")

    def test_b4_confidence_never_pretends_geometry(self):
        self.assertEqual(0.4, parts.business_as_ir_part(ROW).confidence,
                         "有材料的业务件与几何件「开口件」同一档（Spec §C2）")
        row = {"business_part_code": CODE, "authority": {"length_mm": 1, "width_mm": 1}}
        self.assertEqual(0.3, parts.business_as_ir_part(row).confidence)
        for row in (ROW, {"business_part_code": CODE}, {}, None):
            self.assertLessEqual(parts.business_as_ir_part(row).confidence, 0.4,
                                 "业务件不许装成可信几何（Spec §C2）")

    def test_b5_provenance_note_names_the_route_and_the_gaps(self):
        note = parts.business_as_ir_part(ROW).provenance.note
        for token in ("packaging_business_part/%s" % CODE, "size_source=authority_dimensions",
                      "size=300×200 mm", "process_source=workbook", "outline=none",
                      "thickness=unknown"):
            self.assertIn(token, note, "溯源段逐字（Spec §C2）：%s" % token)
        bare = parts.business_as_ir_part({"business_part_code": CODE, "authority": {}})
        for token in ("size_source=unknown", "size=unknown", "process_source=none"):
            self.assertIn(token, bare.provenance.note, "取不到就说 unknown/none（Spec §C2）：%s" % token)

    def test_b6_never_throws(self):
        for row in (None, 7, "x", [], {"authority": None}, {"business_part_code": []}):
            parts.business_as_ir_part(row)        # 不抛就算过

    def test_b7_the_geometric_adapter_is_untouched(self):
        row = {"part_code": "DWG-P01", "name": "板件", "outline_status": "closed",
               "thickness_mm": 2.0, "unfolded_length_mm": 100.0, "unfolded_width_mm": 50.0,
               "material": {"spec": "灰板"}, "size_source": "outline_loop"}
        part = parts.as_ir_part(row)
        self.assertEqual(1, len(part.features), "几何件那条适配点一字不动（Spec §C6）")
        self.assertEqual("plate", part.features[0].type)


# --------------------------------------------------------------------------- #
# C 组：`business_process_assumption()`（Spec §C3）
# --------------------------------------------------------------------------- #
class CAssumptionText(unittest.TestCase):
    def test_c1_the_sentence_says_authority_route_and_not_checked(self):
        self.assertEqual(ASSUMPTION, parts.business_process_assumption(_inputs()),
                         "口径那句话逐字（Spec §C3）")

    def test_c2_bound_geometry_appends_one_clause(self):
        text = parts.business_process_assumption(_inputs(), geometry_part_code="DWG-P03")
        self.assertEqual(ASSUMPTION + "；这一件另有闭合几何件（DWG-P03），本结论有意按权威清单编制",
                         text, "绑了几何件也只是追加一句（Spec §C3）")

    def test_c3_no_conclusion_no_sentence(self):
        self.assertEqual("", parts.business_process_assumption({"ok": False}))
        self.assertEqual("", parts.business_process_assumption(None))
        self.assertEqual("", parts.business_process_assumption("x"))

    def test_c4_pure_and_never_throws(self):
        body = _function_body("business_process_assumption")
        for token in ("get_backend(", "put_doc(", "open(", "requests"):
            self.assertNotIn(token, body, "纯函数体内不许出现 %s（Spec §C3）" % token)
        parts.business_process_assumption({"ok": True}, geometry_part_code=[1])   # 不抛就算过


# --------------------------------------------------------------------------- #
# D 组：`POST …/packaging-business-parts/{code}/process`（Spec §C4）
# --------------------------------------------------------------------------- #
class DPostRoute(unittest.TestCase):
    def test_d1_route_exists_and_shape_is_the_async_task_one(self):
        got = _post()
        self.assertEqual(["task-1"], [got["body"].get("task_id")])
        self.assertEqual(1, len(got["tasks"].calls), "必须提交一个异步任务（Spec §C4）")
        call = got["tasks"].calls[0]
        self.assertEqual("packaging_business_part_process", call["name"])
        self.assertEqual(PID, call["pid"])
        self.assertEqual("PE1", call["actor"])
        self.assertTrue(call["dedup_key"], "任务必须有 dedup_key（Spec §C4）")

    def test_d2_stored_conclusion_is_the_business_one(self):
        got = _post()
        items = got["backend"].docs[parts.DOC_KEY_PROCESS]["items"]
        record = items[0]
        self.assertEqual(CODE, record.get("part_code"), "编码必须逐字是业务部件编码（Spec §C4）")
        self.assertEqual("", record.get("parts_id"),
                         "这份结论不是按几何零件算的，parts_id 必须为空（Spec §C4）")
        self.assertEqual(PLAN, record.get("plan"))
        self.assertEqual({"step_count": 4}, record.get("validation"))
        self.assertEqual(COVERAGE, record.get("coverage"))
        self.assertEqual({}, record.get("lookup"), "业务件没有知识库依据，不许装样子（Spec §C4）")
        self.assertEqual(ASSUMPTION, (record.get("assumptions") or [""])[0],
                         "口径那句话必须是第一条 assumption（Spec §C4）")
        self.assertEqual("authority_dimensions", record.get("size_source"))
        self.assertEqual("酒盒 报价资料.xlsx#Sheet1!B12", record.get("size_source_ref"))
        self.assertEqual("300×200MM", record.get("size_text"))
        self.assertEqual("unbound", record.get("geometry"))
        self.assertEqual(GROUNDING, record.get("grounding"))
        self.assertEqual(CODE, record.get("business_part_code"))
        self.assertEqual(BIZ_ID, record.get("business_parts_id"))
        self.assertEqual(BIZ_HASH, record.get("business_parts_hash"))
        self.assertEqual("task-1", (record.get("source") or {}).get("task_id"))

    def test_d3_the_model_gets_the_business_part_and_the_authority_block(self):
        got = _post(note="客户要求先覆膜再烫金")
        seen = got["seen"]
        part = seen.get("part")
        self.assertEqual(CODE, getattr(part, "part_id", ""),
                         "送进既有工艺链路的必须是业务件适配出来的 Part（Spec §C4）")
        self.assertEqual([], list(getattr(part, "features", [])),
                         "送进去的 Part 不许带编出来的几何特征（Spec §C4）")
        self.assertIsNone(seen.get("overall"), "业务件没有整体 IR（Spec §C4）")
        self.assertIsNone(seen.get("geom"), "业务件没有几何（Spec §C4）")
        note = str(seen.get("note") or "")
        self.assertIn(GROUNDING, note, "权威原文块必须进 note（Spec §C4）")
        self.assertIn("客户要求先覆膜再烫金", note, "用户补充说明必须进 note（Spec §C4）")
        self.assertLess(note.index(GROUNDING), note.index("客户要求先覆膜再烫金"),
                        "权威原文在前、用户说明在后（Spec §C4）")

    def test_d4_missing_size_is_409_and_no_task_is_submitted(self):
        row = {"business_part_code": CODE, "authority": {"material_text": "350G玖龙粉灰"}}
        with self.assertRaises(Exception) as ctx:
            _post(row=row)
        detail = getattr(ctx.exception, "detail", {})
        self.assertEqual(409, getattr(ctx.exception, "status_code", 0),
                         "缺前置条件 → 409（Spec §C4）")
        self.assertEqual("PACKAGING_BUSINESS_PART_SIZE_UNKNOWN", detail.get("code"))
        self.assertEqual(["authority_size"], detail.get("missing_variables"))
        self.assertIs(False, detail.get("retryable"), "缺数据不是可重试故障（Spec §C4）")

    def test_d5_unknown_business_part_is_404(self):
        with self.assertRaises(Exception) as ctx:
            _get(code="NOPE-1")
        self.assertEqual(404, getattr(ctx.exception, "status_code", 0),
                         "清单里没有这一件 → 404（Spec §C5）")
        self.assertEqual("PACKAGING_BUSINESS_PART_NOT_FOUND",
                         getattr(ctx.exception, "detail", {}).get("code"))

    def test_d6_no_technical_ir_is_written(self):
        seen = {}

        def boom(*a, **k):
            seen["ir"] = True
            raise AssertionError("业务件工艺结论不许碰技术 IR（Spec §C4）")

        backend, tasks = _Backend(), _Tasks()

        def fake_outline(part, overall=None, geom=None, note="", attachments=None,
                         library="", lookup=None):
            return _Plan(PLAN), dict(COVERAGE)

        with _Patch((main, "_workflow_project", lambda *a, **k: None),
                    (main, "tasks", tasks),
                    (parts, "load_business_parts", lambda *a, **k: dict(DOC)),
                    (parts, "get_backend", lambda: backend),
                    (main.process, "outline_process", fake_outline),
                    (main.process, "compute", lambda plan: {"step_count": 4}),
                    (main.store, "save_ir", boom),
                    (main.store, "load_ir", boom)):
            asyncio.run(main.packaging_business_part_process(PID, CODE, "", [], USER))
        self.assertEqual({}, seen, "save_ir / load_ir 一次都不许碰（Spec §C4）")

    def test_d7_write_roles_are_reused(self):
        src = _source(MAIN_PY)
        block = src[src.index("PACKAGING_BUSINESS_PART_PROCESS_PATH ="):
                    src.index("PACKAGING_BUSINESS_PART_PROCESS_PATH =") + 9000]
        self.assertIn("packaging_match.BOX_MATCH_DECIDE_ROLES", block,
                      "写权限引用既有角色常量（Spec §C4）")

    def test_d8_dedup_key_follows_the_inputs(self):
        first = _post()["tasks"].calls[0]["dedup_key"]
        self.assertEqual(first, _post()["tasks"].calls[0]["dedup_key"],
                         "同样的输入 → 同一个 dedup_key（Spec §C4）")
        self.assertNotEqual(first, _post(note="客户要求先覆膜再烫金")["tasks"].calls[0]["dedup_key"],
                            "输入变了 dedup_key 必须跟着变（Spec §C4）")
        self.assertEqual(1, _post()["backend"].writes, "只落一版结论（Spec §C4）")


# --------------------------------------------------------------------------- #
# E 组：`GET …/packaging-business-parts/{code}/process`（Spec §C5）
# --------------------------------------------------------------------------- #
class EGetRoute(unittest.TestCase):
    def test_e1_empty_state_is_200_with_all_keys(self):
        body = _get()["body"]
        self.assertIsNone(body.get("plan"), "没跑过 → plan 为 null，不 404（Spec §C5）")
        for key in ("part_code", "plan", "validation", "coverage", "assumptions", "source",
                    "size_source", "size_source_ref", "size_text", "geometry",
                    "parts_id", "stale", "stale_reason", "business_part_code",
                    "business_parts_id", "business_stale", "business_stale_reason"):
            self.assertIn(key, body, "读回体缺键 %s（Spec §C5）" % key)
        self.assertEqual(CODE, body.get("part_code"))

    def test_e2_stored_conclusion_reads_back(self):
        stored = {"part_code": CODE, "parts_id": "", "plan": PLAN,
                  "validation": {"step_count": 4}, "coverage": COVERAGE,
                  "assumptions": [ASSUMPTION], "size_source": "authority_dimensions",
                  "size_source_ref": "xlsx#B12", "size_text": "300×200MM",
                  "geometry": "unbound", "business_part_code": CODE,
                  "business_parts_id": BIZ_ID,
                  "source": {"computed_at": "2026-09-22 10:00:00"}}
        body = _get(stored=stored)["body"]
        self.assertEqual(PLAN, body.get("plan"), "落库的工艺结论必须原样读回来（Spec §C5）")
        self.assertEqual(COVERAGE, body.get("coverage"))
        self.assertEqual([ASSUMPTION], body.get("assumptions"))
        self.assertEqual("authority_dimensions", body.get("size_source"))
        self.assertEqual("unbound", body.get("geometry"))

    def test_e3_read_is_pure(self):
        got = _get()
        self.assertEqual(0, got["backend"].writes, "读接口不许写库（Spec §C5）")

    def test_e4_paths_are_declared(self):
        src = _source(MAIN_PY)
        for path in ('"/api/projects/{pid}/requirement/packaging-business-parts/{code}/process"',
                     '"/api/projects/{pid}/requirement/packaging-business-parts/{code}/cost"',
                     '"/api/projects/{pid}/requirement/packaging-parts/{part_code}/process"',
                     '"/api/projects/{pid}/requirement/packaging-parts/{part_code}/cost"'):
            self.assertEqual(1, src.count(path), "路径逐字、各一处（Spec §C5）：%s" % path)


# --------------------------------------------------------------------------- #
# F 组（护栏）：几何那两条路、算法与非本批范围一个字不改
# --------------------------------------------------------------------------- #
class FGuardrails(unittest.TestCase):
    def test_f1_geometric_gate_is_untouched(self):
        self.assertEqual(("PACKAGING_PART_NOT_CLOSED", "PACKAGING_PART_MATERIAL_UNKNOWN",
                          "PACKAGING_PART_NOT_FOUND"),
                         tuple(parts.PROCESS_REJECT_CODES),
                         "几何那三条码与顺序逐字不变（Spec §C6）")
        verdict = parts.processability({"part_code": "DWG-P01", "outline_status": "open",
                                        "material": {"spec": "灰板"}, "thickness_mm": 2.0})
        self.assertEqual("PACKAGING_PART_NOT_CLOSED", verdict["code"],
                         "闭合门槛不许被本批放宽（Spec §C6）")
        business = parts.processability(dict(ROW, part_code=CODE))
        self.assertEqual("PACKAGING_PART_NOT_CLOSED", business["code"],
                         "业务件仍过不了几何那道门槛 —— 本批走的是另一条路（Spec §C6）")

    def test_f2_process_algorithm_is_untouched(self):
        src = _source(PROCESS_PY)
        self.assertNotIn("business_", src, "工艺算法里不许长出业务件分支（Spec §C6）")
        self.assertEqual(1, src.count("def outline_process("), "只许一处 outline_process（Spec §C6）")

    def test_f3_cost_engine_is_untouched(self):
        src = _source(COST_PY)
        for token in ("business_process_inputs", "business_as_ir_part"):
            self.assertNotIn(token, src, "成本算法与费率一个字不改（Spec §C6）")

    def test_f4_no_frontend_change(self):
        # `## 412` 那条「前端一行不许动」的批次级冻结已被 `## 413` 重指为"只多出业务件成本那一处"；
        # 本批（`## 414`，后端）原本锁"前端一处都不许多"（计数 3）。`## 415` 按 Spec
        # `packaging-business-part-process-entry.md` §C4 把工艺那颗按钮接上面板，这条冻结随之
        # **重指**为 4 —— **重指不等于放宽**：多出来的那一条只许是业务部件工艺路由那一处
        # （`## 412` 的 E4 里逐个点名了两颗按钮）。
        # `## 480` 按 Spec `packaging-2-1-result-parts-and-shape-only-pane.md` §2.4b C6 又**重指**
        # 一次（4 → 5）：多出来的那一条只许是按图纸补推导那一条路由。重指不等于放宽。
        src = _source(APP_JS)
        self.assertEqual(5, src.count("packaging-business-parts/"),
                         "前端只许多出那两条声明的业务件请求（工艺 + 补推导；Spec §C4 / 2.1-result §2.4b）")

    def test_f5_business_cost_route_is_untouched(self):
        src = _source(MAIN_PY)
        self.assertEqual(1, src.count("def packaging_business_part_cost("),
                         "业务件成本路由一字不动（Spec §C6）")
        self.assertEqual(1, src.count("def get_packaging_business_part_cost("))


if __name__ == "__main__":
    unittest.main()

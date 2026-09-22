"""红测：单件工艺/成本结论必须带上业务部件身份（读侧也要能判业务清单漂移）。

Spec：`docs/specs/packaging-part-conclusion-business-identity.md`

现状缺口（代码级，三处，都可指到行）：
  · 后端**没有**"几何件 → 业务件"的映射：前端有
    `packagingBusinessPartDownstreamTarget()`（`app.js:2422`），后端反向那一半不存在；
  · 两条 POST 路由落的结论行（`main.py:8418-8426` 工艺 / `:8551-8559` 成本）只有 `part_code` /
    `parts_id`，没有 `business_part_code` / `business_parts_id` / `business_parts_hash`；
  · 读侧 `main.py:8579 _packaging_part_conclusion_version()` 只披露几何版本三键，
    业务清单重新导入在返回体上完全看不出来 —— 而
    `packaging_parts.business_binding_stale_reason()`（`:3772`）写好了却**全仓无人调用**。

纪律：只读源码 + 假后端 / 假文档；不连 PG / SQLite 生产库、不发 HTTP、不写任何文件、不落库。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

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
PART_CODE = "DWG-P01"
OLD_BIZ_ID = "biz:oldoldoldoldold"
NEW_BIZ_ID = "biz:newnewnewnewnew"
COMPONENT = "geometry:comp:7"

LOOKUP = "geometry_business_part"
LOOKUP_REASONS = ("", "business_doc_unavailable", "geometry_ref_missing", "geometry_unbound")
LOOKUP_RULE_ID = "business_part_lookup_by_component_v1"

#: 一行几何零件（`component_id` 是绑定用的命名空间）+ 一份业务部件文档
PART_ROW = {"part_code": PART_CODE, "component_id": COMPONENT,
            "geometry_component_ref": COMPONENT, "outline_status": "closed"}
BUSINESS_DOC = {
    "business_parts_id": OLD_BIZ_ID, "business_parts_hash": "bizhash-old",
    "business_parts": [
        {"business_part_code": "JWXR21-P02", "geometry_binding": {"component_ids": ["geometry:comp:9"]}},
        {"business_part_code": "JWXR21-P01", "geometry_binding": {"component_ids": [COMPONENT]}},
    ],
    "geometry_evidence": {"components": [{"component_id": COMPONENT}]},
}


class _Patch:
    """把若干 (对象, 属性) 换成临时实现，退出时原样还原。"""

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


def _lookup():
    return getattr(parts, LOOKUP, None)


def _source() -> str:
    return PARTS_PY.read_text(encoding="utf-8")


def _function_body(name: str) -> str:
    """抽一个顶层具名函数的函数体（缩进对齐，含嵌套块）。"""
    text = _source()
    match = re.search(r"^def %s\(.*?\n" % re.escape(name), text, re.M | re.S)
    if not match:
        raise AssertionError("packaging_parts.py 缺少具名函数 %s()（Spec §C1）" % name)
    rest = text[match.end():]
    lines = []
    for line in rest.splitlines():
        if not line.strip():
            lines.append(line)
            continue
        if not line.startswith((" ", "\t")):
            break
        lines.append(line)
    return "\n".join(lines)


def _route(name, record, *, current_business=BUSINESS_DOC):
    """调两个 GET 读路由；`current_business=None` 表示当前业务清单读不到。"""
    loader = "load_part_process" if name == "get_packaging_part_process" else "load_part_cost"
    with _Patch((main, "_workflow_project", lambda *a, **k: None),
                (main, "_packaging_part_row", lambda *a, **k: {"row": dict(PART_ROW)}),
                (parts, loader, lambda *a, **k: dict(record) if record else {}),
                (parts, "load_parts", lambda *a, **k: {"parts_id": "parts:whatever"}),
                (parts, "load_business_parts",
                 lambda *a, **k: dict(current_business) if current_business else None)):
        return getattr(main, name)(PID, PART_CODE, {"username": "PE1"})


def _record(kind="process", **extra):
    base = {"part_code": PART_CODE, "parts_id": "parts:whatever"}
    if kind == "process":
        base.update({"plan": {"steps": [{"seq": 1, "name": "模切"}]}, "validation": {"ok": True}})
    else:
        base.update({"analysis": {"unit_cost": 1.23}, "summary": "单件成本"})
    base.update(extra)
    return base


# --------------------------------------------------------------------------- #
# A 组：纯函数 `geometry_business_part()`（Spec §C1）
# --------------------------------------------------------------------------- #
class AGeometryBusinessPartLookup(unittest.TestCase):
    def test_a1_bound_component_gives_the_business_code(self):
        lookup = _lookup()
        self.assertTrue(callable(lookup),
                        "packaging_parts.py 缺少顶层纯函数 %s()（Spec §C1）" % LOOKUP)
        got = lookup(PART_ROW, BUSINESS_DOC)
        self.assertIs(True, got.get("mapped"), "命中业务件时必须给 mapped=True（Spec §C1）")
        self.assertEqual("JWXR21-P01", got.get("business_part_code"),
                         "必须按 component_id 命中业务行（Spec §C1）")
        self.assertEqual(OLD_BIZ_ID, got.get("business_parts_id"),
                         "结论要留痕「照哪一版业务清单算的」（Spec §C1/C2）")
        self.assertEqual("bizhash-old", got.get("business_parts_hash"), "同上：hash 也要带出")
        self.assertEqual("", got.get("reason") or "", "命中了就没有原因码")

    def test_a2_multiple_hits_pick_the_lowest_code(self):
        doc = {"business_parts_id": OLD_BIZ_ID, "business_parts_hash": "h", "business_parts": [
            {"business_part_code": "JWXR21-P07", "geometry_binding": {"component_ids": [COMPONENT]}},
            {"business_part_code": "JWXR21-P03", "geometry_binding": {"component_ids": [COMPONENT]}},
        ]}
        got = _lookup()(PART_ROW, doc)
        self.assertEqual("JWXR21-P03", got.get("business_part_code"),
                         "多件命中必须取编码升序第一个（确定性，不按遍历顺序碰运气）（Spec §C1）")

    def test_a3_unbound_is_its_own_reason_but_still_carries_the_version(self):
        doc = {"business_parts_id": OLD_BIZ_ID, "business_parts_hash": "bizhash-old",
               "business_parts": [{"business_part_code": "JWXR21-P02",
                                   "geometry_binding": {"component_ids": ["geometry:comp:9"]}}]}
        got = _lookup()(PART_ROW, doc)
        self.assertIs(False, got.get("mapped"), "没命中就是没命中（Spec §C1）")
        self.assertEqual("geometry_unbound", got.get("reason"),
                         "有引用但命不中 → geometry_unbound（Spec §C1）")
        self.assertEqual(OLD_BIZ_ID, got.get("business_parts_id"),
                         "没绑到业务件也照样留痕「照哪一版清单算的」（Spec §C1/C2）")

    def test_a4_no_component_ref_is_its_own_reason(self):
        got = _lookup()({"part_code": PART_CODE}, BUSINESS_DOC)
        self.assertIs(False, got.get("mapped"))
        self.assertEqual("geometry_ref_missing", got.get("reason"),
                         "行上没有任何组件引用 → geometry_ref_missing（Spec §C1）")

    def test_a5_unreadable_business_document_is_its_own_reason(self):
        for doc in (None, {}, {"business_parts": []}):
            got = _lookup()(PART_ROW, doc)
            self.assertIs(False, got.get("mapped"), "清单读不到不许猜一件（Spec §C1）")
            self.assertEqual("business_doc_unavailable", got.get("reason"),
                             "业务文档读不到 → business_doc_unavailable（Spec §C1）：%r" % (doc,))

    def test_a6_reasons_are_a_closed_set(self):
        self.assertEqual(set(LOOKUP_REASONS), set(getattr(parts, "BUSINESS_PART_LOOKUP_REASONS", ())),
                         "原因码必须是闭集常量 BUSINESS_PART_LOOKUP_REASONS（Spec §C1）")
        self.assertEqual(LOOKUP_RULE_ID, getattr(parts, "BUSINESS_PART_LOOKUP_RULE_ID", ""),
                         "规则 id 必须是常量 BUSINESS_PART_LOOKUP_RULE_ID（Spec §C1）")
        for row, doc in ((PART_ROW, BUSINESS_DOC), (PART_ROW, {}), ({"part_code": "x"}, BUSINESS_DOC),
                         (None, None), ("nonsense", 3)):
            got = _lookup()(row, doc)
            self.assertIn(got.get("reason") or "", LOOKUP_REASONS,
                          "任何输入都只许给闭集里的原因码（Spec §C1）：%r × %r" % (row, doc))

    def test_a7_pure_function_has_no_io(self):
        body = _function_body(LOOKUP)
        for token in ("get_backend(", "put_doc(", "open(", "requests", "load_business_parts("):
            self.assertNotIn(token, body, "纯函数体内不许出现 %s（Spec §C1/C5）" % token)

    def test_a8_accepts_single_ref_and_numeric_component_ids(self):
        row = {"part_code": PART_CODE, "component_id": 7, "geometry_component_ref": "geometry:comp:7"}
        doc = {"business_parts_id": OLD_BIZ_ID, "business_parts_hash": "h",
               "business_parts": [{"business_part_code": "JWXR21-P05",
                                   "geometry_component_ref": [7]}]}
        got = _lookup()(row, doc)
        self.assertIs(True, got.get("mapped"),
                      "组件引用要 String 化后比对（单个 / 数组、数字都要认）（Spec §C1）")
        self.assertEqual("JWXR21-P05", got.get("business_part_code"))


# --------------------------------------------------------------------------- #
# B 组：写侧 —— 结论行必须带业务身份（Spec §C2）
# --------------------------------------------------------------------------- #
class BWrittenConclusionCarriesIdentity(unittest.TestCase):
    def _saved(self, *rows):
        class _Backend:
            def __init__(self):
                self.doc = {"items": [dict(row) for row in rows]}

            def get_doc(self, project_id, key):
                return self.doc

            def put_doc(self, project_id, key, doc):
                self.doc = doc

        return _Backend()

    def test_b1_identity_for_row_reads_the_current_document(self):
        with _Patch((parts, "load_business_parts",
                     lambda *a, **k: dict(BUSINESS_DOC))):
            got = parts.business_identity_for_row(PID, dict(PART_ROW))
        self.assertEqual({"business_part_code": "JWXR21-P01", "business_parts_id": OLD_BIZ_ID,
                          "business_parts_hash": "bizhash-old"}, got,
                         "business_identity_for_row() 必须回固定三键（Spec §C2）")

    def test_b2_identity_for_row_without_document_gives_three_empty_keys(self):
        with _Patch((parts, "load_business_parts", lambda *a, **k: None)):
            got = parts.business_identity_for_row(PID, dict(PART_ROW))
        self.assertEqual({"business_part_code": "", "business_parts_id": "",
                          "business_parts_hash": ""}, got,
                         "清单读不到 → 三键全空，而且仍然要给键（Spec §C2）")

    def test_b3_saved_conclusion_row_carries_the_three_keys(self):
        backend = self._saved()
        ident = _lookup()(PART_ROW, BUSINESS_DOC)
        payload = dict(_record("process"), business_part_code=ident.get("business_part_code"),
                       business_parts_id=ident.get("business_parts_id"),
                       business_parts_hash=ident.get("business_parts_hash"))
        with _Patch((parts, "get_backend", lambda: backend)):
            saved = parts.save_part_process(PID, payload)
        for key in ("business_part_code", "business_parts_id", "business_parts_hash"):
            self.assertIn(key, saved, "结论行必须带 %s（Spec §C2）" % key)
        self.assertEqual("parts:whatever", saved.get("parts_id"),
                         "既有 `parts_id` 逐字不变（Spec §C4）")
        self.assertEqual({"seq": 1, "name": "模切"}, saved.get("plan", {}).get("steps", [{}])[0])

    def test_b4_identical_input_is_still_idempotent(self):
        backend = self._saved()
        payload = dict(_record("process"), business_part_code="JWXR21-P01",
                       business_parts_id=OLD_BIZ_ID, business_parts_hash="bizhash-old")
        with _Patch((parts, "get_backend", lambda: backend)):
            parts.save_part_process(PID, payload)
            parts.save_part_process(PID, payload)
            rows = backend.doc["items"]
        self.assertEqual(1, len(rows), "输入完全相同的重跑仍必须幂等（不新增版本）（Spec §C2）")

    def test_b5_reimported_business_list_is_not_treated_as_the_same_row(self):
        backend = self._saved()
        first = dict(_record("process"), business_part_code="JWXR21-P01",
                     business_parts_id=OLD_BIZ_ID, business_parts_hash="bizhash-old")
        second = dict(first, business_parts_id=NEW_BIZ_ID, business_parts_hash="bizhash-new")
        with _Patch((parts, "get_backend", lambda: backend)):
            parts.save_part_process(PID, first)
            saved = parts.save_part_process(PID, second)
        self.assertEqual(NEW_BIZ_ID, saved.get("business_parts_id"),
                         "换了一版业务清单后重跑的结论不许被当成「同一份」（Spec §C2）")

    def test_b6_routes_write_the_identity(self):
        src = MAIN_PY.read_text(encoding="utf-8")
        start = src.index("async def packaging_part_process(")
        block = src[start:src.index("PACKAGING_PART_STALE_REASON", start)]
        self.assertIn("business_identity_for_row", block,
                      "两条 POST 路由落库前必须取业务身份（Spec §C2）："
                      "`packaging_parts.business_identity_for_row(pid, row)`")
        self.assertEqual(2, block.count("business_identity_for_row"),
                         "工艺与成本**各一处**，不许散落多份（Spec §C2）")


# --------------------------------------------------------------------------- #
# C 组：读侧 —— 回显业务身份 + 披露业务清单漂移（Spec §C3）
# --------------------------------------------------------------------------- #
class CReadbackDisclosesBusinessVersion(unittest.TestCase):
    def test_c1_both_routes_echo_the_business_identity(self):
        for name, key in (("get_packaging_part_process", "plan"),
                          ("get_packaging_part_cost", "analysis")):
            body = _route(name, _record("process" if key == "plan" else "cost",
                                        business_part_code="JWXR21-P01",
                                        business_parts_id=OLD_BIZ_ID,
                                        business_parts_hash="bizhash-old"))
            self.assertEqual("JWXR21-P01", body.get("business_part_code"),
                             "%s：读回体必须带出业务部件编码（Spec §C3）" % name)
            self.assertEqual(OLD_BIZ_ID, body.get("business_parts_id"),
                             "%s：读回体必须带出 business_parts_id（Spec §C3）" % name)
            self.assertTrue(body.get(key), "%s：既有结论字段逐字不变" % name)

    def test_c2_reimported_list_makes_the_conclusion_stale(self):
        current = dict(BUSINESS_DOC, business_parts_id=NEW_BIZ_ID, business_parts_hash="bizhash-new")
        for name in ("get_packaging_part_process", "get_packaging_part_cost"):
            body = _route(name, _record("process" if name.endswith("process") else "cost",
                                        business_part_code="JWXR21-P01",
                                        business_parts_id=OLD_BIZ_ID,
                                        business_parts_hash="bizhash-old"),
                          current_business=current)
            self.assertIs(True, body.get("business_stale"),
                          "%s：业务清单重导入后必须标出来（Spec §C3）—— 现在返回体里完全看不出来"
                          % name)
            self.assertEqual("business_parts_reimported", body.get("business_stale_reason"),
                             "%s：原因码复用 business_binding_stale_reason()（Spec §C3）" % name)

    def test_c3_same_version_is_not_stale(self):
        body = _route("get_packaging_part_process",
                      _record(business_part_code="JWXR21-P01", business_parts_id=OLD_BIZ_ID,
                              business_parts_hash="bizhash-old"))
        self.assertIsNot(True, body.get("business_stale"), "同一版清单不许标过期（Spec §C3）")
        self.assertEqual("", body.get("business_stale_reason") or "", "同版没有原因码")

    def test_c4_unreadable_current_list_is_unknown_not_stale(self):
        body = _route("get_packaging_part_process",
                      _record(business_part_code="JWXR21-P01", business_parts_id=OLD_BIZ_ID),
                      current_business=None)
        self.assertEqual("business_parts_unknown", body.get("business_stale_reason"),
                         "当前清单读不到时「比较不了」，不许断言过期（Spec §C3）")
        self.assertIsNot(True, body.get("business_stale"), "`business_parts_unknown` 时不许说过期")

    def test_c5_empty_state_also_carries_the_four_keys(self):
        body = _route("get_packaging_part_process", {})
        self.assertEqual("", body.get("business_part_code"), "空态也要给键（Spec §C3）")
        self.assertEqual("", body.get("business_parts_id"), "空态也要给键（Spec §C3）")
        self.assertIs(False, bool(body.get("business_stale")), "空态不是过期")
        self.assertEqual("", body.get("business_stale_reason") or "", "空态不许给原因码")
        self.assertIsNone(body.get("plan"), "既有空态形状逐字不变")

    def test_c6_the_verdict_lives_in_exactly_one_place(self):
        src = MAIN_PY.read_text(encoding="utf-8")
        start = src.index("def _packaging_part_conclusion_version(")
        block = src[start:src.index("\n@app.", start)]
        # 只数**代码**里的调用点（docstring 里提到判据名字是文档，不是第二份实现）。
        code = re.sub(r'""".*?"""', "", block, count=1, flags=re.S)
        self.assertIn("business_binding_stale_reason", code,
                      "判据只许有一处：`packaging_parts.business_binding_stale_reason()`（Spec §C3）"
                      "—— 它在 packaging_parts 里写好却全仓无人调用")
        self.assertEqual(1, code.count("business_binding_stale_reason"),
                         "同一处判据不许写两遍（Spec §C3）")


# --------------------------------------------------------------------------- #
# D 组（护栏）：既有几何版本口径与业务文档结构一个字不改
# --------------------------------------------------------------------------- #
class DGuardrails(unittest.TestCase):
    def test_d1_parts_stale_reason_is_unchanged(self):
        self.assertEqual("", parts.parts_stale_reason("a", "a"))
        self.assertEqual("parts_reparsed", parts.parts_stale_reason("a", "b"))
        self.assertEqual("parts_unknown", parts.parts_stale_reason("", "b"))
        self.assertEqual("parts_unknown", parts.parts_stale_reason("a", ""))

    def test_d2_business_stale_reason_is_unchanged(self):
        self.assertEqual("", parts.business_binding_stale_reason("a", "a"))
        self.assertEqual("business_parts_reimported", parts.business_binding_stale_reason("a", "b"))
        self.assertEqual("business_parts_unknown", parts.business_binding_stale_reason("", "b"))
        self.assertEqual("business_parts_unknown", parts.business_binding_stale_reason("a", ""))

    def test_d3_no_new_routes(self):
        src = MAIN_PY.read_text(encoding="utf-8")
        for path in ('"/api/projects/{pid}/requirement/packaging-parts/{part_code}/process"',
                     '"/api/projects/{pid}/requirement/packaging-parts/{part_code}/cost"'):
            self.assertEqual(1, src.count(path), "这条路由的路径常量逐字不变、且只有一处（Spec §C4）")

    def test_d4_business_document_shape_is_unchanged(self):
        doc = parts.business_parts_document({}, {"parts": []})
        for key in ("engine_version", "business_parts", "geometry_evidence", "stats", "unavailable",
                    "bindings_shared", "legacy_parts_id", "source", "authority", "thumbnail",
                    "business_parts_id", "business_parts_hash"):
            self.assertIn(key, doc, "业务部件文档结构一个字不改（Spec §C4）：缺 %s" % key)


if __name__ == "__main__":
    unittest.main()

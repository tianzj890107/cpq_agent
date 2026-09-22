"""红测：图纸零件的业务角色 —— 未映射清单读得回来 + 人工映射有生产入口。

Spec：`docs/specs/packaging-part-role-manual-mapping.md`

现状缺口（代码级，可复现）：
  · `packaging_parts.bind_rows()` 已经算出 `role_unbound` / `role_unbound_total`，
    但 `packaging_bom._bind_parts()` 只取 `items` / `pairing_review` 两项，把未映射清单丢掉了，
    所以 `load_bom()` 输出里从来没有这两个键 —— 34 上 `box_part` 11 行全部 `unbound`，没人看得见；
  · `BINDING_METHODS` 里的 `manual_mapping` 全仓 0 个触发点（`main.py` / `app.js` 均无
    `role-map`），§4.4 要求"必须先完成人工映射"却做不完；
  · `build_bom()` 里没有任何重放逻辑，人工映射在重算 BOM 之后必丢。

夹具按 34 真实读回来的形状写死（`648d09d57f6f` / `REQ-648D09D57F6F`，2026-09-22，SM1）。

纪律：只读源码 + 驱动纯函数；不连 PG、不发 HTTP、不写任何文件。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_bom  # noqa: E402

MAIN_SRC = (ROOT / "tech_app/backend/main.py").read_text(encoding="utf-8")
APP_SRC = (ROOT / "tech_app/frontend/app.js").read_text(encoding="utf-8")
BOM_SRC = (ROOT / "tech_app/backend/services/packaging_bom.py").read_text(encoding="utf-8")
PARTS_SRC = (ROOT / "tech_app/backend/services/packaging_parts.py").read_text(encoding="utf-8")

AUDIT_ACTION = "workflow:packaging_bom_role_mapped"

#: 34 上真实的一行（`item_key=WINE-P01`，尺寸来自未闭合件的包围盒，零件 role=unknown）。
BOX_PART_ROW = {
    "project_id": "648d09d57f6f",
    "requirement_no": "REQ-648D09D57F6F",
    "bom_category": "box_part",
    "item_key": "WINE-P01",
    "item_name": "左盖面纸",
    "source": "dwg_parts",
    "part_code": "WINE-P01",
    "component": "面纸",
    "material": "225G铜版底PET光银",
    "quantity": 1.0,
    "unit": "件",
    "length_mm": 440.123,
    "width_mm": 482.92,
    "height_mm": None,
    "status": "computed",
    "locked": 0,
    "missing_variables": [],
    "size_source_json": json.dumps({
        "length": None, "width": None, "height": None,
        "dwg_binding": {
            "component_id": "cmp:50", "part_code": "DWG-P01",
            "rule_id": "dwg_parts_row_pairing_v1", "fallback_paired": False,
            "original_missing_variables": [],
            "pairing_basis": "位置配对：第 1 个待绑行 ↔ 面积第 1 大的零件",
            "material_match": None, "size_source": "component_bbox",
            "outline_status": "open", "size_quality": "bbox_only",
        },
    }, ensure_ascii=False),
}
#: 不该进未映射清单的行（同一份 BOM 里的材料行 / 工序行）。
MATERIAL_ROW = {"bom_category": "material", "item_key": "MAT-01", "item_name": "灰板",
                "part_code": "", "material": "1.8mm灰板", "status": "needs_input"}
PROCESS_ROW = {"bom_category": "process", "item_key": "PROC-01", "item_name": "覆膜",
               "part_code": "", "status": "computed"}
TEMPLATES = [
    {"part_code": "RB02001-P01", "component": "面纸"},
    {"part_code": "RB02001-P02", "component": "面纸"},
    {"part_code": "RB02001-P05", "component": "灰板"},
    {"part_code": "RB02001-P12", "component": ""},
    {"part_code": "RB02001-P13", "component": None},
]


def _has(needle, haystack, message):
    """源码级断言：只报自己那句话，不把整份源码倒进失败信息里。"""
    if needle not in haystack:
        raise AssertionError(message)


def _func(name):
    fn = getattr(packaging_bom, name, None)
    if not callable(fn):
        raise AssertionError("packaging_bom.%s() 不存在：未映射清单/人工映射没有可驱动的纯函数（Spec §3）" % name)
    return fn


def _rows():
    return json.loads(json.dumps([BOX_PART_ROW, MATERIAL_ROW, PROCESS_ROW], ensure_ascii=False))


def _binding(row):
    source = row.get("size_source_json")
    source = json.loads(source) if isinstance(source, str) else (source or {})
    return source.get("dwg_binding") or {}


def _box_type():
    return {"box_type_code": "YT-DWG-WINE-700ML", "parts": TEMPLATES}


def _error(test, status, code, fn):
    try:
        fn()
    except packaging_bom.BomError as exc:                    # noqa: PERF203
        test.assertEqual(int(exc.status_code), status,
                         "status_code 应为 %d（Spec §3 失败口径表）" % status)
        test.assertEqual(exc.code, code, "code 应为 %s（Spec §3 失败口径表）" % code)
        return
    test.fail("应当抛 BomError(%d, %s)，却静默通过了（Spec §3 失败口径表）" % (status, code))


class ACandidatesAndStatus(unittest.TestCase):
    def test_a1_role_candidates_dedupe_keep_order_drop_empty(self):
        got = _func("role_candidates")(TEMPLATES)
        self.assertEqual(list(got), ["面纸", "灰板"],
                         "候选角色只能来自确认盒型模板的 component：按模板顺序去重、丢空值（Spec §2.2）")

    def test_a2_status_lists_only_unmapped_part_rows(self):
        status = _func("role_map_status")(_rows(), box_type_code="YT-DWG-WINE-700ML",
                                          part_templates=TEMPLATES)
        self.assertEqual(status.get("engine_version"), "packaging_bom_role_map_v1")
        self.assertEqual(status.get("candidates_source"), "confirmed_box_type")
        self.assertEqual(status.get("box_type_code"), "YT-DWG-WINE-700ML")
        self.assertEqual(list(status.get("role_candidates") or []), ["面纸", "灰板"])
        rows = status.get("items") or []
        self.assertEqual([row.get("item_key") for row in rows], ["WINE-P01"],
                         "只有绑到零件的 box_part/optional_part 行才进未映射清单（Spec §2.1）")
        row = rows[0]
        self.assertEqual(row.get("part_code"), "DWG-P01")
        self.assertEqual(row.get("part_role"), "unbound")
        self.assertEqual(row.get("reason"), "role_unknown:DWG-P01")
        self.assertEqual(list(row.get("role_candidates") or []), ["面纸", "灰板"],
                         "每一行都要带它可选的候选角色（Spec §3）")
        self.assertEqual(row.get("size_source"), "component_bbox")
        self.assertEqual(row.get("outline_status"), "open")
        self.assertFalse(row.get("mapped"))
        self.assertEqual(status.get("unbound_total"), 1)
        self.assertEqual(status.get("mapped_total"), 0)

    def test_a3_already_mapped_row_counts_as_mapped_not_unbound(self):
        mapped = _rows()
        mapped[0]["part_role"] = "面纸"
        status = _func("role_map_status")(mapped, part_templates=TEMPLATES)
        self.assertEqual(status.get("items") or [], [],
                         "已经映射过的行不许再出现在未映射清单里（Spec §2.1）")
        self.assertEqual(status.get("unbound_total"), 0)
        self.assertEqual(status.get("mapped_total"), 1)

    def test_a4_bind_parts_must_not_drop_role_unbound(self):
        _has("role_unbound", BOM_SRC,
             "`_bind_parts()` 必须把 `bind_rows()` 算出来的 role_unbound 带出来，"
             "不许再丢掉（Spec §1 / §4.3）")

    def test_a5_load_bom_exposes_the_two_keys(self):
        for key in ('"role_unbound"', '"role_unbound_total"'):
            _has(key, BOM_SRC,
                 "`load_bom()` 输出必须含 %s（没有时给 [] / 0，Spec §4.3）" % key)


class BWritePath(unittest.TestCase):
    def test_b1_happy_path_writes_role_and_evidence(self):
        result = _func("apply_role_mapping")(
            _rows(), item_key="WINE-P01", part_code="DWG-P01", role="面纸",
            candidates=["面纸", "灰板"], actor="PE1", note="按图层顺序人工确认",
            mapped_at="2026-09-22 08:30:00")
        self.assertTrue(result.get("changed"), "第一次映射应当 changed=true（Spec §3）")
        row = [r for r in result["items"] if r.get("item_key") == "WINE-P01"][0]
        self.assertEqual(row.get("part_role"), "面纸")
        binding = _binding(row)
        self.assertEqual(binding.get("role_value"), "面纸")
        self.assertEqual(binding.get("binding_method"), "manual_mapping",
                         "人工映射的 binding_method 只能是 BINDING_METHODS 里的 manual_mapping（Spec §2.4）")
        self.assertEqual(binding.get("bound_by"), "PE1")
        self.assertIn("按图层顺序人工确认",
                      json.dumps(binding.get("binding_evidence") or {}, ensure_ascii=False))
        record = result.get("record") or {}
        self.assertEqual(record.get("previous_role"), "unbound")
        self.assertEqual(record.get("mapped_at"), "2026-09-22 08:30:00")
        audit = result.get("audit") or {}
        self.assertEqual(audit.get("action"), AUDIT_ACTION)
        self.assertEqual(audit.get("by"), "PE1")
        self.assertFalse(audit.get("superseded"))

    def test_b2_geometry_material_status_untouched_and_input_not_mutated(self):
        before = _rows()
        snapshot = json.dumps(before, ensure_ascii=False, sort_keys=True)
        result = _func("apply_role_mapping")(
            before, item_key="WINE-P01", part_code="DWG-P01", role="灰板",
            candidates=["面纸", "灰板"], actor="PE1", mapped_at="2026-09-22 08:30:00")
        self.assertEqual(json.dumps(before, ensure_ascii=False, sort_keys=True), snapshot,
                         "纯函数不许改入参（Spec §6）")
        row = [r for r in result["items"] if r.get("item_key") == "WINE-P01"][0]
        for key in ("length_mm", "width_mm", "height_mm", "material", "material_code",
                    "status", "locked", "item_name", "quantity", "part_code"):
            self.assertEqual(row.get(key), BOX_PART_ROW.get(key),
                             "映射只改角色与留痕：%s 不许动（Spec §2.3）" % key)

    def test_b3_same_role_twice_is_idempotent(self):
        first = _func("apply_role_mapping")(
            _rows(), item_key="WINE-P01", part_code="DWG-P01", role="面纸",
            candidates=["面纸", "灰板"], actor="PE1", mapped_at="2026-09-22 08:30:00")
        second = _func("apply_role_mapping")(
            first["items"], item_key="WINE-P01", part_code="DWG-P01", role="面纸",
            candidates=["面纸", "灰板"], actor="PE2", mapped_at="2026-09-22 09:00:00")
        self.assertFalse(second.get("changed"), "同角色重复提交必须 changed=false（Spec §2.5）")
        self.assertEqual(second["items"], first["items"],
                         "幂等提交不许改任何东西，包括 mapped_at（Spec §2.5）")

    def test_b4_rebind_keeps_the_old_role(self):
        first = _func("apply_role_mapping")(
            _rows(), item_key="WINE-P01", part_code="DWG-P01", role="面纸",
            candidates=["面纸", "灰板"], actor="PE1", mapped_at="2026-09-22 08:30:00")
        second = _func("apply_role_mapping")(
            first["items"], item_key="WINE-P01", part_code="DWG-P01", role="灰板",
            candidates=["面纸", "灰板"], actor="PE1", mapped_at="2026-09-22 09:00:00")
        self.assertTrue(second.get("changed"))
        row = [r for r in second["items"] if r.get("item_key") == "WINE-P01"][0]
        binding = _binding(row)
        self.assertEqual(binding.get("role_value"), "灰板")
        self.assertEqual(binding.get("binding_evidence", {}).get("superseded_role"), "面纸",
                         "改绑必须保留旧角色（Spec §2.6）")
        self.assertTrue((second.get("audit") or {}).get("superseded"),
                        "改绑的审计里 superseded=true（Spec §2.6）")


class CRejections(unittest.TestCase):
    def _apply(self, **overrides):
        kwargs = {"item_key": "WINE-P01", "part_code": "DWG-P01", "role": "面纸",
                  "candidates": ["面纸", "灰板"], "actor": "PE1"}
        kwargs.update(overrides)
        return _func("apply_role_mapping")(_rows(), **kwargs)

    def test_c1_role_outside_candidates(self):
        _error(self, 400, "role_not_in_candidates", lambda: self._apply(role="包装盒"))

    def test_c2_empty_or_unknown_role_is_rejected(self):
        for bad in ("", "  ", "unknown", "unbound"):
            with self.subTest(role=bad):
                _error(self, 400, "role_required", lambda bad=bad: self._apply(role=bad))

    def test_c3_unknown_item_key(self):
        _error(self, 404, "item_not_found", lambda: self._apply(item_key="WINE-999"))

    def test_c4_part_code_mismatch(self):
        _error(self, 409, "part_mismatch", lambda: self._apply(part_code="DWG-P99"))


class DProductionEntryPoints(unittest.TestCase):
    def test_d1_two_routes_exist_and_read_route_takes_one_path_param(self):
        _has('"/api/projects/{pid}/requirement/packaging-bom/role-map"', MAIN_SRC,
             "读路由（未映射清单）必须存在，且只带一个路径参数 {pid}（Spec §4.1）")
        _has('"/api/projects/{project_id}/requirement/packaging-bom/role-map"', MAIN_SRC,
             "写路由（人工映射）必须存在，路径参数写 {project_id}（Spec §4.2）")

    def test_d2_write_route_requires_bom_write_roles(self):
        at = MAIN_SRC.find("/api/projects/{project_id}/requirement/packaging-bom/role-map")
        self.assertGreater(at, 0, "写路由不存在，无法检查它的权限门禁（Spec §4.2）")
        window = MAIN_SRC[at:at + 2500]
        self.assertIn("BOM_WRITE_ROLES", window,
                      "人工映射是工艺侧写操作，必须过 BOM_WRITE_ROLES 门禁（Spec §4.2）")

    def test_d3_handlers_call_the_pure_functions(self):
        for name in ("role_map_status(", "apply_role_mapping("):
            _has(name, MAIN_SRC, "接口层必须真的调用 %s（Spec §4.1 / §4.2）" % name)

    def test_d4_audit_action_name_is_written(self):
        _has(AUDIT_ACTION, BOM_SRC, "映射必须写审计 action=%s（Spec §4.2）" % AUDIT_ACTION)


class ERebuildKeepsMappings(unittest.TestCase):
    def test_e1_build_bom_replays_saved_mappings(self):
        at = BOM_SRC.find("def build_bom(")
        self.assertGreater(at, 0)
        end = BOM_SRC.find("\ndef ", at + 1)
        body = BOM_SRC[at:end if end > 0 else len(BOM_SRC)]
        self.assertTrue("apply_saved_role_map" in body or "role_map" in body,
                        "`build_bom()` 必须重放已保存的人工映射，否则映射完再算一次 BOM 就丢了（Spec §2.8）")


class FPanelVisibility(unittest.TestCase):
    def test_f1_panel_shows_unmapped_and_entry(self):
        _has("role-map", APP_SRC, "面板必须能打开人工映射入口（Spec §4.5）")
        _has("未映射", APP_SRC, "面板必须显示「角色未映射 n 行」（Spec §4.5）")


class GRedLinesHold(unittest.TestCase):
    def test_g1_auto_binding_is_still_rejected(self):
        _has("reject_unknown_role_autobind(", PARTS_SRC,
             "`role=unknown` 不许自动贴角色的红线继续生效（Spec §2.7）")

    def test_g2_constants_in_place(self):
        _has("UNBOUND_ROLE", BOM_SRC, "UNBOUND_ROLE 常量仍在（Spec §2.4）")
        _has("BINDING_METHODS", BOM_SRC, "BINDING_METHODS 闭集仍在（Spec §2.4）")
        _has("manual_mapping", BOM_SRC, "manual_mapping 必须留在闭集里（Spec §2.4）")
        self.assertTrue(getattr(packaging_bom, "UNBOUND_ROLE", None) == "unbound")
        self.assertIn("manual_mapping", tuple(getattr(packaging_bom, "BINDING_METHODS", ())))


if __name__ == "__main__":
    unittest.main()

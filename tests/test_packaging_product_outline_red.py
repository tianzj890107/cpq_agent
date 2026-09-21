"""红测（DWG 第 4b 批）：真实刀模图层名、拼版/图框排除与产品级盒型候选。

Spec：`docs/specs/packaging-product-outline-and-die-layer-roles.md`
依赖：第 4 批（`packaging_semantics`）、第 3 批（`cad_ir`）；D 组真实样本额外依赖第 2 批（`cad_converter`）。
夹具：`tests/fixtures/cad_ir/build_fixtures.py`（在内存里现搭合成 CAD IR，不落盘、不改既有夹具）。

现状缺口（实测，9-21；本地 libredwg 0.14 → ezdxf，与 34 上 ODA 27.1 逐值一致）：
  · 真实图层名 `全穿刀` / `压线 Crease` / `图框层` / `排图层` 在交付规则里一条都不命中 → 32 层全 `unknown`；
  · 成品主轮廓取「面积最大的闭合候选」→ 把**展开料/拼版框/整张图框**写成成品长宽
    （酒盒 1705.9507596530002×713.2989662779999、圆盘盒 15639.372814358998×6318.280258252999）；
  · `box_candidate_total=0`：`round_tube` 被拼版大框的面积压死、`folding_carton` 的压线图层没被识别。

四组不许松动的口径（细则见 Spec §1–§4）：
  1. 图层角色只由配置决定，真实世界命名必须命中，且负向图层不得被误判成刀线/压线；
  2. 拼版 / 图框 / 整张候选**保留但不得**参与成品轮廓、成品长宽与盒型候选的判定，并必须披露；
  3. 成品长宽只认产品级证据；拿不到就 `missing`（宁缺毋滥），绝不拿展开料凑数；
  4. `box_type` 永不 `confirmed`；没有刀线/压线/圆证据时不许凭空产生候选。

D 组默认不跑；缺 `CPQ_DWG_REAL_SAMPLES=1`、真实转换器或样本时 `skipTest` 并**点名缺什么**。
D 组只用 `tech_app/tools/dwg_sample_e2e.py`（样本只读、产物只写临时目录）。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PKG = "tech_app.backend.services.packaging_semantics"
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "cad_ir"
SHIPPED_RULES = ROOT / "tech_app" / "agent_knowledge" / "rules" / "packaging_layer_rules.json"
PRIOR_SPEC = ROOT / "docs" / "specs" / "packaging-drawing-semantics.md"
PRIOR_TEST = ROOT / "tests" / "test_packaging_semantics_red.py"
SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
SAMPLE_TOOL = ROOT / "tech_app" / "tools" / "dwg_sample_e2e.py"
WINE_BOX = "酒盒.dwg"
ROUND_BOX = "圆盘盒.dwg"

BOX_TYPES = {"telescope_lid_base", "drawer", "book_style", "folding_carton", "round_tube",
             "irregular", "unknown"}
REJECT_REASONS = {"panel_repeat", "frame_layer", "sheet_extent"}
REJECT_KEYS = {"outline_id", "reason", "bbox", "area", "evidence_refs"}
REJECT_WARNING = "PACKAGING_OUTLINE_SHEET_FRAME_REJECTED"
UNCERTAIN_WARNING = "PACKAGING_PRODUCT_OUTLINE_UNCERTAIN"
UNCERTAIN_REASON = "product_outline_uncertain"
RULES_ERROR_CODE = "PACKAGING_LAYER_RULES_INVALID"

#: 实测反例（Spec §0 / §3）：被排除的拼版外框 —— 这两个值任何情况下都不许出现在成品长宽里。
WINE_PANEL_SIZE = (1705.9507596530002, 713.2989662779999)
ROUND_SHEET_SIZE = (15639.372814358998, 6318.280258252999)

#: Spec §7 —— 冻结面：字段键闭集（不许自创键）。
FROZEN_FIELD_KEYS = {
    "inner_length", "inner_width", "inner_height", "box_type", "box_family", "closure_type",
    "v_groove", "grey_board", "grey_board_thickness", "face_paper", "face_paper_gsm",
    "insert_type", "print_colors", "lamination", "hot_stamping", "uv_coating", "emboss_deboss",
    "silk_screen", "die_cutting", "mounting", "special_process", "units_per_carton", "carton_size",
}
FROZEN_SEMANTICS_VERSION = "packaging-semantics/1"
FROZEN_STATS_KEYS = {"layer_total", "cut_layer_total", "crease_layer_total",
                     "boundary_candidate_total", "hole_total", "conflict_total",
                     "unresolved_total", "box_candidate_total"}
FROZEN_REQUIRED_KEYS = {"semantics_version", "semantics_id", "semantics_hash", "source", "layers",
                        "roles_summary", "outline", "dimensions", "texts", "box_candidates",
                        "fields", "unresolved", "model_assist", "warnings", "stats", "reviewable"}

#: Spec §1.2 —— 负向图层：不得被误判成刀线或压线。
NEGATIVE_LAYERS = ("DESIGN", "SAMPLE", "0", "Defpoints", "Make2D$可见线$普通线",
                   "1轮廓实线层", "6文字层")


def fixture_module():
    """按路径加载夹具构造器（不 import `tests` 包，避免依赖 __init__.py）。"""
    path = FIXTURE_DIR / "build_fixtures.py"
    spec = importlib.util.spec_from_file_location("cpq_cad_ir_fixtures", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def injected_rules(layers, *, colors=None, line_types=None):
    return {"rule_set": "packaging_layer_rules_v1", "review_status": "reviewed",
            "default_template": "generic",
            "templates": {"generic": {"layers": list(layers), "colors": dict(colors or {}),
                                      "line_types": dict(line_types or {})}}}


# --------------------------------------------------------------------------- #
# 合成 CAD IR（内存搭建，Spec §8）
# --------------------------------------------------------------------------- #
def real_world_layer_names(fx):
    """Spec §1：两张真实图纸里出现过的图层名（含负向图层）。"""
    names = [("全穿刀", 49), ("压线 Crease", 12), ("图框层", 1), ("排图层", 4), ("CUTTER", 308),
             ("轮廓线", 39)]
    names += [(name, 1) for name in NEGATIVE_LAYERS]
    return fx.make(
        "real_world_layer_names",
        layers=[fx.layer(name, entity_count=count) for name, count in names],
        entities=[fx.entity("10%d" % index, "LINE", name, bbox=[0, 0, 10, 0], length=10.0)
                  for index, (name, _count) in enumerate(names)],
        geometry={"closed_outlines": [], "open_outlines": [], "components": [], "holes": [],
                  "repeated_groups": [], "overlaps": [], "tolerance": 1e-6},
    )


def panel_blanks_only(fx, *, width=None, height=None):
    """两条同尺寸闭合框，落在 `repeated_groups` 里（拼版/展开料单元）。"""
    width = WINE_PANEL_SIZE[0] if width is None else width
    height = WINE_PANEL_SIZE[1] if height is None else height
    area = width * height
    return fx.make(
        "panel_blanks_only",
        layers=[fx.layer("0", entity_count=2)],
        entities=[fx.entity("B0", "LWPOLYLINE", "0", closed=True, bbox=[0, 0, width, height],
                            length=4838.5, area=area),
                  fx.entity("B1", "LWPOLYLINE", "0", closed=True,
                            bbox=[10, 100, width + 10, height + 100], length=4838.5, area=area)],
        geometry={"closed_outlines": [
                      fx.outline("out:model:B0", "ent:model:B0", "0", [0, 0, width, height],
                                 4838.5, area),
                      fx.outline("out:model:B1", "ent:model:B1", "0",
                                 [10, 100, width + 10, height + 100], 4838.5, area)],
                  "open_outlines": [], "components": [], "holes": [],
                  "repeated_groups": [{"count": 2, "signature": "LWPOLYLINE:4:closed",
                                       "entity_ids": ["ent:model:B0", "ent:model:B1"]}],
                  "overlaps": [], "tolerance": 1e-6},
    )


def sheet_frame_and_circles(fx):
    """拼版大框（＝整张 extents）+ 产品级圆（真实圆盘盒的圆直径分组）。"""
    diameters = (404.0, 404.0, 404.0, 404.0, 401.0, 401.0, 399.0, 399.0, 396.6, 396.6)
    holes = [fx.hole("ent:model:%04X" % (0xC00 + index), "circle", (500 + index, 500), value / 2.0)
             for index, value in enumerate(diameters)]
    ir = fx.make(
        "sheet_frame_and_circles",
        layers=[fx.layer("0", entity_count=2), fx.layer("全穿刀", entity_count=1),
                fx.layer("压线 Crease", entity_count=4), fx.layer("图框层", entity_count=1)],
        entities=[fx.entity("A0", "LWPOLYLINE", "0", closed=True, bbox=[0, 0, 11998, 2655],
                            length=29306.0, area=11998 * 2655.0),
                  fx.entity("A1", "LWPOLYLINE", "全穿刀", closed=True,
                            bbox=[100, 100, 12098, 2755], length=29306.0, area=11998 * 2655.0)],
        geometry={"closed_outlines": [
                      fx.outline("out:model:A0", "ent:model:A0", "0", [0, 0, 11998, 2655],
                                 29306.0, 11998 * 2655.0),
                      fx.outline("out:model:A1", "ent:model:A1", "全穿刀",
                                 [100, 100, 12098, 2755], 29306.0, 11998 * 2655.0)],
                  "open_outlines": [], "components": [], "holes": holes,
                  "repeated_groups": [{"count": 2, "signature": "LWPOLYLINE:4:closed",
                                       "entity_ids": ["ent:model:A0", "ent:model:A1"]}],
                  "overlaps": [], "tolerance": 1e-6},
        dimensions=[fx.dimension("D1", "0", declared=403.99999, measured=403.99999),
                    fx.dimension("D2", "0", declared=396.59999, measured=396.59999)],
    )
    # 整张图框：bbox 与 document.extents 同宽（Spec §2 sheet_extent 判据）。
    ir["document"]["extents"] = [0.0, 0.0, 11998.0, 2655.0]
    return ir


def cut_only_no_crease(fx):
    """只有刀线图层、没有压线、也没有闭合轮廓（酒盒的缩样）。"""
    return fx.make(
        "cut_only_no_crease",
        layers=[fx.layer("CUTTER", entity_count=4)],
        entities=[fx.entity("C0", "LINE", "CUTTER", bbox=[0, 0, 200, 0], length=200.0),
                  fx.entity("C1", "LINE", "CUTTER", bbox=[0, 0, 0, 90], length=90.0),
                  fx.entity("C2", "LINE", "CUTTER", bbox=[200, 0, 200, 90], length=90.0),
                  fx.entity("C3", "LINE", "CUTTER", bbox=[0, 90, 200, 90], length=200.0)],
        geometry={"closed_outlines": [], "open_outlines": [
                      {"outline_id": "out:model:C0", "entity_id": "ent:model:C0", "layer": "CUTTER",
                       "bbox": [0, 0, 200, 0], "length": 200.0, "area": None, "length_mm": 200.0,
                       "area_mm2": None, "evidence_ref": "ev:E:C0"}],
                  "components": [], "holes": [], "repeated_groups": [], "overlaps": [],
                  "tolerance": 1e-6},
    )


def plain_outline_unknown_layer(fx):
    """只有一条普通闭合轮廓、没有任何刀线/压线/圆证据：不许凭空产生盒型候选。"""
    return fx.make(
        "plain_outline_unknown_layer",
        layers=[fx.layer("MYSTERY", entity_count=1)],
        entities=[fx.entity("E0", "LWPOLYLINE", "MYSTERY", closed=True, bbox=[0, 0, 100, 60],
                            length=320.0, area=6000.0)],
        geometry={"closed_outlines": [fx.outline("out:model:E0", "ent:model:E0", "MYSTERY",
                                                 [0, 0, 100, 60], 320.0, 6000.0)],
                  "open_outlines": [], "components": [], "holes": [], "repeated_groups": [],
                  "overlaps": [], "tolerance": 1e-6},
    )


class ProductOutlineCase(unittest.TestCase):
    maxDiff = None

    # ---------------------------------------------------------------- 加载
    def module(self):
        try:
            return importlib.import_module(PKG)
        except ModuleNotFoundError as exc:
            self.fail("缺少 tech_app/backend/services/packaging_semantics/（第 4 批 Spec §2）：%s" % exc)

    def fixtures(self):
        module = getattr(self.__class__, "_fixture_module", None)
        if module is None:
            module = fixture_module()
            self.__class__._fixture_module = module
        return module

    def analyze(self, ir, **kwargs):
        package = self.module()
        fn = getattr(package, "analyze", None)
        self.assertTrue(callable(fn), "packaging_semantics 必须导出 analyze()（第 4 批 Spec §2）")
        return fn(ir, **kwargs)

    def analyze_shipped(self, ir):
        """用**交付的**规则文件跑（不是注入的测试规则）。"""
        return self.analyze(ir, rules=SHIPPED_RULES)

    def built(self, builder, **kwargs):
        return self.analyze_shipped(builder(self.fixtures(), **kwargs))

    # ---------------------------------------------------------------- 断言帮手
    def layer_role(self, semantics, layer_name):
        for item in semantics.get("layers") or []:
            if item.get("name") == layer_name:
                return item
        self.fail("语义结果里没有图层 %s（第 4 批 Spec §3）" % layer_name)

    def field(self, semantics, key):
        fields = semantics.get("fields") or {}
        self.assertIn(key, fields, "语义结果里必须有字段 %s" % key)
        return fields[key]

    def rejected(self, semantics):
        outline = semantics.get("outline") or {}
        self.assertIn("rejected", outline,
                      "必须新增 outline.rejected[] 披露被排除的拼版/图框/整张候选（Spec §2.2）")
        return list(outline.get("rejected") or [])

    def warnings(self, semantics):
        return {str(item.get("code") or "") for item in semantics.get("warnings") or []}

    def unresolved_rows(self, semantics):
        return {(str(item.get("field") or ""), str(item.get("reason") or ""))
                for item in semantics.get("unresolved") or []}

    def rejected_ids(self, semantics):
        return {str(item.get("outline_id") or "") for item in self.rejected(semantics)}

    def candidate_ids(self, semantics):
        return {str(item.get("outline_id") or "")
                for item in (semantics.get("outline") or {}).get("boundary_candidates") or []}

    def product_diameters(self, semantics):
        holes = (semantics.get("outline") or {}).get("holes") or []
        values = []
        for row in holes:
            if row.get("kind") != "circle":
                continue
            try:
                diameter = float(row.get("diameter"))
            except (TypeError, ValueError):
                continue
            if diameter >= 100.0:
                values.append(diameter)
        return sorted(set(round(value, 3) for value in values))

    def box_types(self, semantics):
        return {str(item.get("candidate_type") or "") for item in semantics.get("box_candidates") or []}

    def assert_not_a_rejected_frame(self, semantics, key, forbidden):
        entry = self.field(semantics, key)
        value = entry.get("value")
        if value is None:
            return
        for bad in forbidden:
            self.assertNotAlmostEqual(float(value), float(bad), places=6,
                                      msg="%s 不得等于被排除的拼版/整张外框（Spec §3）" % key)

    def assert_candidate_contract(self, semantics):
        for item in semantics.get("box_candidates") or []:
            self.assertIn(item.get("candidate_type"), BOX_TYPES,
                          "候选类型必须是闭集内的值（第 4 批 Spec §7）")
            for key in ("candidate_type", "confidence", "matched_features", "missing_features",
                        "contradictory_features", "evidence_refs"):
                self.assertIn(key, item, "候选必须带 %s（第 4 批 Spec §7）" % key)
            self.assertTrue(item.get("evidence_refs"), "候选必须带可回查的证据（Spec §4）")
            self.assertLessEqual(float(item.get("confidence") or 0.0), 0.8,
                                 "几何推断的置信度上限 0.8（第 4 批 Spec §5.1）")


# --------------------------------------------------------------------------- #
# A 组：真实世界图层名与规则能力
# --------------------------------------------------------------------------- #
class RealWorldLayerNames(ProductOutlineCase):
    def test_a1_full_cut_layer_is_a_cut_layer(self):
        entry = self.layer_role(self.built(real_world_layer_names), "全穿刀")
        self.assertEqual(entry.get("role"), "cut", "`全穿刀` 必须判成刀线（Spec §1.1）")
        self.assertEqual(entry.get("role_source"), "rule")
        self.assertTrue(entry.get("matched_rule_id"), "必须记下命中的规则 id（Spec §1.1）")

    def test_a2_crease_layer_is_a_crease_layer(self):
        entry = self.layer_role(self.built(real_world_layer_names), "压线 Crease")
        self.assertEqual(entry.get("role"), "crease", "`压线 Crease` 必须判成压线（Spec §1.1）")
        self.assertEqual(entry.get("role_source"), "rule")

    def test_a3_drawing_frame_layers_are_frame(self):
        semantics = self.built(real_world_layer_names)
        for name in ("图框层", "排图层"):
            entry = self.layer_role(semantics, name)
            self.assertEqual(entry.get("role"), "frame",
                             "`%s` 必须判成图框，否则会被当成品轮廓（Spec §1.1）" % name)

    def test_a4_cutter_layer_regression_anchor(self):
        entry = self.layer_role(self.built(real_world_layer_names), "CUTTER")
        self.assertEqual(entry.get("role"), "cut", "`CUTTER` 是已有回归锚点，不许回退（Spec §1.1）")

    def test_a5_design_and_annotation_layers_are_never_die_lines(self):
        semantics = self.built(real_world_layer_names)
        for name in NEGATIVE_LAYERS:
            role = self.layer_role(semantics, name).get("role")
            self.assertNotIn(role, ("cut", "crease"),
                             "`%s` 不得被误判成刀线/压线（Spec §1.2）：%r" % (name, role))

    def test_a6_name_contains_matching_is_supported(self):
        ir = real_world_layer_names(self.fixtures())
        ir["layers"].append(self.fixtures().layer("2-压线层", entity_count=3))
        rules = injected_rules([{"rule_id": "crease_contains_v1", "role": "crease",
                                 "evidence_level": "STRONG", "confidence": 0.9,
                                 "match": {"name_contains": ["压线"]}}])
        entry = self.layer_role(self.analyze(ir, rules=rules), "2-压线层")
        self.assertEqual(entry.get("role"), "crease",
                         "`match.name_contains` 必须真的生效，不许被静默丢弃（Spec §1.3）")
        self.assertEqual(entry.get("role_source"), "rule")

    def test_a7_unknown_match_keys_are_rejected_loudly(self):
        rules = injected_rules([{"rule_id": "cut_regex_v1", "role": "cut",
                                 "evidence_level": "STRONG", "confidence": 0.9,
                                 "match": {"regex": ["^CUT"]}}])
        try:
            self.analyze(real_world_layer_names(self.fixtures()), rules=rules)
        except Exception as exc:                                  # noqa: BLE001
            self.assertEqual(str(getattr(exc, "code", "") or ""), RULES_ERROR_CODE,
                             "`match` 闭集外的键必须报 %s：%r" % (RULES_ERROR_CODE, exc))
            return
        self.fail("`match` 里出现闭集外的键时必须报 %s，不许静默丢弃（Spec §1.3）"
                  % RULES_ERROR_CODE)


# --------------------------------------------------------------------------- #
# B 组：拼版 / 图框 / 整张 不得当成品轮廓，且必须披露
# --------------------------------------------------------------------------- #
class RejectedSheetFrames(ProductOutlineCase):
    def test_b1_repeated_panel_unit_is_not_a_product_size(self):
        semantics = self.built(panel_blanks_only)
        for key in ("inner_length", "inner_width"):
            entry = self.field(semantics, key)
            self.assertNotEqual(str(entry.get("status")), "confirmed")
            self.assertIsNone(entry.get("value"),
                              "%s 不得取重复单元（拼版/展开料）外框，宁缺毋滥（Spec §3.3）" % key)
        self.assertIn(("inner_length", UNCERTAIN_REASON), self.unresolved_rows(semantics),
                      "拿不到产品级轮廓时必须进 unresolved（Spec §3.3）")
        reasons = {item.get("reason") for item in self.rejected(semantics)}
        self.assertIn("panel_repeat", reasons, "重复单元必须披露成 panel_repeat（Spec §2.2）")

    def test_b2_sheet_extent_and_panel_frames_are_disclosed(self):
        semantics = self.built(sheet_frame_and_circles)
        reasons = {item.get("reason") for item in self.rejected(semantics)}
        self.assertIn("panel_repeat", reasons, "拼版单元必须披露（Spec §2.2）")
        self.assertTrue(reasons <= REJECT_REASONS,
                        "reason 必须是闭集内的值：%s" % sorted(reasons - REJECT_REASONS))
        self.assertIn(REJECT_WARNING, self.warnings(semantics),
                      "有候选被排除时必须给 %s 警告（Spec §2.3）" % REJECT_WARNING)
        self.assertTrue(self.rejected_ids(semantics) <= self.candidate_ids(semantics),
                        "被排除的候选必须仍留在 boundary_candidates 里（Spec §2.1）")

    def test_b3_frame_layer_never_beats_the_cut_outline(self):
        semantics = self.analyze_shipped(self.fixture("cut_crease_layers"))
        self.assertEqual(self.layer_role(semantics, "FRAME").get("role"), "frame")
        self.assertAlmostEqual(float(self.field(semantics, "inner_length").get("value") or 0.0),
                               100.0, places=6,
                               msg="FRAME 图层上的 210×310 大框不得压过 CUT 轮廓（Spec §2/§3）")
        self.assertAlmostEqual(float(self.field(semantics, "inner_width").get("value") or 0.0),
                               60.0, places=6)
        self.assertIn("frame_layer", {item.get("reason") for item in self.rejected(semantics)},
                      "图框层候选必须披露成 frame_layer（Spec §2.2）")

    def test_b4_rejected_entries_are_structural_and_ordered(self):
        semantics = self.built(sheet_frame_and_circles)
        rows = self.rejected(semantics)
        self.assertTrue(rows, "必须有被排除的候选（Spec §2.2）")
        for item in rows:
            self.assertEqual(set(item), REJECT_KEYS,
                             "rejected 元素必须正好是 %s（Spec §2.2）" % sorted(REJECT_KEYS))
            self.assertIn(item.get("reason"), REJECT_REASONS)
            self.assertIsInstance(item.get("bbox"), list)
            self.assertEqual(len(item.get("bbox") or []), 4)
            self.assertTrue(item.get("evidence_refs"), "被排除的候选也要带证据（Spec §2.2）")
        key = lambda row: (row.get("reason"), -(row.get("area") or 0.0), row.get("outline_id"))
        self.assertEqual([row.get("outline_id") for row in rows],
                         [row.get("outline_id") for row in sorted(rows, key=key)],
                         "rejected 顺序必须确定（Spec §6）")

    def test_b5_no_rejection_disclosure_when_nothing_is_rejected(self):
        semantics = self.analyze_shipped(self.fixture("unitless_dimensions"))
        self.assertEqual(self.rejected(semantics), [],
                         "没有拼版/图框/整张候选时不许凭空产生 rejected（Spec §2.2）")
        self.assertNotIn(REJECT_WARNING, self.warnings(semantics))
        self.assertIsNotNone(self.field(semantics, "inner_length").get("value"),
                             "普通单轮廓图纸必须照旧给出尺寸候选（回归锚点）")

    def fixture(self, name):
        return json.loads((FIXTURE_DIR / ("%s.json" % name)).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# C 组：盒型候选必须由产品级证据支撑
# --------------------------------------------------------------------------- #
class ProductLevelBoxCandidates(ProductOutlineCase):
    def test_c1_round_tube_survives_the_sheet_frame(self):
        semantics = self.built(sheet_frame_and_circles)
        self.assertIn("round_tube", self.box_types(semantics),
                      "拼版大框不得压掉产品级圆 → 必须出 round_tube 候选（Spec §4.1）")
        self.assert_candidate_contract(semantics)
        item = next(row for row in semantics["box_candidates"]
                    if row.get("candidate_type") == "round_tube")
        self.assertIn("circular_closed_boundary", item.get("matched_features") or [],
                      "round_tube 必须记下圆形证据（第 4 批 Spec §7）")
        self.assertLessEqual(float(item.get("confidence") or 0.0), 0.4)
        excluded = self.rejected_ids(semantics)
        refs = set(item.get("evidence_refs") or [])
        self.assertTrue(refs, "圆盘盒候选必须带圆实体证据（Spec §4.1）")
        for outline_id in excluded:
            self.assertNotIn("ev:E:%s" % outline_id.rsplit(":", 1)[-1], refs,
                             "候选证据不得指向被排除的拼版候选（Spec §4.1）")

    def test_c2_cut_lines_without_product_outline_yield_irregular(self):
        semantics = self.built(cut_only_no_crease)
        self.assertIn("irregular", self.box_types(semantics),
                      "有刀线但没有产品级闭合轮廓时必须出 irregular 候选（Spec §4.3）")
        self.assert_candidate_contract(semantics)
        item = next(row for row in semantics["box_candidates"]
                    if row.get("candidate_type") == "irregular")
        self.assertIn("cut_lines", item.get("matched_features") or [])
        self.assertTrue(item.get("missing_features"), "缺的证据必须写进 missing_features（Spec §4.3）")
        self.assertIn("crease_lines", item.get("missing_features") or [])
        self.assertLessEqual(float(item.get("confidence") or 0.0), 0.35)

    def test_c3_no_evidence_no_candidate(self):
        semantics = self.built(plain_outline_unknown_layer)
        self.assertEqual(semantics.get("box_candidates") or [], [],
                         "没有刀线/压线/圆证据时不许凭空产生候选（Spec §4.4）")
        self.assertEqual((semantics.get("stats") or {}).get("box_candidate_total"), 0)

    def test_c4_box_type_is_never_confirmed(self):
        for builder in (sheet_frame_and_circles, cut_only_no_crease):
            semantics = self.built(builder)
            if not semantics.get("box_candidates"):
                continue
            entry = self.field(semantics, "box_type")
            self.assertNotEqual(entry.get("status"), "confirmed",
                                "box_type 永不 confirmed（第 4 批 Spec §7）")
            self.assertEqual(entry.get("status"), "needs_confirmation")


# --------------------------------------------------------------------------- #
# D 组：两份真实 DWG（默认不跑）
# --------------------------------------------------------------------------- #
class RealSampleProductOutline(ProductOutlineCase):
    irs = {}
    semantics = {}

    @classmethod
    def setUpClass(cls):
        cls.workspace = None
        if os.environ.get("CPQ_DWG_REAL_SAMPLES") != "1":
            raise unittest.SkipTest("未设置 CPQ_DWG_REAL_SAMPLES=1：真实样本组默认不跑（Spec §8）")
        try:
            converter = importlib.import_module("tech_app.backend.services.cad_converter")
            capability = converter.capability()
        except Exception as exc:                                  # noqa: BLE001
            raise unittest.SkipTest("转换器适配层不可用：%s: %s" % (type(exc).__name__, exc))
        if not capability.get("available") or capability.get("simulated"):
            raise unittest.SkipTest("没有可用的真实转换器：%r" % capability.get("message"))
        missing = [name for name in (WINE_BOX, ROUND_BOX) if not (SAMPLES_DIR / name).is_file()]
        if missing:
            raise unittest.SkipTest("样本不在本机（%s）：缺少 %s" % (SAMPLES_DIR, "、".join(missing)))
        if not SAMPLE_TOOL.is_file():
            raise unittest.SkipTest("缺少 tech_app/tools/dwg_sample_e2e.py")

        cls.workspace = pathlib.Path(tempfile.mkdtemp(prefix="cpq-dwg-4b-"))
        try:
            cad_ir = importlib.import_module("tech_app.backend.services.cad_ir")
            semantics_mod = importlib.import_module(PKG)
            for name in (WINE_BOX, ROUND_BOX):
                out = cls.workspace / name.replace(".dwg", "")
                completed = subprocess.run(
                    [sys.executable, str(SAMPLE_TOOL), "--sample", str(SAMPLES_DIR / name),
                     "--out", str(out), "--json"],
                    capture_output=True, text=True, timeout=1800, cwd=str(ROOT))
                text = (completed.stdout or "").strip()
                payload = json.loads(text[text.find("{"):]) if text else {}
                dxf = pathlib.Path(str(payload.get("dxf_path") or ""))
                if not dxf.is_file():
                    raise unittest.SkipTest("%s 转换未产出 DXF（rc=%s）：%s"
                                            % (name, completed.returncode,
                                               (completed.stderr or "")[-200:]))
                ir = cad_ir.parse_dxf(dxf.read_bytes(), filename=dxf.name,
                                      source={"kind": "dwg_2d", "attachment_name": name})
                cls.irs[name] = ir
                cls.semantics[name] = semantics_mod.analyze(ir)
        except unittest.SkipTest:
            raise
        except Exception as exc:                                  # noqa: BLE001
            raise unittest.SkipTest("真实样本转换/解析失败：%s: %s" % (type(exc).__name__, exc))

    @classmethod
    def tearDownClass(cls):
        if cls.workspace is not None:
            shutil.rmtree(cls.workspace, ignore_errors=True)

    def role(self, name, layer):
        for item in (self.semantics[name].get("layers") or []):
            if item.get("name") == layer:
                return str(item.get("role") or "")
        self.fail("%s 里没有图层 %s" % (name, layer))

    def test_d1_real_layer_roles_are_recognised(self):
        for layer in ("全穿刀", "压线 Crease", "图框层", "排图层"):
            self.assertEqual(self.role(ROUND_BOX, layer),
                             "cut" if layer == "全穿刀" else ("crease" if "压线" in layer else "frame"),
                             "圆盘盒的 `%s` 角色（Spec §1.1）" % layer)
        self.assertEqual(self.role(WINE_BOX, "CUTTER"), "cut")
        for name in (WINE_BOX, ROUND_BOX):
            for layer in ("DESIGN", "Defpoints", "Make2D$可见线$普通线"):
                self.assertNotIn(self.role(name, layer), ("cut", "crease"),
                                 "%s 的 `%s` 不得被误判成刀线/压线（Spec §1.2）" % (name, layer))

    def test_d2_wine_box_yields_a_candidate_and_no_panel_size(self):
        semantics = self.semantics[WINE_BOX]
        self.assertGreaterEqual((semantics.get("stats") or {}).get("box_candidate_total") or 0, 1,
                                "酒盒必须至少有一个盒型候选（Spec §4.3）")
        self.assertTrue(self.box_types(semantics) <= {"folding_carton", "irregular"},
                        "酒盒候选类型：%s" % sorted(self.box_types(semantics)))
        self.assert_candidate_contract(semantics)
        for index, key in enumerate(("inner_length", "inner_width")):
            self.assert_not_a_rejected_frame(semantics, key, (WINE_PANEL_SIZE[index],))
            self.assertIn(str(self.field(semantics, key).get("status")),
                          ("missing", "needs_confirmation"),
                          "%s 只能是 missing 或需确认（Spec §3）" % key)
        self.assertTrue(self.rejected(semantics), "酒盒必须披露被排除的拼版外框（Spec §2.2）")

    def test_d3_round_box_yields_round_tube(self):
        semantics = self.semantics[ROUND_BOX]
        self.assertGreaterEqual((semantics.get("stats") or {}).get("box_candidate_total") or 0, 1,
                                "圆盘盒必须至少有一个盒型候选（Spec §4.1）")
        self.assertIn("round_tube", self.box_types(semantics),
                      "圆盘盒必须出 round_tube 候选（Spec §4.1）")
        self.assert_candidate_contract(semantics)

    def test_d4_round_box_size_comes_from_product_circles(self):
        semantics = self.semantics[ROUND_BOX]
        diameters = self.product_diameters(semantics)
        self.assertTrue(diameters, "圆盘盒必须有产品级圆（Spec §3.2）")
        floor = min(diameters)
        ceiling = max(diameters)
        for index, key in enumerate(("inner_length", "inner_width")):
            self.assert_not_a_rejected_frame(semantics, key, (ROUND_SHEET_SIZE[index],))
            value = self.field(semantics, key).get("value")
            self.assertIsNotNone(value, "%s 必须能由产品级圆给出候选（Spec §3.2）" % key)
            self.assertGreaterEqual(float(value), floor - 1.0,
                                    "%s 不得小于最小产品级圆直径（Spec §3.2）" % key)
            self.assertLessEqual(float(value), ceiling + 1.0,
                                 "%s 不得大于最大产品级圆直径（Spec §3.2）" % key)

    def test_d5_round_box_stats_and_boring_layers(self):
        stats = self.semantics[ROUND_BOX].get("stats") or {}
        self.assertGreaterEqual(stats.get("cut_layer_total") or 0, 1, "全穿刀 必须计入刀线层")
        self.assertGreaterEqual(stats.get("crease_layer_total") or 0, 1, "压线 必须计入压线层")

    def test_d6_semantics_hash_is_stable(self):
        module = self.module()
        for name in (WINE_BOX, ROUND_BOX):
            again = module.analyze(self.irs[name])
            self.assertEqual(again.get("semantics_hash"),
                             self.semantics[name].get("semantics_hash"),
                             "同一 IR 两次分析必须同哈希（Spec §6）")
            self.assertEqual(again.get("box_candidates"),
                             self.semantics[name].get("box_candidates"))


# --------------------------------------------------------------------------- #
# E 组：冻结面（锚点守卫）
# --------------------------------------------------------------------------- #
class FrozenSurfaceAnchors(ProductOutlineCase):
    def literal(self, path, name):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name:
                return ast.literal_eval(node.value)
        self.fail("%s 里找不到 %s 的字面定义" % (path.name, name))

    def test_e1_semantics_version_is_frozen(self):
        model = importlib.import_module("%s.model" % PKG)
        self.assertEqual(model.SEMANTICS_VERSION, FROZEN_SEMANTICS_VERSION,
                         "语义文档版本号是第 5 批的依赖锚点，不许改（Spec §5）")

    def test_e2_field_whitelist_is_frozen(self):
        model = importlib.import_module("%s.model" % PKG)
        self.assertEqual(set(model.FIELD_WHITELIST), FROZEN_FIELD_KEYS,
                         "字段键闭集不许自创（Spec §5）")
        from tech_app.backend.services import industry_templates
        keys = set()
        for block in industry_templates.PACKAGING_SPEC:
            for spec_field in block.fields:
                keys.add(spec_field.key)
        self.assertTrue(set(model.FIELD_WHITELIST) <= keys,
                        "字段键名必须取自 industry_templates.PACKAGING_SPEC（第 4 批 Spec §7）：%s"
                        % sorted(set(model.FIELD_WHITELIST) - keys))

    def test_e3_shipped_rule_set_identity_is_frozen(self):
        data = json.loads(SHIPPED_RULES.read_text(encoding="utf-8"))
        self.assertEqual(data.get("rule_set"), "packaging_layer_rules_v1")
        self.assertEqual(data.get("review_status"), "reviewed")
        template = (data.get("templates") or {}).get(data.get("default_template")) or {}
        self.assertEqual(dict(template.get("colors") or {}), {})
        self.assertEqual(dict(template.get("line_types") or {}), {})

    def test_e4_prior_red_test_contracts_are_untouched(self):
        self.assertEqual(self.literal(PRIOR_TEST, "STATS_KEYS"), FROZEN_STATS_KEYS,
                         "stats 键集是冻结契约，不许改（Spec §5）")
        self.assertEqual(self.literal(PRIOR_TEST, "REQUIRED_KEYS"), FROZEN_REQUIRED_KEYS,
                         "语义文档顶层键是冻结契约，不许改（Spec §5）")

    def test_e5_prior_spec_rules_are_still_there(self):
        text = PRIOR_SPEC.read_text(encoding="utf-8")
        for phrase in ("未确认不写值", "单位未确认 → 绝对尺寸不得确认", "冲突不静默",
                       "文件名不是证据", "`box_type` **永不** `confirmed`",
                       "键名取自 `industry_templates.PACKAGING_SPEC`，**不许**自创键"):
            self.assertIn(phrase, text, "第 4 批 Spec 的口径被改动了：%s" % phrase)


if __name__ == "__main__":
    unittest.main(verbosity=2)

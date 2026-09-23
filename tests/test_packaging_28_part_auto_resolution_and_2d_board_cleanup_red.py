"""酒盒 28 业务部件自动解析与包装 2.1 UI 收口红测。"""
from pathlib import Path
import re
import unittest

import ezdxf


ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "tech_app/frontend/app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "tech_app/frontend/index.html").read_text(encoding="utf-8")
CSS = "\n".join(path.read_text(encoding="utf-8") for path in (
    ROOT / "tech_app/frontend/workbench.css",
    ROOT / "tech_app/frontend/drawing-flow.css",
    ROOT / "tech_app/frontend/style.css",
))
FLOW = (ROOT / "tech_app/backend/services/packaging_drawing_flow/steps.py").read_text(encoding="utf-8")
FLOW_MODEL = (ROOT / "tech_app/backend/services/packaging_drawing_flow/model.py").read_text(encoding="utf-8")
MAIN = (ROOT / "tech_app/backend/main.py").read_text(encoding="utf-8")
DXF = ROOT / "tech_app/data/cad-ir-realsample/conversions/47c39dc1ab6738fc48c8/converted.dxf"


class RealWineDxfEvidence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = ezdxf.readfile(DXF)
        cls.model = cls.doc.modelspace()

    def test_dxf_contains_semantic_name_anchors_not_263_part_names(self):
        labels = []
        for entity in self.model:
            if entity.dxftype() not in ("TEXT", "MTEXT"):
                continue
            text = entity.plain_text() if entity.dxftype() == "MTEXT" else entity.dxf.text
            match = re.search(r"名称[：:]\s*([^\n]+)", text or "")
            if match:
                labels.append(match.group(1).strip())
        self.assertGreaterEqual(len(set(labels)), 10)
        self.assertLess(len(set(labels)), 28,
                        "真实 DWG 的名称锚点并不完整，不能假装 ezdxf 单独给出了完整 28 件")

    def test_real_dxf_contains_labels_needed_for_spatial_binding(self):
        all_text = "\n".join(
            (e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text)
            for e in self.model if e.dxftype() in ("TEXT", "MTEXT"))
        for label in ("左盖面纸", "右盖面纸", "内盒1灰板", "顶托EVA", "贴牌"):
            self.assertIn(label, all_text)


class AutoBusinessPartResolutionRed(unittest.TestCase):
    def test_dedicated_resolver_exists(self):
        path = ROOT / "tech_app/backend/services/packaging_business_part_resolver.py"
        self.assertTrue(path.exists(), "缺少 DWG 标注 + 权威清单的自动业务部件解析器")
        if path.exists():
            source = path.read_text(encoding="utf-8")
            for name in ("extract_text_anchors", "normalize_part_label", "build_geometry_regions",
                         "match_authority_parts", "resolve_business_parts"):
                self.assertIn("def " + name, source)

    def test_drawing_flow_resolves_business_parts_automatically(self):
        self.assertIn("business_parts_resolve", FLOW_MODEL)
        self.assertIn("resolve_business_parts", FLOW)
        self.assertIn("authority_source", FLOW)
        self.assertIn("business_part_total", FLOW)

    def test_runtime_authority_sources_are_drawing_only(self):
        """`## 453` 收窄口径：解析只吃 DWG，BOM / 已审核快照只做最后对答案。

        本用例原先是 `test_authority_lookup_precedence_is_explicit`，断言
        `attachment → knowledge_base → drawing_hash → dwg_candidate` 这条优先级链存在。
        那条链已被业务侧否掉（等于把答案当输入），因此改为断言来源闭集只剩图纸自身。
        """
        self.assertRegex(FLOW, r'AUTHORITY_PRECEDENCE\s*=\s*\(\s*"dwg"\s*,\s*"missing"\s*,?\s*\)')
        self.assertNotIn("attachment", self._precedence_line())

    def _precedence_line(self):
        match = re.search(r"AUTHORITY_PRECEDENCE\s*=\s*\([^)]*\)", FLOW)
        return match.group(0) if match else ""

    def test_wine_gold_standard_lives_on_the_test_side_only(self):
        """`## 453`：酒盒 28 件那份金标只允许落在测试侧，生产树里不许再有它。

        本用例原先是 `test_known_wine_case_has_reviewed_28_part_authority_seed`，
        断言仓库里存着按图纸 SHA 命中的已审核快照 —— 那正是"把答案当运行时输入"。
        """
        self.assertFalse(
            (ROOT / "tech_app/agent_knowledge/provenance/packaging_authority_parts.json").exists(),
            "生产树里不许再有业务部件金标快照")
        gold = ROOT / "tests/fixtures/gold/packaging_authority_parts.json"
        self.assertTrue(gold.exists(), "金标搬到 tests/fixtures/gold/")
        if gold.exists():
            text = gold.read_text(encoding="utf-8")
            self.assertIn("YT-DWG-WINE-700ML", text)
            self.assertIn("0991c8b0a9646d1fea6571d2ae6155923351c05ca2aeeb6ed544ef19df93f3e0", text)
            self.assertRegex(text, r'(?s)business_part_total.{0,120}28')

    def test_repeated_layouts_are_instances_not_business_parts(self):
        resolver = ROOT / "tech_app/backend/services/packaging_business_part_resolver.py"
        source = resolver.read_text(encoding="utf-8") if resolver.exists() else ""
        self.assertIn('"instances"', source)
        self.assertIn("global_assignment", source)
        self.assertIn("same_size_parts_are_not_merged", source)


class PackagingPartsListRed(unittest.TestCase):
    def test_no_business_catalog_never_falls_back_to_geometry_rows(self):
        start = APP.index("function renderTree")
        end = APP.index("function renderBboxes", start) if "function renderBboxes" in APP[start:] else start + 18000
        flow = APP[start:end]
        self.assertNotIn("packagingPartsShown", flow)
        self.assertNotIn("packagingPartsItems", flow)
        self.assertNotIn("packagingPartRow", flow)

    def test_bulk_process_uses_business_parts_not_geometry_parts(self):
        start = APP.index("async function startAllPartProcesses") if "async function startAllPartProcesses" in APP else 0
        batch = APP[start:start + 10000]
        self.assertIn("packagingBusinessPartRows", batch)
        self.assertNotRegex(batch, r"currentPackagingParts\.(parts|items)")


class PackagingTwoDimensionalUiRed(unittest.TestCase):
    def test_all_extrude_3d_is_removed_from_packaging_page(self):
        self.assertNotIn("全部挤出 3D", APP)
        self.assertNotIn('id = "packagingPartsSolidBatch"', APP)

    def test_packaging_part_row_has_no_cost_or_3d_actions(self):
        # 包装业务部件清单/详情中不得再造成本或 3D 动作；成本属于阶段 4。
        self.assertNotIn('data-qqBusinessDownstreamMode="cost"', APP)
        self.assertNotIn("成本测算（按权威尺寸）", APP)
        self.assertNotIn('"part-cost": { label: "成本测算"', APP)
        actions = APP[APP.index("function packagingPartActionsHtml"):
                      APP.index("const PACKAGING_SOLID_COPY")]
        self.assertNotIn("packagingPartCost", actions)
        self.assertNotIn("3D 预览", actions)

    def test_row_actions_use_compact_unified_style(self):
        self.assertIn(".part-row-action", CSS)
        rule = CSS[CSS.index(".part-row-action"):CSS.index("}", CSS.index(".part-row-action")) + 1]
        self.assertRegex(rule, r"padding\s*:\s*(4px 8px|[0-6]px [0-9]+px)")
        self.assertRegex(rule, r"font-size\s*:\s*(11|12)px")
        # 零件行的动作不能套页面级大按钮。
        self.assertNotRegex(APP, r'class(Name)?\s*=\s*[`"\'][^`"\']*part-(subaction|material-fix|thickness-fix)[^`"\']*\bbtn\b')

    def test_cad_plan_uses_full_cad_scene_not_filtered_component_boxes(self):
        self.assertIn("packagingCadSceneEndpoint", APP)
        self.assertIn("cad_entity_id", APP)
        self.assertIn("layer_visibility", APP)
        self.assertNotRegex(APP, r"components\.map\(packagingCadPlanComponentSvg\)")
        self.assertRegex(MAIN, r"packaging-geometry[\s\S]{0,2500}(cad_ir|entities)")

    def test_packaging_mode_does_not_initialize_webgl(self):
        init = APP[APP.index("function initViewer"):APP.index("function", APP.index("function initViewer") + 20)]
        self.assertIn("packaging", init)
        self.assertRegex(init, r"packaging[\s\S]{0,250}(return|hidden)")
        self.assertIn("function loadSTL", APP, "非包装项目的 3D 能力必须保留")

    def test_packaging_header_and_accessibility_say_cad_plan(self):
        self.assertIn("CAD 平面图", INDEX + APP)
        self.assertRegex(INDEX + APP, r"aria-label=[\"'](CAD 平面图|包装展开图)[\"']")


if __name__ == "__main__":
    unittest.main()

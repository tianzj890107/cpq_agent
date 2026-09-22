"""Red tests for docs/specs/e2e-packaging-dwg-quote-tech-continuity.md."""
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "tech_app/backend/main.py").read_text(encoding="utf-8")
APP = (ROOT / "tech_app/frontend/app.js").read_text(encoding="utf-8")
REQ = (ROOT / "tech_app/backend/services/requirement_service.py").read_text(encoding="utf-8")
MATCH = (ROOT / "tech_app/backend/services/packaging_match.py").read_text(encoding="utf-8")
BOM = (ROOT / "tech_app/backend/services/packaging_bom.py").read_text(encoding="utf-8")


class PackagingRequirementEvidenceRed(unittest.TestCase):
    def test_quote_text_is_an_extraction_evidence_source(self):
        self.assertTrue("quote_requirement_text" in REQ,
                      "需求抽取必须合并报价原文，不能在无附件时写成待确认")

    def test_quote_origin_is_explicit_not_internal_test(self):
        self.assertTrue("assert_quote_origin_link" in REQ,
                      "报价创建技术项目后必须校验 entry_origin 与业务实例关系")

    def test_packaging_match_isolated_from_other_industries(self):
        self.assertTrue("assert_industry_scoped_candidates" in MAIN,
                      "包装项目必须拒绝跨行业候选，而不只是靠 prompt 提醒")


class DwgLifecycleRed(unittest.TestCase):
    def test_dwg_has_one_dispatch_entry(self):
        self.assertTrue("dispatch_project_drawing_parse" in MAIN,
                      "上传/按钮应由统一分发器直接选择 drawing-flow")

    def test_authoritative_drawing_creates_requirement_revision(self):
        self.assertTrue("create_revision_for_authoritative_drawing" in REQ,
                      "审批后新增权威图纸必须创建修订版并使旧审批失效")

    def test_parts_panel_refreshes_after_drawing_flow_terminal_event(self):
        self.assertTrue("refreshPackagingPartsAfterDrawingFlow" in APP,
                      "drawing-flow 完成后应刷新零件列表，而不是只留标题")

    def test_parts_panel_renders_rows_inside_board(self):
        for token in ("packaging-parts", "part_code", "openPackagingPartInBoard"):
            self.assertTrue(token in APP, f"包装零件看板缺少 {token}")


class PackagingSemanticSafetyRed(unittest.TestCase):
    def test_closure_synonym_normalization_exists(self):
        self.assertTrue("normalize_closure_type" in MATCH,
                      "磁吸/双开门磁吸需要受控同义词匹配")

    def test_unknown_role_cannot_be_bound_by_position(self):
        self.assertTrue("reject_unknown_role_autobind" in BOM,
                      "role=unknown 的 DWG 零件不得按行号静默绑定 BOM")

    def test_bom_binding_keeps_evidence(self):
        for token in ("binding_evidence", "binding_method", "bound_by"):
            self.assertTrue(token in BOM, f"BOM 绑定缺少审计字段 {token}")


if __name__ == "__main__":
    unittest.main()

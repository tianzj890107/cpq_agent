"""红测：全件 CAD 归属候选须经证据门禁，模型只能复核，不能造事实。

Spec: docs/specs/packaging-all-parts-visual-adjudication.md
测试模型使用本地桩，不访问网络、不读 BOM；两份输入都是真 DWG 转出的 DXF。
"""
from __future__ import annotations

from pathlib import Path
from io import BytesIO
import json
import subprocess
from unittest.mock import patch
import unittest

from tech_app.backend.services import cad_ir, packaging_parts
from tech_app.backend.services import packaging_business_part_resolver as resolver


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = {
    "酒盒": ROOT / "tech_app/data/cad-ir-realsample/conversions/47c39dc1ab6738fc48c8/converted.dxf",
    "圆盘盒": ROOT / "tech_app/data/cad-ir-realsample/conversions/a6140fbc4e9b8d2e9bee/converted.dxf",
}


class PackagingAllPartsVisualAdjudicationRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.samples = {}
        for label, path in SAMPLES.items():
            if not path.is_file():
                raise unittest.SkipTest("缺少真实 DXF：%s" % path.name)
            ir = cad_ir.parse_dxf(path.read_bytes(), filename=path.name)
            cls.samples[label] = (ir, packaging_parts.extract(ir))

    def _run(self, label, reviewer):
        ir, geometry = self.samples[label]
        return resolver.resolve_business_parts(
            "visual-adjudication-red", ir, geometry,
            use_model=True, model_reviewer=reviewer,
        )

    def test_model_abstention_retracts_wrong_candidate_without_looking_up_gold(self):
        requests = []

        def reviewer(request):
            requests.append(request)
            return {"selected_candidate_id": "none", "insufficient_evidence": True,
                    "reason": "图像和 CAD 关联不足"}

        outcome = self._run("酒盒", reviewer)
        self.assertTrue(requests, "有风险的零件必须实际进入模型复核")
        self.assertTrue(any(request.get("name") == "贴牌" for request in requests),
                        "不能只复核面纸；贴牌的远距离误绑定必须被发现")
        for request in requests:
            body = repr(request)
            self.assertNotIn("酒盒 报价资料.xlsx", body)
            self.assertNotIn("JWXR21-P", body)
        binding = next(row for row in outcome["match"]["bindings"]
                       if row.get("business_part_code") == "DWG-BP20")
        self.assertIn(binding["status"], ("ambiguous", "unbound"))
        self.assertNotEqual("region:DWG-P74", binding.get("region_id"),
                            "模型拒答后不能继续把旧的最近轮廓当成已绑定")
        self.assertTrue(binding.get("candidates"), "原候选要保留供人核对")

    def test_invalid_model_id_and_fabricated_dimensions_cannot_enter_cad_result(self):
        def reviewer(_request):
            return {"selected_candidate_id": "region:invented", "insufficient_evidence": False,
                    "reason": "模型自信", "length_mm": 226.0, "width_mm": 110.3,
                    "size_confirmed": True}

        outcome = self._run("酒盒", reviewer)
        binding = next(row for row in outcome["match"]["bindings"]
                       if row.get("business_part_code") == "DWG-BP20")
        self.assertNotEqual("region:invented", binding.get("region_id"))
        self.assertIn(binding["status"], ("ambiguous", "unbound"))
        self.assertFalse(binding.get("size_confirmed"))
        self.assertNotEqual((226.0, 110.3),
                            (binding.get("length_mm"), binding.get("width_mm")))

    def test_model_failure_degrades_per_part_not_entire_drawing(self):
        def reviewer(_request):
            raise TimeoutError("模拟模型超时")

        for label in SAMPLES:
            with self.subTest(label=label):
                outcome = self._run(label, reviewer)
                self.assertEqual("dwg", outcome["detail"]["authority_source"])
                self.assertTrue(outcome["match"]["bindings"])
                for binding in outcome["match"]["bindings"]:
                    self.assertIn("attribution", binding,
                                  "每件都要说明为何绑定或为何待确认，不只处理样本件")
                self.assertTrue(any(row.get("attribution", {}).get("status")
                                    in ("review_needed", "model_unavailable")
                                    for row in outcome["match"]["bindings"]))

    def test_disabled_model_keeps_pure_offline_path(self):
        ir, geometry = self.samples["酒盒"]

        def reviewer(_request):
            self.fail("use_model=False 不许触发模型或其测试桩")

        outcome = resolver.resolve_business_parts(
            "visual-adjudication-red", ir, geometry,
            use_model=False, model_reviewer=reviewer,
        )
        self.assertEqual("dwg", outcome["detail"]["authority_source"])

    def test_all_document_rows_explain_attribution_and_preview_is_raster(self):
        from PIL import Image

        previews = []

        def reviewer(request):
            preview = request.get("preview") or {}
            self.assertEqual("image/png", preview.get("media_type"))
            with Image.open(BytesIO(preview["bytes"])) as image:
                self.assertEqual((1056, 1056), image.size)
                self.assertGreater(len(image.getcolors(maxcolors=1_000_000) or []), 2,
                                   "预览不能是白板")
            previews.append(request["name"])
            return {"selected_candidate_id": "none", "insufficient_evidence": True}

        for label in SAMPLES:
            with self.subTest(label=label):
                outcome = self._run(label, reviewer)
                ir, geometry = self.samples[label]
                document = packaging_parts.business_parts_document(
                    outcome["reference"], geometry, bindings=outcome["match"])
                self.assertEqual(len(outcome["authority_rows"]), len(document["business_parts"]))
                self.assertTrue(all((row.get("geometry_binding") or {}).get("attribution")
                                    for row in document["business_parts"]),
                                "新增业务件、推断件也必须逐件披露归属状态")
        self.assertIn("贴牌", previews)

    def test_review_disabled_by_operator_fails_closed_without_model_calls(self):
        ir, geometry = self.samples["酒盒"]
        with patch.dict("os.environ", {"PACKAGING_PART_MODEL_REVIEW": "off"}):
            outcome = resolver.resolve_business_parts(
                "visual-adjudication-red", ir, geometry, use_model=True)
        self.assertEqual(0, outcome["match"]["model_review"]["calls"])
        binding = next(row for row in outcome["match"]["bindings"]
                       if row.get("business_part_code") == "DWG-BP20")
        self.assertIn(binding["status"], ("ambiguous", "unbound"))
        self.assertEqual("review_needed", binding["attribution"]["status"])

    def test_manual_confirmation_can_bind_multiple_real_components(self):
        ir, geometry = self.samples["酒盒"]
        outcome = resolver.resolve_business_parts(
            "visual-adjudication-red", ir, geometry, use_model=True,
            model_reviewer=lambda _request: {"selected_candidate_id": "none",
                                              "insufficient_evidence": True})
        document = packaging_parts.business_parts_document(
            outcome["reference"], geometry, bindings=outcome["match"])
        target = next(row for row in document["business_parts"] if row["name"] == "贴牌")
        before = target["reference"].get("size_quality")
        component_ids = [row["component_id"] for row in geometry["parts"][:2]]
        updated = packaging_parts.set_geometry_binding(
            document, target["business_part_code"], component_ids,
            bound_by="manual", reason="已核对 CAD 候选")
        after = next(row for row in updated["business_parts"]
                     if row["business_part_code"] == target["business_part_code"])
        self.assertEqual("bound", after["geometry_binding"]["status"])
        self.assertEqual("human_confirmed", after["geometry_binding"]["attribution"]["status"])
        self.assertEqual(before, after["reference"].get("size_quality"),
                         "确认归属不等于确认尺寸")

    def test_provider_decision_alias_is_safe_abstention(self):
        from tech_app.backend.services.packaging_part_visual_review import _Decision

        decision = _Decision.model_validate({"decision": "none", "reason": "缺少引线证据"})
        self.assertEqual("none", decision.selected_candidate_id)
        self.assertTrue(decision.insufficient_evidence)
        self.assertEqual("缺少引线证据", decision.reason)
        candidate = _Decision.model_validate({"decision": "region:DWG-P74"})
        self.assertTrue(candidate.insufficient_evidence,
                        "模型不提供足够证据字段时，不能将候选升级成真实归属")

    def test_frontend_marks_candidate_as_unconfirmed_and_exposes_manual_review(self):
        source = (ROOT / "tech_app/frontend/app.js").read_text(encoding="utf-8")
        self.assertIn('data-qq-candidate-warning="1"', source)
        self.assertIn('id="packagingPartCandidateSelect"', source)
        self.assertNotIn('id="packagingConfirmCandidate"', source)
        self.assertIn('packagingGeometryBindingUrl(wanted)', source)
        self.assertIn('尺寸仍须单独确认', source)
        start = source.index("function packagingBusinessPartAttributionText(")
        end = source.index("\nfunction ", start + 1)
        function = source[start:end]
        script = function + "\nprocess.stdout.write(JSON.stringify([" + ",".join([
            'packagingBusinessPartAttributionText({attribution:{status:"review_needed"}})',
            'packagingBusinessPartAttributionText({attribution:{status:"model_suggested"}})',
            'packagingBusinessPartAttributionText({attribution:{status:"human_confirmed"}})',
        ]) + "]));"
        proc = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                              check=True)
        labels = json.loads(proc.stdout)
        self.assertIn("待人工确认", labels[0])
        self.assertIn("尚未经", labels[1])
        self.assertIn("尺寸仍须", labels[2])


if __name__ == "__main__":
    unittest.main()

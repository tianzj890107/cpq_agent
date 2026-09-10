from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"


class TechAssemblyActionButtonStatesRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assembly_js = (FRONTEND / "assembly-integration.js").read_text(encoding="utf-8")
        cls.assembly_css = (FRONTEND / "assembly-integration.css").read_text(encoding="utf-8")
        cls.workbench_js = (FRONTEND / "tech-workbench.js").read_text(encoding="utf-8")
        cls.workbench_css = (FRONTEND / "tech-workbench.css").read_text(encoding="utf-8")

    def test_upload_and_generate_do_not_request_filled_primary_classes(self):
        upload = re.search(r'<button[^>]*id="aiUploadBtn"[^>]*>', self.assembly_js)
        generate = re.search(r'<button[^>]*id="aiGenerate"[^>]*>', self.assembly_js)
        self.assertIsNotNone(upload)
        self.assertIsNotNone(generate)
        for markup in (upload.group(0), generate.group(0)):
            self.assertNotRegex(markup, r'class="[^"]*\bprimary\b')
            self.assertNotRegex(markup, r'class="[^"]*\bstart-parse-btn\b')

    def test_upload_and_generate_have_outline_normal_and_deep_hover_style(self):
        self.assertRegex(
            self.assembly_css,
            r'#aiUploadBtn\s*,\s*#aiGenerate\s*\{[^}]*background:[^;}]*(?:#fff|#FFFFFF|gradient-primary-soft)[^}]*color:[^;}]*(?:0067D1|oc-accent|color-primary)[^}]*border:[^;}]*1px',
        )
        self.assertRegex(
            self.assembly_css,
            r'#aiUploadBtn:hover:not\(:disabled\)\s*,\s*#aiGenerate:hover:not\(:disabled\)\s*\{[^}]*background:[^;}]*(?:gradient-primary-hover|linear-gradient)[^}]*color:\s*#fff',
        )

    def test_analysis_complete_uses_both_generated_results_not_finance_enabled(self):
        self.assertRegex(
            self.assembly_js,
            r'has_params\s*&&\s*(?:state\.)?has_process|has_process\s*&&\s*(?:state\.)?has_params',
        )
        self.assertRegex(self.assembly_js, r'(?:dataset|classList)[\s\S]{0,180}(?:analy|analysis|generated)')
        self.assertNotRegex(self.workbench_js, r'(?:analysis|analyzed)\s*=\s*!?\s*(?:el|primary|finance)[^.\n]*\.disabled')

    def test_process_action_bar_switches_explicit_visual_roles(self):
        sync = re.search(r'function syncActionBar\([^)]*\)\s*\{([\s\S]*?)\n\s*\}\n\n\s*/\*', self.workbench_js)
        self.assertIsNotNone(sync)
        body = sync.group(1)
        self.assertIn("state.stage", body)
        self.assertIn("process", body)
        self.assertRegex(body, r'(?:dataset|classList)[\s\S]{0,500}(?:is-filled|filled|is-outline|outline)')

    def test_workbench_defines_filled_and_outline_roles_with_hover_guards(self):
        self.assertRegex(self.workbench_css, r'\.tech-wb-btn\.(?:is-filled|filled)\s*\{[^}]*gradient-primary[^}]*color:\s*#fff')
        self.assertRegex(self.workbench_css, r'\.tech-wb-btn\.(?:is-outline|outline)\s*\{[^}]*gradient-primary-soft[^}]*color:[^;}]*twb-primary[^}]*border')
        self.assertRegex(self.workbench_css, r'\.tech-wb-btn\.(?:is-outline|outline):hover:not\(:disabled\)[^{]*\{[^}]*gradient-primary-hover[^}]*color:\s*#fff')

    def test_existing_process_targets_and_finance_gate_remain(self):
        self.assertRegex(self.workbench_js, r"'process'\s*:\s*\{[^}]*primary:\s*'#aiToFinance'[^}]*secondary:\s*'#aiStart'")
        self.assertIn("state.params_confirmed && state.process_confirmed", self.assembly_js)


if __name__ == "__main__":
    unittest.main()

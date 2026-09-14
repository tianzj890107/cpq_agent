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
        # 契约更新（「业务按钮统一到左侧会话操作栏」批次）：底栏代理 syncActionBar() 已退役，
        # 2.2 的视觉角色改由看板快照的 role 决定 —— 父壳把 role === 'primary' 的那一个放进
        # 唯一主按钮槽位，其余按 order 描边渲染，父壳不再认识 process 阶段的动作名。
        sync = re.search(r'function syncChatActions\([^)]*\)\s*\{([\s\S]*?)\n  \}', self.workbench_js)
        self.assertIsNotNone(sync, "缺少 syncChatActions()")
        body = sync.group(1)
        self.assertRegex(body, r"primaryActionName\(", "主按钮必须由看板 role 决定")
        self.assertRegex(body, r"variant:\s*'primary'", "唯一主按钮仍须显式声明 primary 外观")
        self.assertIn("syncChatActionList(", body, "其余动作仍须按角色描边渲染")

    def test_workbench_defines_filled_and_outline_roles_with_hover_guards(self):
        self.assertRegex(self.workbench_css, r'\.tech-wb-btn\.(?:is-filled|filled)\s*\{[^}]*gradient-primary[^}]*color:\s*#fff')
        self.assertRegex(self.workbench_css, r'\.tech-wb-btn\.(?:is-outline|outline)\s*\{[^}]*gradient-primary-soft[^}]*color:[^;}]*twb-primary[^}]*border')
        self.assertRegex(self.workbench_css, r'\.tech-wb-btn\.(?:is-outline|outline):hover:not\(:disabled\)[^{]*\{[^}]*gradient-primary-hover[^}]*color:\s*#fff')

    def test_existing_process_targets_and_finance_gate_remain(self):
        # 契约更新（「业务按钮统一到左侧 + 按钮统一到左侧会话操作栏」批次）：父壳不再持有
        # process 阶段的动作表，同一条业务链路改由组装页自己注册 ——
        # sendIntegrationToFinance（确认工艺并发送财务）与 runIntegration /
        # generateIntegrationProcess（整合图纸 / 生成组装工艺）。
        # 门槛没有降低：财务闸门仍由组装页自己判定。
        self.assertIn("sendIntegrationToFinance:", self.assembly_js)
        self.assertIn("aiFinanceBlocker()", self.assembly_js)
        self.assertNotIn("sendIntegrationToFinance", self.workbench_js,
                         "父壳不得再写死 2.2 的业务动作名")
        self.assertIn("state.params_confirmed && state.process_confirmed", self.assembly_js)


if __name__ == "__main__":
    unittest.main()

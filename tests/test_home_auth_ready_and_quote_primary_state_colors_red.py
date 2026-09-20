"""Red contracts for auth-ready tech cards and quote primary-color states."""

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
HOME = (ROOT / "报价首页.html").read_text(encoding="utf-8")
QUOTE = (ROOT / "确认需求解析结果.html").read_text(encoding="utf-8")


def function_body(source: str, name: str, next_marker: str) -> str:
    match = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{(.*?)(?={next_marker})",
        source,
        re.S,
    )
    if not match:
        raise AssertionError(f"missing function: {name}")
    return match.group(1)


class HomeAuthReadyAndQuotePrimaryStateColorsRedTest(unittest.TestCase):
    def test_tech_cards_wait_for_auth_and_use_guarded_global_api(self):
        load_cards = function_body(HOME, "loadCards", r"\n\s*// 「待办任务」")
        tech_branch = re.search(r"if\s*\(mode\s*===\s*['\"]tech['\"]\)\s*\{(.*?)\}\s*else", load_cards, re.S)
        self.assertIsNotNone(tech_branch)
        code = tech_branch.group(1)
        self.assertRegex(code, r"await\s+[A-Za-z_$][\w$]*Auth[A-Za-z_$\w]*\s*\(")
        self.assertIn("window.cpqAuth.api('/api/projects')", code)
        self.assertNotRegex(code, r"(?<!window\.)\bcpqAuth\.api\(")

    def test_auth_waiter_has_ready_event_and_missing_module_error(self):
        self.assertRegex(HOME, r"cpq-auth-ready")
        self.assertRegex(HOME, r"window\.cpqAuth")
        self.assertRegex(HOME, r"登录模块[^'\"\n]*(?:未加载|加载失败|不可用)")

    def test_auth_ready_or_change_retries_tech_project_load(self):
        listeners = "\n".join(re.findall(r"document\.addEventListener\('cpq-auth-(?:ready|change)'[^;]+;", HOME))
        self.assertRegex(listeners, r"loadCards\(currentMode\)|loadCards\(['\"]tech['\"]\)")

    def test_agent_filled_guess_style_uses_primary_palette_only(self):
        guessed = re.search(
            r"\.info-table input\.guessed.*?\{([^}]*)\}",
            QUOTE,
            re.S,
        )
        self.assertIsNotNone(guessed)
        css = guessed.group(1)
        self.assertIn("var(--color-primary-light)", css)
        self.assertIn("var(--color-primary)", css)
        self.assertIn("var(--color-primary-active)", css)
        self.assertNotRegex(css, r"color-warning|FEF3C7|92400e|f59e0b")

    def test_role_mismatch_does_not_add_duplicate_chat_gate_bubble(self):
        gate = function_body(QUOTE, "wfGate", r"\n\s*function\s+removeGateBubble")
        self.assertNotIn("showGateBubble('handoff')", gate)
        self.assertRegex(gate, r"!WF\.canEdit|WF\.canEdit")
        self.assertIn("wfRenderBar()", gate)

    def test_board_readonly_permission_notice_uses_primary_palette(self):
        # 第 33 批：权限提示从结果卡顶部的普通流横条改为固定标题行里的状态胶囊，
        # 因此配色由 .wf-note-readonly 这一条 CSS 类承载（JS 分支只选类名，不再内联色值）。
        render = function_body(QUOTE, "wfRenderBar", r"\n\s*// 完成当前步骤")
        readonly = re.search(r"if\s*\(WF\.canEdit\).*?\}\s*else\s*\{(.*?)\n\s*\}", render, re.S)
        self.assertIsNotNone(readonly)
        code = readonly.group(1)
        self.assertIn("wf-note-readonly", code, "只读提示必须用主色胶囊类渲染")
        rule = re.search(r"\.wf-note-readonly\s*\{([^}]*)\}", QUOTE, re.S)
        self.assertIsNotNone(rule, "缺少 .wf-note-readonly 规则")
        css = rule.group(1)
        self.assertRegex(css, r"color-primary-light|rgba\(0\s*,\s*96\s*,\s*230")
        self.assertRegex(css, r"color-primary-active|#00419F")
        self.assertRegex(css, r"border(?:Color)?|border:")
        self.assertNotRegex(css, r"234\s*,\s*179\s*,\s*8|#a16207|color-warning|f59e0b")
        self.assertNotRegex(code, r"234\s*,\s*179\s*,\s*8|#a16207|color-warning|f59e0b")

    def test_role_protection_and_transfer_action_are_preserved(self):
        self.assertRegex(QUOTE, r"GATE_BLOCKED")
        self.assertRegex(QUOTE, r"WF\.canEdit")
        self.assertIn("转交任务", QUOTE)
        self.assertRegex(QUOTE, r"/wf/(?:step-done|card|task)")


if __name__ == "__main__":
    unittest.main()

import pathlib
import re
import unittest
from html.parser import HTMLParser


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TreeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "parent": self.stack[-1] if self.stack else None}
        self.nodes.append(node)
        if tag not in {"meta", "link", "img", "input", "br", "hr"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def by_id(self, node_id):
        return next((node for node in self.nodes if node["attrs"].get("id") == node_id), None)

    @staticmethod
    def has_ancestor(node, *, node_id=None, class_name=None):
        current = node.get("parent") if node else None
        while current:
            classes = current["attrs"].get("class", "").split()
            if node_id and current["attrs"].get("id") == node_id:
                return True
            if class_name and class_name in classes:
                return True
            current = current.get("parent")
        return False


class QuoteTechAgentShellParityContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.quote = (ROOT / "确认需求解析结果.html").read_text(encoding="utf-8")
        cls.tech_html = (ROOT / "tech_app/frontend/tech-workbench.html").read_text(encoding="utf-8")
        cls.tech_css = (ROOT / "tech_app/frontend/tech-workbench.css").read_text(encoding="utf-8")
        cls.tech_js = (ROOT / "tech_app/frontend/tech-workbench.js").read_text(encoding="utf-8")
        cls.chat_js = (ROOT / "tech_app/frontend/agent-chat.js").read_text(encoding="utf-8")
        cls.tree = TreeParser()
        cls.tree.feed(cls.tech_html)

    def test_quote_logo_links_to_shared_quote_home(self):
        self.assertRegex(
            self.quote,
            r'<a\b[^>]*class="[^"]*nav-logo[^"]*"[^>]*href="/报价首页\.html\?assistant=quote"[^>]*>',
        )

    def test_tech_has_quote_style_navigation_with_live_entry_points(self):
        for token in (
            'class="tech-nav-panel"',
            'href="/报价首页.html?assistant=tech"',
            'id="techNewChat"',
            'id="techHistory"',
            'id="techMsgBtn"',
            'id="techSettings"',
            'id="techAuthBtn"',
        ):
            self.assertIn(token, self.tech_html)

    def test_tech_chat_header_exposes_title_badge_and_real_connection_state(self):
        for token in ('技术工艺智能体', 'class="tech-ai-badge"', 'id="techConnDot"', 'id="techConnText"'):
            self.assertIn(token, self.tech_html)
        self.assertIn('techConnText', self.chat_js)
        self.assertIn('techConnDot', self.chat_js)
        self.assertIn('techShellConn("已连接", true)', self.chat_js)
        self.assertIn('techShellConn("未连接", false)', self.chat_js)

    def test_right_header_exposes_project_flow_and_runtime_model(self):
        for token in ('当前项目', 'id="techProjectLabel"', '技术工艺流程', 'id="techModelInfo"'):
            self.assertIn(token, self.tech_html)
        combined_js = self.tech_js + "\n" + self.chat_js
        self.assertIn('techModelInfo', combined_js)
        self.assertRegex(combined_js, r'(?:data\.model|agent_model|text_model)')
        self.assertNotIn('id="techModelInfo">· qwen3.5-plus', self.tech_html, "模型不得写死")

    def test_desktop_shell_is_three_flush_columns(self):
        compact = re.sub(r"\s+", "", self.tech_css)
        self.assertIn("grid-template-columns:56px460pxminmax(0,1fr)", compact)
        self.assertRegex(compact, r"\.tech-workbench-body\{[^}]*gap:0(?:px)?;[^}]*padding:0(?:px)?;")
        pane = re.search(r"\.tech-workspace-pane\s*\{([^}]*)\}", self.tech_css)
        self.assertIsNotNone(pane)
        pane_body = re.sub(r"\s+", "", pane.group(1)).lower()
        self.assertIn("border-radius:0", pane_body)
        self.assertIn("box-shadow:none", pane_body)

    def test_navigation_chat_and_workspace_follow_three_column_order(self):
        nav_pos = self.tech_html.find('class="tech-nav-panel"')
        chat_pos = self.tech_html.find('id="techChatPane"')
        workspace_pos = self.tech_html.find('class="tech-workspace-pane"')
        self.assertGreaterEqual(nav_pos, 0, "缺少技术工艺左侧导航栏")
        self.assertGreaterEqual(chat_pos, 0, "缺少技术工艺会话列")
        self.assertGreaterEqual(workspace_pos, 0, "缺少技术工艺工作区")
        self.assertLess(nav_pos, chat_pos)
        self.assertLess(chat_pos, workspace_pos)

    def test_navigation_footer_is_owned_by_right_workspace(self):
        footer = next((node for node in self.tree.nodes if "tech-workbench-bottom" in node["attrs"].get("class", "").split()), None)
        self.assertIsNotNone(footer)
        self.assertTrue(TreeParser.has_ancestor(footer, class_name="tech-workspace-pane"))
        # techPanelToggle（“子页面板”）已被 docs/specs/tech-full-width-board-and-single-agent-pane.md
        # 要求删除：统一工作台只保留父壳 #techChatPane 一个会话宿主，嵌入页不再提供展开入口。
        for node_id in ("techPrev", "techNext", "techSecondary", "techPrimary"):
            self.assertTrue(TreeParser.has_ancestor(self.tree.by_id(node_id), class_name="tech-workbench-bottom"))

    def test_footer_groups_put_only_next_on_the_right(self):
        left = self.tree.by_id("techActionLeft")
        right = self.tree.by_id("techActionRight")
        self.assertIsNotNone(left)
        self.assertIsNotNone(right)
        for node_id in ("techPrev", "techNowLabel", "techSecondary", "techPrimary"):
            self.assertTrue(TreeParser.has_ancestor(self.tree.by_id(node_id), node_id="techActionLeft"))
        self.assertTrue(TreeParser.has_ancestor(self.tree.by_id("techNext"), node_id="techActionRight"))
        right_buttons = [
            node for node in self.tree.nodes
            if node["tag"] == "button" and TreeParser.has_ancestor(node, node_id="techActionRight")
        ]
        self.assertEqual([node["attrs"].get("id") for node in right_buttons], ["techNext"])


if __name__ == "__main__":
    unittest.main()

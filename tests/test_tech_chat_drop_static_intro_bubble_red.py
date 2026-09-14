"""红测：删掉技术工艺左侧会话里常驻的「技术工艺评估助手」开场气泡。

现状缺口（实测）：
  · `tech-workbench.html` 在唯一会话宿主 `#techChatPane` 的 `#ocTinner` 顶部静态写了一张
    `oc-amsg > oc-abody > oc-intent-card`（「技术工艺评估助手」+「左侧会话在整个评估流程中
    持续存在：…」）。它不是消息：`agent-chat.js` 只在第一条消息时删 `#ocEmpty`
    （`clearEmpty()`），从不处理这张卡；只有「新对话」的 `resetTaskFlow()` 会连它一起清掉。
  · 于是整个评估流程里这张气泡永久钉在最新消息上方，和栏头「技术工艺智能体 + AI 徽标」
    以及 `#ocEmpty` 的「工艺评估助手」引导重复。

本批只删父壳这一处静态气泡：`#ocEmpty` 引导、三颗结果入口、任务进度宿主、操作栏与输入区
一律保留；`index.html` 的 2.1 设计意图卡、2.2 / 2.3 的说明卡、`.oc-intent-card` CSS 规则
一律保留；JS 逻辑、后端与桥协议不动。
"""
from __future__ import annotations

import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
WORKBENCH = FRONTEND / "tech-workbench.html"
INDEX = FRONTEND / "index.html"
ASSEMBLY = FRONTEND / "assembly-integration.html"
COST = FRONTEND / "cost-review.html"
CHAT_CSS = FRONTEND / "agent-chat.css"
CHAT = FRONTEND / "agent-chat.js"
PARENT = FRONTEND / "tech-workbench.js"
BRIDGE = FRONTEND / "tech-board-bridge.js"

BUBBLE_TITLE = "技术工艺评估助手"
BUBBLE_BODY = "左侧会话在整个评估流程中持续存在"
AGENT_TITLE = "技术工艺智能体"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


class TechChatDropStaticIntroBubbleRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read(WORKBENCH)
        cls.index = read(INDEX)
        cls.assembly = read(ASSEMBLY)
        cls.cost = read(COST)
        cls.css = read(CHAT_CSS)
        cls.chat = read(CHAT)
        cls.parent = read(PARENT)
        cls.bridge = read(BRIDGE)

    # ------------------------------------------- 契约 A：气泡删除
    def test_intro_bubble_is_gone_from_the_shell(self):
        for token in ("oc-intent-card", "oc-intent-meta", BUBBLE_TITLE, BUBBLE_BODY):
            with self.subTest(token=token):
                self.assertNotIn(token, self.html,
                                 f"父壳里仍留着「{token}」这张常驻开场气泡")

    def test_thread_has_no_static_assistant_message(self):
        start = self.html.find('id="ocTinner"')
        end = self.html.find('id="ocEmpty"')
        self.assertGreater(start, -1, "找不到会话宿主 #ocTinner")
        self.assertGreater(end, start, "找不到空态 #ocEmpty（或它被挪到 #ocTinner 之外）")
        region = self.html[start:end]
        self.assertNotIn("oc-amsg", region,
                         "#ocTinner 顶部不得再有静态 .oc-amsg 气泡")
        self.assertNotIn('class="oc-amsg"', self.html,
                         "父壳不该再静态写任何助手消息")

    def test_intro_copy_is_not_left_anywhere_in_frontend(self):
        files = sorted(
            path for path in FRONTEND.rglob("*")
            if path.is_file() and path.suffix in {".html", ".js", ".css"}
        )
        self.assertTrue(files, "没找到任何前端文件")
        for path in files:
            text = read(path)
            for token in (BUBBLE_TITLE, BUBBLE_BODY):
                with self.subTest(file=path.name, token=token):
                    self.assertNotIn(token, text,
                                     f"{path.name} 里仍留着开场气泡文案")

    def test_empty_state_keeps_capability_guidance(self):
        start = self.html.find('id="ocTinner"')
        start_actions = self.html.find('id="ocResultActions"')
        self.assertGreater(start_actions, start, "找不到结果入口容器")
        region = self.html[start:start_actions]
        self.assertIn('id="ocEmpty"', region, "空态必须仍在 #ocTinner 内")
        for token in ("工艺评估助手", "按你的要求发起解析", "切换右侧步骤不会清空这里的会话"):
            with self.subTest(token=token):
                self.assertIn(token, region, f"空态引导丢了「{token}」")

    # ------------------------------------------- 契约 B：不许用替代物补位
    def test_no_replacement_bubble_or_second_card(self):
        start = self.html.find('id="ocTinner"')
        end = self.html.find('class="tech-chat-actions"')
        self.assertGreater(end, start, "找不到左侧操作栏")
        region = self.html[start:end]
        for token in ("oc-intent-card", "oc-intent-meta", "oc-amsg"):
            with self.subTest(token=token):
                self.assertNotIn(token, region,
                                 f"不得用 {token} 换个位置再补一张开场卡")

    def test_result_entries_and_progress_host_stay_in_the_thread(self):
        tinner = self.html.find('id="ocTinner"')
        actions = self.html.find('id="ocResultActions"')
        progress = self.html.find('id="ocTaskProgressHost"')
        self.assertGreater(tinner, -1, "找不到 #ocTinner")
        self.assertGreater(actions, tinner, "结果入口必须仍在 #ocTinner 内")
        self.assertGreater(progress, actions, "任务进度宿主必须仍在会话线程里")
        self.assertRegex(self.html[actions - 200:actions + 200], r'id="ocResultActions"[^>]*hidden',
                         "结果入口仍须默认隐藏，靠看板摘要点亮")

    def test_parent_js_does_not_inject_thread_nodes(self):
        for token in ("ocTinner", "ocThread", "oc-intent-card"):
            with self.subTest(token=token):
                self.assertNotIn(token, self.parent,
                                 f"父壳不得往会话栏写节点 / 造开场卡：{token}")

    def test_assistant_messages_come_from_real_replies_only(self):
        self.assertNotIn("oc-intent-card", self.chat,
                         "会话脚本不得再静态插一张开场卡")
        self.assertNotIn(BUBBLE_TITLE, self.chat, "会话脚本不得再写开场文案")
        self.assertIn("function clearEmpty()", self.chat, "空态的既有清理入口被删除")
        self.assertIn('"ocEmpty"', self.chat, "空态 id 依赖被改动")

    # ------------------------------------------- 契约 C：其它页面的同类卡保留
    def test_other_pages_keep_their_intent_cards(self):
        self.assertIn("oc-intent-card", self.index, "2.1 设计意图卡被误删")
        for token in ('id="intent"', 'id="btnParse"'):
            with self.subTest(token=token):
                self.assertIn(token, self.index, f"2.1 功能卡依赖的 {token} 被删除")
        for name, text in (("assembly-integration.html", self.assembly),
                           ("cost-review.html", self.cost)):
            with self.subTest(file=name):
                self.assertIn("oc-intent-card", text, f"{name} 的说明卡被误删")

    def test_intent_card_css_is_kept(self):
        for token in (".oc-intent-card {", ".oc-intent-meta {"):
            with self.subTest(token=token):
                self.assertIn(token, self.css, f"样式规则 {token} 仍在被其它页面使用")

    # ------------------------------------------- 契约 D：行为不变
    def test_first_message_still_removes_empty_state(self):
        self.assertRegex(self.chat, r"function clearEmpty\(\)[^\n]*ocEmpty[^\n]*remove\(\)",
                         "首条消息仍须移除空态，不得让它变成常驻")
        for call in ("addUser", "addAssistant"):
            with self.subTest(call=call):
                self.assertRegex(self.chat, rf"function {call}\([\s\S]{{0,80}}clearEmpty\(\)",
                                 f"{call}() 仍须清掉空态")

    def test_reset_flow_still_clears_thread_nodes_only(self):
        self.assertIn('querySelectorAll(".oc-amsg, .oc-ubub")', self.chat,
                      "「新对话」仍须按节点清理会话消息")
        self.assertNotIn("replaceChildren(", self.chat.split("results actions")[0][-4000:],
                         "不得改成 replaceChildren() 误删结果入口")

    def test_header_identity_and_composer_are_kept(self):
        for token in (AGENT_TITLE, "tech-ai-badge", "ocChatAttachBtn", "ocChatFileInput",
                      "ocInput", "ocSend", "techChatActions", "ocFilesAction",
                      "techChatPrimary", 'id="ocThread"', 'id="ocTinner"'):
            with self.subTest(token=token):
                self.assertIn(token, self.html, f"删除气泡不得带走 {token}")

    def test_protocol_whitelist_untouched(self):
        for name in ("READY", "ACTION_STATE", "TASK_PROGRESS", "TASK_COMPLETED",
                     "TASK_FAILED", "SELECTION_CHANGED", "BOARD_STATUS"):
            with self.subTest(name=name):
                self.assertIn(name + ":", self.bridge, f"桥事件白名单被改动：{name}")


if __name__ == "__main__":
    unittest.main()

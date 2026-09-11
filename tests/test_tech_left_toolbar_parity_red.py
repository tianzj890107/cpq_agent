"""第 16 步红测：统一左侧基础按钮（与报价 Agent 同级，按 stage 动态变化）。

覆盖：
1. 左侧会话栏新增唯一一条操作栏：附件 / AI 执行 / 上一步 / 下一步 / 转交任务 /
   主要操作 / 次要操作 / 失败重试，并复用既有结果入口与任务进度宿主；
2. 按钮由覆盖九个 stage 的描述表驱动，不只为 2.1–2.3 写死；
3. 主 / 次 / AI 走 TechBoardBridge，上下步走既有 applyStage，附件直接打开隐藏文件输入框；
4. 失败重试复用最近一次动作，不是新流程；
5. 不新增 @app. 路由；父壳不查 iframe DOM；既有底栏与结果入口不被删除。

不联网、不起服务、不读真实业务数据。
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
BACKEND = ROOT / "tech_app" / "backend"
WORKBENCH_HTML = FRONTEND / "tech-workbench.html"
WORKBENCH_JS = FRONTEND / "tech-workbench.js"
MAIN = BACKEND / "main.py"

# 左侧操作栏控件 id（Spec 第 1 节）。
TOOLBAR_IDS = (
    "techChatAttach", "techChatAiRun", "techChatPrev", "techChatNext",
    "techChatTransfer", "techChatPrimary", "techChatSecondary", "techChatRetry",
)
# 九个 stage id：描述表必须全覆盖，不能只写 2.1–2.3。
NINE_STAGES = (
    "requirement-create", "requirement-confirm", "requirement-review",
    "drawing", "process", "cost", "summary", "report-review", "report-publish",
)
KEPT_IDS = ("techPrev", "techNext", "techPrimary", "techSecondary",
            "ocResultActions", "ocTaskProgressHost",
            "ocChatAttachBtn", "ocChatFileInput")


def _read(path):
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def _js_block(text, marker, span=4000):
    idx = text.find(marker)
    if idx < 0:
        return ""
    return text[idx:idx + span]


class TechLeftToolbarParityRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = _read(WORKBENCH_HTML)
        cls.js = _read(WORKBENCH_JS)
        cls.main = _read(MAIN)

    # ---------------------------------------------------------------- 结构
    def test_left_pane_has_unified_action_bar(self):
        self.assertIn('id="techChatActions"', self.html,
                      "左侧会话栏没有统一操作栏 #techChatActions")
        for control in TOOLBAR_IDS:
            self.assertIn(f'id="{control}"', self.html,
                          f"左侧操作栏缺少控件 {control}")

    def test_toolbar_reuses_existing_result_and_progress_hosts(self):
        for kept in ("ocResultActions", "ocTaskProgressHost"):
            self.assertIn(f'id="{kept}"', self.html,
                          f"结果入口 / 任务进度必须复用既有宿主 {kept}")

    # ---------------------------------------------------------------- 动态描述表
    def test_stage_table_covers_all_nine_stages(self):
        block = _js_block(self.js, "STAGE_CHAT_ACTIONS")
        self.assertTrue(block, "tech-workbench.js 没有九阶段描述表 STAGE_CHAT_ACTIONS")
        for stage in NINE_STAGES:
            self.assertIn(stage, block, f"描述表缺少 stage {stage}（不能只为 2.1–2.3 写死）")

    def test_toolbar_controls_are_wired(self):
        for control in TOOLBAR_IDS:
            self.assertIn(control, self.js, f"tech-workbench.js 没有处理 {control}")

    # ---------------------------------------------------------------- 复用既有通道
    def test_primary_secondary_ai_go_through_board_bridge(self):
        self.assertIn("TechBoardBridge", self.js, "主 / 次 / AI 动作必须走看板桥")
        self.assertRegex(self.js, r"executeAction\(",
                         "主 / 次 / AI 动作必须经 TechBoardBridge.executeAction")
        self.assertIn("STAGE_ACTIONS", self.js, "必须复用既有 STAGE_ACTIONS 代理表")

    def test_prev_next_reuse_apply_stage(self):
        self.assertRegex(self.js, r"applyStage\(",
                         "上一步 / 下一步必须复用既有 applyStage")
        for kept in ("techPrev", "techNext"):
            self.assertIn(kept, self.js, f"既有底栏按钮 {kept} 被删除")

    def test_attach_reuses_the_hidden_file_input_not_a_menu(self):
        # 契约更新（附件直传 Spec）：附件不再走 ＋ 能力菜单，改为回形针按钮在同一次
        # 点击里直接触发父壳隐藏文件输入框，仍不新增第二套上传实现。
        self.assertIn("ocChatFileInput", self.js,
                      "附件必须直接触发父壳隐藏文件输入框，不新增上传实现")
        self.assertIn("ocChatAttachBtn", self.html, "附件入口应复用输入区回形针按钮")
        self.assertNotIn("ocCapabilityMenu", self.html,
                         "统一工作台不再使用 ＋ 能力菜单")

    def test_transfer_reuses_existing_capability_not_new_route(self):
        self.assertIn("techChatTransfer", self.js, "缺少转交任务处理")
        self.assertNotIn("/api/projects/{project_id}/transfer", self.main,
                         "不得为左侧按钮新增转交路由")
        self.assertIn("ocInput", self.js,
                      "无既有转交动作时，应把转交意图带进会话输入区")

    def test_retry_replays_last_action(self):
        self.assertIn("techChatRetry", self.js, "缺少失败重试控件处理")
        self.assertRegex(self.js, r"last[A-Za-z]*Action",
                         "失败重试必须记住并重跑最近一次动作")

    # ---------------------------------------------------------------- 边界
    def test_parent_shell_does_not_query_iframe_dom(self):
        for bad in ("contentDocument", "contentWindow.document"):
            self.assertNotIn(bad, self.js,
                             f"父壳不得查询 iframe DOM：{bad}")

    def test_no_new_backend_route_for_toolbar(self):
        self.assertNotIn("techChatActions", self.main,
                         "左侧按钮不得新增后端路由或实现")

    def test_existing_bottom_bar_and_hooks_not_removed(self):
        for kept in KEPT_IDS:
            self.assertIn(kept, self.html, f"既有节点 {kept} 被删除")


if __name__ == "__main__":
    unittest.main()

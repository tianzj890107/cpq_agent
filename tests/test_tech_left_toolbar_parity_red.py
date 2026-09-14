"""第 16 步红测：统一左侧基础按钮（与报价 Agent 同级，按 stage 动态变化）。

契约更新（「右侧看板业务按钮统一到左侧会话操作栏」批次）：
- 附件按钮 `techChatAttach` 删除（输入区圆形 ＋ 是唯一上传入口）；
- `techChatAiRun` / `techChatSecondary` 静态按钮删除，改由看板动作快照动态渲染
  （`[data-tech-action]`），业务动作名不再写死在父壳；
- 右侧业务卡底栏的 `techPrimary` / `techSecondary` 业务按钮退役（与左侧重复）；
- 九阶段壳导航表由 `STAGE_CHAT_ACTIONS` 换成不含动作名的 `STAGE_CHAT_FLOW`。

覆盖：
1. 左侧会话栏唯一一条操作栏：唯一主按钮 / 上一步 / 下一步 / 转交任务 / 失败重试，
   并复用既有结果入口与任务进度宿主；
2. 壳导航标记由覆盖九个 stage 的描述表驱动，不只为 2.1–2.3 写死；
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
CHAT_JS = FRONTEND / "agent-chat.js"
MAIN = BACKEND / "main.py"

# 左侧操作栏控件 id（现行契约）：「唯一主按钮槽位」+「任务文件」。
# 契约更新（「左侧操作栏只留当前步骤业务动作、去掉通用刷新与导航按钮」批次）：用户明确要求
# 去掉上一步 / 下一步 / 转交任务 / 失败重试这些每页重复的按钮，AI 执行与次按钮槽位也改由看板
# 动作快照动态渲染 —— 这些静态控件不得再回到父壳。
TOOLBAR_IDS = ("techChatPrimary", "ocFilesAction")
RETIRED_TOOLBAR_IDS = (
    "techChatPrev", "techChatNext", "techChatTransfer", "techChatRetry",
    "techChatAiRun", "techChatSecondary", "techChatAttach",
)
# 九个 stage id：描述表必须全覆盖，不能只写 2.1–2.3。
NINE_STAGES = (
    "requirement-create", "requirement-confirm", "requirement-review",
    "drawing", "process", "cost", "summary", "report-review", "report-publish",
)
KEPT_IDS = ("techPrev", "techNext", "techNowLabel",
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
        for retired in RETIRED_TOOLBAR_IDS:
            with self.subTest(retired=retired):
                self.assertNotIn(f'id="{retired}"', self.html,
                                 f"{retired} 已被「按钮统一到左侧」批次删除，不得回到父壳")
                self.assertNotIn(retired, self.js, f"父壳不得再驱动 {retired}")

    def test_toolbar_reuses_existing_result_and_progress_hosts(self):
        for kept in ("ocResultActions", "ocTaskProgressHost"):
            self.assertIn(f'id="{kept}"', self.html,
                          f"结果入口 / 任务进度必须复用既有宿主 {kept}")

    # ---------------------------------------------------------------- 动态描述表
    def test_stage_table_covers_all_nine_stages(self):
        # 契约更新（第 19 步「九阶段上下文」批次）：壳导航表定名为 STAGES，只描述九阶段的
        # 编号 / 名称 / 页面，不再携带任何业务动作名（动作由看板快照决定）。
        block = _js_block(self.js, "const STAGES = [")
        self.assertTrue(block, "tech-workbench.js 没有九阶段描述表 STAGES")
        for stage in NINE_STAGES:
            self.assertIn(stage, block, f"描述表缺少 stage {stage}（不能只为 2.1–2.3 写死）")
        for token in ("primary:", "secondary:"):
            with self.subTest(token=token):
                self.assertNotIn(token, block, "壳导航表不得再写死业务动作名")

    def test_toolbar_controls_are_wired(self):
        # 主按钮槽位由父壳按快照渲染（tech-workbench.js），任务文件入口在共享会话脚本里接线。
        self.assertIn("techChatPrimary", self.js, "tech-workbench.js 没有处理主按钮槽位")
        self.assertIn("ocFilesAction", _read(CHAT_JS), "任务文件入口没有接线")
        self.assertIn("data-tech-action", self.js,
                      "其余业务动作必须由看板快照动态渲染成按钮")

    # ---------------------------------------------------------------- 复用既有通道
    def test_primary_secondary_ai_go_through_board_bridge(self):
        self.assertIn("TechBoardBridge", self.js, "主 / 次 / AI 动作必须走看板桥")
        self.assertRegex(self.js, r"executeAction\(",
                         "业务动作必须经 TechBoardBridge.executeAction")
        self.assertIn("boardSnapshot()", self.js,
                      "按钮的文案 / 可见 / 可用必须来自看板动作快照，不再维护兜底动作名表")

    def test_prev_next_reuse_apply_stage(self):
        self.assertRegex(self.js, r"applyStage\(",
                         "上一步 / 下一步必须复用既有 applyStage")
        for kept in ("techPrev", "techNext"):
            self.assertIn(kept, self.js, f"既有底栏按钮 {kept} 被删除")

    def test_attach_reuses_the_hidden_file_input_not_a_menu(self):
        # 契约更新（附件直传 Spec + 输入区单行化批次）：附件不走能力菜单，由输入区左侧圆形 ＋
        # 在同一次点击里直接触发父壳隐藏文件输入框；接线在共享会话脚本 agent-chat.js（父壳
        # tech-workbench.js 不再重复实现一套上传），仍不新增第二套上传实现。
        chat = _read(CHAT_JS)
        self.assertIn("ocChatFileInput", chat,
                      "附件必须直接触发父壳隐藏文件输入框，不新增上传实现")
        self.assertIn("ocChatAttachBtn", chat, "＋ 按钮必须复用既有点击链路")
        self.assertIn("ocChatAttachBtn", self.html, "附件入口应复用输入区回形针按钮")
        self.assertNotIn("ocCapabilityMenu", self.html,
                         "统一工作台不再使用 ＋ 能力菜单")

    def test_transfer_reuses_existing_capability_not_new_route(self):
        # 契约更新（「去掉通用导航按钮」批次）：常驻「转交任务」按钮已按用户要求删除，
        # 转交能力仍由既有页面 / Agent 工具承担（2.2 的发送财务、报价侧的交接收件箱），
        # 不得为它新增后端路由。
        self.assertNotIn("techChatTransfer", self.html, "常驻转交按钮不得回到父壳")
        self.assertNotIn("techChatTransfer", self.js, "父壳不得再驱动转交按钮")
        self.assertNotIn("/api/projects/{project_id}/transfer", self.main,
                         "不得为左侧按钮新增转交路由")
        self.assertIn("/api/projects/{project_id}/integration/send-to-finance", self.main,
                      "既有转交能力（2.2 发送财务）不得被删除")

    def test_retry_replays_last_action(self):
        # 契约更新（「去掉通用导航按钮」+「业务动作一律可点、点了再给真实原因」两个批次）：
        # 常驻「失败重试」按钮与最近一次动作缓存已退役；失败恢复由「业务动作始终可点」+
        # 「真实错误落到标题行提示位」承担，不再靠父壳缓存上一次动作重放。
        self.assertNotIn("techChatRetry", self.html, "常驻失败重试按钮不得回到父壳")
        self.assertNotIn("techChatRetry", self.js, "父壳不得再驱动失败重试按钮")
        self.assertNotRegex(self.js, r"last[A-Za-z]*Action", "最近一次动作缓存应随按钮一起退役")
        for token in ("setBoardNotice", "task-failed"):
            with self.subTest(token=token):
                self.assertIn(token, self.js, f"失败恢复仍须有真实错误出口：{token}")

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

"""红测：技术工艺统一工作台换项目时，左侧会话必须重新绑定并回放该项目历史。

覆盖两条实测缺陷：
  1. agent-chat.js 的项目绑定是脚本加载时的一次性 const 快照 —— 统一工作台换项目
     （历史抽屉 / 看板 set_stage / 上下一步）只 pushState 不重载页面，左侧会话因此
     永远停在首次进入时的项目，历史带不回来，还会串出上一个项目的对话与任务卡。
  2. 换项目路径必须重新 GET /agent/history，且不得调用 /agent/new 或 resetTaskFlow()
     （换项目 ≠ 重置任务，后端已持久化的会话与项目结果一个都不能动）。

（`⚠ 读取历史会话失败：Not Found` 是本地 8010/8012 旧进程缺少 /agent/history 路由，
 属于环境问题：实测 127.0.0.1:8012/openapi.json 无 history，内网 172.16.10.34:8010
 同路由 401 = 存在；重启服务即消失，不写代码兼容。）
"""
import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
CHAT = (FRONTEND / "agent-chat.js").read_bytes().replace(b"\x00", b"").decode("utf-8")
WORKBENCH = (FRONTEND / "tech-workbench.js").read_text(encoding="utf-8")
MAIN = (ROOT / "tech_app" / "backend" / "main.py").read_text(encoding="utf-8")


def function_body(source: str, signature: str):
    """按大括号配平截出某个函数的完整实现体（含首尾大括号）；找不到返回 None。

    返回 None 而不是抛异常：函数还没实现时，失败点要落在具体断言上（红测的可读性）。
    """
    start = source.find(signature)
    if start < 0:
        return None
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace:index + 1]
    return None


class ChatProjectRebindContract(unittest.TestCase):
    def setUp(self):
        self.body = function_body(CHAT, "function setProject(")
        self.assertIsNotNone(
            self.body,
            "agent-chat.js 必须提供 setProject(projectId)：统一工作台换项目时重新绑定会话",
        )

    def test_project_binding_is_mutable_not_a_load_time_snapshot(self):
        self.assertRegex(
            CHAT,
            r"let\s+projectId\s*=\s*new URLSearchParams\(location\.search\)",
            "项目绑定必须是可重绑的 let projectId",
        )
        self.assertNotRegex(
            CHAT,
            r"const\s+projectId\s*=\s*new URLSearchParams\(location\.search\)",
            "一次性 const 快照正是「换项目后会话带不回来」的根因",
        )

    def test_chat_exports_single_rebind_entry(self):
        self.assertRegex(
            CHAT,
            r"window\.ocTechAgent\s*=\s*\{[\s\S]*?setProject[\s\S]*?\};",
            "window.ocTechAgent 必须导出 setProject，父壳只调这一个入口",
        )

    def test_rebind_reloads_history_and_meta_for_the_new_project(self):
        self.assertIn("historyLoaded = false", self.body, "重绑必须放开历史一次性闸门")
        self.assertRegex(
            self.body,
            r"loadHistory\s*\(\s*\)[\s\S]*?loadMeta\s*\(\s*\)",
            "重绑后必须重新回放该项目历史，再同步会话元信息",
        )

    def test_rebind_never_resets_the_persisted_conversation(self):
        for forbidden in ('api("/new")', "api('/new')", "resetTaskFlow("):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.body, "换项目不是重置任务，不得调用 /agent/new")

    def test_rebind_clears_previous_project_visible_state(self):
        self.assertIn("taskProgressCards.clear()", self.body)
        self.assertRegex(self.body, r"ocTaskProgressHost")
        self.assertRegex(self.body, r"replaceChildren\(", "任务卡 DOM 必须一起清掉，不能只清映射")
        self.assertRegex(self.body, r"\.oc-amsg")
        self.assertRegex(self.body, r"\.oc-ubub")
        self.assertIn("hasParsedIR = false", self.body)

    def test_rebind_is_idempotent_for_same_project(self):
        self.assertRegex(
            self.body,
            r"if\s*\([^)]*===?\s*String\(\s*projectId",
            "同一个项目重复同步必须提前返回：不重复请求、不重复渲染",
        )
        self.assertRegex(self.body, r"return\s*;", "幂等分支必须真的 return")

    def test_rebind_to_empty_project_makes_no_request(self):
        guard = self.body.index("if (!projectId)")
        self.assertLess(guard, self.body.index("loadHistory("),
                        "空项目分支必须在请求之前返回")
        self.assertRegex(self.body, r"未选择项目|未连接")

    def test_rebind_keeps_unsent_draft(self):
        self.assertNotRegex(self.body, r"input\.value\s*=",
                            "换项目不得清掉用户正在输入、还没发送的草稿")


class WorkbenchProjectSyncContract(unittest.TestCase):
    def setUp(self):
        self.apply_stage = function_body(WORKBENCH, "function applyStage(")
        self.popstate = function_body(WORKBENCH, "window.addEventListener('popstate'")
        self.assertIsNotNone(self.apply_stage, "tech-workbench.js 缺少 applyStage()")
        self.assertIsNotNone(self.popstate, "tech-workbench.js 缺少 popstate 处理")

    def test_apply_stage_syncs_current_project_to_chat(self):
        self.assertIn("syncChatProject()", self.apply_stage,
                      "applyStage 必须把当前项目同步给左侧会话")

    def test_sync_helper_uses_the_single_chat_entry_and_guards_missing_api(self):
        helper = function_body(WORKBENCH, "function syncChatProject(")
        self.assertIsNotNone(helper, "tech-workbench.js 缺少 syncChatProject()")
        self.assertIn("window.ocTechAgent", helper)
        self.assertIn("setProject", helper)
        self.assertRegex(helper, r"typeof[^\n]*setProject[^\n]*function",
                         "缺少入口时要安全跳过，不抛错、不阻断切步")

    def test_popstate_also_syncs_project(self):
        self.assertIn("syncChatProject()", self.popstate)

    def test_sync_runs_after_url_state_is_pushed(self):
        self.assertLess(
            self.apply_stage.index("pushState()"),
            self.apply_stage.index("syncChatProject()"),
            "会话读到的 URL 必须已经带上新项目",
        )


class HistoryCapabilityKeptContract(unittest.TestCase):
    def test_backend_history_route_is_still_read_only(self):
        self.assertRegex(
            MAIN,
            r"@app\.get\(\s*['\"]/api/projects/\{project_id\}/agent/history['\"]\s*\)",
        )
        self.assertNotRegex(
            MAIN,
            r"@app\.(?:post|put|patch|delete)\(\s*['\"]/api/projects/\{project_id\}/agent/history['\"]",
        )

    def test_replay_path_and_event_types_unchanged(self):
        load = function_body(CHAT, "async function loadHistory(")
        self.assertIsNotNone(load, "agent-chat.js 缺少 loadHistory()")
        self.assertIn('api("/history")', load)
        render = function_body(CHAT, "function renderHistory(")
        self.assertIsNotNone(render, "agent-chat.js 缺少 renderHistory()")
        for event_type in ("user", "assistant", "tool_use", "tool_result"):
            with self.subTest(event_type=event_type):
                self.assertIn(f'"{event_type}"', render)
        # 被取代的断言（会话时间线批次）：旧契约要求回放时主动跳过 tech_ui，
        # 于是结构化卡片永远无法在重进项目后恢复；新契约要求 tech_ui 照常回放。
        self.assertNotRegex(
            render,
            r'event\.name === "tech_ui"\)\s*return',
            "renderHistory() 不允许再跳过 tech_ui（见 docs/specs/tech-session-timeline-persistence-and-order.md）",
        )


if __name__ == "__main__":
    unittest.main()

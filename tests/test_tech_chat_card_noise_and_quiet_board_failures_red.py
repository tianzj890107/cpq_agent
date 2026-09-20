"""红测：技术工艺会话卡片降噪 + 看板「预期内失败」不再进会话（实现前应失败）。

现状缺口（实测）：
  · `tech-board-bridge.js` 切看板时用 `failPending('detached', '看板已切换，命令已取消。')`
    拒绝在途命令，父壳 `tech-workbench.js` 的 `runBoardAction()` catch 原样 `chatNotice()`
    → 会话里出现多条 `⚠ 看板已切换，命令已取消。`。
  · `requirement-confirm-page.js` 的 `cfAct()` 门禁（`missing-comment`）与
    `applyConfirmationNote` 的 `note-target-missing` 也会被同样地打进会话，而看板自己
    已经 toast 过 / 只是当前视图没有那个输入框。
  · `tech-board-runtime.js` 的 `publishTaskCard()` 对**每个**动作都发
    `task-progress(phase:'start')` + `task-completed`，父壳 `renderTaskProgress()` 只要拿到
    `label` 就建卡（按 taskId 去重、永不删除），于是秒级同步动作每次点击都留一张
    「提交审核意见 · 已完成」；页面自己再补一条 `progress: '正在通过确认…'` 也只是空转进度。
    用户要求：报错卡不要、只有标题 / 纯状态的卡不要，只保留有真实执行明细的卡。

本批只改「失败与进度的呈现」这一层；桥的信封 / 白名单 / 状态事件、看板动作注册与业务
实现、后端路由与 Agent 工具、`.oc-task-card` 样式与结果入口常驻一律不动。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = ROOT / "tech_app" / "frontend"
CHAT_JS = F / "agent-chat.js"
CHAT_CSS = F / "agent-chat.css"
BRIDGE = F / "tech-board-bridge.js"
RUNTIME = F / "tech-board-runtime.js"
WORKBENCH_JS = F / "tech-workbench.js"
WORKBENCH_HTML = F / "tech-workbench.html"
APP_JS = F / "app.js"
CONFIRM_PAGE = F / "requirement-confirm-page.js"
MAIN = ROOT / "tech_app" / "backend" / "main.py"

# 预期内失败码：用户主动切走、或当前视图没有目标输入框、或必填项没填（看板已 toast）。
QUIET_CODES = ("detached", "note-target-missing", "missing-comment", "no-selection")


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def block_from(text: str, marker: str) -> str:
    idx = text.find(marker)
    if idx < 0:
        return ""
    brace = text.find("{", idx + len(marker))
    if brace < 0:
        return ""
    depth = 0
    i = brace
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            i = len(text) if j < 0 else j
            continue
        if ch in "\"'`":
            quote = ch
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    break
                i += 1
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
        i += 1
    return ""


class ChatCardNoiseAndQuietBoardFailuresRed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chat = read(CHAT_JS)
        cls.css = read(CHAT_CSS)
        cls.bridge = read(BRIDGE)
        cls.runtime = read(RUNTIME)
        cls.wb_js = read(WORKBENCH_JS)
        cls.wb_html = read(WORKBENCH_HTML)
        cls.app = read(APP_JS)
        cls.confirm_page = read(CONFIRM_PAGE)
        cls.main = read(MAIN)

    # ------------------------------------------- R1 只留「有真实执行明细」的卡
    def test_task_card_requires_real_progress_log(self):
        body = block_from(self.chat, "function renderTaskProgress(")
        self.assertTrue(body, "找不到 renderTaskProgress()")
        self.assertIn("log.length > 0", body,
                      "必须用 progress_log 明细判定「这张卡有没有真实内容」")
        self.assertRegex(body, r"if\s*\(!\s*hasContent",
                         "没有真实内容的任务事件必须直接 return，不建空卡")
        self.assertIn("taskProgressCards.has(", body,
                      "同一 taskId 已有卡时必须继续就地更新，不重复建卡")

    def test_no_content_event_is_never_rendered(self):
        body = block_from(self.chat, "function renderTaskProgress(")
        # 旧行为：拿到 label 就直接建卡，于是「提交审核意见 · 已完成」这类纯标题卡留在会话里。
        gate = body.find("hasContent")
        create = body.find("ensureTaskCard(")
        self.assertGreater(gate, -1, "缺少「有内容」闸门")
        self.assertGreater(create, -1, "找不到 ensureTaskCard(")
        self.assertLess(gate, create, "建卡前必须先过「有内容」闸门，不能先建后判")

    def test_failed_card_needs_real_reason_and_is_quiet_aware(self):
        body = block_from(self.chat, "function renderTaskProgress(")
        self.assertIn("failureReason", body, "失败卡必须来自真实原因，而不是兜底「任务失败」")
        self.assertIn("detail.code", body, "失败卡必须看载荷里的失败码，跳过预期内失败")
        self.assertIn("isQuietBoardCode(", body, "预期内失败码判定必须收口到一个函数")

    def test_bulk_process_progress_carries_real_detail(self):
        body = block_from(self.app, "function allPartsProcessPublish(")
        self.assertTrue(body, "找不到 allPartsProcessPublish()")
        self.assertIn("log:", body,
                      "2.1 批量工艺推荐是真实长任务：必须上报逐件明细，卡才不会降噪掉")
        self.assertIn("progress:", body, "既有 progress 文本保留")
        self.assertIn("task-progress", body, "仍走既有 task-progress 事件")

    # ------------------------------------------- R2 预期内失败不进会话
    def test_bridge_marks_quiet_failures(self):
        self.assertIn("QUIET_FAILURE_CODES", self.bridge, "桥缺少预期内失败码集合")
        for code in QUIET_CODES:
            with self.subTest(code=code):
                self.assertIn(f"'{code}'", self.bridge, f"预期内失败码 {code} 缺失")
        self.assertRegex(self.bridge, r"function\s+isQuietFailure\s*\(",
                         "桥必须暴露统一的预期内失败判定")
        self.assertRegex(self.bridge, r"isQuietFailure:\s*isQuietFailure|isQuietFailure\s*:",
                         "isQuietFailure 必须挂在 TechBoardBridge 上供父壳与会话共用")
        settle = block_from(self.bridge, "function settle(")
        self.assertIn("quiet", settle, "settle() 必须给预期内失败打 quiet 标记")
        pending = block_from(self.bridge, "function failPending(")
        self.assertIn("quiet", pending, "failPending() 必须给预期内失败打 quiet 标记")

    def test_parent_skips_quiet_failures(self):
        body = block_from(self.wb_js, "function runBoardAction(")
        self.assertTrue(body, "找不到 runBoardAction()")
        self.assertIn("isQuietFailure", body, "父壳必须识别预期内失败")
        self.assertRegex(body, r"isQuietFailure[\s\S]{0,200}return",
                         "预期内失败必须提前 return，不进会话也不占标题行提示位")
        self.assertIn("chatNotice", body, "非预期失败的会话提示保留")

    def test_chat_has_single_quiet_aware_failure_exit(self):
        self.assertRegex(self.chat, r"function\s+boardFailureNotice\s*\(",
                         "会话缺少统一的看板失败出口 boardFailureNotice")
        helper = block_from(self.chat, "function boardFailureNotice(")
        self.assertIn("isQuietBoardCode(", helper, "出口必须先判预期内失败")
        gate = block_from(self.chat, "function isQuietBoardCode(")
        self.assertIn("isQuietFailure", gate,
                      "会话侧的预期内失败码必须与桥的 QUIET_FAILURE_CODES 同源")
        self.assertGreaterEqual(
            len(re.findall(r"boardFailureNotice\(", self.chat)), 9,
            "看板动作 / 导航 / 刷新失败必须统一走这个出口")

    def test_note_actions_use_the_quiet_exit(self):
        for marker in ("function applyConfirmationNoteAction(", "function applyReviewNoteAction("):
            with self.subTest(marker=marker):
                body = block_from(self.chat, marker)
                self.assertTrue(body, f"找不到 {marker}")
                self.assertIn("boardFailureNotice(", body,
                              "带入确认 / 审核意见失败必须走统一出口（预期内失败不再报⚠）")

    def test_runtime_forwards_failure_code(self):
        body = block_from(self.runtime, "function runEntry(")
        self.assertTrue(body, "找不到 runEntry()")
        self.assertGreaterEqual(
            len(re.findall(r"publishTaskCard\(EVENT\.TASK_FAILED", body)), 3,
            "runEntry 的三条失败路径都要发布 task-failed")
        for chunk in re.findall(r"publishTaskCard\(EVENT\.TASK_FAILED[\s\S]{0,220}?\}\);", body):
            with self.subTest(chunk=chunk[:60]):
                self.assertIn("code:", chunk,
                              "task-failed 载荷必须带上失败码，父壳才能跳过预期内失败")

    def test_confirm_note_target_missing_is_not_an_error(self):
        self.assertNotIn("note-target-missing", self.confirm_page,
                         "当前视图没有确认意见输入框时不得再返回错误码（会产生⚠卡）")
        block = block_from(self.confirm_page, "applyConfirmationNote:")
        self.assertTrue(block, "找不到 applyConfirmationNote 动作")
        self.assertIn("applied", block, "仍要如实回传有没有写进去")
        self.assertNotRegex(block, r"ok:\s*false", "目标缺失 / 意见为空不再是失败")

    # ------------------------------------------- 防缩水守卫
    def test_task_card_pipeline_kept(self):
        for token in ("function renderTaskProgress(", "function ensureTaskCard(",
                      "function pushTaskStep(", "ocTaskProgressHost"):
            with self.subTest(token=token):
                self.assertIn(token, self.chat)
        self.assertIn(".oc-task-card", self.css, "任务卡样式被删除")
        self.assertIn('id="ocTaskProgressHost"', self.wb_html, "会话进度宿主被删除")

    def test_bridge_protocol_unchanged(self):
        for token in ("envelope('command', name, payload, requestId)",
                      "if (type !== 'state') return;",
                      "STATE_EVENTS", "'board-status'", "'selection-changed'",
                      "DEFAULT_TIMEOUT = 20000"):
            with self.subTest(token=token):
                self.assertIn(token, self.bridge, f"桥协议被改动：{token}")

    def test_navigate_and_result_entries_kept(self):
        for token in ("function boardNavigateView(", "function showBoardNavFailure(",
                      "oc-chip-retry", "navigateView"):
            with self.subTest(token=token):
                self.assertIn(token, self.chat, f"导航 / 结果入口被改动：{token}")
        self.assertIn("runBoardView(\"evidence\")", self.app, "解析视图看板视图本体被删除")

    def test_backend_routes_and_tools_kept(self):
        for route in ("/api/projects/{project_id}/requirement/confirm",
                      "/api/projects/{project_id}/requirement/review",
                      "/api/projects/{project_id}/requirement/return-to-draft",
                      "/api/projects/{project_id}/requirement/extract-documents"):
            with self.subTest(route=route):
                self.assertIn(route, self.main, f"既有需求路由被删除：{route}")


if __name__ == "__main__":
    unittest.main()

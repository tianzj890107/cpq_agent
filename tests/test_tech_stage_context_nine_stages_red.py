"""第 19 步红测：九个内部阶段都有独立 page_context，缺上下文时不冒充 2.1。

覆盖：
1. STAGE_AGENT_CONTEXT 覆盖九个 stage id，每个 stage 有独立且互不重复的 pageContext，
   取值与所属子步骤号一致（1.1 / 1.2 / 1.3 / 2.1 / 2.2 / 2.3 / 3.1 / 3.2 / 3.3）；
2. stageAgentContext() 只按当前 stage 取上下文，查不到返回 null，不回退成 2.1；
3. agent-chat.js 的 currentPageContext() 不再硬回退成 "2.1 图纸解析"；
4. 后端保存会话轮次时 page_context 默认值不再是 "2.1 图纸解析"。

不联网、不起服务、不读真实业务数据。
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "tech_app" / "frontend"
BACKEND = ROOT / "tech_app" / "backend"

# stage id →(子步骤号, 该步骤应有的关键词之一)
STAGE_CONTEXTS = (
    ("requirement-create", "1.1", ("创建",)),
    ("requirement-confirm", "1.2", ("确认",)),
    ("requirement-review", "1.3", ("审核",)),
    ("drawing", "2.1", ("图纸解析",)),
    ("process", "2.2", ("组装",)),
    ("cost", "2.3", ("成本",)),
    ("summary", "3.1", ("汇总",)),
    ("report-review", "3.2", ("审核",)),
    ("report-publish", "3.3", ("发布",)),
)


def _read(path):
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def _js_function(text, name, span=900):
    idx = text.find(f"function {name}(")
    if idx < 0:
        return ""
    return text[idx:idx + span]


class TechStageContextNineStagesRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = _read(FRONTEND / "tech-workbench.js")
        cls.chat = _read(FRONTEND / "agent-chat.js")
        cls.main = _read(BACKEND / "main.py")
        match = re.search(r"STAGE_AGENT_CONTEXT\s*=\s*\{([\s\S]*?)\n  \};", cls.js)
        cls.context_block = match.group(1) if match else ""

    # ---------------------------------------------------------------- 九阶段覆盖
    def test_stage_agent_context_covers_nine_stages(self):
        self.assertTrue(self.context_block, "tech-workbench.js 缺少 STAGE_AGENT_CONTEXT")
        for stage, _no, _kw in STAGE_CONTEXTS:
            self.assertRegex(
                self.context_block,
                rf"['\"]?{re.escape(stage)}['\"]?\s*:\s*\{{",
                f"STAGE_AGENT_CONTEXT 缺少 stage {stage}")

    def test_each_stage_has_independent_page_context(self):
        values = re.findall(r"pageContext\s*:\s*['\"]([^'\"]+)['\"]",
                            self.context_block)
        self.assertEqual(len(values), 9,
                         f"九个 stage 必须各有 pageContext，实际 {values}")
        self.assertEqual(len(set(values)), 9,
                         f"九个 pageContext 必须互不重复：{values}")

    def test_page_context_matches_own_substep(self):
        for stage, no, keywords in STAGE_CONTEXTS:
            match = re.search(rf"['\"]?{re.escape(stage)}['\"]?\s*:\s*\{{",
                              self.context_block)
            self.assertIsNotNone(match, f"缺少 stage {stage}")
            entry = self.context_block[match.start():match.start() + 420]
            found = re.search(r"pageContext\s*:\s*['\"]([^'\"]+)['\"]", entry)
            self.assertIsNotNone(found, f"{stage} 缺少 pageContext")
            value = found.group(1)
            self.assertIn(no, value,
                          f"{stage} 的 pageContext 必须带子步骤号 {no}：{value}")
            self.assertTrue(any(kw in value for kw in keywords),
                            f"{stage} 的 pageContext 应描述本步骤（{keywords}）：{value}")

    # ---------------------------------------------------------------- 不回退成 2.1
    def test_stage_context_lookup_does_not_fall_back_to_another_stage(self):
        body = _js_function(self.js, "stageAgentContext")
        self.assertTrue(body, "缺少 stageAgentContext")
        self.assertIn("state.stage", body, "必须按当前 stage 取上下文")
        self.assertIn("return null", body, "查不到上下文必须返回 null")
        self.assertNotRegex(body, r"['\"]2\.1 图纸解析['\"]",
                            "stageAgentContext 不得回退成 2.1 图纸解析")

    def test_agent_chat_does_not_default_page_context_to_2_1(self):
        body = _js_function(self.chat, "currentPageContext")
        self.assertTrue(body, "agent-chat.js 缺少 currentPageContext")
        self.assertIn("pageContext", body, "必须返回当前 stageContext.pageContext")
        self.assertNotIn("2.1 图纸解析", body,
                         "currentPageContext 不得硬回退成 2.1 图纸解析")

    def test_backend_does_not_default_page_context_to_2_1(self):
        self.assertNotIn('body.page_context or "2.1 图纸解析"', self.main,
                         "后端保存会话轮次不得默认冒充 2.1 图纸解析")
        self.assertNotRegex(
            self.main,
            r"page_context[^\n]{0,60}or\s*['\"]2\.1 图纸解析['\"]",
            "page_context 缺失时不得回退成 2.1 图纸解析")

    # ---------------------------------------------------------------- 边界
    def test_nine_stage_ids_unchanged(self):
        for stage, _no, _kw in STAGE_CONTEXTS:
            self.assertIn(f"'{stage}'", self.js,
                          f"九个 stage id 不得改动：{stage} 缺失")


if __name__ == "__main__":
    unittest.main()

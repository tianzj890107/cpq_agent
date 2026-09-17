"""技术工艺 Agent「结果呈现」提示词口径 红测基线（第一轮，只动后端提示词）。

缺口：`tech_app/backend/services/oc_agent.py` 的 `SYSTEM_APPENDIX`（:2779）只覆盖 2.1 / 2.2
两个阶段，「硬性要求」只有三条（数值只能来自工具、中文简洁结论先行、文件系统只读），
全篇没有 `tech_ui`、没有「数据一律右侧看板」，也没有任何禁止在聊天里贴 Markdown 表格 /
大段 JSON 的约束。报价侧则是「工具描述 + 系统提示词」双保险
（`cpq_agent_server.py:108-110` 与 `:1770-1772`）。

本批只改后端提示词：把「结果呈现」硬性要求与九阶段落点表补进 `SYSTEM_APPENDIX`，
不动前端、不动工具 schema、不动八项 action 白名单与视图白名单。

不联网、不起服务、不读真实业务数据；只做静态契约校验。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT_SERVICE = ROOT / "tech_app" / "backend" / "services" / "oc_agent.py"

# 九个内部阶段 id，落点表必须逐个覆盖。
STAGE_IDS = (
    "requirement-create",
    "requirement-confirm",
    "requirement-review",
    "drawing",
    "process",
    "cost",
    "summary",
    "report-review",
    "report-publish",
)

# 必须原样出现的结果呈现口径（报价侧已有同源说法）。
REQUIRED_SENTENCES = (
    "绝不允许只把表格写在聊天文字里",
    "不要贴 Markdown 表格",
    "右侧看板是唯一真源",
    "只有当右侧看板已注册该视图时才调用 focus_view",
)

# 第 17 步固定的八项 tech_ui action（不得增删）。
TECH_UI_ACTIONS = (
    "focus_view",
    "refresh_view",
    "fill_fields",
    "select_part",
    "show_result_actions",
    "show_progress",
    "set_stage",
    "request_confirmation",
)

TECH_UI_VIEWS = (
    "parts", "questions", "report", "evidence", "review", "files", "upload", "import3d",
    "drawing-overview", "parts-list", "part-detail", "part-process", "part-cost",
    "drawings", "params", "process", "assembly", "total",
)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def _appendix(text: str) -> str:
    match = re.search(r'SYSTEM_APPENDIX\s*=\s*"""([\s\S]*?)"""', text)
    return match.group(1) if match else ""


class TechResultPresentationPromptRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = _read(AGENT_SERVICE)
        cls.appendix = _appendix(cls.source)

    def test_system_appendix_exists(self):
        self.assertTrue(self.appendix, "oc_agent.py 里找不到 SYSTEM_APPENDIX 提示词正文")

    def test_result_presentation_hard_rules_are_present(self):
        missing = [s for s in REQUIRED_SENTENCES if s not in self.appendix]
        self.assertFalse(
            missing,
            "SYSTEM_APPENDIX 缺少结果呈现口径，模型会继续把表格/明细贴进聊天：" + "；".join(missing),
        )

    def test_chat_must_not_dump_json_or_field_lists(self):
        self.assertRegex(
            self.appendix,
            r"(不要|禁止|不得)[^。\n]{0,12}JSON",
            "SYSTEM_APPENDIX 没有禁止在聊天里贴大段 JSON / 字段清单",
        )

    def test_nine_stage_landing_map_covers_every_stage(self):
        missing = [stage for stage in STAGE_IDS if stage not in self.appendix]
        self.assertFalse(
            missing,
            "九阶段落点表没有覆盖这些阶段（模型不知道该往哪放结果）：" + ", ".join(missing),
        )

    def test_prompt_tells_model_to_land_results_via_tech_ui(self):
        for token in ("tech_ui", "focus_view", "refresh_view"):
            self.assertIn(
                token,
                self.appendix,
                "SYSTEM_APPENDIX 必须说明经 tech_ui 把结果落到右侧看板：缺少 %s" % token,
            )

    # ------------------------------------------------------------------ 保留守卫
    def test_existing_hard_requirements_are_kept(self):
        for phrase in ("只能来自工具", "回答用中文", "只读"):
            self.assertIn(phrase, self.appendix, "既有硬性要求被删：%s" % phrase)

    def test_existing_stage_guidance_is_kept(self):
        self.assertIn("2.1 图纸解析", self.appendix)
        self.assertIn("3 组装与整合", self.appendix)
        self.assertIn("4 成本测算", self.appendix)

    def test_prompt_injection_and_whitelists_are_unchanged(self):
        self.assertRegex(
            self.source,
            r'system_prompt\s*=\s*f"\{self\.conv\.system_prompt\}\\n\{SYSTEM_APPENDIX\}"',
            "SYSTEM_APPENDIX 的注入方式被改动",
        )
        actions = re.search(r"TECH_UI_ACTIONS\s*=\s*\(([\s\S]*?)\)", self.source)
        self.assertTrue(actions, "找不到 TECH_UI_ACTIONS 白名单")
        for action in TECH_UI_ACTIONS:
            self.assertIn('"%s"' % action, actions.group(1), "tech_ui action 白名单被改动：%s" % action)
        views = re.search(r"TECH_UI_VIEWS\s*=\s*\(([\s\S]*?)\)", self.source)
        self.assertTrue(views, "找不到 TECH_UI_VIEWS 白名单")
        for view in TECH_UI_VIEWS:
            self.assertIn('"%s"' % view, views.group(1), "看板视图白名单被改动：%s" % view)


if __name__ == "__main__":
    unittest.main()

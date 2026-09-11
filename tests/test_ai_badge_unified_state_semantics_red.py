"""四助手 + 技术工艺的 `AI` 徽标状态语义统一（常态浅蓝描边 / hover 实心深蓝）红测基线。

背景：报价 Agent 页（`确认需求解析结果.html`）与技术工艺（`tech-workbench.css` 的
`.tech-ai-badge`）已经统一为「常态浅蓝描边 + hover 实心深蓝」。但：

  · 报价 Agent 页 `.ai-badge` 的基础规则仍写着 `background: var(--gradient-ai)`，
    被后面的强调控件规则覆盖 —— 是死规则，从上往下读会被误判成"报价实心渐变"；
  · `报价规则.html` / `规则助手-规则配置.html` / `XBOM智能体-配置BOM生成.html`
    三处 `.ai-badge` 仍是纯实心渐变，没有描边常态与 hover 态。

本文件把四页统一锁成同一套状态语义，避免再出现"同一个 AI 徽标两种外观"。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

QUOTE_PAGES = (
    "确认需求解析结果.html",
    "报价规则.html",
    "规则助手-规则配置.html",
    "XBOM智能体-配置BOM生成.html",
)
TECH_CSS = ROOT / "tech_app" / "frontend" / "tech-workbench.css"

# 精确匹配 `.ai-badge` 本体（排除 `.tech-ai-badge` 与 `:hover` 变体）。
BADGE_RULE = re.compile(r"(?<![\w-])\.ai-badge(?!:)[^{}]*\{([^}]*)\}")
BADGE_HOVER = re.compile(r"(?<![\w-])\.ai-badge:hover[^{}]*\{([^}]*)\}")
TECH_BADGE_RULE = re.compile(r"\.tech-ai-badge[^{}]*\{([^}]*)\}")
TECH_BADGE_HOVER = re.compile(r"\.tech-ai-badge:hover[^{}]*\{([^}]*)\}")

RESTING_DECLARATIONS = (
    "background:var(--color-primary-page)",
    "color:var(--color-primary)",
    "border:1pxsolidvar(--color-primary-border)",
)


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


class AiBadgeStateSemanticsRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = {
            name: compact((ROOT / name).read_text(encoding="utf-8"))
            for name in QUOTE_PAGES
        }
        cls.tech = compact(TECH_CSS.read_text(encoding="utf-8"))

    def test_no_ai_badge_keeps_a_solid_gradient_resting_style(self):
        offenders = []
        for name, source in self.pages.items():
            for body in BADGE_RULE.findall(source):
                if "var(--gradient-ai)" in body:
                    offenders.append(f"{name}: {body}")
        self.assertFalse(
            offenders,
            "AI 徽标不得再以实心渐变作为常态样式（现存死规则或未对齐）："
            f"{offenders}",
        )

    def test_every_ai_badge_is_outlined_at_rest(self):
        missing = []
        for name, source in self.pages.items():
            bodies = BADGE_RULE.findall(source)
            if not any(all(token in body for token in RESTING_DECLARATIONS) for body in bodies):
                missing.append(name)
        self.assertFalse(
            missing,
            f"这些页面的 AI 徽标缺少浅蓝描边常态：{missing}",
        )

    def test_every_ai_badge_turns_solid_deep_blue_on_hover(self):
        missing = []
        for name, source in self.pages.items():
            bodies = BADGE_HOVER.findall(source)
            if not any(
                "background:var(--color-primary-active)" in body
                and "color:white" in body
                and "border-color:var(--color-primary-active)" in body
                for body in bodies
            ):
                missing.append(name)
        self.assertFalse(
            missing,
            f"这些页面的 AI 徽标缺少 hover 实心深蓝态：{missing}",
        )

    def test_tech_agent_badge_keeps_the_same_state_semantics(self):
        rest = TECH_BADGE_RULE.findall(self.tech)
        hover = TECH_BADGE_HOVER.findall(self.tech)
        self.assertTrue(rest, "技术工艺 .tech-ai-badge 规则缺失")
        self.assertTrue(
            any(
                "background:var(--color-primary-page)" in body
                and "border:1pxsolidvar(--color-primary-border)" in body
                for body in rest
            ),
            "技术工艺 AI 徽标常态应为浅蓝描边",
        )
        self.assertTrue(hover, "技术工艺 .tech-ai-badge:hover 规则缺失")
        self.assertTrue(
            any(
                "background:var(--twb-primary-active)" in body and "color:#fff" in body
                for body in hover
            ),
            "技术工艺 AI 徽标 hover 应为实心深蓝白字",
        )


if __name__ == "__main__":
    unittest.main()

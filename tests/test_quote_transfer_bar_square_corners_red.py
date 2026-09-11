"""报价「已转交·待领取」提示条去倒角（直角化）的红测基线。

背景：`确认需求解析结果.html` 的 `wfRenderBar()` 会创建 `#wfBar` 顶部提示条，
它的内联样式里写了 `border-radius:8px`。这条提示条承载的是流程状态文案，
例如：
    「已转交·待领取：销售经理·salesm1 发起「新增工艺」 · 请到技术工艺新增产品
      · 指派给指定人员。对方领取后即可继续第 1 步。」
需求：这类流程状态提示条不做倒角（直角），但提示条本身的留白、描边与配色不变，
且页面其它圆角元素（气泡、胶囊、卡片等）不受影响。
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
QUOTE = ROOT / "确认需求解析结果.html"


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


class QuoteTransferBarSquareCornersRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = QUOTE.read_text(encoding="utf-8")
        cls.quote = compact(cls.raw)

    def wf_bar_creation_block(self) -> str:
        match = re.search(r"if\(!bar\)\{(.*?)\}", self.quote)
        self.assertIsNotNone(match, "找不到 #wfBar 的创建分支（wfRenderBar）")
        return match.group(1)

    def test_transfer_bar_has_no_corner_radius(self):
        block = self.wf_bar_creation_block()
        radii = re.findall(r"border-radius:([^;'\"]+)", block)
        offenders = [value for value in radii if value.strip() not in ("0", "0px")]
        self.assertFalse(
            offenders,
            "「已转交·待领取」流程提示条不做倒角，实际仍在设置圆角："
            f"{offenders}（原文 {block!r}）",
        )

    def test_transfer_bar_keeps_its_box_style(self):
        block = self.wf_bar_creation_block()
        for token in ("padding:", "border:1pxsolid", "font-size:12px", "line-height:"):
            self.assertIn(
                token, block,
                f"去倒角只应改圆角，提示条的既有样式项 {token} 不应被删掉",
            )

    def test_transfer_bar_states_are_unchanged(self):
        for token in ("待转交", "已转交·待领取", "未登录", "只能查看", "对方领取后即可继续第"):
            self.assertIn(token, self.quote, f"流程提示条的状态文案被改动或删除：{token}")

    def test_other_rounded_elements_are_untouched(self):
        # 只针对流程状态提示条；页面其它圆角（气泡 14px、胶囊 9999px 等）必须保留。
        for token in ("border-radius:14px", "border-radius:9999px", "border-radius:50%"):
            self.assertIn(
                token, self.quote,
                f"去倒角不应波及其它元素，{token} 不应消失",
            )


if __name__ == "__main__":
    unittest.main()

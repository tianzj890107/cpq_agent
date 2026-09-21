"""红测：第 3/4 步拿不到产品行时的可执行提示（不再单一归因）。

Spec：`docs/specs/quote-markup-gate-advice.md`

现状缺口（实测）：

  · `确认需求解析结果.html:3749` 的空产品分支只有一句
    `addErrorBubble('第 ' + step + ' 步无法计算' + cfg.column + '：前面步骤还没有产品信息。');`
    —— 归因只有「前面没填」，而现场那单是**产品库里没有适配标品**（非标定制），
    按这句话去补是补不出来的；
  · 该分支没有任何可执行动作，用户拿不到出口，这正是「走不过去」的直接体感；
  · `grep -c markupGateAdvice` → 0，`grep -c addGateActionBubble` → 0。

本批边界（Spec §1.1）：只改这句提示与它的动作，**不动**非标判定、第 2 步分区、
`carryProducts`/`CARRY_MAP`、门禁行为（仍然 return false）。

分组（Spec §5）：A 提示构造器 6、B 空产品分支 4、C 动作气泡 2、D 不回归护栏 3。

纪律：
  · 全部离线、确定性、无网络：不连 Postgres、不调模型、不起服务；
  · 只读源码，不改服务器配置；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUOTE_HTML = ROOT / "确认需求解析结果.html"
SERVER_PY = ROOT / "cpq_agent_server.py"

#: 空产品分支的原文案 —— 本批必须被移除（Spec §3.2 第 4 条）。
OLD_ONE_CAUSE_TEXT = "：前面步骤还没有产品信息。"
#: 该分支的定位串（全页唯一）。
EMPTY_BRANCH_MARK = "if (!products.length) {"
#: A 档同批的出口：与按钮区「转技术工艺」按钮同一条任务（Spec §3.1 第 3 条）。
HANDOFF_ACTION = "wfOpenSend(false, 'tech_new_product')"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def body_after(src: str, marker: str, size: int = 2600) -> str:
    i = src.find(marker)
    return "" if i < 0 else src[i:i + size]


class MarkupGateAdviceCase(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.html = read(QUOTE_HTML)

    def advice(self) -> str:
        block = body_after(self.html, "function markupGateAdvice", 1200)
        self.assertTrue(
            block,
            "找不到 markupGateAdvice(step, column)（Spec §3.1）："
            "第 3/4 步的提示还是硬编码一句话，两种原因说不到")
        return block

    def empty_branch(self) -> str:
        block = body_after(self.html, EMPTY_BRANCH_MARK, 420)
        self.assertTrue(block, "定位不到 runMarkupStep 的空产品分支（Spec §3.2）")
        return block


# --------------------------------------------------------------------------- #
# A. 提示构造器存在且内容正确（Spec §3.1）
# --------------------------------------------------------------------------- #
class AAdviceBuilder(MarkupGateAdviceCase):

    def test_a1_helper_exists_before_run_markup_step(self):
        self.advice()
        i_advice = self.html.find("function markupGateAdvice")
        i_run = self.html.find("async function runMarkupStep")
        self.assertTrue(0 < i_advice < i_run,
                        "markupGateAdvice 必须定义在 runMarkupStep 之前（Spec §3.1）")

    def test_a2_takes_step_and_column(self):
        self.assertIn("markupGateAdvice(step, column)", self.advice(),
                      "签名必须是 markupGateAdvice(step, column)（Spec §3.1）")

    def test_a3_names_cause_one(self):
        self.assertIn("还没有选定产品", self.advice(),
                      "第一种原因要说清：前面还没有选定产品（Spec §3.1 第 2 条）")

    def test_a4_names_cause_two(self):
        self.assertIn("新增产品", self.advice(),
                      "第二种原因要说清：产品库里没有适配标品、需要转技术工艺新增产品"
                      "（Spec §3.1 第 2 条）——这条正是现场那单")

    def test_a5_carries_the_tech_handoff_action(self):
        self.assertIn(HANDOFF_ACTION, self.advice(),
                      "提示必须挂上既有出口 %s（Spec §3.1 第 3 条）" % HANDOFF_ACTION)

    def test_a6_message_quotes_step_and_column(self):
        block = self.advice()
        self.assertIn("' + step + '", block, "文案要带步号（Spec §3.1 第 2 条）")
        self.assertIn("' + column + '", block, "文案要带列名（Spec §3.1 第 2 条）")

    def test_a7_no_nonstandard_detection_sneaks_in(self):
        block = self.advice()
        for token in ("below_threshold", "nonstandard", "WF.matchResult", "RECOMMEND_THRESHOLD"):
            self.assertNotIn(token, block,
                             "本批不引入非标判定（Spec §3.1 第 4 条）：不许出现 %s" % token)


# --------------------------------------------------------------------------- #
# B. 空产品分支改用它（Spec §3.2）
# --------------------------------------------------------------------------- #
class BEmptyBranch(MarkupGateAdviceCase):

    def test_b1_branch_calls_the_helper(self):
        self.assertIn("markupGateAdvice(", self.empty_branch(),
                      "空产品分支必须调用 markupGateAdvice（Spec §3.2）")

    def test_b2_old_single_cause_text_is_gone(self):
        self.assertNotIn(OLD_ONE_CAUSE_TEXT, self.empty_branch(),
                         "旧文案「%s」必须从该分支移除（Spec §3.2 第 4 条）："
                         "留着等于还是单一归因" % OLD_ONE_CAUSE_TEXT)

    def test_b3_still_returns_false(self):
        self.assertIn("return false", self.empty_branch(),
                      "产品行缺失时仍然不许算价（Spec §3.2 第 1 条）")

    def test_b4_still_raises_a_visible_error(self):
        self.assertIn("addErrorBubble", self.empty_branch(),
                      "错误不许被吞掉或降级成普通提示（Spec §3.2 第 2 条）")

    def test_b5_passes_step_and_column(self):
        self.assertIn("markupGateAdvice(step, cfg.column)", self.empty_branch(),
                      "必须把 step 与 cfg.column 传进去（Spec §3.2 第 3 条）")


# --------------------------------------------------------------------------- #
# C. 动作气泡与主色（Spec §3.3）
# --------------------------------------------------------------------------- #
class CGateActionBubble(MarkupGateAdviceCase):

    def action_bubble(self) -> str:
        block = body_after(self.html, "function addGateActionBubble", 1200)
        self.assertTrue(block,
                        "找不到 addGateActionBubble(label, action)（Spec §3.3）："
                        "提示旁边没有可点的出口")
        return block

    def test_c1_exists_and_renders_a_button(self):
        self.assertIn("<button", self.action_bubble(),
                      "动作气泡必须给出一个可点按钮（Spec §3.3）")

    def test_c2_uses_primary_colour_and_no_alert(self):
        block = self.action_bubble()
        self.assertIn("var(--color-primary)", block,
                      "按钮用系统主色，与既有建议气泡一致（Spec §3.3）")
        for token in ("alert(", "confirm("):
            self.assertNotIn(token, block,
                             "不许弹 alert/confirm（Spec §3.3）：%s" % token)


# --------------------------------------------------------------------------- #
# D. 不回归护栏（Spec §4）
# --------------------------------------------------------------------------- #
class DGuards(MarkupGateAdviceCase):

    def test_d1_markup_cfg_unchanged(self):
        block = body_after(self.html, "const MARKUP_CFG", 300)
        self.assertIn("3: { column: '利润加成'", block)
        self.assertIn("4: { column: '其他加价'", block)

    def test_d2_carry_products_untouched(self):
        block = body_after(self.html, "function carryProducts", 800)
        self.assertIn("if (!rows.length) return;", block,
                      "本批不许动 carryProducts（Spec §4）：非标第 2 步承载位是 B 档的事")

    def test_d3_add_error_bubble_signature_unchanged(self):
        self.assertIn("function addErrorBubble(text)", self.html,
                      "既有 addErrorBubble 的签名与调用点不许改（Spec §4）")

    def test_d4_backend_not_touched_by_this_batch(self):
        self.assertTrue(SERVER_PY.is_file())
        server = read(SERVER_PY)
        for token in ("markupGateAdvice", "addGateActionBubble"):
            self.assertNotIn(token, server,
                             "本批不新增后端接口（Spec §4）：%s 不该出现在服务端" % token)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""红测：报价工作台按钮区新增「转技术工艺」常驻按钮。

Spec：`docs/specs/quote-tech-handoff-button.md`

现状缺口（实测，不是推断）：

  · 报价工作台聊天输入框上方的按钮区 `#quickActions` 只有四个按钮
    （`确认需求解析结果.html:794-799`：强行填满本步骤 / 进入下一大步骤 / 上一步 / 转交任务），
    **没有**任何能主动发起「新增工艺」的按钮；
  · 唯一的建议气泡 `showTechNewSuggestion()`（`:3220`）只在
    `:2381` `if (d.below_threshold)` 时出现 —— 现场那单总分 86 ≥ 阈值 70，
    入口根本不出现；
  · `:3289` 页脚「转交任务」的预选值同样只看 `below`。

「新增工艺」的能力早就存在（`cpq_wf.TASK_KIND_TECH_NEW` / `tech-task.js` /
`cpq-tech-inbox.js` / `cpq_tech_bridge.send_to_quote()`），本批只要求补一个看得见的入口。

分组（Spec §5）：A 按钮存在/位置/外观 5、B 常驻不依赖匹配结果 3、
C 禁用与登录一致 2、D 发送闭环不退化 4、E 不编造业务数据 2、F 不回归护栏 3。

纪律：
  · 全部离线、确定性、无网络：不连 Postgres、不调模型、不起服务；
  · 只读源码，不改服务器配置、不写 tech_data；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUOTE_HTML = ROOT / "确认需求解析结果.html"
SERVER_PY = ROOT / "cpq_agent_server.py"

from tech_app.backend.services import industry_templates  # noqa: E402

import cpq_wf  # noqa: E402

SERVER_NAME = "cpq_agent_server"

#: 既有的四个快捷按钮：id -> onclick（Spec §4 不许改动）。
EXISTING_QUICK_ACTIONS = {
    "qaFillStep": "fillStepRecommend()",
    "qaStep": "confirmStep()",
    "qaPrev": "goPrev()",
    "qaSend": "wfOpenSendDefault()",
}

BUTTON_RE = re.compile(r"<button\b.*?</button>", re.S)


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def body_after(src: str, marker: str, size: int = 2600) -> str:
    """取 marker 之后的 size 个字符；找不到返回空串（让断言在原文上失败）。"""
    i = src.find(marker)
    return "" if i < 0 else src[i:i + size]


def quick_actions_block(html: str) -> str:
    """`#quickActions` 容器内的静态标签。"""
    start = html.find('id="quickActions"')
    if start < 0:
        return ""
    end = html.find("</div>", start)
    return html[start:end] if end > start else html[start:start + 3000]


def tech_new_button(html: str) -> str:
    """按钮区里那个 `id="qaTechNew"` 的按钮标签；没有则返回空串。"""
    for match in BUTTON_RE.finditer(quick_actions_block(html)):
        tag = match.group(0)
        if 'id="qaTechNew"' in tag:
            return tag
    return ""


class QaTechNewButtonCase(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.html = read(QUOTE_HTML)

    def btn(self) -> str:
        tag = tech_new_button(self.html)
        self.assertTrue(
            tag,
            "报价按钮区 #quickActions 里找不到 id=\"qaTechNew\" 的按钮（Spec §2/§3.1）："
            "用户「想走技术工艺流程」时没有一个可以点的入口")
        return tag


# --------------------------------------------------------------------------- #
# A. 按钮存在、位置、外观（Spec §3.1）
# --------------------------------------------------------------------------- #
class AButtonPresence(QaTechNewButtonCase):

    def test_a1_button_sits_in_the_quick_action_row(self):
        self.assertTrue(quick_actions_block(self.html),
                        "找不到 #quickActions 容器（Spec §3.1）")
        self.btn()

    def test_a2_click_opens_the_tech_new_dialog_verbatim(self):
        self.assertIn(
            "wfOpenSend(false, 'tech_new_product')", self.btn(),
            "onclick 必须逐字复用既有 wfOpenSend(false, 'tech_new_product')（Spec §3.2）："
            "不许新增发送逻辑、不许另起后端接口")

    def test_a3_looks_like_its_siblings(self):
        tag = self.btn()
        self.assertIn('class="quick-action-btn"', tag,
                      "按钮必须与同排按钮同款（Spec §3.1）")
        self.assertTrue(re.search(r'<i class="ti ti-[a-z0-9-]+"></i>', tag),
                        "按钮要带 Tabler 图标，与同排按钮一致（Spec §3.1）")

    def test_a4_label_is_business_readable(self):
        tag = self.btn()
        self.assertIn("转技术工艺", tag,
                      "文案固定为「转技术工艺」（Spec §3.1）")

    def test_a5_id_is_unique(self):
        self.assertEqual(
            self.html.count('id="qaTechNew"'), 1,
            "qaTechNew 必须全页唯一（Spec §3.1）")


# --------------------------------------------------------------------------- #
# B. 常驻、不依赖匹配结果（Spec §3.3）
# --------------------------------------------------------------------------- #
class BAlwaysAvailable(QaTechNewButtonCase):

    def test_b1_not_gated_by_the_score_threshold(self):
        self.assertNotIn(
            "below_threshold", self.btn(),
            "按钮不得依赖 below_threshold（Spec §3.3）：现场那单总分 86 ≥ 70，"
            "按阈值判断永远看不到入口")

    def test_b2_not_gated_by_the_match_result(self):
        tag = self.btn()
        self.assertNotIn("nonstandard", tag,
                         "按钮不得依赖 nonstandard 判定（Spec §3.3）")
        self.assertNotIn("WF.matchResult", tag,
                         "按钮不得依赖 WF.matchResult（Spec §3.3）")

    def test_b3_button_is_not_pre_disabled(self):
        self.assertNotIn(
            "disabled", self.btn(),
            "按钮标签里不许写 disabled（Spec §3.3）：禁用统一由 setBusy() 按登录态管")


# --------------------------------------------------------------------------- #
# C. 禁用与登录一致（Spec §3.3）
# --------------------------------------------------------------------------- #
class CLoginGate(QaTechNewButtonCase):

    def _setbusy(self) -> str:
        block = body_after(self.html, "function setBusy", 1800)
        self.assertTrue(block, "定位不到 setBusy（Spec §3.3）")
        return block

    def test_c1_disabled_together_with_the_other_quick_actions(self):
        block = self._setbusy()
        self.assertIn("qaSend", block, "先在原文里定位既有快捷按钮的禁用写法")
        self.assertIn(
            "qaTechNew", block,
            "setBusy 必须把 qaTechNew 与其它快捷按钮一起禁用（Spec §3.3）")

    def test_c2_uses_the_same_lock_including_not_logged_in(self):
        lines = [ln for ln in self._setbusy().splitlines() if "qaTechNew" in ln]
        self.assertTrue(lines, "setBusy 里没有 qaTechNew 的禁用语句（Spec §3.3）")
        self.assertTrue(
            any("lock" in ln for ln in lines),
            "必须用同一个 lock（busy || GATE_BLOCKED）禁用（Spec §3.3）："
            "未登录时不能绕过登录直接发任务")


# --------------------------------------------------------------------------- #
# D. 发送闭环不退化（Spec §3.4）
# --------------------------------------------------------------------------- #
class DSendLoop(QaTechNewButtonCase):

    def test_d1_dialog_still_offers_tech_new_product(self):
        self.assertIn('<option value="tech_new_product">', self.html,
                      "转交弹窗的「新增工艺」选项不许被删（Spec §3.4）")
        self.assertIn("请工艺经理到技术工艺新建产品", self.html,
                      "选项文案要说明落到技术工艺新建产品（Spec §3.4）")

    def test_d2_default_role_is_the_process_manager(self):
        block = body_after(self.html, "const suggestRole", 200)
        self.assertTrue(block, "定位不到 suggestRole")
        self.assertIn("TECH_NEW_ROLE", block,
                      "「新增工艺」的建议收件角色必须是工艺经理（Spec §3.4）")

    def test_d3_user_bubble_then_ai_receipt(self):
        self.assertIn("已发起「新增工艺」任务", self.html,
                      "发送成功后必须有明确的 AI 回执（Spec §3.4）")
        block = body_after(self.html, "$('wfSend').onclick", 3000)
        self.assertTrue(block, "定位不到 wfSend 的发送处理")
        self.assertIn("beginUserTurn", block,
                      "发送是用户主动触发的回合，必须先出用户气泡（Spec §3.4）")

    def test_d4_quote_card_stays_on_the_current_step(self):
        self.assertIn("仍停在第", self.html,
                      "回执必须说明报价卡片停在原步骤（Spec §3.4）")


# --------------------------------------------------------------------------- #
# E. 绝不编造业务数据（Spec §3.5）
# --------------------------------------------------------------------------- #
class ENoFabricatedData(QaTechNewButtonCase):

    def _send_dialog(self) -> str:
        block = body_after(self.html, "async function wfOpenSend", 11000)
        self.assertTrue(block, "定位不到 wfOpenSend（Spec §3.5）")
        return block

    def test_e1_does_not_touch_the_product_sections(self):
        block = self._send_dialog()
        for token in ("s1_products", "s2_products", "_techparams"):
            self.assertNotIn(
                token, block,
                "「转技术工艺」不得写入 %s（Spec §3.5）：非标定制没有成品编码，"
                "塞假产品行是明确禁止的" % token)

    def test_e2_does_not_render_fake_rows(self):
        block = self._send_dialog()
        for token in ("render_table", "render_form"):
            self.assertNotIn(token, block,
                             "该路径不得调用 %s 造数据（Spec §3.5）" % token)


# --------------------------------------------------------------------------- #
# F. 不回归护栏（Spec §4）
# --------------------------------------------------------------------------- #
class FGuards(QaTechNewButtonCase):

    def test_f1_existing_quick_actions_unchanged(self):
        block = quick_actions_block(self.html)
        for btn_id, onclick in EXISTING_QUICK_ACTIONS.items():
            self.assertIn('id="%s"' % btn_id, block,
                          "既有快捷按钮 %s 不许被删或改名（Spec §4）" % btn_id)
            self.assertIn(onclick, block,
                          "既有快捷按钮 %s 的 onclick 不许改（Spec §4）" % btn_id)

    def test_f2_task_kind_and_default_role_unchanged(self):
        self.assertEqual(cpq_wf.TASK_KIND_TECH_NEW, "tech_new_product")
        self.assertEqual(cpq_wf._KIND_DEFAULT_ROLE[cpq_wf.TASK_KIND_TECH_NEW], "process_mgr")
        self.assertIn(cpq_wf.TASK_KIND_TECH_NEW, cpq_wf.SIDE_TASK_KINDS)

    def test_f3_industry_gates_unchanged(self):
        self.assertTrue(SERVER_PY.is_file())
        try:
            server = importlib.import_module(SERVER_NAME)
        except Exception as exc:                       # noqa: BLE001 - 红测要原文
            raise AssertionError("无法导入 %s：%s: %s" % (SERVER_NAME, type(exc).__name__, exc))
        three = (("max_dimension", "尺寸"),
                 ("application_scope", "应用范围/使用场景"),
                 ("operating_temperature", "工作温度"))
        for industry in ("semiconductor", "battery", "appliance"):
            self.assertEqual(tuple(server.step1_required(industry)), three,
                             "%s 门禁必须逐字不变（Spec §4）" % industry)
        self.assertEqual(
            {key for key, _label in server.step1_required("packaging")},
            set(industry_templates.required_keys("packaging")),
            "包装门禁必须恰好等于 industry_templates.required_keys('packaging')（Spec §4）")


if __name__ == "__main__":
    unittest.main(verbosity=2)

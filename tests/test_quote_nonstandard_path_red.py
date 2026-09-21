"""红测：非标路径治理（判定 / 只读定制要求 / 允许继续 / 回写待确认 / 流程状态只读）。

Spec：`docs/specs/quote-nonstandard-path.md`

现状缺口（实测，不是推断）：

  · `cpq_match.py:293` 唯一的非标信号是 `below = best < RECOMMEND_THRESHOLD`（70）。
    现场那单「用途/场景契合」只有 30 分，但总分 86 ≥ 70 → `below_threshold=False`，
    维度塌陷被其余五项满分掩盖；返回值里也没有任何结构化非标字段。
  · 第 2 步只有「沿用第 1 步产品信息/技术参数」一套契约
    （`确认需求解析结果.html:3492` `CARRY_MAP`、`:3508` `carryProducts` 的
    `if (!rows.length) return;`）—— 非标必然两张表 0 条，也没有「定制技术要求」承载位；
    `grep -c s2_custom_spec` 在服务端与页面都是 0。
  · `cpq_tech_bridge.py:962` payload 只有 `"tech_result": result`，没有待确认标记；
    页面里 `grep -c tech_result` → 0。
  · `_enforce_fixed_template()`（`cpq_agent_server.py:361`）只做字段裁剪，不区分业务值
    与流程状态；`s1_basic` 的 45 个字段里含「测算状态」（已实测），AI 可以照填。

业务拍板（原话）：1 加维度下限 → 按建议；2 要人工填就不做、不加多余步骤才做 → 只读汇总；
3 可以继续走 POC 无所谓；4 提示销售确认；6 流程状态系统改、AI 只读。

分组（Spec §4）：A 非标判定 6 / B 只读定制要求 5 / C 允许继续不新增门禁 3 /
D 回写待确认 5 / E 流程状态只读 3 / F 不回归护栏 3。

纪律：
  · 全部离线、确定性、无网络：不连 Postgres、不调模型、不起服务；
  · 只读源码与纯函数，不改服务器配置、不写业务数据；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import importlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUOTE_HTML = ROOT / "确认需求解析结果.html"
SERVER_PY = ROOT / "cpq_agent_server.py"
MATCH_PY = ROOT / "cpq_match.py"
BRIDGE_PY = ROOT / "cpq_tech_bridge.py"

from tech_app.backend.services import industry_templates  # noqa: E402

import cpq_match  # noqa: E402
import cpq_wf  # noqa: E402

SERVER_NAME = "cpq_agent_server"

# —— 现场会话的确定性复现：总分 86 ≥ 70，但用途维度只有 30 分（与截图逐字一致）——
PPV_COLS = [
    "product_item_code", "product_item_name", "max_dimension", "application_scope",
    "operating_temperature", "service_life", "hermeticity", "rated_voltage",
    "rated_capacity", "max_continuous_current", "weight",
]
REQ = {"max_dimension": "100*90*40",
       "application_scope": "数码产品包装盒/天地盒（耳机盒）",
       "operating_temperature": "常温"}
#: 总分低于阈值的那种需求（尺寸远超限 + 用途不匹配 + 温度无交集）
REQ_LOW = {"max_dimension": "100*90*40",
           "application_scope": "数码产品包装盒/天地盒（耳机盒）",
           "operating_temperature": "0~60"}

REQUIRED_GATE = (("max_dimension", "尺寸"),
                 ("application_scope", "应用范围/使用场景"),
                 ("operating_temperature", "工作温度"))

HANDOFF_ACTION = "wfOpenSend(false, 'tech_new_product')"


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def body_after(src: str, marker: str, size: int = 2600) -> str:
    i = src.find(marker)
    return "" if i < 0 else src[i:i + size]


def battery_row(code, dims, scope, temp):
    return [code, "锂亚电池F0041P-LF", dims, scope, temp, "10000", "IP67",
            "3.6V", "1200mAh", "50mA", "9g"]


WRONG_SCOPE = battery_row("91000226", "50*40*40", "追踪设备", "-40~85")
RIGHT_SCOPE = battery_row("91000001", "50*40*40", "数码产品包装盒", "-40~85")
LOW_TOTAL = battery_row("91000999", "400*400*400", "追踪设备", "-100~-40")


class ServerCase(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.html = read(QUOTE_HTML)
        cls.server_src = read(SERVER_PY)
        try:
            cls.server = importlib.import_module(SERVER_NAME)
        except Exception as exc:                       # noqa: BLE001 - 红测要原文
            raise AssertionError("无法导入 %s：%s: %s" % (SERVER_NAME, type(exc).__name__, exc))


# --------------------------------------------------------------------------- #
# A. 非标判定（Spec §2.1）
# --------------------------------------------------------------------------- #
class ANonstandard(unittest.TestCase):
    maxDiff = None

    def _match(self, row, req=None):
        return cpq_match.match(dict(req or REQ), top_n=3, data=(PPV_COLS, [row]))

    def test_a1_dim_floor_config_exists(self):
        self.assertTrue(hasattr(cpq_match, "NONSTANDARD_DIM_FLOORS"),
                        "cpq_match 必须导出 NONSTANDARD_DIM_FLOORS（Spec §2.1）")
        floors = cpq_match.NONSTANDARD_DIM_FLOORS
        self.assertIsInstance(floors, dict)
        self.assertIn("scope", floors, "用途/场景契合必须能配置维度下限（Spec §2.1）")
        self.assertTrue(0 < float(floors["scope"]) <= 100)

    def test_a2_real_session_86_points_is_nonstandard(self):
        res = self._match(WRONG_SCOPE)
        self.assertEqual(res["products"][0]["total"], 86.0, "先钉住现场复现（Spec §1）")
        self.assertFalse(res["below_threshold"], "86 分高于 70 阈值，这正是缺口")
        self.assertIn("nonstandard", res, "match() 必须返回 nonstandard（Spec §2.1）")
        self.assertTrue(res["nonstandard"]["triggered"],
                        "总分 86 但用途维度 30 分，必须判非标（Spec §2.1 规则 2）")

    def test_a3_reason_names_the_collapsed_dimension(self):
        res = self._match(WRONG_SCOPE)
        reasons = {r["key"]: r for r in res["nonstandard"]["reasons"]}
        self.assertIn("scope", reasons, "reasons 必须点名塌陷的维度（Spec §2.1）")
        reason = reasons["scope"]
        self.assertEqual(float(reason["actual"]), 30.0)
        self.assertEqual(float(reason["threshold"]),
                         float(cpq_match.NONSTANDARD_DIM_FLOORS["scope"]))
        for key in ("label", "reason"):
            self.assertTrue(str(reason.get(key) or "").strip())

    def test_a4_low_total_also_produces_a_total_reason(self):
        low = self._match(LOW_TOTAL, REQ_LOW)
        self.assertTrue(low["below_threshold"], "先复现既有行为：总分低于阈值")
        self.assertTrue(low["nonstandard"]["triggered"])
        self.assertIn("total", {r["key"] for r in low["nonstandard"]["reasons"]},
                      "低于阈值这条既有规则也要成为一条 reason（key=total，Spec §2.1）")
        self.assertFalse(self._match(RIGHT_SCOPE)["nonstandard"]["triggered"],
                         "各项达标时不得误判非标（Spec §2.1）")

    def test_a5_suggests_the_tech_new_task(self):
        res = self._match(WRONG_SCOPE)
        self.assertEqual(res["nonstandard"]["suggested_task_kind"],
                         cpq_wf.TASK_KIND_TECH_NEW,
                         "非标必须直接给出可执行任务类型（Spec §2.1）")
        self.assertEqual(self._match(RIGHT_SCOPE)["nonstandard"]["suggested_task_kind"], "")

    def test_a6_existing_contract_keys_unchanged(self):
        res = self._match(WRONG_SCOPE)
        for key in ("ok", "source", "products", "all_count", "pool_count",
                    "threshold", "below_threshold", "advice", "weights"):
            self.assertIn(key, res)
        self.assertEqual(res["threshold"], 70.0, "RECOMMEND_THRESHOLD 不许改（Spec §3）")


# --------------------------------------------------------------------------- #
# B. 只读定制要求汇总（Spec §2.2）
# --------------------------------------------------------------------------- #
class BReadonlyCustomSpec(ServerCase):

    def section(self):
        sections = getattr(self.server, "_BI_SECTIONS", None)
        self.assertIsInstance(sections, dict)
        self.assertIn("s2_custom_spec", sections,
                      "第 2 步必须新增 s2_custom_spec 分区（Spec §2.2）")
        return sections["s2_custom_spec"]

    def test_b1_section_shape(self):
        spec = self.section()
        self.assertEqual(spec[0], "form", "定制技术要求是表单（Spec §2.2）")
        self.assertIn("定制技术要求", spec[1])

    def test_b2_section_is_readonly(self):
        self.assertIs(self.section()[3], False,
                      "该分区必须只读（Spec §2.2）：要人工填就不做，不加多余步骤")

    def test_b3_fallback_fields_give_the_content(self):
        fallback = getattr(self.server, "_FALLBACK_FIELDS", None)
        self.assertTrue(fallback, "_FALLBACK_FIELDS 必须给出兜底字段（Spec §2.2）")
        text = repr(fallback)
        for token in ("尺寸", "纸张", "板材", "数量"):
            self.assertIn(token, text,
                          "兜底字段要覆盖 %s（Spec §2.2）" % token)

    def test_b4_frontend_knows_the_section(self):
        self.assertTrue("s2_custom_spec" in self.html,
                        "前端必须渲染 s2_custom_spec（Spec §2.2）")

    def test_b5_carried_automatically_not_by_hand(self):
        block = body_after(self.html, "function carryProducts", 1600)
        self.assertTrue(block, "定位不到 carryProducts")
        self.assertIn("s2_custom_spec", block,
                      "非标时必须在 carryProducts 里自动带出该分区（Spec §2.2）："
                      "来源为空不能再静默 return")


# --------------------------------------------------------------------------- #
# C. 允许继续、不新增门禁（Spec §2.3）
# --------------------------------------------------------------------------- #
class CAllowContinue(ServerCase):

    def test_c1_confirm_step_has_no_product_gate(self):
        block = body_after(self.html, "async function confirmStep", 2000)
        self.assertTrue(block, "定位不到 confirmStep")
        self.assertNotIn("s1_products", block,
                         "confirmStep 不得因缺产品行而早退（Spec §2.3）："
                         "业务拍板 POC 阶段可以继续走")

    def test_c2_next_button_not_gated_by_products(self):
        bad = [ln for ln in self.html.splitlines()
               if "btnNext" in ln and "products" in ln]
        self.assertFalse(bad,
                         "btnNext 的禁用不许新增「没有产品行」条件（Spec §2.3）：%r" % bad[:2])

    def test_c3_markup_cfg_and_failure_still_intact(self):
        block = body_after(self.html, "const MARKUP_CFG", 300)
        self.assertIn("3: { column: '利润加成'", block)
        self.assertIn("4: { column: '其他加价'", block)
        self.assertIn("return false", body_after(self.html, "if (!products.length) {", 420))


# --------------------------------------------------------------------------- #
# D. 回写待确认（Spec §2.4）
# --------------------------------------------------------------------------- #
class DHandoffNeedsConfirmation(ServerCase):

    def bridge(self) -> str:
        return read(BRIDGE_PY)

    def test_d1_payload_is_marked_needs_confirmation(self):
        block = body_after(self.bridge(), "def send_to_quote", 12000)
        self.assertTrue(block, "定位不到 send_to_quote")
        self.assertIn("tech_result", block, "先钉住既有 payload 字段")
        self.assertIn("needs_confirmation", block,
                      "回写 payload 必须标记待确认（Spec §2.4）："
                      "否则下游分不清「可直接用」和「等销售确认」")

    def test_d2_bridge_never_writes_product_rows(self):
        text = self.bridge()
        for token in ("s1_products", "s1_techparams"):
            self.assertNotIn(token, text,
                             "回写路径不得直接写 %s（Spec §2.4）" % token)

    def test_d3_frontend_handles_the_handoff_payload(self):
        self.assertTrue("tech_result" in self.html,
                        "报价工作台必须处理工艺回传结果（Spec §2.4）")

    def test_d4_frontend_has_a_confirm_action(self):
        self.assertTrue("applyTechResult" in self.html,
                        "必须有「确认写入」动作 applyTechResult（Spec §2.4）："
                        "价格只提示销售确认，不自动替换")

    def test_d5_writes_are_centralised_in_the_confirm_action(self):
        block = body_after(self.html, "function applyTechResult", 3000)
        self.assertTrue(block, "定位不到 applyTechResult（Spec §2.4）")
        for token in ("s1_products", "s1_techparams"):
            self.assertIn(token, block,
                          "写入必须集中在确认动作里（Spec §2.4）：%s" % token)


# --------------------------------------------------------------------------- #
# E. 流程状态只读（Spec §2.5）
# --------------------------------------------------------------------------- #
class EReadonlyWorkflowFields(ServerCase):

    def test_e1_readonly_field_list_exists(self):
        fields = getattr(self.server, "READONLY_FIELDS", None)
        self.assertIsNotNone(fields, "必须导出 READONLY_FIELDS（Spec §2.5）")
        self.assertIsInstance(fields, (tuple, list))
        self.assertIn("测算状态", fields,
                      "「测算状态」这类流程状态必须只在只读清单里（Spec §2.5）")

    def test_e2_model_value_is_dropped_and_field_marked_readonly(self):
        out = self.server._enforce_fixed_template({
            "action": "render_form", "section_id": "s1_basic",
            "values": {"测算状态": "转定制评估", "客户": "XX2"},
        })
        fields = {f["key"]: f for f in out.get("fields", [])}
        self.assertIn("测算状态", fields,
                      "s1_basic 里必须有「测算状态」字段（先钉住既有模板）")
        state = fields["测算状态"]
        self.assertTrue(state.get("readonly") is True,
                        "流程状态字段必须标 readonly（Spec §2.5）")
        self.assertEqual(state.get("value"), "",
                         "模型给的状态值必须被丢弃（Spec §2.5）：只让系统改")
        self.assertEqual(fields["客户"].get("value"), "XX2",
                         "普通业务字段不受影响（Spec §2.5）")

    def test_e3_prompt_uses_the_same_list(self):
        self.assertGreaterEqual(
            read(SERVER_PY).count("READONLY_FIELDS"), 2,
            "提示词必须引用同一份只读清单（Spec §2.5）：定义之外还要在提示里出现一次，"
            "不许再抄一份中文")


# --------------------------------------------------------------------------- #
# F. 不回归护栏（Spec §3）
# --------------------------------------------------------------------------- #
class FGuards(ServerCase):

    def test_f1_gates_unchanged(self):
        for industry in ("semiconductor", "battery", "appliance"):
            self.assertEqual(tuple(self.server.step1_required(industry)), REQUIRED_GATE,
                             "%s 门禁必须逐字不变（Spec §3）" % industry)
        self.assertEqual({k for k, _l in self.server.step1_required("packaging")},
                         set(industry_templates.required_keys("packaging")))

    def test_f2_match_module_has_no_new_dependencies(self):
        stdlib = set(getattr(sys, "stdlib_module_names", ()))
        local = {p.stem for p in ROOT.glob("cpq_*.py")}
        tree = ast.parse(read(MATCH_PY))
        imported = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        extra = {n for n in imported if n not in stdlib and n not in local}
        self.assertFalse(extra, "cpq_match.py 不许引入新依赖（Spec §3）：%s" % sorted(extra))

    def test_f3_task_kind_unchanged(self):
        self.assertEqual(cpq_wf.TASK_KIND_TECH_NEW, "tech_new_product")
        self.assertEqual(cpq_wf._KIND_DEFAULT_ROLE[cpq_wf.TASK_KIND_TECH_NEW], "process_mgr")
        self.assertIn(HANDOFF_ACTION, self.html,
                      "A 档的「转技术工艺」出口必须还在（Spec §2.2/§2.5）")


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""红测：权威费率的导入路径（demo 退役 + 校验 + 预演）—— 逆向快速报价第 13 批。

Spec：`docs/specs/quick-quote-13-authoritative-rate-import.md`
依赖：批 3（`cpq_quick_quote_workspace.py` 的费率表 / `FIELD_KEYS` / `RULE_KINDS`）、
批 8（`rule_authority()` / `authority_summary()` / `cpq_quick_quote_price.is_formal()`）。

现状缺口（2026-09-21 实测，不是推断）：
  · `cpq_kb.kb_quick_quote_delta_rule` 4 条费率全是 `source_type=demo` / `review_status=draft`
    → `authority_summary()["authoritative"] = False` → `is_formal()` 永远为假，
    **正式快速报价在当前数据下不可达**；
  · `cpq_quick_quote_workspace.py` 只有读与判定（`load_rules()` / `rule_authority()` /
    `authority_summary()`），没有 `rate_import_plan()` 这类写前预演与校验；
  · `scripts/` 下没有费率导入工具（案例侧有 `import_dwg_quick_quote_cases.py`，费率侧没有）；
  · 4 条 demo 行即使换上权威口径也不会自动退场，`authority_summary()` 会继续判 False。

纪律：
  · 全部离线：费率行一律 fixture 注入（`existing=`），不连 PG、不写业务数据；
  · 工具只跑 `--help`，不真导入；写入纪律用源码断言；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WS_PY = ROOT / "cpq_quick_quote_workspace.py"
TOOL_PY = ROOT / "scripts" / "import_quick_quote_rates.py"
DEPLOY_MD = ROOT / "DEPLOYMENT.md"
MODULE_NAME = "cpq_quick_quote_workspace"
PYTHON = sys.executable

EXPECTED_REQUIRED_KEYS = ("rule_code", "field_key", "rule_kind", "unit", "industry",
                          "source_type", "review_status", "version",
                          "effective_from", "source_ref")
EXPECTED_REASONS = ("missing_key", "source_not_authoritative", "not_reviewed",
                    "invalid_value", "duplicate_rule_code", "unknown_field_key",
                    "unknown_rule_kind", "industry_mismatch")

#: 库里现存的 4 条演示费率（字段逐字对齐 2026-09-21 实测行）。
DEMO_RULES = (
    {"rule_code": "QQQ-DEMO-HOTSTEP", "field_key": "hot_stamping", "rule_kind": "step",
     "unit": "元", "industry": "packaging", "source_type": "demo", "review_status": "draft",
     "version": "1", "effective_from": "2026-01-01", "effective_to": "", "rate": None,
     "breakpoints_json": None, "source_ref": "批 3 示例费率"},
    {"rule_code": "QQQ-DEMO-LEN-RATE", "field_key": "inner_length", "rule_kind": "rate",
     "unit": "元/mm", "industry": "packaging", "source_type": "demo", "review_status": "draft",
     "version": "1", "effective_from": "2026-01-01", "effective_to": "", "rate": 0.012,
     "breakpoints_json": None, "source_ref": "批 3 示例费率"},
    {"rule_code": "QQQ-DEMO-PAPER-RATE", "field_key": "face_paper_gsm", "rule_kind": "rate",
     "unit": "元/g/m²", "industry": "packaging", "source_type": "demo", "review_status": "draft",
     "version": "1", "effective_from": "2026-01-01", "effective_to": "", "rate": 0.0062,
     "breakpoints_json": None, "source_ref": "批 3 示例费率"},
    {"rule_code": "QQQ-DEMO-QTY-BAND", "field_key": "quantity", "rule_kind": "band",
     "unit": "元", "industry": "packaging", "source_type": "demo", "review_status": "draft",
     "version": "1", "effective_from": "2026-01-01", "effective_to": "",
     "rate": None, "breakpoints_json": "[[0,1.15],[1000,1.00],[3000,0.95],[6000,0.92],[10000,0.85]]",
     "source_ref": "批 3 示例费率"},
)

WORKBOOK_REF = "成本测算明细.xlsx#费率表"


def workbook_row(**over):
    """一条**合格**的权威费率行（改一个键就能造出各种不通过态）。"""
    row = {"rule_code": "QQQ-WB-LEN-RATE", "field_key": "inner_length", "rule_kind": "rate",
           "unit": "元/mm", "industry": "packaging", "source_type": "workbook",
           "review_status": "reviewed", "version": 2, "effective_from": "2026-09-01",
           "effective_to": "", "rate": 0.0135, "breakpoints_json": None,
           "source_ref": WORKBOOK_REF}
    row.update(over)
    return row


def authoritative_rows():
    """覆盖 4 条 demo 行的 field_key 的一套权威行（用于 §2.5 验收口径）。"""
    return [
        workbook_row(rule_code="QQQ-WB-HOTSTEP", field_key="hot_stamping", rule_kind="step",
                     unit="元", rate=None, amount=1.2, version=2),
        workbook_row(rule_code="QQQ-WB-LEN-RATE", field_key="inner_length", rule_kind="rate",
                     unit="元/mm", rate=0.0135),
        workbook_row(rule_code="QQQ-WB-PAPER-RATE", field_key="face_paper_gsm", rule_kind="rate",
                     unit="元/g/m²", rate=0.0071),
        workbook_row(rule_code="QQQ-WB-QTY-BAND", field_key="quantity", rule_kind="band",
                     unit="元", rate=None, version=2,
                     breakpoints_json="[[0,1.12],[3000,0.96],[6000,0.93],[10000,0.88]]"),
    ]


def load_module():
    return importlib.import_module(MODULE_NAME)


class Base(unittest.TestCase):
    def module(self):
        try:
            return load_module()
        except ImportError:                                        # pragma: no cover
            self.fail("%s.py 不存在" % MODULE_NAME)

    def attr(self, name, hint):
        module = self.module()
        self.assertTrue(hasattr(module, name), "%s.%s 缺失（%s）" % (MODULE_NAME, name, hint))
        return getattr(module, name)

    def plan(self, rows, **kw):
        module = self.module()
        fn = getattr(module, "rate_import_plan", None)
        if not callable(fn):
            self.fail("%s.rate_import_plan() 缺失（Spec 批 13 §2.2）" % MODULE_NAME)
        kw.setdefault("existing", list(DEMO_RULES))
        return fn(rows, **kw)

    def codes(self, plan):
        return [row.get("reason_code") for row in plan.get("blocked") or []]

    def single(self, row):
        """一行进、取那行的 blocked（没有 blocked 时返回 None）。"""
        blocked = self.plan([row]).get("blocked") or []
        return blocked[0] if blocked else None


# --------------------------------------------------------------------------- #
# A 组：命名契约
# --------------------------------------------------------------------------- #
class TestANaming(Base):
    def test_a1_required_keys(self):
        self.assertEqual(EXPECTED_REQUIRED_KEYS, tuple(self.attr("RATE_IMPORT_REQUIRED_KEYS",
                                                                 "Spec 批 13 §2.1")))

    def test_a2_import_source_is_the_authoritative_one(self):
        module = self.module()
        self.assertEqual("workbook", self.attr("AUTHORITATIVE_IMPORT_SOURCE", "Spec 批 13 §2.1"))
        self.assertIn("workbook", tuple(module.AUTHORITATIVE_RATE_SOURCES),
                      "导入通道只认权威来源，必须与既有闭集同值")

    def test_a3_reason_codes_and_labels(self):
        module = self.module()
        self.assertEqual(EXPECTED_REASONS, tuple(self.attr("RATE_IMPORT_REASONS",
                                                           "Spec 批 13 §2.1")))
        labels = self.attr("RATE_IMPORT_LABELS", "Spec 批 13 §2.1")
        self.assertEqual(set(EXPECTED_REASONS), set(labels),
                         "每个结果码都必须有中文标签（前端与工具不许各写一份文案）")
        for code, text in labels.items():
            self.assertTrue(str(text).strip(), "%s 的标签不许为空" % code)


# --------------------------------------------------------------------------- #
# B 组：逐条校验
# --------------------------------------------------------------------------- #
class TestBValidation(Base):
    def test_b1_missing_key_lists_every_missing_key(self):
        row = workbook_row()
        for key in ("unit", "source_ref"):
            row.pop(key)
        blocked = self.single(row)
        self.assertIsNotNone(blocked, "缺必填键必须 blocked（Spec §2.2 第 2 条）")
        self.assertEqual("missing_key", blocked.get("reason_code"))
        detail = blocked.get("detail") or ""
        self.assertIn("unit", detail)
        self.assertIn("source_ref", detail)

    def test_b2_demo_source_is_rejected(self):
        blocked = self.single(workbook_row(source_type="demo"))
        self.assertEqual("source_not_authoritative", blocked.get("reason_code"))

    def test_b3_unreviewed_row_is_rejected(self):
        blocked = self.single(workbook_row(review_status="draft"))
        self.assertEqual("not_reviewed", blocked.get("reason_code"))

    def test_b4_other_industry_is_rejected(self):
        blocked = self.single(workbook_row(industry="semiconductor"))
        self.assertEqual("industry_mismatch", blocked.get("reason_code"))

    def test_b5_unknown_field_key(self):
        blocked = self.single(workbook_row(field_key="no_such_field"))
        self.assertEqual("unknown_field_key", blocked.get("reason_code"))

    def test_b6_unknown_rule_kind(self):
        blocked = self.single(workbook_row(rule_kind="magic"))
        self.assertEqual("unknown_rule_kind", blocked.get("reason_code"))

    def test_b7_invalid_values(self):
        cases = {
            "version 不是正整数": workbook_row(version=0),
            "effective_from 不是 ISO 日期": workbook_row(effective_from="2026/09/01"),
            "effective_to 早于 effective_from": workbook_row(effective_from="2026-09-01",
                                                              effective_to="2026-08-01"),
            "rate 不是正数": workbook_row(rate=0),
            "band 的 breakpoints_json 不合法": workbook_row(rule_kind="band", rate=None,
                                                            breakpoints_json="not-json"),
        }
        for hint, row in cases.items():
            blocked = self.single(row)
            self.assertIsNotNone(blocked, "%s 必须 blocked（Spec §2.2 第 2 条）" % hint)
            self.assertEqual("invalid_value", blocked.get("reason_code"), hint)

    def test_b8_duplicate_rule_code(self):
        twice = [workbook_row(), workbook_row()]
        plan = self.plan(twice)
        self.assertIn("duplicate_rule_code", self.codes(plan),
                      "同一批里同 rule_code 必须 blocked")
        authoritative_existing = list(DEMO_RULES) + [
            dict(workbook_row(rule_code="QQQ-WB-LEN-RATE"))]
        plan = self.plan([workbook_row(rule_code="QQQ-WB-LEN-RATE")],
                         existing=authoritative_existing)
        self.assertIn("duplicate_rule_code", self.codes(plan),
                      "与库里**已权威**的行同码必须 blocked（不许悄悄覆盖权威行）")


# --------------------------------------------------------------------------- #
# C 组：演示行退役
# --------------------------------------------------------------------------- #
class TestBRetire(Base):
    def test_c1_demo_rows_retire_by_default(self):
        plan = self.plan(authoritative_rows())
        retired = sorted(row.get("rule_code") for row in plan.get("retire") or [])
        self.assertEqual(sorted(row["rule_code"] for row in DEMO_RULES), retired,
                         "缺省必须把 4 条 demo 行退役（Spec §2.2 第 3 条）")
        for row in plan.get("retire") or []:
            self.assertEqual("demo_rate", row.get("reason_code"))
            self.assertIn(row.get("rule_code"), [r["rule_code"] for r in DEMO_RULES])

    def test_c2_keep_demo_switch(self):
        plan = self.plan(authoritative_rows(), retire_demo=False)
        self.assertEqual([], list(plan.get("retire") or []))

    def test_c3_replacing_a_demo_code_is_a_replacement_not_a_duplicate(self):
        row = workbook_row(rule_code="QQQ-DEMO-QTY-BAND", field_key="quantity",
                           rule_kind="band", rate=None,
                           breakpoints_json="[[0,1.12],[3000,0.96]]")
        plan = self.plan([row])
        self.assertEqual([], list(plan.get("blocked") or []),
                         "用权威行顶掉同码 demo 行是替换，不该报重复")
        retired = [item.get("rule_code") for item in plan.get("retire") or []]
        self.assertNotIn("QQQ-DEMO-QTY-BAND", retired)
        projected = {r["rule_code"]: r for r in plan.get("projected") or []}
        self.assertEqual("workbook", projected["QQQ-DEMO-QTY-BAND"].get("source_type"))


# --------------------------------------------------------------------------- #
# D 组：projected 与「正式报价可达」
# --------------------------------------------------------------------------- #
class TestDProjectedAuthority(Base):
    def test_d1_projected_is_existing_minus_retire_plus_write(self):
        plan = self.plan(authoritative_rows())
        projected = plan.get("projected") or []
        codes = [row.get("rule_code") for row in projected]
        self.assertEqual(sorted(codes), codes, "projected 必须按 rule_code 升序")
        self.assertEqual(sorted(row["rule_code"] for row in authoritative_rows()), sorted(codes),
                         "4 条 demo 全部退役后，projected 只剩本次写入的权威行")

    def test_d2_authoritative_matches_authority_summary(self):
        module = self.module()
        plan = self.plan(authoritative_rows())
        self.assertEqual([], list(plan.get("blocked") or []))
        self.assertTrue(plan.get("authoritative"),
                        "计划执行后必须判为权威（Spec §2.5）")
        summary = module.authority_summary(plan.get("projected"))
        self.assertTrue(summary.get("authoritative"))
        self.assertEqual([], list(summary.get("blocked_by") or []))
        self.assertEqual(plan.get("authoritative"), summary.get("authoritative"),
                         "plan['authoritative'] 必须与 authority_summary() 同值")

    def test_d3_formal_quote_becomes_reachable(self):
        module = self.module()
        plan = self.plan(authoritative_rows())
        summary = module.authority_summary(plan.get("projected"))
        price = importlib.import_module("cpq_quick_quote_price")
        quote = {"quote_id": "T-1", "warnings": [],
                 "rate_authority": {"authoritative": summary.get("authoritative"),
                                    "authoritative_total": summary.get("authoritative_total")}}
        self.assertTrue(price.is_formal(quote),
                        "权威费率下必须能出正式报价（Spec §2.5）——今天是 False")

    def test_d4_keep_demo_still_reports_non_authoritative(self):
        plan = self.plan(authoritative_rows(), retire_demo=False)
        projected_codes = [r.get("rule_code") for r in plan.get("projected") or []]
        self.assertTrue(set(r["rule_code"] for r in DEMO_RULES).issubset(set(projected_codes)),
                        "keep-demo 时演示行仍在 projected 里")
        self.assertFalse(plan.get("authoritative"),
                         "演示行没退役就不能判权威（Spec §2.2 第 5 条）")


# --------------------------------------------------------------------------- #
# E 组：导入工具
# --------------------------------------------------------------------------- #
class TestETool(Base):
    def test_e1_tool_exists(self):
        self.assertTrue(TOOL_PY.exists(),
                        "缺 scripts/import_quick_quote_rates.py（Spec 批 13 §2.3）")

    def test_e2_help_exposes_flags(self):
        proc = subprocess.run([PYTHON, str(TOOL_PY), "--help"], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(0, proc.returncode, proc.stderr[-400:])
        text = proc.stdout
        for flag in ("--file", "--confirm", "--keep-demo", "--json", "--user"):
            self.assertIn(flag, text, "工具必须暴露 %s（Spec §2.3）" % flag)

    def test_e3_tool_reuses_plan_and_writes_source_ref(self):
        src = TOOL_PY.read_text(encoding="utf-8") if TOOL_PY.exists() else ""
        self.assertIn("rate_import_plan(", src,
                      "工具必须复用 rate_import_plan()，不许另写一份校验（Spec §2.3）")
        self.assertIn("source_ref", src,
                      "source_ref 必须原样落库（批 8 §2.4 的追溯要求）")

    def test_e4_default_is_dry_run_and_dry_run_does_not_write(self):
        src = TOOL_PY.read_text(encoding="utf-8") if TOOL_PY.exists() else ""
        self.assertIn("confirm", src, "工具必须有 --confirm 开关")
        lowered = src.lower()
        self.assertIn("dry_run", lowered, "计划出参 / 打印里必须体现 dry_run")
        for write_hint in ("insert into", "update kb_quick_quote_delta_rule", "upsert"):
            self.assertNotIn(write_hint, lowered,
                             "工具里不许出现裸 SQL 写入（写库要走既有模块，Spec §2.3）")


# --------------------------------------------------------------------------- #
# F 组：部署文档
# --------------------------------------------------------------------------- #
class TestFDeployment(Base):
    def test_f1_deployment_md_registers_the_import_flow(self):
        self.assertTrue(DEPLOY_MD.exists(), "DEPLOYMENT.md 不存在")
        text = DEPLOY_MD.read_text(encoding="utf-8")
        self.assertIn("import_quick_quote_rates.py", text,
                      "DEPLOYMENT.md 必须登记费率导入命令（Spec 批 13 §2.4）")
        for token in ("--confirm", "--keep-demo", "authoritative"):
            self.assertIn(token, text, "登记里必须写清 %s（Spec §2.4）" % token)


if __name__ == "__main__":                                         # pragma: no cover
    unittest.main()

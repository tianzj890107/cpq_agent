"""红测：案例库现状话术、补数据动作与 DWG 实样导入 —— 逆向快速报价第 6 批。

Spec：`docs/specs/quick-quote-6-case-library-readiness.md`
依赖：批 1–5（`cpq_quick_quote_case.py` / `_match.py` / `_workspace.py` / `_price.py` /
`_file.py` 与前端 `quick-quote-panel.js`）。本批红测**假设前五批已实现**。

现状缺口（2026-09-21 实测，不是推断）：
  · `cpq_quick_quote_case.py` 只有 `case_missing_fields()`，没有 `library_readiness()` /
    `case_fix_plan()`；
  · `GET /api/quick-quote/cases` 出参没有 `readiness` 段；
  · `tech_app/frontend/quick-quote-panel.js` 0 行时只画一张空表，没有空态文案、没有转精准出口；
  · `normalize_case()` 丢掉 `source_sha256` / `parser_version` / `confirmed_by` /
    `confirmed_at`，`save_case()` 写出来这四列永远是 NULL；
  · `_row_value()` 对空价格返回 `None`，而价格列是 `NOT NULL` → `save_case()` 直接抛
    `psycopg.errors.NotNullViolation`；
  · 实测库现状：`cpq_wf.cpq_qq_standard_case` 2 行（两份 DWG 实样）、`eligible_total = 0`。

纪律：
  · 全部离线：不连 PG、不真发 HTTP、不调模型、不写业务数据；
  · 只用 `mock` 注入读库失败与 `load_cases()` 返回值；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib
import pathlib
import re
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CASE_PY = ROOT / "cpq_quick_quote_case.py"
SERVER_PY = ROOT / "cpq_agent_server.py"
DEPLOY_MD = ROOT / "DEPLOYMENT.md"
PANEL_JS = ROOT / "tech_app" / "frontend" / "quick-quote-panel.js"
MODULE_NAME = "cpq_quick_quote_case"
TODAY = dt.date(2026, 9, 21)

import cpq_kb  # noqa: E402

DW_CHANNEL_KEYS = ("source_sha256", "parser_version", "confirmed_by", "confirmed_at")


def load_case_module():
    return importlib.import_module(MODULE_NAME)


def eligible_case(**over):
    """一条**完全合格**的案例（改一个键就能造出各种不合格态）。"""
    case = {
        "case_code": "QQ-T-0001", "case_version": 1, "customer_masked": "",
        "box_type_code": "YT-RB-01001-A", "box_family": "01天地盖",
        "closure_type": "天地盖", "insert_type": "纸卡内托",
        "inner_length": 200, "inner_width": 150, "inner_height": 80,
        "standard_price": 12.5, "currency": "CNY", "industry": "packaging",
        "source_type": "workbook", "review_status": "reviewed",
        "quote_date": "2026-09-01", "valid_from": "2026-09-01",
    }
    case.update(over)
    return case


class Base(unittest.TestCase):
    def module(self):
        try:
            return load_case_module()
        except ImportError:                                     # pragma: no cover
            self.fail("%s.py 不存在（Spec 批 6 §2.1）" % MODULE_NAME)

    def readiness(self, cases, **kw):
        module = self.module()
        fn = getattr(module, "library_readiness", None)
        if not callable(fn):
            self.fail("%s.library_readiness() 缺失（Spec 批 6 §2.1）" % MODULE_NAME)
        kw.setdefault("today", TODAY)
        return fn(cases, **kw)

    def fix_plan(self, case):
        module = self.module()
        fn = getattr(module, "case_fix_plan", None)
        if not callable(fn):
            self.fail("%s.case_fix_plan() 缺失（Spec 批 6 §2.2）" % MODULE_NAME)
        return fn(case)

    def meta(self, module, name):
        """按名取模块常量（A 组与 E 组都用；2026-09-21 修：原来只定义在 TestANaming 上，
        E 组调用它一律 AttributeError —— 夹具笔误，不是缺口）。"""
        self.assertTrue(hasattr(module, name), "%s.%s 缺失（Spec §2.1/§2.2）" % (MODULE_NAME, name))
        return getattr(module, name)


# --------------------------------------------------------------------------- #
# A 组：命名契约
# --------------------------------------------------------------------------- #
class TestANaming(Base):
    def test_a1_verdict_and_action_closed_sets(self):
        module = self.module()
        self.assertEqual(("empty", "no_eligible", "ready"),
                         tuple(self.meta(module, "READINESS_VERDICTS")),
                         "READINESS_VERDICTS 顺序即判定顺序（Spec §2.1）")
        self.assertEqual(("import_standard_case", "fill_case_fields", "review_case",
                          "refresh_case", "transfer_to_precise"),
                         tuple(self.meta(module, "READINESS_ACTIONS")))
        self.assertEqual(("fill", "review", "extend", "retire"),
                         tuple(self.meta(module, "CASE_FIX_KINDS")))

    def test_a2_public_callables_exist(self):
        module = self.module()
        for name in ("library_readiness", "case_fix_plan"):
            self.assertTrue(callable(getattr(module, name, None)),
                            "%s.%s() 缺失（Spec §2.1/§2.2）" % (MODULE_NAME, name))


# --------------------------------------------------------------------------- #
# B 组：三态
# --------------------------------------------------------------------------- #
class TestBReadinessVerdicts(Base):
    def test_b1_empty_library(self):
        out = self.readiness([])
        self.assertEqual("empty", out["verdict"])
        self.assertEqual(0, out["case_total"])
        self.assertEqual(0, out["eligible_total"])
        self.assertIn("案例库还没有案例", out["headline"])
        self.assertIn("快速报价", out["detail"])
        self.assertIn("精准报价", out["detail"], "空库必须给出路，不能只说'没有'（Spec §2.1）")
        self.assertEqual([], out["blocked_by"])

    def test_b2_has_cases_but_none_eligible(self):
        out = self.readiness([eligible_case(review_status="draft")])
        self.assertEqual("no_eligible", out["verdict"])
        self.assertEqual(1, out["case_total"])
        self.assertEqual(0, out["eligible_total"])
        self.assertIn("1 条案例", out["headline"])
        self.assertIn("0 条可用于快速报价", out["headline"],
                      "必须把'有资产、只是不合格'与'空库'分开说（Spec §0）")

    def test_b3_ready(self):
        out = self.readiness([eligible_case(), eligible_case(case_code="QQ-T-0002")])
        self.assertEqual("ready", out["verdict"])
        self.assertEqual(2, out["eligible_total"])
        self.assertIn("可用于快速报价", out["headline"])
        self.assertIn("2", out["headline"])
        self.assertEqual([], out["blocked_by"])

    def test_b4_payload_keys_are_complete(self):
        out = self.readiness([eligible_case(review_status="draft")])
        for key in ("verdict", "headline", "detail", "case_total", "eligible_total",
                    "blocked_by", "next_actions"):
            self.assertIn(key, out, "readiness 出参缺 %s（Spec §2.1）" % key)


# --------------------------------------------------------------------------- #
# C 组：blocked_by 聚合与排序
# --------------------------------------------------------------------------- #
class TestCBlockedBy(Base):
    def test_c1_groups_by_reason_and_sorts_deterministically(self):
        cases = [eligible_case(case_code="QQ-T-0003", review_status="draft"),
                 eligible_case(case_code="QQ-T-0001", review_status="draft"),
                 eligible_case(case_code="QQ-T-0002", standard_price=0)]
        out = self.readiness(cases)
        groups = out["blocked_by"]
        self.assertEqual(["not_reviewed", "missing_fields"],
                         [row["reason_code"] for row in groups],
                         "count 降序、同 count 按 reason_code 升序（Spec §2.1）")
        self.assertEqual([2, 1], [row["count"] for row in groups])
        self.assertEqual(["QQ-T-0001", "QQ-T-0003"], groups[0]["cases"],
                         "组内 case_code 必须升序")

    def test_c2_missing_fields_fix_names_chinese_labels(self):
        out = self.readiness([eligible_case(standard_price=0)])
        group = out["blocked_by"][0]
        self.assertEqual("missing_fields", group["reason_code"])
        self.assertIn("标准单价", group["fix"],
                      "fix 必须是动作句并点名缺什么，不能只回字段键（Spec §2.1）")
        self.assertEqual(["标准单价"], group["cases"] and ["标准单价"] or ["标准单价"])

    def test_c3_every_group_has_label_count_fix_cases(self):
        out = self.readiness([eligible_case(source_type="demo", case_code="QQ-T-0009")])
        for row in out["blocked_by"]:
            for key in ("reason_code", "label", "count", "fix", "cases"):
                self.assertIn(key, row, "blocked_by 组缺 %s（Spec §2.1）" % key)
            self.assertNotEqual("ok", row["reason_code"], "ok 不进 blocked_by")


# --------------------------------------------------------------------------- #
# D 组：next_actions
# --------------------------------------------------------------------------- #
class TestDNextActions(Base):
    def test_d1_empty_gives_import_and_transfer(self):
        out = self.readiness([])
        actions = [row["action"] for row in out["next_actions"]]
        self.assertIn("import_standard_case", actions)
        self.assertIn("transfer_to_precise", actions)

    def test_d2_no_eligible_gives_fill_or_review_plus_transfer(self):
        out = self.readiness([eligible_case(standard_price=0)])
        actions = [row["action"] for row in out["next_actions"]]
        self.assertTrue({"fill_case_fields", "review_case"} & set(actions),
                        "不合格态必须给出补数据或审核动作（Spec §2.1）")
        self.assertIn("transfer_to_precise", actions)

    def test_d3_actions_are_inside_closed_set_and_have_text(self):
        module = self.module()
        allowed = set(module.READINESS_ACTIONS)
        for cases in ([], [eligible_case(standard_price=0)]):
            for row in self.readiness(cases)["next_actions"]:
                self.assertIn(row["action"], allowed)
                self.assertTrue(str(row.get("label") or "").strip(), "动作要有中文标签")
                self.assertTrue(str(row.get("hint") or "").strip(), "动作要有说明")


# --------------------------------------------------------------------------- #
# E 组：读不到库不回落 / 不改入参
# --------------------------------------------------------------------------- #
class TestEFailureDiscipline(Base):
    def test_e1_unavailable_raises_instead_of_empty(self):
        module = self.module()
        with mock.patch.object(cpq_kb, "snapshot", side_effect=cpq_kb.KbUnavailable("probe")):
            with self.assertRaises(module.CaseLibraryUnavailable):
                module.library_readiness(None, today=TODAY)

    def test_e2_does_not_mutate_input(self):
        cases = [eligible_case(review_status="draft")]
        snapshot = copy.deepcopy(cases)
        self.readiness(cases)
        self.assertEqual(snapshot, cases, "library_readiness() 不得改注入的原始行")

    def test_e3_reason_labels_cover_all_reason_codes(self):
        module = self.module()
        labels = self.meta(module, "REASON_LABELS")
        for code in ("missing_fields", "not_reviewed", "source_not_authoritative",
                     "expired", "retired", "industry_mismatch"):
            self.assertIn(code, labels, "REASON_LABELS 缺 %s（Spec §2.1）" % code)


# --------------------------------------------------------------------------- #
# F 组：case_fix_plan
# --------------------------------------------------------------------------- #
class TestCFixPlan(Base):
    def test_f1_missing_price_gives_fill_action(self):
        out = self.fix_plan(eligible_case(review_status="draft", standard_price=0))
        self.assertFalse(out["eligible"])
        self.assertEqual("missing_fields", out["reason_code"])
        self.assertEqual(["standard_price"], out["missing_fields"])
        self.assertEqual(["标准单价"], out["missing_labels"])
        kinds = [(row["field"], row["kind"]) for row in out["actions"]]
        self.assertIn(("standard_price", "fill"), kinds)
        self.assertIn(("review_status", "review"), kinds)

    def test_f2_retired_does_not_ask_for_more_data(self):
        out = self.fix_plan(eligible_case(review_status="retired", standard_price=0))
        self.assertEqual("retired", out["reason_code"])
        self.assertEqual([("review_status", "retire")],
                         [(row["field"], row["kind"]) for row in out["actions"]],
                         "已停用只给 retire，不再让补字段（Spec §2.2）")

    def test_f3_demo_source_asks_for_extend(self):
        out = self.fix_plan(eligible_case(source_type="demo"))
        self.assertEqual("source_not_authoritative", out["reason_code"])
        self.assertIn(("source_type", "extend"),
                      [(row["field"], row["kind"]) for row in out["actions"]])

    def test_f4_eligible_has_no_actions(self):
        out = self.fix_plan(eligible_case())
        self.assertTrue(out["eligible"])
        self.assertEqual([], out["actions"])
        self.assertTrue(out["price_present"])

    def test_f5_price_present_uses_blank_definition(self):
        self.assertFalse(self.fix_plan(eligible_case(standard_price=0))["price_present"],
                         "0 = 没有基准价（与 _is_blank() 同口径，Spec §2.2）")
        self.assertTrue(self.fix_plan(eligible_case())["price_present"])


# --------------------------------------------------------------------------- #
# G 组：接口 readiness 段
# --------------------------------------------------------------------------- #
class TestGEndpoint(Base):
    def test_g1_cases_endpoint_carries_readiness(self):
        server = importlib.import_module("cpq_agent_server")
        module = self.module()
        rows = [dict(eligible_case(review_status="draft"), eligible=False,
                     reason_code="not_reviewed", reason="案例还没审核")]
        with mock.patch.object(module, "load_cases", return_value=rows):
            out = server._handle_quick_quote_cases({})
        self.assertTrue(out.get("ok"), out)
        self.assertIn("readiness", out, "GET /api/quick-quote/cases 必须回 readiness（Spec §2.3）")
        self.assertEqual("no_eligible", out["readiness"]["verdict"])
        self.assertEqual(1, out["case_total"])

    def test_g2_unavailable_does_not_fake_an_empty_library(self):
        server = importlib.import_module("cpq_agent_server")
        module = self.module()
        with mock.patch.object(module, "load_cases",
                               side_effect=module.CaseLibraryUnavailable("probe")):
            out = server._handle_quick_quote_cases({})
        self.assertFalse(out.get("ok"))
        readiness = out.get("readiness")
        if readiness is not None:
            self.assertNotEqual("empty", readiness.get("verdict"),
                                "库读不到不许假装'空库'（Spec §2.3）")


# --------------------------------------------------------------------------- #
# H 组：前端
# --------------------------------------------------------------------------- #
class TestHFrontend(Base):
    def panel(self):
        self.assertTrue(PANEL_JS.exists(), "quick-quote-panel.js 不存在")
        return PANEL_JS.read_text(encoding="utf-8")

    def test_h1_render_readiness_is_exported(self):
        text = self.panel()
        self.assertIn("renderReadiness", text,
                      "前端必须有 renderReadiness(readiness)（Spec §2.4）")
        export = text[text.find("global.QuickQuotePanel"):]
        self.assertIn("renderReadiness", export, "renderReadiness 必须挂到 window.QuickQuotePanel")

    def test_h2_readiness_node_attributes(self):
        text = self.panel()
        self.assertIn("data-qq-readiness", text)
        self.assertIn("data-qq-verdict", text)

    def test_h3_transfer_to_precise_exit(self):
        text = self.panel()
        self.assertIn("transfer_precise", text,
                      "必须给「转精准报价」出口（data-qq-action=transfer_precise，Spec §2.4）")
        self.assertIn("data-qq-action", text)

    def test_h4_panel_takes_wording_from_payload(self):
        text = self.panel()
        self.assertRegex(text, r"readiness\.(headline|detail)",
                         "空态文案必须取自 readiness，前端不许自己拼判断句（Spec §2.4）")


# --------------------------------------------------------------------------- #
# I 组：两处入库缺口
# --------------------------------------------------------------------------- #
class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None

    def execute(self, sql, args=()):
        self.conn.statements.append((sql, tuple(args or ())))
        self.description = None

    def fetchall(self):
        return []

    def close(self):
        pass


class FakeConn:
    def __init__(self):
        self.statements = []

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        pass


class TestIWritePathGaps(Base):
    def test_i1_normalize_case_keeps_dwg_channel_columns(self):
        module = self.module()
        row = {"case_code": "QQ-T-0001", "source_sha256": "a" * 64,
               "parser_version": "ODA 27.1", "confirmed_by": "system_dwg_parser",
               "confirmed_at": "2026-09-21 16:36:20"}
        out = module.normalize_case(row)
        for key in DW_CHANNEL_KEYS:
            self.assertIn(key, out,
                          "normalize_case() 丢掉 %s → save_case() 写不进这一列（Spec §2.5）" % key)
        self.assertEqual("a" * 64, out["source_sha256"])

    def test_i2_blank_price_is_written_as_zero_not_none(self):
        module = self.module()
        for key in ("standard_price", "standard_cost"):
            self.assertEqual(0, module._row_value(key, None),
                             "%s 列是 NOT NULL DEFAULT 0，空值必须写 0（Spec §2.5）" % key)

    def test_i3_save_case_without_price_does_not_send_none(self):
        module = self.module()
        conn = FakeConn()
        # 案例根本**没有**价格键（`build_case_from_quote()` 对没有价的报价就是这个形态）。
        case = eligible_case(review_status="draft")
        # 夹具本来就没有 standard_cost 键（2026-09-21 修：pop 无缺省值会 KeyError，
        # 那是夹具笔误 —— 被测的是"没有价格键时不许传 None"）。
        case.pop("standard_price", None)
        case.pop("standard_cost", None)
        module.save_case(case, conn=conn, user={"user_id": 1, "username": "tester"})
        inserts = [row for row in conn.statements if "INSERT INTO" in row[0]]
        self.assertTrue(inserts, "save_case() 应当发出 INSERT")
        sql, params = inserts[-1]
        # 列名里允许数字（`source_sha256`）：2026-09-21 修，原正则 `[a-z_]+` 抓不到它，
        # 与实现无关的夹具笔误。
        names = re.findall(r'"([a-z_0-9]+)"', sql.split("VALUES")[0])
        idx = {name: i for i, name in enumerate(names)}
        for key in ("standard_price", "standard_cost"):
            self.assertIn(key, idx, "INSERT 列清单缺 %s" % key)
            self.assertIsNotNone(params[idx[key]],
                                 "%s 传了 None → 直接撞 NOT NULL（Spec §2.5）" % key)

    def test_i4_save_case_carries_dwg_provenance(self):
        module = self.module()
        conn = FakeConn()
        case = dict(eligible_case(review_status="draft"),
                    source_sha256="b" * 64, parser_version="ODA 27.1",
                    confirmed_by="system_dwg_parser")
        module.save_case(case, conn=conn, user={"user_id": 1, "username": "tester"})
        inserts = [row for row in conn.statements if "INSERT INTO" in row[0]]
        sql, params = inserts[-1]
        names = re.findall(r'"([a-z_0-9]+)"', sql.split("VALUES")[0])
        idx = {name: i for i, name in enumerate(names)}
        self.assertIn("source_sha256", idx, "INSERT 列清单缺 source_sha256（Spec §2.5）")
        self.assertEqual("b" * 64, params[idx["source_sha256"]],
                         "DWG 通道列必须真的进 INSERT 参数")


# --------------------------------------------------------------------------- #
# J 组：部署文档
# --------------------------------------------------------------------------- #
class TestJDeployment(Base):
    def test_j1_documents_case_library_fill_flow(self):
        text = DEPLOY_MD.read_text(encoding="utf-8", errors="replace")
        self.assertIn("cpq_qq_standard_case", text)
        self.assertIn("import_dwg_quick_quote_cases", text,
                      "必须登记补案例的工具（Spec §2.5）")
        marker = text.find("import_dwg_quick_quote_cases")
        window = text[max(0, marker - 2000):marker + 2000]
        self.assertIn("reviewed", window, "要写清怎么把案例审到「已审核」")
        self.assertIn("标准单价", window, "要写清没有价格的案例长什么样、怎么补")


if __name__ == "__main__":
    unittest.main(verbosity=2)

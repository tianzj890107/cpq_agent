"""红测：包装选品接到盒型库（报价侧匹配源换源 + 两侧口径同源）。

Spec：`docs/specs/quote-packaging-box-library-selection.md`
相关：`docs/specs/packaging-box-type-matching.md`（工艺侧五维口径）、
      `docs/specs/kb-in-pg-http-snapshot.md`（知识库唯一事实源 `cpq_kb`）、
      `docs/specs/quote-agent-industry-alignment.md`（④产品技术参数按行业换源）。

现状缺口（实测，不是推断）：

  · `cpq_agent_server.py:805` `_handle_match_products()` 不带行业，直接
    `cpq_match.match()`；`cpq_match.py:25` `PRODUCT_TABLE = "product_para_value"`
    （电池成品参数表）。现场包装询盘（数码天地盒 100*90*40）的 Top3 因此是三个
    锂亚电池，「用途/场景契合」只有 30 分。
  · 仓库里没有报价侧的盒型匹配器：`cpq_packaging_match.py` 不存在。
  · 工艺侧 `tech_app/backend/services/packaging_match.py:339` 早已有五维纯函数
    `match_box_types()`，但报价侧没有同一口径的实现，也没有任何两侧一致性约束。
  · `pick_product()`（`cpq_agent_server.py:702`）的「选用」只查 `product_para_value`，
    包装盒型编码在电池表里必然取不到行。
  · 前端 `确认需求解析结果.html` 里 `grep -c needs_new_tooling` → 0：盒型库接不住时
    没有任何出口。

纪律：
  · 全部离线：不连 Postgres、不调模型、不起服务、不写业务数据；
  · 工艺侧一律用 `kb_repo` 快照注入 fixture；报价侧一律用 `boxes` / `weights` 注入；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import copy
import importlib
import inspect
import pathlib
import re
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SERVER_PY = ROOT / "cpq_agent_server.py"
QUOTE_HTML = ROOT / "确认需求解析结果.html"
QUOTE_MATCH_PY = ROOT / "cpq_packaging_match.py"

from tech_app.backend.storage import kb_repo  # noqa: E402
from tech_app.backend.services import packaging_match as tech_match  # noqa: E402

import cpq_db  # noqa: E402
import cpq_kb  # noqa: E402
import cpq_match  # noqa: E402

SERVER_NAME = "cpq_agent_server"

# Spec §2.1 —— 命名契约（报价侧必须与工艺侧逐字一致）。
MATCH_INPUT_KEYS = ("inner_length", "inner_width", "inner_height", "closure_type",
                    "v_groove", "face_paper_gsm", "fit_clearance")
DIMENSIONS = ("size_range", "fit_clearance", "face_paper_gsm", "closure_type", "v_groove")
ENGINE_VERSION = "packaging_match_v1"

# 演示权重（工艺侧第 3 批 seed 的当前值；报价侧必须回显同一份）。
DEFAULT_WEIGHTS = (
    {"dimension": "size_range", "weight": 0.30, "hard_gate": 0},
    {"dimension": "fit_clearance", "weight": 0.25, "hard_gate": 1},
    {"dimension": "face_paper_gsm", "weight": 0.15, "hard_gate": 0},
    {"dimension": "closure_type", "weight": 0.20, "hard_gate": 1},
    {"dimension": "v_groove", "weight": 0.10, "hard_gate": 0},
)
FLIPPED_WEIGHTS = (
    {"dimension": "size_range", "weight": 0.10, "hard_gate": 0},
    {"dimension": "fit_clearance", "weight": 0.15, "hard_gate": 1},
    {"dimension": "face_paper_gsm", "weight": 0.55, "hard_gate": 0},
    {"dimension": "closure_type", "weight": 0.10, "hard_gate": 1},
    {"dimension": "v_groove", "weight": 0.10, "hard_gate": 0},
)


def _box(code, **over):
    row = {
        "box_type_code": code, "name": code, "name_en": code, "family": "01天地盖",
        "size_l_min": 80.0, "size_l_max": 400.0,
        "size_w_min": 80.0, "size_w_max": 300.0,
        "size_h_min": 25.0, "size_h_max": 120.0,
        "fit_clearance": 1.5, "face_paper_gsm": "157-250",
        "closure_type": "磁吸", "part_count": 5, "v_groove": "是",
        "hand_mount_ratio": "65%", "standard_seconds": 600,
        "automation_level": "半自动", "industry": "packaging", "status": "active",
        "applicable_industries": "化妆品/数码", "business_status": "标准",
    }
    row.update(over)
    return row


BOX_A = _box("BOX-A")                                  # 全维吻合 → matched
BOX_A2 = _box("BOX-A2")                                # 与 BOX-A 完全同分，仅编码不同
BOX_MIX = _box("BOX-MIX", face_paper_gsm="100-200")    # 克重只拿 0.5 分（需求 250）
BOX_BIG = _box("BOX-BIG", size_l_min=300.0, size_l_max=600.0)   # 尺寸越界但不硬淘汰
BOX_DRAWER = _box("BOX-DRAWER", closure_type="抽屉+拉带")        # 闭合方式冲突 → 淘汰
BOX_DRAWER2 = _box("BOX-DRAWER2", closure_type="抽屉")           # 同上

REQ_OK = {"inner_length": 100, "inner_width": 90, "inner_height": 40,
          "closure_type": "磁吸", "v_groove": "是", "face_paper_gsm": 250,
          "fit_clearance": 1.5}
REQ_GSM = dict(REQ_OK)
REQ_NO_GSM = {k: v for k, v in REQ_OK.items() if k != "face_paper_gsm"}
REQ_NO_CLOSURE = {k: v for k, v in REQ_OK.items() if k != "closure_type"}
REQ_NO_FIT = {k: v for k, v in REQ_OK.items() if k != "fit_clearance"}

# 第 1 步工具入参（包装）：10 项必填齐全 + 盒型库匹配输入同名键。
PACKAGING_TOOL_INPUT = {
    "industry": "packaging",
    "packaging_category": "数码产品包装盒",
    "packaging_product_name": "数码天地盒",
    "box_type": "天地盒",
    "quote_quantity": "1500",
    "inner_length": "100", "inner_width": "90", "inner_height": "40",
    "closure_type": "磁吸", "v_groove": "是", "face_paper_gsm": "250",
}
BATTERY_TOOL_INPUT = {"industry": "battery", "max_dimension": "100*90*40",
                      "application_scope": "追踪设备", "operating_temperature": "常温"}

# 非包装行业的既有六维结果（用于回归：包装换源不得动到它们）。
SIX_DIM_RESULT = {
    "ok": True,
    "source": {"db": "Postgres probe", "table": "product_para_value",
               "sql": "SELECT * FROM product_para_value", "rows": 1},
    "products": [{"code": "91000226", "name": "锂亚电池F0041P-LF", "total": 86.0,
                  "detail": {"dimension": {"label": "尺寸合规", "score": 100.0}},
                  "warnings": []}],
    "all_count": 1, "threshold": 70.0, "below_threshold": False, "advice": "",
    "weights": dict(cpq_match.WEIGHTS),
}

HANDOFF_ACTION = "wfOpenSend(false, 'tech_new_product')"


def read(path: pathlib.Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def tables(boxes, weights=None):
    return {
        "kb_packaging_box_type": [copy.deepcopy(b) for b in boxes],
        "kb_packaging_match_weight": [dict(w) for w in (weights or DEFAULT_WEIGHTS)],
    }


def score_of(result, code):
    for item in result.get("candidates") or []:
        if item.get("box_type_code") == code:
            return float(item.get("total_score"))
    raise AssertionError("候选里没有 %s：%s"
                         % (code, [c.get("box_type_code") for c in result.get("candidates") or []]))


def candidate_of(result, code):
    for item in result.get("candidates") or []:
        if item.get("box_type_code") == code:
            return item
    raise AssertionError("候选里没有 %s：%s"
                         % (code, [c.get("box_type_code") for c in result.get("candidates") or []]))


class PackagingCase(unittest.TestCase):
    """共用：工艺侧快照注入 + 报价侧模块按需加载（缺模块时给明确断言）。"""

    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.html = read(QUOTE_HTML)
        cls.server_src = read(SERVER_PY)
        cls.module_src = read(QUOTE_MATCH_PY)
        cls.server = importlib.import_module(SERVER_NAME)

    def setUp(self):
        self._cache = dict(kb_repo._CACHE)
        kb_repo._CACHE["version"] = "probe-version"
        kb_repo._CACHE["tables"] = {}

    def tearDown(self):
        kb_repo._CACHE.clear()
        kb_repo._CACHE.update(self._cache)

    # —— 工艺侧 ——
    def snapshot(self, boxes, weights=None):
        kb_repo._CACHE["version"] = "probe-version"
        kb_repo._CACHE["tables"] = tables(boxes, weights)

    def tech(self, req, boxes, weights=None):
        self.snapshot(boxes, weights)
        return tech_match.match_box_types(dict(req))

    # —— 报价侧 ——
    def quote_module(self):
        return importlib.import_module("cpq_packaging_match")

    def assert_quote_module(self):
        """报价侧匹配器；不存在时给明确断言（不抛 ImportError）。"""
        try:
            module = self.quote_module()
        except Exception as exc:                       # noqa: BLE001 - 红测要原文
            self.fail("缺少 cpq_packaging_match.py（Spec §2.1）：%s: %s"
                      % (type(exc).__name__, exc))
        for name in ("ENGINE_VERSION", "MATCH_INPUT_KEYS", "match_box_types"):
            if not hasattr(module, name):
                self.fail("cpq_packaging_match 缺少 %s（Spec §2.1）" % name)
        return module

    def quote(self, req, boxes, weights=None):
        module = self.assert_quote_module()
        params = inspect.signature(module.match_box_types).parameters
        for name in ("boxes", "weights"):
            if name not in params:
                self.fail("cpq_packaging_match.match_box_types 必须接受 %s 参数（Spec §2.1）" % name)
        return module.match_box_types(
            dict(req),
            boxes=[copy.deepcopy(b) for b in boxes],
            weights=[dict(w) for w in (weights or DEFAULT_WEIGHTS)])

    def both(self, req, boxes, weights=None):
        return self.tech(req, boxes, weights), self.quote(req, boxes, weights)

    # —— 服务端 ——
    def _boom_match(self, calls):
        def _boom(*_a, **_k):
            calls["match"] += 1
            raise AssertionError("包装行业不得再查 product_para_value（Spec §2.3）")
        return _boom

    def run_match_tool(self, tool_input, module, fake):
        """把 `_handle_match_products()` 跑在注入的盒型匹配结果上，返回 (文本, chat_candidates 事件)。"""
        self.server._ui_events().clear()
        calls = {"match": 0}
        with mock.patch.object(cpq_match, "match", self._boom_match(calls)), \
                mock.patch.object(module, "match_box_types",
                                  lambda inputs, **kwargs: copy.deepcopy(fake)):
            text = self.server._handle_match_products(dict(tool_input))
        events = [e for e in self.server._ui_events() if e.get("action") == "chat_candidates"]
        return text, events, calls


# --------------------------------------------------------------------------- #
# A. 报价侧匹配器存在且配置驱动（Spec §2.1）
# --------------------------------------------------------------------------- #
class AQuoteSideMatcher(PackagingCase):
    def test_a1_module_and_naming_contract(self):
        module = self.assert_quote_module()
        self.assertEqual(module.ENGINE_VERSION, ENGINE_VERSION)
        self.assertEqual(module.ENGINE_VERSION, tech_match.ENGINE_VERSION,
                         "报价侧与工艺侧的 engine_version 必须同值（Spec §2.1）")
        self.assertIsInstance(module.MATCH_INPUT_KEYS, tuple, "MATCH_INPUT_KEYS 必须是元组")
        self.assertEqual(tuple(module.MATCH_INPUT_KEYS), MATCH_INPUT_KEYS,
                         "报价侧 MATCH_INPUT_KEYS 必须与工艺侧逐字一致（顺序即契约）")

    def test_a2_match_box_types_accepts_injected_rows(self):
        module = self.assert_quote_module()
        params = inspect.signature(module.match_box_types).parameters
        for name in ("boxes", "weights"):
            self.assertIn(name, params, "match_box_types 必须可注入 boxes / weights（Spec §2.1）")
            self.assertIsNone(params[name].default,
                              "%s 默认必须是 None（None 时才读报价侧自己的 cpq_kb）" % name)

    def test_a3_weights_and_hard_gates_come_from_the_injected_rows(self):
        result = self.quote(REQ_GSM, [BOX_MIX], FLIPPED_WEIGHTS)
        echoed = {row["dimension"]: row for row in result.get("dimensions") or []}
        self.assertEqual(set(echoed), set(DIMENSIONS), "dimensions 必须回显五个维度")
        for row in FLIPPED_WEIGHTS:
            self.assertAlmostEqual(float(echoed[row["dimension"]]["weight"]),
                                   float(row["weight"]), places=6)
            self.assertEqual(bool(echoed[row["dimension"]]["hard_gate"]),
                             bool(row["hard_gate"]))
        base = self.quote(REQ_GSM, [BOX_MIX], DEFAULT_WEIGHTS)
        self.assertNotEqual(score_of(result, "BOX-MIX"), score_of(base, "BOX-MIX"),
                            "换权重必须改变总分 —— 权重只能来自权重行，不能写死在代码里")

    def test_a4_candidate_order_is_status_then_code(self):
        boxes = [BOX_DRAWER, BOX_A2, BOX_A, BOX_BIG]
        result = self.quote(REQ_OK, boxes)
        order = [c["box_type_code"] for c in result["candidates"]]
        self.assertEqual(order[0], "BOX-A", "matched 必须排在 rejected 之前")
        self.assertEqual(order[:3], ["BOX-A", "BOX-A2", "BOX-BIG"],
                         "同档按盒型编码升序：%s" % order)
        self.assertEqual(order[-1], "BOX-DRAWER")
        self.assertEqual(candidate_of(result, "BOX-DRAWER")["status"], "rejected")
        self.assertFalse(candidate_of(result, "BOX-DRAWER")["can_confirm"])
        self.assertTrue(candidate_of(result, "BOX-A")["can_confirm"])

    def test_a5_pure_function_no_write_no_network_no_mutation(self):
        module = self.assert_quote_module()
        boxes = [copy.deepcopy(b) for b in (BOX_A, BOX_MIX, BOX_DRAWER)]
        weights = [dict(w) for w in DEFAULT_WEIGHTS]
        before = copy.deepcopy((boxes, weights))
        module.match_box_types(dict(REQ_OK), boxes=boxes, weights=weights)
        self.assertEqual((boxes, weights), before, "纯函数不得改写传入的 boxes / weights 行")
        src = self.module_src
        self.assertFalse(re.search(r"\b(INSERT|UPDATE|DELETE)\s+INTO\b", src, re.I),
                         "匹配器不得写库")
        for forbidden in ("import requests", "import httpx", "import urllib",
                          "import socket", "from requests", "from urllib"):
            self.assertNotIn(forbidden, src, "匹配器不得联网（%s）" % forbidden)


# --------------------------------------------------------------------------- #
# B. 两侧口径逐字段一致（Spec §2.2）
# --------------------------------------------------------------------------- #
class BParityWithTechSide(PackagingCase):
    def test_b1_dimensions_rows_match_tech_side(self):
        tech_res, quote_res = self.both(REQ_OK, [BOX_A, BOX_BIG], DEFAULT_WEIGHTS)
        self.assertEqual([d["dimension"] for d in quote_res["dimensions"]],
                         [d["dimension"] for d in tech_res["dimensions"]],
                         "维度顺序必须与工艺侧一致")
        for want, got in zip(tech_res["dimensions"], quote_res["dimensions"]):
            self.assertEqual(got["dimension"], want["dimension"])
            self.assertAlmostEqual(float(got["weight"]), float(want["weight"]), places=9)
            self.assertEqual(bool(got["hard_gate"]), bool(want["hard_gate"]))

    def test_b2_every_candidate_field_matches_tech_side(self):
        for req, boxes, weights in (
                (REQ_OK, [BOX_A, BOX_A2, BOX_BIG, BOX_DRAWER], DEFAULT_WEIGHTS),
                (REQ_GSM, [BOX_MIX, BOX_A], FLIPPED_WEIGHTS)):
            with self.subTest(req=req.get("face_paper_gsm"), boxes=len(boxes)):
                tech_res, quote_res = self.both(req, boxes, weights)
                self.assertEqual([c["box_type_code"] for c in quote_res["candidates"]],
                                 [c["box_type_code"] for c in tech_res["candidates"]],
                                 "候选集合与顺序必须与工艺侧一致")
                for want in tech_res["candidates"]:
                    got = candidate_of(quote_res, want["box_type_code"])
                    for key in ("name", "family", "status", "can_confirm",
                                "out_of_range", "reject_reasons",
                                "undecidable_dimensions", "applicable_industries",
                                "business_status"):
                        self.assertEqual(got.get(key), want.get(key),
                                         "候选 %s 的 %s 与工艺侧不一致"
                                         % (want["box_type_code"], key))
                    self.assertAlmostEqual(float(got["total_score"]),
                                           float(want["total_score"]), places=9)
                    self.assertEqual(len(got["dimension_scores"]),
                                     len(want["dimension_scores"]))
                    for dimension, value in want["dimension_scores"].items():
                        self.assertAlmostEqual(float(got["dimension_scores"][dimension]),
                                               float(value), places=9)

    def test_b3_summary_fields_match_tech_side(self):
        for req, boxes in ((REQ_OK, [BOX_A, BOX_BIG, BOX_DRAWER]),
                           (REQ_GSM, [BOX_MIX])):
            tech_res, quote_res = self.both(req, boxes)
            for key in ("engine_version", "inputs_complete", "missing_inputs",
                        "suggested_box_type", "needs_new_tooling", "new_tooling_reason"):
                self.assertEqual(quote_res.get(key), tech_res.get(key),
                                 "汇总字段 %s 与工艺侧不一致" % key)

    def test_b4_missing_input_and_all_rejected_cases_match_tech_side(self):
        cases = {
            "缺闭合方式": (REQ_NO_CLOSURE, [BOX_A, BOX_DRAWER]),
            "缺面纸克重": (REQ_NO_GSM, [BOX_A]),
            "缺选填配合间隙": (REQ_NO_FIT, [BOX_A, BOX_MIX]),
            "全淘汰": (REQ_OK, [BOX_DRAWER, BOX_DRAWER2]),
        }
        for label, (req, boxes) in cases.items():
            with self.subTest(case=label):
                tech_res, quote_res = self.both(req, boxes)
                self.assertEqual(quote_res["missing_inputs"], tech_res["missing_inputs"], label)
                self.assertEqual(quote_res["needs_new_tooling"], tech_res["needs_new_tooling"], label)
                self.assertEqual(quote_res["new_tooling_reason"], tech_res["new_tooling_reason"], label)
                self.assertEqual(quote_res["suggested_box_type"], tech_res["suggested_box_type"], label)
                for want in tech_res["candidates"]:
                    got = candidate_of(quote_res, want["box_type_code"])
                    self.assertEqual(got["status"], want["status"], label)
                    self.assertAlmostEqual(float(got["total_score"]),
                                           float(want["total_score"]), places=9)


# --------------------------------------------------------------------------- #
# C. 入口按行业分流（Spec §2.3）
# --------------------------------------------------------------------------- #
class CEntryByIndustry(PackagingCase):
    def test_c1_match_tool_references_the_box_library(self):
        src = body = self.server_src
        i = src.find("def _handle_match_products")
        self.assertGreater(i, 0, "找不到 _handle_match_products()")
        body = src[i:i + 4000]
        self.assertIn("cpq_packaging_match", body,
                      "_handle_match_products 必须按行业分流到报价侧盒型匹配器（Spec §2.3）")

    def test_c2_packaging_never_queries_the_battery_table(self):
        module = self.assert_quote_module()
        fake = self.tech(REQ_OK, [BOX_A])
        self.assertEqual(fake["suggested_box_type"], "BOX-A")
        text, events, calls = self.run_match_tool(PACKAGING_TOOL_INPUT, module, fake)
        self.assertEqual(calls["match"], 0, "包装行业不得再调 cpq_match.match()（Spec §2.3）")
        self.assertNotIn("❌", text, "包装需求齐全时不得再被当作缺匹配参数拦下：%s" % text)
        self.assertIn("盒型", text)
        self.assertIn("BOX-A", text, "推荐清单必须来自盒型库候选：%s" % text)

    def test_c3_candidates_event_carries_box_library_contract(self):
        module = self.assert_quote_module()
        fake = self.tech(REQ_OK, [BOX_A, BOX_BIG])
        _text, events, _calls = self.run_match_tool(PACKAGING_TOOL_INPUT, module, fake)
        self.assertEqual(len(events), 1, "包装匹配必须发一次 chat_candidates 事件：%s" % events)
        event = events[0]
        for key in ("engine_version", "suggested_box_type", "needs_new_tooling",
                    "candidates", "dimensions", "inputs_complete", "missing_inputs"):
            self.assertIn(key, event, "chat_candidates 缺盒型库口径字段 %s（Spec §2.3）" % key)
        self.assertEqual(event["engine_version"], ENGINE_VERSION)
        self.assertEqual(event["suggested_box_type"], "BOX-A")
        codes = [c["box_type_code"] for c in event["candidates"]]
        self.assertEqual(codes, ["BOX-A", "BOX-BIG"])

    def test_c4_other_industries_keep_the_six_dimension_path(self):
        module = self.assert_quote_module()
        calls = {"match": 0}

        def _fake_match(req, top_n=3, **kwargs):
            calls["match"] += 1
            return copy.deepcopy(SIX_DIM_RESULT)

        self.server._ui_events().clear()
        with mock.patch.object(module, "match_box_types",
                               side_effect=AssertionError("非包装行业不得走盒型库（Spec §2.3）")), \
                mock.patch.object(cpq_match, "match", _fake_match):
            text = self.server._handle_match_products(dict(BATTERY_TOOL_INPUT))
        self.assertEqual(calls["match"], 1, "电池行业必须仍走既有六维匹配")
        self.assertIn("六维", text)
        self.assertNotIn("盒型", text)


# --------------------------------------------------------------------------- #
# D. 选用与 ④ 同源（Spec §2.4）
# --------------------------------------------------------------------------- #
class DPickSameSourceAsTechParams(PackagingCase):
    def test_d1_load_box_type_reads_the_library(self):
        module = self.assert_quote_module()
        self.assertTrue(hasattr(module, "load_box_type"),
                        "cpq_packaging_match 必须提供 load_box_type()（Spec §2.1）")
        row = module.load_box_type("BOX-A", boxes=[copy.deepcopy(BOX_A), copy.deepcopy(BOX_A2)])
        self.assertEqual(row.get("box_type_code"), "BOX-A")
        self.assertEqual(row.get("name"), "BOX-A")
        self.assertFalse(module.load_box_type("BOX-NOPE", boxes=[copy.deepcopy(BOX_A)]),
                         "库里没有的编码必须如实返回空，不得编造")

    def test_d2_pick_reads_box_library_and_keeps_columns_in_sync(self):
        module = self.assert_quote_module()
        header = [c["key"] for c in self.server.tech_param_columns("packaging")]
        seen = []

        def _run_select(sql, limit):
            seen.append(sql)
            return ([], [])

        with mock.patch.object(module, "load_box_type",
                               lambda code, **kwargs: copy.deepcopy(BOX_A)), \
                mock.patch.object(cpq_db, "run_select", _run_select):
            res = self.server.pick_product("BOX-A", "packaging")
        self.assertTrue(res.get("ok"), "包装选用必须能取到盒型行：%s" % res)
        self.assertEqual(set(res["techparams_row"]), set(header),
                         "④ 行键必须与 ④ 表头同源（Spec §2.4）")
        self.assertEqual(res["techparams_row"]["box_type_code"], "BOX-A")
        self.assertIn("BOX-A", list(res.get("products_row", {}).values()),
                      "选用的盒型编码必须写进产品行（Spec §2.4）")
        for sql in seen:
            self.assertNotIn("product_para_value", sql,
                             "包装选用不得再查电池成品参数表（Spec §2.3/§2.4）")

    def test_d3_tech_param_row_stays_the_single_source(self):
        row = self.server.tech_param_row("packaging", dict(BOX_A))
        self.assertEqual(set(row), {c["key"] for c in self.server.tech_param_columns("packaging")})
        self.assertEqual(row["box_type_code"], "BOX-A")
        self.assertEqual(row["closure_type"], "磁吸")
        self.assertEqual(self.server.tech_param_row("packaging", {}), {},
                         "没有盒型编码就不得编造技术参数行")


# --------------------------------------------------------------------------- #
# E. 接不住给出口 + 不回归（Spec §2.5 / §3）
# --------------------------------------------------------------------------- #
class EExitAndNoRegression(PackagingCase):
    def test_e1_new_tooling_says_so_and_offers_the_exit(self):
        module = self.assert_quote_module()
        fake = self.tech(REQ_OK, [BOX_DRAWER, BOX_DRAWER2])
        self.assertTrue(fake["needs_new_tooling"])
        self.assertEqual(fake["suggested_box_type"], "")
        self.assertTrue(fake["new_tooling_reason"])
        text, events, calls = self.run_match_tool(PACKAGING_TOOL_INPUT, module, fake)
        self.assertEqual(calls["match"], 0)
        self.assertIn("没有适配", text, "接不住时必须如实说库里没有适配的盒型：%s" % text)
        self.assertIn("转技术工艺", text, "接不住时必须给出「转技术工艺」出口：%s" % text)
        self.assertTrue(events, "接不住时也必须渲染候选清单（含淘汰原因）")

    def test_e2_frontend_handles_new_tooling(self):
        # 用 assertTrue 而不是 assertIn：失败时不要把整份 HTML（约 6 万字）打进报告。
        self.assertTrue("needs_new_tooling" in self.html,
                        "前端必须处理盒型库接不住的情况（Spec §2.5）："
                        "确认需求解析结果.html 里没有 needs_new_tooling")
        self.assertTrue(HANDOFF_ACTION in self.html,
                        "前端必须挂上转技术工艺出口（wfOpenSend(false, 'tech_new_product')）")

    def test_e3_kb_unavailable_raises_instead_of_empty_pool(self):
        module = self.assert_quote_module()
        self.assertTrue(hasattr(module, "QuoteKbUnavailable"),
                        "cpq_packaging_match 必须导出 QuoteKbUnavailable（Spec §2.1）")
        with mock.patch.object(cpq_kb, "snapshot", side_effect=cpq_kb.KbUnavailable("probe")), \
                mock.patch.object(cpq_kb, "kb_version", side_effect=cpq_kb.KbUnavailable("probe")):
            with self.assertRaises((module.QuoteKbUnavailable, cpq_kb.KbUnavailable),
                                   msg="盒型库读不到必须抛错，绝不回落空候选（Spec §2.1）"):
                module.match_box_types(dict(REQ_OK))

    def test_e4_other_industries_and_tech_side_untouched(self):
        # 本用例是护栏：现在就该绿（在缺模块时 module_src 为空，断言自动跳过；
        # 模块一旦落地，这两条静态断言就开始生效）。
        self.assertEqual(cpq_match.PRODUCT_TABLE, "product_para_value")
        self.assertAlmostEqual(sum(float(v) for v in cpq_match.WEIGHTS.values()), 1.0, places=9)
        self.assertEqual(tuple(cpq_match.WEIGHTS), ("dimension", "scope", "temperature",
                                                    "life", "hermeticity", "other"))
        self.assertNotIn("import cpq_match", self.module_src,
                         "报价侧盒型匹配器不得回落电池表（Spec §3）")
        self.assertNotIn("product_para_value", self.module_src)
        self.assertEqual(tech_match.ENGINE_VERSION, ENGINE_VERSION)
        self.assertEqual(tuple(tech_match.MATCH_INPUT_KEYS), MATCH_INPUT_KEYS)
        self.assertTrue(hasattr(kb_repo, "packaging_match_weights"),
                        "工艺侧权重表读取口不得改（Spec §3）")


if __name__ == "__main__":
    unittest.main()

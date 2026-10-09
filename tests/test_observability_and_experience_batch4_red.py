"""红测：体验与可观察性（批次 4）—— 缺什么 / 哪一版 / 谁算的 / 为什么排在后面。

Spec：docs/specs/observability-and-experience-batch4.md

现状缺口（2026-10-09 实测，代码事实）：

  · P3/P4/P11/P6/P7/P1/P13/P12/P10 没有统一投影：`packaging_observability.py` 不存在；
    `converter_role` 只在 `tech_app/tools/*` 出现，前端没有任何文件渲染「主/回退 + 版本 + 许可」；
  · P8 无 `kb_health`（既有 `kb_deploy_preflight.py` 是**部署**预检，不是运行期面板）；
  · P9 `cpq_quick_quote_match._rank_reason()` 只解释「为什么排在这里」，没有「相对上一名多/少了
    哪些差异项」，也没有「基准案例 vs 当前参数」对照；
  · P10 `home_card._waiting_for()` 给了「在等谁」，但**没有「已等多久」**；
  · P2 `project_access.py` 没有「按你的角色可见：N 个」的范围说明（且不得泄露无权数量）；
  · T15 `cad_ir/parser.py` 把 `sample_step: 0.01` 硬编码在 3 处，不在 `DEFAULT_LIMITS`、
    不进 `parser.options`。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import os
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FRONTEND = ROOT / "tech_app" / "frontend"
SPLINE_DXF = ROOT / "tests" / "fixtures" / "dxf" / "curves_arc_ellipse_spline.dxf"


def _module(dotted: str):
    try:
        return importlib.import_module(dotted)
    except Exception as exc:                                   # noqa: BLE001 - 交回用例判定
        return exc


def _obs(self):
    mod = _module("tech_app.backend.services.packaging_observability")
    if isinstance(mod, Exception):
        self.fail("缺 `tech_app/backend/services/packaging_observability.py`（Spec §2.1）：%r" % mod)
    return mod


def _fn(self, mod, name):
    fn = getattr(mod, name, None)
    if not callable(fn):
        self.fail("缺 `%s`（Spec §2）：模块 %r 里没有这个可调用" % (name, mod.__name__))
    return fn


class T15CurveSamplingConfig(unittest.TestCase):
    """T15：曲线采样参数配置化并写进 IR 元数据 —— 换参数可复现。"""

    def mod(self):
        mod = _module("tech_app.backend.services.cad_ir.parser")
        if isinstance(mod, Exception):
            self.fail("缺 cad_ir parser：%r" % mod)
        return mod

    def test_t15_a_limits_expose_curve_sample_step(self):
        mod = self.mod()
        self.assertIn("curve_sample_step", mod.DEFAULT_LIMITS,
                      "曲线采样步长必须进 DEFAULT_LIMITS（Spec §2.5）")
        default = float(mod.DEFAULT_LIMITS["curve_sample_step"])
        self.assertGreater(default, 0.0)
        overridden = mod.resolve_limits({"curve_sample_step": 0.5})["curve_sample_step"]
        self.assertEqual(0.5, float(overridden), "overrides 必须能覆盖采样步长")

    def test_t15_b_env_overrides_curve_sample_step(self):
        mod = self.mod()
        self.assertEqual("CAD_IR_CURVE_SAMPLE_STEP", getattr(mod, "ENV_CURVE_SAMPLE_STEP", None))
        with mock.patch.dict(os.environ, {"CAD_IR_CURVE_SAMPLE_STEP": "0.25"}):
            value = mod.resolve_limits().get("curve_sample_step")
        self.assertEqual(0.25, float(value), "环境变量必须能配采样步长（浮点）")

    def test_t15_c_parse_records_sample_step_and_option(self):
        mod = self.mod()
        data = SPLINE_DXF.read_bytes()
        ir = mod.parse_dxf(data, filename="curves_arc_ellipse_spline.dxf",
                           limits={"curve_sample_step": 0.5})
        options = (ir.get("parser") or {}).get("options") or {}
        self.assertIn("curve_sample_step", options,
                      "采样步长必须写进 parser.options（IR 元数据可复现）")
        self.assertEqual(0.5, float(options["curve_sample_step"]))
        spline = next((row for row in ir.get("entities") or []
                       if str(row.get("handle")) == "43"), None)
        self.assertIsNotNone(spline, "夹具里的样条实体（handle=43）必须在 IR 里")
        attrs = spline.get("attributes") or {}
        self.assertIn("sample_step", attrs, "样条必须落盘 sample_step")
        self.assertEqual(0.5, float(attrs["sample_step"]), "落盘的 sample_step 必须随配置变化")


class P3QuoteExportBlockers(unittest.TestCase):
    """P3：报价卡片「还差哪几步才能正式导出」。"""

    STEPS = ["确认成本", "回填报价正文", "选定基准案例", "内部评审"]

    def test_p3_a_lists_remaining_steps_in_order(self):
        fn = _fn(self, _obs(self), "quote_export_blockers")
        out = fn(steps=self.STEPS, done=["确认成本"])
        self.assertFalse(out["can_export"])
        self.assertEqual(["回填报价正文", "选定基准案例", "内部评审"], out["next_steps"])
        self.assertEqual(3, out["remaining"])
        self.assertEqual(out["remaining"], len(out["next_steps"]))

    def test_p3_b_ready_when_all_done(self):
        fn = _fn(self, _obs(self), "quote_export_blockers")
        out = fn(steps=self.STEPS, done=list(self.STEPS))
        self.assertTrue(out["can_export"])
        self.assertEqual([], out["next_steps"])
        self.assertEqual(0, out["remaining"])


class P4CostDisplay(unittest.TestCase):
    """P4：成本页「版本 + 是否暂估 + 缺口分类」，分类是闭集，不许静默丢。"""

    def test_p4_a_categories_are_closed_and_unknown_folds_into_other(self):
        fn = _fn(self, _obs(self), "cost_display")
        out = fn(version=3, is_estimate=True, gaps=[
            {"category": "缺材料价"}, {"category": "缺用量"}, {"category": "缺用量"},
            {"category": "别的东西"}])
        self.assertEqual(3, out["version"])
        self.assertTrue(out["is_estimate"])
        self.assertEqual(4, out["gap_total"])
        self.assertEqual(1, out["gaps"]["缺材料价"])
        self.assertEqual(2, out["gaps"]["缺用量"])
        self.assertEqual(0, out["gaps"]["缺尺寸"])
        self.assertEqual(1, out["gaps"]["其它"], "未知分类必须归入「其它」，不许丢")

    def test_p4_b_all_categories_present_even_when_empty(self):
        fn = _fn(self, _obs(self), "cost_display")
        out = fn(version=1, is_estimate=False, gaps=[])
        self.assertEqual(0, out["gap_total"])
        for name in ("缺材料价", "缺用量", "缺尺寸", "其它"):
            self.assertIn(name, out["gaps"])


class P11MoldReadiness(unittest.TestCase):
    """P11：排模确认页点名「缺什么就不能算用量」。"""

    def test_p11_missing_params_block_usage(self):
        fn = _fn(self, _obs(self), "mold_readiness")
        blocked = fn(params={"sheet_width_mm": 1000, "sheet_height_mm": None,
                             "piece_length_mm": 200, "piece_width_mm": 0})
        self.assertFalse(blocked["can_compute"])
        self.assertEqual(["sheet_height_mm", "piece_width_mm"], blocked["missing"])
        ready = fn(params={"sheet_width_mm": 1000, "sheet_height_mm": 700,
                           "piece_length_mm": 200, "piece_width_mm": 100})
        self.assertTrue(ready["can_compute"])
        self.assertEqual([], ready["missing"])


class P6ConverterBanner(unittest.TestCase):
    """P6：2.1 顶部显示「主/回退 + 版本 + 许可状态」，一眼看出踩没踩 PATH 坑。"""

    def test_p6_a_fallback_is_called_out(self):
        fn = _fn(self, _obs(self), "converter_banner")
        out = fn(converter_role="fallback", provider="oda", version="27.1", license_ok=True)
        self.assertEqual("回退转换器", out["done_by"])
        self.assertTrue(str(out["warning"]).strip(), "回退转换器必须给出告警文案")
        self.assertEqual("oda", out["provider"])
        self.assertEqual("27.1", out["version"])

    def test_p6_b_unknown_license_is_not_reported_ok(self):
        fn = _fn(self, _obs(self), "converter_banner")
        out = fn(converter_role="primary", provider="oda", version="27.1", license_ok=None)
        self.assertEqual("unverified", out["license"], "许可未知不许谎报 ok")
        self.assertTrue(str(out["warning"]).strip())
        ok = fn(converter_role="primary", provider="oda", version="27.1", license_ok=True)
        self.assertEqual("ok", ok["license"])
        self.assertEqual("", ok["warning"])


class P7ExpressionNote(unittest.TestCase):
    """P7：表达式口径文案唯一来源 —— 不写「逐字一致」。"""

    def test_p7_note_is_the_exact_wording(self):
        fn = _fn(self, _obs(self), "cost_expression_note")
        note = fn()
        self.assertEqual("变量映射后的表达式匹配", note)
        self.assertNotIn("逐字", note)


class P8KbHealth(unittest.TestCase):
    """P8：知识库健康面板 —— 不因无关表为空宣布全系统不可用。"""

    def mod(self):
        mod = _module("tech_app.backend.services.kb_health")
        if isinstance(mod, Exception):
            self.fail("缺 `tech_app/backend/services/kb_health.py`（Spec §2.2）：%r" % mod)
        return mod

    def test_p8_a_unrelated_empty_table_does_not_block(self):
        fn = _fn(self, self.mod(), "kb_health")
        out = fn({"kb_material": [], "kb_process_route": [{"a": 1}, {"a": 2}]},
                 required_tables=("kb_process_route",), kb_version=4)
        self.assertTrue(out["usable_for_current_route"],
                        "无关表为空不得宣布全系统不可用")
        self.assertEqual([], out["required_empty"])
        self.assertIn("kb_material", out["empty_tables"])
        self.assertEqual(4, out["kb_version"])

    def test_p8_b_required_empty_blocks_and_is_named(self):
        fn = _fn(self, self.mod(), "kb_health")
        out = fn({"kb_material": [], "kb_process_route": [{"a": 1}]},
                 required_tables=("kb_material",), rows_by_source={"demo": 3, "workbook": 1})
        self.assertFalse(out["usable_for_current_route"])
        self.assertEqual(["kb_material"], out["required_empty"])
        self.assertIn("required_empty:kb_material", out["problems"])
        self.assertAlmostEqual(0.75, float(out["demo_ratio"]), places=6)


class P9QuickQuoteExplain(unittest.TestCase):
    """P9：候选「为什么不是更高分」+ 转精准「基准 vs 当前」对照。"""

    def mod(self):
        mod = _module("cpq_quick_quote_match")
        if isinstance(mod, Exception):
            self.fail("缺 cpq_quick_quote_match：%r" % mod)
        return mod

    def test_p9_a_explain_ranking_says_why_not_higher(self):
        fn = _fn(self, self.mod(), "explain_ranking")
        candidates = [
            {"case_code": "C1", "diff_items": [{"field": "face_paper_gsm", "label": "面纸克重"}]},
            {"case_code": "C2", "diff_items": [{"field": "face_paper_gsm", "label": "面纸克重"},
                                               {"field": "hot_stamping", "label": "烫金"}]},
        ]
        out = fn(candidates)
        self.assertEqual("C1", out[0]["case_code"])
        self.assertEqual(1, out[0]["rank"])
        self.assertEqual([], out[0]["why_not_higher"], "第 1 名没有「为什么不是更高分」")
        self.assertEqual(2, out[1]["rank"])
        self.assertTrue(any("烫金" in reason for reason in out[1]["why_not_higher"]),
                        "第 2 名要点出比上一名多出的差异项：烫金")

    def test_p9_b_transfer_compare_lists_changed_fields(self):
        fn = _fn(self, self.mod(), "transfer_compare")
        base = {"face_paper_gsm": 200, "quantity": 5000, "hot_stamping": False}
        current = {"face_paper_gsm": 250, "quantity": 5000, "hot_stamping": True}
        out = fn(base, current)
        self.assertEqual(2, out["changed_total"])
        changed = {row["field"] for row in out["rows"] if row["changed"]}
        self.assertEqual({"face_paper_gsm", "hot_stamping"}, changed)
        face = next(row for row in out["rows"] if row["field"] == "face_paper_gsm")
        self.assertEqual(200, face["base"])
        self.assertEqual(250, face["current"])


class P10WaitingElapsed(unittest.TestCase):
    """P10：卡片显示「在等谁 + 已等多久」。"""

    def test_p10_adds_elapsed_without_dropping_waiting_for(self):
        fn = _fn(self, _obs(self), "waiting_view")
        waiting = {"kind": "user", "role": "", "label": "张三", "username": "zhangsan"}
        out = fn(waiting, now="2026-10-09T12:00:00Z", last_event_at="2026-10-09T11:30:00Z")
        self.assertEqual("张三", out["label"], "在等谁必须原样保留")
        self.assertEqual("zhangsan", out["username"])
        self.assertEqual(1800, int(out["elapsed_seconds"]))
        self.assertTrue(str(out["elapsed_text"]).strip(), "已等多久必须有可读文案")
        none_out = fn({"kind": "none", "role": "", "label": "", "username": ""},
                      now="2026-10-09T12:00:00Z", last_event_at="2026-10-09T11:30:00Z")
        self.assertIsNone(none_out["elapsed_seconds"])
        self.assertEqual("", none_out["elapsed_text"])


class P12DraftVisibility(unittest.TestCase):
    """P12：草稿角标「草稿可看，不可送审」—— 但按操作细分，不一刀切。"""

    def test_p12_internal_review_is_not_blocked_like_formal_release(self):
        fn = _fn(self, _obs(self), "draft_visibility")
        for action in ("read", "internal_review", "formal_release"):
            out = fn(action)
            self.assertTrue(out["can_read"], "草稿三种动作都可看")
            self.assertEqual("草稿可看，不可送审", out["badge"])
        self.assertTrue(fn("read")["can_do"])
        self.assertTrue(fn("internal_review")["can_do"], "草稿内部评审 ≠ 正式报价发布")
        self.assertFalse(fn("formal_release")["can_do"], "正式发布必须被草稿状态拦住")


class P1PartConcepts(unittest.TestCase):
    """P1：主界面只突出「业务零件」，几何数量放次级。"""

    def test_p1_business_is_primary_geometry_is_secondary(self):
        fn = _fn(self, _obs(self), "part_concepts_view")
        out = fn(business_total=28, geometry_total=263)
        self.assertEqual("业务零件", out["primary"]["label"])
        self.assertEqual(28, out["primary"]["total"])
        labels = {item["label"]: item["total"] for item in out["secondary"]}
        self.assertEqual(263, labels.get("几何区域"))


class P13ManualDrawingAdvice(unittest.TestCase):
    """P13：哪些图不建议自动识别、直接人工建档。"""

    def test_p13_signals_recommend_manual_with_reasons(self):
        fn = _fn(self, _obs(self), "manual_drawing_advice")
        hit = fn(signals={"scanned_raster": True, "no_blocks": False})
        self.assertTrue(hit["recommend_manual"])
        self.assertTrue(hit["reasons"], "必须点名命中的信号")
        self.assertIn("人工", hit["advice"])
        clean = fn(signals={"scanned_raster": False, "no_blocks": False})
        self.assertFalse(clean["recommend_manual"])
        self.assertEqual([], clean["reasons"])


class P2VisibleScope(unittest.TestCase):
    """P2：项目列表「按你的角色可见：N 个」—— 只给有权数量，不泄露无权数量。"""

    def test_p2_note_counts_only_visible_and_hides_rest(self):
        mod = _module("tech_app.backend.services.project_access")
        if isinstance(mod, Exception):
            self.fail("缺 project_access：%r" % mod)
        fn = _fn(self, mod, "visible_scope_note")
        out = fn(scope="role", visible_count=7)
        self.assertEqual("role", out["scope"])
        self.assertEqual(7, out["visible_count"])
        self.assertEqual("按你的角色可见：7 个", out["note"])
        self.assertIsNone(out.get("hidden_count"), "不得暴露无权项目数量")


class FrontendWiring(unittest.TestCase):
    """前端接线（Spec §2.6）：展示一律引用后端投影，不在前端另算一份。"""

    def _text(self, name):
        path = FRONTEND / name
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def test_frontend_cost_page_uses_cost_display(self):
        text = self._text("cost-review.js") + self._text("cost.js")
        self.assertTrue("cost_display" in text,
                        "成本页必须引用后端 cost_display 投影（Spec §2.6）")

    def test_frontend_quick_quote_uses_why_not_higher(self):
        text = self._text("quick-quote-panel.js")
        self.assertTrue("why_not_higher" in text,
                        "快速报价候选必须渲染 why_not_higher（Spec §2.6）")

    def test_frontend_shows_converter_banner(self):
        blob = ""
        for path in list(FRONTEND.glob("*.js")) + list(FRONTEND.glob("*.html")):
            blob += path.read_text(encoding="utf-8", errors="ignore")
        self.assertTrue("converter_banner" in blob,
                        "至少一处前端展示「主/回退 + 版本 + 许可」（Spec §2.6）")


if __name__ == "__main__":
    unittest.main()

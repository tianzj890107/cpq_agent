"""红测：盒型匹配的"缺数据不许打折"与"尺寸越界不许推荐"。

Spec：`docs/specs/packaging-match-undecidable-and-size-guard.md`
上游：`docs/specs/packaging-box-type-matching.md`

**现状缺口（首次运行时必须失败，实测不是推断）**：

  · 盒型行缺 `fit_clearance` 时 `_dimension_fit()` 返回 `0.0` 并**计入分母**，而需求侧缺
    同一项时该维**整条跳过**（分子分母都不计）—— 同一种"缺"两套算法。后果：真实 DWG 确认
    进来的盒型只要需求填了间隙就**最高只能 0.75 分**，永远排不到前面；
  · `v_groove` 写 `是（90度）` 时 `_as_bool()` 整词匹配失败 → 该维记 0 分并进
    `undecidable_dimensions`，白丢 0.10；
  · `suggested_box_type` / `needs_new_tooling` 只看 `status == "matched"`，不看
    `out_of_range` —— 30×30×20 的需求会把 396.5–408mm 的盒型推成"建议盒型"，并声称
    "不需要开模"；
  · `kb_deploy_preflight` 不检查权威盒型是否登记了 `fit_clearance`。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import copy
import importlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.storage import kb_repo  # noqa: E402

DEFAULT_WEIGHTS = (
    {"dimension": "size_range", "weight": 0.30, "hard_gate": 0},
    {"dimension": "fit_clearance", "weight": 0.25, "hard_gate": 1},
    {"dimension": "face_paper_gsm", "weight": 0.15, "hard_gate": 0},
    {"dimension": "closure_type", "weight": 0.20, "hard_gate": 1},
    {"dimension": "v_groove", "weight": 0.10, "hard_gate": 0},
)

#: 基准需求：尺寸/克重/闭合/V 槽四项都落在下面这个盒型的区间里。
REQ_OK = {"inner_length": 200, "inner_width": 150, "inner_height": 80,
          "closure_type": "磁吸", "v_groove": "是", "face_paper_gsm": 200,
          "fit_clearance": 1.5}
#: 极小需求（30×30×20）：与 396.5–408mm 的圆盘类盒型差一个数量级。
REQ_TINY = dict(REQ_OK, inner_length=30, inner_width=30, inner_height=20, closure_type="天地盖")


def _box(code, **over):
    row = {
        "box_type_code": code, "name": code, "family": "01天地盖",
        "size_l_min": 80.0, "size_l_max": 400.0,
        "size_w_min": 80.0, "size_w_max": 300.0,
        "size_h_min": 25.0, "size_h_max": 120.0,
        "fit_clearance": 1.5, "face_paper_gsm": "157-250",
        "closure_type": "磁吸", "v_groove": "是",
        "industry": "packaging", "status": "active",
    }
    row.update(over)
    return row


#: 四维全对、只缺配合间隙（就是从真实图纸确认进来时没填字段的那种行）。
BOX_NO_FIT = _box("BOX-NO-FIT")
BOX_NO_FIT.pop("fit_clearance")
BOX_FULL = _box("BOX-FULL")                                     # 全维齐全
BOX_WRONG_FIT = _box("BOX-WRONG-FIT", fit_clearance=3.0)         # 间隙超差 → 必须仍被淘汰
BOX_VGROOVE_SUFFIX = _box("BOX-VG-SUFFIX", v_groove="是（90度）")
BOX_VGROOVE_NO_SUFFIX = _box("BOX-VG-NO-SUFFIX", v_groove="否(无)")
#: 圆盘类盒型：尺寸区间与 REQ_TINY 差一个数量级，其余维度都合得上。
BOX_HUGE = _box("BOX-HUGE", size_l_min=396.5, size_l_max=408.0,
                size_w_min=396.5, size_w_max=408.0,
                size_h_min=47.0, size_h_max=50.5,
                closure_type="天地盖", face_paper_gsm="157/200/250/300/350")


def load_match():
    try:
        return importlib.import_module("tech_app.backend.services.packaging_match")
    except Exception:                                  # noqa: BLE001
        return None


class MatchGuardCase(unittest.TestCase):
    """共用：只替换 `kb_repo` 快照，不碰库、不联网。"""

    def setUp(self):
        self._cache = dict(kb_repo._CACHE)

    def tearDown(self):
        kb_repo._CACHE.clear()
        kb_repo._CACHE.update(self._cache)

    def snapshot(self, boxes, weights=None):
        kb_repo._CACHE["version"] = "probe-version"
        kb_repo._CACHE["tables"] = {
            "kb_packaging_box_type": [copy.deepcopy(b) for b in boxes],
            "kb_packaging_match_weight": [dict(w) for w in (weights or DEFAULT_WEIGHTS)],
        }

    def engine(self):
        module = load_match()
        self.assertIsNotNone(module, "缺少 tech_app/backend/services/packaging_match.py")
        return module

    def candidate(self, result, code):
        for item in result["candidates"]:
            if item["box_type_code"] == code:
                return item
        self.fail("结果里没有候选 %s：%s" % (code, [c["box_type_code"] for c in result["candidates"]]))

    def match(self, boxes, req):
        self.snapshot(boxes)
        return self.engine().match_box_types(dict(req))


# --------------------------------------------------------------------------- #
# A. 缺数据不打折（两侧对称）
# --------------------------------------------------------------------------- #
class AUndecidableIsNotAPenalty(MatchGuardCase):
    def test_a1_box_side_missing_clearance_is_not_scored(self):
        result = self.match([BOX_NO_FIT], REQ_OK)
        item = self.candidate(result, "BOX-NO-FIT")
        self.assertEqual(item["status"], "matched", "缺配合间隙不淘汰（Spec C1）")
        self.assertAlmostEqual(
            item["total_score"], 1.0, places=6,
            msg="其余四维全对时必须满分：缺的那一维不该进分母（现在是 0.75）")

    def test_a2_both_sides_missing_behave_the_same(self):
        with_fit = self.candidate(self.match([BOX_NO_FIT], REQ_OK), "BOX-NO-FIT")
        without_fit = self.candidate(
            self.match([BOX_NO_FIT], dict(REQ_OK, fit_clearance=None)), "BOX-NO-FIT")
        self.assertAlmostEqual(with_fit["total_score"], without_fit["total_score"], places=6,
                               msg="需求填不填间隙，都不该让盒型的分数变差（两侧对称，Spec C1）")

    def test_a3_missing_clearance_is_still_declared(self):
        item = self.candidate(self.match([BOX_NO_FIT], REQ_OK), "BOX-NO-FIT")
        self.assertIn("fit_clearance", item["undecidable_dimensions"],
                      "缺了必须能看见（Spec C1/C2）")

    def test_a4_out_of_tolerance_clearance_is_still_rejected(self):
        result = self.match([BOX_WRONG_FIT], REQ_OK)
        item = self.candidate(result, "BOX-WRONG-FIT")
        self.assertEqual(item["status"], "rejected", "超差仍是硬门槛（Spec C1）")
        self.assertIn("fit_clearance_out_of_tolerance", item["reject_reasons"])

    def test_a5_complete_box_still_scores_one(self):
        item = self.candidate(self.match([BOX_FULL], REQ_OK), "BOX-FULL")
        self.assertAlmostEqual(item["total_score"], 1.0, places=6)


# --------------------------------------------------------------------------- #
# B. 缺数据必须看得见
# --------------------------------------------------------------------------- #
class BDataGapsAreVisible(MatchGuardCase):
    def test_b1_gap_is_reported_in_plain_words(self):
        item = self.candidate(self.match([BOX_NO_FIT], REQ_OK), "BOX-NO-FIT")
        self.assertIn("data_gaps", item, "缺数据的候选必须给可展示的说明（Spec C2）")
        gaps = item["data_gaps"]
        self.assertIsInstance(gaps, list)
        dimensions = {str(gap.get("dimension") or "") for gap in gaps}
        self.assertIn("fit_clearance", dimensions)
        for gap in gaps:
            if str(gap.get("dimension") or "") == "fit_clearance":
                self.assertTrue(str(gap.get("message") or "").strip(),
                                "缺口说明不许是空串")

    def test_b2_no_gap_means_empty_list(self):
        item = self.candidate(self.match([BOX_FULL], REQ_OK), "BOX-FULL")
        self.assertEqual(item.get("data_gaps"), [], "没有缺口时必须是空列表，不许是 None")

    def test_b3_missing_data_does_not_block_confirmation(self):
        item = self.candidate(self.match([BOX_NO_FIT], REQ_OK), "BOX-NO-FIT")
        self.assertTrue(item["can_confirm"],
                        "人工确认优先：缺数据只是提示，不该把候选锁死（Spec C2）")


# --------------------------------------------------------------------------- #
# C. 布尔写法归一（只放宽解析）
# --------------------------------------------------------------------------- #
class CBoolWriting(MatchGuardCase):
    def test_c1_suffixed_true_parses_as_true(self):
        item = self.candidate(self.match([BOX_VGROOVE_SUFFIX], REQ_OK), "BOX-VG-SUFFIX")
        self.assertAlmostEqual(item["dimension_scores"].get("v_groove", 0.0), 1.0, places=6,
                               msg="`是（90度）` 必须与 `是` 同值（Spec C3）")
        self.assertNotIn("v_groove", item["undecidable_dimensions"])

    def test_c2_suffixed_false_parses_as_false(self):
        item = self.candidate(self.match([BOX_VGROOVE_NO_SUFFIX], REQ_OK), "BOX-VG-NO-SUFFIX")
        self.assertAlmostEqual(item["dimension_scores"].get("v_groove", 0.0), 0.0, places=6,
                               msg="需求要 V 槽、盒型不支持 → 0 分（Spec C3）")
        self.assertIn("v_groove_required_but_unsupported", item["reject_reasons"])

    def test_c3_plain_words_are_unchanged(self):
        item = self.candidate(self.match([BOX_FULL], REQ_OK), "BOX-FULL")
        self.assertAlmostEqual(item["dimension_scores"].get("v_groove", 0.0), 1.0, places=6)


# --------------------------------------------------------------------------- #
# D. 尺寸越界不许被推荐
# --------------------------------------------------------------------------- #
class DSizeGuard(MatchGuardCase):
    def test_d1_out_of_range_box_is_not_recommended(self):
        result = self.match([BOX_HUGE], REQ_TINY)
        item = self.candidate(result, "BOX-HUGE")
        self.assertTrue(item["out_of_range"], "396.5–408mm 对 30mm 需求必须标越界")
        self.assertEqual(result["suggested_box_type"], "",
                         "越界的盒型不得被推荐（Spec C4）")
        self.assertTrue(result["needs_new_tooling"],
                        "没有合规候选时不许声称'有现成盒型'（Spec C4）")
        self.assertEqual(result["new_tooling_reason"], "size_out_of_range")

    def test_d2_candidate_list_still_shows_the_near_miss(self):
        result = self.match([BOX_HUGE], REQ_TINY)
        self.assertEqual([c["box_type_code"] for c in result["candidates"]], ["BOX-HUGE"],
                         "越界候选仍要列出来给人看，只是不推荐（Spec C4）")
        self.assertEqual([c["status"] for c in result["candidates"]], ["matched"])

    def test_d3_compliant_candidate_still_wins(self):
        result = self.match([BOX_HUGE, BOX_FULL], REQ_OK)
        self.assertEqual(result["suggested_box_type"], "BOX-FULL")
        self.assertFalse(result["needs_new_tooling"])
        self.assertNotEqual(result["new_tooling_reason"], "size_out_of_range")

    def test_d4_compliant_candidate_wins_even_when_out_of_range_scores_higher(self):
        # BOX-HUGE 在 REQ_OK 下尺寸越界但其余维满分；BOX-WEAK 尺寸合规、克重很差。
        weak = _box("BOX-WEAK", face_paper_gsm="100-120", v_groove="否")
        result = self.match([BOX_HUGE, weak], REQ_OK)
        self.assertFalse(self.candidate(result, "BOX-WEAK")["out_of_range"])
        self.assertTrue(self.candidate(result, "BOX-HUGE")["out_of_range"])
        self.assertEqual(result["suggested_box_type"], "BOX-WEAK",
                         "有合规候选时，越界候选即使分更高也不得被推荐（Spec C4）")

    def test_d5_reason_precedence_is_unchanged(self):
        module = self.engine()
        self.snapshot([])
        empty = module.match_box_types(dict(REQ_OK))
        self.assertEqual(empty["new_tooling_reason"], "no_box_type",
                         "没有盒型行时仍是 no_box_type（Spec C4 优先级）")


# --------------------------------------------------------------------------- #
# E. 数据准入：权威盒型必须登记配合间隙
# --------------------------------------------------------------------------- #
class EAuthoritativeBoxDataGap(unittest.TestCase):
    def preflight(self):
        try:
            return importlib.import_module("tech_app.tools.kb_deploy_preflight")
        except Exception as exc:                       # noqa: BLE001
            self.skipTest("预检工具不可导入：%s" % exc)

    def codes(self, tables, env="production"):
        report = self.preflight().preflight(tables, env=env, kb_version=1)
        return {str(item.get("code")) for item in report.get("problems") or []}

    def box_rows(self, source_type):
        row = _box("BOX-%s" % source_type.upper(), source_type=source_type)
        row.pop("fit_clearance")
        return {"kb_packaging_box_type": [row]}

    def test_e1_authoritative_box_without_clearance_is_reported(self):
        codes = self.codes(self.box_rows("dwg_confirmed"))
        self.assertIn("box_type_missing_fit_clearance", codes,
                      "权威盒型缺配合间隙必须被预检挡下（Spec C5）")

    def test_e2_demo_rows_are_not_reported(self):
        codes = self.codes(self.box_rows("demo"))
        self.assertNotIn("box_type_missing_fit_clearance", codes,
                         "样例数据本来就允许不全，不许拦（Spec C5）")

    def test_e3_local_env_is_not_affected(self):
        codes = self.codes(self.box_rows("dwg_confirmed"), env="local")
        self.assertNotIn("box_type_missing_fit_clearance", codes,
                         "本地/CI 不受生产准入规则影响（Spec C5）")


# --------------------------------------------------------------------------- #
# F. 报价侧同口径模块必须同步（Spec C6：两侧不得漂移）
# --------------------------------------------------------------------------- #
class FQuoteSideParity(unittest.TestCase):
    """报价侧 `cpq_packaging_match.py` 是同一口径的第二份实现（它的 docstring 写明
    「工艺侧是唯一口径来源」）。工艺侧改了口径而报价侧不改，就会在**报价工作台**留下
    "同一份数据两种结论"；现有 parity 用例（`test_quote_packaging_box_selection_red` B 组）
    恰好覆盖不到下面这三种输入，所以单独立一组。
    """

    def quote_module(self):
        try:
            return importlib.import_module("cpq_packaging_match")
        except Exception as exc:                       # noqa: BLE001
            self.skipTest("报价侧匹配器不可导入：%s" % exc)

    def match(self, boxes, req, weights=None):
        return self.quote_module().match_box_types(
            dict(req), boxes=[copy.deepcopy(b) for b in boxes],
            weights=[dict(w) for w in (weights or DEFAULT_WEIGHTS)])

    def candidate(self, result, code):
        for item in result["candidates"]:
            if item["box_type_code"] == code:
                return item
        self.fail("结果里没有候选 %s：%s" % (code, [c["box_type_code"] for c in result["candidates"]]))

    def test_f1_quote_side_scores_symmetric_clearance(self):
        item = self.candidate(self.match([BOX_NO_FIT], REQ_OK), "BOX-NO-FIT")
        self.assertEqual(item["status"], "matched",
                         "报价侧同样不许因盒型缺配合间隙淘汰候选（Spec C1/C6）")
        self.assertAlmostEqual(item["total_score"], 1.0, places=6,
                               msg="报价侧同样不许因盒型缺配合间隙打折（Spec C1/C6）")

    def test_f2_quote_side_never_recommends_an_out_of_range_box(self):
        result = self.match([BOX_HUGE], REQ_TINY)
        self.assertEqual(result["suggested_box_type"], "",
                         "报价侧同样不得推荐越界盒型（Spec C4/C6）")
        self.assertTrue(result["needs_new_tooling"], "Spec C4/C6")
        self.assertEqual(result["new_tooling_reason"], "size_out_of_range")

    def test_f3_quote_side_reports_data_gaps(self):
        item = self.candidate(self.match([BOX_NO_FIT], REQ_OK), "BOX-NO-FIT")
        gaps = item.get("data_gaps")
        self.assertIsInstance(gaps, list, "报价侧候选同样必须给出 data_gaps（Spec C2/C6）")
        self.assertEqual(sorted(str(gap.get("dimension")) for gap in gaps),
                         ["fit_clearance"], "Spec C2/C6")

    def test_f4_quote_side_parses_suffixed_boolean(self):
        item = self.candidate(
            self.match([BOX_VGROOVE_SUFFIX], REQ_OK), "BOX-VG-SUFFIX")
        self.assertAlmostEqual(item["dimension_scores"].get("v_groove", 0.0), 1.0, places=6,
                               msg="`是（90度）` 在报价侧同样必须等同 `是`（Spec C3/C6）")


if __name__ == "__main__":
    unittest.main(verbosity=2)

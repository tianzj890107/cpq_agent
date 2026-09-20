"""红测：盒型匹配与人工确认 —— 包装第 4 批。

Spec：docs/specs/packaging-box-type-matching.md
依赖：第 1 批（四行业注册表）、第 2 批（包装需求模板）、第 3 批（包装知识库扩展表 + 12 盒型）
已完成。

现状缺口（实测，不是推断）：
  · `kb_repo` 有 packaging_box_types / packaging_part_templates /
    packaging_process_templates / packaging_insert_accessories，**没有
    packaging_match_weights()** —— 5 维权重表灌了却读不出来；
  · `tech_app/backend/services/packaging_match.py` 不存在，五维打分、硬门槛淘汰、
    缺输入处理一条也没有落地；
  · 没有 `wip_packaging_box_match` / `wip_packaging_box_match_audit` 两张表，
    候选、分项分、淘汰原因、人工确认都不落库，刷新即丢、无人可查；
  · 没有「输入不全就不许确认」的约束：缺闭合方式时照样能给高分；
  · main.py 没有 box-match 三个路由，也没有决策角色常量。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import copy
import importlib
import inspect
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MATCH_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_match.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
SCHEMA_SQL = ROOT / "tech_app" / "backend" / "storage" / "da_schema.sql"
CONFIRM_JS = ROOT / "tech_app" / "frontend" / "requirement-confirm.js"

from tech_app.backend import config  # noqa: E402
from tech_app.backend.storage import da_db, da_repo, kb_repo, meta_backend, store  # noqa: E402
from tech_app.backend.services import industry_templates  # noqa: E402

# Spec §2.1：匹配真正需要的 7 个键。
MATCH_INPUT_KEYS = ("inner_length", "inner_width", "inner_height", "closure_type",
                    "v_groove", "face_paper_gsm", "fit_clearance")
DIMENSIONS = ("size_range", "fit_clearance", "face_paper_gsm", "closure_type", "v_groove")

# 演示权重（第 3 批 seed 的当前值）。
DEFAULT_WEIGHTS = (
    {"dimension": "size_range", "weight": 0.30, "hard_gate": 0},
    {"dimension": "fit_clearance", "weight": 0.25, "hard_gate": 1},
    {"dimension": "face_paper_gsm", "weight": 0.15, "hard_gate": 0},
    {"dimension": "closure_type", "weight": 0.20, "hard_gate": 1},
    {"dimension": "v_groove", "weight": 0.10, "hard_gate": 0},
)

# 受控盒型：分数可以精确断言，不依赖演示数据的具体数值。
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


BOX_A = _box("BOX-A")                                            # 全维吻合
BOX_B = _box("BOX-B", closure_type="抽屉+拉带")                    # 闭合方式冲突 → 淘汰
BOX_C = _box("BOX-C", fit_clearance=3.0)                          # 间隙超差 → 淘汰
BOX_D = _box("BOX-D", size_l_min=80.0, size_l_max=150.0)          # 尺寸超出（L 太小）
BOX_E = _box("BOX-E")                                             # V 槽盒型=是，需求=否 → 0.6
BOX_F = _box("BOX-F", v_groove="否")                              # 需求要 V 槽但盒型不支持 → 0
BOX_P = _box("BOX-P", face_paper_gsm="100-120")                   # 克重很差、V 槽好
BOX_Q = _box("BOX-Q", v_groove="否")                              # 克重好、V 槽差
BOX_R = _box("BOX-R", size_l_min=80.0, size_l_max=410.0,
             face_paper_gsm="100-120", v_groove="否")             # 尺寸合规、其余差
BOX_S = _box("BOX-S")                                             # 尺寸略超、其余满分
BOX_X = _box("BOX-X")
BOX_Y = _box("BOX-Y")
BOX_TOL = _box("BOX-TOL", fit_clearance=2.0)                      # 与需求差 0.5 → 刚好通过
BOX_MULTI = _box("BOX-MULTI", closure_type="磁吸/天地盖")           # 多值闭合方式
BOX_DRAWER = _box("BOX-DRAWER", closure_type="抽屉+拉带")
BOX_PLAIN = _box("BOX-PLAIN", closure_type="天地盖")

# 基准需求（全部填齐）。
REQ_OK = {"inner_length": 200, "inner_width": 150, "inner_height": 80,
          "closure_type": "磁吸", "v_groove": "是", "face_paper_gsm": 200,
          "fit_clearance": 1.5}


def tables(boxes, weights=None):
    return {
        "kb_packaging_box_type": [copy.deepcopy(b) for b in boxes],
        "kb_packaging_match_weight": [dict(w) for w in (weights or DEFAULT_WEIGHTS)],
    }


def load_match():
    """匹配引擎模块；不存在时返回 None（用例给明确断言，不抛 ImportError）。"""
    try:
        return importlib.import_module("tech_app.backend.services.packaging_match")
    except Exception:
        return None


class MatchCase(unittest.TestCase):
    """共用：快照可替换 + 独立临时 SQLite。"""

    def setUp(self):
        self._cache = dict(kb_repo._CACHE)
        self._da_path = config.DA_DB_PATH
        self.db_file = pathlib.Path(tempfile.mkdtemp()) / "match.sqlite3"
        self._patch_da = mock.patch.object(config, "DA_DB_PATH", self.db_file)
        self._patch_da.start()
        da_db.init_db(self.db_file)

    def tearDown(self):
        self._patch_da.stop()
        da_db.close_conn()
        kb_repo._CACHE.clear()
        kb_repo._CACHE.update(self._cache)

    def snapshot(self, boxes, weights=None):
        kb_repo._CACHE["version"] = "probe-version"
        kb_repo._CACHE["tables"] = tables(boxes, weights)

    def engine(self):
        module = load_match()
        self.assertIsNotNone(module, "缺少 tech_app/backend/services/packaging_match.py（Spec §4.5）")
        return module

    def match_with(self, boxes, req=None, weights=None):
        self.snapshot(boxes, weights)
        module = self.engine()
        return module.match_box_types(dict(req or REQ_OK))

    @staticmethod
    def candidate(result, code):
        for item in result["candidates"]:
            if item["box_type_code"] == code:
                return item
        raise AssertionError("候选里没有 %s：%s" % (code, [c["box_type_code"] for c in result["candidates"]]))


# --------------------------------------------------------------------------- #
# A. 权重与维度一律读表
# --------------------------------------------------------------------------- #
class AWeightsComeFromTheTable(MatchCase):
    def test_a1_kb_repo_exposes_match_weights(self):
        self.snapshot([BOX_A])
        self.assertTrue(
            hasattr(kb_repo, "packaging_match_weights"),
            "kb_repo 缺 packaging_match_weights()（Spec §4.5）—— 权重表读不出来")
        rows = kb_repo.packaging_match_weights()
        self.assertEqual({r["dimension"] for r in rows}, set(DIMENSIONS))
        self.assertLessEqual(float(sum(float(r["weight"]) for r in rows)), 1.0001)

    def test_a2_engine_dimensions_come_from_the_table(self):
        self.snapshot([BOX_A], weights=[
            {"dimension": "size_range", "weight": 0.44, "hard_gate": 0},
            {"dimension": "fit_clearance", "weight": 0.22, "hard_gate": 1},
            {"dimension": "face_paper_gsm", "weight": 0.13, "hard_gate": 0},
            {"dimension": "closure_type", "weight": 0.11, "hard_gate": 1},
            {"dimension": "v_groove", "weight": 0.10, "hard_gate": 0},
        ])
        result = self.engine().match_box_types(dict(REQ_OK))
        echoed = {d["dimension"]: d for d in result["dimensions"]}
        self.assertEqual(set(echoed), set(DIMENSIONS))
        self.assertAlmostEqual(float(echoed["size_range"]["weight"]), 0.44, places=6)
        self.assertAlmostEqual(float(echoed["face_paper_gsm"]["weight"]), 0.13, places=6)
        self.assertTrue(echoed["fit_clearance"]["hard_gate"])
        self.assertFalse(echoed["size_range"]["hard_gate"])

    def test_a3_weights_are_not_hardcoded(self):
        default = self.match_with([BOX_P, BOX_Q])
        self.assertEqual(default["candidates"][0]["box_type_code"], "BOX-P",
                         "默认权重下 BOX-P（克重差/V 槽好）应领先")
        flipped = self.match_with([BOX_P, BOX_Q], weights=[
            {"dimension": "size_range", "weight": 0.30, "hard_gate": 0},
            {"dimension": "fit_clearance", "weight": 0.25, "hard_gate": 1},
            {"dimension": "face_paper_gsm", "weight": 0.05, "hard_gate": 0},
            {"dimension": "closure_type", "weight": 0.20, "hard_gate": 1},
            {"dimension": "v_groove", "weight": 0.90, "hard_gate": 0},
        ])
        self.assertEqual(flipped["candidates"][0]["box_type_code"], "BOX-Q",
                         "改了 kb_packaging_match_weight 的权重后排序必须跟着变"
                         "（说明代码里写死了权重）")

    def test_a4_total_score_is_normalised(self):
        result = self.match_with([BOX_A], weights=[
            {"dimension": "size_range", "weight": 3.0, "hard_gate": 0},
            {"dimension": "fit_clearance", "weight": 2.5, "hard_gate": 1},
            {"dimension": "face_paper_gsm", "weight": 1.5, "hard_gate": 0},
            {"dimension": "closure_type", "weight": 2.0, "hard_gate": 1},
            {"dimension": "v_groove", "weight": 1.0, "hard_gate": 0},
        ])
        top = result["candidates"][0]
        self.assertAlmostEqual(top["total_score"], 1.0, places=6,
                               msg="权重和为 10 时也必须归一化到满分 1.0")
        for item in result["candidates"]:
            self.assertGreaterEqual(item["total_score"], 0.0)
            self.assertLessEqual(item["total_score"], 1.0)


# --------------------------------------------------------------------------- #
# B. 五维判分
# --------------------------------------------------------------------------- #
class BDimensionScoring(MatchCase):
    def test_b1_size_in_range_is_full_score(self):
        item = self.candidate(self.match_with([BOX_A]), "BOX-A")
        self.assertAlmostEqual(item["dimension_scores"]["size_range"], 1.0, places=6)
        self.assertFalse(item["out_of_range"])

    def test_b2_size_out_of_range_decays_linearly(self):
        req = dict(REQ_OK, inner_length=500)
        item = self.candidate(self.match_with([BOX_A], req=req), "BOX-A")
        expected = 1.0 - (500 - 400) / (400 - 80)
        self.assertAlmostEqual(item["dimension_scores"]["size_range"], expected, places=6,
                               msg="超区间必须按 1 - 超出量/区间宽度 线性衰减（Spec §2.3.1）")
        self.assertTrue(item["out_of_range"])

    def test_b3_overshoot_equal_to_span_scores_zero(self):
        item = self.candidate(self.match_with([BOX_A], req=dict(REQ_OK, inner_length=720)), "BOX-A")
        self.assertAlmostEqual(item["dimension_scores"]["size_range"], 0.0, places=6)
        item2 = self.candidate(self.match_with([BOX_A], req=dict(REQ_OK, inner_length=900)), "BOX-A")
        self.assertAlmostEqual(item2["dimension_scores"]["size_range"], 0.0, places=6,
                               msg="超出再多也只能夹到 0，不能变负数")

    def test_b4_gsm_in_range_full_then_decays(self):
        inside = self.candidate(self.match_with([BOX_A]), "BOX-A")
        self.assertAlmostEqual(inside["dimension_scores"]["face_paper_gsm"], 1.0, places=6)
        outside = self.candidate(self.match_with([BOX_A], req=dict(REQ_OK, face_paper_gsm=300)), "BOX-A")
        expected = 1.0 - (300 - 250) / (250 - 157)
        self.assertAlmostEqual(outside["dimension_scores"]["face_paper_gsm"], expected, places=6)

    def test_b5_multivalue_closure_passes_on_intersection(self):
        req = dict(REQ_OK, closure_type="磁吸")
        multi = self.candidate(self.match_with([BOX_MULTI], req=req), "BOX-MULTI")
        self.assertEqual(multi["status"], "matched",
                         "盒型闭合方式是多值（磁吸/天地盖）时，只要交集非空就不该淘汰")
        self.assertAlmostEqual(multi["dimension_scores"]["closure_type"], 1.0, places=6)
        drawer = self.candidate(
            self.match_with([BOX_DRAWER], req=dict(REQ_OK, closure_type="抽屉")), "BOX-DRAWER")
        self.assertEqual(drawer["status"], "matched", "抽屉+拉带 与 抽屉 应命中")
        plain = self.candidate(self.match_with([BOX_PLAIN]), "BOX-PLAIN")
        self.assertEqual(plain["status"], "rejected", "天地盖 与 磁吸 无交集，必须淘汰")

    def test_b6_v_groove_supported_but_not_required_scores_point_six(self):
        item = self.candidate(self.match_with([BOX_E], req=dict(REQ_OK, v_groove="否")), "BOX-E")
        self.assertAlmostEqual(item["dimension_scores"]["v_groove"], 0.6, places=6,
                               msg="需求不要 V 槽、盒型支持 → 按 0.6 计（Spec §2.3.5）")

    def test_b7_v_groove_required_but_unsupported_scores_zero(self):
        item = self.candidate(self.match_with([BOX_F]), "BOX-F")
        self.assertAlmostEqual(item["dimension_scores"]["v_groove"], 0.0, places=6)
        self.assertEqual(item["status"], "matched", "V 槽不是硬门槛，只降分不淘汰")
        self.assertIn("v_groove_required_but_unsupported", item["reject_reasons"])


# --------------------------------------------------------------------------- #
# C. 硬门槛淘汰
# --------------------------------------------------------------------------- #
class CHardGateRejection(MatchCase):
    def test_c1_closure_mismatch_is_rejected(self):
        item = self.candidate(self.match_with([BOX_B]), "BOX-B")
        self.assertEqual(item["status"], "rejected")
        self.assertIn("closure_type_mismatch", item["reject_reasons"])
        self.assertFalse(item["can_confirm"])

    def test_c2_fit_clearance_beyond_tolerance_is_rejected(self):
        item = self.candidate(self.match_with([BOX_C]), "BOX-C")
        self.assertEqual(item["status"], "rejected")
        self.assertIn("fit_clearance_out_of_tolerance", item["reject_reasons"])

    def test_c3_fit_clearance_exactly_at_tolerance_passes(self):
        item = self.candidate(self.match_with([BOX_TOL]), "BOX-TOL")
        self.assertEqual(item["status"], "matched",
                         "|2.0 - 1.5| = 0.5 正好等于容差，属于通过（Spec §2.3.2）")
        self.assertAlmostEqual(item["dimension_scores"]["fit_clearance"], 1.0, places=6)

    def test_c4_soft_dimension_miss_does_not_reject(self):
        item = self.candidate(self.match_with([BOX_A], req=dict(REQ_OK, face_paper_gsm=999)), "BOX-A")
        self.assertEqual(item["status"], "matched", "克重不是硬门槛，只降分")
        self.assertLess(item["dimension_scores"]["face_paper_gsm"], 1.0)

    def test_c5_rejected_candidate_keeps_reasons_and_scores(self):
        item = self.candidate(self.match_with([BOX_B]), "BOX-B")
        self.assertEqual(item["status"], "rejected")
        self.assertIn("closure_type", item["dimension_scores"],
                      "被淘汰也要给出分项分，方便工艺经理看为什么")


# --------------------------------------------------------------------------- #
# D. 缺输入不许装成匹配成功
# --------------------------------------------------------------------------- #
class DMissingInput(MatchCase):
    def test_d1_missing_required_closure_blocks_confirmation(self):
        req = dict(REQ_OK)
        req.pop("closure_type")
        result = self.match_with([BOX_A], req=req)
        item = self.candidate(result, "BOX-A")
        self.assertEqual(item["status"], "needs_input")
        self.assertFalse(item["can_confirm"])
        self.assertIn("closure_type", result["missing_inputs"])
        self.assertFalse(result["inputs_complete"])

    def test_d2_missing_required_size_axis_blocks_confirmation(self):
        req = dict(REQ_OK)
        req.pop("inner_length")
        result = self.match_with([BOX_A], req=req)
        item = self.candidate(result, "BOX-A")
        self.assertEqual(item["status"], "needs_input")
        self.assertIn("inner_length", result["missing_inputs"])

    def test_d3_missing_optional_clearance_still_confirmable(self):
        req = dict(REQ_OK)
        req.pop("fit_clearance")
        result = self.match_with([BOX_A], req=req)
        item = self.candidate(result, "BOX-A")
        self.assertEqual(item["status"], "matched",
                         "配合间隙是选填，缺它不该把候选打成 needs_input")
        self.assertTrue(item["can_confirm"])
        self.assertIn("fit_clearance", result["missing_inputs"])
        self.assertFalse(result["inputs_complete"])

    def test_d4_missing_dimension_scores_zero_not_full(self):
        req = dict(REQ_OK)
        req.pop("closure_type")
        result = self.match_with([BOX_A], req=req)
        item = self.candidate(result, "BOX-A")
        self.assertAlmostEqual(item["dimension_scores"]["closure_type"], 0.0, places=6,
                               msg="缺失维度必须按 0 分计入，不能跳过、更不能给满分")
        self.assertLessEqual(item["total_score"], 0.8001,
                             "其他四维满分时总分最多 0.80（0.30+0.25+0.15+0.10）")

    def test_d5_missing_inputs_limited_to_match_keys(self):
        result = self.match_with([BOX_A], req={"inner_length": 200})
        for key in result["missing_inputs"]:
            self.assertIn(key, MATCH_INPUT_KEYS,
                          "missing_inputs 只允许出现匹配真正需要的 7 个键（Spec §2.4）")


# --------------------------------------------------------------------------- #
# E. 排序与确定性
# --------------------------------------------------------------------------- #
class EOrderingAndDeterminism(MatchCase):
    def test_e1_same_input_same_output(self):
        self.snapshot([BOX_A, BOX_B, BOX_C, BOX_D, BOX_E, BOX_F])
        module = self.engine()
        first = module.match_box_types(dict(REQ_OK))
        second = module.match_box_types(dict(REQ_OK))
        self.assertEqual(json.dumps(first, ensure_ascii=False, sort_keys=True),
                         json.dumps(second, ensure_ascii=False, sort_keys=True),
                         "同输入必须逐字同输出")

    def test_e2_out_of_range_candidate_never_first(self):
        result = self.match_with([BOX_R, BOX_S], req=dict(REQ_OK, inner_length=403.2))
        inline = self.candidate(result, "BOX-R")
        outside = self.candidate(result, "BOX-S")
        self.assertFalse(inline["out_of_range"])
        self.assertTrue(outside["out_of_range"])
        self.assertGreater(outside["total_score"], inline["total_score"],
                           "构造用例要求：越界候选分更高，才检验得出排序规则")
        self.assertEqual(result["candidates"][0]["box_type_code"], "BOX-R",
                         "存在尺寸完全合规的候选时，越界候选不得排第一（Spec §2.5）")

    def test_e3_rejected_sorts_after_matched(self):
        result = self.match_with([BOX_B, BOX_A])
        order = [c["box_type_code"] for c in result["candidates"]]
        self.assertEqual(order, ["BOX-A", "BOX-B"])

    def test_e4_ties_break_by_box_type_code(self):
        result = self.match_with([BOX_Y, BOX_X])
        self.assertEqual([c["box_type_code"] for c in result["candidates"]], ["BOX-X", "BOX-Y"])

    def test_e5_all_rejected_asks_for_new_tooling(self):
        result = self.match_with([BOX_B])
        self.assertTrue(result["needs_new_tooling"])
        self.assertIn(result.get("new_tooling_reason"), ("all_rejected", "no_box_type"))

    def test_e6_engine_version_is_reported(self):
        result = self.match_with([BOX_A])
        self.assertEqual(result["engine_version"], "packaging_match_v1")


# --------------------------------------------------------------------------- #
# F. 落库与四态决策
# --------------------------------------------------------------------------- #
class DecisionCase(MatchCase):
    def setUp(self):
        super().setUp()
        self._backend = meta_backend._backend
        self.data_dir = pathlib.Path(tempfile.mkdtemp())
        meta_backend._backend = meta_backend.JsonMetaBackend(self.data_dir)
        self.project_id = "pkgmatch0001"
        self.requirement_no = "REQ-PKG-0001"

    def tearDown(self):
        meta_backend._backend = self._backend
        super().tearDown()

    def save_requirement(self, **over):
        data = {"industry": "packaging", **REQ_OK}
        data.update(over)
        store.save_requirement(self.project_id, {
            "project_id": self.project_id, "requirement_no": self.requirement_no,
            "title": "包装匹配用例", "status": "pending_confirmation", "data": data})

    def repo_fn(self, name):
        self.assertTrue(hasattr(da_repo, name), "da_repo 缺 %s()（Spec §4.5）" % name)
        return getattr(da_repo, name)

    def run_match(self, boxes=None):
        self.snapshot(boxes or [BOX_A, BOX_B])
        return self.engine().run_box_match(self.project_id, self.requirement_no)

    def decide(self, decision, code=None, **kw):
        self.engine().decide_box_match(
            self.project_id, self.requirement_no, decision, code,
            actor={"username": "wangjingli", "role": "process_manager"}, **kw)


class FPersistAndDecide(DecisionCase):
    def test_f1_run_persists_one_row_idempotently(self):
        self.save_requirement()
        self.run_match()
        self.run_match()
        rows = da_db.query_all("SELECT * FROM wip_packaging_box_match WHERE project_id = ?",
                               (self.project_id,))
        self.assertEqual(len(rows), 1, "同一 (project_id, requirement_no) 只能有一行")
        self.assertEqual(rows[0]["decision"], "pending")
        self.assertEqual(rows[0]["engine_version"], "packaging_match_v1")
        self.assertTrue(json.loads(rows[0]["candidates_json"]))

    def test_f2_confirm_records_actor_and_time(self):
        self.save_requirement()
        self.run_match()
        self.decide("confirmed", "BOX-A")
        record = self.engine().load_box_match(self.project_id, self.requirement_no)
        self.assertEqual(record["decision"], "confirmed")
        self.assertEqual(record["confirmed_box_type"], "BOX-A")
        self.assertEqual(record["confirmed_by"], "wangjingli")
        self.assertTrue(record["confirmed_at"])

    def test_f3_confirm_rejected_candidate_is_refused(self):
        self.save_requirement()
        self.run_match()
        module = self.engine()
        with self.assertRaises(Exception) as ctx:
            self.decide("confirmed", "BOX-B")
        self.assertEqual(getattr(ctx.exception, "status_code", 409), 409)
        self.assertIn("box_type_not_confirmable", str(ctx.exception))
        record = module.load_box_match(self.project_id, self.requirement_no)
        self.assertEqual(record["decision"], "pending", "被拒的确认不得改状态")

    def test_f4_confirm_unknown_box_type_is_refused(self):
        self.save_requirement()
        self.run_match()
        with self.assertRaises(Exception):
            self.decide("confirmed", "BOX-NOT-EXIST")

    def test_f5_return_requires_missing_inputs(self):
        self.save_requirement(closure_type="")
        self.run_match()
        self.decide("returned")
        record = self.engine().load_box_match(self.project_id, self.requirement_no)
        self.assertEqual(record["decision"], "returned")
        self.assertIn("closure_type", json.loads(record["missing_inputs_json"] or "[]"))

    def test_f6_new_tooling_is_allowed_when_no_candidate(self):
        self.save_requirement()
        self.snapshot([BOX_B])
        module = self.engine()
        result = module.run_box_match(self.project_id, self.requirement_no)
        self.assertTrue(result["needs_new_tooling"])
        module.decide_box_match(self.project_id, self.requirement_no, "new_tooling", None,
                               actor={"username": "wangjingli", "role": "process_manager"})
        record = module.load_box_match(self.project_id, self.requirement_no)
        self.assertEqual(record["decision"], "new_tooling")
        task = record.get("new_tooling_task") or {}
        self.assertEqual(task.get("industry"), "packaging")
        self.assertEqual(task.get("project_id"), self.project_id)
        self.assertEqual(task.get("requirement_no"), self.requirement_no)
        self.assertTrue(task.get("reason"))

    def test_f7_repeat_decision_is_idempotent(self):
        self.save_requirement()
        self.run_match()
        self.decide("confirmed", "BOX-A")
        first = self.engine().load_box_match(self.project_id, self.requirement_no)["confirmed_at"]
        self.decide("confirmed", "BOX-A")
        second = self.engine().load_box_match(self.project_id, self.requirement_no)["confirmed_at"]
        self.assertEqual(first, second, "重复确认同一盒型必须幂等")

    def test_f8_switch_records_previous_choice_in_audit(self):
        self.save_requirement()
        self.run_match()
        self.decide("confirmed", "BOX-A")
        self.decide("confirmed", "BOX-B")
        module = self.engine()
        record = module.load_box_match(self.project_id, self.requirement_no)
        self.assertEqual(record["confirmed_box_type"], "BOX-B")
        actions = [row["action"] for row in da_repo.box_match_audit(self.project_id, self.requirement_no)]
        self.assertIn("switched", actions, "换盒型必须单独留一条 switched 审计")
        switched = [r for r in da_repo.box_match_audit(self.project_id, self.requirement_no)
                    if r["action"] == "switched"][0]
        detail = json.loads(switched["detail_json"] or "{}")
        self.assertEqual(detail.get("from_box_type"), "BOX-A")
        self.assertEqual(detail.get("to_box_type"), "BOX-B")

    def test_f9_decision_writes_the_platform_audit(self):
        self.save_requirement()
        self.run_match()
        with mock.patch.object(store, "audit") as spy:
            self.decide("confirmed", "BOX-A")
        actions = [call.args[1] for call in spy.call_args_list]
        self.assertTrue(any("packaging_box_match" in str(a) for a in actions),
                        "决策必须写进平台项目时间线：store.audit(...packaging_box_match...)")


# --------------------------------------------------------------------------- #
# G. 确认保护与失效
# --------------------------------------------------------------------------- #
class GConfirmationProtection(DecisionCase):
    def test_g1_rerun_does_not_overwrite_confirmed_choice(self):
        self.save_requirement()
        self.run_match()
        self.decide("confirmed", "BOX-A")
        self.snapshot([BOX_A, BOX_B])
        self.engine().run_box_match(self.project_id, self.requirement_no)
        record = self.engine().load_box_match(self.project_id, self.requirement_no)
        self.assertEqual(record["confirmed_box_type"], "BOX-A", "重新匹配不得覆盖人工确认的盒型")
        self.assertEqual(record["decision"], "confirmed")
        self.assertFalse(record["stale"])

    def test_g2_requirement_change_marks_stale_without_losing_choice(self):
        self.save_requirement()
        self.run_match()
        self.decide("confirmed", "BOX-A")
        self.save_requirement(inner_width=260, inner_length=300)
        record = self.engine().load_box_match(self.project_id, self.requirement_no)
        self.assertTrue(record["stale"], "关键匹配输入改了必须提示重新确认")
        self.assertIn("inner_width", record["stale_reasons"])
        self.assertIn("inner_length", record["stale_reasons"])
        self.assertEqual(record["confirmed_box_type"], "BOX-A", "stale 不得抹掉确认值")

    def test_g3_reparse_of_requirement_does_not_overwrite_choice(self):
        self.save_requirement()
        self.run_match()
        self.decide("confirmed", "BOX-A")
        # 模拟第 2 批「AI 重新解析」再存一次需求单：字段变了，但人工确认必须留住。
        self.save_requirement(inner_height=100, closure_type="磁吸", face_paper_gsm=250)
        record = self.engine().load_box_match(self.project_id, self.requirement_no)
        self.assertEqual(record["confirmed_box_type"], "BOX-A")
        self.assertEqual(record["decision"], "confirmed")

    def test_g4_unknown_project_returns_empty_record(self):
        self.snapshot([BOX_A])
        record = self.engine().load_box_match("nosuchproject", "")
        self.assertEqual(record["decision"], "none")
        self.assertEqual(record["candidates"], [])


# --------------------------------------------------------------------------- #
# H. 审计只增不改
# --------------------------------------------------------------------------- #
class HAuditAppendOnly(DecisionCase):
    def test_h1_audit_rows_are_ordered_and_complete(self):
        self.save_requirement()
        self.run_match()
        self.decide("confirmed", "BOX-A")
        self.decide("returned")
        rows = da_repo.box_match_audit(self.project_id, self.requirement_no)
        self.assertTrue(rows, "da_repo 缺 box_match_audit()（Spec §4.5）")
        ids = [row["audit_id"] for row in rows]
        self.assertEqual(ids, sorted(ids), "审计必须按 audit_id 升序读回")
        self.assertIn("confirmed", [r["action"] for r in rows])
        for row in rows:
            self.assertTrue(row["at"])
            self.assertIsNotNone(row["actor"])

    def test_h2_no_mutating_api_for_audit(self):
        names = {n for n in dir(da_repo)}
        for forbidden in ("update_box_match_audit", "delete_box_match_audit",
                          "clear_box_match_audit"):
            self.assertNotIn(forbidden, names, "审计表只允许 INSERT（Spec §3.4）")
        for required in ("append_box_match_audit", "box_match_audit",
                         "save_box_match", "load_box_match", "update_box_match_decision"):
            self.assertIn(required, names, "da_repo 缺 %s()（Spec §4.5）" % required)

    def test_h3_schema_has_the_two_tables(self):
        text = SCHEMA_SQL.read_text(encoding="utf-8")
        self.assertIn("wip_packaging_box_match", text)
        self.assertIn("wip_packaging_box_match_audit", text)
        with sqlite3.connect(str(self.db_file)) as conn:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("wip_packaging_box_match", names, "da_schema.sql 必须建 wip_packaging_box_match")
        self.assertIn("wip_packaging_box_match_audit", names)


# --------------------------------------------------------------------------- #
# I. 接口与角色门禁
# --------------------------------------------------------------------------- #
class IApiAndRoles(MatchCase):
    def test_i1_routes_are_registered(self):
        module = self.engine()
        self.assertTrue(hasattr(module, "BOX_MATCH_DECIDE_ROLES"),
                        "服务层必须有 BOX_MATCH_DECIDE_ROLES（Spec §4.5）")
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        for suffix in ("/requirement/box-match",
                       "/requirement/box-match/decision"):
            self.assertIn(suffix, source, "main.py 缺路由 %s" % suffix)
        self.assertIn("BOX_MATCH_DECIDE_ROLES", source,
                      "main.py 必须引用服务层的决策角色常量，不得另抄一份")

        import tech_app.backend.main as main  # noqa: F401  (导入较慢，只在需要时)
        paths = {route.path for route in main.app.routes}
        self.assertIn("/api/projects/{project_id}/requirement/box-match", paths)
        self.assertIn("/api/projects/{project_id}/requirement/box-match/decision", paths)

    def test_i2_decide_roles_closure(self):
        roles = set(self.engine().BOX_MATCH_DECIDE_ROLES)
        self.assertEqual(roles, {"process_manager", "process_director", "admin"})
        for outsider in ("viewer", "engineer", "sales_manager", "finance_manager", "general_manager"):
            self.assertNotIn(outsider, roles, "%s 不该能确认盒型" % outsider)

    def test_i3_non_packaging_industry_is_refused(self):
        self._backend = meta_backend._backend
        self.data_dir = pathlib.Path(tempfile.mkdtemp())
        meta_backend._backend = meta_backend.JsonMetaBackend(self.data_dir)
        try:
            store.save_requirement("semi00000001", {
                "project_id": "semi00000001", "requirement_no": "REQ-SEMI-1",
                "title": "半导体用例", "status": "pending_confirmation",
                "data": {"industry": "semiconductor", "product_name": "某芯片"}})
            self.snapshot([BOX_A])
            module = self.engine()
            with self.assertRaises(Exception) as ctx:
                module.run_box_match("semi00000001", "REQ-SEMI-1")
            self.assertEqual(getattr(ctx.exception, "status_code", 400), 400,
                             "非包装行业调用盒型匹配必须被拒（Spec §4）")
        finally:
            meta_backend._backend = self._backend


# --------------------------------------------------------------------------- #
# J. 非回归护栏
# --------------------------------------------------------------------------- #
class JNonRegression(MatchCase):
    def test_j1_packaging_required_keys_unchanged(self):
        self.assertEqual(len(industry_templates.field_keys("packaging")), 64)
        self.assertEqual(industry_templates.required_keys("packaging"), {
            "box_type", "closure_type", "face_paper_gsm", "inner_height",
            "inner_length", "inner_width", "packaging_category",
            "packaging_product_name", "quote_quantity", "v_groove"})

    def test_j2_engine_never_calls_an_llm(self):
        source = None
        module = self.engine()
        source = inspect.getsource(module)
        for forbidden in ("llm_client", "requests", "urllib", "httpx", "psycopg"):
            self.assertNotIn(forbidden, source,
                             "匹配引擎必须是确定性纯函数，不得出现 %s（Spec §2.5）" % forbidden)

    def test_j3_match_does_not_write_the_database(self):
        self.snapshot([BOX_A])
        module = self.engine()
        module.match_box_types(dict(REQ_OK))
        with sqlite3.connect(str(self.db_file)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM wip_packaging_box_match").fetchone()[0]
        self.assertEqual(count, 0, "match_box_types 是纯函数，不该自己落库")

    def test_j4_seeded_box_types_untouched(self):
        from tech_app.backend.storage import da_seed_packaging as seed
        self.assertEqual(len(seed.BOX_TYPES), 12)
        self.assertEqual(len(seed.MATCH_WEIGHTS), 5)
        self.assertEqual({w["dimension"] for w in seed.MATCH_WEIGHTS}, set(DIMENSIONS))

    def test_j5_frontend_panel_is_wired(self):
        source = CONFIRM_JS.read_text(encoding="utf-8", errors="replace")
        self.assertIn("box-match", source, "1.2 需求确认页要接盒型匹配接口")

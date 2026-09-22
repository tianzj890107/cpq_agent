"""红测：盒型候选必须按相似度降序，并且必须自带"选它能不能往下走"。

Spec：`docs/specs/packaging-box-candidate-rank-and-runnability.md`
血缘：`packaging-box-type-matching.md`（五维打分与排序键的唯一出处）、
`packaging-parametric-bom.md`（`no_part_template` 409 的出处）。

现状缺口（2026-09-22 在 34 上真跑实测，不是推断。项目 `afe9e844f2ec` /
需求单 `REQ-AFE9E844F2EC`、图 `酒盒.dwg`、一键解析 8/8 completed 之后）：

  · `POST …/requirement/box-match` 返回 14 个候选，**`matched` 组内不是按相似度降序**：
    0.667（圆型筒盒）/ 0.767（心形盒）/ 0.800 / 0.800 / 0.800 / 0.900 / 0.900；
    `rejected` 组内同样是 0.533 / 0.533 / 0.663 / 0.663 / 0.663 / 0.333。
    而“第一个候选”正是演示时最容易被点下去的那个 —— 点下去就是那个 0.667 的圆型筒盒。
  · 候选里**没有任何**“这个盒型有没有部件模板”的字段。两个 0.900 的盒型
    （`YT-RB-01003-A` 双层天地盖盒 / `YT-RB-05001-A` 六角异形盒）确认之后，
    `POST …/requirement/packaging-bom` 直接 **409**「盒型 YT-RB-01003-A 没有部件模板，无法展开」
    （`code=no_part_template`）；而 0.800 的 `YT-RB-01001-A` 能出 31 行 BOM。
    也就是说“选哪个能往下走”只有在**确认之后、在另一个接口上**才知道 —— 用户已经在
    确认这一步失去了选择权。

纪律：只读源码 + 快照缓存 + 假 store；不连 PG、不发 HTTP、不写任何文件。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import copy
import importlib
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend import config  # noqa: E402
from tech_app.backend.storage import da_db, kb_repo, meta_backend, store  # noqa: E402

DIMENSIONS = ("size_range", "fit_clearance", "face_paper_gsm", "closure_type", "v_groove")
DEFAULT_WEIGHTS = (
    {"dimension": "size_range", "weight": 0.30, "hard_gate": 0},
    {"dimension": "fit_clearance", "weight": 0.25, "hard_gate": 1},
    {"dimension": "face_paper_gsm", "weight": 0.15, "hard_gate": 0},
    {"dimension": "closure_type", "weight": 0.20, "hard_gate": 1},
    {"dimension": "v_groove", "weight": 0.10, "hard_gate": 0},
)
REQ_OK = {"inner_length": 200, "inner_width": 150, "inner_height": 80,
          "closure_type": "磁吸", "v_groove": "是", "face_paper_gsm": 200,
          "fit_clearance": 1.5}

#: 排序键（Spec `packaging-box-type-matching.md` §3 的键 + 本节新增的两条不变量）。
STATUS_RANK = {"matched": 0, "needs_input": 1, "rejected": 2}


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


#: 满分盒型（五维全中）。
BOX_BEST = _box("BOX-BEST")
#: 只在**非硬门槛**的克重维差一档 —— 仍 matched、分数低，用来检验组内降序。
BOX_WEAK = _box("BOX-WEAK", face_paper_gsm="100-120")


def _template(code, part_code="P01", name="盖面"):
    return {"box_type_code": code, "part_code": part_code, "part_name": name,
            "component": "上盖", "material": "灰板 2.0mm", "quantity": 1,
            "size_length_expr": "L+4t+2c", "size_width_expr": "W+4t+2c",
            "bom_category": "box_part", "industry": "packaging"}


class CandidateCase(unittest.TestCase):
    """共用：可替换快照 + 独立临时 SQLite（照 `test_packaging_box_type_matching_red` 的范式）。"""

    def setUp(self):
        self._cache = dict(kb_repo._CACHE)
        self._da_path = config.DA_DB_PATH
        self.db_file = pathlib.Path(tempfile.mkdtemp()) / "rank.sqlite3"
        self._patch_da = mock.patch.object(config, "DA_DB_PATH", self.db_file)
        self._patch_da.start()
        da_db.init_db(self.db_file)

    def tearDown(self):
        self._patch_da.stop()
        da_db.close_conn()
        kb_repo._CACHE.clear()
        kb_repo._CACHE.update(self._cache)

    def snapshot(self, boxes, templates=()):
        kb_repo._CACHE["version"] = "probe-rank"
        kb_repo._CACHE["tables"] = {
            "kb_packaging_box_type": [copy.deepcopy(b) for b in boxes],
            "kb_packaging_match_weight": [dict(w) for w in DEFAULT_WEIGHTS],
            "kb_packaging_part_template": [dict(t) for t in templates],
        }

    def engine(self):
        try:
            module = importlib.import_module("tech_app.backend.services.packaging_match")
        except Exception as exc:                      # noqa: BLE001
            self.fail("缺少 tech_app/backend/services/packaging_match.py：%s" % exc)
        return module

    def candidates(self, boxes, templates=()):
        self.snapshot(boxes, templates)
        result = self.engine().match_box_types(dict(REQ_OK))
        return result["candidates"]


# --------------------------------------------------------------------------- #
# A 组：同一个 status 内必须按 total_score 降序
# --------------------------------------------------------------------------- #
class AWithinStatusOrder(CandidateCase):
    def test_a1_matched_group_is_sorted_by_score_desc(self):
        # 快照里把**低分**的排在前面：只有真的按分数排序，才可能把 BOX-BEST 顶到第一个。
        cands = self.candidates([BOX_WEAK, BOX_BEST])
        codes = [c["box_type_code"] for c in cands if c["status"] == "matched"]
        self.assertEqual(["BOX-BEST", "BOX-WEAK"], codes,
                         "matched 组内必须按 total_score 降序（Spec §2.1）；"
                         "实测顺序 %s" % codes)

    def test_a2_full_sort_key_holds_for_every_pair(self):
        cands = self.candidates([BOX_WEAK, BOX_BEST])
        key = lambda c: (STATUS_RANK.get(c["status"], 9), bool(c.get("out_of_range")),
                         -float(c.get("total_score") or 0), c["box_type_code"])
        keys = [key(c) for c in cands]
        self.assertEqual(sorted(keys), keys,
                         "候选顺序必须等于排序键 (status, out_of_range, -total_score, code)（Spec §2.1）；"
                         "实测 %s" % [(c["box_type_code"], c["status"], c.get("total_score")) for c in cands])

    def test_a3_score_order_survives_a_third_candidate(self):
        third = _box("BOX-MID", face_paper_gsm="157-250", v_groove="是", fit_clearance=2.0)
        cands = self.candidates([third, BOX_WEAK, BOX_BEST])
        scores = [float(c["total_score"]) for c in cands if c["status"] == "matched"]
        self.assertEqual(sorted(scores, reverse=True), scores,
                         "matched 组内分数必须单调不增（Spec §2.1）；实测 %s" % scores)


# --------------------------------------------------------------------------- #
# B 组：候选必须自带"选它能不能往下走"
# --------------------------------------------------------------------------- #
class BRunnabilityOnTheCandidate(CandidateCase):
    def test_b1_candidate_carries_part_template_flag(self):
        cands = self.candidates([BOX_BEST, BOX_WEAK], templates=[_template("BOX-BEST")])
        for c in cands:
            self.assertIn("part_template_available", c,
                          "%s 缺 part_template_available（Spec §2.2）" % c["box_type_code"])
            self.assertIn("part_template_total", c,
                          "%s 缺 part_template_total（Spec §2.2）" % c["box_type_code"])

    def test_b2_flag_is_true_only_when_a_template_exists(self):
        cands = self.candidates([BOX_BEST, BOX_WEAK], templates=[_template("BOX-BEST")])
        by_code = {c["box_type_code"]: c for c in cands}
        self.assertTrue(by_code["BOX-BEST"]["part_template_available"],
                        "BOX-BEST 在 kb_packaging_part_template 里有模板，必须为 true")
        self.assertFalse(by_code["BOX-WEAK"]["part_template_available"],
                         "BOX-WEAK 没有模板，必须为 false（Spec §2.2）；"
                         "现在确认它之后 BOM 会 409 no_part_template")
        self.assertGreaterEqual(int(by_code["BOX-BEST"]["part_template_total"]), 1)

    def test_b3_flag_is_false_for_every_candidate_when_kb_has_no_templates(self):
        cands = self.candidates([BOX_BEST, BOX_WEAK])
        self.assertEqual([False] * len(cands),
                         [bool(c.get("part_template_available")) for c in cands],
                         "没有任何模板时，所有候选都必须是 false（Spec §2.2）")


# --------------------------------------------------------------------------- #
# C 组：确认一个"没有模板"的盒型，必须留下可判分支的痕迹
# --------------------------------------------------------------------------- #
class CConfirmWarnsAboutNoTemplate(CandidateCase):
    """确认一个没有部件模板的盒型，必须留下可判分支的痕迹（不用等到 BOM 409）。"""

    PROJECT = "pkgrank00001"
    REQUIREMENT = "REQ-RANK-0001"

    def setUp(self):
        super().setUp()
        self._backend = meta_backend._backend
        self.data_dir = pathlib.Path(tempfile.mkdtemp())
        meta_backend._backend = meta_backend.JsonMetaBackend(self.data_dir)

    def tearDown(self):
        meta_backend._backend = self._backend
        super().tearDown()

    def _run(self, boxes, templates):
        self.snapshot(boxes, templates)
        store.save_requirement(self.PROJECT, {
            "project_id": self.PROJECT, "requirement_no": self.REQUIREMENT,
            "title": "候选可运行性用例", "status": "pending_confirmation",
            "data": {"industry": "packaging", **REQ_OK}})
        return self.engine().run_box_match(self.PROJECT, self.REQUIREMENT)

    def _decide(self, code):
        return self.engine().decide_box_match(
            self.PROJECT, self.REQUIREMENT, "confirmed", code,
            actor={"username": "wangjingli", "role": "process_manager"}, note="红测")

    def test_c1_confirmed_candidate_records_missing_template(self):
        self._run([BOX_BEST, BOX_WEAK], [_template("BOX_BEST")])
        out = self._decide("BOX-WEAK")
        blob = repr(out)
        self.assertIn("box_type_without_part_template", blob,
                      "确认一个没有部件模板的盒型时，返回里必须带 "
                      "code=box_type_without_part_template 的可判分支警告（Spec §2.3）；"
                      "现在这件事只在 BOM 那一步以 409「没有部件模板，无法展开」出现")

    def test_c2_confirmed_candidate_with_template_has_no_such_warning(self):
        self._run([BOX_BEST, BOX_WEAK], [_template("BOX_BEST")])
        out = self._decide("BOX-BEST")
        self.assertNotIn("box_type_without_part_template", repr(out),
                         "有模板的盒型不许出现这条警告（Spec §2.3）")


if __name__ == "__main__":
    unittest.main(verbosity=2)

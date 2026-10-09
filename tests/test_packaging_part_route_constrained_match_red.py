"""红测：图纸零件 ↔ 库内零件/工艺路线的**受约束匹配**。

Spec：`docs/specs/packaging-part-route-constrained-match.md`（唯一口径）。

**现状缺口（2026-10-09 只读复核，不是推断）**：

  · `tech_app/backend/services/packaging_part_route_match.py` 不存在 —— 全仓没有任何
    「受约束匹配」实现；库里唯一逐件路线来源（DA CLM 头表 `name='<零件名>工艺路线'`）今天
    只被 `da_process_routing.for_row()` 的**逐字名**桥接查询碰到，没有归一化、没有外购
    短路、没有 `unbound` 原因；
  · 图上 28 件 × DA 26 条酒盒路线：15 件逐字命中、11 件只差归一化（5 件「忖纸」对应库内
    「衬纸」、6 件「左盒/右盒」对应库内「左盖/右盖」）、2 件外购（顶托EVA、磁铁）；
  · 纯文本相似度会错配：`底板` 与 `底板面纸` 共享「底板」但工序不同
    （`开料→模切` vs `开料→UV印刷→覆哑膜→丝印UV→浮雕击凸→模切→包盒`）。

纪律：只读源码 + 纯函数 + 假桥接（monkeypatch `urlopen`）；不连 PG / SQLite 生产库、不发
真 HTTP、不写文件、不落库、不调模型。**禁止为了让红测转绿而修改本文件**；口径变化请改 Spec。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PKG = "tech_app.backend.services.packaging_part_route_match"
ROUTE_PY = ROOT / "tech_app" / "backend" / "services" / "da_process_routing.py"
INSTANCES_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_process_instances.py"
MATCH_FILE = ROOT / "tech_app" / "backend" / "services" / "packaging_part_route_match.py"

# --------------------------------------------------------------------------- #
# 冻结常量（Spec §2.1，逐字）
# --------------------------------------------------------------------------- #
ENGINE_VERSION = "packaging-part-route-match/1"
MATCH_METHODS = ("exact_code", "exact_name", "normalized_name",
                 "constrained_similarity", "unbound")
BINDING_STATUSES = ("matched", "unbound", "skipped_external")
UNBOUND_REASONS = ("no_candidate", "position_conflict", "kind_conflict",
                   "below_threshold", "ambiguous", "duplicate_name")
EXTERNAL_WORDS = ("外购", "采购")
NAME_ALIASES = (("忖纸", "衬纸"), ("左盒", "左盖"), ("右盒", "右盖"))
ROUTE_SUFFIXES = ("工艺路线", "加工工艺路线")
DIRECTION_PAIRS = (("左", "右"), ("上", "下"), ("前", "后"), ("顶托", "底托"),
                   ("内盒", "外盒"))
KIND_WORDS = ("面纸", "衬纸", "灰板", "内卡", "贴牌", "EVA", "磁铁", "衬板",
              "盒背", "标牌")
SIMILARITY_THRESHOLD = 0.5

MATCH_KEYS = ("status", "match_method", "part_code", "part_name", "matched_code",
              "matched_name", "normalized", "score", "reason", "candidates_total",
              "rejected")
PARTS_KEYS = ("engine_version", "bindings", "matched", "unbound",
              "skipped_external", "unbound_parts", "duplicate_targets")

# --------------------------------------------------------------------------- #
# 真实夹具 1：DA CLM 26 条酒盒路线（`md_clm_process_routing_base_info`，2026-10-09 只读复核）
#   行名 = `<零件名>工艺路线`，`product_item_code = YT-JW-XR21-700ML-NN`
# --------------------------------------------------------------------------- #
DA_ROUTES = (
    ("YT-JW-XR21-700ML-01", "左盖面纸"), ("YT-JW-XR21-700ML-02", "右盖面纸"),
    ("YT-JW-XR21-700ML-03", "左盖外盒里层灰板1"), ("YT-JW-XR21-700ML-04", "右盖外盒里层灰板1"),
    ("YT-JW-XR21-700ML-05", "左盖外盒外层衬板"), ("YT-JW-XR21-700ML-06", "右盖外盒外层衬板"),
    ("YT-JW-XR21-700ML-07", "左盖外盒里层灰板2"), ("YT-JW-XR21-700ML-08", "右盖外盒里层灰板2"),
    ("YT-JW-XR21-700ML-09", "底板"), ("YT-JW-XR21-700ML-10", "内皮壳衬纸"),
    ("YT-JW-XR21-700ML-11", "底板面纸"), ("YT-JW-XR21-700ML-12", "内盒1面纸"),
    ("YT-JW-XR21-700ML-13", "内盒1灰板"), ("YT-JW-XR21-700ML-14", "内盒2灰板"),
    ("YT-JW-XR21-700ML-15", "内盒3灰板"), ("YT-JW-XR21-700ML-16", "内盒1衬纸"),
    ("YT-JW-XR21-700ML-17", "内盒3衬纸"), ("YT-JW-XR21-700ML-18", "内盒2面纸"),
    ("YT-JW-XR21-700ML-19", "内盒3面纸"), ("YT-JW-XR21-700ML-20", "贴牌"),
    ("YT-JW-XR21-700ML-21", "顶托面纸"), ("YT-JW-XR21-700ML-22", "底托面纸"),
    ("YT-JW-XR21-700ML-23", "顶托衬纸"), ("YT-JW-XR21-700ML-24", "底托衬纸"),
    ("YT-JW-XR21-700ML-25", "底托灰板"), ("YT-JW-XR21-700ML-26", "内卡"),
)
CANDIDATES = [{"name": base + "工艺路线", "code": code} for code, base in DA_ROUTES]
CODE_OF = {base: code for code, base in DA_ROUTES}

# --------------------------------------------------------------------------- #
# 真实夹具 2：图纸 28 件（34 上 `packaging_business_parts.json` 的 DWG-BP01..BP28 逐字名）
#   expectation：26 matched（15 exact_name + 11 normalized_name）+ 2 skipped_external
# --------------------------------------------------------------------------- #
DRAWING_PARTS = (
    ("DWG-BP01", "左盖面纸", ""), ("DWG-BP02", "右盖面纸", ""),
    ("DWG-BP03", "内盒1面纸", ""), ("DWG-BP04", "内盒1灰板", ""),
    ("DWG-BP05", "内盒2面纸", ""), ("DWG-BP06", "内盒2灰板", ""),
    ("DWG-BP07", "内盒3面纸", ""), ("DWG-BP08", "内盒3灰板", ""),
    ("DWG-BP09", "内盒3忖纸", ""), ("DWG-BP10", "内盒1忖纸", ""),
    ("DWG-BP11", "顶托面纸", ""), ("DWG-BP12", "顶托EVA", "外购，用量1个"),
    ("DWG-BP13", "顶托忖纸", ""), ("DWG-BP14", "内皮壳忖纸", ""),
    ("DWG-BP15", "底板", ""), ("DWG-BP16", "底板面纸", ""),
    ("DWG-BP17", "底托忖纸", ""), ("DWG-BP18", "底托面纸", ""),
    ("DWG-BP19", "底托灰板", ""), ("DWG-BP20", "贴牌", ""),
    ("DWG-BP21", "左盒外盒里层灰板1", ""), ("DWG-BP22", "左盒外盒里层灰板2", ""),
    ("DWG-BP23", "左盒外盒外层衬板", ""), ("DWG-BP24", "右盒外盒外层衬板", ""),
    ("DWG-BP25", "右盒外盒里层灰板1", ""), ("DWG-BP26", "右盒外盒里层灰板2", ""),
    ("DWG-BP27", "磁铁", "外购，用量8/套"), ("DWG-BP28", "内卡", ""),
)
EXPECTED_EXACT = ("左盖面纸", "右盖面纸", "内盒1面纸", "内盒1灰板", "内盒2面纸",
                  "内盒2灰板", "内盒3面纸", "内盒3灰板", "顶托面纸", "底板",
                  "底板面纸", "底托面纸", "底托灰板", "贴牌", "内卡")
EXPECTED_NORMALIZED = ("内盒3忖纸", "内盒1忖纸", "顶托忖纸", "内皮壳忖纸", "底托忖纸",
                       "左盒外盒里层灰板1", "左盒外盒里层灰板2", "左盒外盒外层衬板",
                       "右盒外盒外层衬板", "右盒外盒里层灰板1", "右盒外盒里层灰板2")
EXPECTED_EXTERNAL = ("顶托EVA", "磁铁")


def svc():
    """延迟导入被测模块；缺失时给出可读的失败而不是收集期 ImportError。"""
    try:
        return importlib.import_module(PKG)
    except Exception as exc:  # noqa: BLE001
        raise AssertionError("`%s` 不可用：%r（Spec §2.1）" % (PKG, exc))


def rows():
    out = []
    for code, name, process_text in DRAWING_PARTS:
        part = {"business_part_code": code, "name": name}
        if process_text:
            part["reference"] = {"process_text": process_text}
        out.append(part)
    return out


# --------------------------------------------------------------------------- #
# A 组：归一化（Spec §2.2）
# --------------------------------------------------------------------------- #
class TestANormalize(unittest.TestCase):
    def test_a1_whitespace_and_fullwidth(self):
        m = svc()
        self.assertEqual("内盒1面纸", m.normalize_part_name(" 内盒1面纸 "))
        self.assertEqual("内盒1面纸", m.normalize_part_name("内盒１面纸"))
        self.assertEqual("内盒1面纸", m.normalize_part_name("内盒 1 面纸"))

    def test_a2_route_suffix_stripped(self):
        m = svc()
        self.assertEqual("底板", m.normalize_part_name("底板工艺路线"))
        self.assertEqual("底板", m.normalize_part_name("底板加工工艺路线"))

    def test_a3_aliases_applied(self):
        m = svc()
        self.assertEqual("内皮壳衬纸", m.normalize_part_name("内皮壳忖纸"))
        self.assertEqual("左盖外盒里层灰板1", m.normalize_part_name("左盒外盒里层灰板1"))
        self.assertEqual("右盖外盒外层衬板", m.normalize_part_name("右盒外盒外层衬板"))

    def test_a4_spec_examples(self):
        m = svc()
        self.assertEqual("内盒3衬纸", m.normalize_part_name("内盒3忖纸工艺路线"))
        self.assertEqual("左盖外盒里层灰板1",
                         m.normalize_part_name("左盒外盒里层灰板1工艺路线"))

    def test_a5_empty_after_strip_keeps_original(self):
        m = svc()
        self.assertEqual("", m.normalize_part_name(""))
        self.assertEqual("工艺路线", m.normalize_part_name("工艺路线"))

    def test_a6_idempotent(self):
        m = svc()
        for name in ("内盒3忖纸工艺路线", "左盒外盒里层灰板1", "内皮壳衬纸", "底板",
                     "右盒外盒外层衬板工艺路线"):
            once = m.normalize_part_name(name)
            self.assertEqual(once, m.normalize_part_name(once),
                             "归一化必须幂等：%r" % name)

    def test_a7_frozen_constants(self):
        m = svc()
        self.assertEqual(ENGINE_VERSION, m.ENGINE_VERSION)
        self.assertEqual(MATCH_METHODS, tuple(m.MATCH_METHODS))
        self.assertEqual(BINDING_STATUSES, tuple(m.BINDING_STATUSES))
        self.assertEqual(UNBOUND_REASONS, tuple(m.UNBOUND_REASONS))
        self.assertEqual(EXTERNAL_WORDS, tuple(m.EXTERNAL_WORDS))
        self.assertEqual(NAME_ALIASES, tuple(m.NAME_ALIASES))
        self.assertEqual(ROUTE_SUFFIXES, tuple(m.ROUTE_SUFFIXES))
        self.assertEqual(DIRECTION_PAIRS, tuple(m.DIRECTION_PAIRS))
        self.assertEqual(KIND_WORDS, tuple(m.KIND_WORDS))
        self.assertEqual(SIMILARITY_THRESHOLD, m.SIMILARITY_THRESHOLD)


# --------------------------------------------------------------------------- #
# B 组：判定顺序与方法（Spec §2.4）
# --------------------------------------------------------------------------- #
class TestBMatchOne(unittest.TestCase):
    def test_b1_return_shape(self):
        m = svc()
        got = m.match_one({"name": "底板"}, CANDIDATES)
        self.assertEqual(sorted(MATCH_KEYS), sorted(got.keys()))
        self.assertEqual(26, got["candidates_total"])

    def test_b2_exact_code_beats_name(self):
        m = svc()
        got = m.match_one({"name": "不存在的名字", "product_item_code": "YT-JW-XR21-700ML-09"},
                          CANDIDATES)
        self.assertEqual("matched", got["status"])
        self.assertEqual("exact_code", got["match_method"])
        self.assertEqual("YT-JW-XR21-700ML-09", got["matched_code"])
        self.assertEqual(1.0, got["score"])

    def test_b3_exact_name(self):
        m = svc()
        got = m.match_one({"name": "底板"}, CANDIDATES)
        self.assertEqual("exact_name", got["match_method"])
        self.assertEqual("底板工艺路线", got["matched_name"])
        self.assertEqual(CODE_OF["底板"], got["matched_code"])
        self.assertEqual(1.0, got["score"])
        self.assertEqual("", got["reason"])

    def test_b4_normalized_name(self):
        m = svc()
        got = m.match_one({"name": "内盒3忖纸"}, CANDIDATES)
        self.assertEqual("normalized_name", got["match_method"])
        self.assertEqual(CODE_OF["内盒3衬纸"], got["matched_code"])
        self.assertEqual("内盒3衬纸", got["normalized"])
        self.assertEqual(1.0, got["score"])

    def test_b5_constrained_similarity(self):
        m = svc()
        got = m.match_one({"name": "内盒8衬板"},
                          [{"name": "内盒8衬板副", "code": "C-1"}])
        self.assertEqual("constrained_similarity", got["match_method"])
        self.assertEqual("C-1", got["matched_code"])
        self.assertEqual(0.9091, got["score"])
        self.assertEqual("", got["reason"])

    def test_b6_unbound_no_candidate(self):
        m = svc()
        got = m.match_one({"name": "顶托面纸", "business_part_code": "DWG-BP11"}, [])
        self.assertEqual("unbound", got["status"])
        self.assertEqual("unbound", got["match_method"])
        self.assertEqual("no_candidate", got["reason"])
        self.assertEqual(0, got["candidates_total"])
        self.assertEqual("DWG-BP11", got["part_code"])
        self.assertEqual("顶托面纸", got["part_name"])

    def test_b7_unbound_shape_when_rejected(self):
        m = svc()
        got = m.match_one({"name": "底板"}, [{"name": "底板面纸工艺路线", "code": "X"}])
        self.assertEqual("unbound", got["status"])
        self.assertEqual("unbound", got["match_method"])
        self.assertIn(got["reason"], UNBOUND_REASONS)
        self.assertEqual([{"name": "底板面纸工艺路线", "reason": got["rejected"][0]["reason"]}],
                         got["rejected"])


# --------------------------------------------------------------------------- #
# C 组：三道硬约束与阈值（Spec §2.3 / §2.4）
# --------------------------------------------------------------------------- #
class TestCConstraints(unittest.TestCase):
    def test_c1_direction_conflict(self):
        m = svc()
        self.assertEqual("左/右", m.direction_conflict("左盖面纸", "右盖面纸"))
        self.assertEqual("左/右", m.direction_conflict("右盖面纸", "左盖面纸"))
        self.assertEqual("", m.direction_conflict("左盖面纸", "左盖外盒外层衬板"))
        self.assertEqual("", m.direction_conflict("底板", "底板面纸"))

    def test_c2_index_conflict(self):
        m = svc()
        self.assertEqual(("1",), m.index_tokens("内盒1灰板"))
        self.assertEqual(("2",), m.index_tokens("内盒2灰板"))
        self.assertEqual((), m.index_tokens("底板"))
        self.assertEqual("index", m.index_conflict("内盒1灰板", "内盒2灰板"))
        self.assertEqual("", m.index_conflict("内盒1灰板", "内盒1面纸"))
        self.assertEqual("", m.index_conflict("底板", "底板面纸"))

    def test_c3_kind_conflict(self):
        m = svc()
        self.assertEqual(frozenset({"面纸"}), m.kind_tokens("底板面纸"))
        self.assertEqual(frozenset(), m.kind_tokens("底板"))
        self.assertEqual(frozenset({"衬板"}), m.kind_tokens("左盒外盒外层衬板"))
        self.assertEqual("kind", m.kind_conflict("底板", "底板面纸"))
        self.assertEqual("", m.kind_conflict("内盒1灰板", "内盒2灰板"))

    def test_c4_similarity_tie_is_ambiguous_not_winner(self):
        m = svc()
        got = m.match_one({"name": "内盒9灰板"},
                          [{"name": "内盒9灰板甲", "code": "A"},
                           {"name": "内盒9灰板乙", "code": "B"}])
        self.assertEqual("unbound", got["status"])
        self.assertEqual("ambiguous", got["reason"])
        self.assertEqual("", got["matched_code"])

    def test_c5_duplicate_normalized_is_duplicate_name(self):
        m = svc()
        got = m.match_one({"name": "内盒3忖纸"},
                          [{"name": "内盒3衬纸工艺路线", "code": "A"},
                           {"name": "内盒3衬纸", "code": "B"}])
        self.assertEqual("unbound", got["status"])
        self.assertEqual("duplicate_name", got["reason"])

    def test_c6_below_threshold(self):
        m = svc()
        got = m.match_one({"name": "内盒7灰板"},
                          [{"name": "内盒7灰板外包装用辅助支撑垫片材料", "code": "Z"}])
        self.assertEqual("unbound", got["status"])
        self.assertEqual("below_threshold", got["reason"])

    def test_c7_all_rejected_records_first_conflict(self):
        m = svc()
        got = m.match_one({"name": "左盖面纸"},
                          [{"name": "右盖面纸工艺路线", "code": "R"},
                           {"name": "内盒1灰板工艺路线", "code": "K"}])
        self.assertEqual("unbound", got["status"])
        self.assertIn(got["reason"], ("position_conflict", "kind_conflict"))
        self.assertEqual(2, len(got["rejected"]))
        for item in got["rejected"]:
            self.assertIn(item["reason"], ("position_conflict", "kind_conflict"))


# --------------------------------------------------------------------------- #
# D 组：外购件（Spec §2.4 末段）
# --------------------------------------------------------------------------- #
class TestDExternal(unittest.TestCase):
    def test_d1_authority_process_text(self):
        m = svc()
        got = m.match_one({"name": "顶托EVA", "authority": {"process_text": "外购，用量1个"}},
                          CANDIDATES)
        self.assertEqual("skipped_external", got["status"])
        self.assertEqual("unbound", got["match_method"])
        self.assertEqual("", got["reason"])
        self.assertEqual("", got["matched_code"])
        self.assertEqual(0.0, got["score"])

    def test_d2_reference_process_text_caigou(self):
        m = svc()
        got = m.match_one({"name": "磁铁", "reference": {"process_text": "采购标准件"}},
                          CANDIDATES)
        self.assertEqual("skipped_external", got["status"])

    def test_d3_name_only(self):
        m = svc()
        got = m.match_one({"name": "外购磁铁"}, CANDIDATES)
        self.assertEqual("skipped_external", got["status"])

    def test_d4_external_never_matches_even_with_candidate(self):
        m = svc()
        got = m.match_one({"name": "磁铁", "reference": {"process_text": "外购"}},
                          [{"name": "磁铁工艺路线", "code": "M"}])
        self.assertEqual("skipped_external", got["status"])
        self.assertEqual("", got["matched_code"])


# --------------------------------------------------------------------------- #
# E 组：match_parts 汇总（Spec §2.5）
# --------------------------------------------------------------------------- #
class TestEMatchParts(unittest.TestCase):
    def test_e1_shape_and_counts(self):
        m = svc()
        got = m.match_parts(rows(), CANDIDATES)
        self.assertEqual(sorted(PARTS_KEYS), sorted(got.keys()))
        self.assertEqual(ENGINE_VERSION, got["engine_version"])
        self.assertEqual(28, len(got["bindings"]))
        self.assertEqual(26, got["matched"])
        self.assertEqual(0, got["unbound"])
        self.assertEqual(2, got["skipped_external"])
        self.assertEqual([p["business_part_code"] for p in rows()],
                         [b["part_code"] for b in got["bindings"]])

    def test_e2_unbound_parts_listed(self):
        m = svc()
        got = m.match_parts([{"business_part_code": "P1", "name": "找不到的件"}], CANDIDATES)
        self.assertEqual(1, got["unbound"])
        self.assertEqual("P1", got["unbound_parts"][0]["part_code"])
        self.assertEqual("找不到的件", got["unbound_parts"][0]["part_name"])
        self.assertIn(got["unbound_parts"][0]["reason"], UNBOUND_REASONS)

    def test_e3_duplicate_targets_disclosed_not_rejected(self):
        m = svc()
        got = m.match_parts([{"business_part_code": "P1", "name": "底板"},
                             {"business_part_code": "P2", "name": "底板"}], CANDIDATES)
        self.assertEqual(2, got["matched"])
        self.assertEqual([{"matched_code": CODE_OF["底板"], "matched_name": "底板工艺路线",
                           "part_codes": ["P1", "P2"]}], got["duplicate_targets"])

    def test_e4_never_pulls_candidates_of_its_own(self):
        m = svc()
        got = m.match_parts(rows(), [])
        self.assertEqual(0, got["matched"])
        self.assertEqual(26, got["unbound"])
        self.assertEqual(2, got["skipped_external"])


# --------------------------------------------------------------------------- #
# F 组：真实样本 28 × 26（Spec §3）
# --------------------------------------------------------------------------- #
class TestFRealSample(unittest.TestCase):
    def test_f1_counts(self):
        m = svc()
        got = m.match_parts(rows(), CANDIDATES)
        self.assertEqual(26, got["matched"])
        self.assertEqual(0, got["unbound"], "26 个制造件库里全都有，缺的只是归一化")
        self.assertEqual(2, got["skipped_external"])

    def test_f2_method_split(self):
        m = svc()
        got = m.match_parts(rows(), CANDIDATES)
        methods = [b["match_method"] for b in got["bindings"]]
        self.assertEqual(15, methods.count("exact_name"))
        self.assertEqual(11, methods.count("normalized_name"))
        by_name = {b["part_name"]: b for b in got["bindings"]}
        self.assertEqual(set(EXPECTED_EXACT),
                         {n for n in EXPECTED_EXACT if by_name[n]["match_method"] == "exact_name"})
        self.assertEqual(set(EXPECTED_NORMALIZED),
                         {n for n in EXPECTED_NORMALIZED
                          if by_name[n]["match_method"] == "normalized_name"})
        self.assertEqual(set(EXPECTED_EXTERNAL),
                         {n for n in EXPECTED_EXTERNAL
                          if by_name[n]["status"] == "skipped_external"})

    def test_f3_matched_codes(self):
        m = svc()
        by_name = {b["part_name"]: b for b in m.match_parts(rows(), CANDIDATES)["bindings"]}
        for drawing_name, library_name in (("内盒3忖纸", "内盒3衬纸"),
                                           ("左盒外盒里层灰板1", "左盖外盒里层灰板1"),
                                           ("内皮壳忖纸", "内皮壳衬纸"),
                                           ("底板", "底板"), ("底板面纸", "底板面纸"),
                                           ("内卡", "内卡")):
            self.assertEqual(CODE_OF[library_name], by_name[drawing_name]["matched_code"],
                             "%s 应匹到库内 %s" % (drawing_name, library_name))

    def test_f4_no_silent_mismatch(self):
        m = svc()
        for binding in m.match_parts(rows(), CANDIDATES)["bindings"]:
            if binding["status"] != "matched":
                continue
            self.assertNotEqual("", binding["matched_code"])
            self.assertEqual(1.0, binding["score"])


# --------------------------------------------------------------------------- #
# G 组：反例守卫（Spec §3）
# --------------------------------------------------------------------------- #
class TestGGuards(unittest.TestCase):
    def test_g1_baseboard_never_borrows_face_paper(self):
        m = svc()
        got = m.match_one({"name": "底板"}, [{"name": "底板面纸工艺路线", "code": "K"}])
        self.assertEqual("unbound", got["status"])
        self.assertEqual("kind_conflict", got["reason"])

    def test_g2_left_never_borrows_right(self):
        m = svc()
        got = m.match_one({"name": "左盖面纸"}, [{"name": "右盖面纸工艺路线", "code": "R"}])
        self.assertEqual("unbound", got["status"])
        self.assertEqual("position_conflict", got["reason"])

    def test_g3_index_one_never_borrows_two(self):
        m = svc()
        got = m.match_one({"name": "内盒1灰板"}, [{"name": "内盒2灰板工艺路线", "code": "I2"}])
        self.assertEqual("unbound", got["status"])
        self.assertEqual("position_conflict", got["reason"])

    def test_g4_exact_beats_similar_neighbour(self):
        m = svc()
        got = m.match_one({"name": "左盖面纸"},
                          [{"name": "右盖面纸工艺路线", "code": "R"},
                           {"name": "左盖面纸工艺路线", "code": "L"}])
        self.assertEqual("matched", got["status"])
        self.assertEqual("L", got["matched_code"])
        self.assertEqual("exact_name", got["match_method"])


# --------------------------------------------------------------------------- #
# H 组：接线（Spec §2.6）
# --------------------------------------------------------------------------- #
def _payload(status, routes=None):
    return {"ok": True, "status": status, "routes": routes or []}


class _Resp:
    def __init__(self, payload):
        import json
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestHWiring(unittest.TestCase):
    def test_h1_for_row_uses_shared_matcher(self):
        src = ROUTE_PY.read_text(encoding="utf-8")
        self.assertIn("packaging_part_route_match", src,
                      "外购短路与归一化重试必须引用同一处实现（Spec §2.6）")
        self.assertIn("is_external_part", src)

    def test_h2_for_row_external_short_circuits_without_bridge(self):
        from tech_app.backend.services import da_process_routing as routing
        with mock.patch.object(routing.urllib.request, "urlopen",
                               side_effect=AssertionError("外购件不许调桥接")):
            got = routing.for_row({"name": "顶托EVA",
                                   "reference": {"process_text": "外购，用量1个"}})
        self.assertEqual("external", got["status"])
        self.assertEqual([], got["routes"])
        self.assertEqual("unbound", got["match_method"])

    def test_h3_for_row_normalized_retry(self):
        from tech_app.backend.services import da_process_routing as routing
        seen = []

        def fake(req, timeout=None):
            import urllib.parse
            query = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
            name = (query.get("name") or [""])[0]
            seen.append(name)
            return _Resp(_payload("matched", [{"header": {"name": name + "工艺路线"}}])
                         if name == "内盒3衬纸" else _payload("not_found"))

        with mock.patch.object(routing.cpq_kb_client, "_internal_token", return_value="t"), \
             mock.patch.object(routing.cpq_kb_client, "_base", return_value="http://x"), \
             mock.patch.object(routing.urllib.request, "urlopen", side_effect=fake):
            got = routing.for_row({"name": "内盒3忖纸"})
        self.assertEqual(["内盒3忖纸", "内盒3衬纸"], seen)
        self.assertEqual("matched", got["status"])
        self.assertEqual("normalized_name", got["match_method"])
        self.assertEqual("内盒3忖纸", got["normalized_from"])
        self.assertEqual("内盒3衬纸", got["matched_name"])

    def test_h4_for_row_exact_name_no_retry(self):
        from tech_app.backend.services import da_process_routing as routing
        seen = []

        def fake(req, timeout=None):
            import urllib.parse
            query = urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)
            seen.append((query.get("name") or [""])[0])
            return _Resp(_payload("matched", [{"header": {"name": "底板工艺路线"}}]))

        with mock.patch.object(routing.cpq_kb_client, "_internal_token", return_value="t"), \
             mock.patch.object(routing.cpq_kb_client, "_base", return_value="http://x"), \
             mock.patch.object(routing.urllib.request, "urlopen", side_effect=fake):
            got = routing.for_row({"name": "底板"})
        self.assertEqual(["底板"], seen)
        self.assertEqual("exact_name", got["match_method"])

    def test_h5_for_row_unbound_keeps_reason(self):
        from tech_app.backend.services import da_process_routing as routing
        with mock.patch.object(routing.cpq_kb_client, "_internal_token", return_value="t"), \
             mock.patch.object(routing.cpq_kb_client, "_base", return_value="http://x"), \
             mock.patch.object(routing.urllib.request, "urlopen",
                               side_effect=lambda req, timeout=None: _Resp(_payload("not_found"))):
            got = routing.for_row({"name": "内盒3忖纸"})
        self.assertEqual("not_found", got["status"])
        self.assertEqual("unbound", got["match_method"])
        self.assertEqual("not_found", got["reason"])

    def test_h6_instances_reference_shared_external_words(self):
        src = INSTANCES_PY.read_text(encoding="utf-8")
        self.assertIn("packaging_part_route_match", src,
                      "外购口径只允许有一处（Spec §2.6）")
        self.assertIn("is_external_part", src)
        self.assertNotIn("'外购'", src)

    def test_h7_instances_still_skip_external_parts(self):
        from tech_app.backend.services import packaging_process_instances as instances
        from tech_app.backend.services import packaging_parts as parts
        doc = {"business_parts_hash": "h", "business_parts": [
            {"business_part_code": "A", "name": "左盖面纸",
             "reference": {"process_text": "开料-模切"}},
            {"business_part_code": "B", "name": "顶托EVA",
             "reference": {"process_text": "外购，用量1个"}}]}
        with mock.patch.object(parts, "load_business_parts", return_value=doc), \
             mock.patch.object(parts, "load_part_process",
                               return_value={"plan": {"steps": [{"name": "模切"}]}}):
            got = instances.collect("pid")
        self.assertEqual(["A"], [r["part_code"] for r in got["records"]])


if __name__ == "__main__":
    unittest.main()

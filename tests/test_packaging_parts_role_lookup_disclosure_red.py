"""红测：零件角色全 `unknown` 时，「语义层没算出来」不许与「图纸图层名不认识」同形。

Spec：`docs/specs/packaging-parts-role-lookup-disclosure.md`
血缘：`docs/specs/packaging-silent-degradation-disclosure.md`（同一病症在零件侧的角色来源）、
`packaging-drawing-semantics.md` §2.2（逐层 `role_source` 本来就有，被 `packaging_parts` 扔了）、
`packaging-product-outline-and-die-layer-roles.md`、`packaging-part-role-manual-mapping.md`。

现状缺口（读代码 + 离线实测，2026-09-22）：
  · `packaging_parts._layer_roles()`（`:440`）里 `packaging_semantics.analyze()` 抛异常 → `doc = None`
    → 退回 IR 图层兜底 → 真实 IR 没有 role 字段 → 全 `unknown`；
  · 语义文档本来带逐层 `role_source`（`rule` / `none` …），函数只取 `role`，把
    "这个图层名根本不在规则里"这条唯一能解释原因的证据丢掉；
  · 于是 `stats.by_role = {"unknown": N}` 同时表示"语义层没跑成"与"图层名不认识"；
  · 下游 `reject_unknown_role_autobind()` 因此拒掉全部 BOM 自动绑定（材料费 0 / 工艺推荐没有零件）。

纪律：
  · 只跑离线单测：本机 fixture（`tests/fixtures/cad_ir/parts_panels.json`）+ `mock.patch.object`，
    不连 PG、不连 34、不跑真 DWG 转换、不发 HTTP、不写业务数据；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_parts, packaging_semantics  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "cad_ir" / "parts_panels.json"
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"

ROLE_LOOKUPS = ("semantics", "ir_layers", "unavailable")


def fixture_ir():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def bare_ir(layers):
    """没有 geometry 的最小 IR：只用来验角色来源，不产生零件。"""
    return {"ir_id": "ir-role-lookup", "units": {}, "layers": layers,
            "entities": [], "geometry": {"components": []}}


def lookup_fn():
    fn = getattr(packaging_parts, "role_lookup_state", None)
    if not callable(fn):
        raise AssertionError(
            "packaging_parts 必须提供 role_lookup_state()（Spec §2.1）："
            "今天只有 _layer_roles()，语义层挂了与图层名不认识在返回体上同形")
    return fn


# --------------------------------------------------------------------------- #
# A. 纯函数 role_lookup_state
# --------------------------------------------------------------------------- #
class ARoleLookupState(unittest.TestCase):
    def test_a1_given_semantics_wins_and_analyze_not_called(self):
        semantics = {"layers": [{"name": "CUT", "role": "cut", "role_source": "rule"},
                               {"name": "DESIGN", "role": "unknown", "role_source": "none"}]}
        with mock.patch.object(packaging_semantics, "analyze") as analyze:
            state = lookup_fn()(bare_ir([{"name": "DESIGN"}]), semantics)
        analyze.assert_not_called()
        self.assertEqual("semantics", state.get("source"), "给了语义文档就必须用它（Spec §2.1）")
        self.assertEqual("", state.get("reason"), "不是失败不许编 reason")
        self.assertEqual(["DESIGN"], list(state.get("unknown_layers") or []),
                         "认不出角色的图层名必须列出来（Spec §2.1）")
        self.assertTrue(str(state.get("message") or "").strip(),
                        "有认不出的图层时必须有人话（Spec §2.1）")

    def test_a2_analyze_success_is_semantics(self):
        state = lookup_fn()(fixture_ir(), None)
        self.assertEqual("semantics", state.get("source"),
                         "现算成功就是语义层给的角色（Spec §2.1）")
        self.assertEqual("", state.get("reason"))
        unknown = list(state.get("unknown_layers") or [])
        self.assertIn("0", unknown, "fixture 里 '0' 层认不出角色，必须列出来（Spec §2.1）")
        self.assertEqual(sorted(set(unknown)), unknown, "unknown_layers 必须去重有序（Spec §2.1）")

    def test_a3_analyze_failure_with_ir_roles_is_ir_layers(self):
        with mock.patch.object(packaging_semantics, "analyze",
                               side_effect=RuntimeError("semantics down")):
            state = lookup_fn()(bare_ir([{"name": "CUT", "role": "cut"}]), None)
        self.assertEqual("ir_layers", state.get("source"),
                         "IR 图层真的给出了已知角色才叫 ir_layers（Spec §2.1）")
        self.assertIn("RuntimeError", str(state.get("reason")),
                      "语义层失败必须留痕异常类名（Spec §2.1）")
        self.assertTrue(str(state.get("message") or "").strip(),
                        "走了兜底必须有人话说明（Spec §2.1）")

    def test_a4_analyze_failure_without_roles_is_unavailable(self):
        with mock.patch.object(packaging_semantics, "analyze",
                               side_effect=RuntimeError("semantics down")):
            state = lookup_fn()(bare_ir([{"name": "DESIGN"}, {"name": "0"}]), None)
        self.assertIn(state.get("source"), ROLE_LOOKUPS, "source 必须是闭集（Spec §2.1）")
        self.assertEqual("unavailable", state.get("source"),
                         "语义层挂了且 IR 也没有角色，就是「没查成」这一态（Spec §2.1）")
        self.assertIn("RuntimeError", str(state.get("reason")))
        self.assertEqual(["0", "DESIGN"], list(state.get("unknown_layers") or []),
                         "这一态下列出 IR 的全部图层名（Spec §2.1）")
        self.assertTrue(str(state.get("message") or "").strip(),
                        "「没查成」必须有人话，且不许说成「图纸上没有可识别图层」（Spec §2.4）")


# --------------------------------------------------------------------------- #
# B. extract() 的 stats 必须带出处
# --------------------------------------------------------------------------- #
class BExtractStats(unittest.TestCase):
    def test_b1_stats_carries_role_lookup_matching_the_function(self):
        doc = packaging_parts.extract(fixture_ir(), None)
        stats = doc.get("stats") or {}
        self.assertIn("role_lookup", stats,
                      "extract() 的 stats 必须带 role_lookup（Spec §2.2）")
        state = stats.get("role_lookup") or {}
        self.assertIn(state.get("source"), ROLE_LOOKUPS, "source 必须是闭集（Spec §2.2）")
        self.assertEqual(lookup_fn()(fixture_ir(), None), state,
                         "stats.role_lookup 必须与 role_lookup_state() 逐字一致（Spec §2.2）")

    def test_b2_failure_is_disclosed_but_by_role_stays(self):
        with mock.patch.object(packaging_semantics, "analyze",
                               side_effect=RuntimeError("semantics down")):
            doc = packaging_parts.extract(fixture_ir(), None)
        stats = doc.get("stats") or {}
        state = stats.get("role_lookup") or {}
        self.assertEqual("unavailable", state.get("source"),
                         "语义层挂掉必须报 unavailable（Spec §2.2）")
        self.assertIn("RuntimeError", str(state.get("reason")))
        self.assertEqual({"unknown": len(doc.get("parts") or [])}, stats.get("by_role"),
                         "结论口径不变：角色仍全是 unknown（只留痕，Spec §4）")
        self.assertEqual({row["role"] for row in (doc.get("parts") or [])}, set(stats["by_role"]),
                         "by_role 仍是零件角色的计数（既有口径，Spec §4）")


# --------------------------------------------------------------------------- #
# C. summarize() 原样带出（含老文档兜底）
# --------------------------------------------------------------------------- #
class CSummarize(unittest.TestCase):
    def test_c1_summary_carries_role_lookup(self):
        doc = packaging_parts.extract(fixture_ir(), None)
        summary = packaging_parts.summarize(doc)
        self.assertIn("role_lookup", summary, "summarize() 必须带 role_lookup（Spec §2.3）")
        self.assertEqual((doc.get("stats") or {}).get("role_lookup"), summary.get("role_lookup"),
                         "必须原样带出（Spec §2.3）")

    def test_c2_legacy_document_is_not_guessed_as_semantics(self):
        legacy = {"engine_version": packaging_parts.ENGINE_VERSION, "parts": [], "filtered": [],
                  "unavailable": [], "stats": {"part_total": 0, "filtered_total": 0,
                                               "truncated": 0, "by_role": {}}}
        state = (packaging_parts.summarize(legacy) or {}).get("role_lookup") or {}
        self.assertIn("role_lookup", packaging_parts.summarize(legacy),
                      "老文档也必须带这个键（Spec §2.3）")
        self.assertEqual("unavailable", state.get("source"),
                         "这份文档没带出处 ≠ 语义层可用（Spec §2.3）")
        self.assertEqual("role_lookup_missing", state.get("reason"))
        self.assertTrue(str(state.get("message") or "").strip(), "必须有人话（Spec §2.3）")


# --------------------------------------------------------------------------- #
# D. 前端按 source 分家（源码守卫，离线）
# --------------------------------------------------------------------------- #
class DFrontend(unittest.TestCase):
    def test_d1_parts_tree_discloses_role_lookup_unavailable(self):
        text = APP_JS.read_text(encoding="utf-8")
        self.assertIn("data-parts-role-lookup-unavailable", text,
                      "零件树/左栏必须有 role_lookup 失败的稳定钩子（Spec §2.4）")
        self.assertIn("role_lookup", text,
                      "前端必须读 role_lookup 才能按 source 分家说人话（Spec §2.4）")


# --------------------------------------------------------------------------- #
# E. 护栏（今天必须绿）
# --------------------------------------------------------------------------- #
class EGuards(unittest.TestCase):
    def test_e1_layer_roles_unchanged(self):
        semantics = {"layers": [{"name": "CUT", "role": "cut", "role_source": "rule"},
                               {"name": "design", "role": "unknown", "role_source": "none"}]}
        self.assertEqual({"CUT": "cut", "DESIGN": "unknown"},
                         packaging_parts._layer_roles(bare_ir([]), semantics),
                         "图层名大写 + 角色取值口径逐字不变（Spec §2.1）")

    def test_e2_by_role_matches_part_roles(self):
        doc = packaging_parts.extract(fixture_ir(), None)
        stats = doc.get("stats") or {}
        self.assertEqual(set(stats.get("by_role") or {}),
                         {row["role"] for row in (doc.get("parts") or [])})

    def test_e3_summarize_existing_keys_unchanged(self):
        doc = packaging_parts.extract(fixture_ir(), None)
        summary = packaging_parts.summarize(doc)
        for key in ("part_total", "filtered_total", "closed_ratio",
                    "role_known_ratio", "kind_total"):
            self.assertIn(key, summary, "既有键 %s 不许消失（Spec §4）" % key)
        self.assertIn("by_role", summary.get("stats") or {},
                      "既有键 stats.by_role 不许消失（Spec §4）")


if __name__ == "__main__":
    unittest.main()

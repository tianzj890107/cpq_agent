"""红测：包装成本规则路由与报告分组收敛 —— 包装第 7 批修订 A（修复第 1 批）。

Spec：`docs/specs/packaging-cost-rule-routing.md`（修订 `docs/specs/packaging-cost-engine.md`
的 §2.3 / §4.5 / §4.6）。依赖：包装第 1–8 批已实现。

现状缺口（首次运行时**必须失败**，是实测不是推断）：
  · `REPORT_GROUPS` 只有 10 组 / 15 个成员，覆盖不到 24 + 2 个类别（A 组）；
  · `compute_line()` 走 `_entry_for()` 直接读 `FORMULA_CATALOG`，库里 `reviewed` 公式只对
    材料与人工生效，覆膜/烫金/模切/包材改了也不生效（C/D/E 组）；
  · `compute_line()` 按类别取值时会静默取 `packaging` 的第一条包材公式（C 组 c10）；
  · 结果没有 `formula_source` / `formula_version` / `rule_snapshot_version`（C/D/E 组）；
  · `requirement-confirm.js` 的 `PC_GROUP_ORDER` 只有 10 个分组名（B 组）。

黄金数值沿用第 7 批红测已核对的 `报价逻辑-0903.xlsx` 可见 Sheet `报价-工费率`（本文件不读工作簿）。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec 并同步本节说明。
"""
from __future__ import annotations

import importlib
import pathlib
import re
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FRONTEND_JS = ROOT / "tech_app" / "frontend" / "requirement-confirm.js"

from tech_app.backend import config  # noqa: E402
from tech_app.backend.storage import (da_db, da_repo, da_seed_packaging as seed,  # noqa: E402
                                      kb_repo, meta_backend, store)
from tech_app.backend.services import (cost_model, packaging_bom,  # noqa: E402
                                       packaging_route)

PID = "pkgroute0001"
REQ_NO = "REQ-PKG-R0001"
BOX_MAIN = "YT-RB-01001-A"
ACTOR = {"username": "wangjingli", "role": "process_manager"}

REQ = {"industry": "packaging", "packaging_product_name": "礼盒", "packaging_category": "礼盒",
       "quote_quantity": 1000, "inner_length": 200, "inner_width": 150, "inner_height": 80,
       "fit_clearance": 1.8, "box_type": BOX_MAIN, "closure_type": "天地盖", "v_groove": "是",
       "face_paper_gsm": 200, "lamination": "是", "hot_stamping": "是", "print_colors": "CMYK"}

# --------------------------------------------------------------------------- #
# Spec §2.1：13 组，恰好覆盖 24 + 2 个类别、每个类别只出现一次
# --------------------------------------------------------------------------- #
EXPECTED_GROUPS = (
    ("材料", ("material", "glue")),
    ("印刷", ("print", "print_uv")),
    ("覆膜", ("lamination", "transfer_film")),
    ("烫金", ("hot_stamp_flat", "hot_stamp_round", "cold_stamp")),
    ("丝印", ("silk_screen",)),
    ("表面处理", ("varnish", "anti_scratch", "pet_oil", "visidi_uv", "texture", "emboss_deboss")),
    ("裱纸", ("mounting",)),
    ("模切", ("die_cutting",)),
    ("装订贴盒", ("folding", "auto_mount", "double_tape")),
    ("开槽", ("v_groove",)),
    ("手工", ("labor",)),
    ("其他费用", ("other",)),
    ("包装", ("packaging", "freight")),
)
GROUP_ORDER = [name for name, _ in EXPECTED_GROUPS]
#: 工作簿 `成本细分` 的 10 列同名分组（名字与成员必须保持不变，Spec §2.2）
WORKBOOK_GROUPS = ("材料", "印刷", "覆膜", "烫金", "丝印", "裱纸", "模切", "开槽", "手工", "包装")
#: 工作簿 成本细分 10 列**没有**细分的 13 个类别（现状从 report_groups 里整块消失）
NOT_IN_WORKBOOK = ("transfer_film", "hot_stamp_round", "cold_stamp", "varnish", "anti_scratch",
                   "pet_oil", "visidi_uv", "texture", "emboss_deboss", "folding", "auto_mount",
                   "double_tape", "other")

#: 0903 报价-工费率 V2 的复膜（机器默认参数 + 889×700）
LAMINATION_VARS = {"machine_length": 889, "machine_width": 700, "imposition_count": 1,
                   "setup_minutes": 30, "capacity_per_hour": 5500, "equipment_rate": 197,
                   "labor_rate": 145, "film_price": 1.7, "film_thickness_um": 18,
                   "film_kg_price": 18.5, "tax_factor": 1.13, "quote_quantity": 1000}
GOLDEN_LAMINATION = 1.3766112580048271
GOLDEN_DIE_CUT = 0.8347876923076923
GOLDEN_GLUE = 0.46050199999999997
#: 2026-09-21 口径裁决（`docs/specs/packaging-cost-minimum-charge-decision.md`）：
#: 取 ② 报价-工费率，主行无门限，四个码的 minimum_charge 一律归零。第 1 批冻结值
#: 200/150/100/120 只作证据留在 `FORMULA_CATALOG[*]["frozen_minimum_charge"]`，不再参与命中判定。
DECIDED_MINIMUM_CHARGES = {"PKG-C-LAMINATION": 0, "PKG-C-HOT-STAMP-FLAT": 0,
                           "PKG-C-DIE-CUT": 0, "PKG-C-V-GROOVE": 0}


def load_cost_module():
    try:
        return importlib.import_module("tech_app.backend.services.packaging_cost")
    except Exception:
        return None


def packaging_tables():
    """按 da_seed_packaging 的真实演示数据造快照（不合成假数据）。"""
    materials = [dict(item["material"], industry="packaging", status="active")
                 for item in seed.MATERIALS]
    properties = [dict(prop, industry="packaging")
                  for item in seed.MATERIALS for prop in item.get("properties") or []]
    prices = [dict(item["price"], material_code=item["material"]["material_code"],
                   price_id=index + 1)
              for index, item in enumerate(seed.MATERIALS)]
    tables = {
        "kb_packaging_box_type": [dict(r) for r in seed.BOX_TYPES],
        "kb_packaging_part_template": [dict(r) for r in seed.PART_TEMPLATES],
        "kb_packaging_process_template": [dict(r) for r in seed.PROCESS_TEMPLATES],
        "kb_packaging_insert_accessory": [dict(r) for r in seed.ACCESSORIES],
        "kb_packaging_logistics_rule": [dict(r) for r in seed.LOGISTICS_RULES],
        "kb_packaging_match_weight": [dict(r) for r in seed.MATCH_WEIGHTS],
        "kb_packaging_cost_formula": [dict(r) for r in seed.COST_FORMULAS],
        "kb_material": materials,
        "kb_material_property": properties,
        "kb_material_price": prices,
        "kb_cost_rate": [dict(r, industry="packaging") for r in seed.COST_RATES],
        "kb_cost_factor": [dict(r, industry="packaging") for r in seed.COST_FACTORS],
    }
    for attr, table in (("COST_CONTENTS", "kb_packaging_cost_content"),
                        ("TOOLING_RULES", "kb_packaging_tooling_rule")):
        extra = getattr(seed, attr, None)
        if extra:
            tables[table] = [dict(r) for r in extra]
    return tables


def reviewed_row(code, expression, **over):
    """构造一条 reviewed 覆盖行（Spec §3）。"""
    row = {"formula_code": code, "expression": expression, "minimum_charge": 0, "rounding": 4,
           "rate_code": "", "review_status": "reviewed", "industry": "packaging",
           "status": "active", "formula_version": "9.9", "effective_from": "2026-09-20"}
    row.update(over)
    return row


def draft_row(code, expression, **over):
    row = reviewed_row(code, expression, **over)
    row["review_status"] = "draft"
    row["formula_version"] = "draft-9"
    return row


class RoutingCase(unittest.TestCase):
    """共用：可替换知识库快照 + 独立临时 SQLite + 独立 meta 目录。"""

    def setUp(self):
        self._cache = dict(kb_repo._CACHE)
        self._da_path = config.DA_DB_PATH
        self.db_file = pathlib.Path(tempfile.mkdtemp()) / "routing.sqlite3"
        self._patch_da = mock.patch.object(config, "DA_DB_PATH", self.db_file)
        self._patch_da.start()
        da_db.init_db(self.db_file)
        self._backend = meta_backend._backend
        self.data_dir = pathlib.Path(tempfile.mkdtemp())
        meta_backend._backend = meta_backend.JsonMetaBackend(self.data_dir)
        self.snapshot()

    def tearDown(self):
        meta_backend._backend = self._backend
        self._patch_da.stop()
        da_db.close_conn()
        kb_repo._CACHE.clear()
        kb_repo._CACHE.update(self._cache)

    def snapshot(self, **overrides):
        tables = packaging_tables()
        tables.update(overrides)
        kb_repo._CACHE["version"] = "probe-version"
        kb_repo._CACHE["tables"] = tables
        return tables

    def mod(self):
        module = load_cost_module()
        self.assertIsNotNone(module, "缺少 tech_app/backend/services/packaging_cost.py")
        return module

    def save_requirement(self, project_id=PID, requirement_no=REQ_NO, **over):
        data = dict(REQ)
        data.update(over)
        store.save_requirement(project_id, {
            "project_id": project_id, "requirement_no": requirement_no, "title": "规则路由用例",
            "status": "pending_confirmation", "data": data})

    def prepare(self, code=BOX_MAIN, project_id=PID, requirement_no=REQ_NO, **req_over):
        """需求 + 确认盒型 + 第 5 批 BOM + 第 6 批已确认路线（第 7/8 批的输入链）。"""
        self.save_requirement(project_id, requirement_no, **req_over)
        da_repo.save_box_match({
            "project_id": project_id, "requirement_no": requirement_no,
            "industry": "packaging", "engine_version": "packaging_match_v1",
            "inputs": {}, "candidates": [], "missing_inputs": [], "suggested_box_type": code})
        da_repo.update_box_match_decision(
            project_id, requirement_no, decision="confirmed", confirmed_box_type=code,
            confirmed_by="wangjingli", confirmed_at=da_db.now())
        packaging_bom.build_bom(project_id, requirement_no)
        packaging_route.build_route(project_id, requirement_no)
        packaging_route.confirm_route(project_id, requirement_no, actor=ACTOR)


# --------------------------------------------------------------------------- #
# A. 报告分组闭集
# --------------------------------------------------------------------------- #
class AReportGroups(RoutingCase):
    def test_a1_group_names_and_order(self):
        module = self.mod()
        self.assertEqual(list(module.REPORT_GROUPS), GROUP_ORDER,
                         "分组名与顺序必须与 Spec §2.1 逐条一致（顺序 = 前端渲染顺序）")

    def test_a2_group_membership(self):
        module = self.mod()
        got = {name: tuple(members) for name, members in module.REPORT_GROUPS.items()}
        self.assertEqual(got, dict(EXPECTED_GROUPS), "分组成员划分必须与 Spec §2.1 一致")

    def test_a3_groups_cover_every_category_exactly_once(self):
        module = self.mod()
        flattened = [code for members in module.REPORT_GROUPS.values() for code in members]
        self.assertEqual(len(flattened), len(set(flattened)), "报告分组不得重复归类")
        known = {code for code, _ in module.COST_CATEGORIES}
        known |= {code for code, _ in module.PROJECT_COST_CATEGORIES}
        self.assertEqual(len(known), 26)
        self.assertEqual(set(flattened), known, "报告分组必须正好覆盖 24 + 2 个类别")

    def test_a4_no_empty_group_and_no_empty_name(self):
        module = self.mod()
        for name, members in module.REPORT_GROUPS.items():
            self.assertTrue(str(name).strip(), "分组名不许为空")
            self.assertTrue(tuple(members), "分组 %s 不许为空组" % name)

    def test_a5_workbook_group_names_are_kept(self):
        module = self.mod()
        for name in WORKBOOK_GROUPS:
            self.assertIn(name, module.REPORT_GROUPS,
                          "工作簿 成本细分 的同名分组 %s 不得改名或删除" % name)

    def test_a6_uncategorized_categories_are_now_reachable(self):
        module = self.mod()
        flattened = {code for members in module.REPORT_GROUPS.values() for code in members}
        for code in NOT_IN_WORKBOOK:
            self.assertIn(code, flattened,
                          "类别 %s 在 成本细分 里没有列，必须由扩展分组承载（否则金额静默消失）" % code)

    def test_a7_summarize_reports_all_groups_and_adds_up(self):
        module = self.mod()
        summary = module.summarize([{"cost_category": "material", "amount": 100.0,
                                     "loss_rate": 0.0, "part_code": "P1"}],
                                   packaging=3.0, freight=2.0,
                                   tooling=[{"amount": 5.0}])
        self.assertEqual(list(summary["report_groups"]), GROUP_ORDER,
                         "summarize 必须返回全部 13 个分组（含 0 值组）")
        self.assertAlmostEqual(sum(summary["report_groups"].values()), summary["total_cost"],
                               places=6, msg="分组之和必须等于总成本")
        self.assertAlmostEqual(summary["report_groups"]["材料"], 100.0, places=6)
        self.assertAlmostEqual(summary["report_groups"]["包装"], 5.0, places=6)

    def test_a8_empty_summary_keeps_zero_valued_groups(self):
        module = self.mod()
        summary = module.summarize([])
        self.assertEqual(set(summary["report_groups"]), set(GROUP_ORDER),
                         "没有金额时也要有全部 13 个键，不许因 0 丢键")
        self.assertEqual(summary["total_cost"], 0.0)

    def test_a9_build_and_load_carry_all_groups(self):
        module = self.mod()
        self.prepare()
        cost = module.build_cost(PID, REQ_NO, actor=ACTOR)
        self.assertEqual(set(cost["report_groups"]), set(GROUP_ORDER))
        loaded = module.load_cost(PID, REQ_NO)
        self.assertTrue(loaded["built"])
        self.assertEqual(set(loaded["report_groups"]), set(GROUP_ORDER),
                         "读回也要有全部 13 个分组")

    def test_a10_empty_shell_has_all_groups(self):
        module = self.mod()
        empty = module.load_cost(PID, "REQ-NOT-EXIST")
        self.assertFalse(empty["built"])
        self.assertEqual(set(empty["report_groups"]), set(GROUP_ORDER),
                         "没算过的空壳也要有 13 个分组键")


# --------------------------------------------------------------------------- #
# B. 前端分组渲染顺序
# --------------------------------------------------------------------------- #
class BFrontendGroupOrder(RoutingCase):
    def frontend_order(self):
        text = FRONTEND_JS.read_text(encoding="utf-8")
        match = re.search(r"PC_GROUP_ORDER\s*=\s*\[(.*?)\]", text, re.S)
        self.assertIsNotNone(match, "requirement-confirm.js 必须有 PC_GROUP_ORDER")
        return re.findall(r"['\"]([^'\"]+)['\"]", match.group(1))

    def test_b1_frontend_order_lists_every_group(self):
        self.assertEqual(self.frontend_order(), GROUP_ORDER,
                         "PC_GROUP_ORDER 必须按 Spec §2.1 顺序列出 13 个分组，否则新分组不渲染")


# --------------------------------------------------------------------------- #
# C. 公式取值单一入口：compute_line
# --------------------------------------------------------------------------- #
class CComputeLineRouting(RoutingCase):
    def test_c1_reviewed_override_changes_the_amount(self):
        module = self.mod()
        self.snapshot(kb_packaging_cost_formula=[
            reviewed_row("PKG-C-LAMINATION", "quote_quantity*0+4")])
        line = module.compute_line("lamination", dict(LAMINATION_VARS))
        self.assertAlmostEqual(line["amount"], 4.0, places=6,
                               msg="库里 reviewed 的覆膜公式必须覆盖内置目录（Spec §3.1）")
        self.assertEqual(line["formula_source"], "kb")
        self.assertEqual(line["formula_version"], "9.9")

    def test_c2_reviewed_minimum_charge_is_used(self):
        module = self.mod()
        self.snapshot(kb_packaging_cost_formula=[
            reviewed_row("PKG-C-LAMINATION", "1/1000", minimum_charge=200)])
        line = module.compute_line("lamination", dict(LAMINATION_VARS))
        self.assertTrue(line["min_charge_applied"], "覆盖行的最低收费必须生效")
        self.assertAlmostEqual(line["amount"], 0.2, places=6)

    def test_c3_builtin_is_used_when_nothing_is_reviewed(self):
        module = self.mod()
        line = module.compute_line("lamination", dict(LAMINATION_VARS))
        self.assertAlmostEqual(line["amount"], GOLDEN_LAMINATION, places=6,
                               msg="没有 reviewed 行时必须用内置目录（本批不得改表达式）")
        self.assertEqual(line["formula_source"], "builtin")
        self.assertEqual(line["formula_version"], "1.0")

    def test_c4_override_does_not_mutate_the_catalog(self):
        module = self.mod()
        before = dict(module.FORMULA_CATALOG["PKG-C-LAMINATION"])
        self.snapshot(kb_packaging_cost_formula=[
            reviewed_row("PKG-C-LAMINATION", "quote_quantity*0+4")])
        module.compute_line("lamination", dict(LAMINATION_VARS))
        self.assertEqual(dict(module.FORMULA_CATALOG["PKG-C-LAMINATION"]), before,
                         "覆盖必须只作用于本次计算结果，不许就地改写内置目录")

    def test_c5_draft_rows_are_ignored(self):
        module = self.mod()
        rows = [dict(r) for r in seed.COST_FORMULAS]
        rows.append(draft_row("PKG-C-LAMINATION", "quote_quantity*0+999"))
        self.snapshot(kb_packaging_cost_formula=rows)
        line = module.compute_line("lamination", dict(LAMINATION_VARS))
        self.assertAlmostEqual(line["amount"], GOLDEN_LAMINATION, places=6)
        self.assertEqual(line["formula_source"], "builtin",
                         "draft 公式（含第 3 批中文散文）绝不能被执行")

    def test_c6_broken_reviewed_formula_is_raised_from_compute_line(self):
        module = self.mod()
        self.snapshot(kb_packaging_cost_formula=[
            reviewed_row("PKG-C-LAMINATION", "import os")])
        with self.assertRaises(module.CostError) as ctx:
            module.compute_line("lamination", dict(LAMINATION_VARS))
        self.assertEqual(ctx.exception.code, "invalid_formula:PKG-C-LAMINATION",
                         "reviewed 公式写错必须报错，不许静默用内置值顶替")

    def test_c7_reviewed_formula_with_unknown_variable_fails_closed(self):
        module = self.mod()
        self.snapshot(kb_packaging_cost_formula=[
            reviewed_row("PKG-C-DIE-CUT", "foo_bar*2")])
        with self.assertRaises(module.CostError) as ctx:
            module.compute_line("die_cutting", {})
        self.assertEqual(ctx.exception.code, "invalid_formula:PKG-C-DIE-CUT")

    def test_c8_unknown_formula_code_stays_404(self):
        module = self.mod()
        with self.assertRaises(module.CostError) as ctx:
            module.resolve_formula("PKG-C-NOT-EXIST")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_c9_category_without_formula_returns_gap(self):
        module = self.mod()
        line = module.compute_line("other", {"quote_quantity": 1000})
        self.assertIsNone(line["amount"])
        self.assertEqual(line["gap"]["code"], "no_formula:other")

    def test_c10_ambiguous_category_requires_formula_code(self):
        module = self.mod()
        with self.assertRaises(module.CostError) as ctx:
            module.compute_line("packaging", {"length_mm": 520, "width_mm": 420, "height_mm": 425,
                                              "usage_qty": 1, "material_price": 3,
                                              "units_per_pack": 4, "quote_quantity": 1000})
        self.assertEqual(ctx.exception.code, "category_needs_formula_code:packaging",
                         "packaging 有 11 条包材公式，按类别取值必须报错，不许静默取第一条")

    def test_c11_rule_snapshot_version_is_recorded(self):
        module = self.mod()
        line = module.compute_line("lamination", dict(LAMINATION_VARS))
        self.assertEqual(line.get("rule_snapshot_version"), "probe-version",
                         "每条结果都要记下本次用到的规则快照版本")

    def test_c12_project_reads_the_rule_table_once(self):
        module = self.mod()
        self.prepare()
        real = kb_repo._table
        calls = {"n": 0}

        def counting(name):
            if name == "kb_packaging_cost_formula":
                calls["n"] += 1
            return real(name)

        with mock.patch.object(kb_repo, "_table", side_effect=counting):
            module.compute_project(PID, REQ_NO)
        self.assertLessEqual(calls["n"], 1,
                             "一次成本计算只许读一次 kb_packaging_cost_formula（Spec §3.4）")


# --------------------------------------------------------------------------- #
# D. 公式取值单一入口：包材 compute_content / compute_packaging
# --------------------------------------------------------------------------- #
class DContentRouting(RoutingCase):
    CARTON_VARS = {"length_mm": 520, "width_mm": 420, "height_mm": 425, "usage_qty": 1,
                   "material_price": 3, "units_per_pack": 4, "tax_factor": 1.13,
                   "loss_uplift": 1.03, "yield_divisor": 0.9, "quote_quantity": 1000}

    def test_d1_reviewed_content_formula_overrides_builtin(self):
        module = self.mod()
        self.snapshot(kb_packaging_cost_formula=[
            reviewed_row("PKG-P-CARTON", "quote_quantity*0+7")])
        line = module.compute_content("PKG-P-CARTON", dict(self.CARTON_VARS))
        self.assertAlmostEqual(line["amount"], 7.0, places=6,
                               msg="包材公式的 reviewed 覆盖必须生效（Spec §3.1）")
        self.assertEqual(line["formula_source"], "kb")

    def test_d2_packaging_total_follows_the_override(self):
        module = self.mod()
        rows = [{"content_code": "彩盒", "formula_code": "PKG-P-CARTON",
                 "name": "彩盒", **self.CARTON_VARS}]
        baseline = module.compute_packaging(rows)["amount"]
        self.snapshot(kb_packaging_cost_formula=[
            reviewed_row("PKG-P-CARTON", "quote_quantity*0+7")])
        after = module.compute_packaging(rows)["amount"]
        self.assertAlmostEqual(after, 7.0, places=6)
        self.assertNotAlmostEqual(after, baseline, places=6,
                                  msg="包材合计必须跟着 reviewed 公式走")

    def test_d3_broken_reviewed_content_formula_fails_closed(self):
        module = self.mod()
        self.snapshot(kb_packaging_cost_formula=[
            reviewed_row("PKG-P-CARTON", "import os")])
        with self.assertRaises(module.CostError) as ctx:
            module.compute_content("PKG-P-CARTON", dict(self.CARTON_VARS))
        self.assertEqual(ctx.exception.code, "invalid_formula:PKG-P-CARTON")


# --------------------------------------------------------------------------- #
# E. 全链路：build_cost / load_cost
# --------------------------------------------------------------------------- #
class EEndToEndRouting(RoutingCase):
    def test_e1_project_cost_follows_the_reviewed_override(self):
        module = self.mod()
        self.prepare()
        baseline = module.build_cost(PID, REQ_NO, actor=ACTOR)
        baseline_lamination = [item for item in baseline["items"]
                               if item.get("cost_category") == "lamination"]
        self.assertTrue(baseline_lamination,
                        "用例前提：礼盒需求必须产生覆膜成本行（否则本用例无意义）")

        self.snapshot(kb_packaging_cost_formula=[
            reviewed_row("PKG-C-LAMINATION", "quote_quantity*0+40")])
        after = module.build_cost(PID, REQ_NO, actor=ACTOR)
        self.assertGreater(after["total_cost"], baseline["total_cost"],
                           "覆膜改成 reviewed 的固定 40 后总额必须变大")
        sources = {item.get("formula_source") for item in after["items"]
                   if item.get("cost_category") == "lamination"}
        self.assertEqual(sources, {"kb"}, "明细行必须标出用的是知识库公式")

    def test_e2_load_cost_keeps_groups_and_amounts(self):
        module = self.mod()
        self.prepare()
        module.build_cost(PID, REQ_NO, actor=ACTOR)
        loaded = module.load_cost(PID, REQ_NO)
        self.assertEqual(set(loaded["report_groups"]), set(GROUP_ORDER))
        self.assertAlmostEqual(sum(loaded["report_groups"].values()), loaded["total_cost"],
                               places=6)

    def test_e3_draft_override_does_not_change_the_project(self):
        module = self.mod()
        rows = [dict(r) for r in seed.COST_FORMULAS]
        rows.append(draft_row("PKG-C-LAMINATION", "quote_quantity*0+999"))
        self.snapshot(kb_packaging_cost_formula=rows)
        self.prepare()
        cost = module.build_cost(PID, REQ_NO, actor=ACTOR)
        sources = {item.get("formula_source") for item in cost["items"]
                   if item.get("cost_category") == "lamination"}
        self.assertEqual(sources, {"builtin"}, "draft 行不许进入正式成本")


# --------------------------------------------------------------------------- #
# F. 本批不许动的既有契约
# --------------------------------------------------------------------------- #
class FUnchangedContracts(RoutingCase):
    def test_f1_generic_profile_coefficients_are_untouched(self):
        self.assertEqual((cost_model.TAX_DIVISOR, cost_model.MATERIAL_SHARE,
                          cost_model.LABOR_RATIO, cost_model.OVERHEAD_RATIO,
                          cost_model.PROCESSING_RATIO),
                         (1.13, 0.791, 0.3556, 0.1778, 0.0944),
                         "三个原行业的 generic_v1 系数本批不许改")
        module = self.mod()
        self.assertEqual(module.profile_for("semiconductor"), module.GENERIC_PROFILE)
        self.assertEqual(module.profile_for("packaging"), module.COST_PROFILE)

    def test_f2_non_packaging_requirement_is_rejected(self):
        module = self.mod()
        self.save_requirement(industry="semiconductor")
        with self.assertRaises(module.CostError) as ctx:
            module.compute_project(PID, REQ_NO)
        self.assertEqual(ctx.exception.code, "not_packaging")

    def test_f3_expressions_and_minimum_charges_are_untouched(self):
        module = self.mod()
        self.assertAlmostEqual(
            module.compute_line("lamination", dict(LAMINATION_VARS))["amount"],
            GOLDEN_LAMINATION, places=6)
        self.assertAlmostEqual(
            module.compute_line("die_cutting", {"imposition_count": 1, "setup_minutes": 120,
                                                "capacity_per_hour": 6500,
                                                "equipment_rate": 197.52, "labor_rate": 190.06,
                                                "quote_quantity": 1000})["amount"],
            GOLDEN_DIE_CUT, places=6)
        self.assertAlmostEqual(
            module.compute_line("glue", {"machine_length": 889, "machine_width": 700,
                                         "imposition_count": 1, "glue_unit_price": 0.74})["amount"],
            GOLDEN_GLUE, places=6)
        for code, expected in DECIDED_MINIMUM_CHARGES.items():
            self.assertEqual(module.FORMULA_CATALOG[code]["minimum_charge"], expected,
                             "%s 的最低收费已按 2026-09-21 的 ② 裁决归零"
                             "（docs/specs/packaging-cost-minimum-charge-decision.md）" % code)


if __name__ == "__main__":
    unittest.main(verbosity=2)

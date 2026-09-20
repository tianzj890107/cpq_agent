"""红测：包装工艺路线与标准工时 —— 包装第 6 批。

Spec：docs/specs/packaging-process-route.md
依赖：第 1 批（四行业注册表）、第 2 批（包装需求模板）、第 3 批（包装知识库扩展表 + 12 盒型 /
23 条工艺模板）、第 4 批（盒型匹配与确认）、第 5 批（参数化部件展开与包装 BOM）已完成。

现状缺口（实测，不是推断）：
  · `tech_app/backend/services/packaging_route.py` 不存在 —— 没有任何代码把 23 条工艺模板排成
    路线，也没有 `印刷 → 覆膜 → 烫金 → 丝印 → UV → 压凹凸 → 模切` 这条硬顺序的校验；
  · 需求 3.4 的覆膜/烫金/UV/丝印/压凹凸/模切对路线**没有任何影响**（模板里的 `表面处理` 是
    聚合工序）；第 5 批只把工序当成 BOM 的 `process` 引用行（`item_key = step_name`），
    工时、设备、质控点都没带出来；
  · `da_schema.sql` 没有 `wip_packaging_process_route*` 三张表；
  · `main.py` 没有 packaging-route 四个路由，`requirement-confirm.js` 也没有路线面板。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

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

ROUTE_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_route.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
SCHEMA_SQL = ROOT / "tech_app" / "backend" / "storage" / "da_schema.sql"
CONFIRM_JS = ROOT / "tech_app" / "frontend" / "requirement-confirm.js"

from tech_app.backend import config  # noqa: E402
from tech_app.backend.storage import (da_db, da_repo, da_seed_packaging as seed,  # noqa: E402
                                      kb_repo, meta_backend, store)
from tech_app.backend.services import industry_templates, packaging_bom  # noqa: E402

PID = "pkgroute00001"
REQ_NO = "REQ-PKG-R0001"

BOX_MAIN = "YT-RB-01001-A"        # 11 道工序模板，工时合计 193.0s
BOX_BOOK = "YT-RB-02001-A"        # 12 道工序模板，工时合计 305.0s
BOX_NO_PROCESS = "YT-RB-03001-A"  # 有部件模板、没有工艺模板 → 必须报 no_process_template

REQ = {"industry": "packaging", "packaging_product_name": "礼盒", "packaging_category": "礼盒",
       "quote_quantity": 1000, "inner_length": 200, "inner_width": 150, "inner_height": 80,
       "fit_clearance": 1.8, "box_type": BOX_MAIN, "closure_type": "天地盖", "v_groove": "是",
       "face_paper_gsm": 200}

# 工序闭集（Spec §2.2，19 条）
CATALOG = {
    "灰板开料": 10, "V 槽开槽": 20, "灰板成型": 30, "面纸印刷": 40, "表面处理": 45,
    "覆膜": 50, "烫金": 60, "丝印": 70, "UV 上光": 80, "压凹凸": 90, "面纸模切": 100,
    "铰链贴合": 110, "磁铁嵌入": 120, "机裱": 130, "手裱": 140, "内托组装": 150,
    "组装": 160, "检验": 170, "清洁包装": 180,
}
HARD_CHAIN = ("面纸印刷", "覆膜", "烫金", "丝印", "UV 上光", "压凹凸", "面纸模切")
SURFACE_MAP = {"print_colors": "面纸印刷", "spot_colors": "面纸印刷", "lamination": "覆膜",
               "hot_stamping": "烫金", "silk_screen": "丝印", "uv_coating": "UV 上光",
               "emboss_deboss": "压凹凸", "die_cutting": "面纸模切"}
SURFACE_STATIONS = {"覆膜": "覆膜机", "烫金": "烫金机", "丝印": "丝印机", "UV 上光": "UV 上光机",
                    "压凹凸": "压凹凸机", "面纸印刷": "胶印机", "面纸模切": "模切机"}

# 无表面工艺需求时的路线（`表面处理` 作为聚合工序保留在 45 位）
MAIN_PLAIN = ("灰板开料", "V 槽开槽", "灰板成型", "面纸印刷", "表面处理", "面纸模切",
              "机裱", "手裱", "组装", "检验", "清洁包装")
BOOK_PLAIN = ("灰板开料", "V 槽开槽", "灰板成型", "面纸印刷", "表面处理", "面纸模切",
              "铰链贴合", "磁铁嵌入", "手裱", "组装", "检验", "清洁包装")
MAIN_SECONDS = {"灰板开料": 10.0, "V 槽开槽": 15.0, "灰板成型": 20.0, "面纸印刷": 15.0,
                "表面处理": 12.0, "面纸模切": 8.0, "机裱": 18.0, "手裱": 55.0,
                "组装": 22.0, "检验": 10.0, "清洁包装": 8.0}


def load_route_module():
    """路线引擎；不存在时返回 None（用例给明确断言，不抛 ImportError）。"""
    try:
        return importlib.import_module("tech_app.backend.services.packaging_route")
    except Exception:
        return None


def box_row(code):
    for row in seed.BOX_TYPES:
        if row["box_type_code"] == code:
            return dict(row)
    raise AssertionError("种子里没有盒型 %s" % code)


def packaging_tables():
    """按 da_seed_packaging 的真实演示数据造快照（不合成假数据）。"""
    materials = [dict(item["material"], industry="packaging", status="active")
                 for item in seed.MATERIALS]
    properties = [dict(prop, industry="packaging")
                  for item in seed.MATERIALS for prop in item.get("properties") or []]
    return {
        "kb_packaging_box_type": [dict(r) for r in seed.BOX_TYPES],
        "kb_packaging_part_template": [dict(r) for r in seed.PART_TEMPLATES],
        "kb_packaging_process_template": [dict(r) for r in seed.PROCESS_TEMPLATES],
        "kb_packaging_insert_accessory": [dict(r) for r in seed.ACCESSORIES],
        "kb_packaging_logistics_rule": [dict(r) for r in seed.LOGISTICS_RULES],
        "kb_packaging_match_weight": [dict(r) for r in seed.MATCH_WEIGHTS],
        "kb_material": materials,
        "kb_material_property": properties,
    }


class RouteCase(unittest.TestCase):
    """共用：快照可替换 + 独立临时 SQLite + 独立 meta 目录。"""

    def setUp(self):
        self._cache = dict(kb_repo._CACHE)
        self._da_path = config.DA_DB_PATH
        self.db_file = pathlib.Path(tempfile.mkdtemp()) / "route.sqlite3"
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

    # ---- 知识库快照 -------------------------------------------------------- #
    def snapshot(self, **overrides):
        tables = packaging_tables()
        tables.update(overrides)
        kb_repo._CACHE["version"] = "probe-version"
        kb_repo._CACHE["tables"] = tables
        return tables

    # ---- 模块加载 ---------------------------------------------------------- #
    def route_mod(self):
        module = load_route_module()
        self.assertIsNotNone(
            module, "缺少 tech_app/backend/services/packaging_route.py（Spec §4.5）")
        return module

    # ---- 需求单 / 确认盒型 / BOM ------------------------------------------- #
    def save_requirement(self, project_id=PID, requirement_no=REQ_NO, **over):
        data = dict(REQ)
        data.update(over)
        store.save_requirement(project_id, {
            "project_id": project_id, "requirement_no": requirement_no,
            "title": "包装路线用例", "status": "pending_confirmation", "data": data})

    def confirm_box(self, code=BOX_MAIN, project_id=PID, requirement_no=REQ_NO):
        da_repo.save_box_match({
            "project_id": project_id, "requirement_no": requirement_no,
            "industry": "packaging", "engine_version": "packaging_match_v1",
            "inputs": {}, "candidates": [], "missing_inputs": [],
            "suggested_box_type": code})
        da_repo.update_box_match_decision(
            project_id, requirement_no, decision="confirmed", confirmed_box_type=code,
            confirmed_by="wangjingli", confirmed_at=da_db.now())

    def build_bom(self, project_id=PID, requirement_no=REQ_NO):
        return packaging_bom.build_bom(project_id, requirement_no)

    def prepare(self, code=BOX_MAIN, with_bom=True, **req_over):
        """需求 + 确认盒型（+ 第 5 批 BOM）。"""
        self.save_requirement(**req_over)
        self.confirm_box(code)
        if with_bom:
            self.build_bom()

    # ---- 常用断言 ---------------------------------------------------------- #
    @staticmethod
    def step_names(result):
        return [row["step_name"] for row in result["steps"]]

    @staticmethod
    def step_of(result, name):
        for row in result["steps"]:
            if row["step_name"] == name:
                return row
        raise AssertionError("路线里没有工序 %s：%s" % (name, [r["step_name"] for r in result["steps"]]))

    @staticmethod
    def db_rows(sql, params=()):
        return da_db.query_all(sql, params)


# --------------------------------------------------------------------------- #
# A. 工序闭集与顺序校验
# --------------------------------------------------------------------------- #
class ACatalogAndOrder(RouteCase):
    def test_a1_catalog_is_the_nineteen_step_closure(self):
        module = self.route_mod()
        catalog = dict(module.PROCESS_CATALOG)
        self.assertEqual(catalog, CATALOG, "工序闭集与规范位次必须逐条一致（Spec §2.2）")
        self.assertEqual(len(catalog), 19)

    def test_a2_hard_order_chain(self):
        module = self.route_mod()
        self.assertEqual(tuple(module.HARD_ORDER_CHAIN), HARD_CHAIN)
        for name in module.HARD_ORDER_CHAIN:
            self.assertIn(name, module.PROCESS_CATALOG, "硬顺序链里的工序必须在闭集里")
        ranks = [module.PROCESS_CATALOG[name] for name in module.HARD_ORDER_CHAIN]
        self.assertEqual(ranks, sorted(ranks), "硬顺序链必须与规范位次同序")

    def test_a3_surface_requirement_mapping(self):
        module = self.route_mod()
        self.assertEqual(dict(module.SURFACE_REQUIREMENTS), SURFACE_MAP)

    def test_a4_surface_stations_cover_the_surface_steps(self):
        module = self.route_mod()
        for step, station in SURFACE_STATIONS.items():
            self.assertEqual(module.SURFACE_STATIONS.get(step), station)

    def test_a5_engine_version_and_roles(self):
        module = self.route_mod()
        self.assertEqual(module.ENGINE_VERSION, "packaging_route_v1")
        match = importlib.import_module("tech_app.backend.services.packaging_match")
        self.assertIs(module.ROUTE_WRITE_ROLES, match.BOX_MATCH_DECIDE_ROLES,
                      "确认/生成的角色常量必须直接引用第 4 批那一份，不得另抄")

    def test_a6_generated_routes_always_pass_validation(self):
        module = self.route_mod()
        cases = [dict(REQ), dict(REQ, lamination="是", hot_stamping="是"),
                 dict(REQ, uv_coating="是", silk_screen="是", emboss_deboss="是",
                      die_cutting="是"),
                 dict(REQ, print_colors="CMYK", spot_colors=2)]
        for code in (BOX_MAIN, BOX_BOOK):
            for inputs in cases:
                result = module.build_route_steps(code, inputs)
                self.assertEqual(module.validate_order(result["steps"]), [],
                                 "%s 在 %s 下生成了非法顺序" % (code, inputs))

    def test_a7_validate_order_catches_inversion(self):
        module = self.route_mod()
        bad = [{"step_no": 10, "step_name": "覆膜"}, {"step_no": 20, "step_name": "面纸印刷"},
               {"step_no": 30, "step_name": "面纸模切"}]
        codes = module.validate_order(bad)
        self.assertIn("illegal_process_order:面纸印刷:覆膜", codes,
                      "印刷必须在覆膜之前（Spec §2.2）")
        self.assertNotIn("step_no_not_ascending", codes, "这里只是顺序违规，不是编号乱序")

    def test_a8_validate_order_catches_unknown_duplicate_and_disorder(self):
        module = self.route_mod()
        self.assertIn("unknown_process:抛光",
                      module.validate_order([{"step_no": 10, "step_name": "抛光"}]))
        self.assertIn("duplicate_step:面纸印刷",
                      module.validate_order([{"step_no": 10, "step_name": "面纸印刷"},
                                             {"step_no": 20, "step_name": "面纸印刷"}]))
        self.assertIn("step_no_not_ascending",
                      module.validate_order([{"step_no": 20, "step_name": "灰板开料"},
                                             {"step_no": 10, "step_name": "灰板成型"}]))
        self.assertEqual(module.validate_order([]), [])


# --------------------------------------------------------------------------- #
# B. 需求驱动的表面工序
# --------------------------------------------------------------------------- #
class BSurfaceRequirements(RouteCase):
    def required(self, **fields):
        module = self.route_mod()
        return list(module.required_surface_steps(dict(REQ, **fields)))

    def test_b1_empty_requirement_needs_nothing(self):
        self.assertEqual(self.required(), [])

    def test_b2_affirmative_values_are_required(self):
        for value in ("是", "需要", "要", "有", "单面", "局部UV", "哑膜", "CMYK", 1):
            self.assertIn("覆膜", self.required(lamination=value),
                          "lamination=%r 应判为需要覆膜" % (value,))

    def test_b3_negation_words_are_not_required(self):
        for value in ("", " ", "否", "无", "不需要", "不要", "没有", "NONE", "n", "No",
                      "false", "0", "—", "-"):
            self.assertNotIn("覆膜", self.required(lamination=value),
                             "lamination=%r 不该判成需要" % (value,))

    def test_b4_numbers_use_sign(self):
        self.assertNotIn("烫金", self.required(hot_stamping=0))
        self.assertIn("烫金", self.required(hot_stamping=1))
        self.assertIn("烫金", self.required(hot_stamping=2))

    def test_b5_required_steps_follow_the_catalog_order(self):
        got = self.required(die_cutting="是", lamination="是", hot_stamping="是",
                            uv_coating="是", silk_screen="是", emboss_deboss="是",
                            print_colors="CMYK")
        self.assertEqual(got, ["面纸印刷", "覆膜", "烫金", "丝印", "UV 上光", "压凹凸", "面纸模切"])

    def test_b6_every_mapped_field_is_honoured(self):
        module = self.route_mod()
        for field, step in SURFACE_MAP.items():
            self.assertIn(step, list(module.required_surface_steps(dict(REQ, **{field: "是"}))),
                          "字段 %s 应判出工序 %s" % (field, step))

    def test_b7_unmapped_fields_are_ignored(self):
        got = self.required(mounting="是", special_process="烫金+UV", eco_requirement="全纸可回收")
        self.assertEqual(got, [], "没映射进闭集的需求字段不得凭空变成工序")


# --------------------------------------------------------------------------- #
# C. 路线生成
# --------------------------------------------------------------------------- #
class CRouteGeneration(RouteCase):
    def plain(self, code=BOX_MAIN):
        return self.route_mod().build_route_steps(code, dict(REQ))

    def test_c1_reference_box_plain_route(self):
        result = self.plain()
        self.assertEqual(tuple(self.step_names(result)), MAIN_PLAIN)
        self.assertEqual([row["step_no"] for row in result["steps"]],
                         [10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110])
        self.assertEqual([row["rank"] for row in result["steps"]],
                         [CATALOG[name] for name in MAIN_PLAIN])
        self.assertEqual(result["engine_version"], "packaging_route_v1")
        self.assertEqual(result["box_type_code"], BOX_MAIN)

    def test_c2_book_box_plain_route(self):
        result = self.plain(BOX_BOOK)
        self.assertEqual(tuple(self.step_names(result)), BOOK_PLAIN)
        self.assertEqual(len(result["steps"]), 12)

    def test_c3_aggregate_step_is_kept_and_flagged(self):
        result = self.plain()
        row = self.step_of(result, "表面处理")
        self.assertEqual(row["source"], "template")
        self.assertAlmostEqual(float(row["standard_seconds"]), 12.0, places=1)
        self.assertEqual(result["gaps"]["aggregate_steps"], ["表面处理"],
                         "聚合工序必须在缺口里可见（Spec §2.4.2）")

    def test_c4_aggregate_step_is_replaced_by_required_surface_steps(self):
        result = self.route_mod().build_route_steps(
            BOX_MAIN, dict(REQ, lamination="是", hot_stamping="是"))
        names = self.step_names(result)
        self.assertNotIn("表面处理", names)
        self.assertLess(names.index("面纸印刷"), names.index("覆膜"))
        self.assertLess(names.index("覆膜"), names.index("烫金"))
        self.assertLess(names.index("烫金"), names.index("面纸模切"))
        self.assertEqual(result["gaps"]["aggregate_steps"], [])
        laminating = self.step_of(result, "覆膜")
        self.assertEqual(laminating["source"], "template:表面处理")
        self.assertEqual(laminating["requirement_field"], "lamination")
        self.assertEqual(laminating["workstation"], "覆膜机")
        self.assertEqual(laminating["control_point"],
                         self.step_of(self.plain(), "表面处理")["control_point"],
                         "展开自模板的工序应继承模板质控点")
        hot = self.step_of(result, "烫金")
        self.assertEqual(hot["requirement_field"], "hot_stamping")
        self.assertEqual(hot["workstation"], "烫金机")

    def test_c5_uv_only_still_expands_the_aggregate_step(self):
        result = self.route_mod().build_route_steps(BOX_MAIN, dict(REQ, uv_coating="是"))
        names = self.step_names(result)
        self.assertNotIn("表面处理", names)
        self.assertIn("UV 上光", names)
        self.assertNotIn("覆膜", names, "没填覆膜就不许凭空出覆膜")
        self.assertEqual(self.step_of(result, "UV 上光")["requirement_field"], "uv_coating")

    def test_c6_synthesised_steps_do_not_duplicate_template_steps(self):
        result = self.route_mod().build_route_steps(
            BOX_MAIN, dict(REQ, silk_screen="是", emboss_deboss="是", die_cutting="是"))
        names = self.step_names(result)
        self.assertEqual(names.count("丝印"), 1)
        self.assertEqual(names.count("压凹凸"), 1)
        self.assertEqual(names.count("面纸模切"), 1, "模板已有模切，需求填了也不能重复出")
        self.assertEqual(self.step_of(result, "丝印")["source"], "requirement:silk_screen")
        self.assertEqual(self.step_of(result, "压凹凸")["requirement_field"], "emboss_deboss")
        self.assertEqual(self.step_of(result, "面纸模切")["source"], "template",
                         "模板里已有的工序仍算模板工序")

    def test_c7_requirement_can_synthesise_a_missing_print_step(self):
        tables = self.snapshot()
        tables["kb_packaging_process_template"] = [
            dict(row) for row in seed.PROCESS_TEMPLATES if row["step_name"] != "面纸印刷"]
        result = self.route_mod().build_route_steps(BOX_MAIN, dict(REQ, print_colors="CMYK"))
        row = self.step_of(result, "面纸印刷")
        self.assertEqual(row["source"], "requirement:print_colors")
        self.assertEqual(row["workstation"], "胶印机")
        self.assertIsNone(row["standard_seconds"])

    def test_c8_step_numbering_and_depends_on_chain(self):
        result = self.route_mod().build_route_steps(
            BOX_MAIN, dict(REQ, lamination="是", hot_stamping="是"))
        steps = result["steps"]
        self.assertEqual([row["step_no"] for row in steps], list(range(10, 10 * (len(steps) + 1), 10)))
        self.assertIsNone(steps[0]["depends_on"])
        for previous, current in zip(steps, steps[1:]):
            self.assertEqual(current["depends_on"], previous["step_no"])

    def test_c9_build_route_steps_is_a_pure_function(self):
        module = self.route_mod()
        before = json.dumps(kb_repo._CACHE.get("tables") or {}, sort_keys=True, ensure_ascii=False)
        module.build_route_steps(BOX_MAIN, dict(REQ, lamination="是"))
        after = json.dumps(kb_repo._CACHE.get("tables") or {}, sort_keys=True, ensure_ascii=False)
        self.assertEqual(before, after, "纯函数不得改写知识库快照")
        self.assertEqual(self.db_rows("SELECT COUNT(*) AS n FROM wip_packaging_process_route")[0]["n"], 0,
                         "build_route_steps 不得自己落库")


# --------------------------------------------------------------------------- #
# D. 标准工时
# --------------------------------------------------------------------------- #
class DStandardTime(RouteCase):
    def test_d1_template_totals_match_the_box_standard_seconds(self):
        module = self.route_mod()
        for code, expected in ((BOX_MAIN, 193.0), (BOX_BOOK, 305.0)):
            result = module.build_route_steps(code, dict(REQ))
            self.assertAlmostEqual(float(result["total_seconds"]), expected, places=1,
                                   msg="%s 的模板工时合计必须等于盒型标准工时" % code)
            self.assertFalse(result["has_incomplete_time"])
            self.assertEqual(result["needs_standard_time"], [])

    def test_d2_expansion_loses_the_aggregate_seconds_and_reports_gaps(self):
        result = self.route_mod().build_route_steps(
            BOX_MAIN, dict(REQ, lamination="是", hot_stamping="是"))
        self.assertAlmostEqual(float(result["total_seconds"]), 181.0, places=1,
                               msg="193 − 12（表面处理）= 181")
        self.assertTrue(result["has_incomplete_time"])
        self.assertEqual(result["needs_standard_time"], ["覆膜", "烫金"])

    def test_d3_synthesised_steps_have_no_borrowed_seconds(self):
        result = self.route_mod().build_route_steps(
            BOX_MAIN, dict(REQ, uv_coating="是", silk_screen="是", emboss_deboss="是"))
        for name in ("丝印", "UV 上光", "压凹凸"):
            row = self.step_of(result, name)
            self.assertIsNone(row["standard_seconds"], "%s 的工时必须留空待补" % name)
            self.assertTrue(row["needs_standard_time"])
        self.assertEqual(result["needs_standard_time"], ["丝印", "UV 上光", "压凹凸"],
                         "待补工时按 step_no 排序")
        self.assertAlmostEqual(float(result["total_seconds"]), 181.0, places=1)

    def test_d4_never_splits_the_aggregate_seconds_between_steps(self):
        result = self.route_mod().build_route_steps(
            BOX_MAIN, dict(REQ, lamination="是", hot_stamping="是"))
        for name in ("覆膜", "烫金"):
            self.assertIsNone(self.step_of(result, name)["standard_seconds"],
                              "12 秒是聚合工序的工时，不能按个数摊给覆膜/烫金（Spec §2.4.4）")

    def test_d5_batch_seconds_use_the_quote_quantity(self):
        self.prepare(lamination="是", hot_stamping="是")
        record = self.route_mod().build_route(project_id=PID, requirement_no=REQ_NO)
        self.assertAlmostEqual(float(record["batch_seconds"]), 181000.0, places=1)

    def test_d6_batch_seconds_are_null_without_a_quantity(self):
        self.prepare(quote_quantity="")
        record = self.route_mod().build_route(project_id=PID, requirement_no=REQ_NO)
        self.assertIsNone(record["batch_seconds"], "没填数量就不猜批量工时（Spec §2.5）")
        self.assertAlmostEqual(float(record["total_seconds"]), 193.0, places=1)


# --------------------------------------------------------------------------- #
# E. 落库与缺口
# --------------------------------------------------------------------------- #
class EPersistAndGaps(RouteCase):
    def test_e1_schema_has_the_three_tables(self):
        self.assertTrue("wip_packaging_process_route" in SCHEMA_SQL.read_text(encoding="utf-8"),
                        "da_schema.sql 缺 wip_packaging_process_route 系列表")
        with sqlite3.connect(str(self.db_file)) as conn:
            names = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("wip_packaging_process_route", "wip_packaging_process_route_step",
                          "wip_packaging_process_route_version"):
                self.assertIn(table, names)
            info = list(conn.execute("PRAGMA table_info(wip_packaging_process_route_step)"))
            columns = {row[1] for row in info}
            for column in ("project_id", "requirement_no", "step_no", "step_name", "rank",
                           "workstation", "work_content", "standard_seconds",
                           "needs_standard_time", "automation", "control_point", "parallel_ok",
                           "depends_on", "source", "requirement_field"):
                self.assertIn(column, columns, "路线工序缺列 %s" % column)
            primary = {row[1] for row in info if row[5]}
            self.assertEqual(primary, {"project_id", "requirement_no", "step_no"})

    def test_e2_build_route_persists_route_and_steps(self):
        self.prepare(lamination="是", hot_stamping="是")
        record = self.route_mod().build_route(PID, REQ_NO)
        self.assertTrue(record["built"])
        self.assertEqual(record["engine_version"], "packaging_route_v1")
        self.assertEqual(record["status"], "draft")
        rows = self.db_rows("SELECT COUNT(*) AS n FROM wip_packaging_process_route "
                            "WHERE project_id = ?", (PID,))
        self.assertEqual(rows[0]["n"], 1)
        steps = da_repo.load_packaging_route_steps(PID, REQ_NO)
        self.assertEqual(len(steps), 12)
        self.assertEqual([row["step_name"] for row in steps][:5],
                         ["灰板开料", "V 槽开槽", "灰板成型", "面纸印刷", "覆膜"])
        self.assertEqual(len(record["steps"]), 12)

    def test_e3_rebuild_replaces_everything(self):
        self.prepare()
        module = self.route_mod()
        module.build_route(PID, REQ_NO)
        module.build_route(PID, REQ_NO)
        rows = self.db_rows("SELECT COUNT(*) AS n FROM wip_packaging_process_route_step "
                            "WHERE project_id = ?", (PID,))
        self.assertEqual(rows[0]["n"], 11, "同一 (project_id, requirement_no) 整体替换，不留旧行")

    def test_e4_read_before_build_is_empty_and_quiet(self):
        self.save_requirement()
        record = self.route_mod().load_route(PID, REQ_NO)
        self.assertFalse(record["built"])
        self.assertEqual(list(record["steps"]), [])
        self.assertEqual(record["stats"]["step_count"], 0)

    def test_e5_without_a_confirmed_box_it_is_a_gap(self):
        # 未确认盒型时既没有 BOM 也没有路线；按 Spec §2.7 先报盒型缺口。
        self.save_requirement()
        with self.assertRaises(self.route_mod().RouteError) as ctx:
            self.route_mod().build_route(PID, REQ_NO)
        self.assertEqual(getattr(ctx.exception, "code", ""), "box_type_not_confirmed")
        self.assertEqual(getattr(ctx.exception, "status_code", 409), 409)

    def test_e6_without_a_bom_it_is_a_gap(self):
        self.prepare(with_bom=False)
        with self.assertRaises(self.route_mod().RouteError) as ctx:
            self.route_mod().build_route(PID, REQ_NO)
        self.assertEqual(getattr(ctx.exception, "code", ""), "bom_not_built",
                         "没有第 5 批的 process 行时不许凭空排路线（Spec §2.1）")
        self.assertEqual(getattr(ctx.exception, "status_code", 409), 409)

    def test_e7_box_without_process_template_is_a_gap(self):
        self.prepare(BOX_NO_PROCESS)
        with self.assertRaises(self.route_mod().RouteError) as ctx:
            self.route_mod().build_route(PID, REQ_NO)
        self.assertEqual(getattr(ctx.exception, "code", ""), "no_process_template")
        self.assertIn(BOX_NO_PROCESS, str(ctx.exception))

    def test_e8_non_packaging_industry_is_refused(self):
        store.save_requirement(PID, {
            "project_id": PID, "requirement_no": REQ_NO, "title": "半导体用例",
            "status": "pending_confirmation",
            "data": {"industry": "semiconductor", "product_name": "某芯片"}})
        with self.assertRaises(self.route_mod().RouteError) as ctx:
            self.route_mod().build_route(PID, REQ_NO)
        self.assertEqual(getattr(ctx.exception, "status_code", 400), 400)

    def test_e9_confirm_without_a_route_is_not_found(self):
        self.prepare()
        with self.assertRaises(self.route_mod().RouteError) as ctx:
            self.route_mod().confirm_route(PID, REQ_NO, actor={"username": "wangjingli"})
        self.assertEqual(getattr(ctx.exception, "code", ""), "route_not_found")
        self.assertEqual(getattr(ctx.exception, "status_code", 404), 404)


# --------------------------------------------------------------------------- #
# F. 确认与版本
# --------------------------------------------------------------------------- #
class FConfirmAndVersions(RouteCase):
    def build(self, **req_over):
        self.prepare(**req_over)
        return self.route_mod().build_route(PID, REQ_NO)

    def actor(self):
        return {"username": "wangjingli", "role": "process_manager"}

    def test_f1_confirm_marks_the_route(self):
        self.build()
        record = self.route_mod().confirm_route(PID, REQ_NO, actor=self.actor())
        self.assertEqual(record["status"], "confirmed")
        self.assertEqual(record["confirmed_by"], "wangjingli")
        self.assertTrue(record["confirmed_at"])

    def test_f2_confirm_freezes_a_version_snapshot(self):
        self.build(lamination="是", hot_stamping="是")
        self.route_mod().confirm_route(PID, REQ_NO, actor=self.actor())
        versions = da_repo.packaging_route_versions(PID, REQ_NO)
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["version"], 1)
        steps = json.loads(versions[0]["steps_json"])
        self.assertEqual(len(steps), 12)
        self.assertEqual([row["step_name"] for row in steps][4], "覆膜")
        self.assertEqual(versions[0]["total_seconds"], 181.0)

    def test_f3_repeat_confirm_is_idempotent(self):
        self.build()
        module = self.route_mod()
        first = module.confirm_route(PID, REQ_NO, actor=self.actor())["confirmed_at"]
        second = module.confirm_route(PID, REQ_NO, actor=self.actor())
        self.assertEqual(second["confirmed_at"], first, "重复确认同一路线必须幂等")
        self.assertEqual(len(da_repo.packaging_route_versions(PID, REQ_NO)), 1,
                         "没有变化就不该追加版本")

    def test_f4_confirm_is_refused_when_the_order_is_broken(self):
        self.build(lamination="是", hot_stamping="是")
        da_db.execute("UPDATE wip_packaging_process_route_step SET step_no = 5 "
                      "WHERE project_id = ? AND step_name = ?", (PID, "覆膜"))
        module = self.route_mod()
        self.assertEqual(module.validate_order(module.load_route(PID, REQ_NO)["steps"]),
                         ["illegal_process_order:面纸印刷:覆膜"])
        with self.assertRaises(module.RouteError) as ctx:
            module.confirm_route(PID, REQ_NO, actor=self.actor())
        self.assertEqual(getattr(ctx.exception, "code", ""), "route_not_confirmable")
        self.assertEqual(len(da_repo.packaging_route_versions(PID, REQ_NO)), 0,
                         "被拒的确认不得留下版本快照")

    def test_f5_rebuild_after_confirm_resets_to_draft(self):
        self.build()
        module = self.route_mod()
        module.confirm_route(PID, REQ_NO, actor=self.actor())
        rebuilt = module.build_route(PID, REQ_NO)
        self.assertEqual(rebuilt["status"], "draft")
        self.assertIsNone(rebuilt["confirmed_by"], "路线内容变了必须重新确认")
        self.assertIsNone(rebuilt["confirmed_at"])
        self.assertEqual(len(da_repo.packaging_route_versions(PID, REQ_NO)), 1,
                         "已冻结的版本快照不许被重算抹掉")

    def test_f6_second_confirm_after_change_appends_version_two(self):
        self.build()
        module = self.route_mod()
        module.confirm_route(PID, REQ_NO, actor=self.actor())
        self.save_requirement(lamination="是", hot_stamping="是")
        module.build_route(PID, REQ_NO)
        record = module.confirm_route(PID, REQ_NO, actor=self.actor())
        versions = da_repo.packaging_route_versions(PID, REQ_NO)
        self.assertEqual([row["version"] for row in versions], [1, 2])
        self.assertEqual(record["status"], "confirmed")
        old = json.loads(versions[0]["steps_json"])
        self.assertEqual([row["step_name"] for row in old][4], "表面处理",
                         "第 1 版快照必须原样保留（新版本不覆盖旧版本）")

    def test_f7_quantity_change_marks_stale(self):
        self.build()
        module = self.route_mod()
        module.confirm_route(PID, REQ_NO, actor=self.actor())
        self.save_requirement(quote_quantity=2000)
        record = module.load_route(PID, REQ_NO)
        self.assertTrue(record["stale"])
        self.assertIn("quantity_changed", record["stale_reasons"])
        self.assertEqual(record["status"], "confirmed", "stale 不得抹掉人工确认")

    def test_f8_surface_change_marks_stale(self):
        self.build()
        module = self.route_mod()
        module.confirm_route(PID, REQ_NO, actor=self.actor())
        self.save_requirement(lamination="是")
        record = module.load_route(PID, REQ_NO)
        self.assertTrue(record["stale"])
        self.assertIn("requirement_changed", record["stale_reasons"])
        self.assertEqual(record["confirmed_by"], "wangjingli")

    def test_f9_confirm_writes_the_project_audit(self):
        self.build()
        module = self.route_mod()
        with mock.patch.object(store, "audit") as spy:
            module.confirm_route(PID, REQ_NO, actor=self.actor())
        actions = [str(call.args[1]) for call in spy.call_args_list]
        self.assertTrue(any("packaging_route_confirmed" in a for a in actions), actions)


# --------------------------------------------------------------------------- #
# G. 接口与角色门禁
# --------------------------------------------------------------------------- #
class GApiAndRoles(RouteCase):
    def test_g1_routes_are_registered(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        for suffix in ("/requirement/packaging-route", "/requirement/packaging-route/confirm",
                       "/requirement/packaging-route/versions"):
            self.assertTrue(suffix in source, "main.py 缺路由 %s" % suffix)
        import tech_app.backend.main as main  # noqa: F401  (导入较慢，只在需要时)
        paths = {route.path for route in main.app.routes}
        for path in ("/api/projects/{project_id}/requirement/packaging-route",
                     "/api/projects/{project_id}/requirement/packaging-route/confirm",
                     "/api/projects/{pid}/requirement/packaging-route",
                     "/api/projects/{pid}/requirement/packaging-route/versions"):
            self.assertTrue(path in paths, "main.py 未注册路由 %s" % path)

    def test_g2_write_roles_are_reused_from_batch4(self):
        match = importlib.import_module("tech_app.backend.services.packaging_match")
        self.assertIs(self.route_mod().ROUTE_WRITE_ROLES, match.BOX_MATCH_DECIDE_ROLES)
        self.assertEqual(set(self.route_mod().ROUTE_WRITE_ROLES),
                         {"process_manager", "process_director", "admin"})

    def test_g3_frontend_panel_is_wired(self):
        source = CONFIRM_JS.read_text(encoding="utf-8", errors="replace")
        self.assertTrue("packaging-route" in source, "1.2 需求确认页要接包装路线接口")


# --------------------------------------------------------------------------- #
# H. 非回归护栏
# --------------------------------------------------------------------------- #
class HNonRegression(RouteCase):
    def test_h1_packaging_requirement_fields_unchanged(self):
        self.assertEqual(len(industry_templates.field_keys("packaging")), 64)
        self.assertEqual(industry_templates.required_keys("packaging"), {
            "box_type", "closure_type", "face_paper_gsm", "inner_height",
            "inner_length", "inner_width", "packaging_category",
            "packaging_product_name", "quote_quantity", "v_groove"})

    def test_h2_batch5_bom_contract_still_there(self):
        self.assertEqual(packaging_bom.ENGINE_VERSION, "packaging_bom_v1")
        self.assertEqual(set(packaging_bom.BOM_CATEGORIES), {
            "finished", "box_part", "material", "process", "packaging", "tooling",
            "optional_part"})
        self.assertIs(packaging_bom.BOM_WRITE_ROLES, self.route_mod().ROUTE_WRITE_ROLES)

    def test_h3_batch4_match_contract_still_there(self):
        match = importlib.import_module("tech_app.backend.services.packaging_match")
        self.assertEqual(match.ENGINE_VERSION, "packaging_match_v1")
        self.assertEqual(set(match.BOX_MATCH_DECIDE_ROLES),
                         {"process_manager", "process_director", "admin"})

    def test_h4_legacy_process_tables_are_not_written(self):
        self.prepare()
        self.route_mod().build_route(PID, REQ_NO)
        for table in ("wip_process_plan", "wip_process_step", "wip_bom_item", "wip_part"):
            count = self.db_rows("SELECT COUNT(*) AS n FROM %s" % table)[0]["n"]
            self.assertEqual(count, 0, "包装路线不得写 %s（Spec §6）" % table)

    def test_h5_route_engine_is_offline(self):
        module = self.route_mod()
        source = inspect.getsource(module)
        for forbidden in ("requests", "urllib", "httpx", "psycopg", "llm_client",
                          "subprocess", "socket"):
            self.assertFalse(forbidden in source,
                             "%s 不得出现 %s：不联网、不起进程、不调模型（Spec §5）"
                             % (module.__name__, forbidden))

    def test_h6_batch3_seed_data_is_untouched(self):
        self.assertEqual(len(seed.BOX_TYPES), 12)
        self.assertEqual(len(seed.PART_TEMPLATES), 31)
        self.assertEqual(len(seed.PROCESS_TEMPLATES), 23)
        self.assertEqual(len(seed.ACCESSORIES), 12)
        self.assertEqual(len(seed.LOGISTICS_RULES), 3)
        self.assertEqual(len(seed.MATERIALS), 5)
        seconds = {(row["box_type_code"], row["step_name"]): row["standard_seconds"]
                   for row in seed.PROCESS_TEMPLATES}
        self.assertEqual(seconds[(BOX_MAIN, "表面处理")], 12.0)
        self.assertEqual(seconds[(BOX_BOOK, "手裱")], 120.0)


if __name__ == "__main__":
    unittest.main()

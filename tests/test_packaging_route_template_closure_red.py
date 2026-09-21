"""红测：权威实样盒型的工艺模板必须落在工序闭集内 —— 工序名归一化 + 入库自检.

Spec：docs/specs/packaging-route-template-closure.md

现状缺口（实测，不是推断）：
  · 34 上项目 `325effd296a5`：`YT-DWG-WINE-700ML`（权威实样）盒型匹配 `1.000 matched`、
    BOM 建成、`packaging-route` 落库成 draft，但 `packaging-route/confirm` **409
    route_not_confirmable**（8 条 `unknown_process:*`）→ 成本拿不到已确认路线 → 零件下游全断；
  · 本机用同一份样本工序名（`scripts/tmp_import_dwg_cases.py` 的 `PROCESS` 原文）复现：
    `order_violations` = 8 条 `unknown_process:*`，confirm 同样 409；
  · 工序闭集（19 条）是第 6 批的规格，样本导入的车间说法与它之间**没有任何映射层**，
    也没有入库自检 —— 盒型一入库就注定"永远不能确认"。

禁止为了让红测转绿而修改本文件。
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

DEPLOY_SH = ROOT / "scripts" / "deploy_34_bare.sh"

from tech_app.backend import config  # noqa: E402
from tech_app.backend.storage import (da_db, da_repo, da_seed_packaging as seed,  # noqa: E402
                                      kb_repo, meta_backend, store)
from tech_app.backend.services import packaging_bom  # noqa: E402

PID = "pkgroutealias01"
REQ_NO = "REQ-ALIAS-0001"

WINE = "YT-DWG-WINE-700ML"
ROUND = "YT-DWG-ROUND-10PC"

#: 两份真实 DWG 实样的工艺模板工序名（逐字来自样本导入路径，不许改写样本去迁就闭集）。
SAMPLE_STEPS = {
    WINE: [(10, "材料开料", 20.0), (20, "面纸印刷与覆膜", 15.0), (30, "模切/半穿", 12.0),
           (40, "V槽", 18.0), (50, "裱贴包面", 30.0), (60, "内盒成型", 25.0),
           (70, "EVA与托件制作", 22.0), (80, "总装与检验", 28.0)],
    ROUND: [(10, "纸张印刷覆膜", 20.0), (20, "灰板与面纸模切", 14.0), (30, "纸管成型切管", 26.0),
            (40, "V槽围边", 18.0), (50, "围边裱贴", 30.0), (60, "内托复合", 24.0),
            (70, "天地盖组装", 28.0), (80, "装配检验包装", 30.0)],
}
SAMPLE_NAMES = sorted({name for rows in SAMPLE_STEPS.values() for _, name, _ in rows})

#: 19 条闭集（Spec packaging-process-route.md §2.2）。
CATALOG = {
    "灰板开料": 10, "V 槽开槽": 20, "灰板成型": 30, "面纸印刷": 40, "表面处理": 45,
    "覆膜": 50, "烫金": 60, "丝印": 70, "UV 上光": 80, "压凹凸": 90, "面纸模切": 100,
    "铰链贴合": 110, "磁铁嵌入": 120, "机裱": 130, "手裱": 140, "内托组装": 150,
    "组装": 160, "检验": 170, "清洁包装": 180,
}

REQ = {"industry": "packaging", "packaging_product_name": "700ML双开门酒盒",
       "quote_quantity": 1000, "inner_length": 220, "inner_width": 89, "inner_height": 89,
       "fit_clearance": 0.5, "box_type": WINE, "closure_type": "双开门/对开，磁吸",
       "v_groove": "是", "face_paper_gsm": 225, "print_colors": 4, "lamination": "覆光膜"}


def route_mod():
    return importlib.import_module("tech_app.backend.services.packaging_route")


def seed_module():
    return importlib.import_module("tech_app.backend.storage.da_seed_packaging")


def sample_process_rows(box_code, part_code="SAMPLE-P01"):
    return [{"box_type_code": box_code, "part_code": part_code, "seq": seq,
             "step_name": name, "standard_seconds": seconds, "equipment": "", "qc_point": ""}
            for seq, name, seconds in SAMPLE_STEPS[box_code]]


def table_snapshot(box_code=WINE):
    """用真实演示种子 + 样本工艺模板名造知识库快照（不合成假盒型）。"""
    box = dict(seed.BOX_TYPES[0])
    box.update({"box_type_code": box_code, "business_status": "权威实样",
                "name": "700ML双开门酒盒（DWG实样）"})
    parts = [dict(row, box_type_code=box_code) for row in seed.PART_TEMPLATES[:3]]
    return {
        "kb_packaging_box_type": [box],
        "kb_packaging_part_template": parts,
        "kb_packaging_process_template": sample_process_rows(box_code),
        "kb_packaging_match_weight": [dict(r) for r in seed.MATCH_WEIGHTS],
        "kb_material": [dict(item["material"], industry="packaging", status="active")
                        for item in seed.MATERIALS],
        "kb_material_property": [dict(prop, industry="packaging")
                                 for item in seed.MATERIALS
                                 for prop in item.get("properties") or []],
    }


class Harness(unittest.TestCase):
    """独立临时 SQLite + 独立 meta 目录 + 可替换知识库快照（照抄第 6 批红测的装置）。"""

    box_code = WINE

    def setUp(self):
        self._cache = dict(kb_repo._CACHE)
        self._da_path = config.DA_DB_PATH
        self.db_file = pathlib.Path(tempfile.mkdtemp()) / "closure.sqlite3"
        self._patch_da = mock.patch.object(config, "DA_DB_PATH", self.db_file)
        self._patch_da.start()
        da_db.init_db(self.db_file)
        self._backend = meta_backend._backend
        meta_backend._backend = meta_backend.JsonMetaBackend(pathlib.Path(tempfile.mkdtemp()))
        tables = table_snapshot(self.box_code)
        kb_repo._CACHE["version"] = "closure-probe"
        kb_repo._CACHE["tables"] = tables
        self.tables = tables

    def tearDown(self):
        meta_backend._backend = self._backend
        self._patch_da.stop()
        da_db.close_conn()
        kb_repo._CACHE.clear()
        kb_repo._CACHE.update(self._cache)

    # ---- 需求 / 盒型 / BOM ------------------------------------------------- #
    def prepare(self):
        data = dict(REQ, box_type=self.box_code)
        store.save_requirement(PID, {"project_id": PID, "requirement_no": REQ_NO,
                                     "title": "工序别名用例", "status": "pending_confirmation",
                                     "data": data})
        da_repo.save_box_match({"project_id": PID, "requirement_no": REQ_NO,
                                "industry": "packaging", "engine_version": "packaging_match_v1",
                                "inputs": {}, "candidates": [], "missing_inputs": [],
                                "suggested_box_type": self.box_code})
        da_repo.update_box_match_decision(PID, REQ_NO, decision="confirmed",
                                          confirmed_box_type=self.box_code,
                                          confirmed_by="PE1", confirmed_at=da_db.now())
        packaging_bom.build_bom(PID, REQ_NO)


# --------------------------------------------------------------------------- #
# A. 别名表与归一化纯函数
# --------------------------------------------------------------------------- #
class AAliasTable(Harness):
    def test_a1_process_aliases_exists_and_is_a_table(self):
        module = route_mod()
        table = getattr(module, "PROCESS_ALIASES", None)
        self.assertIsNotNone(
            table, "packaging_route 缺少模块级 PROCESS_ALIASES：闭集外的模板工序名没有映射层"
                   "（Spec §2.1 / §3.1）")
        self.assertIsInstance(table, dict)

    def test_a2_every_sample_step_name_is_covered(self):
        table = getattr(route_mod(), "PROCESS_ALIASES", None) or {}
        missing = [name for name in SAMPLE_NAMES if name not in table]
        self.assertEqual([], missing,
                         "这些样本工序名没有别名映射：%s（两份实样共 %d 个，Spec §3.1）"
                         % (missing, len(SAMPLE_NAMES)))

    def test_a3_mapping_targets_stay_inside_the_catalog(self):
        table = getattr(route_mod(), "PROCESS_ALIASES", None) or {}
        bad = {}
        for name, target in table.items():
            values = target if isinstance(target, (list, tuple)) else [target]
            if not values:
                bad[name] = "空映射"
                continue
            outside = [v for v in values if v not in CATALOG]
            if outside:
                bad[name] = outside
        self.assertEqual({}, bad, "别名映射的目标必须全部在 19 条闭集内（Spec §3.1）：%s" % bad)

    def test_a4_normalize_step_name_shape(self):
        module = route_mod()
        fn = getattr(module, "normalize_step_name", None)
        self.assertIsNotNone(fn, "packaging_route 缺少纯函数 normalize_step_name（Spec §2.1）")
        self.assertEqual(("面纸印刷",), tuple(fn("面纸印刷")), "闭集内的名字原样返回单元素 tuple")
        self.assertEqual((), tuple(fn("抛光")), "闭集外且无映射的名字必须返回空 tuple（不许猜）")

    def test_a5_normalize_is_idempotent_on_catalog_names(self):
        module = route_mod()
        fn = getattr(module, "normalize_step_name", None)
        if fn is None:
            self.fail("normalize_step_name 不存在（Spec §2.1）")
        for name in list(CATALOG)[:5]:
            self.assertEqual((name,), tuple(fn(name)))


# --------------------------------------------------------------------------- #
# B. 入库自检
# --------------------------------------------------------------------------- #
class BIngestGuard(Harness):
    def test_b1_assert_step_names_mappable_exists(self):
        fn = getattr(seed_module(), "assert_step_names_mappable", None)
        self.assertIsNotNone(
            fn, "da_seed_packaging 缺少 assert_step_names_mappable：样本模板入库没有任何自检"
                "（Spec §2.2）")

    def test_b2_sample_rows_pass_the_guard(self):
        fn = getattr(seed_module(), "assert_step_names_mappable", None)
        if fn is None:
            self.fail("assert_step_names_mappable 不存在（Spec §2.2）")
        for box_code in (WINE, ROUND):
            fn(sample_process_rows(box_code))          # 有映射 → 不抛

    def test_b3_unknown_name_is_named_and_refused(self):
        fn = getattr(seed_module(), "assert_step_names_mappable", None)
        if fn is None:
            self.fail("assert_step_names_mappable 不存在（Spec §2.2）")
        rows = sample_process_rows(WINE) + [{"box_type_code": WINE, "part_code": "SAMPLE-P01",
                                            "seq": 90, "step_name": "抛光"}]
        with self.assertRaises(ValueError) as ctx:
            fn(rows)
        self.assertIn("unknown_process:抛光", str(ctx.exception),
                      "拒绝时必须点名到具体工序（Spec §2.2 / §3.4）")


# --------------------------------------------------------------------------- #
# C. 端到端：样本模板 → 路线 → 确认
# --------------------------------------------------------------------------- #
class CEndToEnd(Harness):
    def test_c1_built_route_only_contains_catalog_names(self):
        self.prepare()
        built = route_mod().build_route(PID, REQ_NO)
        names = [row["step_name"] for row in built["steps"]]
        outside = [name for name in names if name not in CATALOG]
        self.assertEqual([], outside, "路线里还有闭集外的工序名：%s（Spec §3.2）" % outside)
        self.assertEqual([], built.get("gaps", {}).get("order_violations"),
                         "归一化后 order_violations 必须为空（Spec §3.2）")

    def test_c2_confirm_route_succeeds_for_authoritative_sample(self):
        self.prepare()
        module = route_mod()
        module.build_route(PID, REQ_NO)
        route = module.confirm_route(PID, REQ_NO, actor={"username": "PE1"})
        self.assertEqual("confirmed", route["status"],
                         "权威实样盒型的路线必须能确认（34 上现在 409 route_not_confirmable，"
                         "Spec §1.1 / §3.4）")

    def test_c3_steps_are_unique_and_ascending(self):
        self.prepare()
        built = route_mod().build_route(PID, REQ_NO)
        names = [row["step_name"] for row in built["steps"]]
        self.assertEqual(len(names), len(set(names)), "1:N 映射后同名工序必须去重（Spec §3.2）")
        numbers = [row["step_no"] for row in built["steps"]]
        self.assertEqual(sorted(numbers), numbers, "step_no 必须严格递增（Spec §3.2）")

    def test_c4_one_to_many_mapping_does_not_invent_seconds(self):
        built = None
        self.prepare()
        built = route_mod().build_route(PID, REQ_NO)
        table = getattr(route_mod(), "PROCESS_ALIASES", None) or {}
        many = {k: v for k, v in table.items() if isinstance(v, (list, tuple)) and len(v) > 1}
        if not many:
            return                                              # 全是 1:1 时这条不适用
        by_name = {row["step_name"]: row for row in built["steps"]}
        missing = [name for target in many.values() for name in list(target)[1:]
                   if by_name.get(name, {}).get("standard_seconds") is not None]
        self.assertEqual([], missing,
                         "1:N 映射出的后续工序不许编工时，必须记 None 并进 needs_standard_time"
                         "（Spec §3.3）：%s" % missing)
        needs = set(built.get("gaps", {}).get("needs_standard_time") or [])
        for target in many.values():
            for name in list(target)[1:]:
                if name not in by_name:
                    continue        # 本盒型的模板里没用这条别名行，本条不适用（不是缺口）
                self.assertIn(name, needs, "1:N 后续工序必须进 needs_standard_time（Spec §3.3）")


# --------------------------------------------------------------------------- #
# D. 权威实样交付自检
# --------------------------------------------------------------------------- #
class DAuthoritativeSelfCheck(unittest.TestCase):
    def test_d1_deploy_selfcheck_covers_authoritative_samples(self):
        text = DEPLOY_SH.read_text(encoding="utf-8")
        self.assertIn("权威实样", text,
                      "部署脚本第 6b 步必须对 business_status='权威实样' 的盒型跑路线自检"
                      "（Spec §2.3 / §3.4）")
        self.assertTrue(re.search(r"confirm", text),
                        "权威实样自检必须真的调 confirm（Spec §3.4）")


# --------------------------------------------------------------------------- #
# E. 护栏（这些现在就该绿：不许用放开闭集的方式绕过）
# --------------------------------------------------------------------------- #
class ECatalogStaysClosed(unittest.TestCase):
    def test_e1_catalog_is_still_the_19_names(self):
        module = route_mod()
        catalog = getattr(module, "PROCESS_CATALOG", {})
        self.assertEqual(19, len(catalog), "19 条闭集不许增删（Spec §4）")
        self.assertEqual({}, {k: v for k, v in catalog.items() if k not in CATALOG})

    def test_e2_unknown_names_still_violate_order(self):
        module = route_mod()
        codes = module.validate_order([{"step_no": 10, "step_name": "抛光"},
                                       {"step_no": 20, "step_name": "面纸印刷"}])
        self.assertIn("unknown_process:抛光", codes,
                      "闭集外的名字必须照旧报 unknown_process，不许在 validate_order 里豁免"
                      "（Spec §4）")


if __name__ == "__main__":
    unittest.main()

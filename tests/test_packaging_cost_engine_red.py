"""红测：包装专用成本引擎 —— 包装第 7 批。

Spec：docs/specs/packaging-cost-engine.md
依赖：第 1–6 批（四行业注册表 / 包装需求模板 / 包装知识库 / 盒型匹配确认 / 参数化 BOM /
工艺路线）已完成。第 6 批红测 `tests/test_packaging_process_route_red.py` 在本批开工前已 OK。

现状缺口（实测，不是推断）：
  · `tech_app/backend/services/packaging_cost.py` 不存在 —— 全仓没有任何包装成本引擎；
  · 现有三行业走 `cost_model.py` 的固定系数模型（人工/制费/加工 = 材料 × 固定系数），
    包装 24 个成本类别无法表达；
  · `kb_packaging_cost_formula` 的 7 条种子 `expression` 是中文散文、`review_status='draft'`，
    不可执行；库里没有包材明细，也没有工装寿命规则；
  · `wip_cost_estimate` / `wip_cost_item` 是设计 IR 口径（cost_type 只有四类），装不下包装类别；
  · `da_schema.sql` 没有 `wip_packaging_cost_*` 三张表，`main.py` 与前端没有 packaging-cost。

黄金数据：`报价逻辑-0903.xlsx` 可见 Sheet `报价-工费率`（14 行逐行金额 + 小计 + 成本 +
总成本 77.685201899648021）与 `包装运输`（11 行单件包装成本 + 合计 3.0913797678856545）。
本文件把已核对过的数值内联为常量，**不读工作簿**（工作簿是客户样例，不入库）。
「报价-行业标准」是同一业务的另一套粗算口径，本批不采用（Spec §1.2）。

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

COST_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_cost.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
SCHEMA_SQL = ROOT / "tech_app" / "backend" / "storage" / "da_schema.sql"

from tech_app.backend import config  # noqa: E402
from tech_app.backend.storage import (da_db, da_repo, da_seed_packaging as seed,  # noqa: E402
                                      kb_repo, meta_backend, store)
from tech_app.backend.services import (cost_model, industry_templates,  # noqa: E402
                                       packaging_bom, packaging_match, packaging_route)

PID = "pkgcost00001"
REQ_NO = "REQ-PKG-C0001"
BOX_MAIN = "YT-RB-01001-A"
BOX_BOOK = "YT-RB-02001-A"

REQ = {"industry": "packaging", "packaging_product_name": "礼盒", "packaging_category": "礼盒",
       "quote_quantity": 1000, "inner_length": 200, "inner_width": 150, "inner_height": 80,
       "fit_clearance": 1.8, "box_type": BOX_MAIN, "closure_type": "天地盖", "v_groove": "是",
       "face_paper_gsm": 200, "lamination": "是", "hot_stamping": "是", "print_colors": "CMYK"}

# --------------------------------------------------------------------------- #
# 0903 黄金数据
# --------------------------------------------------------------------------- #
# 中文列名（0903 的列标题）→ 本批 COST_CATEGORIES 的 code
NAME_TO_CODE = {
    "材料价": "material", "普通印刷": "print", "UV印刷": "print_uv", "复膜": "lamination",
    "覆转移膜": "transfer_film", "热烫-平压": "hot_stamp_flat", "热烫-圆压": "hot_stamp_round",
    "冷烫": "cold_stamp", "丝印": "silk_screen", "过光油": "varnish", "防刮花光/哑油": "anti_scratch",
    "PET环保吸塑油": "pet_oil", "视高迪UV": "visidi_uv", "压纹": "texture", "击凹/凸": "emboss_deboss",
    "裱纸": "mounting", "啤/切": "die_cutting", "折页\n/装钉": "folding", "V槽": "v_groove",
    "机贴盒/贴双面胶": "auto_mount", "双面胶": "double_tape", "胶水": "glue",
    "人工/全检/包装": "labor", "其他": "other",
}

# fmt: off
GOLDEN_ROWS = [
    # 报价-工费率 第 2 行
    {"excel_row": 2, "part_name": '左右盖面纸', "loss_rate": 0.23,
     "categories": {
         '材料价': 0.7954641993584073,
         'UV印刷': 0.9865756637168142,
         '复膜': 1.376611258004827,
         '热烫-平压': 1.343266666666667,
         '啤/切': 0.8347876923076923,
         '胶水': 0.46050199999999997,
     },
     "subtotal": 5.797207480054408, "cost": 7.130565200466922},
    # 报价-工费率 第 3 行
    {"excel_row": 3, "part_name": '左右盖灰板', "loss_rate": 0.23,
     "categories": {
         '材料价': 4.486036200663717,
         '啤/切': 0.4006466666666667,
     },
     "subtotal": 4.886682867330384, "cost": 6.010619926816372},
    # 报价-工费率 第 4 行
    {"excel_row": 4, "part_name": '左右盖内围条', "loss_rate": 0.23,
     "categories": {
         '材料价': 0.6991404789823009,
         'UV印刷': 0.8507331858407079,
         '复膜': 0.7634626093322607,
         '啤/切': 0.8347876923076923,
         '胶水': 0.0036408,
     },
     "subtotal": 3.151764766462962, "cost": 3.876670662749443},
    # 报价-工费率 第 5 行
    {"excel_row": 5, "part_name": '内盒链接皮盒灰板', "loss_rate": 0.23,
     "categories": {
         '材料价': 1.99202773340708,
         '啤/切': 0.2952133333333334,
         'V槽': 0.816,
     },
     "subtotal": 3.1032410667404133, "cost": 3.8169865120907085},
    # 报价-工费率 第 6 行
    {"excel_row": 6, "part_name": '内盒链接皮盒面纸+衬纸', "loss_rate": 0.23,
     "categories": {
         '材料价': 0.7904769033185843,
         'UV印刷': 1.0249140707964601,
         '复膜': 1.455096857429606,
         '啤/切': 0.8347876923076923,
         '胶水': 0.4921111,
     },
     "subtotal": 4.597386623852342, "cost": 5.654785547338381},
    # 报价-工费率 第 7 行
    {"excel_row": 7, "part_name": '内盒面纸', "loss_rate": 0.23,
     "categories": {
         '材料价': 1.560407674247788,
         '啤/切': 0.8347876923076923,
         '胶水': 0.5124944,
     },
     "subtotal": 2.9076897665554804, "cost": 3.5764584128632406},
    # 报价-工费率 第 8 行
    {"excel_row": 8, "part_name": '内盒灰板', "loss_rate": 0.23,
     "categories": {
         '材料价': 5.0861502975663715,
         '啤/切': 0.4006466666666667,
         'V槽': 0.816,
     },
     "subtotal": 6.302796964233038, "cost": 7.752440266006637},
    # 报价-工费率 第 9 行
    {"excel_row": 9, "part_name": '内盒内玉EPE', "loss_rate": 0.02,
     "categories": {
         '材料价': 7.628318584070796,
     },
     "subtotal": 7.628318584070796, "cost": 7.780884955752212},
    # 报价-工费率 第 10 行
    {"excel_row": 10, "part_name": '内盒内玉面卡+内盒内底', "loss_rate": 0.23,
     "categories": {
         '材料价': 0.4241651415929204,
         'UV印刷': 0.7714429203539823,
         '复膜': 0.5636042343121481,
         '啤/切': 0.8049738461538461,
         '胶水': 0.145595,
     },
     "subtotal": 2.7097811424128975, "cost": 3.333030805167864},
    # 报价-工费率 第 11 行
    {"excel_row": 11, "part_name": '说明书卡', "loss_rate": 0.23,
     "categories": {
         '材料价': 0.4305139845132743,
         'UV印刷': 1.4044534955752215,
         '复膜': 0.6329319530490749,
         '热烫-平压': 4.137386666666667,
         '啤/切': 0.8347876923076923,
     },
     "subtotal": 7.44007379211193, "cost": 9.151290764297674},
    # 报价-工费率 第 12 行
    {"excel_row": 12, "part_name": '铭牌面纸', "loss_rate": 0.23,
     "categories": {
         '材料价': 0.02676255136725664,
         'UV印刷': 0.637047610619469,
         '复膜': 0.1893736473644409,
         '热烫-平压': 1.0801306666666666,
         '胶水': 0.00639804,
     },
     "subtotal": 1.939712516017833, "cost": 2.3858463947019346},
    # 报价-工费率 第 13 行
    {"excel_row": 13, "part_name": '铭牌灰板', "loss_rate": 0.23,
     "categories": {
         '材料价': 0.5164029345132745,
         '裱纸': 0.1713285714285714,
         '啤/切': 0.1982146666666667,
     },
     "subtotal": 0.8859461726085125, "cost": 1.0897137923084703},
    # 报价-工费率 第 14 行
    {"excel_row": 14, "part_name": '方形磁铁', "loss_rate": 0.02,
     "categories": {
         '材料价': 1.2743362831858407,
     },
     "subtotal": 1.2743362831858407, "cost": 1.2998230088495575},
    # 报价-工费率 第 15 行
    {"excel_row": 15, "part_name": '手工组装', "loss_rate": 0.23,
     "categories": {
         '人工/全检/包装': 8.444444444444445,
     },
     "subtotal": 8.444444444444445, "cost": 10.386666666666667},
]
# fmt: on

# 报价-工费率!AT2 / AU2 / AV2
GOLDEN_PACKAGING = 3.0913797678856545
GOLDEN_FREIGHT = 1.3480392156862744
GOLDEN_TOTAL = 77.685201899648021
GOLDEN_SUBTOTAL = 73.24578291607608

# 逐成本类别 Σ(金额 × (1+损耗))，来自 报价-工费率 第 2–15 行
GOLDEN_CATEGORY_SUMS = {
    "material": 29.7539921270, "print_uv": 6.9804553447, "lamination": 6.1267290882,
    "hot_stamp_flat": 8.0697643200, "mounting": 0.2107341429, "die_cutting": 7.7165693785,
    "v_groove": 2.0073600000, "glue": 1.9935118482, "labor": 10.3866666667,
}
# 成本细分 10 分组
GOLDEN_REPORT_GROUPS = {
    "材料": 31.7475039752, "印刷": 6.9804553447, "覆膜": 6.1267290882, "烫金": 8.0697643200,
    "丝印": 0.0, "裱纸": 0.2107341429, "模切": 7.7165693785, "开槽": 2.0073600000,
    "手工": 10.3866666667, "包装": 4.4394189836,
}

# 包装运输 第 2–12 行的单件包装成本（11 行，其余为 0）
GOLDEN_CONTENTS = {
    "彩盒": (2.1108074127397023, dict(length_mm=520, width_mm=420, height_mm=425, usage_qty=1,
                                    material_price=3, units_per_pack=4)),
    "平卡": (0.28404101945273708, dict(length_mm=510, width_mm=410, usage_qty=2,
                                    material_price=1.55, units_per_pack=4)),
    "牛皮纸轧带": (0.1803071469026549, dict(length_mm=787, width_mm=1092, usage_qty=4,
                                       material_price=7800, units_per_pack=4, gsm=30)),
    "卡板": (0.51622418879056053, dict(usage_qty=1, material_price=70, units_per_pack=120)),
}

CATEGORY_COUNT = 24


def load_cost_module():
    """成本引擎；不存在时返回 None（用例给明确断言，不抛 ImportError）。"""
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
    prices = [dict(item["price"], material_code=item["material"]["material_code"], price_id=index + 1)
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
    extra = getattr(seed, "COST_CONTENTS", None)
    if extra:
        tables["kb_packaging_cost_content"] = [dict(r) for r in extra]
    tooling = getattr(seed, "TOOLING_RULES", None)
    if tooling:
        tables["kb_packaging_tooling_rule"] = [dict(r) for r in tooling]
    return tables


def golden_project_lines():
    """把 0903 的 14 行还原成「人工录入金额」的成本行（Spec §4.5 的 amount 通道）。"""
    lines = []
    for index, row in enumerate(GOLDEN_ROWS):
        for name, amount in row["categories"].items():
            lines.append({
                "seq": (index + 1) * 100 + len(lines) % 100,
                "part_code": "GOLD-%02d" % row["excel_row"],
                "part_name": row["part_name"],
                "cost_category": NAME_TO_CODE[name],
                "amount": amount,
                "loss_rate": row["loss_rate"],
                "source": "human",
            })
    return lines


class CostCase(unittest.TestCase):
    """共用：可替换知识库快照 + 独立临时 SQLite + 独立 meta 目录。"""

    def setUp(self):
        self._cache = dict(kb_repo._CACHE)
        self._da_path = config.DA_DB_PATH
        self.db_file = pathlib.Path(tempfile.mkdtemp()) / "cost.sqlite3"
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
    def cost_mod(self):
        module = load_cost_module()
        self.assertIsNotNone(
            module, "缺少 tech_app/backend/services/packaging_cost.py（Spec §4.5）")
        return module

    def actor(self):
        return {"username": "wangjingli", "role": "process_manager"}

    # ---- 需求单 / 确认盒型 / BOM / 路线 ------------------------------------ #
    def save_requirement(self, project_id=PID, requirement_no=REQ_NO, **over):
        data = dict(REQ)
        data.update(over)
        store.save_requirement(project_id, {
            "project_id": project_id, "requirement_no": requirement_no,
            "title": "包装成本用例", "status": "pending_confirmation", "data": data})

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

    def confirm_route(self, project_id=PID, requirement_no=REQ_NO):
        packaging_route.build_route(project_id, requirement_no)
        return packaging_route.confirm_route(project_id, requirement_no, actor=self.actor())

    def prepare(self, code=BOX_MAIN, with_bom=True, with_route=True, **req_over):
        """需求 + 确认盒型（+ 第 5 批 BOM）（+ 第 6 批已确认路线）。"""
        self.save_requirement(**req_over)
        self.confirm_box(code)
        if with_bom:
            self.build_bom()
        if with_route:
            self.confirm_route()

    # ---- 便捷调用 ---------------------------------------------------------- #
    def build_cost(self, **kwargs):
        kwargs.setdefault("requirement_no", REQ_NO)
        return self.cost_mod().build_cost(PID, **kwargs)

    def load_cost(self, **kwargs):
        kwargs.setdefault("requirement_no", REQ_NO)
        return self.cost_mod().load_cost(PID, **kwargs)

    @staticmethod
    def code_of(name):
        return NAME_TO_CODE[name]

    @staticmethod
    def db_rows(sql, params=()):
        return da_db.query_all(sql, params)


# --------------------------------------------------------------------------- #
# A. 类别与公式闭集
# --------------------------------------------------------------------------- #
# 0903 报价-工费率 的 24 个成本列（S→AP），(code, 中文名)
EXPECTED_CATEGORIES = (
    ("material", "材料价"), ("print", "普通印刷"), ("print_uv", "UV印刷"),
    ("lamination", "复膜"), ("transfer_film", "覆转移膜"), ("hot_stamp_flat", "热烫-平压"),
    ("hot_stamp_round", "热烫-圆压"), ("cold_stamp", "冷烫"), ("silk_screen", "丝印"),
    ("varnish", "过光油"), ("anti_scratch", "防刮花光/哑油"), ("pet_oil", "PET环保吸塑油"),
    ("visidi_uv", "视高迪UV"), ("texture", "压纹"), ("emboss_deboss", "击凹/凸"),
    ("mounting", "裱纸"), ("die_cutting", "啤/切"), ("folding", "折页/装钉"),
    ("v_groove", "V槽"), ("auto_mount", "机贴盒/贴双面胶"), ("double_tape", "双面胶"),
    ("glue", "胶水"), ("labor", "人工/全检/包装"), ("other", "其他"),
)
# 13 组、恰好覆盖 24 + 2 个类别（2026-09-20 修复第 1 批修订，权威定义见
# docs/specs/packaging-cost-rule-routing.md §2.1）。原 10 组只覆盖工作簿 成本细分 的 13 个类别列，
# 另外 13 个类别在成本细分里找不到 —— 修订后由「表面处理 / 装订贴盒 / 其他费用」承载。
EXPECTED_REPORT_GROUPS = {
    "材料": ("material", "glue"), "印刷": ("print", "print_uv"),
    "覆膜": ("lamination", "transfer_film"),
    "烫金": ("hot_stamp_flat", "hot_stamp_round", "cold_stamp"), "丝印": ("silk_screen",),
    "表面处理": ("varnish", "anti_scratch", "pet_oil", "visidi_uv", "texture", "emboss_deboss"),
    "裱纸": ("mounting",), "模切": ("die_cutting",),
    "装订贴盒": ("folding", "auto_mount", "double_tape"),
    "开槽": ("v_groove",), "手工": ("labor",), "其他费用": ("other",),
    "包装": ("packaging", "freight"),
}
CATEGORIES_WITH_FORMULA = {
    "material", "print_uv", "lamination", "hot_stamp_flat", "mounting", "die_cutting",
    "v_groove", "glue", "labor",
}
CONTENT_FORMULAS = (
    "PKG-P-CARTON", "PKG-P-PAD", "PKG-P-DIVIDER", "PKG-P-BAG", "PKG-P-CRAFT-PAPER",
    "PKG-P-STRAP", "PKG-P-CORNER-TOP", "PKG-P-CORNER-PAPER", "PKG-P-LABEL",
    "PKG-P-BOARD", "PKG-P-PALLET",
)


class ACatalogAndClosures(CostCase):
    def test_a1_cost_categories_are_the_24_workbook_columns(self):
        module = self.cost_mod()
        got = tuple((item[0], item[1]) for item in module.COST_CATEGORIES)
        self.assertEqual(got, EXPECTED_CATEGORIES,
                         "24 个成本类别必须与 0903 报价-工费率 的 S→AP 列逐条一致（Spec §2.3）")
        self.assertEqual(len(module.COST_CATEGORIES), CATEGORY_COUNT)

    def test_a2_project_categories_are_packaging_and_freight(self):
        module = self.cost_mod()
        codes = tuple(item[0] for item in module.PROJECT_COST_CATEGORIES)
        self.assertEqual(codes, ("packaging", "freight"),
                         "包装/运输是项目级类别，不属于任何部件行（Spec §2.3）")

    def test_a3_report_groups_partition_every_category(self):
        module = self.cost_mod()
        groups = {key: tuple(value) for key, value in module.REPORT_GROUPS.items()}
        self.assertEqual(groups, EXPECTED_REPORT_GROUPS,
                         "13 个报告分组必须覆盖 24+2 个类别（Spec 修复第 1 批 §2.1）")
        flattened = [code for members in groups.values() for code in members]
        self.assertEqual(len(flattened), len(set(flattened)), "报告分组不得重复归类")
        known = {code for code, _ in EXPECTED_CATEGORIES} | {"packaging", "freight"}
        self.assertEqual(set(flattened), known, "报告分组必须正好覆盖 24 + 2 个类别")

    def test_a4_formula_catalog_is_complete_and_traceable(self):
        module = self.cost_mod()
        catalog = module.FORMULA_CATALOG
        for code, entry in catalog.items():
            for field in ("cost_category", "expression", "minimum_charge", "rounding",
                          "rate_code", "source_ref"):
                self.assertIn(field, entry, "%s 缺字段 %s（Spec §2.6）" % (code, field))
            self.assertIsInstance(entry["expression"], str)
            self.assertTrue(entry["expression"].strip(), "%s 表达式为空" % code)
            self.assertFalse(str(entry["source_ref"]).startswith("#"),
                             "%s 的 source_ref 必须指向 0903 的可见 Sheet" % code)
            self.assertIn("报价逻辑-0903.xlsx/", str(entry["source_ref"]))
        for category in CATEGORIES_WITH_FORMULA:
            self.assertTrue(any(entry["cost_category"] == category for entry in catalog.values()),
                            "类别 %s 必须有公式（Spec §2.6）" % category)
        for code in ("PKG-C-MATERIAL", "PKG-C-LAMINATION", "PKG-C-HOT-STAMP-FLAT",
                     "PKG-C-DIE-CUT", "PKG-C-V-GROOVE", "PKG-C-GLUE", "PKG-C-LABOR"):
            self.assertIn(code, catalog, "公式目录缺 %s" % code)
        for code in CONTENT_FORMULAS:
            self.assertIn(code, catalog, "公式目录缺包材公式 %s" % code)
            self.assertEqual(catalog[code]["cost_category"], "packaging")

    def test_a5_every_expression_only_uses_whitelisted_variables(self):
        module = self.cost_mod()
        allowed = set(module.LINE_VARIABLES)
        for code, entry in module.FORMULA_CATALOG.items():
            if entry["cost_category"] == "packaging":
                allowed_here = allowed | {"bag_unit_price"}
            else:
                allowed_here = allowed
            for name in module.expression_variables(entry["expression"]):
                self.assertIn(name, allowed_here,
                              "%s 用了白名单外变量 %s（Spec §2.4）" % (code, name))

    def test_a6_tooling_loss_and_step_rate_closures(self):
        module = self.cost_mod()
        self.assertEqual(set(module.TOOLING_MODES),
                         {"one_off", "lifetime", "committed", "refund", "customer_supplied"},
                         "工装五种模式闭集（Spec §2.9）")
        self.assertEqual(set(module.LOSS_BASE_SCOPES),
                         {"material_only", "material_process", "material_process_and_labor",
                          "material_process_labor_packaging"},
                         "损耗基数范围闭集（Spec §2.8）")
        self.assertEqual(tuple(module.TOOLING_PROCESSES),
                         ("烫金", "丝印", "击凹凸", "模切", "装配线"),
                         "工装工序必须与第 5 批 TOOLING_KEYWORDS 同源")
        self.assertEqual(dict(module.STEP_RATE_MAP), {
            "手裱": "RATE-PKG-LABOR-HANDMOUNT", "机裱": "RATE-PKG-LABOR-LAMINATE",
            "组装": "RATE-PKG-LABOR-ASSEMBLY", "检验": "RATE-PKG-LABOR-ASSEMBLY",
            "清洁包装": "RATE-PKG-LABOR-ASSEMBLY"}, "工序 → 费率映射（Spec §2.6.2）")

    def test_a7_version_profile_and_error(self):
        module = self.cost_mod()
        self.assertEqual(module.ENGINE_VERSION, "packaging_cost_v1")
        self.assertEqual(module.COST_PROFILE, "packaging_v1")
        self.assertEqual(module.GENERIC_PROFILE, "generic_v1")
        self.assertEqual(module.profile_for("packaging"), "packaging_v1")
        for industry in ("semiconductor", "battery", "appliance"):
            self.assertEqual(module.profile_for(industry), "generic_v1",
                             "三个原行业必须继续走 generic_v1（Spec §2.2）")
        error = module.CostError("x", 409, "demo")
        self.assertEqual((error.message, error.status_code, error.code), ("x", 409, "demo"))

    def test_a8_engine_never_executes_dynamic_code_or_touches_network(self):
        source = inspect.getsource(self.cost_mod())
        for forbidden in ("eval(", "exec(", "compile(", "__import__", "subprocess",
                          "os.system", "pickle.loads", "requests.", "urllib"):
            self.assertFalse(forbidden in source,
                             "成本引擎不得出现 %s：不执行动态代码、不联网（Spec §5）" % forbidden)


# --------------------------------------------------------------------------- #
# B. 0903 黄金样例（报价-工费率）
# --------------------------------------------------------------------------- #
class BGoldenSamples(CostCase):
    def test_b1_material_formula_matches_the_workbook(self):
        amount = self.cost_mod().compute_line("material", {
            "cut_length": 889, "cut_width": 705, "gsm": 157, "ton_price": 6300,
            "imposition_count": 1, "proof_base": 450, "quote_quantity": 1000,
            "tax_factor": 1.13})["amount"]
        self.assertAlmostEqual(amount, 0.79546419935840729, places=6,
                               msg="报价-工费率!S2 材料价")

    def test_b2_lamination_formula_matches_the_workbook(self):
        line = self.cost_mod().compute_line("lamination", {
            "machine_length": 889, "machine_width": 700, "imposition_count": 1,
            "quote_quantity": 1000, "setup_minutes": 30, "capacity_per_hour": 5500,
            "equipment_rate": 197, "labor_rate": 145, "film_price": 1.7,
            "film_thickness_um": 18, "film_kg_price": 18.5, "tax_factor": 1.13})
        self.assertAlmostEqual(line["amount"], 1.3766112580048271, places=6,
                               msg="报价-工费率!V2 复膜")
        self.assertFalse(line["min_charge_applied"],
                         "1000 件时 200/1000 = 0.2 低于表达式值，最低收费不该命中")

    def test_b3_hot_stamp_formula_matches_the_workbook(self):
        amount = self.cost_mod().compute_line("hot_stamp_flat", {
            "hot_area_mm2": 100 * 75 * 4, "foil_price": 8.5, "imposition_count": 1,
            "quote_quantity": 1000, "setup_minutes": 200, "capacity_per_hour": 5000,
            "equipment_rate": 193, "labor_rate": 115})["amount"]
        self.assertAlmostEqual(amount, 1.3432666666666671, places=6,
                               msg="报价-工费率!X2 热烫-平压（4 处 100×75mm）")

    def test_b4_die_cut_formula_matches_the_workbook(self):
        amount = self.cost_mod().compute_line("die_cutting", {
            "imposition_count": 1, "quote_quantity": 1000, "setup_minutes": 120,
            "capacity_per_hour": 6500, "equipment_rate": 197.52,
            "labor_rate": 190.06})["amount"]
        self.assertAlmostEqual(amount, 0.83478769230769234, places=6,
                               msg="报价-工费率!AI2 啤/切")

    def test_b5_glue_and_v_groove_match_the_workbook(self):
        module = self.cost_mod()
        glue = module.compute_line("glue", {
            "machine_length": 889, "machine_width": 700, "imposition_count": 1,
            "glue_unit_price": 0.74})["amount"]
        self.assertAlmostEqual(glue, 0.46050199999999997, places=6,
                               msg="报价-工费率!AN2 胶水")
        groove = module.compute_line("v_groove", {
            "quote_quantity": 1000, "setup_minutes": 60, "capacity_per_hour": 3000,
            "equipment_rate": 195, "labor_rate": 111, "times": 2})["amount"]
        self.assertAlmostEqual(groove, 0.816, places=6, msg="报价-工费率!AK5 V槽 ×2")

    def test_b6_every_golden_row_subtotal_and_cost_replays(self):
        module = self.cost_mod()
        for row in GOLDEN_ROWS:
            amount = 0.0
            for name, value in row["categories"].items():
                line = module.compute_line(self.code_of(name), {}, amount=value)
                self.assertEqual(line["source"], "human",
                                 "显式给金额时必须标 source=human（Spec §4.5）")
                amount += line["amount"]
            self.assertAlmostEqual(amount, row["subtotal"], places=6,
                                   msg="第 %d 行 小计 AQ" % row["excel_row"])
            cost = module.apply_loss(amount, row["loss_rate"],
                                     category="labor" if row["excel_row"] == 15 else "material")
            self.assertAlmostEqual(cost, row["cost"], places=6,
                                   msg="第 %d 行 成本 AS = 小计 × (1+损耗)" % row["excel_row"])

    def test_b7_project_total_replays_to_the_workbook(self):
        summary = self.cost_mod().summarize(
            golden_project_lines(),
            packaging=GOLDEN_PACKAGING, freight=GOLDEN_FREIGHT,
            loss_base_scope="material_process_and_labor")
        self.assertAlmostEqual(summary["subtotal"], GOLDEN_SUBTOTAL, places=6,
                               msg="ΣAS(2:15) = 73.24578291607608")
        self.assertAlmostEqual(summary["packaging_total"], GOLDEN_PACKAGING, places=6)
        self.assertAlmostEqual(summary["freight_total"], GOLDEN_FREIGHT, places=6)
        self.assertAlmostEqual(summary["total_cost"], GOLDEN_TOTAL, places=6,
                               msg="报价-工费率!AV2 总成本 = 77.685201899648021")


# --------------------------------------------------------------------------- #
# C. 最低收费（② 报价-工费率 落地后主行无门限：只按表达式摊到单件）
# --------------------------------------------------------------------------- #
class CMinimumCharge(CostCase):
    """2026-09-21 口径裁决取 ②（`docs/specs/packaging-cost-minimum-charge-decision.md`）。

    本组 c1/c2/c4 的期望值按裁决更新：四个码的 `minimum_charge` 归零，`c3`（表达式更高）
    与 `c5`（本来就是 0）不变；第 1 批冻结值改由 `frozen_minimum_charge` 留证。
    """

    LAMINATION = {"machine_length": 889, "machine_width": 700, "imposition_count": 1,
                  "setup_minutes": 30, "capacity_per_hour": 5500, "equipment_rate": 197,
                  "labor_rate": 145, "film_price": 1.7, "film_thickness_um": 18,
                  "film_kg_price": 18.5, "tax_factor": 1.13}

    def test_c1_low_expression_does_not_hit_a_minimum_charge(self):
        module = self.cost_mod()
        variables = dict(self.LAMINATION, quote_quantity=1000,
                         machine_length=20, machine_width=20)
        line = module.compute_line("lamination", variables)
        self.assertFalse(line["min_charge_applied"],
                         "② 报价-工费率主行无门限，表达式再低也不许命中最低收费")
        self.assertAlmostEqual(line["amount"], 0.233916788093, places=6,
                               msg="必须复现 ② 口径的表达式值")

    def test_c2_low_expression_scales_by_quantity_without_a_floor(self):
        module = self.cost_mod()
        for quantity, expected in ((100, 1.772916788093), (1000, 0.233916788093),
                                   (10000, 0.080016788093)):
            line = module.compute_line("lamination", dict(
                self.LAMINATION, quote_quantity=quantity,
                machine_length=20, machine_width=20))
            self.assertFalse(line["min_charge_applied"], "q=%d 不许命中门限" % quantity)
            self.assertAlmostEqual(line["amount"], expected, places=6,
                                   msg="② 口径下只按表达式摊到单件：q=%d" % quantity)

    def test_c3_expression_wins_when_it_is_higher(self):
        module = self.cost_mod()
        line = module.compute_line("lamination", dict(self.LAMINATION, quote_quantity=1000))
        self.assertFalse(line["min_charge_applied"])
        self.assertAlmostEqual(line["amount"], 1.3766112580048271, places=6)

    def test_c4_die_cut_has_no_minimum_charge(self):
        module = self.cost_mod()
        base = {"imposition_count": 1, "setup_minutes": 120, "capacity_per_hour": 6500,
                "equipment_rate": 197.52, "labor_rate": 190.06}
        low = module.compute_line("die_cutting", dict(base, quote_quantity=100))
        self.assertFalse(low["min_charge_applied"], "啤/切主行不许再有 100 件门限")
        self.assertAlmostEqual(low["amount"], 7.811227692308, places=6)
        high = module.compute_line("die_cutting", dict(base, quote_quantity=1000))
        self.assertFalse(high["min_charge_applied"])
        self.assertAlmostEqual(high["amount"], 0.834787692308, places=6)

    def test_c5_zero_minimum_charge_never_applies(self):
        module = self.cost_mod()
        line = module.compute_line("material", {
            "cut_length": 1, "cut_width": 1, "gsm": 1, "ton_price": 1, "imposition_count": 1,
            "proof_base": 0, "quote_quantity": 1000, "tax_factor": 1.13})
        self.assertFalse(line["min_charge_applied"], "minimum_charge = 0 时不许命中")
        self.assertEqual(module.FORMULA_CATALOG["PKG-C-MATERIAL"]["minimum_charge"], 0)

    def test_c6_minimum_charge_amounts_shrink_as_quantity_grows(self):
        module = self.cost_mod()
        amounts = [module.compute_line("lamination", dict(
            self.LAMINATION, quote_quantity=quantity,
            machine_length=20, machine_width=20))["amount"]
            for quantity in (100, 500, 1000, 5000)]
        self.assertEqual(amounts, sorted(amounts, reverse=True),
                         "最低收费按数量摊，数量越大单件越低")


# --------------------------------------------------------------------------- #
# D. 损耗
# --------------------------------------------------------------------------- #
class DLoss(CostCase):
    def test_d1_default_scope_is_material_process_and_labor(self):
        module = self.cost_mod()
        self.assertEqual(module.DEFAULT_LOSS_BASE_SCOPE, "material_process_and_labor",
                         "默认必须忠实复现 0903：AQ 含人工，AS = AQ × (1+损耗)（Spec §2.8）")
        self.assertIn("material", module.loss_base_categories("material_process_and_labor"))
        self.assertIn("labor", module.loss_base_categories("material_process_and_labor"))
        self.assertIn("other", module.loss_base_categories("material_process_and_labor"))

    def test_d2_packaging_and_freight_never_take_loss(self):
        module = self.cost_mod()
        for scope in module.LOSS_BASE_SCOPES:
            for category in ("packaging", "freight"):
                self.assertNotIn(category, module.loss_base_categories(scope),
                                 "包材与运输不参与损耗（Spec §2.8）")
        summary = module.summarize([], packaging=GOLDEN_PACKAGING, freight=GOLDEN_FREIGHT)
        self.assertAlmostEqual(summary["total_cost"], GOLDEN_PACKAGING + GOLDEN_FREIGHT,
                               places=6, msg="没有部件行时总成本 = 包装 + 运输，未被损耗放大")

    def test_d3_material_only_scope_excludes_process_categories(self):
        module = self.cost_mod()
        categories = module.loss_base_categories("material_only")
        self.assertEqual(set(categories), {"material"})
        self.assertAlmostEqual(module.apply_loss(1.0, 0.23, category="material",
                                                 loss_base_scope="material_only"), 1.23, places=6)
        self.assertAlmostEqual(module.apply_loss(1.0, 0.23, category="die_cutting",
                                                 loss_base_scope="material_only"), 1.0, places=6)

    def test_d4_material_process_scope_excludes_labor_and_other(self):
        module = self.cost_mod()
        categories = set(module.loss_base_categories("material_process"))
        self.assertIn("die_cutting", categories)
        self.assertNotIn("labor", categories)
        self.assertNotIn("other", categories)

    def test_d5_loss_amount_is_tracked_per_row(self):
        module = self.cost_mod()
        summary = module.summarize([
            {"cost_category": "material", "amount": 10.0, "loss_rate": 0.23},
            {"cost_category": "labor", "amount": 5.0, "loss_rate": 0.23}])
        self.assertAlmostEqual(summary["subtotal"], 18.45, places=6)
        self.assertAlmostEqual(summary["loss_amount"], 3.45, places=6)

    def test_d6_loss_rate_default_comes_from_the_factor_table(self):
        module = self.cost_mod()
        rows = {row["factor_code"]: row for row in seed.COST_FACTORS}
        self.assertAlmostEqual(module.default_loss_rate("灰板 2.0mm", rows=rows.values()),
                               rows["F-PKG-LOSS-GREYBOARD"]["value"], places=6)
        self.assertAlmostEqual(module.default_loss_rate("特种纸 200g", rows=rows.values()),
                               rows["F-PKG-LOSS-PAPER"]["value"], places=6)

    def test_d7_missing_loss_rate_is_a_gap(self):
        module = self.cost_mod()
        self.snapshot(kb_cost_factor=[])
        gap = module.default_loss_rate("未知材料 X", rows=[])
        self.assertIsNone(gap, "取不到损耗率时必须返回 None，由调用方转缺口，不许默认 0")


# --------------------------------------------------------------------------- #
# E. 工装 / 刀模
# --------------------------------------------------------------------------- #
class ETooling(CostCase):
    def rule(self, **over):
        base = {"tooling_code": "T-DEMO", "name": "烫金版", "process_code": "烫金",
                "mode": "lifetime", "tooling_cost": 12000.0, "tooling_lifetime": 60000.0,
                "refund_threshold": None, "refundable": 0}
        base.update(over)
        return base

    def test_e1_lifetime_mode_amortises_by_tooling_life(self):
        line = self.cost_mod().compute_tooling(self.rule(), quote_quantity=1000)
        self.assertAlmostEqual(line["amount"], 0.2, places=6,
                               msg="按寿命摊销：12000 ÷ 60000 = 0.2，与订单量无关")
        self.assertEqual(line["quantity_basis"], "按寿命")
        self.assertEqual(line["tooling_code"], "T-DEMO")

    def test_e2_one_off_mode_amortises_by_quantity(self):
        line = self.cost_mod().compute_tooling(
            self.rule(mode="one_off"), quote_quantity=1000, amortized_quantity=3000)
        self.assertAlmostEqual(line["amount"], 4.0, places=6, msg="一次性收取：12000 ÷ 3000")

    def test_e3_committed_mode_amortises_by_committed_volume(self):
        line = self.cost_mod().compute_tooling(
            self.rule(mode="committed"), quote_quantity=1000, committed_volume=80000)
        self.assertAlmostEqual(line["amount"], 0.15, places=6, msg="按项目承诺量：12000 ÷ 80000")

    def test_e4_refund_mode_reports_status_not_a_discount(self):
        module = self.cost_mod()
        rule = self.rule(mode="refund", refundable=1, refund_threshold=50000.0,
                         tooling_cost=12000.0)
        below = module.compute_tooling(rule, quote_quantity=1000, amortized_quantity=1000,
                                       cumulative_quantity=20000)
        self.assertEqual(below["refund_status"], "chargeable")
        above = module.compute_tooling(rule, quote_quantity=1000, amortized_quantity=1000,
                                       cumulative_quantity=60000)
        self.assertEqual(above["refund_status"], "refundable",
                         "累计数量达到返还门槛时只改状态，金额仍照常收取，冲减由第 8 批处理")
        self.assertAlmostEqual(below["amount"], above["amount"], places=6)

    def test_e5_customer_supplied_is_zero_and_only_noted(self):
        line = self.cost_mod().compute_tooling(
            self.rule(mode="customer_supplied", tooling_cost=12000.0), quote_quantity=1000)
        self.assertEqual(line["amount"], 0.0, "客户自备模具不进成本（Spec §2.9）")
        self.assertEqual(line["quantity_basis"], "客户自备")
        self.assertTrue(line.get("note") or line.get("refund_status"))

    def test_e6_missing_basis_is_a_gap(self):
        module = self.cost_mod()
        gap = module.compute_tooling(self.rule(tooling_lifetime=None), quote_quantity=1000)
        self.assertIsNone(gap["amount"], "寿命缺失时不许按 0 或 1 顶替（Spec §2.9）")
        self.assertEqual(gap["gap"]["code"], "tooling_basis_missing:T-DEMO")

    def test_e7_tooling_lines_are_project_level(self):
        module = self.cost_mod()
        line = module.compute_tooling(self.rule(), quote_quantity=1000)
        self.assertIn("part_code", line)
        self.assertIsNone(line["part_code"], "工装行不摊进任何部件行，避免重复计费（Spec §2.9）")
        summary = module.summarize([], tooling=[line])
        self.assertAlmostEqual(summary["tooling_total"], 0.2, places=6)


# --------------------------------------------------------------------------- #
# F. 包材与运输
# --------------------------------------------------------------------------- #
class FPackagingAndFreight(CostCase):
    def test_f1_content_formulas_are_the_eleven_workbook_rows(self):
        module = self.cost_mod()
        found = {code for code, entry in module.FORMULA_CATALOG.items()
                 if entry["cost_category"] == "packaging"}
        self.assertEqual(found, set(CONTENT_FORMULAS),
                         "包材公式必须正好是 0903 包装运输 的 11 行（Spec §2.10）")
        for code in CONTENT_FORMULAS:
            self.assertIn("包装运输", module.FORMULA_CATALOG[code]["source_ref"])

    def test_f2_carton_matches_the_workbook(self):
        expected, variables = GOLDEN_CONTENTS["彩盒"]
        amount = self.cost_mod().compute_content("PKG-P-CARTON", dict(
            variables, tax_factor=1.13, loss_uplift=1.03, yield_divisor=0.9))["amount"]
        self.assertAlmostEqual(amount, expected, places=6, msg="包装运输!J2 彩盒")

    def test_f3_pad_matches_the_workbook(self):
        expected, variables = GOLDEN_CONTENTS["平卡"]
        amount = self.cost_mod().compute_content("PKG-P-PAD", dict(
            variables, tax_factor=1.13, loss_uplift=1.03, yield_divisor=0.9))["amount"]
        self.assertAlmostEqual(amount, expected, places=6, msg="包装运输!J3 平卡")

    def test_f4_strap_and_pallet_match_the_workbook(self):
        module = self.cost_mod()
        strap_expected, strap_vars = GOLDEN_CONTENTS["牛皮纸轧带"]
        self.assertAlmostEqual(
            module.compute_content("PKG-P-STRAP", dict(strap_vars, tax_factor=1.13))["amount"],
            strap_expected, places=6, msg="包装运输!J7 牛皮纸轧带")
        pallet_expected, pallet_vars = GOLDEN_CONTENTS["卡板"]
        self.assertAlmostEqual(
            module.compute_content("PKG-P-PALLET", dict(pallet_vars, tax_factor=1.13))["amount"],
            pallet_expected, places=6, msg="包装运输!J12 卡板")

    def test_f5_packaging_total_matches_the_workbook(self):
        module = self.cost_mod()
        rows = [{"content_code": "C-CARTON", "formula_code": "PKG-P-CARTON",
                 **GOLDEN_CONTENTS["彩盒"][1]},
                {"content_code": "C-PAD", "formula_code": "PKG-P-PAD",
                 **GOLDEN_CONTENTS["平卡"][1]},
                {"content_code": "C-STRAP", "formula_code": "PKG-P-STRAP",
                 **GOLDEN_CONTENTS["牛皮纸轧带"][1]},
                {"content_code": "C-PALLET", "formula_code": "PKG-P-PALLET",
                 **GOLDEN_CONTENTS["卡板"][1]}]
        total = module.compute_packaging(rows, tax_factor=1.13, loss_uplift=1.03,
                                         yield_divisor=0.9)
        self.assertAlmostEqual(total["amount"], GOLDEN_PACKAGING, places=6,
                               msg="包装运输!J2:J12 合计 = 3.0913797678856545")
        self.assertEqual(len(total["lines"]), 4)

    def test_f6_freight_takes_the_higher_of_the_two_branches(self):
        module = self.cost_mod()
        line = module.compute_freight(
            {"rule_code": "PKG-LG-PALLET-STD", "min_freight": 1150.0, "pallet_freight": 1650.0,
             "units_per_pallet": 120, "loading_rate": "≥85%"}, quote_quantity=1000)
        self.assertAlmostEqual(line["amount"], 1.3480392156862744, places=6,
                               msg="MAX(1150/1000, 1650/120/0.85)，即 0903 报价-工费率!AU2")
        self.assertEqual(line["quantity_basis"], "按件")

    def test_f7_loading_rate_text_is_parsed(self):
        module = self.cost_mod()
        self.assertAlmostEqual(module.parse_loading_rate("≥85%"), 0.85, places=6)
        self.assertAlmostEqual(module.parse_loading_rate("92%"), 0.92, places=6)
        self.assertIsNone(module.parse_loading_rate("不适用"),
                          "解析不了就返回 None，该分支无效，不许当 1")

    def test_f8_loading_rate_failure_falls_back_to_the_minimum_branch(self):
        module = self.cost_mod()
        line = module.compute_freight(
            {"rule_code": "R", "min_freight": 1150.0, "pallet_freight": 1650.0,
             "units_per_pallet": 120, "loading_rate": "不适用"}, quote_quantity=1000)
        self.assertAlmostEqual(line["amount"], 1.15, places=6,
                               msg="装载率不可用 → 只剩最低运费分支")
        self.assertTrue(line["assumptions"])

    def test_f9_zero_units_per_pack_is_a_gap(self):
        module = self.cost_mod()
        line = module.compute_content("PKG-P-PALLET",
                                      {"usage_qty": 1, "material_price": 70,
                                       "units_per_pack": 0, "tax_factor": 1.13})
        self.assertIsNone(line["amount"])
        self.assertEqual(line["gap"]["code"], "invalid_units_per_pack")


# --------------------------------------------------------------------------- #
# G. 三层汇总
# --------------------------------------------------------------------------- #
class GThreeLayers(CostCase):
    def summary(self):
        return self.cost_mod().summarize(
            golden_project_lines(), packaging=GOLDEN_PACKAGING, freight=GOLDEN_FREIGHT,
            loss_base_scope="material_process_and_labor")

    def test_g1_every_category_has_a_bucket(self):
        summary = self.summary()
        self.assertEqual(set(summary["categories"]), {code for code, _ in EXPECTED_CATEGORIES},
                         "类别层必须正好是 24 个成本类别（Spec §2.12）")

    def test_g2_every_report_group_is_present(self):
        summary = self.summary()
        self.assertEqual(set(summary["report_groups"]), set(EXPECTED_REPORT_GROUPS),
                         "报告分组必须正好是 Spec 修复第 1 批 §2.1 的 13 组")

    def test_g3_report_groups_add_up_to_the_total(self):
        summary = self.summary()
        self.assertAlmostEqual(sum(summary["report_groups"].values()), summary["total_cost"],
                               places=6)

    def test_g4_part_layer_adds_up_to_the_subtotal(self):
        summary = self.summary()
        by_part = {}
        for line in golden_project_lines():
            by_part.setdefault(line["part_code"], 0.0)
        for line in summary["lines"]:
            by_part[line["part_code"]] = by_part.get(line["part_code"], 0.0) + \
                line["amount_with_loss"]
        self.assertAlmostEqual(sum(by_part.values()), summary["subtotal"], places=6)
        self.assertEqual(len(by_part), len(GOLDEN_ROWS), "14 个部件层")

    def test_g5_category_sums_replay_the_workbook(self):
        got = self.summary()["categories"]
        for code, expected in GOLDEN_CATEGORY_SUMS.items():
            self.assertAlmostEqual(got[code], expected, places=6,
                                   msg="类别 %s 的 Σ(金额×(1+损耗))" % code)
        for code, value in got.items():
            if code not in GOLDEN_CATEGORY_SUMS:
                self.assertAlmostEqual(value, 0.0, places=6,
                                       msg="%s 在黄金样例里没有金额" % code)

    def test_g6_report_groups_replay_the_workbook(self):
        got = self.summary()["report_groups"]
        for name, expected in GOLDEN_REPORT_GROUPS.items():
            self.assertAlmostEqual(got[name], expected, places=6,
                                   msg="报告分组 %s（对齐 成本细分 Sheet）" % name)
        self.assertAlmostEqual(sum(got.values()), GOLDEN_TOTAL, places=6)

    def test_g7_total_is_the_sum_of_its_layers(self):
        summary = self.summary()
        self.assertAlmostEqual(summary["total_cost"],
                               summary["subtotal"] + summary["tooling_total"]
                               + summary["packaging_total"] + summary["freight_total"],
                               places=6, msg="总成本 = 部件含损耗 + 工装 + 包装 + 运输")


# --------------------------------------------------------------------------- #
# H. 缺口（不许编数字）
# --------------------------------------------------------------------------- #
class HGaps(CostCase):
    def test_h1_non_packaging_industry_is_refused(self):
        module = self.cost_mod()
        self.save_requirement(industry="battery")
        self.confirm_box()
        with self.assertRaises(module.CostError) as ctx:
            module.build_cost(PID, REQ_NO)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "not_packaging")

    def test_h2_without_a_confirmed_box_it_is_a_gap(self):
        module = self.cost_mod()
        self.save_requirement()
        with self.assertRaises(module.CostError) as ctx:
            module.build_cost(PID, REQ_NO)
        self.assertEqual(ctx.exception.code, "box_type_not_confirmed")

    def test_h3_without_a_bom_it_is_a_gap(self):
        module = self.cost_mod()
        self.prepare(with_bom=False, with_route=False)
        with self.assertRaises(module.CostError) as ctx:
            module.build_cost(PID, REQ_NO)
        self.assertEqual(ctx.exception.code, "bom_not_built")

    def test_h4_without_a_confirmed_route_it_is_a_gap(self):
        module = self.cost_mod()
        self.save_requirement()
        self.confirm_box()
        self.build_bom()
        with self.assertRaises(module.CostError) as ctx:
            module.build_cost(PID, REQ_NO)
        self.assertEqual(ctx.exception.code, "route_not_confirmed",
                         "人工费来自第 6 批已确认路线的工时，没有路线不能算（Spec §2.13）")

    def test_h5_without_a_quantity_it_is_a_gap(self):
        module = self.cost_mod()
        self.prepare(quote_quantity="")
        with self.assertRaises(module.CostError) as ctx:
            module.build_cost(PID, REQ_NO)
        self.assertEqual(ctx.exception.code, "quantity_missing")

    def test_h6_category_without_a_formula_becomes_a_gap_not_a_zero(self):
        module = self.cost_mod()
        self.prepare()
        cost = module.compute_project(PID, REQ_NO)
        codes = [item.get("code") for item in cost["gaps"]]
        self.assertIn("no_formula:print", codes,
                      "面纸部件要印但 print 在 0903 里就是手填列（Spec §2.13）")
        self.assertTrue(cost["has_gaps"])
        self.assertAlmostEqual(cost["categories"]["print"], 0.0, places=6,
                               msg="没有公式的类别不许凭空给金额")

    def test_h7_below_moq_still_costs_but_flags_the_gap(self):
        module = self.cost_mod()
        self.prepare(quote_quantity=100)
        cost = module.compute_project(PID, REQ_NO)
        codes = [item.get("code") for item in cost["gaps"]]
        self.assertIn("below_moq", codes)
        self.assertTrue(cost["has_gaps"])
        self.assertGreater(cost["total_cost"], 0.0, "低于 MOQ 仍然出成本，只是标记缺口")

    def test_h8_unbound_variable_is_a_gap_not_a_zero(self):
        module = self.cost_mod()
        line = module.compute_line("material", {"cut_length": 889})
        self.assertIsNone(line["amount"], "变量没绑全时不许当 0 算")
        self.assertTrue(str(line["gap"]["code"]).startswith("missing_variable:"),
                        line["gap"])

    def test_h9_broken_reviewed_formula_fails_closed(self):
        module = self.cost_mod()
        self.snapshot(kb_packaging_cost_formula=[{
            "formula_code": "PKG-C-MATERIAL", "cost_category": "material",
            "expression": "import os", "minimum_charge": 0, "rounding": 4,
            "rate_code": "", "review_status": "reviewed", "loss_scope": "灰板开料",
            "industry": "packaging", "status": "active"}])
        with self.assertRaises(module.CostError) as ctx:
            module.resolve_formula("PKG-C-MATERIAL")
        self.assertEqual(ctx.exception.code, "invalid_formula:PKG-C-MATERIAL",
                         "reviewed 公式解析失败必须报错，不许静默回退到内置目录（Spec §4.6）")

    def test_h10_draft_formulas_from_batch3_are_never_executed(self):
        module = self.cost_mod()
        rows = [row for row in seed.COST_FORMULAS
                if row["formula_code"] == "PKG-F-GREYBOARD"]
        self.assertTrue(rows)
        self.assertEqual(rows[0]["review_status"], "draft")
        resolved = module.resolve_formula("PKG-C-MATERIAL", rows=seed.COST_FORMULAS)
        self.assertNotIn("Σ", resolved["expression"],
                         "第 3 批的中文散文 draft 公式绝不能被执行（Spec §4.6）")


# --------------------------------------------------------------------------- #
# I. 场景与数量阶梯
# --------------------------------------------------------------------------- #
class IScenarios(CostCase):
    def test_i1_default_scenario_is_used_when_none_is_given(self):
        module = self.cost_mod()
        self.prepare()
        cost = module.build_cost(PID, REQ_NO)
        self.assertEqual(cost["scenario_code"], "default")
        self.assertEqual(cost["quote_quantity"], 1000.0)

    def test_i2_trial_and_mass_are_distinguished(self):
        module = self.cost_mod()
        self.prepare(是否首批试产="是")
        trial = module.compute_project(PID, REQ_NO)
        self.assertEqual(trial["trial_or_mass_production"], "trial")
        self.prepare(是否首批试产="否")
        mass = module.compute_project(PID, REQ_NO)
        self.assertEqual(mass["trial_or_mass_production"], "mass")

    def test_i3_scenarios_coexist_and_are_listed_newest_first(self):
        module = self.cost_mod()
        self.prepare()
        module.build_cost(PID, REQ_NO, scenario={"scenario_code": "tier-1k"})
        module.build_cost(PID, REQ_NO, scenario={"scenario_code": "tier-10k",
                                                 "quote_quantity": 10000})
        curve = module.cost_curve(PID, REQ_NO)
        self.assertGreaterEqual(len(curve), 2)
        quantities = [row["quote_quantity"] for row in curve]
        self.assertEqual(quantities, sorted(quantities, reverse=True),
                         "成本曲线按数量降序（Spec §2.11）")
        for row in curve:
            self.assertGreater(row["total_cost"], 0.0)

    def test_i4_output_has_no_price_or_margin_yet(self):
        module = self.cost_mod()
        self.prepare()
        cost = module.compute_project(PID, REQ_NO)
        for forbidden in ("unit_price", "untaxed_price", "margin_rate", "total_price",
                          "quote_amount"):
            self.assertNotIn(forbidden, cost,
                             "利润/售价是第 8 批的事，第 7 批不许出现 %s（Spec §5）" % forbidden)
        self.assertIn("total_cost", cost)

    def test_i5_scenario_carries_included_components_and_quantity_basis(self):
        module = self.cost_mod()
        self.prepare()
        cost = module.compute_project(PID, REQ_NO, scenario={"included_components": "without-manual"})
        self.assertEqual(cost["included_components"], "without-manual")
        self.assertIn(cost["quantity_tier"], ("1000", 1000, None))


# --------------------------------------------------------------------------- #
# J. 落库与接口
# --------------------------------------------------------------------------- #
class JPersistAndApi(CostCase):
    def test_j1_schema_has_the_five_new_tables(self):
        self.assertTrue("wip_packaging_cost_estimate" in SCHEMA_SQL.read_text(encoding="utf-8"),
                        "da_schema.sql 缺包装成本三张表")
        with sqlite3.connect(str(self.db_file)) as conn:
            names = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("wip_packaging_cost_estimate", "wip_packaging_cost_item",
                          "kb_packaging_cost_content", "kb_packaging_tooling_rule"):
                self.assertIn(table, names, "缺表 %s（Spec §3）" % table)
            columns = {row[1] for row in conn.execute(
                "PRAGMA table_info(kb_packaging_logistics_rule)")}
            self.assertIn("pallet_freight", columns, "物流规则要加 pallet_freight（Spec §3.1）")

    def test_j2_build_then_load_round_trips(self):
        module = self.cost_mod()
        self.prepare()
        built = module.build_cost(PID, REQ_NO)
        loaded = module.load_cost(PID, REQ_NO)
        self.assertTrue(loaded["built"])
        self.assertAlmostEqual(loaded["total_cost"], built["total_cost"], places=6)
        self.assertEqual(loaded["engine_version"], "packaging_cost_v1")
        self.assertEqual(loaded["cost_profile"], "packaging_v1")

    def test_j3_rebuild_replaces_items_without_duplicating(self):
        module = self.cost_mod()
        self.prepare()
        first = module.build_cost(PID, REQ_NO)
        count_first = len(first["items"])
        second = module.build_cost(PID, REQ_NO)
        self.assertEqual(len(second["items"]), count_first, "重算必须整体替换，不许翻倍")
        rows = self.db_rows(
            "SELECT COUNT(*) AS n FROM wip_packaging_cost_item WHERE estimate_id = ?",
            (second["estimate_id"],))
        self.assertEqual(int(rows[0]["n"]), count_first)

    def test_j4_items_are_explainable(self):
        module = self.cost_mod()
        self.prepare()
        cost = module.build_cost(PID, REQ_NO)
        self.assertTrue(cost["items"])
        for item in cost["items"]:
            self.assertIn("cost_category", item)
            self.assertIsNotNone(item.get("source_ref"),
                                 "每行都要能追溯到 0903 单元格 / 工序 / 材料码（Spec §3.2）")
        material = [item for item in cost["items"] if item["cost_category"] == "material"]
        self.assertTrue(material)
        self.assertTrue(any(str(item.get("expression") or "").strip() for item in material),
                        "公式行必须留表达式快照")
        self.assertTrue(any(item.get("inputs_json") for item in material),
                        "公式行必须留输入变量快照")

    def test_j5_routes_are_registered(self):
        source = MAIN_PY.read_text(encoding="utf-8", errors="replace")
        for suffix in ("/requirement/packaging-cost", "/requirement/packaging-cost/items",
                       "/requirement/packaging-cost/curve"):
            self.assertTrue(suffix in source, "main.py 缺路由 %s" % suffix)

    def test_j6_write_roles_reuse_batch4(self):
        module = self.cost_mod()
        self.assertIs(module.COST_WRITE_ROLES, packaging_match.BOX_MATCH_DECIDE_ROLES,
                      "写权限常量必须直接引用第 4 批那一份（Spec §4）")

    def test_j7_rebuild_writes_the_project_audit(self):
        module = self.cost_mod()
        self.prepare()
        with mock.patch.object(store, "audit") as spy:
            module.build_cost(PID, REQ_NO)
        actions = [str(call.args[1]) for call in spy.call_args_list]
        self.assertTrue(any("packaging_cost" in action for action in actions), actions)

    def test_j8_seed_adds_cost_contents_and_tooling_rules(self):
        contents = getattr(seed, "COST_CONTENTS", None)
        self.assertIsNotNone(contents, "da_seed_packaging 要新增 COST_CONTENTS（Spec §4.5）")
        self.assertEqual(len(contents), 11, "包材明细对齐 0903 包装运输 的 11 行")
        tooling = getattr(seed, "TOOLING_RULES", None)
        self.assertIsNotNone(tooling, "da_seed_packaging 要新增 TOOLING_RULES")
        self.assertEqual({row["mode"] for row in tooling}, set(self.cost_mod().TOOLING_MODES),
                         "五种工装模式各给一条演示数据")


# --------------------------------------------------------------------------- #
# K. 非回归护栏
# --------------------------------------------------------------------------- #
class KNonRegression(CostCase):
    def test_k1_generic_cost_model_constants_are_untouched(self):
        self.assertEqual(cost_model.TAX_DIVISOR, 1.13)
        self.assertEqual(cost_model.MATERIAL_SHARE, 0.791)
        self.assertEqual(cost_model.LABOR_RATIO, 0.3556)
        self.assertEqual(cost_model.OVERHEAD_RATIO, 0.1778)
        self.assertEqual(cost_model.PROCESSING_RATIO, 0.0944)

    def test_k2_generic_cost_model_results_are_untouched(self):
        self.assertEqual(cost_model.derive(1000.0)["total"], 1146.79)
        module = self.cost_mod()
        for industry in ("semiconductor", "battery", "appliance"):
            self.assertEqual(module.profile_for(industry), module.GENERIC_PROFILE)

    def test_k3_batch6_route_contract_still_there(self):
        self.assertEqual(packaging_route.ENGINE_VERSION, "packaging_route_v1")
        self.assertEqual(len(packaging_route.PROCESS_CATALOG), 19)
        self.assertIs(packaging_route.ROUTE_WRITE_ROLES,
                      packaging_match.BOX_MATCH_DECIDE_ROLES)

    def test_k4_batch5_bom_contract_still_there(self):
        self.assertEqual(packaging_bom.ENGINE_VERSION, "packaging_bom_v1")
        self.assertEqual(set(packaging_bom.BOM_CATEGORIES),
                         {"finished", "box_part", "material", "process", "packaging",
                          "tooling", "optional_part"})
        self.assertIs(packaging_bom.BOM_WRITE_ROLES,
                      packaging_match.BOX_MATCH_DECIDE_ROLES)

    def test_k5_batch3_seed_data_is_intact(self):
        self.assertEqual(len(seed.BOX_TYPES), 12)
        self.assertEqual(len(seed.PART_TEMPLATES), 31)
        self.assertEqual(len(seed.PROCESS_TEMPLATES), 23)
        self.assertEqual(len(seed.ACCESSORIES), 12)
        self.assertEqual(len(seed.LOGISTICS_RULES), 3)
        self.assertEqual(len(seed.MATERIALS), 5)
        self.assertEqual(len(seed.COST_RATES), 8)
        self.assertEqual(len(seed.COST_FACTORS), 5)
        self.assertEqual(len(seed.COST_FORMULAS), 7)
        self.assertEqual(len(seed.MATCH_WEIGHTS), 5)
        materials = {row["material"]["material_code"]: row for row in seed.MATERIALS}
        self.assertEqual(materials["MAT-PKG-GREYBOARD"]["price"]["price"], 6.5)
        factors = {row["factor_code"]: row for row in seed.COST_FACTORS}
        self.assertEqual(factors["F-PKG-TAX-VAT"]["value"], 0.13)
        rules = {row["rule_code"]: row for row in seed.LOGISTICS_RULES}
        self.assertEqual(rules["PKG-LG-PALLET-STD"]["min_freight"], 800.0)
        self.assertEqual(rules["PKG-LG-PALLET-STD"]["units_per_pallet"], 480)

    def test_k6_legacy_cost_tables_are_not_written(self):
        module = self.cost_mod()
        self.prepare()
        module.build_cost(PID, REQ_NO)
        for table in ("wip_cost_estimate", "wip_cost_item", "out_cost_result"):
            rows = self.db_rows("SELECT COUNT(*) AS n FROM %s" % table)
            self.assertEqual(int(rows[0]["n"]), 0,
                             "包装成本不许写设计 IR 口径的 %s（Spec §3.2）" % table)

    def test_k7_engine_module_is_offline(self):
        source = inspect.getsource(self.cost_mod())
        for forbidden in ("openai", "requests", "httpx", "socket", "anthropic"):
            self.assertFalse(forbidden in source,
                             "成本引擎不得出现 %s：不联网、不调模型（Spec §5）" % forbidden)


if __name__ == "__main__":
    unittest.main()

"""红测：包装报价闭环（回传 / 定价 / 报价单 / 版本）—— 包装第 8 批（最后一批）。

Spec：docs/specs/packaging-quote-close-loop.md
依赖：第 1–7 批（四行业注册表 / 包装需求模板 / 包装知识库 / 盒型匹配 / 参数化 BOM /
工艺路线 / 包装专用成本引擎）。

现状缺口（实测，不是推断）：
  · `tech_app/backend/services/packaging_handoff.py` 不存在 —— 全仓没有任何「包装 → 报价」
    交接实现（`cost_flow.integration_quote_result()` 是设计 IR 口径，包装没有成品编码）；
  · `cpq_packaging_quote.py` 不存在 —— 报价侧没有包装定价 / 报价单 / 报价版本；
  · 报价没有版本表：`cpq_wf_card_step.data_snapshot` 是**同名覆盖**的合并快照，
    重算会盖掉上一版；
  · `cpq_tech_bridge.HANDOFF_KINDS` 只有 cost_to_quote / cost_to_process /
    process_to_quote / report_to_quote 四种，没有包装口径；`_step2_snapshot()` 只认
    `material`/`params`/`cost.total` 与固定模板列，包装的盒型/参数/BOM/路线/缺口/公式依据
    装不进快照就整段丢；
  · `cpq_bridge.send_to_quote()` 把 `handoff_kind` 写死成 "cost_to_quote"。

黄金数据：`报价逻辑-0903.xlsx`
  · `报价-工费率!AV2=77.68520189964802`、`AW2=0.25`、`AX2=AV2/(1-AW2)=103.58026919953069`；
  · `报价表!G2=60.04940812974125`、`H2=0.25`、`I2=80.06587750632167`；
  · `成本细分!P2=60.04940812974125`、`Q2=0.25`、`R2=P2/(1-Q2)=80.06587750632167`；
  · `问题点!A30` 的文字口径是「总成本 + 利润率」→ `60.04940812974125×1.25=75.06176016217657`，
    与公式口径差 5.004117344145101（Spec §1.2：两种模式都要能算，默认走公式口径）。
本文件把已核对的数值内联为常量，**不读工作簿**（客户样例不入库）。

禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import copy
import importlib
import inspect
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests" / "fixtures") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

HANDOFF_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_handoff.py"
QUOTE_PY = ROOT / "cpq_packaging_quote.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"
SCHEMA_SQL = ROOT / "tech_app" / "backend" / "storage" / "da_schema.sql"

from tech_app.backend import config  # noqa: E402
from tech_app.backend.storage import (da_db, da_repo, da_seed_packaging as seed,  # noqa: E402
                                      kb_repo, meta_backend, store)
from tech_app.backend.services import (packaging_bom, packaging_match,  # noqa: E402
                                       packaging_route)

PID = "pkgquote00001"
REQ_NO = "REQ-PKG-Q0001"
BOX_MAIN = "YT-RB-01001-A"

REQ = {"industry": "packaging", "packaging_product_name": "礼盒", "packaging_category": "礼盒",
       "quote_quantity": 1000, "inner_length": 200, "inner_width": 150, "inner_height": 80,
       "fit_clearance": 1.8, "box_type": BOX_MAIN, "closure_type": "天地盖", "v_groove": "是",
       "face_paper_gsm": 200, "lamination": "是", "hot_stamping": "是", "print_colors": "CMYK",
       "customer_name": "客户A", "target_gross_margin_rate": 0.25}

# --------------------------------------------------------------------------- #
# 0903 黄金数据
# --------------------------------------------------------------------------- #
COST_WORKRATE = 77.685201899648021        # 报价-工费率!AV2（第 7 批采用口径）
COST_STANDARD = 60.049408129741252        # 报价-行业标准!AV2 / 报价表!G2
MARGIN = 0.25                             # 报价-工费率!AW2
PRICE_WORKRATE = 103.58026919953069       # 报价-工费率!AX2 = AV2/(1-AW2)
PRICE_STANDARD = 80.06587750632167        # 报价表!I2 = G2/(1-H2)
PRICE_MARKUP_STANDARD = 75.06176016217657  # 问题点!A30 的文字口径 = G2×(1+H2)
MARKUP_GAP = 5.004117344145101            # 两种口径的差
TAX_RATE = 0.13
GOLDEN_TAX = 10.408564075821818           # PRICE_STANDARD × 0.13
GOLDEN_TAXED = 90.47444158214348          # PRICE_STANDARD × 1.13
GOLDEN_CHAIN = 88.0977195030363           # ((PRICE_STANDARD+2)×(1-0.05))×1.13

SECTION_KEYS = ("industry", "requirement", "box_type", "params", "bom", "route",
                "cost", "gaps", "formulas", "source")
FORBIDDEN_IN_PACKAGE_COST = ("unit_price", "untaxed_price", "total_price", "quote_amount",
                            "margin_rate", "gross_margin_rate", "markup_rate")

SALES = {"user_id": "100", "username": "sales1", "display_name": "SM1",
         "role_code": "sales_mgr", "role_name": "销售经理", "status": "active"}
VIEWER = {"user_id": "300", "username": "viewer1", "display_name": "V1",
          "role_code": "viewer", "role_name": "只读用户", "status": "active"}
FINANCE = {"user_id": "400", "username": "fin1", "display_name": "FI1",
           "role_code": "finance_manager", "role_name": "财务经理", "status": "active"}


def load_handoff_module():
    """交接模块；不存在时返回 None（用例给明确断言，不抛 ImportError）。"""
    try:
        return importlib.import_module("tech_app.backend.services.packaging_handoff")
    except Exception:
        return None


def load_quote_module():
    """报价定价模块；不存在时返回 None。"""
    try:
        return importlib.import_module("cpq_packaging_quote")
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
    extra = getattr(seed, "COST_CONTENTS", None)
    if extra:
        tables["kb_packaging_cost_content"] = [dict(r) for r in extra]
    tooling = getattr(seed, "TOOLING_RULES", None)
    if tooling:
        tables["kb_packaging_tooling_rule"] = [dict(r) for r in tooling]
    return tables


def subset(actual, expected, where=""):
    """expected 的每个键都必须在 actual 里同值出现；返回不一致清单（空 = 通过）。"""
    bad = []
    if not isinstance(actual, dict):
        return [f"{where}: 不是 dict，而是 {type(actual).__name__}"]
    for key, value in expected.items():
        if key not in actual:
            bad.append(f"{where}.{key} 缺失")
        elif actual[key] != value:
            bad.append(f"{where}.{key} = {actual[key]!r}，期望 {value!r}")
    return bad


def walk_keys(node, path="$"):
    """递归列出所有 dict/嵌套里的键与路径，用于「不许出现售价字段」的扫描。"""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.append((f"{path}.{key}", key))
            found.extend(walk_keys(value, f"{path}.{key}"))
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            found.extend(walk_keys(value, f"{path}[{index}]"))
    return found


def cost_fixture(**over):
    """一份**无缺口**的包装成本读回结果（形状与第 7 批 load_cost 一致）。"""
    value = {
        "built": True, "project_id": PID, "requirement_no": REQ_NO, "scenario_code": "default",
        "estimate_id": f"pkgcost:{PID}:{REQ_NO}:default",
        "industry": "packaging", "engine_version": "packaging_cost_v1",
        "cost_profile": "packaging_v1", "currency": "CNY",
        "quote_quantity": 1000, "tax_rate": TAX_RATE,
        "loss_base_scope": "material_process_and_labor",
        "quantity_tier": "1000", "trial_or_mass_production": "mass",
        "included_components": "all",
        "material_total": 60.0, "process_total": 7.0, "labor_total": 4.0,
        "tooling_total": 2.0, "packaging_total": 3.0913797678856545,
        "freight_total": 1.3480392156862744, "other_total": 0.0,
        "subtotal": 73.24578291607608, "loss_amount": 0.0,
        "total_cost": COST_WORKRATE, "has_gaps": False, "gaps": [], "assumptions": [],
        "categories": {"material": 60.0, "print": 0.0},
        "report_groups": {"材料": 60.0},
        "items": [], "computed_at": "2026-09-20T10:00:00",
    }
    value.update(over)
    return value


def package_fixture(**over):
    """一份典型的包装交接包（形状见 Spec §2.2）。"""
    package = {
        "engine_version": "packaging_handoff_v1",
        "handoff_version": "pkg-quote-handoff-v1",
        "handoff_kind": "packaging_cost_to_quote",
        "industry": "packaging",
        "industry_label": "包装",
        "cost_profile": "packaging_v1",
        "pricing_profile": "packaging_margin_v1",
        "result_version": "pkgcost-v1:1000:77.685201899648",
        "requirement": {"box_type": BOX_MAIN, "closure_type": "天地盖",
                        "quote_quantity": 1000, "inner_length": 200, "inner_width": 150,
                        "inner_height": 80, "face_paper_gsm": 200, "v_groove": "是"},
        "box_type": {"confirmed_box_type": BOX_MAIN, "decision": "confirmed"},
        "params": {"inner_length": 200, "inner_width": 150, "inner_height": 80,
                   "board_thickness": 1.5, "fit_clearance": 1.8},
        "bom": {"built": True, "items": []},
        "route": {"built": True, "status": "confirmed", "steps": []},
        "cost": cost_fixture(),
        "gaps": [],
        "formulas": [{"formula_code": "PKG-MATERIAL-001", "formula_version": "1.0",
                      "expression": "area_m2 * gsm * ton_price", "inputs": {"gsm": 200},
                      "result": 0.7954641993584073, "source_ref": "报价-工费率/R2",
                      "source": "formula"}],
        "source": {"project_id": PID, "requirement_no": REQ_NO, "scenario_code": "default",
                   "result_version": "pkgcost-v1:1000:77.685201899648",
                   "source_task_id": "", "source_session_id": "qsess-pkg-1",
                   "business_case_id": "bc_pkg0001", "handoff_id": ""},
    }
    package.update(over)
    return package


class HandoffCase(unittest.TestCase):
    """技术侧夹具：可替换知识库快照 + 独立临时 SQLite + 独立 meta 目录。"""

    def setUp(self):
        self._cache = dict(kb_repo._CACHE)
        self._da_path = config.DA_DB_PATH
        self.db_file = pathlib.Path(tempfile.mkdtemp()) / "quote.sqlite3"
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
    def handoff_mod(self):
        module = load_handoff_module()
        self.assertIsNotNone(
            module, "缺少 tech_app/backend/services/packaging_handoff.py（Spec §4.1）")
        return module

    def quote_mod(self):
        module = load_quote_module()
        self.assertIsNotNone(module, "缺少 cpq_packaging_quote.py（Spec §4.2）")
        return module

    # ---- 需求单 / 盒型 / BOM / 路线 / 成本 --------------------------------- #
    def save_requirement(self, project_id=PID, requirement_no=REQ_NO, **over):
        data = dict(REQ)
        data.update(over)
        store.save_requirement(project_id, {
            "project_id": project_id, "requirement_no": requirement_no,
            "title": "包装报价用例", "status": "pending_confirmation", "data": data})

    def confirm_box(self, code=BOX_MAIN, project_id=PID, requirement_no=REQ_NO):
        da_repo.save_box_match({
            "project_id": project_id, "requirement_no": requirement_no,
            "industry": "packaging", "engine_version": "packaging_match_v1",
            "inputs": {}, "candidates": [], "missing_inputs": [],
            "suggested_box_type": code})
        da_repo.update_box_match_decision(
            project_id, requirement_no, decision="confirmed", confirmed_box_type=code,
            confirmed_by="wangjingli", confirmed_at=da_db.now())

    def prepare(self, with_cost=True, cost_total=COST_WORKRATE, has_gaps=False,
                project_id=PID, requirement_no=REQ_NO, **req_over):
        """需求 + 确认盒型 + BOM + 已确认路线（+ 一份无缺口成本）。"""
        self.save_requirement(project_id=project_id, requirement_no=requirement_no, **req_over)
        self.confirm_box(project_id=project_id, requirement_no=requirement_no)
        packaging_bom.build_bom(project_id, requirement_no)
        packaging_route.build_route(project_id, requirement_no)
        packaging_route.confirm_route(project_id, requirement_no, actor={"username": "wangjingli"})
        if with_cost:
            self.save_cost(cost_total=cost_total, has_gaps=has_gaps,
                           project_id=project_id, requirement_no=requirement_no)

    def save_cost(self, cost_total=COST_WORKRATE, has_gaps=False, scenario="default",
                  project_id=PID, requirement_no=REQ_NO, gaps=None):
        """直接落一条成本（不走引擎）：本批要验的是交接与定价，不是第 7 批算法。"""
        estimate = {
            "estimate_id": f"pkgcost:{project_id}:{requirement_no}:{scenario}",
            "project_id": project_id, "requirement_no": requirement_no,
            "scenario_code": scenario, "industry": "packaging",
            "engine_version": "packaging_cost_v1", "cost_profile": "packaging_v1",
            "quote_quantity": 1000, "tax_rate": TAX_RATE,
            "material_total": round(cost_total - 17.685201899648021, 6), "process_total": 7.0,
            "labor_total": 4.0, "tooling_total": 2.0,
            "packaging_total": 3.0913797678856545, "freight_total": 1.3480392156862744,
            "other_total": 0.0, "subtotal": round(cost_total - 4.439418983595964, 6),
            "loss_amount": 0.0, "total_cost": cost_total, "has_gaps": bool(has_gaps),
            "gaps": list(gaps or []), "assumptions": [],
        }
        items = [{
            "seq": 1, "part_code": "BOX-MAIN", "part_name": "左右盖面纸",
            "cost_category": "material", "amount": 0.7954641993584073,
            "amount_with_loss": 0.978420965210841, "loss_rate": 0.23,
            "formula_code": "PKG-MATERIAL-001", "expression": "area_m2 * gsm * ton_price",
            "inputs_json": json.dumps({"gsm": 200, "ton_price": 12050}, ensure_ascii=False),
            "source_ref": "报价-工费率/R2", "source": "formula",
        }]
        da_repo.save_packaging_cost(project_id, requirement_no, scenario, estimate, items)

    # ---- 便捷调用 ---------------------------------------------------------- #
    def package(self, **kwargs):
        module = self.handoff_mod()
        return module.handoff_package(PID, REQ_NO, **kwargs)

    def handoff_rows(self):
        accessor = getattr(da_repo, "packaging_handoffs", None)
        if not callable(accessor):
            self.fail("da_repo 缺少 packaging_handoffs(project_id, requirement_no)"
                      "（Spec §3.1）")
        return accessor(PID, REQ_NO)


# --------------------------------------------------------------------------- #
# A. 契约与命名
# --------------------------------------------------------------------------- #
class AContract(unittest.TestCase):
    def test_a1_handoff_module_exists(self):
        self.assertTrue(HANDOFF_PY.exists(),
                        "缺少 tech_app/backend/services/packaging_handoff.py（Spec §4.1）")
        module = load_handoff_module()
        self.assertIsNotNone(module, "packaging_handoff 无法导入（Spec §4.1）")

    def test_a2_quote_module_exists(self):
        self.assertTrue(QUOTE_PY.exists(), "缺少 cpq_packaging_quote.py（Spec §4.2）")
        module = load_quote_module()
        self.assertIsNotNone(module, "cpq_packaging_quote 无法导入（Spec §4.2）")

    def test_a3_handoff_constants(self):
        module = load_handoff_module()
        self.assertIsNotNone(module, "缺少 packaging_handoff.py（Spec §4.1）")
        self.assertEqual(module.ENGINE_VERSION, "packaging_handoff_v1")
        self.assertEqual(module.HANDOFF_VERSION, "pkg-quote-handoff-v1")
        self.assertEqual(module.HANDOFF_KIND, "packaging_cost_to_quote")
        self.assertEqual(module.INDUSTRY, "packaging")
        self.assertEqual(module.COST_PROFILE, "packaging_v1")
        self.assertEqual(module.PRICING_PROFILE, "packaging_margin_v1")

    def test_a4_package_sections(self):
        module = load_handoff_module()
        self.assertIsNotNone(module, "缺少 packaging_handoff.py（Spec §4.1）")
        self.assertEqual(tuple(module.PACKAGE_SECTIONS), SECTION_KEYS,
                         "PACKAGE_SECTIONS 必须是 Spec §2.2 的 10 组、按表序")

    def test_a5_role_sets_are_closed_and_distinct(self):
        handoff = load_handoff_module()
        quote = load_quote_module()
        self.assertIsNotNone(handoff, "缺少 packaging_handoff.py（Spec §4.1）")
        self.assertIsNotNone(quote, "缺少 cpq_packaging_quote.py（Spec §4.2）")
        self.assertEqual(set(handoff.HANDOFF_WRITE_ROLES),
                         {"finance_manager", "process_manager", "process_director", "admin"},
                         "技术侧回传角色集必须是 Spec §2.7 的闭集")
        self.assertEqual(set(quote.WRITE_ROLES), {"sales_mgr", "admin"},
                         "报价侧定价角色集必须是 Spec §2.7 的闭集")
        self.assertEqual(set(handoff.HANDOFF_WRITE_ROLES) & set(quote.WRITE_ROLES), {"admin"},
                         "两边只有 admin 交集：定价是销售的动作、回传是财务/工艺的动作")

    def test_a6_quote_constants(self):
        module = load_quote_module()
        self.assertIsNotNone(module, "缺少 cpq_packaging_quote.py（Spec §4.2）")
        self.assertEqual(module.ENGINE_VERSION, "packaging_quote_v1")
        self.assertEqual(module.PRICING_PROFILE, "packaging_margin_v1")
        self.assertEqual(module.COST_PROFILE, "packaging_v1")
        self.assertEqual(module.INDUSTRY, "packaging")
        self.assertEqual(tuple(module.PRICING_MODES), ("gross_margin", "markup"))
        self.assertEqual(module.DEFAULT_PRICING_MODE, "gross_margin")
        self.assertEqual(module.RATE_FIELDS, {"gross_margin": "gross_margin_rate",
                                              "markup": "markup_rate"})
        self.assertEqual([code for code, _ in module.ADDON_CATEGORIES],
                         ["tech_premium", "market_adjustment", "other_addon"])
        self.assertEqual([code for code, _ in module.DEDUCTION_CATEGORIES], ["discount"])
        self.assertEqual(module.DEFAULT_TAX_RATE, 0.13)
        self.assertEqual(module.PRICE_PATH, "/api/packaging-quote/price")

    def test_a7_profiles_match_global_registry(self):
        import cpq_industries
        handoff = load_handoff_module()
        quote = load_quote_module()
        self.assertIsNotNone(handoff, "缺少 packaging_handoff.py（Spec §4.1）")
        self.assertIsNotNone(quote, "缺少 cpq_packaging_quote.py（Spec §4.2）")
        profile = cpq_industries.profile_of("packaging")
        self.assertEqual(profile["label"], "包装")
        self.assertEqual(handoff.COST_PROFILE, profile["cost_profile"])
        self.assertEqual(quote.COST_PROFILE, profile["cost_profile"])
        self.assertEqual(handoff.PRICING_PROFILE, profile["pricing_profile"])
        self.assertEqual(quote.PRICING_PROFILE, profile["pricing_profile"])

    def test_a8_error_classes_carry_status_and_code(self):
        handoff = load_handoff_module()
        quote = load_quote_module()
        self.assertIsNotNone(handoff, "缺少 packaging_handoff.py（Spec §4.1）")
        self.assertIsNotNone(quote, "缺少 cpq_packaging_quote.py（Spec §4.2）")
        exc = handoff.HandoffError("x", 409, "cost_gaps_unresolved")
        self.assertEqual((exc.status_code, exc.code), (409, "cost_gaps_unresolved"))
        self.assertEqual(str(exc), "x")
        qexc = quote.PricingError("y", 400, "invalid_rate")
        self.assertEqual((qexc.status_code, qexc.code), (400, "invalid_rate"))

    def test_a9_document_sections(self):
        module = load_quote_module()
        self.assertIsNotNone(module, "缺少 cpq_packaging_quote.py（Spec §4.2）")
        titles = [title for _, title in module.DOC_SECTIONS]
        self.assertEqual(titles,
                         ["报价基本信息", "产品与盒型", "部件与材料", "工艺路线",
                          "成本构成", "定价与加价", "税金与总额", "来源与可追溯"],
                         "报价单八节与顺序见 Spec §2.5")

    def test_a10_modules_are_offline(self):
        for module in (load_handoff_module(), load_quote_module()):
            self.assertIsNotNone(module, "模块缺失（Spec §4.1 / §4.2）")
            source = inspect.getsource(module)
            for forbidden in ("openai", "requests", "httpx", "socket", "anthropic"):
                self.assertFalse(forbidden in source,
                                 "%s 不得出现 %s：不联网、不调模型（Spec §5）"
                                 % (module.__name__, forbidden))


# --------------------------------------------------------------------------- #
# B. 交接包内容（10 组）
# --------------------------------------------------------------------------- #
class BHandoffPackage(HandoffCase):
    def setUp(self):
        super().setUp()
        self.prepare()

    def test_b1_all_sections_present(self):
        package = self.package()
        missing = [key for key in SECTION_KEYS if key not in package]
        self.assertEqual(missing, [], "交接包缺组（Spec §2.2）：%s" % (missing,))
        self.assertEqual(package["industry"], "packaging")
        self.assertEqual(package["industry_label"], "包装")
        self.assertEqual(package["cost_profile"], "packaging_v1")
        self.assertEqual(package["pricing_profile"], "packaging_margin_v1")
        self.assertEqual(package["handoff_kind"], "packaging_cost_to_quote")
        self.assertEqual(package["handoff_version"], "pkg-quote-handoff-v1")

    def test_b2_requirement_section_is_the_source_document(self):
        package = self.package()
        requirement = store.load_requirement(PID) or {}
        bad = subset(package["requirement"], dict(requirement.get("data") or {}),
                     "requirement")
        self.assertEqual(bad, [], "需求段必须原样来自需求单：%s" % (bad,))

    def test_b3_box_type_section_is_the_confirmed_box(self):
        package = self.package()
        box = packaging_match.load_box_match(PID, REQ_NO)
        bad = subset(package["box_type"], {"confirmed_box_type": box["confirmed_box_type"],
                                          "decision": box["decision"]}, "box_type")
        self.assertEqual(bad, [], "盒型段必须来自第 4 批已确认盒型：%s" % (bad,))
        self.assertEqual(package["box_type"]["confirmed_box_type"], BOX_MAIN)

    def test_b4_params_section_carries_geometry_inputs(self):
        package = self.package()
        bad = subset(package["params"], {"inner_length": 200, "inner_width": 150,
                                        "inner_height": 80, "fit_clearance": 1.8},
                     "params")
        self.assertEqual(bad, [], "参数段必须带 L/W/H 与配合间隙：%s" % (bad,))

    def test_b5_bom_section_is_batch5_output(self):
        package = self.package()
        bom = packaging_bom.load_bom(PID, REQ_NO)
        self.assertEqual(package["bom"].get("built"), bom.get("built"))
        self.assertEqual(len(package["bom"].get("items") or []), len(bom.get("items") or []),
                         "BOM 段必须逐行来自第 5 批 load_bom")
        self.assertTrue(package["bom"].get("items"), "BOM 段不该是空的")

    def test_b6_route_section_is_batch6_output(self):
        package = self.package()
        route = packaging_route.load_route(PID, REQ_NO)
        self.assertEqual(package["route"].get("status"), route.get("status"))
        self.assertEqual(len(package["route"].get("steps") or []),
                         len(route.get("steps") or []), "路线段必须来自第 6 批 load_route")
        self.assertEqual(package["route"].get("status"), "confirmed")

    def test_b7_cost_section_is_batch7_output(self):
        from tech_app.backend.services import packaging_cost
        package = self.package()
        cost = packaging_cost.load_cost(PID, REQ_NO)
        bad = subset(package["cost"], {"total_cost": cost["total_cost"],
                                      "subtotal": cost["subtotal"],
                                      "has_gaps": bool(cost["has_gaps"]),
                                      "engine_version": cost["engine_version"],
                                      "cost_profile": cost["cost_profile"]},
                     "cost")
        self.assertEqual(bad, [], "成本段必须逐项来自第 7 批 load_cost：%s" % (bad,))
        self.assertAlmostEqual(package["cost"]["total_cost"], COST_WORKRATE, places=9)

    def test_b8_cost_categories_and_report_groups(self):
        from tech_app.backend.services import packaging_cost
        package = self.package()
        cost = packaging_cost.load_cost(PID, REQ_NO)
        self.assertEqual(package["cost"].get("categories"), cost.get("categories"),
                         "24 类别必须随包带走（Spec §2.2）")
        self.assertEqual(package["cost"].get("report_groups"), cost.get("report_groups"),
                         "10 分组必须随包带走（Spec §2.2）")

    def test_b9_gaps_are_carried_verbatim(self):
        from tech_app.backend.services import packaging_cost
        package = self.package()
        cost = packaging_cost.load_cost(PID, REQ_NO)
        self.assertEqual(package["gaps"], cost["gaps"],
                         "缺口必须逐条原样透传（Spec §2.2）")

    def test_b10_formulas_carry_source_and_version(self):
        package = self.package()
        self.assertTrue(package["formulas"], "公式依据不能为空（Spec §2.2）")
        for entry in package["formulas"]:
            for key in ("formula_code", "expression", "result"):
                self.assertTrue(str(entry.get(key) or "").strip() or key == "result",
                                "公式依据缺 %s：%r" % (key, entry))
            self.assertTrue("inputs" in entry, "公式依据缺 inputs：%r" % (entry,))
            self.assertTrue(str(entry.get("source") or "").strip() or
                            str(entry.get("source_ref") or "").strip(),
                            "公式依据要能溯源（source / source_ref）：%r" % (entry,))

    def test_b11_source_section_is_traceable(self):
        package = self.package()
        source = package["source"]
        self.assertEqual(source.get("project_id"), PID)
        self.assertEqual(source.get("requirement_no"), REQ_NO)
        self.assertEqual(source.get("scenario_code"), "default")
        self.assertTrue(str(source.get("result_version") or "").strip(),
                        "回传必须带成本结果版本（否则报价侧无法判重）")
        self.assertEqual(source.get("result_version"), package.get("result_version"),
                         "source.result_version 与顶层 result_version 必须一致")

    def test_b12_no_selling_price_leaks_into_package(self):
        package = self.package()
        hits = []
        for path, key in walk_keys(package.get("cost") or {}, "cost"):
            if key in FORBIDDEN_IN_PACKAGE_COST:
                hits.append(path)
        self.assertEqual(hits, [],
                         "交接包的成本段不许出现售价/毛利字段（Spec §2.2）：%s" % (hits,))

    def test_b13_package_read_is_pure(self):
        before = len(self.handoff_rows())
        first = self.package()
        second = self.package()
        self.assertEqual(before, len(self.handoff_rows()), "handoff_package 不许落库（Spec §4.1）")
        self.assertEqual(module_fingerprint(self.handoff_mod(), first),
                         module_fingerprint(self.handoff_mod(), second),
                         "同一状态两次组装必须同包")

    def test_b14_bridge_result_marks_industry(self):
        module = self.handoff_mod()
        package = self.package()
        result = module.bridge_result(package)
        self.assertEqual(result.get("industry"), "packaging",
                         "回传正文必须带 industry，桥接层据此分流（Spec §4.3）")
        self.assertEqual(result.get("packaging_package"), package,
                         "回传正文必须原样带整包（Spec §4.3）")


def module_fingerprint(module, package):
    """包内容的稳定指纹：先按实现的摘要，退化时用 JSON 排序序列化。"""
    fingerprint = getattr(module, "package_fingerprint", None)
    if callable(fingerprint):
        return fingerprint(package)
    return json.dumps(package, ensure_ascii=False, sort_keys=True, default=str)


BRIDGE_OK = {"quote_session_id": "qsess-pkg-1", "next_step_no": 3,
             "next_step_name": "定价-利润加成", "handoff_id": "h-pkg-1",
             "business_case_id": "bc_pkg0001", "already_sent": False,
             "handoff": {"task_id": "9001", "target_role_name": "销售经理",
                         "target_name": "SM1", "returned_to_sender": False}}


class CHandoffGuards(HandoffCase):
    """交接前置：非包装 / 无成本 / 有缺口 / 放行留痕。"""

    def send(self, **kwargs):
        from tech_app.backend.services import cpq_bridge
        args = {"scenario": None, "user": FINANCE, "token": "tkn"}
        args.update(kwargs)
        with mock.patch.object(cpq_bridge, "send_to_quote", return_value=dict(BRIDGE_OK)) as fake:
            module = self.handoff_mod()
            result = module.send_to_quote(PID, REQ_NO, **args)
        return result, fake

    def test_c1_non_packaging_is_refused(self):
        self.save_requirement(industry="semiconductor")
        module = self.handoff_mod()
        with self.assertRaises(module.HandoffError) as ctx:
            module.send_to_quote(PID, REQ_NO, user=FINANCE, token="tkn")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertEqual(ctx.exception.code, "not_packaging")
        self.assertEqual(len(self.handoff_rows()), 0, "被拒绝的回传不许留下交接记录")

    def test_c2_cost_missing_is_refused(self):
        self.prepare(with_cost=False)
        module = self.handoff_mod()
        with self.assertRaises(module.HandoffError) as ctx:
            module.send_to_quote(PID, REQ_NO, user=FINANCE, token="tkn")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "cost_not_built")

    def test_c3_gaps_block_the_handoff(self):
        gaps = [{"code": "no_formula:print", "where": "BOX-MAIN/print", "detail": "手填列"}]
        self.prepare(has_gaps=True, gaps=gaps)
        module = self.handoff_mod()
        with self.assertRaises(module.HandoffError) as ctx:
            module.send_to_quote(PID, REQ_NO, user=FINANCE, token="tkn")
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(ctx.exception.code, "cost_gaps_unresolved")
        self.assertTrue("no_formula:print" in str(ctx.exception),
                        "缺口拒绝必须点名缺什么：%s" % (str(ctx.exception),))
        self.assertEqual(len(self.handoff_rows()), 0, "缺口未清时不许留下交接记录")

    def test_c4_allow_gaps_needs_a_reason(self):
        self.prepare(has_gaps=True)
        module = self.handoff_mod()
        with self.assertRaises(module.HandoffError) as ctx:
            module.send_to_quote(PID, REQ_NO, user=FINANCE, token="tkn", allow_gaps=True)
        self.assertIn(ctx.exception.code, ("cost_gaps_unresolved", "gap_reason_required"),
                      "放行必须写明原因，不许默默放行")
        self.assertEqual(len(self.handoff_rows()), 0)

    def test_c5_allow_gaps_with_reason_is_traceable(self):
        self.prepare(has_gaps=True)
        result, fake = self.send(allow_gaps=True, reason="客户同意按缺料价先出草稿")
        rows = self.handoff_rows()
        self.assertEqual(len(rows), 1, "写明原因的放行必须留下交接记录")
        row = rows[0]
        self.assertTrue(row.get("has_gaps"))
        waiver = row.get("gap_waiver") or {}
        self.assertEqual(waiver.get("by"), "fin1")
        self.assertTrue(str(waiver.get("at") or "").strip(), "放行要记时间")
        self.assertEqual(waiver.get("reason"), "客户同意按缺料价先出草稿")
        self.assertTrue(result.get("handoff"))

    def test_c6_route_role_gate(self):
        from tech_app.backend import main
        from fastapi import HTTPException
        route = getattr(main, "send_requirement_packaging_quote", None)
        self.assertIsNotNone(
            route, "缺少 POST /api/projects/{project_id}/requirement/packaging-quote/send（Spec §4.5）")
        self.prepare()
        body = type("B", (), {"requirement_no": REQ_NO, "scenario": None,
                              "allow_gaps": False, "reason": ""})()
        with self.assertRaises(HTTPException) as ctx:
            route(PID, body, VIEWER)
        self.assertEqual(ctx.exception.status_code, 403, "只读用户不许回传报价")

    def test_c7_missing_requirement_gives_business_error(self):
        module = self.handoff_mod()
        with self.assertRaises(module.HandoffError):
            module.handoff_package(PID, REQ_NO)
        with self.assertRaises(module.HandoffError):
            module.send_to_quote(PID, REQ_NO, user=FINANCE, token="tkn")

    def test_c9_read_routes_exist(self):
        from tech_app.backend import main
        routes = (("get_requirement_packaging_quote",
                   "GET /api/projects/{pid}/requirement/packaging-quote"),
                  ("get_requirement_packaging_quote_versions",
                   "GET /api/projects/{pid}/requirement/packaging-quote/versions"),
                  ("get_requirement_packaging_quote_package",
                   "GET /api/projects/{pid}/requirement/packaging-quote/package"))
        for name, label in routes:
            self.assertIsNotNone(getattr(main, name, None),
                                 "缺少 %s（Spec §4.5）" % label)

    def test_c8_non_packaging_leaves_no_trace(self):
        self.save_requirement(industry="battery")
        module = self.handoff_mod()
        for kwargs in ({"allow_gaps": True, "reason": "强行放行"}, {}):
            with self.assertRaises(module.HandoffError):
                module.send_to_quote(PID, REQ_NO, user=FINANCE, token="tkn", **kwargs)
        self.assertEqual(len(self.handoff_rows()), 0,
                         "非包装项目不许被包装交接接管（三行业链路隔离）")


class DHandoffStore(HandoffCase):
    """交接落库：只增不改、同包幂等、重算后新版本。"""

    def setUp(self):
        super().setUp()
        self.prepare()

    def send(self, **kwargs):
        from tech_app.backend.services import cpq_bridge
        args = {"user": FINANCE, "token": "tkn"}
        args.update(kwargs)
        with mock.patch.object(cpq_bridge, "send_to_quote", return_value=dict(BRIDGE_OK)) as fake:
            module = self.handoff_mod()
            return module.send_to_quote(PID, REQ_NO, **args), fake

    def test_d1_row_fields_complete(self):
        self.send()
        rows = self.handoff_rows()
        self.assertEqual(len(rows), 1, "一次成功回传必须留一条交接记录（Spec §3.1）")
        row = rows[0]
        self.assertEqual(row.get("industry"), "packaging")
        self.assertEqual(row.get("engine_version"), "packaging_handoff_v1")
        self.assertEqual(row.get("handoff_version"), "pkg-quote-handoff-v1")
        self.assertEqual(row.get("handoff_kind"), "packaging_cost_to_quote")
        self.assertEqual(row.get("cost_profile"), "packaging_v1")
        self.assertEqual(row.get("pricing_profile"), "packaging_margin_v1")
        self.assertEqual(int(row.get("version_no") or 0), 1)
        self.assertTrue(str(row.get("package_fingerprint") or "").strip())
        self.assertEqual(row.get("target_quote_session_id"), "qsess-pkg-1")
        self.assertEqual(str(row.get("target_task_id") or ""), "9001")
        self.assertEqual(row.get("sent_by"), "fin1")
        self.assertTrue(str(row.get("sent_at") or "").strip())

    def test_d2_identical_package_is_idempotent(self):
        first, fake_first = self.send()
        second, fake_second = self.send()
        rows = self.handoff_rows()
        self.assertEqual(len(rows), 1, "同一个包重复发送不许新增行（Spec §3.1 唯一约束）")
        self.assertTrue(second.get("already_sent"), "重复发送必须回 already_sent=True")
        self.assertEqual(second.get("handoff_no"), first.get("handoff_no"))
        self.assertEqual(fake_first.call_count, 1)
        self.assertEqual(fake_second.call_count, 0, "重复发送不该再建一次报价任务")

    def test_d3_recomputed_cost_creates_new_version(self):
        self.send()
        before = dict(self.handoff_rows()[0])
        self.save_cost(cost_total=COST_STANDARD)
        result, _ = self.send()
        rows = sorted(self.handoff_rows(), key=lambda r: int(r.get("version_no") or 0))
        self.assertEqual([int(r["version_no"]) for r in rows], [1, 2],
                         "成本变了必须新建版本，不许覆盖旧版本")
        self.assertEqual(dict(rows[0]), before, "旧版本必须逐字不变")
        self.assertNotEqual(rows[0]["package_fingerprint"], rows[1]["package_fingerprint"])
        self.assertEqual(int(result.get("version_no") or 0), 2)

    def test_d4_load_handoff_returns_latest(self):
        self.send()
        self.save_cost(cost_total=COST_STANDARD)
        self.send()
        module = self.handoff_mod()
        record = module.load_handoff(PID, REQ_NO)
        self.assertTrue(record, "load_handoff 必须给回最近一次交接")
        self.assertEqual(int(record.get("version_no") or 0), 2)

    def test_d5_handoff_versions_are_descending(self):
        self.send()
        self.save_cost(cost_total=COST_STANDARD)
        self.send()
        module = self.handoff_mod()
        versions = module.handoff_versions(PID, REQ_NO)
        self.assertEqual([int(v["version_no"]) for v in versions], [2, 1],
                         "版本列表按版本号降序，两版都在（Spec §2.6）")

    def test_d6_store_is_append_only(self):
        self.send()
        self.save_cost(cost_total=COST_STANDARD)
        self.send()
        rows = self.handoff_rows()
        self.assertEqual(len(rows), 2)
        keys = set().union(*[set(r) for r in rows])
        self.assertNotIn("updated_at", keys, "交接记录只增不改：不允许有 updated_at（Spec §3.1）")
        self.assertEqual(len({r["handoff_no"] for r in rows}), 2, "两版必须各有自己的编号")

    def test_d7_quote_task_is_created_once_per_version(self):
        _, fake = self.send()
        self.save_cost(cost_total=COST_STANDARD)
        self.send()
        self.assertEqual(fake.call_count, 1)
        payload = fake.call_args[0][7] if len(fake.call_args[0]) > 7 else fake.call_args[1].get("result")
        self.assertEqual(payload.get("industry"), "packaging")

    def test_d8_handoff_no_is_readable(self):
        self.send()
        row = self.handoff_rows()[0]
        no = str(row.get("handoff_no") or "")
        self.assertTrue(no.startswith("pkghandoff:"), "交接编号格式见 Spec §3.1：%r" % (no,))
        for part in (PID, REQ_NO, "default", "1"):
            self.assertTrue(part in no, "交接编号必须能读出 %s：%r" % (part, no))


try:                                                    # 报价侧受控假库（批次 3 的引擎）
    import wf_handoff_harness as H
except Exception as _exc:                               # pragma: no cover - 环境问题
    H = None
    H_ERROR = f"{type(_exc).__name__}: {_exc}"
else:
    H_ERROR = ""


# --------------------------------------------------------------------------- #
# E. 定价引擎
# --------------------------------------------------------------------------- #
class PriceCase(unittest.TestCase):
    """定价用例的共用夹具（E / F 各自继承，避免 F 把 E 的用例再跑一遍）。"""

    def setUp(self):
        self.module = load_quote_module()
        self.assertIsNotNone(self.module, "缺少 cpq_packaging_quote.py（Spec §4.2）")

    def price(self, **kwargs):
        package = kwargs.pop("package", None) or package_fixture()
        return self.module.price(package, **kwargs)


class EPrice(PriceCase):
    def test_e1_golden_untaxed_price_from_workrate(self):
        self.assertAlmostEqual(self.module.untaxed_unit_price(COST_WORKRATE, MARGIN),
                               PRICE_WORKRATE, places=9,
                               msg="报价-工费率!AX2 = AV2/(1-AW2)（Spec §1.2）")

    def test_e2_golden_untaxed_price_from_standard(self):
        self.assertAlmostEqual(self.module.untaxed_unit_price(COST_STANDARD, MARGIN),
                               PRICE_STANDARD, places=9,
                               msg="报价表!I2 = G2/(1-H2)（Spec §1.2）")

    def test_e3_markup_mode_is_a_different_number(self):
        markup = self.module.untaxed_unit_price(COST_STANDARD, MARGIN, pricing_mode="markup")
        self.assertAlmostEqual(markup, PRICE_MARKUP_STANDARD, places=9,
                               msg="问题点!A30 的文字口径 = G2×(1+H2)")
        self.assertAlmostEqual(PRICE_STANDARD - markup, MARKUP_GAP, places=9,
                               msg="两种口径必须给出两个答案（Spec §1.2）")

    def test_e4_rate_guards(self):
        cases = [
            ({"total_cost": COST_STANDARD, "rate_gross": -0.1}, "invalid_rate"),
            ({"total_cost": COST_STANDARD, "rate_gross": 1.0}, "invalid_rate"),
            ({"total_cost": COST_STANDARD, "rate_gross": 1.4}, "invalid_rate"),
            ({"total_cost": COST_STANDARD, "rate_gross": None}, "rate_missing"),
        ]
        for case, code in cases:
            with self.subTest(code=code):
                kwargs = {"gross_margin_rate": case["rate_gross"]}
                with self.assertRaises(self.module.PricingError) as ctx:
                    self.price(package=package_fixture(cost=cost_fixture(
                        total_cost=case["total_cost"])), **kwargs)
                self.assertEqual(ctx.exception.code, code)
        with self.assertRaises(self.module.PricingError) as ctx:
            self.module.untaxed_unit_price(COST_STANDARD, MARGIN, pricing_mode="加价")
        self.assertEqual(ctx.exception.code, "invalid_pricing_mode")

    def test_e5_price_records_the_mode_and_rate(self):
        quote = self.price(gross_margin_rate=MARGIN)
        self.assertEqual(quote["pricing_mode"], "gross_margin")
        self.assertAlmostEqual(quote["gross_margin_rate"], MARGIN, places=9)
        self.assertIsNone(quote.get("markup_rate"), "两个概念不许混用（Spec §1.2）")
        self.assertAlmostEqual(quote["cost_total"], COST_WORKRATE, places=9)
        self.assertAlmostEqual(quote["untaxed_unit_price"], PRICE_WORKRATE, places=9)
        self.assertAlmostEqual(quote["untaxed_total"], PRICE_WORKRATE * 1000, places=6)
        self.assertEqual(quote["quote_quantity"], 1000)
        self.assertEqual(quote["industry"], "packaging")
        self.assertEqual(quote["pricing_profile"], "packaging_margin_v1")
        self.assertEqual(quote["engine_version"], "packaging_quote_v1")

    def test_e6_markup_mode_records_markup_rate(self):
        quote = self.price(package=package_fixture(cost=cost_fixture(total_cost=COST_STANDARD)),
                           pricing_mode="markup", markup_rate=MARGIN)
        self.assertEqual(quote["pricing_mode"], "markup")
        self.assertAlmostEqual(quote["markup_rate"], MARGIN, places=9)
        self.assertIsNone(quote.get("gross_margin_rate"))
        self.assertAlmostEqual(quote["untaxed_unit_price"], PRICE_MARKUP_STANDARD, places=9)

    def test_e7_addons_are_a_closed_set(self):
        quote = self.price(gross_margin_rate=MARGIN,
                           addons={"tech_premium": 1.5, "market_adjustment": 0.3,
                                   "other_addon": 0.2})
        self.assertAlmostEqual(quote["addon_total"], 2.0, places=9)
        self.assertAlmostEqual(quote["subtotal_unit"], PRICE_WORKRATE + 2.0, places=9)
        labels = {line["code"]: line["label"] for line in quote["addons"]}
        self.assertEqual(labels, {"tech_premium": "技术溢价", "market_adjustment": "市场调节",
                                  "other_addon": "其他加价"})
        with self.assertRaises(self.module.PricingError) as ctx:
            self.price(gross_margin_rate=MARGIN, addons={"freight_surcharge": 3})
        self.assertEqual(ctx.exception.code, "unknown_addon",
                         "闭集外的加价项不许静默忽略（Spec §2.4）")

    def test_e8_discount_applies_after_margin_and_addons(self):
        quote = self.price(gross_margin_rate=MARGIN, addons={"tech_premium": 2.0},
                           discount={"rate": 0.05})
        subtotal = PRICE_WORKRATE + 2.0
        self.assertAlmostEqual(quote["subtotal_unit"], subtotal, places=9)
        self.assertAlmostEqual(quote["discount_amount"], subtotal * 0.05, places=9)
        self.assertAlmostEqual(quote["net_unit_price"], subtotal * 0.95, places=9)
        with self.assertRaises(self.module.PricingError) as ctx:
            self.price(gross_margin_rate=MARGIN, discount={"rate": 1.5})
        self.assertEqual(ctx.exception.code, "invalid_rate")

    def test_e9_tax_and_totals(self):
        quote = self.price(package=package_fixture(cost=cost_fixture(total_cost=COST_STANDARD)),
                           gross_margin_rate=MARGIN)
        self.assertAlmostEqual(quote["tax_rate"], TAX_RATE, places=9)
        self.assertAlmostEqual(quote["tax_amount"], GOLDEN_TAX, places=6)
        self.assertAlmostEqual(quote["taxed_unit_price"], GOLDEN_TAXED, places=6)
        self.assertAlmostEqual(quote["taxed_total"], GOLDEN_TAXED * 1000, places=3)

    def test_e10_golden_chain(self):
        quote = self.price(package=package_fixture(cost=cost_fixture(total_cost=COST_STANDARD)),
                           gross_margin_rate=MARGIN, addons={"tech_premium": 2.0},
                           discount={"rate": 0.05}, tax_rate=TAX_RATE)
        self.assertAlmostEqual(quote["untaxed_unit_price"], PRICE_STANDARD, places=6)
        self.assertAlmostEqual(quote["net_unit_price"] * 1.13, GOLDEN_CHAIN, places=6,
                               msg="顺序必须是 毛利 → 加价 → 折扣 → 税金（Spec §2.4）")
        self.assertAlmostEqual(quote["taxed_unit_price"], GOLDEN_CHAIN, places=6)

    def test_e11_tax_rate_guards(self):
        for rate in (1.5, -0.1):
            with self.subTest(rate=rate):
                with self.assertRaises(self.module.PricingError) as ctx:
                    self.price(gross_margin_rate=MARGIN, tax_rate=rate)
                self.assertEqual(ctx.exception.code, "invalid_tax_rate")

    def test_e12_quantity_guards(self):
        requirement = dict(package_fixture()["requirement"])
        requirement.pop("quote_quantity")
        package = package_fixture(requirement=requirement)
        with self.assertRaises(self.module.PricingError) as ctx:
            self.price(package=package, gross_margin_rate=MARGIN)
        self.assertEqual(ctx.exception.code, "invalid_quantity")
        with self.assertRaises(self.module.PricingError) as ctx:
            self.price(package=package, gross_margin_rate=MARGIN, quote_quantity=0)
        self.assertEqual(ctx.exception.code, "invalid_quantity")

    def test_e13_non_packaging_is_refused(self):
        package = package_fixture(industry="semiconductor")
        with self.assertRaises(self.module.PricingError) as ctx:
            self.price(package=package, gross_margin_rate=MARGIN)
        self.assertEqual((ctx.exception.status_code, ctx.exception.code), (400, "not_packaging"))

    def test_e14_gaps_cannot_be_priced(self):
        package = package_fixture(cost=cost_fixture(has_gaps=True,
                                                    gaps=[{"code": "no_formula:print"}]))
        with self.assertRaises(self.module.PricingError) as ctx:
            self.price(package=package, gross_margin_rate=MARGIN)
        self.assertEqual((ctx.exception.status_code, ctx.exception.code),
                         (409, "cost_gaps_unresolved"))

    def test_e15_price_is_pure_and_stable(self):
        package = package_fixture()
        frozen = copy.deepcopy(package)
        first = self.module.price(package, gross_margin_rate=MARGIN)
        self.assertEqual(package, frozen, "price 不许改入参（Spec §2.4）")
        for _ in range(200):
            again = self.module.price(copy.deepcopy(package), gross_margin_rate=MARGIN)
            self.assertEqual({k: v for k, v in again.items() if k != "priced_at"},
                             {k: v for k, v in first.items() if k != "priced_at"},
                             "同一输入必须逐次同结果（可复算）")

    def test_e16_recompute_matches(self):
        quote = self.price(gross_margin_rate=MARGIN, addons={"market_adjustment": 0.4},
                           discount={"rate": 0.02})
        again = self.module.recompute(quote)
        for key in ("untaxed_unit_price", "subtotal_unit", "net_unit_price", "tax_amount",
                    "taxed_unit_price", "taxed_total"):
            self.assertAlmostEqual(again[key], quote[key], places=9,
                                   msg="recompute 必须逐项相等：%s" % key)

    def test_e17_lines_carry_formula_and_source(self):
        quote = self.price(gross_margin_rate=MARGIN)
        self.assertTrue(quote["lines"], "定价必须逐行给出依据（Spec §2.4）")
        for line in quote["lines"]:
            for key in ("code", "label", "formula", "inputs", "result"):
                self.assertIn(key, line, "定价行缺 %s：%r" % (key, line))
            self.assertTrue(str(line.get("source") or "").strip() or
                            str(line.get("version") or "").strip(),
                            "定价行要能溯源（source / version）：%r" % (line,))


# --------------------------------------------------------------------------- #
# F. 报价单
# --------------------------------------------------------------------------- #
class FDocument(PriceCase):
    def test_f1_document_sections(self):
        quote = self.price(gross_margin_rate=MARGIN)
        document = self.module.document(quote)
        self.assertEqual([item["title"] for item in document["sections"]],
                         [title for _, title in self.module.DOC_SECTIONS],
                         "报价单八节与顺序见 Spec §2.5")
        self.assertTrue(str(document.get("markdown") or "").strip())

    def test_f2_markdown_carries_the_numbers(self):
        quote = self.price(gross_margin_rate=MARGIN)
        markdown = self.module.document(quote)["markdown"]
        self.assertIn("103.58", markdown, "报价单必须写出未税单价（Spec §2.5）")
        self.assertIn("117.05", markdown, "报价单必须写出含税单价")
        self.assertIn("117045.70", markdown, "报价单必须写出含税总额")

    def test_f3_document_numbers_match_quote(self):
        quote = self.price(gross_margin_rate=MARGIN, addons={"tech_premium": 1.0},
                           discount={"rate": 0.1})
        document = self.module.document(quote)
        flat = json.dumps(document, ensure_ascii=False)
        for key in ("untaxed_unit_price", "net_unit_price", "taxed_unit_price",
                    "taxed_total", "cost_total"):
            rendered = "%.2f" % float(quote[key])
            self.assertIn(rendered, flat,
                          "报价单里的 %s 必须与 quote 字段 %s 一致（Spec §2.5）"
                          % (key, rendered))

    def test_f4_document_is_traceable(self):
        package = package_fixture()
        package["source"]["handoff_id"] = "h-pkg-1"
        quote = self.price(package=package, gross_margin_rate=MARGIN)
        document = self.module.document(quote)
        flat = json.dumps(document, ensure_ascii=False)
        for token in (PID, "h-pkg-1", "packaging_quote_v1", "packaging_v1",
                      "packaging_margin_v1"):
            self.assertIn(token, flat, "报价单必须能追溯到 %s（Spec §2.5）" % token)

    def test_f5_gaps_have_no_formal_document(self):
        package = package_fixture(cost=cost_fixture(has_gaps=True,
                                                   gaps=[{"code": "below_moq"}]))
        with self.assertRaises(self.module.PricingError):
            self.module.price(package, gross_margin_rate=MARGIN)

    def test_f6_title_is_readable(self):
        quote = self.price(gross_margin_rate=MARGIN)
        title = self.module.document(quote)["title"]
        self.assertIn("包装", title)
        self.assertIn(BOX_MAIN, title)

    def test_f8_sections_carry_workbench_numbers(self):
        quote = self.price(gross_margin_rate=MARGIN)
        sections = self.module.sections(quote)
        for key in ("s3_markup", "s4_markup", "s5_basic", "s5_detail"):
            self.assertIn(key, sections, "工作台分区缺 %s（Spec §4.2 sections）" % key)
        markup = json.dumps(sections["s3_markup"], ensure_ascii=False)
        self.assertIn("0.25", markup, "定价分区必须显示毛利率：%s" % markup)
        self.assertIn("103.58", markup, "定价分区必须显示未税单价：%s" % markup)
        detail = json.dumps(sections["s5_detail"], ensure_ascii=False)
        self.assertIn("117.05", detail, "报价明细必须显示含税单价：%s" % detail)

    def test_f7_document_is_deterministic(self):
        quote = self.price(gross_margin_rate=MARGIN)
        first = self.module.document(quote)
        second = self.module.document(copy.deepcopy(quote))
        self.assertEqual(json.dumps(first, ensure_ascii=False, sort_keys=True),
                         json.dumps(second, ensure_ascii=False, sort_keys=True),
                         "报价单不许带时间戳/随机数（历史打开要能逐字恢复）")


# --------------------------------------------------------------------------- #
# G. 报价版本（只增不改）
# --------------------------------------------------------------------------- #
class QuoteStoreCase(unittest.TestCase):
    """报价侧受控假库：真跑 cpq_wf / cpq_packaging_quote 发出的 SQL，绝不连线上 PG。"""

    def setUp(self):
        self.assertIsNotNone(H, "受控假库不可用：%s" % H_ERROR)
        self.assertTrue(H.LOAD_ERROR == "", H.LOAD_ERROR)
        self.assertTrue(H.SELFCHECK_OK, "受控假库自检失败：%s" % H.SELFCHECK_ERROR)
        self._ctx = H.Workbench()
        self.wb = self._ctx.__enter__()
        self.session = H.QUOTE_SESSION
        import cpq_auth
        self.conn = cpq_auth._connect()
        # 不在这里断言报价模块存在：H 组验的是桥接落点，不该被定价模块的缺失挡住。
        self.module = load_quote_module()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)

    def quote_module(self):
        self.assertIsNotNone(self.module, "缺少 cpq_packaging_quote.py（Spec §4.2）")
        return self.module

    def rows(self):
        return [dict(row) for row in self.wb.db.table("cpq_wf_quote_version")]

    def quote(self, cost_total=COST_WORKRATE, addons=None, **over):
        module = self.quote_module()
        package = package_fixture(cost=cost_fixture(total_cost=cost_total))
        package["source"]["source_session_id"] = self.session
        package["source"]["business_case_id"] = "bc_pkg0001"
        package["source"]["handoff_id"] = "h-pkg-1"
        return module.price(package, gross_margin_rate=MARGIN, addons=addons or {}, **over)

    def save(self, quote, user=SALES):
        return self.quote_module().save_version(self.conn, quote, user=user)


class GQuoteVersion(QuoteStoreCase):
    def test_g1_first_version(self):
        saved = self.save(self.quote())
        rows = self.rows()
        self.assertEqual(len(rows), 1, "第一次定价必须落一条版本（Spec §3.2）")
        row = rows[0]
        self.assertEqual(int(row["version_no"]), 1)
        self.assertEqual(row["industry"], "packaging")
        self.assertEqual(row["engine_version"], "packaging_quote_v1")
        self.assertEqual(row["pricing_profile"], "packaging_margin_v1")
        self.assertEqual(row["pricing_mode"], "gross_margin")
        self.assertEqual(row["quote_session_id"], self.session)
        self.assertEqual(row["business_case_id"], "bc_pkg0001")
        self.assertEqual(int(row["created_by_user_id"]), int(SALES["user_id"]))
        self.assertAlmostEqual(float(row["untaxed_unit_price"]), PRICE_WORKRATE, places=6)
        self.assertAlmostEqual(float(row["cost_total"]), COST_WORKRATE, places=6)
        self.assertEqual(int(saved["version_no"]), 1)
        self.assertFalse(saved.get("already_saved", False))

    def test_g2_same_quote_is_idempotent(self):
        self.save(self.quote())
        again = self.save(self.quote())
        self.assertEqual(len(self.rows()), 1, "同一次定价重复保存不许新建版本（Spec §2.6）")
        self.assertTrue(again.get("already_saved"))

    def test_g3_new_cost_makes_a_new_version(self):
        self.save(self.quote())
        saved = self.save(self.quote(cost_total=COST_STANDARD))
        rows = sorted(self.rows(), key=lambda r: int(r["version_no"]))
        self.assertEqual([int(r["version_no"]) for r in rows], [1, 2])
        self.assertEqual(int(saved["version_no"]), 2)
        self.assertEqual(int(rows[1]["previous_version_no"]), 1)
        self.assertAlmostEqual(float(rows[1]["previous_cost_total"]), COST_WORKRATE, places=6,
                               msg="新版必须记下原报价成本（0903 报价表 (2) 的「原报价成本」）")
        self.assertAlmostEqual(float(rows[1]["cost_total"]), COST_STANDARD, places=6)

    def test_g4_old_version_is_never_overwritten(self):
        self.save(self.quote())
        before = dict(self.rows()[0])
        self.save(self.quote(cost_total=COST_STANDARD))
        rows = sorted(self.rows(), key=lambda r: int(r["version_no"]))
        self.assertEqual(dict(rows[0]), before, "旧版本必须逐字不变（Spec §2.6）")
        self.assertNotEqual(rows[0]["document_md"], rows[1]["document_md"])
        self.assertNotEqual(rows[0]["quote_fingerprint"], rows[1]["quote_fingerprint"])

    def test_g5_versions_and_latest(self):
        self.save(self.quote())
        self.save(self.quote(cost_total=COST_STANDARD))
        versions = self.quote_module().versions(self.conn, business_case_id="bc_pkg0001")
        self.assertEqual([int(v["version_no"]) for v in versions], [2, 1])
        latest = self.quote_module().latest(self.conn, business_case_id="bc_pkg0001")
        self.assertEqual(int(latest["version_no"]), 2)
        self.assertAlmostEqual(float(latest["cost_total"]), COST_STANDARD, places=6)
        by_session = self.quote_module().versions(self.conn, quote_session_id=self.session)
        self.assertEqual(len(by_session), 2, "按会话号也必须查得到全部版本")

    def test_g6_role_gate(self):
        module = self.quote_module()
        for user in (VIEWER, {"user_id": "200", "username": "proc1", "role_code": "process_mgr",
                              "role_name": "工艺经理"}):
            with self.subTest(role=user["role_code"]):
                with self.assertRaises(module.PricingError) as ctx:
                    self.save(self.quote(), user=user)
                self.assertEqual((ctx.exception.status_code, ctx.exception.code),
                                 (403, "role_not_allowed"))
        self.assertEqual(self.rows(), [], "越权必须一行都不写（Spec §2.7）")

    def test_g7_version_table_is_append_only(self):
        self.save(self.quote())
        self.save(self.quote(cost_total=COST_STANDARD))
        keys = set().union(*[set(r) for r in self.rows()])
        self.assertNotIn("updated_at", keys, "报价版本只增不改：不许有 updated_at（Spec §3.2）")
        sql = " ".join(cpq_wf_ddl())
        self.assertIn("cpq_wf_quote_version", sql, "cpq_wf.init 的 DDL 必须建这张表（Spec §3.2）")

    def test_g8_restore_for_history(self):
        self.save(self.quote())
        self.save(self.quote(cost_total=COST_STANDARD))
        restored = self.quote_module().restore(self.conn, quote_session_id=self.session)
        self.assertTrue(restored.get("found"))
        self.assertEqual(int(restored["quote"]["version_no"]), 2)
        self.assertEqual(len(restored["versions"]), 2)
        self.assertTrue(str(restored.get("document", {}).get("markdown") or "").strip(),
                        "历史进入必须能恢复报价单（Spec §4.2 restore）")
        for key in ("s3_markup", "s4_markup", "s5_basic", "s5_detail"):
            self.assertIn(key, restored.get("sections") or {},
                          "历史进入必须能恢复工作台分区 %s（Spec §4.2 restore）" % key)
        empty = self.quote_module().restore(self.conn, quote_session_id="no-such-session")
        self.assertFalse(empty.get("found"), "没有版本时不许报错，给 found=False")

    def test_g9_versions_survive_reconnect(self):
        self.save(self.quote())
        import cpq_auth
        other = cpq_auth._connect()
        versions = self.quote_module().versions(other, business_case_id="bc_pkg0001")
        self.assertEqual(len(versions), 1, "重连后版本必须还在（历史打开的前提）")
        other.close()

    def test_g10_row_carries_traceability(self):
        self.save(self.quote(addons={"tech_premium": 1.0}))
        row = self.rows()[0]
        self.assertEqual(row["source_tech_project_id"], PID)
        self.assertEqual(row["source_handoff_id"], "h-pkg-1")
        self.assertTrue(str(row["inputs_json"] or "").strip(), "必须存算过的输入（可复算）")
        addons = json.loads(row["addons_json"] or "[]")
        self.assertEqual([item["code"] for item in addons], ["tech_premium"])
        self.assertTrue(str(row["document_md"] or "").strip(), "版本必须存报价单正文")


def cpq_wf_ddl():
    import cpq_wf
    return [json.dumps(entry, ensure_ascii=False, default=str) for entry in _ddl_statements(cpq_wf)]


def _ddl_statements(cpq_wf):
    try:
        return cpq_wf._ddl_pg("public")
    except Exception:
        return []



# --------------------------------------------------------------------------- #
# H. 桥接落点：回传之后报价卡片上必须有业务数据
# --------------------------------------------------------------------------- #
def as_dict(value):
    """任务 payload 在假库里可能是 dict，也可能是 JSON 串。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except ValueError:
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


class HBridgeLanding(QuoteStoreCase):
    def package(self, **over):
        package = package_fixture()
        package["source"]["source_session_id"] = self.session
        package.update(over)
        return package

    def call(self, **kwargs):
        """真跑 cpq_tech_bridge.send_to_quote（受控假库上的唯一回传命令）。"""
        try:
            out = self.wb.handoff(kind="packaging_cost_to_quote", **kwargs)
        except Exception as exc:                                 # noqa: BLE001
            return None, exc
        return out, None

    def handoff_tasks(self):
        import cpq_wf
        return [dict(row) for row in self.wb.rows("cpq_wf_task",
                                                 task_kind=cpq_wf.TASK_KIND_HANDOFF)]

    def test_h1_packaging_snapshot_sections(self):
        out, raised = self.call(result=self.package())
        self.assertIsNone(raised, "包装回传失败：%r" % (raised,))
        snapshot = self.wb.step2_snapshot() or {}
        for key in ("s2_packaging", "s2_packaging_cost", "packaging_package"):
            self.assertIn(key, snapshot,
                          "第 2 步快照必须有 %s：包装的盒型/参数/BOM/路线/成本不能丢（Spec §4.3）"
                          % key)
        for key in ("s2_packaging", "s2_packaging_cost", "packaging_package"):
            self.assertIn(key, out.get("returned_sections") or [],
                          "returned_sections 必须报出 %s（Spec §4.3）" % key)

    def test_h2_box_and_params_reach_the_card(self):
        self.call(result=self.package())
        snapshot = self.wb.step2_snapshot() or {}
        raw = json.dumps(snapshot.get("s2_packaging"), ensure_ascii=False)
        for token in (BOX_MAIN, "天地盖", "1000", "包装"):
            self.assertIn(token, raw, "第 2 步快照的包装段必须能读出 %s：%s" % (token, raw))

    def test_h3_cost_reaches_the_card(self):
        self.call(result=self.package())
        snapshot = self.wb.step2_snapshot() or {}
        raw = json.dumps(snapshot.get("s2_packaging_cost"), ensure_ascii=False)
        self.assertIn("77.69", raw, "第 2 步快照必须带单件总成本：%s" % raw)
        self.assertIn("packaging_cost_v1", raw, "快照要能看出成本版本：%s" % raw)

    def test_h4_task_payload_carries_the_package(self):
        package = self.package()
        self.call(result=package)
        tasks = self.handoff_tasks()
        self.assertEqual(len(tasks), 1, "回传必须建一条报价任务（Spec §4.3）")
        payload = as_dict(tasks[0].get("payload"))
        self.assertEqual(payload.get("packaging_package"), package,
                         "任务 payload 必须带整包：不能只有任务卡没有业务数据（Spec §4.3）")
        self.assertEqual(payload.get("handoff_kind"), "packaging_cost_to_quote")
        self.assertEqual(payload.get("source_project_id"), H.TECH_PROJECT,
                         "来源技术项目必须可追溯（Spec §4.3）")

    def test_h5_handoff_records_snapshot_sections(self):
        self.call(result=self.package())
        rows = self.wb.handoffs()
        self.assertEqual(len(rows), 1)
        sections = rows[0].get("snapshot_sections")
        if isinstance(sections, str):
            sections = json.loads(sections)
        for key in ("s2_packaging", "s2_packaging_cost", "packaging_package"):
            self.assertIn(key, sections or [],
                          "交接记录必须记下写了哪些栏目（Spec §4.3）：%r" % (sections,))

    def test_h6_non_packaging_result_is_refused(self):
        import cpq_tech_bridge
        # 先证明这个口径本身是通的（否则「拒绝」会因为口径不存在而假通过）
        accepted, ok_error = self.call(result=self.package())
        self.assertIsNone(ok_error, "合法包装包必须能落：%r" % (ok_error,))
        self.assertIsNotNone(accepted)
        before = len(self.wb.tasks())
        out, raised = self.call(result=self.package(industry="battery"))
        self.assertIsNone(out, "非包装结果不许走包装口径")
        self.assertIsInstance(raised, cpq_tech_bridge.BridgeError)
        self.assertEqual(len(self.wb.tasks()), before, "被拒绝的回传不许建任务")

    def test_h7_gaps_block_the_landing(self):
        import cpq_tech_bridge
        before = len(self.wb.tasks())
        package = self.package()
        package["cost"] = cost_fixture(has_gaps=True,
                                       gaps=[{"code": "no_formula:print",
                                              "where": "BOX-MAIN/print"}])
        package["gaps"] = package["cost"]["gaps"]
        out, raised = self.call(result=package)
        self.assertIsNone(out, "有缺口的成本不许直接落成正式报价（Spec §2.3）")
        self.assertIsInstance(raised, cpq_tech_bridge.BridgeError)
        self.assertIn("no_formula:print", str(raised), "拒绝必须点名缺口：%r" % (raised,))
        self.assertEqual(len(self.wb.tasks()), before, "被拒绝的回传不许建任务")

    def test_h8_three_industries_unchanged(self):
        out, raised = self.wb.handoff(kind="cost_to_quote"), None
        self.assertIsNone(raised)
        snapshot = self.wb.step2_snapshot() or {}
        self.assertIn("s2_products", snapshot)
        for key in ("packaging_package", "s2_packaging", "s2_packaging_cost"):
            self.assertNotIn(key, snapshot,
                             "三行业回传的快照不许出现包装栏目（%s）" % key)
        payload = as_dict((self.handoff_tasks() or [{}])[0].get("payload"))
        self.assertNotIn("packaging_package", payload, "三行业 payload 不许出现整包键")
        self.assertEqual(payload.get("handoff_kind"), "cost_to_quote")
        self.assertEqual(3, int(out.get("next_step_no") or 0),
                         "三行业仍推进到第 3 步「定价-利润加成」")

    def test_h9_bridge_accepts_handoff_kind(self):
        from tech_app.backend.services import cpq_bridge
        signature = inspect.signature(cpq_bridge.send_to_quote)
        self.assertIn("handoff_kind", signature.parameters,
                      "回传客户端必须能指定口径（Spec §4.3）")
        self.assertEqual(signature.parameters["handoff_kind"].default, "cost_to_quote",
                         "默认值必须是 cost_to_quote：三行业调用点一字不改")


# --------------------------------------------------------------------------- #
# I. 报价服务入口：包装定价不依赖大模型
# --------------------------------------------------------------------------- #
class IServerEntry(unittest.TestCase):
    def setUp(self):
        import cpq_agent_server
        self.server = cpq_agent_server

    def call(self, data):
        handler = getattr(self.server, "_handle_packaging_quote_price", None)
        self.assertIsNotNone(
            handler, "缺少 cpq_agent_server._handle_packaging_quote_price（Spec §4.4）")
        return handler(data)

    def test_i1_prices_without_any_model(self):
        class Boom:
            def __getattr__(self, name):
                raise AssertionError("包装定价不许碰大模型会话（Spec §4.4）：%s" % name)

        backup = self.server.bridge
        self.server.bridge = Boom()
        try:
            result = self.call({"package": package_fixture(), "gross_margin_rate": MARGIN})
        finally:
            self.server.bridge = backup
        self.assertTrue(result.get("ok"), "无模型模式下包装定价必须成功：%r" % (result,))
        self.assertAlmostEqual(float(result["quote"]["untaxed_unit_price"]), PRICE_WORKRATE,
                               places=6)
        for key in ("s3_markup", "s4_markup", "s5_basic", "s5_detail"):
            self.assertIn(key, result.get("sections") or {},
                          "工作台四段分区必须一起返回（Spec §4.4）")
        self.assertTrue(str((result.get("document") or {}).get("markdown") or "").strip())

    def test_i2_non_packaging_is_not_taken_over(self):
        result = self.call({"package": package_fixture(industry="appliance"),
                            "gross_margin_rate": MARGIN})
        self.assertFalse(result.get("ok"), "非包装包不许被包装定价接管")
        self.assertTrue(str(result.get("error") or "").strip(), "失败要说明原因")

    def test_i3_errors_are_returned_not_raised(self):
        result = self.call({"package": package_fixture(), "gross_margin_rate": 1.0})
        self.assertFalse(result.get("ok"))
        self.assertTrue(str(result.get("error") or "").strip())

    def test_i4_route_is_dispatched(self):
        path = getattr(self.server, "PACKAGING_QUOTE_PRICE_PATH", None)
        self.assertTrue(path == "/api/packaging-quote/price",
                        "路由常量必须是 /api/packaging-quote/price（Spec §4.4），实际 %r" % (path,))
        source = inspect.getsource(self.server.Handler.do_POST)
        self.assertTrue("PACKAGING_QUOTE_PRICE_PATH" in source,
                        "do_POST 必须按常量派发该路径（Spec §4.4）")
        self.assertTrue("_handle_packaging_quote_price" in source,
                        "do_POST 必须把该路径派发到包装定价处理器（Spec §4.4）")

    def test_i5_three_industry_markup_path_untouched(self):
        handler = self.server.Handler
        self.assertTrue("/api/markup/fill" in inspect.getsource(handler.do_POST),
                        "三行业加价入口不许被改动（Spec §9）")
        self.assertTrue("_handle_markup_fill" in inspect.getsource(handler._handle_markup_stream),
                        "第 3/4 步仍走原来的模型加价实现（Spec §9）")
        self.assertTrue(sorted(self.server.MARKUP_STEPS) == [3, 4],
                        "第 3/4 步加价仍只服务三行业（Spec §9）")
        module = load_quote_module()
        self.assertIsNotNone(module, "缺少 cpq_packaging_quote.py（Spec §4.2）")
        signature = inspect.signature(module.price)
        self.assertTrue("gross_margin_rate" in signature.parameters and
                        "markup_rate" in signature.parameters,
                        "两种费率字段必须并存（Spec §1.2）")
        server_signature = inspect.signature(
            getattr(self.server, "_handle_packaging_quote_price", None) or (lambda: None))
        parameters = list(server_signature.parameters)
        self.assertTrue(parameters[:2] == ["data", "emit"] or parameters[:1] == ["data"],
                        "处理器签名见 Spec §4.4，实际 %r" % (parameters,))


# --------------------------------------------------------------------------- #
# J. 端到端衔接与四行业回归
# --------------------------------------------------------------------------- #
class JEndToEnd(HandoffCase):
    def test_j1_tech_package_can_be_priced(self):
        self.prepare()
        package = self.package()
        module = self.quote_mod()
        quote = module.price(package, gross_margin_rate=MARGIN)
        self.assertAlmostEqual(quote["cost_total"], package["cost"]["total_cost"], places=9,
                               msg="定价必须用第 7 批的成本总额，不许另算")
        self.assertAlmostEqual(quote["untaxed_unit_price"],
                               package["cost"]["total_cost"] / (1 - MARGIN), places=9)
        self.assertEqual(quote["quote_quantity"], package["requirement"]["quote_quantity"])

    def test_j2_three_industry_cost_model_unchanged(self):
        from tech_app.backend.services import cost_model
        self.assertEqual(cost_model.derive(100.0),
                         {"material": 100.0, "labor": 8.31, "overhead": 4.16,
                          "machining": 2.21, "total": 114.68,
                          "coefficients": {"labor": 0.083148, "overhead": 0.041574,
                                           "machining": 0.022073},
                          "constants": {"tax_divisor": 1.13, "material_share": 0.791,
                                        "labor_ratio": 0.3556, "overhead_ratio": 0.1778,
                                        "processing_ratio": 0.0944}},
                         "三行业的固定系数成本模型一个数都不许变（Spec §9）")

    def test_j3_industry_registry_unchanged(self):
        import cpq_industries
        self.assertEqual(cpq_industries.industry_keys(),
                         ("semiconductor", "battery", "appliance", "packaging"))
        for key in ("semiconductor", "battery", "appliance"):
            self.assertEqual(cpq_industries.profile_of(key)["pricing_profile"],
                             "generic_margin_v1",
                             "三行业定价档位不许被本批改动（Spec §9）")
        self.assertEqual(cpq_industries.profile_of("packaging")["pricing_profile"],
                         "packaging_margin_v1")

    def test_j4_batch7_cost_catalog_unchanged(self):
        from tech_app.backend.services import packaging_cost
        self.assertEqual(len(packaging_cost.COST_CATEGORIES), 24)
        self.assertEqual(packaging_cost.ENGINE_VERSION, "packaging_cost_v1")
        self.assertEqual(packaging_cost.DEFAULT_LOSS_BASE_SCOPE,
                         "material_process_and_labor")
        for key in ("print", "labor", "die_cutting"):
            self.assertIn(key, [code for code, _ in packaging_cost.COST_CATEGORIES])
        # 定价侧与成本侧的成本档位必须是同一个字符串（否则报价单溯源会对不上）
        self.assertEqual(self.quote_mod().COST_PROFILE, packaging_cost.COST_PROFILE)

    def test_j5_layering_is_respected(self):
        handoff = inspect.getsource(self.handoff_mod())
        quote = inspect.getsource(self.quote_mod())
        self.assertNotIn("import cpq_agent_server", handoff,
                         "技术侧不许 import 报价服务（Spec §2.1）")
        self.assertNotIn("from cpq_agent_server", handoff)
        self.assertNotIn("import tech_app", quote,
                         "报价侧不许 import 技术工艺（Spec §2.1）")
        self.assertNotIn("from tech_app", quote)

    def test_j6_package_is_json_safe(self):
        self.prepare()
        package = self.package()
        text = json.dumps(package, ensure_ascii=False, default=str)
        self.assertTrue(text)
        self.assertIn("packaging", text)


if __name__ == "__main__":
    unittest.main()

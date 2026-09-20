"""包装专用成本引擎 —— 包装第 7 批。

Spec：docs/specs/packaging-cost-engine.md（§2.3–§2.12 是硬合同）
红测：tests/test_packaging_cost_engine_red.py

分段职责：
  · `COST_CATEGORIES` / `PROJECT_COST_CATEGORIES` / `REPORT_GROUPS` / `LINE_VARIABLES` /
    `FORMULA_CATALOG` / `STEP_RATE_MAP` 是**纯声明**：24 个成本类别按 0903 `报价-工费率`
    的 S→AP 列序对齐，9 条工艺公式 + 11 条包材公式的表达式、最低收费、取整、费率码、
    `source_ref` 全部可追溯；
  · `compute_line` / `apply_loss` / `summarize` / `compute_tooling` / `compute_content` /
    `compute_packaging` / `parse_loading_rate` / `compute_freight` 是**确定性纯函数**：
    只读入参，不落库、不调模型、不联网；
  · `compute_project` 读第 4 批确认盒型 + 第 5 批 BOM + 第 6 批已确认路线 → 逐行算 →
    三层汇总（项目 / 部件 / 成本项），`build_cost` 落库并写项目审计，`load_cost` 读回。

口径要点：
  · profile 路由：只有 `packaging` 走本引擎，其它行业 → `CostError(400, "not_packaging")`；
    三个原行业继续走 `cost_model.py` 的 `generic_v1`，逐字不动；
  · 最低收费是一等概念：行金额 = `MAX(minimum_charge / quote_quantity, 表达式)`；
  · 缺料价 / 缺费率 / 缺克重 / 缺尺寸 / 缺损耗率 / 缺工时 / 缺装数 → 记缺口，**绝不按 0
    静默计入**，也绝不编造精确价格；
  · 损耗逐行：需求 `loss_rate` 优先，否则按材料的 `kb_cost_factor`（scrap）；包装与运输
    任何取值下都不参与损耗。

本批只出成本，**不出**售价 / 毛利率 / 报价单（第 8 批），不做拼版优化、不排产能。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from . import industry_templates, packaging_match
from . import packaging_formula
from ..storage import da_db, da_repo, kb_repo, store


# --------------------------------------------------------------------------- #
# 命名契约（Spec §4.5：红测与实现共用，不得改名）
# --------------------------------------------------------------------------- #
ENGINE_VERSION = "packaging_cost_v1"
COST_PROFILE = "packaging_v1"
GENERIC_PROFILE = "generic_v1"
PACKAGING_INDUSTRY = "packaging"

#: 0903 常量（工作簿可见 Sheet）：包材公式里的固定系数。
LOSS_UPLIFT = 1.03
YIELD_DIVISOR = 0.9
#: 0903 `报价-工费率!AU2` 的托盘分支分母里的整车托数（工作簿里是字面量 12）。
PALLETS_PER_TRUCK = 12.0

#: 24 个成本类别（Spec §2.3，按 0903 `报价-工费率` 的 S→AP 列序逐条对齐）。
COST_CATEGORIES = (
    ("material", "材料价"), ("print", "普通印刷"), ("print_uv", "UV印刷"),
    ("lamination", "复膜"), ("transfer_film", "覆转移膜"), ("hot_stamp_flat", "热烫-平压"),
    ("hot_stamp_round", "热烫-圆压"), ("cold_stamp", "冷烫"), ("silk_screen", "丝印"),
    ("varnish", "过光油"), ("anti_scratch", "防刮花光/哑油"), ("pet_oil", "PET环保吸塑油"),
    ("visidi_uv", "视高迪UV"), ("texture", "压纹"), ("emboss_deboss", "击凹/凸"),
    ("mounting", "裱纸"), ("die_cutting", "啤/切"), ("folding", "折页/装钉"),
    ("v_groove", "V槽"), ("auto_mount", "机贴盒/贴双面胶"), ("double_tape", "双面胶"),
    ("glue", "胶水"), ("labor", "人工/全检/包装"), ("other", "其他"),
)

#: 项目级类别（不计入任何部件行，Spec §2.3）。
PROJECT_COST_CATEGORIES = (("packaging", "包装"), ("freight", "运输"))

#: 10 个报告分组（Spec §2.3，对应 `成本细分` Sheet 的 10 列）。
REPORT_GROUPS = {
    "材料": ("material", "glue"), "印刷": ("print", "print_uv"), "覆膜": ("lamination",),
    "烫金": ("hot_stamp_flat", "hot_stamp_round", "cold_stamp"), "丝印": ("silk_screen",),
    "裱纸": ("mounting",), "模切": ("die_cutting",), "开槽": ("v_groove",),
    "手工": ("labor",), "包装": ("packaging", "freight"),
}

#: 变量白名单闭集（Spec §2.4）。表达式引用白名单外变量 → `CostError(409, unknown_variable:)`。
LINE_VARIABLES = {
    "cut_length": "部件展开长", "cut_width": "部件展开宽 + 5mm 咬口",
    "machine_length": "上机长（默认 = 开料长）", "machine_width": "上机宽（默认 = 开料宽 − 5）",
    "imposition_count": "模数（默认 1）", "gsm": "克重（物料属性或 grade 解析）",
    "ton_price": "材料单价（元/kg）× 1000", "front_colors": "正面色彩数",
    "back_colors": "背面色彩数", "proof_base": "校版基数（默认 0）",
    "quote_quantity": "报价数量", "tax_factor": "1 + 增值税率",
    "setup_minutes": "换版分钟", "capacity_per_hour": "每小时产能",
    "equipment_rate": "设备费率", "labor_rate": "人工费率",
    "times": "加工次数", "film_price": "覆膜材料单价（元/㎡）",
    "film_thickness_um": "膜厚（µm）", "film_kg_price": "膜重价（元/kg）",
    "hot_area_mm2": "烫金面积（mm²）", "foil_price": "箔价（元/㎡）",
    "glue_unit_price": "胶水单价（元/㎡）", "length_mm": "包材长（mm）",
    "width_mm": "包材宽（mm）", "height_mm": "包材高（mm）",
    "usage_qty": "包材用量", "material_price": "包材材料单价",
    "units_per_pack": "包材装数", "loss_uplift": "损耗上浮（0903 = 1.03）",
    "yield_divisor": "良率除数（0903 = 0.9）", "ink_thickness_mm": "墨层厚度（µm）",
    "ink_unit_price": "油墨单价（元/kg）", "labor_seconds": "工序标准工时（秒）",
    "overhead_seconds": "制费工时（秒，本批 0）", "overhead_rate": "制费费率",
    "tooling_cost": "工装成本", "tooling_lifetime": "模具寿命",
    "committed_volume": "项目承诺总量", "amortized_quantity": "本单分摊量",
    "refund_threshold": "返还门槛", "min_freight": "最低运费",
    "pallet_freight": "托盘/整车单托运费", "units_per_pallet": "每托装数",
    "loading_rate": "装载率（文本）",
}

#: 工装涉及工序（与第 5 批 `TOOLING_KEYWORDS` 同源）。
TOOLING_PROCESSES = ("烫金", "丝印", "击凹凸", "模切", "装配线")

#: 工装五种分摊模式闭集（Spec §2.9）。
TOOLING_MODES = ("one_off", "lifetime", "committed", "refund", "customer_supplied")

#: 损耗基数范围闭集（Spec §2.8）。
LOSS_BASE_SCOPES = ("material_only", "material_process", "material_process_and_labor",
                    "material_process_labor_packaging")
DEFAULT_LOSS_BASE_SCOPE = "material_process_and_labor"

#: 工序 → 工时费率码（Spec §2.6.2）。
STEP_RATE_MAP = {
    "手裱": "RATE-PKG-LABOR-HANDMOUNT", "机裱": "RATE-PKG-LABOR-LAMINATE",
    "组装": "RATE-PKG-LABOR-ASSEMBLY", "检验": "RATE-PKG-LABOR-ASSEMBLY",
    "清洁包装": "RATE-PKG-LABOR-ASSEMBLY",
}

#: 生成/重算写权限：**直接引用**第 4 批的角色常量（同一对象，不另抄一份）。
COST_WRITE_ROLES = packaging_match.BOX_MATCH_DECIDE_ROLES

#: 04 `PKG-C-*` 的表达式正文（DSL 白名单内；小数常量写成整数除法，见模块头注释）。
_MATERIAL_EXPR = ("cut_length*cut_width/1000000*gsm/1000000*ton_price/tax_factor/"
                  "imposition_count + cut_length*cut_width/1000000*gsm/1000000*ton_price/"
                  "tax_factor*proof_base/quote_quantity")
_PRINT_UV_EXPR = ("(setup_minutes/60 + quote_quantity/imposition_count/capacity_per_hour)*"
                  "(equipment_rate+labor_rate)/quote_quantity + machine_length*machine_width/"
                  "1000000*ink_thickness_mm/1000*ink_unit_price/tax_factor/imposition_count")
_LAMINATION_EXPR = ("machine_length*machine_width/1000000*film_price/tax_factor/"
                    "imposition_count + machine_length*machine_width/1000000*film_thickness_um/"
                    "1000*film_kg_price/imposition_count + (setup_minutes/60 + quote_quantity/"
                    "imposition_count/capacity_per_hour)*(equipment_rate+labor_rate)/quote_quantity")
_HOT_STAMP_FLAT_EXPR = ("hot_area_mm2/1000000*foil_price + (setup_minutes/60 + quote_quantity/"
                        "imposition_count/capacity_per_hour)*(equipment_rate+labor_rate)/quote_quantity")
_MOUNTING_EXPR = ("(setup_minutes/60 + quote_quantity/imposition_count/capacity_per_hour)*"
                  "(equipment_rate+labor_rate)/quote_quantity")
_DIE_CUT_EXPR = _MOUNTING_EXPR
_V_GROOVE_EXPR = ("(setup_minutes/60 + quote_quantity/capacity_per_hour)*"
                  "(equipment_rate+labor_rate)/quote_quantity*times")
_GLUE_EXPR = "machine_length*machine_width/1000000*glue_unit_price/imposition_count"
_LABOR_EXPR = "labor_seconds/3600*labor_rate + overhead_seconds/3600*overhead_rate"

_CARTON_EXPR = ("((width_mm+height_mm+254/10)*(length_mm+width_mm+508/10)*2*material_price/"
                "tax_factor/645160 + 1/10+6/100+12/100)*loss_uplift/yield_divisor*usage_qty/"
                "units_per_pack")
_PAD_EXPR = ("((length_mm+6)*(width_mm+6)*material_price/tax_factor/645160 + 4/100)*"
             "loss_uplift/yield_divisor*usage_qty/units_per_pack")
_DIVIDER_EXPR = ("(length_mm+6)*(width_mm+6)/1000000*gsm/1000000*material_price/tax_factor*"
                 "usage_qty/units_per_pack")
_BAG_EXPR = ("(length_mm+50)*(width_mm+50)*height_mm/100/1000*bag_unit_price*material_price*"
             "usage_qty/units_per_pack")
_CRAFT_PAPER_EXPR = _DIVIDER_EXPR
_STRAP_EXPR = _DIVIDER_EXPR
_CORNER_TOP_EXPR = "length_mm/1000*material_price*usage_qty/units_per_pack"
_CORNER_PAPER_EXPR = _CORNER_TOP_EXPR
_LABEL_EXPR = "material_price*usage_qty/units_per_pack"
_BOARD_EXPR = "material_price/tax_factor*usage_qty/units_per_pack"
_PALLET_EXPR = "material_price/tax_factor*usage_qty/units_per_pack"

_GSTAMP = "报价逻辑-0903.xlsx/报价-工费率/"
_GPACK = "报价逻辑-0903.xlsx/包装运输/"

#: 公式目录（Spec §2.6 / §2.10）。`defaults` 是 0903 同单元格的费率与默认参数。
FORMULA_CATALOG = {
    "PKG-C-MATERIAL": {
        "formula_code": "PKG-C-MATERIAL", "cost_category": "material",
        "expression": _MATERIAL_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "", "source_ref": _GSTAMP + "S2", "defaults": {}},
    "PKG-C-PRINT-UV": {
        "formula_code": "PKG-C-PRINT-UV", "cost_category": "print_uv",
        "expression": _PRINT_UV_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "RATE-PKG-EQUIP-PRINT", "source_ref": _GSTAMP + "U2",
        "defaults": {"setup_minutes": 30.0, "capacity_per_hour": 12000.0,
                     "equipment_rate": 591.0, "labor_rate": 666.0,
                     "ink_thickness_mm": 4.0, "ink_unit_price": 115.0}},
    "PKG-C-LAMINATION": {
        "formula_code": "PKG-C-LAMINATION", "cost_category": "lamination",
        "expression": _LAMINATION_EXPR, "minimum_charge": 200, "rounding": 4,
        "rate_code": "RATE-PKG-EQUIP-SURFACE", "source_ref": _GSTAMP + "V2",
        "defaults": {"setup_minutes": 30.0, "capacity_per_hour": 5500.0,
                     "equipment_rate": 197.0, "labor_rate": 145.0, "film_price": 1.7,
                     "film_thickness_um": 18.0, "film_kg_price": 18.5}},
    "PKG-C-HOT-STAMP-FLAT": {
        "formula_code": "PKG-C-HOT-STAMP-FLAT", "cost_category": "hot_stamp_flat",
        "expression": _HOT_STAMP_FLAT_EXPR, "minimum_charge": 150, "rounding": 4,
        "rate_code": "RATE-PKG-EQUIP-SURFACE", "source_ref": _GSTAMP + "X2",
        "defaults": {"setup_minutes": 200.0, "capacity_per_hour": 5000.0,
                     "equipment_rate": 193.0, "labor_rate": 115.0, "foil_price": 8.5}},
    "PKG-C-MOUNTING": {
        "formula_code": "PKG-C-MOUNTING", "cost_category": "mounting",
        "expression": _MOUNTING_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "RATE-PKG-LABOR-LAMINATE", "source_ref": _GSTAMP + "AH13",
        "defaults": {"setup_minutes": 30.0, "capacity_per_hour": 3500.0,
                     "equipment_rate": 209.0, "labor_rate": 126.0}},
    "PKG-C-DIE-CUT": {
        "formula_code": "PKG-C-DIE-CUT", "cost_category": "die_cutting",
        "expression": _DIE_CUT_EXPR, "minimum_charge": 100, "rounding": 4,
        "rate_code": "RATE-PKG-EQUIP-MOULD", "source_ref": _GSTAMP + "AI2",
        "defaults": {"setup_minutes": 120.0, "capacity_per_hour": 6500.0,
                     "equipment_rate": 197.52, "labor_rate": 190.06}},
    "PKG-C-V-GROOVE": {
        "formula_code": "PKG-C-V-GROOVE", "cost_category": "v_groove",
        "expression": _V_GROOVE_EXPR, "minimum_charge": 120, "rounding": 4,
        "rate_code": "RATE-PKG-EQUIP-VGROOVE", "source_ref": _GSTAMP + "AK5",
        "defaults": {"setup_minutes": 60.0, "capacity_per_hour": 3000.0,
                     "equipment_rate": 195.0, "labor_rate": 111.0, "times": 2.0}},
    "PKG-C-GLUE": {
        "formula_code": "PKG-C-GLUE", "cost_category": "glue",
        "expression": _GLUE_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "", "source_ref": _GSTAMP + "AN2",
        "defaults": {"glue_unit_price": 0.74}},
    "PKG-C-LABOR": {
        "formula_code": "PKG-C-LABOR", "cost_category": "labor",
        "expression": _LABOR_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "", "source_ref": "报价逻辑-0903.xlsx/报价-工费率/AO（按 Spec §2.6.2 改为标准工时 × 费率）",
        "defaults": {"overhead_seconds": 0.0, "overhead_rate": 0.0}},
    "PKG-P-CARTON": {
        "formula_code": "PKG-P-CARTON", "cost_category": "packaging",
        "expression": _CARTON_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "", "source_ref": _GPACK + "J2", "defaults": {}},
    "PKG-P-PAD": {
        "formula_code": "PKG-P-PAD", "cost_category": "packaging",
        "expression": _PAD_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "", "source_ref": _GPACK + "J3", "defaults": {}},
    "PKG-P-DIVIDER": {
        "formula_code": "PKG-P-DIVIDER", "cost_category": "packaging",
        "expression": _DIVIDER_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "", "source_ref": _GPACK + "J4", "defaults": {}},
    "PKG-P-BAG": {
        "formula_code": "PKG-P-BAG", "cost_category": "packaging",
        "expression": _BAG_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "", "source_ref": _GPACK + "J5",
        "defaults": {"bag_unit_price": 0.185}},
    "PKG-P-CRAFT-PAPER": {
        "formula_code": "PKG-P-CRAFT-PAPER", "cost_category": "packaging",
        "expression": _CRAFT_PAPER_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "", "source_ref": _GPACK + "J6", "defaults": {}},
    "PKG-P-STRAP": {
        "formula_code": "PKG-P-STRAP", "cost_category": "packaging",
        "expression": _STRAP_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "", "source_ref": _GPACK + "J7", "defaults": {}},
    "PKG-P-CORNER-TOP": {
        "formula_code": "PKG-P-CORNER-TOP", "cost_category": "packaging",
        "expression": _CORNER_TOP_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "", "source_ref": _GPACK + "J8", "defaults": {}},
    "PKG-P-CORNER-PAPER": {
        "formula_code": "PKG-P-CORNER-PAPER", "cost_category": "packaging",
        "expression": _CORNER_PAPER_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "", "source_ref": _GPACK + "J9", "defaults": {}},
    "PKG-P-LABEL": {
        "formula_code": "PKG-P-LABEL", "cost_category": "packaging",
        "expression": _LABEL_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "", "source_ref": _GPACK + "J10", "defaults": {}},
    "PKG-P-BOARD": {
        "formula_code": "PKG-P-BOARD", "cost_category": "packaging",
        "expression": _BOARD_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "", "source_ref": _GPACK + "J11", "defaults": {}},
    "PKG-P-PALLET": {
        "formula_code": "PKG-P-PALLET", "cost_category": "packaging",
        "expression": _PALLET_EXPR, "minimum_charge": 0, "rounding": 4,
        "rate_code": "", "source_ref": _GPACK + "J12", "defaults": {}},
}

#: 求值精度：Spec §2.6 说「precision 取本条 rounding」，但红测要求金额与 0903 缓存的
#: 全精度值对齐到 1e-6；取整只写进目录元数据（`rounding`），金额本身不做二次取整。
_EVAL_PRECISION = 12

#: 变量白名单之外允许出现在包材公式里的常量参数（Spec §2.10 的胶袋单价）。
_PACKAGING_EXTRA_VARIABLES = frozenset({"bag_unit_price"})

#: 公式目录里没有、但计算时必须有的隐式默认值（Spec §2.4/§2.5，缺省记 assumptions）。
_IMPLICIT_DEFAULTS = {"imposition_count": 1.0, "proof_base": 0.0, "front_colors": 0.0,
                      "back_colors": 0.0, "overhead_seconds": 0.0, "overhead_rate": 0.0}

#: BOM `process` 行 / 第 6 批工序名 → 成本类别。
STEP_CATEGORY_MAP = {
    "面纸印刷": "print", "覆膜": "lamination", "烫金": "hot_stamp_flat",
    "丝印": "silk_screen", "UV 上光": "varnish", "UV上光": "varnish",
    "压凹凸": "emboss_deboss", "面纸模切": "die_cutting",
    "V 槽开槽": "v_groove", "V槽开槽": "v_groove", "V槽": "v_groove",
    "机裱": "mounting", "手裱": "mounting",
}

#: 需求字段 → 表面工艺类别（判定"这个类别这次要不要算"）。
STEP_REQUIREMENT_FIELDS = {
    "print": ("print_colors", "spot_colors"), "lamination": ("lamination",),
    "hot_stamp_flat": ("hot_stamping",), "silk_screen": ("silk_screen",),
    "varnish": ("uv_coating",), "emboss_deboss": ("emboss_deboss",),
    "die_cutting": ("die_cutting",), "v_groove": ("v_groove",),
    "mounting": ("mounting",),
}

_NEGATIVE_VALUES = frozenset({
    "", "否", "无", "不需要", "不要", "没有", "不需", "none", "n", "no", "false", "0", "—", "-",
})

_FUNCTIONS = frozenset({"MIN", "MAX", "IF", "IFERROR", "ROUND"})
_NAME_PATTERN = r"[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*"


class CostError(Exception):
    """包装成本业务错误；`status_code` 与 `code` 供接口层原样映射。"""

    def __init__(self, message: str, status_code: int = 409, code: str = ""):
        super().__init__(message)
        self.message = str(message)
        self.status_code = int(status_code)
        self.code = str(code)


# --------------------------------------------------------------------------- #
# 基础取值（缺字段、空串、脏值都不抛异常）
# --------------------------------------------------------------------------- #
def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed is not None else default


def _industry_of(data: dict) -> str:
    return industry_templates.normalize(
        (data or {}).get("industry") or (data or {}).get("industry_selection"))


def _requirement_data(project_id: str) -> dict:
    doc = store.load_requirement(project_id) or {}
    data = doc.get("data")
    return dict(data) if isinstance(data, dict) else {}


def _resolve_requirement_no(project_id: str, requirement_no: str) -> str:
    explicit = _text(requirement_no)
    if explicit:
        return explicit
    doc = store.load_requirement(project_id) or {}
    return _text(doc.get("requirement_no"))


def _actor_name(actor: Any) -> str:
    if isinstance(actor, dict):
        return _text(actor.get("username") or actor.get("name") or actor.get("actor"))
    return _text(actor)


def is_required(value: Any) -> bool:
    """需求真值判定（与第 6 批同一口径）。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) > 0
    return _text(value).lower() not in _NEGATIVE_VALUES


def profile_for(industry: Any) -> str:
    """profile 路由（Spec §2.2）：只有 packaging 走包装引擎，其余走 generic_v1。"""
    return COST_PROFILE if _text(industry) == PACKAGING_INDUSTRY else GENERIC_PROFILE


# --------------------------------------------------------------------------- #
# 表达式工具
# --------------------------------------------------------------------------- #
def _canon(text: Any) -> str:
    """DSL 只接受 alnum，变量名的下划线统一去掉后再求值。"""
    return re.sub(r"_", "", "" if text is None else str(text))


def expression_variables(expression: Any) -> list:
    """按出现顺序收集表达式引用的变量名（函数名、数字不算）。"""
    names: list = []
    for match in re.finditer(_NAME_PATTERN, "" if expression is None else str(expression)):
        name = match.group(0)
        if name.upper() in _FUNCTIONS:
            continue
        if name not in names:
            names.append(name)
    return names


def _formula_value(expression: str, variables: dict, precision: int = _EVAL_PRECISION) -> float:
    """受控求值：表达式与变量名都去掉下划线后再交给 packaging_formula。"""
    expr = _canon(expression)
    bound = {_canon(key): value for key, value in (variables or {}).items()}
    return packaging_formula.evaluate(expr, bound, precision=precision)


def _entry_for(category: str, formula_code: Optional[str] = None) -> Optional[dict]:
    code = _text(formula_code)
    if code:
        return dict(FORMULA_CATALOG[code]) if code in FORMULA_CATALOG else None
    for entry in FORMULA_CATALOG.values():
        if entry["cost_category"] == category:
            return dict(entry)
    return None


def resolve_formula(formula_code: str, *, rows=None) -> dict:
    """取公式：`kb_packaging_cost_formula` 里 `reviewed` 的行覆盖内置目录（Spec §4.6）。

    · `review_status != 'reviewed'`（含第 3 批 7 条中文散文 draft）→ 一律忽略；
    · `reviewed` 行表达式解析失败 → `CostError(409, invalid_formula:<code>)`，**fail closed**，
      绝不静默回退到内置目录。
    """
    code = _text(formula_code)
    if code not in FORMULA_CATALOG:
        raise CostError("没有内置公式 %s" % code, 404, "unknown_formula:%s" % code)
    base = dict(FORMULA_CATALOG[code])
    base["defaults"] = dict(FORMULA_CATALOG[code].get("defaults") or {})
    if rows is None:
        rows = kb_repo._table("kb_packaging_cost_formula")
    candidates = [dict(row) for row in (rows or [])
                  if _text(row.get("formula_code")) == code
                  and _text(row.get("review_status")) == "reviewed"]
    if not candidates:
        return base
    candidates.sort(key=lambda row: _text(row.get("effective_from")))
    row = candidates[-1]
    expression = _text(row.get("expression"))
    known = set(LINE_VARIABLES) | _PACKAGING_EXTRA_VARIABLES
    probes = {name: 1.0 for name in expression_variables(expression) if name in known}
    try:
        if not expression:
            raise packaging_formula.FormulaError("表达式为空")
        _formula_value(expression, probes)
    except packaging_formula.FormulaError as exc:
        raise CostError("reviewed 公式 %s 解析失败：%s" % (code, exc), 409,
                        "invalid_formula:%s" % code) from exc
    base["expression"] = expression
    if row.get("minimum_charge") is not None:
        base["minimum_charge"] = _num(row.get("minimum_charge")) or 0
    if row.get("rounding") is not None:
        base["rounding"] = int(_num(row.get("rounding")) or base.get("rounding") or 4)
    base["source"] = "kb"
    base["note"] = "source=kb:%s" % code
    return base


# --------------------------------------------------------------------------- #
# 单行计算
# --------------------------------------------------------------------------- #
def _merge_variables(entry: dict, variables: dict) -> tuple:
    merged: dict = {}
    assumptions: list = []
    defaults = dict(entry.get("defaults") or {})
    for name, value in defaults.items():
        merged[name] = value
        if name != "times":
            assumptions.append("%s=0903=%s" % (name, value))
    for name, value in _IMPLICIT_DEFAULTS.items():
        merged.setdefault(name, value)
        assumptions.append("%s=默认=%s" % (name, value))
    for name, value in (variables or {}).items():
        if value is None:
            continue
        merged[name] = value
    return merged, assumptions


def compute_line(category: str, variables: Optional[dict] = None, *,
                 formula_code: Optional[str] = None, amount: Any = None) -> dict:
    """算一行成本（Spec §4.5 / §2.6 / §2.7）。

    · 显式给 `amount` → 直接采用、`source="human"`、不取整；
    · 公式路径 → `MAX(minimum_charge/quote_quantity, 表达式)`，命中最低收费时
      `min_charge_applied=true`；
    · 变量缺 → `amount=None` + `gap.code = missing_variable:<名>`；白名单外变量 →
      `CostError(409, unknown_variable:<名>)`；类别没有公式 → `no_formula:<code>`。
    """
    inputs = dict(variables or {})
    if amount is not None:
        return {"cost_category": category, "formula_code": _text(formula_code),
                "expression": "", "inputs": inputs, "amount": float(amount),
                "min_charge_applied": False, "minimum_charge": 0.0, "source": "human",
                "loss_rate": inputs.get("loss_rate"), "assumptions": []}
    entry = _entry_for(category, formula_code)
    if entry is None:
        code = _text(formula_code) or category
        return {"cost_category": category, "formula_code": code, "expression": "",
                "inputs": inputs, "amount": None, "min_charge_applied": False,
                "minimum_charge": 0.0, "source": "kb", "assumptions": [],
                "gap": {"code": "no_formula:%s" % code, "where": category,
                        "detail": "该类别没有公式，需要人工录入金额"}}
    expression = entry["expression"]
    names = expression_variables(expression)
    allowed = set(LINE_VARIABLES)
    if entry["cost_category"] == "packaging":
        allowed |= _PACKAGING_EXTRA_VARIABLES
    unknown = [name for name in names if name not in allowed]
    if unknown:
        raise CostError("表达式引用了白名单外变量：%s" % "、".join(unknown), 409,
                        "unknown_variable:%s" % unknown[0])
    merged, assumptions = _merge_variables(entry, inputs)
    bound = {_canon(key) for key in merged}
    missing = [name for name in names if _canon(name) not in bound]
    if missing:
        return {"cost_category": category, "formula_code": entry["formula_code"],
                "expression": expression, "inputs": merged, "amount": None,
                "min_charge_applied": False,
                "minimum_charge": float(entry.get("minimum_charge") or 0.0),
                "source": "formula", "assumptions": assumptions,
                "gap": {"code": "missing_variable:%s" % missing[0], "where": category,
                        "detail": "缺少输入变量：%s" % "、".join(missing)}}
    try:
        value = _formula_value(expression, merged)
    except packaging_formula.FormulaError as exc:
        return {"cost_category": category, "formula_code": entry["formula_code"],
                "expression": expression, "inputs": merged, "amount": None,
                "min_charge_applied": False,
                "minimum_charge": float(entry.get("minimum_charge") or 0.0),
                "source": "formula", "assumptions": assumptions,
                "gap": {"code": "formula_error:%s" % entry["formula_code"], "where": category,
                        "detail": str(exc)}}
    quantity = _num(merged.get("quote_quantity"))
    minimum_charge = float(entry.get("minimum_charge") or 0.0)
    threshold = (minimum_charge / quantity) if (minimum_charge and quantity
                                                and quantity > 0) else 0.0
    applied = bool(minimum_charge and threshold > value)
    return {"cost_category": category, "formula_code": entry["formula_code"],
            "expression": expression, "inputs": merged,
            "amount": threshold if applied else value, "min_charge_applied": applied,
            "minimum_charge": minimum_charge, "expression_value": value,
            "source": "formula", "assumptions": assumptions}


# --------------------------------------------------------------------------- #
# 损耗
# --------------------------------------------------------------------------- #
def loss_base_categories(scope: Any) -> tuple:
    """给定损耗基数范围里**参与损耗的部件级类别**（Spec §2.8）。

    包装 / 运输是项目级类别，永不出现在这里（红测 d2 逐 scope 断言）。
    """
    name = _text(scope) or DEFAULT_LOSS_BASE_SCOPE
    codes = [code for code, _ in COST_CATEGORIES]
    if name == "material_only":
        return ("material",)
    if name == "material_process":
        return tuple(code for code in codes if code not in ("labor", "other"))
    return tuple(codes)


def apply_loss(amount: Any, loss_rate: Any, *, category: str,
               loss_base_scope: Any = DEFAULT_LOSS_BASE_SCOPE) -> Optional[float]:
    """单行损耗：类别不在范围内或没有损耗率时原样返回。"""
    if amount is None:
        return None
    value = float(amount)
    rate = _num(loss_rate)
    if rate is None or category not in loss_base_categories(loss_base_scope):
        return value
    return value * (1.0 + rate)


def default_loss_rate(material_text: Any, *, rows=None) -> Optional[float]:
    """按材料/类别取 `factor_type='scrap'` 的损耗率；取不到返回 None（不许默认 0）。"""
    text = _text(material_text)
    source = rows if rows is not None else kb_repo._table("kb_cost_factor")
    factors = {_text(row.get("factor_code")): row for row in (source or [])
               if _text(row.get("factor_type")) == "scrap"}
    if "灰板" in text:
        hit = factors.get("F-PKG-LOSS-GREYBOARD")
        return _num(hit.get("value")) if hit else None
    for keyword in ("面纸", "衬纸", "特种纸", "铜版", "纸"):
        if keyword in text:
            hit = factors.get("F-PKG-LOSS-PAPER")
            return _num(hit.get("value")) if hit else None
    return None


# --------------------------------------------------------------------------- #
# 三层汇总
# --------------------------------------------------------------------------- #
def summarize(lines, *, packaging: Any = 0.0, freight: Any = 0.0, tooling=None,
              loss_base_scope: Any = DEFAULT_LOSS_BASE_SCOPE) -> dict:
    """纯函数汇总（Spec §2.12）：部件行 → 24 类别 → 10 报告分组 → 项目总成本。"""
    scope = _text(loss_base_scope) or DEFAULT_LOSS_BASE_SCOPE
    buckets = {code: 0.0 for code, _ in COST_CATEGORIES}
    out: list = []
    subtotal = 0.0
    loss_amount = 0.0
    for line in (lines or []):
        row = dict(line)
        value = float(row.get("amount") or 0.0)
        with_loss = apply_loss(value, row.get("loss_rate"), category=row.get("cost_category"),
                               loss_base_scope=scope)
        row["amount_with_loss"] = with_loss
        out.append(row)
        subtotal += with_loss
        loss_amount += with_loss - value
        category = row.get("cost_category")
        if category in buckets:
            buckets[category] += with_loss
    tooling_total = sum(float(item.get("amount") or 0.0) for item in (tooling or []))
    project = {"packaging": float(packaging or 0.0), "freight": float(freight or 0.0)}
    groups: dict = {}
    for name, members in REPORT_GROUPS.items():
        groups[name] = sum(buckets.get(member, 0.0) + project.get(member, 0.0)
                           for member in members)
    total_cost = subtotal + tooling_total + project["packaging"] + project["freight"]
    return {"lines": out, "subtotal": subtotal, "loss_amount": loss_amount,
            "tooling_total": tooling_total, "packaging_total": project["packaging"],
            "freight_total": project["freight"], "categories": buckets,
            "report_groups": groups, "total_cost": total_cost}


# --------------------------------------------------------------------------- #
# 工装 / 刀模
# --------------------------------------------------------------------------- #
def compute_tooling(rule: Optional[dict], *, quote_quantity: Any,
                    amortized_quantity: Any = None, committed_volume: Any = None,
                    cumulative_quantity: Any = None) -> dict:
    """工装五种分摊模式（Spec §2.9）；缺基准时 `amount=None` + `gap`，不摊进部件行。"""
    row = dict(rule or {})
    code = _text(row.get("tooling_code"))
    mode = _text(row.get("mode"))
    cost = _num(row.get("tooling_cost"))
    out = {"cost_category": "other", "part_code": None, "part_name": _text(row.get("name")),
           "tooling_code": code, "formula_code": "", "expression": "", "rate_code": "",
           "inputs": {"quote_quantity": _num(quote_quantity), "mode": mode,
                      "tooling_cost": cost, "amortized_quantity": _num(amortized_quantity),
                      "committed_volume": _num(committed_volume),
                      "cumulative_quantity": _num(cumulative_quantity)},
           "min_charge_applied": False, "loss_rate": None, "assumptions": [mode],
           "source": "kb"}
    if mode == "customer_supplied":
        out.update({"amount": 0.0, "quantity_basis": "客户自备",
                    "note": "客户自备模具，只留痕不进成本"})
        return out
    if mode == "lifetime":
        basis, basis_label = _num(row.get("tooling_lifetime")), "按寿命"
    elif mode == "committed":
        basis, basis_label = _num(committed_volume), "按承诺量"
    elif mode in ("one_off", "refund"):
        basis, basis_label = _num(amortized_quantity), "按本单分摊量"
    else:
        basis, basis_label = None, ""
    out["quantity_basis"] = basis_label
    if cost is None or basis is None or basis <= 0:
        out["amount"] = None
        out["gap"] = {"code": "tooling_basis_missing:%s" % code, "where": code,
                      "detail": "工装寿命/分摊量缺失或 ≤ 0，不许按 0 或 1 顶替"}
        return out
    out["amount"] = cost / basis
    if mode == "refund":
        threshold = _num(row.get("refund_threshold"))
        reached = bool(row.get("refundable")) and threshold is not None \
            and _num(cumulative_quantity) is not None \
            and _num(cumulative_quantity) >= threshold
        out["refund_status"] = "refundable" if reached else "chargeable"
        out["note"] = "返还只改状态，金额冲减由报价侧（第 8 批）处理"
    return out


# --------------------------------------------------------------------------- #
# 包材与运输
# --------------------------------------------------------------------------- #
def compute_content(formula_code: str, variables: Optional[dict] = None) -> dict:
    """单条包材单件成本（Spec §2.10）：套 `PKG-P-*` 表达式（表达式内已 ÷ 装数）。"""
    code = _text(formula_code)
    inputs = dict(variables or {})
    entry = FORMULA_CATALOG.get(code)
    if entry is None or entry.get("cost_category") != "packaging":
        return {"formula_code": code, "content_code": _text(inputs.get("content_code")),
                "expression": "", "inputs": inputs, "amount": None,
                "min_charge_applied": False, "source": "kb",
                "gap": {"code": "no_formula:%s" % code, "where": code,
                        "detail": "不是包材公式"}}
    units = _num(inputs.get("units_per_pack"))
    if not units or units <= 0:
        return {"formula_code": code, "content_code": _text(inputs.get("content_code")),
                "expression": entry["expression"], "inputs": inputs, "amount": None,
                "min_charge_applied": False, "source": "formula",
                "gap": {"code": "invalid_units_per_pack", "where": code,
                        "detail": "装数为 0 或空，该包材行不计入"}}
    merged, assumptions = _merge_variables(entry, inputs)
    try:
        value = _formula_value(entry["expression"], merged)
    except packaging_formula.FormulaError as exc:
        return {"formula_code": code, "content_code": _text(inputs.get("content_code")),
                "expression": entry["expression"], "inputs": merged, "amount": None,
                "min_charge_applied": False, "source": "formula", "assumptions": assumptions,
                "gap": {"code": "content_formula_error:%s" % code, "where": code,
                        "detail": str(exc)}}
    return {"formula_code": code, "content_code": _text(inputs.get("content_code")),
            "expression": entry["expression"], "inputs": merged, "amount": value,
            "minimum_charge": 0.0, "min_charge_applied": False, "source": "formula",
            "assumptions": assumptions}


def compute_packaging(rows, *, tax_factor: Any = 1.13, loss_uplift: Any = LOSS_UPLIFT,
                      yield_divisor: Any = YIELD_DIVISOR) -> dict:
    """包材合计：逐条 `compute_content` 求和（Spec §2.10）。"""
    lines: list = []
    total = 0.0
    for row in (rows or []):
        variables = dict(row)
        variables["tax_factor"] = tax_factor
        variables["loss_uplift"] = loss_uplift
        variables["yield_divisor"] = yield_divisor
        result = compute_content(row.get("formula_code"), variables)
        if not result.get("content_code"):
            result["content_code"] = _text(row.get("content_code"))
        lines.append(result)
        if result.get("amount") is not None:
            total += result["amount"]
    return {"amount": total, "lines": lines}


def parse_loading_rate(text: Any) -> Optional[float]:
    """`≥85%` → 0.85；`92%` → 0.92；`不适用` / 解析失败 → None（Spec §2.10）。"""
    raw = _text(text)
    if not raw or "不适用" in raw:
        return None
    match = re.search(r"\d+(?:\.\d+)?", raw)
    if not match:
        return None
    value = float(match.group(0))
    if "%" in raw:
        value = value / 100.0
    return value if value > 0 else None


def compute_freight(rule: Optional[dict], *, quote_quantity: Any) -> dict:
    """运输 = `MAX(min_freight/数量, pallet_freight/整车托数/每托装数/装载率)`（Spec §2.10）。"""
    row = dict(rule or {})
    code = _text(row.get("rule_code"))
    quantity = _num(quote_quantity)
    min_freight = _num(row.get("min_freight"))
    pallet_freight = _num(row.get("pallet_freight"))
    units_per_pallet = _num(row.get("units_per_pallet"))
    loading = parse_loading_rate(row.get("loading_rate"))
    assumptions: list = []
    out = {"cost_category": "freight", "part_code": None, "part_name": _text(row.get("name")),
           "formula_code": "", "expression": "MAX(min_freight/quote_quantity, "
                                             "pallet_freight/pallets_per_truck/units_per_pallet/loading_rate)",
           "rate_code": "", "source": "kb", "min_charge_applied": False, "loss_rate": None,
           "quantity_basis": "按件", "inputs": {"min_freight": min_freight,
                                                "pallet_freight": pallet_freight,
                                                "units_per_pallet": units_per_pallet,
                                                "loading_rate": _text(row.get("loading_rate")),
                                                "quote_quantity": quantity}}
    if not quantity or quantity <= 0:
        out["amount"] = None
        out["gap"] = {"code": "quantity_missing", "where": code,
                      "detail": "数量缺失或 ≤ 0，运输无法按件摊"}
        out["assumptions"] = assumptions
        return out
    branches: list = []
    if min_freight is not None:
        branches.append(min_freight / quantity)
    if pallet_freight is not None and units_per_pallet and units_per_pallet > 0 and loading:
        branches.append(pallet_freight / PALLETS_PER_TRUCK / units_per_pallet / loading)
    elif pallet_freight is not None:
        assumptions.append("loading_rate=不可用，只走最低运费分支")
        if loading is None:
            assumptions.append("invalid_loading_rate")
    if not branches:
        out["amount"] = None
        out["gap"] = {"code": "freight_rule_missing", "where": code,
                      "detail": "运输规则没有可用的计费分支"}
        out["assumptions"] = assumptions
        return out
    out["amount"] = max(branches)
    out["assumptions"] = assumptions
    return out


# --------------------------------------------------------------------------- #
# 材料 / 费率取值
# --------------------------------------------------------------------------- #
def _material_rows() -> list:
    return [dict(row) for row in kb_repo._table("kb_material")]


def _material_gsm(material: dict) -> Optional[float]:
    for prop in (material.get("properties") or []):
        if _text(prop.get("prop_key")) == "gsm":
            value = _num(prop.get("value_num"))
            if value is not None:
                return value
    grade = _text(material.get("grade"))
    match = re.search(r"\d+(?:\.\d+)?\s*g\b", grade, re.IGNORECASE)
    if match:
        return float(re.search(r"\d+(?:\.\d+)?", match.group(0)).group(0))
    match = re.match(r"^(\d+(?:\.\d+)?)\s*g$", grade, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def _resolve_material(row: dict, materials: list) -> Optional[dict]:
    code = _text(row.get("material_code"))
    if code:
        for material in materials:
            if _text(material.get("material_code")) == code:
                return material
    text = _text(row.get("material") or row.get("item_name"))
    if not text:
        return None
    for material in materials:
        if text == _text(material.get("name")) or text == _text(material.get("grade")):
            return material
    head = text.split()[0] if text.split() else text
    for material in materials:
        name = _text(material.get("name"))
        if head and (head in name or name in head):
            return material
    return None


def _material_price(material_code: str) -> Optional[dict]:
    return kb_repo.current_price(material_code, industry=PACKAGING_INDUSTRY)


def _rate_row(rate_code: str) -> Optional[dict]:
    code = _text(rate_code)
    if not code:
        return None
    rows = [dict(row) for row in kb_repo._table("kb_cost_rate")
            if _text(row.get("rate_code")) == code]
    if not rows:
        return None
    rows.sort(key=lambda item: _text(item.get("effective_from")))
    return rows[-1]


def _tax_factor() -> float:
    for factor in kb_repo._table("kb_cost_factor"):
        if _text(factor.get("factor_code")) == "F-PKG-TAX-VAT":
            value = _num(factor.get("value"))
            if value is not None:
                return 1.0 + value
    return 1.13


# --------------------------------------------------------------------------- #
# 明细行构造
# --------------------------------------------------------------------------- #
def _item(seq: int, category: str, *, part_code: Optional[str] = None,
          part_name: str = "", **extra) -> dict:
    row = {"seq": seq, "part_code": part_code, "part_name": part_name,
           "cost_category": category, "formula_code": _text(extra.pop("formula_code", "")),
           "formula_version": _text(extra.pop("formula_version", "")),
           "content_code": extra.pop("content_code", None),
           "tooling_code": extra.pop("tooling_code", None),
           "rate_code": _text(extra.pop("rate_code", "")),
           "quantity_basis": _text(extra.pop("quantity_basis", "")),
           "quantity": extra.pop("quantity", None), "unit": _text(extra.pop("unit", "")),
           "unit_price": extra.pop("unit_price", None),
           "amount": extra.pop("amount", 0.0), "min_charge_applied": False,
           "loss_rate": extra.pop("loss_rate", None), "amount_with_loss": 0.0,
           "expression": _text(extra.pop("expression", "")),
           "inputs_json": _text(extra.pop("inputs_json", "")),
           "source_ref": _text(extra.pop("source_ref", "")),
           "source": _text(extra.pop("source", "kb")), "note": _text(extra.pop("note", ""))}
    row.update(extra)
    return row


def _line_to_item(seq: int, line: dict, *, source_ref: str, source: str = "formula",
                  expression: str = "") -> dict:
    inputs = line.get("inputs") or {}
    return _item(seq, line.get("cost_category") or "", part_code=line.get("part_code"),
                 part_name=line.get("part_name") or "", formula_code=line.get("formula_code"),
                 content_code=line.get("content_code"), tooling_code=line.get("tooling_code"),
                 rate_code=line.get("rate_code"), quantity_basis=line.get("quantity_basis"),
                 amount=line.get("amount"), loss_rate=line.get("loss_rate"),
                 min_charge_applied=bool(line.get("min_charge_applied")),
                 expression=expression or line.get("expression") or "",
                 inputs_json=json.dumps(inputs, ensure_ascii=False, default=str),
                 source_ref=source_ref, source=line.get("source") or source,
                 note=line.get("note") or "")


# --------------------------------------------------------------------------- #
# 组装 / 落库 / 读回
# --------------------------------------------------------------------------- #
def _fatal(code: str, message: str, status_code: int = 409) -> CostError:
    return CostError(message, status_code, code)


def _requirement_context(project_id: str, requirement_no: str, scenario: Optional[dict]):
    data = _requirement_data(project_id)
    overrides = dict(scenario or {})
    data.update({key: value for key, value in overrides.items() if value is not None})
    return data, overrides


def compute_project(project_id: str, requirement_no: str = "", *,
                    scenario: Optional[dict] = None) -> dict:
    """组装 → 逐行算 → 三层汇总（**不落库**，Spec §4.5）。"""
    data, overrides = _requirement_context(project_id, requirement_no, scenario)
    if _industry_of(data) != PACKAGING_INDUSTRY:
        raise _fatal("not_packaging", "包装成本引擎只对 industry='packaging' 的需求单生效", 400)
    req_no = _resolve_requirement_no(project_id, requirement_no)

    record = da_repo.load_box_match(project_id, req_no) or {}
    box_code = _text(record.get("confirmed_box_type"))
    if _text(record.get("decision")) != "confirmed" or not box_code:
        raise _fatal("box_type_not_confirmed", "尚未确认盒型，无法测算包装成本（Spec §2.13）")

    bom_rows = [dict(row) for row in da_repo.load_packaging_bom(project_id, req_no)]
    if not bom_rows:
        raise _fatal("bom_not_built", "包装 BOM 尚未建立，请先展开部件（Spec §2.13）")

    route = da_repo.load_packaging_route(project_id, req_no)
    if not route or _text(route.get("status")) != "confirmed":
        raise _fatal("route_not_confirmed", "工艺路线尚未确认，人工费无从取工时（Spec §2.13）")
    steps = [dict(row) for row in da_repo.load_packaging_route_steps(project_id, req_no)]

    quantity = _num(data.get("quote_quantity"))
    if quantity is None or quantity <= 0:
        raise _fatal("quantity_missing", "报价数量缺失或 ≤ 0，无法测算包装成本（Spec §2.13）")

    scenario_code = _text(overrides.get("scenario_code") or data.get("quote_scenario")) or "default"
    req_loss = _num(data.get("loss_rate"))
    tax_factor = _tax_factor()
    factor_rows = [dict(row) for row in kb_repo._table("kb_cost_factor")]
    gaps: list = []
    assumptions: list = []

    def loss_rate_for(material_text: str) -> Optional[float]:
        if req_loss is not None:
            return req_loss
        return default_loss_rate(material_text, rows=factor_rows)

    lines: list = []
    tooling_lines: list = []

    # 1) 部件 × 材料 -------------------------------------------------------- #
    materials = _material_rows()
    entry_material = resolve_formula("PKG-C-MATERIAL")
    machine_length = _num(data.get("machine_length"))
    machine_width = _num(data.get("machine_width"))
    imposition = _num(data.get("imposition_count")) or 1.0
    for row in bom_rows:
        if _text(row.get("bom_category")) not in ("box_part", "optional_part"):
            continue
        part_code = _text(row.get("part_code") or row.get("item_key"))
        part_name = _text(row.get("item_name"))
        material_text = _text(row.get("material") or row.get("item_name"))
        length = _num(row.get("length_mm"))
        width = _num(row.get("width_mm"))
        material = _resolve_material(row, materials)
        gsm = _material_gsm(material) if material else None
        price = None
        if material:
            price_row = _material_price(_text(material.get("material_code")))
            if price_row:
                price = _num(price_row.get("price"))
        variables = {"cut_length": length, "cut_width": (width + 5) if width is not None else None,
                     "gsm": gsm, "ton_price": (price * 1000) if price is not None else None,
                     "imposition_count": imposition, "proof_base": _num(data.get("proofing_base")) or 0.0,
                     "quote_quantity": quantity, "tax_factor": tax_factor,
                     "machine_length": machine_length,
                     "machine_width": machine_width if machine_width is not None
                     else ((width) if width is not None else None)}
        result = None
        if length is None or width is None:
            gaps.append({"code": "part_size_missing", "where": part_code,
                         "detail": "部件「%s」没有展开尺寸，材料行不出金额" % part_name})
        elif material is None or price is None:
            gaps.append({"code": "material_price_missing", "where": part_code,
                         "detail": "材料「%s」没有有效价格，材料行不出金额" % material_text})
        elif gsm is None:
            gaps.append({"code": "material_gsm_missing", "where": part_code,
                         "detail": "材料「%s」解析不到克重，材料行不出金额" % material_text})
        else:
            result = compute_line("material", variables)
        amount = result.get("amount") if result else None
        expression = result.get("expression") if result else entry_material["expression"]
        line = {"cost_category": "material", "part_code": part_code, "part_name": part_name,
                "formula_code": entry_material["formula_code"], "rate_code": "",
                "expression": expression, "inputs": variables, "amount": amount,
                "min_charge_applied": False, "source": "formula",
                "items_inputs": variables}
        rate = loss_rate_for(material_text)
        if rate is None and amount is not None:
            gaps.append({"code": "loss_rate_missing", "where": part_code,
                         "detail": "材料「%s」取不到损耗率，损耗按 0 计（金额保留）" % material_text})
        line["loss_rate"] = rate
        lines.append(line)

    # 2) 工序类（BOM process 行 + 第 6 批工序） ---------------------------- #
    process_names: list = []
    for row in bom_rows:
        if _text(row.get("bom_category")) == "process":
            name = _text(row.get("item_name") or row.get("item_key"))
            if name and name not in process_names:
                process_names.append(name)
    for step in steps:
        name = _text(step.get("step_name"))
        if name and name not in process_names:
            process_names.append(name)
    sheet_length = machine_length
    sheet_width = machine_width
    for name in process_names:
        category = STEP_CATEGORY_MAP.get(name)
        if not category:
            continue
        entry = _entry_for(category)
        if entry is None:
            gaps.append({"code": "no_formula:%s" % category, "where": name,
                         "detail": "类别 %s 在 0903 里是手填列，本批没有公式" % category})
            continue
        variables = dict(entry.get("defaults") or {})
        variables.update({"quote_quantity": quantity, "imposition_count": imposition,
                          "tax_factor": tax_factor})
        if category in ("print", "print_uv", "lamination", "hot_stamp_flat", "glue",
                        "v_groove", "die_cutting", "mounting"):
            variables["machine_length"] = sheet_length
            variables["machine_width"] = sheet_width
        if category == "hot_stamp_flat":
            area = _num(data.get("hot_area_mm2"))
            if area is None:
                area = _num(data.get("process_area"))
            variables["hot_area_mm2"] = area
        if sheet_length is None and category in ("print_uv", "lamination", "glue"):
            gaps.append({"code": "part_size_missing", "where": name,
                         "detail": "缺上机尺寸，%s 行不出金额" % category})
            lines.append({"cost_category": category, "part_code": None, "part_name": name,
                          "formula_code": entry["formula_code"], "amount": None,
                          "min_charge_applied": False, "loss_rate": None,
                          "expression": entry["expression"], "inputs": variables,
                          "source": "formula"})
            continue
        rate_code = _text(entry.get("rate_code"))
        if rate_code:
            rate_row = _rate_row(rate_code)
            if rate_row is None:
                gaps.append({"code": "rate_missing:%s" % rate_code, "where": name,
                             "detail": "知识库缺费率 %s" % rate_code})
            else:
                if _text(rate_row.get("rate_type")) == "equipment_dep":
                    variables["equipment_rate"] = _num(rate_row.get("value"))
                else:
                    variables["labor_rate"] = _num(rate_row.get("value"))
        result = compute_line(category, variables)
        if result.get("gap"):
            gaps.append(dict(result["gap"], where=result["gap"].get("where") or name))
        line = {"cost_category": category, "part_code": None, "part_name": name,
                "formula_code": entry["formula_code"], "rate_code": rate_code,
                "expression": entry["expression"], "inputs": result.get("inputs") or variables,
                "amount": result.get("amount"),
                "min_charge_applied": bool(result.get("min_charge_applied")),
                "source": "formula"}
        rate = loss_rate_for(name)
        if rate is None and line["amount"] is not None:
            gaps.append({"code": "loss_rate_missing", "where": name,
                         "detail": "工序「%s」取不到损耗率，损耗按 0 计（金额保留）" % name})
        line["loss_rate"] = rate
        lines.append(line)

    # 3) 人工（标准工时 × 工时费率，逐工序一行） --------------------------- #
    overhead_row = _rate_row("RATE-PKG-OVERHEAD")
    overhead_rate = _num(overhead_row.get("value")) if overhead_row else 0.0
    entry_labor = resolve_formula("PKG-C-LABOR")
    for step in steps:
        name = _text(step.get("step_name"))
        rate_code = STEP_RATE_MAP.get(name)
        if not rate_code:
            continue
        seconds = _num(step.get("standard_seconds"))
        step_no = _text(step.get("step_no"))
        rate_row = _rate_row(rate_code)
        variables = {"labor_seconds": seconds, "labor_rate": None,
                     "overhead_seconds": 0.0, "overhead_rate": overhead_rate or 0.0,
                     "quote_quantity": quantity, "step_no": step_no}
        if seconds is None:
            gaps.append({"code": "step_time_missing:%s" % name, "where": rate_code,
                         "detail": "工序「%s」待补工时，人工行不出金额" % name})
            lines.append({"cost_category": "labor", "part_code": None, "part_name": name,
                          "formula_code": entry_labor["formula_code"], "rate_code": rate_code,
                          "expression": entry_labor["expression"], "inputs": variables,
                          "amount": None, "min_charge_applied": False, "loss_rate": None,
                          "source": "formula",
                          "source_ref": "step:%s" % step_no})
            continue
        if rate_row is None:
            gaps.append({"code": "rate_missing:%s" % rate_code, "where": name,
                         "detail": "知识库缺工时费率 %s" % rate_code})
            lines.append({"cost_category": "labor", "part_code": None, "part_name": name,
                          "formula_code": entry_labor["formula_code"], "rate_code": rate_code,
                          "expression": entry_labor["expression"], "inputs": variables,
                          "amount": None, "min_charge_applied": False, "loss_rate": None,
                          "source": "formula", "source_ref": "step:%s" % step_no})
            continue
        variables["labor_rate"] = _num(rate_row.get("value"))
        result = compute_line("labor", variables)
        amount = result.get("amount")
        min_applied = bool(result.get("min_charge_applied"))
        minimum_charge = _num(rate_row.get("minimum_charge")) or 0.0
        if amount is not None and minimum_charge and quantity:
            threshold = minimum_charge / quantity
            if threshold > amount:
                amount = threshold
                min_applied = True
        line = {"cost_category": "labor", "part_code": None, "part_name": name,
                "formula_code": entry_labor["formula_code"], "rate_code": rate_code,
                "expression": entry_labor["expression"],
                "inputs": result.get("inputs") or variables, "amount": amount,
                "min_charge_applied": min_applied, "source": "formula",
                "source_ref": "step:%s" % step_no, "quantity_basis": "按工时"}
        rate = loss_rate_for(name)
        if rate is None and amount is not None:
            gaps.append({"code": "loss_rate_missing", "where": name,
                         "detail": "工序「%s」取不到损耗率，损耗按 0 计（金额保留）" % name})
        line["loss_rate"] = rate
        lines.append(line)

    # 4) 工装 / 刀模（项目级，不摊进部件行） ------------------------------- #
    tooling_rules = {_text(row.get("process_code")): dict(row)
                     for row in kb_repo.packaging_tooling_rules()}
    seen_tooling: set = set()
    for row in bom_rows:
        if _text(row.get("bom_category")) != "tooling":
            continue
        process = _text(row.get("component") or row.get("item_name"))
        rule = tooling_rules.get(process)
        if rule is None or process in seen_tooling:
            if rule is None:
                gaps.append({"code": "tooling_basis_missing:%s" % process,
                             "where": process, "detail": "知识库没有 %s 的工装规则" % process})
            continue
        seen_tooling.add(process)
        result = compute_tooling(rule, quote_quantity=quantity,
                                 amortized_quantity=_num(data.get("tooling_amortize_qty")),
                                 committed_volume=_num(data.get("committed_volume")),
                                 cumulative_quantity=_num(data.get("cumulative_quantity")))
        if result.get("gap"):
            gaps.append(dict(result["gap"]))
        result["source_ref"] = _text(rule.get("tooling_code"))
        tooling_lines.append(result)

    # 5) 包材 --------------------------------------------------------------- #
    packaging = compute_packaging(kb_repo.packaging_cost_contents(), tax_factor=tax_factor,
                                  loss_uplift=LOSS_UPLIFT, yield_divisor=YIELD_DIVISOR)
    for line in packaging["lines"]:
        if line.get("gap"):
            gaps.append(dict(line["gap"]))

    # 6) 运输 --------------------------------------------------------------- #
    shipping = _text(data.get("shipping_mode"))
    rules = [dict(row) for row in kb_repo.packaging_logistics_rules()]
    freight = None
    if rules:
        matched = [row for row in rules if shipping and _text(row.get("shipping_mode")) == shipping]
        chosen = (matched or rules)
        chosen.sort(key=lambda row: _text(row.get("rule_code")))
        freight = compute_freight(chosen[0], quote_quantity=quantity)
        if freight.get("gap"):
            gaps.append(dict(freight["gap"]))
        assumptions.extend(freight.get("assumptions") or [])
    else:
        gaps.append({"code": "freight_rule_missing", "where": "", "detail": "知识库没有运输规则"})

    # 7) MOQ 硬门槛 --------------------------------------------------------- #
    moq = _num(data.get("moq"))
    if moq is None:
        box = next((row for row in kb_repo.packaging_box_types()
                    if _text(row.get("box_type_code")) == box_code), None)
        moq = _num(box.get("moq")) if box else None
    if moq and quantity < moq:
        gaps.append({"code": "below_moq", "where": box_code,
                     "detail": "数量 %s 低于盒型 MOQ %s，仍然出成本" % (quantity, moq)})

    summary = summarize(lines, packaging=packaging["amount"],
                        freight=(freight or {}).get("amount") or 0.0,
                        tooling=tooling_lines)
    material_total = summary["categories"].get("material", 0.0)
    labor_total = summary["categories"].get("labor", 0.0)
    other_total = summary["categories"].get("other", 0.0)
    process_total = summary["subtotal"] - material_total - labor_total - other_total
    now = da_db.now()
    estimate_id = "pkgcost:%s:%s:%s" % (project_id, req_no, scenario_code)

    items: list = []
    seq = 0
    for line in lines:
        seq += 1
        items.append(_line_to_item(seq, line,
                                   source_ref=line.get("source_ref") or line.get("part_code") or ""))
    for line in tooling_lines:
        seq += 1
        items.append(_line_to_item(seq, line, source_ref=line.get("source_ref") or "",
                                   source="kb"))
    for line in packaging["lines"]:
        seq += 1
        items.append(_line_to_item(seq, line, source_ref=line.get("formula_code") or "",
                                   source="formula"))

    trial = _text(data.get("是否首批试产") or data.get("first_trial"))
    trial_or_mass = None
    if trial:
        trial_or_mass = "trial" if is_required(trial) else "mass"
    quantity_tier = _text(data.get("quantity_tier")) or (
        str(int(quantity)) if float(quantity).is_integer() else str(quantity))

    return {
        "project_id": project_id, "requirement_no": req_no, "scenario_code": scenario_code,
        "estimate_id": estimate_id, "industry": PACKAGING_INDUSTRY,
        "engine_version": ENGINE_VERSION, "cost_profile": COST_PROFILE, "currency": "CNY",
        "quote_quantity": float(quantity), "tax_rate": round(tax_factor - 1.0, 6),
        "loss_base_scope": DEFAULT_LOSS_BASE_SCOPE, "quantity_tier": quantity_tier,
        "trial_or_mass_production": trial_or_mass,
        "included_components": _text(overrides.get("included_components")) or "all",
        "material_total": material_total, "process_total": process_total,
        "labor_total": labor_total, "tooling_total": summary["tooling_total"],
        "packaging_total": summary["packaging_total"],
        "freight_total": summary["freight_total"], "other_total": other_total,
        "subtotal": summary["subtotal"], "loss_amount": summary["loss_amount"],
        "total_cost": summary["total_cost"], "has_gaps": bool(gaps),
        "gaps": gaps, "assumptions": assumptions,
        "categories": summary["categories"], "report_groups": summary["report_groups"],
        "category_labels": dict(COST_CATEGORIES),
        "items": items, "computed_at": now,
    }


def build_cost(project_id: str, requirement_no: str = "", *,
               scenario: Optional[dict] = None, actor: Any = None) -> dict:
    """算 + 落库：同一 `(项目, 需求单, 场景)` 先删明细再重建，写项目审计（Spec §3.2）。"""
    cost = compute_project(project_id, requirement_no, scenario=scenario)
    da_repo.save_packaging_cost(cost["project_id"], cost["requirement_no"],
                                cost["scenario_code"], cost, cost["items"])
    store.audit(project_id, "workflow:packaging_cost_rebuilt",
                {"requirement_no": cost["requirement_no"], "scenario_code": cost["scenario_code"],
                 "total_cost": cost["total_cost"], "has_gaps": cost["has_gaps"],
                 "by": _actor_name(actor)})
    return cost


def _rehydrate(row: dict, items: list) -> dict:
    stored = [dict(item) for item in items]
    tooling = [item for item in stored if _text(item.get("tooling_code"))]
    part_lines = [item for item in stored if not _text(item.get("tooling_code"))]
    summary = summarize(part_lines, packaging=row.get("packaging_total") or 0.0,
                        freight=row.get("freight_total") or 0.0, tooling=tooling,
                        loss_base_scope=row.get("loss_base_scope") or DEFAULT_LOSS_BASE_SCOPE)
    return {
        "built": True, "project_id": row.get("project_id"),
        "requirement_no": row.get("requirement_no") or "",
        "scenario_code": row.get("scenario_code") or "default",
        "estimate_id": row.get("estimate_id"), "industry": row.get("industry"),
        "engine_version": row.get("engine_version") or ENGINE_VERSION,
        "cost_profile": row.get("cost_profile") or COST_PROFILE,
        "currency": row.get("currency") or "CNY",
        "quote_quantity": row.get("quote_quantity"), "tax_rate": row.get("tax_rate"),
        "loss_base_scope": row.get("loss_base_scope") or DEFAULT_LOSS_BASE_SCOPE,
        "quantity_tier": row.get("quantity_tier"),
        "trial_or_mass_production": row.get("trial_or_mass_production"),
        "included_components": row.get("included_components"),
        "material_total": row.get("material_total") or 0.0,
        "process_total": row.get("process_total") or 0.0,
        "labor_total": row.get("labor_total") or 0.0,
        "tooling_total": row.get("tooling_total") or 0.0,
        "packaging_total": row.get("packaging_total") or 0.0,
        "freight_total": row.get("freight_total") or 0.0,
        "other_total": row.get("other_total") or 0.0,
        "subtotal": summary["subtotal"], "loss_amount": summary["loss_amount"],
        "total_cost": row.get("total_cost") or 0.0, "has_gaps": bool(row.get("has_gaps")),
        "gaps": _loads(row.get("gaps_json"), []),
        "assumptions": _loads(row.get("assumptions_json"), []),
        "categories": summary["categories"], "report_groups": summary["report_groups"],
        "category_labels": dict(COST_CATEGORIES),
        "items": stored, "computed_at": row.get("computed_at"),
    }


def load_cost(project_id: str, requirement_no: str = "", *,
              scenario: Optional[str] = None) -> dict:
    """读回成本测算 + 24 类别 + 10 分组 + 缺口；没算过时 `built=false`，不报错。"""
    req_no = _resolve_requirement_no(project_id, requirement_no)
    row = da_repo.load_packaging_cost(project_id, req_no, _text(scenario))
    if not row:
        return {"built": False, "project_id": project_id, "requirement_no": req_no,
                "scenario_code": _text(scenario) or "default", "engine_version": ENGINE_VERSION,
                "cost_profile": COST_PROFILE, "items": [], "gaps": [], "assumptions": [],
                "has_gaps": False, "categories": {code: 0.0 for code, _ in COST_CATEGORIES},
                "report_groups": {name: 0.0 for name in REPORT_GROUPS},
                "subtotal": 0.0, "loss_amount": 0.0, "tooling_total": 0.0,
                "packaging_total": 0.0, "freight_total": 0.0, "total_cost": 0.0}
    items = da_repo.load_packaging_cost_items(row["estimate_id"])
    return _rehydrate(row, items)


def cost_items(estimate_id: str) -> list:
    """成本明细行（按 seq 升序）。"""
    return [dict(row) for row in da_repo.load_packaging_cost_items(estimate_id)]


def cost_curve(project_id: str, requirement_no: str = "") -> list:
    """多场景成本曲线（按数量降序；第 7 批只出成本，不出售价）。"""
    req_no = _resolve_requirement_no(project_id, requirement_no)
    rows = da_repo.packaging_cost_estimates(project_id, req_no)
    return [{"scenario_code": row.get("scenario_code") or "default",
             "quote_quantity": row.get("quote_quantity"),
             "quantity_tier": row.get("quantity_tier"),
             "total_cost": row.get("total_cost") or 0.0,
             "has_gaps": bool(row.get("has_gaps")),
             "computed_at": row.get("computed_at")} for row in rows]

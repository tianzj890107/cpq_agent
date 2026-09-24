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
from pathlib import Path
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

#: 13 个报告分组（Spec 修复第 1 批 §2.1）。前 10 组是工作簿 `成本细分` 的同名列，
#: 组名与成员逐字保留；`表面处理` / `装订贴盒` / `其他费用` 承载工作簿没有细分、
#: 但引擎确实算得出金额的 13 个类别 —— 否则这些钱会算进总成本却在分组里消失。
REPORT_GROUPS = {
    "材料": ("material", "glue"), "印刷": ("print", "print_uv"),
    "覆膜": ("lamination", "transfer_film"),
    "烫金": ("hot_stamp_flat", "hot_stamp_round", "cold_stamp"), "丝印": ("silk_screen",),
    "表面处理": ("varnish", "anti_scratch", "pet_oil", "visidi_uv", "texture",
                 "emboss_deboss"),
    "裱纸": ("mounting",), "模切": ("die_cutting",),
    "装订贴盒": ("folding", "auto_mount", "double_tape"),
    "开槽": ("v_groove",), "手工": ("labor",), "其他费用": ("other",),
    "包装": ("packaging", "freight"),
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

#: 生成/重算写权限（Spec `packaging-cost-finance-access.md` §2.2）。
#:
#: 刻意**不**再写成 `packaging_match.BOX_MATCH_DECIDE_ROLES` 的别名：跨批次直接引用会把
#: 「排盒型的人」和「算成本的人」永久绑成同一批人，任何一边调整都会静默漂移。本版选**工艺代算**
#: 这条写法 —— 工艺侧（工艺经理 / 工艺技术总监）与管理员可以算，但**必须留痕**：成本记录带
#: `computed_by` / `computed_by_role`（§2.3），事后能回答「这一版是谁算的」。
#:
#: 与通用 2.3 的关系：`auth.COST_ROLES = {"finance_manager", "admin"}`（通用流程「成本只能财务改」）。
#: 包装这条链路**两边都认**：财务经理（流程归属）与工艺侧（代算）都能算，但**代算必须留痕** ——
#: 成本记录带 `computed_by` / `computed_by_role`，事后能回答「这一版是谁算的」。
#:
#: 为什么把财务经理**加进来**（Spec `packaging-cost-write-role-single-source.md` §2.1 方案 A）：
#: `auth.COST_ROLES` 与 `main.py` 的 `can_cost` 一直告诉前端「财务能算」，服务端却按这里把他
#: 挡在外面、项目 ACL 也只给他「成本算完之后的可见性」。两套口径同时活着，用户看到的是
#: 「按钮点得动，点完告诉你项目不存在」。本批按那份 Spec 的推荐方案把口径合成一处：
#: 财务能算，工艺侧仍可代算且留痕。
COST_WRITE_ROLES = {"process_manager", "process_director", "finance_manager", "admin"}

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
        "expression": _LAMINATION_EXPR, "minimum_charge": 0, "frozen_minimum_charge": 200, "rounding": 4,
        "rate_code": "RATE-PKG-EQUIP-SURFACE", "source_ref": _GSTAMP + "V2",
        "defaults": {"setup_minutes": 30.0, "capacity_per_hour": 5500.0,
                     "equipment_rate": 197.0, "labor_rate": 145.0, "film_price": 1.7,
                     "film_thickness_um": 18.0, "film_kg_price": 18.5}},
    "PKG-C-HOT-STAMP-FLAT": {
        "formula_code": "PKG-C-HOT-STAMP-FLAT", "cost_category": "hot_stamp_flat",
        "expression": _HOT_STAMP_FLAT_EXPR, "minimum_charge": 0, "frozen_minimum_charge": 150, "rounding": 4,
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
        "expression": _DIE_CUT_EXPR, "minimum_charge": 0, "frozen_minimum_charge": 100, "rounding": 4,
        "rate_code": "RATE-PKG-EQUIP-MOULD", "source_ref": _GSTAMP + "AI2",
        "defaults": {"setup_minutes": 120.0, "capacity_per_hour": 6500.0,
                     "equipment_rate": 197.52, "labor_rate": 190.06}},
    "PKG-C-V-GROOVE": {
        "formula_code": "PKG-C-V-GROOVE", "cost_category": "v_groove",
        "expression": _V_GROOVE_EXPR, "minimum_charge": 0, "frozen_minimum_charge": 120, "rounding": 4,
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

#: 公式来源申报（Spec 修复第 3 批 §5.1–§5.3）：`source_sheet` / `source_cell` 指向该条
#: 表达式**真实抄自**的那一格；`variable_map` 只登记由单元格提供的输入（值=列字母），
#: `verify_inputs` 里没进 `variable_map` 的键一律视为写死在公式里的字面量；`source_formula`
#: 是单元格原文（逐字，供 `verbatim_equivalent` 校验）；`minimum_charge_source_ref` 是
#: 门限数值真正所在的那一格（`minimum_charge == 0` 时为空串）。`verbatim=False` 表示该条
#: 是**已申报的改写**（不与 `source_cell` 逐字等价，理由见 `deviation`），不是抄错。
_FORMULA_PROVENANCE = {
    "PKG-C-MATERIAL": {
        "source_sheet": "报价-工费率", "source_cell": "S2",
        "source_ref": "报价逻辑-0903.xlsx/报价-工费率/S2",
        "minimum_charge_source_ref": "",
        "variable_map": {"cut_length": "K", "cut_width": "L", "gsm": "M", "ton_price": "N", "imposition_count": "J", "proof_base": "Q", "quote_quantity": "R"},
        "source_formula": "=K2*L2/1000000*M2/1000000*N2/1.13/J2+K2*L2/1000000*M2/1000000*N2/1.13*Q2/R2",
        "verify_inputs": {"cut_length": 889, "cut_width": 705, "gsm": 157, "ton_price": 6300, "imposition_count": 1, "tax_factor": 1.13, "proof_base": 450, "quote_quantity": 1000},
    },
    "PKG-C-PRINT-UV": {
        "source_sheet": "报价-工费率", "source_cell": "U2",
        "source_ref": "报价逻辑-0903.xlsx/报价-工费率/U2",
        "minimum_charge_source_ref": "",
        "variable_map": {"machine_length": "H", "machine_width": "I", "quote_quantity": "R", "imposition_count": "J"},
        "source_formula": "=((30/60+R2/J2/12000)*(591+666))/R2+H2*I2/1000000*4/1000*115/1.13/J2",
        "verify_inputs": {"machine_length": 889, "machine_width": 700, "imposition_count": 1, "setup_minutes": 30, "capacity_per_hour": 12000, "equipment_rate": 591, "labor_rate": 666, "ink_thickness_mm": 4, "ink_unit_price": 115, "tax_factor": 1.13, "quote_quantity": 1000},
    },
    "PKG-C-LAMINATION": {
        "source_sheet": "报价-工费率", "source_cell": "V2",
        "source_ref": "报价逻辑-0903.xlsx/报价-工费率/V2",
        "minimum_charge_source_ref": "",
        "variable_map": {"machine_length": "H", "machine_width": "I", "imposition_count": "J", "quote_quantity": "R"},
        "source_formula": "=(H2*I2/1000000*1.7/1.13/J2+H2*I2/1000000*18/1000*18.5/J2)+((30/60+R2/J2/5500)*(197+145))/R2",
        "verify_inputs": {"machine_length": 889, "machine_width": 700, "imposition_count": 1, "setup_minutes": 30, "capacity_per_hour": 5500, "equipment_rate": 197, "labor_rate": 145, "film_price": 1.7, "film_thickness_um": 18, "film_kg_price": 18.5, "tax_factor": 1.13, "quote_quantity": 1000},
    },
    "PKG-C-HOT-STAMP-FLAT": {
        "source_sheet": "报价-工费率", "source_cell": "X2",
        "source_ref": "报价逻辑-0903.xlsx/报价-工费率/X2",
        "minimum_charge_source_ref": "",
        "variable_map": {"imposition_count": "J", "quote_quantity": "R"},
        "source_formula": "=((100*75*4)/1000000*8.5)+((200/60+R2/J2/5000)*(193+115))/R2",
        "verify_inputs": {"hot_area_mm2": 30000, "imposition_count": 1, "setup_minutes": 200, "capacity_per_hour": 5000, "equipment_rate": 193, "labor_rate": 115, "foil_price": 8.5, "quote_quantity": 1000},
    },
    "PKG-C-MOUNTING": {
        "source_sheet": "报价-工费率", "source_cell": "AH13",
        "source_ref": "报价逻辑-0903.xlsx/报价-工费率/AH13",
        #: 裱纸 ① 有 100 门限，但引擎不采用（minimum_charge=0）→ 不许声明门限来源。
        "minimum_charge_source_ref": "",
        "variable_map": {"imposition_count": "J", "quote_quantity": "R"},
        "source_formula": "=((30/60+R13/J13/3500)*(209+126))/R13",
        "verify_inputs": {"imposition_count": 25, "setup_minutes": 30, "capacity_per_hour": 3500, "equipment_rate": 209, "labor_rate": 126, "quote_quantity": 1000},
    },
    "PKG-C-DIE-CUT": {
        "source_sheet": "报价-工费率", "source_cell": "AI2",
        "source_ref": "报价逻辑-0903.xlsx/报价-工费率/AI2",
        "minimum_charge_source_ref": "",
        "variable_map": {"imposition_count": "J", "quote_quantity": "R"},
        "source_formula": "=((120/60+R2/J2/6500)*(197.52+190.06))/R2",
        "verify_inputs": {"imposition_count": 1, "setup_minutes": 120, "capacity_per_hour": 6500, "equipment_rate": 197.52, "labor_rate": 190.06, "quote_quantity": 1000},
    },
    "PKG-C-V-GROOVE": {
        "source_sheet": "报价-工费率", "source_cell": "AK5",
        "source_ref": "报价逻辑-0903.xlsx/报价-工费率/AK5",
        "minimum_charge_source_ref": "",
        "variable_map": {"quote_quantity": "R"},
        "source_formula": "=(60/60+R5/3000)*(195+111)/R5*2",
        "verify_inputs": {"quote_quantity": 1000, "setup_minutes": 60, "capacity_per_hour": 3000, "equipment_rate": 195, "labor_rate": 111, "times": 2},
    },
    "PKG-C-GLUE": {
        "source_sheet": "报价-工费率", "source_cell": "AN2",
        "source_ref": "报价逻辑-0903.xlsx/报价-工费率/AN2",
        "minimum_charge_source_ref": "",
        "variable_map": {"machine_length": "H", "machine_width": "I", "imposition_count": "J"},
        "source_formula": "=H2*I2/1000000*0.74/J2",
        "verify_inputs": {"machine_length": 889, "machine_width": 700, "imposition_count": 1, "glue_unit_price": 0.74, "quote_quantity": 1000},
    },
    "PKG-C-LABOR": {
        "verbatim": False,
        "deviation": "AO15 原文 =(36+2)*40/180 是手工示例、量纲与标准工时口径不同；本条按 "
                     "docs/specs/packaging-cost-engine.md §2.6.2 改写为标准工时 × 费率，"
                     "声明为改写后不再与单元格逐字等价（工具按 declared_deviation 记录，不计问题）",
        "source_sheet": "报价-工费率", "source_cell": "AO15",
        "source_ref": "报价逻辑-0903.xlsx/报价-工费率/AO15",
        "minimum_charge_source_ref": "",
        "variable_map": {},
        "source_formula": "=(36+2)*40/180",
        "verify_inputs": {"labor_seconds": 36, "labor_rate": 40, "overhead_seconds": 0, "overhead_rate": 0},
    },
    "PKG-P-CARTON": {
        "source_sheet": "包装运输", "source_cell": "J2",
        "source_ref": "报价逻辑-0903.xlsx/包装运输/J2",
        "minimum_charge_source_ref": "",
        "variable_map": {"length_mm": "D", "width_mm": "E", "height_mm": "F", "usage_qty": "G", "material_price": "H", "units_per_pack": "I"},
        "source_formula": "=((E2+F2+25.4)*(D2+E2+50.8)*2*H2/1.13/645160+0.1+0.06+0.12)*1.03/0.9*G2/I2",
        "verify_inputs": {"length_mm": 520, "width_mm": 420, "height_mm": 425, "usage_qty": 1, "material_price": 3, "units_per_pack": 4, "tax_factor": 1.13, "loss_uplift": 1.03, "yield_divisor": 0.9},
    },
    "PKG-P-PAD": {
        "source_sheet": "包装运输", "source_cell": "J3",
        "source_ref": "报价逻辑-0903.xlsx/包装运输/J3",
        "minimum_charge_source_ref": "",
        "variable_map": {"length_mm": "D", "width_mm": "E", "height_mm": "F", "usage_qty": "G", "material_price": "H", "units_per_pack": "I"},
        "source_formula": "=((D3+6)*(E3+6)*H3/1.13/645160+0.04)*1.03/0.9*G3/I3",
        "verify_inputs": {"length_mm": 510, "width_mm": 410, "height_mm": 0, "usage_qty": 2, "material_price": 1.55, "units_per_pack": 4, "tax_factor": 1.13, "loss_uplift": 1.03, "yield_divisor": 0.9},
    },
    "PKG-P-DIVIDER": {
        "source_sheet": "包装运输", "source_cell": "J4",
        "source_ref": "报价逻辑-0903.xlsx/包装运输/J4",
        "minimum_charge_source_ref": "",
        "variable_map": {"length_mm": "D", "width_mm": "E", "height_mm": "F", "usage_qty": "G", "material_price": "H", "units_per_pack": "I"},
        "source_formula": "=(D4+6)*(E4+6)/1000000*350/1000000*H4/1.13*G4/I4",
        "verify_inputs": {"length_mm": 0, "width_mm": 0, "height_mm": 0, "usage_qty": 0, "material_price": 4200, "units_per_pack": 4, "tax_factor": 1.13, "loss_uplift": 1.03, "yield_divisor": 0.9, "gsm": 350},
    },
    "PKG-P-BAG": {
        "source_sheet": "包装运输", "source_cell": "J5",
        "source_ref": "报价逻辑-0903.xlsx/包装运输/J5",
        "minimum_charge_source_ref": "",
        "variable_map": {"length_mm": "D", "width_mm": "E", "height_mm": "F", "usage_qty": "G", "material_price": "H", "units_per_pack": "I"},
        "source_formula": "=(D5+50)*(E5+50)*F5/100/1000*0.185*H5*G5/I5",
        "verify_inputs": {"length_mm": 0, "width_mm": 0, "height_mm": 0, "usage_qty": 0, "material_price": 18, "units_per_pack": 4, "bag_unit_price": 0.185, "tax_factor": 1.13, "loss_uplift": 1.03, "yield_divisor": 0.9},
    },
    "PKG-P-CRAFT-PAPER": {
        "source_sheet": "包装运输", "source_cell": "J6",
        "source_ref": "报价逻辑-0903.xlsx/包装运输/J6",
        "minimum_charge_source_ref": "",
        "variable_map": {"length_mm": "D", "width_mm": "E", "height_mm": "F", "usage_qty": "G", "material_price": "H", "units_per_pack": "I"},
        "source_formula": "=(D6+6)*(E6+6)/1000000*100/1000000*H6/1.13*G6/I6",
        "verify_inputs": {"length_mm": 0, "width_mm": 0, "height_mm": 0, "usage_qty": 0, "material_price": 5800, "units_per_pack": 4, "tax_factor": 1.13, "loss_uplift": 1.03, "yield_divisor": 0.9, "gsm": 100},
    },
    "PKG-P-STRAP": {
        "source_sheet": "包装运输", "source_cell": "J7",
        "source_ref": "报价逻辑-0903.xlsx/包装运输/J7",
        "minimum_charge_source_ref": "",
        "variable_map": {"length_mm": "D", "width_mm": "E", "height_mm": "F", "usage_qty": "G", "material_price": "H", "units_per_pack": "I"},
        "source_formula": "=(D7+6)*(E7+6)/1000000*30/1000000*H7/1.13*G7/I7",
        "verify_inputs": {"length_mm": 787, "width_mm": 1092, "height_mm": 0, "usage_qty": 4, "material_price": 7800, "units_per_pack": 4, "tax_factor": 1.13, "loss_uplift": 1.03, "yield_divisor": 0.9, "gsm": 30},
    },
    "PKG-P-CORNER-TOP": {
        "source_sheet": "包装运输", "source_cell": "J8",
        "source_ref": "报价逻辑-0903.xlsx/包装运输/J8",
        "minimum_charge_source_ref": "",
        "variable_map": {"length_mm": "D", "width_mm": "E", "height_mm": "F", "usage_qty": "G", "material_price": "H", "units_per_pack": "I"},
        "source_formula": "=D8/1000*H8*G8/I8",
        "verify_inputs": {"length_mm": 0, "width_mm": 0, "height_mm": 0, "usage_qty": 0, "material_price": 1.9, "units_per_pack": 120, "tax_factor": 1.13, "loss_uplift": 1.03, "yield_divisor": 0.9},
    },
    "PKG-P-CORNER-PAPER": {
        "source_sheet": "包装运输", "source_cell": "J9",
        "source_ref": "报价逻辑-0903.xlsx/包装运输/J9",
        "minimum_charge_source_ref": "",
        "variable_map": {"length_mm": "D", "width_mm": "E", "height_mm": "F", "usage_qty": "G", "material_price": "H", "units_per_pack": "I"},
        "source_formula": "=D9/1000*H9*G9/I9",
        "verify_inputs": {"length_mm": 0, "width_mm": 0, "height_mm": 0, "usage_qty": 0, "material_price": 1.9, "units_per_pack": 120, "tax_factor": 1.13, "loss_uplift": 1.03, "yield_divisor": 0.9},
    },
    "PKG-P-LABEL": {
        "source_sheet": "包装运输", "source_cell": "J10",
        "source_ref": "报价逻辑-0903.xlsx/包装运输/J10",
        "minimum_charge_source_ref": "",
        "variable_map": {"length_mm": "D", "width_mm": "E", "height_mm": "F", "usage_qty": "G", "material_price": "H", "units_per_pack": "I"},
        "source_formula": "=H10*G10/I10",
        "verify_inputs": {"length_mm": 0, "width_mm": 0, "height_mm": 0, "usage_qty": 0, "material_price": 0.05, "units_per_pack": 120, "tax_factor": 1.13, "loss_uplift": 1.03, "yield_divisor": 0.9},
    },
    "PKG-P-BOARD": {
        "source_sheet": "包装运输", "source_cell": "J11",
        "source_ref": "报价逻辑-0903.xlsx/包装运输/J11",
        "minimum_charge_source_ref": "",
        "variable_map": {"length_mm": "D", "width_mm": "E", "height_mm": "F", "usage_qty": "G", "material_price": "H", "units_per_pack": "I"},
        "source_formula": "=H11/1.13*G11/I11",
        "verify_inputs": {"length_mm": 0, "width_mm": 0, "height_mm": 0, "usage_qty": 0, "material_price": 30, "units_per_pack": 120, "tax_factor": 1.13, "loss_uplift": 1.03, "yield_divisor": 0.9},
    },
    "PKG-P-PALLET": {
        "source_sheet": "包装运输", "source_cell": "J12",
        "source_ref": "报价逻辑-0903.xlsx/包装运输/J12",
        "minimum_charge_source_ref": "",
        "variable_map": {"length_mm": "D", "width_mm": "E", "height_mm": "F", "usage_qty": "G", "material_price": "H", "units_per_pack": "I"},
        "source_formula": "=H12/1.13*G12/I12",
        "verify_inputs": {"length_mm": 1200, "width_mm": 1000, "height_mm": 120, "usage_qty": 1, "material_price": 70, "units_per_pack": 120, "tax_factor": 1.13, "loss_uplift": 1.03, "yield_divisor": 0.9},
    },
}
for _code, _extra in _FORMULA_PROVENANCE.items():
    FORMULA_CATALOG[_code].update(_extra)


def _prune_variable_maps(catalog: dict) -> None:
    """`variable_map` 只登记**该公式里**用到的单元格输入（Spec 修复第 3 批 §5.3）。

    条目里按「该行在工作簿上的候选输入列」整体登记（便于人工对照），这里按表达式收口：
    去掉不参与该条公式的列。否则 `verbatim_equivalent` 的变量还原表会把单元格里根本不存在的
    引用也算成「已声明」，声明失真。
    """
    for entry in catalog.values():
        mapping = entry.get("variable_map")
        if not mapping:
            continue
        expression = entry.get("expression") or ""
        entry["variable_map"] = {name: column for name, column in mapping.items()
                                 if name in expression}


_prune_variable_maps(FORMULA_CATALOG)


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


def _part_usage_qty(row: Any) -> tuple:
    """BOM 行的用量（Spec `packaging-cost-part-usage-not-applied.md` §2.1）。

    取这一行的 `quantity`；为空 / 非数字 / ≤ 0 → 按 1 计（**不**猜、不拿别的列顶）。
    返回 `(usage_qty, missing)`：`missing=True` 表示这一行没有可用用量，读侧必须披露
    （`usage_qty_missing`），因为界面上的「数量 8」与材料金额会各说各话。
    """
    value = _num(row.get("quantity")) if isinstance(row, dict) else None
    if value is None or value <= 0:
        return 1.0, True
    return float(value), False


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


# --------------------------------------------------------------------------- #
# 最低收费口径申报（Spec 修复第 3 批 §5.3 / §6）
# --------------------------------------------------------------------------- #
#: 规则快照（修复第 2 批产物）；缺失时按「未裁决」处理，绝不静默装作已裁决。
RULES_JSON_PATH = (Path(__file__).resolve().parents[3] / "tech_app" / "agent_knowledge"
                   / "rules" / "packaging_cost_rules.json")

#: 单元格地址（`V2` / `AH13`）；表达式里已经是这个形状的标识符视为已还原。
#: 注意：正则一律以字符串形式内联，不预编译 —— 引擎源码里不许出现动态编译相关的
#: 调用（红测 a8 的「不执行动态代码」守卫）。
_CELL_REF_TEXT = r"^[A-Za-z]{1,3}[0-9]{1,5}$"
#: 科学计数法数字（`1e6`）→ 普通写法后再解析（Spec §5.3 第 3 步）。
_SCIENTIFIC_TEXT = r"\d+(?:\.\d+)?[eE][+-]?\d+"


def _load_minimum_charge_policy() -> dict:
    """从快照读口径申报块；读不到 → `pending`（Spec §6）。

    读不到时**留痕**（Spec `packaging-cost-and-handoff-static-downgrade-disclosure.md` §2.1）：
    新增 `source` ∈ `{"snapshot", "unavailable"}` 与 `unavailable_reason`（异常类名，不是散文），
    既有六键口径逐字不变 —— 结论不改，只让"没裁决"与"根本没读到"分得开。
    """
    source, reason = "snapshot", ""
    try:
        data = json.loads(Path(RULES_JSON_PATH).read_text(encoding="utf-8"))
        block = data.get("minimum_charge_policy") or {}
        if not isinstance(block, dict):
            raise TypeError("minimum_charge_policy 不是块")
    except Exception as exc:  # noqa: BLE001 - 快照不可用不该让成本计算整体失败
        block, source, reason = {}, "unavailable", type(exc).__name__
    status = _text(block.get("status")) or "pending"
    return {"status": status,
            "chosen": _text(block.get("chosen")),
            "decided_by": _text(block.get("decided_by")),
            "decided_at": _text(block.get("decided_at")),
            # 快照缺失/读不到时必须能直接看出「未裁决、当前按哪套回退」（Spec §6）。
            "policy": "unresolved" if status == "pending" else _text(block.get("chosen")),
            "source": source,
            "unavailable_reason": reason}


#: 模块级口径常量（Spec §6）：`pending` = 未裁决，运行时必须标注 `unresolved`。
MINIMUM_CHARGE_POLICY = _load_minimum_charge_policy()

#: 与第 1 批**冻结值**不一致的最低收费：逐条登记出处、差异与裁决（Spec
#: `packaging-cost-red-closure.md` C3）。`frozen` = `FORMULA_CATALOG[...]["frozen_minimum_charge"]`，
#: `current` = 运行时 `minimum_charge`。
#: `owner` / `decided_at` 只能由业务/用户给，实现方不许填数（见
#: docs/specs/packaging-cost-minimum-charge-decision.md §1）。2026-09-21 业务裁决：
#: 取 ② 报价-工费率（主行无门限），四个码的门限一并归零。
_MIN_CHARGE_DECISIONS = {
    "PKG-C-LAMINATION": {
        "frozen": 200, "current": 0, "owner": "张真", "decided_at": "2026-09-21",
        "reason": "② 报价-工费率主行无门限，取报价-工费率原文；第 1 批冻结的 200 出自 "
                  "① 报价-行业标准!V2，本批归零后不再参与命中判定、只作冻结证据保留。"
                  "行级 MAX 只出现在 报价-工费率!AI9/AI14/AI15（见 PKG-C-DIE-CUT.row_variants）。",
    },
    "PKG-C-HOT-STAMP-FLAT": {
        "frozen": 150, "current": 0, "owner": "张真", "decided_at": "2026-09-21",
        "reason": "② 报价-工费率主行无门限，取报价-工费率原文；第 1 批冻结的 150 出自 "
                  "① 报价-行业标准!X2，本批归零后不再参与命中判定、只作冻结证据保留。"
                  "行级 MAX 只出现在 报价-工费率!AI9/AI14/AI15（见 PKG-C-DIE-CUT.row_variants）。",
    },
    "PKG-C-DIE-CUT": {
        "frozen": 100, "current": 0, "owner": "张真", "decided_at": "2026-09-21",
        "reason": "② 报价-工费率主行无门限，取报价-工费率原文；第 1 批冻结的 100 出自 "
                  "① 报价-行业标准!AI2，本批归零后不再参与命中判定、只作冻结证据保留。"
                  "行级 MAX(100/R,0.08/J) 是工作簿原文，登记在本码 row_variants 的 "
                  "AI9/AI14/AI15 上，不并进主行。",
    },
    "PKG-C-V-GROOVE": {
        "frozen": 120, "current": 0, "owner": "张真", "decided_at": "2026-09-21",
        "reason": "② 报价-工费率主行无门限，取报价-工费率原文；第 1 批冻结的 120 在该工作簿"
                  "任何工作表里都不存在（① 报价-行业标准!AK5 原文是 MAX(150/R5,0.15)），"
                  "本批随裁决一并归零，不再参与命中判定、只作冻结证据保留。"
                  "行级 MAX 只出现在 报价-工费率!AI9/AI14/AI15（见 PKG-C-DIE-CUT.row_variants）。",
    },
}


def minimum_charge_policy() -> dict:
    """返回本次计算所用的最低收费口径（Spec §6）。

    · `status == "chosen"` → `policy == chosen`、`fallback == ""`；
    · `status == "pending"` → `policy == "unresolved"`、`fallback == "sheet_labor_rate"`
      （未裁决期间保持现有数值行为，但必须**标注**，不许静默装作已裁决）。

    每次都从模块级 `MINIMUM_CHARGE_POLICY` **现读**，不缓存到闭包/类属性 —— 测试与热更新
    靠替换这个常量验证口径切换。
    """
    block = MINIMUM_CHARGE_POLICY if isinstance(MINIMUM_CHARGE_POLICY, dict) else {}
    status = _text(block.get("status")) or "pending"
    chosen = _text(block.get("chosen"))
    if status == "chosen" and chosen:
        policy, fallback = chosen, ""
    else:
        status, policy, fallback = "pending", "unresolved", "sheet_labor_rate"
    return {"status": status, "chosen": chosen, "policy": policy, "fallback": fallback,
            "decided_by": _text(block.get("decided_by")),
            "decided_at": _text(block.get("decided_at")),
            # 「快照读到了没有」必须原样带出去（Spec `packaging-cost-and-handoff-static-downgrade-disclosure.md`
            # §2.1）：常量里没有这两键（旧快照 / 热替换）时按"读到了"兜底。
            "source": _text(block.get("source")) or "snapshot",
            "unavailable_reason": _text(block.get("unavailable_reason"))}


def _number_text(value: Any) -> str:
    """数字文本（Spec §5.3：整数去掉 `.0`，浮点按 12 位有效数字归一）。"""
    try:
        text = "%.12g" % float(value)
    except (TypeError, ValueError):
        return _text(value)
    return "0" if text in ("-0", "0.0") else text


def _expand_numbers(text: str) -> str:
    """把 `1e6` 这类科学计数法展开成普通写法（与 `1000000` 等价）。"""
    return re.sub(_SCIENTIFIC_TEXT, lambda m: _number_text(float(m.group(0))), str(text))


def _source_row(source_cell: Any) -> str:
    match = re.search(r"([0-9]+)\s*$", _text(source_cell))
    return match.group(1) if match else ""


def _split_top_level(text: str) -> list:
    """按顶层逗号切分（括号里的逗号不算）。"""
    parts: list = []
    depth = 0
    start = 0
    for index, char in enumerate(text):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _strip_iferror(text: Any) -> str:
    """剥掉 `IFERROR(x,"")` 外壳（Spec §5.3：只允许这一种包裹差异）。"""
    work = _text(text).lstrip("=").strip()
    while work.upper().startswith("IFERROR(") and work.endswith(")"):
        parts = _split_top_level(work[len("IFERROR("):-1])
        if len(parts) != 2:
            break
        work = parts[0].strip().lstrip("=").strip()
    return work


def _fold_constants(node):
    """常量折叠：只含数字的子树先算出值（`(100*75*4)` 与 `30000` 等价）。"""
    kind = node[0]
    if kind == "bin":
        left, right = _fold_constants(node[2]), _fold_constants(node[3])
        if left[0] == "num" and right[0] == "num" and node[1] in "+-*/":
            try:
                value = {"+": left[1] + right[1], "-": left[1] - right[1],
                         "*": left[1] * right[1], "/": left[1] / right[1]}[node[1]]
            except ZeroDivisionError:
                return ("bin", node[1], left, right)
            return ("num", value)
        return ("bin", node[1], left, right)
    if kind == "unary":
        inner = _fold_constants(node[2])
        if inner[0] == "num" and node[1] == "-":
            return ("num", -inner[1])
        return ("unary", node[1], inner)
    if kind == "call":
        return ("call", node[1], [_fold_constants(arg) for arg in node[2]])
    if kind == "cmp":
        return ("cmp", node[1], _fold_constants(node[2]), _fold_constants(node[3]))
    return node


def _print_node(node) -> str:
    kind = node[0]
    if kind == "num":
        return _number_text(node[1])
    if kind == "var":
        return str(node[1])
    if kind == "bin":
        return "(%s%s%s)" % (_print_node(node[2]), node[1], _print_node(node[3]))
    if kind == "unary":
        return "(%s%s)" % (node[1], _print_node(node[2]))
    if kind == "call":
        return "%s(%s)" % (node[1], ",".join(_print_node(arg) for arg in node[2]))
    if kind == "cmp":
        return "(%s%s%s)" % (_print_node(node[2]), node[1], _print_node(node[3]))
    return "?"


def _canonical_formula(text: Any) -> Optional[str]:
    """规范化成「全括号 + 常量折叠」的规范串；解析不了 → `None`（Spec §5.3）。"""
    source = _strip_iferror(text)
    if not source:
        return None
    try:
        tokens = packaging_formula._tokenize(
            _expand_numbers(packaging_formula.normalize_expression(source)))
        node = packaging_formula._Parser(tokens).parse()
    except Exception:  # noqa: BLE001 - 解析不了就是不等价，不抛给调用方
        return None
    return _print_node(_fold_constants(node))


def _resolve_expression(expression: Any, variable_map: Any, row: str, literals: Any) -> tuple:
    """把表达式里的标识符还原成 `<列><行>` 或字面量文本（Spec §5.3 第 2 步）。"""
    columns = {str(key): _text(value) for key, value in (variable_map or {}).items()}
    texts: dict = {}
    for key, value in (literals or {}).items():
        if value is None or isinstance(value, bool):
            continue
        texts[str(key)] = _number_text(value) if isinstance(value, (int, float)) else _text(value)
    unresolved: list = []

    def _swap(match):
        name = match.group(0)
        if name.upper() in packaging_formula.ALLOWED_FUNCTIONS:
            return name
        if re.match(_CELL_REF_TEXT, name):
            return name
        column = columns.get(name, "")
        if column and column.isalpha():
            return "%s%s" % (column, row)
        if name in texts:
            return texts[name]
        unresolved.append(name)
        return name

    return re.sub(_NAME_PATTERN, _swap, _text(expression)), unresolved


def verbatim_compare(expression: Any, source_formula: Any, variable_map: Any, source_cell: Any,
                     literals: Any = None) -> dict:
    """逐字等价判定（Spec §5.3）；返回 `{equivalent, unmapped, resolved, source, reason, unparsable}`。

    `unparsable`（Spec `packaging-rules-audit-unparsable-formula.md` §2.1）点名**哪一侧读不懂**
    —— 只允许 `"expression"` / `"source"`，两侧都读不懂时顺序固定「先 expression 后 source」，
    两侧都读得懂（等价与不等价）都是 `[]`。判据就是该侧 `_canonical_formula()` 返回 `None`；
    它只把答案交出来，`reason` 的取值与其余六个键逐字不变。
    """
    row = _source_row(source_cell)
    result = {"equivalent": False, "unmapped": [], "resolved": "", "source": "", "reason": "",
              "unparsable": []}
    if not row:
        result["reason"] = "bad_source_cell"
        return result
    resolved, unresolved = _resolve_expression(expression, variable_map, row, literals or {})
    result["resolved"] = resolved
    result["unmapped"] = unresolved
    if unresolved:
        result["reason"] = "unmapped_variable"
        return result
    left = _canonical_formula(resolved)
    right = _canonical_formula(source_formula)
    result["source"] = right or ""
    result["unparsable"] = [name for name, value in (("expression", left), ("source", right))
                            if value is None]
    if left is None or right is None:
        result["reason"] = "unparsable"
        return result
    result["equivalent"] = bool(left == right)
    result["reason"] = "" if result["equivalent"] else "differs"
    return result


def verbatim_equivalent(expression: Any, source_formula: Any, variable_map: Any,
                        source_cell: Any, literals: Any = None) -> bool:
    """`expression` 是否与 `source_cell` 的原文逐字等价（Spec §5.3）。"""
    return bool(verbatim_compare(expression, source_formula, variable_map, source_cell,
                                 literals)["equivalent"])


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


def _actor_role(actor: Any) -> str:
    """发起人的技术侧角色码（Spec `packaging-cost-finance-access.md` §2.3）。

    技术侧 `role` / CPQ 映射过来的 `role_code` / `cpq_role_code` 依次取第一个非空 ——
    成本记录留痕要的是「谁算的」，不是「他此刻能不能改项目」，所以不做角色白名单校验。
    """
    if isinstance(actor, dict):
        return _text(actor.get("role_code") or actor.get("cpq_role_code") or actor.get("role"))
    return ""


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


def assumption_refs(*sources: Any) -> list:
    """结论里必须带出来的"兜底假设"来源（Spec `packaging-parts-material-attribution.md` §5.4）。

    库内公式本身没有假设，但图纸零件的**材料/厚度可能是按需求整盒口径兜底的** ——
    这种前提下算出来的钱必须让人看见出处（`requirement.grey_board_thickness` 之类），
    否则"这件的料是哪来的"就查不到了。只认 `kind == "requirement_default"` 的来源。
    """
    refs: list = []
    for source in sources:
        payload = source if isinstance(source, dict) else {}
        if _text(payload.get("kind")) != "requirement_default":
            continue
        ref = _text(payload.get("evidence_ref")) or _text(payload.get("text"))
        refs.append(ref or "requirement")
    return sorted(set(refs))


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


def formula_codes_for(category: str) -> list:
    """该类别在目录里的全部 `formula_code`（按声明顺序）。"""
    return [code for code, entry in FORMULA_CATALOG.items()
            if entry.get("cost_category") == category]


def _code_for_line(category: str, formula_code: Optional[str] = None) -> str:
    """单行计算取哪条公式（Spec 修复第 1 批 §3.1）。

    类别下有 ≥ 2 条公式（`packaging` 的 11 条 `PKG-P-*`）时必须显式给 `formula_code`，
    不许静默取第一条 —— 那等于用纸箱公式算卡板/胶袋。
    """
    code = _text(formula_code)
    if code:
        return code
    codes = formula_codes_for(category)
    if len(codes) > 1:
        raise CostError("类别 %s 有 %d 条公式，必须显式指定 formula_code" % (category, len(codes)),
                        409, "category_needs_formula_code:%s" % category)
    return codes[0] if codes else ""


def rule_snapshot_version() -> str:
    """本次计算读到的知识库快照版本（取不到 → `""`，绝不编造）。"""
    try:
        value = kb_repo.kb_version()
    except Exception:  # noqa: BLE001 - 快照不可用不该让成本计算整体失败
        return ""
    return _text(value)


def rule_snapshot_version_detail() -> dict:
    """规则快照版本的三态（Spec `packaging-cost-and-handoff-static-downgrade-disclosure.md` §2.2）。

    返回 `{"version": str, "source": "kb" | "none" | "unavailable", "reason": str}`：
    `unavailable` = 读挂（reason 是异常类名）、`none` = **从没拉过快照**（`kb_repo.kb_version()`
    的既有语义：还没拉过给 `None`）、`kb` = 读到了。三态不许压成同一个空串。
    """
    try:
        value = kb_repo.kb_version()
    except Exception as exc:                            # noqa: BLE001 - 读不到要披露，不许炸
        return {"version": "", "source": "unavailable", "reason": type(exc).__name__}
    text = _text(value)
    if not text:
        return {"version": "", "source": "none", "reason": ""}
    return {"version": text, "source": "kb", "reason": ""}


def rule_snapshot_unavailable_of(detail: dict) -> dict:
    """`rule_snapshot_version_detail()` → 结果上的披露键（正常 `{}`）。"""
    source = _text((detail or {}).get("source"))
    if source == "unavailable":
        return {"code": "rule_snapshot_unavailable",
                "reason": _text((detail or {}).get("reason"))}
    if source == "none":
        return {"code": "rule_snapshot_not_pulled"}
    return {}


def upstream_route_version_detail(project_id: str, requirement_no: str) -> dict:
    """上游确认路线版本的三态（Spec §2.3）：`{"version", "source", "reason"}`。

    与 `_upstream_route_version()` 同一条读取入口、同一个取值口径（取**最后一条** dict 的
    `version`），只是把"读挂 / 一条都没有 / 读到了"分开说。
    """
    try:
        from tech_app.backend.services import packaging_route as _route_mod
        versions = _route_mod.route_versions(project_id, requirement_no) or []
    except Exception as exc:                            # noqa: BLE001 - 读不到要披露，不许炸
        return {"version": "", "source": "unavailable", "reason": type(exc).__name__}
    if not versions:
        return {"version": "", "source": "none", "reason": ""}
    latest = versions[-1] if isinstance(versions[-1], dict) else {}
    return {"version": str(latest.get("version") or ""), "source": "route", "reason": ""}


def _trace_fields(entry: Optional[dict] = None, *, result: Optional[dict] = None,
                  snapshot: str = "") -> dict:
    """结果行的可追溯字段（Spec 修复第 1 批 §3.2）。"""
    row = result or {}
    base = entry or {}
    return {"formula_source": _text(row.get("formula_source"))
            or _text(base.get("formula_source")) or "builtin",
            "formula_version": _text(row.get("formula_version"))
            or _text(base.get("formula_version")) or "1.0",
            "rule_snapshot_version": snapshot or rule_snapshot_version()}


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
    base["formula_source"] = "builtin"
    base["formula_version"] = "1.0"
    if rows is None:
        try:
            rows = kb_repo._table("kb_packaging_cost_formula")
        except Exception as exc:  # noqa: BLE001
            # 知识库快照不可用（未注入 CPQ_INTERNAL_TOKEN / 离线 / 单测环境）时用内置目录兜底：
            # 内置目录是 0903 的冻结口径，`reviewed` 覆盖只是可选增强。异常记在 note 上，不静默。
            base["note"] = "kb_unavailable:%s" % exc.__class__.__name__
            return base
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
    base["formula_source"] = "kb"
    base["formula_version"] = _text(row.get("formula_version")) or "1.0"
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
                 formula_code: Optional[str] = None, amount: Any = None,
                 rows=None) -> dict:
    """算一行成本（Spec §4.5 / §2.6 / §2.7；修复第 1 批 §3.1 起走唯一入口）。

    · 显式给 `amount` → 直接采用、`source="human"`、不取整；
    · 公式路径 → `MAX(minimum_charge/quote_quantity, 表达式)`，命中最低收费时
      `min_charge_applied=true`；
    · 公式一律经 `resolve_formula`（库 `reviewed` 覆盖内置目录），结果带
      `formula_source` / `formula_version` / `rule_snapshot_version` 供追溯；
    · 变量缺 → `amount=None` + `gap.code = missing_variable:<名>`；白名单外变量 →
      `CostError(409, unknown_variable:<名>)`；类别没有公式 → `no_formula:<code>`；
      类别下有多条公式又没给 `formula_code` → `CostError(409, category_needs_formula_code:)`。
    """
    inputs = dict(variables or {})
    policy = minimum_charge_policy()["policy"]
    if amount is not None:
        return {"cost_category": category, "formula_code": _text(formula_code),
                "expression": "", "inputs": inputs, "amount": float(amount),
                "min_charge_applied": False, "minimum_charge": 0.0, "source": "human",
                "loss_rate": inputs.get("loss_rate"), "assumptions": [], "policy": policy,
                "formula_source": "human", "formula_version": "",
                "rule_snapshot_version": rule_snapshot_version()}
    code = _code_for_line(category, formula_code)
    if not code:
        return {"cost_category": category, "formula_code": _text(formula_code) or category,
                "expression": "", "inputs": inputs, "amount": None,
                "min_charge_applied": False, "minimum_charge": 0.0, "source": "kb",
                "assumptions": [], "policy": policy,
                "formula_source": "builtin", "formula_version": "",
                "rule_snapshot_version": rule_snapshot_version(),
                "gap": {"code": "no_formula:%s" % (_text(formula_code) or category),
                        "where": category, "detail": "该类别没有公式，需要人工录入金额"}}
    entry = resolve_formula(code, rows=rows)
    trace = {"formula_source": _text(entry.get("formula_source")) or "builtin",
             "formula_version": _text(entry.get("formula_version")) or "1.0",
             "rule_snapshot_version": rule_snapshot_version()}
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
        out = {"cost_category": category, "formula_code": entry["formula_code"],
               "expression": expression, "inputs": merged, "amount": None,
               "min_charge_applied": False,
               "minimum_charge": float(entry.get("minimum_charge") or 0.0),
               "source": "formula", "assumptions": assumptions, "policy": policy,
               "gap": {"code": "missing_variable:%s" % missing[0], "where": category,
                       "detail": "缺少输入变量：%s" % "、".join(missing)}}
        out.update(trace)
        return out
    try:
        value = _formula_value(expression, merged)
    except packaging_formula.FormulaError as exc:
        out = {"cost_category": category, "formula_code": entry["formula_code"],
               "expression": expression, "inputs": merged, "amount": None,
               "min_charge_applied": False,
               "minimum_charge": float(entry.get("minimum_charge") or 0.0),
               "source": "formula", "assumptions": assumptions, "policy": policy,
               "gap": {"code": "formula_error:%s" % entry["formula_code"], "where": category,
                       "detail": str(exc)}}
        out.update(trace)
        return out
    quantity = _num(merged.get("quote_quantity"))
    minimum_charge = float(entry.get("minimum_charge") or 0.0)
    threshold = (minimum_charge / quantity) if (minimum_charge and quantity
                                                and quantity > 0) else 0.0
    applied = bool(minimum_charge and threshold > value)
    out = {"cost_category": category, "formula_code": entry["formula_code"],
           "expression": expression, "inputs": merged,
           "amount": threshold if applied else value, "min_charge_applied": applied,
           "minimum_charge": minimum_charge, "expression_value": value,
           "source": "formula", "assumptions": assumptions, "policy": policy}
    out.update(trace)
    return out


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


#: 材料行损耗率的来源闭集（Spec `packaging-cost-loss-rate-authoritative-sources` §2.1）。
LOSS_RATE_MATERIAL_SOURCE = "material.standard_loss_rate"


def loss_rate_detail(material_text: Any, *, rows=None,
                     material=None) -> tuple:
    """损耗率取数（Spec §2.1 四级）：返回 `(值, 来源)`；取不到是 `(None, None)`。

    1. 材料行 `standard_loss_rate`（真列，`NOT NULL DEFAULT 0`）—— **0 是「未登记」**，
       不是「损耗 0」，继续往下找（§2.2）；
    2. 既有文字兜底（灰板 → `F-PKG-LOSS-GREYBOARD`；面纸/衬纸/特种纸/铜版/纸 →
       `F-PKG-LOSS-PAPER`），一个字没删；
    3. 因子表按作用域：`material.category` 非空时取 `factor_type='scrap'` 的因子，
       **同作用域压过无作用域**、同级取 `effective_from` 最新（口径同
       `kb_repo.effective_factor()`，那里也是"有作用域的挪到前面"）；
    4. 取不到 → `(None, None)`（**不许**按 0 兜底）。

    没有材料行就没有作用域（§2.3）：工序行 / 人工行只走第 2 级，**不会**随手捡一条因子。
    """
    text = _text(material_text)
    source = rows if rows is not None else kb_repo._table("kb_cost_factor")
    factors = {_text(row.get("factor_code")): row for row in (source or [])
               if _text(row.get("factor_type")) == "scrap"}

    # 1) 材料行上的标准损耗率（0 = 未登记）
    if material:
        registered = _num(material.get("standard_loss_rate"))
        if registered is not None and registered > 0:
            return registered, LOSS_RATE_MATERIAL_SOURCE

    # 2) 文字兜底（既有行为）
    if "灰板" in text:
        hit = factors.get("F-PKG-LOSS-GREYBOARD")
        if hit and _num(hit.get("value")) is not None:
            return _num(hit.get("value")), "kb_cost_factor:F-PKG-LOSS-GREYBOARD"
    else:
        for keyword in ("面纸", "衬纸", "特种纸", "铜版", "纸"):
            if keyword in text:
                hit = factors.get("F-PKG-LOSS-PAPER")
                if hit and _num(hit.get("value")) is not None:
                    return _num(hit.get("value")), "kb_cost_factor:F-PKG-LOSS-PAPER"
                break

    # 3) 因子表按作用域（只有材料行给得出作用域时才走这一级）
    scope = _text((material or {}).get("category")) if material else ""
    if scope:
        rows_scrap = list(factors.values())
        scoped = [row for row in rows_scrap if _text(row.get("applicable_scope")) == scope]
        pool = scoped or [row for row in rows_scrap
                          if not _text(row.get("applicable_scope"))]
        if pool:
            ordered = sorted(pool, key=lambda row: _text(row.get("effective_from")),
                             reverse=True)
            hit = ordered[0]
            value = _num(hit.get("value"))
            if value is not None:
                return value, "kb_cost_factor:%s" % _text(hit.get("factor_code"))

    # 4) 取不到就是取不到
    return None, None


def default_loss_rate(material_text: Any, *, rows=None,
                      material=None) -> Optional[float]:
    """按材料/类别取 `factor_type='scrap'` 的损耗率；取不到返回 None（不许默认 0）。"""
    return loss_rate_detail(material_text, rows=rows, material=material)[0]


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
    #: 工装行的 `cost_category` 是 `other`，但它不进 `lines`（项目级），
    #: 因此按类别补进「其他费用」组，保证 `sum(report_groups) == total_cost`。
    extras = {"other": tooling_total}
    groups: dict = {}
    for name, members in REPORT_GROUPS.items():
        groups[name] = sum(buckets.get(member, 0.0) + project.get(member, 0.0)
                           + extras.get(member, 0.0) for member in members)
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
def compute_content(formula_code: str, variables: Optional[dict] = None, *,
                    rows=None) -> dict:
    """单条包材单件成本（Spec §2.10）：套 `PKG-P-*` 表达式（表达式内已 ÷ 装数）。

    修复第 1 批 §3.1：公式同样经 `resolve_formula`，库里 `reviewed` 的包材公式覆盖内置，
    且结果带 `formula_source` / `formula_version` / `rule_snapshot_version`。
    """
    code = _text(formula_code)
    inputs = dict(variables or {})
    if code not in FORMULA_CATALOG or FORMULA_CATALOG[code].get("cost_category") != "packaging":
        return {"formula_code": code, "content_code": _text(inputs.get("content_code")),
                "expression": "", "inputs": inputs, "amount": None,
                "min_charge_applied": False, "source": "kb",
                "formula_source": "builtin", "formula_version": "",
                "rule_snapshot_version": rule_snapshot_version(),
                "gap": {"code": "no_formula:%s" % code, "where": code,
                        "detail": "不是包材公式"}}
    entry = resolve_formula(code, rows=rows)
    trace = {"formula_source": _text(entry.get("formula_source")) or "builtin",
             "formula_version": _text(entry.get("formula_version")) or "1.0",
             "rule_snapshot_version": rule_snapshot_version()}
    units = _num(inputs.get("units_per_pack"))
    if not units or units <= 0:
        out = {"formula_code": code, "content_code": _text(inputs.get("content_code")),
               "expression": entry["expression"], "inputs": inputs, "amount": None,
               "min_charge_applied": False, "source": "formula",
               "gap": {"code": "invalid_units_per_pack", "where": code,
                       "detail": "装数为 0 或空，该包材行不计入"}}
        out.update(trace)
        return out
    merged, assumptions = _merge_variables(entry, inputs)
    try:
        value = _formula_value(entry["expression"], merged)
    except packaging_formula.FormulaError as exc:
        out = {"formula_code": code, "content_code": _text(inputs.get("content_code")),
               "expression": entry["expression"], "inputs": merged, "amount": None,
               "min_charge_applied": False, "source": "formula", "assumptions": assumptions,
               "gap": {"code": "content_formula_error:%s" % code, "where": code,
                       "detail": str(exc)}}
        out.update(trace)
        return out
    out = {"formula_code": code, "content_code": _text(inputs.get("content_code")),
           "expression": entry["expression"], "inputs": merged, "amount": value,
           "minimum_charge": 0.0, "min_charge_applied": False, "source": "formula",
           "assumptions": assumptions}
    out.update(trace)
    return out


#: 只有这一类缺口能因为「本单没绑上」而不进结论（Spec §2.2）；别的码一律照旧阻断。
UNBOUND_EXEMPT_GAP_PREFIX = "content_formula_error:"


def compute_packaging(rows, *, tax_factor: Any = 1.13, loss_uplift: Any = LOSS_UPLIFT,
                      yield_divisor: Any = YIELD_DIVISOR, rule_rows=None,
                      bound_content_codes=None) -> dict:
    """包材合计：逐条 `compute_content` 求和（Spec §2.10）。

    `bound_content_codes`（Spec `packaging-cost-gaps-scoped-to-order-contents` §2.1）由**调用方**算好
    传进来：命中即 `binding.status="bound"`，没命中是 `"unbound"`；**不传（None）＝ `"unknown"`，
    行为与今天逐字相同**（先立契约、后接数据的退路）。缺口据此分家（§2.2）：

    两个列表的每一项都是 `{"content_code", "binding_status", "gap"}`（行级缺口本身留在
    `lines[i]["gap"]`，一个字都没删）：

    · `bound_gaps`：进结论的缺口（`unbound` 但**不是** `content_formula_error:*` 的也在里面）；
    · `unbound_gaps`：只披露、不单独阻断的缺口（`unbound` 且是 `content_formula_error:*`）。

    `lines[i]["gap"]` 一个字都不删 —— 披露不许消失，只是不再单独参与结论。
    """
    bound = None if bound_content_codes is None else {_text(code) for code in bound_content_codes}
    lines: list = []
    bound_gaps: list = []
    unbound_gaps: list = []
    total = 0.0
    for row in (rows or []):
        variables = dict(row)
        variables["tax_factor"] = tax_factor
        variables["loss_uplift"] = loss_uplift
        variables["yield_divisor"] = yield_divisor
        result = compute_content(row.get("formula_code"), variables, rows=rule_rows)
        if not result.get("content_code"):
            result["content_code"] = _text(row.get("content_code"))
        code = _text(result.get("content_code"))
        if bound is None:
            status = "unknown"
        else:
            status = "bound" if code in bound else "unbound"
        result["binding"] = {"content_code": code, "status": status}
        lines.append(result)
        if result.get("amount") is not None:
            total += result["amount"]
        gap = result.get("gap")
        if not gap:
            continue
        entry = {"content_code": code, "binding_status": status, "gap": dict(gap)}
        if status == "unbound" and _text(gap.get("code")).startswith(UNBOUND_EXEMPT_GAP_PREFIX):
            unbound_gaps.append(entry)
        else:
            bound_gaps.append(entry)
    return {"amount": total, "lines": lines,
            "bound_gaps": bound_gaps, "unbound_gaps": unbound_gaps}


#: 包材绑定集合的来源闭集（Spec `packaging-cost-content-binding-source-disclosure.md` §2.1）：
#: `authoritative` = 有权威数据源；`none` = 今天没有（算不出来）。**不许**假装。
CONTENT_BINDING_SOURCES = ("authoritative", "none")


def bound_content_codes_detail(data: Any, *, rows=None) -> dict:
    """本单「绑上的包材项」集合 **+ 它是从哪来的**（Spec §2.1）—— **唯一** 一处算它的地方。

    这一版故意只给**空集**：仓库里还没有「这一单到底用哪几项包材」的权威数据源（BOM 的
    `packaging` 行目前只放物流规则，`3.5 包装与物流` 的字段也只是文本描述），
    而 Spec 明确写「算不出来就给空集（= 全部 unbound = 只披露不阻断），不许在成本引擎里另写一套
    猜哪一项用到的规则」。等权威绑定数据接入时，**只改这一个函数**。

    返回 `{"codes": [...], "source": "authoritative" | "none"}`：**今天必须老实报 `none`**
    —— "我没数据"不许在读接口上长得像"没有缺口"。
    """
    return {"codes": [], "source": "none"}


def bound_content_codes(data: Any, *, rows=None) -> set:
    """`bound_content_codes_detail()` 的**兼容包装**（只回集合，既有调用点行为不变）。"""
    return set(bound_content_codes_detail(data, rows=rows).get("codes") or ())


def _content_binding_of(codes: Any, unbound_gaps: Any) -> dict:
    """成本结果体里的 `content_binding`（Spec §2.2）：来源 + 绑上/没绑上的包材项。

    与 `compute_project()` 里那一份**同一形状**（读侧没有"这一趟算出的"缺口可回放，
    今天这一列还没落库，所以 `unbound_*` 从落库回来的那一份取，取不到就是空的 —— 绝不另猜）。

    `unbound_total` / `unbound_codes` **逐字来自这一趟算出的 `unbound_gaps`**（不许重算一份）；
    `unbound_codes` 按内容码去重升序 —— 报告要能**指名道姓**，不许只给一个总数（§2.4）。
    """
    bound = sorted({_text(code) for code in (codes or ()) if _text(code)})
    unbound_codes = sorted({_text(entry.get("content_code"))
                            for entry in (unbound_gaps or []) if isinstance(entry, dict)
                            and _text(entry.get("content_code"))})
    return {
        "source": "none",
        "bound_total": len(bound),
        "unbound_total": len(list(unbound_gaps or [])),
        "bound_codes": bound,
        "unbound_codes": unbound_codes,
    }


def _replayed_content_binding(row: Any) -> dict:
    """读回来的成本单里那一份**绑定账**（Spec `packaging-cost-content-binding-replay.md` §C2/§C3）。

    只**回放**算的时候落库的那一份（`content_binding_json`）：不再问当前的数据源、也不按现数据源
    重算 —— 与 `source_versions` 同一条纪律（"读的就是算时那一份"）。老成本单（本批之前落的）没有
    这一列 → 来源报**空串**：`""` 已经是既有前端的「未知档」，而 `"none"`（"没有权威数据源"）是
    **算的那一刻**的结论，老成本单不知道这件事，不许拿它顶上去。五个键与类型逐字不变。
    """
    stored = _loads(row.get("content_binding_json"), None)
    stored = stored if isinstance(stored, dict) and stored else {}
    if not stored:
        return {"source": "", "bound_total": 0, "unbound_total": 0,
                "bound_codes": [], "unbound_codes": []}

    def _count(value: Any) -> int:
        number = _num(value)
        return int(number) if (number is not None and number > 0) else 0

    def _codes(value: Any) -> list:
        items = value if isinstance(value, (list, tuple, set)) else []
        return sorted({_text(item) for item in items if _text(item)})

    return {
        "source": _text(stored.get("source")),
        "bound_total": _count(stored.get("bound_total")),
        "unbound_total": _count(stored.get("unbound_total")),
        "bound_codes": _codes(stored.get("bound_codes")),
        "unbound_codes": _codes(stored.get("unbound_codes")),
    }


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
#: 克重来源闭集（Spec `packaging-cost-gaps-closure` §2.3 / §3.2）。
GSM_SOURCES = ("property", "grade", "derived_from_thickness_density")
#: 属性表里厚度允许的单位（其余单位（cm / m / µm）一律**不推导**，Spec §3.1）。
_THICKNESS_MM_UNITS = ("", "mm", "毫米", "㎜")


def _material_rows() -> list:
    """材料主数据 + `kb_material_property`（按 `material_code` 只读 join）。

    属性表里有"厚度 / 密度"这类材料自身列放不下的属性（例如 `EVA 片材` 的 10mm 只写在
    属性表里）；不 join 就会让这类材料永远报 `material_gsm_missing`（Spec §1.3、§2.2）。
    join 只读、只加 `properties` 键，不改材料行的既有列。
    """
    rows = [dict(row) for row in kb_repo._table("kb_material")]
    by_code: dict = {}
    for prop in kb_repo._table("kb_material_property"):
        by_code.setdefault(_text(prop.get("material_code")), []).append(dict(prop))
    for row in rows:
        joined = by_code.get(_text(row.get("material_code")), [])
        embedded = row.get("properties")
        row["properties"] = joined or (embedded if isinstance(embedded, list) else [])
    return rows


def _thickness_from_text(text: str) -> Optional[float]:
    """`grade` / `spec` 里**明写**的厚度：`2.0mm` / `t2.0` / `t10`（Spec §2.1）。"""
    for pattern in (r"(?:^|[^0-9A-Za-z])t\s*(\d+(?:\.\d+)?)(?![\d.])",
                    r"(\d+(?:\.\d+)?)\s*mm\b"):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def _material_thickness_density(material: dict) -> tuple:
    """材料厚度（mm）与密度（g/cm³）。

    厚度来源依次：属性表 `thickness`（单位 mm 或空）→ `grade` / `spec` 里明写的
    `t2.0` / `2.0mm`；密度取 `kb_material.density` 或属性表 `density`。
    **任一项取不到就返回 (None, None)** —— 不许给默认密度、不许拿"常见值"顶（Spec §3.1）。
    """
    if not isinstance(material, dict):
        return None, None
    thickness, unit, density = None, "", _num(material.get("density"))
    for prop in (material.get("properties") or []):
        key = _text(prop.get("prop_key")).lower()
        if key == "thickness" and thickness is None:
            value = _num(prop.get("value_num"))
            if value is not None:
                thickness, unit = value, _text(prop.get("unit")).lower()
        elif key == "density" and density is None:
            value = _num(prop.get("value_num"))
            if value is not None:
                density = value
    if thickness is None:
        for text in (_text(material.get("grade")), _text(material.get("spec"))):
            value = _thickness_from_text(text)
            if value is not None:
                thickness, unit = value, "mm"
                break
    if thickness is None or density is None or density <= 0:
        return None, None
    if unit not in _THICKNESS_MM_UNITS:
        return None, None                                        # 单位不是 mm：不推导
    return thickness, density


def _material_gsm_detail(material: dict) -> tuple:
    """`(gsm, source)`；`source` ∈ `GSM_SOURCES`，取不到时 `(None, None)`（Spec §2.3）。"""
    if not isinstance(material, dict):
        return None, None
    for prop in (material.get("properties") or []):
        if _text(prop.get("prop_key")).lower() == "gsm":
            value = _num(prop.get("value_num"))
            if value is not None:
                return value, "property"
    grade = _text(material.get("grade"))
    match = re.search(r"\d+(?:\.\d+)?\s*g\b", grade, re.IGNORECASE)
    if match:
        return float(re.search(r"\d+(?:\.\d+)?", match.group(0)).group(0)), "grade"
    match = re.match(r"^(\d+(?:\.\d+)?)\s*g$", grade, re.IGNORECASE)
    if match:
        return float(match.group(1)), "grade"
    thickness, density = _material_thickness_density(material)
    if thickness is not None and density is not None:
        # 唯一允许的推导式：厚度(mm) × 密度(g/cm³) × 1000，四舍五入到 0.1（Spec §2.3）。
        return round(thickness * density * 1000, 1), "derived_from_thickness_density"
    return None, None


def _material_gsm(material: dict) -> Optional[float]:
    """兼容包装：只要克重，不要来源。既有调用点行为不变。"""
    return _material_gsm_detail(material)[0]


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


#: 价格单位口径（Spec `packaging-material-price-unit-truth.md` §C1）：
#: 材料公式的 `ton_price = 单价 × 1000`（`packaging-cost-engine.md` §2.3）已经把
#: "单价必须是 元/kg"写死进算式 —— 单位不是 kg 时**不许**照样 ×1000（那不是换算，是错算）。
MATERIAL_PRICE_UNIT_EXPECTED = "kg"
#: 写法 → 规范单位的闭集小表：只认 kg 与它的中文写法，`吨` / `t` / `ton` 一律不算别名。
MATERIAL_PRICE_UNIT_ALIASES = {"kg": "kg", "千克": "kg", "公斤": "kg"}
MATERIAL_PRICE_UNIT_STATUSES = ("ok", "unit_missing", "unit_mismatch")


def material_price_unit_status(price_row: Any) -> tuple:
    """一条价格行的计价单位核对（Spec `packaging-material-price-unit-truth.md` §C1）。

    只报事实、**不给默认单位**，返回 `(status, unit)`：
      · `("ok", "kg")` —— 单位归一后正是公式要求的 kg；
      · `("unit_missing", "")` —— 行里没写单位 / 写空 / 根本不是一条价格（没核对过）；
      · `("unit_mismatch", <原文>)` —— 写了单位但不是 kg（原文 trim 后回给用户）。
    """
    row = price_row if isinstance(price_row, dict) else {}
    value = row.get("unit")
    raw = _text(value) if isinstance(value, str) else ""
    if not raw:
        return "unit_missing", ""
    if MATERIAL_PRICE_UNIT_ALIASES.get(raw.lower()):
        return "ok", MATERIAL_PRICE_UNIT_EXPECTED
    return "unit_mismatch", raw


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
    item = _item(seq, line.get("cost_category") or "", part_code=line.get("part_code"),
                 part_name=line.get("part_name") or "", formula_code=line.get("formula_code"),
                 content_code=line.get("content_code"), tooling_code=line.get("tooling_code"),
                 rate_code=line.get("rate_code"), quantity_basis=line.get("quantity_basis"),
                 amount=line.get("amount"), loss_rate=line.get("loss_rate"),
                 min_charge_applied=bool(line.get("min_charge_applied")),
                 expression=expression or line.get("expression") or "",
                 inputs_json=json.dumps(inputs, ensure_ascii=False, default=str),
                 source_ref=source_ref, source=line.get("source") or source,
                 note=line.get("note") or "",
                 formula_version=line.get("formula_version"))
    for key in ("formula_source", "rule_snapshot_version"):
        if line.get(key) is not None:
            item[key] = line.get(key)
    # 这一行用的是哪几个默认值（Spec `packaging-cost-assumption-disclosure.md` §C1）：
    # `compute_line()` 已经逐字给了（`<name>=0903=<值>` / `<name>=默认=<值>`），这里只**原样带上**，
    # 不重排、不去重、不改文案；取不到给 `[]`（不许 None —— 落库那一列要写 `"[]"`）。
    rows = line.get("assumptions")
    item["assumptions"] = [str(text) for text in rows] if isinstance(rows, list) else []
    return item


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


# --------------------------------------------------------------------------- #
# 成本权威性：正式 / 暂定（Spec `e2e-packaging-downstream-handoff-report.md` §2.1）
#
# 线上那条 7.27 元/件的试算带着 24 个缺口（缺 GSM、无权威价、缺损耗率、缺公式、
# 模具分摊依据缺失、内容公式引用未绑定变量），但出价与落库都只有一句 `has_gaps` ——
# 「这份成本能不能用于正式报价」在数据里根本不是一个概念。这里把它补成一等公民：
#
#     · 缺口结构化：规则 + 变量 + 影响金额 + 补数入口（`missing_variable` /
#       `affected_amount` / `resolution_action`）；
#     · 结论二值化：`formal`（缺口清零）/ `provisional`（带缺口，只能按 POC 豁免流转）；
#     · 静默兜底一律拒绝：`reject_silent_zero_fallback()` 抓住"变量缺失、表达式却照出
#       金额"的行，命中就不许进正式成本。
#
# 本层不重算任何金额，只读 `compute_project()` 的结果。
# --------------------------------------------------------------------------- #
READINESS_VERSION = "packaging-cost-readiness/1"
READINESS_FORMAL = "formal"
READINESS_PROVISIONAL = "provisional"

#: 缺口码前缀 → 结构化字段（变量 / 补数入口 / 严重度）。有冒号的码按前缀匹配。
GAP_RESOLUTIONS = {
    "part_size_missing": {"missing_variable": ["length_mm", "width_mm"],
                          "resolution_action": "补零件展开尺寸（2.1 零件提取或盒型尺寸确认）",
                          "entry": "packaging-parts", "severity": "blocking"},
    "material_price_missing": {"missing_variable": ["price"],
                               "resolution_action": "在物料主数据里补该材料的权威单价",
                               "entry": "kb_material_price", "severity": "blocking"},
    # 单位不是 kg 时拦住这一行（Spec `packaging-material-price-unit-truth.md` §C3）：
    # 换算是业务口径，引擎不拍系数，只点名要改成什么。
    "material_price_unit_mismatch": {
        "missing_variable": ["price"],
        "resolution_action": ("把 kb_material_price 的计价单位改成 元/kg（或先换算成 元/kg 再入表）："
                              "公式按 元/kg → 元/吨（ton_price = 单价 × 1000）"),
        "entry": "kb_material_price", "severity": "blocking"},
    # 没写单位 → 不拦算（今天的行为不悄悄改），但必须在缺口里显形。
    "material_price_unit_missing": {
        "missing_variable": ["price"],
        "resolution_action": "给这条价格补上计价单位（元/kg）后再算",
        "entry": "kb_material_price", "severity": "advisory"},
    "material_gsm_missing": {"missing_variable": ["gsm"],
                             "resolution_action": "补材料克重/厚度换算（灰板必须给 GSM）",
                             "entry": "kb_material", "severity": "blocking"},
    "loss_rate_missing": {"missing_variable": ["loss_rate"],
                          "resolution_action": "补该材料的损耗率（或确认按 0 计并签字）",
                          "entry": "kb_cost_factor", "severity": "advisory"},
    "rate_missing": {"missing_variable": ["labor_rate", "equipment_rate"],
                     "resolution_action": "知识库补这条工费率",
                     "entry": "kb_cost_rate", "severity": "blocking"},
    "step_time_missing": {"missing_variable": ["standard_seconds", "labor_seconds"],
                          "resolution_action": "工序补标准工时（工艺路线页）",
                          "entry": "packaging-route", "severity": "blocking"},
    "tooling_basis_missing": {"missing_variable": ["tooling_basis"],
                              "resolution_action": "补模具/工装分摊依据与一次投入金额",
                              "entry": "kb_packaging_tooling", "severity": "blocking"},
    "no_formula": {"missing_variable": [], "resolution_action": "该类别还没有公式：先抽规则再算",
                   "entry": "packaging_cost_rules.json", "severity": "blocking"},
    "content_formula_error": {"missing_variable": [], "resolution_action": "绑定包材公式引用的变量",
                              "entry": "packaging_cost_formula", "severity": "blocking"},
    "invalid_units_per_pack": {"missing_variable": ["units_per_pack"],
                               "resolution_action": "补装数（units_per_pack）",
                               "entry": "kb_packaging_cost_content", "severity": "blocking"},
    "freight_rule_missing": {"missing_variable": ["loading_rate"],
                             "resolution_action": "补运输规则",
                             "entry": "kb_packaging_logistics_rule", "severity": "advisory"},
    "below_moq": {"missing_variable": [], "resolution_action": "确认是否按 MOQ 数量报价（业务裁决）",
                  "entry": "quote", "severity": "advisory"},
    # 部件行的用量（Spec `packaging-cost-part-usage-not-applied.md` §2.2）：BOM 行没给可用用量时
    # 材料行仍按 1 件出账，但必须披露 —— 「8 个/套」不许被按 1 件算得无声无息。
    "usage_qty_missing": {"missing_variable": ["quantity"], "resolution_action": "补这一件的用量",
                          "entry": "packaging-bom", "severity": "advisory"},
}

#: 这些"解决方式"等于把缺口按 0 糊过去 —— 不算补齐（Spec §2.1）。
SILENT_ZERO_RESOLUTIONS = ("manual_zero", "assume_zero", "skip", "ignore", "waive_without_signoff")


def _gap_rule(code: str) -> dict:
    text = _text(code)
    if not text:
        return {}
    if text in GAP_RESOLUTIONS:
        return dict(GAP_RESOLUTIONS[text])
    head = text.split(":", 1)[0]
    return dict(GAP_RESOLUTIONS.get(head) or {})


def gap_variables(gap: Any) -> list:
    """这条缺口点名了哪些缺失变量（表里有就用表里的；没有就从报错文案里解析）。"""
    payload = gap if isinstance(gap, dict) else {}
    rule = _gap_rule(payload.get("code"))
    names = [str(name) for name in (rule.get("missing_variable") or []) if str(name)]
    detail = _text(payload.get("detail"))
    # 内容公式报错会把未绑定变量名写在文案里（`unbound variable: gsm` 之类）。
    for name in expression_variables(detail):
        if name in LINE_VARIABLES and name not in names:
            names.append(name)
    return names


def reject_silent_zero_fallback(gap: Any, item: Any = None, resolution: Any = None) -> bool:
    """这条缺口是不是被「静默按 0」糊过去了（Spec §2.1）—— 命中就不许进正式成本。

    两个判据，任一命中即拒绝：

      · 缺口点名了缺失变量，而对应明细行**照样出了金额**（那格在表达式里被当成 0 算
        进去了；金额看着有，其实少算）；
      · 缺口被标成"已解决"，但解决方式是手填 0 / 假定 0 / 直接跳过。
    """
    if isinstance(resolution, dict) and resolution:
        kind = _text(resolution.get("kind"))
        if kind in SILENT_ZERO_RESOLUTIONS:
            return True
    payload = gap if isinstance(gap, dict) else {}
    row = item if isinstance(item, dict) else {}
    if not row:
        return False
    if _num(row.get("amount")) is None:
        return False
    if not _text(row.get("expression")):
        return False
    variables = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
    empty = sorted(str(key) for key, value in variables.items() if value is None)
    if not empty:
        return False
    names = {_canon(name) for name in gap_variables(payload)}
    if not names:
        return True
    return any(_canon(name) in names for name in empty)


def _item_for_gap(cost: dict, gap: dict) -> dict:
    where = _text(gap.get("where"))
    if not where:
        return {}
    for item in (cost.get("items") or []):
        if not isinstance(item, dict):
            continue
        if _text(item.get("part_code")) == where or _text(item.get("part_name")) == where:
            return item
    return {}


def gap_evidence(cost: Any, gap: Any) -> dict:
    """一条缺口的结构化证据：规则 / 变量 / 物料 / 影响金额 / 补数入口（Spec §2.1）。"""
    payload = cost if isinstance(cost, dict) else {}
    row = gap if isinstance(gap, dict) else {}
    rule = _gap_rule(row.get("code"))
    item = _item_for_gap(payload, row)
    amount = _num(item.get("amount"))
    where = _text(row.get("where"))
    return {
        "code": _text(row.get("code")),
        "where": where,
        "detail": _text(row.get("detail")),
        "missing_variable": gap_variables(row),
        "material": _text(item.get("part_name") or item.get("material") or where),
        # 影响金额：有明细行就给那一行当前金额（少算的**上界**），算不出来就 None ——
        # 绝不为了"看起来完整"编一个数。
        "affected_amount": amount,
        "affected_amount_status": "line_amount" if amount is not None else "unknown",
        "resolution_action": _text(rule.get("resolution_action")),
        "resolution_entry": _text(rule.get("entry")),
        "severity": _text(rule.get("severity")) or "advisory",
        "silent_zero_fallback": reject_silent_zero_fallback(row, item),
        "rule_snapshot_version": _text(payload.get("rule_snapshot_version")),
    }


def packaging_cost_readiness_gate(cost: Any) -> dict:
    """这份成本是 `formal` 还是 `provisional`（Spec §2.1）—— 缺口的唯一裁决点。

    缺口清零且没有被静默兜底的行 → `formal`；否则 `provisional`：可以按 POC 豁免流转，
    但**不得**标成正式成本、不得在报价或报告里隐藏缺口。
    """
    payload = cost if isinstance(cost, dict) else {}
    gaps = [gap for gap in (payload.get("gaps") or []) if isinstance(gap, dict)]
    # 包材绑定数据源的来源（Spec `packaging-cost-content-binding-source-disclosure.md` §2.3）：
    # 键**必须存在**，取不到给 `""`。这只是"说出来"，**不改 verdict 口径**。
    binding = payload.get("content_binding")
    binding = binding if isinstance(binding, dict) else {}
    binding_source = _text(binding.get("source"))
    # 「没绑上本单」的包材缺口只报数（Spec §2.4）：不进 verdict、不进 blocking_total。
    unbound_rows = [gap for gap in (payload.get("gaps_unbound_to_order") or [])
                    if isinstance(gap, dict)]
    # 读回来的成本单没有 `gaps_unbound_to_order` 那一栏（它只在算完那一趟的结果体里）→ 退回
    # **回放的**那一份绑定账（Spec `packaging-cost-content-binding-replay.md` §C4）：算完那一趟
    # 两个数逐字相等（`content_binding.unbound_total` 就是它的条数），故既有口径一字不动。
    replayed_unbound = _num(binding.get("unbound_total"))
    unbound_total = (len(unbound_rows) if unbound_rows
                     else (int(replayed_unbound)
                           if (replayed_unbound is not None and replayed_unbound > 0) else 0))
    evidence = [gap_evidence(payload, gap) for gap in gaps]
    blocking = [row for row in evidence if row["severity"] == "blocking"]
    # 提示性缺口（Spec `packaging-cost-readiness-severity-layering` §2.2）：只披露、不决定结论。
    advisories = [row for row in evidence if row["severity"] != "blocking"]
    silent = [row for row in evidence if row["silent_zero_fallback"]]

    def _amount(rows) -> float:
        return sum(row["affected_amount"] or 0.0 for row in rows
                   if row["affected_amount"] is not None)

    quantified = _amount(evidence)
    reasons: list = []
    if blocking:
        reasons.append("%d 项阻断缺口未清零" % len(blocking))
    if silent:
        reasons.append("%d 行存在静默按 0 兜底：%s"
                       % (len(silent), "、".join(sorted({row["code"] for row in silent}))))
    if advisories:
        # §2.2：带提示缺口的正式成本也要有一句 —— 提示必须看得见，不许静默。
        reasons.append("%d 项提示缺口（不影响正式/暂定）" % len(advisories))
    if binding_source == "none" and unbound_total > 0:
        # §2.3：必须回答"为什么这些包材缺口没进阻断" —— 否则读的人只看到"没有阻断缺口"，
        # 而真相是"我们不知道这一单用了哪几项包材"。
        reasons.append("包材绑定数据源缺失：%d 条包材缺口只披露不阻断" % unbound_total)
    unbuilt = not gaps and payload.get("built") is False
    if unbuilt:
        reasons.append("成本尚未测算")
    # §2.1：结论只由 **阻断缺口 / 静默按 0 / 尚未测算** 决定，advisory 不再参与。
    verdict = READINESS_PROVISIONAL if (blocking or silent or unbuilt) else READINESS_FORMAL
    return {
        "version": READINESS_VERSION,
        # 键**必须永远存在**（Spec §2.3）：空 payload 也要有，取不到给 `""`。
        "content_binding_source": binding_source,
        "verdict": verdict,
        "formal_ready": verdict == READINESS_FORMAL,
        "has_gaps": bool(gaps),
        "gap_total": len(evidence),
        "blocking_total": len(blocking),
        "unbound_total": unbound_total,
        "advisory_total": len(advisories),
        "advisories": advisories,
        "silent_zero_total": len(silent),
        "rejected_silent_zero_fallback": bool(silent),
        "blocking_amount_total": round(_amount(blocking), 6),
        "advisory_amount_total": round(_amount(advisories), 6),
        "affected_amount_total": round(quantified, 6),
        "unquantified_total": sum(1 for row in evidence
                                  if row["affected_amount"] is None),
        "gaps": evidence,
        "reasons": reasons,
        "rule_snapshot_version": _text(payload.get("rule_snapshot_version")),
        "estimate_id": _text(payload.get("estimate_id")),
    }


def formal_cost_or_raise(cost: Any, waiver: Any = None) -> dict:
    """正式成本才放行；带缺口必须有 POC 豁免签字（Spec §2.1）。

    `waiver` 只表达"业务签字带缺口继续"，缺口本身以服务端算出的为准（调用方不得
    用前端传来的缺口清单替换这里的结果）。
    """
    gate = packaging_cost_readiness_gate(cost)
    if gate["formal_ready"]:
        return gate
    if isinstance(waiver, dict) and (waiver.get("signed_by") or waiver.get("reason")):
        return {**gate, "waived": True,
                "waived_by": _text(waiver.get("signed_by") or waiver.get("by")),
                "waiver_reason": _text(waiver.get("reason"))}
    raise CostError(
        "这份成本还是暂定（%s）：%s。补齐缺口，或由业务签字带缺口放行"
        % (gate["verdict"], "；".join(gate["reasons"]) or "存在未清零缺口"),
        409, "packaging_cost_not_formal")


def compute_project(project_id: str, requirement_no: str = "", *,
                    scenario: Optional[dict] = None, actor: Any = None) -> dict:
    """组装 → 逐行算 → 三层汇总（**不落库**，Spec §4.5）。

    ``actor`` 只用于留痕（`computed_by` / `computed_by_role`，Spec §2.3），不参与算式、
    不做权限判定 —— 权限在路由那一层由 `COST_WRITE_ROLES` 说完。
    """
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
    #: Spec 修复第 1 批 §3.4：一次计算只读一次规则表，读到的 rows 传给每一行，
    #: 保证同一份成本明细里公式版本一致。
    formula_rows = [dict(row) for row in kb_repo._table("kb_packaging_cost_formula")]
    rule_version = rule_snapshot_version()
    # 「这份价照哪一版规则算的」的来源也要留痕（Spec
    # `packaging-cost-and-handoff-static-downgrade-disclosure.md` §2.2）：三态不许压成一个空串。
    rule_detail = rule_snapshot_version_detail()
    upstream_detail = upstream_route_version_detail(project_id, req_no)
    # 业务部件层（Spec `packaging-business-parts-and-cad-plan-view.md` §7）：成本消费的部件
    # 集合是业务部件，几何分量只作证据；这份 scope 只披露版本与缺口，不改任何公式。
    business_scope = _business_parts_scope(project_id)
    gaps: list = []
    assumptions: list = []

    def loss_rate_with_source(material_text: str, material=None) -> tuple:
        if req_loss is not None:
            return req_loss, None
        return loss_rate_detail(material_text, rows=factor_rows, material=material)

    def loss_rate_for(material_text: str) -> Optional[float]:
        return loss_rate_with_source(material_text)[0]

    lines: list = []
    tooling_lines: list = []

    # 1) 部件 × 材料 -------------------------------------------------------- #
    materials = _material_rows()
    entry_material = resolve_formula("PKG-C-MATERIAL", rows=formula_rows)
    machine_length = _num(data.get("machine_length"))
    machine_width = _num(data.get("machine_width"))
    imposition = _num(data.get("imposition_count")) or 1.0
    for row in bom_rows:
        if _text(row.get("bom_category")) not in ("box_part", "optional_part"):
            continue
        part_code = _text(row.get("part_code") or row.get("item_key"))
        part_name = _text(row.get("item_name"))
        material_text = _text(row.get("material") or row.get("item_name"))
        # 这一行的用量（Spec `packaging-cost-part-usage-not-applied.md` §2.1）：部件行「8 个/套」
        # 不许按 1 件算 —— 用量真的乘进材料金额；取不到就按 1 计并披露（§2.2）。
        usage_qty, usage_missing = _part_usage_qty(row)
        length = _num(row.get("length_mm"))
        width = _num(row.get("width_mm"))
        material = _resolve_material(row, materials)
        gsm, gsm_source = _material_gsm_detail(material) if material else (None, None)
        price = None
        price_row = None
        if material:
            price_row = _material_price(_text(material.get("material_code")))
            if price_row:
                price = _num(price_row.get("price"))
        # 计价单位核对（Spec `packaging-material-price-unit-truth.md` §C2）：读不到就是读不到，
        # 不给默认单位；结果逐行留痕，事后能回答"这一版按什么单位算的"。
        unit_status, price_unit = material_price_unit_status(price_row)
        variables = {"cut_length": length, "cut_width": (width + 5) if width is not None else None,
                     "gsm": gsm, "gsm_source": gsm_source,
                     "price_unit": price_unit, "price_unit_status": unit_status,
                     "ton_price": (price * 1000) if price is not None else None,
                     "imposition_count": imposition, "proof_base": _num(data.get("proofing_base")) or 0.0,
                     "quote_quantity": quantity, "tax_factor": tax_factor,
                     "usage_qty": usage_qty,
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
        elif unit_status == "unit_mismatch":
            # 单位不是 kg：`ton_price = 单价 × 1000` 会把吨价放大 1000 倍 —— 拦住，不猜系数。
            gaps.append({"code": "material_price_unit_mismatch", "where": part_code,
                         "detail": "材料「%s」的价格单位是「%s」，公式按 元/kg 计价"
                                   "（ton_price = 单价 × 1000），单位不对不出金额"
                                   % (material_text, price_unit)})
        elif gsm is None:
            gaps.append({"code": "material_gsm_missing", "where": part_code,
                         "detail": "材料「%s」解析不到克重，材料行不出金额" % material_text})
        else:
            result = compute_line("material", variables, rows=formula_rows)
        if unit_status == "unit_missing" and price is not None:
            # advisory：不拦算（Spec §C2 / §6.3），只让"单位没核对过"显形。
            gaps.append({"code": "material_price_unit_missing", "where": part_code,
                         "detail": "材料「%s」的价格没有计价单位，本行按 元/kg 使用"
                                   "（ton_price = 单价 × 1000），请补上计价单位后再用"
                                   % material_text})
        amount = result.get("amount") if result else None
        # 金额 = 该行**单件**材料费 × 该行用量（Spec §2.1）；`expression` 仍是工作簿原文，
        # 用量绝不塞进表达式字符串（§2.1 / §2.3）。
        if amount is not None:
            amount = amount * usage_qty
        expression = result.get("expression") if result else entry_material["expression"]
        line = {"cost_category": "material", "part_code": part_code, "part_name": part_name,
                "formula_code": entry_material["formula_code"], "rate_code": "",
                "expression": expression, "inputs": variables, "amount": amount,
                "min_charge_applied": False, "source": "formula",
                "items_inputs": variables}
        line.update(_trace_fields(entry_material, result=result, snapshot=rule_version))
        if usage_missing:
            gaps.append({"code": "usage_qty_missing", "where": part_code,
                         "detail": "部件「%s」没有可用用量，材料行按 1 件计；补这一件的用量"
                                   % part_name})
        rate, rate_source = loss_rate_with_source(material_text, material)
        if rate is None and amount is not None:
            gaps.append({"code": "loss_rate_missing", "where": part_code,
                         "detail": "材料「%s」取不到损耗率，损耗按 0 计（金额保留）" % material_text})
        line["loss_rate"] = rate
        if rate_source:
            # §2.4 留痕：只有材料行带 loss_rate_source，工序 / 人工行不带。
            variables["loss_rate_source"] = rate_source
            line["inputs"] = variables
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
        codes = formula_codes_for(category)
        if not codes:
            gaps.append({"code": "no_formula:%s" % category, "where": name,
                         "detail": "类别 %s 在 0903 里是手填列，本批没有公式" % category})
            continue
        entry = resolve_formula(codes[0], rows=formula_rows)
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
        #: 缺上机尺寸只在**表达式真的要用**它时才是缺口：库里的 reviewed 公式可能
        #: 只吃数量（Spec 修复第 1 批 §3.1 起的唯一入口语义），此时不该被这条拦掉。
        needs_sheet = bool(set(expression_variables(entry["expression"]))
                           & {"machine_length", "machine_width"})
        if sheet_length is None and needs_sheet and category in ("print_uv", "lamination", "glue"):
            gaps.append({"code": "part_size_missing", "where": name,
                         "detail": "缺上机尺寸，%s 行不出金额" % category})
            gap_line = {"cost_category": category, "part_code": None, "part_name": name,
                        "formula_code": entry["formula_code"], "amount": None,
                        "min_charge_applied": False, "loss_rate": None,
                        "expression": entry["expression"], "inputs": variables,
                        "source": "formula"}
            gap_line.update(_trace_fields(entry, snapshot=rule_version))
            lines.append(gap_line)
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
        result = compute_line(category, variables, rows=formula_rows)
        if result.get("gap"):
            gaps.append(dict(result["gap"], where=result["gap"].get("where") or name))
        line = {"cost_category": category, "part_code": None, "part_name": name,
                "formula_code": entry["formula_code"], "rate_code": rate_code,
                "expression": entry["expression"], "inputs": result.get("inputs") or variables,
                "amount": result.get("amount"),
                "min_charge_applied": bool(result.get("min_charge_applied")),
                "source": "formula"}
        line.update(_trace_fields(entry, result=result, snapshot=rule_version))
        rate = loss_rate_for(name)
        if rate is None and line["amount"] is not None:
            gaps.append({"code": "loss_rate_missing", "where": name,
                         "detail": "工序「%s」取不到损耗率，损耗按 0 计（金额保留）" % name})
        line["loss_rate"] = rate
        lines.append(line)

    # 3) 人工（标准工时 × 工时费率，逐工序一行） --------------------------- #
    overhead_row = _rate_row("RATE-PKG-OVERHEAD")
    overhead_rate = _num(overhead_row.get("value")) if overhead_row else 0.0
    entry_labor = resolve_formula("PKG-C-LABOR", rows=formula_rows)
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
            gap_line = {"cost_category": "labor", "part_code": None, "part_name": name,
                        "formula_code": entry_labor["formula_code"], "rate_code": rate_code,
                        "expression": entry_labor["expression"], "inputs": variables,
                        "amount": None, "min_charge_applied": False, "loss_rate": None,
                        "source": "formula", "source_ref": "step:%s" % step_no}
            gap_line.update(_trace_fields(entry_labor, snapshot=rule_version))
            lines.append(gap_line)
            continue
        if rate_row is None:
            gaps.append({"code": "rate_missing:%s" % rate_code, "where": name,
                         "detail": "知识库缺工时费率 %s" % rate_code})
            gap_line = {"cost_category": "labor", "part_code": None, "part_name": name,
                        "formula_code": entry_labor["formula_code"], "rate_code": rate_code,
                        "expression": entry_labor["expression"], "inputs": variables,
                        "amount": None, "min_charge_applied": False, "loss_rate": None,
                        "source": "formula", "source_ref": "step:%s" % step_no}
            gap_line.update(_trace_fields(entry_labor, snapshot=rule_version))
            lines.append(gap_line)
            continue
        variables["labor_rate"] = _num(rate_row.get("value"))
        result = compute_line("labor", variables, rows=formula_rows)
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
        line.update(_trace_fields(entry_labor, result=result, snapshot=rule_version))
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
    # 绑定集合的口径**只有一处**（Spec §2.3）：调用方算好传进来；这一版还没有「哪一项用到」
    # 的算法，按 Spec 给**空集** —— 全部 unbound = 只披露、不单独阻断，不许在这里现猜。
    bound_detail = bound_content_codes_detail(data)
    bound_codes = set(bound_detail.get("codes") or ())
    packaging = compute_packaging(kb_repo.packaging_cost_contents(),
                                  bound_content_codes=bound_codes,
                                  tax_factor=tax_factor,
                                  loss_uplift=LOSS_UPLIFT, yield_divisor=YIELD_DIVISOR,
                                  rule_rows=formula_rows)
    for entry in (packaging.get("bound_gaps") or []):
        gaps.append(dict(entry.get("gap") or {}))
    gaps_unbound_to_order = [dict(entry.get("gap") or {},
                                 content_code=entry.get("content_code"),
                                 binding_status="unbound")
                             for entry in (packaging.get("unbound_gaps") or [])]
    # 包材绑定数据源缺失必须自报家门（Spec `packaging-cost-content-binding-source-disclosure.md`
    # §2.2）：`bound_gaps = []` 与"这一单真的没有缺口"在读接口上必须分得开。
    # `unbound_total` / `unbound_codes` 逐字来自上面那一份 `gaps_unbound_to_order`（不重算一份），
    # 且**逐条指名道姓**列出被降级披露的包材项（§2.4）—— 只给总数的话报告没法点到具体包材项。
    content_binding = {
        "source": _text(bound_detail.get("source")),
        "bound_total": len(bound_codes),
        "unbound_total": len(gaps_unbound_to_order),
        "bound_codes": sorted(_text(code) for code in bound_codes if _text(code)),
        "unbound_codes": sorted({_text(entry.get("content_code"))
                                 for entry in gaps_unbound_to_order
                                 if _text(entry.get("content_code"))}),
    }

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
        "rule_snapshot_version": rule_version,
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
        # 整单这本账的两个计数（Spec `packaging-cost-assumption-disclosure.md` §C3）：与读侧同一个
        # `_assumption_counts()`，只数明细行上那一份，不额外现算默认值。
        **_assumption_counts(items),
        # 「没绑上本单」的包材缺口（Spec §2.3）：进出参只披露，**不参与 verdict**。
        "gaps_unbound_to_order": gaps_unbound_to_order,
        "content_binding": content_binding,
        # 谁算的（Spec `packaging-cost-finance-access.md` §2.3）：留痕跟着记录走，
        # 事后不必翻审计表猜。
        "computed_by": _actor_name(actor), "computed_by_role": _actor_role(actor),
        # **算的那一刻**照的输入版本（Spec `packaging-cost-input-version-pinning.md` §2.2）：
        # 路线版本 / BOM 指纹 / BOM 行数一起落库。字段名说的是"照着哪一版算的"，
        # 就必须在读接口那一刻**不再重取**（读侧的比对见 `load_cost()`）。
        "source_versions": {
            "route_version": _upstream_route_version(project_id, req_no),
            "engine_version": ENGINE_VERSION,
            "bom_hash": bom_input_hash(bom_rows),
            "bom_item_total": len(bom_rows),
            # 路线版本那条轴的三态与规则快照的来源（Spec §2.2/§2.3）：读挂与"确实没有"
            # 不许同形；读侧把**存的**这一份原样带回。
            "route_version_source": upstream_detail["source"],
            "route_version_unavailable": _route_unavailable_of(upstream_detail),
            "rule_snapshot_source": _text(rule_detail.get("source")),
            "rule_snapshot_unavailable_reason": _text(rule_detail.get("reason")),
            # 成本是照哪一版**业务部件**清单算的（Spec
            # `packaging-business-parts-and-cad-plan-view.md` §7）：没有清单时给 ""，
            # 与上面的路线/BOM 版本并列，下游据此判 stale。
            "business_parts_id": business_scope["business_parts_id"],
            "business_parts_hash": business_scope["business_parts_hash"],
        },
        "rule_snapshot_source": _text(rule_detail.get("source")),
        "rule_snapshot_unavailable": rule_snapshot_unavailable_of(rule_detail),
        # 业务部件披露（Spec `packaging-business-parts-and-cad-plan-view.md` §7/§8）：键**必须
        # 存在**。有清单给版本与件数；没有清单 `gap` 非空（`business_parts_missing`）。
        "business_parts_id": business_scope["business_parts_id"],
        "business_parts": {
            "available": business_scope["available"],
            "business_part_total": business_scope["business_part_total"],
            "business_parts_hash": business_scope["business_parts_hash"],
            "gap": dict(business_scope["gap"] or {}),
        },
    }


def _route_unavailable_of(detail: dict) -> dict:
    """`upstream_route_version_detail()` → 披露键（正常 `{}`）。"""
    if _text((detail or {}).get("source")) == "unavailable":
        return {"code": "route_version_unavailable",
                "reason": _text((detail or {}).get("reason"))}
    return {}


def bom_input_hash(rows: Any) -> str:
    """BOM 行的**内容**指纹（Spec `packaging-cost-input-version-pinning.md` §2.1）。

    范式照 `packaging_parts._record_hash`：`sha256_hex(canonical_json(json_safe(...)))`。
    输入前**按稳定键排序** —— 行序不是内容，同一批行换个顺序必须是同一个指纹。
    空输入给 `""`：**"没有 BOM" 不是一版内容**，不许拿它当"某一版"。
    """
    from .packaging_semantics import model as sem_model
    safe = [sem_model.json_safe(row) for row in list(rows or [])]
    if not safe:
        return ""
    safe.sort(key=sem_model.canonical_json)
    return sem_model.sha256_hex(sem_model.canonical_json(safe))


def _with_readiness(result: dict) -> dict:
    """给成本结果挂上正式/暂定裁决（Spec §2.1）：出价、落库、报告都读同一个字段。"""
    payload = dict(result or {})
    payload["readiness"] = packaging_cost_readiness_gate(payload)
    return payload


def _business_parts_scope(project_id: str) -> dict:
    """成本消费的**业务部件**版本（Spec `packaging-business-parts-and-cad-plan-view.md` §7）。

    成本该遍历的是 `business_parts`（对照表里的 28 个），不是 CAD 连通分量。这里只做
    **披露**（版本 + 可用性 + 缺口），键**必须存在**：

    - 有清单 → `business_parts_id/hash` 与件数落进 `source_versions`，重新导入清单后
      旧成本单可判 stale；
    - 没有清单 → `gap = business_parts_missing`（"已识别几何区域 n 个，尚未形成业务部件
      清单"），**不许**用几何件数冒充业务件数，也不许静默当成"没有部件"。
    """
    blank = {"business_parts_id": "", "business_parts_hash": "", "business_part_total": 0,
             "available": False, "gap": {}}
    try:
        from . import packaging_parts
        doc = packaging_parts.load_business_parts(project_id)
    except Exception as exc:                            # noqa: BLE001 - 读不到要披露
        blank["gap"] = {"code": "business_parts_document_unavailable",
                        "reason": type(exc).__name__,
                        "message": "业务部件清单读不到（%s）：这次判不了这份成本是按哪一版业务"
                                   "部件算的，别把这次当成「没有业务部件」" % type(exc).__name__}
        return blank
    if not isinstance(doc, dict):
        blank["gap"] = packaging_parts.business_parts_gap({})
        return blank
    rows = doc.get("business_parts") if isinstance(doc.get("business_parts"), list) else []
    stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
    return {"business_parts_id": _text(doc.get("business_parts_id")),
            "business_parts_hash": _text(doc.get("business_parts_hash")),
            "business_part_total": int(stats.get("business_part_total") or len(rows)),
            "available": bool(rows),
            "gap": {} if rows else packaging_parts.business_parts_gap_of(doc)}


def build_cost(project_id: str, requirement_no: str = "", actor: Any = None, *,
               scenario: Optional[dict] = None) -> dict:
    """算 + 落库：同一 `(项目, 需求单, 场景)` 先删明细再重建，写项目审计（Spec §3.2）。"""
    cost = _with_readiness(compute_project(project_id, requirement_no,
                                           scenario=scenario, actor=actor))
    da_repo.save_packaging_cost(cost["project_id"], cost["requirement_no"],
                                cost["scenario_code"], cost, cost["items"])
    store.audit(project_id, "workflow:packaging_cost_rebuilt",
                {"requirement_no": cost["requirement_no"], "scenario_code": cost["scenario_code"],
                 "total_cost": cost["total_cost"], "has_gaps": cost["has_gaps"],
                 "by": _actor_name(actor)})
    return cost


def _assumption_counts(items) -> dict:
    """从**回放后的明细行**数这本账（Spec `packaging-cost-assumption-disclosure.md` §C3）。

    `assumptions_total` = 各明细行 assumptions 条数之和；`assumptions_0903_total` = 其中含
    `=0903=` 的条数（"这一单有几个数是 0903 的常量"）。只数已经回放/算好的那一份 ——
    读侧不许为了这两个计数去现算默认值。
    """
    total = 0
    from_0903 = 0
    for item in list(items or []):
        rows = item.get("assumptions") if isinstance(item, dict) else None
        if not isinstance(rows, list):
            continue
        total += len(rows)
        from_0903 += sum(1 for text in rows if "=0903=" in str(text))
    return {"assumptions_total": total, "assumptions_0903_total": from_0903}


def _rehydrate(row: dict, items: list) -> dict:
    stored = [dict(item) for item in items]
    # 明细行那一份默认值清单逐字回放（Spec `packaging-cost-assumption-disclosure.md` §C2）：
    # 读的就是算时存下的 `assumptions_json`；老明细行 / 解不出 / 不是列表 → 空清单，键仍在。
    for item in stored:
        rows = _loads(item.get("assumptions_json"), [])
        item["assumptions"] = rows if isinstance(rows, list) else []
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
        # 整单这本账的两个计数（Spec `packaging-cost-assumption-disclosure.md` §C3）：
        # 只从上面**回放后的明细行**数，读侧不现算。
        **_assumption_counts(stored),
        "computed_by": _text(row.get("computed_by")),
        "computed_by_role": _text(row.get("computed_by_role")),
        # 读侧也要说得出"绑定数据源是什么"（Spec
        # `packaging-cost-content-binding-source-disclosure.md` §2.2）：来源由**同一个函数**给
        # （写侧那一趟）；读回来时**逐字回放**落库的那一份（Spec
        # `packaging-cost-content-binding-replay.md` §C2）——不再去问当前的数据源。
        "content_binding": _replayed_content_binding(row),
    }


def _rehydrate_with_readiness(result: dict) -> dict:
    return _with_readiness(result)


def load_cost(project_id: str, requirement_no: str = "", *,
              scenario: Optional[str] = None) -> dict:
    """读回成本测算 + 24 类别 + 10 分组 + 缺口；没算过时 `built=false`，不报错。"""
    req_no = _resolve_requirement_no(project_id, requirement_no)
    row = da_repo.load_packaging_cost(project_id, req_no, _text(scenario))
    upstream, route_unavailable = _route_version_and_availability(project_id, req_no)
    rule_detail = rule_snapshot_version_detail()
    if not row:
        # 「还没算」不是「过期」（Spec `packaging-cost-input-version-pinning.md` §2.2 末条）：
        # 这条路径不许报 `provenance_missing`，也不许给 stale —— 除了三个新增键，逐字不变。
        return _with_readiness(
            {"built": False, "project_id": project_id, "requirement_no": req_no,
             "scenario_code": _text(scenario) or "default", "engine_version": ENGINE_VERSION,
             "source_versions": {"route_version": upstream, "engine_version": ENGINE_VERSION,
                                 "route_version_source": (
                                     "unavailable" if route_unavailable
                                     else ("route" if _text(upstream) else "none")),
                                 "route_version_unavailable": dict(route_unavailable or {}),
                                 "rule_snapshot_source": _text(rule_detail.get("source")),
                                 "rule_snapshot_unavailable_reason": _text(rule_detail.get("reason"))},
             "rule_snapshot_source": _text(rule_detail.get("source")),
             "rule_snapshot_unavailable": rule_snapshot_unavailable_of(rule_detail),
             "cost_profile": COST_PROFILE, "items": [], "gaps": [], "assumptions": [],
             # 还没算过 → 这本账的两个计数给 0（Spec §C3）：不许 null、不许抛错。
             "assumptions_total": 0, "assumptions_0903_total": 0,
             "has_gaps": False, "categories": {code: 0.0 for code, _ in COST_CATEGORIES},
             "report_groups": {name: 0.0 for name in REPORT_GROUPS},
             "subtotal": 0.0, "loss_amount": 0.0, "tooling_total": 0.0,
             "packaging_total": 0.0, "freight_total": 0.0, "total_cost": 0.0,
             "stale": False, "stale_reasons": [], "bom_unavailable": {},
             # 「当前路线版本读不到」与「确实没有确认版本」分开（Spec §2.1）：没算过也要有键。
             "route_unavailable": dict(route_unavailable or {}),
             # 业务部件清单那条轴（Spec `packaging-business-parts-version-pinning.md` §2.3）：
             # 没算过当然没有版本漂移，但键**必须存在**。
             "business_parts_unavailable": {}})
    items = da_repo.load_packaging_cost_items(row["estimate_id"])
    result = _rehydrate_with_readiness(_rehydrate(row, items))
    # 输入版本的埋点（Spec `packaging-cost-input-version-pinning.md` §2.2）：读侧**只读存的
    # 那一份**（`source_versions_json`）—— 不再用 `_upstream_route_version()` 的现值覆盖，
    # 否则字段名不副实：路线重确认一次，旧成本单的追溯字段就跟着变。
    stored = _loads(row.get("source_versions_json"), {})
    stored = dict(stored) if isinstance(stored, dict) else {}
    result["source_versions"] = stored
    # "这份成本照哪一版规则算的"的来源（Spec §2.2/§3.1）：**存的**那一份里优先，
    # 本批之前算的没有那个键 → 按"有版本号 kb / 没版本号 none"兜底（历史行无法回溯）。
    stored_source = _text(stored.get("rule_snapshot_source")) or (
        "kb" if _text(row.get("rule_snapshot_version")) else "none")
    result["rule_snapshot_source"] = stored_source
    if stored_source == "unavailable":
        result["rule_snapshot_unavailable"] = {
            "code": "rule_snapshot_unavailable",
            "reason": _text(stored.get("rule_snapshot_unavailable_reason"))}
    elif stored_source == "none":
        result["rule_snapshot_unavailable"] = {"code": "rule_snapshot_not_pulled"}
    else:
        result["rule_snapshot_unavailable"] = {}
    result["route_unavailable"] = dict(route_unavailable or {})
    result["stale"], result["stale_reasons"], result["bom_unavailable"] = _input_drift(
        project_id, req_no, stored, upstream, route_unavailable)
    # 业务部件清单那条轴（Spec `packaging-business-parts-version-pinning.md` §2.3）：与 BOM /
    # 路线两条轴并列、各自独立披露 —— "重新导入清单后旧成本必须被标出来"这条以前没人比。
    business_reason, business_unavailable = _business_parts_drift(project_id, stored)
    if business_reason:
        result["stale_reasons"] = list(result["stale_reasons"]) + [business_reason]
    result["stale"] = bool(result["stale"] or business_reason)
    result["business_parts_unavailable"] = dict(business_unavailable)
    return result


def _input_drift(project_id: str, requirement_no: str, stored: dict,
                 live_route_version: str, route_unavailable: Optional[dict] = None) -> tuple:
    """读侧比对「算时记下的输入」与「当前输入」（Spec §2.2）：只报事实，绝不重算成本。

    - `provenance_missing`：这份成本单没有 `source_versions_json`（本批之前算的）；
    - `route_reconfirmed`：当前确认路线版本 ≠ 存的 `route_version`；
    - `bom_rebuilt`：当前 BOM 指纹 ≠ 存的 `bom_hash`；
    - `bom_unavailable`：当前 BOM **比较不了**（抛异常 / 返回空，或存的没有指纹）——
      "比较不了" ≠ "变了"，所以此时**不给** `bom_rebuilt`；
    - 路线那条轴同一条纪律（Spec `packaging-cost-route-version-read-failure.md` §2.1）：
      `route_unavailable` 非空时**不给** `route_reconfirmed`（读不到不是"变了"）；读**成功**
      但当前没有确认版本时口径不变（照旧按"对不上"报，`route_versions()` 只增不改）。
    """
    if not stored:
        return False, ["provenance_missing"], {}
    reasons: list = []
    if not route_unavailable:
        if _text(stored.get("route_version")) != _text(live_route_version):
            reasons.append("route_reconfirmed")
    stored_hash = _text(stored.get("bom_hash"))
    unavailable: dict = {}
    if not stored_hash:
        unavailable = {"code": "bom_unavailable", "reason": ""}
    else:
        live_rows: Optional[list] = None
        try:
            live_rows = [dict(item) for item in da_repo.load_packaging_bom(project_id, requirement_no)]
        except Exception as exc:                        # noqa: BLE001 - 读不到要披露，不许炸
            unavailable = {"code": "bom_unavailable", "reason": type(exc).__name__}
        if live_rows is not None:
            if not live_rows:
                unavailable = {"code": "bom_unavailable", "reason": ""}
            elif bom_input_hash(live_rows) != stored_hash:
                reasons.append("bom_rebuilt")
    return bool(reasons), reasons, unavailable


def _business_parts_probe(project_id: str) -> tuple:
    """探测当前业务部件清单身份，返回 `(hash, unavailable)`（Spec
    `packaging-business-parts-version-pinning.md` §2.3，与 `_route_probe()` 同形）。

    三态两两可分：
      · 读到了 → `("<hash>", {})`；
      · 读到了、但项目里还没有清单 / 没有行 → `("", {})`（"确实没有"，不是"读不到"）；
      · 读挂 → `("", {"code": "business_parts_unavailable", "reason": "<异常类名>"})`。

    只经 `packaging_parts.load_business_parts()` 这**一个入口**读（与
    `_business_parts_scope()` 同源），绝不绕 `da_repo`、绝不另写第二套匹配。
    """
    try:
        from . import packaging_parts
        doc = packaging_parts.load_business_parts(project_id)
    except Exception as exc:                            # noqa: BLE001 - 读不到要披露，不许炸
        return "", {"code": "business_parts_unavailable", "reason": type(exc).__name__}
    if not isinstance(doc, dict):
        return "", {}
    rows = doc.get("business_parts") if isinstance(doc.get("business_parts"), list) else []
    if not rows:
        return "", {}
    return _text(doc.get("business_parts_hash")), {}


def _business_parts_drift(project_id: str, stored: dict) -> tuple:
    """成本单里存的业务部件版本 vs 当前清单（Spec
    `packaging-business-parts-version-pinning.md` §2.3）：返回 `(reason, unavailable)`。

    - 存的 hash 非空、且 ≠ 当前清单 hash → `("business_parts_reimported", {})`；
    - 存的 hash 非空、与当前一致 → `("", {})`（没换就不报）；
    - 存的**没有**版本（本批之前算的历史成本单）→ `("", {"code": "business_parts_version_missing"})`
      ——「当时没记」≠「变了」，不许当成"没过期"，也不许报 drift；
    - 当前清单**读不到** → `("", {"code": "business_parts_unavailable", "reason": …})`
      —— 「比较不了」≠「变了」；这条轴与 BOM / 路线两条轴**各自独立**披露，不合并。
    """
    if not stored:
        return "", {}                                   # 整份来源都没有：`provenance_missing` 已说清
    live_hash, unavailable = _business_parts_probe(project_id)
    if unavailable:
        return "", dict(unavailable)
    stored_hash = _text(stored.get("business_parts_hash"))
    if not stored_hash:
        return "", {"code": "business_parts_version_missing"}
    if not live_hash:
        return "", {}                                   # 当前确实没有清单：没有"换过"可报
    if stored_hash != live_hash:
        return "business_parts_reimported", {}
    return "", {}


def _route_probe(project_id: str, requirement_no: str) -> tuple:
    """探测当前确认路线版本，返回 `(version, unavailable)`。

    三态两两可分（Spec `packaging-cost-route-version-read-failure.md` §2.1）：
      · 读到了 → `("route:v3", {})`；
      · 读到了、但当前一条确认版本都没有 → `("", {})`（"确实没有"，不是"读不到"）；
      · 读挂 → `("", {"code": "route_unavailable", "reason": "<异常类名>"})`。
    只经 `packaging_route.route_versions()` 一个入口，绝不绕 `da_repo`。
    """
    try:
        from tech_app.backend.services import packaging_route as _route_mod
        versions = _route_mod.route_versions(project_id, requirement_no) or []
    except Exception as exc:                            # noqa: BLE001 - 读不到要披露，不许炸
        return "", {"code": "route_unavailable", "reason": type(exc).__name__}
    if not versions:
        return "", {}
    latest = versions[-1] if isinstance(versions[-1], dict) else {}
    return str(latest.get("version") or ""), {}


def _upstream_route_version(project_id: str, requirement_no: str, *,
                            probe: bool = False):
    """成本照着哪一版确认路线算的；读不到就空串，绝不现编（计算侧口径逐字不变）。

    `probe=True` 时返回 `(version, unavailable)` —— 读侧要分开"读不到"与"确实没有"，
    缺省仍是那一版字符串。
    """
    version, unavailable = _route_probe(project_id, requirement_no)
    if probe:
        return version, unavailable
    return version


def _route_version_and_availability(project_id: str, requirement_no: str) -> tuple:
    """读侧要的那一份：`(version, unavailable)`。

    版本仍经 `_upstream_route_version()` 取（同一模块属性，可被既有测试打桩），
    缺省路径（打桩只给字符串）按"读到了"处理。
    """
    probed = _upstream_route_version(project_id, requirement_no, probe=True)
    if isinstance(probed, tuple) and len(probed) == 2:
        return _text(probed[0]), dict(probed[1] or {})
    return _text(probed), {}


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

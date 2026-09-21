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
        "expression": _LAMINATION_EXPR, "minimum_charge": 200, "frozen_minimum_charge": 200, "rounding": 4,
        "rate_code": "RATE-PKG-EQUIP-SURFACE", "source_ref": _GSTAMP + "V2",
        "defaults": {"setup_minutes": 30.0, "capacity_per_hour": 5500.0,
                     "equipment_rate": 197.0, "labor_rate": 145.0, "film_price": 1.7,
                     "film_thickness_um": 18.0, "film_kg_price": 18.5}},
    "PKG-C-HOT-STAMP-FLAT": {
        "formula_code": "PKG-C-HOT-STAMP-FLAT", "cost_category": "hot_stamp_flat",
        "expression": _HOT_STAMP_FLAT_EXPR, "minimum_charge": 150, "frozen_minimum_charge": 150, "rounding": 4,
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
        "expression": _DIE_CUT_EXPR, "minimum_charge": 100, "frozen_minimum_charge": 100, "rounding": 4,
        "rate_code": "RATE-PKG-EQUIP-MOULD", "source_ref": _GSTAMP + "AI2",
        "defaults": {"setup_minutes": 120.0, "capacity_per_hour": 6500.0,
                     "equipment_rate": 197.52, "labor_rate": 190.06}},
    "PKG-C-V-GROOVE": {
        "formula_code": "PKG-C-V-GROOVE", "cost_category": "v_groove",
        "expression": _V_GROOVE_EXPR, "minimum_charge": 150, "frozen_minimum_charge": 120, "rounding": 4,
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
        "minimum_charge_source_ref": "报价逻辑-0903.xlsx/报价-行业标准/V2",
        "variable_map": {"machine_length": "H", "machine_width": "I", "imposition_count": "J", "quote_quantity": "R"},
        "source_formula": "=(H2*I2/1000000*1.7/1.13/J2+H2*I2/1000000*18/1000*18.5/J2)+((30/60+R2/J2/5500)*(197+145))/R2",
        "verify_inputs": {"machine_length": 889, "machine_width": 700, "imposition_count": 1, "setup_minutes": 30, "capacity_per_hour": 5500, "equipment_rate": 197, "labor_rate": 145, "film_price": 1.7, "film_thickness_um": 18, "film_kg_price": 18.5, "tax_factor": 1.13, "quote_quantity": 1000},
    },
    "PKG-C-HOT-STAMP-FLAT": {
        "source_sheet": "报价-工费率", "source_cell": "X2",
        "source_ref": "报价逻辑-0903.xlsx/报价-工费率/X2",
        "minimum_charge_source_ref": "报价逻辑-0903.xlsx/报价-行业标准/X2",
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
        "minimum_charge_source_ref": "报价逻辑-0903.xlsx/报价-行业标准/AI2",
        "variable_map": {"imposition_count": "J", "quote_quantity": "R"},
        "source_formula": "=((120/60+R2/J2/6500)*(197.52+190.06))/R2",
        "verify_inputs": {"imposition_count": 1, "setup_minutes": 120, "capacity_per_hour": 6500, "equipment_rate": 197.52, "labor_rate": 190.06, "quote_quantity": 1000},
    },
    "PKG-C-V-GROOVE": {
        "source_sheet": "报价-工费率", "source_cell": "AK5",
        "source_ref": "报价逻辑-0903.xlsx/报价-工费率/AK5",
        "minimum_charge_source_ref": "报价逻辑-0903.xlsx/报价-行业标准/AK5",
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
    """从快照读口径申报块；读不到 → `pending`（Spec §6）。"""
    try:
        data = json.loads(Path(RULES_JSON_PATH).read_text(encoding="utf-8"))
        block = data.get("minimum_charge_policy") or {}
    except Exception:  # noqa: BLE001 - 快照不可用不该让成本计算整体失败
        block = {}
    if not isinstance(block, dict):
        block = {}
    status = _text(block.get("status")) or "pending"
    return {"status": status,
            "chosen": _text(block.get("chosen")),
            "decided_by": _text(block.get("decided_by")),
            "decided_at": _text(block.get("decided_at")),
            # 快照缺失/读不到时必须能直接看出「未裁决、当前按哪套回退」（Spec §6）。
            "policy": "unresolved" if status == "pending" else _text(block.get("chosen"))}


#: 模块级口径常量（Spec §6）：`pending` = 未裁决，运行时必须标注 `unresolved`。
MINIMUM_CHARGE_POLICY = _load_minimum_charge_policy()

#: 与第 1 批**冻结值**不一致的最低收费：逐条登记出处、差异与裁决状态（Spec
#: `packaging-cost-red-closure.md` C3）。`frozen` = `FORMULA_CATALOG[...]["frozen_minimum_charge"]`，
#: `current` = 运行时 `minimum_charge`。
#: `owner` / `decided_at` 留空 = **尚未裁决**：这两个字段只能由业务/用户给，实现方不许填数
#: （见 docs/specs/packaging-cost-minimum-charge.md §3：三种候选口径各有代价，必须业务选）。
_MIN_CHARGE_DECISIONS = {
    "PKG-C-V-GROOVE": {
        "frozen": 120, "current": 150, "owner": "", "decided_at": "",
        "reason": "报价-行业标准!AK5 原文 = MAX(150/R5,0.15)，150 有出处；"
                  "第 1 批冻结的 120 在该工作簿任何工作表里都不存在"
                  "（报价-工费率!AK5 无门限）。待业务在 ①行业标准 / ②工费率 / ③混合 之间裁决后，"
                  "本条目补 owner 与 decided_at，并按裁决结果同步 FORMLA_CATALOG 与"
                  "tests/test_packaging_cost_engine_red.py 的 c1/c2/c4 期望值。",
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
            "decided_at": _text(block.get("decided_at"))}


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
    """逐字等价判定（Spec §5.3）；返回 `{equivalent, unmapped, resolved, source, reason}`。"""
    row = _source_row(source_cell)
    result = {"equivalent": False, "unmapped": [], "resolved": "", "source": "", "reason": ""}
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


def compute_packaging(rows, *, tax_factor: Any = 1.13, loss_uplift: Any = LOSS_UPLIFT,
                      yield_divisor: Any = YIELD_DIVISOR, rule_rows=None) -> dict:
    """包材合计：逐条 `compute_content` 求和（Spec §2.10）。"""
    lines: list = []
    total = 0.0
    for row in (rows or []):
        variables = dict(row)
        variables["tax_factor"] = tax_factor
        variables["loss_uplift"] = loss_uplift
        variables["yield_divisor"] = yield_divisor
        result = compute_content(row.get("formula_code"), variables, rows=rule_rows)
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
    #: Spec 修复第 1 批 §3.4：一次计算只读一次规则表，读到的 rows 传给每一行，
    #: 保证同一份成本明细里公式版本一致。
    formula_rows = [dict(row) for row in kb_repo._table("kb_packaging_cost_formula")]
    rule_version = rule_snapshot_version()
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
            result = compute_line("material", variables, rows=formula_rows)
        amount = result.get("amount") if result else None
        expression = result.get("expression") if result else entry_material["expression"]
        line = {"cost_category": "material", "part_code": part_code, "part_name": part_name,
                "formula_code": entry_material["formula_code"], "rate_code": "",
                "expression": expression, "inputs": variables, "amount": amount,
                "min_charge_applied": False, "source": "formula",
                "items_inputs": variables}
        line.update(_trace_fields(entry_material, result=result, snapshot=rule_version))
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
    packaging = compute_packaging(kb_repo.packaging_cost_contents(), tax_factor=tax_factor,
                                  loss_uplift=LOSS_UPLIFT, yield_divisor=YIELD_DIVISOR,
                                  rule_rows=formula_rows)
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
    upstream = _upstream_route_version(project_id, req_no)
    if not row:
        return {"built": False, "project_id": project_id, "requirement_no": req_no,
                "scenario_code": _text(scenario) or "default", "engine_version": ENGINE_VERSION,
                "source_versions": {"route_version": upstream, "engine_version": ENGINE_VERSION},
                "cost_profile": COST_PROFILE, "items": [], "gaps": [], "assumptions": [],
                "has_gaps": False, "categories": {code: 0.0 for code, _ in COST_CATEGORIES},
                "report_groups": {name: 0.0 for name in REPORT_GROUPS},
                "subtotal": 0.0, "loss_amount": 0.0, "tooling_total": 0.0,
                "packaging_total": 0.0, "freight_total": 0.0, "total_cost": 0.0}
    items = da_repo.load_packaging_cost_items(row["estimate_id"])
    result = _rehydrate(row, items)
    # 上游版本的埋点（DWG 第 5 批 Spec §6.1）：成本必须带出它照着哪一版确认路线算的。
    result["source_versions"] = {"route_version": upstream, "engine_version": ENGINE_VERSION}
    return result


def _upstream_route_version(project_id: str, requirement_no: str) -> str:
    """成本照着哪一版确认路线算的；读不到就空串，绝不现编。"""
    try:
        from tech_app.backend.services import packaging_route as _route_mod
        versions = _route_mod.route_versions(project_id, requirement_no) or []
    except Exception:
        return ""
    if not versions:
        return ""
    latest = versions[-1] if isinstance(versions[-1], dict) else {}
    return str(latest.get("version") or "")


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

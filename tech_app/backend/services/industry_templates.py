"""行业规格模板（需求单 Section C）的单一事实源。

同一套字段定义要被四个地方使用，各自散一份必然漂移：
  - 1.1 创建页表单（frontend/requirement-create.js 的 RC_*_SPECS，键名必须与此处一致）
  - AI 需求抽取的可抽取/必填字段集合（services/requirement_extract.py）
  - 需求单 PDF 的「三、产品技术规格」章节（services/requirement_pdf.py）
  - 需求确定性完整性检查（main.py::_requirement_precheck）

因此这里只描述「有哪些字段、叫什么、必填与否」，渲染方式各端自理。
tests/test_industry_templates.py 会比对前端 JS 中的键名，防止两边跑偏。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, NamedTuple

# 行业清单的唯一事实源在仓库根 cpq_industries.py（报价侧与技术工艺侧共用）。
from ..config import ROOT_DIR

REPO_ROOT = Path(ROOT_DIR).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cpq_industries                                                # noqa: E402

# 全部由注册表派生，本模块不再自己写行业字面量。
INDUSTRIES: tuple[str, ...] = tuple(cpq_industries.industry_keys())
DEFAULT_INDUSTRY = cpq_industries.DEFAULT_INDUSTRY

INDUSTRY_LABELS = {key: str(item["label"]) for key, item in cpq_industries.INDUSTRIES.items()}
# 历史草稿可能仍是「灵活（AI 生成字段）」模板。它已不在可选项里，
# 但旧需求单必须还能打开，所以保留标签与渲染兜底。
INDUSTRY_LABELS.update(cpq_industries.LEGACY_LABELS)


class SpecField(NamedTuple):
    key: str
    label: str
    required: bool = False


class SpecBlock(NamedTuple):
    section: str      # 3.1 / 3.2 / ...
    title: str
    fields: tuple[SpecField, ...]


# --------------------------------------------------------------------------- #
# 半导体（静电吸盘等精密零部件）
# --------------------------------------------------------------------------- #
SEMICONDUCTOR_SPEC: tuple[SpecBlock, ...] = (
    SpecBlock("3.1", "基础参数", (
        SpecField("product_name", "产品名称", True),
        SpecField("product_model", "产品型号"),
        SpecField("wafer_size", "晶圆尺寸", True),
        SpecField("chuck_type", "静电吸盘类型", True),
        SpecField("temperature_zones", "温区数量", True),
        SpecField("ceramic_material", "陶瓷基体材料", True),
        SpecField("electrode_material", "电极材料"),
        SpecField("base_material", "金属基座材质", True),
        SpecField("product_weight", "产品重量"),
        SpecField("overall_dimensions", "外形尺寸"),
    )),
    SpecBlock("3.2", "精度与性能参数", (
        SpecField("ttv", "平面度（TTV）要求", True),
        SpecField("roughness", "表面粗糙度（Ra）要求", True),
        SpecField("micro_hole_diameter", "微孔孔径", True),
        SpecField("micro_hole_diameter_tolerance", "微孔孔径公差"),
        SpecField("micro_hole_depth_tolerance", "微孔深度公差"),
        SpecField("mesa_height", "微凸台（mesa）高度"),
        SpecField("adsorption_uniformity", "吸附力均匀性", True),
        SpecField("temperature_range", "工作温度范围"),
        SpecField("max_voltage", "最高使用电压"),
        SpecField("leakage_current", "漏电流要求"),
        SpecField("helium_leak_rate", "氦气漏率要求", True),
        SpecField("cleanliness", "洁净度等级", True),
        SpecField("service_life", "使用寿命要求"),
    )),
    SpecBlock("3.3", "应用场景", (
        SpecField("target_equipment", "目标设备类型", True),
        SpecField("process_stage", "适用工艺环节", True),
        SpecField("vacuum_environment", "真空环境要求"),
        SpecField("heating", "是否含加热功能"),
    )),
)

# --------------------------------------------------------------------------- #
# 电池（电芯 / 模组）
# --------------------------------------------------------------------------- #
BATTERY_SPEC: tuple[SpecBlock, ...] = (
    SpecBlock("3.1", "基本电性能参数", (
        SpecField("battery_model", "电芯型号", True),
        SpecField("cathode_material", "正极材料", True),
        SpecField("anode_material", "负极材料", True),
        SpecField("nominal_voltage", "标称电压", True),
        SpecField("gravimetric_energy_density", "质量能量密度"),
        SpecField("volumetric_energy_density", "体积能量密度"),
        SpecField("dcir", "直流内阻（DCIR）"),
    )),
    SpecBlock("3.2", "安全与可靠性参数", (
        SpecField("battery_operating_temperature", "工作温度范围"),
        SpecField("thermal_runaway_temperature", "热失控触发温度"),
        SpecField("crush_puncture_safety", "挤压/针刺安全"),
        SpecField("cycle_life", "循环寿命"),
        SpecField("calendar_life", "日历寿命"),
    )),
    SpecBlock("3.3", "核心工艺特点", (
        SpecField("stacking_process", "叠片工艺"),
        SpecField("minimalist_packaging", "极简封装"),
        SpecField("battery_process_other", "其他核心工艺特点"),
    )),
    SpecBlock("3.4", "形状与尺寸", (
        SpecField("vda_dimensions", "VDA标准尺寸"),
        SpecField("slim_cell_dimensions", "长薄化尺寸"),
        SpecField("battery_form_factor", "形状"),
    )),
)

# --------------------------------------------------------------------------- #
# 电器（白电整机与关键部件）
#
# 字段取向与前两个行业不同:半导体看精度、电池看电性能,电器看的是
# 「能效 + 安规认证 + 成型工艺」—— 这三项决定整机能不能上市、成本落在哪里。
# --------------------------------------------------------------------------- #
APPLIANCE_SPEC: tuple[SpecBlock, ...] = (
    SpecBlock("3.1", "整机基础参数", (
        SpecField("appliance_category", "产品品类", True),
        SpecField("appliance_model", "产品型号"),
        SpecField("rated_voltage", "额定电压/频率", True),
        SpecField("rated_power", "额定功率", True),
        SpecField("energy_efficiency_grade", "能效等级", True),
        SpecField("appliance_dimensions", "整机外形尺寸"),
        SpecField("appliance_weight", "整机净重"),
    )),
    SpecBlock("3.2", "性能与可靠性参数", (
        SpecField("key_performance", "关键性能指标", True),
        SpecField("noise_limit", "噪声限值"),
        SpecField("standby_power", "待机功耗"),
        SpecField("appliance_operating_temperature", "工作环境温度"),
        SpecField("appliance_service_life", "整机使用寿命"),
        SpecField("reliability_test", "可靠性试验要求"),
    )),
    SpecBlock("3.3", "结构与材料", (
        SpecField("housing_material", "主体结构材料", True),
        SpecField("surface_process", "外观表面工艺"),
        SpecField("insulation_requirement", "保温与密封要求"),
        SpecField("core_components", "核心部件", True),
        SpecField("forming_process", "关键成型工艺", True),
    )),
    SpecBlock("3.4", "安规与认证", (
        SpecField("safety_standard", "安规标准", True),
        SpecField("hipot_requirement", "耐压测试要求"),
        SpecField("ground_resistance", "接地电阻要求"),
        SpecField("emc_requirement", "EMC 要求"),
        SpecField("certification_region", "认证区域"),
    )),
)

# --------------------------------------------------------------------------- #
# 包装（第 2 批：完整 3.1–3.6）
# 键与顺序严格等于 docs/specs/packaging-requirement-template.md §4.6（64 个键，
# 10 个必填）；前端 requirement-create.js 的 RC_PACKAGING_SPECS 必须与这里一致。
# --------------------------------------------------------------------------- #
PACKAGING_SPEC: tuple[SpecBlock, ...] = (
    SpecBlock("3.1", "产品与订单", (
        SpecField("packaging_product_name", "包装产品名称", True),
        SpecField("packaging_category", "包装品类", True),
        SpecField("quote_quantity", "报价数量", True),
        SpecField("moq", "最小起订量"),
        SpecField("sample_quantity", "样品数量"),
        SpecField("mass_quantity", "量产数量"),
        SpecField("first_trial", "首试数量"),
        SpecField("delivery_due", "期望交期"),
        SpecField("destination", "交付地点"),
        SpecField("currency", "币种"),
        SpecField("tax_rate", "税率"),
    )),
    SpecBlock("3.2", "成品尺寸与盒型", (
        SpecField("inner_length", "成品内长", True),
        SpecField("inner_width", "成品内宽", True),
        SpecField("inner_height", "成品内高", True),
        SpecField("box_type", "盒型", True),
        SpecField("box_family", "盒型系列"),
        SpecField("fit_clearance", "配合间隙"),
        SpecField("closure_type", "闭合方式", True),
        SpecField("v_groove", "V槽", True),
        SpecField("magnetic", "磁吸"),
        SpecField("collapsible", "可折叠"),
        SpecField("open_close_life", "开合寿命"),
    )),
    SpecBlock("3.3", "材料", (
        SpecField("grey_board", "灰板"),
        SpecField("grey_board_thickness", "灰板厚度"),
        SpecField("face_paper", "面纸"),
        SpecField("face_paper_gsm", "面纸克重", True),
        SpecField("lining_paper", "里纸"),
        SpecField("insert_type", "内衬类型"),
        SpecField("glue", "胶水"),
        SpecField("magnet", "磁铁"),
        SpecField("ribbon", "丝带"),
        SpecField("accessories", "配件"),
        SpecField("eco_requirement", "环保要求"),
    )),
    SpecBlock("3.4", "印刷与表面工艺", (
        SpecField("print_colors", "印刷色数"),
        SpecField("spot_colors", "专色"),
        SpecField("lamination", "覆膜"),
        SpecField("hot_stamping", "烫金"),
        SpecField("uv_coating", "UV 上光"),
        SpecField("emboss_deboss", "压纹/压凹"),
        SpecField("silk_screen", "丝印"),
        SpecField("die_cutting", "模切"),
        SpecField("mounting", "裱贴"),
        SpecField("special_process", "特殊工艺"),
        SpecField("process_area", "工艺面积"),
    )),
    SpecBlock("3.5", "包装与物流", (
        SpecField("units_per_carton", "每箱数量"),
        SpecField("carton_size", "外箱尺寸"),
        SpecField("flat_card", "平卡"),
        SpecField("poly_bag", "胶袋"),
        SpecField("corner_guard", "护角"),
        SpecField("pallet", "卡板"),
        SpecField("units_per_pallet", "每板数量"),
        SpecField("shipping_mode", "运输方式"),
        SpecField("min_freight", "最小运费"),
        SpecField("loading_rate", "装载率"),
    )),
    SpecBlock("3.6", "商务与价格", (
        SpecField("need_cost_estimate", "是否需要成本估算"),
        SpecField("loss_rate", "损耗率"),
        SpecField("proofing_base", "打样基数"),
        SpecField("tooling_cost", "模具费"),
        SpecField("tooling_amortize_qty", "模具摊销数量"),
        SpecField("target_gross_margin", "目标毛利率"),
        SpecField("tech_premium", "技术加价"),
        SpecField("market_adjustment", "市场调整"),
        SpecField("other_markup", "其它加成"),
        SpecField("discount", "折扣"),
    )),
)

SPECS: dict[str, tuple[SpecBlock, ...]] = {
    "semiconductor": SEMICONDUCTOR_SPEC,
    "battery": BATTERY_SPEC,
    "appliance": APPLIANCE_SPEC,
    # 包装（第 2 批：完整 3.1–3.6）。
    "packaging": PACKAGING_SPEC,
}

# 各行业 Section C 之后「图纸与技术资料」块的编号:模板块数 + 1。
FILE_BLOCK_SECTION = {key: f"3.{len(blocks) + 1}" for key, blocks in SPECS.items()}


# --------------------------------------------------------------------------- #
# 查询
# --------------------------------------------------------------------------- #
def normalize(industry: object) -> str:
    """把任意来源的行业值收敛成受支持的模板键。"""
    value = str(industry or "").strip().lower()
    return value if value in SPECS else DEFAULT_INDUSTRY


def label(industry: object) -> str:
    value = str(industry or "").strip().lower()
    return INDUSTRY_LABELS.get(value, INDUSTRY_LABELS[DEFAULT_INDUSTRY])


def blocks(industry: object) -> tuple[SpecBlock, ...]:
    return SPECS[normalize(industry)]


def field_keys(industry: object) -> list[str]:
    """按页面顺序返回该行业 Section C 的全部字段键。"""
    return [field.key for block in blocks(industry) for field in block.fields]


def required_keys(industry: object) -> set[str]:
    return {field.key for block in blocks(industry) for field in block.fields if field.required}


def labels(industry: object) -> dict[str, str]:
    return {field.key: field.label for block in blocks(industry) for field in block.fields}


def all_labels() -> dict[str, str]:
    """全行业字段标签合集,供 PDF / 确认页等需要跨行业展示的地方使用。"""
    merged: dict[str, str] = {}
    for industry in SPECS:
        merged.update(labels(industry))
    return merged


def all_field_keys() -> set[str]:
    return {key for industry in SPECS for key in field_keys(industry)}


def section_checks(industry: object) -> list[tuple[str, list[str], str]]:
    """需求完整性检查用的 (章节名, 必填字段, 通过语) 列表。"""
    industry = normalize(industry)
    spec = blocks(industry)
    head_fields = sorted(required_keys(industry) & {f.key for f in spec[0].fields})
    checks = [(
        "三、产品技术规格（Section C）", head_fields,
        f"{label(industry)}产品基础规格已录入",
    )]
    for block in spec:
        required = [field.key for field in block.fields if field.required]
        checks.append((f"{block.section} {block.title}", required, f"{block.title}已录入"))
    return checks


def iter_fields(industry: object) -> Iterable[SpecField]:
    for block in blocks(industry):
        yield from block.fields

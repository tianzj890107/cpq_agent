# -*- coding: utf-8 -*-
"""
从《亿纬锂能DA梳理.xlsx》生成 2.2 组装与整合的「成品参数字典」。

来源是报价助手 sheet 里的 **产品技术参数（clm_calc_product_tech）** —— 报价测算单上
挂在成品行下面的那张表。2.2 推荐的整机参数如果和它对不上，算完的整机就没法直接进报价。

这个脚本做两件事，缺一不可：
  1. 字段的 code / 中文名 / 类型 / 示例值**全部从 xlsx 读**，不在代码里抄一遍。
  2. 分组、适用产品族、是否报价必填、单位，是本项目的**加工口径**，写在下面的
     CURATION 里；生成时逐条与 xlsx 核对 —— DA 改了名或删了字段，这里直接报错，
     而不是悄悄生成一份与 DA 对不上的字典。

用法（需要装了 openpyxl 的解释器）：
    python tools/build_quote_product_params.py            # 生成
    python tools/build_quote_product_params.py --check    # 只校验与 DA 是否一致，不写文件

输出：agent_knowledge/rules/quote_product_params.json（由 services/product_params.py 加载）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TOOLS_DIR.parent                      # tech_app/
CPQ_DIR = ROOT_DIR.parent                        # 配置报价CPQ/
OUT_PATH = ROOT_DIR / "agent_knowledge" / "rules" / "quote_product_params.json"

DA_TABLE = "clm_calc_product_tech"
DA_SHEET_BO = "价格测算单"

# --------------------------------------------------------------------------- #
# 产品族。2.2 先判定整机属于哪一族，再只填该族适用的组 —— DA 这张表是三条产品线的
# 并集（锂原电池 / 储能系统 / 光伏组件），给一台 18650 电池包推荐"边框膜厚"是胡说。
# --------------------------------------------------------------------------- #
FAMILIES = [
    {"key": "li_primary", "name": "锂原电池 / 电池组",
     "hint": "一次电池（锂亚 ER、锂锰 CR 等）及其带线组件"},
    {"key": "li_ion_pack", "name": "锂离子电池包 / 模组",
     "hint": "可充电电芯（18650、方形铝壳、软包等）成组后的电池包"},
    {"key": "ess", "name": "储能系统 / 电池柜",
     "hint": "集装箱式或柜式储能，按 kWh 计"},
    {"key": "pv_module", "name": "光伏组件",
     "hint": "晶硅组件，含边框、胶膜、接线盒"},
    {"key": "other", "name": "其他成品",
     "hint": "不属于上述四类时使用，只填通用与结构参数"},
]

BATTERY = ("li_primary", "li_ion_pack")
ALL_BATTERY_AND_ESS = ("li_primary", "li_ion_pack", "ess")

# --------------------------------------------------------------------------- #
# 分组与加工口径。group 的 families 决定这一组对哪些产品族生效。
# required=True 表示"报价必填"：缺了它报价测算单上的成品行就填不完整。
# --------------------------------------------------------------------------- #
GROUPS = [
    {"key": "identity", "name": "成品标识",
     "families": ("li_primary", "li_ion_pack", "ess", "pv_module", "other")},
    {"key": "cell", "name": "电芯与电性能", "families": BATTERY},
    {"key": "structure", "name": "结构与外形",
     "families": ("li_primary", "li_ion_pack", "ess", "pv_module", "other")},
    {"key": "wiring", "name": "引出线与接插件", "families": BATTERY},
    {"key": "environment", "name": "环境与应用",
     "families": ("li_primary", "li_ion_pack", "ess", "other")},
    {"key": "ess", "name": "储能系统配置", "families": ("ess",)},
    {"key": "pv", "name": "光伏组件配置", "families": ("pv_module",)},
    {"key": "pv_cable", "name": "线缆与连接器", "families": ("pv_module",)},
    {"key": "packaging", "name": "包装与运输",
     "families": ("li_primary", "li_ion_pack", "ess", "pv_module", "other")},
]

# code -> (group, unit, required, display_name_override)
# display_name_override 只在 DA 原文有明显笔误时给（会同时保留 da_name 供追溯）。
CURATION = {
    # 成品标识
    "product_series":         ("identity", "", True, None),
    "product_model":          ("identity", "", True, None),
    # 成品编码必填：报价的定价与加价规则是**按成品编码匹配产品行**的
    # （cpq_agent_server::_handle_markup_fill 里 `code in codes` 那道过滤）。
    # 编码为空时那边规则查得到、加价值却落不到产品上，最终价格 = 基础成本，
    # 既没有利润也没有加价，界面上还看不出异常。它由 2.2「写入数据库」生成（92022xxx）。
    "product_item_code":      ("identity", "", True, None),
    "product_item_name":      ("identity", "", True, None),
    "scheme_desc":            ("identity", "", False, None),
    "version_ext":            ("identity", "", False, None),
    # 电芯与电性能
    "cell_code":              ("cell", "", False, None),
    "cell_model":             ("cell", "", True, None),
    "reference_size":         ("cell", "", False, None),
    "rated_voltage":          ("cell", "V", True, "标称电压"),
    "rated_capacity":         ("cell", "mAh", True, None),
    "max_continuous_current": ("cell", "mA", True, None),
    "max_pulse_current":      ("cell", "mA", False, None),
    # 结构与外形
    "machine_model":          ("structure", "", False, None),
    "max_dimension":          ("structure", "mm", True, None),
    "weight":                 ("structure", "g", True, None),
    # 引出线与接插件（电池侧）
    "plug_wire_model":        ("wiring", "", False, None),
    "plug_direction":         ("wiring", "", False, None),
    "wire_length":            ("wiring", "mm", False, None),
    "is_wire_wound":          ("wiring", "", False, None),
    # 环境与应用
    "operating_temperature":  ("environment", "℃", True, None),
    "storage_temperature":    ("environment", "℃", False, None),
    "application_scope":      ("environment", "", False, None),
    # 储能系统
    "capacity":               ("ess", "kWh", True, None),
    "cooling_type":           ("ess", "", True, None),
    "extra_function":         ("ess", "", False, None),
    # 光伏组件
    "component_product_code": ("pv", "", False, None),
    "frame_size":             ("pv", "mm", True, None),
    "frame_color":            ("pv", "", False, None),
    "frame_material":         ("pv", "", False, None),
    "frame_hole_count":       ("pv", "", False, None),
    "frame_film_thickness":   ("pv", "", False, None),
    "junction_box":           ("pv", "", False, None),
    "film_scheme":            ("pv", "", True, None),
    "film_weight":            ("pv", "g/m²", False, None),
    "label":                  ("pv", "", False, None),
    "dust_plug":              ("pv", "", False, None),
    "current_bin":            ("pv", "A", False, None),
    "yield_loss":             ("pv", "%", False, None),
    "el_full_inspect_req":    ("pv", "%", False, None),
    # 线缆与连接器（组件侧）
    "connector":              ("pv_cable", "", True, None),
    "avg_cable_length":       ("pv_cable", "m", False, None),
    "positive_cable_length":  ("pv_cable", "m", False, None),
    "negative_cable_length":  ("pv_cable", "m", False, None),
    # 包装与运输
    "transport_scheme":       ("packaging", "", False, None),
    "other_nonstd_markup":    ("packaging", "元", False, None),
}

# 2.2 里模型/人写出来的常见叫法 → DA 字段。没有它，模型写"整机外形尺寸"就永远
# 对不上 max_dimension，覆盖率会一直显示缺口。归一化后比对（去空格/全角括号/单位后缀）。
ALIASES = {
    "整机外形尺寸": "max_dimension", "外形尺寸": "max_dimension", "最大外形尺寸": "max_dimension",
    "整机尺寸": "max_dimension", "产品尺寸": "max_dimension",
    "整备质量": "weight", "整机重量": "weight", "净重": "weight", "质量": "weight",
    "标称电压": "rated_voltage", "额定电压": "rated_voltage", "工作电压": "rated_voltage",
    "标称容量": "rated_capacity", "额定容量": "rated_capacity", "电池容量": "rated_capacity",
    "最大持续放电电流": "max_continuous_current", "持续放电电流": "max_continuous_current",
    "最大持续电流": "max_continuous_current",
    "脉冲电流": "max_pulse_current", "最大脉冲放电电流": "max_pulse_current",
    "工作温度范围": "operating_temperature", "使用温度": "operating_temperature",
    "存储温度范围": "storage_temperature", "储存温度": "storage_temperature",
    "应用领域": "application_scope", "适用场景": "application_scope",
    "电芯规格": "cell_model", "电芯": "cell_model", "电芯型号规格": "cell_model",
    "产品型号": "product_model", "成品型号": "product_model",
    "成品名称": "product_item_name", "产品名称": "product_item_name", "产品描述": "product_item_name",
    "出线方式": "plug_direction", "插头朝向": "plug_direction",
    "导线长度": "wire_length", "引线长度": "wire_length",
    "连接器": "connector", "接插件": "connector",
    "冷却方式": "cooling_type", "散热方式": "cooling_type",
    "系统电量": "capacity", "额定电量": "capacity",
    "包装方案": "transport_scheme", "运输方案": "transport_scheme",
}

_SPLIT = re.compile(r"[/、,，|]")


def _options(attr: dict) -> list[str]:
    """取候选值。**只认 DA 标了「枚举」「布尔」的字段**。

    DA 的示例列对枚举写的是"反向/正向"这种候选集，对文本字段写的却是一条样例：
    成品描述是"锂亚电池F0041W-LF,ER14250/W15,MOLEX5264反，陆运"、参考尺寸是"1/2AA"。
    只要看到分隔符就当枚举，这两条会被拆成 ['锂亚电池F0041W-LF','ER14250',…]
    和 ['1','2AA'] 塞给模型当合法取值 —— 那是把一句描述当成了下拉框。
    """
    kind = (attr.get("type") or "").strip()
    if kind.startswith("布尔"):
        return ["是", "否"]
    if not kind.startswith("枚举"):
        return []
    text = (attr.get("note") or "").strip()
    if not text or not _SPLIT.search(text):
        return []
    values = [item.strip() for item in _SPLIT.split(text) if item.strip()]
    return values if len(values) > 1 else []


_TRAILING_UNIT = re.compile(r"[（(][^（()）]{1,4}[)）]\s*$")


def _display_name(name: str, unit: str) -> str:
    """单位已经单独一列时，去掉中文名尾巴上的括号单位，免得渲染成"标称容量（mAh） mAh"。

    不按 unit 精确匹配：DA 里写的是「平均线长（米）」而单位列是 m，对不上就白留一个尾巴。
    只在本字段确实带单位时才动，且只吃末尾 4 字以内的括号 —— 长括号是说明，不是单位。
    """
    return _TRAILING_UNIT.sub("", name).strip() if unit else name


def load_da_fields() -> dict:
    """从 xlsx 读出产品技术参数表的字段。复用 cpq_db 的解析，保证与三个 Agent 同源。"""
    sys.path.insert(0, str(CPQ_DIR))
    import cpq_db                                    # noqa: E402

    try:
        ontology = cpq_db._load_ontology()["quote"]
    except ModuleNotFoundError as exc:
        raise SystemExit(
            f"缺少 {exc.name}：本脚本要读 xlsx，请用装了 openpyxl 的解释器跑"
            "（tech_app 运行时不需要它 —— 生成出来的 JSON 才是运行时依赖）"
        ) from exc
    entity = next((e for e in ontology["entities"] if e["table"] == DA_TABLE), None)
    if not entity:
        raise SystemExit(f"DA 梳理里找不到 {DA_TABLE}，请确认 {cpq_db.ONTOLOGY_XLSX} 的报价助手 sheet")
    if entity["business_object"] != DA_SHEET_BO:
        raise SystemExit(f"{DA_TABLE} 的业务对象变成了「{entity['business_object']}」，请先确认 DA 是否重构")
    return {a["code"]: a for a in entity["attrs"]}


def build() -> dict:
    da = load_da_fields()
    problems: list[str] = []
    unknown = sorted(set(CURATION) - set(da))
    if unknown:
        problems.append(f"下列字段在 DA 的 {DA_TABLE} 里已不存在，请更新 CURATION：{'、'.join(unknown)}")
    # 反向也要报：DA 新增了字段而这里没归组，等于 2.2 悄悄少推一批参数。
    ids = {"calc_order_id", "product_line_id", "product_tech_id"}
    missing = sorted(set(da) - set(CURATION) - ids)
    if missing:
        problems.append(f"DA 新增了未归组的字段，请补进 CURATION：{'、'.join(missing)}")
    if problems:
        raise SystemExit("与 DA 梳理不一致：\n  - " + "\n  - ".join(problems))

    group_of = {g["key"]: g for g in GROUPS}
    fields = []
    for code, (group, unit, required, override) in CURATION.items():
        attr = da[code]
        if group not in group_of:
            raise SystemExit(f"{code} 归到了不存在的分组 {group}")
        fields.append({
            "code": code,
            "name": override or _display_name(attr["name"], unit),
            "da_name": attr["name"],          # DA 原文，供追溯；改名时这里会变
            "group": group,
            "unit": unit,
            "type": attr["type"],
            "required": bool(required),
            "options": _options(attr),
            "example": (attr["note"] or "").strip(),
        })

    return {
        "source": {
            "file": "亿纬锂能DA梳理.xlsx", "sheet": "报价助手",
            "business_object": DA_SHEET_BO, "table": DA_TABLE,
            "note": "本文件由 tools/build_quote_product_params.py 生成，不要手改；"
                    "改分组/必填/单位请改脚本里的 CURATION 后重跑。",
        },
        "families": FAMILIES,
        "groups": [{"key": g["key"], "name": g["name"], "families": list(g["families"])} for g in GROUPS],
        "fields": fields,
        "aliases": ALIASES,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="从亿纬锂能DA梳理生成成品参数字典")
    parser.add_argument("--check", action="store_true", help="只校验与 DA 是否一致，不写文件")
    args = parser.parse_args()

    spec = build()
    if args.check:
        print(f"OK：{len(spec['fields'])} 个字段与 DA 的 {DA_TABLE} 一致")
        return
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {OUT_PATH}（{len(spec['fields'])} 个字段 / {len(spec['groups'])} 组 / "
          f"{len(spec['families'])} 个产品族）")


if __name__ == "__main__":
    main()

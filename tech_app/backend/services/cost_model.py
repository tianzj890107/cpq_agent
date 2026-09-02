"""
企业成本口径 —— 材料 / 人工 / 制造费用 / 加工费用。

2.1 图纸拆解与 2.2 组装整合的成本测算**都走这一套**，公式由业务侧给定：

    材料 = Σ(数量 × 单价)                            （维持现状，来自明细行）
    人工 = 材料 / 1.13 / 0.791 × ((1 - 0.791) × 0.3556)
    制费 = 材料 / 1.13 / 0.791 × ((1 - 0.791) × 0.1778)
    加工 = 材料 / 1.13 / 0.791 × ((1 - 0.791) × 0.0944)

也就是说：**只有材料是逐项算出来的，另外三项由材料成本按固定系数推导**。模型给出的
工序成本（机加工工时、表面处理、检验…）在这个口径下不再单独计入 —— 它们已经被
这三个系数概括了，留着就是重复计费。normalize() 会把它们摘掉，并把摘掉的明细记进
assumptions，不做静默丢弃。

放在这里而不是各自实现，是因为两处口径必须一致：写进 md_clm_material_cost_cnf 的
material_unit_price 是这四项之和，零件侧和整机侧算法不同的话，报价拿到的数就对不上。
"""
from __future__ import annotations

from typing import Optional

# 业务给定的口径常量。改这里等于改全公司的成本口径，务必与财务确认后再动。
TAX_DIVISOR = 1.13            # 含税 → 不含税
MATERIAL_SHARE = 0.791        # 材料在不含税成本中的占比
LABOR_RATIO = 0.3556          # 非材料部分里人工的占比
OVERHEAD_RATIO = 0.1778       # 非材料部分里制造费用的占比
PROCESSING_RATIO = 0.0944     # 非材料部分里加工费用的占比

# 计入材料基数的成本类别。外购标准件也是料，一并算进去。
MATERIAL_CATEGORIES = ("material", "standard_part")

# 由公式推导的三项，及它们落在哪个成本类别上。
DERIVED = (
    ("labor", "人工", LABOR_RATIO),
    ("overhead", "制造费用", OVERHEAD_RATIO),
    ("machining", "加工费用", PROCESSING_RATIO),
)
DERIVED_CATEGORIES = tuple(item[0] for item in DERIVED)


def coefficient(ratio: float) -> float:
    """某一项相对材料成本的系数。单独抽出来是为了能被测试逐项核对。"""
    return (1.0 / TAX_DIVISOR / MATERIAL_SHARE) * ((1.0 - MATERIAL_SHARE) * ratio)


def formula_text(ratio: float) -> str:
    return (f"材料成本 / {TAX_DIVISOR} / {MATERIAL_SHARE} × "
            f"((1 - {MATERIAL_SHARE}) × {ratio})")


def category_of(item: dict) -> str:
    """取一行的成本类别，统一成字符串。

    这一步不能省：CostAnalysis.model_dump() 在 python 模式下留的是 CostCategory 枚举
    成员，而 Python 3.11 起 str(枚举) 是 "CostCategory.material" 而不是 "material"。
    直接 str() 比较的话，所有材料行都会被判成"不是材料"，材料基数变 0、四项全 0。
    """
    value = item.get("category")
    return str(getattr(value, "value", value) or "")


def _amount(item: dict) -> float:
    """一行的金额。amount 优先；缺了就用 数量 × 单价 兜底，都没有算 0。"""
    value = item.get("amount")
    if isinstance(value, (int, float)):
        return float(value)
    quantity, price = item.get("quantity"), item.get("unit_price")
    if isinstance(quantity, (int, float)) and isinstance(price, (int, float)):
        return float(quantity) * float(price)
    return 0.0


def material_total(items: list) -> float:
    return round(sum(_amount(item) for item in items or []
                     if category_of(item) in MATERIAL_CATEGORIES), 2)


def derive(material: float) -> dict:
    """由材料成本推出三项及合计。所有金额保留两位。"""
    base = round(float(material or 0.0), 2)
    values = {key: round(base * coefficient(ratio), 2) for key, _, ratio in DERIVED}
    return {
        "material": base,
        "labor": values["labor"],
        "overhead": values["overhead"],
        "machining": values["machining"],
        "total": round(base + sum(values.values()), 2),
        "coefficients": {key: round(coefficient(ratio), 6) for key, _, ratio in DERIVED},
        "constants": {
            "tax_divisor": TAX_DIVISOR, "material_share": MATERIAL_SHARE,
            "labor_ratio": LABOR_RATIO, "overhead_ratio": OVERHEAD_RATIO,
            "processing_ratio": PROCESSING_RATIO,
        },
    }


def _derived_item(category: str, label: str, ratio: float, material: float, amount: float) -> dict:
    return {
        "category": category,
        "name": label,
        "basis": f"{formula_text(ratio)} = {material:.2f} × {coefficient(ratio):.6f}",
        "quantity": None,
        "unit": "元",
        "unit_price": None,
        "amount": amount,
        "source": "企业成本口径公式",
        # 确定性推导，不是模型估的：置信度给满，且不该被当作"待核实"。
        "confidence": 1.0,
    }


def normalize(analysis: Optional[dict]) -> Optional[dict]:
    """把一份成本测算改写成企业口径：材料明细 + 三条推导行。就地修改并返回。

    幂等：重复调用不会叠加推导行，也不会把上一次推导出来的行当成"模型给的工序成本"
    再记一次 assumption。
    """
    if not isinstance(analysis, dict):
        return analysis
    items = [item for item in (analysis.get("items") or []) if isinstance(item, dict)]
    material = material_total(items)
    result = derive(material)

    kept = [item for item in items if category_of(item) in MATERIAL_CATEGORIES]
    # 被口径取代的行：模型算出来的工序/管理/利润等成本。上一轮自己生成的推导行不算，
    # 否则每保存一次就会多出一条"已被取代"的说明。
    superseded = [
        item for item in items
        if category_of(item) not in MATERIAL_CATEGORIES
        and item.get("source") != "企业成本口径公式"
    ]

    analysis["items"] = kept + [
        _derived_item(key, label, ratio, material, result[key]) for key, label, ratio in DERIVED
    ]
    analysis["unit_cost"] = result["total"]

    note = (f"按企业成本口径：材料 {material:.2f} 元由明细逐项累加；"
            f"人工/制造费用/加工费用由材料成本按固定系数推导"
            f"（/{TAX_DIVISOR}/{MATERIAL_SHARE}×((1-{MATERIAL_SHARE})×"
            f"{LABOR_RATIO}/{OVERHEAD_RATIO}/{PROCESSING_RATIO}））。")
    notes = [note]
    if superseded:
        dropped = round(sum(_amount(item) for item in superseded), 2)
        notes.append(
            f"模型另给出 {len(superseded)} 条工序/其它成本行（合计 {dropped:.2f} 元），"
            f"在本口径下已被上述系数概括，未计入合计："
            + "、".join(f"{item.get('name') or '未命名'}({_amount(item):.2f})"
                        for item in superseded[:6])
            + ("…" if len(superseded) > 6 else ""))
    existing = [line for line in (analysis.get("assumptions") or [])
                if not str(line).startswith(("按企业成本口径：", "模型另给出 "))]
    analysis["assumptions"] = notes + existing
    analysis["cost_model"] = result
    return analysis


def breakdown(analysis: Optional[dict]) -> dict:
    """取这份测算的四项金额。没跑过 normalize 也能算 —— 只依赖材料明细。"""
    saved = (analysis or {}).get("cost_model")
    if isinstance(saved, dict) and "total" in saved:
        return saved
    return derive(material_total((analysis or {}).get("items") or []))


def as_rows(result: dict) -> list[dict]:
    """给界面/审计用的四行对照表。"""
    return [
        {"key": "material", "label": "材料", "amount": result["material"],
         "basis": "明细逐项累加（数量 × 单价）"},
        *[{"key": key, "label": label, "amount": result[key], "basis": formula_text(ratio)}
          for key, label, ratio in DERIVED],
        {"key": "total", "label": "合计", "amount": result["total"],
         "basis": "材料 + 人工 + 制造费用 + 加工费用"},
    ]

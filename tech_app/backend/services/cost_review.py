"""
2.3 成本测算 —— CPQ 技术工艺，财务经理的步骤。

分工：**工艺经理出工艺与用量（2.1 / 2.2），财务经理据此核算成本并对数字负责。**
2.2 结束时工艺经理点「确认工艺并发送至财务做成本测算」，任务落到财务这里；
财务在本页把零件成本与组装成本逐项算出来、汇总，然后选三个去向之一：
写入数据库 / 发送至报价 / 退回工艺经理复核。

三条硬约束：

1. **不联网**。这一步只依据企业成本库里已有的物料价、费率、计价系数，以及模型的
   工程经验来估。原来零件与整机测算都会在库内有缺口时开 web_search 补行情价 ——
   财务口径不接受一个来路是"网上搜的"的单价，报价那头也没法追溯。所以这里一律
   web=False，库里没有的写进 open_questions 让财务自己询价。
2. **口径唯一**。仍然走 cost_model：材料逐项累加，人工/制费/加工按固定系数推导。
   零件、整机、汇总三处用的是同一套 breakdown，不另起炉灶。
3. **不复制数据**。零件成本读写 store.save_cost(project_id, part_id)，整机成本读写
   2.2 的 IntegrationPlan.cost —— 2.3 只做编排与汇总。复制一份就意味着 3.1 汇总、
   写主数据、发报价这些下游要面对两个真相。
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from ..models.cost_review import CostReview, merge_totals
from ..models.ir import DesignIR
from ..storage import store
from . import cost as cost_svc
from . import cost_lookup
from . import cost_model
from . import integration
from . import product_params

ProgressFn = Optional[Callable[[str], None]]

# 整机在 2.2 里的"零件编号"。2.3 的清单里它排在所有零件之后，作为最后一行。
ASSEMBLY_ID = integration.ASSEMBLY_PART_ID


def _report(progress: ProgressFn, message: str) -> None:
    if progress:
        progress(message)


# --------------------------------------------------------------------------- #
# 状态
# --------------------------------------------------------------------------- #
def load_review(project_id: str) -> CostReview:
    saved = store.load_cost_review(project_id) or {}
    saved.setdefault("project_id", project_id)
    return CostReview(**saved)


def save_review(project_id: str, review: CostReview, author: str = "system") -> None:
    from ..time_utils import now_cst_str

    review.project_id = project_id
    review.updated_at = now_cst_str()
    store.save_cost_review(project_id, review.model_dump(), author=author)


# --------------------------------------------------------------------------- #
# 汇总
# --------------------------------------------------------------------------- #
def _part_rows(project_id: str, ir: Optional[DesignIR]) -> List[dict]:
    """逐个零件：算没算过、单件多少、单台用量多少、小计多少。"""
    rows: List[dict] = []
    for part in (ir.parts if ir else []):
        saved = store.load_cost(project_id, part.part_id)
        breakdown = cost_model.breakdown(saved) if saved else {}
        quantity = max(1, int(part.quantity or 1))
        unit = float(breakdown.get("total") or 0)
        rows.append({
            "id": part.part_id,
            "name": part.name or "",
            "kind": "part",
            "quantity": quantity,
            "has_cost": bool(saved and (saved.get("items") or [])),
            "item_count": len((saved or {}).get("items") or []),
            "breakdown": breakdown,
            "unit_cost": round(unit, 2),
            # 小计按单台用量算：整机成本是它们的和，不是单件的和。
            "subtotal": round(unit * quantity, 2),
            "summary": (saved or {}).get("summary") or "",
            "open_questions": len((saved or {}).get("open_questions") or []),
        })
    return rows


def _assembly_row(plan) -> dict:
    """整机行：2.2 算出来的组装成本（材料含各零件成本、人工含组装工时）。"""
    analysis = plan.cost.model_dump() if plan.cost else None
    breakdown = cost_model.breakdown(analysis) if analysis else {}
    total = float(breakdown.get("total") or 0)
    return {
        "id": ASSEMBLY_ID,
        "name": (plan.params.assembly_name if plan.params else "") or "整机总成",
        "kind": "assembly",
        "quantity": 1,
        "has_cost": bool(analysis and (analysis.get("items") or [])),
        "item_count": len((analysis or {}).get("items") or []),
        "breakdown": breakdown,
        "unit_cost": round(total, 2),
        "subtotal": round(total, 2),
        "summary": (analysis or {}).get("summary") or "",
        "open_questions": len((analysis or {}).get("open_questions") or []),
    }


def summarize(project_id: str, ir: Optional[DesignIR], plan) -> dict:
    """2.3 的全貌：零件逐项 + 整机 + 两种口径的合计。

    给两个合计是有意的，它们回答不同的问题：
      · parts_total —— 零件成本之和（各零件单件 × 单台用量），"料工费加起来多少"；
      · assembly    —— 2.2 算出来的整机成本，它**已经包含**零件成本（材料项就是
                       逐个零件引过来的），再加组装工序的人工与费用。
    所以对外报价用的是 assembly，不是两者相加 —— 相加会把零件成本算两遍。
    """
    parts = _part_rows(project_id, ir)
    assembly = _assembly_row(plan)
    parts_total = merge_totals([row["breakdown"] for row in parts
                                for _ in range(row["quantity"])])
    missing = [row["id"] for row in parts if not row["has_cost"]]
    # 跑过 ≠ 算出来了：模型给不出材料明细时四项全是 0，界面上和"没算"长得一样，
    # 但 has_cost 是真。零元的行要单独点名，不能让它一路送到报价。
    zero = [row["id"] for row in parts + [assembly]
            if row["has_cost"] and row["unit_cost"] <= 0]
    return {
        "parts": parts,
        "assembly": assembly,
        "parts_total": parts_total,
        # 对外口径：整机成本。它已含零件成本，不能与 parts_total 相加。
        "final": assembly["breakdown"] or {},
        "counts": {
            "parts": len(parts),
            "parts_costed": len(parts) - len(missing),
            "missing": missing,
            "zero": zero,
            "assembly_costed": assembly["has_cost"],
        },
        "ready": bool(parts) and not missing and assembly["has_cost"] and not zero,
    }


def payload(project_id: str, ir: Optional[DesignIR], plan, review: CostReview) -> dict:
    """接口统一返回体。前端只认这一种形状。"""
    data = summarize(project_id, ir, plan)
    written = plan.material_writes[-1] if plan.material_writes else None
    # 「整合参数」这个收口环节从 2.2 搬到了这里：报价必填项要在**出成本、发报价之前**
    # 补齐，而发报价已经是财务的动作。表格的行由 DA 字典定，见 product_params。
    checklist = product_params.checklist(plan.params) if plan.params else None
    review_dict = review.model_dump()
    review_dict["params_final"] = bool(plan.params_final)
    return {
        "review": review_dict,
        "param_checklist": checklist,
        "params_plan": plan.params.model_dump() if plan.params else None,
        "required_missing": (len(product_params.missing_required(plan.params))
                             if plan.params else 0),
        # 参数表里「成品编码」那一格要按它决定是显示「由系统生成」还是给出警告，
        # 说明手上这个号主数据里并不存在。
        "has_material_code": bool(plan.material_writes),
        **data,
        "quantity": plan.quantity,
        "material": ({"number": written.number, "name": written.name,
                      "unit_price": written.material_unit_price} if written else None),
        "quote_handoff": plan.quote_handoff.model_dump() if plan.quote_handoff else None,
        "done_kinds": sorted(review.done_kinds),
    }


# --------------------------------------------------------------------------- #
# 测算（不联网）
# --------------------------------------------------------------------------- #
def run_part(project_id: str, ir: DesignIR, part_id: str, quantity: int,
             note: str = "", progress: ProgressFn = None) -> dict:
    """算一个零件的成本。**不联网** —— 只用企业成本库 + 模型的工程经验。"""
    part = next((item for item in ir.parts if item.part_id == part_id), None)
    if part is None:
        raise ValueError(f"零件 {part_id} 不存在")
    qty = max(1, int(quantity or 1))
    _report(progress, f"检索成本库：{part_id} {part.name or ''}（批量 {qty}）")
    lookup = cost_lookup.lookup_part(
        part.model_dump(),
        quantity=qty,
        process_report=store.load_process_lookup(project_id, part_id),
        progress=progress,
    )
    # needs_web_search 原本用来决定"要不要联网补价"；这一步不联网，但它仍然是
    # "库里够不够"的现成判据 —— 不够就明说，让财务知道哪些数是估的、需要去询价。
    _report(progress, "按库内依据与工程经验测算（本步不联网检索行情价）"
                      + ("；库内价格有缺口，缺的项会写进待确认，请人工询价"
                         if cost_svc.needs_web_search(lookup) else "；库内价格与费率齐全"))
    analysis = cost_svc.analyze_cost(
        part, overall=ir, geom=None, quantity=qty,
        web=False,                       # ← 2.3 一律不联网，见模块 docstring
        note=note,
        library=cost_lookup.as_prompt(lookup) if lookup else "",
    )
    normalized = cost_model.normalize(analysis.model_dump())
    breakdown = normalized["cost_model"]
    _report(progress, f"  ↳ {part_id}：材料 {breakdown['material']:.2f} · "
                      f"人工 {breakdown['labor']:.2f} · 制费 {breakdown['overhead']:.2f} · "
                      f"加工 {breakdown['machining']:.2f} = {breakdown['total']:.2f} 元/件")
    return normalized


def run_assembly(project_id: str, ir: Optional[DesignIR], plan, note: str = "",
                 progress: ProgressFn = None):
    """算整机（组装）成本。同样不联网。

    直接复用 2.2 的 analyze_cost —— 组装成本的输入（BOM、组装工序、各零件已测算
    单件成本）都在那套上下文里，另写一份只会让两处的口径漂移。
    """
    qty = max(1, int(plan.quantity or 1))
    _report(progress, f"检索成本库：整机 {(plan.params.assembly_name if plan.params else '') or ''}"
                      f"（批量 {qty}）")
    lookup = cost_lookup.lookup_part(
        integration.pseudo_part(plan), quantity=qty,
        process_report=store.load_process_lookup(project_id, ASSEMBLY_ID),
        progress=progress,
    )
    _report(progress, "按库内依据与工程经验测算整机成本（本步不联网检索行情价）")
    analysis = integration.analyze_cost(
        project_id, ir, plan, quantity=qty,
        web=False,                       # ← 同上
        library=cost_lookup.as_prompt(lookup) if lookup else "",
        note=note, progress=progress,
    )
    return analysis, lookup

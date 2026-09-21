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

from ..models.cost_review import CostReview, CostReviewWaiver, merge_totals
from ..models.ir import DesignIR
from ..storage import store
from ..time_utils import now_cst_str
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


def confirm_gaps(project_id: str, ir: Optional[DesignIR], plan,
                 data: Optional[dict] = None) -> dict:
    """2.3「确认成本」这一步的缺口：未算零件 / 整机未算 / 算出来是 0 元。

    直接复用 summarize()（本步唯一的汇总口径），不另算一份。返回
    {"codes": [...比对键], "fields": [...中文名], "text": 一句话, "count": n,
     "parts": 零件总数}；codes 与 fields 顺序一致。
    比对键带类型前缀（part:P1:missing / assembly:missing / P1:zero），
    签字之后新冒出的缺口会让集合变大，覆盖判定因此失败、必须重新签。
    """
    data = data if data is not None else summarize(project_id, ir, plan)
    counts = data["counts"]
    codes: List[str] = []
    fields: List[str] = []
    for part_id in counts["missing"]:
        codes.append(f"part:{part_id}:missing")
        fields.append(f"零件 {part_id} 没算成本")
    if not counts["assembly_costed"]:
        codes.append("assembly:missing")
        fields.append("整机（组装）成本还没算")
    for row_id in counts["zero"]:
        # 中文里不加空格：「整机（组装）算出来是 0 元」/「零件 P1 算出来是 0 元」。
        label = "整机（组装）" if str(row_id) == ASSEMBLY_ID else f"零件 {row_id}"
        codes.append(f"{row_id}:zero")
        fields.append(f"{label}算出来是 0 元" if str(row_id) == ASSEMBLY_ID
                      else f"{label} 算出来是 0 元")
    return {
        "codes": codes,
        "fields": fields,
        "count": len(codes),
        "parts": counts["parts"],
        "text": ("；".join(fields) + "。" if fields else ""),
    }


def waiver_reason(waiver: Optional[dict]) -> str:
    """请求体里的签字只取人工填的原因；原因留空时由调用方补默认原因。"""
    if isinstance(waiver, dict):
        return str(waiver.get("reason") or "")
    return ""


def record_waiver(review: CostReview, *, gaps: Optional[dict] = None, reason: str = "",
                  actor: Optional[dict] = None, reused: bool = False,
                  stage: str = "cost_review") -> CostReviewWaiver:
    """写入一条 2.3 的缺口签字，返回该记录（调用方负责 store.save_cost_review）。

    缺口一律以服务端算出的为准；reason 为空时补默认原因，并写入签字人与签字时间。
    同一批缺口已被上一条签字覆盖时用 reused=True 记录，不再重复索要。
    """
    actor = actor or {}
    gaps = gaps or {}
    codes = [str(item) for item in (gaps.get("codes") or [])]
    fields = [str(item) for item in (gaps.get("fields") or [])]
    default_reason = (f"带缺口继续：{'、'.join(fields) if fields else '无'}，"
                      f"已在 {stage} 由本人签字放行")
    waiver = CostReviewWaiver(
        stage=stage,
        missing_codes=codes,
        missing_fields=fields,
        reason=(str(reason or "").strip() or default_reason),
        waived_by=(actor.get("display_name") or actor.get("username") or "system"),
        waived_at=now_cst_str(),
        reused=bool(reused),
    )
    review.waivers = list(getattr(review, "waivers", None) or [])
    review.waivers.append(waiver)
    return waiver


def waiver_covers(review: CostReview, codes) -> Optional[CostReviewWaiver]:
    """同一批缺口已经签过字时返回那条签字；出现新缺口返回 None（必须重新签）。"""
    need = {str(item) for item in (codes or []) if str(item or "").strip()}
    if not need:
        return None
    for waiver in reversed(list(getattr(review, "waivers", None) or [])):
        have = {str(item) for item in (getattr(waiver, "missing_codes", None) or [])}
        if need <= have:
            return waiver
    return None


def waiver_summary(review: CostReview) -> Optional[dict]:
    """最近一条本步签字摘要（2.3 看板与前端闸门共用）。没有则 None。"""
    waivers = list(getattr(review, "waivers", None) or [])
    if not waivers:
        return None
    waiver = waivers[-1]
    return {
        "stage": waiver.stage,
        "missing_codes": list(waiver.missing_codes),
        "missing_fields": list(waiver.missing_fields),
        "reason": waiver.reason,
        "waived_by": waiver.waived_by,
        "waived_at": waiver.waived_at,
        "reused": bool(waiver.reused),
        "count": len(waivers),
    }


def payload(project_id: str, ir: Optional[DesignIR], plan, review: CostReview) -> dict:
    """接口统一返回体。前端只认这一种形状。"""
    data = summarize(project_id, ir, plan)
    written = plan.material_writes[-1] if plan.material_writes else None
    # 「整合参数」这个收口环节从 2.2 搬到了这里：报价必填项要在**出成本、发报价之前**
    # 补齐，而发报价已经是财务的动作。表格的行由 DA 字典定，见 product_params。
    # 4.3 的价格测算单按行业锁定的族出清单：包装项目看的是盒型/内尺寸，不是工作温度。
    family = integration.project_family(project_id)
    checklist = product_params.checklist(plan.params, family) if plan.params else None
    review_dict = review.model_dump()
    review_dict["params_final"] = bool(plan.params_final)
    # 「带缺口继续」的签字与"报价必填到底齐没齐"要一并交给财务：缺口是**人签过字的**，
    # 就不能再按同一批缺口把财务拦在门口（L1 生成依赖与 L4 写库 / 发报价 / 审核发布
    # 仍各自硬校验，不受影响）。复用 integration 的唯一实现，这里不另算一份。
    review_dict["params_complete"] = (bool(plan.params)
                                      and not integration.missing_required(plan, family))
    review_dict["waiver"] = integration.waiver_summary(plan)
    # 本步自己的缺口与签字：前端据此决定「仍要继续」还弹不弹 —— 同一批缺口签过字就
    # 不弹第二次；缺口本身如实带出去（签字只放行，不抹掉）。
    review_dict["gaps"] = confirm_gaps(project_id, ir, plan, data)
    review_dict["cost_waiver"] = waiver_summary(review)
    return {
        "review": review_dict,
        "param_checklist": checklist,
        "params_plan": plan.params.model_dump() if plan.params else None,
        "required_missing": len(integration.missing_required(plan, family)),
        # 参数表里「成品编码」那一格要按它决定是显示「由系统生成」还是给出警告，
        # 说明手上这个号主数据里并不存在。
        "has_material_code": bool(plan.material_writes),
        # 财务看板要能一眼看出"缺的这几项工艺经理签过字了"：params_complete 说数齐没齐，
        # waiver 说缺口是谁在什么时候签字放行的（没有签字时为 None）。
        "params_complete": review_dict["params_complete"],
        "waiver": review_dict["waiver"],
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

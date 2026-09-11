"""
2.3 成本测算的流转正文 —— 路由与平台 Agent 工具共用的**唯一实现**。

本模块只做「编排」：把已经算好的成本汇总、确认状态、去向留痕串起来，并调用
``cpq_bridge`` 完成对外动作。它**不算成本**：四项成本一律来自 ``cost_model`` /
``cost_review`` 的既有实现，2.3 不另开口径。

为什么单独成一层：2.3 的四个流转（确认成本 / 写入物料 / 发送报价 / 返回工艺）既有人工
在看板上点按钮的入口（HTTP 路由），也有统一左侧 Agent 的入口（平台工具）。两条路必须
落在同一份实现上，否则「Agent 确认过的」和「看板显示的」会对不上。``oc_agent.py`` 不能
import ``main``，所以共享实现只能待在 services 层，由路由与工具分别调用。

对外动作（写库 / 发报价 / 退回）需要用户的 CPQ 令牌，由调用方经 ``token`` 传入 —— 路由
取自请求头，Agent 取自 ``current_token()``。本模块不读取、记录或回显令牌。
"""
from __future__ import annotations

from typing import Optional

from ..models.cost_review import CostAction
from ..models.integration import MaterialWrite, QuoteHandoff
from ..storage import store
from ..time_utils import now_cst_str
from . import cost_model, cost_review, cpq_bridge, integration, product_params

# 2.2 的对外正文与本模块共用：写库时主数据行数、发报价时回传的结论都一样。
# 统一放在这里，路由与 Agent 工具都从这里取，避免出现第二份口径。


class CostFlowError(Exception):
    """2.3 流转的业务拒绝：前置结果缺失、未确认或桥接失败，调用方映射成 HTTP。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def bridge_call(action, *args, **kwargs):
    """把桥接层的两类失败翻译成业务错误：业务拒绝 400，服务不可用 503。"""
    try:
        return action(*args, **kwargs)
    except cpq_bridge.BridgeRejected as exc:
        raise CostFlowError(str(exc), 400) from exc
    except cpq_bridge.BridgeUnavailable as exc:
        raise CostFlowError(f"业务数据库/报价服务暂不可用：{exc}", 503) from exc


# --------------------------------------------------------------------------- #
# 2.3 上下文与状态
# --------------------------------------------------------------------------- #
def cost_review_ctx(project_id: str):
    """2.3 的三样输入：IR（零件）、2.2 的整机方案、本步的评审状态。"""
    from ..models.ir import DesignIR

    if not store.load_meta(project_id):
        raise CostFlowError("项目不存在", 404)
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    return ir, integration.load_plan(project_id), cost_review.load_review(project_id)


def _ready(project_id: str):
    """三个去向动作的共同前置：成本得先算齐并确认。"""
    ir, plan, review = cost_review_ctx(project_id)
    if not review.confirmed:
        raise CostFlowError("请先点「确认成本」：三个去向都以确认过的成本为准")
    return ir, plan, review


def _record_action(project_id: str, review, kind: str, label: str, detail: str,
                   user: dict) -> None:
    review.actions.append(CostAction(
        kind=kind, label=label, detail=detail[:300], at=now_cst_str(),
        by=user.get("display_name") or user.get("username") or ""))
    cost_review.save_review(project_id, review, user.get("username", "system"))


def save_note(project_id: str, user: dict, *, note: str = "", quantity: int = 1) -> dict:
    """保存财务的补充说明与核算批量（会作为下一次测算的输入）。"""
    ir, plan, review = cost_review_ctx(project_id)
    review.note = note
    cost_review.save_review(project_id, review, user.get("username", "system"))
    qty = max(1, int(quantity or 1))
    if plan.quantity != qty:
        plan.quantity = qty
        integration.save_plan(project_id, plan, user.get("username", "system"))
    return cost_review.payload(project_id, ir, plan, review)


def confirm_review(project_id: str, user: Optional[dict] = None) -> dict:
    """财务确认本步成本。零件没算齐、或者哪一项是 0，都要先解决。"""
    ir, plan, review = cost_review_ctx(project_id)
    data = cost_review.summarize(project_id, ir, plan)
    counts = data["counts"]
    if counts["missing"]:
        raise CostFlowError(f"还有零件没算成本：{'、'.join(counts['missing'])}")
    if not counts["assembly_costed"]:
        raise CostFlowError("整机（组装）成本还没算")
    if counts["zero"]:
        raise CostFlowError(
            f"这些行算出来是 0 元：{'、'.join(counts['zero'])}。"
            "请重算或人工补上材料明细 —— 0 元送到报价那头会变成没有成本的产品")
    review.confirmed = True
    review.confirmed_by = (user or {}).get("display_name") or (user or {}).get("username") or ""
    review.confirmed_at = now_cst_str()
    cost_review.save_review(project_id, review, (user or {}).get("username", "system"))
    store.audit(project_id, "cost_review_confirm",
                {"by": review.confirmed_by, "total": data["final"].get("total")})
    return cost_review.payload(project_id, ir, plan, review)


# --------------------------------------------------------------------------- #
# 2.2 的两个对外正文（写主数据 / 发送报价）—— 2.2 与 2.3 路由共用
# --------------------------------------------------------------------------- #
def integration_ready_cost(project_id: str):
    """取出可以对外的 2.2 结果。成本没算完就不该往主数据和报价里送。"""
    plan = integration.load_plan(project_id)
    if not (plan.cost and plan.cost.items):
        raise CostFlowError("请先完成整机成本测算，再写入数据库或发送至报价")
    return plan


def integration_quote_result(project_id: str, plan, title: str) -> dict:
    """随「发送至报价」一起回传的整机结论（按 DA 字段拉平的参数 + 四项成本）。"""
    breakdown = cost_model.breakdown(plan.cost.model_dump()) if plan.cost else {}
    written = plan.material_writes[-1] if plan.material_writes else None
    return {
        "project_id": project_id,
        "product_name": title,
        "assembly_name": (plan.params.assembly_name if plan.params else "") or title,
        "quantity": plan.quantity,
        # 写库是另一个按钮，可能还没点 —— 没有成品编码不该挡住推送，但要说清楚。
        "material": ({"number": written.number, "name": written.name,
                      "material_id": written.material_id,
                      "unit_price": written.material_unit_price} if written else None),
        "cost": breakdown,
        "params": product_params.rows_for_quote(plan.params) if plan.params else None,
        "process_step_count": len(plan.process.steps) if plan.process else 0,
        "params_final": bool(plan.params_final),
    }


def requirement_customer(requirement: dict, req_data: dict) -> str:
    """需求单里的客户名（顶层 / data / 早期 customer 逐个兜底）。"""
    for value in (requirement.get("customer_name"), req_data.get("customer_name"),
                  requirement.get("customer"), req_data.get("customer"),
                  (req_data.get("quote") or {}).get("customer")):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def integration_material_write_record(project_id: str, plan, *,
                                      product_name: str = "", spec: str = "",
                                      token: str = "", user: Optional[dict] = None) -> MaterialWrite:
    """取号 + 写两张主数据表 + 把编码回填进整机参数。就地改 plan，不落盘。

    2.2 的「写入数据库」与「发送至报价」的自动补编码、2.3 的「写入数据库」都调它。
    """
    user = user or {}
    name = (product_name or "").strip() or (
        plan.params.assembly_name if plan.params else "") or ""
    if not name:
        raise CostFlowError("缺少产品名称：请先完成参数推荐，或在写入时填写产品名称")
    breakdown = cost_model.breakdown(plan.cost.model_dump())
    result = bridge_call(cpq_bridge.write_material, token,
                         name, breakdown["total"], breakdown, spec)
    record = MaterialWrite(
        material_id=str(result.get("material_id") or ""),
        number=str(result.get("number") or ""),
        name=str(result.get("name") or name),
        material_unit_price=result.get("material_unit_price"),
        breakdown=breakdown,
        tables=result.get("tables") or [],
        written_at=now_cst_str(),
        written_by=user.get("display_name") or user.get("username") or "",
    )
    plan.material_writes.append(record)
    # 成品编码回填进整机参数：报价按成品编码匹配定价/加价规则，产品行没有编码，
    # 那边规则查得到、加价值却落不到产品上（见 services/integration.apply_material_code）。
    integration.apply_material_code(plan, record.number, record.name)
    return record


def integration_send_to_quote_body(project_id: str, *, product_name: str = "",
                                   spec: str = "", note: str = "",
                                   token: str = "", user: Optional[dict] = None) -> dict:
    """「发送至报价」的正文。**不含权限判断** —— 由调用方各自把门。

    两个入口：2.2 归工艺经理（MANAGER_ROLES），2.3 归财务经理（COST_ROLES）。
    权限和正文必须分开，复用正文的人才不会连带继承另一步的权限。
    """
    user = user or {}
    plan = integration_ready_cost(project_id)
    # 报价必填项没齐就不给发：这些参数就是这次要**回传**给报价的东西，缺一格，
    # 那边的测算单上就是一格空白。「整合参数」环节补。
    missing = product_params.missing_required(plan.params) if plan.params else []
    if missing:
        names = "、".join(field["name"] for field in missing[:8])
        more = f" 等 {len(missing)} 项" if len(missing) > 8 else ""
        raise CostFlowError(
            f"报价必填的成品参数还缺：{names}{more}。请先在「整合参数」环节补填并确认")
    # 成品编码必须有 —— 报价的定价与加价规则是按**产品行里的那串字符**匹配的。但它
    # 不该成为一道门：编码只能由系统生成，那就在这里生成。业务主数据写不进去也
    # **不中断推送** —— 改用本地临时号，参数照样送到报价；等库恢复了再补写。
    auto_written = None
    code_fallback = None
    if not plan.material_writes:
        try:
            auto_written = integration_material_write_record(
                project_id, plan, product_name=product_name, spec=spec,
                token=token, user=user)
            store.audit(project_id, "integration_material_write", {
                "number": auto_written.number, "price": auto_written.material_unit_price,
                "by": auto_written.written_by, "auto": "发送至报价时自动生成"})
        except CostFlowError as exc:
            number = integration.next_local_code()
            integration.apply_material_code(
                plan, number,
                (product_name or "").strip()
                or (plan.params.assembly_name if plan.params else "") or f"整机 {project_id}",
                source="临时编码", basis="业务主数据暂不可用，发送至报价时本地生成")
            code_fallback = {"number": number, "reason": str(exc)[:300]}
            store.audit(project_id, "integration_material_write_fallback",
                        {"number": number, "reason": code_fallback["reason"][:200],
                         "by": user.get("username", "system")})
        integration.save_plan(project_id, plan, user.get("username", "system"))
    title = (product_name or "").strip() or (
        plan.params.assembly_name if plan.params else "") or f"技术工艺项目 {project_id}"
    requirement = store.load_requirement(project_id) or {}
    req_data = requirement.get("data") or {}

    result = bridge_call(
        cpq_bridge.send_to_quote, token, project_id, title,
        requirement_customer(requirement, req_data),
        str(requirement.get("product_name") or req_data.get("product_name") or ""),
        note or "技术工艺已确认，请进入定价",
        # 这单是从报价的「新增工艺」任务过来的：原样退回给当初发起的那个人，
        # 而不是新开一张卡片再群发给销售角色。
        str(req_data.get("source_task_id") or ""),
        integration_quote_result(project_id, plan, title),
        # 任务行被删/被后来的任务顶掉时，会话号是认回原卡片的最后一条线索。
        str(req_data.get("source_session_id") or ""))
    handoff = result.get("handoff") or result.get("auto_handoff") or {}
    plan.quote_handoff = QuoteHandoff(
        session_id=str(result.get("quote_session_id") or project_id),
        next_step_no=result.get("next_step_no"),
        next_step_name=result.get("next_step_name") or "",
        target_role_name=(handoff.get("target_role_name")
                          or result.get("next_role_name") or ""),
        target_name=str(handoff.get("target_name") or ""),
        returned_to_sender=bool(handoff.get("returned_to_sender")),
        source_task_no=str(handoff.get("source_task_no") or ""),
        returned_sections=list(result.get("returned_sections") or []),
        task_id=str(handoff.get("task_id") or "") or None,
        sent_at=now_cst_str(),
        sent_by=user.get("display_name") or user.get("username") or "",
    )
    # 发到报价意味着工艺侧定稿；这里顺手把 2.2 标成已确认，省得再点一次。
    if not plan.confirmed:
        plan.confirmed = True
        plan.confirmed_by = user.get("username", "system")
        plan.confirmed_at = plan.quote_handoff.sent_at
        plan.timing.status = "done"
        plan.timing.completed = True
        plan.timing.finished_at = plan.confirmed_at
    integration.save_plan(project_id, plan, user.get("username", "system"))
    store.audit(project_id, "integration_send_to_quote",
                {"next_step": plan.quote_handoff.next_step_name,
                 "task_id": plan.quote_handoff.task_id, "by": plan.quote_handoff.sent_by})
    return {**integration.payload(project_id, plan),
            "handoff": plan.quote_handoff.model_dump(),
            "auto_written": auto_written.model_dump() if auto_written else None,
            "code_fallback": code_fallback,
            "linked_by": result.get("linked_by") or "",
            "new_card": bool(result.get("new_card"))}


# --------------------------------------------------------------------------- #
# 2.3 的三个去向
# --------------------------------------------------------------------------- #
def write_material(project_id: str, user: Optional[dict] = None, *,
                   product_name: str = "", spec: str = "", token: str = "") -> dict:
    """去向①：写入数据库（新建成品编码 + 物料成本配置）。"""
    ir, plan, review = _ready(project_id)
    record = integration_material_write_record(
        project_id, plan, product_name=product_name, spec=spec, token=token, user=user)
    integration.save_plan(project_id, plan, (user or {}).get("username", "system"))
    _record_action(project_id, review, "material-write", "写入数据库",
                   f"成品编码 {record.number}「{record.name}」，"
                   f"单价 {record.material_unit_price} 元", user or {})
    store.audit(project_id, "cost_review_material_write",
                {"number": record.number, "by": (user or {}).get("username", "system")})
    return {**cost_review.payload(project_id, ir, plan, review),
            "written": record.model_dump()}


def send_to_quote(project_id: str, user: Optional[dict] = None, *,
                  product_name: str = "", spec: str = "", note: str = "",
                  token: str = "") -> dict:
    """去向②：发送至报价（复用 2.2 那条推送，成本与参数一并带回）。"""
    _ready(project_id)
    # 走正文，不走 2.2 那个路由函数：那里的权限认的是工艺经理。
    result = integration_send_to_quote_body(
        project_id, product_name=product_name, spec=spec, note=note,
        token=token, user=user)
    ir, plan, review = cost_review_ctx(project_id)
    handoff = result.get("handoff") or {}
    _record_action(
        project_id, review, "send-to-quote", "发送至报价",
        f"卡片进入第 {handoff.get('next_step_no') or 3} 步"
        f"「{handoff.get('next_step_name') or '定价-利润加成'}」"
        + (f"，已退回给{handoff.get('target_name')}" if handoff.get("returned_to_sender")
           else f"，已通知{handoff.get('target_role_name') or '销售经理'}"), user or {})
    store.audit(project_id, "cost_review_send_to_quote",
                {"task_id": handoff.get("task_id"),
                 "by": (user or {}).get("username", "system")})
    return {**cost_review.payload(project_id, ir, plan, review),
            "handoff": handoff, "auto_written": result.get("auto_written"),
            "code_fallback": result.get("code_fallback"),
            "linked_by": result.get("linked_by") or "",
            "new_card": bool(result.get("new_card"))}


def return_to_process(project_id: str, user: Optional[dict] = None, *,
                      product_name: str = "", note: str = "",
                      target_user_id: str = "", token: str = "") -> dict:
    """去向③：把结果退回工艺经理复核。

    不要求先确认 —— 退回的场景恰恰是"这个成本我认不了"：工序或用量有问题，
    要工艺经理去改。硬卡着确认，等于逼财务先认可一份他不认可的数。
    """
    user = user or {}
    ir, plan, review = cost_review_ctx(project_id)
    data = cost_review.summarize(project_id, ir, plan)
    requirement = store.load_requirement(project_id) or {}
    req_data = requirement.get("data") or {}
    title = (product_name or "").strip() or (
        plan.params.assembly_name if plan.params else "") or f"技术工艺项目 {project_id}"
    result = bridge_call(
        cpq_bridge.return_to_process, token, project_id, title,
        requirement_customer(requirement, req_data),
        str(requirement.get("product_name") or req_data.get("product_name") or ""),
        note or "成本已测算，请复核工艺与用量",
        {"cost_review": {"project_id": project_id, "final": data["final"],
                         "parts_total": data["parts_total"],
                         "counts": data["counts"], "note": note}},
        target_user_id)
    _record_action(
        project_id, review, "return-to-process", "退回工艺经理",
        f"任务 {result.get('task_no') or ''} 已发给"
        f"{result.get('target_role_name') or '工艺经理'}", user)
    ir, plan, review = cost_review_ctx(project_id)
    return {**cost_review.payload(project_id, ir, plan, review), "returned": result}

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


# 成本结果回传的语义与版本：任务 payload 与审计里都带上，重复点击时用它做幂等键。
# 只描述"这份结果是哪一版"，不参与任何成本计算。
HANDOFF_VERSION = "cost-handoff-v1"
HANDOFF_KIND_COST = "cost_to_quote"
HANDOFF_KIND_COST_CONFIRM = "cost_to_process"


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


def result_version(plan) -> str:
    """本次成本结果的版本号：同一份数据重复回传时据此识别，不再重复建 open 任务。"""
    total = 0
    if plan.cost:
        try:
            total = cost_model.breakdown(plan.cost.model_dump())["total"]
        except Exception:
            total = 0
    return f"cost-v1:{int(plan.quantity or 1)}:{total}"


def _process_route(plan) -> dict:
    """组装工艺路线摘要。只做字段投影，不复制工艺算法。"""
    process = plan.process
    steps = []
    for step in (process.steps if process else []):
        try:
            data = step.model_dump(mode="json")
        except AttributeError:
            data = dict(step or {})
        steps.append({
            "step_no": data.get("step_no"),
            "name": data.get("name") or "",
            "type": str(data.get("type") or ""),
            "description": data.get("description") or "",
            "equipment": data.get("equipment") or "",
            "duration_min": data.get("duration_min"),
        })
    return {
        "part_id": (process.part_id if process else "") or "",
        "part_name": (process.part_name if process else "") or "",
        "summary": (process.summary if process else "") or "",
        "steps": steps,
    }


def _cost_snapshot(project_id: str, plan) -> dict:
    """这次成本的全貌（零件逐项 + 整机 + 合计）。复用 cost_review.summarize，不另算。"""
    from ..models.ir import DesignIR

    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    return cost_review.summarize(project_id, ir, plan)


def _quote_session_hint(plan, requirement: dict) -> str:
    """认回原报价会话的两条现成线索：需求单里的报价会话号，或最近一次回传的会话号。"""
    req_data = (requirement or {}).get("data") or {}
    return (str(req_data.get("source_session_id") or "").strip()
            or str(((plan.quote_handoff.session_id if plan.quote_handoff else "") or "")).strip())


def requirement_customer(requirement: dict, req_data: dict) -> str:
    """需求单里的客户名（顶层 / data / 早期 customer 逐个兜底）。"""
    for value in (requirement.get("customer_name"), req_data.get("customer_name"),
                  requirement.get("customer"), req_data.get("customer"),
                  (req_data.get("quote") or {}).get("customer")):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def integration_quote_result(project_id: str, plan, title: str,
                             requirement: Optional[dict] = None) -> dict:
    """随「发送至报价」一起回传的整机结论（按 DA 字段拉平的参数 + 四项成本）。

    报价那边的第 2 步快照与第 3 步定价表都从这份结论取数，所以除了参数与整机成本，
    这里还要带上零件成本、组装成本、工艺路线摘要与财务确认信息 —— 销售打开卡片就
    是带着完整技术结果的第 3 步，不需要重新问一遍技术工艺。
    数字一律来自既有 cost_model / cost_review，本函数只做投影，不复制成本算法。
    """
    requirement = requirement or {}
    review = cost_review.load_review(project_id)
    snapshot = _cost_snapshot(project_id, plan)
    breakdown = cost_model.breakdown(plan.cost.model_dump()) if plan.cost else {}
    written = plan.material_writes[-1] if plan.material_writes else None
    return {
        "project_id": project_id,
        # 技术项目号与报价会话号必须分开写：前者是溯源，后者才是报价卡片。
        "tech_project_id": project_id,
        "quote_session_id": _quote_session_hint(plan, requirement),
        "source_session_id": str((requirement.get("data") or {}).get("source_session_id") or ""),
        "source_task_id": str((requirement.get("data") or {}).get("source_task_id") or ""),
        "handoff_kind": HANDOFF_KIND_COST,
        "handoff_version": HANDOFF_VERSION,
        "result_version": result_version(plan),
        "product_name": title,
        "assembly_name": (plan.params.assembly_name if plan.params else "") or title,
        "quantity": plan.quantity,
        # 写库是另一个按钮，可能还没点 —— 没有成品编码不该挡住推送，但要说清楚。
        "material": ({"number": written.number, "name": written.name,
                      "material_id": written.material_id,
                      "unit_price": written.material_unit_price} if written else None),
        "cost": breakdown,
        "cost_breakdown": breakdown,
        "cost_confirmation": {
            "confirmed": bool(review.confirmed),
            "confirmed_by": review.confirmed_by or "",
            "confirmed_at": review.confirmed_at,
            "note": review.note or "",
        },
        "params": product_params.rows_for_quote(plan.params) if plan.params else None,
        "process": _process_route(plan),
        "process_step_count": len(plan.process.steps) if plan.process else 0,
        "part_costs": snapshot.get("parts") or [],
        "parts_total": snapshot.get("parts_total") or {},
        "assembly_cost": snapshot.get("assembly") or {},
        "params_final": bool(plan.params_final),
    }


def process_handoff_package(project_id: str, plan, review, snapshot: dict,
                            requirement: dict, title: str) -> dict:
    """成本结果 → 工艺经理确认的完整交接包。

    工艺经理领取后进的是第 5 大步「工艺评估报告」，所以这里必须把参数、工艺路线、
    逐件成本、组装成本与财务确认一并交过去；只给一句 note 等于让他回头再问一遍。
    字段全部来自既有 plan / review / summarize，不在这一步重算任何成本。
    """
    requirement = requirement or {}
    parts = snapshot.get("parts") or []
    assembly = snapshot.get("assembly") or {}
    final = snapshot.get("final") or {}
    return {
        "handoff_kind": HANDOFF_KIND_COST_CONFIRM,
        "handoff_version": HANDOFF_VERSION,
        "result_version": result_version(plan),
        "tech_project_id": project_id,
        "project_id": project_id,
        "quote_session_id": _quote_session_hint(plan, requirement),
        "source_session_id": str((requirement.get("data") or {}).get("source_session_id") or ""),
        "source_task_id": str((requirement.get("data") or {}).get("source_task_id") or ""),
        "product_name": title,
        "quantity": plan.quantity,
        "params": product_params.rows_for_quote(plan.params) if plan.params else [],
        "params_final": bool(plan.params_final),
        "process": _process_route(plan),
        "part_costs": parts,
        "parts": parts,
        "assembly_cost": assembly,
        "assembly": assembly,
        "cost_breakdown": final,
        "final": final,
        "cost_confirmation": {
            "confirmed": bool(review.confirmed),
            "confirmed_by": review.confirmed_by or "",
            "confirmed_at": review.confirmed_at,
            "note": review.note or "",
        },
        # 工艺经理的正常落点是第 5 大步「工艺评估报告」，不是第 3 大步重新建工艺。
        "confirmed": bool(review.confirmed),
        "target_stage": "summary",
        "target_entry": f"tech-workbench.html?stage=summary&project={project_id}",
    }


def close_source_task(project_id: str, user: Optional[dict], token: str,
                      source_task_id: str, *, comment: str = "") -> dict:
    """完成正式去向后，把来源的那条 claimed 财务待办关掉（幂等）。

    不直接写库：任务表在报价工作流那一侧，这里走既有桥接通道，由 cpq_wf 统一做
    领取人 / 归属 / 状态校验。任务不存在或没带 task_id 时返回 skipped，不影响去向前进。
    """
    task_id = str(source_task_id or "").strip()
    if not task_id:
        return {"closed": False, "skipped": "missing_task_id"}
    try:
        outcome = bridge_call(cpq_bridge.complete_task, token, task_id,
                              project_id, comment or "成本结果已提交工艺经理确认")
        store.audit(project_id, "cost_review_source_task_closed",
                    {"task_id": task_id, "by": (user or {}).get("username", "system"),
                     "already": bool(outcome.get("already"))})
        return {"closed": True, **outcome}
    except CostFlowError as exc:
        # 关不掉旧待办不能反过来吞掉已经成功的业务去向：如实回给界面，让人能手动处理。
        store.audit(project_id, "cost_review_source_task_close_failed",
                    {"task_id": task_id, "error": str(exc)[:200]})
        return {"closed": False, "error": str(exc)}


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
    # 那边的测算单上就是一格空白。「参数推荐」环节补。
    missing = product_params.missing_required(plan.params) if plan.params else []
    if missing:
        names = "、".join(field["name"] for field in missing[:8])
        more = f" 等 {len(missing)} 项" if len(missing) > 8 else ""
        raise CostFlowError(
            f"报价必填的成品参数还缺：{names}{more}。请先在「参数推荐」环节补填并确认")
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
        integration_quote_result(project_id, plan, title, requirement),
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
            "new_card": bool(result.get("new_card")),
            "already_sent": bool(result.get("already_sent")),
            "quote_session_id": str(result.get("quote_session_id") or "")}


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
                  token: str = "", source_task_id: str = "") -> dict:
    """去向②：回传销售经理继续报价（复用 2.2 那条推送，成本与参数一并带回）。

    完成后把来源的 claimed 财务待办关掉 —— 去向下游已经接手，旧待办不该继续挂着。
    """
    _ready(project_id)
    # 走正文，不走 2.2 那个路由函数：那里的权限认的是工艺经理。
    result = integration_send_to_quote_body(
        project_id, product_name=product_name, spec=spec, note=note,
        token=token, user=user)
    closed = close_source_task(project_id, user, token, source_task_id,
                               comment="成本结果已回传销售经理继续报价")
    ir, plan, review = cost_review_ctx(project_id)
    handoff = result.get("handoff") or {}
    _record_action(
        project_id, review, "send-to-quote", "回传销售经理继续报价",
        f"卡片进入第 {handoff.get('next_step_no') or 3} 步"
        f"「{handoff.get('next_step_name') or '定价-利润加成'}」"
        + (f"，已退回给{handoff.get('target_name')}" if handoff.get("returned_to_sender")
           else f"，已通知{handoff.get('target_role_name') or '销售经理'}")
        + ("（重复回传，沿用已有交接）" if result.get("already_sent") else ""), user or {})
    store.audit(project_id, "cost_review_send_to_quote",
                {"task_id": handoff.get("task_id"),
                 "already_sent": bool(result.get("already_sent")),
                 "by": (user or {}).get("username", "system")})
    return {**cost_review.payload(project_id, ir, plan, review),
            "handoff": handoff, "source_task": closed,
            "auto_written": result.get("auto_written"),
            "code_fallback": result.get("code_fallback"),
            "linked_by": result.get("linked_by") or "",
            "quote_session_id": result.get("quote_session_id") or "",
            "already_sent": bool(result.get("already_sent")),
            "new_card": bool(result.get("new_card"))}


def return_to_process(project_id: str, user: Optional[dict] = None, *,
                      product_name: str = "", note: str = "",
                      target_user_id: str = "", token: str = "",
                      source_task_id: str = "") -> dict:
    """去向①：提交工艺经理确认。

    这与旧的"退回返工"不是一回事：财务认可本步成本，把完整结果交给工艺经理，由他在
    第 5 大步做最终工艺确认、汇总、审核和发布。确实需要返工时，由第 5 大步明确退回
    第 3 大步，不能把正常提交确认与返工混在一个模糊按钮里。

    成本必须先确认：未确认的数不该作为正式结果往下走。
    """
    user = user or {}
    ir, plan, review = _ready(project_id)
    snapshot = cost_review.summarize(project_id, ir, plan)
    requirement = store.load_requirement(project_id) or {}
    req_data = requirement.get("data") or {}
    title = (product_name or "").strip() or (
        plan.params.assembly_name if plan.params else "") or f"技术工艺项目 {project_id}"
    package = process_handoff_package(project_id, plan, review, snapshot, requirement, title)
    # 交接包里的这几项要在动作留痕里说清楚：零件成本几项、组装成本是否带上、
    # 合计多少、成本是否已确认、认回的是哪个报价会话 —— 让人一眼看出交出去了什么。
    parts = package.get("part_costs") or []
    assembly = package.get("assembly_cost") or {}
    final = package.get("cost_breakdown") or {}
    confirmed = bool(package.get("confirmed"))
    quote_session_id = str(package.get("quote_session_id") or "")
    result = bridge_call(
        cpq_bridge.return_to_process, token, project_id, title,
        requirement_customer(requirement, req_data),
        str(requirement.get("product_name") or req_data.get("product_name") or ""),
        note or "成本已确认，请做最终工艺确认与报告",
        package, target_user_id, str(source_task_id or ""))
    closed = close_source_task(project_id, user, token, source_task_id,
                               comment="成本结果已提交工艺经理确认")
    _record_action(
        project_id, review, "return-to-process", "提交工艺经理确认",
        f"任务 {result.get('task_no') or ''} 已发给"
        f"{result.get('target_role_name') or '工艺经理'}，进入第 5 大步「工艺评估报告」；"
        f"随包带上零件成本 {len(parts)} 项、组装成本 {'有' if assembly else '无'}、"
        f"合计 {final.get('total') or 0} 元、成本{'已确认' if confirmed else '未确认'}"
        + (f"、原报价会话 {quote_session_id}" if quote_session_id else ""),
        user)
    ir, plan, review = cost_review_ctx(project_id)
    return {**cost_review.payload(project_id, ir, plan, review),
            "returned": result, "source_task": closed,
            "target_stage": "summary", "package": package}

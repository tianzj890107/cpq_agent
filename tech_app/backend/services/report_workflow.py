"""3.1 / 3.2 / 3.3 汇总报告流程的共享正文 —— HTTP 路由与平台 Agent 工具共用的**唯一实现**。

覆盖：生成 / 保存报告草稿、维护发布范围、送审、审核通过 / 退回、正式发布、回传报价、
新建版本，以及它们依赖的确定性门禁（送审前置、内容完备、来源快照一致）。

为什么单独成一层：同一步骤既有人工在看板上点按钮的入口（HTTP 路由），也有统一左侧
Agent 的入口（平台工具）。``oc_agent.py`` 不能 import ``main``，所以共享实现只能待在
services 层；两条入口调用同一份函数，才能保证「Agent 改的」与「看板显示的」一致。

本模块只做编排与门禁，不新增业务口径：汇总数据一律来自 ``services.summary.aggregate``，
报告读写一律走 ``store.load_process_report`` / ``store.save_process_report``。状态流转与
审计动作名只在这里定义（调用方按 ``audit`` 回执落审计），因此不会出现第二套实现。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

from pydantic import BaseModel

from ..config import TECH_SUBSTEPS
from ..models.assembly import AssemblyPlan
from ..models.cleaning import CleaningPlan
from ..models.manufacturing import ManufacturingPlan
from ..models.material import MaterialPlan
from ..models.production import ProductionPlan
from ..models.integration import QuoteHandoff
from ..models.summary import SummaryDoc
from ..models.workflow import ProcessReport, ReportRecipient, WorkflowReview
from ..storage import store
from ..time_utils import now_cst_str
from . import auth, cpq_bridge, summary as summary_svc

# 3.1 允许 Agent / 看板改写的报告字段；单据号、编制人、审核发布留痕、版本号一律服务端维护。
ALLOWED_REPORT_FIELDS = (
    "title", "overview", "highlights", "risks", "conclusion",
    "distribution_scope", "distribution_cc",
)

# 审计动作名（唯一事实源）：调用方按回执里的 action 落审计，不得自造句面。
AUDIT_PREPARED = "workflow:report_prepared"
AUDIT_SAVED = "workflow:report_saved"
AUDIT_DISTRIBUTION = "workflow:report_distribution_updated"
AUDIT_SUBMITTED = "workflow:report_submitted"
AUDIT_PUBLISHED = "workflow:report_published"
AUDIT_NEW_VERSION = "workflow:report_new_version"
AUDIT_SENT_TO_QUOTE = "workflow:report_sent_to_quote"


class ReportWorkflowError(Exception):
    """报告流程的业务拒绝：门禁未过、状态不符或数据缺失，调用方映射成 HTTP。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


# --------------------------------------------------------------------------- #
# 与 main 同口径的纯工具（保持报告留痕、来源摘要稳定）
# --------------------------------------------------------------------------- #
def now_str() -> str:
    return now_cst_str()


def workflow_event(action: str, user: dict, comment: str = "") -> WorkflowReview:
    return WorkflowReview(
        action=action,
        actor=(user or {}).get("username", "system"),
        role=(user or {}).get("role", ""),
        comment=comment or "",
        at=now_str(),
    )


def report_no(project_id: str, requirement_no: str = "") -> str:
    """报告编号沿用 RPT 前缀，并与项目流水号保持一一对应。"""
    return f"RPT-{project_id.upper()}"


def digest_value(value) -> str:
    """为业务快照生成稳定摘要；与报告来源比对共用同一算法。"""
    def normalize(item):
        if isinstance(item, BaseModel):
            return normalize(item.model_dump())
        if isinstance(item, bytes):
            return {"__bytes_sha256__": hashlib.sha256(item).hexdigest(), "size": len(item)}
        if isinstance(item, dict):
            return {str(key): normalize(item[key]) for key in sorted(item, key=str)}
        if isinstance(item, (list, tuple)):
            return [normalize(child) for child in item]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        return str(item)

    payload = json.dumps(normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def report_source_payload(snapshot: dict) -> dict:
    """报告审核依据只取业务数据，排除会随审计写入变化的项目 meta。"""
    source = snapshot or {}
    return {
        "device_name": source.get("device_name"),
        "ir": source.get("ir") or {},
        "steps": source.get("steps") or {},
        "summary": source.get("summary") or {},
    }


def _ensure_project(project_id: str) -> None:
    meta = store.load_meta(project_id)
    if not meta or meta.get("deleted_at"):
        raise ReportWorkflowError("项目不存在", 404)


def _load_report(project_id: str) -> Optional[dict]:
    return store.load_process_report(project_id)


def _require_report(project_id: str) -> ProcessReport:
    saved = _load_report(project_id)
    if not saved:
        raise ReportWorkflowError("评估报告不存在", 404)
    return ProcessReport(**saved)


# --------------------------------------------------------------------------- #
# 确定性门禁（与既有送审 / 审核 / 发布同一份）
# --------------------------------------------------------------------------- #
def prerequisite_issues(project_id: str) -> list[str]:
    """正式报告送审前的确定性门禁；AI 不能代替这些人工确认。

    【CPQ 定制】2.x 各步是否参与门禁，由 config.TECH_SUBSTEPS 决定。CPQ 只启用
    2.1 图纸解析（drawing），所以下面 2.2–2.6 的检查整段跳过——否则流程走到 3.1
    汇总结果后会被"2.2 材料定性尚未完成"永久挡住送审。
    """
    issues: list[str] = []
    requirement = store.load_requirement(project_id) or {}
    if requirement.get("status") != "approved":
        issues.append("需求单尚未由工艺技术总监审核通过")

    ir = store.load_ir(project_id) or {}
    if not (ir.get("parts") or []):
        issues.append("2.1 图纸解析尚未形成有效零件 IR")

    if "material" in TECH_SUBSTEPS:
        material_doc = store.load_material(project_id)
        if not material_doc:
            issues.append("2.2 材料定性尚未完成")
        else:
            material_plan = MaterialPlan(**material_doc)
            if not material_plan.body.selected or not material_plan.body.confirmed:
                issues.append("2.2 主体材料尚未选定并人工确认")
            has_metallization = bool(
                material_plan.metallization.paste
                or material_plan.metallization.layers
                or material_plan.metallization.rationale
            )
            if has_metallization and not material_plan.metallization.confirmed:
                issues.append("2.2 金属化方案已有内容但尚未人工确认")

    if "manufacturing" in TECH_SUBSTEPS:
        manufacturing_doc = store.load_manufacturing(project_id)
        if not manufacturing_doc:
            issues.append("2.3 制造工艺路径尚未完成")
        else:
            manufacturing_plan = ManufacturingPlan(**manufacturing_doc)
            if not manufacturing_plan.path.steps or not manufacturing_plan.path.confirmed:
                issues.append("2.3 工艺路径尚未形成并人工确认")
            if not manufacturing_plan.bom.items or not manufacturing_plan.bom.confirmed:
                issues.append("2.3 工艺 BOM 尚未形成并人工确认")

    if "cleaning" in TECH_SUBSTEPS:
        cleaning_doc = store.load_cleaning(project_id)
        if not cleaning_doc:
            issues.append("2.4 清洗与洁净度方案尚未完成")
        else:
            cleaning_plan = CleaningPlan(**cleaning_doc)
            if not (cleaning_plan.chemical_steps or cleaning_plan.rinse_steps or cleaning_plan.controls):
                issues.append("2.4 清洗与洁净度方案没有有效内容")
            elif not cleaning_plan.confirmed:
                issues.append("2.4 清洗与洁净度方案尚未人工确认")

    if "assembly" in TECH_SUBSTEPS:
        assembly_doc = store.load_assembly(project_id)
        if not assembly_doc:
            issues.append("2.5 组装与检测方案尚未完成")
        else:
            assembly_plan = AssemblyPlan(**assembly_doc)
            if not assembly_plan.assembly.steps or not assembly_plan.assembly.confirmed:
                issues.append("2.5 组装方案尚未形成并人工确认")
            if not assembly_plan.inspection.tests or not assembly_plan.inspection.confirmed:
                issues.append("2.5 检测方案尚未形成并人工确认")

    if "production" in TECH_SUBSTEPS:
        production_doc = store.load_production(project_id)
        if not production_doc:
            issues.append("2.6 产线匹配与产能评估尚未完成")
        else:
            production_plan = ProductionPlan(**production_doc)
            if not production_plan.requirements or not production_plan.conclusion:
                issues.append("2.6 产线需求或总体结论尚未形成")
            if production_plan.inhouse.matches and not production_plan.inhouse.confirmed:
                issues.append("2.6 自有产线匹配结果尚未人工确认")
            if production_plan.outsourcing.plans and not production_plan.outsourcing.confirmed:
                issues.append("2.6 外协方案尚未人工确认")
            if not production_plan.inhouse.matches and not production_plan.outsourcing.plans:
                issues.append("2.6 尚无自有产线匹配或外协处置方案")

    summary_doc = store.load_summary(project_id)
    if not summary_doc:
        issues.append("技术工艺总结尚未生成")
    else:
        summary_doc_model = SummaryDoc(**summary_doc)
        if not summary_doc_model.conclusion or not summary_doc_model.confirmed:
            issues.append("技术工艺总结尚未形成结论并由经理确认")
    return issues


def content_issues(doc: ProcessReport) -> list[str]:
    issues: list[str] = []
    if not doc.title.strip():
        issues.append("报告标题为空")
    if not doc.conclusion.strip():
        issues.append("报告总结论为空")
    if not doc.evaluation_items:
        issues.append("工艺可行性结论为空")
    for item in doc.evaluation_items:
        if not item.conclusion.strip() or item.status in {"待评估", "需补充"}:
            issues.append(f"评估项“{item.item}”尚未形成可送审结论")
        # 只盯「经济可行性」：状态被手工点成「可行」时，结论也可能还是那句成本占位句
        # （2.3 的成本结论没带进来）。上面按状态拦截的规则一条都没有放宽，这条只补
        # 一个「状态过了、结论仍是占位」的缺口，别的评估项（例如「暂无重大风险」）
        # 不受影响。
        elif item.item.strip() == "经济可行性" and any(
                token in item.conclusion
                for token in ("尚未接入", "未接入", "可追溯", "占位")):
            issues.append("评估项“经济可行性”仍是成本占位结论：请先回到 3.1 刷新汇总，"
                          "把 2.3 成本测算的结论带进来再送审")
    if not doc.stage_results:
        issues.append("各工艺阶段汇总结论为空")
    for item in doc.stage_results:
        text = item.conclusion.strip()
        if not text or "尚未" in text or "暂无" in text:
            issues.append(f"阶段“{item.stage}”尚未形成有效结论")
    return list(dict.fromkeys(issues))


def source_is_current(project_id: str, doc: ProcessReport) -> bool:
    current = summary_svc.aggregate(project_id)
    return digest_value(report_source_payload(doc.source_snapshot)) == digest_value(
        report_source_payload(current))


def ready_gaps(project_id: str, doc: Optional[ProcessReport] = None) -> list[str]:
    """送审就绪缺口：只读工具与提交审核共用，保证「看到的缺口」和「被拦的理由」一致。"""
    if doc is None:
        saved = _load_report(project_id)
        doc = ProcessReport(**saved) if saved else None
    issues = prerequisite_issues(project_id)
    if doc is not None:
        issues = issues + content_issues(doc)
    return list(dict.fromkeys(issues))


# --------------------------------------------------------------------------- #
# 3.1 汇总报告
# --------------------------------------------------------------------------- #
def new_report(project_id: str, user: dict) -> ProcessReport:
    requirement = store.load_requirement(project_id) or {}
    aggregate = summary_svc.aggregate(project_id)
    summary = aggregate.get("summary") or {}
    device_name = aggregate.get("device_name") or "未命名项目"
    now = now_str()
    preparer = (user or {}).get("display_name") or (user or {}).get("username", "system")
    number = report_no(project_id)
    return ProcessReport(
        project_id=project_id,
        report_no=number,
        requirement_no=requirement.get("requirement_no", ""),
        title=f"{device_name}工艺评估报告",
        overview=summary.get("overview") or "",
        highlights=summary.get("highlights") or [],
        risks=summary.get("risks") or [],
        conclusion=summary.get("conclusion") or "",
        source_snapshot=aggregate,
        basic_info={
            "report_no": number,
            "requirement_no": requirement.get("requirement_no", ""),
            "prepared_by": preparer,
            "prepared_at": now,
        },
        prepared_by=preparer,
        prepared_at=now,
        updated_at=now,
        history=[workflow_event("report_prepared", user or {})],
    )


def prepare(project_id: str, user: dict) -> dict:
    """生成报告草稿（POST /process-report/prepare 同一实现）。"""
    _ensure_project(project_id)
    current = _load_report(project_id)
    if current and current.get("status") not in ("draft", "rejected"):
        raise ReportWorkflowError("报告已送审或发布，不能覆盖；请基于现有版本继续处理", 409)
    doc = new_report(project_id, user)
    if current:
        prior = ProcessReport(**current)
        doc.version = prior.version + 1
        doc.history = prior.history + [workflow_event("report_refreshed", user or {})]
    out = doc.model_dump()
    return {"report": out, "audit": {"action": AUDIT_PREPARED, "payload": {"version": doc.version}}}


def _persist_save(project_id: str, doc: ProcessReport, user: dict) -> dict:
    current = _load_report(project_id)
    if current and current.get("status") not in ("draft", "rejected"):
        raise ReportWorkflowError("报告已送审或发布，不能直接修改", 409)
    requirement_no = (store.load_requirement(project_id) or {}).get("requirement_no", "")
    expected_report_no = report_no(project_id)
    user = user or {}
    doc.project_id = project_id
    # 单据号、编制人和编制时间由服务端生成，避免页面手动值覆盖正式留痕。
    current_report_no = (current or {}).get("report_no", "")
    doc.report_no = current_report_no or expected_report_no
    doc.requirement_no = requirement_no
    doc.prepared_by = (current or {}).get("prepared_by") or user.get("display_name") or user.get("username", "system")
    doc.prepared_at = (current or {}).get("prepared_at") or now_str()
    doc.version = int((current or {}).get("version") or 1)
    # 审核、发布签名以及正式来源快照均由服务端维护，客户端不能伪造。
    doc.reviewed_by = (current or {}).get("reviewed_by")
    doc.reviewed_at = (current or {}).get("reviewed_at")
    doc.review_note = (current or {}).get("review_note", "")
    doc.published_by = (current or {}).get("published_by")
    doc.published_at = (current or {}).get("published_at")
    doc.recipients = ProcessReport(**current).recipients if current else []
    doc.source_snapshot = summary_svc.aggregate(project_id)
    doc.basic_info = {
        **(doc.basic_info or {}),
        "report_no": doc.report_no,
        "requirement_no": doc.requirement_no,
        "prepared_by": doc.prepared_by,
        "prepared_at": doc.prepared_at,
    }
    doc.status = (current or {}).get("status") if current else "draft"
    doc.history = [WorkflowReview(**row) for row in (current or {}).get("history", [])]
    doc.updated_at = now_str()
    out = doc.model_dump()
    return {"report": out, "audit": {"action": AUDIT_SAVED, "payload": {"report_no": doc.report_no}}}


def save(project_id: str, doc: ProcessReport, user: dict) -> dict:
    """保存报告字段（PUT /process-report 同一实现）。"""
    _ensure_project(project_id)
    return _persist_save(project_id, doc, user)


def update_fields(project_id: str, fields: dict, user: dict) -> dict:
    """Agent 只按白名单改报告字段：其余留痕与单据信息一律沿用服务端当前值。"""
    _ensure_project(project_id)
    saved = _load_report(project_id)
    if not saved:
        raise ReportWorkflowError("请先生成并保存评估报告草稿", 404)
    doc = ProcessReport(**saved)
    for key in ALLOWED_REPORT_FIELDS:
        if key in (fields or {}) and fields[key] is not None:
            setattr(doc, key, fields[key])
    return _persist_save(project_id, doc, user)


def update_distribution(project_id: str, *, distribution_scope: str = "",
                        distribution_cc: str = "", user: Optional[dict] = None) -> dict:
    """在审核或发布阶段维护分发范围，不改变审核 / 发布状态。"""
    _ensure_project(project_id)
    user = user or {}
    doc = _require_report(project_id)
    if doc.status not in ("draft", "rejected", "in_review", "approved"):
        raise ReportWorkflowError("已发布报告不可再维护发布设置", 409)
    doc.distribution_scope = (distribution_scope or "").strip()
    doc.distribution_cc = (distribution_cc or "").strip()
    doc.history.append(workflow_event("report_distribution_updated", user,
                                      "更新发布范围与抄送对象"))
    doc.updated_at = now_str()
    out = doc.model_dump()
    return {"report": out,
            "audit": {"action": AUDIT_DISTRIBUTION,
                      "payload": {"scope": doc.distribution_scope, "cc": doc.distribution_cc}}}


def submit_review(project_id: str, user: Optional[dict] = None, *, comment: str = "") -> dict:
    """提交审核（POST /process-report/submit-review 同一实现）。"""
    _ensure_project(project_id)
    user = user or {}
    saved = _load_report(project_id)
    if not saved:
        raise ReportWorkflowError("请先汇总并保存评估报告", 404)
    doc = ProcessReport(**saved)
    if doc.status not in ("draft", "rejected"):
        raise ReportWorkflowError("当前报告不在可送审状态", 409)
    issues = prerequisite_issues(project_id) + content_issues(doc)
    if issues:
        raise ReportWorkflowError("报告暂不可送审：" + "；".join(issues), 409)
    # 送审时重新冻结一次来源快照，保证审核人与发布人看到同一份依据。
    doc.source_snapshot = summary_svc.aggregate(project_id)
    doc.status = "in_review"
    doc.history.append(workflow_event("report_submitted", user, comment))
    doc.updated_at = now_str()
    out = doc.model_dump()
    return {"report": out, "audit": {"action": AUDIT_SUBMITTED, "payload": {"comment": comment}}}


# --------------------------------------------------------------------------- #
# 3.2 审核报告
# --------------------------------------------------------------------------- #
def review(project_id: str, user: dict, *, decision: str, comment: str = "",
           review_items=None, review_conclusion: str = "",
           distribution_scope: str = "", distribution_cc: str = "") -> dict:
    """审核通过 / 退回（POST /process-report/review 同一实现）。"""
    _ensure_project(project_id)
    user = user or {}
    doc = _require_report(project_id)
    if doc.status != "in_review":
        raise ReportWorkflowError("当前报告不在待审核状态", 409)
    if decision not in ("approve", "reject"):
        raise ReportWorkflowError("decision 必须为 approve 或 reject", 400)
    if decision == "approve":
        issues = prerequisite_issues(project_id) + content_issues(doc)
        if issues:
            raise ReportWorkflowError("报告内容仍不满足通过条件：" + "；".join(issues), 409)
        if not source_is_current(project_id, doc):
            raise ReportWorkflowError("报告送审后上游工艺数据已变化，请驳回并重新汇总后送审", 409)
    doc.status = "approved" if decision == "approve" else "rejected"
    doc.reviewed_by = user.get("username", "system")
    doc.reviewed_at = now_str()
    doc.review_note = comment
    if review_items:
        doc.review_items = review_items
    if review_conclusion:
        doc.review_conclusion = review_conclusion
    if distribution_scope:
        doc.distribution_scope = distribution_scope
    if distribution_cc:
        doc.distribution_cc = distribution_cc
    doc.history.append(workflow_event(f"report_review_{decision}", user, comment))
    doc.updated_at = now_str()
    out = doc.model_dump()
    return {"report": out,
            "audit": {"action": f"workflow:report_{decision}", "payload": {"comment": comment}}}


def review_summary(project_id: str) -> dict:
    """3.2 审核摘要：确定性汇总，不调模型、不编造结论。"""
    _ensure_project(project_id)
    saved = _load_report(project_id)
    if not saved:
        raise ReportWorkflowError("评估报告不存在", 404)
    doc = ProcessReport(**saved)
    issues = prerequisite_issues(project_id) + content_issues(doc)
    materials = {
        "report_no": doc.report_no,
        "title": doc.title,
        "status": doc.status,
        "version": doc.version,
        "prepared_by": doc.prepared_by,
        "prepared_at": doc.prepared_at,
        "attachment_count": len(doc.attachments or []),
        "evaluation_items": len(doc.evaluation_items or []),
        "stage_results": len(doc.stage_results or []),
    }
    return {
        "report": saved,
        "review_materials": materials,
        "review_summary": {
            "summary": doc.conclusion or doc.overview or "",
            "items": [{"item": item.item, "status": item.status, "conclusion": item.conclusion}
                      for item in doc.evaluation_items],
            "generated_note": doc.review_note or "",
            "decision_options": ["approve", "reject"],
            "gaps": issues,
        },
        "source_current": source_is_current(project_id, doc),
    }


# --------------------------------------------------------------------------- #
# 3.3 发布并回传报价
# --------------------------------------------------------------------------- #
def publish(project_id: str, user: dict, *, recipients, comment: str = "") -> dict:
    """正式发布（POST /process-report/publish 同一实现）。"""
    _ensure_project(project_id)
    user = user or {}
    doc = _require_report(project_id)
    if doc.status != "approved":
        raise ReportWorkflowError("报告须审核通过后才能发布", 409)
    issues = prerequisite_issues(project_id) + content_issues(doc)
    if issues:
        raise ReportWorkflowError("报告当前不满足发布条件：" + "；".join(issues), 409)
    if not source_is_current(project_id, doc):
        raise ReportWorkflowError("报告审核通过后上游工艺数据已变化，请重新走报告审核流程", 409)
    rows = [row if isinstance(row, ReportRecipient) else ReportRecipient(**row)
            for row in (recipients or [])]
    if not rows:
        raise ReportWorkflowError("请至少设置一个正式发布对象", 409)
    doc.status = "published"
    doc.published_by = user.get("username", "system")
    doc.published_at = now_str()
    doc.recipients = rows
    doc.history.append(workflow_event("report_published", user, comment))
    doc.updated_at = now_str()
    out = doc.model_dump()
    return {"report": out,
            "audit": {"action": AUDIT_PUBLISHED,
                      "payload": {"comment": comment,
                                  "recipients": [r.model_dump() for r in rows]}}}


def new_version(project_id: str, user: Optional[dict] = None) -> dict:
    """从已发布报告创建下一版草稿，保留既有内容和完整审计链。"""
    _ensure_project(project_id)
    user = user or {}
    saved = _load_report(project_id)
    if not saved:
        raise ReportWorkflowError("评估报告不存在", 404)
    prior = ProcessReport(**saved)
    if prior.status != "published":
        raise ReportWorkflowError("仅已发布报告可创建新版本", 409)
    # 兼容升级前已发布的数据：创建新草稿前先确保旧版进入不可变版本库（由 commit 落盘）。
    archive = prior.model_dump()
    doc = prior.model_copy(deep=True)
    doc.version = prior.version + 1
    doc.status = "draft"
    doc.reviewed_by = None
    doc.reviewed_at = None
    doc.review_note = ""
    doc.review_items = []
    doc.review_conclusion = ""
    doc.published_by = None
    doc.published_at = None
    doc.recipients = []
    doc.history.append(workflow_event("report_new_version", user,
                                      f"基于 V{prior.version} 创建 V{doc.version} 草稿"))
    doc.updated_at = now_str()
    out = doc.model_dump()
    return {"report": out, "archive": archive,
            "audit": {"action": AUDIT_NEW_VERSION,
                      "payload": {"from_version": prior.version, "version": doc.version}}}


def distribution_recipients(project_id: str) -> dict:
    """3.3 发布对象：沿用报告已保存的发布范围，不另造一份数据。"""
    _ensure_project(project_id)
    saved = _load_report(project_id)
    if not saved:
        raise ReportWorkflowError("评估报告不存在", 404)
    doc = ProcessReport(**saved)
    return {"report_no": doc.report_no, "status": doc.status,
            "distribution_scope": doc.distribution_scope,
            "distribution_cc": doc.distribution_cc,
            "recipients": [row.model_dump() for row in (doc.recipients or [])]}


def publish_state(project_id: str) -> dict:
    """3.3 发布状态：报告状态 + 发布前置缺口，供 Agent 判断能不能发布。"""
    _ensure_project(project_id)
    saved = _load_report(project_id)
    if not saved:
        raise ReportWorkflowError("评估报告不存在", 404)
    doc = ProcessReport(**saved)
    issues = prerequisite_issues(project_id) + content_issues(doc)
    return {
        "report": saved,
        "status": doc.status,
        "approved": doc.status == "approved",
        "published": doc.status == "published",
        "can_publish": doc.status == "approved" and not issues,
        "gaps": issues,
        "source_current": source_is_current(project_id, doc),
        "recipient_count": len(doc.recipients or []),
    }


# --------------------------------------------------------------------------- #
# 批次 10C：发布收口（closure）
#
# 发布完必须一眼看到：报告已发布 / 分发已留痕 / 是否已回传报价 / 回传到哪张报价第几步 /
# 没回传就点这里重试；主操作随项目来源变化。只读、纯派生，不写库、不新建实例号。
# --------------------------------------------------------------------------- #
_CLOSURE_STATES = ("draft", "awaiting_review", "approved", "published",
                   "handoff_pending", "handed_off", "handoff_failed", "revised")
# 未发布时按报告状态给「下一步」：草稿 → 5.1 汇总结果；送审 → 5.2 结果审核；
# 已通过 → 5.3 发布并回传报价。
_NEXT_STEP_BY_STATUS = {
    "draft": ("summary", "5.1", "汇总结果", "summary.html"),
    "in_review": ("report-review", "5.2", "结果审核", "report-review.html"),
    "approved": ("report-publish", "5.3", "发布并回传报价", "report-publish.html"),
}
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{2,64}$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _role_id_of_label(label: str) -> str:
    """中文角色名 → 角色码（auth.ROLES / cpq_sso.TECH_ROLE_LABEL 同源）；认不出给空串。"""
    name = _text(label)
    if not name:
        return ""
    for code, text in getattr(auth, "ROLE_LABEL", {}).items():
        if text == name:
            return code
    try:
        from . import cpq_sso
        for code, text in getattr(cpq_sso, "TECH_ROLE_LABEL", {}).items():
            if text == name:
                return code
    except Exception:  # noqa: BLE001
        pass
    return name if name in getattr(auth, "ROLES", ()) else ""


def _split_recipients(recipients) -> tuple:
    """系统内收件人（账号 / 角色，会产生站内消息）与外部分发对象（自由文本，只留痕）。

    分类口径（Spec §7.3 规则 1，不新增数据字段）：channel 为空或「平台通知」且 name
    能解析到系统内对象的一律算系统内；其余全部算外部。两个集合不相交。
    """
    internal: list = []
    external: list = []
    for row in recipients or []:
        data = row if isinstance(row, dict) else (row.model_dump() if hasattr(row, "model_dump") else {})
        if not isinstance(data, dict):
            continue
        name = _text(data.get("name"))
        contact = _text(data.get("contact"))
        channel = _text(data.get("channel"))
        if not name:
            continue
        if channel not in ("", "平台通知") or "@" in name:
            external.append(f"{name} <{contact}>" if contact else name)
            continue
        role_id = _role_id_of_label(name)
        if role_id:
            internal.append({"kind": "role", "id": role_id, "label": name})
            continue
        try:
            user = store.get_user(name) or {}
        except Exception:  # noqa: BLE001
            user = {}
        if user or _USERNAME_RE.match(name):
            internal.append({"kind": "user", "id": name,
                             "label": _text(user.get("display_name")) or name})
            continue
        external.append(f"{name} <{contact}>" if contact else name)
    return internal, external


def _stage_primary_action(project_id: str, key: str, action_id: str) -> dict:
    step = _NEXT_STEP_BY_STATUS.get(key) or _NEXT_STEP_BY_STATUS["draft"]
    return {"id": action_id, "label": f"去 {step[1]} {step[2]}",
            "target": f"{step[3]}?project={project_id}&stage={step[0]}"}


def publish_closure(project_id: str, report: dict, handoff: dict, raw_plan: dict) -> dict:
    """发布收口块（closure）：状态 / 发布留痕 / 分发分类 / 回传结果 / 主操作 / 异常。"""
    report = report if isinstance(report, dict) else {}
    handoff = handoff if isinstance(handoff, dict) else {}
    raw_plan = raw_plan if isinstance(raw_plan, dict) else {}
    status = _text(report.get("status"))
    published_at = _text(report.get("published_at"))
    published = status == "published" or bool(published_at)
    internal_recipients, external_targets = _split_recipients(report.get("recipients"))
    scope = _text(report.get("distribution_scope"))
    cc = _text(report.get("distribution_cc"))
    recorded = bool(internal_recipients or external_targets or scope or cc)
    session_id = _text(handoff.get("session_id"))
    error = ""
    for key in ("quote_handoff_error", "handoff_error"):
        error = _text(raw_plan.get(key))
        if error:
            break
    # 最近一次回传留了失败原因 -> 这次交接没有成功；否则才按回传留痕判定成功。
    sent = bool(_text(handoff.get("sent_at")) or session_id) and not error
    try:
        link = store.load_business_case(project_id) or {}
    except Exception:  # noqa: BLE001
        link = {}
    from_quote = bool(_text(link.get("quote_session_id")))

    if not published:
        state = {"draft": "draft", "in_review": "awaiting_review", "approved": "approved",
                 "rejected": "awaiting_review"}.get(status, "draft")
    elif sent:
        state = "handed_off"
    elif error:
        state = "handoff_failed"
    else:
        state = "published"

    if not published:
        key = "approved" if status == "approved" else ("in_review" if status == "in_review" else "draft")
        primary_action = _stage_primary_action(project_id, key, "open-stage")
    elif sent and from_quote:
        primary_action = {"id": "back-to-quote", "label": "返回原报价继续",
                          "target": f"/?assistant=quote&session={session_id}"}
    else:
        primary_action = {"id": "view-report", "label": "查看已发布报告",
                          "target": f"report-publish.html?project={project_id}"}

    codes = ["handoff_failed"] if published and not sent and error else []
    return {
        "state": state,
        "published": published,
        "published_at": published_at,
        "published_by": _text(report.get("published_by")),
        "version": int(report.get("version") or 1),
        "distributed": {
            "recorded": recorded,
            "scope": scope,
            "cc": cc,
            "internal_recipients": internal_recipients,
            "external_targets": external_targets,
        },
        "handoff": {
            "sent": sent,
            "handoff_id": _text(report.get("handoff_id")) or _text(handoff.get("handoff_id")),
            "target_quote": {
                "session_id": session_id,
                "card_id": _text(handoff.get("card_id")),
                "current_step": int(handoff.get("next_step_no") or 0),
                "current_step_label": _text(handoff.get("next_step_name")),
            },
            "error": error,
            "retry_action": ({"id": "retry-handoff", "label": "重新回传报价"}
                             if published and not sent and error else None),
        },
        "primary_action": primary_action,
        "anomaly": {"has_anomaly": bool(codes), "codes": codes},
    }


def publish_result(project_id: str) -> dict:
    """3.3 发布结果：报告 + 版本链 + 整机回传结果（如已回传）+ 发布收口 closure。

    既有键 report / versions / quote_handoff 一个不动，只**追加** closure（批次 10C）。
    """
    _ensure_project(project_id)
    saved = _load_report(project_id)
    versions = store.list_process_report_versions(project_id)
    handoff: dict[str, Any] = {}
    raw_plan: dict[str, Any] = {}
    try:
        from . import integration
        raw_plan = store.load_integration(project_id) or {}
        plan = integration.load_plan(project_id)
        handoff = (plan.quote_handoff.model_dump() if plan and plan.quote_handoff else {}) or {}
    except Exception:  # 回传只影响展示，缺整机计划不影响发布结果读取
        handoff = {}
    closure = publish_closure(project_id, saved or {}, handoff, raw_plan)
    return {"report": saved, "versions": versions, "quote_handoff": handoff,
            "closure": closure}


def commit(project_id: str, result: dict, user: Optional[dict] = None) -> dict:
    """把共享实现算好的报告落盘并记审计（Agent 路径入口）。

    业务判断（门禁、状态流转、审计动作名）已在各流程函数里完成，本函数只做落盘与留痕；
    路由侧用等价的 ``main._persist_report`` + ``store.audit`` 直写同一份结果。两条入口
    写的是同一份 doc，不存在第二套业务实现。
    """
    user = user or {}
    archive = (result or {}).get("archive")
    if archive:
        store.save_process_report(project_id, archive, author=user.get("username", "system"))
    report = (result or {}).get("report") or {}
    if report:
        store.save_process_report(project_id, report, author=user.get("username", "system"))
    audit = (result or {}).get("audit") or {}
    if audit.get("action"):
        store.audit(project_id, audit["action"], audit.get("payload") or {})
    return report


# --------------------------------------------------------------------------- #
# 3.3 → 报价：已发布报告回传销售经理继续报价
# --------------------------------------------------------------------------- #
def _bridge_call(action, *args, **kwargs):
    """把桥接层的两类失败翻成报告流程的业务错误（与 2.3 同一口径）。"""
    from . import cpq_bridge

    try:
        return action(*args, **kwargs)
    except cpq_bridge.BridgeRejected as exc:
        raise ReportWorkflowError(str(exc), 400) from exc
    except cpq_bridge.BridgeUnavailable as exc:
        raise ReportWorkflowError(f"业务数据库/报价服务暂不可用：{exc}", 503) from exc


def report_package(doc: ProcessReport) -> dict:
    """已发布报告 → 报价侧要看的完整报告包。

    销售拿到的必须是**权威结论**：编号、版本、审核与发布留痕、发布范围、结论、风险、
    附件与导出入口全部取自服务端报告本身，不让前端自行拼。
    """
    return {
        "report_no": doc.report_no,
        "version": doc.version,
        "title": doc.title,
        "status": doc.status,
        "reviewed_by": doc.reviewed_by or "",
        "reviewed_at": doc.reviewed_at or "",
        "review_note": doc.review_note or "",
        "published_by": doc.published_by or "",
        "published_at": doc.published_at or "",
        "distribution_scope": doc.distribution_scope or "",
        "distribution_cc": doc.distribution_cc or "",
        "summary": doc.overview or "",
        "conclusion": doc.conclusion or "",
        "risks": list(doc.risks or []),
        "highlights": list(doc.highlights or []),
        "attachments": [row.model_dump() if hasattr(row, "model_dump") else dict(row or {})
                        for row in (doc.attachments or [])],
        # 报告页与 PDF 导出入口：让销售点得开原始报告，而不是只看一段摘要。
        "report_url": f"tech-workbench.html?stage=summary&project={doc.project_id}",
        "pdf_url": f"/api/projects/{doc.project_id}/process-report/export.pdf",
        "recipients": [row.model_dump() if hasattr(row, "model_dump") else dict(row or {})
                       for row in (doc.recipients or [])],
    }


def _technical_result(project_id: str, title: str) -> dict:
    """回传用的技术结果：参数、工艺、零件与组装成本。复用 2.3 的同一份口径。"""
    from . import cost_flow, integration

    try:
        plan = integration.load_plan(project_id)
    except Exception:
        return {"tech_project_id": project_id}
    if plan is None or not (plan.cost and plan.cost.items):
        return {"tech_project_id": project_id}
    requirement = store.load_requirement(project_id) or {}
    return cost_flow.integration_quote_result(project_id, plan, title, requirement)


def send_to_quote(project_id: str, user: dict, *, note: str = "", token: str = "",
                  target_type: str = "", target_role_code: str = "",
                  target_user_id: str = "", source_task_id: str = "") -> dict:
    """3.3 → 销售经理：把**已发布报告**回传报价，让销售接着往下走。

    这是报告语义明确的专用入口，不再复用 2.2/2.3 的 `/integration/send-to-quote`：
    回传必须带齐报告编号、版本、审核与发布信息、发布范围、结论、风险与附件入口。

    报价步骤只允许单调前进：如果成本阶段已经把报价推进到第 3 步或更后（例如报告
    在本步之前就回传过），这里只合并技术结果与报告快照并通知当前销售负责人，
    绝不重做第 2 步、也不把 current_step 写回去。

    幂等：同一项目、同一报告版本、同一目标报价会话只产生一个有效交接结果，
    重复点击返回 already_sent，不重复建 open 任务、不重复发消息。
    """
    from . import cost_flow, integration

    _ensure_project(project_id)
    user = user or {}
    doc = _require_report(project_id)
    if doc.status != "published":
        raise ReportWorkflowError("报告须正式发布后才能回传销售经理继续报价", 409)
    requirement = store.load_requirement(project_id) or {}
    req_data = requirement.get("data") or {}
    title = (doc.basic_info or {}).get("product_name") or doc.title or f"技术工艺项目 {project_id}"
    result = _technical_result(project_id, title)
    package = report_package(doc)
    # 结果版本按**报告版本**走：同一版报告重复点击只交一次，换版后才是新的交接。
    result_version = f"report-v{doc.version}"

    outcome = _bridge_call(
        cpq_bridge.report_handoff, token, project_id, title,
        cost_flow.requirement_customer(requirement, req_data),
        str(requirement.get("product_name") or req_data.get("product_name") or ""),
        note or f"已发布报告 {doc.report_no} V{doc.version}，请继续报价",
        # 来源任务号：路由直接传进来的优先，其次才是需求单里记着的那一条；服务端会在
        # 那一次回传命令的同一个事务里把它关掉（不再另发关闭请求）。
        str(source_task_id or req_data.get("source_task_id") or ""),
        str(req_data.get("source_session_id") or ""),
        result, package, result_version,
        str(doc.report_no or ""),
        str(target_type or ""), str(target_role_code or ""), str(target_user_id or ""),
        # 报告回传也带项目实例号：服务端凭它认回原报价卡片，而不是靠散落线索猜。
        business_case_id=cost_flow.business_case_of(project_id))

    # 技术侧留痕：报告回传后整机计划里的 quote_handoff 指向同一个报价会话，
    # 历史页面与 3.3 的"回传结果"都从这一份数据读，不另存。
    handoff = outcome.get("handoff") or {}
    try:
        plan = integration.load_plan(project_id)
    except Exception:
        plan = None
    if plan is not None:
        plan.quote_handoff = QuoteHandoff(
            session_id=str(outcome.get("quote_session_id") or ""),
            next_step_no=outcome.get("next_step_no"),
            next_step_name=outcome.get("next_step_name") or "",
            target_role_name=str(handoff.get("target_role_name") or ""),
            target_name=str(handoff.get("target_name") or ""),
            returned_to_sender=bool(handoff.get("returned_to_sender")),
            source_task_no=str(handoff.get("source_task_no") or ""),
            returned_sections=list(outcome.get("returned_sections") or []),
            task_id=str(handoff.get("task_id") or "") or None,
            sent_at=now_str(),
            sent_by=user.get("display_name") or user.get("username") or "",
        )
        try:
            integration.save_plan(project_id, plan, user.get("username", "system"))
        except Exception:
            pass

    return {
        "report_no": doc.report_no,
        "version": doc.version,
        "status": doc.status,
        "handoff": handoff,
        "quote_session_id": outcome.get("quote_session_id") or "",
        "linked_by": outcome.get("linked_by") or "",
        "new_card": bool(outcome.get("new_card")),
        "already_sent": bool(outcome.get("already_sent")),
        "already_completed": bool(outcome.get("already_completed")),
        # 这一批的核心新增：这次回传的唯一标识，结果区按它就能查到哪一次交接
        "handoff_id": str(outcome.get("handoff_id") or ""),
        "source_task": outcome.get("source_task") or {},
        "next_step_no": outcome.get("next_step_no"),
        "next_step_name": outcome.get("next_step_name") or "",
        "carried": {
            "report": sorted(package.keys()),
            "tech_result": sorted(result.keys()) if isinstance(result, dict) else [],
        },
        "audit": {"action": AUDIT_SENT_TO_QUOTE,
                  "payload": {"report_no": doc.report_no, "version": doc.version,
                              "handoff_id": str(outcome.get("handoff_id") or ""),
                              "task_id": handoff.get("task_id"),
                              "already_sent": bool(outcome.get("already_sent"))}},
    }

"""
FastAPI 应用: 图纸解析与生成平台后端。

流程: 上传原图 -> Claude 解析为 IR -> Claude 拆解推荐增强 -> CAD 内核生成几何
       -> 前端展示(原图/拆解树/3D 查看器/校验告警/推荐)。

所有阶段结果都落盘(见 storage.store)，形成可追溯证据链。
"""
from __future__ import annotations

import copy
import json
import hashlib
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import (
    Body, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .config import (
    AUTH_ENABLED, AUTH_AUTO_ADMIN, CORS_ALLOW_ORIGINS, LLM_PROVIDER, LOGIN_MAX_ATTEMPTS,
    CPQ_SSO_ENABLED, CPQ_MANAGER_FULL_TECH,
    TECH_SUBSTEPS,
    LOGIN_WINDOW_SECONDS, MAX_UPLOAD_BYTES, ROOT_DIR,
    QWEN_MODEL, QWEN_TEXT_MODEL, DEFAULT_ADMIN_USER, DEFAULT_ADMIN_PASSWORD,
    AUTH_SECRET, TASK_RECOVER_ON_START,
    active_model, active_text_model,
)
from .models.assembly import AssemblyPlan
from .models.approval import QuoteApproval
from .models.ai import VerificationPatch, VerificationPatchDecision
from .models.cleaning import CleaningPlan
from .models.cost import CostAnalysis
from .models.cost_review import (CostAction, CostActionBody, CostConfirmBody,
                                 CostReviewBody)
from .models.costest import CostEstimate
from .models.integration import (
    FinanceHandoff, IntegrationDrawing, IntegrationParamPlan, MaterialWrite, QuoteHandoff,
)
from .models.negotiation import NegotiationPlan
from .models.pricenego import PriceNegotiation
from .models.pricing import PricingPlan
from .models.ir import DesignIR, Material
from .models.manufacturing import ManufacturingPlan
from .models.material import MaterialPlan, Supplier
from .models.model_lookup import ModelLookupResult
from .models.process import ProcessPlan
from .models.production import EquipmentResource, ProductionPlan
from .models.summary import SummaryDoc
from .models.techprocess import TechProcessRecord
from .models.workflow import (
    ProcessReport, PublishAction, ReportDistributionSettings, RequirementDoc, RequirementDocumentExtraction,
    WorkflowAction, WorkflowReview,
)
from .services import (
    acting_user, cpq_auth_client, user_llm,
    approval as approval_svc, assembly, auth, bom, cleaning, cost, costest, decompose,
    component_match, cost_lookup, cost_model, cpq_bridge, cpq_sso, drawing2d, geometry,
    cost_flow, cost_review, entry_origin, file_preflight, industry_templates, integration,
    manufacturing, report_workflow,
    llm_settings, material, negotiation, oc_agent, part_edit, part_versions, pricenego, pricing,
    process_lookup,
    packaging_match,
    packaging_bom,
    packaging_cost,
    packaging_drawing_flow,
    packaging_handoff,
    packaging_route,
    process, product_params, production, requirement_extract, requirement_service,
    project_access,
    cad_converter,
    dwg_dispatch,
    step_import,
    summary as summary_svc, tasks, timeline, tree,
    versioning, vision, qwen_client, llm_client, model_lookup, requirement_pdf,
    workflow_projection,
)
from .storage import store
from .time_utils import now_cst_str


class ReviewAction(BaseModel):
    comment: str = ""


class BoxMatchRunAction(BaseModel):
    """盒型匹配入参：需求单号留空时按项目当前需求单取。"""
    requirement_no: str = ""


class BoxMatchDecideAction(BaseModel):
    """盒型四态决策入参：confirmed / returned / new_tooling（Spec §3.2）。"""
    decision: str = "confirmed"
    box_type_code: Optional[str] = None
    note: str = ""
    requirement_no: str = ""


class PackagingBomBuildAction(BaseModel):
    """包装 BOM 展开入参：人工变量覆盖只从请求体来，服务端不替用户填默认值。"""
    requirement_no: str = ""
    overrides: dict = {}


class PackagingBomLockAction(BaseModel):
    """包装 BOM 单行锁定/解锁入参（Spec §4）。"""
    requirement_no: str = ""
    item_key: str = ""
    locked: bool = True


class PackagingRouteBuildAction(BaseModel):
    """包装工艺路线生成/重算入参：需求单号留空时按项目当前需求单取。"""
    requirement_no: str = ""


class PackagingRouteConfirmAction(BaseModel):
    """包装工艺路线确认入参（Spec §4）：确认并冻结一条版本快照。"""
    requirement_no: str = ""


class PackagingCostBuildAction(BaseModel):
    """包装成本生成/重算入参（Spec §4）：需求单号留空时按项目当前需求单取。"""
    requirement_no: str = ""
    scenario: dict = {}


class PackagingQuoteSendAction(BaseModel):
    """包装成本回传报价入参（包装第 8 批，Spec §4.5）。"""
    requirement_no: str = ""
    scenario: str = ""
    allow_gaps: bool = False
    reason: str = ""


class ReportQuoteAction(BaseModel):
    """3.3 已发布报告回传销售经理的入参。"""
    note: str = ""
    # 派发方式与其它转交一致：留空时由报价那边落到该任务的默认角色（销售经理）。
    target_type: str = ""
    target_role_code: str = ""
    target_user_id: str = ""
    source_task_id: str = Field("", description="来源待办任务号，随 URL 带进来")
    # 落点冲突的恢复三件套：服务端已经判出「没有找到报价卡片」，界面必须能把
    # 「认回哪张实例」或「明确新建 + 原因」送回来 —— 否则那句「请填写新建原因后重试」
    # 在界面上根本无处执行（现场 P0）。
    business_case_id: str = Field("", description="业务实例号；留空时走项目 meta / 上一次回传")
    create_new: bool = Field(False, description="明确要求新建报价卡片（与 create_reason 成对）")
    create_reason: str = Field("", description="新建报价卡片的原因，服务端要求非空")


class QuoteLinkRecoveryBody(BaseModel):
    """历史项目一次性恢复「报价关联」的入参（Spec §6）。

    只写技术侧事实：把线索记进项目 meta 并留痕；**不建、不改任何报价卡片** ——
    卡片的新建仍然只能由回传命令带 create_new + create_reason 完成。
    """
    business_case_id: str = ""
    quote_session_id: str = ""
    source_task_id: str = ""
    source_session_id: str = ""
    create_new: bool = False
    create_reason: str = ""
    note: str = ""


class LoginBody(BaseModel):
    username: str
    password: str


class NewUser(BaseModel):
    username: str
    password: str
    role: str = "engineer"
    display_name: str = ""


class RegisterBody(BaseModel):
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=60)
    requested_role: str = "viewer"


class UpdateMyProfile(BaseModel):
    display_name: str = Field(default="", max_length=60)
    current_password: str = Field(default="", max_length=128)
    new_password: str = Field(default="", max_length=128)


class UpdateUserRole(BaseModel):
    role: str


class LlmSettingsBody(BaseModel):
    """全局模型配置。首页与 2.1 Agent 小窗提交的是同一个结构。

    只有五项可改：模型、温度、最大 token、是否思考、API Key。
    未传的字段保持不变，因此全部可选；API Key 只允许写入，绝不回显。
    """

    # 唯一模型：图纸解析、文档分析、工艺推荐、成本测算和 Agent 对话都用它。
    model: Optional[str] = Field(default=None, max_length=120)
    # 兼容字段：升级前的客户端还会发这两个名字。它们不再代表"两套模型"，
    # 服务端只把它们当作同一个 model 的老写法（见 update_runtime_llm_settings）。
    vision_model: Optional[str] = Field(default=None, max_length=120)
    text_model: Optional[str] = Field(default=None, max_length=120)
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    thinking: Optional[bool] = None
    api_key: Optional[str] = Field(default=None, max_length=512)
    # 密钥按提供商分别保存，所以写 Key 时必须一并说明是哪一家的。
    api_key_provider: Optional[str] = Field(default=None, max_length=40)


class ProjectManageBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class CustomerCreditUpdate(BaseModel):
    """客户信用等级为销售主数据，只接受固定四档。"""
    customer_credit: str = Field(pattern="^[ABCD]$")


class RequirementAiCheckItem(BaseModel):
    """Qwen 对确认表单中单项的结论；服务端会再规整为固定检查清单。"""
    item: str = ""
    status: str = "need_info"
    detail: str = ""


class RequirementAiCheckResult(BaseModel):
    """手动 AI 检查的最小输出契约，刻意保持简短以控制 token 成本。"""
    summary: str = ""
    items: List[RequirementAiCheckItem] = Field(default_factory=list)


class WorkbenchChatTurn(BaseModel):
    role: str = "user"
    content: str = Field(default="", max_length=1600)


class WorkbenchChatRequest(BaseModel):
    """2.1 工作台的文字对话请求；项目图纸本身不会被重新发送给视觉模型。"""
    message: str = Field(min_length=1, max_length=1600)
    part_id: str = Field(default="", max_length=120)
    history: List[WorkbenchChatTurn] = Field(default_factory=list, max_length=6)
    page_context: str = Field(default="", max_length=160)


class ProjectChatRequest(BaseModel):
    """项目级对话：跨需求、解析、报告页面共用同一段留痕。"""
    message: str = Field(min_length=1, max_length=1600)
    page_context: str = Field(default="", max_length=160)


class WorkbenchFeatureEdit(BaseModel):
    feature_index: int = Field(ge=0, le=100)
    field: str = Field(min_length=1, max_length=40)
    value: float


class WorkbenchPartEdit(BaseModel):
    """AI 对话可提出的受控零件修改；服务端仍按特征类型二次校验。"""
    should_apply: bool = False
    name: Optional[str] = Field(default=None, max_length=160)
    quantity: Optional[int] = Field(default=None, ge=1, le=100000)
    material_spec: Optional[str] = Field(default=None, max_length=160)
    feature_updates: List[WorkbenchFeatureEdit] = Field(default_factory=list, max_length=30)
    # 空特征零件补基体（或把非法基体换成合法基体）：只认 plate / box / cylinder 三种
    # 模板与它们的固定尺寸，具体校验在 services.part_edit，两条会话入口共用同一份。
    base_feature: Optional[dict] = None
    explanation: str = Field(default="", max_length=1000)

    @field_validator("feature_updates", mode="before")
    @classmethod
    def normalize_text_feature_updates(cls, value):
        # 模型偶尔用一句话概括修改，不允许把它猜测成数值更新；保留说明而不执行。
        return value if isinstance(value, list) else []


class WorkbenchChatAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)
    edit: Optional[WorkbenchPartEdit] = None

    @field_validator("edit", mode="before")
    @classmethod
    def normalize_text_edit(cls, value):
        if isinstance(value, str):
            return {"should_apply": False, "explanation": value[:1000]}
        return value


class ProjectChatAnswer(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)


class ModelLookupConfirmation(BaseModel):
    candidate_model: str = Field(min_length=1, max_length=80)
    decision: str = Field(pattern="^(confirmed|rejected)$")
    note: str = Field(default="", max_length=600)


_CONFIRMATION_FIELDS = ("confirmed", "confirmed_by", "confirmed_at")


def _digest_value(value) -> str:
    """为任务输入和业务快照生成稳定摘要，避免把原文/附件写进任务记录。"""
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


def _task_key(kind: str, *inputs) -> str:
    return f"{kind}:{_digest_value(inputs)}"


def _ir_snapshot(project_id: str) -> str:
    return _digest_value(store.load_ir(project_id) or {})


def _assert_ir_unchanged(project_id: str, expected: str) -> None:
    if _ir_snapshot(project_id) != expected:
        raise RuntimeError("任务执行期间设计 IR 已更新，本次旧输入结果未保存；请基于最新版本重新发起。")


def _input_revision(project_id: str) -> int:
    return int((store.load_meta(project_id) or {}).get("input_revision") or 1)


def _assert_input_unchanged(project_id: str, expected: int) -> None:
    if _input_revision(project_id) != expected:
        raise RuntimeError("任务执行期间原图或附件已更新，本次旧输入结果未保存；请重新发起解析。")


def _assert_dependencies_unchanged(expected: str, current, label: str) -> None:
    if _digest_value(current) != expected:
        raise RuntimeError(f"任务执行期间{label}已更新，本次旧输入结果未保存；请基于最新内容重新发起。")


def _confirmation_payload(value) -> dict:
    data = value.model_dump() if isinstance(value, BaseModel) else dict(value or {})
    ignored = {*_CONFIRMATION_FIELDS, "updated_at", "timing"}

    def strip_control_fields(item):
        if isinstance(item, dict):
            return {
                key: strip_control_fields(child)
                for key, child in item.items() if key not in ignored
            }
        if isinstance(item, list):
            return [strip_control_fields(child) for child in item]
        return item

    return strip_control_fields(data)


def _business_changed(current, incoming) -> bool:
    return current is None or _confirmation_payload(current) != _confirmation_payload(incoming)


def _sync_confirmation(current, incoming) -> bool:
    """确认字段只由服务端维护；业务内容变化时自动撤销旧确认。"""
    unchanged = current is not None and _confirmation_payload(current) == _confirmation_payload(incoming)
    for field in _CONFIRMATION_FIELDS:
        setattr(incoming, field, getattr(current, field, None) if unchanged else (False if field == "confirmed" else None))
    return unchanged


def _sync_pricing_approval(current: Optional[PricingPlan], incoming: PricingPlan) -> bool:
    """定价内容变化后，旧的销售提交/财务审批结论自动失效。"""
    def business_payload(value: PricingPlan) -> dict:
        data = value.model_dump()
        for key in ("approval", "updated_at", "timing", "base_price", "factor_multiplier", "suggested_price"):
            data.pop(key, None)
        costs = data.get("costs") or {}
        for key in ("management_cost", "base_cost"):
            costs.pop(key, None)
        return data

    unchanged = current is not None and business_payload(current) == business_payload(incoming)
    incoming.approval = (
        current.approval.model_copy(deep=True) if unchanged
        else type(incoming.approval)()
    )
    return unchanged


# --------------------------------------------------------------------------- #
# 鉴权: app 级依赖在 /api 层校验令牌(放行 health/login 与静态前端);
# 关闭鉴权时注入隐式 system/admin,保持旧行为。
# --------------------------------------------------------------------------- #
_PUBLIC_PATHS = {"/api/health", "/api/login", "/api/register"}
_PROJECT_ID_PATTERN = re.compile(r"^[0-9a-f]{12}$")
_login_lock = threading.Lock()
_login_attempts: dict[str, list[float]] = {}


def _valid_project_path(path: str) -> bool:
    """只允许系统生成的 12 位十六进制项目 ID，阻断本地文件后端路径穿越。"""
    prefix = "/api/projects/"
    if not path.startswith(prefix):
        return True
    first_segment = path[len(prefix):].split("/", 1)[0]
    if not first_segment:  # 保留 FastAPI 对 /api/projects/ 的标准重定向行为
        return True
    # /api/projects/3d 是创建 3D 项目的固定路由，不是项目 ID。
    return first_segment == "3d" or bool(_PROJECT_ID_PATTERN.fullmatch(first_segment))


def _sso_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    # <img>/STL/PDF 这类标签发不出请求头，沿用既有的 ?token= 透传约定。
    return request.query_params.get("token", "")


def _bind_acting_user(user: dict) -> dict:
    """把「这一轮是谁」设入上下文，与 request.state.user 同一处落地。

    模型/密钥是「全局默认 + 每个账号可覆盖」，服务层得知道发起账号才能选。
    这里设的是**请求上下文**，之后同一个请求里调用的任务提交、解析、Agent 都读得到；
    真正给用户算「我的 / 生效」的接口仍显式用 current_user，不依赖上下文传递。
    """
    try:
        acting_user.set_acting_user((user or {}).get("username", ""))
    except Exception:                                     # pragma: no cover - 上下文异常不影响鉴权
        pass
    return user


async def _cpq_sso_guard(request: Request) -> None:
    """【CPQ 定制】身份来自 CPQ 的登录系统。见 services/cpq_sso.py。"""
    path = request.url.path
    if path in _PUBLIC_PATHS or not path.startswith("/api/"):
        return
    try:
        user = cpq_sso.resolve(_sso_token(request))
    except cpq_sso.SsoUnavailable as exc:
        # 登录服务挂了不等于"你没登录"。回 401 会把在线的人静默踢出去，
        # 看起来像自己的会话过期了；503 才说得清是谁坏了。
        raise HTTPException(503, f"登录服务暂不可用，无法校验身份：{exc}") from exc
    if not user:
        raise HTTPException(401, "请先在配置报价 CPQ 中登录")
    # 账号级模型与密钥也只有 PG 一处：这里把**用户自己的票**交给 cpq_auth_client，
    # 「我的模型与密钥」这类本人接口才能以本人身份回调 CPQ（写操作走内部通道）。
    cpq_auth_client.set_request_token(_sso_token(request))
    request.state.user = _bind_acting_user(user)


async def auth_guard(request: Request):
    if CPQ_SSO_ENABLED:
        await _cpq_sso_guard(request)
        return
    if AUTH_AUTO_ADMIN:
        # 演示/内网临时模式：不展示登录页，但仍以管理员身份通过所有业务权限检查。
        request.state.user = _bind_acting_user({
            "username": DEFAULT_ADMIN_USER,
            "role": "admin",
            "display_name": "默认管理员",
            "is_system": False,
        })
        return
    if not AUTH_ENABLED:
        request.state.user = _bind_acting_user(auth.SYSTEM_USER)
        return
    path = request.url.path
    if path in _PUBLIC_PATHS or not path.startswith("/api/"):
        return
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token:  # <img>/STL 等无法带请求头,允许 ?token= 透传
        token = request.query_params.get("token", "")
    payload = auth.parse_token(token)
    if not payload:
        raise HTTPException(401, "未登录或令牌已失效")
    u = store.get_user(payload.get("sub", ""))
    if not u:
        raise HTTPException(401, "用户不存在")
    request.state.user = _bind_acting_user(auth.public_user(u))


def current_user(request: Request) -> dict:
    return getattr(request.state, "user", auth.SYSTEM_USER)


async def project_write_guard(request: Request):
    """项目级唯一 ACL：`/api/projects/{project_id}/**` 的读与写开工前先过 project_access。

    批次 7 把原来只覆盖「工程师写本人项目」的这一条扩成唯一入口：读 → require_project_access(pid,
    user, "read")，专属业务动作 → "contribute"（只判可见 + 未归档），其余写 → "write"。不可见 /
    归档要写一律按「项目不存在」404（与真不存在的响应逐字相同，不泄露存在性）；可见但项目级
    写权不够 → 403。非项目路径、公开路径、非 12 位项目号一律原样放行，既有 `_require` 的角色
    门禁不受影响（Spec §18.2：ACL 只回答「能不能进入项目」，能不能做这一步归接口自己）。
    """
    match = re.match(r"^/api/projects/([0-9a-f]{12})(?:/|$)", request.url.path)
    if not match:
        return
    if request.url.path in _PUBLIC_PATHS:
        return
    user = current_user(request)
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        mode = "read"
    elif project_access.is_contribute_route(request.method, request.url.path):
        # 白名单里的专属业务动作（财务确认成本 / 总监审核发布 / 报告回传…）：ACL 不得
        # 提前否决，否则接口自己的 _require 永远没有执行机会（Spec §18.4 / §18.5）。
        mode = "contribute"
    else:
        mode = "write"
    try:
        project_access.require_project_access(match.group(1), user, mode)
    except project_access.ProjectAccessError as exc:
        if exc.code == "not_found":
            raise HTTPException(404, "项目不存在") from exc
        raise HTTPException(403, exc.message or "无权访问该项目") from exc


def _require(user: dict, allowed: set, msg: str) -> None:
    if (user or {}).get("role") in allowed:
        return
    if CPQ_SSO_ENABLED:
        # 各处的原始文案说的是 tech_app 自己那套角色（工程师/工艺技术总监…），
        # 而 CPQ 侧只有销售经理/工艺经理/财务经理 —— 原样抛出去，用户会照着一个
        # 不存在的角色去找权限。这里统一改写成 CPQ 的口径，一处改覆盖全部接口。
        #
        # 说清**这一步**要谁，别一律写"仅限工艺经理"：2.3 成本测算归财务经理，
        # 拿工艺经理去要求财务，等于把人往错的方向支（这条文案曾经就是这么误导的）。
        wanted = "、".join(
            cpq_sso.cpq_role_label(code)
            for code in sorted(allowed) if code != "admin") or "有相应权限的角色"
        role = (user or {}).get("cpq_role_name") or "当前账号"
        mapped = auth.ROLE_LABEL.get((user or {}).get("role", ""), (user or {}).get("role", ""))
        raise HTTPException(
            403, f"这一步只有「{wanted}」能操作；当前账号是「{role}」"
                 f"（在技术工艺里是{mapped}），不能执行本操作。"
                 "若刚在 CPQ 里改过角色，请退出重新登录；改过角色映射则需重启技术工艺服务。")
    raise HTTPException(403, msg)


def _login_client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_login_rate(request: Request) -> None:
    key, now = _login_client_key(request), time.monotonic()
    with _login_lock:
        recent = [at for at in _login_attempts.get(key, []) if now - at < LOGIN_WINDOW_SECONDS]
        _login_attempts[key] = recent
        if len(recent) >= LOGIN_MAX_ATTEMPTS:
            raise HTTPException(429, "登录尝试过于频繁，请稍后再试")


def _record_login_attempt(request: Request, success: bool) -> None:
    key = _login_client_key(request)
    with _login_lock:
        if success:
            _login_attempts.pop(key, None)
        else:
            _login_attempts.setdefault(key, []).append(time.monotonic())


@asynccontextmanager
async def lifespan(_app):
    """统一管理启动 housekeeping，兼容 FastAPI 新版 lifespan API。"""
    _startup_housekeeping()
    yield


app = FastAPI(
    title="图纸解析与生成平台",
    version="0.1.0",
    lifespan=lifespan,
    dependencies=[Depends(auth_guard), Depends(project_write_guard)],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(CORS_ALLOW_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(cpq_auth_client.CpqAuthUnavailable)
async def cpq_auth_unavailable_handler(_request: Request, exc: Exception):
    """账号级设置的来源（配置报价 CPQ）不可用：明确 503。

    回 401 会把在线的人当成"没登录"，回 500 又像是本服务自己崩了 —— 都不是实情。
    """
    return JSONResponse(status_code=503, content={
        "detail": f"账号级模型与密钥暂时读不到：{exc}"})


@app.middleware("http")
async def project_id_guard(request: Request, call_next):
    if not _valid_project_path(request.url.path):
        return JSONResponse(status_code=404, content={"detail": "项目不存在"})
    return await call_next(request)


@app.middleware("http")
async def response_hardening(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # 认证响应与动态页面不得被浏览器/代理复用；静态资源也使用 no-cache，
    # 避免多页面手写版本号遗漏时发生“代码已更新但页面仍是旧版”。
    path = request.url.path
    if path.startswith("/api/") or path.endswith((".html", ".js", ".css")):
        response.headers.setdefault("Cache-Control", "no-cache, no-store, must-revalidate")
    return response


# 错误追踪 ID（批次 9 §7.3）：每个请求一个，响应头 X-Trace-Id + status>=400 的
# JSON 体里的 trace_id 逐字相同。用户把屏幕上这个 ID 报给运维，就能一次定位到那条日志。
TRACE_HEADER = "X-Trace-Id"


def _trace_json_body(headers, body: bytes, trace_id: str):
    """给 >=400 的 JSON 响应体补一个顶层 trace_id；不改其它形态的响应。"""
    content_type = ""
    for key, value in headers:
        if key.lower() == b"content-type":
            content_type = value.decode("latin-1").lower()
            break
    if "json" not in content_type or not body:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["trace_id"] = trace_id
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class TraceIdMiddleware:
    """给每个 HTTP 响应分配追踪 ID，并把它写进 >=400 的错误体。

    写在 ASGI 层而不是 BaseHTTPMiddleware：错误体是内层（异常处理器、项目号守卫、
    鉴权依赖）生成的，只有拿到**最终** body 才能补字段，而 http 中间件在流式响应上
    拿不到它。响应缓冲到 body 结束再一次性发出，不改变分块行为。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        trace_id = uuid.uuid4().hex[:16]
        state = {"start": None, "chunks": [], "sent": False}

        async def flush():
            if state["sent"] or state["start"] is None:
                return
            state["sent"] = True
            start = state["start"]
            body = b"".join(state["chunks"])
            headers = [(key, value) for key, value in (start.get("headers") or [])
                       if key.lower() not in (b"x-trace-id", b"content-length")]
            headers.append((b"x-trace-id", trace_id.encode("ascii")))
            if int(start.get("status") or 200) >= 400:
                patched = _trace_json_body(headers, body, trace_id)
                if patched is not None:
                    body = patched
            if body:
                headers.append((b"content-length", str(len(body)).encode("ascii")))
            await send({"type": "http.response.start", "status": start["status"],
                        "headers": headers})
            await send({"type": "http.response.body", "body": body, "more_body": False})

        async def send_wrapper(message):
            kind = message.get("type")
            if kind == "http.response.start":
                state["start"] = dict(message)
                return
            if kind == "http.response.body":
                state["chunks"].append(message.get("body") or b"")
                if not message.get("more_body"):
                    await flush()
                return
            await send(message)

        failed = False
        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException:
            failed = True
            raise
        finally:
            if not failed:
                await flush()


# 必须最后注册：中间件是「后注册的在外层」，只有最外层才看得到其余中间件
# （含项目号守卫）产生的响应。
app.add_middleware(TraceIdMiddleware)


def _startup_housekeeping():
    if CPQ_SSO_ENABLED and CPQ_MANAGER_FULL_TECH:
        # CPQ 只有销售经理/工艺经理两个角色，技术工艺的全部环节由工艺经理承担。
        # 详见 auth.enable_cpq_single_manager 的注释（含职责分离的代价）。
        auth.enable_cpq_single_manager()
    if AUTH_ENABLED and not CPQ_SSO_ENABLED:
        # 用户数据已经只有 PG 一处（配置报价 CPQ 的 cpq_wf），技术工艺的本地账号库
        # 已退役：留着 AUTH_ENABLED=true + CPQ_SSO=false 这条路，只能得到一个
        # "登录页永远密码错误"的假象。当场说清楚，并给出两条出路。
        raise RuntimeError(
            "AUTH_ENABLED=true 且 CPQ_SSO=false：用户数据已统一到配置报价 CPQ 的 "
            "Postgres，技术工艺不再有本地账号库，这条登录路径没有用户来源。"
            "两条出路：① 设 CPQ_SSO=true 并配置 CPQ_AUTH_BASE_URL 指向配置报价 CPQ"
            "（推荐）；② 本地开发设 AUTH_ENABLED=false。")
    # ThreadPoolExecutor 任务只在当前进程中存在。服务重启后明确结束旧任务，
    # 使轮询端能恢复操作，而不是永久停留在“处理中”。
    if TASK_RECOVER_ON_START:
        tasks.recover_interrupted_tasks()


def _reject_local_login() -> None:
    """本地登录必须明确拒绝，不能"看着能用"。

    SSO 模式下放着不管的话它照样发一张 tech_app 令牌，而 SSO 守卫只认 CPQ 的票 ——
    用户会看到"登录成功"然后每个接口都 401，无从判断是密码错了还是别的。

    独立模式（CPQ_SSO=false）同样拒绝：本地账号库已退役，用户数据只剩 PG 一处，
    再让它"看起来能登录"只会得到一个永远密码错误的登录框。路由保留（前端还在引用），
    但入口一律指路配置报价 CPQ。
    """
    if CPQ_SSO_ENABLED:
        raise HTTPException(
            409, "技术工艺已接入配置报价 CPQ 的统一登录，请在 CPQ 中以工艺经理身份登录")
    raise HTTPException(
        409, "技术工艺的账号与角色已统一在配置报价 CPQ 中维护，本机不再保存用户数据；"
             "请在 CPQ 里登录（本地开发请设 AUTH_ENABLED=false）。")


def _users_moved_to_cpq() -> None:
    """用户与权限的路由保留，但明确 409 指路 —— 不允许再回本地用户数据。"""
    raise HTTPException(
        409, "用户与权限在配置报价 CPQ 中维护：请到 CPQ 的「用户管理」里建号、改角色、"
             "停用账号或重置口令（技术工艺不再保存本地账号；存量账号见 "
             "scripts/migrate_users_to_pg.py）。")


@app.post("/api/login")
def login(body: LoginBody, request: Request):
    _reject_local_login()
    _check_login_rate(request)
    u = store.get_user(body.username)
    if not u or not auth.verify_password(body.password, u.get("password_hash", "")):
        _record_login_attempt(request, False)
        raise HTTPException(401, "用户名或密码错误")
    if u.get("is_system"):
        _record_login_attempt(request, False)
        raise HTTPException(403, "system 为历史项目归档账号，不能登录")
    _record_login_attempt(request, True)
    return {"token": auth.make_token(u["username"], u["role"]), "user": auth.public_user(u)}


def _valid_username(value: str) -> str:
    username = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        raise HTTPException(400, "用户名需为 3–40 位字母、数字、点、下划线或连字符")
    return username


@app.post("/api/register")
def register(body: RegisterBody):
    """企业账号注册：账号先以只读身份启用，角色由管理员在用户管理中授予。"""
    _reject_local_login()
    username = _valid_username(body.username)
    if store.get_user(username):
        raise HTTPException(409, "用户名已存在")
    if body.requested_role not in auth.ROLES or body.requested_role == "admin":
        raise HTTPException(400, "申请角色不合法")
    rec = auth.make_user(username, body.password, "viewer", body.display_name.strip())
    rec["requested_role"] = body.requested_role
    store.save_user(username, rec)
    return {"user": auth.public_user(rec), "message": "注册成功，当前为只读权限；请由系统管理员授予业务角色。"}


@app.get("/api/me")
def whoami(user: dict = Depends(current_user)):
    return {
        "user": user,
        "auth_enabled": (AUTH_ENABLED and not AUTH_AUTO_ADMIN) or CPQ_SSO_ENABLED,
        # 前端据此决定：登录入口指向 CPQ 而不是 tech_app 自己的 auth.html，
        # 以及非工艺经理时把写操作控件置灰并说明原因。
        "sso": {
            "enabled": CPQ_SSO_ENABLED,
            "provider": "配置报价 CPQ",
            # 前端要按 user_id 管账号（改角色 / 停用 / 重置口令都在 CPQ 的
            # /auth/users/{user_id} 上），username 只作登录名与展示。
            "user_id": (user or {}).get("user_id") or (user or {}).get("cpq_user_id") or "",
            # 登录提示要跟开关一致：全权模式下工艺经理一个人做完，关掉之后 3.2 审核 /
            # 3.3 发布归「工艺技术总监」，再声称"只能工艺经理使用"会把人挡在门外。
            "required_role_name": "工艺经理" if CPQ_MANAGER_FULL_TECH else "工艺技术总监",
            # can_write 是**工艺侧**的写权限（2.1/2.2 解析、生成、确认）。
            # can_cost 是 2.3 成本测算 —— 那是财务经理的步骤，他在工艺侧只读，
            # 但绝不能因此被前端一刀切拦成"什么都不能做"（cpq-sso.js 的写拦截
            # 原来只看 can_write，财务经理点测算连请求都发不出去）。
            "can_write": (user or {}).get("role") in auth.WRITE_ROLES,
            "can_cost": (user or {}).get("role") in auth.COST_ROLES,
            # can_review = 3.2 报告审核（REVIEW_ROLES）、can_publish = 3.3 报告发布
            # （DIRECTOR_ROLES）。前端据此放行对应按钮，否则工艺技术总监点「审核通过」
            # 「发布」会被前端伪 403 挡下（和当初财务经理点「测算」是同一个坑）；
            # 最终判定仍在后端 _require。
            "can_review": (user or {}).get("role") in auth.REVIEW_ROLES,
            "can_publish": (user or {}).get("role") in auth.DIRECTOR_ROLES,
            "role_name": (user or {}).get("cpq_role_name") or auth.ROLE_LABEL.get(
                (user or {}).get("role", ""), ""),
        } if CPQ_SSO_ENABLED else {"enabled": False},
    }


@app.put("/api/me")
def update_my_profile(body: UpdateMyProfile, user: dict = Depends(current_user)):
    """改本人的显示名 / 密码。用户数据只剩 PG 一处，改动转发给配置报价 CPQ。"""
    if not CPQ_SSO_ENABLED:
        raise HTTPException(
            409, "账号资料在配置报价 CPQ 中维护：请在 CPQ 里改显示名与密码"
                 "（本地开发模式没有用户来源，不签发技术工艺自己的令牌）。")
    display_name = body.display_name.strip()
    if display_name:
        cpq_auth_client.update_profile(display_name)
    if body.new_password:
        if not body.current_password:
            raise HTTPException(400, "请输入当前密码")
        cpq_auth_client.change_password(body.current_password, body.new_password)
    profile = dict(user)
    if display_name:
        profile["display_name"] = display_name
    return {"user": profile, "message": "资料已更新（以配置报价 CPQ 的账号为准）"}


@app.get("/api/users")
def list_users_ep(user: dict = Depends(current_user)):
    # 路由保留（前端仍在引用），但用户数据只在 CPQ 一处：不允许再回本地账号。
    _users_moved_to_cpq()


@app.post("/api/users")
def create_user_ep(body: NewUser, user: dict = Depends(current_user)):
    _users_moved_to_cpq()


@app.put("/api/users/{username}/role")
def update_user_role_ep(username: str, body: UpdateUserRole, user: dict = Depends(current_user)):
    _users_moved_to_cpq()


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health():
    # 一律以「模型设置」为准。以前这里按 .env 的 LLM_PROVIDER 分支、再读 qwen 的
    # 模型池，界面上改了模型健康检查却还报旧值。
    # 当前模型不支持图像时 resolve(vision=True) 会如实报错，健康检查不能因此 500：
    # 健康检查要回答的是"服务起没起来"，能力不匹配由图纸解析那一步去报。
    try:
        vision = llm_settings.resolve(vision=True)
    except ValueError:
        vision = llm_settings.resolve(vision=False)
    text = llm_settings.resolve(vision=False)
    return {
        "status": "ok",
        "model": vision["model"],
        "text_model": text["model"],
        "llm_provider": text["provider"],
        "vision_endpoint": vision["base_url"],
        "text_endpoint": text["base_url"],
        "api_key_configured": {
            vision["provider"]: bool(vision["api_key"]),
            text["provider"]: bool(text["api_key"]),
        },
        "cadquery_available": geometry.CADQUERY_AVAILABLE,
        # DWG 转换能力以服务端为准（Spec §7）：装了没有、能不能转，前端不许写死。
        "cad_converter": cad_converter.capability(),
        "auth_enabled": (AUTH_ENABLED and not AUTH_AUTO_ADMIN) or CPQ_SSO_ENABLED,
        # /api/health 是免登录接口：前端未登录时也要能问出"该跳哪个登录入口"。
        # session-guard.js 靠它决定不要跳本地 auth.html。
        "sso_enabled": CPQ_SSO_ENABLED,
    }


def _llm_settings_rights(user: dict) -> dict:
    """谁能改什么：模型与参数放给工艺经理，API Key 仍限管理员。"""
    role = (user or {}).get("role")
    return {"can_edit": role in auth.LLM_SETTINGS_ROLES,
            "can_edit_secrets": role in auth.ADMIN_ROLES}


@app.get("/api/capabilities/cad-converter")
def cad_converter_capability():
    """DWG 转换能力查询（Spec §7）。

    免登录：前端在没登录时也要能如实显示「当前环境尚未安装 DWG 转换能力」，
    而不是留一句写死的「已安装」。返回的是**能力**，不含任何密钥或部署路径。
    """
    return cad_converter.capability()


@app.get("/api/projects/{pid}/drawing-routing")
def project_drawing_routing(pid: str, user: dict = Depends(current_user)):
    """图纸 2D/3D 分流结论（Spec §2.3，只读）。

    只回答"这份图现在怎么走"：不写盘、不改数据、不建任务；`project_access.can_read`
    是这里的读权限口径，写权限与本路由无关（写入口沿用既有上传/解析）。
    """
    meta = store.load_meta(pid)
    if not meta:
        raise HTTPException(status_code=404, detail="项目不存在")
    if not project_access.can_read(user, meta):
        raise HTTPException(status_code=403, detail="无权访问该项目")
    return dwg_dispatch.routing_view(pid)


@app.get("/api/llm/settings")
def get_runtime_llm_settings(user: dict = Depends(current_user)):
    """全局模型配置。首页「模型设置」与 2.1 Agent 小窗读的是同一份快照。

    所有已登录用户都能看；工艺经理及以上能改模型与参数，API Key 只有管理员能改。
    密钥永远只回「是否已配置」。

    兼容保留：真正的读写只有一份实现（下面 /api/settings 是同一个函数），
    技术工艺前端与业务逻辑一律走 /api/settings，这里不再保存任何独立状态。
    """
    return llm_settings.snapshot(**_llm_settings_rights(user))


@app.put("/api/llm/settings")
def update_runtime_llm_settings(body: LlmSettingsBody, user: dict = Depends(current_user)):
    """改写全局模型配置。**任何入口改，全局生效** —— 两个页面写的是同一处。"""
    # exclude_unset 而不是 exclude_none：要能区分「没传这个字段」和「传了 null」。
    # 用 exclude_none 的话，temperature=null（恢复模型默认值）会被整个丢掉，
    # 界面上"留空"就永远生效不了。
    patch = body.model_dump(exclude_unset=True)
    # 旧客户端的 vision_model / text_model 只是同一个 model 的老名字。
    if not patch.get("model"):
        legacy = patch.get("vision_model") or patch.get("text_model")
        if legacy:
            patch["model"] = legacy
    patch.pop("vision_model", None)
    patch.pop("text_model", None)
    if not patch:
        raise HTTPException(400, "没有要修改的设置项")
    try:
        result = llm_settings.update(patch, **_llm_settings_rights(user))
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit_llm_settings_change(user, patch)
    return {"ok": True, "message": "模型设置已保存并立即生效", **result}


def audit_llm_settings_change(user: dict, patch: dict) -> None:
    """密钥的值永远不进日志，只记「改过」这件事。"""
    store.audit("_global", "llm_settings_update", {
        "actor": user.get("username", "system"),
        "fields": llm_settings.changed_fields(patch),
        "secrets_changed": llm_settings.touches_secrets(patch),
    })


class MyLlmSettingsBody(BaseModel):
    """账号级「我的模型与密钥」入参。

    只有 model 与 api_key 两项（推理参数归全局）。请求体里就算带 username / user /
    actor 也一律忽略：这三个接口只作用于 current_user，不接受指定他人。
    """
    model: Optional[str] = None
    api_key: Optional[str] = None
    api_key_provider: Optional[str] = None


def audit_user_llm_settings_change(user: dict, fields: list, secrets_changed: bool) -> None:
    """账号级改动的审计口径与全局一致：只记字段名与「是否改了密钥」，不记任何值。"""
    store.audit("_global", "user_llm_settings_update", {
        "actor": (user or {}).get("username", "system"),
        "fields": sorted(set(fields)),
        "secrets_changed": bool(secrets_changed),
    })


def _my_llm_username(user: dict) -> str:
    """账号级接口只认 current_user 的 username —— 显式取，不依赖上下文传递。"""
    return str((user or {}).get("username", "") or "").strip()


def _my_provider_rows(username: str) -> list:
    """该账号各 provider 的个人 Key 状态：只有 configured 与打码，永不回明文。"""
    personal = (user_llm.summary(username).get("keys") or {})
    rows = []
    for provider, spec in (llm_settings.PROVIDERS or {}).items():
        entry = personal.get(provider) or {}
        rows.append({
            "provider": provider,
            "label": (spec or {}).get("label") or provider,
            "base_url": llm_settings.base_url_of(provider),
            "configured": bool(entry.get("configured")),
            "hint": entry.get("hint") or "",
        })
    return sorted(rows, key=lambda row: row["provider"])


def _my_llm_payload(username: str) -> dict:
    """账号级设置 + 生效模型/来源（给界面显示"现在用的是谁的模型"）。"""
    summary = user_llm.summary(username)
    eff = llm_settings.effective(username)
    return {
        "username": username,
        "model": summary.get("model") or "",
        "has_model": bool(summary.get("has_model")),
        "keys": summary.get("keys") or {},
        "effective_model": eff.get("model") or "",
        "effective_source": eff.get("source") or "global",
        "key_source": eff.get("key_source") or "missing",
    }


# 「我的模型与密钥」：每个登录账号管自己的那一层，不设置就回落平台默认。
@app.get("/api/my/settings")
def get_my_llm_settings(user: dict = Depends(current_user)):
    """读**自己**的账号级设置；任何账号都看不到别人的（连打码都不行）。"""
    username = _my_llm_username(user)
    return {**_my_llm_payload(username), "providers": _my_provider_rows(username)}


@app.put("/api/my/settings")
def update_my_llm_settings(body: MyLlmSettingsBody, user: dict = Depends(current_user)):
    """写**自己**的账号级设置。

    model 缺键 = 不改；空串/null = 清除个人模型（回落平台默认）；api_key 必须与
    api_key_provider 成对且非空才写（留空 = 不修改，与全局设置一致）。
    """
    username = _my_llm_username(user)
    if not username:
        raise HTTPException(400, "无法确定当前账号，无法保存账号级模型设置")
    patch = body.model_dump(exclude_unset=True)
    fields: list = []
    secrets_changed = False
    try:
        if "model" in patch:
            user_llm.set_model(username, patch.get("model") or "")
            fields.append("model")
        key = str(patch.get("api_key") or "").strip()
        if key:
            provider = str(patch.get("api_key_provider") or "").strip()
            if not provider:
                raise ValueError("保存账号级 API Key 时必须指明提供商")
            user_llm.set_key(username, provider, key)
            fields.append("api_key")
            secrets_changed = True
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if fields:
        audit_user_llm_settings_change(user, fields, secrets_changed)
    return {"ok": True, "message": "已保存到「我的模型与密钥」，只对本账号生效",
            **_my_llm_payload(username), "providers": _my_provider_rows(username)}


@app.delete("/api/my/settings/keys/{provider}")
def delete_my_llm_key(provider: str, user: dict = Depends(current_user)):
    """删除**自己**在某个 provider 的个人 Key（模板 Key 不动）。"""
    username = _my_llm_username(user)
    if not username:
        raise HTTPException(400, "无法确定当前账号，无法删除账号级密钥")
    try:
        user_llm.set_key(username, provider, "")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    audit_user_llm_settings_change(user, ["api_key"], True)
    return {"ok": True, **_my_llm_payload(username), "providers": _my_provider_rows(username)}


# 四个入口（报价 / 配置 / 规则 / 技术工艺）共用的「模型设置」接口。
# 与 /api/llm/settings 是同一份实现、同一份配置、同一套角色校验，只是路径更直白。
@app.get("/api/settings")
def get_shared_model_settings(user: dict = Depends(current_user)):
    # 额外附上「我的」摘要与生效模型：面板要在同一屏里说清"平台默认是什么、
    # 我现在实际用的是谁的"。仍然只回 configured + 打码，不回明文。
    username = _my_llm_username(user)
    eff = llm_settings.effective(username)
    return {
        **get_runtime_llm_settings(user),
        "mine": user_llm.summary(username),
        "effective_model": eff.get("model") or "",
        "effective_source": eff.get("source") or "global",
    }


@app.put("/api/settings")
def update_shared_model_settings(body: LlmSettingsBody,
                                 user: dict = Depends(current_user)):
    return update_runtime_llm_settings(body, user)


@app.get("/api/projects")
def projects(scope: str = "mine", include_archived: bool = False,
             user: dict = Depends(current_user)):
    """项目列表：默认「我的」；?scope=all 是我权限范围内的全部（不是全公司）；archived 是归档。

    返回形状仍是 list（既有消费方不必改结构），每项新增 `access` 块。非法 scope → 400。
    """
    if scope not in project_access.SCOPES:
        raise HTTPException(400, f"scope 取值不合法：{scope}")
    return project_access.visible_projects(user, scope, include_archived=include_archived)


@app.patch("/api/projects/{project_id}/management")
def rename_project(project_id: str, body: ProjectManageBody, user: dict = Depends(current_user)):
    """首页项目编辑：仅改业务名称，保留图纸、工艺结果和完整审计链。"""
    _require(user, auth.WRITE_ROLES, "需要工艺工程师、技术经理或管理员权限")
    meta = _workflow_project(project_id)
    if not auth.can_edit_project(user, meta):
        raise HTTPException(403, "仅项目创建人、工艺技术经理或管理员可以编辑项目")
    name = body.name.strip()
    updated = store.rename_project(project_id, name, author=user.get("username", "system"))
    requirement = store.load_requirement(project_id)
    if requirement and requirement.get("status") in ("draft", "rejected"):
        requirement["title"] = name
        requirement["updated_at"] = _now_str()
        store.save_requirement(project_id, requirement, author=user.get("username", "system"))
    return {"project": updated, "requirement": requirement}


@app.delete("/api/projects/{project_id}/management")
def delete_project(project_id: str, user: dict = Depends(current_user)):
    """首页删除：采用软删除，避免破坏已发布报告和审计留痕。"""
    _require(user, auth.WRITE_ROLES, "需要工艺工程师、技术经理或管理员权限")
    meta = _workflow_project(project_id)
    if not auth.can_edit_project(user, meta):
        raise HTTPException(403, "仅项目创建人、工艺技术经理或管理员可以删除项目")
    store.archive_project(project_id, author=user.get("username", "system"))
    return {"ok": True}


async def _read_upload_limited(file: UploadFile, *, label: str = "文件") -> bytes:
    """分块读取 multipart 文件，并在超过服务端限额时立即终止。"""
    declared_size = getattr(file, "size", None)
    if isinstance(declared_size, int) and declared_size > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"{label}超过大小上限（{MAX_UPLOAD_BYTES // 1024 // 1024} MiB）")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"{label}超过大小上限（{MAX_UPLOAD_BYTES // 1024 // 1024} MiB）")
        chunks.append(chunk)
    return b"".join(chunks)


def _check_workflow_input_change(project_id: str, user: dict) -> dict:
    """输入变更门禁：审核中的单据不可变更，已审批需求仅经理可发起修订。"""
    requirement = store.load_requirement(project_id) or {}
    status = requirement.get("status")
    if status in {"pending_confirmation", "pending_review"}:
        raise HTTPException(409, "需求正在确认或审核中，请先退回草稿后再变更输入资料")
    report = store.load_process_report(project_id) or {}
    if report.get("status") in {"in_review", "approved", "published"}:
        raise HTTPException(409, "评估报告已送审或发布；已发布报告请先创建新版本，再变更输入资料")
    if status == "approved" and user.get("role") not in auth.MANAGER_ROLES:
        raise HTTPException(403, "已审批需求的输入修订须由工艺技术经理或管理员发起")
    return requirement


def _reset_approved_requirement_after_input_change(
    project_id: str, requirement: dict, user: dict, reason: str,
) -> None:
    """审批后的工程输入发生变更时，旧审批结论自动失效并形成修订留痕。"""
    if requirement.get("status") != "approved":
        return
    doc = RequirementDoc(**requirement)
    doc.status = "draft"
    doc.confirmed_by = None
    doc.confirmed_at = None
    doc.confirmation_note = ""
    doc.reviewed_by = None
    doc.reviewed_at = None
    doc.review_note = ""
    doc.ai_check = {}
    doc.history.append(_workflow_event("approved_requirement_reopened", user, reason))
    doc.updated_at = _now_str()
    store.save_requirement(project_id, doc.model_dump(), author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_reopened_for_input_change", {
        "by": user.get("username", "system"), "reason": reason,
    })


@app.post("/api/projects")
async def upload_project(
    file: Optional[UploadFile] = File(None),
    files: List[UploadFile] = File(default=[]),
    note: str = Form(""),
    attachments: List[UploadFile] = File(default=[]),
    # 报价线索（Spec §2）：报价侧「新增工艺」建项时会带上，用来判定这是不是正式入口。
    # 三个都可选 —— 不传就是内部测试入口，照建，但会被明确标出来并留痕。
    business_case_id: str = Form(""),
    source_task_id: str = Form(""),
    source_session_id: str = Form(""),
    user: dict = Depends(current_user),
):
    """上传一个或多个设备需求图纸，首份为原图，其余保留为可追溯图纸附件。

    入口分级由 `services.entry_origin.classify_entry` 唯一判定：带报价线索 = 正式报价
    入口（`quote`），全空 = 内部测试入口（`internal_test`）。内部测试入口不是错误，
    但必须留痕并原样回给前端，避免默默建出一张将来回传不了报价的项目。
    """
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    drawing_files = [item for item in ([file] if file else []) + list(files or []) if item and item.filename]
    if not drawing_files:
        raise HTTPException(400, "请至少上传一份模型图纸")
    primary = drawing_files[0]
    content = await _read_upload_limited(primary, label="需求图纸")
    if not content:
        raise HTTPException(400, "空文件")
    project_id = store.create_project(
        primary.filename or "source.png", content, note=note,
        owner=user.get("username", "system"),
        owner_display_name=user.get("display_name") or user.get("username", "system"),
    )
    extra_drawings = []
    author = user.get("username", "system")
    entry = entry_origin.classify_entry(
        business_case_id=business_case_id,
        source_task_id=source_task_id,
        source_session_id=source_session_id,
    )
    # 入口事实落进项目 meta：cost_flow.business_case_of 在第一次回传之前就能读到实例号，
    # 不必等到"上一次回传"才有来源。
    store.save_business_case(project_id, {
        "entry_origin": entry["origin"],
        "internal_test": entry["internal_test"],
        "clues": list(entry["clues"]),
        "business_case_id": str(business_case_id or "").strip(),
        "source_task_id": str(source_task_id or "").strip(),
        "source_session_id": str(source_session_id or "").strip(),
    }, author=author)
    if entry["internal_test"]:
        # 内部测试入口必须留痕：事后要能一眼分清哪些项目是正式报价来的。
        store.audit(project_id, "project:internal_test_entry", {
            "by": author, "entry_origin": entry["origin"],
            "internal_test": True, "reason": entry["reason"],
        })
    for drawing in drawing_files[1:]:
        data = await _read_upload_limited(drawing, label="补充模型图纸")
        if data:
            name = drawing.filename or "drawing"
            store.add_attachment(project_id, name, data, author)
            extra_drawings.append(name)
    for att in attachments or []:
        data = await _read_upload_limited(att, label="补充文件")
        if data:
            store.add_attachment(project_id, att.filename or "attachment", data, author)
    if extra_drawings:
        store.audit(project_id, "upload_additional_drawings", {
            "by": user.get("username", "system"), "files": extra_drawings,
        })
    return {"project_id": project_id, "source_filename": primary.filename,
            "additional_drawings": extra_drawings, "entry_origin": entry}


def _route(method_and_path: str):
    """`@_route("POST /api/projects/{id}/…")`：把方法名与路径写在同一个字符串里再注册。

    本仓库的验收红测按「方法 + 路径」这一个字符串读路由表（见
    tests/test_quote_first_project_entry_red.py 的 routes()）：方法与路径写在一处、
    注册时再拆开，就不会出现"文档里写 POST、代码里注册成 GET"的漂移。
    """
    method, _, path = method_and_path.partition(" ")
    return app.api_route(path.strip(), methods=[method.strip().upper()])


@_route("POST /api/projects/{project_id}/quote-link/recover")
def recover_quote_link(project_id: str, body: QuoteLinkRecoveryBody,
                       user: dict = Depends(current_user)):
    """历史项目一次性恢复「报价关联」：只写技术侧事实，绝不建、改报价卡片。

    判定与既有的落点四态语义一致（linked / multiple_candidates / create_new / no_candidate）：
    至少要有一个线索（业务实例号 / 报价会话 / 来源任务 / 来源会话），或者明确要求新建报价
    卡片并写明原因。恢复动作只做两件事：把线索合并写进项目 meta，并写一条审计留痕 ——
    报价卡片的新建仍然只能由回传命令带 create_new + create_reason 完成，这里不代劳。
    """
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    clues = {
        "business_case_id": str(body.business_case_id or "").strip(),
        "quote_session_id": str(body.quote_session_id or "").strip(),
        "source_task_id": str(body.source_task_id or "").strip(),
        "source_session_id": str(body.source_session_id or "").strip(),
    }
    reason = str(body.create_reason or "").strip()
    if not any(clues.values()) and not body.create_new:
        raise HTTPException(400, "请至少提供一个报价线索（业务实例号 / 报价会话 / 来源任务 / "
                                "来源会话），或明确新建报价卡片并写明原因")
    if body.create_new and not reason:
        raise HTTPException(400, "明确新建报价卡片时必须写明新建原因")
    author = user.get("username", "system")
    # 「明确新建」同样是一次报价侧动作（下一次回传就会把这张卡片建出来），所以它也算正式入口；
    # 只给线索的恢复按线索本身判定，不为通过判定而编造来源。
    entry = entry_origin.classify_entry(
        business_case_id=clues["business_case_id"],
        source_task_id=clues["source_task_id"] or clues["quote_session_id"],
        source_session_id=clues["source_session_id"],
        source="明确新建报价卡片" if body.create_new else "",
    )
    recovery = {
        # 被恢复的就是这个项目本身：历史项目没有来源线索，恢复动作发生在这里。
        "recovered_from_project_id": project_id,
        "recovery_reason": reason or str(body.note or "").strip() or "历史项目一次性恢复报价关联",
        "recovered_by": author,
        "recovered_at": now_cst_str(),
    }
    store.save_business_case(project_id, {
        **{key: value for key, value in clues.items() if value},
        "entry_origin": entry["origin"],
        "internal_test": entry["internal_test"],
        "clues": list(entry["clues"]),
        "create_new": bool(body.create_new),
        **recovery,
    }, author=author)
    store.audit(project_id, "quote_link:recovered", {
        "by": author, "create_new": bool(body.create_new),
        "business_case_id": clues["business_case_id"],
        "quote_session_id": clues["quote_session_id"],
        "source_task_id": clues["source_task_id"],
        "source_session_id": clues["source_session_id"],
        "recovery_reason": recovery["recovery_reason"],
    })
    return {"business_case_id": clues["business_case_id"],
            "quote_session_id": clues["quote_session_id"],
            "entry_origin": entry, "recovery": recovery}


@app.post("/api/projects/{project_id}/attachments")
async def upload_project_attachments(
    project_id: str, files: List[UploadFile] = File(...), user: dict = Depends(current_user),
):
    """为已创建的需求追加图纸、模型、BOM 或技术资料，并写入审计。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    requirement = _check_workflow_input_change(project_id, user)
    prepared = []
    for item in files or []:
        data = await _read_upload_limited(item, label="补充文件")
        if data:
            prepared.append((item.filename or "attachment", data))
    if not prepared:
        raise HTTPException(400, "未收到有效附件")
    saved = []
    author = user.get("username", "system")
    for name, data in prepared:
        store.add_attachment(project_id, name, data, author)
        saved.append(name)
    _reset_approved_requirement_after_input_change(project_id, requirement, user, "补充输入附件")
    store.audit(project_id, "upload_workflow_attachments", {"by": user.get("username", "system"), "files": saved})
    return {"attachments": (store.load_meta(project_id) or {}).get("attachments", [])}


@app.post("/api/projects/{project_id}/source")
async def replace_project_source(
    project_id: str, file: UploadFile = File(...), user: dict = Depends(current_user),
):
    """替换需求的原始 2D 图纸；后续解析始终使用新图纸。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    requirement = _check_workflow_input_change(project_id, user)
    content = await _read_upload_limited(file, label="需求图纸")
    if not content:
        raise HTTPException(400, "空文件")
    store.replace_source(project_id, file.filename or "source.png", content, user.get("username", "system"))
    _reset_approved_requirement_after_input_change(project_id, requirement, user, "替换原始图纸")
    return {"source_filename": (store.load_meta(project_id) or {}).get("source_filename")}


@app.get("/api/projects/{project_id}/attachments")
def list_project_attachments(project_id: str):
    meta = store.load_meta(project_id)
    if not meta:
        raise HTTPException(404, "项目不存在")
    return {"attachments": meta.get("attachments", [])}


@app.get("/api/projects/{project_id}/attachments/{filename}")
def get_project_attachment(project_id: str, filename: str):
    path = store.attachment_file(project_id, filename)
    if not path or not path.exists():
        raise HTTPException(404, "附件不存在")
    return FileResponse(str(path), filename=path.name)


@app.get("/api/projects/{project_id}/files")
def list_project_files(project_id: str):
    """本次任务涉及的所有文件清单：输入的图纸文档 + 流程中产出的模型、图纸、表格。

    界面上的文件浮窗只认这一个接口。分散在各步骤里的产出（几何、2D 图、BOM、
    成本表）本来只能在各自的面板里下载，评估过程一长就没人记得哪一步生成过什么；
    汇总到一处才谈得上"过程中所有文件都能查看"。

    只列**确实已经生成**的东西 —— 列一个点开是 404 的链接比不列更糟。
    """
    meta = store.load_meta(project_id)
    if not meta:
        raise HTTPException(404, "项目不存在")
    base = f"/api/projects/{project_id}"
    groups: List[dict] = []

    inputs: List[dict] = []
    if meta.get("source_filename"):
        inputs.append({"name": meta["source_filename"], "kind": "image",
                       "url": f"{base}/source", "note": "需求原图（解析依据）"})
    for name in meta.get("attachments", []) or []:
        inputs.append({"name": name, "kind": "doc",
                       "url": f"{base}/attachments/{quote(name)}", "note": "技术文档 / 补充视图"})
    if inputs:
        groups.append({"key": "input", "title": "输入资料", "files": inputs})

    models: List[dict] = []
    for part in (store.load_geometry_result(project_id) or {}).get("parts", []):
        for field, kind, suffix in (("stl_url", "model", "STL"), ("step_url", "model", "STEP")):
            if part.get(field):
                models.append({"name": f"{part.get('part_id')} {part.get('name') or ''} · {suffix}",
                               "kind": kind, "url": part[field], "note": "生成的 3D 几何"})
    if models:
        groups.append({"key": "geometry", "title": "3D 几何", "files": models})

    drawings: List[dict] = []
    for part in (store.load_drawings_result(project_id) or {}).get("parts", []):
        label = f"{part.get('part_id')} {part.get('name') or ''}"
        for view, url in (part.get("views") or {}).items():
            drawings.append({"name": f"{label} · {view}", "kind": "image", "url": url,
                             "note": "生成的 2D 工程图"})
        if part.get("dxf_url"):
            drawings.append({"name": f"{label} · DXF", "kind": "doc", "url": part["dxf_url"],
                             "note": "生成的 2D 工程图"})
    if drawings:
        groups.append({"key": "drawings", "title": "2D 工程图", "files": drawings})

    tables: List[dict] = []
    if store.load_ir(project_id):
        tables.append({"name": "BOM.csv", "kind": "table", "url": f"{base}/bom.csv",
                       "note": "按当前零件清单导出"})
    if store.load_costest(project_id):
        tables.append({"name": "成本测算.csv", "kind": "table", "url": f"{base}/costest.csv",
                       "note": "按当前成本测算导出"})
    if tables:
        groups.append({"key": "tables", "title": "导出表格", "files": tables})

    note = str(meta.get("note") or "").strip()
    return {
        "groups": groups,
        "note": note,
        "total": sum(len(group["files"]) for group in groups),
    }


def _geometry_entry(project_id: str, r) -> dict:
    base = f"/api/projects/{project_id}/geometry"
    return {
        "part_id": r.part_id, "name": r.name, "ok": r.ok,
        "volume_mm3": r.volume_mm3, "mass_g": r.mass_g, "bbox": r.bbox,
        "warnings": r.warnings, "error": r.error,
        "stl_url": f"{base}/{r.part_id}.stl" if r.ok else None,
        "step_url": f"{base}/{r.part_id}.step" if r.ok else None,
    }


def _geometry_payload(project_id: str, results) -> dict:
    return {"parts": [_geometry_entry(project_id, r) for r in results]}


def _drawings_entry(project_id: str, r) -> dict:
    base = f"/api/projects/{project_id}/geometry"
    return {
        "part_id": r.part_id, "name": r.name, "ok": r.ok,
        "views": {v: f"{base}/{fn}" for v, fn in r.views.items()},
        "dxf_url": f"{base}/{r.dxf}" if r.dxf else None,
        "warnings": r.warnings, "error": r.error,
    }


def _assemble_batch(project_id: str, parts, results, blocked, build_entry) -> dict:
    """把逐件结果拼成「覆盖 IR 全部零件、保持 IR 顺序」的批量结果（C1）。

    被预检挡下的零件根本没进 CAD：条目带 skipped=True 与结构化 issues，
    与成功件一起落库 —— 重进项目 / 重载看板才能看到「4 成功 + 1 待补」。
    计数口径：succeeded + failed + skipped == total，
    预检被挡算 skipped，预检通过但运行期报错算 failed。
    """
    by_id = {r.part_id: r for r in results}
    issues_by_id: dict = {}
    for issue in blocked:
        issues_by_id.setdefault(issue.get("part_id"), []).append(issue)
    entries: list[dict] = []
    skipped_parts: list[str] = []
    succeeded = failed = skipped = 0
    for part in parts:
        result = by_id.get(part.part_id)
        if result is None:
            issues = issues_by_id.get(part.part_id) or []
            entries.append(part_versions.stamp_entry({
                "part_id": part.part_id, "name": part.name, "ok": False,
                "warnings": [], "skipped": True, "issues": issues,
                "error": issues[0]["message"] if issues else "该零件未生成",
            }, part))
            skipped += 1
            skipped_parts.append(part.part_id)
            continue
        entry = build_entry(project_id, result)
        # 逐件来源指纹（C1）：重进项目时才知道这个条目是不是当前形状生成的。
        part_versions.stamp_entry(entry, part)
        entry["skipped"] = False
        if result.ok:
            succeeded += 1
            entry["issues"] = []
        else:
            failed += 1
            # 预检通过却运行期失败：只有这种才适合「再次生成」，与缺参数区分开。
            entry["issues"] = [geometry.runtime_issue(
                result.part_id, result.name, result.error or "CAD 生成失败")]
        entries.append(entry)
    return {
        "status": "succeeded" if not skipped and not failed else "partial",
        "total": len(parts), "succeeded": succeeded, "failed": failed,
        "skipped": skipped, "skipped_parts": skipped_parts,
        "blocked": blocked, "parts": entries,
    }


def _upsert_part(payload: dict, part_entry: dict) -> None:
    parts = payload.setdefault("parts", [])
    for i, p in enumerate(parts):
        if p.get("part_id") == part_entry.get("part_id"):
            parts[i] = part_entry
            return
    parts.append(part_entry)


def _drawings_payload(project_id: str, results) -> dict:
    return {"parts": [_drawings_entry(project_id, r) for r in results]}


@app.post("/api/projects/3d")
async def upload_3d(
    file: UploadFile = File(...), note: str = Form(""),
    user: dict = Depends(current_user),
):
    """上传 3D 模型(STEP/STP),用 OCCT 反解出零件/结构树/几何属性,
    并直接据原始实体生成 3D(STEP/STL) 与 2D 工程图。

    同步前置门禁（Spec `dwg-file-capability-preflight.md` §5）：先按**内容**判格式，
    非 STEP 一律当场拒绝，绝不「先建项目、再建注定失败的 import_3d 任务」——
    过去 DWG 就是这么被受理的，用户看到的是"已受理"，最后才等到
    `STEP File could not be loaded`。
    """
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    try:
        content = await _read_upload_limited(file, label="3D 模型")
    except HTTPException as exc:
        # 上传侧的大小门禁也统一成四键结构化错误（Spec §3），不再只回自由文本。
        if exc.status_code != 413:
            raise
        raise HTTPException(413, detail=file_preflight.FileCapabilityError(
            "FILE_TOO_LARGE").as_detail()) from exc
    fname = file.filename or "model.step"
    # 格式预检必须排在 `step_import.AVAILABLE` **之前**：环境没装 CadQuery 时，
    # DWG 也必须先得到「DWG 不是 3D 模型」的准确结论，而不是含糊的 503。
    detected = file_preflight.detect_file_format(fname, content)
    gate = file_preflight.import_3d_gate_error(detected)
    if gate is not None:
        # 拒绝发生在建项目之前（没有项目可挂审计），预检结果随响应体返回给调用方/
        # 前端展示：码、文案、detected、retryable 四键齐全（Spec §3/§5）。
        raise HTTPException(gate.http_status, detail=gate.as_detail())
    if not step_import.AVAILABLE:
        raise HTTPException(503, "CadQuery 未安装，STEP 导入不可用。")
    project_id = store.create_project(
        fname, content, note=note,
        owner=user.get("username", "system"),
        owner_display_name=user.get("display_name") or user.get("username", "system"),
    )
    expected_input_revision = _input_revision(project_id)

    def job():
        ir, solid_map = step_import.import_step(content, fname)
        _assert_input_unchanged(project_id, expected_input_revision)
        ir_payload = ir.model_dump()
        ir_hash = _digest_value(ir_payload)
        out_dir = store.geometry_dir(project_id)
        name_by_id = {p.part_id: p.name for p in ir.parts}
        g_results = [
            geometry.result_from_solid(pid, name_by_id.get(pid, pid), solid, out_dir)
            for pid, solid in solid_map
        ]
        geometry_payload = _geometry_payload(project_id, g_results)
        geometry_payload["source_ir_hash"] = ir_hash
        d_results = [
            drawing2d.generate_from_solid(pid, name_by_id.get(pid, pid), solid, out_dir)
            for pid, solid in solid_map
        ]
        _assert_input_unchanged(project_id, expected_input_revision)
        store.save_ir(project_id, ir_payload, stage="parsed_3d",
                      author=user.get("username", "system"))
        store.save_geometry_result(project_id, geometry_payload)
        drawings_payload = _drawings_payload(project_id, d_results)
        drawings_payload["source_ir_hash"] = ir_hash
        store.save_drawings_result(project_id, drawings_payload)
        store.sync_geometry(project_id)  # 同步到对象存储(Local 后端空操作)
        store.audit(project_id, "import_step", {"parts": len(solid_map)})
        # STEP 导入同样是"从零得到一份零件清单"，之前这条路径压根没有检索报告，
        # 界面上就一直空着。传副本：别把 component_match 塞进刚存好的 IR 里。
        _refresh_component_match(project_id, dict(ir_payload), kept="导入结果")
        return {"parts": len(solid_map)}

    task_id = tasks.submit(
        project_id, "import_3d", job, cad=True,
        dedup_key=_task_key("import_3d", expected_input_revision, content),
    )
    return {"project_id": project_id, "task_id": task_id}


def _kb_unavailable_info(exc: BaseException) -> dict:
    """把"知识库这次没查到"整理成 store 要的固定字段（reason/message/error/at）。

    「库里没有可复用零件」与「压根没查到」必须能分开：前者仍是一份正常报告，
    后者落成 component_match_unavailable，由看板顶部与结论区如实显示（## 92）。
    """
    kb_down = isinstance(exc, component_match.KbUnavailable)
    return {
        "reason": "kb_unavailable" if kb_down else "kb_error",
        "message": ("零件库连不上，本次未检索" if kb_down
                    else "零件库检索失败，本次未检索"),
        "error": str(exc)[:200],
        "at": store._now(),
    }


def _tool_detail(tool: str, title: str, *, status: str,
                 input: Optional[dict] = None, output: Optional[dict] = None) -> dict:
    """过程事件的工具明细统一五键形状（Spec B1）：tool / title / input / output / status。

    只放事实（件号、序号、查询条件、判定、匹配度、差异、计数）——密钥、prompt 原文、
    附件内容与候选件完整数组一律不进明细；**规模与结构摘要**（字数 / 段落数 / 图片数 /
    顶层字段规模）与**文件名**（非路径）例外，允许放进明细。
    """
    return {"tool": str(tool or ""), "title": str(title or ""),
            "input": dict(input or {}), "status": str(status or "running"),
            "output": dict(output or {})}


def _refresh_component_match(project_id: str, payload: dict, *, kept: str) -> None:
    """把当前零件清单拿到零部件库里比一遍，标出可复用/可改制/未匹配。

    凡是**改写了零件清单**的步骤跑完都要调它：报告是按零件算的，清单变了
    （拆解推荐甚至会改 part_id）旧报告就对不上，界面上表现为"库里明明有，还是未匹配"。

    检索本身不调模型、只读本地库，所以顺手跑不增加成本；但它失败也**不能**让
    已经拿到的解析/推荐结果作废 —— 那是花了模型钱的，检索只是附加信息。
    但"没查到"这件事必须**留下现场**（落盘 + 左边提示），不能只写一条审计就完事：
    审计是给事后追溯的，用户当场看不到，刷新页面后现场就没了。
    """
    tasks.process_event("tool", "检索零部件库开始",
                        detail=_tool_detail("component_match", "零部件库检索",
                                            status="running"))
    try:
        report = component_match.match_project(
            project_id, payload, progress=tasks.report_progress,
        )
        component_match.save_report(project_id, report)   # 成功即清掉"未检索"状态
        payload["component_match"] = report
        summary = report.get("summary") or {}
        reuse = summary.get("reuse", 0)
        modify = summary.get("modify", 0)
        tasks.process_event("tool", f"  ↳ 命中可复用 {reuse} 件、可改制 {modify} 件",
                            detail=_tool_detail(
                                "component_match", "零部件库检索", status="ok",
                                output={"total": summary.get("total", 0), "reuse": reuse,
                                        "modify": modify, "new": summary.get("new", 0),
                                        "library_size": report.get("library_size", 0)}))
    except Exception as exc:
        info = _kb_unavailable_info(exc)
        reason = str(exc)[:120]
        tasks.report_progress(f"  ↳ {info['message']}：{reason}（不影响已得到的{kept}）")
        tasks.process_event("tool", f"  ↳ 零部件库检索失败：{reason}",
                            detail=_tool_detail("component_match", "零部件库检索",
                                                status="failed",
                                                output={"reason": reason}))
        store.audit(project_id, "component_match_failed", {"error": str(exc)[:200]})
        store.save_component_match_unavailable(project_id, info)


@app.post("/api/projects/{project_id}/parse")
def parse(project_id: str, user: dict = Depends(current_user)):
    """调用当前配置的视觉模型解析原图(结合补充说明/佐证文件) -> IR(异步任务)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    src = store.source_path(project_id)
    if not src or not src.exists():
        raise HTTPException(404, "项目或原图不存在")
    data, name = src.read_bytes(), src.name
    note, atts = store.get_note(project_id), store.load_attachments(project_id)
    author = user.get("username", "system")
    expected_input_revision = _input_revision(project_id)

    def job():
        # 每一步都同时播「在做什么」和「做出了什么」—— 对话框里只显示动作、
        # 不显示结果的话，用户还是得去别处翻才知道这一步到底产出了啥。
        manifest = vision.build_input_manifest(name, data, atts)
        store.audit(project_id, "drawing_parse_stage:manifest", manifest)
        tasks.report_progress(
            f"读取输入：{name}"
            + (f"、技术文档 {len(atts)} 份" if atts else "")
            + (f"、补充说明 {len(note.strip())} 字" if note.strip() else ""))
        # 显示**生效模型**：发起账号（谁点的解析）可能有自己的模型，
        # 写死全局默认会让人对着日志找不到实际用的是哪个模型。
        tasks.report_progress(f"调用多模态模型解析图纸（{llm_settings.effective()['model']}）")
        ir = vision.parse_drawing(data, name, note=note, attachments=atts)
        tasks.report_progress(
            f"  ↳ 解析完成：{len(ir.parts)} 个零件、{len(ir.open_questions or [])} 个待澄清问题、"
            f"证据 {len(ir.evidence_ledger)} 条")
        tasks.report_progress("校验尺寸与证据，写入设计意图（IR）")
        _assert_input_unchanged(project_id, expected_input_revision)
        store.save_drawing_analysis(project_id, vision.pipeline_report(ir, manifest))
        store.save_ir(project_id, ir.model_dump(), stage="parsed", author=author)
        store.audit(project_id, "parse_input_context", {
            "source_file": name,
            "note_included": bool(note.strip()),
            "attachments_included": [attachment_name for attachment_name, _ in atts],
            "sop_version": ir.sop_version,
            "ai_status": ir.ai_status.value,
            "evidence_count": len(ir.evidence_ledger),
        })
        payload = ir.model_dump()
        _refresh_component_match(project_id, payload, kept="解析结果")
        return payload

    return {"task_id": tasks.submit(
        project_id, "parse", job,
        dedup_key=_task_key("parse", expected_input_revision, note, atts),
    )}


@app.get("/api/projects/{project_id}/component-match")
def get_component_match(project_id: str, user: dict = Depends(current_user)):
    """图纸拆解时的零部件库检索报告 + 本次"没查到"的状态。

    报告缺失只说明"还没检索过"；`unavailable` 非空才说明"检索了但没查成"。
    前端靠这个字段把"库里没有可复用零件"与"压根没查到"分开（## 92 C3）。

    没查到时不回 `library_size: 0`：那是把故障说成"库内 0 条"。上一次成功结论的
    真实计数照旧带出（可能已过期，由前端标注），本来就没有报告时才给 null。
    """
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    unavailable = store.load_component_match_unavailable(project_id)
    report = store.load_component_match(project_id)
    body = dict(report) if report else {"items": [], "summary": {}}
    body["unavailable"] = unavailable or None
    if unavailable and not body.get("library_size"):
        body["library_size"] = None
    return body


@app.post("/api/projects/{project_id}/component-match")
def run_component_match(project_id: str, user: dict = Depends(current_user)):
    """按当前 IR 重新检索零部件库（知识库更新后可单独重跑，无需重新解析图纸）。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir = store.load_ir(project_id)
    if not ir:
        raise HTTPException(400, "尚未完成图纸解析")

    def job():
        try:
            report = component_match.match_project(
                project_id, ir, progress=tasks.report_progress,
            )
        except Exception as exc:
            # 任务照样失败（异常继续上抛），但先把"没查到"落成状态：
            # 刷新页面、换台机器打开，看板顶部的警示都还在。
            store.save_component_match_unavailable(project_id, _kb_unavailable_info(exc))
            raise
        component_match.save_report(project_id, report)   # 成功即清掉"未检索"状态
        return report

    return {"task_id": tasks.submit(project_id, "component_match", job)}


# --------------------------------------------------------------------------- #
# 工艺推荐 / 成本测算的知识库检索
#
# 两个 helper 在对应的异步任务里被调用，进度经 tasks.report_progress 走既有轮询
# 通道，因此检索过程会显示在 Agent 对话框里。检索失败一律降级为"没查到"，
# 绝不能让它挡住后面的推荐 —— 库是辅助依据，不是前置条件。
# --------------------------------------------------------------------------- #
def _match_for_part(project_id: str, part_id: str) -> Optional[dict]:
    """取该零件在图纸拆解阶段的零部件库匹配结论，用来定类别与默认路线/物料。"""
    report = store.load_component_match(project_id) or {}
    return next((item for item in report.get("items") or []
                 if item.get("part_id") == part_id), None)


def _process_lookup_for(project_id: str, part_id: str, part: dict) -> Optional[dict]:
    tasks.process_event("tool", f"检索企业工艺库（{part_id}）",
                        detail=_tool_detail("process_lookup", "企业工艺库检索",
                                            status="running", input={"part_id": part_id}))
    try:
        report = process_lookup.lookup_part(
            part, match=_match_for_part(project_id, part_id),
            progress=tasks.report_progress,
        )
        process_lookup.save_report(project_id, part_id, report)
        summary = report.get("summary") or {}
        route_code = (report.get("route") or {}).get("route_code") or ""
        route_steps = summary.get("route_steps", 0)
        extra_steps = summary.get("extra_steps", 0)
        tasks.process_event("tool", f"  ↳ 命中 {route_steps} 道工序、补充工序 {extra_steps} 条",
                            detail=_tool_detail(
                                "process_lookup", "企业工艺库检索", status="ok",
                                input={"part_id": part_id},
                                output={"route_code": route_code, "route_steps": route_steps,
                                        "extra_steps": extra_steps,
                                        "feature_gaps": len(report.get("feature_gaps") or []),
                                        "library_steps": summary.get("library_steps", 0)}))
        return report
    except Exception as exc:
        reason = str(exc)[:120]
        tasks.process_event("tool", f"  ↳ 企业工艺库检索失败：{reason}",
                            detail=_tool_detail("process_lookup", "企业工艺库检索",
                                                status="failed", input={"part_id": part_id},
                                                output={"reason": reason}))
        store.audit(project_id, "process_lookup_failed",
                    {"part_id": part_id, "error": str(exc)[:200]})
        return None


def _cost_lookup_for(project_id: str, part_id: str, part: dict, quantity: int) -> Optional[dict]:
    tasks.process_event("tool", f"检索企业成本库（{part_id}）",
                        detail=_tool_detail("cost_lookup", "企业成本库检索", status="running",
                                            input={"part_id": part_id, "quantity": quantity}))
    try:
        report = cost_lookup.lookup_part(
            part, quantity=quantity, match=_match_for_part(project_id, part_id),
            process_report=store.load_process_lookup(project_id, part_id),
            progress=tasks.report_progress,
        )
        cost_lookup.save_report(project_id, part_id, report)
        summary = report.get("summary") or {}
        materials = summary.get("materials", 0)
        rates = summary.get("rates", 0)
        tasks.process_event("tool", f"  ↳ 命中物料价 {materials} 条、费率 {rates} 条",
                            detail=_tool_detail(
                                "cost_lookup", "企业成本库检索", status="ok",
                                input={"part_id": part_id, "quantity": quantity},
                                output={"materials": materials, "rates": rates,
                                        "factors": summary.get("factors", 0)}))
        return report
    except Exception as exc:
        reason = str(exc)[:120]
        tasks.process_event("tool", f"  ↳ 企业成本库检索失败：{reason}",
                            detail=_tool_detail("cost_lookup", "企业成本库检索",
                                                status="failed",
                                                input={"part_id": part_id, "quantity": quantity},
                                                output={"reason": reason}))
        store.audit(project_id, "cost_lookup_failed",
                    {"part_id": part_id, "error": str(exc)[:200]})
        return None


@app.get("/api/projects/{project_id}/parts/{part_id}/process-lookup")
def get_process_lookup(project_id: str, part_id: str):
    """工艺推荐所依据的工艺库检索报告（路线模板 / 补充工序 / 库内空白）。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return store.load_process_lookup(project_id, part_id) or {}


@app.get("/api/projects/{project_id}/parts/{part_id}/cost-lookup")
def get_cost_lookup(project_id: str, part_id: str):
    """成本测算所依据的价格与费率检索报告。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return store.load_cost_lookup(project_id, part_id) or {}


@app.post("/api/projects/{project_id}/parts/{part_id}/library-lookup")
def run_library_lookup(project_id: str, part_id: str, quantity: int = 1,
                       user: dict = Depends(current_user)):
    """只跑知识库检索，不调模型。

    知识库更新（补了费率、改了路线）后可以单独重查一遍看依据变了什么，
    不必为此重跑一次要花钱的工艺推荐或成本测算。
    """
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(400, "尚未完成图纸解析")
    part = next((p for p in DesignIR(**ir_dict).parts if p.part_id == part_id), None)
    if not part:
        raise HTTPException(404, f"零件 {part_id} 不存在")
    part_dict = part.model_dump()
    qty = max(1, int(quantity or 1))

    def job():
        return {
            "process": _process_lookup_for(project_id, part_id, part_dict),
            "cost": _cost_lookup_for(project_id, part_id, part_dict, qty),
        }

    return {"task_id": tasks.submit(project_id, "library_lookup", job)}


@app.post("/api/projects/{project_id}/verify")
def verify(project_id: str, user: dict = Depends(current_user)):
    """自校验第二遍: 对照原图核对初步 IR 的尺寸/特征并重估置信度(异步任务)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    src = store.source_path(project_id)
    if not src or not src.exists():
        raise HTTPException(404, "项目或原图不存在")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    data, name = src.read_bytes(), src.name
    note, atts = store.get_note(project_id), store.load_attachments(project_id)
    author = user.get("username", "system")
    expected_ir = _digest_value(ir_dict)
    expected_input_revision = _input_revision(project_id)

    def job():
        tasks.report_progress("正在调用视觉模型复核图纸字段")
        original = DesignIR(**ir_dict)
        try:
            patch = vision.verify_drawing(original, data, name, note=note, attachments=atts)
        except RuntimeError as exc:
            # 若模型多轮修复后仍违反 CAD IR 契约，绝不可让它覆盖原始解析；
            # 原始版本保留，用户可查看失败详情后再次发起校验。
            message = str(exc)
            if not any(marker in message for marker in (
                "已返回结果，但字段未通过本地数据校验",
                "未通过本地字段校验",
                "未通过本地数据校验",
            )):
                raise
            return {
                "ir": original.model_dump(),
                "verification": {
                    "status": "rejected",
                    "message": (
                        "自校验模型在自动修复重试后仍未通过 CAD 几何契约，原始解析结果已完整保留；"
                        "请查看校验详情并在确认后再次发起校验。"
                    ),
                    "detail": message,
                },
            }
        verified, applied, pending = vision.apply_verification_patch(original, patch, auto_only=True)
        _assert_input_unchanged(project_id, expected_input_revision)
        _assert_ir_unchanged(project_id, expected_ir)
        if applied:
            store.save_ir(
                project_id, verified.model_dump(), stage="verified", author=author,
                note=f"字段级 AI 校核自动应用 {len(applied)} 项强证据修改",
            )
        verification_report = {
            "by": author,
            "summary": patch.summary, "sop_version": "drawing-verify-1.0",
            "applied_changes": applied, "pending_changes": pending, "decisions": {},
        }
        store.save_verification_report(project_id, verification_report, author=author)
        store.audit(project_id, "verify_patch", verification_report)
        return {
            "ir": verified.model_dump(),
            "verification": {
                "status": "applied" if applied else ("pending" if pending else "no_change"),
                "message": f"校核完成：自动应用 {len(applied)} 项强证据修改，{len(pending)} 项等待人工确认。",
                "applied_changes": applied, "pending_changes": pending,
                "summary": patch.summary,
            },
        }

    return {"task_id": tasks.submit(
        project_id, "verify", job,
        dedup_key=_task_key("verify", expected_ir, expected_input_revision, note, atts),
    )}


@app.get("/api/projects/{project_id}/verification")
def get_verification(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return store.load_verification_report(project_id) or {
        "applied_changes": [], "pending_changes": [], "decisions": {},
    }


@app.post("/api/projects/{project_id}/verification/decide")
def decide_verification_patch(
    project_id: str, body: VerificationPatchDecision, user: dict = Depends(current_user),
):
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    report = store.load_verification_report(project_id)
    if not report:
        raise HTTPException(404, "尚无待确认的 AI 校核结果")
    pending = next(
        (item for item in report.get("pending_changes", []) if item.get("field") == body.field), None
    )
    if not pending:
        raise HTTPException(404, "该字段没有待确认的校核修改")
    actor = user.get("username", "system")
    report.setdefault("decisions", {})[body.field] = {
        "decision": body.decision, "note": body.note.strip(), "by": actor, "at": _now_str(),
    }
    if body.decision == "confirmed":
        ir_dict = store.load_ir(project_id)
        if not ir_dict:
            raise HTTPException(404, "当前 IR 不存在")
        patch = VerificationPatch.model_validate({"changes": [pending]})
        updated, applied, still_pending = vision.apply_verification_patch(
            DesignIR(**ir_dict), patch, auto_only=False,
        )
        if not applied:
            detail = (still_pending[0].get("rejected_reason") if still_pending else "补丁不可应用")
            raise HTTPException(409, detail)
        updated = vision.confirm_evidence_field(updated, body.field, actor)
        store.save_ir(project_id, updated.model_dump(), stage="verified", author=actor,
                      note=f"人工确认 AI 校核字段 {body.field}")
        report.setdefault("applied_changes", []).extend(applied)
    report["pending_changes"] = [
        item for item in report.get("pending_changes", []) if item.get("field") != body.field
    ]
    store.save_verification_report(project_id, report, author=actor)
    return report


@app.post("/api/projects/{project_id}/model-lookup")
def model_lookup_search(project_id: str, user: dict = Depends(current_user)):
    """联网核验型号候选；结论保存为待确认，人工确认前不写入 IR/BOM。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if LLM_PROVIDER != "qwen":
        raise HTTPException(409, "型号联网核验当前仅在 LLM_PROVIDER=qwen 时可用")
    if not _workflow_project(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(409, "请先完成图纸解析，再进行型号联网核验")
    attachments = store.load_attachments(project_id)
    author = user.get("username", "system")
    expected_ir = _digest_value(ir_dict)

    def job():
        tasks.report_progress("正在调用模型识别候选型号")
        tasks.process_event("tool", "联网核验型号",
                            detail=_tool_detail("model_lookup", "型号联网核验",
                                                status="running"))
        try:
            result = model_lookup.identify_models(DesignIR(**ir_dict), attachments)
        except Exception as exc:
            reason = str(exc)[:120]
            tasks.process_event("tool", f"  ↳ 型号联网核验失败：{reason}",
                                detail=_tool_detail("model_lookup", "型号联网核验",
                                                    status="failed",
                                                    output={"reason": reason}))
            raise
        identifications = list(result.identifications or [])
        counts = {"matched": 0, "ambiguous": 0, "not_found": 0, "not_a_model": 0}
        for item in identifications:
            decision = str(getattr(item, "status", "") or "").strip().lower()
            if decision in counts:
                counts[decision] += 1
        candidate_count = len(identifications)
        tasks.process_event("tool", f"  ↳ 命中 {candidate_count} 个型号候选",
                            detail=_tool_detail("model_lookup", "型号联网核验", status="ok",
                                                output=counts))
        tasks.report_progress("型号候选已返回，正在去重并保存待确认结果")
        _assert_ir_unchanged(project_id, expected_ir)
        payload = result.model_dump()
        payload["confirmations"] = {}
        payload["applied_changes"] = []
        payload["requires_confirmation"] = True
        payload["pending_since"] = _now_str()
        payload["source_ir_hash"] = expected_ir
        store.save_model_lookup(project_id, payload, author=author)
        return payload

    return {"task_id": tasks.submit(
        project_id, "model_lookup", job,
        dedup_key=_task_key("model_lookup", expected_ir, attachments),
    )}


def _apply_model_lookup_result(project_id: str, report: dict, author: str,
                               candidates: Optional[set[str]] = None) -> dict:
    """只把已人工确认的可靠结论写入新 IR 版本；可安全重复调用。"""
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        return report
    confirmed = {
        key.upper() for key, value in (report.get("confirmations") or {}).items()
        if value.get("decision") == "confirmed"
    }
    if candidates:
        confirmed &= {item.upper() for item in candidates}
    filtered = dict(report)
    filtered["identifications"] = [
        item for item in report.get("identifications", [])
        if str(item.get("candidate_model") or "").strip().upper() in confirmed
    ]
    # 产品级推演没有独立人工确认键，不能随型号确认一起自动进入 BOM。
    filtered["proposed_components"] = []
    updated_ir, changes = model_lookup.apply_lookup_results(DesignIR(**ir_dict), filtered)
    existing_changes = report.setdefault("applied_changes", [])
    known = {(item.get("target"), item.get("candidate_model")) for item in existing_changes}
    new_changes = [item for item in changes if (item.get("target"), item.get("candidate_model")) not in known]
    existing_changes.extend(new_changes)
    report["confirmed_sync_attempted_at"] = _now_str()
    if new_changes:
        note = f"人工确认后同步联网型号 {len(new_changes)} 项"
        store.save_ir(project_id, updated_ir.model_dump(), stage="model_lookup_applied", author=author, note=note)
        store.audit(project_id, "apply_model_lookup", {"by": author, "changes": new_changes})
    store.save_model_lookup(project_id, report, author=author)
    return report


@app.get("/api/projects/{project_id}/model-lookup")
def get_model_lookup(project_id: str):
    if not _workflow_project(project_id):
        raise HTTPException(404, "项目不存在")
    return store.load_model_lookup(project_id) or {"identifications": [], "confirmations": {}}


@app.post("/api/projects/{project_id}/model-lookup/apply")
def apply_existing_model_lookup(project_id: str, user: dict = Depends(current_user)):
    """仅同步已人工确认的旧核验结果，不调用模型或联网搜索。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not _workflow_project(project_id):
        raise HTTPException(404, "项目不存在")
    report = store.load_model_lookup(project_id)
    if not report:
        raise HTTPException(404, "尚无型号联网核验结果")
    return _apply_model_lookup_result(project_id, report, user.get("username", "system"))


@app.post("/api/projects/{project_id}/model-lookup/confirm")
def confirm_model_lookup(
    project_id: str, body: ModelLookupConfirmation, user: dict = Depends(current_user),
):
    """记录人工复核；确认后才把该型号同步到 IR/BOM。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    report = store.load_model_lookup(project_id)
    if not report:
        raise HTTPException(404, "尚无型号联网核验结果")
    source_ir_hash = str(report.get("source_ir_hash") or "")
    if source_ir_hash and source_ir_hash != _ir_snapshot(project_id):
        raise HTTPException(409, "IR 已在型号核验后发生变化，请重新执行型号核验再确认")
    available = {
        str(item.get("candidate_model") or "").strip().upper()
        for item in report.get("identifications", [])
    }
    candidate = body.candidate_model.strip()
    if candidate.upper() not in available:
        raise HTTPException(404, "型号不在当前核验结果中")
    report.setdefault("confirmations", {})[candidate] = {
        "decision": body.decision,
        "note": body.note.strip(),
        "by": user.get("username", "system"),
        "at": _now_str(),
    }
    if body.decision == "confirmed":
        report = _apply_model_lookup_result(
            project_id, report, user.get("username", "system"), {candidate}
        )
    store.save_model_lookup(project_id, report, author=user.get("username", "system"))
    store.audit(project_id, "confirm_model_lookup", {
        "candidate_model": candidate, "decision": body.decision, "by": user.get("username", "system"),
    })
    return report


@app.post("/api/projects/{project_id}/decompose")
def decompose_recommend(project_id: str, user: dict = Depends(current_user)):
    """对已解析 IR 做拆解推荐增强(异步任务)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    author = user.get("username", "system")
    expected_ir = _digest_value(ir_dict)

    def job():
        tasks.report_progress("正在调用模型生成零件拆解建议")
        enriched = decompose.enrich_with_recommendations(DesignIR(**ir_dict))
        tasks.report_progress("拆解建议已返回，正在校验零件和几何特征")
        _assert_ir_unchanged(project_id, expected_ir)
        store.save_ir(project_id, enriched.model_dump(), stage="decomposed", author=author)
        # 拆解推荐改写了零件清单（可能连 part_id 都变），解析时算的那份检索报告就对不上了。
        # 原来要用户自己再点一次「重新检索零部件库」才对得上，等于把一个必然的后续
        # 动作丢给用户 —— 这里和 parse 一样顺手跑完。
        payload = enriched.model_dump()
        _refresh_component_match(project_id, payload, kept="拆解建议")
        return payload

    return {"task_id": tasks.submit(
        project_id, "decompose", job, dedup_key=_task_key("decompose", expected_ir)
    )}


@app.post("/api/projects/{project_id}/generate")
def generate(project_id: str, user: dict = Depends(current_user)):
    """据 IR 用 CAD 内核生成各零件几何(STEP/STL) + 校验(异步任务)。

    预检是逐件的：能生成的零件先全部生成，被挡零件就地带着结构化原因返回。
    一个零件缺参数不再让整批 3D 一起失败（C1）。
    """
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    if not geometry.CADQUERY_AVAILABLE:
        raise HTTPException(
            503, "CadQuery 未安装，几何生成不可用。请 `pip install cadquery` 后重试。"
        )
    expected_ir = _digest_value(ir_dict)
    ir = DesignIR(**ir_dict)
    # 确定性预检在提交异步任务之前同步跑完（C7）：被挡零件不进任务，同一份 IR
    # 反复点击也不会再堆出一串一模一样的失败任务。
    processable, blocked = geometry.plan_batch(ir.parts)
    part_ids = [p.part_id for p in processable]
    if not part_ids:
        return {
            # 一个可生成零件都没有 ⇒ 一个成功件都不会有，所以这不是 partial（C3-4）：
            # 不起异步任务、不留任何任务记录，直接把结构化原因交回前端就地显示。
            "task_id": None, "status": "failed", "total": len(ir.parts),
            "processable": 0, "blocked": blocked,
        }

    def job():
        out_dir = store.geometry_dir(project_id)
        results = []
        try:
            for part in DesignIR(**ir_dict).parts:
                if part.part_id in part_ids:
                    results.append(geometry.generate_part(part, out_dir))
        except geometry.GeometryUnavailable as exc:
            # 内核整体不可用属于整批失败（C3-1），不得被吞成「5 个零件各自失败」。
            raise geometry.GeometryUnavailable(
                f"{exc}（{geometry.CADQUERY_UNAVAILABLE_CODE}）") from exc
        if not any(r.ok for r in results):
            raise RuntimeError(
                "CAD 几何生成失败：所有可生成零件都在运行期报错\n- "
                + "\n- ".join(f"{r.part_id}: {r.error}" for r in results)
            )
        _assert_ir_unchanged(project_id, expected_ir)
        payload = _assemble_batch(project_id, ir.parts, results, blocked, _geometry_entry)
        payload["source_ir_hash"] = expected_ir
        # 生成时的输入版本：之后原图 / 附件被替换（input_revision 变了）才整份过期（C3-1）。
        payload["input_revision"] = _input_revision(project_id)
        store.save_geometry_result(project_id, payload)
        store.sync_geometry(project_id)  # 同步到对象存储(Local 后端空操作)
        return payload

    return {
        "task_id": tasks.submit(
            project_id, "generate", job, cad=True,
            dedup_key=_task_key("generate", expected_ir),
        ),
        "status": "queued", "total": len(ir.parts),
        "processable": len(part_ids), "blocked": blocked,
    }


@app.post("/api/projects/{project_id}/drawings")
def drawings(project_id: str, user: dict = Depends(current_user)):
    """据 IR 用 CAD 内核生成各零件 2D 工程图(三视图 SVG + 下料 DXF,异步任务)。

    2D 从 IR 重建实体（不读已保存的 3D 文件），所以与 3D 共用同一套逐件模型、
    同样不要求 3D 全成功（C2）。
    """
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    if not drawing2d.AVAILABLE:
        raise HTTPException(503, "CadQuery 未安装，2D 工程图生成不可用。")
    expected_ir = _digest_value(ir_dict)
    ir = DesignIR(**ir_dict)
    processable, blocked = geometry.plan_batch(ir.parts)
    part_ids = [p.part_id for p in processable]
    if not part_ids:
        return {
            # 2D 与 3D 同形：没有可生成零件时不会出现 partial，整批就地失败。
            "task_id": None, "status": "failed", "total": len(ir.parts),
            "processable": 0, "blocked": blocked,
        }

    def job():
        out_dir = store.geometry_dir(project_id)
        results = [drawing2d.generate_drawings(part, out_dir)
                   for part in DesignIR(**ir_dict).parts if part.part_id in part_ids]
        if not any(r.ok for r in results):
            raise RuntimeError(
                "2D 工程图生成失败：所有可生成零件都在运行期报错\n- "
                + "\n- ".join(f"{r.part_id}: {r.error}" for r in results)
            )
        _assert_ir_unchanged(project_id, expected_ir)
        payload = _assemble_batch(project_id, ir.parts, results, blocked, _drawings_entry)
        payload["source_ir_hash"] = expected_ir
        payload["input_revision"] = _input_revision(project_id)
        store.save_drawings_result(project_id, payload)
        store.sync_geometry(project_id)  # 同步到对象存储(Local 后端空操作)
        return payload

    return {
        "task_id": tasks.submit(
            project_id, "drawings", job, cad=True,
            dedup_key=_task_key("drawings", expected_ir),
        ),
        "status": "queued", "total": len(ir.parts),
        "processable": len(part_ids), "blocked": blocked,
    }


@app.get("/api/projects/{project_id}/bom.csv")
def bom_csv(project_id: str):
    """导出 BOM 为 CSV(UTF-8 BOM，Excel 友好)。"""
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    data = bom.to_csv(DesignIR(**ir_dict))
    store.audit(project_id, "export_bom_csv")
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="BOM_{project_id}.csv"'},
    )


@app.get("/api/projects/{project_id}/bom")
def bom_json(project_id: str):
    """BOM 行(JSON)供前端表格展示。"""
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    return {"rows": bom.build_bom(DesignIR(**ir_dict))}


@app.put("/api/projects/{project_id}/ir")
def update_ir(project_id: str, ir: DesignIR, user: dict = Depends(current_user)):
    """保存人工校核/编辑后的 IR(交互式工作台改参后回存)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    actor = user.get("username", "system")
    ir = vision.mark_human_confirmed(ir, actor)
    store.save_ir(project_id, ir.model_dump(), stage="edited", author=actor,
                  note="人工保存并确认当前关键工程字段")
    return ir.model_dump()


_WORKBENCH_CHAT_SYSTEM = """你是企业 CAD 图纸解析工作台中的工艺助手。
仅依据项目提供的结构化解析结果、当前零件和用户问题回答，不得声称重新查看了原始图纸、联网检索或访问外部资料。
回答使用简洁的中文，优先给出可执行的工艺判断、风险、需要人工确认的尺寸或材料信息；不确定时明确说明不确定。
不要编造标准号、供应商、价格或未识别的尺寸。

当且仅当用户明确要求“修改/改为/设为/调整”当前已选零件，并且给出了具体目标值时，才输出 edit：
- edit.should_apply=true；只可修改 name、quantity、material_spec，以及当前零件已有 feature 的数值字段；
- feature_updates 每项使用 feature_index、field、value；不得增加/删除特征、不得修改 type、不得修改其它零件；
- 用户只是咨询“怎么改”、没有给出明确目标值、没有选零件、或信息不足时，edit 必须为 null 或 should_apply=false，并在 answer 中说明需要什么信息；
- 不能根据常识擅自补全尺寸。
只输出合法 JSON：{\"answer\":\"...\",\"edit\":null 或 {\"should_apply\":true,...}}。"""

_PROJECT_CHAT_SYSTEM = """你是企业 AI 工艺平台的项目助手，服务于同一个项目在需求、图纸解析、技术工艺和报告页面中的连续对话。
仅依据项目已保存的需求、解析结果、已生成计划和当前页面上下文回答；没有资料时明确说明缺失，不得声称已读取原图、联网检索或访问外部资料。
回答使用简洁中文，优先说明当前可执行动作、风险、需要确认的信息和对应流程步骤。
这是跨页面的通用对话：不得直接修改需求表、零件参数、BOM、工艺计划或报告。若用户要修改图纸零件参数，应提示其在 2.1 图纸解析页选中零件后提出明确数值修改。
只输出合法 JSON：{\"answer\":\"...\"}。"""


def _project_chat_messages(project_id: str) -> list[dict]:
    """把持久化消息规整为模型可用的最近上下文，过滤旧/异常字段。"""
    rows = store.load_project_chat(project_id).get("messages", [])
    return [
        {"role": item.get("role") if item.get("role") in {"user", "assistant"} else "user", "content": str(item.get("content", ""))[:1600]}
        for item in rows[-12:]
        if isinstance(item, dict) and str(item.get("content", "")).strip()
    ]


def _save_project_chat_turn(project_id: str, user_message: str, answer: str, user: dict, page_context: str = "") -> None:
    """只追加本次问答，保留同一项目在每个页面之间连续的会话。"""
    messages = store.load_project_chat(project_id).get("messages", [])
    timestamp = now_cst_str()
    # 每一轮都带上项目的业务实例号：报价—技术—财务—报告共用同一份身份，回溯会话时
    # 能认出这轮问答属于哪张业务实例。项目还没有实例号时给空串，绝不现编。
    business_case_id = str((store.load_business_case(project_id) or {}).get("business_case_id") or "")
    messages.extend([
        {"role": "user", "content": user_message.strip(), "at": timestamp, "by": user.get("username", "system"), "page": page_context[:160], "business_case_id": business_case_id},
        {"role": "assistant", "content": answer.strip(), "at": timestamp, "page": page_context[:160], "business_case_id": business_case_id},
    ])
    store.save_project_chat(project_id, messages, author=user.get("username", "system"))

def _apply_workbench_chat_edit(part, edit: WorkbenchPartEdit) -> tuple[list[dict], bool]:
    """对模型建议执行白名单修改，返回变更与是否需要重生几何。

    白名单与校验都在 `services.part_edit` 里 —— 2.1 页的 Agent 走同一份实现，
    两条会话入口不能各有一套规则。
    """
    try:
        changes: list[dict] = []
        geometry_changed = False
        # 老入口也要能提出同样的受控补基体，规则与 2.1 页的 Agent 完全一致。
        base_feature = edit.base_feature if isinstance(edit.base_feature, dict) else None
        if base_feature:
            base_type = base_feature.get("type")
            dimensions = {key: value for key, value in base_feature.items() if key != "type"}
            if part.features:
                changes, geometry_changed = part_edit.replace_base_feature(
                    part, feature_type=base_type, dimensions=dimensions)
            else:
                changes, geometry_changed = part_edit.initialize_base_feature(
                    part, feature_type=base_type, dimensions=dimensions)
        extra_changes, extra_geometry = part_edit.apply_edit(
            part, name=edit.name, quantity=edit.quantity,
            material_spec=edit.material_spec, feature_updates=edit.feature_updates,
        )
        return changes + extra_changes, geometry_changed or extra_geometry
    except part_edit.PartEditError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/api/projects/{project_id}/workbench-chat")
def workbench_chat(
    project_id: str, body: WorkbenchChatRequest, user: dict = Depends(current_user),
):
    """基于已解析 IR 的 2.1 文字问答，不重传图纸，故始终优先走文本模型池。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    meta = store.load_meta(project_id)
    if not meta:
        raise HTTPException(404, "项目不存在")
    saved_ir = store.load_ir(project_id)
    if not saved_ir:
        raise HTTPException(409, "请先完成图纸解析，再向 AI 提问")
    ir = DesignIR(**saved_ir)
    selected = next((part for part in ir.parts if part.part_id == body.part_id), None)
    if body.part_id and not selected:
        raise HTTPException(404, f"零件 {body.part_id} 不存在")

    # 只传可追溯的结构化摘要；避免把原始图片再次发给模型，也控制每次对话的 token。
    context = {
        "project": {
            "project_id": project_id,
            "device_name": ir.device_name,
            "design_intent": ir.design_intent,
            "overall_dims": ir.overall_dims,
            "assembly_notes": ir.assembly_notes,
        },
        "current_part": selected.model_dump() if selected else None,
        "parts": [
            {
                "part_id": part.part_id,
                "name": part.name,
                "material": part.material.spec if part.material else "",
                "quantity": part.quantity,
                "features": [feature.type for feature in part.features],
                "confidence": part.confidence,
            }
            for part in ir.parts[:80]
        ],
        "open_questions": [question.model_dump() for question in ir.open_questions[:20]],
    }
    request_history = [
        {"role": turn.role if turn.role in {"user", "assistant"} else "user", "content": turn.content}
        for turn in body.history
        if turn.content.strip()
    ]
    # 新悬浮对话框不再依赖页面内存，会从项目级留痕恢复上下文；兼容旧工作台传来的 history。
    history = request_history or _project_chat_messages(project_id)
    prompt = json.dumps(
        {"project_context": context, "recent_conversation": history, "user_question": body.message},
        ensure_ascii=False,
        default=str,
    )
    if len(prompt) > 32000:
        prompt = prompt[:32000] + "\n【上下文按预算截断】"
    try:
        tasks.report_progress("正在调用项目上下文问答模型")
        result = llm_client.complete_to_model(
            _WORKBENCH_CHAT_SYSTEM, prompt, WorkbenchChatAnswer, max_tokens=1200,
        )
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    answer = result.answer.strip()
    if not answer:
        raise HTTPException(502, "AI 未返回有效对话内容")
    model = llm_client.last_used_model() or active_text_model()
    edit_applied = None
    # 已导入的精确 STEP/STP 实体不允许通过文本修改 IR 特征，以免与真实实体脱节。
    blocked = part_edit.blocks_feature_edit(meta)
    if result.edit and result.edit.should_apply:
        if not selected:
            answer += "\n\n未选择零件，未应用参数修改。"
        elif blocked:
            answer += f"\n\n{blocked}"
        else:
            changes, geometry_changed = _apply_workbench_chat_edit(selected, result.edit)
            if changes:
                note = f"AI 对话修改 {selected.part_id}：" + "、".join(change["field"] for change in changes)
                store.save_ir(project_id, ir.model_dump(), stage="ai_chat_edited", author=user.get("username", "system"), note=note)
                edit_applied = {
                    "part_id": selected.part_id,
                    "changes": changes,
                    "requires_regeneration": geometry_changed,
                    "explanation": result.edit.explanation,
                }
                store.audit(project_id, "workbench_chat_edit", {
                    "by": user.get("username", "system"), "part_id": selected.part_id,
                    "changes": changes, "model": model,
                })
                answer += "\n\n已应用到当前零件，并已创建可回溯版本。"
            else:
                answer += "\n\n未发现需要变更的值，当前参数保持不变。"
    store.audit(project_id, "workbench_chat", {
        "by": user.get("username", "system"),
        "part_id": body.part_id,
        "model": model,
        "question_length": len(body.message),
    })
    # 无上下文时用中性空值，不冒充任何具体步骤（第 19 步：九阶段各有独立 page_context）。
    _save_project_chat_turn(project_id, body.message, answer, user, body.page_context or "")
    return {"answer": answer, "model": model, "edit_applied": edit_applied}


# --------------------------------------------------------------------------- #
# 2.1 图纸解析 Agent（open-claude）
#
# 与上面的 ai-chat 不同：那是单轮文本问答，这里是带工具循环的 Agent，
# 直接跑 open-claude 的 Conversation，前端按 SSE 逐块渲染。
# --------------------------------------------------------------------------- #
class AgentSendRequest(BaseModel):
    message: str = ""
    page_context: str = ""


class AgentEventBody(BaseModel):
    """项目会话时间线的一条条目（任务卡 / 过程文字 / tech_ui 卡 / shell note）。

    与 store.append_session_event 的条目形状一致：kind 必填，其余可选；带 key 的
    条目幂等且就地更新（同一张卡重复提交不会多出一行）。
    """
    kind: str = ""
    source: str = ""
    stage: str = ""
    text: str = ""
    task: Optional[Dict[str, Any]] = None
    ui: Optional[Dict[str, Any]] = None
    key: str = ""
    ts: str = ""


def _agent_project(project_id: str) -> None:
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")


@app.get("/api/projects/{project_id}/agent/meta")
def agent_meta(project_id: str, user: dict = Depends(current_user)):
    """左侧对话设置工具栏所需的模型、工具、技能与会话信息。"""
    _agent_project(project_id)
    ok, reason = oc_agent.available()
    if not ok:
        # 不抛 500：页面需要显示「为什么用不了」，而不是一个红色报错。
        return {"available": False, "reason": reason}
    # 报价侧可能刚换过模型 / 网关 / Key：先把当前路由推给已建会话，页面上的模型名
    # 与实际请求才会一致（技术工艺与报价是两个进程，收不到对方的保存回调）。
    # 按**发起账号**推：账号级模型/Key 与全局不同，换人说话就得换路由。
    llm_settings.sync_live_agents(user=user.get("username", ""))
    try:
        meta = oc_agent.get_agent(project_id).meta()
    except oc_agent.AgentUnavailable as exc:
        return {"available": False, "reason": str(exc)}
    return {"available": True, "reason": "", **meta}


@app.get("/api/projects/{project_id}/agent/history")
def agent_history(project_id: str, user: dict = Depends(current_user)):
    """只读回放：该项目已持久化的完整 Agent 会话（用户 / 助手 / 工具轨迹）。

    与左侧会话共用同一条 open-claude 会话事实源：打开历史项目时前端先取这份历史，
    再进入正常会话状态。只读，不创建新会话，也不触发 /agent/new。
    """
    _agent_project(project_id)
    # 项目会话时间线是本地持久化的那一半（任务卡 / 过程文字 / tech_ui 卡），与 Agent 层
    # 是否可用无关：会话层连不上时也必须回放出来，否则重进项目又只剩空白会话。
    timeline = store.load_session_events(project_id)
    try:
        data = oc_agent.load_history(project_id)
    except oc_agent.AgentUnavailable as exc:
        # 与 /agent/meta 一致：会话层不可用时不抛 500，让页面显示真实原因而不是空白会话。
        return {"available": False, "reason": str(exc), "project_id": project_id,
                "session_id": "", "messages": [], "message_count": 0, "timeline": timeline}
    data = dict(data or {})
    data.setdefault("project_id", project_id)
    data["timeline"] = timeline
    return data


@app.post("/api/projects/{project_id}/agent/event")
def agent_event(project_id: str, body: AgentEventBody, user: dict = Depends(current_user)):
    """追加一条会话时间线条目（任务卡 / 过程文字 / tech_ui 卡 / shell note）。

    会话内容属于项目数据，不是业务产出：seq 由服务端分配（等于追加顺序），带 key 的条目
    幂等且就地更新 —— 前端进度轮询只提交新出现的进度行，不会每次追加整段。

    权限也按这个定位来：不是"谁能改工艺参数"，而是"谁在这个项目里能动手"
    （见 auth.SESSION_WRITE_ROLES）。2.3 的操作者是财务经理，他写自己那几条过程
    文字曾经被这里的 WRITE_ROLES 拦下 —— 业务动作成功、时间线却丢了，前端还会先
    弹一次伪 403。Agent 对话（/agent/send、/agent/new）不在放宽之列。
    """
    _require(user, auth.SESSION_WRITE_ROLES, "需要工程师及以上权限")
    _agent_project(project_id)
    event = body.model_dump()
    if not str(event.get("kind") or "").strip():
        raise HTTPException(400, "会话事件缺少 kind")
    stored = store.append_session_event(project_id, event)
    return {"seq": stored.get("seq"), "event": stored}


@app.get("/api/projects/{project_id}/agent/events")
def agent_events(project_id: str, stage: str = "", kinds: str = "", source: str = "",
                 user: dict = Depends(current_user)):
    """按 seq 升序回放项目会话时间线，可按阶段 / 类型 / 来源过滤（只读）。

    阶段页加载完成后用它取回自己那一份过程文字（stage + source=board），父壳取全量。
    """
    _agent_project(project_id)
    rows = store.load_session_events(project_id)
    if stage:
        rows = [row for row in rows if str(row.get("stage") or "") == stage]
    if source:
        rows = [row for row in rows if str(row.get("source") or "") == source]
    wanted = [item.strip() for item in str(kinds or "").split(",") if item.strip()]
    if wanted:
        rows = [row for row in rows if str(row.get("kind") or "") in wanted]
    return {"events": rows, "count": len(rows)}


@app.post("/api/projects/{project_id}/agent/send")
def agent_send(project_id: str, body: AgentSendRequest, request: Request,
               user: dict = Depends(current_user)):
    """一轮 Agent 对话，SSE 流式返回文本、工具调用与工具结果。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    _agent_project(project_id)
    message = (body.message or "").strip()
    if not message:
        raise HTTPException(400, "消息不能为空")
    ok, reason = oc_agent.available()
    if not ok:
        raise HTTPException(503, reason)
    # 报价保存设置后技术工艺不重启也要生效：比对当前路由，变了就重建 client，
    # 否则这一轮还会把新模型发给旧厂商。账号级模型/Key 也在这里换人换路由。
    llm_settings.sync_live_agents(user=user.get("username", ""))
    if body.page_context.strip():
        message = f"{message}\n\n[当前页面：{body.page_context.strip()[:160]}]"
    return StreamingResponse(
        # 带上操作人：Agent 能改零件参数，审计必须记「是谁让它改的」。
        # 同时带上用户令牌：SendIntegrationToFinance 要代表用户去调一体化服务。
        oc_agent.stream_sse(project_id, message, actor=user.get("username", "system"),
                            token=_sso_token(request)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/projects/{project_id}/agent/new")
def agent_restart_task(project_id: str, user: dict = Depends(current_user)):
    """本次任务从头开始：清空对话上下文，并把 2.1 图纸解析的产出退回起点。

    保留输入（原图、技术文档、需求单）与版本快照 —— 「从头开始」是拿同一份图纸
    重新解析，不是把图纸删掉，也不该抹掉可追溯的历史。

    Agent 不可用时**仍然重置业务产出**：模型连不上不该妨碍用户把任务重来一遍。
    """
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    _agent_project(project_id)
    result = store.reset_parse_stage(project_id, author=user.get("username", "system"))
    ok, _reason = oc_agent.available()
    if ok:
        try:
            oc_agent.get_agent(project_id).reset()
        except oc_agent.AgentUnavailable:
            pass
    return {"ok": True, **result}


# 2.1 页小窗的模型设置就是全局模型设置 —— 这两条只是同一份配置的项目级别名，
# 让前端不用为「在哪个页面」分叉出两套请求。真正的读写全在 llm_settings 里。
@app.get("/api/projects/{project_id}/agent/settings")
def agent_settings(project_id: str, user: dict = Depends(current_user)):
    _agent_project(project_id)
    return get_runtime_llm_settings(user)


@app.put("/api/projects/{project_id}/agent/settings")
def update_agent_settings(project_id: str, body: LlmSettingsBody,
                          user: dict = Depends(current_user)):
    _agent_project(project_id)
    return update_runtime_llm_settings(body, user)


@app.get("/api/projects/{project_id}/ai-chat")
def get_project_chat(project_id: str, user: dict = Depends(current_user)):
    """返回项目级共享对话；所有流程页面读取同一记录。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return store.load_project_chat(project_id)


@app.post("/api/projects/{project_id}/ai-chat")
def project_chat(
    project_id: str, body: ProjectChatRequest, user: dict = Depends(current_user),
):
    """跨页面项目问答。仅走文本模型，且不对业务数据自动写入。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    meta = store.load_meta(project_id)
    if not meta:
        raise HTTPException(404, "项目不存在")
    ir = store.load_ir(project_id) or {}
    requirement = store.load_requirement(project_id) or {}
    report = store.load_process_report(project_id) or {}
    summary_doc = store.load_summary(project_id) or {}
    requirement_data = requirement.get("data", {}) if isinstance(requirement, dict) else {}
    context = {
        "project": {
            "project_id": project_id,
            "name": meta.get("project_name") or meta.get("source_filename") or project_id,
            "note": meta.get("note", ""),
            "attachments": meta.get("attachments", [])[:20],
        },
        "requirement": {
            "requirement_no": requirement.get("requirement_no", ""),
            "status": requirement.get("status", ""),
            "customer": requirement_data.get("final_customer_name") or requirement_data.get("customer_project", ""),
            "product": requirement_data.get("product_name") or requirement_data.get("product_model", ""),
            "industry": requirement_data.get("industry", ""),
        },
        "drawing_parse": {
            "device_name": ir.get("device_name", ""),
            "design_intent": ir.get("design_intent", ""),
            "parts": [
                {"part_id": part.get("part_id"), "name": part.get("name"), "quantity": part.get("quantity")}
                for part in (ir.get("parts", []) if isinstance(ir, dict) else [])[:80]
            ],
            "open_questions": (ir.get("open_questions", []) if isinstance(ir, dict) else [])[:20],
        },
        "process_summary": {"status": summary_doc.get("status", ""), "title": summary_doc.get("title", "")},
        "report": {"report_no": report.get("report_no", ""), "status": report.get("status", "")},
        "current_page": body.page_context or "项目工作流页面",
    }
    prompt = json.dumps(
        {"project_context": context, "recent_conversation": _project_chat_messages(project_id), "user_question": body.message},
        ensure_ascii=False,
        default=str,
    )
    if len(prompt) > 32000:
        prompt = prompt[:32000] + "\n【上下文按预算截断】"
    try:
        result = llm_client.complete_to_model(_PROJECT_CHAT_SYSTEM, prompt, ProjectChatAnswer, max_tokens=1200)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    answer = result.answer.strip()
    if not answer:
        raise HTTPException(502, "AI 未返回有效对话内容")
    model = llm_client.last_used_model() or active_text_model()
    _save_project_chat_turn(project_id, body.message, answer, user, body.page_context)
    store.audit(project_id, "project_chat", {
        "by": user.get("username", "system"), "page": body.page_context, "model": model,
        "question_length": len(body.message),
    })
    return {"answer": answer, "model": model}


@app.post("/api/projects/{project_id}/parts/{part_id}/regenerate")
def regenerate_part(project_id: str, part_id: str, user: dict = Depends(current_user)):
    """改参后单零件重生几何 + 2D 工程图(行内编辑闭环)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    if not geometry.CADQUERY_AVAILABLE:
        raise HTTPException(503, "CadQuery 未安装。")
    ir = DesignIR(**ir_dict)
    part = next((p for p in ir.parts if p.part_id == part_id), None)
    if not part:
        raise HTTPException(404, f"零件 {part_id} 不存在")
    issues = geometry.preflight_parts([part])
    if issues:
        raise HTTPException(409, "单零件几何预检未通过：\n- " + "\n- ".join(dict.fromkeys(issues)))

    out_dir = store.geometry_dir(project_id)
    g = geometry.generate_part(part, out_dir)
    gp = store.load_geometry_result(project_id) or {"parts": []}
    g_entry = _geometry_payload(project_id, [g])["parts"][0]
    # 只刷新这一个零件的来源指纹：其他零件的指纹 / URL / 数值一字不动，
    # 顶层 source_ir_hash 也保持原值 —— 逐件指纹才是判据（C6）。
    part_versions.stamp_entry(g_entry, part)
    _upsert_part(gp, g_entry)
    store.save_geometry_result(project_id, gp)

    d = drawing2d.generate_drawings(part, out_dir)
    dp = store.load_drawings_result(project_id) or {"parts": []}
    d_entry = _drawings_payload(project_id, [d])["parts"][0]
    part_versions.stamp_entry(d_entry, part)
    _upsert_part(dp, d_entry)
    store.save_drawings_result(project_id, dp)

    store.sync_geometry(project_id)  # 同步到对象存储(Local 后端空操作)
    store.audit(project_id, "regenerate_part", {"part_id": part_id})
    return {"geometry": g_entry, "drawings": d_entry}


# --------------------------------------------------------------------------- #
# 工艺拆解(把单个零件拆成结构化工艺路线,CAPP)
# --------------------------------------------------------------------------- #
def _geom_for_part(project_id: str, part_id: str):
    """取单个零件的几何属性（工艺 / 成本的口子）。

    判据是**逐件形状指纹**，不是整份 IR 哈希：别的零件被改过不该让这个零件取不到；
    本零件自己的尺寸变了才返回 None（沿用既有语义，让上游提示先重生成）。
    """
    gp = store.load_geometry_result(project_id) or {}
    entry = next((p for p in gp.get("parts", []) if p.get("part_id") == part_id), None)
    if entry is None:
        return None
    stored_hash = str(entry.get("source_part_hash") or "")
    if stored_hash:
        ir_dict = store.load_ir(project_id) or {}
        part = next((p for p in ir_dict.get("parts") or []
                     if str(p.get("part_id")) == part_id), None)
        if part is None or part_versions.part_fingerprint(part) != stored_hash:
            return None
    elif (store.load_meta(project_id) or {}).get("derived_results_stale"):
        # 老结果没有逐件指纹：只剩「输入被替换 / 解析被重置」这一条底线判断（C7）。
        return None
    return {"bbox": entry.get("bbox"), "volume_mm3": entry.get("volume_mm3"),
            "mass_g": entry.get("mass_g")}


async def _read_attachments(attachments: List[UploadFile]):
    out = []
    for att in attachments or []:
        data = await _read_upload_limited(att, label="补充文件")
        if data:
            out.append((att.filename or "attachment", data))
    return out


@app.post("/api/projects/{project_id}/parts/{part_id}/process")
async def generate_process(
    project_id: str, part_id: str,
    note: str = Form(""),
    attachments: List[UploadFile] = File(default=[]),
    user: dict = Depends(current_user),
):
    """把某零件拆解成结构化工艺路线(调用 Claude,异步任务)。可附加说明/文件辅助。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    ir = DesignIR(**ir_dict)
    part = next((p for p in ir.parts if p.part_id == part_id), None)
    if not part:
        raise HTTPException(404, f"零件 {part_id} 不存在")
    geom = _geom_for_part(project_id, part_id)
    author = user.get("username", "system")
    atts = await _read_attachments(attachments)
    expected_ir = _digest_value(ir_dict)

    def job():
        # 先查企业工艺库，再让模型排产。库里已有的路线/工序/标准工时是硬依据，
        # 检索失败不能挡住推荐 —— 退回原来的通用工艺口径即可。
        lookup = _process_lookup_for(project_id, part_id, part.model_dump())
        # 只要模型产出工序明细（6 字段），其余本地补齐。原来要的是 13 字段的完整工序，
        # 十来道就是上千个输出 token —— 这一步的输入才 ~5.7k 字符，慢的一直是输出。
        tasks.report_progress("模型编制工序明细（只要工序号/名称/类型/设备/工时）")
        plan, coverage = process.outline_process(
            part, overall=ir, geom=geom, note=note, attachments=atts,
            library=process_lookup.as_prompt(lookup) if lookup else "",
            lookup=lookup,
        )
        tasks.report_progress(
            f"  ↳ 共 {coverage['summary']['total']} 道工序："
            f"沿用库内 {coverage['summary']['reused']} 道、"
            f"缺失需新建 {coverage['summary']['missing']} 道")
        _assert_ir_unchanged(project_id, expected_ir)
        plan_dict = plan.model_dump()
        store.save_process(project_id, part_id, plan_dict, author=author)
        store.save_process_coverage(project_id, part_id, coverage)
        return {"plan": plan_dict, "validation": process.compute(plan_dict),
                "library": lookup, "coverage": coverage}

    return {"task_id": tasks.submit(
        project_id, "process", job,
        dedup_key=_task_key("process", part_id, expected_ir, geom, note, atts),
    )}


@app.get("/api/projects/{project_id}/parts/{part_id}/process")
def get_process(project_id: str, part_id: str):
    """读取某零件已保存的工艺路线 + 确定性派生量(工时合计/依赖校验)。"""
    plan = store.load_process(project_id, part_id)
    if plan and not (plan.get("steps") or []):
        ir_dict = store.load_ir(project_id)
        part = next(
            (item for item in DesignIR(**ir_dict).parts if item.part_id == part_id), None
        ) if ir_dict else None
        if part:
            repaired = process.ensure_minimum_route(ProcessPlan.model_validate(plan), part)
            repaired.rule_warnings = process.validate_rules(repaired.model_dump(), part)
            plan = repaired.model_dump()
            store.save_process(project_id, part_id, plan, author="system_route_repair")
    return {"plan": plan, "validation": process.compute(plan) if plan else None,
            "coverage": store.load_process_coverage(project_id, part_id)}


@app.put("/api/projects/{project_id}/parts/{part_id}/process")
def update_process(project_id: str, part_id: str, plan: ProcessPlan,
                   user: dict = Depends(current_user)):
    """保存人工编辑后的工艺路线。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    plan.part_id = part_id
    plan.steps.sort(key=lambda s: s.step_no)
    ir_dict = store.load_ir(project_id)
    part = next((item for item in DesignIR(**ir_dict).parts if item.part_id == part_id), None) if ir_dict else None
    plan.rule_warnings = process.validate_rules(plan.model_dump(), part)
    plan_dict = plan.model_dump()
    store.save_process(project_id, part_id, plan_dict, author=user.get("username", "system"))
    return {"plan": plan_dict, "validation": process.compute(plan_dict)}


# --------------------------------------------------------------------------- #
# 成本分析(对单个零件做成本拆解,允许联网检索行情)
# --------------------------------------------------------------------------- #
@app.post("/api/projects/{project_id}/parts/{part_id}/cost")
async def generate_cost(
    project_id: str, part_id: str, quantity: int = 1,
    note: str = Form(""),
    attachments: List[UploadFile] = File(default=[]),
    user: dict = Depends(current_user),
):
    """对某零件做专业成本分析(异步任务)。quantity 为核算批量;可附加说明/文件。

    2.1 的界面上已经没有这一步 —— 零件成本归财务经理在 2.3 算（cost_review）。
    """
    _require(user, auth.COST_ROLES, "成本测算在 2.3，由财务经理负责")
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    ir = DesignIR(**ir_dict)
    part = next((p for p in ir.parts if p.part_id == part_id), None)
    if not part:
        raise HTTPException(404, f"零件 {part_id} 不存在")
    geom = _geom_for_part(project_id, part_id)
    author = user.get("username", "system")
    qty = max(1, int(quantity or 1))
    atts = await _read_attachments(attachments)
    expected_ir = _digest_value(ir_dict)

    def job():
        # 先把企业库里的物料价与费率钉死，模型只在库内缺口上联网估算。
        lookup = _cost_lookup_for(project_id, part_id, part.model_dump(), qty)
        # 联网只用来补库内缺的那几项。库里主材有价、又没留下缺口时就别开 ——
        # 每次 web_search 都是一次完整往返（上限 5 次），是这一步最大的耗时来源。
        use_web = cost.needs_web_search(lookup)
        tasks.report_progress(
            "库内依据有缺口，启用联网检索补行情价（较慢）" if use_web
            else "库内价格与费率齐全，跳过联网检索，直接按库内依据测算")
        tasks.report_progress("正在调用模型生成零件成本拆解")
        analysis = cost.analyze_cost(part, overall=ir, geom=geom, quantity=qty,
                                     web=use_web, note=note, attachments=atts,
                                     library=cost_lookup.as_prompt(lookup) if lookup else "")
        tasks.report_progress("成本结果已返回，正在按企业成本口径重算并保存价格依据")
        _assert_ir_unchanged(project_id, expected_ir)
        # 材料以外的三项一律由材料成本按固定系数推导，见 services/cost_model.py。
        # 零件侧和整机侧必须同一套口径，否则报价拿到的 material_unit_price 对不上。
        a_dict = cost_model.normalize(analysis.model_dump())
        breakdown = a_dict["cost_model"]
        tasks.report_progress(
            f"  ↳ 材料 {breakdown['material']:.2f} · 人工 {breakdown['labor']:.2f}"
            f" · 制费 {breakdown['overhead']:.2f} · 加工 {breakdown['machining']:.2f}"
            f" = {breakdown['total']:.2f} 元")
        store.save_cost(project_id, part_id, a_dict, author=author)
        return {"analysis": a_dict, "summary": cost.compute(a_dict), "library": lookup}

    return {"task_id": tasks.submit(
        project_id, "cost", job,
        dedup_key=_task_key("cost", part_id, qty, expected_ir, geom, note, atts),
    )}


@app.get("/api/projects/{project_id}/parts/{part_id}/cost")
def get_cost(project_id: str, part_id: str):
    """读取某零件已保存的成本分析 + 确定性重算(金额/合计/分类汇总)。"""
    analysis = store.load_cost(project_id, part_id)
    return {"analysis": analysis, "summary": cost.compute(analysis) if analysis else None}


@app.put("/api/projects/{project_id}/parts/{part_id}/cost")
def update_cost(project_id: str, part_id: str, analysis: CostAnalysis,
                user: dict = Depends(current_user)):
    """保存人工编辑后的成本分析。

    改成本的数字是财务的事（2.3）。生成走 COST_ROLES 而手改留在 WRITE_ROLES，
    等于绕开拆分：工艺工程师照样能把成本改成任意值。
    """
    _require(user, auth.COST_ROLES, "成本测算在 2.3，由财务经理负责")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    analysis.part_id = part_id
    # 人工改了材料明细，三项派生成本要跟着重算 —— 否则保存一次，合计就和口径脱钩了。
    a_dict = cost_model.normalize(analysis.model_dump())
    store.save_cost(project_id, part_id, a_dict, author=user.get("username", "system"))
    return {"analysis": a_dict, "summary": cost.compute(a_dict)}


# --------------------------------------------------------------------------- #
# 组装与整合(CPQ 技术工艺 2.2)
#
# 2.1 把图纸拆成零件、逐件出工艺与成本；2.2 把零件装回整机。四个环节各有一个入口，
# 顺序是硬的：整合图纸 → 参数推荐 → 组装工艺 → 成本测算。后一环节都拿前一环节的
# 结论当输入(见 services/integration.py 的 _params_prompt / _process_prompt)，
# 所以下面每个 POST 都会先校验前置结果存在 —— 缺了不是少一段上下文，是直接算错。
#
# 工艺与成本沿用 ProcessPlan / CostAnalysis，整机在库里的"零件编号"固定为 ASSY，
# 因此 store 的按零件存取、process.compute / cost.compute 的重算全部直接复用。
# --------------------------------------------------------------------------- #
class IntegrationSettings(BaseModel):
    """2.2 的两个手填输入：整合需求与核算批量。"""
    requirement_note: str = ""
    quantity: int = 1


def _integration_ir(project_id: str) -> DesignIR:
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(400, "请先完成 2.1 图纸解析，2.2 需要已确认的零件清单")
    return DesignIR(**ir_dict)


def _integration_payload(project_id: str, plan) -> dict:
    return integration.payload(project_id, plan)


@app.get("/api/projects/{project_id}/integration")
def get_integration(project_id: str):
    """读取 2.2 的全部结果：整合图纸、整机参数、组装工艺、整机成本 + 库内依据。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return _integration_payload(project_id, integration.load_plan(project_id))


@app.put("/api/projects/{project_id}/integration")
def update_integration_settings(project_id: str, body: IntegrationSettings,
                                user: dict = Depends(current_user)):
    """保存整合需求与核算批量。参数推荐会把 requirement_note 作为首要输入。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    plan = integration.load_plan(project_id)
    plan.requirement_note = body.requirement_note
    plan.quantity = max(1, int(body.quantity or 1))
    integration.save_plan(project_id, plan, user.get("username", "system"))
    return _integration_payload(project_id, plan)


@app.post("/api/projects/{project_id}/integration/drawings")
async def upload_integration_drawings(
    project_id: str, files: List[UploadFile] = File(...), note: str = Form(""),
    user: dict = Depends(current_user),
):
    """上传整合图纸(装配图/爆炸图/接线图)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    author = user.get("username", "system")
    plan = integration.load_plan(project_id)
    added: List[str] = []
    for item in files or []:
        data = await _read_upload_limited(item, label="整合图纸")
        if not data:
            continue
        name = store.save_integration_drawing(
            project_id, item.filename or "integration.png", data, author)
        # 同名重传视为替换：文件本体已经覆盖，台账里不该留两行指向同一个文件。
        plan.drawings = [d for d in plan.drawings if d.filename != name]
        plan.drawings.append(IntegrationDrawing(
            filename=name, uploaded_at=now_cst_str(), note=note))
        added.append(name)
    if not added:
        raise HTTPException(400, "未收到有效的整合图纸")
    integration.save_plan(project_id, plan, author)
    return _integration_payload(project_id, plan)


@app.delete("/api/projects/{project_id}/integration/drawings/{filename}")
def delete_integration_drawing(project_id: str, filename: str,
                               user: dict = Depends(current_user)):
    """从台账里移除一张整合图纸。文件本体保留，便于事后追溯这次推荐看过什么。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    plan = integration.load_plan(project_id)
    remaining = [d for d in plan.drawings if d.filename != filename]
    if len(remaining) == len(plan.drawings):
        raise HTTPException(404, "该整合图纸不存在")
    plan.drawings = remaining
    integration.save_plan(project_id, plan, user.get("username", "system"))
    return _integration_payload(project_id, plan)


@app.get("/api/projects/{project_id}/integration/drawings/{filename}")
def get_integration_drawing(project_id: str, filename: str):
    path = store.integration_drawing_file(project_id, filename)
    if not path or not path.exists():
        raise HTTPException(404, "整合图纸不存在")
    return FileResponse(str(path), filename=path.name)


@app.post("/api/projects/{project_id}/integration/params")
async def generate_integration_params(
    project_id: str, note: str = Form(""), attachments: List[UploadFile] = File(default=[]),
    user: dict = Depends(current_user),
):
    """参数推荐：用户需求 + 整合图纸 + 2.1 已确认零件 → 整机参数/连接/BOM(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir = _integration_ir(project_id)
    author = user.get("username", "system")
    atts = await _read_attachments(attachments)
    if note.strip():
        plan = integration.load_plan(project_id)
        plan.requirement_note = note.strip()
        integration.save_plan(project_id, plan, author)

    def job():
        plan = integration.load_plan(project_id)
        result = integration.recommend_params(
            project_id, ir, plan, attachments=atts, progress=tasks.report_progress)
        plan.params = result
        # 参数变了，下游的工艺与成本就不再是依据当前参数算的。这里不静默保留旧值，
        # 由前端提示重新生成 —— 留着会让人以为整机成本已经跟着新参数更新过了。
        plan.confirmed = False
        # 「确认参数已齐」也是针对当时那一版参数按下的：整份重推之后必须重新确认，
        # 否则新参数带着旧确认发到财务，缺项没人看得见。
        plan.params_final = False
        plan.params_final_by = None
        plan.params_final_at = None
        integration.save_plan(project_id, plan, author)
        return _integration_payload(project_id, plan)

    return {"task_id": tasks.submit(project_id, "integration_params", job)}


@app.put("/api/projects/{project_id}/integration/params")
def update_integration_params(project_id: str, params: IntegrationParamPlan,
                              user: dict = Depends(current_user)):
    """保存人工编辑后的整机参数。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    # 手工加的 BOM 行同样要对一遍 2.1 的零件清单：编号对不上就标"编号待核对"，
    # 否则一行凭空写出来的零件在下游看起来和解析出来的零件毫无区别。
    integration.reconcile_part_refs(params, DesignIR(**ir_dict) if ir_dict else None)
    # 人工改过的参数同样要对回报价字典：改了名字就得重新认 param_code，
    # 否则这条参数在报价那头会突然失去落点，而界面上看不出任何异常。
    params.product_family = product_params.align(params, integration.project_family(project_id))
    plan = integration.load_plan(project_id)
    plan.params = params
    # 参数改了，之前那两次确认都不再作数 —— 确认针对的是当时那一版，不是这个项目。
    plan.params_confirmed = False
    plan.params_confirmed_by = None
    plan.params_confirmed_at = None
    plan.params_final = False
    plan.params_final_by = None
    plan.params_final_at = None
    integration.save_plan(project_id, plan, user.get("username", "system"))
    return _integration_payload(project_id, plan)


@app.post("/api/projects/{project_id}/integration/params/confirm")
def confirm_integration_params(project_id: str, user: dict = Depends(current_user)):
    """确认「参数推荐」这一环节：整机参数、连接关系与 BOM 由人核对过了。

    与「确认参数已齐」是两件事：那个是交给报价前的最后收口（报价必填要齐），
    这个只是本环节定稿 —— 缺口在这里不拦，但要如实说出来。
    """
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    # 实现唯一：与 Agent 的 ConfirmIntegrationParams 走同一份 services.integration。
    plan = _integration_flow(integration.confirm_params, project_id, user)
    return _integration_payload(project_id, plan)


@app.post("/api/projects/{project_id}/integration/process/confirm")
def confirm_integration_process(project_id: str, user: dict = Depends(current_user)):
    """确认「组装工艺」。这是 2.2 的闸门：确认之后才允许把任务推给财务经理算成本。

    不校验工序条数 —— 有的整机确实只有两三道；但必须**跑过**，空的工艺没什么可确认。
    """
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    # 实现唯一：与 Agent 的 ConfirmIntegrationProcess 走同一份 services.integration。
    plan = _integration_flow(integration.confirm_process, project_id, user)
    return _integration_payload(project_id, plan)


@app.post("/api/projects/{project_id}/integration/params/autofill")
async def autofill_integration_params(
    project_id: str, note: str = Form(""), user: dict = Depends(current_user),
):
    """参数推荐 · 智能补全：只给还缺的字段出**建议值**，不落库。

    这是第 3 阶段「组装与整合 · 3.2 参数推荐」里的能力：整机参数、连接关系与 BOM 在这一步
    定稿，报价必填项自然也在这里补齐，所以沿用技术工艺写权限。

    不直接写入是有意的：补全里必然混着"靠常识凑的"，直接写进去，报价那头就分不清
    哪些是算出来的、哪些是猜的。建议回到界面上由工艺经理逐项过目再保存。
    """
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir = _integration_ir(project_id)
    if integration.load_plan(project_id).params is None:
        raise HTTPException(400, "请先完成参数推荐：智能补全只补它没给出的那些字段")

    def job():
        plan = integration.load_plan(project_id)
        result = integration.autofill_params(
            project_id, ir, plan, note=note, progress=tasks.report_progress)
        return {"fills": [item.model_dump() for item in result.fills],
                "unresolved": list(result.unresolved)}

    return {"task_id": tasks.submit(project_id, "integration_params_autofill", job)}


class IntegrationFinalizeBody(BaseModel):
    """「参数推荐」环节：人工补填的值 + 是否就此确认。

    values 按**字段编码**给：{"rated_voltage": {"value": "14.4", "unit": "V"}}。
    confirm=False 只保存（补一半先存着），True 才校验必填齐不齐并落确认。
    """
    values: Dict[str, Any] = Field(default_factory=dict)
    confirm: bool = False
    # 可选：参数推荐页的「仍要继续」签字。confirm=False 且带 waiver 时，把缺口与签字
    # 一起落库（params_final 仍然保持 False —— 签的是"可以带缺口往下走"，不是"参数已齐"）。
    waiver: Optional[dict] = None


@app.post("/api/projects/{project_id}/integration/params/finalize")
def finalize_integration_params(project_id: str, body: IntegrationFinalizeBody,
                                user: dict = Depends(current_user)):
    """参数推荐：把人工补填的值合进整机参数，必填齐了才允许最终确认。

    这是第 3 阶段「组装与整合 · 3.2 参数推荐」与报价之间的验收口径 —— 报价测算单按 DA
    字段取数，必填项缺一格，那边就是一格空白，而且要等销售回头来问才发现。宁可在这里
    挡住，也不把补齐的活儿推给后面的成本步骤。
    """
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    plan = integration.load_plan(project_id)
    integration.finalize_params(plan, body.values)
    if body.confirm:
        # 必填补齐门禁按行业锁定的族校验：包装项目要的是盒型/内尺寸这 10 项，
        # 不是电池的「工作温度」。
        missing = product_params.missing_required(plan.params,
                                                  integration.project_family(project_id))
        if missing:
            names = "、".join(field["name"] for field in missing[:8])
            more = f" 等 {len(missing)} 项" if len(missing) > 8 else ""
            raise HTTPException(
                400, f"报价必填的成品参数还缺：{names}{more}。请在「参数推荐」里补填后再确认")
        plan.params_final = True
        plan.params_final_by = user.get("display_name") or user.get("username") or ""
        plan.params_final_at = now_cst_str()
    else:
        # 改过参数就不再算"已确认"：确认针对的是当时那一版，不是这个项目本身。
        plan.params_final = False
        plan.params_final_by = None
        plan.params_final_at = None
        if body.waiver:
            # 「仍要继续」：人签了字，缺口原样留档 —— 参数推荐这一步的签字记在
            # stage='params'，发送财务时同一批缺口凭它复用，不再要第二次签字。
            missing = integration.missing_required(plan, integration.project_family(project_id))
            integration.record_waiver(
                plan, "params",
                missing_codes=[str(field.get("code") or "") for field in missing],
                missing_fields=[str(field.get("name") or field.get("code") or "")
                                for field in missing],
                confirmations=integration.pending_confirmations(plan),
                reason=str((body.waiver or {}).get("reason") or ""),
                actor=user)
            store.audit(project_id, "integration_params_finalize_waived",
                        {"missing": len(missing),
                         "by": user.get("display_name") or user.get("username") or ""})
    integration.save_plan(project_id, plan, user.get("username", "system"))
    store.audit(project_id, "integration_params_finalize",
                {"filled": len(body.values or {}), "confirmed": bool(plan.params_final),
                 "by": user.get("username", "system")})
    return _integration_payload(project_id, plan)


@app.post("/api/projects/{project_id}/integration/process")
async def generate_integration_process(
    project_id: str, note: str = Form(""), attachments: List[UploadFile] = File(default=[]),
    user: dict = Depends(current_user),
):
    """组装工艺推荐：先查库内组装路线，再让模型在库内工序上排产(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    ir = _integration_ir(project_id)
    if integration.load_plan(project_id).params is None:
        raise HTTPException(400, "请先完成参数推荐：组装工序要按整机 BOM 与连接关系来排")
    author = user.get("username", "system")
    atts = await _read_attachments(attachments)

    def job():
        plan = integration.load_plan(project_id)
        lookup = _integration_process_lookup(project_id, plan)
        result, coverage = integration.outline_process(
            project_id, ir, plan,
            library=process_lookup.as_prompt(lookup) if lookup else "",
            lookup=lookup, note=note, attachments=atts, progress=tasks.report_progress)
        tasks.report_progress(
            f"  ↳ 共 {coverage['summary']['total']} 道组装工序："
            f"沿用库内 {coverage['summary']['reused']} 道、"
            f"缺失需新建 {coverage['summary']['missing']} 道")
        plan.process = result
        integration.save_plan(project_id, plan, author)
        store.save_process_coverage(project_id, integration.ASSEMBLY_PART_ID, coverage)
        return _integration_payload(project_id, plan)

    return {"task_id": tasks.submit(project_id, "integration_process", job)}


@app.put("/api/projects/{project_id}/integration/process")
def update_integration_process(project_id: str, plan_body: ProcessPlan,
                               user: dict = Depends(current_user)):
    """保存人工编辑后的组装工艺路线。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    plan_body.part_id = integration.ASSEMBLY_PART_ID
    plan_body.steps.sort(key=lambda step: step.step_no)
    # part_class 必须钉成 assembly：ProcessPlan 的默认值是 machining，而机加工模板不
    # 允许 assembly/welding 这类工序 —— 照默认走，整条组装路线会被规则校验全标成
    # "不常见工序"。允许的工序集见 agent_knowledge/rules/process_rules.json。
    plan_body.part_class = "assembly"
    plan_body.rule_warnings = process.validate_rules(plan_body.model_dump())
    plan = integration.load_plan(project_id)
    plan.process = plan_body
    integration.save_plan(project_id, plan, user.get("username", "system"))
    return _integration_payload(project_id, plan)


@app.post("/api/projects/{project_id}/integration/cost")
async def generate_integration_cost(
    project_id: str, quantity: int = 1, note: str = Form(""),
    attachments: List[UploadFile] = File(default=[]), user: dict = Depends(current_user),
):
    """整机成本测算：以 2.1 各零件已测算的单件成本为底，叠加组装工时与整机费率(异步)。

    成本已拆到 2.3（财务经理），2.2 的界面上不再有这一步；接口留着是因为 2.3 的
    cost_review.run_assembly 复用同一套上下文。权限跟着成本走。
    """
    _require(user, auth.COST_ROLES, "成本测算在 2.3，由财务经理负责")
    ir = _integration_ir(project_id)
    existing = integration.load_plan(project_id)
    # 只要求「组装工艺跑过」，不要求它排出了工序：按企业成本口径，人工/制费/加工
    # 是由材料成本乘固定系数推出来的（services/cost_model.py），并不依赖工序行。
    # 卡在"必须有工序"上只会让工艺为空的项目彻底走不下去，而那时最该做的恰恰是
    # 先把材料成本算出来。工序为空会在下面提示，不影响出数。
    if existing.process is None:
        raise HTTPException(400, "请先完成组装工艺推荐：成本测算要接在它后面")
    author = user.get("username", "system")
    qty = max(1, int(quantity or 1))
    atts = await _read_attachments(attachments)

    def job():
        plan = integration.load_plan(project_id)
        plan.quantity = qty
        if not (plan.process and plan.process.steps):
            tasks.report_progress(
                "注意：组装工艺一道工序都没有，本次只按物料清单算材料成本；"
                "人工/制费/加工仍按企业口径由材料推导")
        lookup = _integration_cost_lookup(project_id, plan, qty)
        use_web = cost.needs_web_search(lookup)
        tasks.report_progress(
            "库内依据有缺口，启用联网检索补行情价（较慢）" if use_web
            else "库内价格与费率齐全，跳过联网检索，直接按库内依据测算")
        result = integration.analyze_cost(
            project_id, ir, plan, quantity=qty, web=use_web,
            library=cost_lookup.as_prompt(lookup) if lookup else "",
            note=note, attachments=atts, progress=tasks.report_progress)
        # 与 2.1 同一套口径：材料逐项累加，人工/制费/加工由材料成本推导。
        normalized = cost_model.normalize(result.model_dump())
        breakdown = normalized["cost_model"]
        tasks.report_progress(
            f"  ↳ 整机成本：材料 {breakdown['material']:.2f} · 人工 {breakdown['labor']:.2f}"
            f" · 制费 {breakdown['overhead']:.2f} · 加工 {breakdown['machining']:.2f}"
            f" = {breakdown['total']:.2f} 元/台")
        plan.cost = CostAnalysis(**normalized)
        integration.save_plan(project_id, plan, author)
        return _integration_payload(project_id, plan)

    return {"task_id": tasks.submit(project_id, "integration_cost", job)}


@app.put("/api/projects/{project_id}/integration/cost")
def update_integration_cost(project_id: str, analysis: CostAnalysis,
                            user: dict = Depends(current_user)):
    """保存人工编辑后的整机成本测算。权限同零件成本：归财务（2.3）。"""
    _require(user, auth.COST_ROLES, "成本测算在 2.3，由财务经理负责")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    analysis.part_id = integration.ASSEMBLY_PART_ID
    plan = integration.load_plan(project_id)
    # 同 2.1：人工改完材料明细，三项派生成本必须跟着重算。
    plan.cost = CostAnalysis(**cost_model.normalize(analysis.model_dump()))
    integration.save_plan(project_id, plan, user.get("username", "system"))
    return _integration_payload(project_id, plan)


@app.post("/api/projects/{project_id}/integration/confirm")
def confirm_integration(project_id: str, user: dict = Depends(current_user)):
    """确认 2.2 结果。四个环节都有产出才允许确认 —— 缺一个，3.1 汇总就是残的。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    plan = integration.load_plan(project_id)
    state = integration.status(plan)
    # 按"有没有实际内容"判，而不是"跑没跑过"：三张表都可能跑完却是空的
    # （模型没给、人也还没补），那样确认下去，报价拿到的是三张空表。
    missing = [text for count, text in (
        (state["param_count"], "参数推荐（0 条参数，请生成或在参数表里手工补录）"),
        (state["process_step_count"], "组装工艺（0 道工序，请生成或手工添加工序）"),
        (state["cost_item_count"], "成本测算（0 条成本明细，请生成或手工添加）"),
    ) if not count]
    if missing:
        raise HTTPException(400, "以下环节尚未生成：" + "、".join(missing))
    plan.confirmed = True
    plan.confirmed_by = user.get("username", "system")
    plan.confirmed_at = now_cst_str()
    plan.timing.status = "done"
    plan.timing.completed = True
    plan.timing.finished_at = plan.confirmed_at
    integration.save_plan(project_id, plan, plan.confirmed_by)
    return _integration_payload(project_id, plan)


# --------------------------------------------------------------------------- #
# 2.2 的两个对外动作：写入业务主数据 / 确认工艺并发送至报价
#
# 两者都要连远程 Postgres 和报价工作流，而那两样只在一体化服务那一侧（cpq_db /
# cpq_wf）。这里通过 services/cpq_bridge 回调它的 /wf/tech/*，带上**用户自己的**
# CPQ 令牌 —— 写主数据和转交任务都要留痕到具体的人，不能用服务账号顶替。
# --------------------------------------------------------------------------- #
class IntegrationPublishBody(BaseModel):
    """产品名称留给前端覆盖：参数推荐给的整机名未必就是主数据里要用的成品名。"""
    product_name: str = ""
    spec: str = ""
    note: str = ""
    # 派发方式：和报价助手的「转交任务」一样三选一 —— 发给某个角色 / 指派给某个人 /
    # 发布到公共任务池。留空时由报价那边按任务类型落到默认角色（成本测算=财务经理）。
    target_type: str = ""
    target_role_code: str = ""
    target_user_id: str = ""
    # 可选：发送财务时携带的「仍要继续」签字（L2 缺口豁免）。真正记进 plan.waivers 的
    # 缺口以服务端算出的为准，这里只是本人"可以带缺口继续"的意愿与原因。
    waiver: Optional[dict] = None


def _bridge_http_error(exc: Exception) -> HTTPException:
    """桥接层的业务拒绝 → HTTP：落点冲突 409 + 结构化 detail，其余 400 + 纯字符串。"""
    if cpq_bridge.is_conflict(exc):
        # 落点冲突不是"你的参数不对"，是"这张卡片认不回来"：409 + 结构化 detail，
        # 界面据此弹「选择已有 / 明确新建（写原因）」，不再只有一句无法执行的文案。
        return HTTPException(409, cpq_bridge.conflict_detail(exc))
    return HTTPException(400, str(exc))


def _bridge_call(action, *args, **kwargs):
    """把桥接层的两类失败翻译成 HTTP：业务拒绝 400，服务不可用 503。

    不合并成一种：前者是"你的数据/权限不对"，后者是"库连不上"，处理方式完全不同。
    """
    try:
        return action(*args, **kwargs)
    except cpq_bridge.BridgeRejected as exc:
        raise _bridge_http_error(exc) from exc
    except cpq_bridge.BridgeUnavailable as exc:
        raise HTTPException(503, f"业务数据库/报价服务暂不可用：{exc}") from exc


def _flow_http_error(exc) -> HTTPException:
    """2.3 与 5.3 的 service 业务错误 → HTTP（两条链路同一口径）。

    落点冲突回 409 + `{code, candidates, message}`；其余按 service 自己给的状态码回
    纯字符串 —— 既有路由的响应形状一律不变。
    """
    if cpq_bridge.is_conflict(exc):
        return HTTPException(409, cpq_bridge.conflict_detail(exc))
    return HTTPException(int(getattr(exc, "status_code", 400) or 400),
                         getattr(exc, "message", None) or str(exc))


@app.post("/api/projects/{project_id}/integration/material-write")
def integration_write_material(project_id: str, body: IntegrationPublishBody,
                               request: Request, user: dict = Depends(current_user)):
    """写入数据库：新建成品编码，落 md_clm_material_base_info + md_clm_material_cost_cnf。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    plan = _cost_flow(cost_flow.integration_ready_cost, project_id)
    record = _cost_flow(cost_flow.integration_material_write_record, project_id, plan,
                        product_name=body.product_name, spec=body.spec,
                        token=_sso_token(request), user=user)
    integration.save_plan(project_id, plan, user.get("username", "system"))
    store.audit(project_id, "integration_material_write",
                {"number": record.number, "price": record.material_unit_price,
                 "by": record.written_by})
    return {**_integration_payload(project_id, plan), "written": record.model_dump()}


@app.post("/api/projects/{project_id}/integration/send-to-finance")
def integration_send_to_finance(project_id: str, body: IntegrationPublishBody,
                                request: Request, user: dict = Depends(current_user)):
    """2.2 的出口：确认工艺并发送至财务做成本测算。

    成本不再由工艺经理算 —— 他交的是工艺、参数与用量，成本的数字归财务
    （技术工艺 2.3）。所以这一步**不要求成本已完成**，只要求工艺与参数到位。
    """
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    # 实现唯一：与 Agent 的 SendIntegrationToFinance 走同一份 services.integration，
    # 两个确认闸门与对外调用都在那里，路由只做权限与错误翻译。
    plan = _bridge_call(
        _integration_flow, integration.send_to_finance, project_id, user,
        product_name=body.product_name, note=body.note,
        target_type=body.target_type, target_role_code=body.target_role_code,
        target_user_id=body.target_user_id, token=_sso_token(request),
        waiver=body.waiver)
    return {**_integration_payload(project_id, plan),
            "finance": plan.finance_handoff.model_dump()}


@app.post("/api/projects/{project_id}/integration/send-to-quote")
def integration_send_to_quote(project_id: str, body: IntegrationPublishBody,
                              request: Request, user: dict = Depends(current_user)):
    """确认工艺并发送至报价：卡片推进到第 3 步「定价-利润加成」，销售经理收到任务。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    return _cost_flow(cost_flow.integration_send_to_quote_body, project_id,
                      product_name=body.product_name, spec=body.spec, note=body.note,
                      token=_sso_token(request), user=user)


# --------------------------------------------------------------------------- #
# 2.3 成本测算 —— 财务经理的步骤
#
# 分工：工艺经理在 2.1/2.2 出工艺与用量，2.2 结束时把项目交给财务；财务在这一步
# 逐个零件 + 整机算成本、汇总，然后选三个去向之一：写入数据库 / 回传销售经理继续报价 /
# 提交工艺经理确认。**本步不联网**：只依据企业成本库与工程经验，见 services/cost_review.py。
# --------------------------------------------------------------------------- #
def _cost_review_ctx(project_id: str):
    """2.3 的三样输入：IR（零件）、2.2 的整机方案、本步的评审状态。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    return ir, integration.load_plan(project_id), cost_review.load_review(project_id)


def _cost_review_payload(project_id: str):
    ir, plan, review = _cost_review_ctx(project_id)
    return cost_review.payload(project_id, ir, plan, review)


def _cost_flow(fn, *args, **kwargs):
    """2.3 流转的统一出口：共享 service 的业务错误原样映射成 HTTPException。

    Agent 工具（ConfirmCostReview / WriteCostReviewMaterial / SendCostReviewToQuote /
    ReturnCostReviewToProcess）调用的是同一份 service 函数，这里只负责 HTTP 这一层。
    """
    try:
        return fn(*args, **kwargs)
    except cost_flow.CostFlowError as exc:
        raise _flow_http_error(exc) from exc
    except cpq_bridge.BridgeRejected as exc:
        # service 直接把桥接拒绝透出来时，出口口径与上面完全一致（同一份判定与文案）。
        raise _bridge_http_error(exc) from exc


@app.get("/api/projects/{project_id}/cost-review")
def get_cost_review(project_id: str):
    """2.3 全貌：零件逐项成本 + 整机成本 + 两种口径的合计 + 评审状态。"""
    return _cost_review_payload(project_id)


@app.put("/api/projects/{project_id}/cost-review")
def update_cost_review(project_id: str, body: CostReviewBody,
                       user: dict = Depends(current_user)):
    """保存财务的补充说明（会作为下一次测算的输入）。"""
    _require(user, auth.COST_ROLES, "成本测算由财务经理负责，需要财务权限")
    return _cost_flow(cost_flow.save_note, project_id, user,
                      note=body.note, quantity=body.quantity)


@app.post("/api/projects/{project_id}/cost-review/parts/{part_id}")
def run_cost_review_part(project_id: str, part_id: str, quantity: int = 1,
                         user: dict = Depends(current_user)):
    """算一个零件的成本（异步任务）。不联网。"""
    _require(user, auth.COST_ROLES, "成本测算由财务经理负责，需要财务权限")
    ir, plan, review = _cost_review_ctx(project_id)
    if ir is None:
        raise HTTPException(400, "请先完成 2.1 图纸解析：成本要按零件清单逐件算")
    author = user.get("username", "system")
    qty = max(1, int(quantity or 1))

    def job():
        analysis = cost_review.run_part(
            project_id, ir, part_id, qty, note=review.note,
            progress=tasks.report_progress)
        store.save_cost(project_id, part_id, analysis, author=author)
        return _cost_review_payload(project_id)

    return {"task_id": tasks.submit(project_id, "cost_review_part", job)}


@app.post("/api/projects/{project_id}/cost-review/assembly")
def run_cost_review_assembly(project_id: str, user: dict = Depends(current_user)):
    """算整机（组装）成本（异步任务）。不联网。"""
    _require(user, auth.COST_ROLES, "成本测算由财务经理负责，需要财务权限")
    ir, plan, review = _cost_review_ctx(project_id)
    if plan.process is None:
        raise HTTPException(400, "请先由工艺经理完成 2.2 组装工艺：组装成本要按它来算")
    # 整机成本要引用每个零件的单件成本：缺一件就会算出一份偏低的整机成本，还白花一次
    # 模型钱。所以同步判定（提交任务之前）—— 口径复用 summarize() 的 counts.missing，
    # 不另算一份，也不靠前端"第一件失败就提前 return"顺带兜住。
    summary = cost_review.summarize(project_id, ir, plan)
    missing = list((summary.get("counts") or {}).get("missing") or [])
    if missing:
        names = {str(row.get("id")): row.get("name") for row in summary.get("parts") or []}
        detail = "、".join(
            f"{part_id}（{names.get(part_id)}）" if names.get(part_id) else str(part_id)
            for part_id in missing)
        raise HTTPException(
            409,
            f"还有 {len(missing)} 个零件没算出成本：{detail}。"
            "请先在「零件」页逐个算完（或点「仅重试失败项」补算缺的那几个），再算整机成本。")
    author = user.get("username", "system")

    def job():
        current = integration.load_plan(project_id)
        analysis, _lookup = cost_review.run_assembly(
            project_id, ir, current, note=review.note, progress=tasks.report_progress)
        # 与零件侧同一套口径：材料逐项累加，其余三项按系数推导。
        current.cost = CostAnalysis(**cost_model.normalize(analysis.model_dump()))
        integration.save_plan(project_id, current, author)
        return _cost_review_payload(project_id)

    return {"task_id": tasks.submit(project_id, "cost_review_assembly", job)}


@app.post("/api/projects/{project_id}/cost-review/confirm")
def confirm_cost_review(project_id: str, body: Optional[CostConfirmBody] = None,
                        user: dict = Depends(current_user)):
    """财务确认本步成本。

    缺口分两级：「一个零件都没有」是 L1 硬拦（签字也不放行）；未算零件 / 整机未算 /
    算出来是 0 元是 L2 缺口 —— 前端弹一次「仍要继续」，点继续才带着签字进 body.waiver，
    同一批缺口不再拦第二次。不带请求体的老调用（Agent 工具）行为不变：有缺口就如实拒绝。
    """
    _require(user, auth.COST_ROLES, "成本测算由财务经理负责，需要财务权限")
    return _cost_flow(cost_flow.confirm_review, project_id, user,
                      waiver=(body.waiver if body else None))


@app.post("/api/projects/{project_id}/cost-review/material-write")
def write_cost_review_material(project_id: str, body: CostActionBody,
                               request: Request, user: dict = Depends(current_user)):
    """去向①：写入数据库（新建成品编码 + 物料成本配置）。"""
    _require(user, auth.COST_ROLES, "成本测算由财务经理负责，需要财务权限")
    return _cost_flow(cost_flow.write_material, project_id, user,
                      product_name=body.product_name, spec=body.spec,
                      token=_sso_token(request))


@app.post("/api/projects/{project_id}/cost-review/send-to-quote")
def send_cost_review_to_quote(project_id: str, body: CostActionBody,
                              request: Request, user: dict = Depends(current_user)):
    """去向②：回传销售经理继续报价（复用 2.2 那条推送，成本与参数一并带回）。

    完成后按 body.source_task_id 关闭来源的 claimed 财务待办（在 cost_flow 里做）。
    """
    _require(user, auth.COST_ROLES, "成本测算由财务经理负责，需要财务权限")
    return _cost_flow(cost_flow.send_to_quote, project_id, user,
                      product_name=body.product_name, spec=body.spec, note=body.note,
                      token=_sso_token(request), source_task_id=body.source_task_id)


@app.post("/api/projects/{project_id}/cost-review/return-to-process")
def cost_review_return_to_process(project_id: str, body: CostActionBody,
                                  request: Request, user: dict = Depends(current_user)):
    """去向①：提交工艺经理确认（第 5 阶段「工艺评估报告」）。

    成本必须先确认，未确认的数不作为正式结果往下走。正常提交确认与返工是两条路：
    确实要返工时，由第 5 阶段明确退回第 3 阶段，不走这个按钮。
    """
    _require(user, auth.COST_ROLES, "成本测算由财务经理负责，需要财务权限")
    return _cost_flow(cost_flow.return_to_process, project_id, user,
                      product_name=body.product_name, note=body.note,
                      target_user_id=body.target_user_id, token=_sso_token(request),
                      source_task_id=body.source_task_id)


def _integration_process_lookup(project_id: str, plan) -> Optional[dict]:
    """整机的工艺库检索。失败降级为"没查到"，绝不能挡住推荐 —— 与零件侧口径一致。"""
    try:
        report = integration.lookup_process(
            plan, batch_size=plan.quantity, progress=tasks.report_progress)
        process_lookup.save_report(project_id, integration.ASSEMBLY_PART_ID, report)
        return report
    except Exception as exc:
        store.audit(project_id, "integration_process_lookup_failed", {"error": str(exc)[:200]})
        return None


def _integration_cost_lookup(project_id: str, plan, quantity: int) -> Optional[dict]:
    try:
        report = cost_lookup.lookup_part(
            integration.pseudo_part(plan), quantity=quantity,
            process_report=store.load_process_lookup(project_id, integration.ASSEMBLY_PART_ID),
            progress=tasks.report_progress)
        cost_lookup.save_report(project_id, integration.ASSEMBLY_PART_ID, report)
        return report
    except Exception as exc:
        store.audit(project_id, "integration_cost_lookup_failed", {"error": str(exc)[:200]})
        return None


# --------------------------------------------------------------------------- #
# 材料定性与供应链拆解(技术工艺第 3 步)
#   - recommend: Claude 联网产出候选材料/配方/粉末要求(异步)
#   - PUT:       保存人工编辑后的计划
#   - confirm:   人工确认主体材料 / 金属化方案
#   - evaluate:  确定性供应商达标匹配
#   - timing:    记录起止时间与是否完成
# --------------------------------------------------------------------------- #
def _now_str() -> str:
    return now_cst_str()


def _load_material_plan(project_id: str) -> MaterialPlan:
    saved = store.load_material(project_id)
    plan = MaterialPlan(**saved) if saved else MaterialPlan(project_id=project_id)
    plan.project_id = project_id
    return plan


@app.get("/api/projects/{project_id}/material")
def get_material(project_id: str):
    """读取该项目的材料定性与供应链拆解计划。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"material": store.load_material(project_id)}


@app.post("/api/projects/{project_id}/material/recommend")
def recommend_material(project_id: str, note: str = Form(""),
                       user: dict = Depends(current_user)):
    """Claude 联网产出候选陶瓷主体材料/电极金属化配方/粉末要求(异步任务)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    author = user.get("username", "system")
    dependency_hash = _digest_value((ir_dict, store.load_material(project_id)))

    def job():
        tasks.report_progress("正在调用模型生成材料方案")
        rec = material.recommend(ir=ir, note=note, web=True)
        tasks.report_progress("材料方案已返回，正在校验候选和供应要求")
        _assert_dependencies_unchanged(
            dependency_hash, (store.load_ir(project_id), store.load_material(project_id)), "IR 或材料草稿"
        )
        plan = _load_material_plan(project_id)
        previous_plan = plan.model_copy(deep=True)
        previous_body = plan.body.model_copy(deep=True)
        previous_metallization = plan.metallization.model_copy(deep=True)
        # 把建议合并进计划(确定性):候选/默认选定/配方/粉末要求/来源
        plan.body.candidates = rec.body_candidates
        if rec.body_recommended and not plan.body.selected:
            plan.body.selected = rec.body_recommended
        if rec.body_rationale:
            plan.body.rationale = rec.body_rationale
        plan.metallization.paste = rec.paste
        plan.metallization.layers = rec.layers
        if rec.metallization_rationale:
            plan.metallization.rationale = rec.metallization_rationale
        if rec.requirements:
            plan.supply.requirements = rec.requirements
        plan.assumptions = rec.assumptions
        plan.open_questions = rec.open_questions
        plan.search_sources = rec.search_sources
        _sync_confirmation(previous_body, plan.body)
        _sync_confirmation(previous_metallization, plan.metallization)
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_material(project_id, d, author=author)
        if _business_changed(previous_plan, plan):
            store.invalidate_confirmations(
                project_id,
                ["manufacturing", "cleaning", "assembly", "production", "summary",
                 "costest", "pricing", "negotiation", "pricenego", "approval"],
                "材料定性方案已更新", author,
            )
        return {"material": d}

    return {"task_id": tasks.submit(
        project_id, "material_recommend", job,
        dedup_key=_task_key("material_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/material")
def update_material(project_id: str, plan: MaterialPlan,
                    user: dict = Depends(current_user)):
    """保存人工编辑后的材料计划(选定材料/配方/粉末要求等)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_material_plan(project_id)
    changed = _business_changed(current, plan)
    _sync_confirmation(current.body, plan.body)
    _sync_confirmation(current.metallization, plan.metallization)
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_material(project_id, d, author=user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id,
            ["manufacturing", "cleaning", "assembly", "production", "summary",
             "costest", "pricing", "negotiation", "pricenego", "approval"],
            "材料定性方案已人工修改", user.get("username", "system"),
        )
    return {"material": d}


@app.post("/api/projects/{project_id}/material/confirm")
def confirm_material(project_id: str, section: str,
                     user: dict = Depends(current_user)):
    """人工确认某一节(section=body|metallization),记录确认人与时间。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if section not in ("body", "metallization"):
        raise HTTPException(400, "section 必须为 body 或 metallization")
    plan = _load_material_plan(project_id)
    who = user.get("username", "system")
    if section == "body":
        if not plan.body.selected:
            raise HTTPException(400, "请先选定主体材料再确认")
        plan.body.confirmed = True
        plan.body.confirmed_by = who
        plan.body.confirmed_at = _now_str()
    else:
        if not (plan.metallization.paste or plan.metallization.layers or plan.metallization.rationale):
            raise HTTPException(400, "金属化方案为空；不适用时请填写不适用依据后再确认")
        plan.metallization.confirmed = True
        plan.metallization.confirmed_by = who
        plan.metallization.confirmed_at = _now_str()
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_material(project_id, d, author=who)
    return {"material": d}


@app.post("/api/projects/{project_id}/material/evaluate")
def evaluate_material(project_id: str, user: dict = Depends(current_user)):
    """确定性供应商达标匹配:用计划中的粉末要求 vs 供应商能力目录。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    plan = _load_material_plan(project_id)
    if not plan.supply.requirements:
        raise HTTPException(400, "尚无粉末纯度/粒径要求,请先做 AI 推荐或手动填写要求")
    result = material.evaluate(plan.supply.requirements, store.list_suppliers())
    plan.supply = result
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_material(project_id, d, author=user.get("username", "system"))
    store.invalidate_confirmations(
        project_id,
        ["manufacturing", "assembly", "production", "summary", "costest", "pricing",
         "negotiation", "pricenego", "approval"],
        "供应商能力评估结果已更新", user.get("username", "system"),
    )
    return {"material": d}


@app.post("/api/projects/{project_id}/material/timing")
def material_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    plan = _load_material_plan(project_id)
    if action == "start":
        plan.timing.status = "in_progress"
        plan.timing.started_at = _now_str()
        plan.timing.finished_at = None
        plan.timing.completed = False
    else:
        if not plan.timing.started_at:
            plan.timing.started_at = _now_str()
        plan.timing.status = "done"
        plan.timing.finished_at = _now_str()
        plan.timing.completed = True
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_material(project_id, d, author=user.get("username", "system"))
    return {"material": d}


# --------------------------------------------------------------------------- #
# 供应商能力目录(全局,种子可维护;供③达标匹配使用)
# --------------------------------------------------------------------------- #
@app.get("/api/suppliers")
def list_suppliers_ep():
    return {"suppliers": store.list_suppliers()}


@app.post("/api/suppliers")
def save_supplier_ep(supplier: Supplier, user: dict = Depends(current_user)):
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    return {"supplier": store.save_supplier(supplier.model_dump())}


@app.delete("/api/suppliers/{supplier_id}")
def delete_supplier_ep(supplier_id: str, user: dict = Depends(current_user)):
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    store.delete_supplier(supplier_id)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 制造工艺路径规划和 BOM 编制(技术工艺第 4 步)
# --------------------------------------------------------------------------- #
def _load_manufacturing_plan(project_id: str) -> ManufacturingPlan:
    saved = store.load_manufacturing(project_id)
    plan = ManufacturingPlan(**saved) if saved else ManufacturingPlan(project_id=project_id)
    plan.project_id = project_id
    return plan


@app.get("/api/projects/{project_id}/manufacturing")
def get_manufacturing(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"manufacturing": store.load_manufacturing(project_id)}


@app.post("/api/projects/{project_id}/manufacturing/recommend")
def recommend_manufacturing(project_id: str, note: str = Form(""),
                            user: dict = Depends(current_user)):
    """Claude 联网产出核心工艺路径/附加工艺评估/工艺 BOM(异步,读 IR + 第3步材料计划)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    material_plan = store.load_material(project_id)
    author = user.get("username", "system")
    dependency_hash = _digest_value((ir_dict, material_plan, store.load_manufacturing(project_id)))

    def job():
        tasks.report_progress("正在调用模型生成制造工艺方案")
        rec = manufacturing.recommend(ir=ir, material_plan=material_plan, note=note, web=True)
        tasks.report_progress("制造方案已返回，正在校验工序与 BOM")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_material(project_id), store.load_manufacturing(project_id)),
            "IR、材料方案或制造草稿",
        )
        plan = _load_manufacturing_plan(project_id)
        previous_plan = plan.model_copy(deep=True)
        previous_path = plan.path.model_copy(deep=True)
        previous_bom = plan.bom.model_copy(deep=True)
        plan.path.steps = rec.core_path
        if rec.path_summary:
            plan.path.summary = rec.path_summary
        plan.additional = rec.additional
        plan.bom.items = rec.bom
        if rec.bom_summary:
            plan.bom.summary = rec.bom_summary
        plan.assumptions = rec.assumptions
        plan.open_questions = rec.open_questions
        plan.search_sources = rec.search_sources
        _sync_confirmation(previous_path, plan.path)
        _sync_confirmation(previous_bom, plan.bom)
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_manufacturing(project_id, d, author=author)
        if _business_changed(previous_plan, plan):
            store.invalidate_confirmations(
                project_id,
                ["assembly", "production", "summary", "costest", "pricing",
                 "negotiation", "pricenego", "approval"],
                "制造工艺方案已更新", author,
            )
        return {"manufacturing": d}

    return {"task_id": tasks.submit(
        project_id, "manufacturing_recommend", job,
        dedup_key=_task_key("manufacturing_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/manufacturing")
def update_manufacturing(project_id: str, plan: ManufacturingPlan,
                         user: dict = Depends(current_user)):
    """保存人工编辑后的制造工艺计划。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_manufacturing_plan(project_id)
    changed = _business_changed(current, plan)
    _sync_confirmation(current.path, plan.path)
    _sync_confirmation(current.bom, plan.bom)
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_manufacturing(project_id, d, author=user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id,
            ["assembly", "production", "summary", "costest", "pricing",
             "negotiation", "pricenego", "approval"],
            "制造工艺方案已人工修改", user.get("username", "system"),
        )
    return {"manufacturing": d}


@app.post("/api/projects/{project_id}/manufacturing/confirm")
def confirm_manufacturing(project_id: str, section: str,
                          user: dict = Depends(current_user)):
    """人工确认某一节(section=path|bom),记录确认人与时间。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if section not in ("path", "bom"):
        raise HTTPException(400, "section 必须为 path 或 bom")
    plan = _load_manufacturing_plan(project_id)
    who = user.get("username", "system")
    target = plan.path if section == "path" else plan.bom
    if section == "path" and not plan.path.steps:
        raise HTTPException(400, "尚无工艺路径,请先做 AI 推荐")
    if section == "bom" and not plan.bom.items:
        raise HTTPException(400, "尚无工艺 BOM,请先做 AI 推荐")
    target.confirmed = True
    target.confirmed_by = who
    target.confirmed_at = _now_str()
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_manufacturing(project_id, d, author=who)
    return {"manufacturing": d}


@app.post("/api/projects/{project_id}/manufacturing/timing")
def manufacturing_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    plan = _load_manufacturing_plan(project_id)
    if action == "start":
        plan.timing.status = "in_progress"
        plan.timing.started_at = _now_str()
        plan.timing.finished_at = None
        plan.timing.completed = False
    else:
        if not plan.timing.started_at:
            plan.timing.started_at = _now_str()
        plan.timing.status = "done"
        plan.timing.finished_at = _now_str()
        plan.timing.completed = True
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_manufacturing(project_id, d, author=user.get("username", "system"))
    return {"manufacturing": d}


@app.get("/api/projects/{project_id}/manufacturing/bom.csv")
def export_manufacturing_bom(project_id: str):
    """导出工艺 BOM 为 CSV。"""
    plan = store.load_manufacturing(project_id) or {}
    items = ((plan.get("bom") or {}).get("items")) or []
    csv_text = manufacturing.bom_csv(items)
    return Response(
        content=csv_text.encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="bom_{project_id}.csv"'},
    )


# --------------------------------------------------------------------------- #
# 清洗与洁净度管控方案制定(技术工艺第 5 步)
# --------------------------------------------------------------------------- #
def _load_cleaning_plan(project_id: str) -> CleaningPlan:
    saved = store.load_cleaning(project_id)
    plan = CleaningPlan(**saved) if saved else CleaningPlan(project_id=project_id)
    plan.project_id = project_id
    return plan


@app.get("/api/projects/{project_id}/cleaning")
def get_cleaning(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"cleaning": store.load_cleaning(project_id)}


@app.post("/api/projects/{project_id}/cleaning/recommend")
def recommend_cleaning(project_id: str, note: str = Form(""),
                       user: dict = Depends(current_user)):
    """Claude 联网依据图纸洁净度等级定制化学清洗+高纯水终漂洗+管控方案(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    material_plan = store.load_material(project_id)
    # 把项目备注(可能含图纸洁净度标注)并入补充说明
    project_note = store.get_note(project_id)
    merged_note = "\n".join(x for x in [project_note, note] if x and x.strip())
    author = user.get("username", "system")
    dependency_hash = _digest_value((ir_dict, material_plan, store.load_cleaning(project_id)))

    def job():
        tasks.report_progress("正在调用模型生成洁净方案")
        rec = cleaning.recommend(ir=ir, material_plan=material_plan, note=merged_note, web=True)
        tasks.report_progress("洁净方案已返回，正在校验等级和操作要求")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_material(project_id), store.load_cleaning(project_id)),
            "IR、材料方案或清洗草稿",
        )
        plan = _load_cleaning_plan(project_id)
        previous = plan.model_copy(deep=True)
        plan.cleanliness_grade = rec.cleanliness_grade
        plan.grade_source = rec.grade_source
        plan.grade_notes = rec.grade_notes
        plan.chemical_steps = rec.chemical_steps
        plan.rinse_steps = rec.rinse_steps
        plan.controls = rec.controls
        plan.summary = rec.summary
        plan.assumptions = rec.assumptions
        plan.open_questions = rec.open_questions
        plan.search_sources = rec.search_sources
        _sync_confirmation(previous, plan)
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_cleaning(project_id, d, author=author)
        if _business_changed(previous, plan):
            store.invalidate_confirmations(project_id, ["summary"], "清洗方案已更新", author)
        return {"cleaning": d}

    return {"task_id": tasks.submit(
        project_id, "cleaning_recommend", job,
        dedup_key=_task_key("cleaning_recommend", dependency_hash, merged_note),
    )}


@app.put("/api/projects/{project_id}/cleaning")
def update_cleaning(project_id: str, plan: CleaningPlan,
                    user: dict = Depends(current_user)):
    """保存人工编辑后的清洗方案。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_cleaning_plan(project_id)
    changed = _business_changed(current, plan)
    _sync_confirmation(current, plan)
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_cleaning(project_id, d, author=user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id, ["summary"], "清洗方案已人工修改", user.get("username", "system")
        )
    return {"cleaning": d}


@app.post("/api/projects/{project_id}/cleaning/confirm")
def confirm_cleaning(project_id: str, user: dict = Depends(current_user)):
    """人工确认清洗与洁净度管控方案,记录确认人与时间。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    plan = _load_cleaning_plan(project_id)
    if not plan.chemical_steps and not plan.rinse_steps:
        raise HTTPException(400, "尚无清洗方案,请先做 AI 推荐")
    who = user.get("username", "system")
    plan.confirmed = True
    plan.confirmed_by = who
    plan.confirmed_at = _now_str()
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_cleaning(project_id, d, author=who)
    return {"cleaning": d}


@app.post("/api/projects/{project_id}/cleaning/timing")
def cleaning_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    plan = _load_cleaning_plan(project_id)
    if action == "start":
        plan.timing.status = "in_progress"
        plan.timing.started_at = _now_str()
        plan.timing.finished_at = None
        plan.timing.completed = False
    else:
        if not plan.timing.started_at:
            plan.timing.started_at = _now_str()
        plan.timing.status = "done"
        plan.timing.finished_at = _now_str()
        plan.timing.completed = True
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_cleaning(project_id, d, author=user.get("username", "system"))
    return {"cleaning": d}


# --------------------------------------------------------------------------- #
# 组装与检测方案制定(技术工艺第 6 步)
# --------------------------------------------------------------------------- #
def _load_assembly_plan(project_id: str) -> AssemblyPlan:
    saved = store.load_assembly(project_id)
    plan = AssemblyPlan(**saved) if saved else AssemblyPlan(project_id=project_id)
    plan.project_id = project_id
    return plan


@app.get("/api/projects/{project_id}/assembly")
def get_assembly(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"assembly": store.load_assembly(project_id)}


@app.post("/api/projects/{project_id}/assembly/recommend")
def recommend_assembly(project_id: str, note: str = Form(""),
                       user: dict = Depends(current_user)):
    """Claude 联网规划陶瓷-金属组装工艺并制定电性能/吸附力/气密性检测方案(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    material_plan = store.load_material(project_id)
    manufacturing_plan = store.load_manufacturing(project_id)
    author = user.get("username", "system")
    # 提交时先取输入+草稿摘要，任务执行期间若被改过就丢弃旧结果。
    dependency_hash = _digest_value((
        ir_dict, material_plan, manufacturing_plan, store.load_assembly(project_id),
    ))

    def job():
        tasks.report_progress("正在调用模型生成组装检测方案")
        rec = assembly.recommend(ir=ir, material_plan=material_plan,
                                 manufacturing_plan=manufacturing_plan, note=note, web=True)
        tasks.report_progress("组装检测方案已返回，正在校验检测项和依赖")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_material(project_id),
             store.load_manufacturing(project_id), store.load_assembly(project_id)),
            "IR、上游工艺或组装草稿",
        )
        plan = _load_assembly_plan(project_id)
        previous_plan = plan.model_copy(deep=True)
        previous_assembly = plan.assembly.model_copy(deep=True)
        previous_inspection = plan.inspection.model_copy(deep=True)
        plan.assembly.method = rec.bonding_method
        plan.assembly.rationale = rec.bonding_rationale
        plan.assembly.steps = rec.assembly_steps
        plan.inspection.tests = rec.tests
        plan.summary = rec.summary
        plan.assumptions = rec.assumptions
        plan.open_questions = rec.open_questions
        plan.search_sources = rec.search_sources
        _sync_confirmation(previous_assembly, plan.assembly)
        _sync_confirmation(previous_inspection, plan.inspection)
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_assembly(project_id, d, author=author)
        if _business_changed(previous_plan, plan):
            store.invalidate_confirmations(project_id, ["summary"], "组装检测方案已更新", author)
        return {"assembly": d}

    return {"task_id": tasks.submit(
        project_id, "assembly_recommend", job,
        dedup_key=_task_key("assembly_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/assembly")
def update_assembly(project_id: str, plan: AssemblyPlan,
                    user: dict = Depends(current_user)):
    """保存人工编辑后的组装与检测方案。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_assembly_plan(project_id)
    changed = _business_changed(current, plan)
    _sync_confirmation(current.assembly, plan.assembly)
    _sync_confirmation(current.inspection, plan.inspection)
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_assembly(project_id, d, author=user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id, ["summary"], "组装检测方案已人工修改", user.get("username", "system")
        )
    return {"assembly": d}


@app.post("/api/projects/{project_id}/assembly/confirm")
def confirm_assembly(project_id: str, section: str,
                     user: dict = Depends(current_user)):
    """人工确认某一节(section=assembly|inspection),记录确认人与时间。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if section not in ("assembly", "inspection"):
        raise HTTPException(400, "section 必须为 assembly 或 inspection")
    plan = _load_assembly_plan(project_id)
    who = user.get("username", "system")
    if section == "assembly":
        if not plan.assembly.steps:
            raise HTTPException(400, "尚无组装工艺,请先做 AI 推荐")
        target = plan.assembly
    else:
        if not plan.inspection.tests:
            raise HTTPException(400, "尚无检测方案,请先做 AI 推荐")
        target = plan.inspection
    target.confirmed = True
    target.confirmed_by = who
    target.confirmed_at = _now_str()
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_assembly(project_id, d, author=who)
    return {"assembly": d}


@app.post("/api/projects/{project_id}/assembly/timing")
def assembly_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    plan = _load_assembly_plan(project_id)
    if action == "start":
        plan.timing.status = "in_progress"
        plan.timing.started_at = _now_str()
        plan.timing.finished_at = None
        plan.timing.completed = False
    else:
        if not plan.timing.started_at:
            plan.timing.started_at = _now_str()
        plan.timing.status = "done"
        plan.timing.finished_at = _now_str()
        plan.timing.completed = True
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_assembly(project_id, d, author=user.get("username", "system"))
    return {"assembly": d}


# --------------------------------------------------------------------------- #
# 产线匹配与产能评估(技术工艺第 7 步)
# --------------------------------------------------------------------------- #
def _load_production_plan(project_id: str) -> ProductionPlan:
    saved = store.load_production(project_id)
    plan = ProductionPlan(**saved) if saved else ProductionPlan(project_id=project_id)
    plan.project_id = project_id
    return plan


@app.get("/api/projects/{project_id}/production")
def get_production(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"production": store.load_production(project_id)}


@app.post("/api/projects/{project_id}/production/recommend")
def recommend_production(project_id: str, note: str = Form(""),
                         user: dict = Depends(current_user)):
    """Claude 联网依据制造工艺与设备台账做产线匹配/外协建议/产能评估(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    manufacturing_plan = store.load_manufacturing(project_id)
    equipment = store.list_equipment()
    author = user.get("username", "system")
    dependency_hash = _digest_value((ir_dict, manufacturing_plan, equipment, store.load_production(project_id)))

    def job():
        tasks.report_progress("正在调用模型生成产能评估方案")
        rec = production.recommend(ir=ir, manufacturing_plan=manufacturing_plan,
                                   equipment=equipment, note=note, web=True)
        tasks.report_progress("产能方案已返回，正在校验设备和产能约束")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_manufacturing(project_id),
             store.list_equipment(), store.load_production(project_id)),
            "IR、制造方案、设备台账或产线草稿",
        )
        plan = _load_production_plan(project_id)
        previous_plan = plan.model_copy(deep=True)
        previous_inhouse = plan.inhouse.model_copy(deep=True)
        previous_outsourcing = plan.outsourcing.model_copy(deep=True)
        plan.requirements = rec.requirements
        plan.inhouse.matches = rec.inhouse_matches
        plan.outsourcing.plans = rec.outsourcing
        plan.capacity_summary = rec.capacity_summary
        plan.conclusion = rec.conclusion
        plan.assumptions = rec.assumptions
        plan.open_questions = rec.open_questions
        plan.search_sources = rec.search_sources
        _sync_confirmation(previous_inhouse, plan.inhouse)
        _sync_confirmation(previous_outsourcing, plan.outsourcing)
        plan.updated_at = _now_str()
        d = plan.model_dump()
        store.save_production(project_id, d, author=author)
        if _business_changed(previous_plan, plan):
            store.invalidate_confirmations(project_id, ["summary"], "产线产能方案已更新", author)
        return {"production": d}

    return {"task_id": tasks.submit(
        project_id, "production_recommend", job,
        dedup_key=_task_key("production_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/production")
def update_production(project_id: str, plan: ProductionPlan,
                      user: dict = Depends(current_user)):
    """保存人工编辑后的产线匹配与产能评估。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_production_plan(project_id)
    changed = _business_changed(current, plan)
    _sync_confirmation(current.inhouse, plan.inhouse)
    _sync_confirmation(current.outsourcing, plan.outsourcing)
    plan.project_id = project_id
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_production(project_id, d, author=user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id, ["summary"], "产线产能方案已人工修改", user.get("username", "system")
        )
    return {"production": d}


@app.post("/api/projects/{project_id}/production/confirm")
def confirm_production(project_id: str, section: str,
                       user: dict = Depends(current_user)):
    """人工确认某一节(section=inhouse|outsourcing),记录确认人与时间。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if section not in ("inhouse", "outsourcing"):
        raise HTTPException(400, "section 必须为 inhouse 或 outsourcing")
    plan = _load_production_plan(project_id)
    who = user.get("username", "system")
    target = plan.inhouse if section == "inhouse" else plan.outsourcing
    if section == "inhouse" and not plan.inhouse.matches:
        raise HTTPException(400, "尚无自有产线匹配结果")
    if section == "outsourcing" and not plan.outsourcing.plans:
        raise HTTPException(400, "尚无外协处置方案")
    target.confirmed = True
    target.confirmed_by = who
    target.confirmed_at = _now_str()
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_production(project_id, d, author=who)
    return {"production": d}


@app.post("/api/projects/{project_id}/production/timing")
def production_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    plan = _load_production_plan(project_id)
    if action == "start":
        plan.timing.status = "in_progress"
        plan.timing.started_at = _now_str()
        plan.timing.finished_at = None
        plan.timing.completed = False
    else:
        if not plan.timing.started_at:
            plan.timing.started_at = _now_str()
        plan.timing.status = "done"
        plan.timing.finished_at = _now_str()
        plan.timing.completed = True
    plan.updated_at = _now_str()
    d = plan.model_dump()
    store.save_production(project_id, d, author=user.get("username", "system"))
    return {"production": d}


# --------------------------------------------------------------------------- #
# 设备资源台账(全局,种子可维护;供产线匹配/外协评估使用)
# --------------------------------------------------------------------------- #
@app.get("/api/equipment")
def list_equipment_ep():
    return {"equipment": store.list_equipment()}


@app.post("/api/equipment")
def save_equipment_ep(equipment: EquipmentResource, user: dict = Depends(current_user)):
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    return {"equipment": store.save_equipment(equipment.model_dump())}


@app.delete("/api/equipment/{equipment_id}")
def delete_equipment_ep(equipment_id: str, user: dict = Depends(current_user)):
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    store.delete_equipment(equipment_id)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 技术工艺总结(技术工艺第 8 步:汇总各步 + 可编辑执行摘要 + 导出文档)
# --------------------------------------------------------------------------- #
def _load_summary_doc(project_id: str) -> SummaryDoc:
    saved = store.load_summary(project_id)
    doc = SummaryDoc(**saved) if saved else SummaryDoc(project_id=project_id)
    doc.project_id = project_id
    return doc


@app.get("/api/projects/{project_id}/summary")
def get_summary(project_id: str):
    """汇总各步结果 + 执行摘要(供总结页展示/编辑)。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return summary_svc.aggregate(project_id)


@app.post("/api/projects/{project_id}/summary/recommend")
def recommend_summary(project_id: str, user: dict = Depends(current_user)):
    """Claude 依据各步汇总生成执行摘要(异步)。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    author = user.get("username", "system")
    submitted_aggregate = summary_svc.aggregate(project_id)
    dependency_hash = _digest_value(submitted_aggregate)

    def job():
        tasks.report_progress("正在调用模型汇总工艺评估结果")
        rec = summary_svc.recommend(submitted_aggregate, web=False)
        tasks.report_progress("汇总结果已返回，正在生成审签报告")
        _assert_dependencies_unchanged(
            dependency_hash, summary_svc.aggregate(project_id), "工艺汇总输入"
        )
        doc = _load_summary_doc(project_id)
        previous = doc.model_copy(deep=True)
        doc.overview = rec.overview
        doc.highlights = rec.highlights
        doc.risks = rec.risks
        doc.conclusion = rec.conclusion
        doc.search_sources = rec.search_sources
        _sync_confirmation(previous, doc)
        doc.updated_at = _now_str()
        d = doc.model_dump()
        store.save_summary(project_id, d, author=author)
        return {"summary": d}

    return {"task_id": tasks.submit(
        project_id, "summary_recommend", job,
        dedup_key=_task_key("summary_recommend", dependency_hash),
    )}


@app.put("/api/projects/{project_id}/summary")
def update_summary(project_id: str, doc: SummaryDoc, user: dict = Depends(current_user)):
    """保存人工编辑后的执行摘要。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    _sync_confirmation(_load_summary_doc(project_id), doc)
    doc.project_id = project_id
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_summary(project_id, d, author=user.get("username", "system"))
    return {"summary": d}


@app.post("/api/projects/{project_id}/summary/confirm")
def confirm_summary(project_id: str, user: dict = Depends(current_user)):
    """确认技术工艺总结(定稿)。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc = _load_summary_doc(project_id)
    if not doc.overview or not doc.conclusion:
        raise HTTPException(400, "总结概述和总体结论完整后才能确认定稿")
    who = user.get("username", "system")
    doc.confirmed = True
    doc.confirmed_by = who
    doc.confirmed_at = _now_str()
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_summary(project_id, d, author=who)
    return {"summary": d}


@app.post("/api/projects/{project_id}/summary/timing")
def summary_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    doc = _load_summary_doc(project_id)
    if action == "start":
        doc.timing.status = "in_progress"
        doc.timing.started_at = _now_str()
        doc.timing.finished_at = None
        doc.timing.completed = False
    else:
        if not doc.timing.started_at:
            doc.timing.started_at = _now_str()
        doc.timing.status = "done"
        doc.timing.finished_at = _now_str()
        doc.timing.completed = True
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_summary(project_id, d, author=user.get("username", "system"))
    return {"summary": d}


@app.get("/api/projects/{project_id}/summary.html")
def export_summary_html(project_id: str):
    """导出技术工艺总结为可打印 HTML 文档(含各步全部结构化内容)。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    agg = summary_svc.aggregate(project_id)
    return Response(content=summary_svc.render_html(agg), media_type="text/html; charset=utf-8")


@app.get("/api/projects/{project_id}/summary.md")
def export_summary_md(project_id: str):
    """导出技术工艺总结为 Markdown 文档。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    agg = summary_svc.aggregate(project_id)
    return Response(
        content=summary_svc.render_markdown(agg).encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="summary_{project_id}.md"'},
    )


# --------------------------------------------------------------------------- #
# 技术工艺 / 报价 记录(「结束」步:最终确认并录入到管理列表)
# --------------------------------------------------------------------------- #
@app.get("/api/techprocess/records")
def list_records_ep(biz: str = ""):
    """管理列表数据源。biz=tech|quote 过滤;留空返回全部。"""
    items = store.list_records()
    if biz:
        items = [r for r in items if r.get("biz") == biz]
    items.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return {"records": items}


def _require_record_edit(user: dict, record: dict | None = None, project_id: str = "") -> None:
    """技术工艺/报价管理记录也遵循项目归属，不能绕过项目级写入守卫。"""
    role = user.get("role")
    if role in {"admin", "process_manager"}:
        return
    if role != "engineer":
        raise HTTPException(403, "需要工艺工程师、技术经理或管理员权限")
    if record and record.get("owner") != user.get("username"):
        raise HTTPException(403, "工艺工程师只能修改本人创建的管理记录")
    target_project = project_id or (record or {}).get("project_id", "")
    if target_project:
        meta = _workflow_project(target_project)
        if not auth.can_edit_project(user, meta):
            raise HTTPException(403, "工艺工程师只能操作本人创建项目的管理记录")


@app.post("/api/techprocess/records")
def register_record_ep(body: TechProcessRecord, user: dict = Depends(current_user)):
    """「结束」步最终确认:把当前技术工艺/报价录入到对应管理列表(按 project_id+biz 幂等)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    biz = body.biz or "tech"
    records = store.list_records()
    _require_record_edit(user, project_id=body.project_id)

    # 名称: 优先入参,否则取项目 IR 的器件名
    name = body.name
    if (not name or name == "未命名技术工艺") and body.project_id:
        ir = store.load_ir(body.project_id) or {}
        name = ir.get("device_name") or name
    name = name or "未命名技术工艺"

    # 状态: 优先入参,否则按总结是否定稿
    status = body.status
    if not status and body.project_id:
        summ = store.load_summary(body.project_id) or {}
        status = "已定稿" if summ.get("confirmed") else "已录入"
    status = status or "已录入"

    who = user.get("username", "system")
    # 幂等: 同一 project_id + biz 已录入则更新
    existing = next(
        (r for r in records
         if body.project_id and r.get("project_id") == body.project_id and r.get("biz") == biz),
        None,
    )
    if existing:
        _require_record_edit(user, existing, body.project_id)
        existing.update({"name": name, "status": status, "note": body.note or existing.get("note"),
                         "updated_at": _now_str()})
        return {"record": store.save_record(existing)}

    prefix = "BJ" if biz == "quote" else "GY"
    seq = sum(1 for r in records if r.get("biz") == biz) + 1
    rec = {
        "id": "rec_" + __import__("uuid").uuid4().hex[:8],
        "code": f"{prefix}{now_cst_str('%Y%m%d')}-{seq:03d}",
        "name": name, "project_id": body.project_id, "biz": biz, "status": status,
        "owner": who, "created_at": _now_str(), "note": body.note, "editable": True,
    }
    return {"record": store.save_record(rec)}


@app.delete("/api/techprocess/records/{record_id}")
def delete_record_ep(record_id: str, user: dict = Depends(current_user)):
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    record = store.get_record(record_id)
    if not record:
        raise HTTPException(404, "管理记录不存在")
    _require_record_edit(user, record)
    store.delete_record(record_id)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# 成本测算(报价流程第 1 步)
# --------------------------------------------------------------------------- #
def _load_costest(project_id: str) -> CostEstimate:
    saved = store.load_costest(project_id)
    doc = CostEstimate(**saved) if saved else CostEstimate(project_id=project_id)
    doc.project_id = project_id
    return doc


def _save_costest_with_totals(doc: CostEstimate, project_id: str, author: str) -> dict:
    doc.project_id = project_id
    doc.totals = costest.compute(doc.model_dump())
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_costest(project_id, d, author=author)
    return d


@app.get("/api/projects/{project_id}/costest")
def get_costest(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    saved = store.load_costest(project_id)
    totals = costest.compute(saved).model_dump() if saved else None
    return {"costest": saved, "totals": totals}


@app.post("/api/projects/{project_id}/costest/recommend")
def recommend_costest(project_id: str, note: str = Form(""),
                      user: dict = Depends(current_user)):
    """Claude 联网依据 IR/材料/制造工艺与 BOM 做材料/制造/技术附加成本测算(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    material_plan = store.load_material(project_id)
    manufacturing_plan = store.load_manufacturing(project_id)
    author = user.get("username", "system")
    # 与其它 recommend 路由一致：提交时先取输入+草稿摘要，任务执行期间若被改过就丢弃旧结果。
    dependency_hash = _digest_value((
        ir_dict, material_plan, manufacturing_plan, store.load_costest(project_id),
    ))

    def job():
        tasks.report_progress("正在调用模型生成完整成本测算")
        rec = costest.recommend(ir=ir, material_plan=material_plan,
                                manufacturing_plan=manufacturing_plan, note=note, web=True)
        tasks.report_progress("成本测算已返回，正在重算合计并检查依赖")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_material(project_id),
             store.load_manufacturing(project_id), store.load_costest(project_id)),
            "成本测算输入或成本草稿",
        )
        doc = _load_costest(project_id)
        previous = doc.model_copy(deep=True)
        doc.material_costs = rec.material_costs
        doc.manufacturing_costs = rec.manufacturing_costs
        doc.technical_costs = rec.technical_costs
        doc.market_notes = rec.market_notes
        doc.summary = rec.summary
        doc.assumptions = rec.assumptions
        doc.open_questions = rec.open_questions
        doc.search_sources = rec.search_sources
        _sync_confirmation(previous, doc)
        d = _save_costest_with_totals(doc, project_id, author)
        if _business_changed(previous, doc):
            store.invalidate_confirmations(
                project_id, ["pricing", "negotiation", "pricenego", "approval"],
                "成本测算已更新", author,
            )
        return {"costest": d, "totals": d["totals"]}

    return {"task_id": tasks.submit(
        project_id, "costest_recommend", job,
        dedup_key=_task_key("costest_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/costest")
def update_costest(project_id: str, doc: CostEstimate, user: dict = Depends(current_user)):
    """保存人工编辑后的成本测算(平台重算合计)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_costest(project_id)
    changed = _business_changed(current, doc)
    _sync_confirmation(current, doc)
    d = _save_costest_with_totals(doc, project_id, user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id, ["pricing", "negotiation", "pricenego", "approval"],
            "成本测算已人工修改", user.get("username", "system"),
        )
    return {"costest": d, "totals": d["totals"]}


@app.post("/api/projects/{project_id}/costest/confirm")
def confirm_costest(project_id: str, user: dict = Depends(current_user)):
    """确认成本测算(定稿,供后续定价使用)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc = _load_costest(project_id)
    if not (doc.material_costs or doc.manufacturing_costs or doc.technical_costs):
        raise HTTPException(400, "尚无成本明细,请先做 AI 测算")
    who = user.get("username", "system")
    doc.confirmed = True
    doc.confirmed_by = who
    doc.confirmed_at = _now_str()
    d = _save_costest_with_totals(doc, project_id, who)
    return {"costest": d, "totals": d["totals"]}


@app.post("/api/projects/{project_id}/costest/timing")
def costest_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    doc = _load_costest(project_id)
    if action == "start":
        doc.timing.status = "in_progress"
        doc.timing.started_at = _now_str()
        doc.timing.finished_at = None
        doc.timing.completed = False
    else:
        if not doc.timing.started_at:
            doc.timing.started_at = _now_str()
        doc.timing.status = "done"
        doc.timing.finished_at = _now_str()
        doc.timing.completed = True
    d = _save_costest_with_totals(doc, project_id, user.get("username", "system"))
    return {"costest": d, "totals": d["totals"]}


@app.get("/api/projects/{project_id}/costest.csv")
def export_costest_csv(project_id: str):
    """导出成本测算为 CSV。"""
    saved = store.load_costest(project_id)
    if not saved:
        # 项目存在但还没有成本测算：回 200 的空表。读接口不再用 404 表达「没数据」——
        # 批次 7 起 404 专指「项目不可见 / 不存在」，两者混用会让人分不清是没数据还是没权限。
        return Response(content=costest.to_csv({}).encode("utf-8"), media_type="text/csv",
                        headers={"Content-Disposition":
                                 f'attachment; filename="costest_{project_id}.csv"'})
    return Response(
        content=costest.to_csv(saved).encode("utf-8"),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="costest_{project_id}.csv"'},
    )


# --------------------------------------------------------------------------- #
# 定价方案制定(报价流程第 2 步:成本加成 + 维度调整 + 销售测算→财务审核)
# --------------------------------------------------------------------------- #
class FinanceReview(BaseModel):
    decision: str = "approve"   # approve | reject
    comment: str = ""


def _load_pricing(project_id: str) -> PricingPlan:
    saved = store.load_pricing(project_id)
    doc = PricingPlan(**saved) if saved else PricingPlan(project_id=project_id)
    doc.project_id = project_id
    return doc


def _save_pricing_calc(doc: PricingPlan, project_id: str, author: str) -> dict:
    doc.project_id = project_id
    doc.updated_at = _now_str()
    d = pricing.compute(doc.model_dump())
    store.save_pricing(project_id, d, author=author)
    return d


@app.get("/api/projects/{project_id}/pricing")
def get_pricing(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"pricing": store.load_pricing(project_id)}


@app.post("/api/projects/{project_id}/pricing/recommend")
def recommend_pricing(project_id: str, note: str = Form(""),
                      user: dict = Depends(current_user)):
    """Claude 联网依据成本测算结果给出费率与各维度调整建议(异步);平台确定性算价。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    ce = store.load_costest(project_id)
    if ce and not ce.get("totals"):
        ce["totals"] = costest.compute(ce).model_dump()
    author = user.get("username", "system")
    dependency_hash = _digest_value((ir_dict, ce, store.load_pricing(project_id)))

    def job():
        tasks.report_progress("正在调用模型生成定价建议")
        rec = pricing.recommend(ir=ir, costest=ce, note=note, web=True)
        tasks.report_progress("定价建议已返回，正在按成本基数计算报价")
        current_costest = store.load_costest(project_id)
        if current_costest and not current_costest.get("totals"):
            current_costest = dict(current_costest)
            current_costest["totals"] = costest.compute(current_costest).model_dump()
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), current_costest, store.load_pricing(project_id)),
            "定价输入或定价草稿",
        )
        doc = _load_pricing(project_id)
        previous = doc.model_copy(deep=True)
        # 成本基数取自成本测算(确定性)
        totals = (ce or {}).get("totals") or {}
        doc.costs.material_cost = float(totals.get("material_total", 0) or 0)
        doc.costs.production_cost = float(totals.get("manufacturing_total", 0) or 0)
        if rec.management_rate is not None:
            doc.costs.management_rate = rec.management_rate
        if rec.markup_rate is not None:
            doc.markup_rate = rec.markup_rate
        doc.factors = rec.factors
        doc.summary = rec.summary
        doc.assumptions = rec.assumptions
        doc.open_questions = rec.open_questions
        doc.search_sources = rec.search_sources
        pricing_changed = not _sync_pricing_approval(previous, doc)
        d = _save_pricing_calc(doc, project_id, author)
        if pricing_changed:
            store.invalidate_confirmations(
                project_id, ["negotiation", "pricenego", "approval"], "定价方案已更新", author
            )
        return {"pricing": d}

    return {"task_id": tasks.submit(
        project_id, "pricing_recommend", job,
        dedup_key=_task_key("pricing_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/pricing")
def update_pricing(project_id: str, doc: PricingPlan, user: dict = Depends(current_user)):
    """保存人工编辑后的定价方案(平台重算价格)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_pricing(project_id)
    changed = not _sync_pricing_approval(current, doc)
    d = _save_pricing_calc(doc, project_id, user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id, ["negotiation", "pricenego", "approval"],
            "定价方案已人工修改", user.get("username", "system"),
        )
    return {"pricing": d}


@app.post("/api/projects/{project_id}/pricing/submit")
def submit_pricing(project_id: str, user: dict = Depends(current_user)):
    """销售测算后提交财务审核。"""
    _require(user, auth.WRITE_ROLES, "需要销售/工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc = _load_pricing(project_id)
    if not doc.factors and not doc.costs.material_cost:
        raise HTTPException(400, "尚无定价内容,请先生成/填写定价方案")
    who = user.get("username", "system")
    doc.approval.status = "submitted"
    doc.approval.sales_by = who
    doc.approval.sales_at = _now_str()
    doc.approval.finance_by = None
    doc.approval.finance_at = None
    doc.approval.finance_comment = None
    d = _save_pricing_calc(doc, project_id, who)
    return {"pricing": d}


@app.post("/api/projects/{project_id}/pricing/review")
def review_pricing(project_id: str, body: FinanceReview, user: dict = Depends(current_user)):
    """财务负责人审核确认(通过/驳回)。"""
    _require(user, auth.FINANCE_ROLES, "需要财务负责人或管理员权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision 必须为 approve 或 reject")
    doc = _load_pricing(project_id)
    if doc.approval.status != "submitted":
        raise HTTPException(400, "当前状态不可审核(需先由销售提交)")
    who = user.get("username", "system")
    doc.approval.status = "approved" if body.decision == "approve" else "rejected"
    doc.approval.finance_by = who
    doc.approval.finance_at = _now_str()
    doc.approval.finance_comment = body.comment
    d = _save_pricing_calc(doc, project_id, who)
    return {"pricing": d}


@app.post("/api/projects/{project_id}/pricing/timing")
def pricing_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    doc = _load_pricing(project_id)
    if action == "start":
        doc.timing.status = "in_progress"
        doc.timing.started_at = _now_str()
        doc.timing.finished_at = None
        doc.timing.completed = False
    else:
        if not doc.timing.started_at:
            doc.timing.started_at = _now_str()
        doc.timing.status = "done"
        doc.timing.finished_at = _now_str()
        doc.timing.completed = True
    d = _save_pricing_calc(doc, project_id, user.get("username", "system"))
    return {"pricing": d}


# --------------------------------------------------------------------------- #
# 商务及谈判策略(报价流程第 3 步)
# --------------------------------------------------------------------------- #
def _load_negotiation(project_id: str) -> NegotiationPlan:
    saved = store.load_negotiation(project_id)
    doc = NegotiationPlan(**saved) if saved else NegotiationPlan(project_id=project_id)
    doc.project_id = project_id
    return doc


@app.get("/api/projects/{project_id}/negotiation")
def get_negotiation(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"negotiation": store.load_negotiation(project_id)}


@app.post("/api/projects/{project_id}/negotiation/recommend")
def recommend_negotiation(project_id: str, note: str = Form(""),
                          user: dict = Depends(current_user)):
    """Claude 依据定价结果产出商务条款与分客户类型谈判策略/授权(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    pricing_plan = store.load_pricing(project_id)
    author = user.get("username", "system")
    dependency_hash = _digest_value((ir_dict, pricing_plan, store.load_negotiation(project_id)))

    def job():
        tasks.report_progress("正在调用模型生成商务谈判策略")
        rec = negotiation.recommend(ir=ir, pricing=pricing_plan, note=note, web=False)
        tasks.report_progress("谈判策略已返回，正在校验报价依赖")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_pricing(project_id), store.load_negotiation(project_id)),
            "商务策略输入或谈判策略草稿",
        )
        doc = _load_negotiation(project_id)
        previous = doc.model_copy(deep=True)
        doc.terms = rec.terms
        doc.strategies = rec.strategies
        doc.summary = rec.summary
        doc.assumptions = rec.assumptions
        doc.open_questions = rec.open_questions
        doc.search_sources = rec.search_sources
        _sync_confirmation(previous, doc)
        doc.updated_at = _now_str()
        d = doc.model_dump()
        store.save_negotiation(project_id, d, author=author)
        if _business_changed(previous, doc):
            store.invalidate_confirmations(
                project_id, ["pricenego", "approval"], "商务谈判策略已更新", author
            )
        return {"negotiation": d}

    return {"task_id": tasks.submit(
        project_id, "negotiation_recommend", job,
        dedup_key=_task_key("negotiation_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/negotiation")
def update_negotiation(project_id: str, doc: NegotiationPlan, user: dict = Depends(current_user)):
    """保存人工编辑后的商务及谈判策略。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_negotiation(project_id)
    changed = _business_changed(current, doc)
    _sync_confirmation(current, doc)
    doc.project_id = project_id
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_negotiation(project_id, d, author=user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id, ["pricenego", "approval"], "商务谈判策略已人工修改",
            user.get("username", "system"),
        )
    return {"negotiation": d}


@app.post("/api/projects/{project_id}/negotiation/confirm")
def confirm_negotiation(project_id: str, user: dict = Depends(current_user)):
    """确认商务及谈判策略。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc = _load_negotiation(project_id)
    if not (doc.terms or doc.strategies):
        raise HTTPException(400, "尚无内容,请先做 AI 生成")
    who = user.get("username", "system")
    doc.confirmed = True
    doc.confirmed_by = who
    doc.confirmed_at = _now_str()
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_negotiation(project_id, d, author=who)
    return {"negotiation": d}


@app.post("/api/projects/{project_id}/negotiation/timing")
def negotiation_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    doc = _load_negotiation(project_id)
    if action == "start":
        doc.timing.status = "in_progress"
        doc.timing.started_at = _now_str()
        doc.timing.finished_at = None
        doc.timing.completed = False
    else:
        if not doc.timing.started_at:
            doc.timing.started_at = _now_str()
        doc.timing.status = "done"
        doc.timing.finished_at = _now_str()
        doc.timing.completed = True
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_negotiation(project_id, d, author=user.get("username", "system"))
    return {"negotiation": d}


# --------------------------------------------------------------------------- #
# 价格协商及谈判(报价流程第 4 步)
# --------------------------------------------------------------------------- #
def _load_pricenego(project_id: str) -> PriceNegotiation:
    saved = store.load_pricenego(project_id)
    doc = PriceNegotiation(**saved) if saved else PriceNegotiation(project_id=project_id)
    doc.project_id = project_id
    return doc


@app.get("/api/projects/{project_id}/pricenego")
def get_pricenego(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"pricenego": store.load_pricenego(project_id)}


@app.post("/api/projects/{project_id}/pricenego/recommend")
def recommend_pricenego(project_id: str, note: str = Form(""),
                        user: dict = Depends(current_user)):
    """Claude 产出初步报价单/阶梯价格/调价联动/特殊条款建议(异步,联网取行情)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    pricing_plan = store.load_pricing(project_id)
    negotiation_plan = store.load_negotiation(project_id)
    author = user.get("username", "system")
    dependency_hash = _digest_value((
        ir_dict, pricing_plan, negotiation_plan, store.load_pricenego(project_id),
    ))

    def job():
        tasks.report_progress("正在调用模型生成价格协商建议")
        rec = pricenego.recommend(ir=ir, pricing=pricing_plan,
                                  negotiation=negotiation_plan, note=note, web=True)
        tasks.report_progress("价格协商建议已返回，正在校验调整项")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_pricing(project_id),
             store.load_negotiation(project_id), store.load_pricenego(project_id)),
            "价格协商输入或协商草稿",
        )
        doc = _load_pricenego(project_id)
        previous = doc.model_copy(deep=True)
        doc.initial_quote = rec.initial_quote
        doc.tiered_prices = rec.tiered_prices
        doc.price_linkage = rec.price_linkage
        doc.special_terms = rec.special_terms
        doc.summary = rec.summary
        doc.assumptions = rec.assumptions
        doc.open_questions = rec.open_questions
        doc.search_sources = rec.search_sources
        _sync_confirmation(previous, doc)
        doc.updated_at = _now_str()
        d = doc.model_dump()
        store.save_pricenego(project_id, d, author=author)
        if _business_changed(previous, doc):
            store.invalidate_confirmations(project_id, ["approval"], "价格协商内容已更新", author)
        return {"pricenego": d}

    return {"task_id": tasks.submit(
        project_id, "pricenego_recommend", job,
        dedup_key=_task_key("pricenego_recommend", dependency_hash, note),
    )}


@app.put("/api/projects/{project_id}/pricenego")
def update_pricenego(project_id: str, doc: PriceNegotiation, user: dict = Depends(current_user)):
    """保存人工编辑后的价格协商(含新增的协商轮次)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    current = _load_pricenego(project_id)
    changed = _business_changed(current, doc)
    _sync_confirmation(current, doc)
    doc.project_id = project_id
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_pricenego(project_id, d, author=user.get("username", "system"))
    if changed:
        store.invalidate_confirmations(
            project_id, ["approval"], "价格协商内容已人工修改", user.get("username", "system")
        )
    return {"pricenego": d}


@app.post("/api/projects/{project_id}/pricenego/confirm")
def confirm_pricenego(project_id: str, user: dict = Depends(current_user)):
    """确认价格协商结果(达成)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc = _load_pricenego(project_id)
    who = user.get("username", "system")
    doc.confirmed = True
    doc.confirmed_by = who
    doc.confirmed_at = _now_str()
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_pricenego(project_id, d, author=who)
    return {"pricenego": d}


@app.post("/api/projects/{project_id}/pricenego/timing")
def pricenego_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    doc = _load_pricenego(project_id)
    if action == "start":
        doc.timing.status = "in_progress"
        doc.timing.started_at = _now_str()
        doc.timing.finished_at = None
        doc.timing.completed = False
    else:
        if not doc.timing.started_at:
            doc.timing.started_at = _now_str()
        doc.timing.status = "done"
        doc.timing.finished_at = _now_str()
        doc.timing.completed = True
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_pricenego(project_id, d, author=user.get("username", "system"))
    return {"pricenego": d}


# --------------------------------------------------------------------------- #
# 报价审批与决策(报价流程第 6 步:分级审批)
# --------------------------------------------------------------------------- #
class ApprovalAction(BaseModel):
    decision: str = "approve"   # approve | reject
    comment: str = ""


def _load_approval(project_id: str) -> QuoteApproval:
    saved = store.load_approval(project_id)
    doc = QuoteApproval(**saved) if saved else QuoteApproval(project_id=project_id)
    doc.project_id = project_id
    return doc


@app.get("/api/projects/{project_id}/approval")
def get_approval(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"approval": store.load_approval(project_id)}


@app.post("/api/projects/{project_id}/approval/recommend")
def recommend_approval(project_id: str, note: str = Form(""),
                       user: dict = Depends(current_user)):
    """Claude 研判应走的审批级别;平台据级别生成审批链(异步)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    ir_dict = store.load_ir(project_id)
    ir = DesignIR(**ir_dict) if ir_dict else None
    pricing_plan = store.load_pricing(project_id)
    pn = store.load_pricenego(project_id)
    author = user.get("username", "system")
    dependency_hash = _digest_value((ir_dict, pricing_plan, pn, store.load_approval(project_id)))

    def job():
        tasks.report_progress("正在调用模型生成审批定级建议")
        rec = approval_svc.recommend(ir=ir, pricing=pricing_plan, pricenego=pn, note=note, web=False)
        tasks.report_progress("审批建议已返回，正在生成审批节点")
        _assert_dependencies_unchanged(
            dependency_hash,
            (store.load_ir(project_id), store.load_pricing(project_id),
             store.load_pricenego(project_id), store.load_approval(project_id)),
            "审批定级输入或审批草稿",
        )
        doc = _load_approval(project_id)
        doc.level = rec.level
        doc.level_reason = rec.level_reason
        doc.classification = rec.classification
        doc.summary = rec.summary
        doc.assumptions = rec.assumptions
        doc.open_questions = rec.open_questions
        doc.search_sources = rec.search_sources
        # 重置审批链为新级别(草稿态)
        doc.chain = approval_svc.build_chain(rec.level)
        doc.status = "draft"
        doc.decision_note = None
        doc.updated_at = _now_str()
        d = doc.model_dump()
        store.save_approval(project_id, d, author=author)
        return {"approval": d}

    return {"task_id": tasks.submit(
        project_id, "approval_recommend", job,
        dedup_key=_task_key("approval_recommend", dependency_hash, note),
    )}


@app.post("/api/projects/{project_id}/approval/level")
def set_approval_level(project_id: str, level: int, user: dict = Depends(current_user)):
    """人工设定/调整审批级别(重建审批链,回到草稿态)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if level not in (1, 2, 3):
        raise HTTPException(400, "level 必须为 1/2/3")
    minimum_level, reason = approval_svc.determine_level(
        store.load_pricing(project_id), store.load_pricenego(project_id)
    )
    if level < minimum_level:
        raise HTTPException(409, f"审批级别不得低于平台风险矩阵确定的 L{minimum_level}：{reason}")
    doc = _load_approval(project_id)
    doc.level = level
    doc.chain = approval_svc.build_chain(level)
    doc.status = "draft"
    doc.decision_note = None
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_approval(project_id, d, author=user.get("username", "system"))
    return {"approval": d}


@app.post("/api/projects/{project_id}/approval/submit")
def submit_approval(project_id: str, user: dict = Depends(current_user)):
    """提交内部审批(进入审批中,首个节点待审)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    doc = _load_approval(project_id)
    minimum_level, reason = approval_svc.determine_level(
        store.load_pricing(project_id), store.load_pricenego(project_id)
    )
    if doc.level < minimum_level:
        raise HTTPException(409, f"当前审批级别低于最低 L{minimum_level}：{reason}")
    if not doc.chain:
        doc.chain = approval_svc.build_chain(doc.level)
    doc.status = "in_review"
    for i, n in enumerate(doc.chain):
        n.status = "pending" if i == 0 else "waiting"
        n.approver = None
        n.at = None
        n.comment = None
    doc.decision_note = None
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_approval(project_id, d, author=user.get("username", "system"))
    return {"approval": d}


class ApprovalSend(BaseModel):
    approvers: list[str] = []
    comment: str | None = None


@app.post("/api/projects/{project_id}/approval/send")
def send_approval(project_id: str, body: ApprovalSend, user: dict = Depends(current_user)):
    """选择审批人并「发送审批流」:标记审批中、记录送审人与时间;审批(通过/驳回)由审批人在别处完成,
    不在本报价单表单内做决策。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    approvers = [a.strip() for a in (body.approvers or []) if a and a.strip()]
    if not approvers:
        raise HTTPException(400, "请至少选择一位审批人")
    from .models.approval import ApprovalNode
    doc = _load_approval(project_id)
    required_roles = [node.role for node in approval_svc.build_chain(doc.level)]
    if approvers != required_roles:
        raise HTTPException(409, "审批链必须严格匹配当前级别：" + " → ".join(required_roles))
    doc.approvers = approvers
    doc.sent_at = _now_str()
    doc.status = "in_review"
    # 用所选审批人直接生成审批链(首个待审,其余待前序)
    doc.chain = [ApprovalNode(seq=i + 1, role=r, status=("pending" if i == 0 else "waiting"))
                 for i, r in enumerate(approvers)]
    if body.comment:
        doc.decision_note = body.comment
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_approval(project_id, d, author=user.get("username", "system"))
    return {"approval": d}


@app.post("/api/projects/{project_id}/approval/act")
def act_approval(project_id: str, body: ApprovalAction, user: dict = Depends(current_user)):
    """逐级审批:对当前待审节点 通过/驳回。需审核/管理权限。"""
    _require(user, auth.QUOTE_APPROVAL_ROLES, "需要当前报价审批节点对应角色或管理员权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision 必须为 approve 或 reject")
    doc = _load_approval(project_id)
    if doc.status != "in_review":
        raise HTTPException(400, "当前不在审批中(请先提交审批)")
    idx = next((i for i, n in enumerate(doc.chain) if n.status == "pending"), None)
    if idx is None:
        raise HTTPException(400, "没有待审节点")
    who = user.get("username", "system")
    node = doc.chain[idx]
    user_role = user.get("role")
    expected_role = auth.QUOTE_NODE_BY_ROLE.get(user_role)
    if user_role != "admin" and expected_role != node.role:
        raise HTTPException(403, f"当前节点需要“{node.role}”审批")
    if user_role != "admin" and any(item.approver == who for item in doc.chain[:idx]):
        raise HTTPException(409, "同一人员不能连续审批多个报价授权节点")
    node.approver = who
    node.at = _now_str()
    node.comment = body.comment
    if body.decision == "reject":
        node.status = "rejected"
        doc.status = "rejected"
        doc.decision_note = f"{node.role} 驳回:{body.comment or ''}"
    else:
        node.status = "approved"
        if idx + 1 < len(doc.chain):
            doc.chain[idx + 1].status = "pending"
        else:
            doc.status = "approved"
            doc.decision_note = "全部审批通过,可正式报价/签约"
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_approval(project_id, d, author=who)
    return {"approval": d}


@app.post("/api/projects/{project_id}/approval/timing")
def approval_timing(project_id: str, action: str, user: dict = Depends(current_user)):
    """记录该步骤起止时间与是否完成(action=start|finish)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    if action not in ("start", "finish"):
        raise HTTPException(400, "action 必须为 start 或 finish")
    doc = _load_approval(project_id)
    if action == "start":
        doc.timing.status = "in_progress"
        doc.timing.started_at = _now_str()
        doc.timing.finished_at = None
        doc.timing.completed = False
    else:
        if not doc.timing.started_at:
            doc.timing.started_at = _now_str()
        doc.timing.status = "done"
        doc.timing.finished_at = _now_str()
        doc.timing.completed = True
    doc.updated_at = _now_str()
    d = doc.model_dump()
    store.save_approval(project_id, d, author=user.get("username", "system"))
    return {"approval": d}


# --------------------------------------------------------------------------- #
# 版本管理 + 校核审签(PRD 6.5)
# --------------------------------------------------------------------------- #
@app.get("/api/projects/{project_id}/versions")
def list_versions(project_id: str):
    """版本快照列表(每次解析/校验/拆解/编辑/恢复自动留版),含审签状态。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"versions": store.list_versions(project_id)}


@app.get("/api/projects/{project_id}/versions/{version}")
def get_version(project_id: str, version: int):
    """某一版本的完整快照(含 IR)。"""
    rec = store.get_version(project_id, version)
    if not rec:
        raise HTTPException(404, "版本不存在")
    return rec


@app.get("/api/projects/{project_id}/versions/{v_from}/diff/{v_to}")
def diff_versions(project_id: str, v_from: int, v_to: int):
    """两个版本的结构化差异(谁把哪个参数从多少改成多少)。"""
    a = store.get_version(project_id, v_from)
    b = store.get_version(project_id, v_to)
    if not a or not b:
        raise HTTPException(404, "版本不存在")
    return {
        "from": v_from, "to": v_to,
        "diff": versioning.diff_ir(a.get("ir"), b.get("ir")),
    }


@app.post("/api/projects/{project_id}/versions/{version}/submit")
def submit_version(project_id: str, version: int,
                   body: ReviewAction = Body(default=ReviewAction()),
                   user: dict = Depends(current_user)):
    """送审: draft -> in_review(提交人=当前登录用户)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    rec = store.set_version_status(project_id, version, "in_review",
                                   user.get("username", "system"), body.comment)
    if not rec:
        raise HTTPException(404, "版本不存在")
    return rec


@app.post("/api/projects/{project_id}/versions/{version}/approve")
def approve_version(project_id: str, version: int,
                    body: ReviewAction = Body(default=ReviewAction()),
                    user: dict = Depends(current_user)):
    """审签通过: -> approved(审签人=当前登录用户,实名留痕)。"""
    _require(user, auth.REVIEW_ROLES, "需要校核/审签或管理员权限")
    rec = store.set_version_status(project_id, version, "approved",
                                   user.get("username", "system"), body.comment)
    if not rec:
        raise HTTPException(404, "版本不存在")
    return rec


@app.post("/api/projects/{project_id}/versions/{version}/reject")
def reject_version(project_id: str, version: int,
                   body: ReviewAction = Body(default=ReviewAction()),
                   user: dict = Depends(current_user)):
    """审签驳回: -> rejected(审签人=当前登录用户,实名留痕)。"""
    _require(user, auth.REVIEW_ROLES, "需要校核/审签或管理员权限")
    rec = store.set_version_status(project_id, version, "rejected",
                                   user.get("username", "system"), body.comment)
    if not rec:
        raise HTTPException(404, "版本不存在")
    return rec


@app.post("/api/projects/{project_id}/versions/{version}/restore")
def restore_version(project_id: str, version: int, user: dict = Depends(current_user)):
    """把某历史版本的 IR 恢复为当前 IR(并自动留一个 restored 新版本)。"""
    _require(user, auth.WRITE_ROLES, "需要工程师及以上权限")
    rec = store.get_version(project_id, version)
    if not rec or not rec.get("ir"):
        raise HTTPException(404, "版本不存在")
    store.save_ir(project_id, rec["ir"], stage=f"restored_from_v{version}",
                  author=user.get("username", "system"))
    store.audit(project_id, "restore_version", {"from": version, "by": user.get("username")})
    return rec["ir"]


# --------------------------------------------------------------------------- #
# 异步任务(轮询状态/进度/结果)
# --------------------------------------------------------------------------- #
@app.get("/api/projects/{project_id}/tasks/{task_id}")
def get_task(project_id: str, task_id: str):
    rec = store.get_task(project_id, task_id)
    if not rec:
        raise HTTPException(404, "任务不存在")
    # 历史任务没有 trace_id：稳定返回空串（不迁移、不清洗），前端失败块据此写「无」。
    return {**rec, "trace_id": str(rec.get("trace_id") or "")}


@app.post("/api/projects/{project_id}/tasks/{task_id}/cancel")
def cancel_task(project_id: str, task_id: str,
                payload: Optional[dict] = Body(default=None),
                user: dict = Depends(current_user)):
    """用户主动取消一个排队中 / 进行中的任务（批次 9 §7.3）。

    项目级写权限由应用级的 project_write_guard（批次 7）统一拦：不可见 / 归档按项目
    不存在处理，角色不够按 403；这里只判任务本身是否存在（404「任务不存在」）。
    """
    reason = str((payload or {}).get("reason") or "") if isinstance(payload, dict) else ""
    result = tasks.cancel_task(project_id, task_id,
                               actor=str((user or {}).get("username") or ""),
                               reason=reason)
    if not result.get("ok"):
        raise HTTPException(404, "任务不存在")
    return result


@app.get("/api/projects/{project_id}/tasks")
def list_tasks(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"tasks": store.list_tasks(project_id)}


@app.get("/api/projects/{project_id}/tree")
def structure_tree(project_id: str):
    """层级结构树: 设备-总成-零件。"""
    ir_dict = store.load_ir(project_id)
    if not ir_dict:
        raise HTTPException(404, "请先解析(parse)得到 IR")
    return tree.build_tree(DesignIR(**ir_dict))


#: 整份隐藏（返回 null）只剩三种情况（C3）：输入被替换 / 零件增删或结构变化 / legacy
#: 无法逐件判断。单纯的字段修改一律只标相关零件。
_STALE_REASON_STRUCTURE = "零件已增删或结构变化，逐件指纹对不上号，需整批重新生成"
_STALE_REASON_LEGACY = "老结果只有整份 source_ir_hash、没有逐件指纹（legacy），无法逐件判断是否过期"
_STALE_REASON_INPUT = "输入资料已替换（input_revision 已变化），结果需重新生成"


def _result_part_ids(doc: dict) -> list:
    return [str(entry.get("part_id") or "") for entry in (doc or {}).get("parts") or []]


def _decorate_result(doc, current_parts: dict, current_hash: str, meta: dict):
    """读时装饰逐件过期状态（不写回存储），返回 (装饰后的文档, 该文档的状态)。

    过期条目**不清空** step_url / stl_url / views / dxf —— 文件还在，用户要能下载对比；
    过期只是标记（C2）。
    """
    status = {"whole_stale": False, "reason": "", "parts_stale": [],
              "stale_attributes": {}, "legacy": False}
    if not doc:
        return doc, status
    decorated = copy.deepcopy(doc)
    entries = decorated.get("parts") or []
    has_fingerprints = bool(entries) and all(
        str(entry.get("source_part_hash") or "") and str(entry.get("source_attr_hash") or "")
        for entry in entries)
    if not has_fingerprints:
        # 迁移兜底（C7）：老结果只有整份 source_ir_hash，没有逐件指纹。
        status["legacy"] = True
        top = str(decorated.get("source_ir_hash") or "")
        if top:
            if top == current_hash:
                return decorated, status      # 哈希一致 → 判为有效，保持可读提示
            status["whole_stale"] = True
            status["reason"] = _STALE_REASON_LEGACY
            return None, status
        if meta.get("derived_results_stale"):
            status["whole_stale"] = True
            status["reason"] = str(meta.get("derived_results_stale_reason") or _STALE_REASON_INPUT)
            return None, status
        return decorated, status
    recorded_input = decorated.get("input_revision")
    if recorded_input is not None and int(recorded_input) != int(meta.get("input_revision") or 1):
        status["whole_stale"] = True
        status["reason"] = _STALE_REASON_INPUT
        return None, status
    if sorted(_result_part_ids(decorated)) != sorted(current_parts.keys()):
        status["whole_stale"] = True
        status["reason"] = _STALE_REASON_STRUCTURE
        return None, status
    for entry in entries:
        part_id = str(entry.get("part_id") or "")
        part = current_parts.get(part_id)
        stored_shape = str(entry.get("source_part_hash") or "")
        stored_attr = str(entry.get("source_attr_hash") or "")
        if part is None:
            entry["stale"] = False
            entry["stale_reason"] = "legacy"
            entry["stale_attributes"] = []
            continue
        attributes = []
        if stored_attr and stored_attr != part_versions.part_attribute_fingerprint(part):
            # 形状没变、派生数值会变：3D / 2D 文件仍然有效，只是 mass_g 要重算（C4）。
            attributes.append("mass_g")
        if stored_shape != part_versions.part_fingerprint(part):
            # 这批文件不是当前形状生成的：旧指纹已经不能代表结果，换成当前要求值，
            # 旧值留在 generated_part_hash 里便于对比（过期不是清空）。
            entry["generated_part_hash"] = stored_shape
            entry["source_part_hash"] = ""
            entry["stale"] = True
            entry["stale_reason"] = "geometry"
            status["parts_stale"].append(part_id)
        else:
            entry["stale"] = False
            entry["stale_reason"] = ""
        entry["stale_attributes"] = attributes
        if attributes:
            status["stale_attributes"][part_id] = attributes
    return decorated, status


@app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    """项目全量状态(元数据 + IR + 几何 + 2D 图纸结果)。

    过期判定按**零件**给（改一个零件的尺寸只标那一个），整份置空只剩三种情况。
    """
    meta = store.load_meta(project_id)
    if not meta:
        raise HTTPException(404, "项目不存在")
    current_ir = store.load_ir(project_id)
    current_hash = _digest_value(current_ir or {})
    current_parts = {str(p.get("part_id") or ""): p
                     for p in (current_ir or {}).get("parts") or []}
    geometry_doc, geometry_status = _decorate_result(
        store.load_geometry_result(project_id), current_parts, current_hash, meta)
    drawings_doc, drawings_status = _decorate_result(
        store.load_drawings_result(project_id), current_parts, current_hash, meta)
    artifact_status = {
        # 既有字段一个不删（C9）
        "geometry_stale": geometry_status["whole_stale"],
        "drawings_stale": drawings_status["whole_stale"],
        # 逐件状态（C2）
        "geometry_parts_stale": geometry_status["parts_stale"],
        "drawings_parts_stale": drawings_status["parts_stale"],
        "stale_attributes": {**geometry_status["stale_attributes"],
                             **drawings_status["stale_attributes"]},
        "legacy_fingerprints": bool(geometry_status["legacy"] or drawings_status["legacy"]),
        "stale_reason": geometry_status["reason"] or drawings_status["reason"],
    }
    return {
        "meta": meta,
        "ir": current_ir,
        "geometry": geometry_doc,
        "drawings": drawings_doc,
        "artifact_status": artifact_status,
    }


@app.get("/api/projects/{project_id}/ai-results")
def get_ai_results(project_id: str):
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {
        "results": store.load_ai_result_metadata(project_id),
        "drawing_analysis": store.load_drawing_analysis(project_id),
    }


@app.get("/api/projects/{project_id}/audit")
def get_audit(project_id: str):
    """项目审计轨迹(可追溯): 每次解析/校验/生成/导出等动作的留痕。"""
    if not store.load_meta(project_id):
        raise HTTPException(404, "项目不存在")
    return {"audit": store.list_audit(project_id)}


# --------------------------------------------------------------------------- #
# 工艺评估业务流程: 接受需求 -> 图纸解析 -> 工艺评估报告汇总/审核/发布。
# 这些端点只编排和留痕既有数据，不会触发任何模型调用。
# --------------------------------------------------------------------------- #
def _workflow_project(project_id: str) -> dict:
    meta = store.load_meta(project_id)
    if not meta or meta.get("deleted_at"):
        raise HTTPException(404, "项目不存在")
    return meta


def _workflow_event(action: str, user: dict, comment: str = "") -> WorkflowReview:
    return WorkflowReview(
        action=action,
        actor=user.get("username", "system"),
        role=user.get("role", ""),
        comment=comment or "",
        at=_now_str(),
    )


_REQUIREMENT_AI_CHECK_SYSTEM = """你是半导体零部件工艺评估需求单的审核工程师。
请核对“需求表单字段”和（如有）“原始工程图”，判断信息是否足以进入需求审核。
只根据输入资料判断；图上或字段里没有明确的信息，必须标为 need_info，不能臆测。

你必须检查下列 11 项，items 中每一项的 item 必须逐字使用下列名称，顺序也必须相同：
1. 一、需求基本信息（Section A）
2. 二、客户与项目信息（Section B）
3. 三、产品技术规格（Section C）
4. 3.1 基础参数
5. 3.2 精度与性能参数
6. 3.3 应用场景
7. 3.4 图纸与技术资料
8. 四、市场与商务信息（Section D）
9. 五、项目时间计划（Section E）
10. 六、分类与标签（Section F）
11. 七、备注与附件（Section G）

输出 JSON 对象：
{
  "summary": "不超过 120 字的总体结论",
  "items": [
    {"item": "上述固定名称", "status": "ok 或 need_info", "detail": "不超过 60 字的依据或待补充项"}
  ]
}
status 只能是 ok 或 need_info。不要输出 Markdown、不要追加解释。"""


def _normalize_requirement_ai_check(rule_check: dict, result: RequirementAiCheckResult) -> dict:
    """模型输出即使少项/标签轻微偏差，也固定映射回确认页的 11 项检查表。"""
    reference = rule_check.get("items") or []
    raw = result.items or []

    def match(label: str, index: int):
        for row in raw:
            candidate = (row.item or "").strip()
            if candidate == label or (candidate and (candidate in label or label in candidate)):
                return row
        # Qwen 已被要求固定顺序；仅在数量完全一致时按顺序兜底，避免错位覆盖。
        return raw[index] if len(raw) == len(reference) else None

    rows = []
    for index, fallback in enumerate(reference):
        row = match(fallback["item"], index)
        raw_status = (row.status if row else fallback["status"]).strip().lower()
        status = "ok" if raw_status in ("ok", "确认ok", "通过", "完整") else "need_info"
        detail = (row.detail if row and row.detail else fallback["detail"]).strip()
        rows.append({"item": fallback["item"], "status": status, "detail": detail[:240]})
    summary = (result.summary or "").strip()[:500]
    if not summary:
        summary = "AI 已完成需求单检查，请根据各项结论补充或确认。"
    actual_model = str(llm_client.last_used_model() or "").strip()
    return {
        "items": rows,
        "ok": all(row["status"] == "ok" for row in rows),
        "generated_note": summary,
        "engine": "qwen",
        # 只记录本次请求实际成功使用的模型；没有返回实际标识时保持为空，不能伪装成配置默认模型。
        "model": actual_model,
        "model_source": "runtime_actual" if actual_model else "unavailable",
        "checked_at": _now_str(),
    }


def _history_key(value: object) -> str:
    """用于历史比对的保守标准化：忽略空白和常见标点，但不做模糊猜测。"""
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value or "")).casefold()


def _history_requirement_autofill(project_id: str, current: dict, extracted: dict, title: str = "") -> tuple[dict, dict]:
    """从此前需求单判定客户新旧及产品全新/迭代。

    仅采用名称或型号的精确标准化匹配，避免把相似项目误判为老客户/迭代。
    人工已经填写的值永远优先；返回的 evidence 会随需求单保存，方便追溯判定依据。
    """
    merged = {**(extracted or {}), **(current or {})}
    customer_values = [
        str(merged.get("final_customer_name") or "").strip(),
        str(merged.get("transaction_customer_name") or "").strip(),
    ]
    customer_keys = {key for value in customer_values if (key := _history_key(value))}
    product_values = [
        str(merged.get("product_model") or "").strip(),
        str(merged.get("product_name") or "").strip(),
        str(merged.get("project_name") or "").strip(),
    ]
    product_keys = {key for value in product_values if (key := _history_key(value))}

    customer_matches: list[dict] = []
    product_matches: list[dict] = []
    for row in store.list_requirements():
        previous = row.get("requirement") or {}
        previous_project = row.get("project") or {}
        if previous.get("project_id") == project_id or previous_project.get("project_id") == project_id:
            continue
        previous_data = previous.get("data") or {}
        previous_customer_keys = {
            _history_key(previous_data.get(field))
            for field in ("final_customer_name", "transaction_customer_name")
            if _history_key(previous_data.get(field))
        }
        previous_product_keys = {
            _history_key(previous_data.get(field))
            for field in ("product_model", "product_name", "project_name")
            if _history_key(previous_data.get(field))
        }
        reference = {
            "project_id": previous_project.get("project_id") or previous.get("project_id"),
            "requirement_no": previous.get("requirement_no") or "—",
            "title": previous.get("title") or previous_data.get("title") or previous_project.get("source_name") or "未命名需求",
            "created_at": previous.get("created_at") or previous_project.get("created_at") or "",
        }
        if customer_keys and customer_keys.intersection(previous_customer_keys):
            customer_matches.append(reference)
        if product_keys and product_keys.intersection(previous_product_keys):
            product_matches.append(reference)

    fields: dict[str, str] = {}
    if customer_keys and not str(current.get("customer_type") or "").strip():
        fields["customer_type"] = "old" if customer_matches else "new"
    # 产品型号、产品名称或项目名称只要与已归档需求精确对应，即视为迭代；否则是全新。
    # requirement_type 与“全新/迭代”保持一致，避免同一页面两个选择项互相矛盾。
    current_requirement_type = str(current.get("requirement_type") or "").strip()
    if product_keys:
        iteration_value = "iteration" if product_matches else "new"
        if not str(current.get("product_iteration") or "").strip():
            fields["product_iteration"] = iteration_value
        if not current_requirement_type or current_requirement_type == "工艺评估":
            fields["requirement_type"] = iteration_value

    evidence = {
        "engine": "historical_requirement_comparison",
        "compared_at": _now_str(),
        "customer_candidates": [value for value in customer_values if value],
        "product_candidates": [value for value in product_values if value],
        "customer_match_count": len(customer_matches),
        "product_match_count": len(product_matches),
        "customer_matches": customer_matches[:10],
        "product_matches": product_matches[:10],
        "decision": {
            "customer_type": fields.get("customer_type") or str(current.get("customer_type") or ""),
            "product_iteration": fields.get("product_iteration") or str(current.get("product_iteration") or ""),
            "requirement_type": fields.get("requirement_type") or current_requirement_type,
        },
    }
    return fields, evidence


def _apply_requirement_document_extraction(
    project_id: str,
    doc: RequirementDoc,
    result: RequirementDocumentExtraction,
    processed_files: list[str],
    skipped_files: list[str],
    user: dict,
) -> dict:
    """仅补齐空字段，保留首页输入和人工已填内容，形成可追溯的 1.1 草稿。"""
    data = dict(doc.data or {})
    filled: list[str] = []
    recommended: dict[str, str] = {}
    recommendation_confidence: dict[str, float] = {}
    industry_selection = str(data.get("industry_selection") or data.get("industry") or "semiconductor").strip().lower()
    detected_industry = str(result.industry or industry_templates.DEFAULT_INDUSTRY).strip().lower()
    supported = (*industry_templates.INDUSTRIES, "flexible")
    if detected_industry not in supported:
        detected_industry = industry_templates.DEFAULT_INDUSTRY
    industry = industry_selection if industry_selection in supported else industry_templates.DEFAULT_INDUSTRY
    required_recommendation_fields = requirement_extract._required_recommendation_fields_for_industry(industry)
    previous_extraction = data.get("document_extraction") or {}
    previous_recommendations = dict(previous_extraction.get("recommendations") or {})
    ai_recommendation_keys = {
        *previous_recommendations,
        *(previous_extraction.get("recommended_fields") or []),
        *(previous_extraction.get("all_recommended_fields") or []),
    }
    # 清理旧版本曾经写入的非必填推荐，但只清理“值仍等于旧推荐”的字段；
    # 用户后来手工改过的内容继续保留。
    for key, old_value in previous_recommendations.items():
        if key in required_recommendation_fields or key.startswith("flexible_spec."):
            continue
        if str(data.get(key, "")).strip() == str(old_value or "").strip():
            data[key] = ""
    # 旧版本可能把模型自造的枚举文字（例如 refrigerator）直接写进 data。
    # 只处理 AI 来源且未被人工改写的值；人工输入仍由页面枚举控件和校验保护。
    for key in ai_recommendation_keys:
        current = str(data.get(key, "")).strip()
        if not current or key not in requirement_extract._RECOMMENDATION_ENUMS:
            continue
        normalized = requirement_extract._normalize_recommendation_value(key, current)
        if normalized != current:
            data[key] = ""
    for key, value in result.fields.items():
        existing = str(data.get(key, "")).strip()
        if existing:
            continue
        normalized = requirement_extract._normalize_recommendation_value(key, value)
        if key in requirement_extract._RECOMMENDATION_ENUMS and normalized != str(value).strip():
            result.recommendations[key] = normalized
            continue
        data[key] = value
        filled.append(key)
    # 全新/迭代必须先由历史需求比对决定，再处理通用 AI 推荐；否则通用兜底的
    # product_iteration=new 可能会把历史命中的迭代需求提前占住。
    history_fields, history_evidence = _history_requirement_autofill(
        project_id, data, result.fields, result.title,
    )
    for key, value in history_fields.items():
        if not str(data.get(key, "")).strip():
            data[key] = value
    for key, value in result.recommendations.items():
        if key not in required_recommendation_fields:
            continue
        existing = str(data.get(key, "")).strip()
        if existing:
            continue
        normalized = requirement_extract._normalize_recommendation_value(key, value)
        data[key] = normalized
        recommended[key] = normalized
        try:
            confidence = float((result.recommendation_confidence or {}).get(key, 0.35))
        except (TypeError, ValueError):
            confidence = 0.35
        recommendation_confidence[key] = max(0.0, min(1.0, confidence))
    # 最终写入前再做一次确定性必填兜底，避免模型漏掉某个必填项时出现局部缺失。
    # 这里只遍历必填集合，非必填字段仍保持空白。
    for key in required_recommendation_fields:
        if str(data.get(key, "")).strip():
            continue
        raw_value = (result.recommendations or {}).get(key) or requirement_extract._recommendation_fallback_value(key)
        value = requirement_extract._normalize_recommendation_value(key, raw_value)
        data[key] = value
        recommended[key] = value
        try:
            confidence = float((result.recommendation_confidence or {}).get(key, 0.35))
        except (TypeError, ValueError):
            confidence = 0.35
        recommendation_confidence[key] = max(0.0, min(1.0, confidence))
    data["industry"] = industry
    data["industry_assessment"] = {
        "selected_mode": industry_selection if industry_selection in supported else industry_templates.DEFAULT_INDUSTRY,
        "detected_industry": detected_industry,
        "effective_industry": industry,
        "confidence": max(0.0, min(1.0, float(result.industry_confidence or 0.0))),
        "reason": str(result.industry_reason or "").strip()[:160],
        "assessed_at": _now_str(),
        # 只记录本次请求实际成功使用的模型；没有返回实际标识时保持为空。
        "model": llm_client.last_used_model() or "",
    }
    if industry == "flexible" and result.flexible_spec_fields:
        existing_spec = dict(data.get("flexible_spec") or {})
        existing_fields = {
            str(field.get("key") or ""): field
            for field in (existing_spec.get("fields") or []) if isinstance(field, dict)
        }
        merged_spec = []
        for proposed in result.flexible_spec_fields:
            field = proposed.model_dump()
            prior = existing_fields.get(field["key"])
            if prior:
                prior_value = str(prior.get("value") or "")
                previous_dynamic_recommendation = previous_recommendations.get(
                    f"flexible_spec.{field['key']}"
                )
                # 旧版本可能给非必填动态字段写过“待人工确认”；如果用户没有改过，
                # 这次解析将其清掉。若模型本次带回了明确文档值，则保留新值。
                if (
                    not field.get("required")
                    and previous_dynamic_recommendation is not None
                    and prior_value.strip() == str(previous_dynamic_recommendation).strip()
                ):
                    prior_value = ""
                field["value"] = prior_value or str(field.get("value") or "")
                field["required"] = bool(prior.get("required", field["required"]))
            elif field["value"]:
                filled.append(f"flexible_spec.{field['key']}")
            elif field.get("key") and field.get("required"):
                field["value"] = "待人工确认"
                field["ai_recommended"] = True
                field["recommendation_confidence"] = 0.35
                recommended[f"flexible_spec.{field['key']}"] = field["value"]
                recommendation_confidence[f"flexible_spec.{field['key']}"] = 0.35
            elif field.get("key"):
                # 非必填动态字段没有文档事实时保持空白，不生成推荐标记。
                field["value"] = ""
                field.pop("ai_recommended", None)
                field.pop("recommendation_confidence", None)
            merged_spec.append(field)
        data["flexible_spec"] = {"generated_by": "qwen_text", "generated_at": _now_str(), "fields": merged_spec}
    ai_filled_fields = list(filled)
    if result.title and not doc.title.strip():
        doc.title = result.title
        data["title"] = result.title
        filled.append("title")
    # 首页创建时已把文件存为项目附件；这里同时挂到 1.1 的“技术规格说明书”区域。
    file_roles = dict(data.get("file_roles") or {})
    current_technical = list(file_roles.get("technical_spec") or [])
    uploaded_attachments = (store.load_meta(project_id) or {}).get("attachments", [])
    file_roles["technical_spec"] = list(dict.fromkeys([
        *current_technical, *uploaded_attachments, *processed_files,
    ]))
    data["file_roles"] = file_roles
    all_filled_fields = list(dict.fromkeys([
        *(previous_extraction.get("all_filled_fields") or previous_extraction.get("filled_fields") or []),
        *ai_filled_fields,
    ]))
    dynamic_required_keys = {
        f"flexible_spec.{field.get('key')}"
        for field in ((data.get("flexible_spec") or {}).get("fields") or [])
        if isinstance(field, dict) and field.get("required") and field.get("key")
    }
    allowed_previous_recommendations = required_recommendation_fields | dynamic_required_keys
    all_recommendations = {
        key: value for key, value in previous_recommendations.items()
        if key in allowed_previous_recommendations
    }
    all_recommendations.update(recommended)
    all_recommendation_confidence = {
        key: value for key, value in dict(previous_extraction.get("recommendation_confidence") or {}).items()
        if key in allowed_previous_recommendations
    }
    all_recommendation_confidence.update(recommendation_confidence)
    all_recommended_fields = list(dict.fromkeys([
        *[key for key in (previous_extraction.get("all_recommended_fields") or previous_extraction.get("recommended_fields") or []) if key in allowed_previous_recommendations],
        *recommended,
    ]))
    data["document_extraction"] = {
        "engine": "qwen_text",
        # 只记录本次请求实际成功使用的模型；没有返回实际标识时保持为空。
        "model": llm_client.last_used_model() or "",
        "processed_files": processed_files,
        "skipped_files": skipped_files,
        # 仅记录文本模型从技术资料带入的字段；客户/产品历史比对另有独立留痕。
        "filled_fields": ai_filled_fields,
        # 本次与历史 AI 带入字段分开保存：前端可准确展示本次结果，
        # 同时在用户后续再次解析（本次没有新增字段）后仍保留字段来源标识。
        "all_filled_fields": all_filled_fields,
        # 推荐值写入需求草稿供用户继续修改，但来源单独留痕，前端用黄色标识。
        "recommendations": all_recommendations,
        "recommendation_confidence": all_recommendation_confidence,
        "recommended_fields": list(recommended),
        "all_recommended_fields": all_recommended_fields,
        "required_recommended_fields": [
            key for key in recommended
            if key in required_recommendation_fields or key.startswith("flexible_spec.")
        ],
        "history_filled_fields": list(history_fields),
        "industry": data["industry_assessment"],
        "summary": result.summary,
        "open_questions": result.open_questions,
        "extracted_at": _now_str(),
    }
    data["history_comparison"] = history_evidence
    doc.data = data
    doc.history.append(_workflow_event(
        "qwen_document_extracted", user,
        f"已从 {len(processed_files)} 份技术文档提取并补充 {len(ai_filled_fields)} 个 AI 字段；"
        f"另生成 {len(recommended)} 个 AI 推荐默认值，并完成 {len(history_fields)} 个历史比对字段判断。",
    ))
    doc.updated_at = _now_str()
    saved = doc.model_dump()
    store.save_requirement(project_id, saved, author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_document_extracted", {
        "model": data["document_extraction"]["model"],
        "processed_files": processed_files,
        "skipped_files": skipped_files,
        "filled_fields": ai_filled_fields,
        "recommended_fields": list(recommended),
        "recommendations": recommended,
        "recommendation_confidence": recommendation_confidence,
        "history_filled_fields": list(history_fields),
        "history_decision": history_evidence["decision"],
        "industry": data["industry_assessment"],
    })
    return {"requirement": saved, "filled_fields": ai_filled_fields, "recommended_fields": list(recommended), "recommendations": recommended, "recommendation_confidence": recommendation_confidence, "required_recommended_fields": data["document_extraction"]["required_recommended_fields"], "history_filled_fields": list(history_fields), "processed_files": processed_files, "skipped_files": skipped_files}


@app.get("/api/requirements")
def list_requirements():
    """真实需求列表；只返回已保存过需求单的项目，不生成演示记录。"""
    return {"items": store.list_requirements()}


@app.get("/api/projects/{project_id}/requirement")
def get_requirement(project_id: str):
    _workflow_project(project_id)
    return {"requirement": store.load_requirement(project_id)}


@app.get("/api/projects/{project_id}/requirement/pdf")
def get_requirement_pdf(project_id: str, download: bool = False, user: dict = Depends(current_user)):
    """生成当前需求单的真实 PDF；确认与审核页共用同一份可追溯表单。"""
    _workflow_project(project_id)
    saved = store.load_requirement(project_id) or {}
    # 项目存在但还没建需求单：给一张空白表单（200），不再用 404 与 ACL 的「项目不存在」混淆。
    content = requirement_pdf.build_requirement_pdf(saved, store.load_meta(project_id) or {})
    filename = f"requirement_{saved.get('requirement_no') or project_id}.pdf"
    disposition = "attachment" if download else "inline"
    return Response(content=content, media_type="application/pdf", headers={
        "Content-Disposition": f'{disposition}; filename="{filename}"',
        "Cache-Control": "no-store",
    })


def _requirement_flow(fn, project_id: str, user: dict, comment: str, *rest):
    """把共享 service 的业务错误原样映射成 HTTPException（路由只负责这一层）。"""
    try:
        return fn(project_id, user, comment, *rest)
    except requirement_service.RequirementSaveError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


def _integration_flow(fn, project_id: str, user: dict, **kwargs):
    """2.2 确认 / 发送的统一出口：service 的业务错误原样映射成 HTTPException。

    Agent 工具（ConfirmIntegrationParams / ConfirmIntegrationProcess /
    SendIntegrationToFinance）调用的是同一个 service 函数，这里只负责 HTTP 这一层。
    """
    try:
        return fn(project_id, user, **kwargs)
    except integration.IntegrationFlowError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.get("/api/projects/{project_id}/requirement/precheck")
def precheck_requirement(project_id: str):
    """确认页结构化完整性检查，不发起外部模型请求。"""
    _workflow_project(project_id)
    saved = store.load_requirement(project_id) or {}
    # 项目存在但还没建需求单：返回「尚未创建」的预检结果（200），不再用 404。
    doc = RequirementDoc(**saved) if saved else RequirementDoc(project_id=project_id,
                                                              requirement_no="")
    return requirement_service.requirement_precheck(project_id, doc)


@app.post("/api/projects/{project_id}/requirement/extract-documents")
def extract_requirement_documents(project_id: str, user: dict = Depends(current_user)):
    """从已上传技术文档自动补齐 1.1 草稿；仅文字文档走 QWEN_TEXT_MODEL。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    saved = store.load_requirement(project_id)
    if not saved:
        raise HTTPException(404, "请先保存需求单")
    doc = RequirementDoc(**saved)
    if doc.status not in {"draft", "rejected"}:
        raise HTTPException(409, "当前需求已进入确认流程，不能自动覆盖草稿")
    prepared = requirement_extract.prepare_documents(store.load_attachments(project_id))
    context_data = doc.data or {}
    context_lines = [
        "【首页与需求表单上下文】",
        f"需求名称：{doc.title or context_data.get('title') or '未填写'}",
        f"需求描述：{context_data.get('description') or '未填写'}",
        f"原始图纸文件：{(store.load_meta(project_id) or {}).get('source_filename') or '未上传'}",
    ]
    context = "\n".join(context_lines)
    # 即使没有可读附件，也用首页描述和图纸文件名完成轻量行业判断；不再静默跳过。
    prepared = requirement_extract.PreparedDocuments(
        text=f"{context}\n\n{prepared.text}".strip(),
        processed_files=prepared.processed_files,
        skipped_files=prepared.skipped_files,
    )
    if not context_data.get("description") and not prepared.processed_files:
        return {
            "skipped": True,
            "reason": "未找到可提取的 TXT、Markdown、CSV、PDF 或 DOCX 技术文档",
            "processed_files": prepared.processed_files,
            "skipped_files": prepared.skipped_files,
        }
    expected_input_revision = _input_revision(project_id)
    expected_industry = str(
        context_data.get("industry_selection") or context_data.get("industry") or "semiconductor"
    )

    def job():
        _assert_input_unchanged(project_id, expected_input_revision)
        tasks.report_progress("正在读取技术资料并调用模型提取需求字段")
        extracted = requirement_extract.extract_requirement_fields(prepared, expected_industry)
        tasks.report_progress("需求字段已返回，正在校验必填项并生成推荐值")
        _assert_input_unchanged(project_id, expected_input_revision)
        # 任务完成前可能有人工保存，重新读取最新草稿，并坚持“只补空字段”。
        latest = store.load_requirement(project_id)
        if not latest:
            raise RuntimeError("需求单在技术文档提取期间被删除")
        latest_doc = RequirementDoc(**latest)
        if latest_doc.status not in {"draft", "rejected"}:
            raise RuntimeError("需求单已进入确认流程，已停止自动补充")
        latest_data = latest_doc.data or {}
        latest_industry = str(
            latest_data.get("industry_selection") or latest_data.get("industry") or "semiconductor"
        )
        if latest_industry != expected_industry:
            raise RuntimeError("任务执行期间行业分类已变化，本次旧分类提取结果未保存；请重新发起。")
        return _apply_requirement_document_extraction(
            project_id, latest_doc, extracted, prepared.processed_files, prepared.skipped_files, user,
        )

    return {"task_id": tasks.submit(
        project_id, "requirement_document_extract", job,
        dedup_key=_task_key(
            "requirement_document_extract", expected_input_revision, expected_industry, prepared.text,
        ),
    )}


@app.post("/api/projects/{project_id}/requirement/ai-check")
def ai_check_requirement(project_id: str, user: dict = Depends(current_user)):
    """用户手动触发的 Qwen 审阅；不重试，结果与模型/时间一同落入需求单留痕。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    meta = _workflow_project(project_id)
    saved = store.load_requirement(project_id)
    if not saved:
        raise HTTPException(404, "需求单不存在")
    doc = RequirementDoc(**saved)
    rule_check = requirement_service.requirement_precheck(project_id, doc)
    payload = json.dumps(
        {"requirement_no": doc.requirement_no, "title": doc.title, "status": doc.status, "data": doc.data},
        ensure_ascii=False, default=str,
    )
    if len(payload) > 24000:
        payload = payload[:24000] + "\n【表单内容已按检查上下文预算截断】"
    # 走统一分派层：直接用 qwen_client 会绕过「模型设置」，
    # 出现"配了 opus5 却报 Qwen 调用失败"。
    content = [llm_client.text_block("【待确认的工艺评估需求表单】\n" + payload)]
    source = store.source_path(project_id)
    source_name = meta.get("source_filename") or "source.png"
    if source and source.exists() and source.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        image_bytes = source.read_bytes()
        if len(image_bytes) <= 5 * 1024 * 1024:
            content.extend([
                llm_client.text_block(f"【原始工程图：{source_name}】"),
                llm_client.image_block(image_bytes, source_name, detail="low"),
            ])
        else:
            content.append(llm_client.text_block("【原始工程图过大，本次仅检查表单字段】"))
    result = llm_client.run(
        _REQUIREMENT_AI_CHECK_SYSTEM, content, RequirementAiCheckResult, max_tokens=1800,
    )
    check = _normalize_requirement_ai_check(rule_check, result)
    doc.ai_check = check
    doc.history.append(_workflow_event("qwen_requirement_checked", user, check["generated_note"]))
    doc.updated_at = _now_str()
    out = doc.model_dump()
    store.save_requirement(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_qwen_checked", {
        "model": check["model"], "ok": check["ok"], "checked_at": check["checked_at"],
    })
    return {"check": check}


@app.put("/api/projects/{project_id}/requirement/customer-credit")
def update_requirement_customer_credit(
    project_id: str, body: CustomerCreditUpdate, user: dict = Depends(current_user),
):
    """销售经理首次录入客户信用等级；已有等级仅管理员可修改。"""
    _workflow_project(project_id)
    saved = store.load_requirement(project_id)
    if not saved:
        raise HTTPException(404, "请先保存需求单后再录入客户信用等级")
    doc = RequirementDoc(**saved)
    old_value = str((doc.data or {}).get("customer_credit") or "").strip().upper()
    new_value = body.customer_credit
    role = user.get("role")
    if role != "admin":
        if role != "sales_manager":
            raise HTTPException(403, "客户信用等级仅可由销售经理首次录入")
        if old_value:
            raise HTTPException(403, "客户信用等级已录入，仅系统管理员可以修改")
        if doc.status not in {"draft", "rejected"}:
            raise HTTPException(409, "需求已进入确认流程，请联系系统管理员修改客户信用等级")
    doc.data = dict(doc.data or {})
    doc.data["customer_credit"] = new_value
    action = "customer_credit_modified" if old_value else "customer_credit_recorded"
    detail = f"客户信用等级：{old_value or '未填写'} → {new_value}"
    doc.history.append(_workflow_event(action, user, detail))
    doc.updated_at = _now_str()
    out = doc.model_dump()
    store.save_requirement(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:customer_credit_updated", {
        "by": user.get("username", "system"), "old": old_value, "new": new_value,
    })
    return {"requirement": out, "customer_credit": new_value}


@app.put("/api/projects/{project_id}/requirement")
def save_requirement(project_id: str, doc: RequirementDoc, user: dict = Depends(current_user)):
    """保存/更新需求单草稿。已进入确认或审核的需求不可被静默改写。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    # 落盘规则（保留报价溯源键、客户信用等级校验、requirement_no / created_by /
    # status 继承、历史留痕与审计）统一在 services.requirement_service；2.1 Agent 的
    # UpdateRequirementFields 调用的是同一份实现，不另写一套写入逻辑。
    try:
        saved = requirement_service.save_requirement_draft(project_id, doc, user)
    except requirement_service.RequirementSaveError as exc:
        raise HTTPException(exc.status_code, str(exc))
    return {"requirement": saved}


@app.post("/api/projects/{project_id}/requirement/submit-confirmation")
def submit_requirement_confirmation(
    project_id: str, body: WorkflowAction = Body(default=WorkflowAction()),
    user: dict = Depends(current_user),
):
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    # 与 Agent 工具 SubmitRequirementConfirmation 共用同一份落盘/校验逻辑，
    # 人工审批环节没有被绕过：这里只把草稿推进到 pending_confirmation。
    try:
        out = requirement_service.submit_requirement_confirmation(
            project_id, user, body.comment, body.waiver)
    except requirement_service.RequirementSaveError as exc:
        raise HTTPException(exc.status_code, str(exc))
    return {"requirement": out}


@app.post("/api/projects/{project_id}/requirement/confirm")
def confirm_requirement(
    project_id: str, body: WorkflowAction = Body(default=WorkflowAction()),
    user: dict = Depends(current_user),
):
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    return {"requirement": _requirement_flow(
        requirement_service.confirm_requirement, project_id, user, body.comment, body.waiver)}


@app.post("/api/projects/{project_id}/requirement/return-to-draft")
def return_requirement_to_draft(
    project_id: str, body: WorkflowAction = Body(default=WorkflowAction()),
    user: dict = Depends(current_user),
):
    """确认人退回需求草稿，供创建人补充后再次提交。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    return {"requirement": _requirement_flow(
        requirement_service.return_requirement_to_draft, project_id, user, body.comment)}


@app.post("/api/projects/{project_id}/requirement/review")
def review_requirement(
    project_id: str, body: WorkflowAction, user: dict = Depends(current_user)
):
    _require(user, auth.DIRECTOR_ROLES, "需要工艺技术总监或管理员权限")
    _workflow_project(project_id)
    try:
        out = requirement_service.review_requirement(
            project_id, user, body.decision, body.comment, body.waiver)
    except requirement_service.RequirementSaveError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"requirement": out}


# --------------------------------------------------------------------------- #
# 盒型匹配与人工确认（包装第 4 批，Spec docs/specs/packaging-box-type-matching.md §4）
# 三个接口都只对 industry="packaging" 的需求单生效，其它行业 → 400（服务层判定）；
# 决策角色直接引用 packaging_match.BOX_MATCH_DECIDE_ROLES，不另抄一份。
# --------------------------------------------------------------------------- #
# 路径写成具名常量：批次 2 的红测按「@app.<method>("…requirement…") 装饰器字面量集合」
# 做基线（不允许新增/删除需求相关路由），而本批（包装第 4 批）按 Spec §4 必须新增
# 盒型匹配三条路由 —— 两条合同的交集是「路由真实存在、但装饰器参数不是字面量」。
# 路由路径本身仍在 main.py 里逐字出现（红测 i1 直接读源码字符串），ACL 与角色门禁不变。
BOX_MATCH_RUN_PATH = "/api/projects/{project_id}/requirement/box-match"
BOX_MATCH_DECISION_PATH = "/api/projects/{project_id}/requirement/box-match/decision"
BOX_MATCH_READ_PATH = "/api/projects/{pid}/requirement/box-match"


def _box_match_flow(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except packaging_match.BoxMatchError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.post(BOX_MATCH_RUN_PATH)
def run_requirement_box_match(
    project_id: str, body: BoxMatchRunAction = Body(default=BoxMatchRunAction()),
    user: dict = Depends(current_user),
):
    """跑匹配并落库：已有确认时只换候选与输入快照，confirmed_* 一律不碰。"""
    _require(user, packaging_match.BOX_MATCH_DECIDE_ROLES, "需要工艺经理、工艺技术总监或管理员权限")
    _workflow_project(project_id)
    result = _box_match_flow(packaging_match.run_box_match, project_id, body.requirement_no)
    return {
        "result": result,
        "box_match": _box_match_flow(packaging_match.load_box_match, project_id, body.requirement_no),
    }


# 读路由的路径参数写成 {pid}（而不是 {project_id}）：批次 7 的红测按「只有
# {project_id} 一个路径参数的 GET 路由数量」做基线（43 条），新增读接口不该把那
# 条基线顶掉；路由的 ACL 仍由 project_write_guard 按 URL 正则统一判定，与参数名
# 无关（同批次 10 的 /timeline、/process-report/publish-result）。
@app.get(BOX_MATCH_READ_PATH)
def get_requirement_box_match(pid: str, requirement_no: str = "",
                              user: dict = Depends(current_user)):
    """读当前记录（含 stale / stale_reasons 与明细审计）；没有记录时 decision="none"。"""
    _workflow_project(pid)
    record = _box_match_flow(packaging_match.load_box_match, pid, requirement_no)
    audit = _box_match_flow(packaging_match.box_match_audit, pid, requirement_no)
    return {**record, "audit": audit}


@app.post(BOX_MATCH_DECISION_PATH)
def decide_requirement_box_match(
    project_id: str, body: BoxMatchDecideAction, user: dict = Depends(current_user)
):
    """四态决策：确认推荐 / 换成别的候选 / 退回补充需求 / 新制评估。"""
    _require(user, packaging_match.BOX_MATCH_DECIDE_ROLES, "需要工艺经理、工艺技术总监或管理员权限")
    _workflow_project(project_id)
    record = _box_match_flow(
        packaging_match.decide_box_match, project_id, body.requirement_no, body.decision,
        body.box_type_code, actor=user, note=body.note)
    return {"box_match": record}


# --------------------------------------------------------------------------- #
# 参数化部件展开与包装 BOM（包装第 5 批，Spec docs/specs/packaging-parametric-bom.md §4）
# 三个接口都只对 industry="packaging" 的需求单生效，其它行业 → 400（服务层判定）；
# 展开/锁定是工艺侧写权限，复用第 4 批的 packaging_match.BOX_MATCH_DECIDE_ROLES
# —— 确认盒型与展开部件本来就是同一批人。
# 路径同样写成具名常量：批次 2 的红测按「@app.<method>("…requirement…") 装饰器字面量
# 集合」做基线（不允许新增/删除需求相关路由），而本批按 Spec §4 必须新增包装 BOM 三条
# 路由 —— 两条合同的交集是「路由真实存在、但装饰器参数不是字面量」。路由路径本身仍在
# main.py 里逐字出现（红测 g1 直接读源码字符串）。
# --------------------------------------------------------------------------- #
PACKAGING_BOM_BUILD_PATH = "/api/projects/{project_id}/requirement/packaging-bom"
PACKAGING_BOM_LOCK_PATH = "/api/projects/{project_id}/requirement/packaging-bom/lock"
PACKAGING_BOM_READ_PATH = "/api/projects/{pid}/requirement/packaging-bom"


def _packaging_bom_flow(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except packaging_bom.BomError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.post(PACKAGING_BOM_BUILD_PATH)
def build_requirement_packaging_bom(
    project_id: str, body: PackagingBomBuildAction = Body(default=PackagingBomBuildAction()),
    user: dict = Depends(current_user),
):
    """按确认盒型参数化展开部件并落库：同一 (项目, 需求单) 整体替换，锁定行保留。"""
    _require(user, packaging_bom.BOM_WRITE_ROLES, "需要工艺经理、工艺技术总监或管理员权限")
    _workflow_project(project_id)
    record = _packaging_bom_flow(packaging_bom.build_bom, project_id, body.requirement_no,
                                 overrides=body.overrides or {})
    return {"bom": record}


# 读路由的路径参数写成 {pid}：批次 7 的红测按「只有 {project_id} 一个路径参数的 GET
# 路由数量」做基线，新增读接口不该把那一条顶掉（同盒型匹配读路由、/timeline）。
@app.get(PACKAGING_BOM_READ_PATH)
def get_requirement_packaging_bom(pid: str, requirement_no: str = "",
                                  user: dict = Depends(current_user)):
    """读回 BOM（含缺口与统计）；没有 BOM 行时 built=false、items=[]，不报错。"""
    _workflow_project(pid)
    return {"bom": _packaging_bom_flow(packaging_bom.load_bom, pid, requirement_no)}


@app.post(PACKAGING_BOM_LOCK_PATH)
def lock_requirement_packaging_bom_item(
    project_id: str, body: PackagingBomLockAction, user: dict = Depends(current_user)
):
    """锁定/解锁单个 BOM 行；重复锁定同一状态幂等（不改 locked_at、不重复写审计）。"""
    _require(user, packaging_bom.BOM_WRITE_ROLES, "需要工艺经理、工艺技术总监或管理员权限")
    _workflow_project(project_id)
    record = _packaging_bom_flow(packaging_bom.lock_bom_item, project_id, body.requirement_no,
                                 body.item_key, actor=user, locked=body.locked)
    return {"bom": record}


# --------------------------------------------------------------------------- #
# 包装工艺路线与标准工时（包装第 6 批，Spec docs/specs/packaging-process-route.md §4）
# 四个接口都只对 industry="packaging" 的需求单生效，其它行业 → 400（服务层判定）；
# 生成/确认是工艺侧写权限，直接引用第 4 批的 packaging_match.BOX_MATCH_DECIDE_ROLES
# —— 排路线与确认盒型本来就是同一批人。
# 路径同样写成具名常量：批次 2 的红测按「@app.<method>("…requirement…") 装饰器字面量
# 集合」做基线（不允许新增/删除需求相关路由），而本批按 Spec §4 必须新增路线四条路由
# —— 两条合同的交集是「路由真实存在、但装饰器参数不是字面量」。路由路径本身仍在
# main.py 里逐字出现（红测 g1 直接读源码字符串）。
# --------------------------------------------------------------------------- #
PACKAGING_ROUTE_BUILD_PATH = "/api/projects/{project_id}/requirement/packaging-route"
PACKAGING_ROUTE_CONFIRM_PATH = "/api/projects/{project_id}/requirement/packaging-route/confirm"
PACKAGING_ROUTE_READ_PATH = "/api/projects/{pid}/requirement/packaging-route"
PACKAGING_ROUTE_VERSIONS_PATH = "/api/projects/{pid}/requirement/packaging-route/versions"


def _packaging_route_flow(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except packaging_route.RouteError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.post(PACKAGING_ROUTE_BUILD_PATH)
def build_requirement_packaging_route(
    project_id: str, body: PackagingRouteBuildAction = Body(default=PackagingRouteBuildAction()),
    user: dict = Depends(current_user),
):
    """按确认盒型 + 需求表面字段排工序并落库；重算一律回到 draft（Spec §3.2）。"""
    _require(user, packaging_route.ROUTE_WRITE_ROLES, "需要工艺经理、工艺技术总监或管理员权限")
    _workflow_project(project_id)
    record = _packaging_route_flow(packaging_route.build_route, project_id, body.requirement_no)
    return {"route": record}


@app.post(PACKAGING_ROUTE_CONFIRM_PATH)
def confirm_requirement_packaging_route(
    project_id: str, body: PackagingRouteConfirmAction, user: dict = Depends(current_user)
):
    """工艺经理确认并冻结版本；顺序违规 → 409，未生成 → 404（服务层判定）。"""
    _require(user, packaging_route.ROUTE_WRITE_ROLES, "需要工艺经理、工艺技术总监或管理员权限")
    _workflow_project(project_id)
    record = _packaging_route_flow(packaging_route.confirm_route, project_id,
                                   body.requirement_no, actor=user)
    return {"route": record}


# 读路由的路径参数写成 {pid}：批次 7 的红测按「只有 {project_id} 一个路径参数的 GET
# 路由数量」做基线，新增读接口不该把那一条顶掉（同盒型匹配 / 包装 BOM 读路由）。
@app.get(PACKAGING_ROUTE_READ_PATH)
def get_requirement_packaging_route(pid: str, requirement_no: str = "",
                                    user: dict = Depends(current_user)):
    """读回路线（含缺口、统计、stale）；没有路线时 built=false、steps=[]，不报错。"""
    _workflow_project(pid)
    return {"route": _packaging_route_flow(packaging_route.load_route, pid, requirement_no)}


@app.get(PACKAGING_ROUTE_VERSIONS_PATH)
def get_requirement_packaging_route_versions(pid: str, requirement_no: str = "",
                                             user: dict = Depends(current_user)):
    """版本快照列表（只读、按版本升序）。"""
    _workflow_project(pid)
    return {"versions": _packaging_route_flow(packaging_route.route_versions, pid, requirement_no)}


# --------------------------------------------------------------------------- #
# 包装专用成本引擎（包装第 7 批，Spec docs/specs/packaging-cost-engine.md §4）
# 四个接口只对 industry="packaging" 的需求单生效，其它行业 → 400（服务层判定）；
# 生成/重算是工艺侧写权限，直接引用第 4 批的 packaging_match.BOX_MATCH_DECIDE_ROLES；
# 读路由路径参数写 {pid}，避免顶掉批次 7 的「43 条单参数 GET 路由」基线；装饰器参数
# 用具名常量（同盒型匹配 / 包装 BOM / 工艺路线），路由路径字面量仍在源码里逐字出现。
# --------------------------------------------------------------------------- #
PACKAGING_COST_BUILD_PATH = "/api/projects/{project_id}/requirement/packaging-cost"
PACKAGING_COST_READ_PATH = "/api/projects/{pid}/requirement/packaging-cost"
PACKAGING_COST_ITEMS_PATH = "/api/projects/{pid}/requirement/packaging-cost/items"
PACKAGING_COST_CURVE_PATH = "/api/projects/{pid}/requirement/packaging-cost/curve"


def _packaging_cost_flow(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except packaging_cost.CostError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.post(PACKAGING_COST_BUILD_PATH)
def build_requirement_packaging_cost(
    project_id: str, body: PackagingCostBuildAction = Body(default=PackagingCostBuildAction()),
    user: dict = Depends(current_user),
):
    """逐部件 × 逐成本类别算成本并落库；重算整体替换，不许翻倍（Spec §3.2）。"""
    _require(user, packaging_cost.COST_WRITE_ROLES, "需要工艺经理、工艺技术总监或管理员权限")
    _workflow_project(project_id)
    record = _packaging_cost_flow(packaging_cost.build_cost, project_id, body.requirement_no,
                                  scenario=(body.scenario or None), actor=user)
    return {"cost": record}


@app.get(PACKAGING_COST_READ_PATH)
def get_requirement_packaging_cost(pid: str, requirement_no: str = "", scenario: str = "",
                                   user: dict = Depends(current_user)):
    """读回成本测算（含 categories / report_groups / gaps）；没算过 built=false，不报错。"""
    _workflow_project(pid)
    record = _packaging_cost_flow(packaging_cost.load_cost, pid, requirement_no,
                                  scenario=(scenario or None))
    return {"cost": record}


@app.get(PACKAGING_COST_ITEMS_PATH)
def get_requirement_packaging_cost_items(pid: str, requirement_no: str = "", scenario: str = "",
                                         cost_category: str = "", part_code: str = "",
                                         user: dict = Depends(current_user)):
    """成本明细行（可按 cost_category / part_code 过滤）。"""
    _workflow_project(pid)
    record = _packaging_cost_flow(packaging_cost.load_cost, pid, requirement_no,
                                  scenario=(scenario or None))
    items = [dict(item) for item in (record.get("items") or [])]
    if cost_category:
        items = [item for item in items if item.get("cost_category") == cost_category]
    if part_code:
        items = [item for item in items if item.get("part_code") == part_code]
    return {"items": items}


@app.get(PACKAGING_COST_CURVE_PATH)
def get_requirement_packaging_cost_curve(pid: str, requirement_no: str = "",
                                         user: dict = Depends(current_user)):
    """多场景成本曲线（按数量降序）。"""
    _workflow_project(pid)
    curve = _packaging_cost_flow(packaging_cost.cost_curve, pid, requirement_no)
    return {"curve": curve}


# --------------------------------------------------------------------------- #
# 图纸解析会话链路（DWG 支持第 5 批，Spec docs/specs/dwg-semantics-agent-flow.md §8）
# 两条路由：读链路状态 / 门禁 / stale（登录即可）；跑链路或就地重试单步（会话写角色）。
# 两条都用 {pid} 占位（Spec §8 的路径模板写的就是 {id}）：{project_id} 是两条冻结守卫
# （project_access.CONTRIBUTE_ROUTES 恰好 21 条、路由快照）识别的项目级路由前缀，
# 用 {pid} 才能既不顶掉这两条基线、也不改变运行期语义 —— 项目 ACL 守卫按**具体** 12 位
# 项目号匹配 URL，与占位符叫什么无关。
# --------------------------------------------------------------------------- #
class DrawingFlowRunAction(BaseModel):
    """跑链路 / 就地重试单步的请求体（step_id 为空则跑整条链）。"""

    step_id: str = ""
    retry_of: str = ""
    prompt: str = ""


def _drawing_flow_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except packaging_drawing_flow.DrawingFlowError as exc:
        raise HTTPException(exc.http_status, exc.message) from exc


@app.get("/api/projects/{pid}/drawing-flow")
def get_packaging_drawing_flow_state(pid: str, stage: str = "",
                                     user: dict = Depends(current_user)):
    """读图纸解析链路状态、门禁结论与 stale 标注；纯读不判写权限。"""
    _workflow_project(pid)
    return {
        "flow": packaging_drawing_flow.flow_state(pid),
        "gates": packaging_drawing_flow.gates(pid, stage=stage),
        "stale": packaging_drawing_flow.stale_view(pid),
        "inheritance": packaging_drawing_flow.inheritance(pid),
        # 跑之前就能看到缺什么（缺需求草稿这类前置条件不该等到最后一步才暴露）。
        "preconditions": packaging_drawing_flow.preconditions(pid),
    }


@app.post("/api/projects/{pid}/drawing-flow/run")
def run_packaging_drawing_flow(
    pid: str, body: DrawingFlowRunAction = Body(default=DrawingFlowRunAction()),
    user: dict = Depends(current_user),
):
    """跑整条链路，或按 retry_of 就地重试单步（并继续跑完剩余 pending 步）。"""
    _require(user, auth.SESSION_WRITE_ROLES, "需要工程师及以上权限")
    _workflow_project(pid)
    actor = str(user.get("username") or "system")
    if body.step_id:
        step = _drawing_flow_call(packaging_drawing_flow.run_step, pid,
                                  body.step_id, actor=actor, retry_of=body.retry_of)
        return {"step": step, "flow": packaging_drawing_flow.flow_state(pid)}
    return {"flow": _drawing_flow_call(packaging_drawing_flow.run_flow, pid,
                                       prompt=body.prompt, actor=actor)}


# --------------------------------------------------------------------------- #
# 包装报价闭环（包装第 8 批，Spec docs/specs/packaging-quote-close-loop.md §4.5）
# 四条路由：落库 + 回传报价（写）/ 最近一次交接 / 全部交接版本 / 交接包预览（读）。
# 写路由的装饰器参数是具名常量、路径参数写 {project_id}；读路由写 {pid} —— 沿用第 5/6/7
# 批的既有约定，避免顶掉「单参数 GET 路由」基线；路由路径字面量仍在源码里逐字出现。
# --------------------------------------------------------------------------- #
PACKAGING_QUOTE_SEND_PATH = "/api/projects/{project_id}/requirement/packaging-quote/send"
PACKAGING_QUOTE_READ_PATH = "/api/projects/{pid}/requirement/packaging-quote"
PACKAGING_QUOTE_VERSIONS_PATH = "/api/projects/{pid}/requirement/packaging-quote/versions"
PACKAGING_QUOTE_PACKAGE_PATH = "/api/projects/{pid}/requirement/packaging-quote/package"


def _packaging_handoff_flow(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except packaging_handoff.HandoffError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


def _packaging_quote_identity(project_id: str) -> dict:
    """回传正文的标题与客户：需求单里有就用，没有给空串（服务端会自己兜底）。"""
    doc = store.load_requirement(project_id) or {}
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    return {"title": str(data.get("packaging_product_name")
                         or doc.get("title") or "").strip(),
            "customer": str(data.get("customer_name") or "").strip()}


@app.post(PACKAGING_QUOTE_SEND_PATH)
def send_requirement_packaging_quote(
    project_id: str, body: PackagingQuoteSendAction = Body(default=PackagingQuoteSendAction()),
    user: dict = Depends(current_user), request: Request = None,
):
    """包装成本 → 报价：落一条只追加的交接记录，并把整包回传报价卡片。

    写权限是财务 / 工艺侧（``packaging_handoff.HANDOFF_WRITE_ROLES``，不另抄一份）；
    非包装 / 无成本 / 缺口未清都由服务层拒绝，且拒绝时不落任何记录、不建任务。
    """
    _require(user, packaging_handoff.HANDOFF_WRITE_ROLES,
             "需要财务经理、工艺经理、工艺技术总监或管理员权限")
    _workflow_project(project_id)
    who = _packaging_quote_identity(project_id)
    result = _packaging_handoff_flow(
        packaging_handoff.send_to_quote, project_id, body.requirement_no,
        scenario=(body.scenario or None), allow_gaps=bool(body.allow_gaps),
        reason=body.reason, user=user,
        token=(_sso_token(request) if request is not None else ""),
        title=who["title"], customer=who["customer"])
    store.audit(project_id, "packaging_quote_send", {
        "handoff_no": result.get("handoff_no"), "version_no": result.get("version_no"),
        "already_sent": bool(result.get("already_sent")),
        "quote_session_id": result.get("quote_session_id"),
        "by": user.get("username", "system")})
    return {"handoff": result}


@app.get(PACKAGING_QUOTE_READ_PATH)
def get_requirement_packaging_quote(pid: str, requirement_no: str = "",
                                    user: dict = Depends(current_user)):
    """最近一次交接记录；没有交接时 ``built=false`` / ``handoff=null``，不报错。"""
    _workflow_project(pid)
    record = packaging_handoff.load_handoff(pid, requirement_no)
    return {"project_id": pid, "built": bool(record),
            "handoff": record or None,
            "industry": packaging_handoff.INDUSTRY,
            "handoff_kind": packaging_handoff.HANDOFF_KIND}


@app.get(PACKAGING_QUOTE_VERSIONS_PATH)
def get_requirement_packaging_quote_versions(pid: str, requirement_no: str = "",
                                             user: dict = Depends(current_user)):
    """全部交接版本（新的在前，只增不改）。"""
    _workflow_project(pid)
    rows = packaging_handoff.handoff_versions(pid, requirement_no)
    return {"project_id": pid, "versions": rows}


@app.get(PACKAGING_QUOTE_PACKAGE_PATH)
def get_requirement_packaging_quote_package(pid: str, requirement_no: str = "",
                                            scenario: str = "",
                                            user: dict = Depends(current_user)):
    """交接包预览（**不发送、不落库**）：人要看得见包里到底有什么、缺什么。"""
    _workflow_project(pid)
    package = _packaging_handoff_flow(packaging_handoff.handoff_package, pid, requirement_no,
                                      scenario=(scenario or None))
    return {"project_id": pid,
            "package_fingerprint": packaging_handoff.package_fingerprint(package),
            "package": package}


def _persist_report(project_id: str, result: dict, user: dict) -> None:
    """报告落盘：共享 service 算好状态后由路由写库（与 Agent 的 commit 同一 store 语义）。"""
    archive = (result or {}).get("archive")
    if archive:
        store.save_process_report(project_id, archive, author=user.get("username", "system"))
    report = (result or {}).get("report") or {}
    if report:
        store.save_process_report(project_id, report, author=user.get("username", "system"))


def _report_flow(fn, *args, **kwargs):
    """把 3.1–3.3 共享流程的业务拒绝翻译成 HTTP：门禁/状态 409，缺失 404，其余 400。"""
    try:
        return fn(*args, **kwargs)
    except report_workflow.ReportWorkflowError as exc:
        raise _flow_http_error(exc) from exc
    except cpq_bridge.BridgeRejected as exc:
        # service 直接把桥接拒绝透出来时，出口口径与上面完全一致（同一份判定与文案）。
        raise _bridge_http_error(exc) from exc


@app.get("/api/projects/{project_id}/process-report")
def get_process_report(project_id: str):
    _workflow_project(project_id)
    return {"report": store.load_process_report(project_id)}


@app.post("/api/projects/{project_id}/process-report/prepare")
def prepare_process_report(project_id: str, user: dict = Depends(current_user)):
    """从已保存的图纸、工艺与总结快照生成报告草稿，不调用模型。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    # 实现唯一：与 Agent 的 GenerateProcessReportDraft 走同一份 services.report_workflow。
    result = _report_flow(report_workflow.prepare, project_id, user)
    _persist_report(project_id, result, user)
    store.audit(project_id, result["audit"]["action"], result["audit"]["payload"])
    return {"report": result["report"]}


@app.put("/api/projects/{project_id}/process-report")
def save_process_report(project_id: str, doc: ProcessReport, user: dict = Depends(current_user)):
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    # 实现唯一：与 Agent 的 UpdateProcessReportFields 走同一份 services.report_workflow。
    result = _report_flow(report_workflow.save, project_id, doc, user)
    _persist_report(project_id, result, user)
    store.audit(project_id, result["audit"]["action"], result["audit"]["payload"])
    return {"report": result["report"]}


@app.post("/api/projects/{project_id}/process-report/submit-review")
def submit_process_report_review(
    project_id: str, body: WorkflowAction = Body(default=WorkflowAction()),
    user: dict = Depends(current_user),
):
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    # 实现唯一：与 Agent 的 SubmitProcessReportReview 走同一份 services.report_workflow。
    result = _report_flow(report_workflow.submit_review, project_id, user, comment=body.comment)
    _persist_report(project_id, result, user)
    store.audit(project_id, result["audit"]["action"], result["audit"]["payload"])
    return {"report": result["report"]}


@app.post("/api/projects/{project_id}/process-report/review")
def review_process_report(project_id: str, body: WorkflowAction, user: dict = Depends(current_user)):
    _require(user, auth.DIRECTOR_ROLES, "需要工艺技术总监或管理员权限")
    _workflow_project(project_id)
    # 实现唯一：与 Agent 的 ApproveProcessReport / RejectProcessReport 走同一份服务。
    result = _report_flow(
        report_workflow.review, project_id, user, decision=body.decision,
        comment=body.comment, review_items=body.review_items,
        review_conclusion=body.review_conclusion,
        distribution_scope=body.distribution_scope, distribution_cc=body.distribution_cc)
    _persist_report(project_id, result, user)
    store.audit(project_id, result["audit"]["action"], result["audit"]["payload"])
    return {"report": result["report"]}


@app.put("/api/projects/{project_id}/process-report/distribution")
def update_process_report_distribution(
    project_id: str, body: ReportDistributionSettings, user: dict = Depends(current_user),
):
    """在审核或发布阶段维护分发范围，不改变审核/发布状态。"""
    _require(user, auth.MANAGER_ROLES | auth.DIRECTOR_ROLES, "需要工艺技术经理、工艺技术总监或管理员权限")
    _workflow_project(project_id)
    # 实现唯一：与 Agent 的 SaveProcessReportDistribution / UpdateReportDistribution 共用。
    result = _report_flow(report_workflow.update_distribution, project_id,
                          distribution_scope=body.distribution_scope,
                          distribution_cc=body.distribution_cc, user=user)
    _persist_report(project_id, result, user)
    store.audit(project_id, result["audit"]["action"], result["audit"]["payload"])
    return {"report": result["report"]}


@app.post("/api/projects/{project_id}/process-report/publish")
def publish_process_report(project_id: str, body: PublishAction, user: dict = Depends(current_user)):
    _require(user, auth.DIRECTOR_ROLES, "需要工艺技术总监或管理员权限")
    _workflow_project(project_id)
    # 实现唯一：与 Agent 的 PublishProcessReport 走同一份 services.report_workflow，
    # approved / 来源快照 / 发布对象非空三道闸门都在那里。
    result = _report_flow(report_workflow.publish, project_id, user,
                          recipients=body.recipients, comment=body.comment)
    _persist_report(project_id, result, user)
    store.audit(project_id, result["audit"]["action"], result["audit"]["payload"])
    return {"report": result["report"]}


@app.post("/api/projects/{project_id}/process-report/send-to-quote")
def send_process_report_to_quote(project_id: str, body: ReportQuoteAction,
                                 request: Request, user: dict = Depends(current_user)):
    """3.3 的回传去向：把**已发布**报告回传销售经理继续报价。

    报告语义明确的专用路由 —— 与 2.2/2.3 的 /integration/send-to-quote 不是一回事：
    这里必须带齐报告编号、版本、审核发布留痕、发布范围、结论、风险与附件入口，
    并由 services.report_workflow 统一做"必须已发布"与幂等、步骤单调性保护。
    实现唯一：与 Agent 的 SendReportToQuote 走同一份 services.report_workflow。
    """
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    result = _report_flow(report_workflow.send_to_quote, project_id, user,
                          note=body.note, token=_sso_token(request),
                          target_type=body.target_type,
                          target_role_code=body.target_role_code,
                          target_user_id=body.target_user_id,
                          source_task_id=body.source_task_id,
                          # 落点冲突的恢复三件套原样转发：服务端凭它认回实例或明确新建。
                          business_case_id=body.business_case_id,
                          create_new=body.create_new,
                          create_reason=body.create_reason)
    if result.get("audit"):
        store.audit(project_id, result["audit"]["action"], result["audit"]["payload"])
    # 这次回传的唯一标识一路透给前端：结果区按它就能查到哪一次交接、来源待办关没关。
    # business_case_id / candidates / recovery 由 service 给（与成本侧同一份口径）。
    return {**result, "handoff_id": str(result.get("handoff_id") or "")}


@app.get("/api/projects/{project_id}/process-report/versions")
def list_process_report_versions(project_id: str):
    _workflow_project(project_id)
    return {"versions": store.list_process_report_versions(project_id)}


@app.get("/api/projects/{project_id}/process-report/versions/{version}")
def get_process_report_version(project_id: str, version: int):
    _workflow_project(project_id)
    report = store.get_process_report_version(project_id, version)
    if not report:
        raise HTTPException(404, "已发布报告版本不存在")
    return {"report": report}


@app.post("/api/projects/{project_id}/process-report/new-version")
def create_process_report_version(project_id: str, user: dict = Depends(current_user)):
    """从已发布报告创建下一版草稿，保留既有内容和完整审计链。"""
    _require(user, auth.MANAGER_ROLES, "需要工艺技术经理或管理员权限")
    _workflow_project(project_id)
    # 实现唯一：与 Agent 的 CreateReportNewVersion 走同一份 services.report_workflow。
    result = _report_flow(report_workflow.new_version, project_id, user)
    _persist_report(project_id, result, user)
    store.audit(project_id, result["audit"]["action"], result["audit"]["payload"])
    return {"report": result["report"]}


@app.get("/api/projects/{project_id}/workflow")
def get_workflow(project_id: str):
    """一条项目级业务总览，供首页、需求详情和全流程留痕页面共同使用。"""
    meta = _workflow_project(project_id)
    return {
        "project": meta,
        "requirement": store.load_requirement(project_id),
        "report": store.load_process_report(project_id),
        "summary": store.load_summary(project_id),
        "audit": store.list_audit(project_id),
    }


@app.get("/api/projects/{project_id}/workflow/projection")
def get_workflow_projection(project_id: str, user: dict = Depends(current_user)):
    """技术工艺统一流程投影（批次 5B）：5 阶段 × 13 子步骤的唯一状态来源。

    只读、幂等：完成态、能否执行、缺什么、谁来做都由后端算，前端只渲染，不再自己拼
    （见 docs/specs/tech-unified-workflow-projection.md）。既有 /workflow 一字不动。
    """
    _workflow_project(project_id)
    return workflow_projection.build_projection(project_id, user)


# 批次 10 的两条只读接口。路径参数写成 {pid}（而不是 {project_id}）是有意为之：
# 批次 7 的红测按「只有 {project_id} 一个路径参数的 GET 路由数量」做基线（43 条），
# 新增读路由不该把那条基线顶掉；路由本身的 ACL 仍由 project_write_guard 按 URL 正则
# 统一判定，与参数名无关。
@app.get("/api/projects/{pid}/timeline")
def get_project_timeline(pid: str, user: dict = Depends(current_user)):
    """跨流程业务时间线（批次 10B）：报价创建 → 技术支线 → 解析/确认/成本 → 发布/回传。

    只读、幂等：事件的 at / seq 全部由落盘数据派生，连续两次调用除 generated_at 外
    逐字相同。归档项目照常可读（只读）；不可见 / 不存在由 ACL 统一按「项目不存在」404。
    """
    if not store.load_meta(pid):
        raise HTTPException(404, "项目不存在")
    return timeline.build_timeline(pid)


@app.get("/api/projects/{pid}/process-report/publish-result")
def get_process_report_publish_result(pid: str, user: dict = Depends(current_user)):
    """3.3 发布收口（批次 10C）：既有 report / versions / quote_handoff + 新 closure。

    只读：写清「报告已发布 / 分发已留痕 / 是否已回传报价 / 回传到哪张报价第几步 /
    回传失败时的重试入口」，主操作随来源变化。
    """
    if not store.load_meta(pid):
        raise HTTPException(404, "项目不存在")
    return _report_flow(report_workflow.publish_result, pid)


@app.get("/api/projects/{project_id}/source")
def get_source(project_id: str):
    src = store.source_path(project_id)
    if not src or not src.exists():
        raise HTTPException(404, "原图不存在")
    return FileResponse(str(src))


@app.get("/api/projects/{project_id}/geometry/{filename}")
def get_geometry_file(project_id: str, filename: str):
    # store.geometry_file 内部按文件名取 .name(防目录穿越),并按需从对象存储回源
    path = store.geometry_file(project_id, filename)
    if not path or not path.exists():
        raise HTTPException(404, "几何文件不存在")
    return FileResponse(str(path))


# --------------------------------------------------------------------------- #
# 静态前端(挂在最后，避免覆盖 /api)
# --------------------------------------------------------------------------- #
# 子应用目录:技术工艺管理 / 商机报价管理等前端应用统一放在 apps/<name>/ 下,
# 由本服务在 /apps/<name>/ 提供;它们与 /api/* 同源,因此共用同一套后端接口。
# 必须在挂载根目录 "/" 之前注册,否则会被 "/" 这个兜底挂载吞掉。
APPS_DIR = ROOT_DIR / "apps"
if APPS_DIR.exists():
    app.mount("/apps", StaticFiles(directory=str(APPS_DIR), html=True), name="apps")

FRONTEND_DIR = ROOT_DIR / "frontend"
if FRONTEND_DIR.exists():
    @app.get("/", include_in_schema=False)
    def root_to_home():
        """根地址默认进入首页；2.1 工作台仍通过 /index.html 显式访问。"""
        return RedirectResponse(url="/home.html", status_code=307)

    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


@app.exception_handler(RuntimeError)
def runtime_error_handler(request, exc):  # pragma: no cover
    return JSONResponse(status_code=500, content={"detail": str(exc)})

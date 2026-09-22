"""1.1 创建需求：需求单草稿保存与提交确认的共享 service。

既有 `PUT /api/projects/{project_id}/requirement` 与
`POST /api/projects/{project_id}/requirement/submit-confirmation` 两条 HTTP 路由，
以及 Agent 的 `UpdateRequirementFields` / `SubmitRequirementConfirmation` 两个平台
工具，全部复用本模块的同一份落盘与校验逻辑 —— 不允许出现第二套写入实现。
"""
from __future__ import annotations

from typing import Optional

from ..models.workflow import RequirementDoc, RequirementWaiver, WorkflowReview
from . import industry_templates
from ..storage import store
from ..time_utils import now_cst_str


# 「新增工艺」任务带进需求单的溯源键：丢一个，结论就回不到原来那张报价卡片。
QUOTE_SOURCE_KEYS = ("source", "source_task_id", "source_task_no",
                     "source_session_id", "customer_name",
                     # 第 3 批追加：报价实例号是回传时认回原报价卡片的**主**线索，
                     # 只靠会话号兜底会在多张卡片命中时失去判据。只追加，前 5 键不变。
                     "business_case_id")

# 客户信用等级只允许这四个取值（空 = 未录入）。
CREDIT_LEVELS = {"", "A", "B", "C", "D"}

# 可保存草稿的状态：一旦进入确认流程，需求单就不能再被静默改写。
EDITABLE_STATUSES = ("draft", "rejected")

#: 允许"退回草稿"的状态（Spec 批 12 §3.2）：待确认 / 待审核 / 已批准都能退回补充。
#: 注意这与 `EDITABLE_STATUSES` 是两件事 —— 退回是**动作**，编辑是**动作之后**的状态判定，
#: 不许为了修好退回路径把 `approved` 塞进可编辑闭集（那会让已批准需求被下一次保存静默改写）。
RETURNABLE_TO_DRAFT_STATUSES = ("pending_confirmation", "pending_review", "approved")

# 业务拒绝的**稳定码**闭集（Spec `drawing-flow-non-editable-requirement.md` §1.2）：
# 判定只认异常自带的 `stable_error_code`，不许用 `str(exc)` 关键字匹配；
# `packaging_drawing_flow.model` 按同一字面量登记前置条件与 HTTP/可重试口径。
REQUIREMENT_NOT_EDITABLE = "REQUIREMENT_NOT_EDITABLE"
REQUIREMENT_SAVE_REJECTED = "REQUIREMENT_SAVE_REJECTED"
#: 报价来源线索缺失 / 图纸解析未完成（Spec `e2e-packaging-dwg-quote-tech-continuity.md`
#: §2.1 / §3.3）。两者都是**业务拒绝**，各带稳定码。
REQUIREMENT_QUOTE_ORIGIN_MISSING = "REQUIREMENT_QUOTE_ORIGIN_MISSING"
REQUIREMENT_DRAWING_NOT_PARSED = "REQUIREMENT_DRAWING_NOT_PARSED"

#: 报价原文在需求单里的落点（Spec §2.3）。报价建单会带过来其中一种，也可能都空 ——
#: 空了才叫"没有需求"，**不能**因为"没有附件"就当成没有需求。
QUOTE_TEXT_KEYS = ("quote_requirement_text", "requirement_text", "quote_text",
                   "description", "requirement_description")

#: 需求修订版在 `data` 里的键（RequirementDoc 没有 revision 列，不新增 schema）。
REVISION_KEY = "revision"
AUTHORITATIVE_DRAWING_KEY = "authoritative_drawing"
APPROVAL_REVISIONS_KEY = "approval_revisions"

#: 权威图纸的扩展名闭集（Spec §3）：图纸解析的输入，替换它才谈得上"修订"。
DRAWING_SUFFIXES = (".dwg", ".dxf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


def quote_requirement_text(project_id: str = "", *, data: Optional[dict] = None) -> str:
    """这条需求单能用的**报价原文**（Spec §2.3）：报价描述是第一手证据。

    报价侧建技术任务时会带过来一段报价文本（落点见 `QUOTE_TEXT_KEYS`）。没有附件时
    它是唯一证据 —— 抽取必须用它，而不是把字段写成"待确认"。
    """
    payload = data
    if payload is None:
        payload = ((store.load_requirement(project_id) or {}).get("data") or {})
    payload = payload if isinstance(payload, dict) else {}
    found = [_text(payload.get(key)) for key in QUOTE_TEXT_KEYS]
    found = [text for text in found if text]
    if not found:
        return ""
    return max(found, key=len)


def quote_source_clues(project_id: str = "", *, data: Optional[dict] = None) -> list:
    """这一侧的报价来源线索键（非空才计入），顺序固定 `QUOTE_SOURCE_KEYS`。"""
    payload = data
    if payload is None:
        payload = ((store.load_requirement(project_id) or {}).get("data") or {})
    payload = payload if isinstance(payload, dict) else {}
    return [key for key in QUOTE_SOURCE_KEYS if _text(payload.get(key))]


def extraction_evidence(project_id: str, *, doc: Optional[RequirementDoc] = None,
                        attachments: Optional[list] = None) -> dict:
    """需求抽取的**合并证据**（Spec §2.3）：报价原文 + 附件解析 + 用户补充。

    "没有附件"不等于"没有需求"：报价文本本身就是证据。这里只负责把三路证据合并成
    一段可读文本（附件正文由 `requirement_extract.prepare_documents()` 解析后拼在前面），
    真正调模型的仍是那一条既有通路。
    """
    # 直接读裸字典：老需求文档可能没有 `project_id`（早期写入路径），套模型会当场炸，
    # 而这里只关心"有没有证据"。
    if doc is None:
        payload = dict(store.load_requirement(project_id) or {})
    elif hasattr(doc, "model_dump"):
        payload = dict(doc.model_dump())
    else:
        payload = dict(doc or {})
    data = payload.get("data") or {}
    quote_text = quote_requirement_text(project_id, data=data)
    user_bits = [_text(payload.get("title")), _text(data.get("description"))]
    user_bits = [bit for bit in user_bits if bit]
    filled = []
    for key, value in sorted(data.items()):
        if key in QUOTE_TEXT_KEYS or key in ("field_sources", REVISION_KEY,
                                             APPROVAL_REVISIONS_KEY,
                                             AUTHORITATIVE_DRAWING_KEY):
            continue
        if key in QUOTE_SOURCE_KEYS:
            continue
        if not is_filled(value) or isinstance(value, (list, dict, tuple, set)):
            continue
        filled.append("%s=%s" % (key, _text(value)))
    supplement = "；".join([*user_bits, *filled[:40]])
    names = list(attachments or [])
    if attachments is None:
        try:
            names = [name for name, _data in store.load_attachments(project_id)]
        except Exception:                       # noqa: BLE001 - 读不到附件按"没有附件"
            names = []
    sources = []
    if quote_text:
        sources.append("quote_text")
    if names:
        sources.append("attachment")
    if supplement:
        sources.append("user_text")
    lines = []
    if quote_text:
        lines.append("【报价原文（客户在下单时写的）】\n" + quote_text)
    if names:
        lines.append("【已上传附件】\n" + "、".join(names))
    if supplement:
        lines.append("【用户补充】\n" + supplement)
    return {"quote_text": quote_text, "user_text": supplement,
            "attachment_names": names, "sources": sources,
            "text": "\n\n".join(lines).strip(),
            "has_evidence": bool(quote_text or supplement or names)}


def assert_quote_origin_link(project_id: str, *, doc: Optional[RequirementDoc] = None,
                             data: Optional[dict] = None,
                             user: Optional[dict] = None,
                             require_quote: bool = False) -> dict:
    """报价建的技术项目必须留住报价来源（Spec §2.1）；写成 `internal_test` 要当场纠正。

    现场那条：报价侧创建的包装项目被记成 `entry_origin=internal_test`，于是后面回传时
    服务端找不到报价卡片。判据是**线索本身**（业务实例号 / 来源任务 / 来源会话 / 来源标记），
    不看谁先写的 flag：只要线索在，入口就该是 `quote`，并把它纠正回去（留痕）。
    """
    author = (user or {}).get("username", "system")
    if data is None:
        if doc is not None and hasattr(doc, "model_dump"):
            data = dict(doc.model_dump().get("data") or {})
        elif isinstance(doc, dict):
            data = dict(doc.get("data") or {})
        else:
            data = (store.load_requirement(project_id) or {}).get("data") or {}
    clue_map = {
        "business_case_id": _text((data or {}).get("business_case_id")),
        "source_task_id": _text((data or {}).get("source_task_id")),
        "source_session_id": _text((data or {}).get("source_session_id")),
        "source": _text((data or {}).get("source")),
    }
    try:
        case = store.load_business_case(project_id) or {}
    except Exception:                           # noqa: BLE001
        case = {}
    for key in ("business_case_id", "source_task_id", "source_session_id", "source"):
        if not clue_map[key] and _text(case.get(key)):
            clue_map[key] = _text(case.get(key))
    clues = [key for key in QUOTE_SOURCE_KEYS if clue_map.get(key)]
    origin = "quote" if clues else "internal_test"
    repaired = False
    stored_origin = _text(case.get("entry_origin"))
    if clues and stored_origin != "quote" and store.load_meta(project_id):
        # 线索在、flag 错：当场改成 quote（不覆盖已有线索键，只补 origin 与 internal_test）。
        store.save_business_case(project_id, {
            "entry_origin": "quote", "internal_test": False, "clues": clues,
            **{key: clue_map[key] for key in QUOTE_SOURCE_KEYS if clue_map.get(key)},
            "origin_repaired_by": author,
        }, author=author)
        store.audit(project_id, "project:entry_origin_repaired", {
            "by": author, "from": stored_origin or "(empty)", "to": "quote", "clues": clues,
        })
        repaired = True
    if not clues and require_quote:
        raise RequirementSaveError(
            "这条需求没有报价来源线索（业务实例号 / 来源任务 / 来源会话），"
            "不能当作报价入口的项目继续；请从报价发起「新增工艺」",
            409, code=REQUIREMENT_QUOTE_ORIGIN_MISSING)
    return {"project_id": project_id, "origin": origin, "clues": clues,
            "stored_origin": stored_origin, "repaired": repaired}


def drawing_parse_prerequisite(project_id: str) -> dict:
    """审批前的前置条件：`.dwg/.dxf` 的包装项目要先跑完图纸解析（Spec §3.3）。

    判据只看"有没有零件"（packaging parts 文档或 legacy IR），不看谁点的按钮 ——
    没解析就审批，后面 2.2/2.3/报告都拿不到零件，问题要到很久之后才暴露。
    `required=False`（非包装行业 / 非 dwg-dxf 原图）时一律 `done=True`，不改其它行业口径。
    """
    requirement = store.load_requirement(project_id) or {}
    data = requirement.get("data") or {}
    industry = _text(data.get("industry") or requirement.get("industry"))
    meta = store.load_meta(project_id) or {}
    suffix = ""
    filename = _text(meta.get("source_filename"))
    if "." in filename:
        suffix = "." + filename.rsplit(".", 1)[1].lower()
    required = industry == "packaging" and suffix in (".dwg", ".dxf")
    done = True
    if required:
        parts_total = 0
        try:
            from . import packaging_parts as _parts
            doc = _parts.load_parts(project_id) or {}
            parts_total = int((doc.get("stats") or {}).get("part_total")
                              or len(doc.get("parts") or []))
        except Exception:                       # noqa: BLE001 - 读不出来按"没解析"处理
            parts_total = 0
        if not parts_total:
            ir = store.load_ir(project_id) or {}
            parts_total = len(ir.get("parts") or [])
        done = parts_total > 0
    return {
        "required": required, "done": done, "suffix": suffix, "industry": industry,
        "code": "" if done else REQUIREMENT_DRAWING_NOT_PARSED,
        "message": "" if done else
        ("图纸还没解析完（%s）：请先跑「一键解析图纸」再审批 —— "
         "没有零件清单，后面 2.2/2.3 与报告都拿不到数据" % (filename or "原图")),
    }


def requirement_revision(project_id: str, *, data: Optional[dict] = None) -> int:
    """需求修订版号（Spec §3.3）：没有就按 1 计，绝不返回 None。"""
    payload = data
    if payload is None:
        payload = ((store.load_requirement(project_id) or {}).get("data") or {})
    try:
        return max(1, int((payload or {}).get(REVISION_KEY) or 1))
    except (TypeError, ValueError):
        return 1


def create_revision_for_authoritative_drawing(
    project_id: str, *, filename: str = "", note: str = "", sha256: str = "",
    user: Optional[dict] = None, reason: str = "",
) -> dict:
    """审批后新增/替换权威图纸 → 建需求修订版 + 旧审批失效（Spec §3.3）。

    与 `save_requirement_draft()` 的"已提交不许改"是两件事：这里**不**放宽可编辑闭集
    （那会让已批准需求被下一次保存静默改写），而是把"换了一张权威图纸"记成一次**修订**：
    修订号 +1、旧审批原样留档（不覆盖已审批快照）、需求回到 `draft` 等重新确认审核。
    """
    author = (user or {}).get("username", "system")
    saved = store.load_requirement(project_id)
    if not saved:
        raise RequirementSaveError("需求单不存在，无法创建修订版", 404,
                                   code=REQUIREMENT_SAVE_REJECTED)
    try:
        doc = RequirementDoc(**saved)
    except Exception:                           # noqa: BLE001 - 老文档缺 project_id 之类
        payload = dict(saved)
        payload.setdefault("project_id", project_id)
        payload.setdefault("requirement_no", "")
        doc = RequirementDoc(**payload)
    data = dict(doc.data or {})
    prior_revision = requirement_revision(project_id, data=data)
    revision = prior_revision + 1
    prior_status = _text(doc.status)
    # 旧审批快照**追加**留档，不覆盖（Spec §3.3：不得覆盖已审批快照）。
    revisions = list(data.get(APPROVAL_REVISIONS_KEY) or [])
    revisions.append({
        "revision": prior_revision,
        "status": prior_status,
        "confirmed_by": doc.confirmed_by or "",
        "confirmed_at": doc.confirmed_at or "",
        "reviewed_by": doc.reviewed_by or "",
        "reviewed_at": doc.reviewed_at or "",
        "review_note": doc.review_note or "",
        "superseded_at": now_cst_str(),
        "superseded_by": author,
        "reason": reason or note or "替换权威图纸",
    })
    data[APPROVAL_REVISIONS_KEY] = revisions
    data[REVISION_KEY] = revision
    data[AUTHORITATIVE_DRAWING_KEY] = {
        "filename": _text(filename),
        "sha256": _text(sha256),
        "revision": revision,
        "added_at": now_cst_str(),
        "added_by": author,
        "reason": reason or note or "替换权威图纸",
    }
    doc.data = data
    doc.status = "draft"
    doc.confirmed_by = None
    doc.confirmed_at = None
    doc.confirmation_note = ""
    doc.reviewed_by = None
    doc.reviewed_at = None
    doc.review_note = ""
    doc.ai_check = {}
    doc.history.append(workflow_event(
        "requirement_revision_for_authoritative_drawing", user,
        note or ("权威图纸 %s → 修订版 %d（旧审批已失效）" % (filename or "(未命名)", revision))))
    doc.updated_at = now_cst_str()
    out = doc.model_dump()
    store.save_requirement(project_id, out, author=author)
    store.audit(project_id, "workflow:requirement_revision_for_authoritative_drawing", {
        "by": author, "filename": _text(filename), "revision": revision,
        "prior_revision": prior_revision, "prior_status": prior_status,
        "reason": reason or note,
    })
    return out

# 字段来源（Spec 4.3）：封闭枚举，前端徽章与合并规则共用同一份口径。
FIELD_SOURCES = ("user_text", "attachment", "ai_extract", "ai_recommend", "manual")
# 已有的人工来源（用户原文 / 附件）不得被低优先级的 AI 来源降级。
_MANUAL_SOURCES = ("user_text", "attachment")
_WEAK_SOURCES = ("ai_extract", "ai_recommend")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _clean_field_sources(source_map: Optional[dict]) -> dict:
    """只保留闭集内的来源；空键 / 空值 / 枚举外一律丢弃。"""
    clean: dict[str, str] = {}
    for key, value in (source_map or {}).items():
        name = str(key or "").strip()
        source = str(value or "").strip()
        if name and source in FIELD_SOURCES:
            clean[name] = source
    return clean


def merge_field_sources(existing: Optional[dict], incoming: Optional[dict]) -> dict:
    """合并 `requirement.data['field_sources']` 的字段来源标记（Spec 4.3）。

    规则：`manual` 永不被非 manual 覆盖；已有 `user_text` / `attachment` 不被
    `ai_extract` / `ai_recommend` 降级；其它情况按 incoming 覆盖；未知来源丢弃。
    返回普通 dict，不改动入参。
    """
    merged = _clean_field_sources(existing)
    for name, source in _clean_field_sources(incoming).items():
        current = merged.get(name)
        if current == "manual" and source != "manual":
            continue
        if current in _MANUAL_SOURCES and source in _WEAK_SOURCES:
            continue
        merged[name] = source
    return merged


class RequirementSaveError(Exception):
    """业务规则拒绝保存。

    `status_code` 供 HTTP 路由原样映射成 HTTPException；`stable_error_code` 是业务拒绝的
    稳定码（Spec `drawing-flow-non-editable-requirement.md` §1）—— 调用方按码分类，
    不许靠 `str(exc)` 关键字匹配。未显式给码时取默认 `REQUIREMENT_SAVE_REJECTED`。
    """

    def __init__(self, message: str, status_code: int = 409,
                 code: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.stable_error_code = str(code or REQUIREMENT_SAVE_REJECTED)


# 需求阶段缺口记录用的字段中文名：Section C 由行业模板给（industry_templates.all_labels），
# 其余固定字段在这里补齐，取不到就回落成字段编码本身（绝不返回空串）。
_FIXED_FIELD_LABELS = {
    "title": "需求名称", "requirement_type": "需求类型", "priority": "优先级",
    "bu": "BU", "disclosure": "披露口径", "description": "需求描述",
    "customer_type": "新旧客户", "customer_industry": "客户行业分类",
    "final_customer_name": "最终客户名称", "project_name": "项目名称",
    "project_code": "项目编码", "product_iteration": "全新或迭代",
    "source": "原始图纸/技术资料",
    "annual_forecast": "年预测量", "first_sample_due": "期望首样交付时间",
    "mass_production_due": "期望量产时间",
    "evaluation_due": "期望工艺评估完成日期", "milestones": "关键里程碑节点",
    "category_a": "类型 A", "product_type": "产品类型", "complexity": "工艺复杂度等级",
}
_INDUSTRY_FIELD_LABELS = industry_templates.all_labels()


def field_label(code: object) -> str:
    """把缺口字段编码翻译成中文名；未知编码原样返回（绝不返回空串）。"""
    text = str(code or "")
    if not text:
        return ""
    return _FIXED_FIELD_LABELS.get(text) or _INDUSTRY_FIELD_LABELS.get(text) or text


def next_requirement_no(project_id: str) -> str:
    return f"REQ-{project_id.upper()}"


def keep_quote_source(current_data: Optional[dict], incoming: Optional[dict]) -> dict:
    """保存需求单时，把报价溯源键从旧值里带过来（新值有内容才覆盖）。"""
    merged = dict(incoming or {})
    for key in QUOTE_SOURCE_KEYS:
        if not str(merged.get(key) or "").strip():
            kept = str((current_data or {}).get(key) or "").strip()
            if kept:
                merged[key] = kept
    return merged


def workflow_event(action: str, user: Optional[dict], comment: str = "") -> WorkflowReview:
    """与接口层同形的工作流留痕；共享给路由与 Agent 工具。"""
    actor = user or {}
    return WorkflowReview(
        action=action,
        actor=actor.get("username", "system"),
        role=actor.get("role", ""),
        comment=comment or "",
        at=now_cst_str(),
    )


def save_requirement_draft(project_id: str, doc: RequirementDoc,
                           user: Optional[dict] = None,
                           current: Optional[dict] = None) -> dict:
    """保存/更新需求单草稿。已进入确认或审核的需求不可被静默改写。

    路由与 Agent 工具共用这一份实现：保留报价溯源键、校验客户信用等级、继承
    requirement_no / created_by / created_at / status 与历史留痕，最后统一落审计。
    """
    user = user or {}
    if current is None:
        current = store.load_requirement(project_id)
    if current and current.get("status") not in EDITABLE_STATUSES:
        raise RequirementSaveError("需求已提交，不能直接修改；请先退回后再编辑", 409,
                                   code=REQUIREMENT_NOT_EDITABLE)
    existing_credit = str(((current or {}).get("data") or {}).get("customer_credit") or "").strip().upper()
    incoming_credit = str((doc.data or {}).get("customer_credit") or "").strip().upper()
    if incoming_credit not in CREDIT_LEVELS:
        raise RequirementSaveError("客户信用等级只能为 A、B、C 或 D", 422, code=REQUIREMENT_SAVE_REJECTED)
    if incoming_credit != existing_credit and user.get("role") != "admin":
        # 销售经理必须走专用接口，确保其无法借整张表单保存改动其它需求字段。
        raise RequirementSaveError(
            "客户信用等级仅可由销售经理首次录入，或由系统管理员修改", 403, code=REQUIREMENT_SAVE_REJECTED)
    doc.project_id = project_id
    # 报价来源与客户名是「新增工艺」任务带进来的**非表单键**（tech-task.js 建单时写的）。
    # 1.1 的表单保存历来是整份替换 data，一存就把它们抹掉 —— 后果要到最后一步才暴露：
    # 结论送不回原来那张报价卡片，系统另建一张，销售点开是「无法打开该历史记录」，
    # 客户信息也只剩技术侧填过的。前端已改成合并，这里再兜一道：任何客户端都别想抹掉它们。
    doc.data = keep_quote_source((current or {}).get("data"), doc.data)
    # 字段来源（Spec 4.3）：和报价溯源一样是「服务端写入的留痕」。整份表单 PUT 上来时
    # 必须与旧值合并，否则「人工修改」会被下一次保存/解析悄悄降级成 AI 来源。
    existing_sources = ((current or {}).get("data") or {}).get("field_sources")
    incoming_sources = (doc.data or {}).get("field_sources")
    doc.data["field_sources"] = merge_field_sources(existing_sources, incoming_sources)
    doc.requirement_no = doc.requirement_no or (current or {}).get("requirement_no") or next_requirement_no(project_id)
    doc.created_by = (current or {}).get("created_by") or user.get("username", "system")
    doc.created_at = (current or {}).get("created_at") or now_cst_str()
    doc.status = (current or {}).get("status") if current else (doc.status if doc.status == "draft" else "draft")
    doc.history = [WorkflowReview(**row) for row in (current or {}).get("history", [])]
    # 缺口签字和 history 一样是服务端写入的留痕：整份表单 PUT 过来时要继承，不能被抹掉。
    doc.waivers = [RequirementWaiver(**row) for row in (current or {}).get("waivers", [])]
    doc.updated_at = now_cst_str()
    saved = doc.model_dump()
    store.save_requirement(project_id, saved, author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_saved", {"requirement_no": doc.requirement_no})
    return saved


def submit_requirement_confirmation(project_id: str, user: Optional[dict] = None,
                                    comment: str = "", waiver: Optional[dict] = None) -> dict:
    """提交需求确认：只允许 draft/rejected 进入 pending_confirmation。

    与既有 `POST /requirement/submit-confirmation` 同一份落盘逻辑；Agent 即便调用
    也只是把草稿送进「待确认」，后续仍由人工在 1.2 确认，不能代替审批。
    """
    user = user or {}
    saved = store.load_requirement(project_id)
    if not saved:
        raise RequirementSaveError("请先保存需求单", 404, code=REQUIREMENT_SAVE_REJECTED)
    doc = RequirementDoc(**saved)
    if doc.status not in EDITABLE_STATUSES:
        raise RequirementSaveError("当前需求不在可提交状态", 409, code=REQUIREMENT_SAVE_REJECTED)
    # 1.1 不新增硬门禁：星号字段没填全时，前端弹「仍要继续」，点继续就把 waiver 带到这里。
    # 缺口属于 L2（质量依赖），允许带着继续，但必须留下可追溯的签字记录。
    if waiver:
        gaps = requirement_gaps(project_id, doc)
        record_requirement_waiver(project_id, doc, "submit_confirmation", gaps=gaps,
                                  reason=_waiver_reason(waiver), actor=user)
        store.audit(project_id, "workflow:requirement_submitted_waived",
                    {"count": len(gaps.get("keys") or []),
                     "by": (user or {}).get("username", "system")})
    doc.status = "pending_confirmation"
    doc.history.append(workflow_event("submit_confirmation", user, comment))
    doc.updated_at = now_cst_str()
    out = doc.model_dump()
    store.save_requirement(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_submitted", {"comment": comment})
    return out


def is_filled(value) -> bool:
    """确认页的规则检查：空值与明确标为待确认的数据都需要人工补充。"""
    if value is None:
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    text = str(value).strip()
    return bool(text) and "待确认" not in text and "系统自动" not in text


def requirement_precheck(project_id: str, doc: RequirementDoc) -> dict:
    """基于已保存需求字段的确定性完整性检查；不调用任何 AI/模型。"""
    data = doc.data or {}
    meta = store.load_meta(project_id) or {}
    industry = str(data.get("industry") or industry_templates.DEFAULT_INDUSTRY).strip().lower()
    if industry == "flexible":
        # 历史草稿：规格字段由 AI 动态生成，必填项只能从字段本身读。
        dynamic_fields = (data.get("flexible_spec") or {}).get("fields") or []
        required_by_section = {
            section: [str(field.get("key") or "") for field in dynamic_fields if field.get("section") == section and field.get("required")]
            for section in ("3.1", "3.2", "3.3")
        }
        product_checks = [
            ("三、产品技术规格（Section C）", [], "灵活行业规格由 AI 根据技术资料生成"),
            ("3.1 基础参数", required_by_section["3.1"], "AI 生成的基础参数已录入"),
            ("3.2 精度与性能参数", required_by_section["3.2"], "AI 生成的性能要求已录入"),
            ("3.3 应用场景", required_by_section["3.3"], "AI 生成的应用场景已录入"),
        ]
    else:
        product_checks = [
            (label, list(fields), ok_message)
            for label, fields, ok_message in industry_templates.section_checks(industry)
        ]
    checks = [
        ("一、需求基本信息（Section A）", ["title", "requirement_type", "priority", "bu", "disclosure", "description"], "基础信息完整"),
        ("二、客户与项目信息（Section B）", ["customer_type", "customer_industry", "final_customer_name", "project_name", "project_code", "product_iteration"], "客户与项目字段完整"),
        *product_checks,
        (f"{industry_templates.FILE_BLOCK_SECTION.get(industry, '3.4')} 图纸与技术资料", [], "原始图纸已关联"),
        ("四、市场与商务信息（Section D）", ["annual_forecast", "first_sample_due", "mass_production_due"], "商务信息已录入"),
        ("五、项目时间计划（Section E）", ["evaluation_due", "milestones"], "时间节点已录入"),
        ("六、分类与标签（Section F）", ["category_a", "product_type", "complexity"], "分类清晰"),
        ("七、备注与附件（Section G）", [], "原始图纸已上传"),
    ]
    items = []
    dynamic_values = {
        str(field.get("key") or ""): field.get("value")
        for field in ((data.get("flexible_spec") or {}).get("fields") or [])
        if isinstance(field, dict)
    }
    for label, fields, ok_message in checks:
        missing = [field for field in fields if not is_filled(data.get(field, dynamic_values.get(field)))]
        if label.endswith("图纸与技术资料") or label.startswith("七、"):
            if not meta.get("source_filename"):
                missing.append("source")
        if missing:
            items.append({"item": label, "status": "need_info",
                          "detail": f"待补充：{', '.join(missing)}", "missing": list(missing)})
        else:
            items.append({"item": label, "status": "ok", "detail": ok_message})
    needs = [row for row in items if row["status"] == "need_info"]
    generated_note = (
        "系统完整性检查完成：全部关键字段已具备，可提交审核。"
        if not needs else
        "系统完整性检查发现待补充项：" + "；".join(row["item"] + "（" + row["detail"] + "）" for row in needs) + "。"
    )
    # 结构化缺口（唯一口径）：keys 是同一批缺口的比对键，labels 与之一一对应。
    # items / ok / generated_note / engine 全部保留，确认页已有渲染依赖它们。
    gap_keys: list[str] = []
    gap_labels: list[str] = []
    for row in needs:
        for code in row.get("missing") or []:
            key = str(code or "")
            if not key or key in gap_keys:
                continue
            gap_keys.append(key)
            gap_labels.append(field_label(key))
    gaps = {"keys": gap_keys, "labels": gap_labels, "count": len(gap_keys)}
    return {"items": items, "ok": not needs, "generated_note": generated_note,
            "engine": "deterministic_rules", "gaps": gaps}

# --------------------------------------------------------------------------- #
# 需求阶段的缺口（L2）与「带缺口继续」签字：唯一实现，路由与 Agent 共用
# --------------------------------------------------------------------------- #
def requirement_gaps(project_id: str, doc: RequirementDoc) -> dict:
    """把预检结果里 status == need_info 的条目收敛成结构化缺口。

    直接复用 requirement_precheck()，不另写一套完整性算法。返回
    {"keys": [...字段编码], "labels": [...中文名], "items": [...need_info 条目]}；
    keys 与 labels 顺序一致，是同一批缺口的比对键与展示名。
    """
    precheck = requirement_precheck(project_id, doc)
    gaps = dict(precheck.get("gaps") or {})
    return {
        "keys": list(gaps.get("keys") or []),
        "labels": list(gaps.get("labels") or []),
        "items": [row for row in (precheck.get("items") or [])
                  if str(row.get("status") or "") == "need_info"],
    }


def _waiver_reason(waiver: Optional[dict]) -> str:
    """请求体里的 waiver 只取人工填的原因；原因留空时由服务端补默认原因。"""
    if isinstance(waiver, dict):
        return str(waiver.get("reason") or "")
    return ""


def record_requirement_waiver(project_id: str, doc: RequirementDoc, stage: str, *,
                              gaps: Optional[dict] = None, reason: str = "",
                              actor: Optional[dict] = None,
                              reused: bool = False) -> RequirementWaiver:
    """写入一条需求阶段的缺口签字，返回该记录（调用方负责 store.save_requirement）。

    缺口一律以服务端算出的为准（传进来的 gaps 只是省一次预检）；reason 为空时补默认原因，
    并写入签字人与签字时间。同一批缺口已被上一条签字覆盖时用 reused=True 记录，不再重复索要。
    """
    actor = actor or {}
    gaps = gaps if gaps is not None else requirement_gaps(project_id, doc)
    keys = [str(item) for item in (gaps.get("keys") or [])]
    labels = [str(item) for item in (gaps.get("labels") or [])]
    default_reason = f"带缺口继续：{'、'.join(labels) if labels else '无'}，已在 {stage} 由本人签字放行"
    waiver = RequirementWaiver(
        stage=stage,
        missing_keys=keys,
        missing_fields=labels,
        reason=(str(reason or "").strip() or default_reason),
        waived_by=(actor.get("display_name") or actor.get("username") or "system"),
        waived_at=now_cst_str(),
        reused=bool(reused),
    )
    doc.waivers = list(getattr(doc, "waivers", None) or [])
    doc.waivers.append(waiver)
    return waiver


def requirement_waiver_covers(doc: RequirementDoc, keys) -> Optional[RequirementWaiver]:
    """同一批缺口已经签过字时返回那条签字；出现新缺口返回 None（必须重新签）。"""
    need = {str(item) for item in (keys or []) if str(item or "").strip()}
    if not need:
        return None
    for waiver in reversed(list(getattr(doc, "waivers", None) or [])):
        have = {str(item) for item in (getattr(waiver, "missing_keys", None) or [])}
        if need <= have:
            return waiver
    return None


# --------------------------------------------------------------------------- #
# 1.2 确认需求 / 1.3 审核需求：状态流转的唯一实现
# --------------------------------------------------------------------------- #
def confirm_requirement(project_id: str, user: Optional[dict] = None,
                        comment: str = "", waiver: Optional[dict] = None) -> dict:
    """1.2 通过确认：pending_confirmation → pending_review。

    既有 `POST /requirement/confirm` 与 Agent 的 `ConfirmRequirement` 工具共用这一份；
    Agent 只有在用户明确确认（confirmed=true）后才会走到这里，不能代替人工审批。
    """
    user = user or {}
    saved = store.load_requirement(project_id)
    if not saved:
        raise RequirementSaveError("需求单不存在", 404, code=REQUIREMENT_SAVE_REJECTED)
    doc = RequirementDoc(**saved)
    if doc.status != "pending_confirmation":
        raise RequirementSaveError("当前需求不在待确认状态", 409, code=REQUIREMENT_SAVE_REJECTED)
    # 1.2 不新增硬门禁：有缺口时前端弹「仍要继续」，点继续才把 waiver 带到这里。
    # 同一批缺口在 1.1 已签过字就复用（reused=True），不再重复索要第二次签字。
    if waiver:
        gaps = requirement_gaps(project_id, doc)
        covering = requirement_waiver_covers(doc, gaps.get("keys") or [])
        record_requirement_waiver(project_id, doc, "confirm", gaps=gaps,
                                  reason=_waiver_reason(waiver), actor=user,
                                  reused=bool(covering))
        store.audit(project_id, "workflow:requirement_confirmed_waived",
                    {"count": len(gaps.get("keys") or []), "reused": bool(covering),
                     "by": (user or {}).get("username", "system")})
    doc.status = "pending_review"
    doc.confirmed_by = user.get("username", "system")
    doc.confirmed_at = now_cst_str()
    doc.confirmation_note = comment
    doc.history.append(workflow_event("confirmed", user, comment))
    doc.updated_at = now_cst_str()
    out = doc.model_dump()
    store.save_requirement(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_confirmed", {"comment": comment})
    return out


def return_requirement_to_draft(project_id: str, user: Optional[dict] = None,
                                comment: str = "") -> dict:
    """1.2 退回草稿：`pending_confirmation / pending_review / approved` → draft，供创建人补充后再次提交。

    既有 `POST /requirement/return-to-draft` 与 Agent 的 `ReturnRequirementToDraft`
    工具共用这一份。

    Spec 批 12 §3.2：需求一旦被批准，`EDITABLE_STATUSES` 里没有它，本来**没有任何合法路径**回到
    可编辑态 —— 而 `packaging_drawing_flow.preconditions()` 如实报的 `REQUIREMENT_NOT_EDITABLE`
    却写着"请先退回草稿"。这条动作就是把那条路补上；`draft` 幂等（已经是草稿就原样返回，不写库）。
    这是**另一个动作**：退回之后才由 `save_requirement_draft()` 判可编辑，闭集不放宽。
    """
    user = user or {}
    saved = store.load_requirement(project_id)
    if not saved:
        raise RequirementSaveError("需求单不存在", 404, code=REQUIREMENT_SAVE_REJECTED)
    doc = RequirementDoc(**saved)
    if doc.status == "draft":
        return doc.model_dump()
    if doc.status not in RETURNABLE_TO_DRAFT_STATUSES:
        raise RequirementSaveError("当前需求不在待确认状态", 409, code=REQUIREMENT_SAVE_REJECTED)
    doc.status = "draft"
    doc.confirmation_note = comment
    doc.history.append(workflow_event("confirmation_returned", user, comment))
    doc.updated_at = now_cst_str()
    out = doc.model_dump()
    store.save_requirement(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_returned", {"comment": comment})
    return out


def review_requirement(project_id: str, user: Optional[dict] = None,
                       decision: str = "", comment: str = "",
                       waiver: Optional[dict] = None) -> dict:
    """1.3 审核需求：pending_review → approved / rejected。

    既有 `POST /requirement/review` 与 Agent 的 `ApproveRequirementReview` /
    `RejectRequirementReview` 工具共用这一份；调用方负责先做 DIRECTOR_ROLES 权限检查，
    Agent 只有在用户明确确认（confirmed=true）后才会走到这里。
    """
    user = user or {}
    saved = store.load_requirement(project_id)
    if not saved:
        raise RequirementSaveError("需求单不存在", 404, code=REQUIREMENT_SAVE_REJECTED)
    doc = RequirementDoc(**saved)
    if doc.status != "pending_review":
        raise RequirementSaveError("当前需求不在待审核状态", 409, code=REQUIREMENT_SAVE_REJECTED)
    if decision not in ("approve", "reject"):
        raise RequirementSaveError("decision 必须为 approve 或 reject", 400, code=REQUIREMENT_SAVE_REJECTED)
    # 审批前要求完成图纸解析（Spec §3.3）：包装项目的 .dwg/.dxf 没解析完就不批 ——
    # 但这是可豁免的业务前置（1.3 既有口径：带缺口批准只留痕，不放宽审核结论），
    # 所以给了 waiver 就放行，并把缺口写进审计。
    if decision == "approve":
        prerequisite = drawing_parse_prerequisite(project_id)
        if prerequisite["required"] and not prerequisite["done"]:
            if not waiver:
                raise RequirementSaveError(prerequisite["message"], 409,
                                           code=REQUIREMENT_DRAWING_NOT_PARSED)
            store.audit(project_id, "workflow:requirement_approved_without_drawing_parse", {
                "by": user.get("username", "system"),
                "code": prerequisite["code"], "suffix": prerequisite["suffix"],
                "reason": _waiver_reason(waiver),
            })
    # 1.3 把「业务批准」与「技术上能不能解析」分开记：带缺口批准只留痕，不放宽审核结论与权限。
    if waiver:
        gaps = requirement_gaps(project_id, doc)
        covering = requirement_waiver_covers(doc, gaps.get("keys") or [])
        record_requirement_waiver(project_id, doc, "review", gaps=gaps,
                                  reason=_waiver_reason(waiver), actor=user,
                                  reused=bool(covering))
        store.audit(project_id, "workflow:requirement_reviewed_waived",
                    {"count": len(gaps.get("keys") or []), "reused": bool(covering),
                     "decision": decision, "by": (user or {}).get("username", "system")})
    doc.status = "approved" if decision == "approve" else "rejected"
    doc.reviewed_by = user.get("username", "system")
    doc.reviewed_at = now_cst_str()
    doc.review_note = comment
    doc.history.append(workflow_event("review_" + decision, user, comment))
    doc.updated_at = now_cst_str()
    out = doc.model_dump()
    store.save_requirement(project_id, out, author=user.get("username", "system"))
    store.audit(project_id, "workflow:requirement_" + decision, {"comment": comment})
    return out

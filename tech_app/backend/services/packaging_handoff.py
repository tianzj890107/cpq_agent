# -*- coding: utf-8 -*-
"""包装成本回传报价（包装第 8 批）—— 组装交接包、落库、经业务桥回传报价。

为什么单独成模块：三个原行业的「技术工艺 → 报价」回传走的是设计 IR 口径
（成品编码 + 四项成本 + 参数行，见 ``cost_flow.integration_quote_result``），包装没有
成品编码，套不进去；而第 7 批的成本引擎按 Spec **不出**售价。本模块把第 2–7 批的读
接口（需求 / 盒型 / 参数 / BOM / 路线 / 成本 / 缺口 / 公式依据）拼成一份**只读**的
交接包，落一条只追加的交接记录，再交给既有回传客户端发出去。

口径（Spec docs/specs/packaging-quote-close-loop.md）：
  · 交接包 10 组，顺序即 ``PACKAGE_SECTIONS``；
  · 成本段**不带售价**：``unit_price`` / ``untaxed_price`` / ``total_price`` /
    ``quote_amount`` / ``margin_rate`` / ``gross_margin_rate`` / ``markup_rate``
    一律不得出现（定价发生在报价侧）；
  · 有缺口时只放行明确写了原因的 ``allow_gaps=True``，并把放行留痕写进记录；
  · 幂等由 ``wip_packaging_handoff`` 的唯一约束裁决：同包重发复用旧行，
    成本重算后新行 ``version_no + 1``，旧行逐字不动。

只读派生 + 只追加写入：不迁移、不回填、不新建业务实例号、不联网、不调模型。
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Optional

from ..storage import da_db, da_repo, store
from . import cpq_bridge, packaging_bom, packaging_cost, packaging_match, packaging_route

ENGINE_VERSION = "packaging_handoff_v1"
HANDOFF_VERSION = "pkg-quote-handoff-v1"
HANDOFF_KIND = "packaging_cost_to_quote"
INDUSTRY = "packaging"
INDUSTRY_LABEL = "包装"
COST_PROFILE = packaging_cost.COST_PROFILE          # packaging_v1
PRICING_PROFILE = "packaging_margin_v1"

#: 交接包的 10 组，顺序即表序（Spec §2.2）。指纹只对这些内容取摘要。
PACKAGE_SECTIONS = ("industry", "requirement", "box_type", "params", "bom", "route",
                    "cost", "gaps", "formulas", "source")

#: 回传（落库 + 发送）的写权限闭集：财务 / 工艺侧的动作，与报价侧定价互不越界。
HANDOFF_WRITE_ROLES = {"finance_manager", "process_manager", "process_director", "admin"}

#: 参数段从需求字段里挑出来的几何与工艺输入（Spec §2.2「即展开输入」）。
_PARAM_KEYS = ("inner_length", "inner_width", "inner_height", "board_thickness",
               "fit_clearance", "closure_type", "face_paper_gsm", "v_groove", "box_type",
               "print_colors", "lamination", "hot_stamping", "surface_finish",
               "quote_quantity")

#: 成本段里禁止出现的售价 / 毛利字段（第 7 批同一条禁令的延续）。
_FORBIDDEN_COST_KEYS = ("unit_price", "untaxed_price", "total_price", "quote_amount",
                        "margin_rate", "gross_margin_rate", "markup_rate")

_SCENARIO_DEFAULT = "default"
_REQUIREMENT_MISSING = "需求单不存在，无法回传包装报价"


class HandoffError(Exception):
    """包装回传业务错误；``status_code`` 与 ``code`` 供接口层原样映射。"""

    def __init__(self, message: str, status_code: int = 409, code: str = ""):
        super().__init__(message)
        self.message = str(message)
        self.status_code = int(status_code)
        self.code = str(code)


# --------------------------------------------------------------------------- #
# 小工具（缺字段、空串、脏值都不抛异常）
# --------------------------------------------------------------------------- #
def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        loaded = json.loads(value)
    except (TypeError, ValueError):
        return default
    return loaded if isinstance(loaded, (list, dict)) else default


def _strip_forbidden(node: Any) -> Any:
    """递归剔除成本段里的售价 / 毛利字段（Spec §2.2）。"""
    if isinstance(node, dict):
        return {key: _strip_forbidden(value) for key, value in node.items()
                if key not in _FORBIDDEN_COST_KEYS}
    if isinstance(node, list):
        return [_strip_forbidden(item) for item in node]
    return node


def _resolve_requirement_no(project_id: str, requirement_no: str) -> str:
    explicit = _text(requirement_no)
    if explicit:
        return explicit
    doc = store.load_requirement(project_id) or {}
    return _text(doc.get("requirement_no"))


def _requirement_doc(project_id: str) -> dict:
    doc = store.load_requirement(project_id)
    if not doc or not isinstance(doc, dict):
        raise HandoffError(_REQUIREMENT_MISSING, 404, "requirement_not_found")
    return doc


def _industry_of(doc: dict) -> str:
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    return _text(data.get("industry")) or _text(doc.get("industry"))


def result_version_of(cost: dict) -> str:
    """成本结果版本：同一份成本读回必须给出同一个版本串（报价侧据此判重）。"""
    quantity = cost.get("quote_quantity")
    quantity_text = "" if quantity in (None, "") else str(quantity)
    total = _num(cost.get("total_cost")) or 0.0
    return "pkgcost-v1:%s:%.6f" % (quantity_text, total)


# --------------------------------------------------------------------------- #
# 组装交接包
# --------------------------------------------------------------------------- #
def handoff_package(project_id: str, requirement_no: str = "", *,
                    scenario: Optional[str] = None) -> dict:
    """组装交接包（不落库、不发送）；缺需求单 / 非包装 / 无成本时抛 HandoffError。"""
    pid = _text(project_id)
    doc = _requirement_doc(pid)
    if _industry_of(doc) != INDUSTRY:
        raise HandoffError("这不是包装需求单，包装交接只接管 industry=packaging 的项目",
                           400, "not_packaging")
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    req_no = _resolve_requirement_no(pid, requirement_no)
    scenario_code = _text(scenario) or _SCENARIO_DEFAULT

    cost = packaging_cost.load_cost(pid, req_no, scenario=(scenario or None))
    if not cost.get("built"):
        raise HandoffError("成本尚未测算，无法回传报价", 409, "cost_not_built")

    box = packaging_match.load_box_match(pid, req_no)
    bom = packaging_bom.load_bom(pid, req_no)
    route = packaging_route.load_route(pid, req_no)
    gaps = [copy.deepcopy(item) for item in (cost.get("gaps") or [])]
    formulas = _formulas_of(cost)
    version = result_version_of(cost)
    link = store.load_business_case(pid) or {}

    params = {key: data.get(key) for key in _PARAM_KEYS if data.get(key) not in (None, "")}
    source = {
        "project_id": pid,
        "requirement_no": req_no,
        "scenario_code": scenario_code,
        "result_version": version,
        "source_task_id": _text(link.get("source_task_id") or data.get("source_task_id")),
        "source_session_id": _text(link.get("quote_session_id")
                                   or data.get("source_session_id")),
        "business_case_id": _text(link.get("business_case_id")),
        # 预览时还没有交接记录，回传成功后由记录回填 —— 绝不现编。
        "handoff_id": "",
    }
    package = {
        "engine_version": ENGINE_VERSION,
        "handoff_version": HANDOFF_VERSION,
        "handoff_kind": HANDOFF_KIND,
        "industry": INDUSTRY,
        "industry_label": INDUSTRY_LABEL,
        "cost_profile": COST_PROFILE,
        "pricing_profile": PRICING_PROFILE,
        "result_version": version,
        "requirement": copy.deepcopy(data),
        "box_type": {
            "confirmed_box_type": _text(box.get("confirmed_box_type")),
            "decision": _text(box.get("decision")),
            "requirement_no": req_no,
            "engine_version": _text(box.get("engine_version")),
            "confirmed_by": _text(box.get("confirmed_by")),
            "confirmed_at": _text(box.get("confirmed_at")),
            "stale": bool(box.get("stale")),
        },
        "params": copy.deepcopy(params),
        "bom": _strip_forbidden(copy.deepcopy(bom)),
        "route": _strip_forbidden(copy.deepcopy(route)),
        "cost": _strip_forbidden(copy.deepcopy(cost)),
        "gaps": gaps,
        "formulas": formulas,
        "source": source,
    }
    publish_gate = _publish_gate(pid, package, cost)
    package.update(publish_gate)
    return package


def _unavailable_policy(reason: str) -> dict:
    """口径读不到时的兜底块（Spec `packaging-cost-and-handoff-static-downgrade-disclosure.md` §2.5）。

    与正常返回值**同形状**（六键齐全），结论仍是"未裁决" —— "读不到"绝不等于"已裁决"，
    也绝不给形状都不同的 `{}`（那个 `{}` 会随 package_json 落库并回传报价侧）。
    """
    return {"status": "pending", "chosen": "", "policy": "unresolved",
            "fallback": "sheet_labor_rate", "decided_by": "", "decided_at": "",
            "source": "unavailable", "unavailable_reason": _text(reason)}


def _publish_gate(pid: str, package: dict, cost: dict) -> dict:
    """正式报价闸门 + 版本六元组（DWG 第 5 批 Spec §5.4/§6.1，只追加键）。

    只在交接包里**读出结论**，不硬拦 `send_to_quote`（那一步的语义是"进入定价"，
    是草稿）；正式报价单的闸门由报价侧按 `publishable` 判。

    两个证据源**分开取**（Spec `packaging-cost-and-handoff-static-downgrade-disclosure.md` §2.4）：
    `gates()` 失败不许让 `inheritance()` 也不再执行（反之亦然），各自留痕；
    `publishable` 的结论口径逐字不变（读不到仍是 `False`）。
    """
    gates_brief: dict = {}
    source_versions: dict = {}
    gates_source, gates_unavailable = "flow", {}
    source_versions_source, source_versions_unavailable = "flow", {}
    flow = None
    try:
        from tech_app.backend.services import packaging_drawing_flow as _flow
        flow = _flow
    except Exception as exc:                            # noqa: BLE001 - 读不到要披露，不许炸
        reason = type(exc).__name__
        gates_source = source_versions_source = "unavailable"
        gates_unavailable = {"code": "packaging_flow_gates_unavailable", "reason": reason}
        source_versions_unavailable = {"code": "packaging_flow_versions_unavailable",
                                       "reason": reason}
    if flow is not None:
        try:
            stages = (flow.gates(pid).get("stages") or {})
            for stage in ("quote_draft", "quote_publish"):
                row = stages.get(stage) or {}
                gates_brief[stage] = {"status": _text(row.get("status")),
                                      "blocking": list(row.get("blocking") or [])}
        except Exception as exc:                        # noqa: BLE001 - 读不到要披露，不许炸
            gates_brief = {}
            gates_source = "unavailable"
            gates_unavailable = {"code": "packaging_flow_gates_unavailable",
                                 "reason": type(exc).__name__}
        try:
            versions = flow.inheritance(pid).get("source_versions") or {}
            source_versions = versions if isinstance(versions, dict) else {}
        except Exception as exc:                        # noqa: BLE001 - 读不到要披露，不许炸
            source_versions = {}
            source_versions_source = "unavailable"
            source_versions_unavailable = {"code": "packaging_flow_versions_unavailable",
                                           "reason": type(exc).__name__}
    try:
        policy = packaging_cost.minimum_charge_policy()
    except Exception as exc:                            # noqa: BLE001 - 读不到要披露，不许炸
        policy = _unavailable_policy(type(exc).__name__)
    return {"publishable": _text((gates_brief.get("quote_publish") or {}).get("status")) == "open",
            "gates": gates_brief,
            "gates_source": gates_source,
            "gates_unavailable": gates_unavailable,
            "minimum_charge_policy": policy if isinstance(policy, dict) else {},
            "source_versions": source_versions if isinstance(source_versions, dict) else {},
            "source_versions_source": source_versions_source,
            "source_versions_unavailable": source_versions_unavailable}


def _formulas_of(cost: dict) -> List[dict]:
    """公式依据逐条来自第 7 批成本明细行（不是公式目录：只交代真正算过的那些）。"""
    out: List[dict] = []
    for item in (cost.get("items") or []):
        if not isinstance(item, dict):
            continue
        code = _text(item.get("formula_code"))
        expression = _text(item.get("expression"))
        if not code and not expression:
            continue
        inputs = _loads(item.get("inputs_json"), {})
        result = item.get("amount_with_loss")
        if result is None:
            result = item.get("amount")
        out.append({
            "formula_code": code,
            "formula_version": _text(item.get("formula_version")),
            "expression": expression,
            "inputs": inputs if isinstance(inputs, dict) else {},
            "result": result,
            "source": _text(item.get("source")),
            "source_ref": _text(item.get("source_ref")),
            "cost_category": _text(item.get("cost_category")),
            "part_code": _text(item.get("part_code")),
        })
    return out


def package_fingerprint(package: dict) -> str:
    """对 10 组内容做的稳定摘要（键排序）：同输入同摘要、改一个数量即变。"""
    payload = {key: (package or {}).get(key) for key in PACKAGE_SECTIONS}
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def bridge_result(package: dict) -> dict:
    """交给 ``cpq_bridge.send_to_quote(result=...)`` 的正文：带行业 + 原样整包。"""
    result = dict(package or {})
    result["industry"] = _text(result.get("industry")) or INDUSTRY
    result["packaging_package"] = package
    return result


# --------------------------------------------------------------------------- #
# 回传留痕（项目审计）
# --------------------------------------------------------------------------- #
#: 回传动作固定审计名（Spec `packaging-handoff-audit-trail.md` §2.1）。
AUDIT_SENT_ACTION = "workflow:packaging_handoff_sent"

#: 留痕没落下时的稳定披露码（Spec `packaging-handoff-audit-availability.md` §C2）：
#: 它只描述"审计写不进去"，**不是**回传失败的码 —— 留痕失败照样回传成功。
AUDIT_UNAVAILABLE_CODE = "PACKAGING_HANDOFF_AUDIT_UNAVAILABLE"


def _audit_handoff_sent(project_id: str, *, requirement_no: str, scenario_code: str,
                        handoff_no: str, version_no: int, already_sent: bool,
                        cost_result_version: str, has_gaps: bool, quote_session_id: str,
                        by: str) -> Dict[str, Any]:
    """给"把哪一版推给了报价侧"留一条项目审计（Spec §2.1）。

    载荷只放回传事实（九键），绝不带售价 / 毛利字段（`_FORBIDDEN_COST_KEYS`），也不
    灌 `user` / `token` / 整份交接包；写审计失败**不得**改变回传结果、不得回滚已落库的
    交接记录 —— 留痕是留痕，闸门是闸门。

    返回值是**留痕可用性**的稳定披露体（Spec `packaging-handoff-audit-availability.md`
    §C1，键集固定五个）：`{"attempted", "ok", "action", "code", "message"}`。
    写成功 `ok=True` / 空码空消息；写失败 `ok=False` / `AUDIT_UNAVAILABLE_CODE` /
    message 含异常类名与原文本 —— 但**不抛**（留痕不是闸门）。
    """
    payload = {
        "requirement_no": _text(requirement_no),
        "scenario_code": _text(scenario_code),
        "handoff_no": _text(handoff_no),
        "version_no": int(version_no or 0),
        "already_sent": bool(already_sent),
        "cost_result_version": _text(cost_result_version),
        "has_gaps": bool(has_gaps),
        "quote_session_id": _text(quote_session_id),
        "by": _text(by),
    }
    for key in _FORBIDDEN_COST_KEYS:
        payload.pop(key, None)
    disclosure: Dict[str, Any] = {"attempted": True, "ok": True,
                                  "action": AUDIT_SENT_ACTION, "code": "", "message": ""}
    try:
        store.audit(project_id, AUDIT_SENT_ACTION, payload)
    except Exception as exc:  # noqa: BLE001 — 留痕失败不许把回传判成失败
        detail = _text(exc)
        disclosure["ok"] = False
        disclosure["code"] = AUDIT_UNAVAILABLE_CODE
        disclosure["message"] = ("%s: %s" % (type(exc).__name__, detail)) if detail \
            else type(exc).__name__
    return disclosure


# --------------------------------------------------------------------------- #
# 缺口与放行
# --------------------------------------------------------------------------- #
def _gap_codes(gaps: List[dict]) -> List[str]:
    codes = []
    for item in gaps or []:
        code = _text((item or {}).get("code")) if isinstance(item, dict) else _text(item)
        if code and code not in codes:
            codes.append(code)
    return codes


def _has_gaps(package: dict) -> bool:
    """缺口判定以成本引擎的 ``has_gaps`` 为准；包里的逐条缺口也算（历史兼容）。"""
    cost = package.get("cost") if isinstance(package.get("cost"), dict) else {}
    return bool(cost.get("has_gaps")) or bool(package.get("gaps"))


def _gap_codes_of(package: dict) -> List[str]:
    """缺口码：优先取成本段的逐条缺口；成本段没逐条列（只抬了 has_gaps）时退回需求单
    里记的缺口清单 —— 拒绝时必须点名缺什么，不许只说"有缺口"。"""
    cost = package.get("cost") if isinstance(package.get("cost"), dict) else {}
    requirement = package.get("requirement") if isinstance(package.get("requirement"), dict) else {}
    for source in (cost.get("gaps"), requirement.get("gaps"), package.get("gaps")):
        codes = _gap_codes(source or [])
        if codes:
            return codes
    return []


def _guard_gaps(package: dict, *, allow_gaps: bool, reason: str,
                user: Optional[dict] = None) -> Optional[dict]:
    """有缺口时的两道门：要么拒绝，要么留痕放行。返回放行留痕（无缺口给 None）。"""
    if not _has_gaps(package):
        return None
    codes = _gap_codes_of(package)
    named = "、".join(codes) or "未标明缺口码"
    if not allow_gaps:
        raise HandoffError(f"成本仍有缺口（{named}），不能生成正式报价；"
                           "请先补齐，或写明原因走放行留痕", 409, "cost_gaps_unresolved")
    if not _text(reason):
        raise HandoffError(f"放行必须写明原因（缺口：{named}）", 409, "gap_reason_required")
    return {"by": _text((user or {}).get("username")),
            "at": da_db.now(), "reason": _text(reason), "codes": codes}


def send_to_quote(project_id: str, requirement_no: str = "", *, scenario: Optional[str] = None,
                  allow_gaps: bool = False, reason: str = "", user: Optional[dict] = None,
                  token: str = "", title: str = "", customer: str = "",
                  business_case_id: str = "", create_new: bool = False,
                  create_reason: str = "") -> dict:
    """落库 + 回传报价。返回 ``handoff_no`` / ``version_no`` / ``already_sent`` /
    ``quote_session_id`` / ``handoff``（目标任务与落点）。

    任何一个拒绝（越权 / 非包装 / 无成本 / 缺口未清）都发生在落库之前 —— 被拒绝的
    回传不留交接记录、不建任务。同包重发命中唯一约束：复用已有行，不再发一次。

    ``business_case_id`` / ``create_new`` / ``create_reason`` 是**落点恢复三件套**
    （Spec `packaging-quote-send-recovery.md` §2.1/§2.2）：桥早就支持，包装这条链以前
    一个都没传 —— 于是服务端回「请填写新建原因后重试」时，界面无处可填。
    """

    user = user or {}
    role = _text(user.get("role_code") or user.get("role"))
    if role not in HANDOFF_WRITE_ROLES:
        raise HandoffError("只有财务经理、工艺经理、工艺技术总监或管理员能回传包装报价，"
                           f"当前是「{_text(user.get('role_name')) or role or '未登录'}」",
                           403, "role_not_allowed")
    package = handoff_package(project_id, requirement_no, scenario=scenario)
    waiver = _guard_gaps(package, allow_gaps=allow_gaps, reason=reason, user=user)

    pid = _text(project_id)
    # 复用项目 meta 里**已有的**恢复留痕（`/quote-link/recover` 写过的那次「明确新建」）：
    # 人已经答过一次的问题不许再问一遍；本次请求里写明的值优先（Spec §2.2）。
    restored = store.load_business_case(pid) or {}
    effective_new = bool(create_new) or bool(restored.get("create_new"))
    effective_reason = _text(create_reason) or _text(restored.get("create_reason"))
    req_no = _text(package["source"].get("requirement_no"))
    scenario_code = _text(package["source"].get("scenario_code")) or _SCENARIO_DEFAULT
    fingerprint = package_fingerprint(package)

    rows = da_repo.packaging_handoffs(pid, req_no)
    for row in rows:
        if _text(row.get("package_fingerprint")) == fingerprint:
            outcome = _reuse_outcome(row, package)
            # 同包重发也是动作，留痕必须写，且写的是**被复用的那一行**（Spec §2.1）。
            audit = _audit_handoff_sent(
                pid, requirement_no=_text(row.get("requirement_no")) or req_no,
                scenario_code=_text(row.get("scenario_code")) or scenario_code,
                handoff_no=outcome.get("handoff_no"), version_no=outcome.get("version_no"),
                already_sent=True,
                cost_result_version=_text(row.get("cost_result_version")),
                has_gaps=bool(row.get("has_gaps")),
                quote_session_id=outcome.get("quote_session_id"),
                by=_text(user.get("username")))
            # 留痕的可用性跟着结果一起回（Spec `packaging-handoff-audit-availability.md` §C3）。
            outcome["audit"] = audit
            return outcome
    version_no = max([int(row.get("version_no") or 0) for row in rows] or [0]) + 1

    source = package["source"]
    gaps = list(package.get("gaps") or [])
    has_gaps = _has_gaps(package)
    # 放行留痕必须跟着包一起过桥（Spec 批 12 §2.1）：技术侧留了痕、报价侧那道门才认得出
    # "财务写明原因放行"这条官方路径；没有缺口时不放这个键，正文与今天逐字一致。
    body = bridge_result(package)
    if waiver:
        body = {**body, "gap_waiver": waiver}
    result = cpq_bridge.send_to_quote(
        token, pid, title or f"包装报价 · {req_no}", customer, "",
        "包装成本已确认，请进入定价", _text(source.get("source_task_id")),
        body, _text(source.get("source_session_id")),
        _text(source.get("result_version")),
        business_case_id=(_text(business_case_id) or _text(source.get("business_case_id"))),
        create_new=effective_new, create_reason=effective_reason,
        handoff_kind=HANDOFF_KIND)
    result = result if isinstance(result, dict) else {}

    handoff_no = "pkghandoff:%s:%s:%s:%d" % (pid, req_no, scenario_code, version_no)
    handoff_id = _text(result.get("handoff_id"))
    handoff = result.get("handoff") if isinstance(result.get("handoff"), dict) else {}
    now = da_db.now()
    record = {
        "handoff_no": handoff_no,
        "project_id": pid,
        "requirement_no": req_no,
        "scenario_code": scenario_code,
        "version_no": version_no,
        "industry": INDUSTRY,
        "engine_version": ENGINE_VERSION,
        "handoff_version": HANDOFF_VERSION,
        "handoff_kind": HANDOFF_KIND,
        "cost_profile": COST_PROFILE,
        "pricing_profile": PRICING_PROFILE,
        "cost_result_version": _text(source.get("result_version")),
        "package_fingerprint": fingerprint,
        "package_json": package,
        "has_gaps": has_gaps,
        "gap_codes_json": _gap_codes_of(package),
        "gap_waiver_json": waiver,
        "target_quote_session_id": _text(result.get("quote_session_id")),
        "target_task_id": _text(handoff.get("task_id")),
        "target_business_case_id": _text(result.get("business_case_id")
                                         or source.get("business_case_id")),
        "sent_by": _text(user.get("username")),
        "sent_at": now,
        "created_at": now,
    }
    da_repo.save_packaging_handoff(record)
    audit = _audit_handoff_sent(pid, requirement_no=req_no, scenario_code=scenario_code,
                                handoff_no=handoff_no, version_no=version_no,
                                already_sent=False,
                                cost_result_version=_text(source.get("result_version")),
                                has_gaps=has_gaps,
                                quote_session_id=_text(result.get("quote_session_id")),
                                by=_text(user.get("username")))
    return {
        "handoff_no": handoff_no,
        "handoff_id": handoff_id,
        "version_no": version_no,
        "already_sent": False,
        "industry": INDUSTRY,
        "handoff_kind": HANDOFF_KIND,
        "quote_session_id": _text(result.get("quote_session_id")),
        "business_case_id": _text(result.get("business_case_id")
                                  or source.get("business_case_id")),
        "package_fingerprint": fingerprint,
        "package": package,
        "handoff": dict(handoff),
        "bridge": result,
        # 留痕的可用性跟着结果一起回（Spec `packaging-handoff-audit-availability.md` §C3）。
        "audit": audit,
    }


def _reuse_outcome(row: dict, package: dict) -> dict:
    """同包重发：复用已有交接行，零副作用（不再建报价任务）。"""
    task_id = _text(row.get("target_task_id"))
    return {
        "handoff_no": _text(row.get("handoff_no")),
        "handoff_id": "",
        "version_no": int(row.get("version_no") or 1),
        "already_sent": True,
        "industry": INDUSTRY,
        "handoff_kind": HANDOFF_KIND,
        "quote_session_id": _text(row.get("target_quote_session_id")),
        "business_case_id": _text(row.get("target_business_case_id")),
        "package_fingerprint": _text(row.get("package_fingerprint")),
        "package": package,
        "handoff": {"task_id": task_id} if task_id else {},
        "bridge": {},
    }


def handoff_stale_reasons(record: dict, cost: dict) -> list:
    """回传记录的输入漂移（Spec `packaging-handoff-input-drift-disclosure.md` §2.1）。

    口径唯一（读接口与列表接口共用这一处），固定顺序 `cost_recomputed` → `provenance_missing`：

    - `cost_recomputed`：记录里的 `cost_result_version` 非空，且当前成本的
      `result_version_of()` 非空、与之不同；
    - `provenance_missing`：记录非空但 `cost_result_version` 为空（本批之前发的）；
    - 当前成本给 `{}` / `built=false`（读不到或没算过）时只允许 `provenance_missing` ——
      **"比较不了" ≠ "变了"**，此时绝不报 `cost_recomputed`。
    """
    if not isinstance(record, dict) or not record:
        return []
    stored = _text(record.get("cost_result_version"))
    if not stored:
        return ["provenance_missing"]
    built = isinstance(cost, dict) and bool(cost.get("built"))
    if not built:
        return []
    current = result_version_of(cost)
    return ["cost_recomputed"] if current and current != stored else []


def _source_versions_of(record: dict) -> dict:
    """这一版回传**按哪一版输入发的**（Spec §2.1）：一律来自记录，绝不用当前值兜。"""
    return {"cost_result_version": _text(record.get("cost_result_version")),
            "handoff_version": _text(record.get("handoff_version")),
            "package_fingerprint": _text(record.get("package_fingerprint"))}


def _stored_cost(project_id: str, requirement_no: str, scenario: str) -> tuple:
    """当前成本：`(成本, 不可用标记)`。**只读**，绝不触发重算（Spec §2.1）。"""
    try:
        cost = packaging_cost.load_cost(project_id, requirement_no, scenario=scenario or None)
    except Exception as exc:                            # noqa: BLE001 - 读不到要披露，不许炸
        return {}, {"code": "cost_unavailable", "reason": type(exc).__name__}
    cost = cost if isinstance(cost, dict) else {}
    if not cost.get("built"):
        return {}, {"code": "cost_unavailable", "reason": ""}
    return cost, {}


def _with_handoff_drift(record: dict, project_id: str, requirement_no: str, *,
                        cost_cache: Optional[dict] = None) -> dict:
    """给一条交接记录挂上漂移结论（Spec §2.1）：四个键都必须存在。"""
    item = dict(record)
    scenario = _text(item.get("scenario_code"))
    cache_key = (requirement_no, scenario)
    if isinstance(cost_cache, dict) and cache_key in cost_cache:
        cost, unavailable = cost_cache[cache_key]
    else:
        cost, unavailable = _stored_cost(project_id, requirement_no, scenario)
        if isinstance(cost_cache, dict):
            cost_cache[cache_key] = (cost, unavailable)
    reasons = handoff_stale_reasons(item, cost)
    item["stale"] = bool(reasons)
    item["stale_reasons"] = reasons
    item["source_versions"] = _source_versions_of(item)
    item["cost_unavailable"] = unavailable
    return item


def load_handoff(project_id: str, requirement_no: str = "") -> dict:
    """最近一次交接记录（没有给 ``{}``，不报错）。

    新增四个键（Spec `packaging-handoff-input-drift-disclosure.md` §2.1）：`stale` /
    `stale_reasons` / `source_versions` / `cost_unavailable` —— 读侧把"这一版是按哪一版成本发的"
    与"现在成本变了没有"说出来（以前只把库里的行原样吐回去）。
    """
    req_no = _resolve_requirement_no(project_id, requirement_no)
    row = da_repo.load_packaging_handoff(_text(project_id), req_no)
    if not row:
        return {}                                       # "还没发过"不是"过期"，逐字给 {}
    return _with_handoff_drift(dict(row), _text(project_id), req_no)


def handoff_versions(project_id: str, requirement_no: str = "") -> list:
    """全部交接版本（新的在前，只增不改）；每条带与 `load_handoff()` 同一口径的四个新键。

    当前成本**只读一次**（同一 (需求单, 场景) 复用一份），绝不每行读一次库。
    """
    req_no = _resolve_requirement_no(project_id, requirement_no)
    rows = da_repo.packaging_handoffs(_text(project_id), req_no)
    ordered = sorted([dict(row) for row in rows],
                     key=lambda item: int(item.get("version_no") or 0), reverse=True)
    cache: dict = {}
    return [_with_handoff_drift(row, _text(project_id),
                                _text(row.get("requirement_no")) or req_no,
                                cost_cache=cache)
            for row in ordered]

"""门禁矩阵（第 5 批 Spec §5）：哪些字段人工确认后才能做哪一段下游。"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from tech_app.backend.storage import store

from . import anchor as anchor_mod
from . import model, persistence

Resolver = Callable[[str], Optional[Any]]

#: 每段的必需字段（有序，与 Spec §5.1 逐格一致）。
GATE_REQUIRES: Dict[str, Tuple[str, ...]] = {
    "box_match": ("inner_length", "inner_width", "inner_height", "closure_type"),
    "bom": ("inner_length", "inner_width", "inner_height", "face_paper_gsm"),
    "route": ("closure_type", "v_groove", "face_paper_gsm"),
    "cost": ("quote_quantity",),
    "quote_draft": (),
    "quote_publish": ("inner_length", "inner_width", "inner_height", "closure_type"),
}

BLOCKING_CODES = ("field_missing", "field_unconfirmed", "field_conflict", "unit_unconfirmed",
                  "box_match_not_confirmed", "bom_not_built", "route_not_confirmed",
                  "cost_not_built", "cost_gaps_unresolved", "minimum_charge_policy_unresolved")

_FIELD_CODES = ("field_missing", "field_unconfirmed", "field_conflict")


def _requirement(project_id: str) -> Dict[str, Any]:
    doc = store.load_requirement(project_id) or {}
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    return {"data": data or {},
            "sources": (data or {}).get("field_sources") or {},
            "provenance": (data or {}).get("field_provenance") or {}}


def _has_value(data: Dict[str, Any], field: str) -> bool:
    return data.get(field) not in (None, "", [], {})


def _is_confirmed(data: Dict[str, Any], provenance: Dict[str, Any], sources: Dict[str, Any],
                  field: str) -> bool:
    """这个字段算不算"已确认"（Spec `packaging-manual-field-confirmation.md` §2.1）。

    两条**互相独立**的证据路径，**任一**成立即可 —— 原来写成"与"，于是两条路各自都走不通：
    人工填的字段卡在 `status`（几何看板给的是 missing），图纸已确认的字段卡在来源/来源口径。

      ① 图纸 / 模型证据已确认：`status == "confirmed"`；
      ② 人工录入 / 人工确认**且当前有值**：`data[field]` 有值 且
         （`field_sources == "manual"` 或 `origin == "user_confirmed"`）。

    拒绝口径一个字没放宽：值为空先走 `field_missing`（值判定在 `_field_blocking` 里排在前面）、
    冲突证据先走 `field_conflict`（同样排在前面）。
    """
    row = provenance.get(field) if isinstance(provenance.get(field), dict) else {}
    status = str(row.get("status") or "")
    origin = str(row.get("origin") or "")
    source = str(sources.get(field) or "")
    if status == "confirmed":                                   # ① 图纸/模型证据
        return True
    return _has_value(data, field) and (source == "manual" or origin == "user_confirmed")  # ② 人工


def _field_blocking(requires: Tuple[str, ...], requirement: Dict[str, Any],
                    boards: Dict[str, Any]) -> List[Dict[str, Any]]:
    data, provenance, sources = requirement["data"], requirement["provenance"], requirement["sources"]
    rows: List[Dict[str, Any]] = []
    for field in requires:
        board = str((boards.get(field) or {}).get("board") or "")
        row = provenance.get(field) if isinstance(provenance.get(field), dict) else {}
        if board == "conflict" or str(row.get("status") or "") == "conflict":
            rows.append({"code": "field_conflict", "field": field,
                         "message": "%s存在冲突证据，需要人工裁定后才能继续" % model.label_of(field),
                         "source": "field_provenance"})
            continue
        if not _has_value(data, field):
            rows.append({"code": "field_missing", "field": field,
                         "message": "缺少%s，请先补全后再进行该步骤" % model.label_of(field),
                         "source": "requirement"})
            continue
        if not _is_confirmed(data, provenance, sources, field):
            rows.append({"code": "field_unconfirmed", "field": field,
                         "message": "%s尚未确认，确认后才能进行该步骤" % model.label_of(field),
                         "source": "field_provenance"})
    return rows


#: 读一个上游结果的**三态**（Spec `packaging-gate-read-failure-disclosure.md` §2.1）：
#: `engine`（模块 / 函数都在且这一次没抛，**空 dict 也算读到** —— 那是"上游说它没做"）/
#: `absent`（这个部署没有这一段）/ `unavailable`（调用抛异常，`reason` 给异常类名）。
READ_SOURCES = ("engine", "absent", "unavailable")

#: 严重度（越大越"该说"）：同一段依赖被读多次时，最坏的那一态胜出 —— 否则
#: `load_cost` 读挂了、随后的 `minimum_charge_policy` 恰好读到，就会把读失败盖掉。
_READ_SEVERITY = {"engine": 0, "absent": 1, "unavailable": 2}


def _read(resolve: Resolver, name: str, function: str, *args: Any) -> Tuple[Dict[str, Any], str, str]:
    """读一个上游结果，返回 `(行, source, reason)`。**绝不把异常抛给调用方。**"""
    module = resolve(name) if callable(resolve) else None
    fn = getattr(module, function, None) if module is not None else None
    if not callable(fn):
        return {}, "absent", ""
    try:
        row = fn(*args)
    except Exception as exc:                              # noqa: BLE001 - 读不到要披露，不许抛给调用方
        return {}, "unavailable", type(exc).__name__
    return (dict(row) if isinstance(row, dict) else {}), "engine", ""


def _engine(resolve: Resolver, name: str, function: str, *args: Any) -> Dict[str, Any]:
    """既有调用点的取数口径：拿不到就给 `{}`（判据与结论逐字不变）。"""
    return _read(resolve, name, function, *args)[0]


def _policy(resolve: Resolver) -> Dict[str, Any]:
    """最低收费口径（读不到就给 `{}`，判据不变）；三态请用 `_read()`。"""
    return _read(resolve, "packaging_cost", "minimum_charge_policy")[0]


def _gap_codes(cost: Dict[str, Any], handoff: Dict[str, Any]) -> List[str]:
    """当前缺口码：成本记录里的那份 + 交接记录里记下的那份（两边都读得到才校验覆盖）。

    只用来回答"这条放行留痕覆盖的是不是此刻的缺口"；读不到就当作**无从校验**，
    交给 `_gap_waiver()` 的其余四条判据（宁可不披露，也不假装放行过）。
    """
    codes: List[str] = []
    for source in (cost.get("gaps"), handoff.get("gap_codes"), handoff.get("gap_codes_json")):
        for row in source if isinstance(source, list) else []:
            code = (row.get("code") if isinstance(row, dict) else row) or ""
            code = str(code).strip()
            if code and code not in codes:
                codes.append(code)
    return codes


def _gap_waiver(handoff: Dict[str, Any], gap_codes: List[str]) -> Optional[Dict[str, Any]]:
    """合法放行留痕 → 摘要对象；否则 `None`（Spec `packaging-parse-to-downstream-seams.md` §2.3）。

    四条都要成立：

      ① 交接记录自证这次交接**带着缺口**（`has_gaps`）—— 没缺口的记录里出现放行留痕本身就不成立；
      ② `by` / `at` / `reason` 都非空（谁、什么时候、为什么）；
      ③ `codes` 非空且每一项都是有效码；
      ④ 读得到的当前缺口码必须被 `codes` **全覆盖**（漏一个就不算这条留痕放行了它）。

    这是**披露**不是放宽：调用方仍然把 `cost_gaps_unresolved` 留在 `blocking` 里。
    """
    if not handoff.get("has_gaps"):
        return None
    raw = handoff.get("gap_waiver_json")
    if isinstance(raw, str):
        try:
            waiver: Any = json.loads(raw) if raw.strip() else None
        except Exception:           # noqa: BLE001 - 留痕损坏按"没有合法留痕"处理
            waiver = None
    else:
        waiver = raw
    if not isinstance(waiver, dict):
        return None
    by = str(waiver.get("by") or "").strip()
    at = str(waiver.get("at") or "").strip()
    reason = str(waiver.get("reason") or "").strip()
    if not (by and at and reason):
        return None
    codes = [str(code).strip() for code in (waiver.get("codes") or [])
             if str(code or "").strip()]
    if not codes:
        return None
    if gap_codes and not set(gap_codes) <= set(codes):
        return None
    return {"by": by, "at": at, "reason": reason, "codes": codes}


def _stage_entry(stage: str, project_id: str, resolve: Resolver,
                 snapshot: str) -> Dict[str, Any]:
    requirement = _requirement(project_id)
    flow = persistence.load_flow(project_id) or {}
    boards = flow.get("fields") if isinstance(flow.get("fields"), dict) else {}
    requires = GATE_REQUIRES[stage]
    blocking: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    waived: Optional[Dict[str, Any]] = None
    # 这一段**实际读过**的依赖与读取三态（Spec `packaging-gate-read-failure-disclosure.md` §2.1）。
    # 判据与结论一个字不改，只是把"我到底读到没有"一并交出去 —— 否则"读不到"与"确实没做"
    # 在返回体与用户文案上逐字相同。
    reads: Dict[str, Dict[str, Any]] = {}
    read_order: List[str] = []

    def read(name: str, function: str, *args: Any) -> Dict[str, Any]:
        row, source, reason = _read(resolve, name, function, *args)
        prior = reads.get(name)
        if prior is None:
            read_order.append(name)
        if prior is None or _READ_SEVERITY[source] > _READ_SEVERITY[prior["source"]]:
            reads[name] = {"source": source, "reason": reason}
        return row

    def _finish(entry: Dict[str, Any]) -> Dict[str, Any]:
        unavailable = [name for name in read_order if reads[name]["source"] != "engine"]
        entry["reads"] = reads
        entry["reads_unavailable"] = ({"code": "gate_read_unavailable",
                                       "dependencies": unavailable} if unavailable else {})
        return entry

    if stage != "quote_draft":
        blocking.extend(_field_blocking(requires, requirement, boards))
    unit_status = str(anchor_mod.current_anchor(project_id).get("unit_status") or "")
    if stage == "box_match" and unit_status and unit_status != "confirmed":
        blocking.append({"code": "unit_unconfirmed", "field": "inner_width",
                         "message": "图纸未声明单位，长宽高不能按毫米确认"})
    if stage in ("bom", "quote_publish"):
        box = read("packaging_match", "load_box_match", project_id)
        if str(box.get("decision") or "") != "confirmed":
            blocking.append({"code": "box_match_not_confirmed",
                             "message": "盒型尚未确认，确认后才能进行该步骤",
                             "source": "packaging_match"})
    if stage == "route":
        bom = read("packaging_bom", "load_bom", project_id)
        if not bom.get("built"):
            blocking.append({"code": "bom_not_built", "message": "BOM 尚未生成，生成后才能排工艺路线",
                             "source": "packaging_bom"})
    if stage == "cost":
        route = read("packaging_route", "load_route", project_id)
        if str(route.get("status") or "") != "confirmed":
            blocking.append({"code": "route_not_confirmed",
                             "message": "工艺路线尚未确认，确认后才能测算成本",
                             "source": "packaging_route"})
    if stage == "quote_publish":
        cost = read("packaging_cost", "load_cost", project_id)
        if not cost.get("built"):
            blocking.append({"code": "cost_not_built", "message": "成本尚未测算，测算后才能生成正式报价",
                             "source": "packaging_cost"})
        elif cost.get("has_gaps"):
            # 「缺口未清」这条结论一个字不改（Spec §2.3 C4）；只是若财务/工艺已经按留痕放行过，
            # 就把那份留痕一并披露出来 —— 否则界面上只有一句"缺口清零后才能生成正式报价"，
            # 看不出这条缺口其实已经被人按什么原因放行了（34 实测两边说法相反）。
            handoff = read("packaging_handoff", "load_handoff", project_id)
            waiver = _gap_waiver(handoff, _gap_codes(cost, handoff))
            row: Dict[str, Any] = {"code": "cost_gaps_unresolved",
                                   "message": "成本仍存在缺口，缺口清零后才能生成正式报价",
                                   "source": "packaging_cost"}
            if waiver:
                row["waived"] = True
                row["waiver"] = waiver
                waived = waiver
            blocking.append(row)
        policy = read("packaging_cost", "minimum_charge_policy")
        if str(policy.get("status") or "") != "chosen":
            blocking.append({"code": "minimum_charge_policy_unresolved",
                             "message": "最低收费口径尚未裁决，暂不能生成正式报价（可先存草稿）",
                             "source": "packaging_cost"})
    if stage == "quote_draft":
        cost = read("packaging_cost", "load_cost", project_id)
        if not cost.get("built"):
            warnings.append({"code": "cost_not_built", "message": "成本尚未测算，草稿里暂不体现成本"})
        elif cost.get("has_gaps"):
            warnings.append({"code": "cost_gaps_unresolved",
                             "message": "成本存在缺口，草稿会随包带出这些缺口"})
    entry = {"stage": stage, "status": "blocked" if blocking else "open",
             "requires": list(requires), "blocking": blocking, "warnings": warnings,
             "snapshot": {"requirement_snapshot_version": snapshot}}
    if waived:
        entry["waiver"] = waived
    return _finish(entry)


def build(project_id: str, *, resolve: Resolver = None, stage: str = "") -> Dict[str, Any]:
    if stage and stage not in model.GATE_STAGES:
        raise ValueError("未知的门禁段：%s" % stage)
    snapshot = anchor_mod.requirement_snapshot_version(project_id)
    stages = {name: _stage_entry(name, project_id, resolve, snapshot) for name in model.GATE_STAGES}
    if stage:
        return stages[stage]
    return {"project_id": project_id,
            "flow_version": model.FLOW_VERSION,
            "requirement_snapshot_version": snapshot,
            "stages": stages}


def blocking_message(entry: Dict[str, Any]) -> str:
    """门禁文案：只给一处结论，短且不换行（红测 G18/C15）。"""
    # 读不到上游结果时，那几句"尚未确认 / 尚未生成 / 尚未测算"是**假结论**：用户会去重新确认
    # 一遍（或去问销售为什么盒型没确认），而真相只是这一趟读不到（Spec
    # `packaging-gate-read-failure-disclosure.md` §2.1）。结论（blocked）不改，只说清为什么。
    unavailable = (entry or {}).get("reads_unavailable") or {}
    dependencies = [str(name) for name in (unavailable.get("dependencies") or []) if str(name)]
    if dependencies:
        return ("暂时读不到上游结果（%s），请稍后重试；这不代表这一步还没做"
                % "、".join(dependencies))
    blocking = (entry or {}).get("blocking") or []
    fields = [str(item.get("field") or "") for item in blocking
              if str(item.get("code") or "") in _FIELD_CODES and str(item.get("field") or "")]
    if fields:
        labels = "、".join(model.label_of(field) for field in fields[:4])
        return "需求字段尚未确认（%s），请先确认后再进行该步骤" % labels
    for item in blocking:
        message = str(item.get("message") or "")
        if message:
            return message
    return "当前条件尚不满足，请先补齐前置步骤"


def require(project_id: str, stage: str, *, resolve: Resolver = None) -> Dict[str, Any]:
    if stage not in model.GATE_STAGES:
        raise ValueError("未知的门禁段：%s" % stage)
    entry = build(project_id, resolve=resolve, stage=stage)
    if entry.get("status") == "blocked":
        http_status, retryable = model.error_meta("PACKAGING_GATE_BLOCKED")
        raise model.DrawingFlowError("PACKAGING_GATE_BLOCKED", http_status, retryable,
                                     blocking_message(entry))
    return entry

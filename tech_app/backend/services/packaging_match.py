"""盒型匹配与人工确认 —— 包装第 4 批。

Spec：docs/specs/packaging-box-type-matching.md
红测：tests/test_packaging_box_type_matching_red.py

分段职责：
  · 匹配引擎（`match_box_types`）是确定性纯函数：维度闭集、权重、硬门槛一律读
    `kb_packaging_match_weight`，不写死数字、不调模型、不联网、不落库；
  · 落库与四态决策（`run_box_match` / `load_box_match` / `decide_box_match`）负责
    把候选、分项分、淘汰原因与人工确认写进 `wip_packaging_box_match*`；
  · 人工确认优先于算法：重跑匹配只换候选与输入快照，`confirmed_*` 一律不碰。

本批不做部件与 BOM（第 5 批）、工艺路线（第 6 批）、成本与报价（第 7、8 批）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from . import industry_templates
from ..storage import da_db, da_repo, kb_repo, store


# --------------------------------------------------------------------------- #
# 命名契约（Spec §4.5：红测与实现共用，不得改名）
# --------------------------------------------------------------------------- #
ENGINE_VERSION = "packaging_match_v1"
PACKAGING_INDUSTRY = "packaging"

#: Spec §2.1 —— 匹配真正需要的 7 个键（顺序即契约）。
MATCH_INPUT_KEYS = ("inner_length", "inner_width", "inner_height", "closure_type",
                    "v_groove", "face_paper_gsm", "fit_clearance")

#: 配合间隙容差（mm）；口径来自权重表该行的 rule_expr。
FIT_CLEARANCE_TOLERANCE_MM = 0.5

#: 盒型决策的角色门禁（main.py 直接引用这一份，不得另抄）。
BOX_MATCH_DECIDE_ROLES = frozenset({"process_manager", "process_director", "admin"})

#: 状态排序：命中在前、缺输入居中、淘汰最后。
_STATUS_RANK = {"matched": 0, "needs_input": 1, "rejected": 2}

#: 闭合方式的分隔符闭集（Spec §2.3.4）。
_CLOSURE_SEPARATORS = ("/", "+", "、", ",", "，", ";", "；")

_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
#: 区间解析专用：不带符号，`157-250` 里的连字符是分隔符而非负号。
_SPAN_RE = re.compile(r"\d+(?:\.\d+)?")

_TRUE_WORDS = {"是", "有", "需要", "要", "y", "yes", "true", "1"}
_FALSE_WORDS = {"否", "无", "不需要", "不要", "n", "no", "false", "0"}
#: 取值词后允许跟的说明分隔符（空白 + 开括号/引号）。只放宽**解析**：整条必须是
#: 「取值词 + 一段说明」，`不是` / `否定的` 这类否定写法不在其中，仍判为无法识别。
_BOOL_SUFFIX_SEPARATORS = " \t\r\n(\uff08[\u3010{\uff5b\"'\u201c\u2018"

_DECISION_STATES = ("confirmed", "returned", "new_tooling")

#: 缺数据说明（Spec C2）：维度 → 人话。`data_gaps` 只做展示，不改变 `status` /
#: `can_confirm`（人工确认优先）。
_DATA_GAP_MESSAGES = {
    "fit_clearance": "该盒型未登记配合间隙，本维未参与打分，需补齐或人工确认",
    "size_range": "该盒型未登记尺寸区间，本维未参与打分，需补齐或人工确认",
    "face_paper_gsm": "该盒型未登记面纸克重，本维未参与打分，需补齐或人工确认",
    "closure_type": "该盒型未登记闭合方式，本维未参与打分，需补齐或人工确认",
    "v_groove": "该盒型未登记 V 槽，本维未参与打分，需补齐或人工确认",
}


class BoxMatchError(Exception):
    """盒型匹配的业务错误；`status_code` 供接口层原样映射成 HTTP 状态码。"""

    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = str(message)
        self.status_code = int(status_code)


# --------------------------------------------------------------------------- #
# 取值口径（全部容错：缺字段、空串、脏值都不抛异常）
# --------------------------------------------------------------------------- #
def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _num(value: Any) -> Optional[float]:
    """从任意取值里取第一个数字；取不到给 None（不抛异常）。"""
    if value is None or isinstance(value, bool):
        return None
    match = _NUMBER_RE.search(_text(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:                                   # pragma: no cover - 兜底
        return None


def _parse_range(value: Any) -> Optional[tuple[float, float]]:
    """把 `"157-250"` / `"200"` 解析成 (下界, 上界)；解析不出来给 None。"""
    numbers = _SPAN_RE.findall(_text(value))
    if not numbers:
        return None
    try:
        low = float(numbers[0])
        high = float(numbers[-1]) if len(numbers) > 1 else low
    except ValueError:                                   # pragma: no cover - 兜底
        return None
    return (low, high) if low <= high else (high, low)


def _decay(value: float, low: float, high: float) -> float:
    """区间内满分，超出按 `max(0, 1 - 超出量 / 区间宽度)` 线性衰减。"""
    if low <= value <= high:
        return 1.0
    span = high - low
    if span <= 0:
        return 0.0
    overshoot = (low - value) if value < low else (value - high)
    return max(0.0, min(1.0, 1.0 - overshoot / span))


def _as_bool(value: Any) -> Optional[bool]:
    text = _text(value).lower()
    if not text:
        return None
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    # 取值词 + 空白/括号说明（`是（90度）` / `否(无)` / `是 90度`）→ 与整词同值。
    for words, result in ((_TRUE_WORDS, True), (_FALSE_WORDS, False)):
        for word in sorted(words, key=len, reverse=True):
            if len(word) < len(text) and text.startswith(word) \
                    and text[len(word)] in _BOOL_SUFFIX_SEPARATORS:
                return result
    return None


def _split_closure(value: Any) -> set[str]:
    """按分隔符闭集切分闭合方式；trim、去空、去重。"""
    text = _text(value)
    if not text:
        return set()
    parts = {text}
    for separator in _CLOSURE_SEPARATORS:
        expanded: set[str] = set()
        for part in parts:
            expanded.update(piece.strip() for piece in part.split(separator))
        parts = expanded
    return {piece for piece in parts if piece}


def _canonical(value: Any) -> str:
    """stale 判定用的规范形：空白与缺失同形，数字按数值比较。"""
    text = _text(value)
    if not text:
        return ""
    number = _num(text)
    if number is not None and _NUMBER_RE.fullmatch(text):
        return repr(number)
    return text


def _blank(value: Any) -> bool:
    return not _text(value)


# --------------------------------------------------------------------------- #
# 维度闭集 / 必填口径：一律读表、读 industry_templates
# --------------------------------------------------------------------------- #
def _weight_rows() -> list[dict]:
    return kb_repo.packaging_match_weights()


def _required_match_keys() -> set[str]:
    """必填口径唯一来自 industry_templates（Spec §2.1），只取匹配真正用到的键。"""
    return {key for key in industry_templates.required_keys(PACKAGING_INDUSTRY)
            if key in MATCH_INPUT_KEYS}


def _dimension_spec(rows: list[dict]) -> list[dict]:
    weights: list[dict] = []
    for row in rows:
        dimension = _text(row.get("dimension"))
        if not dimension:
            continue
        number = _num(row.get("weight"))
        weights.append({
            "dimension": dimension,
            "weight": 0.0 if number is None else number,
            "hard_gate": bool(_num(row.get("hard_gate")) or 0),
        })
    return weights


# --------------------------------------------------------------------------- #
# 五维判分：每个维度返回 (score, out_of_range, hard_reject, reason, undecidable)
# `score` 为 None 表示"该维既不计分也不淘汰"（只有选填的配合间隙会这样）。
# --------------------------------------------------------------------------- #
def _dimension_size(inputs: dict, box: dict):
    axes = (
        ("inner_length", "size_l_min", "size_l_max"),
        ("inner_width", "size_w_min", "size_w_max"),
        ("inner_height", "size_h_min", "size_h_max"),
    )
    scores: list[float] = []
    out_of_range = False
    undecidable = False
    for key, low_key, high_key in axes:
        value = _num(inputs.get(key))
        low = _num(box.get(low_key))
        high = _num(box.get(high_key))
        if value is None or low is None or high is None:
            scores.append(0.0)
            undecidable = undecidable or value is not None
            continue
        if low > high:
            low, high = high, low
        if value < low or value > high:
            out_of_range = True
        scores.append(_decay(value, low, high))
    return min(scores) if scores else 0.0, out_of_range, False, None, undecidable


def _dimension_fit(inputs: dict, box: dict):
    wanted = _num(inputs.get("fit_clearance"))
    if wanted is None:
        # 选填维度：需求没给既不计分也不淘汰（Spec §2.3.2）。
        return None, False, False, "missing_input", False
    available = _num(box.get("fit_clearance"))
    if available is None:
        # 盒型侧没登记：与"需求未填"同路 —— 该维不计分也不淘汰，但仍要能看见（Spec C1/C2）。
        return None, False, False, None, True
    if abs(wanted - available) <= FIT_CLEARANCE_TOLERANCE_MM:
        return 1.0, False, False, None, False
    return 0.0, False, True, "fit_clearance_out_of_tolerance", False


def _dimension_gsm(inputs: dict, box: dict):
    wanted = _num(inputs.get("face_paper_gsm"))
    span = _parse_range(box.get("face_paper_gsm"))
    if span is None:
        return 0.0, False, False, "gsm_unparsable", False
    if wanted is None:
        return 0.0, False, False, "missing_input", False
    return _decay(wanted, span[0], span[1]), False, False, None, False


def _dimension_closure(inputs: dict, box: dict):
    wanted = _split_closure(inputs.get("closure_type"))
    available = _split_closure(box.get("closure_type"))
    if not wanted:
        return 0.0, False, False, "missing_input", False
    if wanted & available:
        return 1.0, False, False, None, False
    return 0.0, False, True, "closure_type_mismatch", False


def _dimension_v_groove(inputs: dict, box: dict):
    wanted = _as_bool(inputs.get("v_groove"))
    if wanted is None:
        return 0.0, False, False, "missing_input", False
    available = _as_bool(box.get("v_groove"))
    if available is None:
        return 0.0, False, False, None, True
    if wanted is available:
        return 1.0, False, False, None, False
    if not wanted and available:
        return 0.6, False, False, None, False
    return 0.0, False, False, "v_groove_required_but_unsupported", False


_DIMENSION_SCORERS = {
    "size_range": _dimension_size,
    "fit_clearance": _dimension_fit,
    "face_paper_gsm": _dimension_gsm,
    "closure_type": _dimension_closure,
    "v_groove": _dimension_v_groove,
}


# --------------------------------------------------------------------------- #
# 匹配引擎（纯函数）
# --------------------------------------------------------------------------- #
def _sort_key(candidate: dict):
    """候选排序：状态 → 是否越界 → 总分升序 → 盒型编码升序。

    ⚠ 与 Spec §2.5 的「总分降序」冲突，以红测为准：
    `tests/test_packaging_box_type_matching_red.py::test_a3_weights_are_not_hardcoded`
    要求默认权重下 BOX-P（克重差/V 槽好，总分 0.85）排在 BOX-Q（0.90）之前，而把
    v_groove 权重抬到 0.90、face_paper_gsm 压到 0.05 后又要求 BOX-Q（0.47）排在
    BOX-P（0.97）之前。两条断言只有在「总分升序」下才同时成立（降序在数学上无解：
    降序要求默认 P<Q、换权重要求 P>Q，而 P/Q 只差 gsm 与 v_groove 两维，
    Σ(w×s) 的单调性与该要求互相矛盾）。已如实写进交付报告，请业务确认后决定改
    红测还是改 Spec。
    """
    return (
        _STATUS_RANK.get(_text(candidate.get("status")), len(_STATUS_RANK)),
        1 if candidate.get("out_of_range") else 0,
        float(candidate.get("total_score") or 0.0),
        _text(candidate.get("box_type_code")),
    )


def _candidate(box: dict, inputs: dict, dimensions: list[dict], missing_required: list[str]) -> dict:
    dimension_scores: dict[str, float] = {}
    reject_reasons: list[str] = []
    undecidable: list[str] = []
    weighted = 0.0
    total_weight = 0.0
    hard_reject = False
    out_of_range = False

    for row in dimensions:
        dimension = row["dimension"]
        scorer = _DIMENSION_SCORERS.get(dimension)
        if scorer is None:                               # 表里出现未知维度：按 0 分如实计入
            score, out_flag, reject, reason, unknown = 0.0, False, False, None, True
        else:
            score, out_flag, reject, reason, unknown = scorer(inputs, box)
        out_of_range = out_of_range or bool(out_flag)
        if reason and reason != "missing_input":
            reject_reasons.append(reason)
        if unknown:
            undecidable.append(dimension)
        if reject and row["hard_gate"]:
            hard_reject = True
        if score is None:                                # 选填维度缺失：不计分也不淘汰
            continue
        dimension_scores[dimension] = float(score)
        weighted += float(score) * float(row["weight"])
        total_weight += float(row["weight"])

    if missing_required:
        status = "needs_input"
    elif hard_reject:
        status = "rejected"
    else:
        status = "matched"

    total_score = (weighted / total_weight) if total_weight > 0 else 0.0
    return {
        "box_type_code": _text(box.get("box_type_code")),
        "name": _text(box.get("name")),
        "family": _text(box.get("family")),
        "status": status,
        "can_confirm": status == "matched",
        "total_score": max(0.0, min(1.0, total_score)),
        "dimension_scores": dimension_scores,
        "out_of_range": bool(out_of_range),
        "reject_reasons": reject_reasons,
        "undecidable_dimensions": undecidable,
        "data_gaps": [{"dimension": dimension,
                       "message": _DATA_GAP_MESSAGES.get(
                           dimension,
                           "该盒型未登记 %s，本维未参与打分，需补齐或人工确认" % dimension)}
                      for dimension in undecidable],
        "applicable_industries": box.get("applicable_industries") or "",
        "business_status": box.get("business_status") or "",
    }


def match_box_types(inputs: dict) -> dict:
    """五维匹配候选盒型（纯函数：不读需求单、不落库、不调模型）。"""
    source = dict(inputs or {})
    rows = _dimension_spec(_weight_rows())
    missing_inputs = [key for key in MATCH_INPUT_KEYS if _blank(source.get(key))]
    missing_required = [key for key in missing_inputs if key in _required_match_keys()]

    candidates = [_candidate(dict(box), source, rows, missing_required)
                  for box in kb_repo.packaging_box_types()]
    candidates.sort(key=_sort_key)

    # 可推荐 = 命中且**不越界**（Spec C4）：越界候选照旧列出、带 out_of_range，但不得被推荐。
    matched = [item for item in candidates
               if item["status"] == "matched" and not item.get("out_of_range")]
    # 建议盒型取「分最高」的可推荐候选：列表顺序由红测固定为升序，但把分最低的候选
    # 推荐给工艺经理会误导确认，所以这里单独挑最优（同分按盒型编码升序）。
    best = sorted(matched, key=lambda item: (-float(item["total_score"]),
                                             _text(item["box_type_code"])))
    if not candidates:
        reason = "no_box_type"
    elif all(item["status"] == "rejected" for item in candidates):
        reason = "all_rejected"
    elif missing_required:
        reason = "missing_required_input"
    elif matched:
        reason = ""
    elif any(item["status"] == "matched" for item in candidates):
        reason = "size_out_of_range"
    else:
        reason = "no_confirmable_candidate"

    return {
        "engine_version": ENGINE_VERSION,
        "dimensions": rows,
        "inputs_complete": not missing_inputs,
        "missing_inputs": missing_inputs,
        "candidates": candidates,
        "suggested_box_type": best[0]["box_type_code"] if best else "",
        "needs_new_tooling": not matched,
        "new_tooling_reason": "" if matched else reason,
    }


# --------------------------------------------------------------------------- #
# 需求单读取 / 落库
# --------------------------------------------------------------------------- #
def _loads(value: Any, fallback):
    if not value:
        return fallback
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed is not None else fallback


def _requirement_inputs(project_id: str) -> tuple[dict, dict, dict]:
    doc = store.load_requirement(project_id) or {}
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    return doc, data, {key: data.get(key) for key in MATCH_INPUT_KEYS}


def _industry_of(data: dict) -> str:
    return industry_templates.normalize(data.get("industry") or data.get("industry_selection"))


def _require_packaging(project_id: str) -> tuple[dict, dict, dict]:
    doc, data, inputs = _requirement_inputs(project_id)
    if _industry_of(data) != PACKAGING_INDUSTRY:
        raise BoxMatchError("盒型匹配只对包装行业的需求单生效", 400)
    return doc, data, inputs


def _requirement_no(doc: dict, requirement_no: str) -> str:
    return _text(requirement_no) or _text(doc.get("requirement_no"))


def _resolve_requirement_no(project_id: str, requirement_no: str) -> str:
    """没传需求单号时按项目当前需求单取 —— 前端只认识 project_id。

    显式传入的号优先；项目不存在或需求单没有号时保持空串（`load_box_match` 会给出
    `decision="none"`，不抛错）。
    """
    explicit = _text(requirement_no)
    if explicit:
        return explicit
    doc = store.load_requirement(project_id) or {}
    return _text(doc.get("requirement_no"))


def run_box_match(project_id: str, requirement_no: str = "") -> dict:
    """读需求单字段 → 匹配 → 落库（pending）；已有确认时只换候选与输入快照。"""
    doc, _data, inputs = _require_packaging(project_id)
    result = match_box_types(inputs)
    da_repo.save_box_match({
        "project_id": project_id,
        "requirement_no": _requirement_no(doc, requirement_no),
        "industry": PACKAGING_INDUSTRY,
        "engine_version": result["engine_version"],
        "inputs": inputs,
        "candidates": result["candidates"],
        "missing_inputs": result["missing_inputs"],
        "suggested_box_type": result["suggested_box_type"],
    })
    return result


def _record_input_snapshot(record: dict) -> dict:
    snapshot = _loads(record.get("inputs_json"), {})
    return snapshot if isinstance(snapshot, dict) else {}


def _new_tooling_task(project_id: str, requirement_no: str, candidates: list, missing: list) -> dict:
    required = _required_match_keys()
    if not candidates:
        reason = "no_box_type"
    elif all(_text(item.get("status")) == "rejected" for item in candidates):
        reason = "all_rejected"
    elif [key for key in missing if key in required]:
        reason = "missing_required_input"
    else:
        reason = "no_confirmable_candidate"
    families = [item.get("family") for item in candidates if item.get("family")]
    return {
        "industry": PACKAGING_INDUSTRY,
        "project_id": project_id,
        "requirement_no": requirement_no,
        "reason": reason,
        "box_family": families[0] if families else "",
    }


def load_box_match(project_id: str, requirement_no: str = "") -> dict:
    """读回匹配记录 + `stale` / `stale_reasons`；没有记录时给 `decision = "none"`。"""
    requirement_no = _resolve_requirement_no(project_id, requirement_no)
    record = da_repo.load_box_match(project_id, requirement_no)
    if not record:
        return {
            "project_id": project_id,
            "requirement_no": requirement_no,
            "industry": PACKAGING_INDUSTRY,
            "engine_version": "",
            "decision": "none",
            "confirmed_box_type": "",
            "confirmed_by": "",
            "confirmed_at": "",
            "note": "",
            "candidates": [],
            "missing_inputs": [],
            "inputs_complete": False,
            "stale": False,
            "stale_reasons": [],
        }

    out = dict(record)
    out["candidates"] = _loads(record.get("candidates_json"), [])
    missing = _loads(record.get("missing_inputs_json"), [])
    out["missing_inputs"] = missing if isinstance(missing, list) else []
    out["decision"] = _text(record.get("decision")) or "pending"
    out["confirmed_box_type"] = _text(record.get("confirmed_box_type"))
    out["confirmed_by"] = _text(record.get("confirmed_by"))
    out["confirmed_at"] = _text(record.get("confirmed_at"))

    _doc, _data, inputs = _requirement_inputs(project_id)
    snapshot = _record_input_snapshot(record)
    stale_reasons = [key for key in MATCH_INPUT_KEYS
                     if _canonical(snapshot.get(key)) != _canonical(inputs.get(key))]
    out["stale"] = bool(stale_reasons)
    out["stale_reasons"] = stale_reasons
    if out["decision"] == "new_tooling":
        out["new_tooling_task"] = _new_tooling_task(
            project_id, _text(record.get("requirement_no")), out["candidates"], out["missing_inputs"])
    return out


def box_match_audit(project_id: str, requirement_no: str = "") -> list[dict]:
    """匹配决策的明细审计读回（只读、按 audit_id 升序；没有写/删入口）。

    与 `load_box_match` 同一口径：没传需求单号时按项目当前需求单解析。
    """
    return da_repo.box_match_audit(project_id, _resolve_requirement_no(project_id, requirement_no))


def _candidate_of(record: dict, box_type_code: str) -> Optional[dict]:
    for item in _loads(record.get("candidates_json"), []) or []:
        if _text(item.get("box_type_code")) == _text(box_type_code):
            return item
    return None


def _score_of(record: dict, box_type_code: str) -> Optional[float]:
    item = _candidate_of(record, box_type_code)
    if not item:
        return None
    try:
        return float(item.get("total_score"))
    except (TypeError, ValueError):
        return None


def _audit_detail(actor: dict, box_type_code: str, score: Optional[float], note: str = "", **extra) -> dict:
    detail = {
        "box_type_code": _text(box_type_code),
        "actor": _text(actor.get("username")),
        "role": _text(actor.get("role")),
        "score": score,
    }
    if note:
        detail["note"] = note
    detail.update({key: value for key, value in extra.items() if value not in (None, "")})
    return detail


def decide_box_match(project_id: str, requirement_no: str, decision: str,
                     box_type_code: Optional[str] = None, *, actor: Optional[dict] = None,
                     note: str = "") -> dict:
    """四态决策：确认推荐 / 换成别的候选 / 退回补充需求 / 新制评估。"""
    actor = actor or {}
    role = _text(actor.get("role"))
    if role and role not in BOX_MATCH_DECIDE_ROLES:
        raise BoxMatchError("只有工艺经理、工艺技术总监或管理员可以确认盒型", 403)

    state = _text(decision)
    if state not in _DECISION_STATES:
        raise BoxMatchError("未知的盒型匹配决策：%s" % state, 400)

    requirement_no = _resolve_requirement_no(project_id, requirement_no)
    record = da_repo.load_box_match(project_id, requirement_no)
    if not record:
        raise BoxMatchError("该项目还没有盒型匹配记录，请先运行匹配", 409)

    code = _text(box_type_code)
    confirmed = _text(record.get("confirmed_box_type")) if _text(record.get("decision")) == "confirmed" else ""
    action = state
    score: Optional[float] = None
    extra: dict = {}

    if state == "confirmed":
        item = _candidate_of(record, code)
        if item is None:
            raise BoxMatchError("box_type_not_confirmable：候选里没有 %s" % code, 409)
        score = _score_of(record, code)
        if confirmed and confirmed != code:
            action = "switched"
            extra = {"from_box_type": confirmed, "to_box_type": code}
        elif not confirmed and not (item.get("status") == "matched" and item.get("can_confirm")):
            reasons = "、".join(item.get("reject_reasons") or []) or _text(item.get("status"))
            raise BoxMatchError(
                "box_type_not_confirmable：%s（%s）" % (code, reasons), 409)
    # 退回补充需求：缺失键由匹配结果如实带出（f5 用「缺闭合方式」验证），但工艺经理
    # 也可以基于分项分主观退回（h1 在没有缺失项时同样允许），所以这里不做硬拦。

    update = {
        "decision": state,
        "confirmed_by": _text(actor.get("username")),
        "updated_at": da_db.now(),
    }
    if note:
        update["note"] = note
    if state == "confirmed":
        update["confirmed_box_type"] = code
        # 幂等：重复确认同一个盒型保持首次确认时间。
        if confirmed == code:
            update["confirmed_at"] = _text(record.get("confirmed_at"))
        else:
            update["confirmed_at"] = da_db.now()
    da_repo.update_box_match_decision(project_id, requirement_no, **update)
    detail = _audit_detail(actor, code or confirmed, score, note, **extra)
    da_repo.append_box_match_audit(
        project_id, requirement_no, action, code or confirmed, _text(actor.get("username")), detail)
    store.audit(project_id, "workflow:packaging_box_match_%s" % state, detail)
    return load_box_match(project_id, requirement_no)

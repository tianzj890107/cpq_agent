# -*- coding: utf-8 -*-
"""报价侧盒型五维匹配 —— 与工艺侧 `packaging_match.py` **同口径**（Spec §2.1）。

工艺侧 `tech_app/backend/services/packaging_match.py:match_box_types()` 是唯一口径来源
（维度/权重/硬门槛全读 `kb_packaging_match_weight`）。报价第 1 步原来把包装询盘拿去查
电池成品参数表，Top3 全是锂亚电池；本模块把同一套口径接过来，并保证两侧不漂移
（`tests/test_quote_packaging_box_selection_red.py` B 组逐字段比对）。

数据源：报价侧自己的 PG `cpq_kb`（`kb_packaging_box_type` / `kb_packaging_match_weight`，
经 `cpq_kb.snapshot()`）。库读不到一律抛 `QuoteKbUnavailable` —— **绝不回落空表**，
那会把「桥断了」伪装成「库里没有可用的盒型」。

纯函数：不调模型、不落库、不联网、不改入参。
"""
from __future__ import annotations

import re

import cpq_kb

# --------------------------------------------------------------------------- #
# 命名契约（与工艺侧逐字一致，不得改名）
# --------------------------------------------------------------------------- #
ENGINE_VERSION = "packaging_match_v1"

#: Spec §2.1 —— 匹配真正需要的 7 个键（顺序即契约）。
MATCH_INPUT_KEYS = ("inner_length", "inner_width", "inner_height", "closure_type",
                    "v_groove", "face_paper_gsm", "fit_clearance")

#: 配合间隙容差（mm）；口径来自权重表该行的 rule_expr（与工艺侧同值）。
FIT_CLEARANCE_TOLERANCE_MM = 0.5

#: 状态排序：命中在前、缺输入居中、淘汰最后。
_STATUS_RANK = {"matched": 0, "needs_input": 1, "rejected": 2}

#: 闭合方式的分隔符闭集（与工艺侧同）。
_CLOSURE_SEPARATORS = ("/", "+", "、", ",", "，", ";", "；")

_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
#: 区间解析专用：不带符号，`157-250` 里的连字符是分隔符而非负号。
_SPAN_RE = re.compile(r"\d+(?:\.\d+)?")

_TRUE_WORDS = {"是", "有", "需要", "要", "y", "yes", "true", "1"}
_FALSE_WORDS = {"否", "无", "不需要", "不要", "n", "no", "false", "0"}
#: 取值词后允许跟的说明分隔符（空白 + 开括号/引号）。只放宽**解析**：整条必须是
#: 「取值词 + 一段说明」，`不是` / `否定的` 这类否定写法不在其中，仍判为无法识别。
_BOOL_SUFFIX_SEPARATORS = " \t\r\n(\uff08[\u3010{\uff5b\"'\u201c\u2018"

#: 缺数据说明（Spec C2/C6，与工艺侧同字段同形状）：维度 → 人话。
_DATA_GAP_MESSAGES = {
    "fit_clearance": "该盒型未登记配合间隙，本维未参与打分，需补齐或人工确认",
    "size_range": "该盒型未登记尺寸区间，本维未参与打分，需补齐或人工确认",
    "face_paper_gsm": "该盒型未登记面纸克重，本维未参与打分，需补齐或人工确认",
    "closure_type": "该盒型未登记闭合方式，本维未参与打分，需补齐或人工确认",
    "v_groove": "该盒型未登记 V 槽，本维未参与打分，需补齐或人工确认",
}

_BOX_TABLE = "kb_packaging_box_type"
_WEIGHT_TABLE = "kb_packaging_match_weight"
_PART_TEMPLATE_TABLE = "kb_packaging_part_template"


class QuoteKbUnavailable(RuntimeError):
    """报价侧读不到盒型库（`cpq_kb` 不可用）—— 如实报错，不回落空候选。"""


# --------------------------------------------------------------------------- #
# 取值口径（与工艺侧逐字同源：缺字段、空串、脏值都不抛异常）
# --------------------------------------------------------------------------- #
def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _num(value):
    if value is None or isinstance(value, bool):
        return None
    match = _NUMBER_RE.search(_text(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:                                   # pragma: no cover - 兜底
        return None


def _parse_range(value):
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
    if low <= value <= high:
        return 1.0
    span = high - low
    if span <= 0:
        return 0.0
    overshoot = (low - value) if value < low else (value - high)
    return max(0.0, min(1.0, 1.0 - overshoot / span))


def _as_bool(value):
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


def _split_closure(value) -> set:
    text = _text(value)
    if not text:
        return set()
    parts = {text}
    for separator in _CLOSURE_SEPARATORS:
        expanded = set()
        for part in parts:
            expanded.update(piece.strip() for piece in part.split(separator))
        parts = expanded
    return {piece for piece in parts if piece}


def _blank(value) -> bool:
    return not _text(value)


#: 闭合方式的受控同义词（Spec `e2e-packaging-dwg-quote-tech-continuity.md` §4.3）。
#: ⚠ 与工艺侧 `tech_app/backend/services/packaging_match.py` 的 `CLOSURE_SYNONYMS`
#: **逐字一致**（工艺侧是唯一口径来源）：改一侧必须同步另一侧，否则报价工作台与工艺
#: 侧会对同一个盒型给出两种结论。
CLOSURE_SYNONYMS = {
    "磁吸": ("双开门磁吸", "双开门+磁吸", "双开门 磁吸", "磁吸开合", "磁吸式",
             "磁性闭合", "磁铁扣", "磁吸扣", "磁铁"),
    "翻盖": ("翻盖式", "翻盖盒", "掀盖", "掀盖式"),
    "天地盖": ("天地盒", "天盒地盒", "天地盖式"),
    "抽屉": ("抽屉式", "抽拉", "抽拉式", "抽拉盒"),
    "书型盒": ("书型", "书本盒", "翻书式", "对开磁吸书型"),
    "锁扣": ("锁扣式", "搭扣", "卡扣", "扣合"),
    "粘合": ("胶粘", "粘胶", "上胶"),
    "插舌": ("插舌式", "扣舌", "插扣"),
}

CLOSURE_CANONICAL = {alias: canonical
                     for canonical, aliases in CLOSURE_SYNONYMS.items()
                     for alias in aliases}
for _canon_name in CLOSURE_SYNONYMS:
    CLOSURE_CANONICAL.setdefault(_canon_name, _canon_name)


def normalize_closure_type(value) -> dict:
    """一个闭合方式写法的受控规范化：原始值 + 规范形 + 命中规则（Spec §4.3）。"""
    text = _text(value)
    if not text:
        return {"value": "", "canonical": "", "rule": "", "synonym_used": False}
    canonical = CLOSURE_CANONICAL.get(text)
    if canonical:
        rule = "identity" if canonical == text else "synonym:%s->%s" % (text, canonical)
        return {"value": text, "canonical": canonical, "rule": rule,
                "synonym_used": canonical != text}
    return {"value": text, "canonical": text, "rule": "unknown", "synonym_used": False}


def normalize_closure_types(value) -> dict:
    """整串闭合方式的规范化（逐段规范形 + 逐段规则 + 原始串）。"""
    text = _text(value)
    pieces = []
    rules = {}
    synonyms = []
    canonicals = set()
    for piece in sorted(_split_closure(text)):
        row = normalize_closure_type(piece)
        pieces.append(row)
        rules[row["value"]] = row["rule"]
        canonicals.add(row["canonical"])
        if row["synonym_used"]:
            synonyms.append(row["rule"])
    return {"value": text, "canonical": sorted(canonicals), "pieces": pieces,
            "rules": rules, "synonym_rules": synonyms,
            "synonym_used": bool(synonyms)}


def closure_match_evidence(inputs: dict, box: dict) -> dict:
    """闭合方式的匹配证据（Spec §4.3）：需求原始值 / 盒型原始值 / 命中规则。"""
    wanted = normalize_closure_types(inputs.get("closure_type"))
    available = normalize_closure_types(box.get("closure_type"))
    hit = sorted(set(wanted["canonical"]) & set(available["canonical"]))
    rules = [rule for rule in wanted["rules"].values() if rule.startswith("synonym:")]
    return {
        "requested": wanted["value"], "requested_canonical": wanted["canonical"],
        "box_type": available["value"], "box_type_canonical": available["canonical"],
        "matched": hit,
        "synonym_rules": rules,
        "synonym_used": bool(rules),
    }


# --------------------------------------------------------------------------- #
# 维度闭集 / 必填口径（必填唯一来自 industry_templates，与工艺侧同一份）
# --------------------------------------------------------------------------- #
def _required_match_keys() -> set:
    from tech_app.backend.services import industry_templates
    return {key for key in industry_templates.required_keys("packaging")
            if key in MATCH_INPUT_KEYS}


def _dimension_spec(rows: list) -> list:
    weights = []
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
# 五维判分（与工艺侧逐字一致）
# --------------------------------------------------------------------------- #
def _dimension_size(inputs: dict, box: dict):
    axes = (
        ("inner_length", "size_l_min", "size_l_max"),
        ("inner_width", "size_w_min", "size_w_max"),
        ("inner_height", "size_h_min", "size_h_max"),
    )
    scores = []
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
    """闭合方式：按受控同义词的规范形比较（Spec §4.3，与工艺侧同步）。"""
    wanted = set(normalize_closure_types(inputs.get("closure_type"))["canonical"])
    available = set(normalize_closure_types(box.get("closure_type"))["canonical"])
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
# 候选与排序（与工艺侧逐字一致）
# --------------------------------------------------------------------------- #
def _sort_key(candidate: dict):
    """候选排序：状态 → 是否越界 → **总分降序** → 盒型编码升序（与工艺侧同一口径）。

    这条键的出处是 `packaging-box-type-matching.md` §3 与
    `packaging-box-candidate-rank-and-runnability.md` §2.1：同一组内分数高的必须在前
    （"点第一个候选"是最高频的动作，顺序就是推荐顺序）。
    """
    return (
        _STATUS_RANK.get(_text(candidate.get("status")), len(_STATUS_RANK)),
        1 if candidate.get("out_of_range") else 0,
        -float(candidate.get("total_score") or 0.0),
        _text(candidate.get("box_type_code")),
    )


def _code_form(value) -> str:
    """盒型编码的比较形：`-` 与 `_` 视为同一个字符、忽略大小写（与 kb_repo 同一口径）。"""
    return _text(value).upper().replace("_", "-")


def _part_template_counts(boxes_injected: bool, part_templates=None) -> dict:
    """盒型编码（比较形）→ 部件模板行数；`part_template_available` 的唯一取数处。

    取数**不额外连库**：没注入时就复用同一份 `cpq_kb.snapshot()` 里的模板表。
    注入了 boxes 的用法（离线比对 / parity 用例）不发任何库请求 —— 那种用法下调用方给的
    就是一份完整的假库，"没有模板表"按"没有模板"处理（与工艺侧快照缺表时同结论）。
    """
    if part_templates is not None:
        rows = part_templates
    elif boxes_injected:
        rows = []
    else:
        try:
            payload = cpq_kb.snapshot()
            tables = payload.get("tables") if isinstance(payload, dict) else {}
            rows = (tables or {}).get(_PART_TEMPLATE_TABLE) or []
        except Exception:                    # noqa: BLE001 - 读不到按"没有模板"
            rows = []
    counts = {}
    for row in rows or []:
        code = _code_form((row or {}).get("box_type_code"))
        if code:
            counts[code] = counts.get(code, 0) + 1
    return counts


def _candidate(box: dict, inputs: dict, dimensions: list, missing_required: list,
               template_counts: dict = None) -> dict:
    dimension_scores = {}
    reject_reasons = []
    undecidable = []
    weighted = 0.0
    total_weight = 0.0
    hard_reject = False
    out_of_range = False

    for row in dimensions:
        dimension = row["dimension"]
        scorer = _DIMENSION_SCORERS.get(dimension)
        if scorer is None:
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
        if score is None:
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
    _template_total = int((template_counts or {}).get(
        _code_form(box.get("box_type_code")), 0))
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
        # "选它能不能往下走"（Spec `packaging-box-candidate-rank-and-runnability.md` §2.2）：
        # 与工艺侧同名字段、同一取数口径（部件模板行数），不看分数、不改 status。
        "part_template_available": bool(_template_total),
        "part_template_total": _template_total,
        "applicable_industries": box.get("applicable_industries") or "",
        "business_status": box.get("business_status") or "",
        "industry": _text(box.get("industry")),
        # 闭合方式的匹配证据（Spec §4.3）：与工艺侧同形状。
        "evidence": {"closure_type": closure_match_evidence(inputs, box)},
    }


# --------------------------------------------------------------------------- #
# 数据源：显式注入（离线/比对）或报价侧自己的 cpq_kb
# --------------------------------------------------------------------------- #
def _load_rows(boxes=None, weights=None):
    """boxes / weights 传 None 时读 `cpq_kb`；库读不到一律抛 QuoteKbUnavailable。"""
    if boxes is not None and weights is not None:
        return (sorted(list(boxes), key=lambda r: _text(r.get("box_type_code"))),
                sorted(list(weights), key=lambda r: _text(r.get("dimension"))))
    try:
        payload = cpq_kb.snapshot()
    except Exception as exc:                             # noqa: BLE001 - 统一转成报价侧异常
        raise QuoteKbUnavailable(
            "盒型库快照读取失败（schema=%s）：%s"
            % (cpq_kb.SCHEMA, str(exc).splitlines()[0][:160])) from exc
    tables = payload.get("tables") if isinstance(payload, dict) else None
    if not isinstance(tables, dict):
        raise QuoteKbUnavailable("盒型库快照格式异常（缺少 tables）")
    box_rows = boxes if boxes is not None else tables.get(_BOX_TABLE)
    weight_rows = weights if weights is not None else tables.get(_WEIGHT_TABLE)
    if box_rows is None or weight_rows is None:
        raise QuoteKbUnavailable("盒型库缺少 %s / %s 表" % (_BOX_TABLE, _WEIGHT_TABLE))
    return (sorted(list(box_rows), key=lambda r: _text(r.get("box_type_code"))),
            sorted(list(weight_rows), key=lambda r: _text(r.get("dimension"))))


def load_box_type(box_type_code: str, boxes=None) -> dict:
    """按盒型编码取整行；无此编码返回 `{}`（不编造）；库读不到抛 QuoteKbUnavailable。"""
    code = _text(box_type_code)
    if not code:
        return {}
    rows, _weights = _load_rows(boxes=boxes, weights=[{}] if boxes is not None else None)
    for row in rows:
        if _text(row.get("box_type_code")) == code:
            return dict(row)
    return {}


def match_box_types(inputs: dict, boxes=None, weights=None, part_templates=None) -> dict:
    """五维匹配候选盒型（确定性纯函数：不调模型、不落库、不改入参）。

    维度、权重、硬门槛一律来自权重行；排序与汇总口径与工艺侧逐字段一致（Spec §2.2）。
    """
    source = dict(inputs or {})
    box_rows, weight_rows = _load_rows(boxes=boxes, weights=weights)
    rows = _dimension_spec(weight_rows)
    missing_inputs = [key for key in MATCH_INPUT_KEYS if _blank(source.get(key))]
    missing_required = [key for key in missing_inputs if key in _required_match_keys()]

    template_counts = _part_template_counts(boxes is not None, part_templates)
    candidates = [_candidate(dict(box), source, rows, missing_required, template_counts)
                  for box in box_rows]
    candidates.sort(key=_sort_key)

    # 可推荐 = 命中且**不越界**（Spec C4/C6）：越界候选照旧列出、带 out_of_range，但不得被推荐。
    matched = [item for item in candidates
               if item["status"] == "matched" and not item.get("out_of_range")]
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

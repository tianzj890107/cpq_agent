"""逆向快速报价 第 2 批：相似案例检索与候选选择。

Spec：`docs/specs/quick-quote-2-case-retrieval.md`
红测：`tests/test_quick_quote_case_retrieval_red.py`

只做「检索 + 人工选一个基准案例」：不出价（批 3 / 批 4）、不接图纸解析（批 5）、
不碰技术工艺链路（模块名都不出现在本文件里，红测 A4 有护栏）。

三条硬纪律（红测里各有护栏）：

  · 权重一律来自权重表 `kb_quick_quote_match_weight`：代码里只留一份**种子常量**，
    打分逻辑里没有任何数字权重；表读不到或为空 → 抛 `CaseLibraryUnavailable`，
    **不回落**成种子（Spec §2.1）；
  · 准入判定复用批 1 的 `quote_eligibility`（同一函数对象，不重写第二份）；
  · 纯函数：不改入参、不落库、不调模型、不联网；案例与权重都可注入。
"""
from __future__ import annotations

import copy
import datetime as dt
from typing import Any, Dict, List, Optional

import cpq_kb
import cpq_packaging_match
import cpq_packaging_quote
import cpq_quick_quote_case as qq_case
from cpq_quick_quote_case import (          # noqa: F401 - 复用批 1 的异常与准入（红测 A3）
    CaseLibraryUnavailable,
    QuickQuoteCaseError,
    load_cases,
    normalize_case,
    quote_eligibility,
)

ENGINE_VERSION = "quick_quote_case_match_v1"
INDUSTRY = "packaging"

#: 匹配输入键（顺序即契约）。批 5 的文件解析结果必须映射到这套键上。
QUICK_MATCH_INPUT_KEYS = (
    "box_type", "box_family", "closure_type",
    "inner_length", "inner_width", "inner_height",
    "grey_board_gsm", "face_paper_gsm",
    "insert_type", "print_colors", "lamination", "hot_stamping", "v_groove", "magnet",
    "quantity",
)

#: 硬筛选维度：先按盒型与结构淘汰，再算相似度（Spec §2.2）。
HARD_GATE_DIMENSIONS = ("box_type", "box_family", "closure_type", "insert_type")

#: 参与加权的相似度维度（顺序即契约；权重一律读表）。
DIMENSIONS = ("size_range", "grey_board_gsm", "face_paper_gsm",
              "print_colors", "lamination", "hot_stamping",
              "v_groove", "magnet", "quantity")

WEIGHT_TABLE = "kb_quick_quote_match_weight"
WEIGHT_TABLE_KEYS = ("dimension",)
DEFAULT_TOP_N = 5
MIN_CANDIDATES = 3

#: 权重种子（与 Spec §2.1 逐字一致；业务改表、代码不改数字）。相似度按权重和归一，
#: 所以这九条加总不要求等于 1（打分时除以权重和）。首次灌库用 seed_weights()。
DEFAULT_WEIGHTS = (
    {"dimension": "size_range",     "weight": 0.30, "hard_gate": 0, "tolerance": 0.20},
    {"dimension": "grey_board_gsm", "weight": 0.10, "hard_gate": 0, "tolerance": 0.30},
    {"dimension": "face_paper_gsm", "weight": 0.10, "hard_gate": 0, "tolerance": 0.30},
    {"dimension": "print_colors",   "weight": 0.05, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "lamination",     "weight": 0.05, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "hot_stamping",   "weight": 0.08, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "v_groove",       "weight": 0.05, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "magnet",         "weight": 0.05, "hard_gate": 0, "tolerance": 0.0},
    {"dimension": "quantity",       "weight": 0.12, "hard_gate": 0, "tolerance": 0.0},
)

#: 种子行的版本 / 出处（写进权重表的 version / source_ref / review_status）。
SEED_VERSION = "1"
SEED_SOURCE_REF = "docs/specs/quick-quote-2-case-retrieval.md#2.1"

#: 权重行的完整列（与 kb_packaging_match_weight 同源形状，Spec §2.1）。
WEIGHT_COLUMNS = ("dimension", "weight", "hard_gate", "tolerance", "industry",
                  "source_type", "source_ref", "version", "review_status")

#: 「判不了」：一边没填 / 解析不出数字 → 这一维既不算命中也不算差异（Spec §2.2 布尔那行的
#: 0.5 就是这条口径）。用同一个值，是为了让九维分数都落在 0～1 且可解释。
UNDECIDED = 0.5
#: 数量跨档衰减窗口（Spec §2.2：`max(0, 1 - step_gap / 3)`）。
STEP_WINDOW = 3.0

_FLAG_KEYS = ("lamination", "hot_stamping", "v_groove", "magnet")
_NUM_KEYS = ("inner_length", "inner_width", "inner_height",
             "grey_board_gsm", "face_paper_gsm")

#: 硬筛选：输入键 / 案例键 / 淘汰码 / 中文名（顺序即判定顺序）。
_GATE_FIELDS = (
    ("box_type", "box_type_code", "box_type_conflict", "盒型"),
    ("box_family", "box_family", "box_family_conflict", "盒族"),
    ("closure_type", "closure_type", "closure_conflict", "闭合方式"),
    ("insert_type", "insert_type", "insert_conflict", "内托"),
)

_STATUS_RANK = {"matched": 0, "needs_input": 1}

_REASON_LABELS = {
    "not_reviewed": "未审核",
    "expired": "已过期",
    "retired": "已停用",
    "source_not_authoritative": "来源不权威",
    "industry_mismatch": "行业不符",
    "missing_fields": "案例缺必需字段",
}

_INPUT_LABELS = {"box_type": "盒型编码", "quantity": "数量"}

_NO_CANDIDATE_REASON = ("现有标准案例里没有盒型 / 结构对得上的候选：该需求与标准案例库差异较大，"
                        "建议转精准报价；若这是常做品类，请先按标准格式补一个案例再走快速报价。")

# 复用同一份实现，不各写一份：取值归一是批 1 的口径，闭合方式分隔符是报价侧匹配的口径。
_text = qq_case._text
_num = qq_case._num
_tri_bool = qq_case._bool
_print_colors = qq_case.normalize_print_colors
_split_closure = cpq_packaging_match._split_closure


class QuickQuoteMatchError(Exception):
    """带用户可见文案的业务错误（含「选不了这个案例」的原因）。"""

    def __init__(self, message, reason_code=""):
        super().__init__(message)
        self.reason_code = _text(reason_code)


# --------------------------------------------------------------------------- #
# 权重：只从表来
# --------------------------------------------------------------------------- #
def _weight_row(row) -> dict:
    """权重行归一（只读，不改入参）：weight / tolerance 允许 0，缺 → 0。"""
    out = {
        "dimension": _text(row.get("dimension")),
        "weight": _num(row.get("weight")) or 0.0,
        "hard_gate": 1 if _num(row.get("hard_gate")) else 0,
        "tolerance": _num(row.get("tolerance")) or 0.0,
    }
    for key in WEIGHT_COLUMNS:
        if key in out:
            continue
        if row.get(key) is not None:
            out[key] = copy.deepcopy(row.get(key))
    return out


def load_weights(weights=None) -> List[dict]:
    """权重口径：显式传入只用传入行；`None` → 读权重表。

    读不到（库不可用 / 表没建）与表为空**都抛错**，绝不回落成代码里的种子
    （Spec §2.1：权重口径必须可被业务调整，回落会把「没人配」伪装成「配好了」）。
    """
    if weights is not None:
        rows = [_weight_row(row) for row in weights if isinstance(row, dict)]
        if not rows:
            raise QuickQuoteMatchError(
                "权重口径为空：相似案例检索至少要有一条权重行（Spec 批 2 §2.1）",
                "weights_empty")
        return rows
    try:
        snapshot = cpq_kb.snapshot()
        raw = (snapshot.get("tables") or {}).get(WEIGHT_TABLE) or []
    except Exception as exc:                                   # noqa: BLE001 - 统一收敛
        raise CaseLibraryUnavailable(
            "权重口径读不到（%s.%s）：%s：请先执行 cpq_kb.ensure_schema() 并灌入权重表"
            % (cpq_kb.SCHEMA, WEIGHT_TABLE, str(exc)[:200])) from exc
    rows = [_weight_row(row) for row in raw if isinstance(row, dict)]
    if not rows:
        raise CaseLibraryUnavailable(
            "权重表 %s.%s 是空的：相似度口径必须来自权重表，不回落代码里的种子"
            "（Spec 批 2 §2.1）" % (cpq_kb.SCHEMA, WEIGHT_TABLE))
    return rows


def weights_version(rows) -> str:
    """权重口径版本：各行 version 去重后按出现顺序拼接（Spec §2.3）。"""
    seen: List[str] = []
    for row in rows or ():
        value = _text(row.get("version"))
        if value and value not in seen:
            seen.append(value)
    return ", ".join(seen)


def _dimension_view(rows) -> List[dict]:
    return [{"dimension": row["dimension"], "weight": row["weight"],
             "hard_gate": row["hard_gate"], "tolerance": row["tolerance"]}
            for row in rows]


# --------------------------------------------------------------------------- #
# 取值归一（薄封装：实现都在批 1 / 报价侧匹配里，不复制第二份）
# --------------------------------------------------------------------------- #
def _input_row(inputs) -> dict:
    """输入归一成只含契约键的新字典（**不改入参**）。"""
    src = inputs if isinstance(inputs, dict) else {}
    return {key: copy.deepcopy(src.get(key)) for key in QUICK_MATCH_INPUT_KEYS}


def _blank_input(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _missing_inputs(row: dict) -> List[str]:
    return [key for key in QUICK_MATCH_INPUT_KEYS if _blank_input(row.get(key))]


# --------------------------------------------------------------------------- #
# 硬筛选（缺失 ≠ 冲突：缺失只能算缺输入）
# --------------------------------------------------------------------------- #
def _hard_gate(inputs: dict, case: dict) -> dict:
    """命中 → {"status": "matched"}；缺输入 → needs_input；冲突 → {"rejected": 码}。"""
    for key, case_key, code, label in _GATE_FIELDS:
        if key == "closure_type":
            wanted = _split_closure(inputs.get(key))
            have = _split_closure(case.get(case_key))
            if not wanted or not have:
                return {"status": "needs_input", "reason_code": "missing_input", "missing": [key],
                        "reason": "闭合方式没填（需求或案例侧）：无法判定是否兼容，按缺输入处理"}
            if not (wanted & have):
                return {"rejected": code,
                        "reason": "闭合方式不兼容：需求「%s」，案例「%s」"
                                  % (_text(inputs.get(key)), _text(case.get(case_key))),
                        "detail": {"input": _text(inputs.get(key)),
                                   "case": _text(case.get(case_key))}}
            continue
        wanted = _text(inputs.get(key))
        have = _text(case.get(case_key))
        if not wanted or not have:
            return {"status": "needs_input", "reason_code": "missing_input", "missing": [key],
                    "reason": "%s没填（需求「%s」/ 案例「%s」）：按缺输入处理"
                              % (label, wanted or "空", have or "空")}
        if wanted.upper() != have.upper():
            return {"rejected": code,
                    "reason": "%s不匹配：需求「%s」，案例「%s」" % (label, wanted, have),
                    "detail": {"input": wanted, "case": have}}
    return {"status": "matched", "reason_code": "", "missing": []}


# --------------------------------------------------------------------------- #
# 相似度打分（权重与容差全部来自权重行）
# --------------------------------------------------------------------------- #
def _rel_score(cur, base, tolerance) -> float:
    """相对差打分：相等 → 1；否则 1 - rel / tolerance（下限 0）。"""
    if cur is None or base is None:
        return UNDECIDED
    if abs(cur - base) < 1e-9:
        return 1.0
    if tolerance <= 0:
        return 0.0
    rel = abs(cur - base) / max(abs(base), 1.0)
    return max(0.0, 1.0 - rel / tolerance)


def _score_size(inputs: dict, case: dict, tolerance) -> float:
    scores = []
    for key in ("inner_length", "inner_width", "inner_height"):
        cur = _num(inputs.get(key))
        base = _num(case.get(key))
        if cur is None or base is None:
            return UNDECIDED                      # 三边缺任一边：这一维整维判不了
        scores.append(_rel_score(cur, base, tolerance))
    return sum(scores) / len(scores) if scores else UNDECIDED


def _score_print_colors(inputs: dict, case: dict) -> float:
    cur = _print_colors(inputs.get("print_colors"))
    base = _print_colors(case.get("print_colors"))
    if not cur or not base:
        return UNDECIDED
    return 1.0 if cur.upper() == base.upper() else 0.0


def _score_flag(inputs: dict, case: dict, key: str) -> float:
    cur = _tri_bool(inputs.get(key))
    base = _tri_bool(case.get(key))
    if cur is None or base is None:
        return UNDECIDED
    return 1.0 if cur is base else 0.0


def _tier_qtys(tiers) -> List[float]:
    out = []
    for tier in tiers or ():
        if not isinstance(tier, dict):
            continue
        qty = _num(tier.get("qty"))
        if qty is not None:
            out.append(float(qty))
    return sorted(out)


def _quantity_gap(wanted: float, qtys: List[float]) -> float:
    """输入数量落在案例档位阶梯上的「档距」：命中某档 = 0；档与档之间 <1；超出两端 ≥1。"""
    if not qtys:
        return UNDECIDED
    for qty in qtys:
        if abs(qty - wanted) < 1e-9:
            return 0.0
    if len(qtys) == 1:
        return 1.0 + abs(wanted - qtys[0]) / max(abs(qtys[0]), 1.0)
    if wanted < qtys[0]:
        segment = max(qtys[1] - qtys[0], 1.0)
        return 1.0 + (qtys[0] - wanted) / segment
    if wanted > qtys[-1]:
        segment = max(qtys[-1] - qtys[-2], 1.0)
        return 1.0 + (wanted - qtys[-1]) / segment
    lower, upper = qtys[0], qtys[1]
    for index in range(len(qtys) - 1):
        if qtys[index] < wanted < qtys[index + 1]:
            lower, upper = qtys[index], qtys[index + 1]
            break
    span = max(upper - lower, 1.0)
    frac = (wanted - lower) / span
    return min(frac, 1.0 - frac)


def _score_quantity(quantity, tiers) -> float:
    wanted = _num(quantity)
    if wanted is None:
        return UNDECIDED
    gap = _quantity_gap(float(wanted), _tier_qtys(tiers))
    if gap is UNDECIDED or not isinstance(gap, float):
        return UNDECIDED
    return max(0.0, 1.0 - gap / STEP_WINDOW)


def _score_dimension(dimension: str, inputs: dict, case: dict, tolerance) -> float:
    if dimension == "size_range":
        return _score_size(inputs, case, tolerance)
    if dimension in ("grey_board_gsm", "face_paper_gsm"):
        return _rel_score(_num(inputs.get(dimension)), _num(case.get(dimension)), tolerance)
    if dimension == "print_colors":
        return _score_print_colors(inputs, case)
    if dimension == "quantity":
        return _score_quantity(inputs.get("quantity"), case.get("quantity_tiers"))
    if dimension in _FLAG_KEYS:
        return _score_flag(inputs, case, dimension)
    return UNDECIDED


def _breakdown(inputs: dict, case: dict, weight_rows) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for row in weight_rows:
        dimension = row["dimension"]
        if not dimension or dimension in out:
            continue
        out[dimension] = float(_score_dimension(dimension, inputs, case, row["tolerance"]))
    return out


def _similarity(breakdown: Dict[str, float], weight_rows) -> float:
    total = sum(float(row["weight"]) for row in weight_rows)
    if total <= 0:
        return 0.0
    got = sum(float(row["weight"]) * float(breakdown.get(row["dimension"], UNDECIDED))
              for row in weight_rows)
    return max(0.0, min(1.0, got / total))


# --------------------------------------------------------------------------- #
# 可解释性：相同项 / 差异项 / 排名理由
# --------------------------------------------------------------------------- #
def _label(key: str) -> str:
    return qq_case.FIELD_LABELS.get(key) or _INPUT_LABELS.get(key) or key


def input_labels(keys) -> Dict[str, str]:
    """键 → 中文标签（Spec 批 11 C4）：**报价侧页面拿标签的唯一出口**。

    事实源仍是 `cpq_quick_quote_case.FIELD_LABELS` + 本模块的 `_INPUT_LABELS` —— 这里不新造表，
    没登记的键原样回键名（**不编中文**），免得页面自带第二份字段表后跟后端各说各话。
    """
    return {str(key): _label(str(key)) for key in (keys or ())}


def _number_text(value) -> str:
    number = _num(value)
    if number is None:
        return ""
    return "%g" % number


def _case_quantity_pair(tiers, quantity):
    """案例侧拿「离需求最近的那个档」当代表值（案例本身是一组档位，不是单个数）。"""
    wanted = _num(quantity)
    qtys = _tier_qtys(tiers)
    if wanted is None or not qtys:
        return None, None
    nearest = min(qtys, key=lambda qty: (abs(qty - wanted), qty))
    return nearest, float(wanted)


def _pair(key: str, inputs: dict, case: dict):
    """(base, current, kind)：base = 案例侧、current = 需求侧。"""
    if key == "box_type":
        return _text(case.get("box_type_code")), _text(inputs.get(key)), "text"
    if key == "quantity":
        base, current = _case_quantity_pair(case.get("quantity_tiers"), inputs.get(key))
        return base, current, "num"
    if key == "print_colors":
        return _print_colors(case.get(key)), _print_colors(inputs.get(key)), "text"
    if key in _FLAG_KEYS:
        return _tri_bool(case.get(key)), _tri_bool(inputs.get(key)), "flag"
    if key in _NUM_KEYS:
        return _num(case.get(key)), _num(inputs.get(key)), "num"
    return _text(case.get(key)), _text(inputs.get(key)), "text"


def _is_blank_pair(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _same_value(kind: str, value):
    if kind == "flag":
        return "是" if value else "否"
    if kind == "num":
        return _num(value)
    return _text(value)


def _delta_text(key: str, kind: str, base, current) -> str:
    label = _label(key)
    if kind == "num":
        before, after = _num(base), _num(current)
        if before is None or after is None:
            return "%s：%s → %s" % (label, _number_text(base), _number_text(current))
        diff = after - before
        return "%s %s → %s（%s%s）" % (label, _number_text(before), _number_text(after),
                                       "+" if diff > 0 else "", _number_text(diff))
    if kind == "flag":
        return "%s：%s → %s" % (label, _same_value(kind, base), _same_value(kind, current))
    return "%s：%s → %s" % (label, _text(base), _text(current))


def _same_and_diff(inputs: dict, case: dict):
    same, diff = [], []
    for key in QUICK_MATCH_INPUT_KEYS:
        base, current, kind = _pair(key, inputs, case)
        if _is_blank_pair(base) or _is_blank_pair(current):
            continue                              # 一边没填：既不算相同也不算差异
        if kind == "num":
            equal = abs(float(base) - float(current)) < 1e-9
        elif kind == "flag":
            equal = bool(base) is bool(current)
        else:
            equal = _text(base).upper() == _text(current).upper()
        if equal:
            same.append({"field": key, "label": _label(key),
                         "value": _same_value(kind, base)})
        else:
            diff.append({"field": key, "label": _label(key),
                         "base": base, "current": current,
                         "delta_text": _delta_text(key, kind, base, current)})
    return same, diff


def _rank_reason(*, status, eligible, reason_code, eligibility_reason,
                 similarity_pct, missing_labels) -> str:
    if not eligible:
        why = _REASON_LABELS.get(reason_code) or _text(reason_code) or "未通过准入"
        return "不可用（%s）：%s；已排在所有可用案例之后。" % (why, eligibility_reason or why)
    if status == "needs_input":
        return "缺输入：还没填 %s，补齐后才能判定是否命中；现在排在命中案例之后。" \
               % ("、".join(missing_labels) if missing_labels else "硬筛选维度")
    tail = "；%s" % eligibility_reason if eligibility_reason else ""
    return "命中盒型 / 盒族 / 闭合方式 / 内托，相似度 %s%%%s" % (_number_text(similarity_pct), tail)


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #
def match_cases(inputs, cases=None, weights=None, *, top_n=None, today=None,
                config=None, include_deal_price=False) -> dict:
    """按需求检索相似标准案例（Spec §2.2 / §2.3）。

    `cases` / `weights` 显式注入 → 完全离线；`None` → 案例读案例表、权重读权重表，
    读不到直接抛错（不回落空库、不回落默认权重）。**不改任何入参**。
    """
    input_row = _input_row(inputs)
    weight_rows = load_weights(weights)
    limit = DEFAULT_TOP_N if top_n is None else max(0, int(top_n))
    missing = _missing_inputs(input_row)
    rows = load_cases(cases, today=today, config=config)

    ranked: List[dict] = []
    rejected: List[dict] = []
    for case in rows:
        verdict = _hard_gate(input_row, case)
        if verdict.get("rejected"):
            rejected.append({"case_code": _text(case.get("case_code")),
                             "reason_code": verdict["rejected"],
                             "reason": verdict["reason"],
                             "detail": verdict.get("detail") or {}})
            continue
        breakdown = _breakdown(input_row, case, weight_rows)
        similarity = _similarity(breakdown, weight_rows)
        eligible = bool(case.get("eligible"))
        status = verdict["status"]
        same, diff = _same_and_diff(input_row, case)
        candidate = {
            "case_code": _text(case.get("case_code")),
            "case_version": case.get("case_version"),
            "status": status,
            "reason_code": verdict["reason_code"],
            "similarity": similarity,
            "similarity_pct": round(similarity * 100.0, 6),
            "score_breakdown": breakdown,
            "standard_price": case.get("standard_price"),
            "currency": case.get("currency"),
            "tax_included": case.get("tax_included"),
            "quote_date": case.get("quote_date"),
            "valid_until": case.get("valid_until"),
            "expired": bool(case.get("expired")),
            "expiring_soon": bool(case.get("expiring_soon")),
            "source_type": case.get("source_type"),
            "review_status": case.get("review_status"),
            "eligible": eligible,
            "eligibility_reason": _text(case.get("reason")),
            "same_items": same,
            "diff_items": diff,
        }
        candidate["rank_reason"] = _rank_reason(
            status=status, eligible=eligible, reason_code=_text(case.get("reason_code")),
            eligibility_reason=candidate["eligibility_reason"],
            similarity_pct=candidate["similarity_pct"],
            missing_labels=[_label(key) for key in verdict.get("missing") or ()])
        if include_deal_price:
            candidate["deal_price"] = case.get("deal_price")
        ranked.append(candidate)

    ranked.sort(key=lambda item: (_STATUS_RANK.get(item["status"], len(_STATUS_RANK)),
                                  0 if item["eligible"] else 1,
                                  -float(item["similarity"]),
                                  item["case_code"]))
    matched = [item for item in ranked if item["status"] == "matched"]
    suggestion = ""
    for item in ranked:
        if item["eligible"]:
            suggestion = item["case_code"]
            break
    return {
        "engine_version": ENGINE_VERSION,
        "industry": INDUSTRY,
        "dimensions": _dimension_view(weight_rows),
        "inputs_complete": not missing,
        "missing_inputs": missing,
        "candidates": ranked[:limit],
        "rejected": rejected,
        "suggested_case_code": suggestion,
        "confirmed_case_code": "",
        "requires_manual_selection": True,
        "few_candidates": len(matched) < MIN_CANDIDATES,
        "no_candidate_reason": "" if ranked else _NO_CANDIDATE_REASON,
        "weights_version": weights_version(weight_rows),
    }


def explain(inputs, case, weights=None) -> dict:
    """单案例打分明细（「为什么像 / 差在哪」），供详情面板用。"""
    input_row = _input_row(inputs)
    weight_rows = load_weights(weights)
    row = normalize_case(case)
    breakdown = _breakdown(input_row, row, weight_rows)
    similarity = _similarity(breakdown, weight_rows)
    verdict = _hard_gate(input_row, row)
    eligible = bool(quote_eligibility(row).get("eligible"))
    status = verdict.get("status") or "rejected"
    same, diff = _same_and_diff(input_row, row)
    pct = round(similarity * 100.0, 6)
    return {
        "engine_version": ENGINE_VERSION,
        "case_code": _text(row.get("case_code")),
        "status": status,
        "reason_code": verdict.get("reason_code") or verdict.get("rejected") or "",
        "similarity": similarity,
        "similarity_pct": pct,
        "score_breakdown": breakdown,
        "same_items": same,
        "diff_items": diff,
        "rank_reason": _rank_reason(status=status, eligible=eligible,
                                    reason_code=verdict.get("rejected") or "",
                                    eligibility_reason="", similarity_pct=pct,
                                    missing_labels=[_label(key) for key in verdict.get("missing") or ()]),
    }


# --------------------------------------------------------------------------- #
# 人工选基准案例
# --------------------------------------------------------------------------- #
def _actor(user) -> dict:
    if not isinstance(user, dict):
        raise QuickQuoteMatchError(
            "选基准案例必须带登录用户（Spec 批 2 §2.4）：没有用户就没有可追溯的选样人",
            "user_required")
    role = _text(user.get("role_code"))
    if role not in cpq_packaging_quote.WRITE_ROLES:
        raise QuickQuoteMatchError(
            "当前角色「%s」不能选基准案例：只有 %s 可以（复用报价写入角色闭集）"
            % (role or "未登录", "、".join(sorted(cpq_packaging_quote.WRITE_ROLES))),
            "role_forbidden")
    return {"user_id": _text(user.get("user_id")), "username": _text(user.get("username")),
            "role_code": role}


def _stamp(today=None) -> str:
    if isinstance(today, dt.datetime):
        return today.isoformat()
    if isinstance(today, dt.date):
        return "%sT00:00:00" % today.isoformat()
    if today is not None:
        text = _text(today)
        if text:
            return text
    return dt.datetime.now().isoformat(timespec="seconds")


def _base_tier(tiers, quantity):
    """基准案例的「该档」：命中就取那一档，否则取最近的档并如实标出。"""
    qtys = _tier_qtys(tiers)
    if not qtys:
        return None, None, False
    wanted = _num(quantity)
    if wanted is None:
        return None, None, False
    for tier in tiers or ():
        if not isinstance(tier, dict):
            continue
        qty = _num(tier.get("qty"))
        if qty is None:
            continue
        if abs(float(qty) - float(wanted)) < 1e-9:
            return float(qty), _num(tier.get("unit_price")), True
    nearest = min(qtys, key=lambda qty: (abs(qty - float(wanted)), qty))
    for tier in tiers or ():
        if isinstance(tier, dict) and _num(tier.get("qty")) == nearest:
            return nearest, _num(tier.get("unit_price")), False
    return nearest, None, False


def build_baseline(inputs, case_code, cases=None, *, user=None, weights=None,
                   today=None, config=None) -> dict:
    """人工选一个基准案例（Spec §2.4）：角色门禁 + 可选性门禁 + 基准价快照。

    **不替用户决定**：`match_cases()` 的 `suggested_case_code` 只是建议，真正落到
    基准案例必须由这里显式选一次，并把选样人 / 时间记进快照。
    """
    actor = _actor(user)
    code = _text(case_code)
    if not code:
        raise QuickQuoteMatchError("必须指定基准案例编号（Spec 批 2 §2.4）", "case_code_required")
    input_row = _input_row(inputs)
    rows = load_cases(cases, today=today, config=config)
    row = next((item for item in rows if _text(item.get("case_code")) == code), None)
    if row is None:
        raise QuickQuoteMatchError("案例库里没有「%s」这个案例：请从候选列表里选一个存在的"
                                   % code, "case_not_found")
    if not row.get("eligible"):
        raise QuickQuoteMatchError("案例「%s」不能作为基准：%s"
                                   % (code, _text(row.get("reason")) or "未通过准入门槛"),
                                   _text(row.get("reason_code")) or "not_eligible")
    quantity = _num(input_row.get("quantity"))
    tier_qty, unit_price, tier_matched = _base_tier(row.get("quantity_tiers"), quantity)
    if tier_qty is None:
        raise QuickQuoteMatchError(
            "案例「%s」没有数量档位单价（quantity_tiers 为空）：先补上档位再作为基准"
            % code, "case_missing_tiers")
    snapshot = {key: copy.deepcopy(row.get(key)) for key in qq_case.CASE_FIELDS}
    return {
        "engine_version": ENGINE_VERSION,
        "case_code": code,
        "case_version": row.get("case_version"),
        "case_snapshot": snapshot,
        "selected_by": actor,
        "selected_at": _stamp(today),
        "base_price": row.get("standard_price"),
        "base_currency": row.get("currency") or qq_case.DEFAULT_CURRENCY,
        "base_tax_included": bool(row.get("tax_included")),
        "base_quantity": quantity,
        "base_tier_qty": tier_qty,
        "base_tier_matched": bool(tier_matched),
        "base_unit_price": unit_price,
        "source_type": row.get("source_type"),
        "review_status": row.get("review_status"),
        "valid_until": row.get("valid_until"),
    }


# --------------------------------------------------------------------------- #
# 权重表灌库（只读→写库的那一步，必须显式调用；幂等）
# --------------------------------------------------------------------------- #
def seed_rows(rows=None) -> List[dict]:
    """权重种子行（含来源 / 版本 / 审核列），纯函数，供 `seed_weights()` 与脚本复用。"""
    source = rows if rows is not None else DEFAULT_WEIGHTS
    out = []
    for row in source:
        item = _weight_row(row)
        item["industry"] = INDUSTRY
        item["source_type"] = "workbook"
        item["source_ref"] = SEED_SOURCE_REF
        item["version"] = SEED_VERSION
        item["review_status"] = "reviewed"
        out.append(item)
    return out


def seed_weights(rows=None, *, conn=None) -> dict:
    """把权重种子幂等地灌进权重表（`ON CONFLICT (dimension) DO UPDATE`）。

    必须显式调用（不跑就没有权重口径，`load_weights(None)` 会明确报错）。单事务：
    建表（幂等）→ upsert → **确有变化才 +1 `kb_version`**（与导入器同一口径，
    否则客户端拿 `since=` 拉快照会永远看不到新权重）。返回
    `{"ok", "table", "rows", "changed", "kb_version"}`；失败抛 `cpq_kb.KbUnavailable`。
    """
    items = seed_rows(rows)
    own = conn is None
    if own:
        conn = cpq_kb._connect()
    try:
        if own:
            with conn.transaction():
                cur = conn.cursor()
                cpq_kb._ensure_schema(cur)               # 幂等：第 31 张表跟着一起建
                changed = cpq_kb._upsert_rows(cur, WEIGHT_TABLE, items)
                version = cpq_kb._bump_version(cur, changed)
        else:
            cur = conn.cursor()
            changed = cpq_kb._upsert_rows(cur, WEIGHT_TABLE, items)
            version = cpq_kb._bump_version(cur, changed)
    except Exception as exc:                                   # noqa: BLE001 - 统一收敛
        raise cpq_kb.KbUnavailable("权重表灌库失败（%s.%s）：%s"
                                   % (cpq_kb.SCHEMA, WEIGHT_TABLE,
                                      str(exc).splitlines()[0][:200])) from exc
    finally:
        if own:
            conn.close()
    return {"ok": True, "table": WEIGHT_TABLE, "rows": len(items),
            "changed": int(changed), "kb_version": int(version)}


if __name__ == "__main__":                                  # pragma: no cover - 手工跑
    print("weight rows =", len(DEFAULT_WEIGHTS), "dimensions =", len(DIMENSIONS))

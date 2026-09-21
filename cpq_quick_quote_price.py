"""逆向快速报价 第 4 批：快速报价生成、风险提示与转精准报价。

Spec：`docs/specs/quick-quote-4-quick-quote-and-handoff.md`
红测：`tests/test_quick_quote_generation_red.py`
依赖：批 1（案例模型）、批 2（候选 + `build_baseline()`）、批 3（字段工作区 + 差异价）。

一句话口径：**快速报价 = 基准案例价 + 逐项差异**，但只有过了适用门槛才出价；
过不了就只给一句可执行的话（转精准报价），并由 `transfer_to_precise()` 把**已填好的
字段值整包**交给既有「转技术工艺」通道（`cpq_wf.TASK_KIND_TECH_NEW`）——不新增交接口径。

三条硬纪律（红测各有护栏）：

  · 差异口径只有一份：逐项金额一律来自批 3 的 `diff_table()`，本模块不重算、不编价；
  · 没有价格依据的差异（`priced=False`）**不得当成 0**：要扩大预估偏差区间，
    并在 `warnings` 里点名是哪个字段；
  · 本模块不进入技术工艺：不 import 技术工艺服务模块、不直连技术工艺接口、不新建表
    （落库复用报价卡片第 2 步快照）。
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from typing import Any, Dict, List, Optional

import cpq_packaging_quote
import cpq_quick_quote_case as qq_case
import cpq_quick_quote_workspace as qq_ws
import cpq_wf

ENGINE_VERSION = "quick_quote_v1"
INDUSTRY = "packaging"
QUOTE_KEY = "quick_quote_price"      # 卡片第 2 步快照里的段名
QUOTE_STEP = 2

#: 适用门槛（顺序即展示顺序，Spec §2.3）。
GATE_KEYS = ("case_reviewed", "case_not_expired", "box_compatible",
             "size_within_threshold", "quantity_in_range", "no_unknown_process")
GATE_LABELS = {
    "case_reviewed": "命中已审核案例",
    "case_not_expired": "案例报价未过期",
    "box_compatible": "盒型与结构未改",
    "size_within_threshold": "尺寸差异在容差内",
    "quantity_in_range": "数量在适用区间",
    "no_unknown_process": "新增工艺都有标准依据",
}
ADVICE_TRANSFER = "当前需求与标准案例差异较大，快速报价可能失真，建议转精准报价。"

#: 转精准报价的必填字段（缺一不可，缺了不许带残缺数据过去，Spec §2.6）。
TRANSFER_REQUIRED_FIELDS = ("box_type", "closure_type", "inner_length", "inner_width",
                            "inner_height", "face_paper_gsm", "grey_board_gsm",
                            "print_colors", "quantity")
TARGET_PRECISE = "precise_quote"

#: 落库 / 转交角色与税率一律复用既有闭集，不新造（Spec §2.1）。
WRITE_ROLES = cpq_packaging_quote.WRITE_ROLES
DEFAULT_TAX_RATE = cpq_packaging_quote.DEFAULT_TAX_RATE
DEFAULT_CURRENCY = qq_case.DEFAULT_CURRENCY

#: 尺寸门槛看这三边（Spec §2.3 第 4 行）。
SIZE_FIELDS = ("inner_length", "inner_width", "inner_height")

#: 结构 / 工艺字段闭集（Spec §2.3 第 6 行）：相对基准**新增**但没有差异价规则的项，
#: 必须拦下来转精准 —— 「按老案例算一个价」比「说不知道」危险得多。
UNKNOWN_PROCESS_FIELDS = ("lamination", "hot_stamping", "v_groove", "magnet",
                          "window", "ribbon", "insert_type")

#: 本批给 `default_config()` 补的键（Spec §2.2）。只加键，不动表结构。
GATE_CONFIG_KEYS = ("size_diff_threshold", "quantity_min", "quantity_max",
                    "base_deviation_pct", "per_miss_deviation_pct",
                    "max_deviation_pct", "tax_rate")

_text = qq_case._text
_num = qq_case._num
_date = qq_ws._date
_number_text = qq_ws._number_text


class QuickQuoteError(Exception):
    """带用户可见文案的业务错误（可选 HTTP 状态码与稳定码）。"""

    def __init__(self, message: str, status_code: int = 400, code: str = ""):
        super().__init__(message)
        self.message = str(message)
        self.status_code = int(status_code)
        self.code = code or ""


class QuickQuoteBlocked(QuickQuoteError):
    """门槛未过：`.result` 里带门槛结果、拦下哪几项与转精准建议（Spec §2.4）。"""

    def __init__(self, message: str, *, result: Optional[dict] = None):
        super().__init__(message, 409, "quick_quote_blocked")
        self.result = dict(result or {})


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def gate_config(config=None) -> dict:
    """门槛/偏差口径：与 `cpq_quick_quote_case.default_config()` **同源**，只做覆盖合并。"""
    merged = qq_case.default_config()
    if isinstance(config, dict):
        for key, value in config.items():
            if value is not None:
                merged[key] = copy.deepcopy(value)
    return merged


def _warn_days() -> int:
    return int(qq_case.default_config().get("expiry_warn_days") or 0)


# --------------------------------------------------------------------------- #
# 门槛
# --------------------------------------------------------------------------- #
def _case_of(baseline) -> dict:
    case = (baseline or {}).get("case_snapshot")
    return dict(case) if isinstance(case, dict) else {}


def _base_of(workspace, case) -> dict:
    base = (workspace or {}).get("base_values")
    if isinstance(base, dict) and base:
        return dict(base)
    return dict(case)


def _current_of(workspace) -> dict:
    current = (workspace or {}).get("current")
    return dict(current) if isinstance(current, dict) else {}


def _rows_of(workspace, rules):
    return qq_ws.diff_table(workspace, rules=rules)


def _is_new_relative_to_base(row) -> bool:
    """「相对基准**新增**」：布尔字段 False/空 → True；文本字段 空 → 非空。

    删掉（True → False）不算新增 —— 这类差异没有价格依据时只扩大偏差区间（Spec §2.4），
    因为「不做了」本来也不会被按老案例多算钱。
    """
    base_value = row.get("base_value")
    current_value = row.get("current_value")
    if isinstance(base_value, bool) or isinstance(current_value, bool):
        return current_value is True and base_value is not True
    return (not _text(base_value)) and bool(_text(current_value))


def _gate_item(key, passed, detail) -> dict:
    return {"key": key, "label": GATE_LABELS.get(key, key),
            "passed": bool(passed), "detail": str(detail or "")}


def gate_check(baseline, workspace, *, today=None, config=None, rules=None) -> dict:
    """六项适用门槛（Spec §2.3）：全过才允许出快速报价。"""
    cfg = gate_config(config)
    case = _case_of(baseline)
    base = _base_of(workspace, case)
    current = _current_of(workspace)
    rows = _rows_of(workspace, rules)
    day = _date(today) or dt.date.today()
    items: List[dict] = []

    # 1) 案例必须已审核（案例快照优先：它才是这条基准的出处）
    review = _text(case.get("review_status")) or _text((baseline or {}).get("review_status"))
    items.append(_gate_item("case_reviewed", review == "reviewed",
                            "基准案例已审核（review_status=reviewed）" if review == "reviewed"
                            else "基准案例未审核（review_status=%s）：未审核案例不得用于快速报价"
                                 % (review or "空")))

    # 2) 有效期
    until = _text((baseline or {}).get("valid_until")) or _text(case.get("valid_until"))
    until_day = _date(until)
    if until_day is None:
        expired = False
        expiry_detail = "基准案例未标有效期：无法判断是否过期，请人工确认"
    else:
        expired = day > until_day
        expiry_detail = ("基准案例报价已过期（有效期至 %s）：需重新询价" % until if expired
                         else "基准案例报价有效期至 %s" % until)
    items.append(_gate_item("case_not_expired", not expired, expiry_detail))

    # 3) 盒型 / 结构不得被改（改了就必须重新检索候选）
    cur_box = _text(current.get("box_type"))
    cur_closure = _text(current.get("closure_type"))
    base_box = _text(case.get("box_type_code")) or _text(base.get("box_type"))
    base_closure = _text(case.get("closure_type")) or _text(base.get("closure_type"))
    box_ok = (cur_box == base_box) and (cur_closure == base_closure)
    items.append(_gate_item("box_compatible", box_ok,
                            "盒型与结构未改（%s / %s）" % (base_box or "空", base_closure or "空")
                            if box_ok else
                            "盒型或结构已改（当前 %s / %s，基准 %s / %s）：请重新检索候选案例，"
                            "不许改了盒型还按老案例算"
                            % (cur_box or "空", cur_closure or "空", base_box or "空", base_closure or "空")))

    # 4) 三边尺寸容差
    threshold = float(cfg["size_diff_threshold"])
    worst_field, worst_rel = "", 0.0
    for field in SIZE_FIELDS:
        base_value, current_value = _num(base.get(field)), _num(current.get(field))
        if base_value in (None, 0) or current_value is None:
            continue
        rel = abs(current_value - base_value) / abs(base_value)
        if rel > worst_rel:
            worst_field, worst_rel = field, rel
    size_diff = {"max_rel": round(worst_rel, 6), "threshold": threshold}
    size_ok = worst_rel <= threshold
    if size_ok:
        size_detail = "三边尺寸相对差 %s%% 在容差 %s%% 内" % (
            _number_text(round(worst_rel * 100, 4)), _number_text(round(threshold * 100, 4)))
    else:
        size_detail = "%s %s → %s（相对差 %s%% > 上限 %s%%）：尺寸差异过大，建议转精准报价" % (
            qq_ws.FIELD_SPECS.get(worst_field, {}).get("label", worst_field),
            _number_text(_num(base.get(worst_field))), _number_text(_num(current.get(worst_field))),
            _number_text(round(worst_rel * 100, 4)), _number_text(round(threshold * 100, 4)))
    items.append(_gate_item("size_within_threshold", size_ok, size_detail))

    # 5) 数量区间
    quantity = _num(current.get("quantity"))
    low, high = cfg["quantity_min"], cfg["quantity_max"]
    qty_ok = quantity is not None and float(low) <= quantity <= float(high)
    items.append(_gate_item("quantity_in_range", qty_ok,
                            "数量 %s 个在适用区间 %s – %s 个内" % (
                                _number_text(quantity), _number_text(low), _number_text(high))
                            if qty_ok else
                            "适用数量区间 %s – %s 个，当前 %s 个：超出区间请转精准报价" % (
                                _number_text(low), _number_text(high), _number_text(quantity))))

    # 6) 新增工艺必须有标准依据
    unknown = [row["field_key"] for row in rows
               if row.get("field_key") in UNKNOWN_PROCESS_FIELDS
               and not row.get("priced") and _is_new_relative_to_base(row)]
    labels = "、".join("%s（%s）" % (qq_ws.FIELD_SPECS.get(f, {}).get("label", f), f) for f in unknown)
    items.append(_gate_item("no_unknown_process", not unknown,
                            "新增工艺都有标准差异价依据" if not unknown else
                            "以下新增工艺没有标准依据：%s：没有依据不许按老案例算，必须转精准报价" % labels))

    blocking = [item["key"] for item in items if not item["passed"]]
    return {"passed": not blocking,
            "items": [item for key in GATE_KEYS for item in items if item["key"] == key],
            "blocking": blocking,
            "advice": ADVICE_TRANSFER if blocking else "",
            "size_diff": size_diff,
            "unknown_process_fields": unknown}


# --------------------------------------------------------------------------- #
# 出价
# --------------------------------------------------------------------------- #
def _requirement_of(current) -> dict:
    return {key: copy.deepcopy(current.get(key)) for key in qq_ws.FIELD_KEYS if key in current}


def _fingerprint_payload(quote) -> dict:
    q = quote or {}
    requirement = q.get("requirement")
    requirement = requirement if isinstance(requirement, dict) else {}
    return {"engine_version": ENGINE_VERSION, "industry": INDUSTRY,
            "case_code": _text(q.get("case_code")),
            "case_version": int(_num(q.get("case_version")) or 0),
            "requirement": {str(k): requirement[k] for k in sorted(requirement)},
            "rule_versions": [str(v) for v in (q.get("rule_versions") or [])]}


def quote_fingerprint(quote) -> str:
    """版本幂等键（Spec §2.5）：基准案例 + 案例版本 + 当前字段值 + 规则版本。"""
    blob = json.dumps(_fingerprint_payload(quote), ensure_ascii=False,
                      sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _basis_lines(baseline, case, rows) -> List[str]:
    case_code = _text(baseline.get("case_code")) or _text(case.get("case_code"))
    version = _num(baseline.get("case_version"))
    price = _num(baseline.get("base_unit_price"))
    if price is None:
        price = _num(baseline.get("base_price"))
    lines = ["基准案例 %s（案例版本 %s）：基准价 %s 元/件（%s）" % (
        case_code or "未标", _number_text(version), "%.2f" % price if price is not None else "未标",
        "含税" if baseline.get("tax_included") else "不含税")]
    quote_date = _text(case.get("quote_date"))
    if quote_date:
        lines.append("基准案例成交日期 %s，有效期至 %s" % (quote_date, _text(baseline.get("valid_until")) or "未标"))
    for row in rows:
        label = qq_ws.FIELD_SPECS.get(row.get("field_key"), {}).get("label") or row.get("field_key")
        if row.get("priced"):
            lines.append("%s %s → %s：%s（规则 %s v%s）" % (
                label, row.get("display_base"), row.get("display_current"),
                row.get("delta_text"), row.get("rule_code"), row.get("rule_version")))
        else:
            lines.append("%s %s → %s：无价格依据（%s），不计入差异合计，已放宽预估偏差"
                         % (label, row.get("display_base"), row.get("display_current"),
                            row.get("note") or "该字段不单独计差"))
    return lines


def price(baseline, workspace, *, today=None, config=None, rules=None) -> dict:
    """组装快速报价（Spec §2.4）。门槛未过直接抛 `QuickQuoteBlocked`，**不先算个价再提示**。"""
    cfg = gate_config(config)
    gate = gate_check(baseline, workspace, today=today, config=config, rules=rules)
    if not gate["passed"]:
        raise QuickQuoteBlocked(ADVICE_TRANSFER, result=gate)

    baseline = copy.deepcopy(baseline or {})
    case = _case_of(baseline)
    current = _current_of(workspace)
    rows = _rows_of(workspace, rules=rules)

    base_unit_price = _num(baseline.get("base_unit_price"))
    if base_unit_price is None:
        base_unit_price = _num(baseline.get("base_price"))
    if base_unit_price is None:
        raise QuickQuoteError("基准案例没有基准单价，无法生成快速报价")

    delta_total = 0.0
    rule_versions: List[str] = []
    for row in rows:
        delta_total += float(_num(row.get("delta")) or 0.0)
        if row.get("priced") and row.get("rule_code"):
            rule_versions.append("%s:v%s" % (row["rule_code"], _number_text(row.get("rule_version"))))
    unit_price = base_unit_price + delta_total

    tax_included = bool(baseline.get("tax_included")) or bool(case.get("tax_included"))
    tax_rate = float(cfg["tax_rate"])
    unit_price_taxed = unit_price if tax_included else unit_price * (1.0 + tax_rate)

    unpriced = [row for row in rows if not row.get("priced")]
    est_pct = min(float(cfg["max_deviation_pct"]),
                  float(cfg["base_deviation_pct"])
                  + float(cfg["per_miss_deviation_pct"]) * len(unpriced))
    est_pct = round(est_pct, 6)
    est_amount = unit_price * est_pct
    price_range = {"low": unit_price * (1.0 - est_pct), "high": unit_price * (1.0 + est_pct)}

    warnings: List[str] = []
    for row in unpriced:
        label = qq_ws.FIELD_SPECS.get(row.get("field_key"), {}).get("label") or row.get("field_key")
        warnings.append("「%s」没有差异价规则（%s → %s）：金额不计入差异合计，"
                        "已按每项 %s%% 放宽预估偏差，请人工复核"
                        % (label, row.get("display_base"), row.get("display_current"),
                           _number_text(round(float(cfg["per_miss_deviation_pct"]) * 100, 4))))
    valid_until = _text(baseline.get("valid_until")) or _text(case.get("valid_until"))
    until_day = _date(valid_until)
    if until_day is not None:
        left = (until_day - (_date(today) or dt.date.today())).days
        if 0 <= left <= _warn_days():
            warnings.append("基准案例报价 %s 后到期（有效期至 %s）：请尽快确认，"
                            "过期后要重新询价" % (left, valid_until))

    # 费率权威段（Spec 批 8 §2.2）："这价是按什么费率算的"必须是一等字段，不能只埋在 note 文本里。
    rate_authority = qq_ws.authority_summary(rules=rules)
    if not rate_authority.get("authoritative"):
        labels = [str(row.get("label") or row.get("reason_code") or "")
                  for row in (rate_authority.get("blocked_by") or [])]
        kind = "演示数据" if any("演示" in label for label in labels) else "非权威费率"
        warnings.append("费率里有%s：这份报价只能作为流程试算，出价前必须换成权威工作簿费率 —— %s"
                        % (kind, "；".join(label for label in labels if label)
                           or str(rate_authority.get("headline") or "")))

    case_code = _text(baseline.get("case_code")) or _text(case.get("case_code"))
    risk_notice = ("本报价按标准案例 %s 推导（有效期至 %s，成交日期 %s），预估偏差约 %s%%；"
                   "建议按 %s – %s 元的区间报价并注明依据，客户确认后转精准报价复核。"
                   % (case_code or "未标", valid_until or "未标", _text(case.get("quote_date")) or "未标",
                      _number_text(round(est_pct * 100, 4)),
                      "%.4f" % price_range["low"], "%.4f" % price_range["high"]))

    basis = _basis_lines(baseline, case, rows)
    if rule_versions:
        basis.append("用到的差异价规则版本：%s" % "、".join(sorted(set(rule_versions))))

    quote = {
        "engine_version": ENGINE_VERSION,
        "industry": INDUSTRY,
        "quick_quote_id": "",
        "version_no": 1,
        "case_code": case_code,
        "case_version": int(_num(baseline.get("case_version")) or 0),
        "base_price": base_unit_price,
        "base_unit_price": base_unit_price,
        "base_currency": _text(baseline.get("base_currency")) or _text(case.get("currency")) or DEFAULT_CURRENCY,
        "tax_included": tax_included,
        "tax_rate": tax_rate,
        "delta_items": copy.deepcopy(rows),
        "delta_total": delta_total,
        "unit_price": unit_price,
        "unit_price_taxed": unit_price_taxed,
        "price_range": price_range,
        "deviation": {"est_pct": est_pct, "est_amount": est_amount,
                      "basis": "基准偏差 %s%% + 无规则差异项 %d 项 × %s%%（上限 %s%%）" % (
                          _number_text(round(float(cfg["base_deviation_pct"]) * 100, 4)), len(unpriced),
                          _number_text(round(float(cfg["per_miss_deviation_pct"]) * 100, 4)),
                          _number_text(round(float(cfg["max_deviation_pct"]) * 100, 4)))},
        "valid_until": valid_until,
        "rule_versions": sorted(set(rule_versions)),
        "gate": gate,
        "rate_authority": copy.deepcopy(rate_authority),
        "warnings": warnings,
        "risk_notice": risk_notice,
        "requirement": _requirement_of(current),
        "transfer_available": True,
        "basis": basis,
        "baseline_case": copy.deepcopy(baseline),
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    quote["quick_quote_id"] = quote_fingerprint(quote)[:16]
    return quote


# --------------------------------------------------------------------------- #
# 落库（复用卡片第 2 步快照，不新建表）
# --------------------------------------------------------------------------- #
def _require_writer(user, action: str) -> str:
    role = _text((user or {}).get("role_code"))
    if not role or role not in WRITE_ROLES:
        raise QuickQuoteError("只有销售经理或管理员能%s（当前角色：%s）"
                              % (action, role or "未登录"), 403, "role_not_allowed")
    return role


def is_formal(quote) -> bool:
    """这份报价能不能当**正式报价**（Spec 批 8 §2.2）。

    三条同时成立才算：费率全权威、`authoritative_total > 0`、没有任何费率告警。
    演示费率的价永远只能算试算 —— 不给"看起来像正式版"的机会。
    """
    q = quote if isinstance(quote, dict) else {}
    authority = q.get("rate_authority") if isinstance(q.get("rate_authority"), dict) else {}
    if not authority.get("authoritative"):
        return False
    try:
        total = int(authority.get("authoritative_total") or 0)
    except (TypeError, ValueError):
        return False
    if total <= 0:
        return False
    for item in (q.get("warnings") or []):
        text = _text(item)
        if "费率" in text and ("演示" in text or "非权威" in text):
            return False
    return True


def save(quote, *, user=None, session_id="", previous=None, formal=False) -> dict:
    """把快速报价写进卡片第 2 步快照的 `quick_quote_price` 段（Spec §2.5）。

    `previous` 传上一版（同一 `quick_quote_id`）时版本号递增；不读库，版本口径由调用方给。

    `formal=False`（缺省）落的是**试算**：快照里带 `rate_authority` 与 `formal=False`，
    事后能看出这价是按演示费率算的；`formal=True` 且费率不权威 → 抛错且**不落库**
    （Spec 批 8 §2.2：不允许把演示费率的价落成正式报价版本）。
    """
    _require_writer(user, "落快速报价版本")
    session_id = _text(session_id)
    if not session_id:
        raise QuickQuoteError("缺少报价会话，请先保存报价卡片")
    if formal and not is_formal(quote):
        authority = (quote or {}).get("rate_authority") if isinstance(quote, dict) else None
        authority = authority if isinstance(authority, dict) else {}
        reasons = "；".join(str(row.get("label") or row.get("reason_code") or "")
                           for row in (authority.get("blocked_by") or []))
        raise QuickQuoteError(
            "这份报价用的费率不是权威费率，不能作为正式报价落库：%s。"
            "请先把 kb_quick_quote_delta_rule 换成权威工作簿费率（source_type=workbook + "
            "review_status=reviewed），或改存试算（formal=False）"
            % (reasons or str(authority.get("headline") or "费率不是权威来源")),
            409, "rate_not_authoritative")
    payload = copy.deepcopy(quote or {})
    if not payload.get("quick_quote_id"):
        payload["quick_quote_id"] = quote_fingerprint(payload)[:16]
    prev = previous if isinstance(previous, dict) else {}
    same = _text(prev.get("quick_quote_id")) == _text(payload.get("quick_quote_id"))
    payload["version_no"] = int(prev.get("version_no") or 0) + 1 if same else int(payload.get("version_no") or 1)
    payload["saved_by"] = {"user_id": _text((user or {}).get("user_id")),
                           "username": _text((user or {}).get("username")),
                           "role_code": _text((user or {}).get("role_code"))}
    payload["rate_authority"] = copy.deepcopy(payload.get("rate_authority") or {})
    payload["formal"] = bool(formal)
    payload["saved_at"] = dt.datetime.now().isoformat(timespec="seconds")
    merged = cpq_wf.merge_step_snapshot(session_id, QUOTE_STEP, {QUOTE_KEY: payload}) or {}
    return {"ok": True, "quick_quote_id": payload["quick_quote_id"],
            "version_no": payload["version_no"], "session_id": session_id,
            "segment": QUOTE_KEY, "snapshot": merged}


def find_quote(quote_id="", *, session_id="") -> dict:
    """读回卡片第 2 步快照里的快速报价段；没有就回 `{}`（不编造，Spec §2.5）。"""
    session_id = _text(session_id)
    if not session_id:
        return {}
    snapshot = cpq_wf.step_snapshot(session_id, QUOTE_STEP) or {}
    seg = snapshot.get(QUOTE_KEY) if isinstance(snapshot, dict) else None
    if not isinstance(seg, dict) or not seg:
        return {}
    wanted = _text(quote_id)
    if wanted and _text(seg.get("quick_quote_id")) != wanted:
        return {}
    return copy.deepcopy(seg)


# --------------------------------------------------------------------------- #
# 一键转精准报价
# --------------------------------------------------------------------------- #
def transfer_to_precise(quote, *, user=None, session_id="") -> dict:
    """把快速报价整包交给既有「转技术工艺」通道（Spec §2.6）。

    只产出交接包并派发任务，**不进入技术工艺实现**：不直接调技术工艺服务、
    不新增 handoff kind（`cpq_tech_bridge.HANDOFF_KINDS` 一个字不动）。
    """
    _require_writer(user, "转精准报价")
    session_id = _text(session_id)
    if not session_id:
        raise QuickQuoteError("缺少报价会话，请先保存报价卡片")
    quote = quote or {}
    gate = quote.get("gate") if isinstance(quote.get("gate"), dict) else {}
    if not gate.get("passed"):
        raise QuickQuoteError("这条快速报价没有通过适用门槛，不允许转精准：%s"
                              % (gate.get("advice") or ADVICE_TRANSFER), 409, "quick_quote_blocked")
    requirement = copy.deepcopy(quote.get("requirement") or {})
    missing = [field for field in TRANSFER_REQUIRED_FIELDS
               if not _text(requirement.get(field)) and not isinstance(requirement.get(field), bool)]
    if missing:
        raise QuickQuoteError("转精准报价前必须补齐这些字段：%s（带过去的数据必须是完整的，"
                              "否则技术工艺侧要重新问一遍）" % "、".join(missing), 400,
                              "quick_quote_incomplete")
    payload = {
        "quick_quote": copy.deepcopy(quote),
        "requirement": requirement,
        "baseline_case": copy.deepcopy(quote.get("baseline_case") or {}),
        "delta_items": copy.deepcopy(quote.get("delta_items") or []),
        "price": {"base_unit_price": quote.get("base_unit_price"),
                  "delta_total": quote.get("delta_total"),
                  "unit_price": quote.get("unit_price"),
                  "unit_price_taxed": quote.get("unit_price_taxed"),
                  "currency": quote.get("base_currency"),
                  "tax_included": quote.get("tax_included")},
    }
    task = cpq_wf.send_task(session_id, user, task_kind=cpq_wf.TASK_KIND_TECH_NEW,
                            note="快速报价转精准报价：请按已填字段复核并出正式报价",
                            payload=payload) or {}
    return {"engine_version": ENGINE_VERSION,
            "transfer_id": "QT-" + quote_fingerprint(quote)[:12],
            "task_kind": cpq_wf.TASK_KIND_TECH_NEW,
            "task_id": _text(task.get("task_id")),
            "target": TARGET_PRECISE,
            "payload": payload,
            "input_fields_complete": True,
            "missing_inputs": [],
            "created_at": dt.datetime.now().isoformat(timespec="seconds")}


if __name__ == "__main__":                                   # pragma: no cover - 手工跑
    print("gates =", len(GATE_KEYS), "| required fields =", len(TRANSFER_REQUIRED_FIELDS),
          "| advise =", ADVICE_TRANSFER)

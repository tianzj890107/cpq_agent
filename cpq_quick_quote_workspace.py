"""逆向快速报价 第 3 批：字段工作区修改与差异价格计算。

Spec：`docs/specs/quick-quote-3-field-workspace-and-delta-price.md`
红测：`tests/test_quick_quote_field_workspace_red.py`

分工（本批的核心，Spec §0）：

  · **左侧 Agent** = 自然语言入口：理解需求、解释候选、把「把数量改成 3000」这类口语
    修改变成**待确认（pending）**建议；
  · **右侧工作区** = 权威编辑与确认入口：展示、编辑、校验、确认、保存结构化参数。

Agent 的修改**不直接生效**：只有右侧逐项确认后才进 `current`。本批不出最终报价，
只算「每项的差异价」与差异合计（批 4 才组装快速报价、门槛与转精准）。

三条硬纪律（红测各有护栏）：

  · 差异价规则一律来自 `kb_quick_quote_delta_rule`：代码里没有费率/金额常量，读不到或表为空
    直接抛 `CaseLibraryUnavailable`（**不编价**）；没有规则的字段 `priced=False` 并说明原因；
  · 未确认不得落库：`pending` 非空时 `save()` 抛错；落库走既有报价卡片第 2 步快照，**不新建表**；
  · Agent 入口不得出价格：`agent_patch()` 只改 pending，返回体里没有任何价格/金额。
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import math
from typing import Any, Dict, List, Optional

import cpq_kb
import cpq_packaging_quote
import cpq_quick_quote_case as qq_case
import cpq_wf
from cpq_quick_quote_case import CaseLibraryUnavailable   # noqa: F401 - 复用批 1 异常类（红测 A4）

ENGINE_VERSION = "quick_quote_workspace_v1"
INDUSTRY = "packaging"
SNAPSHOT_STEP_NO = 2                    # 快速报价工作区挂在报价卡片第 2 步快照（Spec §2.5）
SNAPSHOT_SECTION = "quick_quote"

#: 可编辑字段闭集（顺序即工作区展示顺序）。每个字段都能在批 1 的案例模型里找到同名字段。
FIELD_KEYS = ("box_type", "box_family", "closure_type", "insert_type",
              "inner_length", "inner_width", "inner_height", "fit_clearance",
              "grey_board_gsm", "face_paper_gsm", "material_code",
              "print_colors", "lamination", "hot_stamping", "v_groove", "magnet",
              "window", "ribbon", "quantity", "tooling_fee_amount", "freight_amount")

FIELD_GROUPS = ("尺寸", "材料", "结构", "表面工艺", "印刷", "数量", "费用")

#: 差异价规则表（知识库侧 cpq_kb schema）。读不到或表为空 → 抛错，不编价。
DELTA_RULE_TABLE = "kb_quick_quote_delta_rule"
DELTA_RULE_KEYS = ("rule_code",)
RULE_KINDS = ("rate", "step", "band", "direct")
EDIT_SOURCES = ("workspace", "agent")
WRITE_ROLES = cpq_packaging_quote.WRITE_ROLES     # 复用既有闭集，不新造（Spec §2.5）

#: 字段规格（唯一事实源）：右侧工作区靠它渲染控件、做单位与范围校验。
#: 范围口径见 Spec §2.1；`fit_clearance` 用的是**配合间隙**的量级（0–20mm），
#: 不是内尺寸的 20–2000mm —— 见本批 changelog「实现时发现并回写 Spec 的两处口径」。
_SPEC_ROWS = (
    # key, 中文标签, 分组, 类型, 单位, 下限, 上限, 可选值
    ("box_type", "盒型编码", "结构", "text", "", None, None, ()),
    ("box_family", "盒族", "结构", "text", "", None, None, ()),
    ("closure_type", "闭合方式", "结构", "text", "", None, None, ()),
    ("insert_type", "内托", "结构", "text", "", None, None, ()),
    ("inner_length", "内长", "尺寸", "num", "mm", 20.0, 2000.0, ()),
    ("inner_width", "内宽", "尺寸", "num", "mm", 20.0, 2000.0, ()),
    ("inner_height", "内高", "尺寸", "num", "mm", 20.0, 2000.0, ()),
    ("fit_clearance", "配合间隙", "尺寸", "num", "mm", 0.0, 20.0, ()),
    ("grey_board_gsm", "灰板克重", "材料", "num", "g/m²", 400.0, 3000.0, ()),
    ("face_paper_gsm", "面纸克重", "材料", "num", "g/m²", 60.0, 400.0, ()),
    ("material_code", "材料编码", "材料", "text", "", None, None, ()),
    ("print_colors", "印刷色数", "印刷", "enum", "", None, None,
     ("", "CMYK", "专色", "CMYK+专色")),
    ("lamination", "覆膜", "表面工艺", "bool", "", None, None, ()),
    ("hot_stamping", "烫金", "表面工艺", "bool", "", None, None, ()),
    ("v_groove", "V槽", "表面工艺", "bool", "", None, None, ()),
    ("magnet", "磁铁", "表面工艺", "bool", "", None, None, ()),
    ("window", "开窗", "结构", "bool", "", None, None, ()),
    ("ribbon", "丝带", "结构", "bool", "", None, None, ()),
    ("quantity", "数量", "数量", "num", "个", 1.0, 1000000.0, ()),
    ("tooling_fee_amount", "模具费", "费用", "num", "元", 0.0, 1000000.0, ()),
    ("freight_amount", "运输费", "费用", "num", "元", 0.0, 1000000.0, ()),
)

FIELD_SPECS = {
    key: {"label": label, "group": group, "value_type": value_type, "unit": unit,
          "min": low, "max": high, "choices": tuple(choices)}
    for key, label, group, value_type, unit, low, high, choices in _SPEC_ROWS
}

#: 金额单位（差异价一律以元计）。
MONEY_UNIT = "元"

#: Agent 字段别名（Spec §2.4）：只覆盖**结构性参数**，刻意不含「盒型」——
#: 「把盒型改成超级礼盒」这类说法必须在 unresolved 里回问，不能猜一个盒型编码出来。
_AGENT_ALIASES = (
    ("quantity", ("订购数量", "数量", "订购量", "订单量")),
    ("face_paper_gsm", ("面纸克重", "面纸")),
    ("grey_board_gsm", ("灰板克重", "灰板")),
    ("inner_length", ("内长", "内尺寸长")),
    ("inner_width", ("内宽",)),
    ("inner_height", ("内高",)),
    ("hot_stamping", ("烫金",)),
    ("lamination", ("覆膜",)),
    ("v_groove", ("v槽", "v型槽")),
    ("magnet", ("磁铁",)),
    ("window", ("开窗",)),
    ("insert_type", ("内托",)),
    ("tooling_fee_amount", ("模具费", "开模费")),
    ("freight_amount", ("运输费", "运费", "运输")),
)

#: 数量单位词（用于判断「面纸改成 250 个」这类单位不匹配）。
_UNIT_WORDS = (("个", "个"), ("mm", "mm"), ("毫米", "mm"), ("g/m²", "g/m²"),
               ("g/m2", "g/m²"), ("克", "g/m²"), ("元", "元"))

# 复用同一份实现，不各写一份。
_text = qq_case._text
_num = qq_case._num
_tri_bool = qq_case._bool
_print_colors = qq_case.normalize_print_colors


class WorkspaceError(Exception):
    """带用户可见文案的业务错误；字段级错误放 `.field_errors`。"""

    def __init__(self, message, field_errors=None):
        super().__init__(message)
        self.field_errors = list(field_errors or [])


# --------------------------------------------------------------------------- #
# 差异价规则：只从表来
# --------------------------------------------------------------------------- #
def _rule_row(row) -> dict:
    """规则行归一（只读，不改入参）。数字字段拿不到数字就留 None，**不猜 0**。"""
    out = {}
    for key, value in row.items():
        out[key] = copy.deepcopy(value)
    for key in ("rate", "amount", "step_size"):
        out[key] = _num(row.get(key))
    out["rule_code"] = _text(row.get("rule_code"))
    out["field_key"] = _text(row.get("field_key"))
    out["rule_kind"] = _text(row.get("rule_kind")).lower()
    out["unit"] = _text(row.get("unit"))
    out["industry"] = _text(row.get("industry"))
    out["source_type"] = _text(row.get("source_type"))
    out["review_status"] = _text(row.get("review_status"))
    return out


def load_rules(rules=None) -> List[dict]:
    """差异价规则：显式传入只用传入行；`None` → 读规则表。

    读不到（库不可用 / 表没建）或表为空都抛 `CaseLibraryUnavailable` —— 「没人配费率」
    必须看得见，绝不回落成代码里的默认费率（否则等于编价）。
    """
    if rules is not None:
        rows = [_rule_row(row) for row in rules if isinstance(row, dict)]
        if not rows:
            raise WorkspaceError("差异价规则为空：至少要有一条规则才能算差异价（Spec 批 3 §2.2）")
        return rows
    try:
        snapshot = cpq_kb.snapshot()
        raw = (snapshot.get("tables") or {}).get(DELTA_RULE_TABLE) or []
    except Exception as exc:                                   # noqa: BLE001 - 统一收敛
        raise CaseLibraryUnavailable(
            "差异价规则读不到（%s.%s）：%s：请先执行 cpq_kb.ensure_schema() 并由业务灌入费率"
            % (cpq_kb.SCHEMA, DELTA_RULE_TABLE, str(exc)[:200])) from exc
    rows = [_rule_row(row) for row in raw if isinstance(row, dict)]
    if not rows:
        raise CaseLibraryUnavailable(
            "差异价规则表 %s.%s 是空的：差异价必须来自规则表，不回落代码里的默认费率"
            "（Spec 批 3 §2.2）" % (cpq_kb.SCHEMA, DELTA_RULE_TABLE))
    return rows


def _rule_window(row, today) -> dict:
    """规则有效期判定（与批 1 有效期同口径）。"""
    start = _date(row.get("effective_from"))
    end = _date(row.get("effective_to"))
    day = _date(today) or dt.date.today()
    if start and day < start:
        return {"active": False, "reason": "规则未生效（生效日 %s）" % start.isoformat()}
    if end and day > end:
        return {"active": False, "reason": "规则已过期（截止 %s）" % end.isoformat()}
    return {"active": True, "reason": ""}


def _date(value) -> Optional[dt.date]:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = _text(value)[:10]
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _breakpoints(raw):
    """断点表归一：`"[[0,1.15],[3000,0.05],…]"` → [(0.0, 1.15), …]，按档位升序。"""
    data = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except ValueError:
            return []
    if not isinstance(data, (list, tuple)):
        return []
    out = []
    for item in data:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            qty, factor = _num(item[0]), _num(item[1])
            if qty is not None and factor is not None:
                out.append((float(qty), float(factor)))
    return sorted(out, key=lambda pair: pair[0])


def _band_factor(points, value) -> Optional[float]:
    """数量档倍率：取**不小于该值的最小断点**的倍率（超过最高档 → 最高档）。

    例（[[0,1.15],[1000,1.00],[3000,0.95],[6000,0.92],[10000,0.85]]）：
    3000 → 0.95、5000 → 0.92（Spec §2.2 的业务示例：0.95 - 0.92 = +0.03）。
    """
    if not points:
        return None
    for qty, factor in points:
        if value <= qty:
            return factor
    return points[-1][1]


def _rule_for(rules, field_key, today):
    """同一字段只允许一条生效规则；命中多条 → 口径冲突必须人工解决（Spec §2.2 第 4 条）。"""
    hits = [row for row in rules if row["field_key"] == field_key]
    if not hits:
        return {"rule": None, "note": "该字段不单独计差，仅影响相似度与风险提示（Spec §2.2 第 2 条）"}
    active = [row for row in hits if _rule_window(row, today)["active"]]
    if not active:
        return {"rule": None, "note": "；".join(_rule_window(row, today)["reason"] for row in hits)
                                      + "：过期/未生效规则不得按老口径算钱（Spec §2.2 第 3 条）"}
    if len(active) > 1:
        raise WorkspaceError(
            "字段「%s」命中 %d 条生效规则（%s）：口径冲突必须人工解决，不能任选一条"
            % (field_key, len(active), "、".join(row["rule_code"] for row in active)))
    return {"rule": active[0], "note": ""}


def _step_num(value) -> Optional[float]:
    """step 口径的取值：开关（烫金/覆膜/磁铁）按 0/1 计数，其余走数值归一。

    `qq_case._num(True)` 会回 `None`（布尔不是数字），而「烫金 无 → 有」在业务上
    正好是**一步**（Spec §2.2 step：`ceil(|current - base| / step_size)` 步），
    所以布尔先折成 0.0 / 1.0，再交给同一个公式。
    """
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    return _num(value)


def _rule_note(row) -> str:
    if not row:
        return ""
    bits = []
    if row.get("source_type") == "demo":
        bits.append("费率来源=演示数据（source_type=demo），仅供流程演示，出价前必须换成权威费率")
    elif row.get("source_type") and row["source_type"] != "workbook":
        bits.append("费率来源=%s（非权威工作簿）" % row["source_type"])
    if row.get("review_status") and row["review_status"] != "reviewed":
        bits.append("费率未审核（review_status=%s）" % row["review_status"])
    return "；".join(bits)


def delta_price(field_key, base_value, current_value, *, rules=None,
                base_unit_price=None, base_quantity=None, today=None) -> dict:
    """单项差异价（Spec §2.2）。没有规则/规则过期 → `priced=False` + 说明，不编价。"""
    rows = load_rules(rules)
    picked = _rule_for(rows, field_key, today)
    row = picked["rule"]
    out = {"field_key": field_key, "priced": False, "delta": 0.0,
           "rule_code": row["rule_code"] if row else "",
           "rule_version": row.get("version") if row else None,
           "rule_kind": row["rule_kind"] if row else "",
           "unit": row["unit"] if row else "",
           "formula": "", "note": picked["note"]}
    if row is None:
        return out

    kind = row["rule_kind"]
    base, current = base_value, current_value
    if kind == "rate":
        before, after = _num(base), _num(current)
        if before is None or after is None or row["rate"] is None:
            out["note"] = "数值不可解析（base=%r / current=%r），无法按费率计差" % (base, current)
            return out
        out["delta"] = (after - before) * row["rate"]
        out["formula"] = "rate: (%s - %s) × %s = %s %s" % (
            _number_text(after), _number_text(before), _number_text(row["rate"]),
            _number_text(out["delta"]), MONEY_UNIT)
    elif kind == "step":
        before, after = _step_num(base), _step_num(current)
        if before is None or after is None or row["amount"] is None:
            out["note"] = "数值不可解析（base=%r / current=%r），无法按档位计差" % (base, current)
            return out
        size = row["step_size"] or 1.0
        if size <= 0:
            raise WorkspaceError("规则 %s 的 step_size 必须 > 0（收到 %r）" % (row["rule_code"], size))
        steps = int(math.ceil(abs(after - before) / size)) if after != before else 0
        sign = 1.0 if after > before else (-1.0 if after < before else 0.0)
        out["delta"] = sign * steps * row["amount"]
        out["formula"] = "step: ceil(|%s - %s| / %s) = %d 步 × %s = %s %s" % (
            _number_text(after), _number_text(before), _number_text(size), steps,
            _number_text(row["amount"]), _number_text(out["delta"]), MONEY_UNIT)
    elif kind == "band":
        before, after = _num(base), _num(current)
        price = _num(base_unit_price)
        points = _breakpoints(row.get("breakpoints_json"))
        if before is None or after is None:
            out["note"] = "数量不可解析（base=%r / current=%r），无法按档位计差" % (base, current)
            return out
        if not points:
            out["note"] = "规则 %s 的断点表为空/不可解析，不按档位算钱" % row["rule_code"]
            return out
        if price is None:
            out["note"] = "缺基准单价（base_unit_price），无法把数量档差折成金额（Spec §2.2 band）"
            return out
        factor_base = _band_factor(points, before)
        factor_current = _band_factor(points, after)
        out["delta"] = price * (factor_current - factor_base)
        out["formula"] = "band: %s × (%s - %s) = %s %s" % (
            _number_text(price), _number_text(factor_current), _number_text(factor_base),
            _number_text(out["delta"]), MONEY_UNIT)
    elif kind == "direct":
        before, after = _num(base), _num(current)
        if before is None or after is None:
            out["note"] = "数值不可解析（base=%r / current=%r），无法按绝对值计差" % (base, current)
            return out
        out["delta"] = after - before
        out["formula"] = "direct: %s - %s = %s %s" % (
            _number_text(after), _number_text(before), _number_text(out["delta"]), MONEY_UNIT)
    else:
        out["note"] = "未知规则类型 %r（只支持 %s）" % (kind, "/".join(RULE_KINDS))
        return out

    extra = _rule_note(row)
    out["priced"] = True
    if extra:
        out["note"] = extra
    return out


# --------------------------------------------------------------------------- #
# 取值归一与校验
# --------------------------------------------------------------------------- #
def _number_text(value) -> str:
    number = _num(value)
    if number is None:
        return ""
    return "%g" % number


def normalize_value(field_key, value):
    """脏值归一（纯函数）：`"250g"` → 250.0、`"3,000 个"` → 3000.0、`"有"` → True。

    归一不出来就回 None（调用方据此报错），**不猜**成 0 / False。
    """
    spec = FIELD_SPECS.get(field_key)
    if spec is None:
        return value
    kind = spec["value_type"]
    if kind == "num":
        return _num(value)
    if kind == "bool":
        return _tri_bool(value)
    if kind == "enum":
        return _print_colors(value) if field_key == "print_colors" else _text(value)
    return _text(value)


def _spec_range_text(spec) -> str:
    unit = spec.get("unit") or ""
    low, high = spec.get("min"), spec.get("max")
    if low is None or high is None:
        return ""
    return "%s～%s %s" % (_number_text(low), _number_text(high), unit)


def validate_edits(edits) -> List[tuple]:
    """把一批修改逐条校验，返回 `[(field, reason)]`（空列表 = 全部合法）。

    纯函数、不写入：`apply_edits()` 先整体校验再一次写入 —— 有一条不合法就整批不写。
    """
    out: List[tuple] = []
    if not isinstance(edits, dict):
        return [("<edits>", "修改必须是一个 {字段: 新值} 字典")]
    for field, raw in edits.items():
        spec = FIELD_SPECS.get(field)
        if spec is None:
            out.append((field, "未知字段：不在可编辑字段闭集里（Spec §2.3）"))
            continue
        value = normalize_value(field, raw)
        if spec["value_type"] == "num":
            if value is None:
                out.append((field, "%s 必须是数字（收到 %r）" % (spec["label"], raw)))
                continue
            low, high = spec.get("min"), spec.get("max")
            if low is not None and value < low or high is not None and value > high:
                out.append((field, "%s 超出允许范围（%s）" % (spec["label"], _spec_range_text(spec))))
        elif spec["value_type"] == "bool":
            if value is None:
                out.append((field, "%s 只能是「是 / 否」（收到 %r）" % (spec["label"], raw)))
        elif spec["value_type"] == "enum":
            choices = tuple(spec.get("choices") or ())
            if choices and value not in choices:
                out.append((field, "%s 只能是：%s（收到 %r）"
                            % (spec["label"], "、".join(choice or "空" for choice in choices), raw)))
        else:
            if value is None:
                out.append((field, "%s 不能为空" % spec["label"]))
    return out


# --------------------------------------------------------------------------- #
# 工作区状态机
# --------------------------------------------------------------------------- #
def _stamp(day=None) -> str:
    if isinstance(day, dt.datetime):
        return day.isoformat(timespec="seconds")
    if isinstance(day, dt.date):
        return "%sT00:00:00" % day.isoformat()
    return dt.datetime.now().isoformat(timespec="seconds")


def _actor(user, fallback=None) -> dict:
    """操作人归一。没有用户时沿用工作区创建者；**不产生匿名修改记录**（Spec §2.4 第 5 条）。"""
    source = user if isinstance(user, dict) else (fallback or {})
    return {"user_id": _text(source.get("user_id")),
            "username": _text(source.get("username")),
            "role_code": _text(source.get("role_code"))}


def _require_write_role(user, action) -> dict:
    actor = _actor(user)
    if not actor["user_id"] and not actor["username"]:
        raise WorkspaceError("%s需要登录用户（要留痕：谁改的、谁确认的）" % action)
    if actor["role_code"] not in WRITE_ROLES:
        raise WorkspaceError("%s需要 %s 角色（当前「%s」）；复用报价写入角色闭集，不新造"
                             % (action, "、".join(sorted(WRITE_ROLES)), actor["role_code"] or "未登录"))
    return actor


def _base_values(baseline) -> (Dict[str, Any], List[str]):
    src = baseline if isinstance(baseline, dict) else {}
    values: Dict[str, Any] = {}
    snapshot = src.get("case_snapshot")
    if isinstance(snapshot, dict):
        for key in FIELD_KEYS:
            if key in snapshot:
                values[key] = normalize_value(key, snapshot.get(key))
    explicit = src.get("base_values")
    if isinstance(explicit, dict):
        for key, value in explicit.items():
            if key in FIELD_SPECS and key in explicit:
                values[key] = normalize_value(key, value)
    missing = [key for key in FIELD_KEYS if values.get(key) is None
               and key not in values or values.get(key) is None and values.get(key) != 0]
    return values, missing


def new_workspace(baseline, *, user=None) -> dict:
    """新建工作区：`current = base_values`（基准案例值只读，永不修改）。"""
    src = baseline if isinstance(baseline, dict) else {}
    values, missing = _base_values(src)
    stamp = _stamp()
    return {
        "engine_version": ENGINE_VERSION,
        "industry": INDUSTRY,
        "case_code": _text(src.get("case_code")),
        "baseline": copy.deepcopy(src),
        "base_values": copy.deepcopy(values),
        "current": copy.deepcopy(values),
        "pending": {},
        "edits": [],
        "missing_base_fields": missing,
        "confirmed": True,
        "created_at": stamp,
        "updated_at": stamp,
        "user": _actor(user),
    }


def _edit_record(field, base, value, source, actor, confirmed, stamp) -> dict:
    return {"field": field, "base": base, "current": value, "source": source,
            "user_id": actor.get("user_id", ""), "at": stamp, "confirmed": bool(confirmed)}


def apply_edits(workspace, edits, *, source="workspace", user=None, rules=None) -> dict:
    """把一批修改写进工作区（纯函数，返回新工作区）。

    `source="workspace"`（右侧直接改）→ 进 `current`；`source="agent"`（Agent 建议）→ 进
    `pending` 并置 `confirmed=False`。整批先校验：有一条不合法就整批不写。
    `rules` 是给后续「写入即校验口径」留的参数，本批只做结构性校验（不在这里定价）。
    """
    if source not in EDIT_SOURCES:
        raise WorkspaceError("修改来源只能是 %s（收到 %r）" % ("/".join(EDIT_SOURCES), source))
    problems = validate_edits(edits)
    if problems:
        raise WorkspaceError(
            "有 %d 项修改不合法，整批都没有写入：%s"
            % (len(problems), "；".join("%s：%s" % (field, reason) for field, reason in problems)),
            field_errors=[{"field": field, "reason": reason} for field, reason in problems])
    out = copy.deepcopy(workspace)
    out.setdefault("pending", {})
    out.setdefault("current", {})
    out.setdefault("base_values", {})
    out.setdefault("edits", [])
    actor = _actor(user, out.get("user"))
    stamp = _stamp()
    base_values = out["base_values"]
    for field, raw in (edits or {}).items():
        value = normalize_value(field, raw)
        base = normalize_value(field, base_values.get(field))
        if source == "workspace":
            out["current"][field] = value
            out["pending"].pop(field, None)
            out["edits"].append(_edit_record(field, base, value, source, actor, True, stamp))
            continue
        if base is not None and value == base:
            # 改回基准值 = 还原：pending 清掉，但留一条「还原」编辑记录（Spec §2.3）。
            out["pending"].pop(field, None)
            out["edits"].append(_edit_record(field, base, value, source, actor, True, stamp))
            continue
        out["pending"][field] = value
        out["edits"].append(_edit_record(field, base, value, source, actor, False, stamp))
    out["confirmed"] = not out["pending"]
    out["updated_at"] = stamp
    return out


def pending_fields(workspace) -> dict:
    """当前待确认字段（供 UI 标黄显示）。"""
    return copy.deepcopy(dict((workspace or {}).get("pending") or {}))


def confirm(workspace, *, user=None, fields=None, rules=None) -> dict:
    """右侧确认：把 `pending`（或点名 `fields`）合并进 `current` 并清空对应 pending。"""
    actor = _require_write_role(user, "确认修改")
    out = copy.deepcopy(workspace)
    pending = dict(out.get("pending") or {})
    if fields is None:
        chosen = list(pending.keys())
    else:
        chosen = [key for key in pending if key in set(fields)]
    stamp = _stamp()
    for field in chosen:
        out.setdefault("current", {})[field] = pending.pop(field)
    out["pending"] = pending
    for row in out.get("edits") or []:
        if row.get("field") in set(chosen) and not row.get("confirmed"):
            row["confirmed"] = True
    out["edits"].append({"field": "、".join(chosen) if chosen else "<none>",
                         "base": "", "current": "", "source": "workspace",
                         "user_id": actor.get("user_id", ""), "at": stamp,
                         "confirmed": True, "action": "confirm",
                         "confirmed_fields": list(chosen)})
    out["confirmed"] = not pending
    out["updated_at"] = stamp
    return out


# --------------------------------------------------------------------------- #
# 差异表与差异合计
# --------------------------------------------------------------------------- #
def _display(field, value) -> str:
    spec = FIELD_SPECS.get(field) or {}
    if value is None:
        return "—"
    if spec.get("value_type") == "bool":
        return "有" if value else "无"
    if spec.get("value_type") == "num":
        unit = spec.get("unit") or ""
        return ("%s %s" % (_number_text(value), unit)).strip()
    return _text(value)


def _delta_text(delta) -> str:
    return "%+.2f %s" % (float(delta), MONEY_UNIT)


def _effective(workspace, field):
    """(值, 是否 pending)：pending 优先（预览口径），否则用已确认的 current。"""
    pending = dict((workspace or {}).get("pending") or {})
    if field in pending:
        return pending[field], True
    return ((workspace or {}).get("current") or {}).get(field), False


def diff_table(workspace, *, rules=None) -> List[dict]:
    """只输出**有差异**的字段（基准值 ≠ 当前值，含 pending），行序 = FIELD_KEYS 顺序。"""
    base_values = dict((workspace or {}).get("base_values") or {})
    baseline = dict((workspace or {}).get("baseline") or {})
    unit_price = _num(baseline.get("base_unit_price"))
    rows = []
    for field in FIELD_KEYS:
        if field not in base_values:
            continue
        base = normalize_value(field, base_values.get(field))
        current, pending = _effective(workspace, field)
        current = normalize_value(field, current)
        if base == current:
            continue                             # 差异为 0 的行不出现在表里（Spec §2.3）
        priced = delta_price(field, base, current, rules=rules,
                             base_unit_price=unit_price)
        spec = FIELD_SPECS.get(field) or {}
        rows.append({
            "field_key": field,
            "label": spec.get("label") or field,
            "group": spec.get("group") or "",
            "unit": spec.get("unit") or "",
            "base_value": base,
            "current_value": current,
            "display_base": _display(field, base),
            "display_current": _display(field, current),
            "delta": float(priced["delta"]),
            "delta_text": _delta_text(priced["delta"]),
            "priced": bool(priced["priced"]),
            "pending": bool(pending),
            "rule_code": priced["rule_code"],
            "rule_version": priced["rule_version"],
            "formula": priced["formula"],
            "note": priced["note"],
        })
    return rows


def diff_total(workspace, *, rules=None) -> dict:
    """差异合计：已确认 / 预览（含 pending）两条口径，绝不把 pending 当已确认。"""
    baseline = dict((workspace or {}).get("baseline") or {})
    base_unit_price = _num(baseline.get("base_unit_price"))
    rows = diff_table(workspace, rules=rules)
    confirmed = sum(row["delta"] for row in rows if not row["pending"])
    preview = sum(row["delta"] for row in rows)
    versions: List[str] = []
    for row in rows:
        version = row.get("rule_version")
        if row["priced"] and version is not None and str(version) not in versions:
            versions.append(str(version))
    return {
        "engine_version": ENGINE_VERSION,
        "base_unit_price": base_unit_price,
        "currency": _text(baseline.get("base_currency")) or qq_case.DEFAULT_CURRENCY,
        "tax_included": bool(baseline.get("base_tax_included")),
        "confirmed_delta_total": confirmed,
        "preview_delta_total": preview,
        "confirmed_unit_price": (base_unit_price or 0.0) + confirmed,
        "preview_unit_price": (base_unit_price or 0.0) + preview,
        "items": rows,
        "rule_versions": versions,
    }


# --------------------------------------------------------------------------- #
# 自然语言入口（只进 pending，绝不出价）
# --------------------------------------------------------------------------- #
_CLAUSE_SPLIT = ("，", ",", "；", ";", "。", "\n", "　")
_CHANGE_VERBS = ("改成", "改为", "调整为", "调成", "设为", "设置为")
_ADD_VERBS = ("再增加", "增加", "加上", "加")
_REMOVE_VERBS = ("去掉", "取消", "不要", "不加")


def _clauses(text) -> List[str]:
    out = [str(text or "")]
    for separator in _CLAUSE_SPLIT:
        pieces = []
        for part in out:
            pieces.extend(part.split(separator))
        out = pieces
    return [piece.strip() for piece in out if piece and piece.strip()]


def _find_field(clause: str):
    lowered = clause.lower()
    best = None
    for field, aliases in _AGENT_ALIASES:
        for alias in aliases:
            if alias.lower() in lowered:
                if best is None or len(alias) > len(best[1]):
                    best = (field, alias)
    return best


def _pick_verb(clause: str, verbs):
    for verb in sorted(verbs, key=len, reverse=True):
        index = clause.find(verb)
        if index >= 0:
            return verb, index
    return "", -1


def _unit_word(clause: str) -> str:
    for word, unit in _UNIT_WORDS:
        if word in clause:
            return unit
    return ""


def _candidates_for(raw_value, unit_hint: str) -> List[str]:
    """歧义时的候选字段：按取值类型（和单位提示）筛，宁可多问也不猜（Spec §2.4 第 2 条）。"""
    out = []
    for field in FIELD_KEYS:
        spec = FIELD_SPECS[field]
        kind = spec["value_type"]
        if unit_hint:
            if kind == "num" and (spec.get("unit") or "") != unit_hint:
                continue
        elif kind not in ("num", "bool", "enum"):
            continue
        out.append(field)
    return out


def _builtin_propose(text, workspace) -> dict:
    """内置确定性解析：识别「把 X 改成 Y」「再增加 X」「去掉 X」，歧义一律不猜。"""
    proposed: Dict[str, Any] = {}
    unresolved: List[dict] = []
    for clause in _clauses(text):
        field = _find_field(clause)
        if field is None:
            continue                                     # 没点名字段：整句交给 unresolved 汇总
        key, _alias = field
        spec = FIELD_SPECS[key]
        change_verb, change_at = _pick_verb(clause, _CHANGE_VERBS)
        add_verb, add_at = _pick_verb(clause, _ADD_VERBS)
        remove_verb, remove_at = _pick_verb(clause, _REMOVE_VERBS)
        if change_at >= 0:
            raw_value = clause[change_at + len(change_verb):].strip()
            unit_hint = _unit_word(raw_value)
            if spec["value_type"] == "num" and unit_hint and unit_hint != (spec.get("unit") or ""):
                unresolved.append({"text": clause, "candidates": [key],
                                   "reason": "%s 的单位是 %s，不是「%s」——不写入"
                                             % (spec["label"], spec.get("unit") or "无", unit_hint)})
                continue
            value = normalize_value(key, raw_value)
            if spec["value_type"] == "num" and value is None:
                unresolved.append({"text": clause, "candidates": [key],
                                   "reason": "%s 的值「%s」不是数字——不写入" % (spec["label"], raw_value)})
                continue
            if spec["value_type"] == "bool" and value is None:
                unresolved.append({"text": clause, "candidates": [key],
                                   "reason": "%s 的值「%s」不是「是 / 否」——不写入" % (spec["label"], raw_value)})
                continue
            proposed[key] = value
        elif remove_at >= 0:
            if spec["value_type"] != "bool":
                unresolved.append({"text": clause, "candidates": [key],
                                   "reason": "%s 不是开关字段，「去掉」不适用——不写入" % spec["label"]})
                continue
            proposed[key] = False
        elif add_at >= 0:
            if spec["value_type"] == "bool":
                proposed[key] = True
            else:
                raw_value = clause[add_at + len(add_verb):].strip()
                value = normalize_value(key, raw_value)
                if value is None:
                    unresolved.append({"text": clause, "candidates": [key],
                                       "reason": "%s 要增加多少没说清（「%s」）——不写入"
                                                 % (spec["label"], raw_value)})
                    continue
                proposed[key] = value
        else:
            unresolved.append({"text": clause, "candidates": [key],
                               "reason": "看不出是要改成什么（缺「改成 / 增加 / 去掉」）——不写入"})
    if not proposed and not unresolved:
        unresolved = _ambiguous_items(text)
    return {"proposed": proposed, "unresolved": unresolved}


def _ambiguous_items(text) -> List[dict]:
    """整句都没有字段名时：给出候选字段清单，不猜（Spec §2.4 第 2 条）。"""
    out = []
    for clause in _clauses(text):
        unit_hint = _unit_word(clause)
        out.append({"text": clause,
                    "reason": "这句没说清改哪个字段：请带上字段名（如「面纸改成 250g」）",
                    "candidates": _candidates_for(clause, unit_hint)})
    return out


def agent_patch(workspace, text, *, propose=None, user=None) -> dict:
    """Agent 的自然语言修改入口：**只写 pending**，不写 current、不落库、不出价。"""
    ws = copy.deepcopy(workspace)
    actor = _actor(user, ws.get("user"))
    warnings: List[str] = []
    if callable(propose):
        raw = propose(text, ws) or {}
        proposed = {}
        unresolved = []
        for field, value in (raw.items() if isinstance(raw, dict) else ()):
            problems = validate_edits({field: value})
            if problems:
                unresolved.append({"text": str(text), "candidates": [field],
                                   "reason": problems[0][1]})
                continue
            proposed[field] = normalize_value(field, value)
    else:
        parsed = _builtin_propose(text, ws)
        proposed, unresolved = parsed["proposed"], parsed["unresolved"]
    if proposed:
        ws = apply_edits(ws, proposed, source="agent", user=actor)
    else:
        warnings.append("没有识别到可以写入的字段修改：请带上字段名与目标值（例如「面纸改成 250g」）")
    for item in unresolved:
        warnings.append("%s：%s" % (item.get("text", ""), item.get("reason", "")))
    return {
        "engine_version": ENGINE_VERSION,
        "text": _text(text),
        "proposed_edits": dict(proposed),
        "unresolved": list(unresolved),
        "requires_confirmation": True,
        "warnings": warnings,
        "workspace": _agent_view(ws),
    }


#: Agent 视图里**不出现**的价格字段（Spec §2.4 第 3 条：Agent 入口不得出价）。
_PRICE_MARKERS = ("price", "amount", "cost", "delta")


def _agent_view(workspace) -> dict:
    """给 Agent / 左侧对话用的工作区视图：结构照旧，但把价格类字段摘掉。"""
    out = copy.deepcopy(workspace)
    baseline = out.get("baseline")
    if isinstance(baseline, dict):
        out["baseline"] = {key: value for key, value in baseline.items()
                           if not any(marker in key.lower() for marker in _PRICE_MARKERS)}
    return out


# --------------------------------------------------------------------------- #
# 落库与恢复（复用报价卡片第 2 步快照，不新建表）
# --------------------------------------------------------------------------- #
def save(workspace, *, user=None, session_id="") -> dict:
    """把工作区落进报价卡片第 2 步快照。未确认（有 pending）时拒绝落库。"""
    actor = _require_write_role(user, "保存快速报价工作区")
    ws = workspace if isinstance(workspace, dict) else {}
    pending = dict(ws.get("pending") or {})
    if pending:
        raise WorkspaceError(
            "还有 %d 项修改没确认（%s）：请先在右侧确认修改再保存"
            % (len(pending), "、".join((FIELD_SPECS.get(key) or {}).get("label") or key
                                       for key in pending)))
    sid = _text(session_id)
    if not sid:
        raise WorkspaceError("保存快速报价工作区必须带报价卡片 session_id（Spec 批 3 §2.5）")
    payload = copy.deepcopy(ws)
    payload["saved_by"] = actor
    payload["saved_at"] = _stamp()
    cpq_wf.merge_step_snapshot(sid, SNAPSHOT_STEP_NO, {SNAPSHOT_SECTION: payload})
    return {"ok": True, "session_id": sid, "step_no": SNAPSHOT_STEP_NO,
            "engine_version": ENGINE_VERSION, "saved_at": payload["saved_at"],
            SNAPSHOT_SECTION: payload}


def load(session_id) -> dict:
    """从报价卡片第 2 步快照读回工作区（没有就回 `{}`，不抛错）。"""
    snapshot = cpq_wf.step_snapshot(_text(session_id), SNAPSHOT_STEP_NO) or {}
    section = snapshot.get(SNAPSHOT_SECTION)
    return copy.deepcopy(section) if isinstance(section, dict) else {}


# --------------------------------------------------------------------------- #
# 规则表灌库（只读→写库那一步，必须显式调用；幂等）
# --------------------------------------------------------------------------- #
#: Spec §2.2 的示例费率（**演示数据**，不是客户权威费率）。要真正算钱必须由业务把
#: 工作簿口径灌进 `kb_quick_quote_delta_rule`，见 DEPLOYMENT.md「快速报价（差异价规则）」。
SEED_SOURCE_REF = "docs/specs/quick-quote-3-field-workspace-and-delta-price.md#2.2（示例费率）"
SEED_RULES = (
    {"rule_code": "QQQ-DEMO-QTY-BAND", "field_key": "quantity", "rule_kind": "band",
     "unit": "元", "breakpoints_json": "[[0,1.15],[1000,1.00],[3000,0.95],[6000,0.92],[10000,0.85]]",
     "version": "1", "effective_from": "2026-01-01", "effective_to": ""},
    {"rule_code": "QQQ-DEMO-PAPER-RATE", "field_key": "face_paper_gsm", "rule_kind": "rate",
     "unit": "元/g/m²", "rate": 0.0062, "version": "1",
     "effective_from": "2026-01-01", "effective_to": ""},
    {"rule_code": "QQQ-DEMO-HOTSTEP", "field_key": "hot_stamping", "rule_kind": "step",
     "unit": "元", "amount": 0.18, "step_size": 1.0, "version": "1",
     "effective_from": "2026-01-01", "effective_to": ""},
    {"rule_code": "QQQ-DEMO-LEN-RATE", "field_key": "inner_length", "rule_kind": "rate",
     "unit": "元/mm", "rate": 0.012, "version": "1",
     "effective_from": "2026-01-01", "effective_to": ""},
)


def seed_rule_rows(rows=None) -> List[dict]:
    """示例费率行（**标成 demo + draft**，纯函数）：写库前先看清它们是演示数据。"""
    source = rows if rows is not None else SEED_RULES
    out = []
    for row in source:
        item = {key: copy.deepcopy(value) for key, value in row.items()}
        item.setdefault("industry", INDUSTRY)
        item["source_type"] = "demo"
        item["review_status"] = "draft"
        item["source_ref"] = SEED_SOURCE_REF
        out.append(item)
    return out


def seed_rules(rows=None, *, conn=None) -> dict:
    """把示例费率幂等地灌进规则表（演示用）。返回 `{"ok","table","rows","changed","kb_version"}`。"""
    items = seed_rule_rows(rows)
    own = conn is None
    if own:
        conn = cpq_kb._connect()
    try:
        if own:
            with conn.transaction():
                cur = conn.cursor()
                cpq_kb._ensure_schema(cur)
                changed = cpq_kb._upsert_rows(cur, DELTA_RULE_TABLE, items)
                version = cpq_kb._bump_version(cur, changed)
        else:
            cur = conn.cursor()
            changed = cpq_kb._upsert_rows(cur, DELTA_RULE_TABLE, items)
            version = cpq_kb._bump_version(cur, changed)
    except Exception as exc:                                   # noqa: BLE001 - 统一收敛
        raise cpq_kb.KbUnavailable("差异价规则灌库失败（%s.%s）：%s"
                                   % (cpq_kb.SCHEMA, DELTA_RULE_TABLE,
                                      str(exc).splitlines()[0][:200])) from exc
    finally:
        if own:
            conn.close()
    return {"ok": True, "table": DELTA_RULE_TABLE, "rows": len(items),
            "changed": int(changed), "kb_version": int(version)}


if __name__ == "__main__":                                  # pragma: no cover - 手工跑
    print("fields =", len(FIELD_KEYS), "| groups =", len(FIELD_GROUPS),
          "| rule kinds =", len(RULE_KINDS))

# -*- coding: utf-8 -*-
"""体验与可观察性的统一投影（体验与可观察性批次 4，Spec §2.1）。

为什么要有它（Spec §0/§1）：让销售 / 工艺 / 财务在**不猜**的前提下看懂四件事——
**现在缺什么、当前算的是哪一版、本次结果是谁算的、这一件为什么排在别人后面**；
并把「几何区域 / 几何零件 / 业务件」降级为「主界面只突出业务零件」。

本模块**纯函数、不连库、不写盘、不调模型**：唯一口径来自后端，前端只展示。
本批只做**解释面与可观察性**：不改成本公式、不改匹配算法、不改路线选择（Spec §0）。
"""
from __future__ import annotations

from datetime import datetime, timezone

OBSERVABILITY_VERSION = "packaging-observability/1"

#: 成本缺口分类闭集（未知分类归入「其它」，不许静默丢弃）。
GAP_CATEGORIES = ("缺材料价", "缺用量", "缺尺寸", "其它")

#: 排模/单件算用量必须齐的参数（本批先按 4 项占位，Spec §7）。
MOLD_REQUIRED_PARAMS = ("sheet_width_mm", "sheet_height_mm", "piece_length_mm", "piece_width_mm")

#: 「不建议自动识别、建议直接人工建档」的信号 → 中文标签（顺序稳定）。
MANUAL_SIGNALS = (
    ("scanned_raster", "扫描位图（非矢量）"),
    ("no_blocks", "没有块/符号结构"),
    ("no_layers", "没有有效图层分层"),
    ("all_open_curves", "全是开放曲线，取不到闭合轮廓"),
    ("text_only", "只有文字说明没有几何"),
)

BADGE_DRAFT = "草稿可看，不可送审"
EXPRESSION_NOTE = "变量映射后的表达式匹配"


def quote_export_blockers(*, steps, done) -> dict:
    """正式导出前还差哪几步：`next_steps` 按 `steps` 顺序给未完成项。"""
    steps = list(steps or [])
    done_set = set(done or [])
    next_steps = [step for step in steps if step not in done_set]
    return {"can_export": not next_steps, "next_steps": next_steps,
            "remaining": len(next_steps)}


def cost_display(*, version, is_estimate, gaps) -> dict:
    """成本页的「版本 + 是否暂估 + 缺口分类」统一投影；四类键必须全在。"""
    counts = {name: 0 for name in GAP_CATEGORIES}
    items = list(gaps or [])
    for item in items:
        category = ""
        if isinstance(item, dict):
            category = str(item.get("category") or "")
        counts[category if category in counts else "其它"] += 1
    return {"version": int(version), "is_estimate": bool(is_estimate),
            "gap_total": len(items), "gaps": counts}


def mold_readiness(*, params) -> dict:
    """排模算用量缺什么：`missing` 按 MOLD_REQUIRED_PARAMS 顺序点名空/0 的项。"""
    params = params or {}
    missing = []
    for key in MOLD_REQUIRED_PARAMS:
        value = params.get(key)
        if value is None or isinstance(value, bool):
            missing.append(key)
            continue
        try:
            if float(value) == 0.0:
                missing.append(key)
        except (TypeError, ValueError):
            if not str(value).strip():
                missing.append(key)
    return {"can_compute": not missing, "missing": missing}


def converter_banner(*, converter_role, provider, version, license_ok=None) -> dict:
    """2.1 顶部「主/回退 + 版本 + 许可状态」；未知许可不许谎报 ok。"""
    role = str(converter_role or "")
    done_by = {"primary": "主转换器", "fallback": "回退转换器"}.get(role, "未知")
    license_state = "ok" if license_ok is True else "unverified"
    warnings = []
    if role == "fallback":
        warnings.append("本次由回退转换器完成，请核对与主转换器的一致性")
    if license_state != "ok":
        warnings.append("许可状态未确认，结果仅供参考")
    return {"done_by": done_by, "provider": str(provider or ""), "version": str(version or ""),
            "license": license_state, "warning": "；".join(warnings)}


def cost_expression_note() -> str:
    """表达式口径文案唯一来源：恰为「变量映射后的表达式匹配」。"""
    return EXPRESSION_NOTE


def part_concepts_view(*, business_total, geometry_total) -> dict:
    """主界面只突出「业务零件」；几何区域数量进 secondary。"""
    return {"primary": {"label": "业务零件", "total": int(business_total)},
            "secondary": [{"label": "几何区域", "total": int(geometry_total)}]}


def manual_drawing_advice(*, signals) -> dict:
    """哪些图不建议自动识别：命中任一信号 → 建议直接人工建档。"""
    signals = signals or {}
    reasons = [label for key, label in MANUAL_SIGNALS if signals.get(key)]
    recommend = bool(reasons)
    advice = ("建议直接人工建档：本图纸命中「%s」，自动识别不可靠。" % "、".join(reasons)
              if recommend else "未命中不建议自动识别的信号，可走自动识别。")
    return {"recommend_manual": recommend, "reasons": reasons, "advice": advice}


def draft_visibility(action) -> dict:
    """草稿角标按操作细分：内部评审 ≠ 正式报价发布。"""
    act = str(action or "")
    return {"badge": BADGE_DRAFT, "can_read": True, "can_do": act in ("read", "internal_review")}


def _parse_ts(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        return float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def waiting_view(waiting_for, *, now=None, last_event_at=None) -> dict:
    """消费既有 `waiting_for`（原样保留 kind/role/label/username），另加「已等多久」。"""
    waiting_for = waiting_for or {}
    out = {"kind": str(waiting_for.get("kind") or ""),
           "role": str(waiting_for.get("role") or ""),
           "label": str(waiting_for.get("label") or ""),
           "username": str(waiting_for.get("username") or ""),
           "elapsed_seconds": None, "elapsed_text": ""}
    if out["kind"] == "none":
        return out
    start = _parse_ts(last_event_at)
    end = _parse_ts(now) if now is not None else datetime.now(timezone.utc).timestamp()
    if start is None or end is None:
        return out
    elapsed = max(0, int(round(end - start)))
    out["elapsed_seconds"] = elapsed
    out["elapsed_text"] = _elapsed_text(elapsed)
    return out


def _elapsed_text(seconds: int) -> str:
    if seconds < 60:
        return "已等 %d 秒" % seconds
    if seconds < 3600:
        return "已等 %d 分钟" % (seconds // 60)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return "已等 %d 小时 %d 分钟" % (hours, minutes)

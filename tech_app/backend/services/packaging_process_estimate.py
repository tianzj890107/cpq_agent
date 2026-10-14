"""相似件 / 相似工序估计包装工序**缺的输入参数**（本批只估标准工时）。

Spec：`docs/specs/packaging-cost-estimate-missing-params.md`。

一句话：公式一行不改；只有这一件在库里**对不上**（相似命中或没命中）时，才把缺的
`standard_seconds` 按"相似件路线 → 相似工序模板 → 兜底估算口径"估出来，逐条标
`similarity_estimate`，**不需要 approved**，但绝不冒充标准工时。纯函数、离线、无副作用。
"""
from __future__ import annotations

import math

ENGINE_VERSION = "packaging-process-estimate/1"
ESTIMATE_TIME_SOURCES = ("similarity_estimate",)
ESTIMATE_KINDS = ("standard_seconds",)
ESTIMATE_ORIGINS = ("reference_route", "similar_step_template", "operations_estimate_policy")
DEFAULT_OPERATION_SECONDS = 30.0

#: 只有这些命中方式说明"这一件在库里对不上"；精确命中（编码/逐字名/归一化名）一律不重估。
_SIMILAR_METHODS = ("feature_similarity",)
#: 只有"库里确实没有这一件的路线"才算对不上；桥不可用（unavailable）不算 —— 那种情况不编工时。
_MISS_STATUSES = ("not_found",)


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _text(value):
    return value if isinstance(value, str) else ("" if value is None else str(value))


def similarity_route(lookup) -> bool:
    """这一件是不是"只能靠相似/没命中"（Spec §2.2）：精确命中与空 lookup 一律为假。"""
    source = lookup if isinstance(lookup, dict) else {}
    if _text(source.get("match_method")) in _SIMILAR_METHODS:
        return True
    if _text(source.get("status")) in _MISS_STATUSES:
        return True
    return bool(source.get("reference_routes"))


def _key(name) -> tuple:
    """工序名的比较键：优先用路线闭集的归一化（别名 1:N 展开），否则退回原文。"""
    try:
        from . import packaging_route
        targets = packaging_route.normalize_step_name(name)
    except Exception:                                       # noqa: BLE001 - 归一化缺席不挡估计
        targets = ()
    return tuple(targets) or (_text(name).strip(),)


def _same_step(left, right) -> bool:
    a, b = _key(left), _key(right)
    return bool(a) and (a == b or set(a) & set(b))


def reference_operations(lookup) -> dict:
    """相似路线里**有工时的**同名工序（Spec §2.2）：`{规范化工序名: 秒}`，无则空 dict。"""
    source = lookup if isinstance(lookup, dict) else {}
    routes = []
    for key in ("reference_routes", "routes"):
        for route in source.get(key) or []:
            if isinstance(route, dict):
                routes.append(route)
    found: dict = {}
    for route in routes:
        for row in route.get("steps") or []:
            if not isinstance(row, dict):
                continue
            name = row.get("operation_name") or row.get("name") or row.get("step_name")
            seconds = _num(row.get("standard_seconds"))
            if name and seconds is not None:
                found.setdefault(_text(name).strip(), seconds)
    return found


def template_seconds(step_name, templates=(), *, box_type_code="") -> dict or None:
    """相似**工序**模板里同名工序的标准工时（Spec §2.2）：同盒型优先，其次任意盒型。"""
    rows = [row for row in (templates or []) if isinstance(row, dict)]
    wanted = _text(box_type_code).strip()
    same, other = [], []
    for row in rows:
        seconds = _num(row.get("standard_seconds"))
        if seconds is None:
            continue
        if not _same_step(step_name, row.get("step_name")):
            continue
        item = {"seconds": seconds, "origin": "similar_step_template",
                "reference": {"box_type_code": _text(row.get("box_type_code")),
                              "step_name": _text(row.get("step_name")),
                              "part_code": _text(row.get("part_code"))}}
        (same if wanted and _text(row.get("box_type_code")) == wanted else other).append(item)
    for bucket in (same, other):
        if bucket:
            bucket.sort(key=lambda item: (item["reference"]["step_name"],
                                          item["reference"]["part_code"]))
            return bucket[0]
    return None


def estimate_seconds(step_name, *, lookup, templates=(), box_type_code="") -> dict or None:
    """缺工时的估计值（Spec §2.2）：相似路线 → 相似模板 → 兜底口径；不该估时返回 `None`。"""
    if not similarity_route(lookup):
        return None
    seconds = reference_operations(lookup)
    for name, value in seconds.items():
        if _same_step(step_name, name):
            return {"seconds": value, "origin": "reference_route",
                    "reference": {"operation_name": name}}
    found = template_seconds(step_name, templates, box_type_code=box_type_code)
    if found is not None:
        return found
    return {"seconds": DEFAULT_OPERATION_SECONDS, "origin": "operations_estimate_policy",
            "reference": {"step_name": _text(step_name)}}


def fill_step(step, *, name, lookup, templates=(), box_type_code="", part_code="") -> dict or None:
    """就地补一条缺工时的步（Spec §2.2），返回估计留痕；不该估/已有时工 → `None`。"""
    if not isinstance(step, dict) or step.get("standard_seconds") is not None:
        return None
    estimate = estimate_seconds(name or step.get("step_name"), lookup=lookup,
                                templates=templates, box_type_code=box_type_code)
    if estimate is None:
        return None
    trace = {"kind": "standard_seconds", "origin": estimate["origin"],
             "reference": estimate.get("reference") or {}, "value": estimate["seconds"],
             "engine_version": ENGINE_VERSION, "part_code": _text(part_code)}
    step["standard_seconds"] = estimate["seconds"]
    step["duration_min"] = estimate["seconds"] / 60.0
    step["time_source"] = ESTIMATE_TIME_SOURCES[0]
    step["estimated"] = True
    step["estimate"] = trace
    parameters = dict(step.get("cost_parameters") or {})
    parameters["labor_seconds"] = estimate["seconds"]
    parameters["estimated"] = True
    parameters["estimate_origin"] = estimate["origin"]
    step["cost_parameters"] = parameters
    return trace

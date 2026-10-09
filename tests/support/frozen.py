# -*- coding: utf-8 -*-
"""集合封闭断言（发布保障批次 2，Spec §2.3）。

为什么要有它：现行「防偷加接口」用计数断言（`assertEqual(len(...), 13)`）表达，
**新增一个合法条目也会红**，与「防偷加接口」的本意相反；而且失败信息只说「13 != 14」，
看不出是"多加了接口"还是"删了口径"（Spec §1.3）。

本模块提供两个纯函数：

  · `frozen_diff(actual, allowed)` —— 两侧按**字符串**排序去重后求差，给出
    `{"extra": [...], "missing": [...]}`；
  · `assert_closed_set(case, name, actual, allowed)` —— `set(actual) == set(allowed)`
    才通过；失败信息**分开**列出「新增（extra）」与「缺失（missing）」。

419 处迁移是后续批次的事；本批只交付这个可复用的断言件。
"""
from __future__ import annotations

__all__ = ["frozen_diff", "assert_closed_set"]


def _normalize(values) -> list:
    return sorted({str(item) for item in (values or [])})


def frozen_diff(actual, allowed) -> dict:
    """两侧都按字符串排序去重，返回 `{"extra": [...], "missing": [...]}`。"""
    actual_set = set(_normalize(actual))
    allowed_set = set(_normalize(allowed))
    return {
        "extra": sorted(actual_set - allowed_set),
        "missing": sorted(allowed_set - actual_set),
    }


def assert_closed_set(case, name, actual, allowed) -> None:
    """`set(actual) == set(allowed)` 才通过；否则 `case.fail` 并分开列出新增与缺失。"""
    diff = frozen_diff(actual, allowed)
    if not diff["extra"] and not diff["missing"]:
        return
    lines = ["%s：封闭集合与冻结清单不一致" % name]
    if diff["missing"]:
        lines.append("缺失（口径被删/被改）: " + ", ".join(diff["missing"]))
    if diff["extra"]:
        lines.append("新增（多出的接口，若合法请更新冻结清单）: " + ", ".join(diff["extra"]))
    case.fail("\n".join(lines))

# -*- coding: utf-8 -*-
"""知识库运行期健康面板（体验与可观察性批次 4，Spec §2.2）。

为什么要有它：既有 `tech_app/tools/kb_deploy_preflight.py` 是**部署**预检，不是运行期健康面板；
运行期需要回答「哪些表为空、必需的缺没缺、多少是演示数据、当前路线还能不能用」。

判据红线（Spec §2.2）：`usable_for_current_route` **只由 `required_tables` 决定** ——
无关表为空**不得**宣布「全系统不可用」。纯函数、不连库、不写盘、不联网。
"""
from __future__ import annotations

KB_HEALTH_VERSION = "kb-health/1"


def _rows(value) -> list:
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value.values())
    return list(value)


def kb_health(tables, *, required_tables, rows_by_source=None, kb_version=None) -> dict:
    """对一次快照做运行期健康判定；`required_tables` 为空表才判定不可用。"""
    tables = tables or {}
    table_total = len(tables)
    empty_tables = [name for name, rows in tables.items() if len(_rows(rows)) == 0]
    required_empty = [name for name in (required_tables or ())
                      if len(_rows(tables.get(name))) == 0]

    if rows_by_source is None:
        counts = {}
        for rows in tables.values():
            for row in _rows(rows):
                if isinstance(row, dict):
                    source = str(row.get("source_type") or "").strip()
                    if source:
                        counts[source] = counts.get(source, 0) + 1
    else:
        counts = {str(key): int(value) for key, value in (rows_by_source or {}).items()}
    total_rows = sum(counts.values())
    demo_ratio = (counts.get("demo", 0) / total_rows) if total_rows else 0.0

    problems = ["required_empty:%s" % name for name in required_empty]
    return {
        "version": KB_HEALTH_VERSION,
        "table_total": table_total,
        "empty_tables": empty_tables,
        "required_empty": required_empty,
        "demo_ratio": demo_ratio,
        "kb_version": int(kb_version or 0),
        "usable_for_current_route": not required_empty,
        "problems": problems,
    }

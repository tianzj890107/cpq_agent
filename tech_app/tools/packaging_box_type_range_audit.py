# -*- coding: utf-8 -*-
"""盒型尺寸区间的只读审计（真实数据与识别覆盖批次 3，Spec §2.1）。

为什么要有它：匹配代码**已经**逐轴比 `size_*_min` / `size_*_max`（越界即硬门槛，
见 `packaging_match._dimension_size()`），但**没有任何工具**能对生产
`cpq_kb.kb_packaging_box_type` 跑同一套不变量，也分不出
「固定规格标准盒 / 可调尺寸盒型 / 只能当参考的案例」。

本工具**只输出数据结论**：不连库、不写盘、不联网；吃 JSON / 内存快照。
判据红线（Spec §2.1）：不许因为「小盒匹配大盒」就去改匹配代码。

用法：

    python tech_app/tools/packaging_box_type_range_audit.py --source box_types.json --json
    python tech_app/tools/packaging_box_type_range_audit.py --source box_types.json --strict
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

AUDIT_VERSION = "packaging-box-type-range-audit/1"

#: 三轴的 (min 键, max 键)，顺序固定。
AXES = (
    ("size_l_min", "size_l_max"),
    ("size_w_min", "size_w_max"),
    ("size_h_min", "size_h_max"),
)

#: issue 码闭集。
ISSUE_RANGE_MISSING = "range_missing"
ISSUE_RANGE_INVERTED = "range_inverted"
ISSUE_RANGE_NOT_POSITIVE = "range_not_positive"
ISSUE_CODES = (ISSUE_RANGE_MISSING, ISSUE_RANGE_INVERTED, ISSUE_RANGE_NOT_POSITIVE)

#: 分类闭集。
CLASS_FIXED = "fixed_spec"
CLASS_ADJUSTABLE = "adjustable"
CLASS_REFERENCE = "reference"
CLASSES = (CLASS_FIXED, CLASS_ADJUSTABLE, CLASS_REFERENCE)


def _num(value):
    """把单元格值读成 float；空 / 非数返回 None（不猜、不补 0）。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        text = str(value).strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None


def classify_sizes(row) -> str:
    """`fixed_spec`：三轴 min == max 且 > 0；`reference`：任一轴缺 min/max；其余 `adjustable`。"""
    row = row or {}
    pairs = []
    for min_key, max_key in AXES:
        pairs.append((_num(row.get(min_key)), _num(row.get(max_key))))
    if any(low is None or high is None for low, high in pairs):
        return CLASS_REFERENCE
    if all(low == high and low > 0 for low, high in pairs):
        return CLASS_FIXED
    return CLASS_ADJUSTABLE


def audit_box_types(rows) -> dict:
    """对盒型行跑一次性只读审计，返回分类 + issue + no_lower_bound + inverted。"""
    rows = list(rows or [])
    classes = {name: [] for name in CLASSES}
    issues = []
    no_lower_bound = []
    inverted = []
    for row in rows:
        row = row or {}
        code = str(row.get("box_type_code") or "")
        classes[classify_sizes(row)].append(code)
        missing = False
        axis_inverted = False
        not_positive = False
        for min_key, max_key in AXES:
            low = _num(row.get(min_key))
            high = _num(row.get(max_key))
            if low is None or high is None:
                missing = True
                continue
            if low <= 0 or high <= 0:
                not_positive = True
            if low > high:
                axis_inverted = True
        if _num(row.get(AXES[0][0])) is None or _num(row.get(AXES[1][0])) is None \
                or _num(row.get(AXES[2][0])) is None:
            no_lower_bound.append(code)
        if axis_inverted:
            inverted.append(code)
        issue_code = None
        detail = ""
        if missing:
            issue_code = ISSUE_RANGE_MISSING
            detail = "任一轴缺 min 或 max，只能当参考案例"
        elif axis_inverted:
            issue_code = ISSUE_RANGE_INVERTED
            detail = "任一轴 min > max，区间倒挂"
        elif not_positive:
            issue_code = ISSUE_RANGE_NOT_POSITIVE
            detail = "任一轴 min 或 max ≤ 0"
        if issue_code:
            issues.append({"box_type_code": code, "code": issue_code, "detail": detail})
    return {
        "version": AUDIT_VERSION,
        "total": len(rows),
        "classes": classes,
        "issues": issues,
        "no_lower_bound": no_lower_bound,
        "inverted": inverted,
    }


def _load_rows(source: str) -> list:
    data = json.loads(Path(source).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return list(data.get("rows") or data.get("box_types") or [])
    return list(data or [])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="盒型尺寸区间只读审计（不连库、不写盘）")
    parser.add_argument("--source", required=True, help="盒型行 JSON（列表或 {rows:[...]}）")
    parser.add_argument("--json", action="store_true", help="输出机器可读报告")
    parser.add_argument("--strict", action="store_true", help="有 issue 时以非零退出（默认永远 0）")
    args = parser.parse_args(argv)

    report = audit_box_types(_load_rows(args.source))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("盒型区间审计：共 %d 行，issue %d 条" % (report["total"], len(report["issues"])))
        for item in report["issues"]:
            print("  · %s：%s（%s）" % (item["box_type_code"], item["code"], item["detail"]))
        print("  分类：fixed_spec=%d、adjustable=%d、reference=%d"
              % (len(report["classes"][CLASS_FIXED]),
                 len(report["classes"][CLASS_ADJUSTABLE]),
                 len(report["classes"][CLASS_REFERENCE])))
    if args.strict and report["issues"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

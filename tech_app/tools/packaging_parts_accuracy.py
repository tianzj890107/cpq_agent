# -*- coding: utf-8 -*-
"""零件识别质量的度量（真实数据与识别覆盖批次 3，Spec §2.2）。

为什么要有它：全仓**没有**任何 accuracy / precision / recall / 留出图实现 —— 识别质量只有
「在位性门禁」没有「度量」。口径必须先立住（Spec §1.2）：**「有尺寸证据的比例」不是
「识别正确率」**；漏识别、误识别、归属、尺寸、证据覆盖、人工修正耗时是**六个不同指标**，
不能揉成一个数。

结构约束：本工具**只吃两个已算好的集合**（`predicted` 与 `truth`），**不接受 BOM 作为提取
输入**（BOM / 答案键只在**对答案**阶段使用，Spec §3）。

纯函数 + 只读：不连库、不写盘、不联网。

用法：

    python tech_app/tools/packaging_parts_accuracy.py --holdout <dir> --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ACCURACY_VERSION = "packaging-parts-accuracy/1"
DEFAULT_SIZE_TOLERANCE_MM = 1.0

#: 尺寸比对的键（按 Spec：只看展开长 / 宽）。
SIZE_KEYS = ("length_mm", "width_mm")

#: `summary` 里**禁止**出现的单一正确率标量（大小写不敏感的子串）。
BANNED_SUMMARY_KEYS = ("accuracy", "precision", "recall", "f1", "正确率", "准确率")


def _key(row) -> tuple:
    """匹配键：两边都有 `part_code` 时按它，否则按 `name`。"""
    row = row or {}
    code = str(row.get("part_code") or "").strip()
    if code:
        return ("part_code", code)
    return ("name", str(row.get("name") or "").strip())


def _num(value):
    if value is None or isinstance(value, bool):
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


def load_holdout(folder) -> dict:
    """读留出图答案键目录（`*.json`）。缺失 / 空目录 → `drawings == []`，不抛异常、不编造。"""
    base = Path(str(folder))
    drawings = []
    if base.is_dir():
        for path in sorted(base.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            name = str(data.get("drawing") or path.stem)
            truth = list(data.get("truth") or data.get("parts") or [])
            drawings.append({"drawing": name, "truth": truth})
    return {"version": ACCURACY_VERSION, "drawings": drawings}


def score_parts(predicted, truth, *, size_tolerance_mm=DEFAULT_SIZE_TOLERANCE_MM,
                manual_fix_minutes=None) -> dict:
    """按六个维度分开度量识别质量；`manual_fix_minutes` 原样透传，绝不由程序推算。"""
    predicted = list(predicted or [])
    truth = list(truth or [])
    tolerance = float(size_tolerance_mm)
    truth_by_key = {}
    for row in truth:
        truth_by_key.setdefault(_key(row), row)
    predicted_by_key = {}
    for row in predicted:
        predicted_by_key.setdefault(_key(row), row)

    matched_keys = [key for key in truth_by_key if key in predicted_by_key]
    missed = [truth_by_key[key] for key in truth_by_key if key not in predicted_by_key]
    spurious = [predicted_by_key[key] for key in predicted_by_key if key not in truth_by_key]

    attribution_ok = 0
    attribution_total = 0
    size_ok = 0
    size_checked = 0
    evidence_covered = 0
    for key in matched_keys:
        pred = predicted_by_key[key]
        real = truth_by_key[key]
        attribution_total += 1
        if str(pred.get("role") or "") == str(real.get("role") or "") \
                and str(pred.get("group") or "") == str(real.get("group") or ""):
            attribution_ok += 1
        lengths = [(_num(pred.get(k)), _num(real.get(k))) for k in SIZE_KEYS]
        if all(a is not None and b is not None for a, b in lengths):
            size_checked += 1
            if all(abs(a - b) <= tolerance for a, b in lengths):
                size_ok += 1
        if pred.get("size_evidence"):
            evidence_covered += 1
    evidence_total = len(matched_keys)

    summary = {
        "matched_total": len(matched_keys),
        "missed_total": len(missed),
        "spurious_total": len(spurious),
        "attribution_ok": attribution_ok,
        "attribution_total": attribution_total,
        "size_ok": size_ok,
        "size_checked": size_checked,
        "evidence_covered": evidence_covered,
        "evidence_total": evidence_total,
    }
    for key in summary:
        if any(banned in key.lower() for banned in BANNED_SUMMARY_KEYS):
            raise ValueError("summary 不得出现单一正确率标量：%s" % key)

    return {
        "version": ACCURACY_VERSION,
        "size_tolerance_mm": tolerance,
        "missed": [str(_key(row)[1]) for row in missed],
        "spurious": [str(_key(row)[1]) for row in spurious],
        "matched_total": summary["matched_total"],
        "attribution_ok": attribution_ok,
        "attribution_total": attribution_total,
        "size_ok": size_ok,
        "size_checked": size_checked,
        "evidence_covered": evidence_covered,
        "evidence_total": evidence_total,
        "manual_fix_minutes": manual_fix_minutes,
        "summary": summary,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="零件识别质量度量（只读，不连库）")
    parser.add_argument("--holdout", required=True, help="留出图答案键目录（*.json）")
    parser.add_argument("--json", action="store_true", help="输出机器可读报告")
    args = parser.parse_args(argv)

    data = load_holdout(args.holdout)
    print(json.dumps(data, ensure_ascii=False, indent=2) if args.json
          else "留出图：%d 张（%s）" % (len(data["drawings"]),
                                      "、".join(row["drawing"] for row in data["drawings"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

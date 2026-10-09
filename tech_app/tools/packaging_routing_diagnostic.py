# -*- coding: utf-8 -*-
"""工序路线的离线只读诊断（真实数据与识别覆盖批次 3，Spec §2.3）。

为什么要有它：只读桥 `da_process_routing.py` 只做「按产品编码或精确件名查询」，命中不到就如实
`unavailable`；**没有**任何只读诊断回答三件事：名称 / 产品编码 / 工序编码各自命中多少、
有多少步骤是**孤儿**（有步骤没有头）、有多少头是草稿或工时为空。

本工具吃**快照**（headers + steps），纯函数、不连库、不联网、**不自动采纳**任何模糊匹配结果。

用法：

    python tech_app/tools/packaging_routing_diagnostic.py --source routes.json --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DIAGNOSTIC_VERSION = "packaging-routing-diagnostic/1"
DEFAULT_TIME_KEYS = ("standard_seconds", "labor_seconds", "equipment_seconds")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _is_blank_time(value) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return True
    try:
        return float(value) == 0.0
    except (TypeError, ValueError):
        return not _text(value)


def diagnose_routing(headers, steps, *, time_keys=DEFAULT_TIME_KEYS) -> dict:
    """对头表 + 步骤表做只读诊断；不改入参、不猜匹配、不采纳模糊结果。"""
    headers = list(headers or [])
    steps = list(steps or [])
    time_keys = tuple(time_keys)

    codes = {_text(row.get("product_item_code")) for row in headers
             if _text(row.get("product_item_code"))}
    names = {_text(row.get("name")) for row in headers if _text(row.get("name"))}

    matched_by_code = []
    matched_by_name = []
    unmatched = []
    header_codes_matched = set()
    header_names_matched = set()
    for row in headers:
        code = _text(row.get("product_item_code"))
        name = _text(row.get("name"))
        code_hit = bool(code) and code in {_text(step.get("product_item_code")) for step in steps}
        name_hit = bool(name) and name in {_text(step.get("process_name")) for step in steps}
        if code_hit:
            matched_by_code.append(code)
            header_codes_matched.add(code)
        elif name_hit:
            matched_by_name.append(code or name)
            header_names_matched.add(name)
        else:
            unmatched.append(code or name)

    orphan_steps = []
    for step in steps:
        code = _text(step.get("product_item_code"))
        name = _text(step.get("process_name"))
        if (code and code in header_codes_matched) or (name and name in header_names_matched) \
                or (code and code in codes) or (name and name in names):
            continue
        orphan_steps.append({"product_item_code": code, "seq": step.get("seq")})

    draft_headers = [(_text(row.get("product_item_code")) or _text(row.get("name")))
                     for row in headers if _text(row.get("status")) == "draft"]

    empty_time_headers = []
    for row in headers:
        code = _text(row.get("product_item_code"))
        name = _text(row.get("name"))
        mine = [step for step in steps
                if (_text(step.get("product_item_code")) == code and code)
                or (_text(step.get("process_name")) == name and name)]
        if not mine:
            continue
        if all(all(_is_blank_time(step.get(key)) for key in time_keys) for step in mine):
            empty_time_headers.append(code or name)

    verdict = "attention" if (unmatched or orphan_steps or draft_headers) else "ok"
    return {
        "version": DIAGNOSTIC_VERSION,
        "headers_total": len(headers),
        "matched_by_code": matched_by_code,
        "matched_by_name": matched_by_name,
        "unmatched": unmatched,
        "orphan_steps": orphan_steps,
        "draft_headers": draft_headers,
        "empty_time_headers": empty_time_headers,
        "verdict": verdict,
    }


def _load_source(source: str):
    data = json.loads(Path(source).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return list(data.get("headers") or []), list(data.get("steps") or [])
    return [], []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="工序路线只读诊断（不连库、不联网）")
    parser.add_argument("--source", required=True, help="快照 JSON：{headers:[...], steps:[...]}")
    parser.add_argument("--json", action="store_true", help="输出机器可读报告")
    args = parser.parse_args(argv)

    headers, steps = _load_source(args.source)
    report = diagnose_routing(headers, steps)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("路线诊断：头 %d 行，verdict=%s" % (report["headers_total"], report["verdict"]))
        print("  按编码命中 %d、只按名称命中 %d、完全命中不到 %d、孤儿步骤 %d、草稿头 %d、工时空头 %d"
              % (len(report["matched_by_code"]), len(report["matched_by_name"]),
                 len(report["unmatched"]), len(report["orphan_steps"]),
                 len(report["draft_headers"]), len(report["empty_time_headers"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

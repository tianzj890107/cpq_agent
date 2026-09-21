#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把**权威工作簿费率**导进差异价规则表（`cpq_kb.kb_quick_quote_delta_rule`）。

背景：批 8 把「这份费率能不能用于正式报价」变成一等概念（`rule_authority()` /
`authority_summary()` / `cpq_quick_quote_price.is_formal()`），但**没有任何工具或接口**
能把权威费率导进去，也没人让 4 条 `QQQ-DEMO-*` 演示行退场 —— 于是
`authority_summary()["authoritative"]` 永远是 False，**正式快速报价在当前数据下不可达**。

三条纪律：

  · **默认 dry-run**：只读库、只打印"要写什么、要退什么、哪些不通过"，真写要显式 `--confirm`；
  · **校验只有一份**：一切判定都走 `cpq_quick_quote_workspace.rate_import_plan()`，本脚本
    不另写一份校验（也不写任何 SQL —— 落库走 `apply_rate_import_plan()`）；
  · **不编费率**：费率数值只能来自权威工作簿（`--file` 里的行），本脚本不给任何默认值。

用法：

    # 看一眼计划（默认，不写库）
    ./open-claude/.venv/bin/python scripts/import_quick_quote_rates.py --file rates.json
    ./open-claude/.venv/bin/python scripts/import_quick_quote_rates.py --file rates.csv --json

    # 真写（写权威行 + 退役演示行）
    ./open-claude/.venv/bin/python scripts/import_quick_quote_rates.py --file rates.json \
        --user zhangzhen --confirm

    # 只写权威行、保留演示行（演示行还在库里 → 永远判不了权威，仅用于演练）
    ./open-claude/.venv/bin/python scripts/import_quick_quote_rates.py --file rates.json --keep-demo

`--file` 支持 `.json`（行数组）与 `.csv`（表头即键）。每行必填键见
`cpq_quick_quote_workspace.RATE_IMPORT_REQUIRED_KEYS`（其中 `source_ref` 必须指向工作簿与工作表）。
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cpq_quick_quote_workspace as workspace                       # noqa: E402


def read_rows(path: str) -> list:
    """读导入文件：`.json` = 行数组；`.csv` = 表头即键（空单元格当空串）。"""
    text = pathlib.Path(path).read_text(encoding="utf-8-sig")
    if path.lower().endswith(".csv"):
        return [dict(row) for row in csv.DictReader(text.splitlines())]
    payload = json.loads(text)
    if isinstance(payload, dict):
        payload = payload.get("rows") or payload.get("rules") or []
    if not isinstance(payload, list):
        raise ValueError("JSON 顶层必须是行数组（或带 rows 键的对象）")
    return [dict(row) for row in payload if isinstance(row, dict)]


def describe(plan: dict, *, dry_run: bool) -> dict:
    """给工具打印用的摘要：dry_run 时只是"计划"，--confirm 后才是"结果"。"""
    return {"dry_run": bool(dry_run), "headline": plan.get("headline"),
            "detail": plan.get("detail"), "counts": plan.get("counts"),
            "write": [{"rule_code": row.get("rule_code"), "field_key": row.get("field_key"),
                       "rule_kind": row.get("rule_kind"), "unit": row.get("unit"),
                       "source_ref": row.get("source_ref")} for row in plan.get("write") or []],
            "retire": plan.get("retire") or [],
            "blocked": plan.get("blocked") or [],
            "authoritative": plan.get("authoritative")}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="把权威工作簿费率导入差异价规则表（默认 dry-run；--confirm 才写库）")
    parser.add_argument("--file", required=True, help="费率文件：.json（行数组）或 .csv（表头即键）")
    parser.add_argument("--confirm", action="store_true", help="真写库（缺省只预演，不写任何字节）")
    parser.add_argument("--keep-demo", action="store_true",
                       help="保留演示费率（等价 retire_demo=False）：演示行还在库里，判不了权威")
    parser.add_argument("--user", default="", help="留痕：谁导的（写库时记录）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是人读文本")
    args = parser.parse_args(argv)

    try:
        rows = read_rows(args.file)
    except Exception as exc:                                        # noqa: BLE001 - 统一收敛
        print("✗ 读不了费率文件 %s：%s" % (args.file, exc), file=sys.stderr)
        return 2

    try:
        plan = workspace.rate_import_plan(rows, retire_demo=not args.keep_demo)
    except workspace.CaseLibraryUnavailable as exc:
        print("✗ 费率库读不到（不回落成默认费率）：%s" % exc, file=sys.stderr)
        return 2

    summary = describe(plan, dry_run=not args.confirm)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(plan.get("headline") or "")
        print(plan.get("detail") or "")
        print("计划：写 %s 条、退 %s 条、不通过 %s 条"
              % (plan["counts"]["write"], plan["counts"]["retire"], plan["counts"]["blocked"]))
        for row in plan.get("blocked") or []:
            print("  · 第 %s 行 %s：%s（%s）"
                  % (row.get("row_index"), row.get("rule_code"), row.get("label"),
                     row.get("detail")))
        for row in plan.get("retire") or []:
            print("  - 退役演示行 %s" % row.get("rule_code"))

    blocked = list(plan.get("blocked") or [])
    if blocked:
        print("✗ 有 %d 行不通过：先修数据，本工具不忽略坏行继续写" % len(blocked),
              file=sys.stderr)
        return 1
    if not args.confirm:
        print("· dry-run：没有写任何字节。确认无误后加 --confirm 真写。")
        return 0

    try:
        result = workspace.apply_rate_import_plan(plan)
    except Exception as exc:                                        # noqa: BLE001 - 统一收敛
        print("✗ 写库失败：%s" % exc, file=sys.stderr)
        return 2
    print("· 已写 %s 条，已退役 %s 条演示行（kb_version=%s；操作人=%s）"
          % (result.get("written"), result.get("retired"), result.get("kb_version"),
             args.user or "<未填>"))
    try:
        after = workspace.authority_summary()
    except workspace.CaseLibraryUnavailable as exc:
        print("· 导入后费率仍读不到：%s" % exc, file=sys.stderr)
        return 2
    print("· 现在 authoritative=%s（%s）" % (after.get("authoritative"), after.get("headline")))
    if not after.get("authoritative"):
        print("· 还不是全权威口径：is_formal() 仍为假，正式快速报价仍不可达", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":                                          # pragma: no cover
    sys.exit(main())

# -*- coding: utf-8 -*-
"""通用演示装配：给一个项目的业务部件补演示输入，再重排 BOM → 路线 → 成本。

不针对任何一份图纸：尺寸来自这一件绑定的几何分量，材料原文来自图纸（缺失时按件名推定），
单价优先取库内材料价、取不到才按材质族给演示价，**每一条 mock 都写在成本结论里**。

用法：

    python scripts/packaging_demo_prepare.py <project_id>                      # 只算不写（干跑）
    python scripts/packaging_demo_prepare.py <project_id> --apply --actor PE1   # 写入并重算

只做三件事：写业务部件文档的演示输入（新版本，不删历史）、重排 BOM 与路线、重算成本。
不删项目、不改历史会话、不改人工确认尺寸。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_demo_inputs, packaging_bom  # noqa: E402
from tech_app.backend.services import packaging_cost, packaging_route  # noqa: E402
from tech_app.backend.storage import store  # noqa: E402


def _requirement_no(project_id: str) -> str:
    doc = store.load_requirement(project_id) or {}
    return str(doc.get("requirement_no") or "")


def _print_build(result: dict) -> None:
    print("业务部件 %d 件；几何分量 %d 个" % (result["row_total"], result["geometry_component_total"]))
    print("可装配演示输入 %d 件；无几何尺寸 %d 件；材料按相似度推荐 %d 件；"
          "材料原文缺失按件名推定 %d 件；按族演示单价 %d 件"
          % (result["ready_total"], len(result["no_size"]),
             result["material_recommended_total"], result["material_inferred_total"],
             result["demo_price_total"]))
    for row in result["rows"]:
        candidate = (row.get("recommended_material") or {})
        print("  %-14s %-16s %s×%s mm  材料=%s%s" % (
            row["business_part_code"], row.get("name") or "",
            row.get("length_mm"), row.get("width_mm"), row.get("material_text") or "（空）",
            "  ← %s（相似度 %.2f）" % (candidate.get("name"),
                                      (row.get("recommendation") or {}).get("confidence") or 0.0)
            if candidate else "  ← 库内无候选，按族演示价"))
        if row.get("ok") is False:
            print("      ! %s" % (row.get("message") or row.get("reason")))


def _print_cost(cost: dict) -> None:
    print("成本：材料 %.2f 元 / 工序 %.2f 元 / 人工 %.2f 元 / 工装 %.2f 元 / 合计 %.2f 元"
          % (cost.get("material_total") or 0, cost.get("process_total") or 0,
             cost.get("labor_total") or 0, cost.get("tooling_total") or 0,
             cost.get("total_cost") or 0))
    counts: dict = {}
    for gap in cost.get("gaps") or []:
        code = str(gap.get("code") or "").split(":", 1)[0]
        counts[code] = counts.get(code, 0) + 1
    if counts:
        print("缺口：" + "、".join("%s×%d" % (code, total)
                                  for code, total in sorted(counts.items())))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="通用演示装配（不针对任何一份图纸）")
    parser.add_argument("project_id")
    parser.add_argument("--apply", action="store_true", help="真正写入并重算（默认只干跑）")
    parser.add_argument("--actor", default="demo_prepare")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出装配结果")
    args = parser.parse_args(argv)

    project_id = args.project_id
    result = packaging_demo_inputs.build(project_id)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, default=str))
    else:
        _print_build(result)
    if not args.apply:
        print("干跑结束（未写入、未重算）：加 --apply 才会写演示输入并重排 BOM/路线/成本")
        return 0

    prepared = packaging_demo_inputs.prepare(project_id, actor=args.actor, enable_demo=True)
    print("已写入演示输入 %d 件（业务部件版本 %s）" % (prepared["written"],
                                                prepared["business_parts_id"]))
    if prepared["skipped"]:
        print("跳过的件：%s" % json.dumps(prepared["skipped"], ensure_ascii=False))

    bom = packaging_bom.build_bom(project_id)
    print("BOM 重排：%d 行（来源 %s）" % (len(bom.get("items") or []), bom.get("box_type_code") or ""))
    req_no = _requirement_no(project_id)
    try:
        packaging_route.build_route(project_id, req_no)
        route = packaging_route.confirm_route(project_id, req_no, actor={"username": args.actor})
        print("工艺路线重排并确认：%d 步" % len(route.get("steps") or []))
    except Exception as exc:                                          # noqa: BLE001
        print("工艺路线重排/确认未完成：%s（成本仍会照当前确认的那一版算）" % exc)
    cost = packaging_cost.build_cost(project_id, actor={"username": args.actor})
    _print_cost(cost)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# -*- coding: utf-8 -*-
"""从报价开始的包装 DWG 终验：验收链路 / 门禁清单 / Go-No-Go / 报告模板（唯一口径）。

为什么需要它：从报价开始的五角色交接（销售 → 工艺 → 财务 → 工艺 → 销售）此前只存在于
现场记录与会话里 —— 仓库里没有任何一处声明"这一步归谁、前置条件是什么、必须留下什么证据"。
于是验收只能靠人回忆，`dwg_deploy_gate` 那 19 项又全是**转换器侧**的（许可/版本/健康检查/
临时目录/并发/密钥…），一条都没覆盖"项目是否真的从报价开始""知识库是不是权威数据"
"stale 结果是否被当成有效报价发布"。本模块补的就是这段业务链路，且**不重复**转换器门禁。

本模块是**判定口径**，不授权部署，也不碰业务数据：

  · `STEPS` 12 步链路；`GATE_ITEMS` 10 项业务门禁；`go_no_go()` 是纯函数判定；
  · 报告模板 `REPORT_FIELDS` 与金标业务小节 `GOLDEN_BUSINESS_SECTIONS`；
  · 金标审批沿用 `dwg_acceptance` 的 `approved_by` / `approved_at` 口径，**不自动更新金标**。

`main()` 只读一份 evidence JSON 并打印判定，不连服务器、不写库、不执行任何部署命令。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MODULE_VERSION = "quote-first-acceptance/1"

#: 五角色交接链路（首尾都是销售经理）。顺序即交接顺序，不是枚举。
ROLE_CHAIN = ("sales_mgr", "process_mgr", "finance_mgr", "process_mgr", "sales_mgr")

#: 从报价开始的 12 步验收链路。no 连续、key 唯一、顺序即执行顺序。
STEPS = (
    {"no": 1, "key": "quote_create", "title": "从报价创建项目（入口分级）", "role": "sales_mgr",
     "gate": "项目入口为报价：entry_origin=quote",
     "evidence": ["business_case_id", "quote_session_id", "card_id"]},
    {"no": 2, "key": "requirement_fill", "title": "填写包装需求（64 键 / 10 必填）",
     "role": "sales_mgr", "gate": "包装必填 10 项齐",
     "evidence": ["requirement_snapshot_version", "industry"]},
    {"no": 3, "key": "box_match", "title": "盒型库五维匹配并确认候选", "role": "process_mgr",
     "gate": "盒型候选已确认（或按「没有适配」转新增工艺）",
     "evidence": ["box_type", "match_score", "source_type"]},
    {"no": 4, "key": "tech_handoff", "title": "派发「新增工艺」任务到技术工艺",
     "role": "sales_mgr", "gate": "「新增工艺」任务已派发",
     "evidence": ["source_task_id", "source_session_id", "business_case_id"]},
    {"no": 5, "key": "dwg_parse", "title": "2.1 图纸解析（DWG 原生转换 + CAD IR）",
     "role": "process_mgr", "gate": "DWG 原生解析成功，或如实失败",
     "evidence": ["converter", "converter_version", "ir_version", "three_d_status"]},
    {"no": 6, "key": "params", "title": "3.2 包装族参数（不出现电池字段）",
     "role": "process_mgr", "gate": "包装族必填齐（或签字带缺口）",
     "evidence": ["family", "required_filled", "required_total"]},
    {"no": 7, "key": "bom", "title": "拼版与参数化 BOM", "role": "process_mgr",
     "gate": "参数化 BOM 已生成且有引擎版本",
     "evidence": ["engine_version", "item_count"]},
    {"no": 8, "key": "route", "title": "包装工艺路线并冻结", "role": "process_mgr",
     "gate": "工艺路线已确认并冻结",
     "evidence": ["engine_version", "step_count", "confirmed_at"]},
    {"no": 9, "key": "cost", "title": "4.1 包装成本测算", "role": "finance_mgr",
     "gate": "成本已确认，或带缺口签字",
     "evidence": ["rule_version", "input_snapshot", "gaps"]},
    {"no": 10, "key": "report", "title": "4.2 审核并正式发布报告", "role": "process_mgr",
     "gate": "报告已审核并正式发布（stale 结果不得发布）",
     "evidence": ["report_no", "version", "published_at"]},
    {"no": 11, "key": "back_to_quote", "title": "工艺结果回传报价卡片", "role": "sales_mgr",
     "gate": "交接落回原报价卡片",
     "evidence": ["handoff_id", "quote_session_id", "business_case_id"]},
    {"no": 12, "key": "history_recover", "title": "刷新 / 重进后全量恢复", "role": "sales_mgr",
     "gate": "刷新/重进后会话、字段与证据完整恢复",
     "evidence": ["chat_turns", "field_evidence", "versions"]},
)

#: 12 步的 key（顺序即执行顺序）。判定与文档都从这份清单取，不另抄。
STEP_KEYS = tuple(str(row["key"]) for row in STEPS)

#: 终验门禁（与第 1 批的转换器门禁互补，id 不重复）。
GATE_ITEMS = (
    ("entry_from_quote", "auto", "两个真实样本项目都是从报价创建（entry_origin=quote）"),
    ("business_case_linked", "auto", "技术项目 meta 里有 business_case_id 且回传落回原卡片"),
    ("kb_authoritative", "auto", "知识库预检通过：无 demo_only / unclassified_rows"),
    ("dwg_parsed_natively", "auto", "至少一份样本由服务端转换器原生解析（不是看 PNG 猜尺寸）"),
    ("packaging_closure_complete", "auto",
     "盒型 → 参数 → BOM → 路线 → 成本五段都留下了引擎版本"),
    ("cost_traceable", "auto", "成本可解释到知识库记录与规则版本；缺口进 gaps 而不是被抹平"),
    ("stale_not_published", "auto",
     "derived_results_stale=true 时不得审核发布，除非有实名 stale waiver"),
    ("history_recovery", "auto", "刷新 / 重进 / 切换角色后会话、字段、证据、版本完整恢复"),
    ("role_chain_handoff", "manual",
     "五个角色按 ROLE_CHAIN 交接，权限错误只提示一次且不阻断合法流转"),
    ("golden_approved", "manual",
     "金标业务小节由人工复核并签字（approved_by / approved_at）"),
)
GATE_KINDS = ("auto", "manual")

#: Go/No-Go 的阻断项闭集。顺序即 `go_no_go()` 返回 blockers 的稳定顺序。
GO_BLOCKERS = ("steps_incomplete", "entry_not_from_quote", "industry_not_packaging",
               "kb_not_authoritative", "dwg_not_native", "packaging_closure_incomplete",
               "real_converter_unverified", "history_not_recovered",
               "stale_results_published", "permission_blocked")

#: 两句 claim 原文。只有全绿且金标人工审批过才许说"支持完成"，否则只能如实说"编排完成"。
CLAIM_GO = "包装行业 DWG 支持完成"
CLAIM_ORCHESTRATION = "DWG 编排能力完成，真实转换能力未验收"

#: 验收报告必须有的字段，顺序即展示顺序（每一步的证据随 steps / samples 一起留档）。
REPORT_FIELDS = ("module_version", "golden_version", "converter", "samples", "steps",
                 "gate_verdict", "blockers", "claim", "approved_by", "approved_at",
                 "rollback_plan")

#: 金标里必须由**人工**填写的业务小节（脚本只采集统计值，业务结论不许编）。
GOLDEN_BUSINESS_SECTIONS = ("unit_status", "cut_layer", "crease_layer", "box_type_candidates",
                            "key_dimensions", "pending_confirmations",
                            "forbidden_hallucinations", "downstream_snapshot",
                            "quote_draft_allowed", "three_d_status")

#: 金标审批口径沿用 dwg_acceptance（未审批的金标视为无效，且不得自动刷新快照）。
APPROVAL_FIELDS = ("approved_by", "approved_at")

#: 失败时的回滚口径（保留证据、只回退本次部署改动、不删历史数据）。
ROLLBACK_PLAN = (
    "保留已完成的门禁结果与证据（不改写、不删除）",
    "只回退本次部署改动（配置 / 容器 / 代码版本）",
    "不删除历史项目、会话、上传文件与已发布报告",
    "不得用删除数据的方式让门禁变绿",
)

#: 证据里"缺键一律按不满足处理"的布尔条件 → 对应 blocker。
_BOOL_BLOCKERS = (
    ("dwg_converted_native", "dwg_not_native"),
    ("packaging_closure", "packaging_closure_incomplete"),
    ("real_converter", "real_converter_unverified"),
    ("history_recovered", "history_not_recovered"),
    ("stale_published", "stale_results_published"),
    ("permission_ok", "permission_blocked"),
)


def _missing_blockers(evidence: dict) -> list:
    """按 GO_BLOCKERS 的顺序算出这次验收的阻断项。不改入参。"""
    if not isinstance(evidence, dict):
        return list(GO_BLOCKERS)
    hit = set()

    done = evidence.get("steps")
    done = {str(item) for item in done} if isinstance(done, (list, tuple, set)) else set()
    if [key for key in STEP_KEYS if key not in done]:
        hit.add("steps_incomplete")
    if str(evidence.get("entry_origin") or "") != "quote":
        hit.add("entry_not_from_quote")
    if str(evidence.get("industry") or "") != "packaging":
        hit.add("industry_not_packaging")

    sources = evidence.get("kb_source_types")
    sources = {str(item) for item in sources} if isinstance(sources, (list, tuple, set)) else set()
    # 只认权威来源：空集合与出现 demo 一样都不算权威（演示数据不是知识库）。
    if not sources or "demo" in sources:
        hit.add("kb_not_authoritative")

    for key, blocker in _BOOL_BLOCKERS:
        if key == "stale_published":
            # 这一项是"真为坏"：其他都是"假为坏"，混用会读反。
            if bool(evidence.get(key)):
                hit.add(blocker)
            continue
        if not bool(evidence.get(key)):
            hit.add(blocker)
    return [name for name in GO_BLOCKERS if name in hit]


def go_no_go(evidence: dict) -> dict:
    """Go/No-Go 判定：返回 {"verdict": "go"|"no_go", "blockers": [...], "claim": str}。

    纯函数：同输入同输出、不改入参、每次都返回新的 dict；不读库、不联网、不读环境变量。
    `real_converter=False`（只跑过 fake converter）时永远不是 go —— 那时唯一允许的声明是
    「DWG 编排能力完成，真实转换能力未验收」，绝不许说「支持 DWG」。
    """
    blockers = _missing_blockers(evidence)
    verdict = "go" if not blockers else "no_go"
    return {"verdict": verdict, "blockers": blockers,
            "claim": CLAIM_GO if verdict == "go" else CLAIM_ORCHESTRATION}


def report_template() -> dict:
    """验收报告骨架：字段与顺序取自 REPORT_FIELDS，值一律留空由证据填。

    只给形状，不替人下结论 —— `approved_by` / `approved_at` 必须人工签字，
    脚本不许自动填、更不许自动刷新金标。
    """
    return {name: None for name in REPORT_FIELDS}


def golden_sections() -> tuple:
    """金标里必须由人工复核的业务小节（脚本不代填）。"""
    return tuple(GOLDEN_BUSINESS_SECTIONS)


def _print_human(payload: dict) -> None:
    print(f"{MODULE_VERSION}  verdict={payload['verdict']}")
    if payload.get("blockers"):
        print("blockers: " + "、".join(payload["blockers"]))
    print("claim: " + str(payload.get("claim") or ""))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="从报价开始的包装 DWG 终验判定")
    parser.add_argument("--evidence", help="证据 JSON（缺键一律按不满足处理；只读）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--template", action="store_true", help="打印验收报告骨架")
    args = parser.parse_args(argv)

    if args.template:
        payload = {"module_version": MODULE_VERSION, "steps": list(STEP_KEYS),
                   "report_fields": list(REPORT_FIELDS),
                   "golden_business_sections": list(GOLDEN_BUSINESS_SECTIONS),
                   "rollback_plan": list(ROLLBACK_PLAN)}
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json
              else json.dumps(payload, ensure_ascii=False))
        return 0

    evidence = {}
    if args.evidence:
        try:
            evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"读不到证据文件：{exc}", file=sys.stderr)
            return 2
    elif not sys.stdin.isatty():
        try:
            evidence = json.loads(sys.stdin.read() or "{}")
        except Exception as exc:
            print(f"证据 JSON 解析失败：{exc}", file=sys.stderr)
            return 2

    result = go_no_go(evidence)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_human(result)
    return 0 if result["verdict"] == "go" else 1


if __name__ == "__main__":
    raise SystemExit(main())

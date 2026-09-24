# -*- coding: utf-8 -*-
"""cpq_kb 上线预检：缺表 / 空表 / 全是演示数据 / 未分类行 —— 任一成立即阻断上线。

为什么需要它：34 上 `relation "cpq_kb.kb_packaging_box_type" does not exist` 这类事故
不是代码 bug，是**部署漏了一步**——本地有 DDL 与 seed，线上却没建表，包装知识库检索 503、
`4.1` 成本全部失败。没有预检时，缺表也能上线，问题只在客户点下「算成本」的那一刻暴露。

因此把「快照里到底有什么」变成一条可执行、可复现的判定：判定与 IO 分离 ——
`preflight()` 是纯函数（不读库、不写库、不联网），红测直接喂 fixture；
只有 `main()` 通过 `cpq_kb.snapshot()` 取数，且**只读**。

判定（Spec §5，任一成立即 no-go）：

| code | 条件 |
| --- | --- |
| `missing_tables` | `KB_TABLES` 里有表不在快照中（缺表就是缺表，不许当空表） |
| `empty_required_table` | 关键包装表（7 张主体表）行数为 0 |
| `kb_version_missing` | `kb_version` 为 None 或 0 |
| `demo_only` | `env == "production"` 且任一关键表**全部**是 `source_type='demo'` |
| `authority_missing` | `env == "production"` 且关键表存在 `source_type='demo'` 的行、而这些行没有申报对照出处（`authority_ref` 为空） |
| `unclassified_rows` | `env == "production"` 且存在 `source_type='unknown'` 的行 |
| `box_type_missing_fit_clearance` | `env == "production"` 且 `kb_packaging_box_type` 有非 `demo` 行没登记 `fit_clearance` |
| `below_min_rows` | `--min-rows 表=下限` 指定的表行数低于下限 |

`provenance` / `unclassified_rows` **只统计 9 张包装扩展表**：其余 `kb_*` 表没有
`source_type` 列，不得因此被判成 unknown。

`demo_only` 与 `authority_missing` 的分工：一张关键表**整表都是 demo 且一行都没申报**时，
报 `demo_only`（"没用权威数据"）；部分升格、仍有没申报的 demo 行时报 `authority_missing`
（"还有没人认领的样例行"）。两者都不改 `local` / `ci` 的结论 —— 那两个环境本来就跑样例。

用法：

    python tech_app/tools/kb_deploy_preflight.py --env production
    python tech_app/tools/kb_deploy_preflight.py --env production --json
    python tech_app/tools/kb_deploy_preflight.py --env production --min-rows kb_packaging_box_type=12

退出码：0 = go；1 = no-go；2 = 取不到快照 / 参数非法。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
TECH_APP_DIR = TOOLS_DIR.parent                    # tech_app/
CPQ_DIR = TECH_APP_DIR.parent                      # 仓库根
if str(CPQ_DIR) not in sys.path:
    sys.path.insert(0, str(CPQ_DIR))

import cpq_kb  # noqa: E402  （import 期不连库，只读常量）

PREFLIGHT_VERSION = "kb-deploy-preflight/1"
ENVS = ("local", "ci", "production")

#: 数据分层的适用范围（与 cpq_kb 唯一清单同源，不另抄一份）。
PACKAGING_TABLES = tuple(cpq_kb.PACKAGING_TABLES)
#: 关键主体表：这几张空 = 知识库没灌进去。
REQUIRED_TABLES = tuple(cpq_kb.PACKAGING_REQUIRED_TABLES)

DEMO = "demo"
UNKNOWN = "unknown"

#: 盒型库统一表名（`fit_clearance` 是匹配硬门槛维度，对照表的行不许留空）。
BOX_TABLE = "kb_packaging_box_type"

EXIT_GO = 0
EXIT_NO_GO = 1
EXIT_BROKEN = 2


def _problem(code: str, table: str, message: str) -> dict:
    return {"code": code, "table": table, "message": message}


def _source_type(row) -> str:
    """取一行的 `source_type`；没有这一列的旧行按未分类处理（生产环境据此阻断）。"""
    if not isinstance(row, dict):
        return UNKNOWN
    code = str(row.get("source_type") or "").strip().lower()
    return code if code in cpq_kb.SOURCE_TYPES else UNKNOWN


def _authority_ref(row) -> str:
    """取一行的对照出处（升格时按 `cpq_kb.promote_rows` 写入）；空 = 没人认领。"""
    if not isinstance(row, dict):
        return ""
    return str(row.get("authority_ref") or "").strip()


def _box_row_identified(row) -> bool:
    """这一行是否可识别为一个盒型。

    `kb_packaging_box_type.box_type_code` 是主键（PostgreSQL 主键隐含 NOT NULL），
    真实 PG 行必有编码；没有编码的行不是盒型（形状夹具/空行），不按盒型判定
    `fit_clearance` —— 否则最小夹具也会被当成"权威盒型缺数据"而误拦上线。
    """
    return isinstance(row, dict) and bool(str(row.get("box_type_code") or "").strip())


def _blank_value(value) -> bool:
    """`fit_clearance` 之类数值列是否留空；`0` 是已登记的真实取值，不算空。"""
    if value is None:
        return True
    return not str(value).strip()


def _version_int(kb_version) -> int:
    if kb_version is None:
        return 0
    try:
        return int(kb_version)
    except (TypeError, ValueError):
        return 0


def preflight(tables: dict, *, env: str = "local", kb_version=None,
              min_rows=None) -> dict:
    """纯函数：`tables = {表名: [行, …]}`（来自 `cpq_kb.snapshot()["tables"]`）。

    不读库、不写库、不改入参；同样输入同样输出。
    """
    tables = dict(tables or {})
    env = str(env or "local").strip().lower() or "local"
    version = _version_int(kb_version)
    production = env == "production"

    counts = {name: len(tables.get(name) or []) for name in cpq_kb.KB_TABLES}
    missing_tables = [name for name in cpq_kb.KB_TABLES if name not in tables]

    provenance = {code: 0 for code in cpq_kb.SOURCE_TYPES}
    for name in PACKAGING_TABLES:
        for row in tables.get(name) or []:
            provenance[_source_type(row)] += 1

    problems: list = []
    for name in missing_tables:
        problems.append(_problem("missing_tables", name,
                                 "快照里没有 %s：缺表就是缺表，不许当成空表上线" % name))
    for name in REQUIRED_TABLES:
        if name in tables and counts[name] == 0:
            problems.append(_problem("empty_required_table", name,
                                     "%s 是 0 行：知识库没灌进去，检索与成本会全线失败" % name))
    if version <= 0:
        problems.append(_problem("kb_version_missing", "",
                                 "kb_version 为 %s：没有版本号的快照无法判断导入是否生效"
                                 % (kb_version,)))

    if production:
        for name in REQUIRED_TABLES:
            rows = tables.get(name) or []
            demo_rows = [row for row in rows if _source_type(row) == DEMO]
            if not demo_rows:
                continue
            unclaimed = [row for row in demo_rows if not _authority_ref(row)]
            if all(_source_type(row) == DEMO for row in rows) and len(unclaimed) == len(rows):
                problems.append(_problem("demo_only", name,
                                         "%s 全是演示数据且没有一行申报对照出处："
                                         "生产库不得用演示盒型/工艺/费率报价" % name))
            elif unclaimed:
                problems.append(_problem("authority_missing", name,
                                         "%s 有 %d 行还是演示数据且没有申报对照出处"
                                         "（authority_ref 为空）：生产库不得用没人认领的样例数据报价"
                                         % (name, len(unclaimed))))
        if provenance[UNKNOWN] > 0:
            problems.append(_problem("unclassified_rows", "",
                                     "有 %d 行 source_type='unknown'：生产库不得含未分类数据"
                                     % provenance[UNKNOWN]))
        gap_rows = [row for row in (tables.get(BOX_TABLE) or [])
                    if _box_row_identified(row) and _source_type(row) != DEMO
                    and _blank_value(row.get("fit_clearance"))]
        if gap_rows:
            problems.append(_problem(
                "box_type_missing_fit_clearance", BOX_TABLE,
                "%s 有 %d 条权威盒型（source_type != demo）没登记配合间隙（fit_clearance 为空）："
                "匹配时该维不计分、这类盒型也排不到前面；"
                "补齐数据或走图纸确认流程后重导，样例行不受此限"
                % (BOX_TABLE, len(gap_rows))))

    for name, floor in dict(min_rows or {}).items():
        floor = _version_int(floor)
        if counts.get(name, 0) < floor:
            problems.append(_problem("below_min_rows", name,
                                     "%s 只有 %d 行，低于要求的 %d 行"
                                     % (name, counts.get(name, 0), floor)))

    ok = not problems
    return {"ok": ok, "verdict": "go" if ok else "no-go", "env": env,
            "kb_version": version, "missing_tables": missing_tables,
            "counts": counts, "provenance": provenance, "problems": problems}


def load_snapshot() -> dict:
    """唯一取数点：只读快照（`cpq_kb.snapshot()` 绝不回落空表）。"""
    return cpq_kb.snapshot()


def parse_min_rows(items) -> dict:
    """`["kb_packaging_box_type=12", …]` → `{"kb_packaging_box_type": 12}`。"""
    out: dict = {}
    for item in items or []:
        text = str(item or "").strip()
        if not text:
            continue
        name, _, value = text.partition("=")
        name, value = name.strip(), value.strip()
        if not name or not value:
            raise ValueError("--min-rows 需要「表名=下限」形式：%s" % text)
        try:
            out[name] = int(value)
        except ValueError:
            raise ValueError("--min-rows 的下限必须是整数：%s" % text) from None
    return out


def _print_report(report: dict) -> None:
    print("预检 %s：env=%s kb_version=%s" % (PREFLIGHT_VERSION, report["env"],
                                            report["kb_version"]))
    print("表行数（%d 张）：%s" % (len(report["counts"]),
                                ", ".join("%s=%d" % (k, v)
                                          for k, v in report["counts"].items()
                                          if v) or "（全空）"))
    print("包装表来源分层：%s" % ", ".join("%s=%d" % (k, v)
                                          for k, v in report["provenance"].items()))
    for problem in report["problems"]:
        print("  [%s] %s: %s" % (problem["code"], problem["table"] or "-",
                                 problem["message"]))
    print("结论：%s（%s）" % (report["verdict"], "可上线" if report["ok"] else "禁止上线"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="cpq_kb 上线预检（只读快照，缺表/空表/演示数据/未分类行即 no-go）")
    parser.add_argument("--env", default="local", choices=ENVS,
                        help="判定环境：local / ci / production")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="以 JSON 输出（给 CI 与部署脚本用）")
    parser.add_argument("--min-rows", action="append", default=[], metavar="TABLE=N",
                        help="额外下限，可重复：--min-rows kb_packaging_box_type=12")
    args = parser.parse_args(argv)

    try:
        floors = parse_min_rows(args.min_rows)
    except ValueError as exc:
        print(str(exc))
        return EXIT_BROKEN

    try:
        snap = load_snapshot()
    except Exception as exc:                           # noqa: BLE001 - 取数失败一律 no-go
        print("取不到 cpq_kb 快照：%s" % str(exc).splitlines()[0][:200])
        return EXIT_BROKEN

    report = preflight(snap.get("tables") or {}, env=args.env,
                       kb_version=snap.get("kb_version"), min_rows=floors)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_report(report)
    return EXIT_GO if report["ok"] else EXIT_NO_GO


if __name__ == "__main__":
    sys.exit(main())

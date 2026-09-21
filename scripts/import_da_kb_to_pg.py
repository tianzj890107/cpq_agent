#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把技术工艺本地那份 `da.db` 里的知识库（`kb_*` 全表）搬进 CPQ 的 Postgres `cpq_kb`。

规范见 docs/specs/kb-in-pg-http-snapshot.md（契约 C3）。三条硬要求：

  · **默认 dry-run**：只统计要搬多少行，不写库、不连 PG —— "看一眼计划"不该因为
    Postgres 连不上而失败（先例见 scripts/push_remotes.py --check）；真写要显式 `--confirm`；
  · **源库只读**：一律以 `sqlite3.connect("file:<path>?mode=ro", uri=True)` 打开
    （见 cpq_kb._read_source）。以可写方式打开 WAL 库，进程一关闭就会把 -wal 合并进
    主体并删掉 -wal/-shm —— 分析/搬运工具绝不能改别人的库。本脚本在导入前后各算一次
    源文件 sha256，不一致就直接报错；
  · **幂等 + 单事务**：按主键 upsert，连续跑两次行数不变、`kb_version` 只在确有变化
    时 +1（由 cpq_kb.import_from_sqlite 保证）。

第四步（可选）是**权威升格**：seed 固化下来的行缺省是 `source_type='demo'`，
直接灌生产会被 `kb_deploy_preflight` 的 `demo_only` / `authority_missing` 拦下。把业务确认过的
出处（工作簿/工作表/负责人/确认日期/sha256）写进 `tech_app/agent_knowledge/provenance/
packaging_sources.json`，再带 `--promote workbook --authority-file <json>` 灌库，
导入前按 `cpq_kb.promote_rows` 把 demo 行升格为权威行（出处逐表取自该文件）。
升格只改来源列，业务数值逐字不动；没有出处文件的表不会被升格，也不会被静默放行。

用法：

    python scripts/import_da_kb_to_pg.py --source tech_app/tech_data/da.db --dry-run --json
    python scripts/import_da_kb_to_pg.py --source tech_app/tech_data/da.db --confirm
    python scripts/import_da_kb_to_pg.py --source tech_app/tech_data/da.db \
        --promote workbook \
        --authority-file tech_app/agent_knowledge/provenance/packaging_sources.json --confirm
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:                     # 直接以脚本方式运行时（sys.path[0] 是 scripts/）
    sys.path.insert(0, str(ROOT))

import cpq_kb                                    # noqa: E402

DEFAULT_SOURCE = ROOT / "tech_app" / "tech_data" / "da.db"

#: 本地与线上的 `DATA_DIR` 不是同一个目录，`da.db` 因此可能有两份（实测都出现过）：
#: · `tech_app/tech_data/da.db` —— `tech_app_launch.py` 设的 `DATA_DIR`，线上 34 就是这份；
#: · `tech_app/data/da.db` —— `backend.config.DATA_DIR` 的本地默认值，直接跑
#:   `python -m backend.storage.da_seed_packaging` 会写这份。
#: seed 与导入必须指同一个文件，否则会出现"本地明明 seed 过、导入却是 0 行"的假象。
#: 这里只做**提示**，不替使用者猜：两边都可能是对的（本地调试 vs 线上发布）。
SOURCE_CANDIDATES = (ROOT / "tech_app" / "tech_data" / "da.db",
                     ROOT / "tech_app" / "data" / "da.db")


def alternative_source(source: pathlib.Path, tables: dict) -> str:
    """源库一行包装数据都没有时，指出另一份 `da.db` 是不是有数据（只读库文件）。"""
    if sum(int(tables.get(name) or 0)
           for name in cpq_kb.PACKAGING_TABLES) > 0:
        return ""
    import sqlite3
    for candidate in SOURCE_CANDIDATES:
        if candidate == source or not candidate.exists():
            continue
        try:
            con = sqlite3.connect("file:%s?mode=ro" % candidate, uri=True)
            try:
                names = {row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                rows = sum(con.execute("SELECT COUNT(*) FROM %s" % name).fetchone()[0]
                           for name in cpq_kb.PACKAGING_TABLES if name in names)
            finally:
                con.close()
        except Exception:                                                # noqa: BLE001
            continue
        if rows:
            return ("源库 %s 里没有任何包装表数据，但 %s 有 %d 行；"
                    "请加 --source %s" % (source, candidate.relative_to(ROOT), rows,
                                          candidate.relative_to(ROOT)))
    return ""


def sha256_of(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _print_source_types(source_types: dict) -> None:
    """打印 9 张包装表的 source_type 分布：demo 还剩几行一眼可见（升格是否生效）。"""
    rows = {name: bucket for name, bucket in (source_types or {}).items() if bucket}
    if not rows:
        print("包装表来源分层：（快照里没有 source_type 列的表，或这次没有升格）")
        return
    print("包装表来源分层：")
    for name, bucket in rows.items():
        detail = ", ".join(f"{code}={count}" for code, count in sorted(bucket.items()))
        print(f"  {name:<32} {detail}")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 da.db 的 kb_* 全表导入 CPQ 的 cpq_kb（默认 dry-run，不写库）")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE),
                        help="源库路径（SQLite da.db）；只读打开，不会被改写")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="只统计不写库（默认行为；未给 --confirm 时恒为真）")
    parser.add_argument("--confirm", action="store_true",
                        help="真正写库（单事务 + 按主键 upsert，幂等）")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="最后一行输出机器可读的 JSON 统计")
    parser.add_argument("--promote", default="", metavar="TARGET",
                        help="灌库前升格来源分层（当前只支持 workbook）：demo → 权威行")
    parser.add_argument("--authority-file", default="", metavar="JSON",
                        help="权威出处文件（包装表逐表取出处；配合 --promote 使用）")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    source = pathlib.Path(args.source).expanduser()
    if not source.exists():
        print(f"源库不存在：{source}", file=sys.stderr)
        return 2

    promote = str(args.promote or "").strip()
    authority = None
    if promote:
        if not str(args.authority_file or "").strip():
            print("--promote 必须同时给 --authority-file（权威出处由文件提供，不许手打）",
                  file=sys.stderr)
            return 2
        authority_path = pathlib.Path(args.authority_file).expanduser()
        if not authority_path.exists():
            print(f"出处文件不存在：{authority_path}", file=sys.stderr)
            return 2
        try:
            authority = json.loads(authority_path.read_text(encoding="utf-8"))
        except Exception as exc:                                         # noqa: BLE001
            print(f"出处文件不是合法 JSON：{type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
    elif str(args.authority_file or "").strip():
        print("--authority-file 只在配合 --promote 时使用", file=sys.stderr)
        return 2

    # 未给 --confirm 时 dry_run 必须为真：默认只读、绝不写库。
    dry_run = not args.confirm
    before = sha256_of(source)
    try:
        result = cpq_kb.import_from_sqlite(str(source), confirm=not dry_run,
                                           promote=promote, authority=authority)
    except Exception as exc:                                             # noqa: BLE001
        print(f"导入失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    after = sha256_of(source)
    if before != after:
        print(f"源库被改动了（sha256 {before[:12]} -> {after[:12]}）："
              "导入器必须以只读方式打开源库", file=sys.stderr)
        return 1

    report = {
        "ok": bool(result.get("ok")),
        "dry_run": bool(result.get("dry_run")),
        "source": str(source),
        "source_sha256": after,
        "source_unchanged": True,
        "kb_version": int(result.get("kb_version") or 0),
        "tables": result.get("tables") or {},
        "rows": int(result.get("rows") or 0),
    }
    if "changed" in result:
        report["changed"] = int(result.get("changed") or 0)
    if "promote" in result:
        report["promote"] = str(result.get("promote") or "")
    if "source_types" in result:
        report["source_types"] = result.get("source_types") or {}

    if not args.as_json:
        mode = "dry-run（不写库）" if report["dry_run"] else "确认写库"
        print(f"源库：{source}")
        print(f"模式：{mode}；schema={cpq_kb.SCHEMA}")
        for table in cpq_kb.KB_TABLES:
            print(f"  {table:<32} {report['tables'].get(table, 0):>6}")
        hint = alternative_source(source, report["tables"])
        if hint:
            print(hint)
        print(f"合计 {report['rows']} 行；kb_version={report['kb_version']}")
        if report.get("promote"):
            print(f"权威升格：{report['promote']}（出处取自 --authority-file）")
        _print_source_types(report.get("source_types") or {})
        if report["dry_run"]:
            print("这是计划，没有写库。确认无误后加 --confirm 再跑一次。")
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

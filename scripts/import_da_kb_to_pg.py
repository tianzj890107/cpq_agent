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

用法：

    python scripts/import_da_kb_to_pg.py --source tech_app/tech_data/da.db --dry-run --json
    python scripts/import_da_kb_to_pg.py --source tech_app/tech_data/da.db --confirm
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


def sha256_of(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    source = pathlib.Path(args.source).expanduser()
    if not source.exists():
        print(f"源库不存在：{source}", file=sys.stderr)
        return 2

    # 未给 --confirm 时 dry_run 必须为真：默认只读、绝不写库。
    dry_run = not args.confirm
    before = sha256_of(source)
    try:
        result = cpq_kb.import_from_sqlite(str(source), confirm=not dry_run)
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

    if not args.as_json:
        mode = "dry-run（不写库）" if report["dry_run"] else "确认写库"
        print(f"源库：{source}")
        print(f"模式：{mode}；schema={cpq_kb.SCHEMA}")
        for table in cpq_kb.KB_TABLES:
            print(f"  {table:<32} {report['tables'].get(table, 0):>6}")
        print(f"合计 {report['rows']} 行；kb_version={report['kb_version']}")
        if report["dry_run"]:
            print("这是计划，没有写库。确认无误后加 --confirm 再跑一次。")
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

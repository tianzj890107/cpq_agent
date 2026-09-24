#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统 TMPDIR 里的本地测试垃圾：报告 / 真删。

背景（2026-09-24 本机只读实测）：全量一次在系统 TMPDIR 里新建 1371 个临时目录，攒了
335 349 个条目 / 123 GB。运行期的修法在 `tests/_tmp_guard.py`（一次运行一个
`cpq-testrun-XXXX` 根，退出整根删）。本脚本管**没人删的那些**：

  · 被 SIGKILL / 断电打断的运行留下的 `cpq-testrun-*` 根；
  · 闸门装上之前攒下的历史垃圾（无前缀 `tmp########`、`cpq-kb-script-*` …）；
  · 子进程绕过根直接写系统 TMPDIR 的边角。

三条纪律（顺序即优先级）：

  1. **默认只报告，不删** —— 真删要显式 `--apply`；
  2. **只在自己认得名字的家族里动手** —— 家族清单从源码里的 `mkdtemp(prefix="…")` 字面量
     派生（`tests/` + `tech_app/` + `scripts/` + 根目录 `*.py`），不手抄；无前缀
     `tmp########` 这一类额外要求"里面确实躺着测试载荷"（见 `EVIDENCE_*`），否则不认；
  3. **只删够旧的**（默认 6 小时前）—— 正在跑的会话不动。

用法：

    python3 scripts/reclaim_test_tmpdirs.py                      # 报告（默认）
    python3 scripts/reclaim_test_tmpdirs.py --apply              # 真删（6 小时前的）
    python3 scripts/reclaim_test_tmpdirs.py --apply --older-than 30m
    python3 scripts/reclaim_test_tmpdirs.py --apply --include-empty   # 连空的 tmp######## 一起收
    python3 scripts/reclaim_test_tmpdirs.py --root /tmp          # 换根（默认系统 TMPDIR）

只读报告不碰任何文件；`--apply` 只删 `os.scandir(根)` 的直接子目录，**不跟随符号链接**，
不删文件，不递归找别处的家族。
"""

import argparse
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

#: `--older-than` 的单位（不写单位按**小时**算）。
AGE_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
DEFAULT_OLDER_THAN = "6h"

#: 无前缀 `tempfile.mkdtemp()` 生成的名字：`tmp` + `_RandomNameSequence` 的 8 位。
BARE_RE = re.compile(r"^tmp[a-z0-9_]{8}$")

#: 从源码里抠 `mkdtemp(prefix="…")` 的字面量。
PREFIX_RE = re.compile(r"""mkdtemp\(\s*prefix\s*=\s*["']([^"']+)["']""")

#: 前缀只收"像目录名"的：文档里抄的 `"…"` 这类占位符挡在外面。
PREFIX_OK_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: 闸门那个根的名字也在源码里（不 import `tests` 包 —— 一 import 就把本进程的 TMPDIR 搬走了）。
RUN_ROOT_RE = re.compile(r"""RUN_ROOT_PREFIX\s*=\s*["']([^"']+)["']""")

#: 扫描哪些地方找前缀。
SCAN_DIRS = ("tests", "tech_app", "scripts")

#: 名字兜底家族（源码里搜不到、但确实是我们的）。
FAMILY_EXTRA = ("cpq-testrun-",)

#: 认“这条 `tmp########` 是我们建的”的载荷证据 —— 从实测样本里归纳的，**宁可漏删不可误删**。
EVIDENCE_SUFFIXES = (".sqlite3", ".db", ".dxf", ".stl")
EVIDENCE_NAMES = ("dataset", "work", "input", "output", "cases.json")
EVIDENCE_PREFIXES = ("pkg", "packaging-", "real")

#: store 的桶目录（`parts00001` / `semi00000001` / `pkgbom000001` 同形）：小写词 + 序号。
EVIDENCE_BUCKET_RE = re.compile(r"^[a-z][a-z0-9_]*\d{4,}$")


def repo_root():
    return Path(__file__).resolve().parents[1]


def root_is_safe(root, *, home=None, repo=None):
    """根不能在"删了会出人命"的位置上（仓库根 / home / `/` 及其上级都不许当清理根）。

    这条是 `AGENTS.md` 的删除纪律落到本脚本上的样子：只允许系统临时目录那种"丢了不影响
    任何持久化数据"的位置；`--root` 是给人换根用的，不是给递归删除用的。
    """
    resolved = Path(root).resolve()
    forbidden = {Path("/").resolve(), Path(home or Path.home()).resolve(),
                 Path(repo or repo_root()).resolve()}
    for item in forbidden:
        if resolved == item or item.is_relative_to(resolved):
            return False
    return True


def parse_age(text):
    """`6h` / `30m` / `90s` / `2d` / `6` → 秒（不写单位按小时）。"""
    raw = str(text).strip().lower()
    if not raw:
        return int(DEFAULT_OLDER_THAN.rstrip("h")) * 3600
    unit = raw[-1]
    if unit in AGE_UNITS:
        return int(float(raw[:-1]) * AGE_UNITS[unit])
    return int(float(raw) * AGE_UNITS["h"])


def _source_files(root):
    files = []
    for name in SCAN_DIRS:
        base = root / name
        if base.is_dir():
            files.extend(sorted(base.rglob("*.py")))
    files.extend(sorted(root.glob("*.py")))
    return files


def _read_text(path):
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def families(root=None):
    """家族清单：源码里的前缀 + 闸门根 + 兜底。返回 (前缀元组, 来源计数)。"""
    root = Path(root) if root else repo_root()
    found = set()
    hits = 0
    for path in _source_files(root):
        text = _read_text(path)
        for literal in PREFIX_RE.findall(text):
            prefix = literal.split("%s")[0]           # `cpq-b9-%s-script-` 这种模板取前半段
            if prefix and PREFIX_OK_RE.match(prefix):
                found.add(prefix)
                hits += 1
        for literal in RUN_ROOT_RE.findall(text):
            if literal:
                found.add(literal)
                hits += 1
    found.update(FAMILY_EXTRA)
    return tuple(sorted(found)), hits


def family_of(name, family_list):
    """这条目属于哪个前缀家族（最长前缀优先，避免 `cpq-kb-` 抢走 `cpq-kb-script-`）。"""
    matched = ""
    for prefix in family_list:
        if name.startswith(prefix) and len(prefix) > len(matched):
            matched = prefix
    return matched or ""


def has_payload(path):
    """这条无前缀目录里有没有我们认得的东西（临时 SQLite / 夹具 / gate 输出……）。"""
    try:
        names = os.listdir(path)
    except OSError:
        return False
    for name in names:
        if name.endswith(EVIDENCE_SUFFIXES) or name in EVIDENCE_NAMES:
            return True
        for prefix in EVIDENCE_PREFIXES:
            if name.startswith(prefix):
                return True
        if EVIDENCE_BUCKET_RE.match(name):
            return True
    return False


def verdict(name, *, family_list, now, older_than, is_dir, is_symlink, mtime,
            payload=False, allow_empty=False):
    """纯判据：返回 `(家族, 原因)`；家族非空 = 可以删。

    分开成纯函数是为了让 `tests/test_local_tmp_cleanup_guard.py` 直接钉住这几条边界，
    不用真的去动文件系统。
    """
    if is_symlink:
        return "", "symlink"
    if not is_dir:
        return "", "not_a_dir"
    too_new = mtime is None or (now - mtime) < older_than
    family = family_of(name, family_list)
    if family:
        return ("", "too_new") if too_new else (family, "prefixed")
    if BARE_RE.match(name):
        if too_new:
            return "", "too_new"
        if payload:
            return "tmp########", "bare_with_payload"
        return ("tmp########", "bare_empty") if allow_empty else ("", "bare_without_payload")
    return "", "unknown_family"


def dir_size(path):
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            entries = os.scandir(current)
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        total += entry.stat(follow_symlinks=False).st_size
                except OSError:
                    continue
    return total


def human(num):
    value = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return "%.1f %s" % (value, unit)
        value /= 1024.0
    return "%.1f TB" % value


def collect(root, family_list, *, older_than, allow_empty=False, now=None):
    """扫根下的直接子项，返回 (候选, 跳过原因计数, 检查过的条目数)。"""
    now = time.time() if now is None else now
    candidates = []
    skipped = {}
    seen = 0
    with os.scandir(root) as entries:
        for entry in entries:
            seen += 1
            try:
                is_symlink = entry.is_symlink()
                is_dir = entry.is_dir(follow_symlinks=False)
                mtime = None if is_symlink else entry.stat(follow_symlinks=False).st_mtime
            except OSError:
                skipped["stat_failed"] = skipped.get("stat_failed", 0) + 1
                continue
            payload = has_payload(entry.path) if (is_dir and not is_symlink
                                                 and BARE_RE.match(entry.name)) else False
            family, reason = verdict(entry.name, family_list=family_list, now=now,
                                     older_than=older_than, is_dir=is_dir,
                                     is_symlink=is_symlink, mtime=mtime,
                                     payload=payload, allow_empty=allow_empty)
            if not family:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            candidates.append((entry.path, entry.name, family))
    return candidates, skipped, seen


def main(argv=None):
    parser = argparse.ArgumentParser(description="收系统 TMPDIR 里的本地测试垃圾（默认只报告）")
    parser.add_argument("--apply", action="store_true", help="真删；不加就是报告")
    parser.add_argument("--older-than", default=DEFAULT_OLDER_THAN,
                        help="只动这个时间之前没被动过的（默认 6h；支持 30m / 90s / 2d）")
    parser.add_argument("--root", default="", help="要清理的根（默认系统 TMPDIR）")
    parser.add_argument("--include-empty", action="store_true",
                        help="连空的 tmp######## 目录一起收（默认只收里面有测试载荷的）")
    parser.add_argument("--top", type=int, default=5, help="报告里列几个最大的（默认 5）")
    parser.add_argument("--no-size", action="store_true", help="不统计体积（大 backlog 时快很多）")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser() if args.root else Path(tempfile.gettempdir())
    if not root.is_dir():
        print("根不存在：%s" % root, file=sys.stderr)
        return 2
    if not root_is_safe(root):
        print("拒绝在这个根上动手（仓库根 / home / 系统根及其上级都不许当清理根）：%s" % root,
              file=sys.stderr)
        return 2
    older_than = parse_age(args.older_than)
    family_list, hits = families()

    print("根：%s" % root)
    print("家族清单：%d 个（从源码 %d 处字面量派生）" % (len(family_list), hits))
    print("阈值：%s 之前没被动过（%.1f 小时）" % (args.older_than, older_than / 3600.0))
    print("统计中……（只扫直接子目录，逐个判名字与年龄）")
    now = time.time()
    candidates, skipped, seen = collect(root, family_list, older_than=older_than,
                                       allow_empty=args.include_empty, now=now)

    sizes = {}
    if not args.no_size:
        for path, name, family in candidates:
            sizes[path] = dir_size(path)
    total_bytes = sum(sizes.values())

    by_family = {}
    for path, name, family in candidates:
        count, size = by_family.get(family, (0, 0))
        by_family[family] = (count + 1, size + sizes.get(path, 0))
    print("\n候选：%d 个目录 / %s" % (len(candidates), human(total_bytes)))
    for family, (count, size) in sorted(by_family.items(), key=lambda kv: -kv[1][1])[:20]:
        print("  %-22s %7d 个  %10s" % (family, count, human(size)))
    if skipped:
        print("  跳过：" + "、".join("%s %d" % (k, v) for k, v in sorted(skipped.items())))

    if candidates and not args.no_size and args.top > 0:
        print("\n最大的 %d 个：" % args.top)
        for path, name, family in sorted(candidates, key=lambda c: -sizes.get(c[0], 0))[:args.top]:
            print("  %10s  %s" % (human(sizes.get(path, 0)), name))

    if not args.apply:
        print("\n这是报告（没删任何东西）。真删：加 --apply")
        return 0

    freed = 0
    removed = 0
    failed = 0
    for path, name, family in candidates:
        existed = os.path.exists(path)
        try:
            shutil.rmtree(path, ignore_errors=False)
        except OSError:
            failed += 1
            continue
        if existed:
            removed += 1
            freed += sizes.get(path, 0)
    print("\n已删：%d 个目录 / %s（失败 %d 个）" % (removed, human(freed), failed))
    if failed:
        print("失败的下一轮再跑一次即可（多半是里面还有活进程写文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

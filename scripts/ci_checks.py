#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 本地检查：全量一方 Python 语法 + 空白 / 冲突标记（发布保障批次 2，Spec §2.5）。

为什么要有它：`.gitlab-ci.yml` 的 `python_contract` 原来只 `py_compile` 4 个文件
（仓库一方 Python 共 660 个），也没有空白检查。本脚本把语法检查扩到全部一方 Python，
并补上等价于 `git diff --check` 的空白 / 冲突标记检查。**纯本地、不联网**。

公开 API（红测直接 pin，CLI 只是包装）：

  · `compile_all(roots)` → `{"ok": bool, "checked": int, "failures": [path]}`
  · `whitespace(base=None, cwd=None)` → `{"ok": bool, "offenders": ["file:line", ...]}`

用法：

    python scripts/ci_checks.py --compile-all --json
    python scripts/ci_checks.py --compile-all --whitespace --base "$CI_MERGE_REQUEST_TARGET_BRANCH_NAME"
    python scripts/ci_checks.py --whitespace            # 不给 --base 时退化为工作区差异
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 不带 `--root` 时的四个默认根（一方 Python）。
DEFAULT_ROOTS = ("cpq_*.py", "tech_app", "scripts", "tests")

#: 遍历时跳过的目录名（第三方 / 缓存 / 版本控制）。
SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", "node_modules",
             ".mypy_cache", ".pytest_cache", ".ruff_cache"}

_OFFENDER_RE = re.compile(r"^(.*?):(\d+):")


def _iter_py(target) -> list:
    """把一个文件 / 目录展开成 `*.py` 路径列表（目录递归，`__pycache__` 等跳过）。"""
    path = Path(str(target))
    if path.is_file():
        return [path] if path.suffix == ".py" else []
    if not path.is_dir():
        return []
    found = []
    for dirpath, dirnames, filenames in os.walk(str(path)):
        dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
        for filename in filenames:
            if filename.endswith(".py"):
                found.append(Path(dirpath) / filename)
    return sorted(found)


def resolve_roots(roots=None) -> list:
    """把 CLI 给的根（可含通配符）解析成绝对路径列表；未给则用四个默认根。"""
    entries = list(roots) if roots else list(DEFAULT_ROOTS)
    resolved = []
    for entry in entries:
        text = str(entry)
        if any(char in text for char in "*?["):
            resolved.extend(sorted(str(path) for path in REPO_ROOT.glob(text)))
        else:
            path = Path(text)
            resolved.append(str(path if path.is_absolute() else REPO_ROOT / path))
    return resolved


def compile_all(roots) -> dict:
    """对一组根做语法编译；返回 `{"ok", "checked", "failures"}`。"""
    failures = []
    checked = 0
    for root in roots:
        for path in _iter_py(root):
            checked += 1
            try:
                source = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                failures.append("%s: %s" % (path, exc))
                continue
            try:
                compile(source, str(path), "exec")
            except SyntaxError as exc:
                failures.append("%s:%s: %s" % (path, exc.lineno, exc.msg))
    return {"ok": not failures, "checked": checked, "failures": failures}


def whitespace(base=None, cwd=None) -> dict:
    """等价 `git diff --check <base>...HEAD`；`base` 为空则退化为工作区差异。"""
    workdir = str(cwd or REPO_ROOT)
    base_text = str(base or "").strip()
    command = ["git", "diff", "--check"]
    if base_text:
        command.append("%s...HEAD" % base_text)
    proc = subprocess.run(command, cwd=workdir, capture_output=True, text=True)
    offenders = []
    for line in ((proc.stdout or "") + (proc.stderr or "")).splitlines():
        match = _OFFENDER_RE.match(line)
        if match:
            offenders.append("%s:%s" % (match.group(1), match.group(2)))
    return {"ok": proc.returncode == 0 and not offenders, "offenders": offenders}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="CI 本地检查（编译 + 空白，纯本地）")
    parser.add_argument("--compile-all", action="store_true", help="对全部一方 Python 做语法编译")
    parser.add_argument("--root", action="append", default=None,
                        help="覆盖默认根（可重复；支持通配符）")
    parser.add_argument("--whitespace", action="store_true", help="检查空白 / 冲突标记")
    parser.add_argument("--base", default=None,
                        help="diff 基准 ref；为空则检查工作区差异")
    parser.add_argument("--json", action="store_true", help="输出机器可读汇总")
    args = parser.parse_args(argv)

    do_compile = args.compile_all
    do_whitespace = args.whitespace
    if not do_compile and not do_whitespace:
        do_compile = do_whitespace = True

    report = {}
    exit_code = 0
    if do_compile:
        result = compile_all(resolve_roots(args.root))
        report["compile"] = result
        if not result["ok"]:
            exit_code = 1
            for item in result["failures"]:
                print("语法错误：%s" % item, file=sys.stderr)
    if do_whitespace:
        result = whitespace(args.base)
        report["whitespace"] = result
        if not result["ok"]:
            exit_code = 1
            for item in result["offenders"]:
                print("空白错误：%s" % item, file=sys.stderr)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        if "compile" in report:
            print("语法检查：%d 个文件，%s"
                  % (report["compile"]["checked"],
                     "通过" if report["compile"]["ok"] else "失败"))
        if "whitespace" in report:
            print("空白检查：%s" % ("通过" if report["whitespace"]["ok"] else "失败"))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

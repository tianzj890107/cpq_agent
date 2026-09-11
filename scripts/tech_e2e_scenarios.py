#!/usr/bin/env python3
"""技术工艺端到端场景 runner（第 22 步）。

只读 docs/specs/tech-agent-recovery-22-e2e-scenarios.json：
  --list        列出全部场景的 id / title / depends_on，退出码 0
  --run <id>    运行该场景 automated 里的测试模块，并打印 manual 人工步骤；
                任一自动化失败则退出码非 0

本脚本只读清单、不改业务实现、不启动服务、不写生产数据、不调用删除 / 重置接口。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "specs" / "tech-agent-recovery-22-e2e-scenarios.json"


def load_manifest() -> dict:
    if not MANIFEST.exists():
        raise SystemExit(f"缺少场景清单：{MANIFEST.relative_to(ROOT)}")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def scenarios() -> list:
    return [s for s in (load_manifest().get("scenarios") or []) if isinstance(s, dict)]


def find(scenario_id: str) -> Optional[dict]:
    for item in scenarios():
        if item.get("id") == scenario_id:
            return item
    return None


def to_module(rel: str) -> str:
    path = rel[:-3] if rel.endswith(".py") else rel
    return path.replace("/", ".")


def cmd_list() -> int:
    for item in scenarios():
        deps = ",".join(str(d) for d in (item.get("depends_on") or []))
        print(f"{item.get('id')}\t{item.get('title')}\tdepends_on=[{deps}]")
    return 0


def cmd_run(scenario_id: str) -> int:
    item = find(scenario_id)
    if item is None:
        print(f"未知场景：{scenario_id}", file=sys.stderr)
        return 2
    print(f"=== {item.get('id')} {item.get('title')} ===")
    expected = item.get("expected")
    if expected:
        print(f"预期：{expected}")
    manual = item.get("manual") or []
    if manual:
        print("人工步骤：")
        for index, step in enumerate(manual, 1):
            print(f"  {index}. {step}")
    modules = [to_module(rel) for rel in (item.get("automated") or [])]
    if not modules:
        print("（该场景没有自动化用例，仅人工验收。）")
        return 0
    print("运行自动化：" + " ".join(modules))
    proc = subprocess.run([sys.executable, "-m", "unittest", *modules], cwd=str(ROOT))
    if proc.returncode != 0:
        print(f"场景 {item.get('id')} 自动化失败。", file=sys.stderr)
        return proc.returncode
    print(f"场景 {item.get('id')} 自动化通过。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="技术工艺端到端场景 runner")
    parser.add_argument("--list", action="store_true", help="列出全部场景")
    parser.add_argument("--run", metavar="ID", help="运行指定场景的自动化用例")
    args = parser.parse_args()
    if args.list:
        return cmd_list()
    if args.run:
        return cmd_run(args.run)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

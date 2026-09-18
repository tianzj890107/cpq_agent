# -*- coding: utf-8 -*-
"""CI 依赖契约：证明「干净镜像 + requirements.txt」足以装载真实生产入口。

CI 用 `python:3.10-slim`。如果不装 `requirements.txt`，`production_unit` /
`production_http` / `recorded_provider` 会整体 skipped —— 门禁就会「零执行却绿色」。
所以这里做两件只读的事：

1. `requirement_names()`：把 `requirements.txt` 解析成规范化的发行包名集合；
2. `production_third_party_modules()`：在**子进程**里真装载一次生产入口
   （`prodkit.load()` → `backend.main` 等），收集它 import 的第三方顶层模块。

测试据此断言：生产入口用到的每个第三方模块都能在 `requirements.txt` 里找到出处。
不联网、不安装任何东西、不改环境。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = ROOT / "requirements.txt"

_LOAD_CODE = (
    "import json, sys;"
    "sys.path[:0]=[{tech!r}, {root!r}];"
    "from scripts.cpq_eval import prodkit;"
    "prodkit.load();"
    "mods=sorted({{m.split('.')[0] for m in sys.modules if not m.startswith('_')}});"
    "print('@@MODS@@' + json.dumps(mods))"
)


def requirement_names(path=None) -> set:
    """`requirements.txt` 里的发行包名（小写、`_`→`-`、去掉 extras 与版本号）。"""
    text = Path(path or REQUIREMENTS).read_text(encoding="utf-8")
    names = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        line = re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].strip()
        line = re.split(r"\s", line, maxsplit=1)[0].strip()
        if line:
            names.add(line.lower().replace("_", "-"))
    return names


def production_third_party_modules(timeout: int = 240) -> list:
    """生产入口 import 的**第三方**顶层模块（去掉标准库与仓库内模块）。"""
    code = _LOAD_CODE.format(tech=str(ROOT / "tech_app"), root=str(ROOT))
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), text=True,
                          capture_output=True, timeout=timeout)
    mods = []
    for line in (proc.stdout or "").splitlines():
        if line.startswith("@@MODS@@"):
            mods = json.loads(line[len("@@MODS@@"):])
    if not mods:
        raise RuntimeError(f"生产入口装载失败：{(proc.stderr or '').strip()[-300:]}")
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    local = set()
    for entry in os.listdir(ROOT):
        if entry.endswith(".py"):
            local.add(entry[:-3])
        elif (ROOT / entry / "__init__.py").exists():
            local.add(entry)
    local |= {"backend", "scripts", "tests"}
    return sorted(name for name in mods
                  if name not in stdlib and name not in local and not name.startswith("_"))

def _norm(name) -> str:
    """发行包名规范化：小写、`_` → `-`（PEP 503 的近似）。"""
    return str(name).strip().lower().replace("_", "-")


def _split_requirement(line: str):
    """把一行 requirements 拆成 (发行包名, 请求的 extras 集合)。"""
    spec = line.strip()
    name = re.split(r"[<>=!~;\[\s]", spec, maxsplit=1)[0].strip()
    extras = set()
    match = re.search(r"\[([^\]]*)\]", spec)
    if match:
        for extra in match.group(1).split(","):
            extra = extra.strip().lower()
            if extra:
                extras.add(extra)
    return _norm(name), extras


def requirement_specs(path=None) -> dict:
    """`requirements.txt`：{规范化发行包名: 请求的 extras 集合}。"""
    text = Path(path or REQUIREMENTS).read_text(encoding="utf-8")
    specs = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        name, extras = _split_requirement(line)
        if name:
            specs.setdefault(name, set()).update(extras)
    return specs


def covered_distributions(names=None, extras=None, requirements_path=None) -> set:
    """`requirements.txt` 声明的发行包，加上它们（含已启用 extras）的传递依赖闭包。

    pip 只装直接依赖，其余由被声明的包带进来。所以「生产入口 import 的第三方模块有
    出处」= 它所属发行包出现在 **闭包** 里，不必逐字写在 requirements.txt 中。

    依赖行若带 `; extra == "x"` 标记，只有该发行包被显式请求了 `x` 才会跟进，
    避免把 `pip install openai` 不会装的 extras（numpy / pandas / aiohttp）算成覆盖。
    """
    import importlib.metadata as md

    if names is None:
        specs = requirement_specs(requirements_path)
        roots = {name: set(extra) for name, extra in specs.items()}
    else:
        roots = {_norm(name): set() for name in names}
    for name, extra in (extras or {}).items():
        roots.setdefault(_norm(name), set()).update(extra)

    seen = {}
    frontier = dict(roots)
    while frontier:
        current, wanted = frontier.popitem()
        known = seen.get(current)
        if known is not None:
            if wanted <= known:
                continue
            wanted = wanted | known
        seen[current] = wanted
        try:
            requires = md.requires(current) or []
        except Exception:
            continue
        for raw in requires:
            raw = str(raw)
            spec, _semi, marker = raw.partition(";")
            marker = marker.strip().lower()
            gate = re.search(r'extra\s*==\s*["\']([^"\']+)["\']', marker)
            if gate and gate.group(1).strip().lower() not in wanted:
                continue
            dep, dep_extras = _split_requirement(spec)
            if not dep:
                continue
            pending = seen.get(dep)
            if pending is not None and dep_extras <= pending:
                continue
            frontier[dep] = (frontier.get(dep, set()) | dep_extras)
    return set(seen)

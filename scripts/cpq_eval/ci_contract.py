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

「用到」按**干净镜像**算：缺了也能把入口装起来的模块是可选依赖（guard 住的），
不算缺出处。`cadquery` 就是这一种 —— `requirements.txt` 里明确注释掉了它（缺了只是
`generate-geometry` 返回 503），可开发机上装了它，`sys.modules` 里就会多出来。判定不
看名字、不看注释，而是把候选模块真挡掉再装一次入口（`modules_the_entry_loads_without`）。
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
    "{blocker}"
    "from scripts.cpq_eval import prodkit;"
    "prodkit.load();"
    "mods=sorted({{m.split('.')[0] for m in sys.modules if not m.startswith('_')}});"
    "print('@@MODS@@' + json.dumps(mods))"
)

# 「干净镜像里根本没装这个包」的模拟：把候选模块从 import 系统里彻底挡掉，再看生产入口还装不装
# 得起来。被 guard 住的模块，`except` 会吸收这个 ImportError —— 那正是「可选依赖」的定义。
_BLOCK_CODE = (
    "_blocked_top=set({blocked!r});\n"
    "class _Blocker:\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name.split('.')[0] in _blocked_top:\n"
    "            raise ImportError('blocked for the CI dependency contract: ' + name)\n"
    "        return None\n"
    "sys.meta_path.insert(0, _Blocker());\n"
)


def _load_code(blocked=()) -> str:
    """装载生产入口的 `-c` 源码；`blocked` 非空时先插一层挡住这些顶层模块的 finder。"""
    blocker = _BLOCK_CODE.format(blocked=sorted(blocked)) if blocked else ""
    return _LOAD_CODE.format(tech=str(ROOT / "tech_app"), root=str(ROOT), blocker=blocker)


def _run_load(timeout: int, blocked=()) -> tuple:
    """真装载一次生产入口，回 `(顶层模块名排序表, stderr 尾部)`；装不起来时前者为空。"""
    proc = subprocess.run([sys.executable, "-c", _load_code(blocked)], cwd=str(ROOT),
                          text=True, capture_output=True, timeout=timeout)
    mods = []
    for line in (proc.stdout or "").splitlines():
        if line.startswith("@@MODS@@"):
            mods = json.loads(line[len("@@MODS@@"):])
    return mods, (proc.stderr or "").strip()[-300:]


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
    """生产入口 import 的**必需**第三方顶层模块（去掉标准库与仓库内模块）。

    「必需」= 干净镜像（只装 `requirements.txt`）也装得起生产入口。guard 住的可选依赖
    （`cadquery` 及其 multimethod / nlopt / typish）在开发机上会被真的装进 `sys.modules`，
    但那不代表「缺出处」：把它们挡掉再装一次入口，装得起来就不算（`## 498`）。
    """
    mods, err = _run_load(timeout)
    if not mods:
        raise RuntimeError(f"生产入口装载失败：{err}")
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    local = set()
    for entry in os.listdir(ROOT):
        if entry.endswith(".py"):
            local.add(entry[:-3])
        elif (ROOT / entry / "__init__.py").exists():
            local.add(entry)
    local |= {"backend", "scripts", "tests"}
    third_party = sorted(name for name in mods
                         if name not in stdlib and name not in local
                         and not name.startswith("_"))
    optional = _optional_third_party(third_party, timeout)
    return [name for name in third_party if name not in optional]


def modules_the_entry_loads_without(candidates, timeout: int = 240) -> set:
    """`candidates` 里「缺席也能把生产入口装起来」的模块 = 可选依赖。

    用一次真装载判定，不看名字也不看注释：把这些模块全部挡掉（等价于干净镜像里没装
    它们），生产入口还能装载就说明它们都可选。挡掉就装不起来时回空集 —— 保持原本的
    「缺出处」判定，不因为这条路径把真缺的依赖放行。
    """
    names = sorted({str(name).strip() for name in candidates if str(name).strip()})
    if not names:
        return set()
    mods, _err = _run_load(timeout, blocked=names)
    if not mods:
        return set()
    present = {name.split(".")[0] for name in mods}
    return {name for name in names if name not in present}


def _optional_third_party(modules, timeout: int) -> set:
    """`modules` 里在 `requirements.txt` 闭包中找不到出处、且真挡掉也能装载的那些。"""
    import importlib.metadata as md

    try:
        dist_map = md.packages_distributions()
    except Exception:
        return set()
    covered = covered_distributions()
    suspects = []
    for name in modules:
        dists = {_norm(dist) for dist in (dist_map.get(name) or [])}
        if dists and not (dists & covered):
            suspects.append(name)
    return modules_the_entry_loads_without(suspects, timeout)

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

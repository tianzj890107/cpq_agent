# -*- coding: utf-8 -*-
"""部署版本身份（Spec：`docs/specs/deploy-build-identity.md`）。

回答一个问题：**这台机器上跑的是哪一版代码？**

口径（唯一，不许各处自己读 git）：

  1. 部署脚本在启动参数里注入 `CPQ_BUILD_STAMP=<stamp 文件路径>`，并把当次部署的
     commit 写进去；`/api/health` 读它 → `source="stamp"`；
  2. stamp 拿不到（没部署过 / 文件被删）→ 回退 `git rev-parse HEAD` → `source="git"`；
  3. 两个都拿不到 → `source="unknown"`，各字段空串。

**任何情况都不抛异常**：health 绝不能因为"版本读不到"而 500。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
from typing import Dict, Mapping, Optional

#: 注入 stamp 路径的环境变量名（部署脚本与服务启动命令共用一个名字）。
STAMP_ENV = "CPQ_BUILD_STAMP"
#: 缺省 stamp 文件名；缺省路径落在**部署目录之外**（`<repo_root>/../cpq_build.json`），
#: 免得部署产物脏了工作区。
STAMP_FILENAME = "cpq_build.json"
#: 出参键集（顺序固定，值恒为字符串）。
BUILD_KEYS = ("commit", "branch", "ref", "deployed_at", "source")
#: 拿不到任何版本信息时的哨兵值。
UNKNOWN = "unknown"

#: 缺省仓库根：`tech_app/backend/services/build_identity.py` → parents[3] == 仓库根。
_DEFAULT_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

#: git 命令超时（秒）：探测不许把 health 拖住。
_GIT_TIMEOUT = 5


def _env_of(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def _repo_root(repo_root=None) -> pathlib.Path:
    try:
        return pathlib.Path(repo_root) if repo_root else _DEFAULT_REPO_ROOT
    except Exception:                                          # pragma: no cover - 防御
        return _DEFAULT_REPO_ROOT


def stamp_path(*, env=None, repo_root=None) -> pathlib.Path:
    """stamp 文件路径：env `CPQ_BUILD_STAMP` 优先，否则 `<repo_root>/../cpq_build.json`。"""
    raw = ""
    try:
        raw = str(_env_of(env).get(STAMP_ENV, "") or "").strip()
    except Exception:                                          # pragma: no cover - 防御
        raw = ""
    if raw:
        return pathlib.Path(raw)
    return _repo_root(repo_root).parent / STAMP_FILENAME


def _git(args, repo_root) -> str:
    """跑一条只读 git 命令；任何失败都返回空串（不抛）。"""
    try:
        proc = subprocess.run(["git", "-C", str(repo_root)] + list(args),
                              capture_output=True, text=True, timeout=_GIT_TIMEOUT)
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def git_head(repo_root=None) -> str:
    """`git rev-parse HEAD`；拿不到返回 `UNKNOWN`（短路，不抛）。"""
    head = _git(("rev-parse", "HEAD"), _repo_root(repo_root))
    return head or UNKNOWN


def _from_stamp(path: pathlib.Path) -> Optional[Dict[str, str]]:
    """读 stamp；不是 JSON 对象 / commit 为空都返回 None（调用方回退 git）。"""
    try:
        if not path.is_file():
            return None
        with path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    commit = payload.get("commit")
    if not isinstance(commit, str) or not commit.strip():
        return None
    out = {key: "" for key in BUILD_KEYS}
    for key in BUILD_KEYS:
        if key == "source":
            continue                                       # source 由来源决定，stamp 说了不算
        value = payload.get(key)
        out[key] = value if isinstance(value, str) else ""
    out["commit"] = commit
    out["source"] = "stamp"
    return out


def _from_git(repo_root: pathlib.Path) -> Optional[Dict[str, str]]:
    commit = _git(("rev-parse", "HEAD"), repo_root)
    if not commit:
        return None
    out = {key: "" for key in BUILD_KEYS}
    out["commit"] = commit
    out["branch"] = _git(("rev-parse", "--abbrev-ref", "HEAD"), repo_root)
    out["source"] = "git"
    return out


def build_info(*, env=None, repo_root=None) -> dict:
    """部署版本身份；**任何情况都不抛**，键集恒等于 `BUILD_KEYS`。"""
    empty = {key: "" for key in BUILD_KEYS}
    empty["source"] = UNKNOWN
    try:
        root = _repo_root(repo_root)
        info = _from_stamp(stamp_path(env=env, repo_root=root))
        if info is None:
            info = _from_git(root)
        if info is None:
            return empty
        return {key: str(info.get(key, "") or "") for key in BUILD_KEYS}
    except Exception:                                          # pragma: no cover - 绝不 500
        return empty

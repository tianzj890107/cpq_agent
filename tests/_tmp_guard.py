# -*- coding: utf-8 -*-
"""本地测试的临时目录闸门：一次 `python -m unittest` 跑完，系统 TMPDIR 不留垃圾。

为什么要有这个（2026-09-24 本机只读实测）：

  · `tests/` 里 34 处 `tempfile.mkdtemp()`（**无前缀**）+ 118 处 `mkdtemp(prefix=...)`，
    绝大多数**只建不删**：临时 SQLite（各 0.5–1 MB）、meta 目录、拉子进程用的脚本目录；
  · 只有 38 处挂了自己 `addCleanup(shutil.rmtree, ...)`；
  · 一次全量（400 个模块）会在系统 TMPDIR 里新建 **1371 个目录**；
  · 当时这台机器攒了 335 349 个条目 / **123 GB**，其中无前缀 `tmp########` 227 426 个、
    99.7 GB —— 也就是说：**根因不是某一条红测忘了删，而是整套用例都没有统一出口**。

清理逻辑就两条：

  1. **一次运行一个根**：`install()` 先在系统 TMPDIR 下建一个 `cpq-testrun-XXXX` 根，
     接着把 `tempfile.tempdir` 与 `TMPDIR` **都**指过去 —— 连测试拉起的**子进程**建的
     临时目录也落在这个根里（`TMPDIR` 被子进程继承）；
  2. **退出时整根删掉**：`atexit` 里 `rmtree(..., ignore_errors=True)`。用例自己已经
     `addCleanup` 删过的目录重删是幂等的 —— 因此本模块**在 import 时就把 `rmtree` 抓住**，
     免得某条红测把 `shutil.rmtree` patch 成 boom 时把退出清理一起带崩。

被 SIGKILL / 断电打断时只会留下一个 `cpq-testrun-*` 根，交给 `scripts/reclaim_test_tmpdirs.py` 收。

调试红测要看现场（闸门退化成"不删"）：

    CPQ_TEST_KEEP_TMP=1 ./open-claude/.venv/bin/python -m unittest tests.test_xxx

不想要那行提示：

    CPQ_TEST_TMP_QUIET=1 ./open-claude/.venv/bin/python -m unittest tests.test_xxx
"""

import atexit
import os
import shutil
import sys
import tempfile

#: 每次运行的根目录前缀。`scripts/reclaim_test_tmpdirs.py` 靠这个名字收被 SIGKILL 留下的根
#: （它自己从本文件里正则读这个常量，不 import `tests` 包 —— 一 import 就会把它的 TMPDIR 也搬走）。
RUN_ROOT_PREFIX = "cpq-testrun-"

#: 保留现场（不做删除，只打印根在哪）。
KEEP_ENV = "CPQ_TEST_KEEP_TMP"

#: 不打印那一行收尾提示。
QUIET_ENV = "CPQ_TEST_TMP_QUIET"

#: 在 import 时就抓住真 `rmtree`：红测里 `mock.patch("shutil.rmtree", boom)` 不该影响退出清理。
_RMTREE = shutil.rmtree

_ORIGINAL_MKDTEMP = tempfile.mkdtemp

_INSTALLED = False
_RUN_ROOT = None
_CREATED = []


def install():
    """装闸门。幂等（`tests/__init__.py` 与用例都可能调）。返回本次运行的根。

    顺序有讲究：**先**建根、**再**改 `tempfile.tempdir` / `TMPDIR`。反过来的话根会建到
    自己里面，删根时把根自己也删掉，行为会随实现变化。
    """
    global _INSTALLED, _RUN_ROOT
    if _INSTALLED:
        return _RUN_ROOT
    _INSTALLED = True
    root = _ORIGINAL_MKDTEMP(prefix=RUN_ROOT_PREFIX)
    _RUN_ROOT = root
    tempfile.tempdir = root
    os.environ["TMPDIR"] = root
    tempfile.mkdtemp = _mkdtemp
    atexit.register(_cleanup, _keep_requested())
    return root


def run_root():
    """本次运行的根（没装闸门时是 None）。"""
    return _RUN_ROOT


def created():
    """本次运行里经 `tempfile.mkdtemp` 建的路径（副本，供排查用）。"""
    return list(_CREATED)


def _keep_requested():
    return str(os.environ.get(KEEP_ENV, "")).strip().lower() in ("1", "true", "yes", "on")


def _quiet():
    return str(os.environ.get(QUIET_ENV, "")).strip().lower() in ("1", "true", "yes", "on")


def _mkdtemp(suffix=None, prefix=None, dir=None):
    """`tempfile.mkdtemp` 的替身：不指定 `dir` 就落到本次运行的根里，并记下路径。

    指定了 `dir` 的照旧（例如某条红测要验"逃出 TMPDIR"的路径），只是也记账。
    """
    path = _ORIGINAL_MKDTEMP(suffix=suffix, prefix=prefix, dir=dir or _RUN_ROOT)
    _CREATED.append(path)
    return path


def _rmtree(path):
    """删一个路径；返回它**原本**是否存在（不存在也算成功）。"""
    existed = os.path.exists(path)
    try:
        _RMTREE(path, ignore_errors=True)
    except Exception:                                     # noqa: BLE001 - 退出清理不许抛
        return False
    return existed


def _cleanup(keep):
    """退出清理：先把本次运行建的逐个删掉，再删整根（子进程建的都在根里）。"""
    if keep:
        _echo("临时目录闸门：按 %s 保留现场 %s（本次 %d 个）"
              % (KEEP_ENV, _RUN_ROOT, len(_CREATED)))
        return
    removed = 0
    for path in _CREATED:
        if _rmtree(path):
            removed += 1
    if _RUN_ROOT and _rmtree(_RUN_ROOT):
        removed += 1
    _echo("临时目录闸门：清掉 %d 个临时目录（根 %s）" % (removed, _RUN_ROOT))


def _echo(text):
    if _quiet():
        return
    try:
        sys.stderr.write(text + "\n")
    except Exception:                                     # noqa: BLE001 - 打印失败不该影响退出
        pass

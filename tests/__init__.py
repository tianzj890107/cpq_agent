# -*- coding: utf-8 -*-
"""`tests` 包只做一件事：装上本地临时目录闸门（见 `tests/_tmp_guard.py`）。

为什么要一个 `__init__.py`：全仓 152 处 `tempfile.mkdtemp` 没有统一出口，逐个用例补
`addCleanup` 既改不完也守不住；而 `python -m unittest tests.xxx` 必定先 import 本包 ——
于是这里是唯一"零用例改动"的接入点。

必须容错：闸门装不上（TMPDIR 不可写之类）时只提示一行，**绝不让一整个测试会话起不来**。
"""

try:
    from . import _tmp_guard

    _tmp_guard.install()
except Exception as _exc:                                 # noqa: BLE001 - 闸门失败不许拖垮测试
    import sys as _sys

    _sys.stderr.write("临时目录闸门未能安装（%s: %s）—— 本次运行的临时目录不会自动清理；"
                      "可用 scripts/reclaim_test_tmpdirs.py 收\n" % (type(_exc).__name__, _exc))

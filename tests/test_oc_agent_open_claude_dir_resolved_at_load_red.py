"""红测：`oc_agent` 装载 open-claude 的目录必须在**装载时**解析，不能在导入时冻结。

Spec：`docs/specs/tech-open-claude-dir-resolved-at-load-time.md`

现状缺口（本机实跑，两次只差导入顺序）：

  · `oc_agent.py:42` 在导入时把 `OPEN_CLAUDE_DIR` 求值成常量，默认值是 `tech_app/open-claude`
    （本仓库不存在这个目录）；
  · `_ensure_path()`（`:167-172`）读那个常量 → 抛 `AgentUnavailable`；
  · `_register_runtime_provider()`（`:294-311`）外面是 `except Exception: return`，于是异常被静默吞掉，
    `sync_route_environment()` 照旧写 `CLAUDE_MODEL`，但「模型 → provider」映射没写，
    `get_model_provider("cpq-local-7b")` 回落到 `anthropic`（请求发错厂商，且不报错）。

  · 先设环境变量再导入（现在的动态红测的姿势）→ OK；
  · 先导入 `tech_app.backend.main`（因而导入 `oc_agent`）、后设环境变量 → 必红。

纪律：`OPEN_CLAUDE_DIR` 的**导入时机**是这两条用例的自变量，只能在子进程里跑；
全部离线（不连 PG、不发 HTTP、不调模型、不写业务数据、不落库）；用当前解释器（`sys.executable`，
即 `open-claude/.venv` 的 3.10，能加载 open-claude 的字节码）。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
OPEN_CLAUDE_DIR = ROOT / "open-claude"

#: 子进程探针。`argv[2]` 是"什么时候设环境变量"：early（导入前）/ late（导入后）/ missing（指向不存在的目录）。
SCRIPT = r'''
import json
import os
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
when = sys.argv[2]
real = pathlib.Path(sys.argv[3])
sys.path.insert(0, str(root))

if when == "early":
    os.environ["OPEN_CLAUDE_DIR"] = str(real)

from tech_app.backend.services import oc_agent

out = {}
out["module_dir"] = str(oc_agent.OPEN_CLAUDE_DIR)
out["module_dir_is_dir"] = oc_agent.OPEN_CLAUDE_DIR.is_dir()

if when == "late":
    os.environ["OPEN_CLAUDE_DIR"] = str(real)
elif when == "missing":
    os.environ["OPEN_CLAUDE_DIR"] = str(root / "no-such-open-claude-dir")

try:
    oc_agent._ensure_path()
    out["ensure_path"] = "ok"
except Exception as exc:                                            # noqa: BLE001 - 探针要如实带回
    out["ensure_path"] = "%s: %s" % (type(exc).__name__, exc)

out["real_on_sys_path"] = str(real) in sys.path

route = {"model": "cpq-local-7b", "provider": "cpq_local",
         "provider_label": "本地网关（CPQ 共用）",
         "base_url": "http://127.0.0.1:9999/v1", "native": False,
         "api_key": "local-key"}
try:
    oc_agent.sync_route_environment(route)
    out["sync_raised"] = ""
except Exception as exc:                                            # noqa: BLE001 - 探针要如实带回
    out["sync_raised"] = "%s: %s" % (type(exc).__name__, exc)

try:
    from open_claude import config as oc_config
    out["model_provider"] = str(oc_config.get_model_provider("cpq-local-7b"))
except Exception as exc:                                            # noqa: BLE001
    out["model_provider"] = "import_failed: %s" % exc

out["claude_model_env"] = str(os.environ.get("CLAUDE_MODEL") or "")
print(json.dumps(out, ensure_ascii=False))
'''


def probe(when: str) -> dict:
    """在独立数据目录里真跑一次探针，返回它打印的 JSON。"""
    with tempfile.TemporaryDirectory(prefix="cpq-ocdir-") as tmp:
        env = dict(os.environ, DATA_DIR=tmp, AUTH_ENABLED="false")
        env.pop("OPEN_CLAUDE_DIR", None)          # 导入时机必须由探针自己决定
        env.pop("CLAUDE_MODEL", None)
        env.pop("CPQ_LOCAL_BASE_URL", None)
        completed = subprocess.run(
            [sys.executable, "-c", SCRIPT, str(ROOT), when, str(OPEN_CLAUDE_DIR)],
            capture_output=True, text=True, timeout=300, cwd=str(ROOT), env=env)
    if completed.returncode != 0:
        raise AssertionError("探针跑不起来：%s"
                             % (completed.stderr or completed.stdout)[-800:])
    return json.loads((completed.stdout or "{}").strip().splitlines()[-1])


@unittest.skipUnless((OPEN_CLAUDE_DIR / "open_claude" / "config.pyc").exists(),
                     "本机没有可加载的 open-claude 字节码")
class AResolvedAtLoadTime(unittest.TestCase):
    """A 组：目录必须在装载时解析（今天全红）。"""

    def test_a1_late_env_var_is_honoured(self):
        state = probe("late")
        self.assertFalse(state["module_dir_is_dir"],
                         "前提：`OPEN_CLAUDE_DIR` 没设时模块常量指向不存在的默认目录（%r）—— "
                         "本用例要证明的正是它不该决定装载" % state["module_dir"])
        self.assertEqual("ok", state["ensure_path"],
                         "导入本模块之后才设 `OPEN_CLAUDE_DIR`，`_ensure_path()` 仍拿不到目录（%r）—— "
                         "目录必须在**装载时**解析，不是导入时常量（Spec §2.1）" % state["ensure_path"])
        self.assertTrue(state["real_on_sys_path"],
                        "解析出来的 open-claude 目录必须进 `sys.path`（Spec §2.1）")

    def test_a2_runtime_provider_registers_after_a_late_env_var(self):
        state = probe("late")
        self.assertEqual("", state["sync_raised"],
                         "`sync_route_environment()` 不许抛（Spec §2.3）：%r" % state["sync_raised"])
        self.assertEqual("cpq-local-7b", state["claude_model_env"],
                         "`CLAUDE_MODEL` 的既有搬运不许受影响（Spec §2.5）")
        self.assertEqual("cpq_local", state["model_provider"],
                         "路由同步了、provider 却没登记：`get_model_provider()` 回落到 %r —— "
                         "请求会发到 anthropic（Spec §1、§2.3）" % state["model_provider"])

    def test_a3_missing_dir_error_names_the_resolved_dir(self):
        state = probe("missing")
        self.assertTrue(state["ensure_path"].startswith("AgentUnavailable"),
                        "目录不存在时仍必须抛 `AgentUnavailable`（Spec §2.2）：%r" % state["ensure_path"])
        self.assertIn("no-such-open-claude-dir", state["ensure_path"],
                      "文案里要带**解析出来的**那个目录（环境变量给的那个），"
                      "而不是导入时冻结的默认目录：%r（Spec §2.2）" % state["ensure_path"])


@unittest.skipUnless((OPEN_CLAUDE_DIR / "open_claude" / "config.pyc").exists(),
                     "本机没有可加载的 open-claude 字节码")
class BGuards(unittest.TestCase):
    """B 组：今天就是绿的，不许被改红。"""

    def test_b1_early_env_var_still_works(self):
        state = probe("early")
        self.assertTrue(state["module_dir_is_dir"],
                        "先设环境变量再导入的姿势（部署与既有测试）必须照旧可用")
        self.assertEqual("ok", state["ensure_path"])
        self.assertTrue(state["real_on_sys_path"])
        self.assertEqual("cpq_local", state["model_provider"])

    def test_b2_missing_dir_still_raises(self):
        state = probe("missing")
        self.assertTrue(state["ensure_path"].startswith("AgentUnavailable"),
                        "目录不存在时仍必须抛 `AgentUnavailable`（Spec §2.2）：%r" % state["ensure_path"])
        self.assertNotEqual("ok", state["ensure_path"])

    def test_b3_sync_is_still_silent_when_open_claude_is_missing(self):
        state = probe("missing")
        self.assertEqual("", state["sync_raised"],
                         "open-claude 真找不到时，`sync_route_environment()` 仍不许抛 —— "
                         "「同步一次路由」不能变成可能失败的写操作（Spec §2.3）")

    def test_b4_module_attribute_survives(self):
        state = probe("early")
        self.assertTrue(state["module_dir"],
                        "`oc_agent.OPEN_CLAUDE_DIR` 属性必须保留（兼容，Spec §2.4）")


if __name__ == "__main__":
    unittest.main()

"""红测：依赖自检不许说"就绪"而真跑说"缺失"，导入失败也不许被永久缓存。

Spec：`docs/specs/packaging-flow-dependency-probe-truth.md`
血缘：`docs/specs/dwg-semantics-agent-flow.md` §2.2（依赖只经一个缝 + `capability()` 不许粉饰；
红测 `I1` 的 `find_spec` 口径本批**不动**）、`packaging-silent-degradation-disclosure.md`、
`drawing-flow-error-taxonomy.md`。

现状缺口（读代码 + 离线打桩，2026-09-22）：
  · `packaging_drawing_flow/__init__.py:62-66`：`importlib.import_module` 抛异常 → `module = None`
    → `_CACHE[key] = module` —— 真因被吞，**失败也进缓存**（一次失败 = 这个进程永远"依赖缺失"，
    没有失效入口，只有重启才恢复）；
  · `__init__.py:76 _available()` 用 `importlib.util.find_spec` —— 只证明"文件在"；
    模块自己的 import 失败（缺子依赖 / 语法错误）时它照旧返回 True →
    `capability()["available"] is True`、"编排层已就绪"，而真跑第 3 步就 `unavailable`；
  · `steps.py:21 _resolve()` 的 `except Exception: return None` 把外部注入 resolver 的异常
    也一并吞掉 → `_unavailable(name)` 只给 `detail.dependency`，看不出是"没有"还是"装载失败"。

纪律：
  · 只跑离线单测：打桩 `importlib` + 清 `_CACHE`，不连 34、不跑真 DWG 转换、不发 HTTP、不写数据；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_drawing_flow as flow  # noqa: E402
from tech_app.backend.services.packaging_drawing_flow import model  # noqa: E402

#: `packaging_drawing_flow.steps` 这个名字在包里已经被同名**函数** `steps()` 占住
#: （`from package import steps` 拿到的是那个函数），所以这里按完整路径取子模块。
steps = importlib.import_module("tech_app.backend.services.packaging_drawing_flow.steps")

REQUIRED = ("file_preflight", "cad_converter", "cad_ir", "packaging_semantics")
DEPENDENCY_STATES = ("ok", "missing", "import_failed", "unknown")


class FlowDepsCase(unittest.TestCase):
    """共用：保存并恢复 `_CACHE` 与依赖状态登记表，避免污染同进程的其它测试。"""

    def setUp(self):
        self._cache = dict(flow._CACHE)
        self.registry = getattr(model, "DEPENDENCY_STATE_REGISTRY", None)
        self._registry = dict(self.registry) if isinstance(self.registry, dict) else None
        flow._CACHE.clear()

    def tearDown(self):
        flow._CACHE.clear()
        flow._CACHE.update(self._cache)
        if isinstance(self.registry, dict) and self._registry is not None:
            self.registry.clear()
            self.registry.update(self._registry)

    def state_of(self, name):
        fn = getattr(model, "dependency_state", None)
        self.assertTrue(callable(fn),
                        "model 必须提供 dependency_state()（Spec §2.1）："
                        "今天导入失败与「没有这个依赖」在返回体上逐字相同")
        return fn(name)


# --------------------------------------------------------------------------- #
# A. 失败不许进缓存 + 必须登记
# --------------------------------------------------------------------------- #
class ANoCacheOnFailure(FlowDepsCase):
    def test_a1_failure_must_not_be_cached(self):
        good = mock.Mock(name="cad_ir_module")
        calls = [ModuleNotFoundError("No module named 'ods'"), good]
        with mock.patch.object(importlib, "import_module", side_effect=calls) as imp:
            first = flow._dependency("cad_ir")
            second = flow._dependency("cad_ir")
        self.assertIsNone(first, "拿不到仍是 None（对外口径不变，Spec §2.2）")
        self.assertIs(good, second,
                      "导入失败不许进缓存：第二次必须**重新尝试**并拿到模块（Spec §2.2）")
        self.assertEqual(2, imp.call_count,
                         "第一次失败后必须重新 import（Spec §2.2）：今天 `_CACHE[key]=None` "
                         "会把一次失败钉死到进程结束")

    def test_a2_success_is_still_cached(self):
        """护栏：成功照旧进缓存（不许为了重试改成每次请求都 import）。"""
        good = mock.Mock(name="cad_ir_module")
        with mock.patch.object(importlib, "import_module", return_value=good) as imp:
            self.assertIs(good, flow._dependency("cad_ir"))
            self.assertIs(good, flow._dependency("cad_ir"))
        self.assertEqual(1, imp.call_count, "成功过一次之后必须走缓存（Spec §4）")

    def test_a3_failure_is_registered_with_reason_and_message(self):
        with mock.patch.object(importlib, "import_module",
                               side_effect=ModuleNotFoundError("No module named 'ods'")):
            flow._dependency("cad_ir")
        state = self.state_of("cad_ir")
        self.assertIn(state.get("state"), DEPENDENCY_STATES, "state 必须是闭集（Spec §2.1）")
        self.assertEqual("import_failed", state.get("state"),
                         "导入失败必须登记 import_failed（Spec §2.2）")
        self.assertEqual("ModuleNotFoundError", state.get("reason"),
                         "reason 必须是异常类名（Spec §2.1）")
        self.assertIn("No module named", str(state.get("message")),
                      "message 必须给异常原文（排障要看得到缺哪个子依赖，Spec §2.1）")


# --------------------------------------------------------------------------- #
# B. capability() 必须报"真的能导入吗"
# --------------------------------------------------------------------------- #
class BCapabilityProbe(FlowDepsCase):
    def test_b1_dependencies_bool_keeps_the_find_spec_contract(self):
        """护栏：`dependencies` 的 bool 仍是 `find_spec` 口径（既有红测 I1 钉着）。"""
        deps = flow.capability()["dependencies"]
        for name in REQUIRED:
            path = flow._MODULE_PATHS[name]
            try:
                spec = importlib.util.find_spec(path)
            except (ImportError, ValueError):
                spec = None
            self.assertEqual(bool(deps.get(name)), spec is not None,
                             "capability()['dependencies'] 的口径不许变（Spec §4）")

    def test_b2_file_present_but_import_failing_is_not_ready(self):
        with mock.patch.object(importlib.util, "find_spec", return_value=object()), \
             mock.patch.object(importlib, "import_module",
                               side_effect=ModuleNotFoundError("No module named 'ods'")):
            cap = flow.capability()
        state = (cap.get("dependencies_state") or {}).get("cad_ir") or {}
        self.assertTrue(cap.get("dependencies_state"),
                        "capability() 必须新增 dependencies_state（Spec §2.3）")
        self.assertEqual("import_failed", state.get("state"),
                         "文件在、导入失败必须是 import_failed（Spec §2.3）")
        self.assertIs(False, cap.get("available"),
                      "导入失败时不许说「编排层已就绪」（Spec §2.3）")
        message = str(cap.get("message") or "")
        self.assertIn("cad_ir", message, "message 必须点名是哪一个依赖（Spec §2.3）")
        self.assertIn("装载失败", message,
                      "导入失败与「尚未就绪」必须分家说（Spec §2.3）")


# --------------------------------------------------------------------------- #
# C. 步骤报"依赖缺失"时要带上状态
# --------------------------------------------------------------------------- #
class CStepUnavailable(FlowDepsCase):
    def _unavailable(self, name):
        fn = getattr(steps, "_unavailable", None)
        self.assertTrue(callable(fn), "steps 必须仍有 _unavailable()")
        return fn(name)

    def test_c1_detail_carries_dependency_state_and_reason(self):
        self.assertTrue(isinstance(getattr(model, "DEPENDENCY_STATE_REGISTRY", None), dict),
                        "登记表必须是模块级真 dict（Spec §2.1）")
        model.note_dependency_state("cad_ir", "import_failed", reason="ModuleNotFoundError",
                                    message="No module named 'ods'")
        out = self._unavailable("cad_ir")
        detail = out.get("detail") or {}
        self.assertIn("dependency_state", detail, "detail 必须带状态（Spec §2.4）")
        self.assertEqual("import_failed", detail.get("dependency_state"))
        self.assertEqual("ModuleNotFoundError", detail.get("reason"),
                         "detail.reason 必须是异常类名（Spec §2.4）")
        self.assertEqual("unavailable", out.get("status"))
        self.assertEqual("PACKAGING_FLOW_DEPENDENCY_MISSING", out.get("error_code"),
                         "错误码不许换（Spec §2.4）")

    def test_c2_unregistered_state_is_unknown_and_message_unchanged(self):
        """护栏：没登记过时是 unknown（不许编 missing），既有码 / 文案逐字不变。"""
        out = self._unavailable("cad_ir")
        detail = out.get("detail") or {}
        self.assertEqual("PACKAGING_FLOW_DEPENDENCY_MISSING", out.get("error_code"))
        self.assertEqual("unavailable", out.get("status"))
        self.assertEqual("cad_ir", detail.get("dependency"))
        self.assertEqual(500, detail.get("http_status"))
        self.assertIs(False, out.get("retryable"))


# --------------------------------------------------------------------------- #
# D. 端到端一步：resolver 抛异常 → 步骤 detail 是 import_failed
# --------------------------------------------------------------------------- #
class DCadIrParseStep(FlowDepsCase):
    def test_d1_resolver_failure_is_visible_in_the_step(self):
        def resolver(name):
            raise ModuleNotFoundError("No module named 'ods'")

        out = steps.cad_ir_parse({"project_id": "p1", "resolve": resolver,
                                  "drawing_version": 1})
        detail = out.get("detail") or {}
        self.assertEqual("unavailable", out.get("status"), "拿不到依赖仍是 unavailable（Spec §2.4）")
        self.assertEqual("import_failed", detail.get("dependency_state"),
                         "外部注入 resolver 的异常也必须登记并报出来（Spec §2.4）")
        self.assertEqual("ModuleNotFoundError", detail.get("reason"))


if __name__ == "__main__":
    unittest.main()

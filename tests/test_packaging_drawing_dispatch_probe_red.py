"""红测：图纸入口分发器的能力探测失败不许说成"可用"。

Spec：`docs/specs/packaging-drawing-dispatch-probe-truthfulness.md`

现状缺口（代码级，都可指到行）：
  · `main.py:7355-7362 dispatch_project_drawing_parse()` 在探测抛异常时 `available = True`
    （注释"探测失败不挡分流"）；
  · `file_preflight.py:461-462` 的契约与此相反："探测失败按'没有'返回（available=False,
    role="none"）…… 能力查询失败必须能被上层当作'不可用'处理"；
  · 于是"探测挂了"被渲染成 `flow_available: true`，而且与"探测成功但确实没有转换器"
    在读回体上不可分（没有第三个键）。

纪律：只读源码 + 打桩探测函数；不连 PG / SQLite 生产库、不发 HTTP、不写业务数据。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend import main as backend_main                      # noqa: E402
from tech_app.backend.services import file_preflight                   # noqa: E402

PID = "testpid00001"

PRIMARY_CAPS = {"available": True, "role": "primary", "version": "27.1", "source": "oda",
                "checked_at": "2026-09-22 12:00:00"}
NO_CONVERTER_CAPS = {"available": False, "role": "none", "version": "", "source": "none",
                     "checked_at": "2026-09-22 12:00:00"}


class _Patch:
    """把若干 (对象, 属性) 换成临时实现，退出时原样还原。"""

    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []

    def __enter__(self):
        for owner, name, value in self.pairs:
            self.saved.append((owner, name, getattr(owner, name)))
            setattr(owner, name, value)
        return self

    def __exit__(self, *exc):
        for owner, name, old in reversed(self.saved):
            setattr(owner, name, old)
        return False


def _dispatch(filename="酒盒.dwg", *, caps=None, probe_error=None):
    def detector():
        if probe_error is not None:
            raise probe_error
        return dict(PRIMARY_CAPS if caps is None else caps)

    with _Patch((file_preflight, "detect_converter_availability", detector),
                (backend_main.store, "load_meta", lambda project_id: {})):
        return backend_main.dispatch_project_drawing_parse(PID, filename=filename)


# --------------------------------------------------------------------------- #
# M 组：三种状态（可用 / 不可用 / 探测不到）必须两两可分
# --------------------------------------------------------------------------- #
class MDispatchProbeTruthfulness(unittest.TestCase):
    def test_m1_probe_failure_is_not_reported_as_available(self):
        result = _dispatch(probe_error=RuntimeError("probe boom"))
        self.assertIs(False, result.get("flow_available"),
                      "探测失败必须按不可用处理（Spec §2.1）："
                      "现在这里写的是 `available = True` —— 点下去才发现不行")
        flag = result.get("probe_unavailable") or {}
        self.assertEqual("converter_probe_unavailable", flag.get("code"),
                         "探测失败要显式披露（键必须存在）")
        self.assertIn("探测", str(result.get("reason") or ""),
                      "探测失败时的文案要说'暂时探测不到'，不许说成'本环境没有转换器'")

    def test_m2_probe_success_available_reports_clean(self):
        result = _dispatch(caps=PRIMARY_CAPS)
        self.assertIs(True, result.get("flow_available"), "探测到可用就是可用")
        self.assertEqual({}, result.get("probe_unavailable"),
                         "探测成功时该键必须是空的（键必须存在）")

    def test_m3_probe_success_without_converter_is_not_probe_failure(self):
        result = _dispatch(caps=NO_CONVERTER_CAPS)
        self.assertIs(False, result.get("flow_available"), "没转换器就是不可用")
        self.assertEqual({}, result.get("probe_unavailable"),
                         "这是'确实没有'，不是'探测不到'：必须是空标记（Spec §2.1）")

    def test_m4_other_suffixes_keep_their_exact_contract(self):
        expected = {
            "封面.png": ("vision", ".png", "位图走通用视觉解析", False),
            "模型.step": ("blocked_3d", ".step", "三维交换格式：二维链路与视觉解析都不适用", False),
            "说明.xyz": ("blocked_other", ".xyz", "无法识别的图纸格式", False),
        }
        for filename, (route, suffix, reason, available) in expected.items():
            result = _dispatch(filename=filename)
            self.assertEqual(route, result.get("route"), "%s 的 route 逐字不变" % filename)
            self.assertEqual(suffix, result.get("suffix"), "%s 的 suffix 逐字不变" % filename)
            self.assertEqual(reason, result.get("reason"), "%s 的 reason 逐字不变" % filename)
            self.assertIs(available, result.get("flow_available"),
                          "%s 的 flow_available 逐字不变" % filename)
            self.assertEqual({}, result.get("probe_unavailable") or {},
                             "%s 不走转换器：该键为空" % filename)

    def test_m5_suffix_routing_does_not_depend_on_the_probe(self):
        result = _dispatch(filename="圆盘盒.dwg", probe_error=RuntimeError("probe boom"))
        self.assertEqual("drawing_flow", result.get("route"),
                         "后缀判据不受探测影响：.dwg 永远走图纸解析链路（Spec §2.1）")
        self.assertEqual(".dwg", result.get("suffix"))


if __name__ == "__main__":
    unittest.main()

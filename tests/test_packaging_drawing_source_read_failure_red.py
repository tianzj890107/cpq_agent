"""红测：图纸源文件"读不到"不许折成"空文件"，更不许把空内容的哈希当成这一版图纸。

Spec：`docs/specs/packaging-drawing-source-read-failure.md`

现状缺口（代码级，都可指到行）：
  · `packaging_drawing_flow/__init__.py:252-260 _source_bytes()` 把
    "没有 source_path"与"blob 读不到"一起吞成 `b""`；
  · `start()`（`:299-301`）无条件 `sha256(content)` —— 读不到时写进
    `inputs.source_sha256`（`:319`）与锚点（`:341`）的是 `sha256(b"")` 那个**合法形状的常量**，
    run 复用判定与下游 `source_sha256_changed` 都会把"读不到"当成"源文件没变"；
  · `_context()`（`:373`）把同一个 `b""` 给第 1 步：`detect_file_format(name, b"")` 返回
    `is_empty=True`，于是 `steps.py:108` 给 `FILE_EMPTY`（`retryable=False`）+
    "上传的图纸是空文件，请重新上传" —— 真相是 blob 读不到。

纪律：只读源码 + 打桩 `store.load_meta` / `store._blob` / `persistence`；
不连 PG / SQLite 生产库、不发 HTTP、不建项目、不写任何文件。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import hashlib
import importlib
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tech_app.backend.services import packaging_drawing_flow as flow          # noqa: E402
from tech_app.backend.services.packaging_drawing_flow import model           # noqa: E402
# 包 `__init__` 里有一个同名函数 `steps()`，会把子模块属性遮掉 —— 走 importlib 拿真模块。
steps = importlib.import_module("tech_app.backend.services.packaging_drawing_flow.steps")

PID = "testpid00001"
REAL_CONTENT = b"AC1027 fake dwg bytes"
EMPTY_CONTENT_SHA = hashlib.sha256(b"").hexdigest()


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


class _Blob:
    """假 blob 后端：要么给字节，要么按 `error` 抛。"""

    def __init__(self, content=b"", error=None):
        self.content = content
        self.error = error

    def get_bytes(self, key):
        if self.error is not None:
            raise self.error
        return self.content


def _context(*, meta, content=b"", blob_error=None):
    """跑一次 `_context()`（第 1 步拿到的那份 ctx），只打桩存储与锚点。"""
    with _Patch((flow.store, "load_meta", lambda project_id: dict(meta)),
                (flow.store, "_blob", lambda: _Blob(content, blob_error)),
                (flow.anchor_mod, "current_anchor", lambda project_id: {})):
        return flow._context(PID, "file_preflight", {"run_id": "flow-x"},
                             run_id="flow-x", actor="tester", resolver=lambda name: None)


def _start(*, meta, content=b"", blob_error=None):
    """跑一次 `start()`，把落库的 flow 文档截回来。"""
    saved = {}

    def save_flow(project_id, doc):
        saved["flow"] = doc
        return doc

    with _Patch((flow.store, "load_meta", lambda project_id: dict(meta)),
                (flow.store, "_blob", lambda: _Blob(content, blob_error)),
                (flow.store, "append_session_event", lambda project_id, event: dict(event)),
                (flow.persistence, "load_flow", lambda project_id: None),
                (flow.persistence, "save_flow", save_flow),
                (flow.persistence, "save_anchor", lambda project_id, doc: doc),
                (flow.anchor_mod, "requirement_snapshot_version",
                 lambda project_id: "reqsnap/1:abc"),
                (flow.anchor_mod, "empty_anchor", lambda project_id, run_id: {"run_id": run_id}),
                (flow.anchor_mod, "mark_stale", lambda *args, **kwargs: {}),
                (flow.anchor_mod, "refresh", lambda *args, **kwargs: {}),
                (flow.anchor_mod, "current_anchor", lambda project_id: {})):
        flow.start(PID, prompt="解析酒盒.dwg")
    return saved.get("flow") or {}


def _preflight(*, content_source=None, detect_calls=None):
    """跑第 1 步；`detect_calls` 用来记录依赖有没有被调用过。"""
    calls = detect_calls if detect_calls is not None else []

    class _Mod:
        @staticmethod
        def detect_file_format(filename, content):
            calls.append((filename, content))
            return {"detected_format": "dwg", "dwg_version": "AC1027",
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "file_size": len(content), "is_empty": not content,
                    "is_truncated": False, "content_kind": "binary"}

    ctx = {"filename": "酒盒.dwg", "content": b"", "drawing_version": 1,
           "resolve": lambda name: _Mod if name == "file_preflight" else None}
    if content_source is not None:
        ctx["content_source"] = content_source
        ctx["content_unavailable"] = ({"code": "drawing_source_unavailable",
                                       "reason": "RuntimeError"}
                                      if content_source == "unavailable" else {})
    return steps.file_preflight(ctx)


# --------------------------------------------------------------------------- #
# R 组：三态（读到 / 确实没有 / 读不到）必须两两可分
# --------------------------------------------------------------------------- #
class RSourceReadFailure(unittest.TestCase):
    def test_r1_context_discloses_unreadable_source(self):
        ctx = _context(meta={"source_path": "source.dwg"},
                       blob_error=RuntimeError("blob store down"))
        self.assertEqual("unavailable", ctx.get("content_source"),
                         "blob 读不到必须标出来（Spec §2.1）：今天的 ctx 里只有 b\"\"，"
                         "与'这个项目没有源附件'一模一样")
        flag = ctx.get("content_unavailable") or {}
        self.assertEqual("drawing_source_unavailable", flag.get("code"),
                         "这一趟读不到要显式披露")
        self.assertIn("RuntimeError", str(flag.get("reason") or ""), "reason 带异常类名")
        self.assertEqual(b"", ctx.get("content"), "content 仍是 bytes（本批只补披露）")

    def test_r2_context_reports_a_real_read_as_blob(self):
        ctx = _context(meta={"source_path": "source.dwg"}, content=REAL_CONTENT)
        self.assertEqual("blob", ctx.get("content_source"), "读到了就是 blob（键必须存在）")
        self.assertEqual({}, ctx.get("content_unavailable"), "正常时披露键必须是空的")
        self.assertEqual(REAL_CONTENT, ctx.get("content"), "读到的字节照旧原样交出去")

    def test_r3_no_source_attachment_is_not_a_read_failure(self):
        ctx = _context(meta={})
        self.assertEqual("none", ctx.get("content_source"),
                         "meta 里没有 source_path 是'确实没有'，与'读不到'必须分家（Spec §2.1）")
        self.assertEqual({}, ctx.get("content_unavailable") or {},
                         "'确实没有'没有异常可报，披露键给空")

    def test_r4_start_never_publishes_the_empty_content_hash(self):
        doc = _start(meta={"source_path": "source.dwg", "input_revision": 1},
                     blob_error=RuntimeError("blob store down"))
        inputs = doc.get("inputs") or {}
        self.assertNotEqual(EMPTY_CONTENT_SHA, inputs.get("source_sha256"),
                            "读不到源文件时写 `sha256(b\"\")` 是危险的（Spec §1.1）："
                            "它看起来完全合法，之后所有 stale 比对都会说'源文件没变'")
        self.assertEqual("", inputs.get("source_sha256"),
                         "读不到就是空串（'没有内容'不是一版内容）")
        self.assertEqual("unavailable", inputs.get("source_content"),
                         "落库的 inputs 要说清这一趟拿到的是哪一态")
        flag = inputs.get("source_content_unavailable") or {}
        self.assertEqual("drawing_source_unavailable", flag.get("code"),
                         "读不到要把披露写进 inputs")
        self.assertIn("RuntimeError", str(flag.get("reason") or ""))

    def test_r5_start_still_hashes_the_real_content(self):
        doc = _start(meta={"source_path": "source.dwg", "input_revision": 1},
                     content=REAL_CONTENT)
        inputs = doc.get("inputs") or {}
        self.assertEqual(hashlib.sha256(REAL_CONTENT).hexdigest(), inputs.get("source_sha256"),
                         "读得到时哈希口径逐字不变（护栏）")
        self.assertEqual("blob", inputs.get("source_content") or "blob",
                         "读得到时这一态是 blob（老文档没这个键时按 blob 兜底）")

    def test_r6_preflight_says_unreadable_not_empty(self):
        calls = []
        result = _preflight(content_source="unavailable", detect_calls=calls)
        self.assertEqual("DRAWING_SOURCE_UNAVAILABLE", result.get("error_code"),
                         "读不到源文件必须有自己的码（Spec §2.2）：今天给的是 FILE_EMPTY +"
                         " '上传的图纸是空文件，请重新上传'")
        self.assertEqual("failed", result.get("status"))
        self.assertIs(True, result.get("retryable"),
                      "这是可重试的读取故障（今天 FILE_EMPTY 是 retryable=False）")
        message = str(result.get("error_message") or "")
        self.assertNotIn("重新上传", message, "重传救不了读不到的源文件，文案不许把用户支去重传")
        self.assertIn("RuntimeError", message, "文案要说得出是哪一类失败")
        self.assertEqual([], calls,
                         "拿空字节去调 detect_file_format() 才会得出'文件是空的'——读不到时不许调")

    def test_r7_no_source_attachment_keeps_the_old_path(self):
        calls = []
        result = _preflight(content_source="none", detect_calls=calls)
        self.assertEqual(1, len(calls), "'确实没有源附件'照旧走既有预检（不许被本批改掉）")
        self.assertNotEqual("DRAWING_SOURCE_UNAVAILABLE", result.get("error_code"),
                            "本批只给'读不到'加码，'确实没有'照旧")

    def test_r8_legacy_context_without_the_key_is_unchanged(self):
        calls = []
        result = _preflight(content_source=None, detect_calls=calls)
        self.assertEqual(1, len(calls), "老 run / 单测的 ctx 没有这个键时照旧调预检（向后兼容）")
        self.assertIn(result.get("status"), ("completed", "failed", "unavailable"))

    def test_r9_error_code_is_registered(self):
        self.assertIn("DRAWING_SOURCE_UNAVAILABLE", model.ERROR_CODES,
                      "新码必须登记进 model.ERROR_CODES（否则 error_meta 会兜底成 500/不可重试）")
        self.assertEqual((503, True), model.ERROR_CODES.get("DRAWING_SOURCE_UNAVAILABLE"))


if __name__ == "__main__":
    unittest.main()

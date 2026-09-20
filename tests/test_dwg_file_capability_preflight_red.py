"""红测：DWG 文件能力契约、格式预检与正确失败 —— DWG 支持第 1 批。

Spec：`docs/specs/dwg-file-capability-preflight.md`。真实样本：
`裕同包装项目-待开发/酒盒.dwg`（686195 B）、`裕同包装项目-待开发/圆盘盒.dwg`（889062 B），
两份文件头都是 `AC1027`。样本只读，本文件不得修改它们。

现状缺口（首次运行时**必须失败**，是实测不是推断）：
  · `vision.build_input_manifest()` 只按扩展名分类，DWG 落 `unsupported/none`，**没有 magic 与版本判断**；
  · `vision._base_blocks()` 不读 manifest，**无条件**把主文件塞成图像块，
    `qwen_client._media_type_for(".dwg")` 兜底 `image/png` → 请求体出现
    `data:image/png;base64,<DWG 原始字节>` → Qwen 回 `The image format is illegal and cannot be opened`；
  · `POST /api/projects/3d`（`main.py:1425`）**没有格式门禁**：先 `store.create_project` 再
    `tasks.submit(..., "import_3d", cad=True)`，`step_import.import_step()` 用 DWG 后缀调
    `cq.importers.importStep` → 异步报 `STEP File could not be loaded`（先建项目、再建注定失败的任务）；
  · 前端 4 处入口把 DWG 列为支持格式，`app.js:1438` 却写着"到这一步还解析不了"。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import os
import pathlib
import sys
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLES_DIR = ROOT / "裕同包装项目-待开发"
WINE_BOX = SAMPLES_DIR / "酒盒.dwg"
ROUND_BOX = SAMPLES_DIR / "圆盘盒.dwg"
FRONTEND = ROOT / "tech_app" / "frontend"

DWG_MAGIC = b"AC1027"
#: Spec §3 的错误码 → (HTTP, retryable)
ERROR_CODES = {
    "FILE_EMPTY": (400, True),
    "FILE_TOO_LARGE": (413, True),
    "FILE_EXTENSION_CONTENT_MISMATCH": (422, True),
    "FILE_CORRUPTED": (422, True),
    "DWG_CONVERTER_NOT_INSTALLED": (415, True),
    "DWG_NOT_A_3D_MODEL": (415, False),
    "FILE_FORMAT_UNSUPPORTED": (415, False),
    "DWG_CONVERSION_FAILED": (502, True),
    # 以下 6 条由第 2 批「受控转换服务」提出，第 1 批 Spec §3 已收进同一闭集。
    "DWG_CONVERSION_TIMEOUT": (504, True),
    "DWG_CONVERTER_OUTPUT_MISSING": (502, True),
    "DWG_CONVERTER_OUTPUT_INVALID": (502, True),
    "DWG_CONVERTER_OUTPUT_TOO_LARGE": (502, True),
    "DWG_CONVERTER_UNSAFE_PATH": (500, False),
    "FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION": (500, False),
    "DWG_PARSE_FAILED": (502, True),
}
DETECTED_FORMATS = {"dwg", "dxf", "step", "iges", "stl", "pdf", "raster_image", "text",
                    "docx", "unsupported"}
PNG_BYTES = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 64)
PDF_BYTES = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
STEP_BYTES = (b"ISO-10303-21;\nHEADER;\nFILE_DESCRIPTION((''),'2;1');\nENDSEC;\nDATA;\nENDSEC;\n"
              b"END-ISO-10303-21;\n")


def load_preflight():
    return importlib.import_module("tech_app.backend.services.file_preflight")


def load_vision():
    return importlib.import_module("tech_app.backend.services.vision")


def load_main():
    return importlib.import_module("tech_app.backend.main")


class _StopCall(Exception):
    """哨兵：模型真的被调用了。"""


class PreflightCase(unittest.TestCase):
    def preflight(self):
        try:
            return load_preflight()
        except ModuleNotFoundError:
            self.fail("缺少 tech_app/backend/services/file_preflight.py（Spec §2）")

    def detect(self, filename, content):
        module = self.preflight()
        fn = getattr(module, "detect_file_format", None)
        self.assertTrue(callable(fn), "file_preflight 必须提供 detect_file_format()（Spec §2）")
        return fn(filename, content)

    def capabilities(self, detected):
        module = self.preflight()
        fn = getattr(module, "capabilities_of", None)
        self.assertTrue(callable(fn), "file_preflight 必须提供 capabilities_of()（Spec §2）")
        return fn(detected)

    def sample(self, path):
        if not path.exists():
            self.skipTest("客户样本不在本机（不入库）：%s" % path.name)
        return path.read_bytes()


# --------------------------------------------------------------------------- #
# A. 真实样本识别（不许只看扩展名）
# --------------------------------------------------------------------------- #
class ASampleDetection(PreflightCase):
    def test_a1_both_samples_are_detected_as_dwg_ac1027(self):
        for path in (WINE_BOX, ROUND_BOX):
            detected = self.detect(path.name, self.sample(path))
            self.assertEqual(detected.get("detected_format"), "dwg", path.name)
            self.assertEqual(detected.get("dwg_version"), "AC1027", path.name)
            self.assertIn("AC1027", detected.get("magic") or "", path.name)
            self.assertFalse(detected.get("extension_content_mismatch"), path.name)

    def test_a2_sha256_and_size_are_reported(self):
        content = self.sample(WINE_BOX)
        detected = self.detect(WINE_BOX.name, content)
        self.assertEqual(detected.get("file_size"), len(content))
        self.assertEqual(detected.get("sha256"), hashlib.sha256(content).hexdigest())

    def test_a3_renamed_to_png_is_still_dwg_and_flagged(self):
        detected = self.detect("酒盒.png", self.sample(WINE_BOX))
        self.assertEqual(detected.get("detected_format"), "dwg", "内容才是事实，扩展名不是")
        self.assertTrue(detected.get("extension_content_mismatch"))

    def test_a4_renamed_to_step_is_still_dwg_and_flagged(self):
        detected = self.detect("酒盒.step", self.sample(WINE_BOX))
        self.assertEqual(detected.get("detected_format"), "dwg")
        self.assertTrue(detected.get("extension_content_mismatch"))
        self.assertFalse(self.capabilities(detected).get("step_import"),
                         "DWG 字节不得被当成可做 STEP 导入")

    def test_a5_uppercase_extension_is_equivalent(self):
        detected = self.detect("ROUND.DWG", self.sample(ROUND_BOX))
        self.assertEqual(detected.get("detected_format"), "dwg")
        self.assertEqual(detected.get("extension"), ".dwg")

    def test_a6_empty_and_truncated_and_oversized_get_distinct_states(self):
        empty = self.detect("empty.dwg", b"")
        self.assertTrue(empty.get("is_empty"))
        self.assertEqual(empty.get("detected_format"), "unsupported")
        truncated = self.detect("cut.dwg", DWG_MAGIC + b"\x00" * 10)
        self.assertTrue(truncated.get("is_truncated"), "只有头部、没有实体段的 DWG 必须判截断")
        for name in ("酒盒.dwg", "酒盒.png", "酒盒.step"):
            huge = self.detect(name, DWG_MAGIC + b"\x00" * (80 * 1024 * 1024))
            self.assertEqual(huge.get("file_size"), 80 * 1024 * 1024)
            self.assertIn(huge.get("detected_format"), DETECTED_FORMATS)

    def test_a7_detected_format_is_a_closed_set(self):
        for name, content in (("a.png", PNG_BYTES), ("a.pdf", PDF_BYTES), ("a.step", STEP_BYTES),
                              ("a.txt", b"hello"), ("a.bin", b"\x00\x01\x02"),
                              ("a.dwg", DWG_MAGIC + b"\x00" * 4096)):
            detected = self.detect(name, content)
            self.assertIn(detected.get("detected_format"), DETECTED_FORMATS,
                          "%s 的 detected_format 不在闭集里" % name)


class BCapabilities(PreflightCase):
    def test_b1_dwg_is_never_direct_vision_and_needs_a_converter(self):
        # 第 2 批会把 converter_available 接到 cad_converter.capability()，
        # 这里显式禁用来锁死"未安装"语义（默认环境同样是未安装）。
        with mock.patch.dict(os.environ, {"CAD_CONVERTER": "none"}):
            caps = self.capabilities(self.detect("酒盒.dwg", DWG_MAGIC + b"\x00" * 4096))
        self.assertFalse(caps.get("direct_vision"), "DWG 原始字节永远不许直接喂视觉模型")
        self.assertTrue(caps.get("converter_required"))
        self.assertFalse(caps.get("converter_available"), "未安装转换器时必须为假")
        self.assertFalse(caps.get("step_import"))
        self.assertFalse(caps.get("geometry_3d"))

    def test_b2_raster_image_keeps_direct_vision(self):
        caps = self.capabilities(self.detect("a.png", PNG_BYTES))
        self.assertTrue(caps.get("direct_vision"), "图片路径不得回归")
        self.assertFalse(caps.get("converter_required"))

    def test_b3_step_keeps_3d_import(self):
        caps = self.capabilities(self.detect("a.step", STEP_BYTES))
        self.assertTrue(caps.get("step_import"))
        self.assertFalse(caps.get("direct_vision"))

    def test_b4_pdf_and_text_have_their_own_paths(self):
        pdf = self.capabilities(self.detect("a.pdf", PDF_BYTES))
        self.assertTrue(pdf.get("document_text"), "PDF 走本地文本提取路径")
        self.assertFalse(pdf.get("converter_required"))
        text = self.capabilities(self.detect("a.txt", b"hello"))
        self.assertTrue(text.get("document_text"))


# --------------------------------------------------------------------------- #
# C. 错误码闭集与 HTTP 映射
# --------------------------------------------------------------------------- #
class CErrorCodes(PreflightCase):
    def test_c1_error_code_table_matches_spec(self):
        module = self.preflight()
        table = getattr(module, "STABLE_ERROR_CODES", None)
        self.assertIsInstance(table, dict, "必须导出 STABLE_ERROR_CODES（Spec §3）")
        self.assertEqual(set(table), set(ERROR_CODES),
                         "错误码必须是 Spec §3 的闭集，不许自创或漏项")
        for code, (status, retryable) in ERROR_CODES.items():
            self.assertEqual(table[code].get("http_status"), status, code)
            self.assertEqual(table[code].get("retryable"), retryable, code)
            self.assertTrue(table[code].get("message"), "%s 必须有中文用户文案" % code)

    def test_c2_capability_error_carries_the_stable_code(self):
        module = self.preflight()
        exc_type = getattr(module, "FileCapabilityError", None)
        self.assertTrue(isinstance(exc_type, type) and issubclass(exc_type, Exception),
                        "必须提供 FileCapabilityError（Spec §4）")
        error = exc_type("DWG_CONVERTER_NOT_INSTALLED", detected={"detected_format": "dwg"})
        self.assertEqual(error.stable_error_code, "DWG_CONVERTER_NOT_INSTALLED")
        self.assertEqual(error.http_status, 415)
        self.assertTrue(error.retryable)
        self.assertIn("转换", error.message)


# --------------------------------------------------------------------------- #
# D. 模型调用门禁（本批核心）
# --------------------------------------------------------------------------- #
class DModelCallGate(PreflightCase):
    def spy_run(self, calls):
        def _run(system, content, schema, *args, **kwargs):
            calls.append({"system": system, "content": content, "schema": schema})
            raise _StopCall()
        return _run

    def blocks_of(self, calls):
        return list(calls[-1]["content"]) if calls else []

    def test_d1_dwg_never_reaches_the_vision_model(self):
        vision = load_vision()
        content = self.sample(WINE_BOX)
        calls = []
        with mock.patch.object(vision.claude_client, "run", self.spy_run(calls)):
            with self.assertRaises(Exception) as caught:
                vision.parse_drawing(content, WINE_BOX.name, note="解析酒盒")
        self.assertNotIsInstance(caught.exception, _StopCall,
                                 "DWG 在调用视觉模型之前就必须被拦下，实测却调用了模型")
        self.assertEqual(getattr(caught.exception, "stable_error_code", None),
                         "DWG_CONVERTER_NOT_INSTALLED")
        self.assertEqual(calls, [], "不得调用 claude_client.run")

    def test_d2_no_image_block_carries_dwg_bytes(self):
        vision = load_vision()
        content = self.sample(ROUND_BOX)
        calls = []
        try:
            with mock.patch.object(vision.claude_client, "run", self.spy_run(calls)):
                vision.parse_drawing(content, ROUND_BOX.name)
        except Exception:
            pass
        for call in calls:
            for block in call["content"]:
                self.assertNotIn(block.get("type"), {"image", "image_url", "input_image"},
                                 "任何情况下都不许把 DWG 当图片送进模型")
        blob = repr(calls)
        self.assertNotIn(content[:64].hex(), blob)
        self.assertNotIn("AC1027", blob)

    def test_d3_verify_drawing_has_the_same_gate(self):
        vision = load_vision()

        class _FakeIR:
            device_name = "酒盒"
            design_intent = "包装盒"

            def model_dump_json(self, **kwargs):
                return "{}"

        calls = []
        with mock.patch.object(vision.claude_client, "run", self.spy_run(calls)):
            with self.assertRaises(Exception) as caught:
                vision.verify_drawing(_FakeIR(), self.sample(WINE_BOX), WINE_BOX.name)
        self.assertNotIsInstance(caught.exception, _StopCall, "校验这一遍也必须先拦下 DWG")
        self.assertEqual(getattr(caught.exception, "stable_error_code", None),
                         "DWG_CONVERTER_NOT_INSTALLED")
        self.assertEqual(calls, [])

    def test_d4_dwg_as_attachment_is_text_placeholder_only(self):
        vision = load_vision()
        with mock.patch.object(vision.claude_client, "run", self.spy_run([])) as _:
            pass
        try:
            vision._attachment_blocks([("酒盒.dwg", self.sample(WINE_BOX))])
        except AttributeError:
            self.fail("vision._attachment_blocks 必须存在（Spec §4）")
        blocks = vision._attachment_blocks([("酒盒.dwg", self.sample(WINE_BOX))])
        self.assertTrue(blocks, "DWG 附件必须产生说明块，而不是被静默丢弃")
        for block in blocks:
            self.assertNotIn(block.get("type"), {"image", "image_url", "input_image"},
                             "DWG 附件不得作为图片发送")
        text = " ".join(str(block.get("text") or "") for block in blocks)
        self.assertIn("转换", text,
                      "DWG 附件的占位说明必须写明需要 CAD 转换服务（Spec §4）")

    def test_d5_raster_path_does_not_regress(self):
        vision = load_vision()
        calls = []
        with mock.patch.object(vision.claude_client, "run", self.spy_run(calls)):
            with self.assertRaises(_StopCall):
                vision.parse_drawing(PNG_BYTES, "drawing.png")
        kinds = [block.get("type") for block in self.blocks_of(calls)]
        self.assertTrue(set(kinds) & {"image", "image_url", "input_image"},
                        "图片路径必须继续把原图发给视觉模型")

    def test_d6_manifest_is_part_of_the_failure_evidence(self):
        vision = load_vision()
        try:
            vision.parse_drawing(self.sample(WINE_BOX), WINE_BOX.name)
        except Exception as exc:
            detected = getattr(exc, "detected", None)
        else:
            detected = None
        self.assertIsInstance(detected, dict, "失败必须带 detected 预检结果（Spec §4）")
        self.assertEqual(detected.get("detected_format"), "dwg")


# --------------------------------------------------------------------------- #
# E. 3D 入口同步拒绝
# --------------------------------------------------------------------------- #
class _FakeUpload:
    def __init__(self, filename, content):
        self.filename = filename
        self.size = len(content)
        self._content = content

    async def read(self, size=-1):
        data, self._content = self._content, b""
        return data


class EThreeDGate(PreflightCase):
    def call_route(self, filename, content, *, step_available=True):
        main = load_main()
        fake = _FakeUpload(filename, content)
        with mock.patch.object(main.store, "create_project") as create, \
                mock.patch.object(main.tasks, "submit") as submit, \
                mock.patch.object(main.step_import, "import_step") as importer, \
                mock.patch.object(main.step_import, "AVAILABLE", step_available):
            error = None
            result = None
            try:
                result = asyncio.run(main.upload_3d(file=fake, note="", user={
                    "username": "tester", "role": "engineer"}))
            except BaseException as exc:  # noqa: BLE001 - 只做分类
                error = exc
        return {"result": result, "error": error, "create_project": create,
                "submit": submit, "import_step": importer}

    def code_of(self, error):
        detail = getattr(error, "detail", None)
        if isinstance(detail, dict):
            return detail.get("stable_error_code"), getattr(error, "status_code", None)
        return None, getattr(error, "status_code", None)

    def test_e1_dwg_is_rejected_synchronously_with_a_stable_code(self):
        outcome = self.call_route(WINE_BOX.name, self.sample(WINE_BOX))
        self.assertIsNone(outcome["result"], "DWG 不得返回 project_id/task_id")
        code, status = self.code_of(outcome["error"])
        self.assertEqual(code, "DWG_NOT_A_3D_MODEL")
        self.assertEqual(status, 415)

    def test_e2_no_project_is_created_and_no_task_is_submitted(self):
        outcome = self.call_route(ROUND_BOX.name, self.sample(ROUND_BOX))
        self.assertFalse(outcome["create_project"].called,
                         "拒绝必须发生在 store.create_project 之前")
        self.assertFalse(outcome["submit"].called,
                         "不得创建注定失败的 import_3d 异步任务")
        self.assertFalse(outcome["import_step"].called)

    def test_e3_dwg_renamed_to_step_is_still_rejected(self):
        outcome = self.call_route("酒盒.step", self.sample(WINE_BOX))
        code, status = self.code_of(outcome["error"])
        self.assertIn(code, {"FILE_EXTENSION_CONTENT_MISMATCH", "DWG_NOT_A_3D_MODEL"})
        self.assertFalse(outcome["import_step"].called, "不许把 DWG 交给 STEP 导入器")

    def test_e4_png_sent_to_3d_entry_is_rejected_before_any_project(self):
        outcome = self.call_route("drawing.png", PNG_BYTES)
        code, status = self.code_of(outcome["error"])
        self.assertEqual(code, "FILE_FORMAT_UNSUPPORTED")
        self.assertFalse(outcome["create_project"].called)

    def test_e5_real_step_still_goes_to_the_3d_pipeline(self):
        outcome = self.call_route("model.step", STEP_BYTES)
        self.assertIsNone(outcome["error"], "真 STEP 不得被新门禁拦下")
        self.assertTrue(outcome["create_project"].called)
        self.assertTrue(outcome["submit"].called)

    def test_e6_empty_file_has_its_own_code(self):
        outcome = self.call_route("empty.step", b"")
        code, status = self.code_of(outcome["error"])
        self.assertEqual(code, "FILE_EMPTY")
        self.assertFalse(outcome["create_project"].called)


# --------------------------------------------------------------------------- #
# F. 前端能力展示必须真实
# --------------------------------------------------------------------------- #
class FFrontendClaims(PreflightCase):
    """只断言布尔结论，失败信息里不回显整个前端文件（避免刷屏）。"""

    def frontend_text(self):
        names = ("home.js", "tech-task.js", "requirement.js", "requirement-create.js")
        return {name: (FRONTEND / name).read_text(encoding="utf-8", errors="replace")
                for name in names}

    def test_f1_every_dwg_upload_entry_says_a_converter_is_required(self):
        missing = [name for name, text in self.frontend_text().items()
                   if ".dwg" in text.lower() and "转换" not in text]
        self.assertEqual(
            missing, [],
            "这些入口让用户上传 DWG 却没说明需要 CAD 转换服务（Spec §6）：%s" % missing)

    def test_f2_no_page_claims_dwg_is_parsed_directly(self):
        offenders = []
        for name, text in self.frontend_text().items():
            for banned in ("DWG 可直接解析", "模型直接读取 DWG", "支持 DWG 解析"):
                if banned in text:
                    offenders.append("%s:%s" % (name, banned))
        self.assertEqual(offenders, [], "不得宣称 DWG 可直接解析")

    def test_f3_home_keeps_the_three_format_labels(self):
        home = self.frontend_text()["home.js"]
        self.assertTrue("DWG" in home and "STEP" in home,
                        "首页仍要保留 2D/3D 格式标签，但必须带真实的转换说明")

    def test_f4_unconverted_dwg_is_never_marked_parsed(self):
        source = (FRONTEND / "app.js").read_text(encoding="utf-8", errors="replace")
        self.assertTrue("解析不了" in source,
                        "app.js:1438 的诚实说明必须保留（Spec §6）")
        offenders = [name for name, text in self.frontend_text().items()
                     if "DWG解析完成" in text or "dwg_parsed" in text]
        self.assertEqual(offenders, [], "未转换的 DWG 不得被标成已解析")


if __name__ == "__main__":
    unittest.main(verbosity=2)

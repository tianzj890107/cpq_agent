"""CI / 红测用的假转换器（Spec §2.4）：验证编排层，不碰真实 CAD 内核。

**生产环境禁止启用**：`service.get_adapter()` 在 `APP_ENV=production`（或未显式
`CAD_CONVERTER_ALLOW_SIMULATED=true`）时直接以
`FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION` 拒绝，不会静默回退到这里。

唯一的失败注入点是构造参数；适配器不读环境变量、不按文件名猜行为。产出的 manifest
必须自报 `is_simulated=True`，任何 UI / 报告都不得把它当真实转换。
"""
from __future__ import annotations

import hashlib
import tempfile
import threading
import time
from pathlib import Path

from .base import SOURCE_FILENAME

#: 故障注入模式闭集（Spec §2.4）；闭集之外一律 ValueError，不许静默当成 None。
FAILURE_MODES = ("timeout", "nonzero_exit", "missing_output", "empty_output",
                 "oversized_output", "unsafe_path")

#: 预览格式闭集
PREVIEW_FORMATS = ("png", "svg", "pdf")

#: timeout 模式阻塞多久（必须超过红测给的 0.3s 超时）
TIMEOUT_BLOCK_SECONDS = 2.0

#: oversized 模式写多大（默认上限 256 MiB 不会触发，红测会把上限压到 8 KiB）
OVERSIZED_EXTRA_BYTES = 128 * 1024

#: 逃逸目录：故意写在 output_dir 之外，编排层必须拒绝
ESC_DIR_NAME = "dwg-conv-escape"

_PREVIEW_BYTES = {
    "png": b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 64,
    "pdf": b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n",
    "svg": b"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
           b"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"10\" height=\"10\">"
           b"<line x1=\"0\" y1=\"0\" x2=\"10\" y2=\"10\"/></svg>\n",
}

#: 最小可读 ASCII DXF：batch 3 的矢量解析要能直接读它，因此这里就给一份合法骨架。
_DXF_FIXTURE = (
    "0\nSECTION\n2\nHEADER\n0\nENDSEC\n"
    "0\nSECTION\n2\nTABLES\n0\nENDSEC\n"
    "0\nSECTION\n2\nENTITIES\n"
    "0\nLINE\n8\n0\n10\n0.0\n20\n0.0\n11\n100.0\n21\n50.0\n"
    "0\nCIRCLE\n8\n0\n10\n30.0\n20\n30.0\n40\n10.0\n"
    "0\nENDSEC\n0\nEOF\n"
)


class FakeAdapter:
    """契约见 Spec §2.4（红测按这些签名直接调用）。"""

    name = "fake"

    def __init__(self, *, version: str = "0.0.0", failure_mode=None,
                 three_d: str = "not_present", preview_format: str = "png",
                 stderr: str = "", dxf_text=None) -> None:
        if failure_mode is not None and failure_mode not in FAILURE_MODES:
            raise ValueError("未知的故障注入模式：%r" % (failure_mode,))
        if preview_format not in PREVIEW_FORMATS:
            raise ValueError("未知的预览格式：%r" % (preview_format,))
        self._version = str(version)
        self._failure_mode = failure_mode
        self._three_d = str(three_d)
        self._preview_format = preview_format
        self._stderr = str(stderr or "")
        self._dxf_text = dxf_text
        self._lock = threading.Lock()
        self._calls = []
        self._convert_calls = 0

    # ------------------------------------------------------------------ 身份
    @property
    def declared_version(self) -> str:
        """注入故障的 fake 与干净的 fake 不是同一个转换器（产物不可互换），
        因此把故障配置计入声明版本 —— 缓存键随之变化，故障产物绝不会被当成缓存命中。"""
        if self._failure_mode:
            return "%s+%s" % (self._version, self._failure_mode)
        return self._version

    @property
    def calls(self):
        with self._lock:
            return [dict(item) for item in self._calls]

    @property
    def convert_calls(self) -> int:
        with self._lock:
            return self._convert_calls

    # ------------------------------------------------------------ 协议方法
    def capability(self) -> dict:
        return {
            "name": self.name,
            "version": self.declared_version,
            "dwg_conversion": True,
            "preview_render": True,
            "three_d_conversion": self._three_d == "present",
            "simulated": True,
        }

    def inspect(self, request) -> dict:
        self._record("inspect", request)
        warnings = []
        if self._failure_mode:
            warnings.append("模拟转换器：未接真实 CAD 内核，仅用于编排层验证")
        return {
            "detected_dwg_version": str(getattr(request, "detected_dwg_version", "")),
            "has_2d_entities": self._failure_mode is None,
            "three_d": self._three_d,
            "warnings": warnings,
        }

    def convert_to_dxf(self, request) -> dict:
        self._record("convert_to_dxf", request)
        mode = self._failure_mode
        output_dir = Path(getattr(request, "output_dir"))

        if mode == "timeout":
            # 阻塞到超过编排层给的超时；不写任何文件，编排层超时后清理临时目录也安全。
            time.sleep(TIMEOUT_BLOCK_SECONDS)
            return self._result(dxf_path=None)

        if mode == "nonzero_exit":
            return self._result(status="failed", error_code="DWG_CONVERSION_FAILED",
                                stderr_digest=self._stderr_digest())

        if mode == "missing_output":
            # 自称成功，却既不写文件也不给路径。
            return self._result(dxf_path=None)

        if mode == "unsafe_path":
            escaped = Path(tempfile.gettempdir()) / ESC_DIR_NAME / "escaped.dxf"
            escaped.parent.mkdir(parents=True, exist_ok=True)
            escaped.write_bytes(self._dxf_bytes())
            return self._result(dxf_path=escaped)

        target = output_dir / "converted.dxf"
        if mode == "empty_output":
            target.write_bytes(b"")
        elif mode == "oversized_output":
            target.write_bytes(self._dxf_bytes() + b"\n" * OVERSIZED_EXTRA_BYTES)
        else:
            target.write_bytes(self._dxf_bytes())
        return self._result(dxf_path=target)

    def render_preview(self, request, dxf_path=None) -> dict:
        self._record("render_preview", request)
        target = Path(getattr(request, "output_dir")) / ("preview." + self._preview_format)
        target.write_bytes(_PREVIEW_BYTES[self._preview_format])
        return self._result(preview_paths=[target])

    def convert_3d_if_supported(self, request) -> dict:
        self._record("convert_3d_if_supported", request)
        if self._three_d != "present":
            return {"status": "not_present", "artifact_path": None, "error_code": None}
        return {"status": "requires_real_converter", "artifact_path": None,
                "error_code": "DWG_CONVERTER_NOT_INSTALLED"}

    # ---------------------------------------------------------------- 内部
    def _record(self, method: str, request) -> None:
        item = {
            "method": method,
            "source_sha256": str(getattr(request, "source_sha256", "")),
            "source_filename": str(getattr(request, "source_filename", SOURCE_FILENAME)),
            "conversion_id": str(getattr(request, "conversion_id", "")),
        }
        with self._lock:
            self._calls.append(item)
            if method == "convert_to_dxf":
                self._convert_calls += 1

    def _dxf_bytes(self) -> bytes:
        if self._dxf_text is not None:
            text = str(self._dxf_text)
            return text.encode("utf-8") if isinstance(self._dxf_text, str) else bytes(self._dxf_text)
        return _DXF_FIXTURE.encode("ascii")

    def _stderr_digest(self):
        if not self._stderr:
            return None
        return hashlib.sha256(self._stderr.encode("utf-8")).hexdigest()

    def _result(self, *, status: str = "ok", dxf_path=None, preview_paths=None,
                warnings=None, error_code=None, stderr_digest=None) -> dict:
        return {
            "status": status,
            "dxf_path": dxf_path,
            "preview_paths": list(preview_paths or []),
            "warnings": list(warnings or []),
            "error_code": error_code,
            "stderr_digest": stderr_digest,
            "three_d": {"status": self._three_d},
        }

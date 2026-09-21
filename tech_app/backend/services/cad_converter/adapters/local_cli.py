"""本地 CLI 转换器适配器（第 2 批 Spec §2.4/§6 + 前两批修复 Spec §2/§3）。

只交付「怎么探测、怎么用 argv 列表调用」，不内置、不安装任何转换器二进制；探测不到就当
未安装。调用一律 `subprocess.run(argv 列表, timeout=…, shell=False)`：没有 shell 参与，
转换器拿不到命令字符串，也就没有注入面。

**按驱动分派 argv**（Spec §3，形状已按 LibreDWG 0.14 实测）：

| 驱动 | 命令行形状 | 产物名 | argv_verified |
| --- | --- | --- | --- |
| `libredwg_dwg2dxf` | `dwg2dxf -y -o <out.dxf> <source.dwg>` | `converted.dxf` | 已实测 `True` |
| `libredwg_dwgread` | `dwgread -O DXF -o <out.dxf> <source.dwg>` | `converted.dxf` | 已实测 `True` |
| `oda_file_converter` | `<exe> <inDir> <outDir> ACAD2018 DXF 0 1 *.dwg` | `source.dxf` | 已实测 `True` |

`wrapper`（如 Linux 无头必需的 `xvfb-run -a`）由配置给出，**逐项排在 exe 之前**；配置只做
空白切分，不经过 shell，出现 `; | & $ > < * ? ~ ( )` 等元字符或首项不可执行一律判
`wrapper_invalid`（Spec §2/§3）。

预览：libredwg 用同目录的 `dwg2SVG --mspace <source.dwg>`，**输出走 stdout**（该工具没有
`-o`），由本适配器把 stdout 落成 `converted.svg`。SVG 只用于页面预览，**不是**栅格图，
禁止直接当图片送视觉模型。
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from .base import SOURCE_FILENAME

#: 单次调用的硬上限（秒）；与编排层的 CAD_CONVERTER_TIMEOUT_SECONDS 取小值生效。
HARD_TIMEOUT_SECONDS = 300.0
#: 版本探测的硬上限（秒）：探测失败只让版本变成空串，绝不拖住预检。
VERSION_TIMEOUT_SECONDS = 5.0

#: 预览工具（与转换器同目录的约定名）
PREVIEW_COMMAND = "dwg2SVG"
#: 预览产物名（stdout 落盘）
PREVIEW_OUTPUT_NAME = "converted.svg"

#: 禁止当转换器二进制的解释器（Spec §2：防止把 shell/python 当 CAD 转换器）。
_INTERPRETER_NAMES = frozenset({
    "sh", "bash", "zsh", "dash", "ksh", "csh", "tcsh", "fish", "env", "xargs",
    "perl", "ruby", "node", "osascript",
})
_INTERPRETER_PATTERN = re.compile(r"^python[0-9.]*$")
#: 二进制不可用的原因（稳定短码，进 detected 与文案）
BINARY_MISSING = "binary_missing"
BINARY_NOT_EXECUTABLE = "binary_not_executable"
BINARY_NOT_A_FILE = "binary_not_a_file"
BINARY_IS_INTERPRETER = "binary_is_interpreter"
BINARY_WRAPPER_INVALID = "wrapper_invalid"

#: 版本口径闭集（Spec §2.5）：`--version` 探出来的 / 配置声明的 / 无法核实的。
VERSION_SOURCE_PROBED = "probed"
VERSION_SOURCE_CONFIG = "config_declared"
VERSION_SOURCE_UNVERIFIABLE = "unverifiable"

#: wrapper 里绝不允许出现的 shell 元字符（Spec §2/§3）：一律按 `wrapper_invalid` 处理。
_WRAPPER_META = re.compile(r"""[;|&$><*?~()\[\]{}`"'!]""")

_VERSION_PATTERN = re.compile(rb"(\d+\.\d+(?:\.\d+)*)")
_version_cache: Dict[str, str] = {}
_version_lock = threading.Lock()

_ODA_OUTPUT_VERSION = "ACAD2018"
_ODA_OUTPUT_FORMAT = "DXF"


# --------------------------------------------------------------------------- #
# 驱动：argv 形状与产物名
# --------------------------------------------------------------------------- #
def _argv_libredwg_dwg2dxf(executable: str, source: Path, output_dir: Path, target: Path) -> list:
    # `-y`：临时目录里的目标文件若因重试已存在，允许覆盖；`-o` 只对单个输入有效。
    return [executable, "-y", "-o", str(target), str(source)]


def _argv_libredwg_dwgread(executable: str, source: Path, output_dir: Path, target: Path) -> list:
    return [executable, "-O", "DXF", "-o", str(target), str(source)]


def _argv_oda_file_converter(executable: str, source: Path, output_dir: Path, target: Path) -> list:
    # ODA File Converter 真机 27.1 实测的 7 个位置参数：
    # <inDir> <outDir> <outVer> <outFormat> <recurse> <audit> <filter>。最后一项 `*.dwg`
    # 必须逐字给出（不经 shell 展开）；它是过滤项，不是 glob，由转换器自己解释。
    # 它按输入文件名出图（`source.dwg` → `source.dxf`），不接受单个输出路径。
    return [executable, str(source.parent), str(output_dir), _ODA_OUTPUT_VERSION,
            _ODA_OUTPUT_FORMAT, "0", "1", "*.dwg"]


DRIVERS: Dict[str, dict] = {
    "libredwg_dwg2dxf": {
        "argv": _argv_libredwg_dwg2dxf, "output_name": "converted.dxf",
        "version_args": ("--version",), "argv_verified": True, "provider": "libredwg",
        "output_version": "", "audit_enabled": False,
    },
    "libredwg_dwgread": {
        "argv": _argv_libredwg_dwgread, "output_name": "converted.dxf",
        "version_args": ("--version",), "argv_verified": True, "provider": "libredwg-cli",
        "output_version": "", "audit_enabled": False,
    },
    "oda_file_converter": {
        "argv": _argv_oda_file_converter, "output_name": "source.dxf",
        # ODA 不支持 `--version`（会以非 0 退出）；版本只能由配置显式声明，
        # 所以 version_args 为空 = 编排层**零子进程**（Spec §2.5）。
        "version_args": (), "argv_verified": True, "provider": "oda",
        "output_version": _ODA_OUTPUT_VERSION, "audit_enabled": True,
        # ODA 要的是「输入目录 + 输出目录」两个**真实目录**（不是输出文件路径），
        # 所以编排层必须给它一个调用结束后依然存在的工作区（见 `work_dir_for`）。
        "needs_dirs": True,
    },
}

#: provider -> 该 provider 的候选可执行文件（按顺序；装哪个用哪个）
PROVIDER_COMMANDS: Dict[str, Tuple[str, ...]] = {
    "libredwg": ("dwg2dxf", "dwgread"),
    "libredwg-cli": ("dwgread",),
    "oda": ("ODAFileConverter",),
    "teigha": ("TeighaFileConverter",),
}

#: provider -> 默认驱动（候选二进制改名时再按文件名细化，见 `driver_of`）
PROVIDER_DRIVERS: Dict[str, str] = {
    "libredwg": "libredwg_dwg2dxf",
    "libredwg-cli": "libredwg_dwgread",
    "oda": "oda_file_converter",
    "teigha": "oda_file_converter",
}

#: `auto` 的探测顺序（Spec §2.4：**ODA 优先**，其后才是 LibreDWG 系与 Teigha）。
#: 探测只用 `shutil.which`，不执行任何子进程。
AUTO_PROBE: Tuple[str, ...] = ("oda", "libredwg", "libredwg-cli", "teigha")

#: 已知 provider 名（含旧名 `CAD_CONVERTER` 用过的别名）
KNOWN_PROVIDERS = ("auto", "none", "fake", "libredwg", "libredwg-cli", "oda", "teigha")


# --------------------------------------------------------------------------- #
# 探测
# --------------------------------------------------------------------------- #
def executable_path(name: str) -> str:
    """在 `PATH` 里探测可执行文件；不存在返回空串。"""
    return shutil.which(str(name)) or ""


def probe(provider: str) -> str:
    """按 provider 探测二进制（`auto` 按顺序试所有候选）；找不到返回空串。

    只在**没有显式配置二进制**时使用；显式配置一律直接用，不经 `PATH`。
    """
    want = str(provider or "").strip().lower()
    order = AUTO_PROBE if want in ("", "auto") else (want,)
    for key in order:
        for command in PROVIDER_COMMANDS.get(key, ()):
            found = executable_path(command)
            if found:
                return found
    return ""


def installed_names() -> list:
    """已装好的 provider 名（按 auto 探测顺序，去重）。"""
    names = []
    for key in AUTO_PROBE:
        if any(executable_path(command) for command in PROVIDER_COMMANDS.get(key, ())):
            if key not in names:
                names.append(key)
    return names


def driver_of(provider: str, binary: str) -> str:
    """确定该二进制该用哪套 argv。文件名能细化时以文件名为准。"""
    base = Path(str(binary or "")).name.lower()
    if "dwgread" in base:
        return "libredwg_dwgread"
    if "dwg2dxf" in base:
        return "libredwg_dwg2dxf"
    if "oda" in base or "teigha" in base:
        return "oda_file_converter"
    return PROVIDER_DRIVERS.get(str(provider or "").strip().lower(), "")


def provider_of_binary(binary: str) -> str:
    """从二进制文件名反推 provider（`auto` + 显式二进制时用）；认不出返回空串。"""
    driver = driver_of("", binary)
    return str(DRIVERS.get(driver, {}).get("provider") or "") if driver else ""


def parse_wrapper(raw: str) -> Tuple[list, str]:
    """解析 `DWG_CONVERTER_WRAPPER`（Spec §2/§3）：返回 `(items, reason)`。

    只按空白切分，**不做 shell 解析**（没有引号、没有变量展开、没有管道）。任何一项出现
    shell 元字符，或首项不是存在且可执行的文件，一律 `(空, "wrapper_invalid")`。
    空配置 → `([], "")`。
    """
    text = str(raw or "").strip()
    if not text:
        return [], ""
    if _WRAPPER_META.search(text):
        return [], BINARY_WRAPPER_INVALID
    items = text.split()
    if not items:
        return [], ""
    for token in items:
        if not token or _WRAPPER_META.search(token):
            return [], BINARY_WRAPPER_INVALID
    first = items[0]
    resolved = first if os.sep in first else (shutil.which(first) or "")
    if not resolved or not os.path.isfile(resolved) or not os.access(resolved, os.X_OK):
        return [], BINARY_WRAPPER_INVALID
    return items, ""


def supports_version_probe(driver: str) -> bool:
    """该驱动是否能用 `--version` 探测版本（ODA 不行：它没有这个选项）。"""
    return bool(DRIVERS.get(str(driver or ""), {}).get("version_args"))


def version_source_for(driver: str, *, declared: str = "", probed: str = "") -> str:
    """版本来源三态（Spec §2.5）：能探测的驱动报 `probed`，其余按声明与否二选一。"""
    if supports_version_probe(driver):
        return VERSION_SOURCE_PROBED if probed else VERSION_SOURCE_UNVERIFIABLE
    return VERSION_SOURCE_CONFIG if declared else VERSION_SOURCE_UNVERIFIABLE


def output_version_of(driver: str) -> str:
    """该驱动**声明**的输出版本（ODA 恒为 ACAD2018；LibreDWG 尽力保留源版本 → 空串）。"""
    return str(DRIVERS.get(str(driver or ""), {}).get("output_version") or "")


def audit_enabled_of(driver: str) -> bool:
    """该驱动是否开启转换器自带的 Audit/Repair（Spec §4.5）。"""
    return bool(DRIVERS.get(str(driver or ""), {}).get("audit_enabled"))


def binary_problem(binary: str) -> str:
    """二进制安全检查（Spec §2）；可用返回空串，否则返回稳定原因短码。"""
    path = str(binary or "")
    if not path:
        return BINARY_MISSING
    if not os.path.exists(path):
        return BINARY_MISSING
    if not os.path.isfile(path):
        return BINARY_NOT_A_FILE
    name = Path(path).name.lower()
    if name in _INTERPRETER_NAMES or _INTERPRETER_PATTERN.match(name):
        return BINARY_IS_INTERPRETER
    if not os.access(path, os.X_OK):
        return BINARY_NOT_EXECUTABLE
    return ""


def resolve_binary(provider: str, binary: str = "") -> str:
    """解析出本次要用的转换器二进制：显式配置优先（不经 PATH），否则按 provider 探测。"""
    explicit = str(binary or "").strip()
    if explicit:
        return explicit
    return probe(provider)


def preview_binary_for(binary: str, explicit: str = "") -> str:
    """预览渲染工具：显式配置优先，缺省取转换器**同目录**的 `dwg2SVG`（Spec §2）。"""
    wanted = str(explicit or "").strip()
    if wanted:
        return wanted
    directory = str(Path(str(binary or "")).parent)
    if not directory or directory == ".":
        return ""
    candidate = Path(directory) / PREVIEW_COMMAND
    return str(candidate) if candidate.is_file() and os.access(str(candidate), os.X_OK) else ""


def probe_version(executable: str, version_args=("--version",)) -> str:
    """问出转换器版本（进 cache_key 的转换器指纹）；问不出来返回空串。

    只跑一次并缓存：`capability()` 会被预检链路频繁调用，不该每次都起进程。
    """
    path = str(executable or "")
    if not path:
        return ""
    if not version_args:
        # 该驱动不支持版本探测（ODA）：**绝不**为了探测再执行一次子进程。
        return ""
    with _version_lock:
        if path in _version_cache:
            return _version_cache[path]
    version = _read_version(path, version_args)
    with _version_lock:
        _version_cache[path] = version
    return version


def _read_version(path: str, version_args) -> str:
    argv = [path] + [str(item) for item in (version_args or ())]
    try:
        completed = subprocess.run(argv, capture_output=True, timeout=VERSION_TIMEOUT_SECONDS,
                                   check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    output = (completed.stdout or b"") + b"\n" + (completed.stderr or b"")
    match = _VERSION_PATTERN.search(output)
    return match.group(1).decode("ascii", "replace") if match else ""


def build(*, provider: str, binary: str, driver: str = "", expected_version: str = "",
          preview_binary: str = "", wrapper=None) -> "LocalCliAdapter":
    """按已解析好的配置构造适配器（二进制是否可用由编排层判定）。"""
    resolved_driver = driver or driver_of(provider, binary) or PROVIDER_DRIVERS.get(
        str(provider or "").lower(), "libredwg_dwg2dxf")
    return LocalCliAdapter(
        executable=binary, driver=resolved_driver, provider=provider,
        expected_version=expected_version, preview_binary=preview_binary,
        wrapper=list(wrapper or ()),
        version=probe_version(binary, DRIVERS[resolved_driver]["version_args"]),
        adapter_name=str(provider or resolved_driver),
    )


class LocalCliAdapter:
    """把一个本地 CLI 转换器包成协议适配器。未安装时不会走到这里。"""

    adapter_name = "local_cli"
    name = "local_cli"

    def __init__(self, *, executable: str, version: str = "",
                 driver: str = "libredwg_dwg2dxf", provider: str = "",
                 expected_version: str = "", preview_binary: str = "",
                 wrapper=None, adapter_name: str = "local_cli") -> None:
        self._executable = str(executable)
        self._version = str(version or "")
        self._driver = driver if driver in DRIVERS else "libredwg_dwg2dxf"
        self.provider = str(provider or DRIVERS[self._driver]["provider"])
        self.expected_version = str(expected_version or "")
        self.preview_binary = str(preview_binary or "")
        self.wrapper = [str(item) for item in (wrapper or ())]
        self.adapter_name = str(adapter_name or self.provider or "local_cli")

    @property
    def driver(self) -> dict:
        return DRIVERS[self._driver]

    @property
    def argv_verified(self) -> bool:
        return bool(self.driver.get("argv_verified"))

    @property
    def preview_available(self) -> bool:
        return bool(self.preview_binary)

    def capability(self) -> dict:
        return {
            "name": self.adapter_name,
            "version": self._version,
            "dwg_conversion": True,
            "preview_render": self.preview_available,
            "three_d_conversion": False,
            "simulated": False,
            "driver": self._driver,
            "provider": self.provider,
            "binary": self._executable,
            "expected_version": self.expected_version,
            "argv_verified": self.argv_verified,
            "preview_available": self.preview_available,
            "wrapper": list(self.wrapper),
            "output_version": output_version_of(self._driver),
            "audit_enabled": audit_enabled_of(self._driver),
        }

    def inspect(self, request) -> dict:
        # 真实转换器不做「先探测后转换」的两段式：DWG 版本由预检给出（第 1 批），
        # 这里不为了探测再跑一次外部进程。
        return {
            "detected_dwg_version": str(getattr(request, "detected_dwg_version", "")),
            "has_2d_entities": True,
            "three_d": "unknown",
            "warnings": [],
        }

    def convert_to_dxf(self, request) -> dict:
        output_dir = Path(getattr(request, "output_dir"))
        source = Path(getattr(request, "source_path"))
        target = output_dir / self.driver["output_name"]
        timeout = self._timeout(request)
        argv = list(self.wrapper) + self.driver["argv"](self._executable, source, output_dir,
                                                        target)
        try:
            completed = subprocess.run(argv, timeout=timeout, capture_output=True, check=False)
        except subprocess.TimeoutExpired as exc:
            return _failure("DWG_CONVERSION_TIMEOUT", stderr=exc.stderr, stdout=exc.stdout)
        except (OSError, subprocess.SubprocessError):
            return _failure("DWG_CONVERSION_FAILED")

        diagnostics = {"stdout": completed.stdout or b"", "stderr": completed.stderr or b""}
        if completed.returncode != 0:
            return _failure("DWG_CONVERSION_FAILED", **diagnostics)
        # 退出码为 0 但没写文件：交给编排层按 DWG_CONVERTER_OUTPUT_MISSING 分类，
        # 不在适配器里把它伪装成成功（也不自己编一个空文件）。
        return {
            "status": "ok",
            "dxf_path": target if target.is_file() else None,
            "preview_paths": [],
            "warnings": [],
            "error_code": None,
            "stderr_digest": _digest(diagnostics["stderr"]),
            "three_d": {"status": "unknown"},
            "diagnostics": diagnostics,
        }

    def render_preview(self, request, dxf_path=None) -> dict:
        """`dwg2SVG --mspace <source.dwg>` → stdout 落成 `converted.svg`（Spec §3）。"""
        if not self.preview_available:
            return {"status": "failed", "dxf_path": None, "preview_paths": [],
                    "warnings": ["该转换器不支持预览渲染"], "error_code": None,
                    "stderr_digest": None, "three_d": {}}
        output_dir = Path(getattr(request, "output_dir"))
        source = Path(getattr(request, "source_path"))
        target = output_dir / PREVIEW_OUTPUT_NAME
        argv = [self.preview_binary, "--mspace", str(source)]
        try:
            completed = subprocess.run(argv, timeout=self._timeout(request),
                                       capture_output=True, check=False)
        except (OSError, subprocess.SubprocessError):
            return {"status": "failed", "dxf_path": None, "preview_paths": [],
                    "warnings": ["预览渲染失败（转换器不可执行或超时）"], "error_code": None,
                    "stderr_digest": None, "three_d": {},
                    "diagnostics": _diagnostics(None, None)}
        svg = completed.stdout or b""
        if completed.returncode != 0 or not svg or b"<svg" not in svg[:4096]:
            return {"status": "failed", "dxf_path": None, "preview_paths": [],
                    "warnings": ["预览渲染没有产出可用的 SVG"], "error_code": None,
                    "stderr_digest": _digest(completed.stderr), "three_d": {},
                    "diagnostics": _diagnostics(completed.stdout, completed.stderr)}
        target.write_bytes(svg)
        return {"status": "ok", "dxf_path": None, "preview_paths": [target],
                "warnings": [], "error_code": None,
                "stderr_digest": _digest(completed.stderr), "three_d": {},
                "diagnostics": _diagnostics(completed.stdout, completed.stderr)}

    def convert_3d_if_supported(self, request) -> dict:
        return {"status": "unknown", "artifact_path": None, "error_code": None}

    def work_dir_for(self, *, default, artifact_dir, role=""):
        """本次跳次的工作区（Spec §3）。

        只要「文件路径」的驱动用编排层的临时目录（随转换一起回收）。需要**输入目录 +
        输出目录**的驱动（ODA）拿不到临时目录：临时目录在转换结束后就被删掉，连
        `argv` 里那两个目录都无法再复核、也无法重放。这类驱动改用产物目录下的
        `work/<role>/`——工作区本身保留，内容由编排层用完即清。
        """
        if not self.driver.get("needs_dirs"):
            return default
        target = Path(str(artifact_dir)) / "work" / (str(role or "") or "primary")
        shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)
        return target

    # ------------------------------------------------------------------ 内部
    def _timeout(self, request) -> float:
        value = float(getattr(request, "timeout_seconds", 0) or 0) or HARD_TIMEOUT_SECONDS
        return min(value, HARD_TIMEOUT_SECONDS)


def _failure(code: str, stdout=None, stderr=None) -> dict:
    return {"status": "failed", "dxf_path": None, "preview_paths": [],
            "warnings": [], "error_code": code,
            "stderr_digest": _digest(stderr), "three_d": {"status": "unknown"},
            "diagnostics": _diagnostics(stdout, stderr)}


def _diagnostics(stdout, stderr) -> dict:
    """把两个流的**原始字节**交给编排层当场计数；落盘只允许摘要（Spec §5）。"""
    return {"stdout": bytes(stdout or b""), "stderr": bytes(stderr or b"")}


def _digest(data) -> Optional[str]:
    """stderr 只留摘要：原文绝不进 manifest，也不返回给调用方（Spec §5）。"""
    if not data:
        return None
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    return hashlib.sha256(data).hexdigest()

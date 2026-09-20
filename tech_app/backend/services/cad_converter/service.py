"""DWG 受控转换的编排层（Spec §2.5 / §3 / §4 / §5）。

一次转换的可审计形状：

    预检 → 选适配器 → 幂等（cache_key）→ 独立临时目录 → 调用适配器
         → 校验产物（越界/空/超限/魔数）→ 复制进正式产物目录 → manifest → 审计

三条硬约束在这里落地：
  · 超时不指望适配器自己退出，编排层用线程 join 兜底（`DWG_CONVERSION_TIMEOUT`）；
  · 临时目录无论成功失败都必须清理，正式产物与上一次成功版本一律不动；
  · 失败只抛结构化错误（`FileCapabilityError`），不透传转换器 stderr 原文与堆栈。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...time_utils import now_cst_str
from .. import dxf_inspect, file_preflight
from . import persistence
from .adapters.base import SOURCE_FILENAME, ConversionRequest, result_field
from .adapters.fake import FakeAdapter
from .errors import FileCapabilityError

#: 假转换器的名字（配置里显式写它才会启用，且生产禁止）。
SIMULATED_ADAPTER_NAME = "fake"
#: 默认按真实适配器探测；`none` 用来锁死「未安装」态（本地/CI）。
DEFAULT_CONVERTER = "auto"
DISABLED_CONVERTER = "none"

#: 转换器配置名（修复批 Spec §2）：新名优先，旧名 `CAD_CONVERTER` 继续可用。
PROVIDER_ENV = "DWG_CONVERTER_PROVIDER"
BINARY_ENV = "DWG_CONVERTER_BINARY"
VERSION_ENV = "DWG_CONVERTER_VERSION"
PREVIEW_ENV = "DWG_CONVERTER_PREVIEW_BINARY"
LEGACY_PROVIDER_ENV = "CAD_CONVERTER"
#: 新旧配置同时给出且冲突时的告警标记（Spec §2）
SHADOW_WARNING = "converter_config_shadowed"

#: 状态闭集（Spec §5）：退出码 0 但有诊断必须显式降级，不许报 ok。
STATUS_OK = "ok"
STATUS_WITH_WARNINGS = "success_with_warnings"
STATUS_FAILED = "failed"
SUCCESS_STATUSES = (STATUS_OK, STATUS_WITH_WARNINGS)

#: 告警模板最多保留条数（Spec §5）
MAX_WARNING_CODES = 20

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_OUTPUT_FILES = 20

#: conversion_id 长度（Spec §3.2：12–32 位十六进制）
CONVERSION_ID_LENGTH = 20

_TEMP_PREFIX = "dwg-conv-"
_DXF_BINARY_MAGIC = b"AutoCAD Binary DXF"


# --------------------------------------------------------------------------- #
# 环境（调用时读取，不在 import 时固化；便于测试与热更新）
# --------------------------------------------------------------------------- #
def _env_text(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    text = str(raw).strip()
    return text if text else default


def _env_name(env: Optional[str] = None) -> str:
    value = _env_text("APP_ENV", "local") if env is None else env
    return str(value or "local").strip().lower() or "local"


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env_text(name, str(default)))
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(_env_text(name, str(default))))
    except (TypeError, ValueError):
        return int(default)


def _simulated_allowed(env: Optional[str] = None, allow_simulated: Optional[bool] = None) -> bool:
    """生产环境永远不允许；其余环境要显式 `CAD_CONVERTER_ALLOW_SIMULATED=true`。"""
    if allow_simulated is not None:
        return bool(allow_simulated)
    if _env_name(env) == "production":
        return False
    return _env_text("CAD_CONVERTER_ALLOW_SIMULATED").lower() in ("1", "true", "yes", "on")


def _converter_config() -> dict:
    """读转换器配置（每次调用现读，不在 import 时固化；Spec §2）。

    新名 `DWG_CONVERTER_*` 优先；旧名 `CAD_CONVERTER` 继续可用；两者都给出且冲突时以新名为准，
    并留一条 `converter_config_shadowed` 告警——口径要被看见，不许悄悄换。
    """
    explicit = _env_text(PROVIDER_ENV)
    legacy = _env_text(LEGACY_PROVIDER_ENV)
    warnings: List[str] = []
    if explicit:
        provider = explicit.lower()
        if legacy and legacy.lower() != provider:
            warnings.append("%s：同时配置了 %s=%s 与 %s=%s，以 %s 为准"
                            % (SHADOW_WARNING, PROVIDER_ENV, provider,
                               LEGACY_PROVIDER_ENV, legacy, PROVIDER_ENV))
    else:
        provider = (legacy or DEFAULT_CONVERTER).lower()
    return {
        "provider": provider or DEFAULT_CONVERTER,
        "binary": _env_text(BINARY_ENV),
        "expected_version": _env_text(VERSION_ENV),
        "preview_binary": _env_text(PREVIEW_ENV),
        "warnings": warnings,
    }


def _resolve(name=None, *, env: Optional[str] = None, allow_simulated: Optional[bool] = None) -> dict:
    """把配置解析成「本环境能不能转、用谁转」（Spec §2）。

    唯一会抛异常的情形是 fake 被禁止（`FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION`）；二进制
    不可用**不抛**，而是如实给出 `DWG_CONVERTER_BINARY_UNUSABLE` 与配置现场（provider/binary/
    期望版本/实际版本），由调用方决定是回给用户还是拒绝转换。
    """
    from .adapters import local_cli

    config = _converter_config()
    requested = str(name).strip().lower() if name is not None else config["provider"]
    requested = requested or DEFAULT_CONVERTER
    result = {
        "provider": requested,
        "binary": "",
        "expected_version": config["expected_version"],
        "actual_version": "",
        "version_ok": True,
        "argv_verified": False,
        "preview_binary": "",
        "preview_available": False,
        "simulated": False,
        "adapter": None,
        "error_code": "",
        "failure_reason": "",
        "warnings": list(config["warnings"]),
    }
    if requested == DISABLED_CONVERTER:
        result["error_code"] = "DWG_CONVERTER_NOT_INSTALLED"
        return result
    if requested == SIMULATED_ADAPTER_NAME:
        if not _simulated_allowed(env, allow_simulated):
            raise FileCapabilityError("FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION")
        result.update({"adapter": FakeAdapter(), "simulated": True, "preview_available": True})
        return result
    if requested not in local_cli.KNOWN_PROVIDERS:
        # 具名未知 provider：按未安装处理。绝不静默换适配器，也绝不把配置名当可执行路径。
        result["error_code"] = "DWG_CONVERTER_NOT_INSTALLED"
        return result

    binary = local_cli.resolve_binary(requested, config["binary"])
    if binary:
        inferred = local_cli.provider_of_binary(binary)
        if inferred:
            result["provider"] = inferred
        elif config["binary"]:
            result["warnings"].append(
                "unknown_converter_binary：无法从 %s 的文件名识别转换器类型，argv 形状未经真机验证"
                % Path(binary).name)
    result["binary"] = binary
    if not binary:
        result["error_code"] = "DWG_CONVERTER_NOT_INSTALLED"
        return result

    actual = local_cli.probe_version(binary) if os.path.isfile(binary) else ""
    result["actual_version"] = actual
    result["version_ok"] = (not config["expected_version"]) or (actual == config["expected_version"])
    problem = local_cli.binary_problem(binary)
    if problem or not result["version_ok"]:
        result["error_code"] = "DWG_CONVERTER_BINARY_UNUSABLE"
        result["failure_reason"] = problem or "version_mismatch"
        return result

    adapter = local_cli.build(provider=result["provider"], binary=binary,
                              driver=local_cli.driver_of(result["provider"], binary),
                              expected_version=config["expected_version"],
                              preview_binary=local_cli.preview_binary_for(
                                  binary, config["preview_binary"]))
    result.update({"adapter": adapter,
                   "argv_verified": adapter.argv_verified,
                   "preview_binary": adapter.preview_binary,
                   "preview_available": adapter.preview_available})
    return result


def _detection_extras(resolution: dict) -> dict:
    """未安装/不可用时也要把配置现场带进 `detected`（Spec §6），便于前端与审计定位。"""
    extras = {}
    for key in ("provider", "binary", "expected_version", "actual_version"):
        value = resolution.get(key)
        if value not in (None, ""):
            extras[key] = value
    if resolution.get("failure_reason"):
        extras["failure_reason"] = str(resolution["failure_reason"])
    return extras


def get_adapter(name=None, *, env: Optional[str] = None, allow_simulated: Optional[bool] = None):
    """按配置选适配器；未安装 / 二进制不可用返回 None（细节看 `capability()`）。

    · `auto`：按 Spec §3 的顺序探测真实适配器，全没有就是「未安装」——**绝不**回退到 fake；
    · `fake`：仅当非生产且显式允许，否则直接拒；
    · 具名：未注册/未安装一律视为未安装，不静默换成别的适配器。
    """
    return _resolve(name, env=env, allow_simulated=allow_simulated)["adapter"]


def list_adapters() -> List[str]:
    """当前**可用**的适配器名（未安装的不算）。"""
    from .adapters import local_cli
    names = list(local_cli.installed_names())
    try:
        if get_adapter(SIMULATED_ADAPTER_NAME) is not None:
            names.append(SIMULATED_ADAPTER_NAME)
    except FileCapabilityError:
        pass
    return names


def capability(*, env: Optional[str] = None) -> dict:
    """本环境的 DWG 转换能力（Spec §2.1）。

    口径要点：`dwg_supported` 本批**恒为假**——真实转换能力是否可用只影响
    `support_claim`，不许把「装了转换器」说成「支持 DWG」。
    """
    env_name = _env_name(env)
    error_code = ""
    resolution = {
        "provider": "", "binary": "", "expected_version": "", "actual_version": "",
        "version_ok": False, "argv_verified": False, "preview_available": False,
        "simulated": False, "adapter": None, "error_code": "", "warnings": [],
    }
    try:
        resolution = _resolve(env=env)
    except FileCapabilityError as exc:
        error_code = exc.stable_error_code

    adapter = resolution.get("adapter")
    declaration = adapter.capability() if adapter is not None else {}
    available = adapter is not None
    simulated = bool(declaration.get("simulated")) if available else False
    if not available and not error_code:
        error_code = str(resolution.get("error_code") or "DWG_CONVERTER_NOT_INSTALLED")
    preview_available = bool(resolution.get("preview_available")
                             or declaration.get("preview_render"))
    provider = str(declaration.get("provider") or resolution.get("provider") or "")
    version = str(declaration.get("version") or resolution.get("actual_version") or "")

    if available:
        message = "已安装 DWG 转换服务（%s %s）" % (provider, version)
        if simulated:
            message += "；当前是模拟转换器，只用于验证转换编排，不代表真实转换能力"
        elif not resolution.get("argv_verified"):
            message += "；该驱动的命令行形状未经真机验证"
    else:
        message = str(file_preflight.STABLE_ERROR_CODES.get(error_code, {}).get("message")
                      or "当前环境尚未安装 CAD 转换服务，暂时无法解析 DWG")

    return {
        "available": available,
        "simulated": simulated,
        "adapter_name": str(declaration.get("name") or ""),
        "converter_version": version,
        "expected_version": str(declaration.get("expected_version")
                                or resolution.get("expected_version") or ""),
        "version_ok": bool(resolution.get("version_ok")),
        "argv_verified": bool(resolution.get("argv_verified")),
        "preview_render": preview_available,
        "preview_available": preview_available,
        "binary": str(resolution.get("binary") or ""),
        "provider": provider,
        "warnings": list(resolution.get("warnings") or []),
        "dwg_conversion": bool(declaration.get("dwg_conversion")),
        "three_d_conversion": bool(declaration.get("three_d_conversion")),
        "env": env_name,
        "support_claim": ("conversion_available" if (available and not simulated)
                          else "orchestration_only"),
        "dwg_supported": False,
        "stable_error_code": "" if available else error_code,
        "message": message,
    }


# --------------------------------------------------------------------------- #
# 转换
# --------------------------------------------------------------------------- #
@dataclass
class _Job:
    project_id: str
    content: bytes
    detected: dict
    adapter: Any
    declaration: dict
    safe_name: str
    safe_attachment: str
    drawing_version: int
    timeout: float
    max_bytes: int
    max_files: int
    options: Dict[str, Any] = field(default_factory=dict)
    cache_key: str = ""
    conversion_id: str = ""
    provider: str = ""
    seed_warnings: List[str] = field(default_factory=list)


class _ConversionFailure(Exception):
    """内部：把稳定错误 + 已收集到的脱敏信息一起带出去写 failed manifest。"""

    def __init__(self, error: FileCapabilityError, stderr_digest=None, warnings=None,
                 counts=None, quality=None) -> None:
        self.error = error
        self.stderr_digest = stderr_digest
        self.warnings = list(warnings or [])
        # 失败也要能回答「诊断有多少、产物质量卡在哪一步」（Spec §5）
        self.counts = dict(counts or _empty_counts())
        self.quality = dict(quality or _empty_quality())
        super().__init__(error.message)


_locks_guard = threading.Lock()
_key_locks: Dict[str, threading.Lock] = {}


def _cache_lock(project_id: str, cache_key: str) -> threading.Lock:
    key = "%s:%s" % (project_id, cache_key)
    with _locks_guard:
        lock = _key_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _key_locks[key] = lock
        return lock


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_basename(filename: str) -> str:
    """用户文件名清洗成单一安全 basename（去掉路径分隔、上跳段与 NUL）。"""
    text = str(filename or "").replace("\x00", "")
    text = text.replace("\\", "/").split("/")[-1]
    text = text.strip().strip(".")
    return text or SOURCE_FILENAME


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _assert_convertible(detected: dict) -> None:
    """只有真的 DWG 才进转换器；其余格式在这里就停下（Spec §5）。"""
    if detected.get("is_empty"):
        raise FileCapabilityError("FILE_EMPTY", detected=detected)
    if detected.get("extension_content_mismatch"):
        raise FileCapabilityError("FILE_EXTENSION_CONTENT_MISMATCH", detected=detected)
    if str(detected.get("detected_format") or "") != "dwg":
        raise FileCapabilityError("FILE_FORMAT_UNSUPPORTED", detected=detected)
    if detected.get("is_truncated"):
        raise FileCapabilityError("FILE_CORRUPTED", detected=detected)


#: 诊断分类口径（Spec §5）：只看行内出现的严重性词，大小写与 `Warning:`/`ERROR:` 前缀都覆盖。
_WARNING_WORD = re.compile(r"warning", re.IGNORECASE)
_ERROR_WORD = re.compile(r"error", re.IGNORECASE)
#: 模板归一化：十六进制 mask / 句柄与 4 位以上数字一律折成 `N`（Spec §5）。
_HEX_LITERAL = re.compile(r"0x[0-9A-Fa-f]+")
_LONG_NUMBER = re.compile(r"\d{4,}")
#: 模板只保留「严重性 + 前 3 个词」：**报文正文绝不入库**，模板只用来聚合诊断类别。
TEMPLATE_WORDS = 3

#: 质量门槛的检查项与顺序（Spec §4）：退出码 → 非空 → 结构完整 → 实体>0 → 图层>0。
QUALITY_CHECKS = ("exit_code", "non_empty", "structure", "entities", "layers")


def _empty_counts() -> dict:
    """诊断计数的安全默认值（一次转换没跑起来时也要有可序列化的空壳）。"""
    return {"warning_count": 0, "error_count": 0, "warning_codes": {},
            "warning_codes_truncated": False, "diagnostics_bytes": 0,
            "diagnostics_sha256": None}


def _empty_quality(checks=None) -> dict:
    """`quality` 块的安全默认值（Spec §4）；`checks` 只记**真的跑过**的那些。"""
    return {"verified": False, "parser": "", "parser_version": "", "dxf_version": "",
            "insunits": None, "entity_count": 0, "layer_count": 0, "text_count": 0,
            "dimension_count": 0, "block_ref_count": 0, "degraded": False,
            "problem": "", "checks": list(checks or [])}


def _template(line: str) -> str:
    """把一条诊断行折成模板（Spec §5）。

    数字 / 句柄 / mask 折成 `N`，并只保留前 `TEMPLATE_WORDS` 个词。转换器报文正文一律
    不进 manifest 与审计，模板只用于回答「哪一类诊断、多少条」。
    """
    text = _LONG_NUMBER.sub("N", _HEX_LITERAL.sub("0xN", str(line)))
    words = text.split()
    if not words:
        return ""
    if len(words) > TEMPLATE_WORDS:
        return " ".join(words[:TEMPLATE_WORDS]) + "\u2026"
    return " ".join(words)


def _diagnostics_counts(diagnostics) -> dict:
    """统计 warning/error 条数、归一化模板与摘要（Spec §5）；原文只当场用，不落盘。

    口径：行内出现 `warning` 记 1 条警告，出现 `error` 记 1 条错误（大小写不敏感），
    两者都出现的行两个计数都加；但只有**纯警告行**才进 `warning_codes`——模板值不得
    回带报文正文（否则等于把 stderr 原文写进 manifest）。
    """
    stdout = bytes((diagnostics or {}).get("stdout") or b"")
    stderr = bytes((diagnostics or {}).get("stderr") or b"")
    blob = stderr + b"\n" + stdout
    counts = _empty_counts()
    counts["diagnostics_bytes"] = len(blob)
    if blob:
        counts["diagnostics_sha256"] = hashlib.sha256(blob).hexdigest()
    templates: Counter = Counter()
    for raw in blob.decode("utf-8", "replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        is_warning = bool(_WARNING_WORD.search(line))
        is_error = bool(_ERROR_WORD.search(line))
        if is_warning:
            counts["warning_count"] += 1
        if is_error:
            counts["error_count"] += 1
        if is_warning and not is_error:
            signature = _template(line)
            if signature:
                templates[signature] += 1
    counts["warning_codes"] = dict(templates.most_common(MAX_WARNING_CODES))
    counts["warning_codes_truncated"] = len(templates) > MAX_WARNING_CODES
    return counts


def _collect_diagnostics(base: dict, result, streams=("stdout", "stderr")) -> dict:
    """把适配器回执里的诊断字节并进本次转换的诊断池（只用于当场计数）。

    预览渲染的 **stdout 是 SVG 图元本身**，不是诊断，所以那一步只收 stderr
    （见 `streams`），否则会把 2 MB 的图纸算成「诊断字节」。
    """
    collected = {"stdout": bytes(base.get("stdout") or b""),
                 "stderr": bytes(base.get("stderr") or b"")}
    extra = result_field(result, "diagnostics", None)
    if isinstance(extra, dict):
        for stream in streams:
            value = extra.get(stream)
            if value:
                collected[stream] += bytes(value)
    return collected


def _assert_output_quality(job: "_Job", staged, quality: dict) -> dict:
    """产物质量门槛（Spec §4）：**退出码 0 不等于转换成功**。

    就地填充 `quality`（失败时也保留已经跑过的 check），任一条不过就抛
    `DWG_CONVERTER_OUTPUT_INVALID`。
    """
    quality.update(_empty_quality([QUALITY_CHECKS[0]]))
    dxf_entries = [entry for entry in staged if entry[0] == "dxf"]
    if not dxf_entries:
        raise FileCapabilityError("DWG_CONVERTER_OUTPUT_MISSING", detected=job.detected)
    path = Path(dxf_entries[0][1])
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if size <= 0:
        raise FileCapabilityError("DWG_CONVERTER_OUTPUT_INVALID", detected=job.detected)
    quality["checks"].append(QUALITY_CHECKS[1])

    report = dxf_inspect.inspect_dxf(path)
    for key in ("verified", "parser", "parser_version", "dxf_version", "insunits",
                "entity_count", "layer_count", "text_count", "dimension_count",
                "block_ref_count", "problem"):
        if key in report:
            quality[key] = report[key]
    if not report.get("structure_ok"):
        raise FileCapabilityError("DWG_CONVERTER_OUTPUT_INVALID", detected=job.detected)
    quality["checks"].append(QUALITY_CHECKS[2])

    if int(report.get("entity_count") or 0) <= 0:
        raise FileCapabilityError("DWG_CONVERTER_OUTPUT_INVALID", detected=job.detected)
    quality["checks"].append(QUALITY_CHECKS[3])

    if int(report.get("layer_count") or 0) <= 0:
        raise FileCapabilityError("DWG_CONVERTER_OUTPUT_INVALID", detected=job.detected)
    quality["checks"].append(QUALITY_CHECKS[4])
    return quality


def _limits():
    return (_env_float("CAD_CONVERTER_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
            _env_int("CAD_CONVERTER_MAX_OUTPUT_BYTES", DEFAULT_MAX_OUTPUT_BYTES),
            _env_int("CAD_CONVERTER_MAX_OUTPUT_FILES", DEFAULT_MAX_OUTPUT_FILES))


def convert_drawing(project_id: str, filename: str, content, *, adapter=None,
                    attachment_name: str = "", drawing_version: int = 1,
                    timeout_seconds=None) -> dict:
    """把一份 DWG 转成中间格式，返回 manifest（Spec §2.1 / §3.2）。"""
    detected = file_preflight.detect_file_format(filename, content)
    _assert_convertible(detected)

    resolution: Dict[str, Any] = {}
    if adapter is not None:
        resolved = adapter
    else:
        resolution = _resolve()
        resolved = resolution.get("adapter")
        if resolved is None:
            blocked = dict(detected)
            blocked.update(_detection_extras(resolution))
            raise FileCapabilityError(str(resolution.get("error_code")
                                          or "DWG_CONVERTER_NOT_INSTALLED"), detected=blocked)
    declaration = resolved.capability() or {}

    safe_name = _safe_basename(filename)
    safe_attachment = (_safe_basename(attachment_name)
                       if str(attachment_name or "").strip() else safe_name)
    env_timeout, env_bytes, env_files = _limits()
    timeout = float(timeout_seconds) if timeout_seconds else env_timeout
    version = int(drawing_version or 1)

    options = {
        "timeout_seconds": timeout,
        "max_output_bytes": env_bytes,
        "max_output_files": env_files,
        "drawing_version": version,
        "original_filename": safe_name,
        "attachment_name": safe_attachment,
        "source_format": "dwg",
        "adapter_simulated": bool(declaration.get("simulated")),
        "converter_provider": str(declaration.get("provider") or resolution.get("provider") or ""),
        "converter_binary": Path(str(declaration.get("binary") or "")).name,
        "expected_version": str(declaration.get("expected_version")
                                or resolution.get("expected_version") or ""),
    }
    # 转换身份（决定 conversion_id 与产物目录）只认「同一份图纸 + 同一转换器 + 同一图纸
    # 版本」：**不含展示名**。同一张 DWG 换个附件名再传，不该在项目里堆出第二份产物目录；
    # 但附件名变了 manifest 必须跟着变，所以幂等键 cache_key 仍然把选项（含名字）算进去，
    # 名字变了就重跑一次并就地更新这一版的 manifest。
    identity_digest = _digest("|".join([
        str(detected.get("sha256") or ""),
        str(declaration.get("name") or ""),
        str(declaration.get("version") or ""),
        str(version),
    ]))
    options_digest = _digest(json.dumps(options, sort_keys=True, ensure_ascii=False))
    cache_key = _digest("|".join([identity_digest, options_digest]))
    job = _Job(project_id=str(project_id), content=bytes(content), detected=detected,
               adapter=resolved, declaration=declaration, safe_name=safe_name,
               safe_attachment=safe_attachment, drawing_version=version,
               timeout=timeout, max_bytes=env_bytes, max_files=env_files,
               options=options, cache_key=cache_key,
               conversion_id=identity_digest[:CONVERSION_ID_LENGTH],
               provider=str(declaration.get("provider") or resolution.get("provider") or ""),
               seed_warnings=list(resolution.get("warnings") or []))

    with _cache_lock(job.project_id, cache_key):
        cached = _cached_manifest(job.project_id, cache_key)
        if cached is not None:
            return cached
        return _run(job)


def _cached_manifest(project_id: str, cache_key: str) -> Optional[dict]:
    """幂等命中：同 cache_key 且**成功**的 manifest 直接复用（不调用适配器）。"""
    for item in persistence.list_manifests(project_id):
        if str(item.get("cache_key")) == cache_key and str(item.get("status")) in SUCCESS_STATUSES:
            return item
    return None


def _run(job: _Job) -> dict:
    started = now_cst_str()
    temp_dir = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX))
    try:
        try:
            outcome = _convert_in_temp_dir(job, temp_dir)
        except _ConversionFailure as exc:
            _save_failure(job, started, exc.error, exc.stderr_digest, exc.warnings,
                          counts=exc.counts, quality=exc.quality)
            raise exc.error
        except FileCapabilityError as exc:
            _save_failure(job, started, exc, None, job.seed_warnings)
            raise
        except Exception:
            failure = FileCapabilityError("DWG_CONVERSION_FAILED", detected=job.detected)
            _save_failure(job, started, failure, None, job.seed_warnings)
            raise failure from None

        manifest = _manifest(job, started=started, status=outcome["status"],
                             outputs=outcome["outputs"], output_sha256=outcome["output_sha256"],
                             warnings=outcome["warnings"], stderr_digest=outcome["stderr_digest"],
                             counts=outcome["counts"], quality=outcome["quality"])
        persistence.save_manifest(job.project_id, manifest)
        persistence.sync(job.project_id, job.conversion_id)
        _audit(job, "dwg.convert", manifest)
        return manifest
    finally:
        # 成功与失败都必须清理临时目录（Spec §5）。
        shutil.rmtree(temp_dir, ignore_errors=True)


def _convert_in_temp_dir(job: _Job, temp_dir: Path) -> dict:
    """在临时目录里跑一次转换：调用 → 收集诊断 → 质量门槛 → 落盘产物（Spec §4/§5）。

    返回 `outcome`：
        status / outputs / output_sha256 / warnings / stderr_digest / counts / quality
    任一步不达标都抛 `_ConversionFailure`，并把**已经跑过的**诊断与质量现场一起带走
    （失败也要能回答「卡在哪一条门槛」）。
    """
    warnings: List[str] = [str(item) for item in job.seed_warnings]
    stderr_digest = None
    diagnostics: Dict[str, bytes] = {"stdout": b"", "stderr": b""}
    quality: Dict[str, Any] = _empty_quality()
    try:
        source = temp_dir / SOURCE_FILENAME
        source.write_bytes(job.content)
        request = ConversionRequest(
            project_id=job.project_id, attachment_name=job.safe_attachment,
            drawing_version=job.drawing_version, source_path=source,
            source_sha256=str(job.detected.get("sha256") or ""), source_format="dwg",
            detected_dwg_version=str(job.detected.get("dwg_version") or ""),
            output_dir=temp_dir, timeout_seconds=job.timeout,
            options=dict(job.options), conversion_id=job.conversion_id,
        )
        inspected = _call_with_timeout(lambda: job.adapter.inspect(request), job.timeout,
                                       job.detected)
        warnings.extend(str(w) for w in (result_field(inspected, "warnings", []) or []))

        converted = _call_with_timeout(lambda: job.adapter.convert_to_dxf(request),
                                       job.timeout, job.detected)
        stderr_digest = result_field(converted, "stderr_digest", None)
        warnings.extend(str(w) for w in (result_field(converted, "warnings", []) or []))
        diagnostics = _collect_diagnostics(diagnostics, converted)
        if str(result_field(converted, "status", "ok") or "ok").lower() != "ok":
            code = str(result_field(converted, "error_code", None) or "DWG_CONVERSION_FAILED")
            raise FileCapabilityError(code, detected=job.detected)

        dxf_path = result_field(converted, "dxf_path", None)
        previews = list(result_field(converted, "preview_paths", []) or [])
        if job.declaration.get("preview_render"):
            rendered = _call_with_timeout(
                lambda: job.adapter.render_preview(request, dxf_path), job.timeout, job.detected)
            warnings.extend(str(w) for w in (result_field(rendered, "warnings", []) or []))
            # 预览工具把 SVG 写到 stdout：那是产物不是诊断，只收 stderr。
            diagnostics = _collect_diagnostics(diagnostics, rendered, streams=("stderr",))
            if str(result_field(rendered, "status", "ok") or "ok").lower() == "ok":
                previews.extend(result_field(rendered, "preview_paths", []) or [])

        collected = {"status": "ok", "dxf_path": dxf_path, "preview_paths": previews,
                     "warnings": [], "error_code": None, "stderr_digest": stderr_digest}
        staged = _staged_outputs(job, collected, temp_dir)
        if not any(role == "preview" for role, _path in staged):
            if job.declaration.get("preview_render"):
                raise FileCapabilityError("DWG_CONVERTER_OUTPUT_MISSING", detected=job.detected)
            warnings.append("该转换器不支持预览渲染：本次只产出 DXF")

        counts = _diagnostics_counts(diagnostics)
        _assert_output_quality(job, staged, quality)
        status = STATUS_OK
        if counts["warning_count"] or counts["error_count"]:
            # 有损转换必须如实说：下游要按这个提示去核对，不许报「干净」。
            warnings.append("转换器报告了 %d 条警告、%d 条错误：图纸已生成但有损，请下游核对"
                            % (counts["warning_count"], counts["error_count"]))
            status = STATUS_WITH_WARNINGS
        quality["degraded"] = bool(counts["warning_count"] or counts["error_count"])

        outputs, output_sha256 = _save_outputs(job, staged)
        return {"status": status, "outputs": outputs, "output_sha256": output_sha256,
                "warnings": warnings, "stderr_digest": stderr_digest,
                "counts": counts, "quality": quality}
    except _ConversionFailure:
        raise
    except FileCapabilityError as exc:
        raise _ConversionFailure(exc, stderr_digest=stderr_digest, warnings=warnings,
                                 counts=_diagnostics_counts(diagnostics),
                                 quality=quality)


def _call_with_timeout(action, timeout, detected):
    """适配器可能挂住：编排层自己实现超时，不指望转换器自己退出（Spec §2.5）。"""
    box: Dict[str, Any] = {}

    def runner():
        try:
            box["value"] = action()
        except BaseException as exc:  # noqa: BLE001 - 交由调用方分类
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    try:
        wait = float(timeout) if timeout and float(timeout) > 0 else None
    except (TypeError, ValueError):
        wait = None
    thread.join(wait)
    if thread.is_alive():
        raise FileCapabilityError("DWG_CONVERSION_TIMEOUT", detected=detected)
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _staged_outputs(job: _Job, converted, output_dir: Path):
    """校验适配器给的路径与大小；返回 [(role, 已验证的真实路径)]。"""
    dxf_path = result_field(converted, "dxf_path", None)
    if not dxf_path:
        raise FileCapabilityError("DWG_CONVERTER_OUTPUT_MISSING", detected=job.detected)
    previews = list(result_field(converted, "preview_paths", []) or [])
    entries = [("dxf", dxf_path)] + [("preview", item) for item in previews]
    if len(entries) > job.max_files:
        raise FileCapabilityError("DWG_CONVERTER_OUTPUT_TOO_LARGE", detected=job.detected)

    root = Path(output_dir).resolve()
    staged = []
    total = 0
    for role, raw_path in entries:
        resolved = Path(str(raw_path)).resolve()
        # 越界路径绝不能当产物：必须 realpath 后仍在 output_dir 之内。
        if not _within(resolved, root):
            raise FileCapabilityError("DWG_CONVERTER_UNSAFE_PATH", detected=job.detected)
        if not resolved.is_file():
            raise FileCapabilityError("DWG_CONVERTER_OUTPUT_MISSING", detected=job.detected)
        size = resolved.stat().st_size
        if size <= 0:
            raise FileCapabilityError("DWG_CONVERTER_OUTPUT_INVALID", detected=job.detected)
        total += size
        if size > job.max_bytes or total > job.max_bytes:
            raise FileCapabilityError("DWG_CONVERTER_OUTPUT_TOO_LARGE", detected=job.detected)
        staged.append((role, resolved))

    for role, resolved in staged:
        if not _content_ok(role, resolved.read_bytes()):
            raise FileCapabilityError("DWG_CONVERTER_OUTPUT_INVALID", detected=job.detected)
    return staged


def _content_ok(role: str, data: bytes) -> bool:
    if role == "dxf":
        return (data.startswith(_DXF_BINARY_MAGIC) or b"SECTION" in data[:4096])
    if data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"%PDF-"):
        return True
    head = data[:4096].lstrip()
    return head.startswith(b"<?xml") or b"<svg" in head.lower()


def _save_outputs(job: _Job, staged):
    outputs = []
    output_sha256 = {}
    seen = set()
    for role, resolved in staged:
        name = resolved.name
        if name in seen:
            raise FileCapabilityError("DWG_CONVERTER_OUTPUT_INVALID", detected=job.detected)
        seen.add(name)
        saved = persistence.save_artifact(job.project_id, job.conversion_id, name,
                                          resolved.read_bytes())
        outputs.append({"role": role, "filename": saved["filename"],
                        "sha256": saved["sha256"], "bytes": saved["bytes"]})
        output_sha256[saved["filename"]] = saved["sha256"]
    return outputs, output_sha256


def _manifest(job: _Job, *, started: str, status: str, error_code: Optional[str] = None,
              outputs=None, output_sha256=None, warnings=None, stderr_digest=None,
              counts=None, quality=None) -> dict:
    simulated = bool(job.declaration.get("simulated"))
    counts = dict(counts or _empty_counts())
    return {
        "conversion_id": job.conversion_id,
        "project_id": job.project_id,
        "attachment_name": job.safe_attachment,
        "drawing_version": job.drawing_version,
        "original_filename": job.safe_name,
        "source_sha256": str(job.detected.get("sha256") or ""),
        "source_format": "dwg",
        "detected_dwg_version": str(job.detected.get("dwg_version") or ""),
        "converter_name": str(job.declaration.get("name") or ""),
        "converter_version": str(job.declaration.get("version") or ""),
        "conversion_options": dict(job.options),
        "output_files": list(outputs or []),
        "output_sha256": dict(output_sha256 or {}),
        "warnings": [str(item) for item in (warnings or [])],
        "started_at": started,
        "finished_at": now_cst_str(),
        "status": status,
        "error_code": error_code,
        "is_simulated": simulated,
        "acceptance_level": "orchestration_only" if simulated else "real",
        "cache_key": job.cache_key,
        "converter_stderr_digest": stderr_digest,
        # 诊断与质量（Spec §4/§5）：只留计数、模板与摘要，**没有** stderr 原文。
        "warning_count": int(counts["warning_count"]),
        "error_count": int(counts["error_count"]),
        "warning_codes": dict(counts["warning_codes"]),
        "warning_codes_truncated": bool(counts["warning_codes_truncated"]),
        "diagnostics_bytes": int(counts["diagnostics_bytes"]),
        "diagnostics_sha256": counts["diagnostics_sha256"],
        "quality": dict(quality or _empty_quality()),
        # 新产物天然不是「旧产物」；回看接口会按同源更新的成功转换做读时装饰（Spec §9）。
        "stale_reason": "",
    }


def _save_failure(job: _Job, started: str, error: FileCapabilityError,
                  stderr_digest=None, warnings=None, counts=None, quality=None) -> None:
    """失败也要留一条 status=failed 的 manifest；**不动**上一次成功产物（Spec §4.2）。"""
    manifest = _manifest(job, started=started, status=STATUS_FAILED,
                         error_code=error.stable_error_code, outputs=[], output_sha256={},
                         warnings=warnings, stderr_digest=stderr_digest,
                         counts=counts, quality=quality)
    persistence.save_manifest(job.project_id, manifest)
    _audit(job, "dwg.convert.failed", manifest)


def _audit(job: _Job, action: str, manifest: dict) -> None:
    """审计只记可追溯的安全字段：摘要、版本、状态、计数与质量——没有字节/路径/堆栈。"""
    quality = manifest.get("quality") or {}
    persistence.audit(job.project_id, action, {
        "conversion_id": manifest.get("conversion_id") or "",
        "source_sha256": manifest.get("source_sha256") or "",
        "detected_dwg_version": manifest.get("detected_dwg_version") or "",
        "converter_name": manifest.get("converter_name") or "",
        # 二进制只记 basename：审计里不许出现绝对路径（Spec §5）。
        "provider": str(job.provider or job.declaration.get("provider") or ""),
        "binary": Path(str(job.options.get("converter_binary") or "")).name,
        "converter_version": manifest.get("converter_version") or "",
        "status": manifest.get("status") or "",
        "stable_error_code": manifest.get("error_code") or "",
        "is_simulated": bool(manifest.get("is_simulated")),
        "warning_count": int(manifest.get("warning_count") or 0),
        "error_count": int(manifest.get("error_count") or 0),
        "quality.entity_count": int(quality.get("entity_count") or 0),
        "quality.layer_count": int(quality.get("layer_count") or 0),
    })


# --------------------------------------------------------------------------- #
# 回看（第 3 批的稳定读接口）
# --------------------------------------------------------------------------- #
#: 旧产物被取代的原因（Spec §9）：旧产物一律**保留**，只是被读时标成不再最新。
STALE_CONVERTER_VERSION_CHANGED = "converter_version_changed"
STALE_DRAWING_VERSION_CHANGED = "drawing_version_changed"
STALE_SUPERSEDED = "superseded_by_newer_conversion"


def _stale_reason(item: dict, newer_items: List[dict]) -> str:
    """这一份产物是不是已经被**同源**的新成功转换取代了（读时判定，不写回索引）。

    只有同一份图纸（`source_sha256` 相同）的、更晚写入的成功转换才能取代它：换了转换器
    版本是 `converter_version_changed`，只是图纸版本前进是 `drawing_version_changed`；
    失败的转换没有产物，不构成取代（Spec §9 / §4.2）。
    """
    if str(item.get("status")) != "ok":
        return ""
    source = str(item.get("source_sha256") or "")
    for newer in newer_items:
        if str(newer.get("status")) != "ok":
            continue
        if str(newer.get("conversion_id")) == str(item.get("conversion_id")):
            continue
        if source and str(newer.get("source_sha256") or "") != source:
            continue
        newer_identity = (str(newer.get("converter_name") or ""),
                          str(newer.get("converter_version") or ""))
        own_identity = (str(item.get("converter_name") or ""),
                        str(item.get("converter_version") or ""))
        if newer_identity != own_identity:
            return STALE_CONVERTER_VERSION_CHANGED
        if str(newer.get("drawing_version")) != str(item.get("drawing_version")):
            return STALE_DRAWING_VERSION_CHANGED
        return STALE_SUPERSEDED
    return ""


def _decorated(project_id: str) -> List[dict]:
    """新→旧的 manifest 列表，每条附一个 `stale_reason`（索引本身一字不改）。"""
    items = persistence.list_manifests(project_id)
    decorated = []
    for index, item in enumerate(items):
        current = dict(item)
        current["stale_reason"] = _stale_reason(item, items[:index])
        decorated.append(current)
    return decorated


def load_manifest(project_id: str, conversion_id: str) -> Optional[dict]:
    for item in _decorated(project_id):
        if str(item.get("conversion_id")) == str(conversion_id):
            return item
    return None


def list_conversions(project_id: str) -> List[dict]:
    """该项目全部转换（含失败），新→旧；旧产物带 `stale_reason`（Spec §9）。"""
    return _decorated(project_id)


def latest_manifest(project_id: str, *, status: str = "ok") -> Optional[dict]:
    for item in _decorated(project_id):
        if status is None or str(item.get("status")) == str(status):
            return item
    return None

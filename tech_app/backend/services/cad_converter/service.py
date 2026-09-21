"""DWG 受控转换的编排层（Spec §2.5 / §3 / §4 / §5 / §7）。

一次转换的可审计形状：

    预检 → 选转换器（主 + 受控回退）→ 幂等（cache_key）→ 独立临时目录 → 调用适配器
         → 校验产物（越界/空/超限/质量门槛）→ 复制进正式产物目录 → manifest → 审计

三条硬约束在这里落地：
  · 超时不指望适配器自己退出，编排层用线程 join 兜底（`DWG_CONVERSION_TIMEOUT`）；
  · 临时目录无论成功失败都必须清理，正式产物与上一次成功版本一律不动；
  · 失败只抛结构化错误（`FileCapabilityError`），不透传转换器 stderr 原文与堆栈。

受控回退链（修复批 Spec §7）：只有主转换器**明确失败**（超时 / 非 0 退出 / 产物缺失或
质量不过 / 二进制缺失、不可执行或版本不符）才切到**显式配置**的回退转换器；配置类错误
（wrapper 非法、解释器当二进制）与输入类问题**绝不回退**。每一次尝试都按序记进
`attempts[]`，成功/失败/计数/质量一律指**生效跳次**。
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
from ..dwg_acceptance import acceptance as _acceptance_state
from ..dwg_acceptance import support_claim as _acceptance_claim
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
WRAPPER_ENV = "DWG_CONVERTER_WRAPPER"
FALLBACK_PROVIDER_ENV = "DWG_CONVERTER_FALLBACK_PROVIDER"
FALLBACK_BINARY_ENV = "DWG_CONVERTER_FALLBACK_BINARY"
FALLBACK_VERSION_ENV = "DWG_CONVERTER_FALLBACK_VERSION"
FALLBACK_PREVIEW_ENV = "DWG_CONVERTER_FALLBACK_PREVIEW_BINARY"
FALLBACK_WRAPPER_ENV = "DWG_CONVERTER_FALLBACK_WRAPPER"
LEGACY_PROVIDER_ENV = "CAD_CONVERTER"
#: 新旧配置同时给出且冲突时的告警标记（Spec §2）
SHADOW_WARNING = "converter_config_shadowed"
#: 回退默认**关闭**：未显式配置 `DWG_CONVERTER_FALLBACK_PROVIDER` 就没有回退，
#: 绝不把「本机恰好装了另一个转换器」当成可用回退（Spec §2.7 禁令）。
DEFAULT_FALLBACK_PROVIDER = DISABLED_CONVERTER

#: 状态闭集（Spec §5）：退出码 0 但有诊断必须显式降级，不许报 ok。
STATUS_OK = "ok"
STATUS_WITH_WARNINGS = "success_with_warnings"
STATUS_FAILED = "failed"
SUCCESS_STATUSES = (STATUS_OK, STATUS_WITH_WARNINGS)

#: 角色闭集（修复批 Spec §7.2）
ROLE_PRIMARY = "primary"
ROLE_FALLBACK = "fallback"

#: 告警模板最多保留条数（Spec §5）
MAX_WARNING_CODES = 20

DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_OUTPUT_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_OUTPUT_FILES = 20

#: conversion_id 长度（Spec §3.2：12–32 位十六进制）
CONVERSION_ID_LENGTH = 20

_TEMP_PREFIX = "dwg-conv-"
_DXF_BINARY_MAGIC = b"AutoCAD Binary DXF"

#: 配置类失败：换一个转换器也救不了，**绝不回退**（Spec §7.1）。
_NEVER_FALLBACK_REASONS = ("wrapper_invalid", "binary_is_interpreter")
#: 二进制类失败：确实可以试试回退转换器（Spec §7.1）。
_FALLBACK_BINARY_REASONS = ("binary_missing", "binary_not_executable",
                            "binary_not_a_file", "version_mismatch")
#: 结果类失败：主转换器跑过了但没给出可用产物，值得回退（Spec §7.1）。
_FALLBACK_ERROR_CODES = ("DWG_CONVERSION_TIMEOUT", "DWG_CONVERSION_FAILED",
                         "DWG_CONVERTER_OUTPUT_MISSING", "DWG_CONVERTER_OUTPUT_INVALID",
                         "DWG_CONVERTER_OUTPUT_TOO_LARGE")


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
    fallback_provider = _env_text(FALLBACK_PROVIDER_ENV, DEFAULT_FALLBACK_PROVIDER).lower()
    return {
        "provider": provider or DEFAULT_CONVERTER,
        "binary": _env_text(BINARY_ENV),
        "expected_version": _env_text(VERSION_ENV),
        "preview_binary": _env_text(PREVIEW_ENV),
        "wrapper": _env_text(WRAPPER_ENV),
        "fallback_provider": fallback_provider,
        "fallback_binary": _env_text(FALLBACK_BINARY_ENV),
        "fallback_expected_version": _env_text(FALLBACK_VERSION_ENV),
        "fallback_preview_binary": _env_text(FALLBACK_PREVIEW_ENV),
        "fallback_wrapper": _env_text(FALLBACK_WRAPPER_ENV),
        "warnings": warnings,
    }


#: 二进制指纹缓存（key = 路径 + mtime + 大小），避免每次转换都重新哈希几十 MB 的转换器。
_binary_sha_lock = threading.Lock()
_binary_sha_cache: Dict[str, str] = {}


def _binary_sha256(path: str) -> str:
    """转换器二进制的 sha256（进 cache_key 的生效转换器指纹）；读不到返回空串。"""
    text = str(path or "")
    if not text:
        return ""
    try:
        stat = os.stat(text)
    except OSError:
        return ""
    stamp = "%s|%s|%s" % (text, stat.st_mtime_ns, stat.st_size)
    with _binary_sha_lock:
        cached = _binary_sha_cache.get(stamp)
    if cached:
        return cached
    try:
        digest = hashlib.sha256(Path(text).read_bytes()).hexdigest()
    except OSError:
        return ""
    with _binary_sha_lock:
        _binary_sha_cache[stamp] = digest
    return digest


def _empty_side(provider: str = "", expected_version: str = "") -> dict:
    """一侧（主或回退）转换器的解析结果空壳；键集是**闭集**，capability() 直接读它。"""
    return {
        "provider": str(provider or ""), "requested_provider": str(provider or ""),
        "binary": "", "expected_version": str(expected_version or ""),
        "actual_version": "", "converter_version": "", "version_ok": False,
        "version_source": "unverifiable", "argv_verified": False, "wrapper": [],
        "preview_binary": "", "preview_available": False, "simulated": False,
        "adapter": None, "declaration": {}, "available": False, "error_code": "",
        "binary_reason": "", "driver": "", "output_version": "", "audit_enabled": False,
        "binary_sha256": "", "note": "",
    }


def _resolve_one(*, provider, binary: str = "", expected_version: str = "",
                 preview_binary: str = "", wrapper: str = "",
                 env: Optional[str] = None, allow_simulated: Optional[bool] = None) -> dict:
    """解析**一侧**转换器：能不能用、用哪套 argv、版本口径、wrapper。

    wrapper 非法 / 二进制不可用都**不抛**（除 fake 被禁），而是如实写进 `error_code` +
    `binary_reason`，由 `capability()` 转述、`convert_drawing()` 决定抛什么（Spec §2.6）。
    """
    from .adapters import local_cli

    requested = str(provider or "").strip().lower() or DEFAULT_CONVERTER
    side = _empty_side(requested, expected_version)

    items, wrapper_reason = local_cli.parse_wrapper(wrapper)
    if wrapper_reason:
        side["error_code"] = "DWG_CONVERTER_BINARY_UNUSABLE"
        side["binary_reason"] = wrapper_reason
        return side
    side["wrapper"] = items

    if requested == DISABLED_CONVERTER:
        side["error_code"] = "DWG_CONVERTER_NOT_INSTALLED"
        return side
    if requested == SIMULATED_ADAPTER_NAME:
        if not _simulated_allowed(env, allow_simulated):
            raise FileCapabilityError("FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION")
        adapter = FakeAdapter()
        declaration = adapter.capability() or {}
        side.update({"adapter": adapter, "declaration": declaration, "simulated": True,
                     "available": True, "preview_available": bool(
                         declaration.get("preview_render")),
                     "version_ok": True,
                     "version_source": local_cli.VERSION_SOURCE_CONFIG,
                     "converter_version": str(declaration.get("version") or "")})
        return side
    if requested not in local_cli.KNOWN_PROVIDERS:
        # 具名未知 provider：按未安装处理。绝不静默换适配器，也绝不把配置名当可执行路径。
        side["error_code"] = "DWG_CONVERTER_NOT_INSTALLED"
        return side

    explicit = bool(str(binary or "").strip())
    resolved_binary = local_cli.resolve_binary(requested, binary)
    if resolved_binary:
        inferred = local_cli.provider_of_binary(resolved_binary)
        if not explicit and inferred:
            side["provider"] = inferred
        elif explicit and not inferred:
            side["note"] = ("unknown_converter_binary：无法从 %s 的文件名识别转换器类型，"
                            "argv 形状未经真机验证" % Path(resolved_binary).name)
    side["binary"] = resolved_binary
    side["binary_sha256"] = _binary_sha256(resolved_binary)
    if not resolved_binary:
        side["error_code"] = "DWG_CONVERTER_NOT_INSTALLED"
        return side

    driver = local_cli.driver_of(side["provider"], resolved_binary)
    side["driver"] = driver
    side["output_version"] = local_cli.output_version_of(driver)
    side["audit_enabled"] = local_cli.audit_enabled_of(driver)
    side["argv_verified"] = bool(local_cli.DRIVERS.get(driver, {}).get("argv_verified"))
    if side["note"]:
        side["argv_verified"] = False

    problem = local_cli.binary_problem(resolved_binary)
    if problem:
        side["error_code"] = "DWG_CONVERTER_BINARY_UNUSABLE"
        side["binary_reason"] = problem
        return side

    if local_cli.supports_version_probe(driver):
        actual = local_cli.probe_version(resolved_binary,
                                         local_cli.DRIVERS[driver]["version_args"])
        side["actual_version"] = actual
        side["converter_version"] = actual
        side["version_ok"] = (not side["expected_version"]
                              or actual == side["expected_version"])
        side["version_source"] = local_cli.version_source_for(
            driver, declared=side["expected_version"], probed=actual)
        if not side["version_ok"]:
            side["error_code"] = "DWG_CONVERTER_BINARY_UNUSABLE"
            side["binary_reason"] = "version_mismatch"
            return side
    else:
        # 不支持版本探测的驱动（ODA）：**零子进程**，只认配置声明（Spec §2.5）。
        side["actual_version"] = ""
        side["converter_version"] = side["expected_version"]
        side["version_ok"] = bool(side["expected_version"])
        side["version_source"] = (local_cli.VERSION_SOURCE_CONFIG if side["expected_version"]
                                  else local_cli.VERSION_SOURCE_UNVERIFIABLE)

    adapter = local_cli.build(provider=side["provider"], binary=resolved_binary, driver=driver,
                              expected_version=side["expected_version"],
                              preview_binary=local_cli.preview_binary_for(
                                  resolved_binary, preview_binary),
                              wrapper=items)
    declaration = adapter.capability() or {}
    side.update({"adapter": adapter, "declaration": declaration, "available": True,
                 "argv_verified": bool(declaration.get("argv_verified")),
                 "preview_binary": adapter.preview_binary,
                 "preview_available": bool(adapter.preview_available),
                 "output_version": str(declaration.get("output_version")
                                       or side["output_version"]),
                 "audit_enabled": bool(declaration.get("audit_enabled"))})
    return side


def _fallback_requested(requested: str, primary: dict, fallback_provider: str) -> bool:
    """回退是否被**显式开启**（Spec §2.6/§7.1）：未配置、none、与主同 provider、主为 fake/none → 关。"""
    wanted = str(fallback_provider or "").strip().lower()
    if not wanted or wanted == DISABLED_CONVERTER:
        return False
    if str(requested or "").strip().lower() in (DISABLED_CONVERTER, SIMULATED_ADAPTER_NAME):
        return False
    return wanted != str(primary.get("provider") or "")


def _resolve_chain(name=None, *, env: Optional[str] = None,
                   allow_simulated: Optional[bool] = None) -> dict:
    """解析整条转换链（主 + 可选回退），返回 capability()/convert 共用的解析结果。"""
    config = _converter_config()
    requested = str(name).strip().lower() if name is not None else config["provider"]
    requested = requested or DEFAULT_CONVERTER

    primary = _resolve_one(provider=requested, binary=config["binary"],
                           expected_version=config["expected_version"],
                           preview_binary=config["preview_binary"],
                           wrapper=config["wrapper"], env=env,
                           allow_simulated=allow_simulated)
    if primary.get("note"):
        config["warnings"].append(str(primary["note"]))

    enabled = _fallback_requested(requested, primary, config["fallback_provider"])
    fallback = _empty_side(str(config["fallback_provider"] or ""))
    if enabled:
        fallback = _resolve_one(provider=config["fallback_provider"],
                                binary=config["fallback_binary"],
                                expected_version=config["fallback_expected_version"],
                                preview_binary=config["fallback_preview_binary"],
                                wrapper=config["fallback_wrapper"], env=env,
                                allow_simulated=allow_simulated)
        if str(fallback.get("provider") or "") == str(primary.get("provider") or ""):
            # 主与回退解析到同一个 provider：视为「无回退」，不重复跑同一条命令。
            enabled = False
            fallback = _empty_side(str(config["fallback_provider"] or ""))
    return {"primary": primary, "fallback": fallback, "fallback_enabled": enabled,
            "available": bool(primary.get("available")
                              or (enabled and fallback.get("available"))),
            "warnings": list(config["warnings"])}


def _resolve(name=None, *, env: Optional[str] = None, allow_simulated: Optional[bool] = None) -> dict:
    """兼容入口：返回**主**转换器那一侧的解析结果（键集与旧版一致）。"""
    return _resolve_chain(name, env=env, allow_simulated=allow_simulated)["primary"]


def _detection_extras(side: dict) -> dict:
    """未安装/不可用时也要把配置现场带进 `detected`（Spec §6），便于前端与审计定位。"""
    extras = {}
    for key in ("provider", "binary", "expected_version", "actual_version"):
        value = side.get(key)
        if value not in (None, ""):
            extras[key] = value
    if side.get("binary_reason"):
        extras["binary_reason"] = str(side["binary_reason"])
    return extras


def _side_extras(side: dict) -> dict:
    """一次尝试的「谁失败了、为什么」摘要（Spec §7.2：detected 的 primary/fallback 段）。"""
    return {"provider": str(side.get("provider") or ""),
            "error_code": "",
            "binary_reason": str(side.get("binary_reason") or "")}


def get_adapter(name=None, *, env: Optional[str] = None, allow_simulated: Optional[bool] = None):
    """按配置选适配器；未安装 / 二进制不可用返回 None（细节看 `capability()`）。

    · `auto`：按 Spec §2.4 的顺序探测真实适配器（ODA 优先），全没有就是「未安装」——**绝不**
      回退到 fake；
    · `fake`：仅当非生产且显式允许，否则直接拒；
    · 具名：未注册/未安装一律视为未安装，不静默换成别的适配器。
    """
    chain = _resolve_chain(name, env=env, allow_simulated=allow_simulated)
    adapter = chain["primary"].get("adapter")
    if adapter is None and chain.get("fallback_enabled"):
        adapter = chain["fallback"].get("adapter")
    return adapter


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


#: `capability()["three_d"]` 的键闭集（Spec §2.1）。
THREE_D_STATUS_KEYS = ("declared", "supported", "available", "status", "reason")
#: `three_d.status` 的闭集（Spec §2.1）。
THREE_D_STATUS_VALUES = ("supported", "unsupported", "unavailable", "unknown")
#: `capability()["acceptance"]` 的键闭集（Spec §3.1）。
ACCEPTANCE_KEYS = ("present", "valid", "reason", "approved_by", "approved_at",
                   "golden_version")
#: `capability()["fallback"]` 的键闭集（修复批 Spec §2.7）。
FALLBACK_KEYS = ("enabled", "provider", "binary", "converter_version", "expected_version",
                 "version_ok", "version_source", "argv_verified", "available",
                 "stable_error_code", "binary_reason", "wrapper")


def _three_d_segment(*, documented: bool, available: bool, provider: str) -> dict:
    """把"这台机器能不能做三维"如实转述成四个状态（Spec §2.1）。

    注意：这**不是**"这份图纸有没有三维"——那份结论在 `dwg_dispatch` 的六态里，
    两张表不许互相推导。
    """
    if not available:
        status, reason = "unavailable", "当前环境没有可用的 DWG 转换服务"
    elif documented:
        status, reason = "supported", "适配器声明支持导出三维中间格式"
    else:
        status, reason = "unsupported", "适配器声明不支持导出三维中间格式"
    return {"declared": bool(documented), "supported": bool(documented),
            "available": bool(available and documented), "status": status,
            "reason": reason}


def _acceptance_segment() -> dict:
    """验收记录当前状态（现读；Spec §3.1）；读不到一律给稳定原因码。"""
    try:
        state = _acceptance_state()
    except Exception:                                     # noqa: BLE001 - 只读查询不许抛
        state = {"present": False, "valid": False, "reason": "missing_record",
                 "approved_by": "", "approved_at": "", "golden_version": ""}
    return {key: state.get(key) for key in ACCEPTANCE_KEYS}


def _fallback_segment(fallback: dict, enabled: bool) -> dict:
    """把回退转换器的解析结果转成 `capability()["fallback"]`（Spec §2.7）。"""
    usable = bool(enabled and fallback.get("available"))
    segment = {
        "enabled": bool(enabled), "provider": str(fallback.get("provider") or ""),
        "binary": str(fallback.get("binary") or ""),
        "converter_version": str(fallback.get("converter_version") or ""),
        "expected_version": str(fallback.get("expected_version") or ""),
        "version_ok": bool(fallback.get("version_ok")),
        "version_source": str(fallback.get("version_source") or ""),
        "argv_verified": bool(fallback.get("argv_verified")),
        "available": usable,
        "stable_error_code": "" if usable else str(fallback.get("error_code") or ""),
        "binary_reason": str(fallback.get("binary_reason") or ""),
        "wrapper": list(fallback.get("wrapper") or []),
    }
    return {key: segment[key] for key in FALLBACK_KEYS}


def capability(*, env: Optional[str] = None) -> dict:
    """本环境的 DWG 转换能力（Spec §2.1 / 修复批 §2.7）。

    口径要点：`available = 主可用 or 回退可用`，`provider` 永远是**主** provider（配置值，
    不随跳次变）；`dwg_supported` 本批**恒为假**——装了转换器不等于「支持 DWG」。
    """
    env_name = _env_name(env)
    error_code = ""
    chain = {"primary": _empty_side(), "fallback": _empty_side(), "fallback_enabled": False,
             "available": False, "warnings": []}
    try:
        chain = _resolve_chain(env=env)
    except FileCapabilityError as exc:
        error_code = exc.stable_error_code

    primary = chain.get("primary") or _empty_side()
    fallback = chain.get("fallback") or _empty_side()
    fallback_enabled = bool(chain.get("fallback_enabled"))
    available = bool(chain.get("available"))
    primary_available = bool(primary.get("available"))
    simulated = bool(primary.get("simulated"))
    provider = str(primary.get("provider") or "")
    version = str(primary.get("converter_version") or "")
    version_ok = bool(primary.get("version_ok"))
    version_source = str(primary.get("version_source") or "unverifiable")
    stable_error_code = "" if available else str(
        primary.get("error_code") or error_code or "DWG_CONVERTER_NOT_INSTALLED")
    primary_unavailable_reason = "" if primary_available else str(
        primary.get("binary_reason") or primary.get("error_code") or error_code or "")

    declaration = dict(primary.get("declaration") or {})
    if not primary_available and fallback_enabled:
        declaration = dict(fallback.get("declaration") or declaration)
    preview_available = bool(primary.get("preview_available")
                             or (fallback_enabled and fallback.get("preview_available")))

    if available and primary_available:
        message = "已安装 DWG 转换服务（%s %s）" % (provider, version)
        if simulated:
            message += "；当前是模拟转换器，只用于验证转换编排，不代表真实转换能力"
        elif version_source == "unverifiable":
            message += "；版本未固定（ODA 不支持版本探测）"
        elif not primary.get("argv_verified"):
            message += "；该驱动的命令行形状未经真机验证"
    elif available:
        message = ("主转换器不可用（%s），将使用备用转换器（%s %s）"
                   % (primary_unavailable_reason or stable_error_code or "unavailable",
                      str(fallback.get("provider") or ""),
                      str(fallback.get("converter_version") or "")))
    else:
        message = str(file_preflight.STABLE_ERROR_CODES.get(stable_error_code, {}).get("message")
                      or "当前环境尚未安装 CAD 转换服务，暂时无法解析 DWG")

    # 声明推导（第 6 批 Spec §3.2）：装了转换器 ≠ 支持 DWG；只有验收记录有效才行。
    claim = {"support_claim": "orchestration_only", "dwg_supported": False}
    try:
        claim = _acceptance_claim(adapter={"available": available, "simulated": simulated},
                                  record_state=_acceptance_segment())
    except Exception:                                     # noqa: BLE001 - 能力查询绝不许 500
        claim = {"support_claim": ("conversion_available" if (available and not simulated)
                                   else "orchestration_only"), "dwg_supported": False}

    return {
        "available": available,
        "simulated": simulated,
        "adapter_name": str(declaration.get("name") or ""),
        "converter_version": version,
        "expected_version": str(primary.get("expected_version") or ""),
        "version_ok": version_ok,
        "version_source": version_source,
        "argv_verified": bool(primary.get("argv_verified")),
        "binary_reason": str(primary.get("binary_reason") or ""),
        "wrapper": list(primary.get("wrapper") or []),
        "preview_render": preview_available,
        "preview_available": preview_available,
        "binary": str(primary.get("binary") or ""),
        "provider": provider,
        "warnings": list(chain.get("warnings") or []),
        "dwg_conversion": bool(declaration.get("dwg_conversion")),
        "three_d_conversion": bool(declaration.get("three_d_conversion")),
        "env": env_name,
        # 三维能力是**声明**，不是"这份图纸有三维"（Spec §2.1 的两张表不许互相推导）。
        "three_d": _three_d_segment(documented=bool(declaration.get("three_d_conversion")),
                                    available=available, provider=provider),
        "acceptance": _acceptance_segment(),
        "support_claim": claim["support_claim"],
        "dwg_supported": bool(claim["dwg_supported"]),
        "stable_error_code": stable_error_code,
        "primary_unavailable_reason": primary_unavailable_reason,
        "fallback": _fallback_segment(fallback, fallback_enabled),
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
    safe_name: str
    safe_attachment: str
    drawing_version: int
    timeout: float
    max_bytes: int
    max_files: int
    primary: Dict[str, Any] = field(default_factory=dict)
    fallback: Dict[str, Any] = field(default_factory=dict)
    fallback_enabled: bool = False
    options: Dict[str, Any] = field(default_factory=dict)
    cache_key: str = ""
    conversion_id: str = ""
    seed_warnings: List[str] = field(default_factory=list)

    @property
    def provider(self) -> str:
        return str(self.primary.get("provider") or "")


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


class _ChainFailure(Exception):
    """整条链都没给出可用产物：抛**主**的稳定码，manifest 记两次尝试（Spec §7.2）。"""

    def __init__(self, error: FileCapabilityError, *, attempts, primary_side, fallback_side=None,
                 fallback_used=False, primary_failure_code="", fallback_error=None,
                 stderr_digest=None, warnings=None, counts=None, quality=None) -> None:
        self.error = error
        self.attempts = list(attempts or [])
        self.primary_side = dict(primary_side or {})
        self.fallback_side = dict(fallback_side or {})
        self.fallback_used = bool(fallback_used)
        self.primary_failure_code = str(primary_failure_code or "")
        self.fallback_error = fallback_error
        self.stderr_digest = stderr_digest
        self.warnings = list(warnings or [])
        self.counts = dict(counts or _empty_counts())
        self.quality = dict(quality or _empty_quality())
        super().__init__(error.message)

    @property
    def effective_side(self) -> dict:
        return self.fallback_side if self.fallback_used else self.primary_side

    def error_detected(self, base: dict) -> dict:
        """`detected` 同时带 primary 与 fallback 两段（Spec §7.2），供抛错与审计共用。"""
        detected = dict(base or {})
        detected.update(_detection_extras(self.primary_side))
        primary = _side_extras(self.primary_side)
        primary["error_code"] = str(self.error.stable_error_code or "")
        detected["primary"] = primary
        if self.fallback_used and self.fallback_side:
            fallback = _side_extras(self.fallback_side)
            fallback["error_code"] = str(getattr(self.fallback_error, "stable_error_code", "")
                                         or "")
            detected["fallback"] = fallback
        return detected


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
            "output_version": "", "audit_enabled": False, "insunits": None,
            "entity_count": 0, "layer_count": 0, "text_count": 0,
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


def _assert_output_quality(job: "_Job", side: dict, staged, quality: dict) -> dict:
    """产物质量门槛（Spec §4）：**退出码 0 不等于转换成功**。

    就地填充 `quality`（失败时也保留已经跑过的 check），任一条不过就抛
    `DWG_CONVERTER_OUTPUT_INVALID`。
    """
    quality.update(_empty_quality([QUALITY_CHECKS[0]]))
    quality["output_version"] = str(side.get("output_version") or "")
    quality["audit_enabled"] = bool(side.get("audit_enabled"))
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


def _injected_chain(adapter) -> dict:
    """显式注入适配器（测试 / 扩展点）：单跳次，不做回退。"""
    declaration = adapter.capability() or {}
    binary = str(declaration.get("binary") or "")
    side = _empty_side(str(declaration.get("provider") or declaration.get("name") or ""))
    side.update({
        "adapter": adapter, "declaration": declaration, "available": True,
        "simulated": bool(declaration.get("simulated")),
        "converter_version": str(declaration.get("version") or ""),
        "expected_version": str(declaration.get("expected_version") or ""),
        "argv_verified": bool(declaration.get("argv_verified")),
        "preview_available": bool(declaration.get("preview_render")),
        "wrapper": list(declaration.get("wrapper") or []),
        "output_version": str(declaration.get("output_version") or ""),
        "audit_enabled": bool(declaration.get("audit_enabled")),
        "binary": binary, "binary_sha256": _binary_sha256(binary),
        "version_source": "config_declared" if declaration.get("version") else "unverifiable",
        "version_ok": True,
    })
    return {"primary": side, "fallback": _empty_side(), "fallback_enabled": False,
            "available": True, "warnings": []}


def _chain_fingerprint(primary: dict, fallback: dict, enabled: bool) -> str:
    """生效转换器身份段（Spec §7.3）：主 provider/version/二进制 + 回退 provider/version/二进制。"""
    parts = [str(primary.get("provider") or ""), str(primary.get("converter_version") or ""),
             str(primary.get("binary_sha256") or "")]
    if enabled:
        parts.extend([str(fallback.get("provider") or ""),
                      str(fallback.get("converter_version") or ""),
                      str(fallback.get("binary_sha256") or "")])
    return "|".join(parts)


def convert_drawing(project_id: str, filename: str, content, *, adapter=None,
                    attachment_name: str = "", drawing_version: int = 1,
                    timeout_seconds=None) -> dict:
    """把一份 DWG 转成中间格式，返回 manifest（Spec §2.1 / §3.2 / §7）。"""
    detected = file_preflight.detect_file_format(filename, content)
    _assert_convertible(detected)

    if adapter is not None:
        chain = _injected_chain(adapter)
    else:
        chain = _resolve_chain()
        if not chain["available"]:
            primary = chain["primary"]
            blocked = dict(detected)
            blocked.update(_detection_extras(primary))
            raise FileCapabilityError(str(primary.get("error_code")
                                          or "DWG_CONVERTER_NOT_INSTALLED"), detected=blocked)

    primary = chain["primary"]
    fallback_enabled = bool(chain.get("fallback_enabled"))
    fallback = chain["fallback"] if fallback_enabled else _empty_side()
    declaration = dict(primary.get("declaration") or {})

    safe_name = _safe_basename(filename)
    safe_attachment = (_safe_basename(attachment_name)
                       if str(attachment_name or "").strip() else safe_name)
    env_timeout, env_bytes, env_files = _limits()
    timeout = float(timeout_seconds) if timeout_seconds else env_timeout
    version = int(drawing_version or 1)

    fingerprint = _chain_fingerprint(primary, fallback, fallback_enabled)
    options = {
        "timeout_seconds": timeout,
        "max_output_bytes": env_bytes,
        "max_output_files": env_files,
        "drawing_version": version,
        "original_filename": safe_name,
        "attachment_name": safe_attachment,
        "source_format": "dwg",
        "adapter_simulated": bool(primary.get("simulated")),
        "converter_provider": str(primary.get("provider") or ""),
        "converter_binary": Path(str(primary.get("binary") or "")).name,
        "expected_version": str(primary.get("expected_version") or ""),
        "output_version": str(primary.get("output_version") or ""),
        "audit_enabled": bool(primary.get("audit_enabled")),
        "wrapper": list(primary.get("wrapper") or []),
        "converter_chain": fingerprint,
    }
    # 转换身份（决定 conversion_id 与产物目录）只认「同一份图纸 + **主**转换器 + 同一图纸
    # 版本」：**不含展示名、不随回退分裂**。同一张 DWG 换个附件名或换一次生效跳次，都不该
    # 在项目里堆出第二份产物目录（Spec §7.3）。
    identity_digest = _digest("|".join([
        str(detected.get("sha256") or ""),
        str(primary.get("provider") or ""),
        str(primary.get("expected_version") or primary.get("converter_version") or ""),
        str(version),
    ]))
    options_digest = _digest(json.dumps(options, sort_keys=True, ensure_ascii=False))
    cache_key = _digest("|".join([identity_digest, fingerprint, options_digest]))
    job = _Job(project_id=str(project_id), content=bytes(content), detected=detected,
               safe_name=safe_name, safe_attachment=safe_attachment,
               drawing_version=version, timeout=timeout, max_bytes=env_bytes,
               max_files=env_files, primary=primary, fallback=fallback,
               fallback_enabled=fallback_enabled, options=options, cache_key=cache_key,
               conversion_id=identity_digest[:CONVERSION_ID_LENGTH],
               seed_warnings=list(chain.get("warnings") or []))
    del declaration

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


def _should_fallback(side: dict, error: FileCapabilityError) -> bool:
    """主转换器这一次失败是否值得换回退转换器（Spec §7.1 的严格表）。"""
    reason = str(side.get("binary_reason") or "")
    if reason in _NEVER_FALLBACK_REASONS:
        return False
    if reason in _FALLBACK_BINARY_REASONS:
        return True
    return str(getattr(error, "stable_error_code", "") or "") in _FALLBACK_ERROR_CODES


def _attempt_row(role: str, side: dict, status: str, error_code: str, counts, duration_ms) -> dict:
    """`attempts[]` 的一条（Spec §7.2 的字段闭集）。"""
    counts = dict(counts or _empty_counts())
    return {"role": str(role), "provider": str(side.get("provider") or ""),
            "converter_version": str(side.get("converter_version") or ""),
            "status": str(status), "error_code": str(error_code or ""),
            "warning_count": int(counts.get("warning_count") or 0),
            "error_count": int(counts.get("error_count") or 0),
            "duration_ms": int(duration_ms)}


def _work_dir_for(job: "_Job", side: dict, temp_dir: Path, role: str) -> Path:
    """本次跳次的工作区：默认是临时目录里的子目录（随 `_run` 一起回收）。

    需要真实输入/输出目录的驱动（ODA）由适配器自己指定一个**不会被回收**的工作区，
    否则调用结束后连 `argv` 里的目录都不复存在（Spec §3）。
    """
    default = Path(temp_dir) / role
    allocate = getattr(side.get("adapter"), "work_dir_for", None)
    if not callable(allocate):
        return default
    try:
        artifact = persistence.artifact_dir(job.project_id, job.conversion_id)
        chosen = allocate(default=default, artifact_dir=artifact, role=role)
    except Exception:                                     # noqa: BLE001 - 退回临时目录
        return default
    return Path(chosen) if chosen else default


def _clear_scratch(work_dir: Path, temp_dir: Path) -> None:
    """临时目录整个随 `_run` 删；持久工作区只清内容、保留目录本身。"""
    root = Path(work_dir)
    try:
        if _within(root.resolve(), Path(temp_dir).resolve()) or not root.is_dir():
            return
    except OSError:
        return
    for item in root.iterdir():
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            try:
                item.unlink()
            except OSError:
                pass


def _attempt(job: "_Job", side: dict, temp_dir: Path, role: str) -> dict:
    """跑**一次**跳次：返回 {ok, error?, outcome?, attempt}。半成品一律不进产物。"""
    clock = _monotonic_ms()
    if side.get("adapter") is None:
        code = str(side.get("error_code") or "DWG_CONVERTER_NOT_INSTALLED")
        error = FileCapabilityError(code, detected=_detection_extras(side))
        return {"ok": False, "error": error,
                "attempt": _attempt_row(role, side, STATUS_FAILED, code, _empty_counts(),
                                        _monotonic_ms() - clock)}
    attempt_dir = _work_dir_for(job, side, temp_dir, role)
    attempt_dir.mkdir(parents=True, exist_ok=True)
    try:
        outcome = _convert_in_temp_dir(job, side, attempt_dir)
    except _ConversionFailure as exc:
        return {"ok": False, "error": exc.error, "counts": exc.counts, "quality": exc.quality,
                "warnings": exc.warnings, "stderr_digest": exc.stderr_digest,
                "attempt": _attempt_row(role, side, STATUS_FAILED,
                                        exc.error.stable_error_code, exc.counts,
                                        _monotonic_ms() - clock)}
    except FileCapabilityError as exc:
        return {"ok": False, "error": exc, "counts": _empty_counts(), "quality": _empty_quality(),
                "warnings": list(job.seed_warnings), "stderr_digest": None,
                "attempt": _attempt_row(role, side, STATUS_FAILED, exc.stable_error_code,
                                        _empty_counts(), _monotonic_ms() - clock)}
    except Exception:                                     # noqa: BLE001 - 绝不透传原始异常
        failure = FileCapabilityError("DWG_CONVERSION_FAILED", detected=job.detected)
        return {"ok": False, "error": failure, "counts": _empty_counts(),
                "quality": _empty_quality(), "warnings": list(job.seed_warnings),
                "stderr_digest": None,
                "attempt": _attempt_row(role, side, STATUS_FAILED,
                                        failure.stable_error_code, _empty_counts(),
                                        _monotonic_ms() - clock)}
    finally:
        # 失败跳次留下的半成品绝不进产物：持久工作区用完即清（Spec §7.2）。
        _clear_scratch(attempt_dir, temp_dir)
    return {"ok": True, "outcome": outcome, "counts": outcome["counts"],
            "quality": outcome["quality"], "warnings": outcome["warnings"],
            "stderr_digest": outcome["stderr_digest"],
            "attempt": _attempt_row(role, side, outcome["status"], "",
                                    outcome["counts"], _monotonic_ms() - clock)}


def _monotonic_ms() -> int:
    import time
    return int(time.monotonic() * 1000)


def _execute_chain(job: "_Job", temp_dir: Path) -> dict:
    """主 → （必要时）回退。成功返回 outcome + 留痕；都失败抛 `_ChainFailure`。"""
    attempts: List[dict] = []
    primary_result = _attempt(job, job.primary, temp_dir, ROLE_PRIMARY)
    attempts.append(primary_result["attempt"])
    if primary_result["ok"]:
        outcome = dict(primary_result["outcome"])
        outcome.update({"converter_role": ROLE_PRIMARY, "fallback_used": False,
                        "primary_failure_code": "", "attempts": attempts,
                        "effective": job.primary})
        return outcome

    primary_error = primary_result["error"]
    primary_code = str(primary_error.stable_error_code or "")
    if not job.fallback_enabled or not _should_fallback(job.primary, primary_error):
        # 配置错误 / 输入问题 / 回退被关：一次尝试就结束，半成品随临时目录清理。
        raise _ChainFailure(primary_error, attempts=attempts, primary_side=job.primary,
                            fallback_used=False, primary_failure_code="",
                            stderr_digest=primary_result.get("stderr_digest"),
                            warnings=primary_result.get("warnings"),
                            counts=primary_result.get("counts"),
                            quality=primary_result.get("quality"))

    fallback_result = _attempt(job, job.fallback, temp_dir, ROLE_FALLBACK)
    attempts.append(fallback_result["attempt"])
    if fallback_result["ok"]:
        outcome = dict(fallback_result["outcome"])
        outcome.update({"converter_role": ROLE_FALLBACK, "fallback_used": True,
                        "primary_failure_code": primary_code, "attempts": attempts,
                        "effective": job.fallback,
                        "primary_attempt": primary_result["attempt"]})
        return outcome

    raise _ChainFailure(primary_error, attempts=attempts, primary_side=job.primary,
                        fallback_side=job.fallback, fallback_used=True,
                        primary_failure_code=primary_code,
                        fallback_error=fallback_result["error"],
                        stderr_digest=fallback_result.get("stderr_digest"),
                        warnings=fallback_result.get("warnings"),
                        counts=fallback_result.get("counts"),
                        quality=fallback_result.get("quality"))


def _run(job: "_Job") -> dict:
    started = now_cst_str()
    temp_dir = Path(tempfile.mkdtemp(prefix=_TEMP_PREFIX))
    try:
        try:
            outcome = _execute_chain(job, temp_dir)
        except _ChainFailure as exc:
            _save_failure(job, started, exc)
            error = exc.error
            error.detected = exc.error_detected(job.detected)
            raise error
        except FileCapabilityError as exc:
            _save_simple_failure(job, started, exc)
            raise
        except Exception:
            failure = FileCapabilityError("DWG_CONVERSION_FAILED", detected=job.detected)
            _save_simple_failure(job, started, failure)
            raise failure from None

        side = outcome["effective"]
        manifest = _manifest(job, side=side, started=started, status=outcome["status"],
                             outputs=outcome["outputs"], output_sha256=outcome["output_sha256"],
                             warnings=outcome["warnings"], stderr_digest=outcome["stderr_digest"],
                             counts=outcome["counts"], quality=outcome["quality"],
                             converter_role=outcome["converter_role"],
                             fallback_used=outcome["fallback_used"],
                             primary_failure_code=outcome["primary_failure_code"],
                             attempts=outcome["attempts"])
        persistence.save_manifest(job.project_id, manifest)
        persistence.sync(job.project_id, job.conversion_id)
        _audit(job, "dwg.convert", manifest)
        if outcome["fallback_used"]:
            _audit(job, "dwg.convert.fallback", manifest)
        return manifest
    finally:
        # 成功与失败都必须清理临时目录（Spec §5）。
        shutil.rmtree(temp_dir, ignore_errors=True)


def _convert_in_temp_dir(job: "_Job", side: dict, temp_dir: Path) -> dict:
    """在临时目录里跑一次转换：调用 → 收集诊断 → 质量门槛 → 落盘产物（Spec §4/§5）。

    返回 `outcome`：
        status / outputs / output_sha256 / warnings / stderr_digest / counts / quality
    任一步不达标都抛 `_ConversionFailure`，并把**已经跑过的**诊断与质量现场一起带走
    （失败也要能回答「卡在哪一条门槛」）。
    """
    adapter = side["adapter"]
    declaration = dict(side.get("declaration") or {})
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
        inspected = _call_with_timeout(lambda: adapter.inspect(request), job.timeout,
                                       job.detected)
        warnings.extend(str(w) for w in (result_field(inspected, "warnings", []) or []))

        converted = _call_with_timeout(lambda: adapter.convert_to_dxf(request),
                                       job.timeout, job.detected)
        stderr_digest = result_field(converted, "stderr_digest", None)
        warnings.extend(str(w) for w in (result_field(converted, "warnings", []) or []))
        diagnostics = _collect_diagnostics(diagnostics, converted)
        if str(result_field(converted, "status", "ok") or "ok").lower() != "ok":
            code = str(result_field(converted, "error_code", None) or "DWG_CONVERSION_FAILED")
            raise FileCapabilityError(code, detected=job.detected)

        dxf_path = result_field(converted, "dxf_path", None)
        previews = list(result_field(converted, "preview_paths", []) or [])
        if declaration.get("preview_render"):
            rendered = _call_with_timeout(
                lambda: adapter.render_preview(request, dxf_path), job.timeout, job.detected)
            warnings.extend(str(w) for w in (result_field(rendered, "warnings", []) or []))
            # 预览工具把 SVG 写到 stdout：那是产物不是诊断，只收 stderr。
            diagnostics = _collect_diagnostics(diagnostics, rendered, streams=("stderr",))
            if str(result_field(rendered, "status", "ok") or "ok").lower() == "ok":
                previews.extend(result_field(rendered, "preview_paths", []) or [])

        collected = {"status": "ok", "dxf_path": dxf_path, "preview_paths": previews,
                     "warnings": [], "error_code": None, "stderr_digest": stderr_digest}
        staged = _staged_outputs(job, collected, temp_dir)
        if not any(role == "preview" for role, _path in staged):
            if declaration.get("preview_render"):
                raise FileCapabilityError("DWG_CONVERTER_OUTPUT_MISSING", detected=job.detected)
            warnings.append("该转换器不支持预览渲染：本次只产出 DXF")

        counts = _diagnostics_counts(diagnostics)
        _assert_output_quality(job, side, staged, quality)
        quality["degraded"] = bool(counts["warning_count"] or counts["error_count"])
        status = STATUS_OK
        if counts["warning_count"] or counts["error_count"]:
            # 有损转换必须如实说：下游要按这个提示去核对，不许报「干净」。
            warnings.append("转换器报告了 %d 条警告、%d 条错误：图纸已生成但有损，请下游核对"
                            % (counts["warning_count"], counts["error_count"]))
            status = STATUS_WITH_WARNINGS

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


def _staged_outputs(job: "_Job", converted, output_dir: Path):
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


def _save_outputs(job: "_Job", staged):
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


def _manifest(job: "_Job", *, side: dict, started: str, status: str,
              converter_role: str = ROLE_PRIMARY, fallback_used: bool = False,
              primary_failure_code: str = "", attempts=None,
              error_code: Optional[str] = None, outputs=None, output_sha256=None,
              warnings=None, stderr_digest=None, counts=None, quality=None) -> dict:
    declaration = dict(side.get("declaration") or {})
    simulated = bool(side.get("simulated"))
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
        "converter_name": str(declaration.get("name") or side.get("provider") or ""),
        "converter_version": str(side.get("converter_version") or ""),
        # 受控回退链的留痕（Spec §7.2）：生效角色、是否跳次、主失败码、逐次尝试。
        "converter_role": str(converter_role or ""),
        "fallback_used": bool(fallback_used),
        "primary_failure_code": str(primary_failure_code or ""),
        "attempts": [dict(item) for item in (attempts or [])],
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


def _save_failure(job: "_Job", started: str, chain: "_ChainFailure") -> None:
    """整条链都没给出可用产物：留一条 status=failed 的 manifest（Spec §7.2）。"""
    manifest = _manifest(
        job, side=chain.effective_side, started=started, status=STATUS_FAILED,
        converter_role=(ROLE_FALLBACK if chain.fallback_used else ROLE_PRIMARY),
        fallback_used=chain.fallback_used, primary_failure_code=chain.primary_failure_code,
        attempts=chain.attempts, error_code=chain.error.stable_error_code,
        outputs=[], output_sha256={}, warnings=chain.warnings,
        stderr_digest=chain.stderr_digest, counts=chain.counts, quality=chain.quality)
    persistence.save_manifest(job.project_id, manifest)
    _audit(job, "dwg.convert.failed", manifest)
    if chain.fallback_used:
        _audit(job, "dwg.convert.fallback", manifest)


def _save_simple_failure(job: "_Job", started: str, error: FileCapabilityError) -> None:
    """单跳次失败（预检/输入类或未知异常）：不动上一次成功产物（Spec §4.2）。"""
    manifest = _manifest(job, side=job.primary, started=started, status=STATUS_FAILED,
                         converter_role=ROLE_PRIMARY, fallback_used=False,
                         primary_failure_code="", attempts=[], error_code=error.stable_error_code,
                         outputs=[], output_sha256={}, warnings=list(job.seed_warnings))
    persistence.save_manifest(job.project_id, manifest)
    _audit(job, "dwg.convert.failed", manifest)


def _audit(job: "_Job", action: str, manifest: dict) -> None:
    """审计只记可追溯的安全字段：摘要、角色、版本、状态、计数与质量——没有字节/路径/堆栈。"""
    quality = manifest.get("quality") or {}
    persistence.audit(job.project_id, action, {
        "conversion_id": manifest.get("conversion_id") or "",
        "source_sha256": manifest.get("source_sha256") or "",
        "detected_dwg_version": manifest.get("detected_dwg_version") or "",
        "converter_name": manifest.get("converter_name") or "",
        # 二进制只记 basename：审计里不许出现绝对路径（Spec §5）。
        "provider": str(job.provider or ""),
        "converter_role": manifest.get("converter_role") or "",
        "binary": Path(str(job.primary.get("binary") or "")).name,
        "converter_version": manifest.get("converter_version") or "",
        "status": manifest.get("status") or "",
        "stable_error_code": manifest.get("error_code") or "",
        "primary_failure_code": manifest.get("primary_failure_code") or "",
        "fallback_used": bool(manifest.get("fallback_used")),
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

# -*- coding: utf-8 -*-
"""DWG 支持验收记录：纯读取 + 纯推导（第 6 批 Spec §3）。

这个模块**不 import** `cad_converter` / `dwg_dispatch`（避免环）：它只回答两件事
——"这份验收记录现在是什么状态"与"据此能不能说支持 DWG"。现场事实（换了哪个转换器、
二进制指纹、样本哈希）由调用方以 `live` 传进来，或由本模块从环境与磁盘现算。

纪律（Spec §3.1/§3.2）：
  · 记录**每次调用现读**，不在 import 时固化（删除文件即可回退声明，无需重启）；
  · `support_claim()` 是纯函数：装了转换器**不等于**支持 DWG；
  · 只有记录全部校验通过才允许 `supported` / `dwg_supported=True`。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

ACCEPTANCE_RECORD_VERSION = "dwg-acceptance/1"
RECORD_PATH_ENV = "DWG_ACCEPTANCE_RECORD"
SAMPLES_DIR_ENV = "DWG_ACCEPTANCE_SAMPLES_DIR"

#: 默认记录位（Spec §3.1）。
DEFAULT_RECORD_NAME = "dwg_acceptance.json"
DEFAULT_RECORD_RELATIVE = ("tech_app", "agent_knowledge", DEFAULT_RECORD_NAME)
DEFAULT_SAMPLES_RELATIVE = ("裕同包装项目-待开发",)

#: 两份真实样本（只读、不入库）。
SAMPLE_FILENAMES = ("酒盒.dwg", "圆盘盒.dwg")

#: `validate()` 的稳定原因码闭集（Spec §3.1；空串表示有效）。
ACCEPTANCE_REASONS = ("missing_record", "unreadable", "record_version_unsupported",
                      "converter_mismatch", "binary_mismatch", "samples_missing",
                      "sample_hash_mismatch", "unapproved", "e2e_report_missing",
                      "e2e_report_hash_mismatch")

#: 声明闭集（Spec §3.2）；第 2 批的 "real" 继续禁止。
SUPPORT_CLAIMS = ("orchestration_only", "conversion_available", "supported")

#: `acceptance()` / `validate()` 的冻结形状（Spec §2.1）。
_STATE_KEYS = ("present", "valid", "reason", "approved_by", "approved_at", "golden_version")


def repo_root() -> Path:
    """仓库根：本文件在 `<root>/tech_app/backend/services/` 下。"""
    return Path(__file__).resolve().parents[3]


def record_path() -> Path:
    """验收记录位置：`DWG_ACCEPTANCE_RECORD` 优先，否则仓库内默认位（Spec §3.1）。"""
    override = str(os.environ.get(RECORD_PATH_ENV) or "").strip()
    if override:
        return Path(override)
    return repo_root().joinpath(*DEFAULT_RECORD_RELATIVE)


def samples_dir() -> Path:
    """两份真实样本所在目录：`DWG_ACCEPTANCE_SAMPLES_DIR` 优先（Spec §3.1）。"""
    override = str(os.environ.get(SAMPLES_DIR_ENV) or "").strip()
    if override:
        return Path(override)
    return repo_root().joinpath(*DEFAULT_SAMPLES_RELATIVE)


def sha256_file(path: Any) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except Exception:
        return ""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _parse_time(value: Any) -> bool:
    raw = _text(value)
    if not raw:
        return False
    try:
        datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _read(path: Path):
    """返回 (state, data)：state ∈ {missing, unreadable, ok}。"""
    try:
        if not path.is_file():
            return "missing", {}
    except Exception:
        return "missing", {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "unreadable", {}
    if not isinstance(data, dict):
        return "unreadable", {}
    return "ok", data


def load_record(*, path: Any = None) -> Dict[str, Any]:
    """现读验收记录；缺失或损坏一律返回 `{}`（原因由 `validate()` 点名）。"""
    target = Path(path) if path is not None else record_path()
    state, data = _read(target)
    return data if state == "ok" else {}


def live_facts() -> Dict[str, Any]:
    """现场事实（Spec §3.1）：主转换器身份 + 主二进制指纹 + 两份样本哈希。

    只读环境变量与文件，不 import 适配层（避免环），也不联网。
    """
    provider = _text(os.environ.get("DWG_CONVERTER_PROVIDER")
                     or os.environ.get("CAD_CONVERTER_PROVIDER")
                     or os.environ.get("CAD_CONVERTER"))
    version = _text(os.environ.get("DWG_CONVERTER_VERSION")
                    or os.environ.get("CAD_CONVERTER_VERSION"))
    binary = _text(os.environ.get("DWG_CONVERTER_BINARY")
                   or os.environ.get("CAD_CONVERTER_BINARY"))
    samples: Dict[str, str] = {}
    root = samples_dir()
    for name in SAMPLE_FILENAMES:
        digest = sha256_file(root / name)
        if digest:
            samples[name] = digest
    return {"converter_name": provider, "converter_version": version,
            "binary_sha256": sha256_file(binary) if binary else "",
            "samples": samples}


def _state(*, present: bool, valid: bool, reason: str, record: Dict[str, Any]) -> Dict[str, Any]:
    return {"present": bool(present), "valid": bool(valid), "reason": str(reason or ""),
            "approved_by": _text((record or {}).get("approved_by")),
            "approved_at": _text((record or {}).get("approved_at")),
            "golden_version": _text((record or {}).get("golden_version"))}


def _missing(reason: str, record: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    row = _state(present=False, valid=False, reason=reason, record=record or {})
    if reason != "missing_record":
        row["present"] = True
    return row


def validate(record: Any, *, live: Any = None, samples_dir: Any = None,
             record_path: Any = None) -> Dict[str, Any]:
    """校验验收记录（Spec §3.1 的顺序表）；`record` 为 None 时现读记录文件。"""
    path = Path(record_path) if record_path is not None else globals()["record_path"]()
    data = record
    if data is None:
        state, loaded = _read(path)
        if state == "missing":
            return _missing("missing_record")
        if state == "unreadable":
            return _missing("unreadable")
        data = loaded
    if not isinstance(data, dict) or not data:
        return _missing("missing_record")
    live = live if isinstance(live, dict) else live_facts()
    root = Path(samples_dir) if samples_dir is not None else globals()["samples_dir"]()

    if _text(data.get("record_version")) != ACCEPTANCE_RECORD_VERSION:
        return _missing("record_version_unsupported", data)
    converter = data.get("converter") if isinstance(data.get("converter"), dict) else {}
    if (_text(converter.get("name")) != _text(live.get("converter_name"))
            or _text(converter.get("version")) != _text(live.get("converter_version"))):
        return _missing("converter_mismatch", data)
    if _text(converter.get("binary_sha256")) != _text(live.get("binary_sha256")):
        return _missing("binary_mismatch", data)
    samples = data.get("samples") if isinstance(data.get("samples"), list) else []
    declared = [row for row in samples if isinstance(row, dict)]
    if len(declared) < len(SAMPLE_FILENAMES):
        return _missing("samples_missing", data)
    # 样本来源：显式 `samples_dir` 时以目录里的文件为准（Spec §3.1 的默认位）；
    # 未显式给出时优先用调用方传进来的现场哈希（`live.samples`）——那是"现场事实"，
    # 记录与它比对即可，不必要求同一台机器上还存在同一份文件。
    explicit = samples_dir is not None
    live_samples = live.get("samples") if isinstance(live.get("samples"), dict) else {}
    for row in declared:
        name = _text(row.get("filename"))
        file_hash = sha256_file(root / name) if name else ""
        live_hash = _text(live_samples.get(name))
        if explicit:
            if not file_hash:
                return _missing("samples_missing", data)
            actual = file_hash
        else:
            actual = live_hash or file_hash
            if not actual:
                return _missing("samples_missing", data)
        expected = _text(row.get("sha256"))
        if not expected or expected != actual:
            return _missing("sample_hash_mismatch", data)
    if not _text(data.get("approved_by")) or not _parse_time(data.get("approved_at")):
        return _missing("unapproved", data)
    e2e = data.get("e2e") if isinstance(data.get("e2e"), dict) else {}
    report = _text(e2e.get("report_path"))
    if not report or not Path(report).is_file():
        return _missing("e2e_report_missing", data)
    if _text(e2e.get("report_sha256")) != sha256_file(report):
        return _missing("e2e_report_hash_mismatch", data)
    return _state(present=True, valid=True, reason="", record=data)


def support_claim(*, adapter: Any = None, record_state: Any = None) -> Dict[str, Any]:
    """声明推导（Spec §3.2 四行表）——纯函数，不读盘、不联网。"""
    facts = adapter if isinstance(adapter, dict) else {}
    available = bool(facts.get("available"))
    simulated = bool(facts.get("simulated"))
    state = record_state if isinstance(record_state, dict) else {}
    valid = bool(state.get("valid"))
    reason = _text(state.get("reason"))
    if not available:
        return {"support_claim": "orchestration_only", "dwg_supported": False,
                "reason": "没有可用的真实转换器"}
    if simulated:
        return {"support_claim": "orchestration_only", "dwg_supported": False,
                "reason": "当前是模拟转换器，只能证明编排能力"}
    if valid:
        return {"support_claim": "supported", "dwg_supported": True,
                "reason": "转换器可用且验收记录有效"}
    return {"support_claim": "conversion_available", "dwg_supported": False,
            "reason": reason or "验收记录无效"}


def acceptance(*, adapter: Any = None, live: Any = None, record_path: Any = None) -> Dict[str, Any]:
    """只读查询：`validate(load_record())` 的当前结论（Spec §3.1）。"""
    path = Path(record_path) if record_path is not None else globals()["record_path"]()
    state, loaded = _read(path)
    if state == "missing":
        return _missing("missing_record")
    if state == "unreadable":
        return _missing("unreadable")
    return validate(loaded, live=live, record_path=path)


def summarize(record_state: Any) -> Dict[str, Any]:
    """小摘要（给健康检查/报告用）：只给结论，不带路径与文件明细。"""
    state = record_state if isinstance(record_state, dict) else {}
    return {"present": bool(state.get("present")), "valid": bool(state.get("valid")),
            "reason": _text(state.get("reason")),
            "golden_version": _text(state.get("golden_version")),
            "approved": bool(_text(state.get("approved_by"))
                             and _parse_time(state.get("approved_at")))}

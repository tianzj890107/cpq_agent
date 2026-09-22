# -*- coding: utf-8 -*-
"""客户材料原文 → 材料码的**唯一映射表**（Spec `packaging-business-material-code-map.md` §C1）。

为什么单独成模块：材料码的解析以前只有一个"取第一个空白分词、在 `kb_material.name` 里唯一
包含"的规则（`packaging_bom._resolve_material_code()`），客户原文（`350G玖龙粉灰` / `EVA` /
`磁铁`）大多解不出来，而业务/采购**没有任何可维护的落点**去补一条映射。本模块把那个落点做成
一个仓库内置的 JSON（`tech_app/agent_knowledge/rules/packaging_material_code_map.json`）。

三条硬口径（与 `packaging_semantics/rules.py` 同一条纪律）：
  · 映射只有一个来源（那个 JSON，或 env 覆盖的同一份结构），代码里不许硬编码任何对照；
  · 文件缺失 / 不是合法 JSON / `rule_set` 不对 / `entries` 形状不对 → `MaterialMapError`，
    **不许**静默回落成空表（"读不到"与"没有映射"是两件事）；
  · 同一个规范化键出现两个不同码 → `ambiguous`，**不许猜**。

纯读：不连库、不联网、不调模型、不改任何业务数据。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ENGINE_VERSION = "packaging-material-map/1"
MATERIAL_MAP_FILENAME = "packaging_material_code_map.json"
RULE_SET = "packaging_material_code_map_v1"
ENV_MATERIAL_MAP_PATH = "PACKAGING_MATERIAL_MAP_PATH"
MATERIAL_MAP_ERROR_CODE = "PACKAGING_MATERIAL_CODE_MAP_INVALID"

#: `lookup_material_code()` 的状态闭集。
LOOKUP_STATUSES = ("hit", "missing", "ambiguous")


class MaterialMapError(Exception):
    """映射表不可用。文案只给可读中文（不带路径 / 堆栈），稳定码同时以两个名字暴露。"""

    def __init__(self, message: str):
        super().__init__(message)
        self.code = MATERIAL_MAP_ERROR_CODE
        self.stable_error_code = MATERIAL_MAP_ERROR_CODE


def _text(value: Any) -> str:
    return str(value or "").strip()


def _package_root() -> Path:
    # …/tech_app/backend/services/packaging_material_map.py → 仓库根
    return Path(__file__).resolve().parents[3]


def default_map_path() -> Path:
    return _package_root() / "tech_app" / "agent_knowledge" / "rules" / MATERIAL_MAP_FILENAME


def map_path() -> Tuple[Path, str]:
    """返回 (路径, 来源)。来源闭集：`default`（仓库内置）/ `override`（env 覆盖）。"""
    override = _text(os.environ.get(ENV_MATERIAL_MAP_PATH))
    if override:
        return Path(override), "override"
    return default_map_path(), "default"


def normalize_material_text(text: Any) -> str:
    """映射的键一律过它：去掉**所有**空白（含全角空格）、ASCII 转小写。

    `350G 玖龙粉灰` 与 `350g玖龙粉灰` 因此是同一个键 —— 客户表里的空格与大小写不算区别。
    """
    value = _text(text)
    if not value:
        return ""
    return "".join(char for char in value if not char.isspace()).lower()


def read_material_map(path: Any = None) -> Tuple[List[dict], str, str]:
    """读映射表 → `(entries, source, fingerprint)`；任何形状问题都抛 `MaterialMapError`。

    `fingerprint` = 文件字节的 sha256 前 12 位（与图层规则同一口径，用于"这一版 BOM 用的是
    哪一份映射"回查）。`path` 显式给出时来源记为 `override`。
    """
    if path is None:
        target, source = map_path()
    else:
        target, source = Path(path), "override"
    try:
        raw = Path(target).read_bytes()
    except OSError as exc:
        raise MaterialMapError("材料原文映射表缺失或无法读取，请联系系统管理员") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MaterialMapError("材料原文映射表不是合法 JSON，请联系系统管理员") from exc
    if not isinstance(data, dict):
        raise MaterialMapError("材料原文映射表格式不正确，请联系系统管理员")
    if _text(data.get("rule_set")) != RULE_SET:
        raise MaterialMapError("材料原文映射表的 rule_set 不是 %s，请联系系统管理员" % RULE_SET)
    raw_entries = data.get("entries")
    if not isinstance(raw_entries, list):
        raise MaterialMapError("材料原文映射表的 entries 必须是数组，请联系系统管理员")
    entries: List[dict] = []
    for index, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            raise MaterialMapError("材料原文映射表第 %d 条不是对象" % (index + 1))
        text = _text(item.get("text"))
        code = _text(item.get("material_code"))
        if not text or not code:
            raise MaterialMapError("材料原文映射表第 %d 条缺 text 或 material_code" % (index + 1))
        entries.append({"text": text, "material_code": code, "note": _text(item.get("note"))})
    return entries, source, hashlib.sha256(raw).hexdigest()[:12]


def lookup_material_code(text: Any, entries: Any) -> Tuple[str, str]:
    """按规范化键查映射 → `(code, status)`；`status ∈ LOOKUP_STATUSES`。

    同键两码 → `("", "ambiguous")`（不许挑一个）；没查到 → `("", "missing")`。
    """
    key = normalize_material_text(text)
    if not key:
        return "", "missing"
    codes: List[str] = []
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        if normalize_material_text(item.get("text")) != key:
            continue
        code = _text(item.get("material_code"))
        if code and code not in codes:
            codes.append(code)
    if len(codes) == 1:
        return codes[0], "hit"
    return "", ("ambiguous" if len(codes) > 1 else "missing")


def map_facts(path: Any = None) -> Dict[str, Any]:
    """给读接口用的映射事实（Spec §C3）：读不到**不抛**，但也不装作"没有映射"。

    返回 `{"available", "entries", "source", "fingerprint", "unavailable"}`；
    `unavailable` 非空时带 `{code, message}`。
    """
    try:
        entries, source, fingerprint = read_material_map(path)
    except MaterialMapError as exc:
        return {"available": False, "entries": [], "source": "", "fingerprint": "",
                "unavailable": {"code": exc.code, "message": str(exc)}}
    return {"available": True, "entries": entries, "source": source,
            "fingerprint": fingerprint, "unavailable": {}}


__all__ = ["ENGINE_VERSION", "MATERIAL_MAP_FILENAME", "RULE_SET", "ENV_MATERIAL_MAP_PATH",
           "MATERIAL_MAP_ERROR_CODE", "LOOKUP_STATUSES", "MaterialMapError", "map_path",
           "default_map_path", "normalize_material_text", "read_material_map",
           "lookup_material_code", "map_facts"]

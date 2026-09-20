"""图层规则配置：读取 / 校验 / 版本指纹（DWG 第 4 批 Spec §4）。

三条硬口径：
  1. 颜色与线型的含义**只能**来自客户模板配置；默认模板的 colors / line_types 必须为空，
     代码里不许硬编码任何「颜色 N = 刀线」的表；
  2. `unknown` 只能由「没有规则命中」产生，不许在配置里声明；
  3. 配置缺失 / JSON 非法 / 模板不存在 → `PACKAGING_LAYER_RULES_INVALID`，
     **不许**静默回落到内置兜底规则。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..file_preflight import FileCapabilityError
from . import model

DEFAULT_RULES_FILENAME = "packaging_layer_rules.json"
ENV_RULES_PATH = "PACKAGING_LAYER_RULES_PATH"
ENV_TEMPLATE = "PACKAGING_LAYER_TEMPLATE"
RULES_ERROR_CODE = "PACKAGING_LAYER_RULES_INVALID"


class LayerRulesError(FileCapabilityError):
    """规则配置不可用。错误文案里只给用户可读的中文，不带路径 / 堆栈（Spec §9）。"""

    def __init__(self, message: str):
        super().__init__(RULES_ERROR_CODE, detected={}, message=message)


def _package_root() -> Path:
    # …/tech_app/backend/services/packaging_semantics/rules.py → 仓库根
    return Path(__file__).resolve().parents[4]


def default_rules_path() -> Path:
    return _package_root() / "tech_app" / "agent_knowledge" / "rules" / DEFAULT_RULES_FILENAME


def rules_path() -> Tuple[Path, str]:
    """返回 (路径, 来源)。来源闭集：default（仓库内置）/ override（env 覆盖）。"""
    override = str(os.environ.get(ENV_RULES_PATH) or "").strip()
    if override:
        return Path(override), "override"
    return default_rules_path(), "default"


def read_rules_file(path: Path) -> Tuple[Dict[str, Any], str]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise LayerRulesError("包装图纸图层规则配置缺失或无法读取，请联系系统管理员") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise LayerRulesError("包装图纸图层规则配置不是合法 JSON，请联系系统管理员") from exc
    if not isinstance(data, dict):
        raise LayerRulesError("包装图纸图层规则配置格式不正确，请联系系统管理员")
    return data, model.sha256_hex(raw)[:12]


def _clean_role(value: Any, where: str) -> str:
    role = str(value or "").strip()
    if role not in model.ROLES:
        raise LayerRulesError("图层规则使用了未定义的角色：%s" % where)
    if role == "unknown":
        raise LayerRulesError("图层规则不许声明 unknown 角色（只能由未命中产生）：%s" % where)
    return role


def _normalize_template(name: str, conf: Any) -> Dict[str, Any]:
    if not isinstance(conf, dict):
        raise LayerRulesError("图层规则模板 %s 结构不正确" % name)

    layers = []
    for index, rule in enumerate(conf.get("layers") or []):
        if not isinstance(rule, dict):
            raise LayerRulesError("图层规则模板 %s 的第 %d 条规则结构不正确" % (name, index + 1))
        match = rule.get("match")
        if not isinstance(match, dict):
            raise LayerRulesError("图层规则模板 %s 的第 %d 条规则缺少 match" % (name, index + 1))
        role = _clean_role(rule.get("role"), "rule#%d" % (index + 1))
        level = str(rule.get("evidence_level") or "MODERATE").strip().upper()
        if level not in model.EVIDENCE_LEVELS or level == "NONE":
            raise LayerRulesError("图层规则模板 %s 的第 %d 条证据强度不正确" % (name, index + 1))
        layers.append({
            "rule_id": str(rule.get("rule_id") or "").strip() or "rule_%d" % (index + 1),
            "role": role,
            "evidence_level": level,
            "confidence": model.cap_confidence("inferred_from_geometry",
                                               rule.get("confidence") or 0.0),
            "names": [str(item).strip() for item in (match.get("names") or []) if str(item).strip()],
            "name_prefix": [str(item).strip() for item in (match.get("name_prefix") or [])
                            if str(item).strip()],
        })

    colors = {}
    for key, value in (conf.get("colors") or {}).items():
        role = _clean_role(value, "colors[%s]" % key)
        colors[str(key).strip()] = role

    line_types = {}
    for key, value in (conf.get("line_types") or {}).items():
        role = _clean_role(value, "line_types[%s]" % key)
        line_types[str(key).strip().upper()] = role

    return {"layers": layers, "colors": colors, "line_types": line_types}


def validate_rule_set(data: Any) -> Dict[str, Any]:
    """校验并归一化规则快照；任何一处不合格都抛 `PACKAGING_LAYER_RULES_INVALID`。"""
    if not isinstance(data, dict):
        raise LayerRulesError("包装图纸图层规则配置格式不正确，请联系系统管理员")
    templates = data.get("templates")
    if not isinstance(templates, dict) or not templates:
        raise LayerRulesError("包装图纸图层规则配置里没有任何模板")
    normalized_templates = {str(name): _normalize_template(str(name), conf)
                            for name, conf in templates.items()}
    default_template = str(data.get("default_template") or "").strip()
    if not default_template or default_template not in normalized_templates:
        raise LayerRulesError("图层规则配置的 default_template 不在模板清单里")
    return {
        "rule_set": str(data.get("rule_set") or "").strip(),
        "review_status": str(data.get("review_status") or "").strip(),
        "generated_at": str(data.get("generated_at") or "").strip(),
        "default_template": default_template,
        "templates": normalized_templates,
    }


def resolve_rule_set(rules: Any = None) -> Dict[str, Any]:
    """解析调用方给的规则来源。

    返回 `{"rule_set", "rules_version", "rules_path", "path_kind"}`：
      · `rules` 为 None → 读默认路径（可用 `PACKAGING_LAYER_RULES_PATH` 覆盖）；
      · `rules` 为 dict → 请求级注入，**不读仓库文件**（红测 B5 依赖这一点）；
      · `rules` 为路径 → 读该文件。
    """
    if rules is None or isinstance(rules, (str, Path)):
        if isinstance(rules, (str, Path)):
            path, kind = Path(rules), "override"
        else:
            path, kind = rules_path()
        raw, version = read_rules_file(path)
        rule_set = validate_rule_set(raw)
        return {"rule_set": rule_set, "rules_version": version,
                "rules_path": str(path), "path_kind": kind}
    rule_set = validate_rule_set(rules)
    version = model.sha256_hex(model.canonical_json(rule_set))[:12]
    return {"rule_set": rule_set, "rules_version": version,
            "rules_path": "", "path_kind": "inline"}


def resolve_template(rule_set: Dict[str, Any],
                     template: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """选模板：显式参数 > `PACKAGING_LAYER_TEMPLATE` > 配置里的 default_template。"""
    name = str(template or os.environ.get(ENV_TEMPLATE) or
               rule_set.get("default_template") or "").strip()
    templates = rule_set.get("templates") or {}
    conf = templates.get(name)
    if not isinstance(conf, dict):
        raise LayerRulesError("图层规则模板 %s 不存在" % (name or "<空>"))
    return name, conf


def capability() -> Dict[str, Any]:
    """Spec §2：可观测的配置状态。**绝不抛异常**。"""
    path, kind = rules_path()
    out = {"available": False, "rules_path": str(path), "rules_version": "",
           "template": "", "model_assist": str(os.environ.get("PACKAGING_SEMANTICS_MODEL")
                                               or "auto").strip() or "auto",
           "message": "", "rules_path_kind": kind}
    try:
        raw, version = read_rules_file(path)
        rule_set = validate_rule_set(raw)
        name, _conf = resolve_template(rule_set)
        out["rules_version"] = version
        out["template"] = name
        out["available"] = True
        out["message"] = "包装图纸图层规则已加载"
    except FileCapabilityError as exc:
        out["message"] = str(getattr(exc, "message", "") or "")
    except Exception:  # noqa: BLE001 - capability 是只读探测，绝不向外抛
        out["message"] = "包装图纸图层规则不可用"
    return out

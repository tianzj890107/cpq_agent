"""零件级结果版本粒度：把「整份 IR 哈希」判过期收窄到**逐件指纹**。

要点（见 docs/specs/tech-per-part-result-staleness.md）：

  - **形状指纹** `part_fingerprint(part)`：只由「决定实体形状」的内容决定 ——
    `part_id` + `features` 的顺序 / 类型 / 数值。改 `name` / `quantity` / `model_no` /
    `tolerance_general` / `role` / 备注都不影响它；
  - **属性指纹** `part_attribute_fingerprint(part)`：本批只覆盖 `material.spec` 与
    `material.density` —— 它们决定结果条目里的 `mass_g`（体积 × 密度）。形状没变、
    派生数值会变，只有这一项。

两个函数都是纯函数（不读盘、不写库、不认识 HTTP），写入方（`/generate` `/drawings`
`regenerate`）与读时装饰（`GET /api/projects/{id}`）共用同一份实现 —— 判据只有一份，
不会出现「写入用一种算法、读的时候用另一种」的漂移。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

#: 参与形状的数值字段（白名单）。`purpose` 之类的说明文字不算形状：写清用途不该让
#: 已生成的 STEP / STL 作废。
SHAPE_FIELDS = (
    "length", "width", "thickness", "height", "diameter", "radius", "distance",
    "x", "y", "count_x", "count_y", "spacing_x", "spacing_y",
)


def _as_dict(part: Any) -> dict:
    if isinstance(part, dict):
        return part
    dump = getattr(part, "model_dump", None)
    if callable(dump):
        return dump()
    return {}


def _number(value: Any) -> Any:
    """数值统一成同一种写法：60 与 60.0、'60' 必须得到同一个指纹。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return repr(float(value))
    text = str(value).strip()
    if not text:
        return None
    try:
        return repr(float(text))
    except ValueError:
        return text


def _feature(feature: Any) -> dict:
    data = _as_dict(feature)
    kind = data.get("type")
    out: dict[str, Any] = {"type": str(getattr(kind, "value", kind) or "").strip().lower()}
    for name in SHAPE_FIELDS:
        if name in data:
            out[name] = _number(data.get(name))
    return out


def _digest(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def part_fingerprint(part: Any) -> str:
    """形状指纹：形状一样的零件（同一个 part_id）必须得到同一个串，非空。"""
    data = _as_dict(part)
    return _digest({
        "part_id": str(data.get("part_id") or "").strip(),
        "features": [_feature(feature) for feature in (data.get("features") or [])],
    })


def part_attribute_fingerprint(part: Any) -> str:
    """属性指纹：本批 = 材料（spec / density），它决定条目里的 mass_g。"""
    data = _as_dict(part)
    material = data.get("material")
    if isinstance(material, str):
        spec, density = material.strip(), None
    elif isinstance(material, dict):
        spec = str(material.get("spec") or "").strip()
        density = material.get("density")
    else:
        spec, density = "", None
    return _digest({"material_spec": spec, "material_density": _number(density)})


def generated_at() -> str:
    """结果生成时间（ISO 串，带本地时区偏移）。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def stamp_entry(entry: dict, part: Any) -> dict:
    """给结果条目写来源指纹：形状 / 属性 / 生成时间。就地改写并返回同一个 dict。"""
    entry["source_part_hash"] = part_fingerprint(part)
    entry["source_attr_hash"] = part_attribute_fingerprint(part)
    entry["generated_at"] = generated_at()
    return entry

# -*- coding: utf-8 -*-
"""通用演示输入装配：图纸几何 + 相似度推荐 → 逐件的开料尺寸 / 材料 / 单价 / 工时。

用途：让"零件拆出来了但成本算不出来"这条路在不伪造**确认**的前提下能跑通。
它不是盒型专用脚本、也不认任何一份图纸的名字：尺寸来自这一件绑定的几何分量，
材料来自这一件的图纸材料原文（缺失时按件名关键词**推定并披露**），
单价优先取库内材料价、取不到才按材质族给演示价，**每一条 mock 都写在结论里**。

三条纪律：
  · 不伪造人工确认：写的是 `poc_inputs`（演示输入），**不写** `confirmed_size`；
  · 不针对单一图纸：没有任何"某一份图纸 / 某一个件名"的硬编码分支，换一份图同样走这套；
  · 只读 + 显式落库：`build()` 是纯读，`prepare()` 才写，且写的是业务部件文档的新版本。
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from . import packaging_material_match as material_match

ENGINE_VERSION = "packaging-demo-inputs/1"

#: 件名关键词 → 材料族（**仅用于图纸没写材料时推定**，且必须随结论披露）。通用规则，不含图号。
NAME_FAMILY_HINTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("greyboard", ("灰板", "衬板", "纸板", "盒背", "背板", "底板", "隔板")),
    ("face_paper", ("面纸", "封面", "铜版", "印刷面")),
    ("liner_paper", ("衬纸", "内衬", "包边", "内卡")),
    ("specialty", ("特种", "充皮", "触感")),
    ("eva", ("EVA", "内托", "泡棉")),
    ("magnet", ("磁铁", "磁石", "磁片")),
    ("tube", ("纸管", "圆管")),
)

#: 材料原文与件名都推不出克重时的族默认（g/㎡）：只在推导链全断时用，逐条披露。
FAMILY_DEFAULT_GRAMMAGE: Dict[str, float] = {
    "greyboard": 0.0, "face_paper": 157.0, "white_card": 300.0, "liner_paper": 80.0,
    "specialty": 120.0, "corrugated": 300.0, "tube": 250.0, "eva": 0.0,
}
#: 灰板这类按厚度计的材料：推不出克重时给族默认厚度（mm），配合密度换算。
FAMILY_DEFAULT_THICKNESS: Dict[str, float] = {"greyboard": 2.0, "eva": 10.0}

#: 演示排模/校版口径（非生产参数）：认不到时给这两个值，并逐条披露。
DEMO_IMPOSITION = 1.0
DEMO_PROOF_BASE = 50.0
DEMO_LABOR_RATE = 36.0
DEMO_STANDARD_SECONDS = 30.0
DEMO_OVERHEAD_SECONDS = 0.0
DEMO_OVERHEAD_RATE = 0.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def geometry_sizes(parts_doc: Any) -> Dict[str, Dict[str, Any]]:
    """几何零件文档 → `{component_id: {length_mm, width_mm, size_source}}`（纯读、纯函数）。

    件清单尺寸（`unfolded_length_mm/width_mm`，闭合轮廓环算出来的）优先，缺失才退回 `bbox`
    —— 与 `packaging_parts._component_size()` 同一条口径，这里不另立一套。
    """
    doc = parts_doc if isinstance(parts_doc, dict) else {}
    out: Dict[str, Dict[str, Any]] = {}
    for row in (doc.get("parts") or []):
        if not isinstance(row, dict):
            continue
        code = _text(row.get("component_id"))
        if not code:
            continue
        length = _num(row.get("unfolded_length_mm"))
        width = _num(row.get("unfolded_width_mm"))
        source = "unfolded_size"
        if not (length and width):
            bbox = row.get("bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                try:
                    length = abs(float(bbox[2]) - float(bbox[0]))
                    width = abs(float(bbox[3]) - float(bbox[1]))
                    source = "component_bbox"
                except (TypeError, ValueError):
                    length = width = None
        if length and width and length > 0 and width > 0:
            out[code] = {"length_mm": round(length, 3), "width_mm": round(width, 3),
                         "size_source": source}
    return out


def _component_ids(row: Any) -> List[str]:
    record = row if isinstance(row, dict) else {}
    binding = record.get("geometry_binding") if isinstance(record.get("geometry_binding"), dict) else {}
    ids = [_text(item) for item in (binding.get("component_ids") or []) if _text(item)]
    if ids:
        return ids
    for section in (record.get("sections") or []):
        if isinstance(section, dict):
            ids.extend(_text(item) for item in (section.get("component_ids") or []) if _text(item))
    return ids


def part_size(row: Any, sizes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """一件的开料长宽：按绑定分量的**逐轴最大值**取包络（多分量件因此能给整件尺寸）。

    取不到就返回空 —— 不猜、不给默认值。`component_ids` 一并带回，供结论披露。
    """
    ids = _component_ids(row)
    lengths, widths, sources = [], [], []
    for code in ids:
        item = sizes.get(code) or {}
        length, width = _num(item.get("length_mm")), _num(item.get("width_mm"))
        if length and width:
            lengths.append(length)
            widths.append(width)
            sources.append(_text(item.get("size_source")))
    if not lengths:
        return {"length_mm": None, "width_mm": None, "size_source": "", "component_ids": ids}
    return {"length_mm": round(max(lengths), 3), "width_mm": round(max(widths), 3),
            "size_source": sources[0] if sources else "", "component_ids": ids}


def material_text_of(row: Any) -> Dict[str, Any]:
    """这一件的材料原文与来源：图纸材料原文 → 组成材料原文 → 按件名推定（披露）。"""
    record = row if isinstance(row, dict) else {}
    reference = record.get("reference") if isinstance(record.get("reference"), dict) else {}
    for key in ("reference", "authority"):
        block = record.get(key) if isinstance(record.get(key), dict) else {}
        text = _text(block.get("material_text"))
        if text:
            return {"material_text": text, "source": "drawing_text" if key == "reference" else key}
    for section in (record.get("sections") or []):
        if isinstance(section, dict) and _text(section.get("material_text")):
            return {"material_text": _text(section.get("material_text")), "source": "drawing_text"}
    inferred = infer_material_text(_text(record.get("name")))
    if inferred:
        return {"material_text": inferred, "source": "inferred_from_part_name"}
    return {"material_text": _text(reference.get("material_text")), "source": ""}


def infer_material_text(name: Any) -> str:
    """件名 → 材料原文（图纸没写材料时的**推定**，通用关键词规则，不含图号/客户名）。"""
    text = _text(name).upper()
    if not text:
        return ""
    for family, words in NAME_FAMILY_HINTS:
        if any(word.upper() in text for word in words):
            if family == "greyboard":
                return "%smm灰板" % FAMILY_DEFAULT_THICKNESS.get("greyboard", 2.0)
            if family == "eva":
                return "EVA"
            if family == "magnet":
                return "磁铁"
            if family == "tube":
                return "纸管"
            if family == "face_paper":
                return "面纸"
            if family == "liner_paper":
                return "衬纸"
            return "特种纸"
    return ""


def _kb_materials() -> List[Dict[str, Any]]:
    try:
        from ..storage import kb_repo
        return [dict(row) for row in kb_repo.list_materials(industry="packaging")]
    except Exception:                                                # noqa: BLE001 - 库不可用就退演示价
        return []


def _kb_price(material_code: str) -> Dict[str, Any]:
    if not material_code:
        return {}
    try:
        from ..storage import kb_repo
        row = kb_repo.current_price(material_code, industry="packaging") or {}
    except Exception:                                                # noqa: BLE001
        return {}
    price = _num(row.get("price"))
    unit = _text(row.get("unit"))
    if price is None:
        return {}
    if unit.lower() == "kg":
        return {"unit_price": price, "unit": unit, "source": "kb_material_price",
                "price_id": _text(row.get("price_id"))}
    return {"unit_price": None, "unit": unit, "source": "kb_material_price_unit_mismatch",
            "price_id": _text(row.get("price_id"))}


def _kb_tax_factor() -> float:
    try:
        from ..storage import kb_repo
        for row in kb_repo._table("kb_cost_factor"):
            if _text(row.get("factor_code")) == "F-PKG-TAX-VAT":
                value = _num(row.get("value"))
                return 1.0 + value if value is not None else 1.13
    except Exception:                                                # noqa: BLE001
        pass
    return 1.13


def demo_inputs_for(row: Any, sizes: Dict[str, Dict[str, Any]], *,
                    materials: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """一件的演示输入（纯函数，不写库）。`ok=False` 时 `reason` 说清缺什么。"""
    record = row if isinstance(row, dict) else {}
    materials = materials if materials is not None else _kb_materials()
    size = part_size(record, sizes)
    material = material_text_of(record)
    text = _text(material.get("material_text"))
    if not (size["length_mm"] and size["width_mm"]):
        return {"ok": False, "reason": "no_geometry_size",
                "message": "这一件没有可用的几何尺寸（未绑定分量），演示输入给不出开料长宽",
                "material_text": text, "component_ids": size["component_ids"]}
    recommendation = material_match.recommend_materials(text, materials) if text else {
        "status": "no_candidate", "candidate": None, "candidates": [], "confidence": 0.0}
    candidate = recommendation.get("candidate") or {}
    price = _kb_price(_text(candidate.get("material_code"))) if candidate else {}
    demo_price = material_match.demo_unit_price(text or _text(record.get("name")))
    unit_price = price.get("unit_price")
    price_source = price.get("source") or ""
    if unit_price is None:
        unit_price = demo_price["unit_price"]
        price_source = demo_price["source"] if not price else price.get("source")
    grammage = material_match.derive_grammage(text, material=candidate)
    if not grammage.get("grammage"):
        family = material_match.family_of(text) or demo_price.get("family") or ""
        thickness = FAMILY_DEFAULT_THICKNESS.get(family)
        if thickness:
            grammage = {"grammage": round(thickness * demo_price["density"] * 1000, 1),
                        "source": "derived_from_family_default_thickness"}
        elif FAMILY_DEFAULT_GRAMMAGE.get(family):
            grammage = {"grammage": FAMILY_DEFAULT_GRAMMAGE[family],
                        "source": "family_default_grammage"}
    notes: List[str] = []
    notes.append("演示输入：开料长宽按这一件绑定的几何分量包络（%s），不是人工确认尺寸"
                 % (size["size_source"] or "unknown"))
    if material.get("source") == "inferred_from_part_name":
        notes.append("图纸没有这一件的材料原文，按件名关键词推定为「%s」" % text)
    if candidate:
        notes.append("材料按相似度推荐：%s → %s（相似度 %.2f，%s）"
                     % (text or record.get("name"), candidate.get("name"),
                        recommendation.get("confidence") or 0.0,
                        recommendation.get("status")))
    else:
        notes.append("材料库没有可用候选（%s），按材质族给演示单价" % recommendation.get("status"))
    if price_source == "demo_family_price":
        notes.append(demo_price["note"])
    if price_source == "kb_material_price_unit_mismatch":
        notes.append("库内这条价格的单位是「%s」，不是元/kg，本行按演示价算" % price.get("unit"))
    if not grammage.get("grammage"):
        notes.append("克重推导不出来：这一行材料费给不出金额")
    notes.append("模数 %s、校版 %s 张、工时费率 %s 元/小时、标准工时 %s 秒为演示口径"
                 % (DEMO_IMPOSITION, DEMO_PROOF_BASE, DEMO_LABOR_RATE, DEMO_STANDARD_SECONDS))
    return {
        "ok": bool(grammage.get("grammage")),
        "reason": "" if grammage.get("grammage") else "grammage_unavailable",
        "length_mm": size["length_mm"], "width_mm": size["width_mm"],
        "component_ids": size["component_ids"], "size_source": size["size_source"],
        "material_text": text, "material_source": material.get("source"),
        "recommendation": recommendation, "recommended_material": candidate,
        "grammage": grammage.get("grammage"), "grammage_source": grammage.get("source"),
        "unit_price": unit_price, "price_source": price_source,
        "note": "；".join(notes),
    }


def build(project_id: str, *, limit: int = 0) -> Dict[str, Any]:
    """纯读：算出这个项目每一件的演示输入与全项目的统计（不写任何文档）。"""
    from . import packaging_parts
    business = packaging_parts.load_business_parts(project_id) or {}
    geometry = packaging_parts.load_parts(project_id) or {}
    sizes = geometry_sizes(geometry)
    materials = _kb_materials()
    rows: List[Dict[str, Any]] = []
    for row in (business.get("business_parts") or []):
        if not isinstance(row, dict):
            continue
        if limit and len(rows) >= limit:
            break
        value = demo_inputs_for(row, sizes, materials=materials)
        value.update({"business_part_code": _text(row.get("business_part_code")),
                      "name": _text(row.get("name"))})
        rows.append(value)
    ready = [row for row in rows if row.get("ok")]
    inferred = [row for row in rows if row.get("material_source") == "inferred_from_part_name"]
    recommended = [row for row in rows if row.get("recommended_material")]
    demo_priced = [row for row in rows if row.get("price_source") == "demo_family_price"]
    return {"version": ENGINE_VERSION, "project_id": project_id,
            "business_parts_id": _text(business.get("business_parts_id")),
            "geometry_component_total": len(sizes),
            "rows": rows, "row_total": len(rows),
            "ready_total": len(ready), "no_size": [row["business_part_code"] for row in rows
                                                   if row.get("reason") == "no_geometry_size"],
            "material_inferred_total": len(inferred),
            "material_recommended_total": len(recommended),
            "demo_price_total": len(demo_priced),
            "grammage_missing": [row["business_part_code"] for row in rows
                                 if row.get("ok") is False and row.get("reason") != "no_geometry_size"]}


def prepare(project_id: str, *, actor: str = "", enable_demo: bool = False) -> Dict[str, Any]:
    """把这个项目的演示输入写进业务部件文档（新版本），并按需打开项目的 `poc_demo` 开关。

    只写两处：业务部件文档的 `poc_demo` / `poc_inputs` / `cost_parameters`，以及需求单上的
    `poc_demo` 标志（`enable_demo=True` 时）。**不写 `confirmed_size`**，不删任何历史版本。
    """
    from . import packaging_parts
    from ..storage import store

    result = build(project_id)
    business = packaging_parts.load_business_parts(project_id) or {}
    if not business.get("business_parts"):
        raise ValueError("这个项目还没有业务部件清单（先跑图纸解析）")
    by_code = {row["business_part_code"]: row for row in result["rows"]}
    doc = copy.deepcopy(business)
    doc["poc_demo"] = True
    doc["demo_inputs_version"] = ENGINE_VERSION
    tax_factor = _kb_tax_factor()
    written, skipped = 0, []
    for row in (doc.get("business_parts") or []):
        if not isinstance(row, dict):
            continue
        code = _text(row.get("business_part_code"))
        value = by_code.get(code)
        if not value or not value.get("ok"):
            skipped.append({"business_part_code": code,
                            "reason": (value or {}).get("reason") or "not_in_scope"})
            continue
        inputs = {
            "length_mm": value.get("length_mm"), "width_mm": value.get("width_mm"), "quantity": 1,
            "material_text": value.get("material_text"),
            "gsm": value.get("grammage"),
            "ton_price": (value.get("unit_price") or 0) * 1000,
            "tax_factor": tax_factor,
            "imposition_count": DEMO_IMPOSITION, "proof_base": DEMO_PROOF_BASE,
            "mock": True, "source": "demo_similarity",
            "note": value.get("note"),
            "recommendation": {
                "material_code": (value.get("recommended_material") or {}).get("material_code"),
                "name": (value.get("recommended_material") or {}).get("name"),
                "score": (value.get("recommendation") or {}).get("confidence"),
                "status": (value.get("recommendation") or {}).get("status"),
                "grammage_source": value.get("grammage_source"),
                "price_source": value.get("price_source"),
                "material_source": value.get("material_source"),
                "component_ids": value.get("component_ids"),
                "size_source": value.get("size_source"),
            },
        }
        row["poc_inputs"] = inputs
        row["usage_qty"] = 1
        row["cost_parameters"] = {
            "labor_rate": DEMO_LABOR_RATE, "labor_seconds": DEMO_STANDARD_SECONDS,
            "standard_seconds": DEMO_STANDARD_SECONDS,
            "overhead_seconds": DEMO_OVERHEAD_SECONDS, "overhead_rate": DEMO_OVERHEAD_RATE,
        }
        written += 1
    saved = packaging_parts.save_business_parts(project_id, doc)
    if enable_demo:
        requirement = copy.deepcopy(store.load_requirement(project_id) or {})
        data = requirement.get("data") if isinstance(requirement.get("data"), dict) else {}
        data["poc_demo"] = True
        requirement["data"] = data
        store.save_requirement(project_id, requirement, author=actor or "system")
    store.audit(project_id, "poc:demo_inputs_prepared",
                {"by": actor or "system", "version": ENGINE_VERSION, "written": written,
                 "material_recommended_total": result["material_recommended_total"],
                 "material_inferred_total": result["material_inferred_total"],
                 "demo_price_total": result["demo_price_total"], "mock": True})
    return {"version": ENGINE_VERSION, "project_id": project_id,
            "business_parts_id": _text(saved.get("business_parts_id")),
            "written": written, "skipped": skipped, "build": result}


__all__ = ["ENGINE_VERSION", "NAME_FAMILY_HINTS", "FAMILY_DEFAULT_GRAMMAGE",
           "FAMILY_DEFAULT_THICKNESS", "geometry_sizes", "part_size", "material_text_of",
           "infer_material_text", "demo_inputs_for", "build", "prepare"]

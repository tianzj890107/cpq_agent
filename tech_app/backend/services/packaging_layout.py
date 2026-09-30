"""CAD-derived packaging layout evidence; never a BOM or cost authority."""
from __future__ import annotations

import re
import copy
from typing import Any


_N_UP = re.compile(r"(?:各\s*)?排\s*([1-9]\d*)\s*模")
_SHEET = re.compile(r"(\d+(?:\.\d+)?)\s*[*×xX]\s*(\d+(?:\.\d+)?)\s*mm", re.I)
_SCHEME = re.compile(r"(?:内托)?方案\s*([1-9]\d*)")
_PAIRED_NAME = re.compile(r"^(.*?)(内|左|上|前)、(外|右|下|后)(.+)$")


def is_layout_instruction(value: Any) -> bool:
    """The manufacturing instruction, not its location, decides the text role."""
    return bool(_N_UP.search(str(value or "")))


def extract_layout_rows(cad_ir: Any) -> list[dict]:
    ir = cad_ir if isinstance(cad_ir, dict) else {}
    rows = []
    for text in ir.get("texts") or []:
        if not isinstance(text, dict):
            continue
        raw = str(text.get("raw_text") or text.get("normalized_text") or "")
        n_up = _N_UP.search(raw)
        if not n_up:
            continue
        sheet = _SHEET.search(raw)
        rows.append({
            "entity_id": str(text.get("entity_id") or ""),
            "raw_text": raw,
            "position": list(text.get("position") or []),
            "layer": str(text.get("layer") or ""),
            "n_up": int(n_up.group(1)),
            "sheet_mm": [float(sheet.group(1)), float(sheet.group(2))] if sheet else None,
            "confirmed": False,
            "part_codes": [],  # compound text alone cannot prove each part's allocation
            "rule_id": "cad_layout_instruction_v1",
        })
    return sorted(rows, key=lambda row: (row["entity_id"], row["raw_text"]))


def scheme_labels(cad_ir: Any) -> list[str]:
    ir = cad_ir if isinstance(cad_ir, dict) else {}
    labels = set()
    for text in ir.get("texts") or []:
        if isinstance(text, dict):
            match = _SCHEME.search(str(text.get("raw_text") or text.get("normalized_text") or ""))
            if match:
                labels.add(match.group(1))
    return sorted(labels)


def declared_sections(name: Any) -> list[dict]:
    """A text label can name two portions of one business item.

    This is textual structure only; neither portion is assigned a CAD shape.
    """
    label = str(name or "").strip()
    match = _PAIRED_NAME.match(label)
    names = ([match.group(1) + match.group(2) + match.group(4),
              match.group(1) + match.group(3) + match.group(4)] if match else [label])
    return [{"name": item, "geometry_status": "pending_attribution",
             "source": "drawing_text"} for item in names if item]


def part_fragments(component_ids: Any, components: Any) -> list[dict]:
    """Keep per-piece CAD evidence; a union bbox is not a part drawing."""
    by_id = {str(row.get("component_id") or ""): row for row in (components or [])
             if isinstance(row, dict)}
    result = []
    for component_id in dict.fromkeys(str(cid) for cid in (component_ids or []) if cid):
        row = by_id.get(component_id)
        if row is None:
            continue
        bbox = row.get("bbox")
        if not isinstance(bbox, (list, tuple)):
            outline = row.get("outline") if isinstance(row.get("outline"), dict) else {}
            bbox = outline.get("bbox")
        result.append({
            "component_id": component_id,
            "entity_ids": list(row.get("entity_ids") or []),
            "bbox": list(bbox[:4]) if isinstance(bbox, (list, tuple)) and len(bbox) >= 4 else None,
            "layers": list(row.get("layers") or []),
        })
    return result


def set_layout_confirmation(doc: Any, entity_id: str, confirmed: bool) -> dict:
    """Confirm an observed instruction, not an inferred yield or cost."""
    record = copy.deepcopy(doc) if isinstance(doc, dict) else {}
    rows = record.get("layout_rows") or []
    target = next((row for row in rows if row.get("entity_id") == entity_id), None)
    if target is None:
        raise ValueError("layout_entity_not_found")
    if confirmed and (not target.get("n_up") or not target.get("sheet_mm")):
        raise ValueError("layout_evidence_incomplete")
    target["confirmed"] = bool(confirmed)
    return record

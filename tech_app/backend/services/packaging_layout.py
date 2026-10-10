"""CAD-derived packaging layout evidence; never a BOM or cost authority."""
from __future__ import annotations

import re
import copy
from typing import Any


_N_UP = re.compile(r"(?:各\s*)?排\s*([1-9]\d*)\s*模")
_SHEET = re.compile(r"(\d+(?:\.\d+)?)\s*[*×xX]\s*(\d+(?:\.\d+)?)\s*mm", re.I)
_SCHEME = re.compile(r"(?:内托)?方案\s*([1-9]\d*)")
_PAIRED_NAME = re.compile(r"^(.*?)(内|左|上|前)、(外|右|下|后)(.+)$")


def clean_cad_text(raw: Any) -> str:
    text = str(raw or "").replace(r"\P", "\n").replace(r"\~", " ")
    text = re.sub(r"\\[CcFfHhWwQqTtAa][^;]*;", "", text)
    return re.sub(r"[{}]|\\[LlOoKk]", "", text).strip()


def layout_record(entity_id: str, raw: str) -> dict:
    text = clean_cad_text(raw)
    n_up, sheet = _N_UP.search(text), _SHEET.search(text)
    return {"entity_id": entity_id, "raw_text": raw, "text": text,
            "n_up": int(n_up.group(1)) if n_up else None,
            "sheet_mm": [float(sheet.group(1)), float(sheet.group(2))] if sheet else None,
            "confirmed": False, "part_codes": [], "rule_id": "cad_layout_instruction_v1"}


def is_layout_instruction(value: Any) -> bool:
    """The manufacturing instruction, not its location, decides the text role."""
    return bool(_N_UP.search(clean_cad_text(value)))


def extract_layout_rows(cad_ir: Any) -> list[dict]:
    ir = cad_ir if isinstance(cad_ir, dict) else {}
    rows = []
    for text in ir.get("texts") or []:
        if not isinstance(text, dict):
            continue
        raw = str(text.get("raw_text") or text.get("normalized_text") or "")
        clean = clean_cad_text(raw)
        n_up = _N_UP.search(clean)
        if not n_up:
            continue
        sheet = _SHEET.search(clean)
        rows.append({
            **layout_record(str(text.get("entity_id") or ""), raw),
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


def auto_assign_layout_rows(rows, parts):
    """Full CAD names only. Association never supplies yield or approval."""
    result = copy.deepcopy(rows or [])
    names = sorted([(str(part.get('name') or '').strip(),part.get('business_part_code'))
                    for part in parts or [] if part.get('business_part_code')],
                   key=lambda item: -len(item[0]))
    for row in result:
        if row.get('assignment_source') == 'user_selection' or row.get('part_codes'):
            continue
        text = clean_cad_text(row.get('text') or row.get('raw_text'))
        occupied, codes, evidence = set(), [], []
        for name, code in names:
            if not name:
                continue
            for match in re.finditer(re.escape(name),text):
                positions = set(range(match.start(),match.end()))
                if positions & occupied:
                    continue
                occupied.update(positions)
                if code not in codes:
                    codes.append(code)
                    evidence.append({'part_code':code,'name':name,'entity_id':row.get('entity_id'),
                                     'text':text,'source':'cad_layout_full_name'})
        if codes:
            row.update(part_codes=codes,assignment_source='cad_layout_full_name',
                       assignment_evidence=evidence)
    return result


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


def set_layout_assignment(doc: Any, entity_id: str, part_codes: list, *, actor: str) -> dict:
    """Attach an observed layout to real business parts, without deriving consumption."""
    record = copy.deepcopy(doc) if isinstance(doc, dict) else {}
    known = {row.get("business_part_code") for row in record.get("business_parts") or []
             if isinstance(row, dict)}
    codes = list(dict.fromkeys(part_codes))
    if not actor or any(code not in known for code in codes):
        raise ValueError("layout_assignment_invalid")
    target = next((row for row in record.get("layout_rows") or []
                   if row.get("entity_id") == entity_id), None)
    if target is None:
        raise ValueError("layout_entity_not_found")
    target["part_codes"] = codes
    target["assignment_actor"] = actor
    target["assignment_source"] = "user_selection"
    # Association is not approval of sheet size, yield, usage or price.
    target["confirmed"] = False
    return record

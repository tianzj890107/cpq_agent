# -*- coding: utf-8 -*-
"""件名 → 材料原文的推定（唯一实现；演示输入与真实工艺/成本门禁共用）。

Spec：`docs/specs/packaging-part-material-inference.md`。

图纸和对照表都没写材料时，件名里的材料族关键词是唯一可用的线索。这里只做**通用规则**：
不含图号、客户名或某一份图纸的硬编码；推不出来就返回空串，由调用方按缺口处理。
推定结果必须带来源（`MATERIAL_INFERENCE_SOURCE`）并写进结论，不得伪装成图纸原文。
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

MATERIAL_INFERENCE_SOURCE = "inferred_from_part_name"

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

#: 灰板这类按厚度计的材料：推不出克重时给族默认厚度（mm），配合密度换算。
FAMILY_DEFAULT_THICKNESS: Dict[str, float] = {"greyboard": 2.0, "eva": 10.0}


def infer_material_text(name: Any) -> str:
    """件名 → 材料原文（图纸没写材料时的**推定**，通用关键词规则，不含图号/客户名）。"""
    from .packaging_part_route_match import normalize_part_name
    text = normalize_part_name(name).upper()
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

#: 按厚度计价材料的族默认密度（g/cm³）：只在材料原文只写厚度、库内又没有密度时用，逐条披露。
FAMILY_DEFAULT_DENSITY: Dict[str, float] = {"greyboard": 0.7, "eva": 0.9}

#: 只写材料族、既没克重也没厚度时的族默认克重（g/㎡）：逐条披露，不装成原文。
FAMILY_DEFAULT_GRAMMAGE: Dict[str, float] = {"liner_paper": 80.0, "specialty": 120.0,
                                             "tube": 250.0}

GRAMMAGE_SOURCE_TEXT = "material_text"
GRAMMAGE_SOURCE_THICKNESS = "inferred_thickness_density"
GRAMMAGE_SOURCE_FAMILY = "inferred_family_default"


def material_family(text: Any) -> str:
    """材料原文 / 件名命中的材料族（认不出返回空串）。"""
    from .packaging_part_route_match import normalize_part_name
    body = normalize_part_name(text).upper()
    if not body:
        return ""
    for family, words in NAME_FAMILY_HINTS:
        if any(word.upper() in body for word in words):
            return family
    return ""


def infer_grammage(material_text: Any):
    """材料原文 → `(克重, 来源)`；推不出来 `(None, "")`。

    顺序：原文里的 `<数字>g` → 厚度 × 族默认密度 → 族默认克重。后两步都是**推定**，
    来源必须随结论披露（Spec `packaging-part-material-inference.md`）。
    """
    import re
    text = str(material_text or "")
    match = re.search(r"(\d+(?:\.\d+)?)\s*[gG](?![A-Za-z])", text)
    if match:
        return float(match.group(1)), GRAMMAGE_SOURCE_TEXT
    family = material_family(text)
    thickness = re.search(r"(\d+(?:\.\d+)?)\s*(?:mm|毫米)", text, re.I)
    if family in FAMILY_DEFAULT_DENSITY and thickness:
        gsm = float(thickness.group(1)) * FAMILY_DEFAULT_DENSITY[family] * 1000.0
        return round(gsm, 3), GRAMMAGE_SOURCE_THICKNESS
    if family in FAMILY_DEFAULT_GRAMMAGE:
        return FAMILY_DEFAULT_GRAMMAGE[family], GRAMMAGE_SOURCE_FAMILY
    return None, ""

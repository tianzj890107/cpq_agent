# -*- coding: utf-8 -*-
"""材料原文 → 库内材料的**相似度推荐**（通用能力，不针对任何单一图纸）。

为什么需要它：客户原文（`225G铜版底PET光银` / `2mm灰板` / `35mm厚EVA`）几乎不可能与
`kb_material.name` 逐字相同，既有分词包含规则也接不上；"一件都认不出"会让材料费恒为 0。
这里给出唯一入口：特征抽取（克重 / 厚度 / 材质族 / 字面相似度）→ 打分 → 候选清单。

纪律（与 `packaging_part_route_match` / `packaging_material_map` 同一条）：
  · 纯函数：不读库、不联网、不调模型、不改入参；候选空间由调用方给定，本模块不自取候选；
  · **不许静默取最高分**：低于阈值报 `below_threshold`，前两名太接近报 `ambiguous`；
  · 相似度是**建议不是确认**：返回体里 `approved` 恒为 False，采纳与否由调用方留痕；
  · 演示单价只在该族没有库内价时兜底，且必须随结论披露（`price_source`）。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Tuple

ENGINE_VERSION = "packaging-material-match/1"

#: 结论闭集。`exact_name` 是逐字命中，其余都是"推荐"。
MATCH_STATUSES = ("exact_name", "similarity", "ambiguous", "below_threshold", "no_candidate")
MATCH_THRESHOLD = 0.45
AMBIGUOUS_MARGIN = 0.06
CANDIDATE_LIMIT = 5

#: 材质族 → 关键词。族相同/不同是打分里权重最大的一项（客户原文与库内名的唯一稳定交集）。
FAMILY_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("greyboard", ("灰板", "双灰", "灰纸板", "工业纸板", "纸板")),
    ("face_paper", ("铜版", "光银", "哑银", "面纸", "涂布", "转移膜", "镭射", "PET")),
    ("white_card", ("白卡", "白板", "卡纸", "双铜", "单铜")),
    ("liner_paper", ("衬纸", "牛皮", "本白", "内衬", "包边")),
    ("specialty", ("特种", "充皮", "触感", "布纹", "皮纹", "星幻")),
    ("eva", ("EVA", "泡棉", "海绵")),
    ("magnet", ("磁铁", "磁石", "磁片")),
    ("tube", ("纸管", "圆管", "纸筒")),
    ("corrugated", ("瓦楞", "坑纸", "瓦坑")),
)

#: 演示单价（元/kg）与体积密度（g/cm³）：只在该族**没有库内单价**时兜底。
#: 演示价不是正式费率，出价前必须换成权威口径（结论里逐条披露）。
DEMO_PRICING: Dict[str, Tuple[float, float]] = {
    "greyboard": (6.5, 0.75), "face_paper": (12.8, 1.25), "white_card": (11.5, 1.10),
    "liner_paper": (9.6, 0.85), "specialty": (18.0, 1.05), "eva": (25.0, 0.95),
    "corrugated": (5.2, 0.60), "tube": (9.0, 0.70),
}
DEMO_PRICING_DEFAULT = (12.0, 1.0)

_GRAMMAGE_RE = re.compile(r"(\d{2,4})\s*(?:G|克|GSM)")
_THICKNESS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:MM|毫米)")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


def _flatten(value: Any) -> str:
    return _text(value).upper().replace(" ", "").replace("（", "(").replace("）", ")")


def material_features(text: Any) -> Dict[str, Any]:
    """一条材料原文的特征：克重 / 厚度 / 材质族 / 归一化字面。纯函数。"""
    raw = _text(text)
    flat = _flatten(raw)
    grammage = None
    match = _GRAMMAGE_RE.search(flat)
    if match:
        grammage = _number(match.group(1))
    thickness = None
    match = _THICKNESS_RE.search(flat)
    if match:
        thickness = _number(match.group(1))
    families = []
    for family, words in FAMILY_KEYWORDS:
        if any(word.upper() in flat for word in words):
            families.append(family)
    return {"text": raw, "normalized": flat, "grammage": grammage,
            "thickness_mm": thickness, "families": sorted(families)}


def _candidate_text(candidate: Any) -> str:
    row = candidate if isinstance(candidate, dict) else {}
    parts = [_text(row.get("name")), _text(row.get("grade")), _text(row.get("spec"))]
    return " ".join(part for part in parts if part)


def score_candidate(query: Any, candidate: Any) -> Tuple[float, List[str]]:
    """一条候选的相似度与理由（0~1）。族相同权重最高，其次克重/厚度，最后字面相似。"""
    left = material_features(query) if not isinstance(query, dict) else query
    text = _candidate_text(candidate)
    right = material_features(text) if not isinstance(query, dict) else material_features(text)
    reasons: List[str] = []
    left_families = set(left.get("families") or ())
    right_families = set(right.get("families") or ())
    if left_families and right_families:
        shared = left_families & right_families
        family = 1.0 if shared else 0.0
        reasons.append("同一材质族：" + "、".join(sorted(shared)) if shared else "材质族不同")
    else:
        family = 0.5
        if not left_families:
            reasons.append("原文没有可识别的材质族")
    size = 0.5
    left_gram, right_gram = left.get("grammage"), right.get("grammage")
    if left_gram and right_gram:
        size = min(left_gram, right_gram) / max(left_gram, right_gram)
        reasons.append("克重 %s vs %s" % (_text(left_gram), _text(right_gram)))
    else:
        left_thickness, right_thickness = left.get("thickness_mm"), right.get("thickness_mm")
        if left_thickness and right_thickness:
            size = min(left_thickness, right_thickness) / max(left_thickness, right_thickness)
            reasons.append("厚度 %s vs %s" % (left_thickness, right_thickness))
    literal = SequenceMatcher(None, _text(left.get("normalized")),
                              _text(right.get("normalized"))).ratio()
    score = 0.5 * family + 0.25 * size + 0.25 * literal
    return round(score, 4), reasons


def rank_materials(query: Any, materials: Iterable[Any], *, limit: int = CANDIDATE_LIMIT) -> List[Dict[str, Any]]:
    """按相似度给候选排序（不改入参、不筛掉低分——低分由结论状态表达）。"""
    features = material_features(query) if not isinstance(query, dict) else query
    ranked: List[Dict[str, Any]] = []
    for row in materials or ():
        if not isinstance(row, dict):
            continue
        code = _text(row.get("material_code"))
        if not code:
            continue
        score, reasons = score_candidate(features, row)
        ranked.append({"material_code": code, "name": _text(row.get("name")),
                       "grade": _text(row.get("grade")), "spec": _text(row.get("spec")),
                       "score": score, "reasons": reasons})
    ranked.sort(key=lambda item: (-item["score"], item["material_code"]))
    return ranked[:max(1, int(limit))] if limit else ranked


def recommend_materials(query: Any, materials: Iterable[Any], *,
                        threshold: float = MATCH_THRESHOLD,
                        margin: float = AMBIGUOUS_MARGIN,
                        limit: int = CANDIDATE_LIMIT) -> Dict[str, Any]:
    """材料原文 → 候选与结论（`exact_name` / `similarity` / `ambiguous` / `below_threshold`
    / `no_candidate`）。**只推荐，不代替人签字**：`approved` 恒为 False。"""
    features = material_features(query)
    text = features["text"]
    ranked = rank_materials(features, materials, limit=limit)
    exact = [item for item in ranked if _flatten(item["name"]) == features["normalized"]]
    status, chosen = "no_candidate", None
    if exact:
        status, chosen = "exact_name", exact[0]
    elif ranked:
        top = ranked[0]
        if top["score"] < float(threshold):
            status = "below_threshold"
        elif len(ranked) > 1 and (top["score"] - ranked[1]["score"]) < float(margin):
            status = "ambiguous"
            chosen = top
        else:
            status, chosen = "similarity", top
    return {"version": ENGINE_VERSION, "query": text, "features": features,
            "status": status, "approved": False, "threshold": float(threshold),
            "confidence": (chosen or {}).get("score") if chosen else 0.0,
            "candidate": chosen, "candidates": ranked,
            "match_method": "feature_similarity"}


def family_of(query: Any) -> str:
    """原文的材质族（多族取第一个命中的），识别不出给空串。"""
    features = material_features(query)
    families = features.get("families") or []
    return families[0] if families else ""


def demo_unit_price(query: Any) -> Dict[str, Any]:
    """没有库内单价时的**演示**单价（元/kg）：按材质族给，逐条披露它只是演示。"""
    family = family_of(query)
    price, density = DEMO_PRICING.get(family, DEMO_PRICING_DEFAULT)
    return {"unit_price": float(price), "density": float(density), "family": family,
            "source": "demo_family_price",
            "note": "库内没有这一族单价，按演示费率 %s 元/kg 计（仅供流程演示）" % price}


def derive_grammage(query: Any, *, material: Any = None) -> Dict[str, Any]:
    """克重：原文里的 `<数字>g` 优先；其次材料的 gsm 属性 / grade；最后厚度 × 密度推导。

    都没有时返回 `grammage=None` —— 不猜、不给默认值。
    """
    features = material_features(query)
    if features.get("grammage"):
        return {"grammage": float(features["grammage"]), "source": "from_text"}
    row = material if isinstance(material, dict) else {}
    for prop in (row.get("properties") or []):
        if _text(prop.get("prop_key")).lower() == "gsm":
            value = _number(prop.get("value_num"))
            if value:
                return {"grammage": float(value), "source": "from_material_property"}
    match = re.search(r"(\d+(?:\.\d+)?)\s*G", _flatten(row.get("grade")))
    if match and _number(match.group(1)):
        return {"grammage": float(match.group(1)), "source": "from_material_grade"}
    thickness = features.get("thickness_mm")
    if thickness:
        density = _number(row.get("density"))
        if density is None:
            for prop in (row.get("properties") or []):
                if _text(prop.get("prop_key")).lower() == "density":
                    density = _number(prop.get("value_num"))
                    if density is not None:
                        break
        if density is None:
            family = family_of(query)
            density = DEMO_PRICING.get(family, DEMO_PRICING_DEFAULT)[1]
            source = "derived_from_demo_density"
        else:
            source = "derived_from_density"
        return {"grammage": round(float(thickness) * float(density) * 1000, 1), "source": source,
                "thickness_mm": float(thickness), "density": float(density)}
    return {"grammage": None, "source": ""}


def ai_rerank(query: Any, candidates: List[Dict[str, Any]], *, complete=None,
              limit: int = 5) -> Dict[str, Any]:
    """相似度并列时的模型复排（`complete(**kwargs)` 由调用方注入，离线/失败一律回落）。

    回落不许静默：返回体里 `method` 说明这次到底用了哪条路（`model` / `similarity`）。
    """
    ranked = list(candidates or [])[:max(1, int(limit))]
    if not ranked:
        return {"method": "similarity", "chosen": None, "reason": "no_candidate"}
    if callable(complete):
        try:
            chosen = complete(query=query, candidates=ranked)
            code = _text((chosen or {}).get("material_code"))
            hit = next((item for item in ranked if item["material_code"] == code), None)
            if hit is not None:
                return {"method": "model", "chosen": hit,
                        "reason": _text((chosen or {}).get("reason"))}
        except Exception as exc:                                    # noqa: BLE001 - 复排失败就回落
            return {"method": "similarity", "chosen": ranked[0],
                    "reason": "model_unavailable:%s" % type(exc).__name__}
    return {"method": "similarity", "chosen": ranked[0], "reason": ""}


__all__ = ["ENGINE_VERSION", "MATCH_STATUSES", "MATCH_THRESHOLD", "AMBIGUOUS_MARGIN",
           "CANDIDATE_LIMIT", "FAMILY_KEYWORDS", "DEMO_PRICING", "material_features",
           "score_candidate", "rank_materials", "recommend_materials", "family_of",
           "demo_unit_price", "derive_grammage", "ai_rerank"]

"""受约束匹配：图纸零件 → 库内零件名/工艺路线。

Spec：`docs/specs/packaging-part-route-constrained-match.md`。

一句话：先按编码/逐字名，再按归一化名，最后才允许**受硬约束**的相似度；匹不上必须显式
`unbound` 并说明原因，任何情况下都不许静默取最高分。纯函数、离线、无副作用；候选空间由调用
方给定，本模块绝不跨盒型/跨行业自取候选。
"""
from __future__ import annotations

from difflib import SequenceMatcher
import math
import re

ENGINE_VERSION = "packaging-part-route-match/1"
MATCH_METHODS = ("exact_code", "exact_name", "normalized_name",
                 "constrained_similarity", "unbound")
BINDING_STATUSES = ("matched", "unbound", "skipped_external")
UNBOUND_REASONS = ("no_candidate", "position_conflict", "kind_conflict",
                   "below_threshold", "ambiguous", "duplicate_name")
EXTERNAL_WORDS = ("外购", "采购")
NAME_ALIASES = (("忖纸", "衬纸"), ("左盒", "左盖"), ("右盒", "右盖"))
ROUTE_SUFFIXES = ("工艺路线", "加工工艺路线")
DIRECTION_PAIRS = (("左", "右"), ("上", "下"), ("前", "后"), ("顶托", "底托"),
                   ("内盒", "外盒"))
KIND_WORDS = ("面纸", "衬纸", "灰板", "内卡", "贴牌", "EVA", "磁铁", "衬板",
              "盒背", "标牌")
SIMILARITY_THRESHOLD = 0.5


def _positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError):
        return None


def _material_types(text):
    text = str(text or '').upper().replace('双灰', '灰板')
    return {word for word in ('灰板', '白卡', '铜版', 'EVA', 'PET', '瓦楞', '坑纸', '纸管') if word in text}


def rank_route_features(part, candidates):
    """Rank packaging reference routes, never promote similarity to an approved standard."""
    name = normalize_part_name(part.get('name'))
    material = str(part.get('material_text') or '').strip().upper()
    ranked, rejected = [], []
    for candidate in candidates:
        other = normalize_part_name(candidate.get('name'))
        reason = _constraint_reason(name, other)
        target_material = str(candidate.get('material_text') or '').strip().upper()
        left, right = _material_types(material), _material_types(target_material)
        if left and right and left.isdisjoint(right):
            reason = 'material_conflict'
        if reason:
            rejected.append({'code': candidate.get('code'), 'name': candidate.get('name'), 'reason': reason})
            continue
        missing = []
        material_score = SequenceMatcher(None, material, target_material).ratio() if material and target_material else 0
        if not material or not target_material:
            missing.append('material')
        a = sorted(filter(None, (_positive(part.get('length_mm')), _positive(part.get('width_mm')))))
        b = sorted(filter(None, (_positive(candidate.get('length_mm')), _positive(candidate.get('width_mm')))))
        size = 0
        if part.get('size_verified') is True and len(a) == len(b) == 2:
            size = sum(min(x,y)/max(x,y) for x,y in zip(a,b))/2
        else:
            missing.append('verified_size')
        scores = {'name': SequenceMatcher(None,name,other).ratio(), 'material':material_score, 'size':size}
        score = .55*scores['name'] + .25*material_score + .20*size
        ranked.append({**candidate, 'score':round(score,4), 'scores':scores, 'missing_features':missing})
    ranked.sort(key=lambda c: (-c['score'], str(c.get('code') or ''), str(c.get('name') or '')))
    status, reason = 'unbound', 'no_candidate'
    if ranked:
        if ranked[0]['score'] < .55:
            reason = 'below_threshold'
        elif len(ranked)>1 and ranked[0]['score']-ranked[1]['score'] < .05:
            status, reason = 'ambiguous', 'insufficient_margin'
        else:
            status, reason = 'matched', ''
    return {'status':status,'reason':reason,'match_method':'feature_similarity',
            'approved':False,'weights':{'name':.55,'material':.25,'size':.20},
            'candidates':ranked,'rejected':rejected}

_INDEX_RE = re.compile(r"(?:内盒|灰板|衬板|托|盒|层)\s*([0-9]+)")
_FULLWIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}
_FULLWIDTH[0x3000] = 0x20
_SUFFIXES_BY_LENGTH = tuple(sorted(ROUTE_SUFFIXES, key=len, reverse=True))


def _to_halfwidth(text: str) -> str:
    return text.translate(_FULLWIDTH)


def _strip_route_suffix(text: str) -> str:
    """去掉末尾的路线后缀；不是后缀（或去完为空）时返回 `""`。"""
    for suffix in _SUFFIXES_BY_LENGTH:
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[: -len(suffix)]
    return ""


def _base_name(name) -> str:
    """候选名的「零件名」面：去尾后缀；没后缀就原样。"""
    text = str(name or "")
    return _strip_route_suffix(text) or text


def normalize_part_name(name) -> str:
    """固定顺序（幂等、纯函数）：去首尾空白 → 全角转半角 → 去所有空白 → 去后缀 → 套别名。"""
    text = _to_halfwidth(str(name or "").strip())
    text = "".join(text.split())
    stripped = _strip_route_suffix(text)
    if stripped:
        text = stripped
    for old, new in NAME_ALIASES:
        if old in text:
            text = text.replace(old, new)
    return text


def direction_conflict(a, b) -> str:
    """一对方向词各在一边时返回该对（如 `"左/右"`）；否则 `""`。"""
    left_text, right_text = str(a or ""), str(b or "")
    for left, right in DIRECTION_PAIRS:
        if (left in left_text and right in right_text) or (right in left_text and left in right_text):
            return "%s/%s" % (left, right)
    return ""


def index_tokens(name) -> tuple:
    """名字里的编号（内盒/灰板/衬板/托/盒/层 后的数字）去重升序元组。"""
    return tuple(sorted(set(_INDEX_RE.findall(str(name or "")))))


def index_conflict(a, b) -> str:
    """两侧都有编号且不相等时返回 `"index"`；否则 `""`。"""
    left, right = index_tokens(a), index_tokens(b)
    if left and right and left != right:
        return "index"
    return ""


def kind_tokens(name):
    """名字里出现的件型词集合（`KIND_WORDS` 的子集）。"""
    text = str(name or "")
    return frozenset(word for word in KIND_WORDS if word in text)


def kind_conflict(a, b) -> str:
    """两侧件型词集合不相等（含空集对非空集）时返回 `"kind"`；否则 `""`。"""
    return "kind" if kind_tokens(a) != kind_tokens(b) else ""


def _external_blob(part) -> str:
    source = part if isinstance(part, dict) else {}
    chunks = [str(source.get("name") or "")]
    for key in ("reference", "authority"):
        block = source.get(key)
        if isinstance(block, dict):
            chunks.append(str(block.get("process_text") or ""))
    return " ".join(chunks)


def is_external_part(part) -> bool:
    """外购/采购件（name 或 reference/authority 的 process_text 含 `EXTERNAL_WORDS`）。"""
    blob = _external_blob(part)
    return any(word in blob for word in EXTERNAL_WORDS)


def _constraint_reason(part_normalized: str, cand_normalized: str) -> str:
    """三道硬约束合一的淘汰原因（`UNBOUND_REASONS` 之一）；全过时 `""`。"""
    if direction_conflict(part_normalized, cand_normalized) or index_conflict(
            part_normalized, cand_normalized):
        return "position_conflict"
    if kind_conflict(part_normalized, cand_normalized):
        return "kind_conflict"
    return ""


def _hit(out: dict, candidate: dict, method: str, score: float) -> dict:
    result = dict(out)
    result["status"] = "matched"
    result["match_method"] = method
    result["matched_code"] = str(candidate.get("code") or "")
    result["matched_name"] = str(candidate.get("name") or "")
    result["score"] = score
    result["reason"] = ""
    return result


def match_one(part, candidates) -> dict:
    """按固定顺序判定一件；命中即返回，都不成立 → `unbound` 且带原因。"""
    source = part if isinstance(part, dict) else {}
    pool = [c for c in (candidates or []) if isinstance(c, dict)]
    part_name = str(source.get("name") or "")
    part_code = str(source.get("part_code") or source.get("business_part_code") or "")
    normalized = normalize_part_name(part_name)
    out = {"status": "unbound", "match_method": "unbound", "part_code": part_code,
           "part_name": part_name, "matched_code": "", "matched_name": "",
           "normalized": normalized, "score": 0.0, "reason": "",
           "candidates_total": len(pool), "rejected": []}
    if is_external_part(source):
        out["status"] = "skipped_external"
        return out
    if not pool:
        out["reason"] = "no_candidate"
        return out

    code_key = str(source.get("product_item_code") or "").strip()
    if code_key:
        for candidate in pool:
            if str(candidate.get("code") or "") == code_key:
                return _hit(out, candidate, "exact_code", 1.0)

    if part_name:
        for candidate in pool:
            candidate_name = str(candidate.get("name") or "")
            if part_name == candidate_name or part_name == _base_name(candidate_name):
                return _hit(out, candidate, "exact_name", 1.0)

    if normalized:
        hits = [c for c in pool
                if normalize_part_name(str(c.get("name") or "")) == normalized]
        if len(hits) == 1:
            return _hit(out, hits[0], "normalized_name", 1.0)
        if len(hits) > 1:
            out["reason"] = "duplicate_name"
            return out

    scored = []
    rejected = []
    for candidate in pool:
        candidate_name = str(candidate.get("name") or "")
        reason = _constraint_reason(normalized, normalize_part_name(candidate_name))
        if reason:
            rejected.append({"name": candidate_name, "reason": reason})
            continue
        ratio = SequenceMatcher(None, normalized,
                                normalize_part_name(candidate_name)).ratio()
        scored.append((round(ratio, 4), candidate))
    out["rejected"] = rejected
    if not scored:
        out["reason"] = rejected[0]["reason"] if rejected else "no_candidate"
        return out
    top = max(score for score, _ in scored)
    winners = [candidate for score, candidate in scored if score == top]
    if len(winners) > 1:
        out["reason"] = "ambiguous"
        return out
    if top < SIMILARITY_THRESHOLD:
        out["reason"] = "below_threshold"
        return out
    return _hit(out, winners[0], "constrained_similarity", top)


def match_parts(parts, candidates) -> dict:
    """批量判定：按入参顺序给 `bindings`，并汇总计数、未绑定清单与同目标披露。"""
    pool = [c for c in (candidates or []) if isinstance(c, dict)]
    bindings = [match_one(part, pool) for part in (parts or [])]
    matched = [b for b in bindings if b["status"] == "matched"]
    unbound = [b for b in bindings if b["status"] == "unbound"]
    skipped = [b for b in bindings if b["status"] == "skipped_external"]
    unbound_parts = [{"part_code": b["part_code"], "part_name": b["part_name"],
                      "reason": b["reason"]} for b in unbound]
    targets = {}
    order = []
    for binding in matched:
        key = binding["matched_code"] or binding["matched_name"]
        if key not in targets:
            targets[key] = {"matched_code": binding["matched_code"],
                            "matched_name": binding["matched_name"], "part_codes": []}
            order.append(key)
        targets[key]["part_codes"].append(binding["part_code"])
    duplicate_targets = [targets[key] for key in order
                         if len(targets[key]["part_codes"]) > 1]
    return {"engine_version": ENGINE_VERSION, "bindings": bindings,
            "matched": len(matched), "unbound": len(unbound),
            "skipped_external": len(skipped), "unbound_parts": unbound_parts,
            "duplicate_targets": duplicate_targets}

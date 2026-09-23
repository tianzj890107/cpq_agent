# -*- coding: utf-8 -*-
"""业务部件自动解析器（Spec `packaging-28-part-auto-resolution-and-2d-board-cleanup.md` §2）。

为什么单独一层：`packaging_parts.extract()` 拆出来的是 CAD **连通分量**（真样本酒盒 263 个），
那是几何事实，不是客户物料清单里的**业务部件**（28 件）。一张刀模图里一个业务部件可能由多个
互不连通的分量组成、也可能在排版里出现多个副本；反过来，"一种几何指纹 = 一个业务零件"同样
不成立（左右件同尺寸不同件）。所以业务清单只能来自**权威资料**，几何只负责"这一件在图上哪里"。

本模块是**确定性**的：只读 CAD IR 的文字/标注/图层/分量与已有权威清单，
不调模型、不联网、不落盘（落库由调用方 `packaging_parts.save_business_parts()` 做），
也不 import 任何几何/建模库（几何一律走已经解析好的 CAD IR）。

权威清单来源优先级（Spec §2.1，顺序即判据，先命中先用）：
  1. `attachment`      —— 本项目附件里的权威 BOM/报价资料工作簿；
  2. `knowledge_base`  —— 按 `box_type_code / case_code` 查已审核的业务部件清单；
  3. `drawing_hash`    —— 按 DWG SHA-256 查已审核的图纸权威快照；
  4. `dwg_candidate`   —— 以上都没有时，用图纸文字锚点生成**候选**部件（`authority_missing`）；
  5. `missing`         —— 连候选都构不出来（没有文字锚点、没有几何区域）。

无论哪种来源，`business_part_total` 都是**权威清单的件数**（酒盒样本 = 28），
几何绑定成功与否只改 `bound/partial/ambiguous/unbound`，**不许因为只定位到 20 件就把另外
8 件从清单里删掉**（Spec §2.1 末段）。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

ENGINE_VERSION = "packaging-business-part-resolver/1"

#: 落库的 doc key 与 `packaging_parts.BUSINESS_DOC_KEY` 是同一份文档（这里只做解析，不落库）。
DOC_KEY = "packaging_business_parts"

#: 权威清单来源闭集（Spec §2.1 + §3 的 `authority_source`）。
AUTHORITY_SOURCES = ("attachment", "knowledge_base", "drawing_hash", "dwg_candidate", "missing")

#: 几何绑定状态闭集（与 `packaging_parts.BUSINESS_BINDING_STATUSES` 同形）。
BINDING_STATUSES = ("bound", "partial", "ambiguous", "unbound")

#: 全局一对一匹配的规则号（留痕用）：Spec §2.4 要求**全局**匹配，不许 28 行各自贪心。
GLOBAL_ASSIGNMENT_RULE_ID = "business_parts_global_assignment_v1"

#: 相同尺寸的左右件**不许合并**（Spec §2.4 / §8 第 4 条）—— 与导入器的守卫同名同义。
SAME_SIZE_PARTS_NOT_MERGED = "same_size_parts_are_not_merged"

#: 文字锚点与别名规则都是版本化的（Spec §2.2 末条：别名规则版本化，不散落在前端）。
ANCHOR_VERSION = "packaging-part-anchors/1"
ALIAS_VERSION = "packaging-part-aliases/1"

#: 图纸里"名称锚点"的写法：`名称：左盖面纸`、`零件名称:xxx`、`部件名称 xxx`。
NAME_PATTERNS = (
    re.compile(r"(?:零件名称|部件名称|名称)\s*[：:]\s*(?P<name>[^\n\r]+)"),
    re.compile(r"^(?P<name>[^\n\r：:]{2,24})$"),
)

#: 排除：标题栏 / 图例 / 坐标网格 / 审批栏 / 尺寸公差文字（Spec §2.2 第 3 条）。
EXCLUDED_LAYER_HINTS = ("图框", "标题", "图例", "审批", "签名", "网格", "轴网",
                        "TITLE", "FRAME", "LEGEND", "BORDER", "GRID", "VIEWPORT", "DEFPOINTS")
EXCLUDED_TEXT_HINTS = ("公差", "±", "制表", "审核", "批准", "校对", "设计", "比例", "图号",
                       "版本", "页码", "第 ", "页共", "客户", "供应商", "材质说明", "备注",
                       "工艺说明", "数量", "单位：", "未注", "技术要求",
                       # 标题栏/图例/图框/网格栏位（真样本酒盒图纸里实测会出现这些整词）：
                       "日期", "项目编号", "项目名称", "包装材料", "基本尺寸范围", "修改履历",
                       "更改说明", "参考线", "正面图", "半穿", "V槽", "Flute", "两组为1套",
                       "单位", "角度", "纸盒", "刀模图")

#: 「名称」后面的第二段往往是材料/规格（`左盖面纸\P材料:225G铜版底PET光银`）——取第一段。
LABEL_CUTS = ("材料", "规格", "材质", "\\P", "\n", "\r", "{", "}")

#: 规格串（`1.8mm灰板裱光银纸` / `2.5mm灰板` / `350g粉灰`）：以数值+单位开头的是**材料**，不是件名。
_SPEC_LIKE = re.compile(r"^\s*\d+(?:\.\d+)?\s*(?:mm|MM|g|G|克|度|张|层)")

#: 别名规范（Spec §1.1）：规范写法 `忖纸→衬纸`、`左盒/右盒→左盖/右盖`。
ALIASES: Tuple[Tuple[str, str], ...] = (
    ("忖纸", "衬纸"),
    ("内村", "内衬"),
    ("左盒", "左盖"),
    ("右盒", "右盖"),
    ("外盒里层", "外盒里层"),
)

#: 方向/序号词（Spec §2.4 的判据之一）：匹配时用来区分同尺寸的左右件。
DIRECTION_WORDS = ("左", "右", "顶", "底", "内", "外", "上", "下")

#: 默认已审核权威清单落点（按图纸 hash / 盒型编码查的快照）。
DEFAULT_SEED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "agent_knowledge", "provenance", "packaging_authority_parts.json")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _squeeze(text: Any) -> str:
    return re.sub(r"\s+", "", _text(text))


def _width_normalized(text: Any) -> str:
    """全角数字/字母 → 半角（图纸文字里 `２８` 与 `28` 是同一件事）。"""
    out = []
    for char in _text(text):
        code = ord(char)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - 0xFEE0))
        elif code == 0x3000:
            out.append(" ")
        else:
            out.append(char)
    return "".join(out)


def normalize_part_label(text: Any) -> str:
    """把图纸上的写法规范成可比对的标签（Spec §2.2 第 2 条）。

    规则是**版本化**的（`ALIAS_VERSION`）：去空白与标点、全角转半角、套用别名表。
    原文不在这里丢 —— 调用方要把原文（`raw_text`）一起留着。
    """
    body = _width_normalized(text)
    body = re.sub(r"[\s　]+", "", body)
    for source, target in ALIASES:
        body = body.replace(source, target)
    return body


#: 业务部件名是**中文名**（真样本 28 件的写法）；纯 ASCII 的短文本是图例/图层标签，不是件名。
_CJK = re.compile(r"[\u3400-\u9fff]")


def _label_head(text: Any) -> str:
    """取名称锚点的"件名那一段"（`左盖面纸\P材料:225G…` → `左盖面纸`），并去掉 MTEXT 花括号。"""
    body = _width_normalized(text)
    for marker in LABEL_CUTS:
        at = body.find(marker)
        if at > 0:
            body = body[:at]
    return _text(body)


def _is_excluded_text(text: str, layer: str) -> bool:
    upper_layer = _text(layer).upper()
    for hint in EXCLUDED_LAYER_HINTS:
        if hint.upper() in upper_layer:
            return True
    for hint in EXCLUDED_TEXT_HINTS:
        if hint in text:
            return True
    if not _CJK.search(text):
        return True      # 图例 `Cut` / `Crease` 这类纯 ASCII 标签不是部件名
    if _SPEC_LIKE.match(text):
        return True      # `1.8mm灰板裱光银纸` 是材料规格文字，不是件名
    return False


def extract_text_anchors(cad_ir: Any) -> List[Dict[str, Any]]:
    """CAD IR → 名称锚点（Spec §2.2 第 1 条）。

    只读 IR 里已经解析好的 `texts`（TEXT/MTEXT 的原文、插入点、图层、entity id），
    **不重解析 DXF**；标题栏 / 图例 / 坐标网格 / 审批栏 / 尺寸公差文字一律排除（原因留痕）。
    """
    ir = cad_ir if isinstance(cad_ir, dict) else {}
    anchors: List[Dict[str, Any]] = []
    for row in (ir.get("texts") or []):
        if not isinstance(row, dict):
            continue
        raw = _text(row.get("raw_text") or row.get("normalized_text"))
        if not raw:
            continue
        layer = _text(row.get("layer"))
        name = ""
        for pattern in NAME_PATTERNS:
            match = pattern.search(raw)
            if match:
                name = _label_head(match.group("name"))
                break
        item = {
            "entity_id": _text(row.get("entity_id")),
            "handle": _text(row.get("handle")),
            "layer": layer,
            "raw_text": raw,
            "position": list(row.get("position") or []) or None,
            "anchor_version": ANCHOR_VERSION,
        }
        if not name:
            item["excluded"] = "not_a_name_anchor"
            anchors.append(item)
            continue
        if _is_excluded_text(name, layer):
            item["excluded"] = "annotation_or_frame"
            anchors.append(item)
            continue
        item["name"] = name
        item["normalized"] = normalize_part_label(name)
        item["alias_version"] = ALIAS_VERSION
        anchors.append(item)
    return anchors


def build_geometry_regions(cad_ir: Any) -> List[Dict[str, Any]]:
    """CAD IR → 部件区域（Spec §2.3）：一个区域可以包含多个分量/图元。

    分量来自 IR 的 `geometry.components`（连通分量），区域即"一个分量及其图元集合"。
    图框 / 标题栏 / 图例 / 尺寸线不进部件区域 —— 它们没有可制造曲线时分量本身就已被剔除，
    这里再按图层名挡一道并留痕 `annotation_or_frame`。
    """
    ir = cad_ir if isinstance(cad_ir, dict) else {}
    geometry = ir.get("geometry") if isinstance(ir.get("geometry"), dict) else {}
    by_id: Dict[str, Dict[str, Any]] = {}
    for row in (ir.get("entities") or []):
        if isinstance(row, dict):
            by_id[_text(row.get("entity_id"))] = row
    regions: List[Dict[str, Any]] = []
    for index, component in enumerate(geometry.get("components") or [], start=1):
        if not isinstance(component, dict):
            continue
        entity_ids = [_text(value) for value in (component.get("entity_ids") or []) if _text(value)]
        layers = sorted({_text((by_id.get(eid) or {}).get("layer"))
                         for eid in entity_ids if _text((by_id.get(eid) or {}).get("layer"))})
        frame_only = bool(layers) and all(_is_excluded_text("", layer) for layer in layers)
        region = {
            "region_id": "region:%s" % (_text(component.get("component_id")) or index),
            "component_ids": [_text(component.get("component_id")) or str(index)],
            "entity_ids": entity_ids,
            "entity_total": len(entity_ids),
            "bbox": list(component.get("bbox") or []) or None,
            "length_mm": _num(component.get("unfolded_length_mm")),
            "width_mm": _num(component.get("unfolded_width_mm")),
            "layers": layers,
            "area_mm2": _num(component.get("area_mm2")),
            "outline_status": _text(component.get("outline_status")),
        }
        if frame_only:
            region["excluded"] = "annotation_or_frame"
        regions.append(region)
    return regions


#: 几何零件文档的行 → 区域（Spec §3 的 `geometry_component_total` 报的是**过滤后**的分量数，
#: 真样本 263；IR 的原始连通分量是 1163，那是过滤前的事实，别混着用）。
def regions_from_geometry_parts(geometry_parts: Any) -> List[Dict[str, Any]]:
    doc = geometry_parts if isinstance(geometry_parts, dict) else {}
    regions: List[Dict[str, Any]] = []
    for index, row in enumerate(doc.get("parts") or [], start=1):
        if not isinstance(row, dict):
            continue
        component_id = _text(row.get("component_id")) or _text(row.get("part_code"))
        regions.append({
            "region_id": "region:%s" % (_text(row.get("part_code")) or index),
            "part_code": _text(row.get("part_code")),
            "component_ids": [component_id] if component_id else [],
            "entity_ids": [_text(value) for value in (row.get("entity_ids") or []) if _text(value)],
            "entity_total": len(row.get("entity_ids") or []),
            "bbox": list(row.get("bbox") or []) or None,
            "length_mm": _num(row.get("unfolded_length_mm")),
            "width_mm": _num(row.get("unfolded_width_mm")),
            "area_mm2": _num(row.get("area_mm2")),
            "layers": [_text(value) for value in (row.get("layers") or []) if _text(value)],
            "outline_status": _text(row.get("outline_status")),
        })
    return regions


def _region_size(region: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    length = _num(region.get("length_mm"))
    width = _num(region.get("width_mm"))
    if length is not None and width is not None:
        return length, width
    bbox = region.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        try:
            return abs(float(bbox[2]) - float(bbox[0])), abs(float(bbox[3]) - float(bbox[1]))
        except (TypeError, ValueError):
            return None, None
    return None, None


def _close_enough(left: Optional[float], right: Optional[float]) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= max(2.0, abs(min(left, right)) * 0.05)


def _axis_score(part: Dict[str, Any],
                region: Dict[str, Any]) -> Tuple[int, Optional[str]]:
    """业务件与区域的尺寸相符度：`2` = 两轴（含长宽对调）、`1` = 只对一轴、`0` = 对不上。"""
    length = _num(part.get("length_mm"))
    width = _num(part.get("width_mm"))
    region_length, region_width = _region_size(region)
    if None in (length, width) or None in (region_length, region_width):
        return 0, "size_unknown"
    for left, right in ((length, width), (width, length)):
        if _close_enough(left, region_length) and _close_enough(right, region_width):
            return 2, None
    if _close_enough(length, region_length) or _close_enough(width, region_width):
        return 1, "one_axis_only"
    return 0, "size_mismatch"


def _anchor_score(part: Dict[str, Any], anchors: List[Dict[str, Any]]) -> Tuple[int, Optional[Dict[str, Any]]]:
    """名称锚点相符度：`2` = 规范化标签完全相等；`1` = 一方包含另一方（`里层灰板` vs `里层灰板1`）。"""
    wanted = normalize_part_label(part.get("name"))
    if not wanted:
        return 0, None
    best: Optional[Dict[str, Any]] = None
    for anchor in anchors:
        if anchor.get("excluded") or not anchor.get("normalized"):
            continue
        label = _text(anchor.get("normalized"))
        if not label:
            continue
        if label == wanted:
            return 2, anchor
        if (wanted in label or label in wanted) and best is None:
            best = anchor
    return (1, best) if best is not None else (0, None)


def _direction_bonus(part: Dict[str, Any], anchor: Optional[Dict[str, Any]]) -> int:
    """方向词（左/右/顶/底/内/外…）对得上加分、对不上扣分（Spec §2.4）。

    这是**左右件同尺寸**不许合并的判据之一：两件尺寸一样时，方向词与锚点位置才是分水岭。
    """
    named = _text(part.get("name"))
    if not named:
        return 0
    label = _text((anchor or {}).get("name"))
    if not label:
        return 0
    for word in DIRECTION_WORDS:
        if word in named and word in label:
            return 1
        if word in named and word not in label:
            return -1
    return 0


def _candidate_score(part: Dict[str, Any], region: Dict[str, Any],
                     anchors: List[Dict[str, Any]]) -> Tuple[int, int, Optional[str], Optional[Dict[str, Any]]]:
    name_score, anchor = _anchor_score(part, anchors)
    axis_score, reason = _axis_score(part, region)
    bonus = _direction_bonus(part, anchor)
    total = name_score * 10 + axis_score * 5 + bonus
    if name_score == 0 and axis_score == 0:
        return 0, name_score, reason or "no_candidate", anchor
    return total, name_score, reason, anchor


def _fingerprint(region: Dict[str, Any]) -> str:
    size = _region_size(region)
    return "%.3f:%.3f:%s" % (size[0] if size[0] is not None else -1,
                             size[1] if size[1] is not None else -1,
                             _text(region.get("area_mm2")))


def match_authority_parts(authority_parts: Any, anchors: Any, regions: Any,
                          thumbnails: Any = None) -> Dict[str, Any]:
    """权威 28 件 ↔ 几何区域：**全局一对一**匹配（Spec §2.4）。

    为什么必须全局：按 28 行各自贪心取"最近的分量"时，相同尺寸的左右件会抢同一个分量、
    另一些件被挤掉。这里先算全部 `(件, 区域)` 分数，再按分数降序做**一次**全局指派，
    每个区域至多被一件占用；占用冲突导致没有可用区域时才退回 `ambiguous`。

    相同尺寸的左右件**不合并**：每件各占一行，各自去找自己的区域（找不到就是 `unbound`），
    重复排版的副本归到同一件的 `instances[]`，**不新增业务件**。
    """
    parts = [row for row in (authority_parts or []) if isinstance(row, dict)]
    anchor_rows = [row for row in (anchors or []) if isinstance(row, dict) and not row.get("excluded")]
    region_rows = [row for row in (regions or []) if isinstance(row, dict) and not row.get("excluded")]

    # 同一张图的排版副本：几何指纹相同 → 同一区域组（一个区域 + 若干实例）。
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for region in region_rows:
        groups.setdefault(_fingerprint(region), []).append(region)

    scored: List[Tuple[int, int, Dict[str, Any], Dict[str, Any], Optional[str], Optional[Dict[str, Any]]]] = []
    for part_index, part in enumerate(parts):
        for region in region_rows:
            total, name_score, reason, anchor = _candidate_score(part, region, anchor_rows)
            if total <= 0:
                continue
            scored.append((total, part_index, part, region, reason, anchor))
    # 全局指派：分数降序（同分按件序、区域 id 升序，保证确定性），每个区域至多一件。
    scored.sort(key=lambda row: (-row[0], row[1], _text(row[3].get("region_id"))))
    taken_regions: Dict[str, str] = {}
    assigned: Dict[int, Dict[str, Any]] = {}
    contested: Dict[int, List[Dict[str, Any]]] = {}
    for total, part_index, part, region, reason, anchor in scored:
        region_id = _text(region.get("region_id"))
        owner = taken_regions.get(region_id)
        if owner is not None:
            # 已经被更高分的件占用：这一件只能记成"争用"（同名/同尺寸的候选会有多个）。
            if total >= 5:
                contested.setdefault(part_index, []).append(region)
            continue
        if part_index in assigned:
            continue
        taken_regions[region_id] = _text(part.get("business_part_code")) or str(part_index)
        assigned[part_index] = {"region": region, "score": total, "anchor": anchor, "reason": reason}

    bindings: List[Dict[str, Any]] = []
    instances: List[Dict[str, Any]] = []
    for part_index, part in enumerate(parts):
        code = _text(part.get("business_part_code")) or ("P%02d" % (part_index + 1))
        hit = assigned.get(part_index)
        rivals = contested.get(part_index) or []
        if hit is None:
            status = "ambiguous" if rivals else "unbound"
            binding = {
                "business_part_code": code, "status": status,
                "component_ids": [], "entity_ids": [], "bbox": None,
                "confidence": 0.0,
                "reasons": ["equivalent_candidates"] if rivals else ["no_authority_binding"],
                "rule_id": GLOBAL_ASSIGNMENT_RULE_ID,
                "candidates": [_text(row.get("region_id")) for row in rivals],
            }
        else:
            region = hit["region"]
            copies = [row for row in groups.get(_fingerprint(region), [])
                      if _text(row.get("region_id")) != _text(region.get("region_id"))]
            status = "bound" if hit["score"] >= 10 else "partial"
            binding = {
                "business_part_code": code, "status": status,
                "component_ids": list(region.get("component_ids") or []),
                "entity_ids": list(region.get("entity_ids") or []),
                "bbox": list(region.get("bbox") or []) or None,
                "confidence": 0.6 if status == "bound" else 0.4,
                "reasons": [] if status == "bound" else ["one_axis_only"],
                "rule_id": GLOBAL_ASSIGNMENT_RULE_ID,
                "anchor_entity_id": _text((hit.get("anchor") or {}).get("entity_id")),
                "instances": [_text(row.get("region_id")) for row in copies],
            }
            for copy in copies:
                instances.append({"business_part_code": code,
                                  "region_id": _text(copy.get("region_id")),
                                  "reason": SAME_SIZE_PARTS_NOT_MERGED})
        bindings.append(binding)

    counts = {key: 0 for key in BINDING_STATUSES}
    for binding in bindings:
        counts[binding["status"]] = counts.get(binding["status"], 0) + 1
    return {
        "bindings": bindings,
        "instances": instances,
        "bound_total": counts["bound"],
        "partial_total": counts["partial"],
        "ambiguous_total": counts["ambiguous"],
        "unbound_total": counts["unbound"],
        "business_part_total": len(parts),
        "merge_guard": SAME_SIZE_PARTS_NOT_MERGED,
        "global_assignment": True,
        "same_size_parts_are_not_merged": True,
        "rule_id": GLOBAL_ASSIGNMENT_RULE_ID,
    }


# --------------------------------------------------------------------------- #
# 权威清单来源：attachment → knowledge_base → drawing_hash → dwg_candidate
# --------------------------------------------------------------------------- #
def load_seed(path: Any = None) -> Dict[str, Any]:
    """读已审核权威清单快照（只读；读不到就是空，不抛、不编）。"""
    target = _text(path) or DEFAULT_SEED_PATH
    try:
        with open(target, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except Exception:                                    # noqa: BLE001 - 读不到就是"没有这份快照"
        return {}
    return doc if isinstance(doc, dict) else {}


def _seed_entries(seed: Dict[str, Any]) -> List[Dict[str, Any]]:
    entries = seed.get("sources") if isinstance(seed, dict) else None
    return [row for row in (entries or []) if isinstance(row, dict)]


def authority_from_seed(seed: Dict[str, Any], *, box_type_code: str = "",
                        case_code: str = "", drawing_sha256: str = "") -> Optional[Dict[str, Any]]:
    """按 DWG hash / 盒型编码 / 案例号在快照里找已审核清单（先 hash、后盒型、再案例号）。"""
    wanted_hash = _text(drawing_sha256).lower()
    wanted_box = _text(box_type_code).upper()
    wanted_case = _text(case_code).upper()
    for entry in _seed_entries(seed):
        if not entry.get("reviewed"):
            continue          # 没审过的不是权威来源（Spec §2.1 第 4 条）
        if wanted_hash and _text(entry.get("drawing_sha256")).lower() == wanted_hash:
            return entry
    for entry in _seed_entries(seed):
        if not entry.get("reviewed"):
            continue
        if wanted_box and _text(entry.get("box_type_code")).upper() == wanted_box:
            return entry
    for entry in _seed_entries(seed):
        if not entry.get("reviewed"):
            continue
        if wanted_case and _text(entry.get("case_code")).upper() == wanted_case:
            return entry
    return None


def _entry_rows(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = entry.get("parts") if isinstance(entry, dict) else None
    out: List[Dict[str, Any]] = []
    for index, row in enumerate(rows or [], start=1):
        if not isinstance(row, dict):
            continue
        out.append({
            "sequence_no": int(row.get("sequence_no") or index),
            "business_part_code": _text(row.get("business_part_code")) or ("%s-P%02d" % (
                _text(entry.get("code_prefix")) or "PART", index)),
            "name": _text(row.get("name")),
            "length_mm": _num(row.get("length_mm")),
            "width_mm": _num(row.get("width_mm")),
            "material_text": _text(row.get("material_text")),
            "process_text": _text(row.get("process_text")),
            "product_size_text": _text(row.get("product_size_text")),
            "quantity_text": _text(row.get("quantity_text")),
            "purchase_text": _text(row.get("purchase_text")),
            "note": _text(row.get("note")),
        })
    return out


def _candidate_rows(anchors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """没有权威清单时的候选：用图纸文字锚点生成，**状态必须是 `authority_missing`**。"""
    rows: List[Dict[str, Any]] = []
    seen = set()
    for anchor in anchors:
        if anchor.get("excluded"):
            continue
        label = _text(anchor.get("name"))
        normalized = _text(anchor.get("normalized"))
        if not label or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        rows.append({
            "sequence_no": len(rows) + 1,
            "business_part_code": "CAND-P%02d" % (len(rows) + 1),
            "name": label,
            "raw_text": _text(anchor.get("raw_text")),
            "anchor_entity_id": _text(anchor.get("entity_id")),
        })
    return rows


def _default_import_workbook(source: Any, *, sheet: str = "",
                             file_name: str = "") -> Dict[str, Any]:
    """默认的权威工作簿读取器（读表库只在工具侧，这里**惰性**导入，后端平时不依赖它）。"""
    from . import packaging_part_authority
    kwargs: Dict[str, Any] = {"file_name": file_name}
    if sheet:
        kwargs["sheet"] = sheet
    return packaging_part_authority.import_workbook(source, **kwargs)


def _attachment_items(attachments: Any) -> List[Dict[str, Any]]:
    """附件参数的三种写法都收（`(name, bytes)` 元组 / `{"path": …}` / `{"name","bytes"}`）。"""
    out: List[Dict[str, Any]] = []
    for item in (attachments or []):
        if isinstance(item, (tuple, list)) and len(item) == 2:
            out.append({"name": _text(item[0]), "bytes": item[1]})
        elif isinstance(item, dict):
            out.append(dict(item))
        elif item:
            out.append({"path": _text(item)})
    return out


def _attachment_rows(attachments: Any, import_workbook: Any) -> Optional[Dict[str, Any]]:
    """来源 1：本项目附件里的权威工作簿。工作簿本体读不了就返回 None（换下一条来源）。"""
    importer = import_workbook if callable(import_workbook) else _default_import_workbook
    for item in _attachment_items(attachments):
        name = _text(item.get("name"))
        path = _text(item.get("path")) or (name if not item.get("bytes") else "")
        payload = item.get("bytes")
        if payload is None and (not path or not path.lower().endswith((".xlsx", ".xlsm"))):
            continue
        sheet = _text(item.get("sheet"))
        try:
            if payload is not None:
                result = importer(bytes(payload), sheet=sheet, file_name=name)
            else:
                result = importer(path, sheet=sheet)
        except Exception:                                # noqa: BLE001 - 这份附件读不成，换下一份
            continue
        if isinstance(result, dict) and (result.get("parts") or []):
            return result
    return None


def resolve_business_parts(project_id: str, cad_ir: Any, geometry_parts: Any,
                           attachments: Any = None, kb: Any = None,
                           *, seed_path: Any = None,
                           import_workbook: Callable[..., Any] = None) -> Dict[str, Any]:
    """一次业务部件解析（Spec §2.1 的优先级顺序 + §2.4 的全局匹配）。

    返回 `{"authority_source", "authority", "business_parts", "match", "detail"}`：
    `authority_source` 是命中来源（`missing` = 一个来源都没有），`detail` 直接可进流程步骤详情。
    **不落库**：调用方拿到 `business_parts` 后自己调 `packaging_parts.save_business_parts()`。
    """
    ir = cad_ir if isinstance(cad_ir, dict) else {}
    source_block = ir.get("source") if isinstance(ir.get("source"), dict) else {}
    drawing_sha256 = _text(source_block.get("source_sha256"))
    box_type_code = _text((kb or {}).get("box_type_code")
                          if isinstance(kb, dict) else "") or _text(ir.get("box_type_code"))
    case_code = _text((kb or {}).get("case_code") if isinstance(kb, dict) else "")
    seed = load_seed(seed_path)

    anchors = extract_text_anchors(ir)
    # 区域优先取**已过滤**的几何零件文档（真样本 263 件）；没有文档才退回 IR 的原始分量。
    regions = regions_from_geometry_parts(geometry_parts) or build_geometry_regions(ir)

    authority: Optional[Dict[str, Any]] = None
    authority_source = "missing"
    attachment = _attachment_rows(attachments, import_workbook)
    if attachment:
        authority, authority_source = attachment, "attachment"
    else:
        entry = authority_from_seed(seed, box_type_code=box_type_code, case_code=case_code,
                                    drawing_sha256=drawing_sha256)
        if entry is not None:
            authority = dict(entry)
            authority["parts"] = _entry_rows(entry)
            authority_source = _text(entry.get("authority_source")) or "drawing_hash"
            if authority_source not in AUTHORITY_SOURCES:
                authority_source = "drawing_hash"
    if authority is None:
        candidates = _candidate_rows(anchors)
        if candidates:
            authority = {"parts": candidates, "authority_missing": True,
                         "source": {"kind": "dwg_candidate", "anchor_version": ANCHOR_VERSION,
                                    "alias_version": ALIAS_VERSION}}
            authority_source = "dwg_candidate"

    rows = list((authority or {}).get("parts") or [])
    match = match_authority_parts(rows, anchors, regions)
    detail = {
        "authority_source": authority_source,
        "business_part_total": int(match["business_part_total"]),
        "bound_total": int(match["bound_total"]),
        "partial_total": int(match["partial_total"]),
        "ambiguous_total": int(match["ambiguous_total"]),
        "unbound_total": int(match["unbound_total"]),
        "geometry_component_total": len(regions),
        "anchor_total": len([row for row in anchors if not row.get("excluded")]),
        "drawing_sha256": drawing_sha256,
        "engine_version": ENGINE_VERSION,
        "authority_missing": authority_source in ("dwg_candidate", "missing"),
    }
    return {"authority_source": authority_source, "authority": authority or {},
            "authority_rows": rows, "anchors": anchors, "regions": regions,
            "match": match, "detail": detail}


__all__ = [
    "ALIASES", "ANCHOR_VERSION", "ALIAS_VERSION", "AUTHORITY_SOURCES",
    "BINDING_STATUSES", "DOC_KEY", "ENGINE_VERSION", "GLOBAL_ASSIGNMENT_RULE_ID",
    "SAME_SIZE_PARTS_NOT_MERGED", "authority_from_seed", "build_geometry_regions",
    "extract_text_anchors", "load_seed", "match_authority_parts",
    "normalize_part_label", "regions_from_geometry_parts", "resolve_business_parts",
]

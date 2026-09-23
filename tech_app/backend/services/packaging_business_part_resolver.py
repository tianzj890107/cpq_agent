# -*- coding: utf-8 -*-
"""业务部件自动解析器（Spec `packaging-parts-must-be-derived-from-the-drawing.md` §2）。

为什么单独一层：`packaging_parts.extract()` 拆出来的是 CAD **连通分量**（真样本酒盒 263 个），
那是几何事实，不是客户物料清单里的**业务部件**。业务部件清单的**唯一输入是 DWG 本身**：
图纸文字锚点给名称与材料/工艺，几何分量给尺寸与位置，两者绑在一起就是「名称 + 尺寸 + 图纸证据」。

BOM / 报价资料 / 已审核快照 / 人工导入清单**都不参与解析产出** —— 它们只能用来最后对答案
（放在测试与离线验收里，生产运行时读不到）。把答案当输入，等于把「验收金标」变成「运行时事实」：
真样本实测，按 SHA 命中仓库里的快照时，"酒盒 28 件"是查表查出来的，不是从图里推出来的。

本模块是**确定性**的：只读 CAD IR 的文字/标注/图层/分量，
不调模型、不联网、不落盘（落库由调用方 `packaging_parts.save_business_parts()` 做），
也不 import 任何几何/建模库（几何一律走已经解析好的 CAD IR）。

来源闭集（Spec §2.1）：`dwg` = 从图纸自身证据推导出来的清单；`missing` = 连推导都做不出来。
被拒绝的来源（`attachment` / `knowledge_base` / `drawing_hash` / `manual_import`）按固定顺序
记进 `detail.refused_sources`；`detail.gold_standard_used` 恒为 `False`。
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
AUTHORITY_SOURCES = ("dwg", "missing")

#: 运行时**拒绝**的来源（固定顺序、去重；Spec §2.1 第 2 条）：客户资料 / 已审核快照 /
#: 人工导入清单都不参与解析产出，只在 `detail.refused_sources` 里留痕。
RUNTIME_REFUSED_SOURCES = ("attachment", "knowledge_base", "drawing_hash", "manual_import")

#: 派生编码前缀（Spec §2.2）：派生件必须与客户编码（`JWXR21-P01`）可区分。
DERIVED_CODE_PREFIX = "DWG-BP"

#: 每行的派生态闭集（Spec §2.2）：名称+尺寸+图纸三件套齐 / 缺一样 / 定位不到。
DERIVED_STATUSES = ("derived", "partial", "unbound")

#: 图纸证据块的类型闭集（Spec §2.2）。
DRAWING_REF_KINDS = ("geometry_evidence", "part_view", "none")

#: 名称锚点落在几何区域上的容差（mm）：锚点文字压着轮廓线是常态。
ANCHOR_TOLERANCE_MM = 1.0

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

#: 与 `NAME_PATTERNS[0]` 同口径，但把分隔符也算终止符（`名称：X\P材料：Y` 里的 `\P` 已经是分隔符）。
_NAME_MARKER = re.compile(r"(?:零件名称|部件名称|名称)\s*[：:]\s*(?P<name>[^\n\r\x1f]+)")

#: 段分隔符（Spec §2.4）：MTEXT 换行 `\P`、真实换行、半角/全角逗号都算。
_SEGMENT_SEEDS = ("\\P", "\\p", "\r\n", "\r", "\n", "，", ",")
_SEGMENT_SEP = "\x1f"

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

#: 视图/示意标题（Spec §2.3 第 1 条）：含这些词的整串不是件名。
VIEW_MARKERS = ("视图", "示意图", "剖视", "轴测", "局部放大")
#: 产地/版本/状态备注（Spec §2.3 第 2 条）。
NOTE_MARKERS = ("转越南", "旧款", "新款", "大货", "色位", "不够", "待定", "暂不")
#: 标题栏**栏位**（Spec §2.3 第 4 条）：整串等于其中之一就不算件名。
TITLE_BLOCK_FIELDS = ("单位", "审核", "批准", "日期", "比例", "设计", "制图", "校对",
                      "角度", "图号", "版次", "签名")
#: 材料/说明表头（Spec §2.3 第 5 条）：对**原文**判一次、对拆出来的件名再判一次
#: （`包装材料` 被截成 `包装` 就是这条抓的）。
HEADER_MARKERS = ("包装材料", "材质说明", "技术要求")
#: 整盒自称（Spec §2.3 第 6 条）：`酒盒顶托`、`700ML酒盒底托` 是装配称呼，不是构成件。
WHOLE_BOX_WORDS = ("酒盒", "圆盘盒", "礼盒", "包装盒")
#: 方案名（Spec §2.3 第 3 条）。
_OPTION_NAME = re.compile(r"方案\s*\d")

#: 「名称」后面的第二段往往是材料/规格（`左盖面纸\P材料:225G铜版底PET光银`）——取第一段。
LABEL_CUTS = ("材料", "规格", "材质", "\\P", "\n", "\r", "{", "}")

#: 段内的材料标记（Spec §2.4）：标记之后是材料，之前是件名或其它。
MATERIAL_MARKERS = ("材料", "材质", "规格")

#: 工艺词（Spec §2.2 的 `process_text`）：拆得出就填，拆不出就是空串。
PROCESS_MARKERS = ("啤", "烫", "印", "UV", "uv", "覆膜", "过油", "裱", "粘", "压", "切", "丝印")

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

#: 生产默认**不读任何已审核快照**（Spec §2.1 第 4 条）：解析的输入只有 DWG。
DEFAULT_SEED_PATH = ""


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
    """取名称锚点的"件名那一段"（`左盖面纸\P材料:225G…` → `左盖面纸`），并去掉 MTEXT 花括号。

    只做**截断**；材料/工艺的拆分口径见 `split_label_parts()`（Spec §2.4）。
    """
    body = _strip_mtext_codes(_width_normalized(text))
    for marker in LABEL_CUTS:
        at = body.find(marker)
        if at > 0:
            body = body[:at]
    return _text(body)


def _strip_mtext_codes(text: Any) -> str:
    """去掉 MTEXT 的花括号与格式码（`{\\C0;旧款大货色位不够` → `旧款大货色位不够`）。"""
    body = re.sub(r"\{\\[A-Za-z][^;]*;", "", _text(text))
    return body.replace("{", "").replace("}", "")


def _split_segments(text: Any) -> List[str]:
    """把锚点原文切成段（Spec §2.4）：`\\P` / 真实换行 / 半角逗号 / 全角逗号都算分隔。"""
    body = _text(text)
    for seed in _SEGMENT_SEEDS:
        body = body.replace(seed, _SEGMENT_SEP)
    return [segment.strip() for segment in body.split(_SEGMENT_SEP)]


def _cut_material_marker(segment: str) -> Tuple[str, str]:
    """段内 `材料：X` / `材质 X` / `规格:Y` → `(标记之前, 标记之后)`（没有标记就原样返回）。"""
    for marker in MATERIAL_MARKERS:
        at = segment.find(marker)
        if at >= 0:
            return segment[:at], segment[at + len(marker):]
    return segment, ""


def _is_view_title(text: Any) -> bool:
    return any(marker in _text(text) for marker in VIEW_MARKERS + ("正面图", "侧视图", "后视图", "俯视图"))


def _is_process_text(text: Any) -> bool:
    return any(marker in _text(text) for marker in PROCESS_MARKERS)


def split_label_parts(raw: Any) -> Tuple[str, str, str]:
    """锚点原文 → `(name, material_text, process_text)`（Spec §2.4）。

    - `名称：左盖面纸\\P材料：225G铜版底PET光银` → 名称 `左盖面纸`、材料 `225G铜版底PET光银`；
    - `面卡，300G白卡/哑PP`（半角逗号同口径）→ 名称 `面卡`、材料 `300G白卡/哑PP`；
    - `内托支撑围条灰板\\P650G灰板\\P正面图，啤面` → 名称 `内托支撑围条灰板`、材料 `650G灰板`、
      工艺 `啤面`（视图标题 `正面图` 既不进材料也不进工艺）。
    原文不在这里丢：调用方把 `raw_text` 一起留着（`source_text`）。
    """
    body = _strip_mtext_codes(_width_normalized(raw))
    for seed in _SEGMENT_SEEDS:
        body = body.replace(seed, _SEGMENT_SEP)
    match = _NAME_MARKER.search(body)
    if match:
        # 只丢掉「名称：」这个前缀，件名与它后面那几段（材料/工艺）都留着。
        body = group_concat(match.group("name"), body[match.end():])
    segments = [segment for segment in _split_segments(body) if _text(segment)]
    name = ""
    rest: List[str] = []
    for segment in segments:
        head, tail = _cut_material_marker(segment)
        head = _text(head).strip("：: ")
        if not name:
            if head:
                name = head
                if tail:
                    rest.append(tail)
                continue
            if tail:
                rest.append(tail)
            continue
        if head:
            rest.append(head)
        if tail:
            rest.append(tail)
    if not name:
        name = _label_head(raw)          # 兜底：老口径的截断
    material: List[str] = []
    process: List[str] = []
    for segment in rest:
        piece = _text(segment).strip("：: ")
        if not piece or _is_view_title(piece):
            continue
        if _is_process_text(piece):
            process.append(piece)
        else:
            material.append(piece)
    return name, "/".join(material), "、".join(process)


def group_concat(head: Any, tail: Any) -> str:
    """把 `名称：X` 的件名与它后面那段接回去（中间补一个段分隔符）。"""
    head_text = _text(head)
    tail_text = _text(tail)
    if not tail_text:
        return head_text
    return head_text + _SEGMENT_SEP + tail_text


def _exclusion_reason(raw: Any, name: Any, layer: Any = "") -> str:
    """件名排除（Spec §2.3）：先对**原文**判、再对拆出来的**件名**判，命中就返回稳定原因码。"""
    upper_layer = _text(layer).upper()
    for hint in EXCLUDED_LAYER_HINTS:
        if hint.upper() in upper_layer:
            return "annotation_or_frame"
    name_text = _text(name)
    if not name_text:
        return "not_a_name_anchor"
    for probe in dict.fromkeys((name_text, _squeeze(name_text))):
        if any(marker in probe for marker in VIEW_MARKERS):
            return "view_or_diagram_title"
        if any(marker in probe for marker in NOTE_MARKERS):
            return "origin_or_status_note"
        if _OPTION_NAME.search(probe):
            return "option_name"
        if probe in TITLE_BLOCK_FIELDS:
            return "title_block_field"
        if any(marker in probe for marker in HEADER_MARKERS):
            return "material_table_header"
        if any(word in probe for word in WHOLE_BOX_WORDS):
            return "whole_box_self_reference"
    for probe in dict.fromkeys((_text(raw), _squeeze(raw))):
        if any(marker in probe for marker in HEADER_MARKERS):
            return "material_table_header"
        if any(marker in probe for marker in VIEW_MARKERS):
            return "view_or_diagram_title"
    if _is_excluded_text(name_text, layer):
        return "annotation_or_frame"
    if not _CJK.search(name_text):
        return "not_a_name_anchor"
    if _SPEC_LIKE.match(name_text):
        return "spec_like_text"
    return ""


def _is_excluded_text(text: Any, layer: Any = "") -> bool:
    """老口径的整词排除（图例/公差/材质说明/纯 ASCII 标签）：用于**件名**这一层。"""
    upper_layer = _text(layer).upper()
    for hint in EXCLUDED_LAYER_HINTS:
        if hint.upper() in upper_layer:
            return True
    body = _text(text)
    for hint in EXCLUDED_TEXT_HINTS:
        if hint in body:
            return True
    if not _CJK.search(body):
        return True      # 图例 `Cut` / `Crease` 这类纯 ASCII 标签不是部件名
    if _SPEC_LIKE.match(body):
        return True      # `1.8mm灰板裱光银纸` 是材料规格文字，不是件名
    return False


def extract_text_anchors(cad_ir: Any) -> List[Dict[str, Any]]:
    """CAD IR → 名称锚点（Spec §2.2/§2.3）。

    只读 IR 里已经解析好的 `texts`（TEXT/MTEXT 的原文、插入点、图层、entity id），
    **不重解析 DXF**；名称与材料/工艺在同一处拆开（`split_label_parts()`），
    视图标题 / 产地备注 / 方案名 / 标题栏栏位 / 材料表头 / 整盒自称一律排除（原因留痕）。
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
        name, material, process = split_label_parts(raw)
        item = {
            "entity_id": _text(row.get("entity_id")),
            "handle": _text(row.get("handle")),
            "layer": layer,
            "raw_text": raw,
            "position": list(row.get("position") or []) or None,
            "anchor_version": ANCHOR_VERSION,
        }
        reason = _exclusion_reason(raw, name, layer)
        if reason:
            item["excluded"] = reason
            anchors.append(item)
            continue
        item["name"] = name
        item["material_text"] = material
        item["process_text"] = process
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
    """业务部件行 ↔ 几何区域：按**名称锚点在图上的位置**做全局一对一绑定（Spec §2.2/§2.4）。

    为什么按位置：解析只吃 DWG，行里的尺寸就是从几何来的 —— 先把"这条名称锚点压在哪一块几何上"
    定下来，尺寸与图纸证据才有出处。绑定是**全局一对一**（不许各行各自贪心），同分按
    （区域面积、件序、区域 id）排序，保证与 dict 迭代顺序、时间无关；
    重复排版的区域只记进 `instances`，**不合并**同名同尺寸的件。
    """
    parts = [row for row in (authority_parts or []) if isinstance(row, dict)]
    anchor_rows = [row for row in (anchors or []) if isinstance(row, dict)]
    region_rows = [row for row in (regions or []) if isinstance(row, dict)]
    by_entity = {_text(row.get("entity_id")): row for row in anchor_rows
                 if _text(row.get("entity_id"))}

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for region in region_rows:
        groups.setdefault(_fingerprint(region), []).append(region)

    def _anchor_of(part: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        evidence = part.get("evidence") if isinstance(part.get("evidence"), dict) else {}
        for entity_id in (evidence.get("anchor_entity_ids") or []):
            hit = by_entity.get(_text(entity_id))
            if hit is not None:
                return hit
        return by_entity.get(_text(part.get("anchor_entity_id")))

    scored: List[Tuple[int, float, int, Dict[str, Any], Dict[str, Any]]] = []
    for part_index, part in enumerate(parts):
        point = (_anchor_of(part) or {}).get("position")
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError):
            continue
        for region in region_rows:
            bbox = region.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
                continue
            try:
                x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
            except (TypeError, ValueError):
                continue
            if not (x0 - ANCHOR_TOLERANCE_MM <= x <= x1 + ANCHOR_TOLERANCE_MM):
                continue
            if not (y0 - ANCHOR_TOLERANCE_MM <= y <= y1 + ANCHOR_TOLERANCE_MM):
                continue
            area = abs(x1 - x0) * abs(y1 - y0)
            # 面积 > 0 的区域排在前面，再按面积升序：大图框套小件时取那个小件。
            scored.append((0 if area > 0 else 1, area, part_index, part, region))

    scored.sort(key=lambda row: (row[0], row[1], row[2], _text(row[4].get("region_id"))))
    taken_regions: Dict[str, str] = {}
    assigned: Dict[int, Dict[str, Any]] = {}
    for _flag, _area, part_index, part, region in scored:
        region_id = _text(region.get("region_id"))
        if region_id in taken_regions or part_index in assigned:
            continue
        taken_regions[region_id] = _text(part.get("business_part_code")) or str(part_index)
        assigned[part_index] = region

    bindings: List[Dict[str, Any]] = []
    instances: List[Dict[str, Any]] = []
    for part_index, part in enumerate(parts):
        code = _text(part.get("business_part_code")) or ("%s%02d" % (DERIVED_CODE_PREFIX,
                                                                     part_index + 1))
        region = assigned.get(part_index)
        if region is None:
            bindings.append({
                "business_part_code": code, "status": "unbound",
                "component_ids": [], "entity_ids": [], "bbox": None,
                "length_mm": None, "width_mm": None,
                "confidence": 0.0, "reasons": ["no_geometry_region_at_anchor"],
                "rule_id": GLOBAL_ASSIGNMENT_RULE_ID, "region_id": "", "candidates": [],
            })
            continue
        length, width = _region_size(region)
        status = "bound" if (length is not None and width is not None) else "partial"
        copies = [row for row in groups.get(_fingerprint(region), [])
                  if _text(row.get("region_id")) != _text(region.get("region_id"))]
        bindings.append({
            "business_part_code": code, "status": status,
            "component_ids": list(region.get("component_ids") or []),
            "entity_ids": list(region.get("entity_ids") or []),
            "bbox": list(region.get("bbox") or []) or None,
            "length_mm": length, "width_mm": width,
            "confidence": 0.6 if status == "bound" else 0.4,
            "reasons": [] if status == "bound" else ["size_unknown"],
            "rule_id": GLOBAL_ASSIGNMENT_RULE_ID, "region_id": _text(region.get("region_id")),
            "anchor_entity_id": _text((_anchor_of(part) or {}).get("entity_id")),
            "instances": [_text(row.get("region_id")) for row in copies],
        })
        for copy in copies:
            instances.append({"business_part_code": code,
                              "region_id": _text(copy.get("region_id")),
                              "reason": SAME_SIZE_PARTS_NOT_MERGED})

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
    """读一份**离线**的已审核快照（只读；读不到就是空，不抛、不编）。

    生产默认读不到任何快照（`DEFAULT_SEED_PATH == ""`，Spec §2.1 第 4 条）：解析的输入只有 DWG。
    显式给路径时只给离线工具/验收用 —— **解析器自己不调它**。
    """
    target = _text(path)
    if not target:
        return {}
    try:
        with open(target, "r", encoding="utf-8") as handle:
            doc = json.load(handle)
    except Exception:                                    # noqa: BLE001 - 读不到就是"没有这份快照"
        return {}
    return doc if isinstance(doc, dict) else {}


def _derived_rows(anchors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """名称锚点 → 派生部件行（Spec §2.2 的产出契约）。

    件数由**图纸名称证据**决定：每个规范化名称一件（按 IR 顺序取第一条，去重是确定性的），
    不许硬凑成某个数字，也不许从资料里补进来。
    """
    rows: List[Dict[str, Any]] = []
    seen = set()
    for anchor in anchors:
        if anchor.get("excluded"):
            continue
        name = _text(anchor.get("name"))
        normalized = _text(anchor.get("normalized"))
        if not name or not normalized or normalized in seen:
            continue
        seen.add(normalized)
        entity_id = _text(anchor.get("entity_id"))
        rows.append({
            "sequence_no": len(rows) + 1,
            "business_part_code": "%s%02d" % (DERIVED_CODE_PREFIX, len(rows) + 1),
            "name": name,
            "name_from_drawing": True,
            "length_mm": None,
            "width_mm": None,
            "material_text": _text(anchor.get("material_text")),
            "process_text": _text(anchor.get("process_text")),
            "source_text": _text(anchor.get("raw_text")),
            "evidence": {
                "anchor_entity_ids": [entity_id] if entity_id else [],
                "component_ids": [],
                "bbox": None,
                "drawing_ref": {"kind": "none"},
            },
        })
    return rows


def resolve_business_parts(project_id: str, cad_ir: Any, geometry_parts: Any,
                           attachments: Any = None, kb: Any = None,
                           *, seed_path: Any = None,
                           import_workbook: Callable[..., Any] = None) -> Dict[str, Any]:
    """一次业务部件解析：**只吃 DWG**（Spec §2.1 的闭集 + §2.2 的产出契约 + §2.4 的全局匹配）。

    参数里的 `attachments` / `kb` / `seed_path` / `import_workbook` **一律不读、不用**（它们是
    被拒绝的来源，只记进 `detail.refused_sources`）：客户资料只用来最后对答案。

    返回 `{"authority_source", "authority", "authority_rows", "match", "detail"}`；
    `authority_source` ∈ (`dwg`, `missing`)。**不落库**：调用方拿到行之后自己调
    `packaging_parts.save_business_parts()`。
    """
    ir = cad_ir if isinstance(cad_ir, dict) else {}
    source_block = ir.get("source") if isinstance(ir.get("source"), dict) else {}
    drawing_sha256 = _text(source_block.get("source_sha256"))

    anchors = extract_text_anchors(ir)
    # 区域优先取**已过滤**的几何零件文档（真样本 263 件）；没有文档才退回 IR 的原始分量。
    regions = regions_from_geometry_parts(geometry_parts) or build_geometry_regions(ir)
    rows = _derived_rows(anchors)
    match = match_authority_parts(rows, anchors, regions)
    by_code = {_text(binding.get("business_part_code")): binding
               for binding in (match.get("bindings") or []) if isinstance(binding, dict)}

    for row in rows:
        binding = by_code.get(_text(row.get("business_part_code"))) or {}
        row["length_mm"] = _num(binding.get("length_mm"))
        row["width_mm"] = _num(binding.get("width_mm"))
        located = bool(binding.get("bbox") or binding.get("component_ids"))
        row["evidence"]["component_ids"] = list(binding.get("component_ids") or [])
        row["evidence"]["bbox"] = list(binding.get("bbox") or []) or None
        if not located:
            row["evidence"]["drawing_ref"] = {"kind": "none"}
            row["status"] = "unbound"
        else:
            row["evidence"]["drawing_ref"] = {
                "kind": "geometry_evidence",
                "component_ids": list(binding.get("component_ids") or []),
                "bbox": list(binding.get("bbox") or []) or None,
                "region_id": _text(binding.get("region_id")),
            }
            row["status"] = "derived" if (row["length_mm"] is not None
                                          and row["width_mm"] is not None) else "partial"
        row["reasons"] = [] if row["status"] == "derived" else list(
            binding.get("reasons") or ["no_geometry_region_at_anchor"])

    authority = {
        "parts": rows,
        "derived_from_drawing": True,
        "gold_standard_used": False,
        "refused_sources": list(RUNTIME_REFUSED_SOURCES),
        "source": {},
        "anchor_version": ANCHOR_VERSION,
        "alias_version": ALIAS_VERSION,
    }
    name_anchor_total = len([row for row in anchors if not row.get("excluded")])
    excluded_anchor_total = len([row for row in anchors if row.get("excluded")])
    with_size = [row for row in rows
                 if row.get("length_mm") is not None and row.get("width_mm") is not None]
    with_ref = [row for row in rows
                if _text((row.get("evidence") or {}).get("drawing_ref", {}).get("kind"))
                not in ("", "none")]
    detail = {
        "authority_source": "dwg" if rows else "missing",
        "derived_from_drawing": True,
        "gold_standard_used": False,
        "refused_sources": list(RUNTIME_REFUSED_SOURCES),
        "business_part_total": len(rows),
        "parts_with_size_total": len(with_size),
        "parts_with_drawing_ref_total": len(with_ref),
        "name_anchor_total": name_anchor_total,
        "excluded_anchor_total": excluded_anchor_total,
        "bound_total": int(match["bound_total"]),
        "partial_total": int(match["partial_total"]),
        "ambiguous_total": int(match["ambiguous_total"]),
        "unbound_total": int(match["unbound_total"]),
        "geometry_component_total": len(regions),
        "anchor_total": name_anchor_total,
        "drawing_sha256": drawing_sha256,
        "engine_version": ENGINE_VERSION,
        "authority_missing": not rows,
    }
    return {"authority_source": detail["authority_source"], "authority": authority,
            "authority_rows": rows, "anchors": anchors, "regions": regions,
            "match": match, "detail": detail}


__all__ = [
    "ALIASES", "ANCHOR_TOLERANCE_MM", "ANCHOR_VERSION", "ALIAS_VERSION", "AUTHORITY_SOURCES",
    "BINDING_STATUSES", "DERIVED_CODE_PREFIX", "DERIVED_STATUSES", "DOC_KEY",
    "DRAWING_REF_KINDS", "ENGINE_VERSION", "GLOBAL_ASSIGNMENT_RULE_ID",
    "RUNTIME_REFUSED_SOURCES", "SAME_SIZE_PARTS_NOT_MERGED", "build_geometry_regions",
    "extract_text_anchors", "load_seed", "match_authority_parts", "normalize_part_label",
    "regions_from_geometry_parts", "resolve_business_parts", "split_label_parts",
]

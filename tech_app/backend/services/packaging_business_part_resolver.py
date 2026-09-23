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

#: 推件用到的**证据类别**闭集（`## 458` Spec §3.1 第 1 条）：文字只是其中一类。
EVIDENCE_KINDS = ("text_anchor", "geometry_region", "size_dimension", "leader_callout",
                  "block_attribute", "layer_role", "spatial_relation", "repetition_mirror")

#: 名称锚点 ↔ 部件轮廓的配对半径（mm）：真样本酒盒实测 27 个唯一件名全部在 650mm 内
#: 找到自己那块轮廓（最远 506mm），再放大就跨到邻件上。
OUTLINE_MATCH_RADIUS_MM = 650.0

#: 一条"像零件"的轮廓下限（Spec §3.1：几何轮廓是主证据之一，但不是任何一条碎线都能当件）。
#: 真样本酒盒 1163 个连通分量里绝大多数是标注引线/图框碎片（边长 0.00mm），过滤后 261 条。
MIN_OUTLINE_SIDE_MM = 5.0
MIN_OUTLINE_AREA_MM2 = 2000.0
MIN_OUTLINE_ENTITIES = 2

#: 分组判定（Spec §3.3）：只有"父子分组"的成员轮廓要这么"像一件"，避免把碎片算成成员。
GROUP_MEMBER_MIN_AREA_MM2 = 10000.0

#: 尺寸标注矩形 ↔ 轮廓的相符容差（与 `packaging_match` 同口径量级）。
CONFIRM_TOLERANCE_MM = 2.0

#: 方向词（Spec §3.3 第 3 条）：**镜像类**方向词才参与父子分组判定（左/右、上/下、前/后）；
#: `内盒N` 这类序号由 `_position_token()` 与方向词一起构成族键，独立成族。
MIRROR_DIRECTIONS = ("左盖", "右盖", "左盒", "右盒", "左", "右", "上", "下", "前", "后")

#: 族键 = （方向词/容器词，部件尾词）；`内盒1灰板` 与 `内盒2灰板` 靠序号分开。
_POSITION_TOKEN = re.compile(r"^(左盖|右盖|左盒|右盒|顶托|底托|内盒\d+|左|右|上|下|前|后|内|外)")

#: 证据面口径是版本化的（Spec §3.1：证据类别闭集与配对规则一起演进）。
EVIDENCE_VERSION = "packaging-business-parts-evidence/1"

#: 绑定链断了的稳定码（Spec `packaging-business-parts-outline-bbox-broken-link.md` §1.3）：
#: `business_part_total > 0` 却一件都没绑上图（`bound + partial == 0`）时，`detail.outline_link`
#: 必须带上它 —— 一整份清单一件都没绑上，不是"个别件没找到轮廓"。
OUTLINE_LINK_BROKEN = "PACKAGING_BUSINESS_PARTS_OUTLINE_LINK_BROKEN"

#: 区域记录拿不到轮廓矩形时的原因码（Spec §1.2：不许静默产出 bbox/center 全空的记录）。
REASON_NO_OUTLINE_BBOX = "no_outline_bbox"

#: 稳定原因码（Spec §3.2 第 2 条：拿不到证据的件必须逐件留痕，不许静默少报）。
REASON_NO_OUTLINE = "no_outline_evidence"
REASON_SIZE_UNKNOWN = "outline_size_unknown"
REASON_UNLABELED_OUTLINE = "outline_without_name_anchor"

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


#: 半角/全角冒号后的"规格串"（`底板:2.5MM灰板`）——`## 453` §2.4 只覆盖了全角 `名称：…\P材料：…`。
_COLON_SPEC = re.compile(r"^\s*\d+(?:\.\d+)?\s*(?:mm|MM|g|G|克|度|张|层)")

#: 冒号后面出现这些词，也判成材料（`底盒底板灰板内衬裱卡：衬纸250g白卡裱1200g双灰`）。
MATERIAL_HINTS = ("灰板", "衬纸", "面纸", "裱卡", "白卡", "粉灰", "双灰", "铜版", "单粉",
                  "哑胶", "牛皮", "纸", "卡纸", "EVA", "磁铁", "坑")


def _looks_like_material(tail: str) -> bool:
    """冒号后的那半段是不是材料/规格（是 → 前半段才是件名）。"""
    body = _text(tail)
    if not body:
        return False
    if _COLON_SPEC.match(body):
        return True
    return any(hint in body for hint in MATERIAL_HINTS)


def _cut_material_marker(segment: str) -> Tuple[str, str]:
    """段内 `材料：X` / `材质 X` / `规格:Y` / `件名:材料` → `(标记之前, 标记之后)`。

    `## 458` 补上了**冒号直连**那种写法（真样本 `底板：2.5MM灰板` / `底板面纸：225G铜版底PET光银`）：
    冒号前的半段是件名、后半段像材料/规格，才拆；不像材料（`盒型：YT-RB-01`）就原样保留。
    """
    for marker in MATERIAL_MARKERS:
        at = segment.find(marker)
        if at >= 0:
            return segment[:at], segment[at + len(marker):]
    for colon in ("：", ":"):
        at = segment.find(colon)
        if at <= 0:
            continue
        head, tail = _text(segment[:at]), _text(segment[at + 1:])
        if head and tail and _looks_like_material(tail):
            return head, tail
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
    if len(_squeeze(name_text)) < 2:
        # 真样本里的 `刀`：一个字的整词是刀模标记/碎片，不是件名（Spec §2「多件」清单第 1 条）。
        return "single_character_label"
    if _SPEC_LIKE.match(name_text):
        return "spec_like_text"
    return ""


def _is_excluded_layer(layer: Any) -> bool:
    """只判**图层名**（`图框`/`TITLE`/`DEFPOINTS` …）。

    为什么单独来一条：`_is_excluded_text("", layer)` 会因为"空文本没有汉字"而对**任何**图层
    返回 True（真样本 1163 个分量因此全被 marked 成 `annotation_or_frame`）。只判图层名才
    是这条规则的本意。
    """
    upper = _text(layer).upper()
    return any(hint.upper() in upper for hint in EXCLUDED_LAYER_HINTS)


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


def _bbox_size(bbox: Any) -> Optional[Tuple[float, float]]:
    """包围盒 → `(长, 宽)`（都是非负；拿不到就 None）。"""
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None
    try:
        return abs(float(bbox[2]) - float(bbox[0])), abs(float(bbox[3]) - float(bbox[1]))
    except (TypeError, ValueError):
        return None


def _bbox_center(bbox: Any) -> Optional[List[float]]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return None
    try:
        return [(float(bbox[0]) + float(bbox[2])) / 2.0, (float(bbox[1]) + float(bbox[3])) / 2.0]
    except (TypeError, ValueError):
        return None


def _region_center(region: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    center = region.get("center")
    if isinstance(center, (list, tuple)) and len(center) >= 2:
        try:
            return float(center[0]), float(center[1])
        except (TypeError, ValueError):
            return None
    fallback = _bbox_center(region.get("bbox"))
    return (fallback[0], fallback[1]) if fallback else None


def _region_area(region: Dict[str, Any]) -> float:
    area = _num(region.get("area_mm2"))
    length, width = _region_size(region)
    if area is None and length is not None and width is not None:
        area = length * width
    return float(area or 0.0)


def _is_substantial(region: Dict[str, Any]) -> bool:
    """这条轮廓像不像"一件东西"（Spec §3.1：几何轮廓是主证据之一）。

    真样本酒盒 1163 个连通分量里绝大多数是引线/图框碎片：边长 0.00mm、面积 0、只含 1 个图元。
    过不了这道门的分量**只做位置参考，不当候选件**。
    """
    if region.get("excluded"):
        return False
    length, width = _region_size(region)
    if length is None or width is None:
        return False
    if min(length, width) < MIN_OUTLINE_SIDE_MM:
        return False
    if length * width < MIN_OUTLINE_AREA_MM2:
        return False
    return int(region.get("entity_total") or 0) >= MIN_OUTLINE_ENTITIES


def _region_record(region_id: str, component_ids: Any, entity_ids: Any, bbox: Any,
                   length: Any, width: Any, layers: Any, area: Any,
                   outline_status: Any = None, excluded: str = "") -> Dict[str, Any]:
    """一条轮廓的规范形状（IR 分量与几何零件文档两条来源共用一份）。"""
    size = _bbox_size(bbox)
    record: Dict[str, Any] = {
        "region_id": _text(region_id),
        "component_ids": [_text(value) for value in (component_ids or []) if _text(value)],
        "entity_ids": [_text(value) for value in (entity_ids or []) if _text(value)],
        "entity_total": len(entity_ids or []),
        "bbox": list(bbox) if isinstance(bbox, (list, tuple)) and len(bbox) >= 4 else None,
        "center": _bbox_center(bbox),
        "layers": [_text(value) for value in (layers or []) if _text(value)],
        "area_mm2": _num(area),
        "outline_status": _text(outline_status),
    }
    if size is not None:
        record["length_mm"], record["width_mm"] = size[0], size[1]
    else:
        record["length_mm"], record["width_mm"] = _num(length), _num(width)
    if not record["component_ids"]:
        record["component_ids"] = [record["region_id"]] if record["region_id"] else []
    if excluded:
        record["excluded"] = excluded
    record["substantial"] = _is_substantial(record)
    return record


def build_geometry_regions(cad_ir: Any) -> List[Dict[str, Any]]:
    """CAD IR → 部件区域（Spec §2.3/§3.1）：一个区域 = 一个连通分量及其图元集合。

    分量来自 IR 的 `geometry.components`；图框 / 标题栏 / 图例自带 `annotation_or_frame` 留痕。
    每条区域都带上 `center`（配对用）、`area_mm2` 与 `substantial`（像不像一件，见
    `_is_substantial()`）——**位置**是"这条名称锚点压在哪一块轮廓上"的判据。
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
        frame_only = bool(layers) and all(_is_excluded_layer(layer) for layer in layers)
        component_id = _text(component.get("component_id")) or str(index)
        regions.append(_region_record(
            "region:%s" % component_id,
            [component_id],
            entity_ids,
            component.get("bbox"),
            component.get("unfolded_length_mm"),
            component.get("unfolded_width_mm"),
            layers,
            component.get("area_mm2"),
            component.get("outline_status"),
            "annotation_or_frame" if frame_only else "",
        ))
    return regions


#: 几何零件文档的行 → 区域（Spec §3 的 `geometry_component_total` 报的是**过滤后**的分量数，
#: 真样本 263；IR 的原始连通分量是 1163，那是过滤前的事实，别混着用）。
def regions_from_geometry_parts(geometry_parts: Any) -> List[Dict[str, Any]]:
    """几何零件文档 → 区域（Spec §1.1/§1.2）。

    矩形必须走**唯一读法** `packaging_parts.part_outline_rect(row)`（顶层 `bbox` 否则
    `outline.bbox`）—— 这里以前写的是 `row.get("bbox")`，而真样本 263/312 行只有
    `outline.bbox`，于是每条 region 的 `bbox`/`center` 全空，`_assign_outlines()` 里
    "没有中心就 continue" 把整条绑定链掐死（酒盒 26 行、圆盘盒 66 行 bind 0）。
    拿不到矩形的行**留痕**：`excluded = no_outline_bbox` 且 `substantial = False`。
    """
    from . import packaging_parts as parts_module

    doc = geometry_parts if isinstance(geometry_parts, dict) else {}
    regions: List[Dict[str, Any]] = []
    for index, row in enumerate(doc.get("parts") or [], start=1):
        if not isinstance(row, dict):
            continue
        component_id = _text(row.get("component_id")) or _text(row.get("part_code"))
        part_code = _text(row.get("part_code")) or str(index)
        rect = parts_module.part_outline_rect(row)
        regions.append(_region_record(
            "region:%s" % part_code,
            [component_id] if component_id else [],
            row.get("entity_ids"),
            rect,
            row.get("unfolded_length_mm"),
            row.get("unfolded_width_mm"),
            row.get("layers"),
            row.get("area_mm2"),
            row.get("outline_status"),
            "" if rect else REASON_NO_OUTLINE_BBOX,
        ))
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


def _dimension_point(target: Any) -> Optional[Tuple[float, float]]:
    """`point:1501.715760,3388.310285` → `(1501.715760, 3388.310285)`。"""
    body = _text(target)
    if ":" not in body:
        return None
    body = body.split(":", 1)[1]
    pieces = body.split(",")
    if len(pieces) != 2:
        return None
    try:
        return float(pieces[0]), float(pieces[1])
    except (TypeError, ValueError):
        return None


def dimension_rects(cad_ir: Any) -> List[Dict[str, Any]]:
    """CAD IR 的线性标注 → "矩形"尺寸证据（Spec §3.1 的 `size_dimension`）。

    真样本酒盒 316 条标注里 277 条是水平/垂直线性标注（两个 `target_entity_ids` 是两个
    `point:x,y` 串）。一条水平（y 相等）与一条垂直（x 相等）标注若互相交成矩形
    （竖标注的 x 落在横标注的区间里、横标注的 y 落在竖标注的区间里），就给出一个
    `(长, 宽)` 候选 —— 这是"图上标了这个尺寸"的直接证据，与几何轮廓是**两笔独立的账**。
    """
    ir = cad_ir if isinstance(cad_ir, dict) else {}
    horizontal: List[Tuple[float, float, float]] = []
    vertical: List[Tuple[float, float, float]] = []
    for row in (ir.get("dimensions") or []):
        if not isinstance(row, dict) or _text(row.get("dim_type")) != "linear":
            continue
        targets = row.get("target_entity_ids") or []
        if len(targets) != 2:
            continue
        first, second = _dimension_point(targets[0]), _dimension_point(targets[1])
        if first is None or second is None:
            continue
        value = _num(row.get("measured_value"))
        if value is None:
            value = _num(row.get("declared_value"))
        if value is None or value <= 0:
            continue
        (x1, y1), (x2, y2) = first, second
        if abs(y1 - y2) <= 0.01 and abs(x1 - x2) > 0.01:
            horizontal.append((min(x1, x2), max(x1, x2), y1))
        elif abs(x1 - x2) <= 0.01 and abs(y1 - y2) > 0.01:
            vertical.append((min(y1, y2), max(y1, y2), x1))
    rects: List[Dict[str, Any]] = []
    for hx0, hx1, hy in horizontal:
        for vy0, vy1, vx in vertical:
            if hx0 - 1.0 <= vx <= hx1 + 1.0 and vy0 - 1.0 <= hy <= vy1 + 1.0:
                rects.append({
                    "length_mm": abs(hx1 - hx0),
                    "width_mm": abs(vy1 - vy0),
                    "center": [(hx0 + hx1) / 2.0, (vy0 + vy1) / 2.0],
                })
    return rects


def _sizes_match(left_length: Any, left_width: Any, right: Any) -> bool:
    """两个尺寸是否同形（长宽可对调；容差 `CONFIRM_TOLERANCE_MM`）。"""
    length, width = _num(left_length), _num(left_width)
    other_length, other_width = _region_size(right) if isinstance(right, dict) else (None, None)
    if None in (length, width, other_length, other_width):
        return False

    def _close(a: float, b: float) -> bool:
        return abs(a - b) <= max(CONFIRM_TOLERANCE_MM, min(abs(a), abs(b)) * 0.02)

    return ((_close(length, other_length) and _close(width, other_width))
            or (_close(length, other_width) and _close(width, other_length)))


def _size_key(region: Any) -> str:
    length, width = _region_size(region) if isinstance(region, dict) else (None, None)
    if length is None or width is None:
        return ""
    long_side, short_side = max(length, width), min(length, width)
    return "%.3f:%.3f" % (round(long_side, 3), round(short_side, 3))


def _region_is_size_confirmed(region: Dict[str, Any], rects: Any) -> bool:
    """这条轮廓的尺寸有没有**标注证据**（Spec §3.1 的 `size_dimension`）。"""
    for rect in (rects or []):
        if isinstance(rect, dict) and _sizes_match(rect.get("length_mm"), rect.get("width_mm"), region):
            return True
    return False


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


def _anchor_of(part: Dict[str, Any], by_entity: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    evidence = part.get("evidence") if isinstance(part.get("evidence"), dict) else {}
    for entity_id in (evidence.get("anchor_entity_ids") or []):
        hit = by_entity.get(_text(entity_id))
        if hit is not None:
            return hit
    return by_entity.get(_text(part.get("anchor_entity_id")))


def _part_position(part: Dict[str, Any], by_entity: Dict[str, Dict[str, Any]]) -> Optional[Tuple[float, float]]:
    point = (_anchor_of(part, by_entity) or {}).get("position")
    if not isinstance(point, (list, tuple)) or len(point) < 2:
        return None
    try:
        return float(point[0]), float(point[1])
    except (TypeError, ValueError):
        return None


def _point_in_bbox(point: Optional[Tuple[float, float]], bbox: Any, tolerance: float) -> bool:
    if point is None or not isinstance(bbox, (list, tuple)) or len(bbox) < 4:
        return False
    try:
        x0, y0, x1, y1 = float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])
    except (TypeError, ValueError):
        return False
    low_x, high_x = min(x0, x1), max(x0, x1)
    low_y, high_y = min(y0, y1), max(y0, y1)
    return (low_x - tolerance <= point[0] <= high_x + tolerance
            and low_y - tolerance <= point[1] <= high_y + tolerance)


def _assign_outlines(parts: List[Dict[str, Any]], anchors: List[Dict[str, Any]],
                     regions: List[Dict[str, Any]]) -> Tuple[Dict[int, Dict[str, Any]], Dict[str, str]]:
    """部件行 ↔ 轮廓区域的**全局一对一**指派（Spec §2.4 / §3.1）。

    判据只有一条：**这条名称锚点离哪块轮廓最近**（真样本酒盒实测 27 个唯一件名全部在
    `OUTLINE_MATCH_RADIUS_MM` 内找到自己那块）。候选只收 `substantial` 的轮廓 —— 引线、
    图框碎片（边长 0.00mm）不当候选件；同分按（距离、件序、区域 id）排，与 dict 顺序、
    时间无关。左右件同尺寸**不合并**：一块轮廓只派给一行。
    """
    by_entity = {_text(row.get("entity_id")): row for row in anchors if _text(row.get("entity_id"))}
    scored: List[Tuple[float, int, str, int, Dict[str, Any]]] = []
    for part_index, part in enumerate(parts):
        point = _part_position(part, by_entity)
        if point is None:
            continue
        for region in regions:
            if not region.get("substantial"):
                continue
            center = _region_center(region)
            if center is None:
                continue
            distance = ((center[0] - point[0]) ** 2 + (center[1] - point[1]) ** 2) ** 0.5
            if distance > OUTLINE_MATCH_RADIUS_MM:
                continue
            scored.append((round(distance, 6), part_index, _text(region.get("region_id")),
                           len(scored), region))
    scored.sort(key=lambda row: (row[0], row[1], row[2]))
    taken_regions: Dict[str, str] = {}
    assigned: Dict[int, Dict[str, Any]] = {}
    for distance, part_index, region_id, _order, region in scored:
        if region_id in taken_regions or part_index in assigned:
            continue
        taken_regions[region_id] = _text(parts[part_index].get("business_part_code")) or str(part_index)
        assigned[part_index] = region
    return assigned, taken_regions


def match_authority_parts(authority_parts: Any, anchors: Any, regions: Any,
                          thumbnails: Any = None, *, rects: Any = None) -> Dict[str, Any]:
    """业务部件行 ↔ 几何轮廓：按名称锚点的**图上位置**做全局一对一配对（Spec §2.4 / §3.1）。

    与 `## 456` 那版的关键差别：候选不再要求"锚点压在图框 bbox 里"（真样本里图框 bbox
    吞掉全图，锚点全都"压在里面"，于是配到了整张图框：5 件 `derived` 的尺寸是 3927×967
    这种荒唐值），而是按**轮廓中心到锚点的距离**配对，且只收 `substantial` 的轮廓；
    轮廓的尺寸另有一笔独立的账（`rects` = 尺寸标注矩形）：标注与轮廓同形 → 记
    `size_dimension` 证据（Spec §3.1）。
    """
    parts = [row for row in (authority_parts or []) if isinstance(row, dict)]
    anchor_rows = [row for row in (anchors or []) if isinstance(row, dict)]
    region_rows = [row for row in (regions or []) if isinstance(row, dict)]
    assigned, taken_regions = _assign_outlines(parts, anchor_rows, region_rows)

    size_groups: Dict[str, List[Dict[str, Any]]] = {}
    for region in region_rows:
        key = _size_key(region)
        if key:
            size_groups.setdefault(key, []).append(region)

    bindings: List[Dict[str, Any]] = []
    instances: List[Dict[str, Any]] = []
    for part_index, part in enumerate(parts):
        code = _text(part.get("business_part_code")) or ("%s%02d" % (DERIVED_CODE_PREFIX,
                                                                     part_index + 1))
        point = _part_position(part, {_text(row.get("entity_id")): row for row in anchor_rows})
        region = assigned.get(part_index)
        if region is None:
            bindings.append({
                "business_part_code": code, "status": "unbound",
                "component_ids": [], "entity_ids": [], "bbox": None,
                "length_mm": None, "width_mm": None,
                "confidence": 0.0, "reasons": [REASON_NO_OUTLINE],
                "rule_id": GLOBAL_ASSIGNMENT_RULE_ID, "region_id": "", "candidates": [],
                "evidence_kinds": ["text_anchor"],
            })
            continue
        length, width = _region_size(region)
        confirmed = _region_is_size_confirmed(region, rects)
        status = "bound" if (length is not None and width is not None) else "partial"
        key = _size_key(region)
        copies = [row for row in size_groups.get(key, [])
                  if _text(row.get("region_id")) != _text(region.get("region_id"))]
        kinds = ["text_anchor", "geometry_region"]
        if confirmed:
            kinds.append("size_dimension")
        if _point_in_bbox(point, region.get("bbox"), ANCHOR_TOLERANCE_MM):
            kinds.append("spatial_relation")
        bindings.append({
            "business_part_code": code, "status": status,
            "component_ids": list(region.get("component_ids") or []),
            "entity_ids": list(region.get("entity_ids") or []),
            "bbox": list(region.get("bbox") or []) or None,
            "length_mm": length, "width_mm": width,
            "confidence": 0.6 if status == "bound" else 0.4,
            "reasons": [] if status == "bound" else [REASON_SIZE_UNKNOWN],
            "rule_id": GLOBAL_ASSIGNMENT_RULE_ID, "region_id": _text(region.get("region_id")),
            "size_source": "size_dimension" if confirmed else "geometry_region",
            "size_confirmed": bool(confirmed),
            "region_area_mm2": _region_area(region),
            "region_layers": list(region.get("layers") or []),
            "anchor_entity_id": _text((_anchor_of(part, {_text(row.get("entity_id")): row
                                                         for row in anchor_rows}) or {}).get("entity_id")),
            "evidence_kinds": kinds,
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
        "assignment_radius_mm": OUTLINE_MATCH_RADIUS_MM,
        "assigned_regions": taken_regions,
        "rule_id": GLOBAL_ASSIGNMENT_RULE_ID,
    }


def _position_token(label: Any) -> str:
    """族键里的**方向词/容器词**（Spec §3.3 第 3 条）：`内盒1灰板` → `内盒1`（序号参与）。"""
    match = _POSITION_TOKEN.match(_text(label))
    return match.group(1) if match else ""


def _tail_noun(label: Any) -> str:
    """族键里的**部件尾词**：末尾 2 个汉字（`…里层灰板` → `灰板`）；没有汉字就取 ASCII 尾段。"""
    body = _text(label)
    match = re.search(r"([\u3400-\u9fff]{1,2})$", body)
    if match:
        return match.group(1)
    match = re.search(r"([A-Za-z0-9+\-/]+)$", body)
    return match.group(1) if match else body


def _family_key(label: Any) -> Tuple[str, str]:
    return _position_token(label), _tail_noun(label)


def plan_family_groups(rows: Any, bindings: Any) -> List[Dict[str, Any]]:
    """父子分组判定（Spec §3.3）：把"一条父名 = 一件"改成"一条父名 = 拆出来的 N 件"。

    判据（三条同时成立，缺一不拆）：
    1. 同一**族**（方向词/容器词 + 部件尾词相同，`内盒N` 这类序号参与族键）里有 ≥2 条名称锚点；
    2. 族键的方向词是**镜像类**方向词（`左/右`、`上/下`、`前/后`）—— 真样本酒盒上，
       `左盒外盒里层灰板` 与 `左盒盒背灰板` 指的是同一族的两件，而 `内托加强灰板` 与
       `内托支撑围条灰板` 只是碰巧共尾词，不是一件的上下半（Spec §3.3 第 2/3 条）；
    3. 每个成员的**最近轮廓**互不相同、都够"像一件"（面积 ≥ `GROUP_MEMBER_MIN_AREA_MM2`）。

    成立时：父名 = 持有最大轮廓的那个成员，成员按轮廓面积降序编号（`…1` 是大的那件），
    逐件带上各自的轮廓与材料 —— 父名本身不再单独占一行（真样本实测 `左盖外盒里层灰板1`
    217.06×482.92、`…2` 44.60×273.92，与金标逐字对得上）。
    """
    by_code = {_text(binding.get("business_part_code")): binding
               for binding in (bindings or []) if isinstance(binding, dict)}
    families: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for row in (rows or []):
        if isinstance(row, dict):
            families.setdefault(_family_key(row.get("name")), []).append(row)
    plans: List[Dict[str, Any]] = []
    for key in sorted(families):
        members = families[key]
        if len(members) < 2 or key[0] not in MIRROR_DIRECTIONS:
            continue
        candidates: List[Tuple[float, Dict[str, Any], Dict[str, Any]]] = []
        seen_regions = set()
        for row in members:
            binding = by_code.get(_text(row.get("business_part_code"))) or {}
            region_id = _text(binding.get("region_id"))
            area = float(binding.get("region_area_mm2") or 0.0)
            if not region_id or region_id in seen_regions:
                candidates = []
                break
            if area < GROUP_MEMBER_MIN_AREA_MM2:
                candidates = []
                break
            seen_regions.add(region_id)
            candidates.append((area, row, binding))
        if len(candidates) < 2:
            continue
        candidates.sort(key=lambda item: (-item[0], _text(item[1].get("name"))))
        base_name = _text(candidates[0][1].get("name"))
        plans.append({
            "family": "%s|%s" % (key[0], key[1]),
            "base_name": base_name,
            # Spec §3.3 第 2 条：成员名 = **父名**+序号（`左盒外盒里层灰板` → `…1` / `…2`），
            # 不是"各自的名字+序号"——同族兄弟名（`盒背灰板`）只是族里那一件的**图上写法**。
            "members": [{"name": "%s%d" % (base_name, index), "source_name": _text(row.get("name")),
                         "row": row, "binding": binding}
                        for index, (_area, row, binding) in enumerate(candidates, start=1)],
        })
    return plans


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
    """名称锚点 → 派生部件行（Spec §2.2 的产出契约 + §3.1 的证据面）。

    件数由**图纸证据**决定：先按规范化名称去重（按 IR 顺序取第一条），每条名称锚点先给一行；
    "一条锚点 = 一件"只是**起点** —— 父子分组（`plan_family_groups()`）与轮廓/尺寸证据会在
    后面把父名拆开、把尺寸与位置补齐。
    """
    rows: List[Dict[str, Any]] = []
    seen = set()
    index_by_name: Dict[str, int] = {}
    for anchor in anchors:
        if anchor.get("excluded"):
            continue
        name = _text(anchor.get("name"))
        normalized = _text(anchor.get("normalized"))
        if not name or not normalized:
            continue
        entity_id = _text(anchor.get("entity_id"))
        if normalized in seen:
            # 同一件名在图上出现多次（真样本是"原图 + 镜像"两套排版）：留痕，不另立行。
            if entity_id:
                rows[index_by_name[normalized]]["evidence"]["anchor_entity_ids"].append(entity_id)
            continue
        seen.add(normalized)
        index_by_name[normalized] = len(rows)
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
                "kinds": ["text_anchor"],
                "group": {"kind": "leaf", "members": [], "parent": ""},
            },
        })
    return rows


def _row_evidence_kinds(row: Dict[str, Any], binding: Dict[str, Any],
                        anchor_total: int, entity_by_id: Dict[str, Dict[str, Any]]) -> List[str]:
    """一行到底用了哪几类证据（Spec §3.1 第 1/2 条）：闭集、不重复、按固定顺序。"""
    kinds = ["text_anchor"]
    if _text(binding.get("region_id")):
        kinds.append("geometry_region")
    if binding.get("size_confirmed"):
        kinds.append("size_dimension")
    if anchor_total >= 2:
        kinds.append("repetition_mirror")
    point = _part_position(row, entity_by_id)
    if _point_in_bbox(point, binding.get("bbox"), ANCHOR_TOLERANCE_MM):
        kinds.append("spatial_relation")
    if any(layer and layer != "0" for layer in (binding.get("region_layers") or [])):
        kinds.append("layer_role")
    unique = set(kinds)
    return [kind for kind in EVIDENCE_KINDS if kind in unique]


def _expand_family_groups(rows: List[Dict[str, Any]], anchors: List[Dict[str, Any]],
                          bindings: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """父子分组（Spec §3.3）：父名换成编号成员行，父名本身不再单独占一行。"""
    plans = plan_family_groups(rows, bindings)
    if not plans:
        return rows, []
    replaced: Dict[int, List[Dict[str, Any]]] = {}
    consumed: set = set()
    detail_plans: List[Dict[str, Any]] = []
    by_identity = {id(row): index for index, row in enumerate(rows)}
    for plan in plans:
        member_names = [member["name"] for member in plan["members"]]
        anchors_at = sorted(by_identity[id(member["row"])] for member in plan["members"])
        expanded: List[Dict[str, Any]] = []
        for member in plan["members"]:
            source = member["row"]
            child = json.loads(json.dumps(source, ensure_ascii=False, default=str))
            child["name"] = member["name"]
            child["name_from_drawing"] = True
            child["parent_name"] = plan["base_name"]
            child["parent_code"] = _text(source.get("business_part_code"))
            child["source_text"] = _text(source.get("source_text"))
            child["evidence"]["group"] = {
                "kind": "group",
                "members": list(member_names),
                "parent": plan["base_name"],
                "family": plan["family"],
                "drawing_label": _text(member.get("source_name")),
                "leaf_count": len(member_names),
            }
            child["evidence"]["member_region_id"] = _text((member["binding"] or {}).get("region_id"))
            expanded.append(child)
        # 同族的**每一条**成员行都被拆进成员里（不是只收基准名那一条），父名/兄弟名不再单独占行。
        replaced[anchors_at[0]] = expanded
        consumed.update(anchors_at)
        detail_plans.append({
            "family": plan["family"],
            "parent": plan["base_name"],
            "members": member_names,
            "drawing_labels": [_text(member.get("source_name")) for member in plan["members"]],
            "rule": "same_family_direction_and_tail_noun_with_distinct_outlines",
        })
    ordered: List[Dict[str, Any]] = []
    for index, row in enumerate(rows):
        if index in replaced:
            ordered.extend(replaced[index])
        elif index in consumed:
            continue
        else:
            ordered.append(row)
    return ordered, detail_plans


def resolve_business_parts(project_id: str, cad_ir: Any, geometry_parts: Any,
                           attachments: Any = None, kb: Any = None,
                           *, seed_path: Any = None,
                           import_workbook: Callable[..., Any] = None) -> Dict[str, Any]:
    """一次业务部件解析：**只吃 DWG**（Spec §2.1 的闭集 + §2.2 的产出契约 + §2.4 的全局匹配）。

    参数里的 `attachments` / `kb` / `seed_path` / `import_workbook` **一律不读、不用**（它们是
    被拒绝的来源，只记进 `detail.refused_sources`）：客户资料只用来最后对答案。

    `## 458` 起，推件的输入是**整张 DWG 的全部证据**：文字锚点给名称，几何轮廓给尺寸与位置，
    尺寸标注给尺寸的第二笔账，重复的锚点给"这件在排版里出现多次"，同族方向词给父子分组判定；
    `## 453` 的"名称文字上限"只是名称这一条证据的命中率，不是件数上限。

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
    rects = dimension_rects(ir)
    rows = _derived_rows(anchors)
    match = match_authority_parts(rows, anchors, regions, rects=rects)
    rows, group_plans = _expand_family_groups(rows, anchors, match.get("bindings") or [])

    entity_by_id = {_text(row.get("entity_id")): row for row in anchors if _text(row.get("entity_id"))}
    by_code = {_text(binding.get("business_part_code")): binding
               for binding in (match.get("bindings") or []) if isinstance(binding, dict)}

    # 分组展开后编码重排（保证唯一 + 连续；`## 453` §2.2 的派生前缀不变）。
    # 绑定是按**展开前**的编码配出来的，先把原名留在 `parent_code` 上再重排。
    for index, row in enumerate(rows, start=1):
        row.setdefault("parent_code", _text(row.get("business_part_code")))
        row["sequence_no"] = index
        row["business_part_code"] = "%s%02d" % (DERIVED_CODE_PREFIX, index)

    for row in rows:
        binding = by_code.get(_text(row.get("parent_code"))) or {}
        if not binding:
            # 兜底：分组成员按自己的轮廓 id 找绑定（父名拆出来的行没有原编码时走这里）。
            binding = _binding_for_expanded_row(row, by_code, match)
        row["length_mm"] = _num(binding.get("length_mm"))
        row["width_mm"] = _num(binding.get("width_mm"))
        located = bool(binding.get("region_id"))
        row["evidence"]["component_ids"] = list(binding.get("component_ids") or [])
        row["evidence"]["bbox"] = list(binding.get("bbox") or []) or None
        if not located:
            row["evidence"]["drawing_ref"] = {"kind": "none"}
            row["status"] = "unbound"
            row["reasons"] = list(binding.get("reasons") or [REASON_NO_OUTLINE])
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
                binding.get("reasons") or [REASON_SIZE_UNKNOWN])
        anchor_total = len(row["evidence"].get("anchor_entity_ids") or [])
        row["evidence"]["kinds"] = _row_evidence_kinds(row, binding, anchor_total, entity_by_id)
        row["evidence"]["size_source"] = _text(binding.get("size_source")) or (
            "geometry_region" if located else "none")
        row["evidence"]["size_confirmed"] = bool(binding.get("size_confirmed"))

    authority = {
        "parts": rows,
        "derived_from_drawing": True,
        "gold_standard_used": False,
        "refused_sources": list(RUNTIME_REFUSED_SOURCES),
        "source": {},
        "anchor_version": ANCHOR_VERSION,
        "alias_version": ALIAS_VERSION,
        "evidence_version": EVIDENCE_VERSION,
    }
    name_anchor_total = len([row for row in anchors if not row.get("excluded")])
    excluded_anchor_total = len([row for row in anchors if row.get("excluded")])
    with_size = [row for row in rows
                 if row.get("length_mm") is not None and row.get("width_mm") is not None]
    with_ref = [row for row in rows
                if _text((row.get("evidence") or {}).get("drawing_ref", {}).get("kind"))
                not in ("", "none")]
    used_kinds = set()
    for row in rows:
        used_kinds |= set((row.get("evidence") or {}).get("kinds") or [])
    reasons_breakdown: Dict[str, int] = {}
    for row in rows:
        for reason in (row.get("reasons") or []):
            reasons_breakdown[reason] = reasons_breakdown.get(reason, 0) + 1
    unobservable_total = len([row for row in rows
                              if set((row.get("evidence") or {}).get("kinds") or []) == {"text_anchor"}])
    regions_with_center_total = len([region for region in regions if _region_center(region)])
    linked_total = int(match["bound_total"]) + int(match["partial_total"])
    outline_link: Dict[str, Any] = {
        "business_part_total": len(rows),
        "region_total": len(regions),
        "regions_with_center_total": regions_with_center_total,
        "code": OUTLINE_LINK_BROKEN if (rows and linked_total == 0) else "",
    }
    labeled_regions = {_text((row.get("evidence") or {}).get("drawing_ref", {}).get("region_id"))
                       for row in rows if _text((row.get("evidence") or {})
                                                .get("drawing_ref", {}).get("region_id"))}
    unlabeled_outline_total = len([region for region in regions
                                   if region.get("substantial")
                                   and _text(region.get("region_id")) not in labeled_regions])
    if unlabeled_outline_total:
        reasons_breakdown[REASON_UNLABELED_OUTLINE] = (
            reasons_breakdown.get(REASON_UNLABELED_OUTLINE, 0) + unlabeled_outline_total)
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
        "substantial_outline_total": len([region for region in regions if region.get("substantial")]),
        "dimension_rect_total": len(rects),
        "evidence_kinds": [kind for kind in EVIDENCE_KINDS if kind in used_kinds],
        "evidence_version": EVIDENCE_VERSION,
        "reasons_breakdown": reasons_breakdown,
        "unobservable_total": unobservable_total,
        "unobservable_reason": "row_has_no_drawing_evidence_beyond_its_name",
        "unlabeled_outline_total": unlabeled_outline_total,
        "regions_with_center_total": regions_with_center_total,
        "outline_link": outline_link,
        "group_total": len(group_plans),
        "group_plans": group_plans,
        "size_source_counts": _count_by(rows, lambda row: _text(
            (row.get("evidence") or {}).get("size_source") or "none")),
        "outline_rule_id": GLOBAL_ASSIGNMENT_RULE_ID,
        "anchor_total": name_anchor_total,
        "drawing_sha256": drawing_sha256,
        "engine_version": ENGINE_VERSION,
        "authority_missing": not rows,
    }
    return {"authority_source": detail["authority_source"], "authority": authority,
            "authority_rows": rows, "anchors": anchors, "regions": regions,
            "rects": rects, "match": match, "detail": detail}


def _count_by(rows: Any, key_of: Callable[[Dict[str, Any]], str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in (rows or []):
        if not isinstance(row, dict):
            continue
        key = key_of(row)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _binding_for_expanded_row(row: Dict[str, Any], by_code: Dict[str, Dict[str, Any]],
                              match: Dict[str, Any]) -> Dict[str, Any]:
    """分组成员行 → 它自己那条轮廓的绑定（成员行是父名拆出来的，编码已重排）。"""
    region_id = _text((row.get("evidence") or {}).get("member_region_id"))
    if not region_id:
        return {}
    for binding in (match.get("bindings") or []):
        if isinstance(binding, dict) and _text(binding.get("region_id")) == region_id:
            return binding
    return {}


__all__ = [
    "ALIASES", "ANCHOR_TOLERANCE_MM", "ANCHOR_VERSION", "ALIAS_VERSION", "AUTHORITY_SOURCES",
    "BINDING_STATUSES", "DERIVED_CODE_PREFIX", "DERIVED_STATUSES", "DOC_KEY",
    "DRAWING_REF_KINDS", "ENGINE_VERSION", "EVIDENCE_KINDS", "EVIDENCE_VERSION",
    "GLOBAL_ASSIGNMENT_RULE_ID", "GROUP_MEMBER_MIN_AREA_MM2", "MIN_OUTLINE_AREA_MM2",
    "MIN_OUTLINE_ENTITIES", "MIN_OUTLINE_SIDE_MM", "MIRROR_DIRECTIONS",
    "OUTLINE_LINK_BROKEN", "OUTLINE_MATCH_RADIUS_MM", "REASON_NO_OUTLINE",
    "REASON_NO_OUTLINE_BBOX", "REASON_SIZE_UNKNOWN",
    "REASON_UNLABELED_OUTLINE", "RUNTIME_REFUSED_SOURCES", "SAME_SIZE_PARTS_NOT_MERGED",
    "build_geometry_regions", "dimension_rects", "extract_text_anchors", "load_seed",
    "match_authority_parts", "normalize_part_label", "plan_family_groups",
    "regions_from_geometry_parts", "resolve_business_parts", "split_label_parts",
]

"""DWG 图纸 → 零件（展开件）提取与回填（Spec `packaging-dwg-parts-extraction.md`）。

以 CAD IR 的 `geometry.components`（连通分量）为**唯一**零件来源 —— 真图
（`酒盒.dwg`）只有 2 条闭合轮廓却有 402 个连通分量，"零件 = 闭合轮廓"在真图上不成立。
每个分量产出一件：展开长宽取分量 bbox，角色取件内实体图层角色的最高优先级，
证据逐条可回查。

本模块是**纯函数**：`extract()` 不落盘、不联网、不改入参、不写审计；
落盘只有 `save_parts()` 一处（版本化，同 `packaging_semantics` 的做法）。
BOM 回填（`bind_rows`）只做行级临时口径 —— 正式口径应是「零件 ↔ 模板行」的对应表。
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from ..storage.meta_backend import get_backend
from . import packaging_bom
from .cad_ir import geometry as cad_geometry

ENGINE_VERSION = "packaging-parts/1"

#: 零件文档在项目存储里的 doc key。
DOC_KEY = "packaging_parts"

#: 单件下游结论（工艺 / 成本）各自一份版本化文档 —— 与零件文档同一套 `get_doc/put_doc`
#: 范式，但**分开存**：它们是不同的产物，读回的时机与权限都不同
#: （Spec `packaging-parts-downstream-readback.md` §2.1 命名契约）。
DOC_KEY_PROCESS = "packaging_part_process"
DOC_KEY_COST = "packaging_part_cost"

#: 人工补料厚自己的版本化文档（Spec `packaging-parts-thickness-facts.md` §2.5）：
#: 与零件文档分开存 —— 补一条料厚不该换掉 `parts_id`（换了会把下游落库的结论全指歪）。
DOC_KEY_THICKNESS = "packaging_part_thickness"

#: 人工补材料自己的版本化文档（Spec `packaging-parts-in-card-and-material-fill.md` §2.2）：
#: 与料厚逐字同口径 —— 补一条材料不该换掉 `parts_id`。
DOC_KEY_MATERIAL = "packaging_part_material"

#: 过滤阈值与上限：默认值**只在这里**，不许散落在判定代码里。
DEFAULT_OPTIONS = {"min_area_mm2": 2000, "max_edge_mm": 1200,
                   "max_area_mm2": 1000000, "max_parts": 64}

#: 「可制造曲线」闭集：其余（TEXT / MTEXT / DIMENSION / INSERT / ATTRIB…）不构成零件。
CURVE_TYPES = ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE")

#: 提不出零件时的稳定原因码（前端据此说清"为什么没有零件"）。
REASON_CODES = ("edge_over_max", "area_over_max", "area_under_min", "no_curve_entity",
                "no_components", "all_filtered", "no_unit")

PART_CODE_FORMAT = "DWG-P%02d"

#: 卡片第 6 步「图纸拆出来的零件」的列（Spec `packaging-parts-in-card-and-material-fill.md`
#: §2.1 第 2 条）：**唯一**列定义，读接口随响应下发，卡片页照抄，不自己造列。
#: `可算`/`不可算原因` 的取值一律由 `processability()` 给（同判据同文案）。
CARD_COLUMNS = (
    {"key": "part_code", "label": "零件号"},
    {"key": "name", "label": "名称"},
    {"key": "role", "label": "角色"},
    {"key": "material", "label": "材料"},
    {"key": "thickness_mm", "label": "厚度(mm)"},
    {"key": "unfolded_length_mm", "label": "展开长(mm)"},
    {"key": "unfolded_width_mm", "label": "展开宽(mm)"},
    {"key": "outline_status", "label": "轮廓状态"},
    {"key": "processable_text", "label": "可算"},
    {"key": "unprocessable_reason", "label": "不可算原因"},
)

#: `filtered_reason_mix` 一件只记一次：主因次序与 Spec §2.4 的前端文案同序
#: （「面积超限 X / 长边超限 Y」）—— 面积超限是"吞并块"这一层的头号信号。
#: 每一件**每一条**过滤原因各记一次的是 `filtered_<reason>_total`（见下）。
FILTER_REASON_ACCOUNT_ORDER = ("area_over_max", "edge_over_max", "area_under_min",
                              "no_curve_entity")

#: 逐原因的件数键（一件可同时计入多个原因；与上面"一件只记一次"的两把账分开）。
FILTER_REASON_TOTAL_KEYS = {"edge_over_max": "filtered_edge_over_max_total",
                            "area_over_max": "filtered_area_over_max_total",
                            "area_under_min": "filtered_area_under_min_total",
                            "no_curve_entity": "filtered_no_curve_entity_total"}

#: 零件 id 命名空间（Spec `packaging-parts-downstream-process-and-cost.md` §2）：
#: 本版 `part_id` 逐字等于 `part_code`（图上一件就是一个零件号），不建额外映射表。
PART_ID_NAMESPACE = "packaging_parts/1"

#: 下游（工艺 / 成本）拒绝码闭集：路由据此给 409，前端据此说清缺什么。
PROCESS_REJECT_CODES = ("PACKAGING_PART_NOT_CLOSED", "PACKAGING_PART_MATERIAL_UNKNOWN",
                        "PACKAGING_PART_NOT_FOUND")

#: 件内角色优先级：一件里既有刀线又有压痕 → `cut`。
ROLE_PRIORITY = ("cut", "half_cut", "crease", "v_groove", "glue_flap", "print", "bleed",
                 "frame", "hole", "unknown")

#: 每个项目最多保留的零件文档版本数（旧版本仍可按 id 回看）。
MAX_VERSIONS = 20

#: 回填只碰这两类行（Spec C7.1）。
BINDABLE_CATEGORIES = ("box_part", "optional_part")

#: 行级绑定的规则号（留痕用，正式口径定下来之前一直带这个标记）。
BINDING_RULE_ID = "dwg_parts_row_pairing_v1"

#: 材料分类的关键词闭集（Spec 批 12 §2.5）：**闭集外一律 None** —— 未知不等于不匹配。
#: 顺序即判据顺序：先磁铁系再五金系（否则「钕铁硼磁铁」会被「铁」判成五金），
#: 纸系在丝带/布/绒之前（「海绵裱绒」按闭集顺序落到丝带/布/绒）。
MATERIAL_CLASS_KEYWORDS = (
    ("paper", ("纸", "板", "卡", "坑", "牛皮")),
    ("magnet", ("磁铁", "钕铁硼", "磁石")),
    ("metal", ("五金", "铁", "铝")),
    ("textile", ("丝带", "织带", "布", "绒")),
    ("plastic", ("EVA", "海绵", "PET", "PVC", "塑料")),
)


def _account_reason(reasons: Any) -> str:
    """一件被过滤的**主因**（一件只进 `filtered_reason_mix` 一次，Spec §2.4）。"""
    items = [_text(item) for item in (reasons or []) if _text(item)]
    for reason in FILTER_REASON_ACCOUNT_ORDER:
        if reason in items:
            return reason
    return items[0] if items else "unknown"


def material_words(label: Any) -> frozenset:
    """标签 → 命中的材质词集合（`MATERIAL_KEYWORDS` 里出现在该标签中的那些词）。

    只有集合**相交**才算"同一种材质"（Spec `packaging-parts-thickness-facts.md` §2.2）；
    集合为空表示"看不出材质"，此时不判冲突（没得比）。
    """
    text = _text(label).upper()
    if not text:
        return frozenset()
    return frozenset(word.upper() for word in MATERIAL_KEYWORDS if word.upper() in text)


def material_table_rows(value: Any) -> List[Dict[str, Any]]:
    """`options["material_table"]` → 规范化行（纯数据，Spec §2.3）。

    只认这几个字段；密度取不到就是 `None`（**不许**给默认密度）。顺序确定（按材料码 + 名称）。
    """
    rows: List[Dict[str, Any]] = []
    for item in (value or []):
        if not isinstance(item, dict):
            continue
        rows.append({"material_code": _text(item.get("material_code")) or _text(item.get("code")),
                     "name": _text(item.get("name")), "grade": _text(item.get("grade")),
                     "spec": _text(item.get("spec")), "density": _num(item.get("density"))})
    rows.sort(key=lambda row: (row["material_code"], row["name"]))
    return rows


def _material_density(row: Dict[str, Any], table: List[Dict[str, Any]]
                      ) -> Tuple[Optional[Dict[str, Any]], str]:
    """件材料标签 → 材料表里**唯一**命中的那一条（Spec §2.3）。

    命中 ≥2 条或一条都没命中 → `(None, reason)`；宁可不推，也不许挑一条。
    """
    words = material_words(row.get("material"))
    if not words:
        return None, "material_unknown"
    hits = [item for item in table
            if words & material_words(" ".join([item["name"], item["grade"], item["spec"]]))]
    hits = [item for item in hits if (_num(item.get("density")) or 0.0) > 0.0]
    if len(hits) == 1:
        return hits[0], ""
    if len(hits) >= 2:
        return None, "material_ambiguous"
    return None, "density_missing"


def _grammage_of(*texts: Any) -> Optional[float]:
    """克重（`1500g` / `350克`）—— 只从**已采纳的材料注记文本**里取（Spec §2.1）。

    `2mm` / `1.8mm` / `2.5MM` 一律不是克重（沿用 `quick-quote-10-material-gsm-attribution.md` §C2）。
    """
    for text in texts:
        match = _GRAMMAGE.search(_text(text))
        if match:
            value = _num(match.group(1))
            if value:
                return value
    return None


def _material_class(value: Any) -> Optional[str]:
    """材料原文 → 闭集里的类别；闭集外一律 `None`（Spec 批 12 §3.4：未知 ≠ 不同类）。"""
    text = _text(value).upper()
    if not text:
        return None
    for name, keywords in MATERIAL_CLASS_KEYWORDS:
        for keyword in keywords:
            if keyword.upper() in text:
                return name
    return None

UNAVAILABLE_MESSAGES = {
    "no_components": "图纸里没有可用的连通分量，请确认上传的是 2D 刀模图",
    "all_filtered": "图纸里的分量都被过滤（整版图框 / 碎线 / 无制造曲线），没有可制造的零件",
    "no_unit": "图纸单位未确认，不能给出绝对展开尺寸，请先确认图纸单位",
}

# --------------------------------------------------------------------------- #
# 真实轮廓口径（Spec `packaging-parts-true-outline.md` §3，逐字实现）
# --------------------------------------------------------------------------- #

#: 端点相接容差（mm）。**单位未确认时不得跑** —— 求出来的 "mm" 没有意义。
LOOP_TOLERANCE_MM = 1.0

#: 环至少 3 条边。
MIN_LOOP_EDGES = 3

#: 种类指纹的长度（Spec `packaging-parts-list-visibility-and-kinds.md` §2.2 第 3 步）：
#: 形状 + 尺寸 + 轮廓状态的确定性指纹取 sha256 前 12 位十六进制。
KIND_KEY_LENGTH = 12

#: 轮廓三态与尺寸来源（对外可见，下游据此说清“为什么这件的尺寸不可信”）。
OUTLINE_STATUSES = ("closed", "open", "unavailable")
SIZE_SOURCES = ("closed_outline", "component_bbox", "dwg_outline")

#: 尺寸可信两档（Spec `packaging-bom-part-size-provenance.md` §2.1）：真展开 vs 包围盒。
#: 回填行上必须能一眼认出这两类数字 —— 它们在下游长得一样时，材料费就无从解释。
SIZE_QUALITY_UNFOLDED = "unfolded"
SIZE_QUALITY_BBOX = "bbox_only"
SIZE_QUALITIES = (SIZE_QUALITY_UNFOLDED, SIZE_QUALITY_BBOX)

#: 只有这两个来源算"真展开"；其余（含缺失）一律按包围盒档，不许把没证据的数字说成展开。
UNFOLDED_SIZE_SOURCES = ("closed_outline", "dwg_outline")

#: 求环只吃这几类实体；DIMENSION / HATCH / INSERT / TEXT 一律不参与（Spec §3 第 1 步）。
LOOP_TYPES = ("LINE", "ARC", "CIRCLE", "LWPOLYLINE", "POLYLINE", "SPLINE")

#: CIRCLE 天然闭环，按这个点数采样成多边形（面积走鞋带公式的近似）。
CIRCLE_SAMPLES = 32

#: 求环的计算预算：真图 402 个分量，必须给硬上限（结果仍确定，只是到点就停）。
MAX_LOOP_CYCLES = 256
MAX_LOOP_STATES = 20000

#: 逐件诊断的墙钟预算（毫秒）（Spec `packaging-parts-pipeline-time-budget.md` §3.5）。
#: 超了**不许抛异常、不许挂住**：诊断里带 `budget_exceeded=True`，结论仍走既有开线原因闭集。
TIME_BUDGET_MS = 2000

# —— 重复边折叠与外轮廓重判（Spec `packaging-parts-outline-chaining.md` §2）——
#: 真刀模图里同一条边常被重复画 2～4 份。环搜索把这些重复边当成**不同的边**，分支爆炸后撞上
#: 预算提前中止，却对外报"图纸没闭合"——把"我们没算完"说成了"图纸的结论"。
#: 折叠只作用于**找环**：证据（entity_ids）仍然逐条保留。
CHAIN_RULE_ID = "part_outline_chaining_v1"
EDGE_COLLAPSE_TOLERANCE_MM = 1.0

#: rescue 准入条件：折叠后最大环的 bbox 必须覆盖分量 bbox 的这个比例才算外轮廓。
#: 依据：已验收的闭合件里有 12 件的环只覆盖分量 bbox 的 49%～95%（那是内圈/局部环），
#: 所以覆盖率只能当"救判错的件"的准入条件，不能反过来重算已闭合件。
OUTLINE_BBOX_COVER_RATIO = 0.95

#: 开线原因闭集（顺序即判定顺序，Spec §2.4）；笼统的"没找到闭合环"从代码里消失。
OUTLINE_OPEN_REASONS = ("no_curve_entity", "unit_unconfirmed", "loop_budget_exhausted",
                        "odd_endpoints", "loop_too_small")

#: 未闭合件的两条**出路**（Spec `packaging-open-outline-part-needs-a-way-out.md` §2.2）：
#: · `loop_budget_exhausted` = 我们**没算完**（可重试 → 放大额度只重算这一件）；
#: · 其余 = 图纸**真的**没有闭合轮廓 / 单位没定（要人处理 → 改图重传，或按包围盒签字确认）。
OUTLINE_RETRYABLE_REASONS = ("loop_budget_exhausted",)
OUTLINE_MANUAL_REASONS = ("no_curve_entity", "unit_unconfirmed", "odd_endpoints",
                          "loop_too_small")

#: 两条出路的动作名（前端据 `outline_advice()["action"]` 决定按哪个出口走）。
OUTLINE_ACTION_RECOMPUTE = "recompute"
OUTLINE_ACTION_CONFIRM = "confirm_bbox"

#: 单件重算的搜索额度放大倍数（Spec §2.3(b)）：只放大**这一件**的搜索额度；
#: 判据、闭集与 `_open_outline_reason()` 的次序一律不变，额度再大也可能仍报同一原因。
RECOMPUTE_BUDGET_SCALE = 8
RECOMPUTE_BUDGET_SCALE_MAX = 32
RECOMPUTE_RULE_ID = "part_outline_recompute_v1"

#: 件级轮廓出路的两种留痕（Spec §2.4）。**几何状态一个字不改**：`closed` 只能由几何判定
#: 给出；人签的字另立一笔（`outline_confirmation`），重算的真结论另立一笔（`outline_recompute`）。
MANUAL_OUTLINE_KIND = "manual_bbox"
RECOMPUTED_OUTLINE_KIND = "recomputed_outline"
OUTLINE_EXIT_KINDS = (MANUAL_OUTLINE_KIND, RECOMPUTED_OUTLINE_KIND)
MANUAL_OUTLINE_RULE_ID = "part_outline_manual_bbox_v1"

#: 件级轮廓出路自己的版本化文档（Spec §2.3，与「补材料」/「补料厚」逐字同范式）：
#: 签字与重算结论**不换** `parts_id`，读时合回零件行 —— 换 id 会把下游结论全指歪。
DOC_KEY_OUTLINE = "packaging_part_outline"


# --------------------------------------------------------------------------- #
# 图纸标注里的材料 / 厚度（Spec `packaging-parts-downstream-process-and-cost.md` §3）
# --------------------------------------------------------------------------- #
# 材料与厚度**不是猜出来的**，是图纸自己写着的东西（标题栏的「包装材料说明」与引出标注）。
# 真图上它是**成组**写着的（整体材料说明 + 分件引出标注 + 分区注记），所以取法是四层归属
# （Spec `packaging-parts-material-attribution.md` §2），每层都留痕到 row 的
# material_source / thickness_source：
#   1. 件级引出标注（贴着这一件、且不同时贴 ≥2 件）；
#   2. 成组注记（KV 文本 / 分区注记，一条覆盖多件）；
#   3. 图层名带材料（只给材质）；
#   4. 需求 3.3 的整盒口径兜底（必须标 needs_confirmation）。
# 四层都取不到就留空（缺料由 processability 拒绝并说清缺什么，绝不用默认值硬算）。
MATERIAL_KEYWORDS = ("灰板", "白卡", "粉灰", "铜版纸", "双铜", "卡纸", "牛皮纸", "坑纸",
                     "瓦楞", "PET", "光银", "哑胶", "E坑", "BC坑", "B坑", "裱")

#: 板材厚度可信区间（mm）：超出这个范围的数字是外形尺寸，不是料厚。
THICKNESS_MIN_MM = 0.2
THICKNESS_MAX_MM = 20.0

#: 标注文本留痕长度上限。
NOTE_TEXT_MAX = 60

#: 材料与厚度归属的规则号与来源闭集（顺序即优先级，Spec
#: `packaging-parts-material-attribution.md` §2）。真图上材料是**成组**写着的
#: （整体材料说明 + 分件引出标注 + 分区注记），"一件一件按最近标注取"必然覆盖不到。
MATERIAL_ATTRIBUTION_RULE_ID = "part_material_attribution_v1"
ATTRIBUTION_KINDS = ("part_note", "group_note", "layer_name", "requirement_default")

#: 料厚来源的**合法闭集**（Spec `packaging-parts-thickness-facts.md` §2.4）：在四层归属之外，
#: 多出「克重 ÷ 密度 推出来的」与「人补的」两种 —— 这两类都必须能被下游一眼认出。
DERIVED_THICKNESS_KIND = "derived_from_gsm_density"
MANUAL_THICKNESS_KIND = "manual"
THICKNESS_SOURCE_KINDS = ATTRIBUTION_KINDS + (DERIVED_THICKNESS_KIND, MANUAL_THICKNESS_KIND)

#: 材料来源里**多出来的**那一类：人补的（Spec `packaging-parts-in-card-and-material-fill.md`
#: §2.2）。它与料厚的 `manual` 同形，但**不算图纸证据** —— `material_evidence_ratio`
#: 只数图纸写着的材料，人工补的必须能被一眼分开（否则"覆盖率"会被签字撑起来）。
MANUAL_MATERIAL_KIND = "manual"
MATERIAL_SOURCE_KINDS = ATTRIBUTION_KINDS + (MANUAL_MATERIAL_KIND,)
NON_EVIDENCE_MATERIAL_KINDS = ("requirement_default", MANUAL_MATERIAL_KIND)

#: 克重 → 料厚的换算式（Spec §2.1，逐字）：`thickness_mm = gsm / (density * 1000)`。
#: `gsm` 单位 g/㎡、`density` 单位 g/cm³，四舍五入到 3 位小数。**不许**放行业经验厚度表。
GRAMMAGE_DENSITY_DIVISOR = 1000.0
DERIVED_THICKNESS_PRECISION = 3

#: 「有材料、却仍然没有料厚」的原因闭集（Spec §2.4）—— 推不出来必须看得见，不许静默。
THICKNESS_UNRESOLVED_REASONS = ("density_missing", "material_ambiguous", "no_grammage",
                                "material_unknown")

#: 料厚不许跨材料串味（Spec §2.2）：注记的材质词与该件的材料标签**不相交** → 拒绝采用并留痕。
THICKNESS_CONFLICT_REASON = "thickness_material_conflict"

#: 缺口原因账的闭集（Spec `packaging-parts-coverage-truthfulness.md` §2.2）：每一件没有材料 /
#: 没有料厚的零件都必须说得出是**哪一类**原因，而不是只留一个数字。一件只记一次。
#: `unknown` 长期必须为 0 —— 不为 0 就是原因判别有漏。
MATERIAL_GAP_REASONS = ("no_closed_outline", "no_material_note", "material_ambiguous", "unknown")
THICKNESS_GAP_REASONS = ("no_closed_outline", "no_thickness_note", "grammage_only",
                         "material_missing", "material_ambiguous", "unknown")

#: 件级半径 = min(0.25 × 对角线, 硬上限)：0.25 在整版大件上会放大到 160mm+，把图纸整体
#: 材料说明误归给大件（实测 4 件 open 大件全错），所以必须加硬上限。
NOTE_DISTANCE_RATIO = 0.25
NOTE_DISTANCE_MAX_MM = 150.0

#: 成组注记的覆盖半径（按**轴对齐方框**判定：两个方向的最大间隙 ≤ 该值）。
GROUP_NOTE_RADIUS_MM = 300.0

#: 分区词（盒结构的"哪一块"）；出现在注记里就从原文摘进 `attribution.partition`。
PARTITION_KEYWORDS = ("左盒", "右盒", "内盒", "外盒", "盒盖", "底盒", "底板", "围条", "内托",
                      "面纸", "衬纸")

#: 需求 3.3 的整盒材料口径（键名取自 `industry_templates.PACKAGING_SPEC`，不另立一套）。
REQUIREMENT_MATERIAL_FIELDS = ("grey_board", "grey_board_thickness",
                               "face_paper", "face_paper_gsm", "lining_paper")

#: 最近两条候选的距离差 ≤ 该值且取值不同 → 弃权（Spec §2.1 第 3 条）。
AMBIGUOUS_DISTANCE_MM = 5.0

_THICKNESS_EXPLICIT = re.compile(r"厚(?:度)?\s*[:：]?\s*(\d+(?:\.\d+)?)")
_THICKNESS_MM = re.compile(r"(\d+(?:\.\d+)?)\s*(?:mm|MM)(?![0-9A-Za-z])")
#: KV 前缀（`名称：` / `材料：` / `材质：`）—— 必须切掉，不许把整句塞进 material。
_MATERIAL_KV = re.compile(r"(名称|材料|材质)\s*[:：]")
#: 克重（`350g` / `350克`）：它跟在材质词前后都是规格，统一挪到标签后面。
_GRAMMAGE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:g|G|克)(?![0-9A-Za-z])")
#: 标签尾部要剪掉的标点。
_LABEL_TRIM = " \t，,。；;、:：/|"


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _text(value: Any) -> str:
    return str(value or "").strip()


def _trim_number(value: Any) -> str:
    """数字 → 不带多余小数位的文本（350.0 → "350"；2.5 → "2.5"）。"""
    number = _num(value)
    if number is None:
        return ""
    if float(number).is_integer():
        return str(int(number))
    return ("%s" % number).rstrip("0").rstrip(".")


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 3)


def _options(options: Any) -> Dict[str, float]:
    merged = dict(DEFAULT_OPTIONS)
    if isinstance(options, dict):
        for key, value in options.items():
            if key in merged and _num(value) is not None:
                merged[key] = _num(value)
    return merged


def _layer_key(name: Any) -> str:
    return _text(name).upper()


def _bbox_of(component: Dict[str, Any], entities: List[Dict[str, Any]]) -> Optional[List[float]]:
    """分量外接矩形：优先用分量自带 bbox，缺失时由件内实体 bbox 合并（仍缺失 → None）。"""
    raw = component.get("bbox")
    values = [_num(item) for item in raw] if isinstance(raw, (list, tuple)) else []
    if len(values) == 4 and all(item is not None for item in values):
        return [float(item) for item in values]
    boxes = [entity.get("bbox") for entity in entities if isinstance(entity.get("bbox"), list)]
    boxes = [[_num(item) for item in box] for box in boxes]
    boxes = [box for box in boxes if len(box) == 4 and all(item is not None for item in box)]
    if not boxes:
        return None
    return [min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)]


def _layer_roles(ir: Dict[str, Any], semantics: Any) -> Dict[str, str]:
    """图层名（大写）→ 角色。有语义文档就用它，否则现算一份（同一套规则）。

    返回口径逐字不变；出处（`role_lookup_state()`）由同一趟查找派生 —— 不许在模块里
    写第二套"猜角色"的逻辑。
    """
    roles, _state = _resolve_layer_roles(ir, semantics)
    return roles


#: 角色出处的闭集（Spec `packaging-parts-role-lookup-disclosure.md` §2.1）。
ROLE_LOOKUPS = ("semantics", "ir_layers", "unavailable")

#: 三态与"老文档没记出处"各自的人话（Spec §2.1 / §2.3 / §2.4）。
ROLE_LOOKUP_MESSAGES = {
    "unavailable": "图层角色这一次没算出来，请稍后重试；零件尺寸不受影响。",
    "ir_layers": "语义层这一次没算出来（%s），角色取自图纸 IR 自带的图层角色。",
    "unknown_layers": "这些图层名认不出角色：%s（可补规则或人工指定）。",
    "missing": "这份零件文档没记录角色是怎么查出来的（本批之前落库的），无法判断是语义层给的还是兜底来的。",
}


def _ir_layer_roles(ir: Dict[str, Any]) -> Dict[str, str]:
    """IR 图层自带的角色（`role` / `inferred_role`）；没有就给 `unknown`（绝不猜）。"""
    roles: Dict[str, str] = {}
    for row in ir.get("layers") or []:
        if not isinstance(row, dict):
            continue
        key = _layer_key(row.get("name"))
        if key:
            roles[key] = _text(row.get("role") or row.get("inferred_role")) or "unknown"
    return roles


def _role_lookup_message(source: str, reason: str, unknown_layers: List[str]) -> str:
    if source == "unavailable":
        return ROLE_LOOKUP_MESSAGES["unavailable"] + (("（%s）" % reason) if reason else "")
    if source == "ir_layers":
        return ROLE_LOOKUP_MESSAGES["ir_layers"] % (reason or "未知原因")
    if unknown_layers:
        return ROLE_LOOKUP_MESSAGES["unknown_layers"] % "、".join(unknown_layers)
    return ""


def _resolve_layer_roles(ir: Dict[str, Any], semantics: Any):
    """查角色的**唯一一趟**：返回 `(图层名(大写) → 角色, 出处 state)`（Spec §2.1）。

    出处三态与 `_layer_roles()` 的返回口径由同一趟查找派生：

    - `semantics`：语义文档给了角色（传入的 / 现算成功的）；
    - `ir_layers`：语义层失败，但图纸 IR 图层**真的**给出了至少一个已知角色；
    - `unavailable`：语义层失败且 IR 也没有角色 —— 这就是"这一次没查成"那一态。

    `reason` 只在语义层抛异常时给异常类名；`unknown_layers` 大写去重升序。
    """
    payload = ir if isinstance(ir, dict) else {}
    doc = semantics if isinstance(semantics, dict) else None
    reason = ""
    if doc is None:
        try:                                              # 现算：只读规则文件，不落盘
            from . import packaging_semantics
            doc = packaging_semantics.analyze(payload)
        except Exception as exc:                          # noqa: BLE001 - 算不出来也要出零件
            doc = None
            reason = type(exc).__name__
    roles: Dict[str, str] = {}
    doc_unknown: List[str] = []
    if isinstance(doc, dict):
        for row in doc.get("layers") or []:
            if not isinstance(row, dict):
                continue
            key = _layer_key(row.get("name"))
            if not key:
                continue
            roles[key] = _text(row.get("role")) or "unknown"
            # `role_source == "none"` 是"规则里没有这个图层名"的唯一证据（Spec §1 第 2 条）：
            # 以前这里只取 `role`，把这条证据扔了。
            if roles[key] == "unknown" or _text(row.get("role_source")) == "none":
                doc_unknown.append(key)
    if roles:
        unknown_layers = sorted(set(doc_unknown))
        return roles, {"source": "semantics", "reason": "",
                       "unknown_layers": unknown_layers,
                       "message": _role_lookup_message("semantics", "", unknown_layers)}
    # 语义层不可用（或压根没给角色）时的兜底：IR 里已经带角色就用它，否则 unknown（绝不猜）。
    fallback = _ir_layer_roles(payload)
    known = [key for key, role in fallback.items() if role and role != "unknown"]
    source = "ir_layers" if known else "unavailable"
    names = sorted(fallback)
    unknown_layers = (names if source == "unavailable"
                      else sorted(key for key, role in fallback.items()
                                  if not role or role == "unknown"))
    return fallback, {"source": source, "reason": reason,
                      "unknown_layers": unknown_layers,
                      "message": _role_lookup_message(source, reason, unknown_layers)}


def role_lookup_state(ir: Dict[str, Any], semantics: Any = None) -> Dict[str, Any]:
    """这一次"零件角色是怎么查出来的"（Spec §2.1）：闭集三态 + 原因 + 认不出的图层名 + 人话。

    **不抛异常**（语义层失败同样要能给出 `unavailable`），也不在 `source == "semantics"`
    时再调一次 `analyze()` —— 与 `_layer_roles()` 走同一趟查找。
    """
    _roles, state = _resolve_layer_roles(ir, semantics)
    return state


def _role_index(role: str) -> int:
    try:
        return ROLE_PRIORITY.index(role)
    except ValueError:
        return len(ROLE_PRIORITY) - 1


def _known_evidence(ir: Dict[str, Any]) -> Any:
    return ir.get("evidence") if isinstance(ir.get("evidence"), dict) else {}


# --------------------------------------------------------------------------- #
# 0b 图纸标注 → 材料 / 厚度候选（纯函数，不读库不联网）
# --------------------------------------------------------------------------- #
def _note_thickness(text: str, has_material: bool) -> Optional[float]:
    """标注里的料厚：显式「厚度2MM」优先，其次「1.8mm 灰板」这种带材料的写法。"""
    match = _THICKNESS_EXPLICIT.search(text)
    if match:
        value = _num(match.group(1))
    elif has_material:
        match = _THICKNESS_MM.search(text)
        value = _num(match.group(1)) if match else None
    else:
        value = None
    if value is None or not (THICKNESS_MIN_MM <= value <= THICKNESS_MAX_MM):
        return None
    return value


def _material_label(text: str) -> Tuple[str, str]:
    """标注文本 → (material 短标签, partition)（Spec §3 结构化切分）。

    - KV 前缀（`名称：`/`材料：`/`材质：`）切掉，优先取 `材料：` 那一段；
    - 标签 = 从**材质词**开始到该段结尾（含后面的复合词，如 `灰板裱光银纸`）；
    - 克重（`350g`）统一挪到标签后面（`350g粉灰` → `粉灰 350g`）；
    - 分区词从**原文**摘出写进 partition，绝不因为它是分区词就把材质词删掉。
    """
    raw = " ".join(_text(text).split())
    if not raw:
        return "", ""
    focus = raw
    segments = [segment for segment in _MATERIAL_KV.split(raw) if segment]
    # `re.split` 给出 [前置, 键, 值, 键, 值]：值在奇数位；优先 `材料/材质` 那一对。
    pairs = [(segments[index - 1], segments[index]) for index in range(1, len(segments))]
    for key, value in pairs:
        if key in ("材料", "材质") and value.strip():
            focus = value.strip()
            break
    else:
        if pairs and pairs[0][1].strip():
            focus = pairs[0][1].strip()
    label = ""
    index = min((focus.find(word) for word in MATERIAL_KEYWORDS if focus.find(word) >= 0),
                default=-1)
    if index >= 0:
        label = focus[index:].strip().strip(_LABEL_TRIM)
        gram = _GRAMMAGE.search(focus[:index]) or _GRAMMAGE.search(raw)
        if gram and not _GRAMMAGE.search(label):
            label = "%s %sg" % (label, _trim_number(_num(gram.group(1))))
    partition = ""
    best_at: Optional[int] = None
    for word in PARTITION_KEYWORDS:
        match = re.search(re.escape(word) + r"(\d*)", raw)
        if not match:
            continue
        if best_at is None or match.start() < best_at or (
                match.start() == best_at and len(match.group(0)) > len(partition)):
            best_at = match.start()
            partition = match.group(0)
    return label, partition


def _note_material(text: str) -> str:
    """标注里的材料短标签：命中材料词才认（「面纸转越南」这种备注不是材料）。"""
    return _material_label(text)[0]


def drawing_notes(ir: Dict[str, Any]) -> List[Dict[str, Any]]:
    """图纸标注 → 材料/厚度候选。顺序只由 entity_id 决定（同一份图纸两次跑逐字相同）。"""
    rows: List[Dict[str, Any]] = []
    for item in (ir.get("texts") or []):
        if not isinstance(item, dict):
            continue
        text = " ".join(_text(item.get("normalized_text")).split())
        if not text:
            continue
        material = _note_material(text)
        thickness = _note_thickness(text, bool(material))
        if not material and thickness is None:
            continue
        _label, partition = _material_label(text)
        position = item.get("position")
        position = [float(value) for value in position] \
            if isinstance(position, (list, tuple)) and len(position) >= 2 else None
        rows.append({"entity_id": _text(item.get("entity_id")),
                     "text": text[:NOTE_TEXT_MAX], "material": material,
                     "thickness_mm": thickness, "position": position,
                     "partition": partition,
                     # 成组注记的判据（Spec §2 层 2）：能解析出 `材料：X` 或带分区词。
                     "group": bool(_MATERIAL_KV.search(text)) or bool(partition),
                     "evidence_ref": _text(item.get("evidence_ref"))})
    rows.sort(key=lambda row: (row["entity_id"], row["text"]))
    return rows


def _distance_to_bbox(position: List[float], bbox: List[float]) -> float:
    """点到包围盒的欧氏距离（件内为 0）。"""
    x, y = float(position[0]), float(position[1])
    x0, x1 = sorted((float(bbox[0]), float(bbox[2])))
    y0, y1 = sorted((float(bbox[1]), float(bbox[3])))
    return math.hypot(max(x0 - x, 0.0, x - x1), max(y0 - y, 0.0, y - y1))


def _part_note_radius(bbox: List[float]) -> float:
    """件级半径 = min(0.25 × 对角线, `NOTE_DISTANCE_MAX_MM`)（Spec §2 层 1）。"""
    diagonal = math.hypot(abs(float(bbox[2]) - float(bbox[0])),
                          abs(float(bbox[3]) - float(bbox[1])))
    return min(NOTE_DISTANCE_RATIO * diagonal, NOTE_DISTANCE_MAX_MM)


def _note_reach(position: List[float], bbox: List[float]) -> float:
    """注记点到包围盒的**轴对齐方框**距离（两个方向间隙的最大值）—— 成组注记按它判覆盖。"""
    x, y = float(position[0]), float(position[1])
    x0, x1 = sorted((float(bbox[0]), float(bbox[2])))
    y0, y1 = sorted((float(bbox[1]), float(bbox[3])))
    return max(max(x0 - x, 0.0, x - x1), max(y0 - y, 0.0, y - y1))


def _drop_conflicting_thickness(item: Dict[str, Any],
                                 picked: List[Tuple[float, int, Dict[str, Any], Any]]
                                 ) -> List[Tuple[float, int, Dict[str, Any], Any]]:
    """把「材质词与本件材料不相交」的料厚候选剔掉，并逐条留痕（Spec §2.2）。

    注记不带材质词（显式 `厚度2MM`）或本件材料未定 → 不判冲突，照旧采用（没得比）。
    """
    part_words = material_words(item.get("material"))
    if not part_words:
        return picked
    kept: List[Tuple[float, int, Dict[str, Any], Any]] = []
    for candidate in picked:
        note = candidate[2]
        note_words = material_words(note.get("material"))
        if note_words and not (note_words & part_words):
            item["conflicts"].append({
                "reason": THICKNESS_CONFLICT_REASON,
                "note_ref": _text(note.get("evidence_ref")),
                "note_text": _text(note.get("text")),
                "note_material": _text(note.get("material")),
                "part_material": _text(item.get("material")),
            })
            continue
        kept.append(candidate)
    return kept


def _pick_candidate(candidates: List[Tuple[float, int, Dict[str, Any], Any]]
                    ) -> Tuple[Any, Optional[Tuple[float, int, Dict[str, Any], Any]], bool]:
    """同一层的候选 → `(值, 选中的那条, 是否弃权)`。

    最近者优先（距离并列取 entity_id 小的那条）；最近两条距离差
    `<= AMBIGUOUS_DISTANCE_MM` 且**取值不同** → 弃权（`value=None`、`ambiguous=True`），
    绝不许"取最近那条"了事（Spec §2.1 第 3 条）。
    """
    usable = sorted([item for item in candidates if item[3] not in (None, "")],
                    key=lambda item: (item[0], item[1]))
    if not usable:
        return None, None, False
    best = usable[0]
    if (len(usable) > 1 and (usable[1][0] - best[0]) <= AMBIGUOUS_DISTANCE_MM
            and usable[1][3] != best[3]):
        return None, None, True
    return best[3], best, False


def _requirement_defaults(requirement: Any) -> Dict[str, Any]:
    """需求 3.3 → 整盒兜底口径（材料取面纸+克重，厚度取灰板厚度；取不到就不给）。"""
    data = requirement if isinstance(requirement, dict) else {}
    face = _text(data.get("face_paper"))
    gsm = _num(data.get("face_paper_gsm"))
    board = _text(data.get("grey_board"))
    lining = _text(data.get("lining_paper"))
    out: Dict[str, Any] = {}
    if face:
        out["material"] = "%s %sg" % (face, _trim_number(gsm)) if gsm else face
        out["material_ref"] = "requirement.face_paper"
    elif board:
        out["material"], out["material_ref"] = board, "requirement.grey_board"
    elif lining:
        out["material"], out["material_ref"] = lining, "requirement.lining_paper"
    thickness = _num(data.get("grey_board_thickness"))
    if thickness and THICKNESS_MIN_MM <= thickness <= THICKNESS_MAX_MM:
        out["thickness_mm"] = thickness
        out["thickness_ref"] = "requirement.grey_board_thickness"
    return out


def attribute_materials(rows: Any, notes: Any, *, requirement: Any = None,
                        material_table: Any = None) -> Dict[str, Any]:
    """四层材料归属（纯函数）→ `{component_id: 归属结果}`（Spec §2）。

    顺序即优先级：件级引出标注 > 成组注记（半径内 / 整图重复一致）> 图层名 > 需求整盒口径。
    每一层都**只填还没定下的字段**（跨层不许倒挂），并且只在 `outline_status == "closed"` 的件上
    生效 —— 非闭合件的"最近标注"是偶然命中（实测 9 件全错），宁可不填。
    """
    parts = [row for row in (rows or []) if isinstance(row, dict)]
    notes = [note for note in (notes or []) if isinstance(note, dict)]
    notes = sorted(notes, key=lambda note: (_text(note.get("entity_id")), _text(note.get("text"))))
    state: Dict[str, Dict[str, Any]] = {}
    for row in parts:
        cid = _text(row.get("component_id"))
        if not cid:
            continue
        bbox = row.get("bbox") if isinstance(row.get("bbox"), (list, tuple)) else None
        state[cid] = {
            "component_id": cid, "bbox": list(bbox) if bbox else None,
            "closed": _text(row.get("outline_status")) == "closed",
            "layers": [_text(name) for name in (row.get("layers") or []) if _text(name)],
            "material": None, "thickness_mm": None,
            "material_source": None, "thickness_source": None,
            "needs_confirmation": False, "assumption_refs": [], "note_refs": [],
            "notes": [], "covers": [], "partition": "", "kind": "",
            "conflicts": [], "unresolved": [],
            "done": set(),  # 已判定（含"蓄意弃权"）的字段：下层不许再填
        }

    def _apply(cid: str, kind: str, candidates: List[Tuple[float, int, Dict[str, Any], Any]],
               *, covers: Optional[List[str]] = None) -> None:
        item = state[cid]
        for field in ("material", "thickness_mm"):
            if field in item["done"]:
                continue
            picked = [(distance, order, note, note.get(field))
                      for distance, order, note, _value in candidates if note.get(field)]
            if field == "thickness_mm":
                picked = _drop_conflicting_thickness(item, picked)
            value, best, ambiguous = _pick_candidate(picked)
            if ambiguous:
                item["done"].add(field)
                item["notes"].append("ambiguous:%d" % len(picked))
                continue
            if best is None:
                continue
            distance, order, note, _value = best
            item[field] = value
            source = {"kind": kind, "text": _text(note.get("text")),
                      "evidence_ref": _text(note.get("evidence_ref")),
                      "distance_mm": _round(distance)}
            if _text(note.get("partition")):
                source["partition"] = _text(note.get("partition"))
            if covers is not None:
                source["covers"] = list(covers)
            item["%s_source" % ("material" if field == "material" else "thickness")] = source
            item["done"].add(field)
            if not item["kind"]:
                item["kind"] = kind
            if not item["partition"]:
                item["partition"] = _text(note.get("partition"))
            if covers is not None:
                item["covers"] = list(covers)
            ref = _text(note.get("evidence_ref"))
            if ref:
                item["note_refs"].append(ref)

    # —— 层 1：件级引出标注（一条注记同时贴 ≥2 件时不是件级标注，交给层 2）——
    reached: Dict[int, Dict[str, float]] = {}
    for order, note in enumerate(notes):
        position = note.get("position")
        if not isinstance(position, (list, tuple)) or len(position) < 2:
            continue
        for cid in sorted(state):
            item = state[cid]
            if not item["closed"] or not item["bbox"]:
                continue
            distance = _distance_to_bbox([float(position[0]), float(position[1])], item["bbox"])
            if distance <= _part_note_radius(item["bbox"]):
                reached.setdefault(order, {})[cid] = distance
    single: Dict[str, List[Tuple[float, int, Dict[str, Any], Any]]] = {}
    for order, hits in sorted(reached.items()):
        if len(hits) != 1:
            continue
        cid, distance = next(iter(hits.items()))
        single.setdefault(cid, []).append((distance, order, notes[order], None))
    for cid in sorted(single):
        _apply(cid, "part_note", single[cid])

    # —— 层 2：成组注记（KV 文本 / 分区注记），一条覆盖多件，covers 逐件列全 ——
    for order, note in enumerate(notes):
        if not note.get("group"):
            continue
        position = note.get("position")
        if not isinstance(position, (list, tuple)) or len(position) < 2:
            continue
        point = [float(position[0]), float(position[1])]
        hits = sorted(cid for cid in state
                      if state[cid]["closed"] and state[cid]["bbox"]
                      and _note_reach(point, state[cid]["bbox"]) <= GROUP_NOTE_RADIUS_MM)
        if not hits:
            continue
        for cid in hits:
            distance = _note_reach(point, state[cid]["bbox"])
            _apply(cid, "group_note", [(distance, order, note, None)], covers=hits)

    # —— 层 2 的第二档：整图重复一致的口径（Spec §2.2）——
    # 真图的"整图材料说明"常常离所有零件都很远（实测 圆盘盒.dwg 的 `402X50.5MM高/厚度2MM`
    # 离最近的件 > 600mm），按半径永远够不到；但它会在**多条注记里重复出现同一个取值**，
    # 这就是"整图口径"的证据。只在"该字段全图只有一个取值且至少 2 条注记重复它"时生效
    # （单条孤证不算：那既可能是件级标注够不到，也可能是别人家的说明，宁可留空）。
    closed_ids = sorted(cid for cid in state if state[cid]["closed"] and state[cid]["bbox"])
    for field, key in (("material", "material"), ("thickness_mm", "thickness")):
        counted: Dict[Any, int] = {}
        for note in notes:
            value = note.get(field)
            if value in (None, ""):
                continue
            try:
                counted[value] = counted.get(value, 0) + 1
            except TypeError:  # 取值不可哈希（理论上不会）→ 跳过，宁可不兜底
                counted = {}
                break
        if len(counted) != 1:
            continue
        value, repeats = next(iter(counted.items()))
        if repeats < 2:
            continue
        ref_note = next(note for note in notes if note.get(field) == value)
        for cid in closed_ids:
            item = state[cid]
            if field in item["done"] or item[field] not in (None, ""):
                continue
            item[field] = value
            item["%s_source" % key] = {
                "kind": "group_note", "text": _text(ref_note.get("text")),
                "evidence_ref": _text(ref_note.get("evidence_ref")),
                "distance_mm": None, "covers": list(closed_ids)}
            item["done"].add(field)
            item["covers"] = list(closed_ids)
            item["notes"].append("drawing_wide:%s" % field)
            if not item["kind"]:
                item["kind"] = "group_note"
            ref = _text(ref_note.get("evidence_ref"))
            if ref:
                item["note_refs"].append(ref)

    # —— 层 3：图层名带材料（只给材质，不许给厚度）——
    for cid in sorted(state):
        item = state[cid]
        if not item["closed"] or "material" in item["done"]:
            continue
        for name in item["layers"]:
            if any(word in name for word in MATERIAL_KEYWORDS):
                item["material"] = name
                item["material_source"] = {"kind": "layer_name", "text": name,
                                           "evidence_ref": "ev:L:%s" % name,
                                           "distance_mm": None}
                item["done"].add("material")
                if not item["kind"]:
                    item["kind"] = "layer_name"
                break

    # —— 层 4：需求整盒口径兜底（必须逐件可见：needs_confirmation + assumption_refs）——
    defaults = _requirement_defaults(requirement)
    for cid in sorted(state):
        item = state[cid]
        if not item["closed"]:
            continue
        for field, key in (("material", "material"), ("thickness_mm", "thickness")):
            if field in item["done"] or defaults.get(field) in (None, ""):
                continue
            ref = _text(defaults.get("%s_ref" % key)) or "requirement"
            item[field] = defaults[field]
            item["%s_source" % key] = {"kind": "requirement_default", "text": ref,
                                       "evidence_ref": ref, "distance_mm": None}
            item["done"].add(field)
            item["needs_confirmation"] = True
            item["assumption_refs"].append(ref)
            if not item["kind"]:
                item["kind"] = "requirement_default"

    # —— 层 5：克重 ÷ 密度 推料厚（Spec `packaging-parts-thickness-facts.md` §2.1）——
    # 料厚是下游（工艺 / 成本 / 3D 挤出）共同的卡点：克重能推的必须推出来并**留痕**，
    # 推不出来的必须看得见（`attribution.thickness_unresolved`）—— 绝不给默认厚度、
    # 绝不放纸类"行业经验值"表。推导只走 `options["material_table"]`（不读库、不联网）。
    table = material_table_rows(material_table)
    for cid in sorted(state):
        item = state[cid]
        if not item["closed"]:
            continue
        if item["thickness_mm"] is not None or "thickness_mm" in item["done"]:
            continue
        if not item["material"]:
            item["unresolved"].append({"reason": "material_unknown", "material": "",
                                       "gsm": None, "material_code": ""})
            continue
        source = item["material_source"] if isinstance(item["material_source"], dict) else {}
        gsm = _grammage_of(source.get("text"), item["material"])
        if not gsm:
            item["unresolved"].append({"reason": "no_grammage",
                                       "material": _text(item["material"]),
                                       "gsm": None, "material_code": ""})
            continue
        entry, reason = _material_density(item, table)
        if entry is None:
            item["unresolved"].append({"reason": reason, "material": _text(item["material"]),
                                       "gsm": gsm, "material_code": ""})
            continue
        density = _num(entry.get("density"))
        distance = _num(source.get("distance_mm"))
        item["thickness_mm"] = round(gsm / (density * GRAMMAGE_DENSITY_DIVISOR),
                                     DERIVED_THICKNESS_PRECISION)
        item["thickness_source"] = {
            "kind": DERIVED_THICKNESS_KIND, "text": _text(source.get("text")),
            "evidence_ref": _text(source.get("evidence_ref")),
            "gsm": gsm, "density": density,
            "material_code": _text(entry.get("material_code")),
            "distance_mm": _round(distance) if distance is not None else None,
        }
        item["done"].add("thickness_mm")
        # 推导值能用于 3D 预览与待确认报价，但**不得**被当成已确认事实（Spec §2.1）。
        item["needs_confirmation"] = True
        if not item["kind"]:
            item["kind"] = DERIVED_THICKNESS_KIND
        ref = _text(source.get("evidence_ref"))
        if ref:
            item["note_refs"].append(ref)

    out: Dict[str, Any] = {}
    for cid, item in state.items():
        conflicts: List[Dict[str, Any]] = []
        seen_conflicts = set()
        for row in sorted(item["conflicts"],
                          key=lambda entry: (entry["note_ref"], entry["note_text"])):
            key = (row["note_ref"], row["note_text"])
            if key in seen_conflicts:
                continue
            seen_conflicts.add(key)
            conflicts.append(row)
        unresolved = sorted(item["unresolved"],
                            key=lambda entry: (entry["reason"], entry["material"]))
        out[cid] = {
            "material": item["material"], "thickness_mm": item["thickness_mm"],
            "material_source": item["material_source"],
            "thickness_source": item["thickness_source"],
            "needs_confirmation": bool(item["needs_confirmation"]),
            "assumption_refs": sorted(set(item["assumption_refs"])),
            "note_refs": sorted(set(item["note_refs"])),
            "attribution": {"rule_id": MATERIAL_ATTRIBUTION_RULE_ID,
                            "kind": item["kind"] or None,
                            "partition": item["partition"],
                            "covers": list(item["covers"]),
                            "notes": list(item["notes"]),
                            # 推不出来的必须看得见；串味被拒的必须能回查注记（Spec §2.2 / §2.4）。
                            "thickness_unresolved": unresolved,
                            "thickness_material_conflict": conflicts},
        }
    return out


# --------------------------------------------------------------------------- #
# 1 提取（纯函数）
# --------------------------------------------------------------------------- #
def _quant_key(point: Tuple[float, float]) -> Tuple[int, int]:
    """端点按 LOOP_TOLERANCE_MM 量化后归并（Spec §3 第 2 步）。"""
    return (int(round(float(point[0]) / LOOP_TOLERANCE_MM)),
            int(round(float(point[1]) / LOOP_TOLERANCE_MM)))


def _entity_chains(entity: Dict[str, Any]
                   ) -> Tuple[Optional[List[List[Tuple[float, float]]]], bool, str]:
    """实体 → (chains, natural_closed, approximation)；拿不到坐标 → (None, False, "")。

    chains 是若干条点序列（每条 ≥ 2 点）；natural_closed 表示这一条实体自己就是闭环。
    弧段（ARC）与样条（SPLINE）在本版按端点直连近似，必须留痕（Spec §3 第 5 步）。
    """
    kind = _text(entity.get("type")).upper()
    if kind not in LOOP_TYPES:
        return None, False, ""
    raw = entity.get("attributes")
    attrs = raw if isinstance(raw, dict) else {}
    if kind == "LINE":
        start = cad_geometry.point_of(attrs.get("start"))
        end = cad_geometry.point_of(attrs.get("end"))
        if not start or not end:
            return None, False, ""
        return [[start, end]], False, ""
    if kind == "ARC":
        center = cad_geometry.point_of(attrs.get("center"))
        radius = _num(attrs.get("radius"))
        if not center or radius is None or radius <= 0:
            return None, False, ""
        first_angle = _num(attrs.get("start_angle")) or 0.0
        second_angle = _num(attrs.get("end_angle")) or 0.0
        first = (center[0] + radius * math.cos(math.radians(first_angle)),
                 center[1] + radius * math.sin(math.radians(first_angle)))
        second = (center[0] + radius * math.cos(math.radians(second_angle)),
                  center[1] + radius * math.sin(math.radians(second_angle)))
        if cad_geometry.distance(first, second) <= LOOP_TOLERANCE_MM:
            return None, False, ""
        return [[first, second]], False, "arc_endpoints"
    if kind == "CIRCLE":
        center = cad_geometry.point_of(attrs.get("center"))
        radius = _num(attrs.get("radius"))
        if not center or radius is None or radius <= 0:
            return None, False, ""
        ring = [(center[0] + radius * math.cos(2.0 * math.pi * index / CIRCLE_SAMPLES),
                 center[1] + radius * math.sin(2.0 * math.pi * index / CIRCLE_SAMPLES))
                for index in range(CIRCLE_SAMPLES)]
        return [ring + [ring[0]]], True, ""
    if kind in ("LWPOLYLINE", "POLYLINE"):
        points = cad_geometry.points_of(attrs.get("points") or [])
        if len(points) < 2:
            return None, False, ""
        closed = bool(entity.get("closed")) or cad_geometry.is_closed(points, LOOP_TOLERANCE_MM)
        if closed and not cad_geometry.is_closed(points, LOOP_TOLERANCE_MM):
            points = points + [points[0]]
        return [points], closed, ""
    if kind == "SPLINE":
        points = cad_geometry.points_of(attrs.get("fit_points") or [])
        if len(points) < 2:
            return None, False, ""
        return [points], cad_geometry.is_closed(points, LOOP_TOLERANCE_MM), "spline_fit"
    return None, False, ""


def _component_edges(members: List[Dict[str, Any]]
                     ) -> Tuple[List[Tuple[Any, Any, str, str]], Dict[Any, Tuple[float, float]]]:
    """件内实体 → 边与顶点表。实体顺序按 entity_id 排序，保证输出确定性。"""
    edges: List[Tuple[Any, Any, str, str]] = []
    vertices: Dict[Any, Tuple[float, float]] = {}
    for entity in sorted(members, key=lambda row: _text(row.get("entity_id"))):
        chains, _natural, approximation = _entity_chains(entity)
        if not chains:
            continue
        entity_id = _text(entity.get("entity_id"))
        for chain in chains:
            keys = []
            for point in chain:
                key = _quant_key(point)
                keys.append(key)
                vertices.setdefault(key, (float(point[0]), float(point[1])))
            for index in range(len(keys) - 1):
                if keys[index] == keys[index + 1]:
                    continue
                edges.append((keys[index], keys[index + 1], entity_id, approximation))
    return edges, vertices


def _two_core(adjacency: Dict[Any, List[Tuple[Any, int]]]) -> Dict[Any, List[Tuple[Any, int]]]:
    """剥掉度 < 2 的顶点：环只可能活在 2-core 里（真图多数分量是长开放链，省掉无用搜索）。"""
    degree = {node: len(neighbours) for node, neighbours in adjacency.items()}
    pending = sorted(node for node, value in degree.items() if value < 2)
    removed: set = set()
    while pending:
        node = pending.pop(0)
        if node in removed:
            continue
        removed.add(node)
        for neighbour, _edge in adjacency.get(node) or ():
            if neighbour in removed:
                continue
            degree[neighbour] = degree.get(neighbour, 0) - 1
            if degree[neighbour] < 2:
                pending.append(neighbour)
    return {node: [(neighbour, edge) for neighbour, edge in neighbours
                   if neighbour not in removed]
            for node, neighbours in adjacency.items() if node not in removed}


def _find_cycles(adjacency: Dict[Any, List[Tuple[Any, int]]], *,
                 min_edges: int = MIN_LOOP_EDGES, max_cycles: int = MAX_LOOP_CYCLES,
                 max_states: int = MAX_LOOP_STATES
                 ) -> Tuple[List[Dict[str, Any]], bool]:
    """找简单环：顶点不重复、边不重复、首尾相接（Spec §3 第 3 步）。

    起点固定为环里最小的节点（neighbour < start 剪枝），同一个环只报一次。
    预算是硬上限：到点就停，但结果仍确定（同一份 IR 两次跑一模一样）。
    返回 `(loops, exhausted)`：`exhausted` 表示搜索**撞上预算提前中止**（结论不完整），
    调用方据此说"我们没算完"，而不是把中止当成"没有环"（Spec §2.4 序 3）。
    """
    loops: List[Dict[str, Any]] = []
    states = [0]
    exhausted = [False]
    # 环只可能活在 2-core 里（真图多数分量是长开放链）：先剥掉度 < 2 的顶点，省掉无用搜索。
    # 这一步只改**速度**：度 < 2 的顶点不可能落在任何简单环上，结果逐字不变。
    adjacency = _two_core(adjacency)

    def walk(start: Any, node: Any, path_nodes: List[Any], path_edges: List[int],
             used: set) -> None:
        for neighbour, edge in adjacency.get(node) or ():
            states[0] += 1
            if states[0] >= max_states or len(loops) >= max_cycles:
                exhausted[0] = True
                return
            if edge in path_edges:
                continue
            if neighbour == start:
                if len(path_edges) + 1 >= min_edges:
                    loops.append({"nodes": list(path_nodes), "edges": list(path_edges) + [edge]})
                continue
            if neighbour in used or neighbour < start:
                continue
            used.add(neighbour)
            path_nodes.append(neighbour)
            path_edges.append(edge)
            walk(start, neighbour, path_nodes, path_edges, used)
            path_edges.pop()
            path_nodes.pop()
            used.discard(neighbour)

    for start in sorted(adjacency):
        if states[0] >= max_states or len(loops) >= max_cycles:
            exhausted[0] = True
            break
        walk(start, start, [start], [], {start})
    return loops, exhausted[0]


def _adjacency_of(edges: List[Tuple[Any, Any, str, str]]
                  ) -> Dict[Any, List[Tuple[Any, int]]]:
    """边表 → 邻接表（边序号即 edges 里的下标）。"""
    adjacency: Dict[Any, List[Tuple[Any, int]]] = {}
    for index, (first, second, _entity_id, _approximation) in enumerate(edges):
        adjacency.setdefault(first, []).append((second, index))
        adjacency.setdefault(second, []).append((first, index))
    return adjacency


def _edge_key(first: Any, second: Any) -> Tuple[Any, Any]:
    """边的无向键（方向无关，量化端点已归并）。"""
    return (first, second) if first <= second else (second, first)


def _collapse_edges(edges: List[Tuple[Any, Any, str, str]]
                    ) -> List[Tuple[Any, Any, str, str]]:
    """同一对量化端点之间的重复边折成一条（代表边 = entity_id 最小那条，Spec §2.1）。

    折叠只影响**找环**：`outline.entity_ids` 仍列该件里全部相关实体，证据可回查。
    输出按边键排序 —— 同一份 IR 两次跑逐字相同。
    """
    best: Dict[Tuple[Any, Any], Tuple[Any, Any, str, str]] = {}
    for edge in edges:
        key = _edge_key(edge[0], edge[1])
        current = best.get(key)
        if current is None or edge[2] < current[2]:
            best[key] = edge
    return [best[key] for key in sorted(best)]


def _nearest_gap_mm(keys: List[Any], vertices: Dict[Any, Tuple[float, float]]) -> float:
    """奇度顶点两两**最近的配对**后，取配对间隙的最大值（没有奇度顶点 → 0.0）。

    按坐标排序后取相邻配对 —— 真图上一个分量可能有上千个奇度顶点（开放链的刀口），
    逐对求最近是 O(n^3)，会把整条链路拖死；排序配对是 O(n log n) 且结果确定。
    """
    pending = sorted((float(vertices[key][0]), float(vertices[key][1])) for key in keys
                     if key in vertices)
    gaps = [cad_geometry.distance(pending[index], pending[index + 1])
            for index in range(0, len(pending) - 1, 2)]
    return max(gaps) if gaps else 0.0


def _outline_evidence(members: List[Dict[str, Any]], *,
                      max_states: int = MAX_LOOP_STATES,
                      max_cycles: int = MAX_LOOP_CYCLES) -> Dict[str, Any]:
    """一次算好三件事（Spec §2.1/§2.3）：未折叠图、折叠图、逐件诊断。

    未折叠图是**今天的口径**：已经判成 closed 的件必须逐字保持它的结果，所以两条路都要算
    （只有存在重复边时才真的各跑一次搜索；没有重复边时折叠图 == 未折叠图，复用同一个结果）。

    `max_states` 只给**单件重算**用（Spec `packaging-open-outline-part-needs-a-way-out.md` §2.3(b)）：
    缺省值就是今天的预算，所以既有的抽取结果逐字不变。
    """
    state_budget = max(1, int(max_states))
    cycle_budget = max(1, int(max_cycles))
    edges, vertices = _component_edges(members)
    unique = _collapse_edges(edges)
    duplicated = len(unique) != len(edges)
    if unique:
        loops_collapsed, exhausted_collapsed = _find_cycles(
            _adjacency_of(unique), max_cycles=cycle_budget, max_states=state_budget)
    else:
        loops_collapsed, exhausted_collapsed = [], False
    if duplicated:
        loops_original, _exhausted_original = _find_cycles(
            _adjacency_of(edges), max_cycles=cycle_budget, max_states=state_budget)
    else:
        loops_original = loops_collapsed
    degree: Dict[Any, int] = {}
    for first, second, _entity_id, _approximation in unique:
        degree[first] = degree.get(first, 0) + 1
        degree[second] = degree.get(second, 0) + 1
    odd = [key for key in sorted(degree) if degree[key] % 2]
    # 预算中止**且一个环都没找到**才算"我们没算完"：找到环的件，中止不影响结论。
    budget_exhausted = bool(exhausted_collapsed) and not loops_collapsed
    diagnosis = {
        "edges_total": len(edges),
        "edges_unique": len(unique),
        "collapsed_total": len(edges) - len(unique),
        "cycles_found": len(loops_collapsed),
        "budget_exhausted": budget_exhausted,
        "odd_degree_vertices": len(odd),
        "nearest_gap_mm": _round(_nearest_gap_mm(odd, vertices)),
    }
    return {"edges": edges, "unique": unique, "vertices": vertices,
            "loops_original": loops_original, "loops_collapsed": loops_collapsed,
            "diagnosis": diagnosis}


def outline_diagnosis(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    """逐件诊断（Spec §2.3）：重复边 / 环数 / 预算中止 / 奇度顶点 / 最近配对间隙。

    统计口径与 `_component_edges` 一致（端点按 `LOOP_TOLERANCE_MM` 量化）；纯函数。

    墙钟账（`elapsed_ms` / `budget_exceeded`，Spec `packaging-parts-pipeline-time-budget.md`
    §3.4/§3.5）**只在这里**上报：零件文档里的逐件诊断必须两次跑逐字相同，塞一个秒表进去
    就破坏了"同一份 IR 两次跑必须一样"（既有红测 D 组）。
    """
    started = time.perf_counter()
    diagnosis = dict(_outline_evidence(list(members or []))["diagnosis"])
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    diagnosis["elapsed_ms"] = elapsed_ms
    diagnosis["budget_exceeded"] = elapsed_ms > TIME_BUDGET_MS
    return diagnosis


def _largest_loop(loops: List[Dict[str, Any]], edges: List[Tuple[Any, Any, str, str]],
                  vertices: Dict[Any, Tuple[float, float]], min_area: float
                  ) -> Dict[str, Any]:
    """给定额度内的环集合 → 最大闭合环（`{outline, loop, saw_loop, has_coordinates}`）。

    saw_loop 表示确实找到了环（哪怕面积被门槛挡掉）；has_coordinates 表示这件里至少有
    实体给出了可用坐标 —— 拿不到坐标时分量只能沿用 DWG 自己的包围盒（Spec §3）。
    """
    if not edges:
        return {"outline": None, "loop": None, "saw_loop": False, "has_coordinates": False}
    best: Optional[Dict[str, Any]] = None
    best_loop: Optional[Dict[str, Any]] = None
    best_area = 0.0
    saw_loop = False
    for loop in loops:
        polygon = [vertices[key] for key in loop["nodes"]]
        if len(polygon) < MIN_LOOP_EDGES:
            continue
        area = cad_geometry.polygon_area(polygon)
        saw_loop = True
        if area < min_area or area <= best_area:
            continue
        approximations = [edges[index][3] for index in loop["edges"] if edges[index][3]]
        outline = {
            "points": [[float(x), float(y)] for x, y in polygon],
            "entity_ids": sorted({edges[index][2] for index in loop["edges"]}),
            "closed": True,
            "area_mm2": _round(area),
            "bbox": cad_geometry.bbox_of(polygon),
        }
        if approximations:
            # 一个环里混了弧段与样条时，弧段优先（近似口径只留一条）。
            outline["approximation"] = ("arc_endpoints" if "arc_endpoints" in approximations
                                       else approximations[0])
        best, best_loop, best_area = outline, loop, area
    return {"outline": best, "loop": best_loop, "saw_loop": saw_loop, "has_coordinates": True}


def _bbox_cover(loop_box: Optional[List[float]], component_box: Optional[List[float]],
                tolerance: float = LOOP_TOLERANCE_MM) -> float:
    """环 bbox 覆盖分量 bbox 的比例（每一边按 `tolerance` 容差；Spec §2.2）。

    两个方向各算一个比例（缺多少 / 分量跨度），取较小者 —— 只有**四边都盖住**才是外轮廓。
    """
    if not loop_box or not component_box:
        return 0.0
    width = abs(float(component_box[2]) - float(component_box[0]))
    height = abs(float(component_box[3]) - float(component_box[1]))
    if width <= 0 or height <= 0:
        return 1.0

    def axis_ratio(loop_low: float, loop_high: float, low: float, high: float, span: float) -> float:
        missing = max(0.0, loop_low - low - tolerance) + max(0.0, high - loop_high - tolerance)
        return max(0.0, min(1.0, 1.0 - missing / span))

    return min(axis_ratio(float(loop_box[0]), float(loop_box[2]),
                          float(component_box[0]), float(component_box[2]), width),
               axis_ratio(float(loop_box[1]), float(loop_box[3]),
                          float(component_box[1]), float(component_box[3]), height))


def _rescue_outline(evidence: Dict[str, Any], component_box: Optional[List[float]],
                    min_area: float) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """外轮廓重判（rescue）：折叠图上重找环 → `(outline, compose)`；不满足就 `(None, None)`。

    两个条件缺一不可（Spec §2.2）：① 折叠前判成 open（调用方保证）；② 最大环 bbox 覆盖
    分量 bbox >= `OUTLINE_BBOX_COVER_RATIO`。`compose` 只在 rescue 成功的件上出现 ——
    便于核对"到底动了谁"。
    """
    found = _largest_loop(evidence["loops_collapsed"], evidence["unique"],
                          evidence["vertices"], min_area)
    outline = found["outline"]
    if outline is None or not component_box:
        return None, None
    cover = _bbox_cover(outline.get("bbox"), component_box)
    if cover < OUTLINE_BBOX_COVER_RATIO:
        return None, None
    pairs = {_edge_key(evidence["unique"][index][0], evidence["unique"][index][1])
             for index in (found["loop"] or {}).get("edges") or []}
    merged = dict(outline)
    # 折叠只作用于找环：证据（含被折叠掉的重复实体）仍然逐条列出来回查（Spec §2.1）。
    merged["entity_ids"] = sorted({edge[2] for edge in evidence["edges"]
                                   if _edge_key(edge[0], edge[1]) in pairs})
    compose = {
        "kind": "collapsed_cycle",
        "rule_id": CHAIN_RULE_ID,
        "edges_total": len(evidence["edges"]),
        "edges_unique": len(evidence["unique"]),
        "collapsed_total": len(evidence["edges"]) - len(evidence["unique"]),
        "bbox_cover": _round(cover),
    }
    return merged, compose


def _open_outline_reason(*, has_curve: bool, diagnosis: Dict[str, Any],
                         saw_loop: bool) -> str:
    """判成 open 的具体原因（闭集，按 Spec §2.4 的顺序判定）。"""
    if not has_curve:
        return "no_curve_entity"
    if diagnosis.get("budget_exhausted"):
        return "loop_budget_exhausted"
    if int(diagnosis.get("odd_degree_vertices") or 0) > 0 \
            and float(diagnosis.get("nearest_gap_mm") or 0.0) > LOOP_TOLERANCE_MM:
        return "odd_endpoints"
    if saw_loop or int(diagnosis.get("cycles_found") or 0) > 0:
        return "loop_too_small"
    # 有边却既无环也无奇度顶点在图论上不存在；真到了这里宁可说"断口"也不许笼统。
    return "odd_endpoints"


def outline_advice(reason: Any) -> Dict[str, Any]:
    """未闭合件的「为什么卡着 + 下一步做什么」（**纯函数**，Spec §2.1/§2.2）。

    两种原因分家（`OUTLINE_OPEN_REASONS` 闭集不扩）：

    - `loop_budget_exhausted`：**我们没算完** —— 可重试，指向单件「重算轮廓」；
    - 其余（`no_curve_entity` / `unit_unconfirmed` / `odd_endpoints` / `loop_too_small`）：
      图纸**真的**没有可信的闭合轮廓 —— 指向「改图重传」或「按包围盒签字确认」。
    """
    name = _text(reason) or OUTLINE_MANUAL_REASONS[2]
    if name in OUTLINE_RETRYABLE_REASONS:
        return {
            "reason": name,
            "retryable": True,
            "action": OUTLINE_ACTION_RECOMPUTE,
            "message": ("这一件没算完（%s）：轮廓搜索额度用完，闭合环还没找全 —— "
                        "点这一行「重算轮廓」用更大的额度只重算这一件；"
                        "仍报同一原因就改图重传。" % name),
        }
    return {
        "reason": name,
        "retryable": False,
        "action": OUTLINE_ACTION_CONFIRM,
        "message": ("图纸里这一件真的没有可信的闭合轮廓（%s）—— 改图重传，"
                    "或点这一行「按包围盒签字确认」人工放行"
                    "（按包围盒估算，卡片上会标出这是人签的字，不是几何闭合）。" % name),
    }


def outline_confirmation(row: Any) -> Dict[str, Any]:
    """这一件有没有**人签的**轮廓放行（没有 / 不完整 → `{}`）。

    判据只看留痕本身（`kind` + 签名人）：签字是"人放行"，与几何判定的 `closed` 是两回事
    （Spec §2.4）。
    """
    payload = row if isinstance(row, dict) else {}
    info = payload.get("outline_confirmation")
    if not isinstance(info, dict):
        return {}
    if _text(info.get("kind")) != MANUAL_OUTLINE_KIND or not _text(info.get("bound_by")):
        return {}
    return dict(info)


def set_manual_outline(row: Any, *, bound_by: str, reason: str = "",
                       confirmed_at: str = "") -> Dict[str, Any]:
    """人工签字「这一件按包围盒估算」（**纯函数**）：返回副本，绝不原地改入参。

    Spec §2.4：**几何状态一个字不改** —— `outline_status` 仍是原来的 `open`
    （`closed` 只能由几何判定给出），人签的字单独留在 `outline_confirmation` 上
    （谁 / 为什么 / 什么时候 / 当时是哪条原因），卡片与面板据此说"这是人放行的"。
    签名人（`bound_by`）为空 → `ValueError`（**不许匿名放行**）。
    """
    by = _text(bound_by)
    if not by:
        raise ValueError("人工轮廓确认必须记名（不许匿名放行）")
    updated = copy.deepcopy(row) if isinstance(row, dict) else {}
    updated["outline_confirmation"] = {
        "kind": MANUAL_OUTLINE_KIND,
        "rule_id": MANUAL_OUTLINE_RULE_ID,
        "bound_by": by,
        "reason": _text(reason),
        "confirmed_at": _text(confirmed_at),
        "outline_status_kept": _text(updated.get("outline_status")) or "open",
        "outline_reason": _text(updated.get("outline_reason")),
        "engine_version": ENGINE_VERSION,
    }
    return updated


def set_recomputed_outline(row: Any, record: Any) -> Dict[str, Any]:
    """把一版**单件重算**结论合回零件行（**纯函数**，Spec §2.3(b)）。

    - 重算**真的**算出了闭合环（`outline_status == "closed"`）：行按这次几何结论更新
      —— 这个 `closed` 是几何判定给的（放大额度后的这次搜索），不是人写的；
    - 仍然没算出来：只更新诊断与留痕（仍是 `open` + 原来那条原因），
      **不许**因为"重算过"就放行（Spec §2.5 第 4 条）。
    """
    updated = copy.deepcopy(row) if isinstance(row, dict) else {}
    payload = record if isinstance(record, dict) else {}
    diagnosis = payload.get("outline_diagnosis")
    if isinstance(diagnosis, dict) and diagnosis:
        updated["outline_diagnosis"] = copy.deepcopy(diagnosis)
    status = _text(payload.get("outline_status"))
    outline = payload.get("outline")
    if status == "closed" and isinstance(outline, dict) and outline.get("points"):
        updated.update({
            "outline_status": status,
            "outline": copy.deepcopy(outline),
            "outline_reason": "",
            "size_source": _text(payload.get("size_source")) or "closed_outline",
            "unfolded_length_mm": payload.get("unfolded_length_mm"),
            "unfolded_width_mm": payload.get("unfolded_width_mm"),
            "area_mm2": payload.get("area_mm2"),
        })
    elif status == "open":
        updated["outline_reason"] = (_text(payload.get("outline_reason"))
                                     or _text(updated.get("outline_reason"))
                                     or OUTLINE_RETRYABLE_REASONS[0])
    updated["outline_recompute"] = {
        "kind": RECOMPUTED_OUTLINE_KIND,
        "rule_id": _text(payload.get("rule_id")) or RECOMPUTE_RULE_ID,
        "scale": payload.get("scale"),
        "bound_by": _text(payload.get("bound_by")),
        "reason": _text(payload.get("reason")),
        "confirmed_at": _text(payload.get("confirmed_at")),
        "changed": bool(payload.get("changed")),
        "outline_status": status,
        "outline_reason": _text(payload.get("outline_reason")),
    }
    return updated


# --------------------------------------------------------------------------- #
# 1a 种类指纹（Spec `packaging-parts-list-visibility-and-kinds.md` §2.2）
# --------------------------------------------------------------------------- #
def kind_points_of(row: Dict[str, Any]) -> List[Tuple[int, int]]:
    """一件用于种类指纹的量化轮廓（Spec §2.2 第 1–2 步）。

    闭合件取 `outline.points`（真实轮廓）；其余退回分量包围盒四角 —— 轮廓状态本身参与
    指纹（第 4 步），所以这里只负责「取点 + 按 LOOP_TOLERANCE_MM 量化 + 平移到最小点归零」。
    **只有平移不变性**：旋转 / 镜像的变体本批仍算不同种（宁可多一种，不许把不同刀模合成一种）。
    """
    outline = row.get("outline") if isinstance(row.get("outline"), dict) else {}
    raw = outline.get("points")
    points: List[Tuple[float, float]] = []
    if _text(row.get("outline_status")) == "closed" and isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                x, y = _num(item[0]), _num(item[1])
                if x is not None and y is not None:
                    points.append((float(x), float(y)))
    bbox = row.get("bbox")
    if not points and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        values = [_num(item) for item in bbox]
        if all(item is not None for item in values):
            x0, y0, x1, y1 = (float(item) for item in values)
            points = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if not points:
        return []
    quantized = [_quant_key(point) for point in points]
    min_x = min(item[0] for item in quantized)
    min_y = min(item[1] for item in quantized)
    return [(item[0] - min_x, item[1] - min_y) for item in quantized]


def kind_key_of(row: Dict[str, Any]) -> str:
    """形状 + 尺寸 + 轮廓状态的确定性指纹（Spec §2.2 第 1–5 步）。

    只吃行上的轮廓点与长宽，不读时间 / 随机数 / 字典序 —— 同一份 IR 两次跑必须逐字相同。
    """
    payload = {"outline_status": _text(row.get("outline_status")),
               "size": [_round(row.get("length")), _round(row.get("width"))],
               "points": kind_points_of(row)}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:KIND_KEY_LENGTH]


def extract(ir: Dict[str, Any], semantics: Any = None, *,
            options: Any = None) -> Dict[str, Any]:
    """CAD IR → 零件文档。同一份 IR 两次跑必须逐字相同（排序与编号全部确定性）。"""
    if not isinstance(ir, dict):
        raise TypeError("extract() 需要一份 CAD IR 文档")
    config = _options(options)
    # 需求 3.3 的整盒材料口径（Spec `packaging-parts-material-attribution.md` §2 层 4）；
    # 由调用方通过 options 传进来（图纸流步骤读需求），取不到就不兜底。
    requirement = options.get("requirement") if isinstance(options, dict) else None
    geometry = ir.get("geometry") if isinstance(ir.get("geometry"), dict) else {}
    components = [row for row in (geometry.get("components") or []) if isinstance(row, dict)]
    entities = {str((row or {}).get("entity_id")): row
                for row in (ir.get("entities") or []) if isinstance(row, dict)}
    known = _known_evidence(ir)
    # 角色与"这一次角色是怎么查出来的"走同一趟查找（Spec
    # `packaging-parts-role-lookup-disclosure.md` §2.1）：全 unknown 时也不再与"语义层没跑成"同形。
    roles, role_lookup = _resolve_layer_roles(ir, semantics)
    notes = drawing_notes(ir)
    units = ir.get("units") if isinstance(ir.get("units"), dict) else {}
    unit_status = _text(units.get("unit_status"))
    unit_ok = unit_status == "confirmed"

    kept: List[Dict[str, Any]] = []
    filtered: List[Dict[str, Any]] = []
    for component in components:
        component_id = _text(component.get("component_id"))
        entity_ids = [_text(item) for item in (component.get("entity_ids") or [])]
        members = [entities[item] for item in entity_ids if item in entities]
        bbox = _bbox_of(component, members)
        if bbox is None:
            box_length = box_width = None
            box_area = 0.0
        else:
            box_length = abs(bbox[2] - bbox[0])
            box_width = abs(bbox[3] - bbox[1])
            box_area = box_length * box_width
        curves = [entity for entity in members
                  if _text(entity.get("type")).upper() in CURVE_TYPES]

        # —— 真实轮廓：件内求最大闭合环（Spec `packaging-parts-true-outline.md` §3）——
        # 单位未确认时求环没有意义（没有可信的 mm），直接标 unavailable。
        # —— 重复边折叠 + 外轮廓重判（Spec `packaging-parts-outline-chaining.md` §2）——
        # 先按**今天的口径**（未折叠图）判一次：已经判成 closed 的件必须逐字保持原样；
        # 只有判成 open 的件才走 rescue（折叠图重找环 + 外轮廓覆盖率准入）。
        evidence = _outline_evidence(members)
        outline: Optional[Dict[str, Any]] = None
        compose: Optional[Dict[str, Any]] = None
        saw_loop = False
        has_coordinates = False
        if unit_ok:
            verdict = _largest_loop(evidence["loops_original"], evidence["edges"],
                                    evidence["vertices"], float(config["min_area_mm2"]))
            outline = verdict["outline"]
            saw_loop = verdict["saw_loop"]
            has_coordinates = verdict["has_coordinates"]
            if outline is None:
                outline, compose = _rescue_outline(evidence, bbox,
                                                   float(config["min_area_mm2"]))
                if outline is not None:
                    saw_loop = True
                    has_coordinates = True
        if not unit_ok:
            outline_status, outline_reason = "unavailable", "unit_unconfirmed"
            size_source = "dwg_outline"
            length = width = None
            area = 0.0
        elif outline is not None:
            outline_status, outline_reason, size_source = "closed", "", "closed_outline"
            if compose:
                outline = dict(outline)
                outline["compose"] = compose
            loop_box = outline.get("bbox") or bbox
            length = abs(loop_box[2] - loop_box[0])
            width = abs(loop_box[3] - loop_box[1])
            area = float(outline.get("area_mm2") or 0.0)
        else:
            # 求不出环 → 退回分量包围盒**并留痕**（绝不许把包围盒说成轮廓尺寸）。
            outline_status = "open"
            outline_reason = _open_outline_reason(
                has_curve=bool(curves), diagnosis=evidence["diagnosis"], saw_loop=saw_loop)
            # 分量里一条可用坐标都没有时，尺寸来自 DWG 自己的包围盒（与今天口径一致）。
            size_source = "component_bbox" if has_coordinates else "dwg_outline"
            length, width, area = box_length, box_width, box_area
        if outline is None:
            outline = {"points": None, "entity_ids": [], "closed": False,
                       "area_mm2": None, "bbox": list(bbox) if bbox else None}

        reasons: List[str] = []
        if bbox is not None:
            if max(box_length or 0.0, box_width or 0.0) > float(config["max_edge_mm"]):
                reasons.append("edge_over_max")
            if box_area > float(config["max_area_mm2"]):
                reasons.append("area_over_max")
            # 有环的件即使环比 min_area 小也要留着（Spec §3：报 loop_too_small，不许悄悄丢掉）。
            if box_area < float(config["min_area_mm2"]) and not saw_loop:
                reasons.append("area_under_min")
        # 「没有可制造曲线」只在**看得见实体**时才敢判：分量声明了实体却一条都查不到
        # （块引用 / 代理实体 / IR 缺条），说明这一件对我们是不透明的，宁可留着让人看，
        # 也不能悄悄当碎线丢掉（真图上"零件 = 闭合轮廓"就是这么丢掉 400 件的前车之鉴）。
        if not curves and (not entity_ids or members):
            reasons.append("no_curve_entity")
        if reasons:
            filtered.append({"component_id": component_id, "reasons": reasons,
                             "reason": reasons[0], "bbox": bbox,
                             # 没有它就看不出"这一块是不是吞并块"（Spec §2.4）。
                             "entity_total": len(entity_ids)})
            continue

        part_roles = sorted({roles.get(_layer_key(entity.get("layer")), "unknown")
                             for entity in curves})
        role = min(part_roles, key=_role_index) if part_roles else "unknown"
        layer_names = sorted({_text(entity.get("layer")) for entity in curves
                              if _text(entity.get("layer"))})
        refs = [str(entity.get("evidence_ref") or "") for entity in curves]
        refs += ["ev:L:%s" % name for name in layer_names]
        evidence_refs = sorted({ref for ref in refs if ref and ref in known})
        kept.append({
            "component_id": component_id,
            "entity_ids": sorted(set(entity_ids)),
            "entity_total": len(entity_ids),
            "bbox": bbox,
            "length": length,
            "width": width,
            "area": area,
            "role": role,
            "layers": layer_names,
            "evidence_refs": evidence_refs,
            "outline_status": outline_status,
            "outline": outline,
            "outline_reason": outline_reason,
            "outline_diagnosis": evidence["diagnosis"],
            "size_source": size_source,
            # 材料/厚度归属在**整份零件表**上算（一条成组注记要覆盖多件、件级标注要看
            # 它是否同时贴多件），所以这里先留空，等 kept 收齐后统一填（Spec §2）。
            "thickness_mm": None,
            "material": None,
            "thickness_source": None,
            "material_source": None,
            "needs_confirmation": False,
            "assumption_refs": [],
            "attribution": None,
        })

    # —— 材料/厚度四层归属（Spec `packaging-parts-material-attribution.md` §2）——
    material_table = options.get("material_table") if isinstance(options, dict) else None
    attribution = attribute_materials(kept, notes, requirement=requirement,
                                      material_table=material_table)
    for row in kept:
        info = attribution.get(row["component_id"]) or {}
        row["material"] = info.get("material")
        row["thickness_mm"] = info.get("thickness_mm")
        row["material_source"] = info.get("material_source")
        row["thickness_source"] = info.get("thickness_source")
        row["needs_confirmation"] = bool(info.get("needs_confirmation"))
        row["assumption_refs"] = list(info.get("assumption_refs") or [])
        row["attribution"] = info.get("attribution")
        refs = [ref for ref in (info.get("note_refs") or []) if ref in known]
        if refs:
            row["evidence_refs"] = sorted(set(row["evidence_refs"]) | set(refs))
        # 缺口原因写在行上（Spec `packaging-parts-coverage-truthfulness.md` §2.2）：
        # 账（`material_gap_mix` / `thickness_gap_mix`）与行必须能对上，行上不能只有数字。
        if isinstance(row.get("attribution"), dict):
            row["attribution"]["gap_reason"] = gap_reason_of(row)

    # 排序：面积降序、component_id 升序（input 书写顺序不影响输出）。
    kept.sort(key=lambda row: (-(row["area"] or 0.0), row["component_id"]))
    filtered.sort(key=lambda row: row["component_id"])

    # —— 种类（Spec `packaging-parts-list-visibility-and-kinds.md` §2.2）——
    # 指纹由真实轮廓（闭合件）/ 分量包围盒（其余）算；编号**跨全量 kept 件**统一，
    # 这样分页读到的每一页 `kind_index` 都逐字一致（`repeat_of` 也只在同 `kind_key` 之间）。
    for row in kept:
        row["kind_key"] = kind_key_of(row)
    kind_sizes: Dict[str, int] = {}
    for row in kept:
        kind_sizes[row["kind_key"]] = kind_sizes.get(row["kind_key"], 0) + 1
    kind_order = sorted(kind_sizes, key=lambda key: (-kind_sizes[key], key))
    kind_index_of = {key: index for index, key in enumerate(kind_order, start=1)}

    parts: List[Dict[str, Any]] = []
    seen: Dict[str, str] = {}
    for index, row in enumerate(kept, start=1):
        part_code = PART_CODE_FORMAT % index
        kind_key = _text(row.get("kind_key"))
        repeat_of = seen.get(kind_key, "")
        if not repeat_of:
            seen[kind_key] = part_code
        parts.append({
            "part_code": part_code,
            "part_id": part_code,
            "name": "图纸零件 P%02d" % index,
            "unfolded_length_mm": _round(row["length"]) if unit_ok else None,
            "unfolded_width_mm": _round(row["width"]) if unit_ok else None,
            "area_mm2": _round(row["area"]) if unit_ok else None,
            "layers": row["layers"],
            "role": row["role"],
            "component_id": row["component_id"],
            "entity_ids": row["entity_ids"],
            "evidence_refs": row["evidence_refs"],
            "repeat_of": repeat_of,
            "kind_key": kind_key,
            "kind_index": int(kind_index_of.get(kind_key, 0)),
            "outline_status": row["outline_status"],
            "outline": row["outline"],
            "outline_reason": row["outline_reason"],
            "outline_diagnosis": row["outline_diagnosis"],
            "size_source": row["size_source"],
            "thickness_mm": row["thickness_mm"],
            "material": row["material"],
            "thickness_source": row["thickness_source"],
            "material_source": row["material_source"],
            "needs_confirmation": bool(row.get("needs_confirmation")),
            "assumption_refs": list(row.get("assumption_refs") or []),
            "attribution": row.get("attribution"),
        })

    # —— 不再因为"页大小"丢件（Spec `packaging-parts-list-visibility-and-kinds.md` §2.1）——
    # 缺省（未传 `options["max_parts"]`）时**全部 kept 件都进文档**；显式传正整数时才截断
    # 并计数 —— 旧行为与旧断言都能用显式参数逐字复现。读接口的页大小由路由的 `limit` 管。
    explicit_max = None
    if isinstance(options, dict):
        raw_max = _num(options.get("max_parts"))
        if raw_max is not None and raw_max > 0:
            explicit_max = int(raw_max)
    if explicit_max:
        truncated = max(0, len(parts) - explicit_max)
        parts = parts[:explicit_max]
    else:
        truncated = 0
    # `kept_total` 按 Spec §2.1 与 `part_total` **同值**（缺省时它就是把"过滤后剩多少"显式化；
    # 显式截断那一步的差值单独由 `truncated` 说）。`kind_total` 只数文档里留下的件。
    kept_total = len(parts)
    kind_total = len({_text(row.get("kind_key")) for row in parts
                      if _text(row.get("kind_key"))})
    # 三态计数只统计**最终保留**的件（与 part_total 自洽：三者之和 == part_total）。
    closed_total = sum(1 for row in parts if row["outline_status"] == "closed")
    open_total = sum(1 for row in parts if row["outline_status"] == "open")
    outline_unavailable_total = sum(1 for row in parts
                                    if row["outline_status"] == "unavailable")
    closed_ratio = (float(closed_total) / float(len(parts))) if parts else 0.0
    # 重复边折叠与外轮廓重判的账（Spec `packaging-parts-outline-chaining.md` §3）：
    # 「闭合判定」这一层到底做了多少事，必须由零件文档自己说清楚，而不是靠页面猜。
    collapsed_edge_total = sum(int((row.get("outline_diagnosis") or {}).get("collapsed_total") or 0)
                               for row in parts)
    collapsed_rescue_total = sum(1 for row in parts
                                 if (row.get("outline") or {}).get("compose"))
    budget_exhausted_total = sum(1 for row in parts
                                 if (row.get("outline_diagnosis") or {}).get("budget_exhausted"))
    open_reason_mix: Dict[str, int] = {}
    for row in parts:
        if row["outline_status"] != "open":
            continue
        reason = _text(row.get("outline_reason"))
        open_reason_mix[reason] = open_reason_mix.get(reason, 0) + 1

    # —— 被丢掉的两笔账（Spec `packaging-parts-component-chaining.md` §2.4）——
    # `truncated`（max_parts 截断）与 `filtered_*`（被过滤的分量）不是同一件事，前端分两句说。
    filtered_reason_mix: Dict[str, int] = {}
    for row in filtered:
        reason = _account_reason(row.get("reasons"))
        filtered_reason_mix[reason] = filtered_reason_mix.get(reason, 0) + 1
    filtered_reason_mix = _sorted_mix(filtered_reason_mix)
    filtered_reason_totals = {key: 0 for key in FILTER_REASON_TOTAL_KEYS.values()}
    for row in filtered:
        for reason in (row.get("reasons") or []):
            key = FILTER_REASON_TOTAL_KEYS.get(_text(reason))
            if key:
                filtered_reason_totals[key] += 1
    # 没有端点（也没同圆键）的实体不参与分组、各自成件时，必须计数（Spec §2.1）：
    # 它们不是"碎线噪声"，是"我们看不透的实体"。
    ungroupable_total = sum(
        1 for entity in entities.values()
        if _text(entity.get("type")).upper() in CURVE_TYPES
        and cad_geometry.is_ungroupable(entity))

    unavailable: List[Dict[str, Any]] = []
    if not components:
        unavailable.append({"code": "no_components",
                            "message": UNAVAILABLE_MESSAGES["no_components"]})
    elif not kept:
        unavailable.append({"code": "all_filtered",
                            "message": UNAVAILABLE_MESSAGES["all_filtered"]})
    if not unit_ok:
        unavailable.append({"code": "no_unit", "message": UNAVAILABLE_MESSAGES["no_unit"]})

    by_role: Dict[str, int] = {}
    for row in parts:
        by_role[row["role"]] = by_role.get(row["role"], 0) + 1

    source = ir.get("source") if isinstance(ir.get("source"), dict) else {}
    sem = semantics if isinstance(semantics, dict) else {}
    sem_source = sem.get("source") if isinstance(sem.get("source"), dict) else {}
    doc = {
        "engine_version": ENGINE_VERSION,
        "part_id_namespace": PART_ID_NAMESPACE,
        "parts": parts,
        "filtered": filtered,
        "unavailable": unavailable,
        "stats": {"part_total": len(parts), "filtered_total": len(filtered),
                  "truncated": truncated, "by_role": by_role,
                  # 过滤后剩多少（与 `part_total` 同值）与"有几种形状"（Spec §2.1/§2.2）：
                  # 前端据此把"被过滤掉的分量 / 文档里的件数 / 还有几件未列出"三笔账分开说。
                  "kept_total": kept_total, "kind_total": kind_total,
                  "closed_total": closed_total, "open_total": open_total,
                  "outline_unavailable_total": outline_unavailable_total,
                  "closed_ratio": _round(closed_ratio),
                  "collapsed_edge_total": collapsed_edge_total,
                  "collapsed_rescue_total": collapsed_rescue_total,
                  "budget_exhausted_total": budget_exhausted_total,
                  "open_reason_mix": open_reason_mix,
                  "filtered_reason_mix": filtered_reason_mix,
                  "ungroupable_total": ungroupable_total,
                  # 角色出处（Spec `packaging-parts-role-lookup-disclosure.md` §2.2）：
                  # 全 `unknown` 时也要分得清"语义层没跑成"与"图纸图层名不认识"。既有
                  # `by_role` 的计数口径一个字不改（这里只加出处）。
                  "role_lookup": role_lookup,
                  **filtered_reason_totals},
        "source": {
            "ir_id": _text(ir.get("ir_id")),
            "ir_hash": _text(ir.get("ir_hash")),
            "semantics_id": _text(sem.get("semantics_id")),
            "semantics_hash": _text(sem.get("semantics_hash")),
            "drawing_version": source.get("drawing_version") or sem_source.get("drawing_version"),
            "unit_status": unit_status,
        },
        "reviewable": True,
    }
    doc["parts_id"] = ""
    doc["parts_hash"] = ""
    doc["parts_id"], doc["parts_hash"] = _identity(doc)
    return doc


def _notes_say_ambiguous(row: Dict[str, Any]) -> bool:
    """这一件的归属是不是"歧义弃权"（`attribute_materials` 留的 `ambiguous:N` 痕）。"""
    attribution = row.get("attribution") if isinstance(row.get("attribution"), dict) else {}
    return any(str(item).startswith("ambiguous:") for item in (attribution.get("notes") or []))


def _gap_reason(row: Dict[str, Any], field: str) -> str:
    """一件缺材料 / 缺料厚的**原因**（Spec `packaging-parts-coverage-truthfulness.md` §2.2）。

    判定优先级（一件只记一次）：非闭合 → `no_closed_outline`；材料没定 → `material_ambiguous`
    （歧义弃权）/ `material_missing`（料厚账）/ `no_material_note`（材料账）；材料有但料厚没有 →
    只有克重 `grammage_only` / 注记里就没有厚度 `no_thickness_note`。
    """
    if _text(row.get("outline_status")) != "closed":
        return "no_closed_outline"
    if not _material_spec(row.get("material")):
        if _notes_say_ambiguous(row):
            return "material_ambiguous"
        return "no_material_note" if field == "material" else "material_missing"
    source = row.get("material_source") if isinstance(row.get("material_source"), dict) else {}
    if _grammage_of(source.get("text"), row.get("material")):
        return "grammage_only"
    return "no_thickness_note"


def gap_reason_of(row: Any) -> str:
    """这一件贡献给账的那**一个** `gap_reason`（Spec §2.2：账与行不许对不上）。

    先看材料账（没材料为主），材料有了再看料厚账；两本账都没有它就回 `""`。
    """
    item = row if isinstance(row, dict) else {}
    if not _material_spec(item.get("material")):
        return _gap_reason(item, "material")
    if _num(item.get("thickness_mm")) is None:
        return _gap_reason(item, "thickness")
    return ""


def _identity(doc: Dict[str, Any]) -> Tuple[str, str]:
    """版本锚点：同一份内容 → 同一个 id（落库幂等）。"""
    from .packaging_semantics import model as sem_model

    body = {key: value for key, value in doc.items()
            if key not in ("parts_id", "parts_hash")}
    digest = sem_model.sha256_hex(sem_model.canonical_json(sem_model.json_safe(body)))
    return "parts:" + digest[:16], digest


def _sorted_mix(mix: Dict[str, int]) -> Dict[str, int]:
    """账的排序：件数降序 → code 字典序（Spec `packaging-parts-selfcheck-diagnostics.md` §3）。"""
    return {key: mix[key] for key in sorted(mix, key=lambda name: (-mix[name], name))}


def _summary_role_lookup(stats: Dict[str, Any]) -> Dict[str, Any]:
    """摘要里的角色出处（Spec `packaging-parts-role-lookup-disclosure.md` §2.3）。

    文档里记了就**逐字带出**；老文档（本批之前落库的）没有这个键 → `unavailable` +
    `role_lookup_missing` —— "这份文档没带出处" ≠ "语义层这次可用"，不许猜。
    """
    state = stats.get("role_lookup") if isinstance(stats, dict) else None
    if isinstance(state, dict) and _text(state.get("source")) in ROLE_LOOKUPS:
        return state
    return {"source": "unavailable", "reason": "role_lookup_missing", "unknown_layers": [],
            "message": ROLE_LOOKUP_MESSAGES["missing"]}


def summarize(doc: Any, *, solids: Any = None) -> Dict[str, Any]:
    """摘要（不含 entity_ids / 证据明细）：给会话、看板与门禁用。

    五个指标（Spec `packaging-parts-downstream-acceptance.md` §2）一律**由零件行现算**，
    `part_total = 0` 时全部 `0.0`（不返回 null、不抛错）—— 门禁与看板据此说"这条链路
    到底做到了哪一步"。`solids` 可选：给挤出结论时 `solid_ok_ratio` 才算得出来。
    """
    payload = doc if isinstance(doc, dict) else {}
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    parts = []
    for row in payload.get("parts") or []:
        if not isinstance(row, dict):
            continue
        parts.append({"part_code": _text(row.get("part_code")),
                      "name": _text(row.get("name")),
                      "unfolded_length_mm": row.get("unfolded_length_mm"),
                      "unfolded_width_mm": row.get("unfolded_width_mm"),
                      "area_mm2": row.get("area_mm2"),
                      "layers": list(row.get("layers") or []),
                      "role": _text(row.get("role")),
                      "component_id": _text(row.get("component_id")),
                      "repeat_of": _text(row.get("repeat_of")),
                      "kind_key": _text(row.get("kind_key")),
                      "kind_index": int(_num(row.get("kind_index")) or 0),
                      "size_source": _text(row.get("size_source"))})
    total = _num(stats.get("part_total"))
    total = len(parts) if total is None else max(0, int(total))
    rows = [row for row in (payload.get("parts") or []) if isinstance(row, dict)]
    solid_status = {str(row.get("part_code") or ""): _text(row.get("solid_status"))
                    for row in rows if row.get("solid_status")}
    solid_reason = {str(row.get("part_code") or ""): _text(row.get("solid_reason"))
                    for row in rows if row.get("solid_status")}
    if isinstance(solids, dict):
        for item in (solids.get("parts") or []):
            if not isinstance(item, dict):
                continue
            code = _text(item.get("part_code"))
            if code:
                solid_status[code] = _text(item.get("status"))
                solid_reason[code] = _text(item.get("reason"))
    closed_total = sum(1 for row in rows
                       if _text(row.get("outline_status")) == "closed")
    role_known = sum(1 for row in rows if _text(row.get("role")) not in ("", "unknown"))
    # 不可算 / 不可挤出的两把账（Spec `packaging-parts-selfcheck-diagnostics.md` §2）：
    # 自检失败时必须一眼看出断在哪一环，而不是只有一句"没有一件能跑工艺"。
    unprocessable_reason_mix: Dict[str, int] = {}
    processable = 0
    for row in rows:
        verdict = processability(row)
        if verdict.get("ok"):
            processable += 1
            continue
        code = _text(verdict.get("code")) or "unknown"
        unprocessable_reason_mix[code] = unprocessable_reason_mix.get(code, 0) + 1
    solid_reason_mix: Dict[str, int] = {}
    for row in rows:
        code = _text(row.get("part_code"))
        status = solid_status.get(code)
        if not status:
            continue                      # 没有结论的件不进账："还没算"不等于"算不出来"
        reason = "ok" if status == "ok" else (solid_reason.get(code) or status)
        solid_reason_mix[reason] = solid_reason_mix.get(reason, 0) + 1
    unprocessable_reason_mix = _sorted_mix(unprocessable_reason_mix)
    solid_reason_mix = _sorted_mix(solid_reason_mix)
    solid_ok = sum(1 for row in rows
                   if solid_status.get(_text(row.get("part_code"))) == "ok")
    mix = {name: 0 for name in SIZE_SOURCES}
    for row in rows:
        source = _text(row.get("size_source"))
        if source in mix:
            mix[source] += 1
    # 材料/厚度归属的账（Spec `packaging-parts-material-attribution.md` §4）：
    # 覆盖率与"多少件是靠需求整盒口径兜底的"必须能一眼看出来。
    material_known = sum(1 for row in rows if _material_spec(row.get("material")))
    thickness_known = sum(1 for row in rows if _num(row.get("thickness_mm")) is not None)
    # 料厚的四本账（Spec `packaging-parts-thickness-facts.md` §2.4）：已知 / 未知 / 串味被拒 /
    # 人工补的。前两本必须自洽（已知 + 未知 == 零件总数），后两本回答"数字是怎么来的"。
    thickness_conflict_total = sum(
        1 for row in rows
        if ((row.get("attribution") or {}).get("thickness_material_conflict") or []))
    thickness_manual_total = sum(
        1 for row in rows
        if _material_source_kind(row, "thickness_source") == MANUAL_THICKNESS_KIND)
    # 材料也有一本「人补的」账（Spec `packaging-parts-in-card-and-material-fill.md` §2.2 第 5 条）：
    # 与 `thickness_manual_total` 逐字同口径；人工值**不算图纸证据**，不许抬高 evidence_ratio。
    material_manual_total = sum(
        1 for row in rows
        if _material_source_kind(row, "material_source") == MANUAL_MATERIAL_KIND)
    material_default = 0
    # 证据口径与缺口原因账（Spec `packaging-parts-coverage-truthfulness.md` §2.1 / §2.2）：
    # "有材料"与"有图纸证据"必须分开数；每一件缺材料 / 缺料厚都要说得出是哪一类原因。
    thickness_default_total = 0
    material_evidence = 0
    thickness_evidence = 0
    for row in rows:
        material_kind = _material_source_kind(row, "material_source")
        thickness_kind = _material_source_kind(row, "thickness_source")
        if thickness_kind == "requirement_default":
            thickness_default_total += 1
        if (_material_spec(row.get("material"))
                and material_kind not in NON_EVIDENCE_MATERIAL_KINDS):
            material_evidence += 1
        if _num(row.get("thickness_mm")) is not None and thickness_kind != "requirement_default":
            thickness_evidence += 1
    material_gap_mix = {reason: 0 for reason in MATERIAL_GAP_REASONS}
    thickness_gap_mix = {reason: 0 for reason in THICKNESS_GAP_REASONS}
    for row in rows:
        if not _material_spec(row.get("material")):
            reason = _gap_reason(row, "material")
            if reason in material_gap_mix:
                material_gap_mix[reason] += 1
        if _num(row.get("thickness_mm")) is None:
            reason = _gap_reason(row, "thickness")
            if reason in thickness_gap_mix:
                thickness_gap_mix[reason] += 1
    kind_mix = {name: 0 for name in ATTRIBUTION_KINDS}
    kind_mix["none"] = 0
    for row in rows:
        source = row.get("material_source") if isinstance(row.get("material_source"), dict) else {}
        kind = _text(source.get("kind"))
        if kind == "requirement_default":
            material_default += 1
        kind_mix[kind if kind in ATTRIBUTION_KINDS else "none"] += 1
    # 闭合判定的账（Spec `packaging-parts-outline-chaining.md` §3）：一律由零件行现算，
    # 页面/门禁据此说清"这一版到底折叠了多少重复边、救回几件、还剩几件开线、为什么"。
    collapsed_edge_total = 0
    collapsed_rescue_total = 0
    budget_exhausted_total = 0
    open_reason_mix: Dict[str, int] = {}
    for row in rows:
        diagnosis = row.get("outline_diagnosis") if isinstance(row.get("outline_diagnosis"), dict) else {}
        collapsed_total = _num(diagnosis.get("collapsed_total"))
        collapsed_edge_total += max(0, int(collapsed_total or 0))
        if diagnosis.get("budget_exhausted"):
            budget_exhausted_total += 1
        outline = row.get("outline") if isinstance(row.get("outline"), dict) else {}
        if outline.get("compose"):
            collapsed_rescue_total += 1
        if _text(row.get("outline_status")) == "open":
            reason = _text(row.get("outline_reason"))
            open_reason_mix[reason] = open_reason_mix.get(reason, 0) + 1

    def _ratio(count: int) -> float:
        return _round(float(count) / float(total)) if total else 0.0

    # 被丢掉的两笔账（Spec `packaging-parts-component-chaining.md` §2.4）：两笔必须都能从
    # 读接口拿到，前端据此把"未列出（截断）"与"未成为零件（过滤）"分两句说。
    filtered_rows = [row for row in (payload.get("filtered") or []) if isinstance(row, dict)]
    stats_mix = stats.get("filtered_reason_mix")
    if isinstance(stats_mix, dict):
        filtered_reason_mix = _sorted_mix({_text(key): int(_num(value) or 0)
                                           for key, value in stats_mix.items() if _text(key)})
    else:
        derived: Dict[str, int] = {}
        for row in filtered_rows:
            reason = _account_reason(row.get("reasons"))
            derived[reason] = derived.get(reason, 0) + 1
        filtered_reason_mix = _sorted_mix(derived)
    filtered_total = stats.get("filtered_total")
    filtered_total = (len(filtered_rows) if filtered_total is None
                      else max(0, int(_num(filtered_total) or 0)))

    # 列表可见性 + 种类的四本账（Spec `packaging-parts-list-visibility-and-kinds.md` §2.3）：
    # `listed_total` 只能是"文档里真的有多少行"，`kept_total` 是"过滤后剩多少"（两者相差的
    # 就是显式 `max_parts` 截断掉的件数）；`kind_total` / `repeat_total` 一律由行现算。
    listed_total = len(rows)
    kind_keys = {_text(row.get("kind_key")) for row in rows if _text(row.get("kind_key"))}
    kind_total = len(kind_keys)
    if not kind_total:
        kind_total = max(0, int(_num(stats.get("kind_total")) or 0))
    repeat_total = sum(1 for row in rows if _text(row.get("repeat_of")))
    kept_total = stats.get("kept_total")
    kept_total = (listed_total if kept_total is None
                  else max(listed_total, int(_num(kept_total) or 0)))

    return {
        "engine_version": _text(payload.get("engine_version")) or ENGINE_VERSION,
        "parts_id": _text(payload.get("parts_id")),
        "parts_hash": _text(payload.get("parts_hash")),
        "closed_ratio": _ratio(closed_total),
        "role_known_ratio": _ratio(role_known),
        "solid_ok_ratio": _ratio(solid_ok),
        "processable_ratio": _ratio(processable),
        "size_source_mix": mix,
        "material_known_ratio": _ratio(material_known),
        "thickness_known_ratio": _ratio(thickness_known),
        "thickness_known_total": thickness_known,
        "thickness_unknown_total": max(0, total - thickness_known),
        "thickness_conflict_total": thickness_conflict_total,
        "thickness_manual_total": thickness_manual_total,
        # 「分子 / 分母 / 证据口径」三件套（Spec `packaging-parts-coverage-truthfulness.md` §2.1）：
        # 既有比率一个字不改，只是把分子分母显式化，并区分「图纸证据」与「整盒兜底」。
        "part_total": total,
        "closed_total": closed_total,
        "material_known_total": material_known,
        "material_manual_total": material_manual_total,
        "processable_total": processable,
        "material_default_total": material_default,
        "thickness_default_total": thickness_default_total,
        "material_evidence_ratio": _ratio(material_evidence),
        "thickness_evidence_ratio": _ratio(thickness_evidence),
        "material_gap_mix": material_gap_mix,
        "thickness_gap_mix": thickness_gap_mix,
        "material_default_ratio": _ratio(material_default),
        "attribution_kind_mix": kind_mix,
        "collapsed_edge_total": collapsed_edge_total,
        "collapsed_rescue_total": collapsed_rescue_total,
        "budget_exhausted_total": budget_exhausted_total,
        "unprocessable_reason_mix": unprocessable_reason_mix,
        "solid_reason_mix": solid_reason_mix,
        "open_reason_mix": open_reason_mix,
        "open_total": sum(1 for row in rows if _text(row.get("outline_status")) == "open"),
        # 列表可见性与种类（Spec `packaging-parts-list-visibility-and-kinds.md` §2.3）：
        # 既有键一个字不改，这里只把"文档里有多少行 / 过滤后剩多少 / 有几种 / 几种重复"显式化。
        "kept_total": kept_total,
        "listed_total": listed_total,
        "kind_total": kind_total,
        "repeat_total": repeat_total,
        "filtered_total": filtered_total,
        "filtered_reason_mix": filtered_reason_mix,
        "parts": parts,
        "filtered": [{"component_id": _text(row.get("component_id")),
                      "reasons": list(row.get("reasons") or [])}
                     for row in (payload.get("filtered") or []) if isinstance(row, dict)],
        "unavailable": [{"code": _text(row.get("code")), "message": _text(row.get("message"))}
                        for row in (payload.get("unavailable") or []) if isinstance(row, dict)],
        # 角色出处（Spec `packaging-parts-role-lookup-disclosure.md` §2.3）：文档里记了就逐字带出；
        # 老文档没这个键 → `unavailable` + `role_lookup_missing`，**不编**成"语义层这次可用"。
        "role_lookup": _summary_role_lookup(stats),
        "stats": stats,
        "source": payload.get("source") if isinstance(payload.get("source"), dict) else {},
        "reviewable": bool(payload.get("reviewable")),
    }


# --------------------------------------------------------------------------- #
# 1b 下游适配：图纸零件 → 既有 IR 的 Part（Spec
#    `packaging-parts-downstream-process-and-cost.md` §3）
# --------------------------------------------------------------------------- #
def _material_spec(value: Any) -> str:
    """行里的材料可能是 {"spec": ...} 或一句标注文本；取不到就空（不许猜）。"""
    if isinstance(value, dict):
        for key in ("spec", "grade", "material_spec", "name"):
            text = _text(value.get(key))
            if text:
                return text
        return ""
    return _text(value)


def as_ir_part(row: Any) -> Any:
    """零件文档的一行 → 既有 IR 的 `Part`（唯一适配点，不改既有模型）。

    特征是**门槛式**的：只有闭合轮廓 + 已知料厚才给一个 `plate` 基体 —— 缺料时给
    特征等于拿错误输入去排工艺（Spec §3）。
    """
    from ..models.ir import Feature, Part, Provenance

    payload = row if isinstance(row, dict) else {}
    part_id = _text(payload.get("part_id")) or _text(payload.get("part_code"))
    name = _text(payload.get("name")) or part_id
    closed = _text(payload.get("outline_status")) == "closed"
    material = _material_spec(payload.get("material"))
    thickness = _num(payload.get("thickness_mm"))
    length = _num(payload.get("unfolded_length_mm"))
    width = _num(payload.get("unfolded_width_mm"))
    features = []
    if closed and thickness and thickness > 0 and length and width:
        features.append(Feature(type="plate", length=length, width=width,
                                thickness=thickness, purpose="图纸闭合轮廓 × 料厚"))
    if closed and material:
        confidence = 0.7
    elif closed:
        confidence = 0.4
    else:
        confidence = 0.3
    entities = [str(item) for item in (payload.get("entity_ids") or [])]
    note = " | ".join(part for part in (
        "packaging_parts/%s" % _text(payload.get("part_code")),
        "outline_status=%s" % (_text(payload.get("outline_status")) or "unknown"),
        "size_source=%s" % (_text(payload.get("size_source")) or "unknown"),
        "component=%s" % _text(payload.get("component_id")),
        "entities=%d" % len(entities),
        "material=%s" % (_material_source_kind(payload, "material_source") or "unknown"),
        "thickness=%s" % (_material_source_kind(payload, "thickness_source") or "unknown"),
    ) if part)
    return Part(part_id=part_id, name=name, role=_text(payload.get("role")) or None,
                quantity=1, features=features,
                material={"spec": material} if material else None,
                confidence=confidence, provenance=Provenance(note=note))


def _material_source_kind(payload: Dict[str, Any], key: str) -> str:
    source = payload.get(key)
    return _text(source.get("kind")) if isinstance(source, dict) else ""


def processability(row: Any, *, options: Any = None) -> Dict[str, Any]:
    """这一件现在能不能跑工艺/成本？缺什么就说什么（路由据此 409，绝不硬算）。

    返回 `{ok, code, message, missing_variables, part}`：`ok` 时 `part` 可直接交给
    `process.outline_process()` 与成本入口。
    """
    payload = row if isinstance(row, dict) else {}
    part_code = _text(payload.get("part_code"))
    if not part_code:
        return {"ok": False, "code": "PACKAGING_PART_NOT_FOUND",
                "message": "图纸里没有这个零件，请先跑一键解析", "missing_variables": [],
                "part": None}
    if _text(payload.get("outline_status")) != "closed" and not outline_confirmation(payload):
        # 未闭合件不给可执行下一步就是死路（Spec `packaging-open-outline-part-needs-a-way-out.md`
        # §2.1/§2.2）：与缺材料/料厚那条分支同形，逐条说"为什么卡着 + 下一步做什么"。
        # 人签过字（`outline_confirmation`）的件放行 —— 放行的是**人**，几何状态仍是 open。
        advice = outline_advice(payload.get("outline_reason"))
        return {"ok": False, "code": "PACKAGING_PART_NOT_CLOSED",
                "message": "这一件没有可信的闭合轮廓（%s），不能拿包围盒尺寸去排工艺 —— %s"
                           % (_text(payload.get("outline_reason"))
                              or OUTLINE_MANUAL_REASONS[2], advice["message"]),
                "missing_variables": ["outline"], "part": None}
    missing: List[str] = []
    if not _material_spec(payload.get("material")):
        missing.append("material")
    if not _num(payload.get("thickness_mm")):
        missing.append("thickness_mm")
    if missing:
        # 缺什么就给什么可执行下一步（Spec `packaging-parts-in-card-and-material-fill.md` §2.3）：
        # 材料与料厚各有件级补录入口，补完**只重算这一件**，不必回需求、不必重跑八步。
        advice: List[str] = []
        if "material" in missing:
            advice.append("缺材料 → 点这一行「补材料」补上")
        if "thickness_mm" in missing:
            advice.append("缺料厚 → 点这一行「补料厚」补上")
        return {"ok": False, "code": "PACKAGING_PART_MATERIAL_UNKNOWN",
                "message": "这一件缺材料/厚度：%s（%s）"
                           % ("、".join(missing), "；".join(advice)),
                "missing_variables": missing, "part": None}
    return {"ok": True, "code": "", "message": "", "missing_variables": [],
            "part": as_ir_part(payload)}


# --------------------------------------------------------------------------- #
# 2 落库 / 读回（版本化）
# --------------------------------------------------------------------------- #
def _load_items(project_id: str) -> List[Dict[str, Any]]:
    doc = get_backend().get_doc(project_id, DOC_KEY) or {}
    items = doc.get("items") if isinstance(doc, dict) else None
    return [item for item in (items or []) if isinstance(item, dict)]


def save_parts(project_id: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    """落一版零件文档：同一 `parts_id` 覆盖同一条，新内容追加（最多 20 版）。"""
    if not isinstance(doc, dict):
        raise ValueError("save_parts() 需要一份零件文档")
    record = copy.deepcopy(doc)
    parts_id, parts_hash = _identity(record)
    record["parts_id"] = parts_id
    record["parts_hash"] = parts_hash
    items = [item for item in _load_items(project_id)
             if _text(item.get("parts_id")) != parts_id]
    items.insert(0, record)
    get_backend().put_doc(project_id, DOC_KEY, {"items": items[:MAX_VERSIONS]})
    return record


def _manual_fill_overlay(project_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
    """把两份人工补录侧档**读时合并**回零件行（Spec §2.1 方案 b）。

    为什么要有这一层：`save_part_material()` / `save_part_thickness()` 写的是侧档
    （`DOC_KEY_MATERIAL` / `DOC_KEY_THICKNESS`，留痕与版本历史在它身上），而所有下游
    （单件工艺 409 判据、`summarize()` 的账、卡片 `card_row()`）都从**零件行**取数。
    以前两份侧档"只写不读"，于是"补完刷新就没了"。

    合并口径只有这一处，且**只读**：

    - 侧档按 `part_code` 取最近一版（`_part_doc_items()` 已按新→旧排列），每件只合一次；
    - 合出来的行与逐个调 `set_manual_material()` / `set_manual_thickness()` **同一形状**
      （直接复用这两个纯函数，不另写一份写字段的逻辑）；
    - 零件文档的 `parts_id` / `parts_hash` 一个字不改 —— 补录是「改行」，不是「重算零件」；
    - 侧档内容坏掉（空值 / 非正数）只跳过这一条，**不许**让读路径抛错。
    """
    overlays: Dict[str, Dict[str, Any]] = {}
    for key in (DOC_KEY_MATERIAL, DOC_KEY_THICKNESS, DOC_KEY_OUTLINE):
        for item in _part_doc_items(project_id, key):
            code = _text(item.get("part_code"))
            if not code:
                continue
            # 每个键只取**最近一版**（`_part_doc_items()` 已按新→旧排列），但三份侧档
            # 要能**同时**合回同一行：材料 / 料厚 / 轮廓出路是三件事，不是三选一。
            overlays.setdefault(code, {}).setdefault(key, item)

    out = copy.deepcopy(record)
    parts = out.get("parts") if isinstance(out, dict) else None
    if not overlays or not isinstance(parts, list):
        return out if isinstance(out, dict) else record
    for row in parts:
        if not isinstance(row, dict):
            continue
        fill = overlays.get(_text(row.get("part_code")))
        if not fill:
            continue
        material = fill.get(DOC_KEY_MATERIAL)
        thickness = fill.get(DOC_KEY_THICKNESS)
        outline = fill.get(DOC_KEY_OUTLINE)
        try:
            if material:
                row.update(set_manual_material(row, material.get("spec"),
                                              bound_by=_text(material.get("bound_by")),
                                              reason=_text(material.get("reason"))))
            if thickness:
                row.update(set_manual_thickness(row, thickness.get("thickness_mm"),
                                                bound_by=_text(thickness.get("bound_by")),
                                                reason=_text(thickness.get("reason"))))
            # 件级轮廓出路（Spec `packaging-open-outline-part-needs-a-way-out.md` §2.3）：
            # 人签的字与单件重算的结论都是**侧档**，读时合回这一行 —— 只影响这一件。
            if outline:
                if _text(outline.get("kind")) == MANUAL_OUTLINE_KIND:
                    row.update(set_manual_outline(row,
                                                  bound_by=_text(outline.get("bound_by")),
                                                  reason=_text(outline.get("reason")),
                                                  confirmed_at=_text(outline.get("confirmed_at"))))
                else:
                    row.update(set_recomputed_outline(row, outline))
        except ValueError:
            continue
    return out


def load_parts(project_id: str, parts_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    for item in _load_items(project_id):
        if parts_id is None or _text(item.get("parts_id")) == str(parts_id):
            return _manual_fill_overlay(project_id, item)
    return None


def list_parts(project_id: str) -> List[Dict[str, Any]]:
    return _load_items(project_id)


def _part_doc_items(project_id: str, key: str) -> List[Dict[str, Any]]:
    doc = get_backend().get_doc(project_id, key) or {}
    items = doc.get("items") if isinstance(doc, dict) else None
    return [item for item in (items or []) if isinstance(item, dict)]


def parts_stale_reason(stored_parts_id: Any, current_parts_id: Any) -> str:
    """单件工艺/成本结论的零件版本漂移原因 —— **唯一判据点**（Spec §2.1）。

    - 结论里没有 `parts_id`（本批之前落的）→ `parts_unknown`；
    - 当前零件文档读不到（空 / None）→ `parts_unknown`；
    - 两者都有且不同 → `parts_reparsed`；
    - 相同 → `""`。

    与 `packaging_part_solids.solids_stale_reason()` 同一套三值："比较不了"一律
    `parts_unknown`，**不许**当成"过期"或"没过期"（同一条纪律）。
    """
    stored = _text(stored_parts_id)
    current = _text(current_parts_id)
    if not stored or not current:
        return "parts_unknown"
    if stored != current:
        return "parts_reparsed"
    return ""


def _record_hash(payload: Dict[str, Any]) -> str:
    from .packaging_semantics import model as sem_model

    return sem_model.sha256_hex(sem_model.canonical_json(sem_model.json_safe(payload)))


def _load_part_doc(project_id: str, key: str, part_code: str,
                   parts_id: Optional[str] = None) -> Dict[str, Any]:
    """读这一件的结论；没跑过就回空文档（不抛错、不 404，Spec §2.1）。

    默认取**最近一版**（逐字保持今天的行为）；给了 `parts_id` 就按 `(part_code, parts_id)`
    精确读回**那一版** —— 文档本来就按这两个键分段存（见 `_save_part_doc()`），
    以前只按 `part_code` 取第一条，于是"上一版零件算的结论"永远读不回来。
    """
    wanted = _text(part_code)
    wanted_version = None if parts_id is None else _text(parts_id)
    for item in _part_doc_items(project_id, key):
        if _text(item.get("part_code")) != wanted:
            continue
        if wanted_version is not None and _text(item.get("parts_id")) != wanted_version:
            continue
        return item
    return {}


def _save_part_doc(project_id: str, key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """落一版单件结论：同一 `(part_code, parts_id, 结论内容)` **幂等**、最多 20 版。

    幂等靠内容指纹（`record_hash`）：同一份结论重复落库**不写库、不新增版本** ——
    34 上"刷新就没了"的另一面就是"重跑一次就多一版"，两边都得治。
    """
    record = copy.deepcopy(payload) if isinstance(payload, dict) else {}
    part_code = _text(record.get("part_code"))
    parts_id = _text(record.get("parts_id"))
    if not part_code:
        raise ValueError("save_part_process()/save_part_cost() 需要 part_code")
    body = {name: value for name, value in record.items() if name != "record_hash"}
    record["record_hash"] = _record_hash(body)
    items = _part_doc_items(project_id, key)
    head = items[0] if items else None
    if (isinstance(head, dict) and _text(head.get("part_code")) == part_code
            and _text(head.get("parts_id")) == parts_id
            and _text(head.get("record_hash")) == record["record_hash"]):
        return head
    kept = [item for item in items
            if not (_text(item.get("part_code")) == part_code
                    and _text(item.get("parts_id")) == parts_id)]
    kept.insert(0, record)
    get_backend().put_doc(project_id, key, {"items": kept[:MAX_VERSIONS]})
    return record


def save_part_process(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """落一版单件工艺结论（Spec §2.1）。结论**不写技术 IR**。"""
    return _save_part_doc(project_id, DOC_KEY_PROCESS, payload)


def load_part_process(project_id: str, part_code: str,
                      parts_id: Optional[str] = None) -> Dict[str, Any]:
    """读回单件工艺结论（默认最近一版；给了 `parts_id` 读那一版）；没跑过 → `{}`。"""
    return _load_part_doc(project_id, DOC_KEY_PROCESS, part_code, parts_id)


def save_part_cost(project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """落一版单件成本结论（Spec §2.1）。"""
    return _save_part_doc(project_id, DOC_KEY_COST, payload)


def load_part_cost(project_id: str, part_code: str,
                   parts_id: Optional[str] = None) -> Dict[str, Any]:
    """读回单件成本结论（默认最近一版；给了 `parts_id` 读那一版）；没跑过 → `{}`。"""
    return _load_part_doc(project_id, DOC_KEY_COST, part_code, parts_id)


# --------------------------------------------------------------------------- #
# 2b 人工补料厚（Spec `packaging-parts-thickness-facts.md` §2.5）
# --------------------------------------------------------------------------- #
def set_manual_thickness(row: Any, thickness_mm: Any, *, bound_by: str,
                         reason: str = "") -> Dict[str, Any]:
    """人工补料厚（**纯函数**）：返回副本，绝不原地改入参。

    - 写 `thickness_mm` 与 `thickness_source = {"kind": "manual", ...}`；
    - 清掉该件 `attribution` 里同字段的 `thickness_unresolved` / `thickness_material_conflict`
      （人为签字之后，这两条待办就不再挂着）；
    - `thickness_mm <= 0` 或非有限数 → `ValueError`（**不许**用 0 表示"没填"）。
    """
    value = _num(thickness_mm)
    if value is None or not math.isfinite(value) or value <= 0:
        raise ValueError("人工补料厚必须是大于 0 的有限数（不许用 0 表示「没填」）")
    updated = copy.deepcopy(row) if isinstance(row, dict) else {}
    updated["thickness_mm"] = value
    updated["thickness_source"] = {"kind": MANUAL_THICKNESS_KIND, "text": _text(reason),
                                   "bound_by": _text(bound_by), "evidence_ref": "",
                                   "distance_mm": None}
    attribution = updated.get("attribution")
    if isinstance(attribution, dict):
        attribution["thickness_unresolved"] = []
        attribution["thickness_material_conflict"] = []
        if not _text(attribution.get("kind")):
            attribution["kind"] = MANUAL_THICKNESS_KIND
    return updated


def save_part_thickness(project_id: str, part_code: str, thickness_mm: Any, *,
                        bound_by: str, reason: str = "") -> Dict[str, Any]:
    """落一版人工料厚（同一 `(part_code, 数值, 人, 理由)` 幂等，最多 20 版）。"""
    value = _num(thickness_mm)
    if value is None or not math.isfinite(value) or value <= 0:
        raise ValueError("人工补料厚必须是大于 0 的有限数（不许用 0 表示「没填」）")
    return _save_part_doc(project_id, DOC_KEY_THICKNESS, {
        "part_code": _text(part_code), "thickness_mm": value,
        "bound_by": _text(bound_by), "reason": _text(reason),
        "source_kind": MANUAL_THICKNESS_KIND, "engine_version": ENGINE_VERSION})


def load_part_thickness(project_id: str, part_code: str) -> Dict[str, Any]:
    """读回这一件人工补过的料厚（最近一版）；没补过 → `{}`。"""
    return _load_part_doc(project_id, DOC_KEY_THICKNESS, part_code)


# --------------------------------------------------------------------------- #
# 2c 人工补材料（Spec `packaging-parts-in-card-and-material-fill.md` §2.2）
#    与「补料厚」逐字同形的镜像：真图上 64 件有 51 件卡在缺材料，而材料以前**只能**
#    靠"回需求补全再整体重跑八步"，那条路本身还有顺序陷阱。
# --------------------------------------------------------------------------- #
def set_manual_material(row: Any, spec: Any, *, bound_by: str,
                        reason: str = "") -> Dict[str, Any]:
    """人工补材料（**纯函数**）：返回副本，绝不原地改入参。

    - 写 `material = {"spec": ..., "grade": "", "material_code": ""}`（保持行的材料字典形状，
      `_material_spec()` 读得到）与 `material_source = {"kind": "manual", ...}`；
    - 清掉该件 `attribution` 里同字段的 `material_unresolved`（人为签字之后，这条待办就不再挂着）；
    - `spec` 去空格后为空 → `ValueError`（**不许**用空串表示"没填"）。
    """
    text = _text(spec)
    if not text:
        raise ValueError("人工补材料不能是空白（不许用空串表示「没填」）")
    updated = copy.deepcopy(row) if isinstance(row, dict) else {}
    updated["material"] = {"spec": text, "grade": "", "material_code": ""}
    updated["material_source"] = {"kind": MANUAL_MATERIAL_KIND, "text": _text(reason),
                                 "bound_by": _text(bound_by), "evidence_ref": "",
                                 "distance_mm": None}
    attribution = updated.get("attribution")
    if isinstance(attribution, dict):
        attribution["material_unresolved"] = []
        if not _text(attribution.get("kind")):
            attribution["kind"] = MANUAL_MATERIAL_KIND
    return updated


def save_part_material(project_id: str, part_code: str, spec: Any, *,
                       bound_by: str, reason: str = "") -> Dict[str, Any]:
    """落一版人工材料（同一 `(part_code, 材料, 人, 理由)` 幂等，最多 20 版）。"""
    text = _text(spec)
    if not text:
        raise ValueError("人工补材料不能是空白（不许用空串表示「没填」）")
    return _save_part_doc(project_id, DOC_KEY_MATERIAL, {
        "part_code": _text(part_code), "spec": text,
        "bound_by": _text(bound_by), "reason": _text(reason),
        "source_kind": MANUAL_MATERIAL_KIND, "engine_version": ENGINE_VERSION})


def load_part_material(project_id: str, part_code: str) -> Dict[str, Any]:
    """读回这一件人工补过的材料（最近一版）；没补过 → `{}`。"""
    return _load_part_doc(project_id, DOC_KEY_MATERIAL, part_code)


# --------------------------------------------------------------------------- #
# 2c-bis 件级轮廓出路（Spec `packaging-open-outline-part-needs-a-way-out.md` §2.3）
#   未闭合的件在整条链上是死路：既没有入口，也没有一句话告诉用户该干什么。
#   两条出路与「补材料」/「补料厚」逐字同范式（件级侧档 + 读时合回零件行）：
#     · confirm  —— 人工签字「这一件按包围盒估算」（几何状态仍是 open，另立留痕）；
#     · recompute —— 只对这一件放大搜索额度重跑一次找环（真闭合了才改几何结论）。
# --------------------------------------------------------------------------- #
def save_part_outline(project_id: str, part_code: str, *, bound_by: str,
                      reason: str = "", confirmed_at: str = "") -> Dict[str, Any]:
    """落一版人工轮廓确认（同一 `(part_code, 人, 理由)` 幂等，最多 20 版）。"""
    by = _text(bound_by)
    if not by:
        raise ValueError("人工轮廓确认必须记名（不许匿名放行）")
    return _save_part_doc(project_id, DOC_KEY_OUTLINE, {
        "part_code": _text(part_code), "kind": MANUAL_OUTLINE_KIND,
        "rule_id": MANUAL_OUTLINE_RULE_ID, "bound_by": by,
        "reason": _text(reason), "confirmed_at": _text(confirmed_at),
        "engine_version": ENGINE_VERSION})


def recompute_outline(row: Any, ir: Any, *, scale: Any = RECOMPUTE_BUDGET_SCALE,
                      bound_by: str = "", reason: str = "",
                      confirmed_at: str = "", options: Any = None) -> Dict[str, Any]:
    """单件重算轮廓（**纯函数**：吃零件行 + CAD IR，不落库、不联网，Spec §2.3(b)）。

    只对**这一件**的分量再跑一次找环，搜索额度放大 `scale` 倍；判据、开线原因闭集与
    `_open_outline_reason()` 一律沿用（绝不新写第二套），于是"额度用完仍
    `loop_budget_exhausted`，诚实照旧"。返回一版重算结论（`changed` 说这一件是不是
    由 `open` 变成了 `closed`），落库与读回由调用方按侧档范式处理。
    """
    payload = row if isinstance(row, dict) else {}
    part_code = _text(payload.get("part_code"))
    if not part_code:
        raise ValueError("recompute_outline() 需要 part_code")
    factor = _num(scale)
    factor = (RECOMPUTE_BUDGET_SCALE if factor is None
              else max(1.0, min(float(RECOMPUTE_BUDGET_SCALE_MAX), float(factor))))
    if not isinstance(ir, dict):
        raise ValueError("recompute_outline() 需要一份 CAD IR")
    geometry = ir.get("geometry") if isinstance(ir.get("geometry"), dict) else {}
    components = [item for item in (geometry.get("components") or [])
                  if isinstance(item, dict)]
    component_id = _text(payload.get("component_id"))
    component = next((item for item in components
                      if _text(item.get("component_id")) == component_id), None)
    if component is None:
        raise ValueError("CAD IR 里找不到这一件的分量（%s），重算没有依据" % component_id)
    entities = {str((item or {}).get("entity_id")): item
                for item in (ir.get("entities") or []) if isinstance(item, dict)}
    members = [entities[item] for item in (_text(one) for one in (component.get("entity_ids") or []))
               if item in entities]
    config = _options(options)
    units = ir.get("units") if isinstance(ir.get("units"), dict) else {}
    unit_ok = _text(units.get("unit_status")) == "confirmed"
    bbox = _bbox_of(component, members)
    curves = [entity for entity in members
              if _text(entity.get("type")).upper() in CURVE_TYPES]
    evidence = _outline_evidence(members, max_states=int(MAX_LOOP_STATES * factor),
                                 max_cycles=int(MAX_LOOP_CYCLES * factor))
    outline: Optional[Dict[str, Any]] = None
    saw_loop = False
    has_coordinates = False
    if unit_ok:
        verdict = _largest_loop(evidence["loops_original"], evidence["edges"],
                                evidence["vertices"], float(config["min_area_mm2"]))
        outline = verdict["outline"]
        saw_loop = verdict["saw_loop"]
        has_coordinates = verdict["has_coordinates"]
        if outline is None:
            outline, _compose = _rescue_outline(evidence, bbox,
                                                float(config["min_area_mm2"]))
            if outline is not None:
                saw_loop = True
                has_coordinates = True
    if not unit_ok:
        status, reason_out = "unavailable", "unit_unconfirmed"
    elif outline is not None:
        status, reason_out = "closed", ""
    else:
        status = "open"
        reason_out = _open_outline_reason(has_curve=bool(curves),
                                          diagnosis=evidence["diagnosis"], saw_loop=saw_loop)
    record: Dict[str, Any] = {
        "part_code": part_code, "kind": RECOMPUTED_OUTLINE_KIND,
        "rule_id": RECOMPUTE_RULE_ID, "component_id": component_id,
        "scale": factor, "outline_status": status, "outline_reason": reason_out,
        "outline_diagnosis": dict(evidence["diagnosis"]),
        "bound_by": _text(bound_by), "reason": _text(reason),
        "confirmed_at": _text(confirmed_at), "engine_version": ENGINE_VERSION,
        "changed": bool(status == "closed"
                        and _text(payload.get("outline_status")) != "closed"),
    }
    if outline is not None:
        loop_box = outline.get("bbox") or bbox
        record["outline"] = copy.deepcopy(outline)
        record["size_source"] = "closed_outline" if has_coordinates else "dwg_outline"
        record["unfolded_length_mm"] = _round(abs(loop_box[2] - loop_box[0]))
        record["unfolded_width_mm"] = _round(abs(loop_box[3] - loop_box[1]))
        record["area_mm2"] = _round(float(outline.get("area_mm2") or 0.0))
    return record


def save_part_outline_recompute(project_id: str, record: Any) -> Dict[str, Any]:
    """落一版单件重算结论（同一 `(part_code, 结论内容)` 幂等，最多 20 版）。"""
    payload = record if isinstance(record, dict) else {}
    part_code = _text(payload.get("part_code"))
    if not part_code:
        raise ValueError("save_part_outline_recompute() 需要 part_code")
    saved: Dict[str, Any] = {"part_code": part_code, "kind": RECOMPUTED_OUTLINE_KIND,
                             "engine_version": ENGINE_VERSION}
    for name in ("rule_id", "component_id", "scale", "outline_status", "outline_reason",
                 "outline_diagnosis", "outline", "size_source", "unfolded_length_mm",
                 "unfolded_width_mm", "area_mm2", "changed", "bound_by", "reason",
                 "confirmed_at"):
        if name in payload:
            saved[name] = copy.deepcopy(payload[name])
    return _save_part_doc(project_id, DOC_KEY_OUTLINE, saved)


def load_part_outline(project_id: str, part_code: str) -> Dict[str, Any]:
    """读回这一件的轮廓出路留痕（最近一版）；没走过 → `{}`。"""
    return _load_part_doc(project_id, DOC_KEY_OUTLINE, part_code)


# --------------------------------------------------------------------------- #
# 2d 卡片零件表的「可算 / 不可算原因」列（Spec `packaging-parts-in-card-and-material-fill.md`
#    §2.1 第 2–3 条）：**唯一**判据是 `processability()`，前端不许另写一套。
# --------------------------------------------------------------------------- #
def card_columns() -> List[Dict[str, str]]:
    """卡片第 6 步「图纸拆出来的零件」的列（唯一来源，前端照抄，不自己造列）。"""
    return [dict(column) for column in CARD_COLUMNS]


def card_row(row: Any) -> Dict[str, Any]:
    """零件文档的一行 → 卡片表格行：数值原样、可算性取 `processability()` 的同一判据同文案。"""
    payload = row if isinstance(row, dict) else {}
    verdict = processability(payload)
    # 人签过字的件在卡片上必须**看得出来是人放的**（Spec §2.4）：轮廓状态那一格带上签字痕，
    # 几何列本身仍是 `open`（`closed` 只能由几何判定给出）。
    outline_status = _text(payload.get("outline_status"))
    if outline_confirmation(payload) and outline_status != "closed":
        outline_status = "%s（人工签字·按包围盒估算）" % (outline_status or "open")
    return {
        "part_code": _text(payload.get("part_code")),
        "name": _text(payload.get("name")),
        "role": _text(payload.get("role")),
        "material": _material_spec(payload.get("material")),
        "thickness_mm": _num(payload.get("thickness_mm")),
        "unfolded_length_mm": _num(payload.get("unfolded_length_mm")),
        "unfolded_width_mm": _num(payload.get("unfolded_width_mm")),
        "outline_status": outline_status,
        "processable": bool(verdict.get("ok")),
        "processable_text": "可算" if verdict.get("ok") else "不可算",
        "unprocessable_reason": "" if verdict.get("ok") else _text(verdict.get("message")),
    }


def card_rows(rows: Any) -> List[Dict[str, Any]]:
    return [card_row(row) for row in (rows or []) if isinstance(row, dict)]


# --------------------------------------------------------------------------- #
# 3 回填 BOM 行（Spec C7，行级临时口径）
# --------------------------------------------------------------------------- #
def _needs_binding(row: Dict[str, Any]) -> bool:
    """「这一行算不出尺寸」的判据：缺输入，或整行根本没有尺寸。

    刻意不用「长或宽任一为空」：像 `RB01001-P10`（`长度 = W/3 + 40`，只有长度一维）
    这种行，长度是表达式**真算出来的**，不能拿图纸零件的尺寸去覆盖它（Spec C7.6）。
    """
    if _text(row.get("bom_category")) not in BINDABLE_CATEGORIES:
        return False
    if int(row.get("locked") or 0) == 1:
        return False
    if _text(row.get("status")) == "needs_input":
        return True
    return _num(row.get("length_mm")) is None and _num(row.get("width_mm")) is None


def _size_source_of(row: Dict[str, Any]) -> Dict[str, Any]:
    """把行上的尺寸溯源读成 dict（`_assemble` 给的是 JSON 字符串）。"""
    raw = row.get("size_source")
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    text = _text(row.get("size_source_json"))
    if not text:
        return {}
    try:
        import json
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def size_quality_of(size_source: Any) -> str:
    """尺寸来源 → 可信两档（Spec `packaging-bom-part-size-provenance.md` §2.1 / §3 A2）。

    `closed_outline` / `dwg_outline` → `unfolded`（真展开）；其余（含来源缺失）→ `bbox_only`。
    缺失时**宁可说包围盒**：没有证据的数字不许被下游当成展开尺寸（§3 D3）。
    """
    return (SIZE_QUALITY_UNFOLDED if _text(size_source) in UNFOLDED_SIZE_SOURCES
            else SIZE_QUALITY_BBOX)


def bind_rows(items: Any, parts: Any, *, options: Any = None,
              author: str = "system") -> Dict[str, Any]:
    """把图纸零件回填进算不出尺寸的 BOM 行；纯函数：不改入参、不落库。

    - 只碰 `box_part` / `optional_part` 且（`needs_input` 或长宽为空）的行，锁定行绝不碰；
    - 行按出现顺序（= 模板 `seq` 升序）↔ 零件按面积降序，逐行取件；
      零件比分出的行少时**循环取件**并保留 `fallback_paired=true` 留痕
      —— 口径是"先保证有数"，正式对应表是下一批的事；
    - 留痕齐备：`size_source.dwg_binding`（component_id / part_code / rule_id /
      fallback_paired / original_missing_variables / pairing_basis / material_match）、
      `source="dwg_parts"`、`missing_variables` 清空；
    - **披露配对**（Spec 批 12 §3.4，加法不改拒绝口径）：每行留一句 `pairing_basis`；
      两类材料都已知且不同类时 `material_match=false` 并进 `pairing_review`
      （34 实测把 `RB02001-P08` 磁铁配到纸面板上，报告里原先没有任何地方看得出来）。
      `bound` / `unbound` / `gaps` 口径逐字不变 —— 正式对应表要业务签字后另立一批。

    **角色不许自动贴**（Spec `e2e-packaging-dwg-quote-tech-continuity.md` §4.4）：
    尺寸是有证据的几何事实，照旧自动回填；但零件自己在图上没写业务角色（`role` 空或
    `unknown`）时，**不能**按行号/面积顺序把模板行的角色名（"盖壁长边"…）抄上去 ——
    该行的业务角色保持 `unbound`，等人工映射。每次绑定都留 `binding_evidence` /
    `binding_method` / `bound_by`（`packaging_bom.binding_record()`）。
    """
    rows = [copy.deepcopy(row) for row in (items or []) if isinstance(row, dict)]
    # 零件**文档**身份（不是零件行）：整份 `record` 顶层就带着，逐字取用。
    doc_id = _text((parts or {}).get("parts_id")) if isinstance(parts, dict) else ""
    doc_hash = _text((parts or {}).get("parts_hash")) if isinstance(parts, dict) else ""
    available = [row for row in ((parts or {}).get("parts") or [])
                 if isinstance(row, dict)
                 and _num(row.get("unfolded_length_mm")) is not None
                 and _num(row.get("unfolded_width_mm")) is not None]
    bound = 0
    skipped_locked = 0
    unbound: List[str] = []
    role_unbound: List[Dict[str, Any]] = []
    pairing_review: List[Dict[str, Any]] = []
    pair_index = 0
    for row in rows:
        item_key = _text(row.get("item_key"))
        if _text(row.get("bom_category")) in BINDABLE_CATEGORIES \
                and int(row.get("locked") or 0) == 1 \
                and (_text(row.get("status")) == "needs_input"
                     or (_num(row.get("length_mm")) is None
                         and _num(row.get("width_mm")) is None)):
            skipped_locked += 1
            continue
        if not _needs_binding(row):
            continue
        if not available:
            unbound.append("part_size_unbound:%s" % item_key)
            continue
        part = available[pair_index % len(available)]
        order = (pair_index % len(available)) + 1
        fallback = pair_index >= len(available)
        pair_index += 1
        original_missing = list(row.get("missing_variables") or [])
        # 配对是纯位置的（行顺序 ↔ 面积降序），所以必须把"这一对是怎么配上的"写在行上，
        # 再对材料明显不同类的组合单列出来 —— 披露，不是拒绝（Spec 批 12 §3.4）。
        basis = ("位置配对：第 %d 个待绑行 ↔ 面积第 %d 大的零件%s"
                 % (pair_index, order, "（零件少于行，循环取件）" if fallback else ""))
        row_class = _material_class(row.get("material"))
        part_class = _material_class(part.get("material"))
        material_match: Optional[bool] = None
        if row_class is not None and part_class is not None:
            material_match = row_class == part_class
        source = _size_source_of(row)
        # 尺寸来源一路带到底（Spec `packaging-bom-part-size-provenance.md` §2.1）：
        # 回填行上的长宽和零件文档那一行是同一个数字，就必须带同一份来源 ——
        # 否则"未闭合零件的包围盒"和"闭合轮廓的真展开"在任何读接口上都长得一样。
        # 业务角色：只认零件图上写的；写不出来就把行标成 unbound（Spec §4.4）。
        role_guard = packaging_bom.reject_unknown_role_autobind(part, row)
        if not role_guard["autobind"]:
            role_unbound.append({"item_key": item_key,
                                 "part_code": _text(part.get("part_code")),
                                 "reason": role_guard["reason"]})
        size_binding = {
            "component_id": _text(part.get("component_id")),
            "part_code": _text(part.get("part_code")),
            # 「这一对尺寸是照哪一版零件文档配的」（Spec `packaging-bom-parts-version-binding.md` §2.1）：
            # 逐字取入参**文档**顶层那一份，文档没给就留空 —— 绝不用零件行/行号/时间编一个版本。
            "parts_id": doc_id,
            "parts_hash": doc_hash,
            "rule_id": BINDING_RULE_ID,
            "fallback_paired": bool(fallback),
            "original_missing_variables": original_missing,
            "pairing_basis": basis,
            "material_match": material_match,
            "size_source": _text(part.get("size_source")),
            "outline_status": _text(part.get("outline_status")),
            "size_quality": size_quality_of(part.get("size_source")),
        }
        record = packaging_bom.binding_record(
            part, row, bound_by=author, method="auto_position_area",
            evidence={"rule_id": BINDING_RULE_ID, "pairing_basis": basis,
                      "size_quality": size_binding["size_quality"],
                      "fallback_paired": bool(fallback)})
        size_binding.update({"role_value": role_guard["role_value"],
                             "role_rule": role_guard["reason"] or "role_declared",
                             **record})
        source["dwg_binding"] = size_binding
        if material_match is False:
            pairing_review.append({
                "item_key": item_key,
                "part_code": _text(part.get("part_code")),
                "row_material": _text(row.get("material")),
                "part_material": _text(part.get("material")),
                "material_match": False,
            })
        row["length_mm"] = _num(part.get("unfolded_length_mm"))
        row["width_mm"] = _num(part.get("unfolded_width_mm"))
        row["size_source"] = source
        row["size_source_json"] = copy.deepcopy(source)
        row["missing_variables"] = []
        row["source"] = "dwg_parts"
        row["status"] = "computed"
        # 行的业务角色跟着证据走：零件没写角色时保持 unbound，绝不照抄模板角色名。
        row["part_role"] = role_guard["role_value"]
        row["binding_method"] = record["binding_method"]
        row["bound_by"] = record["bound_by"]
        bound += 1
    return {"items": rows, "bound": bound, "unbound": unbound,
            "skipped_locked": skipped_locked, "gaps": list(unbound),
            "pairing_review": pairing_review,
            "role_unbound": role_unbound, "role_unbound_total": len(role_unbound),
            "rule_id": BINDING_RULE_ID, "engine_version": ENGINE_VERSION,
            # 顶层也带一份文档身份（Spec §2.1）：落库/比对用，缺省 `""`。
            "parts_id": doc_id, "parts_hash": doc_hash}

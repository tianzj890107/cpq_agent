"""DWG 图纸解析会话链路：闭集、规范哈希与摘要（第 5 批 Spec §2/§3）。

本模块只放常量与纯函数，不碰 `store`、不碰任何依赖缝，也不做网络/模型调用。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterator, List, Tuple

FLOW_VERSION = "packaging-drawing-flow/1"
ANCHOR_VERSION = "packaging-flow-anchor/1"
STALE_VERSION = "packaging-drawing-stale/1"

STEP_IDS = ("file_preflight", "dwg_convert", "cad_ir_parse", "packaging_semantics",
            "parts_extract", "field_write", "pending_confirm", "downstream_prepare")

STEP_TITLES = {
    "file_preflight": "文件预检",
    "dwg_convert": "DWG 转换",
    "cad_ir_parse": "CAD 矢量解析",
    "packaging_semantics": "包装语义识别",
    "parts_extract": "零件提取",
    "field_write": "字段写入",
    "pending_confirm": "待确认生成",
    "downstream_prepare": "后续任务准备",
}

STEP_STATUSES = ("pending", "running", "completed", "failed", "blocked",
                 "unavailable", "skipped")

#: 终态（其后步骤必须保持 pending）。**blocked 不在其中**：缺前置条件不是"这一步坏了"，
#: 后续步骤照样要跑到终态（见 Spec `drawing-flow-error-taxonomy.md` C4）。
STEP_TERMINAL_FAILURES = ("failed", "unavailable")

#: 前置条件缺失 → 稳定码、对外文案与下一步动作（同一份给 `steps.field_write` 与
#: `preconditions()` 用，免得两处各写一句、越走越远）。
#: 这类问题的共同点是"重试同一个入口必然再失败"，所以 `status=blocked`、`retryable=False`。
PRECONDITION_BLOCKERS = {
    "REQUIREMENT_DRAFT_MISSING": {
        "message": "需求单不存在，请先创建需求草稿（缺前置条件，重试不会成功）",
        "action": "先在需求看板为这个项目建一张需求草稿，再回到图纸解析重跑",
    },
    # 需求已进入流程（pending_confirmation / pending_review / approved）：不许静默改写，
    # 重试同一个入口必然再失败（Spec `drawing-flow-non-editable-requirement.md` §2/§3）。
    "REQUIREMENT_NOT_EDITABLE": {
        "message": "需求已提交或已进入确认/审核流程，当前状态不可直接改写（缺前置条件，重试不会成功）",
        "action": "把这张需求单退回草稿，或为该项目新建一张需求草稿，再回到图纸解析重跑",
    },
}

FIELD_BOARD_STATES = ("written", "pending", "conflict", "missing", "preserved", "skipped")

GATE_STAGES = ("box_match", "bom", "route", "cost", "quote_draft", "quote_publish")

STALE_REASONS = ("drawing_version_changed", "source_sha256_changed", "ir_hash_changed",
                 "semantics_hash_changed", "requirement_snapshot_changed",
                 "converter_version_changed")

#: Spec §2.2 的九个依赖缝（capability() 按此登记）。
DEPENDENCIES = ("file_preflight", "cad_converter", "cad_ir", "packaging_semantics",
                "requirement_service", "packaging_match", "packaging_bom",
                "packaging_route", "packaging_cost")

#: 依赖状态的闭集（Spec `packaging-flow-dependency-probe-truth.md` §2.1）：
#: `ok` / `missing`（这个部署没有它）/ `import_failed`（装了但装载失败）/ `unknown`（没登记过）。
DEPENDENCY_STATES = ("ok", "missing", "import_failed", "unknown")

#: 依赖状态登记表：**模块级真 dict**（测试要能 clear），键为依赖名。
#: "没登记过" ≠ "没有这个依赖"，读侧一律给 `unknown`（Spec §2.1）。
DEPENDENCY_STATE_REGISTRY: Dict[str, Dict[str, Any]] = {}

#: Spec §9 —— 本批新增的两条码（HTTP, retryable）。
ERROR_CODES = {
    "PACKAGING_GATE_BLOCKED": (409, True),
    "PACKAGING_FLOW_DEPENDENCY_MISSING": (500, False),
    # 需求不可编辑（前置条件）与其它业务规则拒绝：都是 409 且**重试不会成功**。
    "REQUIREMENT_NOT_EDITABLE": (409, False),
    "REQUIREMENT_SAVE_REJECTED": (409, False),
    # 零件提取的前置条件（没有 IR / 提取模块不可用）：都是 409、重试不会成功，
    # 但**不是**终态失败 —— 后续步骤照旧要跑到终态。
    "PACKAGING_PARTS_NO_IR": (409, False),
    "PACKAGING_PARTS_UNAVAILABLE": (409, False),
    # 零件提取时**读不到**上一版 CAD IR（文档通道抛异常）：IR 本来就在，是**可重试的读取故障**，
    # 不是"还没有解析结果"、也不是缺前置条件（Spec `packaging-parts-ir-read-failure.md` §2.2/§2.4）。
    "PACKAGING_PARTS_IR_UNAVAILABLE": (503, True),
    # 源图纸"读不到"（blob 通道抛异常）：是**可重试的读取故障**，不是缺前置条件、
    # 也不是"文件是空的"（Spec `packaging-drawing-source-read-failure.md` §2.2/§2.3）。
    "DRAWING_SOURCE_UNAVAILABLE": (503, True),
}

#: 步骤产出的"证据位"清单（steps() 的 produces）。
_PRODUCES = {
    "file_preflight": ("detected_format", "dwg_version", "sha256", "file_size"),
    "dwg_convert": ("conversion_id", "status", "quality", "warning_count", "error_count"),
    "cad_ir_parse": ("ir_id", "ir_hash", "ir_version", "unit_status"),
    "packaging_semantics": ("semantics_id", "semantics_hash", "stats", "unresolved_total"),
    "parts_extract": ("parts_id", "parts_hash", "parts_total", "filtered_total", "truncated",
                      "unavailable"),
    "field_write": ("fields", "written", "pending", "conflict", "missing", "preserved"),
    "pending_confirm": ("needs_confirmation", "conflict", "missing"),
    "downstream_prepare": ("downstream", "gates"),
}

_DEPENDS_ON = {
    "file_preflight": (),
    "dwg_convert": ("file_preflight",),
    "cad_ir_parse": ("dwg_convert",),
    "packaging_semantics": ("cad_ir_parse",),
    "parts_extract": ("packaging_semantics",),
    "field_write": ("packaging_semantics",),
    "pending_confirm": ("field_write",),
    "downstream_prepare": ("pending_confirm",),
}

#: 字段中文名（门禁与过程行文案用；未知字段退回键名本身）。
FIELD_LABELS = {
    "inner_length": "内长",
    "inner_width": "内宽",
    "inner_height": "内高",
    "closure_type": "闭合方式",
    "face_paper_gsm": "面纸克重",
    "v_groove": "V槽",
    "quote_quantity": "报价数量",
    "box_type": "盒型",
    "packaging_product_name": "包装产品名称",
    "packaging_category": "包装品类",
}

#: 语义来源 → 中文（过程行文案用）。
ORIGIN_LABELS = {
    "confirmed_from_cad": "CAD 标注",
    "inferred_from_geometry": "几何推断",
    "inferred_by_model": "模型推断",
    "user_confirmed": "人工确认",
    "missing": "图纸缺失",
    "conflict": "证据冲突",
    "unresolved": "未决",
}

#: 字段板状态 → 过程行中文（Spec §4.3 的 board 映射）。
BOARD_LABELS = {
    "written": "已写入",
    "preserved": "保留人工确认值",
    "pending": "待确认",
    "conflict": "证据冲突",
    "missing": "待补",
    "skipped": "已跳过",
}


class DrawingFlowError(Exception):
    """链路自身的稳定错误（Spec §9）；属性名与第 1～4 批一致。"""

    def __init__(self, stable_error_code: str, http_status: int = 500,
                 retryable: bool = False, message: str = "") -> None:
        self.stable_error_code = str(stable_error_code)
        self.http_status = int(http_status)
        self.retryable = bool(retryable)
        self.message = str(message or stable_error_code)
        super().__init__(self.message)


def label_of(field: str) -> str:
    key = str(field or "")
    return FIELD_LABELS.get(key) or key


def origin_label(origin: str) -> str:
    key = str(origin or "")
    return ORIGIN_LABELS.get(key) or (key or "未知来源")


def steps() -> List[Dict[str, Any]]:
    """执行顺序即 STEP_IDS（Spec §2.1）。"""
    plan: List[Dict[str, Any]] = []
    for index, step_id in enumerate(STEP_IDS):
        plan.append({
            "step_id": step_id,
            "title": STEP_TITLES[step_id],
            "index": index,
            "depends_on": list(_DEPENDS_ON.get(step_id) or ()),
            "produces": list(_PRODUCES.get(step_id) or ()),
        })
    return plan


def canonical_prompt(prompt: Any) -> str:
    """规范化提示词：折叠空白（run_id 要去掉无关格式差异）。"""
    return re.sub(r"\s+", " ", str(prompt or "")).strip()


def run_id_for(project_id: str, *, prompt: Any = "", source_sha256: Any = "",
               drawing_version: Any = 0, requirement_snapshot_version: Any = "") -> str:
    """`flow-<16 hex>`：同一附件 + 同一需求快照 = 同一个 run_id（Spec §6.2）。"""
    parts = [str(source_sha256 or ""),
             str(_as_int(drawing_version)),
             str(requirement_snapshot_version or ""),
             canonical_prompt(prompt)]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return "flow-" + digest[:16]


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def jsonable(value: Any) -> Any:
    """把任意结构洗成 JSON 安全（无 Path / bytes / NaN）。"""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return value
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return ""
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(jsonable(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def digest16(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:16]


def migrate(doc: Any) -> Dict[str, Any]:
    """版本迁移：不认识的版本一律不猜（Spec §6.4）。"""
    payload = doc if isinstance(doc, dict) else {}
    version = str(payload.get("flow_version") or "")
    if not version:
        return {"status": "needs_rebuild", "reason": "missing_flow_version"}
    if version != FLOW_VERSION:
        raise ValueError("未知的流程版本：%s" % version)
    return {"status": "ok"}


def summarize(state: Any) -> Dict[str, Any]:
    """小摘要：只给计数与状态，绝不塞字段证据或实体明细（Spec §2.1）。"""
    payload = state if isinstance(state, dict) else {}
    steps_in = payload.get("steps") if isinstance(payload.get("steps"), list) else []
    step_rows = [{"step_id": str((row or {}).get("step_id") or ""),
                  "status": str((row or {}).get("status") or ""),
                  "attempt": _as_int((row or {}).get("attempt"), 1)}
                 for row in steps_in if isinstance(row, dict)]
    pending = payload.get("pending") if isinstance(payload.get("pending"), dict) else {}
    downstream = payload.get("downstream") if isinstance(payload.get("downstream"), dict) else {}
    return {
        "flow_version": str(payload.get("flow_version") or FLOW_VERSION),
        "project_id": str(payload.get("project_id") or ""),
        "run_id": str(payload.get("run_id") or ""),
        "status": str(payload.get("status") or ""),
        "step_total": len(step_rows),
        "steps": step_rows,
        "pending_counts": {
            "needs_confirmation": len(pending.get("needs_confirmation") or []),
            "conflict": len(pending.get("conflict") or []),
            "missing": len(pending.get("missing") or []),
            "total": _as_int(pending.get("total"), 0),
        },
        "downstream_status": {str(stage): str((row or {}).get("status") or "")
                              for stage, row in downstream.items() if isinstance(row, dict)},
        "stale": bool((payload.get("stale") or {}).get("stale")),
    }


def iter_step_ids(start: str = "") -> Iterator[str]:
    """从 start（含）开始的后续步骤；start 为空则全量。"""
    if not start:
        for step_id in STEP_IDS:
            yield step_id
        return
    if start not in STEP_IDS:
        raise ValueError("未知的步骤：%s" % start)
    for step_id in STEP_IDS[STEP_IDS.index(start):]:
        yield step_id


def error_meta(code: str) -> Tuple[int, bool]:
    return ERROR_CODES.get(str(code), (500, False))


def note_dependency_state(name: str, state: str, *, reason: str = "",
                          message: str = "") -> Dict[str, Any]:
    """登记某个依赖这一次的状态（Spec `packaging-flow-dependency-probe-truth.md` §2.1）。

    唯一一处写入口：`state` 收进闭集（越界一律折成 `unknown`），`reason` 是异常类名，
    `message` 截到前 200 字（排障要能直接看到 `No module named 'ods'` 这种原文）。
    """
    key = str(name or "")
    state = str(state or "")
    if state not in DEPENDENCY_STATES:
        state = "unknown"
    entry = {"name": key, "state": state,
             "reason": str(reason or ""),
             "message": str(message or "")[:200]}
    if key:
        DEPENDENCY_STATE_REGISTRY[key] = entry
    return entry


def dependency_state(name: str = "") -> Dict[str, Any]:
    """依赖登记体（Spec §2.1）：给了名字给一条，没给给全表（按名字升序）。

    **没有登记过 → `unknown`**（不许编成 `missing`）："没登记过" ≠ "没有这个依赖"。
    """
    key = str(name or "")
    if key:
        entry = DEPENDENCY_STATE_REGISTRY.get(key)
        if isinstance(entry, dict):
            return dict(entry)
        return {"name": key, "state": "unknown", "reason": "", "message": ""}
    return {name: dict(DEPENDENCY_STATE_REGISTRY[name])
            for name in sorted(DEPENDENCY_STATE_REGISTRY)}

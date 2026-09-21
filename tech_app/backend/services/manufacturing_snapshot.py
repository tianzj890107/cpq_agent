# -*- coding: utf-8 -*-
"""唯一的项目制造快照适配层（Spec `e2e-packaging-downstream-handoff-report.md` §2）。

2.2（组装与整合）、2.3（成本测算）与 3.3（工艺评估报告）原来各自去 `store.load_ir()`
读 legacy `DesignIR`。包装行业根本不写那份 IR —— 它的零件、BOM、工艺路线与成本分别
落在 packaging parts / packaging_bom / packaging_route / packaging_cost 四份文档里。
于是包装项目在这三步上都报"请先完成 2.1"或"项目不存在"：不是数据没有，是**没人把四份
文档拼成一份统一的制造快照**。

本模块就是那唯一的一层：

    parts               零件清单（包装：packaging parts；其它：legacy IR 的 parts）
    bom                 物料清单（包装：packaging_bom；其它：legacy IR 的结构）
    process_route       工艺路线（包装：packaging_route；其它：legacy IR 的工序）
    cost                成本（包装：packaging_cost；其它：legacy IR/2.2 的成本）
    requirement_revision 需求单版本与来源指纹（哪一版需求算出来的）
    source_fingerprints 每份来源文档的指纹 + 整份快照的指纹

业务层**只读这一份**，不再各自猜来源；`as_design_ir()` 是唯一把包装零件投影成既有
`DesignIR` 的适配点（复用 `packaging_parts.as_ir_part()`，不新建第二套零件语义）。
本模块**只读**：不写盘、不写生产数据、不产生审计。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from ..storage import store
from ..time_utils import now_cst_str

SNAPSHOT_VERSION = "manufacturing-snapshot/1"
PACKAGING_INDUSTRY = "packaging"

#: 快照来源（Spec §2）：包装走 packaging 文档，其余行业继续读 legacy IR。
SOURCE_PACKAGING = "packaging"
SOURCE_LEGACY = "legacy"

#: 快照的六个标准分区（Spec §2 逐字）。
SNAPSHOT_KEYS = ("parts", "bom", "process_route", "cost", "requirement_revision",
                 "source_fingerprints")

#: 缺来源时的可执行缺口（Spec §2 的"业务层不得分别猜来源"）。
UNAVAILABLE_MESSAGES = {
    "packaging_parts": "尚未跑过 2.1 一键解析：没有零件文档",
    "packaging_bom": "尚未展开部件：没有包装 BOM",
    "packaging_route": "尚未生成工艺路线",
    "packaging_cost": "尚未测算包装成本",
    "legacy_ir": "尚未完成 2.1 图纸解析：没有零件 IR",
}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_safe(value: Any) -> Any:
    """把任意结构收敛成可哈希 JSON（`Decimal`/`set` 之类不参与指纹计算就跳过）。"""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def fingerprint(value: Any) -> str:
    """来源指纹（Spec §5「来源指纹可追溯」）：稳定 JSON 的 sha256，逐字可复算。"""
    payload = json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def industry_of(project_id: str) -> str:
    """需求单里的行业（包装链路识别用）；读不到/没有需求单 → 空串。"""
    try:
        requirement = store.load_requirement(project_id) or {}
    except Exception:                       # noqa: BLE001 - 读不出来按"不是包装"
        return ""
    data = requirement.get("data") or {}
    return _text(data.get("industry") or requirement.get("industry"))


def is_packaging(project_id: str) -> bool:
    return industry_of(project_id) == PACKAGING_INDUSTRY


def requirement_revision(project_id: str) -> dict:
    """需求单的修订信息（Spec §2）：版本号、状态、指纹与时间，读不到给空壳。"""
    try:
        requirement = store.load_requirement(project_id) or {}
    except Exception:                       # noqa: BLE001
        requirement = {}
    if not requirement:
        return {"requirement_no": "", "revision": 0, "status": "", "industry": "",
                "fingerprint": "", "updated_at": ""}
    data = requirement.get("data") or {}
    revision = requirement.get("revision") or data.get("revision") or 0
    return {
        "requirement_no": _text(requirement.get("requirement_no")),
        "revision": int(revision or 0),
        "status": _text(requirement.get("status")),
        "industry": _text(data.get("industry") or requirement.get("industry")),
        "fingerprint": fingerprint(data),
        "updated_at": _text(requirement.get("updated_at")
                            or requirement.get("submitted_at")),
    }


def legacy_ir(project_id: str) -> dict:
    """legacy `DesignIR` 原始字典；没有就给 `{}`（不抛错）。"""
    try:
        return dict(store.load_ir(project_id) or {})
    except Exception:                       # noqa: BLE001
        return {}


# --------------------------------------------------------------------------- #
# 包装来源
# --------------------------------------------------------------------------- #
def packaging_parts_doc(project_id: str) -> dict:
    from . import packaging_parts

    try:
        return dict(packaging_parts.load_parts(project_id) or {})
    except Exception:                       # noqa: BLE001
        return {}


def packaging_bom_doc(project_id: str, requirement_no: str = "") -> dict:
    from . import packaging_bom

    try:
        return dict(packaging_bom.load_bom(project_id, requirement_no) or {})
    except Exception:                       # noqa: BLE001
        return {}


def packaging_route_doc(project_id: str, requirement_no: str = "") -> dict:
    from . import packaging_route

    try:
        return dict(packaging_route.load_route(project_id, requirement_no) or {})
    except Exception:                       # noqa: BLE001
        return {}


def packaging_cost_doc(project_id: str, requirement_no: str = "",
                       scenario: Optional[str] = None) -> dict:
    from . import packaging_cost

    try:
        return dict(packaging_cost.load_cost(project_id, requirement_no,
                                             scenario=scenario) or {})
    except Exception:                       # noqa: BLE001
        return {}


def packaging_snapshot(project_id: str, requirement_no: str = "",
                       scenario: Optional[str] = None) -> dict:
    """包装链路的一份快照：四份文档 + 每份的指纹 + 缺口（不抛错、不猜来源）。"""
    parts_doc = packaging_parts_doc(project_id)
    bom = packaging_bom_doc(project_id, requirement_no)
    route = packaging_route_doc(project_id, requirement_no)
    cost = packaging_cost_doc(project_id, requirement_no, scenario)
    req_no = _text(requirement_no or parts_doc.get("requirement_no")
                   or bom.get("requirement_no"))
    unavailable: list = []
    if not parts_doc:
        unavailable.append(_gap("packaging_parts"))
    if not bom.get("built"):
        unavailable.append(_gap("packaging_bom"))
    if not route.get("built"):
        unavailable.append(_gap("packaging_route"))
    if not cost.get("built"):
        unavailable.append(_gap("packaging_cost"))
    parts = [dict(row) for row in (parts_doc.get("parts") or [])
             if isinstance(row, dict)]
    return {
        "source": SOURCE_PACKAGING,
        "requirement_no": req_no,
        "parts": parts,
        "bom": bom,
        "process_route": route,
        "cost": cost,
        "unavailable": unavailable,
        "fingerprints": {
            "packaging_parts": fingerprint({k: v for k, v in parts_doc.items()
                                            if k != "loaded_at"}),
            "packaging_bom": fingerprint(bom.get("items") or []),
            "packaging_route": fingerprint(route.get("steps") or []),
            "packaging_cost": fingerprint(cost.get("items") or []),
        },
    }


def legacy_snapshot(project_id: str, requirement_no: str = "") -> dict:
    """非包装行业的一份快照：仍是 legacy IR，但套进同一套键，业务层读法一致。"""
    ir = legacy_ir(project_id)
    parts = [dict(row) for row in (ir.get("parts") or []) if isinstance(row, dict)]
    unavailable: list = []
    if not parts:
        unavailable.append(_gap("legacy_ir"))
    integration_plan: dict = {}
    try:
        from . import integration as _integration
        integration_plan = dict(_integration.load_plan(project_id).model_dump() or {})
    except Exception:                       # noqa: BLE001
        integration_plan = {}
    return {
        "source": SOURCE_LEGACY,
        "requirement_no": _text(requirement_no or ir.get("requirement_no")),
        "parts": parts,
        "bom": {"built": bool(parts), "items": ir.get("bom") or []},
        "process_route": {"built": bool(ir.get("processes")),
                          "steps": ir.get("processes") or []},
        "cost": integration_plan.get("cost") or {},
        "unavailable": unavailable,
        "fingerprints": {
            "legacy_ir": fingerprint(ir),
            "integration_plan": fingerprint(integration_plan),
        },
    }


def _gap(source: str) -> dict:
    return {"source": source, "code": "missing_source:%s" % source,
            "message": UNAVAILABLE_MESSAGES.get(source, "缺少来源文档 %s" % source)}


def load_snapshot(project_id: str, requirement_no: str = "",
                  scenario: Optional[str] = None) -> dict:
    """读一份统一制造快照（Spec §2 的唯一下游投影）。

    `source` 说清这次读的是哪一条链路；`ready` 说清下游能不能据此干活（2.2/2.3/报告
    都先看它，不再各自去 `store.load_ir()` 猜）。`unavailable` 逐条点名缺什么，**绝不**
    用"IR 为空"概括包装链路的状态。
    """
    packaging = is_packaging(project_id)
    if packaging:
        base = packaging_snapshot(project_id, requirement_no, scenario)
    else:
        base = legacy_snapshot(project_id, requirement_no)
    parts = base["parts"]
    route_steps = (base["process_route"] or {}).get("steps") or []
    cost = base["cost"] or {}
    cost_ready = bool(cost.get("built"))
    # 包装链路的"能干活"= 有零件；成本是后一步，缺了只记 unavailable，不挡 2.2。
    ready = bool(parts) if packaging else bool(parts)
    snapshot = {
        "version": SNAPSHOT_VERSION,
        "project_id": project_id,
        "industry": industry_of(project_id),
        "source": base["source"],
        "requirement_no": base.get("requirement_no") or _text(requirement_no),
        "parts": parts,
        "parts_total": len(parts),
        "bom": base["bom"],
        "process_route": base["process_route"],
        "process_total": len(route_steps),
        "cost": cost,
        "cost_total": _num(cost.get("total_cost")) or 0.0,
        "cost_ready": cost_ready,
        "gaps_total": len(cost.get("gaps") or []),
        "requirement_revision": requirement_revision(project_id),
        "unavailable": base["unavailable"],
        "ready": ready,
        "loaded_at": now_cst_str(),
    }
    snapshot["source_fingerprints"] = {
        "snapshot": fingerprint({key: snapshot[key] for key in SNAPSHOT_KEYS
                                if key in snapshot}),
        **(base.get("fingerprints") or {}),
    }
    return snapshot


def blocked_message(snapshot: dict) -> str:
    """快照不 ready 时给用户的一句话：点到具体缺哪一份文档，不用"IR 为空"概括。"""
    payload = snapshot if isinstance(snapshot, dict) else {}
    gaps = payload.get("unavailable") or []
    if payload.get("source") == SOURCE_PACKAGING:
        details = "；".join(_text(gap.get("message")) for gap in gaps) or "包装制造快照不完整"
        return "包装项目还缺前置结果：%s" % details
    return "请先完成 2.1 图纸解析，2.2 需要已确认的零件清单"


def as_design_ir(project_id: str, snapshot: Optional[dict] = None) -> Any:
    """把快照投影成既有 `DesignIR`（**唯一**适配点）。

    包装链路把 packaging parts 逐件走 `packaging_parts.as_ir_part()`；其余行业直接
    还原 legacy IR。零件清单为空时抛 `ValueError`（调用方翻成既有的 400 文案）。
    """
    from ..models.ir import DesignIR

    payload = snapshot if isinstance(snapshot, dict) else load_snapshot(project_id)
    if not payload.get("ready"):
        raise ValueError(blocked_message(payload))
    if payload.get("source") == SOURCE_PACKAGING:
        from . import packaging_parts as _parts

        parts = []
        for row in payload.get("parts") or []:
            part = _parts.as_ir_part(row)
            parts.append(part.model_dump() if hasattr(part, "model_dump") else part)
        device_name = _text(payload.get("requirement_no")) or project_id
        try:
            title = _text((store.load_requirement(project_id) or {}).get("product_name"))
            device_name = title or device_name
        except Exception:                   # noqa: BLE001
            pass
        return DesignIR(
            device_name=device_name,
            design_intent=("包装图纸零件（%d 件）：由 packaging parts 文档投影，"
                           "尺寸与轮廓状态逐件来自 2.1 零件提取" % len(parts)),
            overall_dims=_overall_dims(parts),
            assembly_notes="包装链路无 legacy 总成树：零件清单即整机组成",
            parts=parts)
    ir_dict = legacy_ir(project_id)
    if not ir_dict:
        raise ValueError(blocked_message(payload))
    return DesignIR(**ir_dict)


def _overall_dims(parts: list) -> Optional[str]:
    """包装项目的整机外形描述：取各件展开尺寸的最大值（只是描述，不参与任何计算）。"""
    lengths = [_num(row.get("unfolded_length_mm")) for row in parts
               if isinstance(row, dict)]
    widths = [_num(row.get("unfolded_width_mm")) for row in parts
              if isinstance(row, dict)]
    lengths = [value for value in lengths if value]
    widths = [value for value in widths if value]
    if not lengths or not widths:
        return None
    return "%s x %s mm（展开件最大外形，仅描述）" % (max(lengths), max(widths))


def summarize(snapshot: dict) -> dict:
    """给看板/报告用的一行摘要（键固定，缺什么就给 0 / 空串，不返回 null）。"""
    payload = snapshot if isinstance(snapshot, dict) else {}
    bom = payload.get("bom") or {}
    route = payload.get("process_route") or {}
    cost = payload.get("cost") or {}
    return {
        "source": _text(payload.get("source")),
        "parts_total": int(payload.get("parts_total") or 0),
        "bom_items": len(bom.get("items") or []),
        "process_total": int(payload.get("process_total") or 0),
        "cost_total": _num(cost.get("total_cost")) or 0.0,
        "cost_ready": bool(payload.get("cost_ready")),
        "gaps_total": int(payload.get("gaps_total") or 0),
        "ready": bool(payload.get("ready")),
        "requirement_no": _text(payload.get("requirement_no")),
        "source_fingerprint": _text((payload.get("source_fingerprints") or {}).get("snapshot")),
    }

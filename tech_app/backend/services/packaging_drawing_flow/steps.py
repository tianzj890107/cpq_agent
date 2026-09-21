"""七个步骤的执行体（第 5 批 Spec §3）：只调依赖缝，不自己实现转换/解析/几何。"""
from __future__ import annotations

import datetime
from typing import Any, Callable, Dict, List, Optional

from tech_app.backend.storage import store

from . import model, persistence

Resolver = Callable[[str], Optional[Any]]

#: 单位未确认时不许把绝对尺寸当成已确认（第 4 批铁律）。
SIZE_FIELDS = ("inner_length", "inner_width", "inner_height")


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")


def _resolve(ctx: Dict[str, Any], name: str) -> Optional[Any]:
    resolver = ctx.get("resolve")
    if not callable(resolver):
        return None
    try:
        return resolver(name)
    except Exception:
        return None


def _unavailable(name: str) -> Dict[str, Any]:
    http_status, retryable = model.error_meta("PACKAGING_FLOW_DEPENDENCY_MISSING")
    return {"status": "unavailable", "error_code": "PACKAGING_FLOW_DEPENDENCY_MISSING",
            "error_message": "图纸解析链路依赖的能力尚未就绪（%s），请联系系统管理员" % name,
            "retryable": retryable, "detail": {"dependency": name, "http_status": http_status}}


def _blocked(code: str, message: str, detail: Optional[dict] = None,
             action: str = "") -> Dict[str, Any]:
    """前置条件缺失：不是执行失败，因此 `retryable=False`（重试同一入口必然再失败）。"""
    payload = dict(detail or {})
    if action:
        payload["action"] = str(action)
    return {"status": "blocked", "error_code": str(code),
            "error_message": str(message), "retryable": False,
            "detail": payload}


def _failed(code: str, message: str, detail: Optional[dict] = None,
            retryable: bool = True) -> Dict[str, Any]:
    return {"status": "failed", "error_code": str(code),
            "error_message": str(message), "retryable": bool(retryable),
            "detail": dict(detail or {})}


def _emit(project_id: str, run_id: str, key: str, text: str,
          field: Optional[dict] = None) -> dict:
    event = {"kind": "session-note", "source": "flow", "stage": "drawing",
             "key": "flow:%s:%s" % (run_id, key), "text": str(text)}
    if field is not None:
        event["field"] = field
    return store.append_session_event(project_id, event)


# --------------------------------------------------------------------------- #
# 1 文件预检
# --------------------------------------------------------------------------- #
def file_preflight(ctx: Dict[str, Any]) -> Dict[str, Any]:
    module = _resolve(ctx, "file_preflight")
    if module is None:
        return _unavailable("file_preflight")
    detect = getattr(module, "detect_file_format", None)
    if not callable(detect):
        return _unavailable("file_preflight")
    try:
        detected = detect(ctx.get("filename") or "", ctx.get("content") or b"")
    except Exception as exc:
        return _failed("FILE_PREFLIGHT_FAILED", "图纸文件预检失败，请重新上传后再试",
                       {"reason": type(exc).__name__})
    detail = {"detected_format": str(detected.get("detected_format") or ""),
              "dwg_version": str(detected.get("dwg_version") or ""),
              "sha256": str(detected.get("sha256") or ""),
              "file_size": model._as_int(detected.get("file_size")),
              "is_empty": bool(detected.get("is_empty")),
              "is_truncated": bool(detected.get("is_truncated")),
              "content_kind": str(detected.get("content_kind") or "")}
    if detail["is_empty"]:
        return _failed("FILE_EMPTY", "上传的图纸是空文件，请重新上传", detail, False)
    if detail["detected_format"] not in ("dwg", "dxf"):
        return _failed("FILE_FORMAT_UNSUPPORTED",
                       "图纸格式不受支持，请上传 DWG 或 DXF 文件", detail, False)
    # 第 1 批的 `is_truncated` 只是「0x80 哨兵」的离线启发式（R2004+ 还要解加密哨兵），
    # 对最小可识别夹具会给出假阳性。流层不据此判死：把事实原样带进 detail，
    # 真正判断文件能不能用交给第 2 批转换质量门槛（Spec §3 第 2 步）。
    if detail["is_truncated"]:
        detail["warnings"] = ["FILE_TRUNCATED_SUSPECTED"]
    return {"status": "completed", "detail": detail,
            "anchor_updates": {"source_sha256": detail["sha256"],
                               "drawing_version": model._as_int(ctx.get("drawing_version"), 1)}}


# --------------------------------------------------------------------------- #
# 2 DWG 转换
# --------------------------------------------------------------------------- #
def dwg_convert(ctx: Dict[str, Any]) -> Dict[str, Any]:
    detected = _detected_format(ctx)
    if detected == "dxf":
        return {"status": "skipped", "detail": {"reason": "dxf_no_conversion_needed"},
                "progress": "已跳过：无需转换"}
    module = _resolve(ctx, "cad_converter")
    if module is None:
        return _unavailable("cad_converter")
    convert = getattr(module, "convert_drawing", None)
    if not callable(convert):
        return _unavailable("cad_converter")
    try:
        manifest = convert(ctx.get("project_id"), ctx.get("filename") or "",
                           ctx.get("content") or b"",
                           drawing_version=model._as_int(ctx.get("drawing_version"), 1))
    except Exception as exc:
        code = str(getattr(exc, "stable_error_code", "") or "DWG_CONVERSION_FAILED")
        # 真因优先：我们自己的 DrawingFlowError 的 str() 就是它带的消息，别的异常打印
        # 自身文案 —— 不再 sniff 一个多数异常都没有的 `.message`（Spec C2）。
        message = str(exc) or "图纸转换失败，请重试或联系管理员"
        return _failed(code, message, {"reason": type(exc).__name__})
    manifest = manifest if isinstance(manifest, dict) else {}
    status = str(manifest.get("status") or "")
    detail = {"conversion_id": str(manifest.get("conversion_id") or ""),
              "status": status,
              "quality": model.jsonable(manifest.get("quality") or {}),
              "warning_count": model._as_int(manifest.get("warning_count")),
              "error_count": model._as_int(manifest.get("error_count")),
              "converter_name": str(manifest.get("converter_name") or ""),
              "converter_version": str(manifest.get("converter_version") or ""),
              "drawing_version": model._as_int(manifest.get("drawing_version"), 1)}
    if status not in ("ok", "success_with_warnings"):
        return _failed(str(manifest.get("error_code") or "DWG_CONVERSION_FAILED"),
                       "图纸转换未成功，请重试或联系管理员", detail)
    quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
    if quality and not quality.get("verified"):
        return _failed("DWG_CONVERTER_OUTPUT_INVALID",
                       "转换产物未通过质量门槛，请重试或联系管理员", detail)
    return {"status": "completed", "detail": detail,
            "anchor_updates": {"conversion_id": detail["conversion_id"],
                               "converter_version": detail["converter_version"],
                               "drawing_version": detail["drawing_version"]}}


def _detected_format(ctx: Dict[str, Any]) -> str:
    flow = ctx.get("flow") or {}
    for row in flow.get("steps") or []:
        if str((row or {}).get("step_id")) == "file_preflight":
            return str(((row or {}).get("detail") or {}).get("detected_format") or "")
    return ""


# --------------------------------------------------------------------------- #
# 3 CAD 矢量解析
# --------------------------------------------------------------------------- #
def cad_ir_parse(ctx: Dict[str, Any]) -> Dict[str, Any]:
    module = _resolve(ctx, "cad_ir")
    if module is None:
        return _unavailable("cad_ir")
    parse = getattr(module, "parse_conversion", None)
    summarize = getattr(module, "summarize", None)
    if not callable(parse):
        return _unavailable("cad_ir")
    try:
        ir = parse(ctx.get("project_id"),
                   drawing_version=model._as_int(ctx.get("drawing_version"), 1))
    except Exception as exc:
        code = str(getattr(exc, "stable_error_code", "") or "CAD_IR_SOURCE_MISSING")
        return _failed(code, str(exc) or "CAD 矢量解析失败，请重试",
                       {"reason": type(exc).__name__})
    ir = ir if isinstance(ir, dict) else {}
    summary = summarize(ir) if callable(summarize) else {}
    summary = summary if isinstance(summary, dict) else {}
    # 真实 `cad_ir.summarize()` 把计数放在 `stats` 里、单位放在 `units` 里（它自己的 Spec 口径），
    # 而本步骤的替身会直接摊平在顶层。两种形状都读，否则前端看到的是"实体 0 / 图层 0"，而
    # 实际图纸有 6569 个实体（实测 酒盒.dwg）。
    stats = summary.get("stats") if isinstance(summary.get("stats"), dict) else {}
    units = summary.get("units") if isinstance(summary.get("units"), dict) else {}

    def _count(key: str) -> int:
        value = summary.get(key)
        return model._as_int(value if value is not None else stats.get(key))

    counters = {key: model._as_int(value) for key, value in stats.items()
                if key.endswith("_total")}
    detail = {"ir_id": str(summary.get("ir_id") or ir.get("ir_id") or ""),
              "ir_hash": str(summary.get("ir_hash") or ir.get("ir_hash") or ""),
              "ir_version": str(summary.get("ir_version") or ir.get("ir_version") or ""),
              "layer_total": _count("layer_total"),
              "entity_total": _count("entity_total"),
              "unit_status": str(summary.get("unit_status")
                                 or units.get("unit_status") or ""),
              "counts": counters}
    return {"status": "completed", "detail": detail,
            "anchor_updates": {"ir_id": detail["ir_id"], "ir_hash": detail["ir_hash"],
                               "ir_version": detail["ir_version"],
                               "unit_status": detail["unit_status"]},
            "ir": ir}


# --------------------------------------------------------------------------- #
# 4 包装语义识别
# --------------------------------------------------------------------------- #
def packaging_semantics(ctx: Dict[str, Any]) -> Dict[str, Any]:
    module = _resolve(ctx, "packaging_semantics")
    if module is None:
        return _unavailable("packaging_semantics")
    analyze = getattr(module, "analyze_conversion", None)
    if not callable(analyze):
        return _unavailable("packaging_semantics")
    ir = ctx.get("ir") if isinstance(ctx.get("ir"), dict) else None
    if ir is None:
        ir = _previous_ir(ctx)
    try:
        doc = analyze(ctx.get("project_id"), ir=ir)
    except Exception as exc:
        code = str(getattr(exc, "stable_error_code", "") or "PACKAGING_SEMANTICS_FAILED")
        return _failed(code, str(exc) or "包装语义识别失败，请重试",
                       {"reason": type(exc).__name__})
    doc = doc if isinstance(doc, dict) else {}
    source = doc.get("source") if isinstance(doc.get("source"), dict) else {}
    stats = doc.get("stats") if isinstance(doc.get("stats"), dict) else {}
    detail = {"semantics_id": str(doc.get("semantics_id") or ""),
              "semantics_hash": str(doc.get("semantics_hash") or ""),
              "semantics_version": str(doc.get("semantics_version") or ""),
              "stats": model.jsonable(stats),
              "unresolved_total": len(doc.get("unresolved") or []),
              "unit_status": str(source.get("unit_status") or "")}
    return {"status": "completed", "detail": detail,
            "anchor_updates": {"semantics_id": detail["semantics_id"],
                               "semantics_hash": detail["semantics_hash"],
                               "semantics_version": detail["semantics_version"],
                               "unit_status": detail["unit_status"] or None},
            "semantics": doc}


def _previous_ir(ctx: Dict[str, Any]) -> Any:
    module = _resolve(ctx, "cad_ir")
    load = getattr(module, "load_ir", None) if module is not None else None
    if not callable(load):
        return None
    try:
        return load(ctx.get("project_id"))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# 5 零件提取（DWG 图纸 → 零件，Spec `packaging-dwg-parts-extraction.md` C6）
# --------------------------------------------------------------------------- #
def parts_extract(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """按 CAD IR 的连通分量提零件并落一版（纯提取 + 一次版本化落库）。

    没有可用 IR 是**前置条件缺失**（blocked），不是"这一步坏了"：后续步骤照旧要跑到
    终态（Spec `drawing-flow-error-taxonomy.md` C4）。
    """
    module = _resolve(ctx, "packaging_parts")
    extract = getattr(module, "extract", None) if module is not None else None
    save = getattr(module, "save_parts", None) if module is not None else None
    if not callable(extract):
        # 零件是**新增的前置事实**，不是门禁：提取不出来（模块缺失 / 没有 IR）都必须是
        # `blocked`（终态但不阻断后续步骤），否则"零件提不出来"会把字段写入、待确认、
        # 后续任务准备一起判死（Spec C6 / `drawing-flow-error-taxonomy.md` C4）。
        return _blocked("PACKAGING_PARTS_UNAVAILABLE",
                        "零件提取能力当前不可用（packaging_parts），零件清单暂不可生成",
                        {"dependency": "packaging_parts"},
                        action="联系系统管理员确认零件提取模块已随本版本部署，再重跑这一步")
    ir = ctx.get("ir") if isinstance(ctx.get("ir"), dict) else None
    if ir is None:
        ir = _previous_ir(ctx)
    if not isinstance(ir, dict):
        return _blocked("PACKAGING_PARTS_NO_IR",
                        "还没有可用的 CAD 图纸解析结果，无法提取零件"
                        "（缺前置条件，重试不会成功）",
                        {"dependency": "cad_ir"},
                        action="先跑一键解析图纸（前四步）再来提取零件")
    semantics = ctx.get("semantics") if isinstance(ctx.get("semantics"), dict) else None
    try:
        doc = extract(ir, semantics)
    except Exception as exc:
        return _failed("PACKAGING_PARTS_FAILED", str(exc) or "零件提取失败，请重试",
                       {"reason": type(exc).__name__})
    saved = doc
    if callable(save):
        try:
            saved = save(ctx.get("project_id"), doc)
        except Exception as exc:
            return _failed("PACKAGING_PARTS_SAVE_FAILED", "零件文档落库失败，请重试",
                           {"reason": type(exc).__name__})
    saved = saved if isinstance(saved, dict) else {}
    stats = saved.get("stats") if isinstance(saved.get("stats"), dict) else {}
    detail = {"parts_id": str(saved.get("parts_id") or ""),
              "parts_hash": str(saved.get("parts_hash") or ""),
              "parts_total": model._as_int(stats.get("part_total")),
              "filtered_total": model._as_int(stats.get("filtered_total")),
              "truncated": model._as_int(stats.get("truncated")),
              "by_role": model.jsonable(stats.get("by_role") or {}),
              "unavailable": [str((row or {}).get("code") or "")
                              for row in (saved.get("unavailable") or [])
                              if isinstance(row, dict)]}
    if detail["parts_total"]:
        _emit(str(ctx.get("project_id")), str(ctx.get("run_id")), "parts",
              "零件提取：%d 件（已剔除 %d 个非零件分量）"
              % (detail["parts_total"], detail["filtered_total"]))
    else:
        _emit(str(ctx.get("project_id")), str(ctx.get("run_id")), "parts",
              "零件提取：没有提取到零件（%s）"
              % ("、".join(detail["unavailable"]) or "原因未知"))
    return {"status": "completed", "detail": detail, "parts": saved}


# --------------------------------------------------------------------------- #
# 6 字段写入
# --------------------------------------------------------------------------- #
def _board_of(key: str, entry: Dict[str, Any], provenance: Dict[str, Any],
              sources: Dict[str, Any], unit_ok: bool) -> str:
    status = str(entry.get("status") or "")
    row = provenance.get(key) if isinstance(provenance.get(key), dict) else {}
    origin = str(row.get("origin") or entry.get("origin") or "")
    if status == "conflict" or str(row.get("status") or "") == "conflict":
        return "conflict"
    if str(sources.get(key) or "") == "manual" or origin == "user_confirmed":
        return "preserved"
    if status == "missing":
        return "missing"
    if status == "confirmed":
        if key in SIZE_FIELDS and not unit_ok:
            return "pending"
        return "written"
    if status in ("needs_confirmation", "inferred", "unresolved", ""):
        return "pending"
    return "skipped"


def _field_row(key: str, entry: Dict[str, Any], board: str, ir_id: str,
               ir_hash: str) -> Dict[str, Any]:
    return {"key": key, "board": board,
            "origin": str(entry.get("origin") or ""),
            "status": str(entry.get("status") or ""),
            "value": model.jsonable(entry.get("value")),
            "unit": str(entry.get("unit") or ""),
            "confidence": float(entry.get("confidence") or 0.0),
            "evidence_refs": model.jsonable(entry.get("evidence_refs") or []),
            "conflicts": model.jsonable(entry.get("conflicts") or []),
            "ir_id": ir_id, "ir_hash": ir_hash}


def _note_text(key: str, row: Dict[str, Any]) -> str:
    label = model.label_of(key)
    board = str(row.get("board") or "")
    origin = model.origin_label(row.get("origin"))
    if board == "written":
        return "%s：已按图纸标注写入 %s%s（来源：%s）" % (
            label, _fmt(row.get("value")), str(row.get("unit") or ""), origin)
    if board == "preserved":
        return "%s：保留人工确认值 %s，不使用图纸值" % (label, _fmt(row.get("value")))
    if board == "conflict":
        return "%s：两组证据互相冲突，需要人工裁定后才能写入" % label
    if board == "missing":
        return "%s：图纸里未找到，已生成待补项" % label
    return "%s：待确认（来源：%s）" % (label, origin)


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value)


def _manual_accept_fields(data: Dict[str, Any], sources: Dict[str, Any]) -> tuple:
    """人工录入/确认**且当前有值**的字段集 —— 字段确认通道的入参（Spec §2.3）。

    只收"来源 manual 且值非空"的字段：值为空的字段不存在"人工确认过的值"，让它照旧走图纸证据
    （批 12 §3.3 的既有口径）；非空的人工值一个都不许被图纸候选覆盖，这里把它们显式确认下来。
    """
    out = []
    for key, source in (sources or {}).items():
        if str(source or "") != "manual":
            continue
        value = (data or {}).get(key)
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, (list, tuple, dict, set)) and not value:
            continue
        out.append(str(key))
    return tuple(sorted(out))


def field_write(ctx: Dict[str, Any]) -> Dict[str, Any]:
    module = _resolve(ctx, "packaging_semantics")
    if module is None:
        return _unavailable("packaging_semantics")
    apply_fn = getattr(module, "apply_to_requirement", None)
    semantics = ctx.get("semantics")
    if not isinstance(semantics, dict) or not callable(apply_fn):
        return _unavailable("packaging_semantics")
    project_id, run_id = str(ctx.get("project_id")), str(ctx.get("run_id"))
    requirement = store.load_requirement(project_id) or {}
    data = requirement.get("data") if isinstance(requirement.get("data"), dict) else {}
    provenance = (data or {}).get("field_provenance") or {}
    sources = (data or {}).get("field_sources") or {}
    anchor = ctx.get("anchor") if isinstance(ctx.get("anchor"), dict) else {}
    unit_ok = str(anchor.get("unit_status") or "") == "confirmed"
    ir_id = str(anchor.get("ir_id") or "")
    ir_hash = str(anchor.get("ir_hash") or "")
    fields = (semantics.get("fields") if isinstance(semantics.get("fields"), dict) else {})
    rows: Dict[str, Dict[str, Any]] = {}
    for key in sorted(fields):
        entry = fields[key] if isinstance(fields[key], dict) else {}
        rows[key] = _field_row(key, entry, _board_of(key, entry, provenance, sources, unit_ok),
                               ir_id, ir_hash)
    # 人工确认通道（Spec `packaging-manual-field-confirmation.md` §2.3）：把需求里
    # "人工录入/确认且当前有值"的字段作为真实字段集交给确认通道 —— 人工值一个字都不许被
    # 图纸候选覆盖，看板与门禁必须同时认它（`accept` 不再是恒空集）。
    manual_fields = _manual_accept_fields(data, sources)
    try:
        apply_fn(project_id, semantics, accept=manual_fields,
                 author=str(ctx.get("actor") or "system"))
    except Exception as exc:
        # 分类只在**一处**判：异常自带稳定码就认它（码 → HTTP / 可重试查登记表），
        # 没有稳定码才按类型区分"真写失败"与"未识别异常"，不靠 str(exc) 关键字匹配
        # （Spec `drawing-flow-error-taxonomy.md` C1/C2、
        #  `drawing-flow-non-editable-requirement.md` §3.2/§3.3/§3.4）。
        code = str(getattr(exc, "stable_error_code", "") or "")
        reason = type(exc).__name__
        detail = {"written": []}
        if code in model.PRECONDITION_BLOCKERS:
            spec = model.PRECONDITION_BLOCKERS[code]
            message = str(exc) or str(spec["message"])
            detail["reason"] = reason
            blocked = _blocked(code, message, detail,
                               action=str(spec.get("action") or ""))
            # 字段看板照旧有得看（缺前置条件不等于"这次识别没结果"）。
            blocked["fields"] = rows
            return blocked
        if code:
            # 带稳定码的业务拒绝（如 REQUIREMENT_SAVE_REJECTED）：对外码就是它，
            # 可重试性查登记表 —— 业务拒绝重试同样不会成功，不许落成"未识别故障"。
            http_status, retryable = model.error_meta(code)
            detail["reason"] = reason
            detail["http_status"] = http_status
            return _failed(code, str(exc) or "需求字段写入被业务规则拒绝", detail,
                           retryable=retryable)
        code = ("REQUIREMENT_SAVE_FAILED" if isinstance(exc, (OSError, IOError))
                else "PACKAGING_FLOW_STEP_FAILED")
        message = str(exc) or "需求字段写入失败，请重试"
        detail["reason"] = reason
        return _failed(code, message, detail)
    requirement_no = str(requirement.get("requirement_no") or "")
    for key in sorted(rows):
        row = rows[key]
        payload = dict(row)
        payload["requirement_no"] = requirement_no
        _emit(project_id, run_id, "field:%s" % key, _note_text(key, row), payload)
    detail = {"fields": model.jsonable(rows),
              "written": sorted(k for k, v in rows.items() if v["board"] == "written"),
              "pending": sorted(k for k, v in rows.items() if v["board"] == "pending"),
              "conflict": sorted(k for k, v in rows.items() if v["board"] == "conflict"),
              "missing": sorted(k for k, v in rows.items() if v["board"] == "missing"),
              "preserved": sorted(k for k, v in rows.items() if v["board"] == "preserved")}
    return {"status": "completed", "detail": detail, "fields": rows}


# --------------------------------------------------------------------------- #
# 6 待确认生成
# --------------------------------------------------------------------------- #
def pending_confirm(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rows = ctx.get("fields") if isinstance(ctx.get("fields"), dict) else {}
    if not rows:
        flow = persistence.load_flow(str(ctx.get("project_id"))) or {}
        rows = flow.get("fields") if isinstance(flow.get("fields"), dict) else {}
    needs = sorted(k for k, v in rows.items() if str((v or {}).get("board")) == "pending")
    conflicts = sorted(k for k, v in rows.items() if str((v or {}).get("board")) == "conflict")
    missing = sorted(k for k, v in rows.items() if str((v or {}).get("board")) == "missing")
    pending = {"needs_confirmation": needs, "conflict": conflicts, "missing": missing,
               "total": len(needs) + len(conflicts) + len(missing)}
    if pending["total"]:
        names = "、".join(model.label_of(key) for key in (conflicts + needs + missing)[:5])
        _emit(str(ctx.get("project_id")), str(ctx.get("run_id")), "pending",
              "待确认：%s（共 %d 项）" % (names, pending["total"]))
    else:
        _emit(str(ctx.get("project_id")), str(ctx.get("run_id")), "pending",
              "待确认：暂无需要人工处理的字段")
    return {"status": "completed", "detail": model.jsonable(pending), "pending": pending}


# --------------------------------------------------------------------------- #
# 7 后续任务准备
# --------------------------------------------------------------------------- #
def downstream_prepare(ctx: Dict[str, Any]) -> Dict[str, Any]:
    # 走 `from .gates import ...`（而不是 `from . import gates`）：`__init__.py` 里
    # 有同名函数 `gates()`，属性查找会拿到函数而不是子模块。
    from .gates import build as _build_gates
    stages = _build_gates(str(ctx.get("project_id")), resolve=ctx.get("resolve"))["stages"]
    downstream = {stage: {"status": str(row.get("status") or ""),
                          "blocking": model.jsonable(row.get("blocking") or []),
                          "warnings": model.jsonable(row.get("warnings") or [])}
                  for stage, row in stages.items()}
    return {"status": "completed",
            "detail": {"downstream": model.jsonable(downstream), "gates": model.jsonable(stages)},
            "downstream": downstream, "gates": stages}


STEP_BODIES: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "file_preflight": file_preflight,
    "dwg_convert": dwg_convert,
    "cad_ir_parse": cad_ir_parse,
    "packaging_semantics": packaging_semantics,
    "parts_extract": parts_extract,
    "field_write": field_write,
    "pending_confirm": pending_confirm,
    "downstream_prepare": downstream_prepare,
}


def run(step_id: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    body = STEP_BODIES.get(str(step_id))
    if body is None:
        raise ValueError("未知的步骤：%s" % step_id)
    return body(ctx)

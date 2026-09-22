"""包装工艺路线与标准工时 —— 包装第 6 批。

Spec：docs/specs/packaging-process-route.md
红测：tests/test_packaging_process_route_red.py

分段职责：
  · `build_route_steps` / `validate_order` / `route_fingerprint` 是确定性纯函数：工序位次一律
    读本模块的 `PROCESS_CATALOG` 闭集（**不用** `kb_packaging_process_template.seq` 排序 ——
    那是里程碑分组，不是线性顺序），表面工序一律由需求字段判定；只读知识库，不落库、不调模型；
  · `build_route` 读确认盒型 + 第 5 批 BOM → 生成路线 → 整体替换落库；
  · `load_route` / `confirm_route` / `route_versions` 负责读回、确认冻结版本与 stale 判定。

本批不算成本/价格（第 7 批）、不算利润与报价单（第 8 批）、不排产能与设备日历。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional, Tuple

from . import industry_templates, packaging_match
from ..storage import da_db, da_repo, kb_repo, store


# --------------------------------------------------------------------------- #
# 命名契约（Spec §4.5：红测与实现共用，不得改名）
# --------------------------------------------------------------------------- #
ENGINE_VERSION = "packaging_route_v1"
PACKAGING_INDUSTRY = "packaging"

#: 工序闭集：工序名 → 规范位次（Spec §2.2，19 条）。数值越小越先做。
PROCESS_CATALOG = {
    "灰板开料": 10, "V 槽开槽": 20, "灰板成型": 30, "面纸印刷": 40, "表面处理": 45,
    "覆膜": 50, "烫金": 60, "丝印": 70, "UV 上光": 80, "压凹凸": 90, "面纸模切": 100,
    "铰链贴合": 110, "磁铁嵌入": 120, "机裱": 130, "手裱": 140, "内托组装": 150,
    "组装": 160, "检验": 170, "清洁包装": 180,
}

#: 闭集外模板工序名 → 闭集内工序名（Spec `packaging-route-template-closure.md` §3.1）。
#: 键逐字取自两份真实 DWG 实样的模板工序名（现场车间说法）；**映射只允许出现在这张表里**。
#: 1:1 用单元素 tuple；1:N 用多元素 tuple —— 第一道沿用模板行工时，其余记 None（§3.3）。
PROCESS_ALIASES: Dict[str, Tuple[str, ...]] = {
    # 酒盒.dwg（8 道）
    "材料开料": ("灰板开料",),
    "面纸印刷与覆膜": ("面纸印刷", "覆膜"),
    "模切/半穿": ("面纸模切",),
    "V槽": ("V 槽开槽",),
    "裱贴包面": ("机裱",),
    "内盒成型": ("灰板成型",),
    "EVA与托件制作": ("内托组装",),
    "总装与检验": ("组装", "检验"),
    # 圆盘盒.dwg（8 道）
    "纸张印刷覆膜": ("面纸印刷", "覆膜"),
    "灰板与面纸模切": ("面纸模切",),
    "纸管成型切管": ("灰板成型",),
    "V槽围边": ("V 槽开槽",),
    "围边裱贴": ("机裱",),
    "内托复合": ("内托组装",),
    "天地盖组装": ("组装",),
    "装配检验包装": ("组装", "检验"),
}


def normalize_step_name(name: Any) -> Tuple[str, ...]:
    """工序名归一化（纯函数，Spec §2.1）：

    · 闭集内 → `(name,)`；
    · 别名表内 → 映射值（1:N 时多元素）；
    · 其它 → `()`（**不猜、不兜底**：闭集外又没映射的名字由入库自检拒绝，
      `validate_order` 照旧判 `unknown_process:*`）。
    """
    text = _text(name)
    if text in PROCESS_CATALOG:
        return (text,)
    return tuple(PROCESS_ALIASES.get(text) or ())


#: 硬顺序链：两两相对顺序不得颠倒（缺项跳过）。
HARD_ORDER_CHAIN = ("面纸印刷", "覆膜", "烫金", "丝印", "UV 上光", "压凹凸", "面纸模切")

#: 需求字段 → 表面工序（Spec §2.3）。
SURFACE_REQUIREMENTS = {
    "print_colors": "面纸印刷", "spot_colors": "面纸印刷", "lamination": "覆膜",
    "hot_stamping": "烫金", "silk_screen": "丝印", "uv_coating": "UV 上光",
    "emboss_deboss": "压凹凸", "die_cutting": "面纸模切",
}

#: 表面工序的设备固定表（Spec §2.4.5）；模板没有该工序时按这张表补齐。
SURFACE_STATIONS = {
    "覆膜": "覆膜机", "烫金": "烫金机", "丝印": "丝印机", "UV 上光": "UV 上光机",
    "压凹凸": "压凹凸机", "面纸印刷": "胶印机", "面纸模切": "模切机",
}

#: 生成/确认是工艺侧写权限：**直接引用**第 4 批的角色常量（同一对象，不另抄一份）。
ROUTE_WRITE_ROLES = packaging_match.BOX_MATCH_DECIDE_ROLES

#: 聚合工序：模板里的 `表面处理`（「覆膜 → 烫金 → 局部UV（选配）」）。
AGGREGATE_STEP = "表面处理"

#: 需求真值判定的否定闭集（Spec §2.3，写死）。
_NEGATIVE_VALUES = frozenset({
    "", "否", "无", "不需要", "不要", "没有", "不需", "none", "n", "no", "false", "0", "—", "-",
})

#: 聚合工序展开出来的是这三种（需要任意一个才会替换聚合工序）。
_AGGREGATE_EXPANSION = ("覆膜", "烫金", "UV 上光")


class RouteError(Exception):
    """包装路线业务错误；`status_code` 与 `code` 供接口层原样映射。"""

    def __init__(self, message: str, status_code: int = 409, code: str = ""):
        super().__init__(message)
        self.message = str(message)
        self.status_code = int(status_code)
        self.code = str(code)


# --------------------------------------------------------------------------- #
# 取值口径（全部容错：缺字段、空串、脏值都不抛异常）
# --------------------------------------------------------------------------- #
def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed is not None else default


def _industry_of(data: dict) -> str:
    return industry_templates.normalize(
        (data or {}).get("industry") or (data or {}).get("industry_selection"))


def _requirement_data(project_id: str) -> dict:
    doc = store.load_requirement(project_id) or {}
    data = doc.get("data")
    return data if isinstance(data, dict) else {}


def _resolve_requirement_no(project_id: str, requirement_no: str) -> str:
    explicit = _text(requirement_no)
    if explicit:
        return explicit
    doc = store.load_requirement(project_id) or {}
    return _text(doc.get("requirement_no"))


def _actor_name(actor: Any) -> str:
    if isinstance(actor, dict):
        return _text(actor.get("username") or actor.get("name") or actor.get("actor"))
    return _text(actor)


def is_required(value: Any) -> bool:
    """需求表面工艺字段的真值判定（Spec §2.3，写死，不许自行发挥）。"""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value) > 0
    text = str(value).strip().lower()
    if text in _NEGATIVE_VALUES:
        return False
    number = _num(text)
    if number is not None:
        return number > 0
    return True


def _field_for(step_name: str, inputs: dict) -> str:
    """反查该工序由哪个需求字段触发（`面纸印刷` 有两个字段，取先命中的那个）。"""
    for field, step in SURFACE_REQUIREMENTS.items():
        if step == step_name and is_required((inputs or {}).get(field)):
            return field
    for field, step in SURFACE_REQUIREMENTS.items():
        if step == step_name:
            return field
    return ""


def required_surface_steps(inputs: dict) -> list:
    """需求 → 需要的表面工序（按规范位次排序、去重）。"""
    data = inputs or {}
    steps: list = []
    for field, step in SURFACE_REQUIREMENTS.items():
        if is_required(data.get(field)) and step not in steps:
            steps.append(step)
    steps.sort(key=lambda name: (PROCESS_CATALOG.get(name, 999), name))
    return steps


# --------------------------------------------------------------------------- #
# 路线生成（Spec §2.4，纯函数）
# --------------------------------------------------------------------------- #
def _template_step(name: str, row: dict) -> dict:
    return {
        "step_name": name,
        "rank": PROCESS_CATALOG.get(name),
        "workstation": _text(row.get("workstation")) or None,
        "work_content": _text(row.get("work_content")) or None,
        "standard_seconds": _num(row.get("standard_seconds")),
        "needs_standard_time": False,
        "automation": _text(row.get("automation")) or None,
        "control_point": _text(row.get("control_point")) or None,
        "parallel_ok": int(_num(row.get("parallel_ok")) or 0),
        "source": "template",
        "requirement_field": None,
    }


def _expanded_step(name: str, aggregate_row: dict, field: str) -> dict:
    """由聚合工序 `表面处理` 展开出来的工序：工时留空待补，质控点继承模板行。"""
    return {
        "step_name": name,
        "rank": PROCESS_CATALOG.get(name),
        "workstation": SURFACE_STATIONS.get(name),
        "work_content": None,
        "standard_seconds": None,
        "needs_standard_time": True,
        "automation": "自动",
        "control_point": _text(aggregate_row.get("control_point")) or None,
        "parallel_ok": 0,
        "source": "template:%s" % AGGREGATE_STEP,
        "requirement_field": field,
    }


def _synthetic_step(name: str, field: str) -> dict:
    """需求需要、模板里没有 → 合成补齐：工时留空待补，设备取固定表。"""
    return {
        "step_name": name,
        "rank": PROCESS_CATALOG.get(name),
        "workstation": SURFACE_STATIONS.get(name),
        "work_content": None,
        "standard_seconds": None,
        "needs_standard_time": True,
        "automation": "自动",
        "control_point": None,
        "parallel_ok": 0,
        "source": "requirement:%s" % field,
        "requirement_field": field,
    }


def _template_rows(box_type_code: str) -> dict:
    """按 `step_name` 去重：代表行取 `seq` 最小者，同 `seq` 取 `part_code` 升序靠前者。"""
    rows: dict = {}
    for row in kb_repo.packaging_process_templates(box_type_code=box_type_code):
        name = _text(row.get("step_name"))
        if not name:
            continue
        seq = _num(row.get("seq"))
        key = (seq if seq is not None else 0.0, _text(row.get("part_code")))
        current = rows.get(name)
        if current is None or key < current[0]:
            rows[name] = (key, dict(row))
    return {name: item[1] for name, item in rows.items()}


def build_route_steps(box_type_code: str, inputs: dict) -> dict:
    """按确认盒型与需求表面字段排出工序序列（只读知识库、不落库）。"""
    code = _text(box_type_code)
    data = dict(inputs or {})
    templates = _template_rows(code)
    if not templates:
        raise RouteError("盒型 %s 没有工艺模板，无法生成工艺路线" % code, 409,
                         "no_process_template")

    required = required_surface_steps(data)
    aggregate_steps: list = []
    # 模板工序名在构建时归一化到工序闭集内（Spec §2.1 / §3.2）：
    # 别名映射 1:N 时，第一道沿用模板行工时，其余道记 None 并进 needs_standard_time；
    # 闭集外又没映射的名字原样保留，让 validate_order 照旧判 unknown_process（不静默吞掉）。
    steps: list = []
    seen_names: set = set()
    for name, row in templates.items():
        targets = normalize_step_name(name)
        if not targets:
            targets = (name,)
        for index, target in enumerate(targets):
            if target in seen_names:
                continue
            seen_names.add(target)
            step = _template_step(target, row)
            if index:
                step.update({"standard_seconds": None, "needs_standard_time": True,
                             "source": "template:%s" % name})
            steps.append(step)

    aggregate_row = templates.get(AGGREGATE_STEP)
    expansions = [name for name in required if name in _AGGREGATE_EXPANSION]
    if aggregate_row is not None and expansions:
        # 需求需要覆膜/烫金/UV 中任意一个 → 用真正需要的那些替换聚合工序。
        steps = [row for row in steps if row["step_name"] != AGGREGATE_STEP]
        for name in expansions:
            if any(row["step_name"] == name for row in steps):
                continue
            steps.append(_expanded_step(name, aggregate_row, _field_for(name, data)))
    elif aggregate_row is not None:
        # 一个都不需要 → 聚合工序原样保留，并记进缺口让界面看得见。
        aggregate_steps.append(AGGREGATE_STEP)

    # 需求需要、模板没有的工序补齐；模板已有同名工序不重复出。
    for name in required:
        if any(row["step_name"] == name for row in steps):
            continue
        steps.append(_synthetic_step(name, _field_for(name, data)))

    steps.sort(key=lambda row: (row["rank"] if row["rank"] is not None else 999,
                                row["step_name"]))
    for index, row in enumerate(steps):
        row["step_no"] = 10 * (index + 1)
        row["depends_on"] = steps[index - 1]["step_no"] if index else None

    seconds = [row["standard_seconds"] for row in steps
               if row["standard_seconds"] is not None]
    total = round(sum(seconds), 1)
    quantity = _num(data.get("quote_quantity"))
    batch = round(total * quantity, 1) if (quantity is not None and quantity > 0) else None
    needs_time = [row["step_name"] for row in steps if row["needs_standard_time"]]
    return {
        "engine_version": ENGINE_VERSION,
        "box_type_code": code,
        "steps": steps,
        "required_surface": required,
        "total_seconds": total,
        "batch_seconds": batch,
        "has_incomplete_time": bool(needs_time),
        "needs_standard_time": needs_time,
        "gaps": {
            "needs_standard_time": list(needs_time),
            "order_violations": validate_order(steps),
            "aggregate_steps": aggregate_steps,
            "no_process_template": False,
        },
    }


def validate_order(steps) -> list:
    """顺序校验：返回违规码列表（空列表 = 合法，Spec §2.6）。"""
    rows = list(steps or [])
    codes: list = []
    names = [_text(row.get("step_name")) for row in rows]
    for name in names:
        code = "unknown_process:%s" % name
        if name not in PROCESS_CATALOG and code not in codes:
            codes.append(code)
    seen: set = set()
    for name in names:
        if name in seen:
            code = "duplicate_step:%s" % name
            if code not in codes:
                codes.append(code)
        seen.add(name)
    positions: dict = {}
    for row in rows:
        name = _text(row.get("step_name"))
        if name in HARD_ORDER_CHAIN and name not in positions:
            positions[name] = _num(row.get("step_no"))
    for earlier, later in zip(HARD_ORDER_CHAIN, HARD_ORDER_CHAIN[1:]):
        if earlier not in positions or later not in positions:
            continue
        if positions[earlier] >= positions[later]:
            codes.append("illegal_process_order:%s:%s" % (earlier, later))
    numbers = [_num(row.get("step_no")) for row in rows]
    for previous, current in zip(numbers, numbers[1:]):
        if previous is None or current is None or current <= previous:
            codes.append("step_no_not_ascending")
            break
    return codes


def _steps_fingerprint(steps) -> str:
    """工序序列（含 step_no）的稳定指纹。"""
    payload = json.dumps([[_num(row.get("step_no")), _text(row.get("step_name"))]
                          for row in (steps or [])], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _surface_snapshot(inputs: dict) -> str:
    """需求表面字段的取值快照（含判定结果），用于 stale 比对。"""
    data = inputs or {}
    return json.dumps({
        "fields": {field: _text(data.get(field)) for field in SURFACE_REQUIREMENTS},
        "required_surface": required_surface_steps(data),
    }, ensure_ascii=False, sort_keys=True)


def _quote_quantity(inputs: dict) -> Optional[float]:
    return _num((inputs or {}).get("quote_quantity"))


def route_fingerprint(box_type_code: str, steps, inputs: dict) -> tuple:
    """返回（工序指纹、表面字段快照、数量）—— 重算与确认共用这一份。"""
    return (_steps_fingerprint(steps), _surface_snapshot(inputs), _quote_quantity(inputs))


def bom_input_hash(rows: Any) -> str:
    """BOM 行的**内容**指纹（Spec `packaging-route-bom-version-pinning.md` §2.2）。

    范式与成本侧 `packaging_cost.bom_input_hash` 同源：`sha256_hex(canonical_json(json_safe(...)))`，
    输入前**按稳定键排序** —— 行序不是内容，同一批行换个顺序必须是同一个指纹。
    空输入给 `""`：**"没有 BOM" 不是一版内容**，不许拿它当"某一版"。
    """
    from .packaging_semantics import model as sem_model
    safe = [sem_model.json_safe(row) for row in list(rows or [])]
    if not safe:
        return ""
    safe.sort(key=sem_model.canonical_json)
    return sem_model.sha256_hex(sem_model.canonical_json(safe))


def _current_bom_rows(project_id: str, requirement_no: str):
    """读当前 BOM 行：`(行, 不可用标记)`——**读不到要披露，不许炸**（Spec §2.2）。

    返回 `(rows, {})`：读到了；`(None, {"code": "bom_unavailable", "reason": ...})`：
    抛异常或读到空 —— 这两种都是"比较不了"，与"变了"（`bom_rebuilt`）严格分开。
    """
    try:
        rows = [dict(item) for item in da_repo.load_packaging_bom(project_id, requirement_no)]
    except Exception as exc:                            # noqa: BLE001 - 读不到要披露，不许炸
        return None, {"code": "bom_unavailable", "reason": type(exc).__name__}
    if not rows:
        return None, {"code": "bom_unavailable", "reason": ""}
    return rows, {}


def _current_confirmed_box(project_id: str, requirement_no: str):
    """当前**确认**的盒型：`(盒型, 不可用标记)`（Spec `packaging-route-box-type-drift.md` §2.1）。

    只读 `da_repo.load_box_match()`（**绝不**触发匹配或重确认盒型）；读不到 → 空串 +
    `{"code": "box_match_unavailable", "reason": ...}`；还没确认过 → 空串 + `{}`
    （"还没确认" ≠ "盒型变了"）。
    """
    try:
        record = da_repo.load_box_match(project_id, requirement_no)
    except Exception as exc:                            # noqa: BLE001 - 读不到要披露，不许炸
        return "", {"code": "box_match_unavailable", "reason": type(exc).__name__}
    if not isinstance(record, dict):
        return "", {}
    if _text(record.get("decision")) != "confirmed":
        return "", {}
    return _text(record.get("confirmed_box_type")), {}


def _stored_source_versions(row: dict) -> dict:
    """落库的那一份 BOM 来源（Spec §2.2）：**存的**，不是读的时候现取的。"""
    stored = _loads((row or {}).get("source_versions_json"), {})
    return stored if isinstance(stored, dict) else {}


def _has_source_versions_column(row: dict) -> bool:
    """行里**确实带** `source_versions_json` 这一列吗（Spec §2.2 的 `provenance_missing` 判据）。

    库升级后这一列一定在（`_add_missing_columns()` 补的），所以真实历史行都会命中；
    这里区分的是"这一列在、但没有内容"与"这份读取路径根本不知道'来源'这回事"
    （`packaging-route-box-type-drift.md` 的红测夹具就是后者：整行没有这一列）。
    "没有这一列"不许被当成"有过来源又丢了"。
    """
    return isinstance(row, dict) and "source_versions_json" in row


def _bom_drift_reasons(stored: dict, project_id: str, requirement_no: str, *,
                       has_column: bool = True) -> tuple:
    """BOM 轴的 stale 原因（Spec §2.2）：`(原因, bom_unavailable)`。

    - `bom_rebuilt`：存的 `bom_hash` 非空，且 ≠ 当前 BOM 的 `bom_input_hash()`；
    - `provenance_missing`：路线存在、**这一列也在**，但没存 `source_versions`。

    判定与"是否确认过"无关（Spec §4 F6），所以调用方**不许**把它塞进
    `_stale_reasons()` 开头 `if not versions: return []` 的早退分支里。
    比较不了（BOM 读不到）时不给 `bom_rebuilt`：**"比较不了" ≠ "变了"**。
    """
    reasons: list = []
    if has_column and not stored:
        reasons.append("provenance_missing")
    stored_hash = _text((stored or {}).get("bom_hash"))
    rows, unavailable = _current_bom_rows(project_id, requirement_no)
    if stored_hash and rows is not None and bom_input_hash(rows) != stored_hash:
        reasons.append("bom_rebuilt")
    return reasons, unavailable


# --------------------------------------------------------------------------- #
# 落库、读回、确认与 stale（Spec §3、§4）
# --------------------------------------------------------------------------- #
def _step_out(row: dict) -> dict:
    out = dict(row)
    out.pop("project_id", None)
    out.pop("requirement_no", None)
    out["needs_standard_time"] = bool(row.get("needs_standard_time"))
    out["parallel_ok"] = int(row.get("parallel_ok") or 0)
    return out


def _empty_route(requirement_no: str, confirmed_versions: int) -> dict:
    return {
        "built": False,
        "box_type_code": "",
        "requirement_no": requirement_no,
        "engine_version": ENGINE_VERSION,
        "generated_at": "",
        "status": "draft",
        "confirmed_by": None,
        "confirmed_at": None,
        "stale": False,
        "stale_reasons": [],
        # 没排过路线：来源与"能不能比较"都给空（Spec `packaging-route-bom-version-pinning.md`
        # §2.2）——"还没排"不是"过期"，也不是"有上游版本"。
        "source_versions": {},
        "bom_unavailable": {},
        # 盒型轴同理（Spec `packaging-route-box-type-drift.md` §2.1）："还没排"不是"盒型变了"。
        "current_box_type_code": "",
        "box_match_unavailable": {},
        "total_seconds": None,
        "batch_seconds": None,
        "has_incomplete_time": False,
        "steps": [],
        "required_surface": [],
        "gaps": {"needs_standard_time": [], "order_violations": [],
                 "aggregate_steps": [], "no_process_template": False},
        "stats": {"step_count": 0, "template_steps": 0, "synthetic_steps": 0,
                  "manual_steps": 0, "auto_steps": 0,
                  "confirmed_versions": confirmed_versions},
    }


def _stale_reasons(row: dict, steps: list, versions: list, project_id: str) -> tuple:
    """确认后再次读取：与最近一次冻结版本比对（Spec §3.2）。

    返回 `(reasons, route_recompute_unavailable)`（Spec `packaging-route-recompute-unavailable.md`
    §2.1）："用当前输入重排算不出来"是**一条要披露的事实**，绝不许被折成"指纹没变"
    ——失败时把 `RouteError.code` / `message` 逐字带出来，并且不给 `route_changed`
    （"算不出来" ≠ "变过"）。重算成功时三条轴的判法与顺序逐字不变。
    """
    if not versions:
        return [], {}
    latest = versions[-1]
    data = _requirement_data(project_id)
    box_code = _text(row.get("box_type_code"))
    current = None
    recompute_unavailable: dict = {}
    if box_code:
        try:
            current = build_route_steps(box_code, data)
        except RouteError as exc:
            current = None
            recompute_unavailable = {"code": _text(getattr(exc, "code", "")),
                                     "reason": str(getattr(exc, "message", "") or exc)}
    stored_fingerprint = _steps_fingerprint(steps)
    current_fingerprint = (_steps_fingerprint(current["steps"]) if current
                           else stored_fingerprint)
    frozen = _text(latest.get("steps_fingerprint"))
    reasons: list = []
    if not recompute_unavailable:
        if frozen and (frozen != stored_fingerprint or frozen != current_fingerprint):
            reasons.append("route_changed")
    if _text(latest.get("surface_json")) != _surface_snapshot(data):
        reasons.append("requirement_changed")
    if _num(latest.get("quote_quantity")) != _quote_quantity(data):
        reasons.append("quantity_changed")
    return reasons, recompute_unavailable


def load_route(project_id: str, requirement_no: str = "") -> dict:
    """读回路线 + 缺口 + 统计 + stale；没有路线时 `built = false`、`steps = []`，不报错。"""
    req_no = _resolve_requirement_no(project_id, requirement_no)
    row = da_repo.load_packaging_route(project_id, req_no)
    versions = da_repo.packaging_route_versions(project_id, req_no)
    if not row:
        return _empty_route(req_no, len(versions))

    steps = [_step_out(item) for item in da_repo.load_packaging_route_steps(project_id, req_no)]
    snapshot = _loads(row.get("surface_json"), {})
    required_surface = snapshot.get("required_surface") if isinstance(snapshot, dict) else []
    reasons, recompute_unavailable = _stale_reasons(row, steps, versions, project_id)
    # BOM 轴的两条原因（Spec `packaging-route-bom-version-pinning.md` §2.2）：与既有三条
    # 同构（固定顺序、去重），但**不**受"有没有冻结版本"影响 —— 还没确认过的路线同样要报。
    stored_versions = _stored_source_versions(row)
    bom_reasons, bom_unavailable = _bom_drift_reasons(
        stored_versions, project_id, req_no, has_column=_has_source_versions_column(row))
    # 盒型轴（Spec `packaging-route-box-type-drift.md` §2.1）：路线照的盒型 ≠ 当前确认的盒型。
    # 任一侧为空不算命中（"没排过 / 没确认过"不是"变了"）；读不到匹配记录时"比较不了"，不判。
    current_box_type, box_unavailable = _current_confirmed_box(project_id, req_no)
    route_box_type = _text(row.get("box_type_code"))
    if current_box_type and route_box_type and current_box_type != route_box_type:
        bom_reasons = list(bom_reasons) + ["box_type_reconfirmed"]
    for code in bom_reasons:
        if code not in reasons:
            reasons.append(code)
    aggregate = [AGGREGATE_STEP] if any(item["step_name"] == AGGREGATE_STEP
                                        for item in steps) else []
    needs_time = [item["step_name"] for item in steps if item["needs_standard_time"]]
    return {
        "built": True,
        "box_type_code": _text(row.get("box_type_code")),
        "requirement_no": req_no,
        "engine_version": _text(row.get("engine_version")) or ENGINE_VERSION,
        # 上游版本的埋点（DWG 第 5 批 Spec §6.1 / 本批 Spec §2.2）：这里是**落库的那一份**，
        # 不是读的时候从当前 BOM 现取的 —— 字段名说的是"照着哪一版排的"，就必须是那一刻的。
        # 历史路线（本批之前落的）没有这一列 → 给 `{}` + `provenance_missing`，绝不编。
        "source_versions": stored_versions,
        # 当前 BOM **比较不了**（读不到 / 空）时的披露（Spec §2.2）：正常给 `{}`。
        "bom_unavailable": bom_unavailable,
        # 「按当前输入重排算不出来」的披露（Spec `packaging-route-recompute-unavailable.md` §2.1）：
        # 正常给 `{}`；失败时带 `RouteError` 的原因码与 message。已存的路线照旧返回。
        "route_recompute_unavailable": recompute_unavailable,
        # 当前确认的盒型（Spec `packaging-route-box-type-drift.md` §2.1）：`decision != "confirmed"`
        # 或读不到给 `""`；不确认过、读不到都不算漂移。
        "current_box_type_code": current_box_type,
        "box_match_unavailable": box_unavailable,
        "generated_at": _text(row.get("generated_at")),
        "status": _text(row.get("status")) or "draft",
        "confirmed_by": row.get("confirmed_by") or None,
        "confirmed_at": row.get("confirmed_at") or None,
        "stale": bool(reasons),
        "stale_reasons": reasons,
        "total_seconds": row.get("total_seconds"),
        "batch_seconds": row.get("batch_seconds"),
        "has_incomplete_time": bool(row.get("has_incomplete_time")),
        "steps": steps,
        "required_surface": list(required_surface or []),
        "gaps": {
            "needs_standard_time": needs_time,
            "order_violations": validate_order(steps),
            "aggregate_steps": aggregate,
            # 读时实话实说（Spec `packaging-route-recompute-unavailable.md` §2.1）：只有当"按当前输入
            # 重排算不出来、且原因就是没有工艺模板"时才为真 —— 不许再写死 False。
            "no_process_template": bool(
                _text(recompute_unavailable.get("code")) == "no_process_template"),
        },
        "stats": {
            "step_count": len(steps),
            "template_steps": sum(1 for item in steps
                                  if _text(item.get("source")).startswith("template")),
            "synthetic_steps": sum(1 for item in steps
                                   if _text(item.get("source")).startswith("requirement:")),
            "manual_steps": sum(1 for item in steps
                                if _text(item.get("automation")) == "手工"),
            "auto_steps": sum(1 for item in steps
                              if _text(item.get("automation")) == "自动"),
            "confirmed_versions": len(versions),
        },
    }


def build_route(project_id: str, requirement_no: str = "") -> dict:
    """读确认盒型 + 第 5 批 BOM → 生成路线 → 整体替换落库（确认态回到 draft）。"""
    data = _requirement_data(project_id)
    if _industry_of(data) != PACKAGING_INDUSTRY:
        raise RouteError("工艺路线只对包装行业的需求单生效", 400, "not_packaging_industry")
    req_no = _resolve_requirement_no(project_id, requirement_no)

    record = da_repo.load_box_match(project_id, req_no) or {}
    box_code = _text(record.get("confirmed_box_type"))
    if _text(record.get("decision")) != "confirmed" or not box_code:
        raise RouteError("尚未确认盒型，无法生成工艺路线（Spec §2.1）", 409,
                         "box_type_not_confirmed")

    # 没有工艺模板就没有"路线"可言：这条缺口先报，与 BOM 是否建过无关（Spec §2.7）。
    if not kb_repo.packaging_process_templates(box_type_code=box_code):
        raise RouteError("盒型 %s 没有工艺模板，无法生成工艺路线" % box_code, 409,
                         "no_process_template")

    bom_rows = da_repo.load_packaging_bom(project_id, req_no)
    if not bom_rows or not any(_text(row.get("bom_category")) == "process"
                               for row in bom_rows):
        raise RouteError("包装 BOM 尚未建立，请先展开部件（Spec §2.1）", 409,
                         "bom_not_built")

    result = build_route_steps(box_code, data)
    fingerprint, surface_json, quantity = route_fingerprint(box_code, result["steps"], data)
    # **排产那一刻**那一版 BOM 的身份（Spec `packaging-route-bom-version-pinning.md` §2.2）：
    # 指纹取刚读到的行、版本与盒型取同一份 BOM 文档，随主表一起落库 —— 事后才回答得出
    # "这条路线是照哪一版 BOM 排的"（以前读接口只能拿当前 BOM 现取，名字说谎）。
    try:
        from tech_app.backend.services import packaging_bom as _bom_mod
        bom_doc = _bom_mod.load_bom(project_id, req_no)
    except Exception:                                   # noqa: BLE001 - 记不下就留空，不许炸
        bom_doc = {}
    bom_doc = bom_doc if isinstance(bom_doc, dict) else {}
    first_row = bom_rows[0] if bom_rows else {}
    bom_box = _text(bom_doc.get("box_type_code"))
    if not bom_box:                                     # 文档没给就从成品行取（BOM 的既有口径）
        for item in bom_rows:
            if _text((item or {}).get("bom_category")) == "finished":
                bom_box = _text((item or {}).get("item_key"))
                break
    source_versions = {
        "bom_version": (_text(bom_doc.get("generated_at"))
                        or _text((first_row or {}).get("generated_at"))),
        "bom_hash": bom_input_hash(bom_rows),
        "bom_item_total": len(bom_rows),
        "engine_version": (_text(bom_doc.get("engine_version"))
                           or _text((first_row or {}).get("engine_version"))),
        # 与被排路线的盒型不一致时**以 BOM 为准**记录事实（Spec §2.2），不修正任何一方。
        "box_type_code": bom_box,
    }
    now = da_db.now()
    route = {
        "industry": PACKAGING_INDUSTRY,
        "engine_version": ENGINE_VERSION,
        "generated_at": now,
        "box_type_code": box_code,
        "total_seconds": result["total_seconds"],
        "batch_seconds": result["batch_seconds"],
        "has_incomplete_time": result["has_incomplete_time"],
        "status": "draft",
        "confirmed_by": None,
        "confirmed_at": None,
        "stale": False,
        "stale_reasons": [],
        "steps_fingerprint": fingerprint,
        "surface_json": surface_json,
        "quote_quantity": quantity,
        "source_versions": source_versions,
    }
    da_repo.save_packaging_route(project_id, req_no, route, result["steps"])
    store.audit(project_id, "workflow:packaging_route_rebuilt",
                {"requirement_no": req_no, "box_type_code": box_code,
                 "step_count": len(result["steps"])})
    return load_route(project_id, req_no)


def confirm_route(project_id: str, requirement_no: str, *, actor: Any = None) -> dict:
    """工艺经理确认：draft 且顺序合法才能确认，并追加一条版本快照（重复确认幂等）。"""
    req_no = _resolve_requirement_no(project_id, requirement_no)
    row = da_repo.load_packaging_route(project_id, req_no)
    if not row:
        raise RouteError("还没有工艺路线，请先重算（Spec §2.7）", 404, "route_not_found")

    steps = [_step_out(item) for item in da_repo.load_packaging_route_steps(project_id, req_no)]
    violations = validate_order(steps)
    if violations:
        raise RouteError("路线顺序不合法，不能确认：%s" % "；".join(violations), 409,
                         "route_not_confirmable")

    # 盒型轴（Spec `packaging-route-box-type-drift.md` §2.1）：照 A 排的路线不许在"当前确认的是 B"
    # 时被确认成冻结版本。任一侧为空不算命中；读不到匹配记录不新增拒绝（"比较不了" ≠ "变了"）。
    current_box_type, _box_unavailable = _current_confirmed_box(project_id, req_no)
    route_box_type = _text(row.get("box_type_code"))
    if current_box_type and route_box_type and current_box_type != route_box_type:
        raise RouteError("这条路线是照盒型 %s 排的，现在确认的是 %s，请重排后再确认（Spec §2.1）"
                         % (route_box_type, current_box_type), 409, "box_type_reconfirmed")

    versions = da_repo.packaging_route_versions(project_id, req_no)
    data = _requirement_data(project_id)
    fingerprint, surface_json, quantity = route_fingerprint(
        _text(row.get("box_type_code")), steps, data)
    # 幂等重复确认不冻结任何新东西：指纹一致就照旧早退，不再要求输入版本可比
    # （历史行没有来源也不该让"重复确认"变成 409 —— 那条纪律属于"要追加新版本"的路径）。
    stored_versions = _stored_source_versions(row)
    stored_hash = _text(stored_versions.get("bom_hash"))
    live_rows, bom_unavailable = _current_bom_rows(project_id, req_no)
    current_hash = bom_input_hash(live_rows) if live_rows is not None else ""
    if _text(row.get("status")) == "confirmed" and versions:
        latest = versions[-1]
        if (_text(latest.get("steps_fingerprint")) == fingerprint
                and _text(latest.get("surface_json")) == surface_json
                and _num(latest.get("quote_quantity")) == quantity
                and (not stored_hash or not current_hash or current_hash == stored_hash)):
            return load_route(project_id, req_no)

    # 要追加新版本才核对"排产照的那一版输入"（Spec `packaging-route-bom-version-pinning.md` §2.2）：
    # 没有来源 / 来源与当前 BOM 对不上，都必须先重算再确认；被拒时一个版本快照都不留。
    if not stored_versions or not stored_hash:
        raise RouteError("这份工艺路线没有记录排产照的那一版 BOM，请先重算再确认（Spec §5）",
                         409, "route_bom_provenance_missing")
    if bom_unavailable or not current_hash:
        raise RouteError("当前读不到包装 BOM，无法核对这份路线照的是哪一版，请稍后再确认",
                         409, "bom_unavailable")
    if current_hash != stored_hash:
        raise RouteError("排产照的那一版 BOM 已经变了，请先重算路线再确认（Spec §2.2）",
                         409, "bom_rebuilt")

    by = _actor_name(actor)
    now = da_db.now()
    da_repo.append_packaging_route_version({
        "project_id": project_id,
        "requirement_no": req_no,
        "version": len(versions) + 1,
        "confirmed_by": by,
        "confirmed_at": now,
        "box_type_code": _text(row.get("box_type_code")),
        "steps_fingerprint": fingerprint,
        "surface_json": surface_json,
        "quote_quantity": quantity,
        "total_seconds": row.get("total_seconds"),
        "has_incomplete_time": 1 if row.get("has_incomplete_time") else 0,
        "steps_json": json.dumps(steps, ensure_ascii=False),
        # 冻结快照必须记下"照哪一版 BOM 排的"（Spec §2.2、§6.1）：否则事后无法回答
        # 旧版本照的是哪一版输入。逐字等于主表那一份。
        "source_versions": stored_versions,
    })
    da_db.execute(
        "UPDATE wip_packaging_process_route SET status = 'confirmed', confirmed_by = ?, "
        "confirmed_at = ?, stale = 0, stale_reasons = ?, updated_at = ? "
        "WHERE project_id = ? AND requirement_no = ?",
        [by, now, "[]", now, project_id, req_no])
    store.audit(project_id, "workflow:packaging_route_confirmed",
                {"requirement_no": req_no, "version": len(versions) + 1, "by": by})
    return load_route(project_id, req_no)


def route_versions(project_id: str, requirement_no: str = "") -> list:
    """版本快照（按版本升序；只增不改）。

    每条**新增** `source_versions`（Spec `packaging-route-bom-version-pinning.md` §2.2）；
    历史快照（本批之前冻的）没有这一列 → 给 `{}`，绝不拿当前 BOM 兜上去。
    """
    rows = da_repo.packaging_route_versions(
        project_id, _resolve_requirement_no(project_id, requirement_no))
    out: list = []
    for row in rows or []:
        item = dict(row)
        item["source_versions"] = _stored_source_versions(item)
        out.append(item)
    return out

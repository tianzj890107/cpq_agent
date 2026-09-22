"""参数化部件展开与包装 BOM —— 包装第 5 批。

Spec：docs/specs/packaging-parametric-bom.md §2.4 / §2.5 / §3 / §4
红测：tests/test_packaging_parametric_bom_red.py（B–G 组）

分段职责：
  · `bind_variables` / `expand_parts` 是确定性纯函数：变量绑定口径一律照 Spec §2.2
    那张表（不许 `H` 顶 `H盖` 这类"看起来合理"的推断），表达式一律走
    `packaging_formula.evaluate` 的受控求值；只读知识库、不落库、不调模型、不联网；
  · `build_bom` 读第 4 批确认的盒型 → 展开 → 组装七类 BOM → 落 `wip_packaging_bom_item`；
  · `load_bom` / `lock_bom_item` 负责读回与人工锁定（锁定的行不参与重算）。

本批不做工艺路线生成与顺序校验（第 6 批）、成本公式求值（第 7 批）、利润与报价
（第 8 批）。
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Optional

from . import industry_templates, packaging_formula, packaging_match
from ..storage import da_db, da_repo, kb_repo, store


# --------------------------------------------------------------------------- #
# 命名契约（Spec §4.5：红测与实现共用，不得改名）
# --------------------------------------------------------------------------- #
ENGINE_VERSION = "packaging_bom_v1"
PACKAGING_INDUSTRY = "packaging"

#: BOM 七类闭集（Spec §2.5）。
BOM_CATEGORIES = ("finished", "box_part", "material", "process",
                  "packaging", "tooling", "optional_part")

#: 展开/锁定是工艺侧写权限：**直接引用**第 4 批的角色常量（同一对象，不另抄一份）。
BOM_WRITE_ROLES = packaging_match.BOX_MATCH_DECIDE_ROLES

#: 配对复核的落盘 doc key（Spec `packaging-parse-to-downstream-seams.md` §2.2）：
#: 与 `packaging_semantics` / `packaging_parts` 同一范式走 meta 文档通道，
#: 不改数据库 schema、不加表。
PAIRING_DOC_KEY = "packaging_bom_pairing"

#: 零件回填失败的落盘 doc key（Spec `packaging-silent-degradation-disclosure.md` §2.1）：
#: 同一范式的**加法**披露 —— 回填失败照旧不改 BOM 结论，但读接口必须说得出来
#: "零件文档在、回填这一步挂了"。
BIND_ERROR_DOC_KEY = "packaging_bom_bind_error"

#: 可自动绑定的变量（Spec §2.2）。
AUTO_VARIABLES = frozenset({"L", "W", "H", "t", "c"})

#: 只能由工艺经理覆盖、绝不从 L/W/H/t/c 推断的变量（Spec §2.2）。
OVERRIDE_ONLY_VARIABLES = frozenset({
    "H盖", "H内", "L外", "W外", "L内", "W内",
    "盖展开尺寸", "盒身展开尺寸", "整体展开", "外盒展开", "内盒展开", "包边",
})

#: 部件类别（展开结果落在 BOM 里的两类）。
PART_CATEGORIES = ("box_part", "optional_part")

#: 内尺寸三键：缺一个就无法展开（Spec §2.6）。
INNER_DIM_KEYS = ("inner_length", "inner_width", "inner_height")

#: 「含刀模制程」的关键词闭集（Spec §2.5，口径出自 0903「问题点」）。
TOOLING_KEYWORDS = ("烫金", "丝印", "击凹凸", "模切", "装配线")

_NUMBER_PATTERN = r"[-+]?\d+(?:\.\d+)?"
_NAME_PATTERN = r"[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*"
#: 内联默认值：`铰链宽40` / `出血3mm`（归一化后是 `出血3`）。
_LITERAL_PATTERN = r"([\u4e00-\u9fff]{1,12})(\d+(?:\.\d+)?)"


class BomError(Exception):
    """包装 BOM 的业务错误；`status_code` 与 `code` 供接口层原样映射。"""

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
    """从任意取值里取第一个数字；取不到给 None（不抛异常）。"""
    if value is None or isinstance(value, bool):
        return None
    match = re.search(_NUMBER_PATTERN, _text(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:                                   # pragma: no cover - 兜底
        return None


def _fmt_number(value: float) -> str:
    return "%g" % float(value)


def _json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, default=str)


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


def _resolve_requirement_no(project_id: str, requirement_no: str) -> str:
    """没传需求单号时按项目当前需求单取（与第 3、4 批同一口径）。"""
    explicit = _text(requirement_no)
    if explicit:
        return explicit
    doc = store.load_requirement(project_id) or {}
    return _text(doc.get("requirement_no"))


def _actor_name(actor: Any) -> str:
    if isinstance(actor, dict):
        return _text(actor.get("username") or actor.get("name") or actor.get("actor"))
    return _text(actor)


def _load_box_type(box_type_code: str) -> dict:
    code = _text(box_type_code)
    for row in kb_repo.packaging_box_types():
        if _text(row.get("box_type_code")) == code:
            return dict(row)
    raise BomError("盒型不存在：%s" % code, 404, "box_type_not_found")


# --------------------------------------------------------------------------- #
# 变量绑定与表达式展开（Spec §2.2 / §2.3 / §2.4）
# --------------------------------------------------------------------------- #
def bind_variables(box_type: dict, inputs: dict, overrides: dict) -> dict:
    """5 个自动变量 + overrides；只放已绑定的（未绑定由求值器报 `missing_variables`）。

    内联默认值不在这里收 —— 它们由 `expand_parts` 从表达式里收集（Spec §2.2）。
    """
    box = box_type or {}
    data = inputs or {}
    bound: dict[str, dict] = {}
    for name, key in (("L", "inner_length"), ("W", "inner_width"), ("H", "inner_height")):
        value = _num(data.get(key))
        if value is not None:
            bound[name] = {"value": value, "source": "requirement"}
    thickness = _num(box.get("grey_board_thickness"))
    if thickness is not None:
        bound["t"] = {"value": thickness, "source": "box_type"}
    clearance = _num(data.get("fit_clearance"))
    if clearance is not None:
        bound["c"] = {"value": clearance, "source": "requirement"}
    else:
        clearance = _num(box.get("fit_clearance"))
        if clearance is not None:
            bound["c"] = {"value": clearance, "source": "box_type"}
    for name, value in (overrides or {}).items():
        number = _num(value)
        if number is None:
            continue
        bound[str(name)] = {"value": number, "source": "override"}
    return bound


def _normalized(raw: str) -> str:
    try:
        return packaging_formula.normalize_expression(raw)
    except packaging_formula.FormulaError:
        return ""


def _literal_defaults(parts: list) -> list:
    """从（归一化后的）尺寸表达式里收集内联默认值：`出血3` → 出血 = 3。"""
    found: list = []
    seen: set = set()
    for row in parts:
        for key in ("size_length_expr", "size_width_expr", "size_height_expr"):
            norm = _normalized(_text(row.get(key)))
            if not norm:
                continue
            for alias, digits in re.findall(_LITERAL_PATTERN, norm):
                matched = "%s%s" % (alias, digits)
                if matched in seen:
                    continue
                seen.add(matched)
                try:
                    value = float(digits)
                except ValueError:                        # pragma: no cover - 兜底
                    continue
                found.append((matched, alias, value))
    return found


def _replace_literals(norm: str, variables: dict, literals: list) -> str:
    out = norm
    for matched, alias, default in literals:
        if matched not in out:
            continue
        effective = variables.get(alias)
        value = effective["value"] if effective else default
        out = out.replace(matched, _fmt_number(value))
    return out


def _variable_names(norm: str) -> list:
    names: list = []
    for name in re.findall(_NAME_PATTERN, norm):
        if name in packaging_formula.ALLOWED_FUNCTIONS:
            continue
        if name not in names:
            names.append(name)
    return names


def _dim_detail(raw: str, variables: dict, literals: list) -> tuple:
    """算一维：返回 (值, 缺失变量, 溯源)。表达式为空 = 没有这一维（Spec §2.4）。"""
    text = _text(raw)
    if not text:
        return None, [], None
    norm = _normalized(text)
    replaced = _replace_literals(norm, variables, literals)
    trace = {
        "expr": text,
        "normalized": norm,
        "variables": {name: (variables.get(name) or {}).get("source", "")
                      for name in _variable_names(replaced)},
    }
    try:
        value = float(packaging_formula.evaluate(
            replaced, {key: item["value"] for key, item in variables.items()}))
    except packaging_formula.FormulaError as exc:
        return None, list(getattr(exc, "missing_variables", None) or []), trace
    return value, [], trace


def _size_mode(exprs: dict, size_expr: str) -> str:
    """`expression` / `standard_part`（Spec §2.4 的状态闭集）。"""
    text = " ".join(value for value in exprs.values() if value) or _text(size_expr)
    norm = _normalized(text)
    if not norm:
        return "standard_part"
    if re.search(r"\d", norm) or re.search(r"[+\-*/()<>=]", norm):
        return "expression"
    if " " in norm:
        # 多段尺寸（`L  W`）只是没有数字/运算符，不是"外购标准件"。
        return "expression"
    if re.fullmatch(r"[\u4e00-\u9fff]+", norm):
        # 归一化后是纯中文词（`标准件`）：外购标准件，三维留空、不算缺失。
        return "standard_part"
    return "expression"


def expand_parts(box_type_code: str, inputs: dict, *, overrides: Optional[dict] = None) -> dict:
    """按确认盒型参数化展开每个部件（只读知识库、不落库）。"""
    data = dict(inputs or {})
    box = _load_box_type(box_type_code)
    missing_inputs = [key for key in INNER_DIM_KEYS if _num(data.get(key)) is None]
    if missing_inputs:
        raise BomError("缺内尺寸：%s" % "、".join(missing_inputs), 409,
                       "missing_requirement_input")
    parts = kb_repo.packaging_part_templates(box_type_code)
    if not parts:
        raise BomError("盒型 %s 没有部件模板，无法展开" % box_type_code, 409,
                       "no_part_template")

    variables = bind_variables(box, data, overrides or {})
    literals = _literal_defaults(parts)
    for _matched, alias, value in literals:
        if alias not in variables:
            variables[alias] = {"value": value, "source": "literal_default"}

    out_parts: list = []
    for row in parts:
        exprs = {
            "length": _text(row.get("size_length_expr")),
            "width": _text(row.get("size_width_expr")),
            "height": _text(row.get("size_height_expr")),
        }
        mode = _size_mode(exprs, row.get("size_expr"))
        entry = {
            "part_code": _text(row.get("part_code")),
            "name": _text(row.get("name")),
            "component": _text(row.get("component")),
            "material": _text(row.get("material")),
            "quantity": _num(row.get("quantity")),
            "is_optional": int(_num(row.get("is_optional")) or 0),
            "size_mode": mode,
            "size_expr": _text(row.get("size_expr")),
            "size_length_expr": exprs["length"],
            "size_width_expr": exprs["width"],
            "size_height_expr": exprs["height"],
            "length": None,
            "width": None,
            "height": None,
            "missing_variables": [],
            "size_source": {"length": None, "width": None, "height": None},
        }
        if mode == "standard_part":
            entry["status"] = "computed"
            out_parts.append(entry)
            continue

        missing: list = []
        failed = False
        for dim in ("length", "width", "height"):
            value, gaps, trace = _dim_detail(exprs[dim], variables, literals)
            entry["size_source"][dim] = trace
            if exprs[dim] and value is None:
                failed = True
            entry[dim] = value
            for name in gaps:
                if name not in missing:
                    missing.append(name)
        if missing:
            failed = True
        entry["missing_variables"] = missing
        entry["status"] = "needs_input" if failed else "computed"
        out_parts.append(entry)

    return {
        "engine_version": ENGINE_VERSION,
        "box_type_code": _text(box_type_code),
        "variables": variables,
        "parts": out_parts,
        "expanded_count": sum(1 for item in out_parts if item["status"] == "computed"),
        "needs_input_count": sum(1 for item in out_parts if item["status"] == "needs_input"),
    }


# --------------------------------------------------------------------------- #
# BOM 七类组装（Spec §2.5）
# --------------------------------------------------------------------------- #
def _material_index() -> list:
    return kb_repo.list_materials(industry=PACKAGING_INDUSTRY)


def _resolve_material_code(material: str, rows: list) -> Optional[str]:
    """`material` 首个空白分词在 `kb_material.name` 里唯一包含 → 该材料码，否则 None。"""
    text = _text(material)
    if not text:
        return None
    token = text.split()[0]
    hits = [row for row in rows if token and token in _text(row.get("name"))]
    if len(hits) != 1:
        return None
    return _text(hits[0].get("material_code")) or None


def _part_item(entry: dict) -> dict:
    category = "optional_part" if entry.get("is_optional") else "box_part"
    return {
        "bom_category": category,
        "item_key": entry["part_code"],
        "item_name": entry.get("name") or entry["part_code"],
        "part_code": entry["part_code"],
        "component": entry.get("component") or None,
        "material": entry.get("material") or None,
        "quantity": entry.get("quantity"),
        "unit": "件",
        "size_length_expr": entry.get("size_length_expr") or None,
        "size_width_expr": entry.get("size_width_expr") or None,
        "size_height_expr": entry.get("size_height_expr") or None,
        "length_mm": entry.get("length"),
        "width_mm": entry.get("width"),
        "height_mm": entry.get("height"),
        "size_source_json": _json_text(entry.get("size_source")),
        "status": entry.get("status") or "computed",
        "missing_variables": list(entry.get("missing_variables") or []),
        "is_optional": int(entry.get("is_optional") or 0),
        "source": "kb_packaging_part_template",
    }


def _process_rows(box_type_code: str) -> list:
    return [dict(row) for row in
            kb_repo.packaging_process_templates(box_type_code=box_type_code)]


def _matched_keyword(row: dict) -> str:
    blob = "%s %s" % (_text(row.get("step_name")), _text(row.get("work_content")))
    for keyword in TOOLING_KEYWORDS:
        if keyword in blob:
            return keyword
    return ""


def _assemble(expanded: dict, box: dict, data: dict, requirement_no: str) -> list:
    items: list = []
    box_code = expanded["box_type_code"]

    # 1) 成品：恰好 1 行。
    items.append({
        "bom_category": "finished",
        "item_key": box_code,
        "item_name": _text(box.get("name")) or box_code,
        "quantity": _num(data.get("quote_quantity")),
        "unit": "个",
        "status": "computed",
        "is_optional": 0,
        "source": "kb_packaging_box_type",
    })

    # 2) 盒型部件 / 可选部件。
    for entry in expanded["parts"]:
        items.append(_part_item(entry))

    # 3) 材料：部件 material 去重，唯一命中才关联材料码。
    materials = _material_index()
    seen_materials: list = []
    for entry in expanded["parts"]:
        text = _text(entry.get("material"))
        if text and text not in seen_materials:
            seen_materials.append(text)
    for text in seen_materials:
        items.append({
            "bom_category": "material",
            "item_key": text,
            "item_name": text,
            "material": text,
            "material_code": _resolve_material_code(text, materials),
            "status": "computed",
            "is_optional": 0,
            "source": "kb_material",
        })

    # 4) 工艺：按 step_name 去重，其余列取 seq 最小的一条。
    process_rows = _process_rows(box_code)
    deduped: dict = {}
    for row in process_rows:
        step = _text(row.get("step_name"))
        if not step:
            continue
        current = deduped.get(step)
        if current is None or (_num(row.get("seq")) or 0) < (_num(current.get("seq")) or 0):
            deduped[step] = row
    for step in sorted(deduped):
        row = deduped[step]
        items.append({
            "bom_category": "process",
            "item_key": step,
            "item_name": step,
            "component": _text(row.get("workstation")) or None,
            "status": "computed",
            "is_optional": 0,
            "source": "kb_packaging_process_template",
        })

    # 5) 工装/模具：逐条（不去重），只标涉及工装 + 待分摊，不算钱、不定寿命。
    for row in process_rows:
        keyword = _matched_keyword(row)
        if not keyword:
            continue
        seq = _num(row.get("seq")) or 0
        part_code = _text(row.get("part_code"))
        items.append({
            "bom_category": "tooling",
            "item_key": "tooling:%s:%s" % (part_code, _fmt_number(seq)),
            "item_name": _text(row.get("step_name")) or keyword,
            "part_code": part_code or None,
            "component": keyword,
            "status": "computed",
            "is_optional": 0,
            "source": "kb_packaging_process_template",
        })

    # 6) 包材：物流规则全量逐条。
    for rule in kb_repo.packaging_logistics_rules():
        code = _text(rule.get("rule_code"))
        if not code:
            continue
        items.append({
            "bom_category": "packaging",
            "item_key": code,
            "item_name": code,
            "component": _text(rule.get("shipping_mode")) or None,
            "status": "computed",
            "is_optional": 0,
            "source": "kb_packaging_logistics_rule",
        })

    for item in items:
        item["industry"] = PACKAGING_INDUSTRY
        item["engine_version"] = ENGINE_VERSION
    return items


# --------------------------------------------------------------------------- #
# 落库、读回与人工锁定（Spec §3、§4）
# --------------------------------------------------------------------------- #
#: 图纸零件的业务角色闭集之外的取值：图上没说自己是哪个部件（Spec §4.4）。
UNKNOWN_ROLE = "unknown"

#: 绑定方式闭集（Spec §4.4）：只允许这几种，别的写法一律不算留痕。
BINDING_METHODS = ("manual_mapping", "auto_position_area", "unbound")

#: 人工映射未完成时，BOM 行的业务角色保持这个值（不许静默贴一个模板角色名上去）。
UNBOUND_ROLE = "unbound"


def reject_unknown_role_autobind(part: Any, row: Any = None) -> dict:
    """`role=unknown` 的图纸零件**不许**自动贴业务角色名（Spec §4.4）。

    这一条的边界要写清楚：**尺寸**照旧可以自动回填（那是有证据的几何事实），但
    「这件的业务角色是盖壁长边」这类语义**不能**按行号/面积顺序从模板行上抄过来 ——
    图上没说的话，抄一遍就是编造。判据只有零件自己的 `role` 与调用方声明的绑定方式：

      · 零件 role 空或 `unknown` ⇒ `autobind=False`，业务角色保持 `unbound`；
      · 侧面的 `part_role` 也空时同样拒绝；
      · 其余情况放行，但仍要求把证据与操作者写进行上（`binding_record()`）。
    """
    payload = part if isinstance(part, dict) else {}
    role = _text(payload.get("role") or payload.get("part_role"))
    if not role or role == UNKNOWN_ROLE:
        return {"autobind": False, "role": role or "",
                "role_value": UNBOUND_ROLE,
                "reason": "role_unknown:%s" % (_text(payload.get("part_code")) or "?"),
                "message": ("图上没有写明这件的业务角色，不能按行号/面积顺序自动绑定；"
                            "请人工映射，或让该行保持 unbound")}
    return {"autobind": True, "role": role, "role_value": role, "reason": "", "message": ""}


def binding_record(part: Any, row: Any = None, *, bound_by: str = "",
                   method: str = "", evidence: Any = None) -> dict:
    """一条绑定的审计留痕（Spec §4.4）：证据 + 方式 + 操作者。

    `method` 必须落在 `BINDING_METHODS` 闭集里；不在闭集里按 `unbound` 处理 —— 宁可
    标成"没绑"，也不留一条说不清怎么绑上的记录。
    """
    payload = part if isinstance(part, dict) else {}
    target = row if isinstance(row, dict) else {}
    method_text = _text(method)
    if method_text not in BINDING_METHODS:
        method_text = "unbound"
    evidence_payload = evidence if isinstance(evidence, dict) else {}
    body = {
        "part_code": _text(payload.get("part_code")),
        "component_id": _text(payload.get("component_id")),
        "part_role": _text(payload.get("role") or payload.get("part_role")),
        "bom_item_key": _text(target.get("item_key")),
        "bom_part_code": _text(target.get("part_code")),
        "outline_status": _text(payload.get("outline_status")),
        "size_source": _text(payload.get("size_source")),
    }
    body.update({str(key): value for key, value in evidence_payload.items()})
    return {"binding_evidence": body, "binding_method": method_text,
            "bound_by": _text(bound_by) or "system"}


# --------------------------------------------------------------------------- #
# 业务角色的人工映射（Spec `packaging-part-role-manual-mapping.md` §3）
#
# §4.4 关掉了"零件 role=unknown 时自动贴业务角色名"这条错路，但没有给对的路：未映射清单
# 读不回来、人工映射没有任何生产入口 —— 于是"必须先完成人工映射"永远做不完。这一节只补
# **看得见**与**做得动**两件事，绝不猜角色：候选只来自确认盒型的部件模板，写入只改角色与留痕。
# --------------------------------------------------------------------------- #
#: 映射走 meta 文档通道（与 `PAIRING_DOC_KEY` 同范式）：`by_requirement[需求单][行键] = 记录`。
ROLE_MAP_DOC_KEY = "packaging_bom_role_map"
ROLE_MAP_ENGINE_VERSION = "packaging_bom_role_map_v1"
ROLE_MAP_ACTION = "workflow:packaging_bom_role_mapped"
#: 这三个取值都算"还没映射"（Spec §2.1）。
ROLE_UNBOUND_VALUES = ("", UNKNOWN_ROLE, UNBOUND_ROLE)


def _role_binding(row: Any) -> dict:
    """行上的 `dwg_binding`（没有就给空壳；不改入参）。"""
    if not isinstance(row, dict):
        return {}
    source = _loads(row.get("size_source_json"), {})
    binding = source.get("dwg_binding") if isinstance(source, dict) else None
    return dict(binding) if isinstance(binding, dict) else {}


def _row_role(row: Any) -> str:
    """行当前的业务角色：`dwg_binding.role_value` 优先，其次行上的 `part_role`。"""
    if not isinstance(row, dict):
        return ""
    return _text(_role_binding(row).get("role_value")) or _text(row.get("part_role"))


def _row_part_code(row: Any) -> str:
    """行绑到的**图纸零件**号（不是 BOM 行自己的 `part_code`）。"""
    if not isinstance(row, dict):
        return ""
    return _text(_role_binding(row).get("part_code")) or _text(row.get("part_code"))


def role_candidates(part_templates: Any) -> list:
    """候选角色 = 确认盒型部件模板里的 `component`，按模板顺序去重、丢空值（Spec §2.2）。

    只认模板这一路来源：模型、自由文本、`item_name` 拆词一律**不许**当候选。
    """
    out: list = []
    for row in (part_templates or []):
        if not isinstance(row, dict):
            continue
        text = _text(row.get("component"))
        if text and text not in out:
            out.append(text)
    return out


def role_map_status(items: Any, *, box_type_code: str = "", part_templates: Any = None,
                    role_map: Any = None) -> dict:
    """**未映射清单**（Spec §2.1/§3）：只有绑到零件的部件行才进，材料/工序行不进。

    `mapped_total` 是"已经映射过的部件行"计数 —— 它在清单之外，用来回答"还剩几行"。
    """
    candidates = role_candidates(part_templates)
    saved = role_map if isinstance(role_map, dict) else {}
    saved_rows = saved.get("by_requirement") if isinstance(saved.get("by_requirement"), dict) else {}
    if isinstance(saved.get("items"), dict):                # 也接受"已过滤到本需求单"的形状
        saved_rows = saved["items"]
    unbound: list = []
    mapped_total = 0
    for row in (items or []):
        if not isinstance(row, dict):
            continue
        if _text(row.get("bom_category")) not in PART_CATEGORIES:
            continue
        binding = _role_binding(row)
        part_code = _text(binding.get("part_code")) or _text(row.get("part_code"))
        current = _text(binding.get("role_value")) or _text(row.get("part_role"))
        if current and current not in ROLE_UNBOUND_VALUES:
            mapped_total += 1
            continue
        if not part_code and not binding:
            continue                                        # 没绑到零件：不是"未映射"，是"没零件"
        record = saved_rows.get(_text(row.get("item_key"))) or {}
        record = record if isinstance(record, dict) else {}
        unbound.append({
            "item_key": _text(row.get("item_key")),
            "item_name": _text(row.get("item_name")),
            "bom_category": _text(row.get("bom_category")),
            "part_code": part_code,
            "part_role": current or UNBOUND_ROLE,
            "reason": "role_unknown:%s" % (part_code or "?"),
            "role_candidates": list(candidates),
            "size_source": _text(binding.get("size_source") or row.get("size_source")),
            "outline_status": _text(binding.get("outline_status") or row.get("outline_status")),
            "mapped": False,
            "mapped_by": _text(record.get("mapped_by")),
            "mapped_at": _text(record.get("mapped_at")),
            "note": _text(record.get("note")),
        })
    return {
        "engine_version": ROLE_MAP_ENGINE_VERSION,
        "candidates_source": "confirmed_box_type",
        "box_type_code": _text(box_type_code),
        "role_candidates": list(candidates),
        "unbound_total": len(unbound),
        "mapped_total": mapped_total,
        "items": unbound,
    }


def apply_role_mapping(items: Any, *, item_key: str, part_code: str, role: str,
                       candidates: Any = None, actor: Any = None, note: str = "",
                       mapped_at: str = "", history: Any = None) -> dict:
    """把**人工**指定的业务角色写到一行上；纯函数，不改入参（Spec §2.3–§2.6/§3）。

    只改角色与留痕（`part_role` + `size_source_json.dwg_binding.*`）：尺寸、材料、状态、
    锁定一个字都不动。同角色重复提交 = 无变化（`changed=false`，`mapped_at` 不变）；
    换角色必须保留旧角色与旧时间（`superseded_role` / `superseded_at` / `superseded_by`）。
    """
    wanted = _text(role)
    if not wanted or wanted in ROLE_UNBOUND_VALUES:
        raise BomError("业务角色不能为空，也不能是 unknown / unbound（Spec §3 失败口径表）",
                       400, "role_required")
    key = _text(item_key)
    rows = [copy.deepcopy(row) if isinstance(row, dict) else row for row in (items or [])]
    target = next((row for row in rows
                   if isinstance(row, dict) and _text(row.get("item_key")) == key), None)
    if target is None:
        raise BomError("BOM 行不存在：%s（Spec §3 失败口径表）" % key, 404, "item_not_found")

    binding = _role_binding(target)
    bound_code = _text(binding.get("part_code")) or _text(target.get("part_code"))
    given_code = _text(part_code)
    if given_code and bound_code and given_code != bound_code:
        raise BomError("这一行绑的是零件 %s，不是 %s（Spec §3 失败口径表）"
                       % (bound_code, given_code), 409, "part_mismatch")
    options = [_text(name) for name in (candidates or []) if _text(name)]
    if options and wanted not in options:
        raise BomError("角色 %s 不在确认盒型的部件模板里（Spec §3 失败口径表）" % wanted,
                       400, "role_not_in_candidates")

    previous_raw = _text(binding.get("role_value")) or _text(target.get("part_role"))
    previous = previous_raw or UNBOUND_ROLE
    previous_at = _text(binding.get("mapped_at"))
    previous_by = _text(binding.get("bound_by"))
    by = _actor_name(actor) or "system"
    resolved_part = bound_code or given_code
    record = {
        "item_key": key, "part_code": resolved_part, "role": wanted,
        "previous_role": previous, "superseded_role": "", "mapped_by": by,
        "mapped_at": previous_at, "note": _text(binding.get("note")),
        "binding_method": "manual_mapping",
    }
    audit = {
        "action": ROLE_MAP_ACTION, "item_key": key, "part_code": resolved_part,
        "role": wanted, "previous_role": previous, "by": by, "superseded": False,
    }
    if previous_raw not in ROLE_UNBOUND_VALUES and previous_raw == wanted:
        # 幂等（Spec §2.5）：同角色重复提交不改任何东西，包括 `mapped_at`，也不重复写审计。
        return {"items": rows, "changed": False, "record": record,
                "audit": {**audit, "superseded": False, "changed": False}}

    stamp = _text(mapped_at) or da_db.now()
    superseded = "" if previous_raw in ROLE_UNBOUND_VALUES else previous_raw
    evidence = dict(binding.get("binding_evidence") or {})
    evidence.update({"rule_id": ROLE_MAP_ENGINE_VERSION, "mapped_at": stamp,
                     "role_candidates": options, "note": _text(note)})
    if superseded:
        evidence.update({"superseded_role": superseded, "superseded_at": previous_at,
                         "superseded_by": previous_by})
    updated = dict(binding)
    updated.update({"role_value": wanted, "part_role": wanted,
                    "binding_method": "manual_mapping", "bound_by": by,
                    "mapped_at": stamp, "note": _text(note),
                    "binding_evidence": evidence})
    source = _loads(target.get("size_source_json"), {})
    source = dict(source) if isinstance(source, dict) else {}
    source["dwg_binding"] = updated
    target["size_source_json"] = (_json_text(source)
                                  if isinstance(target.get("size_source_json"), str)
                                  else source)
    target["part_role"] = wanted
    record.update({"previous_role": previous, "superseded_role": superseded,
                   "mapped_by": by, "mapped_at": stamp, "note": _text(note)})
    audit["superseded"] = bool(superseded)
    if history is not None:
        record["history"] = list(history) + ([{"role": superseded, "mapped_at": previous_at,
                                               "mapped_by": previous_by}] if superseded else [])
    return {"items": rows, "changed": True, "record": record, "audit": audit}


def _role_doc(project_id: str) -> dict:
    """读映射文档（`{"by_requirement": {需求单: {行键: 记录}}}`）。

    **读不到必须抛**（Spec `packaging-silent-degradation-disclosure.md` §2.2）：
    `apply_saved_role_map()` 是 `build_bom()` 每次都调的，"读不出来按没有"会让文档通道
    抖一下就把人工确认过的角色算没（Spec `packaging-part-role-manual-mapping.md` §2.8），
    而且没有任何留痕 —— 失败与"从来没人映射过"必须能分开。
    """
    try:
        from ..storage.meta_backend import get_backend
        doc = get_backend().get_doc(project_id, ROLE_MAP_DOC_KEY) or {}
    except BomError:
        raise
    except Exception as exc:                            # noqa: BLE001 - 见 docstring：显式失败
        raise BomError(
            "人工角色映射读不到（文档通道不可用：%s）；别把这次当成「从来没人映射过」"
            % type(exc).__name__, 503, "role_map_unavailable") from exc
    rows = doc.get("by_requirement") if isinstance(doc, dict) else None
    return {"by_requirement": rows if isinstance(rows, dict) else {}}


def role_map_doc(project_id: str) -> dict:
    """公开的读入口：映射文档（Spec §3）。读不到 → 抛 `BomError`（`role_map_unavailable`）。"""
    return _role_doc(project_id)


def load_role_map(project_id: str, requirement_no: str = "") -> dict:
    """某个需求单的 `{行键: 记录}`（没有时 `{}`）。"""
    rows = _role_doc(project_id)["by_requirement"].get(_text(requirement_no) or "")
    return dict(rows) if isinstance(rows, dict) else {}


def save_role_mapping(project_id: str, requirement_no: str, row: dict) -> None:
    """把一条人工映射落盘（Spec §3）：**BOM 行本身** + 文档通道留痕。

    `row` 是 `apply_role_mapping()` 返回的 `items` 里那一行（不是 `record` 摘要）——
    事实源是 BOM 行的 `size_source_json.dwg_binding`；文档那一份只回答"谁在什么时候映射的"，
    并在重算 BOM 之后供 `apply_saved_role_map()` 重放。
    """
    if not isinstance(row, dict) or not _text(row.get("item_key")):
        return
    req_no = _text(requirement_no)
    key = _text(row.get("item_key"))
    binding = _role_binding(row)
    if binding:
        source = _loads(row.get("size_source_json"), {})
        try:
            da_db.execute(
                "UPDATE wip_packaging_bom_item SET size_source_json = ?, updated_at = ? "
                "WHERE project_id = ? AND requirement_no = ? AND bom_category = ? "
                "AND item_key = ?",
                [_json_text(source if isinstance(source, dict) else {}), da_db.now(),
                 project_id, req_no, _text(row.get("bom_category")), key])
        except Exception:                               # noqa: BLE001 - 落盘失败如实抛给调用方
            raise
    try:
        from ..storage.meta_backend import get_backend
        doc = _role_doc(project_id)
        bucket = doc["by_requirement"].setdefault(req_no, {})
        if not isinstance(bucket, dict):
            bucket = {}
            doc["by_requirement"][req_no] = bucket
        history = list(binding.get("binding_evidence", {}).get("history") or [])
        prior = bucket.get(key)
        if isinstance(prior, dict) and isinstance(prior.get("history"), list) and not history:
            history = list(prior["history"])
        previous = _text(binding.get("binding_evidence", {}).get("superseded_role"))
        if previous:
            history = history + [{"role": previous,
                                  "mapped_at": _text(binding.get("binding_evidence", {})
                                                     .get("superseded_at")),
                                  "mapped_by": _text(binding.get("binding_evidence", {})
                                                     .get("superseded_by"))}]
        bucket[key] = {
            "item_key": key,
            "part_code": _text(binding.get("part_code")),
            "role": _text(binding.get("role_value")),
            "previous_role": previous or UNBOUND_ROLE,
            "superseded_role": previous,
            "mapped_by": _text(binding.get("bound_by")),
            "mapped_at": _text(binding.get("mapped_at")),
            "note": _text(binding.get("note")),
            "binding_method": _text(binding.get("binding_method")),
            "history": history,
        }
        get_backend().put_doc(project_id, ROLE_MAP_DOC_KEY, doc)
    except BomError:
        raise
    except Exception as exc:                            # noqa: BLE001 - 见下：必须显式失败
        # 留痕没落盘就必须抛（Spec `packaging-silent-degradation-disclosure.md` §2.2）：
        # 以前静默 return、接口回 200，于是"我映射了、它没了"无从追起 —— 下一次重算
        # 读到的还是上一版（或空），人工映射被静默清掉。
        raise BomError("人工角色映射没保存成功（%s）；请重试，别把它当成已经映射过"
                       % type(exc).__name__, 503, "role_map_save_failed") from exc


def role_candidates_for(project_id: str, requirement_no: str = "") -> dict:
    """当前**确认盒型**的部件模板与候选角色（读 KB；读不到给空清单 **+ 显式披露**）。

    `templates_unavailable` 非空表示"这一趟没读到"（Spec
    `packaging-silent-degradation-disclosure.md` §2.3）：它必须与"这个盒型确实没有部件模板"
    （`{}`）分得开 —— 否则用户看到空下拉会去改盒型、以为模板没入库。
    """
    req_no = _resolve_requirement_no(project_id, requirement_no)
    record = da_repo.load_box_match(project_id, req_no) or {}
    box_code = _text(record.get("confirmed_box_type"))
    templates: list = []
    unavailable: dict = {}
    if box_code:
        try:
            templates = list(kb_repo.packaging_part_templates(box_code) or [])
        except Exception as exc:                        # noqa: BLE001 - 不挡人工映射，但必须披露
            templates = []
            unavailable = {"code": "template_lookup_failed", "reason": type(exc).__name__,
                           "message": "部件模板暂时读不到（知识库读失败：%s），"
                                      "请稍后重试；这不代表该盒型没有部件模板"
                                      % type(exc).__name__}
    return {"box_type_code": box_code, "requirement_no": req_no,
            "part_templates": templates,
            "role_candidates": role_candidates(templates),
            "templates_unavailable": unavailable}


def apply_saved_role_map(project_id: str, requirement_no: str, items: list) -> list:
    """重算 BOM 之后**重放**已保存的人工映射（Spec §2.8）；没有映射时原样返回。

    只重放"零件对得上"的那些：重算之后配对换了零件时，旧映射不套到新零件上。
    """
    saved = load_role_map(project_id, requirement_no)
    if not saved:
        return items
    rows = [copy.deepcopy(row) if isinstance(row, dict) else row for row in (items or [])]
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        record = saved.get(_text(row.get("item_key")))
        if not isinstance(record, dict):
            continue
        role = _text(record.get("role"))
        if not role or role in ROLE_UNBOUND_VALUES:
            continue
        try:
            replayed = apply_role_mapping(
                [row], item_key=row.get("item_key"), part_code=_row_part_code(row),
                role=role, actor=record.get("mapped_by"), note=_text(record.get("note")),
                mapped_at=_text(record.get("mapped_at")), history=record.get("history"))
        except BomError:
            continue
        rows[index] = replayed["items"][0]
    return rows


def _item_out(row: dict) -> dict:
    out = dict(row)
    missing = _loads(row.get("missing_variables"), [])
    out["missing_variables"] = missing if isinstance(missing, list) else []
    # 绑定留痕（Spec §4.4）：证据 / 方式 / 操作者从 size_source 的 dwg_binding 里读出来，
    # 也放在明细行的顶层 —— 读接口与看板不必再钻进嵌套结构里找"这行是谁绑的"。
    source = _loads(row.get("size_source_json"), {}) if row.get("size_source_json") else {}
    binding = source.get("dwg_binding") if isinstance(source, dict) else None
    if isinstance(binding, dict) and binding:
        out.setdefault("binding_evidence", binding.get("binding_evidence") or {})
        out.setdefault("binding_method", _text(binding.get("binding_method")))
        out.setdefault("bound_by", _text(binding.get("bound_by")))
        out.setdefault("part_role", _text(binding.get("part_role")))
    return out


def _stats(items: list) -> dict:
    by_category: dict = {}
    for item in items:
        category = _text(item.get("bom_category"))
        by_category[category] = by_category.get(category, 0) + 1
    computed = sum(1 for item in items
                   if _text(item.get("bom_category")) in PART_CATEGORIES
                   and _text(item.get("status")) == "computed")
    needs_input = sum(1 for item in items
                      if _text(item.get("bom_category")) in PART_CATEGORIES
                      and _text(item.get("status")) == "needs_input")
    unresolved = [item for item in items
                  if _text(item.get("bom_category")) == "material"
                  and not _text(item.get("material_code")) and _text(item.get("material"))]
    return {
        "total": len(items),
        "by_category": {key: value for key, value in by_category.items() if value},
        "computed": computed,
        "needs_input": needs_input,
        "locked": sum(1 for item in items if int(item.get("locked") or 0) == 1),
        "material_unresolved": len(unresolved),
    }


def load_bom(project_id: str, requirement_no: str = "") -> dict:
    """读回 BOM + 缺口 + 统计；没有 BOM 行时 `built = false`、`items = []`，不报错。"""
    req_no = _resolve_requirement_no(project_id, requirement_no)
    rows = da_repo.load_packaging_bom(project_id, req_no)
    items = [_item_out(row) for row in rows]
    box_type_code = ""
    for item in items:
        if _text(item.get("bom_category")) == "finished":
            box_type_code = _text(item.get("item_key"))
            break
    needs_input: list = []
    missing_variables: dict = {}
    material_unresolved: list = []
    for item in items:
        category = _text(item.get("bom_category"))
        status = _text(item.get("status"))
        if category in PART_CATEGORIES and status == "needs_input":
            key = _text(item.get("item_key"))
            needs_input.append(key)
            missing_variables[key] = list(item.get("missing_variables") or [])
        if (category == "material" and not _text(item.get("material_code"))
                and _text(item.get("material"))):
            material_unresolved.append(_text(item.get("item_key")))
    generated_at = _text(items[0].get("generated_at")) if items else ""
    box_record = da_repo.load_box_match(project_id, req_no) or {}
    pairing = _pairing_scope(project_id, req_no)
    # 走 `_role_scope()` 这个既有接缝（调用方与测试都按它打桩），键一律按可选读。
    role_scope = _role_scope(project_id, req_no, items, box_type_code) or {}
    return {
        "built": bool(items),
        "box_type_code": box_type_code,
        "requirement_no": req_no,
        "engine_version": ENGINE_VERSION,
        # 上游版本的埋点（DWG 第 5 批 Spec §6.1）：BOM 必须带出它是基于哪一版盒型确认算的。
        "source_versions": {
            "box_type_code": _text(box_record.get("confirmed_box_type")) or box_type_code,
            "engine_version": _text(box_record.get("engine_version")),
            "confirmed_by": _text(box_record.get("confirmed_by")),
            "confirmed_at": _text(box_record.get("confirmed_at")),
        },
        "generated_at": generated_at,
        "items": items,
        # 「在报告里单列不一致项」（Spec `packaging-parse-to-downstream-seams.md` §3.2）：
        # 键**必须存在**，没有不一致时给 `[]`；只在 build 时算得出，所以按需求单存一份文档。
        "pairing_review": pairing["review"],
        # 披露是**加法**（Spec `packaging-silent-degradation-disclosure.md` §2.5/§3）：
        # 读不到配对复核文档时清单照旧给 []，但标记必须非空 —— 不许看起来"这次配对干净"。
        "pairing_review_unavailable": pairing["unavailable"],
        # 回填失败留痕（Spec §2.1）：键**必须存在**，没有失败时 `{}`。
        "binding_error": _bind_error_scope(project_id, req_no),
        # 未映射清单（Spec `packaging-part-role-manual-mapping.md` §4.3）：以前
        # `_bind_parts()` 把 `bind_rows()` 算好的 `role_unbound` 丢在这里，于是
        # "还有 11 行没映射"在任何一个读接口上都看不见。现在按**当前行**现算（与清单
        # 永远一致），没有未映射行时给 `[]` / `0`，绝不省略键。
        "role_unbound": list(role_scope.get("items") or []),
        "role_unbound_total": int(role_scope.get("unbound_total") or 0),
        # 人工映射文档读不到时（Spec §2.2 改成显式失败）读接口**不许**按"没人映射过"渲染：
        # 清单留空但标记非空，界面据此说"暂时读不到，请稍后重试"。
        "role_unbound_unavailable": dict(role_scope.get("unavailable") or {}),
        "gaps": {
            "needs_input": needs_input,
            "missing_variables": missing_variables,
            "material_unresolved": material_unresolved,
        },
        "stats": _stats(items),
    }


def _load_role_scope(project_id: str, requirement_no: str, items: list,
                     box_type_code: str = "", *, unavailable: Optional[dict] = None) -> dict:
    """未映射清单的**现算**入口（读接口与落库共用一处口径，不另存一份会过期的账）。

    候选角色取当前确认盒型的部件模板；知识库读不到时清单照出（候选为空），
    绝不因为"读不到候选"就不报"这一行还没映射"。

    人工映射文档读不到时（`role_map_doc()` 现在会抛，Spec §2.2）：清单给空 + 显式标记
    （`unavailable`），**绝不**按"没人映射过"渲染。
    """
    templates: list = []
    try:
        templates = list(role_candidates_for(project_id, requirement_no).get("part_templates") or [])
    except Exception:                                   # noqa: BLE001 - 读不到 KB 不挡披露
        templates = []
    if unavailable is None:
        try:
            role_map = role_map_doc(project_id)
        except BomError as exc:
            return {"items": [], "unbound_total": 0, "mapped_total": 0,
                    "unavailable": {"code": exc.code or "role_map_unavailable",
                                    "reason": "doc_channel_unavailable",
                                    "message": str(exc)}}
        unavailable = {}
    else:
        role_map = {}
    status = role_map_status(items, box_type_code=box_type_code, part_templates=templates,
                             role_map=role_map)
    status["unavailable"] = dict(unavailable or {})
    return status


def _role_scope(project_id: str, requirement_no: str, items: list,
                box_type_code: str = "") -> dict:
    """`_load_role_scope()` 的兼容包装（返回体形状与既有调用方一致）。"""
    return _load_role_scope(project_id, requirement_no, items, box_type_code)


def _save_role_unbound(project_id: str, requirement_no: str, rows: list) -> None:
    """把 `bind_rows()` 算出来的未映射清单留一份档（披露是加法，写盘失败不改结论）。"""
    try:
        from ..storage.meta_backend import get_backend
        doc = _role_doc(project_id)
        bucket = doc.setdefault("unbound", {})
        if not isinstance(bucket, dict):
            bucket = {}
            doc["unbound"] = bucket
        bucket[_text(requirement_no) or ""] = [dict(row) for row in (rows or [])
                                              if isinstance(row, dict)]
        get_backend().put_doc(project_id, ROLE_MAP_DOC_KEY, doc)
    except Exception:                                   # noqa: BLE001
        return


def _pairing_doc(project_id: str) -> dict:
    """读配对复核文档（`{"by_requirement": {需求单: [...]}}`）。

    **读不到必须抛**（Spec `packaging-silent-degradation-disclosure.md` §1.5/§2.5）：
    配对复核是唯一一处把"配对后材料明显不同类"喊出来的地方，"读不到按没有"等于让一次可疑
    配对在报告里凭空消失 —— 披露失败与"这次配对没有问题"必须分得开。
    """
    try:
        from ..storage.meta_backend import get_backend
        doc = get_backend().get_doc(project_id, PAIRING_DOC_KEY) or {}
    except BomError:
        raise
    except Exception as exc:                            # noqa: BLE001 - 见 docstring：显式失败
        raise BomError(
            "配对复核读不到（文档通道不可用：%s）；别把这次当成「这次配对没有不一致项」"
            % type(exc).__name__, 503, "pairing_review_unavailable") from exc
    rows = doc.get("by_requirement") if isinstance(doc, dict) else None
    return {"by_requirement": rows if isinstance(rows, dict) else {}}


def _pairing_scope(project_id: str, requirement_no: str = "") -> dict:
    """配对复核的**读侧**披露：`{"review": [...], "unavailable": {...}}`。

    读得到 → 清单逐字带出、`unavailable = {}`；读不到 → `review = []` 但
    `unavailable["code"] == "pairing_review_unavailable"`（既有结论键一个字不改，Spec §3）。
    """
    try:
        rows = _pairing_doc(project_id)["by_requirement"].get(_text(requirement_no) or "")
    except BomError as exc:
        return {"review": [],
                "unavailable": {"code": exc.code or "pairing_review_unavailable",
                                "reason": "doc_channel_unavailable",
                                "message": str(exc)}}
    return {"review": [dict(row) for row in rows or [] if isinstance(row, dict)],
            "unavailable": {}}


def _load_pairing_review(project_id: str, requirement_no: str = "") -> list:
    """只读清单（读不到 → 抛 `BomError`）；披露版本请用 `_pairing_scope()`。"""
    rows = _pairing_doc(project_id)["by_requirement"].get(_text(requirement_no) or "")
    return [dict(row) for row in rows or [] if isinstance(row, dict)]


def _bind_error_scope(project_id: str, requirement_no: str = "") -> dict:
    """回填失败的**读侧**披露（Spec `packaging-silent-degradation-disclosure.md` §2.1）。

    读得到就原样带出（没有失败时 `{}`）；连这份披露文档都读不到时给
    `code="binding_error_unavailable"` —— "查不到有没有失败"同样不许显示成"没失败"。
    """
    try:
        from ..storage.meta_backend import get_backend
        doc = get_backend().get_doc(project_id, BIND_ERROR_DOC_KEY) or {}
    except Exception as exc:                            # noqa: BLE001 - 见 docstring
        return {"code": "binding_error_unavailable", "reason": type(exc).__name__,
                "message": "回填失败留痕读不到（文档通道不可用：%s），"
                           "别把这次当成「回填没有失败」" % type(exc).__name__}
    rows = doc.get("by_requirement") if isinstance(doc, dict) else None
    item = (rows or {}).get(_text(requirement_no) or "") if isinstance(rows, dict) else None
    return dict(item) if isinstance(item, dict) else {}


def _save_bind_error(project_id: str, requirement_no: str, error: Any) -> None:
    """把回填失败留一份档；这次没失败就把上一次的留痕**清掉**（不许留下过期告警）。

    写盘失败不改 BOM 结论（披露是加法）—— 读侧会以 `binding_error_unavailable` 说出来。
    """
    req_no = _text(requirement_no) or ""
    try:
        from ..storage.meta_backend import get_backend
        doc = get_backend().get_doc(project_id, BIND_ERROR_DOC_KEY) or {}
        if not isinstance(doc, dict):
            doc = {}
        bucket = doc.setdefault("by_requirement", {})
        if not isinstance(bucket, dict):
            bucket = {}
            doc["by_requirement"] = bucket
        record = dict(error) if isinstance(error, dict) and error else {}
        if record:
            bucket[req_no] = record
        else:
            bucket.pop(req_no, None)
        get_backend().put_doc(project_id, BIND_ERROR_DOC_KEY, doc)
    except Exception:                                   # noqa: BLE001 - 披露写不进去不挡 BOM
        return


def _save_pairing_review(project_id: str, requirement_no: str, review: list) -> None:
    """按需求单存一份不一致项清单；写盘失败不改 BOM 结论（披露是加法）。"""
    try:
        from ..storage.meta_backend import get_backend
        doc = _pairing_doc(project_id)
        doc["by_requirement"][_text(requirement_no) or ""] = [dict(row) for row in review or []
                                                             if isinstance(row, dict)]
        get_backend().put_doc(project_id, PAIRING_DOC_KEY, doc)
    except Exception:                                   # noqa: BLE001
        return


def _bind_parts(project_id: str, items: list, requirement_no: str = "") -> tuple:
    """有零件文档就自动回填（Spec `packaging-dwg-parts-extraction.md` C7）。

    返回 `(items, pairing_review, role_unbound, binding_error)` —— 第二个是"配对后材料明显不同类"的
    清单，由 `build_bom()` 落一份文档，`load_bom()` 读回来（Spec
    `packaging-parse-to-downstream-seams.md` §3.2）；第三个是"零件没说自己是哪个部件、
    业务角色还没映射"的清单（Spec `packaging-part-role-manual-mapping.md` §2.1/§4.3）——
    两个都是 `bind_rows()` 早就算出来、以前在 `_bind_parts()` 这里被丢掉的。

    没有零件文档 / 读不到 → 逐字保持今天的口径（`needs_input` 一个不少），
    绝不用需求尺寸反推、也绝不编数。延迟导入是为了不让 bom ↔ parts 互相 import。
    """
    try:
        from . import packaging_parts

        doc = packaging_parts.load_parts(project_id)
        if not isinstance(doc, dict) or not doc.get("parts"):
            # 「没有零件文档」不是失败（Spec §2.1 第 3 条）：留痕必须是 {}。
            return items, [], [], {}
        result = packaging_parts.bind_rows(items, doc)
        return (list(result.get("items") or items),
                [dict(row) for row in (result.get("pairing_review") or [])
                 if isinstance(row, dict)],
                [dict(row) for row in (result.get("role_unbound") or [])
                 if isinstance(row, dict)],
                {})
    except Exception as exc:                            # noqa: BLE001 - 回填失败不改既有结论
        # 失败必须**报出来**（Spec `packaging-silent-degradation-disclosure.md` §2.1）：
        # 以前返回 3 元组，与"这个项目根本没有零件文档"逐字相同 —— 现场只能看到
        # "零件有、BOM 没数"，没有任何地方说得出"回填这一步挂了"。
        return items, [], [], {"code": "part_binding_failed",
                               "reason": "%s: %s" % (type(exc).__name__, exc),
                               "message": "零件回填这一步挂了（%s），BOM 行维持原状未改；"
                                          "请查零件文档形状与配对口径，修好后重算"
                                          % type(exc).__name__}


def build_bom(project_id: str, requirement_no: str = "", *,
              overrides: Optional[dict] = None) -> dict:
    """读确认盒型 → 展开 → 组装七类 → 整体替换落库（锁定行原样保留）。"""
    doc = store.load_requirement(project_id) or {}
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    if _industry_of(data) != PACKAGING_INDUSTRY:
        raise BomError("包装 BOM 只对包装行业的需求单生效", 400, "not_packaging_industry")

    req_no = _text(requirement_no) or _text(doc.get("requirement_no"))
    record = da_repo.load_box_match(project_id, req_no) or {}
    box_code = _text(record.get("confirmed_box_type"))
    if _text(record.get("decision")) != "confirmed" or not box_code:
        raise BomError("尚未确认盒型，无法展开部件（Spec §2.1）", 409,
                       "box_type_not_confirmed")

    expanded = expand_parts(box_code, data, overrides=overrides or {})
    box = _load_box_type(box_code)
    items = _assemble(expanded, box, data, req_no)
    items, pairing_review, role_unbound, binding_error = _bind_parts(project_id, items, req_no)
    # 重算不许把人工映射算没了（Spec `packaging-part-role-manual-mapping.md` §2.8）：
    # `_bind_parts()` 只认尺寸证据、不会碰角色，映射必须在这之后**重放**回来。
    items = apply_saved_role_map(project_id, req_no, items)
    da_repo.save_packaging_bom(project_id, req_no, items)
    _save_pairing_review(project_id, req_no, pairing_review)
    _save_role_unbound(project_id, req_no, role_unbound)
    # 回填失败留痕（Spec `packaging-silent-degradation-disclosure.md` §2.1）：这次没失败就清掉
    # 上一次的，避免过期告警；BOM 结论一个字不改。
    _save_bind_error(project_id, req_no, binding_error)
    return load_bom(project_id, req_no)


def lock_bom_item(project_id: str, requirement_no: str, item_key: str, *,
                  actor: Any = None, locked: bool = True) -> dict:
    """锁定/解锁单个 BOM 行；重复同一状态幂等（不改 `locked_at`、不重复写审计）。"""
    req_no = _resolve_requirement_no(project_id, requirement_no)
    key = _text(item_key)
    rows = da_repo.load_packaging_bom(project_id, req_no)
    targets = [row for row in rows if _text(row.get("item_key")) == key]
    if not targets:
        raise BomError("BOM 行不存在：%s" % key, 404, "item_not_found")

    want = bool(locked)
    already = any(int(row.get("locked") or 0) == 1 for row in targets)
    if want == already:
        return load_bom(project_id, req_no)

    by = _actor_name(actor)
    now = da_db.now()
    for row in targets:
        if want:
            da_db.execute(
                "UPDATE wip_packaging_bom_item SET locked = 1, status = 'locked', "
                "locked_by = ?, locked_at = ?, updated_at = ? "
                "WHERE project_id = ? AND requirement_no = ? AND bom_category = ? "
                "AND item_key = ?",
                [by, now, now, project_id, req_no, _text(row.get("bom_category")), key])
        else:
            status = "needs_input" if _loads(row.get("missing_variables"), []) else "computed"
            da_db.execute(
                "UPDATE wip_packaging_bom_item SET locked = 0, status = ?, "
                "locked_by = NULL, locked_at = NULL, updated_at = ? "
                "WHERE project_id = ? AND requirement_no = ? AND bom_category = ? "
                "AND item_key = ?",
                [status, now, project_id, req_no, _text(row.get("bom_category")), key])
    store.audit(project_id,
                "workflow:packaging_bom_item_locked" if want
                else "workflow:packaging_bom_item_unlocked",
                {"requirement_no": req_no, "item_key": key, "locked": want, "by": by})
    return load_bom(project_id, req_no)

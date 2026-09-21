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
def _item_out(row: dict) -> dict:
    out = dict(row)
    missing = _loads(row.get("missing_variables"), [])
    out["missing_variables"] = missing if isinstance(missing, list) else []
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
        "pairing_review": _load_pairing_review(project_id, req_no),
        "gaps": {
            "needs_input": needs_input,
            "missing_variables": missing_variables,
            "material_unresolved": material_unresolved,
        },
        "stats": _stats(items),
    }


def _pairing_doc(project_id: str) -> dict:
    """读配对复核文档（`{"by_requirement": {需求单: [...]}}`）；读不到给空壳。"""
    try:
        from ..storage.meta_backend import get_backend
        doc = get_backend().get_doc(project_id, PAIRING_DOC_KEY) or {}
    except Exception:                                   # noqa: BLE001 - 读不出来按"没有"
        return {"by_requirement": {}}
    rows = doc.get("by_requirement") if isinstance(doc, dict) else None
    return {"by_requirement": rows if isinstance(rows, dict) else {}}


def _load_pairing_review(project_id: str, requirement_no: str = "") -> list:
    rows = _pairing_doc(project_id)["by_requirement"].get(_text(requirement_no) or "")
    return [dict(row) for row in rows or [] if isinstance(row, dict)]


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


def _bind_parts(project_id: str, items: list) -> tuple:
    """有零件文档就自动回填（Spec `packaging-dwg-parts-extraction.md` C7）。

    返回 `(items, pairing_review)` —— 后者是"配对后材料明显不同类"的清单，由
    `build_bom()` 落一份文档，`load_bom()` 读回来（Spec
    `packaging-parse-to-downstream-seams.md` §3.2）；`bind_rows()` 早就算出来了，
    以前在 `_bind_parts()` 这里被丢掉，于是没有任何读接口能看到。

    没有零件文档 / 读不到 → 逐字保持今天的口径（`needs_input` 一个不少），
    绝不用需求尺寸反推、也绝不编数。延迟导入是为了不让 bom ↔ parts 互相 import。
    """
    try:
        from . import packaging_parts

        doc = packaging_parts.load_parts(project_id)
        if not isinstance(doc, dict) or not doc.get("parts"):
            return items, []
        result = packaging_parts.bind_rows(items, doc)
        return (list(result.get("items") or items),
                [dict(row) for row in (result.get("pairing_review") or [])
                 if isinstance(row, dict)])
    except Exception:                                   # noqa: BLE001 - 回填失败不改既有结论
        return items, []


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
    items, pairing_review = _bind_parts(project_id, items)
    da_repo.save_packaging_bom(project_id, req_no, items)
    _save_pairing_review(project_id, req_no, pairing_review)
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

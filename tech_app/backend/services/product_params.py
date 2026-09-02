"""
成品参数字典 —— 2.2 组装与整合推荐出来的整机参数，要能直接落到报价测算单上。

事实源是《亿纬锂能DA梳理.xlsx》报价助手 sheet 的 **产品技术参数
（clm_calc_product_tech）**，由 tools/build_quote_product_params.py 生成成
agent_knowledge/rules/quote_product_params.json。本模块只负责三件事：

  1. as_prompt()  把某个产品族适用的字段清单交给模型，让它按这些口径填。
  2. align()      把模型/人填出来的参数对回 DA 字段（按 code → 名称 → 别名三级匹配）。
  3. checklist()  按 DA 的分组顺序出一份"哪些填了、哪些还缺"的对照表。

为什么要按产品族切：DA 这张表是三条产品线的并集（锂原电池 / 储能系统 / 光伏组件）。
给一台 18650 电池包推荐"边框膜厚""胶膜克重"不是参数覆盖得全，是胡说。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any, Optional

from ..config import ROOT_DIR

SPEC_PATH = ROOT_DIR / "agent_knowledge" / "rules" / "quote_product_params.json"
DEFAULT_FAMILY = "other"


@lru_cache(maxsize=1)
def spec() -> dict:
    """读字典。文件缺失/损坏时返回空字典而不是抛错 —— 2.2 的参数推荐本身还能跑，
    只是失去与报价的对齐能力，不该因为一份静态资源把整步废掉。"""
    try:
        return json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"families": [], "groups": [], "fields": [], "aliases": {}}


def families() -> list[dict]:
    return spec().get("families", [])


def family_keys() -> list[str]:
    return [item["key"] for item in families()]


def resolve_family(value: Optional[str]) -> str:
    """把模型给的产品族收敛到合法枚举。给了中文名或不认识的值时退回 other。"""
    text = str(value or "").strip()
    if not text:
        return DEFAULT_FAMILY
    for item in families():
        if text == item["key"] or text == item["name"]:
            return item["key"]
    # 只写了"电池包""储能"这种半截名字时按名称包含关系兜一次
    for item in families():
        if text in item["name"] or item["name"] in text:
            return item["key"]
    return DEFAULT_FAMILY


def _groups_of(family: str) -> list[dict]:
    return [g for g in spec().get("groups", []) if family in (g.get("families") or [])]


def fields_for(family: str) -> list[dict]:
    """某产品族适用的字段，按 DA 分组顺序排。"""
    order = {g["key"]: index for index, g in enumerate(_groups_of(family))}
    return sorted(
        (f for f in spec().get("fields", []) if f.get("group") in order),
        key=lambda f: order[f["group"]],
    )


# --------------------------------------------------------------------------- #
# 名称归一化与匹配
# --------------------------------------------------------------------------- #
_NOISE = re.compile(r"[\s（）()【】\[\]:：·・\-_/]")


def _key(text: Any) -> str:
    return _NOISE.sub("", str(text or "")).lower()


@lru_cache(maxsize=1)
def _name_index() -> dict[str, str]:
    """归一化名称 → 字段 code。覆盖 DA 名、展示名与别名表。"""
    index: dict[str, str] = {}
    for field in spec().get("fields", []):
        for label in (field.get("name"), field.get("da_name")):
            if label:
                index.setdefault(_key(label), field["code"])
    for label, code in (spec().get("aliases") or {}).items():
        index.setdefault(_key(label), code)
    return index


@lru_cache(maxsize=1)
def _by_code() -> dict[str, dict]:
    return {field["code"]: field for field in spec().get("fields", [])}


def match_code(param) -> Optional[str]:
    """把一条参数对到 DA 字段。三级：显式 code → 名称/别名 → 放弃（返回 None）。

    不做模糊/包含匹配：把"电芯重量"错配到"重量(整机)"上，报价那头拿到的就是错数。
    对不上就让它留在"补充参数"里，人一眼能看见。
    """
    code = str(getattr(param, "param_code", "") or "").strip()
    if code in _by_code():
        return code
    return _name_index().get(_key(getattr(param, "name", "")))


def align(plan, family: Optional[str] = None) -> str:
    """就地把参数对到 DA 字段：回填 param_code，并把 category 统一成 DA 分组名。

    返回归一化后的产品族。category 由分组接管是有意的 —— 原来模型自己写"结构/电气"，
    和 DA 的分组是两套口径，同一张表里混着两种分类没法看。
    """
    key = resolve_family(family if family is not None else getattr(plan, "product_family", None))
    group_name = {g["key"]: g["name"] for g in spec().get("groups", [])}
    # 丢掉无名行：IntegrationParam.name 放宽成可空是为了别让一条坏数据毁掉整份参数表，
    # 但没有名字的参数在界面和报价里都无处安放，留着只会变成一行空白。
    rows = [p for p in (getattr(plan, "params", None) or [])
            if str(getattr(p, "name", "") or "").strip()
            or str(getattr(p, "param_code", "") or "").strip()]
    if hasattr(plan, "params"):
        plan.params = rows
    for param in rows:
        code = match_code(param)
        param.param_code = code
        if code:
            field = _by_code()[code]
            # 只给了编码没给名字时按字典补上，界面才有东西显示
            if not str(getattr(param, "name", "") or "").strip():
                param.name = field["name"]
            param.category = group_name.get(field["group"]) or param.category
            # 单位只补空，**绝不覆盖**。模型写"整备质量 240 kg"、DA 的字段单位是 g，
            # 直接改成 g 就成了 240 g —— 数值没换算，覆盖单位等于凭空造了个错数。
            # 不一致的情况交给 checklist 标出来（unit_mismatch），由人决定怎么改。
            if field.get("unit") and not str(param.unit or "").strip():
                param.unit = field["unit"]
    return key


def checklist(plan, family: Optional[str] = None) -> dict:
    """按 DA 分组出一份对照表：每个适用字段填没填、填了什么、取值合不合枚举。

    这是 2.2 与报价之间的验收口径 —— 必填项没齐，这台成品就进不了报价测算单。
    """
    key = resolve_family(family if family is not None else getattr(plan, "product_family", None))
    applicable = fields_for(key)
    if not applicable:
        return {"family": key, "family_name": family_name(key), "groups": [], "extras": [],
                "summary": {"total": 0, "filled": 0, "required_total": 0, "required_filled": 0}}

    filled: dict[str, Any] = {}
    extras: list[dict] = []
    for param in getattr(plan, "params", []) or []:
        code = getattr(param, "param_code", None) or match_code(param)
        value = str(getattr(param, "value", "") or "").strip()
        if code and code in _by_code() and value and code not in filled:
            filled[code] = param
        elif not code:
            extras.append(_row_of(param))

    groups: list[dict] = []
    for group in _groups_of(key):
        rows = [_field_row(field, filled.get(field["code"]))
                for field in applicable if field["group"] == group["key"]]
        if rows:
            groups.append({"key": group["key"], "name": group["name"], "fields": rows})

    required = [field for field in applicable if field.get("required")]
    return {
        "family": key,
        "family_name": family_name(key),
        "groups": groups,
        "extras": extras,
        "summary": {
            "total": len(applicable),
            "filled": sum(1 for field in applicable if field["code"] in filled),
            "required_total": len(required),
            "required_filled": sum(1 for field in required if field["code"] in filled),
        },
    }


def field_of(code: str) -> Optional[dict]:
    """按字段编码取字典条目。人工补填时要拿它回填参数名与单位。"""
    return _by_code().get(str(code or "").strip())


# 由平台生成、人填不了的字段。成品编码是报价必填（定价与加价规则按它匹配产品行），
# 但它是 2.2「写入数据库」那一刻才产生的 92022xxx —— 把它算进"还要人补几项"，
# 就成了一个死结：整合参数不让确认，而确认之前没人会去点写库。
GENERATED_CODES = {"product_item_code"}


def missing_all(plan, family: Optional[str] = None) -> list[dict]:
    """所有还没有值的字段（不只必填）。智能补全按它决定要补哪些格子。

    平台生成项排除在外 —— 让模型去"补"一个成品编码，只会得到一个编出来的号。
    """
    report = checklist(plan, family)
    return [field for group in report["groups"] for field in group["fields"]
            if not field["filled"] and field["code"] not in GENERATED_CODES]


def missing_required(plan, family: Optional[str] = None,
                     include_generated: bool = False) -> list[dict]:
    """报价必填、但还没有值的字段。

    默认**不含平台生成项**（成品编码）：这个清单是给人看的"你还要补几项"，
    把填不了的东西列进去只会让人卡住。发送至报价那道门禁另外单独检查写库有没有做过
    （main.py::integration_send_to_quote），所以成品编码并没有被放过。
    """
    report = checklist(plan, family)
    return [field for group in report["groups"] for field in group["fields"]
            if field["required"] and not field["filled"]
            and (include_generated or field["code"] not in GENERATED_CODES)]


def rows_for_quote(plan, family: Optional[str] = None) -> dict:
    """交给报价那边的整机参数：按 DA 字段拉平成 code → 值，另附中文名与单位。

    报价测算单是按 DA 字段取数的，所以这里不回传 2.2 自己的参数名 —— 那边认的是
    成品编码/标称电压这些**字典字段**，名字对不上就落不进测算单。
    """
    report = checklist(plan, family)
    fields = [dict(field, group=group["name"])
              for group in report["groups"] for field in group["fields"]]
    return {
        "family": report["family"], "family_name": report["family_name"],
        "summary": report["summary"],
        "fields": [{"code": f["code"], "name": f["name"], "da_name": (field_of(f["code"]) or {}).get("da_name") or f["name"],
                    "value": f["value"], "unit": f["unit"], "group": f["group"],
                    "required": f["required"], "filled": f["filled"]} for f in fields],
        # 字典之外的自有参数也一并带过去：报价那边落不进固定列，但人要看得到。
        "extras": [{"name": row.get("name") or "", "value": row.get("value") or "",
                    "unit": row.get("unit") or ""} for row in report.get("extras") or []],
    }


def family_name(key: str) -> str:
    return next((item["name"] for item in families() if item["key"] == key), key)


def _row_of(param) -> dict:
    return {
        "name": getattr(param, "name", ""), "value": getattr(param, "value", ""),
        "unit": getattr(param, "unit", None), "basis": getattr(param, "basis", None),
        "source": getattr(param, "source", None),
        "confidence": getattr(param, "confidence", None),
    }


def _field_row(field: dict, param) -> dict:
    value = str(getattr(param, "value", "") or "").strip() if param else ""
    options = field.get("options") or []
    # 枚举越界只提示、不改值：DA 的候选集是按已有产品整理的，新产品出现新档位很正常，
    # 直接判错会让人以为是模型算错了。
    off_option = bool(value and options and value not in options)
    unit = field.get("unit") or ""
    given_unit = str(getattr(param, "unit", "") or "").strip() if param else ""
    return {
        "code": field["code"], "name": field["name"], "unit": unit,
        "type": field.get("type") or "", "required": bool(field.get("required")),
        # 平台生成项：界面上要标成"写库时生成"，而不是让人在那格里手填。
        "generated": field["code"] in GENERATED_CODES,
        "options": options, "example": field.get("example") or "",
        "value": value, "filled": bool(value),
        "basis": getattr(param, "basis", None) if param else None,
        "source": getattr(param, "source", None) if param else None,
        "confidence": getattr(param, "confidence", None) if param else None,
        "off_option": off_option,
        # 单位对不上必须报出来：报价按 DA 的单位取数，值却是按另一个单位写的，
        # 差的是量级，不是格式。
        "given_unit": given_unit,
        "unit_mismatch": bool(value and unit and given_unit and given_unit != unit),
    }


# --------------------------------------------------------------------------- #
# 给模型的清单
# --------------------------------------------------------------------------- #
def as_prompt(family: Optional[str] = None) -> str:
    """产品族清单 + 该族的字段清单。family 为空时给出全部族，让模型自己先判族。"""
    data = spec()
    if not data.get("fields"):
        return ""
    lines = [
        "【报价成品参数字典（来自亿纬锂能DA梳理 · 报价助手 · 产品技术参数 "
        f"{data.get('source', {}).get('table', '')}）】",
        "这是报价测算单上成品行要填的参数。2.2 推荐的整机参数必须覆盖它们，"
        "字段名请**原样使用下表的名称**，并在 param_code 里填对应的字段编码 —— "
        "对不上编码的参数在报价那头没有落点。",
        "",
        "先判定成品所属产品族（product_family 填 key）：",
    ]
    lines += [f"  {item['key']} —— {item['name']}：{item['hint']}" for item in families()]
    lines.append("")

    keys = [family] if family else family_keys()
    for key in keys:
        applicable = fields_for(key)
        if not applicable:
            continue
        lines.append(f"◆ product_family = {key}（{family_name(key)}）适用 {len(applicable)} 项：")
        current = ""
        for field in applicable:
            group = next((g["name"] for g in spec()["groups"] if g["key"] == field["group"]), "")
            if group != current:
                current = group
                lines.append(f"  · {group}")
            marks = []
            if field.get("required"):
                marks.append("报价必填")
            if field.get("unit"):
                marks.append(f"单位 {field['unit']}")
            if field.get("options"):
                marks.append("取值 " + "/".join(field["options"]))
            elif field.get("example"):
                marks.append(f"示例 {field['example'][:40]}")
            lines.append(f"      {field['code']:<24}{field['name']}"
                         + (f"（{'；'.join(marks)}）" if marks else ""))
        lines.append("")

    lines.append(
        "填写要求：\n"
        "  - **只填所属产品族适用的项**，其余整组跳过，不要为了凑数把别的产品线的参数搬过来。\n"
        "  - 每一项都要写 basis（这个值怎么来的：由哪几个零件尺寸叠加、取自需求单第几条、"
        "还是沿用电芯规格书），由零件尺寸算出来的必须写出算式。\n"
        "  - 有取值范围的字段请从给定取值里选；确实是新档位再写新值，并在 basis 里说明。\n"
        "  - 图纸和需求都推不出来的项，**不要编**：把它写进 open_questions，不要放进 params。\n"
        "  - 除清单外还有对本产品重要的参数，照常放进 params，param_code 留空即可。"
    )
    return "\n".join(lines)


def reload_spec() -> None:
    """重跑生成脚本后不重启服务也能生效（运维用）。"""
    spec.cache_clear()
    _name_index.cache_clear()
    _by_code.cache_clear()

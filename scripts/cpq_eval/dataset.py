# -*- coding: utf-8 -*-
"""数据集加载与 JSON Schema（2020-12 子集）校验。

为什么要自带校验器：CI 环境没有 ``jsonschema`` 依赖（本仓库只保证标准库），而数据集
契约必须能被离线、无第三方依赖地强制检查。这里实现的是 JSON Schema 2020-12 中数据集
实际用到的关键字子集：``$ref``（本地 ``#/...`` 与同目录文件名）、``type``、``enum``、
``const``、``required``、``properties``、``additionalProperties``、``items``、
``minItems`` / ``maxItems``、``minLength`` / ``maxLength``、``pattern``、``minimum`` /
``maximum``、``anyOf`` / ``allOf`` / ``oneOf`` / ``not``。

案例文件（``cases/<domain>/*.json``）是**套件**：``{suite_id, title, domain, cases:[...]}``；
每条 case 走 ``schemas/case.schema.json``。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import CASES_DIR, FIXTURES_DIR, SCHEMAS_DIR

FIXTURE_REF_KEYS = ("fixture", "fixtures", "document", "documents", "provider")


class DatasetError(Exception):
    """数据集结构性错误（schema、重复 id、缺失 fixture / spec）。"""


# --------------------------------------------------------------------------- #
# JSON Schema 子集校验器
# --------------------------------------------------------------------------- #
_SCHEMA_CACHE: dict = {}


def load_schema(name: str, _depth: int = 0) -> dict:
    key = str(name)
    if key in _SCHEMA_CACHE:
        return _SCHEMA_CACHE[key]
    if _depth > 4:
        raise DatasetError(f"schema $ref 递归过深：{name}")
    path = Path(name)
    if not path.is_absolute():
        path = SCHEMAS_DIR / path
    if not path.exists():
        raise DatasetError(f"缺少 schema 文件：{path}")
    schema = json.loads(path.read_text(encoding="utf-8"))
    _SCHEMA_CACHE[key] = schema
    return schema


def _type_ok(value, type_name: str) -> bool:
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    if type_name == "null":
        return value is None
    return True


def _resolve_ref(ref: str, root: dict) -> dict:
    if ref.startswith("#"):
        node = root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(node, list):
                node = node[int(part)]
            else:
                node = node.get(part, {})
        return node
    return load_schema(ref)


def validate(instance, schema: dict, path: str = "$", root: dict = None,
             errors: list = None, _depth: int = 0) -> list:
    """返回错误信息列表（空列表 = 通过）。``path`` 只用于报错定位。"""
    errors = [] if errors is None else errors
    root = schema if root is None else root
    if _depth > 40:
        return errors
    if not isinstance(schema, dict):
        return errors

    if "$ref" in schema:
        return validate(instance, _resolve_ref(schema["$ref"], root), path, root, errors, _depth + 1)

    for keyword in ("allOf",):
        for sub in schema.get(keyword, []) or []:
            validate(instance, sub, path, root, errors, _depth + 1)
    for keyword in ("anyOf", "oneOf"):
        subs = schema.get(keyword) or []
        if not subs:
            continue
        matched = 0
        for sub in subs:
            if not validate(instance, sub, path, root, [], _depth + 1):
                matched += 1
        if matched == 0:
            errors.append(f"{path}: 不满足 {keyword}（{len(subs)} 个分支全部失败）")
        elif keyword == "oneOf" and matched > 1:
            errors.append(f"{path}: 满足 oneOf 的多个分支（{matched}）")
    if "not" in schema:
        if not validate(instance, schema["not"], path, root, [], _depth + 1):
            errors.append(f"{path}: 命中了 not 分支")

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: 期望常量 {schema['const']!r}，实际 {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: 取值 {instance!r} 不在枚举内")

    type_name = schema.get("type")
    if type_name is not None:
        names = type_name if isinstance(type_name, list) else [type_name]
        if not any(_type_ok(instance, name) for name in names):
            errors.append(f"{path}: 类型应为 {names}，实际 {type(instance).__name__}")
            return errors

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: 长度小于 {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: 长度大于 {schema['maxLength']}")
        if "pattern" in schema:
            import re

            if not re.search(schema["pattern"], instance):
                errors.append(f"{path}: 不匹配 pattern {schema['pattern']}（{instance[:60]!r}）")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: 小于 minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: 大于 maximum {schema['maximum']}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: 元素少于 {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: 元素多于 {schema['maxItems']}")
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(instance):
                validate(item, items, f"{path}[{index}]", root, errors, _depth + 1)
        elif isinstance(items, list):
            for index, sub in enumerate(items):
                if index < len(instance):
                    validate(instance[index], sub, f"{path}[{index}]", root, errors, _depth + 1)

    if isinstance(instance, dict):
        import re

        for key in schema.get("required", []) or []:
            if key not in instance:
                errors.append(f"{path}: 缺少必填字段 {key}")
        props = schema.get("properties") or {}
        for key, value in instance.items():
            if key in props:
                validate(value, props[key], f"{path}.{key}", root, errors, _depth + 1)
                continue
            extra = schema.get("additionalProperties", True)
            matched = False
            for pattern, sub in (schema.get("patternProperties") or {}).items():
                if re.search(pattern, key):
                    matched = True
                    validate(value, sub, f"{path}.{key}", root, errors, _depth + 1)
            if matched:
                continue
            if extra is False:
                errors.append(f"{path}: 出现未声明字段 {key}")
            elif isinstance(extra, dict):
                validate(value, extra, f"{path}.{key}", root, errors, _depth + 1)
    return errors


# --------------------------------------------------------------------------- #
# 案例 / 套件
# --------------------------------------------------------------------------- #
class Case:
    """一条业务回归案例。``raw`` 是原始 JSON，属性只是常用字段的快捷方式。"""

    __slots__ = ("raw", "path", "suite_id")

    def __init__(self, raw: dict, path: Path, suite_id: str):
        self.raw = raw
        self.path = path
        self.suite_id = suite_id

    def __getattr__(self, item):
        try:
            return self.raw[item]
        except KeyError as exc:  # pragma: no cover - 只有写错字段名才会走到
            raise AttributeError(item) from exc

    def get(self, key, default=None):
        return self.raw.get(key, default)

    @property
    def case_id(self) -> str:
        return str(self.raw.get("id") or "")

    @property
    def rel_path(self) -> str:
        try:
            return str(self.path.relative_to(CASES_DIR.parent.parent.parent))
        except ValueError:  # pragma: no cover
            return str(self.path)

    def to_dict(self) -> dict:
        return json.loads(json.dumps(self.raw, ensure_ascii=False))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Case {self.case_id} {self.raw.get('domain')}/{self.raw.get('priority')}>"


class Suite:
    __slots__ = ("raw", "path")

    def __init__(self, raw: dict, path: Path):
        self.raw = raw
        self.path = path

    @property
    def suite_id(self) -> str:
        return str(self.raw.get("suite_id") or "")

    @property
    def domain(self) -> str:
        return str(self.raw.get("domain") or "")

    def cases(self) -> list:
        return [Case(item, self.path, self.suite_id) for item in (self.raw.get("cases") or [])]


def iter_case_files() -> list:
    if not CASES_DIR.exists():
        return []
    return sorted(CASES_DIR.rglob("*.json"))


def load_suites(domain: str = "") -> list:
    """加载全部套件；``domain`` 非空时只加载该 domain 目录。"""
    suites = []
    for path in iter_case_files():
        if domain and path.parent.name != domain:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path}: JSON 解析失败：{exc}") from exc
        errors = validate(raw, load_schema("suite.schema.json"), path=str(path))
        if errors:
            raise DatasetError(f"{path}: 套件不符合 suite.schema.json：\n  - " + "\n  - ".join(errors))
        suites.append(Suite(raw, path))
    return suites


def load_cases(domain: str = "", priority: str = "", layer: str = "", case_id: str = "") -> list:
    """加载案例并施加过滤（过滤条件为空表示不过滤）。"""
    out = []
    for suite in load_suites(domain):
        for case in suite.cases():
            if priority and case.get("priority") != priority:
                continue
            if layer and case.get("layer") != layer:
                continue
            if case_id and case.case_id != case_id:
                continue
            out.append(case)
    return out


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
def iter_fixture_files() -> list:
    if not FIXTURES_DIR.exists():
        return []
    return sorted(p for p in FIXTURES_DIR.rglob("*") if p.is_file() and p.suffix != ".pyc")


def load_fixtures() -> dict:
    """返回 ``{"quote/legacy_quote_case.json": <解析后的对象或原文>}``。"""
    out = {}
    for path in iter_fixture_files():
        rel = str(path.relative_to(FIXTURES_DIR))
        if path.suffix == ".json":
            out[rel] = json.loads(path.read_text(encoding="utf-8"))
        else:
            out[rel] = path.read_text(encoding="utf-8")
    return out


def fixture_exists(ref: str) -> bool:
    ref = str(ref or "").strip()
    if not ref or ".." in ref:
        return False
    return (FIXTURES_DIR / ref).is_file()


def walk_strings(node, key: str = ""):
    """深度遍历，产出 ``(key, string)`` 对（用于旧口径文案与浮点金额扫描）。"""
    if isinstance(node, dict):
        for sub_key, value in node.items():
            yield from walk_strings(value, sub_key)
    elif isinstance(node, list):
        for item in node:
            yield from walk_strings(item, key)
    elif isinstance(node, str):
        yield key, node


def fixture_refs(case) -> list:
    """收集案例里引用的 fixture 相对路径（键名：fixture / fixtures / document(s) / provider）。"""
    refs = []

    def _collect(node, parent_key=""):
        if isinstance(node, dict):
            for key, value in node.items():
                _collect(value, key)
        elif isinstance(node, list):
            for item in node:
                _collect(item, parent_key)
        elif isinstance(node, str) and parent_key in FIXTURE_REF_KEYS and node.endswith(".json"):
            refs.append(node)

    _collect(case.raw if isinstance(case, Case) else case)
    return refs


def iter_expected_strings(case) -> list:
    """``expected`` 里的全部字符串（旧口径与浮点金额检查用）。"""
    return [text for _key, text in walk_strings(case.get("expected") or {})]

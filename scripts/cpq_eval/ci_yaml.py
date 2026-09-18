# -*- coding: utf-8 -*-
"""极简 YAML 子集解析器：只用于**结构化**读取仓库的 `.gitlab-ci.yml`。

为什么不直接用 PyYAML：evaluation 只能跑在标准库上（CI 的 fast gate 装了零依赖），
而 CI 防伪测试又**不能只判断字符串出现**，必须真的解析出 job / script / services /
variables / rules 结构。本模块只实现 `.gitlab-ci.yml` 用到的 YAML 子集：

  · 缩进映射：``key: value`` / ``key:`` + 缩进块；
  · 缩进序列：``- value`` / ``- key: value`` / ``-`` 后接缩进映射；
  · 块标量：``key: |``（保留内部换行，用于 heredoc）；
  · 行内序列：``[a, b]`` 与 ``{a: b}`` 的退化形式；
  · 单/双引号标量与 ``#`` 整行注释。

不支持的 YAML 特性（锚点 / 多文档 / 复杂 flow）会抛 ``CiYamlError``，**不静默猜测**。
"""
from __future__ import annotations


class CiYamlError(Exception):
    """解析失败：宁可报错也不返回一个似是而非的结构。"""


def _strip_comment(line: str) -> str:
    out, quote = [], ""
    for index, char in enumerate(line):
        if quote:
            out.append(char)
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
            out.append(char)
            continue
        if char == "#" and (index == 0 or line[index - 1] in " \t"):
            break
        out.append(char)
    return "".join(out).rstrip()


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _scalar(text: str):
    text = text.strip()
    if text == "":
        return ""
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    if text.startswith("[") and text.endswith("]"):
        body = text[1:-1].strip()
        return [_scalar(part) for part in body.split(",")] if body else []
    if text.startswith("{") and text.endswith("}"):
        body = text[1:-1].strip()
        out = {}
        if body:
            for part in body.split(","):
                key, _, value = part.partition(":")
                out[_scalar(key)] = _scalar(value)
        return out
    if text in ("true", "True"):
        return True
    if text in ("false", "False"):
        return False
    if text in ("null", "~"):
        return None
    try:
        return int(text)
    except ValueError:
        return text


def _parse_block(lines, index, indent):
    """解析一个缩进层级；返回 ``(value, next_index)``。"""
    if index >= len(lines):
        return {}, index
    if lines[index].lstrip().startswith("- "):
        return _parse_sequence(lines, index, indent)
    if lines[index].strip() == "-":
        return _parse_sequence(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_sequence(lines, index, indent):
    items = []
    while index < len(lines):
        raw = lines[index]
        if not raw.strip():
            index += 1
            continue
        current = _indent_of(raw)
        if current < indent:
            break
        stripped = raw.strip()
        if current != indent or not (stripped == "-" or stripped.startswith("- ")):
            break
        payload = stripped[1:].strip()
        if not payload:
            value, index = _parse_block(lines, index + 1, _next_indent(lines, index + 1))
            items.append(value)
            continue
        if payload in ("|", ">"):
            index += 1
            block = []
            while index < len(lines) and (not lines[index].strip()
                                          or _indent_of(lines[index]) > indent):
                text = lines[index]
                block.append(text[indent + 2:] if len(text) > indent + 2 else text.strip())
                index += 1
            if payload == "|":
                items.append("\n".join(block).rstrip("\n"))
            else:
                items.append(" ".join(part for part in block if part))
            continue
        if ":" in payload and not payload.startswith(("\"", "'")):
            # `- key: value`：可能的行内映射，后面可能还有同级 key
            key, _, rest = payload.partition(":")
            item = {}
            if rest.strip() == "":
                value, index = _parse_block(lines, index + 1, _next_indent(lines, index + 1))
                item[key.strip()] = value
            else:
                item[key.strip()] = _scalar(rest)
                index += 1
            # 后续同 item 的兄弟 key（缩进 > indent）
            while index < len(lines) and lines[index].strip() and _indent_of(lines[index]) > indent:
                more_indent = _indent_of(lines[index])
                if lines[index].lstrip().startswith("- "):
                    break
                key2, _, rest2 = lines[index].strip().partition(":")
                if rest2.strip() == "":
                    value2, index = _parse_block(lines, index + 1, _next_indent(lines, index + 1))
                else:
                    value2, index = _scalar(rest2), index + 1
                item[key2.strip()] = value2
                del more_indent
            items.append(item)
            continue
        items.append(_scalar(payload))
        index += 1
    return items, index


def _next_indent(lines, index):
    while index < len(lines):
        if lines[index].strip():
            return _indent_of(lines[index])
        index += 1
    return 0


def _parse_mapping(lines, index, indent):
    out = {}
    while index < len(lines):
        raw = lines[index]
        if not raw.strip():
            index += 1
            continue
        current = _indent_of(raw)
        if current < indent:
            break
        if current > indent:
            raise CiYamlError(f"第 {index + 1} 行缩进异常：{raw!r}")
        stripped = raw.strip()
        if stripped.startswith("- "):
            break
        key, sep, rest = stripped.partition(":")
        if not sep:
            raise CiYamlError(f"第 {index + 1} 行不是 key: value：{raw!r}")
        key = key.strip()
        rest = rest.strip()
        if rest == "|":
            index += 1
            block = []
            while index < len(lines) and (not lines[index].strip()
                                          or _indent_of(lines[index]) > indent):
                block.append(lines[index][indent + 2:] if len(lines[index]) > indent + 2
                             else lines[index].strip())
                index += 1
            out[key] = "\n".join(block).rstrip("\n")
        elif rest == ">":
            index += 1
            block = []
            while index < len(lines) and (not lines[index].strip()
                                          or _indent_of(lines[index]) > indent):
                block.append(lines[index].strip())
                index += 1
            out[key] = " ".join(part for part in block if part)
        elif rest == "":
            value, index = _parse_block(lines, index + 1, _next_indent(lines, index + 1))
            out[key] = value
        else:
            out[key] = _scalar(rest)
            index += 1
    return out, index


def loads(text: str):
    lines = [_strip_comment(line) for line in str(text).splitlines()]
    value, index = _parse_block(lines, 0, _next_indent(lines, 0))
    while index < len(lines):
        if lines[index].strip():
            raise CiYamlError(f"第 {index + 1} 行未解析：{lines[index]!r}")
        index += 1
    return value


def load(path):
    with open(path, encoding="utf-8") as handle:
        return loads(handle.read())


RESERVED_TOP_LEVEL = ("stages", "workflow", "default", "variables", "include")


def jobs(doc: dict) -> dict:
    """顶层里除保留键与 `.hidden` 之外的都是 job。"""
    out = {}
    for name, value in (doc or {}).items():
        if name in RESERVED_TOP_LEVEL or str(name).startswith("."):
            continue
        if isinstance(value, dict):
            out[name] = value
    return out


def job_script(job: dict) -> list:
    """job 的 script（含 before_script），归一成字符串列表。"""
    out = []
    for key in ("before_script", "script"):
        value = (job or {}).get(key)
        if isinstance(value, list):
            out.extend(str(item) for item in value)
        elif value:
            out.append(str(value))
    return out


def job_runs_command(job: dict, needle: str) -> bool:
    return any(needle in line for line in job_script(job))

# -*- coding: utf-8 -*-
"""唯一的 JS 函数体抠取实现（发布保障批次 2，Spec §2.2）。

为什么要有它：`tests/` 里至少 17 份各自实现的「找 `function <name>(`，再从 `{` 数括号到 0」，
那种写法不区分字符串 / 注释 / 模板串 / 正则 —— 函数签名或函数体里出现一个 `}` 字面量就会抠错，
进而让多个模块集体报红。这类红不是业务回归，却会淹没真实回归（Spec §1.2）。

本模块只提供**一个**稳健实现，不改既有 17 份私有实现；后继批次再逐个迁移过来。
`function_body(source, name)` 找不到函数名时返回空串，**不抛异常**。
"""
from __future__ import annotations

import re

__all__ = ["function_body"]

#: JS 里 `/` 之前若是这些「非值」字符，则 `/` 是正则字面量的开头而不是除号。
_REGEX_PREFIX = set("([{,;:=!&|?+-*%^~<>")


def _skip_line_comment(source: str, index: int) -> int:
    """`index` 指向 `//` 的第一个斜杠，返回换行之后的索引（无换行则到文件末尾）。"""
    end = source.find("\n", index)
    return len(source) if end < 0 else end + 1


def _skip_block_comment(source: str, index: int) -> int:
    """`index` 指向 `/*` 的第一个斜杠，返回 `*/` 之后的索引（未闭合则到文件末尾）。"""
    end = source.find("*/", index + 2)
    return len(source) if end < 0 else end + 2


def _skip_quoted(source: str, index: int) -> int:
    """`index` 指向单/双引号，返回闭引号之后的索引（处理 `\\` 转义）。"""
    quote = source[index]
    cursor = index + 1
    length = len(source)
    while cursor < length:
        char = source[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == quote:
            return cursor + 1
        cursor += 1
    return length


def _skip_braced(source: str, index: int) -> int:
    """`index` 指向 `{`，返回与它配对的 `}` 之后的索引（用于模板串里的 `${ ... }`）。"""
    depth = 0
    cursor = index
    length = len(source)
    while cursor < length:
        char = source[cursor]
        if char in "\"'":
            cursor = _skip_quoted(source, cursor)
            continue
        if char == "`":
            cursor = _skip_template(source, cursor)
            continue
        if char == "/" and cursor + 1 < length and source[cursor + 1] == "/":
            cursor = _skip_line_comment(source, cursor)
            continue
        if char == "/" and cursor + 1 < length and source[cursor + 1] == "*":
            cursor = _skip_block_comment(source, cursor)
            continue
        if char == "{":
            depth += 1
            cursor += 1
            continue
        if char == "}":
            depth -= 1
            cursor += 1
            if depth == 0:
                return cursor
            continue
        cursor += 1
    return length


def _skip_template(source: str, index: int) -> int:
    """`index` 指向反引号，返回闭反引号之后的索引；`${...}` 里的括号不算外层。"""
    cursor = index + 1
    length = len(source)
    while cursor < length:
        char = source[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "`":
            return cursor + 1
        if char == "$" and cursor + 1 < length and source[cursor + 1] == "{":
            cursor = _skip_braced(source, cursor + 1)
            continue
        cursor += 1
    return length


def _skip_regex(source: str, index: int) -> int:
    """`index` 指向正则字面量的开 `/`，返回闭 `/` 之后的索引（字符类里的 `/` 不算结尾）。"""
    cursor = index + 1
    length = len(source)
    in_class = False
    while cursor < length:
        char = source[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "\n":
            break
        if char == "[":
            in_class = True
        elif char == "]":
            in_class = False
        elif char == "/" and not in_class:
            return cursor + 1
        cursor += 1
    return length


def _scan(source: str, index: int, opener: str, closer: str) -> int:
    """从 `index`（指向 `opener`）起找配对的 `closer`，跳过字符串 / 注释 / 模板串 / 正则。

    只计 `opener` / `closer` 这一对括号：其它括号原样跳过 —— 它们不可能闭合这一对。
    """
    depth = 0
    cursor = index
    length = len(source)
    prev = ""
    while cursor < length:
        char = source[cursor]
        if char in "\"'":
            cursor = _skip_quoted(source, cursor)
            continue
        if char == "`":
            cursor = _skip_template(source, cursor)
            continue
        if char == "/":
            nxt = source[cursor + 1] if cursor + 1 < length else ""
            if nxt == "/":
                cursor = _skip_line_comment(source, cursor)
                continue
            if nxt == "*":
                cursor = _skip_block_comment(source, cursor)
                continue
            if prev == "" or prev in _REGEX_PREFIX:
                cursor = _skip_regex(source, cursor)
                continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return cursor
        if not char.isspace():
            prev = char
        cursor += 1
    return -1


def function_body(source: str, name: str) -> str:
    """抠出 `function <name>(...) { ... }` 的函数体（不含最外层花括号）。

    正确处理：嵌套 `{}`、字符串里的 `{}`、模板串与 `${...}`、行/块注释、正则字面量。
    找不到函数名（或签名/函数体不完整）→ 返回 `""`，不抛异常。
    """
    if not isinstance(source, str) or not name:
        return ""
    match = re.search(r"function\s+" + re.escape(str(name)) + r"\s*\(", source)
    if not match:
        return ""
    paren_close = _scan(source, match.end() - 1, "(", ")")
    if paren_close < 0:
        return ""
    brace_open = source.find("{", paren_close + 1)
    if brace_open < 0:
        return ""
    brace_close = _scan(source, brace_open, "{", "}")
    if brace_close < 0:
        return ""
    return source[brace_open + 1:brace_close]

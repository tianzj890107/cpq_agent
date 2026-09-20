"""受控表达式求值器 —— 包装第 5 批。

Spec：docs/specs/packaging-parametric-bom.md §2.3
红测：tests/test_packaging_parametric_bom_red.py（A 组）

31 条包装部件模板里的 ``L+4t+2c``、``（L-4）×（W-4）× 8``、``长度 = W/3 + 40``
需要算成尺寸，但**绝不允许执行任意代码**：本模块自己写词法/语法分析，字符、函数、
结构三层白名单，白名单外一律抛 :class:`FormulaError`；没有反射、没有动态求值入口、
不联网、不起进程。

口径（Spec §2.3，不得自行放宽）：
  · 允许：数字、变量名（英文/中文）、``+ - * / ( )``、比较运算符（只用于 ``IF`` 条件）、
    函数 ``MIN`` ``MAX`` ``IF`` ``IFERROR`` ``ROUND``；隐式乘法 ``4t`` 按 ``4*t`` 算；
  · 归一化只做全角符号替换、去掉 ``X =`` 单变量赋值前缀、去掉 ``3mm`` 这类纯单位后缀、
    连续空白压成一个空格；
  · 除零抛错（``IFERROR`` 才能兜住）；引用未绑定变量抛错并带上 ``missing_variables``；
  · 结果保留 1 位小数，**半上进位**（``0.25 → 0.3``）。
"""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Optional


#: 白名单函数闭集（Spec §2.3 / §4.5）。
ALLOWED_FUNCTIONS = frozenset({"MIN", "MAX", "IF", "IFERROR", "ROUND"})

#: 全角/异体符号归一化表（只做这些替换）。
_SYMBOLS = {
    "（": "(", "）": ")", "，": ",", "×": "*", "÷": "/", "－": "-",
}

#: 纯单位后缀：``出血3mm`` → ``出血3``。只吃紧跟在数字后面的单位。
_UNIT_PATTERN = r"(?<=\d)\s*(?:mm|cm|m㎡|㎡|gsm|kg|g|%)"

#: 单变量赋值前缀：``长度 = W/3 + 40`` → ``W/3 + 40``；``>=`` ``<=`` ``==`` ``<>`` 不算。
_ASSIGN_PATTERN = r"^([A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*)\s*=(?!=)\s*"

_NUMBER_PATTERN = r"\d+(?:\.\d+)?"
_NAME_PATTERN = r"[A-Za-z_\u4e00-\u9fff][A-Za-z0-9_\u4e00-\u9fff]*"

#: 字符白名单之外的任何字符都不许出现（引号、下标、点、分号、换行…）。
_ALLOWED_CHARS = frozenset(" \t+-*/()<>=,")

_CMP_TOKENS = ("<=", ">=", "<>", "==", "=", "<", ">")


class FormulaError(Exception):
    """表达式错误；引用未绑定变量时 ``missing_variables`` 带上变量名（按出现顺序）。"""

    def __init__(self, message: str, missing_variables: Optional[Iterable[str]] = None):
        super().__init__(message)
        self.message = str(message)
        self.missing_variables = list(missing_variables or [])


def normalize_expression(text: str) -> str:
    """按 Spec §2.3 归一化：只做符号替换、赋值前缀、单位后缀、空白压缩。"""
    out = "" if text is None else str(text)
    for src, dst in _SYMBOLS.items():
        out = out.replace(src, dst)
    out = re.sub(_ASSIGN_PATTERN, "", out)
    out = re.sub(_UNIT_PATTERN, "", out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip()


def _half_up(value: float, digits: int) -> float:
    """半上进位到 ``digits`` 位小数（``0.25 → 0.3``；Python 内置 round 是银行家进位）。"""
    step = Decimal(1).scaleb(-int(digits))
    return float(Decimal(repr(float(value))).quantize(step, rounding=ROUND_HALF_UP))


def _guard_characters(text: str) -> None:
    if "**" in text:
        raise FormulaError("不允许幂运算：%r" % text)
    for position, char in enumerate(text):
        if char in _ALLOWED_CHARS:
            continue
        if char.isalnum():
            continue
        raise FormulaError("表达式含白名单外字符 %r（位置 %d）：%r" % (char, position, text))
    # 小数点只允许出现在数字里：``L.__doc__`` 的 ``.`` 必须被拒。
    for match in re.finditer(r"\.", text):
        before = text[match.start() - 1] if match.start() else ""
        after = text[match.end()] if match.end() < len(text) else ""
        if not (before.isdigit() and after.isdigit()):
            raise FormulaError("表达式含白名单外字符 '.'：%r" % text)


def _tokenize(text: str) -> list[tuple[str, Any]]:
    tokens: list[tuple[str, Any]] = []
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char in " \t":
            index += 1
            continue
        match = re.match(_NUMBER_PATTERN, text[index:])
        if match:
            tokens.append(("num", float(match.group(0))))
            index += match.end()
            continue
        match = re.match(_NAME_PATTERN, text[index:])
        if match:
            tokens.append(("name", match.group(0)))
            index += match.end()
            continue
        for token in _CMP_TOKENS:
            if text.startswith(token, index):
                tokens.append(("cmp", token))
                index += len(token)
                break
        else:
            if char in "+-*/(),":
                tokens.append(("op", char))
                index += 1
                continue
            raise FormulaError("无法解析的字符 %r：%r" % (char, text))
    return tokens


class _Parser:
    """递归下降：cmp → add → mul（含隐式乘法）→ unary → primary。"""

    def __init__(self, tokens: list[tuple[str, Any]]):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Optional[tuple[str, Any]]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> tuple[str, Any]:
        token = self.peek()
        if token is None:
            raise FormulaError("表达式意外结束")
        self.pos += 1
        return token

    def parse(self):
        node = self.cmp()
        if self.peek() is not None:
            raise FormulaError("表达式多余的部分：%r" % (self.peek(),))
        return node

    def cmp(self):
        node = self.add()
        token = self.peek()
        if token and token[0] == "cmp":
            self.take()
            right = self.add()
            return ("cmp", token[1], node, right)
        return node

    def add(self):
        node = self.mul()
        while True:
            token = self.peek()
            if token and token[0] == "op" and token[1] in "+-":
                self.take()
                node = ("bin", token[1], node, self.mul())
                continue
            return node

    def mul(self):
        node = self.unary()
        while True:
            token = self.peek()
            if token and token[0] == "op" and token[1] in "*/":
                self.take()
                node = ("bin", token[1], node, self.unary())
                continue
            if token and (token[0] in ("num", "name")
                          or (token[0] == "op" and token[1] == "(")):
                # 隐式乘法：``4t`` 写成 ``4*t``、``2(L+1)`` 写成 ``2*(L+1)``。
                node = ("bin", "*", node, self.unary())
                continue
            return node

    def unary(self):
        token = self.peek()
        if token and token[0] == "op" and token[1] in "+-":
            self.take()
            return ("unary", token[1], self.unary())
        return self.primary()

    def primary(self):
        token = self.take()
        kind, value = token
        if kind == "num":
            return ("num", value)
        if kind == "op" and value == "(":
            node = self.cmp()
            closing = self.take()
            if closing != ("op", ")"):
                raise FormulaError("括号没有闭合")
            return node
        if kind == "name":
            if self.peek() == ("op", "("):
                self.take()
                args = []
                if self.peek() == ("op", ")"):
                    self.take()
                else:
                    while True:
                        args.append(self.cmp())
                        nxt = self.take()
                        if nxt == ("op", ")"):
                            break
                        if nxt != ("op", ","):
                            raise FormulaError("函数参数之间必须是逗号")
                return ("call", value, args)
            return ("var", value)
        raise FormulaError("表达式里出现了意外的记号 %r" % (value,))


def _names_of(node) -> list[str]:
    """按出现顺序收集表达式里引用的变量名（函数名不算）。"""
    kind = node[0]
    if kind == "var":
        return [node[1]]
    if kind == "num":
        return []
    if kind == "call":
        names: list[str] = []
        for arg in node[2]:
            names.extend(_names_of(arg))
        return names
    if kind == "unary":
        return _names_of(node[2])
    if kind in ("bin", "cmp"):
        return _names_of(node[2]) + _names_of(node[3])
    return []


def _truthy(value: float) -> bool:
    return bool(value)


def _apply_cmp(op: str, left: float, right: float) -> float:
    if op == ">":
        result = left > right
    elif op == "<":
        result = left < right
    elif op == ">=":
        result = left >= right
    elif op == "<=":
        result = left <= right
    elif op in ("=", "=="):
        result = left == right
    elif op == "<>":
        result = left != right
    else:                                                # pragma: no cover - 记号已闭集
        raise FormulaError("不支持的比较运算符 %r" % op)
    return 1.0 if result else 0.0


def _call(name: str, args, variables: dict) -> float:
    if name not in ALLOWED_FUNCTIONS:
        raise FormulaError("白名单外的函数：%r" % name)
    if name == "IF":
        if len(args) != 3:
            raise FormulaError("IF 需要 3 个参数")
        cond = _calc(args[0], variables)
        return _calc(args[1] if _truthy(cond) else args[2], variables)
    if name == "IFERROR":
        if len(args) != 2:
            raise FormulaError("IFERROR 需要 2 个参数")
        try:
            return _calc(args[0], variables)
        except (FormulaError, ArithmeticError, ValueError, TypeError):
            return _calc(args[1], variables)
    values = [_calc(arg, variables) for arg in args]
    if not values:
        raise FormulaError("%s 至少需要 1 个参数" % name)
    if name == "MIN":
        return min(values)
    if name == "MAX":
        return max(values)
    if name == "ROUND":
        digits = int(values[1]) if len(values) > 1 else 0
        return _half_up(values[0], digits)
    raise FormulaError("白名单外的函数：%r" % name)        # pragma: no cover - 闭集兜底


def _calc(node, variables: dict) -> float:
    kind = node[0]
    if kind == "num":
        return float(node[1])
    if kind == "var":
        name = node[1]
        if name not in variables:
            raise FormulaError("引用了未绑定的变量：%s" % name, [name])
        return float(variables[name])
    if kind == "call":
        return _call(node[1], node[2], variables)
    if kind == "unary":
        value = _calc(node[2], variables)
        return -value if node[1] == "-" else value
    if kind == "bin":
        left = _calc(node[2], variables)
        right = _calc(node[3], variables)
        op = node[1]
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            if right == 0:
                raise FormulaError("除以零")
            return left / right
        raise FormulaError("不支持的运算符 %r" % op)      # pragma: no cover - 闭集兜底
    if kind == "cmp":
        return _apply_cmp(node[1], _calc(node[2], variables), _calc(node[3], variables))
    raise FormulaError("表达式结构不合法")                 # pragma: no cover - 闭集兜底


def evaluate(expression: str, variables: dict, *, precision: int = 1) -> float:
    """受控求值：白名单外的字符/函数/结构一律抛 :class:`FormulaError`，绝不执行。"""
    raw = "" if expression is None else str(expression)
    if "\n" in raw or "\r" in raw:
        raise FormulaError("表达式不允许换行：%r" % raw)
    _guard_characters(raw.replace("（", "(").replace("）", ")").replace("，", ","))
    normalized = normalize_expression(raw)
    tokens = _tokenize(normalized)
    if not tokens:
        raise FormulaError("表达式为空")
    node = _Parser(tokens).parse()
    bound = variables or {}
    missing: list[str] = []
    for name in _names_of(node):
        if name not in bound and name not in missing:
            missing.append(name)
    if missing:
        raise FormulaError("引用了未绑定的变量：%s" % "、".join(missing), missing)
    return _half_up(_calc(node, bound), precision)

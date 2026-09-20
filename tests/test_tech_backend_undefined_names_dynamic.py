"""后端 undefined-name 直连回归（“静态测试看不见、一发请求就 500”类缺陷的守卫）。

背景（本文件建立时的实测结论）：
  · 1.2/1.3 需求确认/审核批次里，`tech_app/backend/main.py` 对 `requirement_service`
    有 11 处引用，却从未 `from .services import (...)` 导入它 —— 纯文本 grep 型红测
    （只断言“路由里出现了某个函数名”）全部通过，真实请求却直接 500；
  · 同一批排查还发现 `recommend_costest`（`POST /costest/recommend`）从 `566b1c0`
    起就漏掉了 `dependency_hash = _digest_value(...)`（其余 10 个 recommend 兄弟
    路由都有），同样是即发 500。

两类缺陷的共同点：`python -m py_compile` / `node --check` 只看语法，文本 grep 只看
字符串，都抓不到“函数体里读了一个 never-bound 的全局名”。本文件用 stdlib 的
`symtable` 把这件事做成回归断言（等价于 pyflakes 的 `undefined name` 那一条诊断），
零第三方依赖，所以在系统解释器和 open-claude/.venv 里都真跑、不会 skip。
"""
from __future__ import annotations

import ast
import builtins
import symtable
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND_FILES = sorted((ROOT / "tech_app" / "backend").rglob("*.py"))
ROOT_SERVER_FILES = sorted(ROOT.glob("*.py"))

# 解释器 / 导入机制隐式写入的模块级名字，不能算“未定义”。
IMPLICIT_MODULE_NAMES = {
    "__file__", "__name__", "__doc__", "__builtins__", "__spec__", "__loader__",
    "__package__", "__debug__", "__class__", "__dict__", "__path__", "__all__",
}


def find_undefined_names(source: str, filename: str = "<string>") -> list[str]:
    """返回形如 ``filename:scope:name`` 的“引用了 never-bound 全局名”清单。

    判定规则与 pyflakes 的 undefined-name 对齐：某个作用域里 `is_global()` 且
    `is_referenced()`、却在该作用域内 `not is_assigned()`，同时又不属于模块级符号
    或内置名的，就是可疑的未定义引用。
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        return [f"{filename}:<module>:SYNTAX {exc}"]
    # `from x import *` 会让模块级符号表变得不确定，此时保守跳过。
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            return []

    top = symtable.symtable(source, filename, "exec")
    module_names = {sym.get_name() for sym in top.get_symbols()}
    found: list[str] = []

    def walk(table: symtable.SymbolTable) -> None:
        for sym in table.get_symbols():
            name = sym.get_name()
            if name in IMPLICIT_MODULE_NAMES or name in module_names or hasattr(builtins, name):
                continue
            if sym.is_referenced() and sym.is_global() and not sym.is_assigned():
                found.append(f"{filename}:{table.get_name()}:{name}")
        for child in table.get_children():
            walk(child)

    walk(top)
    return found


class UndefinedNameScannerSelfTest(unittest.TestCase):
    """先证明扫描器真的会报，防止它退化成永远返回空列表。"""

    def test_flags_unbound_global_reference(self):
        hits = find_undefined_names("def route():\n    return helper(1)\n", "sample.py")
        self.assertTrue(any(h.endswith(":helper") for h in hits), hits)

    def test_flags_name_bound_only_in_a_sibling_function(self):
        # 正是 recommend_costest 的形状：别的函数里有赋值，这个函数里只读。
        hits = find_undefined_names(
            "def a():\n    shared = 1\n    return shared\n\n"
            "def b():\n    return shared + 1\n",
            "sample.py",
        )
        self.assertTrue(any(h.endswith(":b:shared") for h in hits), hits)

    def test_ignores_locals_builtins_and_module_imports(self):
        src = (
            "import os\n"
            "from json import dumps\n"
            "VALUE = 1\n"
            "def route(x):\n"
            "    inner = x + VALUE\n"
            "    return dumps(os.getcwd()) + str(inner)\n"
        )
        self.assertEqual(find_undefined_names(src, "sample.py"), [])

    def test_skips_star_import_modules(self):
        self.assertEqual(find_undefined_names("from os import *\nthing()\n", "sample.py"), [])


class BackendUndefinedNameTest(unittest.TestCase):
    def test_scan_covers_expected_modules(self):
        names = {path.name for path in BACKEND_FILES}
        self.assertIn("main.py", names)
        self.assertIn("oc_agent.py", names)
        self.assertIn("requirement_service.py", names)
        # 空 glob 不能让下面那条断言vacuous通过。
        self.assertGreater(len(BACKEND_FILES), 40)

    def test_no_undefined_names_in_backend_or_root_servers(self):
        targets = [*BACKEND_FILES, *ROOT_SERVER_FILES]
        self.assertTrue(targets)
        findings: list[str] = []
        for path in targets:
            rel = path.relative_to(ROOT).as_posix()
            findings += find_undefined_names(path.read_text(encoding="utf-8"), rel)
        self.assertEqual(
            findings, [],
            "以下位置读了一个从未绑定的全局名，真实请求会 NameError → 500：\n  "
            + "\n  ".join(findings),
        )


if __name__ == "__main__":
    unittest.main()

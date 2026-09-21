"""红测：报价版本必须有生产入口（卡片第 5 步落版本 + 第 5 步读回历史）。

Spec：`docs/specs/packaging-quote-version-persistence.md`

现状缺口（代码级，可复现）：
  · `grep -rn "save_version(" cpq_*.py tech_app -g "*.py"` 在生产代码里只命中它自己的定义
    （`cpq_packaging_quote.py:682`）—— `cpq_wf.py` / `cpq_suite_server.py` 里 0 次，
    所以 `cpq_wf_quote_version` 永远空，第 8 批 §2.6 的版本不变式没有生产入口；
  · 卡片第 5 步的 `data_snapshot` 是同名覆盖，重算成本再确认第 5 步就把上一次报价盖掉了；
  · `GET /wf/card/step-data?step_no=5` 只返回一份现值，没有版本列表可读；
  · 报价页没有渲染版本列表的键（`packaging_quote_versions`）。

纪律：只读源码与签名，不连 PG、不发 HTTP、不写任何文件。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CARD_HTML = ROOT / "确认需求解析结果.html"
VERSION_TABLE = "cpq_wf_quote_version"
SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", "tests"}


def prod_py_files():
    files = sorted(ROOT.glob("cpq_*.py")) + sorted((ROOT / "tech_app").rglob("*.py"))
    return [p for p in files if not (SKIP_DIRS & set(p.parts))]


def read(path):
    return pathlib.Path(path).read_text(encoding="utf-8")


def slice_between(text, start, stop_re):
    """从 `start`（含）截到下一条匹配 `stop_re` 的行（不含）。找不到起点返回 ""。"""
    at = text.find(start)
    if at < 0:
        return ""
    rest = text[at:]
    end = re.search(stop_re, rest[len(start):])
    return rest if end is None else rest[:len(start) + end.start()]


def func_source(rel_path, name):
    src = read(ROOT / rel_path)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(src.splitlines()[node.lineno - 1:node.end_lineno])
    return ""


def call_sites(name):
    """生产代码里 `name(` 的位置（跳过它自己的定义所在行）。"""
    pattern = re.compile(r"(?<![\w.])" + re.escape(name) + r"\(")
    hits = []
    for path in prod_py_files():
        for no, line in enumerate(read(path).splitlines(), 1):
            if pattern.search(line) and not line.strip().startswith(("def ", "async def ")):
                hits.append("%s:%d: %s" % (path.relative_to(ROOT), no, line.strip()))
    return hits


class AProductionEntryPoint(unittest.TestCase):
    def test_a1_save_version_has_a_production_call_site(self):
        hits = call_sites("save_version")
        self.assertTrue(hits, "生产代码里没有任何 save_version() 调用点：报价版本表永远空"
                              "（Spec §1.1 / §3.2）")

    def test_a2_call_site_sits_on_the_card_step_five_path(self):
        path = (func_source("cpq_wf.py", "complete_step")
                + slice_between(read(ROOT / "cpq_suite_server.py"),
                                'elif path == "/wf/card/step-done"', r"\n\s+elif "))
        self.assertTrue(path.strip(), "取不到卡片第 5 步的完成路径（cpq_wf.complete_step / "
                                      "/wf/card/step-done）—— 测试前提失效，先修测试")
        self.assertTrue("save_version(" in path,
                        "卡片第 5 步确认时必须在同一事务里落一版（Spec §2.1 / §3.1）")


class BReadBack(unittest.TestCase):
    def test_b1_read_path_never_writes_versions(self):
        branch = slice_between(read(ROOT / "cpq_suite_server.py"),
                               'elif path == "/wf/card/step-data"', r"\n\s+elif ")
        self.assertTrue(branch.strip(), "取不到 /wf/card/step-data 分支 —— 测试前提失效")
        for forbidden in ("save_version(", "INSERT INTO " + VERSION_TABLE):
            self.assertNotIn(forbidden, branch,
                             "读取路径不许写版本（Spec §4）")

    def test_b2_step_five_returns_the_version_list(self):
        branch = slice_between(read(ROOT / "cpq_suite_server.py"),
                               'elif path == "/wf/card/step-data"', r"\n\s+elif ")
        self.assertTrue(branch.strip(), "取不到 /wf/card/step-data 分支 —— 测试前提失效")
        self.assertTrue(
            bool("packaging_quote_versions" in branch
                 or re.search(r"(?<![\w.])(versions|latest)\(", branch)),
            "第 5 步读回必须带出版本列表与最新版本（Spec §2.2 / §3.4）")

    def test_b3_card_page_renders_the_version_list(self):
        html = read(CARD_HTML)
        self.assertTrue("packaging_quote_versions" in html,
                        "报价页必须能渲染历史版本（Spec §2.3 / §3.4）")


class CInvariantsStayFrozen(unittest.TestCase):
    def test_c1_version_table_stays_append_only(self):
        bad = []
        for path in prod_py_files():
            upper = read(path).upper()
            for pattern in ("UPDATE " + VERSION_TABLE.upper(),
                            "DELETE FROM " + VERSION_TABLE.upper()):
                if pattern in upper:
                    bad.append("%s: %s" % (path.relative_to(ROOT), pattern))
        self.assertEqual([], bad, "版本表只增不改（Spec §4）")

    def test_c2_insert_only_happens_inside_save_version_module(self):
        bad = []
        for path in prod_py_files():
            if path.name == "cpq_packaging_quote.py":
                continue
            if "INSERT INTO " + VERSION_TABLE in read(path):
                bad.append(str(path.relative_to(ROOT)))
        self.assertEqual([], bad, "不许绕过 save_version 自己写 INSERT（Spec §4）")

    def test_c3_frozen_signatures_unchanged(self):
        module = __import__("cpq_packaging_quote")
        save = inspect.signature(module.save_version).parameters
        self.assertEqual(["conn", "quote", "user"], list(save),
                         "save_version 签名已冻结（Spec §4）")
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, save["user"].kind)
        self.assertIsNone(save["user"].default)
        for name in ("versions", "latest"):
            params = inspect.signature(getattr(module, name)).parameters
            self.assertEqual(["conn", "business_case_id", "quote_session_id"], list(params),
                             "%s 签名已冻结（Spec §4）" % name)


if __name__ == "__main__":
    unittest.main()

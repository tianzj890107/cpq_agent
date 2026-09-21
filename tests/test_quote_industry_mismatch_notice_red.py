"""红测：报价工作台「行业与需求不一致」的软提示。

Spec：`docs/specs/quote-industry-mismatch-notice.md`

现状缺口（实测）：

  · 现场会话头部行业下拉停在「半导体」，用户输入的是包装需求（数码天地盒 / 盖面纸铜版纸亮膜 /
    灰板 2.5mm），系统**一个字都不说**；
  · `cpq_industries.py` 只有 `INDUSTRY_KEYS` / `DEFAULT_INDUSTRY` / `LEGACY_*`，
    `hasattr(cpq_industries, "INDUSTRY_HINTS")` → False、`industry_hint` → False；
  · `确认需求解析结果.html` 的 `step1Intent()`（`:2311`）只管「信息齐不齐」，
    从不校验需求内容更像哪个行业。

本批边界（Spec §1.1）：**只提示，不自动改行业下拉**。

分组（Spec §5）：A 词表与门槛 4、B 纯函数判定 8、C 服务端与前端接线 4、D 不回归护栏 3。

纪律：
  · 全部离线、确定性、无网络：不连 Postgres、不调模型、不起服务；
  · 只读源码与纯函数，不改服务器配置；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

QUOTE_HTML = ROOT / "确认需求解析结果.html"
SERVER_PY = ROOT / "cpq_agent_server.py"
INDUSTRIES_PY = ROOT / "cpq_industries.py"

import cpq_industries  # noqa: E402
from tech_app.backend.services import industry_templates  # noqa: E402

SERVER_NAME = "cpq_agent_server"

#: 现场会话里的包装需求（用户原文的关键词）。
PACKAGING_TEXT = ("数码天地盒 尺寸（mm）100*90*40；盖面纸 铜版纸，亮膜；底面纸 铜版纸，哑膜；"
                  "盖板材 灰板，厚度 2.5mm；压痕线 刀模；每箱数量 60；常温")
#: 半导体需求（取自 industry_templates 半导体模板的字段词）。
SEMI_TEXT = ("晶圆尺寸 12 英寸；静电吸盘类型 多孔陶瓷；温区数量 3；陶瓷基体材料 Al2O3；"
             "金属基座材质 铝；氦气漏率 1e-9；洁净度等级 Class 100；TTV 平面度 5um")
#: 只命中一个包装特征词 —— 应当被命中门槛挡住（Spec §3.2 第 5 条）。
SINGLE_HIT_TEXT = "灰板 3mm，其余待定"

REQUIRED_GATE = (("max_dimension", "尺寸"),
                 ("application_scope", "应用范围/使用场景"),
                 ("operating_temperature", "工作温度"))

RESULT_KEYS = {"industry", "industry_label", "suggested", "suggested_label", "hits", "text"}


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def body_after(src: str, marker: str, size: int = 2600) -> str:
    i = src.find(marker)
    return "" if i < 0 else src[i:i + size]


class IndustryHintCase(unittest.TestCase):
    maxDiff = None

    def hint(self, text, industry=None, **kw):
        fn = getattr(cpq_industries, "industry_hint", None)
        self.assertIsNotNone(
            fn,
            "cpq_industries 必须提供纯函数 industry_hint(text, industry)（Spec §3.2）："
            "报价侧现在完全不校验行业是否选对")
        return fn(text, industry, **kw) if kw else fn(text, industry)


# --------------------------------------------------------------------------- #
# A. 词表与门槛配置（Spec §3.1）
# --------------------------------------------------------------------------- #
class AHintConfig(IndustryHintCase):

    def hints(self) -> dict:
        table = getattr(cpq_industries, "INDUSTRY_HINTS", None)
        self.assertIsInstance(
            table, dict,
            "cpq_industries 必须导出 INDUSTRY_HINTS（Spec §3.1）：行业特征词只允许一处事实源")
        return table

    def test_a1_covers_exactly_the_registry_industries(self):
        self.assertEqual(set(self.hints()), set(cpq_industries.INDUSTRY_KEYS),
                         "INDUSTRY_HINTS 的键必须与 INDUSTRY_KEYS 完全一致（Spec §3.1）")

    def test_a2_every_row_has_at_least_eight_clean_unique_words(self):
        for key, words in self.hints().items():
            self.assertGreaterEqual(len(words), 8,
                                    "行业 %s 的特征词太少（Spec §3.1）" % key)
            for word in words:
                self.assertIsInstance(word, str)
                self.assertTrue(word.strip(), "行业 %s 里有空词（Spec §3.1）" % key)
            self.assertEqual(len(set(words)), len(words),
                             "行业 %s 的词不许重复（Spec §3.1）" % key)

    def test_a3_rows_are_not_copies_of_each_other(self):
        rows = {k: frozenset(v) for k, v in self.hints().items()}
        self.assertEqual(len(set(rows.values())), len(rows),
                         "四个行业的词表不许互相复制（Spec §3.1）")

    def test_a4_min_hits_threshold_is_configurable(self):
        value = getattr(cpq_industries, "INDUSTRY_HINT_MIN_HITS", None)
        self.assertIsInstance(value, int, "必须导出 INDUSTRY_HINT_MIN_HITS（Spec §3.1）")
        self.assertGreaterEqual(value, 2,
                                "命中门槛至少 2，否则单个词就会误报（Spec §3.1）")


# --------------------------------------------------------------------------- #
# B. 纯函数判定（Spec §3.2）
# --------------------------------------------------------------------------- #
class BPureFunction(IndustryHintCase):

    def test_b1_reproduces_the_live_session(self):
        got = self.hint(PACKAGING_TEXT, "semiconductor")
        self.assertIsInstance(got, dict,
                              "包装需求 + 半导体行业 必须给出提示（Spec §3.2）")
        self.assertEqual(got["suggested"], "packaging")
        self.assertEqual(got["industry"], "semiconductor")
        self.assertGreaterEqual(len(got["hits"]), 2)
        words = list(cpq_industries.INDUSTRY_HINTS["packaging"])
        self.assertTrue(set(got["hits"]) <= set(words),
                        "hits 必须都来自该行业的词表（Spec §3.2 第 3 条）")
        order = [words.index(w) for w in got["hits"]]
        self.assertEqual(order, sorted(order), "hits 要按词表顺序，保证稳定（Spec §3.2）")

    def test_b2_text_names_both_industries_and_says_no_auto_switch(self):
        got = self.hint(PACKAGING_TEXT, "semiconductor")
        self.assertIn("包装", got["text"])
        self.assertIn("半导体", got["text"])
        self.assertIn("不会自动切换", got["text"],
                      "提示必须写明不会自动切换行业（Spec §3.2）")

    def test_b3_result_keys_are_exactly_six(self):
        got = self.hint(PACKAGING_TEXT, "semiconductor")
        self.assertEqual(set(got), RESULT_KEYS,
                         "返回键必须恰好是这 6 个（Spec §3.2）：多出 apply/switch 之类的键"
                         "就等于偷偷自动切行业")

    def test_b4_no_hint_when_the_industry_already_matches(self):
        self.assertIsNone(self.hint(PACKAGING_TEXT, "packaging"),
                          "行业已经选对时不许提示（Spec §3.2 第 6 条）")

    def test_b5_no_hint_for_a_matching_semiconductor_requirement(self):
        self.assertIsNone(self.hint(SEMI_TEXT, "semiconductor"))

    def test_b6_flags_semiconductor_text_under_packaging(self):
        got = self.hint(SEMI_TEXT, "packaging")
        self.assertIsInstance(got, dict)
        self.assertEqual(got["suggested"], "semiconductor")

    def test_b7_single_hit_is_below_the_threshold(self):
        self.assertIsNone(
            self.hint(SINGLE_HIT_TEXT, "semiconductor"),
            "只命中 1 个词时不许提示（Spec §3.2 第 5 条：命中门槛）")

    def test_b8_handles_empty_unknown_and_is_pure(self):
        for bad in ("", "   ", None, 123, [], {}):
            self.assertIsNone(self.hint(bad, "semiconductor"),
                              "空值/非字符串一律返回 None（Spec §3.2 第 1 条）：%r" % (bad,))
        # 未知行业键不抛异常，按 DEFAULT_INDUSTRY 处理
        got = self.hint(PACKAGING_TEXT, "not_an_industry")
        self.assertIsInstance(got, dict)
        self.assertEqual(got["industry"], cpq_industries.DEFAULT_INDUSTRY)
        self.assertEqual(got["suggested"], "packaging")
        # 纯函数：不修改任何全局状态，同样输入永远同样输出
        before_hints = {k: tuple(v) for k, v in cpq_industries.INDUSTRY_HINTS.items()}
        before_keys = tuple(cpq_industries.INDUSTRY_KEYS)
        first = self.hint(PACKAGING_TEXT, "semiconductor")
        second = self.hint(PACKAGING_TEXT, "semiconductor")
        self.assertEqual(first, second, "同样输入必须永远同样输出（Spec §3.2）")
        self.assertEqual({k: tuple(v) for k, v in cpq_industries.INDUSTRY_HINTS.items()},
                         before_hints, "industry_hint 不许改全局词表（Spec §3.2）")
        self.assertEqual(tuple(cpq_industries.INDUSTRY_KEYS), before_keys)


# --------------------------------------------------------------------------- #
# C. 服务端与前端接线（Spec §3.3 / §3.4）
# --------------------------------------------------------------------------- #
class CWiring(IndustryHintCase):

    @classmethod
    def setUpClass(cls):
        cls.server_src = read(SERVER_PY)
        cls.html = read(QUOTE_HTML)

    def test_c1_server_returns_industry_hint(self):
        block = body_after(self.server_src, "def _handle_step1_match", 9000)
        self.assertTrue(block, "定位不到 _handle_step1_match")
        self.assertIn("industry_hint", block,
                      "意图识别返回体必须带上 industry_hint（Spec §3.3）")

    def test_c2_gate_result_is_untouched(self):
        block = body_after(self.server_src, "def _handle_step1_match", 9000)
        self.assertIn('"stage": "intent_ok"', block,
                      "提示是旁路，不许改 stage / missing / 门禁结果（Spec §3.3）")

    def test_c3_frontend_shows_the_notice(self):
        block = body_after(self.html, "async function step1Intent", 3000)
        self.assertTrue(block, "定位不到 step1Intent")
        self.assertIn("industry_hint", block,
                      "前端必须把 industry_hint 显示出来（Spec §3.4）")

    def test_c4_frontend_never_flips_the_dropdown(self):
        block = body_after(self.html, "async function step1Intent", 3000)
        self.assertTrue(block, "定位不到 step1Intent")
        for pattern, label in (
                (r"industrySelect'\)\.value\s*=", "给行业下拉赋值"),
                (r"CURRENT_INDUSTRY\s*=", "改写当前行业变量")):
            self.assertIsNone(
                re.search(pattern, block),
                "不许自动改行业（Spec §3.4）：step1Intent 里出现了 %s" % label)


# --------------------------------------------------------------------------- #
# D. 不回归护栏（Spec §4）
# --------------------------------------------------------------------------- #
class DGuards(IndustryHintCase):

    def test_d1_industry_registry_unchanged(self):
        self.assertEqual(cpq_industries.INDUSTRY_KEYS,
                         ("semiconductor", "battery", "appliance", "packaging"))
        self.assertEqual(cpq_industries.DEFAULT_INDUSTRY, "semiconductor")

    def test_d2_gates_unchanged(self):
        import importlib
        try:
            server = importlib.import_module(SERVER_NAME)
        except Exception as exc:                       # noqa: BLE001
            raise AssertionError("无法导入 %s：%s: %s" % (SERVER_NAME, type(exc).__name__, exc))
        for industry in ("semiconductor", "battery", "appliance"):
            self.assertEqual(tuple(server.step1_required(industry)), REQUIRED_GATE,
                             "%s 门禁必须逐字不变（Spec §4）" % industry)
        self.assertEqual(
            {key for key, _label in server.step1_required("packaging")},
            set(industry_templates.required_keys("packaging")))
        self.assertEqual(len(industry_templates.field_keys("packaging")), 64,
                         "包装模板字段数不许变（Spec §4）")

    def test_d3_no_new_dependencies_in_the_registry(self):
        stdlib = set(getattr(sys, "stdlib_module_names", ()))
        local = {p.stem for p in ROOT.glob("cpq_*.py")}
        tree = ast.parse(read(INDUSTRIES_PY))
        imported = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        extra = {name for name in imported if name not in stdlib and name not in local}
        self.assertFalse(extra,
                         "cpq_industries.py 不许引入第三方依赖（Spec §4）：%s" % sorted(extra))


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""红测：报价建单的行业带过去（首页 → 工作台 → 卡片）。

Spec：`docs/specs/quote-home-industry-carryover.md`

现状缺口（实测，不是推断）：

  · `报价首页.html:2506-2509` `confirmProjectAndGo()` 只写 `cpq:projectName` /
    `cpq:projectCode` / `cpq:customer`；`报价首页.html:2535-2552` `goToAssistant()`
    只写 `cpq:requirement` / `cpq:files` / `cpq:fileContents` —— 报价路径**一个字节的行业
    都没传**（技术工艺路径 `报价首页.html:2212-2216` 早就用 `?industry=` 修过同一类问题）。
  · `确认需求解析结果.html` 全文没有 `URLSearchParams`、不读任何行业 storage；
    `CURRENT_INDUSTRY` 初始为空（`:960`），启动只取 `meta.default_industry`（`:4636`），
    再兜 `INDUSTRIES[0]`（`:989`）→ 两者都是 `semiconductor`（`cpq_industries.py:19/25`）。
  · `wfSyncCard()`（`确认需求解析结果.html:2931`）不带 industry；`/wf/card/sync`
    （`cpq_suite_server.py:660-665`）不透传；而 `cpq_wf.sync_card()`（`cpq_wf.py:595`）
    本身支持 `industry=` —— 卡片 industry 一直是 NULL，转技术工艺时行业还会丢。

业务影响：第 1 步必填门禁 / ④产品技术参数换源 / 候选匹配源都按上报行业算
（`确认需求解析结果.html:2352`、`:2395`；后端 `_industry_of()` `cpq_agent_server.py:1145`），
所以包装询盘会按半导体口径走，即 ## 189/191 修过的「包装询盘 Top3 全是锂亚电池」换个入口重现。

纪律：
  · 全部离线：不连 Postgres、不调模型、不起服务、不写业务数据；
  · 前端规则用 `node` **实际执行** `resolveInitialIndustry()` 纯函数（不是文本 grep）；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import inspect
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HOME_HTML = ROOT / "报价首页.html"
WORKBENCH_HTML = ROOT / "确认需求解析结果.html"
SUITE_PY = ROOT / "cpq_suite_server.py"
WF_PY = ROOT / "cpq_wf.py"
INDUSTRY_PY = ROOT / "cpq_industries.py"

import cpq_industries  # noqa: E402
import cpq_wf  # noqa: E402

STORAGE_KEY = "cpq:industry"
KNOWN = ("semiconductor", "battery", "appliance", "packaging")


def read(path):
    return path.read_text(encoding="utf-8")


def extract_function(source, name):
    """按大括号配对取出 `function <name>(…) { … }` 的完整源码；找不到返回空串。"""
    marker = "function %s(" % name
    start = source.find(marker)
    if start < 0:
        return ""
    brace = source.find("{", start)
    if brace < 0:
        return ""
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    return ""


def extract_balanced(source, marker, open_char="(", close_char=")"):
    """取 marker 之后第一对 `()` / `{}` 的内容（用于按路由分支切代码块）。"""
    start = source.find(marker)
    if start < 0:
        return ""
    begin = source.find(open_char, start)
    if begin < 0:
        return ""
    depth = 0
    for index in range(begin, len(source)):
        char = source[index]
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return source[begin + 1:index]
    return ""


class Base(unittest.TestCase):
    maxDiff = None

    def home(self):
        self.assertTrue(HOME_HTML.exists(), "报价首页.html 不存在")
        return read(HOME_HTML)

    def workbench(self):
        self.assertTrue(WORKBENCH_HTML.exists(), "确认需求解析结果.html 不存在")
        return read(WORKBENCH_HTML)

    def check_js(self, script, *, label):
        node = shutil.which("node")
        if not node:
            self.skipTest("本机没有 node，无法校验 JS 语法：%s" % label)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as handle:
            handle.write(script)
            path = handle.name
        done = subprocess.run([node, "--check", path], capture_output=True, text=True)
        self.assertEqual(0, done.returncode, "%s 解析失败：\n%s" % (label, done.stderr))

    def run_node(self, script, *, label):
        node = shutil.which("node")
        if not node:
            self.skipTest("本机没有 node，无法执行前端纯函数：%s" % label)
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                         encoding="utf-8") as handle:
            handle.write(script)
            path = handle.name
        done = subprocess.run([node, path], capture_output=True, text=True)
        self.assertEqual(0, done.returncode,
                         "%s 执行失败：\n%s\n%s" % (label, done.stdout, done.stderr))
        return done.stdout


# --------------------------------------------------------------------------- #
# A 组：首页把行业带走
# --------------------------------------------------------------------------- #
class TestAHomeCarriesIndustry(Base):
    def test_a1_quote_path_stores_industry(self):
        body = extract_function(self.home(), "confirmProjectAndGo")
        self.assertTrue(body, "报价首页.html 里找不到 confirmProjectAndGo()")
        self.assertIn(STORAGE_KEY, body,
                      "报价路径必须把行业写进 sessionStorage['cpq:industry']（Spec §2.2 第 1 条）")
        self.assertRegex(body, r"if\s*\(\s*intent\s*===\s*'quote'\s*\)[\s\S]{0,240}" + re.escape(STORAGE_KEY),
                         "写行业必须挂在该报价分支内")
        self.assertEqual(1, body.count(STORAGE_KEY),
                         "行业只允许在这一处写入（config/rule 路径不得写，Spec §2.2 第 3 条）")

    def test_a2_quote_path_uses_shared_industry_getter(self):
        body = extract_function(self.home(), "confirmProjectAndGo")
        self.assertIn("techIndustry()", body,
                      "取值必须复用首页同一个 techIndustry()，不得另写一份口径（Spec §2.2）")

    def test_a3_quote_url_carries_industry(self):
        body = extract_function(self.home(), "goToAssistant")
        self.assertTrue(body, "报价首页.html 里找不到 goToAssistant()")
        self.assertIn("industry=", body,
                      "跳转报价页的 URL 必须带 ?industry=，否则刷新/直链会退回默认行业"
                      "（Spec §2.2 第 2 条）")
        self.assertIn("encodeURIComponent", body, "URL 参数必须做 encode")

    def test_a4_config_and_rule_paths_do_not_store_industry(self):
        body = extract_function(self.home(), "confirmProjectAndGo")
        self.assertNotIn("cpq:ruleKind', industry", body)
        for key in ("cpq:projectName", "cpq:projectCode", "cpq:customer"):
            self.assertIn(key, body, "既有建单字段不得被本批动掉：%s" % key)

    def test_a5_tech_path_keeps_industry_param(self):
        body = extract_function(self.home(), "techCreateAndGo")
        self.assertIn("industry=", body, "技术工艺路径既有的 ?industry= 不得被改掉（Spec §2.2 第 4 条）")


# --------------------------------------------------------------------------- #
# B 组：工作台取值优先级（真跑 JS）
# --------------------------------------------------------------------------- #
HARNESS = """
const K = %s;
function assertEq(actual, expected, label) {
  if (actual !== expected) {
    console.error(label + ': ' + JSON.stringify(actual) + ' != ' + JSON.stringify(expected));
    process.exit(1);
  }
}
assertEq(resolveInitialIndustry('packaging', 'battery', 'appliance', 'semiconductor', K),
         'packaging', '卡片行业最高优先级');
assertEq(resolveInitialIndustry('', 'battery', 'appliance', 'semiconductor', K),
         'battery', 'URL 优先于 storage');
assertEq(resolveInitialIndustry('', '', 'appliance', 'semiconductor', K),
         'appliance', 'storage 优先于默认');
assertEq(resolveInitialIndustry('', '', '', 'semiconductor', K),
         'semiconductor', '都没有时落默认');
assertEq(resolveInitialIndustry('flexible', 'bogus', '', 'semiconductor', K),
         'semiconductor', '历史键与非法值逐级视为缺失');
assertEq(resolveInitialIndustry('  Packaging  ', '', '', 'semiconductor', K),
         'packaging', '取值前要 trim + 小写');
assertEq(resolveInitialIndustry('', '', '', '', K), '', '默认值原样返回，不编造');
console.log('OK');
""" % json.dumps(list(KNOWN))


class TestBWorkbenchPriority(Base):
    def resolve_source(self):
        source = self.workbench()
        body = extract_function(source, "resolveInitialIndustry")
        self.assertTrue(body,
                        "确认需求解析结果.html 必须新增纯函数 resolveInitialIndustry()"
                        "（Spec §2.3）")
        return source, body

    def test_b1_helper_is_pure_and_runs(self):
        _, body = self.resolve_source()
        for banned in ("document", "sessionStorage", "localStorage", "window.", "fetch("):
            self.assertNotIn(banned, body,
                             "取值规则必须是纯函数，不得直接读全局：%s（Spec §2.3）" % banned)
        self.run_node(body + "\n" + HARNESS, label="resolveInitialIndustry 优先级")

    def test_b2_priority_order_is_card_url_storage_default(self):
        _, body = self.resolve_source()
        order = [body.find("cardIndustry"), body.find("urlIndustry"),
                 body.find("storedIndustry"), body.find("defaultIndustry")]
        for name, index in zip(("cardIndustry", "urlIndustry", "storedIndustry",
                                "defaultIndustry"), order):
            self.assertGreaterEqual(index, 0, "纯函数缺少参数：%s" % name)
        self.assertEqual(order, sorted(order),
                         "优先级必须是 卡片 > URL > storage > 默认（Spec §2.3）")

    def test_b3_page_reads_url_and_storage(self):
        source = self.workbench()
        self.assertIn("URLSearchParams", source, "工作台必须读 URL 上的行业（Spec §2.3）")
        self.assertIn(STORAGE_KEY, source, "工作台必须读/写 sessionStorage['cpq:industry']")

    def test_b4_helper_is_wired_into_boot(self):
        source = self.workbench()
        self.assertIn("resolveInitialIndustry(", source)
        calls = re.findall(r"resolveInitialIndustry\(([^;]{0,400})", source)
        self.assertTrue(any("CURRENT_INDUSTRY" in call or "default_industry" in call
                            for call in calls),
                        "启动流程必须用 resolveInitialIndustry() 的结果给 CURRENT_INDUSTRY 赋值")

    def test_b5_select_change_persists_industry(self):
        source = self.workbench()
        body = extract_balanced(source, "el.onchange")
        self.assertTrue(body, "找不到 industrySelect 的 onchange 处理器")
        self.assertIn(STORAGE_KEY, body,
                      "下拉改行业必须写回 storage，避免下次又退回默认（Spec §2.3）")
        self.assertIn("refreshIndustryForms", body, "改行业后要换 ④产品技术参数 表头")

    def test_b6_new_quote_keeps_industry_memory(self):
        body = extract_function(self.workbench(), "startNewQuote")
        self.assertTrue(body, "找不到 startNewQuote()")
        self.assertNotIn(STORAGE_KEY, body,
                         "「新报价」保留行业记忆（与技术工艺路径一致），不得清掉 cpq:industry"
                         "（Spec §2.3）")
        for key in ("cpq:requirement", "cpq:files", "cpq:fileContents",
                    "cpq:projectName", "cpq:projectCode", "cpq:customer"):
            self.assertIn(key, body, "既有的清理键不得被本批动掉：%s" % key)

    def test_b7_legacy_and_unknown_values_fall_to_default(self):
        _, body = self.resolve_source()
        self.assertIn("toLowerCase", body, "取值口径要与 cpq_industries.normalize 对齐")
        self.assertIn("knownIndustries", body, "合法性必须按服务端下发的清单判定")
        self.assertNotRegex(body, r"'(semiconductor|battery|appliance|packaging)'",
                            "前端不得硬编码行业清单（Spec §2.1）")


# --------------------------------------------------------------------------- #
# C 组：卡片落库与透传
# --------------------------------------------------------------------------- #
class TestCCardPersist(Base):
    def test_c1_wf_sync_card_sends_industry(self):
        body = extract_function(self.workbench(), "wfSyncCard")
        self.assertTrue(body, "找不到 wfSyncCard()")
        self.assertIn("industry", body,
                      "wfSyncCard() 必须把 currentIndustry() 一起提交（Spec §2.4 第 1 条）")
        self.assertIn("currentIndustry()", body, "行业值必须取当前下拉口径")

    def test_c2_suite_server_passes_industry_through(self):
        source = read(SUITE_PY)
        body = extract_balanced(source, 'path == "/wf/card/sync"')
        self.assertTrue(body, "找不到 /wf/card/sync 分支")
        self.assertIn("industry", body,
                      "cpq_suite_server 的 /wf/card/sync 必须把 industry 透传给 "
                      "cpq_wf.sync_card()（Spec §2.4 第 2 条）")

    def test_c3_cpq_wf_sync_card_contract_unchanged(self):
        signature = inspect.signature(cpq_wf.sync_card)
        self.assertIn("industry", signature.parameters,
                      "cpq_wf.sync_card() 的 industry 参数是既有契约，不得删改")
        self.assertEqual("", signature.parameters["industry"].default,
                         "留空的语义是「这次没有行业信息」，不得改成默认行业")

    def test_c4_old_card_empty_industry_is_empty_string(self):
        row = [None] * len(cpq_wf._CARD_COLS)
        row[cpq_wf._CARD_COLS.index("session_id")] = "sess-1"
        row[cpq_wf._CARD_COLS.index("industry")] = None
        card = cpq_wf._card_row(tuple(row))
        self.assertEqual("", card["industry"],
                         "老卡片没有行业要读成空串（调用方按默认行业处理），不猜、不回填")

    def test_c5_card_industry_conflict_is_surfaced(self):
        source = self.workbench()
        self.assertIn("Card.industry" if "Card.industry" in source else "card.industry", source,
                      "打开已有卡片时要读卡片行业，不能只用 URL/storage（Spec §2.4 第 4 条）")

    def test_c6_industry_sync_does_not_break_other_fields(self):
        body = extract_function(self.workbench(), "wfSyncCard")
        for key in ("session_id", "title", "customer", "project_name", "current_step"):
            self.assertIn(key, body, "既有同步字段不得被本批动掉：%s" % key)


# --------------------------------------------------------------------------- #
# D 组：单一事实源与生效面
# --------------------------------------------------------------------------- #
class TestDSingleSourceOfTruth(Base):
    def test_d1_step1_uses_current_industry(self):
        source = self.workbench()
        self.assertIn("industry: currentIndustry()", source,
                      "第 1 步请求必须按当前行业上报（既有口径，Spec §2.5）")

    def test_d2_backend_industry_resolution_is_single(self):
        source = read(ROOT / "cpq_agent_server.py")
        self.assertEqual(1, source.count("def _industry_of("),
                         "行业判定只能有一个入口 _industry_of()（Spec §2.5）")
        self.assertIn("quote_industry_templates.normalize", source,
                      "后端判定必须复用 cpq_industries 归一化")

    def test_d3_workbench_dropdown_is_served_not_hardcoded(self):
        source = read(WORKBENCH_HTML)
        hardcoded = re.findall(r"<option value=\"(semiconductor|battery|appliance|packaging)\"",
                               source)
        self.assertEqual([], hardcoded,
                         "工作台行业下拉必须由 /api/meta 下发，不得硬编码（Spec §2.1）")

    def test_d3b_home_dropdown_matches_registry_order_and_labels(self):
        source = read(HOME_HTML)
        block = re.search(r'<select id="techIndustry"[\s\S]*?</select>', source)
        self.assertIsNotNone(block, "找不到首页行业下拉 #techIndustry")
        pairs = re.findall(r'<option value="([^"]+)"[^>]*>([^<]+)</option>', block.group(0))
        self.assertEqual(list(cpq_industries.INDUSTRY_KEYS), [key for key, _ in pairs],
                         "首页行业下拉必须与 cpq_industries.INDUSTRY_KEYS 同序同值")
        self.assertEqual([cpq_industries.label_of(key) for key in cpq_industries.INDUSTRY_KEYS],
                         [label.strip() for _, label in pairs],
                         "首页行业下拉标签必须与 label_of() 一致")

    def test_d4_industries_module_is_still_the_registry(self):
        self.assertEqual(("semiconductor", "battery", "appliance", "packaging"),
                         tuple(cpq_industries.INDUSTRY_KEYS))
        self.assertEqual("semiconductor", cpq_industries.DEFAULT_INDUSTRY)
        self.assertEqual("packaging", cpq_industries.normalize(" Packaging "))
        self.assertEqual("semiconductor", cpq_industries.normalize("nope"))


# --------------------------------------------------------------------------- #
# E 组：非回归
# --------------------------------------------------------------------------- #
class TestENonRegression(Base):
    def test_e1_inline_scripts_still_parse(self):
        for path in (HOME_HTML, WORKBENCH_HTML):
            blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", read(path), re.S)
            self.assertTrue(blocks, "%s 里找不到内联脚本" % path.name)
            for index, block in enumerate(blocks):
                if not block.strip():
                    continue
                self.check_js(block, label="%s 内联脚本 #%d 语法" % (path.name, index))

    def test_e2_workbench_still_sends_industry_to_meta(self):
        source = self.workbench()
        self.assertIn("/api/meta?industry=", source,
                      "④产品技术参数换源仍要按行业问服务端（Spec §2.5）")

    def test_e3_home_industry_memory_today_is_untouched(self):
        source = self.home()
        self.assertIn("cpq:tech:industry", source,
                      "首页「记住上次行业」的现有记忆键保持原样")
        body = extract_function(source, "initTechIndustry")
        self.assertTrue(body, "找不到 initTechIndustry()")

    def test_e4_legacy_flexible_still_readable(self):
        self.assertTrue(cpq_industries.is_legacy("flexible"))
        self.assertEqual("flexible", cpq_industries.normalize("flexible"),
                         "历史键必须仍可读（老草稿还能打开），本批不改这个口径")
        self.assertEqual("semiconductor", cpq_industries.normalize("nope"),
                         "未知值仍落默认行业")
        self.assertFalse(cpq_industries.is_supported("flexible"),
                         "历史键不可选：前端下拉只认 is_supported 的键")

    def test_e5_no_tech_side_files_touched(self):
        source = read(INDUSTRY_PY)
        self.assertIn("INDUSTRY_KEYS: tuple[str, ...]", source,
                      "行业注册表结构不得被本批改写；本批只改「带过去」的链路")


if __name__ == "__main__":
    unittest.main(verbosity=2)

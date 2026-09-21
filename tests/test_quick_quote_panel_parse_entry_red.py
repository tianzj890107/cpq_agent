"""红测：快速报价面板的图纸/文件入口 —— 逆向快速报价第 11 批。

Spec：`docs/specs/quick-quote-11-panel-parse-entry.md`
依赖：批 5 的服务端路由 `POST /api/quick-quote/parse`（已实现）、批 2 的候选检索（已实现）、
批 9/10 的解析口径（已实现）。

现状缺口（本机与 34 上查实，不是推断）：

```
grep -rn "QUICK_QUOTE_PARSE_PATH|/api/quick-quote/parse" 前端目录  → 0 处
34：POST /agents/quote/api/quick-quote/parse                     → 401「请先登录」（登录后可用）
报价首页点「快速报价」                                            → 只列案例库，没有上传入口
```

解析通了、检索通了、口径收口了，**页面上没有地方丢图纸** —— 这就是本批要补的那一层。

纪律：
  · A 组用 `node` **真执行** `quickQuoteParseView()` 的函数体（不是文本 grep）；
  · B/C 组是接线与出参断言；D 组是"旧口径不许回退"的护栏（今天即绿）；
  · 全部离线：不联网、不连库、不真转图纸；
  · 禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import base64
import importlib
import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PANEL_JS = ROOT / "tech_app" / "frontend" / "quick-quote-panel.js"
SERVER_PY = ROOT / "cpq_agent_server.py"
MATCH_PY = ROOT / "cpq_quick_quote_match.py"

#: 红测直接抽函数体交给 node 执行（只认 `function name(...) {…}` 具名声明）。
EXTRACT_JS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
function extract(name) {
  const at = src.indexOf("function " + name + "(");
  if (at < 0) return null;
  let i = src.indexOf("{", at);
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === "{") depth++;
    else if (src[j] === "}") { depth--; if (depth === 0) return src.slice(at, j + 1); }
  }
  return null;
}
const name = process.argv[3];
const fn = extract(name);
if (!fn) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
const mode = process.argv[4];
if (mode === "body") { console.log(JSON.stringify({ missing: false, body: fn })); process.exit(0); }
const cases = JSON.parse(mode);
eval(fn);
const out = [];
for (const args of cases) {
  const call = name + "(" + args.map(a => JSON.stringify(a)).join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""

VIEW = "quickQuoteParseView"

#: 真样本 酒盒.dwg 在 34 上的真实出参形状（`## 251.1` / `## 252.1` 的复验输出，逐字照抄）。
OK_RESULT = {
    "ok": True,
    "parse": {"kind": "drawing"},
    "inputs": {"face_paper_gsm": 235.0, "v_groove": True},
    "missing": ["inner_length", "inner_width", "inner_height", "grey_board_gsm"],
    "sources": {"face_paper_gsm": "parse", "v_groove": "parse"},
    "warnings": ["图纸标注尺寸只有实测值、没有轴名（axis）：未用于内尺寸，请人工确认哪条是内长/内宽/内高（不按顺序猜）",
                 "图纸范围（outline_size.source=document_extents）是整张图的幅面、不是成品内尺寸：未用于内尺寸，请人工补内长/内宽/内高"],
    "capability": {"service": "cpq-unified-parse", "provider": "oda", "provider_version": "27.1",
                   "dwg": True, "dxf": True, "preview": True},
    "labels": {"face_paper_gsm": "面纸克重", "v_groove": "V槽", "inner_length": "内长",
               "inner_width": "内宽", "inner_height": "内高", "grey_board_gsm": "灰板克重"},
    "match": {"engine_version": "quick-quote-match/1", "inputs_complete": False,
              "missing_inputs": ["inner_length"],
              "candidates": [{"case_code": "YT-DWG-WINE-700ML", "status": "matched",
                              "similarity_pct": 88.0}],
              "suggested_case_code": "YT-DWG-WINE-700ML", "no_candidate_reason": ""},
}

UNSUPPORTED = {"ok": False, "kind": "unsupported", "error": "不支持的文件格式：需求.docx",
               "advice": "请上传图纸（DWG/DXF）或需求文件（PDF/Excel/文字）"}
UNAVAILABLE = {"ok": False, "kind": "service_unavailable",
               "error": "统一解析服务不可达",
               "advice": "统一解析服务不在线：文字 / Excel / PDF 需求不受影响，"
                         "DWG/DXF 请稍后重试或转人工。"}


def run_view(cases, timeout=120):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(PANEL_JS), VIEW, json.dumps(cases)],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return payload


def view_of(result):
    payload = run_view([[result]])
    if payload.get("missing"):
        raise AssertionError("%s 不存在（Spec 批 11 C3）" % VIEW)
    got = payload["results"][0]
    if not got.get("ok"):
        raise AssertionError("%s 抛错：%s" % (VIEW, got.get("error")))
    return got["value"]


def function_body(name):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(PANEL_JS), name, "body"],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return "" if payload.get("missing") else str(payload.get("body") or "")


class Base(unittest.TestCase):
    maxDiff = None

    def panel(self):
        self.assertTrue(PANEL_JS.exists(), "quick-quote-panel.js 不存在")
        return PANEL_JS.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# A 组：纯函数（node 真跑）
# --------------------------------------------------------------------------- #
class TestAView(Base):
    def test_a1_inputs_rows_sorted_with_labels_and_sources(self):
        view = view_of(OK_RESULT)
        rows = view["inputs"]
        self.assertEqual(["face_paper_gsm", "v_groove"], [row["key"] for row in rows],
                         "匹配输入按 key 升序，便于逐字对照（Spec C3）")
        self.assertEqual("面纸克重", rows[0]["label"], "标签必须取后端 labels")
        self.assertEqual(235.0, float(rows[0]["value"]))
        self.assertEqual("parse", rows[0]["source"], "来源照后端 sources 透出，前端不自己判")

    def test_a2_missing_keeps_backend_order_with_labels(self):
        view = view_of(OK_RESULT)
        self.assertEqual(["inner_length", "inner_width", "inner_height", "grey_board_gsm"],
                         [row["key"] for row in view["missing"]],
                         "缺哪些字段按后端给的顺序（= 匹配键闭集顺序）")
        self.assertEqual({"内长", "内宽", "内高", "灰板克重"},
                         {row["label"] for row in view["missing"]})

    def test_a3_headline_counts_not_vocabulary(self):
        self.assertEqual("解析成功，读出 2 项匹配输入，还有 4 项要人工补",
                         view_of(OK_RESULT)["headline"],
                         "成功话术用计数说话，不引第二份词表（Spec C3）")

    def test_a4_capability_says_provider_and_dwg_support(self):
        text = view_of(OK_RESULT)["capability"]["text"]
        for token in ("oda", "27.1", "支持"):
            self.assertIn(token, text, "能力段要能一眼看出解析器与 DWG 支持：%s" % token)
        self.assertTrue(view_of(OK_RESULT)["capability"]["dwg"])
        no_dwg = dict(OK_RESULT, capability={"provider": "libredwg", "provider_version": "0.12",
                                            "dwg": False})
        self.assertIn("不支持", view_of(no_dwg)["capability"]["text"])

    def test_a5_warnings_passed_through_verbatim(self):
        view = view_of(OK_RESULT)
        self.assertEqual(OK_RESULT["warnings"], view["warnings"], "告警逐条透出、不折叠不改写")

    def test_a6_candidates_render_only(self):
        view = view_of(OK_RESULT)
        self.assertEqual(1, view["candidate_total"])
        self.assertEqual("YT-DWG-WINE-700ML", view["candidates"][0]["case_code"])
        self.assertEqual("YT-DWG-WINE-700ML", view["suggested_case_code"])
        self.assertFalse(view["inputs_complete"])

    def test_a7_no_candidate_reason_is_backend_text(self):
        empty = dict(OK_RESULT, match=dict(OK_RESULT["match"], candidates=[],
                                          suggested_case_code="",
                                          no_candidate_reason="没有可用于快速报价的标准案例：请转精准报价"))
        self.assertEqual("没有可用于快速报价的标准案例：请转精准报价",
                         view_of(empty)["no_candidate_reason"], "无候选时逐字用后端理由（Spec C6）")
        self.assertEqual(0, view_of(empty)["candidate_total"])

    def test_a8_unsupported_keeps_error_and_advice(self):
        view = view_of(UNSUPPORTED)
        self.assertFalse(view["ok"])
        self.assertEqual("unsupported", view["kind"])
        self.assertEqual(UNSUPPORTED["error"], view["headline"], "错误逐字，不许改写")
        self.assertEqual(UNSUPPORTED["advice"], view["advice"])
        self.assertFalse(view["retryable"], "格式不支持不是可重试故障")

    def test_a9_service_unavailable_is_retryable(self):
        view = view_of(UNAVAILABLE)
        self.assertEqual("service_unavailable", view["kind"])
        self.assertTrue(view["retryable"], "解析服务不在线要能重试")
        self.assertIn("统一解析服务不在线", view["advice"])

    def test_a10_plain_error_falls_back_to_error_kind(self):
        view = view_of({"ok": False, "error": "缺少文件名（name）"})
        self.assertEqual("error", view["kind"])
        self.assertEqual("缺少文件名（name）", view["headline"])
        self.assertEqual("", view["advice"])

    def test_a11_missing_labels_fall_back_to_key(self):
        view = view_of({"ok": True, "parse": {"kind": "drawing"},
                        "inputs": {"inner_length": 120.0}, "missing": ["print_colors"],
                        "sources": {}})
        self.assertEqual("inner_length", view["inputs"][0]["label"],
                         "后端没给标签就退回 key —— 前端不许自带第二份中文表（Spec C3）")
        self.assertEqual("print_colors", view["missing"][0]["label"])

    def test_a12_capability_unknown_is_not_unsupported(self):
        view = view_of({"ok": True, "parse": {"kind": "text"}, "inputs": {}, "missing": []})
        self.assertEqual("解析器能力未知", view["capability"]["text"],
                         "未知不等于不支持（Spec C3）")
        self.assertFalse(view["capability"]["dwg"])

    def test_a13_empty_payload_never_throws(self):
        view = view_of({})
        self.assertFalse(view["ok"])
        self.assertEqual([], view["inputs"])
        self.assertEqual([], view["missing"])
        self.assertEqual([], view["warnings"])

    def test_a14_body_has_no_dom_or_network(self):
        body = function_body(VIEW)
        self.assertTrue(body, "%s 必须是具名函数（Spec C3）" % VIEW)
        for banned in ("document", "window", "sessionStorage", "localStorage", "fetch("):
            self.assertNotIn(banned, body, "纯函数体内不许出现 %s（红测把它单独交给 node 跑）" % banned)


# --------------------------------------------------------------------------- #
# B 组：面板接线
# --------------------------------------------------------------------------- #
class TestBWiring(Base):
    def test_b1_parse_path_matches_backend_constant(self):
        module = importlib.import_module("cpq_quick_quote_file")
        found = re.search(r'PARSE_PATH\s*=\s*"([^"]+)"', self.panel())
        self.assertIsNotNone(found, "面板必须定义 PARSE_PATH（Spec C1）")
        self.assertEqual(module.QUICK_QUOTE_PARSE_PATH, found.group(1),
                         "面板路径必须与 cpq_quick_quote_file.QUICK_QUOTE_PARSE_PATH 同值")

    def test_b2_has_file_input_for_drawing(self):
        source = self.panel()
        self.assertIn("data-qq-parse-input", source, "面板要有上传入口（Spec C2）")
        self.assertRegex(source, r'\.type\s*=\s*"file"', "入口必须是真实的 file input")

    def test_b3_accept_covers_dwg_and_dxf(self):
        source = self.panel()
        found = re.search(r'PARSE_ACCEPT\s*=\s*"([^"]+)"', source)
        self.assertIsNotNone(found, "accept 清单要有唯一常量（Spec C2）")
        accept = found.group(1)
        for ext in (".dwg", ".dxf"):
            self.assertIn(ext, accept, "图纸格式必须在 accept 里：%s" % ext)

    def test_b4_upload_posts_base64_not_conversion(self):
        body = function_body("parseFile") or function_body("parse")
        self.assertTrue(body, "面板必须有 parse()/parseFile() 入口（Spec C2）")
        self.assertIn("apiFetch", body, "必须走页面注入的统一带票请求（Spec C1）")
        self.assertIn("base64", body, "文件要以 base64 交给服务端解析")
        self.assertTrue("FileReader" in body or "arrayBuffer" in body,
                        "浏览器只负责读字节，转换在服务端（Spec C2）")

    def test_b5_result_surfaces_parse_attributes(self):
        source = self.panel()
        for attr in ("data-qq-parse-inputs", "data-qq-parse-missing",
                     "data-qq-parse-warnings", "data-qq-parse-capability",
                     "data-qq-parse-candidates"):
            self.assertIn(attr, source, "解析结果要上屏（Spec C3/C5/C6）：%s" % attr)

    def test_b6_no_tech_route_no_browser_conversion(self):
        source = self.panel()
        for banned in ("/api/projects/", "drawing-flow", "cpq_tech_bridge",
                       "dwg2dxf", "ODAFileConverter", "xvfb"):
            self.assertNotIn(banned, source,
                             "报价侧面板不许碰技术工艺链路或自己转换（Spec C7）：%s" % banned)

    def test_b7_exports_stay_and_grow(self):
        source = self.panel()
        for name in ("QUOTE_MODES", "MODE_LABELS", "CASES_PATH", "REASON_LABELS",
                     "DIFF_HEADERS", "QUOTE_ACTIONS", "QUOTE_ACTION_LABELS", "cases",
                     "renderReadiness", "renderDiffTable", "renderQuote", "render",
                     "open", "close"):
            self.assertRegex(source, r"\b%s\b" % re.escape(name),
                             "既有导出不许少（Spec C7）：%s" % name)
        for name in ("PARSE_PATH", "parse", "quickQuoteParseView", "renderParse"):
            self.assertRegex(source, r"\b%s\b" % re.escape(name), "本批新出口：%s" % name)

    def test_b8_node_syntax_ok(self):
        proc = subprocess.run(["node", "--check", str(PANEL_JS)], capture_output=True, text=True,
                              timeout=60)
        self.assertEqual(0, proc.returncode, "面板脚本语法必须过：%s" % (proc.stderr or "")[:400])


# --------------------------------------------------------------------------- #
# C 组：后端标签出口
# --------------------------------------------------------------------------- #
class TestCLabels(Base):
    def test_c1_input_labels_public_helper(self):
        module = importlib.import_module("cpq_quick_quote_match")
        self.assertTrue(hasattr(module, "input_labels"), "要有公开的 input_labels()（Spec C4）")
        labels = module.input_labels(["inner_length", "face_paper_gsm", "zzz_unknown"])
        self.assertIsInstance(labels, dict)
        self.assertEqual("内长", labels["inner_length"])
        self.assertEqual("面纸克重", labels["face_paper_gsm"])
        self.assertEqual("zzz_unknown", labels["zzz_unknown"], "没登记就退回 key，不编中文")

    def _handler(self):
        module = importlib.import_module("cpq_agent_server")
        self.assertTrue(hasattr(module, "_handle_quick_quote_parse"))
        return module

    def test_c2_parse_response_carries_labels(self):
        server = self._handler()
        file_module = importlib.import_module("cpq_quick_quote_file")
        original = file_module.parse_file
        try:
            file_module.parse_file = lambda name, raw, **kw: {
                "kind": "drawing", "fields": {"units": "mm"},
                "capability": {"provider": "oda", "provider_version": "27.1", "dwg": True},
                "inputs": {"face_paper_gsm": 235.0},
                "missing": ["inner_length", "grey_board_gsm"],
                "sources": {"face_paper_gsm": "parse"}, "warnings": []}
            out = server._handle_quick_quote_parse(
                {"name": "酒盒.dwg", "data": base64.b64encode(b"AC1027").decode("ascii"),
                 "match": False})
        finally:
            file_module.parse_file = original
        self.assertTrue(out.get("ok"), out)
        labels = out.get("labels") or {}
        self.assertTrue(labels, "出参必须带 labels（Spec C4）")
        for key in list(out["inputs"]) + list(out["missing"]):
            self.assertIn(key, labels, "labels 要覆盖 inputs 与 missing 的每一个键：%s" % key)
        self.assertEqual("内长", labels["inner_length"])

    def test_c3_existing_response_keys_unchanged(self):
        server = self._handler()
        file_module = importlib.import_module("cpq_quick_quote_file")
        original = file_module.parse_file
        try:
            file_module.parse_file = lambda name, raw, **kw: {
                "kind": "drawing", "fields": {}, "capability": {},
                "inputs": {}, "missing": ["inner_length"], "sources": {}, "warnings": []}
            out = server._handle_quick_quote_parse(
                {"name": "x.dwg", "data": base64.b64encode(b"AC1027").decode("ascii"),
                 "match": False})
        finally:
            file_module.parse_file = original
        for key in ("ok", "parse", "inputs", "missing", "sources", "warnings", "capability", "match"):
            self.assertIn(key, out, "既有出参键一个不少（Spec C4）：%s" % key)


# --------------------------------------------------------------------------- #
# D 组：护栏（今天即绿）
# --------------------------------------------------------------------------- #
class TestDGuards(Base):
    def test_d1_quote_actions_closed_set(self):
        source = self.panel()
        found = re.search(r'QUOTE_ACTIONS\s*=\s*\[([^\]]*)\]', source)
        self.assertIsNotNone(found)
        actions = re.findall(r'"([^"]+)"', found.group(1))
        self.assertEqual(["save_quote", "transfer_precise"], actions, "出价动作闭集不许变（批 8）")

    def test_d2_cases_path_unchanged(self):
        found = re.search(r'CASES_PATH\s*=\s*"([^"]+)"', self.panel())
        self.assertIsNotNone(found)
        self.assertEqual("/api/quick-quote/cases", found.group(1))

    def test_d3_backend_parse_path_unchanged(self):
        module = importlib.import_module("cpq_quick_quote_file")
        self.assertEqual("/api/quick-quote/parse", module.QUICK_QUOTE_PARSE_PATH)

    def test_d4_server_handler_still_avoids_tech_side(self):
        source = SERVER_PY.read_text(encoding="utf-8")
        handler = source.split("def _handle_quick_quote_parse", 1)[1].split("\ndef ", 1)[0]
        for banned in ("/api/projects/", "cpq_tech_bridge", "dwg2dxf"):
            self.assertNotIn(banned, handler)


if __name__ == "__main__":                                      # pragma: no cover
    unittest.main()

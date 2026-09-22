"""红测：单件结论的「业务部件身份」必须出现在内嵌面板上（后端 ## 410 的界面那一半）。

Spec：`docs/specs/packaging-part-conclusion-business-identity-in-panel.md`

现状缺口（代码级，都可指到行）：
  · `inline-analysis.js:41 conclusionVersionNote()` 只认零件版本（`stale` / `parts_unknown`），
    业务清单漂移没有分支；
  · `inline-analysis.js:144` 把读回体只留成 `state.versionNote` —— 后端新回的
    `business_part_code` / `business_parts_id` / `business_stale` / `business_stale_reason`
    **没有任何消费者**；
  · `renderProcess()`（`:358`）/ `renderCost()`（`:469`）的正文里没有结论口径的位置。

纪律：`node -e` 抽 app.js 顶层具名函数真跑（纯函数）+ 源码守卫 + `node --check`；
不起服务、不发 HTTP、不连 PG / 34、不写业务数据。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FRONTEND = ROOT / "tech_app" / "frontend"
APP_JS = FRONTEND / "app.js"
INLINE_JS = FRONTEND / "inline-analysis.js"
PARTS_PY = ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py"
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"

STALE_TEXT = "这份结论是按上一版业务部件清单算的，请重跑后再用。"
UNKNOWN_TEXT = "判断不了这份结论对应哪一版业务部件清单。"
UNBOUND_TEXT = "这一件没有绑到业务部件，结论按几何零件算的。"
BIZ_ID = "biz:oldoldoldoldold"          # 20 字符 → 截断
SHORT_BIZ_ID = "biz:oldoldol…"          # 前 12 个字符 + 省略号

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
const mode = process.argv[4];
const fn = extract(name);
if (!fn) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
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


def run_cases(name: str, cases):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name,
                           json.dumps(cases)], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(name: str) -> str:
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, "body"],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少具名函数 %s()（Spec §C1）" % name)
    return str(payload.get("body") or "")


def note(payload):
    """跑一遍纯函数；函数不存在 / 抛错都按红处理。"""
    got = run_cases("packagingPartBusinessIdentityNote", [[payload]])
    if got.get("missing"):
        raise AssertionError("app.js 缺少顶层纯函数 packagingPartBusinessIdentityNote()（Spec §C1）")
    row = got["results"][0]
    if not row.get("ok"):
        raise AssertionError("packagingPartBusinessIdentityNote() 抛异常：%s" % row.get("error"))
    return row.get("value") or {}


def problem_text(payload):
    return str(note(payload).get("text") or "")


def level_of(payload):
    return str(note(payload).get("level") or "")


def _source(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# A 组：纯函数五态（红）
# --------------------------------------------------------------------------- #
class ABusinessIdentityNote(unittest.TestCase):
    def test_a1_no_business_version_says_nothing(self):
        for payload in ({}, None, "nonsense", 7, {"business_parts_id": "   "}):
            self.assertEqual("", problem_text(payload),
                             "没有业务身份的结论（本批之前落的）什么都不说（Spec §C1）：%r"
                             % (payload,))
            self.assertEqual("", level_of(payload), "同上：level 必须是空串（Spec §C1）")

    def test_a2_stale_says_rerun(self):
        got = note({"business_parts_id": BIZ_ID, "business_part_code": "JWXR21-P01",
                    "business_stale": True, "business_stale_reason": "business_parts_reimported"})
        self.assertEqual("stale", got.get("level"), "上一版清单算的 → level=stale（Spec §C1）")
        self.assertEqual(STALE_TEXT, got.get("text"),
                         "文案必须逐字来自本地表，不许把 `business_parts_reimported` 贴给用户"
                         "（Spec §C1）")

    def test_a3_unknown_is_not_stale_and_has_its_own_text(self):
        got = note({"business_parts_id": BIZ_ID, "business_part_code": "JWXR21-P01",
                    "business_stale": False, "business_stale_reason": "business_parts_unknown"})
        self.assertEqual("unknown", got.get("level"), "判断不了 → level=unknown（Spec §C1）")
        self.assertEqual(UNKNOWN_TEXT, got.get("text"), "比较不了 ≠ 过期，文案要分开（Spec §C1）")

    def test_a4_bound_part_shows_code_and_short_version(self):
        got = note({"business_parts_id": BIZ_ID, "business_part_code": "JWXR21-P01",
                    "business_stale": False, "business_stale_reason": ""})
        self.assertEqual("info", got.get("level"), "绑到业务件 → level=info（Spec §C1）")
        self.assertEqual("业务部件 JWXR21-P01（清单 %s）" % SHORT_BIZ_ID, got.get("text"),
                         "编码 + 清单短号（前 12 字符，截断补 …）（Spec §C1）")

    def test_a5_short_version_is_not_ellipsised(self):
        self.assertEqual("业务部件 JWXR21-P02（清单 biz:short）",
                         problem_text({"business_parts_id": "biz:short",
                                       "business_part_code": "JWXR21-P02"}),
                         "不超过 12 个字符就原样给，不许硬加省略号（Spec §C1）")

    def test_a6_no_code_says_unbound(self):
        got = note({"business_parts_id": BIZ_ID, "business_part_code": "",
                    "business_stale": False, "business_stale_reason": ""})
        self.assertEqual("unbound", got.get("level"), "有清单版本但没绑到业务件（Spec §C1）")
        self.assertEqual(UNBOUND_TEXT, got.get("text"), "这句要说清「结论按几何零件算的」（Spec §C1）")

    def test_a7_precedence_is_fixed(self):
        self.assertEqual("stale", level_of({"business_parts_id": BIZ_ID, "business_stale": True,
                                            "business_stale_reason": "business_parts_unknown",
                                            "business_part_code": "JWXR21-P01"}),
                         "stale 优先于 unknown / info（Spec §C1）")
        self.assertEqual("unknown", level_of({"business_parts_id": BIZ_ID,
                                              "business_stale_reason": "business_parts_unknown",
                                              "business_part_code": "JWXR21-P01"}),
                         "unknown 优先于 info（Spec §C1）")
        self.assertEqual("info", level_of({"business_parts_id": BIZ_ID,
                                           "business_part_code": "JWXR21-P01"}),
                         "info 优先于 unbound（Spec §C1）")

    def test_a8_levels_are_a_closed_set(self):
        for payload in ({}, {"business_parts_id": BIZ_ID, "business_stale": True},
                        {"business_parts_id": BIZ_ID, "business_stale_reason": "business_parts_unknown"},
                        {"business_parts_id": BIZ_ID, "business_part_code": "X"},
                        {"business_parts_id": BIZ_ID, "business_stale": True}):
            self.assertIn(level_of(payload), ("", "info", "stale", "unknown", "unbound"),
                          "level 是闭集（Spec §C1）：%r" % (payload,))

    def test_a9_never_throws_on_odd_payloads(self):
        for payload in ({"business_parts_id": 12, "business_part_code": 34, "business_stale": "yes"},
                        {"business_parts_id": BIZ_ID, "business_part_code": {"a": 1}},
                        {"business_parts_id": [1, 2], "business_part_code": "X"}):
            note(payload)   # 不抛就算过（Spec §C1：任何输入都不抛错）

    def test_a10_pure_function_has_no_dom_or_io(self):
        body = function_body("packagingPartBusinessIdentityNote")
        for token in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(token, body, "纯函数体内不许出现 %s（Spec §C1）" % token)


# --------------------------------------------------------------------------- #
# B 组：接线（红）
# --------------------------------------------------------------------------- #
class BWiredIntoInlineAnalysis(unittest.TestCase):
    def setUp(self):
        self.src = _source(INLINE_JS)

    def test_b1_load_computes_the_note_once(self):
        self.assertEqual(1, self.src.count("state.businessNote = packagingPartBusinessIdentityNote(data)"),
                         "`load()` 里必须有一处把读回体算成 businessNote（Spec §C2）")
        self.assertIn("businessNote:", self.src, "state 初始要给 businessNote 键（Spec §C2）")

    def test_b2_both_bodies_render_the_note(self):
        self.assertEqual(1, self.src.count("data-inline-business-note"),
                         "那一行的节点只许有一处构造（Spec §C2）")
        self.assertEqual(1, self.src.count("function businessIdentityRow(state)"),
                         "那一行的构造只许有一处（Spec §C2）")
        self.assertEqual(2, self.src.count("= businessIdentityRow(state)"),
                         "renderProcess 与 renderCost 的正文最前面各调用一次（Spec §C2）")
        self.assertIn('if (!note.text) return "";', self.src,
                      "空文案必须一个节点都不渲染 —— 既有正文逐字不变（Spec §C2）")

    def test_b3_status_line_is_untouched(self):
        self.assertIn('setStatus(state, state.versionNote || (state.mode === "process"',
                      self.src,
                      "零件版本那句的既有拼法逐字不变：业务那句话不许去顶掉它（Spec §C2/C3）")
        for line in self.src.splitlines():
            if "setStatus(" in line:
                self.assertNotIn("businessNote", line,
                                 "业务那句话不进状态行，免得顶掉零件版本那句（Spec §C2）：%s"
                                 % line.strip())

    def test_b4_no_new_request_or_endpoint(self):
        # 只数**请求与端点**的用法个数（注释里提到路由名不算数）：
        # `jsonFetch` 1 处定义 + 5 处调用；`endpointBase` 1 处定义 + 3 处调用。
        self.assertEqual(6, self.src.count("jsonFetch("),
                         "本批不新增请求（Spec §C2/C3）")
        self.assertEqual(4, self.src.count("endpointBase("),
                         "本批不动数据源地址（Spec §C3）")
        self.assertEqual(1, self.src.count("process-lookup"),
                         "既有依据路由逐字不变（Spec §C3）")
        self.assertEqual(1, self.src.count("cost-lookup"), "同上（Spec §C3）")


# --------------------------------------------------------------------------- #
# C 组（护栏）：既有零件版本口径与后端一个字不改
# --------------------------------------------------------------------------- #
class CGuardrails(unittest.TestCase):
    def test_c1_parts_version_note_is_unchanged(self):
        src = _source(INLINE_JS)
        for text in ("这份结论是上一版零件算的（", "无法判断这份结论对应哪一版零件。"):
            self.assertIn(text, src, "零件版本那句话逐字不变（Spec §C3）")

    def test_c2_backend_is_untouched(self):
        for path in (MAIN_PY, PARTS_PY):
            self.assertNotIn("packagingPartBusinessIdentityNote", _source(path),
                             "本批不改后端（Spec §C3）：%s" % path.name)

    def test_c3_the_function_lives_in_app_js_not_in_the_iife(self):
        self.assertIn("function packagingPartBusinessIdentityNote(",
                      _source(APP_JS), "具名纯函数必须落在 app.js 顶层（Spec §C1）")
        self.assertNotIn("function packagingPartBusinessIdentityNote(",
                         _source(INLINE_JS), "不许在 IIFE 里另写一份（Spec §C1）")

    def test_c4_both_files_pass_node_check(self):
        for path in (APP_JS, INLINE_JS):
            proc = subprocess.run(["node", "--check", str(path)],
                                  capture_output=True, text=True, timeout=60)
            self.assertEqual(0, proc.returncode,
                             "%s 语法必须通过（Spec §C3）：%s" % (path.name, proc.stderr[:400]))


if __name__ == "__main__":
    unittest.main()

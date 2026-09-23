r"""红测：左栏那句「零件清单已刷新：N 件」必须按**业务部件**口径报数。

Spec：`docs/specs/packaging-parts-refresh-line-must-count-business-parts.md`

用户原话（2026-09-23）：

> 而且不应该显示这个 零件清单已刷新：263 件（点一行可在右栏看这一件） 因为下面是 业务部件 28 件
> （从图纸推导（待人工确认））… 那就应该显示 28 件

现状缺口（HEAD `872898b` 工作副本只读）：
  · `app.js:3848-3856` `refreshPackagingPartsAfterDrawingFlow()` 用**几何零件文档**的 `doc.parts.length`
    拼那句文案（酒盒实测 263），而左栏结果区那句「业务部件 28 件」走的是**业务部件文档**（28 行）——
    同一屏两个数打架，用户看到的第一句话就是错的（`## 480` 已规定几何分量只进折叠诊断区）；
  · 这句提示没有任何"业务账优先"的取数口径，也没有把几何账自称"几何区域"的说法。

纪律：只读 `app.js` + `node -e` 抽具名函数真跑；不起服务、不发 HTTP、不连 PG / 34、不写业务数据。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = ROOT / "tech_app" / "frontend" / "app.js"

LINE_FN = "packagingPartsRefreshLine"
REFRESH_FN = "refreshPackagingPartsAfterDrawingFlow"
EMPTY_COPY = "零件文档还没有内容，详见上方步骤表与前置条件。"

BUSINESS_28 = "零件清单已刷新：业务部件 28 件（点一行可在右栏看这一件）"
GEOMETRY_263 = "零件清单已刷新：几何区域 263 个（还没推导出业务部件，点一行可查看）"

EXTRACT_JS = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8").replace(/\u0000/g, "");
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
// 被测函数会调 `packagingBusinessPartRows`：把 app.js 里那份原样抽出来一起 eval，不另写替身。
const helper = extract("packagingBusinessPartRows");
if (helper) eval(helper);
eval(fn);
const out = [];
for (const args of cases) {
  const call = name + "(" + args.map(a => JSON.stringify(a) + "").join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""


def read_text(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def _run(name: str, mode: str):
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, mode],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(name: str) -> str:
    payload = _run(name, "body")
    return "" if payload.get("missing") else str(payload.get("body") or "")


def call(name: str, args):
    payload = _run(name, json.dumps([args]))
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §2.1）" % name)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (name, row.get("error")))
    return row.get("value")


def geometry_doc(count: int):
    return {"parts": [{"part_code": "DWG-P%02d" % (index + 1)} for index in range(count)]}


def business_doc(count: int):
    return {"business_parts": [{"business_part_code": "BP-%02d" % (index + 1)}
                               for index in range(count)]}


# --------------------------------------------------------------------------- #
# A 组：纯函数真跑（业务账优先）
# --------------------------------------------------------------------------- #
class ARefreshLineCaliber(unittest.TestCase):
    def test_a1_business_count_wins(self):
        line = call(LINE_FN, [geometry_doc(263), business_doc(28)])
        self.assertEqual(BUSINESS_28, line,
                         "有业务部件就报业务部件数，且逐字一致（Spec §2.1 第 1 条）")
        self.assertNotIn("263", str(line),
                         "业务部件口径那句里不许出现几何分量数（Spec §2.1/§2.3）")

    def test_a2_geometry_only_declares_itself_geometry(self):
        line = call(LINE_FN, [geometry_doc(263), business_doc(0)])
        self.assertEqual(GEOMETRY_263, line,
                         "没有业务部件时，几何账必须自称「几何区域」并给出自己的数（Spec §2.1 第 2 条）")
        self.assertNotIn("零件清单已刷新：263 件", str(line),
                         "几何分量不许再被叫成「零件清单 N 件」（Spec §2.1 第 2 条）")

    def test_a3_nothing_read_is_silent(self):
        self.assertEqual("", call(LINE_FN, [geometry_doc(0), business_doc(0)]),
                         "两个账都读不到就返回空串，交给调用方的空态文案（Spec §2.1 第 3 条）")
        self.assertEqual("", call(LINE_FN, [None, None]),
                         "非对象入参当 0 处理（Spec §2.1 第 3 条）")

    def test_a4_business_wins_even_without_geometry(self):
        self.assertEqual(BUSINESS_28, call(LINE_FN, [geometry_doc(0), business_doc(28)]),
                         "业务账非空就按业务账报，几何为 0 也不影响（Spec §2.1 第 1 条）")

    def test_a5_broken_input_does_not_throw(self):
        for args in ([{}, {}], [{"parts": "x"}, {"business_parts": None}],
                     [{"parts": [None, 1]}, {"business_parts": [{}, "x"]}]):
            line = call(LINE_FN, args)
            self.assertIsInstance(line, str, "入参畸形也只许回字符串（Spec §2.1）：%r" % (args,))

    def test_a6_line_function_stays_pure(self):
        body = function_body(LINE_FN)
        self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §2.1）" % LINE_FN)
        for token in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(token, body,
                             "%s() 必须是纯函数、不碰 DOM / 网络（Spec §2.1）：%s" % (LINE_FN, token))


# --------------------------------------------------------------------------- #
# B 组：接线守卫
# --------------------------------------------------------------------------- #
class BRefreshWiring(unittest.TestCase):
    def test_b1_the_refresh_path_uses_the_line_function(self):
        body = function_body(REFRESH_FN)
        self.assertTrue(body, "app.js 缺少 %s()" % REFRESH_FN)
        self.assertIn(LINE_FN + "(", body,
                      "刷新提示必须走同一个取数口径（Spec §2.2）")

    def test_b2_no_second_caliber_left_behind(self):
        body = function_body(REFRESH_FN)
        self.assertNotIn("零件清单已刷新：${rows} 件",
                         body.replace(" ", ""),
                         "旧的「零件清单已刷新：${rows} 件」必须消失，不许两套口径并存（Spec §2.2）")

    def test_b3_empty_copy_is_verbatim(self):
        body = function_body(REFRESH_FN)
        self.assertIn(EMPTY_COPY, body, "空态文案逐字保留（Spec §2.2）")

    def test_b4_business_caliber_is_read_first(self):
        body = function_body(LINE_FN)
        self.assertIn("packagingBusinessPartRows(", body,
                      "取数必须先走业务部件行数（Spec §2.1）")
        self.assertLess(body.index("packagingBusinessPartRows("), body.index(".parts"),
                        "业务账优先的顺序就是判据本身（Spec §2.1）")


if __name__ == "__main__":
    unittest.main()

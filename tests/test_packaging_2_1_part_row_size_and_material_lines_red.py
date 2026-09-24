"""红测：2.1 业务部件行——尺寸两位小数、材料另起一行、去掉「已在图纸中定位」「图上识别」。

Spec：`docs/specs/packaging-2-1-part-row-size-and-material-lines.md`

用户原话（2026-09-24）：

> 218.19700899999998×68.2460000000001 mm 保留两位小数
> 然后材料这里换行 225G铜版底PET光银 这两个东西之间不需要这个 ·
> 然后已在图纸中定位 / 图上识别 这两句直接不要

现状缺口（工作副本只读）：
  · `app.js:5237-5243 packagingPartSizeText()` / `:3353-3359 packagingBusinessPartSizeText()` 直接
    `String(number)` 进模板 ⇒ 浮点原值 `218.19700899999998`；
  · `app.js:3536`：`<div class="part-meta">${size}${material ? " · " + material : ""}</div>`
    ⇒ 尺寸与材料同一行、用 ` · ` 连；
  · `app.js:3343-3344 PACKAGING_BINDING_COPY.bound` =「已在图纸中定位」渲染在行上与右栏；
  · `app.js:3368-3373 packagingBusinessPartTruthLabel("observed")` =「图上识别」渲染在行上。

纪律：`node -e` 抽具名函数真跑 + 源码 / CSS 扫描；不起服务、不发 HTTP、不写业务数据。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

APP_JS = ROOT / "tech_app" / "frontend" / "app.js"
WORKBENCH_CSS = ROOT / "tech_app" / "frontend" / "workbench.css"

SIZE_FN = "packagingPartSizeText"
BUSINESS_SIZE_FN = "packagingBusinessPartSizeText"
BINDING_FN = "packagingBindingStatusText"
TRUTH_FN = "packagingBusinessPartTruthLabel"
TRUTH_LINE_FN = "packagingBusinessTruthLine"
TREE_FN = "renderPackagingBusinessTree"

MATERIAL_CLASS = "part-material"
BOUND_COPY = "已在图纸中定位"
OBSERVED_COPY = "图上识别"
OTHER_BINDING = {"partial": "部分定位", "ambiguous": "定位待人工确认",
                 "unbound": "尚未在 CAD 图中定位"}
OTHER_TRUTH = {"inferred": "规则纠名（推断）", "pending_confirmation": "结构规则补件（待确认）"}
TRUTH_LINE = "识别 25 · 推断 1 · 待确认 2"

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
const mode = process.argv[4];
if (mode === "line") {
  const fn = extract(name);
  const consts = {};
  ["PACKAGING_BINDING_COPY"].forEach(c => {
    const at = src.indexOf("const " + c + " = {");
    if (at < 0) return;
    const end = src.indexOf("};", at);
    eval("consts[c] = " + src.slice(at + ("const " + c + " = ").length, end + 1));
  });
  console.log(JSON.stringify({ missing: !fn, body: fn || "" }));
  process.exit(0);
}
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


def read_text(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def js_call(name, cases):
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, json.dumps(cases)],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def js_one(name, args, label):
    payload = js_call(name, [args])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（%s）" % (name, label))
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (name, row.get("error")))
    return row.get("value")


def function_body(name: str) -> str:
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, "body"],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return "" if payload.get("missing") else str(payload.get("body") or "")


def business_row(size_length, size_width):
    """业务部件行：两个键都给（本批改名前后都读得到同一组数）。"""
    block = {"length_mm": size_length, "width_mm": size_width, "product_size_text": "",
             "material_text": "225G铜版底PET光银"}
    return {"business_part_code": "DWG-BP01", "name": "面纸",
            "authority": dict(block), "reference": dict(block)}


def css_blocks(selector: str):
    text = read_text(WORKBENCH_CSS)
    out = []
    for match in re.finditer(r"([^{}]*)\{([^{}]*)\}", text):
        if selector in match.group(1):
            out.append(match.group(2))
    return out


def css_font_sizes(selector: str):
    sizes = []
    for body in css_blocks(selector):
        hit = re.search(r"font-size\s*:\s*([0-9.]+)px", body)
        if hit:
            sizes.append(float(hit.group(1)))
    return sizes


# --------------------------------------------------------------------------- #
# E 组：尺寸一律两位小数（末尾零去掉）
# --------------------------------------------------------------------------- #
class ESizeFormat(unittest.TestCase):
    def test_e1_几何件尺寸两位小数(self):
        got = js_one(SIZE_FN, [{"unfolded_length_mm": 218.19700899999998,
                                "unfolded_width_mm": 68.2460000000001}], "几何分量尺寸")
        self.assertEqual("展开 218.2×68.25 mm", got, "浮点原值不许直接上屏（Spec §2.1）")

    def test_e2_业务件尺寸两位小数(self):
        got = js_one(BUSINESS_SIZE_FN, [business_row(218.19700899999998, 68.2460000000001)],
                     "业务部件尺寸")
        self.assertEqual("218.2×68.25 mm", got, "浮点原值不许直接上屏（Spec §2.1）")

    def test_e3_整数不补零(self):
        got = js_one(SIZE_FN, [{"unfolded_length_mm": 300, "unfolded_width_mm": 200}],
                     "几何分量尺寸")
        self.assertEqual("展开 300×200 mm", got, "`300.00` 不许出现（Spec §2.1）")

    def test_e4_原文尺寸逐字不动(self):
        row = {"authority": {"product_size_text": "200×150×80 mm"},
               "reference": {"product_size_text": "200×150×80 mm"}}
        self.assertEqual("200×150×80 mm", js_one(BUSINESS_SIZE_FN, [row], "业务部件尺寸"),
                         "客户 / 图纸原文不许被格式化（Spec §2.1）")

    def test_e5_后端_mm_text_两位小数(self):
        from tech_app.backend.services import packaging_parts
        self.assertEqual("218.2", packaging_parts._mm_text(218.19700899999998),
                         "后端 `_mm_text()` 同口径（Spec §2.1）")

    def test_e6_后端_mm_text_整数(self):
        from tech_app.backend.services import packaging_parts
        self.assertEqual("300", packaging_parts._mm_text(300.0),
                         "后端整数仍写 `300`（Spec §2.1）")


# --------------------------------------------------------------------------- #
# M 组：材料另起一行
# --------------------------------------------------------------------------- #
class MMaterialLine(unittest.TestCase):
    def test_m1_材料独立节点且不再拼_·(self):
        body = function_body(TREE_FN)
        self.assertTrue(body, "app.js 缺少 %s()" % TREE_FN)
        self.assertIn(MATERIAL_CLASS, body, "材料要有独立节点 .%s（Spec §2.2）" % MATERIAL_CLASS)
        meta = re.search(r'class="part-meta">(.*?)</div>', body, re.S)
        self.assertIsNotNone(meta, "行里要有 `.part-meta` 尺寸节点（Spec §2.2）")
        self.assertNotIn("material", meta.group(1),
                         "尺寸那一行里不许再出现材料（Spec §2.2）")
        self.assertNotIn('" · "', meta.group(1),
                         "尺寸与材料之间不许再用 ` · ` 连（Spec §2.2）")

    def test_m2_材料节点有字号规则(self):
        sizes = css_font_sizes("." + MATERIAL_CLASS)
        self.assertTrue(sizes, "`.%s` 一条字号规则都没有（Spec §2.5）" % MATERIAL_CLASS)
        self.assertLessEqual(min(sizes), 11, "材料行字号不许比件名大（Spec §2.5）")

    def test_m3_尺寸行字号规则仍在(self):
        self.assertTrue(css_font_sizes(".part-meta"), "`.part-meta` 字号规则不许回退（Spec §2.5）")

    def test_m4_状态行字号规则仍在(self):
        self.assertTrue(css_font_sizes(".part-note"), "`.part-note` 字号规则不许回退（Spec §2.5）")

    def test_m5_绑定状态文案表_bould_不出字(self):
        self.assertEqual("", js_one(BINDING_FN, ["bound"], "绑定状态文案"),
                         "`bound` 档不再输出任何文字（Spec §2.3）")
        for state, copy in OTHER_BINDING.items():
            self.assertEqual(copy, js_one(BINDING_FN, [state], "绑定状态文案"),
                             "其余档位逐字不变（Spec §2.3）：%s" % state)


# --------------------------------------------------------------------------- #
# L 组：那两句直接不要
# --------------------------------------------------------------------------- #
class LDropTwoSentences(unittest.TestCase):
    def test_l1_已在图纸中定位不许出现(self):
        text = read_text(APP_JS)
        self.assertNotIn(BOUND_COPY, text, "「%s」直接不要（Spec §2.3）" % BOUND_COPY)

    def test_l2_图上识别不许出现(self):
        text = read_text(APP_JS)
        self.assertNotIn(OBSERVED_COPY, text, "「%s」直接不要（Spec §2.3）" % OBSERVED_COPY)
        self.assertEqual("", js_one(TRUTH_FN, ["observed"], "事实档标签"),
                         "`observed` 不再输出标签（Spec §2.3）")

    def test_l3_其余档位逐字不变(self):
        for state, copy in OTHER_TRUTH.items():
            self.assertEqual(copy, js_one(TRUTH_FN, [state], "事实档标签"),
                             "其余档位逐字不变（Spec §2.3）：%s" % state)

    def test_l4_三档计数行不动(self):
        case = {"summary": {"stats": {"truth_state_counts": {"observed": 25, "inferred": 1,
                                                             "pending_confirmation": 2}}}}
        self.assertEqual(TRUTH_LINE, js_one(TRUTH_LINE_FN, [case], "三档计数行"),
                         "计数那一行不是那两句话，不许动（Spec §2.3）")


if __name__ == "__main__":
    unittest.main()

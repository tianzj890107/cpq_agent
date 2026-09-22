"""红测：清单里"有部件图"的那一件取不到字节时，页面不许只剩一个碎图。

Spec：`docs/specs/packaging-authority-thumbnail-bytes-read-failure.md`

现状缺口（代码级，都可指到行）：
  · `tech_app/frontend/app.js:2345-2363 openPackagingBusinessPart()` 按清单的
    `thumb.available` 直接吐 `<img class="packaging-business-thumb" src=…>`，**没有 `onerror`** ——
    读端点 404（`thumbnail_bytes_missing`）或 500 时只剩碎图，面板照旧写"已配到部件图"；
  · `:2357-2361` 的三元表达式把**任何表外 reason** 都写成
    "部件图还没入库（重新导入权威清单即可）"（把"字节被清理"赖到"没导入"上）；
  · 前端自己抄了一份文案（服务端 `main.py:7382-7389 PACKAGING_THUMBNAIL_REASON_COPY` 才是
    权威那份），前端加码不会跟着说。

纪律：`node -e` 抽具名函数体执行（纯函数）+ 源码守卫；不起服务、不发 HTTP、
不连 PG / SQLite、不写业务数据。
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

APP_JS = ROOT / "tech_app" / "frontend" / "app.js"

REASON_FN = "packagingBusinessThumbnailReasonText"
BYTES_FN = "packagingBusinessThumbnailBytesFailureText"

REASON_COPY = {
    "image_bytes_unreadable": "工作簿里的部件图读不出来（导入时就没读到字节）",
    "thumbnail_missing": "这份清单里这一件没有配到部件图",
    "thumbnail_not_saved": "这件有部件图引用，但字节还没入库：重新导入一次权威清单即可",
}
BYTES_COPY = ("这一件清单里有部件图，但这一次没取到字节（可能已被清理，"
              "也可能是接口暂时读不到）；刷新或重新导入权威清单可重建")

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


def read_text(path: pathlib.Path) -> str:
    return path.read_bytes().replace(b"\x00", b"").decode("utf-8", errors="replace")


def run_cases(name, cases, timeout: int = 60):
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, json.dumps(cases)],
        capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


def function_body(name: str) -> str:
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, "body"],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    return "" if payload.get("missing") else str(payload.get("body") or "")


def reason_text(reason):
    payload = run_cases(REASON_FN, [[reason]])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §2.1）" % REASON_FN)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (REASON_FN, row.get("error")))
    return row.get("value")


def bytes_text():
    payload = run_cases(BYTES_FN, [[]])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §2.2）" % BYTES_FN)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (BYTES_FN, row.get("error")))
    return row.get("value")


# --------------------------------------------------------------------------- #
# T 组：三种"没有图"必须分家（纯函数）
# --------------------------------------------------------------------------- #
class TThumbnailReasonText(unittest.TestCase):
    def test_t1_bytes_unreadable_keeps_its_own_sentence(self):
        self.assertEqual(REASON_COPY["image_bytes_unreadable"],
                         reason_text("image_bytes_unreadable"),
                         "这条既有文案逐字不变（Spec §2.1）")

    def test_t2_missing_says_this_part_has_no_picture(self):
        self.assertEqual(REASON_COPY["thumbnail_missing"],
                         reason_text("thumbnail_missing"),
                         "这条既有文案逐字不变（Spec §2.1）")

    def test_t3_not_saved_says_reimport_can_fix_it(self):
        self.assertEqual(REASON_COPY["thumbnail_not_saved"],
                         reason_text("thumbnail_not_saved"),
                         "这条既有文案逐字不变（Spec §2.1）")

    def test_t4_out_of_table_code_is_exposed_not_blamed_on_import(self):
        got = reason_text("thumbnail_bytes_missing")
        self.assertEqual("部件图读不到（thumbnail_bytes_missing）", got,
                         "表外码照实暴露（Spec §2.1）")
        for forbidden in ("重新导入", "没入库", "没有配图"):
            self.assertNotIn(forbidden, got,
                             "表外码不许被断言成'没导入/没配图'：%s" % forbidden)

    def test_t5_no_reason_means_no_sentence(self):
        for value in ("", None, "   "):
            self.assertEqual("", reason_text(value),
                             "没有原因码就不许拼出这句话（%r）" % (value,))


# --------------------------------------------------------------------------- #
# T 组：取不到字节那一句 + 纯函数纪律
# --------------------------------------------------------------------------- #
class TBytesFailureText(unittest.TestCase):
    def test_t6_pure_functions_have_no_dom(self):
        for name in (REASON_FN, BYTES_FN):
            body = function_body(name)
            self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §2.1/§2.2）" % name)
            for token in ("document.", "window.", "fetch(", "localStorage"):
                self.assertNotIn(token, body, "%s 必须是纯函数" % name)
        self.assertEqual(BYTES_COPY, bytes_text(),
                         "取不到字节要有自己的那句话（Spec §2.2）")
        for forbidden in ("没有配图", "还没入库", "没有部件图"):
            self.assertNotIn(forbidden, bytes_text(),
                             "取不到字节 ≠ 没有配图 ≠ 还没入库（Spec §0）")


# --------------------------------------------------------------------------- #
# T 组：接线（清单侧用纯函数 + <img> 接住 onerror）
# --------------------------------------------------------------------------- #
class TWiring(unittest.TestCase):
    def test_t7_panel_uses_the_pure_function_and_guards_the_img(self):
        body = function_body("openPackagingBusinessPart")
        self.assertTrue(body, "app.js 缺少 openPackagingBusinessPart()")
        self.assertIn(REASON_FN + "(", body,
                      "清单侧的三种 reason 必须走纯函数，不许在面板里另抄一份（Spec §2.3）")
        self.assertNotIn('? "工作簿里的部件图读不出来', body,
                         "面板里那份内联三元式必须删掉（Spec §2.3）")
        self.assertNotIn("部件图还没入库（重新导入权威清单即可）", body,
                         "那条宽泛兜底必须删掉（Spec §2.3）")
        self.assertIn("<img", body, "可用时的 <img> 不许被换成别的渲染方式")
        self.assertIn("onerror", body, "<img> 必须接住取不到字节这件事（Spec §2.3）")
        self.assertIn("qqThumbBytesUnavailable", body,
                      "取不到字节要用自己的 data- 钩子（Spec §2.3）")
        self.assertIn(BYTES_FN, body,
                      "取不到字节时说的话必须来自纯函数（Spec §2.3）")


# --------------------------------------------------------------------------- #
# T 组（护栏）：既有的图本体渲染与三条文案一个字不改
# --------------------------------------------------------------------------- #
class TExistingRenderUnchanged(unittest.TestCase):
    def test_t8_existing_literals_verbatim(self):
        src = read_text(APP_JS)
        for literal in ('<img class="packaging-business-thumb"',
                        "mediaUrl(packagingBusinessPartThumbnailUrl(",
                        REASON_COPY["image_bytes_unreadable"],
                        REASON_COPY["thumbnail_missing"]):
            self.assertIn(literal, src,
                          "既有部件图渲染与两条既有文案逐字不变：%s" % literal)
        for name in (APP_JS, ROOT / "tech_app" / "frontend" / "index.html"):
            self.assertIn("packagingPartThumbnail", read_text(name),
                          "缩略图容器 id 不许改：%s" % name.name)


if __name__ == "__main__":
    unittest.main()

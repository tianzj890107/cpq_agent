"""红测：图纸推导出来的清单整版没有部件图，件级不许逐件说成「这一件没有配到部件图」。

Spec：`docs/specs/packaging-part-thumbnail-absence-must-name-its-source.md`

现状缺口（代码级，都可指到行）：
  · `packaging_parts.py:3943-3949 _business_part_thumbnail()`：只要 `thumbnail_ref` 为空就写死
    `"reason": "thumbnail_missing"` —— 那是**工作簿世界**的码（原文「这份权威清单里这一件没有配到部件图」）；
  · 图纸推导的清单（`packaging_business_part_resolver.py:1571 derived_from_drawing=True`）的行
    **从来没有 `thumbnail_ref` 这个键** ⇒ 28 行每行都拿到 `thumbnail_missing`，右栏点谁都同一句；
  · `main.py:7638` 与 `app.js:3563` 各抄了一份那句话，前端再写码也不会跟着说；
  · 真因是「这一版清单的来源（图纸）里根本没有部件图」，不是「这一件自己缺图」——
    两个世界必须分码、分句。

纪律：后端只调纯函数（不起服务、不发 HTTP、不连 PG / SQLite、不写业务数据）；
前端 `node -e` 抽具名函数体执行（纯函数）+ 源码守卫。
禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
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
MAIN_PY = ROOT / "tech_app" / "backend" / "main.py"

REASON_FN = "packagingBusinessThumbnailReasonText"
DISCLOSURE_FN = "packagingAuthorityDisclosureLines"

#: 新码与它的那句话（Spec §2.1/§2.2，逐字）。
NEW_CODE = "thumbnail_source_has_none"
NEW_SENTENCE = ("这一版清单来自图纸推导，图纸本身不带部件图；要按行看部件图，需先导入权威清单。")

#: 前端那份工作簿世界既有三码的句子（Spec §2.2：逐字不许动）。
WORKBOOK_COPY = {
    "image_bytes_unreadable": "工作簿里的部件图读不出来（导入时就没读到字节）",
    "thumbnail_missing": "这份清单里这一件没有配到部件图",
    "thumbnail_not_saved": "这件有部件图引用，但字节还没入库：重新导入一次权威清单即可",
}

#: 服务端那份（`main.py PACKAGING_THUMBNAIL_REASON_COPY`）的工作簿世界三码——两份各是各的
#: 字面（服务端 `thumbnail_missing` 多一个「权威」），本批**各自逐字**不许动。
SERVER_WORKBOOK_COPY = {
    "image_bytes_unreadable": "工作簿里的部件图读不出来（导入时就没读到字节）",
    "thumbnail_missing": "这份权威清单里这一件没有配到部件图",
    "thumbnail_not_saved": "这件有部件图引用，但字节还没入库：重新导入一次权威清单即可",
}

#: 那句话只许出现在工作簿分支（Spec §2.4）。
WORKBOOK_ONLY_LITERAL = "这份清单里这一件没有配到部件图"

#: 工作簿世界的清单级句式（Spec §2.5：逐字不许动）。
WORKBOOK_LIST_LINE = "部件图：1 件都没配到部件图（这一版清单的图没有归属）。"

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


def call_one(name: str, args, label: str):
    payload = run_cases(name, [args])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少纯函数 %s()（Spec §2.2/§2.5）" % name)
    row = payload["results"][0]
    if not row.get("ok"):
        raise AssertionError("%s() 抛异常：%s" % (name, row.get("error")))
    return row.get("value")


def reason_text(reason):
    return call_one(REASON_FN, [reason], "件级文案")


def list_lines(doc):
    return call_one(DISCLOSURE_FN, [doc], "清单级披露")


# --------------------------------------------------------------------------- #
# 夹具：两个世界的权威产物（都不碰磁盘）
# --------------------------------------------------------------------------- #
def derived_authority():
    """图纸推导出来的清单：解析器的行形状，**没有** thumbnail_ref。"""
    return {
        "parts": [{
            "sequence_no": 1, "business_part_code": "DWG-P01", "name": "面纸",
            "length_mm": 200.0, "width_mm": 300.0, "material_text": "", "process_text": "",
            "name_from_drawing": True, "truth_state": "observed",
        }],
        "derived_from_drawing": True,
        "gold_standard_used": False,
        "refused_sources": ["customer_workbook"],
        "source": {},
    }


def workbook_authority(ref=""):
    """权威清单工作簿来的行：有 `thumbnail_ref` 才有图这一栏。"""
    row = {
        "sequence_no": 1, "business_part_code": "P01", "name": "面纸",
        "length_mm": 200.0, "width_mm": 300.0, "material_text": "", "process_text": "",
    }
    if ref:
        row["thumbnail_ref"] = ref
        row["thumbnail_source"] = "anchor_row"
    return {
        "parts": [row],
        "derived_from_drawing": False,
        "gold_standard_used": False,
        "refused_sources": [],
        "source": {},
        "stats": {"part_total": 1, "image_total": 1 if ref else 0,
                  "thumbnail_bound_total": 1 if ref else 0},
    }


def saved_thumbnail(ref="图-1"):
    return {"by_ref": {ref: {"ref": ref, "sha256": "a" * 64, "media_type": "image/png",
                             "bytes": 12, "key": "k/%s.png" % ("a" * 8), "available": True,
                             "unavailable": ""}}}


def parts_doc(authority, thumbnails=None):
    from tech_app.backend.services import packaging_parts
    return packaging_parts.business_parts_document(authority, {}, thumbnails=thumbnails)


def thumbnail_of(authority, code, thumbnails=None):
    from tech_app.backend.services import packaging_parts
    doc = parts_doc(authority, thumbnails)
    return packaging_parts.authority_thumbnail_of("p1", doc, code)


# --------------------------------------------------------------------------- #
# A 组：件级原因码必须把两个世界分开（后端纯函数）
# --------------------------------------------------------------------------- #
class APerPartReasonCode(unittest.TestCase):
    def test_a1_图纸推导的件级码是来源码_不是工作簿码(self):
        row = parts_doc(derived_authority())["business_parts"][0]
        self.assertEqual(NEW_CODE, row["thumbnail"]["reason"],
                         "图纸推导的清单整版没有部件图，件级要报『来源里就没有』（Spec §2.1）")

    def test_a2_图纸推导的件级码不许是_thumbnail_missing(self):
        row = parts_doc(derived_authority())["business_parts"][0]
        self.assertNotEqual("thumbnail_missing", row["thumbnail"]["reason"],
                            "『这一件没有配到图』是工作簿世界的话术（Spec §2.1）")
        self.assertFalse(row["thumbnail"]["available"])

    def test_a3_新码进闭集(self):
        from tech_app.backend.services import packaging_parts
        self.assertIn(NEW_CODE, packaging_parts.THUMBNAIL_REASONS,
                      "新码必须进 THUMBNAIL_REASONS 闭集，否则会被兜底改写（Spec §2.1）")

    def test_a4_工作簿无引用仍是_thumbnail_missing_逐字(self):
        row = parts_doc(workbook_authority())["business_parts"][0]
        self.assertEqual("thumbnail_missing", row["thumbnail"]["reason"],
                         "工作簿世界的码逐字不变（Spec §2.1）")

    def test_a5_工作簿有引用没字节仍是_thumbnail_not_saved_逐字(self):
        row = parts_doc(workbook_authority(ref="图-1"))["business_parts"][0]
        self.assertEqual("thumbnail_not_saved", row["thumbnail"]["reason"],
                         "工作簿世界的码逐字不变（Spec §2.1）")

    def test_a6_工作簿有引用有字节就是可用(self):
        row = parts_doc(workbook_authority(ref="图-1"),
                        saved_thumbnail())["business_parts"][0]
        self.assertTrue(row["thumbnail"]["available"])
        self.assertEqual("", row["thumbnail"]["reason"],
                         "取得到字节就没有原因要报（Spec §2.1）")

    def test_a7_读端点对图纸推导的件回同一个新码(self):
        got = thumbnail_of(derived_authority(), "DWG-P01")
        self.assertFalse(got["found"])
        self.assertEqual(NEW_CODE, got["reason"],
                         "读端点不许把『来源里没有』说成『这一件没配到』（Spec §2.3）")

    def test_a8_读端点对工作簿无引用仍是_thumbnail_missing_逐字(self):
        got = thumbnail_of(workbook_authority(), "P01")
        self.assertFalse(got["found"])
        self.assertEqual("thumbnail_missing", got["reason"],
                         "工作簿世界的码逐字不变（Spec §2.3）")


# --------------------------------------------------------------------------- #
# B 组：服务端那份文案（源码守卫）
# --------------------------------------------------------------------------- #
def reason_copy_block() -> str:
    text = read_text(MAIN_PY)
    at = text.index("PACKAGING_THUMBNAIL_REASON_COPY = {")
    end = text.index("\n}", at)
    return text[at:end]


def copy_sentence(code: str) -> str:
    block = reason_copy_block()
    import re
    hit = re.search(r'"%s":\s*"([^"]*)"' % re.escape(code), block)
    if not hit:
        raise AssertionError("main.py 的 PACKAGING_THUMBNAIL_REASON_COPY 里没有 %s（Spec §2.2）" % code)
    return hit.group(1)


class BServerCopy(unittest.TestCase):
    def test_b1_服务端有新码且逐字(self):
        self.assertEqual(NEW_SENTENCE, copy_sentence(NEW_CODE),
                         "服务端权威文案要与 Spec §2.2 逐字一致")

    def test_b2_新句必须点名来源与出路(self):
        sentence = copy_sentence(NEW_CODE)
        for token in ("图纸推导", "权威清单"):
            self.assertIn(token, sentence, "这句要说清来源与出路：%s" % token)
        for forbidden in ("这一件", "这一种"):
            self.assertNotIn(forbidden, sentence,
                             "整版没有图 ≠ 这一件缺图（Spec §2.2）：%s" % forbidden)

    def test_b3_工作簿三码逐字不变(self):
        for code, sentence in SERVER_WORKBOOK_COPY.items():
            self.assertEqual(sentence, copy_sentence(code),
                             "工作簿世界的三码逐字不变（Spec §2.2）：%s" % code)


# --------------------------------------------------------------------------- #
# C 组：前端那份文案（node 真跑纯函数）
# --------------------------------------------------------------------------- #
class CFrontendReasonText(unittest.TestCase):
    def test_c1_前端新码逐字同一句(self):
        self.assertEqual(NEW_SENTENCE, reason_text(NEW_CODE),
                         "前端纯函数要与服务端同码同句（Spec §2.2）")

    def test_c2_工作簿三码逐字不变(self):
        for code, sentence in WORKBOOK_COPY.items():
            self.assertEqual(sentence, reason_text(code),
                             "工作簿世界的三码逐字不变（Spec §2.2）：%s" % code)

    def test_c3_表外码照实暴露(self):
        self.assertEqual("部件图读不到（thumbnail_bytes_missing）",
                         reason_text("thumbnail_bytes_missing"),
                         "表外码照实暴露（Spec §2.2）")
        self.assertEqual("", reason_text(""))

    def test_c4_纯函数不碰_dom(self):
        body = function_body(REASON_FN)
        self.assertTrue(body, "app.js 缺少纯函数 %s()（Spec §2.2）" % REASON_FN)
        for token in ("document.", "window.", "fetch(", "localStorage"):
            self.assertNotIn(token, body, "%s 必须是纯函数" % REASON_FN)

    def test_c5_新句不许读成_这一件缺图(self):
        got = reason_text(NEW_CODE)
        for token in ("图纸推导", "权威清单"):
            self.assertIn(token, got, "这句要说清来源与出路：%s" % token)
        self.assertNotIn("这一件", got, "整版没有图 ≠ 这一件缺图（Spec §2.2）")


# --------------------------------------------------------------------------- #
# D 组：接线（那句话只许在工作簿分支 + 清单级要说清为什么）
# --------------------------------------------------------------------------- #
class DWiring(unittest.TestCase):
    def test_d1_那句话在源码里只出现一次(self):
        count = read_text(APP_JS).count(WORKBOOK_ONLY_LITERAL)
        self.assertEqual(1, count,
                         "『%s』只许出现在 %s() 的 thumbnail_missing 分支（Spec §2.4）"
                         % (WORKBOOK_ONLY_LITERAL, REASON_FN))

    def test_d2_面板走纯函数(self):
        body = function_body("openPackagingBusinessPart")
        self.assertTrue(body, "app.js 缺少 openPackagingBusinessPart()")
        self.assertIn(REASON_FN + "(", body,
                      "面板取不到图时必须走纯函数，不许另抄一份（Spec §2.4）")

    def test_d3_图纸推导单的清单级句要点名来源与出路(self):
        doc = {"derived_from_drawing": True,
               "authority": {"stats": {"part_total": 28},
                             "thumbnail": {"bound_total": 0, "bound_by": ""},
                             "skipped": []}}
        lines = [str(item) for item in (list_lines(doc) or [])]
        hit = [line for line in lines if "部件图" in line]
        self.assertEqual(1, len(hit), "图纸推导单要有且只有一句部件图披露：%r" % lines)
        for token in ("图纸推导", "权威清单"):
            self.assertIn(token, hit[0], "清单级那句要说清来源与出路：%s" % token)

    def test_d4_工作簿单的清单级句逐字不变(self):
        doc = {"derived_from_drawing": False,
               "authority": {"stats": {"part_total": 1},
                             "thumbnail": {"bound_total": 0, "bound_by": ""},
                             "skipped": []}}
        lines = [str(item) for item in (list_lines(doc) or [])]
        self.assertIn(WORKBOOK_LIST_LINE, lines,
                      "工作簿世界的清单级句式逐字不变（Spec §2.5）")


if __name__ == "__main__":
    unittest.main()

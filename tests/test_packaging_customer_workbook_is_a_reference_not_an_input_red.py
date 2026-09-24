"""红测：客户那张报价资料表不是「权威清单」、永远不是输入——全仓改名与命名收口。

Spec：`docs/specs/packaging-customer-workbook-is-a-reference-not-an-input.md`

现状缺口（工作副本只读，逐项可复核）：
  · `tech_app/` 里 `权威清单` 96 处、`权威资料` 15、`权威尺寸` 41、`权威出处` 7、`权威原文` 9、
    `权威工作簿` 2、`权威行` 5 —— 名字还在说这张表是"权威"，与代码口径（导入只落「对答案参照」、
    结果文档只由图纸推导产生）**相反**；
  · 标识符 `authority` 227 行，模块还叫 `packaging_part_authority.py`；
  · 最危险的两句把客户资料写成兜底输入：`main.py:7552`「推不出来时业务部件清单要由权威清单导入」、
    `app.js:2866/3074/3578/3581/3591`「…还没导入权威清单 / …需先导入权威清单」。

纪律：源码扫描 + `node -e` 抽纯函数；不起服务、不发 HTTP、不连 PG / 34、不写业务数据。
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

TECH_APP = ROOT / "tech_app"
APP_JS = TECH_APP / "frontend" / "app.js"
PARTS_PY = TECH_APP / "backend" / "services" / "packaging_parts.py"
BOM_PY = TECH_APP / "backend" / "services" / "packaging_bom.py"
MAIN_PY = TECH_APP / "backend" / "main.py"
SERVICES = TECH_APP / "backend" / "services"

OLD_MODULE = SERVICES / "packaging_part_authority.py"
NEW_MODULE = SERVICES / "packaging_reference_workbook.py"

#: 本批禁用词（Spec §2.1）：只指"客户那张表"那一组。
FORBIDDEN = ("权威清单", "权威资料", "权威尺寸", "权威出处", "权威原文", "权威工作簿", "权威行")

#: 另一个意思的「权威」，本批**不许**动（Spec §2.2）：文件 → 必须仍存在的词。
KEEP = {
    "backend/services/requirement_service.py": "权威图纸",
    "backend/services/report_workflow.py": "权威结论",
    "frontend/quick-quote-panel.js": "权威费率",
    "backend/services/file_preflight.py": "权威来源",
    "backend/services/packaging_cost.py": "权威数据源",
    "backend/services/vision.py": "权威信息",
}

#: 件级「没有部件图」那句改名后的逐字（只把"权威清单"换成"对照表"）。
NEW_THUMBNAIL_SENTENCE = ("这一版清单来自图纸推导，图纸本身不带部件图；要按行看部件图，需先导入对照表。")

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
const cases = JSON.parse(process.argv[4]);
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


def source_files():
    out = []
    for path in sorted(TECH_APP.rglob("*")):
        if path.suffix in (".py", ".js", ".html") and path.is_file():
            out.append(path)
    return out


def hits(token: str):
    """词 → [(相对路径, 次数)]（只报有命中的文件，降序）。"""
    found = []
    for path in source_files():
        text = read_text(path)
        count = text.count(token)
        if count:
            found.append((str(path.relative_to(ROOT)), count))
    found.sort(key=lambda item: (-item[1], item[0]))
    return found


def report(token: str) -> str:
    found = hits(token)
    if not found:
        return ""
    total = sum(count for _, count in found)
    head = "、".join("%s×%d" % item for item in found[:5])
    return "还有 %d 处：%s%s" % (total, head, "…" if len(found) > 5 else "")


def run_js(fn_name: str, cases):
    proc = subprocess.run(
        ["node", "-e", EXTRACT_JS, "-", str(APP_JS), fn_name, json.dumps(cases)],
        capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    return json.loads(proc.stdout.strip().splitlines()[-1])


# --------------------------------------------------------------------------- #
# A 组：7 个禁用词在 tech_app 源码里必须 0 处
# --------------------------------------------------------------------------- #
class AForbiddenWords(unittest.TestCase):
    def _assert_gone(self, token: str):
        found = hits(token)
        self.assertEqual([], found, "「%s」还没改完（Spec §2.1/§2.2）：%s" % (token, report(token)))

    def test_a1_权威清单(self):
        self._assert_gone("权威清单")

    def test_a2_权威资料(self):
        self._assert_gone("权威资料")

    def test_a3_权威尺寸(self):
        self._assert_gone("权威尺寸")

    def test_a4_权威出处(self):
        self._assert_gone("权威出处")

    def test_a5_权威原文(self):
        self._assert_gone("权威原文")

    def test_a6_权威工作簿(self):
        self._assert_gone("权威工作簿")

    def test_a7_权威行(self):
        self._assert_gone("权威行")


# --------------------------------------------------------------------------- #
# B 组：替词到位（用户可见文案里出现「对照表」）
# --------------------------------------------------------------------------- #
class BReplacementWords(unittest.TestCase):
    def test_b1_服务端那句要用新词(self):
        text = read_text(MAIN_PY)
        self.assertIn(NEW_THUMBNAIL_SENTENCE, text,
                      "件级「没有部件图」那句要改成新词（Spec §2.1）")

    def test_b2_前端那句要同码同句(self):
        text = read_text(APP_JS)
        self.assertIn(NEW_THUMBNAIL_SENTENCE, text,
                      "前端纯函数要与服务端同句（Spec §2.1）")


# --------------------------------------------------------------------------- #
# C 组：另一个意思的「权威」一个都不许被误改（护栏）
# --------------------------------------------------------------------------- #
class CKeepOtherMeanings(unittest.TestCase):
    def _assert_kept(self, relative: str, token: str):
        path = TECH_APP / relative
        self.assertTrue(path.exists(), "少了文件 %s" % relative)
        self.assertIn(token, read_text(path),
                      "%s 的「%s」是另一个意思，本批不许动（Spec §2.2）" % (relative, token))

    def test_c1_权威图纸(self):
        self._assert_kept("backend/services/requirement_service.py", "权威图纸")

    def test_c2_权威结论(self):
        self._assert_kept("backend/services/report_workflow.py", "权威结论")

    def test_c3_权威费率(self):
        self._assert_kept("frontend/quick-quote-panel.js", "权威费率")

    def test_c4_权威来源(self):
        self._assert_kept("backend/services/file_preflight.py", "权威来源")

    def test_c5_权威数据源(self):
        self._assert_kept("backend/services/packaging_cost.py", "权威数据源")

    def test_c6_权威信息(self):
        self._assert_kept("backend/services/vision.py", "权威信息")


# --------------------------------------------------------------------------- #
# D 组：标识符改名与旧数据兼容
# --------------------------------------------------------------------------- #
IDENTIFIER_TOKEN = re.compile(r"\bauthority\b")


def identifier_hits(path: pathlib.Path):
    return len(IDENTIFIER_TOKEN.findall(read_text(path)))


class DIdentifiersAndCompat(unittest.TestCase):
    def test_d1_前端没有_authority_标识符(self):
        count = identifier_hits(APP_JS)
        self.assertEqual(0, count, "app.js 还有 %d 处 `authority` 标识符（Spec §2.3）" % count)

    def test_d2_服务层改名到位(self):
        from tech_app.backend.services import packaging_parts
        self.assertFalse(hasattr(packaging_parts, "authority_thumbnail_of"),
                         "旧函数名 authority_thumbnail_of 必须消失（Spec §2.3）")
        self.assertTrue(hasattr(packaging_parts, "reference_thumbnail_of"),
                        "新函数名 reference_thumbnail_of 必须存在（Spec §2.3）")
        count = identifier_hits(PARTS_PY)
        self.assertEqual(0, count, "packaging_parts.py 还有 %d 处 `authority`（Spec §2.3）" % count)

    def test_d3_模块改名(self):
        self.assertFalse(OLD_MODULE.exists(),
                         "旧模块 packaging_part_authority.py 必须删掉（Spec §2.3）")
        self.assertTrue(NEW_MODULE.exists(),
                        "新模块 packaging_reference_workbook.py 必须存在（Spec §2.3）")

    def test_d4_旧的_authority_块仍读得出来(self):
        from tech_app.backend.services import packaging_parts
        fn = getattr(packaging_parts, "business_part_reference_block", None)
        self.assertIsNotNone(fn, "缺纯函数 business_part_reference_block()（Spec §2.4）")
        legacy = {"authority": {"length_mm": 200, "width_mm": 300, "material_text": "225G铜版底PET光银"}}
        self.assertEqual(200, fn(legacy).get("length_mm"), "旧 `authority` 块必须仍读得出（Spec §2.4）")
        fresh = {"reference": {"length_mm": 210, "width_mm": 310, "material_text": "300G白卡"}}
        self.assertEqual(210, fn(fresh).get("length_mm"), "新 `reference` 块优先（Spec §2.4）")

    def test_d5_BOM_旧尺寸出处仍识别(self):
        text = read_text(BOM_PY)
        self.assertIn('"authority_workbook"', text,
                      "旧 BOM 的 size_source_json.kind 旧值必须仍被识别（Spec §2.4）")
        self.assertIn('"reference_workbook"', text,
                      "新写入要用新值 reference_workbook（Spec §2.4）")
        self.assertIn('"packaging_business_parts_authority"', text,
                      "旧 BOM 行的来源值仍必须被识别（Spec §2.4）")

    def test_d6_护栏_导入仍只落参照不是输入(self):
        text = read_text(MAIN_PY)
        self.assertIn("save_business_parts_reference", text,
                      "客户工作簿仍只落「对答案参照」，不许变成输入（Spec §2.5）")
        from tech_app.backend.services import packaging_parts
        self.assertEqual((), tuple(packaging_parts.BUSINESS_REFERENCE_FEEDS),
                         "参照文档一个下游都不喂（Spec §2.5）")


if __name__ == "__main__":
    unittest.main()

"""红测：客户工作簿要能从页面上**直接导入**，并如实记下出处（Spec `packaging-authority-workbook-upload.md`）。

现状缺口（代码级，可指到行）：
  · `tech_app/frontend/app.js:2337-2352` 的 `importPackagingBusinessParts()` 只有一条路：
    `window.prompt("权威清单工作簿在服务器上的路径（.xlsx）")` → `{workbook_path: path}`；
    前端源码里 `content_base64` / `file_name` / `FileReader` / `readAsDataURL` **0 处** ——
    客户给的 xlsx 必须先被人手工放到服务器文件系统上；
  · `tech_app/backend/main.py:7275-7281` 收下 `content_base64` 后直接 `b64decode`：
    **没有大小上限**，也没有把文件名带下去；
  · `tech_app/backend/services/packaging_part_authority.py:313-314` 对字节来源
    `source["file"] = ""`、从来没有 `file_bytes` —— 落库的清单说不出"来自哪个文件、多大"。

纪律：`node -e` 抽顶层具名函数真跑（纯函数）+ 源码守卫 + `node --check`；后端用**打桩**的
导入器与假后勤（不落库、不连 PG / 34、不发 HTTP、不写业务数据）。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import base64
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 本文件会 import main（路由要真调）：给父进程一个独立的数据目录，别写进仓库数据目录。
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="cpq-workbook-upload-")
os.environ.setdefault("AUTH_ENABLED", "false")

from tech_app.backend import main                                        # noqa: E402
from tech_app.backend.services import packaging_part_authority as authority  # noqa: E402

APP_JS = ROOT / "tech_app" / "frontend" / "app.js"

PID = "testpid00001"
USER = {"username": "PE1", "role": "process_manager"}
SHEET = "零部件排版工艺"
FILE_NAME = "酒盒 报价资料.xlsx"

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
const extras = JSON.parse(process.argv[5] || "[]");
const fn = extract(name);
if (!fn) { console.log(JSON.stringify({ missing: true })); process.exit(0); }
if (mode === "body") { console.log(JSON.stringify({ missing: false, body: fn })); process.exit(0); }
const cases = JSON.parse(mode);
/* 被抽的函数若复用了同文件的另一个纯函数，一起 eval（Spec §C3：规则只写一处）。 */
eval(extras.map(extract).filter(Boolean).concat([fn]).join("\n"));
const out = [];
for (const args of cases) {
  const call = name + "(" + args.map(a => JSON.stringify(a)).join(", ") + ")";
  try { out.push({ ok: true, value: eval(call) }); }
  catch (e) { out.push({ ok: false, error: String((e && e.message) || e) }); }
}
console.log(JSON.stringify({ missing: false, results: out }));
"""


def run_cases(name: str, cases, also=()):
    """`cases` 是**实参表**：每一项是"一次调用的实参列表"；`also` 是被抽函数依赖的同文件纯函数。"""
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name,
                           json.dumps(cases), json.dumps(list(also))],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少顶层纯函数 %s()（Spec §C3）" % name)
    for index, item in enumerate(payload["results"]):
        if not item.get("ok"):
            raise AssertionError("%s() 第 %d 个入参抛异常：%s（Spec §C3）"
                                 % (name, index, item.get("error")))
    return [item["value"] for item in payload["results"]]


def function_body(name: str) -> str:
    proc = subprocess.run(["node", "-e", EXTRACT_JS, "-", str(APP_JS), name, "body"],
                          capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：%s" % (proc.stderr or proc.stdout)[:800])
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    if payload.get("missing"):
        raise AssertionError("app.js 缺少具名函数 %s()（Spec §C3）" % name)
    return payload["body"]


class _Patch:
    def __init__(self, *pairs):
        self.pairs = pairs
        self.saved = []
        self._missing = object()

    def __enter__(self):
        for owner, name, value in self.pairs:
            self.saved.append((owner, name, getattr(owner, name, self._missing)))
            setattr(owner, name, value)
        return self

    def __exit__(self, *exc):
        for owner, name, old in reversed(self.saved):
            if old is self._missing:
                try:
                    delattr(owner, name)
                except AttributeError:
                    pass
            else:
                setattr(owner, name, old)
        return False


def workbook_bytes(rows=None):
    """合成一份最小工作簿（真 xlsx 字节；不依赖客户样本）。"""
    from openpyxl import Workbook
    body = rows or [("序号", "名称", "长(mm)", "宽(mm)", "材料"),
                    (1, "礼盒面纸", 300, 200, "350G玖龙粉灰"),
                    (2, "礼盒底纸", 300, 200, "350G玖龙粉灰")]
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET
    for row_index, row in enumerate(body, start=1):
        for column_index, value in enumerate(row, start=1):
            sheet.cell(row_index, column_index, value)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def has_openpyxl():
    try:
        import openpyxl  # noqa: F401
        return True
    except Exception:      # noqa: BLE001
        return False


def _body(**kwargs):
    values = {"workbook_path": "", "content_base64": "", "sheet": "", "bind": False,
              "file_name": ""}
    values.update(kwargs)
    return type("Body", (), values)()


class _ImportSpy:
    """打桩的 `import_workbook`：只记下收到的入参，不解析任何工作簿。"""

    def __init__(self, parts=2):
        self.calls = []
        self.parts = parts

    def __call__(self, source, **kwargs):
        self.calls.append({"source": source, "kwargs": dict(kwargs)})
        return {"engine_version": "packaging-authority/1",
                "parts": [{"business_part_code": "PART-P%02d" % index, "name": "件%d" % index,
                           "source": {"row": index + 1}} for index in range(1, self.parts + 1)],
                "skipped": [], "images": [],
                "source": {"file": FILE_NAME, "sheet": SHEET, "file_hash": "hash-1",
                           "code_prefix": "PART"},
                "stats": {"part_total": self.parts, "image_total": 0, "skipped_total": 0,
                          "image_bytes_total": 0},
                "unavailable": []}


def call_import(body, spy, *, limit=None):
    """直接调导入路由（打桩导入器 + 假后勤），返回 (返回值, 异常, 打桩记录)。"""
    patches = [
        (main.packaging_part_authority, "import_workbook", spy),
        (main.packaging_parts, "load_parts", lambda pid: {}),
        (main.packaging_parts, "save_authority_thumbnails",
         lambda pid, record: {"written": 0, "reused": 0}),
        (main.packaging_parts, "save_business_parts",
         lambda pid, doc: dict(doc, business_parts_id="biz-1",
                               stats={"business_part_total": spy.parts, "bound_total": 0},
                               source={"authority_file_hash": "hash-1",
                                       "authority_file": FILE_NAME})),
        (main.store, "audit", lambda *args, **kwargs: None),
        (main, "_workflow_project", lambda pid: {"project_id": pid}),
    ]
    if limit is not None:
        patches.append((main, "MAX_UPLOAD_BYTES", limit))
    with _Patch(*patches):
        try:
            return {"out": main.import_packaging_business_parts(PID, body, USER),
                    "error": None, "calls": spy.calls}
        except Exception as caught:      # noqa: BLE001 — 路由异常即 HTTPException，这里抓住判据
            return {"out": None, "error": caught, "calls": spy.calls}


# --------------------------------------------------------------------------- #
# A 组：前端三个纯函数（上传载荷的构造规则）
# --------------------------------------------------------------------------- #
class AUploadPayload(unittest.TestCase):
    def test_a1_file_name_is_only_a_name(self):
        cases = [
            ("C:\\Users\\客户\\酒盒 报价资料.xlsx", "酒盒 报价资料.xlsx"),
            ("/tmp/upload/酒盒 报价资料.xlsx", "酒盒 报价资料.xlsx"),
            ("  礼盒.xlsx  ", "礼盒.xlsx"),
            ("a\u0000b\u0007c.xlsx", "abc.xlsx"),
            ("", ""),
            (None, ""),
            (123, ""),
        ]
        for raw, expected in cases:
            self.assertEqual(expected, run_cases("packagingAuthorityFileName", [[raw]])[0],
                             "文件名净化规则（Spec §C3）：%r" % (raw,))

    def test_a2_base64_only_from_a_data_url(self):
        cases = [
            ("data:application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;base64,QUJD",
             "QUJD"),
            ("data:application/octet-stream;base64,QU\nJD\n", "QUJD"),
            ("data:text/plain,abc", ""),
            ("QUJD", ""),
            ("", ""),
            (None, ""),
        ]
        for raw, expected in cases:
            self.assertEqual(expected, run_cases("packagingAuthorityBase64Of", [[raw]])[0],
                             "只认 `data:*;base64,` 这种载荷（Spec §C3）：%r" % (raw,))

    def test_a3_body_prefers_the_server_path(self):
        out = run_cases("packagingAuthorityImportBody",
                        [("/srv/x.xlsx", FILE_NAME, "data:x;base64,QUJD")])[0]
        self.assertEqual({"workbook_path"}, set(out.keys()),
                         "服务器路径优先、逐字不变（Spec §C3）")
        self.assertEqual("/srv/x.xlsx", out.get("workbook_path"))
        self.assertNotIn("content_base64", out, "有路径就不发字节（Spec §C3）")

    def test_a4_body_uses_bytes_when_only_a_file_is_picked(self):
        out = run_cases("packagingAuthorityImportBody",
                        [("", FILE_NAME, "data:x;base64,QUJD")],
                        also=("packagingAuthorityBase64Of", "packagingAuthorityFileName"))[0]
        self.assertEqual("QUJD", out.get("content_base64"), "文件要变成 base64（Spec §C3）")
        self.assertEqual(FILE_NAME, out.get("file_name"), "文件名要一起带上（Spec §C3）")
        self.assertNotIn("workbook_path", out)

    def test_a5_body_is_null_when_nothing_to_send(self):
        for args in (("", "", ""), (None, None, None), ("  ", "", "data:text/plain,abc")):
            self.assertIsNone(run_cases("packagingAuthorityImportBody", [args],
                                        also=("packagingAuthorityBase64Of",))[0],
                              "什么都没有 → null，不许编空载荷（Spec §C3）：%r" % (args,))


# --------------------------------------------------------------------------- #
# B 组：出处（文件名 / 字节数）
# --------------------------------------------------------------------------- #
@unittest.skipUnless(has_openpyxl(), "缺 openpyxl：pip install openpyxl（根 requirements.txt 已声明）")
class BAuthoritySource(unittest.TestCase):
    def test_b1_bytes_with_a_file_name_are_recorded(self):
        data = workbook_bytes()
        out = authority.import_workbook(data, file_name=FILE_NAME)
        source = out.get("source") or {}
        self.assertEqual(FILE_NAME, source.get("file"),
                         "字节来源必须记下文件名（Spec §C1）")
        self.assertEqual(len(data), source.get("file_bytes"),
                         "必须记下工作簿字节数（Spec §C1）")
        self.assertTrue(source.get("file_hash"), "指纹不变（Spec §C1）")
        self.assertTrue(out.get("parts"), "合成工作簿要能解析出部件行（本用例的前提）")

    def test_b2_bytes_without_a_file_name_stay_empty(self):
        data = workbook_bytes()
        source = authority.import_workbook(data).get("source") or {}
        self.assertEqual("", source.get("file"),
                         "没给文件名就只能空着（不许编）（Spec §C1）")
        self.assertEqual(len(data), source.get("file_bytes"))

    def test_b3_path_source_semantics_unchanged(self):
        directory = tempfile.mkdtemp(prefix="cpq-workbook-path-")
        path = os.path.join(directory, "客户 资料.xlsx")
        with open(path, "wb") as handle:
            handle.write(workbook_bytes())
        out = authority.import_workbook(path, file_name="冒充.xlsx")
        source = out.get("source") or {}
        self.assertEqual("客户 资料.xlsx", source.get("file"),
                         "路径来源的文件名仍取 basename，不许被 file_name 冒名（Spec §C1）")
        self.assertEqual(os.path.getsize(path), source.get("file_bytes"),
                         "路径来源也要给字节数（Spec §C1）")

    def test_b4_sheet_missing_branch_also_carries_the_source(self):
        data = workbook_bytes()
        out = authority.import_workbook(data, sheet="不存在的表", file_name=FILE_NAME)
        self.assertEqual("authority_sheet_missing",
                         ((out.get("unavailable") or [{}])[0]).get("code"))
        source = out.get("source") or {}
        self.assertEqual(FILE_NAME, source.get("file"), "找不到表的分支也要记文件名（Spec §C1）")
        self.assertEqual(len(data), source.get("file_bytes"))


# --------------------------------------------------------------------------- #
# C 组：路由（上限 / 透传 / 既有拒绝不变）
# --------------------------------------------------------------------------- #
class CImportRoute(unittest.TestCase):
    def test_c1_base64_is_decoded_and_file_name_passed_down(self):
        data = workbook_bytes() if has_openpyxl() else b"PK\x03\x04fake"
        raw = base64.b64encode(data).decode("ascii")
        spy = _ImportSpy()
        result = call_import(_body(content_base64=raw, file_name=FILE_NAME, sheet=SHEET), spy)
        self.assertIsNone(result["error"], "既有拒绝之外不许抛：%r" % (result["error"],))
        self.assertEqual(1, len(spy.calls), "必须恰好调一次导入器")
        call = spy.calls[0]
        self.assertEqual(data, bytes(call["source"]), "送下去的是解码后的字节（Spec §C2）")
        self.assertEqual(FILE_NAME, call["kwargs"].get("file_name"),
                         "文件名必须透传给导入器（Spec §C2）")
        self.assertEqual(SHEET, call["kwargs"].get("sheet"), "既有 sheet 透传不变（Spec §C2）")

    def test_c2_path_source_never_gets_a_file_name(self):
        spy = _ImportSpy()
        result = call_import(_body(workbook_path="/srv/资料.xlsx", file_name="冒充.xlsx"), spy)
        self.assertIsNone(result["error"], "%r" % (result["error"],))
        self.assertEqual(1, len(spy.calls))
        self.assertEqual("/srv/资料.xlsx", spy.calls[0]["source"],
                         "路径来源送下去的还是那个路径（Spec §C2）")
        self.assertEqual("", spy.calls[0]["kwargs"].get("file_name"),
                         "路径那一路不许被 file_name 冒名（Spec §C2/§C4）")

    def test_c3_oversize_base64_text_is_rejected_before_decoding(self):
        spy = _ImportSpy()
        raw = "A" * (4096 * 4 // 3 + 64)
        result = call_import(_body(content_base64=raw), spy, limit=4096)
        error = result["error"]
        self.assertIsNotNone(error, "超过上限必须被拒（Spec §C2）")
        self.assertEqual(413, getattr(error, "status_code", 0),
                         "超限是 413，不许说成 400 / 500（Spec §C2）")
        self.assertIn("上限", str(getattr(error, "detail", "")),
                      "文案要说清上限（Spec §C2）")
        self.assertEqual([], result["calls"], "超限时**不许**调导入器（Spec §C2）")

    def test_c4_oversize_decoded_bytes_are_rejected(self):
        spy = _ImportSpy()
        data = b"P" * 5000
        raw = base64.b64encode(data).decode("ascii")
        result = call_import(_body(content_base64=raw), spy, limit=4096)
        error = result["error"]
        self.assertIsNotNone(error, "解码后超过上限同样要拒（Spec §C2）")
        self.assertEqual(413, getattr(error, "status_code", 0))
        self.assertEqual([], result["calls"], "超限时**不许**调导入器（Spec §C2）")

    def test_c5_within_the_limit_still_imports(self):
        spy = _ImportSpy()
        raw = base64.b64encode(b"PK\x03\x04" + b"x" * 100).decode("ascii")
        result = call_import(_body(content_base64=raw), spy, limit=4096)
        self.assertIsNone(result["error"], "没超限不许被上限挡下（Spec §C2）")
        self.assertEqual(1, len(spy.calls))

    def test_c6_existing_rejections_unchanged(self):
        spy = _ImportSpy()
        empty = call_import(_body(), spy)
        self.assertEqual(400, getattr(empty["error"], "status_code", 0),
                         "什么都没给仍必须是 400（Spec §C2）")
        self.assertIn("workbook_path 或 content_base64",
                      str(getattr(empty["error"], "detail", "")),
                      "既有那句文案逐字保留（Spec §C2）")
        broken = call_import(_body(content_base64="!!!!not base64!!!!"), spy)
        self.assertEqual(400, getattr(broken["error"], "status_code", 0),
                         "解不开仍是 400（Spec §C2）")
        self.assertEqual([], broken["calls"])

    def test_c7_model_has_the_file_name_field(self):
        body = main.PackagingBusinessPartsImportAction()
        self.assertTrue(hasattr(body, "file_name"), "入参要有 file_name（Spec §C2）")
        self.assertEqual("", body.file_name, "缺省空串（Spec §C2）")


# --------------------------------------------------------------------------- #
# D 组：前端接线与护栏
# --------------------------------------------------------------------------- #
class DWiring(unittest.TestCase):
    SOURCE = APP_JS.read_text(encoding="utf-8")

    def test_d1_panel_has_a_file_picker(self):
        for token in ("data-qq-business-upload", 'type="file"', "readAsDataURL",
                      "packagingAuthorityImportBody", "content_base64", "file_name"):
            self.assertIn(token, self.SOURCE, "前端缺上传接线（Spec §C3）：%s" % token)

    def test_d2_server_path_entry_is_kept(self):
        body = function_body("packagingAuthorityImportBody")
        self.assertIn("workbook_path", body, "服务器路径那条路必须保留（Spec §C3/§C4）")
        # `## 481` 按 Spec `packaging-business-tables-are-answer-keys-only.md` §5.1 把这条冻结
        # **重指**到参照那一条路径：业务的表只用来对答案，接口从 `…/packaging-business-parts/import`
        # 改名为 `…/packaging-business-parts/reference`。重指不等于放宽 —— 计数仍是精确相等，
        # 仍只许留一处路径字面量（`…/import` 那份旧字面量一条都不许留在前端）。
        self.assertEqual(1, self.SOURCE.count("packaging-business-parts/reference"),
                         "对答案参照的路径字面量只留一处（Spec §2.2）")

    def test_d3_route_reference_count_is_frozen(self):
        # `## 480` 按 Spec `packaging-2-1-result-parts-and-shape-only-pane.md` §2.4b C6 把这条
        # 批次级冻结**重指**为 5：多出来的那一条只许是"按图纸补推导"那一条路由
        # （`packagingBusinessPartsDerivePath()` → `…/packaging-business-parts/derive`）。
        # 重指不等于放宽 —— 计数仍是精确相等，多出来的那一条逐个点名。
        self.assertEqual(5, self.SOURCE.count("packaging-business-parts/"),
                         "前端业务件路由引用计数重指为 5（`## 480`：只多出 derive 那一条；Spec §C4）")

    def test_d4_pure_functions_have_no_dom_or_fetch(self):
        for name in ("packagingAuthorityFileName", "packagingAuthorityBase64Of",
                     "packagingAuthorityImportBody"):
            body = function_body(name)
            for banned in ("document.", "window.", "fetch(", "localStorage", "FileReader"):
                self.assertNotIn(banned, body,
                                 "%s() 必须是纯函数（Spec §C3）：%s" % (name, banned))

    def test_d5_picker_does_not_parse_the_workbook_in_the_browser(self):
        body = function_body("importPackagingBusinessPartsFromFile")
        for banned in ("xlsx", "SheetJS", "XLSX.read"):
            self.assertNotIn(banned, body, "前端只搬字节、不解析 xlsx（Spec §4）")

    def test_d6_node_check_passes(self):
        proc = subprocess.run(["node", "--check", str(APP_JS)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode,
                         "app.js 必须仍能过 `node --check`：%s"
                         % (proc.stderr or proc.stdout)[:600])


if __name__ == "__main__":
    unittest.main()

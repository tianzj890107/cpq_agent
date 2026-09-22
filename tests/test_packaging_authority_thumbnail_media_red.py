"""红测：工作簿里的部件图（真样本 28 张）要能取到字节、内容寻址入库、并回给页面。

Spec：`docs/specs/packaging-authority-thumbnail-media.md`
依赖口径：`packaging-business-parts-and-cad-plan-view.md` §3、`packaging-authority-disclosure-on-read.md` §9 边界 1。

现状缺口（真样本实测，不是推断）：

  · 真样本 28 张图（jpeg/png，合计 197475 字节）在 `xlsx_grid.read_grid()` 里只剩
    `{index, anchor_row, anchor_col}` —— 字节从来没被取出来过；
  · `Image._data()` 每张图**只能读一次**（第二次 `ValueError: I/O operation on closed file`）；
  · `packaging_parts` 没有内容寻址入库，`main.py` 没有 `.../packaging-business-parts/{code}/thumbnail`
    路由，前端 `#packagingPartPanel` 里没有缩略图节点 —— 所以一件部件图都看不到。

禁止为了让红测转绿而修改本文件；口径变化请改 Spec。
"""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SAMPLE = ROOT / "裕同包装项目-待开发" / "酒盒 报价资料.xlsx"
SHEET = "零部件排版工艺"
EXPECTED_IMAGES = 28
EXPECTED_BYTES = 197475


def sample_xlsx() -> pathlib.Path:
    if not SAMPLE.exists():
        raise unittest.SkipTest("真样本不在仓库里：%s" % SAMPLE)
    return SAMPLE


def sample_zip_hashes() -> set:
    with zipfile.ZipFile(sample_xlsx()) as book:
        return {hashlib.sha256(book.read(name)).hexdigest()
                for name in book.namelist()
                if name.startswith("xl/media/") and not name.endswith("/")}


class FakeImage:
    """最小 openpyxl 图片替身：只实现 `_data()`。"""

    def __init__(self, data=None, error=None):
        self._data_value = data
        self._error = error
        self.calls = 0

    def _data(self):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._data_value


PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
JPEG = (b"\xff\xd8\xff\xe0" + b"\x00" * 20)


# --------------------------------------------------------------------------- #
# A 组：工具层取字节
# --------------------------------------------------------------------------- #
class AXlsxGrid(unittest.TestCase):
    def _grid(self):
        from tech_app.tools import xlsx_grid
        return xlsx_grid

    def test_a1_media_type_from_magic_bytes(self):
        grid = self._grid()
        fn = getattr(grid, "media_type_of", None)
        if fn is None:
            self.fail("xlsx_grid 没有 media_type_of()（Spec §C1）")
        self.assertEqual("image/png", fn(PNG))
        self.assertEqual("image/jpeg", fn(JPEG))
        self.assertEqual("application/octet-stream", fn(b"\x00\x01\x02"))
        self.assertEqual("application/octet-stream", fn(b""))

    def test_a2_image_payload_reads_bytes_once(self):
        grid = self._grid()
        fn = getattr(grid, "image_payload", None)
        if fn is None:
            self.fail("xlsx_grid 没有 image_payload()（Spec §C1）")
        image = FakeImage(data=PNG)
        got = fn(image)
        self.assertEqual("image/png", got["media_type"])
        self.assertEqual(len(PNG), got["bytes"])
        self.assertEqual(hashlib.sha256(PNG).hexdigest(), got["sha256"])
        self.assertEqual(base64.b64encode(PNG).decode("ascii"), got["content_base64"])
        self.assertEqual("", got["unavailable"])
        self.assertEqual(1, image.calls, "字节只许读一次（第二次 openpyxl 会抛 I/O closed file）")

    def test_a3_unreadable_image_is_disclosed_not_raised(self):
        grid = self._grid()
        fn = getattr(grid, "image_payload", None)
        if fn is None:
            self.fail("xlsx_grid 没有 image_payload()（Spec §C1）")
        got = fn(FakeImage(error=ValueError("I/O operation on closed file")))
        self.assertEqual("image_bytes_unreadable", got["unavailable"])
        self.assertEqual("", got["content_base64"])
        self.assertEqual(0, got["bytes"])
        self.assertEqual("", got["sha256"])
        self.assertEqual("application/octet-stream", got["media_type"])

    def test_a4_default_read_does_not_change_existing_shape(self):
        path = sample_xlsx()
        grid = self._grid()
        doc = grid.read_grid(path)
        # `read_grid()["sheets"]` 是「表名 → 网格」的字典；表名结尾可能有空格（真样本就有）。
        sheet = next(item for name, item in doc["sheets"].items() if name.strip() == SHEET)
        self.assertEqual(EXPECTED_IMAGES, len(sheet["images"]))
        for image in sheet["images"]:
            self.assertEqual({"index", "anchor_row", "anchor_col"}, set(image),
                             "缺省调用（不给 with_images）时 images 必须逐字不变（Spec §C1）")

    def test_a5_with_images_returns_bytes_and_hashes(self):
        path = sample_xlsx()
        grid = self._grid()
        doc = grid.read_grid(path, with_images=True)
        # `read_grid()["sheets"]` 是「表名 → 网格」的字典；表名结尾可能有空格（真样本就有）。
        sheet = next(item for name, item in doc["sheets"].items() if name.strip() == SHEET)
        images = sheet["images"]
        self.assertEqual(EXPECTED_IMAGES, len(images))
        digests = set()
        total = 0
        for image in images:
            for key in ("media_type", "bytes", "sha256", "content_base64", "unavailable"):
                self.assertIn(key, image, "with_images=True 时缺 %s（Spec §C1）" % key)
            self.assertEqual("", image["unavailable"])
            raw = base64.b64decode(image["content_base64"])
            self.assertEqual(image["bytes"], len(raw))
            self.assertEqual(image["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertIn(image["media_type"], ("image/png", "image/jpeg"))
            digests.add(image["sha256"])
            total += image["bytes"]
        self.assertEqual(EXPECTED_BYTES, total, "真样本 28 张图合计 197475 字节（Spec §C1）")
        self.assertTrue(digests.issubset(sample_zip_hashes()),
                        "取出来的字节必须真出自这本工作簿的 xl/media（Spec §C1）")


# --------------------------------------------------------------------------- #
# B 组：导入器把图片带出来
# --------------------------------------------------------------------------- #
class BImportWorkbook(unittest.TestCase):
    def _authority(self):
        from tech_app.backend.services import packaging_part_authority
        return packaging_part_authority.import_workbook(sample_xlsx())

    def test_b1_images_list_aligns_with_parts(self):
        authority = self._authority()
        images = authority.get("images")
        self.assertIsInstance(images, list, "权威产物要带 images 列表（Spec §C2）")
        self.assertEqual(EXPECTED_IMAGES, len(images))
        refs = {item["ref"] for item in images}
        part_refs = {row.get("thumbnail_ref") for row in authority["parts"]}
        self.assertEqual(part_refs, refs, "images[].ref 必须与 parts[].thumbnail_ref 同一套写法（Spec §C2）")

    def test_b2_每张图都能解回同一份字节(self):
        authority = self._authority()
        for image in authority["images"]:
            raw = base64.b64decode(image["content_base64"])
            self.assertEqual(image["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(image["bytes"], len(raw))

    def test_b3_stats_gains_byte_total_and_keeps_the_rest(self):
        authority = self._authority()
        stats = authority["stats"]
        self.assertEqual(EXPECTED_BYTES, stats.get("image_bytes_total"),
                         "stats 要新增 image_bytes_total（Spec §C2）")
        self.assertEqual(EXPECTED_IMAGES, stats.get("image_total"))
        self.assertEqual(EXPECTED_IMAGES, stats.get("part_total"))
        self.assertEqual(28, stats.get("thumbnail_bound_total"))
        self.assertEqual(4, stats.get("skipped_total"))

    def test_b4_existing_keys_survive(self):
        authority = self._authority()
        for key in ("engine_version", "parts", "skipped", "source", "stats", "unavailable"):
            self.assertIn(key, authority)
        first = authority["parts"][0]
        for key in ("sequence_no", "name", "business_part_code", "product_size_text",
                    "thumbnail_ref", "thumbnail_refs", "thumbnail_source", "source"):
            self.assertIn(key, first, "件级键 %s 不许回退（Spec §C2）" % key)
        self.assertEqual(4, len(authority["skipped"]))
        self.assertEqual("order", first["thumbnail_source"], "部件图归属算法不许改（Spec §C2）")


# --------------------------------------------------------------------------- #
# C 组：内容寻址入库
# --------------------------------------------------------------------------- #
class CThumbnailStore(unittest.TestCase):
    def _parts(self):
        from tech_app.backend.services import packaging_parts
        return packaging_parts

    def setUp(self):
        from tech_app.backend.storage import blob_backend
        from tech_app.backend.storage.blob_backend import LocalBlobBackend
        self.blob_backend = blob_backend
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="thumb-red-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.blob = LocalBlobBackend(self.tmp)
        self._patch(blob_backend, "_blob", self.blob)

    def _patch(self, module, attr, value):
        original = getattr(module, attr)
        setattr(module, attr, value)
        self.addCleanup(setattr, module, attr, original)

    def _authority(self, images):
        return {"engine_version": "packaging-part-authority/1", "parts": [], "skipped": [],
                "source": {}, "stats": {}, "unavailable": [], "images": images}

    def _image(self, ref, data=PNG):
        return {"ref": ref, "index": 1, "anchor_row": 4, "anchor_col": 8,
                "media_type": "image/png", "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "content_base64": base64.b64encode(data).decode("ascii"),
                "unavailable": ""}

    def test_c1_writes_blobs_and_returns_refs_without_base64(self):
        parts = self._parts()
        fn = getattr(parts, "save_authority_thumbnails", None)
        if fn is None:
            self.fail("packaging_parts 没有 save_authority_thumbnails()（Spec §C3）")
        images = [self._image("image:零部件排版工艺!4#1", PNG),
                  self._image("image:零部件排版工艺!5#2", JPEG)]
        got = fn("proj-thumb", self._authority(images))
        self.assertEqual(2, got["written"])
        self.assertEqual(0, got["reused"])
        self.assertEqual({"image:零部件排版工艺!4#1", "image:零部件排版工艺!5#2"}, set(got["by_ref"]))
        self.assertNotIn("content_base64", json.dumps(got), "入库结果不许带 base64（Spec §C3）")
        for ref, item in got["by_ref"].items():
            self.assertTrue(item["available"])
            self.assertTrue(item["key"].startswith("proj-thumb/packaging-authority/images/"))
            self.assertEqual(1, self.blob.get_bytes(item["key"]).__len__() and 1)

    def test_c2_second_run_reuses_the_same_blobs(self):
        parts = self._parts()
        authority = self._authority([self._image("image:零部件排版工艺!4#1", PNG)])
        parts.save_authority_thumbnails("proj-thumb", authority)
        again = parts.save_authority_thumbnails("proj-thumb", authority)
        self.assertEqual(0, again["written"])
        self.assertEqual(1, again["reused"])
        key = again["by_ref"]["image:零部件排版工艺!4#1"]["key"]
        self.assertEqual(PNG, self.blob.get_bytes(key))

    def test_c3_same_bytes_are_stored_once(self):
        parts = self._parts()
        images = [self._image("image:零部件排版工艺!4#1", PNG),
                  self._image("image:零部件排版工艺!5#2", PNG)]
        got = parts.save_authority_thumbnails("proj-thumb", images and self._authority(images))
        self.assertEqual(1, got["written"], "同一份字节只落一次（内容寻址，Spec §C3）")
        self.assertEqual(1, got["reused"])
        self.assertEqual(got["by_ref"]["image:零部件排版工艺!4#1"]["key"],
                         got["by_ref"]["image:零部件排版工艺!5#2"]["key"])

    def test_c4_broken_base64_is_disclosed_not_raised(self):
        parts = self._parts()
        broken = self._image("image:零部件排版工艺!4#1", PNG)
        broken["content_base64"] = "!!!not-base64!!!"
        got = parts.save_authority_thumbnails("proj-thumb", self._authority([broken]))
        self.assertEqual(0, got["written"])
        item = got["by_ref"]["image:零部件排版工艺!4#1"]
        self.assertFalse(item["available"])
        self.assertTrue(item["unavailable"], "坏图要给原因，不许静默（Spec §C3）")
        self.assertEqual("", item["key"])

    def test_c5_empty_authority_keeps_the_shape(self):
        parts = self._parts()
        got = parts.save_authority_thumbnails("proj-thumb", {})
        self.assertEqual({"written": 0, "reused": 0, "images": [], "by_ref": {}}, got)


# --------------------------------------------------------------------------- #
# D 组：文档里的 thumbnail 引用
# --------------------------------------------------------------------------- #
class DDocumentThumbnail(unittest.TestCase):
    def _parts(self):
        from tech_app.backend.services import packaging_parts
        return packaging_parts

    def _authority(self, *, with_image=True, unavailable=False):
        row = {"sequence_no": 1, "name": "左盖面纸", "business_part_code": "JWXR21-P01",
               "product_size_text": "307.07x528.89mm", "length_mm": 307.07, "width_mm": 528.89,
               "thumbnail_ref": "image:零部件排版工艺!4#1" if with_image else "",
               "thumbnail_refs": ["image:零部件排版工艺!4#1"] if with_image else [],
               "thumbnail_source": "order" if with_image else "",
               "source": {"sheet": SHEET, "row": 4}}
        digest = hashlib.sha256(PNG).hexdigest()
        images = [{"ref": "image:零部件排版工艺!4#1", "sha256": digest, "media_type": "image/png",
                   "bytes": len(PNG), "key": "p/packaging-authority/images/%s.png" % digest,
                   "available": not unavailable,
                   "unavailable": "image_bytes_unreadable" if unavailable else ""}] if with_image else []
        return {"engine_version": "packaging-part-authority/1", "parts": [row], "skipped": [],
                "source": {"sheet": SHEET}, "stats": {"part_total": 1, "image_total": len(images)},
                "unavailable": [], "images": images}

    def _document(self, authority, thumbnails=None):
        parts = self._parts()
        if thumbnails is None:
            kwargs = {}
        else:
            kwargs = {"thumbnails": thumbnails}
        return parts.business_parts_document(authority, {}, **kwargs)

    def test_d1_available_thumbnail_is_copied_from_the_store_result(self):
        authority = self._authority()
        saved = {"written": 1, "reused": 0, "images": [],
                 "by_ref": {"image:零部件排版工艺!4#1": dict(authority["images"][0])}}
        doc = self._document(authority, saved)
        thumb = doc["business_parts"][0]["thumbnail"]
        self.assertTrue(thumb["available"])
        self.assertEqual(authority["images"][0]["sha256"], thumb["sha256"])
        self.assertEqual(authority["images"][0]["key"], thumb["key"])
        self.assertEqual("image/png", thumb["media_type"])
        self.assertEqual(len(PNG), thumb["bytes"])
        self.assertEqual("order", thumb["source"])
        self.assertEqual("", thumb["reason"])

    def test_d2_part_without_image_says_so(self):
        doc = self._document(self._authority(with_image=False), {"by_ref": {}, "images": []})
        thumb = doc["business_parts"][0]["thumbnail"]
        self.assertFalse(thumb["available"])
        self.assertEqual("thumbnail_missing", thumb["reason"])
        self.assertEqual("", thumb["ref"])

    def test_d3_unreadable_bytes_are_named(self):
        authority = self._authority(unavailable=True)
        saved = {"written": 0, "reused": 0, "images": [],
                 "by_ref": {"image:零部件排版工艺!4#1": dict(authority["images"][0])}}
        doc = self._document(authority, saved)
        thumb = doc["business_parts"][0]["thumbnail"]
        self.assertFalse(thumb["available"])
        self.assertEqual("image_bytes_unreadable", thumb["reason"])

    def test_d4_without_store_result_nothing_is_pretended(self):
        doc = self._document(self._authority())
        thumb = doc["business_parts"][0]["thumbnail"]
        self.assertFalse(thumb["available"])
        self.assertEqual("thumbnail_not_saved", thumb["reason"])

    def test_d5_document_summary_and_no_base64_inside(self):
        authority = self._authority(with_image=False)
        doc = self._document(authority, {"by_ref": {}, "images": []})
        summary = doc.get("thumbnail")
        self.assertIsInstance(summary, dict, "文档顶层要有 thumbnail 汇总（Spec §C4）")
        for key in ("available_total", "missing_total", "bytes_total"):
            self.assertIn(key, summary, "汇总缺 %s（Spec §C4）" % key % ())
        self.assertEqual(0, summary["available_total"])
        self.assertEqual(1, summary["missing_total"])
        available = self._authority()
        saved = {"written": 1, "reused": 0, "images": [],
                 "by_ref": {"image:零部件排版工艺!4#1": dict(available["images"][0])}}
        with_doc = self._document(available, saved)
        self.assertEqual(1, with_doc["thumbnail"]["available_total"])
        self.assertEqual(len(PNG), with_doc["thumbnail"]["bytes_total"])
        self.assertNotIn("content_base64", json.dumps(with_doc),
                         "文档里不许出现 base64（Spec §C4）")


# --------------------------------------------------------------------------- #
# E 组：只读端点与取字节
# --------------------------------------------------------------------------- #
class EThumbnailEndpoint(unittest.TestCase):
    def test_e1_route_and_constant_are_registered(self):
        from tech_app.backend import main
        source = (ROOT / "tech_app" / "backend" / "main.py").read_text(encoding="utf-8")
        self.assertTrue("PACKAGING_BUSINESS_PART_THUMBNAIL_PATH" in source,
                        "缺缩略图路由常量（Spec §C5）")
        self.assertTrue("PACKAGING_PART_THUMBNAIL_MISSING" in source, "缺稳定错误码（Spec §C5）")
        self.assertTrue("/packaging-business-parts/{part_code}/thumbnail" in source,
                        "路由字面量要能直接 grep 到（Spec §C5）")
        paths = {getattr(route, "path", "") for route in main.app.routes}
        self.assertIn("/api/projects/{pid}/requirement/packaging-business-parts/{part_code}/thumbnail",
                      paths, "缩略图路由没注册（Spec §C5）")

    def test_e2_handler_is_read_only(self):
        source = (ROOT / "tech_app" / "backend" / "main.py").read_text(encoding="utf-8")
        start = source.index("PACKAGING_BUSINESS_PART_THUMBNAIL_PATH")
        block = source[start:start + 1200]
        self.assertNotIn("_require(", block, "缩略图是纯读，不得判写权限（Spec §C5）")


class EThumbnailBytes(unittest.TestCase):
    def setUp(self):
        from tech_app.backend.storage import blob_backend
        from tech_app.backend.storage.blob_backend import LocalBlobBackend
        self.tmp = pathlib.Path(tempfile.mkdtemp(prefix="thumb-read-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.blob = LocalBlobBackend(self.tmp)
        original = blob_backend._blob
        blob_backend._blob = self.blob
        self.addCleanup(setattr, blob_backend, "_blob", original)

    def _doc(self, *, available=True, code="JWXR21-P01"):
        from tech_app.backend.services import packaging_parts
        digest = hashlib.sha256(PNG).hexdigest()
        key = "proj/packaging-authority/images/%s.png" % digest
        if available:
            self.blob.put_bytes(key, PNG)
        return packaging_parts.business_parts_document(
            {"engine_version": "1", "parts": [{"sequence_no": 1, "name": "左盖面纸",
                                               "business_part_code": code,
                                               "thumbnail_ref": "image:x!4#1",
                                               "thumbnail_source": "order"}],
             "skipped": [], "source": {}, "stats": {},
             "unavailable": [],
             "images": [{"ref": "image:x!4#1", "sha256": digest, "media_type": "image/png",
                         "bytes": len(PNG), "key": key, "available": available,
                         "unavailable": "" if available else "image_bytes_unreadable"}]},
            {}, thumbnails={"by_ref": {"image:x!4#1": {"ref": "image:x!4#1", "sha256": digest,
                                                       "media_type": "image/png", "bytes": len(PNG),
                                                       "key": key if available else "",
                                                       "available": available,
                                                       "unavailable": "" if available else "image_bytes_unreadable"}},
                            "images": []})

    def _read(self, doc, code):
        from tech_app.backend.services import packaging_parts
        fn = getattr(packaging_parts, "authority_thumbnail_of", None)
        if fn is None:
            self.fail("packaging_parts 没有 authority_thumbnail_of()（Spec §C5）")
        return fn("proj", doc, code)

    def test_e3_hit_returns_bytes_and_media_type(self):
        got = self._read(self._doc(), "JWXR21-P01")
        self.assertTrue(got["found"])
        self.assertTrue(got["available"])
        self.assertEqual(PNG, got["content"])
        self.assertEqual("image/png", got["media_type"])
        self.assertEqual(len(PNG), got["bytes"])

    def _doc_without_image(self):
        from tech_app.backend.services import packaging_parts
        return packaging_parts.business_parts_document(
            {"engine_version": "1", "parts": [{"sequence_no": 1, "name": "左盖面纸",
                                               "business_part_code": "JWXR21-P01"}],
             "skipped": [], "source": {}, "stats": {}, "unavailable": [], "images": []},
            {}, thumbnails={"by_ref": {}, "images": []})

    def test_e4_misses_are_named(self):
        doc = self._doc()
        miss = self._read(doc, "JWXR21-P99")
        self.assertFalse(miss["found"])
        self.assertEqual("business_part_not_found", miss["reason"])
        empty = self._read(packaging_doc_empty(), "JWXR21-P01")
        self.assertFalse(empty["found"])
        self.assertEqual("business_parts_missing", empty["reason"])
        noref = self._read(self._doc_without_image(), "JWXR21-P01")
        self.assertFalse(noref["found"])
        self.assertEqual("thumbnail_missing", noref["reason"])
        unreadable = self._read(self._doc(available=False), "JWXR21-P01")
        self.assertFalse(unreadable["found"])
        self.assertEqual("image_bytes_unreadable", unreadable["reason"],
                         "有图但字节没入库 → 说清是字节的问题，不是说这没图")
        hit = self._read(self._doc(), "JWXR21-P01")
        self.assertTrue(hit["available"])


def packaging_doc_empty():
    return {"business_parts": [], "stats": {}}


# --------------------------------------------------------------------------- #
# F 组：前端
# --------------------------------------------------------------------------- #
class FFrontend(unittest.TestCase):
    def _source(self, rel):
        return (ROOT / rel).read_text(encoding="utf-8", errors="replace")

    def test_f1_url_pure_function(self):
        import subprocess
        script = r'''
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
globalThis.API = "http://probe";
const at = src.indexOf("function packagingBusinessPartThumbnailUrl(");
if (at < 0) { console.log(JSON.stringify({missing: true})); process.exit(0); }
let i = src.indexOf("{", at), depth = 0, body = null;
for (let j = i; j < src.length; j++) { if (src[j] === "{") depth++; else if (src[j] === "}") { depth--; if (!depth) { body = src.slice(at, j + 1); break; } } }
eval(body);
console.log(JSON.stringify({missing: false,
  value: packagingBusinessPartThumbnailUrl("p 1", "JWXR21-P01/2"),
  body: body}));
'''
        proc = subprocess.run(["node", "-e", script, "-", str(ROOT / "tech_app" / "frontend" / "app.js")],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode, proc.stderr[:600])
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertFalse(payload.get("missing"), "缺纯函数 packagingBusinessPartThumbnailUrl()（Spec §C6）")
        url = payload["value"]
        self.assertIn("/packaging-business-parts/", url)
        self.assertTrue(url.endswith("/thumbnail"), url)
        self.assertIn("p%201", url, "projectId 要 encode（Spec §C6）")
        self.assertIn("JWXR21-P01%2F2", url, "part_code 要 encode（Spec §C6）")
        for banned in ("document", "window.", "fetch("):
            self.assertNotIn(banned, payload["body"], "纯函数里不许出现 %s（Spec §C6）" % banned)

    def test_f2_panel_has_a_thumbnail_node_and_copy(self):
        html = self._source("tech_app/frontend/index.html")
        self.assertTrue("packagingPartThumbnail" in html, "面板缺缩略图节点（Spec §C6）")
        app = self._source("tech_app/frontend/app.js")
        self.assertTrue("mediaUrl(" in app, "<img> 带不了请求头，必须走 mediaUrl()（Spec §C6）")
        self.assertTrue("packaging-business-thumb" in app, "缺缩略图 <img>（Spec §C6）")
        for text in ("没有配到部件图", "读不出来", "还没入库"):
            self.assertTrue(text in app, "三态文案缺「%s」（Spec §C6）" % text)

    def test_f3_geometry_panel_hides_the_thumbnail(self):
        app = self._source("tech_app/frontend/app.js")
        start = app.index("function renderPackagingPartPanel(")
        block = app[start:start + 6000]
        self.assertTrue("packagingPartThumbnail" in block,
                        "几何零件面板必须收起缩略图，不许留上一件的图（Spec §C6）")
        self.assertTrue("hidden" in block, "收起要真的写 hidden（Spec §C6）")

    def test_f4_no_workbook_file_name_on_the_page(self):
        app = self._source("tech_app/frontend/app.js")
        self.assertTrue("authority.file" not in app, "页面不许显示工作簿文件名（Spec §C7）")


# --------------------------------------------------------------------------- #
# G 组：护栏
# --------------------------------------------------------------------------- #
class GGuards(unittest.TestCase):
    def test_g1_thumbnail_store_does_not_touch_attachments(self):
        import ast
        source = (ROOT / "tech_app" / "backend" / "services" / "packaging_parts.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next((node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef) and node.name == "save_authority_thumbnails"),
                  None)
        self.assertIsNotNone(fn, "先要有 save_authority_thumbnails()（Spec §C3）")
        called = {getattr(node, "attr", None) or getattr(node, "id", None)
                  for node in ast.walk(fn)}
        self.assertNotIn("add_attachment", called,
                         "部件图入库不许走附件通道（会把 input_revision+1、派生结果标 stale）"
                         "（Spec §C3/C7）")
        for node in ast.walk(fn):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                self.assertNotIn("attachments/", node.value,
                                 "部件图不许往 attachments/ 写（Spec §C3/C7）")

    def test_g2_importer_ownership_and_skip_rules_unchanged(self):
        source = (ROOT / "tech_app" / "backend" / "services" / "packaging_part_authority.py").read_text(encoding="utf-8")
        self.assertTrue('item["thumbnail_source"] = "order"' in source,
                        "部件图归属算法不许改（Spec §C7）")
        self.assertTrue(
            'SKIP_REASONS = ("no_header", "blank_row", "no_sequence", "sequence_not_contiguous",'
            in source, "跳过原因闭集不许改（Spec §C7）")

    def test_g3_grid_stays_openpyxl_free_for_the_backend(self):
        backend = (ROOT / "tech_app" / "backend" / "services" / "packaging_part_authority.py").read_text(encoding="utf-8")
        self.assertTrue("import openpyxl" not in backend,
                        "后端不许直接 import openpyxl（Spec §C1）")
        grid = (ROOT / "tech_app" / "tools" / "xlsx_grid.py").read_text(encoding="utf-8")
        self.assertTrue("openpyxl" in grid, "取字节的工具层才是认识 xlsx 的地方（Spec §C1）")


if __name__ == "__main__":                                              # pragma: no cover
    unittest.main(verbosity=2)

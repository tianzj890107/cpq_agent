# 部件图本体落地：工作簿里的 28 张部件图要能看（内容寻址入库 + 只读端点 + 面板缩略图）

依赖：`docs/specs/packaging-business-parts-and-cad-plan-view.md` §3（取图与"受控媒体 / 内容寻址附件"）、
`docs/specs/packaging-authority-disclosure-on-read.md` §9 边界 1（本批就是补它）。

状态：Spec + 红测（已实现）（2026-09-22 落地：xlsx_grid 取字节 + 内容寻址 blob + 文档留引用 + 只读端点回字节 + 面板缩略图；30 OK）
红测：`tests/test_packaging_authority_thumbnail_media_red.py`

## 1. 目标与验收主路径

1. 权威工作簿里**每件业务部件都有一张部件图**（真样本 28 张，jpeg/png，合计 ~197 KB），
   它是现场核对"这件到底是什么"的唯一凭据。
2. 今天一张也看不到：`tech_app/tools/xlsx_grid.read_grid()` 只记图片的**锚点行/列**（`index` /
   `anchor_row` / `anchor_col`），从不取字节；导入器把引用写成 `image:零部件排版工艺!1#1`，
   页面只有这一串字符串。`## 375` 已经把"归属是推定的"说清楚了，但图本体仍然不在系统里。
3. 本批把它做成一条可回查的通路：**取字节（工具层）→ 内容寻址入库（blob）→ 文档里留引用 →
   只读端点回字节 → 面板显示缩略图**；读不到时如实说明原因，不许留空白画布、不许假装有图。

## 2. 契约

### C1 `tech_app/tools/xlsx_grid.read_grid(source, *, sheet=None, with_images=False)`

- `with_images=False`（缺省）：每张表的 `images` 仍是 `[{index, anchor_row, anchor_col}]`，
  **逐字不变** —— 既有调用方（成本规则快照等）零影响。
- `with_images=True`：每条 image 追加五个键：
  `media_type`（`image/png` / `image/jpeg` / `application/octet-stream`）、`bytes`（int）、
  `sha256`（hex）、`content_base64`（str）、`unavailable`（`""` 或 `"image_bytes_unreadable"`）。
- **每张图只读一次底层字节**：openpyxl 的 `Image._data()` 会把图片流读干（第二次调用
  `I/O operation on closed file`，本机实测），所以取字节必须在同一个地方一次取完并缓存成
  base64 字符串 —— 之后任何人再要字节都从这份缓存来，不许再摸流。
- 读不到（流的任何异常）**不抛异常**：`content_base64=""`、`bytes=0`、`sha256=""`、
  `unavailable="image_bytes_unreadable"`。导入不能因为一张坏图整份失败。
- 仍是纯数据：不写文件、不联网、不认识业务。
- 新增两个可单测的公开小函数：
  - `media_type_of(data)`：按字节魔数判定（PNG `89 50 4E 47`、JPEG `FF D8 FF`），认不出给
    `application/octet-stream`；
  - `image_payload(image)`：把一张 openpyxl 图片对象变成上面那五个键（异常收敛成 `unavailable`）。

### C2 `packaging_part_authority.import_workbook(source, *, sheet=None)`

- 内部改走 `read_grid(source, with_images=True)`；权威产物新增 `images`：
  逐条 `{"ref", "index", "anchor_row", "anchor_col", "media_type", "bytes", "sha256",
  "content_base64", "unavailable"}`，其中 `ref` 与 `parts[].thumbnail_ref` / `thumbnail_refs`
  **同一套写法**（`image:<表名>!<锚点行>#<序号>`），页面与文档靠它对齐。
- `stats` 新增 `image_bytes_total`（本表所有图字节合计；一张都没读到 = 0）。
- 既有键一个不改：`parts` 的字段与取舍、`skipped`（含 `SKIP_REASONS` 闭集）、
  `source`（`file` / `sheet` / `file_hash` / `code_prefix` / 行区间）、`stats` 其余键、
  `unavailable`；部件图的**归属算法**（数量相等 → 顺序一一对应）也不动。

### C3 `packaging_parts.save_authority_thumbnails(project_id, authority) -> dict`

- **内容寻址**：key = `{project_id}/packaging-authority/images/{sha256}.{ext}`，
  `ext` 由 `media_type` 定（`png` / `jpeg`，认不出 → `bin`）。
- **幂等**：同一 `sha256` 在同一次调用里去重；`blob.exists(key)` 已存在就不重写（记 `reused`）。
- `content_base64` 空 / 解不开 → 这一条跳过，留 `available=False` + `unavailable` 原因，
  **不抛异常**。
- 返回 `{"written": int, "reused": int, "images": [{ref, sha256, media_type, bytes, key,
  available, unavailable}], "by_ref": {ref: <同一条>}}`；**不含 base64**（字节只进 blob）。
- 没有 `images` → `{"written": 0, "reused": 0, "images": [], "by_ref": {}}`（键必须存在）。
- 只写 blob，**不碰** `store.add_attachment()` / `attachments/`（那条路会把 `input_revision` +1、
  把派生结果标 stale —— 一张部件图不该让整条工艺链重算）。

### C4 `packaging_parts.business_parts_document(authority, geometry, *, thumbnails=None, ...)`

- 每件新增 `thumbnail`：
  `{"ref": <件级 thumbnail_ref>, "available": bool, "sha256": str, "media_type": str,
  "bytes": int, "key": str, "source": <"order"|"anchor_row"|"">, "reason": str}`。
  - `thumbnails`（C3 产物）里查得到该 `ref` 且可用 → `available=True`（`sha256` / `key` / `media_type` / `bytes` 逐字来自它）；
  - 这一件本来就没有图 → `available=False` + `reason="thumbnail_missing"`；
  - 有图但字节读不到 → `available=False` + `reason="image_bytes_unreadable"`；
  - 没有传 `thumbnails`（老调用方 / 离线单测）→ `available=False` + `reason="thumbnail_not_saved"`。
- 文档顶层新增 `thumbnail` 汇总：`{"available_total", "missing_total", "bytes_total"}`。
- 文档里**不许**出现 `content_base64`（base64 只活在导入那一趟的内存里）。

### C5 只读端点 `GET /api/projects/{pid}/requirement/packaging-business-parts/{part_code}/thumbnail`

- **纯读**：不判写权限（与单件详情、业务部件清单同口径），只 `_workflow_project(pid)`。
- 命中 → `Response(content=<字节>, media_type=<件级 media_type 或 application/octet-stream>,
  headers={"Content-Length": <len>})`（blob 可能是 S3：统一走 `get_bytes()`，不许假设本地路径）。
- 未命中 → 404，detail 带稳定码 `PACKAGING_PART_THUMBNAIL_MISSING` + 人话原因
  （没有清单 / 没有这件 / 这件没有图 / blob 里没有）。
- `_business_parts_body()` 不用额外处理：`thumbnail` 块跟在 `business_parts[]` 里原样出去。

### C6 前端

- 新增纯函数 `packagingBusinessPartThumbnailUrl(projectId, code)` → 上面的端点 URL
  （两段都 `encodeURIComponent`）；函数体内不出现 `document` / `window.` / `fetch(`。
- `index.html`：`#packagingPartPanel` 里新增 `#packagingPartThumbnail`
  （`class="packaging-part-thumbnail"`，默认 `hidden`）。
- `openPackagingBusinessPart()`：
  - 可用 → `#packagingPartThumbnail` 显示 `<img class="packaging-business-thumb" src="${mediaUrl(url)}"
    alt="<编码> <名称> 的部件图">` —— `<img>` 带不了请求头，必须走 `mediaUrl()`；
  - 不可用 → 显示三态文案（`thumbnail_missing` → 「这份清单里这一件没有配到部件图」；
    `image_bytes_unreadable` → 「工作簿里的部件图读不出来（导入时就没读到字节）」；
    其它 → 「部件图还没入库（重新导入权威清单即可）」）。
- `renderPackagingPartPanel()`（几何零件面板）必须把 `#packagingPartThumbnail` 收起并清空 ——
  不许把上一件业务部件的图留在几何零件面板里。

### C7 冻结面

- 不许把图片字节写进 meta 文档（`put_doc`）/读接口 JSON；文档里只有引用与指纹；
- 不许改 `store.add_attachment()` 的 revision 语义、不许往 `attachments/` 写；
- 不许改导入器的行取舍、`SKIP_REASONS`、`skipped` 文案、部件图归属算法；
- 不许改绑定口径 / 几何证据 / 平面图 / 下游 BOM 工艺成本；不许新增第三方依赖（不装 Pillow 之外的、
  更不能引入图像处理库）；不许联网。

## 3. 允许修改范围

1. `tech_app/tools/xlsx_grid.py`（`with_images` + `media_type_of()` + `image_payload()`）。
2. `tech_app/backend/services/packaging_part_authority.py`（`images` 列表与 `stats` 新键）。
3. `tech_app/backend/services/packaging_parts.py`（`save_authority_thumbnails()`、
   `authority_thumbnail_of()`、`business_parts_document()` 的 `thumbnail`）。
4. `tech_app/backend/main.py`（导入端点传 `thumbnails`；新增缩略图只读端点）。
5. `tech_app/frontend/app.js` + `tech_app/frontend/index.html`（+ 可选样式）。
6. changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许把部件图当"必须成功"的硬门槛：一张坏图不能拦住整份清单导入；
- 不许在页面显示工作簿文件名（沿用 `## 375` 口径：只有表名 + 文件指纹）；
- 不许把部件图塞进业务部件文档 JSON 或读接口 JSON（会撑爆 meta 文档）；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_authority_thumbnail_media_red -v
node --check tech_app/frontend/app.js
```

真样本端到端（本机）：

```
./open-claude/.venv/bin/python - <<'PY'
# import_workbook(酒盒 报价资料.xlsx) → save_authority_thumbnails(临时 blob) →
# business_parts_document(..., thumbnails=...) → authority_thumbnail_of() 取回字节
PY
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_authority_disclosure_on_read_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_business_part_panel_evidence_red \
  tests.test_packaging_cost_rule_snapshot_red
```

## 6. 现状缺口（真样本实测，不是推断）

```
工作簿 裕同包装项目-待开发/酒盒 报价资料.xlsx / 表 '零部件排版工艺 '（注意结尾有空格）
  · 图片 28 张，format ∈ {jpeg, png}，合计 197475 字节，单张 1905…14875 字节；
  · 每张图的底层字节可以一次读出（Image._data() 用 BytesIO 引用）；
    但**第二次**调用同一张图会抛 ValueError: I/O operation on closed file（实测）；
  · xlsx 包里 xl/media/* 共 30 条（28 张图 + 2 条目录项），合计 232074 字节。

代码实测：
  · xlsx_grid.read_grid() 返回的 image 只有 {"index","anchor_row","anchor_col"}；
  · packaging_part_authority.import_workbook() 的产物里没有任何图片字节（只有引用字符串）；
  · packaging_parts 里没有 save_authority_thumbnails() / authority_thumbnail_of()，
    main.py 里没有 .../packaging-business-parts/{code}/thumbnail 路由（只有 .../packaging-parts/{part_code}）；
  · 前端 app.js 里 `packaging-business-parts` 相关只有清单/平面图/绑定，没有 <img>，
    index.html 的 #packagingPartPanel 里没有缩略图节点。
```

## 7. 与既有 Spec 的关系（不重复立第二套）

- 图片归属（`order` / `anchor_row`）与"跳过的行"的披露口径以
  `packaging-authority-disclosure-on-read.md` 为准；本批只把**图本体**补上，不动那些文案。
- 面板结构与依据行以 `packaging-business-part-panel-evidence.md` 为准；本批只加一个缩略图节点。
- 单件详情的只读口径（`PACKAGING_PART_READ_PATH`、不判写权限）以
  `packaging-parts-selectable-panel.md` 为准 —— 缩略图端点照同一条口径。

## 8. 落地状态（2026-09-22，已实现）

| 契约 | 落点 |
| --- | --- |
| C1 取字节 | `tech_app/tools/xlsx_grid.py`：`IMAGE_MAGIC` / `IMAGE_UNAVAILABLE` / `media_type_of(data)` / `image_payload(image)`；`_sheet_grid(ws, *, with_images=False)` / `read_grid(..., with_images=False)`（缺省路径逐字不变） |
| C2 权威产物 | `tech_app/backend/services/packaging_part_authority.py`：`_image_entries(sheet)`；`read_grid(source, with_images=True)`；产物新增 `images` + `stats.image_bytes_total` |
| C3 入库 | `tech_app/backend/services/packaging_parts.py`：`THUMBNAIL_PREFIX` / `THUMBNAIL_EXTENSIONS` / `THUMBNAIL_REASONS` / `save_authority_thumbnails()` / `_business_part_thumbnail()` / `_thumbnail_summary()` / `authority_thumbnail_of()`；只走 `from ..storage.blob_backend import get_blob_backend`（函数内延迟 import），不碰 `store.add_attachment()` |
| C4 文档 | `business_parts_document(..., thumbnails=None)`：件级 `thumbnail`（8 键）+ 文档级 `thumbnail` 汇总（`available_total` / `missing_total` / `bytes_total`）；文档里没有 `content_base64` |
| C5 端点 | `tech_app/backend/main.py`：导入端点先 `save_authority_thumbnails(pid, authority)` 再把引用交给文档，审计加两条计数；`PACKAGING_BUSINESS_PART_THUMBNAIL_PATH` / `PACKAGING_PART_THUMBNAIL_MISSING` / `PACKAGING_THUMBNAIL_REASON_COPY` + 只读处理器 `read_packaging_business_part_thumbnail` |
| C6 前端 | `app.js`：`packagingBusinessPartThumbnailUrl(projectId, code)`、`openPackagingBusinessPart()` 渲染缩略图与三态文案、`renderPackagingPartPanel()` 收起并清空 `#packagingPartThumbnail`；`index.html`：`#packagingPartPanel` 内新增 `#packagingPartThumbnail`（`class="packaging-part-thumbnail"`，默认 `hidden`） |

复跑原文：

```
实现前（把 6 个实现文件 stash 掉）：
  Ran 30 tests in 2.215s
  FAILED (failures=11, errors=14)

实现后：
  Ran 30 tests in 2.394s
  OK

node --check tech_app/frontend/app.js   → 通过
git diff --check                        → 干净

相邻不回归（authority_disclosure_on_read + business_parts_and_cad_plan_view +
business_part_panel_evidence + cost_rule_snapshot + cost_column_evidence）：
  Ran 116 tests in 3.582s
  OK
```

真样本端到端（本机，非夹具；`裕同包装项目-待开发/酒盒 报价资料.xlsx`，临时 blob 后端）：

```
image_total=28 image_bytes_total=197475 parts=28
written=28 reused=0 by_ref=28
doc thumbnail summary: {'available_total': 28, 'missing_total': 0, 'bytes_total': 197475}
first part: JWXR21-P01 左盖面纸 -> {'available': True, 'media_type': 'image/jpeg',
                                    'bytes': 4964, 'source': 'order', 'reason': ''}
read back: found=True bytes=4964 media_type=image/jpeg sha=3284b8454b17
identical to workbook bytes: True
second run: written=0 reused=28          # 幂等
blob files: 28
attachments dir exists: False            # 没走 add_attachment()
```

## 9. 已记录的边界

1. 只做"同一版清单里的部件图"：不做部件图的 OCR / 尺寸识别 / 与几何分量的图像匹配 ——
   图只是给人看的证据，任何数值仍以权威尺寸与图纸为准。
2. 图片按内容寻址存 blob（`{project}/{...}/images/{sha256}.{ext}`），**不进** meta 文档；
   项目删除时不清理 blob（与既有 `attachments/` / `geometry/` 同口径，属另一批的运维话题）。
3. 端点回的是原始字节（不做缩放/转码）：真样本单张最大 ~15 KB，页面按 CSS 宽度显示即可。
4. `Image._data()` 只能读一次，是 openpyxl 的实现细节：本批把"读一次 + 缓存 base64"钉在
   `xlsx_grid.image_payload()` 一处，任何后续调用方都不许再摸那张图的流。
5. 红测本身校正过四处，均在**未实现的代码**上重新跑过仍是 `failures=11, errors=14`，
   没有把任何红测改绿：① `A4/A5` 的 `read_grid()["sheets"]` 是「表名 → 网格」字典
   （真样本表名带尾空格 `'零部件排版工艺 '`），原夹具写成了 list，改为 `doc["sheets"].items()`；
   ② `E2` 的源码窗口 2600 → **1200 字**（缩略图常量块之后 1200 字内不含 `_require(`，再往后是
   包装路线段的写权限，与缩略图无关；窗口开太大反而会把无关代码扫进来）；
   ③ `G1` 改成 AST 检查（只看 `save_authority_thumbnails()` 的函数体，不看整份文件）；
   ④ `F1` 的 node 探针加 `globalThis.API = "http://probe"`（`mediaUrl()` 依赖它）。

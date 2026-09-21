# 规格：逆向快速报价 第 7 批 —— 统一解析服务端点（8010 上的 `/api/file/parse`）

状态：Spec + 红测（已实现）
红测：`tests/test_quick_quote_parse_service_red.py`
依赖：批 5（报价侧客户端 `cpq_quick_quote_file.py` 已实现）。

## 0. 本批要解决的问题

批 5 把报价侧做成了**纯客户端**（`DEFAULT_PARSE_URL = http://127.0.0.1:8010/api/file/parse`），
但服务端那一半从来没实现。2026-09-21 实测（`172.16.10.34`）：

```
GET  http://172.16.10.34:8010/api/file/parse/capability → 404 {"detail":"Not Found","trace_id":…}
POST http://172.16.10.34:8010/api/file/parse             → 405
```

`cpq_suite_server.py` 把 `/api/*` 整体反代给技术工艺子服务（8012），所以这个 404 来自**技术工艺侧
没有这个路由**。结果：销售拿 `酒盒.dwg` 走快速报价，只会拿到
`kind=service_unavailable` + 「转人工 / 转精准报价」——**如实拒绝，但演示走不通**。

本批把这一半补上：**技术工艺侧提供**、**报价侧消费**，两边共用同一套底层 DWG 解析能力，
报价项目里仍然不装第二套 ODA。

## 1. 现场缺口（实测，不是推断）

| 位置 | 现状 |
| --- | --- |
| `tech_app/backend/main.py` | 没有 `/api/file/parse`、也没有 `/api/file/parse/capability` 路由 |
| `tech_app/backend/services/` | 没有统一解析服务模块（`cad_converter` / `cad_ir` 都在，但没有"按字段取数"的出口） |
| `172.16.10.34:8010` | 两个路径分别是 404 / 405（见 §0） |
| `cpq_quick_quote_file.py` | 客户端已就绪：`capability()` / `parse_file()` / `to_match_inputs()` 都在，只等对面有服务 |

## 2. 契约

### 2.1 新模块 `tech_app/backend/services/unified_parse.py`

```python
SERVICE_NAME = "cpq-unified-parse"
SERVICE_VERSION = "unified_parse_v1"
MAX_PARSE_BYTES = 64 * 1024 * 1024
MAX_LIST_ITEMS = 200                 # 列表类字段的截断上限
MAX_MATERIAL_NOTES = 50
#: 转换产物落这个**隔离解析项目**的目录（不是业务项目：不建卡片、不写需求/零件/成本）。
PARSE_PROJECT_ID = "cpq-unified-parse"

DWG_EXTS = (".dwg", ".dxf")
DOC_EXTS = (".txt", ".md", ".csv", ".xlsx", ".xls", ".pdf", ".docx",
            ".png", ".jpg", ".jpeg")

#: 快速通道只提供这些字段。**必须与 cpq_quick_quote_file.QUICK_FIELDS 逐字一致**
#: （红测直接比对两个元组；两侧不许各自漂移）。
PARSE_FIELDS = ("units", "annotated_dimensions", "outline_size", "box_features",
                "closure_type", "v_groove", "magnet", "window",
                "material_notes", "text_annotations", "layers", "blocks",
                "unfolded_size")

CAPABILITY_KEYS = ("service", "provider", "provider_version", "dwg", "dxf", "preview")
PARSE_ERROR_CODES = ("bad_payload", "empty_file", "unsupported_format",
                     "file_too_large", "converter_unavailable", "parse_failed")
#: 可注入依赖必须提供的方法（红测按这个签名注入假的，不真跑 ODA）。
PARSE_DEPS_METHODS = ("capability", "convert", "parse_dxf")

#: 只做关键词命中的语义字段：**命中给 True，未命中进 missing —— 不给 False**。
#: 「图纸没写」不等于「没有」；给 False 会让批 2 把差异项当成相同项。
KEYWORD_FIELDS = ("v_groove", "magnet", "window")
KEYWORD_HINTS = {"v_groove": ("V槽", "V 槽", "V-CUT", "VCUT"),
                 "magnet": ("磁铁", "磁石", "磁吸", "magnet"),
                 "window": ("开窗", "窗口", "透明窗", "window")}

class ParseError(Exception):
    """带稳定错误码的业务错误：`.code` / `.http_status` / `.advice`。"""

def capability(*, deps=None) -> dict
def fields_from_ir(ir, wanted, *, units_text=None) -> dict
def parse_payload(payload, *, deps=None) -> dict
```

**可注入依赖 `deps`**（缺省 `_DefaultDeps`，把既有能力接上来）：

| 方法 | 职责 | 默认实现 |
| --- | --- | --- |
| `capability()` | 转换器能力探测 | `cad_converter.capability()` |
| `convert(project_id, filename, content)` | 转换出 DXF 字节 | `cad_converter.convert_drawing()` + 按 `manifest["output_files"]` 里 `role=="dxf"` 的产物读字节（`cad_converter.persistence.artifact_dir()`），返回 `{"dxf": bytes, "provider", "provider_version", "status", "warnings"}` |
| `parse_dxf(content, filename, source)` | DXF → CAD IR | `cad_ir.parse_dxf()` |

- **不许调 `cad_ir.parse_conversion()`**：它会 `save_ir` + `audit`，把解析结果写进项目存储；
  快速通道只要字段，不需要留档（源码级断言）。
- 本模块**不 import** `store` / `da_db` / `da_repo` / `meta_backend`，自己不做任何持久化。

### 2.2 能力预检 `capability()`

返回**正好** `CAPABILITY_KEYS` 六个键：

```python
{"service": SERVICE_NAME, "provider": str, "provider_version": str,
 "dwg": bool, "dxf": bool, "preview": bool}
```

- `dwg` **必须来自真探测**（`deps.capability()` 的 `available`），不许硬编码 `True`；
- 探测抛异常 → `dwg=False`、`provider=""`、`provider_version=""`，并在 `detail` 里给原因；
  **不抛异常**（前端要能显示"不支持 DWG"，而不是页面挂掉）；
- `preview` 同样取真探测结果（`preview_available`）。

### 2.3 解析 `parse_payload(payload)`

入参：`{"name": str, "data": base64 str, "fields": [str] 可选}`。出参：

```python
{"ok": True, "kind": "drawing", "service": SERVICE_NAME,
 "provider": str, "provider_version": str,
 "fields": {只含**被请求的**键}, "missing_fields": [str],
 "warnings": [str], "elapsed_ms": int}
```

正确失败的 `ParseError`（`.http_status` 原样回给 HTTP）：

| 情形 | `code` | `http_status` | 备注 |
| --- | --- | --- | --- |
| 空 `data`（解码后 0 字节） | `empty_file` | 400 | **不发转换请求** |
| `data` 不是合法 base64 / 缺 `name` | `bad_payload` | 400 | |
| 原始字节 > `MAX_PARSE_BYTES` | `file_too_large` | 413 | 先看大小再解码 |
| 扩展名不在 `DWG_EXTS`/`DOC_EXTS` | `unsupported_format` | 400 | `advice` 点名支持的格式 |
| 转换器不可用 | `converter_unavailable` | 503 | `advice` 给「转人工 / 转精准报价」 |
| 转换或解析抛异常 | `parse_failed` | 502 | 带底层错误码，不假装成功 |

硬约束：

- `fields` 缺省 = `PARSE_FIELDS`；请求里出现 `PARSE_FIELDS` 之外的键 → `bad_payload`
  （报价快速通道不索取完整几何 / 部件 / 证据）；
- 只回**被请求的**字段：没请求的键不出现在 `fields` 里；
- `deps.capability()` 的 `available` 为假 → `converter_unavailable`(503)，**且不调 `deps.convert()`**；
- 文档类扩展名（`DOC_EXTS`）**本批不做**：明确回 `unsupported_format` + `advice`
  「文字 / Excel / PDF 走既有 `/api/extract`」—— 报价侧客户端本来就自己走那条路，
  统一解析服务不重复实现文档解析。

### 2.4 字段口径 `fields_from_ir(ir, wanted)`

**只从 CAD IR 能确定的东西取数，推不出来的进 `missing_fields`，绝不猜。**

| 字段 | 取值 |
| --- | --- |
| `units` | `ir["units"]["drawing_units"]`（`unit_status == "confirmed"` 才算取到；未确认 → missing） |
| `outline_size` | `ir["document"]["extents"]` 算出的 `{"width","height","source":"document_extents"}`，并记 warning `outline_from_extents`（**这是图纸范围，不是成品内尺寸**） |
| `layers` | `ir["layers"][*]["name"]`，**升序**，截断到 `MAX_LIST_ITEMS` |
| `blocks` | `ir["blocks"][*]["name"]`，去重后**升序**，截断到 `MAX_LIST_ITEMS` |
| `annotated_dimensions` | `ir["dimensions"][*]["measured_value"]`（保持 IR 顺序，截断到 `MAX_LIST_ITEMS`） |
| `text_annotations` | `ir["texts"][*]["normalized_text"]`（空则回退 `raw_text`），保持 IR 顺序，截断到 `MAX_LIST_ITEMS` |
| `material_notes` | `text_annotations` 里含材料关键词（灰板/纸板/铜版/白卡/单粉/牛皮/瓦楞/克重/g/m²…）的行，最多 `MAX_MATERIAL_NOTES` 条 |
| `v_groove` / `magnet` / `window` | **关键词命中给 `True`**；未命中 → missing（**不给 False**） |
| `closure_type` | 关键词命中给字符串（如 `双开门` / `天地盖` / `磁吸`）；未命中 → missing |
| `box_features` / `unfolded_size` | 本批**不产**（属于技术工艺的语义层与零件提取），一律 missing |

### 2.5 HTTP 路由（`tech_app/backend/main.py`）

- `GET /api/file/parse/capability` → 200 + §2.2 的 dict；
- `POST /api/file/parse` → 200 + §2.3 的 dict；`ParseError` 时按 `.http_status` 回
  `{"ok": false, "code", "error", "advice"}`（**健康检查式的"声称支持"不允许**：
  能力段与真实转换结果必须一致）。

### 2.6 真实样本验收

- 真实样本只读：`裕同包装项目-待开发/酒盒.dwg`（`AC1027`）。
- 本机（有 `dwg2dxf` 或 ODA）必须真跑一次：`layers` 8 个且含 `0`/`CUTTER`/`DESIGN`，
  `annotated_dimensions` = 316 条、`text_annotations` = 127 条（与 `## 240` 的金标一致），
  图纸文字里有 `V slot(V槽）` → `v_groove = True`（关键词命中，实测已确认）。
- 34 上的端到端（`CPQ_PARSE_SERVICE_E2E=1` 时才跑，缺服务/缺样本一律 `skipTest` 并**点名缺什么**）。

## 3. 红测映射（`tests/test_quick_quote_parse_service_red.py`）

| 组 | 覆盖 |
| --- | --- |
| A | 模块与命名契约：常量、`PARSE_FIELDS` 与报价侧 `QUICK_FIELDS` **逐字相等**、`ParseError` 有 `code/http_status/advice` |
| B | `capability()`：六键齐、`dwg` 来自真探测（假 converter 可用/不可用两种）、探测抛错不抛异常 |
| C | 正确失败：空文件 / 坏 base64 / 超大 / 未知扩展名 / 转换器不可用，且错误码与 http_status 对得上 |
| D | 只回被请求字段；越界字段键 → `bad_payload` |
| E | `fields_from_ir()`：图层升序、文本/标注截断、关键词字段命中给 True、未命中进 missing（**不给 False**） |
| F | 不落库：源码里不出现 `store` / `da_db` / `da_repo` / `meta_backend` 的导入 |
| G | `main.py` 两条路由与错误码透传 |
| H | 真实样本（本机）：`酒盒.dwg` 真跑，layers/dims/texts 与金标一致 |
| I | 34 端到端（`CPQ_PARSE_SERVICE_E2E=1` 才跑）：capability 200 + 真解析 |

## 4. 非目标

- 不在报价侧装第二套 ODA / LibreDWG（批 5 的红线继续有效）；
- 不复制技术工艺的几何 / 部件 / 建模产出：快速通道只要 §2.4 这些字段；
- 不做语义推断（盒型族识别、成品主轮廓判定）；那是 `packaging_semantics` 的事，
  本批只把 IR 能确定的东西按字段交付；
- 不落库、不建项目、不写任何业务数据。

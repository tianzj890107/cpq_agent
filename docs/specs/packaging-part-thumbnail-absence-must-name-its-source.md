# Spec：件级「没有部件图」必须说出**来源**——图纸推导的清单整版就没有部件图，不许逐件说成「这一件没有配到」

状态：Spec + 红测（已实现）（2026-09-24 落地，20/20 绿；实现前 9 红 / 11 绿护栏）
红测：`tests/test_packaging_part_thumbnail_absence_must_name_its_source_red.py`
血缘：`docs/specs/packaging-authority-thumbnail-media.md`（件级三态与 blob 归属）、
`docs/specs/packaging-authority-thumbnail-bytes-read-failure.md`（三态文案必须分家）、
`docs/specs/packaging-parts-must-be-derived-from-the-drawing.md`（图纸推导的清单必须自称来源）、
`docs/specs/packaging-authority-disclosure-on-read.md`（清单级披露要能刷新后复现）、
`docs/specs/packaging-business-tables-are-answer-keys-only.md`（客户工作簿只对答案、不当输入）。
本批 changelog 条目号：`## 493`

## 0. 用户原话（2026-09-24）

> 为什么每个部件都显示 这份清单里这一件没有配到部件图

现场（34 线上 2.1 结果区）：点左侧**任意一件**业务部件，右栏「部件图」一栏都是同一句话。

## 1. 为什么会这样（代码级实测，不是推断）

| 读数 | 实测 |
| --- | --- |
| 这张单是谁出的 | `packaging_business_part_resolver.py:1571` 出的权威产物写死 `"derived_from_drawing": True`；它的行（`:1361-1385`）只给 `business_part_code` / `name` / 尺寸 / 文本证据，**从来没有 `thumbnail_ref` 这个键** |
| 件级原因码的兜底 | `packaging_parts.py:3943-3949 _business_part_thumbnail()`：只要 `thumbnail_ref` 为空，就写死 `"reason": "thumbnail_missing"` |
| `thumbnail_missing` 的语义 | 它是**工作簿世界**的码：`main.py:7638`「这份权威清单里这一件没有配到部件图」、`app.js:3563` 同一句 |
| 于是 | 图纸推导的 28 行**每一行**都拿到 `thumbnail_missing` ⇒ 右栏点谁都同一句 |
| 为什么这句读起来像缺陷 | 它在说「这一件自己缺图」，而真因是「**这一版清单整版都没有部件图这一栏**」：部件图只存在于权威清单工作簿的浮动图片里，图纸里根本没有这个信息 |
| 前端另一处其实说对了 | 清单级那行（`app.js:3067`）是「部件图：28 件都没配到部件图（这一版清单的图没有归属）。」——已经点出"这一版清单"，但没说**为什么**、也没给出路 |

一句话：**码和文案都只有"工作簿世界"一套，图纸推导的清单被硬塞进了那套话术。**

## 2. 契约

### 2.1 C1 件级原因码必须把两个世界分开

- 新增稳定码 **`thumbnail_source_has_none`**。
- 清单 `derived_from_drawing=True` 时（**或**该行没有 `thumbnail_ref` 且这张单不是工作簿来源），件级
  `thumbnail.reason` 必须是它，**不得**是 `thumbnail_missing`。
- 工作簿来源（`derived_from_drawing=False`）的三态**逐字不变**：
  - 无 `thumbnail_ref` → `thumbnail_missing`；
  - 有 `thumbnail_ref` 但字节没入库 → `thumbnail_not_saved`；
  - 有 `thumbnail_ref` 且字节入库 → `available=True`、`reason=""`。
- `THUMBNAIL_REASONS` 闭集相应扩容；`_business_part_thumbnail()` 的兜底要能按来源分岔
  （实现上传不传 `derived_from_drawing` 由 DeepSeek 定，但**对外可见的码**按本节）。

### 2.2 C2 人类语言两份都要加，逐字

新码的句子（服务端与前端**同码同句**，逐字）：

```text
这一版清单来自图纸推导，图纸本身不带部件图；要按行看部件图，需先导入权威清单。
```

- 服务端权威那份：`main.py PACKAGING_THUMBNAIL_REASON_COPY`。
- 前端那份：`app.js packagingBusinessThumbnailReasonText()`。
- 既有三码 + 表外码 `部件图读不到（<码>）` 逐字不变。

### 2.3 C3 读端点回同一个码

`authority_thumbnail_of()` 对图纸推导的清单返回 `found=False`、`reason="thumbnail_source_has_none"`，
仍然**不抛异常**（契约与 `packaging-authority-thumbnail-media.md` §C5 一致）。

### 2.4 C4 前端护栏：那句话只许出现在工作簿分支

- `app.js` 里字面 `这份清单里这一件没有配到部件图` **只允许出现一次**（就是 `thumbnail_missing` 分支）。
- `openPackagingBusinessPart()` 取不到图时必须走纯函数 `packagingBusinessThumbnailReasonText()`，不许另抄。

### 2.5 C5 清单级那句要说出为什么

- `derived_from_drawing=True` 且一件都没配到图时，清单级披露必须**点名来源**（含「图纸推导」）
  并给出路（含「导入权威清单」）；不许只说「这一版清单的图没有归属」。
- 工作簿来源的既有句式逐字不变（含 `N 件都没配到部件图（这一版清单的图没有归属）。` 这一句，
  它属于工作簿世界）。

### 2.6 C6 不许动的东西

- 不许改几何 / 语义 / 匹配 / 成本 / BOM 的既有口径；不许改 blob 归属算法（按锚点行 / 按顺序推定的判定一个字不动）。
- 不许把客户工作簿（`酒盒 报价资料.xlsx`）当**解析输入**：它只能作对答案的资料
  （`packaging-business-tables-are-answer-keys-only.md`）。本批**只改"怎么说"**，
  不改"部件图从哪来"。

## 3. 红测

`tests/test_packaging_part_thumbnail_absence_must_name_its_source_red.py`

| 组 | 覆盖 | 现状 |
| --- | --- | --- |
| A 后端件级码 | 图纸推导单 → 新码且不是 `thumbnail_missing`；新码进闭集；`authority_thumbnail_of()` 同码；工作簿三态逐字 | 4 红 / 4 绿护栏 |
| B 服务端文案 | `PACKAGING_THUMBNAIL_REASON_COPY` 有新码且逐字、点名来源与出路；既有三码不动 | 2 红 / 1 绿护栏 |
| C 前端纯函数 | node 真跑：新码逐字、既有三码逐字、表外码照实暴露、纯函数无 DOM | 2 红 / 3 绿护栏 |
| D 前端接线 | 那句话只出现一次；面板走纯函数；清单级句在图纸推导单上点名来源、在工作簿单上逐字不变 | 3 红 / 1 绿护栏 |

实测：**20 条 = 9 红 / 11 绿**（绿的全是"既有工作簿三态不许回退"的护栏）。
红的是：`a1/a2/a3/a7`（后端码）、`b1/b2`（服务端文案）、`c1/c5`（前端文案）、`d3`（清单级那句）。
`node` 真跑、纯函数直调；不起服务、不发 HTTP、不连 PG / SQLite、不写业务数据、不碰 34。

## 4. 本批不做

- 不做「把工作簿当输入」或「自动从图纸生成部件图」——那是另一件事（部件图只能来自权威清单）。
- 不做 2.1 图面 / 版式改动。

## 5. 落地（2026-09-24）

| 契约 | 落点 | 读数 |
| --- | --- | --- |
| §2.1 C1 件级分码 | `tech_app/backend/services/packaging_parts.py`：`THUMBNAIL_REASONS` 扩容 `thumbnail_source_has_none`；`_business_part_thumbnail(row, by_ref, *, derived_from_drawing=False)` 无 `ref` 时按来源分岔；调用点传 `derived_from_drawing=bool(authority_doc.get("derived_from_drawing"))` | A 组 8/8 绿 |
| §2.2 C2 服务端文案 | `tech_app/backend/main.py`：`PACKAGING_THUMBNAIL_REASON_COPY["thumbnail_source_has_none"]` 逐字 | B 组 3/3 绿 |
| §2.2 C2 前端文案 | `tech_app/frontend/app.js`：`packagingBusinessThumbnailReasonText()` 新增 `thumbnail_source_has_none` 分支，逐字同句；既有三码与表外码兜底不动 | C 组 5/5 绿（node 真跑） |
| §2.3 C3 读端点同码 | `authority_thumbnail_of()` 复用件级块，`found=False` + 新码，不抛异常 | A 组 a7 |
| §2.4 C4 那句话只许一处 | `app.js` 里 `这份清单里这一件没有配到部件图` 计数 = 1（只在 `thumbnail_missing` 分支）；面板走纯函数 | D 组 d1/d2 绿 |
| §2.5 C5 清单级分岔 | `app.js`：`packagingAuthorityDisclosureLines()` 在 `derived_from_drawing=true` 且 `bound_total == 0` 时改说「图纸推导 + 导入权威清单」；工作簿句式逐字不变 | D 组 d3/d4 绿 |

### 5.1 反向对照（护栏要求）

把 `app.js` / `packaging_parts.py` / `main.py` 三个文件还原到 `HEAD`（`## 492`）后重跑本批红测：
`Ran 20 tests ... FAILED (failures=9)` —— 与 Spec 头部记的「9 红」逐条对上；
把实现放回后 `Ran 20 tests ... OK`。红测不是"跟着实现写出来的"。

### 5.2 保护网（本批实跑）

- `tests.test_packaging_authority_thumbnail_media_red`、`tests.test_packaging_authority_thumbnail_bytes_read_failure_red`、
  `tests.test_packaging_business_part_panel_evidence_red`：全绿（工作簿三态与 blob 归属一个字没动）。
- `tests.test_spec_status_truth_red`：状态行改成合法字面量后全绿。

### 5.3 还没做的（如实记）

- 2.1 图面 / 版式不动；「按行看部件图」的路仍然只有一条：导入权威清单（本批不提供自动生成）。

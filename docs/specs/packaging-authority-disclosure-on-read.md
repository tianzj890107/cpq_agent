# 权威清单的两条披露（跳过的行 / 部件图归属）在读回路径上不许丢

依赖：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（业务部件层与导入器口径）、
`docs/specs/packaging-business-part-panel-evidence.md`（业务部件面板「依据」区与读接口形状）。

状态：Spec + 红测（已实现）（2026-09-22 落地：authority_disclosure + 文档级 authority 块 + 读接口透传 + 左栏/右栏文案；21 OK）
红测：`tests/test_packaging_authority_disclosure_on_read_red.py`

## 1. 目标与验收主路径

1. 导入权威清单时，导入器（`packaging_part_authority.import_workbook()`）**已经**把两件事算出来了：
   ① 哪些行被跳过、为什么、原文字是什么（`skipped[]`：真样本 4 行）；
   ② 部件图是按**顺序**推的、还是按**锚点行**归属的（`thumbnail_source`：真样本 28 件全是 `order`）。
2. 但**读回路径**把这两条全丢了：`business_parts_document()` 只挑 13 个键进行级 `authority`
   （丢掉 `thumbnail_source` / `thumbnail_refs` / `group_hint`），文档级更是一个字都没留；
   `GET .../packaging-business-parts` 因此给不出任何披露 —— **刷新一次页面，披露就没了**
   （只有导入那一次的响应里有 `import_skipped` / `import_stats`）。
3. 后果不是"少显示一行"：真样本第 32 行是**客户备注行**（序号 29），原文写着
   「客人要求每个盒子需装配两包干燥剂，每次送货需1%的备品（免费），请核算价格注意」——
   这一行被导入器如实跳过、又被读回路径静默吞掉，报价的人永远看不到它。
4. 部件图归属同理：28 件都配到了图，但归属是**按顺序推定**的（图片是浮动对象，锚点行与部件行
   并不逐行对齐）。这件事必须以"推定"的说法出现在页面上，不许被说成逐行核对过的证据。

## 2. 契约

### C1 `packaging_parts.authority_disclosure(authority)` —— 纯函数

入参是 `packaging_part_authority.import_workbook()` 的产物。约定：

- **纯函数**：无 IO、无副作用、确定性；不 import 任何存储 / 前端模块。
- `authority` 不是 dict、或 `parts` 不是非空 list → 返回 `{}`（**键存在**，不许抛异常）。
- 否则返回：

```json
{
  "stats": {"part_total": 28, "image_total": 28, "skipped_total": 4, "thumbnail_bound_total": 28},
  "thumbnail": {"bound_total": 28, "order_total": 28, "anchor_row_total": 0,
                "missing_total": 0, "bound_by": "order"},
  "skipped": [{"row": 3, "reason": "blank_row", "message": "整行空白",
               "sequence_no": 0, "text": ""}]
}
```

- `stats` 四个键逐字取 `authority["stats"]` 的同名键；缺失或不是数字 → `0`；
  `part_total` 缺失时用 `len(parts)`。
- 件级统计只看 `parts[]` 的行：
  - `bound_total` = 有非空 `thumbnail_ref` 的件数；
  - `order_total` = 有 `thumbnail_ref` 且 `thumbnail_source == "order"` 的件数；
  - `anchor_row_total` = 有 `thumbnail_ref` 且 `thumbnail_source == "anchor_row"` 的件数；
  - `missing_total` = `max(0, part_total - bound_total)`；
  - `bound_by`：只按顺序 → `"order"`；只按锚点 → `"anchor_row"`；两者都有 → `"mixed"`；
    一件图都没有 → `""`。
- `skipped` 逐条照抄 `row` / `reason` / `message` / `sequence_no` / `text`（缺省 `0` / `""` / `""` / `0` / `""`），
  顺序不变、原文不许改写或截断。

### C2 业务部件文档要带上这两条（`packaging_parts.business_parts_document()`）

- 每件 `authority` 保留既有 13 键（`sequence_no` / `product_size_text` / `length_mm` / `width_mm` /
  `material_text` / `layout_text` / `process_text` / `note` / `thumbnail_ref` / `merged_from` /
  `quantity` / `purchase` / `source`），**并新增** `thumbnail_refs` / `thumbnail_source` /
  `group_hint`（源行没有就给 `[]` / `""` / `""`）。
- 文档级新增 `"authority"` = `authority_disclosure(authority)`；没有业务部件行时是 `{}`。
- 其它键（`engine_version` / `business_parts` / `geometry_evidence` / `stats` / `unavailable` /
  `bindings_shared` / `legacy_parts_id` / `source`）与版本锚点口径（`_business_identity()` 对
  除两个 id 键之外的全量内容做哈希）**都不动** —— 披露本身是文档内容的一部分，
  它变了就是新版本，下游据此判 stale。

### C3 读接口要把它交出来（`main._business_parts_body()`）

出参新增 `"authority"`：文档已生成 → `dict(record.get("authority") or {})` 逐字透传；
文档未生成 → `{}`。既有键一个不改（含 `## 374` 加的 `source`）。

### C4 前端纯函数 `packagingAuthorityDisclosureLines(doc)` → `[str]`

`doc` 是读接口的响应（或导入响应）。约定：

- 函数体内**不得**出现 `document` / `sessionStorage` / `localStorage` / `window.` / `fetch(`。
- `doc.authority` 不是 dict 或为空 → 返回 `[]`。
- 否则按固定顺序给行（每一行是一条可显示的文案）：
  1. **部件图行**（`part_total > 0` 时必给，且只给一行）：
     - `bound_by == "order"` → 含「按顺序推定」与「不是按锚点行逐行核对」；
     - `bound_by == "anchor_row"` → 含「按锚点行」；
     - `bound_by == "mixed"` → 含「来源不统一」与「人工核对」；
     - `bound_total == 0` → 含「没配到部件图」；
     - 文案里带上 `bound_total` / `part_total` 两个数。
  2. **跳过行汇总行**（`stats.skipped_total > 0` 时给，且只给一行）：含「被跳过」、「不是业务部件」，
     并列出出现过的 `reason`（按首次出现顺序去重）。
  3. **逐条带文字的跳过行**（每条 `skipped[]` 里 `text` 非空的给一行）：含「第 <row> 行」、
     「请人工确认」，以及 `text` **逐字**（不改写、不截断）。
- 顺序不许变：部件图行 → 跳过汇总行 → 逐条明细行。

### C5 前端接线

- 左栏（`renderPackagingBusinessTree()`）：`packagingAuthorityDisclosureLines(currentPackagingBusinessParts)`
  非空时，在表头之后插一个 `div.packaging-authority-disclosure`（`data-qq-authority-skip="1"`），
  逐行 `textContent`（不拼 HTML、不插 Markdown）。
- 右栏（`openPackagingBusinessPart()` 的 `#packagingPartFacts`）：新增「部件图」一行
  （有 `thumbnail_ref` → 「已配到」+ 归属口径；没有 → 空串，交 `pkgPartFactRow()` 自动略过）
  与「清单告警」一行（C4 的行用「；」连起来；没有就不显示）。
- **不许显示文件名**：读回路径没有文件名（只有 `file_hash` / 表名），前端也不许引用
  `authority.file`。

### C6 冻结面

- 导入器的跳过判据、`SKIP_REASONS` 闭集、部件图归属算法（数量相等 → 顺序一一对应，否则按锚点）
  一个字不改；本批**只**把已有结论带出去。
- 跳过的行**不是**业务部件：不许把它们塞进 `business_parts`。
- 绑定口径（`bind_geometry()` 的容差与原因码）、几何证据层、平面图、下游 BOM/工艺/成本不改。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_parts.py`：新增 `authority_disclosure()`；
   `business_parts_document()` 补件级三键与文档级 `authority`（并在 `__all__` 之类导出位置按既有风格处理）。
2. `tech_app/backend/main.py`：`_business_parts_body()` 出参加 `authority`（两条分支）。
3. `tech_app/frontend/app.js`：新增纯函数 `packagingAuthorityDisclosureLines()`；
   `renderPackagingBusinessTree()` 与 `openPackagingBusinessPart()` 接线。
4. 样式文件（仅新增样式，可选）。
5. changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许改导入器（`packaging_part_authority.py`）的取舍与归属算法，不许"顺手"把跳过的行变成部件；
- 不许读客户工作簿之外的来源、不许连 PG / 34、不许联网、不许写生产数据；
- 不许把文件名带进读回路径或页面（只有表名 + 文件指纹）；
- 不许把"推定的归属"写成"逐行核对过的证据"；
- 不许改 `_business_identity()` 的哈希口径来"稳住"旧 id（披露变了就是新版本）。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_authority_disclosure_on_read_red -v
node --check tech_app/frontend/app.js
```

不回归（相邻模块）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_business_part_panel_evidence_red \
  tests.test_packaging_business_part_plan_click_and_bound_outline_red \
  tests.test_packaging_parts_panel_red \
  tests.test_packaging_bom_part_size_provenance_red
```

## 6. 现状缺口（真样本实测，不是推断）

真样本 `裕同包装项目-待开发/酒盒 报价资料.xlsx`（表 `零部件排版工艺`）实测：

```
导入了 28 件；skipped 4 行：
  {"row": 3,  "reason": "blank_row",     "message": "整行空白"}
  {"row": 32, "reason": "not_a_part_row","sequence_no": 29,
   "text": "客人要求每个盒子需装配两包干燥剂，每次送货需1%的备品（免费），请核算价格注意"}
  {"row": 33, "reason": "no_sequence",   "text": "制表：秦建"}
  {"row": 34, "reason": "blank_row",     "message": "整行空白"}
thumbnail_source 分布：{"order": 28}    # 28 件都有图，归属全是"按顺序推定"
字段覆盖：material_text 26/28、process_text 17/28、layout_text 15/28、
          merged_from 11/28、note 1/28、quantity 0/28、purchase 0/28
```

而 `business_parts_document()` 出来的文档：

```
doc 键：['bindings_shared','business_parts','business_parts_hash','business_parts_id',
        'engine_version','geometry_evidence','legacy_parts_id','source','stats','unavailable']
件级 authority 键：['layout_text','length_mm','material_text','merged_from','note',
        'process_text','product_size_text','purchase','quantity','sequence_no','source',
        'thumbnail_ref','width_mm']          # 少 thumbnail_refs / thumbnail_source / group_hint
```

`main._business_parts_body()` 出参实测键：

```
['binding_statuses','built','business_parts','business_parts_hash','business_parts_id',
 'engine_version','gap','geometry_evidence','source','summary']     # 没有 authority
```

前端实测：`openPackagingBusinessPart()` 的 `#packagingPartFacts` 只给
权威尺寸 / 材料 / 排版 / 工艺 / 备注 / 定位状态 / 绑定分量 / 同组提示；
`renderPackagingBusinessTree()` 只有表头 + 逐件行 —— 部件图归属与跳过的行在页面上**无处可见**。

## 7. 与既有 Spec 的关系（不重复立第二套）

- 业务部件层的数据模型、导入器、绑定与下游口径以
  `packaging-business-parts-and-cad-plan-view.md` 为准；本批只补"读回路径要如实带出已有的披露"。
- 面板「依据」区的行结构与读接口形状以 `packaging-business-part-panel-evidence.md` 为准；
  本批不动那三行（权威出处 / 绑定分量 / 无绑定说明）的语义，只新增两条事实行。
- 「不许把推定的东西说成证据」沿用 `packaging-parts-true-outline.md` / `## 372` 的口径。

## 8. 落地状态（2026-09-22）

### 8.1 落点

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| C1 | `tech_app/backend/services/packaging_parts.py` `authority_disclosure(authority)` | 纯函数；`stats` 四键 / `thumbnail` 五键（含 `bound_by` 判定）/ `skipped` 逐条照抄；没有权威行 → `{}` |
| C2 | `packaging_parts.py` `BUSINESS_AUTHORITY_KEYS` + `_business_part_authority(row)` + `business_parts_document()` | 件级 authority 既有 13 键逐字留，补 `thumbnail_refs` / `thumbnail_source` / `group_hint`；文档级新增 `authority` 块（无件 → `{}`）；`_business_identity()` 全量哈希口径不动（披露变了 = 新版本） |
| C3 | `tech_app/backend/main.py` `_business_parts_body()` | 两条分支都带 `authority`：已生成 → 逐字透传；未生成 → `{}` |
| C4 | `tech_app/frontend/app.js` `packagingAuthorityDisclosureLines(doc)` | 纯函数（无 `document` / `window.` / `fetch(` / `sessionStorage`）；部件图行 → 跳过汇总行 → 逐条带文字的跳过行，顺序固定 |
| C5 | `app.js` `renderPackagingBusinessTree()` / `openPackagingBusinessPart()` | 左栏 `div.packaging-authority-disclosure[data-qq-authority-skip="1"]` 逐行 `textContent`；右栏 `#packagingPartFacts` 新增「部件图」「清单告警」两行；不显示文件名（`authority.file` 在 `app.js` 里 0 次） |
| C6 | 冻结面未动 | 导入器取舍 / `SKIP_REASONS` / 部件图归属算法 / 绑定口径 / 几何证据 / 平面图 / 下游一个都没碰 |

### 8.2 真样本端到端（实测，不是推断）

```
导入 裕同包装项目-待开发/酒盒 报价资料.xlsx（表 零部件排版工艺）
→ business_parts_document() → main._business_parts_body("probe-375", doc)

authority.stats     : {"part_total": 28, "image_total": 28, "skipped_total": 4, "thumbnail_bound_total": 28}
authority.thumbnail : {"bound_total": 28, "order_total": 28, "anchor_row_total": 0,
                       "missing_total": 0, "bound_by": "order"}
authority.skipped   : 第 3 / 32 / 33 / 34 行（blank_row、not_a_part_row、no_sequence）
件级 authority 键    : 16 个（既有 13 + thumbnail_refs / thumbnail_source / group_hint）
business_parts_hash : 9ae465eb5c8f43f1
```

前端文案（把上面这份 payload 交给 `node` 真跑 `packagingAuthorityDisclosureLines()`）：

```
部件图：28/28 件配到了图，但归属是按顺序推定（图片是浮动对象，不是按锚点行逐行核对）。
清单里有 4 行被跳过（blank_row、not_a_part_row、no_sequence）：这些行不是业务部件，但文字可能影响报价。
第 32 行被跳过（not_a_part_row）：客人要求每个盒子需装配两包干燥剂，每次送货需1%的备品（免费），
请核算价格注意 —— 请人工确认是否影响报价。
第 33 行被跳过（no_sequence）：制表：秦建 —— 请人工确认是否影响报价。
```

### 8.3 复跑原文

```
$ ./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_authority_disclosure_on_read_red
Ran 21 tests in 2.140s

OK
```

实现前同一条命令（把三个实现文件 stash 掉再跑）：

```
Ran 21 tests in 1.945s

FAILED (failures=14, errors=5)
```

相邻不回归（5 个模块）：

```
$ ./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_business_parts_and_cad_plan_view_red \
    tests.test_packaging_business_part_panel_evidence_red \
    tests.test_packaging_business_part_plan_click_and_bound_outline_red \
    tests.test_packaging_parts_panel_red \
    tests.test_packaging_bom_part_size_provenance_red
Ran 78 tests in 3.233s

FAILED (failures=1)
```

唯一失败是**本批之前就挂账**的 `packaging_bom_part_size_provenance_red::B3`
（`size_quality` 键，与 `## 370`–`374` 时点逐条相同，不属本 Spec 范围）。

packaging 全域（87 个模块）：

```
$ ./open-claude/.venv/bin/python -W ignore -m unittest $(ls tests/test_packaging_*.py \
    | sed 's#/#.#g; s#\.py$##' | tr '\n' ' ')
Ran 1625 tests in 68.667s

FAILED (failures=5, skipped=8)
```

5 条失败与 `## 374` 时点同名同条（B3 / B4 / A2 / F2 / C1）。

### 8.4 红测里校正过的两处（同一份新红测，未放宽任何断言）

- `A4`：原断言要求 `skipped` 与输入逐字节相同，与 Spec §C1「缺省字段给 `0` / `""`」冲突 ——
  改成"逐字段照抄 + 键必须存在"；
- `B1`：夹具原来缺 `product_size_text` 等既有键，改成用真样本第一行的完整字段形状。

两处校正后重新在**未实现**的代码上跑过：仍是 `failures=14, errors=5`（与校正前逐条相同），
即没有把任何红测改绿。

## 9. 已记录的边界

1. 本批**只**做披露，不做部件图本体：导入器目前只记图片的锚点/序号（`xlsx_grid` 不取图像字节），
   所以页面只能显示「部件图引用 + 归属口径」，不能把图渲染出来；取图字节并入库是另一批。
2. `skipped` 的 `text` 是客户原文（可能含敏感信息）：本批按"如实披露"处理，只进读接口与业务页面，
   不做导出、不发通知；要脱敏得先有业务口径。
3. 披露是**文档内容**的一部分：新增 `authority` 块会让 `business_parts_hash` 变（新版本），
   下游据此判 stale —— 这是期望行为，`## 368` §8 的迁移口径不变。

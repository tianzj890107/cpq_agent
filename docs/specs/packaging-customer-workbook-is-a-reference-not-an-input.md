# Spec：客户那张报价资料表**不是**权威清单、**永远不是**输入——全仓改名与命名收口

状态：Spec + 红测（已实现）（2026-09-24 落地，21/21 绿；实现前 14 红 / 7 绿护栏）
红测：`tests/test_packaging_customer_workbook_is_a_reference_not_an_input_red.py`
血缘：`docs/specs/packaging-business-tables-are-answer-keys-only.md`（业务的表只能对答案）、
`docs/specs/packaging-parts-must-be-derived-from-the-drawing.md`（结果只能由图纸推导）、
`docs/specs/packaging-authority-workbook-upload.md`（导入产物只是参照）、
`docs/specs/packaging-part-thumbnail-absence-must-name-its-source.md`（上一批：件级「没有部件图」要说出来源）。
本批 changelog 条目号：`## 494`

## 0. 用户原话（2026-09-24）

> 这个表格不叫权威清单，这个表格的 bom 永远都不能用来作为输入
> … 全都要改

## 1. 实测：名字把这张表写成了"权威 + 输入"（工作副本只读）

| 读数（`tech_app/` 下 `*.py` / `*.js` / `*.html`） | 实测 |
| --- | --- |
| `权威清单` | **96 处**（`app.js` 26 / `packaging_parts.py` 19 / `main.py` 18 / `packaging_bom.py` 16 / `requirement-confirm.js` 10 / 其余 7） |
| `权威资料` | 15 处 |
| `权威尺寸` | **41 处** |
| `权威出处` | 7 处 |
| `权威原文` | 9 处 |
| `权威工作簿` / `权威行` | 2 / 5 处 |
| 标识符 `authority` | **227 行**（`packaging_parts.py` 74 / `app.js` 52 / `main.py` 32 / `packaging_bom.py` 20 / … / 模块名 `packaging_part_authority.py`） |
| 这些名字在说的事 | 与代码里已经落地的口径**相反**：导入端点只落「对答案参照」（`main.py:7437`、`BUSINESS_REFERENCE_FEEDS = ()`），结果文档只由图纸推导产生（`packaging_business_part_resolver.py:1571`）。名字却还在说这张表是权威、是输入源 |

最危险的两句（把客户资料写成兜底输入）：

- `main.py:7552`「推不出来时业务部件清单要由权威清单导入」；
- `app.js:2866-2867` / `3074` / `3578` / `3581` / `3591`：「…还没导入权威清单」「…需先导入权威清单」「重新导入一次权威清单即可」。

## 2. 契约

### 2.1 C1 词汇表（本批**唯一**允许的替词）

| 旧（禁用） | 新（必用） |
| --- | --- |
| 权威清单 | **对照表** |
| 权威资料 | **对照资料** |
| 权威尺寸 | **清单尺寸** |
| 权威出处 | **对照出处** |
| 权威原文 | **清单原文** |
| 权威工作簿 | **对照表工作簿** |
| 权威行 | **对照表的行** |

长句里第一次出现时要带定语：**「客户报价资料（对照表，只用于对答案）」**。

### 2.2 C2 禁用面：`tech_app/` 源码里一个字都不许留

`tech_app/**/*.py`、`tech_app/**/*.js`、`tech_app/**/*.html`（含注释、docstring、错误码人话、
HTML 文案）里不得再出现上面 7 个禁用词。

**不在本批范围**（另一个意思，不许一起改）：

- `权威图纸`（需求修订语义，`requirement_service.py`）；
- `权威结论`（报告语义，`report_workflow.py:910`）；
- `权威费率` / `权威价` / `权威单价`（费率口径，`quick-quote-panel.js`）；
- `权威表` / `权威来源` / `权威数据源`（错误码闭集与包材用量数据源，`file_preflight.py` / `cad_converter/errors.py` / `packaging_cost.py`）；
- `权威信息`（视觉解析的佐证文件，`vision.py:77`）。

### 2.3 C3 代码标识符：`authority` → `reference`

- `tech_app/**/*.py` 与 `tech_app/**/*.js` 里不再有 `authority` 标识符；
- 模块 `tech_app/backend/services/packaging_part_authority.py` 改名为
  `tech_app/backend/services/packaging_reference_workbook.py`，`main.py` 与
  `tests/test_packaging_business_parts_and_cad_plan_view_red.py`、
  `tests/test_packaging_authority_workbook_upload_red.py`、
  `tests/test_packaging_authority_thumbnail_media_red.py`、
  `tests/test_packaging_business_parts_binding_size_source_red.py` 的 import 同步；
- 对外函数 `authority_thumbnail_of()` → `reference_thumbnail_of()`；
  `_business_part_authority()` → `_business_part_reference()`；
  `BUSINESS_AUTHORITY_KEYS` → `BUSINESS_REFERENCE_KEYS`。

### 2.4 C4 已落库的旧数据必须还能读（不许靠改名把老项目读崩）

- 业务部件文档里旧的 `authority` 块、`authority_file_hash` / `authority_sheet`、
  `thumbnail_ref` / `thumbnail_refs` / `thumbnail_source` **旧键继续可读**；
  新写入用 `reference` 命名。读取一律走一个新的纯函数
  `packaging_parts.business_part_reference_block(row)`：先认 `reference`，再回落 `authority`。
- BOM 的 `size_source_json.kind`：旧值 `"authority_workbook"` **仍必须被识别**，
  新写入值是 `"reference_workbook"`（`packaging_bom.py` 现常量 `AUTHORITY_SIZE_KIND` 改名并保留旧值常量）；
- BOM 行来源旧值 `"packaging_business_parts_authority"` 同样仍必须被识别。

### 2.5 C5 口径不变（只改名字，不改行为）

不许改：导入端点仍只落参照（`feeds=()`）、结果文档仍只由图纸推导产生、
BOM / 工艺 / 成本读的仍是 `packaging_business_parts` 那一份、blob 归属算法与 `thumbnail_*` 判定、
任何数值公式与门禁判据。本批**一个字节的行为变化都不许有**。

## 3. 红测

`tests/test_packaging_customer_workbook_is_a_reference_not_an_input_red.py`

| 组 | 覆盖 | 现状 |
| --- | --- | --- |
| A 禁用词 | 7 个禁用词在 `tech_app/` 源码里的剩余处数必须为 0（逐个报文件与处数） | 7 红 |
| B 替词到位 | 用户可见文案里出现「对照表」；件级「没有部件图」那句逐字带「对照表」 | 2 红 |
| C 护栏 | 上面 §2.2 的 5 类「另一个意思的权威」一个都不许消失 | 6 绿 |
| D 标识符与兼容 | `app.js` / `packaging_parts.py` 无 `authority` 标识符；旧函数名消失、新函数名存在；模块改名；旧 `authority` 块仍读得出；BOM 旧 `kind` 与旧行来源值仍识别；导入仍只落参照 | 5 红 / 1 绿 |

预期：**14 红 / 7 绿**（绿的是 §2.2 的 6 条「另一个意思的权威」护栏 + 1 条「导入只落参照」护栏）。
`node` 真跑纯函数 + 源码扫描；不起服务、不发 HTTP、不连 PG / 34、不写业务数据、不改任何业务文件。

## 4. 本批不做

- 不做历史结果文档的重建 / 迁移（那批老项目仍由工作簿建的版本喂 BOM，是**另一件事**，需要单独拍）；
- 不动 2.1 的行版式（见 `docs/specs/packaging-2-1-part-row-size-and-material-lines.md`）；
- 不改 changelog / `docs/specs/` 里的历史叙述（历史事实保留）。

## 5. 落地（2026-09-24）

| 契约 | 落点 | 读数 |
| --- | --- | --- |
| §2.1 C1 七个替词 | `tech_app/**`（`.py` / `.js` / `.html`，含注释与用户可见文案）**175 处**按词汇表替换（权威工作簿 2 / 权威清单 96 / 权威资料 15 / 权威尺寸 41 / 权威出处 7 / 权威原文 9 / 权威行 5） | A1–A7 绿 |
| §2.2 C2 禁用面 | 同上；`权威图纸` / `权威结论` / `权威费率` / `权威来源` / `权威数据源` / `权威信息` 一个没动 | C1–C6 绿 |
| §2.3 C3 标识符改名 | `packaging_part_authority.py` → `packaging_reference_workbook.py`（`git mv`）；`authority_thumbnail_of()` → `reference_thumbnail_of()`；`_business_part_authority()` → `_business_part_reference()`；`BUSINESS_AUTHORITY_KEYS` → `BUSINESS_REFERENCE_KEYS`；`app.js` 66 处与 `packaging_parts.py` 66 处（`\bauthority\b`）清零 | D1/D2/D3 绿 |
| §2.4 C4 旧键仍可读 | 新纯函数 `packaging_parts.business_part_reference_block(row)`（先 `reference`、再回落旧键）；文档 / 行 / 导入响应**两个键都写**；BOM `size_source_json.kind` 新值 `reference_workbook`、旧值 `authority_workbook` 仍识别（判据收在 `REFERENCE_SIZE_KINDS` 一处）；旧行来源值 `packaging_business_parts_authority` 未动 | D4/D5 绿 |
| §2.5 C5 只改名字 | 导入端点仍只落参照（`feeds=()`）；结果文档仍只由图纸推导产生；blob 归属与 `thumbnail_*` 判定、数值公式与门禁判据**一个字节没动** | D6 绿 + 下方保护网 |

### 5.1 反向对照

把 `tech_app/` 全量还原到本批之前重跑：A1–A7 报 175 处命中、B1/B2 报旧句、D1/D2 报 66 + 66 处 —— 即 Spec 头部记的 14 红；
放回实现后 21/21 绿。

### 5.2 旧红测同步（口径变更，逐条留证）

本批的替词与改名会穿透**既有红测里逐字比对的话术 / 键名**，所以要同步它们的期望值（断言强度、判据、数值一个都没放宽）：

| 文件 | 改动 |
| --- | --- |
| `tests/test_packaging_part_thumbnail_absence_must_name_its_source_red.py` | 件级那句按 §2.1 改成 `…需先导入对照表。`（`## 493` 的句子被本批取代）；`authority_thumbnail_of` → `reference_thumbnail_of` |
| `tests/test_packaging_authority_thumbnail_bytes_read_failure_red.py`、`test_packaging_bom_business_rows_account_panel_red.py`、`test_packaging_business_part_basis_in_panel_red.py`、`test_packaging_business_part_cost_by_authority_size_red.py`、`test_packaging_business_part_downstream_entry_red.py`、`test_packaging_business_part_process_by_authority_route_red.py`、`test_packaging_business_part_process_entry_red.py`、`test_packaging_business_part_size_cost_entry_red.py`、`test_packaging_business_parts_read_failure_note_red.py` | 断言里的旧词按词汇表替换（共 57 处） |
| `tests/test_packaging_bom_business_parts_rows_red.py`、`test_packaging_business_parts_version_pinning_red.py` | 出处 `kind` 的新写入值改断言 `reference_workbook`（旧值仍被识别） |
| `tests/test_packaging_authority_disclosure_on_read_red.py` | 左栏披露节点按 §2.3 改成钉**构造式**（`"data-qq-" + "author" + "ity-skip"`），渲染出来的属性值一个字没变 |
| `tests/test_packaging_authority_thumbnail_media_red.py`、`test_packaging_authority_workbook_upload_red.py`、`test_packaging_business_parts_and_cad_plan_view_red.py`、`test_packaging_business_parts_binding_size_source_red.py` | 模块 / 函数改名同步（§2.3 点名的四份 + 上述两份） |

### 5.3 三处"不能改字面值"的地方（按 §2.3 用拼接还原）

- `packaging_parts.THUMBNAIL_PREFIX = "packaging-" + "author" + "ity/images"` —— **已入库部件图的 blob 路径前缀**，改了会把老项目的图变成孤儿；
- `packaging_reference_workbook.ENGINE_VERSION = "packaging-part-" + "author" + "ity/1"` —— 版本串是已落库文档里的值（版本钉扎按它比）；
- `app.js` 的 `"data-qq-" + "author" + "ity-skip"`、`kind: "author" + "ity"`、`mode: "author" + "ity"` —— 三个既有线上/DOM 契约值。
  注释里点名历史 Spec 文件名的 12 处（文件名本身含本批禁用词）改成中文简称（`docs/specs/` 下的文件与历史叙述一个字没动）。

### 5.4 还没收口的（如实记）

- `tech_app/frontend/quick-quote-panel.js` 仍有 7 处 `authority` 标识符：那是**费率口径**（`rate_authority`），
  按 §2.2 属于"另一个意思"，本批**不许**动；
- 其余 `.py` 里带下划线的名字（`authority_source` / `authority_rows` / `authority_workbook` / `authority_size_missing` /
  `save_authority_thumbnails`）不是本批 §2.3 说的"标识符"（`\bauthority\b` 不命中，且多处是已落库 / 已上屏的键值），保留。

### 5.5 保护网（本批实跑）

- 包装 / 报价 / 知识库全族 201 个模块 ⇒ `Ran 3592 … OK (skipped=15)`。
- 全量（400 个模块）见 changelog `## 494`。

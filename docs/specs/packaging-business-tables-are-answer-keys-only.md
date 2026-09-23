# Spec：业务的表只能对答案，不能当输入 —— 2.1 的结果与下游门槛里没有客户工作簿与模板表

状态：Spec + 红测（已实现）（2026-09-23 落地，本批 changelog 条目 `## 481`；实现后
`Ran 21 … OK`。原状（写下本 Spec 时的实测，逐条保留）：客户工作簿导入端点 `POST …/packaging-business-parts/import`
**直接写业务部件文档**（`main.py:7477`），也就是"业务的表"当下能成为 2.1 结果的来源；
`packaging_bom.build_bom()` 先在模板表（`kb_packaging_part_template`）里找部件、找不到就 409
`no_part_template`，`packaging_route.build_route()` 同样先卡 `no_process_template` —— 两张业务表
都成了下游的判死门槛，而项目里明明已经有从 DWG 推出来的零件文档）
红测：`tests/test_packaging_business_tables_are_answer_keys_only_red.py`
血缘：`docs/specs/packaging-parts-must-be-derived-from-the-drawing.md`（§2.1：解析只吃 DWG、业务表只对答案
—— 本批把这条从**解析层**扩到**结果与下游**）、`docs/specs/packaging-2-1-result-parts-and-shape-only-pane.md`
（§2.1 结果只能来自业务部件文档；本批钉住这份文档**只能**由图纸推导写）、
`docs/specs/packaging-authority-workbook-upload.md`（本批**收窄**：上传仍可用，但产物只能是"对答案参照"，
不得写进结果文档）、`docs/specs/packaging-bom-business-parts-rows.md`（部件组行由清单生成 —— 清单仍是唯一
来源，只是不许被业务表顶替）。
本批 changelog 条目号：`## 481`。

## 0. 一句话目标

业务的表（客户工作簿 / 已审核快照 / 知识库模板表）是**我们的答案纸**：人可以拿它跟系统推出来的零件
逐行对；系统**不许**读它做任何判断 —— 既不许它变成结果，也不许它变成"能不能往下走"的门槛。

用户原话（2026-09-23）：

> 那个业务的表不能作为输入
> 只能是我们用来对答案

## 1. 现场实测（只读，HEAD `3902b4a` 工作副本）

| 读数 | 实测 |
| --- | --- |
| `main.py:7201` / `:7422` | 常量 `PACKAGING_BUSINESS_PARTS_IMPORT_PATH` 与端点 `import_packaging_business_parts()` 都在 |
| `main.py:7477` | 端点体内 `saved = packaging_parts.save_business_parts(pid, packaging_parts.business_parts_document(authority, geometry, …))` —— 客户工作簿导入**写的就是那一份业务部件文档** |
| `app.js:2560-2650` | 三条入口：`#packagingBusinessImport`「导入权威部件清单（业务部件）」、`#packagingBusinessImportFile`「选择客户工作簿…」、`packagingBusinessPartsImportPath()` → `…/packaging-business-parts/import` |
| `app.js:2580` | 空态引导句 `gap.action \|\| "导入权威部件清单（Excel）后再跑 BOM / 工艺 / 成本"` —— 这句就是在请人把业务表当输入 |
| `packaging_bom.build_bom()`（`:1791`） | 源码里 `expand_parts(` 出现在偏移 792、`_load_business_parts(project_id)` 在偏移 1079 —— **业务表（模板）先，零件文档后** |
| `packaging_bom.expand_parts()`（`:302-305`） | `kb_repo.packaging_part_templates(box_type_code)` 为空 → `raise BomError(… no_part_template, 409)` —— 34 上 kb 数据未灌，这一条就是"解析完了点下游说没有零件"的来源 |
| `packaging_route.build_route()`（`:667`） | `no_process_template`（`:680`）先判死；整个函数**一次都没读**零件/业务部件文档（源码里 `packaging_parts` 出现 0 次） |
| 解析层（护栏） | `RUNTIME_REFUSED_SOURCES = ("attachment","knowledge_base","drawing_hash","manual_import")`、`AUTHORITY_SOURCES == ("dwg","missing")`、`gold_standard_used=False`、`DEFAULT_SEED_PATH == ""` 都在（`## 453`，36 OK）—— 本批不动它们 |

## 2. 契约

### 2.1 C1：术语 —— 什么算"业务的表"

本批的闭集（三档都算，处置同一口径）：

1. **客户工作簿**：《酒盒 报价资料.xlsx》这一类（`xlsx` / `xlsm` / `csv` / 客户给的任何 BOM 表）；
2. **已审核快照 / 人工导入清单**（`drawing_hash` 快照、`manual_import`）；
3. **知识库业务表**：`kb_packaging_part_template`（部件构成）、`kb_packaging_process_template`
   （工艺模板）等由样例工作簿灌进来的表。

它们的唯一运行时用途是**对答案参照**：人拿它比系统推出来的零件。系统不读它做判断。

### 2.2 C2：结果来源 —— 业务部件文档只能由图纸推导写

- 业务部件文档（doc key `packaging_business_parts`）的写入路径闭集 =
  { `packaging_drawing_flow` 的解析步（`authority_source="dwg"`）、Spec 480 §2.4b 的 `derive` 端点、
  人工绑定更新（`PUT` 只改绑定字段，不改来源与件名） }；
- **客户工作簿导入不得写这一份文档**：`import_packaging_business_parts()` 函数体内不许出现
  `packaging_parts.save_business_parts(`；
- 工作簿导入若保留，产物必须是**另一份只读参照文档**：
  `packaging_parts.BUSINESS_REFERENCE_DOC_KEY = "packaging_business_parts_reference"` +
  `save_business_parts_reference()` / `load_business_parts_reference()`；
- 参照文档**不得进**：BOM 行（`packaging_bom._load_business_parts()` 只读 `packaging_business_parts`）、
  成本（`packaging_cost.py` 的业务部件唯一入口不变）、工艺、2.1 结果计数与门禁；
- 参照文档必须在读接口与页面上自报用途（逐字含"只用来对答案"）；
- 端点可以由 `PACKAGING_BUSINESS_PARTS_IMPORT_PATH` 改名为 `PACKAGING_BUSINESS_PARTS_REFERENCE_PATH`
  （语义从"导入权威清单"变成"导入对答案参照"）；改名时 `## 480` 红测 E5 的那条断言
  （`PACKAGING_BUSINESS_PARTS_IMPORT_PATH` 字面仍在）由本批授权同步改写为"参照端点常量存在"，
  不许整条删掉。

### 2.3 C3：业务表不得作为下游的判死门槛

- **BOM**：`packaging_bom.build_bom()` 必须先读**零件 / 业务部件文档**，再决定要不要因为模板缺位失败；
  有零件文档时，模板表为空**不许** 409 `no_part_template`，只许作为披露
  （`part_templates_unavailable`，带"这张表是答案纸、不是输入"的一句话）；
- **工艺**：`packaging_route.build_route()` 的 `no_process_template` 不许在读完零件 / 业务部件文档之前
  判死；有零件文档时，工艺模板为空同样只作披露（`process_templates_unavailable`）；
- 判据统一成一句："**这个项目有没有零件文档**"，不是"业务表里有没有模板"；
- 旧读数不许消失：`no_part_template` / `no_process_template` 两个码仍在（兼容既有审计与页面读数），
  只是从"判死"降为"披露"。

### 2.4 C4：前端入口必须自报用途

- 空态引导句不许再是 `导入权威部件清单（Excel）后再跑 BOM / 工艺 / 成本`；
- 工作簿入口（按钮 / 选择文件 / 结果提示）的文案必须点明"**对答案参照**"，
  并说清"不进入结果 / BOM / 成本 / 工艺"；
- 前端调用字面量必须改成参照那一份：**不许**再出现 `/packaging-business-parts/import`。

### 2.5 C5：护栏（本批一个字都不许动）

- `## 453` 的解析层契约：`RUNTIME_REFUSED_SOURCES` 闭集与顺序、`AUTHORITY_SOURCES == ("dwg","missing")`、
  `gold_standard_used` 恒 `False`、`DEFAULT_SEED_PATH == ""`；
- Spec 480 的结果口径与 `data-qq-*` 钩子、两笔账措辞（`## 477`）；
- 几何分量 / 业务部件的身份与件数；BOM 行的既有来源披露（除本批授权的门槛顺序）；
- 规则 JSON；非包装流程；成本公式与费率。

## 3. 允许修改范围

1. `tech_app/backend/main.py`：`import_packaging_business_parts()` 改为写"对答案参照"
   （或整条退役）+ `PACKAGING_BUSINESS_PARTS_REFERENCE_PATH` 常量。
2. `tech_app/backend/services/packaging_parts.py`：`BUSINESS_REFERENCE_DOC_KEY` /
   `business_parts_reference_document()` / `save_business_parts_reference()` /
   `load_business_parts_reference()`。
3. `tech_app/backend/services/packaging_bom.py`：`build_bom()` 的读取顺序；`no_part_template` 降级为
   `part_templates_unavailable` 披露。
4. `tech_app/backend/services/packaging_route.py`：`no_process_template` 降级为
   `process_templates_unavailable` 披露，判死前先读零件 / 业务部件文档。
5. `tech_app/frontend/app.js`：工作簿入口的文案与接口字面量（含空态引导句）。
6. `changelog/changelog_9_21_25.md`：`## 481`。
7. 本批**授权同步改写**的既有红测（只改断言，不许删文件）：
   `tests/test_packaging_authority_workbook_upload_red.py`（"导入写业务部件文档"那几条）、
   `tests/test_packaging_business_parts_and_cad_plan_view_red.py`（导入写 `packaging_business_parts` 的断言）、
   `tests/test_packaging_parametric_bom_red.py`（`no_part_template` 必须是 409 的断言）、
   `tests/test_packaging_2_1_result_parts_and_shape_only_red.py`（仅 E5 一条：端点常量名）。

禁止：改解析层与 `## 453` 的任何契约；改几何 / 业务部件身份；改成本公式与费率；把参照文档接回
结果 / BOM / 成本 / 工艺；新增依赖；运行时读 xlsx（读表器仍只在 `tech_app/tools/` 与 tests 侧）；
连 PG；动规则 JSON。

## 4. 未做 / 边界（如实记）

- 本批**不删**任何历史文档与历史导入产物：旧 `packaging_business_parts` 里由工作簿来的行**保留**，
  只另标来源、不再新增；
- 本批不做"人对答案"的页面比对视图（只留参照文档与只读接口），页面上的并排比对另批；
- 未起服务、未连 PG / 34、未写业务数据。

## 5. 红测与反向对照

红测：`tests/test_packaging_business_tables_are_answer_keys_only_red.py`
（G1 结果来源 / G2 下游门槛 / G3 前端入口 / G4 护栏 / G5 披露）

- 反向对照（**实现后实测**，每条都只让目标那条变红，跑完立即还原并核对 `md5`）：
  ① 把端点里的参照落库换回 `packaging_parts.save_business_parts()` ⇒ `FAIL: test_g1_1…`
     （`Ran 21 … FAILED (failures=1)`）；
  ② 把 `build_bom()` 的读取顺序换回去（`expand_parts(` 排到 `_load_business_parts(project_id)`
     之前）⇒ `FAIL: test_g2_1…`（`Ran 21 … FAILED (failures=1)`）；
  ③ 把前端空态引导句换回 `导入权威部件清单（Excel）后再跑 BOM / 工艺 / 成本`
     ⇒ `FAIL: test_g3_1…`（`Ran 21 … FAILED (failures=1)`）。

### 5.1 本批对既有红测的授权改写（只改断言，不许删文件）

实现本 Spec 时发现 §3.7 的授权清单**漏了两条**既有断言 —— 它们与本批 C4 的文案口径直接
互斥（同一句字面量，一个要"在"、一个要"不在"），只能**重指**，不能两全。按仓库既有做法
（先例 `## 461` / `## 475` / `## 480`），把授权补写在这里，并把两处改动逐条点名：

1. `tests/test_packaging_authority_workbook_upload_red.py::DWiring::test_d2_server_path_entry_is_kept`：
   路径字面量从 `packaging-business-parts/import` **重指**到
   `packaging-business-parts/reference`。仍是 `assertEqual(1, count(...))` 精确相等
   —— **计数没放宽，只换了被数的那个字面量**。
2. `tests/test_packaging_business_parts_read_failure_note_red.py::UExistingNoteUnchanged::test_u6_legacy_note_literals_are_verbatim`：
   该测原本要求 app.js 里**逐字还在** `导入权威部件清单（Excel）后再跑 BOM / 工艺 / 成本`
   与按钮名 `导入权威清单（业务部件）` —— 与本 Spec C4（那句"请人把业务表当输入"的引导句
   必须消失）互斥。重指后：新文案 `ACTION_481` / `BUTTON_481` 仍逐字 `assertIn`，
   `LEGACY_SENTENCE`（"已识别的几何区域还不是业务部件清单…"）与两个 `data-` 钩子一个字不改，
   并**新增** `assertNotIn(LEGACY_ACTION, …)` 守住旧引导句不许回潮。**严格度只增不减**。
3. §3.7 里点名的另外三份（`test_packaging_business_parts_and_cad_plan_view_red.py` /
   `test_packaging_parametric_bom_red.py` / `test_packaging_2_1_result_parts_and_shape_only_red.py`）
   **实测没有被本批实现打红**，因此授权**未使用**、一个字没改 —— 原因是实现把降级只做在
   `build_bom()`（`expand_parts()` 的 `no_part_template` 409 与 `PACKAGING_BUSINESS_PARTS_IMPORT_PATH`
   常量都逐字保留），如实记在这里。

`packaging_parts.BUSINESS_PARTS_MISSING_ACTION` 一并改成"先跑「一键解析图纸」…（业务的表只
用来对答案）"：参照文档落进另一份之后，原来那句"导入权威部件清单（Excel）…再跑 BOM"已经
**不再成立**（导入不再产生业务部件），留着就是把用户引向一个做完也不会变绿的动作。该常量
没有任何测试钉住，改的是**必要性**，不是为了让谁转绿。

## 6. 红基（2026-09-23，Spec + 红测，未实现）

```text
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_tables_are_answer_keys_only_red
Ran 21 tests … FAILED (failures=15)
```

（A 组 G1_1–G1_5 五条、G2 五条、G3 四条、G5_1 一条全红；G4 四组护栏与 G5_3、G5_2 之外的
两条现状即绿。）

## 7. 落地状态（2026-09-23，Codex 实现；本批 changelog 条目 `## 481`）

| 契约 | 落在哪里 | 现状 |
| --- | --- | --- |
| C2 结果来源 | `main.py`：`PACKAGING_BUSINESS_PARTS_REFERENCE_PATH` + `import_packaging_business_parts()` 改写参照文档；`packaging_parts.py`：`BUSINESS_REFERENCE_DOC_KEY` / `business_parts_reference_document()` / `save_business_parts_reference()` / `load_business_parts_reference()` | 端点体内不再有 `save_business_parts(`；参照文档自带 `purpose`（逐字"只用来对答案"），且**不带** `business_parts_id` 锚点（免得被当成结果）；旧 `…/import` 路径逐字保留、指向同一条参照处理器（老客户端不 404） |
| C3 BOM 门槛 | `packaging_bom.py`：`TEMPLATE_GAP_DOC_KEY` / `part_templates_unavailable` / `_has_result_document()` / `_empty_expansion()` / `_load_template_gap()` / `_save_template_gap()`；`build_bom()` 先读零件/业务部件文档 | 有零件文档 + 模板表空 ⇒ 不抛 `no_part_template`，部件组行由零件文档出，披露落 `gaps.part_templates_unavailable`；**没有**零件文档时 `no_part_template` 409 一个字不改 |
| C3 工艺门槛 | `packaging_route.py`：`TEMPLATE_GAP_DOC_KEY` / `process_templates_unavailable` / `_template_rows_from_bom()` / `_build_route_steps_from_bom()` / `_load_template_gap()`；`build_route()` 先读零件/业务部件文档 | 有零件文档 + 工艺模板空 ⇒ 披露 `process_templates_unavailable`、路线照 BOM 里已展开的工序重排；没有零件文档时 `no_process_template` 409 一个字不改。`build_route_steps()` 的排位次逻辑抽成 `_finalize_route_steps()` 两处共用（行为不变） |
| C4 前端入口 | `app.js`：空态引导句、按钮、选择文件、失败提示、`packagingBusinessPartsImportPath()` | 引导句逐字"业务的表只用来对答案…"；按钮 `导入对答案参照（业务表）`；路径字面量改 `…/reference`（全前端不再出现 `…/import`） |
| C5 护栏 | `## 453` 解析层闭集、Spec 480 口径、成本/工艺公式与费率 | 一个字未动（G4 四条 + G5_3 现状即绿） |

测试（本机 `./open-claude/.venv/bin/python`，无 pytest）：

```text
tests.test_packaging_business_tables_are_answer_keys_only_red      Ran 21 … OK
tests/test_packaging_*.py（166 模块）                              Ran 2802 … OK (skipped=12)
```

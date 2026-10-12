# 报价卡片回到销售视角：技术整包与零件表撤出，第 4 步不再复制第 3 步

血缘：`packaging-quote-close-loop.md` §4.3（`packaging_package`「完整包，原样保真，供历史与看板重建」
—— 本 Spec **不动**快照与该键，只撤掉它在**报价卡片**上的展示）、
`packaging-quote-workspace-render-routing.md` §4（结构化展示需求/BOM/工艺/成本 —— 限定为
**工作区渲染引擎**（`PackagingQuotePanel.renderPackage`）本身，不再对销售卡片上屏）、
`packaging-parts-in-card-and-material-fill.md` §2.1（卡片第 6 步显示零件表 —— **由本 Spec C2 取代**；
该 Spec §2.2/§2.3 的件级补材料/补料厚**保留**）、
`packaging-card-step6-parts-must-be-the-business-parts-list.md`（**整份由 C2 取代**）、
`packaging-card-parts-lookup-must-accept-recovered-link.md`（**整份由 C2 取代**）、
`quick-quote-entry-routing-and-packaging-isolation.md` §5/§6（报价侧隔离口径）、
`packaging-quote-close-loop.md` §1.2/§2.4（定价链 `margin_price → +addons → ×(1-discount) → ×(1+tax)`）、
`packaging-parts-downstream-acceptance.md`（技术侧零件/3D 能力与门禁 —— 一概不动）。

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_quote_card_sales_view_parity_red.py`
本批 changelog 条目号：`## 2026-10-12 报价卡片回到销售视角`

## 0. 一句话目标

包装的**报价卡片**（销售视角）要和另外三个行业**看起来一样**：

1. 只有 6 步业务分区，不出现技术整包（需求/BOM/工艺/成本的原始 JSON 树）；
2. 不出现零件/几何/业务部件表；
3. 定价块只在它该在的步骤出现（不常驻）；
4. 第 4 步「报价-加价与折扣」不再是第 3 步「定价-利润加成」的逐字复制。

技术整包的保真、零件能力、3D 与门禁留在**技术工艺侧**（`app.js` 2.1 看板）与工作区引擎里。

## 1. 现场证据（本机读码 + 34 渲染文本，2026-10-12，不是推断）

| 事实 | 出处 |
| --- | --- |
| 「包装：定价与报价分区」折叠块里先是一整棵**技术整包 JSON 树**（`包装需求`/`零件与组成`/`工艺路线`/`成本测算` + `待补项`/`bbox_only`/`needs_input`/`missing_variables`/`length_mm`/`明细 1…`） | `确认需求解析结果.html:4332-4358`（`#packagingPackageContent` + `renderPackage(pkg)`，缓存分支 `:4327`） |
| 那棵树是**通用递归渲染**：没有中文标签的键直接打英文原键，数组项没有 `name/part_name/title/item_key` 时标题退化成 `明细 N`，空数组给 `暂无明细` | `tech_app/frontend/packaging-quote-panel.js:151-192` |
| 第 6 步还有一条**零件/几何表**：按会话反查技术项目 → 读 `.../packaging-business-parts`，退化读 `.../requirement/packaging-parts` | `确认需求解析结果.html:1749`（唯一触发点）+ `:4623`（`ensureCardPackagingParts`）+ `:4605`（`resolveCardTechProject`） |
| 那个 dump 块**没有步骤归属**：host 直接 `insertBefore` 到 `#resultsContent`（`:4332-4337`），没进 `sections` Map；`showStep()` 只隐藏 `sections` 里的分区（`:1756`）→ 切到哪一步都在 | `确认需求解析结果.html:1756` / `:1900-1909` / `:5452` |
| 第 3 步与第 4 步**逐字一样**：`s3_markup` 与 `s4_markup` 共用同一个 `markup_fields`/`markup_rows`，只有 `title` 与 `step_no` 不同 | `cpq_packaging_quote.py:672-695` |
| 第 4 步"没做什么"的直接原因：卡片只传 `pricing_mode` 与 `gross_margin_rate`，从不传 `addons`/`discount` | `确认需求解析结果.html:4364-4365`；引擎侧支持见 `cpq_packaging_quote.py:192-200` / `:306-308` / `:395-402` |
| 三行业的两步是**两套规则**（`rule_category='定价'` / `'报价'`，各自让模型算一次） | `cpq_agent_server.py:2208-2209` / `:2409` / `:2533-2540` |
| 历史对照：`gitlab/master`（`bd9607f`，2026-09-20）两条报价页 `packaging` 命中 0 次，路径含 `packaging` 的文件 0 个（ytbz 496 个） | 本机 `git` 实测 |

## 2. 契约

### C1 技术整包不再上销售卡片

- `确认需求解析结果.html` 必须**没有** `packagingPackageContent` 节点，也**不得**调用
  `PackagingQuotePanel.renderPackage(...)`。
- `packaging_package` **必须继续留在快照**、继续被页面持有（`LAST_PACKAGING_PACKAGE`）：第 6 步门禁
  （`cpq_quote_gate.py:137`）、定价（`/api/packaging-quote/price`）、历史版本重建都读它。
- `PackagingQuotePanel.renderPackage` 作为**引擎 API 保留**（`packaging-quote-panel.js:264-275` 仍导出）。

### C2 卡片不出现零件/几何/业务部件表

- 卡片页必须**没有**：`ensureCardPackagingParts`、`resolveCardTechProject`、`PACKAGING_PART_COLUMNS`、
  `PACKAGING_PARTS_SECTION`、`packagingPartCardRows`、`packagingBusinessPartCardRows`、
  `cardBusinessPartsAccountingText`、`part_columns`、`packaging-business-parts`、`/api/projects`。
- 第 6 步触发点（`:1749`）必须删除。
- 快照里若仍带 `packaging_parts` 段（技术侧/老快照写的），卡片必须**安静跳过**：不渲染、不报错、
  不影响其它段；判据做成具名纯函数 `cardSnapshotSectionSkipped(sectionId, industry)`。
- 技术侧一概不动：`app.js` 的 2.1 零件树、两个零件端点、件级补材料/补料厚、3D、门禁全部保留。

### C3 定价块并入步骤体系

- 「包装：定价与报价分区」块（毛利率输入 + 「按此毛利率重算」+ 四段定价/报价分区上屏）保留，
  但**可见性按大步骤**：只在第 3–5 步可见，第 1/2/6 步隐藏（与三行业一致）。
- 判据做成具名纯函数 `packagingQuotePanelStepVisible(step)`，`showStep()` 必须用它。
- 非包装行业：该块根本不创建（`packaging_package` 缺失即 `return null`），行为逐字不变。

### C4 业务分区与定价入口一个不少

`s2_packaging`（盒型与参数）、`s2_packaging_cost`（成本构成）、`s3_markup`、`s4_markup`、`s5_basic`、
`s5_detail` 六节与毛利率输入、「按此毛利率重算」按钮必须保留；`_BI_SECTIONS` 与后端快照键不动。

### C5 第 4 步不再是第 3 步的逐字复制

`cpq_packaging_quote.sections()` 里 `s4_markup` 必须只讲**加价与折扣**：

- `s3_markup` 语义不变（定价：成本 → 毛利后单价；`kind`/`step_no`/`title` 不动）；
- `s4_markup` 的 `fields` 改为报价侧金额（至少含 `加价合计`、`加价后单价`、`折扣率`、`折后未税单价`），
  不再复用 `markup_fields`；
- `s4_markup` 的 `rows` 至少含一行标明**沿用第 3 步的定价结果**（来源写 `Spec §1.2`）；
- 有 `addons` 时逐条列加价行，没有时给一行 `本版无其他加价`；
- 有折扣时给折扣行（含 `discount_amount`），没有时给一行 `本版无折扣`；
- 判据：同一份报价下 `s4_markup != s3_markup`（不是逐字复制），且键 `s3_markup`/`s4_markup` 都仍在。

### C6 后端、快照与三行业零改动

本批不碰：`cpq_tech_bridge.packaging_snapshot()` / `packaging_handoff` / `cpq_wf` 的快照键、
`cpq_quote_gate` 的第 6 步判据、`cpq_agent_server._BI_SECTIONS`、技术侧路由与知识库、定价引擎的
金额口径（`money_view` / `_numbers` / 守卫）。

## 3. 取代清单

| 旧契约 | 处置 |
| --- | --- |
| `packaging-parts-in-card-and-material-fill.md` §2.1（卡片第 6 步显示零件表） | §2.1 由 C2 取代；§2.2/§2.3 保留 |
| `packaging-card-step6-parts-must-be-the-business-parts-list.md`（整份） | 整份由 C2 取代 |
| `packaging-card-parts-lookup-must-accept-recovered-link.md`（整份） | 整份由 C2 取代（写侧 `recover_quote_link` 与 `packaging_handoff` 的键映射不动） |
| `packaging-quote-workspace-render-routing.md` §4（卡片上完整展示整包） | 卡片部分由 C1 取代；工作区引擎能力保留 |

## 4. 实现范围

### 4.1 `确认需求解析结果.html`

1. 删 `#packagingPackageContent` 节点与全部 `renderPackage(...)` 调用（`:4327` / `:4358`）；保留毛利率输入、
   重算按钮与 `renderCard` 定价调用。
2. 删第 6 步触发点（`:1746-1750`）与整段零件面（`:4446-4705`）。
3. `wfRestoreStepData()` 的未知段分支保留通用渲染，但对 `sid === 'packaging_parts'` 的包装卡片跳过；
   判据提成具名纯函数 `cardSnapshotSectionSkipped(sectionId, industry)`。
4. `showStep()` 补一步：`#packagingQuotePanel` 的显示 = `packagingQuotePanelStepVisible(n)`；
   新建 host 时也要按当前 `shownStep` 先设一次。

### 4.2 `cpq_packaging_quote.py`

`sections()` 里把 `s4_markup` 的 `fields` / `rows` 改成报价侧（C5）；`s3_markup` 与其它三段不动。
禁止改 `money_view` / `_numbers` / `price()` / 文档八节 / 版本表 / 守卫。

## 5. 非目标

- **不做技术项目深链**：卡片页没有现成的技术看板 URL 方案（「转技术工艺」是发交接任务
  `wfOpenSend(false,'tech_new_product')`，不是跳转），做深链要新增技术侧路由，超出本批展示层范围。
  可追溯性由「整包仍在快照里 + `s2_packaging` / `s2_packaging_cost` 两节保留」承担。
- 不改定价公式、费率、税务与折扣口径；不给第 4 步新增输入控件（`addons`/`discount` 的空值如实写成
  「本版无其他加价 / 本版无折扣」）。

## 6. 验收命令与期望

```text
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_quote_card_sales_view_parity_red -v
./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_card_step6_parts_must_be_the_business_parts_list_red \
    tests.test_packaging_parts_in_card_and_material_fill_red \
    tests.test_packaging_card_parts_lookup_link_key_parity_red \
    tests.test_packaging_online_business_closure_red \
    tests.test_packaging_quote_workspace_render_routing_red \
    tests.test_packaging_quote_draft_and_card_visibility_red \
    tests.test_packaging_quote_close_loop_red
```

护栏（不许被改红）：`tests.test_packaging_drawing_board_two_column_parts_and_3d_red`（技术侧 2.1）、
`tests.test_packaging_parts_extraction_red`（技术侧零件提取）、`tests.test_spec_status_truth_red`。

# Spec：2.1 的结果只能是二十多个零件、点一件就直接看样子、右栏的 3D 区块整块撤掉并占满

状态：Spec + 红测（已实现）（原状：① 业务部件清单缺失的项目打开 2.1 时左栏退回几何分量（酒盒实测
263 个连通分量），页面看上去"解析出来 263 个"，而那 263 行的名字全是 `图纸零件 PNN` 占位名；② 业务
部件行点不开——看不到这一件由哪几个几何分量构成（263 个分量没有"归属"入口）；③ 点一件之后形状是
"顺手画一下"，坐标没回来就没有形状、也没有加载态；④ 右栏在没选中零件时仍然摆着 3D 口径的三块
（`#viewer` 3D 画布 300px 空占位、「零件信息」、「查看并编辑零件参数（专家模式）」））
红测：`tests/test_packaging_2_1_result_parts_and_shape_only_red.py`
（2026-09-23 实测：该红测已 `Ran 27 … OK`，本行按状态守卫 `tests.test_spec_status_truth_red` 随事实更正）
血缘：`docs/specs/packaging-28-part-auto-resolution-and-2d-board-cleanup.md`（§4：263 个分量是几何事实、
不是客户说的 28 件业务部件）、`docs/specs/packaging-two-ledgers-reconciliation.md`（本批的前一批：
两笔账同屏对账与措辞）、`docs/specs/packaging-parts-must-be-derived-from-the-drawing.md`（解析只吃 DWG）、
`docs/specs/packaging-business-parts-and-cad-plan-view.md`（§6 右栏 CAD 平面图与业务部件面板）。
本批 changelog 条目号：`## 480`。

## 0. 用户原话（2026-09-23）

> 解析出来的结果必须要是二十多个零件，然后点击之后在右边直接看样子，就像 bom 里面那样，
> 然后那个 3D 的区域整个就不要了 实际展示的内容直接占满右边看板就行了

> 不是一定要 28 个 但是应该是那二十多个 然后每个零件都是正确的名字，点开之后可以是别的更多的两百多个

## 1. 实测证据（HEAD `3902b4a` 工作副本只读 + 隔离真跑）

| 读数 | 实测 |
| --- | --- |
| 酒盒.dwg 隔离真跑（`packaging_drawing_flow.run_flow`） | 几何分量 `263`；业务部件 `28`（bound 26 / unbound 2）—— 业务部件**能**从图纸推出来 |
| 业务部件文档缺失时（老项目 / 只跑过旧链路）`app.js::renderTree()` | 退回几何分量：顶层 `已显示 64 件，共 263 件（k 种形状）` + 折叠诊断区里 263 行 `DWG-Pxx 图纸零件 Pxx` —— 这就是"解析出来 263 个"的来源 |
| `docs/specs/packaging-parts-must-be-derived-from-the-drawing.md` 落地后 | 业务部件由图纸推导并 `save_business_parts()` 落库；**但只有跑过一键解析的项目才有**，页面侧没有"就地补一份"的路 |
| `main.py` 业务部件路由 | 只有 `GET …/packaging-business-parts`、`PUT …/{code}/geometry-binding`、`POST …/import`（要 Excel）、`GET …/{code}/thumbnail` —— **没有**"按图纸补推导"的端点 |
| `app.js::openPackagingBusinessPart()`（`:3171`） | 选中后把形状画进 `#packagingPartOutline`（`packagingBusinessPartOutlineHtml(binding, currentPackagingCadPlan)`，`:3206`），**但**：坐标还没读回来时 `currentPackagingCadPlan` 是 `null` ⇒ 直接落进"绑定状态"文案，之后再也不会重画；没有加载态，也没有 `data-qq-part-shape` 这样的钩子 |
| `app.js::enterDrawingFlowPanes()`（`:1329`） | 只 `viewer.hidden = true`，**不碰** `#partDetail`（`.part-details`）与 `details.parameter-panel`：没选中零件时右栏摆着「零件信息 · 从「零件清单」中选择一项，查看模型…」与「查看并编辑零件参数（专家模式）」 |
| `index.html:220 / :222` | `.part-details`（零件信息）与 `details.parameter-panel`（专家模式）是 3D/视觉链路的口径，包装 2.1 里没有对应产出 |
| `workbench.css:48` | `.view-3d-content{flex:1;min-height:300px;background:#f8fafc;…}` —— 3D 画布即使 `hidden` 也曾经撑起 300px 的空区 |
| `workbench.css:137-139 / :239` | `.drawing-board-split` 两栏（左 `minmax(260px,34%)` / 右 `1fr`）、`.drawing-model-column{overflow:hidden}`、`.model-panes{display:flex;flex:1;…}` |
| 形状来源 | `GET /api/projects/{pid}/requirement/packaging-geometry`（`main.py:7506`）给 `geometry_evidence`（分量坐标）+ `cad_scene`；前端只用后端坐标画图，不做几何求解 |

## 2. 契约

### 2.1 C1：左栏「结果」只能是那二十多件业务部件（件数不锁死、口径锁死）

- 左栏结果行的来源只有一个：**业务部件文档**（`GET …/requirement/packaging-business-parts`）。
  没有这份文档时，先按 §2.4b C6 就地补推导；补不出来才进 `"unavailable"`；
- 结果口径钩子：`renderTree()` 分支上 `data-qq-parts-result="business"`（业务部件存在）/
  `"unavailable"`（补推导也拿不到）；
- **件数不锁死 28**：结果行数就是业务部件文档的行数（酒盒实测 28；圆盘盒一类图为 30~40 量级），
  不许在前端写死数字或二次聚类；判据是"**数量级是二十多件、粒度是采购/加工件**"，不是某个常数；
- 几何分量行仍只许出现在折叠诊断区（`qqGeometryDiagnostics`，`## 373` / `## 451` 的契约不动），
  不作"结果"呈现（措辞按 `packaging-two-ledgers-reconciliation.md` §2.3）。

### 2.1b C1b：结果里每一件都必须是**正确件名**，不许拿几何占位名顶

- 纯函数 `packagingBusinessPartName(row)`：`row.name` 去空白后非空 → 原样返回；
  否则回 `未命名业务部件`（**唯一**兜底），并且**永远不回落**到 `business_part_code`，
  更不许出现几何分量那套占位名（`图纸零件 P%02d`，`packaging_parts.py:1863` 产出的）；
- `renderPackagingBusinessTree()` 的行名必须取自它，且行上带 `data-qq-name-missing="1"`（兜底时）；
- 来源披露照旧（`packagingBusinessPartSourceLabel()"从图纸推导（待人工确认）"`）—— 名字是从图纸
  文字/结构规则推出来的这件事不许被本批抹掉；
- 反向判据：真样本 28 行的名字必须是真件名（左盖面纸 / 内盒1灰板 / 底托 EVA / 贴牌 / 磁铁 / 内卡 …），
  **一个 `图纸零件 P` 都不许出现在结果里**。

### 2.1c C2：点开一件 → 能看到它的构成（那两百多个几何分量按件归属）

- 业务部件行可展开：行上有开关 `data-qq-part-toggle="<code>"`，展开容器
  `data-qq-part-components="<code>"`；
- 展开内容一行一件：纯函数 `packagingBusinessPartComponentsLine(row, components)`：
  - 绑定了分量 → `共 {n} 个几何分量：{DWG-C001 / DWG-C007 / …}`（顺序跟证据层 `component_ids`）；
  - 没绑定 → `这一件还没定位到几何分量`（**不许**留白、不许显示 0 件之外的任何数字）；
  - `row` 是 `null` / 非对象 → `""`；
- 展开**不改**结果口径：展开里的分量不计入结果区那句"业务部件 N 件"的计数；
- 合计口径：展开逐件看到的几何分量与左栏外那笔几何账（`## 477` 的"几何区域 263 个"）
  必须是同一批分量（同 `component_id`），不许前端另算一遍。

### 2.2 C3：点一件 → 右栏**直接**看样子（三态，坐标没到也不许留空）

- 形状区三态钩子 `data-qq-part-shape`：
  - `loading`：文案逐字 `正在读取这一件的形状…`；
  - `ready`：`packagingBusinessPartOutlineHtml()` 产出的 `<svg>`（`role="img"`、
    `data-qq-part-shape="ready"` 挂在形状容器上）；
  - `unavailable`：画不出来时给原因（逐字取自 `PACKAGING_BINDING_COPY[...]` 或
    `PACKAGING_CAD_PLAN_NO_COORDS`），**不许**留白；
- 坐标还没回来的竞态：点行时把件号记进 `pendingPackagingShapePartCode`，`renderPackagingCadPlan()`
  拿到 `geometry_evidence` 后**重画同一件**（清空该变量）；不许"画一次空的就结束"；
- 形状只来自后端坐标池（`GET …/requirement/packaging-geometry`）；前端不算几何；
- 形状容器占满形状区（`preserveAspectRatio="xMidYMid meet"`，容器 flex）。

### 2.3 C4：包装 2.1 的 3D 区块整块撤掉

- 新增 `applyPackagingShapeOnlyPanes()`：把三块 3D 口径内容恒置 `hidden`（**含未选中零件时**）：
  `#viewer`（3D 画布）、`.part-details`（「零件信息」）、`details.parameter-panel`（「查看并编辑零件参数（专家模式）」）；
  并给右栏容器（`.drawing-model-column`）`data-qq-no-3d="1"`；
- 由 `enterDrawingFlowPanes()`（`:1329`）调用 —— 不再只在"选中零件"后才由
  `showPackagingPartPane()` 收起；`showPackagingPartPane()` / `hidePackagingPartPanel()` 保留且语义不变；
- 包装项目下 `#viewer` 不占位（`display:none`；`.view-3d-content` 的 `min-height:300px` 不再参与布局）。

### 2.4 C5：右栏占满

- 右栏容器加 `data-qq-fill="1"`；`workbench.css` 新增规则让形状面板 `flex:1` 且 `min-height:0`；
- 未选中零件时形状区只留一句引导（逐字）：`从左栏选一件零件，这里直接看它的形状。`
  —— 居中、占满，不留 300px 空画布、不留「零件信息 / 专家参数」。

### 2.4b C6：就地补推导（老项目 / 只跑过旧链路的项目）——不许让人重建项目

- 打开 2.1（`refreshPackagingParts()`）时，若**项目已有零件文档**但**没有业务部件文档**，必须尝试
  `ensurePackagingBusinessParts()` → `POST /api/projects/{pid}/requirement/packaging-business-parts/derive`；
  成功即按业务部件列左栏（酒盒 = 二十多件真件名），而不是 263 个占位名的几何分量；
- 后端新增路径常量
  `PACKAGING_BUSINESS_PARTS_DERIVE_PATH = "/api/projects/{pid}/requirement/packaging-business-parts/derive"`；
  端点**只吃 DWG 证据**：复用
  `packaging_business_part_resolver.resolve_business_parts(pid, ir, geometry_parts, None, None)`
  → `packaging_parts.business_parts_document()` → `packaging_parts.save_business_parts()`；
  **幂等**：同一份图纸重复调用不换 `business_parts_id`、不删旧版本；不许读附件 / 知识库 / 金标；
- 推不出来（没有名称证据）→ 返回稳定原因码与下一步，左栏给"为什么没有 + 下一步"，
  **不许**要求重新上传图纸或重建项目。

### 2.5 C7：护栏（本批一个字都不许动）

- 非包装流程的 3D 行为：`renderDrawingEntry()` 判定、`renderIR()`、`#viewer` 节点与 `actionSheet`
  八颗按钮、挤出/覆盖语义；
- `showPackagingPartPane()` / `hidePackagingPartPanel()` 的既有语义；
- `## 373` / `## 451` 的几何诊断折叠区（`qqGeometryDiagnostics`）、分类折叠（`kind_index`）、
  `继续加载`（`has_more`）、既有读接口形状；
- `## 477` 的 `#packagingPartTitle` = 「几何分量」与两笔账对账行；
- BOM / 成本 / 工艺 / 门禁与规则 JSON；不加依赖、不连 PG、前端不做几何求解。

## 3. 允许修改范围

1. `tech_app/frontend/app.js`：`packagingBusinessPartName()` / `packagingBusinessPartComponentsLine()`
   两个纯函数 + 业务部件树的行名与展开渲染；
2. `tech_app/frontend/app.js`：`ensurePackagingBusinessParts()` + `refreshPackagingParts()` 的接线；
   `openPackagingBusinessPart()` 的形状三态与 `pendingPackagingShapePartCode`；
   `renderPackagingCadPlan()` 的重画；`applyPackagingShapeOnlyPanes()` + `enterDrawingFlowPanes()` 调用；
   `renderTree()` 的 `data-qq-parts-result` 与未选中引导句。
3. `tech_app/frontend/index.html`：形状区的三态容器与引导句（结构最小改动）。
4. `tech_app/frontend/workbench.css`：`[data-qq-no-3d]` / `[data-qq-fill]` 两条规则。
5. `tech_app/backend/main.py`：`PACKAGING_BUSINESS_PARTS_DERIVE_PATH` 与 derive 端点。
6. `changelog/changelog_9_21_25.md`：`## 480`。

禁止：改非包装流程的 3D 行为；改既有测试；改几何分量/业务部件的取值与身份；改 BOM / 成本 / 工艺；
新增依赖；把几何分量当结果列出来。

## 4. 未做 / 边界（如实记）

- 本批不做"从业务部件行点开 3D"（3D 在包装 2.1 里整块撤掉，历史挤出结论与 STL 仍留在库里可回查）。
- 本批不做部件图（authority thumbnail）与形状的合并展示；形状优先用 CAD 坐标，
  部件图仍是既有入口（`{code}/thumbnail`）不变。
- derive 端点只补"业务部件文档"这一份产物，不重跑整条链路、不改 anchor / 语义 / 零件文档。
- 未起服务、未连 PG / 34、未写业务数据。

## 5. 红测与反向对照

红测：`tests/test_packaging_2_1_result_parts_and_shape_only_red.py`
（A 组结果口径与补推导 / B 组形状三态 / C 组撤 3D / D 组占满 / E 组护栏）。

- 红基见 §6。
- 反向对照（2026-09-23，本机实测；每条都是"把这一支改回去"，跑完立即还原并 `md5` 核对）：
  ① 把 `enterDrawingFlowPanes()` 里的 `applyPackagingShapeOnlyPanes()` 删掉 ⇒ **D1 单条红**
     （`test_d1_entering_the_lane_applies_the_shape_only_panes`）；
  ② 把 `renderPackagingCadPlan()` 里"坐标后到就重画待画的那一件"整段改回旧写法
     （只重画已选中的那一件）⇒ **C3 单条红**（`test_c3_coordinates_repaint_the_pending_part`）；
  ③ 把 `refreshPackagingParts()` 里的 `ensurePackagingBusinessParts()` 删掉 ⇒ **E1 单条红**
     （`test_e1_refresh_triggers_the_derive`）。
- **本 Spec 原写的三条对照是错的，如实更正**：原文把 ①/②/③ 对应的用例写成 C1 / B2 / A2，
  实测分别落在 D1 / C3 / E1（那三条才是钉这三处的用例：D1 钉 `enterDrawingFlowPanes()` 的调用、
  C3 钉 `renderPackagingCadPlan()` 的重画、E1 钉 `refreshPackagingParts()` 的接线）。
- **另一条如实记**：② 只把那一行代码删掉、**留着同行注释**时**不转红** —— C3 查的是源码里有没有
  `pendingPackagingShapePartCode` 这个名字，注释里带着同一个名字就判不出来。所以对照必须连注释一起
  改回去才算数（本 Spec 的对照按"整段改回旧写法"记）。

### 5.1 测试侧授权：五条「前端业务件路由引用计数」冻结由本批重指（2026-09-23，Codex；断言未放宽）

本批按 C6 新增 `packagingBusinessPartsDerivePath()`（前端**一处**字面量），前端业务件路由引用计数
从 4 变 5。**五条**既有护栏钉着那个 4，必须随事实重指（先例：`## 413` / `## 415` 两批对同一个冻结
做过同样的重指，口径是"**重指不等于放宽**：计数仍精确相等，多出来的那一条逐个点名"）：

| 文件 | 用例 | 重指后 |
| --- | --- | --- |
| `tests/test_packaging_authority_workbook_upload_red.py` | `DWiring::test_d3_route_reference_count_is_frozen` | 4 → 5，注释点名 derive 那一条 |
| `tests/test_packaging_business_part_basis_in_panel_red.py` | `CTaskResultsCarryTheBasis::test_c6_frontend_route_count_unchanged` | 4 → 5 |
| `tests/test_packaging_business_part_cost_by_authority_size_red.py` | `EGuardrails::test_e4_no_frontend_change` | 4 → 5 |
| `tests/test_packaging_business_part_process_by_authority_route_red.py` | `FGuardrails::test_f4_no_frontend_change` | 4 → 5 |
| `tests/test_packaging_business_part_process_entry_red.py` | `CGuardrails::test_c4_the_412_freeze_is_repointed_not_loosened` | 4 → 5 |

**只改这一个数字与它旁边的注释**：每条仍是 `assertEqual`（精确相等），仍要求"多出来的必须点名"，
没有一条改成范围 / 去掉计数 / 换成 `assertLess`。`test_c4` 另外两条断言（重指必须写在红测里、
计数必须落在 E4 里）逐字未动。

## 6. 红基（2026-09-23，Spec + 红测，未实现）

```text
./open-claude/.venv/bin/python -m unittest tests.test_packaging_2_1_result_parts_and_shape_only_red
Ran 27 tests … FAILED (failures=18)
```

- 红 18 条：A1（结果口径钩子）、A2–A4（没有 `packagingBusinessPartName()`，行名没有兜底钩子）、
  B1/B2/B4（业务部件行点不开、没有构成行）、C1–C3（形状没有三态、没有坐标竞态重画）、
  D1–D4（没有 `applyPackagingShapeOnlyPanes()`、3D 三块没撤、没有 CSS 规则与引导句）、
  E1–E4（前端不补推导、后端没有 derive 端点）；
- 绿 9 条是护栏：A5（几何账仍只进折叠区）、A6（来源披露）、B3（结果计数仍按业务部件行数）、
  C4（形状仍来自后端坐标池）、D5（非包装 3D 与既有选件函数）、E5（补推导不删既有产物、导入端点仍在）、
  F1–F3（几何账/翻页/左栏节点/两笔账措辞）。

## 7. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 480`）

| 契约 | 落点 | 复跑结果 |
| --- | --- | --- |
| C1 结果只能是那二十多件 | `app.js::renderTree()` 的图纸分支对 `#tree` 打 `data-qq-parts-result="business"｜"unavailable"`；结果行只有一个来源（业务部件文档） | A1 绿；反向对照 ③ 的 E1 绿 |
| C1b 件名 | 纯函数 `packagingBusinessPartName(row)`（去空白；缺失只回唯一兜底词，**不**回落成编码）；行上 `data-qq-name-missing="1"` | A2–A4 绿；A5（结果里没有 `图纸零件 P`）绿 |
| C2 点开一件看构成 | 行上 `data-qq-part-toggle="<code>"` + 展开容器 `data-qq-part-components="<code>"`；纯函数 `packagingBusinessPartComponentsLine(row, components)` 只读证据层 `component_ids` | B1/B2/B4 绿；B3（计数仍按业务部件行数）绿 |
| C3 点一件直接看样子（三态） | `openPackagingBusinessPart()`：`data-qq-part-shape` ∈ `ready`（绑定量画出 `<svg role="img">`）/ `loading`（坐标没到，文案逐字 `正在读取这一件的形状…`）/ `unavailable`（`PACKAGING_BINDING_COPY[…]` 或 `PACKAGING_CAD_PLAN_NO_COORDS`） | C1/C2 绿；反向对照 ②（C3 单条红） |
| C3 坐标竞态 | `pendingPackagingShapePartCode` + `repaintSelectedPackagingShape()`：`renderPackagingCadPlan()` / 渲染场景拿到几何后重画**同一件**并清标记 | C3 绿；C4（形状仍来自后端坐标池）绿 |
| C4 撤 3D 区块 | 新增 `applyPackagingShapeOnlyPanes()`（`#viewer` / `.part-details` / `.parameter-panel` 恒 hidden + `.drawing-model-column` 打 `data-qq-no-3d`），由 `enterDrawingFlowPanes()` 调用；`workbench.css` 新增 `[data-qq-no-3d]` 规则让 3D 不占位 | D1/D2/D3 绿；反向对照 ①（D1 单条红）；D5（非包装 3D 与既有选件函数）绿 |
| C5 右栏占满 | `.drawing-model-column` 打 `data-qq-fill`；`workbench.css` 让形状面板 `flex:1;min-height:0`；未选中零件时 `#packagingShapeIdle` 只留一句 `从左栏选一件零件，这里直接看它的形状。`（选中一件或切回视觉链路即收掉） | D2/D3/D4 绿 |
| C6 就地补推导 | 前端 `ensurePackagingBusinessParts()`（`refreshPackagingParts()` 里"有零件、没有业务部件文档"才跑）→ `POST …/packaging-business-parts/derive`；后端 `PACKAGING_BUSINESS_PARTS_DERIVE_PATH` + 端点只吃 DWG 证据，复用 `resolve_business_parts()` → `business_parts_document()` → `save_business_parts()`，缺零件 / 没有名称证据各给稳定原因码与下一步 | E1–E4 绿；E5（不删既有产物、导入端点仍在）绿；实测路由已登记（`app.routes` 269 条） |
| C7 护栏 | 非包装 3D、`showPackagingPartPane()` 语义、几何诊断折叠区 / 分页 / `## 477` 的两笔账措辞、BOM / 成本 / 工艺与规则 JSON 全部未动 | D5 / F1–F3 绿 |

- 红基原文保留在 §6（`Ran 27 … FAILED (failures=18)`）；实现后 `Ran 27 … OK`。
- 保护网：含 `app.js` 的 91 个模块 `Ran 1485 tests … OK (skipped=4)`；
  `tests/test_packaging_*.py` 166 个模块里除下一批（`## 481`）的红测外全绿；
  `node --check tech_app/frontend/app.js` 通过。
- 如实记一处**脆弱**：`tests/test_packaging_parts_downstream_red.FFrontend::test_f3_reuses_inline_analysis`
  用「第一个 `packagingPartProcess` 之后 4000 字符」的窗口找 `CadInlineAnalysis`，本批为它把新增的
  `applyPackagingShapeOnlyPanes()` 挪到该窗口之外（实测窗口内距离 3819，余量 181 字符）。这条护栏
  对**任何**这一带的新增都极敏感，下一批若再动这里会先红 —— 先动它的人应当按"重指"处理，不要放宽。
- 未起服务、未连 PG / 34、未写业务数据、未部署；本轮只本地改动（未 push / 未 MR / 未 tag）。

# Spec：2.1 右栏只画「这一件」的图（一块图 / 整块一起滚 / 按图层着色 / 可拖拽缩放）

状态：Spec + 红测（已实现）（2026-09-23 由本批落地：`app.js` 新增纯函数 `packagingPartSceneEntities` / `packagingPartSceneSvg`，`openPackagingBusinessPart()` 把单件图挪进 `#packagingCadPlanViewer`，`renderPackagingCadScene()` / `renderPackagingCadPlan()` 末尾按选中件重画，`packaging_parts.geometry_evidence_of()` 透传 `annotation_filtered`，`drawing-flow.css` 撤掉两块自持滚动与图高 `100%`；本机实测 `Ran 31 … OK`，本批 changelog 条目 `## 487`）—— 把「选中的这一件」
画成右栏**唯一**一块图（件内几何按图层着色），撤掉下栏那块只画外环的图，三套滚动收归一处；
落地表与实测见 §6，反向对照见 §6.1；根因与实测见 §1）
红测：`tests/test_packaging_2_1_right_pane_single_part_figure_red.py`
血缘：`docs/specs/packaging-business-part-plan-click-and-bound-outline.md`（§C2 被本 Spec 取代）、
`docs/specs/packaging-business-parts-and-cad-plan-view.md`（§6 整图与点选口径）、
`docs/specs/packaging-2-1-parts-row-layout-and-shape-viewport.md`（形状视口与拖拽缩放）、
`docs/specs/packaging-dimension-annotation-must-not-enter-part-shape.md`（标注实体不进件形状）、
`docs/specs/packaging-2-1-result-parts-and-shape-only-pane.md`（右栏只留该看的东西）。
本批 changelog 条目号：`## 487`（落地时序号，2026-09-23 Codex 实现）。

## 0. 用户原话（2026-09-23）

> 现在右侧分了上下两栏滑动，我不是这个意思，我意思是完整的滑动，就像现在下面那栏里面那样直接滑动，
> 也就是说现在上下栏里面的所有内容一起滑动，但我没理解下栏里面的图是什么，是只有最外面一圈的零件吗，
> 我不需要这个东西，我只需要上面那个图，但是上面那个图不是让定位原图然后放大，是要只有这一个东西的图，
> 现在旁边还有别的零件这不对，而且是这个图可以拖拽缩放，而且这个图里面应该也有颜色区分，
> 就像dwg看图的里面那样

## 1. 实测证据（HEAD `89f0bc0` 工作副本只读）

| 读数 | 实测 |
| --- | --- |
| 右栏有几个滚动条 | **三个**：`workbench.css:325` `.drawing-model-column[data-qq-fill]{overflow-y:auto}`、`drawing-flow.css:235-244` `.packaging-cad-plan{height:100%;…;overflow:auto}`、`drawing-flow.css:39-46` `.packaging-part-panel{…;overflow:auto}` ⇒ 上下两块各自能滚（用户的"分了上下两栏滑动"） |
| 上面那个图是什么 | `#packagingCadPlanViewer` 里的**整张** CAD 平面图：`renderPackagingCadScene()`（`app.js:2180-2220`）把 `cad_scene.entities` **全部**画出来；选中一件后 `highlightPackagingBusinessPart()`（`app.js:2038-2058`）把其余图元打 `is-dimmed`、再 `fitPackagingCadPlan(range)` 缩放到这一件的范围 —— 就是用户说的"定位原图然后放大 / 旁边还有别的零件" |
| 下面那个图是什么 | `#packagingPartOutline` 里 `packagingBusinessPartOutlineHtml(binding, doc)`（`app.js:1992-2018`）画的**绑定分量的轮廓**：闭合件优先画 `outline_points` 那一圈（`packagingCadPlanComponentSvg()`，`app.js:2112-2135`），没有环才退到件内折线/包络框 ⇒ 用户猜的"是只有最外面一圈的零件吗"——**对**；面板自己那句 `PACKAGING_BOUND_OUTLINE_NOTE` 也写着"这是绑定分量的形状" |
| 上面那块有没有颜色 | 有：`packagingCadSceneEntitySvg()` 按 `PACKAGING_CAD_LAYER_COLORS[role]`（`app.js:1914-1918`，cut/crease/print/frame… 九色）着色 |
| 下面那块有没有颜色 | 一件里若只有一个 role，就一个颜色 —— 它画的是"分量"，不是"图元"，没有逐图元的图层区分 |
| 「这一件的图元」拿得到吗 | 拿得到：`business_parts[].geometry_binding`（`entity_ids` + `component_ids`）→ `geometry_evidence.components[].entity_ids`（`packaging_parts.geometry_evidence_of()`，`packaging_parts.py:3752-3753`）→ `cad_scene.entities[].cad_entity_id`（`main.py:7316`）。**但** `entity_ids` 是**全量成员**（含标注，`packaging_parts.py:2097`），标注只被"形状"剔除 ⇒ 直接拿 `entity_ids` 画会把上一批刚拿掉的尺寸线又画回来，必须先减掉标注实体 |
| 标注实体前端拿得到吗 | 现在**拿不到**：`geometry_evidence.components[]` 不带 `annotation_filtered`（只有 `packaging-parts` 文档的行带，`packaging_parts.py:1986`）⇒ 本批要给证据层补这一个键（红测 A1） |

## 2. 目标行为

**本批的选择（写在前面，避免两种解读）**：保留**上面那块**（`#packagingCadPlanViewer`）作为右栏
**唯一**的图形区，选中一件后它从"整张图"切成"只这一件"；下面那块（`#packagingPartOutline`）
**不再出图**，只留文字披露。理由：用户要的是"上面那个图 + 只有这一个东西"，
而"整图 + 单件图"并置于上下两块正是他要撤掉的现状。

### C1 一块图

- **未选中任何一件**：那块仍是整张 CAD 平面图（点图选件的入口不变，Spec
  `packaging-business-part-plan-click-and-bound-outline.md` §C1 的点选分流一字不动）。
- **选中一件业务部件**：那块**只画这一件**的图元；别的零件的图元一条都不画（不再靠 `is-dimmed`
  淡化，也不再并排出现第二块图）。
- 「一块」是可数的：整个右栏同时出现的图形区（`svg`）**至多一个**。

### C2 「这一件的图元」怎么算（纯函数，不用浏览器端几何求解）

`packagingPartSceneEntities(binding, doc)`：

1. `ids` = `binding.entity_ids` ∪（`doc.geometry_evidence.components` 里
   `component_id ∈ binding.component_ids` 的每一件的 `entity_ids`），去重；
2. 减去这一件的**标注实体**：`component.annotation_filtered[].entity_id`（本批给证据层补上，
   与 `packaging-parts` 文档同名同形）；
3. 取 `doc.cad_scene.entities` 里 `cad_entity_id ∈ ids` 的图元，**顺序 = 场景顺序**（稳定），
   同一 `cad_entity_id` 只回一条；
4. `ids` 为空、或一条都不命中 → `[]`（不猜、不回退去画整图）。

一个业务部件绑多个分量（真样本上并列歧义件常绑几十个）时，图元集是这些分量的**并集** ——
仍然是"这一件"。

### C3 图里是件内真实几何，不是只有一圈

`packagingPartSceneSvg(binding, doc)`：命中图元逐条复用 `packagingCadSceneEntitySvg()`
（线/折线/多边形/文字，坐标翻 y 的既有口径不动），viewBox 由命中图元的 `bbox` 并集经
`packagingCadPlanRange()` + `packagingCadPlanViewBox()` 产出；命中为空或一条都没有坐标 → `""`
（由调用方给状态文案，不留白、不画空图）。

判据：一件里 1 圈 + 3 条内线 ⇒ 输出里有 **≥ 4** 个图元，且 `<polygon` 与 `<polyline` **同时**出现。

### C4 颜色按图层/角色区分（像 DWG 看图）

`stroke` 一律取 `PACKAGING_CAD_LAYER_COLORS[role]`（与整图**同一张表**，同一 role 两处同色）；
role 缺失 → `PACKAGING_CAD_LAYER_COLORS.unknown`，不凭空造色。
判据：一件跨 2 个 role ⇒ 输出里出现 **≥ 2 种**不同 `stroke`。

### C5 这块图能拖拽缩放，且不吃满整栏

- 复用 `bindPackagingPartShapeInteractions()` 与 `PACKAGING_PART_SHAPE_VIEWPORT_CLASS`
  （`data-qq-shape-viewport` / `data-qq-shape-zoom-label` / `packagingPartReset` 口径不变）：
  pointer 拖拽平移、`ctrl` + 滚轮缩放、裸滚轮留给整栏滚动；
- 图的高度是**上限**（视口 `overflow:hidden`），不 `height:100%` 吃满整栏 —— 否则用户看到的
  仍是"上下两栏"。

### C6 整块一起滚

右栏只剩**一个**滚动条：滚动权继续归 `.drawing-model-column[data-qq-fill]` 一处；
`.packaging-cad-plan` 不再 `height:100%` / `overflow:auto`，`.packaging-part-panel` 不再
`overflow:auto`（两块内容一起滚，像用户说的"就像现在下面那栏里面那样直接滑动"）。

### C7 未绑定 / 画不出来时给原因，不给空图；三态与竞态口径保留

`geometry_binding.status` 不是已绑定（或分量在图上一件都命不中）→ 那块保持既有
`PACKAGING_BINDING_COPY[status]` 状态文案；**不回退**去画整图、不画空 svg。

三态勾子与首点竞态跟着这块图走、口径一个字不改（`docs/specs/packaging-2-1-result-parts-and-shape-only-pane.md`
§2.2）：`data-qq-part-shape` 打在承载这块图的宿主上，加载态文案逐字
`正在读取这一件的形状…`，坐标没到时记 `pendingPackagingShapePartCode`、坐标到了重画同一件。

### C8 竞态与刷新口径

`pendingPackagingShapePartCode` / `currentPackagingBusinessPartCode` 的既有口径沿用：
CAD 场景后到时（首点竞态）、或场景被重新拉取时，那块图仍是**当前选中的这一件**，
不许被重画成整图。

## 3. 与既有 Spec 的关系（被本批改写的契约）

- `packaging-business-part-plan-click-and-bound-outline.md` §C2（"业务部件面板画绑定分量的形状"）
  **被本 Spec 取代**：图从 `#packagingPartOutline`（下栏、只有外环）挪到那一块唯一的图（上栏、
  这一件的全部图元）。该 Spec 的 §C1/§C3（点选分流、冻结面）不变。
  → 既有红测 `tests/test_packaging_business_part_plan_click_and_bound_outline_red.py` 的
  B3/C3/C4/C5 需按本 Spec §5.1 重指（断言不放宽：`packagingBusinessPartOutlineHtml` 仍是
  node 可跑的纯函数，只是不再是"面板那块图"的唯一来源；`PACKAGING_BOUND_OUTLINE_NOTE`
  这句文字口径保留）。
- `packaging-2-1-parts-row-layout-and-shape-viewport.md`（视口三态与拖拽缩放）**沿用**：
  视口与交互函数一个字不改，只是承载这块单件图。
- `packaging-business-parts-and-cad-plan-view.md`（整图、点选、高亮）**未选中态不变**；
  选中态不再"整图 + 高亮定位"。

## 4. 本批明确不做（边界）

1. **几何分量通道（263 件诊断层）右栏口径不动**：本批只改业务部件（2.1 结果那二十多件）的选中态；
   `selectPackagingPart()` / 单件详情接口 / `#packagingPartOutline` 在几何件下的行为一字不改。
2. 未选中态的整张平面图保留（它是点图选件的入口）；若连它也不要，需另开一批并说明替代入口。
3. 不给单件图加旋转 / 测量 / 标注 / 导出；不做"画到哪一件就自动切换"的联动；
   不管图层显隐开关（那是整张图的视图开关；单件图只画这一件）。
4. 不改 `packaging-parts` 文档结构、不改 CAD IR、不改成本/工艺任何口径；
   不在浏览器端做几何求解、不新开接口（只给 `geometry_evidence.components[]` 透传一个已有键）。

## 5. 验收

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_2_1_right_pane_single_part_figure_red -v
#   实现前：A1 + B/C/D/E 组红（函数不存在 / CSS 仍是三套滚动）；F 组护栏绿
#   实现后：全绿

# 不回归（点名）
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_2_1_parts_row_layout_and_shape_viewport_red
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_parts_and_cad_plan_view_red
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_parts_panel_red
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_dimension_annotation_not_in_part_shape_red
```

### 5.1 与既有红测的冲突怎么处理

`tests/test_packaging_business_part_plan_click_and_bound_outline_red.py` 的 B3/C3/C4/C5 断言的是
"面板那块画绑定分量轮廓"——本 Spec 把它取代了。实现落地时**只允许改这几条断言的对象**（改成断言
"单件图画的是这一件的全部图元、颜色 ≥ 2 种、不是一圈"），**不许放宽**（不许删掉颜色/圈数/实体条数
这类判据，也不许把 `assertIn` 换成 `assertTrue(True)`）。

## 6. 落地状态（2026-09-23，Codex 实现；本批 changelog 条目 `## 487`）

只动前端（`app.js` / `drawing-flow.css`）与后端证据层**透传一个已有的键**；接口、数据结构、算法、
成本/工艺口径一个字未动。

| 契约 | 落点 | 实测 |
| --- | --- | --- |
| C1 一块图 | `openPackagingBusinessPart()` 的图从下栏 `#packagingPartOutline` 挪到上面那一块 `#packagingCadPlanViewer`：`figureHost.innerHTML` = `packagingPartSceneSvg(binding, currentPackagingCadPlan)` 包进 `.packaging-part-shape-viewport`；下栏只留一句 `PACKAGING_BOUND_OUTLINE_NOTE`（**不再出图**，也不再出现 `packaging-part-svg`） | D1 / D2 全绿 |
| C2 只画这一件 + 减掉标注 | `app.js` 新增纯函数 `packagingPartSceneEntities(binding, doc)`：按 `component_ids` 从 `geometry_evidence.components[].entity_ids` 收这一件的成员（也接受 `binding.entity_ids`），再**减掉**同一分量 `annotation_filtered` 里的实体，最后**按场景顺序**去重输出；`packagingPartSceneSvg(binding, doc, options)` 画这一件（`viewBox` 由件内坐标产出；`<polygon>` 闭合环、`<polyline>` 件内折线、`<text>` 文字） | A1–A3 / B1–B6 / C1–C7 全绿 |
| C2 证据层带标注键 | `packaging_parts.geometry_evidence_of()` 每个分量新增 `annotation_filtered`（键名与形状跟 `packaging-parts` 文档逐字一致，升序、逐条 `entity_id` + `reason`；没有标注给 `[]`）；**`entity_ids` 一个成员不许少** | A1 / A2 / A3 全绿 |
| C4 按图层着色 | `stroke` 取 `PACKAGING_CAD_LAYER_COLORS[role]`（`options.colours` 可注入，node 单函数抽跑走同值兜底），跨 role ⇒ ≥ 2 色 | C3 全绿 |
| C5 可拖拽缩放、不吃满整栏 | 复用 `bindPackagingPartShapeInteractions()` 与 `data-qq-shape-viewport` / `data-qq-shape-zoom-label` / `#packagingPartReset`（口径一字不改）；`drawing-flow.css` 里 `.packaging-cad-plan` 与 `.packaging-cad-plan-svg` 都删掉 `height:100%`（图高度改成**上限**），`.packaging-part-shape-viewport` 仍 `overflow:hidden` | D3 / E1 / E5 全绿 |
| C6 整块一起滚 | `.packaging-cad-plan` 删 `overflow:auto`、`.packaging-part-panel` 删 `overflow:auto`；滚动权仍归 `workbench.css` 的 `.drawing-model-column[data-qq-fill]{overflow-y:auto}` **一处** | E2 / E3 / E4 全绿 |
| C7 画不出来给原因 | 三态 `data-qq-part-shape` = `ready` / `loading`（逐字 `正在读取这一件的形状…` + 记 `pendingPackagingShapePartCode`）/ `unavailable`（`PACKAGING_BINDING_COPY[status]`，没有就 `PACKAGING_CAD_PLAN_NO_COORDS`）—— 不回退整图、不画空 svg | D4 / D6 全绿 |
| C8 竞态与刷新 | `renderPackagingCadScene()` 与 `renderPackagingCadPlan()` 末尾都调 `repaintSelectedPackagingShape(pendingPackagingShapePartCode || currentPackagingBusinessPartCode)`：场景后到或重拉后那块图仍是**当前选中的这一件** | D5 全绿 |

复跑口径（本机 `./open-claude/.venv/bin/python -W ignore -m unittest`）：

- 本批红测 `tests.test_packaging_2_1_right_pane_single_part_figure_red` → `Ran 31 tests … OK`；
  红基（只把 `app.js` / `drawing-flow.css` / `packaging_parts.py` 退回 HEAD）`Ran 31 … FAILED (failures=20)`：
  A1、A3、B1–B6、C1–C7、D1、D2、E1–E3（11 条绿护栏：A2、D3–D6、E4、E5、F1–F4）。
- 本批两条新红测一起跑 → `Ran 51 … OK`；红基 `Ran 51 … FAILED (failures=34)`。
- 点名保护网（本 Spec §5 四条 + 另一条新红测 + 既有 `test_packaging_2_1_result_parts_and_shape_only_red` /
  `test_packaging_2_1_first_paint_is_2d_not_3d_red` / `test_packaging_business_part_plan_click_and_bound_outline_red` /
  `test_packaging_parts_panel_red`）→ `Ran 195 tests … OK`。
- 全量（396 个模块）→ `Ran 6631 tests … FAILED (failures=2, skipped=28)`：两条失败都是既有
  `test_cpq_eval_ci_contract` 的环境/待裁决项（与基线 `Ran 6580` 的 2 条同一条），本批**零新增失败**。
- `node --check tech_app/frontend/app.js` 通过；`git diff --check` 干净。

### 6.1 反向对照（删掉一处目标改动 ⇒ 只让对应那几条红；跑完还原 + `md5` 核对）

| 反向改动 | 实测 | 还原核对 |
| --- | --- | --- |
| 关掉「标注减除」（`packagingPartSceneEntities()` 与 `packagingPartSceneSvg()` 兜底两处的 `|| annotations[id]` 同时去掉） | `Ran 31 … FAILED (failures=4)`：B1 / B2 / B3 + C1（画出来 5 条，把上一批刚摘掉的尺寸线又画回来了） | `app.js` `md5 0b0b2988b57d7ed10c69b2d7f2177593` 一致 |
| 关掉「这一件的成员扩展」（不再按绑定分量的 `entity_ids` 收成员，只认 `binding.entity_ids`） | `Ran 31 … FAILED (failures=6)`：B1 / B3 + C1 / C2 / C3 / C5（只剩显式把实体写进 `binding.entity_ids` 的那条能画） | 同上一致 |
| `.packaging-cad-plan` 加回 `height:100%` + `overflow:auto` | `Ran 31 … FAILED (failures=2)`：E1 / E2 | `drawing-flow.css` `md5 9acf7829165792b76536a1578b660750` 一致 |

后端 `packaging_parts.py` 的 `md5 4538ac278e7073895f5d665febb07cd4` 在三处反向对照前后一致
（本批后端只加一个透传键）。

## 7. 已知缺口（不属本批）

1. **真样本上这块图目前是单色的**（契约成立、颜色不成立）—— 实测与根因见 §7.1，
   要落地得补"层名 → 角色"的业务规则，**不由本批自行拍数**；
2. 单件图只按既有图层角色着色，不做"看图增强"（颜色合并 / 过滤 / 图层显隐开关）；
3. 任务文件里点图纸那张整图不做拖拽缩放（Spec `packaging-task-file-dwg-opens-the-whole-plan.md` §3 边界 1）；
4. 未选中态仍是整张平面图（点图选件的入口）；若连它也不要，需另开一批并说明替代入口。

### 7.1 真样本实测：这块图目前只会用到色表里 `unknown` 那一格（2026-09-23，只读）

口径：本机 `cad_ir.parse_dxf()` + `packaging_parts.extract()`，离线复刻 `main.py::_packaging_cad_scene()`
的场景（角色走同一条 `_resolve_layer_roles()`），再把每件绑定喂给 `node` 真跑的
`packagingPartSceneEntities()` / `packagingPartSceneSvg()`。

| 样本 | 业务件（其中绑定） | 逐件核对「只这一件 / 减掉标注 / 条数一致」 | 画出 ≥2 色的件 | 每题图元数 |
| --- | --- | --- | --- | --- |
| 酒盒.dwg | 28（26） | **0 件不符** | **0** | 3 / 4 / 6 / 7 / 10 / 12 / 17 / 18 |
| 圆盘盒.dwg | 65（38） | **0 件不符** | **0** | 2 / 3 / 4 / 7 / 8 / 10 / 16 / 28 |

- **契约成立**：26 + 38 件绑定业务部件，画出来的实体集合逐件等于"这一件绑的分量的成员减掉
  `annotation_filtered`"，画出的条数与实体数一致，别件图元一条没进（C1 / C2 / C7 在真数据上同样成立）。
- **颜色不成立的原因在上一层**：`tech_app/agent_knowledge/rules/packaging_layer_rules.json` 只认
  `全穿刀` / `压线` / `CREASE` / `CUTTER` 这类层名，而这两张真图用的是 `0` / `DESIGN` / `SAMPLE` /
  `轮廓线` / `1轮廓实线层` / `2细线层` / `6文字层` / `7标注层` 等 —— 一律落 `unknown`：
  - 酒盒：场景 6696 图元，角色 `{unknown: 6388, cut: 308}`；**263 个几何分量里含已知角色的 = 0**；
  - 圆盘盒：场景 7065 图元，角色 `{unknown: 7004, cut: 49, crease: 12}`；312 个分量里含已知角色的
    只有 9 个且**全是单一 `cut`**，跨 ≥2 个已知角色的分量 = **0**。
- 再往下量一层（**这才是要业务拍的地方**）：按"这一件用了几层"看，绑定件里
  | 样本 | 绑定件 | 只用**一层**（补规则也分不出色） | 跨 **≥2 层**（补规则后才可能分色） | 层组合 |
  | --- | --- | --- | --- | --- |
  | 酒盒.dwg | 26 | **16** | 10 | `('0',)`9 / `('0','DESIGN')`8 / `('DESIGN',)`7 / `('DESIGN','SAMPLE')`2 |
  | 圆盘盒.dwg | 38 | **38** | **0** | `('0',)`33 / `('_U+56FE_U+5C42 1',)`4 / `('全穿刀',)`1 |

  也就是说：即使把真图的层名全部补成角色，**两部分合计 64 件里也只有 10 件**（全在酒盒）能靠"图层"分出颜色；
  圆盘盒 38 件**一件都不行**（每件都画在一层上）。
- 结论（两条，都要业务口径，本批不自作主张）：① 层名 → 角色（哪种层是刀线 / 压痕 / 图框）要补规则；
  ② 只会一层的件若也要分色，只能按**图元自身**分（闭合轮廓 / 件内折线 / 标注），那是另一条产品口径 ——
  两条补齐之前，这块图在真图上就是单色。C4 的取色判据已经就是 `PACKAGING_CAD_LAYER_COLORS[role]`，
  规则定了以后代码不用再改。

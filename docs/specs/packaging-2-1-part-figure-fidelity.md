# Spec：2.1 看图那块（件图与预览图）—— 可缩放拖拽、分色、高度翻倍、画的是「图纸上这一件的样子」

状态：Spec + 红测（已实现）（2026-09-24 本批落地：预览接同一套缩放视口 + 图层分色 + 高度翻倍 +
件图补画这一件的标注；本机实测 `Ran 18 … OK`（红基 12 红 / 6 绿护栏），本批 changelog 条目 `## 492`）
红测：`tests/test_packaging_part_figure_fidelity_red.py`
血缘：`docs/specs/packaging-2-1-parts-row-layout-and-shape-viewport.md`（§2.2/§2.3 的缩放视口与 `bindPackagingPartShapeInteractions()`）、
`docs/specs/packaging-2-1-right-pane-single-part-figure.md`（右栏只画这一件）、
`docs/specs/packaging-dimension-annotation-must-not-enter-part-shape.md`（形状口径把标注摘掉）、
`docs/specs/packaging-task-file-dwg-opens-the-whole-plan.md`（预览画的是整张平面图）
本批 changelog 条目号：`## 492`（落地条目同为 492）。

## 0. 用户原话（2026-09-24）

> 任务文件里的预览 dwg 和 3D 视图的看零件能不能做成可以缩放和拖拽的，能不能做成有颜色区分的？
> 然后另外，现在的 3D 视图里看零件的地方太矮了，应该高度是现在两倍，另外想要的是之前这里的东西
> 而不是之前下面的只有外轮廓的东西，想要的是图纸上整个零件的样子而且是这个零件的全部在图纸上的
> 样子还原出来而且还没有别的零件

## 1. 现状实测（工作副本只读）

| 读数 | 实测 |
| --- | --- |
| 2.1 件图的缩放拖拽 | **已经有**：`bindPackagingPartShapeInteractions()`（`app.js`）给 `.packaging-part-shape-viewport` 绑了 `pointerdown/move/up`（拖拽平移）、`wheel`（**Ctrl/⌘ + 滚轮**才缩放，裸滚轮留给整栏滚动，`packaging-2-1-parts-row-layout-and-shape-viewport` §2.3）、`#packagingPartReset`（适应窗口），纯函数在 `packagingPartShapeNextState()`（0.2–8 倍封顶） |
| 任务文件预览里的 DWG 图 | **是死图**：`openFilePreview()` 的 drawing 分支只把 `fileDrawingPreviewHtml(payload)` 的字符串塞进 `figure.innerHTML` —— 没有视口、没有绑事件（`bindPackagingPartShapeInteractions` 只被 2.1 的件图调用） |
| 颜色 | 三处渲染都按 `PACKAGING_CAD_LAYER_COLORS[row.role]`，认不出角色的落 `unknown` 的同一个灰。真图（酒盒）`role` 分布实测 `{unknown: 6388, cut: 308}` ⇒ 满屏一个灰，看不出图层 |
| 高度 | `.packaging-part-shape-viewport{height: min(46vh, 420px)}`；未选中态那张整图 `.packaging-cad-plan-svg{min-height: 220px}` |
| 件图画了什么 | `packagingPartSceneSvg(binding, doc)` 用 `packagingPartSceneEntities()` —— 它把这一件的**标注**（`annotation_filtered`）摘掉（`## 485` 的形状口径）。于是件图只剩形状骨架，看着就是"只有轮廓" |

## 2. 契约

### C1 缩放与拖拽：预览也要有，且只有一套实现

任务文件预览里的 DWG 图换成**与 2.1 件图同一个视口**：`packagingShapeViewportMarkup(innerHtml)`
产出 `.packaging-part-shape-viewport` + 百分比标签 + 「适应窗口」按钮（类名与复位 id 都取
`PACKAGING_PART_SHAPE_VIEWPORT_CLASS` / `PACKAGING_PART_SHAPE_RESET_ID` 两个既有常量），
随后调 `bindPackagingPartShapeInteractions()`。不在预览里新写第二套缩放 / 拖拽。

### C2 颜色区分：认得到角色用角色色，认不出的按图层名分色

新增纯函数 `packagingCadLayerColour(layer, role)`：`role` 是非 `unknown` 的已知角色 ⇒ 回角色色
（既有口径逐字不变）；否则按**图层名**取稳定色（`PACKAGING_CAD_LAYER_PALETTE`，djb2 之后取模）。
三处渲染（整图 `packagingCadSceneEntitySvg()`、件图兜底 `sceneEntitySvg()`、预览 `fileDrawingPreviewHtml()`）
全部改走它 —— 真图上"有哪几层"从此看得出来，且同一层在任何一处都是同一个颜色。

### C3 高度翻倍

- `.packaging-part-shape-viewport`：`min(46vh, 420px)` → `min(92vh, 840px)`；
- 未选中态那张整图 `.packaging-cad-plan-svg`：`min-height 220px` → `440px`；
- 预览里的视口另给 `min(60vh, 460px)`（预览是小窗，不跟着长到 92vh）。

### C4 画的是「图纸上这一件的样子」——不是只有轮廓，但也只有这一件

新增纯函数 `packagingPartAnnotationEntities(binding, doc)`：只取**这一件**绑定的分量里被判成标注
（`annotation_filtered`）的图元；`packagingPartSceneSvg(binding, doc, {includeAnnotations:true})`
把它补画回去并标 `is-annotation`（CSS：虚线 + 淡），viewBox 一并算进这些图元。
2.1 件图的调用点带上这个选项；**形状口径一个字不改**：`packagingPartSceneEntities()` 仍然摘标注
（`## 485` 的红测不动），标注只是"画出来"，不参与轮廓 / 尺寸 / 成本。
别件的图元（包括别件的标注）一条都不许进这一张图。

### C5 不许做的事

- 不改 `packagingPartSceneEntities()` 的摘标注口径、不动后端 / 接口 / 路由 / 几何 / 成本；
- 不给 2.1 件图新写第二套缩放交互，不把裸滚轮从整栏滚动里抢走；
- 不新增依赖、不改 `viewBox`/`transform` 的分工（`viewBox` 仍由数据范围产出，缩放平移只改 `transform`）。

## 3. 本批不做（边界）

1. 「之前这里的东西 / 下面只有外轮廓的东西」这句话里的**下半句**（是否撤掉 `#packagingPartOutline`
   那块只画外圈的图）涉及 `packaging-2-1-parts-row-layout-and-shape-viewport` §C7 与部件面板守卫，
   本批只做"让这一块变成图纸原样"，撤块另批（见 §5.2）；
2. 不做图层角色的扩充（哪些真图层名算"全穿刀 / 压线"）—— 那是业务口径；
3. 不做排样 / 拼版 / 3D。

## 4. 验收

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_part_figure_fidelity_red -v
#   实现前：A / B / C 组红；D 组护栏绿
#   实现后：全绿

# 不回归（点名）
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_2_1_parts_row_layout_and_shape_viewport_red
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_2_1_right_pane_single_part_figure_red
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_task_file_dwg_opens_the_whole_plan_red
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_dimension_annotation_not_in_part_shape_red
```

## 5. 落地记录

| 位置 | 改动 |
| --- | --- |
| `app.js` | 新增 `PACKAGING_CAD_LAYER_PALETTE` / `packagingCadLayerPaletteIndex()`（纯函数）/ `packagingCadLayerColour()`（纯函数）/ `packagingPartAnnotationEntities()`（纯函数）/ `packagingShapeViewportMarkup()` |
| `app.js` | 三处渲染改走 `packagingCadLayerColour()`；`packagingPartSceneSvg()` 支持 `{includeAnnotations:true}`（默认关）；2.1 件图调用点带上它；预览 drawing 分支接视口 |
| `drawing-flow.css` | 视口高度 ×2；整图 `min-height` ×2；`.is-annotation` 虚线淡色；预览视口高度 |

### 5.1 落地读数与反向对照（本批实跑，`./open-claude/.venv/bin/python -W ignore -m unittest`）

- 本批红测 `Ran 18 … OK`；**红基**（把 `app.js` / `drawing-flow.css` 还原到本批之前）⇒
  `Ran 18 … FAILED (failures=12)`：A1–A4 / A6（配色与标注两条纯函数还没写）、B1–B3（预览没接视口）、
  C1–C4（CSS 还没翻倍）；**6 条绿的是护栏**：A5（默认仍不画标注）、B4（仅一处 createObjectURL）、
  D1–D4（形状口径 / 既有视口交互 / 轮廓块 / 语法）。
- 反向对照后 `md5` 核对：还原再取回，`app.js` 与 `drawing-flow.css` 与落地版逐字节一致。
- 点名保护网（58 个模块，含 `## 484` 视口、`## 485/486` 标注、`## 487` 单件图、CAD 平面图、
  预览、两笔账）⇒ `Ran 918 … OK (skipped=6)`。
- 全量（399 个模块）⇒ `Ran 6677 … FAILED (failures=3, skipped=28)`：两条是既有
  `test_cpq_eval_ci_contract` 环境 / 待裁决项；第三条是 `test_spec_status_truth_red` 抓的**本文件状态行**
  （红测已绿却还写「未实现」）—— 本批收口时改成「已实现」，改完即绿（这正是那条守卫要的）。

### 5.2 与既有 Spec 的关系（谁的口径被谁动了）

- `## 484`（视口）：本批只**复用**，函数体与快捷键口径没动（裸滚轮仍归整栏滚动）；
- `## 485`（标注不进形状）：形状口径不动 —— 本批只让**图**把标注画出来；
- `## 487`（右栏只画这一件）：不动，`packagingPartSceneSvg` 仍只吃这一件绑定的分量。

### 5.3 已知缺口（另批）

用户那句里的"而不是之前下面的只有外轮廓的东西"：`#packagingPartOutline` 仍会把
`outline.points` 画成一块只有外圈的图（`renderPackagingPartPanel()`）。本批没有撤它（撤它要动
`## 484` §C7 的三态与面板守卫）。撤 / 留需要点名确认。

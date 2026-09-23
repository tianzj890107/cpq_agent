# Spec：包装图纸项目一进 2.1 右栏就是 2D（不许先给一个 3D 的框）

状态：Spec + 红测（已实现）（2026-09-23 由并行会话落地：`app.js` 的 `openProject()` 里把
"进入图纸链路就切右栏"从"点了开始解析才做"前移到"项目打开时就做" —— 入口判定写回 `currentDrawingEntry`
后立刻 `try { enterDrawingFlowPanes(); } catch {}`（纯展示，切不动也不许挡住链路状态与零件文档的读回）；
本机实测 `Ran 9 … OK`）
红测：`tests/test_packaging_2_1_first_paint_is_2d_not_3d_red.py`
血缘：`docs/specs/packaging-2-1-result-parts-and-shape-only-pane.md`（§2.3 三块 3D 恒撤、§2.4 未选中时一句引导）、
`docs/specs/packaging-business-parts-and-cad-plan-view.md`（§6 右栏是 CAD 平面图）、
`docs/specs/packaging-parts-entry-readback.md`（重新进入 2.1 必须把已落库的零件读回来）。
本批 changelog 条目号：`## 484`（落地条目同为 484，见 changelog）。

## 0. 用户原话（2026-09-23）

> 为什么现在我重新刷新进来又变成上面是3D的空间了 应该直接就是这个2D的图

## 1. 实测证据（HEAD `872898b` 工作副本只读）

| 读数 | 实测 |
| --- | --- |
| 右栏的 HTML 默认态（`index.html:205-222`） | `#viewer`（`.view-3d-content`，里面写着「从「零件清单」中选择一项，查看模型…」）**默认可见**；`#packagingCadPlanViewer`（CAD 平面图）、`#packagingPartPanel`、`#packagingShapeIdle` 都带 `hidden` |
| 谁把右栏切成 2D | 只有 `enterDrawingFlowPanes()`（`app.js:1346-1356`）：隐藏 `#viewer` + `applyPackagingShapeOnlyPanes()`（打 `data-qq-no-3d` / `data-qq-fill`）+ 显示 CAD 平面图 + `loadPackagingCadPlan()` |
| `enterDrawingFlowPanes()` 的调用点 | 全文件只有一处：`parseDrawing()` 里 `if (currentDrawingEntry === "drawing_flow")` 分支（`app.js:936-937`）—— 也就是**用户点了「开始解析」之后** |
| `openProject()`（`app.js:4457-4585`） | 判定 `entry === "drawing_flow"` 之后只做：`loadDrawingFlowPanel()` + `refreshPackagingParts()` + `renderTree()`；**完全没碰右栏** ⇒ 刷新/重进 2.1 时右栏留在 HTML 默认态 = **那个 3D 的空框** |
| 恢复出来的其它交互 | 点左栏任意一件（`openPackagingBusinessPart()` / `selectPackagingPart()`）会 `setRightPane("model")` 并把 3D 与零件信息重新藏起来 —— 所以现象是"刷新先看到 3D，点一下才变 2D" |
| 影响面 | 只在前端"首屏右栏口径"：不涉及接口、后端、数据；但它是用户重进 2.1 看到的**第一眼** |

## 2. 契约

### 2.1 C1：`openProject()` 判定为图纸链路时，必须**当场**把右栏切成 2D

- 在 `entry === "drawing_flow"` 的分支里调用 `enterDrawingFlowPanes()`（与"读回链路状态 / 零件文档"同一段，
  不依赖任何用户点击）；
- 顺序要求：**先切面板、再读坐标** —— `enterDrawingFlowPanes()` 内部必须在 `loadPackagingCadPlan()`
  （网络读坐标）之前完成 `#viewer` 隐藏与 `applyPackagingShapeOnlyPanes()`，这样首屏不会先闪一个 3D 框；
- `loadPackagingCadPlan()` 读不到坐标**不许**把已经切好的 2D 面板切回去（失败只在图面上给原因）。

### 2.2 C2：`enterDrawingFlowPanes()` 必须与"有没有选中零件"无关

- 进入即调用（未选中任何件）也必须成立：函数体内不许有"没有选中件就直接返回"的前置判定；
- 未选中时右栏给那句引导（`#packagingShapeIdle`：「从左栏选一件零件，这里直接看它的形状。」），
  不留空白画布、也不留 3D 占位文案；
- 幂等：重复调用只重复设置同一批状态，不抛错、不叠加副作用（重进 2.1、切换项目都走同一条路）。

### 2.3 C3：不许误伤别的入口（反向判据）

- 视觉链路（`entry === "vision"`）与 3D 导入项目的右栏 **一个字不动**：`enterDrawingFlowPanes()`
  的**每一处**调用点都必须落在 `currentDrawingEntry === "drawing_flow"` 守卫之内；
- 3D 口径的三块（`#viewer` / `.part-details` / `parameter-panel`）在包装项目里仍由
  `[data-qq-no-3d]` / `[data-qq-fill]` 两个钩子统一撤掉，不许换成"每处各自 `hidden`"的第二套写法；
- 左栏（零件清单 / 两笔账对账 / 三档句）与流程状态栏不受本批影响。

## 3. 本批不做

- 不改 `packagingCadPlanViewer` 里的画法（本批只解决"进来看得到的是不是它"）；
- 不改左边零件清单的取数口径（那是 `## 483` 那批的刷新句）；
- 不改技术工艺（`tech-workbench`）里嵌入的 2.1 壳（同一根因，但那一壳的进入路径另批收）；
- 不为 3D 项目新增任何 2D 面板。

## 4. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_2_1_first_paint_is_2d_not_3d_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_2_1_result_parts_and_shape_only_red -v      # 27 OK 不回退
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_frontend_wiring_red -v                  # 12 OK 不回退
```

人工复核（可选）：在 34 上打开酒盒项目 → 刷新 2.1 → 右栏第一眼必须是 CAD 平面图 + 引导句，
不许出现「从「零件清单」中选择一项，查看模型…」这句 3D 口径的占位。

## 5. 红测清单（`tests/test_packaging_2_1_first_paint_is_2d_not_3d_red.py`）

- A 组（接线）：A1 `openProject()` 在 `drawing_flow` 分支里调用 `enterDrawingFlowPanes()`；
  A2 该调用落在 `currentDrawingEntry === "drawing_flow"` 判定之后；A3 全文件调用点都在同一个守卫之内；
  A4 `enterDrawingFlowPanes()` 仍负责隐藏 `#viewer` 并走 `applyPackagingShapeOnlyPanes()`；
- B 组（顺序与健壮）：B1 先切面板、后 `loadPackagingCadPlan()`；B2 与"是否选中零件"无关；
  B3 幂等（不抛错、体里没有 `throw`）；
- C 组（护栏）：C1 3D 三块仍由 `data-qq-no-3d` / `data-qq-fill` 两个钩子撤；
  C2 视觉链路的右栏默认态没被改（`index.html` 里 `#packagingCadPlanViewer` / `#packagingShapeIdle` 仍带 `hidden`）。

## 6. 落地状态（2026-09-23，Codex 实现）

| 项 | 实测 |
| --- | --- |
| 本批红测（`Ran 9`） | **OK**（红基：HEAD 工作副本 + 本红测 = `Ran 9 … FAILED (failures=2)`，另 7 条为护栏） |
| 反向对照 | 把 `openProject()` 里那句 `enterDrawingFlowPanes()` 抽掉 ⇒ A1/A2 转红 |
| 改动文件 | `tech_app/frontend/app.js`：`openProject()` 的判定改成 `currentDrawingEntry === "drawing_flow"`（与 A2 的逐字守卫一致），分支里**第一件事**是切右栏 |
| 保护网 | `tests.test_packaging_parts_entry_readback_red`（A1–A3 用 `node + vm` 桩跑 `openProject()`）、`Ran 32 … OK`、`Ran 60 … OK` |

**实现细节（必须记住，别"顺手收紧"）**：那句切面板写成

```js
// 切面板是纯展示：任何一步画不出来都不许挡住"读回链路状态 / 读回零件文档"
try { enterDrawingFlowPanes(); } catch (error) { /* 纯展示 */ }
```

—— `enterDrawingFlowPanes()` 内部会 `loadPackagingCadPlan()`（发网络请求）。不 `try/catch` 时，只要
这一趟抛错，`openProject()` 的整段就断在那里，`packaging-parts-entry-readback.md` 要的"重进 2.1 必须把
已落库的零件读回来"就一起没了（桩跑实测：不 `try` 会让那 3 条转红）。C1 的"当场切"与"切不动也不许
挡住读回"两条同时成立才是本批的口径。

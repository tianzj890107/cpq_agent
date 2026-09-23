# Spec：预览里那个「去 2.1 跑图纸解析」按钮，点了不真的去 2.1（只刷了面板、没切面板）

状态：Spec + 红测（已实现）（2026-09-23 本批落地：那个出口接上既有跳转口径 `enterDrawingFlowPanes()`，
顺序照 `openProject()` / 一键解析那一处；本机实测 `Ran 15 tests … OK`，本批 changelog 条目 `## 490`）
红测：`tests/test_packaging_preview_goto_2_1_jump_red.py`
血缘：`docs/specs/packaging-task-file-dwg-opens-the-whole-plan.md`（§C4 说的"给一个去 2.1 的出口"就是本批要接活的按钮）、
`docs/specs/packaging-2-1-first-paint-must-be-2d-not-3d.md`（`enterDrawingFlowPanes()` 的口径与 §2.3 的守卫判据）、
`docs/specs/packaging-parts-entry-readback.md`（进入即读回链路状态）
本批 changelog 条目号：`## 490`。

## 0. 症状（2026-09-23 工作副本只读）

任务文件里点一份还没解析过的 DWG，预览区逐字给出「这份图纸还没有解析结果，请先到 2.1 跑一次图纸解析。」
底下一个按钮「去 2.1 跑图纸解析」。**点它不会去 2.1**：预览是关了，但用户停在原地 ——
右栏还是 3D 空框（或上一个项目留下的状态），看不到图纸链路的面板，也找不到那个解析按钮。

## 1. 实测证据（`## 489` 落地后的工作副本）

| 读数 | 实测 |
| --- | --- |
| 那个按钮原先干什么 | `app.js::openFilePreview()` 的 drawing 分支：`window.CadFilePreview.close(); loadDrawingFlowPanel();` |
| `loadDrawingFlowPanel()` 是什么 | 只 `fetchDrawingFlowState()` + `renderDrawingFlowPanel(state)` —— **刷面板**：把 `#drawingFlowPanel` 填上并 `hidden = false`，不隐藏别的内容、**不切面板**（`app.js` 里那两行就是它的全部） |
| "进入图纸链路"的既有口径在哪 | `app.js::enterDrawingFlowPanes()`：`hidePackagingPartPanel()` + `applyPackagingShapeOnlyPanes()` + 隐藏 `#viewer` + 让出 `#packagingCadPlanViewer` + 换掉「3D 视图」那句承诺 + `loadPackagingCadPlan()` |
| 谁在用那条口径 | 一键解析（`parseDrawing()` 的 `drawing_flow` 分支）与进项目（`openProject()` 里 `currentDrawingEntry === "drawing_flow"` 那一段，两处都是 `try { enterDrawingFlowPanes(); } catch (error) { /* 纯展示 */ }`） |
| 结论 | 那个按钮只做了"刷"，没做"切" ⇒ 承诺「去 2.1」却停在原地；既有的跳转口径现成的，只是没接 |

## 2. 目标行为

### C1 那个按钮走既有跳转口径（含既有守卫）

点「去 2.1 跑图纸解析」时：先 `window.CadFilePreview.close()`（关预览，顺序不变），再调
`enterDrawingFlowPanes()` —— 与 `openProject()` / 一键解析**同一处口径**，不新写第二套"切面板"逻辑。
并且这一处调用也要落在**同一条守卫** `currentDrawingEntry === "drawing_flow"` 之内
（`docs/specs/packaging-2-1-first-paint-must-be-2d-not-3d.md` §2.3：`enterDrawingFlowPanes()` 的**每一处**
调用点都必须在图纸链路守卫里，视觉链路一个字不动）。项目 `meta.source_filename` 是 DWG / DXF 时，
`openProject()` 已经把 `currentDrawingEntry` 定成 `drawing_flow`（`renderDrawingEntry()` 一处判定），
所以这个出口落到目标场景里就是真的切过去。

### C2 切面板是纯展示，不许挡住读回

`enterDrawingFlowPanes()` 那一句要包在 `try { … } catch (error) { /* 纯展示 */ }` 里（与
`openProject()` 里同一写法）：沙箱 / 老壳里没有这套钩子，也不许挡住下一步；随后仍然要
**刷新链路状态**（`loadDrawingFlowPanel()`）—— 两个动作的顺序是「先切、再读」，首屏不闪 3D 空框。

### C3 文案与判定一个字不改

- 「这份图纸还没有解析结果，请先到 2.1 跑一次图纸解析。」与按钮文字「去 2.1 跑图纸解析」逐字不变；
- 五条既有分支（`image` / `pdf` / `text` / `model` / `other`）、`URL.createObjectURL` 只在 image / pdf 那一条、
  `window.CadFilePreview = { open, close }` 契约，全部不动；
- 预览仍然只有一份实现：`agent-chat.js` 里不许出现 `enterDrawingFlowPanes`。

## 3. 本批明确不做（边界）

1. 不改 `enterDrawingFlowPanes()` 自身（口径、它切哪几块、它顺手读坐标，全不动）；
2. 不改后端 / 接口 / 路由；不动几何、成本、工艺、需求任何口径；
3. 不给预览加"带项目 id 跳转"这类新参数，不引入新全局；
4. 不改"还没有解析结果"这个判定的条件（本批只修出口）。

## 4. 验收

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_preview_goto_2_1_jump_red -v
#   实现前：A 组红（没接既有跳转口径）；B / C 组护栏绿
#   实现后：全绿
```

反向对照（`## 490` 实跑）：把新加的那一句 `if (currentDrawingEntry === "drawing_flow") enterDrawingFlowPanes();`
整块删掉、其余一字不动 ⇒ `Ran 15 tests … FAILED (failures=4)`，红的正好是 A1（没有跳转调用）、
A2（没有 try 包住 + 没有守卫）、A3（顺序缺"切"）、A5（全文件调用点退回 2 处）；B / C 组 11 条
护栏仍绿 —— 说明 A 组测的是这一处接线本身，不是"文本碰巧命中"。

## 5. 落地记录（`## 490`，只改 `tech_app/frontend/app.js` 一个文件）

| 位置 | 改动 |
| --- | --- |
| `openFilePreview()` 的 drawing 分支，`goto.addEventListener("click", …)` | 在 `window.CadFilePreview.close();` 之后、`loadDrawingFlowPanel();` 之前插入 `try { if (currentDrawingEntry === "drawing_flow") enterDrawingFlowPanes(); } catch (error) { /* 纯展示 */ }` |

`git diff --stat`：`1 file changed, 6 insertions(+)`，删改零处。

### 5.1 本机实测读数

- 新红测：`Ran 15 tests … OK`（A 组 5 条、B 组 7 条、C 组 3 条）；
- 点名保护网：`tests.test_packaging_task_file_dwg_opens_the_whole_plan_red` +
  `test_tech_file_preview_in_card_and_auth_red` + `test_packaging_2_1_first_paint_is_2d_not_3d_red` +
  `test_packaging_parts_entry_readback_red` + `test_packaging_drawing_preview_ownership_note_source_red` +
  `test_packaging_2_1_right_pane_single_part_figure_red` ⇒ `Ran 100 tests … OK`；
- `node --check tech_app/frontend/app.js` 通过。

### 5.2 已知缺口（不在本批，记着别当已修）

1. 项目 `meta.source_filename` 不是 DWG / DXF（视觉项目）但任务文件里挂了一份 DWG 时，这个出口
   在现有守卫下**什么也不切**（只关预览）—— 那句"请先到 2.1 跑一次图纸解析"对视觉项目本身就不准，
   属于 §3.4 明说不改的判定条件，留给后续批次（要么改判定，要么这个出口在视觉链路里换成别的说法）；
2. `enterDrawingFlowPanes()` 里 `$("viewerPartName").textContent = "几何分量（图纸零件）· 选中后看轮廓与证据"`
   把"整张图纸上的几何分量"摆成"图纸零件"（几何通道 vs 业务件两条账），另批核对。

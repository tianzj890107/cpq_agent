# Spec：零件清单点零件进 3D 视图，「工艺推荐」按钮才进工艺推荐

## 背景（用户反馈）

「零件清单点零件的话就去 3D 视图，现在点零件和点工艺推荐都是去的工艺推荐。」

## 根因（源码已核实）

- `tech_app/frontend/app.js` 的 `selectPart()` 末尾无条件调用
  `autoOpenGeneratedProcess(part)`（约 `app.js:1864`）。
- `autoOpenGeneratedProcess()` 对该零件执行只读 `partHasExistingProcess()`，
  一旦库里已有工艺，就 `openPartAnalysis(part, "process")` 把右栏从 3D 切到工艺推荐。
- 于是所有「选中零件」的入口都被劫持：零件行点击（`renderTree` 的
  `div.onclick = () => selectPart(p)`，`app.js:1368`）、2D 缩略图 bbox 点击
  （`app.js:1514`）、生成结果展示（`showGeneratedResult()`）都会跳工艺推荐；
  零件行下面本来独立的「工艺推荐」子按钮（`buildPartSubActions()`）反而看不出区别。
- 3D 只在「该零件还没有工艺」时才出现，语义倒过来了。

## 范围

- 只改 `tech_app/frontend/app.js` 的「选中零件」与「生成完成后自动展开」两处接线，
  以及被本批推翻的 1 条旧断言。
- 不动：`openPartAnalysis()` / `renderPartAnalysis()` / `CadInlineAnalysis` 渲染、
  `partHasExistingProcess()` 判定、单件与批量工艺生成接口、看板视图注册
  （`parts-list` / `part-detail` / `part-process` / `part-cost`）与返回键、
  父壳与 `tech:command` 协议、后端路由与 Agent 工具。

## R1 点零件 = 3D 视图

- `selectPart(part)` 只保留既有 3D/零件详情渲染：`window.CadInlineAnalysis?.reset()`、
  `setRightPane("model")`、`exitBoardViewHost()`、`markSelection()`、
  `togglePartSubActions()`、`updateChatContext()`、`notePartView("part-detail")`。
- `selectPart()` 里**不得**出现 `autoOpenGeneratedProcess(`、`openPartAnalysis(` 或
  `renderPartAnalysis(` —— 选中零件不再替用户决定看哪一块结论。
- 零件行点击（`renderTree`）与 2D 缩略图 bbox 点击继续只调 `selectPart()`。

## R2 工艺推荐入口保持唯一且不变

- 零件行下的「工艺推荐」子按钮仍走 `openPartAnalysis(part, "process")` →
  `showPartView("part-process")` → `renderPartAnalysis(part, "process")`；
  子按钮 `event.stopPropagation()` 继续阻止冒泡到零件行。
- `part-process` / `part-cost` 的视图注册与父子关系（`PART_VIEW_PARENT` /
  `PART_FLOW_VIEWS`）不变。

## R3 「已生成即自动展开」只保留在解析 / 生成完成的时机

用户此前的要求（图纸解析那一步，工艺推荐已生成就自动展开）继续满足，但改在
**生成/解析完成**的时机补一次，而不是每次选中零件都抢：

- `showGeneratedResult()`：生成几何 / 工程图跑完后既有的「自动选中零件」之后，
  补一次 `autoOpenGeneratedProcess(target)`。
- `runAllPartProcessesInBackground()` 收尾：批量工艺推荐完成后对当前零件补一次
  （既有行为，保留）。
- 同一零件仍只自动展开一次（`autoOpenedProcessParts` 去重），失败静默回落 3D，
  不新增请求、不触发生成、不新建面板。

## 验收

- 红测：`tests/test_tech_part_click_goes_3d_not_process_red.py`（先红后绿）。
- 过期断言更新 1 处（注明「契约更新（点零件=3D 批次）」）：
  `tests/test_tech_drawing_toolbar_cleanup_and_process_auto_expand_red.py`
  的 `test_select_part_triggers_auto_open` 改为「`selectPart` 不得自动展开 +
  生成完成路径保留自动展开」。
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'` 不新增失败；
  `node --check tech_app/frontend/app.js`；`git diff --check`。
- 浏览器：点零件行 → 右栏是 3D 视图（零件详情）；点零件行下的「工艺推荐」→
  右栏是工艺推荐页；解析完成后若该零件工艺已生成仍会自动展开一次。

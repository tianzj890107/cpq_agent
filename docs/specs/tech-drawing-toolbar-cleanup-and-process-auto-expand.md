# Spec: 2.1 图纸解析左侧按钮清理与「已生成工艺推荐」自动展开

状态：TDD Red，等待 DeepSeek 实现。
适用分支：`20260909`
相关步骤：2.1 图纸解析（`tech_app/frontend/index.html` + 统一父壳 `tech-workbench.html`）

## 1. 目标

1. 2.1「联网核验」「校验修正」不再出现在左侧会话操作栏 —— 它们已经在 2.1 右侧的
   「更多功能 ▾」里，左侧重复一颗按钮只会让人分不清点哪个。动作本体、Agent 工具
   分派与看板注册一律保留，只是不再由左侧操作栏渲染。
2. 「解析视图」也不再出现在左侧会话操作栏 —— 它同样是重复入口。`evidence` 看板视图
   本体保留，Agent / 看板内部链路仍可打开。
3. 2.1 的收口主按钮文案由「确认解析结果并进入下一步」改为「确认解析结果」。文案变短，
   行为完全不变（仍先回读后端真实状态，确认成功后才进 2.2 组装与整合）。
4. 2.1 里如果某个零件的工艺推荐已经生成，就自动在右侧展开该零件的工艺推荐，不必再点
   一次「工艺推荐」。

## 2. 现状（实测）

- 左侧操作栏按看板动作快照渲染所有 `visible !== false` 的动作（`tech-workbench.js:681`
  `syncChatActionList`），而 `app.js:2250` `modelLookup`、`app.js:2251` `verify` 的
  `getState()` 都返回 `visible: true`，所以左侧栏里一直有这两颗按钮。
- 2.1 右侧「更多功能 ▾」已经有同名两项：`index.html:179` 的 `#actionSheet` 内
  `#btnModelLookup`（联网核验）与 `#btnVerify`（校验修正）。
- 左侧还有一颗静态「解析视图」按钮：`tech-workbench.html:105`
  `<button type="button" data-tech-capability="evidence">解析视图</button>`。
- 收口动作标签：`app.js:2207` `label: "确认解析结果并进入下一步"`。
- 工艺推荐读取接口已存在：`GET /api/projects/{project_id}/parts/{part_id}/process`
  （`tech_app/backend/main.py:2114`，返回 `plan` / `validation` / `coverage`），
  `inline-analysis.js:109` 的 `load()` 正在用它；`POST` 同路径（`main.py:2064`）才是生成。
- 前端已有「库里是否已有工艺」判定：`app.js:2022` `partHasExistingProcess(projectId, partId)`
  （批量生成时用来 skip 已算过的零件）——本轮自动展开直接复用它。
- 选中零件一律切回 3D：`app.js:1741` `selectPart()` 里 `setRightPane("model")`；
  `app.js:1428` `openPartAnalysis(part, "process")` 才是打开工艺推荐面板的唯一入口。

## 3. 契约

### R1 左侧按钮清理

- `tech-workbench.html` 的 `#techChatActions` 内不得再出现「解析视图」，整份父壳 HTML
  不得再出现 `data-tech-capability="evidence"`。
- `app.js` 的 `modelLookup` / `verify` 条目改为 `visible: false`（保留 `label`、`role`、
  `order`、`run`、`enabled` 判定），因此左侧栏不再渲染它们。
- 两者仍是可执行动作：`run: () => runModelLookup()` / `run: () => runVerification()` 原样保留，
  `agent-chat.js` 的 `DRAWING_ACTION_CAPABILITIES = ["modelLookup", "verify"]`、
  `dispatchBoardAction()` 与 `CAPABILITY_LABELS` 不动 —— Agent 的
  `execute-action` 链路继续可用。
- 能力本体不缩水：`app.js` 的 `BOARD_VIEW_SPECS.evidence` 与
  `TechBoardRuntime.registerViews` 的 `evidence` 视图保留；2.1 的「更多功能 ▾」
  （`#btnModelLookup` / `#btnVerify`）保留。

### R2 收口按钮文案

- `confirmDrawingResult` 条目的 `label` 恰为 `"确认解析结果"`，条目块内不得再出现
  「并进入下一步」。
- 行文之外不改行为：仍回读 `GET /api/projects/<id>` 真实状态、仍用 `drawingParsed()`
  闸门、仍在缺少嵌入导航通道时返回 `no-navigation`、仍 `requestNavigate("process", ...)`、
  仍按 `drawingParsed()` 反转 `role`。
- 只改 2.1 这一条：其它阶段的「…并进入下一步」文案（`assembly-integration.js` 等）不动。

### R3 「已生成工艺推荐」自动展开

- 判定复用既有实现，不新增第二份读取：`app.js:2022`
  `async function partHasExistingProcess(projectId, partId)`——只读
  `GET /api/projects/<id>/parts/<part_id>/process`，`data.plan.steps` 非空才算已生成，
  失败返回假。该函数与它的 GET 语义不得改动、不得复制第二份。
- 新增动作：`function autoOpenGeneratedProcess(part)`：
  1. `part` 缺失或 `!currentProject` 直接返回；
  2. 命中 `autoOpenedProcessParts` 去重集合直接返回；
  3. `await partHasExistingProcess(currentProject, part.part_id)` 为真时调用既有
     `openPartAnalysis(part, "process")`（复用 `#analysisPanel` + `CadInlineAnalysis`，
     不新建面板、不新建数据源、不发 `fetch`），**展开成功后才**
     `autoOpenedProcessParts.add(part.part_id)`。
- 去重语义：同一零件在一次看板会话里只自动展开一次；用户点「返回零件详情」回到 3D 后
  不会被立刻拉回；读取失败不记标记，下次进入还有机会。
- 触发点：
  1. `selectPart(part)` 末尾（含进入步骤后的自动选中）；
  2. 批量「一键生成全部工艺推荐」完成链路 `runAllPartProcessesInBackground()` 内
     （生成刚完成时对当前选中零件补一次自动展开）。

### R4 降级与边界

- 读取失败或没有 `plan`：静默保持 3D，不写页面状态提示、不弹窗、不写会话提示。
- 只改右侧视图：不创建父级 Drawer/Modal，不动 `#techChatActions`，不改桥协议
  （`tech-board-runtime.js` / `tech-board-bridge.js` 的 `tech:command` / `tech:state`
  语义与视图名集合一律不动）。
- 不新增后端路由、不新增工艺算法、不在自动展开链路里调用生成接口（`POST .../process`）。

## 4. 禁止事项

- 不删除 `modelLookup` / `verify` / `evidence` 的动作或视图注册，不删
  `#btnModelLookup` / `#btnVerify`。
- 不用 `visibility:hidden` / 零高度等手段"藏"左侧按钮；直接不渲染。
- 不把「确认解析结果」改成自动进入下一步，也不允许它跳过 `drawingParsed()` 闸门。
- 不在自动展开里重新生成工艺推荐、不重复请求生成接口、不写死示例零件。
- 不改 3D/2D、参数编辑、版本与校核、导出 BOM、批量工艺推荐等既有能力。

## 5. 验收

1. `python3 -m unittest tests.test_tech_drawing_toolbar_cleanup_and_process_auto_expand_red`
   全绿（实现前应失败）。
2. 全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 失败数在原有 16 项之外
   不新增；本批落地后归零。
3. 浏览器：2.1 左侧操作栏只有当前步骤业务动作（「解析视图」「联网核验」「校验修正」
   都不再出现），右侧「更多功能 ▾」里两者仍在；收口按钮显示「确认解析结果」，点击后
   进 2.2；某零件已有工艺推荐时，选中它右侧自动展开该零件工艺推荐，点
   「返回零件详情」后保持 3D 不被拉回。

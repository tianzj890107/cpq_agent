# 技术工艺：1.1 主按钮随解析状态切换、2.1 能力入口归位到「更多功能」并自动生成 3D/2D

状态：Spec + 红测（已实现）
红测：`tests/test_tech_step_primary_and_drawing_entry_cleanup_red.py`

## 1. 问题（实测）

### 1.1 创建需求：主按钮挂错了

`tech_app/frontend/requirement-create.js:257-268`：

- `submitRequirement`（提交确认）写死 `role: 'primary'`、`order: 10`；
- `extractRequirement`（一键解析需求）写死 `role: 'aux'`、`order: 30`。

于是进入 1.1 第一眼看到的主按钮是「提交确认」—— 可这一步真正的起点是「一键解析需求」：
字段没解析、需求单还是空的，提交只会被校验拦回来。解析完成后主按钮才应该交给「提交确认」。
对照 2.2 的做法（`assembly-integration.js:1498-1517`）：`runIntegration` 与 `sendToFinance` 的
`role` 由 `getState()` 按 `aiAnalyzed()` 动态反转 —— 1.1 缺的就是这一份动态切换。

### 2.1 图纸解析：能力入口重复，3D/2D 还要手点

`tech_app/frontend/index.html:179` 的「更多功能 ▾」菜单（`#btnMoreActions` + `#actionSheet`）已经有
`#btnModelLookup 联网核验`、`#btnVerify 校验修正`、`#btnDecompose 拆解推荐`、`#btnGenerate 生成 CAD 几何`、
`#btnDrawings 生成 2D 工程图`、`#btnBom 导出 BOM（CSV）`。

但统一工作台左侧会话栏 `tech_app/frontend/tech-workbench.html:113-117` 又并排放了五项能力按钮
（`data-tech-capability` = `evidence` / `import3d` / `review` / `modelLookup` / `verify`），
其中「联网核验」「校验修正」与「更多功能」里的完全重复；而「导入已有 3D 模型」「版本与校核审查」
反倒不在「更多功能」里（只能从 2.1 页自己的左侧图标栏 `data-open-drawer="import3d"` / `"review"` 进）。

另外，解析成功后 `parseDrawing()`（`app.js:861-906`）只把 `#btnVerify` / `#btnDecompose` /
`#btnModelLookup` / `#btnGenerate` / `#btnDrawings` / `#btnBom` 逐个 `disabled = false`，
用户还要自己打开「更多功能 ▾」点「生成 CAD 几何」、再点「生成 2D 工程图」，右侧才看得到 3D 与 2D。

## 2. 目标

1. **1.1 主按钮按解析状态反转**：未解析时主按钮 = 「一键解析需求」；解析完成后主按钮 = 「提交确认」。
   两者都是看板声明的 `role`，父壳按既有规则渲染到唯一主按钮槽位，视觉与「开始整合分析」一致（主色实心）。
2. **2.1 能力入口不重复**：统一工作台左侧会话栏不再重复提供 2.1 的四项能力；
   「导入已有 3D 模型」「版本与校核审查」进入 2.1 的「更多功能 ▾」菜单，
   与已有的「联网核验」「校验修正」并列；「解析视图」仍留在左侧会话栏。
3. **解析后自动生成 3D 与 2D**：解析成功后自动串行跑几何生成与 2D 工程图（复用既有实现与既有接口），
   生成完直接显示在右侧，用户不必再进「更多功能」点两次。
4. 能力一个不少：`#btnImport3d` / `#secImport3d` / 2.1 左侧图标栏 / 九个看板视图 / 后端接口全部保留。

## 3. 契约 A：1.1 主按钮动态反转（`requirement-create.js`）

- 用同一个判定表达「需求已解析」，两处共用，不允许各写一份：
  建议 `const rcExtracted = () => Boolean((rcData() || {}).document_extraction);`
  （`document_extraction` 由既有 `/requirement/extract-documents` 流程写入 `rcRequirement.data`）。
- `extractRequirement.getState()`：`role: rcExtracted() ? 'aux' : 'primary'`，`order: 10`。
- `submitRequirement.getState()`：`role: rcExtracted() ? 'primary' : 'aux'`，`order: 20`。
- 其余动作（`saveRequirementDraft` order 30、`refreshData` visible:false）不变；
  每个动作的 `getState()` 仍是 `enabled: true` + `busy: rcBoardBusy`（前置条件不灰按钮，
  与 `tech-business-actions-clickable-then-error` 的语义一致）。
- `run()` 实现、`label`、`deferred` 标记与后端调用（`/requirement/extract-documents`、
  `/requirement/submit-confirmation`）一字不改。

## 4. 契约 B：2.1 能力入口归位（`tech-workbench.html` + `index.html` + `app.js`）

- `tech-workbench.html`：删除 `#techChatActions` 里
  `data-tech-capability="import3d"` / `"review"` / `"modelLookup"` / `"verify"` 四个按钮；
  保留 `data-tech-capability="evidence"`（解析视图）与其余入口。
  （`agent-chat.js` 的 `capabilityButtons` 是从 DOM 查出来的，删节点即自动解除绑定与高亮，
  不需要也不得在 `agent-chat.js` 里另加黑名单。）
- `index.html`：`#actionSheet` 里新增两颗按钮，与既有六颗并列：
  - `<button id="btnMoreImport3d" type="button">导入已有 3D 模型</button>`
  - `<button id="btnMoreReview" type="button">版本与校核审查</button>`
- `app.js`：新按钮只复用既有视图入口，不新建第二套面板：
  ```js
  $("btnMoreImport3d").onclick = () => { $("actionSheet").hidden = true; return runBoardView("import3d"); };
  $("btnMoreReview").onclick = () => { $("actionSheet").hidden = true; return runBoardView("review"); };
  ```
  （`runBoardView("import3d")` → `BOARD_VIEW_SPECS.import3d`，`runBoardView("review")` →
  `BOARD_VIEW_SPECS.review`，都是既有实现。）
- 「更多功能 ▾」的既有六项（联网核验 / 校验修正 / 拆解推荐 / 生成 CAD 几何 / 生成 2D 工程图 /
  导出 BOM）保留，不删不加第二份。

## 5. 契约 C：解析后自动生成 3D 与 2D（`app.js`）

- 把 `#btnGenerate` / `#btnDrawings` 的内联实现抽成具名函数（与既有 `parseDrawing()` /
  `runModelLookup()` / `runVerification()` 同模式），按钮与自动流程共用同一份实现：
  ```js
  async function generateGeometry() { /* 原 #btnGenerate.onclick 体，一字不改 */ }
  async function generateDrawings() { /* 原 #btnDrawings.onclick 体，一字不改 */ }
  $("btnGenerate").onclick = generateGeometry;
  $("btnDrawings").onclick = generateDrawings;
  ```
- 新增 `async function autoGenerateAfterParse()`：
  - 只在「图→IR」项目（`currentIsImg`）且解析成功（`currentIR`）时触发；3D 导入项目直接跳过
    （几何与 2D 在 3D 解析阶段已经生成）。
  - 先 `await generateGeometry()`，**几何成功后再** `await generateDrawings()`；几何失败就停在这里，
    把真实原因写进状态行（复用既有 `status(...)` 文案）并返回结构化失败，
    不允许静默、也不允许"失败还继续跑 2D"。
- `parseDrawing()` 解析成功的路径末尾调用 `await autoGenerateAfterParse()`；
  解析失败路径不触发。右侧显示由既有 `showGeneratedResult()` 负责，不新增第二套渲染。

## 6. 非目标与保护边界

- 不改后端任何路由与实现：`POST /api/projects/3d`、
  `/api/projects/{project_id}/generate`、`/api/projects/{project_id}/drawings`、
  `/api/projects/{project_id}/decompose`、`/api/projects/{project_id}/parse` 照旧。
- 不删除既有能力与节点：`#btnImport3d` / `#file3d` / `#secImport3d` / `#secVersions`、
  2.1 左侧图标栏 `data-open-drawer="import3d"` / `"review"`、
  `BOARD_VIEW_SPECS` 的 `import3d` / `review`、`registerViews` 的 `import3d` / `review`
  与 `#actionSheet` 既有六项全部保留。
- 不改 `cpq:tech-board` 信封、事件白名单、`entryState()` 字段契约与 `role` / `order` 语义；
  不新增第二套主按钮槽位，不改 `.tech-chat-actions > button.primary` 的主色实心样式。
- 不改 1.1 字段渲染、行业模板、附件流程与校验规则。
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

## 7. 验收

- `python3 -m unittest tests.test_tech_step_primary_and_drawing_entry_cleanup_red -v` 全绿。
- 回归：`tests.test_tech_step_primary_switch_*`（如有）、
  `tests.test_tech_board_actions_into_left_toolbar_red`、`tests.test_tech_drawing_agent_actions_red`、
  `tests.test_tech_board_bridge_protocol_red`、`tests.test_tech_left_toolbar_drop_generic_buttons_red`
  （同一批 B 的裁剪契约）。
- `node --check` 覆盖 `requirement-create.js`、`app.js`（module）、`agent-chat.js`；`git diff --check` 通过。
- 浏览器：① 进入 1.1 未解析时主按钮是「一键解析需求」，解析完成后主按钮变「提交确认」；
  ② 2.1「更多功能 ▾」里能直接进「导入已有 3D 模型」与「版本与校核审查」，
  左侧会话栏不再有联网核验 / 校验修正 / 导入 3D / 版本与校核审查四颗重复按钮；
  ③ 点「开始解析」后不动手，右侧自动出现 3D 与 2D 工程图。

## 8. 对应测试

`tests/test_tech_step_primary_and_drawing_entry_cleanup_red.py`

# 2.1 零件详情 / 工艺推荐面板收口 + 「更多功能 / 任务文件」弹卡片 Spec

状态：TDD Red，等待 DeepSeek 实现。

## 用户口径（原话，逐条编号）

> U1 这部分内容不需要了，要说什么就在左边 agent 输入对话框就好了 ——
>    「补充工艺说明，如材料状态、关键表面粗糙度、设备或检验要求…」
> U2 「选择补充文件」这整行都不要
> U3 最上面的「返回零件详情」那个按钮不要，重复了
> U4 「重新生成工艺推荐」「编辑」这两个按钮放在右上角的「返回零件详情」左边；
>    「重新生成工艺推荐」不要现在的样式，改成和另外两个按钮一样样式
> U5 「返回零件清单」这个按钮不需要了，因为现在零件清单一直显示
> U6 「更多功能 ▾」这个按钮做成和「返回零件详情 / 选择补充文件 / 编辑」一样样式
> U7 「导入已有 3D 模型」和「版本校核审签」怎么是置灰的点不了？
> U8 下面这些内容不要在零件详情里显示，而是「更多功能」里面点了之后弹出卡片
> U9 左边的「任务文件」那里也是，不要显示在工作区，而是像设置模型一样弹出卡片

本批一起做：**A 面板头部与输入区收口、B 头部按钮样式统一、C 置灰缺陷、D 三处改弹卡片、E 零件详情不再内嵌版本面板**。

## 1. 只读定位（实测，未改任何业务实现）

- U1/U2 的输入行 = `tech_app/frontend/inline-analysis.js:76` 的
  `<div class="inline-analysis-inputs"><textarea data-inline-note …>${quantity}<label class="inline-file-picker">…选择补充文件…</label></div>`；
  说明文案 = `:63-65` 的 `notePlaceholder`（process 分支正是用户引用的那句）。
  `data-inline-note` / `data-inline-files` 的取值在 `extraForm()`（`:186-189`）→ 作为 POST 的表单字段；
  cost 的「批量」`data-inline-quantity` 在 `:67`、`:201`（走 URL query）。
- U3 的两个「返回零件详情」：`index.html:201` 的 `#btnBackToModel`（在 `.analysis-panel-bar` 里，位置在上）
  与 `inline-analysis.js:73` 的 `<button class="inline-analysis-close" data-inline-close>返回零件详情</button>`
  （在面板头部右侧）。`app.js:570-585` 绑前者。
- U4 的两颗按钮 = `inline-analysis.js:77` 的 `data-inline-generate`（class 含 `inline-action primary start-parse-btn`）
  与 `data-inline-edit`（class = `inline-action`）；它们在 `.inline-analysis-actions` 行里，不在头部。
  生成按钮的字形由 `renderControls()`（`:154-176`）写成「生成 / 重新生成工艺推荐」。
- U5 的 `#btnBoardBackList`（`index.html:188`，`hidden` 起步）由 `app.js:586-592` 绑定、
  `app.js:2974-2977` 的 `syncPartViewControls()` 按 `boardPartView === "part-detail"` 显示。
- U6 的 `#btnMoreActions`（`index.html:188`）class = `report-btn`。而
  「返回零件详情」= `.inline-analysis-close`（`inline-analysis.css:5`：`padding:5px 8px` / `font-size:11px`）、
  「编辑」= `.inline-action`（`:8`：`padding:6px 10px` / `font-size:11px`）、
  「选择补充文件」= `.inline-file-picker span`（`:7`：`padding:6px 8px` / `font-size:10px`）——
  三颗都是「白底 + 1px 蓝边 + 小号蓝字」。`#btnMoreActions` 用的 `workbench.css:161`
  却是 `padding:9px 13px` / `font-size:600 13px` / `border-radius:8px` / 带阴影，明显更大更重 —— 这就是不一致的来源。
- U7 的置灰是**缺陷**，不是设计：`#btnMoreImport3d` / `#btnMoreReview`（`index.html:188` 的 `#actionSheet` 内）
  只在 `app.js:885-892` 的 `parseDrawing()` 成功分支里被 `disabled = false`；
  而**打开已有项目**的常态入口 `openProject(pid)`（`app.js:1387`，`:1435-1440` 只处理
  `btnVerify / btnDecompose / btnModelLookup / btnGenerate / btnDrawings / btnBom`）从不启用这两颗。
  工作台里 2.1 页总是以「打开已有项目」进入，所以这两颗永远停在 HTML 的 `disabled` 上 → 用户看到的就是「点不了」。
- U8 的「下面这些内容」= 2.1 的四个 `[data-drawer-section]` 面板：`#secImport3d`（导入已有 3D 模型）、
  `#secVersions`（版本与校核审签）、`#verificationDetails`（AI 校核待确认）、`#modelLookupDetails`（AI 型号联网核验）。
  其中**只有 `#secVersions` 会被搬进零件详情**：`app.js:2090-2106` 的 `selectPart()` 把 `#secVersions`
  `append` 到 `#partDetail` 里的 `#partDetailVersions` 槽位；其余三个靠
  `BOARD_VIEW_SPECS.review / import3d`（`app.js:2763-2767`）搬进工作区宿主 `#boardViewHost`。
- U9 的「任务文件」有两个入口：2.1 页自己的悬浮小窗 `#ocFilesDock`（`index.html:97`），
  与左侧会话栏的 `#ocFilesAction`（`tech-workbench.html:89-93`）。后者经
  `agent-chat.js:1947` 的 `["ocFilesAction","files"]` → 看板桥 → `runBoardView("files")` →
  `BOARD_VIEW_SPECS.files`（`app.js:2768`）→ `renderBoardFiles()`（`app.js:2814`）**渲染进 `#boardViewHost`**，
  也就是「显示在工作区」。
- 参照实现（用户点名「像设置模型一样」）：`tech-workbench.html:211` 的
  `#techModelSettingsMask` + `#techModelSettings` + `#techModelSettingsBody` + `#techModelSettingsClose`，
  行为在 `tech-workbench.js:961-1000`（`openTechModelSettings` / `closeTechSettings` / Esc / 点遮罩关闭 / 焦点归还）。

## 2. 契约 A：面板头部与输入区（`inline-analysis.js` + `inline-analysis.css`）

A1. **不再有说明与附件输入**：`renderShell()`（`:53-82`）的模板里
`data-inline-note`、`data-inline-files`、`inline-file-picker`、`notePlaceholder` 一律不再出现；
`notePlaceholder` 变量本身删除（文案不再需要）。cost 的「批量」`data-inline-quantity` **保留**。

A2. `.inline-analysis-inputs` 只在 **cost** 模式渲染，且行内只剩「批量」；process 模式不渲染这一行
（`${mode === "cost" ? … : ""}`）。

A3. **头部一行放下四颗按钮**（顺序固定，从左到右）：

| 位置 | 节点 | 文字 |
|------|------|------|
| 1 | `data-inline-generate` | `生成工艺推荐` / `重新生成工艺推荐`（`renderControls()` 写） |
| 2 | `data-inline-edit` | `编辑` |
| 3 | `data-inline-save` | `保存`（`hidden` 起步不变） |
| 4 | `data-inline-close` | `返回零件详情` |

四颗按钮都在 `.inline-analysis-head` 内、都排在 `data-inline-close` 之前；独立的
`.inline-analysis-actions` 容器**删除**（不要再有第二行按钮）。

A4. **样式统一**：四颗按钮的 class 都含 `inline-action`；`data-inline-generate` 不再带
`primary` / `start-parse-btn`（`inline-analysis.css:8` 的渐变主色样式对工艺推荐不再适用）。

A5. `bindShell()`（`:85-106`）里 `[data-inline-files]` 的 `onchange` 绑定随之删除；
`extraForm()`（`:186-189`）删除，`generate()`（`:196-205`）的 POST 改为发送 **空 `FormData`**
（保证既有后端表单解析口径不变）；cost 的 `?quantity=` 保持原样。

A6. `inline-analysis.css` 删除 `.inline-file-picker` 三条规则；`.inline-analysis-inputs` 保留但
`grid-template-columns` 不再需要 `auto auto` 两列（只剩「批量」一列）；
`@media(max-width:700px)` 里的 `.inline-file-picker` 分支一并删除。

## 3. 契约 B：2.1 头部按钮（`index.html` + `app.js`）

B1. 删除 `#btnBackToModel` 与它所在的 `<div class="analysis-panel-bar">`（`index.html:200-202`）；
`#analysisPanel` 只保留 `#analysisHost`。`app.js:570-585` 的绑定块与
`BACK_TO_PART_DETAIL`（`:67`）一并删除。

B2. 删除 `#btnBoardBackList`（`index.html:188`）；`app.js:586-592` 的绑定块、
`BACK_TO_PARTS_LIST`（`:66`）与 `syncPartViewControls()`（`:2974-2977`）里的同步块一并删除
（函数若因此空掉，就删掉函数与其调用点）。

B3. `#btnMoreActions`（更多功能 ▾）的 class 从 `report-btn` 改为 `inline-action`
（与「编辑」「返回零件详情」同一颗），`workbench.css:160-162` 的 `.report-btn` 规则**保持不动**
（其它页面在用）。

## 4. 契约 C：`#btnMoreImport3d` / `#btnMoreReview` 的置灰是缺陷

C1. 新增一个具名函数 `syncActionSheet(ir)`（`app.js`），作为 `#actionSheet` 八颗按钮**唯一**的启用判定：
`btnMoreImport3d` 与 `btnMoreReview` 只要 `Boolean(currentProject)` 就 `disabled = false`；
其余六颗沿用今天既有规则（`!isImg` / `!data.ir`，`app.js:1435-1440`）。

C2. `parseDrawing()`（`:885-892`）与 `openProject()`（`:1435-1440`）**两处共用**这一个函数，
不得各写一份。打开已有项目后，这两颗按钮必须是可点的（这是本批要修的缺陷本体）。

C3. 面板自己在缺数据时给空态文案（例如「本项目还没有版本记录」），不靠置灰代替说明。

## 5. 契约 D：「导入已有 3D 模型 / 版本与校核 / 任务文件」改弹卡片

D1. `index.html` 新增卡片外壳（静态 DOM，语义照抄 `#techModelSettingsMask`）：
`#boardCardMask`（全屏遮罩）+ `#boardCard`（`role="dialog"`、`aria-modal="true"`、`aria-labelledby="boardCardTitle"`）
+ `#boardCardTitle` + `#boardCardBody` + `#boardCardClose`。

D2. `workbench.css` 新增 `.board-card-mask` / `.board-card` / `.board-card-head` / `.board-card-title` /
`.board-card-body` / `.board-card-close` 六条规则，含 `.board-card-mask[hidden]{display:none}`；
遮罩居中卡片（沿用 `#techModelSettings` 的居中方式），正文区可滚动。

D3. `BOARD_VIEW_SPECS` 的 `import3d` / `review` / `files` 三项加 `card: true`（其余视图不动）。

D4. 新增两个具名函数 `openBoardCard(view, spec)` / `closeBoardCard()`，并由 `openBoardView()`
（`app.js:2895-2911`）对 `card: true` 的视图转调 `openBoardCard`：
渲染进 `#boardCardBody`（`sections` 仍搬既有节点、`files` 仍调 `renderBoardFiles`、`report` 不带 `card`），
显示 `#boardCardMask`；**不**调用 `boardViewHost()`、**不**隐藏 `#modelPanes` / `#analysisPanel`、
**不**改工作区标题。工作区因此不再被这三个视图占用。

D5. 关闭路径：`#boardCardClose`、`Esc`、点遮罩空白 → 一律走 `closeBoardCard()`：把卡片里的
`[data-drawer-section]` 节点按既有 `resetBoardViewBody()` 口径放回 `#ocDrawerBody`、隐藏遮罩、
焦点归还给触发按钮；`closeBoardView()` 也要能关掉卡片（同一个出口，内部调 `closeBoardCard()`）。

D6. `#btnMoreImport3d` / `#btnMoreReview` 的 `onclick` 不变（仍调 `runBoardView("import3d" / "review")`），
改由 D4 决定呈现方式 —— 入口不新增第二套。

## 6. 契约 E：零件详情不再内嵌版本面板

E1. 删除 `#partDetailVersions` 槽位（`app.js:2090`）与 `selectPart()` 里搬运 `#secVersions` 的整段
（`:2093-2107`）：零件详情只保留「零件信息 / 识别依据 / 2D 与下载 / 参数 / 零件子动作」。
`#secVersions` 留在抽屉里，由 D 的卡片承载。

E2. `loadVersions()` 的调用点保持既有（数据仍来自同一个 `/versions`），不因本批丢失。

## 7. 明确不做

- 不动 `#ocFilesDock`（2.1 自己的悬浮任务文件小窗）与它的接口；
- 不动 `renderBoardFiles()` 的数据来源与 `boardFileManifest`（只换呈现位置）；
- 不动 `report` 视图的工作区呈现（它不是本批的三项之一）；
- 不动零件详情里的 2D 工程图 / 下载链接（`lowerHtml`）；
- 不动 `.report-btn` 的全局规则、不动其它页面（2.2 / 2.3 / 报价）的任何按钮；
- 不动后端任何接口与表单字段含义；不删任何历史数据；
- 不改 `#ocDrawer` 既有抽屉行为（卡片是第三个独立外壳，不复用抽屉的 DOM）。

## 8. 验收标准

1. `tests/test_tech_part_detail_chrome_and_action_cards_red.py` 全绿；
2. 全量 `unittest discover` 全绿；
3. `node --check tech_app/frontend/inline-analysis.js`、`node --check tech_app/frontend/app.js` 通过；
4. `git diff --check` 干净；
5. 无头 Chrome 实测：工艺推荐面板头部只有一行按钮（生成 / 编辑 / 返回零件详情），无说明输入框、
   无「选择补充文件」；`#btnBoardBackList` 与 `#analysisPanel` 里的「返回零件详情」都不存在；
   `更多功能 ▾` 与「编辑」计算样式同族；打开已有项目后 `#btnMoreImport3d` / `#btnMoreReview` 不再 `disabled`；
   点这两项与左侧「任务文件」都出居中卡片，`#modelPanes` 未被隐藏、工作区内容不变。

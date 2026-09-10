# 技术工艺 Agent 能力恢复第 7 步：修复 2.1 Agent 动作 Spec

## 范围

本步只把 **2.1 图纸解析**的 Agent 动作接通到既有后端与既有看板实现：

- RequestParse、GetProjectState、ListParts、GetPartDetail、GetOpenQuestions、
  UpdatePartParameters；
- 零部件库 / 工艺库 / 成本库查询；
- 重新检索零部件库；
- 型号核验、校验修正。

不新增后端能力，不重写解析、库检索或报告逻辑，不动 1.1–3.3 的其它阶段。

## 1. 后端零改动

- `tech_app/backend/**` 不改：平台工具（`services/oc_agent.py` 的 `PLATFORM_TOOL_SCHEMAS`）、
  `UI_ACTION_TOOLS` 映射、路由集合、service 与数据结构保持原样；
- 只读工具（GetProjectState / ListParts / GetPartDetail / GetOpenQuestions /
  LookupComponentLibrary / LookupProcessLibrary / LookupCostLibrary）必须继续由后端实现，
  前端不得复制一份等价逻辑；
- 已有的 UI 动作名 `parse` / `refresh-ir` / `refresh-integration` / `integration-step`
  不得改名或删除。

## 2. 看板侧：把 2.1 业务动作注册进 `TechBoardRuntime`

`tech_app/frontend/app.js` 的 `registerActions` 至少包含：

| 动作名 | 语义 | 复用 |
| --- | --- | --- |
| `parseDrawing` | 开始/重新解析 | 已有 `parseDrawing()` |
| `modelLookup` | 型号联网核验 | 既有 `#btnModelLookup` 处理 |
| `verify` | 校验修正 | 既有 `#btnVerify` 处理 |
| `searchComponents` | 重新检索零部件库 | 既有 `/component-match` 检索 |
| `refreshData` | 重播解析摘要 | 已有 `publishResultSummary()` |

硬性要求：

- 先把既有 `$("btnModelLookup").onclick = async () => {...}`、`$("btnVerify").onclick = ...`
  这类内联实现抽成具名函数 `runModelLookup()` / `runVerification()`，**按钮绑定与注册动作调用
  同一个函数**；
- 零部件库检索现在是 `agent-chat.js` 里的 `rematchButton` → `POST /api/projects/{id}/component-match`。
  本轮必须把这套检索抽成看板可调用的具名实现 `runComponentMatch()`（复用既有接口与既有轮询），
  注册为 `searchComponents`；不得复制第二份检索/打分逻辑；
- 每个动作的 `run` 返回 `{ ok }` 或结构化 `{ ok:false, error }`，与既有动作一致；
- 动作完成后看板要重播 `result-summary`，左侧入口的数量与可用态随之刷新。

## 3. 左侧会话：统一父壳里只经桥发动作

`tech_app/frontend/agent-chat.js` 在 `inUnifiedWorkbench === true` 时：

- `RequestParse` / `ui_action === "parse"` → `TechBoardBridge.executeAction("parseDrawing")`（已有）；
- `UpdatePartParameters` / `ui_action === "refresh-ir"` → **本轮结束时经桥**刷新看板
  （现在是 `window.dispatchEvent("cad-engine:workbench-chat-edit")`，事件发在父窗口，
  看板 iframe 收不到）；
- `LookupComponentLibrary` 或用户要求重新检索 → `executeAction("searchComponents")`；
- 型号核验 / 校验修正 → `executeAction("modelLookup")` / `executeAction("verify")`；
- 上述路径都不得在统一父壳里直接 `fetch` 业务接口（`/model-lookup`、`/verify`、
  `/component-match` 等），也不得读 `contentDocument`；
- 旧 2.1 独立页保持原行为不变。

### 3.1 左侧入口：视图入口与业务动作入口分开走

左侧 `＋` 能力菜单按语义分成两类，**两种入口不能混用同一条协议**：

| 类型 | 例子 | 走的桥接口 |
| --- | --- | --- |
| 视图入口 | `upload` / `evidence` / `import3d` / `review` / `parts` / `questions` / `report` / `files` | `TechBoardBridge.navigateView(view)` |
| 业务动作入口 | `modelLookup`（联网核验）、`verify`（校验修正） | `TechBoardBridge.executeAction(action)` |

- `tech-workbench.html` 的 `＋` 菜单新增两项 `data-tech-capability="modelLookup"` /
  `"verify"`，文案为「联网核验」「校验修正」，`role="menuitem"`；
- `agent-chat.js` 用一张具名映射表把 capability 名分派到 `navigateView` 或
  `executeAction`，不允许把动作名误发给 `navigate-view`（会得到 `unknown-action`）。

## 4. 「开始解析」闭环

用户说「开始解析」后：

1. 左侧出现工具卡（`tool_use`，含 `RequestParse`）与任务进度（`agent:task-progress`）；
2. 右侧看板执行既有解析流水线（`parseDrawing` 动作），期间 `task-progress` 经协议推给父壳；
3. 解析完成，看板发 `task-completed` 并重播 `result-summary`；
4. 左侧出现「零件清单 / 待澄清问题 / 解析报告」按钮与数量；
5. 点击这些按钮只发 `navigate-view`，内容始终在右侧看板内部展开。

## 5. 边界

- 父壳 `tech-workbench.html` / `tech-workbench.js` 不得新增零件详情、3D/2D、参数、工艺、
  成本、库检索明细的 DOM、Drawer、Modal；
- 不新增第二套解析、库检索、成本或报告算法；
- 不改九个 stage id 与 URL，不改历史会话与项目数据。

## 6. 验收标准

1. 看板注册 `parseDrawing` / `modelLookup` / `verify` / `searchComponents` / `refreshData`，
   且与页面上既有按钮共用同一份实现；
2. 左侧把 4 类 Agent 事件映射到上述动作，且在统一父壳里不直接调业务接口；
   `＋` 菜单新增的联网核验 / 校验修正走 `executeAction`，视图入口继续走 `navigateView`；
3. 零件参数编辑在本轮结束时确实刷新右侧看板（经桥，不再依赖同窗口事件）；
4. 解析完成后左侧结果按钮出现，点击只导航看板视图；
5. 平台工具与路由集合不减少，`UI_ACTION_TOOLS` 动作名不变；
6. 父壳没有新增业务 DOM / Drawer / Modal。

## 7. 对应测试

`tests/test_tech_drawing_agent_actions_red.py`

# 技术工艺 Agent 能力恢复第 17 步：统一结构化 UI 事件 tech_ui Spec

## 范围

为技术工艺实现与报价 `cpq_ui` 同构的结构化 UI 事件协议 `tech_ui`：**Agent 只表达意图，
业务看板按固定结构渲染**，模型不得任意生成字段或 HTML。

支持的语义（固定枚举，不得增删）：

| action | 语义 | 落点 |
| --- | --- | --- |
| `focus_view` | 聚焦右侧看板的某个视图 | `TechBoardBridge.navigateView(view)` |
| `refresh_view` | 让右侧看板重新拉取并渲染 | `TechBoardBridge.executeAction('refreshData')` |
| `fill_fields` | 把白名单字段回填到当前步骤表单 | 看板既有 `fill_*` / 表单动作 |
| `select_part` | 在看板内选中/打开某个零件 | 看板既有 `selectPart` / 零件视图动作 |
| `show_result_actions` | 展示结果入口（零件 / 问题 / 报告…） | 既有 `#ocResultActions` + 看板 `result-summary` |
| `show_progress` | 展示任务执行进度 | 既有 `#ocTaskProgressHost` + 看板 task-* 事件 |
| `set_stage` | 切换九个 stage 之一 | 父壳既有 `applyStage`（stage 白名单） |
| `request_confirmation` | 请求用户确认后再执行某个动作 | 固定确认卡片；用户点击才执行 |

硬要求：

- `tech_ui` 入参只允许固定 action + 白名单参数；未知 action / 未知 stage / 未知 view /
  未知字段一律拒绝，且**不得**产生任何 UI 事件。
- 模型**不能**提供 HTML、CSS、脚本或任意字段定义：schema 里不存在 `html` / `raw` /
  `fields` / `columns` 这类自由结构，服务端也不拼接 HTML。
- 所有渲染都交给既有的看板桥与既有宿主；前端不得把模型字符串当 HTML 注入。

## 1. 后端（`tech_app/backend/services/oc_agent.py`）

- `PLATFORM_TOOL_SCHEMAS` 追加 `tech_ui`，`input_schema.properties.action.enum` 必须正好是上表八项；
  参数仅为：`action`、`stage`、`view`、`fields`（对象，键值均为字符串）、`part_id`、
  `note`、`label`、`target`。不得出现 `html` / `raw` / `script` / `columns`。
- 新增 `_handle_tech_ui(tool_input) -> str`：
  - 校验 `action` 在白名单内；`stage` 在九阶段白名单内；`view` 在白名单内；
    `fields` 只允许对象且值转字符串；越界返回可读错误、**不入队**；
  - 归一化后 append 到线程本地队列 `_tech_ui_events()`（与报价 `cpq_ui` 同构，线程隔离即会话隔离）；
  - 返回简短回执（中文），不回显 HTML。
- `stream_turn` 在 `conv._execute_pending_tools()` 之后把队列里的归一化事件以
  `emit({"type": "tech_ui", "tech_ui": ev})` 下发，随后清空队列；`tool_use` 事件对 `tech_ui`
  只透传轻量元信息（不含整份 fields）。
- `UI_ACTION_TOOLS` **不得**映射 `tech_ui`（它走自己的 `tech_ui` 通道，不参与 `ui_action` 自动分派）。
- 不新增 `@app.` 路由；既有工具与路由一个都不能少。

## 2. 前端（`tech_app/frontend/agent-chat.js`）

- 新增固定映射 `TECH_UI_ACTIONS`（八项），`runTechUi(event)` 按 action 分派：
  - `focus_view` → `TechBoardBridge.navigateView(view)`；
  - `refresh_view` → `TechBoardBridge.executeAction('refreshData', {...})`；
  - `fill_fields` → 当前 stage 的看板字段回填动作（经桥，具体动作由映射表给出）；
  - `select_part` → `TechBoardBridge.executeAction('selectPart', {part_id})`；
  - `show_result_actions` → 更新既有 `#ocResultActions`；
  - `show_progress` → 走既有 `renderTaskProgress` / `#ocTaskProgressHost`；
  - `set_stage` → 交给父壳既有切步通道（`cpq:tech-agent:set-stage` 或 `applyStage`），stage 必须在九阶段白名单内；
  - `request_confirmation` → 渲染**固定确认卡片**（文案 + 确认/取消），只有用户点击“确认”才执行 `target`。
- `handleEvent` 增加 `event.type === "tech_ui"` 分支调用 `runTechUi`。
- 模型提供的字符串只能经 `textContent` 等安全方式呈现；不得 `innerHTML = 模型字段`。

## 3. 边界

- 不新增 / 删除路由与既有工具；不改 `UI_ACTION_TOOLS` 既有映射；
- 不经模型生成字段集 / 列定义 / HTML；未知 action 与越界参数必须被拒绝且无副作用；
- 父壳不建业务 Drawer / Modal；确认卡片是全局消息类交互，放在会话流内，不覆盖右侧看板；
- 不改九个 stage id 与 URL，不动历史会话与项目数据。

## 4. 验收标准

1. `tech_ui` 在 `PLATFORM_TOOL_SCHEMAS`，action 枚举正好八项，且无 `html` / `raw` / `columns` 自由结构；
2. `_handle_tech_ui` 存在并被 `_run_platform_tool` 分派；校验九阶段白名单与视图白名单，越界不入队；
3. `stream_turn` 下发 `{"type": "tech_ui", ...}` SSE 帧并清空队列；
4. `UI_ACTION_TOOLS` 不含 `tech_ui`；
5. 前端 `TECH_UI_ACTIONS` 覆盖八项，`handleEvent` 处理 `tech_ui` 事件；
6. `focus_view` / `refresh_view` / `select_part` 经 `TechBoardBridge` 触发；`show_result_actions` / `show_progress` 复用既有宿主；
7. `set_stage` 只接受九个 stage id；`request_confirmation` 必须用户点击才执行；
8. 无模型字符串 `innerHTML` 注入；无新增 `@app.` 路由；既有工具与路由不减少。

## 5. 对应测试

`tests/test_tech_ui_protocol_red.py`

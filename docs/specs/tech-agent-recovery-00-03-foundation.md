# 技术工艺 Agent 能力恢复 0–3：后端基线、守护、跨层协议与动作注册 Spec

## 范围

本 Spec 只定义能力恢复路线的第 0–3 步，不恢复具体步骤按钮，不新增需求解析、成本或报告 Agent 工具：

0. 冻结技术工艺现有后端能力清单；
1. 建立“后端不得缩水”的自动化守护；
2. 建立统一父壳与右侧 iframe 看板之间的标准消息协议；
3. 用业务动作注册表替代父壳对 iframe DOM/CSS selector 的直接操作。

后续 1.1–3.3 的 Agent 接线必须建立在这四步之上。

## 0. 冻结现有后端能力

### 0.1 基线范围

以下能力已经存在，统一页面和 Agent 改造不得删除、改为空实现或另造一套绕开：

- 身份与用户：登录、注册、当前用户、用户管理和角色；
- 项目与资料：项目、源图纸、附件、任务文件、项目管理；
- 图纸与零件：2D/3D 解析、IR、零件树、零件详情、参数编辑、零部件匹配；
- 工程输出：型号核验、校验修正、拆解、CAD、2D 工程图、BOM、版本与审签；
- 单件分析：工艺库查询、工艺推荐/编辑、成本库查询、成本测算/编辑；
- 整机整合：整合图纸、参数推荐/维护/确认、组装工序生成/维护/确认、整机成本；
- 成本复核：零件成本、组装成本、汇总、确认、写入物料、退回工艺、发送报价；
- 工艺专题：材料、制造、清洗、装配、生产及其推荐、维护、确认、计时；
- 需求流程：需求单、文档提取、AI 检查、客户信用、提交确认、确认、退回、审核；
- 报告流程：准备、维护、提交审核、审核、分发设置、发布、版本；
- 工作流与审计：workflow、task、audit、history/version；
- Agent：meta、send、new、settings 及已有平台工具。

### 0.2 基线表示

测试中的 `REQUIRED_ROUTES` 和 `REQUIRED_AGENT_TOOLS` 是本轮冻结的最小公开能力集，不是“允许保留的上限”。以后可以增加能力，但不能删除这里的条目。路由改名必须先提供兼容路由并更新迁移说明，不能让前端或历史会话直接断裂。

## 1. 后端不得缩水守护

- 守护测试必须直接读取 FastAPI 路由声明和 Agent 平台工具 schema，不能依赖启动生产服务。
- 关键路由必须仍绑定具名函数；禁止用统一的“未实现”、恒定空对象或 HTTP 501 替换。
- Agent 的写操作必须继续调用现有 store/service/API 所使用的业务实现，不得复制解析、工艺、成本、审批、发布算法。
- 不删除历史项目、会话、任务、附件、版本、审计、设置或数据库记录。
- 后续每个实现批次都必须同时运行守护测试和全量测试。

## 2. 父壳—看板标准消息协议

### 2.1 所有权

- `tech-workbench.html/js` 父壳拥有：左侧导航、唯一 Agent 会话、顶部流程、全局设置/消息/账户和跨阶段路由。
- iframe 看板拥有：步骤表单、表格、零件清单、零件详情、3D/2D、工艺、成本、报告以及这些业务视图的内部导航。
- 父壳不得通过 `frame.contentDocument.querySelector(...)`、按钮 id 或 CSS selector 操作业务 DOM。
- 业务详情不得搬到父层 Drawer/Modal。点击零件后必须在右侧看板内部展开。

### 2.2 协议模块

建立两个职责清晰的共享模块（文件名作为契约）：

- `tech-board-bridge.js`：父壳端，请求动作、导航或刷新，接收看板状态；
- `tech-board-runtime.js`：iframe 端，校验命令、分派已注册动作、回传状态。

父壳和九个阶段页面只引用模块，不各写一套 `postMessage` 解析器。

### 2.3 消息封装

所有消息使用命名空间 `cpq:tech-board`，并含：

```json
{
  "namespace": "cpq:tech-board",
  "version": 1,
  "type": "command | state | result",
  "requestId": "每次命令唯一",
  "projectId": "当前项目",
  "stage": "九阶段白名单之一",
  "name": "动作或事件名称",
  "payload": {}
}
```

- 只接受 `event.source === 当前 iframe/window.parent` 且 `event.origin === location.origin` 的消息；不得使用 `"*"` 发送。
- 父壳校验消息的 projectId 与当前项目一致、stage 与当前 stage 一致。
- iframe 校验 stage 白名单和已注册动作；未知动作返回结构化失败，不静默吞掉。
- 每个命令必须用同一 requestId 返回成功或失败；父壳可以显示 pending、success、error。
- iframe load/stage 切换时发送 `ready` 与当前可用动作快照。
- 允许的基础命令至少包括 `execute-action`、`navigate-view`、`refresh-data`、`select-part`。
- 基础状态至少包括 `ready`、`action-state`、`task-progress`、`task-completed`、`task-failed`、`selection-changed`。

## 3. 业务动作注册表

### 3.1 注册方式

右侧每个阶段通过统一运行时注册业务动作：

```js
TechBoardRuntime.registerActions({
  parseDrawing: async payload => { /* 调用当前页面既有函数/API */ },
  refreshDrawing: async () => { /* 重新读取真实数据 */ }
});
```

动作名称表达业务语义，不得包含 `#btnParse` 等 DOM selector。父壳与 Agent 只知道动作名。

### 3.2 初始最小注册表

本阶段只要求把当前父壳已有的九阶段主/次动作迁到注册表，不新增业务能力：

- requirement-create：`saveRequirementDraft`、`submitRequirement`；
- requirement-confirm：`confirmRequirement`、`returnRequirementDraft`；
- requirement-review：`submitRequirementReview`；
- drawing：`parseDrawing`；
- process：`runIntegration`、`sendIntegrationToFinance`；
- cost：`runCostReview`、`confirmCostReview`；
- summary：`saveProcessReport`、`submitProcessReportReview`；
- report-review：`approveProcessReport`、`rejectProcessReport`；
- report-publish：`publishProcessReport`。

每个注册动作必须复用现有页面处理函数或既有后端接口。不得用 `.click()` 作为跨层协议；页面内部为兼容旧按钮可以让按钮和注册动作调用同一个具名业务函数。

### 3.3 状态同步

动作注册时同时提供当前 `visible`、`enabled`、`busy`、`label`。页面状态变化后主动发布新快照，父壳底栏和左侧 Agent 快捷按钮都从这份状态渲染，不再定时探测 iframe DOM。

## 验收标准

1. 后端基线守护全部通过，现有能力和 Agent 工具未减少。
2. 父壳不再出现 `contentDocument`、业务 selector 和跨 iframe `.click()`。
3. 九个阶段页面均加载唯一的 `tech-board-runtime.js`。
4. 消息具备 namespace、version、requestId、projectId、stage、同源与 source 校验。
5. 未知动作、错误和超时可见，不静默失败。
6. `STAGE_ACTIONS` 使用业务动作名，不使用 CSS selector。
7. 九阶段现有主/次动作全部注册，并继续命中原业务实现。
8. 本阶段不改变任何业务数据结构，不删除后端、历史数据或旧独立页面能力。


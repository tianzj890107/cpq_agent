# 技术工艺 Agent 能力恢复第 8 步：1.1 创建需求 Agent 动作 Spec

状态：Spec + 红测（已实现）
红测：`tests/test_tech_requirement_agent_red.py`

## 范围

只打通 **1.1 创建需求**：左侧会话发起 → 左侧显示解析进度 → 后端填写需求单 →
右侧看板刷新字段并出现 AI 带入/推荐徽标 → 左侧总结「填了什么、缺什么」。

只新增 Agent 工具与前端接线，**不新增 HTTP 路由、不新增第二套提取算法**。

## 1. 新增平台工具（`tech_app/backend/services/oc_agent.py`）

在 `PLATFORM_TOOL_SCHEMAS` 追加 7 个工具，并在 `_run_platform_tool` 里分派。
每个工具必须复用既有实现，不得新写一套：

| 工具名 | 语义 | 复用 |
| --- | --- | --- |
| `GetRequirementDraft` | 读取当前需求草稿 | `store.load_requirement` |
| `UpdateRequirementFields` | 保存字段（白名单，仅 draft/rejected） | `store.save_requirement` + 审计 |
| `AttachRequirementFiles` | 上传/关联附件（只能由前端已完成的上传触发；工具本身只登记/读取附件清单） | `store.add_attachment` / `store.load_attachments` |
| `ExtractRequirement` | 一键解析需求（只发请求，与 `RequestParse` 同模式） | 触发前端动作 → 既有 `POST /requirement/extract-documents` |
| `GetRequirementTask` | 读取解析任务状态 | `tasks` 既有任务表 |
| `GetRequirementAiFill` | AI 带入 / 推荐 / 置信度 / 仍缺必填 | 读 `data.document_extraction` 与必填校验 |
| `SubmitRequirementConfirmation` | 提交确认 | 与 `POST /requirement/submit-confirmation` 同一份落盘逻辑 |

约束：

- `ExtractRequirement` 与 `RequestParse` 同模式：工具只发请求并回执「已请求」，
  真正的提取由前端看板调用既有 `POST /api/projects/{project_id}/requirement/extract-documents`
  完成 —— 因此**不得**在 `oc_agent.py` 里再写一份提取或落盘逻辑，
  `requirement_extract.extract_requirement_fields` 仍只被该 HTTP 路由调用；
- 只补空字段、不覆盖人工填写内容、不越过 `status in {draft, rejected}` 的限制；
- 写操作必须落审计（`store.audit`）；
- 不新增任何 `@app.` 路由。

## 2. UI 动作映射

`UI_ACTION_TOOLS` 追加：

```
"ExtractRequirement":      "extract-requirement"
"UpdateRequirementFields": "refresh-requirement"
"AttachRequirementFiles":  "refresh-requirement"
```

既有动作名（`parse` / `refresh-ir` / `refresh-integration` / `integration-step`）保持不变。

## 3. 看板侧（`tech_app/frontend/requirement-create.js`）

- 在既有 `TechBoardRuntime.registerActions({...})` 追加 `extractRequirement`，`run` 复用既有
  `rcExtractRequirementFields()`；保留 `saveRequirementDraft` / `submitRequirement`；
- 一键解析必须继续走既有 `POST /requirement/extract-documents`（全文件只允许一处引用），
  不得复制提取或写文件逻辑；
- 解析进度必须经 `TechBoardRuntime` 以 `task-progress` / `task-completed` / `task-failed`
  上报（父壳左侧据此渲染进度卡），不得只在 iframe 内 `window.dispatchEvent`；
- 解析完成后重新渲染表单，AI 带入 / AI 推荐徽标与置信度随之出现（既有 `rcAiBadge`）。

## 4. 左侧会话（`tech_app/frontend/agent-chat.js`）

- 统一父壳里收到 `ui_action === "extract-requirement"` 时，经
  `TechBoardBridge.executeAction("extractRequirement")` 触发看板，不自己调接口；
- 收到 `refresh-requirement` 时经桥刷新看板；
- 工具结果里的 `document_extraction`（`filled_fields` / `recommended_fields` /
  `recommendation_confidence` / `recommendations`）要能在左侧渲染成「需求解析摘要」：
  列出 AI 带入字段、推荐字段及置信度、仍缺失的必填项；
- 左侧不得出现 `extract-documents` 调用或第二份字段提取逻辑。

## 5. 闭环

1. 用户在 1.1 左侧说「解析需求」；
2. 左侧出现 `ExtractRequirement` 工具卡与解析进度卡（协议 `task-progress`）；
3. 看板调用既有 extract-documents 任务，后端只补空字段写回需求单；
4. 看板重新渲染表单，右侧字段刷新、AI 带入/推荐徽标出现；
5. 左侧给出摘要：带入了哪些、推荐了哪些（含置信度）、还缺哪些必填；
6. 「提交确认」仍走既有 `submitRequirement` / 既有确认接口，Agent 不代替人工审批。

## 6. 边界

- 不新增/删除路由与既有工具；`tech_app/backend/storage`、数据模型不改；
- 不覆盖人工填写的字段，不越权修改客户信用等级等受限字段；
- 父壳不新增需求表单 DOM（表单始终在右侧看板 iframe 内）；
- 不改九个 stage id 与 URL，不动历史会话与项目数据。

## 7. 验收标准

1. 7 个新工具都在 `PLATFORM_TOOL_SCHEMAS` 且都被 `_run_platform_tool` 分派；
2. `ExtractRequirement` 走「工具只发请求 + 看板调用既有 extract-documents 接口」的模式，
   `def extract_requirement_fields` 仍然只存在于 `services/requirement_extract.py`，
   且 `oc_agent.py` 里没有第二份提取实现；
3. `UI_ACTION_TOOLS` 出现 `extract-requirement` / `refresh-requirement`，旧动作名不变；
4. 看板注册 `extractRequirement` 并复用 `rcExtractRequirementFields`；
5. 解析进度经 `TechBoardRuntime` 上报 `task-progress`；
6. 左侧由 `extract-requirement` 触发看板动作，并能渲染需求解析摘要；
7. `requirement-create.js` 里 `extract-documents` 只有一处引用；
8. 既有 14 个平台工具与全部既有路由不减少。

## 8. 对应测试

`tests/test_tech_requirement_agent_red.py`

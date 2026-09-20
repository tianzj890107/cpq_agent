# 技术工艺 Agent 工具轨迹：业务行 + 可展开详情 Spec

状态：TDD Red，等待 DeepSeek 实现。

## 背景与目标

第 21 步对照表（`docs/specs/tech-agent-recovery-21-quote-parity.json` 的 `tool_trace` 行）只要求
技术侧“有工具轨迹”，并未规定形态。两侧现在的形态差得很远：

- 技术侧 `tech_app/frontend/agent-chat.js`：`addToolCard()` 把**原始平台工具名**（`ListParts`、
  `tech_ui`、`mcp__…`）当作卡片主标题，`toolSubtitle()` 把入参 JSON 铺在副标题，
  `setToolResult()` 把整份工具返回**常驻**渲染成 `<pre class="oc-tool-result">`。
  业务用户直接看到实现细节，长会话里结果块会淹没正文。
- 报价侧 `确认需求解析结果.html`：`addToolActivity()` + `describeTool()` 只给**一行中文业务描述**
  （可选「· 来源：…」），`tool_result` 只 `console.warn`，排障时看不到返回值。

本批把技术侧改成混合形态：**主行对齐报价侧「业务行」，术语与载荷收进默认折叠的详情**。
既不让业务用户读 JSON，也不丢掉可审计性，并且不减少任何既有能力。

## 产品契约

1. 主行（默认可见）：图标 + **中文业务文案** + 可选参数摘要 + 状态位；密度对齐报价侧业务行。
2. 主行文案必须来自“工具名 → 中文”映射表；主行**不得**出现原始工具名（`ListParts`、`tech_ui`、`mcp__…`）。
3. 状态语义不变：等待 `◌`（旋转）、成功 `✓`（`#16a34a`）、失败 `⚠`（`#dc2626`）。
4. 主行带一个「详情」开关（原生 `<details>` / `<summary>`），**默认折叠**。
5. 详情内依次给出：原始工具名、入参 JSON（截断）、工具结果 `<pre class="oc-tool-result">`。
6. 结果能力不回退：4000 字截断、`max-height: 240px` 可滚动、失败红字 `.err`。
7. 映射表与 `toolTraceLabel()` 是**纯展示**：不发请求、不调看板桥、不新增路由、不写业务分支。
8. `tech_ui` 没有业务含义单一的工具名，必须按 8 个固定 action 各自给中文文案。

## 名称与结构契约

实现必须落在 `tech_app/frontend/agent-chat.js` / `agent-chat.css`，并使用下列名字（Red 测试按此断言）：

- `const TOOL_TRACE_LABELS = { 工具名: "中文文案", ... }`：**必须覆盖 `tech_app/backend/services/oc_agent.py`
  里声明的每一个平台工具**（当前 63 个，`tech_ui` 除外）。
- `const TOOL_TRACE_UI_ACTIONS = { action: "中文文案", ... }`：覆盖
  `focus_view` / `refresh_view` / `fill_fields` / `select_part` / `show_result_actions` /
  `show_progress` / `set_stage` / `request_confirmation`。
- `function toolTraceLabel(name, params)`：返回 `{ title, subtitle }`；`tech_ui` 走 action 表；
  未登记工具回退成含「调用」+ 工具名的中文行，绝不原样显示英文名。
- `function addToolCard(ctx, event)`：主行用 `toolTraceLabel()` 的 `title`（元素类名为 `oc-art-name`）+
  `subtitle`（`oc-art-sub`）+ 图标 `oc-atile` + 状态位 `oc-art-state`；详情块 `oc-art-detail`
  （原生 `<details>`，无 `open`），内含 `oc-art-raw`（原始工具名）、`oc-art-input`（入参 JSON）、
  `oc-tool-result`（结果 `<pre>`）。
- `function setToolResult(ctx, event)`：改为写详情内那一个 `<pre class="oc-tool-result">`，
  保留 4000 字截断、`is_error` 红字与 `.err` 类。
- `agent-chat.css` 新增 `.oc-art-detail` / `.oc-art-detail summary` / `.oc-art-raw` / `.oc-art-input`
  规则；`.oc-art` 允许换行（`flex-wrap: wrap`）以容纳详情块；`.oc-tool-result` 规则保持不变。

## 范围

允许修改：

- `tech_app/frontend/agent-chat.js`（仅工具轨迹渲染）；
- `tech_app/frontend/agent-chat.css`（仅工具卡相关规则）；
- 引用这两个静态资源的页面缓存版本参数：`agent-chat.js?v=` 在 `tech-workbench.html`、`index.html`；
  `agent-chat.css?v=` 另加 `assembly-integration.html`、`cost-review.html`；
- 当周 changelog。

## 禁止事项

- 不改后端工具 schema、工具名、`oc_agent.py` 的工具清单与 `tech_ui` 校验；
- 不改工具结果内容与 4000 字截断上限，不把结果改写成摘要或隐藏失败原因；
- 不新建第二套工具注册表 / 路由 / 业务接口，不在映射表里 `fetch(` 或调 `TechBoardBridge.executeAction`；
- 不动普通 AI 消息、用户主色气泡、任务卡、确认卡、错误行与 `tech_ui` 八个 action 的既有行为；
- 不改 `.oc-tool-result` 的既有效果，不删除 `.oc-art` / `.oc-atile` / `.oc-art-name` / `.oc-art-sub`；
- 不为迁就实现修改本 Spec 或 Red 测试；
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

## 验收标准

1. `oc_agent.py` 里每个平台工具都有中文标签；代表性工具命中业务关键词（零件 / 工艺 / 成本 / 解析 / 审核 / 发布…）。
2. 标签全部为中文字符串，且不等于工具名本身。
3. 8 个 `tech_ui` action 各自有中文文案，`toolTraceLabel()` 会用到它。
4. 工具卡主行文案来自 `toolTraceLabel()`，不把 `event.name` 塞进 `oc-art-name`。
5. 主行带默认折叠的 `<details class="oc-art-detail">`，内含原始工具名、入参 JSON 与结果 `<pre>`。
6. 4000 字截断、`◌ / ✓ / ⚠`、`#16a34a` / `#dc2626`、`.oc-tool-result.err` 全部保留。
7. 新增 CSS 规则存在且 `.oc-art` 可换行；`.oc-tool-result` 仍是 240px 可滚动等宽块。
8. 映射表与 `toolTraceLabel()` 内没有 `fetch(` / `/api/` / `XMLHttpRequest` / `executeAction`。
9. 未登记工具回退到含「调用」的中文行。
10. `agent-chat.js` / `agent-chat.css` 的 `?v=` 在两个 / 四个引用页保持一致并已更新。
11. `node --check tech_app/frontend/agent-chat.js` 通过。
12. 新增 Red 测试转绿，且既有工具卡、`tech_ui`、消息与看板相关测试继续通过。

## 对应测试

`tests/test_tech_tool_trace_business_line_detail_red.py`

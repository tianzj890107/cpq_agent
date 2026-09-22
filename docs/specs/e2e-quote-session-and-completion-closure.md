# 报价会话恢复与“有价才完成”闭环 Spec

状态：Spec + 红测（已实现）
红测：`tests/test_e2e_quote_session_completion_red.py`

## 1. 线上证据

精准报价会话 `d6088e377375` 从技术工艺返回后，首页卡片 `BJD20260921-D608`
能够进入第 3 步；刷新或从新页面重新打开时，却可能回到空白第 1 步，并显示电池字段。
同一业务实例因此出现两种状态。回传成本为 `7.2749 元/件`，但第 3 步产品行为 0，
系统仍允许依次确认第 3、4、5、6 步，最终生成 `QUO202609001`，报价明细为 0 行、金额为空，
同时显示“流程完成”。

## 2. 唯一身份与恢复契约

1. 从首页、待办、历史、刷新、复制链接进入工作台，都必须显式携带 `session_id`；工作台不得仅靠
   `sessionStorage` 或“最后打开的会话”定位项目。
2. `session_id → business_case_id → tech_project_id → source_task_id` 必须属于同一业务实例。
   有多个候选时返回可处理的 `409 ambiguous_business_case`，禁止猜测。
3. 卡片标题、项目名、客户、行业、当前步骤、会话历史均从持久化快照恢复。标题不得降级为
   “新建报价”，行业不得回退为电池。
4. 领取回传任务必须幂等；领取后刷新仍停留在相同步骤，不重复插入“开始第 N 步”消息。

## 3. 技术/财务回传载荷

财务回传至少包含：`business_case_id`、`quote_session_id`、`tech_project_id`、`source_task_id`、
`industry=packaging`、`quantity`、`unit_cost`、`currency`、`cost_status`、`gap_count`、
`cost_fingerprint`。报价侧必须把它落为一条可追溯的产品行；不能只把数字写进备注。

存在成本缺口时允许 POC 继续，但产品行必须标记 `provisional=true` 并展示缺口，不得丢失成本。

## 4. 步骤完成门禁

- 第 3 步：至少一条产品行具有正数基础成本，才可确认。
- 第 4 步：至少一条产品行具有可解释的加价结果（允许 0，但必须有规则/人工依据）。
- 第 5 步：报价明细至少一行，数量、币种、单价均有效，总金额可确定性复算。
- 第 6 步：第 5 步指纹未变化且明细非空，才可生成 Word、导入数据库、标记流程完成。
- “强行填满”不能绕过上述业务门禁。无依据时应保留当前步骤并给出修复入口。

## 5. UI 契约

首页和待办卡片展示真实标题、报价编号、当前阶段和来源任务号。第 6 步若无明细，展示阻断卡，
不得出现“已确认”“流程完成”或可用导出按钮。

## 6. 验收

同一链接连续刷新三次，行业、步骤、消息数、表单内容一致；财务成本能在第 3 步产品行看见；
空产品行不能越过第 3 步；最终报价至少一行且 `总金额=数量×折后价`。


## 7. 实现记录（`## 278`）：身份进 URL、门禁落服务端、成本回传结构化

### 7.1 会话身份只认 URL + 卡片（§2.1）

- `报价首页.html`：新增 `pageWithSession(page, sessionId)`；`openSession()` 打开历史/卡片时
  `window.location.href = pageWithSession(src.page, id)` —— 报价工作台的 URL 从此显式带
  `?session_id=`，`sessionStorage['cpq:openSession']` 退化为兼容兜底（仍写，但不再是唯一身份）。
- `确认需求解析结果.html`：`boot()` 先解析 `new URLSearchParams(location.search).get('session_id')`，
  **URL 优先**，其次才是「上次打开的那个会话」；拿到就直接 `openSession(id)`，不再依赖本机临时状态。
- `cpq_agent_server.py`：新增 `validate_business_identity(session_id, *, business_case_id="")` 与
  `GET /api/quote/identity` —— 解析 `session_id → business_case_id → tech_project_id →
  source_task_id`；卡片多张、显式 `business_case_id` 与卡片冲突、或关联多个技术项目时回
  `409 ambiguous_business_case`（**不猜**）；库读不到回 `503 identity_unavailable`（同样不放行）。

### 7.2 完成门禁（§4）：判定的唯一事实源在服务端

- `cpq_agent_server.py`：新增 `quote_step_completion_gate(step_no, data, *, quote_fingerprint,
  detail_fingerprint, business_case_id)`：第 3 步要「≥1 行正数基础成本」、第 4 步要「可解释的加价结果」、
  第 5 步要「明细非空 + 数量/币种/单价有效 + 总金额 = 折后价格 × 数量（±0.01）」、第 6 步要
  「明细非空且第 5 步确认后指纹未变」。每个阻断项带 `code` / `message` / `action` /
  `fixable_by_fill`。列名按**关键字**匹配（业务中文列名由 DA 本体下发，不写死具体列）。
  只读，路由 `POST /api/quote/step-gate`（不 ok 回 409）。
- `确认需求解析结果.html`：`canCompleteQuoteStep(step, data)`（异步，问服务端并缓存判定）、
  `quoteCompletionGate(step)`（同步读缓存）、`quoteDetailFingerprint(rows)`、
  `quoteGateText()`、`quoteGateFillBlockers()`。
  - `confirmStep()`：第 5/6 步确认前 `await canCompleteQuoteStep(...)`，不 ok 就在聊天里给
    阻断原因 + 修复入口并**保留当前步骤**（拿不到判定也一律拦，不静默放行）；
  - `fillStepRecommend()` 改为 `async`，发出用户气泡后**同步**读缓存判定：只有当阻断项
    明确「填表也修不了」（`fixable_by_fill === false`，例如本单根本没有产品行、或明细在第 5 步
    确认后被改过）才停手并把修复入口告诉用户；其余情况照旧可填 —— 填表正是修它的手段，
    否则第 3 步会死锁在「没有正数基础成本 → 不许填 → 永远没有基础成本」。
  - 第 5 步确认成功后记 `LAST_QUOTE_FINGERPRINT`，供第 6 步判「明细没被改过」。

### 7.3 成本回传结构化 + 导出/导入硬校验（§3 / §5）

- `cpq_agent_server.py`：`FINANCE_HANDOFF_FIELDS` + `normalize_finance_handoff(payload, *,
  session_id)`（`tech_project_id` / `unit_cost` / `cost_fingerprint` / `gap_count` /
  `provisional` 等；缺什么进 `missing`，**不猜数**；有缺口或成本未到 → `provisional=True`、
  `cost_status=provisional`）+ `POST /api/quote/cost-handoff`（只读）。
- `确认需求解析结果.html`：`applyTechResult()` 改为 `async`，写入前先调该端点归一：把
  `unit_cost` 落成产品行的**基础成本**（第 3 步门禁认的就是这一列）、带成本指纹；`provisional`
  时明确提示「暂估 + 缺口 n 项」；`unit_cost` 缺失时**不写**并说明缺什么。
- 新增 `assertQuoteExportable()`（明细非空 + 数量/币种/单价有效 + 总金额可复算），
  `exportDocx()` 与 `importQuoteDb()` 在动手前都先过这一关：空明细不能导出 Word、也不能落库。

### 7.4 未做（不在本批契约里，别当成已通）

- 服务端**没有**在 `/wf/card/step-done` 上二次强制门禁（门禁在报价助手的 `/api/quote/step-gate`，
  由前端在推进前调用）：绕过 UI 直接打 `/wf/card/step-done` 仍能落步。要彻底堵死需要把
  `quote_step_completion_gate` 接到卡片步进服务（`cpq_suite_server.py` / `cpq_wf.py`），另立一批。
- 待办/历史/复制链接的其它入口（`/agents/quote` 侧路由）本批未改。

### 7.5 实跑（本机 `./open-claude/.venv/bin/python -m unittest`）

```
tests.test_e2e_quote_session_completion_red           Ran 7 OK（实现前 7 红）
tests.test_quote_tech_unified_tool_list_conversation_red Ran 35 OK（真跑 fillStepRecommend）
tests.test_quote_nonstandard_path_red                Ran 25 OK（applyTechResult 仍是唯一写入点）
tests.test_quote_tech_handoff_button_red             Ran 19 OK
tests.test_quote_agent_emphasis_hover_red            Ran 4 OK
tests.test_quote_home_industry_carryover_red         Ran 28 OK
tests.test_quote_first_project_entry_red             Ran 22 OK
tests.test_packaging_quote_version_persistence_red   Ran 8 OK
tests.test_packaging_quote_close_loop_red            Ran 96 OK
tests.test_quote_packaging_box_selection_red         Ran 20 OK
node --check（确认需求解析结果.html 两段内联脚本）      ALL OK
```

函数级自检（不是测试文件，是真跑一遍判据）：第 3 步空产品行 → `no_product_rows`（不可填）；
有正数价格 → ok；第 5 步坏明细 → `invalid_currency` + `total_not_recomputable`；
第 6 步指纹变化 → `detail_changed_after_step5_confirm`；回传 7.2749/24 缺口 → `provisional=True`。

# Spec：2.3 成本测算页对工艺经理只留「发送给财务」主按钮

## 背景（用户反馈）

「工艺经理在成本测算那一页就不是这些按钮了：确认成本 / 一键测算全部成本 / 写入数据库 /
回传销售经理继续报价 / 提交工艺经理确认，而是『发送给财务』的按钮，并且是主按钮。」

2.3 是财务经理的步骤（`auth.COST_ROLES`）。工艺经理打开这一页时，那五颗按钮本来
就都被 `crReadOnly()` 挡着（点下去只会得到「2.3 成本测算是财务经理的步骤…」），
但他这一页真正该做的事是**把工艺与整机参数交给财务**：这件事今天只在 2.2
（「确认工艺并发送财务」→ `POST /api/projects/{id}/integration/send-to-finance`）
能做，2.3 上没有任何入口。

## 范围

- 前端两处：`tech_app/frontend/cost-review.js`（动作快照与页内渲染）、
  `tech_app/frontend/cost-review.html`（页内按钮节点）。
- 后端、Agent 工具、看板桥协议一字不改：发送财务复用既有
  `POST /api/projects/{project_id}/integration/send-to-finance`
  （`main.py` 的 `MANAGER_ROLES` 闸门 + `services/integration.send_to_finance`
  的参数 / 工艺确认闸门 + `cpq_bridge.send_to_finance` 对外调用）。

## R1 身份分流（唯一判定仍是 `crReadOnly()`）

- 财务经理（`CpqSso.state().canCost` 为真，或角色码命中财务白名单）：保持今天的行为，
  主按钮仍是「一键测算全部成本 → 确认成本」，五颗财务动作照旧可见。
- 工艺经理 / 其他非财务身份（`crReadOnly()` 为真）：五颗财务动作在左侧操作栏
  `visible: false`（仍注册、仍可被 Agent 与看板调用，只是不占用户操作栏），
  唯一可见的主按钮是「发送给财务」。
- 身份还没核对完（`crReadOnly()` 为假）时不缩小财务按钮的可见性；`CpqSso` 核对完
  会派发 `cpq-sso-ready`，看板收到后重绘并重发动作快照，主按钮自动翻面。

## R2 「发送给财务」＝一次性复用既有出口

- 新动作 `sendCostReviewToFinance`：`label: "发送给财务"`、`role: "primary"`、
  `order: 5`、`deferred: true`（对外调用可能慢，先回执再播报）。
- `run()` 走既有 `POST /api/projects/{project_id}/integration/send-to-finance`，
  请求体只用既有的 `IntegrationPublishBody` 字段（`product_name` / `note`），
  收件人留空由报价侧落到默认角色（成本测算＝财务经理），因此不需要在 2.3 重写
  2.2 的派发弹窗（`aiOpenFinanceDialog`），也不新建第二套发送实现。
- 失败照旧给真实原因：闸门没到位时后端 400 的 `detail`（例如「请先在『组装工艺』里点
  『确认组装工艺』」）进看板状态、会话卡片与 toast，并可按左侧「失败重试」重发。

## R3 页内也不再摆那几颗按不动的按钮

- `cost-review.html` 的 `.ai-ops` 增加 `#crSendToFinance`（`ai-op-btn primary`，默认
  `hidden`）。
- `crRenderOps()` 按 `crReadOnly()` 切换：工艺经理侧隐藏 `#crConfirm` / `#crWriteDb` /
  `#crToQuote` / `#crReturn` 并显示 `#crSendToFinance`；财务经理侧相反。
  左栏聊天卡里的 `#crRunAll`（一键测算全部成本）在工艺经理侧同样隐藏。
- `crRenderActions()` 在只读身份下不再渲染「测算未完成的 N 个零件」按钮，改为一句话
  说明成本归财务经理、入口是「发送给财务」。

## R4 能力不缩水

- 五颗财务动作（`runCostReview` / `confirmCostReview` / `writeCostReviewMaterial` /
  `sendCostReviewToQuote` / `returnCostReviewToProcess`）与 `costStep`、
  `refreshCostReview` 继续注册、继续有真实 `run`，Agent 工具与看板桥调用不受影响。
- `/cost-review` 的全部 GET/POST/PUT 路由、`/integration/send-to-finance` 路由、
  `cpq_bridge` 与 `services.integration` 的实现、`auth.COST_ROLES` /
  `crReadOnly()` 的权限判定全部不动。

## 验收

- 红测：`tests/test_tech_cost_process_manager_send_to_finance_red.py`（先红后绿）。
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'` 不新增失败；
  `node --check tech_app/frontend/cost-review.js`；`git diff --check`。
- 浏览器：以工艺经理打开 2.3 —— 左侧只剩「发送给财务」主按钮，页内只剩同一颗主按钮；
  点下去若工艺 / 参数没确认到位，报出后端真实原因；财务经理打开 2.3 行为不变。

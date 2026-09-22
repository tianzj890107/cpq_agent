# Spec：2.3 成本测算结果必须进入 3.1 汇总报告（经济可行性 + 2.3 阶段行）

状态：Spec + 红测（已实现）
红测：`tests/test_tech_summary_report_includes_cost_review_red.py`

## 背景（用户反馈的高风险缺陷）

`tech_app/frontend/summary-result.js` 里 3.1 的评估项「经济可行性」是**写死的**：

- `:20` 的 `srLiveView()` 里 `{item:'经济可行性',status:'待评估',conclusion:'尚未接入可追溯的成本与报价结论。'}`；
- 「二、各环节评估结果汇总」只拼 `2.1 图纸解析` + `srIntegrationStage(steps.integration)`（2.2），
  **没有 2.3**。

于是：财务在 2.3 做完零件成本 / 组装成本 / 合计 / 确认之后，3.1 仍然显示「尚未接入成本
结论」，用户只能手工改写；而后端送审门禁 `report_workflow.content_issues()`
（`report_workflow.py:230`）恰好拦截 `status ∈ {待评估, 需补充}` 的评估项 —— 不改就交不
上去；更糟的是，用户把状态手工点成「可行」但没改那句话时，**错误的占位结论会作为正式
报告内容通过送审**。

已确认的数据链（实测）：

- 后端汇总 `services/summary.py` 的 `aggregate()` **已经**装载 2.3 状态：
  `_LOADERS["cost_review"] = store.load_cost_review`，即 `steps.cost_review`
  （`CostReview`：`confirmed` / `confirmed_by` / `confirmed_at` / `note` / `actions`）。
  成本数字本身在 2.2 的 `IntegrationPlan.cost`（`steps.integration.cost.items/.quantity`），
  2.3 的对外口径由既有 `services/cost_review.summarize()`（`parts_total` / `assembly` /
  `final` / `ready` / `missing`）给出。
- 3.1 保存时由前端 `srRead()` 把页面表格读成 `evaluation_items` / `stage_results`，
  经 `PUT /api/projects/{id}/process-report` 落库 —— 也就是说**占位句就是落库内容**。

## 范围

- 前端：`tech_app/frontend/summary-result.js`（3.1 视图装配）。
- 后端：`tech_app/backend/services/summary.py`（汇总数据补 2.3 成本口径）、
  `tech_app/backend/services/report_workflow.py`（送审门禁的窄口径补强）。
- 不改：`/summary`、`/process-report*` 路由与权限，2.1 / 2.2 两行结论的现有生成逻辑，
  `cost_review` / `cost_model` / `integration` 的成本算法与 2.3 页面。

## 验收要求

- **R1 数字与状态来自 2.3，不在前端各算各的**：`summary.aggregate()` 增加**顶层**
  `cost` 口径（复用既有 `cost_review.summarize()` / `payload()` 与 `integration.load_plan()`），
  至少含 `final.total`（对外整机成本）、`parts_total`、`assembly`、`ready`、`missing`
  与 `review`（2.3 确认状态）。前端只做展示投影，不得在 3.1 里重新求和或重写成本算法。
  - 顶层而非 `steps.cost`：`report_workflow.report_source_payload()` 只摘要
    `device_name / ir / steps / summary`，顶层新键不改变审核依据摘要，避免已有草稿因为
    「上游数据已变化」被无故挡在审核外。
- **R2 经济可行性由 2.3 数据生成**：`srLiveView()` 的「经济可行性」结论必须由 2.3 数据
  推出，且按三种状态区分：
  - 2.3 已确认 → 状态「可行」，结论带出**可追溯的整机成本合计**（含零件/组装口径）与
    确认人/确认时间，且**不含**「尚未 / 暂无 / 未接入」；
  - 2.3 已测算但未确认 → 状态仍「待评估」，结论说明「成本已测算、财务尚未确认」；
  - 2.3 未开始 → 状态「待评估」，结论说明成本测算尚未完成。
  - 占位句「尚未接入可追溯的成本与报价结论。」必须从代码里消失。
- **R3 阶段汇总新增 2.3 行**：在 2.2 之后出现「2.3 成本测算」行，结论同样来自 2.3 数据；
  2.3 已确认时该行**不得**出现「尚未 / 暂无」字样 —— 后端门禁
  `content_issues()` 会因此拦下报告（与 2.2「有结果才进汇总表」同一理由）。
  2.3 未完成时该行不得声称结论成立。
- **R4 门禁补强（窄口径）**：`content_issues()` 增加一条只针对「经济可行性」的规则：
  状态不是「待评估 / 需补充」但结论仍是占位句（如仍含「尚未接入」「未接入」「可追溯」
  「占位」）时，继续阻止送审并提示先刷新 3.1 汇总把 2.3 结论带进来。不得把这条规则扩大到
  其它评估项（避免误伤「暂无重大风险」这类正当表述）。
- **R5 不缩水**：`/summary` 与 `/process-report*` 全部路由、`steps.*` 既有键、
  2.1 / 2.2 阶段行、附件清单、发布设置、报告保存/提交链路一字不少；`cost_review`
  的只读使用，不改 2.3 的任何写路径。

## 验收命令

- 本批红测：`python3 -m unittest tests.test_tech_summary_report_includes_cost_review_red -v`
- 相关既有测试：`python3 -m unittest tests.test_tech_report_publish_agent_red tests.test_tech_cost_review_agent_red tests.test_tech_e2e_scenarios_red -v`
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'`
- 前端语法：`node --check tech_app/frontend/summary-result.js`
- 后端语法：`python3 -m py_compile tech_app/backend/services/summary.py tech_app/backend/services/report_workflow.py`

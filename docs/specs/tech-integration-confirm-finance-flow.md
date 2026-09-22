# Spec：2.2 / 3.3 收口链路修复（作用域泄漏、发送财务主按钮）

状态：Spec + 红测（已实现）
红测：`tests/test_tech_integration_confirm_finance_flow_red.py`

## 背景（用户反馈与要求）

1. 点「确认并进入下一页签」报 **`aiSetTab is not defined`**，随后 **⚠ …超时未响应**；
   「仍要继续」之后仍然走不下去。
2. 新要求（用户）：「组装工艺完成之后下一个主按钮应该是确认工艺并发给财务」。

## 现状（实测，含并行批次已落地的部分）

- `aiSetTab` 的作用域泄漏已由并行批次修好（提升到模块作用域，注册闭包内不再声明）——
  本批**不重复动它**，只在通用护栏里守住「不许再漏」。
- **仍然漏**：同一个注册闭包里还有 `aiStatusState` / `aiAnalyzed` 两个 `const`，
  而闭包外的 `aiConfirmDrawingsAndNext()`（第 653 / 664 行）直接调用 `aiAnalyzed()` →
  点「确认图纸并进入参数推荐」必然 ReferenceError（与 `aiSetTab` 是同一处病灶，只是还没修）。
- **同类第三处**：`report-publish-result.js` 的 `rpRefreshReport` 定义在 `rpRegisterTechBoardActions`
  闭包内，闭包外的 `rpSendReportToSales()`（第 26 行）调用它 → 3.3「⇪ 回传销售经理继续报价」
  必然 ReferenceError。
- 「确认并进入下一页签」的 deferred 与失败卡降噪由并行批次负责（
  `docs/specs/tech-confirm-actions-no-timeout-and-no-failure-cards.md`），本批不重复实现、不覆盖。
- **本批新增**：组装工艺页的主按钮。现状 `confirmProcessAndNext`（确认并进入下一步，order 45）
  在工艺生成后抢走主按钮，`sendIntegrationToFinance`（确认工艺并发送财务）恒为 `aux`（order 20，
  还排在「一键生成组装工艺」之前），且它不是 deferred —— 弹窗选接收人期间同样占着桥的同步回合。

## 范围

- `tech_app/frontend/assembly-integration.js`、`tech_app/frontend/report-publish-result.js`。
- 不动：后端路由与 service、`aiFinanceBlocker()` 的真实前置条件、`aiConfirmStep` /
  `aiParamsFinalize` / `aiOpenFinanceDialog` / `aiRunOp` 的实现与接口、并行批次负责的
  `confirmParamsAndNext` deferred 与 `agent-chat.js` 卡片降噪、被隐藏的旧动作注册名、
  看板动作名与视图名。

## R1 作用域泄漏（把剩下两处补齐）

- R1.1 `aiStatusState()` / `aiAnalyzed()` 在模块作用域各只有一份函数声明，
  注册闭包内不再重复声明同名 `const`；`aiSetTab` 保持并行批次提升后的形态。
- R1.2 `rpRefreshReport()` 提升到 `report-publish-result.js` 模块作用域，
  闭包内的 `rpRegisterTechBoardActions` 与闭包外的 `rpSendReportToSales()` 共用同一份。
- R1.3 通用护栏（防同类回归）：`tech_app/frontend/*.js` 中，注册 IIFE 内部以页面前缀
  （`ai|cr|rp|rr|cf|sr|qr|oc|tp` + 大写字母）声明的助手，不得在 IIFE 外被引用。

## R2 组装工艺页：主按钮 = 确认工艺并发送财务

- R2.1 工艺生成后（`aiTab === 'process' && aiHasProcess()`）唯一主按钮是
  `sendIntegrationToFinance`「确认工艺并发送财务」；`confirmProcessAndNext`
  「确认并进入下一步」退出左侧栏（`visible: false`）—— 2.3 成本测算是财务经理那一步，
  工艺经理在这里只需把任务交给财务，不再单给一颗「去 2.3」。动作注册与实现保留
  （Agent / 内部链路仍按名字调用）。
- R2.2 清单顺序：`一键生成组装工艺(40)` → `确认工艺并发送财务(42)`；
  工艺未生成时主按钮仍是「一键生成组装工艺」。
- R2.3 「确认工艺并发送财务」是**一次点完**：它自己先把组装工艺确认掉再发送，
  不用人先点另一颗按钮。`sendIntegrationToFinance` 改 `deferred: true`：`run()` 只做同步闸门
  （`aiFinanceBlocker()`）并启动后台链路 `aiSendToFinanceInBackground()`，立即回执；
  人在弹窗里选接收人不再算进桥的 20 秒超时。
- R2.4 未确认工艺时点主按钮不是死按钮：链路先走既有 `POST /integration/process/confirm`
  （`aiConfirmStep('process')`）把组装工艺确认掉，确认没通过就带真实原因停下；
  然后才判 `aiFinanceBlocker()`，通过则打开既有 `aiOpenFinanceDialog()`。
- R2.5 后台链路结束自报收尾：成功 `aiSettleIfNeeded('task-completed', 'sendIntegrationToFinance')`，
  真实失败 `aiSettleIfNeeded('task-failed', 'sendIntegrationToFinance', { message })`，
  `finally` 复位 `aiDeferredBusy` 与 `updateActionState('sendIntegrationToFinance', { busy: false })`。

## 验收

- 红测：`tests/test_tech_integration_confirm_finance_flow_red.py`（先红后绿）。
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'` 不新增失败。
- 浏览器：组装工艺生成后左侧主按钮是「确认工艺并发送财务」；点击先确认工艺再弹出接收人选择；
  3.3 点「回传销售经理继续报价」不再抛 ReferenceError。

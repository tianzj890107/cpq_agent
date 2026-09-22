# 「带缺口继续」签字的复用边界与 2.3 对外回传

状态：Spec + 红测（已实现）
红测：`tests/test_tech_cost_confirm_zero_waiver_red.py` `tests/test_tech_waiver_reuse_across_handoffs_red.py`

## 一、要解决的问题

2.2「参数推荐」页点「仍要继续」时，弹窗对用户是这么承诺的：

> 现在继续的话，报价测算单上这几格会是空白，内部确认也按你的签字放行；
> 平台会记下是你签的字，**之后不再按同一批缺口拦你**。

但用户实际遇到的是：**已经在「参数推荐」签过字了，到了「确认工艺并发送财务」这一步，同一批缺口又被问了一遍**；
再往后走（2.3 财务回传销售经理）还会被同一批缺口**第三次硬拦**，并且回传那一步的文案把用户指回
「参数推荐」，等于要求他逐页倒查 —— 这正是签字机制要消灭的行为。

## 二、根因（本次实测）

1. **前端不看已经落库的签字。**
   `tech_app/frontend/assembly-integration.js` 的 `aiFinanceGaps()` 只读
   `status.params_final / params_confirmed / process_confirmed` 与
   `aiRequiredGaps()`，**完全不读 `status.waiver`**。
   后端 `services/integration.py::send_to_finance()` 其实已经会用
   `waiver_covers()` 复用「参数推荐」那一次签字、不再要求第二次签字；
   前端不知道，于是又弹一次「仍要继续」，用户看到的正是
   「还没点确认的环节：「确认参数已齐」」。
   （`params_final` 在签字路径上本来就是 False —— 签字不等于参数已齐，这是设计，不是缺口。）

2. **2.3 对外回传没有接进豁免机制。**
   `tech_app/backend/services/cost_flow.py::integration_send_to_quote_body()` 在
   `missing_required()` 非空时**无条件**抛 `CostFlowError`，既不查
   `integration.waiver_covers()`，也不区分「这批缺口本人签过字」与「新冒出来的缺口」。
   实测：2.2 已签字放行（三项内部确认都被顺带补掉）之后，
   `cost_flow.send_to_quote()` 仍然被同一批缺口拦下：
   `报价必填的成品参数还缺：产品系列、产品型号… 请先在「参数推荐」环节补填并确认`。

## 三、契约

### C1 前端：同一批缺口不得再问第二次

`assembly-integration.js` 新增纯函数 `aiWaiverCoversGaps(waiver, missingCodes, pending)`：
当 `status.waiver` 的 `missing_codes` 覆盖当前缺口编码、且 `waived_confirmations`
覆盖当前待确认项时返回 `true`，否则 `false`（`waiver` 为空一律 `false`）。

`aiFinanceGaps()` 必须调用它，并在返回值里带上 `covered: true|false`。

### C2 前端：「仍要继续」只在缺口**未被覆盖**时才弹

`aiConfirmProcessAndSendToFinance()`：

- `gaps.covered === false` 时：行为不变 —— 弹「仍要继续」，点取消即停、不发送不落库；
- `gaps.covered === true` 时：**不得再弹**「仍要继续」，直接进入既有的接收人弹窗
  （`aiOpenFinanceDialog(waiver)`，waiver 传 `null`，由后端按已落库的签字复用）；
  同时用普通会话输出把缺口如实说出来（谁在什么时候签的字、报价测算单上哪几格仍是空白），
  只持续展示风险，不阻断。

### C3 后端：对外回传（2.3 → 回传销售经理）复用已签字的豁免

`cost_flow.integration_send_to_quote_body()` 仍保留最终完整性检查
（必须继续引用 `product_params.missing_required`），但判定改为两级：

- 当前缺口已被 `integration.waiver_covers()` 覆盖时：**不再抛错**，按已签字放行；
  缺口照样算出来，随返回体一起交出去
  （`status.required_missing` / `status.params_complete` / `status.waiver` 原样保留），
  回传照旧写既有的 `integration_send_to_quote` 审计，放行依据（签字）在返回体里可追溯；
- 未被覆盖（例如签字之后又冒出新缺口）时：仍然抛 `CostFlowError`，消息里要
  逐个点名缺哪些字段，不得放宽成静默通过。

### C4 一律不放宽的

- L1 生成依赖（没有参数推荐 / 没有组装工艺）不可豁免；
- 权限 `_require`、写入数据库、审核、发布、回传幂等键全部保持原样；
- 报价必填字段清单（`quote_product_params.json` 的 16 项）不得降级成选填；
- `record_waiver` 的比对键（字段编码集合 + 已放行的确认项）不变：集合变大要重新签字。

## 四、验收

- `python3 -m unittest tests.test_tech_waiver_reuse_across_handoffs_red -v` 全绿；
- 既有回归：`tests.test_tech_integration_dependency_waiver_red`、
  `tests.test_tech_requirement_stage_waiver_red`、
  `tests.test_tech_cost_report_handoff_continuity_red` 保持全绿；
- 全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 无新增失败。

## 五、不在本批范围

L0–L4 分级在 1.1/1.2/1.3/2.1/2.3/3.1–3.3 的全面铺开、缺口按「计算关键 / 仅展示」分类、
`excluded_parts` 登记、L3 交接清单、用户手填继续原因、「带缺口确认」一等状态与风险常驻角标。

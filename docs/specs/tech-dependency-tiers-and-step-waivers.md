# Spec：步骤依赖分级与「带缺口继续」的缺口豁免（waiver）

## 背景（用户反馈）

2.2「组装与整合」的出口存在逻辑矛盾，用户原话是：

> 参数推荐已确认 / 组装工艺已确认 这些没必要必须啊，不用确认的；转发或者进下一步的时候
> 仍要继续就帮着确认就好了。然后报价必填参数那些很多都没必要，比方说电压电流重量这些
> 东西都填不出来，还有型号什么的都无所谓，仍要继续就让继续就好了。

实测（本次确认）链路确实是「允许你进下一页看看，但最终仍然不能继续流程」：

1. `assembly-integration.js:717` `aiAskProceed()` —— 必填没齐时弹「仍要继续」，人点了就带着
   缺口进入「组装工艺」。
2. `main.py:2480` —— 那条路径只调 `finalize(confirm=False)`，于是 `params_final` 被显式置回
   `False`（"改过参数就不再算已确认"）。
3. `assembly-integration.js:1050` `aiFinanceBlocker()` —— 发送财务时又把
   `required_missing / params_final / params_confirmed / process_confirmed` 四项全部当硬门禁。
4. `services/integration.py:853` `send_to_finance()` —— 后端再执行一遍同样的硬校验。

即：前端那次「仍要继续」**没有留下任何豁免记录**，所以后面每一道关卡都按原缺口重新拦一次，
人只能逐页倒查、被同一个缺口反复挡回。这不是操作问题。

## 统一口径：依赖分级（全流程）

依赖按「能不能被签字放行」分四级，后续各阶段批次都按这张表落地，不再各页各写一套
「请先完成…」：

| 级别 | 含义 | 能否进入下一页 | 能否「仍要继续」 | 典型场景 |
| --- | --- | --- | --- | --- |
| L1 | 生成依赖：没有基础输入就算不出来 | 可以进入 | **不能**绕过 | 没有图纸要解析、没有零件要算成本、**没有参数推荐/组装工艺要发财务** |
| L2 | 质量依赖：有结果但字段不完整 | 可以 | **可以**，必须记录缺口与签字 | 报价必填参数缺失、内部阶段未点确认、待澄清问题未处理 |
| L3 | 交接依赖：跨角色影响下游 | 可以进入 | 部分允许，必须显式签字 + 接收人 | 工艺交财务、成本交工艺、结果交销售 |
| L4 | 合规依赖：审批 / 写库 / 正式对外 | 可以查看 | **不允许**绕过 | 无权限审批、成本未确认写库、报告未审核发布、回传报价 |

硬规则：

- 页面永远可以打开和查看；
- 生成动作只检查「能不能真的算」；
- 内部阶段确认（参数推荐确认 / 组装工艺确认 / 参数已齐）属于 L2，可以带缺口放行；
- 跨角色交接必须有明确接收人，且缺口清单随交接包一起走；
- 写库、回传报价、审核、发布、权限一律不可豁免；
- **已经签过字的缺口，后续不得再拦第二次**，只能持续显示为风险。

## 本批范围（只做 2.2 → 2.3 出口与参数推荐确认）

### 一、后端：豁免记录（waiver）

新增模型（`tech_app/backend/models/integration.py`）：

```
class IntegrationWaiver(BaseModel):
    stage: str                      # 在哪一步签的字：'params' | 'finance_handoff'
    missing_codes: List[str]        # 报价必填缺口（字段编码），同一缺口的比对键
    missing_fields: List[str]       # 同一批缺口的中文名，给人看
    waived_confirmations: List[str] # 顺带放行的内部确认：params_final / params_confirmed / process_confirmed
    reason: str = ""                # 允许为空，服务端补默认原因
    waived_by: Optional[str] = None
    waived_at: Optional[str] = None
    reused: bool = False            # 本次是复用已有签字，而不是重新签一次
```

`IntegrationPlan` 增加 `waivers: List[IntegrationWaiver] = []`（不下沉到 `steps`，不另存文件）。

新增单一实现（`services/integration.py`，路由与 Agent 都复用它，不许第二份）：

```
record_waiver(plan, stage, *, missing_codes, missing_fields, confirmations, reason, actor) -> IntegrationWaiver
waiver_covers(plan, missing_codes, confirmations) -> Optional[IntegrationWaiver]
```

### 二、后端：`send_to_finance()` 依赖重排

| 依赖 | 级别 | 现在 | 本批之后 |
| --- | --- | --- | --- |
| `plan.params is None` | L1 | 拦 | **仍然拦**（不可豁免） |
| `plan.process is None` | L1 | 拦 | **仍然拦**（不可豁免） |
| 报价必填缺口 | L2 | 拦 | 有签字则放行并记录 |
| `params_final` | L2 | 拦 | 放行时**顺带置位**并记 `params_final_by/_at` |
| `params_confirmed` | L2 | 拦 | 放行时顺带置位（同 `_by/_at`） |
| `process_confirmed` | L2 | 拦 | 放行时顺带置位（同 `_by/_at`） |
| 接收人 / 派发方式 | L3 | 弹窗选 | 不变（仍由 `aiOpenFinanceDialog` 选） |
| 权限 `MANAGER_ROLES` | L4 | 拦 | **不变** |
| 写库 / 回传报价 / 审核 / 发布 | L4 | 拦 | **不变** |

行为细节：

- 未签字且存在 L2 缺口 → 仍抛 `IntegrationFlowError`，但消息要**一次列全**缺口并给出可执行
  下一步（沿用现有「报价必填的成品参数还缺：…」措辞，补上未确认项），供前端弹「仍要继续」；
- `waiver={"reason": "..."}`（前端签字）→ 放行：以服务端算出的缺口为准记录
  `stage='finance_handoff'` 的豁免，顺带补齐内部确认，写审计 `integration_send_to_finance_waived`；
- plan 里已有 `stage='params'` 且覆盖同一批 `missing_codes` 的豁免 → **不再要求第二次签字**，
  直接放行，并追加一条 `stage='finance_handoff'`、`reused=True` 的记录；
- 缺口的比对键是**字段编码集合**：签字之后又出现**新的**缺口 → 必须重新签字（不放过）；
- `reason` 为空时服务端写默认原因（形如「带缺口继续：报价必填缺 3 项，已在 2.2 由本人签字放行」），
  保证「仍要继续」是一次点击即可，不强迫人写理由；
- `waived_by` / `waived_at` 必须落库，不允许匿名豁免。

### 三、后端：下游看得见（不得重复拦截）

- `integration.status(plan)` 增加 `params_complete`（报价必填是否齐）与 `waiver`
  （最近一条豁免的摘要，无则 `None`）；既有 `required_missing` / `params_final` 等字段一个不删。
- `cost_review.payload()`（2.3 财务看到的）增加 `waiver` 与 `params_complete`，让财务知道
  「这些缺口是工艺侧签过字的」，不再按同一缺口重新拦。

### 四、前端：签字入口

- `aiFinanceBlocker()` 只判 L1（`has_params` / `has_process`），不再把
  `required_missing / params_final / params_confirmed / process_confirmed` 当硬门禁；
  它仍是这一页唯一的前置判定（多处共用同一份，不许各写一套）。
- 新增 `aiFinanceGaps()` 描述 L2 缺口（缺几项报价必填 / 参数推荐未确认 / 组装工艺未确认 /
  参数未最终确认），用于按钮 title、状态位与「仍要继续」的弹窗正文。
- `aiConfirmProcessAndSendToFinance()`：L1 通过后，若有 L2 缺口 → 复用既有 `aiAskProceed()`
  取得签字；人点「仍要继续」→ 把豁免（`{reason}`）随发送财务请求带上；点「取消」→ 返回结构化
  失败，不发送、不落库。
- 参数推荐页 `aiConfirmParamsAndNext()` 的「仍要继续」路径：把同一批缺口通过**既有**
  `/integration/params/finalize`（body 增加可选 `waiver` 字段）落库，`confirm` 仍为 `false`
  （`params_final` 保持未定稿、`params_complete=false`），这样到发送财务时同一批缺口已经有签字，
  不再弹第二次。
- 发送财务的豁免随既有 `aiRunOp('send-to-finance', dispatch)` → 既有
  `/integration/send-to-finance` 提交，`IntegrationPublishBody` 增加可选 `waiver` 字段。
  不新增任何路由。

## 明确不在本批范围

- 其余阶段（1.1–1.3 / 2.1 / 2.3 / 3.1–3.3）的依赖分级落地：本批只文档化口径，不顺手改九个页面。
- 「3.2 / 3.3 某些状态没有主按钮」：仍属下一步门禁批次。
- Agent 工具 `SendIntegrationToFinance`：不得**自动**豁免（豁免是人的签字）。工具路径继续要求
  `requires_confirmation`，缺口照旧回结构化错误；本批不把 waiver 接进 Agent 自动链路。
- 报价产品参数字典 `quote_product_params.json` 的 `required` 标记：一个字都不改。
  「必填项太多」要靠 L2 豁免解决，不允许把必填偷偷降级成选填来让流程通过。
- 权限、写库、回传报价、报告审核与发布的既有硬门禁：一律不动。

## 验收要求

- **R1 L1 不可豁免**：`params is None` 或 `process is None` 时，即使带 `waiver` 也必须抛
  `IntegrationFlowError`。
- **R2 未签字仍拦**：L2 缺口存在且无签字 → 抛 `IntegrationFlowError`，消息列出缺口。
- **R3 签字即放行**：带 `waiver={"reason": ...}` → 成功返回 plan，且 `plan.waivers` 追加一条
  `stage='finance_handoff'`，`missing_codes` 与服务端算出的缺口一致，`waived_by`/`waived_at` 非空，
  审计写入 `integration_send_to_finance_waived`。
- **R4 顺带确认**：放行后 `params_final` / `params_confirmed` / `process_confirmed` 全为 `True`
  且各自 `_by/_at` 有值，`waiver.waived_confirmations` 如实列出补掉的项。
- **R5 同一缺口不二次拦截**：`stage='params'` 的豁免已覆盖同一批 `missing_codes` 时，
  `send_to_finance()` 不带 `waiver` 也必须成功，并追加 `reused=True` 的记录。
- **R6 新缺口要重新签字**：豁免只覆盖部分缺口 → 仍抛错，且消息里出现未被覆盖的字段名。
- **R7 下游可见**：`integration.status(plan)` 暴露 `params_complete=False` 与 `waiver`；
  `cost_review.payload()` 暴露 `waiver` 与 `params_complete`。
- **R8 前端分级**：`aiFinanceBlocker()` 不再引用 `required_missing / params_final /
  params_confirmed / process_confirmed`；`aiFinanceGaps()` 存在；签发链路复用 `aiAskProceed()`
  且取消时不发送；参数推荐页的仍要继续路径把 `waiver` 交给既有 finalize 接口。
- **R9 不缩水**：既有路由
  （`/integration/params/finalize`、`/integration/params/autofill`、`/params/confirm`、
  `/process/confirm`、`/integration/send-to-finance`、`/integration/material-write`、
  `/integration/send-to-quote`）与动作名、`aiAskProceed` / `aiOpenFinanceDialog` /
  `aiRunOp` / `aiFinanceBlocker` 全部保留；`MANAGER_ROLES` 权限不变；`status()` 既有字段不删。
- **R10 字典与 L4 不动**：`quote_product_params.json` 的必填字段集合与字节内容不变；
  `cost_flow.integration_send_to_quote_body` 的最终完整性检查、报告审核 / 发布、写库门禁不变。

## 验收命令

- 本批红测：`python3 -m unittest tests.test_tech_integration_dependency_waiver_red -v`
  （后端行为用带 pydantic 的解释器子进程真跑；本机为 `open-claude/.venv/bin/python`，
  无可用解释器时该项自动跳过并在报告里说明）
- 被取代断言更新：`tests/test_tech_integration_params_step_ownership_red.py`
  （`test_send_to_finance_requires_final_complete_parameters` → 分级契约）
- 相关回归：`python3 -m unittest tests.test_tech_integration_confirm_finance_flow_red
  tests.test_tech_business_actions_clickable_then_error_red
  tests.test_integration_params_tab_single_primary_and_auto_fill_red
  tests.test_tech_integration_agent_red -v`
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'`

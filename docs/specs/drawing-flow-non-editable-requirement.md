# 规格：需求不可编辑时的 drawing-flow 前置条件与稳定码（第 5 批补充）

> 前置：`docs/specs/drawing-flow-error-taxonomy.md`（错误分类 / 前置条件 / 不得判死整条链路）。
> 红测：`tests/test_drawing_flow_requirement_state_red.py`。
> 本批只修「需求单已提交（不可编辑）」这一条链路上的**分类与可预见性**，
> **不放宽**「已提交的需求不可被静默改写」这条业务铁律。

状态：Spec + 红测（已实现）
红测：`tests/test_drawing_flow_requirement_state_red.py`

## 0. 现场（实测，9-21，34 上两份真实 DWG 的图纸解析链路）

`bf99bec0d274`（酒盒）与 `ce9d5aae9631`（圆盘盒）两条 requirement 的 `status` 都是 `approved`：

- `field_write` 抛 `RequirementSaveError("需求已提交，不能直接修改；请先退回后再编辑", 409)`
  （`requirement_service.py:139`，守卫条件 `status not in EDITABLE_STATUSES`，`EDITABLE_STATUSES=("draft","rejected")`）；
- 该异常**没有** `stable_error_code`，`steps.field_write` 于是把它归成
  `PACKAGING_FLOW_STEP_FAILED`（"其它未识别异常"）→ `retryable=True`：文案是通用的、
  重试永远不会成功；
- `packaging_drawing_flow.preconditions(project_id)` 返回 **`[]`** —— 跑之前完全看不出缺什么；
- 结果：2.1 里字段看板是空的、报价侧拿不到任何字段候选，用户只看到一句「重试」。

这与 `drawing-flow-error-taxonomy.md` §1 记录的三个问题是**同型复发**（码与因无关 / 连真因文案都丢了 /
缺前置条件把链路判死），只是这次缺的前置条件不是「没有需求草稿」而是「需求已提交、当前状态不可写」。

## 1. 契约 C1：业务拒绝必须自带稳定码

1. `RequirementSaveError` 必须携带稳定码属性 `stable_error_code`（构造时可传；未显式传时取默认值）。
2. `requirement_service.save_requirement_draft` 的**每一处** `raise RequirementSaveError(...)`
   都必须显式给码，码取值闭集：

   | 场景 | 稳定码 |
   | --- | --- |
   | 需求存在但 `status not in EDITABLE_STATUSES` | `REQUIREMENT_NOT_EDITABLE` |
   | 其它业务规则拒绝（信用等级非法、越权改信用等级、提交状态不对…） | `REQUIREMENT_SAVE_REJECTED`（默认值） |

3. 判定只认异常自带的码，**不许**用 `str(exc)` 关键字匹配决定码（沿用 taxonomy Spec C1）；
   文案仍 `str(exc)` 优先（沿用 C2），`detail.reason = type(exc).__name__`。

## 2. 契约 C2：前置条件必须在跑链路之前被枚举出来

`packaging_drawing_flow.preconditions(project_id)` 增补一条：

| 情形 | code | severity | message / action |
| --- | --- | --- | --- |
| 需求存在但状态不可编辑 | `REQUIREMENT_NOT_EDITABLE` | `blocking` | message 必须带上**当前状态**（如 `approved`）与「已提交不可直接改写」；action 指向「把需求退回草稿，或为该项目新建一张需求草稿，再重跑图纸解析」 |

- 需求不存在 → 仍报 `REQUIREMENT_DRAFT_MISSING`（既有条目，不许改文案与动作）；
- 需求存在且状态可编辑（`draft` / `rejected`）→ 这两条都不报，返回 `[]`；
- 仍然**只读、幂等、不写库、不建数据、不改需求状态**（taxonomy Spec C3）。

## 3. 契约 C3：`field_write` 必须 blocked、不可重试、且看板不清空

1. `REQUIREMENT_NOT_EDITABLE` 必须登记进 `model.PRECONDITION_BLOCKERS`（含 message / action），
   `model.ERROR_CODES` 登记 `REQUIREMENT_NOT_EDITABLE: (409, False)`；
2. `field_write` 命中该码时返回 `status="blocked"`、`retryable=False`、`error_message` 来自真实异常、
   `detail.action` 非空，且 `fields` 看板照旧有内容（沿用 taxonomy Spec C4 的 blocked 分支行为）；
3. 任何**带稳定码**的 `RequirementSaveError` 都不得落 `PACKAGING_FLOW_STEP_FAILED`；
   `retryable` 取 `model.error_meta(code)[1]`。`REQUIREMENT_SAVE_REJECTED` 登记为 `(409, False)`：
   业务拒绝重试同样不会成功；
4. **没有**稳定码的未识别异常保持现状：`PACKAGING_FLOW_STEP_FAILED` + `retryable=True`
   （不许借这批把"未识别异常"一律改成不可重试——那会把偶发写库故障也判死）。

## 4. 契约 C4：不许绕过、不许放宽

- `requirement_service.EDITABLE_STATUSES` 仍必须是 `("draft", "rejected")`：
  **不许**为了让图纸解析写进去而把 `approved` 放进可编辑集合；
- 不许自动改需求状态、不许自动退回、不许自动新建需求草稿、不许直接调 `store.save_requirement`
  绕过 `save_requirement_draft`（`steps.py` 里不得出现 `store.save_requirement(`）；
- `preconditions()` 不得写任何数据。

## 5. 契约 C5：冻结面

- `STEP_IDS` / `STEP_TITLES` / `STEP_STATUSES` / `STEP_TERMINAL_FAILURES` 不变
  （`blocked` 仍不是终态失败）；
- `GET /api/projects/{pid}/drawing-flow` 的 `{flow, gates, stale, inheritance}` 结构不变；
- `PRECONDITION_BLOCKERS` 里 `REQUIREMENT_DRAFT_MISSING` 的 message/action 不许改；
- 既有 `tests/test_drawing_flow_error_taxonomy_red.py` 不许被放松或修改。

## 6. 非目标

- 不做「自动退回需求」这类业务动作（属人工决策）；
- 不改需求看板 UI 的状态机与提交/审核流程；
- 不改 `packaging_match` / BOM / 工艺 / 成本；
- 不 commit / push / MR / tag / Release / 部署。

## 7. 红测清单（`tests/test_drawing_flow_requirement_state_red.py`）

| 组 | 用例 | 现状（实现前） |
| --- | --- | --- |
| A 稳定码 | A1 `approved` 时 `save_requirement_draft` 抛出的异常带 `stable_error_code=REQUIREMENT_NOT_EDITABLE` 且 409；A2 信用等级非法带非空默认码；A3 AST 扫描：每处 `raise RequirementSaveError(...)` 都必须显式给码；A4 `field_write` 命中该码 → blocked/不可重试/带 action/fields 非空；A5 带 `REQUIREMENT_SAVE_REJECTED` 的异常不许落 `PACKAGING_FLOW_STEP_FAILED` 且不可重试；A6 源锚点：不得用 `in str(exc)` 决定码；A7 未识别异常仍 `PACKAGING_FLOW_STEP_FAILED` + 可重试（回归锚点） | A1/A2/A3/A4/A5 红；A6/A7 绿 |
| B 前置条件 | B1 `approved` → `REQUIREMENT_NOT_EDITABLE`(blocking，message 带状态，action 非空)；B2 `pending_confirmation`/`pending_review`/`approved` 都要报；B3 `draft`/`rejected` 都不报且返回 `[]`；B4 无需求只报 `REQUIREMENT_DRAFT_MISSING`；B5 只读：不得调用 `store.save_requirement`，连调两次结果一致 | B1/B2 红；B3/B4/B5 绿 |
| C 登记与链路 | C1 `PRECONDITION_BLOCKERS`/`ERROR_CODES` 登记正确；C2 `blocked` 不是终态失败 | C1 红；C2 绿 |
| D 不许绕过 | D1 `EDITABLE_STATUSES` 不变；D2 `steps.py` 不得直接写需求；D3 守卫文案仍在 | D1–D3 绿（锚点守卫） |

## 8. 验收标准

```bash
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_requirement_state_red -v
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_error_taxonomy_red -v   # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red -v        # 不回归
```

1. 新红测全绿；
2. 既有 taxonomy 红测与 drawing-flow 红测**逐条不变**地保持通过；
3. 现场口径：对 `status=approved` 的项目调用 `preconditions()` 能看到 `REQUIREMENT_NOT_EDITABLE`，
   且 `field_write` 返回 `blocked`（不可重试）而不是 `failed`。

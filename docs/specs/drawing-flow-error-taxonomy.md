# drawing-flow 错误分类、前置条件与"不得判死整条链路"

Spec 版本：1 · 状态：待实现（红测已就位）

## 1. 背景（实测）

隔离环境跑 34 的 DWG 链路时，文件预检、转换、CAD IR 解析、包装语义识别全部 completed，
最后 `field_write` 报 `REQUIREMENT_SAVE_FAILED`。追到的真实异常是：

```
tech_app/backend/services/packaging_semantics/provenance.py:55
ValueError: 需求单不存在，请先创建需求草稿
```

三点问题：

1. **码与因无关**：`packaging_drawing_flow/steps.py:304-309` 用
   `except Exception` 把所有异常收敛成 `REQUIREMENT_SAVE_FAILED`；
2. **连真因文案也丢了**：`str(getattr(exc, "message", "") or "需求字段写入失败，请重试")`
   —— `ValueError` 没有 `.message` 属性，于是对外只剩通用文案，真因只在日志里；
3. **一条缺前置条件把整条链路判死**：`model.py:28` 的
   `STEP_TERMINAL_FAILURES = ("failed", "unavailable")`，`__init__.py:431-432` 的
   `run_flow` 遇到终态失败即 `break` —— `field_write` 失败后
   `pending_confirm`、`downstream_prepare` 永不执行，`flow.status="failed"`。
   而 `retryable=True`，用户点重试永远不会成功（缺的是前置数据，不是偶发故障）。

业务后果：用户看到"字段写入失败，请重试"，重试无效；实施/客服无法区分"没建需求单"与
"真的写库失败"；下游任务准备被跳过，报价/工艺拿不到东西。

## 2. 目标

错误分类真实、文案保留原因、缺前置条件不判死链路，并且在跑链路之前就能知道缺什么。

## 3. 契约

### C1 异常分类（不得兜底成一个码）

`steps.field_write` 的 `apply_fn` 调用改为分类捕获：

| 情形 | 稳定码 | retryable |
| --- | --- | --- |
| 需求单不存在（`provenance.py:55` 这一类） | `REQUIREMENT_DRAFT_MISSING` | `False` |
| 写库/落盘真的失败 | `REQUIREMENT_SAVE_FAILED` | `True` |
| 其它未识别异常 | `PACKAGING_FLOW_STEP_FAILED`（既有码） | `True` |

判定实现放一处（例如 `provenance.RequirementDraftMissing` 专用异常类型），
不得靠 `str(exc)` 关键字匹配决定码。

### C2 文案必须来自真实异常

`_failed()` 的 message 一律 `str(exc)` 优先，通用文案只做最后兜底；
`detail` 里保留 `reason=type(exc).__name__`。

### C3 前置条件可查询、可在跑之前提示

新增：

```python
packaging_drawing_flow.preconditions(project_id) -> [{"code", "severity", "message", "action"}]
```

- 缺需求草稿 → `code="REQUIREMENT_DRAFT_MISSING"`、`severity="blocking"`、
  `action` 指向创建需求草稿的入口；
- 无缺失时返回 `[]`；
- 只读、幂等、不写库；
- `GET /api/projects/{pid}/drawing-flow` 的响应里带 `preconditions`。

### C4 缺前置条件 = `blocked`，不是 `failed`

- `field_write` 在 C1 的 `REQUIREMENT_DRAFT_MISSING` 情形返回
  `{"status": "blocked", ...}`、`retryable=False`，并带 C3 的 `action`；
- `run_flow` 的 `break` 只对 `failed` / `unavailable` 生效；`blocked` **不阻断**后续步骤，
  `pending_confirm`、`downstream_prepare` 必须跑完（可 `skipped`，但要有终态）；
- `_flow_status()`：含 `blocked` 但无 `failed` 时返回 `completed`（现状
  `("completed","skipped","blocked")` 已支持，需确认不被 `failed` 分支抢先）；
- `flow.fields` 在该情形下不得被清空（字段看板仍要能展示 `missing/pending`）。

### C5 契约不变的部分

`STEP_IDS`、`STEP_TITLES`、`GET /drawing-flow` 的 `{flow,gates,stale,inheritance}` 结构、
其它步骤的错误码与 `STEP_TERMINAL_FAILURES` 的语义均不变。

## 4. 不在本批范围

- 不自动创建需求草稿（业务动作，不做静默补数据）；
- 不改 `packaging_semantics` 的替代/冲突标记逻辑；
- 不改前端（前端展示属 `docs/specs/drawing-flow-frontend-wiring.md`）。

## 5. 验收标准

1. `tests/test_drawing_flow_error_taxonomy_red.py` 全绿；
2. 脚本/API 建的项目（无需求草稿）跑 `run_flow`：`field_write=blocked`、
   `error_code=REQUIREMENT_DRAFT_MISSING`、`flow.status != "failed"`、
   `downstream_prepare` 有终态；
3. 正路（从报价/需求入口建的项目）行为与本批之前逐字一致。

## 6. 同型复发的补充契约（9-21）

需求单**已提交**（`status=approved` 等不可编辑状态）时，`field_write` 会走到同一类
「码与因无关 + 连真因文案都丢了 + 缺前置条件把链路判死 + 重试永不成功」的坑：
异常是 `RequirementSaveError`（无稳定码），最终对外只剩 `PACKAGING_FLOW_STEP_FAILED`（可重试），
`preconditions()` 返回 `[]`。补充契约见
`docs/specs/drawing-flow-non-editable-requirement.md`（新增前置条件码 `REQUIREMENT_NOT_EDITABLE`
与 `RequirementSaveError` 的稳定码分层）。本节只是指针，§3 的 C1–C5 契约不变。

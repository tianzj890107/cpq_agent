# 规格：卡片步进不许倒回（补做 / 乱序完成已做完的步骤）

状态：Spec + 红测（已实现）
红测：`tests/test_quote_card_step_order_and_replay_red.py`

血缘：承接 `quote-workflow-cards.md`（卡片步进 / 角色归属 / 交接）、
`quote-task-coexistence-and-atomic-claim.md`（受控假库验证方式）、
`tech-handoff-atomic-idempotent-close.md`（事务与幂等口径）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

`cpq_wf.complete_step()` 把 `current_step` 一律写成 `n+1`，**从不看其它步骤是否已经做完**；
于是"补做 / 乱序 / 重放"任何一个动作都会把用户看到的进度条倒回去。本 Spec 要求：
`current_step` 必须是**第一个还没做完的步**，全做完就是 `completed`。

## 1. 现状缺口（34 上真跑实测，2026-09-22）

会话 `a001739dec31`「700ML双开门酒盒（全流程复跑 0116）」（技术项目 `bc0d1aeb4547`）：

1. 第 2 步「工艺确认」归工艺经理，发起人（销售经理）做这一步被 400 拒；顺序是
   先做完 1、3、4、5、6 步 → 卡片 `current_step=6`、`overall_status=completed`；
2. 工艺经理补做第 2 步之后，卡片变成 **`current_step=3` / `overall_status=handoff_pending`** ——
   六步全都做完的卡片反而退回"待转交 3"；
3. 只能把 3、4、5、6 步按原快照**再确认一遍**才回到 `completed`（本轮就是这么把卡片救回来的）。

根因在 `cpq_wf.complete_step()`：写本步之后只做

```
nxt = min(step_no + 1, LAST_STEP)
next_role = "" if done_all else role_of_step(nxt)
need_handoff = bool(next_role) and next_role != user.get("role_code")
status = "completed" if done_all else ("awaiting_handoff" if need_handoff else "in_progress")
```

`done_all` 只判"本步是不是最后一步"，`cpq_wf_card_step` 里其它行的状态**一次都没读**。
所以：补做靠前的步 → 倒回；重放一个已经 done 的步（前端重试 / 双重提交）→ 同样按 `n+1` 重算，
已 `completed` 的卡片会掉回 `awaiting_handoff`；状态机与用户看到的进度不一致。

## 2. 允许修改范围（实现方）

1. `cpq_wf.py`
   - `complete_step()`：写完本步的 `UPDATE` 之后，按 `cpq_wf_card_step` 的**实际状态**算
     `current_step` 与 `overall_status`：`current_step = min{ k | 第 k 步不是 done }`；
     全部 done → `current_step = LAST_STEP`、`overall_status = 'completed'`；
     `awaiting_handoff` 只在"这一部还没做完且它归属别的角色"时出现；
   - 返回体 `next_step_no` / `next_step_name` / `next_role_code` 必须与上面算出来的步一致
     （全做完时 `next_step_no` 为空、`next_role_code` 为空）；
   - 建议把"最小未完成步"抽成可测的纯函数（例如 `next_pending_step(done_steps, last_step)`），
     表结构与既有列不动。
2. 不改 `QUOTE_STEPS` 的步骤名 / 角色归属，不改 `start_step()`、`send_task()` 的语义，
   不改 `cpq_wf_card_step` 的既有列（本步仍只写 `status` / `owner_user_id` / `data_snapshot` /
   `completed_at` / `started_at`）。

### 2.1 实现记录（`## 277`）：`current_step` 从"本步 + 1"改成"最小未完成步"

- **新增纯函数** `cpq_wf.next_pending_step(done_steps, last_step=LAST_STEP)`：返回第一个不在
  `done_steps` 里的步号，全做完给 `None`（非数字项跳过，不抛）。
- **新增只读取数** `cpq_wf._done_step_numbers(conn, card_id)`：
  `SELECT step_no, status FROM cpq_wf_card_step WHERE card_id = %s`，只把 `status == 'done'`
  的行算进集合（大小写/空白归一）。
- **`complete_step()` 的改动**：写本步 `UPDATE` 之后调这两个函数，
  `nxt = next_pending_step(...)`；`done_all = nxt is None`；卡片写的是
  `card_step = LAST_STEP if done_all else nxt`；`next_role` / `need_handoff` / `overall_status`
  全部由 `nxt` 推导（`awaiting_handoff` 只在"这一步还没做完且归属别的角色"时出现）；
  `next_step_no` 仍按既有口径给 `None`/空值（本批只改**它指向哪一步**，不改键名与空值约定）；
  `_log(..., "step_done", step_no, card_step, ...)` 的留痕目标步同步。
- **一处实现选择**：判断"是否全做完"用的是**库里的实际状态**，不是"本步是不是最后一步" ——
  这正是 §3.2 / §3.4 要的（补做第 2 步、重放第 5 步时，6 步都已 done）。
- **未改**：`QUOTE_STEPS` 的步骤名与角色、`start_step()` / `send_task()` 语义、
  `cpq_wf_card_step` 的既有列与本步写入的列集（只写 `status` / `owner_user_id` /
  `data_snapshot` / `completed_at` / `started_at`，其它行一行不碰）。

## 3. 契约（可验收）

### 3.1 顺序推进不变

第 1..n 步依次完成且后面都没做时，行为与今天一致：`current_step = n+1`（因为 1..n 都 done，
最小未完成步就是 n+1）；走完第 6 步 → `completed`。

### 3.2 补做不许倒回

1、3、4、5、6 已 done，补做第 2 步之后：`current_step = 6`（`LAST_STEP`）、
`overall_status = 'completed'`；**不许**出现 `current_step = 3` 或 `handoff_pending`。

### 3.3 乱序按"最小未完成步"

只做了第 2 步（第 1 步没做）时：`current_step = 1`；因为第 1 步归属另一个角色，
`overall_status = 'awaiting_handoff'`（不是 3）。

### 3.4 重放幂等

卡片已经 `completed` 时再确认第 5 步：`current_step` 与 `overall_status` **不变**
（仍 `6` / `completed`）。前端重试、双重提交都不许把卡片打回。

### 3.5 返回体与卡片一致

`complete_step()` 的返回 `card.current_step/overall_status` 与 `next_step_no/next_role_code`
必须来自同一份"最小未完成步"结论：补做第 2 步后 `next_step_no` 为空、`next_role_code` 为空。

### 3.6 留痕不变

补做与重放照旧写一条 `step_done` 事件（`cpq_wf_task_event`），actor 是实际操作的人。

## 4. 禁止事项

- 不许改任何既有红测（含 `test_quote_task_coexistence_and_atomic_claim_red.py` 的受控假库口径）；
- 不许为了让红测转绿改步骤名 / 角色表 / `LAST_STEP`，也不许把"补做"改成"拒绝执行"；
- 不许在读取路径写库；不许对 `cpq_wf_card_step` 的其它行做覆盖写；
- 不许改成本/报价/技术侧口径，不许改前端；不许 commit / push / tag / Release / 部署 / 重启服务。

## 5. 验收

- 红测：`./open-claude/.venv/bin/python -m unittest tests.test_quote_card_step_order_and_replay_red`
  —— 用**受控假连接**驱动真 `cpq_wf.complete_step`（本地不需要 PG、不连库），实现前 §3.2 / §3.3 /
  §3.4 / §3.5 必须红，§3.1 / §3.6 必须绿；
- 34 上手点：让工艺经理补做已完成的第 2 步，卡片应停在 `6 / completed`，不再退回第 3 步。

## 6. 本批不做

- 卡片步骤的"跳步"（允许跨过未完成的步直接做后面的步）与本步权限之外的审批流；
- 时间线 / 前端进度条的展示调整（只要求状态机正确）；
- 报价版本落库那条（见 `packaging-quote-version-persistence.md`）。

# 卡片步快照「完成本步」必须是合并，不是整份替换（第 9 层）

血缘：`quote-card-step-order-and-replay.md` §2.1（`current_step` 取第一个未完成步，乱序/重放不许倒退）、
`packaging-quote-draft-and-card-visibility.md` §3.2（报价卡片第 2 步要看得见包装分区）、
`packaging-quote-version-persistence.md` §2.1（第 5 步落报价版本要靠快照里的整包）、
`cpq_tech_bridge.packaging_snapshot()`（技术侧写进来的 3 个键）。

状态：Spec + 红测（已实现）（`complete_step()` 已改成复用 `merge_step_snapshot()` 做合并，空 / 非法负载不再碰 `data_snapshot`；落地记录见 §7，原红基见 §6）
红测：`tests/test_quote_card_step_snapshot_merge_red.py`
本批 changelog 条目号：`## 317`。

## 0. 一句话目标

`POST /wf/card/step-done`（`cpq_wf.complete_step`）今天对该步的 `data_snapshot` 是**整份替换**：
负载里没出现的键全部消失，负载为空/非法 JSON 时直接写 `NULL`。
于是**任何一次"重做/重放/补做这一步"都会把技术侧写进来的包装投影抹掉** ——
报价卡片第 2 步的「包装：定价与报价分区」面板、以及第 5 步要落的报价版本，一起没了。
本层把这条写成**合并**语义。

## 1. 现场证据（34，2026-09-22 只读复验；这是对我此前读数的**纠偏**）

同一份代码、同一套回传通道，快照里有没有包装投影**只取决于这一步最后是谁写的**：

| 报价会话 | 第 2 步快照键 | 谁最后写的这一步 |
| --- | --- | --- |
| `566207eb006a` | `['packaging_package', 's2_packaging', 's2_packaging_cost']` | 回传通道（`cpq_tech_bridge` → `packaging_snapshot()`） |
| `e2e00a1b2c3d` | 同上（3 键） | 同上 |
| `71c5a1c26619` | 同上（3 键） | 同上 |
| `c0239386c1c4`（本轮真跑） | `['s2_cost', 's2_route']` | **被脚本 `/wf/card/step-done` 覆盖过** |
| `e59e1b382478`（`## 288` 引用的那张） | `['s2_cost', 's2_route']` | 同上（**推断**：`## 288` 那一轮的跑法也用 `/wf/card/step-done` 收尾；`c0239386c1c4` 是本轮实测可知的一例） |

对照组的 3 张卡片是**只读复验**（`GET /wf/card/step-data`）直接读到的键，不是推断；
「谁最后写的这一步」由同一会话的完成路径推出，其中 `c0239386c1c4` 这一例有本轮脚本为证。

读法必须跟着改：`## 288` 记的"第 2 步快照里没有 `packaging_package`"**不是**回传通道没写，
而是那一步后来被 `/wf/card/step-done` 用一份**只含 `s2_cost`/`s2_route` 的负载整份替换**了。
换句话说：**产品这条链本来是通的，是"写快照用替换而不是合并"把它打掉的。**

代码级事实（本机 HEAD，逐条可复现）：

- `cpq_wf.complete_step()`（`cpq_wf.py:861`）的 UPDATE 逐字是
  `UPDATE cpq_wf_card_step SET status = 'done', owner_user_id = %s, data_snapshot = %s::jsonb, …`
  —— 入参 `snap` 直接落库；`snap` 为空串或非法 JSON 时先被置成 `None`，**照写不误**（等于清空）；
- 同一模块里**早就有**合并语义 `cpq_wf.merge_step_snapshot()`（`cpq_wf.py:1041`：
  `merged[key] = value`），但**只有回传通道在用**；用户点按钮走的是替换那条；
- 技术侧那份投影 `cpq_tech_bridge.packaging_snapshot()` 返回的 3 个键
  （`s2_packaging` / `s2_packaging_cost` / `packaging_package`）**逐字没被改过**，问题只在写入语义。

## 2. 口径（逐条，可直接验收）

1. **`/wf/card/step-done` 对该步快照是合并**：负载里出现的键覆盖，负载里**没有**的键**保留原值**；
   与 `merge_step_snapshot()` 同一套语义（同一份实现，不许写第二份合并逻辑）。
2. **空负载不许清空**：快照为空串、`"{}"` 或非法 JSON 时，`data_snapshot` 必须**保持原值**
   （非法 JSON 仍可按既有口径告警/留痕，但**不许**把已有快照写成 `NULL`）。
3. **幂等与乱序**：同一步重复完成、重放已 `done` 的步、补做靠前的步，都不许丢键；
   `current_step` 仍按 `quote-card-step-order-and-replay.md` §2.1 取"第一个还没做完的步"，
   不许因为补做靠前的步把进度条倒回去（本层只做护栏，不改这条口径）。
4. **技术侧键名与形状不变**：`s2_packaging` / `s2_packaging_cost` / `packaging_package` 三个键名、
   `packaging_snapshot()` 的返回结构逐字不动；**不许**为了让面板出现而把 `packaging_package`
   塞进 `FORMS`（渲染那一层是 `packaging-parts-in-card-and-material-fill.md` §2.1 的事）。
5. **回传通道行为不变**：`merge_step_snapshot()` 的合并语义、`_commit_card_steps()` 的补前置步骤、
   `already_sent` 幂等一律逐字保持。

## 3. 本层不做的

- 不改前端提交负载的形状（合并放在服务端做，前端不需要先读一遍再合并）；
- 不改 `FORMS` / 不改任何分区渲染；不动 `packaging_snapshot()` 的键；
- 不改角色判定与门禁；不碰 34 的部署与推送；不删任何项目或会话。

## 4. 红测分组（`tests/test_quote_card_step_snapshot_merge_red.py`）

- **A 组 合并语义（2 红）**
  - A1 `complete_step()` 的函数体里必须出现"读现值再合并"的痕迹（`_snapshot_dict(` 或
    `merge_step_snapshot(`）—— 今天两者都没有（它是直接写 `snap`）；
  - A2 写 `data_snapshot` 的那条 UPDATE 必须对空负载安全：函数体里 `COALESCE(` 与
    `data_snapshot` 必须同现于写快照的语句（或直接复用 `merge_step_snapshot(`）。
- **B 组 护栏（3 绿）**
  - B1 `merge_step_snapshot()` 仍在，且仍是逐键合并（函数体含 `merged[key] = value`）；
  - B2 `packaging_snapshot()` 仍返回那 3 个键（`s2_packaging` / `s2_packaging_cost` /
    `packaging_package`）；
  - B3 `current_step` 仍取"第一个还没做完的步"（`next_pending_step(` 仍在 `complete_step` 里）。

## 5. 验收

```bash
./open-claude/.venv/bin/python -m unittest tests.test_quote_card_step_snapshot_merge_red \
  tests.test_quote_card_step_order_and_replay_red -v
```

34 上（修好后）：对已回传的卡片再点一次第 2 步「完成本步」，`GET /wf/card/step-data?…&step_no=2`
必须仍是 `packaging_package` + `s2_packaging` + `s2_packaging_cost` 三个键都在；
报价卡片第 3–5 步的「包装：定价与报价分区」面板不因重放而消失。

## 6. 红基（2026-09-22 实跑，未实现）

```
./open-claude/.venv/bin/python -m unittest tests.test_quote_card_step_snapshot_merge_red
  → Ran 5 tests … FAILED (failures=2)
```

红的 2 条 = A1（`complete_step` 里没有合并痕迹）、A2（写快照没有空负载保护）；
绿的 3 条护栏 = B1（`merge_step_snapshot` 的逐键合并还在）、B2（技术侧 3 键还在）、
B3（`current_step` 仍取第一个未完成步）。

## 7. 落地记录（2026-09-22，Codex 实现）

改了 **1 个文件**：`cpq_wf.py`（`complete_step()`）。

- 快照写入从「整份替换」改成**合并**：`complete_step()` 把负载解析成对象后，直接调
  `merge_step_snapshot(session_id, step_no, payload, conn=conn)` —— 复用回传通道那条既有语义，
  **没有**写第二份合并逻辑（§2.1）。该函数本来就是「逐键覆盖 + 保留未出现的键」，并且
  `cpq_auth._commit()` 是空实现，接 `conn` 不会把调用方的事务提前提交（§2.5）。
- 主 UPDATE 里 `data_snapshot` 这一列**不再出现**（快照由上面那一步写），既有的
  `status/owner_user_id/completed_at/started_at` 一字未动。
- 空负载口径（§2.2）：负载是空串、`"{}"`、非法 JSON 或**非对象**（数组等）时，**完全不碰**
  `data_snapshot` —— 不是"写回原值"而是"不写"，所以连 `jsonb` 里存的非对象老值也不会被改写。
- 第 5 步落版本（`packaging_quote` / `packaging_package`）改成只看**本次提交的负载**
  （`_packaging_quote_of(payload)`）：否则一次空重放会把早先留下的报价再落一版（只增不改的版本表
  会被重放撑出重复版本）。有报价负载时行为与改动前一致。
- `current_step` / `next_pending_step()` / 自动推送 / 留痕一律未动（§2.3 护栏 B3 绿）。

### 实测（本机）

```
tests.test_quote_card_step_snapshot_merge_red                    → Ran 5 OK（原 2 红全绿）
tests.test_quote_card_step_order_and_replay_red                  → Ran 6 OK
tests.test_tech_handoff_atomic_idempotent_red                    → Ran 35 OK
tests.test_tech_cost_report_handoff_continuity_red               → Ran 14 OK
tests.test_packaging_quote_version_persistence_red               → Ran 8 OK
（上面 5 份一起跑：Ran 68 OK）
tests/test_*.py 里 card|quote|handoff|wf_ 共 1524 条                  → 4 红，全部与本批无关（见下）
```

行为复验（`tests/fixtures/wf_handoff_harness.py` 的受控假库，真调 `cpq_wf.complete_step`）：

- 第 2 步快照预置 `['packaging_package','s1_basic','s2_packaging']`，带负载 `{"s2_cost": …}` 再点一次
  「完成本步」→ 合并后 `['packaging_package','s1_basic','s2_cost','s2_packaging']`（旧键**都在**）；
- 空负载 / 非法 JSON 再点一次 → 键集合**一字不变**（不再出现 `NULL` 清空）；
- 第 5 步带报价负载 → 照旧落版本；随后空负载重放 → **不**再落版本。

### 已记录的偏差 / 边界

1. 本批**没有**顺手把 `merge_step_snapshot()` 的 `_commit(conn)` 去掉：它是空实现，去掉属无关改动；
   等真接上显式事务时再一起收（Spec §2.5 只要求它行为不变）。
2. 与本批无关的 4 条红（都不是本批引入、都不是本批该修的）：
   `tests.test_packaging_quote_send_button_entry_red` A1/A2（并行批次 `## 318` 的新红测，
   前端包装整包「发送」按钮还没落）、`tests.test_packaging_quote_send_recovery_red::CMetaRecovery::test_c1`
   （存量红，`## 272`）、`tests.test_quick_quote_case_maintenance_red::TestFPanelWiring::test_f1`
   （面板缺 `CASE_FIELDS_PATH` 常量，属快速报价维护批次；本机 stash 复跑确认与本批无关）。
3. 34 上**未**部署、未 push：本批只改本地工作区并提交。

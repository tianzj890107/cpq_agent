# 批量动作统一容错语义：逐件跑完 + 部分完成（partial）+ 仅重试失败项（第五批）

状态：Spec + 红测（已实现）（红测 test_tech_params_autofill_and_soft_gates_red 另有已记录的测试侧冲突，见 changelog ## 226）
红测：`tests/test_tech_batch_partial_semantics_red.py` `tests/test_tech_params_autofill_and_soft_gates_red.py`

## 0. 一句话结论

第一批已经把**几何 / 2D** 的批量生成改成「逐件容错 + `partial`」，但同一套语义没有铺到
其余批量动作：

- **成本「一键测算全部成本」仍是一票否决**：`cost-review.js` 的 `crRunParts()` 第一个没算出
  结果的零件就让整批 `return false`，后面的零件一个都不再尝试（线上下现象：P-002 失败 →
  P-003~P-005 连试都不试，用户只能一个个点）；
- **工艺批量虽然逐件跑完，但只要有 1 个失败就上报 `task-failed`**：`app.js` 的
  `runAllPartProcessesInBackground()` 把「4 成功 + 1 失败」说成整批失败，用户在左侧会话里
  看到的是一张失败卡；
- **会话卡不认识 `task-partial`**：`agent-chat.js` 的桥只处理 `task-progress` /
  `task-completed` / `task-failed`，运行时 `tech-board-runtime.js` 的 `EVENT` 里也没有
  `task-partial`；
- **没有任何「仅重试失败项」入口**：失败后只能整批重跑，重新计费、重写已成功结果；
- **整机成本没有服务端门禁**：`cost_review.run_assembly()` 只按 2.2 的上下文算，不检查
  零件是不是都算出成本了。前端靠 `crRunParts()` 的提前 return 顺带挡住，
  任何非界面路径（Agent、脚本、并发操作）都能在零件成本残缺时把整机成本算出来 / 花一次模型钱。

本批把「逐件容错 + partial + 仅重试失败项」做成所有批量动作的唯一口径，并把整机成本
的服务端门禁补上。

## 1. 现状证据（只读检查）

| 位置 | 现状 |
| --- | --- |
| `tech_app/frontend/cost-review.js:757` `crRunParts()` | 逐件 `for`，第一件没出结果就 `crSay(...)` + `return false`，后续零件不再尝试 |
| `tech_app/frontend/cost-review.js:806` `crRunAll()` | `if (!await crRunParts(false)) return;` —— 一件失败整批停，既不汇总失败清单也不出 partial |
| `tech_app/frontend/app.js:2374` `runAllPartProcessesInBackground()` | `result.failures.length` 即 `allPartsProcessSettle("task-failed", ...)`；只有零失败才 `task-completed` |
| `tech_app/frontend/agent-chat.js:1918-1926` 桥订阅 | 只认 `task-progress` / `task-completed` / `task-failed`，没有 `task-partial` |
| `tech_app/frontend/tech-board-runtime.js:52-59` `EVENT` | 只有 `TASK_PROGRESS` / `TASK_COMPLETED` / `TASK_FAILED`；`publishTaskCard()` 的白名单同样只有这三个 |
| `tech_app/backend/main.py:2978` `run_cost_review_assembly()` | 只挡 `plan.process is None`；零件成本残缺也照旧 `tasks.submit(...)` → 后台真调模型 |
| `tech_app/frontend/*.js` | 全仓没有「仅重试失败项」入口；批量失败后只能整批重来 |
| `tech_app/backend/services/tasks.py:140-146` | **已经是** `partial` 感知（第 1 批落地），任务函数返回 `{"status": "partial"}` 时任务记录就是 partial |

## 2. 术语与统一口径

- **逐件批量动作**：把一批零件逐个处理（几何、2D、工艺推荐、成本测算），每件的结果彼此独立。
- **结算词表**（沿用 `tasks.py` 既有词表，不新增）：`succeeded` / `partial` / `failed` / `interrupted`。
- **结算规则**（本批所有逐件批量动作共用同一套）：
  - `failed == 0` → `task-completed`（成功）；
  - `failed > 0 且 succeeded > 0` → **`task-partial`**（部分完成）；
  - `failed > 0 且 succeeded == 0` → `task-failed`（整批失败）；
  - `skipped_done`（本来就有结果、跳过不重复计费，例如工艺已有工艺推荐）不进失败计数，
    **不改变终态**；
  - 第 1 批几何里被预检挡下的零件（`blocked`，必须补参数）继续按 `partial` 计，
    这一条不动。
- **失败清单**：`failures: [{part_id, name, message}]`，顺序与零件表一致，便于「仅重试失败项」。
- **仅重试失败项**：只重跑上一轮失败清单里的零件，不重算已成功件（不重复计费），
  重试完仍按上面的结算规则收尾。

## 3. 契约

### C1 事件与运行时常量

- `tech-board-runtime.js` 的 `EVENT` 增加 `TASK_PARTIAL: 'task-partial'`，并放进
  `publishTaskCard()` 的白名单 —— 否则 `publish('task-partial', ...)` 会被运行时自己吞掉。
- 事件语义：`task-progress`（进行中）/ `task-completed`（成功）/ **`task-partial`（部分完成）**
  / `task-failed`（失败）/ `interrupted`（中断，既有）。
- partial 与 failed 的 payload 统一带：`action`、`total`、`succeeded`、`failed`、`skipped`、
  `failures`（数组）。

### C2 成本：逐件全部尝试，不在第一件停下（`cost-review.js`）

1. `crRunParts(onlyMissing = false, onlyIds = null)`：
   - 对筛选后的**每一个**零件都调用 `crRunPart()`，任何一件失败都**不再提前 return**；
   - `onlyIds` 给定时只跑这些零件（供「仅重试失败项」复用同一份逻辑）；
   - 返回 `{ attempted, succeeded, failed, skipped, failures: [{id, name, message}] }`。
2. `crRunAll()`：
   - 先把所有零件跑完；
   - `failed > 0` → **不运行整机成本**（整机成本依赖全部零件单件成本），把失败清单写进会话，
     并上报 `task-partial`（有成功件）/ `task-failed`（一件都没成功）；
   - `failed == 0` → 沿用既有「整机成本 → 汇总」链路，一行不改。
3. 新增 `crRetryFailed()`（**无参数**）：只重跑上一轮失败清单里的零件
   （复用 `crRunParts(false, ids)`）；重试完按 C1 规则决定是否继续整机成本；
   没有失败项时该入口不出现（按钮隐藏/不渲染）。
   - 失败清单记在模块级变量 **`crLastFailures`**（红测按这个名字预置桩），
     `crRetryFailed()` 从它取 part_id；不要改成「必须由调用方传参」，否则红测与按钮都接不上。

测试桩口径（红测用 Node `vm` 抽出 `crRunParts` / `crRunAll` / `crRetryFailed` 三个函数块真跑）：
桩只提供 `crData / crSay / crStatus / crToast / crRender / crCard / crPublishTask /
crSaveNote / crMoney / crRunPart / crRunAssembly / crBusy / crTab / crLastFailures`，
不要依赖其它新的模块级变量，也不要改这三个函数的调用方式。

### C3 工艺：partial 语义 + 仅重试失败项（`app.js`）

1. `runAllPartProcessesInBackground()`：
   - `failures.length > 0 且 succeeded > 0` → `allPartsProcessSettle("task-partial", {...})`；
   - `failures.length > 0 且 succeeded == 0` → `task-failed`；
   - `failures.length == 0` → `task-completed`（既有）；
   - payload 带 `total / succeeded / failed / skipped / failures`。
2. 新增 `retryFailedPartProcesses()`（**无参数**）：只把上一轮失败清单里的零件交给既有单件链路
   （`runOnePartProcess`）重跑，重试完同样按 C1 结算；不重跑已成功件。
   - 失败清单记在模块级变量 **`lastPartProcessFailures`**（红测按这个名字预置桩）；
     内部可以复用 `runAllPartProcesses({ onlyIds: [...] })`，但要保证「只跑这些零件」。
3. `runAllPartProcesses({ force = false, onlyIds = null })`：`onlyIds` 给定时只跑这些零件，
   既有 force /「已有工艺就跳过」语义不变。

### C4 会话卡：部分完成不是失败（`agent-chat.js`）

1. 桥订阅新增 `task-partial` → `renderTaskProgress(Object.assign({}, payload, { status: "partial" }))`。
2. 卡片复用既有 `is-partial` / `taskStatusWord("partial") === "部分完成"` 机制，
   **不得**把 partial 渲染成失败态：不追加红色 `.oc-task-error` 行（与 ## 70「去掉红色报错
   卡片」的口径一致），失败零件以普通步骤行（`pushTaskStep`）列出。
3. 部分完成卡提供「仅重试失败项」入口（既有 `oc-chip` 风格），把失败清单交给看板对应动作重跑；
   真实失败（`task-failed`）仍然保留既有失败呈现。

### C5 整机成本的服务端门禁（`main.py`）

`POST /api/projects/{project_id}/cost-review/assembly` 在**提交任务之前（同步）**检查零件成本：

- 还有零件没算出成本 → **409**，detail 点名缺失零件（含 part_id），
  **不得提交任务、不得调用模型**（不产生模型费用）；
- 全部零件都有成本 → 照旧提交任务；
- `plan.process is None` 的既有 400、权限 `COST_ROLES`、`ir is None` 的既有判断一律不动；
- 口径复用 `cost_review.summarize()` 的 `counts.missing`（唯一汇总口径），不另算一份。

### C6 其它批量动作核对结论（本批只核对，不改代码）

| 动作 | 现状 | 结论 |
| --- | --- | --- |
| 型号核验 / 材料 / 清洗 / 制造 | 整机一次调用（`ir.parts[:20]` 只作为提示上下文），没有逐件循环 | 无 fail-fast，保持不动 |
| 报告汇总（3.1 / 3.2） | 按评估项与阶段结果汇总，没有「逐件中断」语义 | 保持不动 |
| 零部件库检索（`cost_lookup` / `process_lookup` / `component_match`） | 查不到就留空继续 | 已逐件容错，保持不动 |
| 几何 / 2D 批量 | 第 1 批已落地逐件容错 + `partial` | 不放松、不改口径 |

### C7 不放松既有契约

- `tasks.py` 的状态词表与 `partial` 落库行为不动；
- 第 1 批几何 partial（`_assemble_batch`）与第 2 批空特征回退不动；
- `interrupted`（服务重启 / 切看板 / 桥超时）语义与蓝色中性呈现不动；
- 权限、路由、waiver、确认门禁、审计一律不动；既有字段只增不减。

## 4. 允许修改范围

- `tech_app/frontend/cost-review.js`（`crRunParts` / `crRunAll` / 新增 `crRetryFailed` + 按钮）
  、`tech_app/frontend/cost-review.html`（`?v=`）；
- `tech_app/frontend/app.js`（`runAllPartProcessesInBackground` / 新增 `retryFailedPartProcesses`
  + 看板动作）、`tech_app/frontend/index.html`（`?v=`）；
- `tech_app/frontend/agent-chat.js`（桥订阅 + 部分完成卡 + 仅重试失败项入口）
  、`tech_app/frontend/tech-workbench.html` / `index.html`（`?v=`）；
- `tech_app/frontend/tech-board-runtime.js`（`EVENT.TASK_PARTIAL` + `publishTaskCard` 白名单）
  与所有引用它的 html 的 `?v=`；
- `tech_app/backend/main.py`（`run_cost_review_assembly` 的同步门禁）。

## 5. 本批不做（留给后续）

- 数量 / 材料 变化对 BOM / 工艺 / 成本的逐件失效（第 4 批已定义几何侧，其余不在本批）；
- `invalidate_confirmations()` 收敛、报告与成本快照的逐件版本；
- 零件分类（`cad_requirement` / `make_or_buy`）—— 用户已取消；
- 会话时间线的其它内容类型持久化（业务动作轨迹合并进同一份时间线）。

## 6. 验收（红测清单）

红测文件：`tests/test_tech_batch_partial_semantics_red.py`

**后端（真实 `TestClient` + 临时 `DATA_DIR`，`tasks.submit` 打桩，绝不调模型）**

1. 零件成本残缺时 `POST /cost-review/assembly` → **409**，detail 点名缺失零件。
2. 同一场景 **没有提交任何任务**（`tasks.submit` 调用次数为 0），也没有写整机成本。
3. 零件成本补齐后同一路由 → 允许提交（返回 `task_id`，`submit` 被调用 1 次）。
4. 既有门禁不变：`plan.process is None` 仍是 400。

**前端（Node `vm` 驱动从真实文件抽出的函数 + 源码契约断言）**

5. `crRunParts`：4 个零件里第 2 件失败 → 4 件都尝试过（顺序不变），返回
   `{failed: 1, failures:[P-002]}`，不再提前停。
6. `crRunAll`：有失败时不调用整机成本（`crRunAssembly` 未被调用），并上报 `task-partial`。
7. `crRunAll`：零失败时照旧调用整机成本（既有链路不破）。
8. `crRetryFailed`：只重跑失败清单里的零件（已成功件不再被调用一次）。
9. `runAllPartProcessesInBackground`：4 成功 + 1 失败 → 结算事件是 `task-partial`
   且 payload 带 `succeeded/failed/failures`。
10. 同一函数：0 成功 + 1 失败 → 仍然是 `task-failed`（不把整批失败说成部分完成）。
11. `retryFailedPartProcesses`：只重跑失败清单里的零件。
12. `tech-board-runtime.js` 暴露 `TASK_PARTIAL: 'task-partial'`，且 `publishTaskCard` 白名单包含它。
13. `agent-chat.js` 的桥处理 `task-partial` 并按 `status: "partial"` 渲染，不写红色错误行。
14. 会话卡 / 看板出现「仅重试失败项」入口文案。
15. 被改动的四个脚本在对应 html 里的 `?v=` 已 bump（旧串消失）。

## 7. 测试命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_tech_batch_partial_semantics_red -v
node --check tech_app/frontend/cost-review.js
node --check tech_app/frontend/app.js
./open-claude/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

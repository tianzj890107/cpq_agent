# 规格：留痕欠条**在面板上看得见、能补写**

依赖：`docs/specs/packaging-handoff-audit-relay.md`（§C1 七键披露体、§C2 欠条与补写接口、§C3 两条路由；
§6 边界 4 明写"面板上的告警（左栏 / 状态条那一句）是下一批"——本批就是那一批）、
`docs/specs/packaging-quote-send-button-entry.md`（面板与那颗回传按钮）、
`docs/specs/packaging-silent-degradation-disclosure.md`（"有一条留痕没落下"必须说出来）。

状态：Spec + 红测（已实现）（原状：`tech_app/frontend/requirement-confirm.js` 的包装成本面板
（`pcPanel()` `:961` / `pcRefresh()` `:1002` / `pcBind()` `:1015` / `pcSendQuote()` `:1039`）
**只字未提留痕**：回传成功后 `pcToast(...)` 只报"已回传销售：交接 …"，把 `handoff.audit`
（七键披露体）整个丢掉 —— 留痕没落下时人看不到任何东西；`audit-pending` 与
`audit-pending/relay` 两条新路由在**前端源码里 0 处引用**，"还欠几条、能不能补"在界面上无处可去）
红测：`tests/test_packaging_handoff_audit_pending_panel_red.py`
行号基线：HEAD `6466cb1`

## 0. 一句话目标

回传的**留痕状态**跟"回传成功"这件事一起长在面板上：没落下时当场说清"试了几次、有没有记成待补写"；
还欠几条列出来；有权限的人点一下就能补写，补完剩几条立刻刷新。

## 1. 现状缺口（代码级）

1. `pcSendQuote()`（`:1039`）拿到 `payload.handoff` 后只读 `handoff_no` / `version_no` /
   `already_sent`，`handoff.audit` 从未被读 → 七键披露体的 `attempts` / `pending` 无人消费；
2. 面板（`pcPanel()`）与 `pcRefresh()` 从不请求 `…/packaging-quote/audit-pending`：
   欠条存在后端、界面上是零；
3. 前端源码里 `audit-pending/relay` 0 处引用 —— 补写没有入口，只可能靠人手工发 HTTP。

## 2. 契约

### C1 四个顶层纯函数（`node -e` 可抽出来真跑，体内无 DOM / `fetch(` / `localStorage`）

- `pcAuditWarning(audit)` → 字符串，**空串表示"没什么可说"**：

  | 输入 | 返回 |
  | --- | --- |
  | `null` / 非对象 / `ok !== false` | `""`（没有披露体、或留痕落下了） |
  | `ok === false` | `"这条回传的留痕没落下<（试了 N 次）>：<message 逐字><尾巴>"` |

  - `（试了 N 次）` 只在 `attempts` 是**正有限数**时出现（旧五键披露体没有它 → 不提次数，
    不许编一个 1）；`message` 为空时用 `后端没有给原因` 兜底（不许出现 `undefined` / `null`）；
  - 尾巴按 `pending` 三态互斥：`"recorded"` → 含 `已记成待补写`；`"unavailable"` →
    含 `也没能记下`；其余（含旧后端的 `""`）→ 一句"等接口恢复后重发"，**不许**冒充前两种。

- `pcPendingRows(doc)` → 数组，每项 `{id, text, when}`；`doc` / `doc.items` 不是数组 → `[]`；
  行数**只由 `items` 长度决定**（不许拿 `count` 造假行）；`id` 取 `pending_id`；
  `text` 含 `payload.requirement_no`（缺了写 `（未知需求单）`）、有 `version_no` 时含 `第 N 版`、
  有 `code` 时含那个码；`when` 取 `recorded_at`。
- `pcPendingHeadline(doc)` → `""`（没有行）或 `"还欠 N 条留痕没落下"`（N = `pcPendingRows(doc).length`）：
  它**复用** `pcPendingRows()`（不把"哪些算一行"抄第二遍），因此抽它出来真跑时要连
  `pcPendingRows()` 一起抽（红测的抽具名函数工具支持这一点）。
- `pcRelayText(result)` → `""`（非对象）或 `"补写留痕：补上 R 条，还欠 L 条"` + 有失败时
  `" —— <message 逐字>"`。

### C2 面板：告警与欠条同步长出来

- `pcPanel(cost, items, writable, handoff, pending)` 新增一块 `<div class="pc-audit" data-pc-audit="1">`：
  告警句（`pcAuditWarning(handoff.audit)`，非空才渲染）、标题（`pcPendingHeadline(pending)`）、
  逐条清单（`data-pc-audit-id="<pending_id>"`）、以及**只有 `writable` 且真有欠条时**才给的
  `<button data-pc-audit-relay="1">补写留痕</button>`；
  只读角色看得到欠条、看得到"补写要财务 / 工艺侧权限"，但没有按钮；
  告警与欠条**都没有** → 整块不渲染（不许拼一个空块）。
- `pcRefresh()` 读一次 `GET …/requirement/packaging-quote/audit-pending`，把结果交给
  `pcPanel(..., pending)`；**读失败 → `pending = null`**（于是整块不渲染）——
  读不到**不等于**"不欠"，不许在界面上写成"还欠 0 条"。
- `pcBind(pid)` 绑 `[data-pc-audit-relay]` → `pcRelayAudits(pid)`。

### C3 回传之后：告警跟着 toast 一起出来

- `pcSendQuote()` 成功分支在既有那句 toast 后面追加 `pcAuditWarning(handoff.audit)`
  （有告警时按**警示样式**弹，人不该以为一切正常）；没有告警时文案与今天逐字相同。

### C4 补写入口（纯前端动作，不猜结果）

- `pcRelayAudits(pid)`：`POST …/requirement/packaging-quote/audit-pending/relay`（空 body）→
  toast 用 `pcRelayText(payload)`（取不到就用一句兜底），然后 `await pcRefresh()`；
  失败按后端 `message` 弹错、**不**改界面上的数字；
- 两个路径由 `pcAuditPendingPath(pid)` / `pcRelayAuditsPath(pid)` 两个函数拼（`encodeURIComponent`
  包 `pid`），字面量必须在源码里逐字出现。

### C5 冻结面

- `pcSendQuote()` 的三类恢复口径（`cost_gaps_unresolved` / `gap_reason_required` /
  `no_candidate` + `multiple_candidates`）与既有问答文案一字不动；
- `window.CfPackagingCostPanel` 既有出口（`mount` / `refresh`）与 `pcApi` / `pcToast` 语义不变；
- 不新增后端路由、不改后端；不引入前端框架 / 依赖；不改 `index.html`；
- 依赖的两个后端路由（`## 418` 已落地）本批只**消费**，不重启议。

## 3. 允许修改范围

1. `tech_app/frontend/requirement-confirm.js`：四个纯函数 + `pcAuditBlock()` +
   `pcPanel()` 多一个参数与一块渲染 + `pcRefresh()` 读欠条 + `pcBind()` 绑按钮 +
   `pcRelayAudits()` + `pcSendQuote()` 的 toast 追加告警 + 两个路径函数；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许前端自己判断"留痕算不算落下"（只看后端 `ok` / `attempts` / `pending`，不猜、不重算）；
- 不许把 `message` 重新措辞或翻译（逐字进界面）；不许把"读不到欠条"写成 0 条；
- 不许在前端自动补写 / 定时轮询 / 后台重试（补写由人点）；
- 不许改 `pcSendQuote()` 的三类恢复与 `pcApi` / `pcToast`；不许给只读角色放开补写；
- 不许改任何 `tests/` 下文件；不连 PG / 34、不发真实 HTTP、不写业务数据；
  不 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_handoff_audit_pending_panel_red -v
node --check tech_app/frontend/requirement-confirm.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_handoff_audit_relay_red tests.test_packaging_handoff_audit_availability_red \
  tests.test_packaging_quote_close_loop_red tests.test_packaging_quote_send_button_entry_red
# 既有挂账：tests/test_packaging_quote_send_recovery_red.py::C1（夹具自遮挡，本批不碰）
```

## 6. 已记录的边界

1. 告警只在**看过这份交接**的地方出现（面板 / 回传 toast）；站内信、左栏状态条是别的话题；
2. `pending` 三态文案是**界面话术**，码与判定仍在后端（本批不新增码）；
3. 欠条清单不翻页：`## 418` 的 `AUDIT_PENDING_MAX = 20` 就是上界，面板一次全列；
4. 补写按钮不做二次确认（补写是幂等动作，重复点只是重申同一份留痕）；
5. 面板仍只在 `industry === 'packaging'` 的项目上挂载（既有条件不动）。

## 7. 落地状态（2026-09-22，Codex 实现）

红基（实现前，`git stash push -- tech_app/frontend/requirement-confirm.js` 后实跑）：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_handoff_audit_pending_panel_red
Ran 24 tests … FAILED (failures=20)          # 20 红 / 4 绿
```

- 20 红：`A1`–`A6`（四个纯函数一个都不存在 —— `A1` 起先报 `not defined`，抽具名函数工具
  修正后仍是红）、`B1`–`B5`（同上）、`C1`（两条路径字面量 0 处）、`C2`–`C7`（面板块 / 读欠条 /
  绑按钮 / 补写入口 / 回传 toast 全没接）、`D3`（纯函数不存在）、`D5`（读欠条不是 GET）；
- 4 绿全是护栏：`D1`（回传三类恢复口径未动）、`D2`（面板既有出口未动）、
  `D4`（没有自动补写 / 轮询）、`D6`（`node --check` 通过）。

实现后：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_handoff_audit_pending_panel_red
Ran 24 tests in 1.212s
OK
node --check tech_app/frontend/requirement-confirm.js     # 通过
```

| 契约 | 落点（`tech_app/frontend/requirement-confirm.js`，包装成本面板那一段 IIFE 内） |
| --- | --- |
| §C1 四个纯函数 | `pcAuditWarning()` / `pcPendingRows()` / `pcPendingHeadline()` / `pcRelayText()`；体内无 DOM / `fetch(` / storage，node 抽出来真跑 |
| §C2 面板块 | `pcAuditBlock(handoff, pending, writable)`：`data-pc-audit="1"` + 告警句 + `pcPendingHeadline()` + `data-pc-audit-id="<pending_id>"` 逐条 + `data-pc-audit-relay="1"`（只 `writable` 且有欠条时给按钮）；两样都没有 → 返回空串；`pcPanel(cost, items, writable, handoff, pending)` 多收一个 `pending` 并渲染它；`pcRefresh()` 里 `let pending = null; try { pending = await pcApi(pcAuditPendingPath(pid)); } catch (error) { pending = null; }` —— 读不到就是 null，整块不渲染（不许写成「还欠 0 条」）；`pcBind()` 绑 `[data-pc-audit-relay]` → `pcRelayAudits(pid)` |
| §C3 回传 toast | `pcSendQuote()` 成功分支新增 `const auditWarning = pcAuditWarning(handoff.audit);`，既有那句 toast 追加 `auditWarning`，并按**警示样式**弹（`pcToast(..., !!auditWarning)`）；没有告警时文案逐字不变 |
| §C4 补写入口 | `pcAuditPendingPath(pid)` / `pcRelayAuditsPath(pid)`（都 `encodeURIComponent(pid)`）+ `pcRelayAudits(pid)`：`pcApi(pcRelayAuditsPath(pid), {method: 'POST', body: JSON.stringify({})})` → `pcToast(pcRelayText(payload) || '补写留痕已完成')` → `await pcRefresh()`；失败弹后端 `message`，不改界面数字 |
| §C5 冻结面 | `pcSendQuote()` 的三类恢复（`cost_gaps_unresolved` / `gap_reason_required` / `no_candidate` / `multiple_candidates`）与 `pcPackagingSendPath()` 一字未动；`window.CfPackagingCostPanel`（`mount` / `refresh`）、`pcApi` / `pcToast` 语义未动；未改后端、未改 `index.html`、未加依赖 |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_handoff_audit_relay_red tests.test_packaging_handoff_audit_availability_red \
  tests.test_packaging_quote_close_loop_red tests.test_packaging_quote_send_button_entry_red \
  tests.test_packaging_quote_send_recovery_red
Ran 159 tests … FAILED (failures=1)
# 唯一一条是**既有挂账** tests/test_packaging_quote_send_recovery_red.py::C1（夹具自遮挡，本批未碰）

# 所有引用 requirement-confirm.js 的套件（35 个文件）：
Ran 592 tests … OK
```

边界（与 §6 一致）：告警只出现在面板与回传 toast；补写只由人点（无定时器、无自动重试）；
欠条清单一次全列（后端上界 `AUDIT_PENDING_MAX = 20`）；面板仍只在
`industry === 'packaging'` 的项目上挂载；未连 PG / 34、未写业务数据、未 push / MR / tag / Release / 部署。

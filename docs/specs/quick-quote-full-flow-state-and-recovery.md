# 快速报价全流程收口：状态机、差异工作区、幂等、恢复与权限

状态：Spec + 红测（已实现）（19 条红测全绿：后端状态机 / 差异工作区 / 幂等 / 恢复与权限 + 交付前端 8 条
（顶层 `diff`、内联编辑器、按钮只认服务端状态、幂等键复用、首页卡片即时 upsert）已全部落地，2026-09-22 复核）  
红测：`tests/test_quick_quote_full_flow_state_and_recovery_red.py`  
前置：`quick-quote-entry-routing-and-packaging-isolation.md` 已解决“包装需求误进精准报价/电池匹配器”；
本 Spec 从快速报价实例建立后继续验收到“落版本、回首页、刷新/重启后再次打开”。

## 1. 目标与验收主路径

以 `SM1`、包装行业和 `QQ-YT-DWG-WINE-700ML` 为基准案例，页面必须可以完成：

1. 首页选包装和快速报价，输入结构化需求并发送；
2. 建立且只建立一个快速报价业务实例；
3. 展示候选案例及逐字段匹配依据，点击候选行选择基准；
4. 在页面内编辑白名单差异字段，看到“参数 / 基准 / 当前 / 差异价格”四列；
5. 重算后展示单价、数量、总价、依据、试算/正式状态和阻断原因；
6. 确认一次只生成一个版本，并更新首页同一张业务卡；
7. 回首页、刷新浏览器、重启服务后，从“我的清单/全部清单/历史报价”任一入口打开同一实例，
   恢复行业、快速模式、需求字段、基准、改动、报价和最后版本；
8. 任何一步证据不足都保留当前数据，并允许无损转精准报价。

不得要求用户复制案例编号，不得使用浏览器 `prompt()` 修改业务字段，不得用前端内存冒充持久化。

## 2. 本次实测事实（2026-09-22）

### 2.1 服务器部署版本的入口仍断

服务器 `172.16.10.34:8010`（build `523bee8`）以 SM1 实跑：

- 点“快速报价”先打开遮罩式案例库，遮住首页输入框；
- 关闭遮罩后输入 700ML 酒盒需求并发送，URL 不变，但状态栏为空；
- `QuickQuotePanel.workspaceState.quick_quote_session_id === ""`，`inputs/match/baseline/workspace/quote` 全为空；
- 七个命令按钮全部启用，包括没有基准、没有报价时的“保存、重算、出价”；
- 线上因此尚未进入快速报价业务实例，更谈不上后半程。

本地工作树中的入口代码已经比服务器版本更新，因此这项主要作为部署验收：实现完成后必须验证 build
与静态资源版本，不能只在本地跑绿。

### 2.2 本地当前实现仍有的后半程缺陷

1. 后端 `workspace` / `price` 返回的差异行在顶层 `diff`，前端只保存 `workspace`，并渲染
   `(workspaceState.workspace || {}).rows`；因此真实差异可能成功计算却不上屏。
2. “选为基准”仍用 `prompt()` 输入案例编号；“保存改动”仍用 `prompt()` 输入 `字段=值`，不是可核对的
   工作区字段，也无法提供字段类型、单位、范围和来源。
3. 出价按钮只根据案例库 `eligible_total` 判断。`eligible_total` 未加载时为 `null`，也会启用；它没有检查
   session、baseline、workspace、price 和是否存在阻断。
4. 所有按钮始终可点，没有 `created → matched → based → edited → priced → confirmed/transferred`
   状态机；乱序请求只能等后端报错。
5. 前端每次重试都生成新的幂等键；双击“建实例/出价”会被视为两次新操作。建实例处理器本身也没有走
   幂等壳。
6. `QUICK_QUOTE_SESSIONS` 与 `quick_quote_idempotency` 都是进程内字典。服务重启后只可能读到最后报价段，
   行业、模式、输入、基准、工作区与幂等记录全部丢失。
7. 读取或写入一个不存在的 session 会通过 `_qq_state(...).setdefault()` 静默新建“幽灵实例”；没有
   `session_not_found`。
8. 快速实例没有持久化 owner / participant，也没有逐实例读写 ACL；知道 session id 的其他登录用户理论上
   可以读取或驱动命令。
9. 确认成功只更新面板内存；没有明确契约保证首页同一卡片立即出现版本、价格、状态和快速报价标识。

## 3. 统一状态机契约

服务端是状态真相源，响应必须给稳定 `workflow_state`：

| 状态 | 已具备 | 页面允许的主要动作 |
| --- | --- | --- |
| `created` | 实例 | 匹配、转精准 |
| `matched` | 候选 | 重匹配、选基准、转精准 |
| `based` | 基准与初始工作区 | 编辑、直接重算、转精准 |
| `edited` | 已保存差异 | 继续编辑、重算、转精准 |
| `priced` | 可确认报价 | 编辑、重算、确认、转精准 |
| `confirmed` | 已落版本 | 再次编辑形成下一版、查看版本、转精准 |
| `transferred` | 已转精准 | 打开精准报价；快速侧只读 |

- 前端按钮的 enabled/disabled 与提示必须消费服务端状态，不能用 `eligible_total` 代替。
- 后端仍必须校验顺序；绕过 UI 的乱序命令返回稳定 `409 + code + required_state + current_state`。
- `eligible_total` 只表示案例库总体可用数，不表示当前实例已经选基准或已经算价。

## 4. 页面交互契约

### 4.1 一个工作区，不要双入口互相遮挡

点击首页“快速报价”只切换路径并展开首页工作区；不应先弹遮罩阻止用户输入。案例库可作为工作区内区域或
侧抽屉，但不能要求“打开模式 → 关闭弹层 → 再输入”。发送需求后必须显示进行中状态和最终成功/失败。

### 4.2 候选和基准

- 候选卡必须显示排名、案例编号、盒型、标准价、匹配分、逐字段依据与冲突。
- 点击候选卡的“选为基准”直接提交其 `case_code`；不得再让用户手输编号。
- 已选候选要有唯一选中态，且刷新后恢复。

### 4.3 参数编辑与差异表

- 使用页面字段控件编辑；类型、单位、允许范围来自后端字段元数据。
- 只提交白名单 `edits`，不允许把整个可篡改 workspace 当权威覆盖。
- 页面必须保存和渲染后端顶层 `diff`；每次 baseline/workspace/price/read 响应都更新它。
- 四列至少为：参数、基准案例、当前报价、差异价格；0 差异与未定价要明确区分。

### 4.4 报价与确认

- price 响应完整展示 `unit_price / quantity / total_price / currency / tax_basis / formula_trace /
  authority / gaps / warnings`（以实际返回字段为准，不在前端计算）。
- 只有 `workflow_state=priced` 且服务端给出 `can_confirm=true` 时可确认。
- 试算可以在 POC 中落演示版本，但必须持续显示“试算/非权威”，不得伪装正式报价。
- 确认成功后立即在首页同一张卡上显示快速报价标识、版本、单价/总价和最近更新时间；不新增第二张项目卡。

## 5. 幂等和并发契约

- 一次用户动作创建一个 operation id；超时重试和双击复用同一个 `X-Idempotency-Key`。
- 建实例也必须幂等。同一用户、同一次 operation 的重复创建返回同一 session/card。
- 服务端幂等键至少绑定 `(actor, session-or-create-scope, command, request_fingerprint)`；同键不同 body 返回
  `409 idempotency_conflict`，不能回放错误响应。
- confirm 双击或网络重试只生成一个版本；并发编辑使用 revision/ETag，旧 revision 返回 `409 stale_revision`。
- 幂等记录必须与业务实例一起持久化，不能因进程重启失效。

## 6. 持久化、读回和清单契约

快速实例至少持久化：owner、participants、industry、quote_mode、title/customer/project、inputs、match 摘要、
baseline、workspace、diff、workflow_state、revision、最后 quote、version_no、transfer 信息和时间戳。

- GET 未知 session：`404 session_not_found`，不得 `setdefault` 创建实例。
- 每条写命令对未知 session：同样 404，不得形成幽灵状态。
- 服务重启后 GET 返回与重启前一致的业务状态；已落版本只是其中一部分，不能替代实例状态。
- 首页“我的清单”按 owner/participant 可见，“全部清单”按角色权限可见，“历史报价”按已落版本可见；三处点击
  同一个 quick session 都回到快速工作区。
- 确认和转精准必须以同一业务实例更新卡片，不生成重复卡。

## 7. 权限契约

- 创建者成为 owner；转交/协作人进入 participant。
- 读权限与写权限分开：无读权返回 404（不泄露存在性），有读无写返回 403。
- `SM1` 可创建和驱动自己的快速报价；其他销售经理不能仅凭 session id 修改它；管理员按既有规则处理。
- 权限判断在业务命令之前，但不得重演技术工艺项目 ACL 把合法业务角色整体挡死的问题。

## 8. 两条演示数据验收

酒盒和圆盘盒都要走同一套 UI 主路径，不允许测试直接调用内部纯函数代替页面操作。至少断言：

- 酒盒需求首选 `QQ-YT-DWG-WINE-700ML`，圆盘盒首选 `QQ-YT-DWG-ROUND-10PC`；
- 修改数量到对应阶梯后，价格来自该案例阶梯/确定性规则；
- 页面可见差异，确认得到 version 1；相同确认重试仍为 version 1；
- 刷新和服务重启后仍可见同一版本；
- 页面中不得出现电池 `91000...` 候选或电池六维评分表。

## 9. 非目标

- 本批不让快速案例库自动增长；两条演示案例可先固定维护。
- 本批不把 Agent/LLM 作为价格计算器；结构化抽取可用模型，但价格与门禁必须确定性。
- 本批不解决 ODA 几何质量；DWG 入口沿用统一解析服务，解析失败必须允许手工核对字段继续演示。

## 10. 验收命令

```bash
python -m unittest tests.test_quick_quote_full_flow_state_and_recovery_red -v
python -m unittest tests.test_quick_quote_entry_routing_packaging_isolation_red -v
python -m unittest tests.test_quick_quote_demo_closure_red -v
node --check tech_app/frontend/quick-quote-panel.js
```

部署后还必须用服务器 build 做一次浏览器级验收；本地源码测试通过不等于服务器静态资源已更新。

## 11. 落地状态（2026-09-22）

后端（`cpq_agent_server.py`）与前端（`tech_app/frontend/quick-quote-panel.js`、`报价首页.html`）全部落地，
`tests.test_quick_quote_full_flow_state_and_recovery_red` **19 OK**。

| 契约 | 落点 |
| --- | --- |
| §3 状态机 | `QUICK_QUOTE_WORKFLOW_STATES` / `_qq_workflow_state()` / `_qq_allowed_actions()` / `_qq_state_envelope()`；乱序命令 409 `invalid_workflow_state` + `required_state` + `current_state` |
| §3 按钮只认服务端 | 首页 `syncQuickQuoteWorkflowState()` 消费 `workflow_state` / `allowed_actions` / `can_confirm`（`QUICK_QUOTE_BUTTON_COMMANDS` 与后端闭集同值），出价另受 `can_confirm` 约束 |
| §4.1 一个工作区 | `initQuoteModeEntries` 命中快速时调 `openQuickQuoteHomeWorkspace()`（只展开工作区，**不再**弹遮罩）；案例库面板改由工作区里的 `qqOpenCaseLibrary` 显式打开 |
| §4.2 候选与基准 | 选基准的唯一入口是候选行的 `data-qq-baseline` 按钮；工作区「选为基准」按钮只把候选带到眼前（`openQuickQuoteCandidates()`），`prompt('把哪个案例选为基准…')` 已删 |
| §4.3 编辑与差异 | 差异行统一取顶层 `diff`（`quickQuoteDiffRows()`；`rememberCommandResult` / 读回都存它）；改参数走内联编辑器 `renderQuickQuoteEditor()`（`data-qq-edit-key` + `data-qq-edit-submit`），`prompt('要改哪些差异项…')` 已删 |
| §4.4 报价与确认 | 出价按钮只在 `can_confirm === true` 且 `workflow_state ∈ {priced, confirmed}` 时可点；成功后就地 upsert 首页同一张卡（`upsertQuickQuoteCard()`，版本/单价/状态取后端 `confirm` 响应），不新增第二张项目卡 |
| §5 幂等 | 前端按命令复用 operation id（`operationId()` / `finishOperation()`，成功才丢弃），`postCommand` 与 `openQuickQuoteSession` 都走它；`_quick_quote_write` 的建实例分支现在也把 `X-Idempotency-Key` 传进 `_handle_quick_quote_session_create` |
| §6 持久化与读回 | 整份状态存 `meta_backend` 项目文档（`_JsonDocRepository`：`quick_quote_session_repository` / `quick_quote_idempotency_repository`）；读契约含 owner/participants/inputs/match/baseline/workspace/diff/workflow_state/revision/card/transfer/version_no |
| §7 权限 | `require_quick_quote_access(session_id, user, write=…)`：无读权 404、有读无写 403；读 / 写两条命令路径都先过它 |

复跑（本机）：

```bash
./open-claude/.venv/bin/python -m unittest tests.test_quick_quote_full_flow_state_and_recovery_red   # 19 OK
node --check tech_app/frontend/quick-quote-panel.js
./open-claude/.venv/bin/python -m unittest $(ls tests/test_quick_quote_*.py | sed 's#/#.#g; s#\.py$##' | tr '\n' ' ')  # 489 条 / 1 红（见 §12）
```

## 12. 已记录的偏差（不改测试）

`tests/test_quick_quote_home_wiring_red.py::DReadPathHonestyRed::test_d2_a_genuinely_empty_read_is_not_dressed_up_as_an_error`
与本 Spec §6 **机制互斥**，因此落地后转红：

- 该用例用一个**从未存在过**的 id（`wiring-probe-empty`）调用 `_handle_quick_quote_read()`，断言响应里没有
  任何含 `error` / `diagnostic` 的键；它的前提是旧契约下 `_qq_state()` 会 `setdefault` 出一个幽灵实例，
  于是"读得到、只是空"。
- 本 Spec §6 明确废止 `setdefault`：**未知 session 一律 404 `session_not_found`**（红测
  `test_read_unknown_session_is_404_and_does_not_create_ghost` 守这条），而 404 错误体按全服务统一的
  `_qq_error()` 形状必然带 `error` 键。
- 两条断言在同一个输入（未知 id + `find_quote` 返回 `{}`）上不可能同时成立：去掉 `error` 键会让
  `test_d1_broken_store_is_surfaced_not_swallowed`（要求未知/异常时带诊断键）转红。
- 处理方式：**不改测试、不放宽断言**；§C5「存在的实例没落过卡时不许带诊断键」这条语义在新实现下仍然成立
  （存在但无卡的实例走 200 且无任何 `read_error`），偏差只在这条守卫的**取值机制**上，已同时记在
  `quick-quote-home-wiring-and-read-diagnostics.md` §6.6 与本文件。

# 包装整包回传的前端入口：只见按钮的人也必须能看到卡片第 2 步的包装分区（第 9 层）

血缘：`packaging-quote-close-loop.md` §2.1/§4.5（回传唯一入口 + 四条路由）、
`packaging-quote-draft-and-card-visibility.md` §2（卡片可见性人工验收）、
`packaging-quote-send-recovery.md` §2.3（落点冲突 409）、
`quote-card-step-snapshot-merge-on-complete.md`（写卡片步必须合并，本层是它的上一步）。

状态：Spec + 红测（已实现）（前端按钮落在包装成本面板，共享正文会带 `packaging_package`；落地记录见 §7，原红基见 §6）
红测：`tests/test_packaging_quote_send_button_entry_red.py`
本批 changelog 条目号：`## 318`。

## 0. 一句话目标

用户问：「我没有你可以直接绕过去的那些工具，我就一步一步点那些只有前端才有的按钮，我能不能跑得通？」

**今天跑不通，且是"看不见"的那种跑不通**：`POST /api/projects/{project_id}/requirement/packaging-quote/send`
（`tech_app/backend/main.py` 的 `PACKAGING_QUOTE_SEND_PATH` + `send_requirement_packaging_quote`）
在**前端一处引用都没有**（全仓库 `*.js` / `*.html` 命中数 = 0）。它只能被 curl / Agent 直接调用。

于是用户点完全部按钮的结果是：卡片能到第 6 步，**但卡片第 2 步永远不会出现「包装：定价与报价分区」面板**，
因为该面板唯一的取数来源 `snapshot.packaging_package` 只有 `packaging_handoff.bridge_result()` 会写，
而没有任何一颗前端按钮会走到那里。用户看到的现象就是"零件拆出来了，但后面那截凭空没了"。

## 1. 根因（代码事实，逐条可复现；不是推断）

1. **包装回传是"有接口、无按钮"**：路由与写权限都在
   （`main.py:7356` 常量、`send_requirement_packaging_quote` 处理器、
   `packaging_handoff.HANDOFF_WRITE_ROLES`、缺口放行 `allow_gaps=True` + `reason`），
   但 `packaging-quote/send` 这个字面量只出现在**后端源码 + tests + docs** 里，
   `tech_app/frontend/**` 与仓库根 `*.html` **一次都不出现**。
2. **前端确实有"回传"按钮，但走的是另一条出口**：成本复核页那颗
   `#crToQuote`（`tech_app/frontend/cost-review.html:152` 标签「➜ 回传销售经理继续报价」，
   `cost-review.js:1234` `onclick = () => crRunOp('send-to-quote')`）打的是
   `POST /api/projects/{project_id}/cost-review/send-to-quote`（`main.py:3830`）。
   工艺报告页那颗（`report-publish-result.js:143`）打的是
   `POST /api/projects/{pid}/process-report/send-to-quote`（5.3）。两条都不是包装那条。
3. **那条出口的正文里根本没有包装整包**：`cost-review/send-to-quote` →
   `cost_flow.send_to_quote()`（`cost_flow.py:821`）→
   `integration_send_to_quote_body()`（`cost_flow.py:637`）→
   `integration_quote_result()`（`cost_flow.py:469`）。
   最后这个函数逐字列了 `part_costs` / `parts_total` / `assembly_cost` / `cost_breakdown` 等字段，
   **没有 `packaging_package`**，也没有调用 `manufacturing_snapshot` / `packaging_handoff` 取整包。
4. **整包只在包装那条链上被装上**：`packaging_handoff.bridge_result()`（`packaging_handoff.py:270-275`）
   才有 `result["packaging_package"] = package`；包体 10 段由 `PACKAGE_SECTIONS`
   （`packaging_handoff.py:40`）定义。
5. **面板只认那一个键**：`tech_app/frontend/packaging-quote-panel.js:54` 读
   `snapshot.packaging_package`，取不到就整块不渲染；该脚本在
   `报价首页.html:1588` 与 `确认需求解析结果.html:1000` 都已加载 —— **面板在，数据来不了**。
6. **文档已经把它写成既有能力**：`docs/specs/packaging-quote-close-loop.md:80` 称
   「回传**只有一个入口** `packaging_handoff.send_to_quote()`：**看板按钮**与 Agent 工具共用」——
   这颗"看板按钮"在代码里不存在，`packaging-quote-draft-and-card-visibility.md` §2 的人工验收第 2 条
   因此在按钮路径上**永远不可达**。
7. **34 真跑对照（本批已复现）**：`a42e5e60a720` 用 curl 直接打 `packaging-quote/send`（`allow_gaps=True`）
   → 200，卡片第 6 步快照里就有 `packaging_parts` / `s6_quote`；只点按钮则走到第 6 步，
   第 2 步快照里始终没有 `packaging_package`。

## 2. 口径（逐条，可直接验收）

1. **前端必须有一颗"包装回传"按钮**：包装项目的成本 / 报价相关页面上，用户能直接点到一颗按钮，
   它调用 `POST /api/projects/{project_id}/requirement/packaging-quote/send`
   （即前端源码里必须出现 `packaging-quote/send` 这个字面量）。
   理由：`packaging-quote-close-loop.md:80` 已经把"看板按钮"写成既有口径 —— 这是欠账，不是新增范围。
2. **共享回传正文必须能带包装整包**：既有按钮出口（`cost-review/send-to-quote`，以及复用同一正文的
   `process-report/send-to-quote`）在包装项目上必须把整包一起送出，即 `integration_quote_result()`
   （或它调用的下游）对包装项目要产出键 `packaging_package`，值逐字取自
   `packaging_handoff` / `manufacturing_snapshot` 的既有整包，**不许在 `cost_flow` 里另拼一份**。
   理由：用户从成本复核页点「回传销售经理继续报价」是真实动作（34 上就是这颗），从那出去不能丢整包。
3. **写权限与缺口放行口径逐字不变**：`HANDOFF_WRITE_ROLES` 集合、
   `_guard_gaps(allow_gaps=True, reason=...)` 的"必须写明原因"、无缺口时不许无理由放行 —— 一个字都不改。
4. **整包 10 段逐字不变**：`PACKAGE_SECTIONS` 与 `bridge_result()` 的 `packaging_package` 仍是同一个包体，
   成本段仍不带售价（`unit_price` / `untaxed_price` 等一律不得出现在交接包里）。
5. **既有按钮不许被换掉**：`cost-review/send-to-quote` 路由与 `#crToQuote` 按钮必须仍在；
   本层是**新增**入口，不是替掉老入口。
6. **面板渲染口径不变**：`packaging-quote-panel.js` 仍只在快照里确实有 `packaging_package` 时才渲染，
   不许改成"兜底自己算一份"。
7. **卡片步快照仍是合并写入**：本层不重定义写快照语义，沿用
   `quote-card-step-snapshot-merge-on-complete.md`（合并，不是整份替换）。

## 3. 允许修改范围

- 前端：`tech_app/frontend/` 下与包装 / 成本 / 报价相关的 `.js` / `.html`，以及仓库根
  `报价首页.html` / `确认需求解析结果.html`（任选放按钮的页面，但必须覆盖包装项目）——
  **只加按钮与调用**，不改既有渲染逻辑。
- 后端：`tech_app/backend/services/cost_flow.py` 的 `integration_quote_result()` /
  `integration_send_to_quote_body()`（补整包字段，取数只调既有 `packaging_handoff` /
  `manufacturing_snapshot`，不复制整包拼装逻辑）；
  必要时在 `main.py` 复用既有 `PACKAGING_QUOTE_SEND_PATH`（不新增第二条写路由）。
- 禁止：改 `packaging_handoff` 的包体口径、写权限集合、缺口放行判据；
  改 `packaging-quote-panel.js` 的取数键；动卡片快照写入语义；
  直接读 `store`/DB 绕过 `cpq_bridge` 或 `packaging_handoff`。

## 4. 红测分组（`tests/test_packaging_quote_send_button_entry_red.py`）

- **A 组 缺失的两件事（今天都是红的）**
  - A1（红）**前端直达入口**：`tech_app/frontend/**`（`*.js` / `*.html`）或仓库根 `*.html` 里，
    必须至少有一处引用 `packaging-quote/send`；今天命中数 = 0。
  - A2（红）**共享正文带整包**：`tech_app/backend/services/cost_flow.py` 里必须出现
    `packaging_package`，让既有按钮出口在包装项目上也能把整包带走；今天不出现。
- **B 组 护栏（今天就是绿的，不许被改红）**
  - B1 `packaging_handoff.bridge_result()` 仍写 `packaging_package`，且 `PACKAGE_SECTIONS` 仍是 10 段、顺序不变；
  - B2 `HANDOFF_WRITE_ROLES` 集合与 `_guard_gaps(..., allow_gaps=...)` 的"必须写明原因"仍在；
  - B3 `cost-review/send-to-quote` 路由与 `#crToQuote` 按钮仍在（既有出口不许被换掉）；
  - B4 `packaging-quote-panel.js` 仍只在快照确有 `packaging_package` 时渲染（不许自己兜底造数据）。

## 5. 验收

1. 用 SM1 登录 `http://172.16.10.34:8010/`，在包装项目的成本 / 报价页面上**只用鼠标点按钮**，
   能把包装整包回传出去（或从成本复核页点「回传销售经理继续报价」时整包随行）；
2. 报价卡片第 2 步出现「包装：定价与报价分区」面板，内容与
   `GET /api/projects/{pid}/requirement/packaging-quote/package` 一致；
3. 前端源码里能查到 `packaging-quote/send`（或等价按钮），
   `cost_flow.py` 里能查到 `packaging_package`；

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_send_button_entry_red \
  tests.test_quote_card_step_snapshot_merge_red -v
```

## 6. 红基（2026-09-22 实跑，未实现）

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_send_button_entry_red
  → Ran 6 tests … FAILED (failures=2, errors=0)
```

红的 2 条 = A1（前端 0 处引用 `packaging-quote/send`）、A2（`cost_flow.py` 里没有 `packaging_package`）；
绿的 4 条护栏 = B1（`bridge_result()` 的整包与 10 段不变）、B2（写权限与缺口放行口径不变）、
B3（`cost-review/send-to-quote` + `#crToQuote` 仍在）、B4（面板仍只认 `snapshot.packaging_package`）。

## 7. 落地记录（2026-09-22，Codex 实现）

改了 **2 个文件**，一行后端路由都没新增（复用既有 `PACKAGING_QUOTE_SEND_PATH`）。

### 一、前端入口（§2.1）

`tech_app/frontend/requirement-confirm.js`（包装成本面板，1.1/1.2 页面上那颗「重算成本」旁边）：

- `pcPanel()` 的 `.pc-actions` 里新增一颗按钮 `data-pc-send-quote="1"`「回传销售继续报价」，
  **成本测算出来之前是 disabled**（没成本时后端本来就只会回 `cost_built=false` 那句）；
  旁边写清楚这颗按钮做什么（整包发回原报价卡片 → 第 2 步的包装分区带上它）。
- `pcBind()` 把它接到 `pcSendQuote(pid)`；后者 POST 到
  `/api/projects/{pid}/requirement/packaging-quote/send`（前端源码里出现
  `packaging-quote/send` 这个字面量）。
- **没有在前端抄一份写权限集合**（§2.3）：按钮只按"成本算出来了没有"开关，
  权限一律由后端 `HANDOFF_WRITE_ROLES` 裁决，拒绝时把人话原样弹出来。
- 两类 409 是可操作的，按后端给的 `code` 问一句再重发，**不猜、不自动放行、不替用户写理由**：
  - `cost_gaps_unresolved` / `gap_reason_required` → `prompt()` 问放行原因，
    带 `allow_gaps: true` + `reason` 重发；
  - `no_candidate` / `multiple_candidates` → `prompt()` 问新建原因，
    带 `create_new: true` + `create_reason` 重发（§2.3 的放行口径一个字未改）。
- `pcApi()` 只多做了**一件事**：把结构化 detail 的 `code` / `candidates` / `status`
  挂到抛出的 `Error` 上；`message` 与改动前逐字一致，既有调用点行为不变。

### 二、共享正文带整包（§2.2）

`tech_app/backend/services/cost_flow.py`：

- 新增私有 helper `_packaging_package_of(project_id)`：只调
  `packaging_handoff.handoff_package(project_id)` 取**既有那份装配结果**（10 段唯一装配处），
  `cost_flow` 里**没有**第二份整包拼装逻辑（§2.2）；非包装 / 成本未测算 / 任何业务拒绝
  一律返回 `{}`（不新增任何前置条件，也不改拒绝时机）。
- `integration_quote_result()` 末尾：整包非空时补 `result["packaging_package"] = package`；
  为空时**这个键不出现** —— 非包装项目的正文与改动前逐字一致。
  这条正文同时喂 `cost-review/send-to-quote` 与 `process-report/send-to-quote`（§2.2）。

### 三、实测

```
tests.test_packaging_quote_send_button_entry_red        → Ran 6 OK（原 2 红全绿）
tests.test_quote_card_step_snapshot_merge_red           → Ran 5 OK
node --check tech_app/frontend/requirement-confirm.js   → 通过
tests.test_packaging_quote_close_loop_red               → Ran 96 OK
tests.test_packaging_box_type_matching_red              → Ran 51 OK
tests.test_packaging_cost_rule_routing_red              → Ran 32 OK
tests.test_packaging_process_route_red                  → Ran 57 OK
tests.test_packaging_parametric_bom_red                 → Ran 57 OK
tests.test_packaging_downstream_block_code_http_red     → Ran 10 OK
tests.test_tech_handoff_atomic_idempotent_red           → Ran 35 OK
tests.test_tech_cost_report_handoff_continuity_red      → Ran 14 OK
tests.test_tech_quote_business_case_linkage_red         → Ran 39 OK
tests.test_packaging_quote_send_recovery_red            → 1 红（存量 ## 272，与本批无关）
```

行为复验（借用 `tests/test_packaging_quote_close_loop_red.py` 的夹具，真起临时 SQLite + meta）：

- `packaging_handoff.handoff_package(PID)` 出 **10 段**；
- `cost_flow._packaging_package_of(PID)` 在同一个已备好的包装项目上出**同样 10 段**，
  且 `package_fingerprint()` 与交接包的指纹**相同**（证明它确实取的是同一份整包，不是另拼的）；
- 非包装项目 id 上返回 `{}`（不抛异常）。

### 四、边界与已记录的偏差

1. **没有新增前端路由**，也没有改 `main.py`：只复用既有 `PACKAGING_QUOTE_SEND_PATH`（§3）。
2. 按钮只有一颗，落在**技术侧包装成本面板**（1.1/1.2）。报价首页/卡片页仍不出现这颗按钮 ——
   Spec §2.1 只要求"成本 / 报价相关页面上能点到"，且 §3 明确"任选放按钮的页面"；
   卡片侧要靠的是 §2.2 的随行整包（成本复核页那颗 `#crToQuote` 也会带上整包）。
3. `tests.test_packaging_quote_send_recovery_red::CMetaRecovery::test_c1` 仍红：那是存量
   `## 272` 的缺口（落点恢复的界面入口），本批只补"整包回传"这一颗按钮，不顺手改它。
4. 34 上**未**部署、未 push：本批只改本地工作区并提交。

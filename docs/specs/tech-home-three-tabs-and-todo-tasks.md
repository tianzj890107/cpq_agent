# Spec：技术工艺首页三页签与待办任务口径（批次 ## 139）

状态：Spec + 红测（已实现）（本 Spec 取代 `tech-home-timeline-and-publish-closure.md` §7.4 里「首页五个入口（我的项目 / 全部项目 / 待办任务 / 最近访问 / 已归档）」的产品决定；那个文件的其它契约（项目卡片字段、时间线、发布收口）继续有效）
红测：`tests/test_tech_home_three_tabs_and_todo_tasks_red.py` `tests/test_tech_home_timeline_and_publish_closure_red.py`

## 1. 背景与真实问题

用户口径（原文要点）：

> 现在的技术工艺的首页怎么被改的我看不懂了，应该是和报价一样的三个页签而不是五个，
> 反正后两个里面也没内容；然后待办的卡片应该自己的样式，而且待办怎么现在是 28 个，
> 应该报价转交过来的才是待办，就是反正有人转交给这个人的才是这个人的待办，或者是
> 转交到谁都能领取的池子里。报价和工艺的逻辑都应该是这样的。

### 1.1 现状（只读实测，不是推断）

**五页签**：`tech_app/frontend/tech-home-board.js:15-19` 的 `ENTRIES` 是
`mine / all / todo / recent / archived` 五项，`报价首页.html:2281` 用它渲染技术工艺的页签。
node 实跑该模块（`global.window = {}` + `require`）得到：

```
ids     = ["mine","all","todo","recent","archived"]
labels  = ["我的项目","全部项目","待办任务","最近访问","已归档"]
sources = ["server","server","server","local","server"]
```

「最近访问」是本机 localStorage 排序（默认空），「已归档」只在项目被归档后才有内容 ——
用户看到的就是两个常年空着的页签。

**待办口径错位**：报价侧「待办任务」读 `GET /wf/tasks`（`cpq_wf.inbox`），是
「别人转交给我的任务 / 定向我角色的任务 / 公共池任务 / 我已领取未完成的任务」
（`报价首页.html:1905-1927`、`cpq_wf.py:1408-1432`）。技术工艺侧却在
`报价首页.html:1948-1951` 被显式改道：

```js
// 技术工艺的「待办任务」按批次 10 §6.2 改走 scope=todo 的项目清单（只列我此刻能动手的）
if (mode === 'quote' && tab.indexOf('待办任务') === 0) { renderTaskTab(q, info, ctrl); return; }
```

于是技术工艺的「待办任务」= `GET /api/projects?scope=todo`
（`tech_app/backend/services/project_access.py:334-337`：有 `primary_action`
且当前子步骤对我 `actionable` 的项目）。这是**项目清单**，不是「有人转交给我的活」：
我参与过的、我只看得到但没人交给我的项目都会算进去，条数天然远大于真实待办。

**页签与徽标互相打架**：`报价首页.html:1604` 的 `wfBadge()` 会把页签文字改写成
`待办任务 (N)`，而 `报价首页.html:2220-2224` 的 `techEntryId()` 用**逐字相等**匹配页签文案：

```js
for (let i = 0; i < rows.length; i++) if (rows[i].label === tab) return rows[i].id;
return 'mine';
```

node 实跑（同一次探针）：

```
techEntryId('待办任务')     = 'todo'
techEntryId('待办任务 (28)') = 'mine'   ← 徽标一挂上，待办页签就解析成「我的项目」
```

即：用户点「待办任务」看到的是「我的项目」的内容，而页签上的 28 又是报价工作流
`WF.tasks` 的条数 —— 数字、页签、内容三者互不对应。

**卡片样式**：技术工艺「待办任务」走 `renderCards` 的普通分支，渲染的是项目卡
`cardHtml()`（`.request-card`，无 `wf-task` 标记）；报价侧待办渲染的是任务卡
`taskCardHtml()`（`.request-card.wf-task`，带来源 / 第 N 步 / 领取 CTA）。两侧不同构。

## 2. 用户角色与用户故事

- **工艺经理 / 工艺工程师**：打开技术工艺首页，只想知道「**有没有人把活交给我**」。
  看到三个页签就够了：我的项目、待办任务、全部项目。
- **财务经理**：被销售 / 工艺经理转交「成本测算」任务后，在待办里能领取并直接进成本工作台。
- **销售经理**：转交任务给公共池后，任何有资格的人领取；领取后其他人的待办里不再出现。
- **任何人**：不需要「最近访问」「已归档」占据首页一级入口。

## 3. 当前流程 → 目标流程

| # | 当前行为 | 目标行为 | 验收证据 |
|---|---|---|---|
| 1 | 技术工艺首页 5 个页签（含「最近访问」「已归档」） | 固定 3 个页签：`我的项目 / 待办任务 / 全部项目`（与报价 `我的报价 / 待办任务 / 全部报价` 同构） | 本批 §A1/A2 红测 |
| 2 | 技术工艺「待办任务」读 `scope=todo` 项目清单 | 读 `GET /wf/tasks`，与报价侧**同一接口、同一批数据** | §B 红测 + §A7 静态接线 |
| 3 | 页签徽标改写文案后 `techEntryId` 失配 → 待办页签显示「我的项目」 | `techEntryId('待办任务 (N)')` → `todo`；徽标只影响显示，不影响路由 | §A3 红测 |
| 4 | 待办卡 = 项目卡（`.request-card`，无 `wf-task`） | 待办卡 = 任务卡（`.request-card.wf-task` + 来源 / 第 N 步 / 领取 CTA） | §D 红测 |
| 5 | 报价侧与工艺侧各写一套待办过滤 | 唯一纯函数 `TechHomeBoard.isTodoTask / todoTasks / todoCount`，两侧都调它 | §C 红测 |
| 6 | 「已归档」靠独立页签触达 | 「全部项目」页签内提供「包含已归档」开关（`scope=all&include_archived=true`）；后端 `scope=archived` 保留 | §A6 红测 |

## 4. 状态定义

**待办任务（计入 count）**：

- `status === 'open'` 且 `from_user_id !== 我` 且满足下列任一：
  - `target_type === 'public'`（谁都能领取的公共池）
  - `target_type === 'role'` 且 `target_role_code === 我的角色`
  - `target_type === 'user'` 且 `target_user_id === 我`
- `status === 'claimed'` 且 `claimed_by_user_id === 我`

**不计入待办**：

- `status` 为 `completed` / `cancelled`（含「被新任务替代」的终态行：它们仍可留在列表里给出
  明确出口，但**不算待办**，不参与徽标计数）；
- 我发起但还没有人领取的 open 任务（`from_user_id === 我`）；
- `claimed` 但领取人不是我的任务；
- 不是任务的东西：我创建 / 我参与 / 我仅可读的项目、`scope=todo` 项目清单、历史归档项目。

**页签状态**：

| 页签 id | 文案 | 数据源 | 空态文案要求 |
|---|---|---|---|
| `mine` | 我的项目 | `GET /api/projects?scope=mine`（`owner === 当前登录用户名`） | 「你名下还没有技术工艺项目…到「待办任务」领取同事转交的卡片」 |
| `todo` | 待办任务 | `GET /wf/tasks` | 「暂时没有分派给你的任务。」 |
| `all` | 全部项目 | `GET /api/projects?scope=all`（开启开关时 `&include_archived=true`） | 「还没有数据…」 |

## 5. 接口与数据契约

### 5.1 `TechHomeBoard`（`tech_app/frontend/tech-home-board.js`）

```js
window.TechHomeBoard = {
  ENTRIES: [
    { id: 'mine', label: '我的项目', scope: 'mine', source: 'server' },
    { id: 'todo', label: '待办任务', scope: '',     source: 'tasks'  },
    { id: 'all',  label: '全部项目', scope: 'all',  source: 'server' }
  ],
  RECENT_KEY: 'tech:recentProjects',   // 保留：只做本机排序，不再产生页签
  entries(): Array<Entry>,             // 恰好三项，顺序固定 mine → todo → all
  cardOf(row), stageText(card), waitingText(card),   // 既有契约不变
  rememberRecent(projectId), localRecent(),          // 既有契约不变

  // 本批新增：待办唯一口径
  isTodoTask(task, user): Boolean,
  todoTasks(tasks, user): Array<Task>,   // 过滤，不改元素
  todoCount(tasks, user): Number         // === todoTasks(...).length
};
```

- `ENTRIES` / `entries()` **不得**再出现 `recent` / `archived` 入口，也不得有
  `source: 'local'` 的页签。
- `user` 形状：`{ user_id, role_code }`（`cpqAuth.user()` 的子集）。
- 比较一律按字符串：任务行的 `from_user_id` / `target_user_id` / `claimed_by_user_id`
  由后端 `_uid()` 归一化成字符串（`cpq_wf.py:333`），`user.user_id` 也按字符串比。
- 任务行缺字段（历史数据 / 部分响应）时按「不计入」处理，不能抛异常。

### 5.2 `报价首页.html`（技术工艺模式）

- 技术工艺页签仍然只由 `techEntries()`（即 `TechHomeBoard.entries()`）产出。
- `techEntryId(label)` 必须容忍徽标：先取 `label` 里第一个空白前的部分，或直接
  `indexOf(rows[i].label) === 0`；`'待办任务 (28)'` → `'todo'`。
- `renderCards()` 的「待办任务」分支**不得**再限定 `mode === 'quote'`：
  报价与技术工艺都走 `renderTaskTab(q, info, ctrl)`，列表项由 `taskCardHtml()` 渲染。
- 技术工艺不得再加载 `scope=todo` 的项目清单：`TECH_SCOPE_ROWS` 不再持有 `todo`，
  `techScopedRows()` 不再为 `todo` 调 `techLoadScope()`。
- 「全部项目」页签必须仍能读到归档项目：请求 `/api/projects?scope=all&include_archived=true`
  （一个「包含已归档」开关或等价入口）。后端 `scope=mine|all|todo|archived` +
  `include_archived` 的参数语义一个字都不改。
- `wfBadge()` 的待办数字与技术工艺待办列表条数必须同源：
  `TechHomeBoard.todoCount(WF.tasks, 我)`。

## 6. 正常路径

1. 工艺经理登录 → 首页三个页签，默认停在「我的项目」。
2. 销售把一张报价卡转交给「公共池」→ 工艺经理的「待办任务」出现 1 张任务卡，
   徽标数字 `1`，页签文案变成 `待办任务 (1)`。
3. 点页签 → 列表里是该任务卡（来源、客户、第 N 步、CTA「领取并继续」），不是项目卡。
4. 点击卡片 → `wfClaimAndOpen()` 领取并跳到对应工作台（`tech_new_product` → 1.1；
   `tech_cost` → 成本；`tech_cost_return` → 工艺评估报告；其余 → 报价页）。
5. 领取后该任务 `status='claimed'`，仍留在我的待办里（换 CTA 为「继续处理」）；
   完成后不再出现、徽标数字减一。
6. 「我的项目」「全部项目」内容与样式与本次改动前逐字一致。

## 7. 异常路径

- **未登录**：三个页签都在；「待办任务」显示「登录后可查看同事转交给你的任务」，
  不请求 `/wf/tasks`，**不得**回落到项目清单。
- **`/wf/tasks` 失败**：显示真实错误（`SESSION_ERROR` / 接口 message），
  不得静默显示成项目清单，也不得把数字显示成 0。
- **`/api/projects` 失败**：沿用既有「读取失败：<原因>」空态。
- **任务行缺 `target_type` / `status`**：不计入待办，不抛异常。
- **同名项目与任务**：待办列表只按任务行渲染，不混入项目行。

## 8. 并发与幂等

- 徽标计数是纯函数，连续调用 `wfBadge()` / `renderCards()` 结果一致，
  不得累加（`待办任务 (3) → 待办任务 (3)`，不是 `(3) (3)`）。
- 刷新 / 切页签 / 切模式后，`待办任务` 的条数与内容都由服务端重新计算，不用本地缓存。
- 公共池任务被他人领取后，**刷新即消失**：服务端 `cpq_wf.inbox()` 只返回
  `status='open'` 的公共任务与 `claimed_by_user_id = 我` 的已领任务。前端不得用
  localStorage / 内存里的旧列表把已被他人领走的任务重新画出来。
- 领取接口本身（原子领取、只有一人成功）属批次 2 契约，本批**不改**。

## 9. 刷新、重试、重复点击、服务重启后的行为

- 刷新：页签回到 3 个；待办列表重新拉取；徽标数字来自重新拉取的数据。
- 重复点击卡片：沿用 `wfClaimAndOpen()` 的既有行为（已领取 → 直接打开，不再领）。
- 服务重启 / 后端不可达：`/wf/tasks` 报错走 §7 的真实错误空态；不得显示成「暂时没有」。
- `localStorage` 只保留 `tech:recentProjects` 的排序能力，不再是任何页签的数据源。

## 10. 权限边界

- 「我的项目」= `owner === 当前登录用户名`（后端口径，不用 localStorage 猜）。
- 「待办任务」= §4 的任务口径；公共池任务任何人可领取，定向任务只有命中者能看到
  （`cpq_wf.task_detail` 的可见性判定不变）。
- 「全部项目」= 批次 7 的 `project_access` 可见范围；本批不改 ACL。
- 侧边「历史 Drawer」、`scope=todo`、`scope=archived` 后端能力全部保留，本批只改首页入口。

## 11. 历史数据兼容

- 不迁移、不删除、不清空任何项目 / 任务 / 会话 / 附件。
- 历史任务行（缺 `replaced_by_task_id` 的静默取消）继续不进任何人的待办
  （批次 2 契约，本批不改）。
- 旧链接 `tech_app/frontend/home.html?...` 继续 302 到 `报价首页.html?assistant=tech`。
- `TechHomeBoard.rememberRecent / localRecent / RECENT_KEY` 保留导出，老调用方不报错。

## 12. 非目标

- 不改 `cpq_wf` 的任务并存 / 领取 / 转交 SQL 与状态机（批次 2）。
- 不删后端 `scope=todo` / `scope=archived` / `include_archived` 能力（批次 7 / 10）。
- 不改报价侧「我的报价 / 全部报价」的数据源与项目卡样式。
- 不改项目卡 `cardHtml()` 的字段、布局、高度。
- 不改时间线、报告发布收口、权限模型、数据库结构、Prompt、SSE。
- 不做「最近访问」的新入口设计（本批只把它从页签上摘下）。

## 13. 可自动化验收标准

1. `TechHomeBoard.entries()` 恰好 3 项，id 顺序 `mine / todo / all`，
   文案 `我的项目 / 待办任务 / 全部项目`，全部 `source !== 'local'`。
2. `todo` 入口 `source === 'tasks'`，且不携带项目 `scope`。
3. `TechHomeBoard.isTodoTask / todoTasks / todoCount` 存在，且 §4 的计入 / 不计入矩阵逐条成立。
4. `techEntryId('待办任务 (28)') === 'todo'`，`techEntryId('全部项目') === 'all'`。
5. `renderCards()` 的「待办任务」分支不再要求 `mode === 'quote'`。
6. `报价首页.html` 中技术工艺不再为 `todo` 加载项目清单（`TECH_SCOPE_ROWS` 无 `todo`）。
7. 「全部项目」能带 `include_archived=true`；后端 `scope=mine|all|todo|archived` 仍全部 200。
8. `taskCardHtml()` 产出带 `wf-task`（任务卡），`cardHtml('tech', …)` 产出不带 `wf-task`（项目卡）。
9. `cpq_wf.inbox()`：我发起未领取的 open 任务、终态任务、我名下但没有任务的项目
   都不在待办里；公共池 / 定向我的角色 / 定向我 / 我已领取的都在。
10. `wfBadge()` 的待办数字与技术工艺待办列表条数同源（同一个 `todoCount`）。

## 14. 人工验收场景

1. 用工艺经理账号打开 `报价首页.html?assistant=tech`：只有三个页签
   「我的项目 / 待办任务 / 全部项目」。
2. 用销售账号转交一张卡片到「公共池」→ 回到工艺经理首页刷新：
   待办任务页签出现数字，点进去是**任务卡**（有来源、第 N 步、领取按钮），不是项目卡。
3. 领取 → 卡片变成「继续处理」；完成该任务 → 数字减一、列表里不再有它。
4. 切到「我的项目」「全部项目」：内容与之前一致，卡片样式没有被任务卡带跑。
5. 归档一个项目 → 在「全部项目」里打开「包含已归档」仍能看到它。

## 15. 不允许减少的既有能力

- 项目卡的 owner / 阶段 / 在等谁 / 最后一次业务事件 / 异常 / 主操作字段（批次 10A）。
- 报价侧待办任务的全部能力：领取、跳转、终态出口、被新任务替代提示、备注。
- 历史 Drawer、`/api/projects` 四个 scope、`include_archived`。
- 「最近访问」的本机排序能力（`rememberRecent` 仍被 `openTechProject()` 调用）。
- 任务并发领取的原子性与单人成功保证。

## 16. Red Tests 对照表

`tests/test_tech_home_three_tabs_and_todo_tasks_red.py`

| 用例 | 覆盖 §  | 实测（14 条：10 红 / 4 绿） |
|---|---|---|
| `HomeTabsTest.test_board_exposes_exactly_three_tabs` | 5.1 | 红（现有 5 项） |
| `HomeTabsTest.test_todo_tab_is_backed_by_tasks_not_a_project_scope` | 5.1 | 红（现有 `source:'server'` + `scope:'todo'`） |
| `TodoScopeTest.test_is_todo_task_matrix` | 4 | 红（`isTodoTask` 不存在） |
| `TodoScopeTest.test_role_target_does_not_match_a_user_without_role` | 4 / 5.1 | 红（同上） |
| `TodoScopeTest.test_todo_tasks_and_count_agree` | 4 / 8 | 红（`todoTasks` / `todoCount` 不存在） |
| `TechTabRoutingTest.test_tech_entry_id_resolves_a_badged_todo_tab` | 5.2 | 红（`'待办任务 (28)'` → `'mine'`） |
| `TechTabRoutingTest.test_todo_branch_is_not_quote_only` | 5.2 | 红（现在限定 `mode === 'quote'`） |
| `TechTabRoutingTest.test_tech_mode_no_longer_serves_a_todo_project_list` | 5.2 | 红（`TECH_SCOPE_ROWS.todo` 仍在） |
| `TechTabRoutingTest.test_archived_still_reachable_from_all_tab` | 5.2 | 红（首页没有 `include_archived`） |
| `TodoCardShapeTest.test_quote_and_tech_todo_share_one_count_source` | 5.2 | 红（`wfBadge` 未走 `todoCount`） |
| `TodoCardShapeTest.test_task_card_and_project_card_are_distinguishable` | 3-4 / 5.2 | 绿（守卫项：任务卡与项目卡本就不同，防回归） |
| `InboxSourceTest.test_inbox_holds_exactly_what_was_handed_to_me` | 4 | 绿（守卫项：批次 2 的 `inbox()` 已满足） |
| `InboxSourceTest.test_terminal_tasks_are_not_todos` | 4 | 绿（守卫项） |
| `InboxSourceTest.test_project_ownership_is_not_a_todo` | 4 | 绿（守卫项：`inbox()` 只认任务行） |

`tests/test_tech_home_timeline_and_publish_closure_red.py` 里「首页恰好五个入口」的断言属于
**被本 Spec 取代的旧产品决定**，本批把这**一条**用例更新为三页签；其余用例一字不动。

## 17. 风险与兼容策略

- **风险**：直接删掉 `recent` / `archived` 入口会让归档项目在首页失去出口。策略：归档改为
  「全部项目」内的开关，后端能力不动；本批红测断言 `include_archived` 可达。
- **风险**：把技术工艺待办切到 `/wf/tasks` 后，只做技术项目（没人转交）的人会看到空待办。
  这正是需求要的语义（待办=别人交给我的活），「我的项目」仍然可用。
- **风险**：`techEntryId` 改成前缀匹配后可能误命中（如「我的项目 A」）。策略：只匹配
  `rows[i].label` 作为前缀且下一个字符是行尾 / 空白 / `(`。

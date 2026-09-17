# 统一首页信息架构、跨流程时间线与报告发布收口（批次 10）

假设批次 1–9 已实现。本批是**产品体验收口批**，只做三件事，全部是「把已经算准的
状态送到用户眼前」：

* **10A 首页信息架构**：首页的五个入口（我的项目 / 全部项目 / 待办任务 / 最近访问 /
  已归档）都要真实存在；项目卡上的「谁的项目、现在第几步、在等谁、最后一次业务动作
  是什么、有没有异常」一律由后端算，前端不再自己拼状态。
* **10B 统一业务时间线**：一条从「报价创建」到「回传报价 → 销售继续报价」的完整事件流，
  每条事件都能说清**谁、什么角色、什么时候、做了什么、从哪个状态到哪个状态、点它能去哪**。
* **10C 报告发布收口**：发布完必须一眼看到「报告已发布 / 分发已留痕 / 是否已回传报价 /
  回传到哪张报价的第几步 / 没回传就点这里重试」；主操作随项目来源变化。

**非目标**（本批一律不做）：

* 不重新设计底层状态。阶段完成条件、能否执行、缺什么全部复用批次 5B 的
  `workflow_projection`；业务实例关联复用批次 6 的 `business_case`；可见范围与读写权
  复用批次 7 的 `project_access`；任务状态复用批次 9 的封闭词表。
* 不改报价侧 7 步流程语义、不改回传幂等契约（批次 3/4/6）、不改视觉基线与配色。
* 不新建「消息中心」；系统内收件人（账号/角色）产生消息这件事沿用既有通知，
  外部分发对象只留痕、不发消息。
* 首页的「最近访问」不做跨设备同步、不做服务端记忆 —— 它只是**本机导航**，
  永远不参与「这单是不是我的」的判断。

---

## 1. 背景与真实问题

### 1.1 五个入口里只有三个存在，「已归档」在首页没有出口

`报价首页.html:1558` 里技术工艺的标签是 `['我的清单', '待办任务', '全部清单']`。
没有「最近访问」，也没有「已归档」。

而归档项目在列表接口里就是被过滤掉的：`project_access.visible_projects(user, scope,
include_archived=False)`（`tech_app/backend/services/project_access.py:203`）默认不返回
`meta.deleted_at` 非空的项目，`GET /api/projects`（`tech_app/backend/main.py:1016`）
只认 `scope ∈ {mine, all, archived}`，没有 `todo`。

结果：用户找不到归档项目时，唯一办法是去翻**历史 Drawer**（`报价首页.html:1352-1360`，
标题写死「历史报价」）。历史 Drawer 是「最近浏览过的会话」，它**不是**首页漏掉的业务
项目的补救入口 —— 一个从没被这台浏览器打开过的项目，在 Drawer 里永远找不到。

### 1.2 卡片状态是前端自己拼的第二套口径

`报价首页.html:1752` 的 `statusOf(mode, s)` 里有一张 9 条的本地映射表
（`draft / pending_confirmation / pending_review / approved / published / rejected /
uploaded / parsed / tech_record` → 中文标签），把项目 `status` 字段直接翻成界面文案。

这与批次 5B 建立的**唯一**流程投影是两套口径：后端已经能用
`GET /api/projects/{project_id}/workflow/projection` 说清 13 个子步骤各自的
`status / completed / actionable / blocked_reasons / required_role / primary_action`，
首页却仍在读一个粗粒度的 `status` 字符串。

后果有两类，都会真实发生：

* 卡片说的「阶段」与工作台顶部说的「阶段」可能不一致（同一个项目两个答案）；
* 卡片上**没有** `owner`（谁的项目）、`waiting_for`（在等谁）、`last_event`
  （最后一次业务动作）、`anomaly`（有没有异常）—— 用户必须点进去才知道。

### 1.3 「最近访问」目前既当导航又当归属依据

`报价首页.html:1564-1565` 的 `MY_KEYS` / `OPEN_KEYS` 把「我的会话」「上次打开的会话」
写在本机 localStorage。批次 7 已经把「谁能看到哪个项目」收到后端
（`require_project_access`），但首页仍保留按本机标记过滤的习惯。

这不是本批新引入的问题，但本批必须把它钉死：**最近访问只能决定「先给你看哪几条」，
不能决定「这条是不是你的」。**

### 1.4 没有跨流程时间线

今天能读到的两块都不是业务时间线：

* `store.audit()`（`tech_app/backend/storage/store.py:101-106`）只写
  `{"ts", "action", "detail"}` 三个键 —— 没有 actor、没有角色、没有状态迁移、没有跳转目标；
* `GET /api/projects/{project_id}/agent/events`（`main.py:2423`）回放的是 Agent 会话流
  （任务卡 / 过程文字 / tech_ui 卡 / shell note），是「模型这段时间说了什么」，
  不是「这单业务上发生了什么」。

于是「这单从报价创建到回传报价都发生了什么」这个问题，今天只能跨报价首页、工作台、
报告页三个地方人工拼。

### 1.5 发布收口只到「报告已发布」，后面是断的

`report_workflow.publish_result()`（`tech_app/backend/services/report_workflow.py:584`）
返回 `{report, versions, quote_handoff}`：

* 没有「分发是否已留痕」（`distribution_recipients()` 是另一个接口）；
* 没有「回传到哪张报价、那张报价现在第几步」；
* 没有「回传失败时的重试入口」；
* 没有「主操作应该是返回报价 / 查看报告 / 创建新版本」的判断。

`report-publish-result.js` 只能自己看 `quote_handoff` 猜，猜错就会出现「报告已发布，
但用户不知道下一步点哪里」。

---

## 2. 已实现部分（本批不重复建设）

| 能力 | 落点 | 本批用法 |
|---|---|---|
| 统一流程投影（5 阶段 × 13 子步骤） | `services/workflow_projection.py:399` `build_projection`；`GET /api/projects/{pid}/workflow/projection`（`main.py:6458`） | 首页卡片的 `stage` / `primary_action` / `waiting_for` **唯一来源** |
| 业务实例关联（`business_case_id` / `quote_session_id` / `source_task_id`） | `store.load_business_case` / `save_business_case`（`store.py:352-382`） | 时间线里的报价侧事件、发布收口里的「返回原报价」 |
| 可见范围与项目级 ACL | `services/project_access.py`；`GET /api/projects?scope=mine/all/archived` | 首页四个清单范围的唯一判定；时间线与收口接口同样先过它 |
| 任务状态封闭词表 + 取消 + 追踪 ID | `services/tasks.py`（批次 9）；`/api/projects/{pid}/tasks*` | 卡片的 `anomaly`（interrupted / failed）、时间线的任务事件 |
| 回传原子闭环与幂等 | `cpq_tech_bridge.py` / `services/cost_flow.py` / `services/report_workflow.send_to_quote`（批次 3/4/6） | 收口里的 `handoff.handoff_id` / `target_quote` |

---

## 3. 用户角色与用户故事

角色沿用 `auth.ROLES` 与 `cpq_sso.ROLE_MAP` 的并集口径：`sales_manager`（含
`cpq_role_code == "sales_mgr"`）、`process_manager`、`finance_manager`、
`process_director`、`reviewer`、`general_manager`、`engineer`、`admin`。

* **US-1（销售经理）**：我上午交了一单给工艺，下午回到首页，在「最近访问」第一条就能
  点回去；卡片上直接写着「等工艺经理」。
* **US-2（销售经理）**：我要找三个月前那单已完结的项目，「已归档」里能翻到，
  不需要去历史 Drawer 里一条条试。
* **US-3（技术工艺经理）**：「待办任务」只列**需要我动手**的；别人正在做的不出现。
* **US-4（任何有权限的人）**：打开一个项目，看一条完整时间线，能一眼看出「上一手是谁、
  什么时候交的、交过来之后技术侧做了什么」。
* **US-5（发布人）**：发布完，屏幕上明确写着「已发布 · 已分发 3 个对象 · 已回传报价
  BJD-2026-0007 第 3 步」；如果回传失败，写着失败原因和一个「重新回传报价」按钮。
* **US-6（工艺技术总监）**：报告已发布、来自报价的项目，主按钮是「返回原报价继续」；
  独立技术项目，主按钮是「查看已发布报告」。

---

## 4. 当前流程

1. 打开首页（`报价首页.html`）→ 按 `mode=tech` 调 `window.cpqAuth.api('/api/projects')`
   （`报价首页.html:1848`），拿到项目数组。
2. 前端用 `statusOf()` 的本地映射表算「状态」文案，用 `cardHtml()` 渲染卡片
   （编号 / 状态 / 标题 / 评估对象 / 时间 / 轮数），没有阶段、没有等待方、没有异常。
3. 点卡片 → `openTechProject()`（`报价首页.html:1684`）并发拉 `workflow`、项目详情、
   `summary`、`cost-review`，再按本地信号推 stage。
4. 找不到项目 → 打开历史 Drawer（标题写死「历史报价」）。
5. 报告发布 → `publish_result` 只给 `{report, versions, quote_handoff}`，
   页面自己判断显示什么。

## 5. 目标流程

1. 打开首页 → 五个入口并列：**我的项目 / 全部项目 / 待办任务 / 最近访问 / 已归档**。
   「最近访问」是本机顺序，「已归档」走 `scope=archived`，其它三个走
   `scope=mine / all / todo`。
2. 列表接口在每一行上给出 `card`（见 §7.1），前端**只渲染**，不再映射状态。
3. 点卡片 → 用 `card.primary_action.target` 直接恢复真实 project / stage / task。
4. 项目页提供「业务时间线」（见 §7.2），跨报价—技术—财务—报告。
5. 报告发布后，「发布结果」给出 `closure`（见 §7.3），主操作随来源变化。

---

## 6. 状态定义与状态转换

### 6.1 首页卡片上的四个派生字段

* **`card.stage`**：取自批次 5B 投影 —— 第一个未完成的子步骤（`next_action`）；
  全部完成时取最后一个子步骤。**不得**再由前端的 `status` 映射表推导。
* **`card.waiting_for`**：`kind` 三选一 ——
  * `none`：没有未完成步骤（已收口）；
  * `role`：当前该做的子步骤 `required_role` 命中某个角色，且项目里没有具体领取人；
  * `user`：该项目存在 `open` / `running` 任务且有明确领取人（`claimed_by`）时，
    等这个人。
* **`card.last_event`**：该项目业务时间线（§7.2）里 `at` 最大的一条的
  `{action, at, actor}`；时间线为空时取 `created_at` + `project_created`。
* **`card.anomaly`**：`has_anomaly` 为真当且仅当满足任一条 ——
  存在 `interrupted` / `failed` 任务；投影 `refresh_ok == false`；
  有子步骤 `status == "stale"`；最近一次回传 `sent == false` 且 `error` 非空。
  `codes` 是**稳定**的机器码集合（有序），供前端显示与自动化断言。

### 6.2 清单范围

| scope | 含义 | 是否含归档 |
|---|---|---|
| `mine`（默认） | 我创建 / 我当前持有 / 我参与 / 分派给我的业务实例（批次 7 的四类来源） | 否 |
| `all` | 我权限范围内的全部（不是全公司） | 否 |
| `todo` | 只包含**当前用户可执行动作**的项目，即 `card.primary_action` 存在且其 `required_role` 命中我 | 否 |
| `archived` | 我有权限的归档项目（批次 7 已定义；所有人只读） | 是（只有归档） |

非法 `scope` → 400（沿用批次 7）。

### 6.3 发布收口状态

`closure.state` ∈ `draft` / `awaiting_review` / `approved` / `published` /
`handoff_pending` / `handed_off` / `handoff_failed` / `revised`，由报告状态与回传结果
共同决定：

```
draft
  └─→ awaiting_review ─→ approved ─→ published ─┬─→ handed_off
                                                └─→ handoff_failed ─→ handed_off
draft ─→ revised（new_version 后回到 draft，version+1）
```

* `published` 与 `handed_off` 是**两个可区分**的状态（批次 5 的口径：发布 ≠ 已回传）。
* `handoff_failed` 必须带 `handoff.error` 与 `retry_action`。

---

## 7. 接口与数据契约

### 7.1 `GET /api/projects?scope=mine|all|todo|archived`

在批次 7 的每行结构（含 `access`）之外，**新增** `card` 块：

```json
{
  "project_id": "7393f6a00ccc",
  "project_name": "便携式锂电池 PACK",
  "owner": "alice",
  "owner_display_name": "爱丽丝",
  "created_at": "...", "updated_at": "...",
  "deleted_at": null,
  "has_ir": true,
  "access": { "scope": "mine", "mine_sources": ["owner"], "can_read": true, "can_write": true },
  "card": {
    "owner": "alice",
    "owner_display_name": "爱丽丝",
    "stage": { "code": "4.1", "index": 10, "total": 13, "title": "零件成本", "phase": 4 },
    "waiting_for": { "kind": "role", "role": "finance_manager", "label": "财务经理", "username": "" },
    "last_event": { "action": "integration_send_to_finance", "at": "2026-09-17 14:02:11", "actor": "pm" },
    "anomaly": { "has_anomaly": false, "codes": [] },
    "business_case_id": "bc_0f1e2d3c4b5a",
    "primary_action": {
      "id": "open-stage",
      "label": "去 4.1 零件成本",
      "target": "tech-workbench.html?stage=cost-review&project=7393f6a00ccc"
    }
  }
}
```

* `card` 一律由**后端**算，判据全部来自 §6.1；前端不得再维护 `status → 中文` 的映射表。
* `stage.index` / `stage.total` 与投影的 13 个子步骤一致；`stage.code` 用子步骤号，
  与工作台顶部完全同源。**口径以 `tech_app/backend/services/workflow_stages.py` 为准**
  （五阶段 × 13 子步骤）：

  | 阶段 | 子步骤 |
  |---|---|
  | 1 工艺评估需求 | 1.1 创建需求 / 1.2 确认需求 / 1.3 审核需求 |
  | 2 图纸解析 | 2.1 图纸解析 |
  | 3 组装与整合 | 3.1 整合图纸 / 3.2 参数推荐 / 3.3 组装工艺 |
  | 4 成本测算 | 4.1 零件成本 / 4.2 组装成本 / 4.3 汇总 |
  | 5 工艺评估报告 | 5.1 汇总结果 / 5.2 结果审核 / 5.3 发布并回传报价 |

  **成本测算是第 4 阶段**（4.1 / 4.2 / 4.3），本文档与红测里不得再出现旧编号写法
  （成本测算曾被写成第 2 阶段的第二步/第三步，本批起一律改称第 4 阶段）。
* `primary_action` 可为 `null`（已收口 / 无可执行动作）；`target` 必须能直接打开
  「真实 project + 真实 stage」。
* `primary_action.required_role` 用**角色码**（`engineer` / `process_manager` /
  `finance_manager` / `process_director` / `reviewer` / `general_manager` / `admin`），
  与 `auth.ROLES` 同源。批次 5B 的投影给的是中文标签（如 `"工艺工程师"`），
  卡片层必须**归一化**成角色码，供 `scope=todo` 直接比对。
* 排序：`last_event.at` 降序；缺失时按 `updated_at` 降序；同值时按 `project_id` 升序
  保证稳定。
* 既有字段（`project_id` / `project_name` / `owner` / `deleted_at` / `access` …）一个都不能删。

### 7.2 `GET /api/projects/{project_id}/timeline`

```json
{
  "project_id": "7393f6a00ccc",
  "business_case_id": "bc_0f1e2d3c4b5a",
  "generated_at": "2026-09-17 15:00:00",
  "events": [
    {
      "seq": 1,
      "at": "2026-09-15 09:12:00",
      "actor": "sales1",
      "actor_display": "王销售",
      "role": "sales_manager",
      "action": "quote_created",
      "label": "报价创建",
      "action_kind": "quote",
      "from_state": "not_started",
      "to_state": "in_progress",
      "project_id": "7393f6a00ccc",
      "session_id": "S-2026-0007",
      "task_id": "",
      "version": "",
      "business_case_id": "bc_0f1e2d3c4b5a",
      "target": { "kind": "quote", "label": "打开原报价", "url": "/?assistant=quote&session=S-2026-0007" }
    }
  ]
}
```

* `action_kind` ∈ `quote` / `task` / `tech` / `cost` / `report` / `handoff`。
* **必须能出现**的事件（每条都要有上面那些键，取值按真实业务填）：
  `quote_created`、`quote_step_completed`、`tech_branch_started`、`task_sent`、
  `task_claimed`、`task_cancelled`、`task_completed`、`task_failed`、
  `task_interrupted`、`drawing_parsed`、
  `params_confirmed`、`process_confirmed`、`cost_calculated`、`cost_confirmed`、
  `cost_returned`、`report_submitted`、`report_reviewed`、`report_published`、
  `report_sent_to_quote`、`quote_resumed`。
* 每条事件：`actor` / `role` / `at` / `action` / `target` 均**非空**；
  `target.url` 必须能跳到真实页面（阶段 / 报价 / 报告 / 任务）。
* 顺序：`at` 升序；同一 `at` 按 `seq` 升序，稳定可复现。
* 只读、幂等：连续调用两次，除 `generated_at` 外结果逐字相同。
* 历史项目没有 `business_case_id` 时，顶层与每条事件都给 `""`，
  **不得现编**实例号（批次 6 口径）。
* 不可见 / 不存在的项目 → 404「项目不存在」（沿用批次 7，逐字一致）。

### 7.3 `GET /api/projects/{project_id}/process-report/publish-result`

既有键 `report` / `versions` / `quote_handoff` **全部保留**，新增 `closure`：

```json
{
  "report": { "…": "既有结构不变" },
  "versions": [ "…" ],
  "quote_handoff": { "…": "既有结构不变" },
  "closure": {
    "state": "handed_off",
    "published": true,
    "published_at": "2026-09-17 11:20:00",
    "published_by": "pm",
    "version": 1,
    "distributed": {
      "recorded": true,
      "scope": "工艺、质量、生产",
      "cc": "项目组",
      "internal_recipients": [
        { "kind": "user", "id": "zhangzhen", "label": "张真" },
        { "kind": "role", "id": "finance_manager", "label": "财务经理" }
      ],
      "external_targets": ["张三 <zhangsan@example.com>"]
    },
    "handoff": {
      "sent": true,
      "handoff_id": "hf_9c1d2e3f4a5b",
      "target_quote": {
        "session_id": "S-2026-0007",
        "card_id": "7393f6a00ccc",
        "current_step": 3,
        "current_step_label": "定价-利润加成"
      },
      "error": "",
      "retry_action": null
    },
    "primary_action": {
      "id": "back-to-quote",
      "label": "返回原报价继续",
      "target": "/?assistant=quote&session=S-2026-0007"
    },
    "anomaly": { "has_anomaly": false, "codes": [] }
  }
}
```

规则：

1. **两个收件人集合必须分开**。分类口径（**不新增数据字段**，按既有
   `ReportRecipient{name, organization, contact, channel}` 判定）：
   * `internal_recipients` —— `channel` 为空或等于 `"平台通知"`，且 `name` 能解析到
     系统内对象：命中用户名（`store.get_user`）时 `kind="user"`、`id=<username>`；
     命中 `auth.ROLES` 的中文角色名时 `kind="role"`、`id=<role 码>`。
     `label` 分别是显示名 / 角色名。**必须产生站内消息**。
   * `external_targets` —— 其余全部（自由文本）。格式 `"{name} <{contact}>"`，
     `contact` 为空时只写 `name`。**只留痕、不发消息**。
   * 两者不得互相混入，也不得把同一个对象同时写进两边；`external_targets` 里
     不得出现用户名或角色码，`internal_recipients` 里不得出现 `@` 地址。
2. **未发布**：`published=false`、`state ∈ {draft, awaiting_review, approved}`、
   `handoff.sent=false`、`handoff.error=""`、`primary_action` 指向**下一步**
   （如「去 3.2 报告审核」），`retry_action` 为 `null`。
3. **已发布未回传**：`state="published"`、`published=true`、`handoff.sent=false`、
   `handoff.error=""`、`primary_action.id="view-report"`、`retry_action` 为 `null`
   （还没试过，不叫失败）。
4. **回传失败**：`state="handoff_failed"`、`handoff.sent=false`、`handoff.error` 非空、
   `retry_action = {"id": "retry-handoff", "label": "重新回传报价"}`，
   `anomaly.has_anomaly=true` 且 `codes` 含 `handoff_failed`。
5. **回传成功**：`state="handed_off"`、`handoff.sent=true`、`target_quote.session_id`
   非空、`current_step` ≥ 2 且 `current_step_label` 非空。
   `handoff.handoff_id` 取批次 3 的交接号；历史数据没有这个号时给 `""`，不现编。
6. `primary_action.id` 由**来源**决定：
   * `business_case.quote_session_id` 非空（来自报价）→ `back-to-quote`；
   * 独立技术项目 → `view-report`；
   * 报告已发布且 `new_version` 已被调用 → `new-version`。
7. 该接口只读、幂等：连续两次调用除 `generated_at` 类字段外一致。
8. 不可见 / 不存在的项目 → 404「项目不存在」。

### 7.4 前端首页唯一口径模块 `tech_app/frontend/tech-home-board.js`

首页（`报价首页.html`）与项目页不得各自维护状态映射。新增一个模块承担两件事：
**入口定义**与**卡片渲染口径**。

```js
window.TechHomeBoard = {
  ENTRIES: [
    { id: 'mine',     label: '我的项目',   scope: 'mine',     source: 'server' },
    { id: 'all',      label: '全部项目',   scope: 'all',      source: 'server' },
    { id: 'todo',     label: '待办任务',   scope: 'todo',     source: 'server' },
    { id: 'recent',   label: '最近访问',   scope: '',         source: 'local'  },
    { id: 'archived', label: '已归档',     scope: 'archived', source: 'server' }
  ],
  RECENT_KEY: 'tech:recentProjects',
  entries(): Array<Entry>,                 // 返回上面五个，顺序固定
  cardOf(row): Object | null,              // 原样取 row.card；没有就返回 null，绝不现造
  stageText(card): String,                 // card 有效时 "2.2 组装工艺"；无效时 "—"
  waitingText(card): String,               // none -> ""；role/user -> waiting_for.label；无效 -> "—"
  rememberRecent(projectId): Array,        // 只写 localStorage，去重、最新在前、最多 20 条
  localRecent(): Array                     // 读回本机最近访问 id 列表
};
```

硬性要求：

* `entries()` 里**只有** `recent` 的 `source` 是 `'local'`，其余四个都是 `'server'`
  —— 也就是说「谁的项目」永远问后端，本机只记住「先给你看哪几条」。
* `cardOf(row)` 必须**原样返回** `row.card`。行上没有 `card` 时返回 `null`；
  模块里**不允许**存在 `status → 中文` 的兜底映射表（那是批次 10 要消灭的第二套口径）。
* `rememberRecent()` 只允许写 `localStorage[RECENT_KEY]`，不发起任何网络请求。
* 该模块必须在页面业务脚本之前加载（`报价首页.html` `<head>` 或首位 `<script>`），
  并带 `?v=` 缓存号。

---

## 8. 正常路径

1. 销售经理登录 → 首页「最近访问」第一条是他上午那单，`card.waiting_for = {kind:"role",
   role:"process_manager"}`。
2. 工艺经理登录 → 「待办任务」`scope=todo` 只返回 `card.primary_action.required_role`
   命中 `process_manager` 的项目；别人在做的项目不出现。
3. 项目走到 3.3「组装工艺」结束 → `card.stage.code = "4.1"`、
   `waiting_for.role = "finance_manager"`；卡片上的阶段与工作台顶部一致。
4. 财务经理做完 4.1 零件成本 / 4.2 组装成本 / 4.3 汇总 →
   `last_event.action = "cost_confirmed"`，`waiting_for` 变回工艺经理。
5. 工艺技术总监发布报告并回传 → `closure.state = "handed_off"`、
   `primary_action.id = "back-to-quote"`；销售在原报价第 3 步收到技术结果。
6. 半年后销售要找回这单 → 首页「已归档」能翻到，卡片只读。

## 9. 异常路径

* **回传失败**：发布成功但回传被拒 / 服务不可用 → `closure.state = "handoff_failed"`，
  页面同时显示「报告已发布」（不因回传失败而说报告没发布）与「回传失败 + 重试」。
* **任务中断**（批次 9）：`card.anomaly.codes` 含 `task_failed` / `task_interrupted`，
  卡片上直接可见，不需要点进去。
* **投影读不全**：`workflow_projection.build_projection` 的 `refresh_ok=false` →
  列表接口仍返回该行，但 `card.anomaly.codes` 含 `refresh_failed`，
  且 `card.stage` 取「上次已知」值，**不得**把整行丢掉、也不得把它当成「未开始」。
* **时间线缺 `business_case_id`**：历史项目照常返回事件，`business_case_id = ""`。
* **不可见 / 不存在**：列表里不出现；单项目接口 404「项目不存在」，
  与真正不存在的项目响应不可区分（批次 7 + 批次 9 的 `trace_id` 口径）。

## 10. 并发与幂等

* 三个接口**全部只读**，不产生写入；任何一次调用都不得改动项目、任务或报告数据。
* 列表接口在 meta 变更（归档 / 加参与者 / 任务状态变化）后**立即**反映，不需要重启。
* 时间线与收口接口连续调用两次必须一致（除 `generated_at`）。
* 并发：两个请求同时读同一项目不得互相污染（不得复用可变缓存对象跨请求）。

## 11. 刷新 / 重试 / 重复点击 / 服务重启

* 刷新首页：`card` 全部重新算，不依赖任何前端缓存。
* 刷新项目页：时间线重新拉取，事件不重复、不丢失。
* 发布结果页在回传失败后重试：`retry_action` 调用的仍是既有
  `POST /api/projects/{pid}/process-report/send-to-quote`（幂等键不变），
  重试成功只产生**一条**交接记录。
* 服务重启：以上全部为**读路径**，重启后行为不变；归档标记、参与者、任务状态都落盘。

## 12. 权限边界

* 列表、时间线、发布结果三个接口都先过 `project_access`（批次 7）：
  不可读 → 该行不出现 / 404「项目不存在」。
* 时间线里的每条事件都可能涉及其他人（销售、财务、总监）。**能读到项目就能读到
  时间线**，不额外按事件做权限裁剪 —— 理由是这是同一个业务实例的协作留痕；
  但时间线**不得**携带令牌、密钥、文件绝对路径、`base64` 内容。
* 「待办任务」（`scope=todo`）只把**我此刻能动手**的列出来；这不是权限收紧，
  是入口语义（权限仍由 `can_write` + 各接口 `_require` 决定）。
* 归档项目对所有人只读（批次 7 §6），时间线与发布结果照常可读。

## 13. 历史数据兼容

* 没有 `business_case` 的历史项目：`card.business_case_id = ""`、
  时间线事件 `business_case_id = ""`、发布收口 `primary_action.id = "view-report"`。
  **不迁移、不清洗、不新建**任何业务实例号。
* 没有 `participants` 的历史项目：`mine` 只含 `owner` 一类来源（批次 7 已如此）。
* 历史项目的时间戳缺失：事件 `at` 用 `created_at` 兜底，
  不得因为一条时间戳缺失就把整条时间线清空。
* 已发布的旧报告（没有 `published_by` / `published_at`）：`closure` 照常返回，
  `state` 由 `report.status` 决定，缺失字段给空值。

## 14. 可自动化验收标准

1. `GET /api/projects?scope=mine|all|todo|archived` 四者都返回 200；非法 scope → 400。
2. 每一行（四个 scope 都是）都带 `card`，且 `card` 的子块键齐全：
   `owner` / `owner_display_name` / `stage` / `waiting_for` / `last_event` / `anomaly` /
   `business_case_id` / `primary_action`。
3. `card.stage.code` 与 `GET /api/projects/{pid}/workflow/projection` 的
   `next_action.sub`（或最后一步）**逐字相同**。
4. `scope=mine` 不含归档；`scope=archived` 只含归档；`scope=all` 不含归档。
5. `scope=todo` 只含 `card.primary_action.required_role` 命中当前用户的项目。
6. 列表排序按 `last_event.at` 降序（同值按 `project_id` 升序）稳定。
7. 存在 `failed` / `interrupted` 任务时 `card.anomaly.has_anomaly` 为真且 `codes` 非空。
8. `GET /api/projects/{pid}/timeline` 返回 200 且 `events` 是数组；每条事件
   `actor/role/at/action/label/action_kind/target` 键齐全且 `actor/at/action` 非空。
9. 时间线覆盖 §7.2 列出的事件码：把业务跑一遍后这些 `action` 都出现过。
10. 时间线两次调用（除 `generated_at`）一致；`at` 升序稳定。
11. 时间线对不可见项目 404「项目不存在」，与不存在项目不可区分。
12. `publish-result` 保留 `report` / `versions` / `quote_handoff`，并新增 `closure`。
13. 未发布 / 已发布未回传 / 回传失败 / 回传成功 四态的 `closure` 取值符合 §7.3 规则 2–5。
14. `internal_recipients` 与 `external_targets` 不相交；自由文本只出现在
    `external_targets`。
15. `primary_action.id` 按来源决定（来自报价 → `back-to-quote`）。
16. 三个接口都是只读：调用前后项目的 meta / 报告 / 任务数量逐字不变。

## 15. 人工验收场景

1. 用销售账号打开首页，确认有「我的项目 / 全部项目 / 待办任务 / 最近访问 / 已归档」
   五个入口，且「最近访问」能一键回到上次那单。
2. 用工艺经理账号打开「待办任务」，确认只列自己该动手的。
3. 打开任一项目，确认时间线里能顺序看到报价创建 → 技术支线 → 解析 → 参数/工艺确认 →
   成本 → 审核 → 发布 → 回传报价，且每条能点进去。
4. 发布一份报告并回传，确认「发布结果」写清了三件事：已发布、已分发对象、回传到哪张
   报价第几步；把回传目标服务停掉再发一次，确认出现失败原因与「重新回传报价」。
5. 归档一个项目，确认它从「我的项目」消失、出现在「已归档」、且只读。

## 16. 不允许减少的既有能力

* `GET /api/projects` 仍返回 **list**、仍带 `access` 块、默认 `scope=mine`、
  非法 scope 仍 400（批次 7）。
* `GET /api/projects/{pid}/workflow` 与 `/workflow/projection` 的既有字段一个不删。
* `GET /api/projects/{pid}/agent/events`（Agent 会话流）与 `/audit` 不动，不合并、
  不替换成时间线。
* `publish-result` 的 `report` / `versions` / `quote_handoff` 三个键保留。
* `report_workflow` 的发布门禁（须审核通过 + 前置齐 + 来源快照未变 + 至少一个发布对象）
  与其幂等语义一律不动。
* 批次 1（项目身份唯一来源）、批次 7（ACL）、批次 8（登录态与离开协议）、
  批次 9（任务状态 / 固定失败块 / 安静口径）的红测必须继续全绿。

# 技术项目「我的 / 全部」可见范围与项目级读写权限（批次 7）

假设批次 1–6 已实现。本批**只做可见范围与项目级 ACL**，不改业务状态机、不改流程门禁、不改视觉。

## 1. 背景与真实问题

技术工艺的所有项目读接口今天**没有项目级权限**：

* `GET /api/projects`（`main.py:920-922`）是 `store.list_projects()`，**连当前用户都没取** ——
  实测（`CPQ_SSO=true` + 打桩 `cpq_sso.resolve`，两个账号两个项目）：
  工艺工程师 alice 的列表里同时出现 alice 与 bob 的项目（2 条）。
* 41 个 `/api/projects/{project_id}/...` 单参数 GET 路由里，**34 个**对非属主返回 200：

  `agent/meta`、`agent/settings`、`ai-chat`、`ai-results`、`approval`、`assembly`、`attachments`、
  `audit`、`cleaning`、`component-match`、`cost-review`、`costest`、`files`、`integration`、
  `manufacturing`、`manufacturing/bom.csv`、`material`、`model-lookup`、`negotiation`、`pricenow`、
  `pricing`、`process-report`、`process-report/versions`、`production`、`requirement`、`source`、
  `summary`、`summary.html`、`summary.md`、`tasks`、`verification`、`versions`、`workflow`、
  `workflow/projection`

  （`bom` / `bom.csv` / `costest.csv` / `requirement/pdf` / `requirement/precheck` / `tree` 当时是
  404，原因是**它自己没有数据**，不是权限 —— 换一个有数据的项目照样能读到。）
* 项目级写入只有一条 `project_write_guard`（`main.py:429-446`），且只对 `role == "engineer"` 生效；
  `auth.can_edit_project`（`services/auth.py:200`）只被 4 处调用（`main.py:445 / 930 / 947 / 4376`）。
* 「我的清单」今天的口径是**前端本地列表**（`home.js` / `cpq-tech-inbox.js`），不是后端判定；
  报价首页的技术清单与历史 Drawer 各自读同一份无权限的列表。
* CPQ 登录只有销售经理 / 工艺经理，而 `cpq_sso.ROLE_MAP` 把 `sales_mgr → viewer`
  （`services/cpq_sso.py:35-45`）：**销售经理的身份在技术工艺侧被压成了 viewer**，
  不看 `cpq_role_code` 就再也认不出他是销售经理 —— 本批必须同时读这两个口径。

## 2. 用户角色与用户故事

* 作为工艺工程师，我只想在自己的清单里看到我创建 / 我参与 / 我记得当前持有的项目，
  「全部」不该把全公司的项目都摊给我。
* 作为工艺经理，我要能看到技术工艺的全部项目（含归档），因为全流程归我。
* 作为销售经理，我只该看到**由我那张报价单发起**的技术项目，用来知道我的单子做到哪一步。
* 作为财务经理，我只该看到**当前成本任务归我**的项目。
* 作为任何使用者，我看到「项目不存在」时不应该能反推出「它其实存在只是我没权限」。

## 3. 当前流程

1. 页面调 `GET /api/projects` → 后端返回本机全部项目（无过滤）。
2. 页面按自己的本地规则（localStorage / 前端过滤）分成「我的 / 全部 / 已归档」。
3. 打开任一项目 → 全部读接口放行；写接口只对 engineer 角色做「本人项目」校验。
4. 历史 Drawer 直接按项目 ID 拉数据，因此它天然能拉到任意项目。

## 4. 目标流程

1. 后端出**唯一判定**：`visible_projects(user, scope)` 与 `require_project_access(project_id, user, mode)`。
2. `GET /api/projects` 默认只返回**我的清单**；`?scope=all` 返回权限范围内的全部（不是全公司）；
   `?scope=archived` 返回我有权限的归档项目。返回体形状**仍是 list**（不破坏既有消费方），
   但每项新增 `access` 字段（`scope` / `mine_sources` / `can_read` / `can_write`）。
3. 每个 `/api/projects/{project_id}/**` 路由（读与写）开工前一律先做项目级判定：
   读 → `require_project_access(pid, user, "read")`；写 → `require_project_access(pid, user, "write")`。
4. 前端只消费后端给的 `access`，不再自己拼「我的 / 全部」；历史 Drawer 与报价首页技术清单
   走同一份判定结果。

## 5. 角色与权限矩阵（唯一口径表）

身份判定取 `user.role`（技术工艺口径）与 `user.cpq_role_code`（CPQ 口径）**两者的并集**，
所以 `sales_mgr → viewer` 这条映射不会把销售经理认成只读陌生人。

| 角色 | 读范围 | 写范围 | 归档 |
|---|---|---|---|
| `admin` | 全部 | 全部 | 可读，**只读** |
| `process_manager`（含 CPQ 工艺经理） | 全部 | 全部 | 可读，**只读** |
| `process_director` / `reviewer` | 全部（3.2/3.3 要审要发） | 无（仍走各接口既有 `_require`） | 可读，只读 |
| `general_manager` | 全部 | 无 | 可读，只读 |
| `engineer` | 我创建 / 我参与 / 我当前持有 | 仅我创建（沿用 `can_edit_project`） | 仅我创建的可读，只读 |
| `sales_manager`（含 `cpq_role_code == "sales_mgr"`、`sales_director`） | 我的来源报价关联项目 | 无 | 不可见 |
| `finance_manager`（含 `cpq_role_code == "finance_mgr"`） | 成本任务归我的项目 | 成本相关接口（既有 `COST_ROLES`，本批不改） | 不可见 |
| 其它（`viewer` 等） | 我参与 / 我当前持有 | 无 | 不可见 |

「我的清单」的四类来源（每项都记进返回体的 `access.mine_sources`）：

1. `owner` —— `meta.owner == username`（我创建）；
2. `holder` —— `current_holder(pid) == username`（我当前持有）；
3. `participant` —— `meta.participants` 里有我（我参与过）；
4. `assigned` —— `meta.participants` 里我是带 `assignee=True` 的那一条，或
   `current_holder == 我 且 我 != owner`（分派给我的业务实例）。

## 6. 状态定义

* **归档**：`meta.deleted_at` 非空。归档项目**一律只读**：`mode == "write"` 时按 `forbidden` 拒绝。
* **参与者**：`meta.participants` 是列表，每项 `{username, role, source, assignee, added_at, added_by}`；
  `source ∈ {"manual", "quote_owner", "cost_task_assignee"}`。
* **当前持有者**：`meta.current_holder`；缺省 = `meta.owner`（不写回 meta，读取时兜底）。
* **可见性**：不存在、与我无关、归档且我无权限 —— 三种都按「项目不存在」处理（见 §9）。

## 7. 接口与数据契约

新增 `tech_app/backend/services/project_access.py`：

* `MODES = ("read", "write")`；`SCOPES = ("mine", "all", "archived")`。
* `ProjectAccessError(code, message="")`：`code ∈ {"not_found", "forbidden"}`，
  属性 `code` / `message`，`str(exc)` 至少含 `message`。
* `effective_roles(user) -> set[str]`：`{user["role"]}` ∪ `{user["cpq_role_code"]}` ∪
  `{cpq_role_code 映射后的技术工艺角色}`，空值忽略。
* `mine_sources(user, meta) -> list[str]`：上表四类来源，按 `owner, holder, assigned, participant` 顺序去重。
* `can_read(user, meta) -> bool` / `can_write(user, meta) -> bool`（写沿用 `auth.can_edit_project`
  并叠加「归档只读」）。
* `can_access(project_id, user, mode="read") -> bool`：不存在 / 不可读 → `False`；
  `mode == "write"` 时要求 `can_write`。
* `require_project_access(project_id, user, mode="read") -> dict`：返回 `meta`；
  不存在 / 不可读 / 归档且要写 → 抛 `ProjectAccessError("not_found", "项目不存在")`；
  可读但不可写（角色不够，例如 viewer 想改）→ 抛 `ProjectAccessError("forbidden", "...")`。
* `visible_projects(user, scope="mine", include_archived=False) -> list[dict]`：
  `mine` = 我有 `mine_sources` 的项目；`all` = 我权限范围内的全部（工程师的 `all` 等于 `mine`，
  销售 / 财务的 `all` 等于各自关联范围）；`archived` = 我有权限的归档项目。
  每项附 `access = {scope, mine_sources, can_read, can_write}`。
* `scope_of(user) -> str`：给界面说明「你看到的是我的 / 全部」。

`tech_app/backend/storage/store.py` 新增（落点仍在项目 meta 文档，不新建表）：

* `add_participant(project_id, username, *, role="", source="manual", assignee=False, author="system")`
  —— 幂等：同一 `username` + `source` 已存在时只更新 `role` / `assignee`，不追加重复项；写 audit。
* `remove_participant(project_id, username, *, source="", author="system")` —— 不存在时安静返回。
* `list_participants(project_id) -> list[dict]`；`set_current_holder(project_id, username, author)`；
  `current_holder(project_id) -> str`（缺省 owner）。
* `list_projects(include_archived=False)` 保持**既有签名与语义**（给内部/脚本用），
  权限过滤只发生在 `project_access.visible_projects`。

`tech_app/backend/main.py`：

* `GET /api/projects` 增加 `scope: str = "mine"` 与 `include_archived: bool = False` 查询参数，
  依赖 `current_user`；非法 `scope` → 400；返回 list（每项带 `access`）。
* 所有 `/api/projects/{project_id}/...` 的读路由开头加 `project_access.require_project_access(...)`；
  写路由加 `mode="write"`。`ProjectAccessError("not_found")` 一律映射成 **HTTP 404
  `{"detail": "项目不存在"}`**，与真正不存在的项目**逐字一致**；`forbidden` → 403。
* 既有的 `project_write_guard`（engineer 本人项目）语义被本批覆盖后**保留**（幂等叠加），
  但不得再出现「engineer 改别人项目 → 403，读别人项目 → 200」这种一读一写不对称。

## 8. 正常路径

1. alice（engineer，owner=alice）打开首页 → `GET /api/projects` 只得到自己的项目，`access.scope="mine"`。
2. bob 的项目被 `add_participant(pid_b, "alice", source="manual")` 加进来 → alice 的清单多一条，
   `mine_sources` 含 `participant`，且 alice 能读它的 `attachments` / `workflow` / `summary`。
3. `remove_participant(pid_b, "alice")` → alice 立刻读不到（404），列表里也消失。
4. 工艺经理打开 `?scope=all` → 拿到全部技术项目（含别人的），并可写。
5. 销售经理（`cpq_role_code="sales_mgr"`）在 `participants.source="quote_owner"` 的项目上：
   列表可见、项目内只读；对别人项目 → 404（列表里也没有）。

## 9. 异常路径与「不泄露存在性」

* **不存在**与**无权限**：`GET /api/projects/{pid}/...` 都返回 404，`detail` 都是「项目不存在」，
  **不可区分**：状态码、`detail`、字段名集合、除 `trace_id` 外的取值全部逐字相同；不得出现
  「你有权限吗」这类可区分的文案。
  （跨批次契约：批次 9 起所有 `>= 400` 的 JSON 响应体统一带一个**逐请求唯一**的 `trace_id`
  及其同名响应头，因此这里守的是「不可区分」而不是字面上的「响应体逐字相同」；`trace_id`
  逐请求唯一本身不泄露存在性。任何其它字段的差异仍然是缺口。）
* **写不可见**：非参与者 / 非 owner 对归档项目写 → 404（按不存在处理）；
  可见但角色不够（例如 viewer 改参数）→ 403，文案说明所需角色（沿用 `_require` 的风格）。
* 非法 `scope` → 400，不改任何数据。

## 10. 并发与幂等

* `add_participant` 幂等：并发重复调用只留一条（同 `username` + `source`）。
* `visible_projects` / `require_project_access` 只读，不写库、不改 meta。
* 归档 / 恢复与 ACL 判定无共享可写状态；列表读取不加锁。

## 11. 刷新、重试与服务重启

* ACL 判定只用项目 meta（文件后端），不依赖内存缓存：刷新 / 重试 / 服务重启后结果一致。
* 列表与详情在 meta 变更（加参与者 / 归档）后**立即**反映，不需要重启。

## 12. 权限边界

* 本批不新增角色，也不改各业务接口的 `_require`（谁能做哪一步仍由它们决定）。
* 本批只新增「能不能看到这个项目、能不能碰这个项目」这一层；它**不能**代替业务权限：
  工艺经理能看全部项目，但第 4 阶段的成本测算仍然只有财务经理能改（既有 `COST_ROLES`；
  五阶段口径见 §18.7）。
* 前端过滤只是提示，不是权限；后端判定是唯一依据。

## 13. 历史数据兼容

* 老项目 `meta.owner` 是 `system`（历史导入）或某用户名：`owner` 口径照旧按字符串比较，
  不迁移、不改写历史 meta。
* 老项目没有 `participants` / `current_holder`：视为空列表 / owner，不影响既有可见性
  （owner 仍看得到自己的项目）。
* 归档项目（`deleted_at` 非空）继续保留审计与数据，不删除、不迁移。
* 不新增数据库表；不触碰批次 2–6 的 PG 表。

## 14. 非目标

* 不做前端「我的 / 全部」页签重设计（属于批次 10 的首页 IA），本批只要求消费后端 `access`。
* 不改任务中心 / 待办（批次 9）、不改时间线（批次 10）、不改 Token 与登录态（批次 8）。
* 不引入部门 / 组织架构表：经理范围 = 全部技术项目，不做「部门」细分。
* 不把 ACL 做成配置项或权限点管理系统。
* 不做「按字段脱敏」（例如财务看不到工艺成本明细），本批只到项目粒度。

## 15. 可自动化验收标准

1. `visible_projects(alice, "mine")` 只含 alice 创建 / 参与 / 持有的项目；`scope="all"` 对 engineer 等于 `mine`。
2. 两个账号两个项目的列表：alice 的 `mine` 不含 bob 的项目；bob 的 `mine` 不含 alice 的项目。
3. `require_project_access(pid_b, alice, "read")` 抛 `ProjectAccessError("not_found")`，
   且 `str(exc)` 与真正不存在的项目**完全相同**。
4. `add_participant` 后可读；重复调用只有一条；`remove_participant` 后不可读且列表里消失。
5. `current_holder` 缺省 = owner；`set_current_holder` 后持有人可读，`mine_sources` 含 `holder`。
6. 归档项目默认不在 `mine` / `all` 里；`scope="archived"` 时 owner / process_manager / admin 可见。
7. 归档项目写一律拒绝（owner 也是）。
8. 销售经理只读 `quote_owner` 关联项目；财务经理只读 `cost_task_assignee` 关联项目；其它人 404。
9. `sales_mgr → viewer` 映射下，`cpq_role_code="sales_mgr"` 仍被认成销售经理（不是普通 viewer）。
10. HTTP：`GET /api/projects` 只返回当前用户可见项目，且每项带 `access`。
11. HTTP：**路由表里全部**「只有 `{project_id}` 一个路径参数」的 GET 路由，对非属主一律 404
    （测试从 `main.app.routes` 现算这份清单，并要求数量 ≥ 30，防止空集合假通过）。
12. HTTP：同一批路由对 owner 不返回 404（证明不是把所有请求都变成 404）。
13. HTTP：不存在与无权限的响应体逐字相同。
14. HTTP：未带票 → 401（既有行为不变）。
15. engineer 写别人项目仍被拒（既有 `project_write_guard` 能力不退化）。

## 16. 人工验收场景

1. 用工艺经理登录 → 首页「我的」只列自己的，「全部」列技术工艺所有项目，且能打开任意一个。
2. 用销售经理登录（CPQ 销售经理）→ 只看得到他那张报价单拉出来的技术项目，且是只读。
3. 用财务经理登录 → 只看得到成本任务归他的项目。
4. 手改 URL 用别人的项目 ID 打开 → 界面统一提示「项目不存在」，与真删掉的项目表现一致。
5. 把一个项目归档 → 它从「我的 / 全部」消失，在「已归档」里仍能打开且不可编辑。

## 17. 不允许减少的既有能力

* `GET /api/projects` 的返回形状仍是 list（前端既有消费方不必跟着改结构）。
* `store.list_projects()` 的签名与语义（内部调用方）。
* `project_write_guard` 对工程师「只能改本人项目」的限制。
* 各业务接口的 `_require` 角色门禁（谁能做哪一步）。
* 未登录 401、健康检查等公开路径的行为。
* 归档项目保留数据与审计（软删除语义）。

---

## 18. 修订 v2：项目 ACL 与业务门禁各归其位（批次 7 回归修复）

本节**取代** §4.3 / §7 里「所有写路由一律 `mode="write"`」的那句话，其余各节仍然有效。
§5 的角色矩阵是本节的依据，**不再视为与之冲突**。

### 18.1 背景：现行实现切断了三条真实业务链路

批次 7 落地后，`main.py:449` 的 `project_write_guard` 对**所有**非 GET 请求先执行
`require_project_access(pid, user, "write")`，而 `can_write`
（`services/project_access.py:165-171`）落到 `auth.can_edit_project`
（`services/auth.py:200-207`），只认 `admin` / `process_manager` / `engineer`（本人）。
读侧又依赖参与者表（`quote_owner` / `cost_task_assignee`），而**全仓库没有任何业务代码
调用 `store.add_participant`** —— 这两类记录从不存在。

实测（本地子进程 + 临时 `DATA_DIR` + 打桩 `cpq_sso.resolve`，未连线上库）：

| 账号 | 读（43 个单 `{project_id}` GET） | 写 |
|---|---|---|
| 财务经理（`finance_mgr → finance_manager`） | **43 × 404**（批次 7 之前 40 × 200） | 14 条成本接口 **403**：「你的角色只能查看该项目，不能修改」 |
| 工艺技术总监（`tech_director → process_director`） | 43 × 200 | 审核 / 发布的 5 条接口 **403**（同上文案） |
| 销售经理（`sales_mgr → viewer`） | **43 × 404** | 客户信用等 **403 / 404** |

后果不是「列表少了几行」，而是：财务成本闭环、3.2 报告审核、3.3 报告发布、
报价—技术工艺关联读取**全部断开**；而且业务接口自己的 `_require` 一句都没跑到
（它永远没有执行机会），用户看到的是「角色只能查看」这种自相矛盾的提示。
`报价首页.html:1848` 的技术清单与 `openProject()` 读的就是这批接口，销售侧首页因此为空。

### 18.2 根本原则（唯一口径）

* **项目 ACL** 回答：*这个用户是否与项目有关、是否可以进入项目。*
* **业务接口门禁（`_require` / `COST_ROLES` / `REVIEW_ROLES` / `DIRECTOR_ROLES` /
  `QUOTE_APPROVAL_ROLES` / 路由内联角色判断）** 回答：*这个角色能不能执行当前业务动作。*
* 通用写闸门**不得**提前否决专属业务动作。可见性不自动等于写权限；反过来，
  「角色不够做这一步」也不能被 ACL 提前说成「你只能查看该项目」。

### 18.3 角色池可读（不绑定具体领取人）

CPQ 的任务本来就是派给**角色池**的（`target_type ∈ {role, user, public}`），领取人由
报价侧决定，技术侧拿不到回调。把可读性绑死在具体领取人上会在换班 / 换人时再次变成 404，
因此按**项目状态**判定：

| 角色 | 判据（满足其一即可读） |
|---|---|
| 财务经理（`finance_manager` 或 `cpq_role_code ∈ {finance_mgr, finance_manager}`） | 项目存在 **有效的财务交接**：`integration.load_plan(pid).finance_handoff` 非空且 `sent_at`（或 `task_id`）非空；或参与者表里有该账号的 `cost_task_assignee` |
| 销售经理（`sales_manager` / `sales_director` 或 `cpq_role_code ∈ {sales_mgr, sales_manager, sales_director}`） | 项目存在**有效来源报价关联**：`store.load_business_case(pid)` 的 `quote_session_id` 或 `source_task_id` 非空；或参与者表里有该账号的 `quote_owner` |

要求：

* 这两条必须**从项目状态直接判定**，不得只依赖 `meta.participants`（现状没有任何代码往里写）。
* 必须覆盖**清单（含历史抽屉）与项目的全部读接口** —— 走同一份
  `require_project_access(pid, user, "read")`，不允许「列表看得到、点进去 404」。
* 「有关联」只授予**可见性**，不自动授予任何写权限。
* **归档项目不受本节影响**：继续按 §6「归档对非 owner 按不存在处理」，
  不因角色池关联而可读或可写。

### 18.4 可见性 ≠ 写权限：`write` 与 `contribute` 两级

`require_project_access(pid, user, mode)` 的 `mode` 扩为三个取值：

| mode | 判定 | 用在哪些路由 |
|---|---|---|
| `read` | 可见（§5 + §18.3），归档只读 | 全部 `GET` |
| `contribute` | **可见 + 未归档**，不看项目级写权 | §18.5 白名单里的专属业务动作 |
| `write` | 可见 + 未归档 + 项目级写权（`admin` / `process_manager` / `engineer` 本人） | 其余全部非 GET 路由（新建 / 改名 / 归档 / 附件 / 参数 / 工艺 / 生成 / 材料 / 制造 / 清洗 / 装配 / 生产 / 汇总 / 定价 / 谈判 / 审批提交 …） |

`project_write_guard`（`main.py:449`）按白名单选择 `mode`：命中 §18.5 走 `contribute`，
其余仍走 `write`。**不把业务角色塞进 `can_write`**（那会把 ACL 变成第二套业务门禁，
换个角色就要再改一次）。

失败语义不变：不可见 / 归档 → `not_found`（HTTP 404「项目不存在」，与真不存在不可区分）；
可见但项目级写权不够 → `forbidden`（403）。

### 18.5 专属业务动作白名单（走 `contribute`，共 21 条）

判定依据（唯一，可从代码推导）：路由自带的门禁（`_require` 或路由内联角色判断）
**允许至少一个 `can_write_project()` 会拒绝的角色** —— 等价说法：这条路由的业务门禁在
`write` 模式下永远没有执行机会。以 `CAN_WRITE = {admin, process_manager, engineer}`
为准，用这条判据重扫 `main.py` 的 120 条项目写路由，命中且仅命中下面 21 条。
判据本身由红测的静态用例用 AST 现算守住，所以这份清单不会随实现漂移；
实现侧必须放一份同口径的显式常量（见 §18.12 #12）。

| # | 路由 | 路由自身门禁 |
|---|---|---|
| 1 | `POST /api/projects/{project_id}/agent/event` | `SESSION_WRITE_ROLES`（含 `finance_manager`：财务要往成本会话里追加时间线） |
| 2 | `POST /api/projects/{project_id}/parts/{part_id}/cost` | `COST_ROLES` |
| 3 | `PUT /api/projects/{project_id}/parts/{part_id}/cost` | `COST_ROLES` |
| 4 | `POST /api/projects/{project_id}/integration/cost` | `COST_ROLES` |
| 5 | `PUT /api/projects/{project_id}/integration/cost` | `COST_ROLES` |
| 6 | `PUT /api/projects/{project_id}/cost-review` | `COST_ROLES` |
| 7 | `POST /api/projects/{project_id}/cost-review/parts/{part_id}` | `COST_ROLES` |
| 8 | `POST /api/projects/{project_id}/cost-review/assembly` | `COST_ROLES` |
| 9 | `POST /api/projects/{project_id}/cost-review/confirm` | `COST_ROLES` |
| 10 | `POST /api/projects/{project_id}/cost-review/material-write` | `COST_ROLES` |
| 11 | `POST /api/projects/{project_id}/cost-review/send-to-quote` | `COST_ROLES` |
| 12 | `POST /api/projects/{project_id}/cost-review/return-to-process` | `COST_ROLES` |
| 13 | `POST /api/projects/{project_id}/pricing/review` | `FINANCE_ROLES` |
| 14 | `POST /api/projects/{project_id}/approval/act` | `QUOTE_APPROVAL_ROLES` |
| 15 | `POST /api/projects/{project_id}/versions/{version}/approve` | `REVIEW_ROLES` |
| 16 | `POST /api/projects/{project_id}/versions/{version}/reject` | `REVIEW_ROLES` |
| 17 | `POST /api/projects/{project_id}/requirement/review` | `DIRECTOR_ROLES` |
| 18 | `POST /api/projects/{project_id}/process-report/review` | `DIRECTOR_ROLES` |
| 19 | `PUT /api/projects/{project_id}/process-report/distribution` | `DIRECTOR_ROLES` |
| 20 | `POST /api/projects/{project_id}/process-report/publish` | `DIRECTOR_ROLES` |
| 21 | `PUT /api/projects/{project_id}/requirement/customer-credit` | 路由内联：`admin` 或技术角色 `sales_manager` |

白名单**只影响 ACL 用哪一级模式**，不改变任何业务门禁。上面每一条最终仍由它自己的
`_require` / 内联判断决定「这个角色能不能做」：财务能过 `COST_ROLES` 而过不了
`DIRECTOR_ROLES`，销售能过客户信用而过不了成本。

文档与实现都必须保持这份白名单**显式可读**（放在 `project_access` 或 `main.py`
的常量里），不要用「凡是不以 `/cost` 结尾的就…」这类推断式规则。

**明确不在白名单里的四类**（防止实现方顺手加进去）：

* `PATCH` / `DELETE /api/projects/{project_id}/management`、`POST .../attachments`：
  它们自己只有 `WRITE_ROLES`（= `can_write` 认的那三个角色），`write` 模式就是它们的
  正当门禁 —— 可读性不因此变成写权限。
* `POST /api/projects/{project_id}/tasks/{task_id}/cancel`：它**没有任何自己的角色
  门禁**（路由 docstring 明写「项目级写权限由 project_write_guard 统一拦」，
  `tasks.cancel_task` 也不校验 actor）。若把它降级为 `contribute`，任何可见者都能取消
  别人的任务 —— 那是能力放大，不是修复，因此留在 `write`。将来若要允许「任务创建者 /
  领取人取消自己的任务」，必须先在**路由内**加一条自有门禁（谁能取消），再把它纳入白名单。
* `PUT /api/projects/{project_id}/agent/settings`：改的是这个项目的 Agent 运行参数，
  属项目级配置，留在 `write`。
* 其余 99 条项目写路由：门禁集合要么是 `WRITE_ROLES` 的子集，要么根本没有门禁 ——
  ACL 的 `write` 就是唯一（或最后一层）判定，不动。

关于 #21 的已知历史问题（**本批不改**）：路由自己比较的是技术角色名
`user["role"] == "sales_manager"`，而 CPQ 的销售经理经 `ROLE_MAP` 映射成 `viewer`，
因此放行后拿到的是路由自己的业务 403「客户信用等级仅可由销售经理首次录入」。
这仍然是**正确的归属**（文案来自业务门禁，不是 ACL），所以 #21 留在白名单里；
要不要把路由内的角色比较改成认 `cpq_role_code`，是另一条独立的缺陷，不在本批范围。

### 18.6 与权限矩阵（§5）的对应

| 角色 | 读 | 写 | 归档 |
|---|---|---|---|
| `finance_manager` | §18.3 财务角色池 + 参与者 | 仅 §18.5 里由 `COST_ROLES` / `FINANCE_ROLES` 放行的成本类动作；通用项目修改无 | 不可见、不可写 |
| `sales_manager` / `sales_director` | §18.3 销售角色池 + 参与者 | 仅客户信用（§18.5 #21）；其余只读 | 不可见、不可写 |
| `process_director` / `reviewer` | 全部 | 仅 §18.5 #15–19（审核 / 发布）；通用项目修改无 | 可读、只读 |
| `general_manager` | 全部 | 仅 §18.5 #14（审批）；通用项目修改无 | 可读、只读 |
| `process_manager` / `admin` | 全部 | 全部 | 可读、只读 |
| `engineer` | 我创建 / 我参与 / 我持有 | 仅我创建的项目（§5 原样） | 仅我创建的可读，只读 |
| 其它（`viewer` 等） | 我参与 / 我持有 | 无 | 不可见 |

### 18.7 五阶段口径

文中所有阶段号一律以 `tech_app/backend/services/workflow_stages.py` 为准（五阶段 × 13 子步骤）。
**成本测算是第 4 阶段**：`4.1 零件成本` / `4.2 组装成本` / `4.3 汇总`。
不得再出现把成本写成第三大步的那种旧编号（也不得把「发送财务」写成 2.2）；
出现即视为文档缺陷。

### 18.8 正常路径

1. 工艺经理在 3.3 结束 → `integration.send_to_finance()` 写入 `plan.finance_handoff`
   （`sent_at` 非空）→ 财务角色池获得该项目可见性。
2. 任一财务经理登录 → `GET /api/projects?scope=all` 能看到它，
   `GET /api/projects/{pid}/cost-review` → 200（不再是 404）。
3. 他调 `PUT /cost-review` / `POST /cost-review/confirm`：`contribute` 放行 →
   `_require(user, COST_ROLES)` 通过 → 业务动作正常执行。
4. 工艺技术总监打开同一项目 → 3.2/3.3 审核、发布：`contribute` 放行 →
   `DIRECTOR_ROLES` 通过。
5. 销售经理在报价首页技术清单里能看到这些项目（来源报价关联）→ 点进去只读；
   要改报价回报价侧自己的流程。
6. 财务经理尝试 `PATCH /management`（改项目名）：`write` 判定 → 403
   「你的角色只能查看该项目，不能修改」——**这条文案在这一级是对的**。

### 18.9 异常路径

* 财务经理看**没有**财务交接的项目 → 404（角色池按项目状态收敛，不是「财务能看全部」）。
* 销售经理看**没有**来源报价关联的项目（独立技术项目）→ 404。
* 归档项目：财务 / 销售 / 无关账号一律 404（读与写都不可）。
* `contribute` 路由上角色不够 → 业务文案 403（例如销售调 `PUT /cost-review`
  得到「这一步只有「财务经理」能操作…」），**不得**再出现 ACL 那句「只能查看该项目」。
* 项目不存在 / 不可见 → 404「项目不存在」，与真不存在响应不可区分（含批次 9 的 `trace_id` 口径）。

### 18.10 并发 / 幂等 / 刷新 / 重启

* 角色池判定是**纯读**：不改项目、不写参与者、不产生审计。
* 交接写入（`plan.finance_handoff`）与来源关联（`business_case`）都是既有的幂等落盘；
  重复派发不产生第二份可读性依据。
* 关联写入后**立即**生效，不需要重启；服务重启后依据落盘状态重建，结果一致。
* 并发读同一项目不得互相污染。

### 18.11 历史数据兼容

* 历史项目没有 `business_case`、也没有 `finance_handoff`：行为与今天一致 ——
  只有 owner / 参与者 / 全部可读角色能看到；**不迁移、不回填、不猜**。
* 历史项目已有 `plan.finance_handoff`：自动获得财务角色池可读，无需任何数据变更。
* 已归档项目全部沿用 §6，不因本修订解锁。

### 18.12 可自动化验收标准

1. 财务经理对「有 `finance_handoff`」的项目：43 个单 `{project_id}` GET 全部非 404；
   对没有交接的项目：全部 404。
2. 销售经理对「有来源报价关联」的项目：43 个 GET 全部非 404；对独立技术项目：全部 404。
3. §18.5 全部 21 条路由，用其**合法角色**对「有关联项目」请求时，响应**不得来自 ACL
   闸门** —— `detail` 既不是 `项目不存在`（ACL 的 404），也不是「你的角色只能查看该项目，
   不能修改」（ACL 的 403）。业务自己的 404（如「版本不存在」）或业务 403 都算通过。
4. 同一批路由，用**无关账号**（非参与者、无角色池关联）请求 → 一律 404「项目不存在」；
   财务 / 销售角色池对**没有关联**的项目请求同一批路由 → 同样 404。
5. `PATCH /management`（通用写）对财务经理 / 总监 / 销售 → 仍 403 ACL 文案
   （可见性没有变成写权限）。
6. `engineer` 改别人创建的项目：不得成功（200 一律不合格），且项目名必须原样不变。
   批次 7 §9 的「不泄露存在性」允许它是 404「项目不存在」；路由内那条
   「工艺工程师只能修改本人创建的项目」若因此不可达，不算能力减少 —— 判据是**结果**。
7. 归档项目：财务 / 销售角色池关联者的读与写都 404。
8. 全部 43 个读路由对 owner **不得出现 ACL 文案**（属主永不被自己的项目挡住）。
9. `mode` 只有三个取值，且 §18.5 白名单在代码里是显式常量。
10. 清单与单项目读一致：对财务 / 销售 / 总监，`GET /api/projects?scope=all` 里出现的
    项目集合恒等于「逐个 `GET /api/projects/{pid}` 不为 ACL 404」的集合。
    不允许「列表看得到、点进去 404」，也不允许反过来。
11. 属主基线：对同一个有关联项目，财务 / 销售角色池能通过 ACL 的路由集合 ⊇ 属主能通过
    的路由集合（43 条逐条比对）。
12. 白名单口径由静态用例守住：用 AST 从 `main.py` 现算「自带门禁允许
    `can_write=False` 角色的项目写路由」，结果必须与实现里的显式常量**逐条相等**
    （不多不少），且 `PATCH /management`、`POST /attachments`、
    `POST tasks/{task_id}/cancel` 不得出现在里面。

### 18.13 不允许减少的既有能力

* 批次 7 的收紧（「知道 12 位项目号就能读任意项目」被堵住）必须保持：无关账号对
  全部读 / 写接口仍 404，且与「项目不存在」不可区分。
* `engineer` 只能改本人项目的规则不动。
* 归档只读规则不动。
* `GET /api/projects` 的 list 形状、`access` 块、`scope ∈ {mine, all, archived}`、
  非法 scope 400 —— 一个都不改。
* 各接口既有的 `_require` 角色集合一个都不放宽：本修订**只**改「谁先拦」，
  不改「谁最终能过」。

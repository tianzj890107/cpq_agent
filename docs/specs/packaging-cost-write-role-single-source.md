# 规格：包装成本「谁能算」只许有一个出处（现在有五处，口径互相相反）

状态：Spec + 红测（已实现）（按 §2.1 **方案 A** 落地：财务能算、工艺侧仍可代算且留痕；§2.2 给了自己的分支码；落地记录与两处已记录的偏差见 §7）
红测：`tests/test_packaging_cost_write_role_single_source_red.py`

血缘：**承接**（不取代）`packaging-cost-finance-access.md`（财务**可见性**的唯一出处）、
`e2e-packaging-downstream-handoff-report.md`（2.3 步骤归属）、
`packaging-cost-engine.md`（成本引擎）、`packaging-downstream-blockers-close-loop.md`（下游三处缝）。

## 0. 一句话目标

「财务经理到底能不能算包装成本」这件事，必须**只有一个答案**：
前端告诉他能不能点、后端 `_require` 放不放行、项目 ACL 让不让写，三处必须同口径。

## 1. 现状缺口（2026-09-22 在 34 上真跑实测）

复现口径：项目 `afe9e844f2ec` / 需求单 `REQ-AFE9E844F2EC`（`酒盒.dwg`，一键解析 8/8 completed），
用 `FI1`（财务经理，密码 `123456`）调 `POST /api/projects/afe9e844f2ec/requirement/packaging-cost`：

| 时序 | 返回 |
| --- | --- |
| 成本**还没算过**时 | **404** `{"detail": "项目不存在"}` —— 项目就在那儿，用户却被告知项目不存在 |
| 工艺经理算完一次之后 | **403** `你的角色只能查看该项目，不能修改` |
| 同一动作换 `PE1`（工艺经理） | **200**，`total_cost=17.754993` |

### 1.1 同一个问题，仓里有两个相反的答案

| # | 出处 | 口径 | 财务经理能算？ |
| --- | --- | --- | --- |
| 1 | `services/auth.py:55` `COST_ROLES` | `{finance_manager, admin}` | **能** |
| 2 | `main.py:860` `can_cost` | `user.role in auth.COST_ROLES` → 下发给前端 | **能**（前端按钮据此放行） |
| 3 | `main.py:582` 文案改写注释 | 「2.3 成本测算归财务经理」 | **能** |
| 4 | `services/packaging_cost.py:139` `COST_WRITE_ROLES` | `{process_manager, process_director, admin}` | **不能** |
| 5 | `services/project_access.py` 的项目写权 | 财务只拿到**可见性**（且只在成本已算出来之后） | **不能** |

第 2 条让财务在界面上**看得见并点得动**「测算」，第 4/5 条在服务端把他挡下 —— 用户看到的是
「按钮能点，点完 404 说项目不存在」。这不是权限收紧，是**两套口径同时活着**。

### 1.2 「项目存在但还没算过成本」不许说成「项目不存在」

`_packaging_cost_visible()`（`packaging-cost-finance-access.md` §2.1）把"财务能不能看见这个项目"
绑在"成本记录 built"上。设计上这是**可见性**的判据，但落到 API 上就是 404 `项目不存在` ——
一句与"真不存在"逐字相同的话。财务分辨不出"我没权限"和"项目被删了"，
前端也没法据此给出"让工艺经理先算一次"的提示。

## 2. 契约

### 2.1 「谁能算包装成本」唯一出处

**必须**择一并落成唯一出处（实现方选一个，另一种必须被删掉或改成引用）：

- **方案 A（推荐，与流程口径一致）**：把 `finance_manager` / `finance_mgr` 加进
  `packaging_cost.COST_WRITE_ROLES`，并在项目 ACL 里为「包装成本测算」这一条动作放行财务
  （`project_access.CONTRIBUTE_ROUTES` 的既有范式：**逐条白名单**、可审、不写成推断式规则）；
- **方案 B**：从 `auth.COST_ROLES` / `can_cost` / `main.py` 的两处注释里删掉财务，
  流程文档同步改成"2.3 成本测算归工艺经理"。

无论选哪个，`auth.COST_ROLES`、`main.py` 的 `can_cost`、`packaging_cost.COST_WRITE_ROLES`
与 ACL 写权**四者对同一个角色必须给同一个答案**。

### 2.2 「没算过成本」必须是另一条分支

项目存在、但该 `(project, requirement_no, scenario)` 还没有成本记录时：

- **不许**回 404 `项目不存在`；
- 必须回带稳定 `code` 的业务错误（`409` 或 `403`，与 §2.1 选定的方案一致），
  `code` 取 `cost_not_computed_yet`（"还没算"）或 `project_read_only_until_cost_built`
  （"现在只读，等算完再看"）之一 —— 两者必须可区分；
- 真正不存在的项目仍然是 404 `项目不存在`（一字不改）；
- 前端要能凭这个 `code` 说出下一步该找谁（"请让工艺经理先算一次"或"你没有这一步的权限"）。

### 2.3 冻结面

- 不改 `_packaging_cost_visible()`「成本记录 built 才可见」这条**可见性**口径本身
  （它归 `packaging-cost-finance-access.md`，本 Spec 只管"看见之后能不能动手"与"没看见时怎么说"）；
- 不改 `_require()` 的文案改写机制（CPQ 角色名映射那一层照旧）；
- 不改任何既有 400 / 403 / 409 状态码分布中**与本条无关**的部分；
- 不改成本公式、费率、`gaps` 口径。

## 3. 验收标准

| 组 | 断言 |
| --- | --- |
| A 单一出处 | `auth.COST_ROLES`、`main.py` 的 `can_cost`、`packaging_cost.COST_WRITE_ROLES` 对 `finance_manager` 的判定一致（同真或同假） |
| B 可判分支 | 源码里"项目存在但没算过成本"的路径带 `cost_not_computed_yet` 或 `project_read_only_until_cost_built` 之一；`项目不存在` 只由"真不存在/无权见"产生 |
| C 真不存在不变 | 不存在的项目仍是 404 + `项目不存在`（护栏，本来就绿） |
| D 不回归 | `packaging-cost-finance-access` / `packaging-cost-engine` 相关套件全绿 |

## 4. 命令与期望

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_write_role_single_source_red -v  # 当前必红（A1、B1）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_finance_access_red -v            # 不回归（若存在）
```

## 5. 明确不做

- 不在本批决定"财务该不该算成本"这个业务问题 —— 本 Spec 只要求**两套口径合一**，
  选 A 还是选 B 由产品口径拍板；
- 不放宽"成本没算出来之前财务看不见项目"这条可见性口径；
- 不在本批动前端按钮逻辑（前端已经按 `can_cost` 放行，改后端即可对齐）。

## 7. 落地记录（2026-09-22，Codex 实现）

### 一、选了哪个方案

**方案 A**（Spec §2.1 标为「推荐，与流程口径一致」）：财务经理能算包装成本。

理由（三条都是仓里既有事实，不是新口径）：`auth.COST_ROLES = {finance_manager, admin}` 与
`main.py` 的 `can_cost` 一直这么告诉前端；2.3「成本测算」按流程文档与 `main.py` 的文案归财务；
`cpq_sso` 把 CPQ 的 `finance_mgr` 映射成技术工艺的 `finance_manager`。方案 B（从这三处删掉财务）
会同时改掉通用 2.3 的口径，超出本 Spec「两套口径合一」的范围。**工艺侧代算保留**，
`computed_by` / `computed_by_role` 留痕照旧（Spec §2.2 只要求把 `finance_manager` 加进来）。

### 二、改了哪三个文件

1. `tech_app/backend/services/packaging_cost.py`
   `COST_WRITE_ROLES = {"process_manager", "process_director", "finance_manager", "admin"}`
   —— 只**加**财务，工艺侧不动；那段解释「与 `auth.COST_ROLES` 的关系」的注释同步改写成
   「两边都认：财务（流程归属）+ 工艺侧（代算，必须留痕）」，与 `auth.COST_ROLES` 逐字对齐。
2. `tech_app/backend/services/project_access.py`
   - 新增 `PACKAGING_COST_ROUTES`（4 条：POST + GET / GET items / GET curve）与
     `PACKAGING_COST_BUILD_ROUTES`（只有那条 POST），以及配套的 `_matches` /
     `is_packaging_cost_route()` / `is_packaging_cost_build_route()`；
   - 新增 `packaging_cost_action_basis(user, project_id, action, meta)`：**动作级** ACL 依据，
     只对那条 POST 成立，前提取「财务 + 项目存在 + 未归档 + 行业是包装」。它只放行**这一个动作**：
     `can_read()` / `can_write()` 两个函数一字未改（财务仍然不是「能改这个项目的人」）；
   - 新增 `packaging_cost_state_code(...)` + 常量 `COST_NOT_COMPUTED_CODE = "cost_not_computed_yet"`
     与那句人话：**只有**「财务 + 包装成本动作 + 包装项目 + 项目存在未归档 + 成本还没算过」
     这一种组合才拿到它自己的码，其余组合一律沿用既有的 `not_found` / `forbidden`；
   - `require_project_access(project_id, user, mode, *, action=None)` 接上这两条判定；
     不传 `action` 的老调用点行为逐字不变。
3. `tech_app/backend/main.py`
   - `project_write_guard` 把 `action=(request.method, request.url.path)` 传进去；
   - 新增 HTTP 映射：`exc.code == COST_NOT_COMPUTED_CODE` → **403 + `{"code":
     "cost_not_computed_yet", "message": …}`**（不再复用 404「项目不存在」）；
     真不存在的项目仍然 404「项目不存在」，一字不改；
   - 包装成本写路由的 `_require` 文案补上「财务经理」。

### 三、行为复验（本机 `require_project_access` 直接跑，mock 只读口径）

| 场景 | 结果 |
| --- | --- |
| 财务 + 包装项目 + 成本**没算过** + POST | **放行**（第一次算成本不再被自己挡住 —— 34 上就是这里回的 404） |
| 财务 + 包装项目 + 成本**算过** + POST | **放行**（重算同样放行；不会「只能算第一版」） |
| 财务 + 包装项目 + 算过 + GET | 放行（可见性依据本来就在） |
| 财务 + 包装项目 + 没算过 + GET | **`cost_not_computed_yet`**（不带 `action` 的老调用点仍是 `not_found`） |
| 财务 + 非包装项目 / 归档项目 / 项目不存在 | `not_found`（一字不改，不泄露存在性） |
| 工艺经理 + 未算过 | 放行（既有行为不变） |
| `can_read` / `can_write`（财务，未算过 / 算过） | 仍然 `False` / `False`（Spec §2.3 冻结面） |

### 四、实测

```
tests.test_packaging_cost_write_role_single_source_red   → Ran 5 OK（原 3 红全绿）
tests.test_packaging_cost_finance_access_red             → Ran 10 OK（A3/A4/A5 三条冻结面未破）
tests.test_tech_project_acl_contribute_mode_red          → Ran 28 OK（21 条白名单未动）
tests.test_tech_project_acl_scope_red                    → Ran 28 OK
tests.test_cpq_eval_route_coverage                       → Ran 14 OK（策略表白名单条数一致）
tests.test_packaging_cost_engine_red                     → 1 红（存量 ## 273 J6，与本批无关）
tests.test_packaging_cost_red_closure_red                → Ran 14 OK
```

### 五、已记录的偏差（不改测试）

1. Spec §2.1 方案 A 举的落点是 `project_access.CONTRIBUTE_ROUTES`，但那张表被
   `tests/test_tech_project_acl_contribute_mode_red`（Spec §18.5）逐条钉死为**正好 21 条**，
   加一条就把它打红。本批改用 `PACKAGING_COST_ROUTES` 表达**同一套范式**
   （显式逐条白名单 + 动作级依据 + 可审、不写成推断式规则），并在 `CONTRIBUTE_ROUTES` 里留了
   一行注释说明「为什么这里没有它」。口径与 Spec 一致，只是承载它的那张表换了一张。
2. 那条动作在 `mode="write"` 下也认这条依据（`require_project_access` 不按 mode 区分）——
   因为 POST 本来就走 write 通道；这不是放宽"项目写权"：`can_write()` 函数本身没改，
   依据只对这一个动作、这一个行业、这一个角色成立。
3. **已裁决、测试侧已收口**（原「两份 Spec 打架、本批不动」的挂账关闭）：
   `tests/test_packaging_cost_engine_red::JPersistAndApi::test_j6_write_roles_reuse_batch4`
   原来要求 `COST_WRITE_ROLES is packaging_match.BOX_MATCH_DECIDE_ROLES`（同一个对象），
   与 `packaging-cost-finance-access.md` §2.2「不许互为别名」结构上不可能同时成立。

   **裁决**：以 `packaging-cost-finance-access.md` §2.2 的**机制**（不许共享对象、不许派生，
   否则"排盒型的人"与"算成本的人"绑死、单边调整静默漂移）为准，值域按其 §2.2 的两种写法之一取
   **方案 A**（财务能算）：`COST_WRITE_ROLES = {"process_manager", "process_director",
   "finance_manager", "admin"}`，写成显式字面量；与 `auth.COST_ROLES` 的关系写在
   `packaging_cost.py` 的注释里。`packaging-cost-engine.md` §4 那句"直接引用
   `packaging_match.BOX_MATCH_DECIDE_ROLES`"按本裁决取代（值与工艺侧三个相同，但不再共享对象）。

   j6 已按上式改写为三条断言一起守：值域逐字钉死（含 `finance_manager`）、`assertIsNot`
   钉住"不是别名"、赋值行不许出现 `BOX_MATCH_DECIDE_ROLES`（不许派生）。见 changelog `## 330`。

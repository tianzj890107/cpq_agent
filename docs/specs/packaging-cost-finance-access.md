# 包装 2.3 成本测算：财务可见性与写角色口径

血缘：承接 `packaging-cost-engine.md`（第 7 批成本引擎）、`tech-project-acl-visible-scope.md` §18（项目可见性
与角色池）、`packaging-downstream-blockers-close-loop.md` §6（那里把这条记成了「环境问题」，本批证明是产品问题）。

- 状态：**已实现**（`tests/test_packaging_cost_finance_access_red.py` 10 条全绿；`tests/test_packaging_cost_engine_red.py` 由 81 绿变 80 绿 / 1 红，唯一红的是被本 Spec §2.2 取代的那条，见 §2.4）
- 红测：`tests/test_packaging_cost_finance_access_red.py`
- 依赖：`tech_app/backend/services/project_access.py`、`packaging_cost.py`、`auth.py`

## 0. 一句话目标

让「谁算包装成本」和「财务看不看得见这个项目」各只有一处口径，并且**两个口径互相对得上**：
财务不该在项目已经算出成本之后还看到"项目不存在"，工艺侧代算也必须留下"是谁算的"。

## 1. 现状缺口（34 实测，可复现）

同一个项目、同一个接口，两个账号结果完全不同：

```
POST /api/projects/f1417060ae9d/requirement/packaging-cost      # PE1（工艺经理）
→ 200，total_cost = 6.412359 元/件，gaps = 14 条

POST /api/projects/f1417060ae9d/requirement/packaging-cost      # FI1（CPQ 财务经理）
→ 404 {"detail": "项目不存在", "trace_id": "721685b955f94d93"}
```

FI1 的 404 不是"这一步不归你"，而是**项目可见性判定**先说"看不见"（`project_access.require_project_access`
对不可见按「项目不存在」逐字返回，Spec §18.2 的既定口径）。财务对项目的可见性只有两条依据
（`project_access.py:245-252`）：

- `participants` 里有 `cost_task_assignee` 记录；
- 或 `plan.finance_handoff` 非空（通用 XBOM 2.2→2.3 的 `integration.send_to_finance` 写的）。

全仓 `store.add_participant` 的生产调用点为 **0**；包装链路**没有**"送财务"这一步，也不写
`plan.finance_handoff`。于是：**包装项目的 2.3 对财务永远是 404**。

### 1.1 写角色口径与通用流程不一致

```
tech_app/backend/services/packaging_cost.py:130
    COST_WRITE_ROLES = packaging_match.BOX_MATCH_DECIDE_ROLES     # 工艺侧（含 process_manager）

tech_app/backend/services/auth.py:54
    COST_ROLES = {"finance_manager", "admin"}                     # 通用 2.3：财务专属
```

同一套系统里 2.3 有两套角色口径：通用流程"成本只能财务改"，包装流程"工艺经理就能算"。
哪种对由业务定，但**不能两个并存且没有任何留痕**——现在既看不出是谁算的，也没有说明为什么包装例外。

## 2. 口径（本批要定的三条）

### 2.1 财务角色池可见性必须包含"已有包装成本"的项目

`project_access.can_read(finance_user, meta)` 为真的条件里增一条：该项目已算出包装成本
（`packaging_cost.load_cost(pid).built` 为真）。理由是**成本记录本身就是关联事实**：
钱已经算出来了，做成本的人却看不到项目，这不是权限收紧，是链路断了。

必须保持不变：

- 归档项目仍按归档规则（`deleted_at` 只对 owner 可见），不因本条件解锁；
- 本条件只给**可见性**，不给写权（`can_write` / `contribute` 一字不改）。

### 2.2 包装 2.3 的写角色必须显式声明

`packaging_cost.COST_WRITE_ROLES` 不许再写成 `packaging_match.BOX_MATCH_DECIDE_ROLES` 的**别名**：
跨批次直接复用会把「排盒型的人」和「算成本的人」永久绑成同一批人，任何一边调整都会静默漂移。
第一版允许两种写法之一，但必须写死、必须在注释里说明与 `auth.COST_ROLES` 的关系：

- **财务专属**：`COST_WRITE_ROLES = {"finance_manager", "admin"}`（与通用 2.3 一致）；或
- **工艺代算**：显式集合（含工艺侧），并在成本记录里写 `computed_by` / `computed_by_role` 留痕。

### 2.3 成本记录必须能回答"谁算的"

成本记录（`packaging_cost.build_cost()` 的 `record`）必须带 `computed_by`（账号）与
`computed_by_role`（技术侧角色码）。今天两者都没有，事后只能翻审计表猜。

### 2.4 实现记录：与既有红测 `cost_engine_red::J6` 的一处**真冲突**（实现方按本 Spec 落地，已上报测试侧）

本 Spec §2.2（`COST_WRITE_ROLES` **不许**再是 `packaging_match.BOX_MATCH_DECIDE_ROLES` 的别名，必须是写死的
字面量集合）与 `tests/test_packaging_cost_engine_red.py::JPersistAndApi::test_j6_write_roles_reuse_batch4`
（"写权限常量必须直接引用第 4 批那一份"，断言 `assertIs(module.COST_WRITE_ROLES, packaging_match.BOX_MATCH_DECIDE_ROLES)`）
在同一处互为反命题 —— 一个要求"必须是同一个对象"，一个要求"不许是那个对象"，结构上不可能同时为真。

本批按**较新的**本 Spec 落地（§5 的"不改公式与费率"未越界，只改了写角色的**写法**），因此 J6 由绿转红；
`packaging-cost-engine.md` §4 那句"直接引用 `packaging_match.BOX_MATCH_DECIDE_ROLES`"由本 Spec §2.2 取代。
本 Spec §4 原先写着"`test_packaging_cost_engine_red` OK"，是没发现这处冲突，**以本节为准**。

- 本版取 §2.2 的**工艺代算**写法：`COST_WRITE_ROLES = {"process_manager", "process_director", "admin"}`
  （与 `BOX_MATCH_DECIDE_ROLES` 当前**取值相同**，但不再共享对象），并在成本记录里写
  `computed_by` / `computed_by_role`（§2.3）作为留痕 —— 选它不选"财务专属"是为了不打断既有工艺代算流程
  （PE1 那条 200 的链路今天就是这么跑的）。
- 测试侧的**一行修法**（本批不动 `tests/`）：把 `assertIs(module.COST_WRITE_ROLES, packaging_match.BOX_MATCH_DECIDE_ROLES, ...)`
  改成 `assertEqual(set(module.COST_WRITE_ROLES), set(packaging_match.BOX_MATCH_DECIDE_ROLES), ...)`
  —— 本版两集合取值仍然一致，这条断言的语义（"这两批人此刻是同一批"）保留，只是不再要求对象同一。
  两个断言即可同时为真。

## 3. 验收（红测逐条对应）

- A 组：`project_access.can_read` / `require_project_access` 对财务账号 + 已有包装成本的项目 → 不再 404；
- B 组：写角色口径（不许别名、必须显式；与 `auth.COST_ROLES` 的关系写在一处）；
- C 组：成本记录留痕字段；
- D 组：回归护栏（可见性只加不减；归档规则不变；`test_packaging_cost_engine_red` 全绿不变）。

## 4. 命令与期望

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_finance_access_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red          # 80 OK / 1 FAILED（J6，见 §2.4）
./open-claude/.venv/bin/python -m unittest tests.test_tech_project_acl_scope_red          # OK
./open-claude/.venv/bin/python -m unittest tests.test_tech_project_acl_contribute_mode_red # 28 OK
```

## 5. 本批不做

- 不改 `require_project_access` 的 404-vs-403 口径（不泄露存在性是有意设计）；
- 不新增"项目派发"任务体系；本批只让**已算出成本**这件事成为可见性依据；
- 不动包装成本公式与费率。

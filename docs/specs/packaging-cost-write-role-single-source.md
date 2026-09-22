# 规格：包装成本「谁能算」只许有一个出处（现在有五处，口径互相相反）

状态：Spec + 红测（未实现）
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

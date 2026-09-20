# 规格：包装成本规则路由与报告分组收敛 —— 包装第 7 批修订 A（修复第 1 批）

> 批次：包装验收修复**第 1 批**（共 4 批）。依赖包装第 1–8 批已实现（基线 HEAD `c0ea1f8`）。
> 红测：`tests/test_packaging_cost_rule_routing_red.py`；本批同时修订
> `tests/test_packaging_cost_engine_red.py` 的 `EXPECTED_REPORT_GROUPS`（用例 a3 / g2）。
> 本文件**修订** `docs/specs/packaging-cost-engine.md` 的 §2.3（报告分组）、§4.5（`REPORT_GROUPS`
> 与 `compute_line` 契约）、§4.6（`reviewed` 覆盖的生效范围）；**其余条款一律不变**。
> 本批只做两件事：**报告分组闭集补齐** 与 **公式取值单一入口**。
> 不改任何表达式、不改最低收费、不改三个原行业、不引入规则快照文件（那是修复第 2 批）。

## 1. 为什么先修这两件（实测证据，不是推断）

### 1.1 现状：4 条第 7 批红测失败，其中 1 条是纯缺陷

```
./open-claude/.venv/bin/python tests/test_packaging_cost_engine_red.py
Ran 81 tests in 0.976s
FAILED (failures=4)
```

| 失败用例 | 性质 | 归属 |
| --- | --- | --- |
| `ACatalogAndClosures.test_a3_report_groups_partition_every_category` | 纯缺陷：报告分组只覆盖 15/26 个类别 | **本批** |
| `CMinimumCharge.test_c1` / `test_c2` / `test_c4` | 口径冲突：门限取自另一套 Sheet，需业务裁决 | 修复第 3 批 |

第 5–8 批红测（工艺路线 57 / 成本 81 / 报价闭环 96）其余全绿，三行业无回归。

### 1.2 分组只覆盖 15/26 的证据

工作簿 `报价逻辑-0903.xlsx` 可见 Sheet `成本细分`（第 2 行）逐列公式：

| 列 | 分组名 | 公式 | 引用的类别 |
| --- | --- | --- | --- |
| F | 材料 | `SUMPRODUCT('报价-行业标准'!S2:S15,BC2:BC15)+SUMPRODUCT(!AN2:AN15,BC2:BC15)` | material, glue |
| G | 印刷 | `SUMPRODUCT(!T…)+SUMPRODUCT(!U…)` | print, print_uv |
| H | 覆膜 | `SUMPRODUCT(!V…)` | lamination |
| I | 烫金 | `SUMPRODUCT(!X…)` | hot_stamp_flat |
| J | 丝印 | `SUMPRODUCT(!AA…)` | silk_screen |
| K | 裱纸 | `SUMPRODUCT(!AH…)` | mounting |
| L | 模切 | `SUMPRODUCT(!AI…)` | die_cutting |
| M | 开槽 | `SUMPRODUCT(!AK…)` | v_groove |
| N | 手工 | `SUMPRODUCT(!AO…)` | labor |
| O | 包装 | `=!AT2+!AU2` | packaging, freight |

这 10 列合计只引用 **13** 个类别。剩下 **13** 个类别
（`transfer_film` / `hot_stamp_round` / `cold_stamp` / `varnish` / `anti_scratch` / `pet_oil` /
`visidi_uv` / `texture` / `emboss_deboss` / `folding` / `auto_mount` / `double_tape` / `other`）
**在任何分组里都不存在**：底层算出了钱（算进 `total_cost`），`report_groups` 里却找不到这笔钱。

现状 `REPORT_GROUPS`（`tech_app/backend/services/packaging_cost.py:69`）就是工作簿那 10 组、
15 个成员（烫金组另外含 round/cold），红测 a3 断言
`set(flattened) == 24 个部件级 + 2 个项目级`，因此**必然失败**。

### 1.3 `reviewed` 公式只对材料/人工生效的证据

`tech_app/backend/services/packaging_cost.py` 全文件只有两处调用 `resolve_formula`：

- `:954` `entry_material = resolve_formula("PKG-C-MATERIAL")`（部件材料）
- `:1079` `entry_labor = resolve_formula("PKG-C-LABOR")`（人工）

而 `compute_line()` 走 `_entry_for()`（`:412`）→ 直接返回 `dict(FORMULA_CATALOG[code])`；
`compute_content()` 直接 `FORMULA_CATALOG.get(code)`。

后果：业务在 `kb_packaging_cost_formula` 里把覆膜 / 烫金 / 模切 / 包材改成 `review_status='reviewed'`，
界面显示"已审核生效"，**实际计算仍是内置旧公式**，且没有任何告警。这比"运行时读 Excel"更现实
的偏差源 —— 它会让业务以为改完了。

## 2. 契约 A：报告分组闭集（`REPORT_GROUPS`）

### 2.1 修订后 = 13 组，恰好覆盖 24 + 2 个类别，每个类别只出现一次

| # | 分组名 | 成员（`cost_category` code） | 来源 |
| --- | --- | --- | --- |
| 1 | 材料 | material, glue | 工作簿 `成本细分!F` |
| 2 | 印刷 | print, print_uv | 工作簿 `!G` |
| 3 | 覆膜 | lamination, transfer_film | 工作簿 `!H` + 同族扩展 |
| 4 | 烫金 | hot_stamp_flat, hot_stamp_round, cold_stamp | 工作簿 `!I` + 同族扩展 |
| 5 | 丝印 | silk_screen | 工作簿 `!J` |
| 6 | 表面处理 | varnish, anti_scratch, pet_oil, visidi_uv, texture, emboss_deboss | 本批扩展（工作簿未细分） |
| 7 | 裱纸 | mounting | 工作簿 `!K` |
| 8 | 模切 | die_cutting | 工作簿 `!L` |
| 9 | 装订贴盒 | folding, auto_mount, double_tape | 本批扩展（工作簿未细分） |
| 10 | 开槽 | v_groove | 工作簿 `!M` |
| 11 | 手工 | labor | 工作簿 `!N` |
| 12 | 其他费用 | other | 本批扩展（工作簿未细分） |
| 13 | 包装 | packaging, freight | 工作簿 `!O` |

计数：2+2+2+3+1+6+1+1+3+1+1+1+2 = 26。

### 2.2 不变量（红测逐条断言）

- 工作簿 13 列覆盖到的 10 个分组名与成员**逐字不变**；覆膜 / 烫金是同族**超集**，工作簿样例里
  `W`（覆转移膜）、`Y`（热烫-圆压）、`Z`（冷烫）为 0，因此黄金数值不变。
- 分组顺序 = §2.1 表顺序（`dict` 插入顺序即前端渲染顺序）。
- `set(Σ成员) == 24 个部件级 + 2 个项目级`，且**不得重复归类**。
- 金额为 0 的分组**仍要出现**（键必须 13 个），不得因 0 丢键。
- `sum(report_groups.values()) == total_cost`（含工装 / 包装 / 运输）。
- 分组名是纯展示：以后可以改名，但**不得改变成员划分**；改名必须同步 `PC_GROUP_ORDER` + 红测。

### 2.3 落库、读回与接口

`compute_project` / `build_cost` / `load_cost` / `GET /api/projects/<pid>/requirement/packaging-cost`
的 `report_groups` 一律 13 键；`built=false` 的空壳分支也返回 13 键 0.0。
第 8 批的报价回传包（`cpq_packaging_quote.py` 的 `cost_report_groups`）沿用同一字典，不改结构。

### 2.4 前端

`tech_app/frontend/requirement-confirm.js` 的 `PC_GROUP_ORDER` 必须按 §2.1 顺序列出 13 个分组名。
`pcGroupRows()` 用 `PC_GROUP_ORDER.filter(name => name in groups)` 渲染，漏写组名 = 该组金额
在界面上永远不可见。

## 3. 契约 B：公式取值单一入口（`resolve_formula`）

### 3.1 唯一入口

- `resolve_formula(formula_code, *, rows=None)` 是取公式的**唯一**入口；`FORMULA_CATALOG` 只是
  内置兜底（Spec §4.6 的语义扩展到**所有**公式，不再只对材料和人工生效）。
- `compute_line(category, variables, *, formula_code=None, amount=None)`：
  - 给了 `formula_code` → `resolve_formula(formula_code)`；
  - 只给 `category`，且该类别在目录里恰好 1 条 → `resolve_formula(那一条)`；
  - 该类别在目录里有 ≥ 2 条（`packaging`，11 条 `PKG-P-*`）→
    `CostError(409, "category_needs_formula_code:packaging")`。
    **不许"取第一条"**：现状会静默取 `PKG-P-CARTON`，等于用纸箱公式算卡板/胶袋。
- `compute_content(formula_code, variables)` 同样走 `resolve_formula`。
- `compute_project` / `build_cost` 的每一行（部件材料、工序、人工、包材、工装、运输）都必须来自
  同一入口 —— 不允许任何一行直接读 `FORMULA_CATALOG`。

### 3.2 结果必须带可追溯字段

| 字段 | 取值 | 出现位置 |
| --- | --- | --- |
| `formula_source` | `"kb"`（reviewed 覆盖）或 `"builtin"` | `compute_line` / `compute_content` 结果 |
| `formula_version` | reviewed 行的 `formula_version`；内置 → `"1.0"` | 同上 |
| `rule_snapshot_version` | 本次计算读到的知识库快照版本（`kb_repo` 快照 `version`；取不到 → `""`） | `compute_line` / `compute_content` 结果 + `compute_project` 顶层 |

既有字段（`cost_category` / `formula_code` / `expression` / `inputs` / `amount` /
`min_charge_applied` / `minimum_charge` / `source` / `assumptions` / `gap`）一律保留不改。

### 3.3 fail closed

- `review_status='reviewed'` 且表达式过不了 DSL 校验 → `CostError(409, "invalid_formula:<code>")`；
  `compute_line` / `compute_content` **必须直接抛**，不许吞掉、不许用内置结果顶替
  （否则业务以为改生效了、其实是旧口径）。
- `review_status != 'reviewed'`（含第 3 批 7 条中文散文 `draft`）→ 忽略，用内置，
  `formula_source="builtin"`。
- 同一 `formula_code` 有多条 reviewed → 取 `effective_from` 最大的一条（现状行为，保持不变）。
- `formula_code` 不在目录里 → `CostError(404, "unknown_formula:<code>")`（现状不变）。

### 3.4 一次计算只读一次规则表

一次 `compute_project` 内 `kb_packaging_cost_formula` **只读 1 次**，读到的 rows 传给每一行的
`resolve_formula(..., rows=rows)`：保证同一份成本明细里公式版本一致，不会出现"前半张表用新公式、
后半张用旧公式"。

## 4. 非目标（本批不许顺手做）

- 不改任何表达式文本；不改 `minimum_charge` 数值（`c1`/`c2`/`c4` → 修复第 3 批）。
- 不新增规则快照 JSON、离线 Excel 校验工具、seed 去重（→ 修复第 2 批）。
- 不改三个原行业：`cost_model.py` 的 `generic_v1` 与 `TAX_DIVISOR=1.13` / `MATERIAL_SHARE=0.791` /
  `LABOR_RATIO=0.3556` / `OVERHEAD_RATIO=0.1778` / `PROCESSING_RATIO=0.0944` 逐字不动。
- 不改第 2–6 批知识库种子、契约、演示数据；不改第 8 批回传结构与报价侧逻辑。
- 不改数据库 schema（`report_groups` 是计算结果，不是落库列）。
- 不 commit / push / MR / tag / Release / 部署。

## 5. 自动化验收

| 命令 | 期望 |
| --- | --- |
| `./open-claude/.venv/bin/python tests/test_packaging_cost_rule_routing_red.py` | 实现前 FAIL；实现后 OK |
| `./open-claude/.venv/bin/python tests/test_packaging_cost_engine_red.py` | 只剩 `c1`/`c2`/`c4` 三条（修复第 3 批），其余 77 条 OK |
| `./open-claude/.venv/bin/python tests/test_packaging_quote_close_loop_red.py` | 96 条仍全 OK |
| `./open-claude/.venv/bin/python tests/test_packaging_knowledge_base_seed_red.py` 等第 1–6 批 | 全 OK，不回归 |

## 6. 人工验收

- 成本细分页 13 个分组都能看到（含 0 值分组），分组之和 = 总成本。
- 把 `kb_packaging_cost_formula` 的 `PKG-C-LAMINATION` 改成 `reviewed` 且换一个表达式 → 成本明细与
  总额随新表达式变化；改回 `draft` → 恢复内置值（`formula_source` 分别为 `kb` / `builtin`）。
- 把 `PKG-C-LAMINATION` 改成 `reviewed` 且表达式写错 → 计算**报错**，不出现"界面说改了、数字没变"。

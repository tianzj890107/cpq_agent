# 包装成本既有红测收口：把"7 条既有红"变成"两类可执行动作"

Spec 版本：1（状态行见下）

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_cost_red_closure_red.py`

## 1. 背景（实测，纠正上一份报告的口径）

上一份 34 报告写「1320 条里 7 条红全是既有的 `packaging_cost_*`」。在**本机当前状态**下
实测 5 个 `packaging_cost_*_red.py` 文件是：

```
Ran 226 tests  FAILED (failures=6, errors=8)     # 共 14 条红，不是 7 条
```

两类，性质完全不同：

**A. 8 条 ERROR = 本机没装依赖，不是业务缺陷**

全部是 `ModuleNotFoundError: No module named 'openpyxl'`（样例：
`test_packaging_cost_minimum_charge_red.py:265`、`test_packaging_cost_rule_snapshot_red.py`
的 `COfflineTool` / `ARulesSnapshot` 工作簿证据类）。依赖清单本身也是不一致的：
根 `requirements.txt:14` 有 `openpyxl==3.1.5`，而**后端依赖清单 `tech_app/requirements.txt`
里没有** —— 照后端清单装环境必然缺依赖。于是这 8 条环境问题和真正的业务红混在同一个
数字里，导致任何人都无法从"7 条红"判断风险。
涉及：`test_a10_golden_results_are_transcribed`、`test_c13_real_workbook_sheets_are_readable`、
`test_f2_two_sheets_disagree_on_all_five_categories`、`test_f3_golden_constants_match_workbook`、
`test_f4_threshold_lives_only_in_industry_standard`、`test_f5_v_groove_threshold_is_150_not_120`、
`test_f6_max_only_in_four_rate_sheet_cells`、`test_f7_hidden_sheets_are_hidden`。

**B. 6 条 FAIL = 最低收费口径未裁决（真业务红）**

> 2026-09-21 更新：本条已由业务裁决收敛（取 ② 报价-工费率，主行无门限），
> 落地契约与期望值变更见 `docs/specs/packaging-cost-minimum-charge-decision.md`。
> 下面这段"未裁决"的现场描述作为当时的问题陈述保留。

- `test_d5_chosen_policy_reproduces_its_golden_values`：断言
  `minimum_charge_policy.status == "chosen"`，实测 `pending`
  —— 红测自己的注释写明「本批必须先由业务/用户裁决；未裁决时这条必然失败」；
- `test_e1_batch1_frozen_numbers_still_hold`：
  `PKG-C-V-GROOVE` 的 `minimum_charge` 实测 `150`，第 1 批冻结值 `120`；
- `test_f3_expressions_and_minimum_charges_are_untouched`（路由批的冻结护栏）同一处冲突；
- `test_packaging_cost_engine_red.CMinimumCharge` 3 条：`min_charge_applied` 为 `False`。
  实测同一变量组下 `lamination` 的 `amount=0.233916788093`（最低收费 200/1000 = 0.2），
  即**表达式值本身高于最低收费**，所以"未命中最低收费"在当前数值下是自洽的 ——
  这 3 条红反映的是**口径未裁决导致的数值分歧**（`minimum_charge_policy.status=pending`
  时运行时按 `fallback="sheet_labor_rate"` 取值，表达式值随之变化），
  不是"少收钱"。先按事实描述，方向由裁决决定。

业务后果：**最低收费这一档的数字与第 1 批冻结口径不一致，且对外没有裁决结论**
（`policy="unresolved"`、`fallback="sheet_labor_rate"`），任何人无法判断当前报价用的是哪套；
同时"7 条既有红"掩盖了"8 条其实是环境"与"6 条是等裁决"的本质差别。

## 2. 目标

两类红各自收敛，留下一个可判读的数字：**环境类必须归零（换机器/换 CI 即可），
业务类必须由业务签字裁决后才允许转绿**，不得靠改测试转绿。

## 3. 契约

### C1 环境类：依赖缺失必须表现为 skip，不是 error

- 工作簿证据类测试在缺 `openpyxl` 时 `raise unittest.SkipTest`（`skipUnless`），
  并在 skip 原因里写明 "pip install -r tech_app/requirements.txt"；
- CI 与本地必须有一步"按 requirements 装依赖"的前置命令，写进测试说明；
- 断言：任一 `packaging_cost_*_red.py` 不得因 `ModuleNotFoundError` 报 ERROR。

### C2 裁决落档，且与运行时逐字一致

`tech_app/agent_knowledge/rules/packaging_cost_rules.json` 的 `minimum_charge_policy`
必须补全：

```json
{"status": "chosen", "owner": "<裁决人>", "decided_at": "YYYY-MM-DD",
 "decisions": [{"formula_code": "PKG-C-V-GROOVE", "minimum_charge": 150, "reason": "..."}]}
```

- `status` 未 `chosen` 时，C3 的引擎红测必须保持失败（不许放宽断言）；
- `decisions` 里每个 `minimum_charge` 必须与运行时 `FORMULA_CATALOG` 逐字一致。

### C3 冲突登记：每个与冻结值不一致的码都要有出处

新增 `_MIN_CHARGE_DECISIONS`（放 `packaging_cost.py` 或规则 JSON，二选一并写进本 spec 的
实现提示词）：记录 `{formula_code, frozen, current, owner, decided_at, reason}`。
断言：`FORMULA_CATALOG` 里凡 `minimum_charge` 与第 1 批冻结值不同的码，都必须在
登记表里出现；反之登记表里不得有幽灵条目。

### C4 最低收费行为的裁决结果必须落到运行时

C2 裁决完成后，`compute_line()` 的 `min_charge_applied` / `amount` 必须与裁决口径下的
冻结期望逐字一致（`lamination` 1000 件 → 0.2；`die_cutting` 100 件 → 1.0）。
**裁决前不得改断言**：这 3 条红是"等签字"，不是"待修 bug"。

### C5 口径报告

本 spec §1 的两个数字（环境类 / 业务类）必须随实跑更新；
"零新增红"不能替代"两类各自归零"的结论。

## 4. 不在本批范围

- 不改任何成本表达式与公式常量（除 C2 已裁决的 `minimum_charge`）；
- 不改列证据（`column_evidence`）与分类（`categories`）结构；
- 不删除任何红测文件、不跳过（skip）业务类红测。

## 5. 验收标准

1. `tests/test_packaging_cost_red_closure_red.py` 全绿；
2. 按 requirements 装齐依赖后，5 个 `packaging_cost_*_red.py` 的 ERROR 数 = 0；
3. `minimum_charge_policy.status == "chosen"` 且与运行时数字逐字一致后，
   业务类 6 条全部转绿，且**断言文本未改动**（用 `git diff` 可核对）。

# 规格：BOM 行上的「用量」必须进成本 —— 「8 个/套」不许按 1 件算

依赖：`docs/specs/packaging-cost-engine.md`（材料行的公式与输入）、
`docs/specs/packaging-bom-business-parts-rows.md`（权威清单的「用量1个 / 用量8/套」落进行 `quantity`）、
`docs/specs/packaging-parametric-bom.md`（BOM 行的 `quantity` / `unit` 列）、
`docs/specs/packaging-silent-degradation-disclosure.md`（字段存在但没人用 = 必须披露）。

状态：Spec + 红测（已实现）（原状：成本输入未取 BOM 行的 `quantity`，也没有
「这一行用量没进成本」的读侧披露；红基见 §1；落地见 §7）
红测：`tests/test_packaging_cost_part_usage_red.py`
行号基线：HEAD `2b18057`

## 0. 一句话目标

BOM 行上的「用量」（`quantity`）要么真的进材料金额，要么在读接口上明确说「这一行的用量没进成本」——
不许一边在 2.1 面板上显示「数量 8」，一边按 1 件算。

## 1. 现状缺口（代码级）

### 1.1 材料行的输入里没有这一行的用量

`packaging_cost.py:2082-2136` 的「部件 × 材料」循环里，`variables` 只有
`cut_length / cut_width / gsm / ton_price / imposition_count / proof_base / quote_quantity / machine_*`，
**没有这一行的 `quantity`**。同一件用量 1 与用量 8，材料金额完全相同。

### 1.2 用量确实存在、也确实显示给用户看了

- `da_repo.py:731 _PACKAGING_BOM_COLUMNS` 里有 `quantity` 列；
- `packaging_bom.py:475` 把权威原文里的用量写进这一列（真样本：`顶托EVA`＝用量 1 个、`磁铁`＝用量 8/套）；
- `tech_app/frontend/requirement-confirm.js` 的 BOM 面板把它渲染成「数量 8」。

### 1.3 不是「没有这个变量」，是「部件行的用量没有出口」

包材行（`packaging` 类别）走的是另一条公式，`usage_qty` 来自内容表 `kb_packaging_cost_content`；
部件行（`box_part` / `optional_part`）这条路上没有任何用量出口。

### 1.4 后果

任何一件用多次的部件（外购磁铁 8 个/套最典型）材料费少算 7/8；
界面与成本各说各话，且没有任何缺口提示。

## 2. 要求（可验收）

### 2.1 用量按行展开（材料行）

- 材料行金额 = 该行**单件**材料费 × 该行用量：`amount = 表达式求值结果 × usage_qty`；
- 该行的输入留痕里**必须**有 `usage_qty` 且是数字（落库列 `inputs_json`；内存与读回体的 `inputs` 同步带上）；
- `expression` 仍是工作簿原文，**逐字不变**（不许把用量塞进表达式字符串）；
- 用量取自该行 `quantity`；为空 / 非数字 / ≤ 0 → 按 1 计，`inputs.usage_qty = 1`。

### 2.2 取不到用量要披露

- 行属于 `box_part` / `optional_part`，且 `quantity` 缺 / 非数字 / ≤ 0 →
  `gaps` 里必须有 `usage_qty_missing`（`where` = 该行 `part_code`），并给
  `resolution_action`（一句话说清「补这一件的用量」）；
- 用量 > 1 的行不许静默：金额里必须真的体现出来（同 §2.1），不许只记一个数不用。

### 2.3 护栏

- 用量为 1 或没有用量的行：金额、表达式、既有 `inputs` 键**逐字不变**（既有 24 类别与黄金样例红测不许回归）；
- `loss_rate` / 最低收费 / 分组口径不动；
- 不许改 `kb_*` 里的公式文本（逐字证据链要保住）。

## 3. 允许修改范围

`tech_app/backend/services/packaging_cost.py` 的「部件 × 材料」循环与缺口表，
外加本 Spec 与它的红测。

## 4. 禁止事项

不改公式文本、不改费率、不改 BOM 与模板行、不改前端、不动 schema、
不连 PG / 34、不写业务数据、不 push / 不部署、不改 `tests/` 下既有文件。

## 5. 红基与复跑

红基（2026-09-22，HEAD `2b18057`，只读跑）：A 组三条红、B 组护栏绿。

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_part_usage_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red -v
```

## 6. 已记录的边界

- 本批只动「部件行的用量」；包材行（内容表 `usage_qty`）与人工行一字不动；
- 工艺 / 人工 / 模具行**不**乘用量（那几行是「按单件」口径，与本批无关）。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_part_usage_red
# 实现前：Ran 6 tests … FAILED (failures=3)   ← A1 A2 A3
# 实现后：Ran 6 tests … OK                    ← B1 B2 B3 三条护栏始终绿
```

| 契约 | 落点（`tech_app/backend/services/packaging_cost.py`） |
| --- | --- |
| §2.1 用量按行展开 | 新增模块级 `_part_usage_qty(row)`：取这一行 `quantity`；为空 / 非数字 / ≤ 0 → `(1.0, True)`，否则 `(float(value), False)`。部件 × 材料循环里 `usage_qty, usage_missing = _part_usage_qty(row)`，把 `"usage_qty": usage_qty` 放进 `variables`（因而同时进内存 `inputs` 与落库 `inputs_json` / `items_inputs`）；`amount = result["amount"] × usage_qty`（用量 1 时逐字等价）；`expression` 仍是工作簿原文，用量绝不进表达式字符串。 |
| §2.2 缺用量披露 | `GAP_RESOLUTIONS` 新增 `usage_qty_missing`（`missing_variable: ["quantity"]`、`resolution_action: "补这一件的用量"`、`entry: "packaging-bom"`、`severity: "advisory"`）；循环里 `usage_missing` 时 `gaps.append({"code": "usage_qty_missing", "where": part_code, "detail": …})`（`where` = 该行 `part_code`）。`gap_evidence()` 因此给出结构化 `resolution_action`。 |
| §2.3 护栏 | 用量 1 / 没有用量的行：`amount × 1.0` 逐字等价、`expression` 不变、既有 `inputs` 键（`cut_length` / `cut_width` / `gsm` / `ton_price` / `quote_quantity` / `imposition_count` …）一个不少；`loss_rate` / 最低收费 / 分组口径未动；`kb_*` 公式文本未动；非材料行（工序 / 人工 / 模具）的 `inputs` 不带 `usage_qty`。 |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_cost_engine_red tests.test_packaging_cost_rule_routing_red \
  tests.test_packaging_cost_rule_snapshot_red tests.test_packaging_cost_column_evidence_red \
  tests.test_packaging_cost_red_closure_red tests.test_packaging_cost_minimum_charge_red \
  tests.test_packaging_cost_policy_decision_red   → Ran 255 … OK (skipped=1)
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_bom_business_parts_rows_red \
  tests.test_packaging_bom_business_material_rows_red \
  tests.test_packaging_business_parts_version_pinning_red \
  tests.test_packaging_cost_input_version_pinning_red \
  tests.test_packaging_business_parts_read_failure_note_red   → Ran 66 … OK
```

未改公式文本与费率、未改 BOM 与模板行、未改前端、未动 schema、未连 PG / 34、未写业务数据、
未 push / MR / tag / Release / 未部署。

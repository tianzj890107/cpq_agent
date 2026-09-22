# 规格：包装最低收费口径裁决（②）与落地 —— 裁决记录 + 口径变更

Spec 版本：1（状态行见下）
上游：`docs/specs/packaging-cost-minimum-charge.md`（候选与实测证据，**本 Spec 只负责裁决与落地**）、
`docs/specs/packaging-cost-red-closure.md`（纠纷分两类：环境类 / 业务类）。

状态：Spec + 红测（已实现）（红测 test_packaging_cost_engine_red 另有已记录的测试侧冲突，见 changelog ## 273）
红测：`tests/test_packaging_cost_column_evidence_red.py` `tests/test_packaging_cost_engine_red.py` `tests/test_packaging_cost_policy_decision_red.py` `tests/test_packaging_cost_rule_routing_red.py` `tests/test_packaging_cost_rule_snapshot_red.py`

## 1. 裁决（业务给出，实现方与 Codex 都不得自行选）

```
裁决人：张真（zhangzhen@boulderaitech.com）
日期：2026-09-21
所选口径：② sheet_labor_rate（报价-工费率 原文；主行无门限）
```

理由（产品口径）：

1. 成本引擎要报的是**真实成本**，② 把机台开机与工时算进去，① 只算耗材与保底
   （同一工序差最多 5 倍：烫金 0.555 vs 1.343、啤/切 0.15 vs 0.835、V 槽 0.15 vs 0.816）；
2. ② 是**改动面最小**的收敛方案：q=1000 上 ② 与现状数值逐字相同，第 7 批冻结黄金值
   **一个都不变**，只需要把门限归零并把"120 这个凭空值"消掉；
3. ① 的"对客报价口径"若将来需要，应做成**独立的对客报价层**，不得由成本引擎承担。

## 2. 裁决的具体内容（逐条）

| 项 | 裁决值 |
| --- | --- |
| 权威 Sheet | `报价-工费率`（表达式与门限同表） |
| `PKG-C-LAMINATION` `minimum_charge` | `200` → **`0`** |
| `PKG-C-HOT-STAMP-FLAT` `minimum_charge` | `150` → **`0`** |
| `PKG-C-DIE-CUT` `minimum_charge` | `100` → **`0`** |
| `PKG-C-V-GROOVE` `minimum_charge` | `150` → **`0`**（凭空值 120 一并消失） |
| `PKG-C-MOUNTING` | 保持 `0` |
| 行级门限 | `报价-工费率!AI9 / AI14 / AI15` 的 `MAX(100/R,0.08/J)` 是**工作簿原文**，作为 `row_variants` 如实登记，不并进主行 |

`minimum_charge_source_ref` 在 `minimum_charge == 0` 时**必须为空串**（离线校验工具的既有规则：
`minimum_charge > 0` 才要求声明来源格）。

## 3. 契约

### C1 运行时口径常量必须归零

`tech_app/backend/services/packaging_cost.py` 的 `FORMULA_CATALOG`：上表四个码
`minimum_charge == 0`；**表达式一个字都不许改**；`frozen_minimum_charge`（第 1 批冻结值
200/150/100/120）保留不动，作为"曾经冻结过什么"的证据。

### C2 裁决必须落档，且与运行时同源

`tech_app/agent_knowledge/rules/packaging_cost_rules.json` 的 `minimum_charge_policy`：

```json
{
  "status": "chosen",
  "chosen": "sheet_labor_rate",
  "decided_by": "张真",
  "decided_at": "2026-09-21",
  "decisions": [
    {"formula_code": "PKG-C-LAMINATION", "minimum_charge": 0, "reason": "..."},
    {"formula_code": "PKG-C-HOT-STAMP-FLAT", "minimum_charge": 0, "reason": "..."},
    {"formula_code": "PKG-C-DIE-CUT", "minimum_charge": 0, "reason": "..."},
    {"formula_code": "PKG-C-V-GROOVE", "minimum_charge": 0, "reason": "..."}
  ]
}
```

`reason` 每条必须写明依据（"主行无门限，取报价-工费率原文"）。

行级门限**不需要新字段**：`formulas[PKG-C-DIE-CUT].row_variants` 已经如实登记了
`AI9 / AI14 / AI15`（`row` + `minimum_charge: 100` + `expression_extra: 0.08/J9`），
本批要求**逐字保留**，并在裁决 `reason` 里指向它 —— 这是"② 主行无门限"的对照证据，
不许因为主行归零而顺手删掉。

- `candidates` 三套（`sheet_industry_standard` / `sheet_labor_rate` / `declared_hybrid`）**保留**，
  它们是裁决依据，不许删；
- `rows_variants` 之外的 `golden` 块不动（那是逐候选的工作簿缓存值证据）；
- 只改这一块：`rule_set` 保持 `packaging_cost_v1`（这不是"换来源"，是"口径裁决落地"，
  工作簿 SHA-256 与表达式都没动），`formula_version` 因此不用跟着改。

### C3 冲突登记必须带上裁决人与日期

`_MIN_CHARGE_DECISIONS` 必须**四条全登记**（判据是 `frozen_minimum_charge != minimum_charge`），
每条的 `owner` = `张真`、`decided_at` = `2026-09-21`、`current` = 0、`frozen` = 第 1 批值，
`reason` 写清"为什么与冻结值不同、依据哪张表"。

### C4 计数与文案

`minimum_charge_policy()` 在 `status == "chosen"` 时：`policy == "sheet_labor_rate"`、
`fallback == ""`（不许再回退）；未裁决态的行为**保持不变**（回退 `sheet_labor_rate` +
标注 `unresolved`）—— 那是"没裁决时怎么表现"的契约，本批不动。

### C5 不许"顺手"改的东西

- 表达式、`rate_code`、`rounding`、`defaults`、`loss_scope` 一律不动；
- 第 7 批冻结的总成本黄金值（`报价-工费率!AV2` 合计 77.685201899648021）不动；
- 其它 16 个 `minimum_charge == 0` 的码不动；
- 不许为了让断言转绿而放宽任何断言的精度或删断言。

## 4. 口径变更清单（Codex 已按裁决改，属本批交付范围）

裁决落地会让下面这些**当时按①/混合口径写的期望值**变成错的，逐条改：

| 文件 | 用例 | 旧期望 | 新期望（②） |
| --- | --- | --- | --- |
| `tests/test_packaging_cost_engine_red.py` | `c1` 复膜 20×20 q=1000 | 命中，`0.2` | 不命中，`0.233916788093` |
| 同上 | `c2` q=100 / 1000 / 10000 | 命中 2.0 / 0.2 / 0.02 | 全部不命中，`1.772916788093` / `0.233916788093` / `0.080016788093` |
| 同上 | `c4` 啤/切 q=100 / q=1000 | q=100 命中 1.0 | 全部不命中，`7.811227692308` / `0.834787692308` |
| `tests/test_packaging_cost_rule_routing_red.py` | `f3` | 200 / 150 / 100 / 120 | `0 / 0 / 0 / 0` |
| `tests/test_packaging_cost_rule_snapshot_red.py` | `e1` | 200 / 150 / 100 / 120 | `0 / 0 / 0 / 0` |
| `tests/test_packaging_cost_column_evidence_red.py` | `d2` | 200 / 100 | `0 / 0` |
| `tests/test_packaging_cost_red_closure_red.py` | `c5`（锚点守卫） | 锚点文本 `必须命中最低收费` | 锚点改指裁决后的契约（`② 报价-工费率主行无门限` + `0.233916788093` / `1.772916788093` / `7.811227692308`），并新增「①/混合口径旧断言文本不得回潮」；断言强度只增不减 |

**不改**（现在绿、且裁决后仍应绿）：`c3`（表达式更高时不命中，1.3766112580048271）、
`c5`（`minimum_charge == 0` 不许命中）、`c6`（数量越大单件越低）、
`minimum_charge_red::d5`（它按 `chosen` 分支取黄金值，裁决后自然成立）。

## 5. 不在本批范围

- 不做"对客报价（①）"层；
- 不改工作簿、不改抽取工具、不改 `rule_set`；
- 不改匹配引擎（见 `docs/specs/packaging-match-undecidable-and-size-guard.md`）。

## 6. 验收标准

1. `python3 -m unittest tests.test_packaging_cost_policy_decision_red` 全绿（新增红测）；
2. 上表涉及的文件相关用例全绿，且 `git diff` 里**只有期望值与注释**变化；
3. `tests/test_packaging_cost_minimum_charge_red.py` 的 `d5`、
   `tests/test_packaging_cost_red_closure_red.py` 的 `a3/a4/b1/b2` 全绿
   （同文件的 `c5` 锚点守卫在 §4 锚点更新后即全绿，不依赖实现）；
4. 装齐依赖（`openpyxl`）后，5 个 `packaging_cost_*_red.py` 的 ERROR 数为 0。

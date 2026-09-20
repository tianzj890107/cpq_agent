# 规格：包装成本最低收费口径裁决 —— 包装修复第 3 批

> 批次：包装验收修复**第 3 批**（共 4 批），依赖修复第 1 批（分组闭集 + 公式取值单一入口）。
> 红测：`tests/test_packaging_cost_minimum_charge_red.py`。
> 工作簿：`裕同包装项目-待开发/报价逻辑-0903.xlsx`（只读、不入库），
> SHA-256 `974d9484414824d0bbfd2c83fb0c6044e4348c7a407e1ae0f58699bb60d2acc0`。
> 本批**只处理一件事**：`复膜 / 热烫-平压 / 啤,切 / V槽 / 裱纸` 的**最低收费**到底按哪张 Sheet、哪个
> 单元格算。现状是**未申报的混合口径**——表达式抄 `报价-工费率`，门限抄 `报价-行业标准`。
> **裁决权在业务/用户，不在实现方，也不在 Codex。** 本批把裁决变成一行可校验的申报字段，
> 并让「没申报就按某套静默出货」变成机器能挡下来的事。
> **本批不选口径、不改表达式、不改费率、不改第 7 批冻结黄金值。**

## 0. 术语澄清（本批最容易混淆的地方）

| 名称 | 指什么 |
| --- | --- |
| **契约口径** | 本 Spec 的条款：申报字段、单来源要求、审计码、运行时可观测性 |
| **工作簿口径** | `报价逻辑-0903.xlsx` 里某张 Sheet 某个单元格的**原文**与**缓存结果** |
| **实现现状** | `tech_app/backend/services/packaging_cost.py` 现在实际算出来的数 |

本批说「混合口径」是**工作簿口径层面**的判断：同一条公式的**表达式**与**门限**分别来自两张表。
本批说「未申报」是**契约口径层面**的判断：`FORMULA_CATALOG` 只有一个 `source_ref`，看不出这一点。

## 1. 实测证据：同一类别、同数量，两张表给出完全不同的单件值

`报价-行业标准`（下称 **①**）与 `报价-工费率`（下称 **②**）都有 `V/X/AH/AI/AK` 五列，`J` 列=模数、
`R` 列=报价数量，第 2 行 `J2=1、R2=1000`。逐格原文与缓存值（实测）：

| 类别 | 列 | **①** `报价-行业标准` 原文 | ① 缓存值（q=1000） | **②** `报价-工费率` 原文 | ② 缓存值（q=1000） | 现实现值 |
| --- | --- | --- | --- | --- | --- | --- |
| 复膜 lamination | V2 | `=IFERROR(MAX(200/R2,H2*I2/1000000*1.7/1.13/J2+H2*I2/1000000*18/1000*18.5/J2+0.15/J2),"")` | 1.2934294398230088 | `=(H2*I2/1000000*1.7/1.13/J2+H2*I2/1000000*18/1000*18.5/J2)+((30/60+R2/J2/5500)*(197+145))/R2` | 1.376611258004827 | 1.3766112580048271 |
| 热烫-平压 hot_stamp_flat | X2 | `=MAX(150/R2,(100*75*4)/1000000*8.5+0.3/J2)` | 0.5549999999999999 | `=((100*75*4)/1000000*8.5)+((200/60+R2/J2/5000)*(193+115))/R2` | 1.343266666666667 | 1.3432666666666671 |
| 啤/切 die_cutting | AI2 | `=IFERROR(MAX(100/R2,0.15/J2),"")` | 0.15 | `=((120/60+R2/J2/6500)*(197.52+190.06))/R2` | 0.8347876923076923 | 0.8347876923076923 |
| V槽 v_groove | AK5 | `=MAX(150/R5,0.15)` | 0.15 | `=(60/60+R5/3000)*(195+111)/R5*2` | 0.816 | 0.816 |
| 裱纸 mounting | AH13 | `=IFERROR(MAX(100/R13,H13/25.4*I13/25.4*(0.56/1000)/J13),"")` | 0.1 | `=((30/60+R13/J13/3500)*(209+126))/R13` | 0.17132857142857144 | 0.1713285714285714 |

（① 的复膜/裱纸行、② 的全表都各有 6–13 行公式；上表取 ① 能对上缓存值的行，② 取与实现同行的行。
① 的 `MAX` 只出现于 `V/X/AH/AI/AK`；② 全表只有 4 处 MAX：`AU2`（运输）与 `AI9`/`AI14`/`AI15`
三处行级 `MAX(100/R,0.08/J)`。）

**结论：① 与 ② 是两套不同的公式，不是同一公式的两种写法。**
① 的特长是**固定单件耗材/辅助项**（`0.15/J2`、`0.3/J2`、`0.08/J`、常数 `0.15`）+ 门限 `MAX`，
**没有**机台工时项；② 的特长是**机台工时项** `(setup/60 + q/J/产能)*(设备费率+人工费率)/q`，
主行**没有**门限 `MAX`。

## 2. 现状：未申报的混合口径（可直接复现）

`tech_app/backend/services/packaging_cost.py` 的 `FORMULA_CATALOG`：

| 条目 | `source_ref` | `minimum_charge` | 门限实际出处 |
| --- | --- | --- | --- |
| `PKG-C-LAMINATION` | `…/报价-工费率/V2` | 200 | 只在 `报价-行业标准!V2` 出现（`报价-工费率!V2` 无 MAX） |
| `PKG-C-HOT-STAMP-FLAT` | `…/报价-工费率/X2` | 150 | 只在 `报价-行业标准!X2` 出现 |
| `PKG-C-DIE-CUT` | `…/报价-工费率/AI2` | 100 | 只在 `报价-行业标准!AI2` 出现 |
| `PKG-C-V-GROOVE` | `…/报价-工费率/AK5` | **120** | **两张表里都不存在**（① 是 150，② 无门限） |
| `PKG-C-MOUNTING` | `…/报价-工费率/AH13` | 0 | —（① `AH13` 有 100 门限，未采用） |

也就是说：同一行公式的**表达式取 ②、门限取 ①、V槽门限凭空 120**，而 `source_ref` 只写了 ②，
读代码的人会以为整行都来自 ②。`compute_line()` 再把两者拼成
`MAX(minimum_charge/quote_quantity, ②表达式)`——**这条拼接公式在两张表里都不存在**。

## 3. 三种候选口径（供业务/用户裁决，本批不选）

| 候选 | `policy_id` | 单件金额 | 特征 | 权威 Sheet |
| --- | --- | --- | --- | --- |
| ① | `sheet_industry_standard` | 该表原文（已含 `MAX`） | 固定单件耗材项 + 门限；无机台工时 | `报价-行业标准` |
| ② | `sheet_labor_rate` | 该表原文；主行**无**门限 | 机台工时项；行级 `MAX` 只在 `AI9/AI14/AI15` | `报价-工费率` |
| ③ | `declared_hybrid` | `MAX(①门限/数量, ②表达式)` | = 现状；属**新设计**，非任一表原文 | 两表拼接 |

- 选 **②**：第 7 批冻结黄金值（`报价-工费率!AV2` 总成本 77.685201899648021）**不变**，只需把
  `minimum_charge` 从 200/150/100/120 改为 0/0/0/0，并把 `AI9/AI14/AI15` 三处行级门限登记为
  `row_variants`。代价最小的收敛方案。
- 选 **①**：与第 7 批冻结黄金值冲突（复膜 1.3766 → 1.2934，啤/切 0.8348 → 0.15，V槽 0.816 → 0.15，
  热烫 1.3433 → 0.555，裱纸 0.1713 → 0.1），**第 7 批黄金样例必须重算**。
- 选 **③**：必须逐条列出拼接来源（`expression_source_ref` + `minimum_charge_source_ref`）并给出业务
  理由；同时 `PKG-C-V-GROOVE` 的 120 必须改成 ① 的 150 或 ② 的「无门限」，不许保留。
  注意：③ 在 q=1000 上**与 ② 数值相同**（门限压不过表达式），所以第 7 批冻结黄金值同样不变；
  ③ 与 ② 的实际差别只有两处——**门限被显式声明**，以及 **V槽 120 被改正**。

### 3.1 三条候选对第 7 批 `c1`/`c2`/`c4` 的影响（实测复算）

| 第 7 批用例 | 期望 | ① 行业标准 | ② 工费率 | ③ 混合 |
| --- | --- | --- | --- | --- |
| `c1` 复膜 20×20mm q=1000 | 命中，`0.2` | **0.2 命中 ✓** | 0.23391678809332264 不命中 ✗ | 0.23391678809332264 不命中 ✗ |
| `c2` q=100 | `2.0` 命中 | **2.0 命中 ✓** | 1.7729167880933225 不命中 ✗ | 2.0 命中 ✓ |
| `c2` q=1000 | `0.2` 命中 | **0.2 命中 ✓** | 0.23391678809332264 不命中 ✗ | 0.23391678809332264 不命中 ✗ |
| `c2` q=10000 | `0.02` 命中 | 0.15073496991150442 不命中 ✗ | 0.08001678809332262 不命中 ✗ | 0.08001678809332262 不命中 ✗ |
| `c4` 啤/切 q=100 | `1.0` 命中 | **1.0 命中 ✓** | 7.8112276923076935 不命中 ✗ | 7.8112276923076935 不命中 ✗ |
| `c4` q=1000 | 不命中 | 0.15 不命中 ✓ | 0.8347876923076923 不命中 ✓ | 0.8347876923076923 不命中 ✓ |

**没有任何一套候选口径能同时满足现有 `c1`/`c2`/`c4`。** 因此裁决必须**一并**包含「`c1`/`c2`/`c4`
的期望值按所选口径的黄金值同步修订」，而**不是**让实现迁就现有测试。修订规则（裁决后由 Codex
按裁决结果改，属本批交付范围）：

- 选 ① → `c1`、`c4` 两条不动（本来就通过）；`c2` 的 q=10000 期望改为 `0.15073496991150442` 且
  `min_charge_applied=false`（因为门限被固定耗材项 `0.15` 压过）。
- 选 ② → `c1`/`c2`/`c4` 改为断言语义「② 主行无门限，`minimum_charge == 0`、永不命中」，
  并把 `AI9/AI14/AI15` 的行级门限单独断言。
- 选 ③ → 三条改为 `MAX(①门限/q, ②表达式)` 的复算值，并逐条断言 `minimum_charge_source_ref`
  指向 ① 的单元格。

## 4. 契约 A：快照新增 `minimum_charge_policy`（不论选哪套都要有）

`tech_app/agent_knowledge/rules/packaging_cost_rules.json`（修复第 2 批的产物）顶层新增：

```json
{
  "minimum_charge_policy": {
    "status": "pending",
    "chosen": "",
    "decided_by": "",
    "decided_at": "",
    "candidates": [
      {
        "policy_id": "sheet_industry_standard",
        "authoritative_sheet": "报价-行业标准",
        "minimum_charge_source": "报价-行业标准",
        "expression_source": "报价-行业标准",
        "is_declared_hybrid": false,
        "rationale": "对客报价口径：门限与固定单件耗材项同表",
        "golden": {
          "lamination":    {"cell": "V2",  "quantity": 1000, "unit_amount": 1.2934294398230088},
          "hot_stamp_flat": {"cell": "X2", "quantity": 1000, "unit_amount": 0.5549999999999999},
          "die_cutting":   {"cell": "AI2", "quantity": 1000, "unit_amount": 0.15},
          "v_groove":      {"cell": "AK5", "quantity": 1000, "unit_amount": 0.15},
          "mounting":      {"cell": "AH13","quantity": 1000, "unit_amount": 0.1}
        }
      },
      {"policy_id": "sheet_labor_rate", "authoritative_sheet": "报价-工费率", "...": "..."},
      {"policy_id": "declared_hybrid", "authoritative_sheet": "", "is_declared_hybrid": true, "...": "..."}
    ]
  }
}
```

### 4.1 强制约束

- `status` 闭集 `pending` / `chosen`；`status == "pending"` → `chosen == ""`、`decided_by == ""`；
  `status == "chosen"` → `chosen` ∈ `candidates[].policy_id` 且 `decided_by` 非空。
- `candidates` 必须**恰好** 3 条，`policy_id` 集合 = `{sheet_industry_standard, sheet_labor_rate,
  declared_hybrid}`。
- 每条 candidate 必须有 `authoritative_sheet`、`is_declared_hybrid`、`rationale`、`golden`；
  `golden` 必须**恰好**覆盖 5 个类别（`lamination` / `hot_stamp_flat` / `die_cutting` /
  `v_groove` / `mounting`），每条带 `cell` / `quantity` / `unit_amount`。
- `golden` 的 `unit_amount` 必须与工作簿缓存值逐条一致：
  ① = `报价-行业标准` 缓存值；② = `报价-工费率` 缓存值；③ = `MAX(①门限/quantity, ②缓存值)`。
- `is_declared_hybrid == true` 的 candidate（只允许 `declared_hybrid` 一条）必须
  `authoritative_sheet == ""`，且每条公式必须能给出两个来源（见 §5 的 C 条）。
- 三个隐藏 Sheet（`大货最终定价` / `大货价核算1` / `首批试产毛利核算`）不得出现在
  `authoritative_sheet` / `minimum_charge_source` / `expression_source` 的任何位置。
- 每条 candidate 必须带 `red_test_impact`：§3.1 那张 6 行矩阵的机器可读版本，**顺序与 §3.1 表
  一致**，每行 `{"case": "c1"|"c2"|"c4", "quantity": <int>, "applied": <bool>, "amount": <float>}`。
  要求：`amount` 必须能用该 candidate 自己的口径复算出来（① = 行业标准原文含 `MAX`；
  ② = 工费率原文无 `MAX`、`applied` 恒 `false`；③ = `MAX(①门限/q, ②表达式)`）。
  这条的作用是：**裁决一旦落定，`c1`/`c2`/`c4` 该怎么改就是查表，不是重新讨论**。

## 5. 契约 B/C/D：单来源申报与逐字校验（不论选哪套都要有）

### 5.1 B：每条公式声明自己的来源

- `FORMULA_CATALOG`（快照 `formulas[]` 同步）每条必须有 `source_sheet`（可见 Sheet 名）与
  `source_cell`；`source_ref` 必须与二者一致，格式 `报价逻辑-0903.xlsx/<source_sheet>/<source_cell>`。
- `source_sheet` 必须是可见 Sheet；隐藏 Sheet → 审计码 `hidden_sheet_source`。

### 5.2 C：门限必须声明自己的来源

- `minimum_charge > 0` 的条目必须有 `minimum_charge_source_ref`（同样格式）。
- `minimum_charge_source_ref` 的表与 `source_ref` 的表**不同**时，只有当
  `minimum_charge_policy.status == "chosen"` 且 `chosen == "declared_hybrid"` 才合法；
  否则审计报 `mixed_source_formula`（退出码 1）。
- `minimum_charge` 的数值必须能在 `minimum_charge_source_ref` 指向的单元格**原文**里找到
  （`MAX(<值>/…` 形式）→ 否则报 `minimum_charge_not_in_source`。**这条专门用来挡 120。**
- `minimum_charge == 0` 的条目必须 `minimum_charge_source_ref == ""`。

### 5.3 D：表达式必须与来源单元格逐字等价

每个条目必须新增两个字段：

- `variable_map`：`{变量名: 列字母}`，只登记**该公式里由单元格提供**的输入（如
  `{"machine_length": "H", "machine_width": "I", "imposition_count": "J", "quote_quantity": "R"}`）。
- 内联字面量不放进 `variable_map`：沿用修复第 2 批已有的 `verify_inputs`，
  未出现在 `variable_map` 里的键一律视为**写死在公式里的字面量**。

判定函数（工具与运行时都提供同一个，保持纯函数）：

```python
verbatim_equivalent(expression, source_formula, variable_map, source_cell, literals) -> bool
```

1. 从 `source_cell` 取行号（`V2` → `2`）；
2. 把 `expression` 里每个标识符替换为：`variable_map` 有 → `<列><行>`；否则取 `literals[名]`
   的文本（整数去掉 `.0`）；两边都查不到 → 返回 `False`（audit 报 `unmapped_variable`）；
3. 把替换后的表达式与 `source_formula` **各自用 `packaging_formula` 的同一套解析器规范化为
   「全括号形式」**（数字按浮点值归一，如 `1e6` 与 `1000000` 相同）；
4. 比较两个规范化字符串。

因此允许的差异只有：**多余括号、空白、`IFERROR(x,"")` 外壳、数字写法**。其余任何一个字不同
（例如 `1.7` 改成 `1.8`、`/J2` 漏掉、`5500` 写成 `5000`）都必须判不等。

- 不等价 → `source_cell_mismatch`（退出码 2：说明读到的公式确实不是这一条）。
- 变量既不在 `variable_map` 也不在 `literals` → `unmapped_variable`（退出码 1）。
- **禁止把两张表的写法自由改写后再声称来自某格**：改写即不等价，必须改 `source_sheet` /
  `source_cell` 指向真正被抄的那一格，或按 ③ 显式申报拼接来源。

### 5.4 ② 特有的 `row_variants`

若裁决为 ②，`报价-工费率!AI9/AI14/AI15` 三处行级门限不得丢失，必须在对应条目下登记：

```json
"row_variants": [
  {"row": 9,  "minimum_charge": 100, "expression_extra": "0.08/J9", "source_cell": "AI9"},
  {"row": 14, "minimum_charge": 100, "expression_extra": "0.08/J14", "source_cell": "AI14"},
  {"row": 15, "minimum_charge": 100, "expression_extra": "0.08/J15", "source_cell": "AI15"}
]
```

`row_variants` 在 ①/③ 下属非目标（不要求，也不禁止）。

## 6. 契约 E：运行时必须暴露「用哪套」

`tech_app/backend/services/packaging_cost.py`：

- 新增模块级 `MINIMUM_CHARGE_POLICY`，从快照 §4 块读取；快照不存在时返回
  `{"status": "pending", "chosen": "", "policy": "unresolved"}`。
- 新增 `minimum_charge_policy() -> dict`，返回至少 `{"status", "chosen", "policy", "fallback",
  "decided_by"}`：
  - `status == "chosen"` → `policy == chosen`、`fallback == ""`；
  - `status == "pending"` → `policy == "unresolved"`、`fallback == "sheet_labor_rate"`
    （未裁决期间保持现有数值行为，但必须**标注**，不许静默装作已裁决）。
- `compute_line()` 的结果行新增 `policy` 字段（恒有值），取值与 `minimum_charge_policy()["policy"]`
  一致。
- `min_charge_applied` 语义按所选口径重定义并写入 Spec：
  - ① → `MAX` 在原文内部，`min_charge_applied` 表示「该次取值由门限决定」；
  - ② → 主行无门限，恒为 `false`；
  - ③ → 保留现语义（门限压过表达式时为 `true`）。
- 未裁决（`status == "pending"`）时**必须**可观测：结果行的 `policy == "unresolved"`，
  且 `packaging_cost.minimum_charge_policy()["fallback"] == "sheet_labor_rate"`。
- `minimum_charge_policy()` 必须**每次从模块级 `MINIMUM_CHARGE_POLICY` 现读**，不得缓存到
  闭包/类属性里：这样测试与热更新才能通过替换 `MINIMUM_CHARGE_POLICY` 验证口径切换。
- 未裁决期间**数值行为保持现状**（与 ② 等价：`min_charge_applied` 恒 `false`），但必须标注
  `policy == "unresolved"`。**不允许**在未裁决期间偷偷按 ③ 的拼接公式产出 `min_charge_applied=true`
  —— 那等于没申报就先用了。

## 7. 契约 F：对账工具问题码

`tech_app/tools/extract_packaging_rules.py` 的 `audit_rules` 追加（仍为纯函数、不读文件不连库）：

| `problems[].code` | 触发条件 | `exit_code` |
| --- | --- | --- |
| `minimum_charge_policy_missing` | 快照无 `minimum_charge_policy` 块 | 1 |
| `minimum_charge_policy_unknown` | `status` 不在闭集 / `chosen` 不在 candidate 列表 / candidate 不是 3 条 | 1 |
| `minimum_charge_policy_golden_mismatch` | candidate 的 `golden` 与工作簿缓存值不一致（工作簿在场时） | 2 |
| `mixed_source_formula` | 门限表 ≠ 表达式表，且未申报 `declared_hybrid` | 1 |
| `minimum_charge_source_missing` | `minimum_charge > 0` 但没有 `minimum_charge_source_ref` | 1 |
| `minimum_charge_not_in_source` | `minimum_charge` 数值不在来源单元格原文里（挡 120） | 1 |
| `source_cell_mismatch` | `expression` 与 `source_cell` 原文不逐字等价 | 2 |
| `unmapped_variable` | 表达式变量在 `variable_map` 里没有对应单元格引用 | 1 |
| `hidden_sheet_source` | 任何来源指向隐藏 Sheet | 1 |

## 8. 非目标

- **本批不选口径**，不预设哪套正确；不因为「现状是 ③」就追认 ③。
- 不改任何 `expression` 数值、不改费率、不改损耗、不改第 7 批冻结黄金值（`GOLDEN_*` 常量）。
- 除 §3.1 明确列出的 `c1`/`c2`/`c4`（且必须在裁决之后）外，不改第 7 批任何断言。
- 不改三个原行业、不改第 8 批回传结构、不改数据库 schema、不改 BOM/工艺路线。
- 不从隐藏 Sheet 取任何规则、缓存值或门限。
- 不 commit / push / MR / tag / Release / 部署。

## 9. 自动化验收

| 命令 | 期望 |
| --- | --- |
| `./open-claude/.venv/bin/python tests/test_packaging_cost_minimum_charge_red.py` | 裁决前 FAIL（红）；裁决 + 实现后 OK |
| `./open-claude/.venv/bin/python tests/test_packaging_cost_rule_snapshot_red.py` | 不回归（仍为红，直到修复第 2 批实现） |
| `./open-claude/.venv/bin/python tests/test_packaging_cost_rule_routing_red.py` | 不回归 |
| `./open-claude/.venv/bin/python tests/test_packaging_cost_column_evidence_red.py` | 不回归 |
| `./open-claude/.venv/bin/python tests/test_packaging_cost_engine_red.py` | 修复第 1 批实现后只剩 `c1`/`c2`/`c4`（裁决前必须仍然失败，不许提前改） |
| `./open-claude/.venv/bin/python tech_app/tools/extract_packaging_rules.py --workbook "裕同包装项目-待开发/报价逻辑-0903.xlsx"` | 工作簿在场时按问题码返回退出码 |

## 10. 人工验收

- 把 `PKG-C-V-GROOVE` 的 `minimum_charge` 改成 120 → 工具退出码 1 报 `minimum_charge_not_in_source`。
- 把 `PKG-C-LAMINATION` 的 `source_sheet` 指向 `大货价核算1` → 报 `hidden_sheet_source`。
- 把 `source_cell` 从 `V2` 改成 `V3`（而 `expression` 仍是第 2 行写法）→ 报 `source_cell_mismatch`。
- 把 `minimum_charge_policy.status` 从 `pending` 改成 `chosen` 而 `decided_by` 仍为空 → 报
  `minimum_charge_policy_unknown`。
- 删掉 `minimum_charge_policy` 块 → 报 `minimum_charge_policy_missing`，且红测的 policy 组全部失败
  （不许静默回落到 ③）。

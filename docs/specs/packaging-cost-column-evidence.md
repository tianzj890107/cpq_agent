# 规格：包装成本逐列证据登记 —— 包装修复第 4 批

> 批次：包装验收修复**第 4 批**（共 4 批），依赖修复第 1 批（规则路由）与修复第 2 批
> （规则快照 `tech_app/agent_knowledge/rules/packaging_cost_rules.json`）已实现。
> 红测：`tests/test_packaging_cost_column_evidence_red.py`。
> 本批**只做证据登记与对账**：把「0903 采用口径里哪些列真有公式、哪些列根本没有公式」变成
> 机器可校验的产物，杜绝以后给没有证据的列**造公式**。不改任何公式与费率。

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_cost_column_evidence_red.py`

## 1. 实测证据（`报价逻辑-0903.xlsx` 可见 Sheet `报价-工费率`，第 2–15 行逐单元格统计）

| 列 | 列头 | 公式行 | 空行 | 样例单元格 | 样例公式（截断） |
| --- | --- | --- | --- | --- | --- |
| S | 材料价 | 13 | 1 | `S2` | `=K2*L2/1000000*M2/1000000*N2/1.13/J2+…*Q2/R2` |
| T | 普通印刷 | 0 | 14 | — | — |
| U | UV印刷 | 6 | 8 | `U2` | `=((30/60+R2/J2/12000)*(591+666))/R2+H2*I2/1000000*4/1000*115/1.13/J2` |
| V | 复膜 | 6 | 8 | `V2` | `=(H2*I2/1000000*1.7/1.13/J2+H2*I2/1000000*18/1000*18.5/J2)+((30/60+R2/J2/5500)*(197+145))/R2` |
| W | 覆转移膜 | 0 | 14 | — | — |
| X | 热烫-平压 | 3 | 11 | `X2` | `=((100*75*4)/1000000*8.5)+((200/60+R2/J2/5000)*(193+115))/R2` |
| Y | 热烫-圆压 | 0 | 14 | — | — |
| Z | 冷烫 | 0 | 14 | — | — |
| AA | 丝印 | 0 | 14 | — | — |
| AB | 过光油 | 0 | 14 | — | — |
| AC | 防刮花光/哑油 | 0 | 14 | — | — |
| AD | PET环保吸塑油 | 0 | 14 | — | — |
| AE | 视高迪UV | 0 | 14 | — | — |
| AF | 压纹 | 0 | 14 | — | — |
| AG | 击凹/凸 | 0 | 14 | — | — |
| AH | 裱纸 | 1 | 13 | `AH13` | `=((30/60+R13/J13/3500)*(209+126))/R13` |
| AI | 啤/切 | 13 | 1 | `AI2` | `=((120/60+R2/J2/6500)*(197.52+190.06))/R2` |
| AJ | 折页/装钉 | 0 | 14 | — | — |
| AK | V槽 | 2 | 12 | `AK5` | `=(60/60+R5/3000)*(195+111)/R5*2` |
| AL | 机贴盒/贴双面胶 | 0 | 14 | — | — |
| AM | 双面胶 | 0 | 14 | — | — |
| AN | 胶水 | 6 | 8 | `AN2` | `=H2*I2/1000000*0.74/J2` |
| AO | 人工/全检/包装 | 1 | 13 | `AO15` | `=(36+2)*40/180`（量纲不可核 → 见第 7 批 §2.6.2） |
| AP | 其他 | 0 | 14 | — | — |

项目级：`AT2 = SUM('包装运输 (2)'!$J$2:$J$12)`、`AU2 = MAX(1150/R2,1650/12/'包装运输 (2)'!$I$8/0.85)`。

结论：采用口径里**只有 9 个部件级列有公式**（S/U/V/X/AH/AI/AK/AN/AO），另外 **15 个列一行公式都没有、
也没有手填数字**（T/W/Y/Z/AA/AB/AC/AD/AE/AF/AG/AJ/AL/AM/AP）。这 15 个类别在真实报价里如果要出现，
必须由业务给出费率来源，**不允许由实现方或模型凭空补一条公式**。

## 2. 交付物：快照新增 `categories` 与 `column_evidence`

在 `tech_app/agent_knowledge/rules/packaging_cost_rules.json` 里增加两个顶层字段（不新建文件）：

顶层同时补一个 `"source_rows": 14`（`报价-工费率` 第 2–15 行的行数；`column_evidence` 的三项
行数之和必须等于它）。

```json
{
  "source_rows": 14,
  "categories": [
    {"cost_category": "material", "label": "材料价", "level": "part",
     "evidence_kind": "formula", "source_sheet": "报价-工费率", "source_cell": "S2",
     "source_column": "S", "formula_code": "PKG-C-MATERIAL"},
    {"cost_category": "silk_screen", "label": "丝印", "level": "part",
     "evidence_kind": "no_formula_in_workbook", "source_sheet": "报价-工费率",
     "source_column": "AA", "formula_code": "", "note": "0903 采用口径该列 14 行无公式、无手填"},
    {"cost_category": "packaging", "label": "包装", "level": "project",
     "evidence_kind": "formula", "source_sheet": "报价-工费率", "source_cell": "AT2",
     "source_column": "AT", "formula_code": ""}
  ],
  "column_evidence": [
    {"source_sheet": "报价-工费率", "source_column": "S", "header": "材料价",
     "formula_rows": 13, "blank_rows": 1, "hand_filled_rows": 0,
     "sample_cell": "S2",
     "sample_formula": "=K2*L2/1000000*M2/1000000*N2/1.13/J2+K2*L2/1000000*M2/1000000*N2/1.13*Q2/R2",
     "cost_category": "material"},
    {"source_sheet": "报价-工费率", "source_column": "AA", "header": "丝印",
     "formula_rows": 0, "blank_rows": 14, "hand_filled_rows": 0,
     "sample_cell": "", "sample_formula": "", "cost_category": "silk_screen"}
  ]
}
```

### 2.1 强制约束

- `categories` 必须**恰好**覆盖 `COST_CATEGORIES` 的 24 条 + `PROJECT_COST_CATEGORIES` 的 2 条，
  每条带 `label`（与运行时的中文名逐字相同）、`level`（`part` / `project`）、`evidence_kind`。
- `evidence_kind` 闭集：`formula` / `hand_filled` / `no_formula_in_workbook`。
- `evidence_kind == 'formula'` → `source_sheet` 必须是可见 Sheet，`source_cell` 必须指向一个
  **确实有公式**的单元格；部件级类别若在 `FORMULA_CATALOG` 里有实现，`formula_code` 必须填上。
- `evidence_kind == 'no_formula_in_workbook'` → `formula_code` 必须为空字符串，且该 `cost_category`
  **不得**出现在 `FORMULA_CATALOG` 里（不许给没有证据的列造公式）。
- `column_evidence` 必须覆盖 `报价-工费率` 的 24 个成本列（S→AP），逐列的
  `formula_rows + blank_rows + hand_filled_rows == 14`（第 2–15 行），且 §1 表里的 9 个有公式列的
  `sample_formula` 必须与工作簿逐字一致。
- 三个隐藏 Sheet 不得出现在 `source_sheet` / `column_evidence` 的任何条目里。
- `evidence_kind == 'hand_filled'` 目前**一条都不该有**（采用口径 14 行里没有手填数字）；
  若将来出现，必须同时给出 `hand_filled_rows` 与实际数值来源。

## 3. 交付物：对账工具扩展

`tech_app/tools/extract_packaging_rules.py` 的 `audit_rules` 追加证据校验（同一退出码体系）：

| `problems[].code` | 触发条件 | `exit_code` |
| --- | --- | --- |
| `category_evidence_missing` | `categories` 少条目 / 与运行时类别不一致 | 1 |
| `evidence_kind_unknown` | `evidence_kind` 不在闭集里 | 1 |
| `evidence_cell_has_no_formula` | `formula` 证据指向的单元格没有公式 | 2 |
| `formula_without_evidence` | `FORMULA_CATALOG` 里有、`categories` 里没有（或 kind 不是 `formula`） | 1 |
| `invented_formula_for_blank_column` | 某个 `no_formula_in_workbook` 的列在 `column_evidence` 里 `formula_rows > 0`，或该类别出现在 `FORMULA_CATALOG` 里 | 1 |
| `column_evidence_incomplete` | `column_evidence` 未覆盖 24 列，或某列三项行数之和 != `rules["source_rows"]` | 1 |

`audit_rules` 仍然保持**纯函数**（不读文件、不写文件、不连库）；`catalog_codes` 之外再允许注入
`runtime_categories=None`（默认取运行时 `COST_CATEGORIES` + `PROJECT_COST_CATEGORIES`）供红测使用。

## 4. 非目标

- 不改任何 `expression` / `minimum_charge` / `rounding` / 默认参数。
- 不改最低收费口径（`c1`/`c2`/`c4` 归修复第 3 批）。
- 不为 `no_formula_in_workbook` 的 15 个列实现任何计算；它们出现在需求里时，本引擎保持
  「无公式 → 缺口」的现状（`no_formula:<category>`），不许静默按 0 或按材料比例估算。
- 不改三行业、不改第 8 批回传结构、不改数据库 schema。
- 不 commit / push / MR / tag / Release / 部署。

## 5. 自动化验收

| 命令 | 期望 |
| --- | --- |
| `./open-claude/.venv/bin/python tests/test_packaging_cost_column_evidence_red.py` | 实现前 FAIL；实现后 OK |
| `./open-claude/.venv/bin/python tech_app/tools/extract_packaging_rules.py --workbook "裕同包装项目-待开发/报价逻辑-0903.xlsx"` | 退出码 0（工作簿在场时） |
| 第 1／2／7 批红测 | 不回归 |

## 6. 人工验收

- 把 `categories` 里 `silk_screen` 的 `evidence_kind` 改成 `formula` → 工具退出码 1 报
  `invented_formula_for_blank_column` / `evidence_cell_has_no_formula`。
- 给 `FORMULA_CATALOG` 加一条 `丝印` 公式 → 工具与红测同时报 `formula_without_evidence`。
- 把 `column_evidence` 里 `S` 的 `formula_rows` 从 13 改成 12 → 工具退出码 1 报
  `column_evidence_incomplete` / 与工作簿不符。

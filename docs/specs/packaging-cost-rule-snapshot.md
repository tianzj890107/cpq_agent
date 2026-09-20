# 规格：包装成本规则快照固化 —— 包装修复第 2 批

> 批次：包装验收修复**第 2 批**（共 4 批），依赖修复第 1 批
> （`docs/specs/packaging-cost-rule-routing.md`，`resolve_formula` 全链路单一入口）已实现。
> 红测：`tests/test_packaging_cost_rule_snapshot_red.py`。
> 本批把「Excel 里的公式」固化成**随代码发布的审核快照**，让公式只有一个人工维护来源，
> 并给出一个**离线**校验工具防止快照与工作簿漂移。**不改任何公式口径、不改最低收费、不改三行业。**

## 1. 为什么需要这一批（现状与风险）

第 7 批 Spec 已声明：运行时**不读** Excel、不让 Agent 读 Excel、不执行任意 Excel 公式。这个方向
是对的，现状也遵守了（全仓 `openpyxl` / `load_workbook` 只出现在测试与一次性脚本）。但公式目前
**被人工抄在四处**：

| 位置 | 内容 | 风险 |
| --- | --- | --- |
| `packaging_cost.py` 的 `FORMULA_CATALOG` | 20 条表达式 + 默认参数 | 与库表、快照不一致 |
| `da_seed_packaging.py` 的 `COST_FORMULAS` | 7 条中文散文 `draft`（不可执行） | 与真实公式同名不同义 |
| `kb_packaging_cost_formula`（库） | reviewed 行（第 1 批起才真正生效） | 谁改的、改自哪个单元格无记录 |
| 红测里的黄金常量 | 0903 缓存值 | 抄错时"测试也跟着抄错"，自洽但整体错 |

「抄错但自洽」无法靠代码评审根除。本批引入**版本化机器可读快照 + 离线校验工具**：
工作簿缓存值、快照里的 `expected_result`、运行时引擎复算，三者必须同时相等。

## 2. 交付物 A：规则快照 `tech_app/agent_knowledge/rules/packaging_cost_rules.json`

（放在 `tech_app/agent_knowledge/rules/` 与既有 `quote_product_params.json` / `process_rules.json`
同目录，保持仓库既有约定。）

### 2.1 Schema

```json
{
  "rule_set": "packaging_cost_v1",
  "source_file": "报价逻辑-0903.xlsx",
  "source_sha256": "974d9484414824d0bbfd2c83fb0c6044e4348c7a407e1ae0f58699bb60d2acc0",
  "source_sheets": ["报价-工费率", "包装运输", "包装运输 (2)", "成本细分"],
  "review_status": "reviewed",
  "generated_at": "2026-09-20",
  "formulas": [
    {
      "formula_code": "PKG-C-MATERIAL",
      "cost_category": "material",
      "expression": "（与 FORMULA_CATALOG 逐字相同）",
      "minimum_charge": 0,
      "rounding": 4,
      "rate_code": "",
      "loss_scope": "材料开料",
      "source_sheet": "报价-工费率",
      "source_cell": "S2",
      "verify_inputs": {"cut_length": 889, "cut_width": 705, "gsm": 157, "ton_price": 6300,
                        "imposition_count": 1, "tax_factor": 1.13, "proof_base": 450,
                        "quote_quantity": 1000},
      "expected_result": 0.7954641993584073,
      "formula_version": "packaging_cost_v1"
    }
  ]
}
```

### 2.2 强制约束

- `formulas` 必须**恰好**覆盖 `FORMULA_CATALOG` 的 20 个 `formula_code`（9 条 `PKG-C-*` +
  11 条 `PKG-P-*`）：一条不少、不多、不重。
- 每条的 `cost_category` / `expression` / `minimum_charge` / `rounding` / `rate_code` / `defaults`
  必须与运行时 `FORMULA_CATALOG` **逐条相等**（本批冻结第 1 批的表达式文本，不许借机改口径）。
- `source_sheet` 只能是**可见** Sheet；隐藏 Sheet（`大货最终定价` / `大货价核算1` /
  `首批试产毛利核算`）**不得**出现在快照里。`source_cell` 形如 `AB12`，不含 `#`。
- `loss_scope` 必须是非空字符串（`kb_packaging_cost_formula.loss_scope` 是
  `tests/test_packaging_knowledge_base_seed_red.py::test_b11` 的断言项）。
- `expected_result` = 用 `verify_inputs` 通过运行时引擎复算的结果（容差 1e-6），**同时**等于工作簿
  该单元格的缓存值（容差 1e-9）。两条一起才排除"抄错公式"与"抄错答案"。
- `source_sha256` = `报价逻辑-0903.xlsx` 的 SHA-256（实测
  `974d9484414824d0bbfd2c83fb0c6044e4348c7a407e1ae0f58699bb60d2acc0`）。

### 2.3 必须逐字照抄的黄金值（已用第 1 批冻结口径复算核对）

| `formula_code` | Sheet | 单元格 | `verify_inputs` 要点 | `expected_result` |
| --- | --- | --- | --- | --- |
| `PKG-C-MATERIAL` | 报价-工费率 | S2 | cut 889×705、gsm 157、吨价 6300、校版 450、q 1000 | `0.7954641993584073` |
| `PKG-C-PRINT-UV` | 报价-工费率 | U2 | 上机 889×700、30/12000、591+666、墨 4µm/115、q 1000 | `0.9865756637168142` |
| `PKG-C-LAMINATION` | 报价-工费率 | V2 | 上机 889×700、30/5500、197+145、膜 1.7/18µm/18.5、q 1000 | `1.3766112580048271` |
| `PKG-C-HOT-STAMP-FLAT` | 报价-工费率 | X2 | 烫金面积 30000mm²、200/5000、193+115、箔 8.5、q 1000 | `1.3432666666666671` |
| `PKG-C-DIE-CUT` | 报价-工费率 | AI2 | 120/6500、197.52+190.06、q 1000 | `0.8347876923076923` |
| `PKG-C-V-GROOVE` | 报价-工费率 | AK5 | 60/3000、195+111、次数 2、q 1000 | `0.816` |
| `PKG-C-GLUE` | 报价-工费率 | AN2 | 上机 889×700、胶 0.74 | `0.46050199999999997` |
| `PKG-P-CARTON` | 包装运输 | J2 | 520×420×425、用量 1、单价 3、装数 4 | `2.1108074127397023` |
| `PKG-P-PAD` | 包装运输 | J3 | 510×410、用量 2、单价 1.55、装数 4 | `0.28404101945273708` |
| `PKG-P-PALLET` | 包装运输 | J12 | 用量 1、单价 70、装数 120 | `0.51622418879056053` |

其余 10 条（`PKG-C-MOUNTING` / `PKG-C-LABOR` 与 8 条 `PKG-P-*`）的 `verify_inputs` 与
`expected_result` 由实现方从对应可见 Sheet 的同一行输入单元格与缓存值填入；红测对它们做
**自洽复算**断言（表达式在 `verify_inputs` 下的结果 == `expected_result`）。

## 3. 交付物 B：幂等导入（`da_seed_packaging.py`）

新增 `seed_packaging_cost_rules(*, rules_path=None, overwrite=False) -> dict`，
把快照里的 20 条写进 `kb_packaging_cost_formula`（主键 `formula_code`）：

| 库表现状 | 行为 |
| --- | --- |
| 该 `formula_code` 不存在 | 插入：`review_status='reviewed'`、`formula_version=rule_set`、`source='packaging_rules_json'`、`industry='packaging'`、`status='active'`、`loss_scope` 取快照值 |
| 已存在且 `source='packaging_rules_json'` | `formula_version` 不同 → 更新；相同 → 跳过（幂等） |
| 已存在且 `source != 'packaging_rules_json'`（业务人工维护） | **永不覆盖**，计入 `skipped_user_modified`（`overwrite=True` 也不覆盖） |
| 已存在且 `review_status='retired'` | 跳过（业务已下线，不复活） |

- 返回 `{"inserted": n, "updated": n, "skipped": n, "skipped_user_modified": n, "rule_set": ...}`。
- `seed_packaging()` 末尾调用它（这样 `python -m backend.storage.da_seed_packaging` 一次装好）。
- **`COST_FORMULAS` 的 7 条 `PKG-F-*` 中文散文 `draft` 一条不许删、不许改成 reviewed**
  （第 3 批红测 `test_b3`/`test_b4` 与第 7 批红测逐条断言 `len(COST_FORMULAS) == 7`）。

## 4. 交付物 C：离线校验工具 `tech_app/tools/extract_packaging_rules.py`

与既有 `tech_app/tools/build_quote_product_params.py` 同风格（argparse、中文 docstring、`--check` /
`--write`）。**只在开发与规则升级时运行；生产运行时不调用它。**

### 4.1 IO 与判定必须分开（红测直接调用判定函数）

| 函数 | 职责 |
| --- | --- |
| `load_workbook_cells(path) -> dict` | 用 openpyxl 只读；返回 `{sheet_name: {"state": "visible"\|"hidden", "formulas": {cell: str}, "cached": {cell: object}}}`。**只读**，不写工作簿 |
| `audit_rules(cells, rules, *, workbook_sha256, workbook_name="", catalog_codes=None) -> dict` | **纯函数**：不读文件、不写文件、不连库。`catalog_codes=None` 时用运行时 `FORMULA_CATALOG` 的 code 集合，测试可注入小集合（红测就是这么用的）。返回 `{"ok": bool, "exit_code": int, "problems": [{"code": ..., "where": ..., "detail": ...}], "skipped_hidden_sheets": [...], "mismatches": [...]}` |
| `main(argv=None) -> int` | CLI：读文件 → 调 `audit_rules` → 打印报告 → 返回退出码；`--write` 才写 JSON |

### 4.2 拒绝规则（`problems[].code` 闭集，必须逐条可断言）

| `code` | 触发条件 | `exit_code` |
| --- | --- | --- |
| `source_sha256_mismatch` | 传入的 `workbook_sha256` != `rules["source_sha256"]` | 3 |
| `broken_reference` | 可见 Sheet 出现 `#REF!`（公式或缓存值） | 2 |
| `cached_without_formula` | 单元格没有公式但有缓存值（"只有结果没有公式"） | 2 |
| `hidden_sheet_has_formula` | 隐藏 Sheet 里有公式 → 只记录 + 列进 `skipped_hidden_sheets`，**不影响 `ok`** | 0 |
| `source_sheet_missing` / `source_sheet_hidden` | 快照指向的 Sheet 不存在或不可见 | 1 |
| `source_cell_has_no_formula` | 快照指向的单元格没有公式 | 2 |
| `cached_value_mismatch` | 缓存值 != `expected_result`（容差 1e-9） | 1 |
| `recompute_mismatch` | 用 `verify_inputs` 复算 != `expected_result`（容差 1e-6） | 1 |
| `formula_set_mismatch` | 快照与 `FORMULA_CATALOG` 的 code 集合不一致 | 1 |
| `write_refused` | `--write` 且目标 JSON 的 `review_status='reviewed'` 且未给 `--force` | 4 |

- `ok = (exit_code == 0)`；多个问题同时出现时 `exit_code` 取**最大值**（3 > 2 > 1 > 0）。
- `--check`（默认）**绝不写文件**；`--write` 写 JSON 时保留 `review_status` 与 `source_sha256`，
  不允许工具自行把 `sha256` 改成新值（改来源 = 人工决定 + 新 `rule_set`）。

### 4.3 运行时不依赖工具

`tech_app/backend/**` **不得** import `openpyxl`，不得打开 `.xlsx`。工具与快照都不是生产路径的
必需依赖：没有它们，第 1 批的「库 reviewed → 内置兜底」路由必须照常工作。

## 5. 非目标

- 不改表达式、`minimum_charge`、默认参数、`rounding`（口径问题归修复第 3 批）。
- 不改第 2–6 批种子内容；不删 `COST_FORMULAS` 的 7 条 draft。
- 不改三行业链路、不改第 8 批回传/报价单结构、不改数据库 schema。
- 不引入运行时读 Excel、不让 Agent 读 Excel、不做"公式表达式自动翻译"（DSL 与 Excel 公式的
  对应关系仍由人工转译 + 本批的三方对账来保证）。
- 不 commit / push / MR / tag / Release / 部署。

## 6. 自动化验收

| 命令 | 期望 |
| --- | --- |
| `./open-claude/.venv/bin/python tests/test_packaging_cost_rule_snapshot_red.py` | 实现前 FAIL；实现后 OK |
| `./open-claude/.venv/bin/python tests/test_packaging_knowledge_base_seed_red.py` | 46 条仍 OK（含"重复 seed 不新增行"） |
| `./open-claude/.venv/bin/python tests/test_packaging_cost_engine_red.py` | 只剩 `c1`/`c2`/`c4`（第 3 批） |
| `./open-claude/.venv/bin/python tech_app/tools/extract_packaging_rules.py --workbook "裕同包装项目-待开发/报价逻辑-0903.xlsx"` | 退出码 0（工作簿在场时） |

## 7. 人工验收

- 改快照里某条的 `expected_result` 一位小数 → 校验工具退出码 1 并打印 code/sheet/cell 差异。
- 把工作簿另存一份改一个数字 → 工具退出码 3（sha256 变化），提示"来源已变，需人工确认新 rule_set"。
- 手工在库里把 `PKG-C-LAMINATION` 改成 reviewed 且换个表达式 → 再跑导入，**不被覆盖**。

# 规格：逆向快速报价 第 13 批 —— 权威费率的导入路径（demo 退役 + 校验 + 计划）

状态：Spec + 红测（**未实现**）
红测：`tests/test_quick_quote_authoritative_rate_import_red.py`
依赖：批 3（`cpq_quick_quote_workspace.py` 的差异价规则表与 `RULE_KINDS`）、批 8（`rule_authority()` /
`authority_summary()` / `cpq_quick_quote_price.is_formal()`）。
前序证据：2026-09-21 现场实测 —— `cpq_kb.kb_quick_quote_delta_rule` 4 条费率全是
`source_type=demo` / `review_status=draft`，`authority_summary()["authoritative"] = False`，
于是 `is_formal()` 永远为假、**正式快速报价在当前数据下不可达**。批 8 §2.4 只把「换成权威费率
的流程」写进 `DEPLOYMENT.md`（文档），`§4 非目标`明确不替业务定费率数值 —— 但**没有任何工具或
接口能把权威费率导进去**，也没人把 4 条 `demo` 退场（换成权威后它们还在库里，`authority_summary()`
继续判 False）。

## 0. 一句话目标

让「把权威工作簿费率换成正式口径」从**一段文档**变成**一条可执行、可预演、可审计的命令**：
给一批权威行 → 先算出「要写哪些、要退哪些、哪些不合规为什么」→ 人确认后才写；
写完之后 `authority_summary()["authoritative"]` 为真、`is_formal()` 可达。

## 1. 现场缺口（实测，不是推断）

| 位置 | 现状 |
| --- | --- |
| `cpq_quick_quote_workspace.py` | 只有 `load_rules()`（读）与 `rule_authority()` / `authority_summary()`（判）。**没有任何写入 / 校验 / 退役函数** |
| `cpq_kb` | `kb_quick_quote_delta_rule` 的 4 条 `QQQ-DEMO-*` 是 `demo` / `draft`，没有任何代码路径能把它们的 `source_type` 改成 `workbook` 或让它们退场 |
| `scripts/` | 没有费率导入工具（案例侧有 `import_dwg_quick_quote_cases.py`，费率侧没有对应物） |
| `DEPLOYMENT.md` | 批 8 §2.4 只登记了「要改成 `source_type='workbook'` + `review_status='reviewed'` + 写 `source_ref`」——**谁去改、怎么改、改完怎么验，都还得靠人手写 SQL** |

结论：正式报价的**数据入口缺失**。这跟案例侧（有 `save_case()` 与导入工具）不对称。

## 2. 契约

### 2.1 新增常量（`cpq_quick_quote_workspace.py`）

```python
#: 导入行必填键（缺一即 blocked.missing_key；source_ref 是批 8 要求的"指向工作簿与工作表"）。
RATE_IMPORT_REQUIRED_KEYS = ("rule_code", "field_key", "rule_kind", "unit", "industry",
                             "source_type", "review_status", "version",
                             "effective_from", "source_ref")
#: 导入通道只认权威工作簿（与 AUTHORITATIVE_RATE_SOURCES 同值，不许另写一份）。
AUTHORITATIVE_IMPORT_SOURCE = "workbook"
#: 导入结果码闭集（纯函数、接口、工具共用）。
RATE_IMPORT_REASONS = ("missing_key", "source_not_authoritative", "not_reviewed",
                       "invalid_value", "duplicate_rule_code", "unknown_field_key",
                       "unknown_rule_kind", "industry_mismatch")
#: 每个码一句中文（工具与接口共用，不许各写一份）。
RATE_IMPORT_LABELS = {…}
```

### 2.2 `rate_import_plan(rows, *, existing=None, today=None, retire_demo=True) -> dict`

纯函数（不写库、不改入参）：

```python
{"write": [ {规则行…}, … ],                     # 通过校验的行，按 rule_code 升序
 "retire": [ {"rule_code": "QQQ-DEMO-QTY-BAND", "reason_code": "demo_rate",
              "label": …, "source_type": "demo", "review_status": "draft"}, … ],
 "blocked": [ {"row_index": 0, "rule_code": "…", "reason_code": "missing_key",
               "label": …, "detail": "缺必填键：unit、source_ref"}, … ],
 "projected": [ {规则行…}, … ],                 # 执行后的**完整**规则集（existing − retire + write）
 "counts": {"rows": 5, "write": 5, "retire": 4, "blocked": 0},
 "authoritative": True,                        # == authority_summary(projected)["authoritative"]
 "headline": "…", "detail": "…"}
```

规则：

1. `existing=None` → `load_rules(None)`（读不到照旧抛 `CaseLibraryUnavailable`，回落空列表是不允许的）；
2. 逐行校验（先命中先返回，同一行只报第一条）：
   - 缺 `RATE_IMPORT_REQUIRED_KEYS` 任一 → `missing_key`（`detail` 列出缺哪些键）；
   - `source_type` 不是 `AUTHORITATIVE_IMPORT_SOURCE` → `source_not_authoritative`；
   - `review_status` 不是 `reviewed` → `not_reviewed`；
   - `industry` 不是 `INDUSTRY` → `industry_mismatch`；
   - `field_key` 不在 `FIELD_KEYS` → `unknown_field_key`；
   - `rule_kind` 不在 `RULE_KINDS` → `unknown_rule_kind`；
   - `invalid_value`：`version` 不是正整数；`effective_from` 不是 ISO 日期；
     `effective_to` 非空且早于 `effective_from`；`rule_kind == "rate"` 时 `rate` 不是 > 0 的数；
     `rule_kind == "band"` 时 `breakpoints_json` 不是合法的 `[[阈值, 系数], …]`；
   - `duplicate_rule_code`：同一批内重复，或与 `existing` 里**已权威**的行同 `rule_code`
     （与 `existing` 里的 `demo` 行同码 = **替换**，不算重复）；
3. `retire_demo=True`（缺省）→ `existing` 里 `source_type` 为 `demo`、且 `rule_code` 不在
   `write` 名单里的行进 `retire`（`reason_code="demo_rate"`），按 `rule_code` 升序；
   `retire_demo=False` → `retire` 恒为空（保留演示行，`authoritative` 照样按 `projected` 算）；
4. `projected` = `existing` 去掉 `retire` 的 `rule_code`，再并入 `write`（同 `rule_code` 时以
   `write` 为准），按 `rule_code` 升序；
5. `authoritative` 必须等于 `authority_summary(projected)["authoritative"]`；
6. `blocked` 非空**不阻断** `write` / `retire` 的计算（调用方决定是否执行），但 `headline` 必须
   明说是"预演有不通过的行"。

### 2.3 导入工具 `scripts/import_quick_quote_rates.py`

- 参数：`--file <path>`（必填，`.json`：行数组；`.csv`：表头即键）、`--confirm`（真写）、
  `--keep-demo`（等价 `retire_demo=False`）、`--user <name>`（留痕）、`--json`；
- **默认 dry-run**：只打印 `rate_import_plan()` 的结果（write / retire / blocked / headline），
  不写库；dry-run 分支里**不得**出现任何写入调用；
- `blocked` 非空 → 退出码非零（不允许"忽略坏行继续写"）；`--confirm` 且 `blocked` 为空 → 写库并
  打印 `counts` 与 `authoritative`；
- 写库口径：`source_ref` 必须原样落库（批 8 §2.4 要求能追到工作簿与工作表）；
- 工具必须调用 `rate_import_plan()`（不许在工具里另写一份校验）。

### 2.4 DEPLOYMENT.md 登记

必须登记：命令、dry-run → `--confirm` 的两步用法、`--keep-demo` 的含义、导入后怎么验
（`authority_summary()` 的 `authoritative` 必须为真，`is_formal()` 才可能为真）。

### 2.5 验收口径（本批唯一）

用 fixture 造一批**合格**的权威行（覆盖 4 条现有 `QQQ-DEMO-*` 的 `field_key`）：

- `rate_import_plan(rows, existing=demo_rules)["blocked"]` 为空；
- `retire` 必须包含那 4 条 `demo` 行；
- `authority_summary(plan["projected"])["authoritative"] is True`，且 `blocked_by` 为空；
- 用 `plan["projected"]` 算一次差异价，`cpq_quick_quote_price.is_formal(quote)` 必须为真
  （即"正式快速报价"在这份数据下可达）。

## 3. 红测映射（`tests/test_quick_quote_authoritative_rate_import_red.py`）

| 组 | 覆盖 |
| --- | --- |
| A | 命名契约：必填键、导入来源、结果码闭集、中文标签覆盖全部码 |
| B | 校验每个码各一条（缺键 / 非 workbook / 未审核 / 行业 / field_key / rule_kind / 值域 / 重复） |
| C | `demo` 退役：缺省退役 4 条、`retire_demo=False` 不退役、同 `rule_code` 替换不算重复 |
| D | `projected` 与 `authoritative`：与 `authority_summary()` 同值；`is_formal()` 在这份数据下为真 |
| E | 工具：文件存在、`--help` 暴露参数、默认 dry-run、dry-run 分支无写库调用、必须用 `rate_import_plan()` |
| F | `DEPLOYMENT.md` 登记命令与验收口径 |

## 4. 非目标

- **不替业务定费率数值**（本批只做"能不能导、导了算不算权威"）；
- 不改差异价公式、门槛判据、偏差口径（批 3 / 批 4 口径不动）；
- 不改案例侧（批 6 / 批 12 的案例库与维护路径）；
- 不在红测里连 PG / 写业务数据（`existing` 一律 fixture 注入；工具只跑 `--help` 与源码纪律断言）。

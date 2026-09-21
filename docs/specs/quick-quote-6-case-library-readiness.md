# 规格：逆向快速报价 第 6 批 —— 案例库现状话术、补数据动作与 DWG 实样导入

状态：Spec + 红测（已实现）
红测：`tests/test_quick_quote_case_library_readiness_red.py`
依赖：批 1（案例模型与准入）、批 2（候选检索）。
**本批假设前五批已实现**（`cpq_quick_quote_case.py` / `_match.py` / `_workspace.py` /
`_price.py` / `_file.py` 与前端 `quick-quote-panel.js` 都在）。

## 0. 本批要解决的问题

前五批把链路做完了，但**销售点进快速报价会看到一张空表**。今天（2026-09-21）实测：

```
cpq_wf.cpq_qq_standard_case → 2 行
   QQ-YT-DWG-WINE-700ML   dwg_confirmed / draft / standard_price = 0  → eligible False（missing_fields）
   QQ-YT-DWG-ROUND-10PC   dwg_confirmed / draft / standard_price = 0  → eligible False（missing_fields）
load_cases(None) → 2 行，eligible = 0
GET /api/quick-quote/cases → {"case_total": 2, "eligible_total": 0}
```

`case_total = 2 / eligible_total = 0` 这个状态在页面上**和"库是空的"长得一样**：都是一张空表、
一句"没有候选"。但两者的处置完全不同 ——

| 状态 | 真相 | 该让人做什么 |
| --- | --- | --- |
| 库为空（0 行） | 从来没沉淀过案例 | 导入标准案例，或改走精准报价 |
| 有案例但 0 条可用 | 有资产，**缺数据 / 缺审核** | 按原因补数据、审到「已审核」；不用重新造案例 |

本批把这两件事在**接口和页面上分开**，并把"DWG 实样怎么进案例库"的口径固化下来。

## 1. 现场缺口（实测，不是推断）

| 位置 | 现状 |
| --- | --- |
| `cpq_quick_quote_case.py` | 只有 `case_missing_fields()`（列出缺哪些**字段键**），没有"缺哪几项、补完之后要不要审、下一步动作是什么"的可执行清单 |
| `GET /api/quick-quote/cases` | 出参只有 `case_total` / `eligible_total` / `notes`，没有任何"现在该怎么办"的字段 |
| `tech_app/frontend/quick-quote-panel.js` | `renderCases()` 只画表；0 行时就是一张空表，没有空态文案，也没有"转精准报价"的出口 |
| 仓库 | 快速报价的**导入路径**没有文档化：`save_case()` 是唯一入口，但 `scripts/` 下没有工具、`DEPLOYMENT.md` 没写补数据流程 |
| `cpq_quick_quote_case.normalize_case()` | 只保留 `CASE_FIELDS`，`source_sha256` / `parser_version` / `confirmed_by` / `confirmed_at` **进不了入库路径**（实测：走 `save_case()` 写出来永远是 NULL） |
| `cpq_quick_quote_case._row_value()` | 价格列 `NOT NULL DEFAULT 0`，但不给价时返回 `None` → 直接 `save_case()` 会抛 `psycopg.errors.NotNullViolation`（实测 DETAIL 指向 `standard_cost`） |

## 2. 契约

### 2.1 `library_readiness(cases=None, today=None, config=None) -> dict`

库现状的**唯一事实源**（接口、前端、CLI 都读它，不许各自拼文案）。

```python
READINESS_VERDICTS = ("empty", "no_eligible", "ready")
READINESS_ACTIONS = ("import_standard_case", "fill_case_fields", "review_case",
                     "refresh_case", "transfer_to_precise")
CASE_FIX_KINDS = ("fill", "review", "extend", "retire")
#: 准入原因的**中文标签唯一事实源**（前端不许再各写一份；`blocked_by[].label` 直接用它）。
REASON_LABELS = {"ok": "可用", "missing_fields": "缺必需字段", "retired": "已停用",
                 "industry_mismatch": "非包装案例", "source_not_authoritative": "来源不权威",
                 "not_reviewed": "未审核", "expired": "已过期"}

def library_readiness(cases=None, *, today=None, config=None) -> dict:
    """{"verdict", "headline", "detail", "case_total", "eligible_total",
        "blocked_by": [{reason_code, label, count, fix, cases}],
        "next_actions": [{action, label, hint}]}"""
```

- `cases=None` → 真读库；读不到 → `CaseLibraryUnavailable`（**不回落成"空库"**，空库会被当成
  "没有可复用的成交经验"，与批 1 §2.3 同一条纪律）。
- `cases` 显式传入 → 纯离线口径（不改入参）。
- 三态判定：
  1. `case_total == 0` → `verdict="empty"`；`headline` 必须含 `案例库还没有案例`；
     `detail` 必须含 `快速报价` 与 `精准报价`（说清出路）；`next_actions` 按序必须含
     `import_standard_case` 与 `transfer_to_precise` 两个动作。
  2. `case_total > 0 and eligible_total == 0` → `verdict="no_eligible"`；
     `headline` 必须含 `"%d 条案例" % case_total` 与 `0 条可用于快速报价`。
  3. `eligible_total > 0` → `verdict="ready"`；`headline` 必须含 `可用于快速报价` 与条数。
- `blocked_by`：**只统计不合格**的案例，按 `reason_code` 聚合。
  - `label` 用人话（复用批 1 的 `REASON_LABELS` 口径，`ok` 不出现）；
  - `count` 是该原因的案例数，`cases` 是 `case_code` 列表（升序）；
  - 排序：`count` 降序 → `reason_code` 升序（同输入同输出，前端不许再排一次）；
  - `fix` 是**动作句**，不是字段名堆砌；`missing_fields` 这一组的 `fix` 必须点名缺哪些字段的
    中文标签（复用 `FIELD_LABELS`）。
- `next_actions`：`action` 取自闭集 `READINESS_ACTIONS = ("import_standard_case",
  "fill_case_fields", "review_case", "refresh_case", "transfer_to_precise")`；
  `empty` 态必须给 `import_standard_case` + `transfer_to_precise`；
  `no_eligible` 态必须给 `fill_case_fields` 或 `review_case`（按 `blocked_by` 的实际原因给，
  至少给一个）+ `transfer_to_precise`；`ready` 态 `next_actions` 可以为空列表。

### 2.2 `case_fix_plan(case) -> dict`

一条案例的**可执行补数据清单**（比 `case_missing_fields()` 更进一步：它只给字段键）。

```python
{"case_code": str, "eligible": bool, "reason_code": str,
 "missing_fields": [str], "missing_labels": [str],
 "actions": [{"field", "label", "kind"}], "price_present": bool}
```

- `missing_fields` 按 `QUICK_QUOTE_REQUIRED_FIELDS` 顺序；`missing_labels` 与之一一对应。
- `kind` ∈ `CASE_FIX_KINDS = ("fill", "review", "extend", "retire")`：
  - 缺必需字段 → 每个字段一条 `kind="fill"`（`label` 用 `FIELD_LABELS`）；
  - `review_status != "reviewed"` → 一条 `{"field": "review_status", "kind": "review"}`；
  - `source_type` 不在 `QUICK_QUOTE_ALLOWED_SOURCES` → 一条 `{"field": "source_type",
    "kind": "extend"}`（来源要升格到权威口径）；
  - `review_status == "retired"` → 一条 `{"field": "review_status", "kind": "retire"}`，
    且**不再**给 `fill`（已停用的案例补字段没有意义，与批 1 §2.3 的优先级一致）；
  - 已 `eligible` → `actions = []`。
- `price_present` = 达标口径下的 `standard_price` 是否存在（`standard_price == 0` 视为**不存在**，
  与 `_is_blank()` 同口径）。

### 2.3 接口 `GET /api/quick-quote/cases` 增加 `readiness` 段

`_handle_quick_quote_cases()` 出参新增 `"readiness"`（就是 §2.1 的返回值），其余字段一字不变。
库不可用时（`CaseLibraryUnavailable`）仍回 `ok=False`，并给一个 `readiness` 段说明
**库读不到**（`verdict="unavailable"` 归在 payload 里由 `error` 表达即可，本条只要求
`ok=False` 时不得给出一个假装"空库"的 `readiness`）。

### 2.4 前端：空态 / 全不合格态 + 转精准出口

`tech_app/frontend/quick-quote-panel.js`：

- 新增 `window.QuickQuotePanel.renderReadiness(readiness)`：把它渲染进一个带
  `data-qq-readiness` 属性的节点；节点必须带上 `data-qq-verdict="<verdict>"`。
- `renderCases()` 现有的表格保留；**0 行时不许只画空表**：必须换成空态/全不合格态文案。
- 必须给「转精准报价」出口：一个 `data-qq-action="transfer_precise"` 的按钮或链接。
- 文案一律从 `readiness` 里取，**前端不许自己拼**"没有案例"这类判断句（否则接口说 2 条、
  页面说 0 条，现场无法对账）。

### 2.5 DWG 实样导入口径（写进文档，工具已存在）

- 工具：`scripts/import_dwg_quick_quote_cases.py`（默认 dry-run，`--confirm` 才写库；
  `--price` / `--cost` / `--review-status` / `--user` 可选）。它把 `cpq_kb.kb_packaging_box_type`
  里 `YT-DWG` 前缀的盒型（连同零件、工序模板）派生成案例行，**不另抄一份数据**。
- **幂等**：同一 `case_code` 内容未变时重复执行不新增行、不升版本；内容变了才写。
- **不编数据**：DWG 里没有价格，`--price` 不给就留空（`standard_price = 0`），
  准入如实报 `missing_fields`，绝不拿一个来路不明的数当基准价。
- `DEPLOYMENT.md` 必须登记：这张表怎么补案例、怎么把 `draft` 审到 `reviewed`、
  没有价格的案例在页面上长什么样。
- 修掉 §1 里那两处入库缺口（**这是本批的必做项**）：
  1. `normalize_case()` 必须保留 `source_sha256` / `parser_version` / `confirmed_by` /
     `confirmed_at` 四个 DWG 通道键（`CASE_COLUMNS` 里本来就有它们，
     `save_case()` 落不了的根因是 `normalize_case()` 把它们丢了）；
  2. 价格列为空时 `_row_value()` 必须给 `0`（列就是 `NOT NULL DEFAULT 0`，
     且 `_is_blank()` 本就把 0 当"没有基准价"），`save_case()` **不得**再抛
     `psycopg.errors.NotNullViolation`。

## 3. 红测映射（`tests/test_quick_quote_case_library_readiness_red.py`）

| 组 | 覆盖 |
| --- | --- |
| A | 命名契约：`READINESS_VERDICTS` / `READINESS_ACTIONS` / `CASE_FIX_KINDS`、两个新函数存在 |
| B | `library_readiness()` 三态：空库 / 有案例但 0 可用 / 有可用；文案必含关键句 |
| C | `blocked_by` 聚合与**确定性排序**；缺字段的 `fix` 点名中文标签 |
| D | `next_actions` 闭集与分态取值；`ready` 可以为空 |
| E | 读不到库抛 `CaseLibraryUnavailable`（**不回落空库**）；纯函数不改入参 |
| F | `case_fix_plan()`：缺价格 / 未审核 / 来源不权威 / 已停用 / 已可用 五种 |
| G | 接口 `readiness` 段（`_handle_quick_quote_cases`，离线注入 `load_cases`） |
| H | 前端：`renderReadiness` + `data-qq-readiness` + `data-qq-verdict` + 转精准出口 + 不自己拼判断句 |
| I | 两处入库缺口：`normalize_case()` 保留 DWG 通道列；`_row_value()` 价格列为空给 0 |
| J | `DEPLOYMENT.md` 登记补案例 / 审核 / 无价格案例的说明 |

## 4. 非目标

- 不改批 1 的准入判据（`reason_code` 闭集、优先级、`QUICK_QUOTE_ALLOWED_*` 一个字不动）；
- 不改批 2 的检索排序、批 3 的差异价、批 4 的门槛与出价、批 5 的解析客户端；
- 不在本批实现统一解析服务（那是下一批）；
- 不引入任何"演示价"：**没有价格的案例就是不能用**，本批只负责把这件事说清楚。

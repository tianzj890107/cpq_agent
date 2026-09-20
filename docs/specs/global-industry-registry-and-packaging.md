# 规格：全局行业口径统一（唯一行业注册表 + 新增包装行业）—— 包装第 1 批

> 批次：包装行业 8 批计划的**第 1 批**（依赖：无；后续第 2–8 批都依赖本批）。
> 红测：`tests/test_industry_registry_unified_red.py`（实现前失败）。
> 业务依据：`蜀同包装项目-待开发/` 下的 `YTBZ-报价业务流程-20260907.xlsx`、
> `成本测算明细.xlsx`、`礼盒盒型库_数据样例.xlsx`，以及后续补充的 `报价逻辑-0903.xlsx`
> （只作口径与黄金样例来源，不直接运行 Excel 公式）。

## 1. 背景与真实问题

平台现在只有三个行业口径 —— 半导体、电池、电器 —— 而且这份口径**没有唯一事实源**，
散落在至少 7 处各自维护：

| 位置 | 现状 |
| --- | --- |
| `报价首页.html:1322` | 硬编码 `<option>` 三项 |
| `报价首页.html:2178` | 硬编码 `TECH_INDUSTRIES = ['semiconductor','battery','appliance']` |
| `tech_app/frontend/cpq-industry.js:14` | 硬编码 `OK = ['semiconductor','battery','appliance']` |
| `tech_app/frontend/tech-task.js:81` | 硬编码 `TT_INDUSTRIES`（顺序还不同） |
| `tech_app/frontend/home.js:346` | 硬编码三项 |
| `tech_app/frontend/requirement-create.js:86,126` | 硬编码标签与 `<option>` |
| `tech_app/backend/services/industry_templates.py:18` | `INDUSTRIES` 三元组 |
| `tech_app/backend/storage/da_repo.py:55` | `INDUSTRY_KEYS` 四元组（含 `flexible`） |
| `tech_app/backend/storage/da_schema.sql:429` | `CHECK (industry IN ('semiconductor','battery','appliance','flexible'))` |
| `tech_app/backend/main.py:5807,6115,6133` | 三处各写一遍 `"semiconductor"` 兜底 |

在这样的结构上直接加“包装”，一定会出现：首页能选包装、进报价工作台丢失、转技术工艺
回退成半导体、数据库校验拒收 `packaging`、历史卡片与 Agent 上下文不知道所属行业。
所以第 1 批**先统一口径**，不碰包装的业务字段与算法。

补充事实（已实测）：

- `tech_app.backend.services.industry_templates.INDUSTRIES` = `('semiconductor','battery','appliance')`。
- `tech_app.backend.storage.da_repo._normalized_industry('packaging')` → `'semiconductor'`（**静默改行业**）。
- `cpq_wf._ddl_pg('cpq_wf')` 里 `cpq_wf_card` 没有任何 `industry` 列（报价侧完全没有行业概念）。
- `tests/` 目录下现在**没有任何一个测试**引用 `industry`；这块完全没有回归保护。

## 2. 用户角色与用户故事

- 销售经理：我在报价首页就能选“包装”，这个选择在会话、报价卡片、转技术工艺后都不丢。
- 工艺经理：技术工艺拿到的行业就是报价传过来的行业，不会被平台默认的“半导体”顶掉。
- 财务经理：成本任务带着行业，不会拿电池/半导体的费率去算包装。
- 平台管理员：行业清单只在一处维护，加一个行业不需要改 7 个文件。
- 历史用户：早期没有行业字段的报价卡片、需求单还能正常打开。

## 3. 当前流程 → 目标流程

当前：

```
报价首页(只有 tech 模式有 3 行业下拉, 各自硬编码)
   → tech-workbench?industry=  → requirement-create(硬编码 3 项) → 需求单 industry
   报价模式: 完全没有行业概念
```

目标：

```
cpq_industries.py  ← 唯一行业注册表（报价 + 技术工艺共同读取）
   ├─ 报价首页（报价模式与 tech 模式共用同一个下拉）
   ├─ 报价会话 / 报价卡片 cpq_wf_card.industry
   ├─ 报价 → 技术工艺交接（handoff payload 带 industry）
   ├─ 技术项目 / 需求单 src_requirement.industry
   └─ 技术任务 / 历史卡片 / Agent 上下文
```

## 4. 状态与数据契约

### 4.1 唯一注册表模块 `cpq_industries.py`（仓库根目录）

必须同时能被仓库根的 `cpq_*.py` 与 `tech_app/backend/**` 导入
（`tech_app` 已有把仓库根加入 `sys.path` 的既有做法，见
`tech_app/backend/services/llm_settings.py:40`）。

```python
INDUSTRY_KEYS: tuple[str, ...] = ("semiconductor", "battery", "appliance", "packaging")
LEGACY_INDUSTRY_KEYS: tuple[str, ...] = ("flexible",)
DEFAULT_INDUSTRY: str = "semiconductor"

INDUSTRIES: dict[str, dict] = {
  "semiconductor": {
    "key": "semiconductor", "label": "半导体", "enabled": True,
    "quote_template": "semiconductor", "process_template": "semiconductor",
    "knowledge_scope": "semiconductor",
    "cost_profile": "generic_v1", "pricing_profile": "generic_margin_v1",
  },
  "battery":       {... 同上，label "电池" ...},
  "appliance":     {... 同上，label "电器" ...},
  "packaging": {
    "key": "packaging", "label": "包装", "enabled": True,
    "quote_template": "packaging", "process_template": "packaging",
    "knowledge_scope": "packaging",
    "cost_profile": "packaging_v1",          # 第 7 批实现，本批只落路由标识
    "pricing_profile": "packaging_margin_v1",# 第 8 批实现，本批只落路由标识
  },
}
```

公开函数（名称固定，红测按此断言）：

- `industry_keys()` → `INDUSTRY_KEYS`（可选行业的唯一清单，顺序即展示顺序，四个）
- `all_industries()` → 按 `INDUSTRY_KEYS` 顺序返回 `INDUSTRIES[key]` 列表
- `is_supported(value)` → 是否可选行业（不含 `flexible`）
- `is_legacy(value)` → 是否是历史遗留键（`flexible`）
- `is_known(value)` → 可选 + 历史
- `normalize(value)` → 归一化：去空白/小写；可选或历史键原样返回；其它一律 `DEFAULT_INDUSTRY`
- `label_of(value)` → 中文标签（未知值给默认行业标签）
- `profile_of(value)` → 归一化后的 profile dict

**单一事实源硬约束**：`INDUSTRY_KEYS` 是唯一清单，其它模块必须**派生**，不得再写字面量。
红色用例 `test_b3` 会在子进程里先改 `cpq_industries.INDUSTRY_KEYS` 再导入消费方，
消费方如果自己硬编码，就看不到注入的探针行业。

### 4.2 技术工艺侧（`tech_app/backend/services/industry_templates.py`）

- `INDUSTRIES` / `DEFAULT_INDUSTRY` / `INDUSTRY_LABELS` 改为从注册表派生，四个行业。
- `SPECS` 增加 `packaging`：**本批只放最小占位块**（保证 `normalize` / `blocks` /
  `field_keys` / `section_checks` 对 `packaging` 不抛错），完整 3.1–3.6 字段由第 2 批补齐。
- `flexible` 继续作为历史键：`normalize('flexible')` 行为保持不变（落回 `SPECS` 缺省），
  旧草稿仍能打开。

### 4.3 存储

- `tech_app/backend/storage/da_schema.sql`：`src_requirement.industry` 的 `CHECK`
  必须接受 `packaging`；非法值仍然拒绝。
- `tech_app/backend/storage/da_repo.py`：`INDUSTRY_KEYS` 从注册表派生（可选 + 历史），
  `_normalized_industry('packaging')` 必须返回 `'packaging'`，未知值仍落默认。
- 历史数据兼容：`NULL` / `''` / 未知值 → 默认行业且**不抛错**；`'flexible'` 原样保留。

### 4.4 报价侧（`cpq_wf`）

- `cpq_wf_card` 增加 `industry varchar(32)`：
  - `_ddl_pg()` 的 `CREATE TABLE ... cpq_wf_card` 含该列；
  - 同时给老库一条 `ALTER TABLE ... cpq_wf_card ADD COLUMN IF NOT EXISTS industry varchar(32)`
    （幂等升级，沿用 `business_case_id` 的既有写法）。
- `cpq_wf.sync_card(..., industry: str = "")` 接受行业：
  - 建卡与更新都写入归一化后的值；
  - 未知行业 → 默认行业；
  - 不传 / 老卡片 → `NULL`，读回为 `""`（不猜、不回填）。
- `cpq_wf.get_card(session_id)` 的返回里带 `industry`。
- 报价 → 技术工艺交接（`cpq_tech_bridge` / handoff payload）必须携带行业，技术侧据此建需求单。

### 4.5 前端

- `报价首页.html`：行业下拉在**报价模式也显示**（不再是 tech 模式专属），
  `<option>` 与注册表可选集合一致（含“包装”）。
- `tech_app/frontend/cpq-industry.js`、`tech-task.js`、`requirement-create.js`、`home.js`
  的行业清单与注册表可选集合一致；不允许再出现硬编码的“旧三项”数组。

## 5. 正常路径

1. 用户在报价首页选择“包装” → 建报价会话 → `cpq_wf_card.industry = 'packaging'`。
2. 该报价转技术工艺 → handoff 携带 `packaging` → 需求单 `industry = 'packaging'`。
3. 技术工艺页面读到的行业是 `packaging`，不是 `semiconductor`。
4. 刷新 / 重开历史：行业仍在。

## 6. 异常路径

- 行业值缺失或非法：落默认行业并留痕，**不静默改成别的业务行业**。
- `industry_selection`（人工选的）与 AI 识别结果不一致时，**人工选择优先**（既有行为，不得回退）。
- 数据库写入非法行业：由 `CHECK` 拒绝，不写入。

## 7. 并发与幂等

- 建表/加列语句必须幂等（`IF NOT EXISTS`），重复执行不报错。
- `sync_card` 重复调用同一 `session_id` 不新建卡片，行业只按“非空才更新”写入。

## 8. 权限边界

本批不新增权限；沿用既有报价卡片与项目权限。

## 9. 历史数据兼容

- 老报价卡片无 `industry` → 读回 `""`，不报错、不自动填。
- 老需求单 `flexible` → 保留可读。
- 迁移只加列，不改写、不删除任何历史行。

## 10. 非目标（本批不做）

- 包装 3.1–3.6 字段模板与 AI 抽取（第 2 批）。
- 包装知识库表与 mock 数据（第 3 批）。
- 盒型匹配、参数化 BOM、工艺路线、成本引擎、利润/报价单（第 4–8 批）。
- 不实现 `packaging_v1` / `packaging_margin_v1` 的任何计算逻辑，只落路由标识。
- 不改现有三行业的默认行为与计算结果。

## 11. 可自动化验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_industry_registry_unified_red
```

覆盖：注册表契约（A）、消费方派生（B）、存储与兼容（C）、报价卡片携带行业（D）、
前端选项一致（E）。

## 12. 人工验收场景

1. 报价首页在报价模式能看到“行业模板”下拉，选择“包装”。
2. 新建一个报价会话，刷新后行业仍是包装。
3. 把该报价转技术工艺，需求单页面显示“包装”，不是“半导体”。
4. 打开一个历史（无行业）报价卡片，页面不报错。

## 13. 不允许减少的既有能力

- 半导体 / 电池 / 电器三行业的模板、默认值、成本算法与既有结果不变。
- `flexible` 历史草稿仍能打开。
- 报价首页 tech 模式的既有上传与跳转行为不变。
- `industry_selection` 优先于 AI 识别的既有语义不变。

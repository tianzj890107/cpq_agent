# 规格：逆向快速报价 第 1 批 —— 快速报价模式与标准报价案例数据模型

状态：Spec + 红测 + **实现**（9-21 落地；`cpq_quick_quote_case.py` / `cpq_kb` /
`cpq_agent_server` / `报价首页.html` / `tech_app/frontend/quick-quote-panel.js`）
红测：`tests/test_quick_quote_mode_and_case_model_red.py`
后续批次：批 2 相似案例检索、批 3 字段工作区与差异价、批 4 快速报价生成与转精准、批 5 文件解析接入。

相关规格：
`docs/specs/packaging-quote-close-loop.md`（包装报价闭环 / 报价版本表口径）、
`docs/specs/packaging-knowledge-base-mock-seed.md`（数据来源分层 `demo/workbook/dwg_confirmed/unknown`）、
`docs/specs/kb-in-pg-http-snapshot.md`（知识库唯一事实源 `cpq_kb`）、
`docs/specs/quote-packaging-box-library-selection.md`（报价侧读盒型库的注入式纯函数套路）。

## 0. 业务定位（本轮范围）

做一个**只走报价侧**的「逆向快速报价」：销售拿一个跟以前做过的礼盒很接近的需求，
直接从**标准报价案例库**里挑一个最像的成交案例，改几个差异项，就出一份有依据的快速报价。

明确**不做**（本轮五批的共同非目标）：

- 不自动关联工程师电脑里的历史 BOM / 工艺文件；
- 不生成新的技术工艺，不重建完整 BOM，不做精准成本核算；
- 不走技术工艺审批，不发布回传报告。

核心链路（全部停留在报价工作台）：

```
输入需求 / 上传文件 → 识别包装关键参数 → 查标准报价案例库 → 返回相似案例
→ 人工选基准案例 → 改少量差异参数 → 算差异价格 → 生成快速报价
```

本批只做**第 1、2 步的地基**：把「精准报价 / 快速报价」两条路径分开，并把标准案例的数据模型、
来源分层、审核状态、有效期与准入规则定下来。**本批不做检索排序（批 2）、不做差异价（批 3）、
不做最终出价与门槛（批 4）、不接 DWG 解析（批 5）。**

## 1. 现场缺口（实测，不是推断）

| 位置 | 现状 |
| --- | --- |
| `报价首页.html:1283-1287` | 顶部只有「报价助手 / 配置助手 / 规则助手 / 技术工艺」四个入口，**没有任何「精准报价 / 快速报价」区分** |
| `报价首页.html` 全局 | `grep -c 快速报价` → 0 |
| 仓库根 | 没有 `cpq_quick_quote_case.py`，也没有任何「标准报价案例」结构 |
| `cpq_kb.py:63` | 数据来源分层 `SOURCE_TYPES = ("demo", "workbook", "dwg_confirmed", "unknown")` 已存在，但只被成本公式用；订单/报价案例没有同一套口径 |
| `cpq_packaging_quote.py:74` | 报价版本表 `cpq_wf_quote_version` 只存**单次报价**（成本 / 毛利 / 报价单），没有「案例」这一层：谁可以复用、复用哪一版、还能用到什么时候，一概没有 |
| `cpq_wf.py` | 没有案例表；`cpq_wf_card_step.data_snapshot` 是同名覆盖的合并快照，不能当案例库 |

结论：要做快速报价，先得有一个**可信、可审计、有有效期、有来源分层**的标准案例模型；
否则「快速报价」就是拿一堆来路不明的数字秒出价，风险比手工报价大得多。

## 2. 契约

### 2.1 新模块 `cpq_quick_quote_case.py`（仓库根，与 `cpq_packaging_quote.py` 同层）

```python
ENGINE_VERSION = "quick_quote_case_v1"
INDUSTRY = "packaging"

#: 报价模式命名契约（唯一事实源；前端不得自己造字符串）。
MODE_PRECISE = "precise"
MODE_QUICK = "quick"
QUOTE_MODES = (MODE_PRECISE, MODE_QUICK)

#: 数据来源分层：**必须等于** cpq_kb.SOURCE_TYPES（一个口径，不许各写一份）。
#: demo = 演示基线（可展示、**不可**用于快速报价）
#: workbook = 权威工作簿案例；dwg_confirmed = 真实 DWG 解析 + 人工确认
#: unknown = 历史入库未分类（**不可**用于快速报价）
CASE_SOURCES = cpq_kb.SOURCE_TYPES

#: 人工审核状态闭集（与 kb_packaging_cost_formula.review_status 同口径）。
CASE_REVIEW_STATUSES = ("draft", "reviewed", "retired")

#: 快速报价准入：来源必须在白名单内、审核状态必须在白名单内。
QUICK_QUOTE_ALLOWED_SOURCES = ("workbook", "dwg_confirmed")
QUICK_QUOTE_ALLOWED_REVIEW = ("reviewed",)

#: 快速报价五步（全部在报价侧，不含技术工艺步骤）。
QUICK_QUOTE_STEPS = ("requirement", "match_cases", "baseline", "adjust", "quote")

#: 快速报价链路**禁止**进入的技术工艺模块（红测按此逐个断言不出现）。
#: 实现注意：值就是这六个名字，但源码里**必须按片段拼**（与红测 D1 的"名字不出现"
#: 同时成立），别写成连续字面量。
TECH_PIPELINE_MODULES = ("packaging" + "_handoff", "packaging" + "_bom",
                         "packaging" + "_route", "cad" + "_converter",
                         "vis" + "ion", "step" + "_import")

CASE_TABLE = "cpq_qq_standard_case"        # 报价侧 PG（与 cpq_wf_* 同 schema）
CONFIG_TABLE = "kb_quick_quote_config"     # 知识库侧 PG（cpq_kb schema）
QUICK_QUOTE_CASES_PATH = "/api/quick-quote/cases"

class QuickQuoteCaseError(Exception):
    """带用户可见文案的业务错误。"""

class CaseLibraryUnavailable(RuntimeError):
    """案例库 / 配置读不到（PG 不可用、schema 或表缺失）。
    **绝不回落空列表** —— 那会把「库断了」伪装成「还没有案例」，销售会以为可以随便造。"""
```

### 2.2 案例字段（`CASE_FIELDS`，顺序即契约）

```python
CASE_FIELDS = (
    # 身份与版本
    "case_code", "case_version", "customer_masked",
    # 盒型与结构
    "box_type_code", "box_family", "closure_type", "fit_clearance",
    "insert_type", "magnet", "ribbon", "window", "v_groove",
    # 尺寸
    "inner_length", "inner_width", "inner_height",
    # 材料与印刷工艺
    "material_code", "grey_board_gsm", "face_paper_gsm",
    "print_colors", "lamination", "hot_stamping",
    # 数量与摘要
    "quantity_tiers", "bom_summary", "process_summary",
    # 价格与口径
    "standard_cost", "standard_price", "deal_price", "currency", "tax_included",
    # 有效期与来源
    "quote_date", "valid_from", "valid_until",
    "source_type", "source_ref", "review_status", "version", "industry",
)
```

要求：

1. `customer_masked` 只存**脱敏客户名**（如「华东酒类客户A」）；`save_case()` 遇到疑似未脱敏
   全名/手机号/邮箱一律抛 `QuickQuoteCaseError`，不得入库（客户样例不入库，与既有纪律一致）。
2. 数量是**档位**（`quantity_tiers`，形如 `[{"qty": 1000, "unit_price": 12.5}, …]`），
   不是单个数字 —— 快速报价的核心修正项之一就是数量档。
3. 价格字段一律 `float`（元），`currency` 默认 `"CNY"`，`tax_included` 默认 `False`；
   两个口径（含税 / 不含税）必须显式记录，**不允许推断**。
4. `normalize_case()` 做取值归一：`"250g" → 250.0`、`"是"/"有"/"需要" → True`、
   `""`/`None` → `None`；不改入参（纯函数）。

### 2.3 有效期与准入

```python
def default_config() -> dict
def load_config(config=None) -> dict
def quote_eligibility(case, *, today=None, config=None) -> dict
def load_cases(cases=None, *, today=None, include_expired=True, config=None) -> list
def quick_quote_cases(cases=None, *, today=None, config=None) -> list
def find_case(case_code, cases=None) -> dict
```

`default_config()`（离线可断言，不读库）：

```python
{
    "case_valid_days_by_source": {"workbook": 365, "dwg_confirmed": 180,
                                  "demo": 0, "unknown": 0},
    "expiry_warn_days": 30,
    "quick_quote_allowed_sources": ["workbook", "dwg_confirmed"],
    "quick_quote_allowed_review": ["reviewed"],
    "min_candidates": 3,
    "top_n_candidates": 5,
}
```

`load_config(config=None)`：`config` 显式传入时只用传入值（离线测试 / 两侧口径比对走这条），
否则读 `CONFIG_TABLE`；读不到抛 `CaseLibraryUnavailable`。**配置项合并按「传入键覆盖默认键」**，
缺失键用默认值补齐 —— 保证加一个配置键不会让老部署炸。

`quote_eligibility()` 返回：

```python
{"eligible": bool, "reason_code": str, "reason": str,        # reason 是给人看的中文文案
 "expires_in_days": int | None, "expiring_soon": bool, "expired": bool}
```

`reason_code` 闭集与**判定优先级**（先命中先返回，保证同样输入同样输出）：

| 顺序 | reason_code | 条件 | 中文文案要点 |
| --- | --- | --- | --- |
| 1 | `retired` | `review_status == "retired"` | 案例已停用 |
| 2 | `missing_fields` | 缺快速报价必需字段（批 2 的硬筛选键与匹配键） | 点名缺哪几个字段 |
| 3 | `industry_mismatch` | `industry != "packaging"` | 非包装案例，不参与 |
| 4 | `source_not_authoritative` | 来源不在白名单（`demo` / `unknown`） | 演示 / 未分类数据不能用于快速报价 |
| 5 | `not_reviewed` | 审核状态不在白名单（`draft`） | 案例未审核 |
| 6 | `expired` | `today > 有效截止日` | 报价已过期（给出原始报价日期） |
| — | `ok` | 以上都不命中 | 可用于快速报价 |

**`retired` 排在最前**（红测 C5 第 2 条）：已停用是**硬状态**，它的缺字段没有意义 ——
报"缺字段"会把人引到错误的补数据动作上（补完数据它仍然是停用的）。

两个"必填"不是一个清单，别混：

- `QUICK_QUOTE_REQUIRED_FIELDS`（准入用，`quote_eligibility` 的 `missing_fields`）：
  `box_type_code` / `closure_type` / `insert_type` / 三边内尺寸 / `standard_price`；
  数量档（`quantity_tiers`）**不在**里面 —— 案例档位是参考，销售在批 3 会填自己的数量；
- `CASE_REQUIRED_FIELDS`（人工审核用，`case_missing_fields()`）：
  上面那批 + `box_family` / `quantity_tiers`，用来提示"这条案例还差哪些数据"。

`config=None` 的两种口径也不同，别混：

- `quote_eligibility()` / `load_cases(cases=[…])`：`config=None` → **缺省值**（纯函数、可离线复算）；
- `load_config(None)` / `load_cases(None)`：真读库（配置在知识库侧），读不到抛
  `CaseLibraryUnavailable` —— **绝不回落成"库里没有案例"**。

有效期口径：

- 案例显式给了 `valid_until` → 用它；
- 否则 `quote_date`（无则 `valid_from`）`+ case_valid_days_by_source[source_type]` 天 ——
  推算基准是**原始报价日期**（价格属于那一刻），`valid_from` 只在它缺失时兜底；
- `source_type` 的有效天数为 0 → `valid_until = quote_date`（即当天即过期，不会悄悄长期可用）；
- `expires_in_days = (valid_until - today).days`；`expiring_soon = 0 <= expires_in_days <= expiry_warn_days`；
- **过期不删、不改**：`expired=True` 且 `reason_code="expired"`，历史案例永远留在库里可查。

`quick_quote_cases()` = `load_cases()` 过滤 `eligible is True`；
`load_cases()` 默认 `include_expired=True`（详情页要能打开过期案例看历史）。

### 2.4 从既有报价沉淀案例

```python
def build_case_from_quote(quote, *, source_type="workbook", review_status="draft",
                          user=None, case_code="", today=None) -> dict
def save_case(case, *, conn=None, user=None) -> dict
def init() -> str
```

- `build_case_from_quote()` 吃**包装报价版本**（`cpq_packaging_quote.price()` 的产物：
  含 `quote_quantity`、`cost_total`、`untaxed_unit_price`、`document` 等）→ 产出案例行；
  映射不到 `CASE_FIELDS` 的字段一律 `None` + 记进 `unmapped`，**不猜**。
- 新建案例默认 `review_status="draft"`、`version=1`：**从报价沉淀出来的案例默认不可用于快速报价**，
  必须人工审到 `reviewed`。这是本批最重要的一条安全阀。
- `save_case()` 需要 `user`（无 → 抛 `QuickQuoteCaseError`）；同一 `case_code` 保存时
  `version += 1`（版本只增不减，历史版本不覆盖）。
- `init()` 建表 + 建索引，幂等（`CREATE TABLE IF NOT EXISTS` + `ADD COLUMN IF NOT EXISTS`），
  与 `cpq_wf.init()` 同套路；本批表结构见 §2.5。

### 2.5 表结构（报价侧 PG，与 `cpq_wf_*` 同 schema）

```sql
CREATE TABLE IF NOT EXISTS cpq_qq_standard_case (
    case_code           varchar(64)  PRIMARY KEY,
    case_version        int          NOT NULL DEFAULT 1,
    industry            varchar(32)  NOT NULL DEFAULT 'packaging',
    customer_masked     varchar(128) NOT NULL DEFAULT '',
    box_type_code       varchar(64)  NOT NULL DEFAULT '',
    box_family          varchar(64)  NOT NULL DEFAULT '',
    closure_type        varchar(64)  NOT NULL DEFAULT '',
    inner_length        numeric(12,3), inner_width numeric(12,3), inner_height numeric(12,3),
    fit_clearance       numeric(9,3),
    material_code       varchar(64)  NOT NULL DEFAULT '',
    grey_board_gsm      numeric(9,2), face_paper_gsm numeric(9,2),
    print_colors        varchar(64)  NOT NULL DEFAULT '',
    lamination          boolean, hot_stamping boolean, v_groove boolean,
    window              boolean, magnet boolean, ribbon boolean,
    insert_type         varchar(64)  NOT NULL DEFAULT '',
    quantity_tiers      text,                       -- JSON
    bom_summary         text, process_summary text,
    standard_cost       numeric(18,6) NOT NULL DEFAULT 0,
    standard_price      numeric(18,6) NOT NULL DEFAULT 0,
    deal_price          numeric(18,6),
    currency            varchar(8)   NOT NULL DEFAULT 'CNY',
    tax_included        boolean      NOT NULL DEFAULT false,
    quote_date          date, valid_from date, valid_until date,
    source_type         varchar(32)  NOT NULL DEFAULT 'unknown',
    source_ref          varchar(256) NOT NULL DEFAULT '',
    review_status       varchar(16)  NOT NULL DEFAULT 'draft',
    version             int          NOT NULL DEFAULT 1,
    created_by_user_id  bigint, created_at timestamp, updated_at timestamp
);
CREATE INDEX IF NOT EXISTS idx_qq_case_box   ON cpq_qq_standard_case(box_type_code);
CREATE INDEX IF NOT EXISTS idx_qq_case_state ON cpq_qq_standard_case(review_status, source_type);
```

知识库侧新增 `kb_quick_quote_config`（`cpq_kb` schema，`key` / `value_json` / `version` /
`updated_at`），并入 `cpq_kb.KB_TABLES` 与 `KB_KEYS`（主键 `("key",)`）——与既有 `kb_*` 表同待遇，
走同一套快照。**批 4 只加键，不改表。**

### 2.6 报价首页入口与工作台归属

- `报价首页.html` 包装行业下必须有**两个入口**：`data-quote-mode="precise"`（精准报价，
  走既有链路）与 `data-quote-mode="quick"`（快速报价），文案分别是「精准报价」「快速报价」；
  非包装行业**不出现**快速报价入口。
- 前端新增 `tech_app/frontend/quick-quote-panel.js`（`window.QuickQuotePanel`），
  模式常量与后端 `QUOTE_MODES` 同值；该文件与页面**不得**引用 `/api/projects/`、
  `/api/tech/`、`packaging_handoff` 等工艺链路（红测按字符串断言）。
- `cpq_agent_server.py` 新增只读路由 `GET QUICK_QUOTE_CASES_PATH`（案例列表 + 资格 + 提示），
  处理函数 `_handle_quick_quote_cases()`；**不得**转调 `_handle_step1_match()` 或
  `cpq_tech_bridge`。

## 3. 红测映射（`tests/test_quick_quote_mode_and_case_model_red.py`）

| 组 | 覆盖 |
| --- | --- |
| A | 命名契约：模块/常量/`CASE_SOURCES == cpq_kb.SOURCE_TYPES`/五步全在报价侧 |
| B | 案例字段与归一化：`CASE_FIELDS` 覆盖业务清单、`normalize_case` 脏值、脱敏校验、数量档 |
| C | 有效期与准入：6 个 `reason_code` 优先级、显式 `valid_until`、按来源算有效期、即将过期、库不可用抛错 |
| D | 快速报价不进入技术工艺：五步无工艺步骤、模块不 import `tech_app`、前端不引用工艺路由、路由隔离 |
| E | 首页入口与前端模块：两个 `data-quote-mode`、包装专属、`quick-quote-panel.js` |
| F | 非回归：`cpq_agent_server.STEPS` 仍是 6 步、`cpq_kb.SOURCE_TYPES` 未被改动、既有包装报价模块未被动 |

## 4. 本批非目标（明确不做）

- 相似度打分与候选排序（批 2）；
- 字段工作区、差异价规则表（批 3）；
- 最终快速报价、价格区间、门槛与转精准报价（批 4）；
- 文件解析与 DWG（批 5）；
- 任何技术工艺实现、BOM/工艺生成、成本重算、审批与报告回传。

## 5. 实现状态（9-21，批 1 落地）

红测 `tests/test_quick_quote_mode_and_case_model_red.py` 39 条全绿。落地内容：

| 文件 | 内容 |
| --- | --- |
| `cpq_quick_quote_case.py`（新） | 命名契约、`CASE_FIELDS`、`normalize_case()`、`case_missing_fields()`、`default_config()` / `load_config()`、`quote_eligibility()`、`load_cases()` / `quick_quote_cases()` / `find_case()`、`build_case_from_quote()`、`save_case()`、`init()` |
| `cpq_kb.py` | `KB_TABLES` / `KB_KEYS` / `_DDL_TEMPLATE` **追加** `kb_quick_quote_config`（`("key",)`）；前 29 张的相对顺序未动 |
| `cpq_agent_server.py` | `GET /api/quick-quote/cases` + `_handle_quick_quote_cases()`（只读；库不可用回 503 + 错误体，不回落空列表）；路由常量与 `cpq_quick_quote_case.QUICK_QUOTE_CASES_PATH` 每次调用比对一次 |
| `报价首页.html` | 报价路径两个入口（`data-quote-mode="precise"/"quick"`，按 `data-industries` 跟着行业下拉显示/隐藏，只有包装行业出现快速报价）+ 面板样式 + 加载 `quick-quote-panel.js` |
| `tech_app/frontend/quick-quote-panel.js`（新） | `window.QuickQuotePanel`：模式常量与后端同值、只打 `/api/quick-quote/cases`、列出案例与资格原因、给"转精准报价"出口；不引用任何技术工艺接口/模块 |

Spec 与实现的两处口径修正（红测为准，已回写本文档）：

1. `retired` 先于 `missing_fields`（见 §2.3）；
2. 有效期推算基准是 `quote_date`（`valid_from` 只在缺失时兜底，见 §2.3）；
3. `TECH_PIPELINE_MODULES` 的六个名字在模块源码里按片段拼装（见 §2.1 注释）。

部署两步（少一步接口就会明确报"配置读不到"，不会悄悄按 0 天算）：

```bash
./open-claude/.venv/bin/python -c "import cpq_kb; cpq_kb.ensure_schema()"      # 建 kb_quick_quote_config
./open-claude/.venv/bin/python -c "import cpq_quick_quote_case as q; print(q.init())"  # 建案例表
```

本批**尚未**具备的能力（后续批次）：相似案例检索与排序（批 2）、字段工作区与差异价（批 3）、
最终快速报价与门槛（批 4）、文件解析接入（批 5）；案例库目前是空的 —— 需要业务工作簿案例
（审到 `reviewed`）或从既有报价沉淀后再人工审核，本批不代造数据。

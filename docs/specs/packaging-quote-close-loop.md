# 规格：包装报价闭环（回传 / 定价 / 报价单 / 版本）—— 包装第 8 批

> 批次：包装 8 批计划的**最后一批**。依赖第 1 批（四行业注册表）、第 2 批（包装需求模板）、
> 第 3 批（包装知识库）、第 4 批（盒型匹配）、第 5 批（参数化 BOM）、第 6 批（工艺路线）、
> 第 7 批（包装专用成本引擎）已完成。
> 红测：`tests/test_packaging_quote_close_loop_red.py`。
> 口径来源：`报价逻辑-0903.xlsx` 的 `报价表` / `成本细分` / `报价-工费率` / `问题点`，以及
> 仓内既有回传链路（`cpq_tech_bridge.py`、`cpq_wf.py`、`cpq_bridge.py`、`cost_flow.py`）。

本批把「包装成本」变成「对客户的报价」：定价、加价、折扣、税金、报价单、报价版本，以及把
技术侧结果**完整**回传到报价卡片。**不改**三个原行业的成本与定价链路。

## 1. 背景与真实问题

### 1.1 成本已经能算，但报价侧接不住

第 7 批出了 `tech_app/backend/services/packaging_cost.py`（`packaging_v1`：24 个成本类别、
三层汇总、缺口闭集），但它按 Spec §5 明确**不出**售价 / 毛利率 / 报价单。

报价卡片第 3 步「定价-利润加成」今天是模型驱动的：`cpq_agent_server._handle_markup_fill()`
从报价库 `md_clm_material_price_rule` 取 `rule_classification='定价'/'报价'` 的规则，交给大模型
逐条判断命中并算金额。包装套不进这条路：包装的定价口径是**确定的除式**（§1.2），不该由模型
决定；而且包装这单在报价库里没有对应的成品编码与规则行（`报价表!B2 = '报价-行业标准'!#REF!`
就是同一件事的现场）。

### 1.2 0903 的定价是一个除式，不是「成本 + 利润率」

| Sheet | 成本 | 毛利率 | 售价 | 单元格公式 | 缓存值 |
| --- | --- | --- | --- | --- | --- |
| `报价-工费率` | `AV2` | `AW2` | `AX2` 未税单价 | `=AV2/(1-AW2)` | 77.68520189964802 / 0.25 → **103.58026919953069** |
| `报价表` | `G2` | `H2` | `I2` 未税售价/RMB | `=G2/(1-H2)` | 60.04940812974125 / 0.25 → **80.06587750632167** |
| `成本细分` | `P2` | `Q2` | `R2` 未税售价 | `=P2/(1-Q2)` | 60.04940812974125 / 0.25 → **80.06587750632167** |

同一个工作簿的 `问题点` 页 A30 却写着：

> 成本测算完之后，会直接在总成本上+利润率报给客户

**文字说「成本 × (1+利润率)」，公式写的是「成本 ÷ (1-毛利率)」。** 同样 25%：

- 公式口径：`60.049408129741252 ÷ 0.75 = 80.06587750632167`
- 文字口径：`60.049408129741252 × 1.25 = 75.06176016217657`
- 差 `5.004117344145101`（6.7%）

处理方式与第 7 批的损耗歧义一致：

- **第一版忠实复现工作簿公式**：默认 `pricing_mode='gross_margin'` → `成本 ÷ (1 - 毛利率)`；
- 文字口径保留为**可配置**的 `pricing_mode='markup'` → `成本 × (1 + 加成率)`，**不删、不合并**；
- 两种模式在库与回传里**字段名不同**（`gross_margin_rate` / `markup_rate`），记录里必须存
  `pricing_mode` —— 否则「这单到底怎么算的」永远说不清。

### 1.3 现状缺口（实测）

- `cpq_packaging_quote.py` 不存在：全仓没有任何「包装定价 / 报价单 / 报价版本」实现；
- 报价没有版本表：`cpq_wf_card_step.data_snapshot` 是**同名覆盖**的合并快照
  （`cpq_wf.merge_step_snapshot`），重算会盖掉上一版 —— 0903 `报价表 (2)` 里的
  「原报价成本 60.0494 vs 新成本 77.6852」正是必须同时存在的两版；
- `cpq_tech_bridge.HANDOFF_KINDS`（`:618`）只有四个成本/报告口径，没有包装口径；回传 payload
  只带 `tech_result`/`report`，`_step2_snapshot(result)` 只认 `material`/`params`/`cost.total`
  与固定模板列 —— 包装的盒型、参数、BOM、路线、缺口、公式依据**装不进快照就会整段丢**，
  报价侧只剩一句 note（"只有任务卡没有业务数据"）；
- `cpq_bridge.send_to_quote()` 把 `handoff_kind` 写死成 `"cost_to_quote"`，技术侧发不出包装口径；
- `tech_app` 侧没有「包装 → 报价」交接实现：`cost_flow.integration_quote_result()` 是设计 IR
  口径（成品编码 / 四项成本 / 参数行），包装没有成品编码；
- 第 7 批的 `wip_packaging_cost_estimate.gross_margin_rate` **只存不算**，也没有消费方。

## 2. 数据契约

### 2.1 两个新模块与唯一入口

| 侧 | 模块 | 职责 |
| --- | --- | --- |
| 技术工艺 | `tech_app/backend/services/packaging_handoff.py` | 组装交接包（10 组）、落库、经 `cpq_bridge` 回传报价 |
| 报价 | `cpq_packaging_quote.py`（仓库根） | 定价、加价/折扣/税金、报价单、报价版本、历史恢复 |

- 定价**只有一个入口** `cpq_packaging_quote.price()`：路由、Agent、HTTP 处理器都调它，
  不许有第二份算法（这是「报价计算可复算」的前提）。
- 回传**只有一个入口** `packaging_handoff.send_to_quote()`：看板按钮与 Agent 工具共用。
- 报价侧不得 import `tech_app`；技术侧不得 import `cpq_agent_server`（既有分层，见
  `cost_flow.py` 顶部注释）。

### 2.2 交接包（`PACKAGE_SECTIONS`，10 组，顺序即下表顺序）

| key | 内容 | 来源（不得另算） |
| --- | --- | --- |
| `industry` | 字符串 `"packaging"`（同级的 `industry_label` / `cost_profile` / `pricing_profile` 由注册表派生） | `cpq_industries.profile_of("packaging")` |
| `requirement` | 需求单原文 3.1–3.6 全字段 | `store.load_requirement` |
| `box_type` | 已确认盒型（编码/名称/族/尺寸范围/闭合方式/V 槽/MOQ） | 第 4 批 `packaging_match.load_box_match` |
| `params` | L/W/H、t、c、克重、工艺参数（即展开输入） | 第 2 批需求字段 |
| `bom` | 部件与材料清单（含类别、数量、材料、公式版本） | 第 5 批 `packaging_bom.load_bom` |
| `route` | 工艺路线（工序、设备、标准工时、确认状态、指纹） | 第 6 批 `packaging_route.load_route` |
| `cost` | 第 7 批成本全貌：`total_cost`/`subtotal`/`loss_amount`/六个分项/`categories`/`report_groups` | 第 7 批 `packaging_cost.load_cost` |
| `gaps` | 成本缺口逐条（`code`/`where`/`detail`） | 第 7 批 `cost.gaps` |
| `formulas` | 公式依据逐条（`formula_code`/`version`/`expression`/`inputs`/`result`/`source_ref`/`source`） | 第 7 批成本明细行 |
| `source` | `project_id`/`requirement_no`/`scenario_code`/`source_task_id`/`source_session_id`/`business_case_id`/`result_version` | 项目 meta 与需求单 |

顶层形状（`industry` 是**字符串**，不是 dict —— 桥接层要能直接用 `result["industry"]` 判断）：

```json
{
  "engine_version": "packaging_handoff_v1",
  "handoff_version": "pkg-quote-handoff-v1",
  "handoff_kind": "packaging_cost_to_quote",
  "industry": "packaging",
  "industry_label": "包装",
  "cost_profile": "packaging_v1",
  "pricing_profile": "packaging_margin_v1",
  "result_version": "<第 7 批成本结果版本>",
  "requirement": {}, "box_type": {}, "params": {}, "bom": {}, "route": {},
  "cost": {}, "gaps": [], "formulas": [], "source": {}
}
```

`source` 段：`project_id` / `requirement_no` / `scenario_code` / `result_version` /
`source_task_id` / `source_session_id` / `business_case_id`；`handoff_id` 在回传成功后由
交接记录回填（技术侧落库后才有，预览时为 `""`）。

缺口（`gaps`）与公式依据（`formulas`）的形状**沿用第 7 批**：`gaps` 每条
`{"code", "where", "detail"}`；`formulas` 每条
`{"formula_code", "formula_version", "expression", "inputs", "result", "source", "source_ref"}`。

约束：

- **交接包不带售价**：`cost` 段内不得出现 `unit_price` / `untaxed_price` / `total_price` /
  `quote_amount` / `margin_rate`（第 7 批同一条禁令的延续：定价发生在报价侧）；
- `gross_margin_rate` 允许作为**需求输入**出现在 `requirement` 段（第 7 批"只存不算"的口径），
  但不得在交接包里被算成金额；
- `package_fingerprint(package)` = 对 10 组内容做的稳定摘要（键排序、浮点按 `repr`），
  用于幂等与版本判定；同输入同摘要、改一个数量即变。

### 2.3 缺口与放行

- 成本存在任何缺口（`cost.has_gaps`）时：`handoff_package()` 照常返回（人要看得见缺什么），
  但 `send_to_quote()` **拒绝发送**：`HandoffError(409, "cost_gaps_unresolved")`，错误里带
  `gaps`；
- 只有显式 `allow_gaps=True` **且** `reason` 非空时才放行，并把
  `gap_waiver = {"by": <username>, "at": <ISO 时间>, "reason": <原文>, "codes": [缺口码…]}`
  写进交接记录；报价侧拿到 `has_gaps=True` 的包时**只能出成本与草稿，不得出正式报价单**
  （`price()` 对 `has_gaps=True` 的包 → `PricingError(409, "cost_gaps_unresolved")`）。

### 2.4 定价口径

计算顺序（每一步都进 `lines`，含公式、输入、结果、来源、版本）：

```
1  cost_total             = 第 7 批 total_cost
2  margin_price           = cost_total ÷ (1 - gross_margin_rate)          # gross_margin 模式
   margin_price           = cost_total × (1 + markup_rate)                # markup 模式
3  addon_total            = Σ addons（按单件金额）
4  subtotal_unit          = margin_price + addon_total
5  discount_amount        = subtotal_unit × discount_rate                 # 比例，0–1
6  net_unit_price         = subtotal_unit - discount_amount
7  tax_amount             = net_unit_price × tax_rate
8  taxed_unit_price       = net_unit_price + tax_amount
9  untaxed_total / taxed_total = 单价 × quote_quantity（未税总额以 net_unit_price 为准）
```

- `ADDON_CATEGORIES = (("tech_premium","技术溢价"), ("market_adjustment","市场调节"),
  ("other_addon","其他加价"))`，`DEDUCTION_CATEGORIES = (("discount","折扣"),)`：
  **闭集**，出现闭集外的 key → `PricingError(400, "unknown_addon")`，不许静默忽略；
- `DEFAULT_TAX_RATE = 0.13`（0903 `报价-行业标准` 里的 1.13 税率口径）；
- `quote_quantity` 缺省取 `package.requirement.quote_quantity`；缺失或 ≤ 0 →
  `PricingError(400, "invalid_quantity")`；
- 守卫：`pricing_mode` 不在 `("gross_margin","markup")` → `invalid_pricing_mode`；
  该模式的费率缺失 → `rate_missing`；费率 `< 0` → `invalid_rate`；`gross_margin` 模式下
  费率 `≥ 1` → `invalid_rate`（除零）；`tax_rate` 不在 `[0,1]` → `invalid_tax_rate`；
- 非 packaging 的包 → `PricingError(400, "not_packaging")`；
- `price()` 是**纯函数**：不改入参、不读写库、不调模型、不联网；`recompute(quote)` 用
  `lines` 里的输入重跑一遍，结果必须逐项相等（这是"报价计算可复算"）。

### 2.5 报价单

`document(quote)` 返回 `{"title", "markdown", "sections"}`，`DOC_SECTIONS` 八节：
`报价基本信息 / 产品与盒型 / 部件与材料 / 工艺路线 / 成本构成 / 定价与加价 / 税金与总额 /
来源与可追溯`。

- 每节至少有一条 `来源` 或 `公式` 说明（"来源与可追溯"节必须写出 `source_tech_project_id`、
  `source_handoff_id`、成本 `engine_version`、`cost_profile`、定价 `pricing_profile`）；
- 报价单里的数字必须与 `quote` 里的字段**逐个一致**（不许另算一遍）；
- 缺口未清时不得生成正式报价单（见 §2.3）。

### 2.6 报价版本不变式

- 报价版本表**只增不改**：只有 INSERT 与 SELECT，没有 UPDATE / DELETE 路径；
- `version_no` 在同一个 `(business_case_id 或 quote_session_id)` 范围内自增（从 1 开始）；
- 同 `quote_fingerprint` 重复保存 → 复用已有版本并返回 `already_saved=True`，**不新建**；
- 换了成本（技术工艺重算后重传）→ 新建版本，并记 `previous_version_no` /
  `previous_cost_total`（0903 `报价表 (2)` 的「原报价成本」列就是这件事）；
- 旧版本在新版本产生后仍然可读：`versions()` 返回全部，`latest()` 返回最大 `version_no`；
- **任何路径都不得**用新版本覆盖旧版本的 `document_md` / `inputs_json` / 金额。

### 2.7 角色

| 动作 | 允许的角色 | 越权结果 |
| --- | --- | --- |
| 技术侧回传（`packaging_handoff.send_to_quote`） | `HANDOFF_WRITE_ROLES = {finance_manager, process_manager, process_director, admin}` | `HandoffError(403, "role_not_allowed")` |
| 报价侧定价落版本（`cpq_packaging_quote.save_version`） | `WRITE_ROLES = {sales_mgr, admin}` | `PricingError(403, "role_not_allowed")` |
| 读回传/读版本/读报价单 | 任何已登录用户 | —— |

- 两个角色集合都是**模块级唯一常量**，路由与 Agent 工具直接引用，不另抄一份；
- 定价是销售的动作，技术侧只读；回传是财务/工艺的动作，报价侧只读 —— 两边互不越界。

## 3. 落库

### 3.1 技术侧：`wip_packaging_handoff`（`da_schema.sql`，只追加）

```sql
CREATE TABLE IF NOT EXISTS wip_packaging_handoff (
    handoff_no          TEXT PRIMARY KEY,      -- pkghandoff:<project>:<requirement>:<scenario>:<version_no>
    project_id          TEXT NOT NULL,
    requirement_no      TEXT NOT NULL DEFAULT '',
    scenario_code       TEXT NOT NULL DEFAULT 'default',
    version_no          INTEGER NOT NULL DEFAULT 1,
    industry            TEXT NOT NULL DEFAULT 'packaging',
    engine_version      TEXT NOT NULL,
    handoff_version     TEXT NOT NULL,
    handoff_kind        TEXT NOT NULL,          -- packaging_cost_to_quote
    cost_profile        TEXT NOT NULL,          -- packaging_v1
    pricing_profile     TEXT NOT NULL,          -- packaging_margin_v1
    cost_result_version TEXT,
    package_fingerprint TEXT NOT NULL,
    package_json        TEXT,
    has_gaps            INTEGER NOT NULL DEFAULT 0 CHECK (has_gaps IN (0, 1)),
    gap_codes_json      TEXT,
    gap_waiver_json     TEXT,
    target_quote_session_id TEXT NOT NULL DEFAULT '',
    target_task_id      TEXT NOT NULL DEFAULT '',
    target_business_case_id TEXT NOT NULL DEFAULT '',
    sent_by             TEXT NOT NULL DEFAULT '',
    sent_at             TEXT,
    created_at          TEXT,
    UNIQUE (project_id, requirement_no, scenario_code, package_fingerprint)
);
```

- `UNIQUE(...)` 是幂等的**裁判**：同一个包重复发送 → 命中唯一约束 → 复用已有行，
  返回 `already_sent=True`，**不新增行、不再建报价任务**；
- 成本重算导致 `package_fingerprint` 变了 → 新行 `version_no + 1`（旧行不动）；
- `da_repo` 新增：`save_packaging_handoff(record)`、`load_packaging_handoff(project_id,
  requirement_no="", fingerprint="")`、`packaging_handoffs(project_id, requirement_no="")`。

### 3.2 报价侧：`cpq_wf_quote_version`（`cpq_wf._ddl_pg`，只追加）

```sql
CREATE TABLE IF NOT EXISTS cpq_wf_quote_version (
    quote_version_id     bigint       PRIMARY KEY,
    business_case_id     varchar(64)  NOT NULL DEFAULT '',
    quote_session_id     varchar(64)  NOT NULL DEFAULT '',
    card_id              bigint,
    requirement_no       varchar(64)  NOT NULL DEFAULT '',
    scenario_code        varchar(64)  NOT NULL DEFAULT 'default',
    industry             varchar(32)  NOT NULL DEFAULT 'packaging',
    engine_version       varchar(32)  NOT NULL,
    pricing_profile      varchar(32)  NOT NULL,
    version_no           int          NOT NULL DEFAULT 1,
    quote_fingerprint    varchar(64)  NOT NULL,
    pricing_mode         varchar(16)  NOT NULL DEFAULT 'gross_margin',
    cost_total           numeric(18,6) NOT NULL DEFAULT 0,
    previous_cost_total  numeric(18,6),
    previous_version_no  int,
    quote_quantity       numeric(18,3),
    gross_margin_rate    numeric(9,6),
    markup_rate          numeric(9,6),
    tax_rate             numeric(9,6),
    untaxed_unit_price   numeric(18,6) NOT NULL DEFAULT 0,
    untaxed_total        numeric(18,6) NOT NULL DEFAULT 0,
    addon_total          numeric(18,6) NOT NULL DEFAULT 0,
    discount_amount      numeric(18,6) NOT NULL DEFAULT 0,
    tax_amount           numeric(18,6) NOT NULL DEFAULT 0,
    taxed_total          numeric(18,6) NOT NULL DEFAULT 0,
    addons_json          text,
    discount_json        text,
    document_md          text,
    inputs_json          text,
    source_tech_project_id varchar(64) NOT NULL DEFAULT '',
    source_handoff_id    varchar(64) NOT NULL DEFAULT '',
    created_by_user_id   bigint,
    created_at           timestamp,
    UNIQUE (quote_session_id, quote_fingerprint)
);
```

- `UNIQUE(quote_session_id, quote_fingerprint)` 是"同一次定价重复点击不新建版本"的裁判；
- 表内**没有** `updated_at`：只增不改，改就是新版本。

### 3.3 本批不改动的表

`wip_cost_estimate` / `wip_cost_item` / `out_cost_result` / `wip_packaging_cost_estimate` /
`wip_packaging_cost_item` / 以及四行业的既有表：**一行都不改**（第 7 批与三行业红测逐条断言）。

## 4. 接口与命名契约

### 4.1 `tech_app/backend/services/packaging_handoff.py`（新）

| 名称 | 说明 |
| --- | --- |
| `ENGINE_VERSION = "packaging_handoff_v1"` | 交接层版本 |
| `HANDOFF_VERSION = "pkg-quote-handoff-v1"` | 回传语义版本 |
| `HANDOFF_KIND = "packaging_cost_to_quote"` | 交接类型 |
| `INDUSTRY = "packaging"` / `COST_PROFILE = "packaging_v1"` / `PRICING_PROFILE = "packaging_margin_v1"` | 与第 1 批注册表一致 |
| `PACKAGE_SECTIONS` | §2.2 的 10 组（`tuple[str, ...]`，顺序即表序） |
| `HANDOFF_WRITE_ROLES` | §2.7 |
| `HandoffError(message, status_code=409, code="")` | 业务错误 |
| `handoff_package(project_id, requirement_no="", *, scenario=None) -> dict` | 组装交接包（不落库、不发送） |
| `package_fingerprint(package) -> str` | 稳定摘要 |
| `bridge_result(package) -> dict` | 交给 `cpq_bridge.send_to_quote(result=…)` 的正文（含 `industry` 与 `packaging_package`） |
| `send_to_quote(project_id, requirement_no="", *, scenario=None, allow_gaps=False, reason="", user=None, token="", title="", customer="") -> dict` | 落库 + 回传；返回 `handoff_no` / `version_no` / `already_sent` / `quote_session_id` / `handoff`（目标任务与落点） |
| `load_handoff(project_id, requirement_no="") -> dict` | 最近一次交接 |
| `handoff_versions(project_id, requirement_no="") -> list` | 全部交接版本（只增） |

### 4.2 `cpq_packaging_quote.py`（新，仓库根）

| 名称 | 说明 |
| --- | --- |
| `ENGINE_VERSION = "packaging_quote_v1"` | 定价引擎版本 |
| `PRICING_PROFILE = "packaging_margin_v1"` / `COST_PROFILE = "packaging_v1"` / `INDUSTRY = "packaging"` | 与注册表一致 |
| `PRICING_MODES = ("gross_margin", "markup")` / `DEFAULT_PRICING_MODE = "gross_margin"` | §1.2 |
| `RATE_FIELDS = {"gross_margin": "gross_margin_rate", "markup": "markup_rate"}` | 两个概念不许混用 |
| `ADDON_CATEGORIES` / `DEDUCTION_CATEGORIES` | §2.4 闭集 |
| `DEFAULT_TAX_RATE = 0.13` | 税金口径 |
| `WRITE_ROLES` | §2.7 |
| `DOC_SECTIONS` | §2.5 的八节 |
| `PRICE_PATH = "/api/packaging-quote/price"` | 报价侧定价入口 |
| `PricingError(message, status_code=400, code="")` | 业务错误 |
| `untaxed_unit_price(total_cost, rate, *, pricing_mode="gross_margin") -> float` | 纯函数 |
| `price(package, *, gross_margin_rate=None, markup_rate=None, pricing_mode="gross_margin", addons=None, discount=None, tax_rate=DEFAULT_TAX_RATE, quote_quantity=None, actor=None, previous=None) -> dict` | §2.4 |
| `recompute(quote) -> dict` | 用 `lines` 的输入重跑，逐项相等 |
| `quote_fingerprint(quote) -> str` | 版本幂等键 |
| `sections(quote) -> dict` | `s3_markup` / `s4_markup` / `s5_basic` / `s5_detail` 四段工作台分区（形状与 `cpq_ui` 一致：`{"kind","title","rows"/"fields"}`） |
| `document(quote) -> dict` | §2.5 |
| `save_version(conn, quote, *, user) -> dict` | 只在给定连接上 INSERT/SELECT，**不 commit 不 close**（同 `cpq_wf.*(conn=…)` 的既有约定）；返回 `version_no` / `already_saved` |
| `versions(conn, *, business_case_id="", quote_session_id="") -> list` | 全部版本（新的在前，读回时按 `version_no` 降序） |
| `latest(conn, *, business_case_id="", quote_session_id="") -> dict` | 最新版本 |
| `restore(conn, *, quote_session_id="", business_case_id="") -> dict` | 历史进入时恢复：`{"found", "quote", "versions", "document", "sections"}`；没有版本时 `found=false`，**不报错** |

`price()` 从包里继承这些溯源字段（不许由调用方另传）：

| 报价字段 | 来源 |
| --- | --- |
| `quote_session_id` | `package.source.quote_session_id or package.source.source_session_id` |
| `business_case_id` | `package.source.business_case_id` |
| `source_tech_project_id` | `package.source.project_id` |
| `source_handoff_id` | `package.source.handoff_id` |
| `requirement_no` / `scenario_code` | `package.source.*` |

### 4.3 `cpq_tech_bridge` / `cpq_bridge` / `cpq_wf`

- `cpq_bridge.send_to_quote(..., handoff_kind="cost_to_quote")`：新增关键字参数，默认值保证
  三行业行为**逐字不变**；payload 的 `handoff_kind` 用传进来的值；
- `cpq_tech_bridge.HANDOFF_KINDS` 增加 `"packaging_cost_to_quote"`；
  `_HANDOFF_ADVANCE_KINDS` 增加它（与 `cost_to_quote` 一样推进第 2 步）；
  `_HANDOFF_LABELS` 增加 `"packaging_cost_to_quote": "包装成本回传报价"`；
- `cpq_tech_bridge.packaging_snapshot(result) -> dict`：包装专用第 2 步快照，至少含
  `s2_packaging`（盒型 + 参数 + 数量 + 场景）、`s2_packaging_cost`（成本分项 + 缺口数 +
  成本版本）、`packaging_package`（完整包，原样保真，供历史与看板重建）；
- `cpq_tech_bridge.send_to_quote(...)`：`handoff_kind == HANDOFF_KIND` 且
  `result["industry"] != "packaging"` → `BridgeError`；包内 `has_gaps` 为真 → `BridgeError`
  并说明缺口；任务 payload 增加 `packaging_package`；
- `cpq_wf`：`_ddl_pg` 增加 `cpq_wf_quote_version`（§3.2）；`init()` 的返回文案包含它。

### 4.4 `cpq_agent_server`

- `PACKAGING_QUOTE_PRICE_PATH = "/api/packaging-quote/price"`；
- `_handle_packaging_quote_price(data, emit=None) -> dict`：入参 `{"package": {...}, "gross_margin_rate":
  …, "addons": […], "discount": {…}, "tax_rate": …}`；成功返回
  `{"ok": True, "quote": …, "sections": …, "document": …}`；失败返回 `{"ok": False, "error": …}`
  （不抛给调用方）；**不依赖大模型**（"无模型"模式下也必须成功，这是包装与三行业定价路径的
  根本区别）；
- `do_POST` 必须把该路径派发到该处理器；既有的 `/api/markup/fill` → `_handle_markup_fill`
  派发**不动**。

### 4.5 `tech_app/backend/main.py`

| 路由 | 方法 | 角色 | 说明 |
| --- | --- | --- | --- |
| `/api/projects/{project_id}/requirement/packaging-quote/send` | POST | `packaging_handoff.HANDOFF_WRITE_ROLES` | 落库 + 回传报价（请求体 `PackagingQuoteSendAction`：`requirement_no` / `scenario` / `allow_gaps` / `reason`） |
| `/api/projects/{pid}/requirement/packaging-quote` | GET | 已登录 | 最近一次交接（`built=false` 不报错） |
| `/api/projects/{pid}/requirement/packaging-quote/versions` | GET | 已登录 | 全部交接版本 |
| `/api/projects/{pid}/requirement/packaging-quote/package` | GET | 已登录 | 交接包预览（不发送） |

读路由路径参数写 `{pid}`、写路由写 `{project_id}`：沿用第 5/6/7 批的既有约定，避免顶掉
「单参数 GET 路由」基线；路由路径字面量仍在源码里逐字出现。

## 5. 非目标

- 不做阶梯报价的**多方案比选界面**：本批支持"多场景各自定价 + 各自成版本"（第 7 批的
  `cost_curve` 提供数量阶梯），但不在本轮做方案对比交互；
- 不做 BPM 审批流（`报价规则.html` 的审批链不进本批）；
- 不做议价 / `pricenego` 的模型链路（三个原行业继续用）；
- 不做产能排程、拼版优化、设备日历；
- 不改三个原行业的成本算法（`generic_v1`）与定价路径（`md_clm_material_price_rule` + 模型）；
- 不接线上 PDT / Postgres 之外的任何生产库写入；测试只落本地 SQLite 与受控假库。

## 6. 历史兼容

- 没有交接记录的旧项目：读接口返回 `built=false` / `handoff=null`，不报错；
- 交接记录没有 `updated_at` 列、报价版本表也没有：两张表都是**只追加**，没有 UPDATE 路径；
- 没有报价版本的旧报价卡片：第 2 步快照里没有 `packaging_package` 键时，报价侧按
  "非包装卡片"处理，不报错、不改写快照；
- `wip_packaging_handoff` 与 `cpq_wf_quote_version` 都是**只追加**表，迁移=建表，
  回滚=停用新入口（旧表与旧快照不受影响）；
- `cpq_bridge.send_to_quote` 的 `handoff_kind` 默认值保证既有三行业调用点不需要改。

## 7. 可自动化验收

`tests/test_packaging_quote_close_loop_red.py` 实际运行，分组：

- A 契约与命名（模块、常量、闭集、与第 1 批注册表一致）
- B 交接包内容（10 组、来源、数值取自第 7 批、不许出现售价字段、公式依据可追溯）
- C 交接前置与拒绝（非包装、无成本、缺口、放行留痕、角色）
- D 交接落库与幂等（只增、同指纹复用、变更后新版本、历史可读）
- E 定价引擎（黄金值、两种模式、加价/折扣/税金顺序、守卫、纯函数、复算一致）
- F 报价单（八节、数字与 quote 一致、来源可追溯、缺口不出正式单）
- G 报价版本（只增不改、新版本不覆盖、latest/versions、角色门禁、幂等）
- H 桥接落点（包装快照三段、payload、handoff 记录、三行业逐字不变）
- I 报价服务入口（`/api/packaging-quote/price` 无模型可用、非包装不接管、markup 路径不动）
- J 历史恢复与四行业回归（重连后可读、三行业成本与定价路径不变、离线约束）

## 8. 人工验收

1. 包装需求 → 盒型 → BOM → 路线 → 成本 → 回传报价 → 定价 → 报价单，全链路走一遍；
2. 报价卡片第 2 步能看到盒型/参数/BOM/路线/成本/缺口，而不是只有一句 note；
3. 技术工艺重算成本后再次回传：报价里出现版本 2，版本 1 仍能打开；
4. 断网/无模型环境下定价仍然可用（`/api/packaging-quote/price` 不依赖模型）；
5. 半导体 / 电池 / 电器三条链路回归：成本四项与定价行为与改动前一致。

## 9. 不允许减少的既有能力

- 三个原行业的成本四项（材料/人工/制费/加工）与 `generic_v1` 固定系数算法一份不动；
- 三个原行业的第 3/4 步加价仍走 `md_clm_material_price_rule` + 模型，行为不变；
- `cost_flow` 的四个去向（确认成本/写入物料/发送报价/返回工艺）与 `cpq_tech_bridge` 的四种
  交接类型行为不变；
- 第 5/6/7 批的包装 BOM / 路线 / 成本结果与缺口闭集不变；
- `cpq_wf_card_step` 的既有快照合并语义不变（包装快照是**新增键**，不改合并规则）。

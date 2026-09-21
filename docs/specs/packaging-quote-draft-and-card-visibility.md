# 规格：缺口包的草稿报价 + 报价卡片里看得见包装分区

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_quote_draft_and_card_visibility_red.py`

血缘：承接 `packaging-quote-close-loop.md`（第 8 批：交接包 / 定价 / 报价单 / 报价版本）、
`packaging-downstream-blockers-close-loop.md`（批 12：放行留痕过桥 / 需求退回草稿 / 人工来源空值）、
`quote-agent-industry-alignment.md`（行业化需求门禁）、`dwg-semantics-agent-flow.md`（第 5 批：门禁矩阵）、
`packaging-parts-selectable-panel.md`（零件面板）。本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

**与同批另两份 Spec 的边界（不重复、不冲突）**：

- 「图纸字段的人工确认 / 人工字段不被解析降级 → 门禁转绿」由
  `packaging-manual-field-confirmation.md` 与 `packaging-parse-to-downstream-seams.md` §3.1 承接
  （那里的口径是**门禁判据 + 确认通道**，不是本 Spec 的职责）；
- 「技术工艺看板里的 64 件零件列表必须真的渲染出来」由
  `e2e-packaging-dwg-quote-tech-continuity.md` §4 承接；本 Spec 只管**报价卡片**
  （`确认需求解析结果.html`）里的包装分区与定价路径。

## 0. 一句话目标

把"**零件拆出来之后，报价侧到底能不能看见、能不能据此出价**"这两处接起来：
① 带缺口的包**必须能出草稿报价**（正式报价单仍然拦住）；
② 报价卡片第 2 步必须**真的渲染出包装分区**（盒型与参数 / 成本构成），第 3–5 步看得见定价与报价明细。

## 1. 现状缺口（34 上真跑证据，逐条可复现）

### 1.1 缺口包连"草稿报价"都出不来，与自己的门禁自相矛盾（P0）

34 上项目 `325effd296a5`（酒盒.dwg 全流程演示，`REQ-325EFFD296A5`）：

```
POST /agents/quote/api/packaging-quote/price   （SM1，gross_margin_rate=0.25）
→ {"ok": false, "error": "成本仍有缺口，只能出成本与草稿，不得生成正式报价单"}
```

- 错误文案说"**只能出成本与草稿**"，实现却是硬拒：`cpq_packaging_quote.price()`
  （`cpq_packaging_quote.py:243`）对 `has_gaps=True` 直接
  `PricingError("成本仍有缺口…", 409, "cost_gaps_unresolved")` —— 全仓**只有这一个入口**，
  于是"草稿"这条路根本不存在；
- 同一个包里技术侧的门禁明说：`gates.quote_draft.status == "open"`（blocking 为空）、
  只有 `quote_publish` 是 `blocked: cost_gaps_unresolved`（`/requirement/packaging-quote/package`
  的 `gates` 字段，34 上实测）；
- 后果：全流程的第 3 步「定价-利润加成」在线上做不出来 —— 真实盒子几乎必然带缺口
  （`material_gsm_missing` / 材料无价 / `no_formula:print`），所以**任何真实单都卡在定价**。

### 1.2 报价卡片里看不见包装：面板没人引用、根路径调用 405（P0）

- `tech_app/frontend/packaging-quote-panel.js`（渲染 s3_markup / s4_markup / s5_basic / s5_detail）
  **没有任何页面引用**（`quote-agent-industry-alignment.md` §1.3 已登记为现状）；
- `cpq_agent_server._BI_SECTIONS` 里没有 `s2_packaging` / `s2_packaging_cost`，而报价页
  `确认需求解析结果.html` 从快照恢复分区时只认 `FORMS` 里的 section id（`wfRestoreStepData`）——
  于是第 8 批 Spec §8 人工验收第 2 条（"报价卡片第 2 步能看到盒型/参数/BOM/路线/成本/缺口"）
  **做不到**：卡片上只有一个任务 note；
- 面板里的定价路径是根相对 `/api/packaging-quote/price`，34 上实测 **405 Method Not Allowed**
  （`cpq_suite_server._TECH_PREFIXES = ("/api/", …)` 把 root 的 `/api/*` 全转给 tech_app），
  而报价页其它 Agent 调用（`/api/step1/match`、`/api/markup/fill`）都走
  `AGENT_URL = "/agents/quote"`（`确认需求解析结果.html:1046`）——两条路不一致。

### 1.3 图纸字段的门禁死结（本批只引用，不重复定义）

34 上实测：八步全 completed、64 件零件都在，但 `/requirement/packaging-quote/package` 的
`gates.box_match / bom / route / cost` **全部 blocked（`field_unconfirmed`）**；为了让下游动起来
只能**手写** `field_provenance` + `field_sources`（绕过，不是能力）。这条缝的**口径与修法**写在
`packaging-manual-field-confirmation.md`（门禁判据 + 人工确认通道）与
`packaging-parse-to-downstream-seams.md` §3.1（人工字段不许被解析降级）——本 Spec 不复述、
也不另立第二套接口或第二套判据。

### 1.4 报价版本从不落库（P1，本批只登记）

`cpq_packaging_quote.save_version()` 全仓无调用点：定价接口只返回 `quote/sections/document`，
报价版本表永远空 —— 第 8 批 §2.6 的"报价版本不变式"没有生产入口。本批不改（见 §6）。

## 2. 允许修改范围（实现方）

1. `cpq_packaging_quote.py`
   - `price(package, …, publish: bool = False)`：缺口 + `publish=False` → **正常返回**草稿报价，
     并附 `draft=True` / `publish_blocked=True` / `publish_block_reason="cost_gaps_unresolved"` /
     `gap_count` / `gaps`（原样）；缺口 + `publish=True` → 照旧
     `PricingError(409, "cost_gaps_unresolved")`；
   - `document(quote, *, publish: bool = False)`：`publish=True` 且 `quote.gaps` 非空时同样拒绝；
     草稿（`publish=False`）时 `markdown` 第一行之后必须写明"本报价为缺口草稿，不得对外发布"；
   - `lines` / 公式 / 数值口径一个字不改；`recompute()` 对草稿同样成立。
2. `cpq_agent_server.py`
   - `_handle_packaging_quote_price` 透传 `publish`（缺省 False）；
   - `_BI_SECTIONS` 增两条：`s2_packaging`（`("table", "包装：盒型与参数", …)`）与
     `s2_packaging_cost`，**kind/title 与 `cpq_tech_bridge.packaging_snapshot()` 产出的分区逐字一致**。
3. `确认需求解析结果.html` + `tech_app/frontend/packaging-quote-panel.js`
   - 页面必须引用面板脚本，并在快照里有 `packaging_package` 时调用 `PackagingQuotePanel`；
   - 面板必须支持**注入定价基址**（缺省与该页其它 Agent 调用一致：`AGENT_URL`），
     不许把根相对的 `/api/packaging-quote/price` 写死成唯一入口。
（`packaging_drawing_flow` / `main.py` 的门禁与确认通道**不在本批范围**：
见 `packaging-manual-field-confirmation.md` §2。）

## 3. 口径（逐条验收）

### 3.1 草稿 / 正式的边界

- `publish=False`：缺口包必须有完整数字链（`cost_total → margin_price → addon_total →
  subtotal_unit → discount_amount → net_unit_price → tax_amount → taxed_unit_price`）与
  `gaps`，`draft is True`、`publish_blocked is True`；
- `publish=True`：缺口未清 → `PricingError(409, "cost_gaps_unresolved")`，码与文案不变；
  缺口已清 → 正常返回（`draft is False`）；
- **不许**把 `has_gaps` / `gaps` 洗成干净值来"绕过"（草稿必须如实带缺口）。

### 3.2 卡片可见性

- 报价页必须引用 `packaging-quote-panel.js`；
- `_BI_SECTIONS` 的 `s2_packaging` / `s2_packaging_cost` 与后端快照分区同名同标题，
  这样 `wfRestoreStepData()` 才恢复得出来；
- 面板的定价调用必须可注入基址（默认 `AGENT_URL`），不得写死根相对路径。

### 3.3 门禁联动（只引用，不在本批复核）

报价侧要能出价的前提是门禁转绿；这条由 `packaging-manual-field-confirmation.md` 的红测
B 组（`box_match` 转 open）与 `packaging-parse-to-downstream-seams.md` 的 A5 覆盖，本 Spec 不重复断言。

## 4. 禁止事项

- 不许在缺口未清时允许 `publish=True`、不许生成"正式"报价单；
- 不许改 `ADDON_CATEGORIES` / `DEDUCTION_CATEGORIES` / 税率 / 公式 / `DOC_SECTIONS` 八节；
- 不许在本批里另立第二套"人工确认"接口或第二套门禁判据（那条缝归
  `packaging-manual-field-confirmation.md`）；不许把 `EDITABLE_STATUSES` 放宽；
- 不许改任何既有红测的字面常量；
- 不许 commit / push / tag / Release / 部署 / 重启服务 / 写生产库。

## 5. 验收

```bash
# 本批红测（实现前必须真的红）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_draft_and_card_visibility_red -v

# 不回归（冻结面）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_close_loop_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_downstream_blockers_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red
./open-claude/.venv/bin/python -m unittest tests.test_quote_agent_industry_alignment_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red
```

## 6. 本批不做（写在这里，别当成已通）

- 报价版本落库（§1.4）：`save_version()` 的生产入口（谁在什么时候落版本、页面怎么读历史版本）
  需要单独一批；
- 技术工艺看板里的 64 件零件列表渲染归 `e2e-packaging-dwg-quote-tech-continuity.md` §4；
- 门禁与人工确认通道归 `packaging-manual-field-confirmation.md` /
  `packaging-parse-to-downstream-seams.md`；
- 缺口的业务清账（材料价格 / 损耗率 / `print` 公式 / 刀模分摊基数）仍按
  `packaging-cost-gaps-closure.md` 的登记走。

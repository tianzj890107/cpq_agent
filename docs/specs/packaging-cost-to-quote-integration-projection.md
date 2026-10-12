# Spec：包装项目的成本确认之后，整机成本必须是同一份包装账（否则回传销售走不通）

状态：Spec + 红测（成本投影已实现；包装参数投影本批已实现）
红测：`tests/test_packaging_cost_to_quote_integration_projection_red.py`（7 条：成本投影 4 + 包装参数 3）
血缘：`docs/specs/packaging-single-cost-ledger.md`（单份成本账、不复制算法）、
`docs/specs/tech-cost-confirm-gaps-and-waiver.md`（确认成本与缺口签字）、
`docs/specs/packaging-multipart-business-part-complete.md`（一个业务零件多组成）。
关联：`docs/specs/packaging-material-similarity-recommendation-and-demo-inputs.md`
（本批的演示输入让包装账第一次真的算出材料费）。

## 1. 现场事实（34 线上实测，非推断）

项目 `6beeeebb19a1`（酒盒，2026-10-12 演示装配后）：

- 包装账已算齐：`packaging_cost_ledger.load()` → `reconciled=true`、
  `material_total=11.46`、`total_cost=15.60`；
- `POST /cost-review/confirm` 通过（`cost_review` 缺口为 0，无需签字放行），
  这一步在修复"组成发号归集"之后不再报 400；
- **同一个项目 `integration.load_plan().cost` 是 `None`**：包装 2.3 的
  「整机成本测算」分支只调 `packaging_cost.build_cost()`，从不回填集成计划成本；
- 于是 `cost_flow.send_to_quote()` 在 `integration_ready_cost()` 上被
  「请先完成整机成本测算，再写入数据库或发送至报价」拦死。

结论：**"技术工艺把成本回传给销售"这一步，在包装链上从来没有真的通到过。**
它不是数据缺口，是投影缺口 —— 同一份成本存在两处，一处有值、一处为空。

## 2. 契约

### 2.1 C1：包装项目的整机成本 = 同一份包装账

包装项目（`requirement.data.industry == "packaging"`）在包装账已构建且未过期时，
`cost_flow.integration_ready_cost(project_id)` **必须不抛错**，并且返回的 `plan.cost`
非空、`items` 非空。

投影口径逐条：

- 金额**逐字取包装账的四项对账结果**（`packaging_cost_ledger` 的 `final`），
  不许重算、不许另起一套系数：材料、人工、加工、工装、包装、运费、其它一一对应；
- `cost_model` 直接写这份 `final`，使 `cost_model.breakdown()` 与 `cost_model.as_rows()`
  得到的就是包装账的数字；
- 明细行按包装账的分类给出（材料 / 人工 / 加工 / 工装 / 包装物流 / 其它），
  金额与 `final` 一致；
- `summary` / `assumptions` 必须说明这是**包装账的投影**，并带上这次投影的来源
  （`estimate_id` 或成本账版本），让人事后查得出这一版整机成本是哪一版包装账。

### 2.2 C2：陈旧或不存在的包装账不许投影

- 包装账 `built=false` 或 `stale=true` 时，仍然按今天的口径拒绝
  （「请先完成整机成本测算，再写入数据库或发送至报价」），不许拿一份过期或空的账
  冒充整机成本；
- 非包装项目一个字都不许变。

### 2.3 C3：已有人工/模型测算过的整机成本不许被覆盖

`plan.cost` 已经有 `items` 时，投影**不执行**（既有的 2.2 整机成本优先）。
投影只补"从来没有整机成本"的那一类项目，不覆盖任何已存在的测算结果。

### 2.4 C4：回传销售因此可用

C1 成立之后，`cost_flow.send_to_quote()` 不再卡在 `integration_ready_cost()`。
参数（`plan.params`）为空时既有口径不变（`product_params.missing_required(None)` 不拦），
本批不改参数与工艺环节。

### 2.5 C5：包装项目的回传必填按包装族校验

包装项目的 `integration_send_to_quote_body()` 做必填检查时用的产品族**必须**是
`pkg_box`（`integration.project_family()` 的既有口径），不许因为
`plan.params.product_family` 为空就退回「其他成品」去问 **产品系列 / 产品型号 / 最大尺寸 /
重量 / 工作温度 / 成品描述** —— 这些字段在包装链上**没有任何来源**，问了就是死结。

现场（34 实测，项目 `6beeeebb19a1`）：参数推荐跑过，`plan.params` 里只有平台自动写的
`product_item_code` / `product_item_name`，`product_family` 是空的，回传于是报
「报价必填的成品参数还缺：产品系列、产品型号、最大尺寸、重量、工作温度」。

### 2.6 C6：包装成品参数从需求单投影，且不许编值

包装项目的「参数推荐」结果里，需求单 `data` **已有值**的包装族字段（内长 / 内宽 / 内高 /
盒型 / 闭合方式 / V 槽 / 面纸克重 / 报价数量 …）必须直接投影进去并落盘，来源注明是需求单
投影；**需求单没有的字段一个都不许编**（`包装产品名称` / `包装品类` 这类只写在需求文本里的，
由「参数推荐」环节补，或者由文本抽取补）—— 缺的照旧由 `missing_required()` 如实点名。
已存在的参数行（人工或模型填过的）一个字不改。



- 不改包装成本算法、费率、损耗、缺口分级、对账口径；
- 不改 `cost_review.confirm_review` 的缺口与签字口径；
- 不改 `integration.analyze_cost` 的模型路径（模型给的整机成本仍然是首选，投影只是兜底）；
- 不改报价侧的定价与加价规则。

## 4. 验收

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_to_quote_integration_projection_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_single_cost_ledger_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_gaps_red -v
./open-claude/.venv/bin/python -m unittest tests.test_e2e_packaging_downstream_handoff_red -v
```

## 5. 实现及测试记录（2026-10-12）

- 实现只加一处编排：`tech_app/backend/services/cost_flow.py` 新增
  `packaging_cost_projection(project_id)`（包装项目 → 读 `packaging_cost_ledger.load()` 的
  对账结果 `final` → 拼成 `CostAnalysis`，明细按包装账分类、`cost_model` 直接写 `final`
  并附 `source="packaging_ledger"` 与 `estimate_id`），并让 `integration_ready_cost()` 在
  `plan.cost` 为空时先尝试这条投影，投影不成立才照旧拒绝。
- `integration_send_to_quote_body()` 把 `author` 透传给 `integration_ready_cost()`，投影写入
  落在实际发起回传的人名下。
- 金额一个字没动：投影不重算、不套企业成本口径系数，`cost_model.breakdown()` 读到的
  `material/labor/overhead/machining/total` 与包装账 `final` 逐项相等（红测 a2 钉住）。
- 红测 `tests/test_packaging_cost_to_quote_integration_projection_red.py` 4 条由 2 红转全绿
  （a1/a2 红→绿；a3 已有整机成本不被覆盖、a4 没有包装账仍拒绝，两条护栏自始为绿）。

## 6. 第二轮实现及测试记录（2026-10-12）

- 现场推进：成本投影上线后，回传的拒绝文案从「请先完成整机成本测算」变成
  「报价必填的成品参数还缺：产品系列、产品型号、最大尺寸、重量、工作温度」——
  即 §2.5 的族口径问题浮出水面。
- 实现：`cost_flow.py` 新增 `packaging_handoff_family()`（直接复用
  `integration.project_family()`）、`packaging_param_projection()`、
  `ensure_packaging_params()`；`integration_send_to_quote_body()` 在必填检查前调用
  `ensure_packaging_params()`，并把族显式传给 `product_params.missing_required()`。
- 红测 b1（不许问产品系列 / 工作温度）、b3（不许给需求单没有的字段编值）由红转绿；
  b2（需求单已有的字段不许再问一遍）本批为护栏。
- 仍未闭合的一步：`包装产品名称` / `包装品类` 只写在需求文本里，本批**不编值**，
  由「参数推荐」环节（模型）或文本抽取补上。

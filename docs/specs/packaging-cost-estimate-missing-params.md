# 包装成本：缺的参数按相似件估计（不需要 approved）

血缘：`docs/specs/packaging-part-route-constrained-match.md`（受约束匹配：先精确、再归一化、最后受硬约束
的相似度，匹不上显式 `unbound`）、`docs/specs/packaging-analogical-process-recommendation.md`（相似件
只借鉴、不继承标准工时/费率/批准状态）、`docs/specs/packaging-demo-native-and-analogy.md`（演示件未知
工时 30 秒、人工 36 元/小时的 `poc_mock` 估算口径）、`docs/specs/packaging-cost-engine.md`（公式目录
`FORMULA_CATALOG` 与费率一律不改）。

状态：Spec + 红测
红测：`tests/test_packaging_cost_estimate_missing_params_red.py`

## 0. 一句话目标

**公式一行不改**。当一件的工艺在库里**对不上**（只能是相似件 / 库内无命中）时，缺的**输入参数**不再
只留缺口：按相似件把 **标准工时（`standard_seconds`）** 估出来并逐条标 `similarity_estimate`，让下游
人工费算得出钱；**不要求任何人先批准（approved）**，但估算值**绝不冒充**标准工时 —— 结果里说清
"这是估算、来源是哪个相似件/哪条模板、未审核"。

## 1. 现状缺口（代码事实 + 2026-10-14 只读实测）

1. `da_process_routing._similar_route()` 的相似命中只给参考、不给工时：`packaging_manufacturing.outline()`
   的相似/未命中那一支走模型，`ProcessStep.standard_seconds = None`（`packaging_manufacturing.py:106`）。
   于是 `packaging_process_instances.expand()`（`:66`）原样带出 `None`，成本人工行落 `step_time_missing` 缺口。
2. `approved` 只在**精确编码命中且来源不是演示**时才为真（`cpq_process_routing.normalize()`：
   `match_method == 'product_item_code'`、`data_source != 'yutong_wine_box_quote'`、状态已生效…），
   所以真实样本（`yutong_wine_box_quote`）**全部** `approved=False`，逐件工时永远取不到 → 这就是
   "没有 approved 就算不出人工费"的根因。
3. 只读实测：`master_data.md_clm_process_routing_operation` 124 行，`man_time` / `machine_time` /
   `setup_time` **全为 NULL**（`man_time_unit` 也全空）。即"相似路线"本身给不出工时，
   **不能**把 `approved` 一放就当它继承了标准工时。
4. 有工时的只有 `cpq_kb.kb_packaging_process_template`：`YT-RB-01001-A` / `YT-RB-02001-A` / … 每行带
   `standard_seconds`（10~120 秒），而 `YT-DWG-ROUND-10PC` / `YT-DWG-WINE-700ML` 的模板行
   `standard_seconds` 为空 —— 所以"相似件能给工时"这件事**必须按同名工序去模板里找**，找不到才用兜底
   估算口径，并如实写出来源。

## 2. 契约（新增 `tech_app/backend/services/packaging_process_estimate.py`）

### 2.1 常量（逐字）
- `ENGINE_VERSION = "packaging-process-estimate/1"`
- `ESTIMATE_TIME_SOURCES = ("similarity_estimate",)`
- `ESTIMATE_KINDS = ("standard_seconds",)`
- `ESTIMATE_ORIGINS = ("reference_route", "similar_step_template", "operations_estimate_policy")`
- `DEFAULT_OPERATION_SECONDS = 30.0`

### 2.2 纯函数
- `similarity_route(lookup) -> bool`：`lookup` 表明这一件只能靠相似/没命中时才为真 ——
  `match_method == "feature_similarity"`，或 `status in ("not_found", "unavailable")`，
  或带非空 `reference_routes`；`exact_code` / `exact_name` / `normalized_name` 一律为假（精确命中不重估）。
- `reference_operations(lookup) -> dict`：把 `lookup["reference_routes"]` 与（相似时的）`lookup["routes"]`
  里的 `steps` 收成 `{规范化工序名: standard_seconds}`，只收**数值非空**的行；无则空 dict。
- `template_seconds(step_name, templates=(), *, box_type_code="") -> dict|None`：在模板行里按
  `packaging_route.normalize_step_name()` 的归一化名匹配（同名工序，如 `V槽围边`↔`V 槽开槽`），
  先同盒型、再任意盒型；命中返回值非空 `standard_seconds` 的行 → `{"seconds", "origin", "reference"}`。
- `estimate_seconds(step_name, *, lookup, templates=(), box_type_code="") -> dict|None`：判据顺序固定
  ① 相似路线同名工序工时 → `"reference_route"`；② 相似工序模板同名工时 → `"similar_step_template"`；
  ③ `DEFAULT_OPERATION_SECONDS` → `"operations_estimate_policy"`。**只有 `similarity_route(lookup)` 为真
  才估**，否则返回 `None`（精确命中 / 外购件不重估）。
- `fill_step(step, *, name, lookup, templates=(), box_type_code="", part_code="") -> dict|None`（就地补
  `step`，返回估计留痕）：仅当 `step["standard_seconds"] is None` 且 `similarity_route(lookup)` 为真；
  写入 `standard_seconds` / `duration_min` / `time_source="similarity_estimate"` / `estimated=True` /
  `estimate={"kind","origin","reference","value","engine_version","part_code"}`，并把 `labor_seconds`
  同步进 `cost_parameters`（`estimated=True` / `estimate_origin`）。**不改** `formula_code` /
  `cost_category` / 其它参数。

### 2.3 接线（只加可选入参，默认不启用）
- `packaging_process_instances.expand(records, *, templates=None, box_type_code="")`：新增两个**可选**入参；
  给了 `templates` 才对每条记录的每个缺工时步调 `fill_step`，`needs_standard_time` 随之重算
  （估值填上后不再是"缺工时"，但 `estimated=True` 明写这是估算）。
- `packaging_route.build_route()`：把 `kb_repo.packaging_process_templates()` 与确认盒型传进 `expand()`。
- 估算值随既有链路进 `packaging_cost.compute_project()` 的人工费公式；**公式与费率不动**。

## 3. 禁止
- 不改 `packaging_cost.FORMULA_CATALOG` / 任何费率 / 任何公式结构；不改 `kb_cost_rate`；
- 不改 `packaging_part_route_match` 的身份/冲突口径与 `rank_route_features` 权重；
- 不把估算值写回 DA / 不写 `approved=True` / 不冒充标准工时；
- 不使用包围盒面积当轮廓或当工时依据；不新增第三方依赖、不联网、不调模型；
- 不改 `packaging_manufacturing.outline()` 既有产出（模型步仍 `standard_seconds=None`，估算只在下游装配）。

## 4. 验收
- `./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_estimate_missing_params_red`
- 真实样本：对相似/未命中件，`expand()` 后该步 `standard_seconds > 0`、`time_source == "similarity_estimate"`、
  `estimated is True`、`estimate["origin"]` 落在 `ESTIMATE_ORIGINS` 内；精确命中件与已有时工的步**一字不改**；
- 保护网不回归：`test_packaging_part_route_constrained_match_red`、`test_packaging_analogical_process_red`、
  `test_packaging_process_route_red`、`test_packaging_unified_route_cost_red`、
  `test_packaging_cost_engine_red`、`test_packaging_cost_red_closure_red`、`test_packaging_demo_flow_end_to_end_red`。

## 5. 边界
- 本批只估 `standard_seconds`（人工费唯一真正缺的、且相似件/相似工序能给出来的参数）；
  排模参数（`machine_length` / `machine_width` / `imposition_count`）与材料价、损耗率
  **不在本批**：它们的权威来源是尺寸/材料/费率，不按相似件编。
- `operations_estimate_policy`（30 秒）只是**兜底**：相似路线与相似模板都给不出工时才用，且在
  `estimate["origin"]` 里如实标注，不冒充模板值。

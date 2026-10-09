# 体验与可观察性（批次 4）：把「为什么走不动、当前算的是哪一版、缺什么」讲清楚

血缘：承接 `10-待拍板优化清单.md` 第四部分批次映射；
上游批次 1（链路一致性：单一缺口裁决源 + 金额唯一投影 + 版本失效）、
批次 2（发布保障）、批次 3（真实数据与识别覆盖）。
本批只做**解释面与可观察性**：不改成本公式、不改匹配算法、不改路线选择。

状态：Spec + 红测（已实现）（原状：本批只写 Spec 与红测；业务实现由 Codex 在本地落地，按本文 §5 未提交/未推送/未部署，红测 23 项已全绿）
红测：`tests/test_observability_and_experience_batch4_red.py`
复核基线：2026-10-09 实测（§1 的每条缺口都可指到文件/字符串）

## 0. 一句话目标

让销售、工艺、财务在**不猜**的前提下看懂四件事：**现在缺什么、当前算的是哪一版、
本次结果是谁算的、这一件为什么排在别人后面**；并把 `P1`/`P2` 从「三个技术术语一起教给用户」
降级为「主界面只突出业务零件 + 只显示有权看到的数量」。

## 1. 现状缺口（代码事实 + 10-9 实测）

- **P3（导出还差哪几步）**：`cost-review.js` 会用整段文案说「缺口/不能作为正式报价」，
  但没有一处把「正式导出前还差哪几步」结构化列出；用户只能从大段文字里自己拆。
- **P4（成本版本 + 暂估 + 缺口分类）**：成本缺口以后端 `cost_review.gaps` 为准（前端不另算），
  但缺口**没有分类**（缺材料价 / 缺用量 / 缺尺寸），也没有把「版本 + 是否暂估」作为一个
  统一投影给页面。
- **P11（排模不能算用量时缺什么）**：排模/单件参数缺失时后端给缺口，但页面没有
  一张「缺这些就不能算用量」的点名清单。
- **P6（本次转换由谁完成）**：`converter_role` 只在 `tech_app/tools/*`（部署自检、验收）
  出现，**前端没有任何文件**渲染「主/回退 + 版本 + 许可状态」（10-9 `grep converter_role
  tech_app/frontend` 为空）。
- **P7（表达式口径文案）**：既有 `extract_packaging_rules.py` 讲得清「登记源公式 + 变量映射后
  匹配」，但成本页没有一处把这句话作为**唯一文案来源**展示，存在被写成「逐字一致」的风险。
- **P8（知识库健康面板）**：无 `kb_health` / 知识库健康面板实现（10-9 `grep kb_health` 为空）；
  现有 `kb_deploy_preflight.py` 是**部署预检**，不是运行期健康面板。
- **P9（候选为什么不是更高分 + 转精准对照）**：`cpq_quick_quote_match._rank_reason()` 只解释
  「为什么排在这里 / 为什么不可用」，**没有**「相对上一名多/少了哪些差异项」；
  转精准报价也没有「基准案例 vs 当前参数」的对照表。
- **P10（在等谁 + 已等多久）**：`home_card._waiting_for()` 已给 `kind/role/label/username`，
  `tech-home-board.js::waitingText()` 已渲染「在等谁」，**但没有「已等多久」**。
- **P12（草稿角标按操作细分）**：无「草稿可看，不可送审」角标；且不能一刀切——
  草稿内部评审与正式报价发布不是同一件事。
- **P13（哪些图不建议自动识别）**：无「建议直接人工建档」的判据与建议文案。
- **P1/P2**：`P1` 前端仍可能同时出现「几何区域 / 几何零件 / 业务件」三个口径；
  `P2` 项目列表没有「按你的角色可见：N 个」的范围说明（且**不得**显示无权看到的数量）。
- **T15（曲线采样参数配置化）**：`cad_ir/parser.py` 把 `sample_step: 0.01` **硬编码**在 3 处
  （椭圆/样条/折线折展），不在 `DEFAULT_LIMITS`、不进 `parser.options` → 换参数不可复现。

## 2. 契约

### 2.1 P3 / P4 / P6 / P11 / P1 / P13 / P12 / P7 / P10 → 新模块
`tech_app/backend/services/packaging_observability.py`（纯函数、不连库、不写盘、不调模型）：

- `OBSERVABILITY_VERSION = "packaging-observability/1"`
- `GAP_CATEGORIES = ("缺材料价", "缺用量", "缺尺寸", "其它")`
- `MOLD_REQUIRED_PARAMS = ("sheet_width_mm", "sheet_height_mm", "piece_length_mm", "piece_width_mm")`
- `quote_export_blockers(*, steps, done) -> {"can_export": bool, "next_steps": [str, ...],
  "remaining": int}`：`steps` 为**完整有序**的正式导出前置步骤，`done` 为已完成子集；
  `next_steps` 按 `steps` 顺序给未完成项；`remaining == len(next_steps)`；`can_export == (remaining == 0)`。
- `cost_display(*, version, is_estimate, gaps) -> {"version": int, "is_estimate": bool,
  "gap_total": int, "gaps": {每类: n}}`：`gaps` 是可迭代的缺口项（每项含 `category`）；
  **四类键必须全在**（缺的补 0）；`category` 落在 `GAP_CATEGORIES` 之外的**归入「其它」**，
  不许静默丢弃；`gap_total == len(gaps)`。
- `mold_readiness(*, params) -> {"can_compute": bool, "missing": [str, ...]}`：
  `missing` 按 `MOLD_REQUIRED_PARAMS` 顺序给出空/0 的项；`can_compute == (not missing)`。
- `converter_banner(*, converter_role, provider, version, license_ok=None) ->
  {"done_by": str, "provider": str, "version": str, "license": "ok"|"unverified",
  "warning": str}`：`converter_role` 为 `primary`/`fallback`/其它 → `done_by` 分别为
  `"主转换器"`/`"回退转换器"`/`"未知"`；`license_ok is not True` → `license="unverified"`
  （**不许**把未知许可谎报成 ok）；`converter_role == "fallback"` 或 `license != "ok"` 时
  `warning` 非空。
- `cost_expression_note() -> str`：**恰为** `"变量映射后的表达式匹配"`；不得包含 `"逐字"`。
- `part_concepts_view(*, business_total, geometry_total) ->
  {"primary": {"label": "业务零件", "total": int},
   "secondary": [{"label": str, "total": int}, ...]}`：主界面**只**突出业务零件；
  几何区域数量进 `secondary`。
- `manual_drawing_advice(*, signals) -> {"recommend_manual": bool, "reasons": [str, ...],
  "advice": str}`：`signals` 命中任一「不建议自动识别」信号 → `recommend_manual=True`，
  `reasons` 点名命中的信号（顺序稳定），`advice` 给「直接人工建档」建议。
- `draft_visibility(action) -> {"badge": "草稿可看，不可送审", "can_read": True,
  "can_do": bool}`：`action` 为 `read` / `internal_review` / `formal_release`；
  `read` 与 `internal_review` → `can_do=True`（草稿内部评审 ≠ 正式报价发布），
  `formal_release` → `can_do=False`；三种 `can_read` 恒为 `True`。
- `waiting_view(waiting_for, *, now=None, last_event_at=None) -> dict`：**消费**既有
  `waiting_for`（`{"kind","role","label","username"}`）并原样保留这几个键，
  另加 `"elapsed_seconds": int|None`、`"elapsed_text": str`；`kind == "none"` 时
  `elapsed_seconds is None` 且 `elapsed_text == ""`；有时间输入时 `elapsed_seconds` 为两者之差（秒，非负）。

### 2.2 P8 → 新模块 `tech_app/backend/services/kb_health.py`（纯函数）
- `KB_HEALTH_VERSION = "kb-health/1"`
- `kb_health(tables, *, required_tables, rows_by_source=None, kb_version=None) -> dict`：
  - `tables`：`{表名: 行列表}`；空表 = 行数为 0；
  - 返回 `{"version", "table_total": int, "empty_tables": [名, ...], "required_empty": [名, ...],
    "demo_ratio": float, "kb_version": int, "usable_for_current_route": bool,
    "problems": [码, ...]}`；
  - **判据红线**：`usable_for_current_route` 只由 `required_tables` 决定——
    **无关表为空不得**宣布「全系统不可用」；`required_tables` 为空 → `False` 且
    `problems` 含 `"required_empty:<表名>"`；
  - `demo_ratio = demo 行 / 全部行`（无数据时 `0.0`）；`rows_by_source` 形如
    `{"demo": n, "workbook": n, ...}`。

### 2.3 P9 → `cpq_quick_quote_match.py`（在既有模块上新增纯函数）
- `explain_ranking(candidates) -> [{"case_code", "rank", "why_not_higher": [str, ...]}, ...]`：
  - 输入是 `match_cases()["candidates"]` 那样的**已排序**候选（含 `diff_items`）；
  - 第 1 名：`why_not_higher == []`；
  - 第 N(>1) 名：相对**第 N-1 名**比较 `diff_items`——
    多出的差异项给 `"比上一名多差异项：<标签>"`，少掉的给 `"比上一名少差异项：<标签>"`；
    两者差异项相同 → 给一条 `"差异项相同，相似度更低"`（不许给空理由）。
- `transfer_compare(base, current) -> {"rows": [{"field", "base", "current", "changed"}],
  "changed_total": int}`：逐字段对照基准案例与当前参数；`changed` 为两值是否不同；
  `changed_total == changed 的行数`。

### 2.4 P2 → `tech_app/backend/services/project_access.py`（新增纯函数）
- `visible_scope_note(*, scope, visible_count) -> {"scope": str, "visible_count": int,
  "note": str, "hidden_count": None}`：`note` **恰为** `"按你的角色可见：%d 个" % visible_count`；
  结构里**不得**出现无权项目的数量（`hidden_count` 恒为 `None`，或该键不存在）。

### 2.5 T15 → `tech_app/backend/services/cad_ir/parser.py`
- `DEFAULT_LIMITS` 新增 `"curve_sample_step"`（默认 `0.01`，正数）；
- 新增 `ENV_CURVE_SAMPLE_STEP = "CAD_IR_CURVE_SAMPLE_STEP"`，`resolve_limits()` 读**浮点**环境变量，
  并允许 `overrides` 覆盖；
- 3 处硬编码 `sample_step: 0.01` 改用解析后的值；
- `parser.options` 落 `"curve_sample_step"`（IR 元数据可复现）。

### 2.6 前端接线（唯一口径来自后端）
- `P4`：成本页（`tech_app/frontend/cost-review.js` 或 `cost.js`）渲染时引用后端 `cost_display`
  投影（不前端自算缺口分类）；
- `P9`：`tech_app/frontend/quick-quote-panel.js` 渲染候选时引用 `why_not_higher`；
- `P6`：至少一个前端文件引用 `converter_banner`（把「主/回退 + 版本 + 许可」展示出来）。

## 3. 不做什么

- 不改成本公式、费率、匹配算法与权重、路线选择与缺口裁决源（缺口一律以后端为准）；
- 不新增第二套缺口判定、不在前端自算相似度 / 缺口分类 / 是否暂估；
- 不把「几何区域 / 几何零件 / 业务件」三个术语同时教给用户（主界面只突出业务零件）；
- 不做大规模前端重构（只做本批要求的展示接线）；
- 不删/改历史数据；不动 `da_process_routing.py`。

## 4. 允许修改范围

1. 新增 `tech_app/backend/services/packaging_observability.py`；
2. 新增 `tech_app/backend/services/kb_health.py`；
3. `cpq_quick_quote_match.py` 新增 `explain_ranking` / `transfer_compare`；
4. `tech_app/backend/services/project_access.py` 新增 `visible_scope_note`；
5. `tech_app/backend/services/cad_ir/parser.py` 的曲线采样参数；
6. 本批要求的前端展示接线（`cost-review.js` / `cost.js` / `quick-quote-panel.js` 及 2.1 看板）。

## 5. 禁止事项

- 不许改 `tests/` 下任何既有文件（含本批红测）；
- 不许引入联网、写库、写生产目录的行为；新模块必须能从内存快照驱动；
- 不许把「缺口分类」「是否暂估」「相似度」在前端另算一份；
- 不许为了好看改 `P1` 的业务零件口径（几何数量只能进 secondary）；
- 不许 commit / push / tag / Release / 部署。

## 6. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_observability_and_experience_batch4_red -v
./open-claude/.venv/bin/python -m unittest tests.test_dxf_cad_ir_red -v            # 不回归（T15 改动面）
./open-claude/.venv/bin/python -m unittest tests.test_tech_home_timeline_and_publish_closure_red -v   # 不回归（P10 只加字段）
```

## 7. 验收清单（本批不做、只登记）

- [ ] 「正式导出前置步骤」的业务口径（哪几步、谁负责）由业务确认后写进 `steps`；
- [ ] `MOLD_REQUIRED_PARAMS` 与排模真实参数表对齐（本批先按 4 项占位）；
- [ ] `MANUAL_SIGNALS`（不建议自动识别的信号集）与工艺经理确认；
- [ ] `P2` 的范围说明**不能**替代修 ACL（可见性缺陷单独修）。

## 8. 实现记录（2026-10-09，Codex）

按 §2 契约逐条落地，只动 §4 允许范围：

- 新增 `tech_app/backend/services/packaging_observability.py`（纯函数）：
  `quote_export_blockers` / `cost_display`（四类键全在、未知分类归「其它」）/ `mold_readiness` /
  `converter_banner`（未知许可 → `unverified`，回退或非 ok 有告警）/ `cost_expression_note`
  （恰为「变量映射后的表达式匹配」）/ `part_concepts_view`（业务零件 primary、几何进 secondary）/
  `manual_drawing_advice`（`MANUAL_SIGNALS` 命中即建议人工）/ `draft_visibility`（`internal_review` 可做、
  `formal_release` 不可）/ `waiting_view`（原样保留 kind/role/label/username，另加 `elapsed_seconds`/`elapsed_text`）。
- 新增 `tech_app/backend/services/kb_health.py`：`kb_health` 的 `usable_for_current_route` 只由
  `required_tables` 决定（无关表为空不判死），`demo_ratio` / `required_empty` / `problems` 如实给。
- `cpq_quick_quote_match.py`：新增 `explain_ranking`（第 1 名空理由；第 N 名相对上一名多/少差异项，
  相同则「差异项相同，相似度更低」）与 `transfer_compare`（逐字段对照 + `changed_total`）。
- `tech_app/backend/services/project_access.py`：新增 `visible_scope_note`（`note` 恰为
  「按你的角色可见：N 个」，`hidden_count` 恒 `None`）。
- `tech_app/backend/services/cad_ir/parser.py`：`DEFAULT_LIMITS["curve_sample_step"]=0.01`、
  `ENV_CURVE_SAMPLE_STEP="CAD_IR_CURVE_SAMPLE_STEP"`、`resolve_limits` 读浮点环境变量并允许 overrides 覆盖；
  椭圆 / 样条 / 折线折展 3 处采样改用解析值并落 `sample_step`，`parser.options` 落 `curve_sample_step`。
- 前端接线（唯一口径来自后端）：`cost-review.js` 新增 `crCostDisplayNote` 引用 `cost_display`；
  `quick-quote-panel.js` 候选表新增「为什么不是更高分」列，读 `why_not_higher`；
  `tech-task.js` 新增 `converterBannerNote` 并在能力载荷带 `converter_banner` 时展示「主/回退 + 版本 + 许可」。

实跑（2026-10-09，`./open-claude/.venv/bin/python -W ignore -m unittest`）：

- `tests.test_observability_and_experience_batch4_red`：改前 23 红，改后 **`Ran 23 … OK`**；
- Spec §6 不回归：`test_dxf_cad_ir_red + test_tech_home_timeline_and_publish_closure_red` → `Ran 81 … OK (skipped=1)`；
- 受影响面（引用本批改动模块/文件的 71 个模块）→ `Ran 1465 … OK (skipped=2)`。

§7 四项（前置步骤业务口径、`MOLD_REQUIRED_PARAMS` 对齐、`MANUAL_SIGNALS` 确认、ACL 另修）仍留给后续批次/人工。

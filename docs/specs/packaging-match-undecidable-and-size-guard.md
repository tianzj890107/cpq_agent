# 规格：盒型匹配的"缺数据不许打折"与"尺寸越界不许推荐"

Spec 版本：1 · 状态：待实现（红测已就位）
红测：`tests/test_packaging_match_undecidable_and_size_guard_red.py`
上游：`docs/specs/packaging-box-type-matching.md`（五维打分与硬门槛的既有契约，本批只补两处缺口）

## 1. 背景（实测复现，不是推断）

用当前引擎算三种输入（演示 12 盒型 + 从真实 DWG 确认进来的 2 条盒型，权重 0.30/0.25/0.15/0.20/0.10）：

1. **缺一项就永久打折**：盒型行没有 `fit_clearance` 时，`_dimension_fit()` 返回 `0.0` 并把它
   计入分母；而**需求侧**缺同一项时该维直接跳过、分母也跟着变小。同一种"缺"，两张算法：
   - 需求缺 → 其余四维归一化，**能拿满分**；
   - 盒型缺 → 该维占 0.25 权重记 0 分，**最高只能 0.75**。
   后果：真实 DWG 确认进来的盒型（`YT-DWG-WINE-700ML`、`YT-DWG-ROUND-10PC` 都没有这一项）
   只要需求填了配合间隙就永远排不到前面 —— **新数据录了也不会被推荐**。
2. **同一行的值写法不同就判"看不懂"**：圆盘盒的 `v_groove` 写 `是（90度）`，`_as_bool()` 只做
   整词匹配（`是`/`有`/`true`/`y`），于是该维记 0 分并进 `undecidable_dimensions` —— 又白丢 0.10。
3. **尺寸差 13 倍照样"匹配"**：需求 30×30×20 时，`YT-DWG-ROUND-10PC`（396.5–408mm）的
   `status` 仍是 `matched`、`out_of_range=true`；而 `suggested_box_type` 与
   `needs_new_tooling` 都只看 `status`，于是结论是"**有现成盒型、不需要开模**"。
   尺寸维 `hard_gate=0`（柔性衰减是设计意图），但"越界"从来没有进入"能不能被推荐"的判定。
4. **报价侧逐字复刻了这三处**：`cpq_packaging_match.py` 与工艺侧同口径（
   `docs/specs/quote-packaging-box-library-selection.md` §2.1–2.2 要求两侧逐字段一致），
   所以销售在**报价工作台**看到的是同一套失真结论 —— 只改工艺侧等于没解决现场问题。

## 2. 目标

让"数据缺失"与"尺寸越界"各自有**一处**、可解释、可测的口径：缺数据不打折（但必须看得见），
越界的盒型不得被推荐、也不得让系统宣称"不用开模"。

## 3. 契约

### C1 同一维度两侧对称：缺了就不参与打分

- 判据：**盒型行 `fit_clearance` 缺失/无法解析**，与"需求未填"走同一条路 ——
  该维 `score = None`，**分子与分母都不计**（不是记 0 分占分母）；
- 该维仍必须进 `undecidable_dimensions`（与现状一致，"缺了要能看见"）；
- 对称性判据（红测）：同一个盒型，需求填了间隙与没填间隙，在该维之外的表现必须一致 ——
  盒型其余四维全对时 `total_score == 1.0`（现状是 0.75）。
- **`hard_gate` 语义不变**：`fit_clearance` 仍是硬门槛，但"判定不了"不等于"超差"，
  所以不淘汰；`closure_type` 的硬门槛与 `fit_clearance_out_of_tolerance` 行为一律不动。

### C2 缺数据必须看得见（可展示字段）

- `undecidable_dimensions` 非空的候选，必须多带一个 `data_gaps`：由维度名 + 人话说明组成的
  列表（例：`{"dimension": "fit_clearance", "message": "该盒型未登记配合间隙，本维未参与打分，需补齐或人工确认"}`）；
- `data_gaps` 为空时必须是 `[]`，不许是 `None`；
- 该字段只做展示，**不改变** `status` / `can_confirm`（人工确认优先）。

### C3 布尔类维度的写法归一（只放宽解析，不放宽判定）

- `v_groove` 解析时，`是`/`否` 后跟括号或空白说明视为同值：`是（90度）` → `True`、
  `否(无)` → `False`、`是 90度` → `True`；
- 只认"取值词开头"且整条是"取值词 + 一段说明"，不许把 `不是` / `否定的` 判成 `True`/`False`
  之外的值：无法识别时**保持现状**（记 0 分 + 进 `undecidable_dimensions`）；
- `_TRUE_WORDS` / `_FALSE_WORDS` 两个闭集不扩充（不许把 `是（90度）` 直接塞进闭集）。

### C4 越界的盒型不得被推荐

- `suggested_box_type` **只能**从 `status == "matched"` 且 `out_of_range == false` 的候选里取
  （仍按"总分降序、同分按盒型编码升序"）；
- 若没有任何"命中且不越界"的候选 → `suggested_box_type = ""`、
  `needs_new_tooling = true`、`new_tooling_reason = "size_out_of_range"`；
- **候选列表本身不变**：越界候选照旧列出、仍带 `out_of_range = true`、
  排序仍遵守 `_sort_key`（合规在前）—— 用户要能看到"接近但不合适"的选项，只是不会被推荐；
- `new_tooling_reason` 的既有取值优先级不变：`no_box_type` / `all_rejected` /
  `missing_required_input` 先于 `size_out_of_range`。

### C5 数据准入：权威盒型必须登记配合间隙

- `tech_app/tools/kb_deploy_preflight.py` 在 `env == "production"` 时，对
  **非 `demo`** 的盒型行缺 `fit_clearance` 报问题码
  `box_type_missing_fit_clearance`（可读说明 + 可执行动作：补数据或走确认流程）；
- `source_type == "demo"` 的行**不报**（样例数据本来就允许不全）；
- 该问题不改变 `local` / `ci` 的结论。

### C6 报价侧同口径模块必须同步

- `cpq_packaging_match.py`（报价侧；口径来源与 parity 契约见
  `docs/specs/quote-packaging-box-library-selection.md` §2.1–2.2）必须与工艺侧**逐条同行为**：
  C1（缺配合间隙不计分不打折）、C2（`data_gaps` 字段名与形状一致）、
  C3（带后缀布尔按同值解析）、C4（越界不推荐 + `size_out_of_range`）；
- 两侧的 `suggested_box_type` / `needs_new_tooling` / `new_tooling_reason` 在任何输入下都必须同值 ——
  这就是"报价工作台不掉队"的判据；
- 现有 parity 用例（`tests/test_quote_packaging_box_selection_red.py` B 组）覆盖不到
  "盒型缺配合间隙 / 越界候选 / 带后缀布尔"这三种输入，因此本 Spec 额外补 F 组红测；
- **不许**把报价侧改成 `import` 工艺侧来换取一致（合并两侧实现是更大的改动，需另行裁决）。

## 4. 不在本批范围

- 不改权重表、不改 `size_range` 的衰减公式、不改 `_sort_key`、不改"总分升序/降序"那条
  已记入交付报告的既有冲突（仍在等业务确认改红测还是改 Spec）；
- 不改硬门槛集合（`fit_clearance` / `closure_type`）；
- 不自动补任何盒型数据（补数据是业务动作，不做静默填充）；
- 不改前端展示（`data_gaps` 的界面接入单独提）。
- 不合并两侧实现（不做"报价侧 import 工艺侧"这类重构，见 C6 最后一条）；
- 不改权重表来源、不改 `MATCH_INPUT_KEYS` 与 `ENGINE_VERSION` 命名契约。

## 5. 验收标准

1. `python3 -m unittest tests.test_packaging_match_undecidable_and_size_guard_red` 全绿
   （23 条：A 5 / B 3 / C 3 / D 5 / E 3 / F 4）；
2. `tests/test_packaging_box_type_matching_red.py`（51 条）与
   `tests/test_quote_packaging_box_selection_red.py` 一条都不许因此转红
   （尤其 `e2` 越界候选不得排第一、`e5` 全淘汰要新开模、`b2` 衰减律）；
3. 手工复核：12 演示盒型 + 2 DWG 盒型的快照下，
   - 需求填齐且尺寸合规 → `suggested_box_type` 非空、`needs_new_tooling == false`；
   - 需求 30×30×20 → `suggested_box_type == ""`、`new_tooling_reason == "size_out_of_range"`；
   - **两侧同值**：同一份快照喂给工艺侧 `match_box_types()` 与报价侧
     `cpq_packaging_match.match_box_types()`，`suggested_box_type` / `needs_new_tooling` /
     `new_tooling_reason` / 每个候选的 `total_score` 与 `data_gaps` 逐字段相同（Spec C6）；
4. `tech_app/tools/kb_deploy_preflight.py --env production` 在 34 现状下报
   `box_type_missing_fit_clearance`（两条 DWG 盒型），并把它与 `authority_missing` 分开列。

# 包装零件 ↔ 库内零件/工艺路线的受约束匹配

血缘：`docs/specs/packaging-dwg-parts-extraction.md`（C7 行级临时口径）、
`docs/specs/packaging-part-role-manual-mapping.md`（正式对应表另批）、
`docs/specs/packaging-parts-downstream-process-and-cost.md`（单件工序输入）；
数据事实来自 2026-10-09 对 `master_data.md_clm_process_routing_base_info` /
`md_clm_process_routing_operation` 的只读复核。

状态：Spec + 红测（已实现）（原状：Spec 与红测先行；业务实现由 Codex 落地，红测
`tests/test_packaging_part_route_constrained_match_red.py` 44 项全绿）
红测：`tests/test_packaging_part_route_constrained_match_red.py`

## 0. 一句话目标

把「图纸上这一件 ↔ 库内哪一件 ↔ 它该走哪条工序」这一步，从"精确逐字名"升级成
**受约束的匹配**：先按编码/逐字名，再按归一化名，最后才允许受硬约束的相似度；匹不上
必须显式 `unbound` 并说明原因，**任何情况下都不许静默取最高分**。

## 1. 现状缺口（代码事实 + 10-9 只读实测）

- 今天唯一的匹配是**逐字名**：`tech_app/backend/services/da_process_routing.py::for_row()`
  把 `row["name"]` 直接交给桥接查询，`cpq_process_routing.lookup()` 命中口径是
  `name = <零件名>工艺路线`（末尾自动补后缀）或 `product_item_code` 精确相等；注释明写
  "Names are exact business names, never fuzzy cross-industry matches"。库里没有零件级
  相似度召回，`key_process` / `kb_packaging_process_template.part_code` 也无人读。
- DA 里其实**已经有逐件路线**：`md_clm_process_routing_base_info` 27 行，26 条是
  `data_source='yutong_wine_box_quote'`、`name='<零件名>工艺路线'`、
  `product_item_code='YT-JW-XR21-700ML-NN'`（NN = 酒盒报价资料序号 01–26）。
- 用图纸上 28 件名去匹：**15 件逐字命中，13 件未中**，而未中全部可解释 ——
  5 件是图上写「忖纸」库里写「衬纸」，6 件是图上写「左盒/右盒」库里写「左盖/右盖」，
  2 件是外购件（顶托EVA、磁铁，本来就没有工艺）。即 26 个制造件库里**全都有**，
  缺的是归一化与显式兜底，不是数据。
- 名字相近 ≠ 同一件，实测反例：`底板`(09) 是 `开料 → 模切`（2 道）；
  `底板面纸`(11) 是 `开料 → UV印刷 → 覆哑膜 → 丝印UV → 浮雕击凸 → 模切 → 包盒`（7 道）。
  两个名字共享「底板」，纯文本相似度会高分给错件，而且错得看不出来。
- 别的行业的"相似度"不是文本相似度，是**结构化属性打分**
  （`tech_app/backend/storage/kb_repo.py::recommend_routes()`：
  `applicable_category` +0.5 / `applicable_material` +0.4 / 批量区间 +0.1）。
  包装这两张 DA 表没有这些可打分列：27 行里 `md_bpart_product_base_info_id`
  只有 1 行非空，26 条酒盒路线全空 —— 没有可打分的属性，只有可对上的键。
- 该批是 `packaging-dwg-parts-extraction.md` §C7 / `packaging-part-role-manual-mapping.md`
  记为"要业务签字、另立一批"的那个"正式对应表"的**可执行前置**：本批只做
  "匹得上/匹不上"的判定与留痕，不产生任何新的绑定写入。

## 2. 契约

### 2.1 `tech_app/backend/services/packaging_part_route_match.py` 常量（逐字）
- `ENGINE_VERSION = "packaging-part-route-match/1"`
- `MATCH_METHODS = ("exact_code", "exact_name", "normalized_name", "constrained_similarity", "unbound")`
- `BINDING_STATUSES = ("matched", "unbound", "skipped_external")`
- `UNBOUND_REASONS = ("no_candidate", "position_conflict", "kind_conflict", "below_threshold", "ambiguous", "duplicate_name")`
- `EXTERNAL_WORDS = ("外购", "采购")`
- `NAME_ALIASES = (("忖纸", "衬纸"), ("左盒", "左盖"), ("右盒", "右盖"))`
- `ROUTE_SUFFIXES = ("工艺路线", "加工工艺路线")`
- `DIRECTION_PAIRS = (("左", "右"), ("上", "下"), ("前", "后"), ("顶托", "底托"), ("内盒", "外盒"))`
- `KIND_WORDS = ("面纸", "衬纸", "灰板", "内卡", "贴牌", "EVA", "磁铁", "衬板", "盒背", "标牌")`
- `SIMILARITY_THRESHOLD = 0.5`

### 2.2 归一化 `normalize_part_name(name) -> str`
按固定顺序（幂等、纯函数）：去掉首尾空白 → 全角转半角 → 去掉所有空白 →
去掉 `ROUTE_SUFFIXES` 里任意后缀（若去完为空则保留原串）→ 依次套用 `NAME_ALIASES`。
例：`内盒3忖纸工艺路线` → `内盒3衬纸`；`左盒外盒里层灰板1工艺路线` → `左盖外盒里层灰板1`。

### 2.3 身份与冲突
- `direction_conflict(a, b) -> str`：`DIRECTION_PAIRS` 里任意一对，若一侧含 X、更一侧含 Y
  且 X≠Y，返回该对（如 `"左/右"`）；否则 `""`。
- `index_tokens(name) -> tuple`：`re.findall(r"(?:内盒|灰板|衬板|托|盒|层)\s*([0-9]+)")` 的去重升序元组。
  `index_conflict(a, b) -> str`：两侧都非空且不相等时返回 `"index"`。
- `kind_tokens(name) -> frozenset`：`KIND_WORDS` 里出现在名字中的词。
  `kind_conflict(a, b) -> str`：两侧 `kind_tokens` **不相等**时返回 `"kind"`（空集对非空集也算冲突
  —— 这正是 `底板` vs `底板面纸` 的判据）。

### 2.4 判定顺序 `match_one(part, candidates) -> dict`
`part` 至少含 `name`（可选 `product_item_code`）；`candidates` 是**调用方给定的候选**，
每项至少含 `name`（可选 `code`）。判定顺序**固定**，命中即返回：
1. `exact_code`：`part["product_item_code"]` 非空且与某个候选 `code` 逐字相等；
2. `exact_name`：`part["name"]` 与候选 `name` 逐字相等；
3. `normalized_name`：`normalize_part_name()` 后相等（**多个候选同命中 → `ambiguous`**）；
4. `constrained_similarity`：候选先过三道硬约束（`direction_conflict` / `index_conflict` /
   `kind_conflict` 任一非空即淘汰），剩下的算 `difflib.SequenceMatcher` 比值；取唯一最高且
   ≥ `SIMILARITY_THRESHOLD` 者为命中；并列最高 → `ambiguous`；最高 < 阈值 → `below_threshold`；
   全被硬约束淘汰 → 记下首个冲突原因。
5. 都不成立 → `unbound`。

返回逐字为：
`{"status": "matched"|"unbound"|"skipped_external", "match_method": <MATCH_METHODS 之一>,
  "part_code": "", "part_name": <原文>, "matched_code": "", "matched_name": "",
  "normalized": <归一化名>, "score": 0.0, "reason": <UNBOUND_REASONS 之一或 "">,
  "candidates_total": <int>, "rejected": [{"name": str, "reason": str}, ...]}`
- `matched` 时 `matched_code` / `matched_name` 为候选原值；`score`：`exact_*`/`normalized_name`
  记 `1.0`，`constrained_similarity` 记比值（四舍五入 4 位）。
- `part` 命中 `EXTERNAL_WORDS`（`name` 或 `reference.process_text` / `authority.process_text` 含
  「外购」「采购」）→ `status="skipped_external"`、`match_method="unbound"`、`reason=""`，
  **不参与任何匹配**（外购件没有工艺）。

### 2.5 `match_parts(parts, candidates) -> dict`
返 `{"engine_version", "bindings": [...按入参顺序...], "matched", "unbound", "skipped_external",
"unbound_parts": [{"part_code","part_name","reason"}], "duplicate_targets": [{"matched_code",
"matched_name","part_codes"}]}`
- 同一候选被多件命中 → 记进 `duplicate_targets`（**披露，不拒绝**：左右件本就可能同工艺）。
- 候选空间由调用方给定，本函数**绝不跨盒型/跨行业自取候选**。

### 2.6 接线
- `tech_app/backend/services/da_process_routing.py::for_row(row)`：
  - 外购/采购件（`reference.process_text` 或 `authority.process_text` 含 `EXTERNAL_WORDS`）→ 直接
    返回 `{"status": "external", "routes": [], "match_method": "unbound", ...}`，**不调桥接**；
  - 逐字名查询未命中（`not_found` / `missing_identity`）且 `normalize_part_name(name)` 与原名不同
    时，用归一化名**重试一次**，并在返回载荷里加 `match_method="normalized_name"`、
    `normalized_from=<原原文>`、`matched_name=<归一化名>`；逐字命中记 `match_method="exact_name"`；
    两次都不中记 `match_method="unbound"`、`reason` 取桥接的 `status`。
  - 既有键（`status` / `routes` / `notice`）与 `grounding()` 文本结构不变，只做加法。
- `tech_app/backend/services/packaging_process_instances.py::collect()` 的"外购/采购跳过"
  改引用同一处 `EXTERNAL_WORDS` / `is_external_part()`，口径只允许有一个。

### 2.7 禁止
- 不做"一件对多行/多件同角色"的正式对应表写入；不改 `bind_rows` 的行位置↔面积配对；
- 不新增第三方依赖（相似度只用标准库 `difflib`）；
- 不跨盒型/跨行业召回；不把 `ambiguous` 静默降级成"取最高分"；
- 不写库、不连生产、不改任何既有测试。

## 3. 验收
- `./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_part_route_constrained_match_red`
- 真实样本（离线夹具）：图上 28 件 × DA 26 条路线名 → 26 个制造件全部 `matched`
  （15 条 `exact_name` + 11 条 `normalized_name`），2 件外购 `skipped_external`，0 条静默错配。
- 反例守卫：`底板` 不得被相似度配到 `底板面纸`；`左盖面纸` 不得配 `右盖面纸`；
  `内盒1灰板` 不得配 `内盒2灰板`。
- 不回归：`test_packaging_business_part_process_by_authority_route_red`、
  `test_packaging_parts_extraction_red`、`test_packaging_drawing_flow_red`、
  `test_packaging_cost_engine_red`、`test_packaging_cost_red_closure_red`。

## 4. 未做 / 下一批
- 不把 `key_process` 接进单件推荐 grounding（要业务确认"哪一件对哪一件"之后再做）；
- 不做 `kb_packaging_process_template.part_code` 的按件取工序（同因）；
- 不做前端展示（本批只出判定与留痕）。

## 5. 实现记录（Codex，2026-10-09）

- 新增 `tech_app/backend/services/packaging_part_route_match.py`：§2.1 常量逐字、§2.2
  `normalize_part_name`（去空白 → 全角转半角 → 去空白 → 去后缀 → 套别名）、§2.3
  `direction_conflict` / `index_conflict` / `kind_conflict`、§2.4 `match_one`、§2.5
  `match_parts`。相似度只用标准库 `difflib`，无新依赖。
- §2.6 接线：`da_process_routing.for_row()` 加外购/采购短路（`status="external"`、不调
  桥接）与归一化名重试一次（命中加 `match_method` / `normalized_from` / `matched_name`；
  既有 `status` / `routes` / `notice` 与 `grounding()` 结构不动）；`packaging_process_instances.collect()`
  的外购跳过改引用同一处 `is_external_part`，外购口径只留一处。
- 红测 44 项全绿。真实样本夹具（图上 28 件 × DA 26 条酒盒路线名）→ 26 `matched`
  （15 `exact_name` + 11 `normalized_name`，即 5 件「忖纸」→「衬纸」、6 件「左盒/右盒」→
  「左盖/右盖」）+ 2 `skipped_external`（顶托EVA、磁铁）+ 0 `unbound`。
- 反例守卫拦下：`底板` 不配 `底板面纸`（`kind_conflict`）、`左盖面纸` 不配 `右盖面纸`
  （`position_conflict`）、`内盒1灰板` 不配 `内盒2灰板`（`position_conflict`）。
- 不回归（本机实跑）：`test_packaging_business_part_process_by_authority_route_red`、
  `test_packaging_drawing_flow_red`、`test_packaging_cost_engine_red`、
  `test_packaging_cost_red_closure_red`、`test_da_process_routing_live_red`、
  `test_packaging_unified_route_cost_red`。

# 规格：成本单的输入版本要"算时记下"，不是"读时现取"

状态：Spec + 红测（已实现）（算时记下路线版本 + BOM 指纹，读时只读存的并报 `stale` / `stale_reasons`；落地见 §5）
红测：`tests/test_packaging_cost_input_version_pinning_red.py`

血缘：承接 `packaging-dwg-parts-extraction.md` §6.1（"BOM / 成本必须带出它是基于哪一版算的"）、
`packaging-box-type-matching.md`（`load_box_match()` 已有 `stale` / `stale_reasons` 的现成范式：
比对**存的快照**与**当前输入**）、
`packaging-bom-parts-version-binding.md`（同一条纪律的零件侧）、
`packaging-cost-readiness.md`（`formal` / `provisional` 裁决，本批不动）。

## 0. 一句话目标

一份成本测算必须能回答：**算它的那一刻**，BOM 是哪一版、路线是哪一版；以及**现在**有没有变。
今天这三个问题的答案分别是"没记"、"读的时候现取"、"从来不比"。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

### 1.1 `source_versions` 是读的时候现取的，不是算的时候记下的

`tech_app/backend/services/packaging_cost.py:2278 load_cost()`：

```python
    row = da_repo.load_packaging_cost(project_id, req_no, _text(scenario))
    upstream = _upstream_route_version(project_id, req_no)
    ...
    result = _rehydrate_with_readiness(_rehydrate(row, items))
    # 上游版本的埋点（DWG 第 5 批 Spec §6.1）：成本必须带出它照着哪一版确认路线算的。
    result["source_versions"] = {"route_version": upstream, "engine_version": ENGINE_VERSION}
```

`upstream` 来自 `:2301 _upstream_route_version()` → `packaging_route.route_versions()` —— 是**读接口那一刻**的
路线版本，不是这份成本当时算的那一版。路线重确认一次，同一份旧成本单读出来的
`source_versions.route_version` 就跟着变：这个字段名为"照着哪一版算的"，实际是"现在哪一版"。

### 1.2 成本表没有来源列，`_rehydrate()` 也不返回来源 —— 落库都存不下

- `tech_app/backend/storage/da_repo.py:903 _PACKAGING_COST_COLUMNS` 里没有 `source_versions` /
  `bom_hash` 之类的列，`save_packaging_cost()`（`:919`）只按这个清单从 `estimate` 取值，
  多出来的键**直接丢弃**；
- `packaging_cost.py:2235 _rehydrate()` 返回体里也没有任何来源字段；
- 成本估算表 `wip_packaging_cost_estimate` 的加列清单（`tech_app/backend/storage/da_db.py:30 _ADDED_COLUMNS`）
  里同样没有它。

所以今天不是"读的时候丢了"，是**从来就没存下来过**。

### 1.3 成本完全建立在 BOM 上，却从不比对 BOM

`packaging_cost.py:1866`：

```python
    bom_rows = [dict(row) for row in da_repo.load_packaging_bom(project_id, req_no)]
```

成本逐行算的材料/工费都吃这些行。BOM 一重建（新一版图纸解析回填、人工改尺寸/材料、锁定行变化），
旧成本单照旧 `built=true`、照旧带自己的 `readiness.verdict`，**没有任何"输入已经变了"的标记** ——
用户看到的是一份看起来完全有效的报价底稿。对照同仓 `packaging_match.py:652 load_box_match()`：
那里已经用 `stale` / `stale_reasons` 把"输入变了"报出来了（`_record_input_snapshot` vs `_requirement_inputs`），
成本这一侧一个都没有。

### 1.4 分不出"没算过"与"算过但没来源"

`load_cost()` 的两条路径（`:2284-2293` 未算过、`:2295-2298` 算过）都把 `source_versions` 写成
同一对现取值 —— 从返回体上分不出"这份成本是照哪一版算的"还是"还没算"。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/storage/da_schema.sql` + `tech_app/backend/storage/da_db.py:30 _ADDED_COLUMNS`
   - `wip_packaging_cost_estimate` **新增列** `source_versions_json TEXT`：新库由 schema 给，
     老库靠既有幂等补列 `_add_missing_columns()`（`da_db.py:168`）补 —— 只加列，**不许**改类型、
     不许删列、不许重建表。
2. `tech_app/backend/services/packaging_cost.py`
   - 新增模块级纯函数 `bom_input_hash(rows) -> str`：对 BOM 行的**规范形**做哈希
     （范式照 `packaging_parts._record_hash`：`packaging_semantics.model` 的
     `sha256_hex(canonical_json(json_safe(...)))`）；输入前必须**按稳定键排序**
     （行序无关：同一批行换个顺序必须是同一个 hash），空输入给 `""`（"没有 BOM"不是一版内容）。
   - `compute_project()`：结果**新增** `source_versions`，在**算的那一刻**写入四项：
     `route_version`（当前确认路线版本）、`engine_version`（仍是 `ENGINE_VERSION`）、
     `bom_hash`（`bom_input_hash(bom_rows)`）、`bom_item_total`（`len(bom_rows)`）。
   - `load_cost()`：
     - `source_versions` = **落库的那一份**（`source_versions_json` 解析后原样返回）；
       读不到 / 是历史成本单 → 给 `{}`（**不许**用 `_upstream_route_version()` 现取值兜上去）；
     - **新增** `stale`（bool）+ `stale_reasons`（list[str]，固定顺序、去重）：
       - `bom_rebuilt`：当前 BOM 的 `bom_input_hash()` ≠ 存的 `bom_hash`；
       - `route_reconfirmed`：当前确认路线版本 ≠ 存的 `route_version`；
       - `provenance_missing`：这份成本单没有 `source_versions_json`（本批之前算的）；
       - `stale = bool(stale_reasons)`；`stale_reasons` **必须说清是哪一项变了**，不许折成一个布尔；
     - **新增** `bom_unavailable`（键**必须存在**，正常给 `{}`）：当前 BOM 读不到
       （抛异常 / 返回空）时给 `{"code": "bom_unavailable", "reason": "<异常类名或空>"}`，
       此时**不许**给 `bom_rebuilt` —— "比较不了" ≠ "变了"；
     - **未算过**（`row` 为空）的路径：除新增键外逐字不变，且 `stale` 必须是 `false`、
       `stale_reasons` 必须是 `[]`、**不许**给 `provenance_missing`（"还没算"不是"过期"）。
3. `tech_app/backend/storage/da_repo.py`
   - `save_packaging_cost()`：把 `estimate["source_versions"]` 序列化进 `source_versions_json`
     落库（写法与 `gaps_json` / `assumptions_json` 一致）；`load_packaging_cost()` 读回来。
4. `tech_app/backend/main.py`：成本读路由原样带出 `stale` / `stale_reasons` /
   `bom_unavailable` / `source_versions`（键名不变、不改路由形状）。
5. 前端（成本页）：`stale` 为真时显示"输入已变（BOM / 工艺路线），请重新测算"，
   并列出 `stale_reasons` 对应的人话；**不许**继续把这份成本显示成"当前有效"。

## 3. 禁止事项

- 不许把 `stale` 变成拒绝：成本照旧读得出来、金额照旧返回（本批只要求**标记**）。
- 不许在读接口里顺手重算成本（重算是 `build_cost()` 的动作）。
- 不许把 `stale_reasons` 折成一个布尔、也不许只报"变了"不说哪一项变了。
- 不许用现取的路线版本兜 `route_version` —— "记不下来"就给 `{}` 并报 `provenance_missing`，
  绝不编一个"看起来对"的版本（`_upstream_route_version()` 在成本**计算**那一刻仍可用）。
- 不许把"比较不了"（BOM 读不到）当成"变了"或"没变"。
- 不许改既有键与裁决：`built` / 24 类别 / `report_groups` / `items` / `gaps` / `readiness` /
  `source_versions.engine_version` 的口径逐字不变。
- 不许删改已有成本行与明细；本批只**加列**。
- 不许改 `tests/` 下任何既有文件（含 `test_packaging_cost_engine_red.py` 与
  `test_packaging_match_undecidable_and_size_guard_red.py`）。
- 不许连线上 PG / SQLite 生产库跑测试、不许发 HTTP、不许写业务数据；本批红测全部离线。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_input_version_pinning_red -v
# H 组（7 条）：
#   H1 存的是旧 BOM 指纹 + 当前 BOM 已变 → stale=true、reason 含 bom_rebuilt、
#      source_versions.bom_hash 必须是**存的**那一个
#   H2 存的 route_version 与当前不同 → reason 含 route_reconfirmed，
#      且 source_versions.route_version 是**存的**那个（不是现取的）
#   H3 历史成本单没有来源 → reason 含 provenance_missing、source_versions 给 {}
#   H4 当前 BOM 读不到 → bom_unavailable 非空、不许给 bom_rebuilt
#   H5 bom_input_hash 存在、行序无关、内容不同值不同、空输入给 ""
#   H6 既有键逐字不变（built / total_cost / engine_version / items / readiness）（护栏）
#   H7 没算过的路径逐字不变且不许报 provenance_missing（护栏）
# 现状：H1 H2 H3 H4 H5 红（5 条），H6 H7 绿（2 条护栏）
# 不回归（成本引擎既有口径与本批相邻批次）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_rule_snapshot_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_size_quality_accounting_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_parts_version_binding_red
```

### 锚点现状说明（不是本批的红）

本批跑 `tests.test_packaging_cost_engine_red` 时它是 `Ran 81` **1 failure**：
`test_j6_write_roles_reuse_batch4` 断言 `COST_WRITE_ROLES is packaging_match.BOX_MATCH_DECIDE_ROLES`，
而当前 `COST_WRITE_ROLES` 多了一个 `finance_manager`（并行会话在改财务权限那一批）。
这条**与本批无关**，实现方不要为它改 `tests/`；但它会让"不回归"读数不好看，
提交前应由财务权限那一批的 owner 决定是改常量还是改那条断言。

（2026-09-22 复核更新：财务权限那一批已收口，`test_packaging_cost_engine_red` 现为 `Ran 81` **OK**；
上面那段保留为当时的事实记录。）

真机复验（实现方做完、且部署后）：

```
POST /api/projects/{pid}/requirement/packaging-cost            # 测算一次 → 落 source_versions_json
POST /api/projects/{pid}/requirement/packaging-bom             # 重建 BOM
GET  /api/projects/{pid}/requirement/packaging-cost            # stale=true、stale_reasons 含 bom_rebuilt
```

## 5 落地状态（2026-09-22，实现方 Codex）

实跑：`./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_input_version_pinning_red`
→ **Ran 7 tests … OK**（H1–H5 五条红转绿，H6/H7 两条护栏仍绿）。

- `da_schema.sql`：`wip_packaging_cost_estimate` 新增列 `source_versions_json TEXT`（只加列）；
  `da_db._ADDED_COLUMNS` 新增 `("wip_packaging_cost_estimate", "source_versions_json", "TEXT")`
  —— 老库靠既有幂等补列补得上。
- `da_repo.save_packaging_cost()`：把 `estimate["source_versions"]` 用 `_box_match_json()` 序列化进
  `source_versions_json`（写法与 `gaps_json` / `assumptions_json` 一致）；`load_packaging_cost()`
  走 `SELECT *`，新列原样读回。
- `packaging_cost.bom_input_hash(rows)`（新纯函数）：范式照 `packaging_parts._record_hash`
  （`sha256_hex(canonical_json(json_safe(...)))`），**先按稳定键排序**（行序无关），空输入给 `""`
  （"没有 BOM"不是一版内容）。
- `packaging_cost.compute_project()`：返回体新增 `source_versions`，**在算的那一刻**写四项
  （`route_version` = 当时确认路线版本、`engine_version`、`bom_hash`、`bom_item_total`）。
- `packaging_cost.load_cost()`：`source_versions` = **落库的那一份**（`_loads(source_versions_json)`），
  不再用现值覆盖；新增 `_input_drift()` 只做比对：
  - `provenance_missing`（存的为空）、`route_reconfirmed`（路线版本对不上）、
    `bom_rebuilt`（BOM 指纹对不上）；
  - `stale = bool(stale_reasons)`；`stale_reasons` 逐项说清哪一项变了（不折成布尔）；
  - `bom_unavailable`（键**必须存在**，正常 `{}`）：BOM 读不到（抛异常 / 返回空）或存的没有
    指纹 → `{"code": "bom_unavailable", "reason": <异常类名或空>}`，此时**不给** `bom_rebuilt`；
  - **未算过**的路径：除上述新键外逐字不变，`stale=false`、`stale_reasons=[]`、
    **不报** `provenance_missing`（"还没算"不是"过期"）。
  成本**照旧读得出来**、金额照旧返回 —— 本批只加标记，不做拒绝、不在读接口重算。
- `main.py`：`GET …/requirement/packaging-cost` 原样带出新键（`{"cost": record}` 形状未动）。
- 前端 `requirement-confirm.js`（成本面板实际所在文件 `pcPanel()`）：`data-pc-stale` 横幅
  "输入已变（BOM / 工艺路线），请重新测算后再用这份成本"+ 逐条人话；
  `data-pc-bom-unavailable` 把"比较不了"与"变了"分开说。`node --check` 通过。

### 已记录的边界

- Spec §2 第 5 条写"前端（成本页）"，未点名文件；成本面板实际在
  `tech_app/frontend/requirement-confirm.js`（`#packagingCostPanel` / `pcPanel()`）里 ——
  按意图改在真正承载面板的文件上（与 `## 341` / `## 345` 同一条已记边界）。

未改任何测试、未放宽任何断言、未连 34、未写生产数据、未 push / MR / tag / Release / 未部署。

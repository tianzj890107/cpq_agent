# 规格：单件工艺 / 成本结论必须带上业务部件身份（`## 368` §12 边界 2 的收尾）

依赖：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（§12 边界 2：「单件工艺 / 成本
目前仍从几何零件入口发起；业务部件行的『单件工艺 / 成本』尚未按 `business_parts_id` 落到下游任务表」）、
`docs/specs/packaging-business-part-downstream-entry.md`（§6 边界 1：「业务部件身份到任务表的写入
（`business_parts_id` 落任务行）仍是下一批」）、
`docs/specs/packaging-parts-conclusion-version-readback.md`（§2.2 的三值纪律：比较不了 ≠ 过期）、
`docs/specs/packaging-business-parts-version-pinning.md`（§2.1 的业务版本漂移原因码）、
`docs/specs/packaging-silent-degradation-disclosure.md`（说不出"这份结论照哪一版业务清单算的"
就是静默降级）。

状态：Spec + 红测（已实现）（现状：单件工艺 / 成本结论行（`packaging_parts.DOC_KEY_PROCESS` /
`DOC_KEY_COST`）只有**几何**身份 —— `main.py` 的 `packaging_part_process()` / `packaging_part_cost()`
两条 POST 路由把 `parts_id` 落进结论行（`main.py:8424` / `:8557`），读侧 `_packaging_part_conclusion_version()`
（`:8579`）也只比对 `parts_id`；而业务部件文档的 `business_parts_id/hash`
（`packaging_parts.save_business_parts()` `:3619`）与几何行的对应关系（`geometry_binding.component_ids`
↔ 零件行 `component_id`）**一处都没进结论行** —— 于是「同一版几何、换了一版业务部件清单」时，
上一版清单算出来的单件结论照旧以"当前有效"的样子显示，谁也说不出来它是照哪一版业务清单算的。
另：`packaging_parts.business_binding_stale_reason()`（`:3772`，三值 `business_parts_unknown` /
`business_parts_reimported` / `""`）**已存在但全仓无人调用** —— 判据写好了，接线没做）
红测：`tests/test_packaging_part_conclusion_business_identity_red.py`
行号基线：HEAD `df6ea49`

## 0. 一句话目标

跑完单件工艺 / 成本之后，这份结论**说得出来**它是照哪一版业务部件清单、哪一件业务部件算的；
业务清单重新导入时，它能标出"这是上一版清单算的"，而不是伪装成当前结果。

## 1. 现状缺口（代码级）

1. **业务 → 几何的映射不存在**：前端有 `packagingBusinessPartDownstreamTarget()`（业务行 → 几何件），
   后端**没有**反向的那一半（几何件 → 业务件）—— `packaging_parts` 里没有这样的函数，
   `business_part_row()` 只能按**编码**取件，不能按 `component_id` 反查。
2. **结论行没有业务身份**：两条 POST 路由落的 payload（`main.py:8418-8426` / `:8551-8559`）只有
   `part_code` / `parts_id` / `engine_version` / 结论 / `source`，没有 `business_part_code`、
   没有 `business_parts_id` / `business_parts_hash`。
3. **读侧只披露几何版本**：`_packaging_part_conclusion_version()` 只回
   `parts_id` / `stale` / `stale_reason` 三个键，业务清单换版在返回体上**完全看不出来**；
   `business_binding_stale_reason()` 写好了却没人用。

## 2. 契约

### C1 新增顶层纯函数 `packaging_parts.geometry_business_part(row, business_doc) -> dict`

- 体内**不得**出现 `get_backend(` / `load_` / `open(` / `requests` / `put_doc(`（可被 AST / 源码
  直接验；本函数只吃两个已在内存里的东西）；
- 入参：一行几何零件（`component_id` / `geometry_component_ref`，两者都认）+ 一份业务部件文档
  （`business_parts[]` 与 `geometry_evidence` 那种形状；`None` / `{}` 都算"读不到"）。
- 返回**固定六键**：`{"business_part_code", "business_parts_id", "business_parts_hash", "mapped",
  "reason", "rule_id"}`；
- 组件引用集合 = 行上 `component_id` + `geometry_component_ref`（单个或数组都认），
  `String()` 化、`strip()`、去空、去重；业务文档读不到 → `{"mapped": False, "reason":
  "business_doc_unavailable"}`（**仍然**带上能读到的 `business_parts_id/hash`，读不到就是 `""`）；
- 引用集合为空 → `{"mapped": False, "reason": "geometry_ref_missing"}`；
- 业务行与引用命中判据：业务行的 `geometry_binding.component_ids` **或** `geometry_component_ref`
  与引用集合有交集；命中多件 → 取 `business_part_code` **升序第一个**（确定性，不按遍历顺序碰运气）；
- 命中不到 → `{"mapped": False, "reason": "geometry_unbound"}`；
- 命中 → `{"mapped": True, "business_part_code": <编码>, "reason": ""}`；
- `reason` 是**闭集**：`("", "business_doc_unavailable", "geometry_ref_missing", "geometry_unbound")`，
  以模块常量 `BUSINESS_PART_LOOKUP_REASONS` 暴露；
- `rule_id` 以模块常量 `BUSINESS_PART_LOOKUP_RULE_ID = "business_part_lookup_by_component_v1"` 暴露；
- 任何一级**都不抛错**：`row` / `business_doc` 不是 dict、`business_parts` 不是 list、
  行里字段是数字 → 都按"没有"处理。

### C2 写侧：结论行带业务身份

- `main.py` 的 `packaging_part_process()` 与 `packaging_part_cost()` 落库前，用
  `packaging_parts.business_identity_for_row(pid, row)` 取三键，**并进** payload
  （`business_part_code` / `business_parts_id` / `business_parts_hash`）；
- `business_identity_for_row(project_id, row)`：`load_business_parts(project_id)` + C1 的纯函数，
  只回这三键（读不到清单 → 三键全 `""`，`business_part_code` 也为 `""`），**不**写库、不抛错；
- 既有键（`part_code` / `parts_id` / `engine_version` / 结论 / `source`）一个字不改；
- 三键参与 `_save_part_doc()` 的内容指纹（`record_hash`）—— 换了一版业务清单后重跑的结论
  **不再被判成"同一份"**（幂等只对**输入完全相同**的重跑成立）。

### C3 读侧：结论回显业务身份并披露业务版本漂移

- `_packaging_part_conclusion_version(pid, record)` 在既有三键之外新增**四个键**：
  `business_part_code`、`business_parts_id`、`business_stale`、`business_stale_reason`；
- 判据**只有一处**：`packaging_parts.business_binding_stale_reason(stored, current)`
  （本批**必须**开始用它，不许在路由里另写一套比较）；
- 三值纪律与 `parts` 那一格逐字同口径：只有 `business_parts_reimported` 算 `business_stale: True`；
  `business_parts_unknown`（存的为空 / 当前清单读不到）→ `business_stale: False`
  （"比较不了 ≠ 过期"）；
- 空态（没跑过）也必须给这四个键：`""` / `""` / `False` / `""`。

### C4 冻结面

- 不改 `parts_stale_reason()`、不改 `_save_part_doc()` 的分段键 `(part_code, parts_id)`、
  不改 `MAX_VERSIONS`；
- 不改 `packaging_bom` / `packaging_cost` 的业务清单 scope 与 `size_source`；
- 不改 `bind_geometry()` / `business_parts_document()` 的产出结构；
- 不加任何新路由（结论仍只由既有两条 POST 写、两条 GET 读）；
- 不改前端（面板显示业务身份另提）；不改 `tests/` 下任何文件。

### C5 依赖注入面

- `packaging_parts` 是**模块级**导入 `get_backend`；本批新函数一律走它已有的
  `get_backend()` / `load_business_parts()`，不新增全局状态、不新增环境变量。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_parts.py`：新增 `BUSINESS_PART_LOOKUP_REASONS` /
   `BUSINESS_PART_LOOKUP_RULE_ID` / `geometry_business_part()` / `business_identity_for_row()`；
2. `tech_app/backend/main.py`：两条 POST 的 payload 加三键；`_packaging_part_conclusion_version()`
   加四个键；
3. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许用编码前缀 / 尺寸 / 名字**猜**业务件（只认 `geometry_binding` 的组件引用命中）；
- 不许把 `business_parts_unknown` 说成"过期"，也不许把"上一版清单算的"说成"当前有效"；
- 不许自动重算、不许自动改结论行、不许删旧结论；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_part_conclusion_business_identity_red -v
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_conclusion_version_readback_red \
  tests.test_packaging_parts_downstream_readback_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_business_parts_binding_size_source_red \
  tests.test_packaging_business_parts_version_pinning_red \
  tests.test_packaging_business_part_downstream_entry_red \
  tests.test_packaging_parts_downstream_red
```

## 6. 已记录的边界

1. 本批只做**身份落行 + 回显**，不做业务部件级工艺 / 成本（真跑仍落在几何件那套算法上，
   `processability()` 也只覆盖几何零件）；
2. 前端面板**不**显示这四个新键（下一批）；本批的红测只验后端契约；
3. 业务清单换版**不触发**任何自动重算，只让旧结论说得清自己是哪一版的。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_part_conclusion_business_identity_red
# 实现前：Ran 24 tests … FAILED (failures=9, errors=8)   ← 17 红：A1–A8 / B1–B3 / B6 / C1–C2 / C4–C6
# 实现后：Ran 24 tests … OK                              ← B4 / B5 / C3 / D1–D4 七条护栏始终绿
```

| 契约 | 落点 |
| --- | --- |
| §C1 纯函数 | `tech_app/backend/services/packaging_parts.py:3820 geometry_business_part(row, business_doc)`：六键 `{business_part_code, business_parts_id, business_parts_hash, mapped, reason, rule_id}`；引用集合 = `_component_refs()`（`component_id` + `geometry_component_ref`，单个/数组、数字都 `_text()` 化去重）；清单读不到（空 rows）→ `business_doc_unavailable`；引用为空 → `geometry_ref_missing`；命不中 → `geometry_unbound`；多件命中按编码升序取首。纯读：体内无 `get_backend(` / `put_doc(` / `open(` / `requests` / `load_business_parts(` |
| §C1 常量 | `:3788 BUSINESS_PART_LOOKUP_REASONS`（四值闭集）、`:3792 BUSINESS_PART_LOOKUP_RULE_ID = "business_part_lookup_by_component_v1"`；命中判据只认 `_business_row_refs()`（`geometry_binding.component_ids` 兜底 `geometry_component_ref`）—— 不按编码前缀 / 尺寸 / 名字猜件 |
| §C2 写侧 | `packaging_parts.py:3847 business_identity_for_row(project_id, row)`（`load_business_parts()` + C1，只回三键，清单读不到三键全 `""`，不写库不抛错）；`main.py:8428`（工艺）与 `:8563`（成本）各一处 `**packaging_parts.business_identity_for_row(pid, row)` 并进结论 payload —— 既有 `part_code` / `parts_id` / `engine_version` / 结论 / `source` 一个字不改；三键进 `record_hash` 内容指纹，换版后重跑不再被判成"同一份" |
| §C3 读侧 | `main.py:8586 PACKAGING_PART_BUSINESS_STALE_REASON = "business_parts_reimported"`；`:8579 _packaging_part_conclusion_version()` 由三键扩到七键（新增 `business_part_code` / `business_parts_id` / `business_stale` / `business_stale_reason`），判据只有一处调用 `:8610 packaging_parts.business_binding_stale_reason()`（该函数此前全仓无人调用）；`business_parts_unknown` 一律 `business_stale: False`，空态四键齐给且全空 |
| §C4 冻结面 | `parts_stale_reason()` / `_save_part_doc()` 的分段键 `(part_code, parts_id)` / `MAX_VERSIONS` / `bind_geometry()` / `business_parts_document()` 产出键集合 / `packaging_bom`・`packaging_cost` 的业务 scope 全未动；两条 POST 与两条 GET 的路径常量逐字不变、无新路由；前端与 `tests/` 一行未改 |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_conclusion_version_readback_red \
  tests.test_packaging_parts_downstream_readback_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_business_parts_binding_size_source_red \
  tests.test_packaging_business_parts_version_pinning_red \
  tests.test_packaging_business_part_downstream_entry_red \
  tests.test_packaging_parts_downstream_red
  → Ran 103 … OK

./open-claude/.venv/bin/python -W ignore -m unittest discover -s tests -p 'test_packaging_*.py'
  → Ran 1901 … FAILED (failures=5, skipped=8)   ← 仍是那 5 条既有挂账
    （bom_part_size_provenance::B3、parse_to_downstream_seams::B4、
      part_role_mapping_reaches_card::A2、quote_send_recovery::C1、
      route_bom_version_pinning::F2），本批未引入新红
```

未改前端、未改 `tests/` 下任何文件、未连 PG / 34、未写生产数据、未 push / MR / tag / Release / 未部署。

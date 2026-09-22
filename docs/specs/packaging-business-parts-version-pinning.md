# 规格：权威清单的版本要「建 BOM 时固定、读的时候比对」

依赖：`docs/specs/packaging-business-parts-and-cad-plan-view.md` §7（下游按 `business_parts_id/hash` 判 stale）、
`docs/specs/packaging-bom-parts-version-binding.md`（零件轴已经这么做：行上留痕 + 读侧 `parts_binding_stale`）、
`docs/specs/packaging-cost-input-version-pinning.md`（成本「算时记下、读时比对」）、
`docs/specs/packaging-route-bom-version-pinning.md`（路线固定「排产照的那一版 BOM」）、
`docs/specs/packaging-silent-degradation-disclosure.md`（比较不了 ≠ 变了）。

状态：Spec + 红测（已实现）（原状：建 BOM 时未固定 `business_parts_id/hash`，读侧未做
「存的 vs 当前的」比对；红基见 §1；落地见 §7）
红测：`tests/test_packaging_business_parts_version_pinning_red.py`
行号基线：HEAD `2b18057`（行号只用来指路；口径以本 Spec 正文为准，不以行号为准）。

## 0. 一句话目标

「重新导入权威清单 / 改几何绑定都会换 `business_parts_id`」这句话，必须真的能被下游读出来：
BOM 行固定它**建的时候**照的那一版清单，BOM 读侧与成本读侧都比对「存的」与「当前的」，
而且三态两两可分 —— 读到了 / 确实没有 / 读不到。

## 1. 现状缺口（代码级，逐条可指到行）

### 1.1 行上没有版本：出处只到「哪张表哪一行」

`packaging_bom.py:436 business_part_rows()` 造出来的行只带
`size_source_json = {"kind": "authority_workbook", sheet, row}`（`packaging_bom.py:423 _authority_size_source()`）。
**没有清单版本**。对照零件轴：`packaging_parts.py:2878` 把 `parts_id` / `parts_hash` 写进了
`size_source_json.dwg_binding` —— 同一个文件、同一个位置，两条轴一个有一个没有。

### 1.2 读接口里的「存的那一版」其实是现取的

`packaging_bom.py:1174 load_bom()` 里的 `source_versions.business_parts_id/hash` 来自
`packaging_bom.py:1465 _business_parts_scope()`，而它是**读的时候**去读当前文档算出来的；
全文件没有 `business_parts_*_stale` 这类比对。对照零件轴：`packaging_bom.py:1417 _parts_binding_scope()`
给出 `parts_binding_stale` + `parts_document_unavailable`。

### 1.3 成本存了版本，却没有任何地方比

`packaging_cost.py:2427-2429` 已经把 `business_parts_id/hash` 落进 `source_versions_json`，
注释逐字写着「下游据此判 stale」；而 `packaging_cost.py:2629 _input_drift()` 只比
`route_version` 与 `bom_hash` —— 业务部件那条轴一个判断都没有。

### 1.4 后果（一个场景就够）

重新导入一份新的权威清单（件数 / 尺寸 / 材料变了）之后、**重建 BOM 与重算成本之前**：

- 读接口给你的 `business_parts_hash` 是**新**的；
- 行上的尺寸与材料是**旧**的；
- 两边都不说「这份 BOM / 这份成本是按上一版清单做的」—— 版本号「对得上」，反而更危险。

## 2. 要求（可验收）

### 2.1 BOM 行固定清单版本（键必须存在）

`business_part_rows(business_doc)` 的每一行，`size_source_json` 必须与 `kind/sheet/row` **并列**带上：

```json
{"kind": "authority_workbook", "sheet": "零部件排版工艺", "row": 4,
 "business_parts_id": "business-parts:...", "business_parts_hash": "..."}
```

- 逐字取 `business_doc` 顶层的 `business_parts_id` / `business_parts_hash`；
- 文档没给就写空串（**不许编**，不许拿时间、行号或当前清单顶）；
- 行数、`source` 取值、`item_key`、`material_code`、`bom_category` 一个字不改。

### 2.2 BOM 读侧比对（`load_bom()`）

- 新增 `business_parts_stale`（键**必须存在**；没有过期行时 `[]`），逐条：
  `{"item_key", "bound_business_parts_hash", "current_business_parts_hash", "reason"}`，按 `item_key` 升序；
- `reason` 闭集：`business_parts_reimported`（行上有版本、与当前不同）、
  `binding_without_version`（行来自权威清单但没有版本，历史行）；
- 判据**只认行上留痕**（`size_source_json`），不许在读接口里另算一套；
- 三态两两可分：
  - 读到了、对不上 → 报 `business_parts_reimported`；
  - 读到了、对得上 → `[]`；
  - 当前清单**读不到** → `[]`，且既有 `business_parts.gap.code == "business_parts_document_unavailable"`（比较不了 ≠ 变了）；
  - **还没有清单**（模板行）→ `[]`，既有 `gap.code == "business_parts_missing"`（行上本来就没有来源，不进列表）。

### 2.3 成本读侧比对（`load_cost()`）

- `stale_reasons` 必须能报 `business_parts_reimported`：存的 `business_parts_hash` 非空、且 ≠ 当前清单 hash；
- 存的**没有**版本（本批之前算的历史成本单）→ 不报 drift，改披露 `business_parts_version_missing`
  （「当时没记」≠「变了」）；
- 当前清单**读不到** → 不报 `business_parts_reimported`；顶层新增 `business_parts_unavailable`
  （键必须存在，正常给 `{}`），读挂时 `code == "business_parts_unavailable"` 且带 `reason`（异常类名）；
- 当前清单身份必须经 `packaging_parts.load_business_parts()` **一个入口**读（与 `packaging_cost.py:2475 _business_parts_scope()` 同源），
  不许绕 `da_repo`、不许另写第二套匹配。

### 2.4 加法与护栏（一个字都不许改）

- BOM 侧：`parts_binding_stale` / `parts_document_unavailable` / `business_rows` / `business_material_rows` /
  `role_unbound` / `source_versions` 既有七项 / `stats` 键集与 `size_quality` 三档；
- 成本侧：`stale` / `stale_reasons` 既有取值（`provenance_missing`、`route_reconfirmed`、`bom_rebuilt`）/
  `bom_unavailable` / `route_unavailable` / `source_versions` 既有键；
- **不新增数据库列**：版本落在 `size_source_json` 与 `source_versions_json` 里（`da_repo.py:731 _PACKAGING_BOM_COLUMNS` 一个字不动）；
- 前端 `tech_app/frontend/requirement-confirm.js`：2.2 面板要能显示这条新原因的中文
  （「按上一版业务部件清单建的」），不许把码直接甩给用户。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_bom.py`：`_authority_size_source()` / `business_part_rows()` /
   `_business_parts_scope()` / `load_bom()`
2. `tech_app/backend/services/packaging_cost.py`：`_input_drift()` + 一个与 `_route_probe()` 同形的清单探测
3. `tech_app/frontend/requirement-confirm.js`：一条文案
4. 本 Spec 与它的红测

## 4. 禁止事项

- 不许重算 / 重绑 / 改金额 / 改行数；不许删行、不许清值；
- 不许改 `tests/` 下任何既有文件（含本批红测）；不许放宽任何断言；
- 不许新增 schema 列、不许改 `kb_*`；不许连 PG / 34、不许写业务数据；不许 push / MR / tag / Release / 部署。

## 5. 红基与复跑

红基（2026-09-22，HEAD `2b18057`，只读跑）：本批红测 **Ran 13 tests … FAILED (failures=9)**
—— A1 / A2 / B1 / B2 / B3 / B4 / C1 / C3 / C4 红；A3 / B5 / C2 / C5 四条护栏绿
（每一条红的失败原因都必须能在 §1 里找到）。

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_version_pinning_red -v
./open-claude/.venv/bin/python -m unittest \
  tests.test_packaging_bom_business_parts_rows_red \
  tests.test_packaging_bom_business_material_rows_red \
  tests.test_packaging_cost_input_version_pinning_red \
  tests.test_packaging_authority_disclosure_on_read_red -v
```

## 6. 已记录的边界

- 本批只做「版本可见 + 漂移可判」，**不**自动重建 BOM、**不**自动重算成本（那是人的决定）；
- 权威清单的「材料原文 → 材料码」映射不在这里（由 `docs/specs/packaging-bom-business-material-rows.md` 那条线管）。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_parts_version_pinning_red
# 实现前：Ran 13 tests … FAILED (failures=9)   ← A1 A2 B1 B2 B3 B4 C1 C3 C4
# 实现后：Ran 13 tests … OK                    ← A3 B5 C2 C5 四条护栏始终绿
```

| 契约 | 落点 |
| --- | --- |
| §2.1 行上固定版本 | `packaging_bom.py` 新增 `AUTHORITY_SIZE_KIND = "authority_workbook"`；`_authority_size_source(authority, *, business_parts_id="", business_parts_hash="")` 在既有 `kind/sheet/row` 之后并列写 `business_parts_id` / `business_parts_hash`；出处本身拼不出（无表名 + 行号）仍给 `{}`，一个键都不加（A2 要求「文档没给版本不许编」→ 写空串）。`business_part_rows(business_doc)` 从文档顶层取 `business_parts_id` / `business_parts_hash` 逐字传下去，每行都固定住。 |
| §2.2 读侧比对 | `packaging_bom.py` 新增 `BUSINESS_PARTS_STALE_REASONS = ("business_parts_reimported", "binding_without_version")` 与 `_business_parts_stale_rows(items, current_hash)`：只认 `size_source_json.kind == AUTHORITY_SIZE_KIND` 的行；存的 hash 与当前 hash 不同 → `business_parts_reimported`；行上根本没版本 → `binding_without_version`；按 `item_key` 升序。`_business_parts_scope(project_id, items=None)` 新增 `stale` 键（读不到清单 / 没有清单 → `[]`，不当结论），`load_bom()` 新增 **`business_parts_stale`** 键并把它 `items` 传进 scope。 |
| §2.3 成本侧比对 | `packaging_cost.py` 新增 `_business_parts_probe(project_id) -> (hash, unavailable)`（只经 `packaging_parts.load_business_parts()`）与 `_business_parts_drift(project_id, stored) -> (reason, unavailable)`：存的没有版本 → `business_parts_version_missing`；读不到清单 → `business_parts_unavailable`；两者都不报 drift。`load_cost()` 把 drift 结果并入 `stale_reasons` / `stale`，并新增 **`business_parts_unavailable`** 键（`built=False` 早返回分支同样补上）。 |
| §2.4 前端文案 | `tech_app/frontend/requirement-confirm.js` `PC_STALE_REASONS` 加 `business_parts_reimported` → 「按上一版业务部件清单建的（权威清单重新导入过，请重建 BOM 后重算成本）」。 |
| §3 未动的 | 行形状 / 行数 / 金额 / `size_source_json` 其余键 / `source_versions` 既有键 / `stale_reasons` 既有取值（`provenance_missing` / `route_reconfirmed` / `bom_rebuilt`）/ `bom_unavailable` / `route_unavailable` 逐字不变；`da_repo.py:731 _PACKAGING_BOM_COLUMNS` 一个字不动（版本落在 JSON 列里）；未读不到就当「没过期」。 |

复跑（不回归；F2 是既有挂账，与本批无关，不许本批修）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_bom_business_parts_rows_red \
  tests.test_packaging_bom_business_material_rows_red \
  tests.test_packaging_cost_input_version_pinning_red \
  tests.test_packaging_authority_disclosure_on_read_red \
  tests.test_packaging_bom_parts_version_binding_red \
  tests.test_packaging_route_bom_version_pinning_red
# Ran 87 tests … FAILED (failures=1)   ← 唯一那条是既有挂账 route_bom_version_pinning_red::F2
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_parts_version_pinning_red \
  tests.test_packaging_cost_input_version_pinning_red \
  tests.test_packaging_cost_route_version_read_failure_red
# Ran 30 tests … OK
node --check tech_app/frontend/requirement-confirm.js   → OK
```

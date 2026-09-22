# 规格：业务部件没有几何时，材料费必须能按**权威尺寸**算出来

依赖：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（§12 边界 3 已声明「几何没绑定只影响
依赖几何的尺寸，不影响有权威尺寸的材料与采购项」—— 本批把这句话从"口径"变成"能算"）、
`docs/specs/packaging-parts-downstream-process-and-cost.md`（几何件那套下游入口与
`processability()` 的口径，本批**不新写第二套成本算法**）、
`docs/specs/packaging-business-part-downstream-entry.md`（§6 边界 2/3：没绑定的件今天**没有任何下游入口**）、
`docs/specs/packaging-silent-degradation-disclosure.md`（说不出"这份金额是按什么尺寸算的"就是静默降级）。

状态：Spec + 红测（已实现）（现状：`packaging_parts.processability()`（`:2700` 一带）第一道门槛是
`outline_status == "closed"`（或人签字的 `outline_confirmation`），也就是说**没有几何的件根本走不进
成本那一路**；`main.py` 的单件成本路由（`PACKAGING_PART_COST_PATH`）第一句就是
`processability(row)` → 409，而业务部件编码在零件文档里查不到 → 更是 404。真样本 28 件里 24 件
`unbound`，**这 24 件今天一个金额都算不出来**，尽管权威清单里长度 / 宽度 / 材料原文都写着。
另外：权威尺寸与几何轮廓本来就不该混算 —— 今天的结论里没有任何字段说得出来"这个金额是按
权威尺寸还是按几何轮廓算的"）
红测：`tests/test_packaging_business_part_cost_by_authority_size_red.py`
行号基线：HEAD `c2e0e36`

## 0. 一句话目标

一件**没有几何**的业务部件，只要权威清单里有长度 / 宽度 / 克重，就能跑出**材料开料**成本；
结论里明写"按权威尺寸算的、未与 CAD 几何核过"，缺什么就说什么，绝不用包围盒或默认克重硬算。

## 1. 现状缺口（代码级）

1. `packaging_parts.processability()` 先判 `outline_status != "closed"` → `PACKAGING_PART_NOT_CLOSED`；
   业务件没有 `outline_status`，永远过不去。
2. `main.py` 的 `packaging_part_cost()`（`POST .../requirement/packaging-parts/{code}/cost`）先
   `_packaging_part_row(pid, code)`（只认 `DWG-Pxx`）→ 业务编码 404；就算过了，第二句
   `processability()` 也会 409。
3. 业务件行上其实**什么都有**：`authority.length_mm` / `width_mm` / `material_text`
   （`BUSINESS_AUTHORITY_KEYS` `:3291`），只是没人把它送进 `packaging_cost.compute_line()`。
4. 读回体（`_packaging_part_conclusion_version()`）只说得出来"哪一版零件 / 哪一版业务清单"，
   **说不出来**这份金额用的是哪一套尺寸。

## 2. 契约

### C1 新增纯函数 `packaging_parts.business_cost_inputs(row, *, requirement=None, quantity=1) -> dict`

- 体内**不得**出现 `get_backend(` / `load_` / `open(` / `requests` / `put_doc(`（可被源码/AST 直接验）；
- 返回**固定十二键**：`{ok, code, message, missing_variables, part_code, name, material_text,
  gsm, variables, size_source, size_source_ref, size_text}`；
- `code` 闭集 = 常量 `BUSINESS_COST_REJECT_CODES = ("PACKAGING_BUSINESS_PART_NOT_FOUND",
  "PACKAGING_BUSINESS_PART_SIZE_UNKNOWN", "PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN")`
  （`ok` 时为 `""`）；`size_source` 闭集 = 常量 `BUSINESS_COST_SIZE_SOURCES =
  ("authority_dimensions",)`，成功时恒为 `"authority_dimensions"`；
- 判据顺序固定：
  1. `business_part_code` 去空后为空 → `PACKAGING_BUSINESS_PART_NOT_FOUND`
     （message：这一件没有业务部件编码，不能算材料费），`missing_variables: []`；
  2. 权威尺寸不过（`authority.length_mm` / `width_mm` 任一取不到，或 ≤ 0）→
     `PACKAGING_BUSINESS_PART_SIZE_UNKNOWN`，`missing_variables: ["authority_size"]`，
     message 要说清两条出路（在平面图里确认几何映射，或补录权威尺寸后再算）；
  3. 克重取不到 → `PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN`，`missing_variables: ["gsm"]`，
     message 点名"材料原文里没有克重"与需求整盒口径那条兜底；
  4. 否则 `ok: True`：`variables = {"cut_length": <length_mm>, "cut_width": <width_mm>,
     "gsm": <克重>, "quote_quantity": max(1, int(quantity))}`；`size_source_ref` 取权威行
     `source`（没有就 `""`）、`size_text` 取 `product_size_text`（原文，缺省 `""`）、
     `material_text` 取 `material_text`（原文）；
- 克重来源只有两处（顺序固定）：`authority.material_text` 里的 `<数字>g` 原文；兜底
  `requirement["data"]["face_paper_gsm"]`（与几何那一路同一口径）。**不许**给默认克重、
  **不许**读知识库猜材料；
- 任何输入都不抛错（非 dict 行 / 数组 / 字符串一律按"没有"处理）。

### C2 新增纯函数 `packaging_parts.business_cost_assumption(inputs, *, geometry_part_code="") -> str`

- 成功时逐字：`按权威尺寸（<L>×<W> mm）算的材料开料，未与 CAD 几何核过`
  （`<L>` / `<W>` 用 `inputs["variables"]` 的两轴，去掉多余小数位）；
- `geometry_part_code` 非空 → 追加 `；这一件另有闭合几何件（<code>），本结论有意按权威尺寸算`
  （**不**改前面的正文）；
- `inputs["ok"]` 非真 → 返回 `""`（没结论就没有口径那句话）；不抛错。

### C3 新路由 `POST /api/projects/{pid}/requirement/packaging-business-parts/{code}/cost`

- 写权限直接引用 `packaging_match.BOX_MATCH_DECIDE_ROLES`（与既有 R1 那几条同口径）；
- 取件：`load_business_parts(pid)` 读不到 / 那一件不在清单里 → **404** + `PACKAGING_BUSINESS_PART_NOT_FOUND`
  （message 说清"先导入权威清单再算"）；
- `business_cost_inputs(row, requirement=..., quantity=...)` 不过 → **409**（`retryable: False`）
  + `{code, message, missing_variables}`（形状与几何那一路的 `_packaging_part_reject()` 逐字同形）；
- 过了 → 与既有单件成本**同一个异步任务形状**（`tasks.submit(...)` + `task_id`），任务体里：
  - `packaging_cost.compute_line("material", inputs["variables"])`（**复用**库内公式与费率，
    一行都不新写）；
  - `lookup = {}`（业务件没有知识库检索依据，不装样子）；
  - 落一版 `save_part_cost(pid, {...})`：`part_code` = 业务编码、`parts_id` = **空串**
    （这份结论不是按几何零件算的，不许拿它冒充几何版本）、`engine_version`，
    `analysis` / `summary` / `source{task_id, computed_at, actor}` 与几何那一路同形，
    另加 `size_source` / `size_source_ref` / `size_text` / `business_part_code` /
    `business_parts_id` / `business_parts_hash` / `geometry`（`"unbound"`，或这一件在清单里绑了分量时的
    `"bound:<分量引用>"`），
    `assumptions` 第一条恒为 C2 那句话；
- 结论**不写**技术 IR（`store.save_ir()` 一行都不碰）。

### C4 新路由 `GET /api/projects/{pid}/requirement/packaging-business-parts/{code}/cost`

- 形状与几何那一路**逐字同形**（`part_code` / `analysis` / `summary` / `source`）+
  `_packaging_part_conclusion_version()` 的七键 + 新增 `size_source` / `size_source_ref` /
  `size_text` / `geometry`；
- 没跑过 → **200** 空态（不 404）；件不存在 / 清单读不到 → 404 + `PACKAGING_BUSINESS_PART_NOT_FOUND`；
- 纯读，不写库。

### C5 冻结面

- `processability()` 与 `PROCESS_REJECT_CODES` 一字不动；**不许**用几何那几条码（`PACKAGING_PART_*`）
  代替业务这三条；
- 既有两条几何路由（`PACKAGING_PART_PROCESS_PATH` / `PACKAGING_PART_COST_PATH`）的路径与行为不变；
- 不改 `packaging_cost`（公式 / 费率 / 最低收费口径）、不改 `packaging_bom` / 匹配算法；
- 不改前端（面板入口下一批）；不改 `tests/` 下任何文件。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_parts.py`：新增两个常量 + `business_cost_inputs()` +
   `business_cost_assumption()`；
2. `tech_app/backend/main.py`：新增两条业务件成本路由（POST / GET）+ 一个组装结论的小函数；
3. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许用几何轮廓 / 包围盒 / 默认克重 / 默认料厚去算这一件；
- 不许把业务件的成本结论写成几何件那一份（`parts_id` 必须为空、编码必须是业务编码）；
- 不许自动重算、不许自动改权威清单、不许连 PG / 34、不许写生产数据；
- 不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_part_cost_by_authority_size_red -v
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_downstream_red \
  tests.test_packaging_business_part_downstream_entry_red \
  tests.test_packaging_part_conclusion_business_identity_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_business_parts_binding_size_source_red \
  tests.test_packaging_cost_engine_red \
  tests.test_packaging_parts_conclusion_version_readback_red
```

## 6. 已记录的边界

1. 只做**材料开料**这一条公式（与几何那一路同一支）：工艺推荐、料厚相关的工序、采购项仍要几何，
   这些不在本批；
2. 面板入口（`openPackagingBusinessPart()` 那颗按钮）是下一批：本批只保证后端算得出来、
   读得回来、说得清口径；
3. 权威尺寸与几何轮廓**并存**时不算"谁对" —— 两条路各按自己的输入出数，结论里各说各的口径。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_part_cost_by_authority_size_red
# 实现前：Ran 25 tests … FAILED (failures=2, errors=19)   ← 21 红：A1–A10 / B1–B3 / C1–C5 / D1–D3
# 实现后：Ran 25 tests … OK                              ← E1–E4 四条护栏始终绿
```

| 契约 | 落点 |
| --- | --- |
| §C1 纯函数 | `tech_app/backend/services/packaging_parts.py:3888 business_cost_inputs(row, *, requirement=None, quantity=1)`：固定十二键；判据顺序 = 无 `business_part_code` → `PACKAGING_BUSINESS_PART_NOT_FOUND` → 权威长度/宽度缺或 ≤0 → `PACKAGING_BUSINESS_PART_SIZE_UNKNOWN` + `missing_variables: ["authority_size"]` → 克重取不到 → `PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN` + `["gsm"]` → `ok`；`variables = {cut_length, cut_width, gsm, quote_quantity}`；克重只认 `authority.material_text` 的 `<数字>g`（`_gsm_of()` `:3882`）与需求 `face_paper_gsm` 兜底；体内无 `get_backend(` / `load_` / `open(` / `requests` |
| §C1 常量 | `:3865 BUSINESS_COST_REJECT_CODES`（三条闭集，与几何的 `PROCESS_REJECT_CODES` **分家**）、`:3870 BUSINESS_COST_SIZE_SOURCES = ("authority_dimensions",)`；`_mm_text()` `:3873` 负责人话写法（`300.0` → `300`） |
| §C2 口径那句 | `:3930 business_cost_assumption(inputs, *, geometry_part_code="")`：逐字「按权威尺寸（300×200 mm）算的材料开料，未与 CAD 几何核过」；绑了几何时追加「；这一件另有闭合几何件（DWG-Pxx），本结论有意按权威尺寸算」；`ok` 非真 → `""` |
| §C3 POST | `main.py:8704 PACKAGING_BUSINESS_PART_COST_PATH`；`:8708 _packaging_business_part_row()`（清单读不到 / 没这件 → 404 + 业务那条码）；`:8722 _packaging_business_geometry_label()`（`unbound` / `bound:<分量引用>`，只作披露）；`:8746 _packaging_business_cost_analysis()`（复用 `_packaging_part_cost_analysis()` 的 CostAnalysis 契约，只改 summary 口径与第一条 assumption）；`:8766 packaging_business_part_cost()`：写权限 `BOX_MATCH_DECIDE_ROLES` → 不过前置条件 409（`retryable: False`）→ 任务里 `packaging_cost.compute_line("material", inputs["variables"])` → `save_part_cost()` 落一版（`parts_id` **空串**、`lookup: {}`、带 `size_source` / `size_source_ref` / `size_text` / `geometry` / 业务三键 / `source{task_id, computed_at, actor}`） |
| §C4 GET | `:8830 get_packaging_business_part_cost()`：形状与几何那一路同形 + 版本七键（复用 `_packaging_part_conclusion_version()`）+ 新增四键；未跑过 → 200 空态；件不存在 → 404 + `PACKAGING_BUSINESS_PART_NOT_FOUND` |
| §C5 冻结面 | `processability()` / `PROCESS_REJECT_CODES` 一字未动（E1）；几何两条路径常量各一处、行为不变（E2）；`packaging_cost.py` 一行未改（E3）；前端一行未改（E4）；未写技术 IR（C5 用 `store.save_ir` 打桩验证） |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_downstream_red tests.test_packaging_business_part_downstream_entry_red \
  tests.test_packaging_part_conclusion_business_identity_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_business_parts_binding_size_source_red \
  tests.test_packaging_cost_engine_red tests.test_packaging_parts_conclusion_version_readback_red
  → Ran 178 … OK

./open-claude/.venv/bin/python -W ignore -m unittest discover -s tests -p 'test_packaging_*.py'
  → 仍是那 5 条既有挂账（bom_part_size_provenance::B3、parse_to_downstream_seams::B4、
    part_role_mapping_reaches_card::A2、quote_send_recovery::C1、route_bom_version_pinning::F2），
    本批未引入新红
```

未改前端、未改 `tests/` 下任何文件、未连 PG / 34、未写生产数据、未 push / MR / tag / Release / 未部署。

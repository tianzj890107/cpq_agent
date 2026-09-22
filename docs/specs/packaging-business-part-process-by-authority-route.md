# 规格：业务部件没有几何时，工序明细必须能按**权威清单原文**编出来

依赖：`docs/specs/packaging-business-part-cost-by-authority-size.md`（§C1 那条纯函数的三条判据与
三条业务码口径，本批逐条对齐；§6 边界 1 明写「工艺推荐仍要几何」——本批把那句话收窄为
"**料厚/排样相关**的工序仍要几何"，工序明细本身不再被几何卡住）、
`docs/specs/packaging-business-part-size-cost-entry.md`（§6 边界 1：工艺那一半留给后来一批）、
`docs/specs/packaging-parts-downstream-process-and-cost.md`（几何件那套 `processability()` 与
`outline_process()` 的口径：本批**不新写第二套工艺算法**）、
`docs/specs/packaging-business-parts-and-cad-plan-view.md`（§12 边界 3 那句"几何没绑定只影响
依赖几何的尺寸"）。

状态：Spec + 红测（已实现）（现状：`packaging_parts.processability()`（`:2261`）第一道门槛是
`outline_status == "closed"`，业务部件行上**没有** `outline_status` → 恒 `PACKAGING_PART_NOT_CLOSED`；
`main.py` 的单件工艺路由（`PACKAGING_PART_PROCESS_PATH` `:8326`）第一句 `_packaging_part_row(pid, code)`
只认零件文档里的 `DWG-Pxx` → 业务编码（`JWXR21-P01` 这类）**404**。于是真样本 28 件里 24 件
`unbound` 的件，**材料费能算了（`## 412`/`## 413`），工序明细一个都排不出来**，
尽管权威清单里 `product_size_text` / `length_mm` / `width_mm` / `material_text` / `process_text`
（工艺路线原文）都写着）
红测：`tests/test_packaging_business_part_process_by_authority_route_red.py`
行号基线：HEAD `6e3644c`

## 0. 一句话目标

一件**没有几何**的业务部件，只要权威清单里有长度 / 宽度 / 材料原文，就能跑出**工序明细**
（复用既有 `process.outline_process()` 那一支），结论里明写"按权威清单原文与权威尺寸编制的、
未与 CAD 几何核过：没有展开轮廓、没有排样、料厚未知"，缺什么就说什么，绝不用包围盒 /
默认料厚 / 猜出来的几何特征去排工艺。

## 1. 现状缺口（代码级）

1. `processability()`（`:2261`）两道门槛都要几何：`outline_status == "closed"`（或人签字），
   再过 `material` + `thickness_mm` —— 业务件这三样一样都没有，永远过不去。
2. `main.py` 的 `packaging_part_process()`（`:8381`）先 `_packaging_part_row(pid, code)`（只认
   零件文档）→ 业务编码 404；就算过了，第二句 `processability()` 也会 409。
3. 业务件行上其实**什么都有**：`authority.length_mm` / `width_mm` / `material_text` /
   `process_text`（`BUSINESS_AUTHORITY_KEYS` `:3291`），只是没人把它送进
   `process.outline_process(part, ...)`。
4. `as_ir_part()`（`:2214`）是几何件的适配点：它按 `outline_status` / `thickness_mm` 决定给不给
   `plate` 特征 —— 业务件没有这两样，直接拿它适配会得到一个"什么都没有"的 `Part`，
   而**没有任何一处**说得出来"这份工序是按权威清单原文编的、没有几何"。

## 2. 契约

### C1 新增纯函数 `packaging_parts.business_process_inputs(row) -> dict`

- 体内**不得**出现 `get_backend(` / `load_` / `open(` / `requests` / `put_doc(`（可被源码/AST 直接验）；
- 返回**固定十四键**：`{ok, code, message, missing_variables, part_code, name, material_text,
  process_text, grounding, size_source, size_source_ref, size_length, size_width, size_text}`；
- `code` 闭集 = 常量 `BUSINESS_PROCESS_REJECT_CODES = ("PACKAGING_BUSINESS_PART_NOT_FOUND",
  "PACKAGING_BUSINESS_PART_SIZE_UNKNOWN", "PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN")`
  （`ok` 时为 `""`）；这三个码**与成本那三条是同一个事实的同一套写法**（业务件缺编码 / 缺权威尺寸 /
  缺材料），不是几何那三条 `PACKAGING_PART_*`；
- `size_source` 闭集 = 常量 `BUSINESS_PROCESS_SIZE_SOURCES = ("authority_dimensions",)`,
  成功时恒为 `"authority_dimensions"`；
- 判据顺序固定：
  1. `business_part_code` 去空后为空 → `PACKAGING_BUSINESS_PART_NOT_FOUND`
     （message：这一件没有业务部件编码，不能排工艺），`missing_variables: []`；
  2. 权威尺寸不过（`authority.length_mm` / `width_mm` 任一取不到，或 ≤ 0）→
     `PACKAGING_BUSINESS_PART_SIZE_UNKNOWN`，`missing_variables: ["authority_size"]`，
     message 要说清两条出路（在平面图里确认几何映射，或补录权威尺寸后再排）；
  3. 材料原文去空后为空（`authority.material_text`，兜底 `row.material`）→
     `PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN`，`missing_variables: ["material"]`，
     message 点名"权威清单里没有材料原文"；
  4. 否则 `ok: True`：`process_text` 取 `authority.process_text` 原文（没有就 `""`）、
     `size_source_ref` 取权威行 `source`（没有就 `""`）、`size_length` / `size_width` 取两个
     权威毫米数（数值，不是文本）、`size_text` 取 `product_size_text` 原文（没有就 `""`）、
     `name` 取行上 `name`；
- `grounding` 是给模型的**权威原文块**，拼法固定（只收非空项、`；` 分隔、逐字带键名、顺序不变）：
  `尺寸原文：<product_size_text>`、`权威尺寸：<L>×<W> mm`、`材料：<material_text>`、
  `工艺路线：<process_text>`、`排版：<layout_text>`、`备注：<note>`；一项都收不到 → `""`；
- 任何输入都不抛错（非 dict 行 / 数组 / 字符串一律按"没有"处理）；纯函数不许改入参。

### C2 新增纯函数 `packaging_parts.business_as_ir_part(row) -> Part`

- 由业务件行造一个 IR `Part`（**几何件那条 `as_ir_part()` 一字不动**，本函数是业务件那一半）；
- `part_id` = `business_part_code`、`name` = 行上 `name`（空则取 `part_id`）、`role = None`、
  `quantity = 1`；
- `features = []`：**不造几何特征** —— 没有闭合轮廓也没有料厚，给 `plate` 特征就是编几何
  （这正是 `input_gaps()` 该报的"零件没有几何特征"）；
- `material = {"spec": <material_text>}`（材料原文为空时 `None`）；
- `confidence`：有材料 `0.4`、无材料 `0.3`（与几何件"开口件 / 未闭合"同一档：**不装成可信几何**）；
- `provenance.note` 逐段带（` | ` 分隔、顺序不变，取不到的一律 "unknown"/"none"，不许留空段）：
  `packaging_business_part/<code>`、`size_source=authority_dimensions|unknown`、
  `size=<L>×<W>`、`process_source=workbook|none`、`outline=none`、`thickness=unknown`；
- 任何输入都不抛错。

### C3 新增纯函数 `packaging_parts.business_process_assumption(inputs, *, geometry_part_code="") -> str`

- 成功时逐字：`按权威清单的尺寸（<L>×<W> mm）与材料原文编制工序，未与 CAD 几何核过：
  没有展开轮廓、没有排样，料厚未知` —— 两轴取 `inputs["size_length"]` / `inputs["size_width"]`
  （去掉多余小数位；换行只是本 Spec 的排版，字符串里**没有**换行符）；
- `geometry_part_code` 非空 → 追加 `；这一件另有闭合几何件（<code>），本结论有意按权威清单编制`
  （**不**改前面的正文）；
- `inputs["ok"]` 非真 → 返回 `""`（没结论就没有口径那句话）；不抛错。

### C4 新路由 `POST /api/projects/{pid}/requirement/packaging-business-parts/{code}/process`

- 写权限直接引用 `packaging_match.BOX_MATCH_DECIDE_ROLES`（与业务件成本那一条同口径）；
- 取件：`load_business_parts(pid)` 读不到 / 那一件不在清单里 → **404** + `PACKAGING_BUSINESS_PART_NOT_FOUND`
  （message 说清"先导入权威清单再排工艺"）；
- `business_process_inputs(row)` 不过 → **409**（`retryable: False`）+ `{code, message, missing_variables}`
  （**复用** `_packaging_business_part_reject()` 那个形状，逐字同形）；
- 过了 → 与既有单件工艺**同一个异步任务形状**（`tasks.submit(...)` + `task_id` + `report_progress`），
  任务体里：
  - `part = packaging_parts.business_as_ir_part(row)`，
    `plan, coverage = process.outline_process(part, overall=None, geom=None,
    note=<grounding + 用户 note>, attachments=<本次附件>)` —— **复用**既有工艺链路
    （不另写第二套工艺算法、**不**调模型以外的东西）；`note` 里权威原文在前、
    用户补充说明在后（用户那段仍走既有"请优先采用"那一路）；
  - `validation = process.compute(plan.model_dump())`；
  - `lookup = {}`（业务件没有知识库检索依据，不装样子）；
  - 落一版 `save_part_process(pid, {...})`：`part_code` = 业务编码、`parts_id` = **空串**
    （这份结论不是按几何零件算的，不许冒充几何版本）、`engine_version`、
    `plan` / `validation` / `coverage` / `source{task_id, computed_at, actor}` 与几何那一路同形，
    `assumptions` 第一条恒为 C3 那句话，另加 `size_source` / `size_source_ref` / `size_text` /
    `geometry`（`"unbound"`，或这一件在清单里绑了分量时的 `"bound:<分量引用>"`——**沿用成本那条
    已有判据**，不在路由里另写一套）/ `grounding`（那份权威原文块）/ `business_part_code` /
    `business_parts_id` / `business_parts_hash`；
- 结论**不写**技术 IR（`store.save_ir()` 一行都不碰）。

### C5 新路由 `GET /api/projects/{pid}/requirement/packaging-business-parts/{code}/process`

- 形状与几何那一路**逐字同形**（`part_code` / `plan` / `validation` / `coverage` / `assumptions` /
  `source`）+ `_packaging_part_conclusion_version()` 的七键 + 新增 `size_source` /
  `size_source_ref` / `size_text` / `geometry`；
- 没跑过 → **200** 空态（不 404，`plan: None`）；件不存在 / 清单读不到 → 404 +
  `PACKAGING_BUSINESS_PART_NOT_FOUND`；纯读，不写库。

### C6 冻结面

- `processability()`、`PROCESS_REJECT_CODES`、`as_ir_part()` 一字不动；**不许**用几何那几条码
  （`PACKAGING_PART_*`）代替业务这三条；
- 既有两条几何路由（`PACKAGING_PART_PROCESS_PATH` / `PACKAGING_PART_COST_PATH`）与业务件成本
  那两条路由的路径与行为不变；
- 不改 `process.outline_process()` / `process.compute()` 的算法，不改 `packaging_cost`、
  `packaging_match`、`packaging_bom`；
- 不改前端（面板入口是下一批：本批只保证后端排得出来、读得回来、说得清口径）；
- 不改 `tests/` 下任何文件。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_parts.py`：新增两个常量 + `business_process_inputs()` +
   `business_as_ir_part()` + `business_process_assumption()`；
2. `tech_app/backend/main.py`：新增两条业务件工艺路由（POST / GET）+ 一个组装结论的小函数；
3. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许用几何轮廓 / 包围盒 / 默认料厚 / 猜出来的特征去排工艺；
- 不许放宽 `processability()` 的闭合门槛（几何那条路仍是几何那条路）；
- 不许把业务件的工艺结论写成几何件那一份（`parts_id` 必须为空、编码必须是业务编码）；
- 不许自动重算、不许自动改权威清单、不许自动补料厚、不许连 PG / 34、不许写生产数据；
- 不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_part_process_by_authority_route_red -v
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_part_cost_by_authority_size_red \
  tests.test_packaging_business_part_size_cost_entry_red \
  tests.test_packaging_business_part_downstream_entry_red \
  tests.test_packaging_part_conclusion_business_identity_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_parts_downstream_red \
  tests.test_packaging_parts_downstream_readback_red \
  tests.test_packaging_parts_conclusion_version_readback_red
```

## 6. 已记录的边界

1. 本批只做**工序明细**：料厚相关的工时、排样 / 拼版、采购项仍要几何，这些不在本批；
2. 权威清单里的 `process_text`（工艺路线原文）只是**输入证据**，不是结论 —— 工序明细仍由既有
   `outline_process()` 那一支编制，结论里点明它是按原文 + 权威尺寸编的；
3. 面板入口（`openPackagingBusinessPart()` 那第三颗按钮）是下一批；
4. 权威尺寸与几何轮廓**并存**时不算"谁对"：两条路各按自己的输入出数，结论里各说各的口径
   （与 `## 412` §6 边界 3 同一条纪律）。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_part_process_by_authority_route_red
# 实现前：Ran 38 tests … FAILED (failures=6, errors=26)   ← 32 红（A1–A10 / B1–B6 / C1–C4 / D1–D8 / E1–E4）
# 实现后：Ran 38 tests … OK                              ← 6 条护栏（B7 / F1–F5）红基即绿、实现后仍绿
```

| 契约 | 落点 |
| --- | --- |
| §C1 纯函数 | `packaging_parts.py` `:3944 BUSINESS_PROCESS_REJECT_CODES` / `:3953 BUSINESS_PROCESS_SIZE_SOURCES` / `:3957 _business_process_grounding()` / `:3973 business_process_inputs()`：十四键；判据 = 无编码 `PACKAGING_BUSINESS_PART_NOT_FOUND` → 权威长度/宽度缺或 ≤0 `PACKAGING_BUSINESS_PART_SIZE_UNKNOWN`（`missing_variables: ["authority_size"]`）→ 材料原文空 `PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN`（`["material"]`）→ `ok`；`grounding` 六段固定顺序、只收非空 |
| §C2 适配点 | `:4025 business_as_ir_part(row)`：`part_id` = 业务编码、`features=[]`、`material.spec` = 权威材料原文、`confidence` 0.4（有材料）/0.3（无材料）、`provenance.note` 六段（`packaging_business_part/<code>` / `size_source=` / `size=` / `process_source=` / `outline=none` / `thickness=unknown`）；几何件那条 `as_ir_part()` 一字未动 |
| §C3 口径句 | `:4061 business_process_assumption(inputs, *, geometry_part_code="")`：两轴取 `inputs["size_length"]` / `["size_width"]`（不解析展示文本）；`ok` 非真 → `""` |
| §C4 发起 | `main.py` `PACKAGING_BUSINESS_PART_PROCESS_PATH` + `_packaging_business_part_process_note()` + `packaging_business_part_process()`（POST）：`_require(BOX_MATCH_DECIDE_ROLES)` → `_workflow_project` → `_packaging_business_part_row`（404）→ `business_process_inputs()` 不过 → 复用 `_packaging_business_part_reject()`（409 / `retryable: False`）→ 任务体 `process.outline_process(part, overall=None, geom=None, note=权威原文块+用户 note, attachments=本次附件)` → `process.compute()` → `save_part_process(parts_id="")`（另带 `size_source` / `size_source_ref` / `size_text` / `geometry` / `grounding` / 业务三键） |
| §C5 读回 | 同文件的 `get_packaging_business_part_process()`（GET）：几何那一路同形的六键 + 版本七键 + 四键；空态 200（`plan: null`）、件不在清单 404、纯读不写库 |
| §C6 冻结面 | `processability()` / `PROCESS_REJECT_CODES` / `as_ir_part()` / `process.py`（`grep business_` 在 `process.py` 里为 0 处）/ `packaging_cost.py` / 前端 3 处引用 / 业务件成本两条路由 —— 都在红测 F 组与 E4 里逐条锁住 |

**顺带记录的两点**：

1. 业务件走**几何**那条 `processability()` 仍然恒 `PACKAGING_PART_NOT_CLOSED`（F1 明写）—— 本批**不是**
   放宽几何门槛，而是给业务件开了另一条有自己前置条件的路；
2. 权威清单里的 `process_text`（工艺路线原文）只作为**输入证据**进 `note` 与 `grounding`，
   工序明细仍由既有 `outline_process()` 编制（`process.py` 里没有任何业务件分支）。

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_part_cost_by_authority_size_red \
  tests.test_packaging_business_part_size_cost_entry_red \
  tests.test_packaging_business_part_downstream_entry_red \
  tests.test_packaging_part_conclusion_business_identity_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_parts_downstream_red \
  tests.test_packaging_parts_downstream_readback_red \
  tests.test_packaging_parts_conclusion_version_readback_red
  → Ran 134 … OK
```

未改前端、未连 PG / 34、未写生产数据、未调模型、未 push / MR / tag / Release / 未部署。

# 规格：DWG 图纸 → 零件（展开件）提取与下游回填

Spec 版本：1（状态行见下）
红测：`tests/test_packaging_parts_extraction_red.py`
夹具：`tests/fixtures/cad_ir/parts_panels.json`（由 `tests/fixtures/cad_ir/build_fixtures.py` 生成）
上游：`docs/specs/packaging-drawing-semantics.md`（第 4 批：图层角色 / 外轮廓 / 字段证据）、
`docs/specs/packaging-parametric-bom.md`（第 5 批：参数化展开）、
`docs/specs/packaging-cost-engine.md`（第 7 批：成本读 `length_mm` / `width_mm`）

状态：Spec + 红测（已实现）（红测 test_packaging_drawing_flow_red 另有已记录的测试侧冲突，见 changelog ## 262）
红测：`tests/test_drawing_flow_error_taxonomy_red.py` `tests/test_packaging_drawing_flow_red.py` `tests/test_packaging_parts_extraction_red.py`

## 0. 口径变更（本批的前提，取代旧条款）

业务裁决（张真，2026-09-21）：**"肯定要从 DWG 得到零件"**。

因此本文取代 `docs/specs/packaging-product-outline-and-die-layer-roles.md` 中
「不做展开尺寸（`unfolded_size`）建模、不做排样、不做 3D」里**关于"不做展开尺寸"的那半句**：
现在必须从图纸把零件与展开尺寸提出来，并让它喂给 BOM / 成本。
该 Spec 的其余条款（色 / 线型 / 图层角色口径、字段闭集「不得自创 `panel_size`/`unfolded_size`
这类需求字段键」）**一律不变** —— 本批新增的是**零件文档**，不是新的需求字段键。

## 1. 背景（34 上实测，不是推断）

`酒盒.dwg`（sha256 `0991c8b0…f3e0`）在 34 上跑完 2.1 之后的真实产物：

- CAD IR：顶层实体 6711 / 实体 6569 / 图层 8 / 单位 `confirmed`；
  `closed_outline_total = 2`、`open_outline_total = 5598`、`components = 402`、`holes = 642`；
- 包装语义：刀线层 1 个（`CUTTER`，308 条 SPLINE）、压痕线层 0 个、
  边界候选 200（被上限截断）、孔 200、盒型候选 0；
- 字段：只有内长 / 内宽"待确认（几何推断）"，其余 21 项 `missing`，合计 23 项要人补；
- **零件：`ir.parts = 0`、`ir.assemblies = 0`** —— 2.1 左栏零件树空着写「完成解析后显示零件清单」；
- 下游：BOM 的零件行**不是图纸来的**，全部来自盒型模板（`source: kb_packaging_part_template`），
  其中 4 行因缺展开尺寸标 `needs_input`；成本 `material_total = 0.0`、
  缺口含 `part_size_missing`（`packaging_cost.py:1541`）。

同时确认的两条事实（决定本批算法形态）：

1. **"零件 = 闭合轮廓"在真图上不成立**：真图只有 2 条闭合轮廓，5598 条开放轮廓；
   零件只能按**连通分量**（`geometry.components`，真图 402 个）聚合，不能只认闭合环。
2. **知识库里的 DWG 实样零件表没有尺寸**：`kb_packaging_part_template` 里
   `YT-DWG-WINE-700ML` 有 11 行（`WINE-P01..P11`，材料/数量齐全）、
   `YT-DWG-ROUND-10PC` 有 14 行，但 `size_expr / size_length_expr / size_width_expr /
   size_height_expr` **全部为空** → 展开尺寸无处可来。这正是本批要补的那一段。

## 2. 目标

1. 2.1 跑完就能看到**图纸里的零件清单**（每件带展开长宽 mm、图层、角色、证据）；
2. 零件的展开尺寸**回填到 BOM 行**，让 `needs_input` / `part_size_missing` 消失、材料费算得出来；
3. 提不出来时**必须说清为什么**（稳定原因码），绝不静默给 0 件。

## 3. 契约

### C1 零件 = 连通分量（几何口径）

`packaging_parts.extract(ir, ...)` 以 CAD IR 的 `geometry.components` 为唯一零件来源，
每个分量产出一件，字段见 §4。判据与顺序**全部确定性**，不调模型、不联网、不落盘。

### C2 过滤（只留"像零件"的分量）

按顺序逐条剔除，并逐件记 `reason`：

| 规则 | 判据 | 原因码 |
| --- | --- | --- |
| 整版 / 图框 | 长边 > `max_edge_mm`（默认 1200）或面积 > `max_area_mm2`（默认 1000000） | `edge_over_max` / `area_over_max` |
| 碎线 / 标注残渣 | 面积 < `min_area_mm2`（默认 2000） | `area_under_min` |
| 没有可制造曲线 | 分量内实体全是 `TEXT/MTEXT/DIMENSION/INSERT` 之类 | `no_curve_entity` |

阈值走 `options` 覆盖，默认值写进常量 `DEFAULT_OPTIONS`（不得散落在代码里）。
被剔除的件不消失：记在 `filtered` 里（`component_id` + `reason` + `bbox`），供 2.1 与排查用。

### C3 排序 / 去重 / 上限（可复现）

- 排序：`(area_mm2 desc, component_id asc)`；
- 零件号：`part_code = "DWG-P%02d"`，按上面的排序稳定编号（同一份 IR 两次跑必须逐字相同）；
- 重复件：`bbox` + 实体数相同的第 2 件起标 `repeat_of`（指向首件 `part_code`），**不删**；
- 上限：`max_parts`（默认 64），超出时截断并给 `stats.truncated`（> 0 时前端要提示）。

### C4 保证"能出零件"

- 对真实 `酒盒.dwg`（同构夹具 + 34 真样本）：`parts` 必须 ≥ 2 件，每件都有
  `unfolded_length_mm` / `unfolded_width_mm`（mm，单位 `confirmed` 才写数值；否则给 `unit_status` 与空值）；
- 一件也提不出来时，`parts = []` 且 `unavailable` **必须有**一条：
  `no_components`（IR 没有分量）/ `all_filtered`（全被 C2 剔掉）/ `no_unit`（单位未确认）；
- 绝不允许"0 件 + 空原因"，也绝不允许用 `inner_*` 反推的假尺寸凑数。

### C5 落库、读回与版本锚点

- 文档：`engine_version = "packaging-parts/1"`，按 `semantics_id` 覆盖同一条、按新 IR 追加；
- `source` 必带 `ir_id` / `ir_hash` / `semantics_id` / `semantics_hash` / `drawing_version` /
  `unit_status`，字段名与 `packaging_semantics` 的 `source` 块一致；
- 读回：`GET /api/projects/{pid}/requirement/packaging-parts`（纯读，不判写权限）；
- 写：`POST /api/projects/{project_id}/requirement/packaging-parts/extract`，
  写权限直接引用 `packaging_match.BOX_MATCH_DECIDE_ROLES`（同一对象，不另抄一份）；
- 图纸换版后旧零件文档必须能被判为 stale（判据与 `packaging_drawing_flow.stale_view` 同源：
  `ir_hash` / `semantics_hash` / `drawing_version` 任一变化）。

### C6 绘图链路新增一步 `parts_extract`（2.1 可见）

- `packaging_drawing_flow.model.STEP_IDS` 在 `packaging_semantics` 之后、`field_write` 之前插入
  `"parts_extract"`，`STEP_TITLES` 给「零件提取」；
- 该步 `detail` 必带：`parts_id` / `parts_hash` / `parts_total` / `filtered_total` / `truncated` /
  `unavailable`（原因码列表）；
- 该步失败**不阻断**后续步骤的既有口径（仍按 `drawing-flow-error-taxonomy.md` C4：
  前置缺失是 `blocked`，不是"这一步坏了"）；
- BOM / 成本 / 报价门禁的既有结论不变 —— 零件是**新增前置事实**，不改变任何门禁的判据集合。

### C7 回填 BOM 行（本批只做"行级绑定"，不做零件语义命名）

`packaging_bom.build_bom(...)` 在装配时（有零件文档就自动带上）对**算不出来的行**做行级绑定：

1. 适用条件：该行 `bom_category in ("box_part", "optional_part")` 且
   （`status == "needs_input"` 或 `length_mm/width_mm` 为空）。锁定行（`locked = 1`）**绝不绑定**；
2. 绑定顺序：模板行 `seq` 升序 ↔ 零件面积降序（`part_code` 升序兜底），一一对应；
3. 绑定值：`length_mm = unfolded_length_mm`、`width_mm = unfolded_width_mm`；
4. 留痕（缺一不可）：`size_source.dwg_binding = {"component_id": …, "part_code": …,
   "rule_id": "dwg_parts_row_pairing_v1", "fallback_paired": true,
   "original_missing_variables": [...]}`；`source` 改 `"dwg_parts"`；
   `missing_variables` 清空（原值进 `original_missing_variables`）；
5. 绑不上的行保持 `needs_input`，并在 BOM `gaps` 里给 `part_size_unbound:<part_code>`
   —— **不许**用需求尺寸反推、不许编数；
6. 表达式**能**求出来的行（如 `YT-RB-01001-A` 的 P01/P04/P05/P06/P09/P10）一个字都不许变。

## 4. 命名与数据契约（实现方不得改名）

模块 `tech_app/backend/services/packaging_parts.py`：

```
ENGINE_VERSION = "packaging-parts/1"
DOC_KEY = "packaging_parts"                      # meta 后端 doc key
DEFAULT_OPTIONS = {"min_area_mm2": 2000, "max_edge_mm": 1200,
                   "max_area_mm2": 1000000, "max_parts": 64}
CURVE_TYPES = ("LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE")
REASON_CODES = ("edge_over_max", "area_over_max", "area_under_min", "no_curve_entity",
                "no_components", "all_filtered", "no_unit")
PART_CODE_FORMAT = "DWG-P%02d"

extract(ir, semantics=None, *, options=None) -> dict     # 纯函数：不落盘、不调模型
save_parts(project_id, doc) -> dict                      # 版本化落库（同 semantics 的做法）
load_parts(project_id, parts_id=None) -> Optional[dict]
bind_rows(items, parts, *, options=None) -> dict         # 纯函数：回填 BOM 行（C7）
summarize(doc) -> dict                                   # 摘要（不含 entity_ids 明细）
```

零件文档：

```
{"engine_version","parts":[{"part_code","name","unfolded_length_mm","unfolded_width_mm",
  "area_mm2","layers":[…],"role","component_id","entity_ids":[…],"evidence_refs":[…],
  "repeat_of","size_source":"dwg_outline"}],
 "filtered":[{"component_id","reason","bbox"}],
 "unavailable":[{"code","message"}],
 "stats":{"part_total","filtered_total","truncated","by_role":{…}},
 "source":{"ir_id","ir_hash","semantics_id","semantics_hash","drawing_version","unit_status"},
 "reviewable": true}
```

- `role` 取件内实体的图层角色，优先级 `cut > half_cut > crease > v_groove > glue_flap >
  print > bleed > frame > hole > unknown`（同一件里既有刀线又有压痕 → `cut`）；
- `name` 默认 `"图纸零件 P01"`（按 `part_code` 序号），有图纸文字证据时可回填，但**不许**用文件名猜；
- `evidence_refs` 至少含该件每条实体曲线的 `ev:E:*` 与涉及图层的 `ev:L:*`，且必须能在
  `ir.evidence` 里回查。

## 5. 前端接线（2.1）

- `tech_app/frontend/app.js`：2.1 左栏零件树改为渲染零件文档
  （每行：`part_code` + `name` + `展开 长×宽 mm` + 图层；空态显示 `unavailable` 的中文原因）；
- `drawingFlowPanel` 增加「零件提取」一行（沿用既有步骤渲染，不新写一套）；
- 零件树的行数必须等于 `stats.part_total`（不许前端自己过滤），`truncated > 0` 要给提示。

## 6. 不在本批范围

- 不做排样 / 拼版优化 / 3D；
- 不做"零件 ↔ 模板行的正式对应表"（C7 是行级临时口径，带 `fallback_paired` 留痕）；
- 不改 `packaging_semantics` 的图层角色口径与字段闭集；
- 不改任何需求字段键（不许加 `unfolded_size` 这类键）；
- 不改成本公式与费率。

## 7. 验收标准

1. `python3 -m unittest tests.test_packaging_parts_extraction_red` 全绿；
   （本机用 `./open-claude/.venv/bin/python3`；`openpyxl` 只在该 venv）
2. 保护网不破：`test_packaging_semantics_red`、`test_packaging_parametric_bom_red`、
   `test_packaging_drawing_flow_red`、`test_packaging_cost_engine_red` 的
   既有绿测一条都不许转红（`## 222` 里等裁决的红除外）；
3. 34 真样本（实现后由 Codex 复跑）：`酒盒.dwg` 走完 2.1 后
   `GET /api/projects/{pid}/requirement/packaging-parts` 的 `stats.part_total ≥ 2`，
   每件 `unfolded_length_mm/unfolded_width_mm > 0`；
   再跑 BOM：`needs_input` 行数下降且 `length_mm/width_mm` 有值；
   再跑成本：`material_total > 0`，`gaps` 里不再出现 `part_size_missing`。

## 8. 待业务复核（本批按临时口径实现，不阻塞）

1. 行级绑定（C7）是"先保证有数"，正式口径应是"零件 ↔ 模板行"的对应表（按 `component` 关键词）；
2. 零件命名（`name`）现在是 `图纸零件 PNN`，是否要用图纸里的文字/块名回填；
3. 真图是多拼版（`components = 402`，`repeated_groups` 只有 1 组），
   拼接件要不要合并成一件（本批不合并，只标 `repeat_of`）。

# 规格：包装图纸语义、刀线压痕线与需求字段证据 —— DWG 支持第 4 批

> 批次：包装行业 **DWG 支持第 4 批**（共 6 批）。前置：第 1 批（`docs/specs/dwg-file-capability-preflight.md`）、
> 第 2 批（`docs/specs/dwg-controlled-conversion-adapter.md`）、第 3 批（`docs/specs/dxf-cad-ir.md`）
> 与第 1/2 批修复（`docs/specs/dwg-conversion-quality-repair.md`）。
> 红测：`tests/test_packaging_semantics_red.py`。夹具：`tests/fixtures/cad_ir/`（冻结的 CAD IR 文档）。
> 真实样本：`裕同包装项目-待开发/酒盒.dwg`、`圆盘盒.dwg`（只读）。
> 本批目标：把**通用 CAD IR** 转成**包装行业结构化信息 + 带证据的字段候选**。
> **不锁定盒型、不写正式报价、不计算成本、不碰 Agent 会话与右侧看板 UI（第 5 批）。**

## 0. 与既有两条包装线的关系（先说清，避免撞名）

| 线条 | 编号 | 与本批的关系 |
| --- | --- | --- |
| 包装业务线 | `docs/specs/packaging-requirement-template.md`（第 2 批）、`packaging-box-type-matching.md`（第 4 批）、`packaging-parametric-bom.md`（第 5 批）等 | 本批**复用**其需求字段键、`field_sources` 闭集与 `packaging_match` 输入键，**不改**它们 |
| DWG 图纸线（本批所属） | 第 1 批预检 → 第 2 批转换 → 第 3 批 CAD IR → **第 4 批包装语义** → 第 5 批会话贯通 → 第 6 批 E2E | 本批是「图纸事实」到「需求字段候选」的最后一跳 |

**禁止把两条线的编号混读**：`packaging-box-type-matching.md`（包装第 4 批）早已实现盒型匹配打分；
本批（DWG 第 4 批）**只产出候选**，打分与淘汰仍由既有 `packaging_match.py` 负责。

## 1. 现状取证（实测，9-20）

- 全仓**没有任何包装图纸语义代码**：`grep -rn "刀线\|crease\|half_cut" --include=*.py tech_app/backend/` 无命中；
  `services/dxf_inspect.py` 是运维排查用的 DXF 概览（图层/实体计数），**不含**角色判定、轮廓候选、字段证据。
- 需求看板的字段来源闭集已存在：`requirement_service.FIELD_SOURCES = ("user_text", "attachment",
  "ai_extract", "ai_recommend", "manual")`，合并规则 `merge_field_sources()` 已保证 `manual` /
  `user_text` / `attachment` 不被弱来源降级（`requirement_service.py:29-61`）。
- 需求数据里的**页面状态键**已有先例：`da_repo._STRUCTURAL_DATA_KEYS` 含 `field_sources`
  （`da_repo.py:70-75`），注释明确「属页面状态而非业务字段，不能进 `src_requirement_field`」。
- 规则快照约定：`tech_app/agent_knowledge/rules/*.json`（`packaging_cost_rules.json` / `process_rules.json`），
  成本侧读法见 `packaging_cost.py:550`。**本批的图层规则沿用同一目录与同一风格**。
- 模型调用只有一个入口：`vision.py` 通过 `claude_client.resolve_route/_module_for/_translate/run`
  （`vision.py:607-609`），红测 patch `claude_client.run` 即可统计调用次数
  （既有做法见 `tests/test_dwg_file_capability_preflight_red.py:242`）。
- 第 3 批（CAD IR）由并行的实现方落地中；本批红测因此分成两层：**主体验（A–I 组）只依赖冻结的
  CAD IR 夹具**，可独立验收；仅 1 条依赖用例（`J1`）在 `cad_ir` 尚未落地时明确报「依赖第三批」。
- LibreDWG 附带 `dwgbmp` 能导出**缩略图**：`圆盘盒.dwg → 31,878 B`、`酒盒.dwg → 31,878 B`
  （实测），内容为 `220×140`、8 位索引 **BMP**（即使文件名叫 `.png`）。**分辨率不足以读标题栏**，
  只可作为「有这么一张小图」的弱证据（§6）。

## 2. 契约 A：模块结构与对外稳定接口

新增包 `tech_app/backend/services/packaging_semantics/`（确定性优先；模型只做辅助且可完全关闭）：

```
packaging_semantics/
  __init__.py       # 对外稳定接口（下方）
  model.py          # 语义数据结构 + SEMANTICS_VERSION + 规范排序/哈希 + 枚举闭集
  rules.py          # 图层规则配置（读取/校验/版本指纹）
  roles.py          # 图层 → 角色（cut/crease/half_cut/v_groove/glue_flap/print/bleed/frame/hole/unknown）
  geometry_semantics.py  # 外轮廓/展开边界/出血/开窗/孔位/拼版候选
  fields.py         # 长宽高候选、展开尺寸、材料/工艺文字、单拼多拼、盒型候选
  provenance.py     # 字段来源/可信度/冲突 + 写需求看板
  model_assist.py   # 唯一模型入口（栅格预览可选；不可用时确定性规则照常工作）
  persistence.py    # 落盘（doc key `packaging_semantics`，唯一写盘入口）
```

对外接口（**冻结**，第 5 批直接调用）：

```python
SEMANTICS_VERSION = "packaging-semantics/1"

def capability() -> dict          # {"available","rules_path","rules_version","template","model_assist","message"}
def analyze(ir, *, template=None, rules=None, preview=None, use_model=False, options=None) -> dict
def analyze_conversion(project_id, *, ir=None, ir_id=None, template=None, rules=None,
                       preview=None, use_model=False, author="system") -> dict
def load_semantics(project_id, semantics_id=None) -> dict | None
def list_semantics(project_id) -> list[dict]      # 时间倒序
def field_candidates(semantics) -> dict           # {field_key: candidate}
def apply_to_requirement(project_id, semantics, *, accept=(), author="system") -> dict
def summarize(semantics) -> dict                  # 第 5 批 Agent 只吃摘要
def migrate(semantics) -> dict
```

- `analyze()` 必须是**纯函数式确定性**：同样输入两次调用 → `semantics_hash` 完全一致。
- `persistence.py` 是**唯一写盘入口**；`analyze()` 本身不落盘。
- `persistence.py` 必须导出三个函数 `save_semantics(project_id, semantics)` /
  `load_semantics(project_id, semantics_id=None)` / `list_semantics(project_id)`；
  包级 `load_semantics` / `list_semantics` / `apply_to_requirement` 一律**转调**它们
  （红测在 persistence 层注入内存实现，不在包级打桩）。
- `analyze_conversion(project_id, ir=...)`：给了 `ir` 就直接用，**不查** `cad_ir`
  （红测与第 5 批复用）；没给就 `cad_ir.load_ir(project_id, ir_id)`，取不到 → §9 的码。
- 模块**不得** import `vision` / `step_import`；模型访问只允许经 `model_assist.py` 转发到
  `claude_client`（红测用源码扫描 + 调用计数双重锁死）。

## 3. 契约 B：语义文档结构（`packaging_semantics` 文档位）

```json
{
  "semantics_version": "packaging-semantics/1",
  "semantics_id": "<16 hex>",
  "semantics_hash": "<sha256 hex>",
  "source": {"project_id": "", "ir_id": "", "ir_hash": "", "ir_version": "cad-ir/1",
             "drawing_version": 1, "conversion_status": "success_with_warnings",
             "warning_count": 252, "error_count": 3,
             "template": "generic", "rules_version": "<12 hex>", "rules_path_kind": "default|override"},
  "layers": [{"name": "CUT", "role": "cut", "role_confidence": 0.9,
              "evidence_level": "STRONG", "matched_rule_id": "cut_name_v1",
              "role_source": "rule|color_rule|line_type_weak|none", "evidence_refs": ["ev:L:CUT"]}],
  "roles_summary": {"cut": {"layer_count": 1, "entity_count": 12}, "unknown": {"layer_count": 3, "entity_count": 40}},
  "outline": {"boundary_candidates": [{"outline_id": "", "entity_ids": [], "bbox": [], "length": 0.0,
                "length_mm": null, "area": 0.0, "is_closed": true, "evidence_level": "STRONG",
                "evidence_refs": []}],
              "bleed_candidates": [], "windows": [], "holes": [],
              "panel": {"panel_count": 1, "multi_up": false, "candidates": [], "evidence_refs": []}},
  "dimensions": {"conflicts": [{"field": "inner_length", "declared": 70.0, "measured": 72.0,
                 "delta": 2.0, "tolerance": 0.5, "unit": "mm", "status": "conflict",
                 "evidence_refs": ["ev:D:34", "ev:E:33"]}],
                 "measured_total": 0, "declared_total": 0},
  "texts": {"material_candidates": [{"field": "face_paper", "text": "白卡纸 350g",
             "evidence_level": "MODERATE", "evidence_refs": ["ev:E:53"]}],
            "process_candidates": [], "title_block": {}, "unit_hints": []},
  "box_candidates": [{"candidate_type": "round_tube", "confidence": 0.0, "matched_features": [],
                      "missing_features": [], "contradictory_features": [], "evidence_refs": []}],
  "fields": {"<field_key>": {"origin": "confirmed_from_cad", "status": "confirmed", "value": null,
              "confidence": 0.0, "evidence_level": "STRONG", "evidence_refs": [],
              "conflicts": [], "alternatives": [],
              "ir_id": "", "ir_hash": ""}},
  "unresolved": [{"field": "box_type", "reason": "no_rule_matched", "status": "missing"}],
  "model_assist": {"used": false, "status": "unavailable", "calls": 0, "model": "",
                   "stable_error_code": "", "preview_kind": "none", "evidence_level": "NONE",
                   "extra_fields_dropped": []},
  "warnings": [{"code": "", "message": "", "evidence_refs": []}],
  "stats": {"layer_total": 0, "cut_layer_total": 0, "crease_layer_total": 0,
            "boundary_candidate_total": 0, "hole_total": 0, "conflict_total": 0,
            "unresolved_total": 0, "box_candidate_total": 0},
  "reviewable": true
}
```

- 每个候选/字段都必须带 `evidence_refs`，且 ref 必须能在**输入 CAD IR 的 `evidence` 里解析到**
  （红测 `A8`：逐条回查，缺一条即失败）。
- 排序：`layers` 按 `name`；轮廓候选按 `(is_closed desc, -area, outline_id)`；`fields` 按 key；
  `unresolved` 按 `(field, reason)`；`warnings` 按 `(code, message)`。→ 保证 `semantics_hash` 稳定。
- 禁止 NaN/Infinity/不可序列化对象（红测 `A11`）。

## 4. 契约 C：图层规则配置（`tech_app/agent_knowledge/rules/packaging_layer_rules.json`）

```json
{"rule_set": "packaging_layer_rules_v1", "review_status": "reviewed", "generated_at": "2026-09-20",
 "default_template": "generic",
 "templates": {"generic": {"layers": [
    {"rule_id": "cut_name_v1", "role": "cut", "evidence_level": "STRONG", "confidence": 0.9,
     "match": {"names": ["CUT", "DIE", "刀线", "模切线"], "name_prefix": ["CUT", "DIE"]}},
    {"rule_id": "crease_name_v1", "role": "crease", "evidence_level": "STRONG", "confidence": 0.9,
     "match": {"names": ["CREASE", "FOLD", "压痕", "折痕"]}},
    {"rule_id": "half_cut_name_v1", "role": "half_cut", "evidence_level": "MODERATE", "confidence": 0.7,
     "match": {"names": ["HALFCUT", "HALF_CUT", "半切"]}},
    {"rule_id": "v_groove_name_v1", "role": "v_groove", "evidence_level": "MODERATE", "confidence": 0.7,
     "match": {"names": ["VGROOVE", "V_GROOVE", "V槽"]}},
    {"rule_id": "glue_flap_name_v1", "role": "glue_flap", "evidence_level": "MODERATE", "confidence": 0.6,
     "match": {"names": ["GLUE", "糊口", "粘口"]}},
    {"rule_id": "print_name_v1", "role": "print", "evidence_level": "MODERATE", "confidence": 0.6,
     "match": {"names": ["PRINT", "印刷"]}},
    {"rule_id": "bleed_name_v1", "role": "bleed", "evidence_level": "MODERATE", "confidence": 0.6,
     "match": {"names": ["BLEED", "出血"]}},
    {"rule_id": "frame_name_v1", "role": "frame", "evidence_level": "STRONG", "confidence": 0.8,
     "match": {"names": ["FRAME", "BORDER", "图框", "标题栏"]}},
    {"rule_id": "hole_name_v1", "role": "hole", "evidence_level": "MODERATE", "confidence": 0.6,
     "match": {"names": ["HOLE", "孔位"]}}
  ], "colors": {}, "line_types": {}}}}
```

- **规则 ID 闭集**：`cut` / `crease` / `half_cut` / `v_groove` / `glue_flap` / `print` / `bleed` /
  `frame` / `hole` / `unknown`。`unknown` 只能由「没有规则命中」产生，**不许**在配置里声明。
- `colors` / `line_types` 默认**空**：颜色编号与线型的含义只能来自**客户模板配置**
  （`templates.<name>.colors = {"3": "cut"}`），**不许**在代码里硬编码任何「颜色 N = 刀线」的表。
- 线型（虚线/实线）只能作为**弱证据**：命中后 `evidence_level="WEAK"`、`role_confidence<=0.5`、
  字段状态 `needs_confirmation`；**不许**把「所有虚线」判成压痕。
- 未命中任何规则 → `role="unknown"`、`role_confidence<=0.3`、`role_source="none"`，并进 `unresolved`。
- 配置读取：默认路径 `tech_app/agent_knowledge/rules/packaging_layer_rules.json`，
  可用 `PACKAGING_LAYER_RULES_PATH` 覆盖；`rules_version` = 文件字节 sha256 前 12 位；
  模板用 `template=` 参数或 `PACKAGING_LAYER_TEMPLATE`（默认取配置里的 `default_template`）。
- 配置缺失/JSON 非法/模板不存在 → `PACKAGING_LAYER_RULES_INVALID`（§9），**不许**静默用内置兜底规则。

## 5. 契约 D：字段来源、可信度与冲突（本批核心）

### 5.1 两个闭集（不许自创）

```python
ORIGINS  = ("confirmed_from_cad", "inferred_from_geometry", "inferred_from_text",
            "inferred_by_model", "user_confirmed", "conflict", "missing")
STATUSES = ("confirmed", "needs_confirmation", "conflict", "missing")
```

| origin | 允许的 status | 置信度上限 | 含义 |
| --- | --- | --- | --- |
| `confirmed_from_cad` | `confirmed` | 1.0 | 明确标注或强规则几何，且**单位已确认** |
| `inferred_from_geometry` | `needs_confirmation` | 0.8 | 几何推断（轮廓/孔位/拼版），需人工确认 |
| `inferred_from_text` | `needs_confirmation` | 0.7 | 文字/标题栏抽取 |
| `inferred_by_model` | `needs_confirmation` | 0.6 | 模型辅助，恒 `evidence_level=WEAK` |
| `user_confirmed` | `confirmed` | 1.0 | 人工确认过（`accept=` 或既有看板确认） |
| `conflict` | `conflict` | — | 证据互相矛盾，必须人工裁决 |
| `missing` | `missing` | 0.0 | 图纸里确实看不出来，**保持缺失** |

### 5.2 铁律

1. **未确认不写值**：`status != "confirmed"` 的字段**不得**写进 `requirement.data[field]`，
   只能出现在 `field_provenance` 与 `unresolved` 里（红测 `D5`）。→ 未确认信息**不可能**进入成本正式计算。
2. **单位未确认 → 绝对尺寸不得确认**：`ir.units.unit_status != "confirmed"` 时，
   `inner_length` / `inner_width` / `inner_height` / 展开尺寸最高只能是
   `needs_confirmation` + `PACKAGING_UNIT_UNCONFIRMED` 警告（红测 `D4`）。
3. **模型不得覆盖 CAD**：同字段已有 CAD 证据时，模型结论只能进 `alternatives`，
   `origin` 保持 CAD 来源（红测 `E3`）。
4. **冲突不静默**：`declared`(标注) 与 `measured`(几何) 之差超过 `tolerance` → 生成
   `status="conflict"`、保留**两条**证据、`delta` 如实记录；未裁决前该字段及其下游依赖字段
   （`MATCH_INPUT_KEYS` 里同组字段）都不得被视为已确认（红测 `D3`/`D6`）。
5. **不覆盖用户确认值**：`apply_to_requirement()` 遇到 `field_provenance[field].origin == "user_confirmed"`
   或既有 `field_sources[field] == "manual"` → 只追加 `alternatives` + `PACKAGING_FIELD_USER_CONFIRMED`
   警告，**不改值、不降级来源**（红测 `D7`）。
6. **文件名不是证据**：`酒盒.dwg` / `圆盘盒.dwg` 的**文件名**不得作为 `box_type` 或任何结构参数的证据
   （红测 `F4`：把同一份 IR 用不同文件名喂进去，字段结论必须完全一致）。
7. **缺什么就 missing**：图纸没有材料信息时 `face_paper` / `grey_board` 等保持
   `origin="missing"`，**不许**编造（如 FR-4、白卡纸 350g）（红测 `D8`）。

### 5.3 长宽高候选的确定性口径（不许拍脑袋）

对**面积最大的闭合边界候选**（`outline.boundary_candidates[0]`，按面积降序后的第一个）：

| 字段 | 口径 | 允许的最高 origin |
| --- | --- | --- |
| `inner_length` | 该候选 `bbox` 的**较长边** | 见下方三分支 |
| `inner_width` | 该候选 `bbox` 的**较短边** | 见下方三分支 |
| `inner_height` | **不能**从二维展开图单面得出：只接受标注文字（`inferred_from_text`）或模型（`inferred_by_model`）；否则 `missing` | 最高 `inferred_from_text` |

`inner_length` / `inner_width` 按下列分支判定（顺序即优先级）：

1. 存在 **目标为该候选** 的 `DIMENSION`，且 `|declared - measured| <= tolerance`，且
   `units.unit_status == "confirmed"` → `confirmed_from_cad` / `confirmed`，
   值取**标注值** `declared_value`（图纸明示值；`measured_value` 留在证据里）。
2. 同一条件但 `|declared - measured| > tolerance` → `conflict` / `conflict`，保留**两条**证据（§5.2 第 4 条）。
3. 其余（无标注、或单位未确认）→ `inferred_from_geometry` / `needs_confirmation`；
   单位未确认时**必须**附带 `PACKAGING_UNIT_UNCONFIRMED` 警告。

- **禁止**用文件名、预览图比例、像素测量、盒型先验去填长宽高。
- **禁止**在 `unit_status != "confirmed"` 时给出任何 `confirmed` 的绝对尺寸（含 `length_mm` 派生值）。

### 5.4 写需求看板的双写要求

`apply_to_requirement()` 每次写入必须**同时**维护两份留痕：

```python
requirement.data["field_provenance"] = {field: {...见 §3 fields...}}
requirement.data["field_sources"]    = {field: <既有闭集值>}   # user_text/attachment/ai_extract/ai_recommend/manual
```

- 来源映射（沿用既有闭集，**不新增** `FIELD_SOURCES` 取值）：
  `confirmed_from_cad` / `inferred_from_geometry` / `inferred_from_text` → `attachment`；
  `inferred_by_model` → `ai_recommend`；`user_confirmed` → `manual`。
- `da_repo._STRUCTURAL_DATA_KEYS` 必须新增 `field_provenance`（`field_sources` 已在其中），
  否则会被拆成 `inner_length` 之类的字段行（红测 `D9`）。
- 写入必须**增量**：一次只写本次涉及的字段，不清空其它字段、不动 `history` / `waivers` / 报价溯源键；
  写盘走既有 `requirement_service.save_requirement_draft()`，不自己 `store.save_requirement`（红测 `D10`）。

## 6. 契约 E：模型辅助（可关闭、可审计、不许越权）

- 输入**必须**是栅格预览（`image/png` / `image/jpeg` / `image/webp` / `image/bmp`）。
  SVG **不得**直接送模型（第 2 批 Spec §3），XML/文本也不行。
- 预览参数形状固定为 `preview={"bytes": <bytes>, "media_type": "image/png"}`（**只此一种**）：
  `media_type` 不在栅格闭集内（含 `image/svg+xml`）按「不可用」处理，**模型调用数为 0**。
- 预览缺失或不是栅格 → `model_assist.status="unavailable"` +
  `stable_error_code="PACKAGING_PREVIEW_UNAVAILABLE"`，**模型调用数为 0**，确定性结果照常产出（红测 `E1`/`E6`）。
- 调用只允许一条缝：`packaging_semantics/model_assist.py` → `claude_client`（`resolve_route` /
  `_module_for` / `_translate` / `run`）。红测 patch `claude_client.run` 计数（红测 `E2`）。
- 模型输出必须过 schema：非法 JSON / 缺字段 / 类型错 → `status="failed"` +
  `stable_error_code="PACKAGING_MODEL_OUTPUT_INVALID"`，**保留**确定性结果，**不许**整批失败（红测 `E4`）。
- **越权字段丢弃**：模型返回的字段不在允许清单（§7）内 → 丢进 `model_assist.extra_fields_dropped`
  并记警告，**不写** `fields`、**不写**看板（红测 `E5`）。
- 模型结论一律 `origin="inferred_by_model"` + `status="needs_confirmation"` + `evidence_level="WEAK"`
  （红测 `E7`）。
- 允许模型做：读标题栏/复杂文字、生成盒型候选、解释不规范图层名、提人工确认问题。
  禁止：覆盖 CAD 尺寸、无依据填长宽高、编造材料/克重/工艺、决定金额、把像素测量当正式尺寸。
- 审计与日志：**不得**记录预览图 Base64、整张 DXF 原文、模型思维链（`reasoning`/`thinking`/`chain_of_thought`）；
  只记 `model` / `calls` / `status` / `preview_sha256` / `preview_bytes`（红测 `E8`/`E9`）。
- 关闭开关：`use_model=False`（默认）时**零调用**；`PACKAGING_SEMANTICS_MODEL=off` 全局锁死（红测 `E10`）。

### 6.1 调用缝与输出契约（红测就 patch 这一处）

- 必须以 **`claude_client.run(...)` 的模块属性形式**调用（`claude_client` 作为模块 import），
  **不得** `from ... import run` —— 否则红测的 patch 缝不成立（`vision.py:607-609` 是既有范本）。
- `output_model` 固定为 `packaging_semantics.model.PackagingAssistResult`，且该模型
  `model_config = ConfigDict(extra="allow")`：越权键因此能在 `__pydantic_extra__` 里被看见并计入
  `extra_fields_dropped`，**不许**靠 `extra="ignore"` 把它们悄悄吞掉。
- 返回值**两种都要能处理**：pydantic 模型（优先 `model_dump()`）或普通 `dict`（红测直接喂 dict）。
- 允许的模型输出键闭集（其余一律算越权）：

```json
{"title_block": {"material": "", "box_type_hint": "", "notes": []},
 "material_candidates": [{"field": "face_paper", "text": "", "confidence": 0.0}],
 "process_candidates": [{"field": "hot_stamping", "text": "", "confidence": 0.0}],
 "box_candidates": [{"candidate_type": "", "confidence": 0.0, "matched_features": [],
                     "missing_features": [], "evidence": ""}],
 "layer_interpretations": [{"layer": "", "role": "", "confidence": 0.0}],
 "open_questions": ["..."]}
```

- `material_candidates[].field` / `process_candidates[].field` 还必须落在 §7 的字段白名单里；
  否则同样算越权（丢进 `extra_fields_dropped`，不写 `fields`、不写看板）。
- 模型**抛异常**（含 JSON/schema 错误）→ `status="failed"` + `PACKAGING_MODEL_OUTPUT_INVALID`，
  确定性结果**原样保留**。

## 7. 契约 F：本批只产生候选，不锁定

- 结构字段候选允许清单（写看板的最长清单，其它字段只进 `unresolved`）：
  `inner_length` / `inner_width` / `inner_height` / `box_type` / `box_family` / `closure_type` /
  `v_groove` / `grey_board` / `grey_board_thickness` / `face_paper` / `face_paper_gsm` /
  `insert_type` / `print_colors` / `lamination` / `hot_stamping` / `uv_coating` / `emboss_deboss` /
  `silk_screen` / `die_cutting` / `mounting` / `special_process` / `units_per_carton` / `carton_size`。
  键名取自 `industry_templates.PACKAGING_SPEC`，**不许**自创键（红测 `F1`）。
- `box_candidates[].candidate_type` 取闭集
  `("telescope_lid_base", "drawer", "book_style", "folding_carton", "round_tube", "irregular", "unknown")`；
  必须给出 `matched_features` / `missing_features` / `contradictory_features` / `evidence_refs`。
- `box_type` **永不** `confirmed`；最高 `inferred_from_geometry` + `needs_confirmation`
  （红测 `F2`）。既有 `packaging_match.run_box_match()` 与人工确认流程**不在本批改动范围**（红测 `F3`）。
- 拼版：`outline.panel` 只给 `panel_count` / `multi_up` / `candidates`，**不**决定实际排版。

## 8. 契约 G：幂等、版本与证据隔离

- `analyze(ir)` 幂等：同样 `ir_hash` + 同样 `rules_version` + 同样 `options` → 同 `semantics_id` / `semantics_hash`。
- `analyze_conversion()` 重复调用**不新增**版本条目（`list_semantics()` 长度不变）。
- 新图纸版本（新 `ir_id` / `ir_hash`）→ 新 `semantics_id`；旧 semantics 与旧 `field_provenance`
  **仍可回看**（`load_semantics(project_id, semantics_id)`），且 `field_provenance[field]` 的
  `ir_id`/`ir_hash` 必须指向**产生该值的那一版**（红测 `G2`）。
- `migrate()`：缺 `semantics_version` → `{"status": "needs_rebuild", "reason": "missing_semantics_version"}`；
  同版本 → `{"status": "ok"}`；未知/更高版本 → `raise ValueError("unsupported packaging semantics version")`。

## 9. 契约 H：错误码（并入第 1 批权威闭集，18 → 20 条）

| `stable_error_code` | HTTP | `retryable` | 触发 |
| --- | --- | --- | --- |
| **`PACKAGING_SEMANTICS_SOURCE_MISSING`**（新） | 422 | 是 | 项目里没有可用的 CAD IR（`load_ir` 为空 / `ir_version` 不认识） |
| **`PACKAGING_LAYER_RULES_INVALID`**（新） | 500 | 否 | 规则文件缺失 / JSON 非法 / 模板不存在 / 规则 ID 越界 |

- 两条必须同步加进 `docs/specs/dwg-file-capability-preflight.md` §3 的表与第 1 批红测 `ERROR_CODES`
  （第 1 批表是唯一权威），并新增 §9 的计数说明（18 → 20）。
- 失败统一 `raise file_preflight.FileCapabilityError(code, detected=..., message=...)`；
  错误信息**不得**含堆栈、绝对路径、DXF 原文。
- 「预览不可用」「模型返回非法 JSON」**不是**异常，只是 `model_assist` 的状态（§6）。
- 不变量：失败**不写** semantics 文档、不改 `stages`、不写看板、不产生版本条目。

## 10. 契约 I：性能与安全

| 参数（env 可覆盖） | 默认 | 行为 |
| --- | --- | --- |
| `PACKAGING_SEMANTICS_MAX_CANDIDATES` | 200 | 轮廓/孔位候选上限，超出截断 + 警告 |
| `PACKAGING_SEMANTICS_MAX_CONFLICTS` | 200 | 冲突条数上限，超出截断 + 警告 |
| `PACKAGING_SEMANTICS_TIMEOUT_SECONDS` | 60 | 超时 → `DWG_PARSE_FAILED`（不挂死） |
| `PACKAGING_SEMANTICS_MODEL` | `auto` | `off` 时全局禁用模型（§6） |
| `PACKAGING_SEMANTICS_MAX_PREVIEW_BYTES` | 8 MiB | 超过 → 不送模型，`PACKAGING_PREVIEW_TOO_LARGE` 警告 |

- 不联网（除模型辅助这一条显式路径）；不执行图纸里的脚本/宏；不读 XREF。
- 不把 CAD IR 实体明细、DXF 原文、预览 Base64 写进 `packaging_semantics` 文档或审计。

## 11. 契约 J：夹具与红测清单（`tests/test_packaging_semantics_red.py`）

夹具：`tests/fixtures/cad_ir/*.json`，每份都是一份**合法的 CAD IR 文档**（第 3 批 §3 结构）。
`ir_id` / `ir_hash` 是**不透明版本锚点**：本批只透传、不重算、不校验其算法
（避免与第 3 批的规范化实现耦合）。

| 夹具 | 用途 | 关键期望 |
| --- | --- | --- |
| `cut_crease_layers.json` | CUT 1 / CREASE 2 / PRINT 1 / FRAME 1 / 无规则图层 1 | 角色按规则命中；无规则图层 `unknown` |
| `color_only_layers.json` | 图层名无意义、颜色 3 承载刀线（仅配置能解释） | 默认规则下 `unknown`；模板配了颜色才 `cut` |
| `dashed_crease_layer.json` | 只有线型（`DASHED`）可作弱证据 | `WEAK` + `needs_confirmation`，不许直接确认 |
| `dim_conflict.json` | 标注 70 / 几何 72 | `conflict`、两条证据、`delta=2` |
| `dim_conflict_tolerance.json` | 标注 70 / 几何 70.4（容差内） | **不**冲突，`delta=0.4` |
| `unitless_dimensions.json` | `unit_status=needs_confirmation` | 长宽高最高 `needs_confirmation` + 警告 |
| `material_texts.json` | MTEXT 含「材质：白卡纸 350g」「工艺：烫金」 | `inferred_from_text`；字段有证据 |
| `no_material_texts.json` | 只有尺寸文字 | `face_paper` 等保持 `missing`，**不**编造 |
| `hole_plate.json` | 闭合外轮廓 + 两个圆孔 | 边界候选 1、孔 2、孔有证据 |
| `multi_panel.json` | 两个相同闭合外轮廓 | `panel.multi_up=true`、`panel_count=2` |
| `round_outline.json` | 圆形闭合轮廓（圆盘盒形状） | 盒型候选只出 `round_tube` 候选，**不**锁定 |
| `ambiguous_layers.json` | 大量 `unknown` 图层 + 无盒型特征 | `box_candidates` 为空或 `unknown`，`unresolved` 列出 |
| `warnings_passthrough.json` | 带 `conversion_degraded` 与 `unsupported` | 语义层如实透传，不改写成干净 |
| `no_preview.json` | 无预览（默认） | 模型 0 调用，确定性结果仍在 |
| `extra_field_model.json` | 模型额外输出越权字段 | 进 `extra_fields_dropped`，**不**写看板 |

红测分组：`A` 结构/确定性（11）/ `B` 角色与规则（5）/ `C` 轮廓与拼版（5）/ `D` 字段与冲突（10）/
`E` 模型辅助（10）/ `F` 候选与边界（4）/ `G` 幂等与版本（3）/ `H` 错误码（4）/ `I` 安全与文案（5）/
`J` 依赖与真实样本（2）。共 **59** 条。

- 真实样本用例（`J2`）：`裕同包装项目-待开发/*.dwg` 或第 3 批产物存在时才跑；否则
  `skipTest("真实样本转换产物未就绪")`。**只断言结构不变量**（有候选或明确未确认项、
  证据能回查、无幻觉字段），**不硬编码业务尺寸**。
- 小夹具验证算法、真实样本验证兼容性，**两者不得混为一类**。

## 12. 契约 K：真实样本人工金标（不编造尺寸）

- 工具 `tech_app/tools/packaging_semantics_review_pack.py`：对某项目/某份 CAD IR 生成
  **可视化审查包** → `tech_app/tech_data/<project_id>/review/packaging_semantics/<semantics_id>/`，
  含 `report.md` + `summary.json`：①来源摘要（DWG 摘要/DXF sha256/转换器与版本/转换状态与告警计数）
  ②转换预览路径（SVG 或缩略图）③图层清单（名称/颜色/线型/实体数/命中规则/角色）
  ④实体统计 ⑤尺寸文字与归一化 ⑥候选轮廓 ⑦识别出的刀线与压痕线 ⑧字段候选及证据 ⑨未确认项。
- 人工看完后写 `tests/fixtures/real_baselines/<sample>.packaging.golden.json`，字段：
  `source_sha256` / `layer_total` / `cut_layer_total` / `crease_layer_total` /
  `boundary_candidate_total` / `unit_status` / `must_have` / `must_not_have` /
  `unresolved` / `reviewed_by` / `reviewed_at`，并**逐项标注**：已确认事实 / 可接受候选 /
  明确不应出现的错误 / 尚无法确认的字段。
- 金标不存在 → `skipTest("真实基线未人工复核")`；存在 → 只核对金标写明的项，**不许**自动更新 snapshot。
- **禁止**测试作者凭感觉编造「酒盒 = 260×180×80」这类尺寸；也**禁止**用文件名（「酒盒」「圆盘盒」）
  作为盒型或结构参数证据（§5.2 第 6 条）。

## 13. 非目标（本批一律不做）

- 不锁定盒型、不改 `packaging_match` 打分/淘汰/确认、不写 3D 分流。
- 不做 Agent 会话、Tool List 卡片、右侧看板 UI 与同步（第 5 批）。
- 不计算成本、不发布报价、不改成本公式/费率/最低收费口径。
- 不让模型生成正式尺寸、不从像素换算尺寸、不从预览截图反推几何。
- 不引入新依赖（不加 shapely / cairosvg / PIL / dxfgrabber；栅格预览只接受调用方给的图片或
  第 2 批已装工具的产物）。
- 不 commit / push / MR / tag / Release / 部署；不改两份真实 DWG 样本。

## 14. 与需求看板、盒型匹配的接口边界（第 5 批依赖）

| 边界 | 本批提供 | 本批不做 |
| --- | --- | --- |
| 需求看板 | `fields` / `unresolved` / `field_provenance` / `field_sources` 的写入（经 `save_requirement_draft`） | 左 Agent 输出、右看板实时同步、待确认交互 UI |
| 盒型匹配 | `box_candidates` + 候选特征 | 打分、淘汰、人工确认、写 `wip_packaging_box_match` |
| 成本 | 只提供「哪些字段已确认/未确认」 | 任何金额与费率 |
| 版本 | `semantics_id` / `semantics_hash` / `source.ir_hash` / `rules_version` | 下游 stale 标记（第 5 批） |
| 事件（第 5 批要接） | 审计动作名 `packaging_semantics.analyzed` / `.applied` / `.failed` | Agent 事件与 Tool 状态 |

## 15. 自动化验收

| 命令 | 期望 |
| --- | --- |
| `./open-claude/.venv/bin/python tests/test_packaging_semantics_red.py` | 实现前 FAIL（红）；实现后 OK |
| `./open-claude/.venv/bin/python tests/test_dwg_file_capability_preflight_red.py` | 加 2 条新码后先红，实现后 OK |
| `./open-claude/.venv/bin/python tests/test_dxf_cad_ir_red.py` | 不回归（第 3 批） |
| `./open-claude/.venv/bin/python tests/fixtures/cad_ir/build_fixtures.py --check` | 全部夹具为合法 CAD IR 文档 |
| `./open-claude/.venv/bin/python tech_app/tools/packaging_semantics_review_pack.py --help` | 工具可运行 |
| `./open-claude/.venv/bin/python /tmp/run_pkg.py 1` | 除既有失败集合与新红测外不新增失败 |

## 16. 人工验收

- 用 `cut_crease_layers.json`：图层角色按配置命中，无规则图层是 `unknown` 且进 `unresolved`。
- 用 `color_only_layers.json`：默认规则下**不**能解释颜色；只有客户模板配了颜色才判刀线。
- 用 `dim_conflict.json`：标注 70 与实测 72 **同时**出现在冲突条目里，字段不得被确认。
- 用 `unitless_dimensions.json`：长宽高只能是 `needs_confirmation`，看板显示「单位待确认」而不是毫米。
- 用 `no_material_texts.json`：材料字段保持 `missing`，**不**出现任何编造材料。
- 用 `round_outline.json`：只出现 `round_tube` **候选**，`box_type` 未被锁定。
- 真实样本：跑审查包 → 人工复核 → 写 golden；golden 缺失时真实样本用例必须**跳过**并提示
  「真实基线未人工复核」。

# 规格：DXF 确定性解析与统一 CAD IR —— DWG 支持第 3 批

> 批次：包装行业 **DWG 支持第 3 批**（共 6 批）。前置：第 1 批（`docs/specs/dwg-file-capability-preflight.md`）
> 与第 2 批（`docs/specs/dwg-controlled-conversion-adapter.md`）。
> 红测：`tests/test_dxf_cad_ir_red.py`。夹具：`tests/fixtures/dxf/`（19 个小 DXF + 生成脚本）。
> 真实样本：`裕同包装项目-待开发/酒盒.dwg`、`圆盘盒.dwg`（只读）。
> 本批目标：把转换后的 DXF **确定性地**解析成统一 CAD IR，为包装结构分析和字段填充提供可信数据。
> **不判断刀线/压痕线/盒型（第 4 批）、不写需求看板（第 5 批）、不改成本、不调任何模型。**

## 0. 术语与两条 IR 的关系

| 名称 | 含义 |
| --- | --- |
| **CAD IR**（本批） | 图纸的确定性矢量事实：图层 / 实体 / 几何量 / 文字 / 标注 / 证据。二维、无"设计意图" |
| DesignIR（既有 `models/ir.py`） | 设备需求的**设计意图**（零件/特征/材质），由视觉模型 + CAD 内核产出 |
| CAD IR 节点 | CAD IR 中一条可寻址记录（图层、实体、轮廓、标注、文字、警告） |
| 证据引用 | `ev:<handle>` 形式的稳定指针，指向上面任一条记录 |

**两条 IR 并存、互不嵌套**：CAD IR 不带 `parts/features`，DesignIR 不带图层/实体明细。
STEP 流程继续产出 `DesignIR` + 几何结果（`stage="parsed_3d"`），本批**不改**它，也不把 CAD IR
塞进 `DesignIR`。判定口径写在 `CAD IR` 的 `source.kind`：`dxf_2d` / `dwg_2d`。

## 1. 现状取证（实测）

- 全仓**没有任何 DXF 解析**：`grep -rn "ezdxf" --include=*.py .` 只命中第 2 批红测；
  `services/drawing2d.py` 是 OCCT **出**图（导出 SVG/DXF），`services/step_import.py` 只吃 STEP/STP。
- `open-claude/.venv` 里 `ezdxf 1.4.4` 可用、`numpy 2.2.6` 可用、`shapely` 与 `dxfgrabber` **不存在**；
  `requirements.txt` **没有** `ezdxf`（`:14` 只有 `openpyxl==3.1.5`，`cadquery` 在 `:44` 仍被注释）→
  本批必须把 `ezdxf` 正式写进 `requirements.txt`，并且**不引入**新的几何/网络依赖。
- 第 2 批（转换适配器）**尚未实现**：`tech_app/backend/services/cad_converter/` 不存在，
  第 2 批红测 40 条仍红。因此本批的红测分两层：`A–C`/`G–I` 组只依赖 `parse_dxf()` + 夹具（本批独立可验收），
  `D` 组依赖第 2 批的 manifest/产物（第 2 批落地前会以「依赖第 2 批」失败，这是**预期的顺序约束**）。
- 真实样本转换产物**当前不存在**（本机无 DWG 转换器，见第 2 批 Spec §1.1）→ 真实样本用例必须
  `skipTest` 并写明「真实转换产物未就绪」，**不许**用夹具冒充真实样本基线。

## 2. 契约 A：模块结构与对外稳定接口

新增包 `tech_app/backend/services/cad_ir/`（**纯确定性解析，不联网、不调模型、不写 Agent 上下文**）：

```
cad_ir/
  __init__.py        # 对外稳定接口（§2.1）
  model.py           # CAD IR 数据结构 + CAD_IR_VERSION + 规范排序/哈希
  parser.py          # ezdxf → CAD IR（实体、图层、文字、标注、上限、错误）
  geometry.py        # 纯函数几何：长度/面积/包围盒/闭合判定/连通组件/容差
  units.py           # 单位判定（$INSUNITS / 标注 / 标题栏文字 / 候选与置信度）
  blocks.py          # 块与变换（嵌套、镜像、循环保护、深度限制）
  persistence.py     # 落盘（唯一写盘入口，走 store 的 blob/文档约定）
```

### 2.1 对外接口（冻结给第 4 批）

```python
CAD_IR_VERSION = "cad-ir/1"

def capability() -> dict                      # {"available","parser","parser_version","max_entities","max_block_depth","message"}
def parse_dxf(content: bytes, *, filename="drawing.dxf", source=None, limits=None) -> dict
def parse_conversion(project_id, *, conversion_id=None, drawing_version=None, author="system") -> dict
def load_ir(project_id, ir_id=None) -> dict | None
def list_irs(project_id) -> list[dict]        # 时间倒序
def ir_hash(ir: dict) -> str                  # 规范哈希（§3.4）
def migrate(ir: dict) -> dict                 # 版本迁移/重建判定（§6.3）
def summarize(ir: dict) -> dict               # 给 Agent/看板用的安全摘要（不含实体明细）
```

- `parse_dxf()` **不做任何 I/O**：不读库、不写盘、不联网，输入字节、输出 IR（可序列化 dict）。
- `parse_conversion()` 才落盘：从第 2 批 manifest 取 DXF 产物 → `parse_dxf()` → `persistence.save_ir()` →
  `store.audit(project_id, "cad_ir_parsed", {...})`；**失败不许写 IR**、不许把 stage 标成完成。
- `parse_dxf()` 返回的 IR 必须 **JSON 安全**：无 `NaN`/`Infinity`/`bytes`/`Path`/自定义对象
  （`json.dumps(ir, allow_nan=False)` 必须通过）。

## 3. 契约 B：CAD IR 数据结构

```json
{
  "ir_version": "cad-ir/1",
  "ir_id": "<16 hex>",
  "ir_hash": "<sha256 hex>",
  "source": {"kind": "dxf_2d", "project_id": "", "attachment_name": "", "source_sha256": "",
             "conversion_id": "", "dxf_artifact": "", "dxf_sha256": "",
             "converter_name": "", "converter_version": "", "detected_dwg_version": "",
             "drawing_version": 1},
  "parser": {"name": "ezdxf", "version": "1.4.4", "options": {...}},
  "units": {"drawing_units": "mm|inch|unitless|unknown", "unit_status": "confirmed|needs_confirmation|unknown",
            "unit_confidence": 0.0, "scale_to_mm": null,
            "candidates": [{"unit": "mm", "confidence": 0.4, "reason": "标注文字含 mm"}]},
  "document": {"extents": [0,0,0,0], "model_space": {...}, "paper_space": {...},
               "layouts": [{"name": "Layout1", "entity_count": 0}], "dxf_version": "AC1015", "warnings": []},
  "layers": [{"name": "CUT", "visible": true, "frozen": false, "color": 1, "line_type": "CONTINUOUS",
              "entity_count": 1, "inferred_role": null, "role_confidence": null, "evidence_refs": ["ev:L:CUT"]}],
  "entities": [{"entity_id": "ent:model:10", "handle": "10", "type": "LWPOLYLINE", "layer": "0",
                "space": "model", "block_path": [], "closed": true,
                "bbox": [0,0,10,5], "length": 30.0, "area": 50.0,
                "length_mm": 30.0, "attributes": {...}, "evidence_ref": "ev:E:10"}],
  "geometry": {"closed_outlines": [{"outline_id": "out:model:10", "entity_id": "ent:model:10",
                                    "layer": "0", "bbox": [0,0,10,5], "length": 30.0, "area": 50.0,
                                    "length_mm": 30.0, "area_mm2": 50.0, "evidence_ref": "ev:E:10"}],
               "open_outlines": [], "components": [{"component_id": "cmp:1", "entity_ids": ["ent:model:10"],
                                                    "bbox": [0,0,10,5], "closed_cycles": 1}],
               "holes": [{"entity_id": "ent:model:21", "kind": "circle", "center": [20,10],
                          "diameter": 6.0, "radius": 3.0, "evidence_ref": "ev:E:21"}],
               "repeated_groups": [{"signature": "LWPOLYLINE:4:closed", "entity_ids": ["..."], "count": 2}],
               "overlaps": [{"entity_ids": ["ent:a","ent:b"], "bbox": [0,0,1,1], "kind": "bbox_overlap"}],
               "tolerance": 0.01},
  "texts": [{"entity_id": "ent:model:53", "type": "MTEXT", "layer": "PRINT",
             "raw_text": "...", "normalized_text": "材质：白卡纸 350g", "position": [2,22],
             "height": 2.5, "evidence_ref": "ev:E:53"}],
  "dimensions": [{"entity_id": "ent:model:34", "layer": "0", "dim_type": "linear",
                  "raw_text": "70", "normalized_text": "70", "declared_value": 70.0,
                  "measured_value": 72.0, "unit": "unitless", "delta": 2.0, "tolerance": 0.5,
                  "target_entity_ids": ["ent:model:33"], "confidence": 0.8,
                  "evidence_ref": "ev:E:34"}],
  "unsupported": [{"type": "REGION", "count": 1, "handles": ["72"], "warning": "..."}],
  "warnings": [{"code": "unsupported_entity", "message": "...", "evidence_refs": []}],
  "stats": {"entity_total": 1, "layer_total": 6, "closed_outline_total": 1, "open_outline_total": 0,
            "arc_total": 0, "circle_total": 0, "ellipse_total": 0, "spline_total": 0,
            "dimension_total": 0, "text_total": 0, "block_ref_total": 0, "unsupported_total": 0},
  "evidence": {"ev:E:10": {"kind": "entity", "handle": "10", "layer": "0", "spatial": [5, 2.5]}}
}
```

### 3.1 稳定标识（不许用数组下标做长期 ID）

- 实体：`ent:<space>:<handle>`；`handle` 来自 DXF 本体（跨解析稳定）。无 handle 时退化为
  `ent:<space>:<type>:<layer>:<canonical_index>`，`canonical_index` 由 §3.4 规范排序确定，并写进 `warnings`。
- 块内解析出的实体：`block_path` 记录 `[{"block": "OUTER", "insert_handle": "1A"}, ...]`，
  `entity_id` 追加块路径：`ent:model:1A/3F`（父引用 handle + 子实体 handle）。
- 证据：`ev:E:<handle>`（实体）、`ev:L:<layer>`（图层）、`ev:D:<handle>`（标注）、`ev:B:<handle>`（块引用）。

### 3.2 几何口径

- `length`/`area` 用**图纸单位**；`length_mm`/`area_mm2` **只在 `unit_status == "confirmed"` 时**给出，
  否则为 `null` 并追加 `unit_unconfirmed` 警告（§4）。
- 闭合判定：`LWPOLYLINE/POLYLINE` 的 `70` 标志、首尾点在 `tolerance` 内重合、`CIRCLE`/`ELLIPSE` 恒闭合。
- `geometry.closed_outlines` **只收闭合的折线/多段线**；`CIRCLE`/`ELLIPSE`/闭合 `ARC`/`SPLINE` 归
  `geometry.holes`（`kind` = `circle`/`ellipse`/`arc`，带 `radius`/`diameter`/`center`）。
  `geometry.open_outlines` 收 `LINE` 与不闭合的多段线（`LINE` 是单段开放回路）。
- `tolerance` 默认 `1e-9 * max(extent)`（不得小于 `1e-9`），写进 `geometry.tolerance`。
- `area` 只对闭合回路给值；开放回路 `area = null`（**不许**把开口折线当面积）。
- 弧长/圆周长/椭圆周长必须真实计算（`ARC`=r·Δθ，`CIRCLE`=2πr），**不许**用包围盒近似。

### 3.3 图层与角色（本批不做刀线/压痕判断）

- 图层记录必须保留 `name/visible/frozen/color/line_type/entity_count`。
- `inferred_role` 在本批**恒为 `null`**、`role_confidence` 恒为 `null`：刀线/压痕线/出血等角色识别
  属于第 4 批，且必须由**可配置规则**给出。本批不许把 `CUT`/`CREASE` 字样的图层直接当刀线证据。
- 图层名、颜色、线型原样保留即可；颜色编号的**含义**不在本批解释。

### 3.4 规范排序与哈希

- 实体按 `(space, layer, handle_or_canonical_index, type)` 排序；图层按 `name`；文字/标注/证据按 key 排序。
- `ir_hash = sha256(canonical_json(IR 去掉 ir_id/ir_hash/parser.version/时间戳))`。
  → **同一份 DXF，实体书写顺序不同、图层声明顺序不同，`ir_hash` 必须一致**（红测 `A13`）。
- `parse_dxf()` 必须是**纯函数式确定性**：同样输入两次调用，`ir_hash` 完全一致。

### 3.5 实体覆盖清单（本批必须处理的类型）

| DXF 类型 | 去向 | 关键字段 | 说明 |
| --- | --- | --- | --- |
| `LINE` | `entities` + `geometry.open_outlines` | 起止点、`length` | 单段开放回路 |
| `LWPOLYLINE` / `POLYLINE`（含 R12 `VERTEX`/`SEQEND`） | `entities`；闭合 → `closed_outlines`，否则 → `open_outlines` | 顶点、`closed`、`length`、`area` | 闭合判定见 §3.2 |
| `ARC` | `entities` + `geometry.holes(kind="arc")` | `center`/`radius`/起止角、弧长 | 弧长按 r·Δθ |
| `CIRCLE` | `entities` + `holes(kind="circle")` | `center`/`radius`/`diameter` | 周长 2πr |
| `ELLIPSE` | `entities` + `holes(kind="ellipse")` | 长轴/短轴比例、bbox | 真实计算周长（椭圆积分） |
| `SPLINE` | `entities` + `holes(kind="spline")` | 控制点/拟合点、闭合、长度 | 点数不足时 warning |
| `INSERT` / `BLOCK` | `entities`（引用本身）+ 展开后的块内实体 | `block_path`、`stats.block_ref_total` | 变换必须算进绝对坐标（§5） |
| `HATCH` | `entities`，只登记 `attributes.boundary_paths` / `attributes.boundary_loops` | 边界路径数、是否闭合 | **不进** `closed_outlines`/`holes`：填充不是刀线 |
| `TEXT` / `MTEXT` | `texts` | `raw_text`/`normalized_text`/`position`/`height` | 归一化见 §3.6 |
| `DIMENSION` | `dimensions`（同时以 `type="DIMENSION"` 记进 `entities`） | 见 §3.7 | — |
| `LEADER` / `MLEADER` | 解析库给得出就登记 `entities`；给不出引用关系 → warning | — | 不因它整图失败 |
| 其他（`3DFACE`/`REGION`/`SOLID`/…） | `unsupported` + warning | `type`/`count`/`handles` | 只警告，**图不消失**（红测 `A11`） |

- 以上任一类型解析时抛异常：该实体进 `unsupported` + warning，**不许**让整次解析失败（Spec §8 上限除外）。
- HATCH 在真实样本里存在且边界可能不完整（LibreDWG 会打印 `Skip HATCH common handles`）：边界按
  best-effort 登记，缺边界 → warning，不当成解析失败。

### 3.6 文字归一化（两种来源都要读对）

- AutoCAD 系写出的 DXF 把非 ASCII 存成 `\U+XXXX` 转义；**LibreDWG 0.14 转出的 DXF 直接写明文 UTF-8**
  （实测 `酒盒.dwg → dxf` 的 MTEXT 就是 `材质...`/`350g粉灰` 这类明文）。两种都必须读对。
- `normalized_text` = 先解码 `\U+XXXX`（**存在才解**）→ 去掉 MTEXT 格式码（`\f`/`\H…;`/`\C…;`/
  花括号/`\P` 折行）→ `strip()`。
- **禁止二次解码**：对已是明文的字符串再做一次转义解码，或让 `normalized_text` 里出现 `\U+` 字面量，
  都算缺陷（红测 `A16`）。`raw_text` 永远保留原样字符串用于证据回放。

### 3.7 标注（DIMENSION）回落规则

- `measured_value` 来自几何测量（`get_measurement()`），**不许**被标注文字覆盖（红测 `A6`）。
- `declared_value`：标注文字可解析出数值 → 用它；文字为空或解析不出 → **回落到 `measured_value`**
  （真实图纸绝大多数标注没有文字覆盖，实测样本 `raw_text` 为空串；红测 `A17`）。
- `delta = declared_value - measured_value`（回落时恒为 `0.0`）；单位未确认时 `unit = "unitless"`。
- 任一步算不出数值 → 该字段 `null` + warning；**禁止** NaN / Infinity（§8）。

### 3.8 空间口径

- `entities` 与 `stats` **默认只统计 model space**（含块展开出来的实体，`space="model"`）。
- `document.paper_space` 只给 `{entity_count, layouts[]}`，默认**不**并入 `entities`；
  `limits.include_paper_space=True` 时才并入（`space="paper"`）。
- 真实样本 `酒盒.dwg` 的 model space 里 `INSERT` 为 0（719 个块定义未被引用），所以
  **不许**把 `block_ref_total > 0` 写成真实样本的硬性验收条件。

## 4. 契约 C：单位不许猜

| `$INSUNITS` | `drawing_units` | `unit_status` | `scale_to_mm` |
| --- | --- | --- | --- |
| 4 | `mm` | `confirmed` | 1.0 |
| 1 | `inch` | `confirmed` | 25.4 |
| 2（英尺）/5（cm）/6（m） | 对应单位 | `confirmed` | 对应换算 |
| 0 / 缺失 / 未知值 | 按候选**不填结论** | `needs_confirmation` | `null` |

- `$INSUNITS = 0` 或缺失时：可以结合①标注文字后缀（`70mm`）、②标题栏文字（`单位：mm`）、
  ③图幅范围与业务常识给出 `candidates`（带 `confidence`），但
  **`drawing_units` 不得直接变成已确认的 `mm`**，`unit_status` 必须是 `needs_confirmation`，
  `scale_to_mm = null`，`unit_confidence <= 0.5`，并产生 `unit_unconfirmed` 警告。
- 单位未确认时：所有 `length_mm`/`area_mm2` 为 `null`；依赖绝对尺寸的字段（后续批次的长宽高）
  不得被视为已确认。

## 5. 契约 D：块与变换

- 必须支持：平移、旋转、缩放（含负缩放=镜像）、extrusion 翻转、嵌套块、同一块多次引用。
- 计算几何**必须应用完整变换链**（`INSERT` → `BLOCK` → 嵌套 `INSERT`），不许直接读块定义里的原始坐标。
- 镜像：`xscale/yscale < 0` 或 `extrusion = (0,0,-1)`；镜像**不改变面积**，`area` 必须为正值。
- 循环引用保护：块路径上出现重复块名 → 停止递归、记 `block_cycle` 警告、`count` 计入 `unsupported`/`warnings`，
  **不得**死循环、不得抛未捕获异常。
- 深度上限 `CAD_IR_MAX_BLOCK_DEPTH`（默认 8）；超过 → `block_depth_exceeded` 警告 + 截断（记数）。
- 块内实体也要进 `entities`，带 `block_path` 与稳定 `entity_id`（§3.1）。

## 6. 契约 E：错误模型与状态机

### 6.1 错误码（沿用第 1 批权威闭集，本批新增 2 条）

| `stable_error_code` | HTTP | `retryable` | 触发 |
| --- | --- | --- | --- |
| `FILE_CORRUPTED`（复用） | 422 | 是 | DXF 结构损坏/被截断（`ezdxf` 抛结构错误） |
| `DWG_PARSE_FAILED`（复用） | 502 | 是 | 解析过程其他失败（不含算错尺寸） |
| `DWG_CONVERTER_NOT_INSTALLED`（复用） | 415 | 是 | 没有转换产物可解析且转换器未安装 |
| `FILE_TOO_LARGE`（复用） | 413 | 是 | DXF 字节数超过上限 |
| **`CAD_IR_SOURCE_MISSING`**（新） | 422 | 是 | 项目里没有可用的 DXF 产物（manifest 缺失/产物丢失/状态非 ok） |
| **`CAD_IR_ENTITY_LIMIT_EXCEEDED`**（新） | 413 | 否 | 实体数超过 `CAD_IR_MAX_ENTITIES`（**不允许静默截断**） |

- 新增 2 条必须同步加进**第 1 批 Spec §3** 的闭集（第 1 批表是唯一权威），HTTP 与 `retryable` 以本表为准。
- 失败统一 `raise file_preflight.FileCapabilityError(code, detected=..., message=...)`，
  错误信息**不得**包含 ezdxf 原始堆栈、绝对路径、DXF 原文。

### 6.2 状态机

```
无 DXF 产物 ─┬─ 转换器未安装 → DWG_CONVERTER_NOT_INSTALLED（终态，可重试）
             └─ 有其他失败   → CAD_IR_SOURCE_MISSING（可重试，先在 2.x 重跑转换）
DXF 产物 ─ 读字节 ─┬─ 超上限 → FILE_TOO_LARGE
                   ├─ 结构损坏 → FILE_CORRUPTED
                   ├─ 实体超限 → CAD_IR_ENTITY_LIMIT_EXCEEDED
                   └─ 解析 → 规范化 → 校验 → 落盘(ir_revision+1, audit) → ok
```

不变量：
1. 失败**不写 IR**、不改 `stages`、`store.record_version` 不产生新版本（不许把失败当完成）。
2. 同一 DXF 重复解析 → **幂等复用**：`ir_id`/`ir_hash` 相同，不新增版本条目
   （`list_irs()` 长度不变）；`load_ir(project_id, ir_id)` 能按 id 回看。
3. 解析**完全离线**：不调任何模型/网络（红测用补丁断言调用数为 0）。

### 6.3 版本与迁移

- `CAD_IR_VERSION` 是 IR 的契约版本，写进每条 IR；`migrate(ir)`：
  - 缺 `ir_version` → 返回 `{"status": "needs_rebuild", "reason": "missing_ir_version"}`（旧数据重建，不猜）。
  - `ir_version == CAD_IR_VERSION` → `{"status": "ok"}`。
  - 未知/更高版本 → `raise ValueError("unsupported cad ir version")`（**不许**按当前版本硬读）。
- IR 落地路径：`store` 的文档位 `cad_ir`（新 doc key），与 `ir`（DesignIR）**互不覆盖**。

### 6.4 持久化接口（`cad_ir/persistence.py`，唯一写盘入口）

```python
def save_ir(project_id: str, ir: dict) -> dict      # 落 cad_ir 文档位 + 版本快照，返回已保存的 IR
def load_ir(project_id: str, ir_id: str | None = None) -> dict | None
def list_irs(project_id: str) -> list[dict]         # 时间倒序
```

- 文档位用**新的 doc key `cad_ir`**（与 DesignIR 的 `ir` 互不覆盖），并让
  `store.PARSE_STAGE_DOCS` 包含 `cad_ir`（"本次任务从头开始"要能清掉它）。
- `parse_conversion()` 取产物的路径固定为第 2 批的三个函数：
  `cad_converter.capability()` → 判断转换能力；`cad_converter.latest_manifest(project_id)` → 拿最新成功
  manifest；`cad_converter.persistence.artifact_dir(project_id, conversion_id)` → 定位 DXF 文件。
  **不得**自己遍历 `tech_data` 目录猜产物位置。

## 7. 契约 F：证据与可回放

- 每个业务字段将来都要能追到：业务字段 → CAD IR 节点 → DXF 实体 → 转换产物 → 原始 DWG。
  因此 **IR 里每个实体/轮廓/孔/标注/文字都必须带 `evidence_ref`**，且 `evidence` 字典里能查到该 ref。
- `evidence` 条目给出：`kind`（entity/layer/dimension/block_ref）、`handle`、`layer`、
  `spatial`（代表点，便于图纸定位）、可选 `block_path`。
- **不许**把整份 DXF 原文或大段实体 JSON 塞进 `warnings`/`evidence`/Agent 上下文；
  `summarize(ir)` 只输出统计与少量关键项（供第 5 批 Agent 用）。

## 8. 契约 G：性能与安全限制

| 参数（env 可覆盖） | 默认 | 行为 |
| --- | --- | --- |
| `CAD_IR_MAX_ENTITIES` | 200000 | 超限 → `CAD_IR_ENTITY_LIMIT_EXCEEDED` |
| `CAD_IR_MAX_LAYERS` | 5000 | 超限 → 记警告并按上限处理（图层不影响几何正确性） |
| `CAD_IR_MAX_BLOCK_DEPTH` | 8 | 超限截断 + 警告 |
| `CAD_IR_MAX_DXF_BYTES` | 256 MiB | 超限 → `FILE_TOO_LARGE` |
| `CAD_IR_PARSE_TIMEOUT_SECONDS` | 120 | 超时 → `DWG_PARSE_FAILED`（不挂死） |
| `CAD_IR_MAX_EVIDENCE` | 50000 | 超出不再新增证据，记 `evidence_truncated` 警告 |

- 安全：不执行 DXF 里的脚本/宏、不解析外部参照（XREF 只记 `unsupported` + 警告）、不联网。
- 解析器**不得** import `vision` / `qwen_client` / `claude_client` / `step_import`；
  CAD IR 与 DesignIR 代码路径互不依赖（红测 `G1/G2` 用源码扫描锁死）。
- 单次解析峰值内存必须与实体数量线性相关，不得把 `ezdxf` 文档对象留在 IR 结果里。

## 9. 契约 H：测试夹具（`tests/fixtures/dxf/`，19 个）

| 夹具 | 用途 | 关键期望 |
| --- | --- | --- |
| `rect_10x5.dxf` | 闭合矩形基线 | 闭合 1、面积 50、周长 30、bbox `[0,0,10,5]` |
| `hole_plate.dxf` | 闭合外轮廓 + 圆孔 | 闭合 2、孔直径 6 与 3、外框面积 800 |
| `open_polyline.dxf` | 开放折线 + 直线 | 开放 1、闭合 0、折线长 20、面积 `null` |
| `curves_arc_ellipse_spline.dxf` | 弧/圆/椭圆/样条不被丢弃 | 弧长 ≈ 15.707963、圆周长 ≈ 12.566371、椭圆 bbox `[30,40,70,60]`、样条长 > 0 |
| `layers_cut_crease.dxf` | 图层元数据 + 中文 MTEXT | CUT 1 / CREASE 2 / PRINT 1 / FRAME 1；`inferred_role` 为 `null`；MTEXT 归一化为「材质：白卡纸 350g」 |
| `units_mm`（`rect_10x5`） | `$INSUNITS=4` | `unit_status=confirmed`、`scale_to_mm=1` |
| `units_inch.dxf` | `$INSUNITS=1` | `confirmed`、`scale_to_mm=25.4`、10 单位 → 254 mm |
| `units_unitless.dxf` | `$INSUNITS=0` + 文字含 mm | `needs_confirmation`、`scale_to_mm=null`、`length_mm=null`、有候选 |
| `r12_polyline.dxf` | R12 `POLYLINE/VERTEX` | 三角面积 50、周长 ≈ 34.142136 |
| `block_rotated.dxf` | 平移 + 旋转 | 块内矩形 bbox `[95,0,100,10]` |
| `block_nested.dxf` | 嵌套块 + 多次引用 | 解析出 4 个内层矩形，并集 bbox `[-2,0,64,74]` |
| `block_mirror.dxf` | 负缩放镜像 | 面积仍为 51、bbox 与未镜像引用不同、宽高仍 10×10 |
| `block_mirror_flip.dxf` | extrusion 翻转镜像 | 同上（只断言尺寸/面积不变） |
| `block_cycle.dxf` | 块循环引用 | 不死循环、有 `block_cycle` 警告、实体数有界 |
| `dims_override.dxf` | 标注文字覆盖 | `declared_value=70`、`measured_value≈72`、`delta≈2`；TEXT 归一化「单位：mm」 |
| `unsupported_entities.dxf` | 不支持的实体 | `unsupported` 含 `REGION`、有警告、矩形仍被解析 |
| `many_entities.dxf` | 实体上限防护 | 200 条 LINE；`CAD_IR_MAX_ENTITIES=50` → `CAD_IR_ENTITY_LIMIT_EXCEEDED` |
| `order_a.dxf` / `order_b.dxf` | 顺序无关的稳定 ID 与哈希 | 两份 `ir_hash` 相同、`entity_id` 集合相同 |
| `broken.dxf` | 损坏文件 | `FILE_CORRUPTED`（可重试），不写 IR |

- 生成脚本 `tests/fixtures/dxf/build_fixtures.py` **只在夹具需要变更时手工运行**；测试直接读 `.dxf`。
- 简单夹具是手写 ASCII DXF（AC1015/AC1009，中文用 `\U+XXXX` 转义，与真实 CAD 导出一致）；
  块/镜像/标注夹具用 `ezdxf` 生成（生成期依赖 ezdxf，运行期不需要）。
- **小夹具验证算法，真实样本验证兼容性，两者不得混为一类**（真实样本断言只做结构不变量，不硬编码业务尺寸）。

## 10. 契约 I：真实样本基线（生成 + 人工复核）

- 工具 `tech_app/tools/dxf_ir_review_pack.py`：对给定的项目/转换产物（或直接给一个 DXF）生成
  **可视化审查包**：①来源摘要（sha256/版本/转换器）②图层清单（颜色/线型/实体数）③实体统计
  ④尺寸文字与归一化结果 ⑤候选闭合轮廓 ⑥单位判定与候选 ⑦警告；输出到
  `tech_app/tech_data/<project_id>/review/cad_ir/<ir_id>/`（含 `report.md` + `summary.json`），
  **不再生成第二套几何结论**。
- 人工看完审查包后写 `tests/fixtures/real_baselines/<sample>.golden.json`，字段：
  `source_sha256` / `detected_dwg_version` / `converter_name` / `layer_total` / `entity_total` /
  `unit_status` / `closed_outline_total` / `must_have` / `must_not_have` / `unresolved` / `reviewed_by` / `reviewed_at`。
- 红测行为：`golden` 不存在 → `skipTest("真实基线未人工复核")`；存在 → 逐条核对
  （且**只核对金标里写明的项**，不许自动更新 snapshot）。
- **禁止**在不知道真实内容的情况下硬编码"酒盒 = 260×180×80"这类业务尺寸。

## 11. 与既有 STEP IR / 2D 出图的复用与隔离

- 复用：`file_preflight.FileCapabilityError`/`STABLE_ERROR_CODES`、`store` 的文档位与 blob 约定、
  `store.audit`、`FieldEvidence`（第 4 批用）、第 2 批 `manifest`。
- 隔离：**不复用** `models/ir.py` 的 `DesignIR`；CAD IR 不参与 `save_ir()`（会触发下游
  `invalidate_confirmations`），只走 `cad_ir` 文档位与自己的 `ir_revision`。
- `services/drawing2d.py`（OCCT 出图）与 `services/geometry.py`（3D 几何）**不改**。
- STEP 3D 流程保持 `stage="parsed_3d"`、`save_geometry_result`、`save_drawings_result` 原样。

## 12. 非目标（本批一律不做）

- 不判断刀线/压痕线/半切/开槽/糊口/插舌/盒型/长宽高（第 4 批）。
- 不写需求看板、不生成字段候选、不碰 Agent 会话（第 5 批）。
- 不实现 DWG→DXF 转换（第 2 批）、不改 2D/3D 分流状态机（第 6 批）。
- 不调任何模型、不从预览 PNG 换算尺寸、不把 DXF 原文塞进 Agent 上下文。
- 不改包装成本公式/费率/最低收费口径；不改三个非包装行业链路。
- 不引入 `shapely`/`dxfgrabber`/`pythonocc` 等新依赖；除 `ezdxf` 外不加新依赖。
- 不 commit / push / MR / tag / Release / 部署；不改两份真实 DWG 样本。

## 13. 第 4 批所依赖的稳定接口（本批冻结）

| 接口/字段 | 第 4 批用法 |
| --- | --- |
| `parse_conversion(project_id)` / `load_ir(project_id)` | 拿最新 CAD IR |
| `ir_id` / `ir_hash` / `ir_version` | 字段证据的版本锚点 |
| `entities[].entity_id` / `limit`/`bbox`/`closed`/`layer` | 几何规则（刀线/压痕）判定输入 |
| `geometry.closed_outlines` / `open_outlines` / `holes` / `components` | 展开轮廓与外轮廓候选 |
| `layers[]`（name/color/line_type/entity_count） | 图层规则配置的匹配输入；`inferred_role` 由第 4 批填 |
| `dimensions[].declared_value` / `measured_value` / `delta` / `tolerance` | 标注-几何冲突（conflict）判定 |
| `texts[].normalized_text` / `layer` | 标题栏/材料文字抽取 |
| `units.unit_status` / `drawing_units` / `scale_to_mm` | 绝对尺寸是否可用于确认 |
| `evidence`（`ev:*`） | 每个字段的证据引用 |
| `warnings` / `unsupported` | 不确定项与"看不出来的东西"清单 |
| `summarize(ir)` | 第 5 批 Agent 只用摘要，不把实体明细塞进上下文 |

## 14. 自动化验收

| 命令 | 期望 |
| --- | --- |
| `./open-claude/.venv/bin/python tests/test_dxf_cad_ir_red.py` | 实现前 FAIL（红）；实现后 OK |
| `./open-claude/.venv/bin/python tests/fixtures/dxf/build_fixtures.py --check` | 19 个夹具全部可被 ezdxf 读回（`broken.dxf` 除外） |
| `./open-claude/.venv/bin/python tests/test_dwg_file_capability_preflight_red.py` | OK（不回归） |
| `./open-claude/.venv/bin/python tests/test_dwg_conversion_adapter_red.py` | 第 2 批实现后应 OK；未实现时 `D` 组按预期红 |
| `./open-claude/.venv/bin/python tech_app/tools/dxf_ir_review_pack.py --help` | 工具可运行（真实样本审查包） |
| `./open-claude/.venv/bin/python /tmp/run_pkg.py 1` | 除既有失败集合外不新增失败 |

## 15. 人工验收

- 用 `rect_10x5.dxf` 走一遍：IR 里能看到 1 个闭合轮廓、面积 50、证据可点回实体 `ent:model:10`。
- 用 `dims_override.dxf`：IR 里 `declared_value=70` 与 `measured_value=72` **同时存在**、`delta=2`，
  且没有任何字段把 70 当几何实测值。
- 用 `units_unitless.dxf`：`unit_status=needs_confirmation`，`length_mm` 为空，
  界面上必须显示"单位待确认"而不是直接给毫米尺寸。
- 用 `block_nested.dxf`：4 个内层矩形都在，坐标是变换后的绝对坐标（不是块定义里的原始坐标）。
- 第 2 批装好真实转换器后：跑 `dxf_ir_review_pack.py` 生成审查包，人工复核后写 golden；
  在 golden 缺失的情况下，真实样本用例必须**跳过**并明确提示"真实基线未人工复核"。

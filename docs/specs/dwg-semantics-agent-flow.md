# 规格：DWG 图纸解析会话、右侧需求看板与包装业务流程贯通 —— DWG 支持第 5 批

> 批次：包装行业 **DWG 支持第 5 批**（共 6 批）。前置：第 1 批（`docs/specs/dwg-file-capability-preflight.md`）、
> 第 2 批（`docs/specs/dwg-controlled-conversion-adapter.md`）、第 1/2 批修复
> （`docs/specs/dwg-conversion-quality-repair.md`）、第 3 批（`docs/specs/dxf-cad-ir.md`）、
> 第 4 批（`docs/specs/packaging-drawing-semantics.md`）。
> 红测：`tests/test_packaging_drawing_flow_red.py`。
> 真实样本：`裕同包装项目-待开发/酒盒.dwg`、`圆盘盒.dwg`（只读、不入库）。
> 本批目标：把「DWG 转换 + CAD IR + 包装语义 + 字段候选」这串**已经存在的事实**，按**一条受控步骤链**
> 贯通到 **Agent 会话 / 右侧需求看板 / 盒型- BOM-路线-成本-报价** 五段下游，并给每一步配上
> **版本锚点、门禁和 stale 传播**。
> **不新增第二套 Tool List 契约、不新增第二条气泡路径、不改成本公式、不发布报价、不做 2D/3D 分流（第 6 批）。**

## 0. 先说清：本批"复用"什么、"新增"什么

第 5 批最容易犯的错是**再造一套**。下面这张表是硬边界：左边一律复用，只有右边是新增。

| 能力 | 归属 | 第 5 批的动作 |
| --- | --- | --- |
| 统一执行卡 / Tool List DOM / 五态 / 排序去重 | `docs/specs/quote-tech-unified-tool-list-and-conversation.md`、`tech-session-timeline.js`、`agent-chat.js` | **只复用**：新事件用既有 `kind`，不新增构造器、不新增 class |
| 用户气泡（`prompt` + `runId` + 落库 `kind:"user"`） | `docs/specs/tech-agent-echo-bubble-and-single-exec-card.md` C1–C6 | **只复用**：本批不新增气泡入口，只在服务端补一条**幂等**的用户条目（同 key 就地更新） |
| 会话时间线持久化（`seq` / `key` 幂等 / `task.id` 一张卡） | `store.append_session_event` / `load_session_events`（`store.py:1361-1455`） | **只复用**：全部经它写入，不自建存储 |
| 需求字段来源合并（`field_sources` 5 值闭集） | `requirement_service.merge_field_sources`（`:46`）、`save_requirement_draft`（`:124`） | **只复用**：不新增 `FIELD_SOURCES` 取值 |
| 字段证据 / 冲突 / 未确认不写值 | `docs/specs/packaging-drawing-semantics.md` §5、`apply_to_requirement` | **只调用**：本批只决定"什么时候调、调完播哪条会话事件" |
| 盒型匹配 / BOM / 路线 / 成本 / 报价回传 | `packaging_match` / `packaging_bom` / `packaging_route` / `packaging_cost` / `packaging_handoff` | **只在结果里追加 `source_versions`**，不改打分、不改公式、不改确认位语义 |
| 最低收费口径未裁决 | `docs/specs/packaging-cost-minimum-charge.md`（`status="pending"`） | **新增**一个只读门禁结论（本批不裁决口径） |
| 会话写权限（工艺侧 + 财务） | `auth.SESSION_WRITE_ROLES`（`auth.py:61`） | **只复用**：不新增角色、不放宽 `/agent/send` |
| **图纸解析步骤链服务** | —— | **新增** `tech_app/backend/services/packaging_drawing_flow/` |
| **门禁矩阵 + `require_gate`** | —— | **新增**（本批核心） |
| **版本锚点 + stale 传播** | —— | **新增**（本批核心） |
| **2D/3D 分流** | —— | **不做**，第 6 批 |

## 1. 现状取证（实测，9-20）

- **七步链路服务不存在**：`tech_app/backend/services/` 下无 `packaging_drawing_flow*`；
  全仓 `grep -rn "file_preflight.*dwg_convert\|drawing_flow" tech_app/backend/` 无命中。
  现在 DWG 在原图位置只会被第 1 批的门禁**拒掉**（`vision_gate_error`），没有任何"先转换再解析"的编排。
- **Agent 会话与看板是两套并行事实**：`oc_agent` 只有 8 个 `tech_ui` action 白名单（`oc_agent.py:85`）
  与平台工具；`/agent/event` 只做"入队 + 幂等"（`main.py:2498`）。**没有任何领域步骤链**告诉会话
  "现在轮到 DWG 转换 / CAD 解析 / 字段写入"。
- **门禁基本只有 `project exists`**：`/upload-3d`、`/parse` 各自只判项目与原图；`packaging_*` 五段
  各判自己的前置（`packaging_match._require_packaging`、`packaging_route` 缺口、`handoff` 缺口），
  **没有**统一的"哪些字段确认后才能盒型匹配 / BOM / 路线 / 成本 / 正式报价"矩阵。
- **`minimum_charge_policy` 完全没有门禁**：`grep -rn "minimum_charge_policy" tech_app/backend/`
  只命中 `packaging_cost.py`（读取与标注），**没有**任何一处用它拦下游。
  实测 `packaging_cost_rules.json` → `minimum_charge_policy.status == "pending"`。
- **版本继承六元组不存在**：`grep -rn "source_versions\|requirement_snapshot_version" tech_app/backend/`
  **零命中**。`packaging_handoff.handoff_package()`（`:139`）只带 `box_type.confirmed_by/at` 与
  `result_version`，BOM / 路线 / 成本各自没有"我是在哪一版图纸、哪一版 IR、哪一版需求快照上算出来的"。
- **图纸维度的 stale 不存在**：`packaging_match.load_box_match`（`:465-501`）与
  `packaging_route._stale_reasons`（`:398`）都只比**需求字段**，没有任何一处比"图纸版本 / IR 哈希"。
  第 2 批 `cad_converter.service` 已提供转换产物级 `stale_reason`（`service.py:873-905`），
  但**没有向业务段传播**。
- **第 3 批与第 4 批的落地状态（实测，本文件写作时）**：`packaging_semantics/` 已被并行实现方落地
  （全量红测 57 → 3），`cad_ir/` **尚未**出现（`test_dxf_cad_ir_red.py` 仍 42 红）。
  因此本批红测**不许把第 3/4 批的落地与否当成自己的验收条件**：链路测试一律通过依赖缝注入 fake
  （§2.3），只有 `I` 组 3 条用例显式说明依赖现状。

## 2. 契约 A：模块结构与对外稳定接口

新增包 `tech_app/backend/services/packaging_drawing_flow/`（纯编排，**不含**转换/解析/几何实现）：

```
packaging_drawing_flow/
  __init__.py      # 对外稳定接口（§2.1）
  model.py         # FLOW_VERSION / 步骤闭集 / 状态闭集 / 计划表（steps()）与规范哈希
  steps.py         # 七个步骤的执行体（每个只调依赖缝，不自己实现几何或转换）
  gates.py         # 门禁矩阵（§5）与 require_gate()
  anchor.py        # 版本锚点、requirement_snapshot_version、stale 传播（§6）
  persistence.py   # 落盘（唯一写盘入口，§2.4）
```

### 2.1 对外接口（冻结给第 6 批）

```python
FLOW_VERSION = "packaging-drawing-flow/1"

STEP_IDS = ("file_preflight", "dwg_convert", "cad_ir_parse", "packaging_semantics",
            "field_write", "pending_confirm", "downstream_prepare")
STEP_TITLES = {"file_preflight": "文件预检", "dwg_convert": "DWG 转换",
               "cad_ir_parse": "CAD 矢量解析", "packaging_semantics": "包装语义识别",
               "field_write": "字段写入", "pending_confirm": "待确认生成",
               "downstream_prepare": "后续任务准备"}
STEP_STATUSES = ("pending", "running", "completed", "failed", "blocked",
                 "unavailable", "skipped")
FIELD_BOARD_STATES = ("written", "pending", "conflict", "missing", "preserved", "skipped")
GATE_STAGES = ("box_match", "bom", "route", "cost", "quote_draft", "quote_publish")

def capability() -> dict          # {"available","version","steps","dependencies":{name:bool},"message"}
def steps() -> list               # [{"step_id","title","index","depends_on","produces"}]（顺序即执行顺序）
def run_id_for(project_id, *, prompt="", source_sha256="", drawing_version=0,
               requirement_snapshot_version="") -> str
def start(project_id, *, prompt, actor="system", run_id="") -> dict
def run_step(project_id, step_id, *, actor="system", run_id="", retry_of="") -> dict
def run_flow(project_id, *, prompt="", actor="system", run_id="", deps=None) -> dict
def flow_state(project_id) -> dict
def gates(project_id, *, stage="") -> dict
def require_gate(project_id, stage, *, actor="") -> dict
def inheritance(project_id, *, stage="") -> dict
def current_anchor(project_id) -> dict
def requirement_snapshot_version(project_id) -> str
def stale_view(project_id) -> dict
def mark_downstream_stale(project_id, reasons, *, actor="system") -> dict
def clear_downstream_stale(project_id, stage, *, actor="system") -> dict
def summarize(state: dict) -> dict
def migrate(doc: dict) -> dict

class DrawingFlowError(Exception):            # 携带 stable_error_code / http_status / retryable / message
    ...
```

### 2.1.1 返回形状（冻结；红测逐键断言）

- `start()` → `{"run_id": "<16 hex>", "user_entry": {...}, "anchor": {...}}`
- `run_flow()` / `flow_state()` → **同一个形状**：

```json
{"flow_version": "packaging-drawing-flow/1", "project_id": "", "run_id": "",
 "status": "pending|running|completed|failed",
 "started_at": "", "finished_at": "",
 "anchor": {…§6.2…},
 "steps": [{"step_id": "dwg_convert", "title": "DWG 转换", "status": "completed",
            "attempt": 1, "key": "flow:<run_id>:dwg_convert", "seq": 4,
            "started_at": "", "finished_at": "", "error_code": "", "error_message": "",
            "retryable": false, "detail": {…}}],
 "fields": {"inner_length": {"key": "inner_length", "board": "written", "origin": "",
            "status": "", "value": null, "unit": "", "confidence": 0.0,
            "evidence_refs": [], "conflicts": [], "ir_id": "", "ir_hash": ""}},
 "pending": {"needs_confirmation": [], "conflict": [], "missing": [], "total": 0},
 "downstream": {"box_match": {"status": "blocked", "blocking": [], "warnings": []},
                "bom": {}, "route": {}, "cost": {}, "quote_draft": {}, "quote_publish": {}},
 "gates": {…同 gates(project_id)["stages"]…},
 "stale": {…同 stale_view(project_id)…}}
```

- `run_step()` → **该步的 `steps[]` 条目**（含 `attempt` / `status` / `error_code`）。
- `flow_state()` 的 `steps` 顺序恒等于 `STEP_IDS`；`fields` 按 key 排序（规范哈希要求）。
- `gates()` → `{"project_id", "flow_version", "requirement_snapshot_version", "stages": {…}}`；
  `gates(stage=X)` → 该段的条目（`{"stage","status","requires","blocking","warnings","snapshot"}`）。
- `require_gate()` 通过时返回该段条目；被拦时 `raise DrawingFlowError`。
- `stale_view()` → §6.3；`inheritance()` → §6.1；`summarize()` → 小摘要（§2.1）。
- `mark_downstream_stale()` → 写后的 `stale_view()`；`clear_downstream_stale()` → 同。
- 每一步的 `detail` 至少含该步"产出了什么"的安全字段；`field_write` 的 `detail` **必须**含
  `{"fields": {<key>: {<board 结论>}}, "written": [], "pending": [], "conflict": [],
  "missing": [], "preserved": []}`。

- `run_flow()` 是**唯一**按顺序跑七步的入口；`run_step()` 跑一步，返回**该步**的条目
  （不是链尾那一步）；带 `retry_of` 且该步成功后，**继续按顺序跑完剩余仍为 `pending` 的步**
  （§7），返回的仍是 `retry_of` 指定那一步的条目（红测 `E23`）。
- `flow_state()` / `gates()` / `stale_view()` **只读**，不得有副作用（不写盘、不建会话条目）。
- `summarize(state)` 是给 Agent 上下文用的**小摘要**（统计 + 待确认清单 + 门禁结论），
  **不得**包含实体明细、DXF 原文、预览 Base64（红测 `A12`）。

### 2.2 依赖只经一个缝

```python
def _dependency(name: str):
    """惰性取依赖模块：file_preflight / cad_converter / cad_ir / packaging_semantics /
    requirement_service。未安装返回 None —— 不抛异常、不 import 报错。"""
```

- **九个**名字的闭集固定（红测 `A6` 用 `set()` 逐名比对，多一个少一个都算越界）：

```python
DEPENDENCIES = ("file_preflight", "cad_converter", "cad_ir", "packaging_semantics",
                "requirement_service", "packaging_match", "packaging_bom",
                "packaging_route", "packaging_cost")
```

- `capability()["dependencies"]` 逐名给出 `bool`，**必须与真实可导入状态一致**
  （红测 `I1`：不许粉饰"已支持"）。
- 依赖缺失时对应步骤状态是 `unavailable`（**不是** `failed`、更不是 `completed`），
  `detail.dependency` 写明是哪一个（红测 `A9`）。
- `run_flow(..., deps={...})` 允许显式注入（红测用 fake）；**生产路径必须不传**，
  由 `_dependency` 解析真实模块。

#### 2.2.1 缝的调用面（冻结：实现只许调这几个函数，多调一个就是越界）

| 依赖名 | 允许调用的函数 | 返回值用途 |
| --- | --- | --- |
| `file_preflight` | `detect_file_format(filename, content)` | 第 1 步的 `detail`（真实格式 / DWG 版本 / sha256 / 尺寸 / 截断） |
| `cad_converter` | `convert_drawing(project_id, filename, content, drawing_version=…)`；`latest_manifest(project_id)` | 第 2 步的 manifest（`conversion_id`/`status`/`quality`/`warning_count`/`error_count`/`drawing_version`） |
| `cad_ir` | `parse_conversion(project_id, drawing_version=…)`；`load_ir(project_id, ir_id=None)`；`summarize(ir)` | 第 3 步的 IR 与摘要（`ir_id`/`ir_hash`/`ir_version`/`unit_status`） |
| `packaging_semantics` | `analyze_conversion(project_id, ir=…)`；`apply_to_requirement(project_id, semantics, accept=(), author=…)`；`summarize(semantics)` | 第 4/5 步的语义文档与看板写入 |
| `requirement_service` | `merge_field_sources(existing, incoming)`；`save_requirement_draft(project_id, doc)` | 需求单保存沿用既有服务（`save_requirement_draft` 自身会走 `store.save_requirement` 并做来源合并） |
| `packaging_match` | `load_box_match(project_id, requirement_no="")` | 门禁判"盒型是否已确认"、`stage_chain` 的盒型段 |
| `packaging_bom` | `load_bom(project_id, requirement_no="")` | 门禁判"BOM 是否已生成"、`stage_chain` 的 BOM 段 |
| `packaging_route` | `load_route(project_id, requirement_no="")`；`route_versions(project_id, requirement_no="")` | 门禁判"路线是否已确认"、`stage_chain` 的路线段（取最新一版） |
| `packaging_cost` | `load_cost(project_id, requirement_no="")`；`result_version_of(cost)`；`minimum_charge_policy()` | 门禁判"成本/缺口/最低收费口径"、`stage_chain` 的成本段 |

- **需求单的读取不走依赖缝**：读 `requirement.data` / `field_sources` / `field_provenance` /
  `waivers` 一律用 `store.load_requirement(project_id)`（`store.py:969`）——
  `requirement_service` 里没有 `load_requirement()`，红测 `G27`/`C*` 注入的就是这个真模块，
  调错名字会直接 `AttributeError`。
- 上表九个依赖都是**只读优先**：流层只调这一列的函数（外加 §6.1 的 `source_versions` 埋点，
  那是改既有引擎，不在流层调用面内）。任何 `getattr` 前必须判空——依赖没装/没实现时当空值，
  不许把 `AttributeError` 抛给用户。

- `deps={...}` 的键就是 §2.2 的九个依赖名；`run_flow(..., deps={"cad_ir": FakeCadIr()})`
  只覆盖指定依赖，其余仍走 `_dependency`。测试用的 fake 必须与真实模块**同名同参**，
  否则第 6 批换回真实实现时契约会漂移。
- 流层**不得**自己读写 DXF 字节、不得自己解析实体、不得自己算几何：
  `grep -n "ezdxf\|struct.unpack\|subprocess"` 在 `packaging_drawing_flow/` 里必须零命中。

### 2.3 禁止

- 模块**不得** `import vision`、`import step_import`、`import requests`；
  不得直接 `subprocess` 调转换器（一律经 `cad_converter`）；不得自己读 DXF 字节。
- 不得把 `cad_ir` / `packaging_semantics` 的内部结构再实现一遍（不得出现 `ezdxf` import）。
- **顶层 import 是白名单**（红测 `A10` 用 AST 逐个 import 名比对，不在名单里就红）：

```python
{"__future__", "collections", "copy", "datetime", "hashlib", "importlib", "json", "os",
 "pathlib", "re", "time", "typing", "unicodedata", "uuid"}   # + 包内相对 import + tech_app.*
```

  也就是说：**不要**在这个包顶层 `import logging` / `import sys` / `import threading`
  （`threading` 的并发语义由 `store` 的 `_document_lock` 负责，红测 `E26` 只在外部起两个线程）。
  要审计就写 `store.audit(...)`，要报错就用 `DrawingFlowError`。
- 红测 `A10` 同时用 AST 扫 import 名 + 源码禁用词双重锁死（`ezdxf` / `struct.unpack` /
  `subprocess` / `UploadFile` / `requests.` 一律不许出现）。

### 2.4 持久化（唯一写盘入口）

`persistence.py` 必须导出，且包级函数一律**转调**它们（红测在 persistence 层注入内存实现）：

```python
def save_flow(project_id: str, flow: dict) -> dict
def load_flow(project_id: str) -> dict | None
def save_anchor(project_id: str, anchor: dict) -> dict
def load_anchor(project_id: str) -> dict | None
def save_stale(project_id: str, stale: dict) -> dict
def load_stale(project_id: str) -> dict | None
```

新增三个文档位，并全部进 `store.PARSE_STAGE_DOCS`（`/agent/new`「本次任务从头开始」要能清掉）：

| doc key | 内容 |
| --- | --- |
| `packaging_drawing_flow` | 本次 run 的步骤状态、字段看板状态、门禁结论（`flow_state` 的来源） |
| `packaging_flow_anchor` | 版本锚点（§6.1） |
| `packaging_downstream_stale` | stale 传播结论（§6.3） |

## 3. 契约 B：七步链路与状态机

| # | step_id | 输入 | 输出（`detail`） | 依赖 | 成功判定 |
| --- | --- | --- | --- | --- | --- |
| 1 | `file_preflight` | 项目原图字节 | `detected_format` / `dwg_version` / `sha256` / `file_size` / `is_empty` | 第 1 批 | `detected_format in ("dwg","dxf")` 且非空、非截断 |
| 2 | `dwg_convert` | 上面的事实 | 第 2 批 manifest：`conversion_id` / `status` / `quality` / `warning_count` / `error_count` / 产物名 | 第 2 批 | `status in ("ok","success_with_warnings")` 且 `quality.verified`（或字节级检查通过） |
| 3 | `cad_ir_parse` | 转换产物 | `ir_id` / `ir_hash` / `ir_version` / `layer_total` / `entity_total` / `unit_status` | 第 3 批 | IR 落盘成功 |
| 4 | `packaging_semantics` | CAD IR | `semantics_id` / `semantics_hash` / `stats` / `unresolved_total` | 第 4 批 | 语义文档落盘成功 |
| 5 | `field_write` | 语义 `fields` | 逐字段 `board` 状态（`written`/`pending`/`conflict`/`missing`/`preserved`） | 第 4 批 + `requirement_service` | `apply_to_requirement` 未抛错 |
| 6 | `pending_confirm` | 语义 `unresolved` + `needs_confirmation`/`conflict` | 待确认清单（字段 + 原因 + 证据） | —— | 恒 `completed`（清单可以为空） |
| 7 | `downstream_prepare` | `gates()` | 五段下游每段 `open`/`blocked` + 中文原因 | 既有包装五引擎 | 恒 `completed`（`blocked` 是结论不是失败） |

### 3.1 状态机

```text
start(prompt) ── ensure_user_entry(幂等) ──▶ file_preflight
 file_preflight:
   ├─ 非 dwg/dxf（unsupported / raster / step / pdf…）→ failed(FILE_FORMAT_UNSUPPORTED) 终态
   ├─ 空 / 截断                                  → failed(FILE_EMPTY / FILE_CORRUPTED) 终态
   ├─ dxf                                        → dwg_convert = skipped(dxf_no_conversion_needed)
   └─ dwg                                        → dwg_convert
 dwg_convert:
   ├─ 依赖缺失            → unavailable(PACKAGING_FLOW_DEPENDENCY_MISSING, detail.dependency)
   ├─ 转换失败/超时        → failed(第 2 批既有稳定码)
   ├─ 产物不合格           → failed(DWG_CONVERTER_OUTPUT_INVALID 等)
   └─ ok|success_with_warnings → cad_ir_parse
 cad_ir_parse:
   ├─ 依赖缺失 → unavailable；├─ 无产物 → failed(CAD_IR_SOURCE_MISSING)
   └─ ok → packaging_semantics
 packaging_semantics:
   ├─ 依赖缺失 → unavailable；├─ 失败 → failed(第 4 批既有稳定码)
   └─ ok → field_write
 field_write ──▶ pending_confirm ──▶ downstream_prepare ──▶ done
```

### 3.2 不变量（全部可断言）

1. **每一步恰一条会话条目**：`kind="task"` + `key=f"flow:{run_id}:{step_id}"`；
   同 key 重复写入是**就地更新**（`store.append_session_event` 既有语义），不会多出一张卡。
2. **顺序**：同一 run 内，七个步骤条目的 `seq` **严格递增**，且与 `STEP_IDS` 顺序一致。
3. **失败即停**：某步 `failed` / `unavailable` 时，其后所有步骤保持 `pending`，`flow.status="failed"`。
4. **`blocked` ≠ `failed`**：`downstream_prepare` 恒 `completed`，被门禁拦住的下游只记在
   `gates()` 与 `flow.downstream` 里；它**不能**把整条 run 变成失败。
5. **不假装**：任何一步 `unavailable`/`failed` 时，`flow.status` 不许是 `completed`，
   且 **不得**写 `stages.parsed`、**不得**把 `parse` 任务标成完成（第 3 批同款不变量）。
6. 全链路**零模型调用**（第 4 批 `model_assist` 默认关闭；红测 `A11` patch `claude_client.run` 断言 0 次）。

## 4. 契约 C：会话事件与右侧看板更新契约

### 4.1 事件 kind 闭集（**不新增**）

只用既有三个代表（`agent-chat.js:249-300` 的 `renderHistory` 已支持，前端**零改动**）：

| kind | 用途 | 关键字段 |
| --- | --- | --- |
| `user` | 用户气泡（服务端幂等副本） | `text` / `key=f"flow:user:{run_id}"` / `source="shell"` / `stage="drawing"` |
| `task` | 七步执行卡（Tool List） | `task={"id":"flow:<run_id>:<step_id>","label":中文步骤名,"status","steps":[...],"error"}` |
| `session-note` | 字段写入 / 待确认的过程行 | `text`（中文一句话）+ 附加结构键 `field` |

- **禁止**新增 `flow_step` / `flow_field` 之类新 kind：那会要求前端再造一套渲染，等于第二套契约。
- `task.id` 用 `flow:<run_id>:<step_id>`（**每个 run 每个步骤一张卡**）；同一 run 重复执行同一步 = 就地更新。

### 4.2 `task` 条目的 `status` 映射（沿用既有五态）

| 步骤状态 | 卡片状态 |
| --- | --- |
| `pending` | `pending` |
| `running` | `running` |
| `completed` | `completed` |
| `blocked` | `completed`（结论是"待确认"，不是失败） |
| `failed` | `failed` |
| `unavailable` | `failed` + `task.error` = 中文原因（不暴露堆栈） |
| `skipped` | `completed` + 进度行写"已跳过：无需转换" |

### 4.3 字段写入与看板的**逐步**同步

`field_write` 步骤内，**每处理一个字段就立即**：

1. 调 `packaging_semantics.apply_to_requirement(project_id, semantics, accept=(), author=actor)`
   —— 一次调用即可，但**必须在写盘之后**才播事件（顺序不能反）；
2. 追加一条 `kind="session-note"`，`key=f"flow:{run_id}:field:{field_key}"`，`text` 是中文一句话，
   并带结构键：

```json
{"kind": "session-note", "source": "flow", "stage": "drawing",
 "key": "flow:<run_id>:field:inner_length", "seq": 0,
 "text": "内长：已按图纸标注写入 70.0 mm（来源：CAD 标注）",
 "field": {"key": "inner_length", "board": "written", "origin": "confirmed_from_cad",
           "status": "confirmed", "value": 70.0, "unit": "mm", "confidence": 1.0,
           "evidence_refs": ["ev:D:34"], "conflicts": [],
           "requirement_no": "REQ-1"}}
```

`board` 映射（**不许自行发挥**）：

| 语义字段状态 | `board` | 看板效果 |
| --- | --- | --- |
| `status=confirmed` 且单位已确认 | `written` | 正常运行写入 + 显示来源 |
| `field_sources[field]=="manual"` 或 `origin=="user_confirmed"` | `preserved` | **不覆盖**用户确认值 |
| `status=needs_confirmation` | `pending` | 待确认状态 |
| `status=conflict` | `conflict` | 两组证据、阻止自动确认 |
| `status=missing` | `missing` | 生成待补项 |
| 字段不在本次包装模板闭集内 | `skipped` | 不创建该字段、**不播事件** |

- **禁止**在 run 结束后一次性补播：`field_write` 步骤卡处于 `running` 时，已写字段的事件与看板
  必须已经生效（红测 `B4` 用"只跑 `field_write` 一步"来证）。
- **禁止**把未确认值写进 `requirement.data`（沿用第 4 批铁律）；`board != "written"` 时
  `requirement.data` 里对应键必须不存在或保持原值。

### 4.4 左侧只吃摘要

- 步骤卡的进度行文案来自 `detail` 的**安全字段**（计数、版本、状态），
  **不得**出现绝对路径、stderr 原文、DXF 片段、Base64、模型思维链（红测 `H25`）。
- `summarize()` 与所有事件文本都过一遍"禁用词"检查：`/tmp/`、`.dwg` 全路径、`data:image`、
  `base64`、`thinking`、`stack`、`Traceback`。

## 5. 契约 D：门禁矩阵（本批核心之一）

### 5.1 矩阵（"哪些字段确认后才能做什么"）

`确认` 的定义（**只认人工确认**，红测 `C7`/`C8`/`C13`）：
`requirement.data[field]` 有值 **且** `field_provenance[field].status == "confirmed"`
**且**（`requirement.data["field_sources"][field] == "manual"` **或**
`field_provenance[field].origin == "user_confirmed"`）。

- `confirmed_from_cad` **不等于**已确认：它说明"图纸里明确"，会写进看板并标 `written`
  （要求 4/5），但盒型 / BOM / 路线 / 成本 / 正式报价这些**下游仍要人点头**——
  这正是要求 8「用户确认后相应步骤解锁」的落地方式。
- `origin=="user_confirmed"` 与 `field_sources=="manual"` 二者取或：前端确认页写前者，
  人工在表单里填过、由 `requirement_service` 打 `manual` 的写后者。

| 下游 | 必需字段（全部 `confirmed`） | 附加条件 | 缺失后果 | 是否允许草稿 |
| --- | --- | --- | --- | --- |
| `box_match` | `inner_length` `inner_width` `inner_height` `closure_type` | 单位已确认 | **阻止**盒型匹配 | 否 |
| `bom` | `inner_length` `inner_width` `inner_height` `face_paper_gsm` | `box_match.decision == "confirmed"` | **阻止** BOM | 否 |
| `route` | `closure_type` `v_groove` `face_paper_gsm` | BOM 已生成 | **阻止**工艺路线 | 否 |
| `cost` | `quote_quantity` | 路线已确认（`packaging_route` 的 `status=="confirmed"`） | 缺 `quote_quantity` → **只形成成本缺口**，不阻止出成本 | —— |
| `quote_draft` | 无 | 成本已测算 | **不阻止**，缺口随包传递 | 是 |
| `quote_publish` | `box_match` 全部字段 | 成本无缺口（或已留痕放行）**且** `minimum_charge_policy().status == "chosen"` | **阻止正式报价** | 是（草稿可存） |

- 字段清单**冻结**：不得在实现里加字段而不改本表；`MATCH_INPUT_KEYS` / `INNER_DIM_KEYS`
  与既有引擎保持一致（`packaging_match.py:32`、`packaging_bom.py:52`）。
- 一次接口就能问清全部结论：`gates(project_id)` 返回六段；`gates(project_id, stage="bom")` 只返回一段。
- `stages[stage]["requires"]` 是**有序**元组，顺序与上表逐格一致（红测逐格比对）。

**每段的 `blocking` 计算方式（冻结；红测 `C14` 只许出闭集里的码）**：

| 段 | 逐字段结论（顺序同 `requires`） | 段级附加条件 → 码 |
| --- | --- | --- |
| `box_match` | 冲突 → `field_conflict`；无值 → `field_missing`；有值未人工确认 → `field_unconfirmed` | 单位未确认 → `unit_unconfirmed` |
| `bom` | 同上 | `box_match.decision != "confirmed"` → `box_match_not_confirmed` |
| `route` | 同上 | `bom.built != true` → `bom_not_built` |
| `cost` | 同上 | `route.status != "confirmed"` → `route_not_confirmed` |
| `quote_draft` | **不逐字段判** | **恒 `open`**：未测算/有缺口只进 `warnings`（`cost_not_built` / `cost_gaps_unresolved`），缺口随包传递 |
| `quote_publish` | 同上 | 盒型未确认 → `box_match_not_confirmed`；成本未测算 → `cost_not_built`；有缺口 → `cost_gaps_unresolved`；口径未裁决 → `minimum_charge_policy_unresolved` |

- `field_conflict` 的判定要**同时**看流自己的字段看板（`flow_state.fields[field].board ==
  "conflict"`）与 `field_provenance[field].status`：冲突字段按第 4 批铁律**不会**写进
  `requirement.data`，只看需求单会漏判（红测 `C7`）。
- 单位码只在 `anchor.unit_status` **有明确取值且不是 `confirmed`** 时出
  （`needs_confirmation` / `unknown`…）；`anchor` 还是空（本次还没解析过）时不判单位——
  此时字段本身就会把段拦下，不该凭空多出一条单位结论。
- `blocking` 的顺序：**字段类在前（按 `requires` 顺序），段级结论在后**，段级内部按上表右列顺序。

### 5.2 `gates()` 输出

```json
{"project_id": "p1", "flow_version": "packaging-drawing-flow/1",
 "requirement_snapshot_version": "reqsnap/1:9f2c…",
 "stages": {
   "box_match": {"stage": "box_match", "status": "blocked",
     "requires": ["inner_length","inner_width","inner_height","closure_type"],
     "blocking": [{"code": "field_unconfirmed", "field": "inner_length",
                   "message": "内长尚未确认，确认后才能做盒型匹配", "source": "field_provenance"}],
     "warnings": [{"code": "unit_unconfirmed", "field": "inner_width",
                   "message": "图纸未声明单位，长宽高不能按毫米确认"}],
     "snapshot": {"requirement_snapshot_version": "reqsnap/1:9f2c…"}},
   "quote_publish": {"status": "blocked",
     "blocking": [{"code": "minimum_charge_policy_unresolved",
                   "message": "最低收费口径尚未裁决，暂不能生成正式报价（可先存草稿）"}]}}}
```

`blocking[].code` 是**闭集**：`field_missing` / `field_unconfirmed` / `field_conflict` /
`unit_unconfirmed` / `box_match_not_confirmed` / `bom_not_built` / `route_not_confirmed` /
`cost_not_built` / `cost_gaps_unresolved` / `minimum_charge_policy_unresolved`。

### 5.3 `require_gate()`

```python
require_gate(project_id, "bom")   # 允许 → 返回该段 gates 结论；被拦 → raise DrawingFlowError
```

- 被拦时抛 `DrawingFlowError`，携带 `stable_error_code="PACKAGING_GATE_BLOCKED"`、
  `http_status=409`、`retryable=True`、中文 `message`（点名缺哪个字段 + 怎么解锁）。
- `stage` 不在 `GATE_STAGES` 内 → `ValueError`（不猜）。
- **接线点只有一处**：`handoff_package()` / `send_to_quote()` 里新增 `publishable` 与
  `gates` 两个**只读**结论键（§5.4），**不**在既有五引擎的生成函数里硬拦
  （那会改掉已通过用例的行为，属于越权改他人契约）。

### 5.4 与报价侧的契约（本仓能做的部分）

`packaging_handoff.handoff_package()` 的返回里**追加**（不删不改既有键）：

```json
{"publishable": false,
 "gates": {"quote_draft": {"status": "open"}, "quote_publish": {"status": "blocked",
            "blocking": [{"code": "minimum_charge_policy_unresolved", "message": "…"}]}},
 "minimum_charge_policy": {"status": "pending", "policy": "unresolved",
                           "fallback": "sheet_labor_rate", "decided_by": ""},
 "source_versions": {…§6.1…}}
```

- `publishable` 为 `false` 时，报价侧**只能**出成本与草稿（对齐
  `docs/specs/packaging-quote-close-loop.md` §2.3「缺口未清时不得生成正式报价单」）。
- **为什么不在本仓硬拦 `send_to_quote`**：那一步的语义是"进入定价"（`packaging_handoff.py:330`
  「包装成本已确认，请进入定价」），是**草稿**；硬拦会连带打断既有已通过的回传用例，
  等于用新门禁改旧契约。正式报价单的闸门在第 6 批跨系统 E2E 里验。

## 6. 契约 E：版本继承与 stale 传播（本批核心之二）

### 6.1 六元组（下游每一段都必须携带）

```python
def inheritance(project_id, *, stage="") -> dict
```

`drawing_version` 的**来源**是项目 meta 的 `input_revision`（`store.replace_source()`
会把它 +1，`store.py:166`）：流层把它传给第 2 批的 `convert_drawing(drawing_version=…)`，
再从 manifest 回读校验；两边不一致以 manifest 为准并记 `warnings`。

```json
{"source_drawing_version": 1,                 // int，来自第 2 批 manifest 的 drawing_version
 "source_ir_version": "cad-ir/1",             // 第 3 批的契约版本；无 IR 时空串
 "requirement_snapshot_version": "reqsnap/1:9f2c…",
 "confirmed_by": "",                          // 该段的人工确认人（无则空串，绝不现编）
 "confirmed_at": "",
 "unresolved_gaps": [{"field": "face_paper", "reason": "missing",
                      "status": "missing", "message": "缺少材料信息"}],
 "source_ir_hash": "",                        // 追加证据键（可选）
 "source_semantics_version": "packaging-semantics/1",
 "source_semantics_hash": ""}
```

- 前六个键**必填**（名字一字不改）；后三个是追加证据键。
- `requirement_snapshot_version(project_id)`：`"reqsnap/1:" + sha256(规范化 JSON)[:16]`，
  规范化输入**只含**业务口径：

```json
{"status": "approved|draft|…",
 "fields": {"<field>": "规范化取值"},        // 只含非空、非内部键（排除 field_sources/
                                             // field_provenance/*_json/quote_source_* 等）
 "provenance": {"<field>": {"origin": "", "status": ""}},
 "waivers": [{"field": "", "reason": ""}],   // 按 field 排序
 "confirmed_by": "", "confirmed_at": ""}
```

  禁止把 `history`、时间戳、`updated_at` 混进去（否则每次保存都变版本，stale 判定失效）。
- 埋点（**只追加键**）：`packaging_match.run_box_match` / `decide_box_match`、
  `packaging_bom.build_bom`、`packaging_route.build_route` / `load_route`、
  `packaging_cost` 的计算结果、`packaging_handoff.handoff_package` 的 `packaging_package` 段
  → 各加 `"source_versions": inheritance(...)`。
- **上一段的版本必须传进下一段**：BOM 的 `source_versions` 里含盒型的
  `{"box_type_code": …, "engine_version": "packaging_match_v1"}`；路线含 BOM 的
  `{"bom_version": …}`；成本含路线的 `{"route_version": …}`；报价草稿（交接包）含成本的
  `{"result_version": …}`。这一段链条叫 **`stage_chain`**，同样进 `source_versions`。

### 6.2 锚点

```json
{"anchor_version": "packaging-flow-anchor/1", "project_id": "", "run_id": "",
 "drawing_version": 1, "source_sha256": "", "conversion_id": "",
 "ir_id": "", "ir_hash": "", "ir_version": "",
 "semantics_id": "", "semantics_hash": "", "semantics_version": "",
 "unit_status": "", "requirement_snapshot_version": "reqsnap/1:9f2c…",
 "updated_at": "", "updated_by": ""}
```

- `run_id_for()`：`"flow-" + sha256("|".join([source_sha256, str(drawing_version),
  requirement_snapshot_version, 规范化 prompt]))[:16]`。
  **同一份附件 + 同一份需求快照 = 同一个 run_id**（重跑幂等）；
  **新图纸版本 / 新需求快照 = 新 run_id**（新会话条目，旧条目保留可回看）。

**"同一份需求快照"指哪一份（踩过的坑，红测 `E15`/`F30`）**：流自己会写需求字段，
写完 `requirement_snapshot_version()` 必然变。所以 `run_id` 只能锚在**运行开始、
还没写任何字段之前**的那份快照上，并把它记进 flow：

```json
{"inputs": {"source_sha256": "", "drawing_version": 1, "prompt": "",
            "requirement_snapshot_version": "reqsnap/1:9f2c…"},
 "requirement_snapshot_version_after": "reqsnap/1:8a11…"}
```

- `start(project_id)` 的复用判定（满足才复用旧 `run_id`，否则按当前快照算新 `run_id`）：
  1. `inputs.source_sha256` 与当前原图一致；
  2. `inputs.drawing_version` 与当前 `input_revision` 一致；
  3. `inputs.prompt` 一致；
  4. **且**当前需求快照 ∈ {`inputs.requirement_snapshot_version`,
     `requirement_snapshot_version_after`} —— 即"需求只被本 run 自己改过"。
- 判 (4) 时**必须**允许 `requirement_snapshot_version_after`：否则第一次跑完就再也复用不了，
  重跑必然变成新 run（要求 15 直接失效）。人工改过需求（两者都不等）→ 新 `run_id`，旧证据保留。
- `anchor.requirement_snapshot_version` 记的是**当前**快照（每步收尾刷新一次），
  所以跑完后它等于 `requirement_snapshot_version(project_id)`（红测 `F30`）；
  它**不是** run_id 的输入——run_id 只认 `flow["inputs"]`。
- 由此 `requirement_snapshot_changed` 这条 stale 原因只对**外部改动**成立：
  本次 run 自己写字段引起的变化不算 stale（否则每跑一次就把自己的下游全标脏，红测 `D14`）。

### 6.3 stale 传播

`mark_downstream_stale(project_id, reasons, *, actor)` 只做三件事：

1. 写 `packaging_downstream_stale`：`{"stale": true, "reasons": [...], "since": "...", "anchor": {...}}`；
2. 清 `clear_downstream_stale` 时按 `stage` 逐段解除；
3. 审计 `packaging_flow.downstream_stale`。

- `reasons` 闭集：`drawing_version_changed` / `source_sha256_changed` / `ir_hash_changed` /
  `semantics_hash_changed` / `requirement_snapshot_changed` / `converter_version_changed`。
- **只标 stale，绝不改正文**：不删、不覆盖、不重置 `box_match` 的 `decision/confirmed_*`、
  BOM 明细、路线步骤、成本结果、`pricing` / 报价单。已发布报价**不得**被静默改写。
- `stale_view(project_id)`：

```json
{"stale": true, "reasons": ["drawing_version_changed"], "since": "…",
 "anchor": {…}, "downstream": {"box_match": true, "bom": true, "route": true,
 "cost": true, "quote_draft": true}}
```

- 触发点：`run_step` 的 1/2/3/4 步成功后调 `anchor.refresh`；新旧锚点的
  `drawing_version` / `source_sha256` / `ir_hash` / `semantics_hash` /`requirement_snapshot_version`
  任一变化 → 逐项产出 reason 并 `mark_downstream_stale`。
- 转换器版本变化的 stale **沿用第 2 批**（`cad_converter.service._stale_reason`），本批只把它
  翻译成 `converter_version_changed` 传播到业务段，不重新实现。

### 6.4 版本与迁移

```python
migrate({"flow_version": "packaging-drawing-flow/1"})  # → {"status": "ok"}
migrate({})                                           # → {"status": "needs_rebuild",
                                                      #    "reason": "missing_flow_version"}
migrate({"flow_version": "packaging-drawing-flow/99"}) # → raise ValueError（不许按当前版本硬读）
```

- anchor 与 stale 文档同样带 `anchor_version`；读到时缺版本 → `needs_rebuild`，**不猜**。

## 7. 契约 F：重跑、重试与幂等

| 场景 | 期望 |
| --- | --- |
| 同附件 + 同需求快照再点一次"解析" | `run_id` 不变；用户气泡 1 条；七步卡片各 1 张（就地更新）；`session-note` 按 `key` 就地更新；需求字段 1 份 |
| 失败步重试（`run_step(..., retry_of=…)`） | 同 key 就地更新 → `attempt += 1`，**不新增**卡片、不新增字段、不新增任务；**不重新上传附件** |
| 新图纸版本 | 新 `run_id`；旧 run 的条目与证据**全部保留**；下游标 stale（§6.3） |
| 新需求快照 | 新 `run_id`；旧证据保留 |
| 同一附件并发跑两次 | 第二步起复用第 2 批 `cache_key` 锁；`flow` 文档按 `_document_lock` 串行写；不得出现两张同 key 卡片 |

- 重试入口只认 `retry_of`（前一次的步骤 key）；不允许"重跑整个 run"来掩盖单步失败。
- `run_step(pid, step_id)`（不带 `retry_of`）**只跑这一步**，且要求其前置步骤已完成。
- `run_step(pid, step_id, retry_of=X)` 在成功后**按顺序继续跑完剩余仍为 `pending` 的步骤**
  （等价于从该步恢复整条链）；再次失败则链停在原地，`attempt` 继续递增。
- 每次重试都复用同一个 `key`，因此卡片、字段过程行、需求字段都**只有一份**。
- `run_flow` 对已完成且输入未变的一步**跳过重算**（读上一份 `detail` 与产物判定），
  避免重复转换/重复解析。

## 8. 契约 G：权限角色矩阵

读写**分开**测（红测 `G28`）。

| 能力 | 接口 | 最低角色（复用既有，不改） |
| --- | --- | --- |
| 读流程状态 / 门禁 / stale | `GET /api/projects/{id}/drawing-flow` | 登录且 `project_access.can_read`（含销售/财务的只读可见性） |
| 跑链路 / 重试单步 | `POST /api/projects/{id}/drawing-flow/run` | `auth.SESSION_WRITE_ROLES` = `{engineer, process_manager, admin, finance_manager}` |
| 写会话时间线 | `POST /api/projects/{id}/agent/event` | `SESSION_WRITE_ROLES`（既有，不放宽） |
| Agent 对话 | `/agent/send`、`/agent/new` | `WRITE_ROLES` = `{engineer, process_manager, admin}`（**刻意不放宽**，`auth.py:52-61` 有注释） |
| 需求字段人工确认 | 既有确认路由 | 既有 `MANAGER_ROLES`/`DIRECTOR_ROLES`（不改） |
| 盒型决策 / BOM | `packaging_match` / `packaging_bom` | `BOX_MATCH_DECIDE_ROLES`（不改） |
| 成本写数 | `packaging_cost` | `COST_ROLES`（不改） |
| 正式报价 | 报价侧 | `QUOTE_APPROVAL_ROLES`（**跨系统，第 6 批验**） |

- **不允许**把所有用户加进项目写权限；**不允许**为了跑通链路放宽 `/agent/send`。
- 销售（`sales_manager`）与财务（`finance_manager`）必须能**读**到流程状态与门禁结论：
  否则"合法的销售→工艺→财务流转"会被 404 挡住（红测 `G27`）。
- 普通权限说明（如"你的角色只能查看该项目"）用系统蓝色主色提示，**不得**用橙色告警替代 ——
  该视觉口径由既有 `docs/specs/global-brand-color-0060e6.md` 与
  `tests/test_global_brand_color_red.py` / `tests/test_tech_global_single_primary_by_state_and_nonblocking_notices_red.py`
  负责，本批**不新增**颜色/样式契约（红测 `H19`/`H20`/`H21`/`H22` 只守"没有第二套"）。

## 9. 契约 H：错误码（并入第 1 批权威闭集，20 → 22 条）

追加两条，既有 20 条的数值与文案**一字不改**：

| `stable_error_code` | HTTP | 用户文案（中文） | `retryable` |
| --- | --- | --- | --- |
| `PACKAGING_GATE_BLOCKED` | 409 | 需求字段尚未确认（{字段}），请先确认后再进行该步骤 | 是（确认后同一请求可重试） |
| `PACKAGING_FLOW_DEPENDENCY_MISSING` | 500 | 图纸解析链路依赖的能力尚未就绪（{依赖}），请联系系统管理员 | 否（装好后重跑同一条链路） |

- 依赖缺失**不抛异常到用户界面**：对应步骤是 `unavailable` + 这条码 + `detail.dependency`；
  只有显式调 `require_gate` / 直接调步骤执行体时才 `raise`。
- 转换/解析自身的失败一律沿用既有码（`DWG_CONVERSION_*`、`CAD_IR_*`、`PACKAGING_*`），
  本批**不新增**同义码。
- 用户可见文案**不得**含堆栈、绝对路径、stderr 原文、第三方工具名以外的内部细节。

## 10. 契约 I：红测分层（`tests/test_packaging_drawing_flow_red.py`）

| 层 | 组 | 内容 | 依赖真实转换器？ |
| --- | --- | --- | --- |
| L1 单元 | `A` | 步骤闭集、顺序、状态机、依赖缝、禁用词、零模型调用 | 否（fake 依赖） |
| L1 单元 | `C` | 门禁矩阵、`require_gate`、冲突阻断、确认解锁 | 否 |
| L1 单元 | `D` | `requirement_snapshot_version` 确定性、anchor、stale 传播 | 否 |
| L2 适配器契约 | `I` | 依赖缝：fake 与真实模块满足同一契约；第 3/4 批落地现状 | 否 |
| L3 服务集成 | `B` / `E` / `F` / `G` | 看板双写、幂等重跑、重试、历史恢复、权限读写分离 | 否 |
| L4 真实样本 E2E | 第 6 批 | `酒盒.dwg` / `圆盘盒.dwg` 全链路 | **是**（第 6 批门禁） |

- **L1/L2 通过 ≠ DWG 已支持**。本批完成后只能声明"编排与门禁完成"，
  真实转换能力与真实样本 E2E 在第 6 批。
- 红测自带 fake：`FakeCadIr` / `FakeSemantics` / `FakeConverter`（内存、确定性、无网络），
  经 `run_flow(..., deps={...})` 注入；**绝不**把 fake 配成生产默认。

### 10.0 红测自洽性怎么证的（交付前必须做）

红测写完**不能**直接交付：它必须先在**一次性 stub** 上跑绿一次，证明"这套断言彼此不矛盾、
而且真的可满足"。本次验证方式（stub 只在 `/tmp`，**不入库、不作为实现**）：

1. 在 `/tmp` 造一个 `/tmp/.../packaging_drawing_flow/`（`__init__.py` + `model.py`）与
   `fakeroot/tech_app/backend/services/{packaging_match,packaging_bom,packaging_route,
   packaging_cost,packaging_handoff}.py` 占位；
2. 用 `importlib` 把 stub 挂到 `tech_app.backend.services.packaging_drawing_flow` 名下，
   再把红测里的 `FLOW_DIR` / `FLOW_FILE` / `MAIN` / `ROOT` 指到 `/tmp`；
3. 跑 `unittest` —— **53/54 通过**即为自洽（唯一允许红的是 `I3`：第 3 批 `cad_ir`
   尚未落地，它按设计红，且失败文案已写明"不是第 5 批的缺口"）。

这轮验证抓出并修掉了 **6 个红测自身的缺陷**（都已修，且修完复核"真仓库里仍 54 红"）：

| # | 位置 | 缺陷 | 修法 |
| --- | --- | --- | --- |
| 1 | `A5` | `int(row.get("index") or -1)` —— `index=0` 被判成 `-1`（falsy 0） | 改 `int(row.get("index", -1))` |
| 2 | `A10` | 相对 import `.model` 的 `head` 算成空串，**任何**包内相对 import 都判违规 | 显式跳过 `.` 开头的相对 import |
| 3 | `G18` | 调 `self.engines()`，但该方法定义在 `CGates`，`G` 组没有 | 改用 `self.start(pid)`（默认就是 `FakeEngines`） |
| 4 | `FakeEngines` | `self.route_versions = …` 把同名**方法**覆盖成列表 → `'list' object is not callable` | 存到 `_route_versions`，方法返回值 |
| 5 | `D14` | 断言 `pricing` 文档原样不变，但测试自己调的 `store.replace_source()` 会走既有 `invalidate_confirmations()` 把审批打回 `draft` —— **断言不可能成立** | 改成只锁"正文不许被改写、不许塞新键"，并注明打回 draft 来自既有行为 |
| 6 | `E23` | 断言重试后卡片总数不变，却又断言"后续步骤必须继续跑"（二者互相矛盾） | 改成锁"同一步骤只有一张卡 + 旧卡按同一 key 就地更新" |

**纪律**：这 6 处改的是**测试自己的 bug**，没有一条是为了让实现好过而放宽断言；
改完必须重跑真仓库确认红数不变（本轮仍 54 红）。

### 10.1 用户 30 条红测要求的映射

| # | 用户要求 | 测试 |
| --- | --- | --- |
| 1 | 点击解析按钮先产生用户气泡 | `A1` 用户条目先于第一步，且同 run 不重复 |
| 2 | Agent 执行过程按 Tool List 展示 | `A2` 七步各一张 `kind="task"` 卡，`task.id` 规则正确 |
| 3 | 转换、解析、字段写入状态顺序正确 | `A3` 同 run 的步骤 `seq` 严格递增且顺序固定 |
| 4 | 左侧输出字段时右侧同步更新 | `B4` 只跑 `field_write` 时看板已更新、事件已播 |
| 5 | 字段显示来源和证据 | `B5` 每条 `field` 带 `origin`/`status`/`evidence_refs` |
| 6 | 模型候选进入待确认，不自动确认 | `B6` `inferred_by_model` → `board="pending"` 且未写 `data` |
| 7 | 冲突字段阻止依赖步骤 | `C7` `field_conflict` → `box_match` blocked |
| 8 | 用户确认后相应步骤解锁 | `C8` 置 `user_confirmed` 后同段变 `open` |
| 9 | 盒型确认结果传给 BOM | `C9` BOM 的 `source_versions.stage_chain` 含盒型确认 |
| 10 | BOM 版本传给工艺路线 | `C10` 路线的 `stage_chain` 含 BOM 版本 |
| 11 | 工艺路线版本传给成本 | `C11` 成本的 `stage_chain` 含路线版本 |
| 12 | 成本缺口传给报价草稿 | `C12` 交接包 `unresolved_gaps` 非空且 `quote_draft` 仍 `open` |
| 13 | 未解决关键缺口不能发布正式报价 | `C13` 缺口 / 口径未裁决 → `quote_publish` blocked、`publishable=false` |
| 14 | 新图纸版本使旧下游结果 stale | `D14` 标 stale 且正文逐字未改 |
| 15 | 重跑不重复建字段/消息/任务 | `E15` 两次 `run_flow` 后条目数与字段数不变 |
| 16 | 刷新后会话与 Tool 状态恢复 | `F16` `load_session_events` 回放出同一批卡与顺序 |
| 17 | 历史项目恢复会话与字段证据 | `F17` 两步走：新实例读回 flow/anchor/字段证据 |
| 18 | 权限错误只保留一处提示 | `G18` 403 只产生一条 409/403 结论，不重复播 |
| 19 | 权限提示用蓝色主色、不用橙色 | `H19` 本批不新增颜色契约（守"没有第二套"） |
| 20 | 报价与技术工艺会话结构一致 | `H20` 事件形状复用既有 `task`/`session-note`/`user` |
| 21 | 模型设置不占输入框主要空间 | `H21` 本批不产出任何 UI 布局字段 |
| 22 | 两边输入框底部对齐 | `H22` 同上（视觉契约归既有套件） |
| 23 | 失败步骤可安全重试 | `E23` 重试成功后 `attempt=2`、卡片唯一、步骤推进 |
| 24 | 重试不重复上传原始附件 | `E24` 附件列表与 `source_path` 逐字未变 |
| 25 | 不展示思维链 | `H25` 事件文本过禁用词表 |
| 26 | 未确认值不进正式成本 | `B26` 未确认字段不进 `data`、不进成本输入快照 |
| 27 | 角色与 ACL 不阻断合法流转 | `G27` 销售/财务可读流程与门禁 |
| 28 | 读与写权限分别测 | `G28` 读路由 `current_user` vs 写路由 `SESSION_WRITE_ROLES` |
| 29 | 两个 DWG 项目能从历史清单重开 | `F29` 两个项目的 flow 都能读回 |
| 30 | 历史项目恢复全量会话/字段/状态 | `F30` 三样齐全且顺序一致 |

## 11. 契约 J：酒盒 / 圆盘盒的预期流程（不编造业务值）

本批**不**给两份真实图纸填任何尺寸。预期只有"结构结论 + 待确认项"：

| 环节 | `酒盒.dwg` | `圆盘盒.dwg` |
| --- | --- | --- |
| `file_preflight` | `dwg` / `AC1027` / 686,195 B | `dwg` / `AC1027` / 889,062 B |
| `dwg_convert` | `success_with_warnings`，warning≈1520 / error 0 | `success_with_warnings`，warning≈252 / error 3（不得写成"无损"） |
| `cad_ir_parse` | 图层/实体非零（约 8 图层 / 6711 实体） | 图层/实体非零（约 32 图层 / 3457 实体） |
| `packaging_semantics` | 至少产出可审查摘要或明确待确认项 | 同左（`round_tube` **候选**，不锁定 `box_type`） |
| `field_write` | 单位确认后才可能 `written`；否则 `pending` | 同左 |
| `pending_confirm` | 非空（长/宽/高、材料至少各一条待确认） | 同左 |
| `downstream_prepare` | `box_match` 通常 `blocked`（缺确认字段） | 同左 |

- 文件名**不是证据**：`酒盒` / `圆盘盒` 不得成为盒型或结构参数依据（沿用第 4 批 `F4`）。
- 真实样本用例在夹具/基线缺失时必须**跳过**并提示，不得伪造通过。

## 12. 契约 K：失败、重试与历史恢复的验收方法

| 场景 | 怎么验 |
| --- | --- |
| 转换失败 | 注入 fake 转换器返回失败 → 第 2 步 `failed` + 既有稳定码；3–7 步 `pending`；`flow.status="failed"`；`stages.parsed` 未写 |
| 解析器缺失 | 依赖缝返回 `None` → 第 3 步 `unavailable` + `PACKAGING_FLOW_DEPENDENCY_MISSING` + `detail.dependency="cad_ir"` |
| 单步重试 | `run_step(..., retry_of=key)`；断言 `attempt` 递增、卡片唯一、附件未变、后续步骤被推进 |
| 幂等重跑 | 同一 run 连跑两次 `run_flow`；断言 `load_session_events` 条数不变、需求 `data` 键值不变 |
| 刷新恢复 | 新建 `flow_state` 读回对象与落盘一致（顺序、`seq`、状态） |
| 历史恢复 | 用同一临时存储第二次 `import`/读回（模拟重进项目）→ flow/anchor/stale/字段证据齐全 |
| 新版本 stale | 改了 `drawing_version` 再跑 → `stale_view().stale is True`，下游正文快照逐字相等，`pricing` 未被触碰 |

## 13. 非目标（本批一律不做）

- 不做 2D/3D 分流、不改 `/upload-3d`、不让 DWG 进 STEP importer（第 6 批）。
- 不实现转换器/解析器本身；不改第 2/3/4 批的错误码数值与文案。
- 不新增前端 Tool List / 气泡 / 配色 / 布局契约；不改 `agent-chat.js` 的会话渲染分支。
- 不改成本公式、费率、损耗、最低收费口径（`status` 仍 `pending`）、不改 `packaging_cost_rules.json`。
- 不发布报价、不生成报价单、不改 `pricing`/`approval` 正文。
- 不给真实样本填任何业务尺寸；不把文件名当证据。
- 不填充 mock 业务值来让流程"跑通"；不自动确认任何模型推断。
- 不删除/清空历史会话、证据、版本。
- 不新增第三方依赖（`ezdxf` 属第 3 批遗留项，本批不引入）。
- 不 commit / push / MR / tag / Release / 部署 / 重启服务 / 改服务器配置。

## 14. 第 6 批最终验收前仍需解决的事项

1. **第 3 批 `cad_ir` 必须真的落地**：本批的 `cad_ir_parse` 步骤在它缺失时只能 `unavailable`。
2. **第 4 批 `packaging_semantics` 的稳定接口对齐**：`analyze_conversion` / `apply_to_requirement`
   / `summarize` 的签名若与第 4 批最终实现不一致，第 5 批要按实际冻结名对齐（不许两边各留一套）。
3. **真实转换器在目标环境的许可证与版本固定**：已拍板 —— 主转换器 ODA File Converter 27.1
   （非会员限非商业用途，**业务/法务已确认可用于本 CPQ 生产环境**），回退 LibreDWG 0.14（GPLv3+）；
   34 服务器上 ODA 走 `xvfb-run` + `squashfs-root/AppRun`，LibreDWG 走
   `/home/data/cpq-tools/current/bin/dwg2dxf`。配置项、argv、wrapper 与回退链契约见
   `docs/specs/dwg-conversion-quality-repair.md`（`/2`）；本批只消费 `manifest` 的
   `converter_name`/`converter_version`/`converter_role`/`fallback_used`，不自己判断转换器。
4. **最低收费口径裁决**：`pending` 期间 `quote_publish` 恒 `blocked` —— 这是**有意**的，
   第 6 批不能为了让 E2E 变绿而放开。
5. **跨系统正式报价闸门**：`publishable=false` 需要报价侧真的遵守（第 6 批 L4 验）。
6. **`/parse` 的 DWG 分支**：接入后 DWG 走链路、其余格式走既有视觉路径，两者都不许回归。
7. **`ezdxf` 未进 `requirements.txt`**：进生产镜像前必须补。
8. **金标**：两份真实样本的图层/实体/单位/待确认项基线仍待人工复核（第 4 批 §12 的审查包）。

## 15. 自动化验收

| 命令 | 期望 |
| --- | --- |
| `./open-claude/.venv/bin/python tests/test_packaging_drawing_flow_red.py` | 实现前 FAIL（红）；实现后 OK |
| `./open-claude/.venv/bin/python tests/test_dwg_file_capability_preflight_red.py` | OK（加 2 条新码后先红，实现后 OK） |
| `./open-claude/.venv/bin/python tests/test_packaging_semantics_red.py` | 不回归 |
| `./open-claude/.venv/bin/python tests/test_dxf_cad_ir_red.py` | 不回归（第 3 批落地后转绿） |
| `./open-claude/.venv/bin/python tests/test_packaging_box_type_matching_red.py` | 不回归 |
| `./open-claude/.venv/bin/python tests/test_packaging_parametric_bom_red.py` | 不回归 |
| `./open-claude/.venv/bin/python tests/test_packaging_process_route_red.py` | 不回归 |
| `./open-claude/.venv/bin/python tests/test_packaging_quote_close_loop_red.py` | 不回归 |
| `./open-claude/.venv/bin/python tests/test_packaging_cost_engine_red.py` | 不回归（既有 3 条待裁决项除外） |
| `./open-claude/.venv/bin/python tests/test_quote_tech_unified_tool_list_conversation_red.py` | 不回归（Tool List 契约唯一） |
| `./open-claude/.venv/bin/python tests/test_tech_agent_echo_bubble_and_single_exec_card_red.py` | 不回归（气泡契约唯一） |
| `./open-claude/.venv/bin/python tests/test_global_brand_color_red.py` | 不回归（蓝色主色） |
| `./open-claude/.venv/bin/python tests/test_quote_tech_composer_geometry_and_flush_bottom_red.py` | 不回归（输入框底部对齐） |
| `./open-claude/.venv/bin/python /tmp/run_pkg.py 1` | 除既有失败集合与本批新红外不新增失败 |

## 16. 人工验收

1. 打开一个 DWG 项目 → 点"开始解析"：左栏**先**出现「我：帮我解析这张图纸。」，随后是
   七步执行卡（文件预检 / DWG 转换 / CAD 矢量解析 / 包装语义识别 / 字段写入 / 待确认生成 /
   后续任务准备），每步可展开、无思维链。
2. 转换完成那一刻，右侧需求看板**已经**出现"内长：待确认"这类条目（不是等整条会话跑完才刷）。
3. 刷新页面：气泡、七步卡与状态、字段来源与证据原样恢复。
4. 故意让转换失败（改错二进制路径）→ 第 2 步显示失败与中文原因；点重试 → 同一张卡更新，
   不新建卡、不重复上传。
5. 上传新图纸版本 → 盒型/BOM/路线/成本显示"已过期，请重新生成"，旧报价单**未被改写**。
6. 未确认关键字段时打开盒型匹配/正式报价 → 明确提示缺哪个字段；确认后同一处变可执行。
7. 换一个只有只读权限的账号 → 能看到流程与门禁结论，写操作被拒一次且只提示一处（蓝色）。

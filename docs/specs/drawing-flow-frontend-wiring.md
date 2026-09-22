# 2.1 图纸解析入口接线 drawing-flow（DWG / DXF 项目）

Spec 版本：1（状态行见下）

状态：Spec + 红测（已实现）
红测：`tests/test_drawing_flow_frontend_wiring_red.py` `tests/test_dwg_conversion_adapter_red.py` `tests/test_dwg_file_capability_preflight_red.py`

## 1. 背景（实测，不是推断）

- 服务端链路已在 34 打通：隔离 `DATA_DIR` 下用真实 `酒盒.dwg` 跑
  `packaging_drawing_flow.run_flow()`，`file_preflight → dwg_convert → cad_ir_parse →
  packaging_semantics` 全部 completed，落盘 `cad_ir` 的 `DIMENSION=316`、`layers=8`
  与金标逐项一致。
- 界面完全够不到：`tech_app/frontend/app.js:1436` 的 `blockedReason` 用
  `isImg = /\.(png|jpe?g|webp|gif|bmp)$/i` 把 DWG/DXF 判成"不是位图"，
  `app.js:1457` `$("btnParse").disabled = !isImg;` 把解析按钮置灰，文案是
  「请上传该图纸的 PNG 或 JPG 后再解析」。
- 前端全文没有 `drawing-flow` 字样；`app.js:916` 打的是
  `POST /api/projects/{id}/parse` —— 视觉模型路径，与 drawing-flow 是两条链路。
- 服务端入口早已存在：`tech_app/backend/main.py:6831`（`GET /api/projects/{pid}/drawing-flow`）、
  `main.py:6844`（`POST /api/projects/{pid}/drawing-flow/run`）。

业务后果：客户手里就是 DWG，系统收下文件（1.1 允许 PDF/DWG/DXF）却要求他回 CAD 另存 PNG，
服务端已验收的能力在产品上等于零。

## 2. 目标

DWG/DXF 项目在 2.1 直接走 drawing-flow，用户点一次就能看到步骤状态与 `cad_ir` 摘要；
视觉路径（PNG/JPG/PDF）行为不变；3D 导入项目仍被正确阻断。

## 3. 契约

### C1 入口判定必须是可测纯函数

页面新增纯函数：

```js
renderDrawingEntry(filename) -> "vision" | "drawing_flow" | "blocked_3d" | "blocked_other"
```

- `.dwg` / `.dxf` → `"drawing_flow"`；
- `.png|.jpg|.jpeg|.webp|.gif|.bmp` → `"vision"`；
- `.step|.stp|.iges|.igs|.stl` → `"blocked_3d"`；
- 其余（含空文件名）→ `"blocked_other"`；
- 大小写不敏感；函数体内**不得**引用 `document` / `window` / `sessionStorage`
  （与 `resolveInitialIndustry()` 同一条纪律：可被 `node` 直接执行）。

### C2 按钮可用性由 C1 决定

- `drawing_flow` → `btnParse.disabled = false`，`title` 不得出现「请上传 PNG / JPG」；
- `vision` → 现状不变（`disabled = false`）；
- `blocked_3d` / `blocked_other` → `disabled = true`，占位文案给出原因；
- 源码中不得再存在 `$("btnParse").disabled = !isImg;` 这一句。

### C3 两个调用点

- 触发：`POST /api/projects/{pid}/drawing-flow/run`（可带 `{"prompt": "..."}`）；
- 刷新/首屏：`GET /api/projects/{pid}/drawing-flow`（返回
  `{flow, gates, stale, inheritance}`）；
- 两个调用都必须复用页面既有的 `api()` / `mediaUrl()` 封装与鉴权，不新开 `fetch` 风格。

### C4 结果渲染

- 新增容器 `#drawingFlowPanel`，把 `flow.steps` 渲染为步骤表：步骤名（`title`）、
  状态（`status`）、失败时的 `error_code` 与 `error_message`；
- 渲染 `cad_ir` 摘要：`entities` 总数、`layers` 数、实体类型计数
  （`LINE` / `DIMENSION` / `ARC` / `SPLINE` / ...），数据来自 flow 或只读端点，
  不得在前端重新解析 DWG；
- `drawing_flow` 项目不得再落入「原图占位」分支（占位只服务 `blocked_*`）。

### C5 失败必须可见

步骤 `failed` 时页面必须展示 `error_code` 与 `error_message`，不得只显示「解析失败，请重试」，
也不得吞掉 `detail` 里的字段看板（`fields.{written,pending,conflict,missing}`）。

### C6 不引入第二套解析器

前端只调统一服务；不得出现 ODA / ODAFileConverter / xvfb / 本地转换命令或本地 CLI 调用。
DWG 的解析能力只有一份，在服务端。

## 4. 不在本批范围

- 不改 `POST /parse` 的视觉路径与 `file_preflight.vision_gate_error`（见
  `docs/specs/dwg-capability-truth-and-audit.md`）；
- 不改 drawing-flow 的服务端步骤实现与错误分类（见
  `docs/specs/drawing-flow-error-taxonomy.md`）；
- 不做 DWG 解析结果的人工确认 UI 改造，只做"看得见"。

## 5. 验收标准

1. `tests/test_drawing_flow_frontend_wiring_red.py` 全绿；
2. 手工：新建项目上传 `酒盒.dwg` → 2.1 解析按钮可点 → 点一次后出现步骤表与
   `cad_ir` 摘要（`entities/layers/类型计数`），页面任何位置不出现「请上传 PNG」；
3. 手工：PNG 项目行为与本批之前逐字一致（视觉路径仍走 `/parse`）。

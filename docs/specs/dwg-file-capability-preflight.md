# 规格：DWG 文件能力契约、格式预检与正确失败 —— DWG 支持第 1 批

> 批次：包装行业 **DWG 支持第 1 批**（共 6 批）。红测：`tests/test_dwg_file_capability_preflight_red.py`。
> 真实样本：`裕同包装项目-待开发/酒盒.dwg`（686195 B）、`裕同包装项目-待开发/圆盘盒.dwg`（889062 B），
> 两份文件头都是 `AC1027`（AutoCAD 2013）。样本**只读**，测试不得修改。
> 本批**只做能力边界**：把「页面宣称支持、后端实际错误处理」的假支持改成「识别准确 + 明确拒绝 +
> 可审计 + 可重试」。**不装转换器、不解析 DXF、不识别盒型、不改成本。**

状态：Spec + 红测（已实现）
红测：`tests/test_dwg_conversion_adapter_red.py` `tests/test_dwg_conversion_quality_repair_red.py` `tests/test_dwg_file_capability_preflight_red.py`

## 0. 术语

| 名称 | 含义 |
| --- | --- |
| 直接视觉 | 把文件字节作为 `image`/`image_url` 块交给视觉大模型 |
| DWG 转换能力 | 把 DWG 转成 DXF/预览图的受控转换器（**本批不装**，第 2 批落地） |
| CAD 矢量解析 | 从 DXF 读图层/实体/尺寸（第 3 批） |
| 3D 几何导入 | 现有 STEP/STP → OCCT 链路（`services/step_import.py`） |

**DWG ≠ 3D 模型。** 两份样本是包装刀模/展开图（二维），即使文件内含三维实体，现有链路也无法判定。

## 1. 现状取证（代码定位 + 实跑，均为实测）

### 1.1 前端宣称支持（`tech_app/frontend/`）

| 位置 | 原文 |
| --- | --- |
| `home.js:253` | 「支持格式：3D 格式 STEP/STP/SLDPRT/STL/SAT；2D 格式 **DWG**/DXF/PDF」，`accept="image/*,.pdf,.dwg,.dxf,.step,…"` |
| `tech-task.js:132` | `accept="image/*,.pdf,.dwg,.dxf,.step,.stp,.sldprt,.stl,.sat"` |
| `requirement.js:7` | 「支持 PNG、JPG、PDF、**DWG**、DXF 等格式」，`accept="image/*,.pdf,.dwg,.dxf"` |
| `requirement-create.js:26` | `drawing_2d` 角色 `accept=".dwg,.dxf,.pdf,image/*"` |
| `app.js:1438` | 反而写着「1.1 允许上传的 PDF / DWG / DXF 到这一步还解析不了」 |

→ 同一产品里「宣称支持」与「自认不解析」并存，用户无法判断。

### 1.2 后端分类只按扩展名，且分类结果不参与门禁

- `services/vision.py:184-202` `build_input_manifest()`：`_IMAGE_EXTS=(.png,.jpg,.jpeg,.webp,.gif,.bmp)`→
  `image/vision`；`_EXTRACTABLE_DOCUMENT_EXTS=(.pdf,.docx)`→`document`；`_TEXT_EXTS`→`text`；
  **其余一律 `kind="unsupported", extraction="none"`**。DWG 落这一档，**没有任何 magic/版本判断**。
- `services/vision.py:504-518` `_base_blocks()`：**无条件**把主文件塞成图像块
  `claude_client.image_block(image_bytes, filename)` —— 不读 `manifest`，分类结果**从不参与门禁**。
- `services/qwen_client.py:191-201` `_media_type_for()`：`.dwg` 落到兜底 `image/png`，
  于是请求体里出现 `data:image/png;base64,<DWG 原始字节>`。
- 实跑（本地 8010 + Qwen3.5-Plus）：Qwen 返回 **`The image format is illegal and cannot be opened`**。
  审计里同时写着 `kind=unsupported`、`extraction=none` —— **系统自己知道不支持，却仍然调用模型，
  并拿模型报错当作结论**。换 3.8-Max 不改变结论：失败发生在模型看到有效图像之前。

### 1.3 3D 入口无格式门禁

- `main.py:1425` `POST /api/projects/3d`（`upload_3d`）：**不校验格式**，直接
  `store.create_project(...)` → `tasks.submit(project_id, "import_3d", job, cad=True, …)`。
- `job()` 调 `step_import.import_step(content, fname)`；`services/step_import.py:99`
  用 `suffix = Path(filename).suffix or ".step"` 写临时文件后调 `cq.importers.importStep(tmp)`。
- 实跑：DWG 进 3D 入口 → 接口先返回 `project_id`/`task_id`，异步任务最终报
  **`STEP File could not be loaded`**。即**先建项目、再建注定失败的任务**，用户看到的是"已受理"。

### 1.4 上传侧只有大小上限

`main.py:1119` `_read_upload_limited()`：只做 `MAX_UPLOAD_BYTES` 限制（超限 413），
**无内容嗅探、无空文件/截断文件/扩展名与内容不符的区分**。

## 2. 契约 A：统一文件预检（新模块，纯函数）

新增 `tech_app/backend/services/file_preflight.py`（**不联网、不调模型、不读写库、不装依赖**）：

```python
detect_file_format(filename: str, content: bytes) -> dict
capabilities_of(detected: dict) -> dict
```

`detect_file_format()` 至少返回：

| 字段 | 说明 |
| --- | --- |
| `original_filename` | 只留文件名，不含路径 |
| `extension` | 小写扩展名（`.DWG` 与 `.dwg` 等价） |
| `detected_format` | 闭集：`dwg` / `dxf` / `step` / `iges` / `stl` / `pdf` / `raster_image` / `text` / `docx` / `unsupported` |
| `magic` | 前 16 字节的 ASCII 可打印形式（不可打印用 `.`） |
| `dwg_version` | 头部 `AC10xx`（如 `AC1027`）；非 DWG 为 `""` |
| `extension_content_mismatch` | 扩展名与内容不一致（如 DWG 字节命名为 `.png`） |
| `file_size` / `sha256` | 字节数 / 十六进制摘要 |
| `is_empty` / `is_truncated` | 空文件 / DWG 头部或 DXF 段落结构不完整 |
| `content_kind` | `text` / `binary` |

`capabilities_of()` 至少返回：

```json
{"direct_vision": false, "cad_vector_parse": false,
 "converter_required": true, "converter_available": false, "step_import": false, "geometry_3d": false}
```

### 2.1 判定规则（不许只看扩展名）

- **DWG**：前 6 字节匹配 `AC1006`–`AC1032`（样本为 `AC1027`）→ `detected_format="dwg"`；
  扩展名不是 `.dwg` → 同时 `extension_content_mismatch=true`。
- **DXF**：扩展名 `.dxf` 且内容含 `SECTION` 段标记（ASCII DXF）或 DXF 二进制头。
- **STEP**：内容以 `ISO-10303-21` 开头 → `step`；`iges` 以 `IGES`/`S` 段头为准；`stl` 以
  `solid ` 或二进制三角面片长度自洽为准。
- **PDF**：`%PDF-`；**raster_image**：`\x89PNG` / `\xff\xd8\xff` / `GIF8` / `RIFF….WEBP` / `BM`。
- 扩展名 `.dwg` 但 magic 不是 `AC10xx` → `detected_format` 按内容判、`extension_content_mismatch=true`。
- `direct_vision` 只对 `raster_image`（以及明文 `pdf` 经 `vision` 提取路径）为 `true`；
  **`dwg`/`dxf` 必须为 `false`**，`converter_required=true`。

## 3. 契约 B：稳定错误码（闭集）

新增统一错误码（`services/file_preflight.py` 导出，接口与审计共用）：

| `stable_error_code` | HTTP | 用户文案（中文） | `retryable` |
| --- | --- | --- | --- |
| `FILE_EMPTY` | 400 | 文件为空，请重新上传 | 是 |
| `FILE_TOO_LARGE` | 413 | 文件超过大小上限 | 是 |
| `FILE_EXTENSION_CONTENT_MISMATCH` | 422 | 文件扩展名与实际内容不一致 | 是 |
| `FILE_CORRUPTED` | 422 | 文件不完整或已损坏 | 是 |
| `DWG_CONVERTER_NOT_INSTALLED` | 415 | 已识别为 DWG；当前环境尚未安装 CAD 转换服务，暂时无法解析 | 是（装转换器后同一文件可重试） |
| `DWG_NOT_A_3D_MODEL` | 415 | DWG 不是 3D 实体格式，无法直接做 3D 解析 | 否（改走 2D 图纸流程） |
| `FILE_FORMAT_UNSUPPORTED` | 415 | 该文件格式暂不支持解析 | 否 |
| `DWG_CONVERSION_FAILED` | 502 | DWG 转换失败 | 是 |
| `DWG_CONVERSION_TIMEOUT` | 504 | DWG 转换超时，请稍后重试 | 是 |
| `DWG_CONVERTER_OUTPUT_MISSING` | 502 | 转换器没有产出可用图纸文件 | 是 |
| `DWG_CONVERTER_OUTPUT_INVALID` | 502 | 转换产出的图纸文件为空或格式无效 | 是 |
| `DWG_CONVERTER_OUTPUT_TOO_LARGE` | 502 | 转换产出超过大小上限 | 是 |
| `DWG_CONVERTER_UNSAFE_PATH` | 500 | 转换器返回了不安全的输出路径 | 否 |
| `FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION` | 500 | 测试用转换器不允许在生产环境启用 | 否 |
| `DWG_PARSE_FAILED` | 502 | DWG 转换成功但图纸解析失败 | 是 |
| `CAD_IR_SOURCE_MISSING` | 422 | 项目里没有可用的 DXF 转换产物，请先重跑图纸转换 | 是 |
| `CAD_IR_ENTITY_LIMIT_EXCEEDED` | 413 | 图纸实体数超过解析上限，请拆分图纸或提高上限后重试 | 否 |
| `DWG_CONVERTER_BINARY_UNUSABLE` | 500 | 已配置的 DWG 转换器不可用（不存在 / 不可执行 / 版本不符） | 否 |
| `PACKAGING_SEMANTICS_SOURCE_MISSING` | 422 | 项目里没有可用的 CAD 图纸解析结果，请先重跑图纸解析 | 是 |
| `PACKAGING_LAYER_RULES_INVALID` | 500 | 包装图纸图层规则配置缺失或不可用，请联系系统管理员 | 否 |

后 6 条（`DWG_CONVERSION_TIMEOUT` / `…OUTPUT_MISSING` / `…OUTPUT_INVALID` / `…OUTPUT_TOO_LARGE` /
`…UNSAFE_PATH` / `FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION`）由 DWG 第 2 批「受控转换服务」提出，
本节表即为其唯一权威来源；第 2 批的 `CONVERSION_ERROR_CODES` 必须是本表的子集且数值逐条一致。

末 2 条（`CAD_IR_SOURCE_MISSING` / `CAD_IR_ENTITY_LIMIT_EXCEEDED`）由 DWG 第 3 批「DXF 确定性解析与
统一 CAD IR」提出（见 `docs/specs/dxf-cad-ir.md` §6.1），同样并入本表闭集：第 3 批的解析器只允许
抛本表内的码，不许自创。

末条（`DWG_CONVERTER_BINARY_UNUSABLE`）由 DWG 第 1/2 批修复「转换质量门槛与转换器配置」提出
（见 `docs/specs/dwg-conversion-quality-repair.md` §6）：配置的二进制不存在、不可执行、是
`sh`/`bash`/`python*` 之类解释器，或实际版本与 `DWG_CONVERTER_VERSION` 不一致时抛此码，
`detected` 必须带 `provider` / `binary` / `expected_version` / `actual_version`。
末 2 条（`PACKAGING_SEMANTICS_SOURCE_MISSING` / `PACKAGING_LAYER_RULES_INVALID`）由 DWG 第 4 批
「包装图纸语义」提出（见 `docs/specs/packaging-drawing-semantics.md` §9），同样并入本表闭集。
前 15 条 + 第 2 批 6 条 + 第 3 批 2 条 + 本修复 1 条 + 第 4 批 2 条 = **20 条**，
闭集之外一律视为实现缺陷。

- 每条响应体必须含 `stable_error_code`、`message`、`detected`（预检结果）、`retryable`，
  **不得**只回自由文本异常，**不得**把第三方/模型原始报错或堆栈透传给用户。
- 模型恰好返回 400 不是判定依据；判定必须在调用模型**之前**完成。

## 4. 契约 C：模型调用门禁（本批核心）

- 结构化失败统一用 `file_preflight.FileCapabilityError`（`Exception` 子类，字段
  `stable_error_code` / `http_status` / `detected` / `retryable` / `message`）。
- `vision.parse_drawing()` / `vision.verify_drawing()`：主文件 `capabilities.direct_vision == false` 时
  **必须**在构造内容块**之前**抛出 `FileCapabilityError`，**不得**出现 `image`/`image_url` 块，
  **不得**调用 `claude_client.run`。
- DWG 主文件的失败码固定 `DWG_CONVERTER_NOT_INSTALLED`（`converter_available=false` 时）。
- 佐证附件里出现 DWG/DXF：只发**文本占位**
  （如「【佐证文件 xxx.dwg 需要 CAD 转换服务，本次未作为解析依据】」），**不得**发图像块。
- 非 DWG 的既有路径（`raster_image` → vision、`.pdf/.docx` → 文本提取、`.txt` → 文本）**不得回归**。
- 门禁必须在 `build_input_manifest` 的结果上做，且 `manifest` 必须进入审计与响应 `detected`。

## 5. 契约 D：3D 入口同步拒绝

`POST /api/projects/3d` 在**同步阶段**（`_read_upload_limited` 之后、`store.create_project` 之前）：

- 收到 `detected_format != "step"` 的 `iges`/`stl` 之外的**二维/未知格式**（含 DWG、DXF、PDF、图片）→
  返回 `DWG_NOT_A_3D_MODEL`（DWG）或 `FILE_FORMAT_UNSUPPORTED`，HTTP 415。
- 拒绝方式固定为 `raise HTTPException(status_code=<HTTP>, detail={"stable_error_code": …,
  "message": …, "detected": …, "retryable": …})`。
- **格式预检必须排在 `step_import.AVAILABLE` 检查之前**：环境没装 CadQuery 时，DWG 也必须先得到
  `DWG_NOT_A_3D_MODEL`，而不是含糊的 503。
- **不得**创建项目、**不得**创建异步任务、**不得**调用 `step_import.import_step`。
- 扩展名为 `.step` 但内容是 DWG → 同样拒绝（`FILE_EXTENSION_CONTENT_MISMATCH`）。
- 真正的 STEP 文件**不得回归**：仍走现有 `store.create_project` + `tasks.submit(..., cad=True)` 路径。

## 6. 契约 E：前端能力展示必须真实

- 本批允许继续上传 DWG，但所有入口的能力说明必须写成
  「可上传，DWG 需 CAD 转换服务解析（当前环境未安装）」，**不得**表达成"模型可直接解析 DWG"。
- `home.js:253` 的 2D 格式标签区、`tech-task.js:132`、`requirement.js:7`、`requirement-create.js:26`
  四处都要有同样的能力说明，文案取自同一处（不许四份硬编码不同说法）。
- 未发生转换时，任何卡片/看板**不得**把 DWG 标成「解析完成」。
- `app.js:1438` 的诚实说明保留（可作为能力文案来源之一）。

## 7. 契约 F：原文件保全、审计与可重试

- 即使当前不能解析：原文件安全保存（现有 `tech_data/<id>/source.dwg` 行为保持），
  解析失败**不得**删除项目、附件或历史记录。
- 审计条目至少含：`original_filename`、`detected_format`、`extension`、`magic`、`dwg_version`、
  `file_size`、`sha256`、`selected_pipeline`、`converter_available`、`parse_status`、
  `stable_error_code`、`retryable`。
- 审计**不得**包含文件原始字节、base64、模型密钥、内部堆栈或绝对部署路径。
- 同一原文件在转换器安装后必须可重试（不得因为首次失败而进入不可重试状态）。

## 8. 非目标（本批一律不做）

- 不安装、不接入、不调用任何 DWG 转换器（第 2 批）；`converter_available` 恒为 `false`。
  （第 2 批接入后该项改由 `cad_converter.capability()` 给出；默认未安装时仍为 `false`，
  `CAD_CONVERTER=none` 显式锁死 `false`。）
- 不解析 DXF 实体、图层、尺寸（第 3 批）。
- 不识别刀线/压痕线/盒型/长宽高（第 4 批）。
- 不改 Agent 会话、右侧看板、业务流程门禁（第 5 批）。
- 不改 3D 分流实现与真实样本 E2E（第 6 批）；本批只要求 3D 入口**拒绝**非 STEP。
- 不改包装成本公式/费率/最低收费口径（属成本修复第 3 批，未裁决）。
- 不改半导体/电池/电器三行业任何链路。
- 不为通过测试而放宽既有红测；不改两个真实 DWG 样本文件。
- 不 commit / push / MR / tag / Release / 部署。

## 9. 自动化验收

| 命令 | 期望 |
| --- | --- |
| `./open-claude/.venv/bin/python tests/test_dwg_file_capability_preflight_red.py` | 实现前 FAIL（红）；实现后 OK |
| 三行业 + 包装既有套件（第 1–8 批） | 不回归 |
| `./open-claude/.venv/bin/python /tmp/run_pkg.py 1` | 除既有 17 条与包装成本待裁决项外不新增失败 |

## 10. 人工验收

- 上传 `酒盒.dwg`：界面显示"已识别为 DWG（AC1027），需 CAD 转换服务"，**不出现**模型报错文案。
- 上传 `圆盘盒.dwg` 到 3D 入口：**同步**返回 415 + `DWG_NOT_A_3D_MODEL`，项目列表不新增。
- 把 `酒盒.dwg` 改名为 `酒盒.png` 后上传：报 `FILE_EXTENSION_CONTENT_MISMATCH`，**不调用**视觉模型。
- 把 `酒盒.dwg` 改名为 `酒盒.step` 后进 3D 入口：同样拒绝，**不创建**异步 CAD 任务。
- 断网/无转换器环境重复上传同一 DWG：每次都是同一个稳定错误码，原附件仍在，可直接重试。

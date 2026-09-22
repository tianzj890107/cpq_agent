# 规格：DWG 受控转换服务（转换适配器层） —— DWG 支持第 2 批

> 批次：包装行业 **DWG 支持第 2 批**（共 6 批）。前置：第 1 批 `docs/specs/dwg-file-capability-preflight.md`
> （文件能力契约 / 格式预检 / 正确失败）已实现。
> 红测：`tests/test_dwg_conversion_adapter_red.py`。真实样本：`裕同包装项目-待开发/酒盒.dwg`（686195 B）、
> `裕同包装项目-待开发/圆盘盒.dwg`（889062 B），文件头均为 `AC1027`；样本**只读**，测试不得修改。
> 本批目标：建立 `DWG → 中间格式` 的**受控、可审计、可替换**转换层。
> **不解析 DXF 实体（第 3 批）、不识别盒型（第 4 批）、不改 Agent/看板（第 5 批）、不改成本公式。**

状态：Spec + 红测（已实现）
红测：`tests/test_dwg_conversion_adapter_red.py` `tests/test_dwg_conversion_quality_repair_red.py`

## 0. 术语与两层验收

| 名称 | 含义 |
| --- | --- |
| 转换适配器 | 把 DWG 转成 DXF/预览图的独立实现（本地 CLI、外部服务、测试 fake） |
| 转换编排层 | 预检 → 选适配器 → 幂等 → 临时目录 → 调用适配器 → 校验产物 → manifest → 审计 |
| A 层验收 | **编排层**通过：fake adapter 红测 + 安全/错误/幂等/manifest 全绿 |
| B 层验收 | **真实转换器**通过：两份样本真出可打开的 DXF 与预览，且非空白 |
| manifest | 一次转换的可审计记录（§3） |

**判定文案（唯一口径）**

- 只完成 A 层：只能写「**DWG 编排能力完成，真实转换能力未验收**」，`capability().support_claim == "conversion_available"`、`dwg_supported == False`。
- A + B 都完成：第 6 批金标通过后才允许写「DWG 支持完成」；**本批即使 B 层通过也不得宣称支持完成**。

## 1. 现状取证（实测）

### 1.1 本机 / 当前环境没有任何 DWG 转换器

```
$ for c in ODAFileConverter dwg2dxf dwgread libredwg soffice libreoffice inkscape teigha; do command -v $c; done
（全部 MISSING，无输出）
$ ./open-claude/.venv/bin/python -c "import ezdxf"            # OK（已在 venv 里）
$ ./open-claude/.venv/bin/python -c "import dxfgrabber"       # ModuleNotFoundError
$ ./open-claude/.venv/bin/python -c "import libredwg"         # ModuleNotFoundError
$ ./open-claude/.venv/bin/python -c "import pyautocad"        # ModuleNotFoundError
```

`requirements.txt:14` 只有 `openpyxl==3.1.5`；`cadquery` 在 `requirements.txt:44` 仍被注释；**没有 `ezdxf`**。
`tech_app/backend/services/` 下**没有任何** DWG/DXF/转换相关模块（`drawing2d.py` 是 OCCT 出图，
`step_import.py` 只吃 STEP/STP）。

→ 结论：**当前只能在生产配置里保持「转换能力未安装」**，本批可交付物是编排层 + fake adapter；
真实转换器必须由用户单独拍板（§6），拍板前不得把 `converter_available` 置真、不得把 fake 当默认。

### 1.2 上传与 3D 入口现状（第 1 批已修正，本批只挂接，不改语义）

- `main.py:1188` 需求图纸上传：`_read_upload_limited()`（`main.py:1119`，只有 `MAX_UPLOAD_BYTES` 上限）
  → `store.create_project()`；DWG 落盘为 `tech_data/<project_id>/source.dwg`。
- `main.py:1435` 3D 入口：第 1 批后必须在**同步阶段**拒绝非 STEP/IGES/STL。
- `store.py:67` `project_dir()` / `store.py:71` `attachments_dir()` / `store.py:75` `geometry_dir()`
  是既有落盘约定，转换产物**必须**沿用同一套 blob 后端（`_blob().ensure_local_dir()` + `sync_dir()`），
  不得自建第二套目录约定。
- `store.py:101` `audit()` / `store.py:105` `list_audit()` 是唯一审计入口。

## 2. 契约 A：模块结构与适配器协议

新增包 `tech_app/backend/services/cad_converter/`（**纯编排 + 适配器，不联网、不调模型、不解析 DXF**）：

```
cad_converter/
  __init__.py          # 对外稳定接口（§2.1）
  service.py           # 转换编排（预检/幂等/临时目录/校验/manifest）
  persistence.py       # 产物与 manifest 落盘（唯一写盘入口）
  errors.py            # CONVERSION_ERROR_CODES（§4）
  adapters/
    base.py            # 适配器协议 + ConversionRequest/ConversionResult
    fake.py            # 测试用 fake（CI 可用，生产默认禁止）
    local_cli.py       # 本地 CLI 适配器骨架（转换器不存在时不注册）
    remote.py          # 外部服务适配器扩展点（本批不实现，只留接口）
```

### 2.1 对外稳定接口（`cad_converter/__init__.py`）

```python
CONVERSION_ERROR_CODES: dict[str, dict]          # §4，必须是第 1 批 §3 闭集的子集
SIMULATED_ADAPTER_NAME = "fake"

def capability(*, env: str | None = None) -> dict
def list_adapters() -> list[str]                 # 已注册适配器名（含未安装状态不算）
def get_adapter(name=None, *, env=None, allow_simulated=None) -> object | None   # 未安装返回 None
def convert_drawing(project_id, filename, content, *, adapter=None, attachment_name="",
                    drawing_version=1, timeout_seconds=None) -> dict   # 返回 manifest（§3）
def load_manifest(project_id, conversion_id) -> dict | None
def list_conversions(project_id) -> list[dict]   # 全部（含 failed），时间倒序
def latest_manifest(project_id, *, status="ok") -> dict | None
```

`capability()` 至少返回：

| 字段 | 说明 |
| --- | --- |
| `available` | 有可用适配器（含 fake） |
| `simulated` | 可用适配器是 fake |
| `adapter_name` / `converter_version` | 可用适配器名与版本；不可用为空串 |
| `dwg_conversion` / `preview_render` / `three_d_conversion` | 三项能力分别声明 |
| `env` | `local` / `ci` / `production` |
| `support_claim` | 不可用 → `"orchestration_only"`；真实适配器可用 → `"conversion_available"`；fake 可用 → `"orchestration_only"` |
| `dwg_supported` | **本批恒为 `False`**（第 6 批金标通过后才可能为真） |
| `stable_error_code` | 不可用时 `DWG_CONVERTER_NOT_INSTALLED`，可用时 `""` |
| `message` | 中文用户文案（不可用时含「转换」字样） |

**环境在调用时读取**（不得在 import 时固化），便于测试与热更新：测试用 `mock.patch.dict(os.environ, …)`。

### 2.2 适配器协议（`adapters/base.py`）

适配器必须实现且只实现下列 5 个方法；**适配器不读数据库、不读项目目录、不看 Agent/路由**：

```python
def capability(self) -> dict                 # {"name","version","dwg_conversion","preview_render","three_d_conversion","simulated"}
def inspect(self, request) -> dict           # {"detected_dwg_version","has_2d_entities","three_d","warnings"}
def convert_to_dxf(self, request) -> dict    # ConversionResult
def render_preview(self, request, dxf_path) -> dict   # ConversionResult
def convert_3d_if_supported(self, request) -> dict    # {"status","artifact_path","error_code"}
```

`ConversionRequest`（编排层构造，适配器只读）：

| 字段 | 说明 |
| --- | --- |
| `project_id` / `attachment_name` / `drawing_version` | 关联信息 |
| `source_path` | **编排层写在临时目录里的固定名文件**（`source.dwg`） |
| `source_sha256` / `source_format` / `detected_dwg_version` | 预检结果 |
| `output_dir` | 适配器**唯一允许写入**的临时目录 |
| `timeout_seconds` | 单次调用超时（含 `inspect`） |
| `options` | 来自环境/配置的选项字典（明文可审计，不含密钥） |

**`request.source_filename` 与用户原始文件名无关**：固定 `source.dwg`。用户原名只出现在 manifest 的
`original_filename`（仅 basename），转换器永远拿不到用户可控路径。

`ConversionResult`：`{"status": "ok"|"failed", "dxf_path": Path|None, "preview_paths": [Path],
"warnings": [str], "error_code": str|None, "stderr_digest": str|None, "three_d": {...}}`。

### 2.3 适配器选择

- `CAD_CONVERTER`（默认 `auto`）：`auto` 按顺序探测真实适配器，全都没有 → **未安装**（返回 `None`）。
- `CAD_CONVERTER=fake`：仅当 `APP_ENV ∈ {local, ci}` **且** `CAD_CONVERTER_ALLOW_SIMULATED=true` 才生效；
  生产或未显式允许 → 抛 `FileCapabilityError("FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION")`。
- `CAD_CONVERTER=<name>` 指向未注册/未安装的适配器 → 视为未安装（`DWG_CONVERTER_NOT_INSTALLED`），
  **不得**静默回退到 fake，**不得**静默回退到别的适配器。
- fake 结果必须 `is_simulated=True`；编排层必须把它透传进 manifest，**任何 UI/报告都不得把它当真实转换**。

### 2.4 fake adapter 契约（CI 与红测都用它，生产禁止启用）

`adapters/fake.py` 必须提供 `FakeAdapter`，签名固定（红测按此调用）：

```python
class FakeAdapter:
    name = "fake"

    def __init__(self, *, version="0.0.0", failure_mode=None, three_d="not_present",
                 preview_format="png", stderr="", dxf_text=None): ...

    @property
    def calls(self) -> list[dict]      # 每次方法调用一条：{"method","source_sha256","source_filename","conversion_id"}
    @property
    def convert_calls(self) -> int     # convert_to_dxf 的调用次数（并发/幂等断言用）
```

- `failure_mode` 闭集：`None` / `timeout` / `nonzero_exit` / `missing_output` /
  `empty_output` / `oversized_output` / `unsafe_path`。
  - `timeout`：`convert_to_dxf` 阻塞超过 `request.timeout_seconds`（阻塞 2s）→ 编排层必须报 `DWG_CONVERSION_TIMEOUT`。
  - `nonzero_exit`：返回 `status="failed"`（等价转换器非零退出）→ `DWG_CONVERSION_FAILED`。
  - `missing_output`：`status="ok"` 但既不写文件也不返回路径 → `DWG_CONVERTER_OUTPUT_MISSING`。
  - `empty_output`：写出 0 字节 DXF → `DWG_CONVERTER_OUTPUT_INVALID`。
  - `oversized_output`：写出 128 KiB DXF → 超过 `CAD_CONVERTER_MAX_OUTPUT_BYTES` 时 `DWG_CONVERTER_OUTPUT_TOO_LARGE`。
  - `unsafe_path`：把产物写到 `tempfile.gettempdir()/dwg-conv-escape/escaped.dxf`（`output_dir` 之外）
    → 编排层必须报 `DWG_CONVERTER_UNSAFE_PATH`，且**不得**把它复制进正式产物目录。
- `stderr` 非空时返回 `stderr_digest = sha256(stderr)`；**只允许摘要进 manifest**，原文不得落盘或返回。
- `preview_format` ∈ {`png`,`svg`,`pdf`}，写出的预览必须 magic 合法。
- fixture 产出的 DXF 必须是**可读的 ASCII DXF**（含 `SECTION`/`ENTITIES`），且 fake 必须线程安全
  （`convert_calls` 用锁自增）。
- **唯一允许的失败注入点**是构造参数；适配器不得读环境变量决定失败，也不得按文件名猜测。
- `failure_mode` 传闭集之外的值必须立刻 `ValueError`（不许静默当成 `None`）。

### 2.5 超时兜底由编排层负责

适配器可能挂住，**编排层必须自己实现超时**（本地 CLI 用 `subprocess.run(timeout=…)`，
进程内/服务型适配器用线程 + `join(timeout)`），超时统一报 `DWG_CONVERSION_TIMEOUT`；
不得依赖转换器自己退出。同一 `cache_key` 的并发调用必须加锁串行，只做一次真实转换（§4.2）。

### 2.6 与第 1 批的接线（本批唯一的既有行为改动）

- 第 1 批把 `file_preflight.capabilities_of()["converter_available"]` **硬编码为 `False`**
  （`services/file_preflight.py:177-208`）。本批必须把它接到
  `cad_converter.capability()["available"]`，默认环境（`CAD_CONVERTER=auto` 且本机无真实转换器）
  仍然为 `False`，因此第 1 批的门禁语义（DWG 不进视觉模型、DWG 不进 STEP 导入器）**不得改变**。
- `CAD_CONVERTER=none` 必须让 `converter_available` 稳定为 `False`（本地/CI 用来锁死"未安装"态）。
- `DWG_CONVERTER_NOT_INSTALLED` 仍由第 1 批的门禁抛出；本批只是让 `converter_available` 变成真实值。

## 3. 契约 B：产物与 manifest

### 3.1 产物

- 规范化 **DXF**（角色 `dxf`）——第 3 批的唯一矢量解析输入。
- **预览图**至少一种（角色 `preview`）：PNG / SVG / PDF。
- 落盘位置：`persistence.artifact_dir(project_id, conversion_id)`，内部走
  `_blob().ensure_local_dir(f"{project_id}/conversions/{conversion_id}")`，结束时 `sync_dir()` 同步对象存储。
- 产物必须与**项目 + 附件（`source_sha256`）+ 图纸版本（`drawing_version`）**绑定，写进 manifest。
- 临时目录**只**用于转换过程，成功/失败都必须删除（§5）。

### 3.2 manifest（`persistence.save_manifest()` / `load_manifest()`）

必含字段（缺一项即红测失败）：

| 字段 | 说明 |
| --- | --- |
| `conversion_id` | 稳定 ID（12–32 hex），同一幂等键复用同一个 |
| `project_id` / `attachment_name` / `drawing_version` | 关联信息 |
| `original_filename` | **仅 basename**，不含路径分隔符 |
| `source_sha256` / `source_format` / `detected_dwg_version` | 来源与版本（`AC10xx`） |
| `converter_name` / `converter_version` | 适配器身份（**生效**的那一个：回退成功时是回退转换器） |
| `converter_role` / `fallback_used` / `primary_failure_code` / `attempts` | 受控回退链留痕（字段含义见修复 Spec `/2` §7；无回退时 `role="primary"`、其余为空/单条 attempt） |
| `quality` / `warning_count` / `error_count` / `warning_codes` / `diagnostics_*` | 产物质量门槛与诊断计数（修复 Spec `/2` §4/§5） |
| `conversion_options` | 本次选项（含超时、上限、`output_dir` 无关项） |
| `output_files` | `[{"role","filename","sha256","bytes"}]`，角色 ∈ {`dxf`,`preview`} |
| `output_sha256` | `{filename: sha256}`，必须能从磁盘文件重算一致 |
| `warnings` | 转换器警告（已脱敏） |
| `started_at` / `finished_at` | 非空字符串 |
| `status` | `ok` / `success_with_warnings` / `failed`（修复 Spec `/2` §5 的状态闭集） |
| `error_code` | 成功为 `None`，失败为 §4 稳定码 |
| `is_simulated` | fake → `True` |
| `acceptance_level` | `real` / `orchestration_only` |
| `cache_key` | `sha256(source_sha256 + adapter_name + adapter_version + options_digest)` |
| `converter_stderr_digest` | 转换器 stderr 的 sha256（**只存摘要，不存原文**）；无则 `None` |

- `manifest` **不得**包含：源文件字节、base64、密钥、绝对部署路径、第三方原始堆栈、模型输出。
- 失败也必须写一条 `status="failed"` manifest（含 `error_code`），但**不得覆盖/删除上一次成功产物**（§4.2）。

## 4. 契约 C：错误码与状态机

### 4.1 错误码（`cad_converter/errors.py` 的 `CONVERSION_ERROR_CODES`）

| `stable_error_code` | HTTP | `retryable` | 触发条件 |
| --- | --- | --- | --- |
| `DWG_CONVERTER_NOT_INSTALLED` | 415 | 是 | 无可用适配器 |
| `DWG_CONVERSION_FAILED` | 502 | 是 | 适配器返回失败/非零退出 |
| `DWG_CONVERSION_TIMEOUT` | 504 | 是 | 超过 `timeout_seconds` |
| `DWG_CONVERTER_OUTPUT_MISSING` | 502 | 是 | 声明成功但没有产物 |
| `DWG_CONVERTER_OUTPUT_INVALID` | 502 | 是 | 产物为空/不是 DXF/预览不是 PNG·SVG·PDF |
| `DWG_CONVERTER_OUTPUT_TOO_LARGE` | 502 | 是 | 单文件或总产物超过上限 |
| `DWG_CONVERTER_UNSAFE_PATH` | 500 | 否 | 适配器返回 `output_dir` 之外的路径 |
| `FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION` | 500 | 否 | 生产/未允许时选了 fake |

- 本表是**第 1 批 Spec §3 十五码闭集的子集**，`HTTP`/`retryable`/中文文案**必须逐条一致**；
  第 1 批 Spec §3 是唯一权威来源，两批冲突时以第 1 批为准（并回头改第 1 批）。
- 本批不新增任何第 1 批闭集之外的错误码；需要新码时先改第 1 批 Spec。
- 失败统一 `raise file_preflight.FileCapabilityError(stable_error_code, detected=…, message=…)`，
  **不得**只抛自由文本异常，**不得**透传转换器 stderr/堆栈。

### 4.2 状态机

```
                 ┌──────────────── DWG_CONVERTER_NOT_INSTALLED（未安装：终态，可重试）
待转换 ──预检──┤
                 └─ 幂等命中 ──→ 复用已成功 manifest（不调用适配器）
                          │
                          └─ 未命中 → 运行中 ─┬─ 成功 → 校验产物 ─┬─ 合规 → ok（写 manifest + 审计）
                                             │                  └─ 不合规 → failed（对应 OUTPUT_* / UNSAFE_PATH）
                                             └─ 失败/超时 → failed（DWG_CONVERSION_FAILED / TIMEOUT）
```

不变量：

1. 失败**不得**动上一次成功产物与 manifest；`latest_manifest(project_id)` 仍返回上一次成功版本。
2. 同一幂等键重复提交**不得**重复调用适配器、**不得**新建 `conversion_id`。
3. 适配器版本变化 → `cache_key` 变化 → 必须重新转换（**不得**复用旧缓存）。
4. 并发同一幂等键：只允许一次真实转换，其余等待并复用同一结果。
5. 源文件（项目附件）**只读**；任何失败路径都不得删除/改写原附件，装好转换器后可直接重试。

## 5. 契约 D：安全硬约束

- 使用 `tempfile.mkdtemp(prefix="dwg-conv-")` 建**独立临时目录**；输入文件固定命名 `source.dwg`。
- 输出路径必须 realpath 后仍在 `output_dir` 内；越界 → `DWG_CONVERTER_UNSAFE_PATH`。
- `.dwg` 之外的格式（含 PNG/PDF/STEP）→ `FILE_FORMAT_UNSUPPORTED`，**不得**进转换器。
- 禁止字符串拼接 shell：必须 `subprocess.run(argv_list, shell=False, timeout=…)`；
  包内**不得出现** `shell=True` / `os.system(` / 拼接命令字符串。
- 上限：`CAD_CONVERTER_TIMEOUT_SECONDS`（默认 120）、`CAD_CONVERTER_MAX_OUTPUT_BYTES`（默认 256 MiB）、
  `CAD_CONVERTER_MAX_OUTPUT_FILES`（默认 20）；超限 → 对应 `OUTPUT_TOO_LARGE` / `OUTPUT_*`。
- 不执行 DWG 内宏/脚本/外部参照，不联网拉资源；网络访问只允许出现在文件名含 `remote`/`aps` 的适配器模块
  （本批不实现），`service.py`/`persistence.py`/`adapters/local_cli.py`/`adapters/fake.py` 中
  出现 `requests` / `urlopen` / `http.client` / `socket` 一律视为违规。
- 失败后清理临时文件；正式产物保留。
- 不记录密钥：manifest/审计**只**存 `converter_stderr_digest`，不存 stderr 原文。
- 本批**不得** `import ezdxf`（DXF 实体解析属第 3 批）、**不得** import `vision` / `qwen_client` /
  `claude_client` / `step_import`（避免把 DWG 重新送回模型或 STEP 入口）。

## 6. 转换器选型（必须先拍板，未拍板则生产保持未安装）

| 维度 | ODA File Converter | Autodesk APS (Model Derivative) | LibreDWG (`dwg2dxf`) |
| --- | --- | --- | --- |
| DWG 版本兼容 | 覆盖到当前 DWG，需选版本 | 云端覆盖较新版本 | 对 `AC1027` 等新版本常不完整 |
| 文字/尺寸/块/图层 | 保留较完整 | 保留（转换器输出为准） | 文字/尺寸丢失风险高 |
| 2D/3D 实体 | 2D+3D，取决于输出格式 | 可出 STEP/IGES（需付费能力） | 基本只做 2D |
| 部署方式 | 本地二进制（含 GUI 依赖，需 headless 验证） | HTTPS 上传下载，需出网 | 本地库/CLI，易装 |
| 授权/许可证 | 免费版限非商业；**已由业务/法务确认可用于本 CPQ 生产环境** | 按量收费，需账号与合规评审 | GPLv3，作为回退保留 |
| 服务器批量 | 需确认（非交互、并发、临时目录） | 支持，但受配额/时长限制 | 支持，但产出质量需评估 |
| 离线/联网 | 完全离线 | 必须联网（数据出境风险） | 完全离线 |

- 本批**不安装**任何一个；只把接口留好。选型由用户在第二批单独拍板。
- 未拍板时的默认配置：`CAD_CONVERTER=auto` 且探测不到任何真实适配器 → `capability().available == False`，
  `stable_error_code == "DWG_CONVERTER_NOT_INSTALLED"`，UI 显示「当前环境尚未安装 DWG 转换能力」。

**9-21 拍板结论（上表的落地）**：主转换器 = **ODA File Converter 27.1**
（`ACAD2018`/DXF + Audit/Repair），回退 = **LibreDWG 0.14**，只在主转换器明确失败时启用。
两份真实样本实测：模型空间实体类型与数量、图层名、文字/尺寸文本、包围盒、渲染像素完全一致
（0 像素差）；ODA 的块定义更干净、转换 stderr 为空。配置项（`DWG_CONVERTER_PROVIDER/BINARY/VERSION/
WRAPPER`、`DWG_CONVERTER_FALLBACK_*`）、argv 形状、wrapper 校验、`auto` 顺序与回退链留痕契约
一律以 `docs/specs/dwg-conversion-quality-repair.md`（`dwg-conversion-repair/2`）为准；本文件
§1.1 的「本机没有任何 DWG 转换器」是 9-20 的历史取证，现状见修复 Spec §1。

## 7. 契约 E：三环境能力配置

| 环境 | `APP_ENV` | `CAD_CONVERTER` | `CAD_CONVERTER_ALLOW_SIMULATED` | 期望 |
| --- | --- | --- | --- | --- |
| 本地开发 | `local` | `auto`（或 `fake`） | `true`（仅本地） | 有真转换器则真转；否则可用 fake 验证编排 |
| CI | `ci` | `fake` | `true` | 只跑 A 层；**不得**把 fake 结果当真实转换 |
| 生产 | `production` | `auto` / 真实适配器名 | `false` | 没装真转换器就如实报未安装；选 fake 直接拒 |

- 生产**禁止**把 fake 设为默认：`APP_ENV=production` + `CAD_CONVERTER=fake` → `FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION`。
- 三环境都必须能通过 `GET /api/capabilities/cad-converter` 与 `/api/health` 的 `cad_converter` 字段问出
  「装没装转换器、能不能转」，**不允许**让前端用写死的「已安装」文案替代。

## 8. 真实样本验收（A / B 两层）

### 8.1 A 层（编排层，本批必须交付）

- `tests/test_dwg_conversion_adapter_red.py` 全绿（fake adapter 驱动）。
- 安全、错误、幂等、并发、manifest、审计全部覆盖。

### 8.2 B 层（真实转换器，需先拍板）

脚本 `tech_app/tools/dwg_conversion_smoke.py`（只读样本，不入库、不改样本）：

1. 打印 `capability()`；未安装 → 明确输出 `SKIP（未安装真实转换器）：A 层已验收 / B 层未验收`，退出码 0。
2. 已安装 → 对 `酒盒.dwg`、`圆盘盒.dwg` 各转换一次，打印 manifest 摘要。
3. 判定：DXF 可被 `ezdxf.readfile()` 打开且**实体数 > 0**、**图层数 > 0**；至少一个预览文件非空且 magic 合法；
   预览**不是空白页**（像素非全白/非全透明）；`is_simulated == False`、`acceptance_level == "real"`。
4. 任一项不满足 → 退出码非 0，并打印「B 层未通过」。

> `ezdxf` 属于第 3 批正式依赖；本批 smoke 脚本允许 `try/except ImportError` 后降级为
> 「DXF 可打开性未验证」，**不允许**把 fake 产物拿去充当 B 层证据。

## 9. 回滚策略

- 回滚粒度：`CAD_CONVERTER` 改回 `auto` 且无真实适配器 → 立即回到「未安装」的诚实状态，
  上传/解析行为与第 1 批一致（DWG 不被送进模型或 STEP）。
- 已产生的 conversion 产物与 manifest 保留可读（不自动删除历史数据），`list_conversions()` 仍能回看。
- 回滚**不得**删除历史会话、项目、附件、产物或审计；升级/降级转换器版本后旧产物标记 `stale_reason`
  而不是被覆盖。
- 转换器升级导致缓存失效：`cache_key` 含 `converter_version`，旧产物保留、新转换新建 `conversion_id`。

## 10. 非目标（本批一律不做）

- 不解析 DXF 实体/图层/尺寸/文字（第 3 批）；不 import `ezdxf`。
- 不识别刀线/压痕线/盒型/长宽高（第 4 批）。
- 不改 Agent 会话、右侧看板、流程门禁（第 5 批）；**不把转换器逻辑写进 Agent prompt**，
  **不让 Agent 决定命令行参数**。
- 不改 3D 分流实现与 2D/3D 判定状态机（第 6 批）；本批 `convert_3d_if_supported` 只返回状态，不接 STEP 解析。
- 不改成本公式、费率、最低收费口径；不运行 Excel/公式。
- 不从预览截图反推精确尺寸。
- 不改半导体/电池/电器三行业任何链路；不改两个真实 DWG 样本。
- 不 commit / push / MR / tag / Release / 部署。

## 11. 第 3 批所依赖的稳定接口（本批必须冻结）

| 接口 | 第 3 批用法 |
| --- | --- |
| `list_conversions(project_id)` / `latest_manifest(project_id)` | 找到可解析的最新成功转换 |
| `manifest["conversion_id"]` | CAD IR 的来源版本标识 |
| `manifest["output_files"][role=="dxf"]` + `persistence.artifact_dir()` | DXF 文件路径 |
| `manifest["source_sha256"]` / `detected_dwg_version` / `converter_name` / `converter_version` | IR 的 `source_*` 溯源字段 |
| `manifest["warnings"]` / `error_code` / `status` | 解析前置状态与失败原因 |
| `manifest["output_files"][role=="preview"]` | 视觉辅助/人工复核的预览图（第 4 批） |
| `unit_status` | 由第 3 批产出；本批只保证 manifest 不含任何"单位已确认"的结论 |

## 12. 自动化验收

| 命令 | 期望 |
| --- | --- |
| `./open-claude/.venv/bin/python tests/test_dwg_conversion_adapter_red.py` | 实现前 FAIL（红）；实现后 OK |
| `./open-claude/.venv/bin/python tests/test_dwg_file_capability_preflight_red.py` | 实现后 OK（第 1 批不得回归） |
| `./open-claude/.venv/bin/python tech_app/tools/dwg_conversion_smoke.py` | 未装转换器 → SKIP 且明确声明「B 层未验收」 |
| `./open-claude/.venv/bin/python /tmp/run_pkg.py 1` | 除既有失败集合与包装成本待裁决项外不新增失败 |

## 13. 人工验收

- 未装转换器：上传 `酒盒.dwg` → 界面显示「已识别为 DWG（AC1027），当前环境尚未安装 DWG 转换能力」，
  **不出现**模型报错、**不出现**「解析完成」。
- `APP_ENV=production` + `CAD_CONVERTER=fake`：`GET /api/capabilities/cad-converter` 返回
  `FAKE_CONVERTER_FORBIDDEN_IN_PRODUCTION`，**不产生**任何转换产物。
- 装好真实转换器（拍板后）再对同一附件重试：不重复上传、直接复用原附件，产出 DXF + 预览。
- 同一附件连续点两次「转换」：只产生一个 `conversion_id`，不产生重复产物。

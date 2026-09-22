# DWG 支持第 6 批：3D 分流、真实样本 E2E 与部署验收门禁（权威 Spec）

对应批次：DWG 支持第 6 批（最终验收）。上游 Spec：

| 批次 | Spec | 本批关系 |
| --- | --- | --- |
| 第 1 批 | `docs/specs/dwg-file-capability-preflight.md` | 复用：`detect_file_format()`、`STABLE_ERROR_CODES` 权威闭集、`import_3d_gate_error()` |
| 第 2 批 | `docs/specs/dwg-controlled-conversion-adapter.md`、`docs/specs/dwg-conversion-quality-repair.md` | 复用：`cad_converter.capability()/convert_drawing()/latest_manifest()`、manifest 契约、`CAD_CONVERTER_*` 上限与超时、A/B 层冒烟口径 |
| 第 3 批 | `docs/specs/dxf-cad-ir.md` | 复用：CAD IR 的实体类型与统计（3D 证据来源之一） |
| 第 4 批 | `docs/specs/packaging-drawing-semantics.md` | 复用：图层角色（判断**包装展开图**） |
| 第 5 批 | `docs/specs/dwg-semantics-agent-flow.md` | 复用：门禁与版本继承；本批**不重复实现** |

**本批不是"再加一批功能"，而是关闭"假支持"**：把 2D/3D 分流做成有证据的确定性判断，
把"支持 DWG"这句话变成**机器可验证的验收记录**，把上线前的检查变成**可执行的部署门禁脚本**，
并如实给出当前 Go/No-Go。

状态：Spec + 红测（已实现）
红测：`tests/test_dwg_final_acceptance_red.py` `tests/test_dwg_real_samples_e2e_red.py`

## 0. 当前状态实证（决定本批要证伪什么）

实测（2026-09-21，本仓库工作区；括号内为 9-20 的历史数字）：

- **第 1/2 批修复（ODA 修订）未落地**：`tests/test_dwg_conversion_quality_repair_red.py` 实测
  `Ran 43 / failures=8, errors=8` —— 16 条红全部来自
  `docs/specs/dwg-conversion-quality-repair.md`（`/2`）的 ODA 主 + 回退链要求。
- **第 3 批 `cad_ir` 已落地**：`tests/test_dxf_cad_ir_red.py` 实测 `Ran 46 / failures=1, skipped=1`
  （9-20 为 `Ran 45 / failures=42, skipped=1`）；唯一红是 ODA 修订新增的 `E5`
  （回退产物必须在 IR 里留痕：`source.converter_role`/`source.fallback_used` +
  `conversion_fallback_used` 警告）。
- **第 4 批 `packaging_semantics` 已落地**：`tests/test_packaging_semantics_red.py` 实测
  `Ran 59 / OK (skipped=1)`。
- **第 5 批 `packaging_drawing_flow` 未落地**：`tests/test_packaging_drawing_flow_red.py` 实测
  `Ran 54 / failures=54`。
- **2D/3D 分流完全不存在**：全仓没有 `dwg_dispatch*`；
  `grep -rn "three_d" tech_app/backend/services/cad_converter/` 只命中 `capability()` 的一行声明
  （`service.py:297`），**manifest 里不持久化任何三维证据** → 现在无法回答"这份图到底有没有 3D"。
- **转换器真实可用（ODA 主 + LibreDWG 回退）**：本机 ODA 27.1
  （`/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter`）与 `/opt/homebrew/bin/dwg2dxf`
  （LibreDWG 0.14）；34 服务器 ODA 27.1（`/home/data/cpq-tools/oda-file-converter-27.1/squashfs-root/AppRun`，
  经 `xvfb-run`）+ `/home/data/cpq-tools/current/bin/dwg2dxf`。许可口径、argv 与**受控回退链**见
  `docs/specs/dwg-conversion-quality-repair.md`（`dwg-conversion-repair/2`）；两份真实样本转换已由
  第 2 批 B 层验收覆盖。**两套驱动都只导出二维 DXF** → 含三维实体的图纸当前只能得到
  `3d_converter_unavailable`。
- **"支持 DWG"目前只能否**：`cad_converter/capability()` 的
  `support_claim == "conversion_available"`、`dwg_supported is False`（第 2 批 Spec §2.1 明确
  "第 6 批金标通过后才可能为真"）；`__init__.py` 文档也写着这一口径。
- **没有任何验收记录/金标**：`grep -rn "dwg_acceptance\|golden" tech_app/agent_knowledge/` 无 DWG 相关命中；
  `tests/fixtures/` 下没有 DWG 金标目录。
- **CI 没有区分真实冒烟**：`.gitlab-ci.yml` 的 `python_contract` 用
  `unittest discover -s tests -p 'test_*.py'` 一把跑；真实转换器冒烟只在
  `tech_app/tools/dwg_conversion_smoke.py` 里，**没有独立 job**。

**因此本批的默认判定是 No-Go**（§10），且这一结论本身要被红测锁住：不许在缺 L4 证据时出现
"支持 DWG"的字样。

## 1. 契约 A：2D/3D 分流（`tech_app/backend/services/dwg_dispatch.py`）

### 1.1 模块与闭集（冻结）

```python
DISPATCH_VERSION = "dwg-dispatch/1"

#: 图纸性质（document kind）——只能由 §1.3 的证据推出
DRAWING_KINDS = ("packaging_2d", "2d_only", "3d_present_unsupported", "3d_convertible",
                 "mixed", "unknown")

#: 用户可见的"三维状态"——六态，别的码一律不许出现（红测 `A12`）
DRAWING3D_STATUS = ("2d_parsed", "3d_absent", "3d_converter_unavailable",
                    "3d_conversion_failed", "3d_converted_and_parsed", "3d_unknown")

#: 判定"存在三维实体"的实体类型闭集（CAD IR / DXF 的 type 字段，大写比较）
THREE_D_ENTITY_TYPES = ("3DFACE", "3DSOLID", "BODY", "REGION", "SURFACE", "MESH",
                        "POLYFACE", "EXTRUDED", "EXTRUDEDSURFACE", "LOFTED", "REVOLVED")

#: 可作为"三维中间格式产物"的角色闭集（manifest.output_files[].role）
THREE_D_ARTIFACT_ROLES = ("step", "stp", "sat", "iges", "igs")

STATUS_MESSAGES = {  # 键集必须 == DRAWING3D_STATUS
    "2d_parsed": "二维图纸已解析，可继续提取结构与需求字段",
    "3d_absent": "图纸里没有三维实体",
    "3d_converter_unavailable":
        "图纸含三维实体，但当前转换器不支持导出三维；已按二维图纸解析",
    "3d_conversion_failed": "三维导出失败，已按二维图纸解析",
    "3d_converted_and_parsed": "三维实体已导出并解析",
    "3d_unknown": "无法判断是否含三维实体，已按二维图纸解析",
}
```

对外接口（冻结给第 6 批之后的维护；红测逐键断言）：

```python
def capability() -> dict
def classify(project_id, *, manifest=None, ir=None, semantics=None, deps=None) -> dict
def route(project_id, *, classification=None, deps=None) -> dict
def status_for(project_id, *, deps=None) -> dict
def dispatch_document(project_id, *, deps=None) -> dict
def limits() -> dict
def retention_plan(now, items, *, days=None, keep=()) -> dict
def recover(project_id, *, deps=None) -> dict
def summarize(doc) -> dict
def migrate(doc) -> dict
class DispatchError(Exception): ...      # stable_error_code / http_status / retryable / message
```

`persistence.py` 是唯一写盘入口（`save_dispatch` / `load_dispatch`），包级函数一律转调它；
新增一个文档位 `dwg_dispatch` 并进 `store.PARSE_STAGE_DOCS`。

### 1.2 `classify()` 返回

```json
{"dispatch_version": "dwg-dispatch/1", "project_id": "p1",
 "drawing_kind": "packaging_2d", "confidence": 0.9,
 "signals": {"has_2d_entities": true, "has_3d_entities": false,
             "three_d_declared": true, "three_d_artifact": null,
             "packaging_layers": ["CUT", "CREASE"], "z_only_ignored": false},
 "evidence": [{"source": "cad_ir", "key": "entity_types",
               "value": ["LINE", "LWPOLYLINE"], "ref": "ir:ir-0001"}],
 "reasons": ["no_3d_entity_types"], "warnings": []}
```

- 每个 `evidence[]` 必须带 `source` ∈ {`file_preflight`,`manifest`,`cad_ir`,`packaging_semantics`}、
  `key`、`ref`；`ref` 必须能在输入里回查（红测 `A9`）。
- 排序确定（evidence 按 `(source,key,ref)`、reasons 按字典序）→ 同输入同哈希。

### 1.3 证据规则（冻结；这是本批最容易被"猜"坏的地方）

| # | 规则 |
| --- | --- |
| 1 | **只吃三类证据**：manifest（含 `three_d` 段与 `output_files[].role`）、CAD IR（实体类型/统计/图层）、packaging_semantics（图层角色）。 |
| 2 | **Z 坐标不是证据**：实体带非零 Z 但类型全是二维 → 仍是 `2d_only`（红测 `A3`）。 |
| 3 | **文件名不是证据**：叫"三维盒.dwg"也不例外（红测 `A4`）。 |
| 4 | **预览图不是证据**：不许开图、不许像素测量、不许把预览发给模型（红测 `A8`）。 |
| 5 | **`drawing_kind` 判定顺序**（从上到下取第一个命中，不许跳步）：① IR 未出 / manifest 缺失 / 读取失败 → `unknown`；② 有三维实体（`has_3d`）且三维产物 sha256 校验通过 → 有包装二维证据则 `mixed`，否则 `3d_convertible`；③ `has_3d`、无可用产物、转换器 `capability().three_d_conversion == false`（或未安装）→ `3d_present_unsupported`；④ `has_3d`、无可用产物、转换器声明支持三维 → `3d_convertible`（导出失败由 §1.5.1 落到 `3d_conversion_failed`）；⑤ 无 `has_3d` 且有包装二维证据 → `packaging_2d`；⑥ 无 `has_3d` 且有二维实体 → `2d_only`；⑦ 其它 → `unknown`。 |
| 6 | `has_3d` = `ir.entities[].type`（大写）命中 `THREE_D_ENTITY_TYPES`；`stats.entity_types` 只在没有 `entities` 时使用（§1.6）。 |
| 7 | **包装二维证据** = 存在二维实体，且第 4 批语义（或 CAD IR 图层 `role`）里命中刀线/压痕线图层 → `packaging_2d`。"是包装展开图"是**加分项**，不是三维判据。 |
| 8 | 二维与三维证据同时成立 → `mixed`（即规则 5② 的"有包装二维证据"分支）；证据不足 → `unknown`。 |
| 9 | `unknown` **必须**按二维保守处理（§1.4），不许猜三维。 |

### 1.4 `route()` 与"绝不把 DWG 交给 3D 解析器"

```json
{"dispatch_version": "dwg-dispatch/1", "pipeline": "2d", "drawing_kind": "packaging_2d",
 "status": "2d_parsed", "blocked_by": "",
 "reason": "图纸只有二维实体", "evidence_refs": ["ir:ir-0001"], "enabled": true}
```

- `pipeline` 闭集 `("2d", "3d", "none")`：
  - `3d` **仅当**（a）`drawing_kind ∈ {"3d_convertible","mixed"}`，（b）三维产物 sha256 校验通过，
    （c）`step_import.AVAILABLE` 为真。三条缺一条 → `2d` + `blocked_by` 说明缺哪条。
  - `none` 只在分流被显式关闭时（§9），`blocked_by = "DWG_DISPATCH_DISABLED"`。
- **结构性保证（红测 `A11` 查签名）**：`classify()` / `route()` 的参数里**没有**原始字节
  （无 `content`、无 `data`、无 `bytes`），因此"DWG 原始字节交给 `step_import`"在类型上不可能发生；
  三维路径只接受**转换器产出的中间格式产物**。
- 分流层**不得**顶层 `import step_import`（惰性经 `_dependency("step_import")` 读 `AVAILABLE`）。
- 分流层不得 import `ezdxf` / `vision` / `step_import`（顶层）/ `requests` / `subprocess`；
  顶层 import 白名单与第 5 批一致（`__future__ collections copy datetime hashlib importlib json
  os pathlib re time typing unicodedata uuid` + 相对 import + `tech_app.*`）。

### 1.5 `status_for()`（页面/API 用的六态）

```json
{"code": "3d_converter_unavailable", "message": "图纸含三维实体，但当前转换器不支持导出三维；已按二维图纸解析",
 "pipeline": "2d", "drawing_kind": "3d_present_unsupported", "blocked_by": "",
 "three_d_artifact": null, "evidence_refs": ["ir:ir-0001"], "fresh": true}
```

- `code` ∈ `DRAWING3D_STATUS`，`message` 取自 `STATUS_MESSAGES`，**业务用户能读懂**：
  不许出现堆栈、绝对路径、`Traceback`、第三方工具内部细节、英文异常名。
- 老项目（没跑过分流）→ `3d_unknown`，**不抛异常**（红测 `B17`）。
- `fresh`：本次判定是否基于当前 `input_revision`；旧版本图上的结论 → `false`（不删旧结论）。

### 1.5.1 六态推导表（冻结：六态与 `drawing_kind` 一一对应，不许自由发挥）

| `drawing_kind` | `pipeline` | `code` |
| --- | --- | --- |
| `packaging_2d` | `2d` | `2d_parsed` |
| `2d_only` | `2d` | `3d_absent` |
| `3d_present_unsupported` | `2d` | `3d_converter_unavailable` |
| `3d_convertible` | `3d` | `3d_converted_and_parsed` |
| `3d_convertible` | `2d`（`step_import` 不可用，或产物 sha256 校验失败） | `3d_conversion_failed` |
| `mixed` | `3d` | `3d_converted_and_parsed` |
| `mixed` | `2d`（`step_import` 不可用） | `3d_converter_unavailable` |
| `mixed` | `2d`（产物校验失败） | `3d_conversion_failed` |
| `unknown` | `2d` | `3d_unknown` |
| 分流被关闭（§9） | `none` | `3d_unknown` |

- `route()` 与 `status_for()` 必须对同一输入给同一 `code`／`pipeline`：不许一条路径说
  "三维已导出"、另一条说"三维不存在"。
- `3d_converted_and_parsed` **只允许**在 `pipeline == "3d"` 出现；`pipeline != "3d"` 时
  出现该码即视为"在转换失败后静默降级并声称 3D 成功"（§一 禁止事项），红测 `B18` 锁死。

### 1.6 输入读取面、依赖缝与 `ref` 形状（冻结）

`classify(project_id, *, manifest=None, ir=None, semantics=None, deps=None)` 的三个输入只按
下列**具体键**读取；键缺失一律按"没有这份证据"处理（退到 `unknown`），不许抛异常：

| 输入 | 读取键 |
| --- | --- |
| `manifest`（第 2 批 manifest） | `status`、`converter_name`、`converter_version`、`converter_role`、`fallback_used`、`primary_failure_code`、`conversion_id`、`output_files[].role`、`output_files[].sha256`、`output_files[].path`、可选 `three_d.status` |
| `ir`（第 3 批 CAD IR） | `ir_id`、`entities[].type`（**大写比较**）、`entities[].layer`、`entities[].z`（可选，非零即"带 Z 坐标"，**只用于 `z_only_ignored`，不构成三维证据**）、可选 `stats.entity_types`（dict 或 list）、`layers[].name`、`layers[].role` |
| `semantics`（第 4 批语义） | `semantics_version`、`layers[].name`、`layers[].role` |

- 实体类型口径：`entities` 与 `stats.entity_types` 都在时**以 `entities` 为准**；
  `stats.entity_types` 只在没有 `entities` 时使用（防止两处统计不一致时被"宽松的一边"决定）。
- 三维产物"sha256 可校验"= `output_files[]` 里 `role ∈ THREE_D_ARTIFACT_ROLES` 的项，
  `path` 指向的文件存在且实际 sha256 == `sha256` 字段。校验失败 → 该产物**不算证据**，
  回落 `3d_present_unsupported`（有三维实体时）。
- `deps` 键闭集：`cad_converter`（`capability()` / `latest_manifest()`）、`cad_ir`（`load_ir()`）、
  `packaging_semantics`（`latest()`）、`step_import`（`AVAILABLE`）；`deps=None` 时惰性 import 真模块。
- `evidence[].ref` 形状冻结：`ir:<ir_id>` / `manifest:<conversion_id>` /
  `semantics:<semantics_version>`；`ref` 必须能在对应输入对象里回查到同一个值（红测 `A9`）。

## 2. 契约 B：能力输出（health / capability）

### 2.1 `cad_converter.capability()` 追加（只加键，不改既有键）

```json
{"three_d": {"declared": true, "supported": false, "available": false,
             "status": "unsupported", "reason": "adapter declares three_d_conversion=false"},
 "acceptance": {"present": false, "valid": false, "reason": "missing_record",
                "approved_by": "", "approved_at": "", "golden_version": ""},
 "support_claim": "conversion_available",
 "dwg_supported": false}
```

- `three_d.status` 闭集 `("supported", "unsupported", "unavailable", "unknown")`（红测 `B14`）。
- `acceptance.reason` 闭集：`missing_record` / `unreadable` / `record_version_unsupported` /
  `converter_mismatch` / `binary_mismatch` / `samples_missing` / `sample_hash_mismatch` /
  `unapproved` / `e2e_report_missing` / `e2e_report_hash_mismatch`（空串表示有效）。
- `status_for()` 的六态与 `three_d.status` **不是**同一张表：前者是"这份图怎么样"，
  后者是"这台机器能不能做三维"；实现里不许互相推导（红测 `B18`）。

### 2.2 `GET /api/health` 的 `cad_converter` 段

必须含（红测 `B13`）：`available` / `provider` / `converter_version` / `three_d` / `acceptance` /
`support_claim` / `dwg_supported` / `env`。`/api/health` 是免登录接口，
所以这里只报**能力**，不许出现密钥、部署绝对路径、第三方原始 stderr。

### 2.3 `GET /api/projects/{project_id}/drawing-routing`（新增，只读）

- 权限：登录 + `project_access.can_read`（销售/财务只读可见性由项目层负责，不在此路由里判角色）。
- 返回：`{"project_id", "dispatch_version", "status": {…§1.5…}, "classification": {…§1.2 摘要…},
  "limits": {…§8…}}`。
- **不写盘、不改数据**（红测 `B16` 用"调用前后文档与 mtime 不变"证）。
- 本批只加这一条只读路由；写入口沿用既有上传/解析（第 5 批负责），本批不新增写路由。

## 3. 契约 C：把"支持 DWG"变成机器可验证的记录

### 3.1 验收记录（acceptance record）

- 位置：环境变量 `DWG_ACCEPTANCE_RECORD`（绝对路径）；默认
  `<仓库根>/tech_app/agent_knowledge/dwg_acceptance.json`。
- 版本串：`ACCEPTANCE_RECORD_VERSION = "dwg-acceptance/1"`。
- 归属模块：`tech_app/backend/services/dwg_acceptance.py`（**纯读取 + 纯推导**：不 import
  `cad_converter` / `dwg_dispatch`，避免环；`cad_converter.capability()` 与 `dwg_dispatch`
  都从它取口径）。
- 对外接口（冻结；红测逐项调用）：

```python
ACCEPTANCE_RECORD_VERSION = "dwg-acceptance/1"
RECORD_PATH_ENV = "DWG_ACCEPTANCE_RECORD"
SAMPLES_DIR_ENV = "DWG_ACCEPTANCE_SAMPLES_DIR"
def record_path() -> Path
def samples_dir() -> Path
def load_record(*, path=None) -> dict          # 每次现读；缺失 → {}
def validate(record, *, live=None, samples_dir=None, record_path=None) -> dict
def support_claim(*, adapter=None, record_state=None) -> dict
def acceptance(*, adapter=None, live=None) -> dict   # 只读查询：validate(load_record())
def summarize(record_state) -> dict
```

- `live`（现场事实；`None` 时从真环境推导）：`{"converter_name": "", "converter_version": "",
  "binary_sha256": "", "samples": {"酒盒.dwg": "<sha256>", "圆盘盒.dwg": "<sha256>"}}`。
- `samples_dir()` 默认 `<仓库根>/裕同包装项目-待开发`（两份样本只读、不入库）；
  `validate()` 按 `samples[]` 的 `filename` 在该目录下找样本。
- `support_claim()` 是**纯函数**：`adapter` = `{"available": bool, "simulated": bool}`，
  `record_state` = `validate()` 的输出；返回
  `{"support_claim": …, "dwg_supported": bool, "reason": …}`，不读盘、不联网。
- 形状（冻结；红测逐键）：

```json
{"record_version": "dwg-acceptance/1",
 "converter": {"name": "oda", "version": "27.1", "binary_sha256": "…"},
 "samples": [{"filename": "酒盒.dwg", "sha256": "…", "dxf_sha256": "…",
              "preview_sha256": "…", "layer_count": 0, "entity_count": 0, "non_blank": true},
             {"filename": "圆盘盒.dwg", "sha256": "…", "dxf_sha256": "…",
              "preview_sha256": "…", "layer_count": 0, "entity_count": 0, "non_blank": true}],
 "e2e": {"report_path": "…", "report_sha256": "…", "finished_at": "…"},
 "golden_version": "2026-09-20.1",
 "approved_by": "…", "approved_at": "…"}
```

- `converter` 段记的是**主**转换器身份（`DWG_CONVERTER_PROVIDER` 的生效 provider + 显式声明的
  版本 + 主二进制 sha256）：**回退转换器不许冒充主转换器**。E2E 期间若发生过回退，
  `e2e.report` 必须写明 `fallback_used=true` 与 `primary_failure_code`，门禁第 1/2 项仍按主转换器判定；
  回退产物**不得**作为「主转换器能力已验收」的证据。

### 3.2 声明推导（冻结）

| 现场状态 | `support_claim` | `dwg_supported` |
| --- | --- | --- |
| 没有可用适配器 | `orchestration_only` | `false` |
| 适配器可用（真实），记录**缺失/无效** | `conversion_available` | `false` |
| 适配器是 fake | `orchestration_only` | `false` |
| 适配器可用 **且** 记录有效 | `supported` | `true` |

- `support_claim` 闭集扩展为 `("orchestration_only", "conversion_available", "supported")`
  （第 2 批的 `"real"` 继续禁止出现在任何位置）。
- **记录有效** = 全部满足：文件存在且可解析、`record_version` 受支持、
  `converter.name/version` 与现场**主**转换器一致、`binary_sha256` 与现场主二进制一致、
  两份样本的 `sha256` 与现场样本一致、`approved_by` 非空、`approved_at` 可解析、
  `e2e.report_sha256` 与该报告文件一致。
- **禁止**由"装了转换器"推导 `supported`（红测 `C20`）。
- 记录读取**每次调用现读**（不在 import 时固化），便于撤离与热更新（红测 `C25`）。
- `validate()` 的 reason 触发表（**按上到下取第一个命中**，红测 `C21`–`C24`）：

| 触发条件 | `reason` |
| --- | --- |
| 记录文件不存在 | `missing_record` |
| 文件存在但 JSON 解析失败 | `unreadable` |
| `record_version` 不是 `dwg-acceptance/1` | `record_version_unsupported` |
| `converter.name` / `converter.version` 与现场不一致 | `converter_mismatch` |
| `converter.binary_sha256` 与现场二进制不一致 | `binary_mismatch` |
| `samples[]` 少于两份，或样本文件在 `samples_dir()` 下不存在 | `samples_missing` |
| 样本 `sha256` 与现场样本不一致 | `sample_hash_mismatch` |
| `approved_by` 为空，或 `approved_at` 不可解析 | `unapproved` |
| `e2e.report_path` 缺失或报告文件不存在 | `e2e_report_missing` |
| `e2e.report_sha256` 与报告文件不一致 | `e2e_report_hash_mismatch` |
| 全部通过 | `""`（`valid = true`） |
- 记录不是程序生成的默认产物：`tech_app/tools/dwg_acceptance_report.py --write-record` 需要
  `--approved-by`，缺则拒绝写入并非零退出（红测 `D28`）。

## 4. 契约 D：金标验收（版本化 + 人工审批）

### 4.1 位置与形状

```
tests/fixtures/dwg_acceptance/<golden_version>/manifest.json
tests/fixtures/dwg_acceptance/<golden_version>/酒盒.json
tests/fixtures/dwg_acceptance/<golden_version>/圆盘盒.json
```

每份样本金标（冻结字段）：

```json
{"golden_version": "2026-09-20.1", "sample_filename": "酒盒.dwg",
 "source_sha256": "…", "detected_dwg_version": "AC1027",
 "converter": {"name": "oda", "version": "27.1", "role": "primary", "fallback_used": false},
 "artifacts": {"dxf_sha256": "…", "preview_sha256": "…", "preview_non_blank": true},
 "stats": {"layer_total": 0, "entity_total": 0, "dimension_total": 0, "text_total": 0,
           "block_total": 0},
 "unit_status": "", "cut_layers": [], "crease_layers": [], "box_candidates": [],
 "key_dimensions": [], "required_unresolved": [], "forbidden_fields": [],
 "three_d_status": "", "reviewed_by": "", "reviewed_at": "",
 "approval": {"approved_by": "", "approved_at": "", "note": ""}}
```

- 统计类字段（`stats`/`layer_total`/实体数）**允许**由脚本采集后人工确认；
  业务结论（`cut_layers` / `key_dimensions` / `box_candidates` / `required_unresolved`）
  必须人工填写——**测试作者不许凭感觉编造酒盒/圆盘盒的尺寸**（第 4 批铁律延续）。
- `forbidden_fields` **必须非空**：明确写出"这份图上不该出现的幻觉字段"（如无依据的材料、克重）。
- `converter` 段必须写明产出方身份与跳次：`{"name": "oda", "version": "27.1", "role": "primary",
  "fallback_used": false}`。金标**只认主转换器产物**（`role == "primary"` 且 `fallback_used is false`）：
  只有回退产物可用的环境**不能**生成"支持 DWG"的基线，必须如实降级为
  `conversion_available`，门禁判定为 No-Go（红测 `D26`）。

### 4.2 审批与"不许自动刷绿"

- 金标只有在 `approval.approved_by` 非空且 `approval.approved_at` 可解析时才算**已审批**；
  缺任一项 → 视为未审批，L4 与 `acceptance record` 都必须拒绝据此宣称支持（红测 `D27`）。
- 写入金标**只能**由 `tech_app/tools/dwg_acceptance_report.py --write-baseline --approved-by <人>`
  完成；不带 `--approved-by` → 非零退出且**不写任何文件**（红测 `D28`）。

工具 CLI 冻结（红测 `D27`–`D29`/`G48` 逐项调用）：

```
python tech_app/tools/dwg_acceptance_report.py [--verify] [--baseline-dir DIR] [--record PATH]
                                              [--write-baseline | --write-record]
                                              --approved-by <人> [--json] [--report PATH]
```

- 默认动作是 `--verify`（只读比较）：全部通过退出 0；有差异 / 有未审批金标 / 记录无效 →
  非零退出，并输出版本化 diff 摘要。
- `--write-baseline` / `--write-record` 必须同时给 `--approved-by`；缺则退出码 2 且不写任何文件。
- 工具**不得**提供 `--update-snapshot` / `--force` 之类"自动刷绿"开关（红测 `D29`）。
- 工具默认**只读比较**：跑一次不改动金标文件（红测 `D29`）；发现差异时输出版本化 diff 摘要
  并非零退出，绝不自动更新 snapshot。
- `tests/` 下的任何测试代码**不得**写金标目录（红测 `D30` 用 AST + 源码扫描：
  不许出现 `dwg_acceptance` 路径的写操作 / `write_text` / `json.dump` 目标）。

## 5. 契约 E：四层测试与 CI 区分

| 层 | 范围 | 位置 | 默认是否跑 |
| --- | --- | --- | --- |
| **L1 单元** | 分流规则、声明推导、`limits`/`retention_plan`、门禁脚本逻辑 | `tests/test_dwg_final_acceptance_red.py`（A–G 组） | 是（CI `python_contract`） |
| **L2 适配器契约** | fake 与真实适配器满足同一契约（含 `inspect()` / `convert_3d_if_supported()` 的三维证据形状） | 同文件 `H` 组 + 第 2 批既有契约测试 | 是 |
| **L3 服务集成** | 上传→分流→文档落库、`/api/health`、`/drawing-routing`、重启恢复 | 同文件 `I` 组 | 是 |
| **L4 真实样本 E2E** | `酒盒.dwg` / `圆盘盒.dwg` 全链路 | `tests/test_dwg_real_samples_e2e_red.py` | **否**：需 `CPQ_DWG_REAL_SAMPLES=1` 且样本+转换器都在 |

- **L4 没跑过就不算验收**：L1/L2/L3 全绿只允许写"编排层通过"，
  **不许**写"真实转换能力已验收"（红测 `G48` 锁死措辞）。
- L4 缺失前置条件时必须 `skipTest` 且**在跳过原因里点名缺什么**
  （`没有真实转换器` / `样本不在本机` / `未设置 CPQ_DWG_REAL_SAMPLES=1`），
  不许静默跳过、不许把 skip 当 pass。
- CI（`.gitlab-ci.yml`）必须新增独立 job `dwg_real_samples`：
  - `when: manual`、`allow_failure: false`、`stage: test`；
  - 只调 `tech_app/tools/dwg_sample_e2e.py`（真实转换 + 金标比对），**不跑** L1–L3；
  - `python_contract` 里**不得**出现 `CPQ_DWG_REAL_SAMPLES`、也不得调用该脚本（红测 `E33`）。

`tech_app/tools/dwg_sample_e2e.py` CLI 冻结（L4 红测按此调用）：

```
python tech_app/tools/dwg_sample_e2e.py --sample <样本.dwg> --out <目录> [--json]
```

- 只读样本、只写 `--out` 目录；`--json` 输出
  `{"status", "returncode", "dxf_path", "preview_paths", "output_files": [{"path", "role",
  "sha256"}], "detected_dwg_version", "three_d_status", "stats": {"layer_total", "entity_total",
  "dimension_total", "text_total", "block_total"}}`。
- 不读 `.env`、不联网、不上传样本；不写金标（金标只由 `dwg_acceptance_report.py` 写）。
- `python_contract` 继续用 `unittest discover`：L1–L3 与 L4 文件都会被收集，L4 在缺前置条件时
  skip（允许），但 `dwg_real_samples` job 必须以 `--strict` 语义失败于"零执行"。

## 6. 契约 F：部署门禁脚本（`tech_app/tools/dwg_deploy_gate.py`）

- `GATE_VERSION = "dwg-deploy-gate/1"`。
- `GATE_ITEMS`：**§7 的 18 项**，逐项 `{"id", "kind", "title"}`，
  `kind ∈ ("auto", "manual")`（闭集，红测 `E34` 逐项比对 id/kind）。
- 用法与输出：

```
python tech_app/tools/dwg_deploy_gate.py --env local|ci|production [--json] [--report PATH]
                                         [--ack <item_id>=<用户>] [--dry-run]
```

```json
{"gate_version": "dwg-deploy-gate/1", "env": "ci", "checked_at": "…",
 "items": [{"id": "converter_version_pinned", "kind": "auto", "status": "ok",
            "evidence": {"expected": "0.14", "actual": "0.14"}, "message": "…"}],
 "summary": {"ok": 0, "fail": 0, "manual": 0, "acknowledged": 0, "skip": 0},
 "verdict": "no_go", "reasons": ["real_samples_e2e_passed"]}
```

- `status` 闭集 `("ok", "fail", "manual_unacknowledged", "acknowledged", "skip")`。
- **退出码**：`verdict == "go"` → 0；否则非零。
- `verdict == "go"` 的条件（全部满足）：
  1. 没有任何 `kind == "auto"` 且 `status == "fail"`；
  2. 每个 `kind == "manual"` 项要么 `acknowledged`（`--ack id=user`），要么 → No-Go；
  3. `kind == "auto"` 的 `skip` 只允许在 `env == "ci"` 且原因明确（例如"CI 无转换器"）；
  4. `--env production` 下 `skip` 一律算 fail。
- **禁止**：读 `.env` / 任何密钥；联网；把 `manual` 项报成 `ok`；把 skip 计进 ok；
  在输出里打印密钥、token、绝对部署路径以外的敏感值（红测 `E38`/`E39`）。
- 未知的 `--ack <item_id>=<用户>`（id 不在 18 项里）→ 用法错误，**退出码 2**，不许静默忽略。
- `--report PATH` 写 Markdown 报告（模板见 §10.1），报告本身也要过"禁用词"检查。

## 7. 契约 G：部署门禁清单（18 项，逐项判定方式）

| # | id | kind | 判定方式（可执行） | 不通过后果 |
| --- | --- | --- | --- | --- |
| 1 | `converter_license` | manual | 人工确认：许可证与部署方式合法（**ODA 27.1 非会员限非商业用途，已由业务/法务确认可用于本 CPQ 生产环境**；LibreDWG GPLv3+ 作为回退） | No-Go |
| 2 | `converter_version_pinned` | auto | `DWG_CONVERTER_VERSION` 非空且 `version_ok == true`；LibreDWG 系驱动还必须与 `local_cli.probe_version(binary)` 一致（`version_source="probed"`）；ODA 无法自证版本（`version_source="config_declared"`），只校验显式声明非空。无转换器时在 `env == "ci"` 记 `skip`（原因必须写明"CI 无转换器"），在 `env == "production"` 记 `fail` | No-Go |
| 3 | `health_reports_capability` | auto | `cad_converter.capability()` 含 §2.1 全部键且 `status_for` 六态可用 | No-Go |
| 4 | `tmp_dir_permissions` | auto | 转换临时目录存在、可写、不在仓库内、非世界可写 | No-Go |
| 5 | `disk_quota_and_cleanup` | auto | `limits()["retention_days"] > 0` 且清理入口存在（`retention_plan()` 可用） | No-Go |
| 6 | `conversion_timeout` | auto | `limits()["converter_timeout_seconds"] > 0` | No-Go |
| 7 | `concurrency_limit` | auto | `limits()["max_concurrency"] >= 1` 且 `limits()["per_project_concurrency"] >= 1` | No-Go |
| 8 | `malicious_cad_isolation` | auto | 第 2 批隔离项全部在位：独立临时目录、无网络、不执行宏/脚本、路径穿越防护（源码+配置核对） | No-Go |
| 9 | `model_failure_isolation` | auto | 模型不可用时（`claude_client.run` 抛错）分流与确定性链仍返回结论 | No-Go |
| 10 | `db_migration_rollback` | auto | 文档位迁移有 `migrate()` 三分支且**不删旧文档**；无 destructive 迁移 | No-Go |
| 11 | `legacy_projects_open` | auto | 没有分流文档的老项目 `status_for()` 返回 `3d_unknown`，不抛异常 | No-Go |
| 12 | `non_packaging_no_regression` | auto | 非包装行业相关既有测试集全绿（在 CI 里由 `python_contract` 覆盖） | No-Go |
| 13 | `step_flow_no_regression` | auto | STEP/3D 既有流程测试全绿；`/api/projects/3d` 门禁不被绕过 | No-Go |
| 14 | `no_secrets_in_logs_or_fixtures` | auto | 扫描日志/夹具/输出：不含 `API_KEY`、`token`、私钥、生产库口令 | No-Go |
| 15 | `no_dev_machine_dependency` | auto | 无转换器环境下 L1–L3 仍可跑完（不 import 失败、不联网） | No-Go |
| 16 | `ci_separates_adapter_and_real_smoke` | auto | `.gitlab-ci.yml` 有 `dwg_real_samples` job（`when: manual`）且 `python_contract` 不含真实冒烟 | No-Go |
| 17 | `real_samples_e2e_passed` | manual | 两份真实样本 L4 通过 + 金标已人工审批 + 验收记录有效 | No-Go |
| 18 | `converter_chain_configured` | auto | 受控回退链在位：`capability().fallback` 段存在且 `provider`/`version` 显式配置、`manifest` 契约含 `converter_role`/`fallback_used`/`primary_failure_code`/`attempts`、且用 fake 适配器验证「主成功不回退、主失败才回退」（`docs/specs/dwg-conversion-quality-repair.md` §7） | No-Go |

## 8. 契约 H：性能与稳定性上限

| 配置 | 默认 | 谁负责 | 说明 |
| --- | --- | --- | --- |
| `CAD_CONVERTER_TIMEOUT_SECONDS` | 120 | 第 2 批 | 单次转换超时（编排层自己实现） |
| `CAD_CONVERTER_MAX_OUTPUT_BYTES` | 256 MiB | 第 2 批 | 单次产物总量上限 |
| `CAD_CONVERTER_MAX_OUTPUT_FILES` | 20 | 第 2 批 | 产物文件数上限 |
| `CAD_CONVERTER_MAX_CONCURRENCY` | 2 | **本批** | 全局转换并发上限 |
| `DWG_DISPATCH_MAX_CONCURRENCY_PER_PROJECT` | 1 | **本批** | 单项目并发上限 |
| `CAD_IR_PARSE_TIMEOUT_SECONDS` | 120 | 第 3 批 | 解析超时 |
| `CAD_IR_MAX_ENTITIES` | 500000 | 第 3 批 | 实体上限 |
| `CAD_ARTIFACT_RETENTION_DAYS` | 30 | **本批** | 产物保留期 |
| `CAD_ARTIFACT_CLEANUP_ENABLED` | `false` | **本批** | 默认**不**自动清理历史产物 |
| `DWG_DISPATCH_ENABLED` | `false` | **本批** | 分流开关（§9） |

- `limits()` 返回**当前生效值**（不是默认值），键闭集固定，并且 JSON 安全（红测 `F40`）。
  键闭集就是上表这十个配置名，逐字同名、不增不减：
  `CAD_CONVERTER_TIMEOUT_SECONDS` / `CAD_CONVERTER_MAX_OUTPUT_BYTES` /
  `CAD_CONVERTER_MAX_OUTPUT_FILES` / `CAD_CONVERTER_MAX_CONCURRENCY` /
  `DWG_DISPATCH_MAX_CONCURRENCY_PER_PROJECT` / `CAD_IR_PARSE_TIMEOUT_SECONDS` /
  `CAD_IR_MAX_ENTITIES` / `CAD_ARTIFACT_RETENTION_DAYS` / `CAD_ARTIFACT_CLEANUP_ENABLED` /
  `DWG_DISPATCH_ENABLED`；数值键返回数字、开关类键返回布尔。
- `retention_plan(now, items, *, days=None, keep=())` 是**纯函数**：返回
  `{"policy_version": "artifact-retention/1", "days": 30, "keep": [...], "expire": [...]}`；
  **不删文件、不读磁盘、不写审计**。
  - `CAD_ARTIFACT_CLEANUP_ENABLED != "true"` 时 `expire` 必须为空（默认不动任何历史数据）；
  - `keep` 里的 id 永不进 `expire`；
  - 只有 `created_at` 早于 `now - days` 且不在 `keep` 里的才进 `expire`。
  - `now` 与 `items[].created_at` 都是 **ISO-8601 字符串**；`expire` / `keep` 按输入顺序保留
    `{"id", "created_at"}` 原样字段，不做格式化。
- 任务取消与服务重启：`recover(project_id)` 把处于 `running` 的分流判定标成
  `interrupted`（沿用既有 `tasks.py:475`「服务重启中断」口径），**不删除**任何既有文档与产物；
  再次调用 `classify()` 会正常重算（红测 `F43`）。
- 转换器升级重建：`cache_key` 含版本（第 2 批已实现），旧产物保留、新转换新建 `conversion_id`；
  本批额外要求 `status_for()["fresh"] == false` 时不把旧结论当当前结论。
- 分流文档必须带 `content_hash`（同一输入恒同值）；`dispatch_document()` 重复调用**幂等**：
  不产生新的文档位、不新增文件、`content_hash` 不变（红测 `I53`）。

## 9. 契约 I：回滚策略

| 层级 | 动作 | 效果 | 是否删数据 |
| --- | --- | --- | --- |
| 功能开关 | `DWG_DISPATCH_ENABLED=false`（默认） | `route()` → `pipeline="none"`、`blocked_by="DWG_DISPATCH_DISABLED"`；不写文档、不改既有行为 | 否 |
| 声明回退 | 删除/移走 `DWG_ACCEPTANCE_RECORD` | `support_claim` 回到 `conversion_available`、`dwg_supported=false`（无需改代码、无需重启） | 否 |
| 数据层 | 只新增文档位（`dwg_dispatch`），不改旧键、不迁移旧记录 | 老项目照常打开 | 否 |
| 产物层 | 清理默认关闭；回滚不清理产物与 manifest | 历史转换结果仍可回看 | 否 |
| 代码层 | 新模块独立 + 一条只读路由 | 关开关即回到第 1–5 批行为 | 否 |
| 迁移 | `migrate()` 对未知版本抛 `ValueError`、缺版本 `needs_rebuild` | 不猜、不硬读 | 否 |

- **回滚不等于删除**：任何回滚动作都不得删除历史会话、项目、附件、产物、manifest、
  `dwg_acceptance` 记录与 `dwg_dispatch` 文档（AGENTS.md 历史数据铁律）。
- 回滚后必须能回答：`capability()` 的 `support_claim`、`route()` 的 `pipeline`、
  `status_for()` 的 `code` —— 三者都有明确的"关闭态"取值（红测 `G45`/`G46`）。
- 关闭态的取值冻结：`route()` → `pipeline="none"` + `blocked_by="DWG_DISPATCH_DISABLED"`；
  `status_for()` → `code="3d_unknown"` + `pipeline="none"` + `fresh=False`；
  `dispatch_document()` → 与 `route()` 同形状（`pipeline="none"`）且**不写文档**。
  既有 `dwg_dispatch` 文档原样保留（回滚不等于删除）。

## 10. 最终验收报告与 Go/No-Go

### 10.1 报告模板（Markdown，`--report` 产出）

```text
# DWG 支持最终验收报告
- 版本/分支/commit：<git>
- 环境：<local|ci|production>　转换器：<name> <version>　二进制 sha256：<…>
- 样本：酒盒.dwg <sha256>　圆盘盒.dwg <sha256>
- L1 单元：<通过数>/<总数>　L2 适配器契约：<…>　L3 服务集成：<…>　L4 真实样本 E2E：<…|未跑>
- 转换结果：状态/质量/warning 数/error 数（逐样本）
- CAD IR：图层数/实体数/尺寸数/文字数（逐样本）
- 包装语义：刀线/压痕线确认、盒型候选、必须待确认项
- 3D 状态：<六态之一>（逐样本，含判据）
- 金标：版本/审批人/审批时间
- 部署门禁：18 项逐项状态（ok/fail/manual_unacknowledged/skip）
- 能力声明：<一句话，必须与 support_claim 一致>
- 判定：GO / NO-GO　理由：<列点>
- 未决风险与后续动作：<列点>
- 签批：approved_by / approved_at
```

### 10.2 Go/No-Go 判定（全部满足才 GO）

1. L1–L3 全绿（含本批红测与既有回归）；
2. L4 两份真实样本通过，且证据文件（报告 + 金标）在仓外留存、哈希写进验收记录；
3. 金标已人工审批（`approval.approved_by` 非空）；
4. 验收记录通过 §3.2 全部校验；
5. 部署门禁 18 项无 `fail`、无 `manual_unacknowledged`；
6. `capability().support_claim == "supported"` 且与报告"能力声明"逐字一致；
7. 无未决 critical 风险（含性能/内存/磁盘）；
8. 回滚开关可用且已验证（关掉后 `pipeline == "none"`）。

### 10.3 当前判定：**No-Go**（实测，2026-09-21）

| 失败条件 | 实测证据 |
| --- | --- |
| L1–L3 未全绿 | 第 1/2 批修复（ODA 修订）16 红、第 3 批 `cad_ir` 1 红（回退留痕）、第 5 批 `packaging_drawing_flow` 54 红、第 6 批本文件 52 红 |
| 分流模块不存在 | 全仓无 `dwg_dispatch*` |
| L4 未跑过 | 无 `CPQ_DWG_REAL_SAMPLES` 执行记录、无 E2E 报告 |
| 金标未建立/未审批 | `tests/fixtures/dwg_acceptance/` 不存在 |
| 验收记录缺失 | `tech_app/agent_knowledge/dwg_acceptance.json` 不存在 |
| 门禁脚本不存在 | `tech_app/tools/dwg_deploy_gate.py` 不存在 |

→ 当前只允许写：**"DWG 编排能力完成，真实转换能力未验收"**。
**不许**写"支持 DWG"、"DWG 已支持"、"已完成 DWG 支持"（红测 `G48`）。

## 11. 红测文件与分组

| 文件 | 分组 | 条数 | 说明 |
| --- | --- | --- | --- |
| `tests/test_dwg_final_acceptance_red.py` | `A` 分流状态机（12）/ `B` 能力输出（6）/ `C` 验收声明（7，落在 `dwg_acceptance.py`）/ `D` 金标审批（5）/ `E` 门禁脚本（9）/ `F` 上限与回滚数据（5）/ `G` 回滚与诚实（4）/ `H` 适配器契约（2）/ `I` 服务集成（3） | 53 | 默认跑（L1–L3） |
| `tests/test_dwg_real_samples_e2e_red.py` | L4 真实样本 E2E | 9 | 需 `CPQ_DWG_REAL_SAMPLES=1` + 样本 + 转换器 |

- 红测的 fake 一律内存、确定性、无网络；L4 只读样本、只写临时目录。
- 红测**不许**写金标、不许写真实 `tech_data`、不许改服务器配置。

## 12. 非目标（本批一律不做）

- 不重写第 1–5 批的任何契约；不改 `packaging_*` 业务规则与成本口径。
- 不实现真实 3D 几何解析（那是既有 `geometry.py` / `step_import` 的职责）；
  本批只做**分流**与"只在证据充分时才允许进 3D"。
- 不自动更新金标、不自动生成 `approved_by`、不自动创建验收记录。
- 不做部署、不改服务器、不重启服务、不修改 `.gitlab-ci.yml` 之外的 CI 设置。
- 不把 DWG 原始字节发给任何模型或 3D 解析器。
- 不删除任何历史数据（含回滚路径）。

## 13. 自动化验收

| 命令 | 期望 |
| --- | --- |
| `./open-claude/.venv/bin/python tests/test_dwg_final_acceptance_red.py` | 实现前 FAIL；实现后 OK |
| `./open-claude/.venv/bin/python tests/test_dwg_real_samples_e2e_red.py` | 默认 SKIP（点名缺什么）；`CPQ_DWG_REAL_SAMPLES=1` 且前置齐全时真实跑 |
| `./open-claude/.venv/bin/python tech_app/tools/dwg_deploy_gate.py --env ci --json` | 输出 §6 形状；当前 `verdict == "no_go"`、退出码非零 |
| `./open-claude/.venv/bin/python tech_app/tools/dwg_acceptance_report.py --help` | 可跑；`--write-baseline` 缺 `--approved-by` 时非零退出 |
| `./open-claude/.venv/bin/python tech_app/tools/dwg_conversion_smoke.py` | 不回归（A/B 层口径不变） |
| `./open-claude/.venv/bin/python /tmp/run_pkg.py 1` | 除既有失败集合与本批新红外不新增失败 |

## 14. 第 6 批之后的维护

- **金标更新流程**：转换器升级 / 客户图纸改版 → 重跑 L4 → 生成新 `<golden_version>` 目录 →
  人工逐项确认（尤其业务字段）→ 写 `approval` → 更新验收记录 → 重新出报告。
  任何一步缺失 → `support_claim` 必须退回 `conversion_available`。
- **样本改动**：`酒盒.dwg` / `圆盘盒.dwg` 的 sha256 变化即视为**新样本**，旧金标与旧验收记录立即失效。
- **门禁脚本**：18 项 id 闭集只增不改（新增项一律追加在末尾，避免重排既有 id）；新增项必须同时
  更新本节与红测 `E34`。
- **每次升级 3D/转换器**：必须重跑 L2 契约测试 + L4；`three_d.status` 变化必须同步到报告。

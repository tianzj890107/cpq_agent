# 规格：DWG 前两批修复 —— LibreDWG 0.14 接入后的配置、质量门槛与转换状态

适用范围：**只修第 1 批（文件能力契约）与第 2 批（受控转换服务）**。第 3 批（CAD IR）只在
§7 约定联动字段；第 4–6 批不动。本规格不授权部署、重启或修改服务器。

关联文档：`docs/specs/dwg-file-capability-preflight.md`（第 1 批）、
`docs/specs/dwg-controlled-conversion-adapter.md`（第 2 批）、`docs/specs/dxf-cad-ir.md`（第 3 批）。

## 1. 现状取证（实测，9-20）

### 1.1 转换器已就位

| 环境 | 路径 | 版本 |
| --- | --- | --- |
| 本机 macOS ARM64 | `/opt/homebrew/bin/dwg2dxf`（另有 `dwgread`/`dwg2SVG`/`dwglayers` 等 10 个工具） | LibreDWG 0.14 |
| 34 服务器 Ubuntu x86_64 | `/home/data/cpq-tools/libredwg-0.14/bin/dwg2dxf`，稳定别名 `/home/data/cpq-tools/current/bin/dwg2dxf` | LibreDWG 0.14 |

许可证 `GPL-3.0-or-later`（ODA File Converter 的免费许可限定非商业用途，故未采用）。
服务器为共享目录安装、库固定在同目录、不依赖个人环境变量，`wugefei` 的 CPQ 服务有读取执行权限。

### 1.2 两份真实 DWG 的转换事实（本机实测，服务器数量一致）

| 样本 | 源大小 | DXF 大小 | warning | error | 退出码 | 关键实体 |
| --- | --- | --- | --- | --- | --- | --- |
| `酒盒.dwg` | 686,195 B | 3,826,412 B | **1520** | 0 | 0 | LINE 7792 · LWPOLYLINE 2 · MTEXT 773 · DIMENSION 316 |
| `圆盘盒.dwg` | 889,062 B | 4,088,695 B | **252** | **3**（`bit_read_BD`×2、`bit_read_BL`×1） | 0 | LINE 4540 · LWPOLYLINE 1754 · INSERT 421 · MTEXT 386 · DIMENSION 143 |

警告类别（归一化模板）：`Unknown flag (0x10)`、`Object handle not found <N>/<N>`、
`Unstable Class object <N> <TYPE> (<mask>) …`、`TODO TABLESTYLE r2010+ missing fields`、
`Skip HATCH common handles due to short handle stream` 等。

### 1.3 当前代码的五个缺口（这就是本规格要修的）

1. **只看退出码**：`adapters/local_cli.py` 判定成功的依据是 `returncode == 0` 且目标文件存在，
   没有质量门槛；`圆盘盒` 有 3 条位流错误却仍是 `status="ok"`。
2. **没有 warning/error 计数**：诊断信息只被折成一句
   「转换器输出了 N 字节诊断信息（只保留摘要，不入库）」，无法区分「干净转换」与「有损转换」。
3. **没有 `success_with_warnings` 状态**：状态只有 `ok` / `failed`，中间态无法表达。
4. **没有服务器配置项**：二进制只能靠 `shutil.which` 在 `PATH` 里找；34 服务器的
   `/home/data/cpq-tools/current/bin/dwg2dxf` 不一定在服务进程的 `PATH` 里，也没有
   `provider` / `binary` / `version` 的显式配置与校验。
5. **预览未接线**：`dwg2SVG` 已可用（`酒盒 → 2,031,603 B SVG`、`圆盘盒 → 1,861,437 B SVG`），
   但适配器仍返回「不支持预览渲染」，导致 B 层验收卡在「没有预览产物」。

## 2. 契约 A：转换器配置（服务器可注入）

| 环境变量 | 取值 | 说明 |
| --- | --- | --- |
| `DWG_CONVERTER_PROVIDER` | `libredwg` / `oda` / `fake` / `none` / `auto`（默认 `auto`） | 显式指定驱动；`none` = 锁死「未安装」；`auto` = 按 §3 顺序探测 |
| `DWG_CONVERTER_BINARY` | **绝对路径**（推荐）或裸命令名 | 绝对路径优先且**不经 `PATH`**；给出时不再探测其它候选 |
| `DWG_CONVERTER_VERSION` | 例如 `0.14` | 期望版本；探测到的实际版本与之不一致 → `DWG_CONVERTER_BINARY_UNUSABLE`（§6） |
| `DWG_CONVERTER_PREVIEW_BINARY` | 绝对路径（可选） | 预览渲染工具；缺省取转换器二进制**同目录**的 `dwg2SVG`，没有则 `preview_available=false` |

- 旧名 `CAD_CONVERTER` / `CAD_CONVERTER_ALLOW_SIMULATED` / `CAD_CONVERTER_TIMEOUT_SECONDS` /
  `CAD_CONVERTER_MAX_OUTPUT_BYTES` / `CAD_CONVERTER_MAX_OUTPUT_FILES` **继续可用**（向后兼容）；
  `DWG_CONVERTER_*` 优先，两者都给出且冲突时以 `DWG_CONVERTER_*` 为准并写一条
  `converter_config_shadowed` 警告。第 2 批已有的 `CAD_CONVERTER=fake` 语义不变。
- 二进制不可用时的**报错位置**：`capability()` **不抛异常**，返回 `available=false` +
  `stable_error_code` + `message` + `provider/binary/expected_version/actual_version`；
  `convert_drawing()` 则必须 `raise FileCapabilityError("DWG_CONVERTER_BINARY_UNUSABLE", detected=…)`。
- `capability()` 必须新增并如实返回以下字段（现有字段一个都不许删）：

```json
{"available": true, "provider": "libredwg", "binary": "/home/data/cpq-tools/current/bin/dwg2dxf",
 "converter_version": "0.14", "expected_version": "0.14", "version_ok": true,
 "argv_verified": true, "preview_available": true, "simulated": false,
 "dwg_conversion": true, "three_d_conversion": false, "dwg_supported": false,
 "support_claim": "conversion_available", "stable_error_code": "", "message": "已安装 DWG 转换服务（libredwg 0.14）"}
```

- **不许**为了「看起来可用」而回退：配置了 `libredwg` 但二进制不可用 → 直接报 §6 的码，
  **不得**静默改用 fake 或其它 provider。
- 二进制安全检查：必须是存在且可执行的文件；**禁止** `sh` / `bash` / `zsh` / `python*` / `env`
  之类解释器作为转换器二进制（防注入），违反 → `DWG_CONVERTER_BINARY_UNUSABLE`。
- 生产环境（`APP_ENV=production`）依旧禁止 fake（沿用第 2 批既有规则）。

## 3. 契约 B：驱动与 argv（按驱动分派，已实测）

| 驱动 | argv 形状 | 产物名 | 状态 |
| --- | --- | --- | --- |
| `libredwg_dwg2dxf`（`dwg2dxf`） | `dwg2dxf -y -o <out.dxf> <source.dwg>` | `converted.dxf` | **已按 0.14 实测** |
| `libredwg_dwgread`（`dwgread`） | `dwgread -O DXF -o <out.dxf> <source.dwg>` | `converted.dxf` | **已按 0.14 实测** |
| `oda_file_converter`（`ODAFileConverter`/`TeighaFileConverter`） | `<exe> <inDir> <outDir> ACAD2018 DXF 0 1` | `<src 名>.dxf` | 未安装、**未验证** |

- `argv_verified` 必须如实反映上表：未验证的驱动只能是 `false`，且 `capability().message` 里
  不得把它说成已验证。
- 预览：libredwg 用 `dwg2SVG --mspace <source.dwg>`，**输出走 stdout**（该工具没有 `-o`），
  编排层把 stdout 落成 `<out>.svg`；落盘后必须非空且含 `<svg`。产出的 SVG **不是栅格图**，
  只用于页面预览与后续（第 4 批）栅格化，**禁止**直接把 SVG 当图片送视觉模型。
- 一律 `subprocess.run(argv 列表, timeout=…, shell=False)`；输出路径必须 realpath 落在
  临时目录内（沿用第 2 批 §4 的安全规则）。

## 4. 契约 C：产物质量门槛（不许只看退出码）

转换成功的判定必须**同时**满足：

1. 退出码 0（非 0 → `DWG_CONVERSION_FAILED`，超时 → `DWG_CONVERSION_TIMEOUT`）。
2. DXF 非空且 ≥ 配置文件大小下限（沿用 `MAX_OUTPUT_BYTES` 的上限检查）。
3. **结构完整**：能被解析器读一遍（`ezdxf.readfile`，只读表结构与实体计数，不做几何计算）：
   含 `HEADER`/`TABLES`/`ENTITIES` 段与 `$ACADVER`；解析器不可用时退化为字节级检查
   （`SECTION`/`ENDSEC`/`ENTITIES` 出现且 `$ACADVER` 可读）并把 `quality.verified=false`。
4. `entity_count > 0` **且** `layer_count > 0`（模型空间顶层实体，不展开块引用）。
5. 以上任一条不满足 → `DWG_CONVERTER_OUTPUT_INVALID`（502，可重试），**不许**判成功。

manifest 新增 `quality` 块（计数口径：模型空间**顶层**实体、未展开块）：

```json
{"quality": {"verified": true, "parser": "ezdxf", "parser_version": "1.4.4",
             "dxf_version": "AC1027", "insunits": 4, "entity_count": 6711, "layer_count": 7,
             "text_count": 127, "dimension_count": 316, "block_ref_count": 0,
             "degraded": false, "checks": ["exit_code", "non_empty", "structure", "entities", "layers"]}}
```

- `degraded = (error_count > 0 or warning_count > 0)`。

计数口径（与第 3 批交叉核对时必须一致）：`entity_count` / `text_count` / `dimension_count` /
`block_ref_count` 统计的都是**模型空间顶层**实体（不展开块引用）；`layer_count` 是**图层表条目数**
（含未被任何实体引用的图层），与第 3 批 `stats.layer_total` 同口径。

## 5. 契约 D：诊断捕获与状态机

- 从转换器 stderr/stdout 统计 `warning_count` / `error_count`：行内出现 `Warning`/`warning:` 记警告，
  出现 `ERROR`/`error:` 记错误（大小写与 `Warning:`/`ERROR:` 前缀都要覆盖）。
- `warning_codes`：把诊断行归一化成**模板**（数字/句柄/mask → `N`）后去重计数，
  形如 `{"Unknown flag (0xN)": 308, "Object handle not found N/N ...": 108}`；最多 20 条，
  超出置 `warning_codes_truncated=true`。
- 诊断原文**不入库**：manifest 只放 `diagnostics_bytes`（原始字节数）与 `diagnostics_sha256`；
  禁止把 stderr 原文、绝对路径、堆栈写进 manifest、审计或用户可见响应。
- manifest 新增字段：`warning_count`、`error_count`、`warning_codes`、`warning_codes_truncated`、
  `diagnostics_bytes`、`diagnostics_sha256`。
- **状态闭集**（`status` 只允许这三个值）：

| status | 触发 | 用户可见含义 |
| --- | --- | --- |
| `ok` | 质量门槛通过 且 `warning_count == 0` 且 `error_count == 0` | 干净转换 |
| `success_with_warnings` | 质量门槛通过，但 `error_count > 0` 或 `warning_count > 0` | 已生成可用图纸，但**有损/有警告**，需下游核对 |
| `failed` | 门槛未过或进程失败（带稳定错误码） | 没拿到可用图纸 |

- **禁止「无损」声称**：只有 `status == "ok"`（两个计数都为 0）时，任何 manifest 字段、审计或
  界面文案才可以说「无损/lossless/完全一致」；否则必须出现「有警告」「有 N 条错误」这类如实表述。
  红测按「manifest 序列化结果里不得出现 `lossless`/`无损` 字样」断言。
- 下游规则：第 3 批只接受 `ok` / `success_with_warnings`；后者必须把计数与状态带进 CAD IR（§7）。
- 审计（`dwg.convert.*`）追加记录 `provider` / `binary`（只记 basename）/ `converter_version` /
  `status` / `warning_count` / `error_count` / `quality.entity_count` / `quality.layer_count`。

## 6. 契约 E：错误码（第 1 批闭集新增 1 条）

| `stable_error_code` | HTTP | `retryable` | 触发 |
| --- | --- | --- | --- |
| **`DWG_CONVERTER_BINARY_UNUSABLE`**（新） | 500 | 否 | 配置的二进制不存在/不可执行/是解释器/实际版本与 `DWG_CONVERTER_VERSION` 不一致 |

- `detected` 必须带 `provider` / `binary` / `expected_version` / `actual_version`。
- 该码并入第 1 批 Spec §3 的**唯一权威闭集**（17 → 18 条），第 1 批红测 `ERROR_CODES` 同步。
- 其余沿用：质量门槛失败用 `DWG_CONVERTER_OUTPUT_INVALID`，写不出产物用
  `DWG_CONVERTER_OUTPUT_MISSING`，越界用 `DWG_CONVERTER_UNSAFE_PATH`，超时用 `DWG_CONVERSION_TIMEOUT`，
  非 0 退出用 `DWG_CONVERSION_FAILED`，没有可用转换器用 `DWG_CONVERTER_NOT_INSTALLED`。

## 7. 契约 F：与第 3 批的联动（交叉核对）

CAD IR 的 `source` 必须透传第 2 批的质量信息，供第 4 批判断「这份图纸能不能信」：

```json
{"source": {"conversion_status": "success_with_warnings", "warning_count": 252, "error_count": 3,
            "converter_name": "libredwg", "converter_version": "0.14",
            "quality": {"verified": true, "entity_count": 3457, "layer_count": 4},
            "crosscheck": {"match": true,
                           "deltas": {"entity_count": 0, "layer_count": 0, "text_count": 0,
                                      "dimension_count": 0, "block_ref_count": 0}}}}
```

- `crosscheck` 的口径：把 IR 侧**模型空间顶层**实体/文字/标注/块引用/图层计数与 manifest 的
  `quality` 逐项相减；全为 0 → `match=true`。
- `conversion_status != "ok"` → IR 的 `warnings` 必须含 `conversion_degraded`（带计数）；
  `crosscheck.match == false` → 追加 `ir_manifest_mismatch` 警告（带 deltas），
  **不得**静默改数或丢掉不一致。
- 这两条是第 3 批的验收点（红测 `E3`），实现放在第 3 批，本规格只冻结字段名。

## 8. 契约 G：环境配置与验收边界

- 34 服务器（生产注入示例，本轮**不执行**）：

```
DWG_CONVERTER_PROVIDER=libredwg
DWG_CONVERTER_BINARY=/home/data/cpq-tools/current/bin/dwg2dxf
DWG_CONVERTER_VERSION=0.14
```

- 本机（开发/验收）：`DWG_CONVERTER_BINARY=/opt/homebrew/bin/dwg2dxf`，`DWG_CONVERTER_VERSION=0.14`。
- CI：默认 `DWG_CONVERTER_PROVIDER=fake`（第 2 批规则），**不**依赖真二进制；真二进制 smoke
  只在存在时运行，缺失时 skip 并如实写「真实转换未验收」。
- 本轮不重启、不部署、不改服务器配置；配置全部走环境变量，缺配置时行为与旧版一致（未安装）。
- 未装 `ezdxf` 时质量门槛退化为结构检查并标 `quality.verified=false`，**不许**假装验过。

## 9. 红测清单（`tests/test_dwg_conversion_quality_repair_red.py`）

| 组 | 条数 | 覆盖 |
| --- | --- | --- |
| A 配置 | 6 | provider/binary/version 生效；绝对路径不走 PATH；二进制不可用 → 新码 + detected；版本不匹配 → 新码；`none` 锁死；旧名兼容与优先级 |
| B 驱动 argv | 4 | libredwg 形状被精确调用（fake CLI 记录 argv）；ODA 驱动 `argv_verified=false`；`dwg2SVG` 预览走 stdout 落盘；解释器二进制被拒 |
| C 质量门槛 | 5 | 退出码 0 + 空文件/截断/0 实体/0 图层 → 都不判成功；完整文件 → `ok` |
| D 诊断与状态 | 6 | 警告计数与模板；错误计数；`success_with_warnings`；状态闭集；禁止 lossless 声称；stderr 原文不入库 |
| E 真实二进制 | 4 | 两份样本能转；圆盘盒 `error_count>=1` 且 `success_with_warnings`；酒盒 1520 条警告不得说成干净；预览非空 |
| F 审计与文案 | 3 | 审计含 provider/版本/计数/质量；不含 stderr 原文与绝对路径；有损转换的文案如实 |

小夹具（fake CLI 脚本）只验证编排与判定逻辑；真实样本只验证兼容性。两者**不得混为一类**。

## 10. 非目标与禁止事项

- 不部署、不重启服务、不改服务器、不装新依赖（`ezdxf` 已属第 3 批依赖）。
- 不把 ODA 的 argv 形状用在 LibreDWG 上，也不把「未验证的驱动」说成已验证。
- 不因为退出码 0 就宣称成功；`圆盘盒` 不许被标成干净/无损。
- 不把 stderr 原文、绝对路径或堆栈写进 manifest、审计或响应。
- 不修改第 2 批既有的安全约束（临时目录、路径越界、大小/文件数上限、并发幂等、失败不覆盖成功产物）。
- 不用 fake 产物冒充真实转换；不为了让红测变绿而放宽门槛或改测试。

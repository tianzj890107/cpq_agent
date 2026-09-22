# 规格：DWG 前两批修复 —— ODA 27.1 主转换器 + LibreDWG 0.14 回退链的配置、质量门槛与转换状态

规格版本：`dwg-conversion-repair/2`。首版（`/1`）按「LibreDWG 单转换器」写；本版改为
**ODA 主 + LibreDWG 回退**的受控链，原因是 2026-09-21 两套转换器都已完成本机与 34 服务器
实测，且业务/法务已确认 ODA 可用于本 CPQ 生产环境。

适用范围：**只修第 1 批（文件能力契约）与第 2 批（受控转换服务）**。第 3 批（CAD IR）只在
§8 约定联动字段；第 4–6 批不动。本规格不授权部署、不重启、不改服务器、不装依赖。

关联文档：`docs/specs/dwg-file-capability-preflight.md`（第 1 批）、
`docs/specs/dwg-controlled-conversion-adapter.md`（第 2 批，§6 选型表由本规格落地）、
`docs/specs/dxf-cad-ir.md`（第 3 批）、`docs/specs/dwg-final-acceptance.md`（第 6 批门禁）。

状态：Spec + 红测（已实现）
红测：`tests/test_dwg_conversion_quality_repair_red.py`

## 1. 现状取证（实测）

### 1.1 两套转换器都已就位

| 环境 | 转换器 | 路径 | 版本 | 许可证 |
| --- | --- | --- | --- | --- |
| 本机 macOS ARM64 | ODA File Converter | `/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter`（**不在 `PATH`**） | 27.1（`Info.plist` 27.1.0.0；Developer ID 签名、公证、原生 arm64 均已验证） | ODA 免费版限非商业；**已由业务/法务确认可用于本 CPQ 生产环境** |
| 本机 macOS ARM64 | LibreDWG | `/opt/homebrew/bin/dwg2dxf`（另有 `dwgread`/`dwg2SVG`/`dwgbmp` 等） | 0.14 | `GPL-3.0-or-later` |
| 34 服务器 Ubuntu x86_64 | ODA File Converter | `/home/data/cpq-tools/oda-file-converter-27.1/squashfs-root/AppRun` | 27.1 | 同上 |
| 34 服务器 Ubuntu x86_64 | xvfb（用户级，无 sudo） | `/home/data/cpq-tools/xvfb-user/root/usr/bin/xvfb-run` | — | 系统包未改动 |
| 34 服务器 Ubuntu x86_64 | LibreDWG | `/home/data/cpq-tools/libredwg-0.14/bin/dwg2dxf`，稳定别名 `/home/data/cpq-tools/current/bin/dwg2dxf` | 0.14 | `GPL-3.0-or-later` |

- 服务器为共享目录安装、库固定在同目录、不依赖个人环境变量，`wugefei` 的 CPQ 服务有读取执行权限。
- Linux 版 ODA **即使命令行运行也依赖 X Display**，所以 34 服务器必须经 `xvfb-run` 调用。

### 1.2 服务器调用形状（实测可用，本轮只作配置依据，**不执行**）

```
PATH="/home/data/cpq-tools/xvfb-user/root/usr/bin:$PATH" \
xvfb-run -a \
/home/data/cpq-tools/oda-file-converter-27.1/squashfs-root/AppRun \
<input-directory> <output-directory> ACAD2018 DXF 0 1 '*.dwg'
```

- 位置参数依次为：输入目录、输出目录、输出版本 `ACAD2018`、输出格式 `DXF`、不递归 `0`、
  开启 Audit/Repair `1`、输入过滤 `*.dwg`。**早期报告漏写了最后的 `'*.dwg'`**，本规格以含
  过滤项的 7 项形状为准（§4）。
- `xvfb-run` 属于**包装前缀**，不是转换器本体：本规格要求适配器原生支持包装前缀（§3），
  不要求部署方再造一个 wrapper 脚本。

### 1.3 两套转换器的实测对比（用户 2026-09-21 报告）

| 指标 | 结论 |
| --- | --- |
| 模型空间实体类型与数量 / 图层名 / Layout 名 / 文字与尺寸文本 / 几何包围盒 | **完全一致** |
| 渲染像素差异 | 酒盒 699,840 像素**差 0**；圆盘盒 313,920 像素**差 0** |
| 块定义数量（酒盒） | ODA 319（约 316 个匿名尺寸块，与 316 个 `DIMENSION` 对应）；LibreDWG 719（多出约 400 个冗余匿名尺寸块） |
| `ezdxf.audit()` 修复项（圆盘盒） | ODA 0；LibreDWG 1 |
| 转换日志 | ODA 两份图服务器 stderr 为空；LibreDWG 酒盒约 1,520 条警告，圆盘盒约 252–255 条警告且含 3 条位流读取错误（`bit_read_BD`×2、`bit_read_BL`×1） |
| 输出 DXF 版本 | ODA 恒为 `ACAD2018`（`$ACADVER = AC1032`）；LibreDWG 尽力保留源版本 |
| 三维 | 两份样本模型空间 z 最小/最大值都是 0 → 现行内容按**二维**包装图纸处理，不进 STEP/3D |

### 1.3.1 本机复核（2026-09-21，只写 `/tmp`，未碰仓库与服务器）

用 §1.2 的 7 参数形状在本机各转一次：两份图均 `rc=0`、stderr 为空、**2–3 秒**完成。

| 样本 | 产物 | 大小 | `$ACADVER` | `$INSUNITS` | 模型空间**顶层**实体 | 图层数 | `ezdxf.audit()` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `酒盒.dwg` | `source.dxf` | 2,693,476 B | `AC1032` | 4（毫米） | **6711** | 8 | errors 0 / fixes 0 |
| `圆盘盒.dwg` | `source.dxf` | 4,138,970 B | `AC1032` | 4（毫米） | **3457** | 32 | errors 0 / fixes 0 |

- 酒盒顶层分布：`LINE 5598`、`DIMENSION 316`、`ARC 311`、`SPLINE 310`、`TEXT 70`、`MTEXT 57`、
  `ELLIPSE 21`、`ATTDEF 15`、`HATCH 11`、`LWPOLYLINE 2` —— 与用户报告的 ODA 分布一致。
- 圆盘盒顶层分布：`LINE 1632`、`LWPOLYLINE 1237`、`INSERT 234`、`DIMENSION 141`、`MTEXT 87`、
  `ARC 66`、`CIRCLE 56`、`POLYLINE 4`。
- 结论：含 `'*.dwg'` 的 7 参数形状**被 ODA 接受**；产物名确为 `source.dxf`；输出恒为 `AC1032`。
- 图层名里存在 `_U+56FE_U+5C42 1` 这类**转义残留**（源 DWG 里就是这么存的，与明文中文图层
  `轮廓线` 并存）→ 解析层必须容忍，**不得**当成乱码丢弃。

**实体分布口径不一致，本轮不冻结任何实体数。** 两次实测报告里同一转换器的实体类型分布不同
（一次像是含块展开、一次只数顶层），根因未核实。因此：

- 本规格**不写死**任何实体数/图层数；`quality.*` 由实现方用确定性解析重测后写入 manifest；
- 真实样本的基线数字只在第 6 批 L4 生成金标时固定，且必须人工复核（第 6 批 Spec §4）；
- 红测只断言「非零、可解析、可核对」，不断言具体数字（红测 `E1`/`E5`）。

### 1.4 本批生效的结论口径

- **主转换器 = ODA File Converter 27.1**（输出 `ACAD2018`/DXF，开启 Audit/Repair）。
- **回退转换器 = LibreDWG 0.14**，**只在主转换器明确失败时**启用（§7）。
- 主与回退都不可用 → 结构化 `DWG_CONVERTER_NOT_INSTALLED` / `DWG_CONVERTER_BINARY_UNUSABLE`；
  **禁止**把原始 DWG 发给视觉模型或 STEP 导入器（第 1 批铁律不变）。
- 两份样本当前按二维处理；「DWG 是否含三维」由第 6 批分流负责，本批不做。

### 1.5 当前代码的六个缺口（这就是本规格要修的）

1. **只看退出码**：`adapters/local_cli.py` 判定成功的依据是 `returncode == 0` 且目标文件存在，
   没有质量门槛；`圆盘盒`（LibreDWG）有 3 条位流错误却仍是 `status="ok"`。
2. **没有 warning/error 计数**：诊断信息只被折成一句
   「转换器输出了 N 字节诊断信息（只保留摘要，不入库）」，无法区分「干净转换」与「有损转换」。
3. **没有 `success_with_warnings` 状态**：状态只有 `ok` / `failed`，中间态无法表达。
4. **没有服务器配置项、也没有回退链**：二进制只能靠 `shutil.which` 在 `PATH` 里找；34 服务器的
   `/home/data/cpq-tools/current/bin/dwg2dxf` 不一定在服务进程的 `PATH` 里；没有
   `provider` / `binary` / `version` 的显式配置与校验；**更没有任何回退配置**，主转换器失败只能整单失败。
5. **预览未接线**：`dwg2SVG` 已可用（酒盒 → 2,031,603 B SVG、圆盘盒 → 1,861,437 B SVG），
   但适配器仍返回「不支持预览渲染」，导致 B 层验收卡在「没有预览产物」。
6. **ODA 驱动是「未验证」的空壳**（本版新增的缺口）：
   `DRIVERS["oda_file_converter"]` 的 `argv_verified=False`；`argv` 只有 6 个参数，**缺 `'*.dwg'`**；
   `version_args=("--version",)` 而 **ODA 不支持 `--version`**；`AUTO_PROBE` 把 `libredwg` 排在
   `oda` 前面。即：既没有按真机形状调用 ODA，也没有把 ODA 当主转换器的任何能力。

## 2. 契约 A：转换器链配置（服务器可注入）

### 2.1 主转换器

| 环境变量 | 取值 | 说明 |
| --- | --- | --- |
| `DWG_CONVERTER_PROVIDER` | `oda` / `libredwg` / `libredwg-cli` / `teigha` / `fake` / `none` / `auto`（默认 `auto`） | 显式指定主驱动；`none` = **整条链关闭**；`auto` = 按 §2.4 顺序探测 |
| `DWG_CONVERTER_BINARY` | **绝对路径**（推荐）或裸命令名 | 绝对路径优先且**不经 `PATH`**；给出时不再探测其它候选 |
| `DWG_CONVERTER_VERSION` | 例如 `27.1`（ODA）、`0.14`（LibreDWG） | 期望版本；口径见 §2.5 |
| `DWG_CONVERTER_WRAPPER` | 空格分隔的 argv 前缀，例如 `/home/data/cpq-tools/xvfb-user/root/usr/bin/xvfb-run -a` | **本版新增**；逐项排在可执行文件之前；校验规则见 §2.3 |
| `DWG_CONVERTER_PREVIEW_BINARY` | 绝对路径（可选） | 预览渲染工具；缺省取转换器二进制**同目录**的 `dwg2SVG`，没有则 `preview_available=false` |

### 2.2 回退转换器（本版新增）

| 环境变量 | 取值 | 说明 |
| --- | --- | --- |
| `DWG_CONVERTER_FALLBACK_PROVIDER` | `libredwg`（默认）/ `libredwg-cli` / `oda` / `teigha` / `none` | `none` = 关闭回退 |
| `DWG_CONVERTER_FALLBACK_BINARY` | 绝对路径或裸命令名（可选） | 缺省时按 provider 在 `PATH` 里探测 |
| `DWG_CONVERTER_FALLBACK_VERSION` | 例如 `0.14` | 期望版本，口径同 §2.5 |
| `DWG_CONVERTER_FALLBACK_WRAPPER` | 空格分隔的 argv 前缀（可选，默认空） | 与主转换器 wrapper 各自独立：回退是纯 CLI，不需要 X，允许留空 |
| `DWG_CONVERTER_FALLBACK_PREVIEW_BINARY` | 绝对路径（可选） | 一般留空——预览统一走 §4 的 `dwg2SVG` |

- 回退**默认开启**（`libredwg`），因为 ODA 是主转换器且它可能需要 X/授权环境；关闭方式是显式
  `DWG_CONVERTER_FALLBACK_PROVIDER=none`。
- 回退**只在主转换器明确失败时**发生，触发条件见 §7；配置类错误**不回退**。

### 2.3 wrapper（包装前缀）校验

- 空格分隔成 argv 项；**不做 shell 解析**（不认引号、变量、重定向、`&&`、通配符）。
- 每项非空，且只允许 `[A-Za-z0-9._/+-]`；出现空白（例如被引号包住的带空格路径）、`;`、`|`、
  `&`、`$`、反引号、`>`、`<`、`*`、`?`、`~`、`(`、`)` → 非法。
- 第一项必须存在且可执行（绝对路径，或 `PATH` 命中）。
- 非法或首项不可用 → `capability().available=false` + `stable_error_code="DWG_CONVERTER_BINARY_UNUSABLE"`
  + `binary_reason="wrapper_invalid"`；`convert_drawing()` 抛同码且 `detected.binary_reason="wrapper_invalid"`。
- 生效时形状固定为 `argv = [*wrapper, exe, *driver_args]`，一律 `subprocess.run(..., shell=False)`。
- `capability().wrapper` 返回生效的 wrapper 数组（未配置为 `[]`）。

### 2.4 `auto` 的探测顺序（本版改）

`oda` → `libredwg` → `libredwg-cli` → `teigha`。

理由：ODA 是主转换器。**探测 ODA 时不许执行任何子进程**（ODA 不支持 `--version`，见 §2.5）。

### 2.5 版本口径（本版新增 ODA 特例）

- **ODA 不支持版本探测**：
  - 声明了 `DWG_CONVERTER_VERSION` → `converter_version = expected_version = 声明值`、
    `version_ok=true`、`version_source="config_declared"`；
  - 没声明 → 仍 `available=true`（二进制存在且可执行），但 `version_ok=false`、
    `version_source="unverifiable"`、`converter_version=""`，且 `message` 必须写明
    「版本未固定（ODA 不支持版本探测）」；**第 6 批部署门禁第 2 项会因此 No-Go**。
  - **禁止**把目录名 / `Info.plist` / 安装包名当成「已探测版本」（跨平台不可靠）；
    **禁止**用 `--version` 探测 ODA。
- LibreDWG 仍按 `--version` 探测 → `version_source="probed"`；实际版本与 `DWG_CONVERTER_VERSION`
  不一致 → `DWG_CONVERTER_BINARY_UNUSABLE`（沿用首版规则）。
- `version_source` 闭集：`probed` / `config_declared` / `unverifiable`。

### 2.6 其它配置规则

- 旧名 `CAD_CONVERTER` / `CAD_CONVERTER_ALLOW_SIMULATED` / `CAD_CONVERTER_TIMEOUT_SECONDS` /
  `CAD_CONVERTER_MAX_OUTPUT_BYTES` / `CAD_CONVERTER_MAX_OUTPUT_FILES` **继续可用**（向后兼容）；
  `DWG_CONVERTER_*` 优先，两者都给出且冲突时以 `DWG_CONVERTER_*` 为准并写一条
  `converter_config_shadowed` 警告。第 2 批已有的 `CAD_CONVERTER=fake` 语义不变。
- provider=`none` → 整条链关闭（含回退），`available=false`、`DWG_CONVERTER_NOT_INSTALLED`。
- provider=`fake` → 只用模拟器、**不回退**（避免用真转换器掩盖编排缺陷）；生产环境依旧禁止 fake。
- 主与回退 provider 相同时 → 视为「无回退」（`fallback.enabled=false`），不重复跑同一条命令。
- 二进制安全检查（主与回退都适用）：必须存在且可执行；**禁止** `sh` / `bash` / `zsh` / `python*` /
  `env` 之类解释器作为转换器二进制，违反 → `DWG_CONVERTER_BINARY_UNUSABLE` +
  `binary_reason="binary_is_interpreter"`。
- 二进制不可用时的**报错位置**：`capability()` 不抛异常，返回 `available=false` +
  `stable_error_code` + `message` + `provider/binary/expected_version/actual_version/binary_reason`；
  `convert_drawing()` 则必须 `raise FileCapabilityError(码, detected=…)`。

### 2.7 `capability()` 必须新增/如实返回的字段（现有字段一个都不许删）

```json
{"available": true, "provider": "oda",
 "binary": "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter",
 "converter_version": "27.1", "expected_version": "27.1", "version_ok": true,
 "version_source": "config_declared", "argv_verified": true, "binary_reason": "",
 "wrapper": ["/home/data/cpq-tools/xvfb-user/root/usr/bin/xvfb-run", "-a"],
 "preview_available": true, "simulated": false, "dwg_conversion": true,
 "three_d_conversion": false, "dwg_supported": false,
 "support_claim": "conversion_available", "stable_error_code": "",
 "primary_unavailable_reason": "",
 "fallback": {"enabled": true, "provider": "libredwg",
              "binary": "/home/data/cpq-tools/current/bin/dwg2dxf",
              "converter_version": "0.14", "expected_version": "0.14", "version_ok": true,
              "version_source": "probed", "argv_verified": true, "available": true,
              "stable_error_code": "", "binary_reason": "", "wrapper": []},
 "message": "已安装 DWG 转换服务（主 oda 27.1）"}
```

- `available` 口径：`available = 主可用 or 回退可用`；`provider` 永远是**主** provider（配置值，
  **不随跳次变**）。主不可用但回退可用 → `available=true`、`stable_error_code=""`、
  `primary_unavailable_reason=<主的原因码>`，`message` 必须明说「主转换器不可用，将使用备用转换器（…）」。
  两者都不可用 → `available=false`、`stable_error_code` 取**主**的码、`fallback.stable_error_code`
  记回退的码。
- **不许**为了「看起来可用」而回退到 `fake`/模拟器，也不许把未配置的转换器当回退。

## 3. 契约 B：驱动与 argv（按驱动分派，已实测）

| 驱动 | argv 形状（`exe` 之前还会逐项插入 wrapper） | 产物名 | 状态 |
| --- | --- | --- | --- |
| `oda_file_converter`（`ODAFileConverter` / `AppRun`） | `ODAFileConverter <inDir> <outDir> ACAD2018 DXF 0 1 *.dwg` | `source.dxf` | **已按 27.1 真机实测（本版转正为主驱动）** |
| `libredwg_dwg2dxf`（`dwg2dxf`） | `dwg2dxf -y -o <out.dxf> <source.dwg>` | `converted.dxf` | 已按 0.14 实测（回退驱动） |
| `libredwg_dwgread`（`dwgread`） | `dwgread -O DXF -o <out.dxf> <source.dwg>` | `converted.dxf` | 已按 0.14 实测（回退驱动） |

- ODA 的 6 个位置参数必须**逐字**一致；`0`=不递归、`1`=开启 Audit/Repair、最后一项是输入过滤
  `*.dwg`（**必须显式给出**，作为字面量传入，不经 shell 展开）。
- 每次转换用**独立临时目录**，输入固定落成 `source.dwg`，所以 ODA 的 `*.dwg` 过滤只会命中本次输入，
  不会误伤其它文件；输出固定为 `source.dxf`，missing → `DWG_CONVERTER_OUTPUT_MISSING`。
- 预览（与主转换器无关）：`dwg2SVG --mspace <source.dwg>`，**输出走 stdout**（该工具没有 `-o`），
  编排层把 stdout 落成 `<out>.svg`；落盘后必须非空且含 `<svg`。ODA 自身不产预览；部署环境没有
  `dwg2SVG` 时 `preview_available=false`，**不许**用别的产物冒充预览。产出的 SVG **不是栅格图**，
  只用于页面预览与第 4 批栅格化，**禁止**直接把 SVG 当图片送视觉模型。
- 输出路径必须 realpath 落在临时目录内（沿用第 2 批 §4 的安全规则）。
- `argv_verified` 必须如实：上表三条都是 `true`；未真机跑过的驱动只能是 `false`。

## 4. 契约 C：产物质量门槛（不许只看退出码）

转换成功的判定必须**同时**满足：

1. 退出码 0（非 0 → `DWG_CONVERSION_FAILED`，超时 → `DWG_CONVERSION_TIMEOUT`）。
2. DXF 非空且 ≥ 配置文件大小下限（沿用 `MAX_OUTPUT_BYTES` 的上限检查）。
3. **结构完整**：能被解析器读一遍（`ezdxf.readfile`，只读表结构与实体计数，不做几何计算）：
   含 `HEADER`/`TABLES`/`ENTITIES` 段与 `$ACADVER`；解析器不可用时退化为字节级检查
   （`SECTION`/`ENDSEC`/`ENTITIES` 出现且 `$ACADVER` 可读）并把 `quality.verified=false`。
4. `entity_count > 0` **且** `layer_count > 0`（模型空间顶层实体，不展开块引用）。
5. **Audit 留痕**：`quality.audit_enabled` 必须如实（ODA 传 `1` → `true`；LibreDWG 无此能力 → `false`）。
   ODA 跑完后再做一次 `ezdxf.audit()` 复核，其报出的 error 要按 §5 计数，**不许**因为「已经开过
   Audit/Repair」就省略复核。
6. 以上任一条不满足 → `DWG_CONVERTER_OUTPUT_INVALID`（502，可重试），**不许**判成功。

manifest 新增 `quality` 块（计数口径：模型空间**顶层**实体、未展开块）。下面示例的数值取自
§1.3.1 的酒盒 ODA 实测——**示例不是断言**，红测不断言具体数字：

```json
{"quality": {"verified": true, "parser": "ezdxf", "parser_version": "1.4.4",
             "dxf_version": "AC1032", "output_version": "ACAD2018", "audit_enabled": true,
             "insunits": 4, "entity_count": 6711, "layer_count": 8,
             "text_count": 127, "dimension_count": 316, "block_ref_count": 0,
             "degraded": false, "checks": ["exit_code", "non_empty", "structure", "entities", "layers"]}}
```

- `degraded = (error_count > 0 or warning_count > 0)`。
- **`dxf_version` 因转换器而异**：ODA 恒为 `AC1032`（`ACAD2018`），LibreDWG 尽力保留源版本。
  下游（第 3 批）必须按实际 header 解析，**禁止**假定固定版本。
- 计数口径（与第 3 批交叉核对时必须一致）：`entity_count` / `text_count` / `dimension_count` /
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

- **状态、计数、`quality` 一律指「生效跳次」**：回退成功时就是回退转换器的计数与产物。
- **同一份图的两个转换器可以有不同 status**（ODA 实测 stderr 为空 → `ok`；LibreDWG 有警告/错误 →
  `success_with_warnings`）。这是事实，**不许**把两边口径互相套用或强行统一。
- 产出方身份：`converter_name` / `converter_version` = **生效**转换器；另加
  `converter_role`（`primary` / `fallback`，本版新增）。
- **禁止「无损」声称**：只有 `status == "ok"`（两个计数都为 0）时，任何 manifest 字段、审计或
  界面文案才可以说「无损/lossless/完全一致」；否则必须出现「有警告」「有 N 条错误」这类如实表述。
  红测按「manifest 序列化结果里不得出现 `lossless`/`无损` 字样」断言。
- 下游规则：第 3 批只接受 `ok` / `success_with_warnings`；两者都必须把计数、状态与产出方身份带进
  CAD IR（§8）。
- 审计（`dwg.convert.*`）追加记录 `provider` / `converter_role` / `binary`（只记 basename） /
  `converter_version` / `status` / `warning_count` / `error_count` / `quality.entity_count` /
  `quality.layer_count`。

## 6. 契约 E：错误码（第 1 批闭集新增 1 条，共 18 条）

| `stable_error_code` | HTTP | `retryable` | 触发 |
| --- | --- | --- | --- |
| **`DWG_CONVERTER_BINARY_UNUSABLE`**（新） | 500 | 否 | 配置的二进制不存在/不可执行/是解释器/实际版本与 `DWG_CONVERTER_VERSION` 不一致/wrapper 非法 |

- `detected` 必须带 `provider` / `binary` / `expected_version` / `actual_version` / `binary_reason`。
  `binary_reason` 闭集：`binary_missing` / `binary_not_executable` / `binary_not_a_file` /
  `binary_is_interpreter` / `wrapper_invalid` / `version_mismatch`（空串表示无问题）。
- 该码并入第 1 批 Spec §3 的**唯一权威闭集**（17 → 18 条），第 1 批红测 `ERROR_CODES` 同步。
- 其余沿用：质量门槛失败用 `DWG_CONVERTER_OUTPUT_INVALID`，写不出产物用
  `DWG_CONVERTER_OUTPUT_MISSING`，越界用 `DWG_CONVERTER_UNSAFE_PATH`，超时用 `DWG_CONVERSION_TIMEOUT`，
  非 0 退出用 `DWG_CONVERSION_FAILED`，没有可用转换器用 `DWG_CONVERTER_NOT_INSTALLED`。

## 7. 契约 F：受控回退链（本版新增）

### 7.1 触发条件（只在主转换器**明确失败**时）

| 主转换器结果 | 是否回退 |
| --- | --- |
| 超时 `DWG_CONVERSION_TIMEOUT` | **是** |
| 非 0 退出 `DWG_CONVERSION_FAILED` | **是** |
| 产物缺失 `DWG_CONVERTER_OUTPUT_MISSING` | **是** |
| 质量门槛不过 `DWG_CONVERTER_OUTPUT_INVALID`（空/截断/0 实体/0 图层/结构坏） | **是** |
| 主二进制缺失/不可执行（`binary_reason ∈ {binary_missing, binary_not_executable, binary_not_a_file}`） | **是** |
| 主二进制版本与 `DWG_CONVERTER_VERSION` 不一致（`binary_reason="version_mismatch"`） | **是** |
| 配置非法（`wrapper_invalid` / `binary_is_interpreter`） | **否**——配置错误，换转换器也救不了 |
| 输入问题（`DWG_CONVERTER_UNSAFE_PATH`、源文件超限、格式识别失败） | **否**——换转换器没意义 |
| 回退被关闭（`FALLBACK_PROVIDER=none`、与主同 provider、主为 `fake`/`none`） | 否 |
| 回退转换器自身不可用（二进制/版本/wrapper） | 无可回退对象，直接按主失败处理 |

### 7.2 留痕（缺一不可）

- manifest：`fallback_used`（bool）、`primary_failure_code`（稳定码；无回退时 `""`）、
  `attempts[]`——**每一次**尝试一条，按发生顺序：
  `{"role": "primary"|"fallback", "provider", "converter_version", "status", "error_code",
    "warning_count", "error_count", "duration_ms"}`。
- 失败跳次**不许**写进 `output_files` / `output_sha256`；主转换器失败留下的半成品必须随临时目录
  清理，**不得**覆盖上一次成功产物（沿用第 2 批 §4）。
- 审计新增 `dwg.convert.fallback`（含 `primary_failure_code` 与生效 provider）。
- 两者都失败 → 抛出的稳定码取**主**的码，`detected` 必须**同时**带 `primary` 与 `fallback` 两段
  （各含 `provider`、`error_code`、`binary_reason`）；manifest `status="failed"`、`attempts` 两条。

### 7.3 缓存与幂等

- `cache_key` 的身份段必须含**生效转换器**身份：源 `sha256` + 主 `provider`/`version` +
  生效 `provider`/`version` + 生效二进制 sha256；`options` 追加 `output_version` / `audit_enabled` / `wrapper`。
- 后果（红测 `G7`）：回退跳次产生的成功产物与主转换器成功产物**不同键**；主转换器恢复后重跑
  **必然**重新调用主转换器，**不许**复用回退产物冒充主转换器结果。
- `conversion_id` 与产物目录仍只由「源 sha256 + 主 provider/version + 图纸版本」决定，
  **不因回退而分裂**：同一份图纸不产生两份产物目录。
- 同一生效转换器下的重复请求仍幂等复用（沿用第 2 批）；并发仍按 `project_id + cache_key` 串行，
  主/回退两条路径共用同一把锁。

## 8. 契约 G：与第 3 批的联动（交叉核对）

CAD IR 的 `source` 必须透传第 2 批的质量与产出方信息，供第 4 批判断「这份图纸能不能信」：

```json
{"source": {"conversion_status": "ok", "warning_count": 0, "error_count": 0,
            "converter_name": "oda", "converter_version": "27.1",
            "converter_role": "primary", "fallback_used": false,
            "output_version": "ACAD2018", "audit_enabled": true,
            "quality": {"verified": true, "entity_count": 6711, "layer_count": 8},
            "crosscheck": {"match": true,
                           "deltas": {"entity_count": 0, "layer_count": 0, "text_count": 0,
                                      "dimension_count": 0, "block_ref_count": 0}}}}
```

- `crosscheck` 的口径：把 IR 侧**模型空间顶层**实体/文字/标注/块引用/图层计数与 manifest 的
  `quality` 逐项相减；全为 0 → `match=true`。
- `conversion_status != "ok"` → IR 的 `warnings` 必须含 `conversion_degraded`（带计数）；
  `crosscheck.match == false` → 追加 `ir_manifest_mismatch` 警告（带 deltas），**不得**静默改数或丢掉不一致。
- **`fallback_used == true` → IR 的 `warnings` 必须含 `conversion_fallback_used`**（带生效 provider
  与主失败码），第 4 批据此降低相关字段的可信度。
- 这些是第 3 批的验收点（红测 `E3`），实现放在第 3 批，本规格只冻结字段名。

## 9. 契约 H：三环境能力配置

- 本机（开发/验收）：

```
DWG_CONVERTER_PROVIDER=oda
DWG_CONVERTER_BINARY=/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter
DWG_CONVERTER_VERSION=27.1
DWG_CONVERTER_FALLBACK_PROVIDER=libredwg
DWG_CONVERTER_FALLBACK_BINARY=/opt/homebrew/bin/dwg2dxf
DWG_CONVERTER_FALLBACK_VERSION=0.14
DWG_CONVERTER_PREVIEW_BINARY=/opt/homebrew/bin/dwg2SVG
```

（本机 ODA 不需要 Xvfb，`DWG_CONVERTER_WRAPPER` 留空。）

- 34 服务器（生产注入示例，本轮**不执行**）：

```
DWG_CONVERTER_PROVIDER=oda
DWG_CONVERTER_BINARY=/home/data/cpq-tools/oda-file-converter-27.1/squashfs-root/AppRun
DWG_CONVERTER_VERSION=27.1
DWG_CONVERTER_WRAPPER=/home/data/cpq-tools/xvfb-user/root/usr/bin/xvfb-run -a
DWG_CONVERTER_FALLBACK_PROVIDER=libredwg
DWG_CONVERTER_FALLBACK_BINARY=/home/data/cpq-tools/current/bin/dwg2dxf
DWG_CONVERTER_FALLBACK_VERSION=0.14
DWG_CONVERTER_PREVIEW_BINARY=/home/data/cpq-tools/current/bin/dwg2SVG
```

  - ODA 用 **`squashfs-root/AppRun`**（不是 `.AppImage` 挂载点）；Linux 无头必须经 wrapper，
    且 wrapper 是绝对路径 —— 服务进程**不需要**自己改 `PATH`。
  - 回退 wrapper 留空：LibreDWG 是纯 CLI，不需要 X。
  - 预览需要 LibreDWG 的 `dwg2SVG`：**主转换器换成 ODA 后，LibreDWG 仍必须安装**，否则没有预览。
    若部署环境确实不装 LibreDWG，则必须如实 `preview_available=false`；基于 DXF 的预览渲染留给
    第 3 批评估，本批不做。
- CI：默认 `DWG_CONVERTER_PROVIDER=fake`，**不**依赖真二进制；真二进制 smoke 只在存在时运行，
  缺失时 skip 并如实写「真实转换未验收」。
- 本轮不重启、不部署、不改服务器配置；配置全部走环境变量，缺配置时行为与旧版一致（未安装）。
- 未装 `ezdxf` 时质量门槛退化为结构检查并标 `quality.verified=false`，**不许**假装验过。

## 10. 红测清单（`tests/test_dwg_conversion_quality_repair_red.py`）

| 组 | 条数 | 覆盖 |
| --- | --- | --- |
| A 配置 | 11 | provider/binary/version 生效；绝对路径不走 PATH；二进制不可用 → 新码 + detected；版本不匹配 → 新码；`none` 锁死；旧名兼容与优先级；`auto` 以 ODA 优先；wrapper 逐项排在 exe 前生效；非法 wrapper 被拒；ODA 版本只能显式声明 |
| B 驱动 argv | 5 | libredwg 形状被精确调用；**ODA 驱动 `argv_verified=true`**；`dwg2SVG` 预览走 stdout 落盘；解释器二进制被拒；ODA argv 逐字 7 项（含 `*.dwg`）且只调用一次 |
| C 质量门槛 | 4 | 退出码 0 + 空文件/截断/0 实体 → 都不判成功；完整文件 → `ok` 且带 `quality` 块 |
| D 诊断与状态 | 6 | 警告计数与模板；错误计数；`success_with_warnings`；状态闭集；禁止 lossless 声称；stderr 原文不入库 |
| E 真实二进制 | 6 | LibreDWG 两份样本能转、圆盘盒 `error_count>=1` 且 `success_with_warnings`、酒盒警告不得说成干净、预览非空；**ODA 两份样本 `ok` 且 `audit_enabled=true`**；**真实回退链（主缺失 → 回退成功且留痕）** |
| F 审计与文案 | 3 | 审计含 provider/版本/计数/质量；不含 stderr 原文与绝对路径；有损转换的文案如实 |
| G 回退链 | 8 | 主成功不回退；非 0/超时/质量不过触发回退；两者皆败取主码；`none` 关闭回退；配置错误不回退；缓存不串跳次；留痕与文案 |

G 组逐条：

1. `G1` 主成功 → `fallback_used=false`、`attempts` 一条、回退 CLI **未被调用**。
2. `G2` 主非 0 退出 → 回退成功：`fallback_used=true`、`primary_failure_code=DWG_CONVERSION_FAILED`、
   生效 `converter_name=libredwg`、`converter_role=fallback`、`status` 取回退结果。
3. `G3` 主超时 → `primary_failure_code=DWG_CONVERSION_TIMEOUT` + 回退成功。
4. `G4` 主产物为空（质量门槛不过）→ `primary_failure_code=DWG_CONVERTER_OUTPUT_INVALID` + 回退成功；
   主跳次半成品不得进 `output_files`。
5. `G5` 两者都失败 → 抛**主**码、`detected` 带 `primary`/`fallback` 两段、manifest `status=failed`、
   `attempts` 两条。
6. `G6` `DWG_CONVERTER_FALLBACK_PROVIDER=none` → 不回退、`attempts` 一条、抛主码。
7. `G7` 缓存不串跳次：先「主二进制缺失 + 回退可用」跑出回退成功，再让主可用并重跑同一份图 →
   必须重新调用主转换器、`converter_role=primary`。
8. `G8` 配置错误（非法 wrapper）→ 不回退、抛 `DWG_CONVERTER_BINARY_UNUSABLE`、`attempts` 一条。

小夹具（假 CLI / 假 wrapper）只验证编排与判定逻辑；真实样本只验证兼容性。两者**不得混为一类**。

## 11. 非目标与禁止事项

- 不部署、不重启服务、不改服务器、不装新依赖（`ezdxf` 已属第 3 批依赖）；本轮**不执行**服务器命令。
- 不把 ODA 的 argv 形状用在 LibreDWG 上（也不反过来）；不把「未验证的驱动」说成已验证。
- 不因为退出码 0 就宣称成功；ODA 的 `ok` 与 LibreDWG 的 `success_with_warnings` 都是如实结果，
  **不许**互相套用或强行统一；`圆盘盒` 经 LibreDWG 转换时不许被标成干净/无损。
- **不许静默回退**：回退必须由配置显式开启、只在 §7.1 的触发条件发生、且必须留下
  `fallback_used` / `primary_failure_code` / `attempts`。
- **不许**回退到 `fake`/模拟器；生产环境禁止 fake（沿用第 2 批）。
- 不把 stderr 原文、绝对路径（用户可见响应里）、堆栈写进 manifest、审计或响应；manifest 的
  `binary` 只记 basename。
- 不修改第 2 批既有的安全约束（临时目录、路径越界、大小/文件数上限、并发幂等、失败不覆盖成功产物）。
- 不用 fake 产物冒充真实转换；不为了让红测变绿而放宽门槛或改测试。

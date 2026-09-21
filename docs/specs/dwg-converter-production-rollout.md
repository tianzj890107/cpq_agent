# 规格：DWG 转换器在 34 生产上线（ODA 27.1 主用 + LibreDWG 0.14 回退）

批次：新五批（上线闭环）**第 1 批**。红测：`tests/test_dwg_converter_production_rollout_red.py`。

**前提已完成（不在本批范围，不得重复做）**：ODA File Converter 27.1 与 LibreDWG 0.14 已在
本机（macOS ARM64）与 34 服务器安装并完成转换质量对比 —— 两份真实样本（`酒盒.dwg`、
`圆盘盒.dwg`）主要二维几何完全一致、渲染 0 像素差异、图层/文字/尺寸/包围盒一致，ODA 的
DXF 结构更干净（尺寸块冗余更少、ezdxf audit 无需修复、无 stderr）；业务/法务已确认 ODA 可
用于本 CPQ 生产环境。结论已定：**ODA 主用，仅在主转换器明确失败时回退 LibreDWG**。

**本批目标**：让 34 上运行中的 8010 **真的调到转换器**，并把「怎么配、怎么验、怎么判定」
固化成可执行、可审计、可复现的部署口径。本批**不改转换器代码**（`cad_converter` 已实现且
红测全绿），交付的是**部署配置口径 + 部署文档 + 上线门禁 + 真机验收证据要求**。

---

## 1. 现状取证（实测）

- `DEPLOYMENT.md` 全文 `grep -E "DWG|转换器|dwg_deploy_gate|dwg_conversion_smoke|dwg_sample_e2e"`
  → **0 命中**。部署文档里没有任何转换器配置段，34 上线时没有人给 8010 配 `DWG_CONVERTER_*`，
  这正是「线上 2.1 提示 DWG/DXF 不能解析、只能退化成看 PNG」的直接原因。
- 代码侧已就绪（不作为本批改动对象）：
  - `tech_app/backend/services/cad_converter/`：provider `oda` / `libredwg` / `libredwg-cli`、
    `DWG_CONVERTER_WRAPPER`（wrapper 逐项排在 exe 之前）、显式回退链、manifest、`capability()`；
  - `tech_app/backend/main.py:889`（`/…/cad/converter/capability`）已在健康检查里上报能力；
  - `tech_app/tools/dwg_deploy_gate.py`（18 项门禁）、`dwg_conversion_smoke.py`（A/B 两层）、
    `dwg_sample_e2e.py`（单样本 E2E）、`dwg_acceptance_report.py`（验收记录/金标，需审批人）。
- 本地 DWG 侧红测**全绿**（实测，2026-09-21）：`test_dwg_file_capability_preflight_red` 29 OK、
  `test_dwg_conversion_adapter_red` 42 OK(1 skip)、`test_dwg_conversion_quality_repair_red` 43 OK、
  `test_dxf_cad_ir_red` 46 OK(1 skip)、`test_dwg_final_acceptance_red` 53 OK、
  `test_dwg_real_samples_e2e_red` 9 OK(9 skip)。

结论：**缺的不是代码，是配置与文档**——以及「漏配无人拦」这一部署治理缺口。

---

## 2. 配置口径（唯一取值表）

env 名**必须从代码常量取**（`cad_converter.service.PROVIDER_ENV` 等，见下），文档与配置里
不得手抄字符串（手抄会漂移）：

| 常量 | 34 取值 |
| --- | --- |
| `PROVIDER_ENV` | `oda` |
| `BINARY_ENV` | `/home/data/cpq-tools/oda-file-converter-27.1/squashfs-root/AppRun` |
| `VERSION_ENV` | `27.1` |
| `WRAPPER_ENV` | `/home/data/cpq-tools/xvfb-user/root/usr/bin/xvfb-run -a` |
| `FALLBACK_PROVIDER_ENV` | `libredwg` |
| `FALLBACK_BINARY_ENV` | `/home/data/cpq-tools/current/bin/dwg2dxf` |
| `FALLBACK_VERSION_ENV` | `0.14` |
| `LEGACY_PROVIDER_ENV`（旧名，兼容） | 不要再配（同时配且冲突会产生 `converter_config_shadowed` 告警） |

口径要点：

1. ODA 的 argv 形状固定为 **7 个位置参数**：`<exe> <inDir> <outDir> ACAD2018 DXF 0 1 *.dwg`
   （`0` = 不递归，`1` = 开启 Audit/Repair，`*.dwg` 逐字给出、不经 shell 展开）。输出版本
   `ACAD2018`、输出格式 `DXF`、`audit_enabled=True` 必须能在 capability/manifest 里看到。
2. Linux 版 ODA 即使命令行运行也依赖 X Display，**必须经 `xvfb-run -a` 包装**：wrapper 逐项
   排在 exe 之前，且 wrapper 目录要在服务进程的 `PATH` 里。
3. 回退默认**关闭**：未显式配置 `FALLBACK_PROVIDER_ENV` 就没有回退；**绝不把「这台机器恰好
   装了另一个转换器」当成可用回退**。
4. 回退只在**主转换器明确失败**时触发；`wrapper_invalid` / `binary_is_interpreter` 这类配置
   问题与输入类问题**绝不回退**。
5. `provider` 名必须取自 `KNOWN_PROVIDERS`；配 `oda` 时 `capability()["provider"]` 必须是
   `oda`，`capability()["fallback"]["provider"]` 必须是 `libredwg`。

---

## 3. 部署文档要求（`DEPLOYMENT.md` 新增段）

新增一节「DWG 转换器（包装图纸）」，必须覆盖：

1. §2 全表 env（名字与代码常量一致）+ 生效方式：写在**仓库外**的 env 文件（`0600`，如
   `/home/wugefei/CPQ/cpq_env.sh`）或用 `CPQ_ENV_FILE` 指定，**重启 8010** 生效；不得写进仓库。
2. `PATH` 要求：`xvfb-run` 所在目录（`/home/data/cpq-tools/xvfb-user/root/usr/bin`）必须在
   服务进程可见的 `PATH` 里。
3. 运行用户权限：服务用户对 `/home/data/cpq-tools` 及其子目录**可读可执行**（含 ODA
   squashfs 内的同目录依赖库），不依赖任何个人 shell 的环境变量。
4. 健康检查命令与**期望输出**：`available=true`、`provider=oda`、`converter_version=27.1`、
   `fallback.enabled=true`、`fallback.provider=libredwg`、`audit_enabled=true`、
   `output_version=ACAD2018`。
5. 上线三件套命令：
   `python tech_app/tools/dwg_conversion_smoke.py`（A/B 两层）、
   `python tech_app/tools/dwg_sample_e2e.py --sample 裕同包装项目-待开发/酒盒.dwg --out <目录>`
   （两份样本各一次）、
   `python tech_app/tools/dwg_deploy_gate.py --env production`（门禁）。
6. 判定文案与声明口径原文：「**DWG 编排能力完成，真实转换能力未验收**」；未通过两份真实样本
   L4 E2E 前不得写「支持 DWG」。
7. 两条硬禁令：**DWG 原始字节不得发给模型**；**DWG 不得交给 STEP importer**。
8. 失败时的正确行为：主失败才回退且 manifest 必须落 `fallback_used` / `primary_failure_code`；
   转换整体失败时页面/会话必须显式报错，**不得**退化成交给视觉模型看图猜尺寸。

---

## 4. 上线门禁新增一项（追加，不改既有 18 项）

`tech_app/tools/dwg_deploy_gate.py` 的 `GATE_ITEMS` **在末尾追加**一项（id 冻结、不可改名）：

```
("converter_rollout_documented", "auto", "部署文档已写明转换器配置、生效方式与验收命令")
```

- 该项检查 `DEPLOYMENT.md` 是否含 §3 的转换器段与 §2 表里的关键取值；
- **文档缺段时必须是 `fail`（不许 skip）**，`--env production` 下直接让 `verdict != "go"`；
- `GATE_VERSION` 与既有 18 项的 id/kind/结论口径**一律不动**（红测会逐项比对既有 id 序列）。

---

## 5. 真机验收证据（写入验收记录，必须有审批人）

对 `酒盒.dwg`、`圆盘盒.dwg` 各一次，证据里必须有：

样本 `sha256`、`provider`、`converter_version`、`output_version=ACAD2018`、`audit_enabled`、
`wrapper`、DXF 字节数、图层数、实体统计、`manifest` id、`status`
（`ok` / `success_with_warnings` 及 warning/error 计数）、3D 状态、产物相对路径。

纪律：

- **弱证据不得出现在这组证据里**：PNG 截图、视觉模型给出的像素推算值、文件名语义都不是转换证据；
- `is_simulated=true` 的产物**永远**不能作为 B 层证据；
- 金标/验收记录的变化必须人工审批，**不得为了让测试变绿自动更新**。

---

## 6. 正确失败（不得静默降级）

1. 二进制不存在/不可执行 → `available=false` + `stable_error_code` 非空 + 页面可见提示；
2. 配了 `PROVIDER_ENV=oda` 但 binary 路径不存在 → **不得**自动改用别的转换器（除显式配置的回退）；
3. 主失败走回退 → manifest 必须 `fallback_used=true` + `primary_failure_code` 非空；
4. 转换失败不得把图交给视觉模型猜尺寸、不得把 DWG 给 STEP importer、不得宣称「已解析」。

---

## 7. 红测

`tests/test_dwg_converter_production_rollout_red.py`，全部离线（不联网、不起服务、不真跑转换）：

| 组 | 覆盖 | 条数 |
| --- | --- | --- |
| A 部署文档必须写清转换器配置 | §3 | 10 |
| B 配置口径与代码常量一致 | §2 | 4 |
| C 上线门禁与正确失败 | §4 / §6 | 3 |
| D 验收证据链 | §5 | 3 |

运行：

```bash
./open-claude/.venv/bin/python -m unittest tests.test_dwg_converter_production_rollout_red -v
```

实现前实测：

```
Ran 20 tests  FAILED (failures=10)
```

红点：**A 组 9 条**（`DEPLOYMENT.md` 无转换器段：a1/a2/a3/a4/a5/a7/a8/a9/a10；
a6「生效方式」当前即绿 —— 文档已有 `CPQ_ENV_FILE`/`0600`/重启 的通用说明）、
**C1**（门禁 `GATE_ITEMS` 里没有 `converter_rollout_documented`，
现有 18 项 id 为 `converter_license … converter_chain_configured`）。

绿 10 条护栏：a6、B1（`AUTO_PROBE` 首选 `oda`）、B2（ODA 7 参数形状 + audit）、
B3（wrapper 逐项排在 exe 之前，非法 wrapper 判 `wrapper_invalid`）、B4（未装转换器时
`support_claim=orchestration_only`）、C2（production 下 `skip` 一律算 `fail`）、
C3（配了 oda 但二进制缺失时给稳定错误码、不换适配器、不启用未配置的回退）、
D1（fake 产物永不判 B 层通过）、D2（样本 E2E 输出 sha256/转换器/版本）、
D3（写验收记录必须给审批人）。

---

## 8. 禁止事项

- 不改 `cad_converter` 既有口径与既有红测；第 4 节只允许**追加**门禁项。
- 不把任何密码/密钥写进仓库、文档、夹具或日志（`wugefei` 的密码尤其禁止落盘）。
- 不装新依赖、不改样本 DWG、不写真实 `tech_data`。
- 不把 PNG/视觉结果当转换证据；不为了让门禁变绿删检查项。
- 本规格**不授权部署**：不重启服务、不改服务器文件、不连服务器（部署属后续批次，需用户当次明确授权）。

---

## 9. 与后续批次的接口

- 第 2 批（包装权威数据入库）：只有转换能力在 34 就绪后，DXF/CAD IR 才能作为「权威零件样本」的输入。
- 第 3 批（项目必须从报价开始）：与本批无耦合，可并行。
- 第 4 批（包装参数/BOM/工艺/成本闭环）：依赖本批的真实解析结果。
- 第 5 批（部署与终验）：本批产出的服务器验收证据是 L4 前置；终验用它核对「DWG 真解析」这一步。

# 可用性与服务边界（批次 5）：能力隔离 + 统一解析服务

血缘：承接 `10-待拍板优化清单.md` 第四部分批次映射（批 5 = `T8` + `T17`）、
`dwg-file-capability-preflight.md`（稳定错误码闭集）、
`dwg-final-acceptance.md`（验收声明 `support_claim` / `dwg_supported`）。

状态：Spec + 红测（已实现）（原状：本批只写 Spec 与红测（含既有实现的守卫）；业务实现由 Codex 在本地落地，按本文 §5 未提交/未推送/未部署，红测 12 项已全绿）
红测：`tests/test_capability_isolation_and_shared_parse_batch5_red.py`
复核基线：2026-10-09 实测（§1 每条都可指到文件/常量）

## 0. 一句话目标

把「转换器坏了」与「系统坏了」分开：**部署验收失败只阻止发布，运行时转换器故障只禁用
DWG 解析并明确告警**，登录 / 历史报价 / 文字·Excel·PDF·图片 快速报价一律不受影响；
并钉住「报价与技术工艺共用同一个统一解析服务、不装第二套 ODA」。

## 1. 现状缺口（代码事实 + 10-9 实测）

- **`T8` 运行侧已经是能力隔离**（本批**只验收、不重做**）：
  `cad_converter.capability()` 的 `available = 主可用 or 回退可用`，探测失败**不抛**；
  `unified_parse.capability()` 明确「能力查询绝不许 500」（探测抛错 → `dwg=False` + `detail`）；
  `file_preflight.STABLE_ERROR_CODES["DWG_CONVERTER_NOT_INSTALLED"]` 给 415/retryable 的
  **单文件**业务错误，不是系统级崩溃；`cpq_suite_server.main()` 对登录/工作流初始化失败也只打告警。
- **缺的是一层统一投影**：没有任何模块把 `capability()` 翻成「**哪些能力可用 / 哪些被禁用 /
  为什么**」给下游与界面用，调用方各自 `if available:`；也**没有**把「发布」与「运行」分开的判据。
- **`T8` 发布侧未接线**：`scripts/create_release.py` 只校验 tag 存在，**不消费**生产门禁报告
  （`dwg_deploy_gate.py --env production` 的 `verdict` 是 `go`/`no_go`）；`deploy_34_bare.sh`
  里那行门禁命令只是**打印提示**，不是判据。
- **`T17` 已经是共用同一服务**（本批**只验收、加守卫**）：
  `unified_parse.SERVICE_PATH = "/api/file/parse"`、`CAPABILITY_PATH = "/api/file/parse/capability"`；
  `main.py` 用它俩登记免登录白名单（唯一事实源）；`cpq_quick_quote_file.PARSE_PATH /
  CAPABILITY_PATH` 与之一致；报价侧**不 import** `ezdxf`/ODA，转换只有
  `tech_app/backend/services/cad_converter/adapters/local_cli.py` 一处。
- **`T17` 条件触发**：线上 DWG 是否可用，取决于 `/api/file/parse/capability` 的真探测结果；
  不可用就必须**如实**回 `service_unavailable`（既有实现），不许假装成功。本批只加守卫。

## 2. 契约

### 2.1 `T8` 运行侧 → 新模块 `tech_app/backend/services/capability_isolation.py`（纯函数）
- `ISOLATION_VERSION = "capability-isolation/1"`
- `isolation_view(capability) -> dict`：
  ```python
  {"version": ISOLATION_VERSION,
   "dwg_parse": "enabled" | "disabled",
   "documents": "enabled",                      # 文字/Excel/PDF/图片 恒可用
   "preview": "enabled" | "disabled",
   "disabled_reason": str,                      # disabled 时非空；enabled 时 ""
   "warning": str}                              # disabled 时非空且含 disabled_reason；enabled 时 ""
  ```
  规则：
  - 入参取 **两种形状**都认：`cad_converter.capability()`（`available` / `preview_available` /
    `stable_error_code`）与 `unified_parse.capability()`（`dwg` / `preview` / `detail`）；
  - `available = bool(cap.get("available")) or bool(cap.get("dwg"))`；
    `preview = bool(cap.get("preview_available")) or bool(cap.get("preview"))`；
  - `disabled_reason` 取最具体原因（`stable_error_code` → `error_code` → `detail` → 默认码
    `"DWG_PARSER_DISABLED"`）；
  - 入参不是 dict（`None`/字符串/异常对象）→ 视为 **disabled**，给默认码，**不抛异常**；
  - **红线**：本函数只描述「DWG 解析被禁用」，**不得**把 `documents` 置为 disabled。

### 2.2 `T8` 发布侧 → 同模块 `release_verdict(gate_report) -> dict`
- 入参形如 `dwg_deploy_gate.run_gate()` 的输出（`{"env": ..., "verdict": "go"|"no_go",
  "items": [{"id", "kind", "status"}, ...]}`）。
- 返回：
  ```python
  {"version": ISOLATION_VERSION,
   "release_ok": bool,
   "verdict": "go" | "no_go",
   "blocking": [item_id, ...],
   "manual": [item_id, ...]}
  ```
  规则：
  - `blocking` = 状态为 `fail` 或 `manual_unacknowledged` 的项 id；`env == "production"` 时
    `skip` 也算 blocking（生产不允许跳过）；
  - `manual` = 状态为 `manual_unacknowledged` 的项 id；
  - `release_ok = (not blocking) and gate_report.get("verdict") != "no_go"`；
  - 入参不是 dict / 缺 `items` → `release_ok=False`、`verdict="no_go"`、`blocking=["gate_report_invalid"]`；
  - **红线**：只产出**发布**结论；**不得**据此对运行期做任何禁用（运行期只认 `isolation_view`）。

### 2.3 `T8` 发布工具接线 → `scripts/create_release.py`
- 新增 `--gate-report <path>`（生产门禁报告 JSON）；创建 Release（非 `--check`）**必须**先过
  `release_verdict`：`release_ok` 为假 → **拒绝创建**并打印 `blocking`；`--force` 可显式覆盖并
  打印告警。
- 必须保留既有口径：tag 必须存在（`repository/tags`、`不得隐式创建 tag`）、tag 名 `vMAJOR.MINOR.PATCH`；
  `--check` 行为不变（不创建）。

### 2.4 `T17` 共用同一解析服务（守卫，代码已实现，只钉住）
- `cpq_quick_quote_file.PARSE_PATH == unified_parse.SERVICE_PATH`、
  `cpq_quick_quote_file.CAPABILITY_PATH == unified_parse.CAPABILITY_PATH`（防两套地址漂移）；
- `cpq_quick_quote_file` 源码**不得**出现 `ezdxf` / `ODAFileConverter`（报价侧不自己解析 DWG）；
- `unified_parse.capability(deps=...)` 探测抛错**不抛异常**（回 `dwg=False` + `detail`）；
  探测成功必须把 `provider` / `provider_version` 转述出来。

## 3. 不做什么

- 不改 `cad_converter` 的 `available = 主 or 回退` 口径、不改稳定错误码闭集；
- 不做第二套 DWG 解析（不装第二个 ODA、不在报价侧 import `ezdxf`）；
- 不在 CI 里加部署（`.gitlab-ci.yml` 仍禁止 deploy；发布门禁放 Release 工具，不放 CI）；
- 不把运行时转换器故障升级成登录/历史报价/文字快速报价的停机；
- 不改 `da_process_routing.py`、不动历史数据。

## 4. 允许修改范围

1. 新增 `tech_app/backend/services/capability_isolation.py`；
2. `scripts/create_release.py` 的发布门禁接线（保留既有 tag 口径）；
3. （守卫项不需要改代码）`unified_parse.py` / `cpq_quick_quote_file.py` 本批只验收。

## 5. 禁止事项

- 不许改 `tests/` 下任何既有文件（含本批红测与 `test_repository_workflow_contract.py`）；
- 不许在 CI 中引入 deploy / 重启服务器 / 写生产；
- 不许把 `release_verdict` 的结论用于运行期禁用；
- 不许让 `isolation_view` 或能力查询抛异常（能力查询绝不许 500）；
- 不许 commit / push / tag / Release / 部署。

## 6. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_capability_isolation_and_shared_parse_batch5_red -v
./open-claude/.venv/bin/python -m unittest tests.test_repository_workflow_contract -v      # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_dwg_file_capability_preflight_red -v # 不回归
```

## 7. 验收清单（本批不做、只登记）

- [ ] `T17` 条件触发：在 34 上真查 `/api/file/parse/capability`，**记录** `dwg` 真假，
      再决定要不要把「统一解析服务上线」前移；可用则不另起第二套；
- [ ] 生产门禁那 2 个 manual 项（转换器许可 `O2`、真实样本签字 `O3`）由谁签，与 Release 门禁一并定；
- [ ] `--gate-report` 的默认来源（由部署脚本产出还是人工附上）由你拍板。

## 8. 实现记录（2026-10-09，Codex）

按 §2 契约逐条落地，只动 §4 允许范围：

- 新增 `tech_app/backend/services/capability_isolation.py`（纯函数、绝不抛异常）：
  · `isolation_view(capability)`：两种形状都认（`available`/`preview_available`/`stable_error_code`
    与 `dwg`/`preview`/`detail`）；`disabled_reason` 取 `stable_error_code → error_code → detail →
    "DWG_PARSER_DISABLED"`；脏入参（`None`/字符串/`{}`/`{"available": None}`）一律按 disabled 处理；
    `documents` **恒为 enabled**（红线）。
  · `release_verdict(gate_report)`：`blocking` = `fail` + `manual_unacknowledged`（`env=="production"`
    时 `skip` 也计）；`manual` = `manual_unacknowledged`；`release_ok = 无 blocking 且报告 verdict != no_go`；
    报告非法 → `no_go` + `["gate_report_invalid"]`；只产出发布结论（红线：不用于运行期禁用）。
- `scripts/create_release.py`：新增 `--gate-report <path>` 与 `--force`，新增 `require_release_gate()`
  在**创建**（非 `--check`）前消费 `release_verdict`；未过 → 打印 blocking 并拒绝创建，`--force`
  显式覆盖并打印告警。既有 tag 口径（`repository/tags`、`不得隐式创建 tag`、`vMAJOR.MINOR.PATCH`）逐字保留。
- T17 只验收（未改代码）：`unified_parse.SERVICE_PATH`/`CAPABILITY_PATH` 仍是唯一事实源，报价侧
  `cpq_quick_quote_file` 不含 `ezdxf`/`ODAFileConverter`，探测抛错回 `dwg=False` + `detail`。

实跑（2026-10-09，`./open-claude/.venv/bin/python -W ignore -m unittest`）：

- `tests.test_capability_isolation_and_shared_parse_batch5_red`：改前 `Ran 12 … FAILED (failures=8)`，
  改后 **`Ran 12 … OK`**（T8a–T8h 八条转绿，T17 四条守卫维持绿）；
- Spec §6 不回归：`test_repository_workflow_contract + test_dwg_file_capability_preflight_red`
  → `Ran 38 … OK`；
- 离线核验 `require_release_gate`：go 报告放行；no_go 报告在无 `--force` 时 `SystemExit` 拒绝、
  带 `--force` 打印告警后放行；缺报告 → `gate_report_invalid` 拒绝。

§7 三项（34 上真查 `/api/file/parse/capability` 并记录、两个 manual 项签字人、`--gate-report`
默认来源）仍留给后续批次/人工。

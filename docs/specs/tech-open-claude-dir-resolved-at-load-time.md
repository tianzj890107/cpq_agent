# open-claude 目录必须在**装载时**解析，不能在导入时冻结（同进程里「谁先 `import` 本模块」不许决定路由有没有登记）

血缘：`tests/test_tech_agent_provider_readiness_dynamic.py`（§4 的 cpq_local 运行时登记）、
`tests/test_tech_agent_loopback_gateway_proxy_bypass_red.py`（同样先设 `OPEN_CLAUDE_DIR` 再导入）、
`tech_app_launch.py`（部署在**进程启动前**把 `OPEN_CLAUDE_DIR` 写进环境）。

- 状态：Spec + 红测（已实现）（落地见 §6）
- 红测：`tests/test_oc_agent_open_claude_dir_resolved_at_load_red.py`
- 唯一落点：`tech_app/backend/services/oc_agent.py`

## 0. 一句话目标

`oc_agent._ensure_path()` 每次调用都**重新读一次** `OPEN_CLAUDE_DIR`：真实部署在进程启动前就设好了这个变量，
但在同一个解释器里（测试进程、长驻服务里的后设环境），**谁先导入 `tech_app.backend.services.oc_agent`**
不该决定「路由同步有没有真的登记 provider」。

## 1. 现状缺口（本机实测，代码级可指到行；不是推断）

- `tech_app/backend/services/oc_agent.py:42`
  `OPEN_CLAUDE_DIR = Path(os.getenv("OPEN_CLAUDE_DIR", ROOT_DIR / "open-claude"))` —— **导入时**求值一次；
  `ROOT_DIR` 是 `tech_app/`，所以环境变量没设时默认值是 `tech_app/open-claude`（**本仓库不存在**这个目录）。
- `oc_agent.py:167-172 _ensure_path()` 读的就是那个常量：目录不存在 → `raise AgentUnavailable(...)`。
- `oc_agent.py:294-311 _register_runtime_provider()` 把 `_ensure_path()` + `from open_claude import config`
  包在 `try/except Exception: return` 里（`:307-310`，注释写明"环境相关"）—— 于是上面的异常被**静默吞掉**：
  `sync_route_environment()` 照旧写 `CLAUDE_MODEL` / Key / `<PROVIDER>_BASE_URL`，唯独 `_MODEL_PROVIDERS[model]`
  没写 → `oc_config.get_model_provider("cpq-local-7b")` 回落到 `anthropic`，请求会发错厂商，而且**没有任何报错**。
- 复现（本机实跑，两次只差导入顺序）：

  ```text
  ./open-claude/.venv/bin/python -m unittest tests.test_tech_agent_provider_readiness_dynamic
  # Ran 2 … OK（模块层 _load_services() 先 setdefault OPEN_CLAUDE_DIR，再导入 oc_agent）

  ./open-claude/.venv/bin/python -m unittest \
      tests.test_packaging_authority_workbook_upload_red tests.test_tech_agent_provider_readiness_dynamic
  # Ran 29 … FAILED (failures=1)
  #   AssertionError: 'anthropic' != 'cpq_local'
  # 后者在模块层 `from tech_app.backend import main`（此时 OPEN_CLAUDE_DIR 还没设）→ oc_agent 常量冻结在
  # `tech_app/open-claude`；随后的用例再怎么设环境变量都救不回来。
  ```

  该模块是全量扫描里**第一个**导入 `main`（因而导入 `oc_agent`）的文件，所以整仓全量跑才会出现这条；
  33 个测试文件在模块层导入 `main`，顺序一变就换人踩。

## 2. 口径（逐条，可直接验收）

1. **C1 每次装载重新解析**：`_ensure_path()` 先读 `os.environ["OPEN_CLAUDE_DIR"]`（`strip()`；空串按"未设"处理），
   没有再落到 `ROOT_DIR / "open-claude"`。
2. **C2 缺目录的契约逐字不变**：目录不存在时仍 `raise AgentUnavailable("未找到 open-claude 目录：<解析出来的那个>")`。
3. **C3 静默兜底不改**：`_register_runtime_provider()` 在 open-claude 真的找不到时**仍然不抛**（不许把
   "同步一次路由"变成可能失败的写操作）；C1 之后"先导入、后设环境变量"必须能登记成功。
4. **C4 兼容**：模块属性 `oc_agent.OPEN_CLAUDE_DIR` 保留（外部/历史引用不破），但 `_ensure_path()` 不再读它。
5. **C5 `sync_route_environment()` 的既有搬运行为一行不改**：`CLAUDE_MODEL`、provider Key、`<PROVIDER>_BASE_URL`
   的 `setdefault`（不覆盖运维已配的业务空间域名）、`_exclude_loopback_from_proxy()`。

## 3. 红测分组（`tests/test_oc_agent_open_claude_dir_resolved_at_load_red.py`）

- **A 组（今天必须红）**
  - A1「先导入、后设环境变量」：`OPEN_CLAUDE_DIR` **未设**时导入 `oc_agent`，之后设成真实目录 →
    `_ensure_path()` 不许抛，且真实目录要进 `sys.path`；
  - A2 同一姿势下 `sync_route_environment({"provider": "cpq_local", "model": "cpq-local-7b", …})` →
    `oc_config.get_model_provider("cpq-local-7b") == "cpq_local"`（今天回落到 `anthropic`，静默错发）；
- **B 组（今天就是绿的护栏，不许被改红）**
  - B1 「先设环境变量、再导入」照旧可用（部署与现有测试的姿势）；
  - B2 目录不存在 → 仍 `AgentUnavailable`，且文案里带那个目录；
  - B3 目录不存在时 `sync_route_environment()` **不许抛**（C3 的静默兜底）；
  - B4 `oc_agent.OPEN_CLAUDE_DIR` 属性仍在（C4 兼容）。

A/B 两组都在**子进程**里跑（导入顺序是这两条用例的自变量，同进程内没法重来）；
全部离线：不连 PG、不发 HTTP、不调模型、不写业务数据。

## 4. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_oc_agent_open_claude_dir_resolved_at_load_red -v   # A1/A2 红 → 全绿
./open-claude/.venv/bin/python -m unittest tests.test_tech_agent_provider_readiness_dynamic \
    tests.test_tech_agent_provider_readiness_red tests.test_tech_agent_loopback_gateway_proxy_bypass_red
./open-claude/.venv/bin/python -m unittest \
    tests.test_packaging_authority_workbook_upload_red tests.test_tech_agent_provider_readiness_dynamic  # 修复后必须 OK
```

## 5. 本批不做

- 不改 `tech_app_launch.py` / 部署脚本（部署本来就在启动前设好这个变量）；
- 不改 `llm_settings` / `open_claude` 包内任何东西（open-claude 是编译产物，且本批不动它）；
- 不给 `_register_runtime_provider()` 新增报错/日志（C3：静默兜底是既有设计，本批只修"解析时机"）；
- 不改任何测试的期望值 / 断言。

## 6. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 469`）

```
./open-claude/.venv/bin/python -m unittest tests.test_oc_agent_open_claude_dir_resolved_at_load_red
# 实现前：Ran 7 tests … FAILED (failures=3)   ← A1/A2/A3
# 实现后：Ran 7 tests … OK
```

### 6.1 落点（只 1 个文件：`tech_app/backend/services/oc_agent.py`）

| 契约 | 落点 |
| --- | --- |
| §2.1 C1 装载时解析 | 新增纯函数 `open_claude_dir()`：`raw = str(os.getenv("OPEN_CLAUDE_DIR") or "").strip()`，非空用 `Path(raw)`，否则 `ROOT_DIR / "open-claude"`；`_ensure_path()` 每次调用它 |
| §2.2 C2 缺目录契约 | `_ensure_path()` 仍是 `raise AgentUnavailable(f"未找到 open-claude 目录：{directory}")`，只是 `directory` 换成**本次解析出来**的那个（A3：文案里的目录不再是导入时冻结的默认值） |
| §2.3 C3 静默兜底 | `_register_runtime_provider()` 的 `try/except Exception: return` 一行未动（B3 仍绿：open-claude 真找不到时 `sync_route_environment()` 不抛） |
| §2.4 C4 兼容 | 模块常量 `OPEN_CLAUDE_DIR` 保留（加了注释说明它只是兼容读数）；`_ensure_path()` 不再读它 |
| §2.5 C5 搬运行为 | `sync_route_environment()` 一行未改：`CLAUDE_MODEL`、Key、`<PROVIDER>_BASE_URL` 的 `setdefault`、`_exclude_loopback_from_proxy()` 全部照旧（A2/B1 断言其在位） |

### 6.2 复跑（不回归）

```
./open-claude/.venv/bin/python -m unittest     tests.test_packaging_authority_workbook_upload_red tests.test_tech_agent_provider_readiness_dynamic
# Ran 29 … OK（实现前：Ran 29 … FAILED (failures=1)「'anthropic' != 'cpq_local'」—— 就是 §1 那条复现）

./open-claude/.venv/bin/python -W ignore -m unittest     tests.test_tech_agent_provider_readiness_dynamic tests.test_tech_agent_provider_readiness_red     tests.test_tech_agent_loopback_gateway_proxy_bypass_red tests.test_tech_backend_get_route_smoke_dynamic     tests.test_tech_base_geometry_fallback_red tests.test_tech_home_timeline_and_publish_closure_red     tests.test_task_process_detail_red
# Ran 102 … OK
```

### 6.3 说明

- 本批**只改 1 个生产文件**（`oc_agent.py`，`_ensure_path` 的解析时机 + 一个新的纯函数）+ 本份 Spec +
  本批红测 + changelog；未改任何既有测试的期望值 / 断言，未新增 skip。
- 部署侧不需要任何动作：真实进程由 `tech_app_launch.py` 在启动前设好 `OPEN_CLAUDE_DIR`，
  本批对"环境变量已设"的路径是**行为等价**的（B1 守着这条）。

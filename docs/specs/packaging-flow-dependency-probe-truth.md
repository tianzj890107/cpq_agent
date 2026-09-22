# 规格：依赖自检不许说"就绪"而真跑说"缺失"，导入失败也不许被永久缓存

状态：Spec + 红测（已实现）（原状：`capability()` 用 `find_spec` 判"依赖是否可用" —— 那只说明**文件在不在**；
模块内部导入失败（缺子依赖 / 语法错误 / 环境缺件）时它照旧说"编排层已就绪"，
而真跑那条链路第 3 步就 `unavailable`；并且 `_dependency()` 把**导入失败也写进缓存**，
一次失败在这个进程里就永远"依赖缺失"，没有重试入口）
红测：`tests/test_packaging_flow_dependency_probe_truth_red.py`

血缘：承接 `dwg-semantics-agent-flow.md` §2.2（依赖只经一个缝 + `capability()["dependencies"]`
不许粉饰；红测 `I1` 用 `find_spec` 当判据 —— **本批不动那条口径**，只补"导入真的能不能成"这一层）、
`packaging-silent-degradation-disclosure.md`（失败 / 空 / 没有不许同形，本批是**依赖缝**这一处）、
`drawing-flow-error-taxonomy.md`（`field_write` 的粗错误码已经分家；本批分的是"依赖为什么不可用"）、
`packaging-cost-and-handoff-static-downgrade-disclosure.md`（同一病症在成本 / 交接侧）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

`GET …/drawing-flow/capability` 是部署自检入口，它必须回答的是
**"这条链路现在真的跑得起来吗"**，不是"文件在不在"；而当某一步真的报
`PACKAGING_FLOW_DEPENDENCY_MISSING` 时，用户与管理员必须能看出是
**"这个部署没有它"（missing）** 还是 **"它装载失败了（import_failed + 异常类名）"** ——
今天的两种情形在返回体上逐字相同，且失败会被缓存到进程结束。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/backend/services/packaging_drawing_flow/__init__.py`：

```python
51  _CACHE: Dict[str, Any] = {}
54  def _dependency(name: str) -> Optional[Any]:
55      """依赖缝：解析不到就返回 None（不许把 AttributeError 抛给用户）。"""
57      if key in _CACHE:
58          return _CACHE[key]
59      path = _MODULE_PATHS.get(key)
60      module = None
61      if path:
62          try:
63              module = importlib.import_module(path)
64          except Exception:                 # ← 真因被吞
65              module = None
66      _CACHE[key] = module                  # ← **失败也进缓存**
67      return module

76  def _available(name: str) -> bool:
77      path = _MODULE_PATHS.get(str(name or ""))
80      try:
81          return importlib.util.find_spec(path) is not None   # ← 只证明"文件在"
82      except Exception:
83          return False

86  def capability() -> Dict[str, Any]:
87      dependencies = {name: _available(name) for name in model.DEPENDENCIES}
88      missing = [name for name in ("file_preflight", "cad_converter", "cad_ir",
89                                   "packaging_semantics") if not dependencies.get(name)]
90      return {"available": not missing, ...
```

三个问题：

1. **自检与真跑不一致**：`find_spec` 只看得到模块文件；`import tech_app.backend.services.cad_ir`
   因为**它自己**的 `import`（子依赖缺失、环境缺件、语法错误）而失败时，
   `find_spec` 仍返回非 `None` → `capability()["available"] is True`、"编排层已就绪"，
   而 `run_flow()` 到 `cad_ir_parse` 直接 `unavailable(PACKAGING_FLOW_DEPENDENCY_MISSING)`。
   这条"自检说能跑、真跑说不能"正是现场排查最费时间的一类。
2. **真因被吞**：`except Exception: module = None` —— `ModuleNotFoundError: No module named 'ods'`
   与"这个部署里没有 cad_ir 这个缝"在返回体上逐字相同（`detail.dependency` 只给名字）。
3. **失败被永久缓存**：`_CACHE[key] = module` 连 `None` 一起缓存，且 `_CACHE` 没有失效入口 ——
   运维装好依赖 / 热修模块文件之后，**同一进程**里所有后续请求仍然报"依赖缺失"，
   只有重启才恢复；而返回值里没有任何字段能提示"这是缓存里的旧结论"。

## 2. 契约

### 2.1 依赖状态登记（唯一一处，`model.py`）

`tech_app/backend/services/packaging_drawing_flow/model.py` 新增：

```python
DEPENDENCY_STATES = ("ok", "missing", "import_failed", "unknown")

def note_dependency_state(name, state, *, reason="", message="") -> dict   # 写一处，返回该条
def dependency_state(name="") -> dict
#   name 给了 → {"name", "state", "reason", "message"}
#   name 空   → {name: {...}}（按名字升序）
#: 登记表必须是模块级真 dict（测试要能 clear），键为依赖名。
DEPENDENCY_STATE_REGISTRY: Dict[str, Dict[str, Any]] = {}
```

- `state` 是**闭集**；`reason` 是异常类名（`missing` / `ok` 给 `""`）；
- `message` 给异常文案的前 200 字（`missing` / `ok` 给 `""`）—— 排障要能直接看到
  `No module named 'ods'` 这种原文；
- 没有登记过 → `{"name", "state": "unknown", "reason": "", "message": ""}`
  （**"没登记过" ≠ "没有这个依赖"**，不许编成 `missing`）。

### 2.2 `_dependency()` 只缓存成功，失败必须登记

`packaging_drawing_flow/__init__.py`：

- **失败不许进 `_CACHE`**：`importlib.import_module` 抛异常时不得写 `_CACHE[key]`
  （下一次调用必须**重新尝试导入**；`_CACHE` 只保存解析成功的模块）；
- `_MODULE_PATHS` 里没有这个名字 → 登记 `missing`；
- `import_module` 抛异常 → 登记 `import_failed` + `reason=异常类名` + `message=str(exc)[:200]`；
- 成功 → 登记 `ok`（并照旧进缓存）；
- `_dependency()` 的返回口径**逐字不变**（仍是 `Optional[Any]`，拿不到仍是 `None`，
  仍是"不把异常抛给用户"）；
- 对外暴露 `dependency_state(name="")`（`= model.dependency_state`），与其它 `model` 常量同一处方言。

### 2.3 `capability()` 必须报"真的能导入吗"

- **新增键（键必须存在）** `dependencies_state`：逐名 `dependency_state(name)` 的登记体；
- **必需四项**（`file_preflight` / `cad_converter` / `cad_ir` / `packaging_semantics`）
  的 `ok` 判定必须来自**真的导入**（`_dependency(name) is not None`），
  **不许**只靠 `find_spec`：文件在但导入失败 → 该名字的 `dependencies_state[name].state == "import_failed"`，
  且 `available is False`、`message` 里点它的名字；
- `message` 文案分家：`missing` → 今天的"依赖的能力尚未就绪：…"；出现 `import_failed` →
  "依赖装载失败：<名字>（<异常类名>），请查看服务日志后重启服务"（**不许**把导入失败说成"尚未就绪"）；
- 既有 `dependencies`（逐名 `bool`，`find_spec` 口径）**逐字不变**（红测 `I1` 钉着）；
  既有键 `available` / `version` / `steps` / `message` 的位置与类型不变（`message` 是上面的分家文案）。

### 2.4 步骤报"依赖缺失"时要带上状态

`packaging_drawing_flow/steps.py`：

- `_resolve(ctx, name)` 的 resolver 抛异常时（它今天 `except Exception: return None`），
  必须登记 `import_failed` + 异常类名 + 文案（走的可能是外部注入的 resolver，所以**不能只靠
  `_dependency()` 那边的登记**）；resolver 返回 `None` → 登记 `missing`；
- `_unavailable(name)` 的 `detail` **新增键（键必须存在）** `dependency_state`（该名字的 `state`）
  与 `reason`（异常类名，没有给 `""`）；
- `status` / `error_code`（仍是 `PACKAGING_FLOW_DEPENDENCY_MISSING`）/ `retryable` /
  `detail.dependency` / `detail.http_status` 与文案**逐字不变**（`import_failed` 时文案可加一句
  "装载失败"，但"依赖的能力尚未就绪"这句在 `missing` / `unknown` 时必须逐字保留）。

## 3. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_drawing_flow/model.py`
   - `DEPENDENCY_STATES` / `DEPENDENCY_STATE_REGISTRY` / `note_dependency_state()` / `dependency_state()`。
2. `tech_app/backend/services/packaging_drawing_flow/__init__.py`
   - `_dependency()`：失败不写缓存 + 登记状态；
   - `_available()` 保持（`find_spec` 口径不动）；
   - `capability()`：新增 `dependencies_state`，必需四项改走真导入，`available` / `message` 按 §2.3 分家；
   - 导出 `dependency_state = model.dependency_state`。
3. `tech_app/backend/services/packaging_drawing_flow/steps.py`
   - `_resolve()` 登记失败；`_unavailable()` 的 detail 加 `dependency_state` / `reason`。
4. `tech_app/backend/main.py`
   - `GET …/drawing-flow/capability` 与步骤读接口原样带出新增键（不改路由形状）。

## 4. 禁止事项

- 不许改 `_dependency()` 的对外形状：仍是 `Optional[Any]`、仍不抛异常、仍可被
  `run_flow(..., deps=...)` 的 dict 依赖缝替换（既有红测大量依赖这个缝）。
- 不许改 `capability()["dependencies"]` 的 `bool` 口径（`find_spec`；`I1` 钉着），
  也不许删既有键、改既有键的类型。
- 不许把"没登记过"折成 `missing` / `import_failed`；不许把 `import_failed` 折成 `missing`
  （方向相反也不行：`missing` 会让人去"装依赖"，而真相是"它装了但装载失败"）。
- 不许为了自检去**捕获后静默**：`capability()` 允许真的导入必需四项（模块导入的既有副作用不变），
  但**不许**在导入失败时把异常吞成 `available: True`。
- 不许把失败重试做成"每次请求都重新 import 全部依赖"：只有 `_CACHE` 里**没有**成功结果时才尝试。
- 不许改 `tests/` 下任何既有文件（含本批红测与 `test_packaging_drawing_flow_red.py` 的 `I1`）。
- 不许连线上 PG / 生产库跑测试、不许发 HTTP、不许写业务数据；本批红测全部离线
  （打桩 `importlib` + 清 `_CACHE`，不连 34、不跑真 DWG 转换）。

## 5. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_flow_dependency_probe_truth_red -v
# A 组（3 条）：导入失败不许进缓存（第二次必须重新尝试）/ 成功仍走缓存（护栏）
#              / 失败必须登记 import_failed + 异常类名 + 原文前 200 字
# B 组（2 条）：capability().dependencies 的 bool 口径不变（护栏）
#              / 文件在但导入失败 → dependencies_state=import_failed、available=False、
#                message 说"装载失败"且点名
# C 组（2 条）：_unavailable() 的 detail 带 dependency_state / reason，既有码与文案不变
#              / 没登记过时状态是 unknown，不许编 missing（护栏）
# D 组（1 条）：resolver 抛异常时 cad_ir_parse() 返回的 detail 也是 import_failed
# 现状：A1 A3 B2 C1 D1 红（5 条），A2 B1 C2 绿（3 条护栏）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red
# 不回归（依赖缝与七步编排的既有口径）
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_error_taxonomy_red
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_parse_terminal_signal_red
```

真机复验（实现方做完、且部署后）：

```
GET  /api/projects/{pid}/drawing-flow/capability
     # 依赖文件在、但导入失败时 available=false + dependencies_state.<name>.state=import_failed
POST /api/projects/{pid}/drawing-flow/run
     # 对应步骤 detail 带 dependency_state / reason；仍不把异常抛给用户
```

## 6. 落地状态

（已实现）（`packaging-flow-dependency-probe-truth` 9-22 落地。`_dependency()` 失败不再进缓存、
并登记 `missing` / `import_failed`（异常类名 + 原文前 200 字）；`capability()` 新增
`dependencies_state`、必需四项改走**真的导入**、`message` 按"装载失败 / 尚未就绪"分家；
步骤的 `_unavailable()` detail 新增 `dependency_state` / `reason`，`_resolve()` 也登记失败。
A 组 3 条 + B2 + C1 + D1 全绿，A2 / B1 / C2 三条护栏仍绿。）

### 6.1 落点

| 契约 | 落点 |
| --- | --- |
| §2.1 状态登记 | `packaging_drawing_flow/model.py`：`DEPENDENCY_STATES = ("ok","missing","import_failed","unknown")`、模块级真 dict `DEPENDENCY_STATE_REGISTRY`、`note_dependency_state(name, state, *, reason="", message="")`（越界折 `unknown`、`message` 截 200 字）、`dependency_state(name="")`（没登记 → `unknown`；不给名字 → 全表按名字升序） |
| §2.2 `_dependency()` | `packaging_drawing_flow/__init__.py`：没有这条缝 → 登记 `missing`；`import_module` 抛异常 → 登记 `import_failed` + 异常类名 + 原文，**不写 `_CACHE`**（下次重新尝试）；成功 → 登记 `ok` 并进缓存。返回口径仍是 `Optional[Any]`、仍不抛异常 |
| §2.3 `capability()` | 新增 `dependencies_state`（逐名登记体）；必需四项 `REQUIRED_DEPENDENCIES` 改走 `_dependency(name) is not None`（真导入）；`available = not unresolved`；`message`：有 `import_failed` → 「依赖装载失败：<名字>（<异常类名>），请查看服务日志后重启服务」，否则 `missing` → 原「依赖的能力尚未就绪：…」，否则原「编排层已就绪」。既有 `dependencies`（`find_spec` 口径）逐字不变 |
| §2.4 步骤 | `steps.py`：`_resolve()` 抛异常 → 登记 `import_failed`（含外部注入 resolver）、返回 `None` → 登记 `missing`；`_unavailable()` 的 `detail` 新增 `dependency_state` / `reason`，状态码 / 错误码 / `retryable` / `detail.dependency` / `http_status` 与文案形状不变（`import_failed` 时文案点出"装载失败（<类名>）"） |
| §2.1 导出 | `packaging_drawing_flow.dependency_state = model.dependency_state` |

### 6.2 复跑

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_flow_dependency_probe_truth_red
# Ran 8 tests ... OK
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_drawing_flow_red \
    tests.test_drawing_flow_error_taxonomy_red tests.test_drawing_flow_parse_terminal_signal_red
# Ran 98 tests ... OK (skipped=1)
```

### 6.3 已记录的边界

- Spec §3.4 提到「`GET …/drawing-flow/capability` 原样带出新增键」：**这个路由目前不存在**
  （图纸链路的入口只有 `GET /api/projects/{pid}/drawing-flow` 与 `POST …/drawing-flow/run`；
  前端问转换能力走的是 `/api/capabilities/cad-converter`）。因此本批**没有**改 `main.py`；
  `flow.capability()` 的新键在进程内可直接读，步骤的 `detail` 两个新键随既有读路由自动带出。
  等那条路由真的建起来时，它照旧"原样交出去"即可，不需要再改服务层。
- 本批只登记状态、不改判定：`_available()` 的 `find_spec` 口径、`dependencies` 的 `bool` 值、
  `PACKAGING_FLOW_DEPENDENCY_MISSING` 的码与状态都没动。

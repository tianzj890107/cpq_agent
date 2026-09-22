# 规格：`stage_chain` 里"这一段读不到"不许显示成"这一段还没做"

状态：Spec + 红测（已实现）（原状：`anchor._load()` / `_load_list()`（`:195`/`:211`）把所有异常吞成 `{}` / `[]`，
`stage_chain()`（`:221`）于是给这一段的 `value: ""` + `status: "none"` —— 与"这段业务上确实还没做"
逐字相同；`result_version_of(cost)` 抛异常（`:238`）同样退成 `""`）
红测：`tests/test_packaging_stage_chain_read_failure_red.py`

血缘：承接 `dwg-semantics-agent-flow.md` §6.1（`stage_chain` 是"上一段的版本必须传进下一段"的
唯一载体：盒型 → BOM → 路线 → 成本 → 报价草稿；本批不动它的键与顺序，只补"读不到"这一态）、
`packaging-silent-degradation-disclosure.md`（同一条纪律：失败 / 空 / 没有不许同形）、
`packaging-cost-and-handoff-static-downgrade-disclosure.md` §2.4（`inheritance()` **自己**抛异常时
交接侧的披露；本批管的是**链条里某一段**读不到，比它细一层，两者都要到位）、
`packaging-cost-route-version-read-failure.md`（同一病症在成本读侧的投影）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

`GET /api/projects/{pid}/drawing-flow` 返回的 `inheritance.stage_chain` 是用户看"这条链走到哪了"的
唯一依据（盒型 → BOM → 路线 → 成本）。今天 BOM / 路线 / 成本服务只要有一次读取异常，
这一段的 `status` 就变成 `"none"`、`value` 变成 `""` —— 用户读到的结论是**"这一段还没做"**，
于是去重跑下游步骤；而真相是"这一段读不到"（重跑不会让它变好）。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/backend/services/packaging_drawing_flow/anchor.py`：

```python
195 def _load(resolve, name, function, project_id, requirement_no=""):
196     module = _engine(resolve, name)
197     fn = getattr(module, function, None) if module is not None else None
198     if not callable(fn):
199         return {}                       # ← 「这个部署没有这一段」
...
204     except Exception:
205         return {}                       # ← 「这段读挂了」—— 与上一处同形
211 def _load_list(resolve, name, function, project_id):
...
216     except Exception:
217         return []                       # ← 同上
221 def stage_chain(project_id, resolve=None, *, stage=""):
222     box = _load(resolve, "packaging_match", "load_box_match", project_id)
223     bom = _load(resolve, "packaging_bom", "load_bom", project_id)
224     route = _load(resolve, "packaging_route", "load_route", project_id)
225     versions = _load_list(resolve, "packaging_route", "route_versions", project_id)
226     cost = _load(resolve, "packaging_cost", "load_cost", project_id)
...
238     if callable(result_version_of) and cost:
239         try:
240             result_version = str(result_version_of(cost))
241         except Exception:
242             result_version = ""             # ← 「算版本时挂了」与「成本算出版本号是空」同形
```

三个后果（都在同一条链上）：

1. **`status: "none"` 有两义**：`{"stage": "bom", "value": "", "status": "none"}` 今天既表示
   "BOM 还没生成"（`bom.get("built")` 假），也表示"读 BOM 抛了异常"。用户只能按前者理解。
2. **`route` 段的 `value` 一起丢**：`versions` 读不到时 `version_row = {}`，`value` 给 `""` ——
   "路线版本读不到"看起来就是"没有路线版本"（与 `packaging-cost-route-version-read-failure.md`
   是同一个洞在链条上的投影）。
3. **三种状态（读到了 / 确实没做 / 读不到）在返回体上两两不可分**：
   `grep -rn "stage_chain" tech_app/` 里没有任何一个字段能回答"这一段是没做还是没读到"。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_drawing_flow/anchor.py`
   - `_load()` / `_load_list()` 的返回值必须能区分三态，**不许**再让 `{}` / `[]` 同时表示
     "模块或函数没装"与"调用抛异常"。实现形状不限（例如返回 `(row, source, reason)`，
     或另加一对私有函数），但必须满足下面全部要求：
   - `stage_chain()` 的**每一行**新增两个必存在键：
     - `source` ∈ `{"engine", "absent", "unavailable"}`：
       - `"engine"`：这一段需要的调用**全部成功**（结果为 `{}` / `[]` / 空值也算成功，
         "业务上真的没做"走这一态）；
       - `"absent"`：这一段依赖的**模块或函数不存在**（`resolve` 拿不到模块、
         `getattr` 拿不到可调用函数，含 `result_version_of` 这类可选函数不存在）；
       - `"unavailable"`：函数存在但**调用抛异常**（`route` 段以先抛出的那个入口为准，
         `load_route` 与 `route_versions` 任一抛异常都算这一段 `unavailable`）；
     - `unavailable`：`source == "unavailable"` → `{"code": "stage_chain_stage_unavailable",
       "reason": "<异常类名>"}`；其余两态 → `{}`（键必须存在）。
   - 既有键 `stage` / `value` / `status` / `engine_version` / `confirmed_by` / `confirmed_at`
     的**值与口径逐字不变**（`unavailable` / `absent` 时 `status` 仍是 `"none"`、
     `value` 仍是 `""`，`result_version` 仍**不许**编一个版本）。行的数量与顺序不变。
   - `inheritance()` 结果**新增必存在键** `stage_chain_unavailable`：
     - 全部 `source == "engine"` → `{}`；
     - 有非 `engine` 的段 →
       `{"code": "stage_chain_stage_unavailable", "stages": {<段名>: {"source": <该行的 source>,
       "reason": <该行 unavailable.reason 或 "">}}}`，`stages` 的键**按链条顺序**
       （`box_match` / `bom` / `route` / `cost`）且只含非 `engine` 的段；
     - `source_versions.stage_chain` 与顶层 `stage_chain` 仍是同一份（逐字不变）。
2. 读接口（`main.py:7386 GET /api/projects/{pid}/drawing-flow`）原样带出新键
   （不改路由形状、不换键名、不新增路由）。

## 3. 禁止事项

- 不许把 `status` 从 `"none"` 改成别的值、不许给 `value` 编一个"看起来对"的版本、
  不许把 `source != "engine"` 的段从链里删掉（链的**形状**逐字不变，只加披露）。
- 不许让读取异常抛给调用方：`GET /drawing-flow` 照旧 200，链照旧返回四段。
- 不许改 `gates.build()` / `_stage_entry()` 的门禁结论（`blocked` / `open` 逐字不变）——
  本批只碰 `stage_chain` 与 `inheritance`。
- 不许改 `flow_state()` / `stale_view()` / `preconditions()` 的返回体。
- 不许改 `tests/` 下任何既有文件（含 `test_packaging_drawing_flow_red.py` 的 C9/C10/C11 三条
  版本传递用例、`test_packaging_quote_close_loop_red.py`）；本批红测是新增文件。
- 不许连线上 PG / SQLite 生产库、不许发 HTTP、不许写业务数据；本批红测全部离线
  （假依赖 + 纯函数，不建项目、不写盘）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_stage_chain_read_failure_red -v
# P 组（10 条）：
#   P1 BOM 读抛异常 → 该行 source="unavailable"、unavailable.code=stage_chain_stage_unavailable、
#      reason 带异常类名；且 value/status 逐字不变（红）
#   P2 路线两个入口任一抛异常 → route 行 source="unavailable"（红）
#   P3 result_version_of(cost) 抛异常 → cost 行 source="unavailable" 且 value 仍是 ""（红）
#   P4 四段全部读得到 → 每行 source="engine"、unavailable 给 {}（键必须存在）（红）
#   P5 模块 / 函数根本没装 → source="absent"、unavailable 给 {}（红）
#   P6 真的没做（BOM 读到但 built 假）→ 既有键逐字不变（护栏）
#   P7 inheritance() 全绿时 stage_chain_unavailable 必须是 {}（键必须存在）（红）
#   P8 inheritance() 有读不到时列出段名与原因，且键按链条顺序（红）
#   P9 inheritance() 既有键逐字不变（source_versions / stage_chain / 锚点字段）（护栏）
#   P10 依赖抛异常时 stage_chain 照旧不抛、四段照旧齐全（护栏）
# 现状：P1 P2 P3 P4 P5 P7 P8 红（7 条），P6 P9 P10 绿（3 条护栏）
# 不回归（链条与门禁的既有口径）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_close_loop_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_silent_degradation_red
```

真机复验（实现方做完、且部署后）：

```
GET /api/projects/{pid}/drawing-flow
# 正常：{"inheritance": {..., "stage_chain_unavailable": {},
#                        "stage_chain": [{"stage": "bom", "source": "engine", ...}, ...]}}
# BOM 服务异常时：{"inheritance": {..., "stage_chain_unavailable":
#                   {"code": "stage_chain_stage_unavailable",
#                    "stages": {"bom": {"source": "unavailable", "reason": "..."}}},
#                  "stage_chain": [..., {"stage": "bom", "status": "none",
#                                        "source": "unavailable", ...}]}}
```

## 5. 落地状态

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_stage_chain_read_failure_red
# Ran 10 tests ... OK（P1–P5 / P7 / P8 由红转绿；P6 / P9 / P10 三条护栏仍绿）
```

链条每一段现在带 `source`（`engine` / `absent` / `unavailable`）与 `unavailable` 披露键，
`inheritance()` 带 `stage_chain_unavailable`；既有键、链的形状、`status` / `value` 口径逐字不变。

### 5.1 落点

| 契约 | 落点 |
| --- | --- |
| §2.1 三态探测 | `tech_app/backend/services/packaging_drawing_flow/anchor.py`：新增模块常量 `STAGE_SOURCES = ("engine", "absent", "unavailable")` 与 `_probe()` / `_probe_list()`（返回 `(row, source, reason)`）；`_worse_source()` 合看两个入口（`unavailable` > `absent` > `engine`）；`_stage_disclosure()` 造 `{"code": "stage_chain_stage_unavailable", "reason": "<异常类名>"}` |
| §2.1 每行两个新键 | `stage_chain()` 四段（`box_match` / `bom` / `route` / `cost`）各加 `source` + `unavailable`（非 `unavailable` 给 `{}`，键必须存在） |
| §2.1 路线两入口 | `route` 段 = `load_route` 与 `route_versions` 两者较严重的那个；任一抛异常 → 该段 `unavailable` |
| §2.1 可选函数 | `result_version_of` 拉不到（`cost` 非空时）→ `cost` 段 `absent`；调用抛异常 → `unavailable`（`result_version` 仍给 `""`，绝不编） |
| §2.1 既有键不动 | `stage` / `value` / `status` / `engine_version` / `confirmed_by` / `confirmed_at` 计算与文案逐字未动；`{}`/`[]` 的"业务上真的没做"仍走 `engine` |
| §2.1 `_load` / `_load_list` | 保留原名与签名，改成 `_probe()` / `_probe_list()` 的薄封装（`unresolved_gaps()` 的行为逐字不变） |
| §2.1 `inheritance()` | 新增 `stage_chain_unavailable`：全 `engine` → `{}`；否则 `{"code": "stage_chain_stage_unavailable", "stages": {<段>: {"source", "reason"}}}`，键按链条顺序且只含非 `engine` 段 |
| §2.2 路由 | `main.py` 未改（`"inheritance": packaging_drawing_flow.inheritance(pid)`，新键自动带出） |

### 5.2 复跑（不回归）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_drawing_flow_red \
    tests.test_packaging_quote_close_loop_red tests.test_packaging_silent_degradation_red
# Ran 163 tests ... OK (skipped=1)
```

### 5.3 已记录的边界

- 既有的 `_load()` 在"第二次 `fn(project_id)` 也抛 TypeError"时会漏出去，新实现把这一支也收进
  `unavailable`（更保守，非回归）。
- `result_version_of` 不存在只在该段 `cost` 读到东西时报 `absent`；空成本时无可版本化，
  仍按 `engine`（避免把"业务上没算过"误报成"部署缺件"）。Spec §2.1 未细分这一组合，
  本批按保留既有口径实现。
- 只披露不重跑：链照旧返回四段、读接口照旧 200，不改门禁结论；未 push / 未建 MR / 未 tag /
  未部署 / 未连库 / 未写生产数据。

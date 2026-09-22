# 规格：成本口径 / 规则版本 / 上游路线 / 交接闸门 —— 「读不到」不许显示成「本来就没有」

状态：Spec + 红测（已实现）（原状：五处 `except Exception` 把失败折成空值：口径快照退成 `pending`、
规则版本与上游路线退成 `""`、交接闸门与口径整块退成 `{}` —— 读接口、落库包、报价侧全都分不出
「读挂了」与「真的是空」）
红测：`tests/test_packaging_cost_and_handoff_static_downgrade_red.py`

血缘：承接 `packaging-silent-degradation-disclosure.md`（同一病症的零件 / 人工映射 / 角色候选 / 配对复核
四处，本批是**成本与交接侧**的五处；那份 Spec 的 §2 契约与「留痕不改结论」纪律本批逐字沿用）、
`packaging-cost-input-version-pinning.md`（算的那一刻要记下上游版本 —— 本批管的是「记不下来」怎么自报）、
`packaging-cost-content-binding-source-disclosure.md`（同一病症在成本侧的第四处：绑定集合恒空集）、
`packaging-cost-minimum-charge-decision.md` / `packaging-cost-red-closure.md`（最低收费口径的裁决登记；
本批不动口径、只让「快照读不到」可判）、
`packaging-quote-send-recovery.md`（交接包是回传报价侧的凭据 —— 本批让它的闸门与版本六元组读不到时留痕）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

成本引擎与交接包上有五处 `except Exception`，**失败后的返回值与「业务上真的是空」逐字相同**：
最低收费口径退成 `pending`、规则快照版本与上游路线版本退成 `""`、交接包的闸门与口径退成 `{}`。
后果是用户（和报价侧）拿着"没有 / 未裁决 / 版本为空"去做决定，永远查不出真因。
本批只要求**留痕降级**：结论可以不变，但「我读不到」必须能在返回体上被读出来。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

### 1.1 最低收费口径快照读不到 → 与「业务还没裁决」同形

`tech_app/backend/services/packaging_cost.py:575 _load_minimum_charge_policy()`：

```python
    try:
        data = json.loads(Path(RULES_JSON_PATH).read_text(encoding="utf-8"))
        block = data.get("minimum_charge_policy") or {}
    except Exception:  # noqa: BLE001 - 快照不可用不该让成本计算整体失败
        block = {}
    if not isinstance(block, dict):
        block = {}
    status = _text(block.get("status")) or "pending"
```

`minimum_charge_policy()`（`:632`）随后把 `status == "pending"` 翻译成
`{"status": "pending", "policy": "unresolved", "fallback": "sheet_labor_rate"}`。
于是**快照文件丢了 / JSON 坏了 / 块被删了**与**业务确实还没裁决**产出同一个返回体：
调用方按 `policy == "unresolved"` 处理，看起来一切正常，没有一处能回答"是没裁决，还是根本没读到"。
（对照同一文件 `:982 resolve_formula()` 的做法：`base["note"] = "kb_unavailable:<异常类名>"` ——
仓库里已经有"留痕不静默"的现成范式，这三处是漏网。）

### 1.2 规则快照版本读不到 → 与「快照没有版本号」同形，且这个值会落库当审计凭据

`packaging_cost.py:944 rule_snapshot_version()`：

```python
    try:
        value = kb_repo.kb_version()
    except Exception:  # noqa: BLE001 - 快照不可用不该让成本计算整体失败
        return ""
    return _text(value)
```

`tech_app/backend/storage/kb_repo.py:55 kb_version() -> Optional[int]` 明确写着
「本地缓存的 `kb_version`;还没拉过快照时为 `None`」——**三态**（读挂了 / 从没拉过快照 / 版本号是空）
全部压成一个 `""`：

- 冷进程实测：`kb_repo.kb_version() -> None`、`rule_snapshot_version() -> ''`；
- 这个 `""` 会被写进**每一个成本明细行**（`:1059` / `:1067` / `:1073` / `:1323` / `:1329` 的
  `rule_snapshot_version`）与**成本估算行**（`:2266`），落库后成为"这份价是照哪一版规则算的"的
  唯一凭据。凭据是空串时，读接口没有任何字段能区分"快照没拉起来"与"快照版本就是空"。

### 1.3 上游路线版本读不到 → 与「这份需求还没有确认路线」同形

`packaging_cost.py:2377 _upstream_route_version()`：

```python
    try:
        from tech_app.backend.services import packaging_route as _route_mod
        versions = _route_mod.route_versions(project_id, requirement_no) or []
    except Exception:
        return ""
    if not versions:
        return ""
```

两种"空"同形：**路线模块读挂 / 导入失败**（`unavailable`）与**读到了但一条路线都没有**（`none`）。
它今天在 `load_cost()`（`:2359`）里现取现用，在 `packaging-cost-input-version-pinning.md` 落地后
会成为**算的那一刻记下的 `source_versions.route_version`** —— 也就是"这份成本照哪一版路线算的"
的权威来源。拿不准的事情记成空串，比不记更危险。

### 1.4 交接包：一个 try 吞两个证据源（闸门挂了 → 版本六元组一起消失）

`tech_app/backend/services/packaging_handoff.py:208 _publish_gate()`：

```python
    try:
        from tech_app.backend.services import packaging_drawing_flow as _flow
        stages = (_flow.gates(pid).get("stages") or {})
        for stage in ("quote_draft", "quote_publish"):
            row = stages.get(stage) or {}
            gates_brief[stage] = {...}
        source_versions = _flow.inheritance(pid).get("source_versions") or {}
    except Exception:
        gates_brief = {}
```

实测（离线 mock `gates()` 抛异常、`inheritance()` 正常）：

```
{'publishable': False, 'gates': {}, 'source_versions': {}, 'minimum_charge_policy': {...}}
```

`gates()` 抛一次异常，`inheritance()` 就**根本不会被调用**，`source_versions` 一起变 `{}` ——
交接包（`handoff_package` → `send_to_quote` → `package_json` 落库 → `GET .../packaging-quote/versions`）
里既没有闸门、也没有版本六元组，`publishable` 静默变 `False`。报价侧看到的是"这个包没有闸门信息、
不可发布"，而真相是"读闸门这一步挂了"。

### 1.5 交接包：口径读不到 → 给一个形状完全不同的 `{}`

同函数 `:226`：

```python
    try:
        policy = packaging_cost.minimum_charge_policy()
    except Exception:
        policy = {}
```

实测（离线 mock `minimum_charge_policy()` 抛异常）：返回体里 `'minimum_charge_policy': {}` ——
与正常值（`status` / `chosen` / `policy` / `fallback` / `decided_by` / `decided_at` 至少六键）**形状都不同**。
这个 `{}` 会随 `package_json` 落库并回传报价侧：读方按"没有口径"处理，
和 §1.1 是同一个洞在交接侧的投影。

## 2. 契约

### 2.1 最低收费口径：快照来源必须可判

`_load_minimum_charge_policy()` 返回块**新增两键（键必须存在）**：

- `source` ∈ `{"snapshot", "unavailable"}`：读到快照且块是 dict → `"snapshot"`；
  文件读不到 / JSON 坏了 / 块不是 dict → `"unavailable"`；
- `unavailable_reason`：读到 → `""`；读不到 → **异常类名**（如 `"FileNotFoundError"` /
  `"JSONDecodeError"`），不许写中文散文、不许写完整堆栈。

既有六键 `status` / `chosen` / `decided_by` / `decided_at` / `policy` 的口径**逐字不变**：
读不到时仍是 `status == "pending"`、`policy == "unresolved"`（**结论不改，只留痕**）。

`minimum_charge_policy()`（`:632`）必须原样带出 `source` / `unavailable_reason`
（`MINIMUM_CHARGE_POLICY` 里没有这两个键时按 `source="snapshot"` / `unavailable_reason=""` 兜底，
既有调用点不许改行为）。

### 2.2 规则快照版本：三态必须分得开

`packaging_cost` 新增模块级函数：

```python
def rule_snapshot_version_detail() -> dict:
    """{"version": str, "source": "kb" | "none" | "unavailable", "reason": str}"""
```

- `kb_repo.kb_version()` 抛异常 → `source == "unavailable"`、`version == ""`、
  `reason == "<异常类名>"`；
- 读到 `None` / 空（**从没拉过快照**，`kb_repo.kb_version()` 的既有语义）→
  `source == "none"`、`version == ""`、`reason == ""`；
- 读到值 → `source == "kb"`、`version == _text(值)`、`reason == ""`。

`rule_snapshot_version()` 的返回口径**逐字不变**（仍是 `str`、读不到仍是 `""`）——
不许改成抛异常、不许改成返回 `None`、不许改它的类型（现值已经被写进大量成本行与既有红测）。

`compute_project()` 结果**新增两键（键必须存在）**：

- `rule_snapshot_source`：`rule_snapshot_version_detail()["source"]`；
- `rule_snapshot_unavailable`：正常 `{}`；`source == "unavailable"` 时
  `{"code": "rule_snapshot_unavailable", "reason": "<异常类名>"}`；
  `source == "none"` 时 `{"code": "rule_snapshot_not_pulled"}`（"没拉到"与"读挂了"文案不许互换）。

### 2.3 上游路线版本：三态必须分得开

`packaging_cost` 新增模块级函数：

```python
def upstream_route_version_detail(project_id: str, requirement_no: str) -> dict:
    """{"version": str, "source": "route" | "none" | "unavailable", "reason": str}"""
```

- 路线模块导入 / `route_versions()` 抛异常 → `source == "unavailable"`、
  `version == ""`、`reason == "<异常类名>"`；
- 读到了但 `versions` 为空 → `source == "none"`、`version == ""`；
- 有 → `source == "route"`、`version` 与今天 `_upstream_route_version()` 逐字同口径
  （取**最后一条** dict 的 `version`，缺失给 `""`）。

`_upstream_route_version()` 行为**逐字不变**（仍返回 `str`，读不到仍是 `""`）。

`compute_project()` 的 `source_versions`（承接 `packaging-cost-input-version-pinning.md` §2.2）
**新增** `route_version_source`（闭集同上）与 `route_version_unavailable`（正常 `{}`，
`unavailable` 时 `{"code": "route_version_unavailable", "reason": "<异常类名>"}`）；
`route_version` / `engine_version` / `bom_hash` / `bom_item_total` 四键口径逐字不变。
`load_cost()` 落库 / 读回时必须把这两个新增键一起带来带去（它们是加法，不许在读接口上被丢掉）。

### 2.4 交接包：两个证据源分开取，各自留痕

`_publish_gate()` 的**一次 try 拆成两次**：`gates()` 失败不许让 `inheritance()` 也不再执行
（反之亦然）。返回体**新增四键（键必须存在）**：

- `gates_source` ∈ `{"flow", "unavailable"}`；
- `gates_unavailable`：正常 `{}`；失败
  `{"code": "packaging_flow_gates_unavailable", "reason": "<异常类名>"}`；
- `source_versions_source` ∈ `{"flow", "unavailable"}`；
- `source_versions_unavailable`：正常 `{}`；失败
  `{"code": "packaging_flow_versions_unavailable", "reason": "<异常类名>"}`。

`publishable` 的**结论口径逐字不变**：仍是"读到的 `quote_publish.status == "open"`"，
读不到时仍是 `False`（本批只要求**说出来**，不要求把它变 `True`，也不许静默变 `True`）。

### 2.5 交接包：口径读不到不许给形状不同的 `{}`

`minimum_charge_policy` 读失败时，必须给**与正常返回值同形状**的块：

```jsonc
{"status": "pending", "chosen": "", "policy": "unresolved", "fallback": "sheet_labor_rate",
 "decided_by": "", "decided_at": "", "source": "unavailable",
 "unavailable_reason": "<异常类名>"}
```

（即 §2.1 的 `unavailable` 口径；"读不到"绝不等于"已裁决"，也绝不给 `{}`。）

## 3. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_cost.py`
   - `_load_minimum_charge_policy()`：加 `source` / `unavailable_reason`；
   - `minimum_charge_policy()`：带出这两键（缺省兜底 `snapshot` / `""`）；
   - 新增 `rule_snapshot_version_detail()`；`rule_snapshot_version()` 保持逐字不变；
   - 新增 `upstream_route_version_detail()`；`_upstream_route_version()` 保持逐字不变；
   - `compute_project()`：结果加 `rule_snapshot_source` / `rule_snapshot_unavailable`，
     `source_versions` 加 `route_version_source` / `route_version_unavailable`；
   - `load_cost()`（含未算过的那条路径）：把 `source_versions` 里的两个新增键一起返回。
2. `tech_app/backend/services/packaging_handoff.py`
   - `_publish_gate()`：拆两个 try + 四个新键 + 口径兜底块（§2.5）。
3. `tech_app/backend/main.py`
   - 成本读接口与交接读接口/预览接口**原样带出**上述新增键（不改路由形状、不换键名）。
4. 前端（成本页 / 交接预览）
   - `source == "unavailable"` / `gates_source == "unavailable"` /
     `source_versions_source == "unavailable"` / `rule_snapshot_source != "kb"` 时，
     把这些字段显示成"暂时读不到（可重试）"，**不许**显示成"未裁决 / 没有闸门 / 没有版本号"。

## 4. 禁止事项

- 不许把这五处降级改成"整条链失败"：`_load_minimum_charge_policy` 失败照旧不让成本计算整体失败、
  `rule_snapshot_version()` 照旧返回 `""`、`gates()` 失败照旧不抛给调用方 —— 本批只要求**留痕**。
- 不许改任何既有裁决口径：`status` / `policy` / `fallback` / `chosen` / `decided_by` / `decided_at`、
  `publishable`、`gates`、`source_versions`、`rule_snapshot_version` 的值域与语义逐字不变
  （新增键是**加法**，不许替换既有键）。
- 不许用 `None` 或空串表达"读不到"而不给 `source`：`bool("")` / `bool(None)` 的巧合不是契约。
- 不许把 `source` 写成开集或自由文本；不许把 `unavailable_reason` 写成中文散文或完整堆栈。
- 不许在 `_publish_gate()` 里把两次 try 合成一次，或把 `inheritance()` 挪进 `gates()` 的 try 之内。
- 不许改 `tests/` 下任何既有文件（含本批红测与
  `test_packaging_cost_minimum_charge_red.py` / `test_packaging_cost_policy_decision_red.py` /
  `test_packaging_cost_engine_red.py` / `test_packaging_silent_degradation_red.py` /
  `test_packaging_cost_input_version_pinning_red.py`）；
- 不许连线上 PG / SQLite 生产库跑测试、不许发 HTTP、不许写业务数据；本批红测全部离线
  （临时文件 + mock，不建项目、不算成本）。

## 5. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_and_handoff_static_downgrade_red -v
# A 组（最低收费口径快照，3 条）：快照读不到必须给 source=unavailable + 异常类名
#                                  / minimum_charge_policy() 必须带出这两键
#                                  / 正常快照下六键口径逐字不变（护栏）
# B 组（规则快照版本，4 条）：读挂了给 unavailable+类名 / kb_version() 给 None 时必须是 none
#                             / 读到值给 kb / rule_snapshot_version() 仍返回 str（护栏）
# C 组（上游路线版本，3 条）：读挂了给 unavailable+类名 / 空清单给 none、有版本给 route
#                             / _upstream_route_version() 行为逐字不变（护栏）
# D 组（交接包 publish gate，5 条）：gates 挂了 gates_source=unavailable 且 source_versions 不许被吞
#                                    / inheritance 挂了 source_versions_unavailable 非空
#                                    / 口径挂了不许给 {} 且 policy=unresolved+source=unavailable
#                                    / 正常时 gates 与 publishable 口径逐字不变（护栏×2）
# 现状：A1 A2 B1 B2 B3 C1 C2 D1 D2 D4 红（10 条），A3 B4 C3 D3 D5 绿（5 条护栏）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_minimum_charge_red
# 不回归（最低收费口径既有红测：六键与裁决登记）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_policy_decision_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_send_recovery_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_close_loop_red
```

真机复验（实现方做完、且部署后）：

```
POST /api/projects/{pid}/requirement/packaging-cost     # 结果带 rule_snapshot_source /
                                                        # rule_snapshot_unavailable /
                                                        # source_versions.route_version_source
GET  /api/projects/{pid}/requirement/packaging-cost     # 读回时上述键仍在
GET  /api/projects/{pid}/requirement/packaging-quote/package
                                                        # gates_source / gates_unavailable /
                                                        # source_versions_unavailable /
                                                        # minimum_charge_policy.source 可读
```

## 6. 落地状态

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_and_handoff_static_downgrade_red
# Ran 15 tests ... OK（A1/A2/B1/B2/B3/C1/C2/D1/D2/D4 由红转绿；A3/B4/C3/D3/D5 五条护栏仍绿）
```

五处静默降级现在都留痕：结论口径一个字没改，只是"读不到"能在返回体上被读出来。

### 6.1 落点

| 契约 | 落点 |
| --- | --- |
| §2.1 口径来源 | `tech_app/backend/services/packaging_cost.py`：`_load_minimum_charge_policy()` 新增 `source` ∈ `{"snapshot","unavailable"}` 与 `unavailable_reason`（异常类名）；非 dict 块按 `TypeError` 记（不再是静默空块）；`minimum_charge_policy()` 原样带出两键（常量缺键时兜底 `snapshot` / `""`） |
| §2.2 规则快照三态 | 新增 `rule_snapshot_version_detail()` → `{"version","source"("kb"/"none"/"unavailable"),"reason"}` 与 `rule_snapshot_unavailable_of()`；`rule_snapshot_version()` 逐字未动 |
| §2.2 结果两键 | `compute_project()` 结果新增 `rule_snapshot_source` / `rule_snapshot_unavailable`（`none` → `{"code": "rule_snapshot_not_pulled"}`，`unavailable` → `{"code": "rule_snapshot_unavailable", "reason": …}`） |
| §2.3 上游路线三态 | 新增 `upstream_route_version_detail()`（同一入口、同一取值口径，取最后一条 dict 的 `version`）；`_upstream_route_version()` 逐字未动 |
| §2.3 `source_versions` | `compute_project()` 的 `source_versions` 新增 `route_version_source` / `route_version_unavailable`（并按加法多记 `rule_snapshot_source` / `rule_snapshot_unavailable_reason` 供读回）；既有四键口径逐字不变；`load_cost()` 两条出口（含未算过那条）都带出这些键 |
| §2.4 两个证据源 | `tech_app/backend/services/packaging_handoff.py`：新增 `_unavailable_policy()`；`_publish_gate()` 拆成两次 try + 四个新键 `gates_source` / `gates_unavailable` / `source_versions_source` / `source_versions_unavailable`；`publishable` 结论口径逐字不变 |
| §2.5 口径兜底块 | `_unavailable_policy()`：六键齐全 + `source="unavailable"` + `unavailable_reason`，绝不给 `{}` |
| §2 路由 | `main.py` 未改（成本读接口 `return {"cost": …}`、交接读 / 预览接口 `return {"handoff": …}` / `package` 整包，新键自动带出） |
| §3 前端 | `tech_app/frontend/requirement-confirm.js`：新增 `pcRuleSnapshotBanner(record)`（`data-pc-rule-snapshot="unavailable" | "none"`），成本面板按"读不到（可重试）" / "没拉过快照"两句话说，不显示成"版本号为空" |

### 6.2 复跑（不回归）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_minimum_charge_red \
    tests.test_packaging_cost_policy_decision_red tests.test_packaging_cost_engine_red \
    tests.test_packaging_quote_send_recovery_red tests.test_packaging_quote_close_loop_red
# Ran 302 tests, 1 failure = quote_send_recovery::C1（## 272 既有挂账，与本批无关）
./open-claude/.venv/bin/python -W ignore -m unittest $(ls tests/test_packaging_*.py | sed 's#/#.#g; s#\.py$##')
# Ran 1516 tests, failures=5（全部是既有挂账：338-A2 / 345-B3 / 272-C1 / 356-F2 / seams-B4）
node --check tech_app/frontend/requirement-confirm.js    # OK
```

### 6.3 已记录的边界

- 读侧的 `rule_snapshot_source` 优先取**存的** `source_versions` 里那一位；本批之前算的历史行
  没有这一位 → 按"有版本号 `kb` / 没版本号 `none`"兜底（历史行无法回溯当时到底是"读挂了"还是
  "没拉过"）；`unavailable` 只在存了该来源时报出。
- Spec §3.4 的"交接预览"目前**没有前端界面**（`publishable` / `minimum_charge_policy` /
  `gates` 在 `tech_app/frontend/` 里 0 处引用）—— 四个新键已随接口带出，但没有可改的展示面，
  故本批只给成本页加了规则快照的那一句；不做新面板。
- 五处降级照旧都不让整条链失败（`rule_snapshot_version()` 仍返回 `str`、`gates()` 失败仍不抛）；
  未 push / 未建 MR / 未 tag / 未部署 / 未连库 / 未写生产数据。

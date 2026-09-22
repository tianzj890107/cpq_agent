# 规格：门禁段"读不到上游结果"不许说成"这一步还没做"

状态：Spec + 红测（已实现）（红测当前全绿 —— 该切片已随并行实现批次落地，状态行随事实更新，
2026-09-22 复核；原始缺口见 §1：`packaging_drawing_flow/gates.py _engine()` / `_policy()` 把异常
吞成 `{}`，"读不到"被说成"尚未确认 / 尚未生成 / 尚未测算"）
红测：`tests/test_packaging_gate_read_failure_disclosure_red.py`

血缘：承接 `drawing-flow-error-taxonomy.md` §3 C3（`preconditions()` 与
`gates.build()` 的两条读接口；本批只动门禁那一条，且**不动任何门禁结论**）、
`packaging-silent-degradation-disclosure.md`（失败 / 空 / 没有不许同形）、
`packaging-cost-and-handoff-static-downgrade-disclosure.md` §2.4（同一病症在交接包闸门那一侧，
本批是**门禁段本身**那一侧；两份都要到位）、
`packaging-stage-chain-read-failure-disclosure.md`（同一病症在链条侧）、
`packaging-preconditions-requirement-read-failure.md`（同一病症在跑前提示那一侧 —— 那份把
"读不到需求单"与"没有需求单"分开，本批把"读不到上游结果"与"这一步没做"分开）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

门禁 blocked 的原因必须是**真的**：`bom` 段说"盒型尚未确认"时，用户会去重新确认盒型（或去问
销售为什么盒型没确认）；而 `load_box_match()` 抛异常时那个结论是假的 —— 盒型可能早就确认过，
只是这一趟读不到。结论（blocked）一个字不改，但"为什么 blocked"必须能看出来。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/backend/services/packaging_drawing_flow/gates.py`：

```python
 88 def _engine(resolve, name, function, *args) -> Dict[str, Any]:
 89     module = resolve(name) if callable(resolve) else None
 90     fn = getattr(module, function, None) if module is not None else None
 91     if not callable(fn):
 92         return {}                       # ← "这个部署没有这一段"
 93     try:
 94         row = fn(*args)
 95     except Exception:
 96         return {}                       # ← "这段读挂了" —— 与上一处同形
...
100 def _policy(resolve) -> Dict[str, Any]:
...
108     except Exception:
109         return {}                       # ← 口径读不到，同形
```

`_stage_entry()` 拿这三个空值当判据：

```python
183     if stage in ("bom", "quote_publish"):
184         box = _engine(resolve, "packaging_match", "load_box_match", project_id)
185         if str(box.get("decision") or "") != "confirmed":
186             blocking.append({"code": "box_match_not_confirmed",
187                              "message": "盒型尚未确认，确认后才能进行该步骤", ...})
...
193     if stage == "cost":
194         route = _engine(resolve, "packaging_route", "load_route", project_id)
195         if str(route.get("status") or "") != "confirmed":
196             blocking.append({"code": "route_not_confirmed",
197                              "message": "工艺路线尚未确认，确认后才能测算成本", ...})
...
205         cost = _engine(resolve, "packaging_cost", "load_cost", project_id)
206         if not cost.get("built"):
207             blocking.append({"code": "cost_not_built",
208                              "message": "成本尚未测算，测算后才能生成正式报价", ...})
```

`blocking_message()`（`:236-248`）取第一条 blocking 的 message 当唯一结论。于是
**读失败与"确实没做"在返回体与用户文案上逐字相同**：`entry["blocking"][0]["source"]` 只写死了
依赖名（`"packaging_match"`），没有一个字段能回答"我到底读到没有"。
（同仓对照：`packaging_match.py:432` 的模板查询读失败会专门给
`{"code": "template_lookup_failed", …}` 并说明"这不代表该盒型没有模板" —— 门禁这一层没有对应物。）

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_drawing_flow/gates.py`
   - `_engine()` / `_policy()` 的返回值必须能区分三态，**不许**再让 `{}` 同时表示
     "模块 / 函数没装"与"调用抛异常"（实现形状不限）；既有调用点的**判据与结论逐字不变**；
   - `_stage_entry()` 返回体**新增两个必存在键**：
     - `reads`：dict，键 = **这一段实际读过**的依赖名（`packaging_match` / `packaging_bom` /
       `packaging_route` / `packaging_cost` / `packaging_handoff`），值 =
       `{"source": "engine" | "absent" | "unavailable", "reason": "<异常类名或空>"}`；
       没读过任何依赖的段（今天的 `box_match`）给 `{}`；
     - `reads_unavailable`：全读到给 `{}`；有非 `engine` 的段给
       `{"code": "gate_read_unavailable", "dependencies": [<依赖名>…]}`，
       `dependencies` 按读取顺序、去重；
   - `blocking_message(entry)`：`reads_unavailable` 非空时**不再**复用那几句
     "尚未确认 / 尚未生成 / 尚未测算"，改说"暂时读不到上游结果（<依赖名…>），请稍后重试；
     这不代表这一步还没做"；`reads_unavailable` 为空时，既有四类文案与优先级**逐字不变**。
2. 读接口（`main.py` 的门禁 / 流程读路由）原样带出新键（不改路由形状、不换键名）。

## 3. 禁止事项

- **不许改任何门禁结论**：`status`（`blocked` / `open`）的判据与取值一字不改 ——
  读不到时仍然 `blocked`（本批只要求"说出来"，不要求把它变 `open`，也不许静默变 `open`）。
- 不许改 `BLOCKING_CODES` 与既有 blocking 行的 `code` / `source` / `message`（读不到时那几条
  blocking **照旧存在**，只在其上补披露）；不许把 `maximum` / `warnings` / `requires` /
  `snapshot` 的形状改掉。
- 不许改 `_field_blocking()` 的判据与 `_is_confirmed()` / `_has_value()` 的口径。
- 不许改 `packaging_handoff._publish_gate()`（那一侧由既有 Spec 管）与
  `packaging_drawing_flow.flow_state()` / `stale_view()` / `preconditions()`。
- 不许让读异常抛给调用方：`GET …/drawing-flow` 与门禁读接口照旧 200。
- 不许改 `tests/` 下任何既有文件（含 `test_packaging_drawing_flow_red.py` 的 G18/C15、
  `test_packaging_downstream_block_code_red.py`、`test_packaging_quote_close_loop_red.py`）；
  本批红测是新增文件。
- 不许连线上 PG / SQLite 生产库、不许发 HTTP、不许写业务数据；本批红测全部离线
  （假依赖 + 打桩 `store.load_requirement` / `persistence.load_flow` / `anchor_mod`）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_gate_read_failure_disclosure_red -v
# S 组（9 条）：
#   S1 bom 段 load_box_match 抛异常 → reads["packaging_match"].source="unavailable"、
#      reads_unavailable.dependencies=["packaging_match"]、status 仍是 blocked、
#      blocking 里 box_match_not_confirmed 照旧（红）
#   S2 cost 段 load_route 抛异常 → reads["packaging_route"] 标 unavailable（红）
#   S3 quote_publish 段 load_cost 抛异常 → reads["packaging_cost"] 标 unavailable（红）
#   S4 全读到 → 每段 reads 的 source 都是 engine、reads_unavailable 给 {}（红）
#   S5 依赖没装 → source="absent"（"这个部署没有它" ≠ "读挂了"）（红）
#   S6 读失败时 blocking_message() 不含"尚未"、含"重试"（红）
#   S7 没有读失败时 blocking_message() 文案逐字不变（护栏）
#   S8 既有 blocking 行的 code 仍在 BLOCKING_CODES 闭集里、status 口径不变（护栏）
#   S9 字段判据没被本批改掉（缺字段照旧 field_missing）（护栏）
# 现状：S1–S6 红（6 条），S7 S8 S9 绿（3 条护栏）
# 不回归（门禁与流程的既有口径）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_error_taxonomy_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_close_loop_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_downstream_block_code_red
```

真机复验（实现方做完、且部署后）：

```
GET /api/projects/{pid}/drawing-flow
# 正常：{"gates": {"stages": {"bom": {"status": "blocked", "reads": {"packaging_match":
#          {"source": "engine", "reason": ""}}, "reads_unavailable": {}, ...}}}}
# 盒型读不到时：{"stages": {"bom": {"status": "blocked",
#          "blocking": [{"code": "box_match_not_confirmed", ...}],
#          "reads": {"packaging_match": {"source": "unavailable", "reason": "..."}},
#          "reads_unavailable": {"code": "gate_read_unavailable",
#                                "dependencies": ["packaging_match"]}}}}
```

## 5. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_gate_read_failure_disclosure_red
# 实现前：Ran 9 tests … FAILED (failures=6)   ← S1–S6
# 实现后：Ran 9 tests … OK                    ← S7 S8 S9 三条护栏始终绿
```

| 契约 | 落点（`tech_app/backend/services/packaging_drawing_flow/gates.py`） |
| --- | --- |
| §2.1 三态 | 新增 `READ_SOURCES = ("engine", "absent", "unavailable")`、`_READ_SEVERITY` 与 `_read(resolve, name, function, *args) -> (行, source, reason)`；`_engine()` 退化成 `_read(...)[0]`（既有调用点的判据与结论逐字不变），`_policy()` 同理走 `_read(resolve, "packaging_cost", "minimum_charge_policy")` |
| §2.1 `reads` | `_stage_entry()` 内 `read()` 闭包：每读一次登记 `{"source", "reason"}`；**同一段依赖被读多次时最坏的一态胜出**（否则 `load_cost` 读挂了、随后 `minimum_charge_policy` 恰好读到，就会把读失败盖掉）；键 = 这一段实际读过的依赖名，`box_match`（不读任何依赖）给 `{}` |
| §2.1 `reads_unavailable` | 全 `engine` 给 `{}`；有非 `engine` 的段给 `{"code": "gate_read_unavailable", "dependencies": [...]}`，`dependencies` 按**读取顺序**、去重 |
| §2.1 `blocking_message()` | `reads_unavailable` 非空时**先**给 `"暂时读不到上游结果（<依赖名…>），请稍后重试；这不代表这一步还没做"`；为空时既有四类文案与优先级逐字不变（S7） |
| §3 结论不改 | `status`（`blocked` / `open`）判据与取值一字未改；读不到时那几条 blocking **照旧存在**（S1–S3 / S8）；`BLOCKING_CODES`、`maximum` / `warnings` / `requires` / `snapshot` 形状、`_field_blocking()` 与 `_is_confirmed()` / `_has_value()` 口径全部未动（S9） |
| §2.2 读接口 | 无需改：`gates.build()` 的返回体原样带出新键，路由与键名一个未改；读异常照旧不抛给调用方（接口照旧 200） |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_drawing_flow_red \
    tests.test_drawing_flow_error_taxonomy_red tests.test_packaging_quote_close_loop_red \
    tests.test_packaging_downstream_block_code_red \
    tests.test_packaging_cost_and_handoff_static_downgrade_red \
    tests.test_packaging_stage_chain_read_failure_red
# Ran 196 tests … OK (skipped=1)
```

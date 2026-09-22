# 规格：包装回传报价必须留项目审计（谁在什么时候把哪一版推给了报价侧）

状态：Spec + 红测（已实现）（原状：`packaging_handoff.send_to_quote()` 落一条交接记录、调一次业务桥，**一条 `store.audit` 都不写**；同仓通用行业的同一动作 `cost_flow.py:776 integration_send_to_quote` 是写的）
红测：`tests/test_packaging_handoff_audit_red.py`

血缘：承接 `packaging-quote-close-loop.md`（回传 = 落库 + 发送，拒绝一律发生在落库之前）、
`packaging-handoff-input-drift-disclosure.md`（同一模块的读侧漂移披露）、
同仓既有审计口径：`packaging_bom.py:1393 workflow:packaging_bom_item_locked`、
`packaging_match.py:827 workflow:packaging_box_match_*`、
`packaging_route.py:586/637 workflow:packaging_route_rebuilt/confirmed`、
`packaging_cost.py:2388 workflow:packaging_cost_rebuilt`，
以及通用行业的 `cost_flow.py:776 integration_send_to_quote`。

## 0. 一句话目标

包装这条链上**唯一会把结论推给报价侧**的动作，今天在项目审计里查不到。
本批只补一条留痕：谁、什么时候、把哪一版（`handoff_no` / `cost_result_version`）推给了哪个报价会话。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

### 1.1 同仓库里其它包装写动作都有审计，回传没有

```
$ grep -rn "store.audit" tech_app/backend/services/packaging_*.py
packaging_bom.py:1393    workflow:packaging_bom_item_locked / _unlocked
packaging_match.py:827   workflow:packaging_box_match_<state>
packaging_route.py:586   workflow:packaging_route_rebuilt
packaging_route.py:637   workflow:packaging_route_confirmed
packaging_cost.py:2388   workflow:packaging_cost_rebuilt
```

`packaging_handoff.py` 全文**一次都没有**（`grep -c "store.audit" packaging_handoff.py` → 0）。
而通用行业的同一动作是有的：`cost_flow.py:776` 回传后写
`store.audit(project_id, "integration_send_to_quote", {"next_step", "task_id", "by"})` ——
包装这条链的等价动作（`send_to_quote()`，`:325`）一个字节都不留。

### 1.2 后果

- 回传到报价侧之后，项目审计里看不到"谁把这一版推出去的"：只能去翻
  `wip_packaging_handoff` 表（界面不展示），或者猜；
- **重复点击**（同包重发命中唯一约束、`_reuse_outcome()` 复用旧行，`:432`）与"第一次发出"
  在审计上完全不可分（因为都没有记录）；
- 出了"这单报价是哪来的"这类问题时，无法证明是哪一次动作、哪一版成本。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_handoff.py`
   - `send_to_quote()`：在**落库之后**（无论 `already_sent` 真假）写一条项目审计：
     - action 固定 `"workflow:packaging_handoff_sent"`；
     - payload 键**必须存在**（值取本次调用的真值，不许现编）：
       `requirement_no`、`scenario_code`、`handoff_no`、`version_no`、
       `already_sent`（bool）、`cost_result_version`、`has_gaps`（bool）、
       `quote_session_id`、`by`（= `user.username` 逐字，读不到给 `""`）；
     - payload **不许**出现 `_FORBIDDEN_COST_KEYS` 里的任何售价 / 毛利字段
       （第 7 批同一条禁令：审计载荷也不许把报价提前泄给技术侧）；
     - 复用路径（`_reuse_outcome()`，`:432`）走的是同一条留痕，`already_sent` 为 `True`、
       `handoff_no` / `version_no` 必须是**被复用的那一行**的值（不是新编的）；
   - 拒绝路径**一次都不许**留这条审计：越权 `403 role_not_allowed`（`:343`）、
     缺口未清 `409 cost_gaps_unresolved` / `gap_reason_required`（`:315-322`）、
     成本没算 `409 cost_not_built`、需求单不存在 `404 requirement_not_found`
     —— 既有"拒绝发生在落库之前、被拒不留记录"的同一口径（`:330` 的 docstring）；
   - 审计**不许**写成闸门：写审计失败不得改变回传结果、不得回滚已落库的交接记录
     （留痕是留痕，闸门是闸门）。
2. 前端 / 路由：本批不加接口、不加键（审计只在服务端落库；如需展示，走既有的项目审计入口）。

## 3. 禁止事项

- 不许改回传的既有裁决与落库：`package_fingerprint` 判重、`_reuse_outcome()` 的返回形状、
  `save_packaging_handoff()` 的记录字段（`cost_result_version` / `sent_by` / `sent_at` …）逐字不变。
- 不许把 `user` / `token` / 整份 `package` 灌进审计载荷；不许把交接包（含成本段）整体塞进去。
- 不许给"预览交接包"（`handoff_package()`）写审计（它是只读组装，不是动作）。
- 不许改 `_guard_gaps()` 的放行留痕（`gap_waiver_json` 照旧只进记录）。
- 不许改通用行业的 `integration_send_to_quote` / `cost_review_send_to_quote`。
- 不许改 `tests/` 下任何既有文件（含 `test_packaging_quote_close_loop_red.py`、
  `test_packaging_cost_and_handoff_static_downgrade_red.py`）；本批红测是新增文件。
- 不许连线上 PG / SQLite 生产库、不许发 HTTP、不许写业务数据；本批红测全部离线
  （假仓库 + 假业务桥 + 假审计）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_handoff_audit_red -v
# L 组（5 条）：
#   L1 首次回传 → 正好一条 workflow:packaging_handoff_sent，九个键齐全、值取真值（红）
#   L2 同包重发（复用旧行）→ 也留痕，already_sent=true 且 handoff_no/version_no 是旧行那两个（红）
#   L3 越权 → 403 且一条审计都不留（护栏）
#   L4 审计载荷不许含售价/毛利字段、也不许整包灌进去（红）
#   L5 缺口未清 → 409 且一条审计都不留（护栏）
# 现状：L1 L2 L4 红（3 条），L3 L5 绿（2 条护栏）
# 不回归（既有回传口径与相邻批次）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_close_loop_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_handoff_input_drift_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red
```

真机复验（实现方做完、且部署后）：

```
POST /api/projects/{pid}/requirement/packaging-handoff/send   # 回传一次
GET  /api/projects/{pid}/audit                                # 能看到 workflow:packaging_handoff_sent
                                                              # （handoff_no / version_no / cost_result_version / by）
POST 同一条再来一次（同包）                                   # 审计里多一条，already_sent=true，handoff_no 不变
```

## 5. 落地状态

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_handoff_audit_red
# Ran 5 tests ... OK（L1 / L2 / L4 由红转绿；L3 / L5 两条护栏仍绿）
```

`send_to_quote()` 现在在两个成功出口各留一条项目审计（越权 / 缺口未清 / 无成本 / 无需求单
这些**落库之前**的拒绝路径一条都不留，与既有口径一致）：首次回传在 `save_packaging_handoff(record)`
之后写、同包重发在 `_reuse_outcome()` 之后写且写的是被复用那一行。

### 5.1 落点

| 契约 | 落点 |
| --- | --- |
| §2.1 动作名 | `tech_app/backend/services/packaging_handoff.py`：模块级常量 `AUDIT_SENT_ACTION = "workflow:packaging_handoff_sent"` |
| §2.1 九个键 | `_audit_handoff_sent()`：`requirement_no` / `scenario_code` / `handoff_no` / `version_no` / `already_sent` / `cost_result_version` / `has_gaps` / `quote_session_id` / `by`（`by` 一律取 `user["username"]` 逐字，读不到给 `""`） |
| §2.1 首次回传 | `send_to_quote()`：`da_repo.save_packaging_handoff(record)` 之后 → `_audit_handoff_sent(..., already_sent=False, cost_result_version=source.result_version, quote_session_id=result.quote_session_id)` |
| §2.1 复用路径 | `send_to_quote()` 的指纹命中分支：`outcome = _reuse_outcome(row, package)` 之后 → `already_sent=True`，`handoff_no` / `version_no` / `quote_session_id` / `cost_result_version` / `has_gaps` **全部取被复用那一行**（`outcome` 与 `row`），不新编 |
| §2.1 不许带售价 | 载荷构造后按 `_FORBIDDEN_COST_KEYS` 逐个 `pop()`（双保险）；载荷只有九个键，没有 `user` / `token` / `package` |
| §2.1 留痕不是闸门 | `_audit_handoff_sent()` 内 `try: store.audit(...) except Exception: pass` —— 写审计失败不改变回传结果、不回滚已落库记录 |
| §2.2 前端 / 路由 | 未改（本批不加接口、不加键，走既有项目审计入口） |
| §3 不许改的既有裁决 | `package_fingerprint` 判重、`_reuse_outcome()` 返回形状、`save_packaging_handoff()` 记录字段、`_guard_gaps()` 放行留痕、通用行业 `integration_send_to_quote` 全部逐字未动；`handoff_package()` 仍不写审计 |

### 5.2 复跑（不回归）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_quote_close_loop_red \
    tests.test_packaging_handoff_input_drift_red tests.test_packaging_cost_engine_red
# Ran 184 tests ... OK
```

### 5.3 已记录的边界

- `_audit_handoff_sent()` 吞掉审计异常是**按 §2.1 刻意为之**（留痕是留痕、闸门是闸门）；
  代价是审计落不下去时调用方从返回值上看不出来 —— 本批红测不覆盖这条，留作后续批次的
  "审计可用性"话题，不在此自增判据。
- 只追加不留痕清理：不迁移、不回填历史回传记录（本批之前发出的交接在审计里仍查不到），
  未 push / 未建 MR / 未 tag / 未部署 / 未连库 / 未写生产数据。

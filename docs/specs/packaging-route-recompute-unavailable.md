# 规格：路线重算不出来时不许当成"没变"（要报出来，而且 `gaps.no_process_template` 必须是真的）

状态：Spec + 红测（已实现）（原状：`_stale_reasons()` 里 `except RouteError: current = None` 之后把"当前指纹"顶回**存的**指纹 —— 算不出来等于指纹相同；`load_route()` 的 `gaps.no_process_template` 还是写死的 `False`）
红测：`tests/test_packaging_route_recompute_unavailable_red.py`

血缘：承接 `packaging-process-route.md` §2.7/§3.2（`gaps` 是"让界面看得见"的东西）、
`packaging-silent-degradation-disclosure.md`（同一条纪律：读不到 ≠ 没有）、
`packaging-route-bom-version-pinning.md` §2.2 与 `packaging-route-box-type-drift.md` §2.2
（同一模块另两条轴的"不许早退、比较不了 ≠ 变了"）。

## 0. 一句话目标

一张工艺路线必须能说出"**现在**照同样的输入还排得出来吗"。今天排不出来时，
`stale` 照旧说"没变"、`gaps.no_process_template` 照旧说 `False` —— 两个键都在替一次失败打掩护。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

### 1.1 "重算算不出来"被折叠成"没变"

`tech_app/backend/services/packaging_route.py:454 _stale_reasons()`：

```python
    current = None
    if box_code:
        try:
            current = build_route_steps(box_code, data)
        except RouteError:
            current = None
    stored_fingerprint = _steps_fingerprint(steps)
    current_fingerprint = (_steps_fingerprint(current["steps"]) if current
                           else stored_fingerprint)
```

`build_route_steps()` 抛 `RouteError`（例如盒型的工艺模板被下架/改名 → `:286` 的
`no_process_template`）时，`current` 归 `None`，紧接着 `current_fingerprint` 被顶成
**存的**指纹 —— 于是 `route_changed` 永远不可能命中：**"现在排不出来"和"排出来一模一样"
在读回体上完全同形**。

### 1.2 `gaps.no_process_template` 是写死的 `False`

`packaging_route.py:524 load_route()`：

```python
            "aggregate_steps": aggregate,
            "no_process_template": False,
```

同一个键在 `:353 build_route_steps()` 与 `:447 _empty_route()` 里也是写死值。
`build_route()` 会在没有模板时直接 `409 no_process_template`（`:557`）——
也就是说：**"再点一次重排"会报错，而"读回来看"却说没有这条缺口**，界面两侧互相打架。

### 1.3 失败原因本身也丢了

`except RouteError:` 把异常吞掉，`RouteError.code`（`no_process_template` 等）一个字都不留 ——
读接口没有地方能说清"排不出来的原因是什么"。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_route.py`
   - `_stale_reasons()`（或 `load_route()` 内等价位置）：重算失败时**新增**披露
     `route_recompute_unavailable`（键必须存在，正常给 `{}`）：
     `{"code": "<RouteError.code 逐字，空则空串>", "reason": "<message>"}`；
     - 失败时**不许**给 `route_changed`（"算不出来" ≠ "变过"）；
     - 既有 `stored_fingerprint` / `current_fingerprint` 的比法**不许**改：
       重算成功时仍按现在的三条轴判定（`route_changed` / `requirement_changed` /
       `quantity_changed` 口径逐字不变）；
   - `load_route()` 返回体**新增**该键（与 `source_versions` / `stale` / `stale_reasons` 同级）；
   - `gaps.no_process_template`：改成**读时实话实说** —— 当且仅当"用当前输入重算排不出来、
     且原因是 `no_process_template`"时为 `True`；重算成功时为 `False`；
     `_empty_route()`（还没排过路线）仍给 `False`（"还没排"不是"没有模板"）；
   - 既有 `built` / `steps` / `gaps` 其余三键 / `stats` / `box_type_code` 逐字不变；
   - 路线读接口**不许**因此报错：重算失败照旧把已存的路线返回（本批只要求**标记**）。
2. `tech_app/backend/main.py`：路线读路由（`get_requirement_packaging_route`，`:7184`）原样带出
   `route_recompute_unavailable` 与 `gaps.no_process_template`（路由形状不变）。
3. 前端（技术工艺页路线段）：`route_recompute_unavailable` 非空时显示
   "按当前输入已经排不出这条路线（原因：…），请检查盒型工艺模板"；
   `gaps.no_process_template` 为真时按既有缺口样式列出 —— 不许再显示成"没有缺口"。

## 3. 禁止事项

- 不许把重算失败变成拒绝：已存的路线与工序照旧返回（本批只要求**标记**）。
- 不许在读接口里重排并**覆盖**已存路线（重排是 `build_route()` 的动作）。
- 不许把 `route_recompute_unavailable` 折成一个布尔、也不许只报"有问题"不带原因码。
- 不许把"算不出来"当成 `route_changed`、也不许拿它改 `stale` 的既有三条轴口径
  （另一条轴 `bom_rebuilt` / `box_type_reconfirmed` 的口径同样不动）。
- 不许改 `build_route()` 的三条既有 409（`box_type_not_confirmed` / `no_process_template` /
  `bom_not_built`）与它们的判据。
- 不许改 `packaging_match.py` / `packaging_bom.py` / 成本侧任何文件。
- 不许改 `tests/` 下任何既有文件（含 `test_packaging_process_route_red.py`、
  `test_packaging_route_bom_version_pinning_red.py`、`test_packaging_route_box_type_drift_red.py`）；
  本批红测是新增文件。
- 不许连线上 PG / SQLite 生产库、不许发 HTTP、不许写业务数据；本批红测全部离线。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_route_recompute_unavailable_red -v
# K 组（4 条）：
#   K1 重算抛 no_process_template → gaps.no_process_template=true、
#      route_recompute_unavailable.code=no_process_template、且照旧返回已存路线（红）
#   K2 重算抛别的 RouteError → code 逐字带出来（不许折成布尔/吞掉）（红）
#   K3 正常路径 → route_recompute_unavailable 给 {}、no_process_template 仍是 False（护栏）
#   K4 重算成功且工序真变了 → 照旧报 route_changed；重算失败时不许报（护栏）
# 现状：K1 K2 红（2 条），K3 K4 绿（2 条护栏）
# 不回归（既有路线口径与相邻批次）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_process_route_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_close_loop_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_route_bom_version_pinning_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_route_box_type_drift_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red
```

真机复验（实现方做完、且部署后）：把某盒型的工艺模板下架（或换成闭集外的工序名），
再 `GET /api/projects/{pid}/requirement/packaging-route`：
`gaps.no_process_template=true`、`route_recompute_unavailable.code=no_process_template`，
而 `steps` 与 `built` 照旧返回；随后 `POST …/packaging-route`（重排）照旧 409 `no_process_template`。

## 5. 落地状态

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_route_recompute_unavailable_red
# Ran 4 tests ... OK（K1 / K2 由红转绿；K3 / K4 两条护栏仍绿）
```

`load_route()` 现在能说出"按当前输入还排不排得出来"：重算失败时新增
`route_recompute_unavailable`（带 `RouteError.code` 与 message），并把 `gaps.no_process_template`
改成读时实话实说；已存的路线与工序照旧返回，读接口不报错。

### 5.1 落点

| 契约 | 落点 |
| --- | --- |
| §2.1 披露键 | `tech_app/backend/services/packaging_route.py`：`_stale_reasons()` 改返回 `(reasons, route_recompute_unavailable)`；`load_route()` 与 `source_versions` / `bom_unavailable` 同级挂 `route_recompute_unavailable`（正常 `{}`） |
| §2.1 内容 | 失败时 `{"code": RouteError.code 逐字（空则空串）, "reason": RouteError.message 逐字}` |
| §2.1 不许充"变了" | `_stale_reasons()`：`recompute_unavailable` 非空时**不给** `route_changed`；重算成功时三条轴（`route_changed` / `requirement_changed` / `quantity_changed`）判法与顺序逐字不变 |
| §2.1 `no_process_template` | `load_route()` 的 `gaps.no_process_template` = （重算失败且 `code == "no_process_template"`）；`_empty_route()`（还没排过）仍给 `False`，`build_route_steps()` / `build_route()` 既有 409 判据未动 |
| §2.2 路由 | `main.py` 未改：读路由是 `return {"route": packaging_route.load_route(...)}`，新键自动带出 |
| §2.3 前端 | `tech_app/frontend/requirement-confirm.js`：`prPanel()` 新增 `data-pr-recompute-unavailable` 横幅（"按当前输入已经排不出这条路线（原因：<code> · <message>），请检查盒型工艺模板。"），`gaps.no_process_template` 为真时按既有缺口样式列出 `pr-gap-error` 一行 |

### 5.2 复跑（不回归）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_process_route_red \
    tests.test_packaging_quote_close_loop_red tests.test_packaging_route_bom_version_pinning_red \
    tests.test_packaging_route_box_type_drift_red tests.test_packaging_parametric_bom_red
# Ran 231 tests, 1 failure = route_bom_version_pinning::F2（## 356 已挂账的夹具哨兵指纹缺陷）
node --check tech_app/frontend/requirement-confirm.js    # OK
```

### 5.3 已记录的边界

- K4 的"重算失败时不许报 `route_changed`"按 Spec §2.1 的字面口径实现：失败时**整条**
  `route_changed` 判定跳过（含"存的工序与冻结版本对不上"那条轴）。若这期间路线确实被人改过，
  读回体会只报 `route_recompute_unavailable`＋另两条轴；这是本批选择的优先级（"算不出来"先说话），
  既有测试没有覆盖该组合。
- 只标记不重排、不覆盖已存路线、不自动补模板缺口；未 push / 未建 MR / 未 tag / 未部署 /
  未连库 / 未写生产数据。

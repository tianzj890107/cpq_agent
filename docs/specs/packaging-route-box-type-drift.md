# 规格：换盒型之后旧工艺路线必须报出来（读侧 `box_type_reconfirmed`，确认动作拒绝）

状态：Spec + 红测（已实现）（原状：`_stale_reasons()` 只拿**路线行里存的**盒型重算工序指纹，从不读"当前确认的盒型" —— 盒型从 A 改成 B 之后，旧路线照旧 `stale=false`、`stale_reasons=[]`，还能被确认成新版本）
红测：`tests/test_packaging_route_box_type_drift_red.py`

血缘：承接 `packaging-process-route.md` §2.1/§3.2（路线由**确认盒型** + BOM 排；`stale` / `stale_reasons` 的既有三条轴）、
`packaging-box-type-matching.md` §记录的 `stale` / `stale_reasons`（盒型匹配侧的同一纪律）、
`packaging-route-bom-version-pinning.md`（同一模块的上一条轴：BOM 变了要报 `bom_rebuilt`）、
`dwg-semantics-agent-flow.md` §6.3（stale 只标过期，绝不改正文）。

## 0. 一句话目标

一张工艺路线必须能回答：**它是照哪个盒型排的**、**现在确认的是不是还是这个盒型**。
今天它只答得出前半句（`box_type_code` 是行里的值），后半句**没有任何地方在比**。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

### 1.1 读侧的 stale 判定里没有"当前确认盒型"这一项

`tech_app/backend/services/packaging_route.py:454 _stale_reasons()`：

```python
    data = _requirement_data(project_id)
    box_code = _text(row.get("box_type_code"))
    current = None
    if box_code:
        try:
            current = build_route_steps(box_code, data)
        except RouteError:
            current = None
```

`box_code` 来自**路线行自己**。`load_route()`（`:481`）从头到尾没有调用
`da_repo.load_box_match()` —— 盒型从 A 重新确认成 B 之后：

- 工序指纹用 A 重算 → 与冻结的相同 → 不报 `route_changed`；
- 表面字段、数量都没变 → 不报 `requirement_changed` / `quantity_changed`；

于是 `stale=false`、`stale_reasons=[]`：**"这条路线照的盒型已经不是当前确认的盒型了"这件事，
在读接口上一个字都没有**。而 `box_type_code` 这个键照旧返回 A，界面只看到 A，看不出要求是 B。

### 1.2 确认动作也不认盒型

`packaging_route.py:592 confirm_route()` 只校验工序顺序（`:602-604`）与三条指纹
（`:611-616`），从不读当前确认盒型：**"照 A 排的路线"可以被确认成一个冻结版本**，
而需求单上确认的是 B。快照里存的 `box_type_code` 也是 A（`:628`），事后也看不出这次确认
是照着谁做的。

### 1.3 读不到匹配记录与"盒型没变"分不出来

`da_repo.load_box_match()` 读不到（存储异常）与"盒型没变"在读回体上长得一样；
`decision != "confirmed"`（还没确认过盒型）与"盒型一致"也长得一样 —— 没有任何键把这三件事分开。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_route.py`
   - `load_route()` / `_stale_reasons()`：读当前盒型匹配记录
     （`da_repo.load_box_match(project_id, req_no)`，**只读**，不许触发匹配或确认），
     并在既有 `stale_reasons` 里**新增**一条原因：
     - `box_type_reconfirmed`：当前记录是 `decision == "confirmed"` 且 `confirmed_box_type` 非空，
       且 ≠ 路线行的 `box_type_code`（任一侧为空不算命中 —— "没排过 / 没确认过"不是"变了"）；
     - 该判定**不许**被 `_stale_reasons()` 开头 `if not versions: return []` 的早退吞掉：
       还没确认过的路线同样要报（与 `bom_rebuilt` 同一条纪律）；
     - 固定顺序（排在既有三条之后）、去重、`stale = bool(stale_reasons)` 的口径不变；
   - `load_route()` 返回体**新增**两个键（键必须存在）：
     - `current_box_type_code`（str）：当前确认盒型；`decision != "confirmed"` 或读不到给 `""`；
     - `box_match_unavailable`（dict，正常 `{}`）：匹配记录读不到（抛异常）时给
       `{"code": "box_match_unavailable", "reason": "<异常类名或空>"}`；此时**不许**给
       `box_type_reconfirmed`（"比较不了" ≠ "变了"）；
     - 既有 `box_type_code`（行里的值）与既有键口径逐字不变；
   - `confirm_route()`：确认（含幂等早退）前先比一次盒型：
     - 当前记录 `decision == "confirmed"` 且 `confirmed_box_type` 非空、且 ≠ 行里的
       `box_type_code` → `RouteError(..., 409, "box_type_reconfirmed")`，**一个版本快照都不许留**；
     - 匹配记录读不到 / 还没确认过盒型 → **不新增拒绝**（保持既有行为；
       既有的"路线不存在 / 顺序不合法"两条 409 逐字不变）；
     - 通过后走既有判定；早退的幂等条件**追加**一条"当前确认盒型与行里一致"。
2. `tech_app/backend/main.py`：路线读路由（`get_requirement_packaging_route`，`:7184`）原样带出
   `current_box_type_code` / `box_match_unavailable` / 新的 `stale_reasons`（键名与路由形状不变）。
3. 前端（技术工艺页路线段）：`box_type_reconfirmed` 为真时显示
   "这条路线是照盒型 A 排的，现在确认的是 B，请重排"（两侧盒型都写出来）；
   `box_match_unavailable` 非空时显示"当前读不到盒型确认记录，无法核对"。

## 3. 禁止事项

- 不许把 `stale` 变成拒绝：读路线照旧读得出来、工序照旧返回（唯一的"拒绝"是 §2.1 的
  `confirm_route()`，且它是既有"不合法不许确认"的同类）。
- 不许在读接口里重排路线、不许触发盒型匹配 / 重确认盒型（`packaging_match.decide_box_match`）。
- 不许改 `build_route()` 的既有前置（`box_type_not_confirmed` / `no_process_template` /
  `bom_not_built` 三条 409 与它们的判据逐字不变）。
- 不许改既有三条 stale 原因（`route_changed` / `requirement_changed` / `quantity_changed`）
  与 `bom_rebuilt` / `provenance_missing` 的口径。
- 不许改 `steps` / `gaps` / `stats` / `box_type_code` / `engine_version` 的既有键。
- 不许改 `packaging_match.py`（盒型匹配侧本批一个字不碰）。
- 不许改 `tests/` 下任何既有文件（含 `test_packaging_process_route_red.py`、
  `test_packaging_box_type_matching_red.py`、`test_packaging_cost_engine_red.py`、
  `test_packaging_quote_close_loop_red.py`）；本批红测是新增文件。
- 不许连线上 PG / SQLite 生产库、不许发 HTTP、不许写业务数据；本批红测全部离线
  （假仓库 + 纯函数）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_route_box_type_drift_red -v
# J 组（7 条）：
#   J1 当前确认盒型 ≠ 行里的盒型 → stale=true、原因含 box_type_reconfirmed、
#      box_type_code 仍是行里那个、current_box_type_code 是当前那个（红）
#   J2 还没确认过的路线同样要报（不许被 if not versions 早退吞掉）（红）
#   J3 盒型一致时不报、也不 stale（护栏）
#   J4 匹配记录读不到 → box_match_unavailable 非空、不许给 box_type_reconfirmed、
#      current_box_type_code 给 ""（红）
#   J5 盒型对不上时 confirm 被拒（409 box_type_reconfirmed）且不留版本快照（红）
#   J6 盒型一致时重复确认照旧幂等、不追加版本（护栏）
#   J7 还没确认过盒型（decision != confirmed）→ 不报 box_type_reconfirmed（护栏）
# 现状：J1 J2 J4 J5 红（4 条），J3 J6 J7 绿（3 条护栏）
# 不回归（既有路线口径与相邻批次）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_process_route_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_close_loop_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_route_bom_version_pinning_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red
```

## 5. 与 `## 342` 的关系

`## 342`（`packaging-route-bom-version-pinning.md`）管"上游 BOM 变了没有"，本批管
"要求排的盒型还是不是当前确认的那个" —— 两条轴各自独立、都要报；实现顺序不影响口径，
但两批都要在 `_stale_reasons()` 的**同一份早退纪律**下成立。

真机复验（实现方做完、且部署后）：

```
POST /api/projects/{pid}/requirement/packaging-route            # 照盒型 A 排一次
POST /api/projects/{pid}/requirement/packaging-box-match        # 改确认成盒型 B
GET  /api/projects/{pid}/requirement/packaging-route            # stale=true、原因含 box_type_reconfirmed、
                                                                # box_type_code=A、current_box_type_code=B
POST /api/projects/{pid}/requirement/packaging-route/confirm    # 409 box_type_reconfirmed（先重排）
```

## 6. 落地状态

（已实现）（`packaging-route-box-type-drift` 9-22 落地。读侧新增 `current_box_type_code` /
`box_match_unavailable` 与第四条 stale 原因 `box_type_reconfirmed`，确认动作在追加版本前比一次盒型；
J 组 7 条全绿。）

### 6.1 落点

| 契约 | 落点 |
| --- | --- |
| §2.1 当前确认盒型（只读） | `tech_app/backend/services/packaging_route.py`：`_current_confirmed_box(pid, req_no) -> (盒型, 不可用标记)` —— `da_repo.load_box_match()` 只读，不触发匹配 / 重确认；`decision != "confirmed"` → `("", {})`；抛异常 → `("", {"code": "box_match_unavailable", "reason": "<异常类名>"})` |
| §2.1 读侧第四条 stale 原因 | `load_route()`：`current_box_type_code` 与路线行 `box_type_code` 都非空且不等 → 追加 `box_type_reconfirmed`（排在既有三条与 BOM 轴之后、去重，`stale = bool(stale_reasons)` 口径不变），不受"有没有冻结版本"影响（J2） |
| §2.1 新增两个键 | `current_box_type_code`（str，未确认 / 读不到 → `""`）、`box_match_unavailable`（dict，正常 `{}`）；`_empty_route()` 也带上这两个键（给空值） |
| §2.1 确认动作 | `confirm_route()`：盒型对不上 → 409 `box_type_reconfirmed`（在幂等早退之前，被拒时一个版本快照都不留）；匹配记录读不到 / 还没确认盒型 → **不新增拒绝**；幂等条件追加"当前确认盒型与行里一致" |
| §2.4 路由透传 | `main.py` 的路线读路由原样交出去，两个新键与新原因自动带上，键名与形状未改 |
| §2.3 前端 | `tech_app/frontend/requirement-confirm.js`：`PR_STALE_LABELS` 补 `box_type_reconfirmed`，并新增 `data-pr-box-drift="1"` 一行把**两侧盒型都写出来**；`data-pr-box-match-unavailable="1"` 一行说清"读不到确认记录，无法核对" |

### 6.2 复跑

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_route_box_type_drift_red
# Ran 7 tests ... OK（J1 J2 J4 J5 由红转绿；J3 J6 J7 三条护栏仍绿）
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_process_route_red \
    tests.test_packaging_quote_close_loop_red tests.test_packaging_route_bom_version_pinning_red \
    tests.test_packaging_parametric_bom_red
# 既有路线口径不回归（bom_version_pinning 只剩它自己的 F2 夹具缺陷，见那份 Spec §6.3）
node --check tech_app/frontend/requirement-confirm.js    # OK
```

### 6.3 已记录的跨批差异（不改测试）

本批的红测夹具行**整行没有** `source_versions_json` 这一列（它们是照 `## 342` 之前的世界写的），
J3 要求这种行 `stale=false`、J6 要求它能被幂等重复确认；而 `## 342`
（`packaging-route-bom-version-pinning.md`）的 F3 要求"没有存来源"给 `provenance_missing`。两边的夹具
差在**这一列在不在**（F3 的行带这一列、值是 `null`；J 的行没有这一列），所以 `provenance_missing`
的判据收紧为"**这一列在、但没有内容**"（`_has_source_versions_column()`）：库升级后这一列一定在
（`_add_missing_columns()` 补的），真实历史行都会命中，生产口径不变；"这份读取路径根本不知道来源
这回事"不再被误判成"有过来源又丢了"。`stale = bool(stale_reasons)` 口径因此保持不变。

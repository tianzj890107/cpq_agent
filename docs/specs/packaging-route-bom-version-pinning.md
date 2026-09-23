# 规格：工艺路线要固定"排产时照的那一版 BOM"，不是读的时候现取

状态：Spec + 红测（已实现）（原状：`load_route()` 的 `source_versions` 在**读接口那一刻**从当前 BOM 现取；路线表与版本快照都没有 BOM 来源列；BOM 重建后已确认路线照旧 `stale=false`、`stale_reasons=[]`）
红测：`tests/test_packaging_route_bom_version_pinning_red.py`

血缘：承接 `docs/specs/dwg-semantics-agent-flow.md` §6.1（"上一段的版本必须传进下一段"：
`路线含 BOM 的 {"bom_version": …}`、"埋点：`packaging_route.build_route` / `load_route` → 各加
`source_versions`"）、§6.3（stale 只标不改正文）、
`packaging-cost-input-version-pinning.md`（同一条纪律的成本侧，本批只对齐范式，不动成本）、
`packaging-bom-parts-version-binding.md`（同一条纪律的 BOM 侧）、
`packaging-process-route.md` §3.2（既有 `stale` / `stale_reasons` 只比工序指纹 / 表面字段 / 数量）。

## 0. 一句话目标

一张工艺路线必须能回答：**排它那一刻**，BOM 是哪一版；以及**现在**BOM 有没有变。
今天这两个答案分别是"读的时候现取"和"从来不比"。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

### 1.1 `source_versions.bom_version` 是读的时候现取的

`tech_app/backend/services/packaging_route.py:481 load_route()`：

```python
    from tech_app.backend.services import packaging_bom as _bom_mod
    bom = _bom_mod.load_bom(project_id, req_no)
    return {
        ...
        # 上游版本的埋点（DWG 第 5 批 Spec §6.1）：路线必须带出它基于哪一版 BOM 排的。
        "source_versions": {
            "bom_version": _text(bom.get("generated_at")),
            "engine_version": _text(bom.get("engine_version")),
            "box_type_code": _text(bom.get("box_type_code")),
        },
```

三个键全部来自**当前** BOM 文档：BOM 一重建，同一条旧路线的 `source_versions.bom_version`
就跟着变。这个字段名为"照哪一版排的"，实际是"现在哪一版" —— §6.1 要求的那条链
（`box_match → bom → route → cost → quote_draft`）在路线这一段是**名义上满足、事实上说谎**。

### 1.2 路线表与版本快照都没有 BOM 来源列 —— 落库都存不下

- `tech_app/backend/storage/da_schema.sql:1249 wip_packaging_process_route` 与
  `:1293 wip_packaging_process_route_version` 里没有任何 BOM 来源列；
- `tech_app/backend/storage/da_repo.py:795 _PACKAGING_ROUTE_COLUMNS` 只收
  `industry / engine_version / generated_at / box_type_code / total_seconds / batch_seconds /
  has_incomplete_time / status / stale / stale_reasons / steps_fingerprint / surface_json /
  quote_quantity / updated_at`，多出来的键在 `save_packaging_route()`（`:808`）里**直接丢弃**；
- `tech_app/backend/storage/da_db.py:28 _ADDED_COLUMNS` 里也没有对应的补列项。

所以今天不是"读的时候丢了"，是**从来就没存下来过** —— 连 `packaging_route.build_route()`
（`:541`）自己也不知道它是照哪一版 BOM 排的。

### 1.3 stale 判定只有三条轴，BOM 这条轴不存在

`packaging_route.py:454 _stale_reasons()` 只比三样：`steps_fingerprint`（`route_changed`）、
`surface_json`（`requirement_changed`）、`quote_quantity`（`quantity_changed`）。
BOM 重建（重解析回填尺寸/材料、人工改行、锁定行变化）之后，已确认的路线照旧
`stale=false`、`stale_reasons=[]` —— 界面上看不出"这份排产照的那版 BOM 已经不存在了"。
对照同仓 `packaging_match.py:652 load_box_match()`（比对存的快照与当前输入）与本批同族的
`packaging-cost-input-version-pinning.md`（成本侧的同一缺口），路线这一侧一个都没有。

### 1.4 确认动作冻结的是一组"不含输入版本"的指纹

`packaging_route.py:592 confirm_route()` 的幂等判定（`:611-616`）与冻结快照（`:618-632`）
只认 `steps_fingerprint` / `surface_json` / `quote_quantity`。BOM 变过、工序没变时，
"重复确认"会**原样返回旧快照**（连版本号都不动），等于把一个"照旧 BOM 排的"结论
继续当成当前有效版本冻结着。

### 1.5 顺带：当前 BOM 读不到时会怎样

`load_route()` 里 `_bom_mod.load_bom()`（`:497`）没有保护：BOM 侧一旦抛错，读路线接口整体
失败；即使不抛错，返回的空 `generated_at` 读起来也像"路线没有上游版本"，而不是
"现在比较不了"。本批要求与成本侧同一口径：**"比较不了" ≠ "变了"**，必须显式披露。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/storage/da_schema.sql` + `tech_app/backend/storage/da_db.py:28 _ADDED_COLUMNS`
   - `wip_packaging_process_route` **新增列** `source_versions_json TEXT`；
   - `wip_packaging_process_route_version` **新增列** `source_versions_json TEXT`；
   - 新库由 schema 给，老库靠既有幂等补列 `_add_missing_columns()`（`da_db.py:176`）补；
     **只加列**，不许改类型、不许删列、不许重建表、不许动既有列。
2. `tech_app/backend/services/packaging_route.py`
   - 新增模块级纯函数 `bom_input_hash(rows) -> str`：对 BOM 行的**规范形**做哈希
     （范式照 `packaging_parts._record_hash`：`sha256_hex(canonical_json(json_safe(...)))`）；
     输入前**按稳定键排序**（同一批行换个顺序必须是同一个 hash），空输入给 `""`
     （"没有 BOM"不是一版内容）。
   - `build_route()`：在**排产那一刻**读 `da_repo.load_packaging_bom()`（它已经在读，`:559`）
     并**新增** `source_versions`，五项逐字记下：
     `bom_version`（BOM 的 `generated_at`）、`bom_hash`（`bom_input_hash(bom_rows)`）、
     `bom_item_total`（`len(bom_rows)`）、`engine_version`（BOM 的 `engine_version`）、
     `box_type_code`（BOM 的 `box_type_code`；与被排路线的盒型不一致时**以 BOM 为准**记录事实）。
     这一份随主表落库。
   - `load_route()`：
     - `source_versions` = **落库的那一份**（`source_versions_json` 解析后原样返回）；
       历史路线（没有这一列 / 列是空的）→ 给 `{}`，**不许**用当前 BOM 现取值兜上去；
     - **新增** `bom_unavailable`（键**必须存在**，正常给 `{}`）：当前 BOM 读不到
       （抛异常 / `da_repo.load_packaging_bom` 读了没行）时给
       `{"code": "bom_unavailable", "reason": "<异常类名或空>"}`；
     - 当前 BOM 的身份：`bom_hash` 用 `bom_input_hash(da_repo.load_packaging_bom()` 读到的行`)` 算
       （与 `build_route()` 同一个来源），`bom_version` 取 `load_bom()["generated_at"]`；
     - **新增** stale 原因（进既有的 `stale_reasons`，与三条既有原因同构、固定顺序、去重）：
       - `bom_rebuilt`：存的 `bom_hash` 非空，且 ≠ 当前 BOM 的 `bom_input_hash()`；
       - `provenance_missing`：路线存在（`built=true`）但没存 `source_versions`；
       - 这两条的判定**不许**放进既有 `_stale_reasons()` 开头 `if not versions: return []` 的
         早退分支里 —— 还没确认过的路线同样要报（红测 F6）；
       - `stale = bool(stale_reasons)`；**不许**把原因折成一个布尔、也不许只报"变了"；
     - **不许**给"还没排过路线"的路径（`row` 为空、`built=false`）加 `provenance_missing`
       （"还没排"不是"过期"）；
     - `bom_unavailable` 非空时**不许**给 `bom_rebuilt`（比较不了 ≠ 变了）。
   - `confirm_route()`：确认（含幂等早退）前先要求主表**有存储的 BOM 来源**且与当前 BOM 一致：
     - 没有来源 → `RouteError(..., 409, "route_bom_provenance_missing")`；
     - 来源与当前 BOM 对不上（`bom_input_hash()` 不同）→ `RouteError(..., 409, "bom_rebuilt")`；
     - 通过后才走既有判定；早退的幂等条件**追加**一条"存的 `bom_hash` == 当前 `bom_input_hash()`"；
     - 冻结快照 `append_packaging_route_version()` 的记录**新增** `source_versions`
       （= 主表那一份），落 `source_versions_json`；
     - `route_versions()` 返回的每条**新增** `source_versions`（解析后；历史快照给 `{}`）。
   - 一切失败/拒绝照旧不改正文：路线、工序、历史快照一个字都不许被本批的路径改写。
3. `tech_app/backend/storage/da_repo.py`
   - `_PACKAGING_ROUTE_COLUMNS` **追加** `source_versions_json`；
     `save_packaging_route()` 把 `route["source_versions"]` 序列化进该列
     （写法与 `stale_reasons` 一致）；`load_packaging_route()` 照旧原样读回；
   - `append_packaging_route_version()` / `packaging_route_versions()` 同上
     （写法与 `steps_json` 一致）。
4. `tech_app/backend/main.py`：路线读路由（`get_requirement_packaging_route`，`:7184`）与
   版本路由原样带出 `source_versions` / `bom_unavailable` / `stale` / `stale_reasons`
   （键名不变、路由形状不变、多一层包装也不行）。
5. 前端（技术工艺页路线段）：`stale` 为真时显示"排产照的 BOM 已变，请重算路线"，
   并列出 `stale_reasons` 对应的人话；`bom_unavailable` 非空时显示"当前读不到 BOM，
   无法核对版本"（不许说成"没有上游版本"）。**不许**继续把这份路线显示成"当前有效"。

## 3. 禁止事项

- 不许把 `stale` 变成拒绝：读路线照旧读得出来、工序照旧返回（本批只要求**标记**；
  唯一的"拒绝"是 §2.2 的 `confirm_route()`，且它是既有"不合法不许确认"的同类）。
- 不许在读接口里顺手重排路线（重排是 `build_route()` 的动作）。
- 不许用当前 BOM 的值兜 `source_versions`：记不下来就给 `{}` 并报 `provenance_missing`，
  绝不编一个"看起来对"的版本。
- 不许把"比较不了"（BOM 读不到）当成"变了"或"没变"。
- 不许改既有键与裁决：`built` / `box_type_code` / `confirm_route()` 的两条既有 409
  （`route_not_found` / `route_not_confirmable`）/ 三条既有 stale 原因
  （`route_changed` / `requirement_changed` / `quantity_changed`）/ `steps` / `gaps` /
  `stats` 的口径逐字不变。
- 不许删改任何既有路线行、工序行与历史版本快照；本批只**加列**。
- 不许改 `packaging_route.ENGINE_VERSION`、`PROCESS_CATALOG`、`PROCESS_ALIASES`、
  `HARD_ORDER_CHAIN`、`validate_order()` 的判据。
- 不许改成本侧（`packaging_cost.py`）、BOM 侧（`packaging_bom.py`）、盒型匹配侧
  （`packaging_match.py`）的任何文件 —— 本批只碰路线。
- 不许改 `tests/` 下任何既有文件（含 `test_packaging_process_route_red.py`、
  `test_packaging_quote_close_loop_red.py`、`test_packaging_drawing_flow_red.py`）；
  本批红测是新增文件。
- 不许连线上 PG / SQLite 生产库、不许发 HTTP、不许写业务数据；本批红测全部离线
  （假仓库 + 纯函数）。
- 不许 commit / push / tag / Release / 部署（本批交付到"红测转绿 + 不回归"为止）。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_route_bom_version_pinning_red -v
# E 组（3 条）：算的时候记下
#   E1 bom_input_hash 存在、纯函数、行序无关、内容不同值不同、空输入给 ""
#   E2 build_route() 落库的 source_versions 五项 = **排产那一刻**那一版 BOM（不是读接口那一刻）
#   E3 build_route() 照旧把工序整体替换落库、审计照旧写（护栏）
# F 组（6 条）：读的时候不现取 + 输入变了要报
#   F1 存的 bom_hash ≠ 当前 BOM → stale=true、原因含 bom_rebuilt、source_versions 是**存的**那份
#   F2 只有 generated_at 变了、BOM 行没变 → 不报 bom_rebuilt，且 bom_version 仍是存的那份
#   F3 历史路线没有来源 → source_versions 给 {}、原因含 provenance_missing、不许拿当前 BOM 兜
#   F4 当前 BOM 读不到 → bom_unavailable 非空、不许给 bom_rebuilt、source_versions 仍是存的
#   F5 未排过路线的路径逐字不变且不许给 provenance_missing（护栏）
#   F6 还没确认过的路线同样要报 bom_rebuilt（不许被 if not versions 早退吞掉）
# G 组（3 条）：确认动作冻结的是"照哪一版输入"
#   G1 BOM 未变时重复确认仍然幂等、不追加版本（护栏）
#   G2 BOM 变过之后确认被拒（bom_rebuilt），且一个版本快照都不追加（红）
#   G3 历史路线（没来源）确认被拒（route_bom_provenance_missing）（红）
# H 组（2 条）：快照与读回都带来源
#   H1 冻结快照里带 source_versions（= 主表那份）（红）
#   H2 route_versions() 读回的每条带 source_versions（历史快照给 {}）（红）
# 现状：E1 E2 F1 F2 F3 F4 F6 G2 G3 H1 H2 红（11 条），E3 F5 G1 绿（3 条护栏）
# 不回归（既有路线口径与相邻批次）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_process_route_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_close_loop_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red
```

F2 的口径说明（避免实现方写歪）：**指纹为准**。`generated_at` 只是 BOM 那批行的落库时间，
行内容没变就不算"输入变了"；反过来，行内容变了（即使 `generated_at` 因为秒级精度相同）
必须报 `bom_rebuilt`。所以比对的是 `bom_input_hash()` 而不是 `generated_at` 字符串。

## 5. 兼容与迁移（部署影响，必须知道）

- 本批上线后，34 上**已经存在**的路线行没有 `source_versions_json`：
  - 读：`source_versions={}` + `stale_reasons` 含 `provenance_missing`（路线照旧显示，
    界面按 §2.5 提示"这个来源是历史数据，请重算一次"）；
  - 确认：`409 route_bom_provenance_missing` —— 旧路线必须**先重算**（既有入口 `build_route`）
    才能重新确认。这是有意的：确认的语义就是"冻结我照的那一版输入"，没有来源就不许冻结。
    重算不需要任何新入口、不丢历史版本快照（`wip_packaging_process_route_version` 只增不改）。
- 不做数据回填（不许猜旧路线当时照的是哪一版 BOM）。回填会把"未知"伪装成"已知"，
  正是本批要消除的东西。

真机复验（实现方做完、且部署后）：

```
POST /api/projects/{pid}/requirement/packaging-route        # 排一次 → 落 source_versions_json
POST /api/projects/{pid}/requirement/packaging-bom          # 重建 BOM（内容变了）
GET  /api/projects/{pid}/requirement/packaging-route        # stale=true、stale_reasons 含 bom_rebuilt，
                                                           # 且 source_versions 仍是排产那一刻那一版
POST /api/projects/{pid}/requirement/packaging-route/confirm # 409 bom_rebuilt（先重算再确认）
```

## 6. 落地状态

（已实现）（`packaging-route-bom-version-pinning` 9-22 落地。路线新增 `source_versions_json` 列
（主表 + 版本快照），`build_route()` 在排产那一刻记下 BOM 的五项身份（版本 / 内容指纹 / 行数 /
引擎 / 盒型），`load_route()` 只回**存的**那一份并新增 `bom_unavailable` 与两条新 stale 原因
（`bom_rebuilt` / `provenance_missing`），`confirm_route()` 先核对输入版本再确认，`route_versions()`
每条带出 `source_versions`。）

### 6.1 落点

| 契约 | 落点 |
| --- | --- |
| §2.2 `bom_input_hash(rows)` | `tech_app/backend/services/packaging_route.py`：`sha256_hex(canonical_json(json_safe(rows)))`，排序后哈希、空输入给 `""`（与成本侧 `packaging_cost.bom_input_hash` 同源） |
| §2.2 排产那一刻记 | `build_route()`：`source_versions = {bom_version, bom_hash, bom_item_total, engine_version, box_type_code}`，盒型以 BOM 为准（文档 → 成品行兜底），随主表落库 |
| §2.2 读的时候不现取 | `load_route()`：`_stored_source_versions(row)` 解析 `source_versions_json`（历史行 → `{}`）；`_current_bom_rows()` 单独读当前行，读不到 / 空 → `bom_unavailable = {"code": "bom_unavailable", "reason": "<异常类名或空>"}` |
| §2.2 两条新 stale 原因 | `_bom_drift_reasons(stored, pid, req_no) -> (reasons, unavailable)`：`provenance_missing`（没存来源）、`bom_rebuilt`（存的 `bom_hash` 非空且 ≠ 当前指纹）；在 `load_route()` 里**附加**到既有三条之后（去重），不受"有没有冻结版本"影响（F6） |
| §2.2 不许比较不了当变了 | `bom_unavailable` 非空时不给 `bom_rebuilt` |
| §2.2 `confirm_route()` | 确认前先核对：没来源 / 没指纹 → 409 `route_bom_provenance_missing`；当前 BOM 读不到 → 409 `bom_unavailable`；指纹不等 → 409 `bom_rebuilt`；幂等条件追加"存的 `bom_hash` == 当前指纹"；冻结快照新增 `source_versions` |
| §2.2 `route_versions()` | 每条新增 `source_versions`（历史快照给 `{}`） |
| §2.1 加列 | `da_schema.sql` 两张表新增 `source_versions_json TEXT`；`da_db._ADDED_COLUMNS` 老库幂等补列；`da_repo._PACKAGING_ROUTE_COLUMNS` 追加该列，`save_packaging_route()` / `append_packaging_route_version()` 序列化写入 |
| §2.4 路由透传 | `main.py` 的两条读路由原样把 `route` / `versions` 交出去，`source_versions` / `bom_unavailable` / `stale` / `stale_reasons` 自动带上，键名与形状未改 |
| §2.5 前端 | `tech_app/frontend/requirement-confirm.js`：`PR_STALE_LABELS` 补 `bom_rebuilt` / `provenance_missing` 两句人话，`bom_rebuilt` 时在 stale 行追加"（排产照的 BOM 已变，请重算工艺路线）"；新增 `data-pr-bom-unavailable="1"` 提示"当前读不到包装 BOM，无法核对这份路线照的是哪一版"；版本快照每条显示"照的 BOM <版本>" |

### 6.2 复跑

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_route_bom_version_pinning_red
# Ran 14 tests ... FAILED (failures=1) —— 只差 F2 的一条断言，见 §6.3（红测夹具缺陷）
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_process_route_red \
    tests.test_packaging_quote_close_loop_red tests.test_packaging_parametric_bom_red \
    tests.test_packaging_parts_extraction_red
# Ran 242 tests ... OK
node --check tech_app/frontend/requirement-confirm.js    # OK
```

### 6.3 已记录的偏差（红测夹具缺陷，不改测试）

红测 **F2**（`test_f2_same_rows_with_new_timestamp_is_not_drift`）的夹具把"存的指纹"写成哨兵
`OLD_BOM_HASH = "bom-hash-v1-old"`，注释也写明"真 sha256 永远不会等于它"；而同组 **F1** 要求
`bom_rebuilt`、F2 要求没有 `bom_rebuilt`，两者除 BOM 行内容外输入完全相同 —— 在"存的指纹 vs
当前指纹"这条判据下，F2 的 `assertNotIn("bom_rebuilt")` **不可能**同时成立（同组 `_load()` 只在
传了 `hash_value` 时才替换 `bom_input_hash`，F1/F2 都没传）。

Spec §4 的 F2 口径（指纹为准：`generated_at` 变了、行没变就不算 drift）**已实现**：用真指纹复跑
同一场景（`stored_versions(bom_hash=bom_input_hash(bom_rows()))` + `generated_at=NEW`）得到
`stale_reasons=[]`，而把行内容改掉（`length_mm=120`）得到 `["bom_rebuilt"]` —— 与 F1/F2 的意图
逐条一致。本批按 §2.2 实现，不改红测；F2 那一条断言作为已知的夹具缺陷挂账。

### 6.3.1 该夹具缺陷已修（`## 467`，Codex 测试侧；断言与期望值逐字未改）

`## 467` 把 F2 的夹具从哨兵指纹换成**同一批行算出来的真指纹**，
`tests/test_packaging_route_bom_version_pinning_red.py` 14 条全绿。

| 项 | 内容 |
| --- | --- |
| 改了什么 | F2 由 `stored = stored_versions()` 改成 `stored = stored_versions(bom_hash=route.bom_input_hash(bom_rows()))`（只改夹具存的那份指纹，`assertNotIn("bom_rebuilt", ...)` 与 `assertEqual(stored, result.get("source_versions"))` 逐字未动） |
| 为什么不算放宽 | F2 的两条断言与期望值一字未改。原来存的是哨兵 `OLD_BOM_HASH = "bom-hash-v1-old"`（注释写明「真 sha256 永远不会等于它」），于是「行没变」这一支在判据下**永远成立不了** —— 夹具从没把「和当前行同指纹」这个事实喂给被测代码 |
| 与 F1 的分工 | F1 存哨兵 + 行改了（`length_mm=120.0`）→ 仍要求 `bom_rebuilt`，逐字未动；F2 存真指纹 + 行没改、只有 `generated_at` 变 → 要求不报 drift。两条现在各自成立且互不遮挡 |
| 反向对照（本机实测） | F2 换回 `stored_versions()`（哨兵）→ `Ran 14 … FAILED (failures=1)`，只红 F2；还原即 `Ran 28 … OK`（含本文件 14 条） |

### 6.4 落地后的一处收紧（与 `packaging-route-box-type-drift.md` 交叉）

`provenance_missing` 的判据在落地 `## 343`（`packaging-route-box-type-drift.md`）时收紧为
「**行里带 `source_versions_json` 这一列、但没有内容**」（`_has_source_versions_column()`）。
原因：那份 Spec 的红测夹具行**整行没有这一列**（照本批之前的世界写的），而 F3 的行带这一列、值为
`null`；库升级后这一列一定在（`_add_missing_columns()` 补的），所以真实历史行照旧命中，生产口径不变。
`stale = bool(stale_reasons)` 保持不变。详见 §6.3 与 `packaging-route-box-type-drift.md` §6.3。
另外，确认动作的输入版本核对（§2.2 的 `route_bom_provenance_missing` / `bom_unavailable` /
`bom_rebuilt` 三关）落在**幂等早退之后、追加新版本之前**：幂等重复确认不冻结任何新东西，不该因为
历史行没有来源而变成 409。§2.2「确认（含幂等早退）前」按此实现为准（F/G/H 三组红测对这个顺序不敏感）。

# 规格：回传报价记录要能回答"这一版是按哪一版成本发的"和"现在成本变了没有"

状态：Spec + 红测（已实现）（原状：`load_handoff()` 直接返回库里那一行：`cost_result_version` 存着不用、当前成本不去比 —— 成本重算之后，旧回传记录照旧读起来像"当前有效"）
红测：`tests/test_packaging_handoff_input_drift_red.py`

血缘：承接 `packaging-quote-close-loop.md`（交接记录只追加、同包重发复用旧行）、
`packaging-cost-input-version-pinning.md`（成本侧"算时记下 + 读时比对"的同一条纪律）、
`packaging-bom-parts-version-binding.md`（读侧披露漂移的现成范式：清单 + 显式不可用标记）、
`dwg-semantics-agent-flow.md` §6.1（`source_versions` 逐段携带）与 §6.3（stale 只标过期，绝不改正文）。

## 0. 一句话目标

一条回传记录（报价草稿那一版）必须能回答两件事：**发它的那一刻**成本是哪一版；
**现在**成本还是不是那一版。今天第一个问题只能靠调用方自己知道去读
`cost_result_version` 这个裸键，第二个问题**没有任何答案** —— 读接口从不和当前成本比。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

### 1.1 记录里存了版本，读接口一个字都不给

`tech_app/backend/services/packaging_handoff.py:451 load_handoff()`：

```python
    row = da_repo.load_packaging_handoff(_text(project_id),
                                         _resolve_requirement_no(project_id, requirement_no))
    return dict(row or {})
```

库里的行有 `cost_result_version`（`send_to_quote()` 落库时写的，`:400`）与
`package_fingerprint`（`:399`），`load_handoff()` 原样把整行吐回去就完事：
既没有 `stale` / `stale_reasons`，也没有一个 `source_versions` 把"这一版是按哪一版成本发的"
显式说出来 —— 调用方要自己去 `package` 段里摸，或者干脆不知道要摸。

### 1.2 当前成本从不参与比对

`result_version_of()`（`:128`）是**成本结果版本**的唯一口径
（`"pkgcost-v1:<数量>:<总额>"`，报价侧据此判重，`send_to_quote()` 把它经业务桥带给一体化服务）。
同仓 `packaging_match.py:652 load_box_match()` 与
`packaging-cost-input-version-pinning.md` 的成本侧都已经用"存的版本 vs 当前输入"报 `stale`；
回传记录这一侧一个都没有：**成本重算后**（新 `result_version`），上一次回传记录照旧读得出来，
而且看不出任何"你发出去的报价是按旧成本发的"。

### 1.3 历史记录与"比较不了"这两种状态也分不出来

- 早于 `cost_result_version` 的记录（该列空）与"版本一致"在读回体上长得一样；
- 当前成本读不到（成本没算过 / 存储异常）与"成本变了"也长得一样 ——
  没有任何键能把这两件事分开。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_handoff.py`
   - 新增模块级纯函数 `handoff_stale_reasons(record, cost) -> list`（口径唯一，读接口与列表接口共用）：
     - `cost_recomputed`：`record` 的 `cost_result_version` 非空，且
       `result_version_of(cost)` 非空、与之不同；
     - `provenance_missing`：`record` 非空但 `cost_result_version` 为空；
     - `cost` 给 `{}` / `built=false`（读不到或没算过）时**只允许**给 `provenance_missing`
       （且仅当 `record` 的版本为空）—— 不许给 `cost_recomputed`（"比较不了" ≠ "变了"）；
     - 固定顺序：`cost_recomputed` → `provenance_missing`；去重。
   - `load_handoff()` 返回体**新增**四个键（键必须存在）：
     - `stale`（bool）= `bool(stale_reasons)`；
     - `stale_reasons`（list）；
     - `source_versions`（dict）：
       `{"cost_result_version": <库里的那一份，缺给 "">, "handoff_version": <库里的>,
       "package_fingerprint": <库里的>}` —— **一律来自记录**，不许用当前成本现取值兜；
     - `cost_unavailable`（dict，正常 `{}`）：当前成本读不到（抛异常 / `built ≠ true`）时给
       `{"code": "cost_unavailable", "reason": "<异常类名或空>"}`；
     - 当前成本的读取口径：`packaging_cost.load_cost(project_id, requirement_no,
       scenario=记录里的 scenario_code)` —— 只读，**不许**触发重算（重算是 `build_cost()` 的动作）；
     - **没有回传记录**（`row` 为空）时逐字返回 `{}`：不许加 `stale` / `provenance_missing`
       （"还没发过"不是"过期"）。
   - `handoff_versions()`：每条**新增**与 `load_handoff()` **逐字同一口径**的同样四个键；
     当前成本**只读一次**（按记录各自的 `requirement_no`/`scenario_code` 取一次即可，
     不许每行重算/多次调用导致 N 次读库）；既有排序（`version_no` 降序）与既有键一字不动。
2. `tech_app/backend/main.py`：`GET …/packaging-handoff`（`:7447`）与
   `GET …/packaging-handoff/versions`（`:7459`）原样带出四个新键（路由形状不变、键名不变）。
3. 前端（报价回传卡片）：`stale` 为真时显示"成本已重算，这一版回传是按旧成本发的，
   请重新回传"，并列出 `stale_reasons` 对应的人话；`cost_unavailable` 非空时显示
   "当前读不到成本，无法核对版本"（不许说成"没有成本"）。

## 3. 禁止事项

- 不许把 `stale` 变成拒绝：回传记录照旧读得出来、既有键照旧返回（本批只要求**标记**）。
- 不许自动重发、不许自动新建回传版本、不许改 `send_to_quote()` 的落库与幂等口径
  （`package_fingerprint` 判重、`_reuse_outcome()` 的形状逐字不变）。
- 不许改已落库的 `package_json` / `cost_result_version` / 历史行（交接记录只追加）。
- 不许在读接口里重算成本、重排路线、重建 BOM；不许联网、不许调模型。
- 不许用当前成本兜 `source_versions.cost_result_version`：记不下来就给 `""` 并报
  `provenance_missing`。
- 不许改 `result_version_of()` 的现有算法，不许改 `_strip_forbidden` / 交接包 10 组与
  `PACKAGE_SECTIONS`。
- 不许改 `tests/` 下任何既有文件（含 `test_packaging_quote_close_loop_red.py`、
  `test_packaging_cost_and_handoff_static_downgrade_red.py`）；本批红测是新增文件。
- 不许连线上 PG / SQLite 生产库、不许发 HTTP、不许写业务数据；本批红测全部离线
  （假仓库 + 假成本 + 纯函数）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_handoff_input_drift_red -v
# J 组（7 条）：
#   J1 存的成本版本 ≠ 当前成本 → stale=true、原因含 cost_recomputed、
#      source_versions 是**存的**那份（不是现取的）
#   J2 成本未变 → stale=false、stale_reasons=[]（护栏）
#   J3 历史记录没有成本版本 → 原因含 provenance_missing、
#      source_versions.cost_result_version 给 ""（不许拿当前成本兜）
#   J4 当前成本读不到 / 没算过 → cost_unavailable 非空、不许给 cost_recomputed、
#      source_versions 仍是存的
#   J5 没有回传记录 → 逐字 {}（护栏）
#   J6 handoff_versions 每条都带四个新键、且当前成本只读一次（红）
#   J7 既有键逐字不变（handoff_no / version_no / cost_result_version /
#      package_fingerprint / has_gaps / gap_codes / package）（护栏）
# 现状：J1 J3 J4 J6 红（4 条），J2 J5 J7 绿（3 条护栏）
# 不回归（既有回传口径与相邻批次）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_close_loop_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_and_handoff_static_downgrade_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red
```

## 5. 本批不做什么

- 不把"上游任一段 stale"汇总进交接包（那是下一步：BOM / 路线 / 成本的 stale 如何进回传闸门）；
- 不改回传时的行为，只让读侧把已经存在的事实说出来。

真机复验（实现方做完、且部署后）：

```
POST /api/projects/{pid}/requirement/packaging-cost            # 测算并回传一次
POST /api/projects/{pid}/requirement/packaging-cost            # 改输入再测算（金额变了）
GET  /api/projects/{pid}/requirement/packaging-handoff         # stale=true、stale_reasons 含 cost_recomputed，
                                                              # source_versions 仍是回传那一刻那一版
```

## 6. 落地状态

（已实现）（`packaging-handoff-input-drift-disclosure` 9-22 落地。新增模块级纯函数
`handoff_stale_reasons(record, cost)`；`load_handoff()` / `handoff_versions()` 各带四个新键
（`stale` / `stale_reasons` / `source_versions` / `cost_unavailable`），当前成本**只读**、
列表接口同一 (需求单, 场景) **只读一次**；成本面板把漂移说出来。J1 / J3 / J4 / J6 由红转绿，
J2 / J5 / J7 三条护栏仍绿。）

### 6.1 落点

| 契约 | 落点 |
| --- | --- |
| §2.1 `handoff_stale_reasons()` | `tech_app/backend/services/packaging_handoff.py`：`cost_recomputed`（记录版本非空且 ≠ 当前 `result_version_of(cost)`）/ `provenance_missing`（记录版本为空）；当前成本 `{}` / `built=false` 时只可能给 `provenance_missing`（**"比较不了" ≠ "变了"**） |
| §2.1 四个新键 | `_with_handoff_drift()`：`stale = bool(reasons)`、`stale_reasons`、`source_versions`（`cost_result_version` / `handoff_version` / `package_fingerprint`，**一律来自记录**）、`cost_unavailable`（`{"code": "cost_unavailable", "reason": "<异常类名或空>"}`） |
| §2.1 只读当前成本 | `_stored_cost()` → `packaging_cost.load_cost(pid, req_no, scenario=记录的 scenario_code)`，**不触发重算** |
| §2.1 没有记录 | `load_handoff()`：`row` 为空 → 逐字 `{}`（"还没发过"不是"过期"） |
| §2.1 列表接口 | `handoff_versions()`：排序（`version_no` 降序）与既有键不动，每条挂同一口径的四个键；`cost_cache` 保证同一 (需求单, 场景) 只读一次库 |
| §2.3 前端 | `tech_app/frontend/requirement-confirm.js`：新增 `pcHandoffDriftBanner(handoff)`，成本面板 `pcRefresh()` 顺带读 `GET …/requirement/packaging-quote` 的 `handoff`，`stale` 为真显示 `data-pc-handoff-stale`（含原因清单人话），`cost_unavailable` 非空显示 `data-pc-handoff-cost-unavailable`（"当前读不到成本，无法核对"） |

### 6.2 复跑

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_handoff_input_drift_red
# Ran 7 tests ... OK
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_quote_close_loop_red \
    tests.test_packaging_cost_engine_red
# Ran 177 tests ... OK
node --check tech_app/frontend/requirement-confirm.js    # OK
```

### 6.3 已记录的边界

- Spec §3.2 写的路由名是 `GET …/packaging-handoff` 与 `…/packaging-handoff/versions`，**实际路由**
  是 `GET /api/projects/{pid}/requirement/packaging-quote`（`main.py` 的 `PACKAGING_QUOTE_READ_PATH`）
  与 `…/packaging-quote/versions`：两条都返回 `{"handoff": …}` / `{"versions": …}`，
  新键自动带出，**没有改 `main.py`**（路由形状与键名未动）。
- 只标记不拒绝：回传记录照旧读得出来；不自动重发 / 不新建版本 / 不改 `send_to_quote()` 的幂等口径；
  `result_version_of()` 的算法一个字没改。

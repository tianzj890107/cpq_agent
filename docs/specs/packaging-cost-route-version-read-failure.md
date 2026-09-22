# 规格：成本的"当前路线版本读不到"不许被说成"工艺路线已重新确认"

状态：Spec + 红测（已实现）（原状：`packaging_cost._upstream_route_version()` 把"读不到"折成
空串，`_input_drift()`（`:2477`）拿这个空串去和存的 `route_version` 比 —— 探测一挂，
旧成本单立刻被标成"工艺路线已重新确认，请重算"；反过来存的是空串时，探测挂了与"确实没有路线"
都会静默算作"对得上"，一个原因都不报）
红测：`tests/test_packaging_cost_route_version_read_failure_red.py`

血缘：承接 `packaging-cost-input-version-pinning.md` §2.2（同一处读侧比对；本批补上它漏掉的那条轴：
BOM 侧有 `bom_unavailable`，路线侧一个都没有）、
`packaging-silent-degradation-disclosure.md`（失败 / 空 / 没有不许同形）、
`packaging-route-bom-version-pinning.md`（路线侧的同一条纪律；本批**不动路线侧**，只动成本读侧）、
`packaging-drawing-dispatch-probe-truthfulness.md`（同一病症在图纸入口分发器上）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

成本读接口对"当前工艺路线版本读不到"这件事必须**说得出来**：
既不许把它当成"路线变过"（会让 PE1 白重算一次成本），也不许静默当成"没变"
（会让一张照旧输入算的成本看起来永远有效）。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/backend/services/packaging_cost.py`：

```python
2509 def _upstream_route_version(project_id: str, requirement_no: str) -> str:
2510     """成本照着哪一版确认路线算的；读不到就空串，绝不现编。"""
2511     try:
2512         from tech_app.backend.services import packaging_route as _route_mod
2513         versions = _route_mod.route_versions(project_id, requirement_no) or []
2514     except Exception:
2515         return ""                      # ← 读失败与"确实没有"折成同一个值
2516     if not versions:
2517         return ""
2518     latest = versions[-1] if isinstance(versions[-1], dict) else {}
2519     return str(latest.get("version") or "")
```

`_input_drift()`（`:2477-2506`）只用它能拿到的那一个字符串做比对：

```python
2489     if _text(stored.get("route_version")) != _text(live_route_version):
2490         reasons.append("route_reconfirmed")
```

于是两件事同时发生：

1. **读失败被渲染成"变了"**：探测一抛异常 → `live_route_version = ""` → 与存的那一版不等
   → `stale_reasons` 含 `route_reconfirmed`（`requirement-confirm.js:840`
   → "工艺路线已重新确认"）。PE1 看到的是"路线重确认过，请重算"，但事实是**根本没读到**。
   这条口径与同一函数里 BOM 那条轴**明显不一致**：BOM 侧专门有
   `bom_unavailable`（`:2496-2504`）把"比较不了"和"变了"分开，路线侧没有对应物
   （`grep -rn "route_unavailable" tech_app/` 命中数 0）。
2. **读失败被静默当成"没变"**：存的那一版本来就是空串（历史成本单、或算的那一刻同样没读到）
   时，两边都是 `""` → 一个原因都不报，`stale=false`。"比较不了"在这里被伪装成了"没问题"。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_cost.py`
   - 读侧必须能区分三态，**不许**继续用空串同时表示两件事：读失败 / 确实没有确认版本 / 读到了版本。
     实现形状不限（例如给 `_upstream_route_version()` 配一个显式的探测同伴，或让它返回
     `(version, unavailable)`），但必须满足下面全部要求：
     - 读路线版本仍**只经** `packaging_route.route_versions()` 一个入口（不许绕到 `da_repo`）；
     - `load_cost()` 结果**新增** `route_unavailable`（键**必须存在**，正常给 `{}`）：
       读路线版本抛异常 → `{"code": "route_unavailable", "reason": "<异常类名>"}`；
       读到（哪怕一条版本都没有）→ `{}`；
     - `_input_drift()`：`route_unavailable` 非空时**不许**给 `route_reconfirmed`
       ——"比较不了" ≠ "变了"（与 BOM 侧 `bom_unavailable` 同一条纪律，逐字对齐）；
       读**成功**但当前没有任何确认版本时，口径**不变**（照旧按"对不上"报
       `route_reconfirmed`：`route_versions()` 只增不改，存量成本单对不上就是真的对不上）；
     - `source_versions` 照旧是**存的**那一份（`source_versions_json`），读失败时
       **不许**拿任何现场值覆盖、也不许改成 `{}`；
     - `stale` 只由真实原因决定：读失败时若 BOM 侧没有真实变化，`stale` 必须是 `false`；
     - `bom_unavailable` / `bom_rebuilt` / `provenance_missing` / 未算过路径
       （`built=false`）的口径逐字不变。
2. 前端 `tech_app/frontend/requirement-confirm.js`（成本面板实际所在文件 `pcPanel()`）
   - 新增 `route_unavailable` 的**独立**横幅（稳定钩子 `data-pc-route-unavailable`），
     文案照 `pcBomUnavailableBanner()` 的口径："暂时读不到当前工艺路线版本（<reason>），
     无法判断这份成本是否还跟得上；这不代表输入没变。"；
   - **不许**把 `route_unavailable` 塞进 `PC_STALE_REASONS`（那是"变了"的人话表，
     读不到不是"变了"）；
   - 既有 `PC_STALE_REASONS` / `pcStaleBanner()` / `pcBomUnavailableBanner()` 逐字不变。

## 3. 禁止事项

- 不许改 `packaging_cost.bom_input_hash()` / `_input_drift()` 里 BOM 那两条轴的行为与字段名。
- 不许改 `packaging_route.py` / `da_repo.packaging_route_versions()` / 路线读接口的任何文件
  —— 本批只改成本**读侧**怎么说话（路线侧缺什么由 `packaging-route-bom-version-pinning.md` 管）。
- 不许把 `route_unavailable` 折进 `bom_unavailable`（两条轴各自独立披露）。
- 不许在 `load_cost()` 里现算路线 / 现写盘 / 调模型 / 联网（本批只改"怎么说"）。
- 不许改 `compute_project()` 在**算的那一刻**记 `route_version` 的口径
  （`_upstream_route_version()` 在计算侧仍可用；本批只约束读侧）。
- 不许改 `tests/` 下任何既有文件（含 `test_packaging_cost_input_version_pinning_red.py`、
  `test_packaging_cost_engine_red.py`、`test_packaging_route_bom_version_pinning_red.py`）；
  本批红测是新增文件。
- 不许连线上 PG / SQLite 生产库、不许发 HTTP、不许写业务数据；本批红测全部离线
  （假仓库 + 打桩 `packaging_route.route_versions`）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_route_version_read_failure_red -v
# N 组（10 条）：
#   N1 存的 route:v1、读路线抛异常 → 不许报 route_reconfirmed；route_unavailable.code
#      必须是 route_unavailable 且 reason 带异常类名（红）
#   N2 存的 route:v1、正常读到 route:v2 → route_reconfirmed 照旧（护栏）
#   N3 存的是空串、读路线抛异常 → route_unavailable 非空、不许报 route_reconfirmed（红）
#   N4 正常读到、版本一致 → route_unavailable 必须是 {}（键必须存在）（红）
#   N5 正常读到、当前一条版本都没有、存的也是空串 → route_unavailable 给 {}、
#      不许报 route_reconfirmed（"确实没有" ≠ "读不到"）（护栏）
#   N6 读路线抛异常、BOM 没变 → stale 必须是 false（不许因为读不到就标"过期"）（红）
#   N7 既有键与 BOM 侧披露逐字不变（bom_unavailable / source_versions）（护栏）
#   N7b BOM 那一条轴照旧独立报：bom_rebuilt 照旧、BOM 读不到照旧给 bom_unavailable，
#      且此时不许报 route_reconfirmed（两条轴不许合并）（护栏）
#   N8 前端源码守卫：必须有 data-pc-route-unavailable 独立横幅，
#      且 route_unavailable 不在 PC_STALE_REASONS 里（红）
#   N9 读路线抛异常时 source_versions 仍是**存的**那一份（不许被现场值覆盖）（护栏）
# 现状：N1 N3 N4 N6 N8 红（5 条），N2 N5 N7 N7b N9 绿（5 条护栏）
# 不回归（成本读侧与相邻批次的既有口径）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_input_version_pinning_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_engine_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_route_bom_version_pinning_red
```

真机复验（实现方做完、且部署后）：

```
GET /api/projects/{pid}/requirement/packaging-cost
# 正常：{"cost": {..., "stale": false, "route_unavailable": {}}}
# 路线读服务异常时：{"cost": {..., "stale": false,
#                   "route_unavailable": {"code": "route_unavailable", "reason": "..."}}}
```

## 5. 落地状态

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_route_version_read_failure_red
# Ran 10 tests ... OK（N1 / N3 / N4 / N6 / N8 由红转绿；N2 / N5 / N7 / N7b / N9 五条护栏仍绿）
```

读侧现在把"读不到当前路线版本"与"确实没有确认版本"分开：探测三态走 `_route_probe()`，
`load_cost()` 结果新增 `route_unavailable`，`_input_drift()` 在 `route_unavailable` 非空时
不给 `route_reconfirmed`。

### 5.1 落点

| 契约 | 落点 |
| --- | --- |
| §2.1 探测三态 | `tech_app/backend/services/packaging_cost.py`：`_route_probe(project_id, requirement_no) -> (version, unavailable)` —— 读到了 `("route:v3", {})` / 确实没有 `("", {})` / 读挂 `("", {"code": "route_unavailable", "reason": "<异常类名>"})`；只经 `packaging_route.route_versions()` 一个入口（不绕 `da_repo`） |
| §2.1 计算侧口径不动 | `_upstream_route_version(project_id, requirement_no, *, probe=False)`：缺省仍返回那一版字符串（读挂 → `""`），计算侧 `source_versions` 逐字不变；`probe=True` 才给二元组 |
| §2.1 新增键 | `load_cost()`：**两条出口都挂** `route_unavailable`（没算过那条路径给 `{}`，键必须存在） |
| §2.1 不许折成"变了" | `_input_drift(..., route_unavailable=None)`：`route_unavailable` 非空 → 跳过 `route_reconfirmed` 比对；读**成功**但当前无确认版本 → 口径不变（照旧按"对不上"报） |
| §2.1 `source_versions` | 仍是 `source_versions_json` 那一份（`result["source_versions"] = stored`），探测失败不覆盖、不改 `{}` |
| §2.1 BOM 轴 | `bom_unavailable` / `bom_rebuilt` / `provenance_missing` / `built=false` 逐字不变（两条轴各自独立披露，未合并） |
| §2.2 前端 | `tech_app/frontend/requirement-confirm.js`：新增 `pcRouteUnavailableBanner(record)`（稳定钩子 `data-pc-route-unavailable`，文案与 `pcBomUnavailableBanner()` 同口径），面板按 `pcStaleBanner` → `pcBomUnavailableBanner` → `pcRouteUnavailableBanner` 顺序渲染；**没有**动 `PC_STALE_REASONS` |

### 5.2 复跑（不回归）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_input_version_pinning_red \
    tests.test_packaging_cost_engine_red tests.test_packaging_route_bom_version_pinning_red
# Ran 102 tests, 1 failure = `test_packaging_route_bom_version_pinning_red::F2`
#（## 356 已挂账的夹具哨兵指纹缺陷，与本批无关，逐字未动）
node --check tech_app/frontend/requirement-confirm.js    # OK
```

### 5.3 已记录的边界

- 读侧路线版本仍会再探一次（`_route_probe()` 内一次 `route_versions()`；`_upstream_route_version()`
  缺省路径本身也探一次）——为了让既有打桩缝（`cost._upstream_route_version`）继续生效而刻意保留的
  双读，只读不写；代价是路线版本被读两次，本批不改调用形状。
- 只披露不重算：`load_cost()` 不现算路线 / 不写盘 / 不调模型 / 不联网；未 push / 未建 MR /
  未 tag / 未部署 / 未连库 / 未写生产数据。

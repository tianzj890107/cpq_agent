# 规格：成本面板上说得出「读不到业务部件清单」——它与「清单没换版」不许同形

依赖：`docs/specs/packaging-business-parts-version-pinning.md` §2.3（成本侧新增
`business_parts_unavailable`，键**必须存在**、正常给 `{}`、读挂时带 `code` 与 `reason`（异常类名）；
"读不到清单"与"存的没有版本"都**不报 drift**）、
同 Spec §2.4（当时只加了 `business_parts_reimported` 那句人话）、
`docs/specs/packaging-cost-route-version-read-failure.md` §2.2（同一条纪律的先例：路线那条轴的
读失败**不许**混进 `PC_STALE_REASONS` 那张"变了"的人话表，要自己一条 banner）、
`docs/specs/packaging-silent-degradation-disclosure.md`（"读不到 / 没有 / 没变"三者不许同形）。

状态：Spec + 红测（已实现）（原状：`packaging_cost.load_cost()` 的两条返回分支**都**给
`business_parts_unavailable`（`built=False` 早返回 `:2689` 给 `{}`；主路径 `:2720` 由
`_business_parts_drift()` 填），而 `tech_app/frontend/requirement-confirm.js` 里
`business_parts_unavailable` **0 处引用**（`grep -c` 实测）：
① 清单**读不到**时，成本面板上只看得到 `stale=false` / `stale_reasons=[]` —— 与"清单没换版"**
一模一样**，而真相是"这一版成本算的时候用的是几何零件，且这次判不了清单有没有换版"；
② 已有的 `pcBomUnavailableBanner()` / `pcRouteUnavailableBanner()` 两条轴都各自有一条 banner
（`data-pc-bom-unavailable` / `data-pc-route-unavailable`），第三条轴（业务部件清单）
**没有**自己的落点；
③ 后端那句 `code`（`business_parts_unavailable`）与 `reason`（异常类名）在界面上 0 处 ——
运维/工艺看不出是"读挂了"还是"真没换版"）
红测：`tests/test_packaging_cost_business_parts_read_failure_panel_red.py`
行号基线：HEAD `47f5356`

## 0. 一句话目标

业务部件清单读不到时，成本面板上多一条"暂时读不到业务部件清单"的告警，并说清
"这不代表没换版"；读得到时一个节点都不多。

## 1. 现状缺口（代码级）

1. `pcPanel()`（`requirement-confirm.js:1253` 一带）只渲了 BOM（`pcBomUnavailableBanner`）/
   路线（`pcRouteUnavailableBanner`）/ 规则快照（`pcRuleSnapshotBanner`）三条读失败 banner，
   **没有**业务部件清单那条轴；
2. `PC_STALE_REASONS`（`:1219`）是"变了"的人话表：`business_parts_reimported` 在里面，而
   "读不到"进去会变成假警报 —— 所以它必须像路线那条轴一样**自己一条 banner**；
3. `business_parts_unavailable.code` / `.reason` 在界面上 0 处。

## 2. 契约

### C1 两个纯函数（`requirement-confirm.js` 的成本面板 IIFE 内，`pcRuleSnapshotBanner()` 之后；体内无 DOM / `fetch(` / `storage`）

- `pcBusinessPartsUnavailable(record)` → `{state, code, reason, headline}`：
  - `value` = `record.business_parts_unavailable`；
  - `state` 闭集只有 `"unavailable"` / `"ok"` / `"unknown"`，判据顺序固定：
    1. `value` **不是对象**（`undefined` / `null` / 字符串 / 数组 / 数字）→ `unknown`
      （老载荷没有这一栏：**不许**当成"清单没换版"，也不许当成"读不到"）；
    2. `value` 是**空对象** → `ok`（后端规定正常给 `{}`）；
    3. `value` 非空 → `unavailable`；
  - `code`：`value.code` 逐字 trim；空 / 缺失时兜底 `business_parts_unavailable`（**不许**留空）；
  - `reason`：`value.reason` 逐字 trim（异常类名），可空；
  - `headline`：
    - `unavailable` → `暂时读不到业务部件清单：这一版成本只按几何零件算，而且判不了清单有没有换版 —— 这不代表没换版。`
    - `ok` / `unknown` → `""`（没有事实就不编话）。
  - 任何输入都不抛错。
- `pcBusinessPartsBanner(record)` → `""` 或一段 HTML（**落点就是这里**，`pcPanel()` 只负责拼进去）：
  - `state !== "unavailable"` → `""`；
  - 否则 `<div class="pc-warning" data-pc-business-parts-unavailable="<code>">` + `headline`
    + （`reason` 非空才接的 `（<reason>）`）+ `</div>`，插值全部过 `pcEsc()`；
  - `reason` 为空**不许**留空 `（）`。

### C2 面板接线（`pcPanel()`）

- 新增 `${pcBusinessPartsBanner(record)}`（**一处**调用），插在 `${pcRouteUnavailableBanner(record)}`
  之后、`${pcRuleSnapshotBanner(record)}` 之前 —— 与另外两条"读不到"的轴并列；
- 既有四条 banner（`pcStaleBanner` / `pcBomUnavailableBanner` / `pcRouteUnavailableBanner` /
  `pcRuleSnapshotBanner`）、裁决条（`data-pc-readiness`）、包材绑定条
  （`data-pc-content-binding`）与 `pcAuditBlock()` **一字不动**。

### C3 冻结面

- 判据只有一个来源：后端 `business_parts_unavailable`（前端**不许**按 `stale` / `stale_reasons`
  或零件文档 / BOM 的可用性反推）；
- 不许把它写进 `PC_STALE_REASONS`（"读不到"不是"变了"：那条轴比不了就不报 drift，
  `_business_parts_drift()` 的口径一个字不动）；
- 不改后端、不加接口 / 依赖、`pcPanel()` 里不新增请求、不改 `index.html`；
- 不改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言。

## 3. 允许修改范围

1. `tech_app/frontend/requirement-confirm.js`：C1 两个纯函数 + C2 的一处调用；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许把"读不到清单"说成"清单没换版"（`ok` 与 `unknown` 都**不**给话）；
- 不许把 `unknown`（老载荷）当成 `unavailable` 报假警报；
- 不许在 `reason` 为空时留空 `（）`；不许改写后端给的 `code` / `reason`；
- 不连 PG / 34、不写生产数据、不 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_business_parts_read_failure_panel_red -v
node --check tech_app/frontend/requirement-confirm.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_parts_version_pinning_red \
  tests.test_packaging_cost_route_version_read_failure_red tests.test_packaging_cost_gaps_red \
  tests.test_packaging_cost_readiness_panel_red tests.test_packaging_cost_content_binding_panel_red \
  tests.test_packaging_cost_engine_red tests.test_packaging_cost_red_closure_red
```

## 6. 已记录的边界

1. 只**显示**读失败这一件事：不自动重读、不自动重算成本、不把"读不到"升级成阻断
   （严重度分层归 `packaging-cost-readiness-severity-layering.md`）；
2. 读得到时的**版本漂移**仍由 `PC_STALE_REASONS` 里那句 `business_parts_reimported` 承担（本批不动）；
3. 清单**内容**的披露（哪一件没绑几何、权威尺寸等）仍归 2.1 的 BOM / 业务部件面板，本批不越界；
4. 成本面板仍只在包装需求单上挂载（既有条件不动）。

## 7. 落地状态（2026-09-22，Codex 实现）

**实现前红基**（`git stash push -- tech_app/frontend/requirement-confirm.js` 后的原文）：

```
Ran 17 tests ... FAILED (failures=12, errors=1)
```

13 红 / 4 绿 —— 绿的四条是守卫（`C3` 既有四条 banner 与 `data-pc-readiness` /
`data-pc-content-binding` 一字未动、`D3` `PC_STALE_REASONS` 那张"变了"的人话表未动、
`D4` `index.html` 未动、`D5` `node --check` 通过）；
13 红 = `A1`–`A5`、`B1`–`B4`（两个纯函数根本不存在）+ `C1`、`C2`（锚点拿不到 → ERROR）、
`D1`、`D2`。

**实现后**：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cost_business_parts_read_failure_panel_red -v
Ran 17 tests ... OK
node --check tech_app/frontend/requirement-confirm.js   # 退出码 0
```

落点（只改了 `tech_app/frontend/requirement-confirm.js` 一个文件）：

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §C1 事实 | `pcBusinessPartsUnavailable(record)`（成本面板 IIFE 内，`pcHandoffDriftBanner()` 之前） | 三态闭集：那一栏不是对象（含老载荷缺键）→ `unknown`（**不给话**，不许当成"没换版"也不许当成"读不到"）；空对象 → `ok`；非空 → `unavailable`，`code` 空 / 缺失时兜底 `business_parts_unavailable`、`reason` 逐字可空、`headline` 那句逐字 |
| §C1 渲染 | `pcBusinessPartsBanner(record)` | `state !== "unavailable"` → `""`；否则 `data-pc-business-parts-unavailable="<code>"` + `headline` + （`reason` 非空才接的 `（<reason>）`），插值全过 `pcEsc()`，`reason` 为空不留空 `（）` |
| §C2 | `pcPanel()`：`${pcRouteUnavailableBanner(record)}` 之后、`${pcRuleSnapshotBanner(record)}` 之前插入 `${pcBusinessPartsBanner(record)}` | 一处调用；既有四条 banner、裁决条、包材绑定条与 `pcAuditBlock()` 一字未动；`pcPanel()` 里无 `fetch(` |
| §C3 | 无 | 判据只读 `record.business_parts_unavailable`（两个函数体内不出现 `stale` / `stale_reasons`）；未改后端（`_business_parts_drift()` 的"比不了就不报漂移"口径一字未动）、未加接口 / 依赖、未改 `index.html` |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_parts_version_pinning_red \
  tests.test_packaging_cost_route_version_read_failure_red tests.test_packaging_cost_gaps_red \
  tests.test_packaging_cost_readiness_panel_red tests.test_packaging_cost_content_binding_panel_red \
  tests.test_packaging_cost_engine_red tests.test_packaging_cost_red_closure_red
Ran 176 tests ... OK
```

边界（与 §6 一致，实现如约未越）：只显示读失败、不自动重读 / 不自动重算、不升级成阻断；
读得到时的版本漂移仍由 `PC_STALE_REASONS` 里那句 `business_parts_reimported` 承担；
清单内容的披露仍归 2.1 的 BOM / 业务部件面板；成本面板仍只在包装需求单上挂载。

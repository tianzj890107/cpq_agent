# 规格：CAD 平面图「读不到」不许冒充「这份图纸没有图元」

依赖：`docs/specs/packaging-parts-read-failure-empty-state.md`（左栏同一套「404 与 5xx 分家 +
`read_problem` 空文档形状 + 具名纯函数出文案」的口径）、
`docs/specs/packaging-business-parts-read-failure-note.md`（业务部件那一侧的同一套口径）、
`docs/specs/packaging-cad-plan-drawing-coordinates.md`（`PACKAGING_CAD_PLAN_NO_COORDS` 与
「一件一个矩形」的渲染粒度，本批**不动**）。

状态：Spec + 红测（已实现）（原状：`app.js:1952-1967` 的 `loadPackagingCadPlan()` 把所有失败
折成同一个 `throw` + 原始 message 写进右栏 —— 404（路由未上线）与 500（后端读不到）逐字不可分，
断网时中文界面里直接出现浏览器的英文原生文本；`renderPackagingCadPlan()`（`:1893`）拿不到任何
「读失败」迹象，只能回落成「这份图纸还没有可显示的 CAD 图元。」）
红测：`tests/test_packaging_cad_plan_read_failure_red.py`
行号基线：HEAD `ae72b1e`

## 0. 一句话目标

平面图读不到时，右栏要说清「暂时读不到（HTTP n / 网络错误），这不代表这份图纸没有图元」，
并且 404（还没生成 / 路由未上线）与 5xx / 网络错误**分开说**；既有三种空态文案一句不改。

## 1. 现状缺口（代码级）

### 1.1 所有失败一个样子（`app.js:1952-1967`）

```js
async function loadPackagingCadPlan() {
  if (!packagingCadPlanApplies()) return null;
  const host = packagingCadPlanViewer();
  if (host) host.innerHTML = `<div class="view-3d-placeholder">正在读取 CAD 平面图…</div>`;
  try {
    const res = await fetch(packagingCadPlanEndpoint());
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(`读取 CAD 平面图失败（HTTP ${res.status}）`);   // ← 404 与 500 同一句
    return renderPackagingCadPlan(payload);
  } catch (error) {
    if (host) host.innerHTML = `…${esc(String((error && error.message) || error))}…`;  // ← 断网时是英文原生文本
    return null;
  }
}
```

- 404（端点未上线 / 非工作流项目）与 500（后端读不到零件文档）在源码上是**同一个** `throw`；
- `fetch` 抛异常（断网 / DNS）时 `error.message` 是浏览器原生文本（`Failed to fetch`），
  在中文界面里被逐字贴出来；
- 既有的三种空态（`PACKAGING_CAD_PLAN_EMPTY` `:1726`、gap 文案、`PACKAGING_CAD_PLAN_NO_COORDS`
  `:1729`）里**没有**「读失败」这一态 —— 读不到与「这份图纸还没有可显示的 CAD 图元」不可分。

### 1.2 同一条线上早有统一口径，只有这一处没接

- 左栏零件文档：`fetchPackagingParts()` 404 → `return null`；其它非 2xx / 网络异常 → 带
  `read_problem` 的空文档形状 + 具名纯函数 `packagingPartsPageReadProblemText()` / `packagingPartsEmptyText()`；
- 业务部件清单：`fetchPackagingBusinessParts()` 同构（`packagingBusinessReadProblemText()`）；
- 平面图这一处仍是裸 `throw`。

## 2. 契约

### C1 新增纯函数 `packagingCadPlanReadProblemText(problem) -> string`

- 体内**不得**出现 `document.` / `window.` / `fetch(` / `localStorage`（可被 `node -e` 抽出来真跑）；
- `problem.code` 去空白后为空 → 返回 `""`（没有码就等于没有「读失败」这件事）；
- `status > 0` → `暂时读不到 CAD 平面图（HTTP <status>），请稍后重试；这不代表这份图纸没有图元`
  （`<status>` 是十进制数字，逐字）；
- 其它（`status` 缺失 / `0`，即网络错误）→
  `暂时读不到 CAD 平面图（网络错误），请稍后重试；这不代表这份图纸没有图元`。

### C2 新增纯函数 `packagingCadPlanEmptyText(doc) -> string`（空态文案的唯一出处）

优先级固定三条：

1. `doc.read_problem` 是非空对象 → 返回 `packagingCadPlanReadProblemText(doc.read_problem)`
   （**读失败优先**：不许被 gap 文案或 `PACKAGING_CAD_PLAN_EMPTY` 抢先）；
2. 否则 `doc.business_parts_gap.message`（或 `doc.gap.message`）非空 → 逐字返回它（既有行为）；
3. 否则返回 `PACKAGING_CAD_PLAN_EMPTY`（既有常量，逐字不变）。

同样不许出现 `document.` / `window.` / `fetch(` / `localStorage`。

### C3 `renderPackagingCadPlan(doc)` 认 `read_problem`

- `doc.read_problem` 非空时，**第一优先**在 `#packagingCadPlanViewer` 里只显示
  `packagingCadPlanReadProblemText(doc.read_problem)` 的文案，**不**再走
  `PACKAGING_CAD_PLAN_EMPTY` / gap 文案 / `PACKAGING_CAD_PLAN_NO_COORDS` 分支；`return null` 照旧；
- 没有 `read_problem` 时，既有三个分支（有分量有坐标 → 画图；有分量没坐标 → `NO_COORDS`；
  没有分量 → gap 文案 / `EMPTY`）**逐字不变**，且「没有分量」那一条改用 `packagingCadPlanEmptyText(doc)`
  产出（无 `read_problem` 时与今天等价）；
- 渲染粒度与高亮逻辑（`packagingCadPlanComponentBox()` 先 `drawing_bbox` 再 `bbox`、
  `packagingCadPlanViewBox()` 的 y 轴翻转、点图元高亮）**一个字不改**。

### C4 `loadPackagingCadPlan()` 三态分家（不再裸 `throw`）

- **404** → 按「还没有几何证据」空态渲染（`geometry_evidence.components` 为空的空文档形状，
  走 §C2 的第 2/3 条），**不**写成读失败、**不**给 `read_problem`（与左栏 `fetchPackagingParts()`
  的 404 口径一致：端点未上线不是「读不到」）；
- **其它非 2xx（含 5xx）** → 渲染带 `read_problem = {code: "cad_plan_unavailable", status: <HTTP 码>,
  message: ""}` 的空文档形状（§C3 第一优先分支出文案）；
- **`fetch` 抛异常** → 同上，`status` 给 `0`（网络错误）；
- 三条路径的返回值仍是 `null`（既有调用方契约不变）；除这四条分支外不改 `loadPackagingCadPlan()` 的
  调用时机与 `packagingCadPlanApplies()` 前置判断。

### C5 冻结面

- `PACKAGING_CAD_PLAN_EMPTY` / `PACKAGING_CAD_PLAN_NO_COORDS` / `PACKAGING_CAD_PLAN_UNBOUND` /
  `PACKAGING_CAD_PLAN_ALIASES` / `PACKAGING_CAD_PLAN_LABEL` 的**文本逐字不变**；
- 不改后端（`read_packaging_geometry` 与 `PACKAGING_GEOMETRY_READ_PATH` 一行不动）、
  不改其它读接口的口径、不加依赖、不加接口、不改 CSS 类名与 `data-` 钩子形状；
- 不许把 `read_problem` 塞进 `geometry_evidence.components`（那是图元数组，不是错误通道）。

## 3. 允许修改范围

1. `tech_app/frontend/app.js`：新增 `packagingCadPlanReadProblemText()` / `packagingCadPlanEmptyText()`
   （放 `PACKAGING_CAD_PLAN_NO_COORDS` 常量附近），改 `renderPackagingCadPlan()` 与
   `loadPackagingCadPlan()` 两处；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许改后端、不许改 `#viewer` / `loadSTL()` 的技术侧 3D 行为、不许引入新的前端依赖；
- 不许把 `PACKAGING_CAD_PLAN_NO_COORDS` 换成「读不到」的文案（那是「有图元没坐标」，另一件事）；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cad_plan_read_failure_red -v
node --check tech_app/frontend/app.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_cad_plan_drawing_coordinates_red \
  tests.test_packaging_parts_read_failure_empty_state_red \
  tests.test_packaging_business_parts_read_failure_note_red \
  tests.test_packaging_parts_panel_red
```

## 6. 已记录的边界

1. 本批只做**读失败的三态分家与文案**：不重试、不缓存、不自动重画；
2. 404 按「还没生成」处理（与左栏一致）；服务端将来若把「项目不是工作流项目」从 404 改成别的码，
   那一档要另开一批讨论，本批不改后端；
3. `PACKAGING_CAD_PLAN_NO_COORDS`（有图元、没坐标）与「读失败」是两件事，本批只保证两者不再互相冒充。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_cad_plan_read_failure_red
# 实现前：Ran 12 tests … FAILED (failures=8)   ← 8 红：T1 T2 T3 T4 T5 T6 T7 T8
# 实现后：Ran 12 tests … OK                    ← S1–S4 四条护栏始终绿
node --check tech_app/frontend/app.js          # OK
```

| 契约 | 落点（`tech_app/frontend/app.js`） |
| --- | --- |
| §C1 读失败文案 | 新增纯函数 `packagingCadPlanReadProblemText(problem)`（放在 `PACKAGING_CAD_PLAN_NO_COORDS` 常量之后）：`code` 去空白后为空 → `""`；`status > 0` → `暂时读不到 CAD 平面图（HTTP <n>），请稍后重试；这不代表这份图纸没有图元`；否则网络错误那一句。体内无 `document.` / `window.` / `fetch(` / `localStorage` |
| §C2 空态唯一出处 | 新增纯函数 `packagingCadPlanEmptyText(doc)`：`read_problem` → `packagingCadPlanReadProblemText()`；否则 `business_parts_gap/gap.message`；否则既有常量 `PACKAGING_CAD_PLAN_EMPTY`（逐字不变） |
| §C3 渲染接读失败 | `renderPackagingCadPlan()` 在 `if (!host) return null;` 之后新增**第一优先**分支：`read_problem` 非空 → 只显示读失败文案、`currentPackagingCadPlanBox = null`、`return null`；「没有分量」那一条改用 `packagingCadPlanEmptyText(doc)`（无 `read_problem` 时与 `gap.message \|\| PACKAGING_CAD_PLAN_EMPTY` 逐字等价）；`NO_COORDS` 分支与 `packagingCadPlanComponentBox()` / viewBox / 点击高亮未动 |
| §C4 三态分家 | `loadPackagingCadPlan()` 去掉裸 `throw`：`fetch` 抛异常 → `problemDoc(0)`；`Number(res.status) === 404` → 「还没有几何证据」空文档（**不**给 `read_problem`）；其余非 2xx → `problemDoc(res.status)`；三条路径都 `return null`。空文档形状由函数内 `problemDoc(status)` 一处构造：`{geometry_evidence: {components: []}, business_parts: [], business_parts_gap: {}, built: false, read_problem: {code: "cad_plan_unavailable", status, message: ""}}` |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_cad_plan_drawing_coordinates_red \
  tests.test_packaging_parts_read_failure_empty_state_red \
  tests.test_packaging_business_parts_read_failure_note_red \
  tests.test_packaging_parts_panel_red   → Ran 48 … OK
./open-claude/.venv/bin/python -W ignore -m unittest discover -s tests -p 'test_packaging_*.py'
  → Ran 1830 … FAILED (failures=5) —— 五条全是既有挂账（bom_part_size_provenance::B3、
    parse_to_downstream_seams::B4、part_role_mapping_reaches_card::A2、quote_send_recovery::C1、
    route_bom_version_pinning::F2，各 Spec 已记账），与本批无关
```

未改后端、未改 `tests/` 下任何文件、未连 PG / 34、未写生产数据、未 push / MR / tag / Release / 未部署。

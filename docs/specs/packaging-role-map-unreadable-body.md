# 规格：角色映射正文解不出时，页面写着"每一行都有业务角色了" —— 200 但正文不可用被当成"全映射完"

状态：Spec + 红测（已实现）（原状：`tech_app/frontend/app.js:2508-2523 loadPackagingRoleMap()`
用 `const payload = await res.json().catch(() => ({}));`（`:2512`）把"正文解不出"折成 `{}`，
再走 `renderPackagingRoleMap((payload && payload.role_map) || {})`（`:2519`）—— `{}` 的
`items` 不是数组、`unbound_total` 为 0，于是 `renderPackagingRoleMap()`（`:2539-2542`）渲染出
**"每一行都有业务角色了。"** 这是这一栏能给出的最强结论，却由一个读不到的正文触发）
红测：`tests/test_packaging_role_map_unreadable_body_red.py`
行号基线：HEAD `7e81fd7`（行号只用来指路；口径以本 Spec 正文为准，不以行号为准）。

血缘：承接 `packaging-part-role-manual-mapping.md` §4.5（这一栏是"角色未映射 n 行"的唯一出口）、
`packaging-silent-degradation-disclosure.md` §2.2（`renderPackagingRoleMapUnavailable()` 的注释
自己写着"读不到**不许**显示成'每一行都有业务角色了'" —— 本批补上它漏掉的**正文一层**）、
`packaging-bom-role-unbound-note-read-failure.md`（同一天同一族：BOM 那一趟读失败）、
`packaging-business-parts-read-failure-note.md`（同一族的形状与措辞）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

HTTP 200 不等于"正文可用"。角色映射这一趟拿到的正文如果不是那个形状，页面必须说
**"这一次读不到角色映射"**，而不是 **"每一行都有业务角色了"**（后者是一个会被当成结论的界面）。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/frontend/app.js`：

```js
2508 async function loadPackagingRoleMap() {
2509   if (!currentProject) return null;
2510   try {
2511     const res = await fetch(API + packagingRoleMapUrl());
2512     const payload = await res.json().catch(() => ({}));      // ← 正文解不出 → {}
2513     if (!res.ok) {
2514       const detail = payload && payload.detail;
2515       return renderPackagingRoleMapUnavailable(               // ← 非 2xx 这条路是对的
2516         (detail && (detail.message || detail)) || (payload && payload.message)
2517         || `HTTP ${res.status}`);
2518     }
2519     return renderPackagingRoleMap((payload && payload.role_map) || {});   // ← 形状不对也照渲染
2520   } catch (error) {
2521     return renderPackagingRoleMapUnavailable("网络错误，请稍后重试");       // ← 抛异常这条路也是对的
2522   }
2523 }
```

```js
2539 function renderPackagingRoleMap(roleMap) {
...
2539   if (!total) {
2540     host.innerHTML = summary + unavailableNote
2541       + '<div class="role-map-empty">每一行都有业务角色了。</div>';   // ← 于是读不到也这么说
```

服务端这一趟每次都给三个键（`packaging_bom.role_map_status()`，`packaging_bom.py:858` 起的返回体：
`items` / `unbound_total` / `templates_unavailable` 等），并且 `main.py:6888` 外面还包一层
`{"role_map": body, ...}`。所以：

1. **"正文解不出"被折成合法空态**：`res.json()` 失败 → `{}` → `payload.role_map` 不是对象 →
   `renderPackagingRoleMap({})` → `total = 0` → **"每一行都有业务角色了。"**。
   断网重连、网关截断正文、S3/meta 文档通道抖一下都可能让 200 的正文不可用。
2. **同一个函数里三种失败只有两条被接住**：非 2xx（`:2513-2518`）与抛异常（`:2520-2521`）
   都说话，唯独"200 但形状不对"不说 —— 它甚至比前两条更危险：前两条至少不会给出结论。
3. `role_map` 缺失 / 不是对象 / `items` 与 `unbound_total` 都没有，都会被同一个 `|| {}` 吃掉，
   与"确实一行都没映射"（服务端会给 `items: []` + `unbound_total: 0`）完全同形。

## 2. 允许修改范围（实现方）

1. `tech_app/frontend/app.js`：**新增纯函数** `packagingRoleMapReadProblemText(problem)`
   （不得出现 `document.` / `window.` / `fetch(` / `localStorage`）：
   - `problem` 是非空对象且 `problem.code === "role_map_body_unexpected"` →
     `"这一次读到的角色映射正文解不出，请稍后重试；这不代表每一行都有业务角色。"`
   - 其余（`{}` / `null` / 表外 code）→ `""`
2. `tech_app/frontend/app.js:loadPackagingRoleMap()`
   - **新增一条分支**：`res.ok` 且正文形状不对 → `renderPackagingRoleMapUnavailable(
     packagingRoleMapReadProblemText({"code": "role_map_body_unexpected", "status": res.status,
     "message": ""}))`，返回值契约不变（`renderPackagingRoleMapUnavailable()` 返回 `null`）；
   - **形状不对的判据（写死）**：`payload.role_map` 不是对象，**或**是对象但 `items` /
     `unbound_total` / `templates_unavailable` **三个键一个都没有**；
   - 非 2xx 分支（`:2513-2518`，含 `HTTP ${res.status}` 兜底）与 `catch` 里的
     `"网络错误，请稍后重试"` **逐字不变**；`res.json().catch(() => ({}))` 保留；
   - 形状正常时照旧 `renderPackagingRoleMap(payload.role_map)`（含合法的 `items: []` 空态）。
3. 不许改 `renderPackagingRoleMap()` / `renderPackagingRoleMapUnavailable()` 的既有口径与文案，
   不许改 `packagingRoleMapUrl()`、`refreshPackagingParts()` 的调用顺序。

## 3. 禁止事项

- 不许把正文形状不对写成"每一行都有业务角色了" / "角色未映射 0 行" / "没有待映射的行"。
- 不许把这条失败并进 `role_unbound_templates_unavailable`（那是"读到了、但候选角色读不到"）。
- 不许改服务端 `packaging-bom/role-map` 路由与 `role_map_status()` 的返回体。
- 不许放宽成"任何 200 都当读不到"（合法的 `items: []` 空态必须照旧渲染成"每一行都有业务角色了。"）。
- 不许在函数里加重试循环 / 自动重试 / 弹窗；不许重写 host 的 `innerHTML`。
- 不许改 `tests/` 下任何既有文件（含 `tests/test_packaging_part_role_manual_mapping_red.py`）；
  本批红测是新增文件。
- 不许起服务、不许发 HTTP、不许连线上 PG / SQLite、不许写业务数据；本批红测全部离线
  （`node -e` 抽函数体执行 + 源码守卫）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_role_map_unreadable_body_red -v
# S 组（8 条）：
#   S1 纯函数：role_map_body_unexpected →
#      "这一次读到的角色映射正文解不出，请稍后重试；这不代表每一行都有业务角色。"（红）
#   S2 纯函数：{} / null / 表外 code → ""（"没有读问题"不许拼出这句话）（红）
#   S3 纯函数守卫：不碰 DOM / fetch / localStorage（红）
#   S4 源码守卫：res.ok 之后有形状检查，且把 role_map_body_unexpected 交给
#      renderPackagingRoleMapUnavailable（红）
#   S5 源码守卫：loadPackagingRoleMap() 引用了纯函数，形状不对不再直接喂给
#      renderPackagingRoleMap（红）
#   S6 护栏：非 2xx 分支（含 HTTP ${res.status} 兜底）与 catch 的"网络错误，请稍后重试"逐字不变（绿）
#   S7 护栏：renderPackagingRoleMap() 的三处文案与钩子逐字不变（绿）
#   S8 护栏：合法空态口径（Array.isArray(roleMap.items) / unbound_total）逐字不变（绿）
# 现状：S1–S5 红（5 条），S6–S8 绿（3 条护栏）
# 不回归（人工映射与 BOM 角色披露两批）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_part_role_manual_mapping_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_role_unbound_note_read_failure_red
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_parse_terminal_signal_red
node --check tech_app/frontend/app.js
```

真机复验（实现方做完、且部署后）：

```
# 2.1 → 角色映射栏：
# 1) 正常（还有未映射的行）→ "角色未映射 n 行" + 每行下拉；没有差别的提示；
# 2) 让 role-map 回 200 但正文截断/为空 → 面板当场写
#    "人工角色映射读不到：这一次读到的角色映射正文解不出，请稍后重试；这不代表每一行都有业务角色。"
#    **没有**"每一行都有业务角色了。"；
# 3) 服务端恢复正常且确实都映射完了（items: []）→ 照旧显示"每一行都有业务角色了。"。
```

## 5. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_role_map_unreadable_body_red
# 实现前：Ran 8 tests … FAILED (failures=5)   ← S1 S2 S3 S4 S5
# 实现后：Ran 8 tests … OK                    ← S6 S7 S8 三条护栏始终绿
```

| 契约 | 落点（`tech_app/frontend/app.js`） |
| --- | --- |
| §2.1 纯函数 | 新增 `packagingRoleMapReadProblemText(problem)`（`renderPackagingRoleMapUnavailable()` 与 `loadPackagingRoleMap()` 之间）：`code === "role_map_body_unexpected"` → `这一次读到的角色映射正文解不出，请稍后重试；这不代表每一行都有业务角色。`；其余（`{}` / `null` / 表外码）→ `""`。无 `document.` / `window.` / `fetch(` / `localStorage`（S3）。 |
| §2.2 形状检查 | `loadPackagingRoleMap()`：`res.json().catch(() => ({}))` 与非 2xx 分支（`payload.detail` / `detail.message \|\| detail` / `` `HTTP ${res.status}` ``）逐字保留；`res.ok` 之后新增形状判据 —— `const roleMap = (payload && payload.role_map) \|\| null;`，`shaped = roleMap 是对象 && ("items" in roleMap \|\| "unbound_total" in roleMap \|\| "templates_unavailable" in roleMap)`；`!shaped` → `const problem = {code: "role_map_body_unexpected", status: …, message: ""}` 并 `return renderPackagingRoleMapUnavailable(packagingRoleMapReadProblemText(problem))`（返回值契约不变，仍是 `null`）；形状正常 → `return renderPackagingRoleMap(roleMap)`（含合法的 `items: []` 空态）。 |
| §2.3 未动的 | `renderPackagingRoleMap()` / `renderPackagingRoleMapUnavailable()` 的文案与钩子、`packagingRoleMapUrl()`、`refreshPackagingParts()` 的调用顺序；`catch` 里 `renderPackagingRoleMapUnavailable("网络错误，请稍后重试")` 逐字不变；没有重试循环 / 自动重试 / 弹窗；没有重写 `host.innerHTML`。 |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_part_role_manual_mapping_red \
  tests.test_packaging_bom_role_unbound_note_read_failure_red \
  tests.test_drawing_flow_parse_terminal_signal_red   → Ran 59 … OK
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_bom_role_unbound_template_disclosure_red \
  tests.test_packaging_parts_panel_red \
  tests.test_packaging_authority_thumbnail_media_red   → Ran 57 … OK
node --check tech_app/frontend/app.js   → OK
```

未改任何既有测试与业务数据、未放宽任何断言、未连 PG / SQLite、未起服务、未发 HTTP、
未 push / MR / tag / Release / 未部署。

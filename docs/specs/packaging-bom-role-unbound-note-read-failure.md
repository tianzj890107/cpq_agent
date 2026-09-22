# 规格：BOM 读不到时，"候选角色暂时读不到"这句提示被**擦掉** —— 一次读失败把已披露的事实抹平

状态：Spec + 红测（已实现）（原状：`tech_app/frontend/app.js:2466-2487 refreshPackagingBomRoleUnboundNote()`
**从不检查 `res.ok`**，也把 `res.json()` 解不出 / `fetch` 抛异常一起并进 `flag = {}`；然后**无条件**执行
`if (old) old.remove();`（`:2477-2478`）—— 于是 BOM 读失败时，上一次刷新已经写在页面上的
"候选角色暂时读不到（…），请稍后重试；这不代表该盒型没有候选角色。"被**删掉**，且不补任何说明）
红测：`tests/test_packaging_bom_role_unbound_note_read_failure_red.py`
行号基线：HEAD `7e81fd7`（行号只用来指路；口径以本 Spec 正文为准，不以行号为准）。

血缘：承接 `packaging-bom-role-unbound-template-disclosure.md` §2.3（本函数就是那条 Spec 的产物：
把 `role_unbound_templates_unavailable` 说给人听，**不许**让"空下拉"没有解释）、
`packaging-silent-degradation-disclosure.md` §2.2（读不到 ≠ 没有；这里更糟——读不到会把**已经说过的
事实**回收掉）、
`packaging-part-role-manual-mapping.md` §2.8（这一栏是人工映射的唯一入口，"候选角色都在"是个会被
当成结论的界面）、
`packaging-business-parts-read-failure-note.md` / `packaging-parts-pagination-read-failure.md`
（同一天同一族的姊妹批：键名同构 `read_problem`）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

`refreshPackagingBomRoleUnboundNote()` 每次刷新都要能分清三件事：**读到了、这一趟没有候选角色披露**
（什么都不加，逐字不变）、**读到了、候选角色确实读不到**（既有那句话，逐字不变）、
**这一趟读不到 BOM**（新的一句话 + 既有那句话**不许**被擦掉）。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/frontend/app.js`：

```js
2466 async function refreshPackagingBomRoleUnboundNote() {
2471   try {
2472     const res = await fetch(API + `/api/projects/${currentProject}/requirement/packaging-bom`);
2473     const payload = await res.json().catch(() => ({}));   // ← res.ok 从未被检查
2474     const body = (payload && payload.bom) || {};          // ← 500 的 {"detail": …} 也落成 {}
2475     flag = (body && body.role_unbound_templates_unavailable) || {};
2476   } catch (error) { return null; }                       // ← 网络异常同形
2477   const old = host.querySelector("[data-role-unbound-templates-unavailable]");
2478   if (old) old.remove();                                  // ← 读失败也执行：擦掉已披露的事实
2479   if (!flag.code) return flag;                            // ← 于是"读不到"与"本来就没有"完全同形
```

三个后果：

1. **读失败会回收已经说过的事实**：500 / 网络异常 / 正文解不出时 `flag = {}`，函数仍然走到
   `old.remove()`，把上一次刷新挂上的那句"候选角色暂时读不到（…）"删掉。用户看到的是一个干净的
   角色映射栏 —— 恰好就是 `packaging-bom-role-unbound-template-disclosure.md` §2.3 要防的
   "空下拉框没有解释"。
2. **`res.ok` 不检查**：`GET …/requirement/packaging-bom` 失败时返回的是错误体（`{"detail": …}`），
   照样被当成"没有 flag"，于是失败与"这个盒型没有候选角色"同形。
3. **面板是三笔账共用的一块**：这一栏同时挂着"人工映射读不到"（`data-role-map-unavailable`）与
   本函数的两条披露；`old.remove()` 只按自己的选择器删，说明作者已经知道要精细删除 ——
   但读失败时**没有**任何一笔账留下来。

## 2. 允许修改范围（实现方）

1. `tech_app/frontend/app.js`：**新增纯函数** `packagingRoleUnboundReadProblemText(problem)`
   （不得出现 `document.` / `window.` / `fetch(` / `localStorage`）：
   - `problem.code === "bom_unavailable"` 且 `Number(problem.status) > 0` →
     `"这一次读不到 BOM（HTTP <status>），候选角色的披露也读不到；请稍后重试，这不代表这些行都有候选角色。"`
   - `problem.code === "bom_unavailable"`，没有 `status`（或 `0`）→ 同一句，括号里是 `网络错误`
   - `problem.code === "bom_body_unexpected"` →
     `"这一次读到的 BOM 正文里没有盒型信息，候选角色的披露读不到；请稍后重试，这不代表这些行都有候选角色。"`
   - 其余（`{}` / `null` / 表外 code）→ `""`
2. `tech_app/frontend/app.js:refreshPackagingBomRoleUnboundNote()`
   - **检查 `res.ok`**：非 2xx → `{"code": "bom_unavailable", "status": res.status, "message": ""}`；
     `fetch` 抛异常 → 同一形状但 `status: 0`；
   - `res.ok` 但 `payload.bom` 不是对象 → `{"code": "bom_body_unexpected", "status": res.status, "message": ""}`；
   - 上述读失败三条：**在** `old.remove()` **之前**返回，**不许**移除既有的
     `[data-role-unbound-templates-unavailable]` 提示；改为在 host 里 upsert 一个读失败块
     （`className = "role-map-warning"`、`node.setAttribute("data-qqRoleUnboundReadProblem", "1")`，
     文本逐字取纯函数），并返回 `{"read_problem": <problem>}`；
     upsert = 先删上一次的读失败块（按 `data-qqRoleUnboundReadProblem` 自己那一块）再挂新的；
   - 成功且 `flag.code` 非空 → 既有那句
     `候选角色暂时读不到（<code>），请稍后重试；这不代表该盒型没有候选角色。` 与既有钩子
     `data-role-unbound-templates-unavailable="1"` **逐字不变**；
   - 成功且没有 `flag` → 既有 `old.remove()` 与 `return flag` **逐字不变**，不加任何东西。
3. 读失败路径不许整块重写 host（`host.innerHTML = …`）——那一栏还挂着"人工映射读不到"与
   本函数的既有提示，整块重写会把别的账一起擦掉。

## 3. 禁止事项

- 不许把读失败写成"候选角色都在" / "每一行都有业务角色了" / "该盒型没有候选角色"。
- 不许把 `read_problem` 塞进 `role_unbound_templates_unavailable`（服务端那份是"读到了、
  但候选角色读不到"，与"这一趟读不到 BOM"是两件事）。
- 不许改服务端 `packaging-bom` 路由与 `load_bom()` 的 `role_unbound_templates_unavailable` 口径。
- 不许改 `loadPackagingRoleMap()` / `renderPackagingRoleMap()` / `renderPackagingRoleMapUnavailable()`
  的既有行为（人工映射那一路是 fail-loud 的，是对的）。
- 不许在函数里做重试循环 / 自动重试 / 弹窗；不许把 `refreshPackagingParts()` 里的调用点删掉。
- 不许改 `tests/` 下任何既有文件（含 `tests/test_packaging_bom_role_unbound_template_disclosure_red.py`）；
  本批红测是新增文件。
- 不许起服务、不许发 HTTP、不许连线上 PG / SQLite、不许写业务数据；本批红测全部离线
  （`node -e` 抽函数体执行 + 源码守卫）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_role_unbound_note_read_failure_red -v
# R 组（8 条）：
#   R1 纯函数：bom_unavailable + status=500 → "这一次读不到 BOM（HTTP 500）…"（红）
#   R2 纯函数：bom_unavailable + 无状态码 → 同一句、括号里"网络错误"（红）
#   R3 纯函数：bom_body_unexpected → "这一次读到的 BOM 正文里没有盒型信息…"（红）
#   R4 纯函数：{} / null / 表外 code → ""（红）
#   R5 纯函数守卫：不碰 DOM / fetch / localStorage（红）
#   R6 源码守卫：函数体里 read_problem 的判定排在 old.remove() 之前，且出现
#      qqRoleUnboundReadProblem 钩子（红）
#   R7 源码守卫：函数体检查 res.ok，并把三条读失败写成 bom_unavailable / bom_body_unexpected（红）
#   R8 护栏：既有那句文案 + data-role-unbound-templates-unavailable 钩子 + old.remove() +
#      refreshPackagingParts() 里的调用点逐字仍在（绿）
# 现状：R1–R7 红（7 条），R8 绿（1 条护栏）
# 不回归（角色披露与人工映射两批）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_role_unbound_template_disclosure_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_part_role_manual_mapping_red
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_parse_terminal_signal_red
node --check tech_app/frontend/app.js
```

真机复验（实现方做完、且部署后）：

```
# 2.1 → 角色映射栏，构造一次"候选角色读不到"（例如让知识库读取失败）后刷新：
# 1) 读得到 BOM 且有 flag → 既有那句"候选角色暂时读不到（…）"照旧出现；
# 2) 让 GET …/requirement/packaging-bom 回 500 再刷新 → 那句**仍在**，旁边多出
#    "这一次读不到 BOM（HTTP 500），候选角色的披露也读不到；请稍后重试，这不代表这些行都有候选角色。"
# 3) 读接口恢复、且没有 flag → 两句都消失（成功路径逐字不变）。
```

## 5. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_bom_role_unbound_note_read_failure_red
# 实现前：Ran 8 tests … FAILED (failures=7)   ← R1 R2 R3 R4 R5 R6 R7
# 实现后：Ran 8 tests … OK                    ← R8 一条护栏始终绿
```

| 契约 | 落点（`tech_app/frontend/app.js`） |
| --- | --- |
| §2.1 纯函数 | 新增 `packagingRoleUnboundReadProblemText(problem)`（`refreshPackagingBomRoleUnboundNote()` 上方）：`code === "bom_unavailable"` 且 `Number(status) > 0` → `这一次读不到 BOM（HTTP <status>），候选角色的披露也读不到；请稍后重试，这不代表这些行都有候选角色。`；`bom_unavailable` 且无状态码 → 同句的「（网络错误）」版；`code === "bom_body_unexpected"` → `这一次读到的 BOM 正文里没有盒型信息，候选角色的披露读不到；…`；其余（`{}` / `null` / 表外码）→ `""`。无 `document.` / `window.` / `fetch(` / `localStorage`（R5）。 |
| §2.2 读失败三条 | `refreshPackagingBomRoleUnboundNote()`：函数内新增 `showReadProblem(problem)` —— 先按 `[data-qqRoleUnboundReadProblem]` 删自己上一次的读失败块，再 `document.createElement("div")`、`className = "role-map-warning"`、`setAttribute("data-qqRoleUnboundReadProblem", "1")`、`textContent = 纯函数(problem)`、`host.append(node)`，返回 `{read_problem: problem}`。`!res.ok` → `showReadProblem({code: "bom_unavailable", status: res.status, …})`；`res.ok` 但 `payload.bom` 不是对象 → `showReadProblem({code: "bom_body_unexpected", …})`；`fetch` 抛异常 → `showReadProblem({code: "bom_unavailable", status: 0, …})`。三条都在 `old.remove()` **之前**返回（R6），**不**移除既有的 `[data-role-unbound-templates-unavailable]` 提示。 |
| §2.2 成功路径逐字不变 | 有 `flag.code` → 既有那句 `候选角色暂时读不到（<code>），请稍后重试；这不代表该盒型没有候选角色。` 与钩子 `data-role-unbound-templates-unavailable="1"` 逐字不变；没有 `flag` → `if (old) old.remove();` + `return flag` 逐字不变、不加任何东西（R8）。`if (!currentProject) return null;` 与 `refreshPackagingParts()` 里的 `await refreshPackagingBomRoleUnboundNote()` 调用点仍逐字在（R8）。 |
| §3 未动的 | 没有 `host.innerHTML = …` 整块重写（那一栏还挂着「人工映射读不到」与既有提示）；未改 `loadPackagingRoleMap()` / `renderPackagingRoleMap()` / `renderPackagingRoleMapUnavailable()`；未改服务端路由与 `load_bom()` 的 `role_unbound_templates_unavailable` 口径；无重试循环 / 自动重试 / 弹窗。 |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_bom_role_unbound_template_disclosure_red \
  tests.test_packaging_part_role_manual_mapping_red \
  tests.test_drawing_flow_parse_terminal_signal_red   → Ran 59 … OK
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_panel_red \
  tests.test_drawing_flow_frontend_wiring_red \
  tests.test_packaging_parts_pagination_read_failure_red \
  tests.test_packaging_parts_read_failure_empty_state_red   → Ran 48 … OK
node --check tech_app/frontend/app.js   → OK
```

未改任何既有测试与业务数据、未放宽任何断言、未连 PG / SQLite、未起服务、未发 HTTP、
未 push / MR / tag / Release / 未部署。

# 规格：2.1 左栏"继续加载"点了没反应 —— 分页读失败被静默吞掉

状态：Spec + 红测（已实现）（原状：`tech_app/frontend/app.js:2412-2436 loadMorePackagingParts()`
把 `!res.ok` / `res.json()` 解不出 / `fetch` 抛异常**三条都折成 `return null`**，而唯一调用点
`:3667 more.addEventListener("click", () => { loadMorePackagingParts(); })` **丢掉返回值** ——
点一下"继续加载"什么都不会发生：按钮还亮着、文案一字不变、左栏不重画，用户只能反复点）
红测：`tests/test_packaging_parts_pagination_read_failure_red.py`
行号基线：HEAD `7e81fd7`（行号只用来指路；口径以本 Spec 正文为准，不以行号为准）。

血缘：承接 `packaging-parts-read-failure-empty-state.md`（同一病症在**第一页**那一侧：读不到
≠ 没有零件；本批把它搬到**分页**这一侧）、
`packaging-parts-list-visibility-and-kinds.md` §2.5（"还有 N 件未列出"+ 继续加载是 2.1 左栏
的唯一翻页出口）、
`packaging-silent-degradation-disclosure.md`（失败 / 空 / 没有不许同形 —— 一次**点击**失败
却零反馈，比第一页读失败更隐蔽：第一页至少有句文案）、
`packaging-business-parts-read-failure-note.md`（同一天写的姊妹批：键名同构）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

点"继续加载（还有 N 件）"没成功时，用户必须在左栏当场看到一句话说清
**这一页没读出来**（而不是"没有更多零件"，更不是一片安静），并且**已经列出的零件一件都不许丢**
—— 重试就是再点一次那个按钮。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/frontend/app.js`：

```js
2412 async function loadMorePackagingParts() {
2413   const doc = currentPackagingParts || {};
2414   if (!currentProject || !doc.has_more) return null;
2415   const offset = packagingPartsShown.length;
2416   try {
2417     const url = `${API}/api/projects/${currentProject}/requirement/packaging-parts`
2418       + `?${packagingPartsQueryString(packagingPartsPage, offset)}`;
2419     const res = await fetch(url);
2420     if (!res.ok) return null;                 // ← 500（读不到）与网络异常同形
2421     const page = await res.json().catch(() => null);
2422     if (!page) return null;                   // ← 正文解不出也同形
...
2435   } catch (error) { return null; }            // ← 网络异常也同形
2436 }
```

```js
3666       more.textContent = `继续加载（还有 ${missing} 件）`;
3667       more.disabled = !doc.has_more;
3668       more.addEventListener("click", () => { loadMorePackagingParts(); });   // ← 返回值被丢掉
```

调用点只有一个（`:3667`），且**不看返回值**：三条失败路径在源码上都只表现为"函数返回
`null`"，界面上没有任何一笔账。后果：

1. **失败与空同形**：`total > rows.length` 时左栏写着"还有 N 件未列出（只显示前 64 件）"，
   点一下按钮没有任何变化 —— 用户会以为"就是没有更多了"，或以为按钮坏了。
   `GET …/requirement/packaging-parts` 是 fail-loud 的（读文档失败直接抛，FastAPI 给 500），
   所以 500 这一步真的会发生。
2. **同一页失败可以无限重演**：`packagingPartsPage` / `offset` 都不变，按钮不置灰、不加
   提示，点第 2 次、第 3 次和第 1 次完全一样，现场无从判断是"还没加载"还是"读不到"。

## 2. 允许修改范围（实现方）

1. `tech_app/frontend/app.js`：**新增纯函数** `packagingPartsPageReadProblemText(problem)`
   （不得出现 `document.` / `window.` / `fetch(` / `localStorage`）：
   - `problem` 是非空对象且 `Number(problem.status) > 0` →
     `"这一页零件没读出来（HTTP <status>），已列出的零件不受影响；点"继续加载"重试"`
   - 非空对象但没有 `status`（或 `0`）→
     `"这一页零件没读出来（网络错误），已列出的零件不受影响；点"继续加载"重试"`
   - 其余（`{}` / `null` / 没有 `code`）→ `""`
2. `tech_app/frontend/app.js:loadMorePackagingParts()`
   - **前置判断逐字不变**：`if (!currentProject || !doc.has_more) return null;` 一个字不许动；
   - 成功路径**逐字不变**：按 `part_code` 去重累加进 `packagingPartsShown`、
     `currentPackagingParts = Object.assign({}, doc, page, {...})`、`renderTree(currentIR || {})`、
     `return page`；**成功后必须清掉上一次的失败提示**（`page_problem: null`），否则旧提示会赖在
     页面上；
   - 三条失败路径（其它非 2xx / `page` 为空 / `fetch` 抛异常）都**不再只返回 `null`**：
     把 `{"code": "parts_page_unavailable", "status": <HTTP 状态码，网络异常给 0>, "message": ""}`
     写进 `currentPackagingParts.page_problem` 并重画左栏，再 `return null`（**返回值契约不变**，
     取值仍是 `null`；调用点不用改）。
3. `tech_app/frontend/app.js:renderTree(ir)` 的"还有 N 件未列出"那一块（`:3660-3670` 附近）：
   - `doc.page_problem` 非空时，在"继续加载"按钮**旁边**追加一个提示块
     （`className = "part-page-problem-note"`、`wrap.dataset.qqPartsPageProblem = "1"`），
     文本逐字取 `packagingPartsPageReadProblemText(doc.page_problem)`；
   - 按钮**保持可点**（重试就是再点一次），`more.disabled = !doc.has_more` 这一行不许改；
   - `page_problem` 为空时这一块**不许出现**（今天的行为逐字不变）。

## 3. 禁止事项

- 不许把失败说成"没有更多零件" / "已全部列出" / "加载完成"；不许改 `doc.has_more` 的取值方向。
- 不许在失败路径清空 `packagingPartsShown` / 重置 `packagingPartsPage`（已列出的零件一件不许丢）。
- 不许把 `page_problem` 塞进 `stats` / `unavailable` / `filtered_reason_mix` 等既有字段。
- 不许改 `fetchPackagingParts()`（第一页，由 `packaging-parts-read-failure-empty-state.md` 管）、
  `packagingPartsQueryString()` / `packagingPartsItems()` 的既有口径、
  `GET …/requirement/packaging-parts` 服务端路由（fail-loud 是对的）。
- 不许在 `loadMorePackagingParts()` 里做重试循环 / 自动重试 / 弹窗。
- 不许改 `tests/` 下任何既有文件；本批红测是新增文件。
- 不许起服务、不许发 HTTP、不许连线上 PG / SQLite、不许写业务数据；本批红测全部离线
  （`node -e` 抽函数体执行 + 源码守卫）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_pagination_read_failure_red -v
# P 组（8 条）：
#   P1 纯函数：status=503 → "这一页零件没读出来（HTTP 503），已列出的零件不受影响；点"继续加载"重试"（红）
#   P2 纯函数：无状态码 → 文案说"网络错误"（红）
#   P3 纯函数：{} / null / 无 code → 返回 ""（红）
#   P4 源码守卫：loadMorePackagingParts() 失败时写 parts_page_unavailable 并交给渲染（红）
#   P5 源码守卫：renderTree() 里出现 qqPartsPageProblem 钩子（红）
#   P6 纯函数守卫：无 DOM / fetch / localStorage（红）
#   P7 护栏：前置判断逐字不变 + 成功路径四件事（push / Object.assign / renderTree / return page）都在（绿）
#   P8 护栏：失败路径不清空 packagingPartsShown、不改 has_more（绿）
# 现状：P1–P6 红（6 条），P7 P8 绿（2 条护栏）
# 不回归（零件左栏与既有读失败两批）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_read_failure_empty_state_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_read_failure_note_red
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_parse_terminal_signal_red
node --check tech_app/frontend/app.js
```

真机复验（实现方做完、且部署后）：

```
# 打开 2.1（零件 > 64 件、有"还有 N 件未列出"）：
# 1) 服务端正常 → 点"继续加载"照旧追加下一页，页面里没有 data-qqPartsPageProblem；
# 2) 服务端返回 500 → 点按钮后左栏当场出现
#    "这一页零件没读出来（HTTP 500），已列出的零件不受影响；点"继续加载"重试"，
#    已列出的零件数量一件不变，按钮仍可点；
# 3) 服务端恢复 → 再点一次，提示消失、下一页正常追加。
```

## 5. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_parts_pagination_read_failure_red
# 实现前：Ran 8 tests … FAILED (failures=6)   ← P1 P2 P3 P4 P5 P6
# 实现后：Ran 8 tests … OK                    ← P7 P8 两条护栏始终绿
```

| 契约 | 落点（`tech_app/frontend/app.js`） |
| --- | --- |
| §2.1 纯函数 | 新增 `packagingPartsPageReadProblemText(problem)`（紧邻 `packagingPartsEmptyText()`）：`code` 为空 / `problem` 非对象 → `""`；`Number(problem.status) > 0` → `这一页零件没读出来（HTTP <status>），已列出的零件不受影响；点"继续加载"重试`；否则 → 同句的"（网络错误）"版。无 `document.` / `window.` / `fetch(` / `localStorage`（P6）。 |
| §2.2 分页失败路径 | `loadMorePackagingParts()`：前置判断 `if (!currentProject || !doc.has_more) return null;` 逐字不动；新增函数内 `failPage(status)` —— 组 `{"code": "parts_page_unavailable", "status": Number(status) \|\| 0, "message": ""}`，`message` 由纯函数填，写进 `currentPackagingParts = Object.assign({}, doc, {page_problem: problem})` 后 `renderTree(currentIR \|\| {})`，`return null`（返回值契约不变）；三条失败路径改为 `return failPage(res.status)` / `return failPage(res.status)` / `catch → failPage(0)`。 |
| §2.2 成功路径 | 累加 / `Object.assign({}, doc, page, …)` / `renderTree` / `return page` 逐字不变（P7）；第三个对象里补 `page_problem: null`，清掉上一次的失败提示。 |
| §2.3 渲染位置 | `renderTree()` 的"还有 N 件未列出"块里，`note.appendChild(more)` 之后按 `packagingPartsPageReadProblemText(doc.page_problem)` 追加 `div.part-page-problem-note`（`dataset.qqPartsPageProblem = "1"`）；`more.disabled = !doc.has_more` 不动（按钮保持可点）；`doc.page_problem` 为空时这一块不出现（今天的行为逐字不变）。 |
| §3 未动的 | 失败路径不碰 `packagingPartsShown` / `packagingPartsPage` / `doc.has_more`（P8）；未改 `fetchPackagingParts()`（第一页）、`packagingPartsQueryString()` / `packagingPartsItems()`、服务端路由；无重试循环 / 自动重试 / 弹窗。 |
未改任何既有测试与业务数据、未放宽任何断言、未连 PG / SQLite、未起服务、未发 HTTP。
未 push / MR / tag / Release / 未部署。

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_read_failure_empty_state_red \
  tests.test_packaging_business_parts_read_failure_note_red \
  tests.test_drawing_flow_parse_terminal_signal_red   → Ran 47 … OK
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_list_visibility_red \
  tests.test_packaging_parts_panel_red \
  tests.test_drawing_board_two_column_parts_and_3d_red \
  tests.test_packaging_parts_read_failure_empty_state_red   → Ran 49 … OK (skipped=1)
node --check tech_app/frontend/app.js   → OK
```

# 规格：补料厚 / 补材料 / 轮廓放行**成功之后**，列表读不回来就把整栏零件换成一句"读不到"

状态：Spec + 红测（已实现）（原状：本批只写 Spec 与红测，三处"写后重读"仍是旧契约（`if (reread)` 判真值）：`tech_app/frontend/app.js` 的三处"写后重读"：
补料厚 `:1541-1548`、补材料 `:1588-1595`、轮廓出路 `:1641-1642`。它们都写成
`if (reread) { currentPackagingParts = reread; } else { <回显补丁> }` —— 这是**旧契约**
（`fetchPackagingParts()` 读失败给 `null`）；而 `packaging-parts-read-failure-empty-state.md`
（`## 381`）落地后，读失败给的是**带 `read_problem` 的真值文档**（`app.js:2980-3010`
`readProblemDoc(status)`，`parts: []`），于是 `else` 分支成了**死代码**，
`currentPackagingParts` 被整份替换成空文档 —— 左栏刚刚还在的零件全没了）
红测：`tests/test_packaging_parts_reread_failure_after_write_red.py`
行号基线：HEAD `7e81fd7`（行号只用来指路；口径以本 Spec 正文为准，不以行号为准）。

血缘：**跨批交互缺口**（不是老 bug）：`## 381` 改了 `fetchPackagingParts()` 的失败返回契约，
`packaging-part-manual-fill-persists.md` §2.6 / `packaging-open-outline-part-needs-a-way-out.md` §2.3
里"重读失败才退回回显补丁"的设计**本来是对的**，现在被新契约顶掉了；
承接 `packaging-silent-degradation-disclosure.md` §2.2（"读不到"≠"文档是空的"，更不许
把已经读到的事实整份换掉）、`packaging-parts-pagination-read-failure.md` §2.2
（同一份文档里 `page_problem` 的处置范式：**不许**丢掉已列出的零件）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

一笔补录**已经提交成功**之后，列表读不回来时：左栏必须**继续显示刚才那些零件**（并带上这一笔的
本地回显），再用一句话说清"列表没能重新读回来" —— 不许把整栏换成空文档 + "读不到"。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/frontend/app.js`（三处同形）：

```js
1541   const reread = await fetchPackagingParts();
1542   if (reread) {
1543     currentPackagingParts = reread;            // ← 读失败时 reread 是"带 read_problem 的空文档"，
1544   } else {                                     //   真值 → 走这一支，整份替换成空
1545     const patch = { thickness_mm: payload.thickness_mm };
1547     patchPackagingPartRows(partCode, patch);   // ← 这一支成了死代码
1548   }
```

```js
1641   const reread = await fetchPackagingParts();
1642   if (reread) currentPackagingParts = reread;  // ← 连回显补丁都没有，直接换空
```

`fetchPackagingParts()` 的新契约（`:2980-3010`）：404 → `null`；其它非 2xx / 网络异常 →
`{parts: [], filtered: [], unavailable: [], stats: {}, source: {}, reviewable: false,
built: false, read_problem: {code: "parts_unavailable", status: …}}`（真值）。

三个后果：

1. **成功的写被一次读故障连坐**：补料厚 POST 返回 200、服务端已经落库，紧接着的重读失败 →
   左栏零件全清、只剩"暂时读不到零件文档（HTTP 500）"。用户会以为这一笔把列表搞坏了。
2. **回显补丁成了死代码**：三处里两处的 `else` 分支（`patchPackagingPartRows`）从 `## 381`
   落地那一刻起再也不会执行 —— 设计里"立刻可用"的那条兜底消失了。
3. **已列出的零件被丢掉**：`packagingPartsShown` 是翻页累加的行，`renderTree()` 在有 `parts` 时
   才用它；文档被换成空文档之后，用户翻了几页的清单也一起没了。

## 2. 允许修改范围（实现方）

1. `tech_app/frontend/app.js`：**新增纯函数** `packagingPartsRereadProblemText(problem)`
   （不得出现 `document.` / `window.` / `fetch(` / `localStorage`）：
   - `problem` 是非空对象且 `Number(problem.status) > 0` →
     `"刚才这一笔已经提交成功，但列表没能重新读回来（HTTP <status>），下面显示的是本地回显；刷新页面即可核对服务端的值"`
   - 非空对象但没有 `status`（或 `0`）→ 同一句，括号里是 `网络错误`
   - 其余（`{}` / `null` / 表外 code）→ `""`
2. `tech_app/frontend/app.js`：**新增共用入口** `applyPackagingPartsReread(reread, partCode, patch)`，
   三处"写后重读"都改用它，三态分别处置：
   - `reread` 是 `null`（404，端点未上线）→ **既有**回显补丁路径（`patchPackagingPartRows(partCode, patch)`），
     `currentPackagingParts` 一字不改；
   - `reread.read_problem` 非空 → **不许**替换 `currentPackagingParts`（也不许碰
     `packagingPartsShown`）、打上回显补丁、并把
     `currentPackagingParts = Object.assign({}, currentPackagingParts, {reread_problem: reread.read_problem})`
     记下来交给渲染；
   - 读到 → 既有 `currentPackagingParts = reread`（逐字不变），并**清掉**上一次的
     `reread_problem`（`reread_problem: null`，否则旧提示赖着不走）。
3. `tech_app/frontend/app.js:renderTree()`：`currentPackagingParts.reread_problem` 非空时，
   在零件列表**上方**渲染一个提示块（`className = "part-reread-problem-note"`、
   `wrap.dataset.qqPartsRereadProblem = "1"`），文本逐字取纯函数；
   **有零件时也必须显示**（不许只在空态分支里加）；没有 `reread_problem` 时一个节点都不加。

## 3. 禁止事项

- 不许在读失败时把 `currentPackagingParts` 换成空文档、不许清空 `packagingPartsShown`。
- 不许把这三处改回 `if (reread)` 判真值（那正是本批的缺口）；判据必须是 `read_problem` 三态。
- 不许把"这一笔提交成功但列表读不回来"说成"零件文档读不到，请先跑一键解析"或
  "补录失败"；也不许反过来声称列表已刷新。
- 不许改 `fetchPackagingParts()` / `loadMorePackagingParts()` 的既有契约（`null` / `read_problem` /
  `page_problem`），不许改 `packagingPartsEmptyText()` 的既有分支。
- 不许改 `patchPackagingPartRows()` 的既有语义（整份行 + 当前页 + 已累加行都要改）。
- 不许用 `window.alert` 报这件事（那是打断式；本批是页面内的披露）。
- 不许改 `tests/` 下任何既有文件；本批红测是新增文件。
- 不许起服务、不许发 HTTP、不许连线上 PG / SQLite、不许写业务数据；本批红测全部离线
  （`node -e` 抽函数体执行 + 源码守卫）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_reread_failure_after_write_red -v
# T 组（8 条）：
#   T1 纯函数：status=500 → "刚才这一笔已经提交成功，但列表没能重新读回来（HTTP 500）…"（红）
#   T2 纯函数：无状态码 → 同一句、括号里"网络错误"（红）
#   T3 纯函数：{} / null / 表外 code → ""（红）
#   T4 纯函数守卫：不碰 DOM / fetch / localStorage（红）
#   T5 源码守卫：三处"写后重读"都按 read_problem 分三态（出现 read_problem 判据）（红）
#   T6 源码守卫：renderTree() 里有 qqPartsRereadProblem 渲染位（红）
#   T7 护栏：读到时仍 currentPackagingParts = reread；404 的回显补丁路径仍在（绿）
#   T8 护栏：三处都不清空 packagingPartsShown、也不再把空文档当"读到了"（绿）
# 现状：T1–T6 红（6 条），T7 T8 绿（2 条护栏）
# 不回归（零件左栏、手动补录与轮廓出路三批）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_read_failure_empty_state_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_pagination_read_failure_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_part_manual_fill_persists_red
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_parse_terminal_signal_red
node --check tech_app/frontend/app.js
```

真机复验（实现方做完、且部署后）：

```
# 2.1 左栏选中一件 → 补料厚（POST 200）→ 立刻让零件读接口回 500 再点一次补材料：
# 1) 左栏零件**还在**（数量与刚才一致），行上是本地回显的值；
# 2) 列表上方出现
#    "刚才这一笔已经提交成功，但列表没能重新读回来（HTTP 500），下面显示的是本地回显；
#     刷新页面即可核对服务端的值"；没有 window.alert；
# 3) 读接口恢复后再补一次 → 提示消失、整栏来自服务端重读；
# 4) 轮廓出路（重算/签字）同样：读失败不吞掉列表。
```

## 5. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 445`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 纯函数 `packagingPartsRereadProblemText(problem)` | `app.js`（原 `PACKAGING_REREAD_PROBLEM_CODES` 旁） | 码表**就地写进函数体**（`code !== "parts_unavailable" && code !== "business_parts_unavailable"`）：证据取法用 `node -e` 单独抽这一个函数执行（`eval(fn)`），依赖同文件 const 会 `is not defined`（同一坑在 `packagingPartsEmptyText` 上已踩过）；`status > 0` → HTTP 码，否则"网络错误" |
| §2.2 共用入口 `applyPackagingPartsReread(reread, partCode, patch)` | `app.js` | 三态：`null` → 回显补丁、`currentPackagingParts` 一字不改；`read_problem` 非空 → 补丁 + `currentPackagingParts = Object.assign({}, currentPackagingParts, {reread_problem})`（不换文档、不碰 `packagingPartsShown`）；读到 → 既有 `currentPackagingParts = reread` + 清 `reread_problem` |
| §2.2 三处写后重读改走共用入口 | `app.js` 补料厚 / 补材料 / 轮廓出路 | 每处 `const reread = await fetchPackagingParts();` 后一行注明三态（`read_problem` 判据就地可见） |
| §2.3 `renderTree()` 披露位 | `app.js` `renderTree()` | `packagingPartsRereadProblemText(doc.reread_problem)` 非空 → `className="part-reread-problem-note"` + `dataset.qqPartsRereadProblem = "1"`，位置在空态分支**之前**（有零件也要显示）；无问题时不加节点 |

复跑命令与结果：

```
node --check tech_app/frontend/app.js                                          # 通过
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_parts_reread_failure_after_write_red
Ran 8 tests ... OK                                                             # T1–T8 全绿（红基 6 红）

./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_read_failure_empty_state_red \
  tests.test_packaging_parts_pagination_read_failure_red \
  tests.test_packaging_part_manual_fill_persists_red \
  tests.test_drawing_flow_parse_terminal_signal_red
Ran 61 tests ... OK
```

红测自身缺陷：无（T1–T8 八条只靠"纯函数真跑 + 源码守卫"，本批一次落地全绿）。

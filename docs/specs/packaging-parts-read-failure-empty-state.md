# 规格：2.1 左栏"零件文档读不到"不许显示成"还没生成，请先跑一键解析"

状态：Spec + 红测（已实现）（原状：`tech_app/frontend/app.js:2399-2413 fetchPackagingParts()` 把
**所有**非 2xx 与网络异常一起折成 `null`（`if (!res.ok) return null;` / `catch { return null; }`），
`packagingPartsEmptyText()`（`:2017`）于是拿不到任何"读失败"迹象，回落到
"零件文档还没生成，请先跑一键解析图纸。" —— 用户会去重跑一键解析，而服务端是 500）
红测：`tests/test_packaging_parts_read_failure_empty_state_red.py`

血缘：承接 `packaging-parts-list-visibility-and-kinds.md` §2.5（空表格不等于"没有零件"；
`built === true && total === 0` 要说"这份图纸没有可用的零件"）、
`drawing-flow-parse-terminal-signal.md` C4（`packagingPartsEmptyText()` 是 2.1 左栏空态的
唯一取数口；本批只**加一条分支**，既有四类文案逐字不变）、
`packaging-preconditions-requirement-read-failure.md`（同一病症在跑前提示那一侧 —— 那里的
`[code] message → action` 就是被本函数渲染的）、
`packaging-silent-degradation-disclosure.md`（失败 / 空 / 没有不许同形）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

2.1 左栏一件零件都没有时，用户看到的第一句话必须能区分三件事：
**这份图纸确实没有零件**（去改图纸/盒型）、**零件还没算过**（去点一键解析）、
**这一趟读不到零件文档**（稍后重试 —— 重跑解析不会有帮助，服务端此刻正返回 500）。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/frontend/app.js`：

```js
2399 async function fetchPackagingParts() {
2400   if (!currentProject) return null;
2401   try {
2402     const url = `${API}/api/projects/${currentProject}/requirement/packaging-parts`
2403       + `?${packagingPartsQueryString(packagingPartsPage, 0)}`;
2404     const res = await fetch(url);
2405     if (!res.ok) return null;                 // ← 404（端点没上线）与 500（读不到）同形
2406     const doc = await res.json().catch(() => null);
2407     packagingPartsShown = packagingPartsItems(doc);
2408     return doc;
2409   } catch (error) { return null; }            // ← 网络异常也同形
2410 }
```

```js
2017 function packagingPartsEmptyText(partsDoc, preconditions) {
2018   const doc = (partsDoc && typeof partsDoc === "object") ? partsDoc : {};
2019   const parts = Array.isArray(doc.parts) ? doc.parts : [];
2020   if (parts.length) return "";
...
2042   if (!segments.length) return "零件文档还没生成，请先跑一键解析图纸。";
2043   return segments.join("；");
2044 }
```

两个后果：

1. `null` 一路传到 `packagingPartsEmptyText(null, preconditions)`：没有 `parts`、没有
   `unavailable`、没有向服务端问过"为什么空"，于是给出
   **"零件文档还没生成，请先跑一键解析图纸。"** —— 而服务端此刻可能正返回 500
   （`GET …/requirement/packaging-parts` 是 fail-loud 的：读文档失败直接抛，FastAPI 给 500）。
2. 404（端点未上线，代码注释里明确要"不谎报解析失败"）与 500 的处理**必须**分家，但今天
   两者在源码上是同一个 `return null`：**没有任何字段**能让空态文案说出"读不到"。

## 2. 允许修改范围（实现方）

1. `tech_app/frontend/app.js:fetchPackagingParts()`
   - `res.ok` 分支与 `404` 分支的**行为逐字不变**（404 仍返回 `null`：端点未上线那条既有路径）；
   - 其它非 2xx（含 5xx）与 `fetch` 抛异常时，返回一份**带 `read_problem` 的空文档形状**
     （不许再返回 `null`）：

     ```jsonc
     {"parts": [], "filtered": [], "unavailable": [], "stats": {}, "source": {},
      "reviewable": false, "built": false,
      "read_problem": {"code": "parts_unavailable", "status": 500, "message": ""}}
     ```

     `status` 取 HTTP 状态码；网络异常（没有状态码）给 `0`。
2. `tech_app/frontend/app.js:packagingPartsEmptyText(partsDoc, preconditions)`
   - **新增第一优先分支**：`doc.read_problem` 是非空对象时立即返回
     - 有 `status`（>0）：`"暂时读不到零件文档（HTTP <status>），请稍后重试；这不代表这份图纸没有零件"`
     - 没有 `status` 或 `status === 0`：`"暂时读不到零件文档（网络错误），请稍后重试；这不代表这份图纸没有零件"`
   - **优先于其它所有分支**：不许被 `parts.length` / `built === true && total === 0` /
     `doc.unavailable` 文案抢先；
   - 没有 `read_problem` 时，既有四类文案与优先级**逐字不变**（含
     `"零件文档还没生成，请先跑一键解析图纸。"` 与 `"这份图纸没有可用的零件。"`）；
   - 仍是**纯函数**（不得出现 `document.` / `window.` / `fetch(` / `localStorage`）。
3. `loadMorePackagingParts()`（分页）本批**不动**（非目标：它失败时仍 `return null`）。

## 3. 禁止事项

- 不许把 `res.ok` / 404 两条既有路径的行为改掉（404 仍是 `null`，ok 仍原样返回 doc）。
- 不许把 `read_problem` 塞进 `unavailable` 数组（那是服务端给的零件不可用原因，
  键盘/审计口径不同；本批只加一个前端自己的读失败标记）。
- 不许在空态文案里说"图纸解析失败 / 一键解析未完成"（那会把读失败赖到解析链路上）。
- 不许改 `packagingPartsEmptyText()` 的既有四类文案、不许改它的签名与纯函数性质。
- 不许改 `tech_app/backend/main.py` 的零件读路由（服务端 fail-loud 是**对的**，本批只改
  前端怎么说话）与本批之外的任何前端文件。
- 不许改 `tests/` 下任何既有文件（含 `test_drawing_flow_parse_terminal_signal_red.py` 的 C 组、
  `test_packaging_parts_list_visibility_and_kinds_red` 一类既有套件）；本批红测是新增文件。
- 不许起服务、不许发 HTTP、不许连线上 PG / SQLite、不许写业务数据；本批红测全部离线
  （`node -e` 抽函数体执行，纯函数 + 源码守卫）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_read_failure_empty_state_red -v
# T 组（9 条）：
#   T1 read_problem.status=500 → "暂时读不到零件文档（HTTP 500），请稍后重试；这不代表这份图纸没有零件"（红）
#   T2 read_problem.status=0 / 缺失 → 文案说"网络错误"（红）
#   T3 read_problem 优先于 "这份图纸没有可用的零件。" / "请先跑一键解析图纸。"（红）
#   T4 没有 read_problem 的普通空 doc → 既有文案逐字不变（护栏）
#   T5 有零件 → 仍返回 ""（护栏）
#   T6 built===true && total===0 → 仍是"这份图纸没有可用的零件。"（护栏）
#   T7 源码守卫：fetchPackagingParts() 认 404、且失败时写 read_problem（红）
#   T7b 服务端给的 unavailable 原因照旧原样渲染（护栏）
#   T8 源码守卫：packagingPartsEmptyText() 仍是纯函数（护栏）
# 现状：T1 T2 T3 T7 红（4 条），T4 T5 T6 T7b T8 绿（5 条护栏）
# 不回归（空态与零件可见性的既有口径）
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_parse_terminal_signal_red
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_frontend_wiring_red
node --check tech_app/frontend/app.js
```

真机复验（实现方做完、且部署后）：

```
# 服务端 500 时打开 2.1：左栏第一句必须是
#   "暂时读不到零件文档（HTTP 500），请稍后重试；这不代表这份图纸没有零件"
# 服务端 200 + 空零件：仍然是 "这份图纸没有可用的零件。" / 既有不可用原因
```

## 5. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_parts_read_failure_empty_state_red
# 实现前：Ran 9 tests … FAILED (failures=4)   ← T1 T2 T3 T7
# 实现后：Ran 9 tests … OK                    ← T4 T5 T6 T7b T8 五条护栏始终绿
```

| 契约 | 落点（`tech_app/frontend/app.js`） |
| --- | --- |
| §2.1 404 与 5xx 分家 | `fetchPackagingParts()`：`Number(res.status) === 404` → `return null`（那条既有路径逐字不变）；其余非 2xx → 返回带 `read_problem` 的空文档形状；`fetch` 抛异常 → `status` 给 `0`（网络错误）。空文档形状由函数内 `readProblemDoc(status)` 一处构造：`{parts: [], filtered: [], unavailable: [], stats: {}, source: {}, reviewable: false, built: false, read_problem: {code: "parts_unavailable", status, message: ""}}` |
| §2.1 不许再给上一份的累加行 | 读失败两条路径都清 `packagingPartsShown = []`（左栏 `rows` 回落到本页 `parts` = 空 → 空态文案才有机会出现） |
| §2.2 第一优先分支 | `packagingPartsEmptyText()` 开头新增 `doc.read_problem` 分支：`status > 0` → `"暂时读不到零件文档（HTTP <status>），请稍后重试；这不代表这份图纸没有零件"`；否则 `"暂时读不到零件文档（网络错误），请稍后重试；这不代表这份图纸没有零件"` —— **优先于** `parts.length` / `built === true && total === 0` / `doc.unavailable` 三类既有分支（T3） |
| §2.2 纯函数 | 只读入参，无 `document.` / `window.` / `fetch(` / `localStorage`（T8） |
| §2.3 分页不动 | `loadMorePackagingParts()` 一行未改（失败时仍 `return null`） |
| §3 未动的 | 既有四类空态文案（含 `"零件文档还没生成，请先跑一键解析图纸。"` 与 `"这份图纸没有可用的零件。"`）、服务端给的 `unavailable` 原因渲染、`main.py` 的零件读路由（服务端 fail-loud 保持不变） |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_drawing_flow_parse_terminal_signal_red \
    tests.test_drawing_flow_frontend_wiring_red tests.test_packaging_parts_panel_red \
    tests.test_packaging_parts_downstream_red tests.test_drawing_board_two_column_parts_and_3d_red \
    tests.test_packaging_parts_list_visibility_red
# Ran 102 tests … OK (skipped=1)
node --check tech_app/frontend/app.js   → OK
```

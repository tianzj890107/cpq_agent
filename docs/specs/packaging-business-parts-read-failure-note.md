# 规格：业务部件（权威清单）"读不到"不许显示成"已识别的几何区域还不是业务部件清单"

状态：Spec + 红测（已实现）（原状：`tech_app/frontend/app.js:2437-2445 fetchPackagingBusinessParts()` 把所有非 2xx
与 `fetch` 异常一起折成 `null`；`packagingBusinessImportNote()`（`:2067-2090`）收到 `null`
就断言"已识别的几何区域还不是业务部件清单"并给出**导入权威清单**按钮 ——
清单可能好好地存在，只是这一趟读不到）
红测：`tests/test_packaging_business_parts_read_failure_note_red.py`

血缘：承接 `packaging-business-parts-and-cad-plan-view.md` §2 第 5 条（没有权威清单时要"先说清
下面那些是几何证据，不是业务零件"，并给导入出口）、
`packaging-parts-read-failure-empty-state.md`（同一病症在几何零件读接口那一侧；本批把它搬到
业务部件侧，键名同构：`read_problem`）、
`packaging-authority-disclosure-on-read.md`（权威清单的披露不许在读回路径上丢）、
`packaging-silent-degradation-disclosure.md`（失败 / 空 / 没有不许同形）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

业务部件面板一件都没有时，用户看到的第一句话必须能区分三件事：
**这个项目确实还没导入权威清单**（去导入）、**清单是空的**（去查资料）、
**这一趟读不到清单**（稍后重试 —— 重新导入是错的下一步：清单本来就在）。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/frontend/app.js`：

```js
2437 async function fetchPackagingBusinessParts() {
2438   if (!currentProject) return null;
2439   try {
2440     const res = await fetch(`${API}/api/projects/${currentProject}/requirement/`
2441       + `packaging-business-parts`);
2442     if (!res.ok) return null;                 // ← 404（端点没上线）与 500（读不到）同形
2443     return await res.json().catch(() => null);
2444   } catch (error) { return null; }            // ← 网络异常也同形
2445 }
```

```js
2067 function packagingBusinessImportNote(doc) {
2068   if (!doc || packagingBusinessPartRows(doc).length) return null;   // ← null = "没有权威清单"
2069   const gap = (doc && doc.gap) || {};
...
2075   text.textContent = gap.message
2076     || "已识别的几何区域还不是业务部件清单：下面列的是几何分量，不是业务零件。";
...
2079   action.textContent = gap.action || "导入权威部件清单（Excel）后再跑 BOM / 工艺 / 成本";
```

调用点（`:2451` / `:3507` / `:3516`）把 `null` 与"真的没有清单"当同一件事：
`currentPackagingBusinessParts = await fetchPackagingBusinessParts();`。

两个后果：

1. **读失败被写成一句断言**："已识别的几何区域还不是业务部件清单" + 动作
   "导入权威部件清单（Excel）后再跑 BOM / 工艺 / 成本" —— 用户会去**重新导入**，
   而 `POST …/packaging-business-parts/import` 会落**新的一版**业务部件文档
   （`save_business_parts` 版本化），这不是读失败该有的下一步。
2. 404（端点未上线，`fetchPackagingBusinessParts` 注释里明确"读不到就保持 null，
   左栏退回几何分量并说明原因"）与 500 在源码上是同一个 `return null`：
   **没有任何字段**能让这句文案说出"读不到"。

## 2. 允许修改范围（实现方）

1. `tech_app/frontend/app.js:fetchPackagingBusinessParts()`
   - `res.ok` 分支与 `404` 分支的行为**逐字不变**（404 仍返回 `null`）；
   - 其它非 2xx（含 5xx）与 `fetch` 抛异常时，返回一份**带 `read_problem` 的空文档形状**
     （不许再返回 `null`）：

     ```jsonc
     {"business_parts": [], "geometry_evidence": {}, "source": {},
      "read_problem": {"code": "business_parts_unavailable", "status": 500, "message": ""}}
     ```

     `status` 取 HTTP 状态码；网络异常（没有状态码）给 `0`。
2. `tech_app/frontend/app.js`：**新增纯函数** `packagingBusinessReadProblemText(problem)`
   （不得出现 `document.` / `window.` / `fetch(` / `localStorage`）：
   - `problem` 是非空对象且 `status > 0` →
     `"暂时读不到业务部件清单（HTTP <status>），请稍后重试；这不代表这个项目还没导入权威清单"`
   - 非空对象但没有 `status`（或 `0`）→
     `"暂时读不到业务部件清单（网络错误），请稍后重试；这不代表这个项目还没导入权威清单"`
   - 其余（`{}` / `null` / 没有 `code`）→ `""`
3. `tech_app/frontend/app.js:packagingBusinessImportNote(doc)`
   - **新增第一优先分支**：`doc.read_problem` 非空时返回一个提示块
     （文本 = `packagingBusinessReadProblemText(doc.read_problem)`），并且：
     - **不给**"导入权威清单（业务部件）"按钮（不许把人支去重新导入）；
     - `data-` 钩子用 `qqBusinessUnavailable`（源码里写成 `wrap.dataset.qqBusinessUnavailable = "1";`，
       与既有的 `wrap.dataset.qqBusinessMissing = "1"` 分家）；「读不到」时**不许**再带
       `qqBusinessMissing`；
   - 没有 `read_problem` 时，既有返回（`null` / `data-qqBusinessMissing` / `gap.message` /
     `gap.action` / 导入按钮）**逐字不变**。

## 3. 禁止事项

- 不许把 `res.ok` / 404 两条既有路径改掉（404 仍是 `null`）。
- 不许把 `read_problem` 塞进 `gap`（那是服务端给的"几何区域还不是业务部件清单"链路，
  与"读不到"是两件事）。
- 不许在文案里说"清单不存在 / 还没导入 / 请重新导入"（那会把读失败赖到业务数据上）。
- 不许改 `packagingBusinessPartRows()` / `packagingAuthorityDisclosureLines()` /
  `renderPackagingBusinessTree()` 的既有口径；不许动本批之外的读接口
  （`fetchPackagingParts` / `loadMorePackagingParts` 由既有 Spec 管）。
- 不许改 `tech_app/backend/main.py` 的业务部件读路由（服务端 fail-loud 是**对的**）。
- 不许改 `tests/` 下任何既有文件；本批红测是新增文件。
- 不许起服务、不许发 HTTP、不许连线上 PG / SQLite、不许写业务数据；本批红测全部离线
  （`node -e` 抽函数体执行 + 源码守卫）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_read_failure_note_red -v
# U 组（8 条）：
#   U1 status=500 → "暂时读不到业务部件清单（HTTP 500），请稍后重试；这不代表这个项目还没导入权威清单"（红）
#   U2 无状态码 → 文案说"网络错误"（红）
#   U3 `{}` / 无 code → 返回 ""（"没有读问题"不许拼出这句话）（红）
#   U4 源码守卫：fetchPackagingBusinessParts() 认 404、且失败时写 read_problem（红）
#   U5 源码守卫：packagingBusinessImportNote() 把 read_problem 判在既有文案之前（红）
#   U6 源码守卫：既有 qqBusinessMissing 钩子与两句既有文案逐字不变（护栏）
#   U7 packagingBusinessPartRows(null / {}) 仍是 []（护栏）
#   U8 源码守卫：packagingBusinessReadProblemText() 是纯函数（红）
# 现状：U1–U5 U8 红（6 条），U6 U7 绿（2 条护栏）
# 不回归（业务部件面板与空态）
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_parse_terminal_signal_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_read_failure_empty_state_red
node --check tech_app/frontend/app.js
```

真机复验（实现方做完、且部署后）：

```
# 服务端 500 时打开 2.1：业务部件区第一句必须是
#   "暂时读不到业务部件清单（HTTP 500），请稍后重试；这不代表这个项目还没导入权威清单"
#   并且**没有**"导入权威清单（业务部件）"按钮
# 服务端 200 + 没有清单：仍是既有的 data-qqBusinessMissing 提示 + 导入按钮
```

## 5. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_parts_read_failure_note_red
# 实现前：Ran 8 tests … FAILED (failures=6)   ← U1 U2 U3 U4 U5 U8
# 实现后：Ran 8 tests … OK                    ← U6 U7 两条护栏始终绿
```

| 契约 | 落点（`tech_app/frontend/app.js`） |
| --- | --- |
| §2.1 404 与 5xx 分家 | `fetchPackagingBusinessParts()`：`Number(res.status) === 404` → `return null`（既有路径逐字不变）；其余非 2xx → 带 `read_problem` 的空文档形状；`fetch` 抛异常 → `status: 0`。形状由函数内 `readProblemDoc(status)` 一处构造：`{business_parts: [], geometry_evidence: {}, source: {}, read_problem: {code: "business_parts_unavailable", status, message: ""}}` |
| §2.2 纯函数 | 新增 `packagingBusinessReadProblemText(problem)`：`code` 为空 → `""`；`status > 0` → `"暂时读不到业务部件清单（HTTP <status>），请稍后重试；这不代表这个项目还没导入权威清单"`；否则 `"…（网络错误）…"`。无 `document.` / `window.` / `fetch(` / `localStorage`（U8） |
| §2.3 第一优先分支 | `packagingBusinessImportNote(doc)` 开头新增 `doc.read_problem` 分支：只给一个提示块（文本 = 上面的纯函数），**不给**导入按钮；`data-` 钩子用 `qqBusinessUnavailable`（与 `qqBusinessMissing` 分家，且读不到时不再带旧钩子） |
| §2.3 既有路径不变 | 没有 `read_problem` 时，`null` / `data-qqBusinessMissing` / `gap.message` / `gap.action` / 导入按钮逐字不变（U6） |
| §3 未动的 | `packagingBusinessPartRows()` / `packagingAuthorityDisclosureLines()` / `renderPackagingBusinessTree()` 的既有口径、`fetchPackagingParts()` / `loadMorePackagingParts()`、`main.py` 的业务部件读路由（服务端 fail-loud 保持不变） |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_drawing_flow_parse_terminal_signal_red \
    tests.test_packaging_parts_read_failure_empty_state_red \
    tests.test_packaging_business_parts_and_cad_plan_view_red \
    tests.test_packaging_business_part_panel_evidence_red \
    tests.test_packaging_business_part_plan_click_and_bound_outline_red \
    tests.test_packaging_authority_disclosure_on_read_red \
    tests.test_packaging_authority_thumbnail_media_red
# Ran 134 tests … OK
node --check tech_app/frontend/app.js   → OK
```

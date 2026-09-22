# 规格：右栏「零件详情」读不到 / 没这件，不许混成同一句、也不许贴原生文本

依赖：`docs/specs/packaging-parts-selectable-panel.md`（§C1 的读接口：零件不存在 404 +
`PACKAGING_PART_NOT_FOUND`；未生成文档 200 + `built:false`）、
`docs/specs/packaging-cad-plan-read-failure.md`（本批的前一件同族修正：三态分家 + 具名纯函数出文案）、
`docs/specs/packaging-parts-read-failure-empty-state.md`（左栏那一侧同一套口径）。

状态：Spec + 红测（已实现）（原状：`tech_app/frontend/app.js:1298-1328` 的
`selectPackagingPart()` 把 404（没这件，后端带 `PACKAGING_PART_NOT_FOUND`）/ 5xx / 网络异常
一起折成 `throw new Error(message || "读取零件详情失败（HTTP n）")`，再把这一句原文贴进右栏
`#packagingPartFacts` —— 断网时贴的是浏览器原生英文文本，且「重跑过解析、这一件不在了」
与「后端读不到」长得一模一样）
红测：`tests/test_packaging_part_detail_read_failure_red.py`
行号基线：HEAD `5b4a265`

## 0. 一句话目标

右栏点一件零件时：「这一件不在当前零件文档里」（404 + `PACKAGING_PART_NOT_FOUND`）与
「暂时读不到这一件（HTTP n / 网络错误）」必须**分开说**，且都不再贴服务端/浏览器原文。

## 1. 现状缺口（代码级）

```js
  try {
    const res = await fetch(`${API}/api/projects/${currentProject}/requirement/`
      + `packaging-parts/${encodeURIComponent(code)}`);
    const payload = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = (payload && payload.detail) || {};
      const message = typeof detail === "string" ? detail : String(detail.message || "");
      throw new Error(message || `读取零件详情失败（HTTP ${res.status}）`);   // ← 404 与 5xx 同一句
    }
    renderPackagingPartPanel(payload);
    highlightPackagingBusinessPartSelection(code, payload);
  } catch (error) {
    if (facts) facts.textContent = String((error && error.message) || error);  // ← 断网时是英文原生文本
  }
```

后端那半早已分家：`main.py:8078` 在零件不在文档里时回 **404 + `{code: "PACKAGING_PART_NOT_FOUND"}`**，
未生成文档回 **200 + `built:false`**（`PACKAGING_PART_READ_PATH` `:8009`）—— 前端一个都没用。

## 2. 契约

### C1 新增纯函数 `packagingPartDetailReadProblemText(problem) -> string`

- 体内**不得**出现 `document.` / `window.` / `fetch(` / `localStorage`（可被 `node -e` 抽出来真跑）；
- 判据顺序固定，**先认码、再认状态**：
  1. `problem.code` 去空白后为空 → `""`（没有码就没有这件事）；
  2. `code === "PACKAGING_PART_NOT_FOUND"` →
     `这一件已经不在当前的零件文档里了（可能重跑过图纸解析）；请点左栏重新选一件`；
  3. `Number(problem.status) > 0` → `暂时读不到这一件（HTTP <n>），请稍后重试；这不代表这一件没有数据`；
  4. 其它（缺状态码 / `0`）→ `暂时读不到这一件（网络错误），请稍后重试；这不代表这一件没有数据`。
- 第 2 条**只**认码：状态码是 404 但码不是 `PACKAGING_PART_NOT_FOUND` 时走第 3 条（"读不到"），
  不许把「读不到」说成「没这件」。

### C2 `selectPackagingPart()` 用这一处出处产文案

- `fetch` 抛异常 → `status` 给 `0`；非 2xx → `status` 给 HTTP 码、`code` 取载荷
  `detail.code`（`detail` 是字符串时 `code` 给 `""`，走第 3/4 条）；
- 文案**只**来自 `packagingPartDetailReadProblemText()`；`#packagingPartFacts` 里不再出现
  `String((error && error.message) || error)` 这种原生文本；
- 不抛出去（函数仍返回 `currentSelectedPanelPart`），右栏占位「正在读取零件详情…」的既有行为不变；
- **200 路径逐字不变**：`renderPackagingPartPanel(payload)` +
  `highlightPackagingBusinessPartSelection(code, payload)`，`built:false` 的 200 仍按面板既有空态渲染。

### C3 冻结面

- 不改后端（`PACKAGING_PART_READ_PATH` / `PACKAGING_PART_NOT_FOUND` / 响应键一行不动）、
  不加接口、不加依赖、不改 CSS 变量；
- 不改 `renderPackagingPartPanel()` / `showPackagingPartPane()` / `setRightPane()` /
  `markSelection()` / `highlightPackagingBusinessPartSelection()` 的行为；
- 不改 `packagingPartProcessability()` 与下游按钮的置灰口径；
  `node --check tech_app/frontend/app.js` 必须通过。

## 3. 允许修改范围

1. `tech_app/frontend/app.js`：新增纯函数 `packagingPartDetailReadProblemText()`；改
   `selectPackagingPart()` 的失败分支；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许把 404（没这件）与 5xx / 网络（读不到）混成同一句，也不许把「读不到」说成「没这件」；
- 不许把服务端 `detail.message` 或浏览器原生英文文本直接贴进界面；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_part_detail_read_failure_red -v
node --check tech_app/frontend/app.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_panel_red \
  tests.test_packaging_parts_read_failure_empty_state_red \
  tests.test_packaging_parts_downstream_red \
  tests.test_packaging_cad_plan_read_failure_red
```

## 6. 已记录的边界

1. 本批只做**读失败的两态分家与文案**：不重试、不自动重载零件文档、不自动改选中态；
2. 「这一件不在文档里」不等于「这个项目没有零件」：那是左栏空态（另一条口径），本批不动；
3. 404 但码不是 `PACKAGING_PART_NOT_FOUND`（例如中间层回的 404）按「读不到」处理 —— 宁可说"稍后重试"
   也不误报"这一件没了"。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_part_detail_read_failure_red
# 实现前：Ran 10 tests … FAILED (failures=6)   ← T1–T6
# 实现后：Ran 10 tests … OK                    ← S1–S4 四条护栏始终绿
node --check tech_app/frontend/app.js          # OK
```

| 契约 | 落点（`tech_app/frontend/app.js`） |
| --- | --- |
| §C1 纯函数 | `packagingPartDetailReadProblemText(problem)`：`code` 空 → `""`；`PACKAGING_PART_NOT_FOUND` → 「这一件已经不在当前的零件文档里了（可能重跑过图纸解析）；请点左栏重新选一件」；否则 `status > 0` → 「暂时读不到这一件（HTTP n）…」；再否则网络错误那一句。体内无 `document.` / `window.` / `fetch(` / `localStorage` |
| §C2 两态分家 | `selectPackagingPart()`：`fetch` 抛异常 → `{code: "parts_unavailable", status: 0}`；非 2xx → `{code: detail.code \|\| "parts_unavailable", status: res.status}`；两条路径都只把 `packagingPartDetailReadProblemText()` 的文案写进 `#packagingPartFacts` 并 `return currentSelectedPanelPart`（不再 `throw` / 不再贴 `String((error && error.message) \|\| error)`）；200 路径仍是 `renderPackagingPartPanel(payload)` + `highlightPackagingBusinessPartSelection(code, payload)` |
| §C3 冻结面 | 后端 `main.py` 一行未改（`PACKAGING_PART_NOT_FOUND` / `PACKAGING_PART_READ_PATH` 逐字未动）；面板渲染 / 下游按钮（`packagingPartProcessability()`）未动 |

**落点位置的既有约束（踩过一次）**：新函数最初插在 `selectPackagingPart()` 之前（`:1300` 一带），
落进了 `tests/test_packaging_parts_downstream_red.py::F3` 的 `packagingPartProcess` **+4000 字窗口**
（源码 `:2250-2251` 早就写明「② 不许落在 … +4000 字窗口里」），把 `CadInlineAnalysis` 顶出了窗口 →
该守卫转红。已把函数搬到 `packagingPartsPageReadProblemText()` 之后（`:2143` 一带，窗口之外），
F3 立即恢复绿。**本批没有改任何测试**。

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_panel_red tests.test_packaging_parts_read_failure_empty_state_red \
  tests.test_packaging_parts_downstream_red tests.test_packaging_cad_plan_read_failure_red \
  tests.test_packaging_parts_extraction_red tests.test_packaging_parts_outline_red
  → Ran 112 … OK
```

未改后端、未改 `tests/` 下任何文件、未连 PG / 34、未写生产数据、未 push / MR / tag / Release / 未部署。

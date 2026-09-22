# 规格：图纸解析链路面板「读不到状态」不许沉默，也不许当成「没跑过」

依赖：`docs/specs/drawing-flow-frontend-wiring.md`（面板与 `fetchDrawingFlowState()` 的来源）、
`docs/specs/drawing-flow-parse-terminal-signal.md`（§C1 那条终态信号与 `renderDrawingFlowPanel()`
既有字段，本批**只加**一条读失败分支，不动它们）、
`docs/specs/packaging-parts-read-failure-empty-state.md`（同族的「404 与 5xx 分家 + `read_problem`」口径）。

状态：Spec + 红测（已实现）（原状：`tech_app/frontend/app.js:1057-1069` 的
`fetchDrawingFlowState()` 把非 2xx 与网络异常一起折成 `null`，`loadDrawingFlowPanel()` 再
`if (state) render…` —— 于是「链路状态读不到」与「这个项目还没点过一键解析」长得一模一样：
面板**什么都不说**，上一次的内容也照旧留着，没有任何"这一次没读到"的痕迹）
红测：`tests/test_packaging_drawing_flow_read_failure_red.py`
行号基线：HEAD `c677471`

## 0. 一句话目标

2.1 的「图纸解析链路」面板在**读不到**链路状态时必须自己说一句
「暂时读不到图纸解析链路状态（HTTP n / 网络错误），这不代表这个项目没跑过一键解析」，
而不是沉默、也不是把旧内容当成本次结果。

## 1. 现状缺口（代码级）

```js
async function fetchDrawingFlowState() {                                   // :1057
  const res = await fetch(`${API}/api/projects/${currentProject}/drawing-flow`);
  if (!res.ok) return null;                                                // ← 404 与 5xx 同一句
  return res.json().catch(() => null);                                     // ← 正文不可用也折成 null
}

async function loadDrawingFlowPanel() {                                    // :1067
  const state = await fetchDrawingFlowState().catch(() => null);            // ← 网络异常同样 null
  if (state) renderDrawingFlowPanel(state);                                 // ← 读不到 = 什么都不做
}
```

后果：

1. 服务端 500 / 正文不可用 / 断网时，面板**一个字都不说**（`renderDrawingFlowPanel()` 根本不被调用）；
2. 面板上若还留着上一次读到的步骤表，用户会把它当成本次结果 —— 「读不到」与「刚跑完」同形；
3. 与左栏零件 / 业务部件 / 平面图那几条读路径的口径不一致（那几条都已有 `read_problem` 通道）。

## 2. 契约

### C1 新增纯函数 `drawingFlowReadProblemText(problem) -> string`

- 体内**不得**出现 `document.` / `window.` / `fetch(` / `localStorage`（可被 `node -e` 抽出来真跑）；
- `problem.code` 去空白后为空 → `""`；
- `Number(problem.status) > 0` →
  `暂时读不到图纸解析链路状态（HTTP <n>），这不代表这个项目没跑过一键解析`；
- 其它（缺状态码 / `0`）→ `暂时读不到图纸解析链路状态（网络错误），这不代表这个项目没跑过一键解析`。

### C2 `fetchDrawingFlowState()` 三态分家

| 情形 | 返回 |
| --- | --- |
| **404**（端点未上线 / 老服务） | `null`（**既有路径逐字不变**：与"还没跑过"同形，不报读失败） |
| 其它非 2xx | 带 `read_problem` 的空状态形状：`{steps: [], cad_ir: {}, read_problem: {code: "drawing_flow_unavailable", status: <HTTP 码>, message: ""}}` |
| 200 但正文解不出 | 同上，`code` 用 `drawing_flow_body_unexpected`、`status` 用响应码（**不许**折成 `null`） |
| `fetch` 抛异常 | 同上，`code` 用 `drawing_flow_unavailable`、`status` 给 `0` |

请求地址与 HTTP 方法不变（仍是 `@app.get("/api/projects/{pid}/drawing-flow")`）。

### C3 `loadDrawingFlowPanel()` 不再"读到空就不画"

- 拿到 `null`（只可能是 404）→ 保持既有行为：什么都不做、不报错；
- 拿到任何对象（含带 `read_problem` 的那种）→ 一律 `renderDrawingFlowPanel(state)`，
  由面板把「读不到」说出来；**不许**再把非 2xx 折成"不渲染"。

### C4 `renderDrawingFlowPanel()` 加一条第一优先分支

- `payload.read_problem` 非空 → 面板标题仍照旧，正文只渲染
  `drawingFlowReadProblemText(read_problem)` 那一句（沿用既有 `drawing-flow-empty` 类名），
  **不**渲染步骤表、**不**渲染 CAD IR 摘要（那两处会把"读不到"伪装成"跑过但为空"）；
- 没有 `read_problem` 时，既有渲染（步骤表 `title` / `status` / `error_code` / `error_message`、
  CAD IR 摘要「实体 / 图层 / 单位 / 类型计数」、空态「CAD IR：尚无解析结果」）**逐字不变**；
- 不改 `drawingFlowTerminalSignal()`（终态信号那一批的口径）与 `ensureDrawingFlowPanel()`。

### C5 冻结面

- 不改后端（`@app.get("/api/projects/{pid}/drawing-flow")` 的路由与响应一行不动）、不加接口、不加依赖、
  不改 CSS 变量；`node --check tech_app/frontend/app.js` 必须通过；
- 新版函数**不许**落在 `tests/test_packaging_parts_downstream_red.py::F3` 的
  `packagingPartProcess` +4000 字窗口里（源码 `:2250-2251` 的既有约束，同族批次踩过一次）。

## 3. 允许修改范围

1. `tech_app/frontend/app.js`：新增纯函数 `drawingFlowReadProblemText()`；改
   `fetchDrawingFlowState()` / `loadDrawingFlowPanel()` / `renderDrawingFlowPanel()` 三处；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许把 404（未上线）说成「读不到」，也不许把「读不到」说成「这个项目没跑过」；
- 不许把服务端 `detail.message` 或浏览器原生英文文本贴进面板；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_drawing_flow_read_failure_red -v
node --check tech_app/frontend/app.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_drawing_flow_parse_terminal_signal_red \
  tests.test_drawing_flow_frontend_wiring_red \
  tests.test_packaging_parts_downstream_red \
  tests.test_packaging_part_detail_read_failure_red
```

## 6. 已记录的边界

1. 本批只做**读侧的"说出来"**：不重试、不轮询、不清空上一次的步骤表以外的东西（读失败时面板正文整块换成那一句）；
2. 404（端点未上线）仍按「还没跑过」处理 —— 与左栏零件那侧一致（那是一个"什么都没有"的真实状态，不是读失败）；
3. 「跑过、但某一步 failed/blocked」的终态信号仍是 `drawingFlowTerminalSignal()` 的口径，本批不碰。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_drawing_flow_read_failure_red
# 实现前：Ran 11 tests … FAILED (failures=7)   ← T1–T7
# 实现后：Ran 11 tests … OK                    ← S1–S4 四条护栏始终绿
node --check tech_app/frontend/app.js          # OK
```

| 契约 | 落点（`tech_app/frontend/app.js`） |
| --- | --- |
| §C1 纯函数 | `drawingFlowReadProblemText(problem)`（放在 `fetchDrawingFlowState()` 之前）：`code` 空 → `""`；`status > 0` → 「暂时读不到图纸解析链路状态（HTTP n），这不代表这个项目没跑过一键解析」；否则网络错误那一句。体内无 `document.` / `window.` / `fetch(` / `localStorage` |
| §C2 三态分家 | `fetchDrawingFlowState()`：`fetch` 抛异常 → `unavailable("drawing_flow_unavailable", 0)`；`Number(res.status) === 404` → `null`（既有路径逐字不变）；其它非 2xx → `unavailable("drawing_flow_unavailable", res.status)`；200 但正文不是对象 → `unavailable("drawing_flow_body_unexpected", res.status)`。`unavailable()` 是函数内一处构造：`{steps: [], cad_ir: {}, read_problem: {code, status, message: ""}}`；请求地址与方法未动 |
| §C3 面板不再沉默 | `loadDrawingFlowPanel()`：`if (!state) return null;`（只可能是 404）否则 `return renderDrawingFlowPanel(state)` —— 带 `read_problem` 的形状照样画 |
| §C4 第一优先分支 | `renderDrawingFlowPanel()` 在 `panel.hidden = false;` 之后先看 `payload.read_problem`：非空 → 标题照旧 + 正文只写 `drawingFlowReadProblemText()` 那一句（沿用 `drawing-flow-empty` 类名）、`return panel`；否则既有渲染（步骤表 `title`/`status`/`error_code`/`error_message`、CAD IR 摘要、`CAD IR：尚无解析结果`）逐字不变 |
| §C5 冻结面 | 后端 `main.py` 一行未改（`@app.get("/api/projects/{pid}/drawing-flow")` 原样）；`drawingFlowTerminalSignal()` 体内不含 `read_problem`（两套东西不混）；新函数放在 `packagingPartProcess` 标记**之前**，未落进 `F3` 的 +4000 字窗口 |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_drawing_flow_parse_terminal_signal_red tests.test_drawing_flow_frontend_wiring_red \
  tests.test_packaging_parts_downstream_red tests.test_packaging_part_detail_read_failure_red \
  tests.test_packaging_cad_plan_read_failure_red tests.test_drawing_board_two_column_parts_and_3d_red
  → Ran 94 … OK
```

未改后端、未改 `tests/` 下任何文件、未连 PG / 34、未写生产数据、未 push / MR / tag / Release / 未部署。

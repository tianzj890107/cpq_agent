# Spec：重新进入 2.1 必须读回**已经落库的那一版**零件（进入即读回 + 不许催人重新解析）

状态：Spec + 红测（已实现）（原状：本批只写 Spec 与红测：`openProject()` 进入项目时只拉链路状态、从来不读零件文档，红测 12 条中 4 条红；本行只补原因括注，正文一字未动）
红测：`tests/test_packaging_parts_entry_readback_red.py`
依赖：`packaging-dwg-parts-extraction.md`（零件文档落库）、
`packaging-parts-read-failure-empty-state.md`（读失败文案分层）、
`packaging-parts-list-visibility-and-kinds.md`（分页 / 空态必须说清原因）、
`e2e-packaging-dwg-quote-tech-continuity.md` §4.1（链路终态后要重拉零件文档）、
`packaging-parts-selectable-panel.md`（2.1 左栏 = 零件文档）

## 0. 一句话目标

2.1 每次**进入**（刷新、深链、从首页 / 历史再进来，或内部切回本阶段）都要把
**已经落库的那一版零件文档**读回来画进左栏；没读回来时，不许把
「零件文档还没生成，请先跑一键解析图纸」当成默认结论 —— 那句话会让用户重跑一遍本来就有的东西。

## 1. 用户反馈（原话）

> 为什么过往解析出来的零件 重新进入这个项目的 2.1 之后 零件就都没有了 要重新解析。

## 2. 事实依据（2026-09-23 本机隔离实测，都可复现）

### 2.1 服务端**没有**丢零件

隔离 `DATA_DIR` 跑真实样本 `裕同包装项目-待开发/酒盒.dwg` 的完整链路：

```
八步 8/8 completed（file_preflight / dwg_convert / cad_ir_parse / packaging_semantics /
                   parts_extract / field_write / pending_confirm / downstream_prepare）
同进程读回：parts_id=parts:9b0069978377e477  parts=263
新进程读回：parts_id=parts:9b0069978377e477  parts=263      ← 换一个进程再读，同一版、同一批件
落盘：DATA_DIR/<pid>/packaging_parts.json（2.1 MB，版本化 items 列表）
```

即：零件文档是**持久化**的，"重新进入"不会让它消失。

### 2.2 丢的是「进入时根本没人读」

`tech_app/frontend/app.js` 里 `refreshPackagingParts()`（读零件文档 + 业务部件清单 + `renderTree`）
今天只有两个调用点：

| 调用点 | 位置 | 触发时机 |
| --- | --- | --- |
| 批量挤出之后 | `app.js:2160` | 用户点了"批量计算 3D 挤出体" |
| "一键解析"跑完之后 | `app.js:3293`（`refreshPackagingPartsAfterDrawingFlow`） | 用户当场点了"一键解析图纸" |

**进入项目这条路上没有任何调用点**：`openProject()`（`app.js:3806`）在 `entry === "drawing_flow"` 时
只调 `loadDrawingFlowPanel()`（`:3853`）去拉链路状态，从不读零件文档；深链 / 刷新走的
`afterAuth() → openProject().then(replayDrawingTimeline)`（`:540`）也不读。

于是进入后 `currentPackagingParts` 一直是 `null` → `renderTree()` 走空态分支
（`:4198-4203`）→ `packagingPartsEmptyText({}, preconditions)` 返回
「零件文档还没生成，请先跑一键解析图纸。」（`:2204`）—— 用户看到的就是"零件全没了，要重新解析"。

实测（在桩环境里把 `openProject('<pid>')` 跑一遍，记录所有 `fetch`）：

```
请求：["/api/projects/<pid>"]          ← 只有项目本身这一条
…/requirement/packaging-parts           ← 一次都没有
renderTree 调用次数：0
```

## 3. 唯一口径（本批裁决）

1. **事实源不变**：零件的事实源仍是 `packaging_parts` 的版本化文档（`DOC_KEY = "packaging_parts"`）；
   不带 `parts_id` 读 = 读**最新一版**（`load_parts(project_id)`）。本批不新增第二份零件事实源、不缓存到 localStorage。
2. **"有没有零件"必须靠读一次来回答**，不许用前端内存里"这次会话有没有跑过解析"来判定：
   进入 2.1 一律读一次。
3. **读不到 ≠ 没生成**：链路状态里 `parts_extract` 这一步已经 `completed`（`detail.parts_id` 非空
   或 `detail.parts_total > 0`）时，左栏空态**不许**说"还没生成 / 请先跑一键解析图纸"——
   那是错的下一步；要说清"这一刻没读到"并带上"已经有这一版零件"的证据。
4. **加载器只有一份**：`refreshPackagingParts()` 是**唯一**的零件文档加载器（它自己负责
   `renderTree` 与业务部件清单）；进入路径只许调它，不许自己拼第二份 URL、不许另写一套渲染。

## 4. 目标契约

### A. 进入即读回（前端 `tech_app/frontend/app.js`）

- **A1** `openProject()` 在**入口判定之后**（`renderDrawingEntry()` 已经给出 `entry`，
  `currentDrawingEntry` 已就位），当 `entry === "drawing_flow"` 时，必须发起一次零件文档加载
  `refreshPackagingParts()`（`await` 或不阻塞都可以，但必须真的发起）。
- **A2** 这次加载**独立于链路状态那一路**：`loadDrawingFlowPanel()` 这一次没读到 / 抛异常，
  不许把零件加载一起跳过（两条路的失败互不牵连，各自兜住）。
- **A3** 发起加载时 `currentProject` 必须已经是本项目（不许拿上一次打开的项目身份去读）。
- **A4** 重复进入幂等：每次进入都从第一页重新累加（沿用 `fetchPackagingParts()` 既有行为
  —— 它把 `packagingPartsShown` 重置成本页），不许把上一轮翻过页的行残留在页面上。
- **A5** 护栏：`openProject()` 里**不许**出现 `requirement/packaging-parts` 这条 URL 字面量；
  该 URL 仍只出现在 `fetchPackagingParts()` / `loadMorePackagingParts()` 两处。

### B. 链路已经产出过零件时，不许催人重新解析（前端 `packagingPartsEmptyText()`）

签名扩为 `packagingPartsEmptyText(partsDoc, preconditions, flowState)`（第三个参数可省略；
**省略时行为逐字保持今天**）。`flowState` 就是 `currentDrawingFlowState` 那份形状
（`GET .../drawing-flow` 的 `flow`：`{steps: [{step_id, status, detail}], …}`）。

- **B1** `read_problem` 优先（既有口径，一个字不改）。
- **B2** 有零件 → `""`（既有）。
- **B3** 没有零件、没有 `read_problem`，而 `flowState` 里 `parts_extract` 已 `completed`
  （`detail.parts_id` 非空，或 `detail.parts_total > 0`）时，必须逐字返回：

  ```
  这一刻读不到零件清单；链路里已经有这一版零件（parts_id <parts_id>，共 <parts_total> 件），请稍后重试或刷新页面 —— 不用重新解析图纸。
  ```

  `<parts_id>` / `<parts_total>` 逐字取自那一步的 `detail`；`parts_total` 不是正数时，
  「，共 N 件」这半句整段省略（其余逐字不变）。
- **B4** 没有链路证据时，逐字保持今天那句：`零件文档还没生成，请先跑一键解析图纸。`

### C. 服务端（**护栏**：本批不改行为，只把"数据本来就没丢"钉住）

- **C1** 零件文档落库后，**换一个后端实例**（= 换进程 / 换请求）读回必须是同一
  `parts_id` 与同一批行。
- **C2** `load_parts(project_id)` 不带 `parts_id` 时回**最新一版**；同一 `parts_id` 重复落库是覆盖，
  不产生第二条记录。

## 5. 非目标（不许顺手改）

- 不改零件提取的算法、阈值、角色判定与 CAD IR 语义；
- 不改读接口的响应形状（`built` / `items` / `total` / `kind_total` / `summary` / `part_columns` …）；
- 不改分页与筛选口径（`packaging-parts-list-visibility-and-kinds.md` §2.4）；
- 不动 3D 覆盖率、业务部件清单、BOM / 成本 / 工艺链路；
- 不改任何既有红测与门禁判据；不改 `packaging_parts` 的存储键名与版本化规则（`MAX_VERSIONS`）。

## 6. 落点

| 文件 | 改什么 |
| --- | --- |
| `tech_app/frontend/app.js` | `openProject()` 的 drawing_flow 分支加一次 `refreshPackagingParts()`（A）；`packagingPartsEmptyText()` 加第三个参数与 B3 那句话（B） |
| （服务端） | **不改**：C1 / C2 是护栏，今天就是绿的 |

## 7. 验收

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_entry_readback_red -v
# 实现前：Ran 12 … FAILED (failures=4)   ← A1 A2 A3 B1
# 实现后：Ran 12 … OK

# 不回归（零件与 2.1 面板那一圈）：
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_read_failure_empty_state_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_list_visibility_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red -v
node --check tech_app/frontend/app.js
```

真实样本（可选，只读）：`CPQ_DWG_REAL_SAMPLES=1` 那一组仍按各自 Spec 跑，本批不改它们。

## 8. 红基（2026-09-23，HEAD `c9a203c` 实测）

`tests.test_packaging_parts_entry_readback_red` → `Ran 12 … FAILED (failures=4)`：
A1 / A2 / A3（进入路径一次都没读零件文档）与 B1（已经有这一版零件时仍说"还没生成，请先跑一键解析"）红；
A4 / A5 / B2 / B3 / B4 / C1 / C2 是护栏，今天就是绿的，不许被改红。

未连 34、未写业务数据、未 push / MR / tag / Release。

## 9. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 442`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §4 A1/A2/A3 进入即读回 | `tech_app/frontend/app.js:openProject()` 的 `entry === "drawing_flow"` 分支 | 入口判定（`renderDrawingEntry()`）之后 `try { await loadDrawingFlowPanel(); } catch (…)` + `try { await refreshPackagingParts(); } catch (…)` —— 两条路各自兜住，链路状态抛异常也不跳过零件读回；发起时 `currentProject` 已是本项目 |
| §4 A4/A5 唯一加载器 | 同处 | `openProject()` 体内**没有** `requirement/packaging-parts` 字面量，复用的是 `refreshPackagingParts()`（唯一的零件文档加载器） |
| §4 B3 有链路证据时给证据 | `packagingPartsEmptyText(partsDoc, preconditions, flowState)` | 第三个参数省略时行为逐字不变；`parts_extract` 为 `completed` 且 `detail` 有 `parts_id` / 正的 `parts_total` 时返回「这一刻读不到零件清单；链路里已经有这一版零件（parts_id X，共 N 件），请稍后重试或刷新页面 —— 不用重新解析图纸。」；`blocked` 不算证据；证据取法**就地写在该函数里**（该纯函数被 `node` 单独抽出来执行，不许依赖同文件其它函数） |
| §4 B1/B2/B4 既有口径 | 同函数 | `read_problem` 优先、有零件回 `""`、没有证据时逐字保持「零件文档还没生成，请先跑一键解析图纸。」 |
| §4 C1/C2 服务端护栏 | 未改 | 换后端实例读回同一版、不带 `parts_id` 读最新一版（两条本来就绿） |
| §5 非目标 | 未动 | 提取算法 / 读接口响应形状 / 分页口径 / 3D 覆盖率 / 业务部件清单 / BOM·成本·工艺链路 / 存储键名与 `MAX_VERSIONS` 全部未改；`node --check app.js` 通过 |

### 9.1 红测 A1/A2 的观测点与它自己的桩互斥（实现无法绕过，需测试侧改 1 处）

`probe_entry()` 的沙箱把 `refreshPackagingParts()` **换成记录器**：

```js
refreshPackagingParts: async function () { marks.push("parts-loader"); state.loaderCalls += 1; … }
```

于是 `urls`（沙箱 `fetch` 的流水）里**永远**只会有 `openProject()` 自己发的那一条项目请求；
而 A1/A2 判的恰是 `[url for url in urls if "/requirement/packaging-parts" in url]` 非空 ——
"调了加载器"这条事实被记录在 `marks` / `state.loaderCalls` 里，**不在** `urls` 里。
唯一能让 `urls` 出现该 URL 的办法是让 `openProject()` 自己拼这条 URL 去 fetch，
可那正是 §4 A5 明令禁止、且 A4（`test_a4`）正在守的行为。

实测（本机跑红测自己的探针）：

```
{"urls": ["/api/projects/978876df2bbb"],
 "marks": ["entry-decision", "flow-panel", "parts-loader"],
 "state": {"loaderCalls": 1, "loaderProject": "978876df2bbb", "renderTreeCalls": 0, "error": ""}}
# flow-fails 变体同上：marks 里仍有 parts-loader —— §4 A2 的语义要求（互不牵连）已满足
```

即：**A1/A2 的语义（进入就调唯一加载器、且与链路状态那一路互不牵连）已按 Spec 落地**，
判错的只是观测点（该看 `marks` / `state.loaderCalls`，不是 `urls`）。
修法（测试侧，1 行）：A1/A2 改成断言 `out["marks"]` 含 `"parts-loader"`（A2 再断言 `flow-fails` 变体下仍然如此），
或让桩在 `refreshPackagingParts` 里 `urls.push("/requirement/packaging-parts")`。

复跑命令与结果：

```
node --check tech_app/frontend/app.js            # 通过
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_parts_entry_readback_red -v
A1 FAIL / A2 FAIL（§9.1 的观测点问题）/ A3 ok / A4 ok / A5 ok / B1 ok / B2 ok / B3 ok / B4 ok / B5 ok / C1 ok / C2 ok
  → 红基是 4 红 8 绿（A1/A2/A3/B1）；本批把 A3 与 B1 转绿，**没有把任何一条已绿的改红**，
    剩下 2 条红=§9.1 的测试侧观测点问题。

./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_extraction_red tests.test_packaging_parts_read_failure_empty_state_red \
  tests.test_packaging_parts_list_visibility_red tests.test_packaging_drawing_flow_red \
  tests.test_drawing_flow_parse_terminal_signal_red
Ran 136 tests ... OK (skipped=2)
```

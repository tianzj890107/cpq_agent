# 快速报价「能点完」：首页必须把工作区命令接上、读路径不许假装正常

血缘：`e2e-quick-quote-executable-path.md`（本层把它的 §3/§4/§6 验收从「源码里出现过」升到「页面上真点得到」）、
`quick-quote-6-case-library-readiness.md` §2（「不许假装空库」的**读路径**版本）、
`quick-quote-12-case-maintenance.md`（补字段 / 审核两个入口）、
`quick-quote-3-field-workspace-and-delta-price.md`（工作区与四列差异表）。

状态：Spec + 红测（已实现）（见 changelog ## 290 / ## 291）
红测：`tests/test_quick_quote_home_wiring_red.py`
依赖：`报价首页.html`、`tech_app/frontend/quick-quote-panel.js`、`cpq_agent_server.py`

## 0. 一句话目标

从首页点「快速报价」开始，人必须能**一路点到出价**：建实例 → 匹配 → 选基准 → 改参数 → 重算 → 出价。
今天这条路在页面上是断的 —— 七个命令函数都躺在面板里，首页**一个都没调用**，字段工作区与报价段
也从来没被画出来；于是"点完"只能点到第 3 步（看案例库）。

## 1. 线上证据（34，`http://172.16.10.34:8010`，2026-09-22，只读实测）

| 读法 | 真实结果 |
| --- | --- |
| `GET /agents/quote/api/quick-quote/cases` | 200；`case_total=2`、**`eligible_total=0`**、`verdict=no_eligible` |
| 同上的 `blocked_by` | `{"reason_code":"missing_fields","label":"缺必需字段","count":2,"fix":"补齐缺的必需字段：标准单价","cases":["QQ-YT-DWG-ROUND-10PC","QQ-YT-DWG-WINE-700ML"]}` |
| 同上的 `next_actions` | `fill_case_fields`（补齐案例字段）、`transfer_to_precise`（转精准报价） |
| `报价首页.html` 调用的面板 API | 只有 `open` / `fillCaseFields` / `reviewCase`：七个工作区命令 **0 次调用** |
| 面板 `render()` 实际画出的块 | 标题/模式、解析入口、五步、案例库、readiness、说明 —— **没有**字段工作区、**没有**报价段 |
| `报价首页.html` 里的工作区容器 | **不存在**（`确认需求解析结果.html:875` 有 `#quickQuoteWorkspace`，首页没有） |
| 首页 `onAction` 覆盖的动作 | `fill_case_fields` / `review_case` / `transfer_to_precise` —— **`save_quote` 没接** |

代码级事实（本机 HEAD 当场复现）：

- `tech_app/frontend/quick-quote-panel.js` 导出了 `openQuickQuoteSession` / `matchQuickQuoteCases` /
  `selectQuickQuoteBaseline` / `saveQuickQuoteWorkspace` / `repriceQuickQuote` / `confirmQuickQuote` /
  `transferQuickQuoteToPrecise` 与 `renderDiffTable` / `renderQuote`，但 `render()`（同一文件）只调用
  解析入口、五步、案例库、readiness —— **`renderDiffTable` / `renderQuote` 在模块内零调用点**。
- `selectQuickQuoteBaseline()` 在 `quickQuoteSessionId()` 为空时仍然 `postCommand("baseline")`，
  拼出的 URL 是 `/api/quick-quote/sessions//baseline`；服务端
  `QUICK_QUOTE_SESSION_RE = ^/api/quick-quote/sessions/(?P<session_id>[^/]+)/…` 里 `[^/]+` 匹配不上
  → `_quick_quote_write()` 返回 False → 落到 404 分支。用户看到的是"点了没反应"。
  实测复现方式：把 `cpq_quick_quote_match.build_baseline` 换成返回 `{"case_code":"X"}` 的桩后，
  直接调 `_handle_quick_quote_session_baseline("probe-xyz", …)` 能返回 `ok=True`，
  说明**服务端这条命令本身是好的**，断点在页面没接线与空 session 前置。
- `_handle_quick_quote_read`（`cpq_agent_server.py`）把 `cpq_quick_quote_price.find_quote(...)` 整个
  裹在 `except Exception` 里、失败就把 `saved` 留成 `{}`。实测：把 `find_quote` 换成抛 `NameError` 的桩，
  返回体是 `ok=True` + `quote={}` + `saved_quote={}`，**没有任何诊断键** ——
  「这个会话确实还没落过卡」与「报价存储这一路坏了」在响应里长得一模一样。

### 1.1 上一版验收为什么是绿的

`tests/test_e2e_quick_quote_executable_red.py` 的 UI 组只断言**源码里出现过这些 token**
（`self.assertTrue("selectQuickQuoteBaseline" in PANEL)`）。函数写在面板里、页面不调用，它一样绿。
这正是 `test_tech_backend_undefined_names_dynamic` 文档里点名的"纯文本 grep 型红测全绿、真实请求却 500"
的同一形状。本层把验收换成**调用点与行为**。

### 1.2 已收口、本层只留护栏

`cpq_agent_server.py` 曾按全局名使用 `cpq_quick_quote_price` / `cpq_quick_quote_workspace` 却没有模块级
`import`（真实请求 `NameError` → 500），已由 `## 287` 修掉（`undefined-names` 6 OK）。本层不重复立，
只把"这两个名字必须仍在模块级绑定"写成护栏，防回退。

## 2. 契约

### C1 首页必须接线七个命令（不是"函数存在"）

`报价首页.html` 对面板的调用必须真实覆盖：建实例 → 匹配 → 选基准 → 改参数 → 重算 → 出价 → 转精准。
判据是**首页源码里出现调用点**（`面板.函数名(` 或 `panel.函数名(`），不是面板里定义过。

### C2 字段工作区与报价段必须渲染出来

面板主体（`render()`）或首页必须调用 `renderDiffTable` 与 `renderQuote`，并给它们一个容器
（首页 `#quickQuoteWorkspace`，或面板自己的弹层里）。否则"改差异项""出价"在人眼里根本不存在。

### C3 出价段的两个动作必须由页面处理

`save_quote` / `transfer_precise` 必须在首页 `onAction` 里有分支；动作闭集
（`QUOTE_ACTIONS`）仍只有这两个，不许新增第三个出口。

### C4 session 为空时不许发请求

`selectQuickQuoteBaseline` 在 `quickQuoteSessionId()` 为空时必须**先建实例**（`openQuickQuoteSession`）
或给出可见错误，不得拼出 `/sessions//baseline` 这种注定 404 的请求。

### C5 读路径不许假装正常

`GET /api/quick-quote/sessions/{id}` 在 `find_quote` 失败（含 `NameError` 这类"处理器坏了"）时，
响应必须带可诊断信息（例如 `read_error`），**不得**静默返回空报价冒充"这个会话还没落过卡"。
正常读到空（确实没落过卡）时不许出现诊断键 —— 两种情形必须分得开。

### C6 资格不达标时不许出价

`eligible_total=0` 时页面必须给出「补齐案例字段」与「转精准报价」两条出口，且**不得**出现可点的出价按钮；
补齐必需字段并把案例审到 `reviewed` 之后，同一条案例必须立刻变 `eligible`。

### C7 护栏

- `cpq_agent_server.py` 里凡按全局名使用 `cpq_quick_quote_*` 的，模块级必须有 import（`## 287` 已收口）。
- 首页「快速报价」入口仍只对 `data-industries="packaging"` 可见（案例库是包装案例库）。
- 前端不得复制价格公式与金额计算（金额一律取后端）。

## 3. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_quick_quote_home_wiring_red -v   # 本层
node --check tech_app/frontend/quick-quote-panel.js
./open-claude/.venv/bin/python -m unittest tests.test_e2e_quick_quote_executable_red        # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_quick_quote_case_library_readiness_red # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_tech_backend_undefined_names_dynamic    # 不回归
```

## 4. 与既有 Spec 的关系（不重复立第二套）

- 命令路由与幂等口径：仍以 `e2e-quick-quote-executable-path.md` §3 为准，本层只加"页面必须真的调"。
- 资格判定与修复动作：仍以 `quick-quote-6-case-library-readiness.md` 为准，本层只加"资格必须挡在出价前"。
- 补字段 / 审核的写路径：仍以 `quick-quote-12-case-maintenance.md` 为准，本层不动它的字段闭集。

## 5. 边界

- 本层只写 Spec + 红测，不含业务实现；不改 `tests/` 下任何既有文件；不改命令路由、费率、公式与角色门槛。
- 34 上的现网数据动作（给 `QQ-YT-DWG-WINE-700ML` / `QQ-YT-DWG-ROUND-10PC` 补标准单价并审到已审核）
  是**数据操作**，不是本层代码交付；入口（面板「补齐案例字段」）已存在且是通的。

## 6. 实现记录（`## 290`）

### 6.1 改了什么（只这 3 个文件）

| 文件 | 改动 | 对应契约 |
| --- | --- | --- |
| `cpq_agent_server.py` | `_handle_quick_quote_read()`：`find_quote` 抛异常时把 `{type, message}` 作为 `read_error` 放进响应（`ok` 仍为 `True`，因为"刷新"本身成功了）；**正常读到空时一个诊断键都不带** | C5 |
| `tech_app/frontend/quick-quote-panel.js` | ① `selectQuickQuoteBaseline()` 在 `quickQuoteSessionId()` 为空时先 `openQuickQuoteSession()` 再选基准，杜绝 `/sessions//baseline`；② `saveQuickQuoteWorkspace()` 的 `edits` 缺省值 `[] → {}`（后端 `validate_edits()` 只认字典）；③ `render()` 末尾新增「字段工作区（差异项）」与「报价」两段，真的调用 `renderDiffTable` / `renderQuote` | C2、C4 |
| `报价首页.html` | ① `#quoteModeRow` 后新增 `<section id="quickQuoteWorkspace" hidden>`（7 个命令按钮 + `#qqWorkspaceBody`）；② 内联脚本新增工作区接线块（`runQuickQuoteWorkspaceCommand` / `renderQuickQuoteWorkspace` / `onQuickQuotePanelAction` / `syncQuickQuoteActionState` / `initQuickQuoteWorkspace`），七个命令**全部真实调用**；③ `openQuickQuotePanel()` 接住 `panel.open(...)` 的 `.then(payload => …)`，用后端 `eligible_total` 决定出价按钮可用性 | C1、C2、C3、C6 |

### 6.2 口径（不是新造的）

- 七个命令的路由与幂等仍以 `e2e-quick-quote-executable-path.md` §3 为准；本层只补"页面真的调"。
- 资格判定仍在后端；页面只消费 `eligible_total`，**不复制**任何判据与话术（C7 护栏 `test_e4` 守这条）。
- 动作闭集仍是 `QUOTE_ACTIONS = ["save_quote", "transfer_precise"]`（C3），`test_e2` 守这条。
- `selectQuickQuoteBaseline()` 自动建实例后若仍拿不到 session，返回 `{ok:false, error:"还没建立快速报价实例，无法选择基准案例"}` —— **不猜 session**、不写假 id。

### 6.3 现场复现用的独立冒烟（一次性，未入库）

node 打桩 `fetch` 指向面板模块：空 session 调 `selectQuickQuoteBaseline("QQ-X")` 时**不发任何 `/baseline` 请求**，
而是先打 `POST /api/quick-quote/sessions` → 拿到 id 后才打 `POST …/{id}/baseline`；把 `session` 命令打桩成
永远不返回 id 时，返回体是 `{ok:false, error:"还没建立快速报价实例…"}`，全程无 `/sessions//baseline`。

### 6.4 已知既有红（不是本层引入，勿修）

`tests/test_quick_quote_case_maintenance_red.py::test_f1_panel_action_constants_match_backend`：
面板里 `CASE_FIELDS_PATH` / `CASE_REVIEW_PATH` 由 `CASES_PATH + "/{case_code}/fields"` 拼出，而该用例要求
源码里出现**字面量**。属既有红，已记 Spec / changelog，本层不动它（动它会改 `tests/`）。

### 6.5 边界

未改 `tests/` 下任何文件（含本层红测）、未连 PG、未写生产数据；未改命令路由 / 费率 / 公式 / 角色门槛。

### 6.6 C5 后半句被 `quick-quote-full-flow-state-and-recovery.md` §6 收窄（2026-09-22）

§C5 的后半句（"正常读到空时不许出现诊断键"）**仍然成立**，但它说的是**存在的实例**里还没落过卡这一种情形。
新 Spec §6 另外规定：**GET 一个从未存在过的 session 一律 404 `session_not_found`**（禁止 `setdefault` 造幽灵实例）。
于是本层红测 `DReadPathHonestyRed::test_d2`（用一个从未存在的 id `wiring-probe-empty` 冒充"正常空值"）与新契约
**在机制上互斥**：旧契约下 `_qq_state()` 会把该 id 建成幽灵实例所以读得到 200 空值，新契约下同一 id 必须是 404 错误体，
而错误体必然带 `error` 键。这条偏差已记在 `quick-quote-full-flow-state-and-recovery.md` §12，**不改任何测试**。

### 6.7 该偏差已处置（`## 470`，Codex 测试侧；断言与期望值一字未改）

§6.6 已把根因说清：后半句讲的是**存在的实例里还没落过卡**，而探针从来没把「实例存在」这件事给出来 ——
于是它断言的对象变成了 `quick-quote-full-flow-state-and-recovery.md` §6 那条 404 契约。本批按本节措辞把它补上：

| 项 | 内容 |
| --- | --- |
| 改了什么 | `tests/test_quick_quote_home_wiring_red.py` 的 `DReadPathHonestyRed` 新增 `_existing_session()` 上下文管理器：只替换**进程内的存在性判定**（`mock.patch.object(cpq_agent_server, "_qq_existing", …)` 给一个内存里的实例状态），D1 / D2 在它里面读 |
| 为什么不落盘 | 实例仓储 `_JsonDocRepository` 是**持久化**的，真去 `_qq_state(..., create=True)` 会把探针 id 写进数据目录（违反本文件"不写业务数据"的纪律）；本批只替换存在性判定，一个字都不落盘 |
| 为什么不算放宽 | 三条断言逐字未动：D1 的「必须有 `error` / `diagnostic` 键」、D2 的「不许有」、键名判据。改掉的只是"夹具没把被断言的事实给出来" |
| D1 顺带被修实 | D1 原来读的也是一个不存在的 id → 404 错误体**必然**带 `error`，那条断言一直是**空转**（`find_quote` 的 `NameError` 根本没跑到）。现在实例存在，D1 才真的在测"仓库坏了必须说出来" |
| 反向对照 | 把存在性判定改回"不存在" ⇒ `D2 FAIL`（`Ran 12 … FAILED (failures=1)`）；恢复 ⇒ `Ran 12 … OK` |
| 连带 | 系列守卫 `tests/test_spec_status_consistency_red::C1`（"声明已实现、但点名的红测仍失败"）随之转绿 |

同批把此前调试时落下的探针污染收干净：`tech_app/data/cpq/quick_quote_sessions.json` /
`quick_quote_idempotency.json`（内容只有 `wiring-probe-xyz` / `wiring-probe-empty` 两条探针 id）
已移出仓库数据目录，`tech_app/data/cpq/` 整个目录不复存在。

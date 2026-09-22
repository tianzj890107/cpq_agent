# 规格：单件结论的「业务部件身份」必须出现在内嵌面板上（`## 410` 的界面那一半）

依赖：`docs/specs/packaging-part-conclusion-business-identity.md`（§C3 的读回四键：后端已经把
`business_part_code` / `business_parts_id` / `business_stale` / `business_stale_reason` 回在
两条 GET 上）、`docs/specs/packaging-parts-conclusion-version-readback.md`（§2.3：零件版本那句话
已经落在内嵌面板的**状态行**上，本批照同一条纪律把业务版本那句话落在**正文**里，不与它抢位）、
`docs/specs/packaging-silent-degradation-disclosure.md`（读回来却不显示 = 静默降级）。

状态：Spec + 红测（已实现）（现状：后端 ## 410 已经回四个键，`tech_app/frontend/inline-analysis.js`
只认 `parts_id` / `stale` / `stale_reason` —— `conclusionVersionNote()`（`:41`）三分支里没有一个字
提业务清单；`state`（`:63`）只有 `versionNote`；`renderProcess()` / `renderCost()` 的正文里
**没有**任何业务身份的位置。于是「业务清单重新导入过、这份结论是上一版清单算的」在界面上
完全看不出来 —— 四个键回到了浏览器，然后被丢掉）
红测：`tests/test_packaging_part_conclusion_business_identity_in_panel_red.py`
行号基线：HEAD `37eca21`

## 0. 一句话目标

打开一件零件的「工艺推荐 / 成本测算」时，正文里说得出来这份结论是照**哪一件业务部件、哪一版
业务清单**算的；业务清单重新导入过就明说"请重跑后再用"。

## 1. 现状缺口（代码级）

1. `inline-analysis.js:41 conclusionVersionNote()` 只处理零件版本：
   `stale === true` → 上一版零件那句；`stale_reason === "parts_unknown"` → 判断不了那句；
   其余 → `""`。业务清单漂移没有任何分支。
2. `inline-analysis.js:144` 把读回体交给它之后只留了 `state.versionNote`，四个业务键
   （`business_part_code` / `business_parts_id` / `business_stale` / `business_stale_reason`）
   在 `load()` 之后**没有任何消费者**。
3. `renderProcess()`（`:358`）/ `renderCost()`（`:469`）的正文卡片全是工序 / 成本内容，
   不显示结论的口径 —— 用户只能靠"重新点一遍"猜自己看的是哪一版。

## 2. 契约

### C1 新增顶层纯函数 `packagingPartBusinessIdentityNote(payload) -> {text, level}`

- 落点：`tech_app/frontend/app.js` **顶层**（不是 IIFE 内部 —— 必须能被 `node -e` 抽出来真跑）；
- 体内**不得**出现 `document.` / `window.` / `fetch(` / `localStorage`；
- `level` 是闭集：`("", "info", "stale", "unknown", "unbound")`（只在这一个函数里出现，
  不做模块常量 —— 免得函数体外的依赖让它没法被 `node -e` 单跑）；`text` 一律是本地文案
  （后端只给码）；
- 判据顺序固定（先命中先返回）：
  1. `payload` 不是对象，或 `business_parts_id` 去空后为空 → `{"text": "", "level": ""}`
     （本批之前落的结论没有业务身份，**不许**说成"没绑业务件"）；
  2. `business_stale === true` → `level: "stale"`，text =
     `这份结论是按上一版业务部件清单算的，请重跑后再用。`
     （**只**用本地文案：`business_parts_reimported` 这类码不贴给用户）；
  3. `business_stale_reason === "business_parts_unknown"` → `level: "unknown"`，text =
     `判断不了这份结论对应哪一版业务部件清单。`；
  4. `business_part_code` 去空后非空 → `level: "info"`，text =
     `业务部件 <code>（清单 <short>）`，其中 `short` = `business_parts_id` 的
     **前 12 个字符**，被截断时补一个 `…`（不超过 12 个字符就原样给）；
  5. 其余（有清单版本、但没有业务部件编码）→ `level: "unbound"`，text =
     `这一件没有绑到业务部件，结论按几何零件算的。`；
- 任何输入都不抛错：`null` / 字符串 / 数字字段 / 缺键一律按上面的规则兜住。

### C2 `inline-analysis.js` 接线

- `state` 初始多一个 `businessNote: ""`；`load()` 里在既有 `state.versionNote = ...` **之后**
  多一行 `state.businessNote = packagingPartBusinessIdentityNote(data);`
  （`data` 就是那条读回体；**不**新增请求、不改 `endpointBase()`）；
- `renderProcess()` 与 `renderCost()` 各自在正文**最前面**渲染一行
  `<div class="inline-warn" data-inline-business-note="<level>"><text></div>`：
  那一行的节点只有**一处**构造（IIFE 内的 `businessIdentityRow(state)`），两个正文各调用一次；
  `text` 为空时**一个节点都不渲染**（既有正文逐字不变）；
- 状态行（`setStatus`）**不**承载这句话：`versionNote` 的既有拼法逐字不变
  （零件版本那句优先级不动，两句话不互相顶掉）。

### C3 冻结面

- `conclusionVersionNote()` 的三个分支与文案逐字不变；
- 不改 `endpointBase()` / `lookupPath()` / `generate()` / `poll()` / `save()` 的既有行为；
- 不改 `app.js` 里既有的 `packagingPartAnalyze()` / `packagingBusinessPartAnalyze()` /
  `packagingPartBusinessIdentityNote` 之外的任何零件面板逻辑；
- 不改后端（`main.py` / `packaging_parts.py` 一行不动）；不改 `tests/` 下任何文件；
- `node --check tech_app/frontend/app.js` 与 `node --check tech_app/frontend/inline-analysis.js`
  必须通过。

## 3. 允许修改范围

1. `tech_app/frontend/app.js`：新增顶层纯函数 `packagingPartBusinessIdentityNote()`；
2. `tech_app/frontend/inline-analysis.js`：`state` / `load()` / `renderProcess()` /
   `renderCost()` 四处接线；
3. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许把 `business_parts_reimported` / `business_parts_unknown` 这类码原样贴给用户；
- 不许在"没有业务身份"时说成"没绑业务件"（那是两件事）；
- 不许新增请求 / 轮询 / 缓存；不许把业务那句话塞进状态行去顶掉零件版本那句；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_part_conclusion_business_identity_in_panel_red -v
node --check tech_app/frontend/app.js
node --check tech_app/frontend/inline-analysis.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_conclusion_version_readback_red \
  tests.test_packaging_part_conclusion_business_identity_red \
  tests.test_packaging_parts_panel_red \
  tests.test_packaging_parts_downstream_red \
  tests.test_packaging_business_part_downstream_entry_red \
  tests.test_tech_part_detail_chrome_and_action_cards_red \
  tests.test_tech_cad_batch_partial_generation_red \
  tests.test_chat_fused_assistant_card_style_red
```

## 6. 已记录的边界

1. 只做**显示**：业务清单换版不触发任何重算（重跑仍要用户点），也不改结论本身；
2. 本批不碰 `app.js` 的业务部件主面板（`openPackagingBusinessPart()`）—— 那里显示的是
   业务件自己的权威资料，不是结论口径；
3. 三句话都是**本地文案**：后端只给码，前端不许把码拼进用户可见文本（后续加码只改文案表）。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_part_conclusion_business_identity_in_panel_red
# 实现前：Ran 18 tests … FAILED (failures=13)   ← 13 红：A1–A10 / B1 / B2 / C3
# 实现后：Ran 18 tests … OK                     ← B3 / B4 / C1 / C2 / C4 五条护栏始终绿
node --check tech_app/frontend/app.js              # OK
node --check tech_app/frontend/inline-analysis.js  # OK
```

| 契约 | 落点 |
| --- | --- |
| §C1 纯函数 | `tech_app/frontend/app.js:2196 packagingPartBusinessIdentityNote(payload)`（顶层，紧跟 `packagingPartDetailReadProblemText()` 之后）：五态 `""` / `stale` / `unknown` / `info` / `unbound`；判据顺序 = 没有 `business_parts_id` → 什么都不说 → `business_stale === true` → `business_stale_reason === "business_parts_unknown"` → 有 `business_part_code` → 其余；`info` 的清单短号 = 前 12 字符（超过才补 `…`）；三句文案全是本地字面量，码不贴给用户；体内无 `document.` / `window.` / `fetch(` / `localStorage` |
| §C2 一行节点 | `inline-analysis.js:54 businessIdentityRow(state)`：`note.text` 为空 → 返回 `""`（一个节点都不渲染）；否则 `<div class="inline-warn" data-inline-business-note="<level>">…</div>` |
| §C2 接线 | `inline-analysis.js:73`（`state.businessNote` 初始 null）、`:157`（`load()` 里 `state.businessNote = packagingPartBusinessIdentityNote(data);` —— 既有的 `state.versionNote = …` 逐字不动）、`:383`（`renderProcess` 正文最前面）、`:490`（`renderCost` 正文最前面） |
| §C3 冻结面 | `conclusionVersionNote()` 三分支逐字未动；`setStatus(state, state.versionNote || …)` 一字未改、业务那句话不进状态行；`jsonFetch(` 6 处、`endpointBase(` 4 处、`process-lookup` / `cost-lookup` 各 1 处都没变；后端 `main.py` / `packaging_parts.py` 一行未改；`tests/` 下一行未改 |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parts_conclusion_version_readback_red \
  tests.test_packaging_part_conclusion_business_identity_red \
  tests.test_packaging_parts_panel_red tests.test_packaging_parts_downstream_red \
  tests.test_packaging_business_part_downstream_entry_red \
  tests.test_tech_part_detail_chrome_and_action_cards_red \
  tests.test_tech_cad_batch_partial_generation_red tests.test_chat_fused_assistant_card_style_red
  → Ran 152 … OK

./open-claude/.venv/bin/python -W ignore -m unittest discover -s tests -p 'test_packaging_*.py'
  → Ran 1919 … FAILED (failures=5, skipped=8)   ← 仍是那 5 条既有挂账，本批未引入新红
```

未连 PG / 34、未写生产数据、未 push / MR / tag / Release / 未部署。

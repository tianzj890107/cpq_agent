# Spec：会话里「我：…」回声必须与 Agent 输出成对 —— 被拒的动作连回声都不发

状态：Spec + 红测（已实现）（原状：点「一键解析图纸」而按钮还是灰的（还在加载 / 3D 导入项目）时，
会话里先落下一句用户回声、再没有任何 Agent 输出 —— 看板动作回 `{ok:false, code:"not-ready"}` 时
`renderTaskProgress()` 提前 return，既不建卡也没有别的 Agent 行；连续点几次就是一串只有用户气泡的会话。
2026-09-23 实测：点名的红测 `tests/test_chat_echo_must_pair_with_agent_output_red.py` 已
`Ran 18 … OK`，本行按状态守卫 `tests.test_spec_status_truth_red` 随事实更正）
红测：`tests/test_chat_echo_must_pair_with_agent_output_red.py`
血缘：`docs/specs/tech-agent-echo-bubble-and-single-exec-card.md`（§1 决定一「只给 **Agent 主动发起、
并且会真的跑起来** 的动作补一条用户气泡」—— 本批把这句话在**运行时**收口：被拒的回执不算"跑起来"）、
`docs/specs/tech-load-states-and-progressive-load.md`（§2.2 把「还在加载」与「被拒」分开之后，
被拒这一类必须有明确的气泡口径）。
本批 changelog 条目号：`## 479`。

## 1. 用户原话与实测证据

> 点击解析图纸的时候只有用户气泡没有 agent 输出气泡 要有就都要有 要没有就都没有
> 不能一连串用户气泡没有 agent 气泡

| 读数 | 实测（HEAD `0312614` 工作副本） |
| --- | --- |
| `tech-board-runtime.js` `runEntry()` | 调 `entry.run()` **之前**就发 `TASK_PROGRESS`（载荷带 `prompt`）；动作返回 `{ok:false, error:{code}}` 时再发 `TASK_FAILED`（同 `runId`）|
| `agent-chat.js` `renderTaskProgress()` | 第一句就是 `echoTaskPrompt(detail)` —— **不论后面会不会有 Agent 输出**，先把用户气泡落下去 |
| 同一函数 | `const hasContent = log.length > 0 \|\| existingCard \|\| Boolean(blockedReason); if (!hasContent && !streamLength) return;` ⇒ 没有进度行的事件（正是 `not-ready` / `loading` / `busy` 这类**被拒**回执）**不建卡、也不写别的 Agent 行** |
| 结果 | 会话里留下「我：帮我解析这张图纸。」，下面空空如也；再点几次就是一串用户气泡 |
| `app.js:5622-5625` | `not-ready`（按钮灰）/ `busy`（正在解析）两类回执都会走到上面这条路 |
| 既有护栏 | `tests/test_tech_agent_echo_bubble_and_single_exec_card_red.py` 钉看板侧载荷（`prompt` 字段与 5 个声明动作）**外加一条钉左侧字面顺序的断言**（`test_bubble_comes_before_the_no_content_early_return`）。那条被本批 C3 正面取代，按 §5.1 书面授权**重指**（意图保留、未放宽）；该文件其余 38 条一条未动。（本行原文写「本批不动它的任何断言」，与实测不符，随 §5.1 一并更正）|

## 2. 契约

### 2.1 C1：被拒回执的闭集只有一处

`agent-chat.js` 新增常量与判定（唯一来源，别处不许再抄一份字符串列表）：

```
const REJECTED_BOARD_CODES = ["not-ready", "loading", "busy", "no-project", "no-part",
                              "no-navigation", "not-parsed"];
function isRejectedBoardCode(code)   // 取 String(code) 精确比对，缺省 false
```

### 2.2 C2：回声只在「这一次真的开始执行」时才发

`agent-chat.js` 新增纯函数（体内无 `document` / `window` / `fetch(` / `localStorage` /
`sessionStorage`）：

```
function taskEchoAllowed(detail)   // true = 这一声「我：…」该发
```

| 入参 | 出参 |
| --- | --- |
| `detail.prompt` 去空白后为空 / 缺失 | `false`（没有那句话就没有回声） |
| `detail.code` 落在 `REJECTED_BOARD_CODES` | `false`（被拒的回执不是一次执行） |
| 其余（非空 `prompt`、`code` 为空或不在闭集） | `true` |
| `detail` 是 `null` / 字符串 / 数组 | `false`，且不抛异常 |

### 2.3 C3：回声与 Agent 输出必须成对（同一次事件内）

`renderTaskProgress()` 里：

- **先判定后回声**：`taskEchoAllowed(detail)` 的判定必须排在 `echoTaskPrompt(` **之前**；
- 回声判定结果必须参与建卡判定：`hasContent` 的表达式里带上这一声回声（例如
  `const hasContent = log.length > 0 || existingCard || Boolean(blockedReason) || echoAllowed;`）；
  也就是**不许**出现「发了回声、却因为 `!hasContent && !streamLength` 直接 return」的组合；
- 回声气泡必须插在**这一张卡的上方**：调用形式固定为 `echoTaskPrompt(detail, card)`，
  且 `echoTaskPrompt(detail, before)` 的第二形参叫 `before`，体内用
  `beginUserTurn(text, before)` / `addUser(text, before)` 落位（既有 `addUser(text, before)`
  的 `insertBefore` 语义不变）；
- 被拒回执（C1 闭集）：**既不回声、也不建卡**（看板自己就地提示），会话里不留任何半截气泡。

### 2.4 C4：护栏

- `tech-board-runtime.js` 照旧在 `TASK_PROGRESS` 里带 `prompt`（`tests/test_tech_agent_echo_bubble_and_single_exec_card_red.py`
  的载荷契约不受影响）；
- 既有的「不建空卡」纪律不变：没有 `prompt`、没有进度行、没有阻断原因的秒级同步动作，**不许**
  因为它们而新建卡；
- `QUIET_BOARD_CODES` / `INTERRUPTED_CODES` / `isInterruptedCode()` 的语义与取值一个字不改；
- 真人打字发送（`send()`）与历史回放（`replayingHistory`）两条路照旧出用户气泡，不受本批判定影响；
- 独立 2.1 页（无父壳桥）里 `requestParse()` 的既有系统行
  （`当前还不能解析：请先在 ＋ →「补充需求图纸」里上传需求原图并创建评估任务。`）逐字保留 ——
  那是 Agent 侧说明，不是回声。

## 3. 允许修改范围

1. `tech_app/frontend/agent-chat.js`：`REJECTED_BOARD_CODES` / `isRejectedBoardCode()` /
   `taskEchoAllowed()`；`renderTaskProgress()` 的回声判定顺序与建卡判定；
   `echoTaskPrompt(detail, before)` 的落位方式。
2. `changelog/changelog_9_21_25.md`：`## 479`。

禁止：改 `tech-board-runtime.js` 的载荷字段；改既有 5 个动作的 `prompt` 文案与声明数量；
改 `QUIET_BOARD_CODES` / `INTERRUPTED_CODES`；改业务动作本身的错误码；
改任何既有测试（只有 §5.1 书面授权的两处例外：一条顺序断言重指 + 一个 collaborator stub，断言与期望值未放宽）；
新增依赖；把被拒回执改成"失败卡"（那是另一种口径，要先改本 Spec）。

## 4. 未做 / 边界（如实记）

- 本批**不**给被拒回执补 Agent 说明行：按用户原话「要没有就都没有」，被拒 → 会话里两边都不出现。
  看板按钮自己的灰态说明（`#btnParse.title`）与 `tech-load-states-and-progressive-load.md`
  的回包文案仍然照旧。
- 本批**不**改真人输入的成对性（那本来就成对：用户气泡 + Agent 回复）。
- 未起服务、未连 PG / 34、未写业务数据、未读真样本。

## 5. 红测与反向对照

红测：`tests/test_chat_echo_must_pair_with_agent_output_red.py`
（E 组纯函数 / R 组接线与成对性 / G 组护栏）。

- 红基见 §6。
- 反向对照（2026-09-23 本机实测；每条都是"把这一支改回去"，跑完立即还原，`md5` 复原核对）：
  ① 把 `taskEchoAllowed()` 里被拒闭集那一支删掉（改成 `return true;`）⇒ **E2 单条红**
     （`test_e2_rejected_callbacks_do_not_echo`）；
  ② 把 `echoTaskPrompt(detail, card)` 改回 `echoTaskPrompt(detail)` ⇒ **R3 单条红**
     （`test_r3_echo_is_placed_above_this_card`）；
  ③ 把 `hasContent` 表达式里的 `echoAllowed` 去掉 ⇒ **R2 单条红**
     （`test_r2_echo_counts_as_content`）。
- **不符合预期的一条如实记**：① 本 Spec 原写"E2/E3 单条红"，实测**只有 E2 红** —— E3 的 6 个入参
  （`{}` / `{"prompt": ""}` / `{"prompt": "   "}` / `{"prompt": None}` / `{"prompt": "\t\n"}` /
  `{"code": "not-ready"}`）prompt 全空，没有"被拒闭集"那一支也照样回 `false`，判不出来。
- 三次反向对照逐次复跑 `tests/test_tech_agent_echo_bubble_and_single_exec_card_red.py`（39 条）均
  `OK` ⇒ §5.1 重指后的那条守卫与本节三条各自独立，不会互相顶替。

### 5.1 测试侧的两处书面授权（2026-09-23，Codex；断言与期望值未放宽）

**第一处：`tests/test_tech_agent_echo_bubble_and_single_exec_card_red.py::LeftEchoBubbleContract::test_bubble_comes_before_the_no_content_early_return` 重指。**

§1 原来那句「本批不动它的任何断言」**不准确**。那个文件里除看板侧载荷契约外，还有一条钉**左侧字面顺序**的
断言：`self.assertLess(body.find("echoTaskPrompt"), body.find("hasContent"))`。那是"回声先落、卡后建"的旧口径；
本批 C3 把落位改成「回声挂在这一张卡上方」（回声必须有 Agent 输出成对），`echoTaskPrompt(` 必然落在
**建卡之后**，两条契约正面对撞（实测：落地后原断言 `2707 not less than 191`）。

授权把这条断言**重指**到本批口径，**意图逐条保留、一处也没有放宽**：

- 原意图「回声不被『没有明细就不建卡』吃掉」⇒ 改钉「回声判定 `taskEchoAllowed(` 排在 `hasContent`
  赋值之前」（判定结果参与建卡判定，见 C3）；
- 补钉本批的成对口径：「`ensureTaskCard(` 排在 `echoTaskPrompt(` 之前」；
- `hasContent` 的位置改用 `re.search(r"hasContent\s*=")` 定位 —— 原写法命中的是注释里的同名词
  （偏移 191），不是判定本身；
- 该文件其余 38 条断言、期望文案、用例数、分组一律未动。
- **这条重指后的断言有牙**：拿 HEAD `44fa9b1` 的 `agent-chat.js` 复跑 ⇒ 该条红
  （「renderTaskProgress 没有回声判定」）；本批落地后绿。

（先例：`## 461` 重指既有守卫、`## 475` 由新 Spec §5 授权改既有期望值 —— 都要求"授权写进 Spec + 意图保留"。）

**第二处：`tests/test_task_process_detail_red.py` 的 driver 补一个 collaborator stub。**

`detail_driver()` 把 `renderTaskProgress()` 单独抽出来真跑，并按它的依赖表逐个打桩
（`clearEmpty` / `scrollDown` / `taskProgressHost` / `echoTaskPrompt` / `refreshResultChips` /
`loadFiles` / `renderTaskRetry` / `boardStage`）。本批新增的被调函数 `taskEchoAllowed()` 没进这张表
⇒ `setUpClass` 直接 `ReferenceError: taskEchoAllowed is not defined`（是 ERROR，不是断言失败；
既不是本批口径有问题，也不是那条用例在判什么）。

授权在该 driver 的桩区**补一行** `function taskEchoAllowed() { return false; }`（与旁边 8 个桩同形，
返回 `false` = 这条走查不关心回声）。**该文件 19 条用例的断言、期望值、用例数一律未动。**
（先例：`## 465` / `## 467` / `## 478` 三批同样只修测试侧 harness。）

## 6. 红基（2026-09-23，Spec + 红测，未实现）

```text
./open-claude/.venv/bin/python -m unittest tests.test_chat_echo_must_pair_with_agent_output_red
Ran 18 tests … FAILED (failures=12)
```

- 红 12 条：E1–E7（没有 `taskEchoAllowed()` / `isRejectedBoardCode()` / `REJECTED_BOARD_CODES`）、
  R1–R5（`renderTaskProgress()` 里没有回声判定、`hasContent` 不含回声、回声不带卡）；
- 绿 6 条是护栏：R6（既有"没有真实执行内容不建卡"与去重逻辑）、G1（看板载荷仍带 `prompt`）、
  G2（`QUIET_BOARD_CODES` / `INTERRUPTED_CODES` 逐字）、G3（回放不补回声）、
  G4（独立页系统行）、G5（`addUser(text, before)` 原语仍在）。

## 7. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 479`）

| 契约 | 落点（`tech_app/frontend/agent-chat.js`） | 复跑结果 |
| --- | --- | --- |
| C1 被拒闭集只有一处 | `function isRejectedBoardCode(code)`，函数体内 `const REJECTED_BOARD_CODES = [...]`（唯一声明，`src.count("REJECTED_BOARD_CODES = [") == 1`）；按 `String(code)` 精确比对，非闭集回 `false` | E5 / E6 绿 |
| C2 回声判定 | 纯函数 `taskEchoAllowed(detail)`：非对象/数组 → `false`；`prompt` 去空白为空 → `false`；`code` 在闭集 → `false`；其余 → `true`；体内无 `document` / `window` / `fetch(` / `localStorage` / `sessionStorage` | E1–E4 / E7 绿；反向对照 ① |
| C3 先判定后回声 | `renderTaskProgress()` 第一句 `const echoAllowed = taskEchoAllowed(detail);`；`hasContent` 表达式 `log.length > 0 \|\| existingCard \|\| Boolean(blockedReason) \|\| echoAllowed`；`const card = ensureTaskCard(taskId, label);` 之后 `if (echoAllowed) echoTaskPrompt(detail, card);` | R1–R6 绿；反向对照 ② ③ |
| C3 回声落位 | `echoTaskPrompt(detail, before)` 第二形参 `before`：卡对象 → `before.wrapper \|\| before.box` 作锚点，未给锚点 → 退回 `activeTurnCtx.wrap`（旧调用点语义不变）；`beginUserTurn(text, before)` / `addUser(text, before)` 落位 | R5 绿；G3 / G5 绿 |
| C4 看板载荷与既有纪律 | `tech-board-runtime.js` 照旧 `prompt: resolveActionPrompt(entry, payload)`（未碰）；`QUIET_BOARD_CODES` / `INTERRUPTED_CODES` 逐字未动；「没有真实执行内容不建卡」纪律保留 | G1 / G2 / G4 绿 |

- 用户口径现场：被拒回执（`not-ready` / `loading` / `busy` / `no-project` / `no-part` /
  `no-navigation` / `not-parsed`）**既不回声也不建卡**（会话里两边都不出现）；真的跑起来的动作
  ⇒ 用户气泡与 Agent 卡成对出现、气泡在卡上方。
- 红基原文保留在 §6（`Ran 18 … FAILED (failures=12)`）；实现后 `Ran 18 … OK`。
- 保护网：含 `agent-chat` 的 67 个模块 `Ran 1027 tests … OK`（`-W ignore`）；
  `tests/test_tech_agent_echo_bubble_and_single_exec_card_red` 39 OK；
  `tests/test_task_process_detail_red` 19 OK；`node --check tech_app/frontend/agent-chat.js` 通过。
- 未起服务、未连 PG / 34、未写业务数据、未部署；本轮只本地改动（未 push / 未 MR / 未 tag）。

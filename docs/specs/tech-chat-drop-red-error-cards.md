# Spec：技术工艺会话去掉红色报错卡片，失败信息按普通输出继续

## 背景（用户反馈）

> ⚠ 回传销售经理继续报价失败，请查看看板提示。 这些报错的红色文字的卡片全都不要了

左侧会话里凡是失败/系统提示，都会以**红字卡片**出现（`.oc-err-line`，`color: #dc2626`，
带一个「!」头像），例如：

- 业务动作失败：`⚠ 回传销售经理继续报价失败，请查看看板提示。`
- 看板未就绪 / 命令失败：`⚠ 「零件清单」执行失败：…`
- 会话层故障：`⚠ 读取历史会话失败：Not Found`
- 流式失败：`⚠ <后端错误>`

用户口径（此前已确认过一次，本次再次强调）：**报错要和普通输出一样继续输出**，
不要单独的红字卡片。以前的问题描述是「现在这个报错持续在最下面一直看到」——
既占着常驻位置、又和正常对话不是一套样式。

## 事实依据（实测）

`tech_app/frontend/agent-chat.js`：

| 位置 | 现状 |
| --- | --- |
| `pushSystem()`（:458） | `el("div", "oc-aav", "!")` + `el("div", "oc-err-line", `⚠ ${text}`)` —— 全仓库唯一的红字卡片出口 |
| `handleEvent()` 的 `event.type === "error"`（:668） | `ctx.body.append(el("div", "oc-err-line", `⚠ ${event.error}`))` |
| SSE 读取失败（:720 catch） | `ctx.body.append(el("div", "oc-err-line", `⚠ ${error.message || "连接错误"}`))` |
| `boardFailureNotice()`（:482） | 预期内失败码提前 return，其余 `pushSystem(prefix + reason)` |

`tech_app/frontend/agent-chat.css:255`：`.oc-err-line { margin-top: 8px; color: #dc2626; font-size: 13px; }`

调用面：`pushSystem` 有约 40 处调用（动作失败、看板未就绪、Agent 不可用、历史读取失败…），
并通过 `window.ocTechAgent.notice` 暴露给父壳（`tech-workbench.js`）——
**这些提示本身要保留**（真实错误不能被吞掉），要改的只是「用什么样式呈现」。

## 目标契约

### 一、失败/系统提示 = 普通输出

- `pushSystem(text)` 必须改用**与普通助手输出同款**的结构渲染：
  `oc-amsg` + `oc-aav`（与助手同样的 `✦` 身份，不再是 `!`）+ `oc-abody` + `oc-atxt`，
  正文排版与普通回复一致（白底气泡、常规字号与颜色）。
- 线程里**不得**再出现 `.oc-err-line` 元素；正文不得是红色（不得用 `#dc2626` 承载错误文字）。
- 文本内容原样保留（含具体原因与建议），只是不再是红字卡片。

### 二、流式失败并入同一条回复

- `handleEvent()` 的 `event.type === "error"`：失败原因写进同一条助手回复的**正文**
  （普通排版），**不再**追加 `.oc-err-line`；状态位仍要置为失败
  （`setAssistantState(ctx, "failed")`，chip 文案仍是 `⚠ 失败`）。
- SSE 读取失败（catch）同上：正文写普通文字 + 状态位置失败。

### 三、样式清理

- `agent-chat.css` 删除 `.oc-err-line` 规则。
- `agent-chat.js` 不再出现 `oc-err-line` 字符串。
- **保留**：助手状态 chip（`.oc-alabel-state.is-failed`，`⚠ 失败`）、工具结果错误边框
  （`.oc-tool-result.err`）、看板侧白色气泡里的 `⚠` 文本（`crSay` / `aiSay`）——
  这些都不是「红字报错卡片」。

### 四、不缩水

- `pushSystem` 仍是唯一系统提示出口，仍是 `window.ocTechAgent.notice` 的实现；
  `boardFailureNotice` 仍走 `pushSystem`，预期内失败码（`detached` / `note-target-missing` /
  `missing-comment` / `no-selection`）仍提前 return、不刷噪音。
- 失败信息必须仍然可见、仍然按发生顺序就地插入线程（与
  `docs/specs/tech-session-timeline-persistence-and-order.md` 的顺序契约一致）。
- 不改动任何业务接口、看板协议、动作名与既有测试锁定的其它结构。

## 被取代的断言

`tests/test_chat_errors_inflow_and_drop_refresh_task_cards_red.py::test_existing_error_channels_are_kept`
原先要求 `oc-err-line` 同时出现在 `agent-chat.js` 与 `agent-chat.css`（「流内错误行样式保留」）。
它的真实意图是「错误仍在会话流内、不被静默吞掉」，与红色样式无关，改为：
`pushSystem` 仍在 + 普通输出结构 + `boardFailureNotice` 仍走它 + 不得再出现 `oc-err-line`。

## 验收要求

- **E1 无红字卡片**：`agent-chat.js` 与 `agent-chat.css` 都不再出现 `oc-err-line`；
  `pushSystem` 正文不含 `⚠ ${` 这种红色前缀拼接。
- **E2 普通输出**：`pushSystem` 用 `oc-amsg` + `oc-abody` + `oc-atxt`，头像与助手一致（`✦`）。
- **E3 失败仍可见**：`pushSystem` 仍把文本写进线程；`handleEvent` 的 error 分支与 SSE catch
  仍把原因写进回复正文，且仍置失败状态位。
- **E4 状态位与其它样式不缩水**：`setAssistantState` 仍产出 `⚠ 失败`；`.oc-tool-result.err`
  与看板侧 `⚠` 文本保留。
- **E5 出口不缩水**：`window.ocTechAgent.notice` 仍等于 `pushSystem`；预期内失败码仍静默。

## 验收命令

- 本批红测：`python3 -m unittest tests.test_tech_chat_drop_red_error_cards_red -v`
- 相关回归：`python3 -m unittest tests.test_chat_errors_inflow_and_drop_refresh_task_cards_red
  tests.test_chat_fused_assistant_card_style_red tests.test_tech_chat_card_noise_and_quiet_board_failures_red
  tests.test_tech_business_actions_clickable_then_error_red -v`
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'`
- 语法：`node --check tech_app/frontend/agent-chat.js`

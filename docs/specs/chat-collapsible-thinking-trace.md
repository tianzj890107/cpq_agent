# 思考过程（思维链）：默认折叠、点击展开（报价 + 技术工艺）

状态：Spec + 红测（已实现）
红测：`tests/test_chat_collapsible_thinking_trace_red.py`

## 1. 背景

1. 用户要求：报价与工艺两个 Agent 运行过程中都要有「默认隐藏、点击展开」的模型思考过程。
2. 供应商流**本来就有**：`open-claude/open_claude/api.pyc` 的 `content_block_delta` 支持
   `text_delta / thinking_delta / input_json_delta`；`thinking` / `thinking_budget` 也已接到两端设置
   （`tech_app/backend/services/claude_client.py:138-155`、`llm_settings.py:258/367`、`cpq_agent_server.py:2229`）。
3. 但两端后端都只转发 `text_delta / tool_use_* / message_end / error`
   （技术 `tech_app/backend/services/oc_agent.py:2994/2997/3011`，报价 `cpq_agent_server.py:2412/2417/2421/2441`）
   → `thinking_delta` 被静默丢弃，前端拿不到。
4. 报价侧还有一层历史包袱：系统提示（`cpq_agent_server.py:1745-1765`）强制模型按
   「🤔 思考 / 📋 规划 / ⚙️ 执行 / ✅ 结果」输出，这几段现在落在正文气泡里，既占版面又与真实思考重复。

## 2. 目标

1. 两端后端把真实思维链增量转发给前端（新事件 `thinking`）。
2. 两端会话各有一个默认折叠、点击展开的「思考过程」块；**没有内容时不出现**。
3. 报价正文里的「🤔 思考 / 📋 规划 / ⚙️ 执行」段收进同一个折叠块，正文只保留 ✅ 结果及后续内容，
   同一件事不显示两遍。
4. 思维链内容不写入会话事件、不落库、不进历史回放。

## 3. 契约 A：后端转发（两端）

技术 `tech_app/backend/services/oc_agent.py::_stream_once()`：

```python
elif kind == "thinking_delta":
    emit({"type": "thinking",
          "text": str(event.get("thinking") or event.get("text") or "")})
```

报价 `cpq_agent_server.py::_stream_once()`（其循环形式是 `for ev in gen: t = ev["type"]`）：

```python
elif t == "thinking_delta":
    emit({"type": "thinking", "text": str(ev.get("thinking") or ev.get("text") or "")})
```

- 只在 `profile.thinking` 为真且供应商真的推了 thinking 时才有事件；**不合成、不猜、不补文案**。
- **不写入**会话事件（技术 `self.events.append(...)`、报价 `self.events.append(...)`），
  保证刷新 / 历史回放不会重复出现。
- 不改既有 SSE 事件语义与 `message_end` 的 usage / 截断处理。

## 4. 契约 B：技术工艺会话折叠块（`agent-chat.js` + `agent-chat.css`）

- `handleEvent()` 新增分支：`if (event.type === "thinking") { appendThinking(ctx, event.text); return; }`。
- `appendThinking(ctx, text)`：
  - 文本为空（`!String(text || '').length`）直接 return，不建空块；
  - 首次调用在该轮 `.oc-abody` 内、文本节点**之前**插入
    `<details class="oc-thinking"><summary>思考过程</summary><div class="oc-thinking-body"></div></details>`（默认关闭）；
  - 后续调用追加到同一个 `.oc-thinking-body`，保留换行。
- 与 `docs/specs/chat-fused-assistant-card-style.md` 的插入顺序约定：
  **身份行 → 思考过程折叠块 → 正文文本**（身份行先由 `addAssistant()` 插入，思考块同样插在文本节点之前，自然落在身份行之后）。
- CSS（白底 + 边框，与气泡同一视觉）：

```css
.oc-thinking { margin-bottom: 8px; padding: 9px 12px; background: #ffffff;
  border: 1px solid var(--oc-border-2); border-radius: 12px; }
.oc-thinking summary { cursor: pointer; color: var(--oc-text-3); font-size: 11px; }
.oc-thinking-body { margin-top: 7px; color: var(--oc-text-2); font-size: 12px;
  line-height: 1.65; white-space: pre-wrap; word-break: break-word; }
```

- 轮末 `done` 时该块保持原样，不并入 markdown 正文；历史回放不渲染（后端不落盘，本来也拿不到）。

## 5. 契约 C：报价会话折叠块（`确认需求解析结果.html`）

- SSE `switch (ev.type)` 新增 `case 'thinking':` → `appendThinkingText(ev.text)`；空文本同样直接 return。
- 折叠块结构与样式：

```html
<details class="thinking-block"><summary>思考过程</summary><div class="thinking-body"></div></details>
```

```css
.thinking-block { margin-bottom: 8px; padding: 9px 12px; background: #ffffff;
  border: 1px solid var(--border-color); border-radius: 12px; }
.thinking-block summary { cursor: pointer; color: var(--text-tertiary); font-size: 11px; }
.thinking-body { margin-top: 7px; color: var(--text-secondary); font-size: 12px;
  line-height: 1.65; white-space: pre-wrap; word-break: break-word; }
```

- 正文收拢：`finishStreamBubble()` 在 `renderMarkdown(raw)` 之前调用新增函数
  `splitReasoningSections(raw)`；**签名固定**：入参原始字符串，
  返回 `{ body: string, thinking: string }`（红测会提取该函数直接执行校验，
  因此它必须自包含：只用 JS 内置方法，不依赖页面里其它辅助函数）：
  - 识别行首标记 `🤔` / `📋` / `⚙️`（`**思考**` / `**规划**` / `**执行**`）所在段落，
    直到下一个标记或 `✅` 为止；
  - 这些段落文本移入该轮折叠块（本轮没有真实 thinking 时也照样收进去）；
  - `✅ **结果**` 及其之后内容留在正文并照常 `renderMarkdown`；
  - 一段都没识别到 → 正文一字不改（增量安全，绝不吞内容）。
- 折叠块插入位置：当前流式气泡 `.message-ai` 内、`.message-text` **之前**；没有气泡时按 `addAiBubble()` 的形态新建。

## 6. 非目标与保护边界

- 不改系统提示词（`cpq_agent_server.py` 的「思考-规划-执行-结果」要求保持原样）。
- 不改 `text` / `stage` / `tool_use` / `tool_result` / `ui` / `error` / `done` / `truncated` 事件语义。
- 思维链不写入会话事件、不落库、不进历史回放、不参与用户可见的 token 统计。
- 不新增第二套 SSE 通道（技术仍是既有 `/send` 流，报价仍是既有 agent SSE）；不新增后端路由。
- 思考为空或设置关闭时不得出现空的折叠块。
- 不执行提交、推送、MR、merge、tag、Release、部署或服务重启。

## 7. 验收

- `python3 -m unittest tests.test_chat_collapsible_thinking_trace_red -v` 全绿。
- 回归：`tests.test_tech_agent_provider_readiness_dynamic`、`tests.test_tech_step_status_in_context_row_red`、
  `tests.test_chat_white_bubble_and_expandable_run_progress_red`。
- `node --check` 覆盖 `agent-chat.js`；`python3 -m py_compile` 覆盖
  `tech_app/backend/services/oc_agent.py` 与 `cpq_agent_server.py`。
- 浏览器：开启思考时两端都出现默认折叠的「思考过程」，点击展开可见增量文本；
  关闭思考或模型未产出思考时完全不出现该块；报价正文不再重复显示思考 / 规划 / 执行段。

## 8. 对应测试

`tests/test_chat_collapsible_thinking_trace_red.py`

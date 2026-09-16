# 模型选择只有一个口径 + 任务过程事件的结构化明细（## 95）

两件事一起做，因为它们是同一类"口径分裂"的两个面：**模型**这边，同一件事有两套判定
（账号感知的 `resolve()` 与只读平台默认的 `selected_model()`）；**过程事件**这边，通道已经
有了，但只带一行文字，真正有价值的载荷（查询条件、命中件、差异）被拼成中文句子后就没法
再展开。

## 1. 背景（已实测，非推断）

### 1.1 图纸解析的能力闸门读的是平台默认，不是你的模型

`## 86` 给模型加了"全局默认 + 每个账号可覆盖"这一层，但覆盖只进了 `resolve()` 一条路：

- 账号感知的一路：`tech_app/backend/services/llm_settings.py:315` 的 `resolve(vision=...)`
  → `_model_and_source(账号)`；`claude_client.py:167`、`llm_client.py:103` 都用它。
- 只读平台默认的一路：`llm_settings.py:286` 的 `selected_model()` → `current_model_id()`
  （全局那一个），**没有账号维度**；`llm_settings.py:293` 的 `ensure_vision_capable()`
  用它做图像能力判定，`qwen_client.py:291` 的 `_model_candidates()` 也用它当候选模型。

于是 OpenAI 兼容这一条路（qwen / deepseek / openai 都走它）出现两个后果：

1. **闸门判的不是实际会用的模型**：`qwen_client.py:770` 拿到的候选来自平台默认，
   而 `get_client()` 建的客户端来自账号模型 —— 检查的和用的不是一个东西；
2. **报错永远写"平台默认"**：`ensure_vision_capable()` 里 `target != personal` 就落
   `global`，文案便是 `当前生效模型 <平台默认>（来源：平台默认）不支持图像解析…`
   （`llm_settings.py:309-311`）—— 看起来像"我的设置没生效"，实际是这一关压根没看设置。

本地实测（子进程，账号 `wugefei` 选 `qwen3.5-plus`，平台默认 `deepseek-v4-flash`，
`acting_user` 已设入）：

```
resolve(vision=True)            -> {"model": "qwen3.5-plus", "provider": "qwen", "api_key": "MY-QWEN-KEY"}
selected_model(vision=True)     -> ValueError: 当前生效模型 deepseek-v4-flash（来源：平台默认）不支持图像解析，请在「模型设置」里改用支持多模态的模型
qwen_client._model_candidates(True) -> 同上（逐字一致）
```

推论（同一条因果链）：平台默认只要是不支持图像的文字模型（`llm_settings.py:118` 的
`VISION_MODELS` 明确不含 deepseek 系），**图纸解析对所有人都跑不起来** —— 除非那个账号
恰好选的是 Anthropic 模型（走 `claude_client` 那一路）。

同源的第二、第三处：`model_lookup.py:55` 用平台默认判 provider 选路（百炼原生搜索 vs
OpenAI hosted web search），`ai_governance.py:51` 用平台默认当留痕的兜底模型名；而
`llm_client.py:155` 的联网能力判定又是账号感知的 —— 同一次调用里两种口径混用。

### 1.2 过程事件通道已经有了，但只带一行文字

`## 94` 已落地（工作区）：`tasks.process_event(phase, text, detail=None)`
（`tech_app/backend/services/tasks.py:227`）把 `model` / `tool` / `progress` 三类事件写进
任务的 `process_log`（`storage/store.py:262`），左侧按 `seq` 渲染成
`.oc-process-step`（`tech_app/frontend/agent-chat.js:1407`），并按 `seq` 取并集落库 / 回放。

缺的是**载荷**：

- 四处工具事件只发文本（`main.py:1345` / `:1473` / `:1492` / `:1692`），`detail` 一个都没用；
- 逐件零部件库检索的四行（`component_match.py:156-169`：检索目标、查询条件、命中/未命中、
  差异）是拼好的中文句子，`query_params`、`component_code`、`score`、`gap_notes` 这些
  结构化事实只存在于落盘的报告里，会话里看不到、也展不开；
- 前端 `pushTaskStep()` 只画 `.oc-process-dot + .oc-process-text`，没有"详情"入口；
- `persistTaskCard()`（`agent-chat.js:1585`）落库时只挑 `seq/phase/text`（`detail` 会被丢掉），
  即使后端带上也活不过一次刷新。

所以今天的过程卡是"看得出发生过什么，看不出拿到了什么"。

## 2. 目标

1. 模型选择**只有一个口径**：闸门判定的模型 = 实际发起调用的模型 = 留痕记的模型
   = 进度文案写的模型；账号覆盖在这四处一致生效；报错点名的是真正在用的那个模型；
2. 过程事件带**结构化明细**：工具/模型的输入与产出以 JSON 载荷随事件下发，左侧可以像
   Agent 的工具卡一样展开「详情」；
3. 明细随落库、合并、回放一起存活，刷新/重进项目后仍可展开；
4. 既有文本口径、状态机、既有测试一律不放松。

## 3. 契约 A：模型选择只有一个口径

### A1 `selected_model()` 不再能返回与 `resolve()` 不同的模型

`llm_settings.selected_model(vision=...)` 必须返回**生效模型**：与同一上下文下
`llm_settings.resolve(vision=...)["model"]` **逐字相同**（账号级覆盖生效；没有账号上下文时
仍回落平台默认，与今天一致）。`ensure_vision_capable()` 的判定对象必须是这个模型，
且 `user` 必须是同一个发起账号（不允许"判平台默认、按账号报错"这种错配）。

### A2 候选模型与实际发出的 `model=` 同源

`qwen_client._model_candidates(vision)[0]` 必须等于 `llm_settings.resolve(vision=vision)["model"]`，
并且**实际发给 API 的 `model=` 参数就是它**（`qwen_client.py:770` 的循环里那一项）。
不允许闸门用 A、调用用 B。

### A3 报错点名"实际会用的模型 + 正确来源"

- 账号模型不支持图像 → 文案里的模型名必须是账号模型，来源写「账号 <user> 的个人设置」；
- 账号没有模型、平台默认不支持图像 → 文案点名平台默认、来源写「平台默认」（既有口径不回归）；
- 仍然**不静默换模型**（不许悄悄回落到支持图像的那个）。

### A4 型号核验的选路按生效模型

`model_lookup._lookup_with_search()` 的 `provider` 判定必须取自生效模型（`resolve` 口径），
不再用 `selected_model()` 的裸全局值。

### A5 留痕按生效模型

`ai_governance._model(kind)` 在 `llm_client.last_used_model()` 为空时的兜底必须取生效模型。

### A6 不放松

- 账号选了没有 Key 的模型仍然明确失败（`llm_settings.py:330-337` 的口径不动）；
- `VISION_MODELS` / `_PREFIX_PROVIDERS` / 白名单语义不动（deepseek 系仍不算支持图像）；
- 没有账号上下文（定时任务、历史恢复、本地直跑）时行为与今天逐字一致；
- 不改 `resolve()` 的返回结构与调用签名。

## 4. 契约 B：过程事件的结构化明细

### B1 `detail` 的形状（tool）

工具事件的 `detail` 必须是 JSON 对象，键固定为五个：

```json
{"tool": "component_match", "title": "零部件库检索",
 "input": {"part_id": "P-001", "index": 1, "total": 4},
 "status": "running", "output": {}}
```

- `tool`：稳定英文名（机器可读，用于分类与去重），白名单
  `component_match` / `process_lookup` / `cost_lookup` / `model_lookup`；
- `title`：中文业务标题（给界面显示，与今天的事件文本同源）；
- `input` / `output`：只放**事实**（零件号、零件名、序号、总数、查询条件数值、材料、
  命中件号/名称、判定、匹配度、差异文本、各类计数）；缺项写 `{}`，不写 `null`；
- `status`：`running` / `ok` / `failed`；`failed` 时 `output` 必须含 `{"reason": "<≤120 字>"}`。

### B2 `detail` 的形状（model）

模型事件必须带 `detail`：`{"model": "<实际模型名>", "provider": "<provider>", "vision": <bool>}`。
模型名取**实际跑过的那个**（`claude_client.last_used_model()` / `route["model"]` /
`qwen_client` 的 `_last_used_model`），不许写死、不许拿配置值顶替。

### B3 `report_progress` 多一个可选 detail，文本口径一字不改

`tasks.report_progress(text, detail=None)`：

- `text` 与写进 `progress_log` / `progress` 的内容**一字不改**（旧客户端与既有轮询不受影响）；
- 有任务上下文时，同一事件写进 `process_log` 的 `phase="progress"` 行**携带 `detail`**；
- 无任务上下文仍静默 no-op（与 `## 94` 同一口径）；
- 服务层的小helper（`component_match._report` / `process_lookup._report` / `cost_lookup._report`）
  在 `detail` 非空时按 `progress(text, detail)` 调用，并在回调只接受一个参数时回落
  `progress(text)`（既有的 `list.append` / 单参 lambda 探针不得因此报错）。

### B4 逐件检索的"查询条件 / 命中 / 差异"必须结构化

零部件库检索的每一行都要带 `detail`（`component_match.py:156-169`）：

| 行 | detail 关键字段 |
| --- | --- |
| `检索零部件库（1/4）：P-001 上壳` | `input.part_id / part_name / index / total` |
| `  · 查询条件：length=108、…，材料 ABS` | `input.params`（数值字典）、`input.material_spec` |
| `  ↳ 命中 CMP-… （可改制，匹配度 65%）` | `output.decision / decision_label / component_code / component_name / score / match_type / candidate_count` |
| `    差异：…` | `output.gap_notes`（全文） |
| `  ↳ 库内无同类件，按新制评估` | `output.decision="new" / match_type="none" / score=0.0 / candidate_count` |

**文本行一条不减、一条不改**：明细是**附着在同一行**上的载荷，不许为了带明细另起一条
工具事件（那会让同一件事在会话里出现两遍）。

### B5 另外三处工具事件带 detail

`_refresh_component_match`（`main.py:1345`）、`_process_lookup_for`（`main.py:1473`）、
`_cost_lookup_for`（`main.py:1492`）、型号联网核验 job（`main.py:1692`）的开始行与返回行
都必须带 `detail`：

- 零部件库：返回行 `output = {"total","reuse","modify","new","library_size"}`；
- 工艺库：`input.part_id`，返回行 `output = {"route_code","route_steps","extra_steps","feature_gaps","library_steps"}`；
- 成本库：`input = {"part_id","quantity"}`，返回行 `output = {"materials","rates","factors"}`；
- 型号核验：返回行 `output = {"matched","ambiguous","not_found","not_a_model"}`；
- 四处失败降级都要 `status="failed"` + `output.reason`，不能静默。

### B6 落库、合并、回放都要保住 detail

- `persistTaskCard()` 必须**原样**带上 `detail`（不许只挑 `seq/phase/text`）；
- `store._merge_task_entry`（`storage/store.py:1265`）与 `tech-session-timeline.js::mergeTask`
  按 `seq` 取并集、保留整行（含 `detail`）；
- `replayTimelineTask()` 透传 `process`，回放顺序与运行时一致。

### B7 左侧渲染：有 detail 才有「详情」

- `.oc-process-step` 内部结构不变（`.oc-process-dot` + `.oc-process-text`）；
- 该行有 `detail` 时，在行内追加 `<details class="oc-process-detail"><summary>详情</summary>
  …工具名…`输入`…`输出`…</details>`（沿用 Agent 工具卡 `oc-art-detail` 的视觉语言：
  顶部细分隔线、小号 summary、`pre` 展示 JSON）；
- 没有 `detail` 的行（含 `phase="progress"` 的旧任务行）DOM 与今天**逐字一致**，不建 `details`；
- 展开态不要求持久化；`detail` 必须随回放回来。

### B8 不泄漏与尺寸上限

- `detail` 里不得出现 prompt 原文、用户输入原文、附件内容、模型响应正文、API Key、
  绝对路径、候选件完整数组；
- 单条 `detail` 序列化后 ≤ 4096 字节；超出时优先保留计数与件号、截断差异文本
  （截断要留明确后缀，不许静默丢字段）。

## 5. 验收

| 编号 | 场景 | 通过标准 |
| --- | --- | --- |
| A1 | 账号模型 ≠ 平台默认 | `selected_model(vision=True)` 与 `resolve(vision=True)["model"]` 逐字相同（= 账号模型），不再抛错 |
| A2 | 候选与实发一致 | `_model_candidates(True)[0]` == 实际 `create(model=…)` 收到的那个值 |
| A3 | 闸门报错 | 账号模型不支持图像 → 文案含账号模型名 + 「账号 <user> 的个人设置」；平台默认不支持 → 含平台默认 + 「平台默认」 |
| A4 | 型号核验选路 | 账号模型为 qwen 而平台默认为 deepseek 时，选路按账号模型（走百炼原生分支） |
| A5 | 留痕 | `last_used_model()` 为空时，`ai_governance` 记的是生效模型 |
| A6 | 无账号上下文 | 结果与今天逐字一致（回落平台默认，报错文案不变） |
| B1 | detail 形状 | 五键齐全、`status` 在白名单、`failed` 带 `output.reason` |
| B2 | 模型事件 | 一次调用的 model 事件都带 `{"model","provider","vision"}`，模型名是实跑的那个 |
| B3 | 进度行 | `progress_log` 文本与今天逐字一致；同一条过程行带 detail |
| B4 | 逐件明细 | 查询条件数值、命中件号、判定、匹配度、差异全文都能在 detail 里读到 |
| B5 | 四处工具 | 开始行与返回行都有 detail；失败降级有 `status="failed"` + reason |
| B6 | 落库/合并/回放 | 刷新与重进后 detail 仍在，顺序与运行时一致 |
| B7 | 渲染 | 有 detail 的行出现「详情」并能展开出输入/输出；无 detail 的行不产生 details 元素 |
| B8 | 不泄漏/上限 | 事件文本与 detail 都不含 prompt / 响应正文 / Key / 绝对路径；单条 ≤ 4096 字节 |

## 6. 不在本批

- 不改 Agent 会话的思考折叠与工具卡（`oc-thinking` / `oc-art-detail` / `oc-tool-result`）；
- 不把候选件完整数组、库内原件详情放进 detail（只放计数与命中件号）；
- 不做"点详情跳到右侧看板对应视图"；
- 不新增 HTTP 路由、不改任务状态机与既有失败静默口径；
- 不改 `## 94` 已定的 `process_event` 文本口径与 `seq` 合并算法。

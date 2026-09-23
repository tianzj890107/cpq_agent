# 模型调用行：一次调用一行（返回补写回原行）+ 明细改「短摘要」 Spec

状态：Spec + 红测（已实现）（「一次调用一行 + 后端短摘要」已落地；折叠「详情」块 `oc-process-detail` 的交互被 `## 133`/`## 226` 两批过程行口径取代、前端已不再渲染，红测按「测试侧待处置」改成锚定它的缺席，见 changelog `## 329`）
红测：`tests/test_tech_model_call_row_merged_and_summary_detail_red.py`

## 用户口径

用户原话：

> 调用模型（qwen3.5-plus）详情 输入{} 输出{} · 模型返回（qwen3.5-plus）详情 输入{} 输出{}
> 这个东西没必要 只需要调用模型（qwen3.5-plus）不需要模型返回（qwen3.5-plus）这个题目，
> 然后输入输出都是空的，只要一个问的是什么 返回的是什么只要有就行了 看一下现在为什么没有

> 输入：这是哪一步的调用 + 实际模型 + 是否带图 + 附件名/数量 + 发给模型的文字提示……
> 这挺好的但是还是你参考一下报价和提问 agent 的时候怎么做的 分析一下 我不想有很长文本
> 截断成只有开头那点，我想总结性的比较简短的就像现在报价和提问 agent 的时候怎么做的
> 然后返回时把结果补写进原来那条

两条要求，本 Spec 一起做：**① 一次调用只占一行，返回时把结果补写进这一行；② 明细改成简短的结构化摘要，不倒长文本。**

## 1. 为什么现在是空的（只读定位，实测）

- 后端在「开始调用」与「拿到结果」各播一条 `model` 事件（`tech_app/backend/services/qwen_client.py:710`
  / `:720`，`tech_app/backend/services/claude_client.py:185` / `:227`），这是 `## 94` 明确写入契约的
  「一次逻辑调用恰好一对（开始 + 成功/失败）」。
- 两条事件的明细都只有三项：`qwen_client.py:725` 的 `_model_detail()` 返回
  `{model, provider, vision}`；`claude_client.py:232` 同款。**没有** `input` / `output`。
- 前端对**任何**带明细的过程行长一个折叠「详情」，里面固定两格：
  `输入` = `JSON.stringify(detail.input || {})`、`输出` = `JSON.stringify(detail.output || {})`
  （`tech_app/frontend/agent-chat.js:1421-1433`）。明细里没有这两项 → 两行都显示 `{}`。
- 对比：工具事件的明细有内容五键形状（`tech_app/backend/main.py:1334` 的
  `_tool_detail(tool, title, status, input, output)`），所以工具行的详情是有东西的。
- 当时刻意不留正文：`docs/specs/effective-model-for-vision-and-task-process-detail.md:189-193`（B8）
  与 `docs/specs/tech-task-card-body-layout-and-process-stream.md:206` 都写着「不得落 prompt 原文、
  附件内容、模型响应正文、API Key」；守卫在 `tests/test_task_process_detail_red.py:523`（test_10）与
  `tests/test_tech_task_process_stream_red.py:516`（test_23）。

## 2. 参照实现（用户点名「像报价和提问 Agent 那样」）

**报价侧（`确认需求解析结果.html`）**：`showStage(text)`（`:1131`）——**只有一条**轨迹行，
随时被后面的 stage 原地覆盖，本轮结束由 `clearStage()` 收掉；`addToolActivity(main, source)`
（`:1119`）用一句业务语言 + 一句来源摘要，**从不贴长原文**。

**提问 Agent 侧（`tech_app/frontend/agent-chat.js`）**：工具卡 `addToolCard()`（`:639`）——

- 主行 = 中文业务文案 `label.title`（`TOOL_TRACE_LABELS`，`:409`）+ **一句话入参摘要** `label.subtitle`
  （`toolSubtitle()`，`:379`，如 `LookupComponentLibrary → "全部零件"`、`GetPartDetail → "P-001"`）；
- 详情默认折叠：原始工具名 + 入参（`slice(0, 300)`）+ 结果（`slice(0, 4000)`）；
- **结果到达时写回同一张卡**：`setToolResult()`（`:668`）按 `tool_use_id` 找到那张卡，
  只更新状态位与结果 `<pre>`，**绝不另建一行**。

结论：参照实现给出的形态就是「**一行 + 一句摘要 + 折叠详情 + 结果写回原行**」，而不是把长正文截断后贴上来。

## 3. 契约 A：后端模型明细改成短摘要，并带同一次调用的配对 id

`qwen_client._model_detail()` 与 `claude_client._model_detail()` 的返回值扩成：

```
{
  "model": "<实跑模型>",          # 既有，保留（test_09 / test_24 依赖这三个键）
  "provider": "<服务商>",         # 既有，保留
  "vision": bool,                 # 既有，保留
  "call": "<同一次逻辑调用的配对 id>",   # 新增
  "status": "running" | "ok" | "failed",  # 新增
  "input": {...},                 # 开始事件带，见 3.1
  "output": {...},                # 成功/失败事件带，见 3.2
}
```

- `call` 在 `run()` 里生成**一次**（如 `uuid.uuid4().hex[:8]`），两条事件共用；`_invoke` 内部的
  候选切换 / schema 修复重试仍算**同一次**调用，`call` 不变。
- `该模型名 + provider + vision + call + status` 五个键在两条事件里都在，值除了 `status` 外相同。

摘要本身必须由**两个具名私有辅助函数**生成（两个 client 同名，便于被红测按名抽取、也便于复用）：

| 函数 | 职责 |
|------|------|
| `_model_input_summary(...)` | 返回 3.1 的 `input` 字典：只算计数、长度与文件名 |
| `_model_output_summary(...)` | 返回 3.2 的 `output` 字典：只算规模与结构 |

- 长度/规模一律用 `len(...)` 现算，**不落任何原文**（不出现 `data:` / `base64`）；
- `_model_detail()` 只负责把这些值装进既有三键的返回里，不额外放宽任何安全口径。

### 3.1 `input`：问的是什么（短摘要，由 `run()` 的入参直接算，不改 25 个调用点）

| 键 | 值 | 说明 |
|----|----|------|
| `任务` | `<当前任务中文名>`，如 `图纸解析 SOP` | 来自新增的 `tasks.current_task_name()`；没有任务上下文时**省略该键** |
| `模型` | `<planned model>` | 这次打算用的模型 |
| `服务商` | `<provider>` | 同 `provider` |
| `带图` | `<图片块数量>`（int） | 0 表示纯文本调用 |
| `文本段` | `<文本块数量>`（int） | |
| `提示字数` | `len(system_prompt)`（int） | system prompt 的字数 |
| `消息字数` | 用户消息里**文本块**字数合计（int） | 不含 base64 |
| `附件` | `["source.png", …]`，最多 5 个、去重、保持出现顺序 | 从文本块里按扩展名抓文件名；一个都没有时**省略该键** |

- **只放计数、长度与文件名**：不出现 prompt 原文、不出现用户消息原文、不出现 `data:` / base64、
  不出现绝对路径。
- 文件名白名单扩展名：`png jpg jpeg gif webp bmp pdf dwg dxf xlsx xls docx doc txt md csv step stp`。

### 3.2 `output`：返回的是什么（短摘要）

成功（`status = "ok"`）：

| 键 | 值 |
|----|----|
| `状态` | `"ok"` |
| `结果` | 返回对象的**顶层字段规模**字典，最多 12 个键，见下表 |
| `规模` | 返回 JSON 的字符数（int） |

失败（`status = "failed"`）：

| 键 | 值 |
|----|----|
| `状态` | `"failed"` |
| `原因` | 失败原因前 120 字（不含密钥；本来就只有一句） |

`结果` 每个键的值按类型给一句规模，**不落任何字符串正文**：

| 值类型 | 摘要写法 |
|--------|----------|
| list | `"N 项"` |
| dict | `"N 键"` |
| str | `"N 字"` |
| bool | `"true"` / `"false"` |
| int / float | 原值（数字） |
| None | `"—"` |
| 其它 | 类型名 |

- 顶层字段超过 12 个时，保留前 12 个并追加 `"…": "另有 N 键"`。

### 3.3 `tasks.current_task_name()`

`tech_app/backend/services/tasks.py` 新增：

```python
def current_task_name() -> str:
    """当前任务的中文名（没有任务上下文时返回空串）。"""
```

- 从 `_CURRENT_TASK` 取 kind，再查 `_SOP_NAMES`（`:43`）的第一项，如 `"parse" → "图纸解析 SOP"`；
- 没有任务上下文（Agent 会话线程、HTTP 线程）返回 `""`，调用方据此省略 `input["任务"]`。

## 4. 契约 B：前端把同一次调用的两条事件渲染成一行（`pushTaskStep`）

改 `tech_app/frontend/agent-chat.js` 的 `pushTaskStep()`（`:1407`）：

- 现有行为全部保留：相位类名、圆点、文字、"有明细才长「详情」"、`.oc-process-detail` 的内部
  结构（`summary` → `.oc-process-detail-tool` → `输入` label + `pre` → `输出` label + `pre`）。
- 新增：`phase === "model"` 且 `detail.call` 非空时

| 情况 | 行为 |
|------|------|
| 本张卡里还没有同 `call` 的行 | 按今天的方式建行并渲染详情；把 `{call, 行节点, 输出 pre}` 记进 `card.modelRows` |
| 已有同 `call` 的行，`detail.status === "ok"` | **不建行**；只把 `detail.output` 写进该行已有的「输出」`pre`；**不重写「输入」** |
| 已有同 `call` 的行，`detail.status === "failed"` | **不建行**；写「输出」（失败摘要），并把行文字改成 `<第一条文字> · <第二条文字>`（即 `调用模型（x） · 模型调用失败（原因）`），失败原因必须**不展开就能看见** |

- `detail.call` 缺失（旧任务回放、旧落库数据）→ **逐字保持今天的行为**：两条各自建行、各自详情。
- 合并只发生在渲染层：`card.processCursor` 的推进与 `persistTaskCard()` 的落库内容**都不变**
  （两条事件照旧落库，回放时按同样的规则再合并一次）。
- 合并逻辑必须写在 `pushTaskStep()` 内（或同文件顶层、且被本 Spec 的红测按名抽取的小函数），
  不得依赖新的顶层状态。

## 5. 契约 C：口径补丁（不放松安全底线）

- B8 的禁止项**全部继续有效**：prompt 原文、用户消息原文、附件内容、模型响应正文、API Key、
  绝对路径、候选件完整数组一律不进明细；
- **新增允许项**：prompt / 响应的**规模与结构摘要**（字数、段落数、图片数、顶层字段规模）、
  以及**文件名**（非路径）；
- 单条明细 ≤ 4096 字节（`tasks.PROCESS_DETAIL_LIMIT`）**不变**——本批的摘要天然远小于它，
  不需要放宽上限；
- `tasks.process_event()` 与 `main._tool_detail()` 的 docstring 那句「不得写入 prompt 原文、
  附件内容、密钥或响应正文」补成「不得写入 prompt 原文与响应正文；**规模与结构摘要**允许」；
- `docs/specs/effective-model-for-vision-and-task-process-detail.md` 的 B8 与
  `docs/specs/tech-task-card-body-layout-and-process-stream.md` 的 B5 各补一句同样的例外。
- **因此本批不反转任何既有守卫测试**（`test_task_process_detail_red.py` 的 test_10 / test_11、
  `test_tech_task_process_stream_red.py` 的 test_23 继续按原样通过），这是本方案相对
  「贴 prompt 与响应正文」的关键优势。

## 6. 明确不做

- 不把 prompt 原文 / 模型响应正文贴进「详情」（无论是否截断）；
- 不提高 `PROCESS_DETAIL_LIMIT`，不为正文另建存储或文件；
- 不改后端两条事件的**文本**（`调用模型（x）` / `模型返回（x）` / `模型调用失败（原因）` 一个字不改），
  合并只发生在渲染层；
- 不改 `## 94` 的「一次逻辑调用恰好一对事件」契约，不动 process_log 的 seq 语义与落库形状；
- 不在 25 个 `run()` 调用点加「用途」参数（任务名由 `tasks.current_task_name()` 自动带出）；
- 不给模型行加新的副标题 / 图标 / 状态胶囊，不新增 CSS 类；
- 不改报价侧 `showStage()` / `addToolActivity()` 与提问 Agent 的工具卡。

## 7. 验收标准

1. `tests/test_tech_model_call_row_merged_and_summary_detail_red.py` **全绿**；
2. 无头 Chrome 实测：触发一次右侧看板任务，会话卡里同一次模型调用**只有一行**
   `调用模型（x）`，其「详情」的 `输入` / `输出` 都是短摘要（不出现 `{}`，也不出现长正文），
   并且**没有**「模型返回（x）」这一行；失败时该行文字带 `· 模型调用失败（原因）`；
3. `tests/test_task_process_detail_red.py`、`tests/test_tech_task_process_stream_red.py`、
   `tests/test_task_process_detail_red.py` 三份既有守卫**一条不改、继续全绿**；
4. `open-claude/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'` 全绿；
5. `node --check tech_app/frontend/agent-chat.js`、`python -m py_compile` 两个 client 通过；
6. `git diff --check` 干净。

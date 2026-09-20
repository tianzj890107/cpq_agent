# Spec: 长结构化输出截断恢复（输出 token 上限中断）

状态：待实现（红测已落地）
适用分支：`20260909`
相关步骤：1.1 创建需求、2.1 图纸解析、2.3 成本测算、3.1 汇总报告，以及技术工艺
Agent 会话与报价/配置/规则三个业务 Agent 会话。

## 1. 问题陈述

上线前 1.1「创建需求」经常出现"输出 token 超过 12000 后中断"：模型已经吐出大半段
JSON，达到输出上限被 provider 截断，调用方拿不到合法 JSON，用户看到解析失败或字段
缺失，且必须整份重来。

### 1.1 现状（本分支代码事实）

| 位置 | 现状 | 后果 |
| --- | --- | --- |
| `tech_app/backend/services/qwen_client.py:669` | 已能识别 `finish_reason in {"length","max_tokens"}` | 检测没问题 |
| `tech_app/backend/services/qwen_client.py:686`、`:710` | 修复分支把预算设为 `max(当前, 24000 if vision else 12000)` | 文本路径初始预算就是 12000，**提升是 no-op** |
| `tech_app/backend/services/qwen_client.py:663` | 修复时**整份重新生成**（只回灌一句"请重新完整输出"） | 同样的输入、同样的预算，必然再次截断 |
| `tech_app/backend/services/openai_client.py:255`、`:388` | `status=="incomplete"` + `max_output_tokens` 直接抛错，文案写死"系统未自动重试" | OpenAI 路径截断即失败，无补救 |
| `tech_app/backend/services/requirement_extract.py:406` | 1.1 需求解析写死 `max_tokens=12000` | 长文档/灵活行业模板必踩上限 |
| `tech_app/backend/config.py:114`、`:50` | 文本上限默认 `QWEN_TEXT_MAX_OUTPUT_TOKENS=12000` / `OPENAI_TEXT_MAX_OUTPUT_TOKENS=12000` | 文本路径的默认天花板就是触发值 |
| `tech_app/backend/services/oc_agent.py:2984` | `stop_reason` 默认 `end_turn`，不区分 `max_tokens` | Agent 会话被截断时静默当正常结束 |
| `cpq_agent_server.py:2442`、`xbom_agent_server.py:1235`、`rule_agent_server.py:928` | 同上，只比较 `tool_use` | 三个业务 Agent 同样静默截断 |

### 1.2 本次修改是否涉及

不涉及。最近提交（`6d9be94` 容器部署持久化、`79bf198` 品牌主色、`615c7ea`/`df8de49`
技术工艺 16–22 步）都未触碰模型输出预算、截断识别或续写逻辑，1.1 中断风险与上线前
一致。

## 2. 目标

把"输出被 provider 上限截断"从**静默失败/整份重来**改成**可续写、可提升预算、失败
可诊断**的统一能力，并且覆盖所有会产生长结构化输出的调用路径。

## 3. 术语

- **截断**：provider 明确告知输出因输出上限结束。
  - Qwen / OpenAI 兼容：`finish_reason in {"length", "max_tokens"}`
  - OpenAI Responses：`status == "incomplete"` 且 `incomplete_details.reason in {"max_output_tokens", "max_tokens"}`
  - open-claude 会话：`stop_reason == "max_tokens"`
- **初始预算**：调用方或默认值给这一次请求的 `max_tokens` / `max_output_tokens`。
- **提升预算**：截断后重试时使用的预算，必须严格大于初始预算（除非已到 provider 上限）。
- **续写（continuation）**：把已产出的片段交回模型，只要求它输出**剩余部分**，再本地拼接，
  而不是整份重新生成。

## 4. 需求

### R1 统一截断语义（新增共享模块）

新增 `tech_app/backend/services/llm_output.py`，至少导出：

- `class OutputTruncated(RuntimeError)`：截断且无法补救时抛出。必须带属性
  `finish_reason: str | None`、`limit: int | None`、`partial: str`。
- `TRUNCATION_REASONS: frozenset[str]`，至少含 `length`、`max_tokens`、`max_output_tokens`。
- `def is_truncated(reason: str | None) -> bool`
- `def raised_budget(current: int, cap: int) -> int | None`：返回**严格大于** `current`
  的值且不超过 `cap`；`current >= cap` 时返回 `None`（表示无法再提升，只能靠续写或报错）。
- `def truncation_note(*, finish_reason, initial_budget, final_budget, attempts, limit) -> str`：
  生成可写入日志/审计的单行诊断，**必须包含** finish_reason、初始预算、最终预算与上限值。

同一模块不得被任何 provider 之外的业务逻辑复制；`qwen_client` / `openai_client` /
`requirement_extract` / `oc_agent` 必须复用这一处定义（测试以是否引用 `llm_output` 作为契约）。

### R2 Qwen 路径

1. 截断后重试必须**真的提升预算**：不得再出现 `24000 if vision else 12000` 这类对文本
   路径等于原值的表达式；提升值必须来自 `llm_output.raised_budget`。
2. 截断后的重试必须是**续写**：请求中必须包含上一轮已产出的片段，并要求模型只补剩余内容；
   不得只发一句"请重新完整输出"。
3. 续写请求**不得重复上传原图/附件**，也不得重复触发联网检索，避免重复计费。
4. 续写片段必须本地拼接后重新做 JSON 解析 + Pydantic 校验，校验通过才算成功。
5. 续写次数有上限（沿用 `QWEN_SCHEMA_REPAIR_RETRIES` 或独立常量），不得无限循环。
6. 到达 `QWEN_MAX_OUTPUT_TOKENS` 仍无法完整输出时，抛 `OutputTruncated`，错误信息必须
   含 `finish_reason`、本次上限值和"输出被截断"的中文说明；**不得**返回半截 JSON。

### R3 OpenAI 路径

1. `status == "incomplete"` 且原因为输出上限时，必须按 R2 的策略重试（提升预算 + 续写），
   不得直接判定"系统未自动重试"。
2. 预算提升同样走 `llm_output.raised_budget`。
3. 重试耗尽或已到上限仍不完整时抛 `OutputTruncated`，并保留 `finish_reason`/上限信息。
4. 续写不得重复上传图纸（沿用现有"修复只传回已产出 JSON"的原则）。

### R4 1.1 需求解析

1. `requirement_extract.extract_requirement_fields` 必须引用 `llm_output` 获取输出预算，
   不得再写死 `max_tokens=12000`。
2. 首轮被截断时，1.1 必须能通过续写拿到完整 `RequirementDocumentExtraction`；不能因为
   截断丢字段、丢 `fields` 条目或让整次解析失败。
3. 若确实无法完整（达到 provider 上限且续写仍失败），错误必须可识别为输出超长，供前端
   提示"减少一次解析的文档量/改用长输出模型后重试"。

### R5 Agent 会话（open-claude `stop_reason`）

1. `tech_app/backend/services/oc_agent.py` 必须区分 `stop_reason == "max_tokens"`：向前端
   发出可识别的截断事件/提示，并保留已产出文本，不得当作正常 `end_turn` 静默收尾。
2. `cpq_agent_server.py`、`xbom_agent_server.py`、`rule_agent_server.py` 三处同样必须显式
   处理 `max_tokens` 停止原因（至少写入会话事件并让前端可提示重试）。
3. 该处理不得改变现有 `tool_use` 循环语义与既有事件协议。

### R6 可观测性

1. 每次补救（提升预算/续写）都要留下可追踪记录，统一用 `llm_output.truncation_note`
   生成，至少包含：调用路径、初始预算、最终预算、续写次数、最终结果（成功/失败）。
2. 记录不得包含图纸二进制、完整提示词或 API Key。

### R7 不得回归

1. 现有"识别截断"能力、`QWEN_MAX_OUTPUT_TOKENS` / `OPENAI_MAX_OUTPUT_TOKENS` 等环境变量
   语义、以及 `llm_settings` 的 `max_tokens` 范围校验 `[256, 64000]` 保持不变。
2. 不新增第二套截断/续写实现，不降低现有结构化校验强度（仍必须 Pydantic 严格校验）。

## 5. 验收标准

- 红测 `tests/test_llm_output_truncation_recovery_red.py` 全绿。
- 端到端：1.1 用长文档解析，首轮被截断时最终仍返回完整需求字段；被截断且无法补救时给出
  明确的"输出超长"错误，而不是半截结果或泛化报错。
- Qwen 文本路径截断重试的 `max_tokens` 严格大于首轮。
- 四个 Agent 会话在 `stop_reason == "max_tokens"` 时都有显式处理。
- 全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 无新增失败。

## 6. 不在本次范围

- 修改 `open-claude` 包字节码（`.pyc`）本身；只允许在调用方（`oc_agent.py` 与三个
  agent server）处理 `stop_reason`。
- 调整各 provider 的定价、超时、代理或模型池策略。
- 前端视觉/交互改版（前端只需能展示新的截断错误与重试入口）。

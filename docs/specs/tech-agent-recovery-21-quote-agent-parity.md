# 技术工艺 Agent 能力恢复第 21 步：与报价 Agent 对照验收 Spec

## 范围

逐项比较技术工艺左侧会话栏与报价 Agent 左侧会话（`确认需求解析结果.html`）的**交互能力**，
不是要求视觉完全复制，而是技术工艺的交互能力不能比报价 Agent 少。每一条必须落到一个
**技术工艺侧的真实入口**（DOM id / JS 标识符 / 动作名）和一个**报价侧参照**，
并由一份可机读对照表 + 自动测试守住。

十四项对照（顺序固定）：

| id | 能力 | 报价侧参照（示例） |
| --- | --- | --- |
| `quick_buttons` | 快捷按钮 | `确认需求解析结果.html#quickActions` |
| `attachments` | 附件 | `确认需求解析结果.html#chatAttachBtn` |
| `tool_trace` | 工具轨迹 | 会话内工具调用卡片 |
| `structured_fill` | 结构化回填 | `cpq_ui` 固定表单 / 表格 |
| `current_step` | 当前步骤 | 步骤条 / 当前分区 |
| `prev_next` | 上下步 | 步骤前进 / 后退 |
| `transfer` | 转交 | 转交任务入口 |
| `history` | 历史 | `确认需求解析结果.html#historyList` |
| `settings` | 设置 | `确认需求解析结果.html#settingsModal` |
| `error_prompt` | 错误提示 | 失败时明确提示、不静默 |
| `task_progress` | 任务进度 | 长任务进度卡 |
| `result_entry` | 结果入口 | 结果快捷按钮 |
| `session_restore` | 会话恢复 | 历史项目 / 会话恢复 |
| `model_display` | 模型显示 | `确认需求解析结果.html#modelInfo` |

## 1. 交付物：可机读对照表

新增 `docs/specs/tech-agent-recovery-21-quote-parity.json`，结构固定：

```json
{
  "step": 21,
  "quote_reference": "确认需求解析结果.html",
  "rows": [
    {
      "id": "quick_buttons",
      "label": "快捷按钮",
      "quote": "确认需求解析结果.html#quickActions",
      "tech": "tech_app/frontend/tech-workbench.html#techChatActions",
      "status": "gated",
      "gated_by": 16
    }
  ]
}
```

- `rows` 必须**正好覆盖上表十四项**，不重复、不缺失。
- 每行 `quote` / `tech` 都用 `"路径#token"` 形式；文件必须存在，`token` 必须在该文件里真实出现。
- `status` 只能是：
  - `live`：该能力当前已实现，`tech` 锚点必须能在代码里解析到；
  - `gated`：依赖尚未实现的批次，必须写 `gated_by`（16 / 17 / 18 / 19 之一），且该批次的
    Spec 与 Red 测试文件必须存在（证明不是随口挂账）。
- 不允许用 `status: gated` 逃避已实现能力；`live` 行必须真实可解析。

## 2. 边界

- 只新增对照表与测试，不改业务实现；不新增 / 删除 `@app.` 路由。
- 报价侧是参照物，不改报价页面与报价逻辑。
- 对照的是"能力有没有"，不是像素 / 布局是否一致。

## 3. 验收标准

1. 对照表存在且十四项齐全、无重复；
2. 每行 `quote` / `tech` 锚点都能解析到真实代码；
3. `live` 行的 `tech` 锚点存在；`gated` 行的 `gated_by` 批次 Spec + Red 都在仓库里；
4. 报价参照文件存在，且技术工艺侧十四项**没有一项缺失**（缺失必须显式 `gated` 并标批次）。

## 4. 对应测试

`tests/test_tech_quote_agent_parity_matrix_red.py`

# 快速报价入口路由与包装行业隔离 Spec

状态：Spec + 红测（已实现）（`activeQuoteMode` 与 `quote_mode` 落实例、提交单一路由、结构化抽取、`_handle_step1_match` 在查电池表之前分流、工作台跨行业候选拦截）
红测：`tests/test_quick_quote_entry_routing_packaging_isolation_red.py`

## 1. 缺陷复现与根因

在报价首页选择“包装”，点击“快速报价”，输入 `700ML 双开门酒盒`及完整盒型参数后发送，页面却跳到
`确认需求解析结果.html` 的六步精准报价工作台。随后第 1 步请求 `/api/step1/match`，查询
`master_data.product_para_value` 的 19 条电池产品，最终把 3 条锂亚电池打成 100 分。

这不是快速案例库匹配错误。两条包装案例根本没有参与本次请求：

1. 首页“快速报价”按钮只调用 `openQuickQuotePanel()`，没有保存当前 `quote_mode`；
2. 输入框发送和 Enter 都走 `submitRequirement() → requestNavigate(currentMode, text)`，无条件进入精准报价；
3. 精准工作台两段式匹配的 `phase=match` 无条件执行 `SELECT * FROM product_para_value`；
4. 已有的包装分流只存在于 `_handle_match_products()`，当前页面调用的是另一条
   `_handle_step1_match()`，两套实现口径漂移；
5. 首页快速匹配只传 `{industry, requirement_text}`，而快速案例匹配器要求结构化盒型、尺寸、材料、
   工艺和数量字段，原始文本没有经过统一抽取便直接送匹配。

## 2. 报价模式是业务状态

首页必须维护显式 `activeQuoteMode`，取值闭集 `precise|quick`：

- 点击“精准报价”设为 `precise`；
- 点击“快速报价”设为 `quick`，并在按钮、输入区和工作区显示当前模式；
- 行业离开 `packaging` 时自动退回 `precise`，同时清理未确认的快速工作区；
- `activeQuoteMode` 必须随业务实例保存为 `quote_mode`，不得只存在 DOM class 或临时闭包；
- 刷新、首页卡片、历史卡片再次进入时，按 `quote_mode` 恢复对应工作区。

同一张卡片一旦创建，行业和模式以服务端卡片为准；页面不得因刷新回退到默认行业或精准模式。

## 3. 首页提交的唯一分流

`submitRequirement()` 只能委托一个模式路由函数：

```text
quote + precise → 既有 requestNavigate('quote', ...)
quote + quick   → submitQuickQuoteRequirement(...)
其它助手        → 既有路径
```

快速路径不得导航到 `确认需求解析结果.html`，也不得启动六步精准报价 Agent。它必须留在首页快速工作区，
顺序完成：

1. 创建/复用 quick session；
2. 把输入文本和附件送统一需求抽取；
3. 将结构化字段送 `/quick-quote/sessions/{id}/match`；
4. 展示候选及逐字段证据；
5. 用户点候选行选择基准；
6. 修改少量字段、重算、确认保存。

一次按钮触发只产生一条用户消息；不得像本次复现一样把同一需求气泡输出两遍。

## 4. 快速需求抽取契约

快速报价抽取结果必须使用 `QUICK_MATCH_INPUT_KEYS` 同一键集，至少支持：

`box_type`、`box_family`、`closure_type`、`inner_length/width/height`、`grey_board_gsm`、
`face_paper_gsm`、`insert_type`、`print_colors`、`lamination`、`hot_stamping`、`v_groove`、
`magnet`、`quantity`。

示例文本必须确定性得到：

```json
{
  "box_type": "YT-DWG-WINE-700ML",
  "box_family": "书型盒/双开门礼盒",
  "closure_type": "双开门/对开",
  "inner_length": 220.5,
  "inner_width": 90,
  "inner_height": 90,
  "face_paper_gsm": 225,
  "insert_type": "EVA内托",
  "v_groove": true,
  "quantity": 1000
}
```

抽取结果必须展示给用户核对；缺字段返回 `missing_inputs`，不得补造。用户原文只作为证据保留，不能把
`requirement_text` 当成匹配字段直接传给 `match_cases()`。

## 5. 包装行业硬隔离

无论入口是快速还是精准，只要 `industry=packaging`：

- 禁止查询 `product_para_value`；
- 禁止调用电池六维评分（尺寸、用途、温度、寿命、密封性）；
- 禁止展示电池成品编码、机械号、电芯、电压、容量、电流、工作温度等字段；
- 快速报价使用 `cpq_quick_quote_match` 的标准案例库；
- 精准报价第 1 步如需推荐，使用包装盒型/结构知识库 `cpq_packaging_match`，或者明确转技术工艺；
- 包装候选必须满足 `industry=packaging`，返回跨行业候选视为服务端错误，不由前端过滤掩盖。

`_handle_step1_match()` 与 Agent 工具不得各维护一份行业分流。应抽成同一确定性服务，两个入口复用。

## 6. 页面与卡片

快速报价页面只显示：需求证据、候选案例、基准案例、参数差异、价格与版本；不出现精准报价的六步导航、
商务保函大表、电池技术参数表或“转技术工艺”主操作。需要升级时只有显式“转精准报价”出口。

首页快速报价卡片至少展示 `快速报价` 标识、案例编码、试算/正式状态、单价和版本。重新打开应进入快速工作区，
不能重新播放精准 Agent 历史。

## 7. 服务端防误用

即使前端路由再次出错，`/api/step1/match` 收到 `industry=packaging` 也必须走包装匹配；不得以客户端调用了
旧端点为理由返回电池。快速 session 的命令还必须校验 `industry=packaging` 和 `quote_mode=quick`。

## 8. 验收场景

### 8.1 酒盒快速报价

首页选择包装与快速报价，粘贴 §4 示例并发送：不发生页面跳转；推荐第一名必须是
`QQ-YT-DWG-WINE-700ML`，标准价 `15.18`，不得出现 `9100...` 电池编码。

### 8.2 圆盘盒快速报价

输入 `YT-DWG-ROUND-10PC / 408×408×50.5 / 圆盒/天地盖 / 灰板内托 / 1000只`，第一名必须是
`QQ-YT-DWG-ROUND-10PC`，标准价 `39.80`。

### 8.3 精准包装防串库

明确选择精准报价后输入相同酒盒需求，允许进入六步工作台，但第 1 步只能出现包装盒型候选或“需新增盒型”，
绝不能查询、渲染或选用电池产品。

### 8.4 恢复

保存快速报价后刷新、返回首页、从历史卡片打开三次，均恢复快速工作区、相同案例、单价和版本；用户需求消息
只出现一次。

## 9. 实现记录（2026-09-22 落地）

| 位置 | 改了什么 |
| --- | --- |
| `报价首页.html` | `activeQuoteMode`（precise\|quick）+ `setActiveQuoteMode/restoreActiveQuoteMode`；按钮先存模式再开面板；`routeQuoteSubmission()` 成为提交的唯一分流；新增 `submitQuickQuoteRequirement()`（不跳页、建/复用实例 → 结构化抽取 → 匹配 → 候选上屏）；`quickQuoteInputs()` 改走 `structuredQuickQuoteInputs()`；卡片点击先问服务端 `quote_mode`（`openQuickQuoteCard`），快速卡片就地开工作区、不重放精准 Agent；`#qqModeBadge` 显示当前模式；行业离开 packaging 自动退回 precise 并收起未确认工作区 |
| `tech_app/frontend/quick-quote-panel.js` | `QUICK_MATCH_INPUT_KEYS`（与后端同值）+ `extractQuickQuoteInputs()` 确定性抽取 + `renderQuickInputs()` 证据上屏 + `renderQuickCandidates()` 候选上屏；建实例体带上 `quote_mode` |
| `cpq_agent_server.py` | `match_step1_by_industry()` 成为第 1 步匹配段唯一分流（页面与 Agent 工具共用）；`_handle_step1_match` 的 phase=match 在**构造 `product_para_value` 的 SQL 之前**按行业分流，包装走 `cpq_packaging_match`；`quick_quote_industry_guard()` 把住 session 的行业（包装）与模式（precise/quick）；`quote_mode`/`industry` 落进实例状态并由读接口返回 |
| `确认需求解析结果.html` | `refreshFixedFormsForIndustry()`（固定表单目录按行业重取，boot 也按当前行业重取一次，包装不再停在电池列）；`rejectCrossIndustryCandidates()`（载荷行业 / 取数表 / 行本身三处任一露馅即全部拦截并显式报错，不静默过滤）；`dedupeInitialRequirementEcho()`（首页那条需求回显只出现一次） |

### 实现口径说明

- **`quote_mode` 落点**：卡片表 `cpq_wf_card` 没有 `quote_mode` 列（加列要动生产 DDL，本批不做），
  因此模式落在快速报价**实例状态**里，由 `POST /api/quick-quote/sessions` 写入、`GET /api/quick-quote/sessions/{id}`
  读回。首页卡片点击时先读一次该实例：`quote_mode=quick` 才就地开快速工作区，否则走既有精准入口 ——
  模式仍以服务端为准，前端只做一次询问，不用本地猜测。
- **跨行业候选的处理**：Spec §5 说"返回跨行业候选视为服务端错误，不由前端过滤掩盖"。实现按字面执行：
  拦截后**显式报错**（点明载荷行业 / 取数表），既不渲染成可选产品，也不假装无事发生。

### 复跑（`./open-claude/.venv/bin/python -m unittest`）

- 本批红测：`Ran 15 OK`（落地前 14 红）
- 不回归：quick-quote 系列 16 份 `Ran 466`（1 条既有红 `test_quick_quote_case_maintenance_red::TestFPanelWiring::test_f1`，
  `## 256` 已记录）；quote/industry 系列 11 份 `Ran 290`（10 条红全属另一份未实现 Spec
  `packaging-parts-in-card-and-material-fill`，与本批无关）；`test_quote_tech_unified_tool_list_conversation_red`
  `Ran 35 OK`；首页 / 工作台相关 30+ 份共 `Ran 438 OK`（2 条既有红 `## 133` 已记录）。
- 三个前端文件 `node --check` 通过（含两个页面的内联脚本）。

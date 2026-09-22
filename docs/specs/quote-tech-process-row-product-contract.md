# 规格：Agent 过程行（Tool List）产品侧收口 —— 报价与技术工艺统一

> 本批口径来自用户对现有渲染结果的直接反馈（2026-09-18），覆盖并**取代**
> `quote-tech-unified-tool-list-and-conversation.md` §11.2 里「标题行即折叠开关」的交互合同。
> 红测：`tests/test_quote_tech_process_row_product_contract_red.py`。

状态：Spec + 红测（已实现）
红测：`tests/test_quote_tech_process_row_product_contract_red.py`

## 1. 背景与真实问题

用户看到的执行卡（以技术工艺「解析」为例）当前长这样：

```
• 读取输入：source.png、补充说明 48 字
◌ 调用多模态模型解析图纸（qwen3.5-plus）   详情
◌ 检索零部件库（1/4）：P-001 上壳          详情
• 零部件库检索完成：可复用 0、可改制 2、未匹配 2component_match · 零部件库检索  详情
```

四个问题：

1. **图标不统一**：有的行是「点」（`•`），有的行是圆圈（`◌`），命中 / 未命中又是
   `●` / `○`；做完的步骤也看不出来做完了。
2. **颜色丢了**：命中（绿）、未命中 / 按新制（橙）、模型调用（蓝）、工具执行（绿）
   这些色调在本次修改后看不到了 —— 缩进行渲染成 `.oc-process-sub hit/miss`，
   而 CSS 只给 `.oc-process-step.sub.hit/miss` 上色，类名对不上，规则写了不生效。
3. **「详情」这个东西和按钮完全不要**：每行挂一个「详情」，用户还要点开才知道发生了什么。
4. **技术实现外露**：展开后是 `输入 { "part_id": "P-001" }` / `输出 {}`，
   以及 `component_match · 零部件库检索` 这种原始工具名；
   用户不需要感知技术实现。

同时：**不只是图纸解析这一张卡** —— 报价侧、技术工艺左栏、3/4 阶段页所有 Agent
输出气泡都必须是同一套过程行合同。

## 2. 用户角色与用户故事

- 工艺经理 / 报价员：扫一眼就知道「这步做完了没有、结论是什么」，不需要点开任何东西。
- 售前 / 销售经理：看到的是产品语言（可复用 / 可改制 / 按新制评估、匹配度、差异），
  不是 JSON 和工具名。
- 审核岗（校核 / 总监）：例外项（未命中、失败）靠颜色与图标一眼可辨。

## 3. 目标流程（过程行合同）

一条业务过程 = 一个过程行；该过程的**产品侧结果行**紧跟其后、缩进归属它，**直接可见**：

```
✓ 读取输入：source.png、补充说明 48 字
✓ 调用多模态模型解析图纸（qwen3.5-plus）
✓ 检索零部件库（1/4）：P-001 上壳
   ✓ 查询条件：length=108、width=56、height=13.25、hole_diameter=4
   ✓ 命中 CMP-SEMI-EE-BLOCK-0001 搬运吸嘴主体安装块（可改制，匹配度 65%）
   ✓ 差异：length: 库内 120.0mm / 图纸 108.0
✓ 检索零部件库（2/4）：P-002 下壳
   ✓ 库内无同类件，按新制评估
```

### 3.1 硬性规则

1. **没有「详情」、没有折叠、没有按钮**
   - 不得出现 `data-agent-role="tool-detail"`、`<details>/<summary>`、文本「详情」；
   - 不得出现 `role="button"` / `tabindex` / `aria-expanded`（过程行不再是可交互控件）；
   - 产品侧结果行不得靠 `hidden` / `aria-hidden` / 收起的 `<details>` 藏起来。
2. **图标统一**
   - 已完成：`✓`；进行中 / 待处理：圆圈（`○` 或 `◌`）；失败：`⚠`（保留错误色）；
   - 不得再用「点」（`•` / `●` / `⏺`）当状态图标；
   - 每个过程行都必须带自己的状态图标（不允许行内再嵌一个没有图标的重复标记）；
   - 卡片走到成功终态后，**不得再有 `data-state="running"` 的行**，全部收成 `✓`。
3. **颜色保留且必须落在文字上**
   - 命中 / 可改制 → 绿；未命中 / 按新制 → 橙；模型调用 → 蓝；工具执行 → 绿；失败 → 红；
   - 色调类必须与实际渲染出来的类名对得上（规则写了不生效视为不达标）；
   - 色调只上给图标不算达标，必须作用在文字上。
4. **技术实现不外露**
   - 不显示输入 / 输出 JSON（空对象时的 `{} {}` 更不行）、不显示「输入」「输出」标签、
     不显示原始工具名（`component_match`、`vision_parse`、`LookupComponentLibrary`…）。
5. **产品侧结果一条都不能少**
   - 查询条件、命中件与匹配度、差异、库内条数、可复用 / 可改制 / 未匹配统计、
     `费率 0 条 / 回退 global 0 条 / 系数 0 条 / 待补 10 项`、读取输入与模型名等。

## 4. 结构契约（两侧一致）

```html
<div class="oc-process-step" data-agent-role="tool-item" data-state="completed">
  <span class="oc-process-dot" data-agent-role="tool-state-icon">✓</span>
  <span class="oc-process-text" data-agent-role="tool-title">检索零部件库（1/4）：P-001 上壳</span>
  <div class="oc-process-subs">
    <div class="oc-process-sub hit" data-agent-role="tool-item" data-state="completed">
      <span class="oc-process-dot" data-agent-role="tool-state-icon">✓</span>
      <span class="oc-process-text" data-agent-role="tool-title">命中 …（可改制，匹配度 65%）</span>
    </div>
  </div>
</div>
```

- 类名可以按现有实现调整，但**角色标记**（`tool-item` / `tool-state-icon` / `tool-title`）
  与「子行归属父行」的父子关系必须保持。
- 覆盖范围：`agent-chat.js::pushTaskStep`（技术左栏）、
  `assembly-integration.js::aiProcessCard`（3 阶段页）、
  `cost-review.js::crCard`（4 阶段页）、
  `确认需求解析结果.html::addToolActivity / showStage`（报价侧）。

## 5. 数据与历史兼容

- 后端 `process[].detail` 载荷**保留**（审计与排障仍需要），只是**不再渲染**到会话里。
- 历史会话回放使用同一套渲染函数，因此历史消息也自动符合新合同；
  **不得为历史消息补造内容，也不得删除历史里已有的产品侧文本行**。
- 中文缩进判定（前导 2+ 空格 / `↳` / `·`）继续沿用，缩进行归父行。

## 6. 非目标

- 不改后端协议、SSE 事件名、数据库结构、Prompt、工具协议。
- 不新增 / 不修改 font-family。
- 不改变卡片外层（白底、浅灰边框、圆角、无强阴影）。
- 不处理真实流式追加（另批）。

## 7. 自动化验收

```
./open-claude/.venv/bin/python -m unittest tests.test_quote_tech_process_row_product_contract_red
./open-claude/.venv/bin/python -m unittest tests.test_quote_tech_unified_tool_list_conversation_red
./open-claude/.venv/bin/python -m unittest tests.test_task_process_detail_red tests.test_quote_btn_radius_and_tech_board_render_red
```

红测对照：A1–A5（无折叠 / 无按钮 / 结果直接可见 / 子行归父 / 报价页同合同）、
B1–B5（✓、圆圈、无「点」、⚠、完成后无 running）、
C1–C4（色调类存在、规则匹配真实类名、颜色互不相同、阶段页同合同）、
D1–D3（无 JSON、无工具名、产品侧结果保留）。

## 8. 人工验收

技术工艺左栏跑一次「解析」，以及 3/4 阶段页与报价侧各跑一次带工具的过程：

- 全卡没有「详情」二字，点哪都不会展开出 JSON；
- 完成的步骤都是 ✓，进行中是圆圈，失败是 ⚠；
- 命中是绿的、未命中 / 按新制是橙的、模型调用是蓝的；
- 产品侧结论（查询条件 / 命中件 / 差异 / 统计）直接看得到。

## 9. 已退役的既有断言（合同更替，非回归掩盖）

| 文件 | 退役内容 | 原因 |
|---|---|---|
| `tests/test_quote_tech_unified_tool_list_conversation_red.py` | D 组 D22–D31（折叠 / 开关 / hover / aria） | 用户要求取消折叠交互 |
| `tests/test_task_process_detail_red.py` | test_31 / 32（详情块 + 输入输出 JSON）、test_35 / 36（看板渲染明细 / CSS） | 界面不再展示技术明细 |
| `tests/test_quote_btn_radius_and_tech_board_render_red.py` | test_41–45（详情块版式 / summary 文案 / 明细体结构） | 同上 |

保留：过程行既有结构类名、flex-wrap 换行、工具轨迹卡（`.oc-art-detail`）与思考折叠（与过程行无关）。

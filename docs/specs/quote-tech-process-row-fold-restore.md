# 规格：过程行「点标题行展开」恢复 + 图标同行 + 完成后全部 ✓

> 本批（## 136）是对 `quote-tech-process-row-product-contract.md`（## 133）的**增补**：
> ## 133 的「没有『详情』、没有输入输出 JSON、图标 ✓/○/⚠、颜色落在文字上、
> 报价与技术工艺统一」全部保留；本批把**折叠交互恢复回来**，并修掉两处遗留。
> 红测：`tests/test_quote_tech_process_row_fold_and_done_red.py`。

## 1. 用户反馈（原话要点）

1. 「之前那个每行标题可以点击展开没有了，我需要那个展开功能恢复」；
2. 「查询条件 / 命中 / 差异的圆圈后面换行才是内容，是不是多了一个换行」；
3. 「这些现在没有被折叠进去标题行」；
4. 「这些的标题行在最后做完之后没有变成 ✓」；
5. 「别的改的很好」。

## 2. 现状与根因

- **折叠被删**：`## 133` 把过程行的折叠区整体拿掉，缩进行改成 `.oc-process-subs`
  直接可见，标题行也不再挂 `tool-toggle` / `aria-expanded` / `tabindex`。
- **结果行没有归父行**：缩进行虽然进了父项的 `.oc-process-subs`，但没有「收起 / 展开」，
  用户看到的是与父行同时平铺的一串行。
- **完成后不收口**：`setAssistantState()` 里的收尾翻转只查 `ctx.steps || ctx.tools`；
  而任务卡是把 `oc-task-steps` 挂到 `turn.body`（`ensureTaskCard`），
  于是「解析」这类卡片里仍是 `data-state="running"` 的行不会被翻成 `✓`，
  卡片明明显示「已完成」，行里还是 ○。
- **图标与标题本身在同一行**（`.oc-process-step` 是横向 flex，图标与
  `tool-title` 是兄弟节点），红测 `B1/B2` 现在是守卫项；
  用户看到的「圆圈后面换行」来自结果行当时是**独立的一行**（复制文本时更明显）。
  折叠恢复后，结果行缩进在标题行下方，观感问题一并消失。

## 3. 目标合同（在 ## 133 基础上增补）

1. **标题行可点展开**：含产品侧结果行的过程行，其标题行挂
   `data-agent-role="tool-toggle"` + `role="button"` + `tabindex="0"` + `aria-expanded="false"`；
   点击 / Enter / Space 切换，`aria-expanded` 同步。默认收起。
2. **结果行折进标题行**：缩进行（前导 2+ 空格 / `↳` / `·`）放进该父行的折叠区
   （`data-agent-role="tool-detail"`），**不是**与父行平级的顶层行；顶层行数 == 父项个数。
3. **仍然没有「详情」**：不得出现文本为「详情」的节点，也不得出现第二个开关
   （原生 `<summary>` / `<button>` 一律不要）。语义只挂在标题行上：
   `aria-expanded` / `tabindex` 的数量必须与 `tool-toggle` 数量一致。
4. **没有内容可展开的行**：不建折叠区、也不挂开关（折叠区与开关必须成对出现）。
5. **默认收起，展开后内容完整**：查询条件、命中件与匹配度、差异、库内条数、
   `费率 / 回退 / 系数 / 待补` 一条不少；仍然不显示输入输出 JSON 与原始工具名。
6. **图标与标题同行**：行内横向排列，图标不得占满整行（禁止 `flex-basis:100%` /
   `width:100%` / `display:block`）；标题文本不得带前导空白或换行。
7. **完成后全部 ✓**：卡片进入成功终态时，**该卡自己的**过程行（含折叠区里的结果行）
   一律收成 `data-state="completed"` + `✓`；失败行保留 `⚠`；不得再留任何圆圈。
   收尾翻转必须覆盖任务卡（`oc-task-steps` 挂在 `turn.body` 的形态），不能只认
   `ctx.steps / ctx.tools`。
8. **覆盖两侧**：技术左栏、3 阶段页（`aiProcessCard`）、4 阶段页（`crCard`）、
   报价页（`addToolActivity` / `showStage`）同一套；报价页也要有标题行开关与 `aria-expanded`。

## 4. 数据与兼容

- 后端 `process[].detail` 载荷保留，仍不在界面渲染。
- 历史回放走同一渲染入口，历史消息同样恢复折叠；不补造、不删除历史文本。
- 图标字符沿用 `✓` / `○` / `⚠`；色调沿用命中绿、未命中橙、模型蓝、工具绿、失败红。

## 5. 非目标

不改后端 / SSE / 数据库 / Prompt / 工具协议；不新增字体；不改卡片外层视觉；
不做流式追加。

## 6. 自动化验收

```
./open-claude/.venv/bin/python -m unittest tests.test_quote_tech_process_row_fold_and_done_red
./open-claude/.venv/bin/python -m unittest tests.test_quote_tech_process_row_product_contract_red
./open-claude/.venv/bin/python -m unittest tests.test_quote_tech_unified_tool_list_conversation_red tests.test_task_process_detail_red tests.test_quote_btn_radius_and_tech_board_render_red
```

红测对照：A1 折进标题行、A2 默认收起 + 标题行可点 + aria 同步、A3 无「详情」无 JSON、
A4 阶段页同折叠、A5 报价页标题行即开关；B1 图标与标题同行、B2 CSS 不把图标挤成整行；
C1 完成后每行都是 ✓、C2 结果行随父行一起翻、C3 失败保留 ⚠、C4 不得回退成「点」；
D1 命中 / 未命中色调仍在。

## 7. 人工验收

技术工艺左栏跑一次「解析」：每行左边是 ✓ 或 ○（同一行里紧跟着文字）；带结果的行点标题行
才展开查询条件 / 命中 / 差异，再点收起；卡片显示「已完成」时**所有行都是 ✓**；
全卡没有「详情」二字、点开也看不到 JSON。

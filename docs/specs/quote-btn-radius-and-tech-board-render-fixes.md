# 报价按钮倒角 + 技术工艺三处渲染修正 Spec

状态：Spec + 红测（已实现）
红测：`tests/test_quote_btn_radius_and_tech_board_render_red.py`

## 用户口径（四条，原文归纳）

1. 「正在查看第 1 步『确认需求配置』（可编辑）—— 当前进行到第 6 步 / 回到当前步骤 / 保存修改并重算」
   这一排里的按钮**没做倒角**；而且「你看一下全局还有哪有这样的问题」。
2. 右侧零件清单里的零部件库检索结论「**这段文字好像没做渲染**」，例如：

   ```
   零部件库检索：可复用 0 · 可改制 2 · 未匹配 2（库内 20 条 · 2026-09-16 18:14:37）
   P-001 上壳 → CMP-SEMI-EE-BLOCK-0001 搬运吸嘴主体安装块（可改制 · 65%）
   P-003 保护板/BMS → 库内无同类件（未匹配（按新制评估） · 51%）
   P-004 电芯组支架 → 库内无同类件（未匹配（按新制评估））
   ```

3. Agent 输出卡片（过程事件行）里的「**详情**」应该换行，「现在详情跟着展开很奇怪」。
4. 零件清单层级缩进不对：**零件应该在同一缩进、总成应该在同一缩进、工艺推荐应该缩进为零件的而不是总成的**。

范围：只改报价页 1.x 的按钮样式、技术工艺 2.1 看板（`app.js` + `workbench.css`）、
左栏会话过程行（`agent-chat.css`）。不动业务逻辑、不动接口、不动数据。

---

## A. 报价页 `.btn` 统一倒角（含全局同类问题排查）

### A0. 排查结论（实测，非推断）

对全仓「markup 里出现裸 `btn` 类名」的页面逐个核对它自己的样式来源
（内联 `<style>` + 同目录相对路径的本地 css）：

| 页面 | 基础 `.btn` 规则（含 `border-radius`） | 结论 |
|------|----------------------------------------|------|
| `BOM层级结构.html` | 有（`:67`） | 正常 |
| `XBOM智能体-配置BOM生成.html` | 有（`:1080`） | 正常 |
| `报价规则.html` | 有（`:764`） | 正常 |
| `规则助手-规则配置.html` | 有（`.chat-send, .btn`，`:329`） | 正常 |
| `tech_app/frontend/assembly-integration.html` | 有（`workbench.css`） | 正常 |
| `tech_app/frontend/cost-review.html` | 有（`workbench.css`） | 正常 |
| `tech_app/frontend/index.html` | 有（`workbench.css`） | 正常 |
| `tech_app/frontend/report.html` | 有（`report.css`） | 正常 |
| **`确认需求解析结果.html`** | **没有任何 `.btn` 基础规则** | **唯一的坏点** |

`确认需求解析结果.html` 的 `.btn` 只有两条**作用域受限**的规则：
`.bottom-bar .btn`（`:556`，给了 `padding` / `border-radius:10px` / `min-width:110px`）与
`.modal-footer .btn`（`:700`，只给了 `min-width:92px`）。于是该页 8 颗 `.btn` 里，
底部栏之外的 4 颗都没有倒角、没有内边距：

- `.view-bar .vb-actions` 的「回到当前步骤」「保存修改并重算」（`:831` / `:832`）——
  靠**行内** `style="padding:7px 14px;min-width:0"` 撑出尺寸，所以只有尺寸、没有倒角；
- 设置弹窗的「取消」「保存」（`:908` / `:909`）—— 连内边距都没有。

### A1. 契约

| 编号 | 契约 |
|------|------|
| A1 | `确认需求解析结果.html` 的 `<style>` 里新增**基础规则**，选择器正好是 `.btn`（允许与其他选择器并列分组），声明里同时含 `border-radius`、`display`、`align-items`、`padding`、`cursor`。 |
| A2 | 倒角值取 `10px`，与该页 `.bottom-bar .btn` 一致，不引入第二套视觉。 |
| A3 | `.view-bar .vb-actions .btn` 自己声明小尺寸 `padding:7px 14px` 与 `min-width:0`；两个按钮标签上的行内 `style="padding:7px 14px;min-width:0"` 必须删掉（行内样式不参与统一倒角，且让"这排按钮为什么小"变得不可查）。 |
| A4 | `.bottom-bar .btn` 的 `min-width:110px`、`.modal-footer .btn` 的 `min-width:92px`、`.btn-primary` 的渐变与投影、`.btn-secondary` 的描边**全部不变**。 |
| A5 | 设置弹窗的两颗按钮（取消 / 保存）修完后与底部栏同一套圆角与内边距（都吃基础规则）。 |
| A6 | **全局回归护栏**：凡在 markup 里使用裸 `btn` 类名的页面，其样式来源必须存在一条选择器含 `.btn` 且声明含 `border-radius` 的基础规则。上表 9 个页面都必须为真（今天 `确认需求解析结果.html` 为假）。 |

### A2. 明确不做

- 不改 `.btn-mini`、`.btn-delete`、`.icon-btn` 等页面局部小按钮（各自已有圆角或本就无边框）。
- 不改 `tech_app/frontend/*.css` 里已有的 `.btn` 基础规则（`workbench.css` / `report.css` /
  `flow.css` / `requirement-*.css` 的值不动）。
- 不给「不可见的遮罩按钮」（如 `index.html` 的 `.oc-drawer-backdrop`）加圆角 —— 它不是可见控件。

---

## B. 零部件库检索结论：结果区必须真的"渲染"出来

### B0. 现状（实测）

`app.js:1015` 的 `renderComponentMatchResult()` 已经在拼正确的结构
（`.component-match-summary` + `.component-match-list` + 每行 `.component-match-item`），
但 `workbench.css` 里**只有** `.component-match-unavailable*` / `.component-match-stale` 三条样式
（`:247`–`:255`），`.component-match-summary` / `.component-match-list` / `.component-match-item`
**一条都没有** → 结论区退化成一段没有任何层级的纯文字，这就是"没做渲染"。

同时行文案本身还有两个缺陷（`app.js` 的 `rowOf`）：

- 判定文案被塞进外层括号：`（未匹配（按新制评估） · 51%）` —— 嵌套括号；
- 「未匹配」的行也挂了匹配度：`· 51%`，而这一行的命中结论是「库内无同类件」，
  分数只说明"最像的候选也不太像"，挂在结论后面会让人以为命中了。

### B1. 契约

| 编号 | 契约 |
|------|------|
| B1 | `.component-match-summary` 有自己的样式（小号次要文字、与列表留出间距）；`.component-match-list` 是纵向列且有行间距；`.component-match-item` 是**一条独立记录**（有行内排列与分隔），不再是纯文本堆。 |
| B2 | 每行由三个结构化节点组成：`.component-match-part`（件号 + 名称）、`.component-match-hit`（命中件号 + 名称，或「库内无同类件」）、`.component-match-tag`（判定胶囊，带 `.reuse` / `.modify` / `.new` 三种配色）。不允许把三段拼成唯一一段 `textContent`。 |
| B3 | 判定文案原样进胶囊，**不再被外层括号包住**：渲染出的文本里不得出现 `（未匹配（` 这种嵌套括号。 |
| B4 | 匹配度只在**真的命中**（`component_code` 有值）时出现；未匹配的行不得挂匹配度。 |
| B5 | 未命中行的命中文案保持「库内无同类件」。 |
| B6 | 顶部小结行的口径不变：`可复用 {reuse} · 可改制 {modify} · 未匹配 {new}（库内 {library_size} 条 · {generated_at}）`；「库连不上」分支（`unavailable`）与「上一次结论可能已过期」分支的文案与行为不变。 |

---

## C. 过程事件行的「详情」换行

### C0. 现状（实测）

`agent-chat.js:1407` 的 `pushTaskStep()` 在带结构化明细时给过程行追加一个
`<details class="oc-process-detail">`（summary「详情」+ 工具行 + 输入 / 输出 JSON）。
但 `.oc-process-step`（`agent-chat.css:326`）是**横向 flex**，`.oc-process-detail`
（`:345`）只有 `flex:1; min-width:0` → 「详情」被当成同一 flex 行的第三个 item，
挤在过程文字右边，展开后更是把一行撑成两列。这就是"详情跟着展开很奇怪"。

### C1. 契约

| 编号 | 契约 |
|------|------|
| C1 | `.oc-process-step` 允许换行（`flex-wrap: wrap`），圆点与过程文字仍在同一行。 |
| C2 | `.oc-process-detail` 占满整行（`flex-basis: 100%`），落在过程文字**下一行**，不再与文字抢同一行。 |
| C3 | 展开区左侧与过程文字对齐：普通行缩进 `17px`（圆点 `10px` + 间距 `7px`）；`.sub` 行再叠加既有的 `14px` 缩进。 |
| C4 | summary 文案仍是「详情」，并给出可见的展开指示（`::before` 三角，展开时旋转 90°）；`::-webkit-details-marker` 仍隐藏。 |
| C5 | 只有带结构化明细的行才建 `.oc-process-detail`；没有明细的行 DOM 逐字不变（回归防护）。 |

---

## D. 零件清单缩进：总成一层、零件一层、子操作跟零件

### D0. 现状（实测，node 驱动真实函数）

`app.js:1520` 的 `renderNode(node, container, depth, partById)` 里 `const pad = 6 + depth * 14;`
把**层级深度**直接当缩进：总成用 `paddingLeft = pad`、零件用 `marginLeft = pad`。
同一份 IR（`A-001 → A-002 → P-001`，另有 `A-001 → P-002` 与根下 `P-003`）实测输出：

```
总成 paddingLeft : ["6px", "20px"]          ← 两个总成不同缩进
零件 marginLeft  : ["34px", "20px", "6px"]  ← 三个零件三档缩进
工艺推荐 marginLeft: [null, null, null]      ← 完全没有缩进，只吃 CSS 的 padding-left:10px
```

而 `.part-subactions`（`workbench.css:228`）是 `padding:0 0 0 10px` —— 10px 比总成的 6px 还靠里一点点，
比零件的 20px 靠外，所以「工艺推荐」看起来挂在**总成**那一档。

### D1. 契约

| 编号 | 契约 |
|------|------|
| D1 | `renderNode` 不再按深度缩放缩进：**所有总成行同一缩进，所有零件行同一缩进**。 |
| D2 | 两个缩进取固定值：总成 `6px`、零件 `20px`（= 今天"总成 + 一个子级"的观感），由具名常量表达，不再出现 `depth * …`。 |
| D3 | 零件行的子操作（`.part-subactions`，「工艺推荐」）与所属零件行**同缩进**，不再落在总成那一档。 |
| D4 | 父子关系、渲染顺序、零件行的 `onclick`、状态标记（几何✓ / 2D✓ / 待补参数 / 结果已过期）、推荐文案、`buildClientTree()` 全部不变 —— 本次只动"缩进从哪来"。 |

---

## E. 缓存版本号

`workbench.css`（B）与 `agent-chat.css`（C）被多个页面引用，改完必须同步 `?v=`：

| 文件 | 引用处 |
|------|--------|
| `workbench.css` | `tech_app/frontend/index.html:7`、`assembly-integration.html:7`、`cost-review.html:7` |
| `agent-chat.css` | `tech_app/frontend/index.html:10`、`tech-workbench.html:8`、`assembly-integration.html:10`、`cost-review.html:10` |
| `app.js`（B 的行结构 + D 的缩进） | `tech_app/frontend/index.html:269` |

`确认需求解析结果.html` 的改动是页内 `<style>`，不需要版本号。

## F. 验收

```
./open-claude/.venv/bin/python -m unittest tests.test_quote_btn_radius_and_tech_board_render_red
```

红测文件：`tests/test_quote_btn_radius_and_tech_board_render_red.py`。
其中 A `QuoteButtonRadiusTest` / B `ComponentMatchRenderTest` / C `ProcessDetailWrapTest` /
D `PartsTreeIndentTest` 是本批契约，实施前必须失败；`SpecPinsTheContractTest` 钉住本文件。

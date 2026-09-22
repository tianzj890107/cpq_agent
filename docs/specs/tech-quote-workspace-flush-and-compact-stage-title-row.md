# 报价 / 工艺工作区去卡片 + 工艺标题行收窄 + 嵌入态去灰底 Spec

状态：Spec + 红测（已实现）
红测：`tests/test_tech_quote_workspace_flush_red.py` `tests/test_tech_right_workspace_quote_rounded_card_red.py`

## 决策覆盖

本 Spec 以用户最新决策为准，**覆盖** `docs/specs/tech-right-workspace-quote-rounded-card.md`
的 §2「报价式卡片视觉」（`margin:16px` / `border-radius:12px` / `0.5px 边框`）与 §4「响应式」
（12px / 8px 非零外间距）。那张「业务结果卡」的**结构与内部顺序不变**，只取消它的卡片外观。

用户原话（三点）：

1. 报价和工艺工作区都用了圆角卡片把所有内容包起来 —— 这个不需要，直接填满整个工作区；
2. 工艺的标题行（`组装与整合` + `就绪` + `整合图纸 / 参数推荐 / 组装工艺` 这一行）太厚，要窄很多；
3. 工作区里还有一层卡片，它外面有灰底 —— 灰底取消，这层卡片四周外边距减半。
   **用户已确认：灰底只存在于技术工艺侧，报价侧没有那圈灰，这一条只改技术工艺。**

## 1. 报价工作区：结果区铺满，不再是圆角卡片

`确认需求解析结果.html` 的 `.results-area`（现 `:344`）：

| 声明 | 现在 | 改为 |
|------|------|------|
| `margin` | `var(--space-lg)`（16px） | `0` |
| `border` | `0.5px solid var(--border-color)` | `0` |
| `border-radius` | `var(--radius-lg)`（12px） | `0` |
| `background` | `var(--bg-page)` | **不变** |
| `display` / `flex-direction` / `flex` / `overflow` | `flex` / `column` / `1` / `hidden` | **不变** |

- 结果区从此与右栏等宽等高、不留白、不圆角、不描边，`#resultsContent` 与 `.bottom-bar` 照旧在内部滚动与常驻。
- `.right-panel`（现 `:269`，`background: var(--bg-page)`）**不动** —— 用户确认报价侧没有那圈灰。
- 报价页的分隔由既有 `.chat-panel { border-right: 0.5px solid var(--border-color) }` 承担，不靠结果区自己的边框。

## 2. 工艺工作区：结果区铺满，不再是圆角卡片

`tech_app/frontend/tech-workbench.css` 的 `.tech-results-area`（现 `:337`）：

| 声明 | 现在 | 改为 |
|------|------|------|
| `margin` | `16px` | `0` |
| `border` | `.5px solid var(--twb-border)` | `0` |
| `border-radius` | `12px` | `0` |
| `background` | `var(--twb-card)` | **不变** |
| `display` / `flex-direction` / `flex` / `min-height` / `overflow` / `box-shadow` | `flex` / `column` / `1 1 auto` / `0` / `hidden` / `none` | **不变** |

- `#techContextHeader`、`#techWorkspaceOutlet`、`.tech-workbench-bottom` 三个子块在卡片内的**顺序与归属不变**，只是这张卡不再有外边距与圆角。
- 响应式同步归零：`.tech-results-area { margin:12px }`（现 `:947`）与 `.tech-results-area { margin:8px }`（现 `:989`）
  都改为 `margin:0`；窄屏不回退成别的间距。
- `.tech-workspace-pane`（现 `:322`）**不动**：仍然 `margin` 为 0、`border-radius:0`、`box-shadow:none`、
  `border-left:.5px solid var(--twb-border)`，右侧列与左栏的分隔由它承担。
- `.tech-workspace-context` 的 `border-bottom` 与 `.tech-workbench-bottom` 的 `border-top` 两条内部细分隔线**保留**。

## 3. 工艺标题行收窄

`tech_app/frontend/tech-workbench.css` 的 `.tech-workspace-context`（现 `:546`）：

| 声明 | 现在 | 改为 |
|------|------|------|
| `min-height` | `52px` | `34px` |
| `padding` | `8px 16px` | `4px 16px` |
| `align-items` / `justify-content` / `border-bottom` / `background` | `center` / `space-between` / `.5px` 分隔线 / `var(--twb-card)` | **不变** |

- 这一行仍是**固定高度**：`min-height` 必须是一个固定 px，五个大流程（含没有子页签的 1、5）共用同一高度，
  不允许写成由内容撑开、或按子页签数量变化。
- 行高只由标题行自己的 `min-height` / `padding` 决定：`.tech-context-title`（13px）、
  `.tech-context-notice`（`padding:2px 10px`）、`.tech-substep-btn`（`padding:5px 12px`）
  的字号与内边距**一个都不许改**，不允许靠缩小页签或状态胶囊把行弄矮。
- `.tech-substeps-slot` 的 `margin-left:auto` 与 `.tech-substeps-bar` 的右对齐保持，右端仍是
  `整合图纸 / 参数推荐 / 组装工艺`。

## 4. 工艺嵌入态：取消灰底 + 四周间距减半

只作用于 `.tech-embed`（父工作台 iframe 里的阶段页）。样式仍由
`tech_app/frontend/tech-embed.js` 注入的 style 块承担。

| 规则 | 现在 | 改为 |
|------|------|------|
| 阶段页底色 | `workbench.css:12` 的 `body { background: var(--bg-page) }`（#F5F5F5 灰） | `.tech-embed body { background:#fff !important; }` |
| `.tech-embed .oc-shell .page-container` 左右内边距 | `18px !important` | `9px !important` |
| `.tech-embed .oc-shell .page-container` 上下内边距 | `var(--space-xl)` = 20px（`workbench.css:13` / `:95`） | `10px !important` |
| `.tech-embed body` 下内边距 | `18px !important` | `9px !important` |

- 阶段页里那层卡片（`.center-panel`，`workbench.css:48` 的白底 + `1px` 边框 + `12px` 圆角）**本身保留**：
  本批只取消它外面的灰底、把它四周的间距减半，不动它的底色、边框、圆角与内部业务样式。
- 灰底取消与间距减半**必须写在 `.tech-embed` 作用域内**：独立打开 `assembly-integration.html` /
  `cost-review.html` / `index.html`（没有 `.tech-embed`）时，`workbench.css` 的灰底与
  `.page-container` 的 20px 内边距照旧，页面自身不分叉。
- 现成的 `.tech-embed .oc-shell .page-container { width:100% !important; max-width:none !important;
  min-width:0 !important; margin-left:0 !important; margin-right:0 !important; }` 整列满宽声明保持；
  四条内边距用 longhand 写全，避免与既有 `padding-left/right` 简写互相覆盖。

## 5. 必须保留

- 三列贴合骨架（56px 导航 / 460px 会话列 / 1fr 工作区）、`#techWorkspaceOutlet`、
  `#techContextHeader`、`.tech-workbench-bottom` 的 DOM 与顺序；
- `.tech-stage-frame`：`width:100% / height:100% / border:0 / border-radius:0` 不变；
- 九阶段 URL、iframe 加载、`postMessage`、`TechBoardBridge`、`syncChatActions()` 与看板动作注册；
- 报价 `.results-header` / `.results-content` / `.bottom-bar` 的结构与内边距；
- 子页签与状态胶囊的既有样式、标题行右对齐布局、既有两条细分隔线；
- 除本 Spec 明确改动的声明外，`.tech-workspace-context`、`.center-panel`、`.right-panel` 的其他样式不动。

## 禁止事项

- 不给 `.tech-results-area` / `.results-area` 换一种新的卡片（不许改成阴影卡、渐变卡、描边卡）；
- 不删 `#techResultsArea` 这层 DOM（只去外观），不改三个子块的顺序；
- 不改 `workbench.css` 的 `body` 底色与 `.page-container` 基础内边距（独立打开阶段页照旧）；
- 不动 `.center-panel` 自身的底色 / 边框 / 圆角；
- 不缩小子页签、状态胶囊或标题字号来"假装"标题行变窄；
- 不给报价侧加灰底、不给报价 `.results-area` 加返回来的间距；
- 不修改本 Spec 与对应 Red 测试；
- 不提交、推送、MR、merge、tag、Release、部署或重启服务。

## 验收标准

1. 报价右栏：结果区四周无留白、无圆角、无边框，内容铺满整个右下工作区。
2. 工艺右栏：结果卡四周无留白、无圆角、无边框，标题行 / 阶段内容 / 底栏铺满右侧列；
   桌面、中等屏、窄屏都不再出现非零外边距。
3. 工艺标题行高度从 52px 收到 34px，仍为固定高度、仍居中、右端子页签仍靠右。
4. 工艺嵌入态阶段页没有灰底，四周间距为现在的一半（上下 10px、左右 9px）。
5. 独立打开阶段页的视觉与今天一致（灰底、20px 内边距）。
6. 九阶段导航、iframe、看板桥、底栏动作、报价结果区内部结构与业务能力零回归。

## 对应测试

- `tests/test_tech_quote_workspace_flush_red.py`（本批新增）
- `tests/test_tech_right_workspace_quote_rounded_card_red.py`（本批按新契约更新几何与响应式断言）
- `tests/test_tech_drawing_title_and_result_actions_cleanup_red.py`（本批按新契约更新标题行高度断言）

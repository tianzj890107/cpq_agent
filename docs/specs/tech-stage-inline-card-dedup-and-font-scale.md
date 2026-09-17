# 2.2 / 2.3 六个页签：去掉重复的页内卡片层 + 内容卡片字号分级 Spec

状态：TDD Red，等待 DeepSeek 实现。

## 决策与用户口径

用户原话（两条）：

1. 「整合图纸 / 参数推荐 / 组装工艺 / 零件成本 / 组装成本 / 汇总 这六个页签，每个页里面内容里面
   包了多余的一层，标题和流程标题完全一样，完全是重复的。直接把这个多余的圆角卡片连同标题
   一起去掉，直接显示『已上传的整合图纸』『整机概览』『工序明细』『零件成本（2.1 拆出来的每个零件）』
   『组装成本 · 便携式锂电池 PACK（108×56×26.5）』『汇总』等等这个级别的内容卡片。」
2. 「这六页的内容是不是字体相比于别的页来说有点小，而且不是很统一。比如『材料 0.71 / 人工 0.06 /
   制造费用 0.03 / 加工费用 0.02』这里就很合适，但『0.82 0.82 单件 小计 操作』这里的字体就太小，
   『来料检验与配组 / 设备: 检验台、量具 / 工时: 4 分』这也太小。**你只看哪些需要大一点，
   不要全都直接变大。**」

本 Spec 以用户最新决策为准，**部分覆盖** `docs/specs/tech-quote-workspace-flush-and-compact-stage-title-row.md`
的 §「内层 `.center-panel` 自身保留」一句：上一批保留的正是本批要删掉的那层卡（原因见下），
该 Spec 其余契约（外框 `.tech-results-area` 归零、标题行 34px、嵌入态白底）**继续有效**。

## 1. 只读定位（实测，非推断）

### 1.1 「多余的一层圆角卡片」= 阶段页自己的 `.center-panel`

`tech_app/frontend/workbench.css:48`：

```
.center-panel{background:var(--bg-card);border-radius:var(--radius-lg);
              border:1px solid var(--border-color);display:flex;flex-direction:column;...}
```

它把这两个阶段页的**全部内容**包住：

```
.center-panel                                  ← 多余的一层圆角卡片
└── .center-header                             ← 卡片头（padding 12px 16px + 分隔线 + sticky 阴影）
    ├── #aiPanelTitle / #crPanelTitle          ← 重复标题（本批要删）
    └── .ai-tabs                               ← 页签（保留）
├── .ai-actions / .ai-status                   ← 嵌入态已被 tech-embed 隐藏
└── .ai-body / #crBody                         ← 真正的内容卡片层（保留）
    ├── section.inline-card 「已上传的整合图纸」
    ├── section.inline-card 「整机概览」
    └── …
```

### 1.2 「标题和流程标题完全一样」= `.center-title` 被写成页签文案

- `tech_app/frontend/assembly-integration.html:181`
  `<div id="aiPanelTitle" class="center-title">整合图纸</div>`
  由 `assembly-integration.js:1058` `$ai('aiPanelTitle').textContent = AI_TABS[aiTab];` 每次渲染写一次；
  `AI_TABS = { drawings: '整合图纸', params: '参数推荐', process: '组装工艺' }`（`:19`）。
- `tech_app/frontend/cost-review.html:148`
  `<div id="crPanelTitle" class="center-title">成本清单</div>`
  由 `cost-review.js:515` `$cr('crPanelTitle').textContent = CR_TABS[crTab];` 每次渲染写一次；
  `CR_TABS = { parts: '零件成本', assembly: '组装成本', total: '汇总' }`（`:21`）。

→ 用户点这六个页签时，这行标题**依次等于**「整合图纸 / 参数推荐 / 组装工艺 / 零件成本 / 组装成本 / 汇总」，
正是他列出的那六个名字；它和页签按钮、和父壳标题行的子页签**逐字重复**。

### 1.3 嵌入态只剩这一行是重复的

`tech_app/frontend/tech-embed.js`（`EMBEDDED` 分支，`:131` 起）：

- `:160` `.tech-embed .title-section { display:none !important; }` —— 页内大标题「组装与整合 / 2.3 成本测算」已隐藏；
- `:161` `.tech-embed .ai-tabs { display:none !important; }` —— 页内页签已隐藏（父壳 `tech-workbench.html:156-160`
  的 `#techContextHeader` + `#techSubstepsBar` 承担）；
- `:172` `.tech-embed #aiActions`、`:174` `.tech-embed #crRunAll`、`:173`/`:175` `… .ai-ops`、`:166` `.ai-status`
  也都被隐藏。

**没有被隐藏的**就是这个 `.center-panel` 圆角边 + `.center-header` 里的 `.center-title` —— 于是嵌入态下
它看上去就是「一层多余的圆角卡片，卡片头上写着一个和流程标题一模一样的标题」。

### 1.4 内容卡片本身是对的，必须保留

`.inline-card` / `.inline-card-title`（`inline-analysis.css`）在下列函数里逐块渲染，
标题就是用户点名要「直接显示」的那一批，**本批不动它们的结构、顺序与文案**：

| 页 | 渲染函数 | 内容卡片标题 |
|----|----------|--------------|
| 2.2 整合图纸 | `assembly-integration.js:423` / `:437` | 已上传的整合图纸 / 来自 2.1 的零件（参数推荐的另一路输入） |
| 2.2 参数推荐 | `assembly-integration.js:488` / `:511` / `:525` / `:540` | 报价必填参数完成度 / 整机概览 / 零件间连接 / 整机 BOM（单台用量） |
| 2.2 组装工艺 | `assembly-integration.js:905` / `:919` / `:1010` / `:1025` / `:1044` | 工序明细 / 装配方案 / 工艺库覆盖 / 库内依据 · 工艺库 / 假设与待澄清 |
| 2.3 零件成本 | `cost-review.js:290` | 零件成本（2.1 拆出来的每个零件） |
| 2.3 组装成本 | `cost-review.js:324` | 组装成本 · ${零件/整机名} |
| 2.3 汇总 | `cost-review.js:345` / `:368` | 汇总 / 本步状态 |

## 2. 契约一：去掉页内这一层圆角卡片与重复标题

改动范围严格限定在 **2.2 / 2.3 两页**（`assembly-integration.css` 被这两页共同引用；
`cost-review.css` 只被 2.3 引用），**不改 `workbench.css` 的共享基线**，
因此 `index.html`（2.1）以及 requirement-* / report-* 等其它阶段页的 `.center-panel` 一个像素都不变。

### 2.1 CSS：这两页的 `.center-panel` 不再是卡片

在 `tech_app/frontend/assembly-integration.css` 追加（该文件在两页里都最后加载，能压过 `workbench.css`）：

| 声明 | 现在（`workbench.css:48` 继承） | 改为 |
|------|-------------------------------|------|
| `background` | `var(--bg-card)` | `transparent` |
| `border` | `1px solid var(--border-color)` | `0` |
| `border-radius` | `var(--radius-lg)` | `0` |
| `box-shadow` | 无（防御性写死） | `none` |
| `display` / `flex-direction` / `max-height` / `overflow-y` | `flex` / `column` / `calc(100vh - 180px)` / `auto` | **不变** |

- 选择器必须落在 `.oc-work .center-panel`（比 `.center-panel` 高一级），保证压得住 `workbench.css`；
- 内容从此直接铺在工作区里，`#aiBody` / `#crBody` 的 `padding:12px 16px 20px` 与 `gap:12px` **不变**。

### 2.2 CSS：嵌入态这一行整行退出布局

`.center-header` 里删掉标题后只剩页签，而嵌入态页签已被 `tech-embed.js` 隐藏 —— 若只删标题，
会留一条 ~24px 的空白条 + 一条分隔线。因此在 `assembly-integration.css` 里补一条
**只在嵌入态生效**的规则：

```
.tech-embed .oc-work .center-panel > .center-header { display: none; }
```

- 作用域带 `.tech-embed`，独立打开这两个阶段页（`tech-embed.js` 不会加这个 class）时页签行照旧显示；
- 不得写成裸 `.center-header { display:none }`，也不得改 `tech-embed.js` 的共享 `style.textContent`
  清单（那段是 11 个阶段页共用的）。

### 2.3 HTML：删掉重复标题节点

- `assembly-integration.html:181` 删除 `<div id="aiPanelTitle" class="center-title">整合图纸</div>`；
- `cost-review.html:148` 删除 `<div id="crPanelTitle" class="center-title">成本清单</div>`；
- `.center-header` 与 `.ai-tabs`（`#aiTabs` / `#crTabs` 及三颗按钮）保留；
- 删完后这两页的 HTML 里**不再出现 `center-title`**。

### 2.4 JS：删掉写标题那一行

- 删除 `assembly-integration.js:1058` `$ai('aiPanelTitle').textContent = AI_TABS[aiTab];`
  （`aiRender()` 内，页签高亮那段保留）；
- 删除 `cost-review.js:515` `$cr('crPanelTitle').textContent = CR_TABS[crTab];`
  （`crRender()` 内，`.active` 切换与后面的 `$cr('crState')` 那行保留）；
- `AI_TABS` **必须保留**：它还有 4 处用途（`assembly-integration.js:273` 页签切换提示、
  `:367` 生成按钮文案、`:558` 生成步骤文案、`:1569` 缺少产出时的提示）；
- `CR_TABS` 只服务被删的那一行，可一并删除；删之前必须 `grep -n CR_TABS` 确认为 0 处引用。

## 3. 契约二：内容卡片字号改为三档，不再出现 9px / 10px

### 3.1 基准（用户点名的「很合适」样本）

- `.cr-part { font-size: 12px }` + `.cr-part i { font-size: 11px }`（`cost-review.css:9`/`:11`）——
  即「材料 0.71 / 人工 0.06 / 制造费用 0.03 / 加工费用 0.02」那一格 —— **保持原样，作为基准**；
- `.inline-analysis` 根字号 `12px`（`inline-analysis.css:2`）**不变**。

### 3.2 三档

| 档 | 字号 | 用途 |
|----|------|------|
| 卡片标题 | `13px` | 一个内容卡的最高一级标题 |
| 正文 | `12px`（工序名 `12.5px`） | 表格、工序明细、可编辑字段、正文行 |
| 注解 | `11px` | 提示、警示、待澄清、来源、假设、标签、徽标、计数小标签 |

内容卡片里**不再保留 9px 与 10px**（面板自身的头部/页签/输入/状态行不在本契约内，见 3.4）。

### 3.3 逐条改动（`tech_app/frontend/inline-analysis.css`）

标题档：

| 选择器 | 现在 | 改为 |
|--------|------|------|
| `.inline-card-title` | `11px` | **`13px`** |
| `.inline-step-title` | `11px` | **`12.5px`** |

正文档（`12px`）：

| 选择器 | 现在 | 改为 |
|--------|------|------|
| `.inline-row` | `11px` | **`12px`** |
| `.inline-cov-row` | `11px` | **`12px`** |
| `.inline-description` | `10px` | **`12px`** |
| `.inline-step-grid` | `9px` | **`12px`** |
| `.inline-edit-grid label` | `9px` | **`12px`** |
| `.inline-edit-grid input, .inline-edit-grid select, .inline-edit-grid textarea` | `font:10px/1.35 inherit` | **`font:12px/1.35 inherit`** |
| `.inline-cost-table` | `9px` | **`12px`** |
| `.inline-cost-table input, .inline-cost-table select` | `9px` | **`12px`** |

> `.inline-step-grid` 与 `.inline-cost-table` 是用户直接点名的两处：
> 「设备: 检验台、量具 / 工时: 4 分」在 `inline-analysis.js:434` / `assembly-integration.js:956-957` 的
> `.inline-step-grid`；「0.82 0.82 单件 小计 操作」是 `.inline-cost-table` 的表头与单元格。

注解档（`11px`）：

| 选择器 | 现在 | 改为 |
|--------|------|------|
| `.inline-hint` | `10px` | **`11px`** |
| `.inline-warn, .inline-question` | `10px` | **`11px`** |
| `.inline-source, .inline-assumption` | `10px` | **`11px`** |
| `.inline-reference` | `10px` | **`11px`** |
| `.inline-reference small` | `9px` | **`11px`** |
| `.inline-lib-step` | `10px` | **`11px`** |
| `.inline-lib-step code` | `9px` | **`11px`** |
| `.inline-lib-step small` | `9px` | **`11px`** |
| `.inline-cat-bar` | `10px` | **`11px`** |
| `.inline-cat-tag` | `9px` | **`11px`** |
| `.inline-cov-code` | `10px` | **`11px`** |
| `.inline-cov-split` | `10px` | **`11px`** |
| `.inline-totals span` | `9px` | **`11px`** |
| `.inline-cost-total span` | `10px` | **`11px`** |
| `.inline-cost-total em` | `9px` | **`11px`** |
| `.inline-type` | `9px` | **`11px`** |
| `.inline-confidence` | `9px` | **`11px`** |
| `.inline-dep` | `9px` | **`11px`** |
| `.inline-sno` | `10px` | **`11px`** |

### 3.4 明确不改（防止「全都直接变大」）

- **面板自身的头部/控件**一律不动：`.inline-analysis-head`、`.inline-analysis-title strong`（`14px`）、
  `.inline-analysis-title small`（`10px`）、`.inline-analysis-close`、`.inline-analysis-tabs button`、
  `.inline-analysis-inputs textarea`、`.inline-analysis-qty`、`.inline-file-picker span/em`、
  `.inline-analysis-actions`、`.inline-action`、`.inline-analysis-status`、`.start-parse-btn`、
  `.inline-analysis-icon`；
- **数值大字号**一律不动：`.inline-totals strong`（`15px`）、`.inline-cost-total strong`（`23px`）；
- **卡片几何**一律不动：`.inline-card` 的 `padding:9px`、`border`、`border-radius`、`box-shadow`；
- **表格排版**一律不动：`.inline-cost-table th/td` 的 `padding:5px 4px`、`min-width:650px`、
  `.inline-cost-table-wrap` 的 `overflow-x:auto`；
- **2.3 自有样式**一律不动：`cost-review.css` 的 `.cr-part` / `.cr-part i` / `tr.cr-final`；
- **其它页**不动：`workbench.css`、`assembly-integration.css` 里 `.ai-*` 的字号、
  `tech-embed.js` 的共享隐藏清单、`.center-title` 在 `workbench.css` 的定义（`13px`）。

### 3.5 影响面（需要知情，不是越界）

`inline-analysis.css` 由 `index.html`（2.1）、`assembly-integration.html`（2.2）、
`cost-review.html`（2.3）三页共用，2.1 的右侧内嵌面板用的是**同一套** `.inline-card` /
`.inline-step-grid` / `.inline-cost-table`。因此本批改的是**共用规则**，2.1 的同名卡片会同步变大 ——
这正是用户要的「统一」；如果改成只给 2.2/2.3 加页面级覆盖，就会出现同一个「工序明细」在两页里
两种字号。

## 4. 版本号

改 CSS 必须同步引用页的 `?v=`（否则浏览器吃缓存，验收看不到变化）：

| 文件 | 引用位置 | 现在 | 改为 |
|------|----------|------|------|
| `inline-analysis.css` | `index.html:8` / `assembly-integration.html:8` / `cost-review.html:8` | `?v=20260819-flat6` | **`?v=20260917-font1`** |
| `assembly-integration.css` | `assembly-integration.html:14` / `cost-review.html:14` | `?v=ai8` | **`?v=ai9`** |
| `cost-review.css` | `cost-review.html:15` | `?v=cr1` | 只有确实改了才提到 **`?v=cr2`** |

`.js` 版本号：本批会改 `assembly-integration.js` / `cost-review.js`（各删一行），
`?v=` 相应提升（现在分别是 `ai18` / `cr6`）。

## 5. 禁止事项

- 不删 `.inline-card` 这一层内容卡片，不改它们的顺序、标题文案与渲染函数；
- 不动 `#aiTabs` / `#crTabs` 页签与 `.center-header` 的 DOM 结构（只删标题那一个节点）；
- 不改 `workbench.css` 的 `.center-panel` / `.center-header` / `.center-title` 共享定义；
- 不改 `tech-embed.js` 的共享隐藏清单，不新增裸 `body` / 裸 `.center-header` 规则；
- 不动 `#aiBody` / `#crBody` 的 padding / gap / display；
- 不动 `index.html` 的 `.center-panel`（2.1 的 3D 视图卡）与 `app.js:2775` 的选择器；
- 不放大 3.4 节列出的任何一项，尤其不接受「把整页 `font-size` 调大一档」这种一刀切做法；
- 不改后端、路由、权限、成本算法、任务协议。

## 6. 验收标准

1. `tests/test_tech_stage_inline_card_dedup_and_font_scale_red.py` **全绿**；
2. 无头 Chrome 实测（嵌入态打开 2.2 / 2.3 任一页签）：
   - `.oc-work .center-panel` 计算样式 `background-color` 为透明、`border-top-width` 为 `0px`、
     `border-top-left-radius` 为 `0px`；
   - 页内不存在 `#aiPanelTitle` / `#crPanelTitle`；
   - `.inline-cost-table` 与 `.inline-step-grid` 的计算 `font-size` 为 `12px`；
   - `.inline-card-title` 为 `13px`；`.cr-part` 仍为 `12px`；
3. 独立打开（不带 `embed=1`）这两个阶段页时，页签行（`整合图纸 / 参数推荐 / 组装工艺`）照旧显示；
   2.1 与其它阶段页的 `.center-panel` 计算样式与本批之前逐字节一致；
4. `open-claude/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'` 全绿；
5. `node --check tech_app/frontend/assembly-integration.js` 与 `node --check tech_app/frontend/cost-review.js` 通过；
6. `git diff --check` 干净。

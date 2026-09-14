# Spec: 报价输入框对齐工艺规格 + 工艺输入区贴住会话列底部

状态：TDD Red，等待 DeepSeek 实现。
适用分支：`20260909`
相关页面：报价单智能体 `确认需求解析结果.html`、技术工艺统一工作台 `tech_app/frontend/tech-workbench.html`

## 1. 问题

两个工作台的会话输入区互相不一致，且工艺侧底部留白：

1. 报价侧输入框比工艺侧大一圈：报价 `.chat-input-wrapper` 高 76px、内衬
   `10px 12px 10px 20px`、`gap: 12px`、textarea `15px/24px`、附件圆钮 50px、发送圆钮
   54px；工艺 `.oc-inputbox-single`（继承 `.oc-inputbox`）高 52px、内衬
   `7px 8px 7px 12px`、`gap: 8px`、textarea `12px/20px`、圆形 + 34px、圆形发送 36px。
   用户要求报价按工艺的尺寸与字号改。
2. 工艺会话列没有贴底：`.tech-workbench-body` 是三列 grid（`height: 100vh`，
   `grid-template-rows: 100%`，行高 900px），但 `#techChatPane` 实测只有 862px ——
   它继承了 `agent-chat.css:122` 的 `.oc-agent-pane { height: calc(100vh - 38px);
   max-height: calc(100vh - 38px); align-self: start; position: sticky; }`。工艺侧的
   `#techChatPane.oc-agent-pane, .tech-chat-pane` 规则只覆盖了 `height`（100%）与
   `position`，没覆盖 `max-height`，于是 `max-height: 862px` 把整列压短，输入区底边停在
   862px，视口底部留下 38px 空白。报价侧 `.chat-panel` 是 900px、`.chat-input-area`
   底边与列底重合（实测差值 0），所以看起来贴底。

实测（headless Chrome，1440×900，改前）：

| 位置 | 工艺 | 报价 |
| --- | --- | --- |
| 会话列高度 | 862px（列底 862） | 900px（列底 901） |
| 输入区容器底边 | 862px | 901px = 列底 |
| 输入框高度 | 52px | 76px |
| textarea 字号/行高 | 12px / 20px | 15px / 24px |
| 附件圆钮 / 发送圆钮 | 34px / 36px | 50px / 54px |

验证：把 `#techChatPane` 的 `max-height` 临时改成 `none` 后，列高 862 → 900，
输入区容器底边 862 → 900，与报价一致。

## 2. 目标

- 报价侧输入框的几何尺寸与字号按工艺侧 `.oc-inputbox-single` 逐项对齐（尺寸、字号、
  圆角、间距、两个圆形按钮），配色继续用报价页自己的 token。
- 工艺侧会话列必须撑满可用高度，输入区容器底边贴住会话列底部（差值 0），与报价一致；
  两侧输入区的底部内衬取同一数值。

## 3. 契约

### R1 工艺会话列撑满、输入区贴底

- `tech-workbench.css` 的 `#techChatPane.oc-agent-pane, .tech-chat-pane` 规则在保留
  `height: 100%`、`position: static`、`border: 0` 的同时，必须显式写
  `max-height: none`，覆盖从 `.oc-agent-pane` 继承来的 `calc(100vh - 38px)` 上限。
- `agent-chat.css` 的 `.oc-agent-pane`（独立 2.1 页那套 sticky 双栏）保持
  `height/max-height: calc(100vh - 38px)`、`position: sticky`、`align-self: start`
  **不变** —— 本批只修统一工作台里被连带压短的问题。
- `.tech-workbench-body` 继续 `height: 100vh` + `grid-template-rows: 100%`，让
  `height: 100%` 有确定解析基准。

### R2 两侧输入区底部内衬一致

- 报价 `.chat-input-area` 的 `padding-bottom` 与工艺 `#techChatPane .oc-composer` 的
  `padding-bottom` 必须相等（当前报价 16px，工艺 0 → 工艺改成 16px）。
- 两侧的输入区都必须是会话列的最后一个常驻区块（工艺 `#techChatPane .oc-thread`
  `flex: 1 1 auto` + composer `flex: 0 0 auto`；报价 `.chat-messages` + `.chat-input-area`），
  不得新增底部占位元素。

### R3 报价输入框尺寸与字号 = 工艺 `.oc-inputbox-single`

`.chat-input-wrapper` 必须与工艺侧合并后的 `.oc-inputbox` + `.oc-inputbox-single` 逐项相等：

| 属性 | 工艺值（合并后） | 报价现值 |
| --- | --- | --- |
| `min-height` | `0` | `76px` |
| `padding` | `7px 8px 7px 12px` | `10px 12px 10px 20px` |
| `gap` | `8px` | `12px` |
| `border-radius` | `24px` | `24px`（已一致） |

`.chat-input`（textarea）必须与工艺 `.oc-inputbox textarea` + `.oc-inputbox-single textarea`
合并后一致：`font-size: 12px`、`line-height: 20px`、`max-height: 120px`、`flex: 1`、
`min-width: 0`。

圆形按钮同规格：

- `.chat-attach-btn` = 工艺 `.oc-add`：`width/height/flex-basis: 34px`、`border-radius: 50%`、
  图标 `font-size: 18px`（报价现值 50px）。
- `.chat-send` = 工艺 `.oc-inputbox-single .oc-send`：`width/height/flex-basis: 36px`、
  `border-radius: 50%`、图标 `font-size: 22px`（报价现值 54px，图标已 22px）。
- 报价侧继续用本页配色 token（`--color-primary` / `--bg-secondary` 等），不照抄工艺的
  `--oc-*` 变量；工艺侧也不改自己的配色。

## 4. 禁止事项

- 不改两个页面的按钮功能、附件上传、Enter 发送 / Shift+Enter 换行 / IME 保护、输入框
  自增高（`autoSize` / `rows=1` + `max-height` 上限）。
- 不改 `.oc-disc`（会话绑定说明）文案与位置语义；不为了贴底把它删掉或改成绝对定位。
- 不动桥协议、看板、后端路由、Agent 工具；不改独立 2.1 页（`index.html` /
  `agent-chat.css` 的 `.oc-agent-pane`）的 sticky 双栏布局。
- 不把报价页输入框改小到影响可用性：只按工艺现有规格对齐，不新增第三种尺寸。

## 5. 验收

1. `python3 -m unittest tests.test_quote_tech_composer_geometry_and_flush_bottom_red`
   全绿（实现前应失败）。
2. 全量 `python3 -m unittest discover -s tests -p 'test_*.py'` 不因本批新增失败。
3. 浏览器（1440×900）：工艺统一工作台左侧会话列高 = 视口高，输入区容器底边与会话列底边
   重合（差值 0）；报价页与工艺页输入框高度、圆角、内衬、字号、两个圆钮尺寸一致；
   两页输入区底部内衬相同。

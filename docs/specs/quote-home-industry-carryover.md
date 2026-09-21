# 规格：报价建单的行业带过去（首页 → 工作台 → 卡片）

状态：Spec + 红测（**未实现**）
红测：`tests/test_quote_home_industry_carryover_red.py`

相关规格：
`docs/specs/global-industry-registry-and-packaging.md`（行业清单唯一事实源）、
`docs/specs/quote-agent-industry-alignment.md`（④产品技术参数按行业换源）、
`docs/specs/quote-packaging-box-library-selection.md`（包装候选换源）。

## 1. 现场问题（实测，不是推断）

客户在首页选「包装」→「新建报价」→ 进第 1 步，行业下拉仍然显示**半导体**。

**断点 1：首页报价路径根本没把行业带走。**

| 位置 | 现状 |
| --- | --- |
| `报价首页.html:1319-1326` | 行业下拉 `#techIndustry`（报价与技术工艺共用） |
| `报价首页.html:2212-2216` | 技术工艺路径 `techCreateAndGo()` 会拼 `?industry=`，注释写着「行业用 URL 带过去，否则这里选的『电池』到了需求页会静默变回默认的『半导体』」 |
| `报价首页.html:2506-2509` | 报价路径 `confirmProjectAndGo()` 只写 `cpq:projectName` / `cpq:projectCode` / `cpq:customer` |
| `报价首页.html:2535-2552` | `goToAssistant()` 只写 `cpq:requirement` / `cpq:files` / `cpq:fileContents` |

→ 报价这条路**一个字节的行业信息都没传**（同一类问题在技术工艺路径上早已修过，报价路径漏了）。

**断点 2：工作台只能落默认值。**

| 位置 | 现状 |
| --- | --- |
| `确认需求解析结果.html` | 全文没有 `URLSearchParams`，不读任何行业 storage |
| `确认需求解析结果.html:960` | `let CURRENT_INDUSTRY = ''` |
| `确认需求解析结果.html:4636` | 只取 `meta.default_industry` |
| `确认需求解析结果.html:989` | 再兜 `INDUSTRIES[0]` |
| `cpq_industries.py:25` / `:19` | `DEFAULT_INDUSTRY = "semiconductor"`，且清单第一项也是它 |

**断点 3：行业没进卡片。** `wfSyncCard()`（`确认需求解析结果.html:2931-2946`）不带 industry；
`/wf/card/sync`（`cpq_suite_server.py:660-665`）也没从请求体透传；而
`cpq_wf.sync_card()`（`cpq_wf.py:595-600`）**本来就支持** `industry=`（非空才写、留空不猜）→
卡片 `industry` 一直是 NULL，之后「转技术工艺」时行业还会丢。

**业务影响（不只是显示）**：第 1 步的必填门禁、④产品技术参数换源、候选匹配源都按上报的行业算
（`确认需求解析结果.html:2352`、`:2395` 带 `industry: currentIndustry()`；后端 `_industry_of()`
只认显式值，`cpq_agent_server.py:1145-1154`）→ 包装询盘会按半导体必填项与半导体/电池参数表走，
即 ## 189/191 修过的「包装询盘 Top3 全是锂亚电池」换了个入口重现。

## 2. 契约

### 2.1 行业清单仍是唯一事实源

行业键、标签、默认行业只有 `cpq_industries.py` 一份。

- `报价首页.html` 的行业下拉按现状保留静态 `<option>`，但必须与 `cpq_industries.INDUSTRY_KEYS`
  **同序同值**、标签与 `label_of()` 一致（红测逐项比对，加行业时漏改会被挡住）；
- `确认需求解析结果.html` 的行业下拉**不得**硬编码，只能消费 `/api/meta` 下发的
  `industries` / `default_industry`，合法性判定也按下发清单来。

### 2.2 首页 → 工作台：报价路径必须携带行业

`报价首页.html`：

1. `confirmProjectAndGo()` 在 `intent === "quote"` 时写入 `sessionStorage.setItem('cpq:industry', techIndustry())`
   （与技术工艺路径同一取值函数，保证两处口径一致）。
2. `goToAssistant()` 跳转报价页时在 URL 上追加 `?industry=<encodeURIComponent(行业键)>`
   （storage 供刷新/回退，URL 供首次进入；两者都要有）。
3. `intent === "config"` / `intent === "rule"` **不写** `cpq:industry`（配置/规则与行业模板无关，
   写进去会给后续建单塞假行业）。
4. 技术工艺路径既有的 `?industry=` 行为保持不变。

### 2.3 工作台取值优先级（唯一实现点）

`确认需求解析结果.html` 新增**纯函数**：

```js
function resolveInitialIndustry(cardIndustry, urlIndustry, storedIndustry, defaultIndustry,
                                knownIndustries) {
  // 逐级取值先 String(v || '').trim().toLowerCase()（与 cpq_industries.normalize 同口径），
  // 非空且在 knownIndustries 里才算有效；非法值（含历史键 flexible）视为该级缺失，继续往下找。
  // 优先级：卡片行业 > URL ?industry= > sessionStorage['cpq:industry'] > meta.default_industry。
  // 全部无效 → defaultIndustry（行为与今天一致，不引入新默认）。
}
```

- 打开**已有会话/卡片**（历史记录、`cpq:openSession`、工艺回传卡片）时用 `card.industry`：
  卡片里的行业是该单据的权威记录，非空就压过 URL 与 storage（`cpq_wf._card_row()` 已把老卡片的
  空行业读成空串，此时自然回落到下一级）。
- **新建会话**时卡片行业为空，等价于 URL > storage > 默认。
- `startNewQuote()`（清空旧需求后重开空白单）**保留** `cpq:industry` —— 与技术工艺路径
  「记住上次选的行业」一致（那个记忆键 `cpq:tech:industry` 本来就在 localStorage 里）；其余
  需求/文件/项目名/客户键照旧清理。
- 下拉 `#industrySelect` 的 `onchange` 必须：更新 `CURRENT_INDUSTRY` → 写 `cpq:industry` →
  `refreshIndustryForms()`（④产品技术参数换源）→ 同步卡片（§2.4）。

### 2.4 卡片落库：行业不能丢

1. `wfSyncCard()` 请求体加 `industry: currentIndustry()`。
2. `cpq_suite_server.py` 的 `/wf/card/sync` 把 `d.get("industry", "")` 透传给 `cpq_wf.sync_card()`。
3. `cpq_wf.sync_card()` 语义**不变**：非空才归一化写入；留空表示「这次没有行业信息」，
   不拿默认值覆盖老卡片；老卡片读回空串（`_card_row()`）。
4. 打开已有卡片且卡片行业非空、与页面当前行业不同时：以卡片为准，并给一条可见提示
   （例如「本单行业：包装」），**不静默**。

### 2.5 生效面沿用既有口径

第 1 步必填门禁、④产品技术参数换源、候选匹配源继续统一走 `_industry_of()`
（`cpq_agent_server.py:1145`）这一套判定，本批**不新增第二套行业判定**，
也不改 `cpq_industries.DEFAULT_INDUSTRY`。

## 3. 红测映射（`tests/test_quote_home_industry_carryover_red.py`）

| 组 | 覆盖 |
| --- | --- |
| A | 首页携带：报价路径写 `cpq:industry`、URL 带 `industry=`、config/rule 不写、tech 路径不变 |
| B | 工作台优先级：`resolveInitialIndustry()` 存在且**是真纯函数**（用 `node` 实际执行 5 组输入）、下拉写 storage、新报价保留 `cpq:industry` |
| C | 卡片落库：`wfSyncCard` 带 industry、`/wf/card/sync` 透传、`cpq_wf.sync_card` 契约与老卡片空串语义 |
| D | 单一事实源：前端不得硬编码行业清单/标签、第 1 步上报 `currentIndustry()`、后端只用 `_industry_of()` |
| E | 非回归：技术工艺 `?industry=` 仍带、两个页面 `node --check` 仍通过、默认行业仍是 `semiconductor`、`flexible` 历史键仍落默认 |

## 4. 本批非目标

- 不改技术工艺侧的行业口径与 `tech-workbench.html` 流程；
- 不改默认行业（仍是半导体），不迁移/回填老卡片的行业数据；
- 不改 `/api/step1/match`、`/api/meta` 的既有参数语义；
- 不做「按行业自动切换助手」这类自动判定（`industry_hint` 仍只是软提示）。

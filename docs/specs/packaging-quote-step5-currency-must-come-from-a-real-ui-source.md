# 报价卡片第 5 步的完成门禁要一个「界面上根本没有的格子」：明细行币种没有来源，第 5 步永远确认不了、报价单也导不出来

血缘：`e2e-quote-session-and-completion-closure.md` §4（第 5 步门禁：数量、币种、单价均有效）、
§7.2（判定的唯一事实源在服务端 `quote_step_completion_gate`）、§7.3（`assertQuoteExportable()` 导出/导入前硬校验）、
`packaging-quote-close-loop.md`（包装卡片第 3–5 步的分区由 `cpq_packaging_quote.sections()` 定价后回填）。

状态：Spec + 红测（已实现）（原状：本批只写 Spec 与红测，业务实现不在本批；34 真跑现场见 §1）
红测：`tests/test_packaging_quote_step5_currency_must_come_from_a_real_ui_source_red.py`
本批 changelog 条目号：`## 445`

## 0. 一句话目标

第 5 步的完成条件要求**每一行明细都有币种**，但报价明细这张表的列定义里根本没有「币种」：
`GET /agents/quote/api/meta` 给 `s5_detail` 的 14 列没有它，`fillStep5()` 的默认值 `S5_DEFAULTS`
也没有它，`s5RecalcRow()` 只重算三个派生列。第 5 步唯一的币种落在**另一个分区**（报价基本信息
`s5_basic` 的表单字段，`fillStep5()` 自动填「人民币」）。于是：

- 用户在卡片上点「报价方案」把明细表填出来 → 点「确认，进入下一步」→ 服务端 409
  `invalid_currency`「第 1 行币种为空。」，修复入口写着「填写币种（如 人民币）。」——
  而**界面上没有那一格可以填**；点「强行填满本步骤」也只是把同一张 14 列表填满，币种还是空的；
- 第 6 步的「生成报价单(Word)」「导入数据库」用同一份判据（`assertQuoteExportable()`），
  同样报「第 1 行：币种为空。」。

也就是说：按前端按钮走的用户**永远走不出第 5 步**，报价单永远生不出来（这正是"零件拆出来之后
下游就走不动了"在报价侧的现场）。

## 1. 现场证据（34，2026-09-23，Codex 真跑；会话 `1bef04f7dab3`）

- 界面的列定义（`GET /agents/quote/api/meta?industry=packaging`，HTTP 200）：

```
s5_basic  fields : 报价单号 / 报价单名称 / 关联测算单号 / 报价形式 / 报价类型 / 报价模板 /
                   报价有效天数 / 是否用印 / 关联商机编号 / 【币种】/ 签约主体 / … / 报价说明
s5_detail columns: 产品系列 / 产品型号 / 成品编码 / 成品描述 / 版本扩展 / 方案描述 / 规格 /
                   数量 / 报价 / 折扣 / 折后价格 / 总金额 / 税率 / 税金
```

  「币种」只在 `s5_basic`（第 5 步的**另一个**分区），明细表的列里没有它。

- 页面源码（`确认需求解析结果.html`）三处口径合起来证明明细行永远不可能有币种：
  `S5_DEFAULTS = { '数量': '1', '折扣': '1', '税率': '0.13' }`（无币种）、
  `fillStep5()` 只把 `'币种': '人民币'` 写进 `s5_basic` 的自动值、行内只沿用它自己的 14 列
  （`(detailF.columns || []).forEach(...)`）。

- 真跑门禁（`POST /agents/quote/api/quote/step-gate`，头部 SM1）：

```
step_no=5, data.s5_detail.数据 = [14 列行：数量 1000 / 报价 27.37 / 折扣 1 / 税率 0.13 /
                                        折后价格 27.37 / 总金额 27370]
  → HTTP 409 {"code":"invalid_currency","message":"第 1 行币种为空。",
              "action":"填写币种（如 人民币）。","fixable_by_fill":true}
同一行**手工**加上 "币种":"人民币"（界面没有这一格，是人为构造的）
  → HTTP 200 {"ok":true,"message":"第 5 步可以完成。"}
```

- 同一个键在导出侧也是硬的：`assertQuoteExportable()` 逐行读 `row['币种']`，
  空就 `problems.push(tag + '：币种为空。')`；`exportDocx()`（`/api/export/docx`）与
  `importQuoteDb()`（`/api/import`）在动手之前都先过它。

- 这不是"包装特例"：列定义来自 DA 本体**全行业共用**（同一份 meta 给三个行业下发同样的 17 个分区），
  所以第 5 步的 `invalid_currency` 是每个行业都躲不过的。

## 2. 口径（可直接验收）

1. **币种来源单一化**：第 5 步的币种只有一个界面来源 —— 同一步「报价基本信息」（`s5_basic`）里的
   「币种」字段。判「这一行有没有币种」时，行里没有币种（没有这一列或值为空）**必须**按**同一步**
   `s5_basic` 的币种兜底；只有当行里和同一步的报价基本信息里都没有币种时才拦 `invalid_currency`。
2. **不许凭空补值**：兜底只读同一步的报价基本信息，不许跨步取（不许看第 1 步）、
   不许默认成「人民币」、不许把空串/空白当有值。`invalid_currency` 的 `message` / `action`
   文案逐字不变（只有在同一步的报价基本信息也没有币种时才会出现）。
3. **导出/导入同一判据**：`assertQuoteExportable()` 的币种判据与门禁同源 ——
   明细行没有币种时按**同一步报价基本信息**的币种判；两处都没有才报「第 N 行：币种为空。」。
   取数入口限定为页面已有的「第 5 步整步快照」`collectStepData(5)`（`s5_basic.数据.币种`），
   不新开第二条读 DOM 的路。数量/单价/复算三条判据与文案一个字不动。
4. **冻结面**：行级币种有值时仍然优先、逐字不改（第 1 步带进来的行、非包装行业、外部载荷都照旧）；
   `_row_number()` 的列名关键字口径、`_gate_rows()` 的分区形状、第 3/4/6 步的判据、
   `POST /api/quote/step-gate` 的路由与 200/409 语义都不动。

## 3. 允许修改范围

- `cpq_agent_server.py`：`quote_step_completion_gate()` 第 5 步那一支的币种判据（取同一步
  `s5_basic` 的币种兜底；建议抽成一个只读同一步快照的小函数，`_gate_rows` / `_row_number` 复用）。
- `确认需求解析结果.html`：`assertQuoteExportable()` 的币种判据（同上，取 `collectStepData(5)` 的
  `s5_basic` 兜底）。
- 禁止：往 `s5_detail` 的列定义里加「币种」（列定义来自 DA 本体，属于另一条口径）、
  改 `_gate_blocker()` 的文案、放宽数量/单价/复算判据、把空币种"默认成人民币"、
  改导出/导入的端点与路由。

## 4. 红测分组（`tests/test_packaging_quote_step5_currency_must_come_from_a_real_ui_source_red.py`）

- **A 组：门禁与导出必须认「同一步报价基本信息」的币种（今天都是红的）**
  - A1 14 列明细行（无币种）+ 同一步 `s5_basic.数据.币种='人民币'` → 第 5 步必须 `ok`；
  - A2 3 行明细（其中 1 行自带币种、2 行没有）+ 同一步报价基本信息有币种 → 必须 `ok`
    （行级优先、缺的按同一步兜底）；
  - A3 页面 `assertQuoteExportable()`：明细行没有币种、第 5 步报价基本信息有币种 → 必须 `ok`
    （今天报「第 1 行：币种为空。」）。
- **B 组：护栏（今天就是绿的，不许被改红）**
  - B1 行里自带币种（`'币种': '人民币'`）→ 仍然 `ok`；
  - B2 行里没有币种、**同一步报价基本信息也没有** → 仍然 `invalid_currency`，`message`/`action` 逐字不变；
  - B3 数量/单价/复算（`总金额 ≠ 折后价格 × 数量`）三条判据与文案逐字不变；
  - B4 第 3 步（正数基础成本）、第 4 步（可解释加价）、第 6 步（指纹未变 + 明细非空）判据不变；
  - B5 页面 `assertQuoteExportable()` 仍然拦「数量无效 / 单价无效 / 复算不上 / 明细为空」四类问题。

## 5. 验收

1. 34 上打开 `确认需求解析结果.html?session_id=1bef04f7dab3` 的第 5 步，点「报价方案」把明细表
   填出来，再点「确认，进入下一步」：不再 409，能进第 6 步；
2. 第 6 步「生成报价单(Word)」与「导入数据库」不再被「币种为空」挡住；
3. 把第 5 步报价基本信息的「币种」清空后，第 5 步重新被 409 挡住（口径没被删掉）。

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_step5_currency_must_come_from_a_real_ui_source_red -v
```

## 6. 红基（2026-09-23 实跑，未实现）

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_step5_currency_must_come_from_a_real_ui_source_red
  → Ran NN tests … FAILED (failures=N)
```

红的 = A1–A3；绿的护栏 = B1–B5。逐条失败点与数字见 changelog `## 445`。

## 7. 关联

- 同一轮还发现「服务端第 5 步快照的报价明细是 `kind:"summary"` 的 13 行汇总，门禁却判它『报价明细为空』」
  → 见 `packaging-quote-step5-reported-detail-must-satisfy-step-gate.md`（同一批 `## 445`）。
  两条一起修，第 5 步才真正走得通。

## 8. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 454`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 币种来源单一化 | `cpq_agent_server._gate_step5_currency()`（新）+ `quote_step_completion_gate()` 第 5 步 | 明细行没有币种（无该列或值为空）→ 按**同一步** `s5_basic` 的币种兜底；行级币种有值仍然优先。取币种只读同一步：`s5_basic.数据.币种`（页面 `collectStepData(5)` 的形状），服务端快照那种 `kind:'summary'` 的 `fields` 列表里 `label`/`key` 含「币种」的那条也认；两处都没有才拦 |
| §2.2 不许凭空补值 | 同上 | 兜底只读同一步（`data["s5_basic"]`），不跨步、不默认成「人民币」、`""`/空白都不算有值；`invalid_currency` 的 `message`「第 N 行币种为空。」与 `action`「填写币种（如 人民币）。」逐字未改 |
| §2.3 导出侧同源 | `确认需求解析结果.html:assertQuoteExportable()` | 币种判据内联同一步兜底（`collectStepData(5)` → `s5_basic` → `数据.币种`／`fields` 里 label 含「币种」的那条）；读不到就当没有。数量/单价/复算三条判据与文案一个字未动 |
| §2.4 冻结面 | 未动 | 行级币种优先、`_row_number()` 的关键字口径、`_gate_rows()` 的分区形状、第 3/4/6 步判据、`POST /api/quote/step-gate` 的路由与 200/409 语义、`s5_detail` 的列定义（不加「币种」）都没改 |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_quote_step5_currency_must_come_from_a_real_ui_source_red
Ran 8 tests ... OK                     # 红基 3 红（A1–A3）/ 5 绿护栏（B1–B5）

./open-claude/.venv/bin/python -W ignore -m unittest tests.test_e2e_quote_session_completion_red \
    tests.test_packaging_quote_close_loop_red tests.test_packaging_parts_in_card_and_material_fill_red
Ran 116 tests ... OK                   # 报价会话/闭环/卡片 零回归
```

红测自身缺陷：无（A 组 3 条用进程内 import 直调纯函数，A3 用 `node -e` 抽 `assertQuoteExportable()` +
`_numOf()` 执行；两条都想通过"界面真有的那个格子"来满足，不是放宽判据）。

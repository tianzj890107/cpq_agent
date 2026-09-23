# 服务端自己写进第 5 步的那份报价明细，第 5/6 步的完成门禁却判它「为空」：两种形状互不承认，用户被指去回第 4 步

血缘：`packaging-quote-close-loop.md`（包装报价四段分区由 `cpq_packaging_quote.sections()` 定价后产出，
第 5 步的 `s5_detail` 是定价引擎的**汇总**）、`e2e-quote-session-and-completion-closure.md` §4
（第 5 步要「明细非空 + 数量/币种/单价有效 + 总金额可复算」、第 6 步要「明细非空且指纹未变」）、§5
（"无依据时应保留当前步骤并给出修复入口"）。

状态：Spec + 红测（已实现）（原状：本批只写 Spec 与红测，业务实现不在本批；34 真跑现场见 §1）
红测：`tests/test_packaging_quote_step5_reported_detail_must_satisfy_step_gate_red.py`
本批 changelog 条目号：`## 445`

## 0. 一句话目标

第 5 步的报价明细在服务端有两种形状：**定价引擎的汇总**（`{"kind":"summary","rows":[{项目,值,来源}]}`，
包装卡片上肉眼可见 13 行）和**页面/模型填的明细表**（`{"标题":…,"数据":[14 列行]}`）。
`quote_step_completion_gate()` 只认后者（`_gate_rows()` 只读 `数据`/`data` 是数组的那种，
否则返回空列表），于是：

- 打开第 5 步（或跨步骤接手卡片）时页面发给门禁的载荷里那份 13 行明细**读不出来** →
  409 `no_detail_rows`「报价明细为空：至少要有 1 行。」，修复入口写着「回到第 4 步确认产品行后，
  第 5 步会自动生成报价明细。」——第 4 步早就确认过了，回第 4 步什么也不会发生；
- 同一份判定 `fixable_by_fill:false` → 点「强行填满本步骤」被当场拒绝（"填也推不动"），
  用户在第 5 步**没有任何按钮可点**；
- 第 6 步拿同一份载荷判「明细非空」，同样 409，报价单也生不出来；
- 卡片侧 `wfRestoreStepData()` 对没有 `数据`/`data` 的分区快照直接 `return`，
  这两种汇总分区（`s5_basic` / `s5_detail`）**整段被丢掉**，用户连那 13 行都看不到。

## 1. 现场证据（34，2026-09-23，Codex 真跑；会话 `1bef04f7dab3`）

- `GET /wf/card/step-data?session_id=1bef04f7dab3&step_no=5`（HTTP 200）→

```json
{"s5_basic": {"kind": "summary", "title": "报价基本信息", "fields": [...]},
 "s5_detail": {"kind": "summary", "title": "报价明细", "rows": [
   {"项目": "成本总额", "值": "20.53", "来源": "定价引擎"},
   {"项目": "毛利后单价", "值": "27.37", "来源": "定价引擎"},
   {"项目": "加价合计", "值": "1.15", "来源": "定价引擎"},
   {"项目": "加价后单价", "值": "28.52", "来源": "定价引擎"},
   {"项目": "折扣金额", "值": "1.43", "来源": "定价引擎"},
   {"项目": "未税单价", "值": "27.10", "来源": "定价引擎"},
   {"项目": "税金", "值": "3.52", "来源": "定价引擎"},
   {"项目": "含税单价", "值": "30.62", "来源": "定价引擎"},
   {"项目": "未税单价（定价）", "值": "27.37", "来源": "定价引擎"},
   {"项目": "未税总额", "值": "27095.08", "来源": "定价引擎"},
   {"项目": "含税总额", "值": "30617.45", "来源": "定价引擎"},
   {"项目": "税金", "值": "3.52", "公式": "未税单价 × 税率"},
   {"项目": "税率", "值": "0.13", "来源": "Spec §2.4"}]}}
```

  同一份 meta 把 `s5_detail` 注册成 `table`（"报价明细"），服务端快照却写 `kind:"summary"`。

- 真跑门禁（`POST /agents/quote/api/quote/step-gate`，头部 SM1）：

```
A) step_no=5, data = 上面那份快照原样打回去
   → HTTP 409 {"code":"no_detail_rows","message":"报价明细为空：至少要有 1 行。",
               "action":"回到第 4 步确认产品行后，第 5 步会自动生成报价明细。",
               "fixable_by_fill":false}
B) step_no=5, data = {}（页面把汇总分区丢掉之后）→ 同上，一字不差
C) step_no=6, data = 同一份快照 + 指纹
   → HTTP 409 {"code":"no_detail_rows","message":"报价明细为空，不能生成报价单。"}
D) step_no=5, data.s5_detail = {"标题":"报价明细","数据":[13 行 {项目,值,来源}]}（页面把汇总渲染进表信封）
   → HTTP 409 {"code":"invalid_quantity","message":"第 1 行数量无效。"}
```

  也就是说：**这份明细不管以哪种形状到达门禁，都会被判成"没有明细"或"明细行不合格"**。

- 卡片侧（`确认需求解析结果.html` `wfRestoreStepData()`）：
  `const data = payload['数据'] !== undefined ? payload['数据'] : payload.data;`
  → `if (data === undefined || data === null) return;` —— 汇总形状没有 `数据`/`data`，
  两个分区都被这一行 `return` 静默丢掉；第 6 步零件表那条"未知节 `kind==='table'`"的兜底
  （`packaging-parts-in-card-and-material-fill.md` §2.1）不覆盖 `kind:'summary'`。

## 2. 口径（可直接验收）

1. **两种形状都要认**：第 5 步的明细可能是①明细表（行里只有业务列）或②定价引擎汇总
   （`kind:"summary"` 的 `rows`，或表信封里带「项目」键的行）。判「这一步有没有明细」时两种都算有：
   - 载荷里**存在表行**（不带「项目」键、带 数量/报价 这类业务列的行）→ 表行按现有逐行判据判
     （数量 / 币种 / 单价 / 总金额可复算，币种按
     `packaging-quote-step5-currency-must-come-from-a-real-ui-source.md` §2.1 的同一步兜底）；
     同一载荷里的汇总行**不当表行**（不参与逐行判据）；
   - 载荷里**没有任何表行、只有汇总行** → 至少 1 行、每行「项目」非空且「值」非空即算
     「明细非空」；数量 / 单价 / 逐行复算这些判据对汇总形状不适用（汇总里没有这两个口径），
     币种也不在明细行上判。
2. **真为空才说为空**：两种形状都取不到行时，才回 `no_detail_rows`。
3. **修复入口必须走得通**：`no_detail_rows` 的 `action` 必须指向**第 5 步自己的入口**
   （文案含「报价方案」或「重算明细」），不许只说「回到第 4 步确认产品行后，第 5 步会自动生成报价明细。」
   —— 这条链路上第 4 步早就确认过了，回第 4 步什么也不会发生。
4. **`fixable_by_fill` 不许扣在这份快照上**：第 5 步自己有可填/可算的入口（「报价方案」按钮
   就是把明细生出来的那个动作），所以第 5 步的 `no_detail_rows` 不许报 `fixable_by_fill:false`
   —— 今天正是它让「强行填满本步骤」被当场拒绝、用户一个按钮都点不了。
5. **卡片必须把它渲染出来**：`wfRestoreStepData()` 不许把 `kind:"summary"` 的分区快照整段丢掉；
   第 5 步的报价明细（和报价基本信息）要能只读上屏。
6. **冻结面**：表形状的逐行判据与文案、第 6 步的指纹判据（`detail_changed_after_step5_confirm`）、
   第 3/4 步的判据、`cpq_packaging_quote.sections()` 的输出形状、
   `packaging_parts` 那条"未知节 `kind==='table'`"的渲染兜底，都不动。

## 3. 允许修改范围

- `cpq_agent_server.py`：`quote_step_completion_gate()` 第 5/6 步读明细的那一段
  （`_gate_rows()` 旁边加一个只读汇总形状的取行口径：`kind:"summary"` 的 `rows`、
  以及表信封里带「项目」键的行；`no_detail_rows` 的 `action` 文案；
  第 5 步 `fixable_by_fill` 的判据）。
- `确认需求解析结果.html`：`wfRestoreStepData()` 里对汇总形状分区快照的渲染（只读表即可）。
- 禁止：改 `cpq_packaging_quote.sections()` 的输出形状、把汇总行当成表行去套数量/单价/复算、
  放宽第 6 步指纹判据、往 `no_detail_rows` 里加第二套文案、动 `_gate_blocker()` 的字段集合。

## 4. 红测分组（`tests/test_packaging_quote_step5_reported_detail_must_satisfy_step_gate_red.py`）

- **A 组（今天都是红的）**
  - A1 34 那份第 5 步快照原样打给门禁 → 第 5 步必须 `ok`（今天 `no_detail_rows`）；
  - A2 `data = {}` → 仍是 `no_detail_rows`，但 `fixable_by_fill` 必须是 `true`（今天 `false`）；
  - A3 `data = {}` → `action` 必须指向第 5 步自己的入口（今天只说回第 4 步）；
  - A4 34 那份快照 + 指纹打给第 6 步 → 必须 `ok`（今天 `no_detail_rows`）；
  - A5 表信封里全是「项目/值」汇总行 → 必须 `ok`（今天 `invalid_quantity`）；
  - A6 卡片 `wfRestoreStepData()` 不许把带 `rows` 的汇总分区快照整段丢掉（今天直接 `return`）。
- **B 组：护栏（今天就是绿的，不许被改红）**
  - B1 表信封里是**空数组** → 仍然 `no_detail_rows`；
  - B2 表形状 + 行级币种 → 仍然 `ok`；
  - B3 表形状 + 行里没有币种、同一步报价基本信息也没有 → 仍然 `invalid_currency`；
  - B4 第 6 步指纹变了（`detail_changed_after_step5_confirm`）仍然拦；
  - B5 汇总行里「项目」为空 → 不算明细（仍然不 `ok`）；
  - B6 第 3 步（正数基础成本）、第 4 步（可解释加价）判据不变。

## 5. 验收

1. 34 上打开 `确认需求解析结果.html?session_id=1bef04f7dab3` 第 5 步：那 13 行报价明细在卡片上
   看得见，点「确认，进入下一步」不再报「报价明细为空」；
2. 第 5 步的明细真的空的时候，提示的修复入口指向「报价方案」（点它就能把明细生出来），
   且「强行填满本步骤」不再被 `fixable_by_fill:false` 当场拒绝；
3. 第 6 步不再因为同一份快照被判「明细为空」。

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_step5_reported_detail_must_satisfy_step_gate_red -v
```

## 6. 红基（2026-09-23 实跑，未实现）

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_step5_reported_detail_must_satisfy_step_gate_red
  → Ran NN tests … FAILED (failures=N)
```

红的 = A1–A6；绿的护栏 = B1–B6。逐条失败点与数字见 changelog `## 445`。

## 7. 关联

- 同批的 `packaging-quote-step5-currency-must-come-from-a-real-ui-source.md`（`## 445`）：
  第 5 步的币种没有界面来源。两条一起修，第 5 步才走得通。

## 8. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 454`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 两种形状都认 | `cpq_agent_server._gate_detail_rows()` + `_gate_summary_rows_are_detail()`（新） | 一个分区的明细行按**有没有「项目」列**分成表行与汇总行：有表行 → 只对表行套逐行判据（汇总行不当表行）；没有表行、只有汇总行（`kind:'summary'` 的 `rows`，或表信封里带「项目」的行）→ 至少 1 行且每行「项目」「值」都非空即算「明细非空」，数量/单价/逐行复算与币种都不对汇总形状判 |
| §2.2 真为空才说为空 | `quote_step_completion_gate()` 第 5/6 步 | 两种形状都取不到可用行才回 `no_detail_rows`；第 6 步「明细非空」用同一个 `has_detail` |
| §2.3 修复入口走得通 | 第 5 步的 `no_detail_rows.action` | 改成「点「报价方案」重算明细（或点「强行填满本步骤」），明细由定价引擎按第 4 步的产品行生成；也可以点「重算明细」再试。」——含「报价方案」与「重算明细」两个第 5 步入口 |
| §2.4 `fixable_by_fill` 不许扣 | 同上 | 第 5 步的 `no_detail_rows` 走 `_gate_blocker()` 缺省（`fixable_by_fill=True`），不再由 `bool(products)` 决定；顶层 `fixable_by_fill` 随之回 `true` |
| §2.5 卡片渲染出来 | `确认需求解析结果.html:wfRestoreStepData()` + `summarySectionRows()`（新） | 已知节与未知节都新增一条：`数据`/`data` 缺失但带 `rows`/`fields` 的 `kind:'summary'` 分区快照，按只读表渲染（`SUMMARY_ROWS_COLUMNS` = 项目/值/来源/公式，`SUMMARY_FIELDS_COLUMNS` = 字段/值），不再整段 `return` 丢掉 |
| §2.6 冻结面 | 未动 | 表形状的逐行判据与文案、第 6 步指纹判据、第 3/4 步判据、`cpq_packaging_quote.sections()` 输出、`packaging_parts` 那条 `kind==='table'` 的通用渲染兜底，一行未改 |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_quote_step5_reported_detail_must_satisfy_step_gate_red
Ran 12 tests ... OK                    # 红基 6 红（A1–A6）/ 6 绿护栏（B1–B6）

./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_quote_step5_currency_must_come_from_a_real_ui_source_red \
    tests.test_packaging_parts_in_card_and_material_fill_red
Ran 28 tests ... OK                    # 同批两条一起修（B3 依赖币种那一条的兜底顺序）
```

红测自身缺陷：无。两条 Spec 同批落地：`_gate_detail_rows()` 只把**没有「项目」列**的行当表行，
所以 B3（表形状又没有币种）先落进逐行判据、再走同一步币种兜底 —— 顺序与两份 Spec 的 §2 一致。

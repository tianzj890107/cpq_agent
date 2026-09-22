# 解析到下游三处未闭合的缝：人工字段被降级 / 配对复核读不到 / 放行留痕门禁不认

状态：Spec + 红测（已实现）（见 changelog ## 262 / ## 274）
红测：`tests/test_packaging_parse_to_downstream_seams_red.py`
实测：`tests/test_packaging_parse_to_downstream_seams_red.py` `Ran 13 OK`（B1 / B2 / B3 / C1 由红转绿，A 组 5 条与 C2 / C3 / C4 八条护栏保持绿）。

血缘：承接 `packaging-downstream-blockers-close-loop.md`（§1.3 人工来源判定、§1.4 配对披露、
§3.1 放行留痕过桥）、`packaging-manual-field-confirmation.md`（门禁判据 + 人工确认通道）、
`packaging-dwg-parts-extraction.md`（C7 BOM 回填）、`packaging-parametric-bom.md`（BOM 读接口）、
`dwg-semantics-agent-flow.md`（门禁矩阵）。

本批解决**"图解析成功、零件也出来了、回传也成功了，但下游被自己的解析结论挡住、复核结论谁都看不到、
门禁还在说不行"**的三处缝。三处都有 34 上的真跑证据（见 §1）。本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

把已经算出来的三件事**如实送到人和门禁能看见的地方**：

1. **人工已经填好的字段不许被解析降级成"未确认"**（否则下游每一段都被自己的解析结论挡住）；
2. **零件↔BOM 行的配对不一致项**要能从 BOM 读接口读到；
3. **按留痕放行的缺口**要能在门禁读接口上认出来。

三条都是**加法 / 口径修正**：**不放宽任何既有拒绝口径**，也不新增接口。

## 1. 现状缺口（34 实测，逐条可复现）

真跑现场：项目 `559892f033b9`（由报价卡片 `fullchain-a05627f2` 进入，`酒盒.dwg`，
ODA 27.1 → DXF，`flow-f8a49b459cf3abd4`）。八步全 `completed`，零件 64 件，BOM 33 行，
工艺路线 12 步已确认，成本 `total_cost=6.577`、`has_gaps=True`，
`POST .../requirement/packaging-quote/send` → **200**，报价卡片推进到 `handoff_pending`。

### 1.0 先说清楚：本次真跑被什么挡住过

| # | 现象 | 性质 | 本批是否处理 |
| --- | --- | --- | --- |
| 1 | `field_write` = `blocked / REQUIREMENT_DRAFT_MISSING` | 调用顺序（没先建需求草稿），不是产品缺陷 | 否 |
| 2 | `PUT /requirement` → 403「客户信用等级仅可由销售经理首次录入」 | 设计如此（`requirement_service` 销售主数据门） | 否 |
| 3 | 解析成功后 7 个字段全 `field_unconfirmed`，下游 5 段全 blocked | **真缺陷** | **本批 §3.1**（与 `packaging-manual-field-confirmation.md` 同一根因、互补口径） |
| 4 | `cost_gaps_unresolved` 让 `quote_publish` 恒 blocked，但放行后回传已经成功 | **真缺陷** | **本批 §1.2** |
| 5 | 配对不一致项算出来了，但没有任何读接口能看到 | **真缺陷** | **本批 §1.1** |

第 3 条已经有 `docs/specs/packaging-manual-field-confirmation.md` + 红测
`tests/test_packaging_manual_field_confirmation_red.py`（本机复跑：**Ran 13，failures=7**，仍未实现），
本批**不重复**；证据留在 §1.3 供追溯。

### 1.1 配对不一致项只落在 `bind_rows()` 的返回值里（P1）

同一项目、同一次真跑。BOM 33 行，其中 4 行走 `source: dwg_parts`：

```
RB02001-P02 盖壁（长边）  灰板 2.0mm       len 443.523  wid 492.62   material_match=null
RB02001-P03 盖壁（短边）  灰板 2.0mm       len 440.123  wid 482.92   material_match=true
RB02001-P08 磁铁         钕铁硼 Ø10×2mm    len 440.123  wid 482.92   material_match=false  ← 物理上不可能
RB02001-P09 面纸（整体）  特种纸 200g      len 398.024  wid 446.32   material_match=null
```

`packaging-downstream-blockers-close-loop.md` §1.4 记录的现场（磁铁被绑到纸面板）**一模一样地复现了**。
那一批要求的是**披露**：逐行 `pairing_basis` / `material_match`，并"在**报告**里单列不一致项"（§3.4）。

现状是披露只走了一半：

- `packaging_parts.bind_rows()` **确实**返回 `pairing_review`
  （`tech_app/backend/services/packaging_parts.py:1042-1049/1060`；冻结红测
  `tests/test_packaging_downstream_blockers_red.py` 的 `pairing_review` 四条守）；
- 但 `packaging_bom._bind_parts()` 只取 `items`：

  ```python
  return list(packaging_parts.bind_rows(items, doc).get("items") or items)
  ```

  `pairing_review` 在这里被**丢掉**，`build_bom()` 组装文档时自然也没有这个键；
- 34 实测 `GET /api/projects/559892f033b9/requirement/packaging-bom` 的 BOM 顶层键：

  ```
  ['box_type_code', 'built', 'engine_version', 'gaps', 'generated_at', 'items',
   'requirement_no', 'source_versions', 'stats']
  ```

  `pairing_review` 不在其中；`gaps` 只有 `needs_input` / `missing_variables` / `material_unresolved`。
- 全仓前端（`tech_app/frontend/*.js`）里 `pairing_review` **0 处引用**——因为这个键根本到不了前端。

后果：想看出"磁铁被配到纸面板上"，只能去 `items[].size_source_json`（一个**字符串**）里
`JSON.parse` 出来再看。**"单列不一致项"落成了一次函数返回值，没落到任何能被人看到的地方。**

### 1.2 门禁不认放行留痕 → 放行成功后仍报"成本仍存在缺口"（P1）

同一次真跑，同一个项目，同一时刻的两份读数：

```
GET /api/projects/559892f033b9/requirement/packaging-quote
→ {"built": true, "handoff": {"handoff_no": "pkghandoff:559892f033b9:REQ-559892F033B9:default:1",
   "has_gaps": true,
   "gap_waiver_json": "{\"by\": \"PE1\", \"at\": \"2026-09-22 00:26:16\",
                        \"reason\": \"全流程演示：已知缺口按演示口径放行，待语义层实现后收敛\",
                        \"codes\": [\"material_gsm_missing\", \"material_price_missing\", ...]}"}}

GET /api/projects/559892f033b9/drawing-flow?stage=quote_publish
→ status: blocked
   - field_unconfirmed | inner_length   | 内长尚未确认，确认后才能进行该步骤     （§1.3，另有 Spec）
   - field_unconfirmed | inner_width    | 内宽尚未确认，…                      （§1.3，另有 Spec）
   - field_unconfirmed | inner_height   | 内高尚未确认，…                      （§1.3，另有 Spec）
   - field_unconfirmed | closure_type   | 闭合方式尚未确认，…                   （§1.3，另有 Spec）
   - cost_gaps_unresolved |            | 成本仍存在缺口，缺口清零后才能生成正式报价   ← 本批
```

代码事实（`packaging_drawing_flow/gates.py:126-136`）：`quote_publish` 只看
`packaging_cost.load_cost(pid).has_gaps`，**完全不看** `packaging_handoff` 的交接记录／`gap_waiver`。
于是"财务/工艺写明原因放行"这条官方路径**走完之后，门禁读接口没有任何变化**：
界面上仍然只有一句"成本仍存在缺口，缺口清零后才能生成正式报价"，看不出"已经按留痕放行过、
放行人和原因是谁"。而报价卡片那边已经推进到 `handoff_pending`——**两边对同一件事说法相反**。

> 与 `packaging-downstream-blockers-close-loop.md` §3.1 的关系：那一批合的是
> **技术侧留痕 → 过桥 → 报价侧那道门**；本批合的是**同一条留痕 → 门禁读接口**。
> 既有 `dwg-semantics-agent-flow.md` 的门禁矩阵（「有缺口 → `cost_gaps_unresolved`」）**不改**：
> 本批只要求把"这条缺口已被谁按什么原因放行"**披露出来**，`blocking` 里那条**保留**。

### 1.3 人工字段被解析降级成"未确认"（P0）

同一项目、同一次真跑：`data` 里 7 个字段值都在、`field_sources` 全是 `manual`，
但 `field_provenance` 被写成 `status=missing / origin=missing`，于是
`gates` 的 `box_match / bom / route / cost / quote_publish` 全部 `blocked`
（第一条读数：`quote_publish blocked ["field_unconfirmed" ×4, "cost_gaps_unresolved"]`）。

根因（`packaging_semantics/provenance.py:110-127`）：人工已确认那条分支里

```python
entry = dict(previous) if isinstance(previous, dict) and previous else _entry_snapshot(candidate)
...
entry.setdefault("origin", "user_confirmed")
entry.setdefault("status", "confirmed")
```

`previous` 是**上一版** `field_provenance[key]`。用户第一次填需求时表单里没有 `field_provenance`
（前端 `.js` 全仓 0 处引用这个键），所以 `previous is None` → `entry` 变成**语义候选的快照**；
DWG 里读不到 `inner_length` 这类字段时候选 `status` 就是 `missing` / `needs_confirmation`，
`setdefault` 对**已存在的键**不生效 → "人工已填且有值"这个事实被候选的 `missing` 覆盖。

> 与 `packaging-manual-field-confirmation.md` 的分工：那一份定的是**门禁判据 + 人工确认通道**
> （谁把字段标成 confirmed、门禁凭什么认）；本批 §3.1 定的是**解析侧不许把人工事实降级**。
> 同一根因、互补口径，`packaging-quote-draft-and-card-visibility.md` §3.3 已明确把
> "门禁转绿"的断言委托给本批 **A5**，所以两条都要在。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_semantics/provenance.py`
   - 走「人工已确认、只追加 alternatives」这条分支时，`field_provenance[key]` 必须以**人工事实**为准：
     明确写入 `origin="user_confirmed"`、`status="confirmed"`、`value=<当前 data 里的值>`
     （不再用 `setdefault` 去改一份已经带 status 的候选快照）；候选的 `status` / `origin`
     **只进 alternatives**，不许覆盖顶层这两个键。
   - 只在这一分支内改。`_is_user_confirmed()` 的判定条件（人工来源 **且** 当前值非空）逐字不变；
     `field_sources` 的写入语义逐字不变；**没有任何人工来源的字段**（`field_sources` 为空）
     必须一字不变——候选 `missing` 就还是 `missing`，不许被这条改动"顺手"标成 confirmed。
2. `tech_app/backend/services/packaging_bom.py`
   - `_bind_parts()` 除了 `items`，还要把 `bind_rows()` 返回的 `pairing_review` 交回 `build_bom()`；
   - `build_bom()` 组装的 BOM 文档新增 `pairing_review`（数组；无不一致时给 `[]`，**键必须存在**）；
   - `load_bom()` 读回路径必须带着它。
   - 存哪由实现方定：可以复用既有 meta 文档通道（`meta_backend.get_doc/put_doc`，
     与 `packaging_semantics` / `packaging_parts` 同一范式），**不改数据库 schema、不加表**。
   - **`items` / `bound` / `gaps` / `stats` 的口径逐字不变**（冻结红测
     `tests/test_packaging_parts_extraction_red.py` E 组、`tests/test_packaging_parametric_bom_red.py` 57 条守）。
3. `tech_app/backend/services/packaging_drawing_flow/gates.py`
   - `_stage_entry()` 在 `stage == "quote_publish"` 且 `cost.has_gaps` 时，
     读一次 `packaging_handoff.load_handoff(project_id, requirement_no)`（与既有
     `packaging_match` / `packaging_bom` / `packaging_route` / `packaging_cost` 同一套 `resolve()` 机制，
     不新增 import 耦合）；
   - `gap_waiver_json` 是**合法**放行留痕（`by` / `at` / `reason` 非空，`codes` 覆盖当前缺口码）时：
     - `cost_gaps_unresolved` 那条 **保留**在 `blocking` 里，并新增 `waived: true`、
       `waiver: {by, at, reason, codes}`；
     - entry 顶层新增 `waiver`（同一个摘要对象）。
   - **不许**把 `cost_gaps_unresolved` 从 `blocking` 里删掉；**不许**在 `has_gaps` 为
     `False`、或留痕不合法（缺 `by`/`at`/`reason`、`codes` 没覆盖缺口）时给出任何 `waiver`。

### 2.4 实现记录（`## 274`）：落盘位置与一处**补强**的留痕判据

- **§3.2 落盘位置**：按本节授权的 meta 文档通道落一份 `packaging_bom_pairing`
  （`{"by_requirement": {需求单: [不一致项…]}}`），`build_bom()` 写完行之后写它、
  `load_bom()` 读它；`items` / `bound` / `gaps` / `stats` 一个字节没动，也没碰数据库 schema。
- **§3.3 留痕判据补强**：本节列的四条是「`by` / `at` / `reason` 非空 + `codes` 覆盖当前缺口码」。
  实现时发现"覆盖"这条**在拿不到当前缺口码时无从校验**（成本记录里没有 `gaps`、交接记录里没有
  `gap_codes` 时，任何非空 `codes` 都会被判成"覆盖"）——那等于"无从校验即视同已放行"，方向正好错。
  因此本版把判据收紧成四条：① 交接记录自证这次交接**带着缺口**（`has_gaps`）；② `by` / `at` / `reason`
  非空；③ `codes` 非空且每项有效；④ **读得到**的当前缺口码必须被 `codes` 全覆盖。
  这条收紧在生产口径上**没有副作用**：`_guard_gaps()` 只在包真的带缺口时才产生留痕，所以
  「有留痕 ⟹ `has_gaps=True`」恒成立；它只是不让"看不见缺口码"变成默认放行。
  `blocking` 里的 `cost_gaps_unresolved` 依旧一条不少（C4 护栏）。

## 3. 口径（逐条验收）

### 3.1 人工字段不许被解析降级

- **A1**：人工来源（`field_sources[key] == "manual"`）+ 当前值非空 + 语义候选 `status="missing"`
  → 写完后 `field_provenance[key].status == "confirmed"`、`.origin == "user_confirmed"`、
  `.value` 等于当前值；`data[key]` 与 `field_sources[key]` 逐字不变。
- **A2**：同上，但候选是 `status="needs_confirmation"` → 结论与 A1 相同。
- **A3**：候选的证据必须仍然进 `alternatives`（既有披露不许丢）。
- **A4**（护栏）：`field_sources` 为空（没人填过）时，候选 `missing` 必须还是
  `provenance.status="missing"`，不许被标成 confirmed；值为空时也仍然是 `missing`。
- **A5**（门禁联动）：`apply_to_requirement()` 跑完之后，用真 `packaging_drawing_flow.gates.build()`
  读，人工已填的字段必须让 `box_match` / `bom` / `route` / `cost` **都不再报 `field_unconfirmed`**。
  （`packaging-quote-draft-and-card-visibility.md` §3.3 把这条断言委托给本批，**不许删**。）

### 3.2 配对复核必须可读

- **B1**：有 `dwg_parts` 绑定且出现材料不一致时，`build_bom()` 产出的 BOM 文档必须有
  `pairing_review`：数组，元素键与 `bind_rows()` 返回值**一致**
  （`item_key` / `part_code` / `row_material` / `part_material` / `material_match`）。
- **B2**：不存在不一致时 `pairing_review == []`（**键存在**，不许 `None` / 省略）。
- **B3**：`pairing_review` 里每条 `material_match` 必须是 `False`（它是"不一致项"清单）。
- **B4**：`load_bom()` 读回同样带 `pairing_review`（不是只在 `build_bom()` 的返回值里）。
- **B5**（护栏）：`stats` 的键集与 `bound` 结论与改动前逐字相同。

### 3.3 放行留痕必须能在门禁上认出来

- **C1**：`cost.has_gaps=True` 且存在合法放行留痕时，`quote_publish` 的 entry 必须带
  `waiver`（`by` / `at` / `reason` / `codes` 与落库那份一致），且 `blocking` 里
  `cost_gaps_unresolved` 那条带 `waived=True` 并附同一份 `waiver`。
- **C2**（护栏）：`has_gaps=False` 时不许出现 `waiver` / `waived`（没缺口就没有放行这回事）。
- **C3**（护栏）：有交接记录但**没有**合法留痕（`gap_waiver_json` 空 / 缺 `by`/`at`/`reason` /
  `codes` 没覆盖当前缺口）时，不许 `waived=True`。
- **C4**（护栏）：`blocking` 里的 `cost_gaps_unresolved` **一条都不能少**——口径是披露，不是放宽；
  `dwg-semantics-agent-flow.md` 的门禁矩阵不动。

## 4. 非目标

- 不改配对规则（"行顺序 ↔ 面积降序"仍是现状），不把不一致变成拒绝——那要业务先签字
  （`packaging-downstream-blockers-close-loop.md` §1.4 已定）。
- 不改 `gates.py` 的判定条件与稳定码闭集，只加披露字段；不改 `quote_publish` 的 `blocked` 结论。
- 不动前端、不动成本公式与费率、不动知识库；不新增"人工确认字段"的接口或第二套判据
  （那条通道归 `packaging-manual-field-confirmation.md`，本批只保证解析侧不把人工事实降级）。

## 5. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parse_to_downstream_seams_red -v  # 13 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_downstream_blockers_red -v         # 20 OK 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red -v            # 32 OK 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red -v              # 57 OK 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red -v                # 54 OK (skipped=1) 不回归
```

真样本复验（本机有 `酒盒.dwg` 时）：跑完之后
`GET .../requirement/packaging-bom` 的顶层键里必须有 `pairing_review`；
`GET .../drawing-flow?stage=quote_publish` 在已放行时 `blocking` 里那条
`cost_gaps_unresolved` 必须带 `waived=True`。

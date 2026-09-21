# DWG 全流程下游剩余三处缝：放行留痕过桥 / 需求退回草稿 / 人工来源空值

血缘：承接 `packaging-quote-close-loop.md`（第 8 批交接与定价）、`packaging-dwg-parts-extraction.md`（C7
BOM 回填）、`packaging-product-outline-and-die-layer-roles.md`（第 4b 批语义）、`drawing-flow-non-editable-requirement.md`。

本批解决**"每一步单独看都是通的，接起来最后一步 500 / 中途永久卡住"**的三处缝。三处都有线上实测证据，
不是推断（证据见 §1）。本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

把图纸零件的下游链路从「八步能跑完」补到「**带缺口的包也能按留痕放行送到报价**、**需求被批准后仍有一条
合法的退回路径**、**人工来源但值为空时图纸证据能补上**」，并且**三处都不放宽既有拒绝口径**。

## 1. 现状缺口（实测证据，逐条可复现）

### 1.1 报价侧桥读不到放行留痕 → 最后一步 500（P0）

34 上真跑（项目 `cbef817fb1da`，酒盒.dwg，全新项目，未改代码）：

```
POST /api/projects/cbef817fb1da/requirement/packaging-quote/send
  （PE1，allow_gaps=True，reason=已写明）
→ 500，异常落在 cpq_tech_bridge._guard_packaging_result（cpq_tech_bridge.py:1076）
```

代码事实：

- `tech_app/backend/services/packaging_handoff.py:309 _guard_gaps()` **已经**接受
  `allow_gaps=True + reason`，并把 `gap_waiver = {by, at, reason, codes}` 写进
  `wip_packaging_handoff.gap_waiver_json`（冻结红测 `test_packaging_quote_close_loop_red` C5 守）；
- 但同一函数把 `bridge_result(package)`（**不含 waiver**）交给 `cpq_bridge.send_to_quote`；
- `cpq_tech_bridge._guard_packaging_result()` 只看 `cost.has_gaps or gaps`，**看不见任何留痕**，
  于是"财务写明原因放行"这条官方路径在报价侧必然 500。

也就是说：**技术侧留了痕，报价侧那道门不认**。这不是权限问题，是缝没合上。

### 1.2 需求被批准后没有合法的退回路径（P0）

`requirement_service.return_requirement_to_draft()` 只接受 `pending_confirmation`
（`requirement_service.py:418`）；`save_requirement_draft()` 的可编辑闭集只有
`EDITABLE_STATUSES = ("draft", "rejected")`（`requirement_service.py:29/151`）。

后果链（可复现）：

1. 需求先被批准（`status=approved`）；
2. 图纸到了，跑一键解析 → `packaging_drawing_flow.preconditions()` 如实报
   `REQUIREMENT_NOT_EDITABLE`（`__init__.py:483`），文案是「请先退回草稿或为该项目新建一张需求草稿」；
3. 用户去点「退回草稿」→ **409「当前需求不在待确认状态」**；去 PUT 需求 → **409 不可编辑**。

于是 `field_write` 这一步**永久 blocked**，而系统给的 action 里有一半是做不到的。
（画板侧 `packaging_drawing_flow` 的前置条件判定本身是对的，**不改**；缺的是它指向的那条路真的存在。）

### 1.3 人工来源但值为空 → 图纸证据永远补不上（P0）

`packaging_semantics/provenance.py:57 _is_user_confirmed(previous, source)` 只要
`field_sources[field] == "manual"` 就判 True，于是走"只追加 alternatives、不改值"的分支（:86-97）。

实测后果：`data.closure_type = ""` 而 `field_provenance.closure_type.origin = "user_confirmed"`
—— 留痕说"人工确认过"，值却是空的，图纸里读到的 `磁吸` 只进 `alternatives`，
**这个字段永远是空的**。

口径修正（§3.3）：**"人工确认"是一个有值的判断**。值为空时不存在"用户确认过的值"，
必须让图纸证据按正常路径写入；值非空时一个字都不许改（冻结红测 D7 守这条）。

### 1.4 BOM 行 ↔ 零件是纯位置配对，且静默（P1）

`packaging_parts.bind_rows()` 现在的配对规则是「模板行出现顺序 ↔ 零件面积降序」，没有材料 / 类别一致性，
也不在报告里披露——34 上实测把 **`RB02001-P08`（磁铁）配到了 443.5×492.6 的纸面板**上，
BOM 行看起来"有尺寸了"，却是一个物理上不可能的配对，且没有任何地方能看出来。

本批**不改成拒绝配对**（拒绝会让冻结红测 E2/E6 的 `bound == 4 / 11` 口径失效，需要业务先签字），
只要求**披露**：逐行留痕 `pairing_basis` / `material_match`，并在报告里单列不一致项（§3.4）。

### 1.5 成本缺口 6 类（P1，记录在案，本批不实现）

34 上同一项目 24 条缺口，按类归并：

| 缺口 | 条数 | 性质 |
| --- | --- | --- |
| `content_formula_error:PKG-P-*` | 7 | **数据，不是 bug**：源工作簿 `包装运输!D..G` 本来就是空单元格（隔卡 / 胶袋 / 双胶纸 / 护角 / 标签 / 盖板） |
| `material_gsm_missing` | 6 | **引擎能自己算**：`灰板 2.0mm` + 密度 0.75 g/cm³ → 1500 g/㎡（推导口径见 `packaging-cost-gaps-closure.md`） |
| `loss_rate_missing` | 6 | 数据：非纸类材料在 `kb_cost_factor` 里没有对应损耗率 |
| `material_price_missing` | 3 | 数据：装帧布 / 钕铁硼 / 海绵裱绒 无价 |
| `no_formula:print` | 1 | **设计如此**：print 在 0903 里是手填列，只能走 §3.1 的放行留痕（冻结红测守） |
| `tooling_basis_missing:T-PKG-DIE-REFUND` | 1 | 口径未定：刀模分摊基数是商务决定，不许实现方拍 |

> **更正（9-22 实测）**：我上一轮把 `content_formula_error` 说成"变量绑定 bug"是**错的**——那 7 行的
> 尺寸/用量在源工作簿里就是空的，Spec（第 7 批）要求"记 None、由缺口披露"。真正实现侧能自己收口的是
> `material_gsm_missing`（6 条），已单独成批：`packaging-cost-gaps-closure.md` + 红测
> `tests/test_packaging_cost_gaps_red.py`。同样更正：`packaging_route._AGGREGATE_EXPANSION`
> **不是**死规则（它比对的三个工序名正是 `SURFACE_REQUIREMENTS` 的取值）。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_handoff.py`
   - `send_to_quote()` 在调用 `cpq_bridge.send_to_quote` 之前，把已算出的 `waiver` 放进交给桥的正文：
     `bridge_result = {**bridge_result(package), "gap_waiver": waiver}`（无缺口时**不**放这个键，
     保持与今天逐字一致）。
2. `cpq_tech_bridge.py`
   - `_guard_packaging_result(result)` 增加放行分支：正文里有**合法** `gap_waiver` 时不再抛
     `BridgeError`，并把它写进落点（`payload["tech_result"]["gap_waiver"]` 已经在 §3.1 的路径上，
     不需要额外写第二份）；返回值改成放行摘要（无缺口 / 未放行时仍返回 `None`）。
   - **不得**放宽非包装、不得放宽"没有 gaps 却要放行"、不得放宽角色门。
3. `tech_app/backend/services/requirement_service.py`
   - `return_requirement_to_draft()` 接受 `pending_confirmation / pending_review / approved`，
     并在 `history` 里记 `confirmation_returned`；`draft` 幂等返回自身。
   - **`EDITABLE_STATUSES` 逐字不变**（不许靠"把 approved 塞进可编辑集合"来绕过——
     那会让已批准需求被下一次保存静默改写）。
4. `tech_app/backend/services/packaging_semantics/provenance.py`
   - `_is_user_confirmed()` 只在**当前值非空**时才算人工确认；值为空（`None` / `""` / 空白 / 键不存在）
     时走正常写入路径（该写值就写值、来源仍是 `manual`、`origin` 记 `user_confirmed`、alternatives 照旧）。
5. `tech_app/backend/services/packaging_parts.py`
   - `bind_rows()` 逐行留痕 `dwg_binding.pairing_basis`（非空字符串，说明这一对是怎么配上的）与
     `dwg_binding.material_match`（`True/False/None`：None = 任一侧材料未知，**不许猜**）；
   - 返回值新增 `pairing_review`：`[{item_key, part_code, row_material, part_material, material_match}]`，
     只登记 **两类都已知且不同类** 的组合；`pairing_review` 是**披露**，`bound` / `unbound` / `gaps`
     口径逐字不变（冻结红测 E2/E3/E4/E5/E6 一个字都不许动）。
   - 材料分类只认关键词闭集（纸/板/卡/坑/牛皮 · 磁铁/钕铁硼/磁石 · 五金/铁/铝 · 丝带/织带/布/绒 ·
     EVA/海绵/PET/PVC/塑料），**闭集外一律 `None`**（未知不等于不匹配）。

## 3. 口径（逐条验收）

### 3.1 放行留痕过桥

`gap_waiver` 形状（与技术侧落库的那份**同一个对象**）：

```python
{"by": <username 非空>, "at": <时间串 非空>, "reason": <原因原文 非空>, "codes": [<缺口码…>]}
```

合法判据（全部满足才算合法，否则照旧拒绝并**点名缺口**）：

- `by` / `at` / `reason` 都是非空字符串（去空白后）；
- 包里**能逐条列举缺口码**时，`waiver["codes"]` 必须覆盖它们（超集）；
  包里只有 `has_gaps` 而没有逐条码时（技术侧允许这种包），`codes` 允许为空；
- 放行只作用于**这一份包**：`codes` 不覆盖新出现的缺口时，照旧拒绝。

放行之后，落点的任务 payload 必须能读到 `reason`（页面要显示"为什么带缺口也放行了"）。

### 3.2 需求退回草稿

- `pending_confirmation / pending_review / approved` → `draft`，`history` 追加 `confirmation_returned`，
  写审计；`draft` 幂等；其它状态（含未知状态）照旧 409，码不变。
- `EDITABLE_STATUSES` 仍是 `("draft", "rejected")`；`save_requirement_draft()` 对 `approved` 仍然拒绝
  （**退回之后**才可编辑，这是两条不同的动作）。

### 3.3 人工来源空值

| 当前值 | 来源 | 图纸证据 | 结果 |
| --- | --- | --- | --- |
| 空 / 缺键 | `manual` | confirmed 值 | **写入**该值；`field_sources` 仍 `manual`；`origin=user_confirmed` |
| 非空 | `manual` | confirmed 值 | 值不变；只追加 `alternatives` + `PACKAGING_FIELD_USER_CONFIRMED` 警告（冻结 D7） |
| 非空 | `manual` | missing / 值为 None | 值不变（不许被清空） |

### 3.4 回填披露

- 每个被绑定的行：`size_source.dwg_binding` 至少含
  `component_id / part_code / rule_id / fallback_paired / original_missing_variables / pairing_basis / material_match`；
- 两类材料都已知且不同类 → 该行 `material_match=False`，并在 `pairing_review` 里出现（带 `row_material` /
  `part_material`）；行**仍然被绑定**（`status=computed`、有长宽），`bound` 计数不含它以外的任何变化；
- 任一侧材料未知 → `material_match=None`，**不**进 `pairing_review`；
- 没有零件文档 → 报告与今天逐字一致（`_bind_parts` 的早退分支不许动）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测与所有冻结红测的字面常量）；
- 不许改 `packaging-quote-close-loop` / `packaging-dwg-parts-extraction` / `packaging-process-route` /
  `packaging-cost-*` 的既有口径与稳定码；
- 不许把 `approved` 加进 `EDITABLE_STATUSES`；不许让 `save_requirement_draft` 接受非 draft/rejected；
- 不许在放行分支里绕开角色门、不许给"没有缺口的包"造留痕、不许把 `codes` 校验降级成"有 codes 就行"；
- 不许给材料分类猜值（闭集外必须 `None`）、不许用默认值补成本缺口；
- 不许改前端、不许改 `cpq_packaging_quote` 定价口径、不许改数据库 schema；
- 不许 commit / push / tag / Release / 部署 / 重启服务 / 写生产库。

## 5. 验收

```bash
# 本批红测（实现前必须真的红）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_downstream_blockers_red -v

# 不回归（冻结面）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_quote_close_loop_red      # OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red      # 32 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_semantics_red             # 59 OK (skipped=1)
./open-claude/.venv/bin/python -m unittest tests.test_packaging_process_route_red          # OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red         # 57 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red           # 54 OK (skipped=1)
./open-claude/.venv/bin/python -m unittest tests.test_tech_requirement_stage_waiver_red    # OK
./open-claude/.venv/bin/python -m unittest tests.test_tech_requirement_confirm_red         # OK
./open-claude/.venv/bin/python -m unittest tests.test_tech_requirement_review_red          # OK
```

真样本（本机有转换器时；gated 用例）不变：
`CPQ_DWG_REAL_SAMPLES=1 ./open-claude/.venv/bin/python -m unittest tests.test_packaging_product_outline_red -v`。

## 6. 本批不做（写在这里，别当成已通）

- §1.5 的四类成本缺口（材料价格 / 损耗率 / `print` 公式 / 刀模分摊基数）要业务给数或给口径；
  其中 `material_gsm_missing` 是引擎自己能算的，已单独成批
  （`packaging-cost-gaps-closure.md` + `tests/test_packaging_cost_gaps_red.py`）；
- BOM 行 ↔ 零件的**正式对应表**（不是位置配对）需要业务签字，签字后要同步改冻结红测 E2/E6 的期望；
- 双数据目录（`tech_app/data` vs `tech_app/tech_data`）与财务角色对无财务交接项目的 404 属环境问题。

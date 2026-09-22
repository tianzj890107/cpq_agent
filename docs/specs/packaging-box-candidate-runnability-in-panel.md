# 规格：盒型候选列表要说清「选它能不能往下走」（接 `## 289` §7.3 的前端接线）

依赖：`docs/specs/packaging-box-candidate-rank-and-runnability.md`（§2.2 接口已带
`part_template_available` / `part_template_total` / `part_template_unavailable`；§7.3 明写
「不改前端的候选列表渲染（前端接线是另一批，本批只保证接口上有事实可用）」）、
`docs/specs/packaging-box-type-matching.md`（候选列表与四态决策）、
`docs/specs/packaging-silent-degradation-disclosure.md`（§2.4：「查不到」不许说成「没有」）。

状态：Spec + 红测（已实现）（原状：`tech_app/frontend/requirement-confirm.js` 的
`bmCandidate()`（`:107-127`）只渲染总分 / 分项分 / 淘汰原因 / 无法判定 / 尺寸越界 /
适用行业，**一个字都没提**后端已经给的 `part_template_available` —— 用户要等确认了
无模板盒型、走到 2.2+ 才在 BOM / 工艺那一步撞墙）
红测：`tests/test_packaging_box_candidate_runnability_panel_red.py`
行号基线：HEAD `0b8b64b`

## 0. 一句话目标

1.2 的盒型候选列表里，每个候选要带一句「选它能不能往下走」：有模板给条数、没模板给可判警告、
**查不到**要说「查不到」而**不是**「没有」—— 披露不改任何既有判据（不禁用确认按钮）。

## 1. 现状缺口（代码级）

1. 接口那半早已就位：`packaging_match._candidate()`（`:523-531`）给出
   `part_template_available`（`True` / `False` / `None` 三态）、`part_template_total`、
   `part_template_unavailable`（读失败时的 `{code, reason, message}`）；`_part_template_state()`
   （`:419-436`）的纪律是「读不到 → `None`（未知），**不折成 `False`**」。
2. 前端这半没接：`requirement-confirm.js:107-127` 的 `bmCandidate(row, index)` 渲染了
   `total_score` / `dimension_scores` / `reject_reasons` / `undecidable_dimensions` /
   `out_of_range` / `applicable_industries`，**没有** `part_template_*` 任何一项。
3. 后果：用户点「确认此盒型」时看不到「这个盒型根本没有部件模板（确认后 BOM / 工艺 / 成本
   都跑不动）」这条唯一预兆；而 `packaging-box-candidate-rank-and-runnability.md` §2.2 已经
   保证这条事实**在选之前就看得见** —— 接口有、页面没显示，等于没有。

## 2. 契约

### C1 新增顶层纯函数 `boxCandidateRunnabilityNote(row) -> string`

放在 `requirement-confirm.js` 的**顶层**（与 `confirmationQuestions()` 同级，不是 IIFE 里），
体内**不得**出现 `document.` / `window.` / `fetch(` / `localStorage`（可被 `node -e` 抽出来真跑）。
四态固定，**互斥**：

| `row.part_template_available` | 返回 |
| --- | --- |
| `false` | `这个盒型还没有部件模板（<total> 条），确认后 BOM / 工艺 / 成本都跑不动；先补模板再确认`（`<total>` = `Number(row.part_template_total)`，非有限数按 `0`） |
| `true` | `Number(row.part_template_total)` 是 `> 0` 的有限数 → `部件模板 <total> 条`；否则 `""`（不猜条数） |
| `null`（显式未知） | `row.part_template_unavailable.message` 去空白后**逐字**返回（后端那句话本来就是给人看的）；拿不到 message → `部件模板暂时查不到，请稍后重试；这不代表该盒型没有模板` |
| 其它（键缺失 / `undefined`） | `""`（老后端没有这条事实，前端不替它编） |

「未知」这一态**不许**出现「这个盒型还没有部件模板（0 条）」这种断言句（Spec §2.4 的同一条纪律）—— 后端那句话里的「这不代表该盒型没有模板」是**正确措辞**，逐字引用不算违反。

### C2 `bmCandidate()` 渲染这一句

- 在候选行里新增一处（`data-bm-runnability="1"` 钩子）：`boxCandidateRunnabilityNote(row)`
  非空才输出，空串时**不输出空节点**；
- 这句是**披露**，不是闸门：`确认此盒型` 按钮的 `disabled` 判据仍是
  `row.can_confirm && bmCanDecide()`，**不许**因为"没有模板 / 查不到"而额外禁用；
- 既有渲染一个字段都不减：名次 / 编码 / 名称 / 族 / 总分 / 状态 / 尺寸越界 /
  分项分 / 淘汰原因 / 无法判定 / 按钮 / 适用行业与业务状态，逐字不变。

### C3 前端不重排、不改判据

- 候选顺序仍严格按接口给的数组（`record.candidates || []`）渲染，**不许**在前端 `sort()`；
- 不改 `bmSubmit()` / `bmCanDecide()` / 四态决策的码与文案；不改 `missing_inputs` / `stale` 的渲染。

### C4 冻结面

- 不改后端（`packaging_match._candidate()` / `_part_template_state()` /
  `_template_warnings()` 一行不动）、不加接口、不加依赖、不改 CSS 变量；
- 不改 `packaging-box-candidate-rank-and-runnability.md` 已定的排序键与
  `part_template_*` 三态口径；`node --check tech_app/frontend/requirement-confirm.js` 必须通过。

## 3. 允许修改范围

1. `tech_app/frontend/requirement-confirm.js`：新增顶层纯函数
   `boxCandidateRunnabilityNote()`；`bmCandidate()` 加一处渲染；必要时在样式文件里加一条
   类名下样式（不新增设计变量）；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许把「未知」渲染成「没有模板」，也不许用 emoji / 颜色暗示代替那句话；
- 不许因为这一句禁用确认按钮、不许改四态决策、不许在前端重排候选；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_box_candidate_runnability_panel_red -v
node --check tech_app/frontend/requirement-confirm.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_box_candidate_rank_and_runnability_red \
  tests.test_packaging_box_type_matching_red \
  tests.test_quote_packaging_box_selection_red
```

## 6. 已记录的边界

1. 本批只做**读侧披露**：不补模板、不自动换候选、不在前端算"能不能往下走"；
2. `part_template_total` 只是条数显示，**不是**判据（判据说的是 `part_template_available`）；
3. 老后端（没有这三个键）时这一句不出现 —— 与既有的「前端不替后端编事实」一致。

## 7. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_box_candidate_runnability_panel_red
# 实现前：Ran 12 tests … FAILED (failures=7)   ← T1–T6 + S1（S1 要抽那个还不存在的函数体）
# 实现后：Ran 12 tests … OK                    ← S2–S6 五条护栏始终绿
node --check tech_app/frontend/requirement-confirm.js   # OK
```

| 契约 | 落点 |
| --- | --- |
| §C1 顶层纯函数 | `tech_app/frontend/requirement-confirm.js`：`boxCandidateRunnabilityNote(row)` 放在**顶层**（`function renderConfirm(` 之前，与 `confirmationQuestions()` 同级）—— `false` → 「这个盒型还没有部件模板（N 条），确认后 BOM / 工艺 / 成本都跑不动；先补模板再确认」；`true` 且条数 > 0 → 「部件模板 N 条」（否则 `""`）；`null` → `part_template_unavailable.message` 逐字（拿不到给「部件模板暂时查不到，请稍后重试；这不代表该盒型没有模板」）；键缺失 / 其它 → `""`。体内无 `document.` / `window.` / `fetch(` / `localStorage` |
| §C2 候选行渲染 | `bmCandidate()` 内新增 `const runnability = boxCandidateRunnabilityNote(row)`，在「无法判定」之后、动作行之前输出 `<div class="box-match-runnability" data-bm-runnability="1">`（**非空才输出**）；`确认此盒型` 的 `disabled` 判据仍是 `row.can_confirm && bmCanDecide()` —— 披露不是闸门；既有字段（名次 / 编码 / 名称 / 族 / 总分 / 状态 / 尺寸越界 / 分项分 / 淘汰原因 / 无法判定 / 按钮 / 适用行业与业务状态）逐字未动 |
| §C3 不重排 | `bmPanel()` 仍 `candidates.map(bmCandidate)`（`record.candidates \|\| []`），没有 `candidates.sort(` |
| §C4 冻结面 | 后端 `packaging_match.py` 一行未改（三态口径与 `return None, 0, {` 原样）；`tech_app/frontend/requirement-confirm.html` 的内联 `<style>` 只加了一条 `.box-match-panel .box-match-runnability{color:#5b6472;font-size:12px;margin-top:4px}`（不新增设计变量） |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_box_candidate_rank_and_runnability_red \
  tests.test_packaging_box_type_matching_red \
  tests.test_quote_packaging_box_selection_red            → Ran 79 … OK
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_cost_route_version_read_failure_red tests.test_packaging_cost_rule_routing_red \
  tests.test_packaging_downstream_block_code_http_red tests.test_packaging_parametric_bom_red \
  tests.test_packaging_process_route_red                  → Ran 166 … OK
```

未改后端、未改 `tests/` 下任何文件、未连 PG / 34、未写生产数据、未 push / MR / tag / Release / 未部署。

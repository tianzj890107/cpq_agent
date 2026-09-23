# 包装项目的技术侧流程投影必须认得图纸解析链路的产物：2.1 今天永远是「图纸还没有解析」，后面 12 步全被它挡住

血缘：`tech-unified-workflow-projection.md`（唯一流程投影）、`drawing-flow-error-taxonomy.md`、
`packaging-parts-list-visibility-and-kinds.md`（零件文档是唯一事实源）、
`packaging-parts-downstream-process-and-cost.md`（零件下游的可算性判据）、
`quote-first-project-entry.md` §6（历史项目一次性恢复）。

状态：Spec + 红测（已实现）（原状：本批只写 Spec 与红测，`workflow_projection._LOADERS` 只登记技术侧 `store.load_ir`，包装项目 2.1 永判 `not_started`）
红测：`tests/test_packaging_tech_projection_2_1_must_see_drawing_flow_red.py`
本批 changelog 条目号：`## 443`（缺口类，作者侧）；落地条目见 `## 447`。

## 0. 一句话目标

**包装（DWG 图纸）项目**跑完 2.1 一键解析图纸（8/8 completed）、零件也拆出来了，
技术侧流程投影却把 2.1 判成 `not_started + 图纸还没有解析`，于是 2.1 之后**每一步**
都被追加 `请先完成 2.1 图纸解析` 并 `actionable=false`，`next_action` 永远指回 2.1 ——
工艺工程师照着看板点，只会一遍遍回到 2.1 重跑，而看板的结论一个字都不变。
这既是"下游任务做不动"的直接原因，也是"顺序看着全乱"的来源（1.2/1.3 都完成了，
却被告知要先去做 2.1）。

## 1. 现场证据（34，2026-09-23，Codex 真跑，非推断）

链路：SM1 报价会话 `1bef04f7dab3` → PE1 技术项目 `8131f6d29d99`（`酒盒.dwg`）→ 需求
`REQ-8131F6D29D99` → 一键解析图纸 8/8 → 零件 263 件 →（盒型确认后）BOM 33 行 / 工艺路线
13 道 / 成本 20.528356 / 回传 `pkghandoff:8131f6d29d99:REQ-8131F6D29D99:default:1` →
卡片 `3992194522812519820`（6 步 done）。

| 事实 | 值 | 读法 |
| --- | --- | --- |
| 图纸解析链路 | 8/8 `completed`，`flow.status="completed"` | `GET /api/projects/8131f6d29d99/drawing-flow` |
| 零件文档 | `part_total=263`、`parts_id=parts:c2f1579db133d9ba` | `GET …/requirement/packaging-parts` |
| **2.1 投影** | `status="not_started"`、`completed=false`、`actionable=true`、`missing_requirements=["图纸还没有解析"]` | `GET /api/projects/8131f6d29d99/workflow/projection`（09:30:08） |
| **下游 12 步** | 1.2/1.3/3.1/3.2/3.3/4.1/4.2/4.3/5.1/5.2/5.3 全部 `actionable=false` + `blocked_reasons=["请先完成 2.1 图纸解析"]` | 同上 |
| `next_action` | `{"key":"2.1","primary_action":"parseDrawing"}` | 同上 |
| 技术侧 IR | `ir: null` | `GET /api/projects/8131f6d29d99` |
| 包装侧 IR 留痕 | `meta.cad_ir_rev` / `meta.cad_ir_updated_at` 有值 | `GET /api/projects?scope=all`（`meta`） |

**同一根因的第二个症状（HEAD 上已经挂着的一条红，不是本批引入）**：
`./open-claude/.venv/bin/python -m unittest tests.test_tech_unified_workflow_projection_red`
今天 43 条里红 1 条 —— `RequirementCompletionTest.test_confirmed_requirement_completes_confirm_and_opens_review`
（"确认后 1.3 应可执行"），失败原因正是 `blocked_reasons: ['请先完成 2.1 图纸解析']`。
那条断言与本 Spec 要求的方向一致（2.1 不该压住 1.3），但它的夹具项目连图纸都没上传，
所以本 Spec 只负责"**有解析产物时** 2.1 必须算完成"这一半；"没有图纸时 1.3 该不该可执行"
是那份 Spec 自己的裁决，本批**不改**它、也不替它下结论。

## 2. 根因（代码事实，逐条可复现）

1. `tech_app/backend/services/workflow_projection.py` 的 `_LOADERS` 只登记了
   `store.load_ir(pid)`（**技术侧** `DesignIR`）—— 没有包装图纸解析链路的任何取数项：
   `grep -n "_LOADERS" -A 10 workflow_projection.py` → `meta / requirement / ir / plan /
   review / summary / report / audit`。
2. `_judge("2.1", …)` 只看 `facts["ir"]`：`if ir is None: return {"status": "not_started",
   "completed": False, "missing": ["图纸还没有解析"]}`。
3. 包装 DWG 项目把解析产物写在**另外两份文档**里：`tech_app/backend/services/cad_ir`
   （`cad_ir.load_ir(pid)`，`meta.cad_ir_rev` 是它的版本号）与
   `packaging_parts.load_parts(pid)`（263 件零件）。两份都不经 `store.load_ir`。
4. `_rows_for()` 用"**前面任何一个子步骤没完成**"给后面每一步追加
   `请先完成 {prior} {prior_spec['sub_title']}`（只取最近一个未完成的前置）。2.1 永远
   没完成 → 2.1 之后的每一步都带上那一句，`actionable` 因此恒为 `false`。
5. `_next_action()` 取第一个"未完成且可执行"的步骤 → 恒为 2.1。

于是：解析真跑过、零件真有 263 件，看板却告诉用户"图纸还没有解析"。

## 3. 口径（可直接验收）

1. `_LOADERS` 必须新增两个来源，键名固定为
   `packaging_cad_ir`（`cad_ir.load_ir(pid)`）与 `packaging_parts`
   （`packaging_parts.load_parts(pid)`）；取数失败只记进 `facts["errors"]`，不抛。
2. `_judge("2.1", …)` 在**技术侧** `ir is None` 时，必须再看这两个来源：
   1. `packaging_cad_ir` 非空**且** `packaging_parts` 里 `parts` 非空（或
      `stats.part_total > 0`）→ `{"status": "generated", "completed": True}`，
      `missing` 为空 —— 与技术侧 `parts` 非空时的取值**逐字一致**；
   2. `packaging_cad_ir` 非空但零件为 0 → 沿用既有"本次没有识别出零件"分支：
      审计里有过 `parse_no_parts_confirmed` → `confirmed` + `completed: True`；
      否则 `generated` + `completed: False` + 既有那句 missing 文案（逐字不改）；
   3. 两条都不满足（没有包装 IR、没有零件、也没有技术 IR）→ **逐字保持今天的行为**
      （`not_started` + `["图纸还没有解析"]`）。
3. 下游不再被 2.1 挡：2.1 判成 `completed` 后，`_rows_for()` 不再给后面的步骤追加
   `请先完成 2.1 图纸解析`；`_next_action()` 不再指回 2.1。
4. 冻结面（一个字都不许动）：`STATUS_ENUM`、`stages_table.STAGES` 的 13 个 `sub`
   编号与标题、其余 12 个子步骤的判定与文案、`_PRIMARY_ACTIONS`、
   `actionable` 的算法（`(completed or not blockers) and not blocked and has_role`）、
   角色/权限判定 `_has_role`。
5. 不许把"包装"当成二套判据写在前端：唯一事实源仍是这一份投影，前端只渲染。

## 4. 允许修改范围

- `tech_app/backend/services/workflow_projection.py`：新增两个 loader 登记 +
  `_judge("2.1")` 的包装分支（可以抽一个 `_packaging_drawing_done(facts)` 纯函数，便于红测直接跑）。
- 禁止：改 `workflow_stages.py`（编号表）、改其它子步骤的 `_judge` 分支、
  改任何既有文案、改前端"特判"、改 `actionable` 规则、直接读 34 上的数据兜底。

## 5. 红测分组（`tests/test_packaging_tech_projection_2_1_must_see_drawing_flow_red.py`）

- **A 组 包装项目必须认得（今天都是红的）**
  - A1 `_LOADERS` 里必须登记到包装图纸解析链路的取数项（`packaging_cad_ir` / `packaging_parts`）；
  - A2 技术侧 `ir=None` + 包装 IR 有 + 零件 263 件 → `_judge("2.1")` 必须
    `completed=True` 且 `status == "generated"`、`missing == []`；
  - A3 同一份 facts 走 `_rows_for()`：2.1 `completed=True`，且 1.2/1.3/3.1/3.2/3.3 的
    `blocked_reasons` 里**没有** `请先完成 2.1 图纸解析`；
  - A4 同一份 rows 走 `_next_action()`：不再指回 2.1；
  - A5 包装侧"有 IR、零件 0"的分支今天完全没接：必须与技术侧同形
    （无 `parse_no_parts_confirmed` → `generated` + `completed=False` + 既有 missing 文案；
    有该审计 → `confirmed` + `True`）。
- **B 组 护栏（今天就是绿的，不许被改红）**
  - B1 没有任何解析产物（技术 IR 空、包装 IR 空、零件 0）→ 2.1 仍 `not_started` +
    `missing == ["图纸还没有解析"]`；
  - B2 技术侧有 IR + 有零件 → 2.1 仍 `generated` + `completed=True`（原口径不变）；
  - B3 **技术侧**有 IR、零件 0、无 `parse_no_parts_confirmed` 审计 → 仍 `completed=False` +
    既有 missing 文案；补上该审计 → `confirmed` + `True`；
  - B4 1.1/1.2/1.3 判定抽样不变（`draft` → 1.1 `in_progress`；`pending_confirmation` →
    1.2 `awaiting_confirmation` + `missing == ["需求确认尚未提交"]`）；
  - B5 `workflow_stages.STAGES` 的 13 个 `sub` 与 `STATUS_ENUM` 不变；
  - B6 前置阻断文案模板仍是 `请先完成 {key} {title}`（用"2.1 真的没完成"的 facts 验一次）；
  - B7 前置阻断仍只由投影模块产生（源码守卫：`请先完成` 不出现在前端）。

## 6. 验收

1. 34 上对 `8131f6d29d99` 重读投影：2.1 `completed=True`、`next_action` 不再指回 2.1、
   1.2/1.3 的 `blocked_reasons` 里没有 `请先完成 2.1 图纸解析`；
2. 技术侧（`store.load_ir` 有值的）项目口径逐字不变；
3. 三种"真没解析"的项目仍照旧 `not_started` + `["图纸还没有解析"]`。

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_tech_projection_2_1_must_see_drawing_flow_red -v
```

## 7. 红基（2026-09-23 实跑，未实现）

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_tech_projection_2_1_must_see_drawing_flow_red
  → Ran 12 tests … FAILED (failures=5, errors=0)
```

红的 5 条 = A1（`_LOADERS` 没有包装来源）、A2（2.1 判成 `not_started`）、
A3（2.1 之后的步骤全被 `请先完成 2.1 图纸解析` 挡住）、A4（`next_action` 指回 2.1）、
A5（包装侧"0 零件要人工确认"的分支完全没接）；
绿的 7 条护栏 = B1–B7。

## 8. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 447`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §3.1 两个包装取数项 | `workflow_projection._LOADERS` | 新增 `("packaging_cad_ir", lambda pid: packaging_cad_ir.load_ir(pid))` 与 `("packaging_parts", lambda pid: packaging_parts.load_parts(pid))`（模块顶部 `from . import cad_ir as packaging_cad_ir` / `from . import packaging_parts`，无循环导入）；取数失败仍只进 `facts["errors"]`，不抛 |
| §3.2 包装分支 | 新纯函数 `_packaging_drawing_done(facts)` | 没有包装 IR → `None`（沿用技术侧口径）；有 IR 且 `parts` 非空（或 `stats.part_total > 0`）→ `generated` + `completed=True`（与技术侧逐字一致）；有 IR、零件 0 → 交 `_no_parts_verdict(facts)`（审计有 `parse_no_parts_confirmed` → `confirmed` + True，否则 `generated` + False + 既有文案） |
| §3.2 第 2 条两侧同形 | 新纯函数 `_no_parts_verdict(facts)` | 技术侧原来的"0 零件要人工确认"三条分支抽成这一支，技术侧与包装侧共用（技术侧行为逐字不变） |
| §3.3 下游不再被 2.1 挡 | `_rows_for()` / `_next_action()`（未动） | 2.1 算完成后前置阻断自然消失，两函数一字未改 |
| §3.4 冻结面 | 未动 | `STATUS_ENUM`、`workflow_stages.STAGES`、其余 12 步判定、`_PRIMARY_ACTIONS`、`actionable` 算法、`_has_role` 都没碰 |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_tech_projection_2_1_must_see_drawing_flow_red
Ran 12 tests ... OK            # 红基 5 红 7 绿 → 12 全绿

./open-claude/.venv/bin/python -W ignore -m unittest tests.test_tech_unified_workflow_projection_red
Ran 30 tests ... FAILED (failures=1)   # 仍是 §1 那条既有挂账
                                       # RequirementCompletionTest（夹具项目连图纸都没上传），非本批引入，本批不改它
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_tech_home_timeline_and_publish_closure_red
Ran 35 tests ... OK
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_cpq_eval_production_backed
Ran 21 tests ... OK (skipped=1)
```

红测自身缺陷：无。本批只扩 2.1 判据，技术侧口径与其它 12 步一字未动。

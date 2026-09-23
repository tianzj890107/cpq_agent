# 包装项目 2.1 解析出 263 件零件，成本阶段一件都看不见：4.1 永远说「还没有可测算的零件」、4.2 却报「进行中」（第 4 阶段口径）

血缘：`packaging-tech-projection-2-1-must-see-drawing-flow.md`（2.1 已改认包装解析产物：
`packaging_cad_ir` / `packaging_parts` 两份文档，不走 `store.load_ir`）、
`tech-unified-workflow-projection.md`（投影是唯一事实源）、
`unified-tech-cost-workbench.md` / `cost_review.summarize()`（成本阶段的唯一汇总口径）、
`packaging-cost-*`（包装的成本口径在报价侧 `packaging_cost`，技术侧这一阶段仍按同一批零件算）。

状态：Spec + 红测（已实现）（原状：本批只写 Spec 与红测，业务实现不在本批；34 真跑现场见 §1）
红测：`tests/test_packaging_cost_stage_must_see_the_parsed_parts_red.py`
本批 changelog 条目号：`## 451`

## 0. 一句话目标

2.1 已经认包装解析产物了（`## 447`），可 **4.x 成本阶段没有跟着改**：`_judge_cost()` → `_cost_data()` →
`cost_review.summarize(project_id, ir, plan)` 的零件来源仍是 `ir.parts`（`store.load_ir`），
而包装项目的零件在 `packaging_parts` 文档里、IR 是空的。于是同一个项目上：

- 2.1「图纸解析」= `generated / completed`（零件真有 263 件，读件端点也读得回来）；
- 4.1「零件成本测算」= `not_started`，`missing = ["还没有可测算的零件"]`（**一件都看不见**）；
- 4.2「整机（组装）成本」= `in_progress`，`missing = ["整机（组装）成本还没有测算"]`
  —— 一件零件都没有、成本阶段根本没开始，却报「进行中」；
- 4.3「成本汇总」= `not_started`；父阶段（phase 4「成本测算」）= `not_started`。

也就是：**零件拆出来了，成本这一步永远做不了**（点「成本测算」看到的是"没有零件"），
而同一份投影里 4.2 又骗人说"进行中"。

## 1. 现场证据（34，2026-09-23，Codex 真跑；项目 `8131f6d29d99`）

`GET /api/projects/8131f6d29d99/workflow/projection`（PE1，HTTP 200、`refresh_ok=true`、
`generated_at 2026-09-23 10:13:21`）逐条：

```
drawing   (2.1)  status=generated    completed=True   blocked=[]
cost      (4.1)  status=not_started  blocked=["请先完成 3.1 整合图纸", "需要财务负责人角色才能执行本步"]
cost      (4.2)  status=in_progress  blocked=["请先完成 3.1 整合图纸", "…"]
cost      (4.3)  status=not_started  blocked=["请先完成 3.1 整合图纸", "…"]
PHASE 4 成本测算  completed=False status=not_started
```

把 4.x 逐条单独判一遍（同一进程内直调投影自己的判据）：

```
_judge("4.1") → {"status": "not_started", "missing": ["还没有可测算的零件"]}
_judge("4.2") → {"status": "in_progress",  "missing": ["整机（组装）成本还没有测算"]}
_judge("4.3") → {"status": "not_started",  "missing": []}
```

同一项目的零件事实（`GET /api/projects/8131f6d29d99/requirement/packaging-parts`，HTTP 200，
1.3MB）：`part_total = 263`、`parts_id = parts:c2f1579db133d9ba`、零件号 `DWG-P01…`；
而 `GET /api/projects/8131f6d29d99/summary` 里 `ir = {}`（包装项目不往 IR 写零件）——
成本阶段读的就是这份空 IR。

同轮真跑把 3.1 也走完了（`POST /integration/params` → 参数 64 条 / 连接 3 处 / BOM 8 行；
`POST /integration/process` → 组装工序 10 道），投影里 3.1 之外的下游**全部**仍卡在
「请先完成 3.1 整合图纸」+ 4.x 的上面这两条，零件对成本阶段依然不可见。

## 2. 口径（可直接验收）

1. **零件口径同源**：成本阶段（4.1/4.2/4.3）的零件清单必须与**技术侧零件事实源**同源 ——
   技术侧 IR 有零件就用 IR；**包装项目（零件在 `packaging_parts` 文档里、IR 为空）必须看得见
   那一批零件**，数量与 2.1 报出的 `part_total` 一致（同一份 `packaging_parts.parts`），
   不许把「IR 为空」当成「没有零件」。
2. **4.1 的状态不许说反**：有零件、没有一件算过时是 `in_progress`（`missing` 点名未测算的零件号，
   沿用既有「还有零件未测算成本：…」句式）；**有零件但零件清单一件都取不到**才是
   `not_started`「还没有可测算的零件」。263 件零件的项目不许回 `not_started`。
3. **同一阶段内状态自洽**：4.1 判「没有可测算的零件」（`not_started`）或零件清单为空时，
   4.2 不许报 `in_progress`；`in_progress` 只能表示「这一步真的已经在做」
   （例如已有零件成本但没有整机成本）。
4. **冻结面**：2.1 认包装解析产物的那套判据（`packaging-t*` 第 447 条）不动；
   4.1/4.2/4.3 在**技术侧 IR 项目**上的三种口径与文案逐字不变；
   `cost_review.summarize()` 的返回键、`phase` 聚合规则（取第一个未完成子步的状态）、
   包装的报价侧成本口径（`cpq_packaging_quote`）都不动。

## 3. 允许修改范围

- `tech_app/backend/services/workflow_projection.py`：`_judge_cost()` 的零件口径与 4.2 的前置判断
  （把包装零件当作与 IR 零件同等的输入；口径统一走 `cost_review.summarize()`，不另算一份）。
- `tech_app/backend/services/cost_review.py`：若要在 `summarize()` 里接纳包装零件，
  只许**增加**技术侧零件来源（IR 为空时取包装解析产物），不许改返回键、不许改已有口径。
- 禁止：改 `store.load_ir` 的语义、把包装零件复制/写进 IR、改 2.1 的判据、改 4.x 的文案句式、
  改报价侧打包与定价。

## 4. 红测分组（`tests/test_packaging_cost_stage_must_see_the_parsed_parts_red.py`）

- **A 组（今天都是红的）**
  - A1 包装项目（`packaging_cad_ir` + `packaging_parts`：263 件）→ 4.1 **不许** `not_started`；
  - A2 同一份包装事实 → 4.1 的 `missing` 里不许再出现「还没有可测算的零件」；
  - A3 一件零件都没有、成本也没开始的项目 → 4.2 **不许** `in_progress`
    （「整机（组装）成本还没有测算」在那种状态下是假话），必须与 4.1 同为 `not_started`。
- **B 组：护栏（今天就是绿的，不许被改红）**
  - B1 2.1 仍认包装解析产物（`generated/completed`，`## 447` 的口径）；
  - B2 技术侧 IR 项目：有零件、没成本 → 4.1 `in_progress` 且 `missing` 点名零件号；
  - B3 技术侧 IR 项目：4.1/4.3 在「没有零件」时仍是 `not_started`；
  - B4 `_phases()` 的聚合规则不变（取第一个未完成子步的状态）；
  - B5 4.2 在「已有零件成本、缺整机成本」时仍判 `in_progress`（这一步真在做）。

## 5. 验收

1. 34 上 `GET /api/projects/8131f6d29d99/workflow/projection`：4.1 不再是「还没有可测算的零件」
   （263 件零件必须出现在成本阶段的口径里），4.2 不再在"什么都没算"时报「进行中」；
2. 包装卡片链路的成本阶段（财务经理那一步）点下去能看到这批零件；
3. 技术侧 IR 项目的投影与今天逐字一致。

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_stage_must_see_the_parsed_parts_red -v
```

## 6. 红基（2026-09-23 实跑，未实现）

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_cost_stage_must_see_the_parsed_parts_red
  → Ran 8 tests … FAILED (failures=3)
    A1 包装项目 263 件 → 4.1 仍 not_started「还没有可测算的零件」                 （红）
    A2 同一份事实 → missing 里仍是「还没有可测算的零件」                        （红）
    A3 真空项目 → 4.2 仍 in_progress「整机（组装）成本还没有测算」               （红）
    B1–B5 2.1 仍认包装解析产物 / IR 项目有零件缺成本仍 in_progress 且点名零件 /
          IR 项目无零件仍 not_started / `_phases()` 聚合规则不变 /
          4.2 在「零件在、整机成本缺」时仍是 in_progress：5 条护栏绿
```

红的是 A1–A3；绿的护栏是 B1–B5。逐条失败点与数字见 changelog `## 451`。

## 7. 关联

- 同族：`packaging-tech-projection-2-1-must-see-drawing-flow.md`（`## 443`，已实现 `## 447`）
  只修了 2.1；本批是**同一个根因在成本阶段的第二处**。
- 报价侧同一轮的两个死结见 `## 445`（第 5 步币种 / 汇总明细被判空）。

## 8. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 455`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 零件口径同源 | `cost_review.ir_from_packaging_parts()`（新，纯函数）+ `parts_source()`（新）+ `workflow_projection._cost_data()` | `_cost_data()` 先取 IR；IR 没有零件时把 `facts["packaging_parts"]` 那份**包装零件文档**按同一形状折成 `DesignIR` 交回**同一个口径** `cost_review.summarize()`（不另算一份、不复制进 IR、不动 `store.load_ir` 语义）。`parts_source()` 是给运行时调用方（真实项目、无 facts）用的同一条兜底：IR 为空时 `packaging_parts.load_parts(pid)` 读不到就沿用技术侧口径 |
| §2.2 4.1 不许说反 | `workflow_projection._judge_cost()` 4.1 | 判据一字未改（`data["parts"]` 空才 `not_started` + 「还没有可测算的零件」）——零件口径同源之后，263 件的项目自然落到 `in_progress` + 「还有零件未测算成本：DWG-P01、…」，零件号仍由 `counts.missing` 点名 |
| §2.3 同阶段内自洽 | `_judge_cost()` 4.2 | `not data` 之外新增 `not data["parts"]` → `not_started`：零件一件都取不到时这一步根本没开始，不再报「进行中」；零件在、整机成本缺仍是 `in_progress` + 「整机（组装）成本还没有测算」（B5 逐字不变） |
| §2.4 冻结面 | 未动 | 2.1 认包装解析产物的 `## 447` 判据、4.1/4.2/4.3 在技术侧 IR 项目上的三种口径与文案、`cost_review.summarize()` 的返回键、`_phases()` 聚合规则、报价侧打包与定价，一行未改 |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_cost_stage_must_see_the_parsed_parts_red
Ran 8 tests ... OK                     # 红基 3 红（A1–A3）/ 5 绿护栏（B1–B5）

./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_integration_stage_must_count_its_own_analysis_red
Ran 8 tests ... OK                     # 同批另一份（## 452）

# 保护网（投影 / 成本口径相关的 62 个模块）
Ran 1092 tests ... FAILED (failures=1, skipped=1)
# 那 1 条是既有挂账 `tech_unified_workflow_projection_red.RequirementCompletionTest`
# （`## 320` 按设计变红），与本批无关
```

红测自身缺陷：无。真实项目的零件清单走 `parts_source()`（读 `packaging_parts` 文档），
测试里走 `facts["packaging_parts"]`（同一形状、同一个折叠函数）——**两条路共用一个口径**。

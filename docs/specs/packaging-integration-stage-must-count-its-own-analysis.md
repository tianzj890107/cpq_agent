# 3.1「整合图纸」分析真跑完了（参数 64 条 / 工序 10 道都在），投影还说「整合分析还没有执行」：下游 3.2 / 3.3 / 4.x / 5.x 全被它挡着

血缘：`tech-unified-workflow-projection.md`（投影是唯一事实源）、
`unified-tech-cost-workbench.md`（3.1 整合图纸 → 3.2 参数推荐 → 3.3 组装工艺）、
`assembly-integration`（`runIntegration`：整合分析 = 参数推荐 + 组装工艺两轮模型调用）、
`packaging-cost-stage-must-see-the-parsed-parts.md`（同批 `## 451`，成本阶段看不见包装零件）。

状态：Spec + 红测（已实现）（原状：本批只写 Spec 与红测，业务实现不在本批；34 真跑现场见 §1）
红测：`tests/test_packaging_integration_stage_must_count_its_own_analysis_red.py`
本批 changelog 条目号：`## 452`

## 0. 一句话目标

3.1 的完成判据是 `done = bool(plan.drawings) or bool(parts)` —— 只看「有没有整合图纸」和
「有没有零件」，**不看这一步自己产出的东西**（`plan.params` / `plan.process`）。
包装项目既没有整合图纸、IR 里也没有零件（零件在 `packaging_parts`），
于是**哪怕整合分析真跑完了**：

- 3.1 = `not_started` + `missing=["整合分析还没有执行"]`；
- 3.2 = `awaiting_confirmation`（参数推荐就在那儿）、3.3 = `generated`（组装工艺也在那儿），
  但两条都带 `blocked_reasons=["请先完成 3.1 整合图纸"]` → `actionable=false`；
- `next_action` 永远指回 3.1 的「一键分析整合图纸」——用户点一次是它、再点一次还是它，
  4.x 成本、5.x 报告一步都走不到。

## 1. 现场证据（34，2026-09-23，Codex 真跑；项目 `8131f6d29d99` / `酒盒.dwg`）

同一轮真跑（前端「一键分析整合图纸」的真实载荷）：

```
POST /api/projects/8131f6d29d99/integration/params  → task 成功
   progress_log: 参数 64 条、连接 3 处、BOM 8 行；报价成品参数 23/64 已给出（必填 10/10）
POST /api/projects/8131f6d29d99/integration/process → task 成功
   progress_log: 共 10 道组装工序
GET  /api/projects/8131f6d29d99/integration
   status = {"drawings": 0, "has_params": true, "param_count": 64,
             "has_process": true, "process_step_count": 10, "params_complete": true, ...}
   plan.drawings=0  plan.params=True  plan.process=True
```

紧接着的投影（`GET /api/projects/8131f6d29d99/workflow/projection`，HTTP 200、`refresh_ok=true`、
`generated_at 2026-09-23 10:13:21`）：

```
3.1  not_started            completed=False  missing=["整合分析还没有执行"]  blocked=[]
3.2  awaiting_confirmation  completed=False  missing=["参数推荐尚未人工确认"] blocked=["请先完成 3.1 整合图纸"]
3.3  generated              completed=False  missing=["组装工艺尚未确认"]     blocked=["请先完成 3.1 整合图纸"]
next_action = {"key": "3.1", "sub_title": "整合图纸", "primary_action": "runIntegration",
               "required_role": "工艺工程师", "blocked_reasons": []}
```

也就是说：**面板上明明有 64 条参数与 10 道工序，流程却卡在它们的上一步，而且永远卡在那儿**
（再点一次「一键分析整合图纸」只会重跑一遍分析，3.1 的判据还是 `drawings or parts`）。

## 2. 口径（可直接验收）

1. **3.1 的完成判据要认自己产出的结果**：整合分析（`runIntegration`）的产出就是
   `plan.params`（参数推荐）与 `plan.process`（组装工艺）。`plan.params` 或 `plan.process` 已生成时，
   3.1 必须判 `completed`（`generated`），不许因为它没有整合图纸、IR 里没有零件就回 `not_started`
   ——「装配图不是必需的」（页面上原话），没有它也能按 2.1 的零件推参数。
2. **既有的两条通路不许退化**：有整合图纸 → 完成（今天的口径）；技术侧有 IR 零件 → 完成（今天的口径）。
3. **真的没跑时仍要说没跑**：`plan.params` / `plan.process` 都没有、也没有图纸与零件时，
   3.1 仍然是 `not_started` + `missing=["整合分析还没有执行"]`（文案逐字不变）。
4. **下游解锁**：3.1 判完成之后，3.2 / 3.3 的 `blocked_reasons` 里不许再出现「请先完成 3.1 整合图纸」，
   `next_action` 往前走（3.2 参数推荐确认），4.x / 5.x 的「请先完成 3.1 整合图纸」随之消失
   ——4.x 自己的零件口径问题另见 `## 451`。
5. **冻结面**：3.2 / 3.3 各自的判据与文案、4.x / 5.x 的判据、`_phases()` 聚合规则、
   阶段/子步骤的状态枚举、`runIntegration` 的动作与载荷都不动。

## 3. 允许修改范围

- `tech_app/backend/services/workflow_projection.py`：`_judge()` 里 `key == "3.1"` 的完成判据
  （把 `plan.params` / `plan.process` 纳入；`missing` 文案除「没跑过」那一支外不变）。
- 禁止：改 3.2 / 3.3 的判据、改 `runIntegration` 或 `/integration/*` 端点、把「有图纸/有零件」
  这两支删掉、改任何文案字面量。

## 4. 红测分组（`tests/test_packaging_integration_stage_must_count_its_own_analysis_red.py`）

- **A 组（今天都是红的）**
  - A1 只有 `plan.params`（参数推荐已生成）、无整合图纸、无 IR 零件 → 3.1 必须 `completed`；
  - A2 只有 `plan.process`（组装工艺已生成）→ 同上；
  - A3 34 那份真实形态（params + process 都在、drawings=0、IR 为空）→
    `next_action` 不许再指回 3.1。
- **B 组：护栏（今天就是绿的，不许被改红）**
  - B1 什么都没跑（无 params / 无 process / 无图纸 / 无零件）→ 3.1 仍 `not_started`，
    `missing == ["整合分析还没有执行"]`；
  - B2 有整合图纸 → 3.1 `completed`；
  - B3 技术侧有 IR 零件 → 3.1 `completed`；
  - B4 3.2 有 params 未确认 → `awaiting_confirmation`；3.3 有 process 未确认 → `generated`（文案不变）；
  - B5 `_phases()` 聚合规则不变（取第一个未完成子步的状态）。

## 5. 验收

1. 34 上 `GET /api/projects/8131f6d29d99/workflow/projection`：3.1 变 `generated/completed`，
   3.2 的 `blocked_reasons` 里不再有「请先完成 3.1 整合图纸」，`next_action` 指向 3.2；
2. 界面上「一键分析整合图纸」跑完后不再被要求重跑，能直接点「确认参数推荐 / 确认组装工艺」；
3. 技术侧 IR 项目与真空项目的投影与今天逐字一致。

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_integration_stage_must_count_its_own_analysis_red -v
```

## 6. 红基（2026-09-23 实跑，未实现）

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_integration_stage_must_count_its_own_analysis_red
  → Ran 8 tests … FAILED (failures=3)
    A1 只有 params（参数推荐已生成）→ 3.1 仍 not_started「整合分析还没有执行」      （红）
    A2 只有 process（组装工艺已生成）→ 同上                                     （红）
    A3 34 那份真实形态 → next_action 仍指回 3.1                                 （红）
    B1–B5 什么都没跑仍 not_started 且文案逐字不变 / 有整合图纸仍完成 /
          有 IR 零件仍完成 / 3.2·3.3 的判据与文案不变 / `_phases()` 聚合规则不变：5 条护栏绿
```

红的是 A1–A3；绿的护栏是 B1–B5。逐条失败点与数字见 changelog `## 452`。

## 7. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 455`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 3.1 认自己产出的结果 | `workflow_projection._judge()` 的 `key == "3.1"` | `done` 在 `bool(plan.drawings) or bool(parts)` 之外加上 `plan.params is not None or plan.process is not None`（`runIntegration` 的两样产出）；命中即 `generated/completed`，`missing` 清空。`plan is None` 那一支与「没跑过」的文案逐字未改 |
| §2.2 既有两条通路不退化 | 同上 | `bool(plan.drawings)`（B2）与 `bool(parts)`（B3）原样保留；3.2 / 3.3 的判据与文案一行未改（B4） |
| §2.3 真没跑仍说没跑 | 同上 | 图纸、零件、参数、工艺四样都没有 → 仍是 `not_started` + `missing=["整合分析还没有执行"]`（B1 逐字） |
| §2.4 下游解锁 | `_rows_for()` / `_next_action()` | 3.1 完成之后它不再是「前置未完成」的那一步，3.2 / 3.3 的 `blocked_reasons` 里不再出现「请先完成 3.1 整合图纸」，`next_action` 落到 3.2 参数推荐确认（A3） |
| §2.5 冻结面 | 未动 | 3.2 / 3.3 判据与文案、4.x / 5.x 判据、`_phases()` 聚合规则、状态枚举、`runIntegration` 的动作与载荷、`/integration/*` 端点，一行未改 |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_integration_stage_must_count_its_own_analysis_red
Ran 8 tests ... OK                     # 红基 3 红（A1–A3）/ 5 绿护栏（B1–B5）

./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_cost_stage_must_see_the_parsed_parts_red
Ran 8 tests ... OK                     # 同批另一份（## 451）
```

红测自身缺陷：无（A3 真跑 `_rows_for()` + `_next_action()`，不是只读源码）。

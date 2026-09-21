# 图纸零件的下游结论必须读得回来（落库 + 两个依据路由 + 进度文案一致）

血缘：承接 `packaging-parts-downstream-process-and-cost.md`（第 3 层：单件工艺与成本）、
`packaging-parts-selectable-panel.md`（第 2 层：可选中面板）、`packaging-parts-3d-extrusion.md`（第 4 层）。

- 状态：Spec + 红测（未实现）
- 红测：`tests/test_packaging_parts_downstream_readback_red.py`
- 依赖：`packaging_parts.py`、`packaging_drawing_flow`（任务层）、2.1 面板 `inline-analysis.js`

## 0. 一句话目标

零件级的工艺推荐与成本测算，**跑完要留得住、读得回、依据看得见，进度文案要和产出对得上**。
今天三件事全不成立：结论只活在任务记录里，刷新就没；依据路由 404；进度说"0 道工序"而结果有 4 道。

## 1. 现状缺口（34 实测，可复现）

项目 `f1417060ae9d`，4 件可算零件（`DWG-P07 / P14 / P24 / P35`）各跑一次：

```
POST /api/projects/f1417060ae9d/requirement/packaging-parts/DWG-P07/process   → 200 {"task_id": "04a5ea8fa699"}
GET  /api/projects/f1417060ae9d/tasks/04a5ea8fa699                            → succeeded
      result.plan.steps = 4 道工序（板料准备与开料 / 主体成形 / 去毛刺 / 最终检验）
      result.coverage    = {"library_steps": 0, ...}
      progress_log       = ["模型编制工序明细…", "  ↳ 共 0 道工序：沿用库内 0 道、缺失需新建 0 道"]

GET  /api/projects/f1417060ae9d/requirement/packaging-parts/DWG-P07/process    → 200 {"plan": null, "validation": null, "coverage": null}
GET  /api/projects/f1417060ae9d/requirement/packaging-parts/DWG-P07/process-lookup → 404 Not Found
GET  /api/projects/f1417060ae9d/requirement/packaging-parts/DWG-P07/cost-lookup    → 404 Not Found
```

### 1.1 三处代码事实

1. **结论不落库**：`main.py:7559 get_packaging_part_process` 的 docstring 写着「第一版每次现算，这里如实回『还没生成』」，函数体直接 `return {"plan": None, ...}`；成本侧同理
   （`main.py:7569`）。而 2.1 面板每次 `load()` 都读它 → **刷新页面/重新选中零件，刚跑出来的工艺与成本就没了**。
2. **依据路由不存在**：前端 `inline-analysis.js:116 loadLibrary()` 固定去读
   `${endpointBase}/${mode}-lookup`（process → `process-lookup`，cost → `cost-lookup`）。
   技术 IR 那条链给图纸零件必然 404（`main.py:1946` 的 `get_process_lookup` 要求 `store.load_ir`），
   包装零件这侧**没有注册**这两个路由 → "依据/检索报告"面板永远空。
3. **进度文案与产出不一致**：`main.py:7445` 上报的是 `coverage['summary']['total']`（**库内**沿用/缺失计数），
   而实际产出在 `plan.steps`。实测同一件零件：进度说"共 0 道工序"，结果是 4 道工序
   （`plan.overall_note` 说明"AI 未返回结构化工序明细，系统已按零件分类与几何特征补齐通用工序骨架"）。
   用户看到"0 道工序"会以为这一步没结果。

## 2. 口径

### 2.1 结论落库、可读回（版本化）

单件工艺与单件成本各落一份**版本化文档**（沿用 `packaging_parts.persistence` 的
`get_doc/put_doc` 范式，与零件文档同一套），字段至少：

- 工艺：`engine_version` / `plan` / `validation` / `coverage` / `part_code` / `parts_id` /
  `source`（`{"task_id", "computed_at", "actor"}`）；
- 成本：`engine_version` / `analysis` / `summary` / `part_code` / `parts_id` / `source`。

命名契约（红测与实现共用，不得改名）：

```
packaging_parts.DOC_KEY_PROCESS = "packaging_part_process"
packaging_parts.DOC_KEY_COST    = "packaging_part_cost"
packaging_parts.save_part_process(project_id, payload) -> dict     # 返回落库后的文档
packaging_parts.load_part_process(project_id, part_code) -> dict   # 没有就 {}
packaging_parts.save_part_cost(project_id, payload) -> dict
packaging_parts.load_part_cost(project_id, part_code) -> dict
```

同一 `(project_id, part_code, parts_id, 结论内容)` 重复落库必须**幂等**（不新增版本）。

`GET …/{part_code}/process` / `…/cost` 读**最近一版**；没有版本时保持今天的
`{"plan": null}` / `{"analysis": null}` 空态（不许 404、不许报错）。
任务成功时写版本；`POST` 的幂等键与既有 `_task_key` 口径不变。

### 2.2 两个依据路由必须存在且同形状

`GET …/{part_code}/process-lookup` / `…/cost-lookup` 必须注册，返回与既有技术 IR 同形状的
报告（`process_lookup` / `cost_lookup` 的服务层函数，逐字复用，不新写一套），
数据源改用零件文档（`packaging_parts.as_ir_part()` 已经把零件适配成 IR `Part`）。
未跑过时返回 `{}`（前端已有"空对象当没有依据"的分支）。

### 2.3 进度文案必须与最终产出一致

进度上报必须让"工序数"指的是**这次产出的工序数**：`plan.steps` 的长度，
并且把"库内沿用 / 需新建"作为**第二个数字**说明，不许把库内计数当成工序数报。
最终产出还要在进度里带上 `overall_note`（模型没给结构化明细、系统补了通用骨架这类事实必须可见）。

### 2.4 不许放宽的

- `processability()` 的三道门槛（闭合轮廓 / 材料 / 厚度）一字不改；
- 任务状态机（`succeeded` / `failed` 的判定）与 `dedup_key` 口径不变；
- 结论仍然**不写技术 IR**（`store.save_ir` 只由技术链路写）。

## 3. 验收（红测逐条对应）

- A 组：落库/读回的形状与幂等（同输入同版本，不重复落新版）；
- B 组：空态与 404 的界线（没跑过 → 200 + null；零件不存在 → 404 + 码）；
- C 组：两个依据路由注册且同形状；数据源是零件文档（不调 `store.load_ir`）；
- D 组：进度文案（工序数 = 产出数；`overall_note` 可见）；
- E 组：回归护栏（`processability` 门槛不变、`test_packaging_parts_downstream_red` / `_gate_red` 全绿）。

## 4. 命令与期望

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_downstream_readback_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_downstream_red      # 20 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_downstream_gate_red # 17 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red      # 32 OK
```

## 5. 本批不做

- 不改单件工艺的模型提示词与工序骨架策略（模型返回空结构化结果这件事见
  `packaging-parts-output-quality.md`，本批只管"跑完读得回来"）；
- 不改面板的渲染组件（`inline-analysis.js` 只换数据源，渲染复用）。

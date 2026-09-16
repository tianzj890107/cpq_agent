# CAD 批量生成改成「逐件容错 + 部分成功」：Spec / 红测口径（第 1 批）

## 0. 一句话结论

现在 `/generate` 与 `/drawings` 把「整批」当成了一个布尔：**只要有一个零件过不了几何预检，
整批任务直接抛异常、一件都不生成**。线上项目 `7393f6a00ccc`（电池图纸案例 1.png，5 个零件）
因为 `P-005 输出线缆组件`（型号 `XT30`）的 `features=[]`，连续产生 6 次完全相同的失败任务，
`P-001 ~ P-004` 明明几何齐全却一张 3D / 2D 都拿不到。

底层本来就是逐件容错的：`geometry.generate_part()` / `drawing2d.generate_drawings()` 返回
`ok=True/False + error`，`generate_all()` 只是对每个零件调用一次。**被上层接口自己封死了。**

本批（第 1 批）只解决「一票否决」这一个问题；零件级几何补录、零件分类、版本粒度、成本 / 工艺
批量语义分别是第 2 ~ 5 批（见第 8 节）。

## 1. 复现（线上实证，红测按同一形状构造）

- 项目：`7393f6a00ccc`，图纸：`电池图纸案例1.png`，零件数：5。
- `P-001 box 108×56×13.25`、`P-002 box 108×56×13.25`、`P-003 plate 90×40×1.6`、
  `P-004 box 70×40×10` —— 都有可生成的基体。
- `P-005 输出线缆组件`，型号 `XT30`，`features=[]` —— 没有基体特征。
- 结果：`CAD 几何预检未通过：零件 P-005（输出线缆组件）: 缺少基体特征（plate / box / cylinder）`，
  6 次任务全部 `failed`，`P-001 ~ P-004` 的 STEP/STL/SVG/DXF 一个都没有。

现场代码位置（本批要改的 4 处）：

| 位置 | 现状 |
| --- | --- |
| `tech_app/backend/main.py:1579`（`/generate` 的 `job()`） | `issues = preflight_parts(ir.parts)`；有任意一条就 `raise RuntimeError` |
| `tech_app/backend/main.py:1611`（`/drawings` 的 `job()`） | 同上，整批 2D 一起被否 |
| `tech_app/frontend/app.js:1099`（`autoGenerateAfterParse()`） | `if (!geometryOk) return false;` —— 3D 布尔失败即停 2D |
| `tech_app/backend/services/tasks.py:124`（`_run()`） | 终态只有 `succeeded` / `failed`，没有「部分成功」 |

## 2. 术语与状态

### 2.1 批量终态词汇（P1-7）

任务终态统一为：`succeeded` / `partial` / `failed` / `cancelled` / `stale`（本批只新增 `partial`，
其余沿用既有 `interrupted` 等既有语义，不新造）。

`partial` 必须带：`total`、`succeeded`、`failed`、`skipped`、`skipped_parts`（零件号列表）。

**计数口径（本批的硬口径，实现与测试都按这一条）：**

- `succeeded`：CAD 真的生成成功（`ok=True`）。
- `skipped`：**预检**就判定「这个零件缺人工输入」而没有调用 CAD（`features=[]`、基体尺寸缺失/非正…）。
- `failed`：过了预检、但 CAD 运行期失败（内核报错、导出失败…）。
- 恒等式：`succeeded + failed + skipped == total`。

> 为什么不把「缺尺寸」算 `failed`：它不是运行失败，是**等人补参数**，重试按钮对它没有意义
> （P1-8）。UI 要显示「补充参数」而不是「再次生成」。

### 2.2 结构化预检问题（P1-3）

预检问题不能再是字符串列表，必须是可判定对象：

```json
{
  "part_id": "P-005",
  "part_name": "输出线缆组件",
  "scope": "geometry",
  "code": "BASE_FEATURE_MISSING",
  "field": "features[0]",
  "severity": "blocking_for_part",
  "repair": "initialize_base_feature",
  "message": "缺少基体特征（plate / box / cylinder）"
}
```

`severity` 取值：`blocking_for_part`（只挡这个零件）/ `blocking_for_batch`（挡整批）。
`repair` 取值：`initialize_base_feature` / `fill_base_feature_dimensions` /
`replace_base_feature` / `fill_feature_dimensions` / `regenerate` / `install_cadquery` / `none`。

本批要求实现的 code（`message` 文案允许微调，`code` 与 `repair` 必须一致）：

| code | 触发条件 | severity | repair |
| --- | --- | --- | --- |
| `BASE_FEATURE_MISSING` | `features == []` | `blocking_for_part` | `initialize_base_feature` |
| `BASE_FEATURE_INVALID_TYPE` | 第 1 个特征不是 plate/box/cylinder | `blocking_for_part` | `replace_base_feature` |
| `BASE_FEATURE_DIMENSION_MISSING` | 基体必填尺寸为 `None` | `blocking_for_part` | `fill_base_feature_dimensions` |
| `BASE_FEATURE_DIMENSION_INVALID` | 基体必填尺寸 `<= 0` | `blocking_for_part` | `fill_base_feature_dimensions` |
| `FEATURE_DIMENSION_MISSING` | 第 2+ 个特征必填尺寸为 `None` | `blocking_for_part` | `fill_feature_dimensions` |
| `FEATURE_DIMENSION_INVALID` | 第 2+ 个特征必填尺寸 `<= 0` | `blocking_for_part` | `fill_feature_dimensions` |
| `PART_ID_DUPLICATE` | `part_id` 重复 | `blocking_for_part` | `none` |
| `CAD_GENERATION_FAILED` | 预检通过、CAD 运行期失败 | `blocking_for_part` | `regenerate` |
| `CADQUERY_UNAVAILABLE` | 内核整体不可用 | `blocking_for_batch` | `install_cadquery` |

现有 `geometry.preflight_parts(parts) -> List[str]` 是 `vision.py`、`regenerate` 路由的既有依赖，
**保留签名与返回类型**（内部改为基于逐件结构化结果拼字符串），避免本批顺带改掉别的环节。

### 2.3 兼容接口

- `/api/projects/{project_id}/generate`、`/api/projects/{project_id}/drawings`、
  `/api/projects/{project_id}/parts/{part_id}/regenerate`、`/api/projects/3d` 路由必须全部保留。
- 几何 / 2D 结果的既有字段（`part_id`、`name`、`ok`、`volume_mm3`、`mass_g`、`bbox`、`warnings`、
  `error`、`step_url`、`stl_url`、`views`、`dxf`、`source_ir_hash`）一个都不能少；
  本批只**新增** `status` / 计数 / `skipped_parts` / `blocked` / 逐件 `issues`。
- `geometry.generate_part()` 的 `ok` 语义、`store.save_geometry_result()` /
  `store.save_drawings_result()` 的文档形状不变。

## 3. 契约

### C1 —— 3D 逐件预检：能生成的先全部生成

`POST /api/projects/{pid}/generate`：

1. 预检**同步**跑完（纯本地、不调模型、不调 CAD），按 `part_id` 分组：
   `processable`（无问题）/ `blocked`（有问题）。
2. 只有 `processable` 的零件进异步任务；被 block 的零件**不调用 CAD**，原因原样进结果。
3. 任务结果：`status="partial"`（有 block 或运行期失败、但至少一件成功）、
   `total/succeeded/failed/skipped/skipped_parts`，`parts[]` 覆盖**IR 中全部零件且保持 IR 顺序**，
   被 block 的零件条目带 `ok=false`、`skipped=true`、`error`、`issues=[结构化问题]`。
4. 落库的 `geometry` 文档与返回的 payload 同形（成功项落盘、跳过项带原因落盘），
   这样重进项目 / 重载看板还能看到「4 成功 + 1 待补」。

提交响应（同步，HTTP 200）：

```json
{
  "task_id": "…",
  "status": "queued",
  "total": 5,
  "processable": 4,
  "blocked": [ {完整结构化问题} ]
}
```

`processable == 0` 时：`{"task_id": null, "status": "failed", "total": 5, "processable": 0, "blocked": [ …5 条… ]}`。

### C2 —— 2D 与 3D 同一套逐件模型，且**不要求 3D 全成功**

`POST /api/projects/{pid}/drawings` 采用与 C1 完全相同的判定与返回形状。
2D 从 IR 重建实体（`drawing2d.generate_drawings()` → `geometry.build_solid()`），**不读上一阶段
保存的 3D 文件**，因此不存在「必须 3D 全成功才允许 2D」这个依赖。
逐件口径与 `status=partial` 与 3D 一致。

### C3 —— 整批失败只剩 5 种（其余一律不允许整批失败）

只有这几种情况任务才是 `failed`（且不得被降级成 `partial`）：

1. `geometry.GeometryUnavailable` / `drawing2d` 内核整体不可用（`CADQUERY_UNAVAILABLE`）；
2. 项目不存在或没有 IR（沿用既有 404）；
3. 任务运行期间 IR 被并发修改（沿用 `_assert_ir_unchanged` 的既有判定）；
4. 所有零件都被挡（`processable == 0`，`succeeded == 0`）；
5. 文件系统 / 存储等基础设施异常。

单件预检失败、单件 CAD 运行失败、单个零件缺尺寸 —— **都不是**整批失败的理由。

### C4 —— 任务终态支持 `partial`

`tasks._run()` 在任务函数返回的结果自带 `status == "partial"` 时，把任务终态写 `partial`
（否则维持既有 `succeeded`）；异常路径仍是 `failed`。
`partial` 是终态：轮询方不得继续等待，也不得把它当失败。
前端四处轮询（`app.js` 的 `pollTask`、`cost.js`、`process.js`、`inline-analysis.js`）与
会话进度卡（`agent-chat.js`）都要把 `partial` 当终态；进度卡状态词给「部分完成」，
卡片类名沿用 `is-partial`（配色沿用 `is-interrupted` 的蓝色，不新增红色）。

### C5 —— 前端：结构化门禁，不再用单一布尔挡 2D

`autoGenerateAfterParse()` 的判定规则：

- 基础设施失败（HTTP 503 / 任务 `failed` / IR 并发变更）→ 停止 2D；
- `processable === 0`（没有任何零件能进 CAD）→ 不发起 2D（没有输入，不产生空任务）；
- **≥ 1 个零件可生成 → 继续跑 2D**，哪怕 3D 里有零件被跳过。

因此 `generateGeometry()` / `generateDrawings()` 的返回值从布尔改成结构化摘要：

```js
{
  infrastructure_ok: true,      // 只有基础设施 / IR 级失败才是 false
  total: 5, processable: 4, succeeded: 4, failed: 0, skipped: 1,
  blocked: [ {part_id, code, message, repair, …} ]
}
```

`runTask()` 遇到「提交响应里没有 `task_id`」（同步判定整批不可执行）时不得去轮询 `null`，
直接把该响应交给调用方按 `blocked` 处理。

### C6 —— UI：成功项立即可看，跳过项就地可辨

- `generateGeometry()` 完成后仍调用 `showGeneratedResult()`（成功的 `P-001 ~ P-004` 立即进 3D 视图，
  不能因为 `P-005` 缺失而整屏为空）。
- 状态行必须同时给出成功数与待补数（形如 `几何生成完成（4/5 件）：4 成功、1 待补`）；
  2D 同理。
- 零件清单每行：`g.ok` → `几何✓`；`g.ok === false` 且带 `issues` → 显示「待补参数」之类标记，
  而不是笼统的 `几何✗`。
- 被挡零件要能一键定位补参数（本批先给「补充参数」入口文案，实际补录能力在第 2 批）。

### C7 —— 确定性失败不再反复产生异步失败任务（P1-8）

预检不调模型也不需要 CAD，必须在提交异步任务之前同步完成：

- 被挡零件不进任务 → 同一个 `P-005` 不会每次都产生一条 `failed` 任务记录；
- 同一份 IR 连续点 N 次，`generate` / `drawings` 的 `failed` 任务数为 0（不允许出现
  「同一确定性缺字段 → 反复失败任务」）；
- `processable == 0` 时连任务都不提交（无 `task_id`），响应直接给 `status="failed"` + `blocked`。

### C8 —— 不放松任何既有门禁

单零件重生成 `/parts/{part_id}/regenerate` 的预检 409 语义、权限校验（`_require` + 本人项目守卫）、
`source_ir_hash` 与 `_assert_ir_unchanged` 的并发保护、CadQuery 缺失时的 503、对象存储同步
（`store.sync_geometry`）、审计（`store.audit`）全部保持。
本批只是把「整批一票否决」换成「逐件判定」，不新增任何绕过路径。

## 4. 本批**不做**（留给后面批次，红测也不覆盖）

- P0-4 / P0-5：`features=[]` 时页面出现基体类型选择器、`initialize_base_feature` 服务能力、
  Agent 询问尺寸、上传 STEP、「该零件不需要 CAD」。
- P1-1：解析阶段 `blocked` 改成逐件 `cad_ready / cad_pending / cad_not_required`。
- P1-2：`quantity` 不再挡几何（本批保持既有行为，避免和逐件语义混在一批）。
- P1-4 / P1-5：`source_part_hash` 与字段级失效表。
- P1-6：成本「一键测算全部」逐件继续、「仅重试失败项」。
- 第 3 批零件分类（`cad_requirement` / `make_or_buy`）。

## 5. 验收（红测清单）

红测文件：`tests/test_tech_cad_batch_partial_generation_red.py`
（真实 CadQuery + `TestClient` + 临时 `DATA_DIR`，不联网、不碰线上数据；本机解释器为
`open-claude/.venv/bin/python`）。

1. 5 个零件、1 个 `features=[]`：其余 4 个 3D 必须生成成功（任务 `partial`）。
2. 同一场景 2D 同样生成 4 张。
3. 结果里 `P-005` 必须带**结构化**失败原因（`code=BASE_FEATURE_MISSING`、
   `repair=initialize_base_feature`、`severity=blocking_for_part`）。
4. 结果 `parts[]` 覆盖 IR 全部 5 个零件且保持顺序，既有字段不缩水。
5. 计数恒等式 `succeeded + failed + skipped == total`，`skipped_parts == ["P-005"]`。
6. 成功的 4 件必须真的落库（`geometry` / `drawings` 文档里 4 件 `ok`）。
7. 全部零件都不可生成 → 整批 `failed`、无异步任务、`blocked` 5 条。
8. 同一份 IR 连续 3 次请求，`failed` 任务数为 0（不再堆重复失败任务）。
9. 基础设施失败（内核不可用）仍是整批失败，不得误报 `partial`。
10. IR 并发变更仍让任务失败（并发保护不被本批破坏）。
11. 单件 CAD 运行期失败（其余成功）→ `partial`，且 `failed` 计数 +1。
12. 前端 `generateGeometry()` / `generateDrawings()` 返回结构化摘要（含 `infrastructure_ok`）。
13. `autoGenerateAfterParse()` 只挡基础设施 / IR 级失败与 `processable === 0`；
    部分成功必须继续跑 2D。
14. `pollTask`（`app.js`）与 `agent-chat.js` 把 `partial` 当终态；进度卡有 `partial` 状态词。
15. `tasks._run()` 支持 `partial` 终态。
16. 成功件立即可看：`showGeneratedResult()` 仍被调用；状态行含成功数与待补数；
    零件行对被挡零件显示「待补」标记。

同时把 `tests/test_tech_step_primary_and_drawing_entry_cleanup_red.py::test_parse_triggers_automatic_generation`
里那条**已经过时**的断言（「几何失败必须挡住 2D：先判断几何结果再继续」）改成：
只有基础设施 / IR 级失败才挡 2D，单件预检失败不得阻断其他零件。

## 6. 允许修改范围

- 后端：`tech_app/backend/services/geometry.py`（逐件结构化预检 + 批量计划）、
  `tech_app/backend/services/drawing2d.py`（如需对齐逐件入口）、
  `tech_app/backend/services/tasks.py`（`partial` 终态）、
  `tech_app/backend/main.py`（`/generate`、`/drawings` 两个路由与 `_geometry_payload` /
  `_drawings_payload`）。
- 前端：`tech_app/frontend/app.js`（`runTask` / `pollTask` /
  `generateGeometry` / `generateDrawings` / `autoGenerateAfterParse` / 零件行状态）、
  `tech_app/frontend/agent-chat.js` 与 `agent-chat.css`（`partial` 状态），
  以及需要使用 `partial` 的轮询 `cost.js` / `process.js` / `inline-analysis.js`。
- 允许在同一批内微调上述文件的既有注释与文案。

## 7. 禁止事项

- 不得把 `partial` 引入到与几何无关的任务语义里（例如把需求确认、报告审核改成 `partial`）。
- 不得删除或弱化任何既有门禁：权限、IR 并发保护、CadQuery 缺失 503、单零件重生成 409、
  审计、对象存储同步。
- 不得用「静默吞掉错误」实现容错：被挡零件必须带 `code` + `message` 落进结果与 UI。
- 不得让 `preflight_parts()` 的既有字符串返回退化成空实现。
- 不得改动 `/api/projects/3d` 导入链路、`regenerate` 路由的语义、几何 / 2D 结果既有字段名。
- 不得为了让测试变绿而删掉或放宽 `tests/test_tech_backend_capability_preservation_red.py`
  里冻结的路由集合。

## 8. 后续批次（只登记，不在本批实现）

- 第 2 批：`features=[]` 的基础几何初始化（plate/box/cylinder 选择器 + `initialize_base_feature`
  服务 + Agent 复用同一服务 + 「不需要 CAD」+ 单件重生成）。
- 第 3 批：零件分类 `cad_requirement` / `make_or_buy`；标准件、外购件、电气件、柔性件默认
  「待确认是否需要 CAD」，不再算失败。
- 第 4 批：`source_part_hash` 与字段级失效表（改数量不过期几何、改尺寸只过期该件）。
- 第 5 批：成本 / 工艺批量统一 partial 语义与「仅重试失败项」。

## 9. 测试命令

```bash
# 红测（红：改前必须失败）
./open-claude/.venv/bin/python -m unittest tests.test_tech_cad_batch_partial_generation_red -v

# 受影响的既有测试
./open-claude/.venv/bin/python -m unittest \
  tests.test_tech_step_primary_and_drawing_entry_cleanup_red \
  tests.test_tech_backend_capability_preservation_red -v

# 全量
python3 -m unittest discover -s tests -p 'test_*.py'
```

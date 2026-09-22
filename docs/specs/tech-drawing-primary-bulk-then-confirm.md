# Spec：2.1 主按钮改为「一键生成全部工艺推荐」在前、「确认解析结果」在后

状态：Spec + 红测（已实现）
红测：`tests/test_tech_drawing_primary_bulk_then_confirm_red.py`

## 背景（用户要求）

2.1 图纸解析现在的主按钮状态机是：未解析 → 「一键解析图纸」；**已解析就直接把「确认解析结果」
变成主按钮**，批量工艺推荐一直只是描边（aux）动作。

用户要求改成三段式，并且按钮清单里的顺序也照这个排：

1. 未解析 → 主按钮 = 「一键解析图纸」
2. 解析完成、零件还没生成工艺推荐 → 主按钮 = 「一键生成全部工艺推荐」
3. 全部零件都已有工艺推荐 → 主按钮 = 「确认解析结果」

也就是「一键生成全部工艺推荐」在清单里排在「确认解析结果」前面。

口径与 2.3 成本测算完全一致（`cost-review.js`：`runCostReview` 在没算全时是主按钮、
算全后 `confirmCostReview` 才是主按钮），不新造第二套主按钮机制。

## 现状（实测）

- `app.js` 动作注册：`parseDrawing`（order 10，`role: drawingParsed() ? "aux" : "primary"`）、
  `confirmDrawingResult`（order **15**，`role: drawingParsed() ? "primary" : "aux"`）、
  `runAllPartProcesses`（order **20**，`role: "aux"`，`getState` 只看 project/IR/busy）。
- 2.1 页面侧没有「工艺推荐是否已生成」的同步信号：既有能力只有单零件只读
  `GET /api/projects/{id}/parts/{part_id}/process`（`partHasExistingProcess()`），
  没有项目级的工艺清单接口；`getState()` 只能同步读，所以需要一个本地缓存。
- 父壳（`tech-workbench.js`）按快照的 `order` 升序排按钮、取唯一 `role === 'primary'`
  当主按钮；看板 `refreshState()` 会重发整份快照。

## 范围

- 只改 2.1 的主按钮状态机与清单顺序：`tech_app/frontend/app.js`。
- 不动：`parseDrawing` 的解析链路、`confirmDrawingResult` 的后端回读与既有闸门、
  `runAllPartProcesses` 的受控串行实现与逐件 `task-progress` 上报、`partHasExistingProcess`
  的只读语义、后端路由（仍只有单零件 `/parts/{part_id}/process`，不新增 process-all / bulk）、
  父壳的主按钮渲染与诊断逻辑、其它阶段。

## R1 主按钮三段式

- R1.1 `parseDrawing`：`order: 10` 不变；`role` 仍是「未解析 primary、已解析 aux」。
- R1.2 `runAllPartProcesses`：`order` 提到 `15`（排在确认之前）；`role` = 已解析且
  **尚未全部生成** 时 primary，否则 aux。
- R1.3 `confirmDrawingResult`：`order` 改为 `25`（排在批量之后、其余动作之前）；
  `role` = **全部生成** 时 primary，否则 aux；`visible: drawingParsed()` 与
  `enabled: true` 不变（没生成完也一直可点，点了由既有闸门给真实原因，不靠禁用挡人）。
- R1.4 三个表达式互斥且完备：任意状态下可见动作里恰好一个 primary —— 未解析只有
  `parseDrawing`；已解析未生成只有 `runAllPartProcesses`；全部生成只有 `confirmDrawingResult`。
- R1.5 清单顺序：`parseDrawing(10)` → `runAllPartProcesses(15)` → `confirmDrawingResult(25)`
  → 其余 2.1 动作（`modelLookup` 30 / `verify` 40 / `searchComponents` 50）不变。

## R2 「全部零件都已有工艺推荐」的判定

- R2.1 新增同步判定 `partsProcessComplete()`：必须同时满足「有解析结果」且
  **当前 IR 的每个零件** 都已在缓存里记为「已有工艺推荐」。函数内不得发请求（`getState()` 要同步）。
- R2.2 缓存 `processReadyParts`（按 `project:part` 记 key）由只读判定填充：
  - `partHasExistingProcess()` 命中时（自动展开路径、批量里的 skip 判定）；
  - 单件 / 批量生成成功之后（这一件确实已经有工艺推荐了）。
- R2.3 首次进入已有 IR 的项目时，按当前 IR 的零件各只读探一次
  （`probePartsProcessState()`），复用 `partHasExistingProcess()`，不新写第二份探测；
  按「项目 + 零件表」签名去重，同一份零件表不重复探。
- R2.4 探测/标记改变结论后必须 `refreshState()` 把新快照推给父壳：主按钮要**自己**翻面，
  不能等用户再点一次某个按钮才变。
- R2.5 探测协议是只读：只用 GET，不得 POST、不得触发生成、不得写库。

## 验收

- 红测：`tests/test_tech_drawing_primary_bulk_then_confirm_red.py`（先红后绿）。
- 既有断言更新 1 处：`tests/test_tech_drawing_toolbar_cleanup_and_process_auto_expand_red.py`
  的 `order: 15`（当时锁的是确认动作的旧序号）改为 `order: 25`，并注明「契约更新（主按钮
  三段式批次）」。
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'` 不新增失败。
- 浏览器：2.1 解析完成后左侧主按钮是「一键生成全部工艺推荐」；批量跑完（或探测到全部
  零件都已有工艺）后主按钮自动变成「确认解析结果」，清单顺序同上。

# 包装图纸零件：可选中的零件与右栏零件面板（第 2 层）

血缘：承接第 1 层 `docs/specs/packaging-parts-true-outline.md`（真实轮廓）。
本层解决**"看得见但点不动"**。

## 0. 一句话目标

2.1 左栏的图纸零件行变成**可选中**的；选中后右栏出现**零件面板**：轮廓（后端给的坐标）、
尺寸/面积/图层/角色/证据，以及**上游为什么给出这个结论**。右栏不再用"3D 视图 · 选择零件后查看"
这句无法兑现的承诺。

## 1. 现状缺口（代码事实）

- `tech_app/frontend/app.js` 的 `renderTree()` drawing_flow 分支（约 2010–2023 行）只 `appendChild` 了行：
  该分支内 `dataset.partId` / `addEventListener` / `selectPart` **出现 0 次** → 点零件毫无反应。
- 右栏 `section.drawing-model-column`（`index.html:190`）只有 `#viewer`（THREE 画布）、`#partDetail`、
  `#parameterEditor`；图纸项目下 `currentIR` / `currentGeometry` / `currentDrawings` 全是 `null`
  （图纸链路把 CAD IR 写进自己那份文档，不回写 `store.load_ir()`），所以右栏永远停在
  `从「零件清单」中选择一项…` 的占位与空白 3D 画布。
- 没有任何"按零件取几何"的只读接口：可读的只有 `GET /drawing-flow`（flow/gates/stale/inheritance/
  preconditions，**不含**实体）与 `GET /requirement/packaging-parts`（零件文档，**不含坐标**）。

## 2. 后端：单件详情只读面

新增（`tech_app/backend/main.py`）：

```python
PACKAGING_PART_READ_PATH = "/api/projects/{pid}/requirement/packaging-parts/{part_code}"
```

- 纯读（**不判写权限**，与 `packaging-parts` 列表口径一致）。
- 数据源：零件文档（`packaging_parts.load_parts`）+ 该件在文档里的 `outline`（第 1 层新增的
  `points` / `entity_ids` / `area_mm2` / `bbox`）。
- 响应形状（**不套壳**，键名固定）：

```json
{
  "found": true, "built": true, "part": { /* 零件文档里的那一件，原样 */ },
  "outline": { "status": "closed", "reason": "", "points": [[x, y]],
               "bbox": [...], "area_mm2": 5000.0, "entity_ids": ["..."],
               "approximation": "arc_endpoints"? },
  "component_bbox": [x0, y0, x1, y1],
  "evidence": [ {"ref": "ev:E:B1", "kind": "entity", "layer": "CUT", "note": "..."} ],
  "size_source": "closed_outline",
  "summary": { "part_total": 64, "closed_total": 12, "closed_ratio": 0.1875 }
}
```

规则：

- **只回这一个零件的点**，不许把整份 IR 的实体吐出去（真图 6569 条实体）。
- 零件不存在 → `404`，错误码 `PACKAGING_PART_NOT_FOUND`，文案含 `part_code`。
- 零件文档还没生成 → `200` + `built: false` + `found: false`（不报错，让前端说"先跑一键解析"）。
- `outline.points` 归一到**零件自身坐标系**（相对 `bbox` 左下角），坐标系口径写进 `outline.origin`
  （`"part_bbox"`），前端不需要知道图纸绝对坐标。

## 3. 前端：可点 + 右栏面板

1. **可点**：`renderTree()` 的 drawing_flow 分支给每行 `row.dataset.partId = part.part_code`，
   并绑 `click` → `selectPackagingPart(part.part_code)`；行获得 `.part-item` 的选中样式（复用
   `markSelection` 的 `#tree .part[data-part-id]` 选择器，不新增第二套高亮逻辑）。
2. **`selectPackagingPart(partCode)`：新函数，不许复用 `selectPart`**（后者绑定视觉链路
   `currentGeometry` / `currentDrawings`，会在图纸项目下把右栏清空）。职责：
   - 记 `currentSelectedPanelPart`（新变量）；
   - 左侧高亮 + 展开该行子动作（`togglePartSubActions` 对无 `partId` 的行要安全返回）；
   - `setRightPane("model")` 保持右栏可见，然后**显示** `#packagingPartPanel`、隐藏 `#viewer` / `#partDetail` / `#parameterEditor`；
   - `fetch` 单件详情 → 渲染；失败时面板内给可读错误，不留白。
3. **面板内容**（`index.html` 的 `#modelPanes` 内新增，默认 `hidden`）：
   - `#packagingPartTitle`：`DWG-P01 图纸零件 P01`；
   - `#packagingPartOutline`：**SVG**，用 `viewBox` 直接吃后端给的点（前端只做 `viewBox` 字符串拼接，
     **不许**在 JS 里做几何求解：不许出现 `polygon_area` / 鞋带 / 环搜索）；`closed` 用实线，
     `open` 用虚线（并同时显示包围盒矩形作为参考）；
   - `#packagingPartFacts`：展开长×宽、面积、面积口径（`size_source` 的人话）、图层、角色、
     分量 bbox、`repeat_of`；
   - `#packagingPartEvidence`：证据列表（`ref / kind / layer / note`）。
4. **三种状态的文案（必须一一对应，不许混用）**：

| `outline_status` | 面板标题下的说明 |
| --- | --- |
| `closed` | 轮廓已闭合：尺寸与面积为真实轮廓口径。 |
| `open` | 未找到闭合轮廓，以下尺寸来自分量包围盒，仅供估算（原因：`no_closed_loop` / `loop_too_small`）。 |
| `unavailable` | 图纸单位未确认，不给出尺寸；请先确认单位后再解析。 |

5. **不再承诺 3D**：图纸项目下 `#viewerPartName` 的文案改为"图纸零件 · 选中后看轮廓与证据"
   （视觉链路保持原文案不变）。`#viewer` 在图纸项目下隐藏，避免留一块空白 3D 画布。

## 4. 允许修改范围

1. `tech_app/backend/main.py`：新增单件只读路由（含 `PACKAGING_PART_READ_PATH` 常量与 404 码）。
2. `tech_app/frontend/app.js`：`renderTree()` 的 drawing_flow 分支加可点绑定；新增
   `selectPackagingPart()` 与面板渲染函数；`#viewerPartName` 文案按入口分级。
3. `tech_app/frontend/index.html`：`#modelPanes` 内新增 `#packagingPartPanel`（默认 `hidden`）。
4. `tech_app/frontend/drawing-flow.css`（或既有样式文件）：面板样式；**不得新增设计变量**。

## 5. 禁止事项

- 不许前端做几何解析（环、面积、相交、坐标变换一律后端算好）。
- 不许改 `selectPart()`、`#viewer` 的初始化、`PART_FLOW_VIEWS` 既有视图、视觉链路任何行为。
- 不许把零件行接到 `currentIR` / `currentGeometry` / `currentDrawings`。
- 不许在右栏同时渲染"零件面板"和"零件详情"（两套内容互斥）。
- 不许改 `tests/` 下任何既有文件；不许改 `packaging_parts` / 语义层 / 成本口径。

## 6. 红测

`tests/test_packaging_parts_panel_red.py`（实现前必须失败）：

- A 后端：`PACKAGING_PART_READ_PATH` 常量存在且路由已注册；响应键集固定；404 码为
  `PACKAGING_PART_NOT_FOUND`；未生成零件文档时 `built:false` 且 `status 200`。
- B 可点：drawing_flow 分支出现 `dataset.partId` / `addEventListener("click"` /
  `selectPackagingPart`；`selectPart(` 在该分支出现 0 次。
- C 面板：`index.html` 有 `id="packagingPartPanel"`，位于 `#modelPanes` 内且带 `hidden`；
  `app.js` 有渲染函数并切到该面板。
- D 三态文案：`closed` / `open` / `unavailable` 三条文案都在源码里，且 `open` 文案含
  `包围盒`、`unavailable` 文案含 `单位`。
- E 禁止几何：图纸零件相关渲染路径里不出现 `polygon_area` / `shoelace` / `computeLoop`；
  出现 `viewBox`。
- F 口径：`app.js` 里存在按 `currentDrawingEntry === "drawing_flow"` 改写 `#viewerPartName`
  的代码，且该文案**不含** `3D 视图`。

## 7. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_panel_red -v     # 全绿
./open-claude/.venv/bin/python -m unittest tests.test_drawing_board_two_column_parts_and_3d_red  # 10 OK
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_frontend_wiring_red           # 12 OK
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_red                           # 54 OK(1 skip)
./open-claude/.venv/bin/python -m unittest tests.test_drawing_flow_parse_terminal_signal_red      # 30 OK
```

## 8. 与后续层的接口

- 第 3 层复用本层的"选中件"概念：面板上的「生成工艺推荐」「成本测算」按钮只在
  `outline_status == "closed"` 且材料/厚度已知时可点，其余置灰并说明原因。
- 第 4 层复用本层的 `#packagingPartPanel` 位置放"3D 预览"tab，不再新建右栏容器。

# 包装图纸零件：平板挤出与 3D 预览（第 4 层）

血缘：承接第 1 层（`outline.points` 真实轮廓）与第 3 层（材料/厚度前提）。
本层解决**"右栏那块 3D 画布永远是空的"**。

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_parts_3d_red.py`

## 0. 一句话目标

对**闭合轮廓 + 厚度已知**的零件做**直线挤出**（板件），导出 STL，复用既有 `#viewer` + `loadSTL`
在右栏看 3D；挤不出的件必须给 `unsupported` + 明确原因，**不许留一块空白画布**。

## 1. 现状缺口（代码事实）

- 图纸链路 8 步（`packaging_drawing_flow/model.py` 的 `STEP_IDS`）产物里**没有任何几何**
  （`file_preflight … downstream_prepare`，最远到 `parts_id/parts_total`）；
  Spec `packaging-dwg-parts-extraction.md:170` 已明写"不做 3D"——所以这不是 bug，是**未实现的能力**。
- 右栏 3D 走 `loadSTL(url)` → `geomFor()` → `currentGeometry`（`GET /api/projects/{pid}/geometry`，
  技术链路写的）。图纸项目下它恒为 null；且 `#viewer` 已初始化（挂着 `AxesHelper`），
  于是用户看到的是一条坐标轴的空白画布。

## 2. 本层明确不做什么（先写清楚，避免越界）

- **不做折弯成型**、不做盒体装配、不做真实刀模三维重建：只做"闭合轮廓 × 厚度"的**直线挤出**。
- **不新增链路步**（链路形状已冻结在 ## 231），按需算、按需落。
- 不引入新依赖（三角化自己实现，见 §3）。

## 3. 新服务 `packaging_part_solids`

文件：`tech_app/backend/services/packaging_part_solids.py`

```python
ENGINE_VERSION = "packaging-part-solids/1"
DOC_KEY = "packaging_part_solids"
STL_FORMAT = "ascii"
UNSUPPORTED_REASONS = ("outline_open", "outline_unavailable", "thickness_unknown",
                       "concave_polygon", "too_few_points", "too_many_points")
MAX_POINTS = 2000          # 超过就不挤（先保证确定性，不做简化）
CONVERT_TOLERANCE_MM = 1e-6
```

`extrude(row, *, options=None) -> dict`（**纯函数**）：

- 输入：第 1 层的 `row["outline"]["points"]`（零件自身坐标系）、`row["thickness_mm"]`；
- 门槛（**顺序固定**，先判门槛再算）：
  1. `outline_status != "closed"` → `unsupported: outline_open` / `outline_unavailable`；
  2. `thickness_mm` 为空或 `<= 0` → `unsupported: thickness_unknown`（**绝不许默认 2mm**）；
  3. 点数 `< 3` → `too_few_points`；`> MAX_POINTS` → `too_many_points`；
  4. 多边形非凸（用叉积符号不一致判定）→ `concave_polygon`（第一版不做耳切）；
- 三角化口径：底面按**扇形**（凸多边形下与耳切等价）→ `n-2` 个三角形；顶面同样 `n-2`；
  侧壁每边 2 个三角形 → `2n`；总计 `2n + 2(n-2)` 个三角形（矩形 4 点 = 12 个）。
- 输出：

```json
{"part_code": "DWG-P01", "status": "ok", "reason": "",
 "stl": "<ASCII STL 文本>", "triangles": 12,
 "bbox_mm": {"length": 100.0, "width": 50.0, "thickness": 2.0},
 "volume_mm3": 10000.0, "points": 4}
```

- STL 一律 **ASCII**：以 `solid ` 开头、以 `endsolid` 结尾，每面一个 `facet normal … endfacet`；
  `volume_mm3` 用**底面多边形面积 × 厚度**（§5 红测要按它核值）。
- `save_solids(project_id, doc)` / `load_solids(project_id)`：照
  `packaging_semantics/persistence.py` 的 `meta_backend.get_doc/put_doc` 范式做**版本化**。

## 4. 路由与前端

路由（`main.py`）：

```python
PACKAGING_PART_SOLID_PATH      = "/api/projects/{pid}/requirement/packaging-parts/{part_code}/solid"
PACKAGING_PART_SOLID_STL_PATH  = "/api/projects/{pid}/requirement/packaging-parts/{part_code}/solid.stl"
```

- `POST …/solid`：按当前零件文档现算并落一版；写权限引用 `packaging_match.BOX_MATCH_DECIDE_ROLES`；
  不可挤出时 **200** + `status: unsupported` + `reason`（这是"算得出结论"，不是错误）；
- `GET …/solid.stl`：返回 `application/sla`（STL 的正式 MIME）与 `Content-Disposition: attachment`；
  尚未生成 → 404 `PACKAGING_PART_SOLID_MISSING`。

前端（`app.js` + `index.html`）：

- 第 2 层面板里加「3D 预览」按钮；点击后**复用 `#viewer` 与既有 `loadSTL()`**（不新建画布、
  不新建第二套 THREE 初始化）；
- `status == "unsupported"` 时把原因翻成人话显示在面板里（`outline_open` → "该件没有闭合轮廓，
  无法挤出"；`thickness_unknown` → "缺厚度，无法挤出"；`concave_polygon` → "凹多边形本版不支持"）；
- **没有 3D 能力的旧项目行为一个字都不能变**（视觉链路/技术链路的 3D 照旧）。

## 5. 允许修改范围

1. 新建 `tech_app/backend/services/packaging_part_solids.py`。
2. `tech_app/backend/main.py`：两个新路由 + 常量 + 404 码。
3. `tech_app/frontend/app.js` / `index.html` / `drawing-flow.css`：面板 3D 按钮与 unsupported 文案。
4. 允许 `tech_app/tools/` 下加只读自检（可选）。

## 6. 禁止事项

- 不许改 `#viewer` / `initViewer()` / `loadSTL()` 的既有行为；
- 不许改技术侧 `project.geometry` 文档形状与既有生成链路；
- 不许引入新依赖（`numpy` / `trimesh` / `shapely` 一律不许；三角化自己写）；
- 不许默认厚度、不许用包围盒代替轮廓挤出；
- 不许把挤出结果写进 `store.load_ir()` / `save_ir()`；
- 不许改 `tests/` 下任何既有文件。

## 7. 红测

`tests/test_packaging_parts_3d_red.py`（实现前必须失败）：

- A 契约：常量与函数齐全，`UNSUPPORTED_REASONS` 逐字一致。
- B 矩形挤出：100×50×2 → `status=="ok"`、`triangles==12`、`volume_mm3==10000.0`、
  STL 以 `solid ` 开头并以 `endsolid` 结尾、含 12 个 `facet normal`。
- C 门槛：`open` → `outline_open`；`thickness_mm=None` → `thickness_unknown`；2 点 → `too_few_points`；
  点数超 `MAX_POINTS` → `too_many_points`。
- D 凹多边形 → `concave_polygon`（不许硬挤）。
- E 落库幂等：同输入 `save_solids` 两次只有一个版本号增长，`load_solids` 读回一致。
- F 路由：两个路由已注册；写权限引用 `BOX_MATCH_DECIDE_ROLES`；STL 路由给 `application/sla`。
- G 前端：面板有 3D 按钮；出现 `loadSTL`；三种 unsupported 人话文案都在；`initViewer` 源码块
  不含 `packaging`（既有 3D 行为未被改）。

## 8. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_3d_red -v      # 全绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_outline_red     # 第 1 层不回退
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_panel_red       # 第 2 层不回退
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_downstream_red  # 第 3 层不回退
./open-claude/.venv/bin/python -m unittest tests.test_drawing_board_two_column_parts_and_3d_red  # 10 OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red    # 57 OK
```

## 9. 实现期回写（2026-09-21）

1. **E1 的固定项目 id（已改成每次新项目，断言未改）**：原文用 `"proj-solid"` 这个写死的项目名
   存两次，并断言"首次落库版本号必须是 1"。落库后端是**持久化**的（`tech_app/tech_data/<pid>/`，即启动器的 `DATA_DIR` 缺省根），
   所以这条只有在"这台机器从没跑过这个测试"时才成立 —— 第二次跑必然 `5 != 1`。
   实现期改为每次跑用 `proj-solid-<uuid4>`，期望值与断言一字未动。
2. **轮廓点归一**：`extract()` 给的环可能带连续重复点或"首尾同点"的收尾重复；`extrude()` 先做
   **只去重复、不改坐标**的归一（`CONVERT_TOLERANCE_MM`），再判门槛与点数。矩形仍是 4 点 → 12 面。
3. **真实样本实测**：`酒盒.dwg` 64 件里 1 件可挤出（DWG-P35，12 面 / 84729.3235 mm³），
   `圆盘盒.dwg` 9 件里 7 件可挤出；不可挤出的原因是 `thickness_unknown`（标注里没有这一件的料厚）、
   `outline_open`（没闭合）与 `concave_polygon`（凹多边形本版不挤）——都不是错误。
4. **前端复用点**：`packagingPartSolidPreview()` 在 `POST …/solid` 之后调**既有** `loadSTL()`，
   画布仍是 `initViewer()` 建的那一个；`initViewer()` 源码块内不出现 `packaging`（G3 守住）。

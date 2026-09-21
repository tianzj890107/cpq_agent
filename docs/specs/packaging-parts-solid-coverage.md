# 包装图纸零件：3D 挤出覆盖率（凹多边形 + 批量结论 + 真值指标）

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_parts_solid_coverage_red.py`

**取代**：`packaging-parts-3d-extrusion.md` §C/D 里「凹多边形本版不挤、必须显式拒绝」这一条。
本 Spec 起，凹件必须能挤出（耳切三角化）；该文件其余条款（ASCII STL、料厚不许默认、
`too_few_points` / `too_many_points`、落库版本化、路由与前端复用 `loadSTL`）全部继续有效。

血缘：`packaging-parts-3d-extrusion.md`（第 4 层）、`packaging-parts-downstream-acceptance.md`
（第 5 层 `solid_ok_ratio`）、`packaging-parts-outline-chaining.md`（闭合判定）、
`packaging-parts-material-attribution.md`（料厚来源）。三份是**因果链**：没有料厚和闭合，就不可能有覆盖率。

## 0. 一句话目标

真刀模零件的展开轮廓**绝大多数是凹的**（实测 51 件闭合件里 34 件凹、17 件凸），
而今天只挤凸件；更糟的是"覆盖率"这个数字根本没人算（`solid_ok_ratio` 恒 `0.0`）——
所以"零件能不能出 3D"既做不出来也说不清楚。本 Spec 要求：凹件能挤、整份零件文档能一次算完、
覆盖率必须是真值。

## 1. 现状缺口（34 上真跑 + 本机同代码实测）

| 事实 | 值 | 出处 |
| --- | --- | --- |
| `summarize().solid_ok_ratio` | `0.0` | `GET /api/projects/f1417060ae9d/requirement/packaging-parts` |
| 64 行零件里带 `solid_status` 的行 | 0 | 同上 |
| `DWG-P35` 单件挤出 | `ok`，12 个三角面 | `POST …/packaging-parts/DWG-P35/solid` |
| `DWG-P07` 单件挤出 | `unsupported:concave_polygon` | 同上 |
| `DWG-P01` 单件挤出 | `unsupported:outline_open` | 同上（闭合问题归 `packaging-parts-outline-chaining.md`） |
| 51 件闭合件的轮廓形状 | **34 凹 / 17 凸** | 本机 `is_convex()` 逐件统计 |

- 三角化是**扇形**（`packaging_part_solids._triangles`）+ 凸性门槛（`is_convex`）→ 凹件一律拒绝，
  覆盖率天花板 = 17/64 = 0.266；
- `extrude()` 只能逐件调用，**没有"整份零件文档一次算完"的入口** → 覆盖率算不出来；
- 所以面板上那个"3D 覆盖率 0%"既不是"都失败"，也不是"还没算"，是**没人算**。

## 2. 口径

```python
TRIANGULATION = "ear_clipping"
UNSUPPORTED_REASONS = ("outline_open", "outline_unavailable", "thickness_unknown",
                       "too_few_points", "too_many_points",
                       "self_intersecting", "degenerate_polygon")
def extrude_all(rows, *, options=None) -> {"parts": [...], "stats": {...}}
```

### 2.1 耳切三角化（取代扇形 + 凸性门槛）

- 凸件必须与今天**逐字同形同数**（`triangles == 2n + 2(n - 2)`，STL 面数与顶点坐标不变）；
- 凹件必须 `status == "ok"`，并给出 `out["triangulation"] == TRIANGULATION`；
  三角面数同样按 `2n + 2(n - 2)`（n = 去掉连续重复点后的轮廓点数）；
- 三角化必须自写（不许引入 `numpy` / `trimesh` / `shapely`）、必须确定性（同输入两次跑逐字相同）；
- 真自交（`bowtie` 这种边相交）→ `self_intersecting`；面积 ≤ 0 或点数不足 → `degenerate_polygon` /
  `too_few_points`；**都不许硬挤**。

### 2.2 批量产出（覆盖率的前提）

- `extrude_all(rows, options=...)` 是**纯函数**：返回 `{"parts": [逐件结论...], "stats": {...}}`，
  **不改入参**（返回的行是副本）；
- 逐件结论形状与 `extrude()` 一致（`part_code` / `status` / `reason` / `stl` / `triangles` / `volume_mm3`…），
  并**回写** `solid_status` / `solid_reason` 两个键；
- `stats = {"part_total", "ok_total", "unsupported_total", "solid_ok_ratio", "unsupported_reason_mix"}`，
  `part_total = 0` 时 `solid_ok_ratio = 0.0`（不许 `null`、不许抛错）。

### 2.3 覆盖率必须是真值

- `packaging_parts.summarize(doc)` 的 `solid_ok_ratio` 取行上的 `solid_status`（今天行上没有该键 → 恒 0.0）；
- 批量路由产出后，零件列表接口每行必须带 `solid_status` / `solid_reason`；
- 前端"3D 覆盖率"不许在没有任何结论时显示 0%（要显示"未生成"），更不许把 `unsupported` 当 0 糊过去。

### 2.4 路由

```python
PACKAGING_PARTS_SOLIDS_PATH = "/api/projects/{pid}/requirement/packaging-parts/solids"
```

- `POST` 批量挤出并把结果落 `packaging_part_solids`（复用第 4 层 `save_solids()`）；
- 写权限**直接引用** `packaging_match.BOX_MATCH_DECIDE_ROLES`（不另抄一份）；
- 失败不阻断：单件 `unsupported` 是结论，不是错误；整批失败才给稳定错误码。

### 2.5 前端文案

unsupported 文案闭集里**删掉**「凹多边形本版不支持」，新增「轮廓自交」「轮廓退化」；
`odd_endpoints` / `outline_open` 的文案归 `packaging-parts-outline-chaining.md`。

## 3. 门槛（`酒盒.dwg`；改门槛必须改本文件）

| 门槛 | 值 | 今天 |
| --- | --- | --- |
| `solid_ok_ratio` | `>= 0.70` | 0.0（仅凸件天花板 0.266） |
| `concave_ok_total` | `>= 30` | 0 |
| 凸件回归 | 每个凸件的 `triangles` 与今天一致（`2n + 2(n-2)`） | — |
| 逐件结论 | 每件都有 `status ∈ {ok, unsupported}` 且 `reason` 在闭集里 | 无批量入口 |

门槛成立依赖前两份 Spec：料厚（`packaging-parts-material-attribution.md`）与闭合
（`packaging-parts-outline-chaining.md`）。单独实现本 Spec 时门槛必然不达标 —— 那是依赖没做，不是门槛错。

## 4. 允许修改范围

1. `tech_app/backend/services/packaging_part_solids.py`：耳切三角化、`TRIANGULATION` 常量、
   新 `UNSUPPORTED_REASONS` 闭集与自交/退化判定、`extrude_all()`、`stats`。
2. `tech_app/backend/services/packaging_parts.py`：`summarize()` 的 `solid_ok_ratio` 读行上 `solid_status`。
3. `tech_app/backend/main.py`：新增 §2.4 批量路由；零件列表/详情接口透出 `solid_status` / `solid_reason`。
4. `tech_app/frontend/app.js`：§2.5 文案（删「凹多边形」、加「自交」「退化」、"未生成" 三态）。
5. `tests/test_packaging_parts_3d_red.py`：**本 Spec 修订三处**（A1 的 `UNSUPPORTED_REASONS` 字面、
   D1 的凹件期望、G2 的文案闭集）——由本 Spec 作者同步改，实现方不许再动。
6. `changelog/changelog_9_21_25.md`：按周记录。

## 5. 禁止事项

- 不许改 `packaging-parts-3d-extrusion.md` 的其他条款与其余红测断言；
- 不许给缺失的料厚默认值（`thickness_unknown` 仍然是拒绝，绝不许默认 2mm）；
- 不许用"把凹件当凸件硬挤"或"凸包近似"来提覆盖率；
- 不许引入 `numpy` / `trimesh` / `shapely`；
- 不许改 `MAX_POINTS`、`ENGINE_VERSION`、`DOC_KEY`、`STL_FORMAT`、既有单件路由的路径与权限；
- 不许在 `packaging_part_solids` 里调模型、联网或读库；
- 不许 commit / push / tag / Release / 部署。

## 6. 红测

`tests/test_packaging_parts_solid_coverage_red.py`（实现前必须失败）：

- A 常量：`TRIANGULATION`；`UNSUPPORTED_REASONS` 不含 `concave_polygon`、含 `self_intersecting` /
  `degenerate_polygon`；`extrude_all` 可调用。
- B 耳切：凸矩形 12 面（回归）；L 形（6 点）`ok` + 20 面 + 体积 = 面积 × 料厚；U 形（8 点）`ok` + 28 面；
  bowtie → `self_intersecting`；三点共线 → `degenerate_polygon`；缺厚度仍是 `thickness_unknown`。
- C 批量：`extrude_all` 返回 `parts` + `stats`；`ok_total` / `unsupported_reason_mix` 正确；
  不改入参（入参行跑完仍没有 `solid_status`）；返回行带 `solid_status` / `solid_reason`；
  `part_total=0` 时 `solid_ok_ratio == 0.0`。
- D 真值指标：`summarize()` 的 `solid_ok_ratio` 反映行上的 `solid_status`（今天恒 0.0）。
- E 路由与前端：`main.py` 有新批量路由且引用 `BOX_MATCH_DECIDE_ROLES`；`app.js` 文案里有「自交」「退化」
  且不再把「凹多边形」当拒绝理由。
- F 真实样本门槛（本机有样本才跑）：§3 表格逐项断言。

## 7. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_solid_coverage_red -v   # 红→绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_3d_red -v               # 修订后不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_downstream_gate_red -v  # 不回归
```

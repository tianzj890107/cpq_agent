# 包装图纸零件：重复边折叠与外轮廓重判（"open 件"其实是我们没算完）

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_parts_outline_chaining_red.py`

血缘：承接 `packaging-parts-true-outline.md`（第 1 层：件内求最大闭合环）、
`packaging-dwg-parts-extraction.md`（零件提取）、`packaging-parts-downstream-process-and-cost.md`
（第 3 层 `PACKAGING_PART_NOT_CLOSED` 门槛）、`packaging-parts-material-attribution.md`（材料归属也只服务闭合件）。
本 Spec **不放宽**第 3 层门槛，只修"闭合判定"这一层的两个真问题：**重复边把环搜索撑爆** 与
**"没算出外轮廓"被当成"图纸没闭合"**。

## 0. 一句话目标

真刀模图里同一条边常被重复画（同一条线 2～4 份实体）。今天的环搜索把这些重复边当成**不同的边**，
分支爆炸后撞上计算预算提前中止，却对外报 `no_closed_loop`（"图纸没闭合"）——
把"我们没算完"说成了"图纸的结论"。本 Spec 要求：**折叠重复边**、**把预算中止说出来**、
并且只在**能证明是外轮廓**时才把原判 open 的件翻成闭合。

## 1. 现状缺口（本机同代码 + 34 上真跑，逐条可复现）

项目 `f1417060ae9d`（`酒盒.dwg`，`parts:b435f8c89cf9bbeb`）：64 件里 **13 件 `open`**，
`outline_reason` 全部是同一条 `no_closed_loop`，`closed_ratio = 0.797`。

本机同代码逐件实测（折叠前 / 折叠后）：

| 件 | 尺寸 | 实体数 | 唯一端点对 | 折叠前状态数 | 折叠前中止 | 折叠后环数 | 折叠后最大环 bbox | 折叠后 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `DWG-P56`～`P61`（6 件） | 89.2×273.9 | 64 | 31 | ≥ 20000 | **是** | 6 | 与分量一致 | 可判闭合 |
| `DWG-P01` | 443.5×492.6 | 90 | 49 | ≥ 20000 | **是** | 66 | 与分量一致 | 可判闭合 |
| `DWG-P08` | 222.1×492.6 | 57 | 30 | ≥ 20000 | **是** | 12 | 与分量一致 | 可判闭合 |
| `DWG-P02` / `P03` | 440.1×482.9 | 209 | 111 | ≥ 20000 | **是** | 12 | **小于分量** | 仍是开线 |
| `DWG-P09` / `P10` | 217.1×482.9 | 104 | 55 | ≥ 20000 | **是** | 6 | **小于分量** | 仍是开线 |
| `DWG-P64` | 88.4×260.5 | 5 | 5 | 0 | 否 | **0** | — | 真开线（6 个奇度顶点） |

三条结论：

1. **13 件全部是"预算中止"**（`MAX_LOOP_STATES=20000` 撞顶），没有一件是"图里真没有环"；
   根因是**重复边**：`DWG-P56` 64 条实体只对应 31 对唯一端点（重复度最高 4）。
2. 折叠重复边后，12 件都能找到环，其中 **8 件（P01 / P08 / P56～P61）的最大环 bbox 与分量 bbox 完全一致**
   —— 那就是外轮廓；剩下 4 件（P02/P03/P09/P10）的最大环明显小于分量 → 不能当外轮廓。
3. 只有 `DWG-P64` 是真开线（5 条实体、6 个奇度顶点、无环）。

## 2. 口径

```python
CHAIN_RULE_ID = "part_outline_chaining_v1"
EDGE_COLLAPSE_TOLERANCE_MM = 1.0     # = LOOP_TOLERANCE_MM；同一对量化端点之间的重复边视为同一条
OUTLINE_BBOX_COVER_RATIO = 0.95      # rescue 判据：环的 bbox 必须覆盖分量 bbox 的 95%
OUTLINE_OPEN_REASONS = ("no_curve_entity", "unit_unconfirmed", "loop_budget_exhausted",
                        "odd_endpoints", "loop_too_small")
```

### 2.1 重复边折叠（只作用于"找环"）

- 同一对量化端点（`_quant_key`）之间的多条边，在环搜索里**折成一条**，代表边取 `entity_id` 最小的那条
  （同一份 IR 两次跑必须逐字相同）；
- **折叠只影响找环**：`outline.entity_ids` 仍然列出该件里全部相关实体（证据可回查），
  `stats.collapsed_edge_total` 记录被折叠掉的边数。

### 2.2 外轮廓重判（rescue，只救"判错的件"）

- 折叠只改"找环"这一步；**已经 `closed` 的件一律不走 rescue**：本机实测 51 件已闭合件在折叠图上
  重找环，最大环与今天逐字一致（面积与点数完全相同）——它们必须保持原样，一个字都不许改；
- 只有"**判成 open**"的件才走 rescue：折叠后找到的**最大环**必须满足
  `环 bbox 覆盖分量 bbox >= OUTLINE_BBOX_COVER_RATIO`（每一边按 `LOOP_TOLERANCE_MM` 容差）才算外轮廓。
  依据：51 件已验收件里有 12 件的环只覆盖分量 bbox 的 49%～95%（那是内圈/局部环，不是外轮廓），
  所以覆盖率判据**只能当 rescue 的准入条件**，不能反过来重算已闭合件；
- rescue 成功 → `outline_status = "closed"`、`size_source = "closed_outline"`、并写
  `outline.compose = {"kind": "collapsed_cycle", "rule_id": CHAIN_RULE_ID, "edges_total": n,
  "edges_unique": m, "collapsed_total": n - m, "bbox_cover": 0.97}`；`stats.collapsed_rescue_total` +1；
- rescue 失败 → **保持 open**（尺寸口径一起保持），按 §2.4 给具体原因；
- `outline.compose` **只在 rescue 成功的件上出现**（没走 rescue 的件不许出现该键，便于核对"到底动了谁"）。

### 2.3 诊断（逐件留痕）

```python
def outline_diagnosis(members) -> dict
# {"edges_total": n, "edges_unique": m, "collapsed_total": n - m, "cycles_found": k,
#  "budget_exhausted": bool, "odd_degree_vertices": n, "nearest_gap_mm": float}
```

- 统计口径与 `_component_edges` 一致（端点按 `LOOP_TOLERANCE_MM` 量化）；
- `budget_exhausted` 表示环搜索在 `MAX_LOOP_CYCLES` / `MAX_LOOP_STATES` 处提前中止；
- `nearest_gap_mm` = 奇度顶点两两最近配对后的最大间隙（没有奇度顶点时 `0.0`）。

### 2.4 开线原因（闭集，按序判定；`no_closed_loop` 从代码里消失）

| 序 | 条件 | `outline_reason` |
| --- | --- | --- |
| 1 | 件里没有可用曲线/坐标 | `no_curve_entity` |
| 2 | 单位未确认（没有可信 mm） | `unit_unconfirmed` |
| 3 | 环搜索预算中止且 rescue 未通过 | `loop_budget_exhausted` |
| 4 | 存在奇度顶点（最近配对间隙 > `LOOP_TOLERANCE_MM`） | `odd_endpoints` |
| 5 | 找到环但最大环面积 < `min_area_mm2` | `loop_too_small` |

### 2.5 硬纪律

1. `no_closed_loop` 这个笼统值从代码里消失（源码不许再出现该字面）；每件必须能说出上面 5 个值里的哪一个。
2. **本版不做容差桥接**：断口 > `LOOP_TOLERANCE_MM` 就是开线（真样本最近可配对间隙 106～118mm，
   桥接只会造假）。以后要桥接，另开 Spec。
3. **不许为了凑 `closed_ratio` 硬接**：rescue 必须同时满足"折叠前 open"+"外轮廓 bbox 覆盖"两个条件。
4. open 件的尺寸口径不变：`size_source != "closed_outline"`（仍是 `component_bbox` / `dwg_outline`）。
5. 图框 / 排刀层照旧排除（第 1 层 `frame` 口径不变）。
6. 同一份 IR 两次跑逐字相同（含 `compose`、`outline_diagnosis`、`stats`）。

## 3. 指标与门槛

`packaging_parts.summarize(doc)` 新增：

| 指标 | 定义 |
| --- | --- |
| `open_reason_mix` | `{reason: n}`（只统计 open 件） |
| `collapsed_rescue_total` | rescue 转闭合的件数 |
| `budget_exhausted_total` | 诊断里 `budget_exhausted=true` 的件数 |

真实样本门槛（`酒盒.dwg`；改门槛必须改本文件）：

| 门槛 | 值 | 今天 |
| --- | --- | --- |
| `closed_ratio` | `>= 0.88`（实测折叠+rescue 后 59/64 = 0.922） | 0.797 |
| `collapsed_rescue_total` | `>= 6`，且每个 rescue 件都带 `compose.kind == "collapsed_cycle"` 与 `bbox_cover >= 0.95` | 0（无该指标） |
| `collapsed_edge_total` | `>= 30`（真图重复边量级） | 无该指标 |
| `budget_exhausted_total` | `== 0`（折叠后不应再有预算中止） | 13 |
| `open_total` | `1 <= n <= 7`（真图上确实有开线件，不许全绿） | 13 |
| open 件 `outline_reason` | 全部 ∈ `OUTLINE_OPEN_REASONS` 且 `!= "no_closed_loop"` | 13 件全 `no_closed_loop` |

## 4. 与其他 Spec 的边界

- 材料/厚度归属归 `packaging-parts-material-attribution.md`（那里只认 `closed` 这一事实）；
- 3D 挤出（含凹多边形）归 `packaging-parts-solid-coverage.md`；
- 第 1 层的 `LOOP_TOLERANCE_MM` / `OUTLINE_STATUSES` / `SIZE_SOURCES` / `MIN_LOOP_EDGES` 字面不许改。

## 5. 允许修改范围

1. `tech_app/backend/services/packaging_parts.py`：新增 §2 常量、`outline_diagnosis()`、环搜索的重复边折叠
   与预算上报、rescue 判据、§2.4 原因映射、`summarize()` 补 §3 指标、逐件写 `outline_diagnosis`。
2. `tech_app/backend/main.py`：零件详情/列表接口透出 `outline_diagnosis` 与 `outline.compose`（不新增路由）。
3. `tech_app/frontend/app.js`：面板里 open 件的灰按钮文案必须引用**具体** reason
   （`odd_endpoints` → 「这一件在图纸里没有闭合轮廓（缺口 > 1mm）」等）。
4. `changelog/changelog_9_21_25.md`：按周记录。

## 6. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测与第 1～5 层红测）；
- 不许改 `LOOP_TOLERANCE_MM` / `OUTLINE_STATUSES` / `SIZE_SOURCES` / `DEFAULT_OPTIONS` / `PART_CODE_FORMAT`
  / `MIN_LOOP_EDGES`；
- 不许放宽第 3 层 `PACKAGING_PART_NOT_CLOSED`（open 件仍不许算工艺/成本）；
- 不许为实现本 Spec 顺手重算已闭合件的轮廓（它们的 `outline` 必须逐字不变）；
- 不许靠调大 `MAX_LOOP_STATES` / `MAX_LOOP_CYCLES` 来让红测转绿（预算是共享资源，改预算必须改本 Spec）；
- 不许在 `packaging_parts` 里调模型、联网或读库；
- 不许 commit / push / tag / Release / 部署。

## 7. 红测

`tests/test_packaging_parts_outline_chaining_red.py`（实现前必须失败）：

- A 常量与闭集：`CHAIN_RULE_ID` / `EDGE_COLLAPSE_TOLERANCE_MM` / `OUTLINE_BBOX_COVER_RATIO` /
  `OUTLINE_OPEN_REASONS` 在位；`outline_diagnosis` 可调用。
- B 诊断：重复边环（64 实体 / 31 唯一对）给出正确 `edges_unique` / `collapsed_total`；
  真开线给出奇度顶点数与间隙；普通闭合件诊断里 `budget_exhausted=false`。
- C 折叠 + 护栏：重复边环必须 `closed`；"内圈 + 大外框"的件必须保持 `open`；真开线件必须 `open` +
  `reason == "odd_endpoints"`；已闭合件（含带重复边的矩形）不许出现 `compose`，点数也不许变。
  （rescue 的 `compose` 契约由 E 组真样本钉住：真图上那 8 件才是 rescue 的适用对象。）
- D 护栏：源码里不再出现 `no_closed_loop`；`MAX_LOOP_STATES` / `MAX_LOOP_CYCLES` 字面不变；
  open 件 `size_source != "closed_outline"`；第 3 层拒绝码不变；同一份 IR 两次跑逐字相同。
- E 真实样本门槛（本机有样本才跑）：§3 表格逐项断言。

## 8. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_outline_chaining_red -v   # 红→绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_downstream_gate_red -v    # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_downstream_red -v         # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red -v     # 不回归
```

## 9. 与既有红测的已知冲突（已上报；本批未改测试）

`tests/test_packaging_parts_outline_red.py::DDegrade::test_d1_open_component_says_so`
（第 1 层 `packaging-parts-true-outline.md`，已验收）断言逐字
`assertEqual(row["outline_reason"], "no_closed_loop")`；本 Spec §2.5 第 1 条要求该字面
**从源码里消失**（`tests/test_packaging_parts_outline_chaining_red.py::DGuards::test_d1`
直接扫 `packaging_parts.py` 源码）。两条断言**结构上不可能同时为真**；按本 Spec 落地后，
那条第 1 层用例由绿转红（相邻冻结面 451 条里只此 1 条）。

测试侧一行修法（本批**未**改测试，等测试侧点一下头）：

```diff
-        self.assertEqual(row["outline_reason"], "no_closed_loop")
+        self.assertIn(row["outline_reason"], packaging_parts.OUTLINE_OPEN_REASONS)
+        self.assertNotEqual(row["outline_reason"], "no_closed_loop")
```

该夹具是三条互不相接的线段（6 个奇度顶点、最近配对间隙 ≈100mm）→ 落地后的实际值为
`odd_endpoints`，落在本 Spec §2.4 的闭集里，语义更准（"断口 > 1mm" 而不是"没找到闭合环"）。
`PACKAGING_OUTLINE_REASONS` 前端文案表**保留** `no_closed_loop` 键（老零件文档里仍可能有该值，
`test_packaging_parts_panel_red.py::DStatusCopy` 也要求该字面在表里）——被禁的是"服务端只说得
出这个笼统值"。

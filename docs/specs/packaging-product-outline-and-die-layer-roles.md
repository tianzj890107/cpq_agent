# 规格：真实刀模图层名、拼版/图框排除与产品级盒型候选 —— DWG 支持第 4 批补充（第 4b 批）

> 批次：DWG 支持第 4 批的**现场补充批**（前置：`docs/specs/packaging-drawing-semantics.md`，已实现）。
> 红测：`tests/test_packaging_product_outline_red.py`。
> 真实样本：`裕同包装项目-待开发/酒盒.dwg`（sha256 `0991c8b0…93f3e0`）、
> `圆盘盒.dwg`（sha256 `4c70ce7b…4a531b`），**只读**。
> 本批目标：让两份真实刀模图产出**产品级**盒型候选与成品尺寸候选，使 2.1 图纸解析能看到零件、
> 报价能拿到可用尺寸；**不锁定盒型、不算成本、不改 UI、不改数据库**。

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_product_outline_red.py`

## 0. 为什么要补这一批（现场实测，9-21）

两份真实 DWG 已在 34 上跑通 drawing-flow（project `bf99bec0d274` / `ce9d5aae9631`，ODA 27.1 → DXF，
单位 mm 已确认），但 2.1 **看不到任何零件**，报价也拿不到成品尺寸。根因不是数据缺失，而是语义结论没用：

本地 libredwg 0.14 → ezdxf 复现（与 34 上 ODA 结果**逐值一致**）：

| 现场事实 | 酒盒.dwg | 圆盘盒.dwg |
| --- | --- | --- |
| 图层 / 实体 | 8 层 / 6569 | 32 层 / 6864 |
| 已识别角色 | 仅 `CUTTER` → `cut`（308 实体） | **0 个**（32 层全 `unknown`） |
| 真实刀线图层名 | `CUTTER`、`轮廓线`、`SAMPLE` | `全穿刀`、`压线 Crease` |
| 闭合轮廓 | 2 条（都在图层 `0`，1705.9508 × 713.2990，`repeated_groups` count=2） | 633 条（全部落在 `repeated_groups`） |
| 最大闭合轮廓 | 1705.9508 × 713.2990 | 15639.3728 × 6318.2803（＝`document.extents` 的宽度） |
| 圆（`kind=circle`） | 0 | 78 个，产品级 Ø404×4 / Ø401×2 / Ø399×2 / Ø396.6×2 / Ø389.6×6 … |
| 原生标注 | 316 条（219.6435 / 89.19 / 86.39925 / 267.94 / 124.9 …，**没有** 1705.95 / 713.30） | 141 条（403.99999 / 396.59999 / 193 / 48.5 …，拼版侧 11998 / 2655 / 2330） |
| `box_candidate_total` | **0** | **0** |
| 写成成品长宽的实测值 | `inner_length=1705.9507596530002`、`inner_width=713.2989662779999` | `inner_length=15639.372814358998`、`inner_width=6318.280258252999` |
| `outline.panel` | `multi_up=true`、`panel_count=2`、候选＝那 2 条闭合框 | `multi_up=true`、`panel_count=1290` |

**三条缺口**（每条都能用合成 CAD IR 独立复现，见 §8）：

- **D1 图层名规则只认英文且只做精确/前缀匹配**：`全穿刀`、`压线 Crease`、`图框层`、`排图层`
  在交付的 `packaging_layer_rules.json` 里没有任何条目能命中（`CUTTER` 只是恰好被 `name_prefix=["CUT"]` 撞上）。
- **D2 成品主轮廓取「所有闭合候选里面积最大的一个」**（`fields.py`：`closed[0]`）：
  真实图纸里面积最大的闭合候选就是**展开料/拼版框/整张图框**，于是展开料外框被写成成品长宽。
  两张图都错得**像一个合法尺寸**（1705.95×713.30、15639.37×6318.28），现场不核对根本看不出来。
- **D3 盒型候选的两条路径都拿不到产品级证据**：`round_tube` 要求「最大圆面积 > 最大闭合轮廓面积」
  （被拼版大框压死），`folding_carton` 要求「有 `crease` 图层 + 有闭合轮廓」（`压线 Crease` 没被识别）。

## 1. 契约 C1：图层角色必须认真实世界命名

1. 交付的默认模板（`tech_app/agent_knowledge/rules/packaging_layer_rules.json`，`rule_set` 仍为
   `packaging_layer_rules_v1`、`review_status=reviewed`）必须能命中下列**真实图层名**：

   | 图层名 | 必须的角色 | 来源样本 |
   | --- | --- | --- |
   | `全穿刀` | `cut` | 圆盘盒 |
   | `压线 Crease` | `crease` | 圆盘盒 |
   | `图框层` | `frame` | 圆盘盒 |
   | `排图层` | `frame` | 圆盘盒 |
   | `CUTTER` | `cut`（现状已命中，回归锚点） | 酒盒 |

   命中后 `role_source="rule"`、`evidence_level` 沿用规则（cut/crease/frame 均为 `STRONG`）、
   `matched_rule_id` 非空，且**不再进** `unresolved` 的 `no_rule_matched` 行。

2. **负向**（保底，防误判）：`DESIGN`、`SAMPLE`、`0`、`Defpoints`、`Make2D$可见线$普通线`、
   `1轮廓实线层`、`6文字层` 的角色**不得**是 `cut` 或 `crease`（可以是 `unknown`，也可以由客户模板显式配置）。
   理由：把设计稿/中心线/标注层当刀线，会直接污染轮廓与盒型候选。

3. `match` 的键闭集扩为 `{names, name_prefix, name_contains}`：
   - `name_contains`：图层名**包含**该子串即命中（大小写不敏感、去首尾空白），用于
     `压线`、`全穿刀`、`轮廓线` 这类带前后缀的命名（真实样本里已出现 `1轮廓实线层`、`3中心线层`
     这种「编号+名称」风格，纯枚举会漏）；
   - `match` 里出现闭集外的键 → **必须报错** `PACKAGING_LAYER_RULES_INVALID`，
     **不许**静默丢弃（静默丢弃会让配置看起来生效、实际不生效）。
   - 颜色/线型的口径不变：默认模板的 `colors` / `line_types` 必须仍为空，线型只能作弱证据。

## 2. 契约 C2：候选分层 —— 拼版 / 图框 / 整张 一律不得作为成品轮廓

定义**产品级闭合候选**：`outline.boundary_candidates` 中 `is_closed=true`，且**不属于**下列三类：

| reason | 判据（确定性、可从 CAD IR 直接算出） |
| --- | --- |
| `frame_layer` | 候选所在图层角色为 `frame` |
| `sheet_extent` | 候选 `bbox` 与 `ir.document.extents` 在**任一方向**相差 ≤ 1% |
| `panel_repeat` | 候选 `entity_ids` 落在 `ir.geometry.repeated_groups` 里 `count >= 2` 的组内 |

1. 三类候选**必须保留**在 `boundary_candidates` 里（不许删除、不许改 `is_closed`），
   但**不得**参与成品主轮廓、成品长宽与盒型候选的判定。
2. 被排除的候选必须逐条披露在 `outline.rejected[]`（新位，见 §5），元素：
   `{"outline_id", "reason", "bbox", "area", "evidence_refs"}`；
   `reason` 闭集 = `{panel_repeat, frame_layer, sheet_extent}`；
   排序 `(reason, -area, outline_id)`，保证 `semantics_hash` 稳定。
3. 只有存在被排除候选时才产生 `PACKAGING_OUTLINE_SHEET_FRAME_REJECTED` 警告，
   `evidence_refs` = 被排除候选的 ref（去重、有序）。
4. **已知取舍（写进契约，不许悄悄改）**：重复出现的成品轮廓（多联/多件）本批也按 `panel_repeat` 排除，
   结果是「没有产品级闭合轮廓 → 成品尺寸走 `missing` 或圆直径回退」。取舍原则是
   **宁缺毋滥**：宁可让报价侧拿不到自动尺寸（人工/模型补），也绝不把展开料或拼版外框当成品尺寸。

## 3. 契约 C3：成品长宽只认产品级证据

主轮廓选择顺序（三层，全部只从**产品级**候选里选）：

1. **有产品级闭合候选** → 取面积最大的那个作主轮廓，沿用既有 `Spec §5.3` 逻辑
   （落在候选上的标注 → `declared`/`measured` 冲突判定 → 单位未确认不得 confirmed）。
2. **没有产品级闭合候选，但有产品级圆**（`kind=circle` 且 `diameter >= 100mm`）→
   `inner_length = inner_width =` 产品级圆的直径（取最大者；实现若选择同为产品级圆的相近直径，
   必须落在**产品级圆直径带** `[min(直径), max(直径)]` 内），`origin=inferred_from_geometry`、
   `status=needs_confirmation`、`confidence <= 0.8`、`evidence_refs` = 同径圆的全部 ref。
   （圆盘盒：只有这一步能把 Ø404 / Ø396.6 这类真实成形尺寸交出去。）
3. **两者都没有** → `inner_length` / `inner_width` 必须 `missing`（`value=None`、`confidence=0`），
   并在 `unresolved` 里给出 `field=inner_length|inner_width`、`reason="product_outline_uncertain"`，
   同时产生 `PACKAGING_PRODUCT_OUTLINE_UNCERTAIN` 警告。
   （酒盒：2 条闭合框都是拼版单元、且图纸里 0 个 `kind=circle`，所以只能是这一步。）

**硬性禁止**：`inner_length` / `inner_width` 在任何情况下都不得等于被 §2 排除的候选的长边/短边
（实测反例：酒盒 `1705.9507596530002` / `713.2989662779999`；圆盘盒 `15639.372814358998` / `6318.280258252998`）。

## 4. 契约 C4：盒型候选必须由产品级证据支撑

`box_candidates` 的判定输入改为**产品级**圆与**产品级**闭合候选（§2 定义），决策规则本身不变：

1. **`round_tube`**：最大产品级圆面积 > 最大产品级闭合候选面积 → `round_tube`
   （`matched_features=["circular_closed_boundary"]`、`confidence <= 0.4`、`missing_features` 允许非空；
   `evidence_refs` 必须是圆实体的 ref，**不得**引用被排除的拼版/图框候选）。
   → 圆盘盒必须因此产出 `round_tube`（拼版大框不再压掉它）。
2. **`folding_carton`**：存在 `crease` 角色图层 **且** 存在产品级闭合候选 → `folding_carton`
   （口径同现状：`matched_features=["closed_outline","crease_lines"]`、`confidence <= 0.35`）。
3. **`irregular`（新增路径，只在本条成立时出）**：存在 `cut` 角色图层，且**不存在**产品级闭合候选
   （即刀线在图上，但成品轮廓拿不到）→ `irregular`，`matched_features=["cut_lines"]`、
   `confidence <= 0.35`、`missing_features` 至少含 `"crease_lines"`、`evidence_refs` = 刀线图层/实体 ref。
   → 酒盒必须因此产出 `irregular`（`CUTTER` 是刀线，只是它的闭合框是拼版，不算产品轮廓）。
4. 既有铁律不变：`box_type` **永不** `confirmed`；候选类型必须落在闭集
   `("telescope_lid_base","drawer","book_style","folding_carton","round_tube","irregular","unknown")`；
   每条候选必须带 `matched_features` / `missing_features` / `contradictory_features` / `evidence_refs`；
   没有任何刀线/压线/圆证据时**不许**凭空产生候选（`box_candidates` 允许为空）。

## 5. 契约 C5：披露面（只加这三个位置，不动冻结面）

本批只允许新增这三处可观测输出：

1. `outline.rejected[]`（§2.2）；
2. 警告码 `PACKAGING_OUTLINE_SHEET_FRAME_REJECTED`（§2.3）与
   `PACKAGING_PRODUCT_OUTLINE_UNCERTAIN`（§3.3）。两者都是**警告**，不是新的 HTTP 错误码，
   不进第 1 批错误码闭集，恢复方式都是人工核对图纸；
3. `unresolved` 里的 `reason="product_outline_uncertain"`（§3.3）。

**不许动**的冻结面（红测 §8 E 组逐条守）：

- `SEMANTICS_VERSION` 仍为 `"packaging-semantics/1"`；语义文档顶层键 `REQUIRED_KEYS` 不变；
  `stats` 键集**不变**（不加新键）；
- 字段键闭集 `model.FIELD_WHITELIST` 不变（不得自创 `panel_size`、`unfolded_size` 这类键）；
- `rule_set` id 仍为 `packaging_layer_rules_v1`、`review_status=reviewed`、默认模板 `colors`/`line_types` 仍为空；
- `packaging_match` 的打分/淘汰/确认流程与需求看板字段来源规则**不在本批改动范围**。

## 6. 契约 C6：确定性与幂等

- `analyze(ir)` 仍是纯函数；同一 `ir` 两次调用 → 同 `semantics_hash`、同 `box_candidates`、同 `rejected` 顺序；
- 候选/披露顺序不得依赖输入书写顺序：`rejected[]` 按 `(reason, -area, outline_id)`，
  `boundary_candidates` 排序口径不变。

## 7. 非目标

- 不实现「多个相同成品轮廓时自动推断单个成品尺寸」（§2.4 已明确取舍）；
- 不做展开尺寸（`unfolded_size`）建模、不做排样、不做 3D；
- 不写业务数据库、不改 `packaging_match`、不改会话/看板 UI、不算成本；
- 不改两份真实 DWG 样本；不 commit / push / MR / tag / Release / 部署。

## 8. 红测清单（`tests/test_packaging_product_outline_red.py`）

| 组 | 用例 | 现状（实现前） |
| --- | --- | --- |
| A 图层名 | A1 `全穿刀`→cut；A2 `压线 Crease`→crease；A3 `图框层`/`排图层`→frame；A4 `CUTTER`→cut（回归）；A5 负向：`DESIGN`/`Defpoints`/`Make2D$*`/`0` 不得是 cut/crease | A1–A3 红（全 `unknown`）；A4/A5 绿 |
| A 规则能力 | A6 `name_contains` 生效；A7 `match` 闭集外的键必须报 `PACKAGING_LAYER_RULES_INVALID`，不许静默丢弃 | A6/A7 红（静默丢弃） |
| B 排除披露 | B1 重复单元（`multi_panel` 形状）不得当成品轮廓 + `rejected[].reason=panel_repeat`；B2 整张框（`sheet_extent`）+ 图框层（`frame_layer`）；B3 `cut_crease_layers` 里 FRAME 大框不得压过 CUT 轮廓（期望 100×60，现状 210×310）；B4 `rejected[]` 元素结构/`reason` 闭集/排序稳定；B5 无被排除候选时 `rejected` 必须是空数组且不得出 `PACKAGING_OUTLINE_SHEET_FRAME_REJECTED` 警告 | B1–B5 红（`outline.rejected` 整个键还不存在） |
| C 盒型候选 | C1 有拼版大框 + 产品级圆 → 必须出 `round_tube` 且证据指向圆；C2 只有刀线 → 必须出 `irregular`（`missing_features` 非空）；C3 无任何刀线/压线/圆 → 不许产生候选；C4 候选 `box_type` 仍 `needs_confirmation` | C1/C2 红（`box_candidate_total=0`）；C3/C4 绿 |
| D 真实样本（gated） | D1 真实图层角色；D2 酒盒候选 ⊆ {folding_carton, irregular}；D3 圆盘盒含 `round_tube`；D4 圆盘盒成品长宽 ∈ 产品圆直径带（由 IR 自行算出）且 ≠ 15639.37/6318.28；D5 酒盒成品长宽 ≠ 1705.95/713.30 且 `rejected` 非空；D6 `stats.cut/crease_layer_total`；D7 `semantics_hash` 稳定 | 全部红（除 D7） |
| E 冻结面 | E1 `SEMANTICS_VERSION`；E2 字段键闭集；E3 `rule_set`/`review_status`/`colors`/`line_types`；E4 `box_type` 永不 confirmed；E5 第 4 批 Spec 的六条铁律文本与 `tests/test_packaging_semantics_red.py` 的 `STATS_KEYS`/`REQUIRED_KEYS` 字面未被改动 | 全绿（锚点守卫） |

D 组的门（缺任何一条就 `skipTest` 并点名缺什么，静默跳过等于把「没跑过」包装成「通过」）：
`CPQ_DWG_REAL_SAMPLES=1`、本机有真实转换器（`cad_converter.capability().available` 且非 `simulated`）、
两份样本存在。转换只走 `tech_app/tools/dwg_sample_e2e.py`（样本只读、产物只写临时目录）。

## 9. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_product_outline_red -v
CPQ_DWG_REAL_SAMPLES=1 ./open-claude/.venv/bin/python -m unittest \
    tests.test_packaging_product_outline_red.RealSampleProductOutline -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_semantics_red -v   # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_drawing_flow_red -v # 不回归
```

## 10. 人工验收

1. 用两份真实 DWG 跑 drawing-flow（2.1 图纸解析）→ 圆盘盒能看到 `round_tube` 候选与 Ø404 量级的成品尺寸候选；
   酒盒能看到 `irregular` 候选 + 「成品尺寸需人工确认」的明确披露，且不再出现 1705.95×713.30。
2. 报价侧盒型匹配用圆盘盒/酒盒的真实规格检索 `YT-DWG-ROUND-10PC` / `YT-DWG-WINE-700ML`（KB 侧已就绪）。

## 11. 实现状态（9-21，Codex 实现）

§1–§10 原文未动，本节只记录落地结果（详细实跑见 `changelog_9_21_25.md` ## 231）。

- 红测：`tests.test_packaging_product_outline_red` 由 `FAILED (failures=12, skipped=1)`
  转 `OK (skipped=1)`；D 组由 `FAILED (failures=5)` 转 `FAILED (failures=1)`。
- 落地文件：`tech_app/agent_knowledge/rules/packaging_layer_rules.json`、
  `tech_app/backend/services/packaging_semantics/{rules,roles,geometry_semantics,fields,__init__}.py`。
- 真样本（本机 ODA 27.1）：酒盒 → `irregular` + 成品长宽 `missing`
  （`1705.9508 × 713.2990` 不再出现）；圆盘盒 → `round_tube` + `inner_length=inner_width=404.0`
  （`15639.3728 × 6318.2803` 不再出现）。
- 两处按字面取舍（留给 Spec 所有者决定是否收紧）：§4.3 的 `irregular` 在「有 `cut` 且无
  产品级闭合候选」时一律出，因此圆盘盒同时出 `round_tube` 与 `irregular`（`box_type` 取
  `round_tube`）；`outline.rejected[]` 覆盖的是 `PACKAGING_SEMANTICS_MAX_CANDIDATES`（默认
  200）截断后的候选集合，圆盘盒 633 条重复候选披露 200 条并同时给出截断警告。
- 仍未绿：D1 假设 `酒盒.dwg` 也有 `Make2D$可见线$普通线` 图层 —— 实测该图层的 DXF 里
  `Make2D` 出现 0 次（酒盒只有 8 个图层：`0`/`CUTTER`/`DESIGN`/`Defpoints`/`SAMPLE`/
  `_U+56FE_U+5C42 1`/`图层 2`/`轮廓线`），而 D1 的 `role()` 帮手在图层缺失时直接 `fail`。
  需测试所有者把该项只对圆盘盒断言，或让 `role()` 对缺失图层返回空串；实现方未改测试。

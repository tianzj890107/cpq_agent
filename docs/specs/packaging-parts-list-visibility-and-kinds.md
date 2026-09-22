# 包装图纸零件：列表可见性与"种类"（199 件真零件凭空消失 + 22 种形状没人看见）

血缘：**取代** `packaging-dwg-parts-extraction.md` §5 的「上限：`max_parts`（默认 64），超出时截断并给
`stats.truncated`」这一条；承接 `packaging-parts-component-chaining.md`（分量口径改对之后件数暴涨）、
`packaging-parts-true-outline.md`（件内真实轮廓，`kind_key` 要在它之上算）、
`packaging-part-role-manual-mapping.md`（人工映射"看得见 + 做得动"，本层是它的前置：
**看不到的件根本映射不了**）。

状态：Spec + 红测（已实现）（由并行会话落在 `packaging_parts.py` + `main.py` + `app.js` +
`drawing-flow.css`，本会话复核：离线 `Ran 11 OK (skipped=1)`、带 `CPQ_DWG_REAL_SAMPLES=1` 的真样本
F 组 `Ran 2 OK`（`酒盒.dwg` 实测 `part_total=263` / `truncated=0` / `kind_total=101`）。
`## 308` 记录了本层引起的 4 条测试侧基线冲突 —— 全是"分母从"前 64 件"变成全量"造成的，未改任何既有断言）
红测：`tests/test_packaging_parts_list_visibility_red.py`

（本批 changelog 条目号 `## 306`「零件列表把 199 件真零件丢了…」；同周另有并行会话的 `## 304/## 305/## 306`，
周文件里的重复编号未做改写，只在本 Spec 里写清指哪一条。）

## 0. 一句话目标

**一份图纸里有多少件、有几种，必须能在零件文档里读到；任何一件真零件都不许因为"页大小"而从文档里消失**，
并且 2.1 面板要能按"种类"折叠着看、能接着往下翻。

## 1. 现状缺口（9-22 实测，本机 `酒盒.dwg`，逐条可复现）

| 事实 | 值 | 出处 |
| --- | --- | --- |
| 端点相接分组后的分量 | 1163 | `geometry.components` |
| 被 `area_under_min` / `area_over_max` / `edge_over_max` 挡掉的 | 900 | `stats.filtered_reason_mix` |
| **过滤后、截断前的真零件** | **263**（= `part_total` 64 + `truncated` 199） | 由两个键相加才推得出 |
| 文档里真正留下的零件 | **64** | `parts[]` |
| 列出的 64 件里的不同（长,宽,轮廓状态）组合 | 22 | 本机统计 |
| 带 `repeat_of` 的件 | 40 / 64 | 同上 |
| `kind_total` / `kind_key` / `kept_total` | **不存在** | `stats.keys()` / 行 keys |

代码事实（`packaging_parts.py`）：

```python
kept.sort(key=lambda row: (-(row["area"] or 0.0), row["component_id"]))
for index, row in enumerate(kept, start=1):   # 263 件都建好了 part_code
    ...
max_parts = int(config["max_parts"])          # 64
truncated = max(0, len(parts) - max_parts)    # 199
parts = parts[:max_parts]                     # ← 199 件真零件在这里被丢掉，文档里再也读不回来
```

- `repeat_of` 的键是 `(round(length,3), round(width,3), entity_total)`，**只有长宽没有形状**
  → "同尺寸不同轮廓"（例如一个矩形和一个 L 形）会被算成同一件；"同轮廓不同尺寸"会被算成两件；
- 于是用户能看到的全部信息是：64 行 + 一句"还有 199 件未列出（只显示前 64 件）"；
  那 199 件既不能读、也不能按种类/面积筛、更没法人工映射角色（`packaging-part-role-manual-mapping.md` 的前置被堵死）。

## 2. 口径（可直接验收）

### 2.1 `extract()`：不再因为页大小丢件

- `parts` 必须是**全部** kept 件（本图 263），顺序仍为 `(area desc, component_id asc)`；
- `stats.part_total` = `len(parts)`（原义不变：**这一份零件文档有多少件**）；
- `stats.truncated` 语义改为"**本份文档里因显式传入 `options["max_parts"]` 而未纳入的件数**"，
  默认（未传）**恒为 0**；显式传正整数时行为与今天一致（截断 + `truncated` 计数），
  这样旧行为与旧断言都能用显式参数复现；
- 新增 `stats.kept_total`（= `part_total`，把"过滤后剩多少"显式化，方便前端把
  "被过滤掉的分量"与"文档里的件数"分开说）；
- `DEFAULT_OPTIONS` 的**字面不变**（`max_parts: 64` 继续是"读接口默认页大小"的唯一出处，
  由 §2.4 的 `limit` 缺省值引用它）。

### 2.2 种类：`kind_key` / `kind_index` / `stats.kind_total`

- 每件新增 `kind_key`：**形状 + 尺寸 + 轮廓状态**的确定性指纹，必须由
  `outline.points`（真实轮廓，见 `packaging-parts-true-outline.md`）算，**不许再用 `(长,宽,entity_total)`**：
  1. 取该件的 `outline.points`（`outline_status != "closed"` 时用 `component bbox` 的四角）；
  2. 按 `LOOP_TOLERANCE_MM` 量化、平移到最小点归零（**只有平移不变性，不做旋转/镜像归一**，
     旋转或镜像的变体本批算不同种 —— 宁可多一种，不许把不同刀模合成一种）；
  3. 拼接为字符串后取 `sha256` 前 12 位十六进制 → `kind_key`；
  4. 指纹还必须含 `outline_status` 与量化后的 `(unfolded_length_mm, unfolded_width_mm)`；
  5. 同一份 IR 两次跑，`kind_key` 必须逐字相同（确定性）。
- `stats.kind_total` = 去重后的种类数；
- 每件新增 `kind_index`（1 起、按"该种件数 desc, `kind_key` asc"排序、跨全量件一致）；
- `repeat_of` 保留原键与原值口径，但必须**跨全量 kept 件**计算（不许退化成"只在当前页里找重复"）；
  并且 `repeat_of` 只在**同 `kind_key`** 的件之间出现。

### 2.3 指标（`summarize()`）

**新增**（既有键不改）：`kept_total` / `listed_total`（= 文档里的件数）/ `kind_total` /
`repeat_total`（`repeat_of` 非空的件数）。

### 2.4 读接口分页与筛选

`GET /api/projects/{pid}/requirement/packaging-parts` 支持
`offset`（默认 0）、`limit`（默认 = `DEFAULT_OPTIONS["max_parts"]`，上限 500）、
`kind`（`kind_key`）、`role`、`outline_status`、`min_area_mm2`，并返回：

```json
{"items": [...], "total": 263, "offset": 0, "limit": 64, "has_more": true, "kind_total": 22}
```

- 过滤/分页只影响 `items`，`total` / `kind_total` **永远是全量真值**；
- `offset >= total` → `items: []`、`has_more: false`（不许 404、不许报错）；
- 非法 `limit`（<=0 或 >500）→ 400，不许静默夹取。

### 2.5 面板（2.1 零件树）

- 必须能**按种类折叠**：一种一行（`kind_index` / `kind_key` / 该种件数 / 代表尺寸），展开看这一种的件；
- 截断/分页提示必须把三笔账分三句说清（与 `packaging-parts-component-chaining.md` §2.4 同一口径）：
  已显示的件数、**共多少件（= `total`）**、被过滤掉的分量数；
- "还有 N 件未列出"这句必须能**继续加载**（下一页），不许只能看前 64 件；
- 空态（`total == 0`）必须明说"这份图纸没有可用的零件"，不许只显示空白表格。

## 3. 验收标准

| 组 | 断言 |
| --- | --- |
| A 不再丢件 | 70 个互不相同的合成件 → `stats.part_total == 70`、`len(parts) == 70`、`stats.truncated == 0`；`stats.kept_total == 70` |
| A 显式截断仍可用 | `options={"max_parts": 10}` → `len(parts) == 10`、`truncated == 60`（旧行为可复现） |
| B 种类 | 3 个同形状同尺寸 + 1 个不同 → `stats.kind_total == 2`；同种 3 件 `kind_key` 相同且 `kind_index` 相同；不同种不同 |
| B 形状参与指纹 | 同长宽、不同轮廓（矩形 vs L 形）→ `kind_total == 2`（今天只按长宽，会算成 1 种） |
| B 确定性 | 同一份 IR 跑两次 `kind_key` / `kind_index` 逐字相同；`repeat_of` 只在同 `kind_key` 之间出现 |
| C 指标 | `summarize()` 有 `kept_total` / `listed_total` / `kind_total` / `repeat_total`，且 `listed_total == len(parts)` |
| D 路由 | 读接口源码含 `offset` / `limit` / `has_more` / `kind_total`；非法 `limit` 返回 400 |
| E 面板 | `app.js` 含种类折叠（`kind_key` 或 `kind_index`）、"共 … 件"、继续加载（`offset`）三类痕迹 |
| F 真样本（gated） | `酒盒.dwg`：`part_total >= 200`、`len(parts) == part_total`、`truncated == 0`、`kind_total >= 20`、`kind_total <= part_total` |

## 4. 明确不做

- 不做旋转/镜像归一（转 90° 的同一刀模本批仍算两种，宁可多不许少）；
- 不做拼版/套裁归并；
- 不改 `filtered_*` 与 `filtered_reason_mix` 的口径与键名（那笔账归
  `packaging-parts-component-chaining.md`）；
- 不改 `REASON_CODES` / `PART_CODE_FORMAT` / `component_id` 的格式；
- 不做"按种类批量映射角色"（本批只让种类可见；批量映射留给
  `packaging-part-role-manual-mapping.md` 的后续批次）；
- 不许改 `tests/` 下任何既有文件；不 commit / push / tag / Release / 部署。

## 5. 命令与期望

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_list_visibility_red -v  # 当前必红
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red -v       # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_components_red -v       # 不回归
CPQ_DWG_REAL_SAMPLES=1 ./open-claude/.venv/bin/python -m unittest \
  tests.test_packaging_parts_list_visibility_red.RealSampleList -v                            # 真样本 F 组
```

## 6. 测试侧基线与新分母的重标定（`## 308` 落地后复跑 → 9-22 已按本节修法落地）

本层让零件文档保留**全部** kept 件（`酒盒.dwg` 64 → 263、`圆盘盒.dwg` 64 → 312），
于是所有"文件级比率"的**分母**跟着变大，而分子只按新增的件涨得慢 —— 四条按旧分母
（前 64 件）标定的真样本门槛当场变红。**这是测试侧基线与新口径脱钩，不是能力退步**：
`closed_ratio` 的分子从 40 涨到 134、`material_known_total` 从 40 涨到 134，
`圆盘盒` 的 `role_known_total` 从 8 涨到 9（绝对数一个都没掉）。

**修法只有一条判据**：把"按比值标定"的门槛换成**绝对分子地板**。比值分母本来就是"文档里有几件"
（§2.1），它会随分组口径变；绝对分子才是"能力有没有退步"，而本轮分子**没有一个下降**。

| 测试 | 旧断言（按前 64 件标定） | 旧实测 → 新实测 | 现断言（已落地） |
| --- | --- | --- | --- |
| `test_packaging_parts_material_attribution_red.ERealSample::test_e1_material_coverage` | 酒盒 `material_known_ratio >= 0.60` | 0.625（40/64）→ 0.510（134/263） | `material_known_total >= 40` |
| 同上 `::test_e2_thickness_coverage` | 酒盒 `thickness_known_ratio >= 0.60` | 0.625 → 0.510 | `thickness_known_total >= 40` |
| 同上 `::test_e3_processable_coverage` | 酒盒 `processable_ratio >= 0.60` | 0.625 → 0.510 | `processable_total >= 40` |
| `test_packaging_parts_downstream_gate_red.FRealSampleThresholds::test_f2_disc_box_threshold` | 圆盘盒 `role_known_ratio >= 0.10` | 0.125（8/64）→ 0.029（9/312） | `round(role_known_ratio × part_total) >= 8`（`summarize()` 无 `role_known_total` 键，测试侧还原分子，不新增实现键） |
| `test_packaging_parts_solid_coverage_red.FRealSample::test_f1_solid_ok_ratio` | 酒盒 `solid_ok_ratio >= 0.70` | 0.625 → 0.510 | `stats["ok_total"] >= 40` |
| `test_packaging_parts_outline_chaining_red.ERealSample::test_e1_closed_ratio` | 酒盒 `closed_ratio >= 0.88` | 0.625 → 0.510 | `closed_total >= 40` |
| 同上 `::test_e4_open_reason_mix_has_no_vague_reason` | `1 <= open_total <= 7` | 24 → 129 | `1 <= open_total < part_total`（上限改对分母无关的不变式） |
| 同上 `::test_e2_rescue_total` | `rescue_total >= 6` | 0（红）→ **已转绿** | 未改 |

**不做的三件事**：不删用例、不把 `summarize()` 的分母改回"前 64 件"、不为了让比值好看去扩大整盒兜底 ——
分母口径归本 Spec §2.1/§2.3（`total` 是文档里的件数），门槛数字归测试侧重新标定（本节）。

9-22 复跑（`CPQ_DWG_REAL_SAMPLES=1`）：`material_attribution` 27 OK、`downstream_gate` OK、
`solid_coverage` OK、`outline_chaining` 24 OK。

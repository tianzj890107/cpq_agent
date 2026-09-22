# 包装图纸零件：覆盖率的诚实性（三条"覆盖率"其实是同一个数 + 缺口必须有原因账）

血缘：**取代** `packaging-parts-material-attribution.md` §4 的「真实样本门槛」表；
承接 `packaging-parts-downstream-acceptance.md`（第 5 层五个指标）、
`packaging-parts-component-chaining.md`（分组口径变更是本 Spec 的触发者）、
`packaging-parts-thickness-facts.md`（负责把"证据口径"覆盖率抬上去）、
`packaging-parts-3d-extrusion.md` §2（"绝不许默认料厚" —— 本 Spec 就是来堵它被 KPI 反着用的）。

状态：Spec + 红测（已实现）（由并行会话落在 `packaging_parts.py` 并提交 `b8f0759`；本会话复跑：离线 `Ran 11 OK (skipped=1)`、带 `CPQ_DWG_REAL_SAMPLES=1`
`Ran 11 OK`。缺口背景：`material_known_ratio` = `thickness_known_ratio` = `processable_ratio`
= `closed_ratio` = **0.625**，其中料厚 40 件里 **34 件来自整盒兜底** `requirement_default`）
红测：`tests/test_packaging_parts_coverage_truthfulness_red.py`

## 0. 一句话目标

三个"覆盖率"必须能**各自动**；"图纸证据"与"整盒兜底"必须**分开数**；
每一件没有材料 / 没有料厚的零件，必须能说出**是哪一类原因**，而不是只留一个数字。

## 1. 现状缺口（9-22 实测，本机 `酒盒.dwg` + 需求 3.3 = 灰板 2.5 / 粉灰 350g）

| 指标 | 实测值 | 分子 / 分母 |
| --- | --- | --- |
| `closed_ratio` | **0.625** | 40 / 64 |
| `material_known_ratio` | **0.625** | 40 / 64 |
| `thickness_known_ratio` | **0.625** | 40 / 64 |
| `processable_ratio` | **0.625** | 40 / 64 |
| `material_default_ratio` | 0.391 | 25 / 64（层 4 整盒兜底） |

四条比率**逐字相等**，不是巧合、也不是测量精度：

1. `packaging-parts-material-attribution.md` §2 明写归属**只在 `outline_status == "closed"` 的件上生效**
   → `material_known_total <= closed_total` **恒成立**；
2. 同一份 Spec 的层 4「需求整盒兜底」会把**每一个** closed 件都填上材料与料厚
   → 等号成立，三条比率被钉死在 `closed_ratio` 上。

也就是说，**这三条门槛测的从来不是"材料归属做得好不好"，而是"闭合轮廓占比"**。

### 1.1 后果一：门槛可以被"分组更粗"刷过去（本轮真实发生）

`packaging-parts-component-chaining.md` 落地（分量 402 → 1163）后，同一份图纸：

| 时点 | 分量数 | `closed_ratio` | 三条门槛（0.75 / 0.75 / 0.70） |
| --- | --- | --- | --- |
| bbox 相交分组（旧） | 402 | ≥ 0.75 | 全通过 |
| 端点相接分组（新） | 1163 | **0.625** | `test_packaging_parts_material_attribution_red` **failures=3** |

分组变**诚实**，覆盖率就掉下来 —— 说明这个数不能用来判断归属质量。

### 1.2 后果二：料厚覆盖率 90% 是"整盒兜底"，与 3D 层口径直接打架

| 事实 | 值 |
| --- | --- |
| 有料厚的件 | 40 |
| 其中 `thickness_source.kind == "requirement_default"` | **34** |
| 其中来自图纸证据（`part_note` / `group_note`） | **6**（4 + 2） |
| 材料：来自图纸证据 | 15（`group_note` 10 + `part_note` 5） |

`packaging-parts-3d-extrusion.md` §2 的原话是"`thickness_mm` 为空或 `<= 0` → `unsupported:
thickness_unknown`（**绝不许默认 2mm**）"；而这里的 `thickness_known_ratio = 0.625` 里有
**85%（34/40）**正是整盒兜底给的值。**一个模块明令禁止的东西，正在给另一个模块的 KPI 充数。**

### 1.3 后果三：没有原因账，缺口不可归因

24 件没有材料 —— 是"这件没有闭合轮廓"，还是"图纸根本没写"，还是"写了但只有克重"，今天
从任何读接口都看不出来（只有 `material_known_ratio` 一个数）。

## 2. 口径（可直接验收）

### 2.1 每个指标必须给"分子 / 分母 / 证据口径"三件套

`summarize()` **新增**（既有键一个都不许去掉、改名或换类型）：

| 键 | 定义 |
| --- | --- |
| `part_total` / `closed_total` | 件数 / 闭合件数（分子分母显式化） |
| `material_known_total` / `thickness_known_total` / `processable_total` | 各比率的**分子** |
| `material_default_total` / `thickness_default_total` | 来源是 `requirement_default` 的件数 |
| `material_evidence_ratio` | 有材料**且** `material_source.kind != "requirement_default"` 的件数 / `part_total` |
| `thickness_evidence_ratio` | 有料厚**且** `thickness_source.kind != "requirement_default"` 的件数 / `part_total` |

- 既有 `material_known_ratio` / `thickness_known_ratio` / `processable_ratio` / `closed_ratio` /
  `material_default_ratio` / `attribution_kind_mix` **一个字都不许改**（它们是历史口径，继续按原定义算）；
- `part_total == 0` 时所有比值仍 `0.0`、所有计数仍 `0`（不许 null、不许抛错）。

### 2.2 缺口必须逐件有原因（原因账）

`summarize()` **新增**两个账（顺序固定，值为 0 的键也要出现）：

```python
MATERIAL_GAP_REASONS = ("no_closed_outline", "no_material_note", "material_ambiguous", "unknown")
THICKNESS_GAP_REASONS = ("no_closed_outline", "no_thickness_note", "grammage_only",
                         "material_missing", "material_ambiguous", "unknown")
```

- `material_gap_mix` / `thickness_gap_mix`：`{reason: count}`，且
  `Σ(material_gap_mix) == part_total - material_known_total`、
  `Σ(thickness_gap_mix) == part_total - thickness_known_total`（**必须成立**）；
- 判定优先级（一件只记一次，取第一个命中的）：
  1. `outline_status != "closed"` → `no_closed_outline`；
  2. 材料未定 → `no_material_note`；材料歧义弃权（`attribution.notes` 含 `ambiguous:`）→ `material_ambiguous`；
  3. 厚度：材料本身就没有 → `material_missing`；有材料但**只有克重**且没有可推的密度 → `grammage_only`；
     材料有、注记里根本没有厚度 → `no_thickness_note`；
  4. 以上都不命中 → `unknown`（这个键**长期必须为 0**，不为 0 就是原因判别有漏）。
- 每件的 `attribution` 上必须能读回同一个 reason（键名 `gap_reason`），账与行不许对不上。

### 2.3 门槛（本 Spec 取代 `packaging-parts-material-attribution.md` §4 的「真实样本门槛」表）

**必须两条同时成立**才算达标 —— 只看前三条等于只看 `closed_ratio`：

| 门槛 | 值 | 今天（9-22 实测） |
| --- | --- | --- |
| `material_known_total` | `>= 40` | 134 |
| `thickness_known_total` | `>= 40` | 134 |
| `processable_total` | `>= 40` | 134 |
| `material_evidence_total` | `>= 20` | 37 |
| `thickness_evidence_total` | `>= 8` | 13 |

**2026-09-22 重标定：五条门槛全部由"比值地板"改成"绝对分子地板"。**

- 原因：比值分母（`part_total`）**会随分组口径变**。`packaging-parts-list-visibility-and-kinds.md`
  让零件文档保留全量件之后，`酒盒.dwg` 的件数从 **64 → 263**，而分子一件都没掉
  （`closed_total` / `material_known_total` 40 → 134、`material_evidence_total` 8 → 37、
  `thickness_evidence_total` 3 → 13）。原来那张按"前 64 件"标定的比值表于是**五条同时**假性失败
  （实测 0.51 / 0.51 / 0.51 / 0.141 / 0.049）—— 测的是"分母涨没涨"，不是"能力退没退步"。
- 判据：**分母口径归 Spec，能力门槛归绝对分子。** 同一条判据也用在
  `packaging-parts-list-visibility-and-kinds.md` §6 的另外 7 条真样本门槛上。
- 前三条：`closed_ratio` 的天花板仍是 `closed_total / part_total`（同一份图纸、同一套 Spec 规则）；
- 后两条是**证据地板**，**只许升不许降**（现在按绝对分子只许升）；把它们抬上去的唯一合法手段是
  `packaging-parts-thickness-facts.md`（克重 → 料厚 / 防串味 / 人工补料厚），
  不许靠"扩大整盒兜底"或"把件合并得更粗"；
- 比值本身（`material_known_ratio` 等）仍是冻结面（§2.4），读接口照常给，只是**不再当门槛**；
- 改任何一条门槛都必须改本文件（本表是门槛的唯一出处）。

历史值（"前 64 件"分母时代，已取代）：`material_known_ratio >= 0.60`（0.625）、
`thickness_known_ratio >= 0.60`（0.625）、`processable_ratio >= 0.60`（0.625）、
`material_evidence_ratio >= 0.15`（0.234）、`thickness_evidence_ratio >= 0.08`（0.094）。

### 2.4 冻结面

- 不改 `material_known_ratio` / `thickness_known_ratio` / `processable_ratio` / `closed_ratio` 的
  原定义与原字面（只加不改）；
- 不改 §2 四层归属的优先级、半径、弃权规则；
- 不改 `as_ir_part` / `processability` 的三道拒绝码与"缺料不许给默认料厚"；
- 不许改 `tests/` 下任何既有文件；不 commit / push / tag / Release / 部署。

## 3. 验收标准

| 组 | 断言 |
| --- | --- |
| A 计数与证据口径 | `summarize()` 含 §2.1 全部新键；合成夹具（1 件有件级注记 + 1 件走整盒兜底）→ `material_known_ratio == 1.0` 但 `material_evidence_ratio == 0.5`；`material_default_total == 1`；`part_total=0` 时全 0 不抛错 |
| B 原因账 | 合成夹具（1 件 open + 1 件 closed 无注记 + 1 件 closed 有注记）→ `Σ(material_gap_mix) == 3 - 1`、open 那件记 `no_closed_outline`；`thickness_gap_mix` 里"只有克重没料厚"记 `grammage_only`；行上的 `attribution.gap_reason` 与账一致；`unknown` 为 0 |
| C 真样本（gated） | `酒盒.dwg` + 需求 3.3：§2.3 五条门槛同时成立；`thickness_default_total >= thickness_evidence_total` 与 `material_default_total` 可读；`Σ` 两个账各自等于 `part_total - known_total` |
| D 文档一致 | `packaging-parts-material-attribution.md` §4 必须点名本 Spec 为门槛新出处（两套数字不许并存） |

## 4. 明确不做

- 不为了让数字好看而放宽 §2 的"open 件不许有材料"；
- 不改 `closed_ratio` 的算法（闭合判定归 `packaging-parts-outline-chaining.md`）；
- 不在本批引入新的材料知识或料厚推导（归 `packaging-parts-thickness-facts.md`）。

## 5. 命令与期望

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_coverage_truthfulness_red -v  # 当前必红
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_material_attribution_red -v   # 不回归
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red -v              # 不回归
CPQ_DWG_REAL_SAMPLES=1 ./open-claude/.venv/bin/python -m unittest \
  tests.test_packaging_parts_coverage_truthfulness_red.RealSampleCoverage -v                        # 真样本 C 组
```

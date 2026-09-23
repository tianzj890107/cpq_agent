# Spec：被过滤的分量有三套口径在打架 —— `filtered[].reason` 归并 ≠ `filtered_reason_mix`，四个 `filtered_*_total` 又不能相加

状态：Spec + 红测（已实现）（原状：实现提示词只在会话交付、业务实现不在本批；红基见 §4；
`## 460` 已落地，落点、等价改写与实测见 §6）
红测：`tests/test_packaging_parts_filtered_two_books_must_agree_red.py`
血缘：`packaging-parts-component-chaining.md` §2.4（本批修正它那一对没定单位的计数键；
"各项之和 == `filtered_total`" 只对 `filtered_reason_mix` 成立）、
`packaging-parts-list-visibility-and-kinds.md`（2.1 那句话用的是 `filtered_reason_mix`）、
`packaging-parts-coverage-truthfulness.md`（同一件事的多种口径必须能机械区分）。
本批 changelog 条目号：`## 460`。

## 0. 一句话目标

一份零件文档里"被过滤掉的分量"现在有三套数：`filtered[].reason`（列表第一条原因）、
`filtered_reason_mix`（一件一次的主因）、四个 `filtered_*_total`（一件可进多本）。
三套都叫"被过滤的原因/件数"，**却没有一处说得出它们不是同一把尺** ——
按 `filtered[]` 自己归并会得到与 `filtered_reason_mix` 不同的桶，把四个 `_total` 相加会得到一个
大于 `filtered_total` 的数。审阅者拿哪个数核都对不上，而"哪个才是真的"没人说得出。

## 1. 真实跑证据（本机 LibreDWG 真转换，两份真刀模图 + 一个合成夹具）

| 口径 | `酒盒.dwg` | `圆盘盒.dwg` | 合成夹具 `parts_panels()` |
| --- | --- | --- | --- |
| `filtered_total` | 900 | 2988 | 3 |
| `filtered_reason_mix`（一件一次的主因） | `{area_under_min: 888, area_over_max: 6, edge_over_max: 6}` | `{area_under_min: 2919, area_over_max: 38, edge_over_max: 31}` | `{area_under_min: 2, area_over_max: 1}` |
| 按 `filtered[].reason` 归并 | `{edge_over_max: 12, area_under_min: 888}` | `{area_under_min: 2919, edge_over_max: 69}` | `{edge_over_max: 1, area_under_min: 2}` |
| 两者不一致的行数 | **6** | **38** | **1**（`cmp:F`） |
| 四个 `filtered_*_total` 之和 | **911** | **3029** | **5** |
| `Σ len(row["reasons"])` | 911 | 3029 | 5 |
| 多因件（`reasons` ≥ 2 条） | 11 | 41 | 2 |

三件事一眼可见：

1. **`filtered[].reason` 不是主因**：它是 `reasons[0]`（跟着原因追加顺序走），而
   `filtered_reason_mix` 走的是 `FILTER_REASON_ACCOUNT_ORDER`（`area_over_max` 优先）。
   于是 `酒盒.dwg` 按 `reason` 归并得到 `edge_over_max 12 / area_over_max 0`，
   而 `mix` 是 `edge_over_max 6 / area_over_max 6`：**`area_over_max` 那一桶在归并里直接消失**
   （6 件被错归到 `edge_over_max`；圆盘盒 38 件）。同一个词"主因"，两处两把尺。
2. **四个 `_total` 与 `filtered_total` 不可相加**：前者一件可进多本（`cmp:1` 这类
   `["edge_over_max", "area_over_max"]` 的件各记一次），后者一件一次；911 ≠ 900、3029 ≠ 2988，
   而键名里没有一处说得出这个区别（`FILTER_REASON_TOTAL_KEYS` 的注释说了，产物没说）。
3. **合成夹具也复现**：`parts_panels()` 的 `cmp:F`（`["edge_over_max", "area_over_max"]`）就是
   那一件不一致 —— 这条不依赖真样本，任何一次跑都能看出来。

为什么这条值得钉：2.1 面板那句"另有 900 个图元分组未成为零件（面积过小 888 / 面积超限 6）"
用的是 `mix`，而同一份产物里 `filtered_edge_over_max_total` 是 12、`filtered_area_over_max_total`
是 6 —— 谁拿后者去核前者，都会认为自己算错了或系统坏了。

## 2. 契约

### 2.1 `filtered[].reason` 必须就是主因口径（唯一一把尺）

- `extract()` 写进 `filtered[]` 的 `reason` 必须**逐字等于** `_account_reason(row["reasons"])`
  （即 `filtered_reason_mix` 用的那一个；`FILTER_REASON_ACCOUNT_ORDER` 的优先级口径不变）；
- 等价的可核对判据：**按 `filtered[].reason` 归并 == `filtered_reason_mix`**（逐键、逐值、键集相同）；
- 冻结点：`reasons` 的**内容与顺序**一个字不改（它是原始证据；改的只是那个"主因摘要"字段）；
  `filtered[]` 的成员、顺序、`bbox` / `entity_total` 都不动。

### 2.2 `stats` 新增三个键（键必须存在）

- `filtered_reason_hits_total`：整数 = `Σ len(row["reasons"]) for row in filtered[]`
  = 四个 `filtered_*_total` 之和（一件可进多本的那本账的合计）；
- `filtered_reason_mix_scope`：恒定字符串 `"primary_reason_per_part"`；
- `filtered_reason_totals_scope`：恒定字符串 `"reason_per_part"`。

（名字与取值定死，便于接口/前端/审查机械判定；不许用自由文案替代。）

### 2.3 四条不变量必须同时成立

1. `sum(filtered_reason_mix.values()) == filtered_total`（既有口径，继续成立）；
2. `filtered_reason_hits_total == sum(四个 filtered_*_total) == Σ len(row["reasons"])`；
3. `filtered_reason_hits_total >= filtered_total`，且差额
   `== Σ (len(row["reasons"]) - 1)`（多因件贡献的额外命中）；
4. `filtered[]` 每行 `reasons` 非空且**无重复项**（同一原因一件只记一次，与四键的语义同构）。

### 2.4 `summarize()` 必须透出这三项

`filtered_total` / `filtered_reason_mix` 之外，读侧摘要里必须能读到
`filtered_reason_hits_total`、`filtered_reason_mix_scope`、`filtered_reason_totals_scope`
（读侧只给数字，就会重演今天这场"两本账都对不上"）。

### 2.5 非目标 / 冻结面

- 不许改任何既有键名与数值口径：`filtered_total`、`filtered_reason_mix`、四个 `filtered_*_total`
  （数值仍等于"逐原因件数"，一个字不许变）、`filtered[]` 结构；
- 不许改 `FILTER_REASON_ACCOUNT_ORDER` / `REASON_CODES` / `DEFAULT_OPTIONS`（`min_area_mm2` /
  `max_edge_mm` / `max_area_mm2` / `max_parts`）与过滤判据本身；
- 不许改 `filtered[]` 的 `reasons` 内容与顺序、`entity_total`、排序口径；
- 不许改前端文案与 `packaging-parts-component-chaining.md` 里那句"各项之和 == `filtered_total`"
  所指向的 `filtered_reason_mix` 口径；
- 不许动后端接口形状（只加键）、不许新增落库字段。

## 3. 红测映射（`tests/test_packaging_parts_filtered_two_books_must_agree_red.py`，8 条）

| 用例 | 夹具 | 期望 | 现状 |
| --- | --- | --- | --- |
| S1 | 合成 `parts_panels()` | 按 `filtered[].reason` 归并 == `filtered_reason_mix`（`cmp:F` 必须归到 `area_over_max`） | 红（`reason` 是 `edge_over_max`） |
| S2 | 合成 `parts_panels()` | 三个新键存在；`mix` 之和 == `filtered_total` == 3；`hits == 5` == 四键之和 == `Σ len(reasons)` | 红（键不存在） |
| S3 | 合成 `parts_panels()` | 差额不变量：`hits - filtered_total == Σ(len(reasons)-1)` == 2 | 红 |
| S4 | 合成 `parts_panels()` | `summarize()` 里能读到三个新键 | 红 |
| S5 | 两份真实样本（`CPQ_DWG_REAL_SAMPLES=1`） | 两份图：`mix` 之和 == `filtered_total`（900 / 2988）、`hits == Σ len(reasons)`（911 / 3029）、多因件 11 / 41、`hits > filtered_total`、按 `reason` 归并 == `mix` | 红（键与归并都不成立） |
| S6 | 合成 + 真样本 | 护栏：四个 `filtered_*_total` 仍等于用 `filtered[]` 逐原因重算的值（数值一个字不改） | 护栏（今天绿） |
| S7 | `packaging_parts.py` 源码 | 接线守卫：`filtered.append({... "reason": _account_reason(...)})` 必须存在，且三个新键名出现在源码里 | 红（今天是 `reasons[0]`） |
| S8 | 合成 + 真样本 | 护栏：每行 `reasons` 非空且无重复；`len(filtered[]) == filtered_total`；`FILTER_REASON_ACCOUNT_ORDER` 与 `REASON_CODES` 不变；`extract()` 仍是纯函数（不落库、不联网） | 护栏（今天绿） |

## 4. 实测（本机，HEAD 工作副本）

```text
tests.test_packaging_parts_filtered_two_books_must_agree_red                        Ran 7 … FAILED (failures=5, skipped=1)
CPQ_DWG_REAL_SAMPLES=1 tests.test_packaging_parts_filtered_two_books_must_agree_red Ran 8 in 12.966s … FAILED (failures=6)
```

- 红的是 S1–S4、S7：S1 `{'area_under_min': 2, 'edge_over_max': 1} != {'area_under_min': 2, 'area_over_max': 1}`
  （`cmp:F` 被归错桶）、S2/S3/S4 `'filtered_reason_hits_total' not found in {...}`（合计与单位没披露）、
  S7 `0 != 7`（源码里 `filtered[]` 的 `reason` 是 `reasons[0]`，没走 `_account_reason`）。
- S5 在真样本上同样红，且报的就是真图那两个数：
  `{'area_under_min': 888, 'edge_over_max': 12} != {'area_under_min': 888, 'area_over_max': 6, 'edge_over_max': 6}`（酒盒）。
- 绿的是 S6（四个 `filtered_*_total` 仍等于用 `filtered[]` 逐原因重算）与 S8
  （`reasons` 非空去重、`len(filtered[]) == filtered_total`、`FILTER_REASON_ACCOUNT_ORDER` /
  `REASON_CODES` 闭集不变、同一份 IR 两次跑出同一个 `parts_hash`）—— 这两条是护栏，改完必须还是绿。
- 未设 `CPQ_DWG_REAL_SAMPLES=1` 时 `Ran 7 (skipped=1)`：真样本类整体跳过（1 条 skip 计在类上）。

## 5. 交付边界

本批只写 Spec + 红测（业务实现不在本批）；未改业务实现、未改既有测试、未放宽任何断言、
未起服务、未连 34 / PG、样本只读（只在临时目录产出 DXF）、未 push / MR / tag / Release / 未部署。

## 6. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 460`）

只改一个文件：`tech_app/backend/services/packaging_parts.py`（`extract()` / `summarize()`，
外加把六处与过滤账无关的 `X.append({…「reason」…})` 字面量改成"先建局部变量再 `append`"，
行为逐字不变 —— 见 §6.2）。

| 契约 | 落点 | 实现 |
| --- | --- | --- |
| §2.1 `filtered[].reason` 就是主因 | `extract()` 里 `filtered.append(...)` | `"reason": _account_reason(reasons)`（原为 `reasons[0]`）；`reasons` 内容与顺序、`filtered[]` 成员 / 顺序 / `bbox` / `entity_total` 一个字不改 |
| §2.2 `stats` 三个新键 | `extract()` 的 `stats` 字面量 | `filtered_reason_hits_total`（= `Σ len(row["reasons"])`）、`filtered_reason_mix_scope="primary_reason_per_part"`、`filtered_reason_totals_scope="reason_per_part"`；两个 scope 字符串落成模块常量 `FILTERED_REASON_MIX_SCOPE` / `FILTERED_REASON_TOTALS_SCOPE` |
| §2.3 四条不变量 | 同上 | `mix` 之和 == `filtered_total`；`hits == 四个 *_total 之和 == Σ len(reasons)`；`hits - filtered_total == Σ(len(reasons)-1)`；每行 `reasons` 非空且去重（S2 / S3 / S8 逐条钉住） |
| §2.4 `summarize()` 透出三项 | `summarize()` 返回值 | 三个键逐字带出；老文档（本批之前落库的）没有 → 现算 + 常量兜底，绝不返回 `None` |
| §2.5 冻结面 | 未动 | `filtered_total` / `filtered_reason_mix` / 四个 `filtered_*_total` 的数值、`filtered[]` 结构与 `reasons` 顺序、`FILTER_REASON_ACCOUNT_ORDER` / `REASON_CODES` / `DEFAULT_OPTIONS`、过滤判据、前端文案、接口形状（只加键）全部未动 |

### 6.1 复跑命令与结果（本机 `./open-claude/.venv/bin/python -m unittest`）

```text
tests.test_packaging_parts_filtered_two_books_must_agree_red
    # Ran 7 … OK (skipped=1)          （红基 Ran 7 … FAILED (failures=5, skipped=1)）
CPQ_DWG_REAL_SAMPLES=1 tests.test_packaging_parts_filtered_two_books_must_agree_red
    # Ran 8 in 10.484s … OK          （红基 Ran 8 in 12.966s … FAILED (failures=6)）
    # 酒盒 filtered_total=900 / hits=911 / 多因件 11；圆盘盒 2988 / 3029 / 41，逐条对上 §1
```

不回归（本机 12 套件 + 状态守卫）：

```text
tests.test_packaging_parts_extraction_red tests.test_packaging_parts_outline_red     tests.test_packaging_parts_components_red tests.test_packaging_bom_business_parts_rows_red     tests.test_packaging_drawing_flow_red tests.test_packaging_parts_must_come_from_the_drawing_red     tests.test_packaging_business_parts_and_cad_plan_view_red     tests.test_packaging_business_parts_binding_size_source_red     tests.test_packaging_business_parts_must_come_from_all_drawing_evidence_red     tests.test_packaging_business_parts_outline_bbox_link_red     tests.test_packaging_bom_part_size_provenance_red tests.test_packaging_bom_size_quality_accounting_red     tests.test_spec_status_truth_red
    # Ran 270 … FAILED (failures=1, skipped=2)
    #   唯一那条是**既有挂账**、与本批无关：`packaging_bom_part_size_provenance_red` B3 的
    #   `stats` 键集冻结（多出 `size_quality`，`## 462` 已记）。已用 `git stash` 去掉本批改动
    #   复跑确认：改前同样 FAIL (failures=1)。
    #   过程中 `spec_status_truth_red` C2 曾红过（本 Spec 状态行还写着"未实现"、红测却已全绿，
    #   属危险方向）—— 本批把状态行改成"已实现"后转绿，见 §6.3。
```

### 6.2 一处为了让 §3 S7 守卫成立而做的等价改写（如实记录）

S7 的 AST 守卫要求"**凡**是 `X.append({…「reason」…})` 里那个 `reason`，取值都必须是
`_account_reason(...)`"，而它扫的是整份 `packaging_parts.py`；文件里另有六处**与过滤账无关**的
`append({…「reason」…})`（厚度冲突 `conflicts`、密度未解 `unresolved` ×3、角色未绑定 `role_unbound`、
跳过行 `skipped`）。为让这六处不被误算进"过滤账的口径"，把它们从"直接把字面量传给 `append`"
改成"先建局部变量再 `append`"（`conflict_row` / `unknown_row` / `no_grammage_row` /
`density_unresolved` / `unbound_row` / `skipped_entry`）：

- 行为逐字不变（同一个 dict，只是换个名字再传入）。`role_unbound` 那处的局部变量名刻意避开
  外层的 `unbound: List[str]`；第一次改用了 `unbound`，把外层列表覆盖成 dict、`unbound` 结果多出
  条目，`packaging_parts_extraction_red` E2 立刻转红 —— 改名后复绿（`git stash` 对比确认）。
- 于是守卫只剩 `filtered.append` 一处需要 `_account_reason`，与 §3 S7 的表意
  （"`filtered.append` 必须由 `_account_reason` 产生"）逐字对齐。

### 6.3 红测自身缺陷（如实记录）

- S7 的守卫**过宽**：Spec §3 S7 说的是"`filtered.append({…「reason」: _account_reason(…)})
  必须存在"，红测却实现成"整份文件里所有 `append({…「reason」…})` 都必须用 `_account_reason`"
  —— 把六处无关的 `reason` 字段也算了进去（红基报的正是 `0 != 7`，而不是 `0 != 1`）。
  为不改红测，本批按 §6.2 做等价改写满足它；若要收紧，应把守卫限定在 `filtered` 这个列表上。
- S6 只在合成夹具上循环（`for doc in (synth_doc(),)`），真样本那四个 `filtered_*_total` 的
  "逐原因重算"其实由 S5 末行兜着 —— 口径重复，不算错误。
- 本批红测没有校验 `filtered[].reasons` 的**顺序**（Spec §2.1 说"一个字不改"，但没有任何一条
  断言钉它）；这是覆盖缺口，不是错误。

未 push / MR / tag / Release / 部署，未连 PG / 34、未起服务、未写业务数据。

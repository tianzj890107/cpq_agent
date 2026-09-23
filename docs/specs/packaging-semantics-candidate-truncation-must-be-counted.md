# Spec：轮廓/孔位候选被上限截断却不说丢了多少 —— `stats` 上的 200 读起来像"一共就 200 条"

状态：Spec + 红测（已实现）（原状：本批只加"截断要计数、要披露"这一层 —— `build_geometry()`
只把两本书的和当布尔用，`truncated` 的计数当场丢掉，`outline`/`stats`/警告里都读不出"丢了多少"）
红测：`tests/test_packaging_semantics_candidate_truncation_red.py`
血缘：`packaging-product-outline-and-die-layer-roles.md` §2（产品级成品候选从 `boundary_candidates`
里挑）、`packaging-parts-component-chaining.md` §2.4（"被丢掉的东西必须看得见"——零件那一层已经有
`truncated` / `filtered_total` 两本账，语义层这一层没有）、`packaging-drawing-semantics.md`
（`stats` 与 `warnings` 是这一层的对外口径）。
本批 changelog 条目号：`## 450`（缺口类，作者侧）；落地条目见 `## 453`。

## 0. 一句话目标

候选被 `PACKAGING_SEMANTICS_MAX_CANDIDATES`（默认 200）截断时，语义文档必须说得出**上限是多少、
两本书各丢了多少条**；今天它只把两个被截断的长度当"总数"写进 `stats`，再加一句没有数字的
警告 —— `boundary_candidate_total: 200` 与"这张图恰好只有 200 条候选"完全同形。

## 1. 真实跑证据（本机 LibreDWG 真转换，两份真刀模图）

`tech_app/tools/dwg_sample_e2e.py` 转出 DXF → `cad_ir.parse_dxf()` → `geometry_semantics.build_geometry()`
分别按"默认上限 200"与"不设上限（0）"各跑一遍：

| 样本 | 轮廓候选真值 | 列出的 | 丢掉的 | 孔位真值 | 列出的 | 丢掉的 | `geometry["truncated"]` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `酒盒.dwg` | **5600** | 200 | **5400** | **642** | 200 | **442** | 5842 |
| `圆盘盒.dwg` | **6087** | 200 | **5887** | **222** | 200 | **22** | 5909 |

于是 `analyze()` 产出的 `stats` 是 `{"boundary_candidate_total": 200, "hole_total": 200, …}`，
警告是 `{"code": "PACKAGING_SEMANTICS_CANDIDATES_TRUNCATED", "message": "轮廓/孔位候选超过上限，
已截断，请人工核对图纸", "evidence_refs": []}`：

- **两个 200 是上限，不是真值**：`stats.boundary_candidate_total == len(outline["boundary_candidates"])`
  本身没错，但它读起来就是"这张图一共 200 条候选"，而真相是 5600 / 6087；
- **丢了多少一个字都没有**：`build_geometry()` 明明算出了 `truncated`（= 两本书被丢掉的条数之和），
  但 `packaging_semantics/__init__.py:144` 只把它当布尔用（`if geometry.get("truncated"):`），
  计数当场丢掉，`outline` / `stats` / 警告里都查不到；
- **两本书被合并成一个数**：`truncated` 是"轮廓候选 + 孔位"的和，连"是哪一本被截断"都分不出来；
  `酒盒.dwg` 的两本书都被截断（5400 / 442），`圆盘盒.dwg` 的孔位只丢了 22 条 —— 处置方式完全不同；
- **截断会往下游传染**：`product_candidates`（成品级候选）、`rejected`、`windows`、`bleed_candidates`
  都是从**截断后**的 `candidates` / `holes` 派生的（`geometry_semantics.build_geometry()` 里
  `_split_product_candidates(ir, layers, candidates)` 吃的是截断后的列表），排序又是
  `(is_closed desc, -area, outline_id)` —— 丢掉的都是**面积较小的候选**。用户看到"成品轮廓拿不到、
  请人工确认"时，无从判断这是图纸本身如此，还是被上限吃掉了。

对照：零件那一层早就把同一件事做成两本账（`stats.filtered_total` 与 `filtered_reason_mix`，
见 `packaging-parts-component-chaining.md` §2.4"被丢掉的东西必须看得见"），语义层这一层缺这半页。

## 2. 契约

### 2.1 `geometry_semantics.build_geometry(ir, layers, known, max_candidates=200)`

- 返回值**新增**两个键（键必须存在，未截断时为 `0`）：
  - `boundary_candidates_dropped`：因上限被丢掉的**轮廓候选**条数；
  - `holes_dropped`：因上限被丢掉的**孔位**条数；
- 既有键 `truncated` 的取值与含义**不变**（仍是两者之和：`dropped` 之和 == `truncated`）；
- `max_candidates <= 0` 仍是"不设上限"（既有口径），此时两个新键都为 `0`；
- 排序口径、截断口径（`candidates[:max]` / `holes[:max]`）与列表内容一个字不许改。

### 2.2 `analyze()` 的 `outline`

`outline` **新增**三个键（键必须存在；没有截断时两个 dropped 键为 `0`）：

- `candidate_cap`：本次**生效**的上限值（= 本次传给 `build_geometry` 的 `max_candidates`；
  `0` 表示不设上限）—— 没有它，`200` 与"恰好 200 条"永远分不出来；
- `boundary_candidates_dropped_total`：`build_geometry()["boundary_candidates_dropped"]`；
- `holes_dropped_total`：`build_geometry()["holes_dropped"]`。

既有 `outline` 键（`boundary_candidates` / `holes` / `rejected` / `bleed_candidates` / `windows` /
`panel`）与它们的取值口径不变。

### 2.3 `PACKAGING_SEMANTICS_CANDIDATES_TRUNCATED` 警告必须带数

- 出现条件**不变**：只有真被截断（`truncated > 0`）才出现，不许为了披露而常驻；
- 警告项**新增**键 `dropped_total`（= 两本书被丢掉的条数之和）；
- `message` 必须让人读出"上限 + 两本书各丢了多少"：上限值、`boundary_candidates_dropped`、
  `holes_dropped` 三个数都要出现在 `message` 里（措辞与分隔符不限，但三个数必须是可读的十进制数字）；
- `code` 仍是 `PACKAGING_SEMANTICS_CANDIDATES_TRUNCATED`、`evidence_refs` 仍是 `[]`。

### 2.4 非目标 / 冻结面

- 不许改 `stats` 的**键集与数值口径**：`boundary_candidate_total` / `hole_total` 仍是"列出来的条数"、
  仍是 `len(...)`（本批是**加**披露，不是把这两个键改成真值）；
- 不许改 `REQUIRED_KEYS` / `SEMANTICS_VERSION` / 警告 code 闭集 / 截断与排序口径；
- 不许把上限调大或调小（`PACKAGING_SEMANTICS_MAX_CANDIDATES` 的环境变量名与默认值 200 都不动）；
- 不许改 `packaging_product_outline` 的产品级候选判据（本批只加"丢了几个"的账）；
- 不许动后端接口形状与前端（本批只写 Spec + 红测）。

## 3. 红测映射（`tests/test_packaging_semantics_candidate_truncation_red.py`，8 条）

| 用例 | 夹具 | 期望 | 现状 |
| --- | --- | --- | --- |
| T1 | 合成：250 条闭合候选 + 210 个孔位（默认上限 200） | `outline.candidate_cap == 200`、`boundary_candidates_dropped_total == 50`、`holes_dropped_total == 10`；`stats.boundary_candidate_total == 200`（口径不变） | 红（三个键都不存在） |
| T2 | 合成：10 条候选 + 3 个孔位（未触顶） | 三个键都在、两个 dropped 为 `0`；**没有** `PACKAGING_SEMANTICS_CANDIDATES_TRUNCATED` 警告 | 红（键不存在） |
| T3 | T1 的产物 | 警告仍在且 `code` 不变，`message` 里同时出现上限 `200`、`50`、`10` 三个数，警告项 `dropped_total == 60` | 红（message 无数字、无 `dropped_total`） |
| T4 | T1 的文档 | `build_geometry()` 不设上限跑出来的真值 == 列出的 + 丢掉的（候选与孔位各一条） | 红（没有 dropped 键） |
| T5 | `PACKAGING_SEMANTICS_MAX_CANDIDATES=7` + 20 条候选 | `candidate_cap == 7`、`boundary_candidates_dropped_total == 13`；清空环境变量后 `candidate_cap == 200` | 红（上限不可读） |
| T6 | 两份真实样本（`CPQ_DWG_REAL_SAMPLES=1`） | 两份图都触顶（`boundary_candidate_total == candidate_cap == 200`）、轮廓候选至少丢 1000 条、孔位至少丢 1 条、警告里三个数都在，且"列出的 + 丢掉的 == 不设上限的真值" | 红（没有这些键） |
| T7 | 合成 + T1 的产物 | 护栏：`stats` 键集与既有 8 键逐字相同、`REQUIRED_KEYS` 仍在、`SEMANTICS_VERSION` 不变、`outline` 既有 6 键仍在、`stats.*_total == len(outline[...])`、截断/排序口径不变 | 护栏（今天绿） |
| T8 | `packaging_semantics/__init__.py` 源码 | 接线守卫：`analyze()` 必须把两个 dropped 键写进 `outline`（源码里出现键名），不许只留 `if geometry.get("truncated")` 的布尔用法 | 红（源码里没有这两个键名） |

## 4. 实测（本机，HEAD 工作副本）

```text
tests.test_packaging_semantics_candidate_truncation_red                        Ran 7 … FAILED (failures=6, skipped=1)
CPQ_DWG_REAL_SAMPLES=1 tests.test_packaging_semantics_candidate_truncation_red Ran 8 in 10.645s … FAILED (failures=7)
```

- 红的是 T1–T5、T8：T1/T2/T4 `candidate_cap`（或 dropped 键）不在 `outline` 里、T3
  `None != 60`（警告项没有 `dropped_total`）、T5 `None != 7`（生效上限读不出来）、T8 源码里
  `candidate_cap` 一个字母都没有（`analyze()` 只把 `truncated` 当布尔用）。T6 在真样本上同样红：
  `'candidate_cap' not found in {'boundary_candidates': [{'outline_id': 'out:124B', …}]}`。
- 绿的是 T7（冻结面护栏：`stats` 八键逐字不变、`outline` 六键都在、
  `boundary_candidate_total == len(boundary_candidates)`、触顶时列表长度就是上限、排序口径不变）。
- 未设 `CPQ_DWG_REAL_SAMPLES=1` 时 `Ran 7 (skipped=1)`：真样本类整体跳过（1 条 skip 计在类上）。
- §1 的两份真样本真值是可复核的：不设上限（`cap=100000`）跑同两份 IR 得到
  轮廓候选 5600 / 6087、孔位 642 / 222，与列出的 200 / 200 之差就是被丢掉的 5400 / 442 与
  5887 / 22 —— 这条账今天在产物里任何地方都读不到。

## 5. 交付边界

本批只写 Spec + 红测（业务实现不在本批）；未改业务实现、未改既有测试、未放宽任何断言、
未起服务、未连 34 / PG、样本只读（只在临时目录产出 DXF）、未 push / MR / tag / Release / 未部署。

## 6. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 453`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 两本账 | `geometry_semantics.build_geometry()` | 新增 `boundary_candidates_dropped` / `holes_dropped`（未截断为 `0`）；**分两处**计数（候选那一段与孔位那一段各记一次），`truncated` 改成两者之和 —— 取值与含义不变；`max_candidates <= 0` 仍是"不设上限"，两个新键都为 `0`；排序与 `[:max]` 截断口径一字未改 |
| §2.2 `outline` 三个键 | `packaging_semantics/__init__.py:analyze()` | `outline` 新增 `candidate_cap`（= 本次生效的 `max_candidates`，`0` = 不设上限）、`boundary_candidates_dropped_total`、`holes_dropped_total`；既有六键与取值口径不变 |
| §2.3 警告带数 | 同上 | `PACKAGING_SEMANTICS_CANDIDATES_TRUNCATED` 仍是"只有真被截断才出现"，新增 `dropped_total`（两本书之和），正文改成"轮廓/孔位候选超过上限（200）：轮廓候选丢了 5400 条、孔位丢了 442 条，已截断，请人工核对图纸" —— 三个数都是可读十进制 |
| §2.3 透传 | `_merge_warnings()` | 既有警告行仍是三键；带数的警告（`dropped_total`）在这一层**原样透传**，不再被合并掉 |
| §2.4 冻结面 | 未动 | `stats` 键集与 `boundary_candidate_total` / `hole_total`（仍是 `len(...)`）、`REQUIRED_KEYS`、`SEMANTICS_VERSION`、警告 code 闭集、`PACKAGING_SEMANTICS_MAX_CANDIDATES` 默认值 200、产品级候选判据、后端接口与前端，一行未改 |

真样本复跑（本机 LibreDWG 真转换，只读；`candidate_cap=200`）：

```
酒盒.dwg   ：列出 200 / 丢掉 5400（轮廓）+ 442（孔位），真值 5600 / 642
圆盘盒.dwg ：列出 200 / 丢掉 5887（轮廓）+ 22（孔位），真值 6087 / 222
警告正文   ：轮廓/孔位候选超过上限（200）：轮廓候选丢了 5400 条、孔位丢了 442 条，已截断…
             （圆盘盒：…丢了 5887 条、孔位丢了 22 条…）
```

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_semantics_candidate_truncation_red
Ran 7 tests ... OK (skipped=1)        # 红基 6 红 + 1 skip；T6 真样本组默认跳过

CPQ_DWG_REAL_SAMPLES=1 ./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_semantics_candidate_truncation_red
Ran 8 tests in 9.7s ... OK            # 红基 7 红 → 8 全绿

./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_semantics_red \
    tests.test_packaging_parts_extraction_red tests.test_packaging_product_outline_red \
    tests.test_packaging_layer_name_unicode_escape_red \
    tests.test_packaging_semantics_candidate_truncation_red tests.test_packaging_drawing_flow_red \
    tests.test_packaging_business_parts_and_cad_plan_view_red tests.test_dxf_cad_ir_red
Ran 239 tests ... OK (skipped=6)      # 语义/零件/成品轮廓/图纸链路 零回归
```

红测自身缺陷：无。

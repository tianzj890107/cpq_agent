# Spec：`role_known_ratio` 是唯一一个只能靠比值反推的分子 —— 门禁的"角色已知件数"地板会被悄悄放行

状态：Spec + 红测（已实现）（原状：实现提示词只在会话交付、业务实现不在本批；红基见 §4；
`## 462` 已落地，落点与实测见 §6）
红测：`tests/test_packaging_parts_role_known_numerator_red.py`
血缘：`packaging-parts-coverage-truthfulness.md` §2.1（"分子 / 分母 / 证据口径"三件套 ——
`closed_total` / `material_known_total` / `thickness_known_total` / `processable_total` 都给了分子，
只有 `role_known_ratio` 没给）、`packaging-parts-gate-threshold-recalibration.md` §C1/C3
（门禁只比**计数**、禁止"比值或计数"双通道；`role_known_total` 地板 8）、
`packaging-parts-role-lookup-disclosure.md`（角色出处的另一本账）。
本批 changelog 条目号：`## 462`。

## 0. 一句话目标

`summarize()` 给了 `role_known_ratio`（三位小数）却没给分子，于是门禁为了比"角色已知件数"的
地板，只能**把比值乘回分母**（`int(round(ratio × part_total))`）。比值是舍入过的，
这个反推在分母变大时会多报或少报 —— 而它比的是**地板**：多报 1 就能让一件本该 fail 的样本
报 go。分子必须由引擎自己说出来，门禁直读，不许反推。

## 1. 真实跑证据（本机，HEAD 工作副本）

同一条代码路径（`packaging_parts.extract()` → `summarize()` → 门禁现在用的那个反推公式）：

| 夹具 | `part_total` | 真·角色已知件数 | `summarize().role_known_ratio` | 门禁反推 `int(round(ratio×T))` | 差 |
| --- | --- | --- | --- | --- | --- |
| 合成 3000 件（2 件角色已知） | 3000 | 2 | `0.001` | **3** | **+1**（地板 3 会被放行） |
| 合成 16000 件（1 件角色已知） | 16000 | 1 | `0.0` | **0** | **−1** |
| `酒盒.dwg`（真转） | 263 | 0 | `0.0` | 0 | 0 |
| `圆盘盒.dwg`（真转） | 312 | 9 | `0.029` | 9 | 0 |

- 根因是精度：`packaging_parts._round(value) = round(float(value), 3)`（`packaging_parts.py:439`），
  `role_known_ratio` 只有三位小数；`1/16000 = 6.25e-05` 直接舍成 `0.0`，`2/3000 = 6.67e-04`
  舍成 `0.001`。反推 `ratio × T` 因此可以偏 ±1 甚至更多（`T` 越大越糟）。
- 门禁那一行就在 `tech_app/tools/packaging_parts_gate.py`：
  `summary["role_known_total"] = int(round(float(summary.get("role_known_ratio") or 0.0) * int(summary["part_total"])))`，
  随后 `sample_verdict()` 拿它比 `THRESHOLDS["圆盘盒.dwg"]["role_known_total"] = 8`。
  也就是说**go/no-go 的地板会被舍入误差左右**：多报 1 → 地板被悄悄放行；少报 → 明明够也被判 fail。
- 今天两份真样本恰好整除（0 与 9），所以没出事；这是"再大四倍的图纸就会翻车"的结构性隐患。
- 顺带：`closed_total` / `material_known_total` / `thickness_known_total` / `processable_total` /
  `thickness_unknown_total` 这些分子早就显式给了（同一条 Spec 系列），**只有 `role_known_ratio`
  落单** —— 修法就是把它补齐，不是给门禁开一条新口径。

## 2. 契约

### 2.1 `summarize()` 必须给出 `role_known_total`

- 新增键 `role_known_total`（键必须存在；与 `part_total` 同一次遍历算出来），
  取值口径逐字等于今天算 `role_known` 的那一句：
  `件里 role 不是 "" / "unknown" 的件数`；
- 自洽：`0 <= role_known_total <= part_total`，且
  `role_known_ratio == round(role_known_total / part_total, 3)`（与既有 `_round` 精度一致）；
  `part_total == 0` 时比值仍是 `0.0`；
- `role_known_ratio` 的**取值与精度一个字不改**（本批只加分子，不改比值）。

### 2.2 门禁不许再反推分子

`tech_app/tools/packaging_parts_gate.py`：

- `_sample_metrics()` 必须直接取 `summary["role_known_total"]`（引擎给的分子），
  **不许**再出现"用 `role_known_ratio` 乘 `part_total` 反推"的写法（含 `.get("role_known_ratio")`）；
- 读数行（`role_known_total=%d`）与地板比较（`sample_verdict()`）的**语义与数值一个字不改** ——
  变的只是这个数从哪来；
- `THRESHOLDS`（酒盒 `closed_total 7`；圆盘盒 `closed_total 32`、`role_known_total 8`）与
  `GATE_ITEMS` 六项、退出码语义（`verdict != go` → 退出码 1；`--env production` 下 skip 算 fail）
  都不动。

### 2.3 非目标 / 冻结面

- 不许改 `_round` 的精度（3 位）、不许改其它比率的取值；
- 不许给门禁新增"比值或计数"双通道判据（Spec `packaging-parts-gate-threshold-recalibration.md` §C1 明确禁止）；
- 不许改 `summarize()` 既有键（本批只加 `role_known_total`）、不许改 `extract()` 的零件内容；
- 不许改前端（面板用不用这个键由它自己决定，本批不碰）。

## 3. 红测映射（`tests/test_packaging_parts_role_known_numerator_red.py`，8 条）

| 用例 | 夹具 | 期望 | 现状 |
| --- | --- | --- | --- |
| R1 | 合成 3 件（1 件角色已知） | `summarize().role_known_total == 1` | 红（键不存在） |
| R2 | 合成 | `role_known_total` 等于从零件行重算的值；`0 <= 它 <= part_total`；`role_known_ratio == round(分子/分母, 3)` | 红（键不存在） |
| R3 | 合成 3000 件（2 已知）/ 16000 件（1 已知） | `role_known_total` 分别是 2 / 1，而 `int(round(ratio × part_total))` 是 3 / 0（反推会多报 / 少报） | 红（键不存在） |
| R4 | 两份真实样本（`CPQ_DWG_REAL_SAMPLES=1`） | `role_known_total` 等于从零件行重算的值，且 `role_known_ratio == round(分子/分母, 3)`（比值口径不变） | 红 |
| R5 | `packaging_parts_gate.py` 源码（ast） | `_sample_metrics()` 里必须出现 `summary["role_known_total"]`，且**不许**出现 `role_known_ratio` 这个取数 | 红（今天正是反推） |
| R6 | `packaging_parts_gate.py` | 护栏：`THRESHOLDS` 与 `GATE_ITEMS` 逐字不变；`sample_verdict()` 仍只比计数、等于地板算过、低于地板给 1 条原因、且是纯函数 | 护栏（今天绿） |
| R7 | `summarize()` 产物 | 护栏：既有分子键（`closed_total` / `material_known_total` / `thickness_known_total` / `thickness_unknown_total` / `processable_total`）与 `role_known_ratio` 仍在且口径不变 | 护栏（今天绿） |
| R8 | `packaging_parts.py` 源码 | 接线守卫：`role_known_total` 与既有 `role_known` 计数出自同一次遍历（源码里 `role_known` 与 `"role_known_total"` 同时出现），不许由比值反算 | 红（源码里没有 `role_known_total`） |

## 4. 实测（本机，HEAD 工作副本 `8f8b011` + 本批未提交改动）

```text
tests.test_packaging_parts_role_known_numerator_red                            Ran 9  in 0.538s  FAILED (failures=7, skipped=1)
CPQ_DWG_REAL_SAMPLES=1 tests.test_packaging_parts_role_known_numerator_red     Ran 10 in 12.210s FAILED (failures=8)
  R1  'role_known_total' not found in {…}（键不存在，Spec §2.1）                                    （红）
  R2  同上：分子不可复核                                                                             （红）
  R2b 同上：0 件时分母 0 的分支也拿不到分子                                                          （红）
  R3  两个 subTest 都红（T=3000 / T=16000）                                                          （红）
  R4  真样本：`酒盒.dwg：part_total=263 角色已知=0 ratio=0.0（重算 0）` 打到一半就因缺键红            （红）
  R5  'role_known_ratio' unexpectedly found in {'…','role_known_total','role_known_ratio',…}        （红）
  R8  `summarize()` 返回字典里没有 'role_known_total'（今天只能由比值反算）                          （红）
  R6  门禁地板 `THRESHOLDS` 逐字不变 + 六项清单 id/顺序不变                                          （护栏绿）
  R6b `sample_verdict()` 只比计数：8 过 / 7 给 1 条原因 / 比值再漂亮也不放行 / 纯函数                 （护栏绿）
  R7  既有分子键（closed/material/thickness/processable）与 `role_known_ratio` 取值口径不变          （护栏绿）
```

同一趟真转换里的原始读数（`role_known_total` 键在两份样本上都不存在，门禁只能反推）：

```text
酒盒.dwg   零件 263 件  角色已知=0  role_known_total 键=False  ratio=0.0    门禁反推 int(round(ratio*263))=0
圆盘盒.dwg 零件 312 件  角色已知=9  role_known_total 键=False  ratio=0.029  门禁反推 int(round(ratio*312))=9
门禁现场：python tech_app/tools/packaging_parts_gate.py --env local --json
          → verdict=go  summary={ok:5, fail:0, manual:1}，读数行里打的就是反推来的 role_known_total=0
```

## 5. 交付边界

本批只写 Spec + 红测（业务实现不在本批）；未改业务实现、未改既有测试、未放宽任何断言、
未起服务、未连 34 / PG、样本只读（只在临时目录产出 DXF）、未 push / MR / tag / Release / 未部署。

## 6. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 462`）

改两个文件，都只动"这个数从哪来"，不动任何数值口径：

| 契约 | 落点 | 实现 |
| --- | --- | --- |
| §2.1 `summarize()` 给出 `role_known_total` | `tech_app/backend/services/packaging_parts.py` 的 `summarize()` 返回字典 | 新增 `"role_known_total": role_known` —— 与既有 `"role_known_ratio": _ratio(role_known)` 出自**同一次遍历的同一个计数变量**（R8 用 AST 钉住这一点：两者名字集相同、分子里不出现 `ratio`） |
| §2.2 门禁直读、不许反推 | `tech_app/tools/packaging_parts_gate.py` 的 `_sample_metrics()` | 原来那句 `int(round(ratio × part_total))` 换成 `int(summary.get("role_known_total") or 0)`；函数体里不再出现比值取数（R5 用 AST 钉住：既不读也不提） |
| §2.3 冻结面 | 未动 | `_round` 精度（3 位）、`role_known_ratio` 的取值、其它比率、`summarize()` 既有键、`extract()` 的零件内容、`THRESHOLDS`（酒盒 `closed_total 7`；圆盘盒 `closed_total 32` / `role_known_total 8`）、`GATE_ITEMS` 六项与顺序、`sample_verdict()` 的"只比计数"与退出码语义、前端 —— 全部未动 |

### 6.1 复跑命令与结果（本机 `./open-claude/.venv/bin/python -m unittest`）

```text
tests.test_packaging_parts_role_known_numerator_red
    # Ran 9 … OK (skipped=1)             （红基 Ran 9 in 0.538s … FAILED (failures=7, skipped=1)）
CPQ_DWG_REAL_SAMPLES=1 tests.test_packaging_parts_role_known_numerator_red
    # Ran 10 in 10.206s … OK             （红基 Ran 10 in 12.210s … FAILED (failures=8)）
    # 酒盒 263 件 角色已知 0（ratio 0.0）；圆盘盒 312 件 角色已知 9（ratio 0.029）—— 分子逐条复核

tests.test_packaging_parts_downstream_gate_red tests.test_packaging_parts_gate_threshold_red \
tests.test_packaging_parts_extraction_red tests.test_packaging_parts_outline_red \
tests.test_packaging_parts_components_red tests.test_packaging_parts_filtered_two_books_must_agree_red \
tests.test_packaging_parts_ir_read_failure_red tests.test_packaging_parts_role_lookup_disclosure_red \
tests.test_packaging_parts_list_visibility_red tests.test_packaging_parts_thickness_facts_red \
tests.test_packaging_bom_size_quality_accounting_red \
tests.test_packaging_business_part_size_must_be_confirmed_by_dimension_red \
tests.test_packaging_drawing_flow_red tests.test_spec_status_truth_red
    # Ran 230 in 66.657s … OK (skipped=5)（首次跑时 `spec_status_truth_red` C2 因本 Spec 状态行
    #   还写着"未实现"而红，改成本节的状态行后转绿）

现场（门禁读数行里的分子现在是引擎直给的那个）：
    ./open-claude/.venv/bin/python tech_app/tools/packaging_parts_gate.py --env local --json
```

### 6.2 红测自身缺陷（如实记录）

- R3 只在**合成**夹具上复现"反推会偏 ±1"（3000 件 / 16000 件）；真样本今天恰好整除
  （0 / 9），所以 R4 只复核"分子等于从零件行重算的值 + 比值口径不变"，**没有**在真图上钉"反推必错"
  —— 那需要一份 16000 件级的真图，本机没有。
- R5 是"函数体里不许出现比值取数"的源码守卫；它不证明门禁在**所有**调用路径上都拿到了分子
  （例如有人绕过 `_sample_metrics()` 自己拼 metrics）。这一条由 R6b 的纯函数语义兜着。
- 本 Spec 头部写的 changelog 条目号是 `## 462`，与已落地的
  `packaging-business-part-size-must-be-confirmed-by-dimension.md`（也是 `## 462`）**撞号**。
  本批按自己的号写 `## 462`。

未 push / MR / tag / Release / 部署，未起服务、未连 PG / 34、样本只读（只在临时目录产出 DXF）。

### 6.4 人工复核（红测覆盖缺口之外，2026-09-23）

§6.2 记的两条缺口复核：

```text
门禁源码：_sample_metrics() 的 AST unparse 里还出现 "role_known_ratio" 吗 → False（已不再反推）
引擎：summarize({"parts": [], "stats": {}}) → role_known_total=0、role_known_ratio=0.0
      （没有零件时分子是 0、比值仍是 0.0，Spec §2.1 的 `part_total == 0` 分支）
门禁读数：--env local --json → verdict=go，酒盒 role_known_total=0 / 圆盘盒=9（引擎直给的值）
```

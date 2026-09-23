# Spec：业务部件的「识别 / 推断 / 待确认」三档必须走到读接口与页面（今天只活在解析返回值里）

状态：Spec + 红测（已实现）（原状：三档算得出来、却一个读接口都不带、页面一个字不显示 —— 实测见 §1）
红测：`tests/test_packaging_business_truth_state_disclosure_red.py`
血缘：`docs/specs/packaging-wine-dwg-parts-and-downstream-truth.md` §2.1 第 2 条（三档的定义与判定
**已经落地**：行上 `truth_state` + `authority.truth_states` 闭集）与 §7.4 挂账
（「未把 `size_quality` / `truth_state` 写进 `business_parts_document()` 的行形状 … 另起一批」）；
`docs/specs/packaging-dwg-generalization-and-downstream-trust.md` §7 最后一条（**本批兑现的那一条**：
「页面和 API 如实展示识别、推断、待确认及下游可用覆盖率」）与 §8.4（把这条记为"不在本批"）；
`docs/specs/packaging-parts-must-be-derived-from-the-drawing.md` §2.6（来源披露：不许伪称权威清单）。
本批 changelog 条目号：`## 476`。

## 0. 一句话目标

解析器**已经**把每一件业务部件分成三档（`observed` = 图上直接读到、`inferred` = 被视图方向规则纠过名、
`pending_confirmation` = 结构规则补出来的采购件），但这条事实只活在
`packaging_business_part_resolver.resolve_business_parts()` 的返回值里：
`packaging_parts.business_parts_document()` 的行形状是固定五键（`business_part_code` / `name` /
`authority` / `geometry_binding` / `thumbnail`），**不带** `truth_state`；`summarize_business_parts()`
也没有三档计数；页面（`tech_app/frontend/app.js`）全文 0 处 `truth_state`。
于是"从图纸推导（待人工确认）"这句话在页面上是一句笼统的提示 —— 客户看不出**哪几件是图上有的、
哪几件是规则补的**，而后者（真样本 `内卡` / `磁铁`）恰好是最不该被当成"图纸识别"的两件。

## 1. 实测证据（本机，只读；HEAD `8f8a910` 工作副本）

| 读数 | 实测 |
| --- | --- |
| `packaging_business_part_resolver.TRUTH_STATES` | `('observed', 'inferred', 'pending_confirmation')` |
| `resolve_business_parts()["detail"]` | 已有 `truth_state_counts` / `inferred_total` / `pending_confirmation_total`（`## 466` 落地） |
| `business_parts_document()` 的行键（图纸来源与工作簿来源**都是**） | `['authority', 'business_part_code', 'geometry_binding', 'name', 'thumbnail']` —— **没有** `truth_state` |
| 同一份权威行（4 件，分别 `observed` / `inferred` / `pending_confirmation` / 没给档位）过一遍文档 | `stats = {"ambiguous_total": 0, "bound_total": 0, "business_part_total": 4, "partial_total": 0, "unbound_total": 4}` —— **三档一件都数不出来** |
| `summarize_business_parts(doc)` | 只有上面那五个统计；没有 `truth_state_counts` |
| 流程 `detail`（`packaging_drawing_flow/steps.py::_resolve_business_parts` 的复制清单） | 只有 `authority_source` / `business_part_total` / `bound_total` / … 20 个键，**没有**三档 |
| 读接口 `_business_parts_body()` | `summary` 来自 `summarize_business_parts()` ⇒ 同样没有三档 |
| `tech_app/frontend/app.js` 全文 `truth_state` | **0 处**（`grep` 实测） |
| 「下游可用覆盖率」 | **已披露、本批不新造**：`packaging_parts.summarize()` 有 `processable_total` / `processable_ratio` / `solid_ok_ratio`，页面有既有的「3D 覆盖率 …%（k/n 件可挤出）」文案（`app.js` `packagingSolidCoverageText()`） |

## 2. 契约

### 2.1 C1：行上必须带 `truth_state`（闭集不许被偷偷放宽）

`packaging_parts.business_parts_document()` 的每一行新增 `truth_state`：

- 权威行（`authority.parts[i]`）里 `truth_state` 落在三档闭集内 ⇒ **逐字照抄**；
- `authority.derived_from_drawing is True` 但那一行没有档位 ⇒ 给 `"observed"`（解析器自己就是这么兜的）；
- **不是**从图纸推导的（工作簿导入的权威清单）⇒ 给 `""`：那种行的"识别 / 推断 / 待确认"根本不适用，
  不许伪称"图上识别"；
- 闭集外的任何值（拼错、未来的第四档、`None`）⇒ 与"没给"同一条路（不抛异常）；
- 既有五键**逐字保留**，键名与顺序规则不动。

### 2.2 C2：计数必须给出（三档各一个分子，外加一个 `truth_state_counts`）

`business_parts_stats()` 新增（`summarize_business_parts()` 与文档 `stats` 因此一起有）：

- `truth_state_counts`：三档**键必须都在**（缺的给 `0`，不许 `null`、不许省略）；
- `observed_total` / `inferred_total` / `pending_confirmation_total`：三个整数分子；
- `business_part_total == 0` ⇒ 全 `0`；`truth_state` 是 `""` / 闭集外的行**只进 `business_part_total`**，
  不计入任何一档（三档之和 ≤ `business_part_total`）；
- 既有五笔账（`business_part_total` / `bound_total` / `partial_total` / `unbound_total` /
  `ambiguous_total`）取值一个字不改。

### 2.3 C3：判定只有一处（本批只搬运）

`packaging_parts` 侧新增的闭集常量必须与 `packaging_business_part_resolver.TRUTH_STATES`
**逐字相同**；三档**怎么判**仍在解析器里（视图方向规则 / 结构规则），本批不新写判定、
不改规则号、不改 `name_from_drawing` / `name_rule` / `structure_rule` 任何一个键。

### 2.4 C4：流程 `detail` 也要说得出三档

`packaging_drawing_flow/steps.py::_resolve_business_parts()` 的复制清单新增
`truth_state_counts` / `observed_total` / `inferred_total` / `pending_confirmation_total` 四个键，
值取解析器 `detail` 的同名键（缺失就不写进去，与既有"复制清单"同一条路）。
页面/看板因此能在**跑完的那一次**就说出三档，而不是非要再读一次接口。

### 2.5 C5：读接口如实带出去，老文档不许炸

`main.py::_business_parts_body()`：

- `summary.stats` 带 C2 的四个键（来自 `summarize_business_parts()`，不另算一遍）；
- `business_parts` 行带 C1 的 `truth_state`（存在就逐字带出去；闭集外或 `""` 也照原样带）；
- **存库里的老文档**（没有这些键）读起来必须给零计数、行上缺这一键时**不抛** `KeyError` / 不 500 ——
  页面把「缺键 / 空串」都当"不显示这一档"（`packagingBusinessPartTruthLabel(undefined)` 回 `""`）。

### 2.6 C6：页面照 payload 显示，不猜

`tech_app/frontend/app.js` 新增两个**纯函数**（可被 `node` 直接执行：体内不许有
`document` / `window` / `localStorage` / `sessionStorage` / `$(` / `fetch(`）：

- `packagingBusinessPartTruthLabel(truth_state)`：
  `observed` → `图上识别`；`inferred` → `规则纠名（推断）`；
  `pending_confirmation` → `结构规则补件（待确认）`；其余 → `""`；
- `packagingBusinessTruthLine(doc)`：从 `doc.summary.stats`（兼容扁平的四个分子）取数，
  三档合计为 `0` 时回 `""`，否则回 `识别 a · 推断 b · 待确认 c`。

渲染：业务部件树上三档行数非空时多一行 `data-qq-truth-line`；每一行按其 `truth_state` 加
`data-qq-truth-state="<state>"` 与标签文案（`""` 的行**不加**这个属性）。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_parts.py`：`business_parts_document()` 行形状 +
   `business_parts_stats()` + 闭集常量。
2. `tech_app/backend/services/packaging_drawing_flow/steps.py`：`_resolve_business_parts()` 的复制清单。
3. `tech_app/frontend/app.js`：两个纯函数 + `renderPackagingBusinessTree()` 的两处渲染。
4. `changelog/changelog_9_21_25.md`：`## 476`。

禁止：改解析器的三档判定 / 规则号 / 闭集；改 `business_parts_id` 的生成口径；改五笔账的取值；
改 `size_quality` 的门禁口径；新造"下游可用覆盖率"这个比值（既有 `processable_ratio` /
`solid_ok_ratio` / 「3D 覆盖率」文案照旧）；改任何测试；改索引 / 路由 / 权限；新增依赖。

## 4. 未做 / 边界（如实记）

- **`size_quality` 本批未上行走**：`packaging-wine-dwg-parts-and-downstream-truth.md` §7.4 把它与
  `truth_state` 并列挂账，但它的**下游门禁**在 `business_process_inputs()` / `business_cost_inputs()`
  里读的是 `authority.size_quality`（`_text(authority.get("size_quality"))`，老载荷缺键时**不判死**），
  往行上搬要先定"行上的值以谁为准"，那是另一份 Spec 的事。本批只搬 `truth_state`。
- 本批**不**新造覆盖率：`processable_ratio` / `solid_ok_ratio` 与页面的「3D 覆盖率」文案就是既有真值，
  红测只钉"它们仍可从读载荷取到"，不钉新的比值。
- 未读金标、未按 DWG sha 命中金标、未起服务、未连 PG / 34、未写业务数据、未改成本公式与费率。

## 5. 红测与反向对照

红测：`tests/test_packaging_business_truth_state_disclosure_red.py`
（A 行形状 / B 计数 / C 单一口径 / D 流程 detail / E 读接口 / F 页面）。

- 红基（把三个生产文件还原成 HEAD `8f8a910` 工作副本）：`Ran 22 … FAILED (failures=16, errors=2)`
  —— 18 红 / 4 绿。红的正好是
  A1/A2/A3/A5（行上没有档位）、B1/B2/B4（没有三档计数）、B3/B5（`ERROR: KeyError` —— 键还不存在，
  断言直接读到 `truth_state_counts`）、C1（闭集常量还没加）、D1、E1/E2/E3、F1/F2/F3/F4；
  绿的 4 条是护栏：A4（既有五键）、B6（既有五笔账）、C2（解析器的三档判定不许被本批改动）、
  E4（读接口既有披露键）。
- 反向对照 1（只还原 `packaging_parts.py`，`steps.py` / `app.js` 保持本批）⇒
  `Ran 22 … FAILED (failures=11, errors=2)`（D1 与 F 组四条转绿，其余照旧红）；
- 反向对照 2（只去掉 `_resolve_business_parts()` 复制清单里的四个键）⇒ **D1 单条红**；
- 反向对照 3（只把 `packagingBusinessPartTruthLabel()` 的 `inferred` 文案改成 `推断`）⇒
  **F2 单条红**（页面文案必须逐字取自 §2.6）。

## 6. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 476`）

| 契约 | 落点 | 复跑结果 |
| --- | --- | --- |
| C1 行上带 `truth_state` | `packaging_parts.business_parts_document()` + `_business_truth_state()` | 图纸来源逐字照抄（缺档位兜 `observed`）、工作簿来源给 `""`；既有五键未动 |
| C2 三档计数 | `packaging_parts.business_parts_stats()`（文档 `stats` 与 `summarize_business_parts()` 共用） | `truth_state_counts`（三键都在）+ 三个分子；空文档全 0 |
| C3 判定只有一处 | `packaging_parts.BUSINESS_TRUTH_STATES` | 与 `resolver.TRUTH_STATES` 逐字相同；真样本 28 件仍然三档齐全 |
| C4 流程 `detail` | `packaging_drawing_flow/steps.py::_resolve_business_parts()` 复制清单 +4 键 | D1 绿；反向对照 2 单条红 |
| C5 读接口 | `main.py::_business_parts_body()`（`summary` 直接来自 `summarize_business_parts()`） | E1/E2/E3/E4 绿；老文档零计数、不抛异常 |
| C6 页面 | `tech_app/frontend/app.js`：`packagingBusinessPartTruthLabel()` / `packagingBusinessTruthLine()` + 业务树两处渲染 | F1–F4 绿（`node` 真跑两条纯函数 + `node --check` 通过） |

复跑（本机 `./open-claude/.venv/bin/python -W ignore -m unittest`）与全量保护网见 changelog `## 476`。

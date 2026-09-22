# 规格：BOM 的尺寸质量必须有账（包围盒行不许和真展开行一样算"已算好"）

状态：Spec + 红测（已实现）（落地见 §5：行顶层提升 / `stats.size_quality` 三档 / `gaps.bbox_only`）
红测：`tests/test_packaging_bom_size_quality_accounting_red.py`

血缘：承接 `packaging-bom-part-size-provenance.md`（已实现：绑定留痕里的 `size_source` / `outline_status` /
`size_quality`；本批**不重开**它"不改配对、不挡成本"的决定，只补**账**）、
`packaging-bom-part-size-provenance.md` §1.3（成本引擎按 `cut_length × cut_width` 算料）、
`packaging-silent-degradation-disclosure.md`（同一条披露纪律：事实存在但读接口看不见 = 等于没有）。

## 0. 一句话目标

一份 BOM 里"尺寸来自包围盒"的行，必须在**读接口行上**、**整份汇总上**、**报价产物上**都看得出来，
并且口径只有一处 —— 不许再靠界面各自钻 `size_source_json` 猜。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

### 1.1 单行已留痕，但读接口不提升：BOM 面板看不到"这一行是包围盒"

`tech_app/backend/services/packaging_bom.py:881 _item_out()`：

```python
    if isinstance(binding, dict) and binding:
        out.setdefault("binding_evidence", binding.get("binding_evidence") or {})
        out.setdefault("binding_method", _text(binding.get("binding_method")))
        out.setdefault("bound_by", _text(binding.get("bound_by")))
        out.setdefault("part_role", _text(binding.get("part_role")))
```

四个字段被提到行顶层，**尺寸来源三件套（`size_source` / `outline_status` / `size_quality`）没有**
—— 要看这一行的数是不是包围盒，只能自己钻 `size_source_json.dwg_binding`。
同一个仓库里另一处（`packaging_bom.py:657-658` 的未映射清单）反而带上了这两样：

```python
            "size_source": _text(binding.get("size_source") or row.get("size_source")),
            "outline_status": _text(binding.get("outline_status") or row.get("outline_status")),
```

同一份数据、两个口径 —— 前端 `app.js` 里也没有任何 `size_quality` 用法（`grep -c` = 0），
BOM 面板上包围盒行与真展开行显示完全一样。

### 1.2 整份 BOM 没有尺寸质量账

`packaging_bom.py:897 _stats()` 只数四件事：`computed` / `needs_input` / `locked` /
`material_unresolved`。一份 33 行的 BOM 里"3 行的尺寸其实是包围盒"在读接口上一个字都读不到：

```
grep -rn "bbox_only" tech_app/            # 只有 packaging_parts.py:227 的常量与 :2288 的 docstring
grep -c "size_source\|size_quality\|outline_status" tech_app/backend/services/packaging_cost.py   # → 0
```

后果（`packaging-bom-part-size-provenance.md` §1.3 已记过一次，本批补的是账）：
材料费按 `cut_length × cut_width` 算，包围盒越大越贵；到底多少行、哪些行是包围盒贡献的，
在 BOM 与报价的**任何汇总**上都看不出来 —— 客户问"这料费为什么这么高"时无从解释。

### 1.3 来源缺失的行会静默落进"和展开一样"

`size_quality_of()`（`packaging_parts.py:2285`）的口径是"缺失一律按包围盒"（`:229` 注释），
但读接口不做汇总，这个口径只在单行嵌套结构里生效 —— 一份文档里有多少行属于"没证据"，
同样没有账。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_bom.py`
   - `_item_out()`（`:881`）：在有 `dwg_binding` 的行上，**新增**把 `size_source` /
     `outline_status` / `size_quality` 提到行顶层（逐字取自该 binding；缺失给 `""`）；
     既有四个提升字段（`binding_evidence` / `binding_method` / `bound_by` / `part_role`）
     的名称、取值、`setdefault` 语义逐字不变。
   - `_stats()`（`:897`）：**新增** `size_quality` 键（键**必须存在**）：
     `{"unfolded": n, "bbox_only": n, "unknown": n}`，按行计一次：
     - 行上 `dwg_binding.size_quality` 是 `"unfolded"` / `"bbox_only"` → 计入对应档；
     - 行上没有 `dwg_binding`，或有 binding 但 **`size_quality` 为空** → 一律计 `"unknown"`
       （**不许**把"没有来源"折进 `unfolded`）；
     - 判据只认**行上留痕**，不许在这里重新按 `size_source` 字符串另算一套（口径只有一处：
       `packaging_parts.size_quality_of()`）。
     既有六个键（`total` / `by_category` / `computed` / `needs_input` / `locked` /
     `material_unresolved`）的取值逐字不变。
   - `load_bom()`（`:921`）的 `gaps`（`:966`）：**新增** `bbox_only` 键（键**必须存在**）——
     `size_quality == "bbox_only"` 的行 `item_key` 列表，按 `item_key` 升序；
     没有这样的行时给 `[]`。既有三个键（`needs_input` / `missing_variables` /
     `material_unresolved`）口径不变。
2. `tech_app/backend/main.py`
   - `GET /api/projects/{pid}/requirement/packaging-bom` 原样带出新键（不改路由形状、
     不改既有键名）。
3. 前端（`tech_app/frontend/app.js` 包装 BOM 面板）
   - 行上 `size_quality === "bbox_only"` 时显示"尺寸来自包围盒（仅供估算）"标记 ——
     文案复用已有词表 `app.js:1132 PACKAGING_SIZE_SOURCE_COPY`，不许另写一套说法；
   - 面板汇总处显示 `stats.size_quality.bbox_only` 条数；为 0 时不许显示成"未知"。

## 3. 禁止事项

- **不许**用尺寸质量去挡成本、挡报价、挡 BOM 行（`packaging-bom-part-size-provenance.md` §4 已定的
  "不改未闭合件进 BOM 这条第一版口径"继续有效）—— 本批只要求**有账**。
- 不许改 `length_mm` / `width_mm` / `status` / `missing_variables` 的任何取值；
  不许把包围盒行标成 `needs_input`。
- 不许在 `packaging_bom` 里另写一套"来源 → 质量"的映射（必须复用
  `packaging_parts.size_quality_of()` 写进留痕的那个值）。
- 不许改 `bind_rows()` 写留痕的既有键与位置（承接批 ## 329 的加法规则：新增项一律加在末尾）。
- 不许把 `bbox_only` 算进 `gaps` 的既有三个键里（新账是**加法**，不许污染 `needs_input`）。
- 不许改 `tests/` 下任何既有文件（含 `test_packaging_bom_part_size_provenance_red.py` —— 它是本批的
  回归锚点，必须继续全绿）。
- 不许连线上 PG 跑测试、不许发 HTTP、不许写业务数据；本批红测全部离线（纯函数 / 假后端）。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_size_quality_accounting_red -v
# G 组（7 条）：
#   行顶层：包围盒行的 size_source / outline_status / size_quality 必须能直接读到；
#           既有四个提升字段逐字不变（护栏）
#   汇总：_stats 必须给 size_quality 三档（unfolded / bbox_only / unknown，按行计一次）；
#         既有六个键逐字不变（护栏）
#   读接口：gaps.bbox_only 列出包围盒行（升序），无此类行时是 []（护栏）；
#           来源缺失（有 binding 无 size_source）必须计 bbox_only，不许被当成展开
# 现状：G1 G3 G5 G7 红（4 条），G2 G4 G6 绿（3 条护栏）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_part_size_provenance_red
# 不回归（本批的锚点：留痕三件套的既有口径）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_parts_version_binding_red
```

真机复验（实现方做完、且部署后）：

```
GET /api/projects/{pid}/requirement/packaging-bom
# items[].size_quality 直接可读；stats.size_quality 三档相加 == items 行数；
# gaps.bbox_only 与 stats.size_quality.bbox_only 的行数一致
```

## 5. 落地（2026-09-22，`## 342`）

- `tech_app/backend/services/packaging_bom.py:931-936 _item_out()`：行顶层提升
  `size_source` / `outline_status` / `size_quality`，口径只有一处 ——
  `:942-950 _size_quality_of()` 转调 `packaging_parts.size_quality_of()`（本模块不另算一套）；
- `:972-984 _stats()`：新增 `size_quality` 三档（`unfolded` / `bbox_only` / `unknown`，按行计一次），
  既有六个键逐字未动；
- `:1014 gaps.bbox_only`：列出包围盒行（升序），无此类行时给 `[]`；
- 红测已全绿：`tests.test_packaging_bom_size_quality_accounting_red` → `Ran 7` **OK**（G1–G7）；
- 不回归复核：`tests.test_packaging_parametric_bom_red`（57 OK）、
  `tests.test_packaging_parts_extraction_red`（32 OK）、
  `tests.test_packaging_bom_parts_version_binding_red`（7 OK）。

### 已记录的偏差（不改测试）

本批新增 `_stats().size_quality` 与 `gaps.bbox_only` 两个**必存在的键**（§2.2 / §2.3），而两份先前的
护栏测试把这两个键集**逐字冻结**了 —— 两者不可能同时成立，按仓库口径**不改测试**、只在此挂账：

1. `tests/test_packaging_bom_part_size_provenance_red.py::BBomDocumentCarriesProvenance::
   test_b3_numbers_and_totals_are_unchanged`（`:238-245`）：断言
   `set(stats) == {total, by_category, computed, needs_input, locked, material_unresolved}` 且
   `set(gaps) == {needs_input, missing_variables, material_unresolved}`。本批加账后必然多出
   `size_quality` / `bbox_only` 两个键 —— 该断言按设计变红（它编码的正是本 Spec §1.2 判定为缺口的
   那份"没有账"的读接口）。
2. `tests/test_packaging_parse_to_downstream_seams_red.py::BPairingReviewExposure::
   test_b4_stats_and_binding_are_unchanged`（`:294-305`）：同一处 `BOM_STATS_KEYS` 冻结 + `gaps`
   键集冻结，同上。

两条测试的其余断言（数字、绑定、配对复核、`length_mm` / `width_mm` 逐字不变）**仍然全绿**：
本批只加账，没有改任何既有取值。要它们转绿需要测试侧把两个键集断言改成"**包含**既有六/三个键"
（`assertLessEqual(set(FLOW), set(stats))`）—— 属测试侧动作，本层不动。

# 包装 BOM 的材料组也按权威清单遍历（`## 368` §7 切片 2，接 `## 377`）

依赖：`docs/specs/packaging-bom-business-parts-rows.md`（切片 1：部件组行）、
`docs/specs/packaging-business-parts-and-cad-plan-view.md` §7（BOM 只遍历 `business_parts`）。

状态：Spec + 红测（已实现）（红测当前全绿 —— 该切片已随并行实现批次落地，状态行随事实更新，2026-09-22 复核）  
红测：`tests/test_packaging_bom_business_material_rows_red.py`

## 1. 现场缺口（承接 `## 377` 的披露）

1. `## 377` 把 BOM 的**部件组**切到权威清单（真样本 28 件），但 `_assemble()` 第 3 组的材料行
   仍来自**模板展开的部件材料**（`expanded["parts"]` 的 `material`，`source="kb_material"`）。
   一份 BOM 于是自相矛盾：部件行是 `JWXR21-P01…P28`，材料行却是模板件那几种。
2. 权威清单里的材料是真样本事实（`225G太阳铜版底PET光银`、`1.8MM双灰裱225G太阳铜版底PET光银`、
   `350G玖龙粉灰`、`38度A级白色EVA …`），其中不少件是**合并单元格**（材料与上一行同组）：
   导入器按既有口径保留空值、只记 `merged_from`，**不复制上一行的值**——所以材料组必须按**去重原文**收，
   不能按"每件一行"。
3. 材料行是"这份 BOM 需要哪些材料、哪几种还没解析到材料码"的唯一清单
   （`load_bom().gaps.material_unresolved`），它今天列的是模板件那几种 —— 与真实待办无关。

## 2. 契约

### C1 `packaging_bom.business_material_rows(business_doc, *, materials=None) -> list[dict]`（纯函数）

- 入参：业务部件文档 + 可选的 KB 材料行（`packaging_bom._material_index()` 的产物）。
  不是 dict / `business_parts` 不是 list / 清单为空 / 一条材料原文都取不到 → `[]`。
- 逐件取 `authority.material_text` 原文：去空白后为空 → 跳过（合并单元格"同上一组"的件本来就没有原文，
  这里**不替它复制上一行的值**）；同一原文只出一行（首次出现顺序，不重排）。
- 每行：
  - `bom_category = "material"`；`item_key = item_name = material = 原文`（与既有材料行同形）；
  - `material_code`：**复用既有唯一解析口径** `_resolve_material_code(原文, materials)`；
    `materials` 没传 → `""`（纯函数不许自己去读知识库）；
  - `status = "computed"`；`is_optional = 0`；`source = BUSINESS_ROW_SOURCE`
    （与 `## 377` 的部件行同一个取值：一份 BOM 里的"权威派生行"一个口径，靠 `bom_category` 分组）。
- 纯函数：体内不出现 `kb_repo.` / `da_repo.` / `store.` / `get_backend(`。

### C2 `_assemble()` 接线

- `_assemble(..., *, business_parts=None)` 第 3 组：`business_material_rows(business_parts, materials=materials)`
  非空 → 材料组行就是这些行（模板材料行不再进入这一版）；空 / 没传 → **逐字保持今天的行为**。
- 其余五组与 `## 377` 的部件组口径都不变；`_material_index()` / `_resolve_material_code()` 一个字不改。

### C3 `load_bom()` 的披露

新增键 `business_material_rows`（**必须存在**）：

```json
{"row_total": 21, "resolved_total": 0, "unresolved_total": 21, "keys": ["…"]}
```

- 判据只有一处：`source == BUSINESS_ROW_SOURCE` 且 `bom_category == "material"`；
  `resolved_total + unresolved_total == row_total`；`keys` 升序；没有这种行时 `0 / 0 / 0 / []`。
- `## 377` 的 `business_rows`（部件组）口径**不变**（两把账分开）。

### C4 冻结面

- 不许改 `_material_index()` / `_resolve_material_code()` 的匹配口径（材料原文 → 材料码仍是同一套判据；
  本批只是把"要解析哪些原文"换成权威清单里的那些）；
- 不许给任何材料**编**材料码；解析不到就是 `""` + 进 `material_unresolved`；
- 不许改 BOM 七类、材料行的其它键、`## 377` 已定的部件组口径；
- 没有业务部件清单时，`build_bom()` / `load_bom()` 的输出逐字不变。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_bom.py`（`business_material_rows()`、`_assemble()` 第 3 组、
   `_business_material_scope()`、`load_bom()` 的新键）。
2. changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不许替合并单元格的件复制材料原文、不许按件数把材料行拆成 28 行；
- 不许连 PG / 34、不许写生产数据、不许 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_bom_business_material_rows_red -v
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_bom_business_parts_rows_red \
  tests.test_packaging_parametric_bom_red \
  tests.test_packaging_bom_part_size_provenance_red \
  tests.test_packaging_cost_engine_red \
  tests.test_packaging_parse_to_downstream_seams_red
```

## 6. 落地状态（2026-09-22，已实现）

| 契约 | 落点 |
| --- | --- |
| C1 纯函数 | `tech_app/backend/services/packaging_bom.py`：`business_material_rows(business_doc, *, materials=None)`（体内不出现 `kb_repo.` / `da_repo.` / `store.` / `get_backend(`） |
| C2 接线 | `_assemble()` 第 3 组：清单非空 → 用清单的去重原文；否则逐字回到模板展开；`_material_index()` / `_resolve_material_code()` 未动 |
| C3 披露 | `_business_material_scope(items)` + `load_bom()` 的 `business_material_rows` 键（与 `## 377` 的 `business_rows` 两把账分开） |

**Supersede 记录**：`## 377`（`packaging-bom-business-parts-rows.md`）§C2 的"其余**五**组逐字不变"
与 §7 边界 1（"材料组仍是模板来源"）已被本批显式取代（那两处都留了指针），
`tests/test_packaging_bom_business_parts_rows_red.py::B4` 的清单同步收窄到其余**四**组
（被取代的那一组由本批的 `test_b3_other_groups_and_part_rows_are_untouched` 守），
**没有改任何业务断言的期望值**。

复跑原文：

```
实现前（把 packaging_bom.py 的实现改动 stash 掉）：
  Ran 15 tests in 0.598s
  FAILED (failures=9, errors=1)        # 10 红 / 5 绿护栏

实现后：
  Ran 15 tests in 0.597s
  OK

邻近模块（business_parts_rows + parametric_bom + bom_part_size_provenance +
cost_engine + parse_to_downstream_seams）第一次复跑：
  Ran 189 tests in 4.957s, FAILED (failures=3)
  → 2 条是既有挂账（bom_part_size_provenance::B3、parse_to_downstream_seams::B4）；
    第 3 条是本批 supersede 掉的 `## 377` ::B4（它的清单里还写着"材料组不变"）
  把那条断言按 supersede 收窄到其余四组后：本批两个模块 Ran 38 OK（上面的 189 里 2 条挂账照旧）
```

真样本端到端（真链路：种子 KB + 临时 SQLite + meta 沙盘 + 真工作簿）：

```
导入前材料组 = ['EVA 植绒 5mm', '涤纶丝带 10mm', '灰板 2.0mm', '特种纸 200g']（模板材料）
导入后材料组 = 14 条权威原文（28 件按**去重原文**收，合并单元格的件没有独立原文）：
  225G太阳铜版底PET光银 / 1.8MM双灰裱225G太阳铜版底PET光银 / 350G玖龙粉灰 /
  38度A级白色EVA 125×54×35MM异形 / 长方形镀锌双面磁铁侧吸3500GS 15×5×2MM …
business_material_rows = {"row_total": 14, "resolved_total": 0, "unresolved_total": 14, "keys": […升序]}
部件组行仍是 28（`## 377` 口径未被打回）；gaps.material_unresolved = 14 条
（解析到 0 条是**诚实结果**：种子 KB 里没有这些商品牌号，映射/价格是已登记的业务数据待办）
```

## 7. 已记录的边界

1. 真样本 28 件里只有一部分行**独立**写了材料原文（其余是合并单元格"同上一组"，导入器只记
   `merged_from`、不复制值）—— 所以材料组行数会**明显少于** 28，这是事实，不是漏。
2. 材料行只报"能不能解析到材料码"，**不做**价格/计价单位（`packaging-cost-gaps-closure.md` §1.1
   已把 `material_price_missing` 登记为业务/采购要给的数据）。
3. 外购件（EVA / 磁铁）的材料原文同样进材料组 —— 它们的计价口径（按件、按 kg）是
   `packaging-cost-gaps-closure.md` 里登记的下一批话题，本批只保证原文不丢、缺口可见。
4. 全域（`tests/test_packaging_*.py`，2026-09-22 复跑）`Ran 1719, failures=20`：
   20 = 4 条既有挂账（`bom_part_size_provenance::B3`、`parse_to_downstream_seams::B4`、
   `route_bom_version_pinning::F2`、`quote_send_recovery::C1`）+ **16 条本轮新到的红测**
   （`packaging-part-role-mapping-must-reach-the-card` A1–A4、
   `packaging-drawing-source-read-failure` R1–R4/R6/R9、
   `packaging-gate-read-failure-disclosure` S1–S6 —— 那三批是并行会话刚落的 Spec + 红测，
   本批一行都没碰、也不属于本 Spec 范围）。本批自己的两个模块 38 条全绿。

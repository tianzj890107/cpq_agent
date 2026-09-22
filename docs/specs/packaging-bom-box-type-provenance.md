# 规格：BOM 行必须认自己的盒型（换盒型重算后，旧锁定行不许冒充新盒型的部件）

状态：Spec + 红测（已实现）（落地见 §5：行带盒型列 / 读接口按行报混入 / 老库幂等补列）
红测：`tests/test_packaging_bom_box_type_provenance_red.py`

血缘：承接 `packaging-parametric-bom.md`（BOM 行与盒型）、
`packaging-bom-part-size-provenance.md`（同一纪律：行必须带得出"它是怎么来的"）、
`packaging-part-role-manual-mapping.md`（候选角色来自**当前确认盒型**的部件模板）、
`packaging-bom-parts-version-binding.md`（同一条"行要认来源"的路子，本批是盒型维度）。

## 0. 一句话目标

一行 BOM 必须说得出来它属于哪个盒型。换盒型重算之后，"上一版盒型留下来的锁定行"不许
静默混在新盒型的 BOM 里冒充新盒型的部件 —— 混了就要报出来，但**不许删**（锁定是用户的意思）。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

### 1.1 行上没有盒型，整份 BOM 的盒型是"从成品行推"的

`tech_app/backend/services/packaging_bom.py:406 _assemble()` 造六组行（成品 / 部件 / 材料 /
工艺 / ……）全是字面量 dict，**没有任何一组行带盒型**；落库列清单
`tech_app/backend/storage/da_repo.py:731 _PACKAGING_BOM_COLUMNS` 也没有 `box_type_code`。

读接口只能从"成品行"反推整份 BOM 的盒型，`packaging_bom.py:926-929 load_bom()`：

```python
    box_type_code = ""
    for item in items:
        if _text(item.get("bom_category")) == "finished":
            box_type_code = _text(item.get("item_key"))
            break
```

一句"整份 BOM 的盒型"就是这么来的 —— 只要成品行属于新盒型，**其余行属于谁它并不问**。

### 1.2 换盒型重算：锁定行被"原样保留"，于是两套盒型的部件混在一份 BOM 里

`packaging_bom.py:1089` 落库走 `da_repo.save_packaging_bom()`，而
`da_repo.py:748 save_packaging_bom()` 的口径是：

```python
    db.execute(
        "DELETE FROM wip_packaging_bom_item "
        "WHERE project_id = ? AND requirement_no = ? AND locked = 0", ...)
```

只删**未锁定**行，锁定行原样保留（"锁定是用户的意思"，这条口径本批不动）。
于是：确认盒型从 A 换成 B → 重建 BOM → A 的锁定行留下来，B 的行写进来，
**同一份 (项目, 需求单) 里同时存在两个盒型的部件**。而 `load_bom()` 会把它整体报成 B 盒型：

- 报价按"B 盒型 + A 盒型的残留部件"算材料/工艺；
- 人工角色映射（`role_candidates_for()` → 当前确认盒型的部件模板，`packaging_bom.py:621`）
  给 A 的行配 B 的候选角色；
- 读接口上一个字都不说。

### 1.3 历史行（没有盒型）同样无从判断

本批之前落库的所有行都属于这一类。没有盒型字段，读接口既不能说它属于哪个盒型、
也不能说"无从判断" —— 与 §1.2 是同一个洞的另一格。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/storage/da_schema.sql` + `tech_app/backend/storage/da_db.py:30 _ADDED_COLUMNS`
   - `wip_packaging_bom_item` **新增列** `box_type_code TEXT`：新库由 schema 给，
     老库靠既有幂等补列 `_add_missing_columns()`（`da_db.py:168`）补 —— 只加列，
     **不许**改类型、不许删列、不许重建表、不许回填/改写到已有行上。
2. `tech_app/backend/storage/da_repo.py`
   - `_PACKAGING_BOM_COLUMNS`（`:731`）加入 `box_type_code`（不加就会被
     `{key: item.get(key) for key in _PACKAGING_BOM_COLUMNS}` 丢掉）。
3. `tech_app/backend/services/packaging_bom.py`
   - `_assemble()`（`:406`）在**返回前统一盖章**：所有行 `box_type_code = expanded["box_type_code"]`
     （一处 for 循环，不许在六组字面量里各写一遍 —— 漏一组就是今天这个洞）；
   - `load_bom()`（`:921`）：
     - `source_versions` **新增** `box_type_codes`：这份 BOM 里出现过的盒型码**去重升序**；
     - **新增** `rows_from_other_box_type`（键**必须存在**，`item_key` 升序）：
       行上 `box_type_code` 非空且 ≠ 当前确认盒型（`source_versions.box_type_code`）的行；
     - **新增** `rows_without_box_type`（键**必须存在**，`item_key` 升序）：
       行上 `box_type_code` 为空（本批之前落的历史行）——"无从判断"不许当成"同盒型"；
     - 既有键（`built` / `box_type_code` / `items` / `gaps` / `stats` / `source_versions` 既有四项）
       的名称与取值逐字不变。
   - 锁定行**一律不许删**：混盒型的处置是"报出来 + 让人决定"，不是自动清理
     （`packaging_bom.py:1095 lock_bom_item()` 的既有语义一个字不改）。
4. `tech_app/backend/main.py`：BOM 读路由原样带出两个新清单与 `source_versions.box_type_codes`。
5. 前端（BOM 面板）：`rows_from_other_box_type` 非空时按行标"这一行属于另一个盒型（%s），
   请解锁或重新确认盒型后再算"；`rows_without_box_type` 非空时标"这一行的盒型未知（旧数据）"。
   BOM 头部显示"这份 BOM 混了 N 个盒型"。

## 3. 禁止事项

- **不许删、不许清空、不许改写锁定行**（含"换个盒型就把异盒型行删掉"这种自动清理）——
  锁定行是用户明确保下来的东西；本批只要求**报出来**。
- 不许在 `load_bom()` 里顺手"修复"（例如按当前盒型过滤掉异盒型行、或把它们标成 `needs_input`）——
  读接口只报事实，处置是人的动作。
- 不许用"成品行的盒型"继续代表整份 BOM 的盒型（这正是本批要消灭的静默冒充）；
  `box_type_code` 这个既有键的取值口径保持不变（它仍是当前确认盒型的门面），
  但**必须**同时给出 `box_type_codes` 与两个清单。
- 不许把"没有盒型的历史行"折进 `rows_from_other_box_type`（两者的处置话术不同：
  一个要解锁/重确认，一个是旧数据）。
- 不许改 `_assemble()` 的成型口径、行顺序、`item_key` 生成规则与既有行的任何取值。
- 不许改 `tests/` 下任何既有文件（含 `test_packaging_parametric_bom_red.py` 与本批相邻的
  `test_packaging_bom_parts_version_binding_red.py`）。
- 不许连线上 PG / SQLite 生产库跑测试、不许发 HTTP、不许写业务数据；本批红测全部离线。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_box_type_provenance_red -v
# I 组（6 条）：
#   I1 成品行是当前盒型、另有三行属于旧盒型 → rows_from_other_box_type 列出那三行（升序）、
#      source_versions.box_type_codes == ["YT-NEW", "YT-OLD"]
#   I2 全部行同盒型 → rows_from_other_box_type 必须是 []（护栏）
#   I3 行上没有盒型（历史行）→ 进 rows_without_box_type，不许被当成同盒型
#   I4 既有键逐字不变：built / box_type_code（仍取成品行）/ source_versions 既有四项（护栏）
#   I5 落库列清单 _PACKAGING_BOM_COLUMNS 必须含 box_type_code（否则行上的盒型写不进去）
#   I6 补列清单 da_db._ADDED_COLUMNS 必须含 ("wip_packaging_bom_item", "box_type_code")
# 现状：I1 I3 I5 I6 红（4 条），I2 I4 绿（2 条护栏）
# 不回归（BOM 既有口径）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_parts_version_binding_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_size_quality_accounting_red
```

真机复验（实现方做完、且部署后）：

```
POST /api/projects/{pid}/requirement/packaging-bom/lock          # 先锁一行
（把确认盒型换成另一个）→ POST …/packaging-bom                   # 重建
GET  /api/projects/{pid}/requirement/packaging-bom               # rows_from_other_box_type 必须列出那行
```

## 5. 落地（2026-09-22，`## 342`）

- 落库：`tech_app/backend/storage/da_schema.sql`（`wip_packaging_bom_item.box_type_code`）、
  `da_repo.py:731-738 _PACKAGING_BOM_COLUMNS` 追加 `box_type_code`、
  `da_db.py:52 _ADDED_COLUMNS` 给老库幂等补列（只加列）；
- 造行：`tech_app/backend/services/packaging_bom.py:510` 每行写 `box_type_code`；
- 读回：`:938 _item_out()` 提升 `box_type_code`；`:1016 box_type_codes` 由**行**汇总
  （不再拿"成品行"的 `item_key` 当整份 BOM 的盒型）；
- 红测已全绿：`tests.test_packaging_bom_box_type_provenance_red` → `Ran 6` **OK**（I1–I6）；
- 不回归复核：`tests.test_packaging_parametric_bom_red`（57 OK）、
  `tests.test_packaging_parts_extraction_red`（32 OK）、
  `tests.test_packaging_bom_parts_version_binding_red`（7 OK）。

# 规格：3D 结论（挤出体 / STL）必须认零件文档版本

状态：Spec + 红测（已实现）（两个写入口落 `parts_id`/`parts_hash`，索引与 STL 下载都按 `solids_stale_reason()` 比对并披露；落地见 §5）
红测：`tests/test_packaging_solids_parts_version_binding_red.py`

血缘：承接 `packaging-parts-solid-coverage.md`（3D 覆盖率与三态）、
`packaging-bom-parts-version-binding.md`（同一条"下游结论必须认零件文档版本"的纪律）、
`packaging-cost-input-version-pinning.md`（同一套"算时记下 / 读时比对"的路子）。
对照：同一批下游结论里，**单件工艺**（`main.py:8012`）与**单件成本**（`main.py:8145`）都已经带了 `parts_id`。

## 0. 一句话目标

一份 3D 结论必须说得出来它是照**哪一版零件文档**算的；零件重解析之后，
列表行、覆盖率、STL 下载三处都要能看出"这是上一版零件算的"——但**不许删**旧结论与旧文件。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

### 1.1 两个写入口都不带零件文档版本

- `tech_app/backend/main.py:8261`（单件 `POST …/packaging-parts/{part_code}/solid`）：
  `save_solids(pid, {"engine_version": …, "parts": parts})` —— 没有 `parts_id`；
- `tech_app/backend/main.py:8310-8314`（整份 `POST …/packaging-parts/solids`）：
  落库体是 `{"engine_version", "stats", "parts"}` —— 也没有。

而 `main.py:6943` 那一段的 docstring 自己写着"零件文档本身**不改**（改了会换 `parts_id`、
把下游落库的结论全指歪）"：口径知道，落库没做。

### 1.2 列表索引不比对：重解析后照旧显示"这一件有 3D"

`main.py:6940 _packaging_solids_index()` 只贴两样：

```python
        if code:
            index[code] = {"solid_status": str(item.get("status") or ""),
                           "solid_reason": str(item.get("reason") or "")}
```

`_packaging_parts_body()`（`:7033`）把它按 `part_code` 贴到列表每一行上。零件重解析换了
`parts_id` 之后，这份索引照旧把**上一版零件**算出来的 `solid_status` 贴上去 ——
2.1 上"这一件有 3D / 覆盖率"看起来完全是当前零件的结论。

### 1.3 STL 下载不带版本

`main.py:8265 get_packaging_part_solid_stl()`：只要文档里那一件 `status == "ok"` 且有 `stl`
就 200 返回（`:8270-8279`），响应头只有 `content-disposition`。用户下的可能是上一版零件算出的
挤出体，而下载这个动作本身没有任何地方能看出这件事。

### 1.4 整份入口是"合并写"，不区分版本

`main.py:8305-8314`：

```python
    merged = {str(item.get("part_code") or ""): item
              for item in (record.get("parts") or []) if isinstance(item, dict)}
    for item in result.get("parts") or []:
        merged[str(item.get("part_code") or "")] = item
```

按 `part_code` 合并，不看这些件属于哪一版零件文档：重解析后件号重排/件数变化时，
上一版的件留在文档里、与新一版的件混在一起（`parts` 数组里两种来源无法分辨）。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_part_solids.py`
   - 新增模块级纯函数 `solids_stale_reason(record, current_parts_id) -> str`（**唯一判据点**）：
     - 记录里没有 `parts_id`（本批之前落的结论）→ `"parts_unknown"`；
     - `current_parts_id` 为空（当前零件文档读不到 / 没解析过）→ `"parts_unknown"`；
     - 两者都有且不同 → `"parts_reparsed"`；
     - 相同 → `""`。
   - `save_solids()` / `load_solids()` 的**整份文档原样存取**语义、版本号只增不改、`MAX_VERSIONS`
     **一个字都不改**（本批只让调用方把版本写进去）。
2. `tech_app/backend/main.py`
   - 单件入口（`:8248`）与整份入口（`:8288`）落库体新增 `parts_id` / `parts_hash`
     （当前零件文档的，取不到给 `""`）；每件结论体也带 `parts_id`；
   - 整份入口的合并写：来自**别的** `parts_id` 的件**保留**（不许删），但在落库体上另记
     `rows_from_other_parts_id`（`part_code` 升序；没有时给 `[]`）；
   - `_packaging_solids_index()`（`:6940`）：每个索引项新增 `parts_id` / `stale` / `stale_reason`
     （`stale = bool(stale_reason)`，判据一律调 `solids_stale_reason()`）；
     既有 `solid_status` / `solid_reason` 的名称与取值逐字不变；
   - `_packaging_parts_body()`（`:7019`）：响应体新增 `solids_parts_id` / `solids_stale` /
     `solids_stale_reason` / `solids_rows_from_other_parts_id`（键**必须存在**；
     没有 3D 结论时给 `""` / `false` / `""` / `[]`），既有键与响应形状逐字不变；
   - STL 下载（`:8265`）：**仍 200**（文件还在，用户要能对比），响应头新增
     `X-Packaging-Parts-Id`（结论那一版）与 `X-Packaging-Parts-Stale`（`0` / `1`），
     `stale` 为真时另加 `X-Packaging-Parts-Stale-Reason`。
3. 前端：列表行 / 右栏在 `stale` 为真时显示"这一件的 3D 是上一版零件算的（%s），请重新生成"，
   **不许**继续按"有 3D"的样式展示；`solids_stale_reason == "parts_unknown"` 时显示
   "无法判断这份 3D 对应哪一版零件"。

## 3. 禁止事项

- **不许删旧 STL / 旧 3D 结论**（含"重解析后清一遍 3D 文档"这种自动清理）——本批只要求披露。
- 不许把"过期"变成 404 / 错误码：下载照旧 200，结论照旧读得出来。
- 不许在读接口里自动重算 3D（重算是两个 POST 入口的动作）。
- 不许把"读不到当前零件文档"当成"过期"或"没过期"——一律 `parts_unknown`
  （与 `packaging-bom-parts-version-binding.md` / `packaging-cost-input-version-pinning.md` 同一纪律）。
- 不许改 `save_solids()` / `load_solids()` 的版本语义与 `MAX_VERSIONS`，不许改既有键
  （`solid_status` / `solid_reason` / `stats` / `solids_version`）与既有响应形状
  （`parts` / `stats` / `solids_version`）。
- 不许改 `tests/` 下任何既有文件（含 `test_packaging_parts_extraction_red.py` 与本批相邻的
  `test_packaging_bom_parts_version_binding_red.py`）。
- 不许连线上 PG / SQLite 生产库跑测试、不许发 HTTP、不许写业务数据；本批红测全部离线。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_solids_parts_version_binding_red -v
# J 组（6 条）：
#   J1 整份入口落库体必须带当前零件文档的 parts_id / parts_hash（每件结论也带 parts_id）
#   J2 索引项：结论 parts_id ≠ 当前零件文档 parts_id → stale=true、stale_reason=parts_reparsed
#   J3 索引项：当前零件文档读不到 → stale_reason=parts_unknown，且 stale 不许为 true
#   J4 STL 下载（仍 200）必须带 X-Packaging-Parts-Id / X-Packaging-Parts-Stale
#   J5 存储层护栏：save_solids / load_solids 原样保留 parts_id，版本号只增不改（现状即绿）
#   J6 既有键护栏：索引项的 solid_status / solid_reason 逐字不变（现状即绿）
# 现状：J1 J2 J3 J4 红（4 条），J5 J6 绿（2 条护栏）
# 不回归（零件与相邻批次）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_solid_coverage_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_bom_parts_version_binding_red
```

真机复验（实现方做完、且部署后）：

```
POST /api/projects/{pid}/requirement/packaging-parts/solids      # 生成一版 3D
（重跑一键解析图纸，换一版零件文档）
GET  /api/projects/{pid}/requirement/packaging-parts            # 每行 stale=true
GET  /api/projects/{pid}/requirement/packaging-parts/{code}/solid.stl   # 200 且带版本头
```

## 5 落地状态（2026-09-22，实现方 Codex）

实跑：`./open-claude/.venv/bin/python -m unittest tests.test_packaging_solids_parts_version_binding_red`
→ **Ran 6 tests … OK**（J1–J4 四条红转绿，J5/J6 两条护栏仍绿）。

- `packaging_part_solids.solids_stale_reason(record, current_parts_id)`（新纯函数，**唯一判据点**）：
  没 `parts_id` / 当前零件文档读不到 → `parts_unknown`；两者都有且不同 → `parts_reparsed`；相同 → `""`。
  原因码闭集 `STALE_REASONS`。`save_solids()` / `load_solids()` 的整份文档原样存取、版本只增不改、
  `MAX_VERSIONS` **一个字未改**。
- `main.py` 两个写入口：单件（`POST …/{part_code}/solid`）与整份（`POST …/solids`）的落库体都新增
  `parts_id` / `parts_hash`（取当前零件文档，读不到给 `""`），**每件结论也带 `parts_id`**；
  整份入口的合并写按件号保留旧件、来自**别的** `parts_id` 的件另记 `rows_from_other_parts_id`
  （`part_code` 升序）；**旧结论一个字都不删**。
- `_packaging_solids_scope()` + `_packaging_solids_index()`：索引项新增 `parts_id` / `stale` /
  `stale_reason`（判据一律调存储层那个纯函数），既有 `solid_status` / `solid_reason` 逐字不变。
- `_packaging_parts_body()`：响应体新增 `solids_parts_id` / `solids_stale` / `solids_stale_reason` /
  `solids_rows_from_other_parts_id`（键**必须存在**，没有 3D 结论时给 `""` / `false` / `""` / `[]`）；
  既有键与响应形状逐字不变。
- STL 下载**仍 200**（文件还在，用户要能对比），响应头新增 `X-Packaging-Parts-Id` 与
  `X-Packaging-Parts-Stale`，过期时另加 `X-Packaging-Parts-Stale-Reason`。
- 前端 `app.js`：覆盖率行后缀按 `solids_stale_reason` 说话（`parts_unknown` 说"无法判断这份 3D
  对应哪一版零件"，换版说"是上一版零件算的"），逐件行在 `stale` 为真时带 `data-solid-stale` 与
  这句话 —— 不再按"有 3D"的样式展示。`node --check` 通过。

### 已记录的偏差（不改测试）

Spec §2 的措辞写「`stale = bool(stale_reason)`」，而本批红测 **J3** 明确要求
`stale_reason == "parts_unknown"` 时 `stale` **不许**为 true（"比较不了 ≠ 过期"，与 Spec §3
禁止事项第 4 条同一意图）。两者在同一份 Spec 内自相矛盾，按**红测**实现：
新增 `_solids_stale_flag(reason)`，只有 `parts_reparsed` 才算"过期"，`parts_unknown` 一律 `false`
但 `stale_reason` 照旧带出来（页面据此说"无法判断"）。未改任何测试、未放宽任何断言。

未连 34、未写生产数据、未 push / MR / tag / Release / 未部署。

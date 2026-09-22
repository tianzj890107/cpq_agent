# 规格：单件工艺/成本结论的读侧必须认零件文档版本

状态：Spec + 红测（已实现）（K1–K6 全绿：两个 GET 回显 `parts_id` / `stale` / `stale_reason`、`load_part_process` / `load_part_cost` 支持按 `parts_id` 精确读回那一版、右栏把"这份结论是上一版零件算的"说出来）
红测：`tests/test_packaging_parts_conclusion_version_readback_red.py`

血缘：承接 `packaging-parts-downstream-readback.md`（它只要求结论**存** `parts_id` 与幂等，没要求读的时候认它）、
`packaging-solids-parts-version-binding.md`（3D 那一格，同一形状）、
`packaging-parts-selectable-panel.md`（右栏零件面板）。

## 0. 一句话目标

右栏读回一份工艺/成本结论时，必须说得出来它是照**哪一版零件文档**算的；
零件重解析（换 `parts_id`）之后，这两份结论不许再以"当前有效"的样子显示 —— 但也**不许删**。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

### 1.1 写侧记了版本，读侧既不回显也不比对

- 写侧：工艺结论文档带 `parts_id`（`tech_app/backend/main.py:8123`，成本那一路同口径）；
- 读侧：`main.py:8273 get_packaging_part_process()` 与 `main.py:8294 get_packaging_part_cost()`
  的返回体只有 `part_code` / `plan`(或 `analysis`) / `validation` / `coverage` / `source` ——
  **没有 `parts_id`、没有任何"这份结论针对哪一版零件"的信息**，也不读当前零件文档。

于是：重跑一次图纸解析（换 `parts_id`）之后，右栏照旧把**上一版零件**算出的工艺/成本结论显示成
当前结果，一个字都不说。

### 1.2 想按版本读也读不了

`packaging_parts.load_part_process()`（`:2329`）与 `load_part_cost()`（`:2339`）都转发到
`_load_part_doc(project_id, key, part_code)`，只按 `part_code` 取**最近一版**：

```python
    for item in _part_doc_items(project_id, key):
        if _text(item.get("part_code")) == wanted:
            return item
```

而文档本身是按 `(part_code, parts_id)` 分段存的（`_save_part_doc()` `:2298-2318`：
同一 `(part_code, parts_id, 结论内容)` 幂等、换版本各留一条）—— **存得下，但读不出**。

### 1.3 现状的三种情形在返回体上长得一样

"没跑过"（`{}` → 空态）、"跑过且是当前版"、"跑过但是上一版零件算的"，
在 `get_packaging_part_process()` / `get_packaging_part_cost()` 的返回体上完全一样：
`plan` / `analysis` 有值就是"有结论"，没有任何一栏能分出后两种。

## 2. 允许修改范围（实现方）

1. `tech_app/backend/services/packaging_parts.py`
   - 新增模块级纯函数 `parts_stale_reason(stored_parts_id, current_parts_id) -> str`
     （**唯一判据**，与 `packaging_part_solids.solids_stale_reason()` 同一套三值）：
     任一侧为空 → `"parts_unknown"`；两侧都有且不同 → `"parts_reparsed"`；相同 → `""`；
   - `load_part_process(project_id, part_code, parts_id=None)` 与
     `load_part_cost(project_id, part_code, parts_id=None)` **新增可选** `parts_id` 参数：
     传了就按 `(part_code, parts_id)` 精确匹配**那一版**；不传时逐字保持今天的行为
     （同一 `part_code` 的最近一版）。`_load_part_doc()` 可以新增同形的可选参数，
     但既有调用（`part_code` only）的返回值必须逐字不变。
2. `tech_app/backend/main.py`
   - `get_packaging_part_process()`（`:8273`）与 `get_packaging_part_cost()`（`:8294`）
     返回体**新增**三个键（键**必须存在**，空态也要有）：
     - `parts_id`：这份结论那一版（没跑过 → `""`）；
     - `stale`：`bool(stale_reason)`；
     - `stale_reason`：取 `parts_stale_reason(结论的 parts_id, 当前零件文档 parts_id)`
       （当前零件文档读不到 → `parts_unknown`，**`stale` 不许为真**）；
   - 既有键（`part_code` / `plan` / `validation` / `coverage` / `source`、
     `analysis` / `summary`）的名称、取值与空态形状逐字不变。
3. 前端（右栏零件面板）
   - `stale` 为真时显示"这份结论是上一版零件算的（%s），请重新跑一次"；
   - `stale_reason == "parts_unknown"` 时显示"无法判断这份结论对应哪一版零件"。

## 3. 禁止事项

- 不许在读接口里自动重算工艺/成本（重算是两个 POST 入口的动作）。
- 不许删/清空过期结论：`_save_part_doc()` 的 `(part_code, parts_id)` 分段、幂等判据
  与 `MAX_VERSIONS` 一个字都不许改。
- 不许把"读不到当前零件文档"当成过期或没过期 —— 一律 `parts_unknown`。
- 不许改既有键与空态形状，也不许把新增键做成"有结论才出现"（空态必须同样给三个键）。
- 不许改 `tests/` 下任何既有文件（含 `test_packaging_parts_downstream_readback_red.py` ——
  它是本批的回归锚点）。
- 不许连线上 PG / SQLite 生产库跑测试、不许发 HTTP、不许写业务数据；本批红测全部离线。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_conclusion_version_readback_red -v
# K 组（6 条）：
#   K1 结论是旧版零件算的、当前零件文档已换版 → 两个 GET 都必须给
#      parts_id=旧版 / stale=true / stale_reason=parts_reparsed
#   K2 当前零件文档读不到 → stale_reason=parts_unknown，且 stale 不许为 true
#   K3 没跑过（空态）也必须给三个键（parts_id="" / stale=false / stale_reason=""）
#   K4 结论与当前零件文档同版 → stale 不为真、既有键逐字不变（护栏）
#   K5 load_part_process / load_part_cost 必须支持按 parts_id 精确读回那一版
#   K6 不传 parts_id 时读回行为逐字不变（最近一版）（护栏）
# 现状：K1 K2 K3 K5 红（4 条），K4 K6 绿（2 条护栏）
# 不回归（本批锚点与相邻批次）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_downstream_readback_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_solids_parts_version_binding_red
```

真机复验（实现方做完、且部署后）：

```
POST /api/projects/{pid}/requirement/packaging-parts/{code}/process   # 跑一版结论
（重跑一键解析图纸，换一版零件文档）
GET  /api/projects/{pid}/requirement/packaging-parts/{code}/process   # stale=true + parts_reparsed
```

## 5. 更正（§2.2 的 `stale` 口径，以本条为准）

§2.2 第 2 条写「`stale`：`bool(stale_reason)`」，与 §3「不许把"读不到当前零件文档"当成过期或没过期
—— 一律 `parts_unknown`」以及红测 **K2**（`parts_unknown` 时 `stale` 不许为 true）自相矛盾。
按 §3 / K2 收口 —— 与 `packaging-solids-parts-version-binding.md` §6 同一条纪律（"比较不了 ≠ 过期"）：

- `parts_reparsed` → `stale = true`；
- `parts_unknown`（结论没版本 / 当前零件文档读不到）→ `stale = false`，但 `stale_reason` 照旧带出来；
- 同版 → `stale = false` 且 `stale_reason = ""`；
- 空态（没跑过）→ 三键必须存在：`parts_id=""` / `stale=false` / `stale_reason=""`。

§2.2 原文保留为历史事实，以本条为准；红测一个字未改。

## 6. 落地状态（2026-09-22）

`tests.test_packaging_parts_conclusion_version_readback_red` **Ran 6 OK**。

| 契约 | 落点 |
| --- | --- |
| §2.1 唯一判据 | `packaging_parts.parts_stale_reason(stored, current)`（三值，与 `packaging_part_solids.solids_stale_reason()` 同一套） |
| §2.1 按版本读回 | `_load_part_doc(project_id, key, part_code, parts_id=None)`；`load_part_process()` / `load_part_cost()` 新增**可选** `parts_id`（不传时逐字保持"最近一版"） |
| §2.2 读侧回显 | `main.py` 新增 `_packaging_part_conclusion_version(pid, record)` + 常量 `PACKAGING_PART_STALE_REASON`；两个 GET 路由的返回体都 `update()` 这三个键（空态也走同一函数） |
| §2.3 右栏披露 | `tech_app/frontend/inline-analysis.js` 的 `conclusionVersionNote(data)`：`stale` → 「这份结论是上一版零件算的（%s），请重新跑一次。」；`parts_unknown` → 「无法判断这份结论对应哪一版零件。」；`load()` 里优先把它显示在状态行 |

复跑（本机）：

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_conclusion_version_readback_red   # Ran 6 OK
./open-claude/.venv/bin/python -m unittest $(ls tests/test_packaging_parts_*.py | grep -v role_lookup | sed 's#/#.#g; s#\.py$##' | tr '\n' ' ') tests.test_packaging_solids_parts_version_binding_red
# Ran 309 OK (skipped=4)
node --check tech_app/frontend/inline-analysis.js   # 通过
```

未改 `tests/` 下任何文件（含回归锚点 `test_packaging_parts_downstream_readback_red.py`）、未连 PG / SQLite 生产库、
未发 HTTP、未写业务数据；`_save_part_doc()` 的分段 / 幂等 / `MAX_VERSIONS` 一个字未改。

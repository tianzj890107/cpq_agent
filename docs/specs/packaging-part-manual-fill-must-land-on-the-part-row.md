# 人工补录必须落回**零件行**：补材料/补料厚写进了没人读的侧档，等于没补（第 10 层）

血缘：`packaging-parts-in-card-and-material-fill.md` §2.2/§2.3（件级「补材料」入口，`## 315` 已落地）、
`packaging-parts-thickness-facts.md` §2.5（件级「补料厚」，`## 304` 落地）、
`packaging-parts-coverage-truthfulness.md` §2.2（缺口原因账）。

状态：Spec + 红测（已实现）（本层只修**读回口径**：`load_parts()` 读时合并两份侧档，不改判据、
不新增业务概念；红基见 §6、落地与 1 条测试侧偏差见 §7）
红测：`tests/test_packaging_part_manual_fill_persists_red.py`
本批 changelog 条目号：`## 326`。

## 0. 一句话目标

用户在 34 上点了「补材料」，页面当场显示材料补上了、按钮也消失了；但只要**刷新一次**
（或在报价卡片第 6 步上看）材料又没了，再点这件下游「工艺推荐」**照样 409**。

本层说清：补录是**真的写进库了**，但写进的是一份**没有任何读路径会读的侧档**；
零件行（下游判据、摘要账、卡片表格唯一的数据源）从来没被更新过。所以这不是"没生效"，
是"生效在没人看的地方"。

## 1. 根因（代码事实，逐条可复现；不是推断）

1. **补录确实写了库**：两条写路由各自落一版侧档 ——
   `main.py:7824` `set_requirement_packaging_part_thickness` → `save_part_thickness()`
   → `DOC_KEY_THICKNESS = "packaging_part_thickness"`（`packaging_parts.py:39`）；
   `main.py:7883` `set_requirement_packaging_part_material` → `save_part_material()`
   → `DOC_KEY_MATERIAL = "packaging_part_material"`（`packaging_parts.py:43`）。
   两条路由的 docstring 都写着"**写零件行的副本（不换 parts_id）**"。
2. **但那份"零件行的副本"没有被落回零件文档**：路由拿到的是
   `set_manual_material(row, …)` / `set_manual_thickness(row, …)` 返回的 **deepcopy 副本**
   （`packaging_parts.py:2177` / `2129`，docstring 明写"返回副本，绝不原地改入参"），
   它只出现在 HTTP 响应体里；两条路由都**没有**随后调用 `save_parts()` 或任何写回
   `packaging_parts` 文档的动作（`DOC_KEY = "packaging_parts"`，`packaging_parts.py:29`）。
3. **两份侧档是"只写不读"**：全仓 `packaging_part_thickness` / `packaging_part_material`
   这两个字面量只出现在 `packaging_parts.py` 的常量定义处；
   `load_part_material()` / `load_part_thickness()` 的调用点**只有它们自己的 GET 路由**
   （`main.py:7815` / `7874`）。没有任何 overlay / merge 把它们合回零件行。
4. **所有下游都从零件行取数**：
   - `_packaging_part_row()`（`main.py:7944`）`packaging_parts.load_parts(pid)` →
     `record["parts"]` 里那一行（`load_parts()` `packaging_parts.py:2047`）；
   - 单件工艺路由 `packaging_part_process()`（`main.py:7969`）拿到行后直接
     `packaging_parts.processability(row)`，`ok=False` → `_packaging_part_reject()` **409**；
   - `summarize()`（`packaging_parts.py:1691`）的 `material_known_total` /
     `material_manual_total` / `unprocessable_reason_mix` 全部**由行现算**
     （`material_manual_total` 判据见 `packaging_parts.py:1776`：行上 `material_source.kind`）；
   - 卡片表格 `card_row()`（`packaging_parts.py:2228`）的 `可算` / `不可算原因` 也取自
     `processability()`。
5. **前端把症状盖住了**：`app.js:1496-1499` 拿 POST 的**回显**打内存补丁
   （`patchPackagingPartRows(partCode, {material: payload.material})` + `renderTree(...)`），
   于是本标签页里看着"补好了"；一刷新、或换到报价卡片第 6 步（读服务端快照/接口），
   材料又是空的 —— 34 上"刷新就没了"的老形状又来了一次。
6. **既有测试没盖这一层**：`tests/test_packaging_parts_in_card_and_material_fill_red.py` 的
   B2 只验侧档自己的读写（`load_part_material()` 读得回 `灰板`），
   B4 只验 `processability(setter(row, …))` 这个**纯函数副本**变得可算 ——
   两处都没验"落库之后，`load_parts()` 读到的那一行可算"。料厚那套
   （`tests/test_packaging_parts_thickness_facts_red.py` D2/D3）同样只验函数与路由存在。

## 2. 口径（逐条，可直接验收）

1. **补录必须落回零件行**：`POST …/packaging-parts/{part_code}/material`（以及 `…/thickness`）
   成功之后，`packaging_parts.load_parts(project_id)` 读回来的那一行必须**已经带上**人工值
   （材料行的 `material` + `material_source.kind = "manual"`；料厚行的 `thickness_mm` +
   `thickness_source.kind = "manual"`）。
   实现任选其一，但必须**唯一且确定**，不许两套并存：
   （a）写入时把该行写回 `packaging_parts` 文档；或
   （b）所有读路径（`load_parts()` / `_packaging_part_row()`）统一 overlay 两份侧档。
2. **补完这一件就能往下走**：补录之后 `processability(那一行)["ok"]` 必须为 true
   —— 同一件的「工艺推荐」/「成本测算」不再 409 `PACKAGING_PART_MATERIAL_UNKNOWN`；
   **其它件一律不受影响**（不许连坐）。
3. **账要跟着变**：`summarize()` 的 `material_manual_total` / `thickness_manual_total` 必须
   数得到这一件；`material_known_total` / `thickness_known_total` 相应 +1；
   `unprocessable_reason_mix` 里对应原因的件数相应 −1。
4. **幂等与版本口径不变**：同一 `(件, 值, 人, 理由)` 重复补录 = 无变化、不新增版本；
   **不许换 `parts_id`**（补录是改行，不是重算零件）；`record_hash` 幂等口径逐字不变。
5. **侧档不许被删掉当"修复"**：两份侧档仍要写得进、读得回（留痕与版本历史在它身上），
   本层只解决"行上没有"的问题。
6. **前端不许再靠回显掩盖**：补录成功后，页面上的那一行必须来自**服务端重读**（或后端
   写回后的同一份行），不能只有本标签页的内存补丁 —— 刷新之后现象必须一致。
7. **判据与列定义不变**：`processability()` 的判据、`CARD_COLUMNS` 的 10 列、
   `card_row()` 取数的唯一性逐字保持；写权限仍 `BOX_MATCH_DECIDE_ROLES`，审计动作名不变。

## 3. 允许修改范围

- `tech_app/backend/services/packaging_parts.py`：给人工补录加"落回行"的落库函数，
  或给 `load_parts()` 加统一 overlay（二选一）。
- `tech_app/backend/main.py`：两条补录路由在 `save_part_*` 之后调用落库/失效逻辑；
  `_packaging_part_row()` 若走 overlay 方案，在此处取合并后的行。
- `tech_app/frontend/app.js`：补录成功后改为按服务端重读（或使用响应里**已落库**的行）。
- 禁止：改 `processability()` 判据、改摘要键名与口径、改 `CARD_COLUMNS`、
  动 `packaging_parts` 文档的 `parts_id` 身份算法、删侧档、放宽写权限。

## 4. 红测分组（`tests/test_packaging_part_manual_fill_persists_red.py`）

- **A 组 落回行（今天都是红的）**
  - A1（红）**材料**：`save_parts()` + `save_part_material()` 之后，`load_parts()` 的那一行
    `material` 必须等于补的值、`material_source.kind == "manual"`，且 `processability(row)["ok"]`
    为 true（今天：材料仍为空、`ok=False`、code `PACKAGING_PART_MATERIAL_UNKNOWN`）。
  - A2（红）**账**：同一回合后 `summarize()` 的 `material_manual_total == 1`、
    `material_known_total` +1、`unprocessable_reason_mix["PACKAGING_PART_MATERIAL_UNKNOWN"]` −1
    （今天：`material_manual_total == 0`，其余两个数不变）。
  - A3（红）**料厚**（同一缺陷的镜像）：`save_part_thickness()` 之后那一行 `thickness_mm`
    必须等于补的值、`thickness_source.kind == "manual"`、`processability(row)["ok"]` 为 true、
    `thickness_manual_total == 1`（今天：全都没变）。
- **B 组 护栏（今天就是绿的，不许被改红）**
  - B1 侧档本身仍写得进、读得回、幂等（`load_part_material()` / `load_part_thickness()`）；
  - B2 `set_manual_material()` / `set_manual_thickness()` 仍是纯函数（不改入参）+ 空值/非正数仍 `ValueError`；
  - B3 `processability()` 的判据文案与 `CARD_COLUMNS` 10 列不变，且 `card_row()` 的可算性仍**只**取自 `processability()`；
  - B4 两条写路由的写权限仍是 `BOX_MATCH_DECIDE_ROLES`、审计动作名仍是
    `workflow:packaging_part_material_bound` / `workflow:packaging_part_thickness_bound`、
    响应里仍回 `part` 且 `parts_id` 不变。

## 5. 验收

1. 34 上对 `a42e5e60a720` 的 `DWG-P05`（已闭合、缺材料）点「补材料」→ 填 `灰板`；
2. **刷新页面**后那一行材料仍是 `灰板`，卡片第 6 步同一行的 `可算` 变成「可算」；
3. 再点这一件的「工艺推荐」→ `200 succeeded`（不再是 409 `PACKAGING_PART_MATERIAL_UNKNOWN`）；
4. 同一件的「成本测算」也能跑，且 `summary.material_manual_total` 变成 1；
5. 其它 50 件仍照旧 409 —— 补录只影响这一件。

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_part_manual_fill_persists_red \
  tests.test_packaging_parts_in_card_and_material_fill_red -v
```

## 6. 红基（2026-09-22 实跑，未实现）

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_part_manual_fill_persists_red
  → Ran 7 tests … FAILED (failures=3, errors=0)
```

红的 3 条 = A1（补材料后零件行仍没有材料、仍不可算）、A2（`material_manual_total` 仍 0，
摘要账不动）、A3（补料厚同形状：行上没有、账不动）；
绿的 4 条护栏 = B1（侧档读写与幂等正常）、B2（纯函数与入参校验不变）、
B3（判据与 10 列不变、`card_row()` 仍只认 `processability()`）、B4（写权限 / 审计名 / 响应形状不变）。

## 7. 落地状态（2026-09-22 实现轮完成，本地提交）

- 采用 §2.1 的**方案 b（读时统一 overlay）**：`packaging_parts.load_parts()` 读回零件文档时，
  用 `_manual_fill_overlay()` 把两份侧档（`packaging_part_material` / `packaging_part_thickness`）
  按 `part_code` 的**最近一版**合回行上 —— 合出来的行直接复用既有两个纯函数
  `set_manual_material()` / `set_manual_thickness()`，没有第二份写字段的逻辑；
  零件文档的 `parts_id` / `parts_hash` **一个字未改**（补录是改行，不是重算零件）。
- 侧档本身一个字未改：仍写得进、读得回、同一 `(件, 值, 人, 理由)` 幂等（B1 仍绿）。
- 前端（§2.6）：补材料 / 补料厚成功后改成 **`fetchPackagingParts()` 服务端重读**，
  只有重读失败才退回 POST 回显打内存补丁 —— 刷新前后现象一致。

### 已记录的偏差（不改测试）

`tests/test_packaging_part_manual_fill_persists_red.py::AWhenFilled::test_a2` 的**第三条断言**
（`unprocessable_reason_mix` 相应 −1）与**同一探针的 A3** 不可能同时成立：

- 探针在同一次运行里既给 `DWG-P01` 补了材料、又给 `DWG-P03` 补了料厚；`processability()` 的
  「缺材料 / 缺料厚」共用同一个码 `PACKAGING_PART_MATERIAL_UNKNOWN`，所以补完之后这一项
  **由 2 变 0（键消失）**；
- 而 A3 明确要求同一探针里 `DWG-P03` 的 `ok == True`（补料厚必须真的生效），A1 要求 `DWG-P01`
  `ok == True`；三件里剩下那件 `DWG-P09` 材料与料厚都齐 —— 于是「还有 1 件缺材料」在事实层面
  不存在。`unknown_mix_before == 2` 已是本机实测（`before - 1 == 1` 无法达到）。
- 本层按 Spec 执行（A1 / A2 前两条 / A3 / B1–B4 全绿），**不动那条断言**；要它转绿需要测试侧把
  `- 1` 改成 `- 2`，或把两件分开两轮探测。

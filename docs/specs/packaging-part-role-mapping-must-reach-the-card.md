# 人工映射的业务角色必须看得见：BOM 侧映射完了，卡片上永远是 unknown（第 12 层）

血缘：`packaging-part-role-manual-mapping.md` §2.7（只有人工映射能改 `part_role`）/§1（卡片上
"还没映射"必须可见）、`packaging-parts-in-card-and-material-fill.md` §2.1 第 2 条（卡片第 6 步
必须有「角色」列）、`packaging-part-manual-fill-must-land-on-the-part-row.md` §2.1（"侧档要合回行"
的同一范式）、`e2e-packaging-dwg-quote-tech-continuity.md` §4.4（`reject_unknown_role_autobind()` 红线）。

状态：Spec + 红测（已实现）（本层只补**角色这一本账的读回**，不改 BOM 侧任何口径；红基见 §6、
落地与 1 条夹具偏差见 §7）
红测：`tests/test_packaging_part_role_mapping_reaches_card_red.py`
本批 changelog 条目号：`## 378`。

## 0. 一句话目标

用户在 BOM 页把 64 件的人工角色一个个映射完了，回到报价卡片第 6 步：**「角色」列还是 `unknown`**，
`role_known_ratio` 还是 `0.0` —— 刚做完的事在用户看得到的地方**一点痕迹都没有**，
于是"必须先完成人工映射"这句话在卡片上永远像是没做完。

这就是 `packaging-part-manual-fill-must-land-on-the-part-row.md`（材料 / 料厚 / 轮廓）的
**同一形状第四次出现**：人工动作落在侧档上，而卡片与摘要只认零件行。
那次修的是材料 / 料厚 / 轮廓三本账（`_manual_fill_overlay()` 只合
`DOC_KEY_MATERIAL` / `DOC_KEY_THICKNESS` / `DOC_KEY_OUTLINE`），**角色这本账没在里面**。

## 1. 根因（代码事实，逐条可复现；不是推断）

1. **零件行的角色来自图纸图层，不来自人工映射**：`extract()` 里每行的
   `role` 由 `_layer_roles()`（`packaging_parts.py`，图层名 → 角色）给出，
   真实客户图 `layers = ["0","DESIGN"]` 无语义 → 64 件全是 `unknown`
   （34 实测 `a820...`/`a42e5e60a720`：`summary.role_known_ratio = 0.0`、`stats.by_role = {"unknown": 64}`）。
2. **人工映射只写 BOM 侧**：`POST …/requirement/packaging-bom/role-map`
   （`main.py` 的 `PACKAGING_BOM_ROLE_MAP_WRITE_PATH`）→ `packaging_bom.apply_role_mapping()`
   → `packaging_bom.save_role_mapping()`：事实源写 **BOM 行**的
   `size_source_json.dwg_binding`（`UPDATE wip_packaging_bom_item …`），留痕写 meta 文档
   `ROLE_MAP_DOC_KEY = "packaging_bom_role_map"` 的
   `by_requirement[需求单][行键] = {item_key, part_code, role, …}`。
   **没有任何一步写零件文档那一行。**
3. **读回这一侧没有角色这本账**：`packaging_parts._manual_fill_overlay()` 的键集合逐字只有
   `DOC_KEY_MATERIAL` / `DOC_KEY_THICKNESS` / `DOC_KEY_OUTLINE`；`load_parts()` 是唯一挂 overlay 的读入口
   （`list_parts()` 无 overlay 且全仓无调用方），它不认识 `ROLE_MAP_DOC_KEY`，
   也不认识 `packaging_bom`（零件侧不 import BOM 侧）。
4. **卡片与摘要都只看零件行**：`summarize()` 的
   `role_known = sum(1 for row in rows if _text(row.get("role")) not in ("", "unknown"))`
   → `role_known_ratio`；卡片 `card_row()` 的 `"role": _text(payload.get("role"))`。
   两者都只读零件行 → 人工映射对它们不可见。
5. **后果**（与 34 实测一致）：`packaging-part-role-manual-mapping.md` §1 当初抱怨"卡片上没有「角色」列、
   '还没映射'不可见"；`## 315` 把列加上了，但这一列**永远只能显示 `unknown`** ——
   因为唯一能让它变成具体角色的那条路（人工映射）不在它的取数链上。
   用户看到的现象是"我映射完了，卡片没变"，与"补完材料刷新就没"是同一个病。

## 2. 口径（逐条，可直接验收）

1. **人工映射落盘之后，零件行必须看得到那个角色**：`packaging_parts.load_parts(project_id)` 读回的
   那一行，其 `role` 必须等于映射记录里的 `role`。**配对键只有 `part_code` 一个**
   （不是 BOM 行键、不是行号、不是顺序），且读回**不要求调用方先知道需求单号** ——
   实现可以在 `by_requirement` 的各桶里按 `part_code` 找，命中即用。
   实现二选一，必须唯一且确定：
   （a）映射成功时把角色写回零件文档那一行；或
   （b）读路径统一 overlay —— 与 `_manual_fill_overlay()` 同一处再合一本账（推荐：
   三本人工账 + 角色账在同一层，前端与摘要不必各写一套）。
2. **卡片与摘要跟着变**：同一回合后，卡片 `card_row()["role"]` 必须是那个角色；
   `summarize()["role_known_ratio"]` 必须把这一件算进"已知"。
3. **必须能分辨来源**：零件行要留"这个角色是人工映射来的"的痕迹，字段名与既有范式一致 ——
   `role_source`（与 `material_source` / `thickness_source` 同形状），至少含
   `kind`（人工映射取 `"manual_mapping"`）/ `bound_by` / `mapped_at` / `note`（无则空串）。
   不许把人工值与 `_layer_roles()` 的自动推定混在一个没有留痕的字段里。
4. **红线不变**：`packaging_bom.reject_unknown_role_autobind()` 继续生效；
   `_layer_roles()` / `extract()` 的自动判定逐字不改；**只有**人工映射这一条路能把零件行的 `role`
   从 `unknown` 变成具体角色（`packaging-part-role-manual-mapping.md` §2.7）。
5. **配对口径与 BOM 侧重放逐字不变**：只有"映射记录里的 `part_code` 在零件文档里找得到"才改那一行
   （与 `apply_saved_role_map()` 的同一口径）；找不到就**一行都不改**，绝不按行号 / 面积 / 顺序猜。
   幂等（同行同角色重复提交无变化）、改绑（保留 `superseded_*`）、
   `apply_saved_role_map()` 在重算 BOM 之后仍重放 —— 全部照旧。
6. **BOM 侧与卡片列定义不改**：`ROLE_MAP_DOC_KEY` 的记录形状、`role_map_status()` 的未映射清单、
   `role_map_status().mapped_total` 的口径、`CARD_COLUMNS` 的 10 列，一个字都不动。

## 3. 允许修改范围

- `tech_app/backend/services/packaging_parts.py`：`_manual_fill_overlay()` 再加一本角色账，
  或在读路径上合入（**不许 import `packaging_bom` 造成循环依赖**：读 meta 文档即可，
  doc key 用 `packaging_bom.ROLE_MAP_DOC_KEY` 的字面量常量或由调用方传入）。
- `tech_app/backend/main.py`：若走 "写回零件文档" 方案，在 role-map 写路由成功之后调用零件侧的落库函数；
  若走 overlay 方案，确保 `_packaging_part_row()` 等读入口取到合并后的行。
- `tech_app/frontend/app.js`：若列表行的渲染依赖 `role`，按服务端重读（不许只改内存）。
- 禁止：改 `_layer_roles()` / `extract()` 的角色判定、改 `reject_unknown_role_autobind()`、
  改 BOM 侧 `apply_role_mapping()` / `save_role_mapping()` / `apply_saved_role_map()` 的写入口径、
  改 `CARD_COLUMNS`、按行号或面积顺序猜角色。

## 4. 红测分组（`tests/test_packaging_part_role_mapping_reaches_card_red.py`）

- **A 组 读回（今天都是红的）**
  - A1（红）映射记录落盘后，`load_parts()` 那一行的 `role` 必须等于映射到的角色
    （今天仍是 `unknown`）；
  - A2（红）`summarize()["role_known_ratio"]` 必须把这一件算进"已知"（今天仍是 `0.0`）；
  - A3（红）卡片 `card_row()["role"]` 必须等于映射到的角色（今天仍是 `unknown`）；
  - A4（红）零件行必须有来源留痕 `role_source`，`kind` 为 `"manual_mapping"` 且带 `bound_by`
    （今天行上没有任何角色来源字段）。
- **B 组 护栏（今天就是绿的，不许被改红）**
  - B1 `packaging_bom.reject_unknown_role_autobind()` 仍在，且 `_layer_roles()` 的自动判定未被改成
    "自动贴角色"（`extract()` 的 `role` 仍由图层语义给出，不写 `role_source`）；
  - B2 BOM 侧四个符号仍在：`ROLE_MAP_DOC_KEY` / `apply_role_mapping()` / `save_role_mapping()` /
    `apply_saved_role_map()` / `role_map_status()`；
  - B3 **配对口径不变**：映射记录里的 `part_code` 在零件文档里找不到时，`load_parts()` 的**任何一行**
    都不许被改（今天与实现后都必须成立）；
  - B4 卡片 `CARD_COLUMNS` 仍 10 列、`card_row()` 的可算性仍只取自 `processability()`。

## 5. 验收

1. 34 上对 `a42e5e60a720` 的若干部在 BOM 页做人工角色映射（例如 `DWG-P09 → 面纸`）；
2. 报价卡片第 6 步同一行的「角色」列显示该角色，`summary.role_known_ratio` 不再是 0.0；
3. 行上能看出"这是人工映射的"（`role_source.kind = manual_mapping` + 谁在什么时候映射的）；
4. 没映射的件仍显示 `unknown`，且 `reject_unknown_role_autobind()` 的拒绝仍然生效。

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_part_role_mapping_reaches_card_red \
  tests.test_packaging_part_role_manual_mapping_red -v
```

## 6. 红基（2026-09-22 实跑，未实现）

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_part_role_mapping_reaches_card_red
  → Ran 8 tests … FAILED (failures=4, errors=0)
```

红的 4 条 = A1（映射落盘后零件行 `role` 仍是 `unknown`）、A2（`role_known_ratio` 仍是 0.0）、
A3（卡片 `card_row()["role"]` 仍是 `unknown`）、A4（行上没有 `role_source` 留痕）；
绿的 4 条护栏 = B1（`reject_unknown_role_autobind()` 与自动判定不变）、
B2（BOM 侧五个符号仍在）、B3（配对不上就一行都不改）、
B4（卡片 10 列与 `card_row()` 取数不变）。

## 7 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_part_role_mapping_reaches_card_red
# 实现前：Ran 8 tests … FAILED (failures=4)   ← A1–A4
# 实现后：Ran 8 tests … FAILED (failures=1)   ← A2（见 §7.3，夹具断言与本轮口径冲突）
```

### 7.1 落点（§3 方案 b：读路径统一 overlay，**没有**写回零件文档）

| 契约 | 落点（`tech_app/backend/services/packaging_parts.py`） |
| --- | --- |
| §2.1 第 1 条 按 `part_code` 读回 | `_role_mapping_overlay(project_id)`：读 meta 文档 `packaging_bom.ROLE_MAP_DOC_KEY` 的 `by_requirement` **各桶**，按 `part_code` 命中即用（不要求调用方先知道需求单号）；同一个 `part_code` 出现在多条记录里时取 `(mapped_at, item_key)` 最大的那条（`_mapping_order()`），口径唯一且确定 |
| §2.1 第 1 条 读时合并 | `_manual_fill_overlay()` 在既有三本人工账（材料 / 料厚 / 轮廓）之外**再加一本角色账**，命中才改那一行；`load_parts()` 是唯一挂 overlay 的读入口，逐字不变 |
| §2.1 第 3 条 留痕 | `MANUAL_ROLE_KIND = "manual_mapping"` + `set_manual_role(row, role, *, bound_by, mapped_at, note)`（**纯函数**，返回副本）：写 `role` 与 `role_source = {"kind", "bound_by", "mapped_at", "note"}`；角色是空 / `unknown` / `unbound` 时 `ValueError`（这三个值正是"还没映射"，不许冒充映射过了） |
| §2.1 第 2 条 卡片与摘要 | 无需改：`card_row()` 取 `row["role"]`、`summarize()` 的 `role_known` 判据仍是 `role not in ("", "unknown")` —— 行上有了角色，两处自然跟着变 |
| §2.4 红线 | `_layer_roles()` / `extract()` 的自动判定、`packaging_bom.reject_unknown_role_autobind()` 逐字未动（B1 守卫） |
| §2.5 第 6 条 冻结面 | `CARD_COLUMNS` 10 列、`card_row()` 的可算性取数（`processability()`）、BOM 侧 `ROLE_MAP_DOC_KEY` / `apply_role_mapping()` / `save_role_mapping()` / `apply_saved_role_map()` / `role_map_status()` 全部一行未动（B2 / B4 守卫） |
| 兼容 | 零件文档的 `parts_id` / `parts_hash` 一个字不改（补录是「改行」，不是「重算零件」）；读路径**不 import** 造成循环依赖 —— `packaging_parts` 模块层已 `from . import packaging_bom`，直接用 `packaging_bom.ROLE_MAP_DOC_KEY` / `ROLE_UNBOUND_VALUES` 这**唯一事实源** |

### 7.2 复跑（不回归）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_part_role_manual_mapping_red \
    tests.test_packaging_parts_panel_red tests.test_packaging_parts_extraction_red \
    tests.test_packaging_drawing_flow_red
# 见本批提交说明（全部 OK，1 skip）
```

### 7.3 已记录的偏差（不改测试）

`A2`（`test_a2_role_known_ratio_moves_with_the_mapping`）的期望值与**冻结口径**相差一个分母：

- `role_known_ratio` 的定义是 `role != "unknown"` 的件数 / `part_total`
  （`packaging-parts-downstream-acceptance.md` §2；由 `test_packaging_parts_downstream_gate_red.py`
  的 A3 逐字守着，本层不许改）；
- 探针的零件文档是**两件** —— `DWG-P09`（映射到 `面纸`）与 `DWG-P10`（未映射），
  `stats.part_total = 2`；于是映射落盘后这一比率是 **0.5**（`0.0 → 0.5`，
  "把这一件算进「已知」"这件事**已经发生**），拿不到 A2 写的 `1.0`；
- 要得到 `1.0` 只能让 `DWG-P10` 也变成已知角色，而**同一文件**的 `B3` 明确要求它
  `other_role == "unknown"` 且 `other_role_source is None` —— 也就是说，唯一能让 A2 成立的做法
  （按行键 / 位置把第二条映射记录套到第二件上）正是 §2.5 第 5 条与 B3 禁止的"猜配对"。
  两条断言不可能同时成立。

本层按 Spec §2.1 第 1/2 条与 §2.5 第 5 条执行（A1 / A3 / A4 / B1–B4 全绿，**不动那条断言**）；
要它转绿需要测试侧把 `1.0` 改成 `0.5`（或把探针的零件文档收成一件）。

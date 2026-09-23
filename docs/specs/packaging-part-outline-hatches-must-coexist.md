# 未闭合件的两条出路必须**并存**：现在签字会被一次重算静默抹掉，这一件重新变回不可算（第 13 层）

血缘：`packaging-open-outline-part-needs-a-way-out.md` §2.3（两条出路：人工签字 / 单件重算）、
`packaging-part-manual-fill-must-land-on-the-part-row.md` §2.1（侧档读时合回零件行的同一范式）、
`packaging-parts-downstream-process-and-cost.md` §3（`processability()` 与 409 出口）。

状态：Spec + 红测（已实现）（原状：本层只修**两条出路的并存口径**，不改几何判据；红基见 §6）
红测：`tests/test_packaging_part_outline_hatches_coexist_red.py`
本批 changelog 条目号：`## 438`。

## 0. 一句话目标

`## 339` 给未闭合件开了两条出路（(a) 单件重算轮廓、(b) 人工签字按包围盒估算）。
两条都落地了，**但它们不是并存的**：同一件上先签字放行、随后只要再点一次「重算轮廓」
（哪怕只是想确认一下），**人签的字就被静默抹掉**，这一件重新变回 409 ——
没有提示、没有留痕说明"签字被顶掉了"。反过来（先重算再签字）也一样：重算那份留痕不见了。

真实图纸上 `odd_endpoints`（断口是真的）放大额度也算不出来，所以"签字放行"是主要出路；
而"再点一次重算"恰恰是最自然的动作 —— 这个顺序下用户会看到一个已经放行的件**莫名其妙又变红**。

## 1. 根因（代码事实，逐条可复现；不是推断）

1. **两条出路写进同一个 doc key**：`save_part_outline()`（签字，`kind = MANUAL_OUTLINE_KIND = "manual_bbox"`）
   与 `save_part_outline_recompute()`（重算，`kind = RECOMPUTED_OUTLINE_KIND = "recomputed_outline"`）
   都走 `_save_part_doc(project_id, DOC_KEY_OUTLINE, …)`，`DOC_KEY_OUTLINE = "packaging_part_outline"`
   （`packaging_parts.py:308`），最多 20 版。
2. **读回时每件每 key 只取最近一版**：`_manual_fill_overlay()` 用
   `overlays.setdefault(code, {}).setdefault(key, item)` 遍历 `_part_doc_items()`（新→旧）
   → **last-writer-wins**：一份记录就把另一份盖住。
3. **合并时又按 `kind` 二选一分派**：`_manual_fill_overlay()` 里
   「`kind == MANUAL_OUTLINE_KIND` → `set_manual_outline()`，否则 → `set_recomputed_outline()`」
   —— 一次只可能合一份，所以行上**永远不可能同时**有 `outline_confirmation` 与 `outline_recompute`。
4. **放行判据只认这两样之一**：`processability()` 是
   `outline_status != "closed" and not outline_confirmation(payload)` → 409
   （`packaging_parts.py:2351`），`outline_confirmation()` 只认行上的
   `outline_confirmation.kind == "manual_bbox"` + 非空 `bound_by`。
   → 签字记录被顶掉 = `outline_confirmation` 消失 = 这一件**重新 409**。
5. **本机离线实测（纯函数 + 侧档读写，未起服务、未发 HTTP）**：

| 顺序 | 结果 |
| --- | --- |
| 初始 `open`、无留痕 | `ok=False` + `PACKAGING_PART_NOT_CLOSED` |
| 只签字 | `ok=True`，`outline_confirmation=True` |
| 签字 → 重算（**仍 open**，`odd_endpoints`） | `ok=True → **False**`；`outline_confirmation=True → **False**` |
| 签字 → 重算（这次**闭合**） | `ok=True`（因 `closed`）；但 `outline_confirmation=True → **False**`（留痕丢） |
| 重算（仍 open）→ 签字 | `outline_recompute=True → **False**`（反方向同样丢） |

### 1.1 34 实测（2026-09-23，真实 HTTP，不是本机推断）

现场：SM1 报价会话 `1bef04f7dab3` → PE1 技术项目 `8131f6d29d99`（酒盒.dwg）→ 零件文档
`parts:c2f1579db133d9ba`（263 件，`open` 129 / `closed` 134）。取第一件未闭合件
`DWG-P01`（`open` + `outline_reason=odd_endpoints`）按顺序真的点：

| 步骤 | 接口 | 结果 |
| --- | --- | --- |
| ① 什么都不点，直接排工艺 | `POST …/packaging-parts/DWG-P01/process` | **409** `PACKAGING_PART_NOT_CLOSED` |
| ② 先点「重算轮廓」 | `POST …/packaging-parts/DWG-P01/outline/recompute` | 200，`changed=false`、仍 `open` + `odd_endpoints`（诚实） |
| ③ 点「按包围盒签字确认」 | `POST …/packaging-parts/DWG-P01/outline/confirm` | 200，`outline_confirmation` 落行 |
| ④ 签字后再排工艺 | 同上 `…/process` | **409** `PACKAGING_PART_MATERIAL_UNKNOWN`（轮廓已放行，只剩材料/料厚 —— 签字这条路是通的） |
| ⑤ **再点一次**「重算轮廓」 | `POST …/outline/recompute` | 200 `changed=false` |
| ⑥ **关键**：签字后再排工艺 | 同上 `…/process` | **409 `PACKAGING_PART_NOT_CLOSED`** —— 人签的字被这一次重算静默抹掉了 |
| ⑦ 读回这一件的出路留痕 | `GET …/outline/confirm` | `{"manual": true, "kind": "recomputed_outline", "bound_by": "PE1", …}` —— 只剩重算那一份 |
| ⑧ 读回零件行 | `GET …/packaging-parts/DWG-P01` | `outline_confirmation=False`、`outline_recompute=True` |

即：**同一台服务器上，与 §1 的本机离线结论逐条一致**（签字 → 重算 = 放行被抹掉），
而且读路由 `…/outline/confirm` 还把重算那一份报成 `manual: true`（`manual` 这个布尔
今天等于"有任一版留痕"，分不出是人签的还是重算的）。

## 2. 口径（逐条，可直接验收）

1. **两条出路并存，不是"后写的顶掉先写的"**：同一件上的
   `outline_confirmation`（人签的字）与 `outline_recompute`（单件重算的结论）各自独立落账、
   各自可读；**写任何一条都不许清掉另一条**。
2. **签字只由用户显式撤销**：签字之后做重算（无论重算是否算出闭合），`outline_confirmation`
   必须**原样保留**（`kind` / `bound_by` / `reason` / `confirmed_at` / `outline_status_kept` 逐字不变），
   于是 `processability().ok` 仍为 `True`。要取消放行，必须有一个**具名的撤销动作**
   （明确写一条"撤销签字"的留痕），不许靠"写别的记录"顺带抹掉。
3. **重算的几何结论照旧生效**：重算真的算出闭合时，行仍按重算更新
   （`outline_status = "closed"`、`outline` / `size_source` / 展开尺寸按重算）——
   这一条已实现，不许退回；此时签字与 `closed` 并存（`closed` 优先，签字只作留痕）。
4. **读回口径唯一且确定**：同一件的两份留痕必须**同时**能从
   `packaging_parts.load_parts()` 那一行读到；`_manual_fill_overlay()` 仍是唯一合并处。
5. **不许放宽**：没有任何留痕的未闭合件仍必须 409 `PACKAGING_PART_NOT_CLOSED` +
   `missing_variables == ["outline"]`；`outline_status` 仍不许被人工写成 `"closed"`。
6. **冻结面**：`processability()` 的判据与文案、`OUTLINE_OPEN_REASONS` 闭集、
   两条 rule id（`part_outline_manual_bbox_v1` / `part_outline_recompute_v1`）、
   `CARD_COLUMNS` 的 10 列、`card_row()` 的可算性取数、`parts_id` / `parts_hash` —— 全部一个字不动。

## 3. 允许修改范围

- `tech_app/backend/services/packaging_parts.py`：让两份轮廓留痕**分账**（例如签字继续走
  `DOC_KEY_OUTLINE`、重算换一个 key，或在同一 key 下按 `kind` 各取最近一版**分别**合并），
  并把 `_manual_fill_overlay()` 的轮廓分支改成"两份都合、互不覆盖"。
- 若新增 key/字段，命名与 `DOC_KEY_*` / `*_KIND` 既有范式一致；读路径仍只挂 `load_parts()` 一处。
- 禁止：改 `processability()` 判据与文案、改 `_open_outline_reason()` / `outline_diagnosis()` /
  `_rescue_outline()` 的判定、把 `outline_status` 写成 `"closed"`、改两条 rule id、
  改 `CARD_COLUMNS`、动 `packaging_bom` 一侧任何口径。

## 4. 红测分组（`tests/test_packaging_part_outline_hatches_coexist_red.py`）

- **A 组 并存（今天都是红的）**
  - A1（红）签字 → 重算（仍 `open`）：`processability().ok` 必须仍为 `True`；
  - A2（红）同上：行上 `outline_confirmation` 必须仍在，且 `bound_by` / `reason` 逐字不变；
  - A3（红）签字 → 重算（**闭合**）：`outline_status == "closed"` **且** `outline_confirmation` 仍在
    （两份留痕同时可见），`ok` 为 `True`；
  - A4（红）反方向：重算（仍 `open`） → 签字：`outline_recompute` 与 `outline_confirmation` **都在**行上。
- **B 组 护栏（今天就是绿的，不许被改红）**
  - B1 无任何留痕的未闭合件仍 409 `PACKAGING_PART_NOT_CLOSED` + `missing_variables == ["outline"]`；
  - B2 `recompute_outline()` 仍是纯函数：不改入参；缺 IR / 缺这一件的分量 → `ValueError`；
  - B3 没有任何地方把 `outline_status` 写成 `"closed"`（源码守卫）；
  - B4 卡片 `CARD_COLUMNS` 仍 10 列、`card_row()` 的可算性仍只取自 `processability()`、
    两条 rule id 与 `OUTLINE_OPEN_REASONS` 不变。

## 5. 验收

1. 34 上对 `a42e5e60a720` 的 `DWG-P01`（`odd_endpoints`）：先点「重算轮廓」→ 仍 `open`（诚实照旧）；
2. 再点「按包围盒估算」签字 → 这一件的「工艺推荐」/「成本测算」能跑通（200）；
3. **再点一次「重算轮廓」** → 这一件**仍然**能跑通（签字没被抹掉），刷新后卡片第 6 步也仍显示"人放行"；
   此时行上同时看得到重算诊断与签字留痕；
4. 未点过任何出路的其它未闭合件仍照旧 409。

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_part_outline_hatches_coexist_red \
  tests.test_packaging_open_outline_part_needs_a_way_out_red -v
```

## 6. 红基（2026-09-23 实跑，未实现）

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_part_outline_hatches_coexist_red
  → Ran 8 tests … FAILED (failures=4, errors=0)
```

红的 4 条 = A1（签字后被一次重算顶回 `ok=False`）、A2（`outline_confirmation` 被抹掉）、
A3（重算闭合时签字留痕也丢）、A4（反方向：重算留痕被签字顶掉）；
绿的 4 条护栏 = B1（无留痕仍 409）、B2（`recompute_outline()` 仍是纯函数 + 入参校验）、
B3（没人把 `outline_status` 写成 `closed`）、B4（卡片列与两条 rule id 不变）。

## 7. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 439`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 两条出路并存 | `packaging_parts.py` 新增常量 `OUTLINE_DOC_IDENTITY = ("part_code", "parts_id", "kind")` | 轮廓那本账**按 `kind` 分段**（签字 / 重算各一版），`save_part_outline()` 与 `save_part_outline_recompute()` 都以它调 `_save_part_doc()` |
| §2.1 写侧不再顶掉 | 同文件 `_save_part_doc(..., *, identity=("part_code", "parts_id"))` | 新增 `identity` 形参：`kept` 过滤按分段键判"同一份结论"，默认值与 `save_part_process()` / `save_part_cost()` / 材料 / 料厚**逐字不变**；写重算不再删掉同一件上的签字记录 |
| §2.4 读侧两份都合 | `_manual_fill_overlay()` | 轮廓侧档改成 `outlines[part_code][kind]` 分桶（各取最近一版、两份都合）：先 `set_manual_outline()`（人放行），再 `set_recomputed_outline()`（真算闭合时以几何为准，签字只作留痕）；材料 / 料厚 / 角色三条既有合并口径未动 |
| §2.2 签字只由具名动作撤销 | 未动 | 本次改动**没有**任何"撤销签字"的顺带路径，签字记录只在同 kind 再写一版时被替换 |
| §2.6 冻结面 | 未动 | `processability()` 判据与文案、`OUTLINE_OPEN_REASONS`、两条 rule id、`CARD_COLUMNS` 10 列、`card_row()` 取数、`parts_id` / `parts_hash` 全部未改（B3/B4 绿） |

边界（登记，不改）：`GET …/outline/confirm`（`load_part_outline()` → `_load_part_doc()`「最近一版」）本批**未动** ——
签字后再重算，该接口仍返回最近写的那一版（§1.1 ⑦ 的现象在这个端点上还在）；行内的唯一事实源
（`load_parts()` → `_manual_fill_overlay()`）已经是两份并存，卡片 / 面板 / `processability()` 都从那一行取数。
要不要把这个读接口也改成"签字优先"或"两份都给"，另开一批裁决。

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_part_outline_hatches_coexist_red
Ran 8 tests ... OK        （红基：Ran 8 ... FAILED (failures=4) —— A1/A2/A3/A4 红，B1–B4 绿护栏）

./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_open_outline_part_needs_a_way_out_red
（30 个 `test_packaging_part(s)_*_red.py` 一起跑）Ran 448 tests ... FAILED (failures=1, skipped=4)
  —— 唯一 1 红是既有固定挂账 `part_role_mapping::test_a2_role_known_ratio_moves_with_the_mapping`
     （把本批改动 stash 掉后同一条仍红，非本批引入；见 `## 434` 之后的挂账清单）
```

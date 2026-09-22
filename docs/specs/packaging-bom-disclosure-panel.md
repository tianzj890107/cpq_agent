# 规格：BOM 面板上必须看得见「配对复核 / 回填失败 / 业务清单换版」这三本账

依赖：`docs/specs/packaging-parse-to-downstream-seams.md`（§3.2 要求 `pairing_review` **可读**；
§4 明写"不动前端"，把界面接入留在后面）、`docs/specs/packaging-silent-degradation-disclosure.md`
（§2.1 `binding_error` / §2.5 `pairing_review_unavailable` 的读侧披露体，
§2 item 6 只做了角色映射面板那一处）、`docs/specs/packaging-business-parts-version-pinning.md`
（§2.2 `business_parts_stale` 两档原因码）、`docs/specs/packaging-silent-degradation-disclosure.md`
（"说不出'这一版到底有没有不一致 / 有没有回填失败'就是静默降级"）。

状态：Spec + 红测（已实现）（原状：`tech_app/frontend/app.js` 与
`tech_app/frontend/requirement-confirm.js` 里 `pairing_review` / `pairing_review_unavailable` /
`binding_error` / `business_parts_stale` **四个键 0 处引用**（`grep -c` 实测）——
后端已经在
`GET /api/projects/{pid}/requirement/packaging-bom` 的顶层把三本账交出来了
（`packaging_bom.load_bom()` 的 `":1391"` `pairing_review` / `":1394"`
`pairing_review_unavailable` / `":1396"` `binding_error` / `":1404"` `business_parts_stale`），
而 BOM 面板（`requirement-confirm.js` 的 `pbPanel()`）只渲染
`parts_binding_stale`（几何零件文档版本漂移）一件事：
① **几何零件版本漂移看得见，业务清单版本漂移看不见**——同样一条"这一行是上一版清单算的"，
两本账一个有一个没有；
② **材料配错（磁铁被配到纸面板上）看不见**：`pairing_review` 只在接口上，
想看出来只能去 `items[].size_source_json` 里 `JSON.parse` 一个人读不懂的字符串；
③ **配对复核读不到时与"没有不一致"长得一模一样**（`pairing_review_unavailable` 无落点）；
④ **零件回填失败时与"没有失败"长得一模一样**（`binding_error` 无落点）
（`## 419`/`## 422` 修的是同一形状：接口上给了披露体，界面上 0 处））
红测：`tests/test_packaging_bom_disclosure_panel_red.py`
行号基线：HEAD `768cc28`

## 0. 一句话目标

BOM 面板上说清三件事、且**把"读不到"与"没有"分开**：
材料与几何件配错了几行（逐行给行键 + 两侧材料）、零件回填失败过没有、
这一版有多少行是上一版业务清单算的。

## 1. 现状缺口（代码级）

1. `pbPanel()`（`requirement-confirm.js:390`）渲染了 `record.parts_binding_stale`
   （`data-pb-parts-stale`）与 `record.parts_document_unavailable`（`data-pb-parts-unavailable`），
   但**没有** `business_parts_stale` 的对应块 —— 同一类漂移两本账只显示一本；
2. `pairing_review` / `pairing_review_unavailable` / `binding_error` 在前端**没有任何落点**
   （三处披露只到接口）；
3. 三家的空值语义各不相同（`{}` = 没有失败 / `[]` + `unavailable` 非空 = 读不到 / `[]` = 确实没有），
   前端**一处都没有区分**——今天连"区分"这件事都没发生。

## 2. 契约

### C1 四个纯函数（`requirement-confirm.js` 的包装 BOM 面板 IIFE 内；体内无 DOM / `fetch(` / `storage`）

- `pbPairingMismatchRows(record)` → `[{key, part_code, row_material, part_material}]`：
  - 只读 `record.pairing_review`（数组）；**只收不一致项**：`material_match === true` 的条目**跳过**
    （这一列的是"不一致"，后端 B3 要求恒为 `False`），其余值（含 `false` / 缺失）照收——
    **不许自己再判一次材料是否相等**（那会造出第二套判据）；
  - `item_key` 去空白后为空 → 跳过（不许造无名行）；
  - 四个值一律 `String(...).trim()`，缺的给 `""`（不写字面量 `undefined`）；
  - `record` 不是对象 / `pairing_review` 不是数组 → `[]`；
  - 行数只由真数据决定。
- `pbPairingMismatchNote(record)` → `""` 或一句：
  - `record.pairing_review_unavailable` 是**非空对象** → `配对复核读不到（<code>）：这一版不知道有没有材料配错，别当成「没有不一致」。`
    （`<code>` 逐字取后端的 `code`，取不到才回退字面量 `pairing_review_unavailable`）；
  - 否则 `pbPairingMismatchRows(record)` 非空 → `配对复核：N 行材料与绑定的几何件不一致，请核对后再往下算。`
    （N = 行数）；
  - 否则 `""`（确实没有不一致 → 一个字都不说）。
- `pbBindingErrorNote(record)` → `""` 或一句：
  - `record.binding_error` 是非空对象 → `零件回填失败（<code>）：<reason>；这一版 BOM 的几何绑定可能不完整。`
    （`code` / `reason` 逐字，缺的跳过不带空括号）；
  - `{}` / 非对象 → `""`（没有失败就不说）；
  - **不许**把 `binding_error_unavailable`（连披露文档都读不到）显示成"没有失败"：
    它是非空对象，走同一句，`code` 逐字是 `binding_error_unavailable`。
- `pbBusinessStaleRows(record)` → `[{key, label}]`（`key` 逐字 = `item_key`，空项跳过）：
  - `reason` 闭集认两档，其余一律未知档（**不许**归到已知两档）：

    | `reason` | `label` |
    | --- | --- |
    | `business_parts_reimported` | `业务清单已重新导入（这一行还是上一版清单算的）` |
    | `binding_without_version` | `这一行没记业务清单版本，判断不了是不是过期` |
    | 其它 / 空 / 非字符串 | `原因未知（后端没有给出原因档）` |
  - 非数组 → `[]`。

### C2 面板渲染（`pbPanel()`）

- 新增三块（都放在既有 `${unresolved}` / 映射备注块之后、`mixedBanner` 之前）：
  1. 配对复核：`data-pb-pairing-review="<行数>"` 块 + 逐行 `data-pb-pairing-mismatch="<item_key>"`
     （渲染 `key` / `part_code` / `row_material` / `part_material`）；
     `unavailable` 非空时改渲染 `data-pb-pairing-review-unavailable="<code>"` 块
     （内容 = `pbPairingMismatchNote(record)`）；
  2. 回填失败：`pbBindingErrorNote(record)` 非空时渲染 `data-pb-binding-error="<code>"` 块；
  3. 业务清单换版：`data-pb-business-stale="<行数>"` 块 + 逐行 `data-pb-business-stale-key="<item_key>"`；
- 既有 banner（混盒型 / 盒型未知 / 包围盒 / 零件文档过期 / 零件文档读不到）与 `pbRow()`
  **一字不动**；四块都为空的场景（今天种子的常态）**一个节点都不多**。

### C3 冻结面

- 三个键的**唯一**事实源仍是后端响应（前端不重算配对、不重判材料、不猜回填有没有失败）；
- 不新增接口、不改后端、不改 `index.html`、不加依赖；`pbPanel()` 里不新增请求；
- 不改 `tests/` 下任何文件。

## 3. 允许修改范围

1. `tech_app/frontend/requirement-confirm.js`：C1 四个纯函数 + `pbPanel()` 的三块；
2. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许在前端自己判"行材料 == 件材料"（`pairing_review` 已经是后端判过的结论）；
- 不许把 `pairing_review_unavailable` / `binding_error_unavailable` 显示成"没有不一致" / "没有失败"；
- 不许把未知 `reason` 归到已知两档；不许在 `item_key` 为空时造无名行；
- 不许改后端键名与形状（`pairing_review` / `pairing_review_unavailable` / `binding_error` /
  `business_parts_stale` 逐字消费）；
- 不许改 `tests/` 下任何文件（含本批红测）、不许放宽任何断言；
- 不连 PG / 34、不写业务数据、不 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_bom_disclosure_panel_red -v
node --check tech_app/frontend/requirement-confirm.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parse_to_downstream_seams_red tests.test_packaging_downstream_blockers_red \
  tests.test_packaging_bom_parts_version_binding_red tests.test_packaging_business_parts_version_pinning_red \
  tests.test_packaging_material_unresolved_panel_red tests.test_packaging_parametric_bom_red
```

## 6. 已记录的边界

1. 只**显示**这三本账，不提供"在面板上改配对 / 重跑回填"的按钮（那是写路径，另批）；
2. `pairing_review` 里除材料以外的配对依据（`pairing_basis` 那类）本批不上界面；
3. 业务清单换版的行**不自动重算**（`## 419`/`422` 同口径：披露不是行动）；
4. 四块都空时不渲染任何节点（页面上"什么都没有"= 确实没有这三件事，不是加载中）；
5. 其它行业的 BOM 面板仍在 `industry === 'packaging'` 之外不挂载（既有条件不动）。

## 7. 落地状态（2026-09-22，Codex 实现）

**实现前红基**（`git stash push -- tech_app/frontend/requirement-confirm.js` 后的原文）：

```
Ran 29 tests ... FAILED (failures=24)
```

24 红 / 5 绿 —— 绿的五条是环境与冻结面护栏（E5 既有 banner 未动、E6 面板不发请求、
F1 `pbRow()` 未变、F4 `index.html` 未动、G1 `node --check` 通过）；
24 红 = A1–A5、B1–B4、C1–C4、D1–D5（四个纯函数根本不存在）+ E1–E4（面板里 0 处
`data-pb-pairing-review` / `data-pb-pairing-review-unavailable` / `data-pb-binding-error` /
`data-pb-business-stale`）+ F2、F3。

**实现后**：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_bom_disclosure_panel_red -v
Ran 29 tests ... OK
node --check tech_app/frontend/requirement-confirm.js   # 退出码 0
```

落点（只改了 `tech_app/frontend/requirement-confirm.js` 一个文件）：

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §C1 | `pbPairingMismatchRows()` / `pbPairingMismatchNote()` / `pbBindingErrorNote()` / `pbBusinessStaleRows()`（包装 BOM 面板 IIFE 内，`pbRow()` 之前） | 四个纯函数，体内无 DOM / `fetch(` / `storage`；三家的空值语义各判各的（`material_match === true` 跳过 / `unavailable` 非空 / `binding_error` 非空 / `business_parts_stale` 非空） |
| §C2 | `pbPanel()`：`${mapBlock}` 之后、`${mixedBanner}` 之前插入 `pairingBlock` / `bindingBlock` / `businessStaleBlock` | `data-pb-pairing-review="<行数>"` + 逐条 `data-pb-pairing-mismatch="<item_key>"`；读不到改渲 `data-pb-pairing-review-unavailable="<code>"`；`data-pb-binding-error="<code>"`；`data-pb-business-stale="<行数>"` + 逐行 `data-pb-business-stale-key="<item_key>"`；既有 banner 与 `pbRow()` 一字未动 |
| §C3 | 无 | 未新增接口、未改后端与 `index.html`、未加依赖；前端不重判材料、不猜回填是否失败 |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_parse_to_downstream_seams_red tests.test_packaging_downstream_blockers_red \
  tests.test_packaging_bom_parts_version_binding_red tests.test_packaging_business_parts_version_pinning_red \
  tests.test_packaging_material_unresolved_panel_red tests.test_packaging_parametric_bom_red
Ran 141 tests ... FAILED (failures=1)   # 唯一红是 `parse_to_downstream_seams::B4` 的
                                        # `size_quality` 键集冻结（`packaging-bom-size-quality-accounting.md`
                                        # §已记录的偏差，本批实现前后逐条同名）

# 同一个 `requirement-confirm.js` 上的其它面板批次
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_material_code_map_red tests.test_packaging_bom_business_material_rows_red \
  tests.test_packaging_bom_business_parts_rows_red tests.test_packaging_business_part_basis_in_panel_red \
  tests.test_packaging_handoff_audit_pending_panel_red tests.test_packaging_authority_workbook_upload_red \
  tests.test_packaging_handoff_audit_relay_red
Ran 157 tests ... OK
```

边界（与 §6 一致，实现如约未越）：只显示不提供写入口、不自动重算、不上 `pairing_basis`
那类配对依据、四块全空时一个节点都不多、非包装行业仍不挂载。

# 业务部件面板的「依据」区：显示权威出处与绑定分量证据（并清掉上一件的残留）

依赖：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（业务部件层与面板口径）、
`docs/specs/packaging-business-part-plan-click-and-bound-outline.md`（面板轮廓与点选分流）。

状态：Spec + 红测（已实现）（2026-09-22 落地：读接口 source + 依据行纯函数 + 共用渲染 + 后到刷新；15 OK）
红测：`tests/test_packaging_business_part_panel_evidence_red.py`

## 1. 目标与验收主路径

1. 点业务部件时右栏「依据」区**属于这一件**：今天 `openPackagingBusinessPart()` 根本不写
   `#packagingPartEvidence`，上一件几何零件的实体证据（或"正在读取零件详情…"）会一直留在那里，
   与当前选中的业务部件张冠李戴。
2. 依据要能回答"这份业务资料凭什么"：权威清单出处（表 + 行 + 文件指纹）+ 绑定分量的实体证据
   （分量/图层/图元数）；拿不到就如实写"未记录"，不猜、不编。
3. 读接口把权威出处交出来：`_business_parts_body()` 今天把文档里的 `source`
   （`ir_id` / `ir_hash` / `authority_file_hash` / `authority_sheet`）整块吞掉 —— 页面无从显示
   "这份清单来自哪张表、哪一版"。
4. 平面图**后**到不再留空白轮廓：首点竞态（点业务部件时 `currentPackagingCadPlan` 还没加载完）
   下，平面图到位后要用新到的证据重画当前选中的业务部件。

## 2. 契约

### C1 读接口带权威出处

`_business_parts_body(pid, doc)` 出参新增 `source`：

- 文档已生成 → 逐字透传文档里的 `source`（**没有就 `{}`**，不许编文件名）；
- 文档未生成 → `{}`。

既有键（`built` / `engine_version` / `business_parts_id` / `business_parts_hash` / `business_parts` /
`geometry_evidence` / `gap` / `summary` / `binding_statuses`）一个不改。

### C2 依据行由纯函数决定

`packagingBusinessPartEvidenceRows(row, components, source)` → `[{kind, ref, layer, note}]`，
顺序固定：

1. **权威出处**一行：`kind:"authority"`；`ref` 由行级 `authority.source`（`sheet` / `row`）与文档级
   `source`（`authority_sheet` / `authority_file_hash`）拼出（表名 + 第 n 行 + 文件指纹前 12 位）；
   `note` 写"业务尺寸 / 材料 / 排版来自这份权威清单"。拼不出任何出处时 `ref` 为空、`note` 写
   "权威出处未记录"，**不许**猜文件名或表名。
2. **绑定分量**每件一行：`kind:"component"`；`ref` 是 `component_id`；`layer` 是该件的图层；
   `note` 由角色 / 图层 / 图元数拼出（取不到的字段跳过，不写字面量 `undefined`）。
3. **没有绑定**时补一行 `kind:"binding"`：`note` 写"尚未在 CAD 图中定位（几何未绑定；材料与采购项
   不受影响）"。

### C3 面板与几何零件通道共用一套行渲染

抽出 `packagingPartEvidenceRowsHtml(rows)`（`{kind, ref, layer, note}` → 现有
`packaging-part-evidence-row` 结构；空列表 → 既有"这一件没有可回查的实体证据。"文案），
`renderPackagingPartPanel()`（几何零件面板）与 `openPackagingBusinessPart()`（业务部件面板）
都必须走它 —— 两种面板块样式一致、空态一致。

### C4 平面图后到要刷新当前业务部件

`renderPackagingCadPlan()` 结束时：若 `currentPackagingBusinessPartCode` 非空，就用新到的证据重画
该业务部件面板（轮廓 + 依据）。首点竞态下不许留下空轮廓与旧依据。

### C5 冻结面

- 不改绑定判据 / 状态机 / 容差 / 原因码，不改证据层的形状键（`drawing_bbox` / `outline_points`）；
- 不改几何零件通道的数据来源（`selectPackagingPart()` 仍读单件详情接口，仍渲染 `payload.evidence`）；
- 不改平面图的其余交互与配色；新纯函数不引用 `document` / `sessionStorage` / `window.` / `fetch(`；
- 不新增接口与依赖，不在浏览器端算几何。

## 3. 允许修改范围

| 文件 | 改什么 | 契约 |
| --- | --- | --- |
| `tech_app/backend/main.py` | `_business_parts_body()` 出参加 `source`（透传，缺则 `{}`） | C1 |
| `tech_app/frontend/app.js` | 新增 `packagingBusinessPartEvidenceRows()` 与 `packagingPartEvidenceRowsHtml()`；`renderPackagingPartPanel()` 改用共用行渲染；`openPackagingBusinessPart()` 重写 `#packagingPartEvidence`；`renderPackagingCadPlan()` 末尾按 C4 刷新 | C2、C3、C4 |

## 4. 禁止事项

- 不许改 `tests/` 下任何文件（含本批红测），不许放宽断言让它变绿；
- 不许猜/编权威出处（没有文件名就没有文件名，只报表 + 行 + 指纹）；
- 不许把业务部件的依据区留成上一件的内容（选中即重写，空也要写空态）；
- 不许改业务文档的落库内容与 `business_parts_id/hash` 口径（本批只读透传）；
- 不许改样式表、不许连生产库、不许新增依赖。

## 5. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_part_panel_evidence_red -v
node --check tech_app/frontend/app.js
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_part_plan_click_and_bound_outline_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_and_cad_plan_view_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_panel_red
```

## 6. 现状缺口（源码 / 形状实测，不是推断）

- `openPackagingBusinessPart()` 只写 `packagingPartTitle` / `packagingPartFacts` /
  `packagingPartOutline` / `packagingPartActions`，**从不写** `#packagingPartEvidence` ——
  几何零件面板留下的实体证据会一直挂在那里。
- `main._business_parts_body()` 出参只有
  `binding_statuses / built / business_parts / business_parts_hash / business_parts_id / engine_version
  / gap / geometry_evidence / summary`（实测），文档里的 `source`（实测
  `{ir_id, ir_hash, authority_file_hash, authority_sheet}`）没有出口。
- 权威清单逐行出处实测为 `{"sheet": "零部件排版工艺", "row": 4}`，
  文档级指纹实测 `authority_file_hash = "1358f7cd…"`、`authority_sheet = "零部件排版工艺"` ——
  页面能如实报"表 + 行 + 指纹"，但**没有**文件名可报（导入器不存文件名，本批不许编）。

## 7. 与既有 Spec 的关系（不重复立第二套）

- 业务部件面板的资料与状态展示以 `packaging-business-parts-and-cad-plan-view.md` 为准，
  本批只补"依据"这一块（出处 + 绑定分量证据）。
- 证据行的字段口径（`kind` / `ref` / `layer` / `note`）沿用几何零件面板既有结构
  （`packaging-parts-selectable-panel.md`），本批只是把它抽成共用渲染。
- 平面图点选分流与绑定分量形状以 `packaging-business-part-plan-click-and-bound-outline.md` 为准；
  本批只加"证据后到就刷新"。

## 8. 落地状态（2026-09-22）

### 8.1 落点

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| C1 | `tech_app/backend/main.py` `_business_parts_body()` | 文档未生成 → `"source": {}`；已生成 → `"source": dict(record.get("source") or {})`，其余键一字不动 |
| C2 | `tech_app/frontend/app.js` `packagingBusinessPartEvidenceRows(row, components, source)` | 纯函数（无 `document` / `sessionStorage` / `window.` / `fetch(`），① 权威出处 ② 每件绑定分量一行 ③ 无绑定补一行说明 |
| C3 | `tech_app/frontend/app.js` `packagingPartEvidenceRowsHtml(rows)` | 共用行渲染 + 共用空态「这一件没有可回查的实体证据。」；`renderPackagingPartPanel()` 改调用它，输出逐字不变 |
| C4 | `tech_app/frontend/app.js` `renderPackagingCadPlan()` 末尾 + `openPackagingBusinessPart()` | 平面图到位后若 `currentPackagingBusinessPartCode` 非空即以新证据重画；选中即重写 `#packagingPartEvidence`（清掉上一件残留） |
| C5 | 冻结面未动 | `geometry_evidence` / `binding_statuses` / `gap` / 业务尺寸与绑定口径、左栏、导入器、导出都没碰 |

### 8.2 判定口径

- 权威出处只报**表 + 行 + 文件指纹前 12 位**；拼不出任何一段就如实写「权威出处未记录」，
  绝不显示文件名（导入器本来就没存文件名）。
- 绑定分量的细节（角色 / 图层 / 图元数）来自业务文档自己的 `geometry_evidence`（与绑定同一版）。
- `packagingPartEvidenceRowsHtml()` 的**位置是硬约束**，不是风格问题（见 §9 边界 4）。

### 8.3 复跑原文

```
$ ./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_part_panel_evidence_red
Ran 15 tests in 2.397s

OK
```

相邻不回归（7 个模块）：

```
$ ./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_business_part_panel_evidence_red \
    tests.test_packaging_business_part_plan_click_and_bound_outline_red \
    tests.test_packaging_business_parts_and_cad_plan_view_red \
    tests.test_packaging_parts_panel_red \
    tests.test_packaging_cad_plan_true_outline_polygons_red \
    tests.test_packaging_cad_plan_drawing_coordinates_red \
    tests.test_packaging_business_parts_binding_size_source_red
Ran 107 tests in 15.400s

OK
```

packaging 全域：

```
$ ./open-claude/.venv/bin/python -W ignore -m unittest $(ls tests/test_packaging_*.py \
    | sed 's#/#.#g; s#\.py$##' | tr '\n' ' ')
Ran 1604 tests in 64.366s

FAILED (failures=5, skipped=8)
```

5 条失败全是本批之前就挂账、不属于本 Spec 范围的既有红灯：
`packaging_bom_part_size_provenance_red::B3`、`packaging_parse_to_downstream_seams_red::B4`、
`packaging_part_manual_fill_persists_red::A2`、`packaging_route_bom_version_pinning_red::F2`、
`packaging_quote_send_recovery_red::C1`（与 ## 370–373 时点逐条相同）。
`node --check tech_app/frontend/app.js` 通过；`git diff --check` 干净。

## 9. 已记录的边界

1. 出处只能报到**表 + 行 + 文件指纹**：导入器不保存工作簿文件名（只有 `file_hash`），
   所以页面不许显示任何文件名 —— 指纹是"是不是同一份"的唯一凭据。
2. 依据行里的分量细节（角色 / 图层 / 图元数）取自业务文档自己的 `geometry_evidence`
   （与绑定同一版），不是当前平面图那一份；两者版本不同时以绑定那一版为准。
3. 本批不做"证据下载/导出"，也不改左栏。
4. **共用行渲染函数的位置是硬约束**：`packagingPartEvidenceRowsHtml()` 既不许前移到
   `renderPackagingPartPanel()` 之前，也不许落在 `packagingPartProcess` 之后 4000 字以内。
   两条既有纯源码窗口护栏各自钉住一侧 ——
   `tests.test_packaging_parts_panel_red::E2` 取 `function \w*[Pp]ackagingPart\w*` 的
   **第一命中**并要求其函数体内有 `viewBox`（所以它只能是 `renderPackagingPartPanel`）；
   `tests.test_packaging_parts_downstream_red::F3` 在 `packagingPartProcess` 的
   `[−2000, +4000]` 字窗口里要求出现 `CadInlineAnalysis`（所以这一段不许被新代码挤长）。
   本批两处都撞过一次（先放面板前 → E2 红；改放面板后 → F3 红），最终固定在
   「`packagingBusinessPartEvidenceRows()` 之前、`renderPackagingPartPanel()` 之后足够远处」。
   把函数**换名**规避这两条护栏也不对：`packagingPartEvidenceRowsHtml` 是本 Spec §C3 的对外名字。
   后续再往这两段之间加代码，必须先跑这两个模块。

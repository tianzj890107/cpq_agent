# Spec：任务文件预览里那句「哪一张图纸」的归属说明，现在永远出不来（源名字一直喂不进去）

状态：Spec + 红测（已实现）（2026-09-23 由本批落地：`app.js` 新增纯函数 `requirementSourceFilename()`
与模块级 `currentRequirementSourceFilename`（`openProject()` 留档），`openFilePreview()` 的 drawing 分支
把"本次解析的图纸名"真的喂给既有 `fileDrawingOwnershipNote()`；本机实测 `Ran 13 … OK`，
本批 changelog 条目 `## 489`）—— 给"本次解析的图纸名"补一个纯函数口径、在打开项目时留档、
并在预览的 drawing 分支真的喂给那句归属说明；根因与实测见 §1）
红测：`tests/test_packaging_drawing_preview_ownership_note_source_red.py`
血缘：`docs/specs/packaging-task-file-dwg-opens-the-whole-plan.md`（§C5 那个纯函数就是本批要喂活的对象）、
`docs/specs/tech-file-preview-in-card-and-auth.md`（预览只有一份实现）、
`docs/specs/packaging-business-parts-and-cad-plan-view.md`（那张整图的数据源口径）
本批 changelog 条目号：`## 489`（落地时序号，2026-09-23 Codex 实现）。

## 0. 症状（2026-09-23 工作副本只读）

`## 487` 落地后，任务文件里点 `酒盒.dwg` 已经能看到那张整张平面图，但
Spec `packaging-task-file-dwg-opens-the-whole-plan.md` §C5 的**归属说明在任何情况下都不会出现** ——
点附件里另一份 DWG / DXF 也看不到"本次解析的图纸是 xxx；这一份不是本次解析用的图纸"。
纯函数与红测都是绿的，问题只是**没人喂它**。

## 1. 实测证据

| 读数 | 实测 |
| --- | --- |
| 前端怎么取"本次解析的图纸名" | `app.js::openFilePreview()` 的 `drawing` 分支：`payload.source_filename \|\| payload.source.filename` |
| 那个响应里到底有什么 | `GET /api/projects/{pid}/requirement/packaging-geometry`（`main.py:7580`）= `_business_parts_body()` + `geometry_evidence` + `parts_built` + `business_parts_gap` + `cad_scene`；`source` 块的键实测只有 **`ir_id` / `ir_hash` / `authority_file_hash` / `authority_sheet`**（离线跑同一条 `packaging_parts.business_parts_document()` 逐字核过），既没有 `source_filename`、也没有 `source.filename` |
| 结论 | 两个取值恒为 `""` ⇒ `fileDrawingOwnershipNote(file, "")` 恒回 `""` ⇒ 那句话是死代码 |
| 真名在哪（前端本来就有） | `app.js::openProject()` 里的 `data.meta.source_filename` —— 同一个字段就是 `/files` 里"需求原图（解析依据）"那一行的 `name`（`main.py:1533-1535`）；现在只被拿去判链路（视觉 / 图纸 / 3D / 其它），**没留档** |
| 谁在读它 | 卡片（`renderBoardFiles()`）与 2.1 悬浮小窗（`agent-chat.js::fileRow`）共用 `window.CadFilePreview.open()` ⇒ 一处喂活，两处都对 |

## 2. 目标行为

### C1 源名字有一个纯函数口径

新增纯函数 `requirementSourceFilename(projectBody)`：读 `projectBody.meta.source_filename`，
去首尾空白后返回；`projectBody` 不是对象 / `meta` 不是对象 / 该字段不是字符串 / 空串 →
一律 `""`（**不编名字**、不抛异常、不回落项目号之类的替代物）。

判据：`{"meta": {"source_filename": "酒盒.dwg"}}` → `"酒盒.dwg"`；`{"meta": {"source_filename": " 酒盒.dwg "}}`
→ `"酒盒.dwg"`；`{"meta": {}}` / `{}` / `None` / `"酒盒.dwg"` / `{"meta": {"source_filename": 123}}` → `""`。

### C2 打开项目时留档

`openProject()` 拿到 `GET /api/projects/{pid}` 的响应后，把 `requirementSourceFilename(data)` 存进
模块级 `currentRequirementSourceFilename`；读不到就是 `""`（覆盖上一份项目的残留值）。

### C3 预览里真的喂进去

`openFilePreview()` 的 `drawing` 分支把"本次解析的图纸名"传给既有纯函数
`fileDrawingOwnershipNote(file, sourceName)`：**优先**用响应里确实有的名字（现在没有；将来响应若带上就用它），
否则退到 `currentRequirementSourceFilename`；两边都空 → 仍然是 `""`（不啰嗦、不编）。

### C4 与既有口径一致（一个字不改）

- 点的那份就是本次解析的图纸（同名）→ 仍然 `""`；点附件里另一份 DWG / DXF → 仍然逐字写
  「本次解析的图纸：`<name>`；这一份不是本次解析用的图纸。」（`…-dwg-opens-the-whole-plan.md` §C5）；
- 预览仍然**只有一份实现**：`agent-chat.js` 里不许出现 `requirementSourceFilename` /
  `currentRequirementSourceFilename`；
- **不动后端**：`/files`（`"kind": "image"`）与 `/requirement/packaging-geometry` 的返回口径不变，
  不开新接口、不加新请求、不把 token 拼进 URL。

## 3. 本批明确不做（边界）

1. 不改 `packaging-geometry` 的响应结构（本批要修的是"前端已经拿到的那份数据没用上"，不是后端少给字段）；
2. 不为了这句话多发一次请求（`openProject()` 本来就拉过一次项目体）；
3. 不查附件清单、不做跨项目比对、不猜文件名；不改几何 / 成本 / 工艺 / 需求任何口径；
4. 不改 `image / pdf / text / model / other` 五条既有分支与 `window.CadFilePreview` 契约。

## 4. 验收

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_drawing_preview_ownership_note_source_red -v
#   实现前：A 组（纯函数不存在）与 B 组（没留档 / 没喂）红；C 组护栏绿
#   实现后：全绿

# 不回归（点名）
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_task_file_dwg_opens_the_whole_plan_red
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_tech_file_preview_in_card_and_auth_red
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_parts_and_cad_plan_view_red
```

## 5. 落地状态（2026-09-23，Codex 实现；本批 changelog 条目 `## 489`）

只动 `app.js` **一个文件**（一个纯函数 + 一个模块级变量 + 两处调用）；后端、接口与几何 / 成本 / 工艺口径一个字未动。

| 契约 | 落点 | 实测 |
| --- | --- | --- |
| C1 纯函数口径 | `app.js` 新增 `requirementSourceFilename(projectBody)`：`projectBody.meta.source_filename` 必须是字符串，`trim()` 后返回；其余（缺 / 非对象 / 非字符串 / 空串）一律 `""`，不抛异常 | A1–A4 全绿 |
| C2 打开项目留档 | `openProject()` 拿到 `GET /api/projects/{pid}` 的响应后写模块级 `let currentRequirementSourceFilename = requirementSourceFilename(data);`（读不到就是 `""`，覆盖上一份项目的残留） | B1 / B2 全绿 |
| C3 预览真的喂进去 | `openFilePreview()` 的 drawing 分支：`payloadName`（`payload.source_filename` / `payload.source.filename` / `payload.source.source_filename`）优先，否则退到留档值，再交给既有 `fileDrawingOwnershipNote(file, sourceName)` | B3 / B4 全绿 |
| C4 既有口径不变 | 同名仍 `""`、异名仍逐字「本次解析的图纸：`<name>`；这一份不是本次解析用的图纸。」；`agent-chat.js` 里没有本批两个符号；`/files` 仍 `"kind": "image"`、`main.py` 里没有本批符号 | C1–C5 全绿 |

复跑口径（本机 `./open-claude/.venv/bin/python -W ignore -m unittest`）：

- 本批红测 `tests.test_packaging_drawing_preview_ownership_note_source_red` → `Ran 13 tests … OK`；
  红基（实现前）`Ran 13 … FAILED (failures=7)`：A1–A4、B1–B3（6 条绿护栏：B4、C1–C5）。
- 点名保护网（本批 + `## 448` 的 `test_packaging_parts_entry_readback_red` + `…-dwg-opens-the-whole-plan` +
  `tech-file-preview-in-card-and-auth` + `business-parts-and-cad-plan-view` +
  `2-1-right-pane-single-part-figure` + `2-1-result-parts-and-shape-only` + `parts-panel`）→ `Ran 151 tests … OK`。
- `node --check tech_app/frontend/app.js` 通过；`git diff --check` 干净。
- 全量（397 个模块）→ `Ran 6644 … FAILED (failures=2, skipped=28)`：两条失败都是既有
  `test_cpq_eval_ci_contract` 的环境 / 待裁决项（与 `## 487` 那次基线里的 2 条同一条），本批**零新增失败**。

### 5.1 反向对照（删掉一处目标改动 ⇒ 只让对应那几条红；跑完还原 + `md5` 核对）

| 反向改动 | 实测 | 还原核对 |
| --- | --- | --- |
| drawing 分支去掉 `\|\| currentRequirementSourceFilename`（只认响应里的名字） | `Ran 13 … FAILED (failures=1)`：B3 | `app.js` `md5 94b61248e4a2736bc0e3294c8f96c677` 一致 |
| `openProject()` 去掉留档那一行 | `Ran 13 … FAILED (failures=1)`：B1 | 同上一致 |
| `requirementSourceFilename()` 改成 `String(meta.source_filename \|\| "")`（不 trim、不判类型） | `Ran 13 … FAILED (failures=2)`：A2 / A3 | 同上一致 |

### 5.2 与 `## 448` 那条 vm 桩红测的关系（全量跑出来的，必须记）

`tests/test_packaging_parts_entry_readback_red.py`（`## 448`）会把 `openProject()` **单独**放进一个 `vm` 桩上下文
执行，桩里只提供它自己要用到的那几个函数。本批在 `openProject()` 里新增了一次取值：若直接写
`requirementSourceFilename(data)`，在桩里就是 `ReferenceError` ⇒ 那条红测的 A1–A3 立刻转红
（本批第一次全量跑就是这么红的：`Ran 6644 … FAILED (failures=5)`，多出来的 3 条全在它那儿）。
所以取值写成：

```js
currentRequirementSourceFilename = (typeof requirementSourceFilename === "function")
  ? requirementSourceFilename(data)
  : String((data && data.meta && data.meta.source_filename) || "").trim();
```

—— **注入 + 同值兜底**，与本批 / 既有代码同一套口径（`typeof esc === "function"`、
`typeof fileIsDrawing === "function"`）。桩里退回直读 `meta.source_filename`，行为一致；两条红测与既有那条都全绿。

## 6. 与既有 Spec 的关系

- `packaging-task-file-dwg-opens-the-whole-plan.md` §C5（归属说明怎么判）**一个字不改**：本批只把喂给它的
  那个名字变成真的；该 Spec 的红测 `…-dwg-opens-the-whole-plan_red.py` 仍全绿。
- 预览仍只有一份实现（`window.CadFilePreview.open()`），卡片与 2.1 悬浮小窗共用。

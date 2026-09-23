# Spec：任务文件里点 DWG，看到的就应该是 2.1 未选中态那张「整张平面图」

状态：Spec + 红测（已实现）（2026-09-23 由本批落地：`app.js` 新增纯函数 `fileIsDrawing` / `filePreviewKind` 的 `drawing` 分支 / `fileDrawingPreviewHtml` / `fileDrawingOwnershipNote`，`openFilePreview()` 的 `drawing` 分支复用 `/requirement/packaging-geometry` 那张整图；本机实测 `Ran 20 … OK`，本批 changelog 条目 `## 487`）—— 给文件预览加了一条
`drawing` 分支：复用 2.1 那张整张平面图的既有渲染与数据源，`.dwg/.dxf` 不再落位图分支；
落地表与实测见 §5，反向对照见 §5.1，一处测试侧管道事实见 §5.2；根因与实测见 §1）
红测：`tests/test_packaging_task_file_dwg_opens_the_whole_plan_red.py`
血缘：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（§6 整张 CAD 平面图的口径）、
`docs/specs/packaging-cad-plan-drawing-coordinates.md`（画图坐标系与取框）、
`docs/specs/packaging-2-1-right-pane-single-part-figure.md`（未选中态 = 整张图；选中态 = 只这一件）、
`docs/specs/tech-file-preview-in-card-and-auth.md`（卡片内预览、同源带票 fetch、四类分支）。
本批 changelog 条目号：`## 487`（落地时序号，2026-09-23 Codex 实现）。

## 0. 用户原话（2026-09-23）

> 在这个 任务文件 / × 输入资料 2 / 酒盒.dwg / 需求说明_Codex只点按钮版.txt 里面
> 显示那个「未选中时仍是整张平面图」的那个平面图

即：在任务文件卡片里点 `酒盒.dwg`，预览区要显示**2.1 右上那块在没选中任何零件时的那张整张平面图**。

## 1. 实测证据（HEAD `89f0bc0` 工作副本只读）

| 读数 | 实测 |
| --- | --- |
| 文件清单从哪来 | `GET /api/projects/{pid}/files`（`main.py:1516`）；需求原图走 `{"name": meta["source_filename"], "kind": "image", "url": "…/source"}`（`main.py:1533-1535`）—— **`.dwg` 也被标成 `kind:"image"`** |
| 点 `酒盒.dwg` 现在发生什么 | `openFilePreview()`（`app.js:6374`）先 `fetch(file.url)` 拿 blob → `filePreviewKind()`（`app.js:6344-6351`）**先看 `file.kind === "image"`** ⇒ 直接进 image 分支 `URL.createObjectURL(blob)` + `<img>`（`app.js:6420-6427`）⇒ 一张碎图/空白（DXF 字节不是位图） |
| 就算进得了 `other` 分支 | 也只会是「该类型暂不支持预览。」—— 总之看不到图 |
| 那张「整张平面图」在哪 | 右栏未选中态那张：数据 `GET /api/projects/{pid}/requirement/packaging-geometry`（`main.py:7198`、`app.js:1943-1945`），画法 `renderPackagingCadScene()`（`app.js:2180-2220`）+ 逐图元 `packagingCadSceneEntitySvg()`（`app.js:2140-2177`，`stroke` 取 `PACKAGING_CAD_LAYER_COLORS[role]`）+ `packagingCadPlanRange()/packagingCadPlanViewBox()`（`app.js:1948-1961`、`2021-2025`） |
| 两处入口 | 看板「任务文件」卡片（`renderBoardFiles()`，`app.js:6495`）与 2.1 悬浮小窗（`agent-chat.js::fileRow`）**共用** `window.CadFilePreview.open()` ⇒ 这条分支一次改，两处都对 |

## 2. 目标行为

### C1 `.dwg / .dxf` 是「图纸」这一类，不再当位图

新增纯函数 `fileIsDrawing(file)`：只看**文件名后缀**（`.dwg` / `.dxf`，大小写不敏感），
**不看** `file.kind`。`filePreviewKind()` 必须在 image 判定**之前**判它，命中回 `"drawing"` ——
后端把需求原图标成 `kind:"image"` 也不许再把 DWG 当位图（这是现在这张碎图的直接原因）。

### C2 预览里画的就是那张整张平面图（复用，不复制）

新增纯函数 `fileDrawingPreviewHtml(doc)`：入参是既有 `/requirement/packaging-geometry` 的响应
（`cad_scene` + `layer_visibility`），逐图元复用 `packagingCadSceneEntitySvg()` 画出整张图，
`viewBox` 由 `packagingCadPlanRange()` + `packagingCadPlanViewBox()` 产出，颜色仍取
`PACKAGING_CAD_LAYER_COLORS[role]`。

- 与 2.1 未选中态**同一份数据、同一套画法**：不新开接口、不在浏览器端解析 DWG、不复制第二套渲染；
- 预览里**不出现**选中态的东西：没有 `is-highlighted`、没有 `is-dimmed`（未选中就是整张图）；
- 场景为空 / 没有 `cad_scene` / 一条图元都没有 → 回空串，由调用方给文案（C4）。

### C3 drawing 分支不许再走位图/PDF/文本那几条路

`openFilePreview()` 的 `drawing` 分支里：不 `URL.createObjectURL()`、不建 `<img>`、不建 `<iframe>`；
`.dwg / .dxf` 不再落「该类型暂不支持预览。」。

### C4 还没有解析结果时说清下一步，不给空白

这份图纸还没跑过解析（`packaging-geometry` 还没有场景）或这一次读不到 → 预览区逐字给：

```
这份图纸还没有解析结果，请先到 2.1 跑一次图纸解析。
```

并给一个去 2.1 的出口（沿用既有跳转口径）。**不许**留白、不许给碎图、不许假装画出来了。

### C5 归属说清是哪一张

图永远是**本项目这次解析的那一张**。纯函数 `fileDrawingOwnershipNote(file, sourceName)`
决定要不要补一句：

| 情形 | 输出 |
| --- | --- |
| 点的那份就是本次解析的图纸（同名） | `""`（不啰嗦） |
| 点的是别的 DWG/DXF（附件里的第二份） | 说清「本次解析的图纸：`<sourceName>`；这一份不是本次解析用的图纸。」 |

### C6 两处入口一致

卡片与 2.1 悬浮小窗共用同一份预览实现：`agent-chat.js` 里**不许**出现 `fileDrawingPreviewHtml`
或 `packagingCadSceneEndpoint`（那条纪律与既有「预览逻辑只能在 app.js 里有一份」同一条）。

### C7 不回归

`image / pdf / text / model` 四类分支与「该类型暂不支持预览。」「不在卡片内预览。」原样；
`window.CadFilePreview = { open, close }` 契约不变；预览仍走同源带票 `fetch(file.url)`
（不发裸链接、不把 token 拼进 URL）。

## 3. 本批明确不做（边界）

1. 预览里**不做**拖拽/缩放（那是 2.1 右栏那块图的事）；预览只把整张图按比例铺满。
2. 不在预览里做「选中一件看它自己的图」——那需要一块比卡片更大的工作区，属于 2.1。
3. 不改 `/files` 接口的返回口径（后端不动）；不改 CAD IR；不改 DWG 转换/解析链路。
4. 不改预览的下载按钮与鉴权口径。

## 4. 验收

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_task_file_dwg_opens_the_whole_plan_red -v
#   实现前：A / B 组红（函数不存在 / drawing 分支不存在）；C 组护栏绿
#   实现后：全绿

# 不回归（点名）
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_tech_file_preview_in_card_and_auth_red
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_business_parts_and_cad_plan_view_red
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_2_1_right_pane_single_part_figure_red
```

## 5. 落地状态（2026-09-23，Codex 实现；本批 changelog 条目 `## 487`）

只动 `app.js`（新增 4 个纯函数 + 预览加 `drawing` 分支）与两处样式；后端 / 接口口径一个字未动
（`/files` 仍把需求原图标成 `kind:"image"` —— 前端自己按后缀判，Spec 边界 3）。

| 契约 | 落点 | 实测 |
| --- | --- | --- |
| C1 图纸分类 | `app.js` 新增 `fileIsDrawing(file)`（`/(^|[^A-Za-z0-9])(dwg|dxf)$/i`：只看文件名后缀、不看 `kind`，`P01 面板 · DXF` 这种没点的写法也命中）；`filePreviewKind()` 在 image 判定**之前**先判它、命中回 `"drawing"` | A1–A4 全绿 |
| C2 复用那张整图 | 新增 `fileDrawingPreviewHtml(doc)`：逐图元走 `packagingCadSceneEntitySvg()`（node 单函数抽跑走同值兜底），取框与 `viewBox` 用**同一套**口径，`stroke` 取 `PACKAGING_CAD_LAYER_COLORS[role]`；输出**不带** `is-highlighted` / `is-dimmed` | A5–A8 全绿 |
| C3 不落位图分支 | `openFilePreview()` 的 `drawing` 分支**不**建 object URL、**不**建 `<img>` / `<iframe>`；`URL.createObjectURL` 挪进 `if (kind === "image" \|\| kind === "pdf")`（全文件只剩 1 处） | B1 全绿 + C2 护栏（既有四类分支与 `该类型暂不支持预览。` 原样） |
| C4 说清下一步 | 图来自既有 `packagingCadSceneEndpoint()`（`GET /api/projects/{pid}/requirement/packaging-geometry`）；场景为空时逐字给 `这份图纸还没有解析结果，请先到 2.1 跑一次图纸解析。` + 「去 2.1 跑图纸解析」出口（不留白、不给碎图） | B2 / B3 全绿 |
| C5 归属说清 | 新增 `fileDrawingOwnershipNote(file, sourceName)`：同名回 `""`；不同名写「本次解析的图纸：`<sourceName>`；这一份不是本次解析用的图纸。」 | A9 / B4 全绿 |
| C6 两处入口一份实现 | 卡片与 2.1 悬浮小窗共用 `window.CadFilePreview.open()`；`agent-chat.js` 里不出现 `fileDrawingPreviewHtml` / `packagingCadSceneEndpoint` | B5 / B6 全绿 |

复跑口径（本机 `./open-claude/.venv/bin/python -W ignore -m unittest`）：

- 本批红测 `tests.test_packaging_task_file_dwg_opens_the_whole_plan_red` → `Ran 20 tests … OK`；
  红基（`app.js` 退回 HEAD）`Ran 20 … FAILED (failures=14)`：A1–A10、B1–B4（6 条绿护栏：B5 / B6 / C1–C4）。
- 本批两条新红测一起跑 → `Ran 51 … OK`；红基 `Ran 51 … FAILED (failures=34)`。
- 点名保护网（本 Spec §4 三条 + 另一条新红测 + 既有五条）→ `Ran 195 tests … OK`。
- 全量（396 个模块）→ `Ran 6631 tests … FAILED (failures=2, skipped=28)`：两条失败都是既有
  `test_cpq_eval_ci_contract` 的环境/待裁决项，本批**零新增失败**。
- `node --check tech_app/frontend/app.js` 通过；`git diff --check` 干净。

### 5.1 反向对照（删掉目标改动 ⇒ 只让对应那几条红；跑完还原 + `md5` 核对）

| 反向改动 | 实测 | 还原核对 |
| --- | --- | --- |
| `filePreviewKind()` 删掉 `if (isDrawing) return "drawing";` | `Ran 20 … FAILED (failures=2)`：A1（`.dwg` 又被 `kind:"image"` 带走、回位图分支）/ A2 | `app.js` `md5 0b0b2988b57d7ed10c69b2d7f2177593` 一致 |

### 5.2 一处测试侧管道事实（只记录，不改断言、不放宽期望值）

本 Spec 的红测想把 `IMAGE_FILE_PATTERN` / `TEXT_FILE_PATTERN` 用 `const` 写进 `EXTRACT_JS` 的 `prelude`
"注入"给抽出来的单函数；但 `eval()` 里的 `const` **不外泄**到外层作用域（只有 `var` / 函数声明会），
那两条注入实际不可见 —— 红测本身作为"按后缀分类"的判据仍然成立（它跑的是抽出来的单函数）。
因此实现侧把这两条**同值**判据按既有依赖注入口径写进 `filePreviewKind()` 自身
（`typeof X !== "undefined"` → 用模块级真源，否则用**同一个字面量**），红测期望值与断言一个字未动。

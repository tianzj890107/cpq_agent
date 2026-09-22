# 规格：清单里"有部件图"的那一件，字节取不到时页面只剩一个碎图 —— "取不到"不许与"没有配图"同形

状态：Spec + 红测（已实现）（原状：`tech_app/frontend/app.js:2345-2363 openPackagingBusinessPart()`
按清单里的 `thumb.available` 直接吐 `<img class="packaging-business-thumb" src=…>`，
**没有 `onerror`**；读端点 `GET …/packaging-business-parts/{code}/thumbnail`（`main.py:7393-7408`）
在字节被清理时回 404 `thumbnail_bytes_missing`、接口挂了回 500 —— 两条都只让浏览器画一个碎图，
面板照旧写着"已配到部件图"；同时清单侧的三态里 **"其它" 一律写成
"部件图还没入库（重新导入权威清单即可）"**，把"字节被清理"这种情形也赖到"没导入"头上）
红测：`tests/test_packaging_authority_thumbnail_bytes_read_failure_red.py`
行号基线：HEAD `7e81fd7`（行号只用来指路；口径以本 Spec 正文为准，不以行号为准）。

血缘：**supersede** `packaging-authority-thumbnail-media.md` §C6 的
"其它 → 「部件图还没入库（重新导入权威清单即可）」"这半句（宽泛兜底 = 未知码也被断言成"没导入"；
本批换成闭合表 + 未知码照实暴露，其余口径逐字不变）；
承接 `packaging-silent-degradation-disclosure.md`（失败 / 空 / 没有不许同形：这里要分成
**没有配图** / **字节没入库** / **这一次取不到字节** 三件事）、
`packaging-authority-thumbnail-media.md` §C4（`img.available` 只说"清单里有引用"，**不是**
"这一次取得到字节"）、
`packaging-business-parts-read-failure-note.md`（同一天的姊妹批：清单读不到 ≠ 没有清单）。
本批只写 Spec + 红测；业务实现交给实现方（AGENTS.md）。

## 0. 一句话目标

`thumbnail.available === true` 只说明**清单里这一件配了图**。图上真正显示不出来时，用户必须在
这一件的位置看到一句说清的话，且能分辨三种情形：**这一件没有配图**（去核资料）、
**有引用但字节还没入库**（重新导入可建）、**引用与字节都在，这一次没取到**（刷新/重试 ——
不是"没导入"）。

## 1. 现状缺口（代码级，逐条可指到行；本批不依赖任何真跑）

`tech_app/frontend/app.js`：

```js
2345   const thumbnailHost = $("packagingPartThumbnail");
2346   if (thumbnailHost) {
2347     const thumb = (row.thumbnail && typeof row.thumbnail === "object") ? row.thumbnail : {};
2348     if (thumb.available) {
2351       thumbnailHost.innerHTML = `<img class="packaging-business-thumb"`
2352         + ` src="${esc(mediaUrl(packagingBusinessPartThumbnailUrl(currentProject, wanted)))}"`
2353         + ` alt="${esc(alt + " 的部件图")}">`;        // ← 没有 onerror：取不到就只剩碎图
2354       thumbnailHost.hidden = false;
2355     } else {
2356       const reason = String(thumb.reason || "");
2357       const copy = reason === "image_bytes_unreadable"
2358         ? "工作簿里的部件图读不出来（导入时就没读到字节）"
2359         : (reason === "thumbnail_missing"
2360           ? "这份清单里这一件没有配到部件图"
2361           : "部件图还没入库（重新导入权威清单即可）");   // ← 未知码全被断言成"没导入"
```

`tech_app/backend/main.py`（服务端这一侧是**对**的，本批不动）：

```python
7382 PACKAGING_THUMBNAIL_REASON_COPY = {
7383     "business_parts_missing": …, "business_part_not_found": …,
7385     "thumbnail_missing": "这份权威清单里这一件没有配到部件图",
7386     "image_bytes_unreadable": "工作簿里的部件图读不出来（导入时就没读到字节）",
7387     "thumbnail_not_saved": "这件有部件图引用，但字节还没入库：重新导入一次权威清单即可",
7388     "thumbnail_bytes_missing": "部件图字节在存储里找不到了（可能被清理过）",
7389 }
```

三个后果：

1. **取不到字节 = 零披露**：`<img>` 拿不到字节时浏览器只画碎图，`onerror` 没人接；
   面板上其它字段照旧写"已配到部件图（按顺序推定）"，两句话自相矛盾。
   真样本 28 件全部 `available: true`（`## 376` 实测 197475 字节），所以这条路径随时会被走到：
   blob 被清理、S3 抖一下、接口 500 —— 都属于这一类。
2. **未知 reason 被改写成"没导入"**：清单侧的兜底把任何非空 reason 都写成
   "部件图还没入库（重新导入权威清单即可）"。`thumbnail_bytes_missing`（字节在存储里没了）
   落进这条兜底就变成"你还没导入"，而导入**不是**这件事的下一步（引用与字节本来都在）。
3. **前端自己抄了一份文案**：`app.js:2357-2361` 与 `main.py:7383-7389` 是两份互不相干的
   映射，服务端加了码、前端不会跟着说；文案必须收成一个具名纯函数，未知码照实暴露。

## 2. 允许修改范围（实现方）

1. `tech_app/frontend/app.js`：**新增纯函数** `packagingBusinessThumbnailReasonText(reason)`
   （不得出现 `document.` / `window.` / `fetch(` / `localStorage`），**闭合表**逐字照抄服务端
   `PACKAGING_THUMBNAIL_REASON_COPY` 里件级会出现的三个码：
   - `"image_bytes_unreadable"` → `"工作簿里的部件图读不出来（导入时就没读到字节）"`
   - `"thumbnail_missing"` → `"这份清单里这一件没有配到部件图"`
   - `"thumbnail_not_saved"` → `"这件有部件图引用，但字节还没入库：重新导入一次权威清单即可"`
   - 其它**非空**码 → `"部件图读不到（<码>）"` —— **不许**再断言"没入库 / 重新导入即可"；
   - 空串 / `null` / 缺失 → `""`
2. `tech_app/frontend/app.js`：**新增纯函数** `packagingBusinessThumbnailBytesFailureText()`
   （无参，同样不许碰 DOM / fetch），逐字返回：
   `"这一件清单里有部件图，但这一次没取到字节（可能已被清理，也可能是接口暂时读不到）；刷新或重新导入权威清单可重建"`
3. `tech_app/frontend/app.js:openPackagingBusinessPart()`
   - 清单侧文案改成 `packagingBusinessThumbnailReasonText(String(thumb.reason || ""))`
     （**删掉** `:2357-2361` 的三元表达式与那句宽泛兜底）；
   - `<img>` 增加 `onerror`：把 `#packagingPartThumbnail` 换成
     `class="packaging-part-note"` + `data-qqThumbBytesUnavailable="1"` 的提示块，
     文本 = `packagingBusinessThumbnailBytesFailureText()`（**不是**"没有配图"、**不是**"还没入库"）；
     `onerror` 只换这一块的内容，不许动页面其它部分；
   - 显示条件仍是 `thumb.available`（**不许**改成按字节判断、不许改清单侧 `bound_total` 口径）；
   - 可用时 `<img>` 的 `class` / `src`（走 `mediaUrl(packagingBusinessPartThumbnailUrl(...))`）/
     `alt` 逐字不变。

## 3. 禁止事项

- 不许改服务端 `PACKAGING_THUMBNAIL_REASON_COPY` / 缩略图端点（它的 404 + reason 是对的口径）。
- 不许把 `thumb.available` 当成"这一次取得到字节"；不许因为取不到字节就把行上的
  `thumbnail_ref` / `bound_total` / "已配到"那几行改掉（清单事实不变）。
- 不许把"这一次取不到字节"写成"这一件没有配图" / "还没导入权威清单" / "没有部件图"。
- 不许在 `onerror` 里重试循环、不许自动重新导入、不许弹窗、不许改 `hidden` 语义之外的行为。
- 不许改 `renderPackagingPartPanel()` 收起缩略图那句、不许改 `pkgPartFactRow()` /
  `packagingAuthorityDisclosureLines()` 的既有口径、不许改 `packaging-business-parts` 读接口。
- 不许改 `tests/` 下任何既有文件（含 `packaging-authority-thumbnail-media.md` 点名的
  `tests/test_packaging_authority_thumbnail_media_red.py`）；本批红测是新增文件。
- 不许起服务、不许发 HTTP、不许连线上 PG / SQLite、不许写业务数据；本批红测全部离线
  （`node -e` 抽函数体执行 + 源码守卫）。
- 不许 commit / push / tag / Release / 部署。

## 4. 验收标准

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_authority_thumbnail_bytes_read_failure_red -v
# T 组（8 条）：
#   T1 纯函数：image_bytes_unreadable → "工作簿里的部件图读不出来（导入时就没读到字节）"（红）
#   T2 纯函数：thumbnail_missing → "这份清单里这一件没有配到部件图"（红）
#   T3 纯函数：thumbnail_not_saved → "这件有部件图引用，但字节还没入库：重新导入一次权威清单即可"（红）
#   T4 纯函数：thumbnail_bytes_missing（表外码）→ "部件图读不到（thumbnail_bytes_missing）"
#      且不含"重新导入/没入库"（红）
#   T5 纯函数：空串 / null → ""（红）
#   T6 纯函数守卫：两个新函数都不碰 DOM / fetch / localStorage（红）
#   T7 源码守卫：openPackagingBusinessPart() 用纯函数渲染清单侧文案、<img> 带 onerror、
#      onerror 提到 qqThumbBytesUnavailable 与字节失败文案（红）
#   T8 护栏：两条既有件级文案（`image_bytes_unreadable` / `thumbnail_missing`）+
#      `<img class="packaging-business-thumb"` + `mediaUrl(packagingBusinessPartThumbnailUrl(…)`
#      + `packagingPartThumbnail` 容器 id 逐字仍在（绿）
# 现状：T1–T7 红（7 条），T8 绿（1 条护栏）
# 不回归（部件图本体与权威清单披露两批）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_authority_thumbnail_media_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_authority_disclosure_on_read_red
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_read_failure_note_red
node --check tech_app/frontend/app.js
```

真机复验（实现方做完、且部署后）：

```
# 打开 2.1 → 点一件「部件图 已配到」的业务部件：
# 1) blob 正常 → 图照旧显示，页面里没有 data-qqThumbBytesUnavailable；
# 2) 把该件的 blob 字节清掉（或让端点 500）→ 缩略图位置当场出现
#    "这一件清单里有部件图，但这一次没取到字节（可能已被清理，也可能是接口暂时读不到）；
#     刷新或重新导入权威清单可重建"，行上的"部件图 已配到（…）"一字不变；
# 3) 把字节恢复 → 刷新后图回来，提示消失。
```

## 5. 落地状态（2026-09-22，Codex 实现）

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_authority_thumbnail_bytes_read_failure_red
# 实现前：Ran 8 tests … FAILED (failures=7)   ← T1 T2 T3 T4 T5 T6 T7
# 实现后：Ran 8 tests … OK                    ← T8 一条护栏始终绿
```

| 契约 | 落点（`tech_app/frontend/app.js`） |
| --- | --- |
| §2.1 清单侧纯函数 | 新增 `packagingBusinessThumbnailReasonText(reason)`（`openPackagingBusinessPart()` 上方）：闭合表逐字照抄服务端 `PACKAGING_THUMBNAIL_REASON_COPY` 的件级三码（`image_bytes_unreadable` / `thumbnail_missing` / `thumbnail_not_saved`，文案逐字）；**表外非空码** → `部件图读不到（<码>）`（不再断言"没入库 / 重新导入即可"）；空串 / `null` / 纯空白 → `""`。无 `document.` / `window.` / `fetch(` / `localStorage`（T6）。 |
| §2.2 取不到字节纯函数 | 新增 `packagingBusinessThumbnailBytesFailureText()`（无参）：逐字返回 `这一件清单里有部件图，但这一次没取到字节（可能已被清理，也可能是接口暂时读不到）；刷新或重新导入权威清单可重建`（T6 断言、且不含"没有配图 / 还没入库 / 没有部件图"）。 |
| §2.3 面板接线 | `openPackagingBusinessPart()`：`thumb.available` 分支里 `<img class="packaging-business-thumb" src=mediaUrl(packagingBusinessPartThumbnailUrl(…)) alt=…>` 逐字不变（T8），新增 `img.onerror` —— 只把 `#packagingPartThumbnail` 这一块换成 `div.packaging-part-note[data-qqThumbBytesUnavailable="1"]`，文本 = `packagingBusinessThumbnailBytesFailureText()`；`else` 分支删掉内联三元式与宽泛兜底，改为 `packagingBusinessThumbnailReasonText(String(thumb.reason || ""))`。显示条件仍是 `thumb.available`。 |
| §3 未动的 | 未改服务端 `PACKAGING_THUMBNAIL_REASON_COPY` / 缩略图端点；未把 `thumb.available` 当成"取得到字节"；未改行上 `thumbnail_ref` / `bound_total` / "已配到"；未改 `renderPackagingPartPanel()` 收起缩略图那句 / `pkgPartFactRow()` / `packagingAuthorityDisclosureLines()` / `packaging-business-parts` 读接口；`onerror` 不重试 / 不自动重新导入 / 不弹窗。 |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_authority_thumbnail_media_red \
  tests.test_packaging_authority_disclosure_on_read_red \
  tests.test_packaging_business_parts_read_failure_note_red   → Ran 59 … OK
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_part_panel_evidence_red \
  tests.test_packaging_business_part_plan_click_and_bound_outline_red \
  tests.test_packaging_business_parts_and_cad_plan_view_red   → Ran 44 … OK
node --check tech_app/frontend/app.js   → OK
```

未改任何既有测试与业务数据、未放宽任何断言、未连 PG / SQLite、未起服务、未发 HTTP、
未 push / MR / tag / Release / 未部署。

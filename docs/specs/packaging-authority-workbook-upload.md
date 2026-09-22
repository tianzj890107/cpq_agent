# 规格：客户工作簿**从上机界面直接导入**，并如实记下出处

依赖：`docs/specs/packaging-business-parts-and-cad-plan-view.md`（§12 边界 1 明写"导入的是
**服务器可见路径**上的工作簿 —— 客户资料不入库，部署机上要先把工作簿放上去"；本批就是那条边界的
第一半）、`docs/specs/packaging-authority-disclosure-on-read.md`（清单出处的披露口径）、
`docs/specs/packaging-authority-thumbnail-media.md`（同一条路的图片字节）。

状态：Spec + 红测（已实现）（原状：接口那半**已经收** `content_base64`
（`main.py:7208` / `:7275-7281`，`:7279` 那句"请给出权威清单工作簿（workbook_path 或 content_base64）"），
但有两处断点 ——
① 前端**只给一条路**：`app.js:2337-2352` 的 `importPackagingBusinessParts()` 只会
`window.prompt("权威清单工作簿在服务器上的路径（.xlsx）")` 然后发 `{workbook_path: path}`，
`content_base64` / `file_name` 在前端源码里 **0 处**；客户给的 xlsx 只能先被人手工放上服务器；
② 走字节这条路时**出处丢失**：`packaging_part_authority.import_workbook()`（`:313-314`）
对字节来源把 `source["file"]` 写成空串，页面上"这份清单来自哪个文件"只能是空白，
既没说清文件名、也没有字节数）
红测：`tests/test_packaging_authority_workbook_upload_red.py`
行号基线：HEAD `05da09f`

## 0. 一句话目标

客户把 xlsx 交到工艺经理手上 → 工艺经理在页面上**选中这个文件**就能导入（字节走同一条
`import` 接口、同一套确定性解析）；导完的清单说得出"来自哪个文件、多大、指纹多少"。

## 1. 现状缺口（代码级）

1. `app.js` 只有 `{workbook_path: path}` 一条路：没有 `<input type="file">`、没有 `FileReader`、
   没有 `content_base64` / `file_name` 字面量 —— 客户资料必须先落到服务器文件系统；
2. `main.py:7275-7281` 收下 `content_base64` 后直接 `b64decode`：**没有大小上限**
   （一个入口把任意大小的工作簿读进内存），也**没有把文件名带下去**；
3. `packaging_part_authority.import_workbook()` 对字节来源 `source["file"] = ""`
   （`:314`）→ 落库的 `source` 里没有文件名；字节数（`file_bytes`）这一列从来没有过。

## 2. 契约

### C1 出处：文件名与字节数如实落库（`packaging_part_authority.import_workbook`）

- 签名扩展为 `import_workbook(source, *, sheet=None, file_name="")`；
- **只有**字节来源看 `file_name`：给了就净化为文件名（见 C3 的 `packagingAuthorityFileName`
  同一条规则：取最后一段、去控制字符、trim）写进 `source["file"]`；没给仍是 `""`；
- **路径来源的 `source["file"]` 语义一字不变**（仍取 `os.path.basename`）—— 服务器路径那条路
  不许被 `file_name` 改名（防冒名）；
- 两个返回分支（**成功** 与 `authority_sheet_missing`）都新增 `source["file_bytes"]`：
  字节来源 = `len(bytes)`；路径来源 = 文件真实大小；
- 既有键（`file` / `sheet` / `file_hash` / `code_prefix` / `header_row` / `data_row_*`）与解析
  规则（表头定位、连续序号、跳过原因、编码前缀）**一个字不动**。

### C2 上传入口的上限与透传（`main.py`）

- `PackagingBusinessPartsImportAction` 增 `file_name: str = ""`；
- 大小上限用既有 `MAX_UPLOAD_BYTES`（不新造常量）：**先**按 base64 文本粗判
  （长度 > `MAX_UPLOAD_BYTES * 4 // 3 + 16` 直接拒），**再**解码后精判（`len(data) > MAX_UPLOAD_BYTES`）；
  两条都给 **413** 且文案含"上限"与实际大小（MB，一位小数）；超限时**不调用** `import_workbook`；
- 透传规则与 C1 对齐：`file_name` **只在字节那一路**传下去（路径那一路传 `""`）；
- 既有的三条拒绝**不变**：既没路径也没字节 → 400（那句"请给出权威清单工作簿（workbook_path
  或 content_base64）"逐字保留）；base64 解不开 → 400；解析失败 → 409。

### C3 前端三条路（`app.js`）

- 三个顶层纯函数（体内无 DOM / `fetch(` / `localStorage`，可被 `node -e` 抽出来真跑）：
  - `packagingAuthorityFileName(name)`：取最后一段（`/` 与 `\` 都算分隔符）、去掉控制字符与
    首尾空白；非字符串 / 空 → `""`；
  - `packagingAuthorityBase64Of(dataUrl)`：`data:*;base64,XXXX` → `XXXX`（去掉其中的换行与空白）；
    不是这种 data URL（含空串、`data:text/plain,abc`）→ `""`；
  - `packagingAuthorityImportBody(path, fileName, dataUrl)`（**复用**上面两个，不把规则抄第二遍 ——
    抽它出来真跑时要连那两个一起抽）：`path` 非空 → `{workbook_path: path}`
    （**服务器路径优先、逐字不变**）；否则 base64 非空 → `{content_base64, file_name}`；
    两者都没有 → `null`（不猜、不编空 base64）。
- 面板的导入按钮旁边新增「选择客户工作簿…」（`data-qq-business-upload`），触发一个隐藏的
  `<input type="file" accept=".xlsx,.xlsm">`；选中后用 `FileReader.readAsDataURL()` 读出 →
  `packagingAuthorityImportBody("", file.name, dataUrl)` → 与路径导入**共用同一个** POST 函数
  （`packagingBusinessPartsImportPath()`，路径字面量仍只在源码里出现一次）；
- 导入成功后沿用既有的重渲染（`currentPackagingBusinessParts` / `renderTree` / `loadPackagingCadPlan`）；
  失败按后端 `message` 提示，**不**猜原因、**不**把失败说成成功；
- 路径导入入口**保留**（部署机上仍可能直接用服务器路径）。

### C4 冻结面

- `packaging-business-parts/` 在 `app.js` 里的出现次数**仍是 4**（不许因为新增入口多一处，
  红测会数）；
- `import_workbook` 的解析规则、`bind_geometry` 默认、导入后的下游（BOM / 工艺 / 成本）口径不变；
  不改 `index.html`、不加依赖、不调模型、不联网；
- 不改 `tests/` 下任何文件。

## 3. 允许修改范围

1. `tech_app/backend/services/packaging_part_authority.py`：`import_workbook` 的 `file_name` 与
   `file_bytes`（两个返回分支）；
2. `tech_app/backend/main.py`：`PackagingBusinessPartsImportAction.file_name` + 两道大小上限 +
   透传；
3. `tech_app/frontend/app.js`：三个纯函数 + 文件选择入口 + 共用 POST；
4. 本 Spec 与它的红测；changelog 追加一条（当周文件）。

## 4. 禁止事项

- 不许把客户文件**落盘存副本**（导入是一次读、解析、落清单；文件本体不入库）。
  缩略图字节仍走既有内容寻址那条路，本批不碰；
- 不许放宽上限 / 不许"先解码再看大小"（粗判在前）；不许把 413 说成 400 或 500；
- 不许给路径来源套 `file_name`（防冒名）；不许在解析失败时留下半份清单；
- 不许让前端自己解析 xlsx（前端只搬字节）；不许新增依赖、不许联网、不许调模型；
- 不连 PG / 34、不写生产数据；不 push / MR / tag / Release / 部署。

## 5. 验收命令

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_authority_workbook_upload_red -v
node --check tech_app/frontend/app.js
```

不回归：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_parts_and_cad_plan_view_red \
  tests.test_packaging_authority_thumbnail_media_red \
  tests.test_packaging_authority_disclosure_on_read_red \
  tests.test_packaging_business_part_panel_evidence_red \
  tests.test_packaging_business_part_plan_click_and_bound_outline_red
```

## 6. 已记录的边界

1. 本批只解决"客户文件怎么上来"与"出处怎么记"：**不**做附件归档、**不**做多文件合并、
   **不**做版本对比；
2. 前端不判大小（只搬字节），超限一律以后端的 413 为准 —— 一处口径，两处实现会漂；
3. `file_bytes` 是**原始工作簿**大小，不是解析后的行数；行数仍在 `stats` 里；
4. 同一份文件重复导入仍幂等（`business_parts_id` 由内容决定，既有行为）；
5. 部署脚本、34 的磁盘目录约定本批不动。

## 7. 落地状态（2026-09-22，Codex 实现）

红基（实现前，`git stash push -- tech_app/backend/services/packaging_part_authority.py
tech_app/backend/main.py tech_app/frontend/app.js` 后实跑）：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_authority_workbook_upload_red
Ran 22 tests … FAILED (failures=15, errors=3)          # 18 红 / 4 绿
```

- failures=15：`A1`–`A5`（三个纯函数都不存在）、`B2`（字节来源没有 `file_bytes`）、
  `C1`/`C2`（`file_name` 透传缺失）、`C3`/`C4`（没有上限，超限反而被当成 base64 解不开）、
  `C7`（入参没有 `file_name`）、`D1`/`D2`/`D4`/`D5`（前端没有文件入口，也没有三个纯函数）；
- errors=3：`B1`/`B3`/`B4` —— `import_workbook()` 不认 `file_name=` 这个关键字（`TypeError`）；
- 4 绿全是护栏：`C5`（没超限照样导入）、`C6`（既有 400 两条逐字不变）、
  `D3`（`packaging-business-parts/` 计数仍是 4）、`D6`（`node --check` 通过）。

实现后：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_authority_workbook_upload_red
Ran 22 tests in 0.833s
OK
node --check tech_app/frontend/app.js        # 通过
```

| 契约 | 落点 |
| --- | --- |
| §C1 出处 | `packaging_part_authority.source_file_name()`（净化：`/` 与 `\` 都算分隔符、去控制字符、trim）+ `import_workbook(source, *, sheet=None, file_name="")`：字节来源用 `file_name` 净化后的名字，路径来源仍取 `os.path.basename`（**不看** `file_name`）；两个返回分支的 `source` 都新增 `file_bytes`（字节来源 `len(bytes)`、路径来源 `os.path.getsize`） |
| §C2 上限与透传 | `PackagingBusinessPartsImportAction.file_name` + `_workbook_too_large_detail(size, limit)`；路由先按 base64 文本粗判（`limit*4//3+16`）再按解码字节精判，两处都 413 且不调导入器；`file_name` 只在 `raw` 那一路透传 |
| §C3 前端 | `packagingAuthorityFileName()` / `packagingAuthorityBase64Of()` / `packagingAuthorityImportBody()`（纯函数）+ `packagingBusinessPartsImportPath()`（导入路径唯一字面量）+ `importPackagingBusinessPartsData()`（两条路共用的 POST）+ `importPackagingBusinessPartsFromFile(file)`（`FileReader.readAsDataURL` → 同一个 POST）；面板里导入按钮旁新增「选择客户工作簿…」与隐藏的 `<input type="file" accept=".xlsx,.xlsm" hidden>`（容器带 `data-qq-business-upload`） |
| §C4 冻结面 | `packaging-business-parts/` 在 `app.js` 里仍是 **4** 处（`D3` 数）；服务器路径入口保留（`workbook_path` 仍由 `packagingAuthorityImportBody()` 产出）；解析规则 / `bind` 默认 / 下游口径未动；未改 `index.html`、未加依赖 |

复跑（不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_business_parts_and_cad_plan_view_red tests.test_packaging_authority_thumbnail_media_red \
  tests.test_packaging_authority_disclosure_on_read_red tests.test_packaging_business_part_panel_evidence_red \
  tests.test_packaging_business_part_plan_click_and_bound_outline_red \
  tests.test_packaging_business_parts_binding_size_source_red
Ran 114 tests … OK

# 所有引用 app.js 的套件（131 个文件）：
Ran 1304 tests … OK (skipped=4)
```

边界（与 §6 一致）：客户文件**不落盘存副本**（一次读、解析、落清单；缩略图字节仍走既有内容寻址那条路）；
前端不判大小（超限一律以后端 413 为准，一处口径）；`file_bytes` 是原始工作簿大小，不是解析后的行数；
同一份文件重复导入仍幂等；未连 PG / 34、未写生产数据、未 push / MR / tag / Release / 部署。

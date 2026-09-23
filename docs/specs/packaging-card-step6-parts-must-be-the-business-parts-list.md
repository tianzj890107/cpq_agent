# 报价卡片第 6 步的「零件」必须是业务部件清单：263 个几何分量不许再整表冒充零件

血缘：`packaging-business-parts-and-cad-plan-view.md` §7（「BOM、单件工艺、单件成本、整包成本只遍历
`business_parts`；酒盒权威版本应产生 28 个业务部件，**不能产生 263/402 行任务**」——本批把这条裁定
落到**报价卡片**这一侧）、
`packaging-28-part-auto-resolution-and-2d-board-cleanup.md`（2.1 左栏只留业务部件、右栏改画整张 CAD
平面图；业务清单来源成为 2.1 的运行时事实）、
`packaging-parts-in-card-and-material-fill.md` §2.1（卡片第 6 步要「看得见零件」——本批把这里的
「零件」口径从**几何分量**改回**业务部件**）、
`packaging-card-parts-lookup-must-accept-recovered-link.md`（卡片反查技术项目的两个会话键，本批不动）。

状态：Spec + 红测（已实现）（原状是「卡片第 6 步整表渲染 `packaging-parts` 几何文档」——
34 上真实显示「图纸拆出来的零件（263 件）」8 列、`DWG-P01`…`DWG-P263`；卡片页里
`packaging-business-parts` 一个引用都没有，业务部件清单在卡片上根本读不到。本行由 `## 457`
按状态守卫 `tests.test_spec_status_truth_red` 更正：点名的红测现在
`Ran 11 … OK`，红基 6 红是 A1–A3 / B1–B2 / C1）
红测：`tests/test_packaging_card_step6_parts_must_be_the_business_parts_list_red.py`
本批 changelog 条目号：`## 457`（原写 `## 455`；该号已被同期的
`packaging-cost-stage-must-see-the-parsed-parts.md` 占用，本批按实际落地条目改号）

## 0. 一句话目标

销售在报价卡片第 6 步看到的「拆出来的零件」，必须是**业务部件清单**（有权威清单时 28 件，
件号 `business_part_code`、带几何绑定状态）；几何连通分量（263 个）只能作为**证据**，并如实说
「已识别几何区域 n 个，尚未形成业务部件清单」，不许占用零件表的位置。

## 1. 现场证据（34 只读实测 + 本机读码，2026-09-23）

报价会话 `1bef04f7dab3` → 技术项目 `8131f6d29d99`（`酒盒.dwg`），三个只读读数：

```text
GET /api/projects/8131f6d29d99/requirement/packaging-parts?limit=1
    → total=263，filtered_total=900，part_code 形如 DWG-P01…DWG-P263

GET /api/projects/8131f6d29d99/requirement/packaging-business-parts
    → built=false，business_parts=[]
      gap = {"code": "business_parts_missing",
             "message": "已识别几何区域 263 个，尚未形成业务部件清单",
             "action": "导入权威部件清单（Excel）或人工建立业务部件后，再跑 BOM / 工艺 / 成本",
             "geometry_component_total": 263}

GET /wf/card/step-data?session_id=1bef04f7dab3&step_no=6
    → 段 packaging_parts = {"kind": "table",
                            "title": "图纸拆出来的零件（263 件）",
                            "数据": [263 行，含 零件号=DWG-P01、名称=图纸零件 P01、尺寸来源=component_bbox]}
```

本机读码（HEAD `ytbz`，逐条可复现）：

| 事实 | 值 |
| --- | --- |
| `确认需求解析结果.html` 里 `/requirement/packaging-parts` 出现次数 | 4 |
| 同一文件里 `packaging-business-parts` 出现次数 | **0** |
| 同一文件里 `business_part_code` / `geometry_binding` / `几何区域` / `geometry_component_total` | 各 **0** |
| `ensureCardPackagingParts()` 读的端点 | `'/api/projects/' + pid + '/requirement/packaging-parts'`（几何口径） |
| 该函数渲染的标题 | `title: '图纸拆出来的零件'`（不带任何几何口径字样） |
| 后端 `_business_parts_body()`（`main.py:7372`） | 早就**不**回退成几何件：没有清单时 `built=false` + `business_parts_missing` 缺口文案 |

结论：**后端两端口径已经分开，缺口在卡片页这一侧。** 卡片页这条读路径比
`packaging-business-parts-and-cad-plan-view.md` §7 早（那是 2.1 看板的批），一直没有跟着改；
于是「几何分量当零件」这件事在 2.1 已经收口，在**报价卡片上还整表活着**。

## 2. 需求

### 2.1 A 组：卡片第 6 步优先按业务部件口径出表（运行时来源闭集）

1. `ensureCardPackagingParts()` 必须**先**读
   `GET /api/projects/{pid}/requirement/packaging-business-parts`；
   读几何端点只能在业务清单读不到之后发生（函数体内业务端点的位置必须早于几何端点）。
2. 命中 `built:true` 时，渲染的是 `business_parts` 行：每行必须有 `business_part_code`
   （业务件号）与 `geometry_binding.status`（`bound` / `partial` / `unbound` / `ambiguous`）；
   业务行**不许**用 `DWG-Pxx` + `尺寸来源=component_bbox` 那套几何列冒充。
3. 业务清单为空（`built:false`）时，卡片必须走下面 §2.2 的披露，**不得**把 263 个几何分量
   整表渲染成「零件表」。
4. `resolveCardTechProject()` 的反查判据（`source_session_id` / `quote_session_id` 两键同义）
   逐字不动 —— 本批不碰 ## 446 的成果。

### 2.2 B 组：没有业务清单时，说清是几何区域

1. 卡片第 6 步必须出现后端那条缺口文案（或逐字等义）：
   「已识别几何区域 %d 个，尚未形成业务部件清单」，并带上下一步动作
   （导入权威部件清单 / 人工建立业务部件）。
2. 几何口径的表如果要显示，标题/说明里必须含「几何」字样；
   `图纸拆出来的零件` 这个标题**只允许**挂在业务部件表上。

### 2.3 C 组：两笔账分开说

1. 卡片必须同时给得出 `geometry_component_total`（几何区域数）与 `business_part_total`
   （业务部件数）两个数，不许用其中一个掩盖另一个（263 件 ≠ 28 件，页面上要一眼看得出区别）。

## 3. 非目标

- 不改 `packaging_business_part_resolver` 的来源优先级（那是 `## 451` / `## 453` 的地盘）。
- 不改几何端点 `.../packaging-parts` 的列定义（`packaging_parts.CARD_COLUMNS`）与
  `processability()` 判据（`## 315` 的成果）。
- 不删历史：老卡片快照里已经存过的几何 `packaging_parts` 段重开时仍要能渲染（只追加不迁移）。
- 不动 2.1 技术看板（`app.js` / `requirement-confirm.js`）。
- 不改后端读接口——它今天就是业务口径。

## 4. 实现范围（给实现侧）

- 唯一要改的文件：`确认需求解析结果.html`，`ensureCardPackagingParts()` 及其新增的小函数
  （业务行映射 + 几何兜底披露）。后端与本仓库其它文件零改动。
- 老卡片快照里那份几何段的既有渲染路径保留；新渲染走业务口径。

## 5. 验收命令与期望

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_card_step6_parts_must_be_the_business_parts_list_red -v
    # 本批红基：见 §6；实现后必须全绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_card_parts_lookup_link_key_parity_red -v
    # 不回归（反查两键）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_in_card_and_material_fill_red -v
    # 不回归（快照未知节渲染 + 件级补材料）
```

## 6. 红基（本批实测，2026-09-23，本机 `./open-claude/.venv/bin/python`）

```text
tests.test_packaging_card_step6_parts_must_be_the_business_parts_list_red
    → Ran 11 tests … FAILED (failures=6)       # A1–A3、B1–B2、C1 红；D1–D5 是有意护栏
tests.test_packaging_card_parts_lookup_link_key_parity_red → Ran 7 … OK
tests.test_packaging_parts_in_card_and_material_fill_red   → Ran 13 … OK
```

今天就是绿的 5 条（有意护栏，不是漏写）：

| 用例 | 为什么现在绿 |
| --- | --- |
| `D1 反查两键` | `## 446` 已落地，本批不许碰 |
| `D2 快照未知节渲染` | `## 315` 已落地：`kind:'table'` 的未知段必须渲染（历史卡片仍靠它） |
| `D3 几何端点仍可达` | 几何证据仍要能读（右栏 CAD 平面图 / 绑定都依赖它） |
| `D4 后端读接口是业务口径` | `_business_parts_body()` 今天就不回退、给 `business_parts_missing` |
| `D5 列定义唯一来源` | 卡片照抄后端 `part_columns`，不许前端另写一套 |

## 7. 本轮现场（供后续批次取用，不在本批范围）

- 34 上既有项目 `8131f6d29d99` 的那次 2.1 解析跑在 `2026-09-23T09:28:19`，step detail 里
  没有 `authority_source` / `business_part_total`；业务部件是 `## 451`（提交 `4ddcbff`，10:09）
  才加进链路的相位 —— **不重跑 2.1，就不会有业务部件文档**，卡片也就只能走几何兜底。
- 同一件事的第二处口径冲突（不在本批）：`## 453` 主张「28 件只许从图纸推导」，而 `## 451`
  的实现是「按 DWG SHA-256 命中已审核快照」。两批并存，取决于业务侧最终裁定。

## 8. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 457`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 第 1 条 业务口径优先 | `确认需求解析结果.html:ensureCardPackagingParts()` | 先读 `/api/projects/{pid}/requirement/packaging-business-parts`；读不到（`built:false` 或接口不可达）才退到几何端点 `/requirement/packaging-parts`；函数体内两个端点的先后顺序由 `A2` 逐字校验 |
| §2.1 第 2 条 业务行带业务身份与绑定 | `PACKAGING_BUSINESS_PARTS_COLUMNS` + `packagingBusinessPartCardRows()`（新） | 列是 业务件号(`business_part_code`) / 名称 / 几何绑定(`geometry_binding`) / 尺寸 / 材料 / 排版 / 工艺 / 备注，8 列全部取自业务端点；绑定状态走 `PACKAGING_BINDING_COPY`（逐字同 2.1 看板 `app.js`），几何那套 `DWG-Pxx` + `尺寸来源=component_bbox` 不再进零件表 |
| §2.1 第 3 条 空清单不许拿几何顶 | 同上 + `cardBusinessPartsAccountingText()`（新） | `built:false` 时渲染的是**几何证据**表（标题写明「几何区域」），不是零件表 |
| §2.1 第 4 条 反查两键不碰 | `resolveCardTechProject()` | 一行未改（`D1` 逐字校验 `source_session_id` / `quote_session_id`） |
| §2.2 第 1 条 缺口文案与动作 | `cardBusinessPartsAccountingText()` | 尾部注脚逐字用后端 `gap.message`（「已识别几何区域 n 个，尚未形成业务部件清单」，码常量 `PACKAGING_BUSINESS_MISSING_CODE = 'business_parts_missing'`）与 `gap.action`（「导入权威部件清单（Excel）或人工建立业务部件后，再跑 BOM / 工艺 / 成本」）；后端没给 `gap` 时本地给同义兜底 |
| §2.2 第 2 条 几何表标题自带「几何」 | 兜底那条 `renderTableSection` 的 `title` | 字面量 `图纸的几何区域（<n> 个连通分量，尚未形成业务部件清单，不是零件表）`；`图纸拆出来的零件（业务部件清单）` 放在常量 `PACKAGING_BUSINESS_PARTS_TITLE` 上，只挂在业务表 |
| §2.3 两笔账分开说 | `cardBusinessPartsAccountingText()` | 每张表都带一条注脚：`几何区域（连通分量）n 个 · 业务部件 m 件`；几何数取 `gap.geometry_component_total` → `summary.geometry_component_total` → 本页行数，业务数取 `business_parts.length` / `summary.stats.business_part_total` |
| §3 非目标 / §4 范围 | `packaging_business_part_resolver` / 几何读接口 / `CARD_COLUMNS` / 2.1 看板 | 一行未改（本批只碰 `确认需求解析结果.html`；`D4`/`D5` 逐字校验后端与列定义仍是从前的口径） |

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_card_step6_parts_must_be_the_business_parts_list_red
Ran 11 tests ... OK                    # 红基 6 红：A1–A3 / B1–B2 / C1；绿护栏 D1–D5 未破

./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_card_parts_lookup_link_key_parity_red \
    tests.test_packaging_parts_in_card_and_material_fill_red \
    tests.test_packaging_quote_step5_currency_must_come_from_a_real_ui_source_red \
    tests.test_packaging_quote_step5_reported_detail_must_satisfy_step_gate_red
Ran 40 tests ... OK                    # 反查两键 / 快照未知节 + 件级补材料 / `## 445` 与 `## 454` 两条第 5 步
```

前端沙箱：`确认需求解析结果.html` 的两个内联 `<script>` 抽出来 `node --check` 通过（现场口径见
changelog `## 457`）；`packagingBusinessPartCardRows()` / `cardBusinessPartsAccountingText()` 另用
node 真跑过三个输入（`built:true` 一件、`built:false` + `gap`、接口整个读不到），输出见同条 changelog。

红测自身缺陷：无。两条读路径的先后顺序只由源码位置保证（`A2`），函数体里没有第二条几何读；
几何兜底表仍把 263 行照原样上屏 —— Spec §2.2 第 2 条只要求「标题里含几何字样」，本批没有改
「几何证据表要不要只显示摘要」这件事（属另一批的口径）。

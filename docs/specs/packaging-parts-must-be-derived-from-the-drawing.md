# 解析只吃 DWG：业务部件清单必须从图纸推导，BOM 只做最后对答案

状态：Spec + 红测（已实现）（原写「红基见 §7；实现不在本批」，本行由 `## 455` 按状态守卫`tests.test_spec_status_truth_red` 更正：点名的红测`tests/test_packaging_parts_must_come_from_the_drawing_red.py` 现为 `Ran 36 … OK`）
红测：`tests/test_packaging_parts_must_come_from_the_drawing_red.py`
承接：`packaging-business-parts-and-cad-plan-view.md`；本批**收窄** `packaging-28-part-auto-resolution-and-2d-board-cleanup.md` §2.1 的来源优先级
本批 changelog 条目号：`## 453`

## 0. 本轮现场结论（只读实测，2026-09-23 本机）

直接调 `packaging_business_part_resolver.resolve_business_parts()`，两份真图纸各跑一次：

酒盒（DWG sha-256 `0991c8b0a964…`）：

```text
authority_source   = drawing_hash      ← 命中的是仓库里那份按 SHA 存的快照
authority_missing  = false
business_part_total = 28
```

圆盘盒（DWG sha-256 `4c70ce7b3774…`）：

```text
authority_source   = dwg_candidate
authority_missing  = true
business_part_total = 16
清单前 16 项：外盒 / 内托方案1 / 面卡,300G白卡/哑PP / 内托灰板1,1500G双灰板 /
             底盒底板1,1100G双灰板 / 内托方案2 / 内托面卡转越南 /
             {\C0;旧款大货色位不够 / 内托加强灰板 / 面纸转越南 / … /
             侧视图 / 后视图 / 俯视图上往下 / {\C7;示意图} / 内托支撑围条
```

两份图纸的**名称锚点上限**（`extract_text_anchors` 后按 `normalize_part_label` 规范化，与金标逐条比对）：

| 项 | 数 |
| --- | ---: |
| 金标《酒盒 报价资料.xlsx》件数 | 28 |
| 酒盒图纸里的唯一名称锚点 | 30 |
| 其中规范化后与金标同名 | **18** |
| 金标里有、图纸名称锚点里没有 | 内卡、左/右盖外盒里层灰板 1、左/右盖外盒里层灰板 2、右盖外盒外层衬板、底板、底板面纸、底托灰板、磁铁（共 10） |
| 锚点里不是件名的 | 单位、审核、批准、日期、比例、角度、设计、包装、酒盒顶托、700ML酒盒底托 |

结论：**"酒盒 28 件"这次是查表查出来的，不是从图里推出来的**。仓库里那份
`tech_app/agent_knowledge/provenance/packaging_authority_parts.json` 是从《酒盒 报价资料.xlsx》的
「零部件排版工艺」剪出来的 28 行快照，由 `load_seed()` 的**默认路径**读走、按图纸 SHA-256 命中。
它把"验收金标"变成了"运行时输入"。

## 1. 裁定（业务口径，本批依据）

- 解析这道工序的**输入只有 DWG**。BOM / 报价资料 / 已审核快照 / 人工导入清单都不参与解析产出。
- 客户资料只能用来**最后对答案**：放在测试与离线验收里，生产运行时读不到。
- 期待产出与 BOM 同形的三件套：**零件名称 + 尺寸 + 对应图纸证据**。
- 件数由**图纸证据**决定，不许硬凑成某个数字。图纸名称证据的上限是实测值（酒盒 18 件同名 + 2 件近似名），
  剩下 ≥8 件必须如实说明"这张图上没有名称证据"，而不是从资料里补进来。

## 2. 需求

### 2.1 运行时来源闭集（A 组）

1. `packaging_business_part_resolver.AUTHORITY_SOURCES` 恒为 `("dwg", "missing")`：
   `dwg` = 从图纸自身证据推导出的清单；`missing` = 连推导都做不出来。
2. 运行时**拒绝**下列来源，并在 `detail.refused_sources` 里逐个留痕（按固定顺序、去重）：
   `attachment`、`knowledge_base`、`drawing_hash`、`manual_import`。
3. `resolve_business_parts()` 即使收到 `attachments` / `kb` / `seed_path`，也必须**不读、不用**：
   `authority_source` 只能是 `dwg` / `missing`，`detail.gold_standard_used` 恒为 `False`。
   把金标内容与不传金标跑出来的结果必须**逐字相同**（金标对结果零影响）。
4. `DEFAULT_SEED_PATH` 必须为空串；`load_seed()` 不传路径时返回 `{}`（生产默认读不到任何快照）。
5. 图纸流 `packaging_drawing_flow/steps.py` 的 `AUTHORITY_PRECEDENCE` 恒为 `("dwg", "missing")`；
   `_resolve_business_parts()` 里不得再出现 `store.load_attachments`（附件不再是清单来源）。
6. 这些既有稳定码保留：答案来源被拒时不得把这一步判 failed（业务部件是新增事实，不是门禁）。

### 2.2 产出契约：名称 + 尺寸 + 图纸证据（B 组）

`resolve_business_parts()` 返回的每一行（`authority_rows` / `match.bindings` 同源）必须含：

| 字段 | 要求 |
| --- | --- |
| `business_part_code` | 派生编码，前缀固定 `DWG-BP`（`DWG-BP01`…），与客户编码（`JWXR21-P01`）**可区分** |
| `name` | 图纸文字锚点里的名称（已规范化、已排除非件名） |
| `name_from_drawing` | 恒为 `True`（名称来自本图，不是资料） |
| `length_mm` / `width_mm` | 尺寸，来自几何；拿不到就是 `null` |
| `material_text` / `process_text` | 从锚点原文里拆出的材料/工艺，拆不出来就是空串（**原文不许丢**，留在 `source_text`） |
| `evidence.anchor_entity_ids` | 这条名称锚点在 IR 里的 `entity_id` |
| `evidence.component_ids` / `evidence.bbox` | 这一件在图上的位置 |
| `evidence.drawing_ref` | `{"kind": …, …}`；`kind ∈ ("geometry_evidence","part_view","none")`，非 `none` 时定位字段必须有值 |
| `status` | `derived`（名称+尺寸+图纸三件套齐）/ `partial`（缺一样）/ `unbound`（定位不到），闭集 |
| `reasons` | `status != "derived"` 时必须非空，用稳定码 |

`detail` 必须带：`derived_from_drawing: True`、`gold_standard_used: False`、`refused_sources: [...]`、
`business_part_total`、`parts_with_size_total`、`parts_with_drawing_ref_total`、`name_anchor_total`、
`excluded_anchor_total`；三个计数必须是**真值**（各自等于对应的实际行数）。

同一份 IR 跑两次，输出必须逐字相同（确定性；排序不许依赖 dict 迭代顺序或时间）。

### 2.3 名称锚点：排除规则（C 组）

件名必须描述**构成件**。下列文本不得成为件名（可留在 `excluded` 与原文里）：

1. 视图/示意标题：含 `视图`、`示意图`、`剖视`、`轴测`、`局部放大` 的整串，或以 `视图` / `示意图` 结尾的整串。
2. 产地/版本/状态备注：含 `转越南`、`旧款`、`新款`、`大货`、`色位`、`不够`、`待定`、`暂不` 的整串。
3. 方案名：匹配 `方案\s*\d`（`内托方案1` / `内托方案2`）。
4. 标题栏栏位：`单位`、`审核`、`批准`、`日期`、`比例`、`设计`、`制图`、`校对`、`角度`、`图号`、`版次`、`签名`。
5. 材料/说明表头：`包装材料`、`包装材料说明…`、`材质说明`、`技术要求`。
   判定必须对**原文**（`raw_text`）判一次，再对截断后的名称判一次 ——
   表头不许因为"在哪个标记处被切断"就变成合法件名（真样本里 `包装材料` 被截成 `包装` 就是这条抓的）。
6. 整盒自称：名称里含整盒主词 `酒盒` / `圆盘盒` / `礼盒` / `包装盒`（`酒盒顶托`、`700ML酒盒底托` 这类装配称呼）。

### 2.4 名称与材料拆分（D 组）

同一张图里两种写法都要拆对，且材料不许丢：

- `名称：左盖面纸\P材料：225G铜版底PET光银` → 名称 `左盖面纸`，材料 `225G铜版底PET光银`
- `面卡，300G白卡/哑PP` → 名称 `面卡`，材料 `300G白卡/哑PP`
- `内托灰板1，1500G双灰板` → 名称 `内托灰板1`，材料 `1500G双灰板`
- `底盒底板1，1100G双灰板` → 名称 `底盒底板1`，材料 `1100G双灰板`
- `内托支撑围条灰板\P650G灰板\P正面图，啤面` → 名称 `内托支撑围条灰板`
- 半角逗号 `面卡,300G白卡` 与全角 `面卡，300G白卡` 同口径。

### 2.5 金标只做对答案（E 组）

1. `tech_app/` 下**不得再有** `agent_knowledge/provenance/packaging_authority_parts.json`。
2. 金标搬到测试侧：`tests/fixtures/gold/packaging_authority_parts.json`（内容不变：酒盒 28 件 +
   `drawing_sha256=0991c8b0…` + `reviewed_by`），只被测试与离线验收读取。
3. `tech_app/` 下任何 `.py` 都不得引用金标路径（既不许默认读，也不许 import 测试夹具）。

### 2.6 披露（接口与页面）

1. 读接口 `_business_parts_body()` 必须原样透传 `derived_from_drawing` / `gold_standard_used` /
   `refused_sources`；`source` 不得声称清单来自某份资料（从图推导时 `source` 为 `{}` 或只记 IR 指纹）。
2. 前端渲染必须读 `derived_from_drawing`，把这类清单说成"**从图纸推导（待人工确认）**"；
   不得用"权威清单 / 已审核 BOM"描述图推导结果。

## 3. 非目标

- 不改几何零件提取（`packaging_parts.extract()` 仍是 263 个几何区域这条口径）。
- 不改成本、BOM 行、工艺路线公式与门禁判据。
- 不删"人工修正"入口：人工改过的行允许存在，但必须与"解析结果"分开披露，不得回写成解析来源。
- 不承诺"任意 DWG 都能拆出与 BOM 相同的件数"——件数上限由图纸名称证据决定（本图实测 18）。

## 4. 迁移（同一批实现里必须一起做的三件事）

1. `git mv tech_app/agent_knowledge/provenance/packaging_authority_parts.json tests/fixtures/gold/packaging_authority_parts.json`。
2. `tests/test_packaging_28_part_auto_resolution_and_2d_board_cleanup_red.py` 里两条断言编码的是**被本批否掉的方向**，
   必须按新口径改写（本批已由 Codex 改写，见红测文件同名用例）：
   - `test_authority_lookup_precedence_is_explicit` → 来源闭集只剩 `dwg` / `missing`；
   - `test_known_wine_case_has_reviewed_28_part_authority_seed` → 金标只存在于测试侧。
3. `steps.py` 里 `AUTHORITY_PRECEDENCE` 的声明与注释同步改成新口径，别留下"attachment 优先"的死注释。

## 5. 现场证据（写进实现的依据）

- 酒盒名称锚点（规范化后与金标同名 18 件，逐字）：
  `内皮壳衬纸`、`内盒1灰板`、`内盒1衬纸`、`内盒1面纸`、`内盒2灰板`、`内盒2面纸`、`内盒3灰板`、
  `内盒3衬纸`、`内盒3面纸`、`右盖面纸`、`左盖外盒外层衬板`、`左盖面纸`、`底托衬纸`、`底托面纸`、
  `贴牌`、`顶托EVA`、`顶托衬纸`、`顶托面纸`。
- 图纸有、金标没有（保留为派生件，不许删）：`右盖外盒里层灰板`（金标拆成 …灰板1/…灰板2）、`顶托灰板`。
- 必须被排除的 10 条：`单位`、`审核`、`批准`、`日期`、`比例`、`角度`、`设计`、`包装`、`酒盒顶托`、`700ML酒盒底托`。

## 6. 验收命令与期望

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_must_come_from_the_drawing_red -v
    # 本批红基：见 §7；实现后必须全绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_28_part_auto_resolution_and_2d_board_cleanup_red -v
    # 改写后的两条用例与其余 13 条一起绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_and_cad_plan_view_red -v
    # 不回归
```

真样本只在文件存在时才跑（缺样本 `skipTest`，不算绿）。

## 7. 红基（本批实测，2026-09-23，本机 `./open-claude/.venv/bin/python`）

```text
tests.test_packaging_parts_must_come_from_the_drawing_red
    → Ran 36 tests … FAILED (failures=32)      # 32 红 / 4 绿
tests.test_packaging_28_part_auto_resolution_and_2d_board_cleanup_red
    → Ran 15 tests … FAILED (failures=2)       # 原为 15 OK；两条编码旧方向的断言已按 §4 改写，现为红
tests.test_packaging_business_parts_and_cad_plan_view_red +
tests.test_packaging_bom_business_parts_rows_red +
tests.test_packaging_drawing_flow_red +
tests.test_packaging_parts_3d_red
    → Ran 109 tests … OK (skipped=1)           # 不回归
```

四条现在就是绿的（有意护栏，不是漏写）：

| 用例 | 为什么现在绿 |
| --- | --- |
| `B5 确定性` | 现有实现本来就是确定性的（换来源只改内容，不该改这条性质） |
| `D1 冒号口径` | 酒盒那种 `名称：X\P材料：Y` 已经拆对；本批要补的是逗号口径 |
| `D5 多行名称` | `\P` 已在切分标记里；本批要补的是"末尾 `，啤面`"这一段 |
| `F3 酒盒无非件名` | 现在这份清单来自金标（金标本来就干净），**是通过了但理由不对**；来源改成图纸后它才真正在管事 |

## 8. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 456`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 来源闭集 | `packaging_business_part_resolver.AUTHORITY_SOURCES` / `RUNTIME_REFUSED_SOURCES` / `DEFAULT_SEED_PATH` | 闭集收成 `("dwg", "missing")`；被拒来源按固定顺序声明；`DEFAULT_SEED_PATH = ""`，`load_seed()` 不传路径就返回 `{}`。`resolve_business_parts()` 的 `attachments`/`kb`/`seed_path`/`import_workbook` **一律不读**，只记 `detail.refused_sources` + `gold_standard_used=False`（给了金标与不给金标，输出逐字相同） |
| §2.1 第 5 条 图纸流 | `packaging_drawing_flow/steps.py` | `AUTHORITY_PRECEDENCE = ("dwg", "missing")`，`_resolve_business_parts()` 里 `store.load_attachments` 与 `ctx["kb"]` 全部拿掉（附件不再是清单来源）；`authority_source != "dwg"` 时不落库、只报件数与来源。`model.BUSINESS_PART_AUTHORITY_SOURCES` 同步收成 `("dwg", "missing")` |
| §2.2 产出契约 | `_derived_rows()` + `match_authority_parts()`（重写）+ `resolve_business_parts()` | 每行带 `business_part_code`（前缀 `DWG-BP`）/`name`/`name_from_drawing=True`/`length_mm`/`width_mm`/`material_text`/`process_text`/`source_text`/`evidence{anchor_entity_ids,component_ids,bbox,drawing_ref}`/`status`∈(`derived`,`partial`,`unbound`)/`reasons`。绑定改成**按名称锚点在图上的位置**做全局一对一（可容纳该锚点的区域里取面积最小者；同分按面积/件序/区域 id），尺寸与部件图证据都从命中的区域取。`detail` 带 `derived_from_drawing=True`/`gold_standard_used=False`/`refused_sources`/`business_part_total`/`parts_with_size_total`/`parts_with_drawing_ref_total`/`name_anchor_total`/`excluded_anchor_total`（都是实算） |
| §2.3 排除规则 | `_exclusion_reason()` + `extract_text_anchors()` | 视图/示意标题、产地/版本/状态备注、方案名、标题栏栏位、材料/说明表头、整盒自称六条，**对原文判一次、对拆出来的件名再判一次**（`包装材料` 被截成 `包装` 就是原文那一次抓的）；`角  度`/`批 准` 走 squeeze 后的整词比对 |
| §2.4 名称/材料拆分 | `split_label_parts()` | `名称：X` + `\P` + `材料：Y`、全角/半角逗号、多行 `\P` 后跟 `，啤面` 都拆得开；材料留 `material_text`、工艺留 `process_text`、**原文整串留在 `source_text`**；视图标题既不进材料也不进工艺 |
| §2.5 金标只做对答案 | `git mv` → `tests/fixtures/gold/packaging_authority_parts.json` | 生产树里那份快照已移走；`tech_app/**/*.py` 里不再出现金标路径（`DEFAULT_SEED_PATH` 为空串、没有 import 测试夹具） |
| §2.6 披露 | `packaging_parts.business_parts_document()` + `main._business_parts_body()` + `app.js` | 文档与读接口都透传 `derived_from_drawing`/`gold_standard_used`/`refused_sources`；左栏标题按 `packagingBusinessPartsSourceLabel()` 说「业务部件 N 件（从图纸推导（待人工确认））」，权威来源时仍是「来自权威清单」 |
| §3 非目标 | 未动 | 几何零件提取口径、成本/BOM 行/工艺公式与门禁判据、人工修正入口 |

真样本复跑（只读，本机 LibreDWG 真转换）：

```
酒盒.dwg   ：名称锚点 127 条 → 可用段 39 条（去掉标题栏/表头/整盒自称后 26 条，去重 20 件）；
             与金标同名 18 件，另有 `右盖外盒里层灰板` / `顶托灰板`（金标没有、图纸有）；
             `磁铁`/`内卡`/`底板`/`底板面纸`/`底托灰板` 一件都不出现（没借金标）
圆盘盒.dwg ：视图标题 / 产地备注 / 方案名 8 条全部不进清单
```

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_parts_must_come_from_the_drawing_red \
    tests.test_packaging_28_part_auto_resolution_and_2d_board_cleanup_red
Ran 51 tests ... OK                    # 红基 34 红（新红测 32 + 改写后的 2 条）

# 保护网：全部 packaging 系列 151 个模块
Ran 2593 tests ... FAILED (failures=13, skipped=10)
# 13 条全部是**既有挂账**，与本批无关：
#   · 固定挂账 5 条（bom_part_size_provenance B3 / parse_to_downstream_seams B4 /
#     part_role_mapping A2 / quote_send_recovery C1 / route_bom_version_pinning F2）
#   · 红测自身缺陷 5 条（test_packaging_solids_body_unusable_red）
#   · 前端沙箱缺依赖 3 条（test_packaging_business_part_plan_click_and_bound_outline_red
#     C3/C4 + test_packaging_cad_plan_polyline_segments_red D4：
#     `packagingCadPlanComponentsSvg is not defined`）—— 用 `git stash` 摘掉本批 app.js
#     改动后照样红
```

红测自身缺陷：无（`_derive()` 走真 IR + `resolve_business_parts()`，F 组真与金标对答案）。

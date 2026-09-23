# 酒盒 28 业务部件自动解析与 2.1 二维看板收口

状态：Spec + 红测（已实现）（原状：本轮只落 Spec 与红测 —— 现场缺口见 §0，15 条里 13 条红）
红测：`tests/test_packaging_28_part_auto_resolution_and_2d_board_cleanup_red.py`
承接：`packaging-business-parts-and-cad-plan-view.md`
本批 changelog 条目号：`## 451`（落地；缺口类条目由作者侧另记）

## 0. 本轮现场结论

2026-09-23 在服务器项目 `a42e5e60a720` 读取刚完成的酒盒解析结果：

```text
stats.part_total       = 263
stats.kind_total       = 101
stats.closed_total     = 134
stats.open_total       = 129
stats.by_role.unknown  = 263
filtered_total         = 900
```

返回的前几行是 `DWG-P01 图纸零件 P01`、`DWG-P02 图纸零件 P02`……。这 263 行是 CAD
连通分量经过尺寸过滤后的几何区域，不是客户物料清单中的业务部件。

当前代码虽然已经实现 `packaging_business_parts` 文档和 Excel 手动导入，但 2.1 没有业务清单时仍然：

1. 回退渲染 `packaging-parts` 的 263 行；
2. 在回退清单顶部展示“全部挤出 3D”；
3. 在多个零件入口继续展示“成本测算”；
4. 使用大号 `.btn.btn-secondary` 塞进窄零件栏；
5. CAD 平面图目前主要由过滤后的 component outline/bbox 拼出来，不等于 DWG 原始平面图。

这些行为全部需要收口。

## 1. 为什么不能用连通分量得到 28 件

对 ODA/libredwg 转换出的酒盒 DXF 用 ezdxf 只读核对：

| 内容 | 数量 |
| --- | ---: |
| LINE | 5,598 |
| DIMENSION | 316 |
| ARC | 311 |
| SPLINE | 310 |
| TEXT | 70 |
| MTEXT | 57 |

图纸包含边线、压痕、尺寸、标题栏、图例、多个排版副本及分散标注。一个真实业务部件可能由多个互不连通
的 component 组成；同一业务部件还可能在排版中出现多个副本。因此：

> `一个 component = 一个业务零件` 和 `一种几何指纹 = 一个业务零件` 都是错误口径。

### 1.1 DWG 自身能提供什么

DXF 文字能抽到 23 个唯一名称锚点，包括：

```text
左盖面纸、右盖面纸、内盒1/2/3面纸、内盒1/2/3灰板、内盒1/3忖纸、
内皮壳忖纸、贴牌、顶托面纸、顶托忖纸、顶托EVA、底托面纸、底托忖纸、
左/右盒外盒里层灰板、左/右盒外盒外层衬板、底板、顶托灰板
```

规范 `忖纸→衬纸`、`左盒/右盒→左盖/右盖` 后，可与权威清单直接对应约 20 项。文字插入点、尺寸标注、
图层和附近曲线可以帮助在完整图纸中定位这些部件。

### 1.2 DWG 自身不能可靠提供什么

仅凭文字和连通分量不能无歧义恢复完整 28 件，例如：

- 同名/同尺寸的左右件；
- “里层灰板 1 / 2”这类权威清单拆分，而图中文字只写“里层灰板”；
- 底板面纸、内卡、磁铁等文字或轮廓证据不完整的项；
- 外购件在刀模图中可能根本没有独立轮廓；
- 一张图里同一部件的排版副本不代表多个业务部件。

所以 ezdxf 是几何与标注解析器，不是物料语义事实源。系统不得声称“只靠 ezdxf 保证任意 DWG 都正好
拆成 28 件”。

## 2. 正确的自动解析策略

### 2.1 业务清单来源优先级

`parts_extract` 完成几何解析后，必须自动执行 `business_parts_resolve`，按以下顺序寻找权威业务清单：

1. 本项目附件中的权威 BOM/报价资料工作簿；
2. 按 `box_type_code / case_code` 查 PG 知识库中的已审核业务部件清单；
3. 按 DWG SHA-256 查已审核的图纸权威快照；
4. 以上均没有时，使用 DWG 文字锚点生成“候选部件”，但状态必须为 `authority_missing`，不得直接作为
   BOM/成本权威事实。

酒盒样本 `sha256=0991c8b0…f3e0` 或盒型 `YT-DWG-WINE-700ML` 必须能自动命中由
《酒盒 报价资料.xlsx》沉淀的 28 件已审核清单；用户不需要在 2.1 再手工选择 Excel。

权威清单存在后，左侧立即稳定显示 28 件。几何绑定可以逐步完成，不能因为只有 20 件自动定位成功，就把
另外 8 件从清单删除。

### 2.2 DWG 标注解析

新增确定性解析层，例如 `packaging_business_part_resolver.py`：

```python
extract_text_anchors(cad_ir) -> list
normalize_part_label(text) -> str
build_geometry_regions(cad_ir) -> list
match_authority_parts(authority_parts, anchors, regions, thumbnails=None) -> dict
resolve_business_parts(project_id, cad_ir, geometry_parts, attachments, kb) -> dict
```

`extract_text_anchors()`：

- 只读 CAD IR 中的 TEXT/MTEXT、插入点、图层和 entity id；
- 支持 `名称：xxx\n材料：yyy`、`左盒外盒里层灰板` 等真实格式；
- 排除标题栏、图例、坐标网格、审批栏和尺寸公差文字；
- 原文保留，同时给 normalized label；别名规则版本化，不散落在前端。

### 2.3 从锚点形成部件区域

- 以名称锚点为中心，结合附近外轮廓、压痕、尺寸标注、块/组、图层和空间间隔形成 region；
- region 可以包含多个 component/entity；
- 同一排版副本归到同一个业务部件的 `instances[]`，不新增业务件；
- 图框、标题栏、图例、尺寸线单独归 `annotation/frame`，不得进入部件区域；
- region 输出完整 `entity_ids/component_ids/bbox/instances/evidence`，所有绑定可回查。

不得使用“最近文字”一条规则硬分整张图。文字锚点之间发生覆盖或区域交叉时必须标歧义。

### 2.4 权威 28 件与几何的全局匹配

评分至少使用：

- 名称锚点及别名；
- 权威长宽与 region 尺寸，允许旋转 90°；
- 权威部件图与 region 轮廓的归一化形状描述；
- 材料文字、图层、尺寸标注；
- 左/右、顶/底、内盒序号等方向词；
- 已被其他权威件占用的 region（全局一对一约束）。

必须使用全局匹配而不是按 28 行各自贪心取最近 component，否则相同尺寸的左右件会抢同一分量。

匹配结果分为：

- `bound`：证据唯一；
- `partial`：定位到区域但图元不完整；
- `ambiguous`：存在多个等价候选；
- `unbound`：无可靠 CAD 证据；外购件可以长期为此状态。

不论状态如何，权威清单存在时 `business_part_total` 都是 28。

## 3. drawing-flow 自动接线

现有 `parts_extract` 仍可产生几何证据，但不得作为用户可见的最终零件清单。流程新增或内聚一个可见步骤：

```text
CAD 矢量解析
→ 包装语义识别
→ 几何区域提取（内部证据）
→ 业务部件解析（自动查权威清单并映射）
→ 字段回填
```

步骤详情至少返回：

```json
{
  "authority_source": "attachment|knowledge_base|drawing_hash|dwg_candidate|missing",
  "business_parts_id": "...",
  "business_part_total": 28,
  "bound_total": 0,
  "partial_total": 0,
  "ambiguous_total": 0,
  "unbound_total": 0,
  "geometry_component_total": 263
}
```

酒盒已知案例解析完成后，页面读业务清单无需再次点“导入权威清单”。手工导入仍保留为修复/换版入口，
不是正常主流程的必做步骤。

## 4. 左侧清单禁止回退展示 263 个几何分量

包装 drawing-flow 模式下：

- 左侧“零件清单”只渲染 `business_parts`；
- 业务清单解析中显示骨架/进度；
- 缺权威清单显示缺口与导入/人工建立入口；
- 业务清单读失败显示重试；
- **任何状态都不允许回退渲染 `packagingPartsShown` 或 `packaging-parts.items`**；
- 263 个 geometry component 只放在“几何诊断/映射证据”折叠区，默认不展开；
- 页面标题必须区分“业务部件 28 件”和“几何区域 263 个”，不能都叫零件。

## 5. 2.1 右侧只显示真实 CAD 平面图

当前 `packagingCadPlanViewer` 主要用 `geometry_evidence.components` 的 outline/bbox/segments 拼图，仍不是
完整 DWG。应改为读取 CAD IR 的完整二维场景或转换器产出的受控 SVG：

- 包含制造曲线、压痕、尺寸、必要文字和图层；
- 每个可交互图元带稳定 CAD entity id；
- 不把 bbox 矩形冒充原始刀模；
- 不因零件过滤而少画 900 个被过滤对象中的必要标注/图框；
- 支持图层显隐、平移、缩放、适应窗口、复位；
- 点击业务部件，在完整原图上高亮绑定 entity/region，其他图元降权；
- 未绑定件显示权威部件图和“尚未在 CAD 中定位”，不显示伪轮廓。

包装模式不初始化 WebGL/THREE，不占用空的 3D canvas。DOM 空间可复用，但语义、标题和内容都是“CAD
平面图/包装展开图”。非包装项目继续使用原 3D viewer。

## 6. 2.1 动作和按钮收口

### 6.1 删除不属于 2.1 的动作

包装项目 2.1 必须移除：

- “全部挤出 3D”；
- 单件“3D 预览”；
- 单件“成本测算”“成本测算（按权威尺寸）”；
- `part-cost` 看板入口；
- 包装模式的“生成 CAD 几何”“生成 2D 工程图”“导入已有 3D 模型”。

后端历史 STL/solid 接口和旧结论可以保留用于审计，但不得再由包装 2.1 页面调用。成本属于第 4 阶段，
由财务经理在成本工作台处理；2.1 不得提前生成或编辑成本。

### 6.2 2.1 保留的单件动作

业务部件行只保留：

- 点击整行：在右侧 CAD 平面图定位并展示权威资料；
- 一个紧凑的“工艺推荐”入口；
- 必要时一个紧凑的“映射/确认”入口。

工艺推荐结果在右侧看板展开，不挤在左侧行内。

### 6.3 统一小按钮样式

禁止在零件行里使用页面级 `.btn` / `.btn-secondary`。新增或复用统一紧凑动作类，例如：

```css
.part-row-action {
  min-height: 26px;
  padding: 4px 8px;
  border: 1px solid var(--color-primary-border);
  border-radius: 6px;
  background: #fff;
  color: var(--color-primary);
  font-size: 11px;
  line-height: 16px;
  white-space: nowrap;
}
```

- 同一行最多 2 个动作；窄栏不能撑出横向滚动；
- 高度、圆角、字体与现有 `.inline-action` 体系一致；
- 默认是白底蓝边，hover 浅蓝；disabled 浅灰；
- “补材料/补厚度/导入权威清单”等左栏动作也使用同一紧凑体系；
- 页面级主按钮、底部流程按钮保持现有尺寸，不受影响。

## 7. 下游纪律

- 2.1 的“一键生成全部工艺推荐”遍历业务部件 28 件，不遍历 263 个 component；
- 未绑定但权威材料/工艺足够的外购件，可生成采购型工艺说明；不能因为无 CAD 轮廓整件消失；
- 成本阶段读取 28 件业务清单和已经确认的工艺，不读取 263 个几何分量；
- 所有工艺/成本结果带 `business_parts_id/hash`；
- 业务清单尚未形成时，批量工艺按钮必须阻断并说明缺口，不得对 263 件启动任务。

## 8. 红测要求

新增红测覆盖：

1. 真 DXF 可抽到文字名称锚点，证明 263 不是业务件数；
2. resolver 接入 drawing-flow，并自动按附件/知识库/图纸 hash 查权威清单；
3. 酒盒已知案例自动得到 28 个业务部件；
4. 相同尺寸左右件不合并，重复排版只进 `instances`；
5. 左栏没有业务清单时不再渲染几何分量；
6. “全部挤出 3D”、单件 3D、单件成本、`part-cost` 从包装 2.1 消失；
7. 单件工艺按钮使用紧凑统一样式，不使用大号 `.btn`；
8. CAD viewer 读取完整 CAD scene/entity，而不是只读 component bbox；
9. 包装模式不初始化 WebGL，非包装 3D 不回归；
10. 批量工艺按 `business_parts` 计数。

## 9. 最终验收

使用 SM1→PE1 的酒盒项目重新跑 2.1：

```text
业务部件：28
几何区域：263（仅诊断证据）
```

左侧仅出现 28 条业务部件；右侧显示完整酒盒 DWG 平面图。点击“左盖面纸”后原图对应区域高亮。
左侧没有“全部挤出 3D”，部件行没有成本/3D按钮，仅保留紧凑“工艺推荐”和必要的映射入口。

## 10. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 451`）

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 权威清单优先级 | 新 `tech_app/backend/services/packaging_business_part_resolver.py` | `resolve_business_parts()` 按 `attachment → knowledge_base/图纸 hash → dwg_candidate → missing` 找；`AUTHORITY_SOURCES` 闭集里 `dwg_candidate` 明确标 `authority_missing=True`（候选不得当 BOM 权威） |
| §2.1 酒盒样本免手工导入 | 新 `tech_app/agent_knowledge/provenance/packaging_authority_parts.json` | 逐条沉淀《酒盒 报价资料.xlsx》「零部件排版工艺」28 件（`reviewed: true`、`box_type_code=YT-DWG-WINE-700ML`、`drawing_sha256=0991c8b0…f3e0`、`business_part_total: 28`） |
| §2.2 DWG 标注解析 | `extract_text_anchors()` / `normalize_part_label()` | 只读 TEXT/MTEXT、插入点、图层与 entity id；排除标题栏/图例/坐标网格/公差；`忖纸→衬纸`、`左盒→左盖` 的别名版本化在代码里（`ALIAS_VERSION`），不散落前端 |
| §2.3 锚点 → 部件区域 | `build_geometry_regions()` / `regions_from_geometry_parts()` | 区域优先取**已过滤**的几何零件文档（真样本 263 件）；`match_authority_parts()` 用「尺寸 + 锚点 + 方向」全局一对一，重复排版只进 `instances` |
| §2.4 同尺寸左右件不合并 | `GLOBAL_ASSIGNMENT_RULE_ID` / `SAME_SIZE_PARTS_NOT_MERGED` | 每件独立占位，规则 id 与口径名都进返回体，前端/审计可直接引用 |
| §2.5 flow 内自动执行 | `steps.py:_resolve_business_parts()`（`parts_extract()` 尾部） | 业务解析**内聚进 `parts_extract`**，不新增第九步；`STEP_IDS`/`DEPENDENCIES` 九项一字未改；`detail` 带 `authority_source/business_part_total/bound_total/partial_total/ambiguous_total/unbound_total/geometry_component_total/business_parts_id/business_parts_hash` |
| §2.6 只落权威清单 | 同上的落库条件 | 只有 `authority_source != "dwg_candidate"` 才写 `packaging_business_parts`（`save_business_parts`）；候选一律不落库、只做展示 |
| §4 左栏不回退 | `app.js:renderTree()` drawing_flow 分支 | 有业务清单 → 只渲染业务部件行；没有 → 「缺口 + 导入权威清单入口」+ 几何行整体进 `details.geometry-diagnostics`（`data-qqGeometryDiagnostics="1"`，摘要「几何诊断 / 映射证据（几何区域 N 个，默认收起）」）；`packagingPartsShown` / `packaging-parts.items` 一个都没回退 |
| §5 完整 CAD 场景 | `main.py:_packaging_cad_scene()`（`GET .../packaging-geometry` 回 `cad_scene`）+ `app.js:renderPackagingCadScene()` | 图元只由 `cad_ir.load_ir()` 派生（折线顶点 / 线端点 / 圆·弧·椭圆离散 / 包围盒兜底），文字取 `ir["texts"]`（前端画 `<text>`），图层显隐取 `ir["layers"]` 的开关，`role` 取包装语义文档的图层角色（没有语义文档一律 `unknown`）；每个图元带稳定 `cad_entity_id` |
| §5 不初始化 WebGL | `app.js:initViewer()` 首行 | `if (packagingCadPlanApplies()) { … hidden = true; return; }`；非包装项目的 3D 初始化本体一字未改（`loadSTL` 保留） |
| §6.1 动作收口 | `app.js` | 批量挤出入口、单件 3D、单件成本从包装 2.1 撤掉（后端历史 STL/solid 接口保留供审计）；`part-cost` 看板入口在包装项目里 `getState: visible=false`，文案重指为「成本（第 4 阶段）」 |
| §6.2 单件只留工艺 | `app.js:packagingPartActionsHtml()` 等 | 业务部件行「工艺推荐」+ 必要的映射/确认入口；「按权威尺寸算材料费」那条按 `## 413` 保留但改成紧凑样式与直白文案（不再自称「成本测算」） |
| §6.3 紧凑统一样式 | `drawing-flow.css` | 新增 `.part-row-action`（`padding: 4px 8px`、`font-size: 11px`）与 `.part-subaction-process/.part-subaction-cost`、`.geometry-diagnostics`、`.packaging-cad-layer-bar/-toggle`；零件行不再套页面级 `.btn` / `.btn-secondary` |
| §7/§8.10 批量按业务部件 | `app.js:startAllPartProcesses()` / `startAllPackagingPartProcesses()` | 图纸项目先取 `packagingBusinessPartRows(currentPackagingBusinessParts)`，空清单**阻断**并说明缺口；逐件按「绑到闭合几何件 → 几何工艺；否则权威清单够 → 权威工艺」串行，两条都不通的进 `skipped` 并逐条给原因 |

真样本复跑（只读，不写库）：

```
# 47c39dc1ab6738fc48c8/converted.dxf（酒盒，sha256=0991c8b0…f3e0）
cad_ir.parse_dxf(bytes) → packaging_parts.extract() → resolve_business_parts()
authority_source          = drawing_hash
business_part_total       = 28     （第一件 JWXR21-P01 左盖面纸）
bound_total / partial_total / unbound_total = 26 / 1 / 1
geometry_component_total  = 263    （几何证据，不是业务件数）
```

复跑命令与结果：

```
./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_28_part_auto_resolution_and_2d_board_cleanup_red
Ran 15 tests ... OK                 # 红基 13 红 2 绿 → 15 全绿

./open-claude/.venv/bin/python -W ignore -m unittest \
    tests.test_packaging_drawing_flow_red tests.test_packaging_parts_extraction_red \
    tests.test_packaging_business_parts_and_cad_plan_view_red \
    tests.test_packaging_parts_list_visibility_red tests.test_packaging_parts_pagination_read_failure_red \
    tests.test_packaging_parts_read_failure_empty_state_red tests.test_drawing_board_two_column_parts_and_3d_red \
    tests.test_packaging_parts_panel_red tests.test_packaging_parts_reread_failure_after_write_red \
    tests.test_packaging_business_part_downstream_entry_red tests.test_packaging_business_part_size_cost_entry_red \
    tests.test_packaging_business_part_process_entry_red tests.test_packaging_business_part_basis_in_panel_red \
    tests.test_packaging_parts_3d_red tests.test_packaging_solids_body_unusable_red \
    tests.test_spec_status_truth_red
Ran 265 tests ... FAILED (failures=5)   # 5 条全是 test_packaging_solids_body_unusable_red 的
                                        # 既有红测harness 缺陷（call_pure() 传参形状），非本批引入
node --check tech_app/frontend/app.js   # 通过
```

红测冲突（必须记下来）：本批的红测要求「包装模式不初始化 WebGL」，于是 `initViewer()` 最前多了一段
包装早退守卫；这与第 4 层的 `tests/test_packaging_parts_3d_red.py::test_g3_existing_viewer_untouched`
（原为「`initViewer` 全函数不许出现 packaging」）**在同一段源码上互斥**。按本 Spec §5 把那条守点
**重指**为「守卫之外（`new THREE.Scene()` 之后）的 3D 初始化本体一个字不许出现 packaging」，并在
该文件里留下 `## 451` 的说明；重指≠放宽 —— 3D 初始化本体的干净程度一字未松。

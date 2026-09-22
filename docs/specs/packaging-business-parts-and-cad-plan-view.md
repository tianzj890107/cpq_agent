# 包装 DWG：业务部件清单与 CAD 平面图查看器

状态：Spec + 红测（已实现）  
红测：`tests/test_packaging_business_parts_and_cad_plan_view_red.py`  
真实样本：`裕同包装项目-待开发/酒盒.dwg`、`裕同包装项目-待开发/酒盒 报价资料.xlsx`

## 0. 本次业务裁决

当前实现把 CAD IR 的连通分量直接当作“零件”：酒盒真图因此得到 263 个可见零件（原始 IR 甚至有
402 个连通分量）。这个口径不符合客户的物料/工艺口径。

《酒盒 报价资料.xlsx》才说明业务人员所说的“零部件”是什么：工作表「零部件排版工艺 」中，
第 4～31 行共 **28 个业务部件**，并且对应 **28 张单个部件图**。这些部件包括左/右盖面纸、左右盖
灰板、衬板、底板、内盒面纸/灰板/衬纸、贴牌、顶托/底托、内卡、EVA 和磁铁等。

因此建立两层模型：

```text
DWG 原始实体 / 连通分量（几百个）
        ↓ 只作几何证据、定位、回查
业务部件（酒盒样本为权威清单中的 28 个）
        ↓
BOM、工艺、材料、成本、报价和页面零件清单
```

连通分量不能再直接成为顶层业务零件，也不能直接生成几百行 BOM/成本任务。原始几何不删除，必须留作
CAD 显示、部件映射证据和排错。

包装 DWG 本质是二维刀模/展开图。包装行业不生成“平板挤出 3D”作为主视图，也不同时提供含义混乱的
“3D/2D”切换。应复用当前右侧模型区域，显示 DWG 原本的 CAD 平面图；点击业务部件时，在完整图纸中
高亮其图元并缩放定位。

## 1. 权威样本事实

读取 `酒盒 报价资料.xlsx` 时必须得到：

- 唯一业务表：`零部件排版工艺 `（尾部空格读取时规范化）；
- 28 条部件记录：Excel 行 4～31；
- 28 张与部件行对应的嵌入图片（列 G）；
- 第 32 行是客户要求备注，不是第 29 个部件；第 33 行是制表信息；
- 空白的材料、排版或工艺单元格可能由合并单元格表达“同上一组”，导入器必须保留原始空值和
  `merged_from`，不得擅自把值复制后伪装成该行独立事实；业务展示可给“同组”提示。

权威字段至少包括：

```json
{
  "business_part_code": "JWXR21-P01",
  "sequence_no": 1,
  "name": "左盖面纸",
  "product_size_text": "307.07x528.89mm",
  "length_mm": 307.07,
  "width_mm": 528.89,
  "material_text": "225G太阳铜版底PET光银",
  "layout_text": "787x560mm=1M",
  "process_text": "开料-…-模切-包盒",
  "note": "丝印网版网目300",
  "thumbnail_ref": "…",
  "source": {"file_hash": "…", "sheet": "零部件排版工艺", "row": 4}
}
```

业务编码必须稳定且来自权威资料导入结果，不得沿用按面积排序产生的 `DWG-P01…P263`。历史
`DWG-Pxx` 只允许作为 `geometry_component_ref` 出现在映射证据里。

## 2. 数据模型与唯一事实源

新增/扩展包装零件文档为以下结构（具体存储表可复用既有版本化文档）：

```json
{
  "engine_version": "packaging-business-parts/1",
  "business_parts": [{
    "business_part_code": "JWXR21-P01",
    "name": "左盖面纸",
    "authority": {},
    "geometry_binding": {
      "status": "bound|partial|unbound|ambiguous",
      "component_ids": [],
      "entity_ids": [],
      "bbox": [0, 0, 1, 1],
      "confidence": 0.0,
      "reasons": [],
      "bound_by": "deterministic|manual",
      "bound_at": ""
    }
  }],
  "geometry_evidence": {
    "component_total": 402,
    "kept_component_total": 263,
    "components": []
  },
  "stats": {
    "business_part_total": 28,
    "bound_total": 0,
    "partial_total": 0,
    "unbound_total": 0,
    "ambiguous_total": 0
  },
  "source": {"ir_id": "", "ir_hash": "", "authority_file_hash": ""}
}
```

关键纪律：

1. 页面、BOM、工艺、成本以 `business_parts` 为唯一部件集合；
2. `geometry_evidence.components` 完整保留，但不得混入业务部件列表；
3. 一个业务部件可以绑定多个连通分量；一个分量默认只属于一个业务部件，人工明确共享时例外并留痕；
4. 未绑定不等于删除：仍展示该业务部件，并标“尚未在 CAD 图中定位”；
5. 没有权威资料时不得回退成“263 个业务零件”。只能显示“已识别几何区域 n 个，尚未形成业务部件清单”，
   并提供导入权威清单/人工建部件的出口；
6. 既有原始 parts 文档须可迁移或兼容读取，但迁移不得自动把每个 `DWG-Pxx` 变成业务部件。

## 3. 权威资料导入

新增确定性导入器，例如 `packaging_part_authority.py`：

- 只解析工作簿结构和单元格，不调用 LLM；
- 用表头语义定位列，不把 A/B/C 等坐标写死为业务逻辑；
- 识别序号连续的业务行，跳过说明、签名和完全空行；
- 尺寸解析支持 `x` / `×` / `X` 与可选 `mm`，原文永远保留；
- 嵌入图片按锚点行关联到对应部件，保存为受控媒体或内容寻址附件；
- 返回 warning 而不是静默吞掉：重复序号、重复名称、尺寸不可解析、图片缺失、合并单元格来源不清等；
- 文件 hash 相同重复导入必须幂等；新版本不得覆盖旧版本证据。

本批先保证酒盒权威工作簿真实可用，但实现不能把 28 个名称硬编码到 Python。测试夹具可以从真实工作簿
提取并固化最小脱敏快照，生产逻辑必须走通用导入器。

## 4. 业务部件与 CAD 几何映射

### 4.1 映射对象

映射的是“业务部件 ↔ CAD 图元集合”，不是“业务部件 ↔ 单个连通分量”。包装刀模中外轮廓、压痕、
文字、尺寸线可能互不连通，一个部件天然可能跨多个 component。

### 4.2 确定性候选

候选评分可使用：

- 权威长宽与 CAD 区域包围盒（允许旋转 90°）；
- 权威嵌入部件图与 CAD 轮廓的归一化形状描述；
- 图中文字/标注与部件名称、尺寸；
- 图层角色、邻接关系、成组/块信息；
- 左/右同规格部件必须保留为两个业务部件，不能仅因尺寸相同合并。

每个候选必须返回逐项依据和冲突。低于阈值或出现一对多歧义时标 `ambiguous/unbound`，不得强绑。
LLM 可以解释候选，但不能凭语言直接写最终 entity id。

### 4.3 人工映射

页面允许在 CAD 图上框选/点选图元或区域，然后绑定到当前业务部件。人工映射需记录操作人、时间、
图纸版本、权威资料版本、entity/component id。图纸换版后按 hash 判 stale，旧映射保留但不继续冒充当前。

## 5. API 契约

在既有 packaging-parts 路径兼容扩展，至少提供：

- `POST /api/projects/{pid}/requirement/packaging-part-authority/import`：导入权威部件清单；
- `GET /api/projects/{pid}/requirement/packaging-parts`：默认返回 `business_parts` 和统计，不分页暴露
  几百个 geometry component；
- `GET /api/projects/{pid}/requirement/packaging-geometry`：读取 CAD 平面图所需的图层、图元、范围；
- `GET /api/projects/{pid}/requirement/packaging-parts/{code}`：业务部件、权威资料、绑定和证据；
- `PUT /api/projects/{pid}/requirement/packaging-parts/{code}/geometry-binding`：人工确认/修改映射。

CAD geometry 响应必须有稳定图元 id、图层、类型、坐标/路径和整体 bbox；可以按图层/视口分片，但前端
不得重新解析 DWG。所有写接口沿用项目写权限和审计；读接口沿用项目读权限。

## 6. 包装行业右侧 CAD 平面图

### 6.1 模式切换

- 非包装项目：既有 3D、导入 STEP、生成工程图能力不变；
- 包装 DWG/DXF 项目：右栏标题和 `aria-label` 改为“CAD 平面图”或“包装展开图”；
- 不显示“3D 预览”“生成 CAD 几何”“生成 2D 工程图”“导入已有 3D 模型”等不适用动作；
- 不调用 `packagingPartSolidPreview()`、`loadSTL()` 或包装平板挤出接口；旧 3D 结论可保留为历史数据，
  但不作为包装主流程入口。

### 6.2 显示要求

在当前 `drawing-model-column` 内复用布局，加入专用 CAD SVG/Canvas 查看器：

- 初始显示 DWG 完整平面图，不是只画一个 bbox 或简化 polygon；
- 保留可识别的线型/图层色，支持图层显隐、适应窗口、缩放、平移和复位；
- 点击左侧业务部件：完整图仍在，其他图元降权，绑定图元高亮，并缩放到部件范围；
- 点击图元可反查所属业务部件；未绑定图元明确显示“几何证据，尚未归属业务部件”；
- 选择部件后同时展示名称、权威尺寸、材料、排版、工艺、备注和映射状态；
- 禁止浏览器端从图元重新计算业务尺寸或重新拆件；显示数据来自服务端 CAD IR/绑定文档。

这里所谓“放在原 3D 展示的位置”，是复用右栏空间和布局，不是继续把二维刀模假装成 3D。

## 7. 下游切换与门禁

- BOM、单件工艺、单件成本、整包成本只遍历 `business_parts`；酒盒权威版本应产生 28 个业务部件，
  不能产生 263/402 行任务；
- 排版模数、数量和左右件不得从重复几何数量猜测，优先读取权威资料；
- 外购件（如 EVA、磁铁）即使在 DWG 中没有可绑定轮廓，也仍是合法业务部件，可进入采购/成本链；
- 几何未绑定只阻断依赖几何的尺寸/工艺，不应把有权威尺寸的材料和采购成本全部阻断；
- 所有下游结果带 `business_parts_id/hash`；重新导入清单或修改映射后旧结论标 stale；
- 不删除旧几何 parts、STL 或成本结果，保留审计，但默认页面不混展示。

## 8. 迁移和兼容

1. 历史仅有 `packaging-parts/1` 的项目标为 `business_parts_missing`；不得自动展示几百个旧件；
2. 有权威清单时生成新版业务部件文档并保留 `legacy_parts_id`；
3. 已有 BOM/成本若绑定旧 `parts_id`，显示“基于旧几何分量拆件，请按业务部件重新生成”；
4. 快速报价案例的盒型、价格不因本次迁移失效；精准工艺/成本使用新业务部件版本；
5. 圆盘盒没有同等权威清单时不伪造部件名，显示待导入/待人工建立，而不是继续冒充几百个零件。

## 9. 红测范围

`test_packaging_business_parts_and_cad_plan_view_red.py` 至少验证：

1. 真实酒盒工作簿为 28 行业务部件、28 张部件图；
2. 权威导入器不把备注/制表行当部件；
3. 服务层区分 `business_parts` 与 `geometry_evidence`；
4. 默认清单不再使用 `DWG-P01…` 几何编号作为业务件；
5. 多个 component 可绑定一个业务部件；相同尺寸的左右件不合并；
6. 无权威清单时返回明确缺口，而不是回退几百件；
7. BOM/工艺/成本消费 `business_parts_id`；
8. 包装模式右栏标题为 CAD 平面图，存在完整图纸查看器和选中高亮；
9. 包装模式隐藏/禁用 3D 挤出、STL、生成 2D 等动作；
10. 非包装 3D 行为不回归。

## 10. 非目标

- 不删除、合并或篡改 DWG 原始图元；
- 不承诺第一版自动映射 100%，允许人工确认；
- 不用大模型编造 28 个名称、材料、尺寸或工艺；
- 不把 Excel 嵌入图片当正式 CAD 几何，它只可作为映射辅助证据；
- 不在本批重做成本公式或快速报价价格。

## 11. 验收

酒盒项目最终应满足：

- 左侧显示 28 个业务部件，而不是 263/402 个几何分量；
- 点击“左盖面纸”等部件，右侧原模型区域显示完整 DWG 平面图并高亮对应区域；
- 页面不出现包装“3D 预览/平板挤出”的主流程按钮；
- 28 个部件资料可查看，未绑定项和外购项状态真实；
- BOM、工艺、成本任务数按业务部件产生；
- 原始几何分量仍可在诊断/证据中回查。

---

## 12. 落地状态（`## 368`）

红测 14 条全绿；相邻批次不回归：packaging 全域 1530 条只剩 5 处既有挂账
（`packaging_bom_part_size_provenance::b3`、`packaging_parse_to_downstream_seams::b4`、
`packaging_part_manual_fill_persists::a2`、`packaging_quote_send_recovery::c1`、
`packaging_route_bom_version_pinning::f2`，都已在各自 Spec 里挂账）。

| 落点 | 文件 | 做了什么 |
| --- | --- | --- |
| 权威资料导入器 | `tech_app/backend/services/packaging_part_authority.py`（新） | `import_workbook()`：表头语义定位列、只认连续序号、说明/签名/制表行进 `skipped`、`merged_from` 只记锚点不复制值、尺寸解析保留原文、`code_prefix` 从标题派生（真样本 → `JWXR21`） |
| 读表依赖隔离 | `tech_app/tools/xlsx_grid.py`（新） | 唯一认识 xlsx 的模块（openpyxl 在函数内才导入）；后端只拿纯 dict 网格 —— `packaging-cost-rule-snapshot.md` §4.3「生产后端不得依赖 openpyxl」继续成立 |
| 业务部件层 | `tech_app/backend/services/packaging_parts.py` | `BUSINESS_ENGINE_VERSION="packaging-business-parts/1"`、`business_parts` / `geometry_evidence` / `stats` 三层文档、`bind_geometry()` 确定性尺寸绑定（多分量可绑一件、同尺寸左右件不合并、拿不准报 `ambiguous`）、`business_parts_missing` + 「尚未形成业务部件清单」、`geometry_component_ref` 回查引用、`save/load/summarize/set_geometry_binding` |
| 下游版本绑定 | `packaging_bom.py` / `packaging_cost.py` | `business_parts_id/hash` 落进 `source_versions` 与 top-level `business_parts` 披露；没有清单时 `gap=business_parts_missing`，**不**用几何件数冒充业务件数；`bind_rows()` 把业务版本写进行上 `dwg_binding` |
| 读/写接口 | `tech_app/backend/main.py` | `GET .../packaging-business-parts`、`GET .../packaging-geometry`、`PUT .../packaging-business-parts/{code}/geometry-binding`（写权限沿用 `BOX_MATCH_DECIDE_ROLES`） |
| 右栏 CAD 平面图 | `tech_app/frontend/app.js` / `index.html` / `drawing-flow.css` | `#packagingCadPlanViewer` + `renderPackagingCadPlan()` / `fitPackagingCadPlan()` / `highlightPackagingBusinessPart(partCode, entity_ids)`；`drawing-model-column` 改成 `aria-labelledby`、标题改「图纸零件…」；零件面板去掉平板挤出按钮 |
| 左栏业务部件 | `tech_app/frontend/app.js` | 有权威清单时左栏列**业务部件**（编码 + 名称 + 权威尺寸 + 定位状态），点击进 `openPackagingBusinessPart()`：右栏给权威资料/绑定状态并在平面图里高亮；没有清单才退回几何分量并如实说明 |

**已记录的边界（不许当成本批已做）**

1. 左栏改成业务部件的**前提**是项目里已有业务部件文档；`## 369` 已补上入口
   （`POST .../packaging-business-parts/import` + 左栏「导入权威清单（业务部件）」按钮，
   见下面 §12.1），但导入的是**服务器可见路径**上的工作簿 —— 客户资料不入库，部署机上要先把
   工作簿放上去。
2. 单件工艺 / 成本目前仍从几何零件入口发起；业务部件行的「单件工艺 / 成本」只给了说明文案，
   尚未按 `business_parts_id` 落到下游任务表 —— 这是 Spec §7「BOM/工艺/成本只遍历 business_parts」
   剩下的那一半。
3. `bind_geometry()` 的确定性判据是**尺寸相符**（两轴 ±max(2mm, 5%) → `bound`；只对一轴 → `partial`；
   并列多个 → `ambiguous`），真图上多数件仍会落在 `unbound`/`ambiguous`，需要人工用
   `PUT .../geometry-binding` 确认；Spec §10 已声明第一版不承诺自动映射 100%。
4. 平面图按**分量 bbox** 画（`geometry_evidence.components`），不是逐段折线 —— 图元级别坐标要等
   CAD IR 把折线顶点带到 `geometry_evidence`（第 1 层 `packaging-parts-true-outline` 已铺路）。
5. 非包装项目的 3D 与 `loadSTL()` 未动；平板挤出的后端路由 / STL 保留，只是不再出现在包装主流程按钮里。
6. 本批**未**动 `tests/` 下任何红测；`test_packaging_business_parts_and_cad_plan_view_red.py` 的
   「备注行不是部件」一条由上游会话在 17:04 自行改成读 `cell(32,2)`（A 列确实是序号 29），
   本批按改后的断言实现，没有放宽任何断言。

### 12.1 `## 369` 补的入口与端到端核对

- `POST /api/projects/{pid}/requirement/packaging-business-parts/import`：`workbook_path`
  或 `content_base64` + `sheet` + `bind`；写权限沿用 `BOX_MATCH_DECIDE_ROLES`，落一版业务部件
  文档并写审计（`workflow:packaging_business_parts_imported`）。没有连续序号部件行 → 409 并点名
  应该给哪张业务表；路径读不到 → 400。
- 左栏：没有权威清单时插一条说明（`data-qq-business-missing` + `gap.message/action`）与
  「导入权威清单（业务部件）」按钮（`#packagingBusinessImport`）—— 说清"下面列的是几何分量，
  不是业务零件"，不再只是空白或默默列几百行。
- 端到端实测（临时 `JsonMetaBackend` + 真样本工作簿）：
  导入 28 件 → `bind_geometry` 出 `bound 2 / partial 2 / unbound 24` → `save_business_parts`
  （版本 1）→ `load_business_parts` / `business_part_row("JWXR21-P01")` 回权威材料原文 →
  `packaging_bom._business_parts_scope()` / `packaging_cost._business_parts_scope()` 读到
  `business_parts_id/hash` 与件数 → `PUT geometry-binding` 改名后 `bound 3`、版本 2 且
  `business_parts_id` 变化（下游据此判 stale）。

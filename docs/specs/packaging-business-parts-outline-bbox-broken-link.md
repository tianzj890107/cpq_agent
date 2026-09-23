# 业务部件绑不上图的真因：几何零件的轮廓矩形藏在 `outline.bbox` 里，区域记录却读 `row["bbox"]`

状态：Spec + 红测（已实现）（原状：实现提示词只在会话交付、业务实现不在本批；红基见 §6；
`## 461` 已落地，落点与实测见 §7）
红测：`tests/test_packaging_business_parts_outline_bbox_link_red.py`
承接：`packaging-business-parts-and-cad-plan-view.md`、`packaging-business-parts-binding-size-source.md`、
`packaging-parts-must-be-derived-from-the-drawing.md`、
`packaging-business-parts-must-come-from-all-drawing-evidence.md`
本批 changelog 条目号：`## 461`

## 0. 本轮现场结论（真实跑，2026-09-23 本机，隔离 `DATA_DIR`）

**怎么跑的**：起隔离 `DATA_DIR`，把 `裕同包装项目-待开发/酒盒.dwg`、`圆盘盒.dwg` 各建成一个**包装行业**
项目（需求单 `industry="packaging"`），跑 `packaging_drawing_flow.run_flow()`，再看语义 / 零件 / 业务部件 / BOM。

八步 flow 两份都 `8/8 completed`；业务部件的来源闭集已经按新口径落地
（`derived_from_drawing=true`、`gold_standard_used=false`、`refused_sources=[attachment, knowledge_base,
drawing_hash, manual_import]`），但**一件都绑不上图**：

```text
酒盒   ：业务部件 26 行 → bound 0 / partial 0 / unbound 26，有尺寸的件 0
圆盘盒 ：业务部件 66 行 → bound 0 / partial 0 / unbound 66，有尺寸的件 0
行上的原因码一律 reasons=["no_outline_evidence"]（酒盒 detail.reasons_breakdown 里
另一笔账 outline_without_name_anchor = 256：263 条轮廓里只有 7 条被认为"有名字"）
```

也就是说：解析只吃 DWG 这条已经做到，但"**名称 + 尺寸 + 图纸**"三件套里**只有名称拿到了**。

### 0.1 真因（字段级，两条证据链都实测过）

同两份真 DXF，只调纯函数复现（不经过转换器，5~7 秒）：

```text
cad_ir.parse_dxf(真 DXF) → packaging_parts.extract(ir)   → 几何零件 263 / 312 件
r.regions_from_geometry_parts(那份零件文档)              → 263 / 312 条 region，
                                                            有 bbox 的 0 条、有 center 的 0 条
r.extract_text_anchors(ir) → r._derived_rows(...)         → 26 / 66 行
r._part_position(每行, 锚点表)                            → 26/26 都有位置（锚点这一侧没坏）
```

- 关键：**几何零件行里根本就有轮廓矩形，只是放在 `row["outline"]["bbox"]`**
  （真样本 `DWG-P01`：`outline.bbox = [3571.66, 4283.84, 4713.23, 4748.97]`；
  263/312 行都有它，行顶层**没有** `bbox` 这个键）；
- 而 `regions_from_geometry_parts()`（`packaging_business_part_resolver.py:581`）取的是 `row.get("bbox")`
  → 空 → region 的 `bbox`/`center` 全空 → `_assign_outlines()`（`:814`）里
  `_region_center(region) is None` → **每一件都 `continue`**
  → 结果必然是 0 绑定、0 尺寸、图纸证据一律 `drawing_ref.kind="none"`。
- 图纸流**正是把这份文档**交给解析器的（`packaging_drawing_flow/steps.py:500`
  `_resolve_business_parts(ctx, ir, saved, module)` → `resolve_business_parts(pid, ir, geometry_parts, …)`），
  所以线上一模一样是 0 绑定。

### 0.2 修这一处会发生什么（本批实测，把矩形从 `outline.bbox` 接到 region 上模拟）

```text
酒盒  ：regions 有 center 的 263/263；绑定 total=26 bound=26 partial=0 unbound=0；有尺寸 26 件
圆盘盒：regions 有 center 的 312/312；绑定 total=66 bound=39 partial=0 unbound=27；有尺寸 39 件
```

所以这不是"算法不够聪明"，是**一个字段路径写错**，把整条绑定链掐死在第一步。
（修好之后仍有一个质量问题：`size_confirmed=false` 的几何猜测尺寸也被判成 `bound` —— 那是
`packaging-business-part-size-must-be-confirmed-by-dimension.md` 管的事，本批不混在一起。）

## 1. 需求

1. **轮廓矩形必须有一个唯一的读法**：`packaging_parts` 新增纯函数
   `part_outline_rect(row)`，返回该件的轮廓矩形（行顶层 `bbox`，否则由 `outline.bbox` **明确派生**）
   或 `None`；**不允许**再由每个下游各自去猜字段路径。
   `regions_from_geometry_parts()` 必须改用它，不许继续写 `row.get("bbox")`。
   冻结：几何零件文档的对外键集与 `outline` 的内容一个字不改（不许为了"能读到"去删 `outline.bbox`，
   也不许往行里塞一个与既有口径重复的 `bbox` 去骗过下游）。
2. **区域记录必须自证可用**：`regions_from_geometry_parts()` 产出的每条 region，只要有矩形就必须给出
   `bbox` 与 `center`；**拿不到矩形的行必须标 `excluded` 原因**（建议 `"no_outline_bbox"`），
   `substantial` 必须是 `False`。**不许静默产出 bbox/center 全空的记录**
   （静默空值就是这条链断了两天没人发现的原因）。
3. **绑定链必须自证未断**：一趟解析结束时，若 `business_part_total > 0` 且 `bound_total + partial_total == 0`，
   `detail` 必须给出稳定码 `PACKAGING_BUSINESS_PARTS_OUTLINE_LINK_BROKEN` 与三个计数：

   ```python
   detail["outline_link"] = {
       "code": "PACKAGING_BUSINESS_PARTS_OUTLINE_LINK_BROKEN",
       "business_part_total": 26,          # 业务部件行数
       "region_total": 263,                # 区域总数
       "regions_with_center_total": 0,     # 有中心的区域数（真因就在这个数上）
   }
   ```

   码本身是模块级常量 `OUTLINE_LINK_BROKEN`；**只在断链时出现**，绑定通的时候这个块不许带码。
   另外 `detail["regions_with_center_total"]` **无条件**出现（页面/运维据此判断这条链是否可用）。
4. **`no_outline_evidence` 的使用边界**：它是**行级**原因（"这一件确实没找到轮廓"），
   当它覆盖**全部**行时，必须同时按第 3 条报断链码 —— 一整份清单一件都没绑上图，
   那就不是"个别件没找到轮廓"，不许静默了事。
5. **真样本验收口径**（本机实测值，门槛留 ~20% 余量）：
   - 酒盒：`derived`（绑上且长宽齐全）≥ **20** 件（实测修后 26/26）
   - 圆盘盒：`derived` ≥ **31** 件（实测修后 39/66）
   - 每一件 `derived` 行必须有 `length_mm`/`width_mm`，`drawing_ref.kind != "none"`，
     且 `drawing_ref` 里 `component_ids` 或 `bbox` 至少一样非空。
6. **口径不回归**：几何零件提取本身不许变（两份样本仍是 263 / 312 件、
   `closed_ratio` 0.510 / 0.817），`extract()` 的确定性不变，区域仍是"一行一条、id 与零件号一一对应"。
7. **护栏（本批必须保持绿、不许为转绿去改）**：既有 6 个套件
   （`packaging_parts_extraction_red` / `packaging_parts_outline_red` / `packaging_parts_components_red` /
   `packaging_bom_business_parts_rows_red` / `packaging_drawing_flow_red` /
   `packaging_parts_must_come_from_the_drawing_red` / `packaging_business_parts_and_cad_plan_view_red` /
   `packaging_business_parts_binding_size_source_red` /
   `packaging_business_parts_must_come_from_all_drawing_evidence_red`）本批实测全绿（§6）。

## 2. 非目标

- 不改成本 / BOM / 工艺路线公式与门禁。
- 不改语义层的轮廓/孔位候选上限（200）与截断披露（那是 `## 450`/`## 453` 已落的事）。
- 不做"这张轮廓该配给哪一件"的**判定质量**改进（绑错件的概率、`size_confirmed=false` 的猜测尺寸
  算不算"绑上"）—— 见承接的 `packaging-business-part-size-must-be-confirmed-by-dimension.md`。
- **前端文案不在本批**：本批只要求服务端给出码与三个计数；"绑定链断了，请联系管理员"这句
  人话与面板接线由前端批次做（现有人话规则见 `app.js:2443`/`:2469` 一带的口径）。

## 3. 迁移

`regions_from_geometry_parts()` 的调用点只有 `resolve_business_parts()`（`:1180`）与测试；
改字段来源即可，不涉及落库格式、不涉及历史数据回填（每次解析都会重算）。

## 4. 验收命令与期望

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_outline_bbox_link_red -v
    # 本批红测：现状 Ran 16 … FAILED (failures=11, skipped=1)；实现后 Ran 16 … OK
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_extraction_red \
    tests.test_packaging_parts_outline_red tests.test_packaging_parts_components_red \
    tests.test_packaging_bom_business_parts_rows_red tests.test_packaging_drawing_flow_red
    # 现状 Ran 142 OK (skipped=2)（不回归）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_must_come_from_the_drawing_red \
    tests.test_packaging_business_parts_and_cad_plan_view_red \
    tests.test_packaging_business_parts_binding_size_source_red \
    tests.test_packaging_business_parts_must_come_from_all_drawing_evidence_red
    # 现状 Ran 83 OK（不回归）
```

真样本来自 `tech_app/data/cad-ir-realsample/conversions/<cid>/converted.dxf`（缺样本时红测自 `skip`）：
酒盒 `47c39dc1ab6738fc48c8`、圆盘盒 `a6140fbc4e9b8d2e9bee`。

## 5. 现场数字（写进实现的依据）

| 项 | 酒盒 | 圆盘盒 |
| --- | ---: | ---: |
| 几何零件 | 263 | 312 |
| 有 `outline.bbox` 的零件行 | 263 | 312 |
| 行顶层有 `bbox` 的零件行 | 0 | 0 |
| region 有 `bbox` / `center`（现状） | 0 / 0 | 0 / 0 |
| 业务部件行 | 26 | 66 |
| 现状 `bound + partial` | 0 | 0 |
| 把矩形接到 region 上之后 `bound + partial` | 26 | 39 |
| 尺寸标注矩形 `dimension_rects(ir)` | 107 | — |
| `closed_ratio` | 0.510 | 0.817 |

## 6. 红基（本批实测，2026-09-23 本机隔离 `DATA_DIR`）

```text
tests.test_packaging_business_parts_outline_bbox_link_red   Ran 16 … FAILED (failures=11, skipped=1)

红（11）：A1 没有 `part_outline_rect()` 这个唯一读法
        A3/A4 区域记录的 bbox/center 全空（合成夹具：顶层有 bbox 的行、只有 outline.bbox 的行都读不到；
             没有矩形的行也没有 `excluded` 原因）
        A6 真样本有矩形的区域 0/263、0/312
        B1 `OUTLINE_LINK_BROKEN` 常量不存在
        B2 detail 里没有 `regions_with_center_total`
        B3/B4/B5 26 / 66 行一件没绑上图，却没有任何断链码与计数说明
        C1/C2 真样本 derived 0 件（门槛 20 / 31）
跳过（1）：A2（`part_outline_rect()` 落地的下一层断言，A1 转绿后自动生效）
护栏绿（4）：A5 区域与零件行一一对应；D1 件数与 closed_ratio 不变；D2 同一份 IR 两次提取逐字相同；
            D3 读区域不改零件文档
```

不回归（本批未改业务实现，逐条复跑）：

```text
tests.test_packaging_parts_extraction_red + packaging_parts_outline_red + packaging_parts_components_red
  + packaging_bom_business_parts_rows_red + packaging_drawing_flow_red          Ran 142 OK (skipped=2)
tests.test_packaging_parts_must_come_from_the_drawing_red
  + packaging_business_parts_and_cad_plan_view_red + packaging_business_parts_binding_size_source_red
  + packaging_business_parts_must_come_from_all_drawing_evidence_red            Ran 83 OK
```

本批只写 Spec + 红测：未改任何业务实现、未改既有测试断言、未起服务、未发 HTTP、未连 PG / 34、
未写业务数据、未 push / MR / tag / Release、未部署。

## 7. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 461`）

改了两个文件：`tech_app/backend/services/packaging_parts.py`（新增唯一读法）、
`tech_app/backend/services/packaging_business_part_resolver.py`（改用唯一读法 + 断链自证）。

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §1.1 唯一读法 | `packaging_parts.part_outline_rect(row)` | 顶层 `bbox` 优先，否则由 `outline.bbox` 明确派生，两处都没有回 `None`；只读、不往行里塞键 |
| §1.1 断链根因 | `regions_from_geometry_parts()` | 改用它取矩形，不再写 `row.get("bbox")`（真样本 263/312 行只有 `outline.bbox`，行顶层没有 `bbox`） |
| §1.2 区域自证 | `REASON_NO_OUTLINE_BBOX` | 拿不到矩形的行标 `excluded="no_outline_bbox"` 且 `substantial=False`；有矩形的一律给 `bbox` + `center` |
| §1.3 断链自证 | `OUTLINE_LINK_BROKEN` / `detail.outline_link` / `detail.regions_with_center_total` | `business_part_total > 0` 且 `bound + partial == 0` 时带码与三个计数；绑定通时 `code=""`；`regions_with_center_total` 无条件出现 |
| §1.4 `no_outline_evidence` 边界 | 同上 | 全行都是该原因时必然命中"一件没绑上"分支 → 一定报断链码 |
| §1.5/§1.6 真样本 | 见下 | 酒盒 derived 26（门槛 20）、圆盘盒 derived 39（门槛 31）；提取口径不变（263 / 312、closed_ratio 0.510 / 0.817） |
| §2 非目标 | 未动 | 成本 / BOM / 工艺公式、语义层截断上限、前端文案、`size_confirmed` 的判定质量都留原样 |

真样本实测（隔离只读，`regions_from_geometry_parts()` 走真几何零件文档）：

```text
酒盒  ：regions 有 center 263/263；business_part_total 26；bound 26 / partial 0 / unbound 0
         derived 26 件（门槛 20）；regions_with_center_total 263
圆盘盒：regions 有 center 312/312；business_part_total 66；bound 39 / partial 0 / unbound 27
         derived 39 件（门槛 31）；regions_with_center_total 312
断链码 ：两份样本都不出现（`outline_link.code == ""`）—— 绑定已通
```

修复前后对照（同一份真样本、同一条调用路径）：`bound + partial` 0/0 → 26/39，
行级原因从"26 / 66 行全是 `no_outline_evidence`"变成"个别件没找到轮廓"。

复跑命令与结果（本机 `./open-claude/.venv/bin/python`）：

```bash
python -m unittest tests.test_packaging_business_parts_outline_bbox_link_red
    # Ran 16 … OK（红基 Ran 16 … FAILED (failures=11, skipped=1)；A2 由 skip 转绿）
python -m unittest tests.test_packaging_parts_extraction_red \
    tests.test_packaging_parts_outline_red tests.test_packaging_parts_components_red \
    tests.test_packaging_bom_business_parts_rows_red tests.test_packaging_drawing_flow_red \
    tests.test_packaging_parts_must_come_from_the_drawing_red \
    tests.test_packaging_business_parts_and_cad_plan_view_red \
    tests.test_packaging_business_parts_binding_size_source_red \
    tests.test_packaging_business_parts_must_come_from_all_drawing_evidence_red
    # Ran 225 … OK (skipped=2)（142 + 83，与 Spec §1.7 的两组逐项对上）
```

红测自身缺陷（如实记录）：本批红测钉的是"矩形可读 + 区域自证 + 断链自证 + 真样本绑定件数"，
**没有**校验绑上之后尺寸对不对（`size_confirmed=false` 的几何猜测尺寸也被算成 `bound`）——
那正是承接的 `packaging-business-part-size-must-be-confirmed-by-dimension.md` 管的事。

另：本批 Spec 头部写的 changelog 条目号是 `## 461`，同一时刻落地的
`tech-projection-step-state-must-agree-with-its-reasons.md` 也写了 `## 461`（号撞了）。
本批按自己的号写 `## 461`，另一批的实现方请另取号。

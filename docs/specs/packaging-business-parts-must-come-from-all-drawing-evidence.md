# 业务部件必须由 DWG 的**全部证据**推出来：名称文字只是其中一类，18/28 不是能力上限

血缘：`packaging-parts-must-be-derived-from-the-drawing.md`（`## 453` 写入、`## 456` 落地；本批**收窄**它
§1 第 4 条「图纸名称证据的上限是实测值（18 件同名 + 2 件近似名）」与 §2.3 只认名称锚点的推件口径，
其余条款（运行时只吃 DWG、金标只做对答案、排除规则、名称/材料拆分、披露）逐字保留）、
`packaging-business-parts-and-cad-plan-view.md` §7（BOM / 单件工艺 / 单件成本 / 整包成本只遍历
`business_parts`）、`packaging-28-part-auto-resolution-and-2d-board-cleanup.md`（酒盒 28 件的业务口径）。

状态：Spec + 红测（已实现）（原状：`## 456` 落地的 `_derived_rows()` 是「一条名称锚点 = 一件」，
几何/尺寸/引线/图块/图层/空间关系/重复镜像都没进"推件"这一步 —— 真样本实测 27 行、对 28 件金标
recall 19/28、precision 19/27、只有 5 件带尺寸、22 件 `unbound`、没有证据类别字段、没有父子分组判定；
`## 459` 已落地，落点与边界见 §7）
红测：`tests/test_packaging_business_parts_must_come_from_all_drawing_evidence_red.py`
本批 changelog 条目号：`## 458`

## 0. 一句话目标

图纸拆件的**输入是整张 DWG**：文字、几何轮廓、尺寸标注、引出线、图块、图层、空间关系、重复/镜像
关系都是证据；`28 件 BOM` 只在旁路做验收金标。件数由证据决定 —— 既不许用 BOM 凑数，也不许因为
"图上没有这四个字"就把这一件判成不存在。

## 1. 裁定（业务口径，本批依据）

四层必须分开，谁也不许越层：

```text
运行时输入        DWG
                    ↓
证据提取          文字 + 几何 + 尺寸 + 引线 + 图块 + 图层 + 空间关系 + 重复/镜像
                    ↓
业务部件推导      给出 名称 / 尺寸 / 材料 / 工艺 / 图上位置（逐件带证据）
                    ↓
BOM 输出          由推出来的业务部件生成（不是解析器的输入）

旁路（只在测试/离线）   DWG → 系统输出  ⇄  28 件金标：precision / recall / 漏件 / 错件 / 多拆件
```

- `## 453` 定的「运行时只吃 DWG、金标只做对答案」**继续有效**；本批推翻的只有一条：
  **件数上限不由"名称文字证据"决定**。
- 图纸里没有"底板"三个字，不等于图上没有底板 —— 还可能有闭合轮廓、长宽尺寸、引出线、材料注记、
  与相邻件的空间关系、重复/镜像关系。
- 「金标里有、图上没有同名文字」的件：**该被推出来就要推出来**；确实在 DWG 里任何证据都观测不到的
  （典型是纯外购件），必须逐件标「DWG 不可观测」，**不许**从金标补进去，也不许悄悄少报。
- 名称整串可能是**父节点**（例：金标把「左/右盖外盒里层灰板」拆成 `…灰板1` / `…灰板2`，而图上只有
  一条含该词组的名称锚点）：父名不许直接当成独立件，必须先做分组/层级判定再决定拆几件。

## 2. 现场证据（本机真样本只读复跑，2026-09-23）

用仓库里两份真样本的转换产物跑当前实现（`## 456` 落地后的 HEAD），酒盒（`酒盒.dwg`，DWG
sha-256 `0991c8b0a964…`）与《酒盒 报价资料.xlsx》那份 28 件金标对答案：

```text
authority_source        = dwg          （来源闭集正确）
business_part_total     = 27           （不是 28，也不是 18+2）
parts_with_size_total   = 5            （27 件里只有 5 件拿得到长宽）
parts_with_drawing_ref_total = 5
status 分布             = {derived: 5, unbound: 22}
name_anchor_total       = 38           excluded_anchor_total = 89
对 28 件金标：recall    = 19/28 (0.679)
              precision = 19/27 (0.704)
漏件  9 件：内卡 / 底板 / 底板面纸 / 底托灰板 / 磁铁 /
            左盖外盒里层灰板1 / 右盖外盒里层灰板1 / 左盖外盒里层灰板2 / 右盖外盒里层灰板2
多件  8 条：刀 / `底板:2.5MM灰板` / `底板面纸:225G铜版底PET光银` /
            左盒外盒里层灰板 / 右盒外盒里层灰板 / 左盒盒背灰板 / 右盒盒背灰板 / 顶托灰板
```

读法（每一条都指向本批要补的能力）：

1. **只有文字一类证据**：27 行全部由名称锚点产出，`evidence` 里没有"证据类别"这个概念，
   几何分量只用来"事后找位置"（`component_ids` 空着 22 行）。
2. **尺寸基本没进来**：`5/27` 有尺寸 —— 而尺寸正是把"这个轮廓是不是底板"判出来的主证据。
3. **父名直接成件**：`左盒外盒里层灰板` 被当成一件（金标是 `…灰板1` + `…灰板2` 两件）；
   同一条锚点带出的还有 `左盒盒背灰板` 这类金标没有的行 → 漏件与多件同时发生。
4. **名称没拆干净**：`底板:2.5MM灰板`、`底板面纸:225G铜版底PET光银` 把材料串在件名里
   （半角冒号 + 没有 `材料` 二字的那种写法，`## 453` §2.4 只覆盖了全角 `名称：…\P材料：…`），
   `刀` 这种只有一个字的碎词也当成了件名。
5. **金标零影响 ✅**、来源闭集 ✅、确定性 ✅ —— 这三条是 `## 453` 的成果，本批不许回退。

## 3. 需求

### 3.1 A 组：证据面（运行时）

1. 每一行必须带 `evidence.kinds`：该类行用上了哪些证据，取值来自闭集
   `("text_anchor", "geometry_region", "size_dimension", "leader_callout", "block_attribute",
   "layer_role", "spatial_relation", "repetition_mirror")`，至少一类、不重复、按固定顺序。
2. `detail.evidence_kinds` 必须给出**本趟实际用上**的类别集合（真值，不许把没用的类也列上）。
3. 清单里必须出现**非文字证据**产出的行（`geometry_region` 或 `size_dimension`）——
   今天 27 行全是 `text_anchor`。
4. `parts_with_size_total / business_part_total ≥ 0.5`：尺寸是主证据之一，不许只有 5/27。

### 3.2 B 组：与金标对答案（测试侧；解析器运行时读不到金标）

1. 真样本验收目标：酒盒 28 件里 **recall ≥ 22/28**，且 **precision ≥ 0.85**
   （今天 19/28、0.70；多件与漏件必须同时降下来）。
2. 仍拿不到证据的件（≤6 件）必须**逐件**出现在输出里并带稳定原因码
   （`detail.unobservable_total` + 逐行 `reasons`），不许静默少报；纯外购件标「DWG 不可观测」。
3. `detail` 必须给出原因码分布（`reasons_breakdown`：码 → 件数），让"为什么没推出来"一眼可查。

### 3.3 C 组：分组与层级（不许把父名当第 29 件）

1. 行上必须带分组判定：`evidence.group`（`{"kind": "leaf"|"group", "members": [...], "parent": …}`）。
2. 图上只有一条父名（`左盒/右盒外盒里层灰板`）时，必须结合尺寸/轮廓/对称关系拆出
   `左盖外盒里层灰板1` / `左盖外盒里层灰板2` / `右盖外盒里层灰板1` / `右盖外盒里层灰板2`
   四件（金标就是这四件），父名本身不再单独占一行。
3. `左/右`、`内盒N`、`上/下` 这类方向词与序号必须参与分组判定（不许按出现顺序猜）。

### 3.4 D 组：护栏（`## 453` 的成果，不许回退）

1. `AUTHORITY_SOURCES` 仍是 `("dwg", "missing")`；被拒来源照旧留痕。
2. 金标对结果**零影响**：传 `seed_path` / `attachments` / `kb` 与不传，输出逐字相同。
3. 同一份 IR 跑两次逐字相同（确定性）。
4. `business_part_total == len(authority_rows)`：不许靠删件把 precision 做上去。
5. 编码唯一、前缀 `DWG-BP`；名称非空。

## 4. 非目标

- 不改运行时来源闭集（`## 453`/`## 456` 的地盘）。
- 不改几何零件提取口径（`packaging_parts.extract()`）。
- 不承诺"任意 DWG 都能拆出与 BOM 相同的件数"：验收目标是**这个酒盒样本 28 件**，
  算法不许因为知道答案是 28 而凑数。
- 不改卡片 / 看板的读接口形状（本批只动推件这一层）。

## 5. 验收命令与期望

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_must_come_from_all_drawing_evidence_red -v
    # 本批红基：见 §6；实现后必须全绿
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_must_come_from_the_drawing_red -v
    # 不回归（`## 453`/`## 456` 的 36 条；本批已删掉其中两条编码旧方向的断言）
./open-claude/.venv/bin/python -m unittest tests.test_packaging_28_part_auto_resolution_and_2d_board_cleanup_red -v
./open-claude/.venv/bin/python -m unittest tests.test_packaging_business_parts_and_cad_plan_view_red -v
    # 不回归
```

真样本只在文件存在时才跑（缺样本 `skipTest`，不算绿）。

## 6. 红基（本批实测，2026-09-23，本机 `./open-claude/.venv/bin/python`）

```text
tests.test_packaging_business_parts_must_come_from_all_drawing_evidence_red
    → Ran 14 tests … FAILED (failures=9)      # A1–A4、B1–B3、C1–C2 红；D1–D5 是有意护栏
tests.test_packaging_parts_must_come_from_the_drawing_red      → Ran 36 … OK
tests.test_packaging_28_part_auto_resolution_and_2d_board_cleanup_red → Ran 15 … OK
tests.test_packaging_business_parts_and_cad_plan_view_red      → Ran 14 … OK
```

今天就是绿的 5 条（有意护栏，不是漏写）：

| 用例 | 为什么现在绿 |
| --- | --- |
| `D1 来源闭集` | `## 456` 已落地 |
| `D2 金标零影响` | `## 456` 已落地（`attachments`/`kb`/`seed_path` 一律不读） |
| `D3 确定性` | 现有实现本来就确定性；本批只换"从哪些证据推"，不改这条性质 |
| `D4 不靠删件凑数` | 现有实现件数与行数本来就相等 |
| `D5 编码唯一` | 现有实现的 `DWG-BP%02d` 本来就唯一 |

## 7. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 459`）

实现文件只有一个：`tech_app/backend/services/packaging_business_part_resolver.py`。

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §3.1 证据闭集 | `EVIDENCE_KINDS` / `EVIDENCE_VERSION` | 8 类证据闭集；`evidence.kinds` 按闭集顺序、去重；`detail.evidence_kinds` = 各行 kinds 的并集（真值） |
| §3.1 轮廓门槛 | `MIN_OUTLINE_SIDE_MM=5` / `MIN_OUTLINE_AREA_MM2=2000` / `MIN_OUTLINE_ENTITIES=2` / `OUTLINE_MATCH_RADIUS_MM=650` | 一条"像零件"的轮廓下限：真样本 1163 个连通分量 → 261 条候选 |
| §3.1 尺寸证据 | `dimension_rects()` / `_region_is_size_confirmed()` | 水平×垂直线性标注交成矩形；与轮廓同形（容差 2mm/2%）→ 记 `size_dimension`（真样本 316 条标注 → 107 个矩形、52 条被确认） |
| §3.2 对答案 | 见下表 | 真样本 recall 25/28、precision 0.962（门槛 22 / 0.85） |
| §3.2 原因账 | `detail.reasons_breakdown` / `unobservable_total` / `unlabeled_outline_total` | 逐行稳定码 + 两张账（"只有名字、观测不到几何" / "有轮廓但没有任何名称锚点"） |
| §3.3 分组 | `plan_family_groups()` / `_expand_family_groups()` / `_POSITION_TOKEN` / `MIRROR_DIRECTIONS` | 族键 =（方向词/容器词，部件尾词），`内盒N` 序号参与；只对**镜像类**方向词分组；成员按轮廓面积降序编号、统一用**父名**+序号 |
| §3.4 护栏 | 未改 | `AUTHORITY_SOURCES` / 金标零影响 / 确定性 / 件数 == 行数 / `DWG-BP` 前缀 |

真样本实测（酒盒，`酒盒.dwg` sha `0991c8b0a964…`，只读复跑）：

```text
business_part_total      = 26
对 28 件金标：recall     = 25/28      （门槛 ≥ 22）
              precision  = 0.962     （门槛 ≥ 0.85）
parts_with_size_total    = 26/26      （门槛 ≥ 0.5）
evidence_kinds           = text_anchor / geometry_region / size_dimension /
                           layer_role / spatial_relation / repetition_mirror
group_total              = 2          （左盖×灰板、右盖×灰板 → 四件 `…里层灰板1/2`）
unlabeled_outline_total  = 235        （261 条候选轮廓里 235 条没有任何名称锚点）
漏件 3                   = 内卡 / 底托灰板 / 磁铁（≤ 6，由原因账逐件披露）
```

分组展开后逐件尺寸与金标对得上：`左盖外盒里层灰板1` 217.06×482.92、`…2` 44.60×273.92、
右盖两件同形 —— 父名不再单独占一行，同族兄弟名（`盒背灰板`）也不再单独占一行。

已知边界（不遮）：

- **尺寸是"位置配对"，不是"尺寸校对"**：26 行都拿到了长宽，其中 13 行与金标长宽逐字相符（±3%）；
  其余是"这块名称锚点最近的那块轮廓"的尺寸（例：`贴牌` 拿到 184.9×146，金标是 226×110.3）。
  §3.1 第 4 条验收的是"尺寸是主证据之一、不许只有 5/27"，**不是**"尺寸必然等于 BOM"。
- 分组判据是**同族 + 镜像方向 + 成员轮廓互不相同**。真样本上只有 `左/右盒外盒里层灰板` 这一族成立；
  圆盘盒里 `内托加强灰板` / `内托支撑围条灰板` 这类"共尾词但不是一件的两半"不分组。
- 图上"有轮廓、没有任何名称锚点"的 235 条**没有**当件（否则 precision 直接塌），只进
  `unlabeled_outline_total` + `reasons_breakdown[outline_without_name_anchor]`。
- `## 453` §2.1 的来源闭集与"金标零影响"两条没有回退；本批只把"件数由**名称证据**决定"
  换成"件数由**全部证据**决定"。
- 顺手修了一处老 bug：`_is_excluded_text("", layer)` 会因为"空文本没有汉字"而对**任何**图层返回 True，
  于是 `build_geometry_regions()` 把 1163 个分量全标成 `annotation_or_frame`。新增只判图层名的
  `_is_excluded_layer()`，本批的候选门才有意义。

复跑命令与结果（本机 `./open-claude/.venv/bin/python`）：

```bash
python -m unittest tests.test_packaging_business_parts_must_come_from_all_drawing_evidence_red
    # Ran 14 … OK（红基 9 红 → 全绿）
python -m unittest tests.test_packaging_parts_must_come_from_the_drawing_red \
    tests.test_packaging_28_part_auto_resolution_and_2d_board_cleanup_red \
    tests.test_packaging_business_parts_and_cad_plan_view_red
    # Ran 65 … OK
python -m unittest <全部 tests.test_packaging_*.py>
    # Ran 2618 … FAILED (failures=10, skipped=10)；10 条全是既有挂账（`## 441` 夹具、
    # stats 键集冻结、role ratio 1.0 vs 0.5、报价发送自遮挡、哨兵指纹），与本批无关
```

红测自身缺陷（如实记录）：本批红测盯的是**对答案**（recall / precision）与**结构**
（kinds 闭集 / group 形状 / detail 字段 / 归属），**没有**校验"尺寸数值对不对" ——
所以"位置配对"这种会偏的尺寸口径也能过；上面那句"13/26 逐字相符"是人工实测，不是红测断言。

# 酒盒 DWG 业务部件与下游工艺/成本必须同时可用

状态：Spec + 红测（已实现）（9-23 落地：件名 28/28、事实档三档、确认尺寸经得起金标复核、下游两道门禁；落点与实测见 §7）

红测：`tests/test_packaging_wine_dwg_parts_and_downstream_truth_red.py`

## 0. 现场基线（2026-09-23，本地最新提交）

用真样本 `47c39dc1ab6738fc48c8/converted.dxf` 直接调用
`packaging_business_part_resolver.resolve_business_parts()`：

- 返回 26 件；测试侧对《酒盒 报价资料.xlsx》28 件金标，命中 25 件；
- 漏 `底托灰板`、`内卡`、`磁铁`，多出父级/误归类件 `顶托灰板`；
- 26 件都有长宽，但只有 12 件标成尺寸标注确认，14 件只是轮廓包围盒；
- 多个尺寸明显不是部件开料尺寸，例如 `右盖面纸≈3927.76×967.95 mm`，而金标为
  `307.07×528.89 mm`；
- 由当前业务部件文档调用 `business_process_inputs()`，仅 19/26 可进入工艺推荐，7 件缺材料；
- 调用 `business_cost_inputs(... face_paper_gsm=225)`，26/26 都被判可算：缺材料的灰板/衬板也被
  整盒面纸克重 225g 兜底，属于错误放行，而不是“下游已跑通”。

因此本批验收不是“接口能返回 200”，而是：部件、尺寸、材料和下游门禁使用同一份可信事实。

## 1. 不可改变的输入边界

1. 生产解析输入仍然是 DWG/CAD IR 及由它生成的几何文档。
2. 28 件金标只能位于 `tests/fixtures/gold/`，用于离线对答案；生产解析器不得读取或按 DWG SHA
   命中金标，不得把附件 BOM 当解析输入。
3. 允许使用与具体样本无关、已审核的通用盒型结构规则，但每个推断件必须声明规则号、触发它的
   图纸/需求事实和 `pending_confirmation`；不得在通用规则中写死本图28个名称或尺寸。

## 2. 真样本业务部件验收

### 2.1 名称集合

酒盒真样本必须产出28个业务叶子件，与测试侧金标规范化名称集合相等：不得缺件、不得多出父组。

- `顶托灰板`若只是图上的父名/别名，不得作为第29个叶子件；应关联到真正叶子件或保留为组证据。
- DWG完全不能直接观测的采购件可以是 `pending_confirmation`，但仍须说明其通用结构规则证据；不得
  伪称“图纸直接识别”。
- 每行必须区分 `observed`、`inferred`、`pending_confirmation`，并保留 evidence/reasons。

### 2.2 尺寸不是“有两个数字就算成功”

1. `size_quality=confirmed` 只允许来自可定位的尺寸标注、明确产品尺寸文字，或有可复核规则的
   镜像/同族继承。
2. 区域 bbox、标题框、多个排版图形的总包围盒只能是 `size_quality=unconfirmed`，不得包装成
   `authority_dimensions`。
3. 对真样本中可与金标同名的件，确认尺寸按长宽可交换比较，绝对误差不得超过 1 mm；达不到就降级
   为待确认，不能继续作为正式工艺/成本输入。
4. `parts_with_size_total` 与 `size_confirmed_total` 分账；页面分别展示，不能用“26/26有尺寸”掩盖
   “只有12件确认”。

## 3. 材料事实与继承

1. 材料必须来自图纸文字、引线关联、明确父组/镜像同族继承或人工确认，并带 provenance。
2. 同族继承必须验证部件角色一致，例如左右同款灰板可以继承；不得跨“面纸/灰板/衬板/EVA/磁铁”。
3. `face_paper_gsm` 只可兜底明确属于面纸/衬纸且材料类别为纸的行；不得兜底灰板、衬板、EVA、磁铁、
   内卡等不同材料类别。
4. 不知道材料就保持缺口，成本状态为 `needs_input`，不得为了跑通而填225g。

## 4. 工艺推荐门禁

1. 工艺入口接收 DWG 派生业务件，不再把所有尺寸统称为“权威清单尺寸”。
2. 单件只有在以下事实齐全时可自动推荐：叶子件、尺寸确认、材料类别/原文可信。
3. bbox猜测尺寸必须返回稳定拒绝码 `PACKAGING_BUSINESS_PART_SIZE_UNCONFIRMED`。
4. 缺材料继续返回 `PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN`。
5. 批量工艺允许部分成功：返回总数、成功、待确认、失败及逐件原因；不得把19/26描述成“全部完成”。

## 5. 成本测算门禁

1. 成本只能消费已确认尺寸和与部件角色相容的材料参数。
2. 灰板/衬板缺材料时，即使需求有 `face_paper_gsm=225` 也必须拒绝，不得按225g纸计算。
3. `pending_confirmation`、尺寸未确认、材料缺失的行必须进入缺口清单；成本可以生成草稿，但
   readiness 不得为 formal，不能发送报价或沉淀快速报价案例。
4. 每条成本行必须带 `business_part_code`、尺寸来源、材料来源、公式/费率版本和缺口状态。

## 6. 完成标准

- 真酒盒输出28个业务叶子件，规范化名称集合与金标相等；生产结果不读取金标。
- 不再出现明显错误尺寸被标为 confirmed 或被工艺/成本消费。
- 28件逐件给出下游状态；能算的真实计算，不能算的明确待确认。
- 缺材料灰板不会被225g面纸克重兜底。
- 工艺/成本的“完成”以可信输入覆盖率判断，而不是以HTTP成功或行数非空判断。


## 7. 落地状态（2026-09-23，Codex 实现；本批 changelog 落地条目 `## 466`）

### 7.1 落点

| 契约 | 落点 | 说明 |
| --- | --- | --- |
| §2.1 第 1 条 名称集合 28/28 | `tech_app/backend/services/packaging_business_part_resolver.py`：新增 `_apply_view_direction_rule()` 与 `_structure_rule_rows()` | `顶托灰板` 改名成 `底托灰板` 的依据不是"猜哪个名顺眼"：真样本 `700ML酒盒底托`（被排除的视图标题，y≈215）与 `顶托灰板`（y≈446）只差 231mm，**件名方向词与它所在视图标题的方向词互为镜像** → 以视图为准（`VIEW_DIRECTION_RULE_ID`）。图上没有的两件采购件（`内卡` / `磁铁`）由 `STRUCTURE_RULES` 三条**图上可核**的触发事实推出来（有 EVA 内托 + 有顶托/底托托盘 + 灰板 ≥ 3 件 ⇒ 磁吸硬质礼盒），一律 `pending_confirmation` + 规则号 + 触发事实，`name_from_drawing=False`，**不伪称图纸识别** |
| §2.1 第 2 条 每行区分事实档 | 同上：行上新增 `truth_state ∈ ("observed","inferred","pending_confirmation")` | `observed` = 图上直接读到；`inferred` = 被视图方向规则纠过名（锚点上留 `renamed_from` / `name_rule` / `view_direction`）；`pending_confirmation` = 结构规则补件。锚点表与 `authority.truth_states` 同步列出闭集 |
| §2.2 第 1/2 条 尺寸只认可定位的标注 | 同上：`match_authority_parts()` 新增「同心嵌套只认最外层」 | 同一中心套着两层及以上矩形时（真样本 `内盒2灰板`：内框 88.5×262.2 / 外形 261.3×435.0；`底板面纸`：126.5×268.7 / 161.2×303.9），件的外形取**最外层**；最外层已被别件占住时（`内盒3灰板` 的外层 184.9×146.0 归了 `贴牌`）尺寸**降级为待确认**，不再冒充确认尺寸。留痕：`match.nested_region_upgraded_total` / `detail.nested_region_upgraded_total` |
| §2.2 第 4 条 两笔账分开 | `detail.parts_with_size_total` / `size_confirmed_total` / `size_unconfirmed_total`（既有）+ 新增 `truth_state_counts` / `inferred_total` / `pending_confirmation_total` / `view_direction_total` / `structure_rule_total` / `structure_rule_rows` / `nested_region_upgraded_total` | 页面可以分别说"28 件里多少件有尺寸、其中多少件是标注确认的"，不再用"26/26 有尺寸"掩盖"只有 12 件确认" |
| §2.3 材料缺口不许兜底 | `tech_app/backend/services/packaging_parts.py`：`business_cost_inputs()` 新增「没有材料原文 → `PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN`」 | 灰板/衬板/EVA/磁铁**行上没有材料**时，不再拿需求里的整盒面纸克重（225g）兜底；有材料原文、只是原文里没有克重时，既有那条 `face_paper_gsm` 兜底**保持不动**（`test_packaging_business_part_cost_by_authority_size_red::A5` 冻结着它，见 §7.3） |
| §4.2/§4.3 工艺门禁 | `packaging_parts.business_process_inputs()`：`authority.size_quality` 不在 `BUSINESS_SIZE_CONFIRMED_QUALITIES` 时返回 `PACKAGING_BUSINESS_PART_SIZE_UNCONFIRMED`（新常量 `BUSINESS_PART_SIZE_UNCONFIRMED` + 纯函数 `business_size_unconfirmed_reason()`） | 包围盒猜测尺寸**不得**进工艺推荐；老载荷没有 `size_quality` 这一键时**不判死**（免得把"还没接这笔账"当成"尺寸不可信"） |
| §5.1/§5.2 成本门禁 | 同文件 `business_cost_inputs()`：同一条尺寸门禁 + §2.3 的材料门禁 | 成本只能消费确认尺寸与相容材料 |

### 7.2 复跑（本机实测）

```
./open-claude/.venv/bin/python -W ignore -m unittest \
  tests.test_packaging_wine_dwg_parts_and_downstream_truth_red
Ran 7 tests ... OK                      # 实现前：Ran 7, failures=6（A1/A2/B1/C1/C2/C3）

node --check tech_app/frontend/app.js   # 未改前端，退出码 0
```

- `A1` 名称集合：**28/28**（实现前缺 `内卡`/`底托灰板`/`磁铁`、多 `顶托灰板`）。
- `B1` 确认尺寸：件级 `size_quality=unfolded` 的行**逐件过金标 1mm**（实现前 `内盒2灰板`/`内盒3灰板`/`底板面纸` 三件错）。
- `C1`/`C2`/`C3`：`bbox_only` 被工艺拒（`PACKAGING_BUSINESS_PART_SIZE_UNCONFIRMED`）、灰板缺材料被成本拒（`PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN`）、真文档里"名字带灰板/衬板/EVA/磁铁且没有材料原文"的行**一件都不再被 225g 放行**。

保护网（同时跑，全部不回归）：

```
./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_*.py
Ran 2680 tests ... FAILED (failures=5, skipped=12)
# 5 条全是**既有挂账**（逐条见 changelog `## 418`/`## 441` 一系的记录）：
#   bom_part_size_provenance::B3 / parse_to_downstream_seams::B4（stats 键集冻结 vs size_quality）、
#   part_role_mapping_reaches_card::A2（与同文件 B3 互斥）、quote_send_recovery::C1（夹具自遮挡）、
#   route_bom_version_pinning::F2（夹具哨兵指纹）。本批**没有新增任何一条失败**。
```

### 7.3 本批的两处「既有断言重指」与一处口径收窄（都已在 changelog `## 466` 记明）

1. **`tests/test_packaging_parts_must_come_from_the_drawing_red.py::B4` 重指**：原文是"每一行的
   `name_from_drawing` 都必须是 `True`"，与 §2.1 第 3 条**直接冲突** —— 规则补进来的采购件
   必须**不许**伪称图纸识别。改成"每一行要么来自本图，要么是带规则号的 `pending_confirmation`"，
   强度不降（规则补件那一支比原来更严）。
2. **§2.3 与 `test_packaging_business_part_cost_by_authority_size_red.py::A5` 的边界**：A5 冻结了
   "材料原文 `双灰板`（有材料、没克重）+ 需求 225g → 允许兜底"。本批按**更窄**的口径落地 ——
   只有"**完全没有材料原文**"才拒；因此 A5 仍绿。要连"有材料原文但没克重"也拒，需要先改那份
   Spec 与 A5，**不在本批**。
3. **`detail` 新增 7 个键、行上新增 `truth_state` / `name_rule` / `structure_rule`**：既有
   `DERIVED_TOTAL`（`derived` 件数）/`SIZED_TOTAL`（有尺寸件数）**逐字未变** —— 结构规则补出来的
   两件没有尺寸、状态是 `partial`，所以两笔账都不动（`test_packaging_business_part_size_must_be_confirmed_by_dimension_red::E1` 仍绿）。

### 7.4 未做 / 边界

- 未读金标、未按 DWG sha 命中金标、未读附件 BOM（`gold_standard_used=False`、`refused_sources` 不变）；
- 未新增依赖、未起服务、未连 PG / 34、未写业务数据、未改前端、未改成本公式与费率；
- 未把 `size_quality` / `truth_state` 写进 `business_parts_document()` 的行形状（那会改文档行形状，
  另起一批）；下游门禁本轮按"行上/`authority` 里给了 `size_quality` 就判、没给就不判死"的口径。

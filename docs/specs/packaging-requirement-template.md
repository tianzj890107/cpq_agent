# 规格：包装需求模板与会话 / 看板联动 —— 包装第 2 批

> 批次：包装 8 批计划的**第 2 批**。依赖第 1 批（唯一行业注册表 + 四行业口径）已完成。
> 红测：`tests/test_packaging_requirement_template_red.py`。
> 依据：`蜀同包装项目-待开发/YTBZ-报价业务流程-20260907.xlsx` 的包装流程与盒型匹配维度。

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_requirement_template_red.py`

## 1. 背景与真实问题

第 1 批把「包装」接进行业注册表后，`packaging` 还是一个空壳：后端
`industry_templates.SPECS` 没有包装字段，前端 `rcReplaceProductSpec()` 没有包装分支
（会掉进 `rcManagedFlexibleSpec()` 的“AI 生成字段”历史路径），
`requirement_service.requirement_precheck()` 也查不到包装的必填项。

现状实测：

- `industry_templates.blocks('packaging')` → 落到半导体（`normalize` 把 packaging 抹掉），
  所以包装需求单会用半导体的晶圆尺寸/静电吸盘去要求必填。
- `requirement-create.js:130` 的 `rcReplaceProductSpec()` 只有
  semiconductor / battery / appliance 三个分支，包装落到 `rcManagedFlexibleSpec()`。
- `FIELD` 级别的“来源”信息不存在：只有 `document_extraction` 的
  `filled_fields` / `recommendations` 两套集合，无法区分「用户原文 / 附件 / AI 抽取 / 人工修改」。
- `da_repo._STRUCTURAL_DATA_KEYS` 只列了 `industry` / `industry_selection` /
  `industry_assessment` / `template_spec_manager`，新增的结构化键会被当成普通字段行写库。

## 2. 用户角色与用户故事

- 销售经理：选“包装”后，1.1 需求单出现包装自己的 3.1–3.6 字段，而不是晶圆/静电吸盘。
- 销售经理：贴一段客户需求或传一份文档，AI 解析后右侧看板自动填入盒型、长宽高、面纸克重、V 槽；
  我手动改过的字段不能被下一次解析悄悄改回去。
- 工艺经理：进 1.2 确认时，看到的缺口是包装的必填项（成品内长/内宽/内高、盒型、闭合方式、
  V 槽、面纸克重、报价数量），不是半导体字段。
- 任意用户：刷新或从历史打开后，字段与“这个值从哪来”的标记都还在。

## 3. 目标流程

```
报价首页选“包装”
  → 1.1 需求单 Section C 渲染 3.1–3.6 包装字段（后端模板为唯一事实源）
  → 粘贴需求 / 上传文档 → AI 解析
  → 结果写进 document_extraction（既有唯一通道）→ 右侧看板按字段回填
  → 每个字段带来源标记：用户原文 / 附件 / AI 抽取 / 人工修改
  → 人工改过的字段标记为「人工修改」，后续解析不再覆盖
  → 保存 → 3.7 图纸与技术资料
  → 1.2 确认页按包装必填项做完整性检查
```

## 4. 数据契约

### 4.1 后端模板（唯一事实源）

`tech_app/backend/services/industry_templates.py` 增加 `PACKAGING_SPEC`，六段：

| 段 | 标题 | 关键字段（key） |
| --- | --- | --- |
| 3.1 | 产品与订单 | packaging_product_name\*, packaging_category\*, quote_quantity\*, moq, sample_quantity, mass_quantity, first_trial, delivery_due, destination, currency, tax_rate |
| 3.2 | 成品尺寸与盒型 | inner_length\*, inner_width\*, inner_height\*, box_type\*, box_family, fit_clearance, closure_type\*, v_groove\*, magnetic, collapsible, open_close_life |
| 3.3 | 材料 | grey_board, grey_board_thickness, face_paper, face_paper_gsm\*, lining_paper, insert_type, glue, magnet, ribbon, accessories, eco_requirement |
| 3.4 | 印刷与表面工艺 | print_colors, spot_colors, lamination, hot_stamping, uv_coating, emboss_deboss, silk_screen, die_cutting, mounting, special_process, process_area |
| 3.5 | 包装与物流 | units_per_carton, carton_size, flat_card, poly_bag, corner_guard, pallet, units_per_pallet, shipping_mode, min_freight, loading_rate |
| 3.6 | 商务与价格 | need_cost_estimate, loss_rate, proofing_base, tooling_cost, tooling_amortize_qty, target_gross_margin, tech_premium, market_adjustment, other_markup, discount |

（\* = required）`FILE_BLOCK_SECTION['packaging']` 必须为 `'3.7'`。

### 4.2 前端同一套模板

`tech_app/frontend/requirement-create.js`：

- 新增 `RC_PACKAGING_SPECS`（与后端 `field_keys('packaging')` 键集合一致，防漂移）。
- `rcReplaceProductSpec()` 增加 `packaging` 分支 → `rcStaticSpec(RC_PACKAGING_SPECS)`。
- 「图纸与技术资料」块编号改 3.7。
- 行业横幅文案说明“已加载包装（盒型/材料/印刷/物流）规格字段”。
- 字段渲染继续走既有 `rcField` / `rcAiBadge`，不新增第二套渲染。

### 4.3 字段来源

- 来源枚举（封闭）：`user_text`（用户原文）、`attachment`（附件）、`ai_extract`（AI 抽取）、
  `ai_recommend`（AI 推荐默认值）、`manual`（人工修改）。
- 存 `requirement.data['field_sources'] = {field_key: source}`。
- 合并规则 `requirement_service.merge_field_sources(existing, incoming)`：
  1. `manual` 永不被非 `manual` 覆盖；
  2. 已有来源为 `user_text` / `attachment` 时，不被 `ai_extract` / `ai_recommend` 降级；
  3. 新字段按传入来源写入；
  4. 未知来源值被丢弃（不得写入枚举外的值）。
- `da_repo._STRUCTURAL_DATA_KEYS` 必须包含 `field_sources`，避免被拆成字段行。

### 4.4 完整性门禁

`requirement_service.requirement_precheck()` 对 `industry='packaging'` 必须按包装模板检查：
缺 `inner_length` / `inner_width` / `inner_height` / `box_type` / `closure_type` / `v_groove` /
`face_paper_gsm` / `quote_quantity` 等必填项 → `ok=False`，`gaps.keys` 给出字段键、
`gaps.labels` 给出中文标签（不是原始 key）。

`section_checks('packaging')` 的条目必须覆盖 3.1–3.6；缺任一必填项时对应条目的
`status` 必须为 `need_info`，只有全部填齐才允许该条目 `status == 'ok'`。不得因为字段缺失
而把包装回退成半导体模板。

### 4.5 前端来源徽章

字段来源徽章必须复用既有 `rcField` → `rcAiBadge` 渲染链，不新增第二套渲染：

- `RC_FIELD_SOURCE_LABELS = {user_text:'用户输入', attachment:'附件',
  ai_extract:'AI 抽取', ai_recommend:'AI 推荐', manual:'人工修改'}`。
- `rcAiBadge(name)` 优先读取 `rcData().field_sources?.[name]`：
  有来源标记时返回该来源的徽章（`class` 复用既有 `ai-filled-badge`），
  `manual` 必须渲染为「人工修改」且不被后续解析的 AI 徽章覆盖。
- 无 `field_sources` 时保持既有 `AI 带入` / `AI 推荐` / 历史决策徽章行为，不得回归。

## 4.6 精确字段清单（红测与前端对齐用）

`field_keys('packaging')` 必须**恰好**等于下面 64 个键（顺序即页面顺序），
`required_keys('packaging')` 必须**恰好**等于带 `*` 的 10 个键。

```
3.1 packaging_product_name* packaging_category* quote_quantity* moq sample_quantity
    mass_quantity first_trial delivery_due destination currency tax_rate
3.2 inner_length* inner_width* inner_height* box_type* box_family fit_clearance
    closure_type* v_groove* magnetic collapsible open_close_life
3.3 grey_board grey_board_thickness face_paper face_paper_gsm* lining_paper insert_type
    glue magnet ribbon accessories eco_requirement
3.4 print_colors spot_colors lamination hot_stamping uv_coating emboss_deboss
    silk_screen die_cutting mounting special_process process_area
3.5 units_per_carton carton_size flat_card poly_bag corner_guard pallet
    units_per_pallet shipping_mode min_freight loading_rate
3.6 need_cost_estimate loss_rate proofing_base tooling_cost tooling_amortize_qty
    target_gross_margin tech_premium market_adjustment other_markup discount
```

## 5. 正常路径

1. 选包装 → Section C 出现 3.1–3.6，3.7 为图纸与技术资料。
2. 粘贴“天地盖礼盒，内尺寸 200×150×80mm，面纸 128g 铜版纸，V 槽，磁吸闭合，5000 个”。
3. AI 解析 → 盒型/长宽高/面纸克重/V 槽写入字段，带来源标记。
4. 人工把 inner_height 改成 90 → 该字段来源变 `manual`，再次解析不被覆盖。
5. 保存 → 刷新 → 字段与来源都在。

## 6. 异常路径

- 缺长宽高时不得判定需求完整；确认页必须报缺口。
- 行业值为 `flexible` 的历史草稿：仍走既有动态字段路径，不受影响。
- 解析失败：不写部分结果、不产生第二套通道。

## 7. 非目标

盒型匹配算法、参数化 BOM、工艺路线、成本与定价（第 3–8 批）；不改三行业模板与既有结果；
不新增第二套 AI 抽取通道；不改 `document_extraction` 既有字段名。

## 8. 历史兼容

- `flexible` 草稿、既有三行业草稿行为不变。
- 新增 `field_sources` 是可选结构：老数据没有也能打开、保存时补写。

## 9. 可自动化验收

```
./open-claude/.venv/bin/python -m unittest tests.test_packaging_requirement_template_red
```

## 10. 人工验收

选包装 → 1.1 出现 3.1–3.6；粘贴一段包装需求 → 看板回填并显示来源徽章；手改一个字段后再解析
不被覆盖；1.2 确认页缺口是包装必填项；刷新后字段与来源仍在。

## 11. 不允许减少的既有能力

半导体 / 电池 / 电器模板与标签；`flexible` 动态字段路径；`document_extraction` 通道与
`AI 带入` / `AI 推荐` 徽章；`requirement_precheck` 既有 items/ok/generated_note/engine 字段。

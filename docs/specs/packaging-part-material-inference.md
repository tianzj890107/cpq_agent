# 业务件材料按件名推定并写回件级资料

2026-11-26

## 现场复现

34 上酒盒项目的业务件 `DWG-BP01` / `DWG-BP03` 排工艺返回
`PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN`（"这一件在对照表里没有材料原文"），`retryable:false`。
同项目 28 件里 9 件 `reference.material_text` 为空：这些件在图纸/对照表里没有材料原文，但件名
已经带材料族（灰板 / 衬板 / 底板 / 面纸…）。演示输入路径里早就有「按件名推定材料」
（`infer_material_text`），但它只写进演示 `poc_inputs`，**没有写回**件级对照资料，真实工艺
门禁读不到，于是同一份数据在演示里能过、在真实门禁里被拒。

## 验收

1. 件级对照资料的 `material_text` 为空、且件名能识别材料族时，读侧按同一套关键词与默认厚度
   推定材料原文，并在块上标 `material_source="inferred_from_part_name"`；已有材料原文
   **不改写**、不标推定。
2. 推定是读侧派生：不改历史文档、不改 `business_parts_hash`；重新生成业务部件后同样成立
   （`business_parts_document` 本来就经这个读函数取块，因此新文档会一并落推定值与来源）。
3. 结论必须披露：`business_process_assumption` / `business_cost_assumption` 要写出
   "材料原文缺失，按件名推定为「X」"，不得把推定装作图纸原文。
4. 件名识别不到材料族时仍返回 `PACKAGING_BUSINESS_PART_MATERIAL_UNKNOWN`；尺寸仍只认清单
   尺寸，不因材料推定放宽。
5. 推定实现只有一份：`packaging_demo_inputs.infer_material_text` 委托给新模块
   `packaging_material_inference`，演示路径与真实门禁共用同一个函数。

## 回归入口

`./open-claude/.venv/bin/python -W ignore -m unittest tests.test_packaging_part_material_inference_red`

## 补充：材料费克重（2026-11-26）

材料原文只有厚度或只有材料族时，成本门禁按以下顺序推定克重，全部披露、不装原文：

1. 原文里有 `<数字>g` → 直接采用（来源 `material_text`）；
2. 灰板 / EVA 只写 `<数字>mm` → `料厚 × 族默认密度 × 1000`（灰板 0.7、EVA 0.9 g/cm³；来源
   `inferred_thickness_density`）；
3. 只写材料族 → 族默认克重（衬纸 80、特种 120、纸管 250 g/㎡；来源 `inferred_family_default`）。

来源写进 `variables.grammage_source`，成本结论写明"克重按…推定为 X g/㎡（非原文）"。
既有硬规则不动：bbox 不得进下游、无几何不得编尺寸（`drawing_part_size_not_confirmed`）。

## 演示兜底（2026-11-26，用户决定）

已审核需求缺本件证据时，提交单件工艺**不再直接 409**，而是走演示兜底：

1. 先跑 AI 名称/材料推荐（`packaging_name_assist.prepare`）：材料仍缺的件由模型在候选里选，
   结果记 `semantic_material`，来源标 `ai_semantic`；
2. 再跑演示输入准备（`packaging_demo_inputs.prepare(..., enable_demo=False)`）：写 `poc_inputs`
   （`mock=True`）与文档级 `poc_demo`，缺尺寸按已绑定几何或 `100×100` 占位、缺克重按演示值；
3. **不改写已审核需求单**；随后按 `poc_reference` 口径重算并提交，结论里仍标 demo。

只有演示准备本身失败（如盒型冲突、无业务部件清单）才回落到原来的 409。

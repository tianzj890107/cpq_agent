# 包装材料相似度推荐与演示输入装配

状态：Spec + 红测（已实现）
红测：`tests/test_packaging_material_similarity_and_demo_inputs_red.py`（15 条）、
`tests/test_packaging_demo_flow_end_to_end_red.py`（4 条）

## 1. 问题

图纸解析能拆出零件，但成本算不出来，卡在三处，且三处都**不该靠人工逐条填**：

1. 业务部件清单里没有展开长宽 → 材料行全部 `part_size_missing` → 材料费恒为 0；
2. 客户材料原文（`225G铜版底PET光银` / `2mm灰板` / `35mm厚EVA`）与 `kb_material.name`
   对不上任何一条，分词包含规则也接不上 → 一行都认不出；
3. 材料认不出时既没有候选、也没有"缺什么"的可执行结论。

## 2. 目标

给出一条**与具体图纸无关**的通路：几何给尺寸、相似度给材料候选、演示口径给单价与工时，
并把"哪一条是 mock、按什么依据给的"逐条写进成本结论。

## 3. 通用性红线（本批硬约束）

- 源码里**不许出现**任何单一图纸 / 单一客户 / 单一零件的标识（`酒盒`、`圆盘盒`、`DWG-BP…`
  一律不许出现在这两个模块里）——由 `test_c7_module_has_no_single_drawing_hardcoding` 钉住；
- 尺寸只来自"这一件绑定的几何分量"，不来自任何对照表、不来自模型猜；
- 材料候选只来自**调用方给定的**库内材料清单，不跨行业自取；
- 相似度只是**建议**：返回体 `approved` 恒为 `false`，采纳与否由调用方留痕。

## 4. 材料相似度推荐（`packaging_material_match.py`）

特征：克重（`<数字>g/克`）、厚度（`<数字>mm`）、材质族（关键词表）、归一化字面。
打分：`0.5 × 材质族 + 0.25 × 克重/厚度 + 0.25 × 字面相似度`。

结论闭集：`exact_name`（逐字命中）/ `similarity` / `ambiguous`（前两名差距 < 0.06）/
`below_threshold`（< 0.45）/ `no_candidate`（库为空或无可比候选）。
**低于阈值与前两名太近都不许静默取最高分。**

克重推导顺序：原文 `<数字>g` → 材料 `gsm` 属性 → 材料 `grade` 里的 `<数字>g` →
`厚度 × 密度 × 1000`（密度取材料行/属性表，取不到才用族演示密度并披露）。都没有 → 不给金额。

没有库内单价时按材质族给**演示单价**（元/kg），`price_source=demo_family_price`，
且必须随结论披露"这不是正式费率"。

## 5. 演示输入装配（`packaging_demo_inputs.py`）

对每一件业务部件产出一份 `poc_inputs`：开料长宽（几何包络）、材料原文、克重、吨价、
模数/校版/税率、以及一段把所有 mock 说清楚的 `note`。

- 材料原文：图纸原文 → 组成材料原文 → **按件名关键词推定**（三次来源都写进 `material_source`）；
- 开料长宽：绑定分量的**逐轴最大值**（件清单尺寸优先、`bbox` 兜底）；取不到 → `no_geometry_size`，
  不给默认值；
- 落库只写 `poc_inputs` / `cost_parameters` / 文档级 `poc_demo`，**绝不写 `confirmed_size`**
  （演示输入不是人工确认）。见 `test_a4_demo_never_writes_a_confirmed_size`。

## 6. 命令

```bash
# 干跑：只算不写
python scripts/packaging_demo_prepare.py <project_id>
# 写入演示输入并重排 BOM → 路线 → 成本
python scripts/packaging_demo_prepare.py <project_id> --apply --actor PE1
```

## 7. 边界与披露

- 演示输入只影响材料行的**输入变量**，不改任何公式与费率口径；
- 成本结论里必须出现"演示"字样（`test_a2`），缺口照旧按 `GAP_RESOLUTIONS` 分级；
- 正式出价前必须换成权威材料价与人工确认尺寸 —— 本批不改变任何生产判据。

# 包装整件工艺单位修正：本地验收

2026-10-13。Spec：`docs/specs/packaging-whole-part-process-unit.md`。

单件异步任务与批量缺失补齐均按父业务件名称查DA参考路线、一次生成整件方案；
组成尺寸、材料与用量进入说明上下文，不再独立生成和拼接组成工序。
3.3每件只展示一套路线，旧组成方案展示待更新；3.1组成及3.2开料材料单位保留。
旧任务去重键与新工艺口径区分；旧记录保留，不作为当前整件路线或成本输入。

原逻辑6项红测实际失败；收口时另复现重新生成后残留旧警告并修正，最终10项专项全部通过。特别验证：

- 真正执行单件处理函数提交的任务，查库和推荐各一次，编码仍为父件。
- 多组成上下文保留两种材料和各自尺寸，未用并集尺寸代替。
- 旧结果读回保留historical_plan/historical_sections，当前plan为空且显示整件重算提示。
- 在原面板重新生成整件工艺后立刻清掉旧组成警告，无需关闭或重新进入。
- 一个父件的两道同名真实工序仍为两道；路线确认后公式成本只产生两行，每行用量为
  父件每套用量2，没有乘上组成数量或复制成四行。
- 对已有效的其他整件结果保留既有“只补缺口”能力。

## 扩展回归与既有失败

主回归15模块240项通过；额外多组成模块30项中26通过、4失败。失败均位于
`tests/test_packaging_multipart_sections_complete_red.py`：

| 测试 | 现象 |
|---|---|
| test_pending_section_bom_never_falls_back_to_confirmed_parent_union | 旧断言要求所有未人工核对组成无尺寸，但当前自动采用路径返回了尺寸/状态 |
| test_plain_name_group_excludes_layout_copies_and_is_translation_invariant | 名称区域合并候选为空，而合成断言期待a/b组合 |
| test_single_business_name_can_propose_multiple_owned_shapes | 同样未生成期待的多图形组合候选 |
| test_ui_section_gate_and_markup_keep_every_portion_without_untrusted_html | UI自动采用门槛允许原测试认为应拒绝的输入 |

基线核对：在内存中将本次修改的business_process_inputs/business_process_assumption
替换回HEAD（4566e7a），上述4条仍失败4/4、无异常；涉及的几何合并、BOM与UI组成门槛
未在本批改动。未把旧确认口径断言自动改成绿色，也未借本批改写几何识别规则。
这些断言需结合之前用户要求的自动采用口径另行核对，不能把整件工艺修复当成它们已解决。

Python编译、3个JS语法与diff检查通过。仅本地修改，未提交、未推送、未部署；
无线上API/模型/数据库验收。上线后已有分组成工艺的项目须经原推荐按钮补齐整件路线，
再重新生成项目路线与成本；不自动覆盖线上历史项目。

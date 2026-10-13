# 已选CAD几何自动进入工艺与成本

状态：Spec + 红测（已实现）；本地，未部署。
红测：`tests/test_packaging_auto_geometry_cost_inputs_red.py`

本轮取代“已归属候选必须再人工确认尺寸”的默认交互。已选CAD候选有真实实体、分量和正数有限尺寸时自动作为估算输入，不要求人工确认。优先保留已核对标注/人工尺寸，次取候选展开长宽，最后几何包络；来源auto_selected_cad_geometry，不写confirmed_size，不冒称CAD标注。

多组成逐部分保留，自动尺寸写临时automatic_size，不覆盖持久化确认值。实体覆盖/组成完整性仍校验；无归属、空实体、缺尺寸不以0或占位放行。尺寸估算的提示保留，但不能再要求“先人工确认”。auto_geometry_inputs=false可保留旧严格门禁，默认自动。

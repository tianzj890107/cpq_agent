"""包装行业演示数据（礼盒盒型库）。

为什么单独一份：`da_seed` 是通用机加工/钣金口径、`da_seed_battery` 是电池口径，两者都
没有盒型、部件尺寸公式、裱糊工时与内托配件 —— 包装这条线在库里一条也命中不上，演示时
只能看到「未匹配 / 需补录」。

这份种子把 `裕同包装项目-待开发/礼盒盒型库_数据样例.xlsx` 的四个 Sheet 逐条固化成库内
演示数据，并给 11 张行业主体表补上行业维度：

    盒型      kb_packaging_box_type           12 条  01盒型
    部件构成  kb_packaging_part_template      31 条  02-盒型-部件构成
    工艺模板  kb_packaging_process_template   23 条  03-盒型+部件-工艺路线与工时
    内托配件  kb_packaging_insert_accessory   12 条  04-内托与配件库

    成本口径  kb_material / kb_material_price / kb_cost_rate / kb_cost_factor
    其余口径  kb_packaging_cost_formula（7 条占位，review_status='draft'，本批不参与计算）
              kb_packaging_logistics_rule / kb_packaging_match_weight（盒型五维匹配权重）

行业口径（见 docs/specs/packaging-knowledge-base-mock-seed.md 2.4）：

  · 每条包装行都写 `industry='packaging'`；既有行的 `industry` 为空/NULL = 通用，
    三行业行为逐字不变；
  · 检索按 `kb_repo` 的 `industry=` 参数收口，包装数据不会串进半导体/电池/电器。

本批只建数据与隔离：不做盒型匹配打分（第 4 批）、参数化 BOM 展开（第 5 批）、工艺路线
生成（第 6 批）、成本公式求值（第 7 批）。演示数据不是正式主数据。

用法（幂等，已存在的记录默认不覆盖）：

    python -m backend.storage.da_seed_packaging               # 基础种子 + 包装
    python -m backend.storage.da_seed_packaging --force       # 覆盖已存在记录
    python -m backend.storage.da_seed_packaging --packaging-only
"""
from __future__ import annotations

from . import da_db as db
from . import kb_repo as kb

SEED_DATE = "2026-01-01 00:00:00"
VERSION = "2026.01"

SOURCE_XLSX = "裕同包装项目-待开发/礼盒盒型库_数据样例.xlsx"
SOURCE_BOX_TYPE = SOURCE_XLSX + " / 01盒型"
SOURCE_PART_TEMPLATE = SOURCE_XLSX + " / 02-盒型-部件构成"
SOURCE_PROCESS_TEMPLATE = SOURCE_XLSX + " / 03-盒型+部件-工艺路线与工时"
SOURCE_ACCESSORY = SOURCE_XLSX + " / 04-内托与配件库"
SOURCE_COST = "报价逻辑-0903.xlsx / 包装成本口径"
SOURCE_MATERIAL = "报价逻辑-0903.xlsx / 包装材料价"
SOURCE_LOGISTICS = "报价逻辑-0903.xlsx / 包装物流规则"
SOURCE_MATCH_WEIGHT = "报价逻辑-0903.xlsx / 盒型五维匹配权重"
SOURCE_COST_CONTENT = "报价逻辑-0903.xlsx / 包装运输"
SOURCE_TOOLING = "报价逻辑-0903.xlsx / 包装运输 / 工装刀模"


BOX_TYPES = [
    {"box_type_code": "YT-RB-01001-A", "name": "天地盖盒（全盖）", "name_en": "Lid & Base Box",
     "family": "01天地盖", "size_l_min": 80.0, "size_l_max": 400.0, "size_w_min": 80.0,
     "size_w_max": 300.0, "size_h_min": 25.0, "size_h_max": 120.0, "fit_clearance": 1.8,
     "grey_board_thickness": "2.0（1.5/2.5可选）", "face_paper_gsm": "157-250", "closure_type": "天地盖",
     "part_count": 10, "v_groove": "是", "hand_mount_ratio": "65%", "standard_seconds": 193.0,
     "automation_level": "半自动", "moq": 500, "sample_lead_days": 5, "mass_lead_days": 15,
     "load_kg": 2.0, "standard_cost": 12.5, "standardization_level": "标准共用",
     "applicable_industries": "化妆品/数码/茶叶", "business_status": "标准"},
    {"box_type_code": "YT-RB-01002-A", "name": "半天地盖盒", "name_en": "Partial Telescope Box",
     "family": "01天地盖", "size_l_min": 100.0, "size_l_max": 450.0, "size_w_min": 100.0,
     "size_w_max": 350.0, "size_h_min": 40.0, "size_h_max": 150.0, "fit_clearance": 1.5,
     "grey_board_thickness": "2.0（1.5/2.5可选）", "face_paper_gsm": "157-250", "closure_type": "天地盖",
     "part_count": 10, "v_groove": "是", "hand_mount_ratio": "62%", "standard_seconds": 182.0,
     "automation_level": "半自动", "moq": 500, "sample_lead_days": 5, "mass_lead_days": 15,
     "load_kg": 3.0, "standard_cost": 14.2, "standardization_level": "标准共用",
     "applicable_industries": "月饼/茶叶/食品", "business_status": "标准"},
    {"box_type_code": "YT-RB-01003-A", "name": "双层天地盖盒", "name_en": "Double Cover Box",
     "family": "01天地盖", "size_l_min": 120.0, "size_l_max": 400.0, "size_w_min": 120.0,
     "size_w_max": 300.0, "size_h_min": 60.0, "size_h_max": 160.0, "fit_clearance": 1.8,
     "grey_board_thickness": "2.5（2.0/3.0可选）", "face_paper_gsm": "200-300", "closure_type": "天地盖",
     "part_count": 14, "v_groove": "是", "hand_mount_ratio": "75%", "standard_seconds": 268.0,
     "automation_level": "手工为主", "moq": 1000, "sample_lead_days": 7, "mass_lead_days": 22,
     "load_kg": 3.5, "standard_cost": 21.0, "standardization_level": "半定制",
     "applicable_industries": "高端化妆品/珠宝", "business_status": "标准"},
    {"box_type_code": "YT-RB-02001-A", "name": "书型盒（铰链翻盖）", "name_en": "Book Style Hinged Box",
     "family": "02书型盒", "size_l_min": 100.0, "size_l_max": 350.0, "size_w_min": 80.0,
     "size_w_max": 280.0, "size_h_min": 30.0, "size_h_max": 90.0, "fit_clearance": 1.5,
     "grey_board_thickness": "2.0（2.5可选）", "face_paper_gsm": "157-300", "closure_type": "铰链+磁吸",
     "part_count": 12, "v_groove": "是", "hand_mount_ratio": "80%", "standard_seconds": 305.0,
     "automation_level": "手工为主", "moq": 1000, "sample_lead_days": 7, "mass_lead_days": 25,
     "load_kg": 2.5, "standard_cost": 26.8, "standardization_level": "半定制",
     "applicable_industries": "珠宝/腕表/收藏品", "business_status": "标准"},
    {"box_type_code": "YT-RB-02002-A", "name": "磁吸翻盖盒", "name_en": "Magnetic Closure Box",
     "family": "02书型盒", "size_l_min": 90.0, "size_l_max": 380.0, "size_w_min": 90.0,
     "size_w_max": 280.0, "size_h_min": 28.0, "size_h_max": 100.0, "fit_clearance": 1.5,
     "grey_board_thickness": "2.0（2.5可选）", "face_paper_gsm": "157-300", "closure_type": "磁吸",
     "part_count": 12, "v_groove": "是", "hand_mount_ratio": "70%", "standard_seconds": 245.0,
     "automation_level": "半自动", "moq": 800, "sample_lead_days": 6, "mass_lead_days": 20,
     "load_kg": 2.5, "standard_cost": 19.6, "standardization_level": "标准共用",
     "applicable_industries": "高端手机/酒类/化妆品", "business_status": "标准"},
    {"box_type_code": "YT-RB-03001-A", "name": "抽屉盒（内滑式）", "name_en": "Inner Slide Drawer Box",
     "family": "03抽屉盒", "size_l_min": 100.0, "size_l_max": 320.0, "size_w_min": 80.0,
     "size_w_max": 240.0, "size_h_min": 30.0, "size_h_max": 100.0, "fit_clearance": 1.2,
     "grey_board_thickness": "2.0（1.5/2.5可选）", "face_paper_gsm": "157-250",
     "closure_type": "抽屉+拉带", "part_count": 11, "v_groove": "是", "hand_mount_ratio": "60%",
     "standard_seconds": 210.0, "automation_level": "半自动", "moq": 500, "sample_lead_days": 6,
     "mass_lead_days": 18, "load_kg": 1.5, "standard_cost": 16.4, "standardization_level": "标准共用",
     "applicable_industries": "电子产品/文具", "business_status": "标准"},
    {"box_type_code": "YT-RB-03002-A", "name": "抽屉盒（外滑式）", "name_en": "Outer Slide Drawer Box",
     "family": "03抽屉盒", "size_l_min": 90.0, "size_l_max": 280.0, "size_w_min": 70.0,
     "size_w_max": 200.0, "size_h_min": 25.0, "size_h_max": 80.0, "fit_clearance": 1.2,
     "grey_board_thickness": "1.5（2.0可选）", "face_paper_gsm": "157-250", "closure_type": "抽屉+拉带",
     "part_count": 11, "v_groove": "是", "hand_mount_ratio": "68%", "standard_seconds": 225.0,
     "automation_level": "手工为主", "moq": 800, "sample_lead_days": 6, "mass_lead_days": 20,
     "load_kg": 1.0, "standard_cost": 18.2, "standardization_level": "半定制",
     "applicable_industries": "珠宝/饰品", "business_status": "标准"},
    {"box_type_code": "YT-RB-04001-A", "name": "折叠精装盒", "name_en": "Collapsible Rigid Box",
     "family": "04折叠精装", "size_l_min": 120.0, "size_l_max": 400.0, "size_w_min": 100.0,
     "size_w_max": 300.0, "size_h_min": 40.0, "size_h_max": 120.0, "fit_clearance": 1.5,
     "grey_board_thickness": "2.0（1.5可选）", "face_paper_gsm": "157-250", "closure_type": "磁吸/天地盖",
     "part_count": 12, "v_groove": "是", "hand_mount_ratio": "55%", "standard_seconds": 198.0,
     "automation_level": "半自动", "moq": 500, "sample_lead_days": 6, "mass_lead_days": 18,
     "load_kg": 2.0, "standard_cost": 15.8, "standardization_level": "标准共用",
     "applicable_industries": "电商礼盒/服装", "business_status": "标准"},
    {"box_type_code": "YT-RB-05001-A", "name": "六角异形盒", "name_en": "Hexagon Box", "family": "05异形",
     "size_l_min": 100.0, "size_l_max": 260.0, "size_w_min": 100.0, "size_w_max": 260.0,
     "size_h_min": 40.0, "size_h_max": 120.0, "fit_clearance": 1.5, "grey_board_thickness": "3.0",
     "face_paper_gsm": "200-300", "closure_type": "天地盖", "part_count": 14, "v_groove": "是",
     "hand_mount_ratio": "90%", "standard_seconds": 352.0, "automation_level": "手工", "moq": 1000,
     "sample_lead_days": 8, "mass_lead_days": 28, "load_kg": 2.0, "standard_cost": 28.0,
     "standardization_level": "全定制", "applicable_industries": "限定款/节庆礼盒", "business_status": "标准"},
    {"box_type_code": "YT-RB-05002-A", "name": "心形盒", "name_en": "Heart Shape Box",
     "family": "05异形", "size_l_min": 120.0, "size_l_max": 300.0, "size_w_min": 110.0,
     "size_w_max": 280.0, "size_h_min": 40.0, "size_h_max": 100.0, "fit_clearance": 1.5,
     "grey_board_thickness": "2.5", "face_paper_gsm": "200-300", "closure_type": "天地盖",
     "part_count": 2, "v_groove": "否", "hand_mount_ratio": "92%", "standard_seconds": 315.0,
     "automation_level": "手工", "moq": 1000, "sample_lead_days": 8, "mass_lead_days": 28,
     "load_kg": 1.0, "standard_cost": 24.5, "standardization_level": "全定制",
     "applicable_industries": "情人节/礼品", "business_status": "试用"},
    {"box_type_code": "YT-RB-06001-A", "name": "圆型筒盒", "name_en": "Round Tube Box",
     "family": "06筒盒", "size_l_min": 80.0, "size_l_max": 200.0, "size_w_min": 80.0,
     "size_w_max": 200.0, "size_h_min": 80.0, "size_h_max": 300.0, "fit_clearance": 1.5,
     "grey_board_thickness": "1.5（2.0可选）", "face_paper_gsm": "157-250", "closure_type": "天地盖",
     "part_count": 3, "v_groove": "否", "hand_mount_ratio": "58%", "standard_seconds": 165.0,
     "automation_level": "半自动", "moq": 500, "sample_lead_days": 5, "mass_lead_days": 16,
     "load_kg": 1.5, "standard_cost": 11.2, "standardization_level": "标准共用",
     "applicable_industries": "茶叶/食品/化妆品", "business_status": "标准"},
    {"box_type_code": "YT-RB-07001-A", "name": "双开门盒", "name_en": "Double Door Box",
     "family": "07其他", "size_l_min": 150.0, "size_l_max": 400.0, "size_w_min": 100.0,
     "size_w_max": 280.0, "size_h_min": 35.0, "size_h_max": 90.0, "fit_clearance": 1.5,
     "grey_board_thickness": "2.0（2.5可选）", "face_paper_gsm": "157-300", "closure_type": "磁吸双开",
     "part_count": 13, "v_groove": "是", "hand_mount_ratio": "78%", "standard_seconds": 288.0,
     "automation_level": "手工为主", "moq": 800, "sample_lead_days": 7, "mass_lead_days": 24,
     "load_kg": 3.0, "standard_cost": 23.4, "standardization_level": "半定制",
     "applicable_industries": "化妆品套装/礼盒", "business_status": "标准"},
]


PART_TEMPLATES = [
    {"part_code": "RB01001-P01", "box_type_code": "YT-RB-01001-A", "seq": 1, "name": "盖面",
     "component": "上盖", "material": "灰板 2.0mm", "quantity": 1, "size_expr": "L+4t+2c  ×  W+4t+2c",
     "size_length_expr": "L+4t+2c", "size_width_expr": "W+4t+2c", "size_height_expr": "",
     "sample_value": "207.6 × 157.6", "key_process": "开料/V槽", "is_optional": 0,
     "note": "全包边，四周留包边量"},
    {"part_code": "RB01001-P02", "box_type_code": "YT-RB-01001-A", "seq": 2, "name": "盖壁（长边）",
     "component": "上盖", "material": "灰板 2.0mm", "quantity": 2, "size_expr": "L+4t+2c  ×  H盖",
     "size_length_expr": "L+4t+2c", "size_width_expr": "H盖", "size_height_expr": "",
     "sample_value": "207.6 × 30.0", "key_process": "开料/V槽", "is_optional": 0, "note": "V槽折直角"},
    {"part_code": "RB01001-P03", "box_type_code": "YT-RB-01001-A", "seq": 3, "name": "盖壁（短边）",
     "component": "上盖", "material": "灰板 2.0mm", "quantity": 2, "size_expr": "W+4t+2c  ×  H盖",
     "size_length_expr": "W+4t+2c", "size_width_expr": "H盖", "size_height_expr": "",
     "sample_value": "157.6 × 30.0", "key_process": "开料/V槽", "is_optional": 0, "note": "V槽折直角"},
    {"part_code": "RB01001-P04", "box_type_code": "YT-RB-01001-A", "seq": 4, "name": "底面",
     "component": "下底", "material": "灰板 2.0mm", "quantity": 1, "size_expr": "L  ×  W",
     "size_length_expr": "L", "size_width_expr": "W", "size_height_expr": "",
     "sample_value": "200.0 × 150.0", "key_process": "开料", "is_optional": 0, "note": "—"},
    {"part_code": "RB01001-P05", "box_type_code": "YT-RB-01001-A", "seq": 5, "name": "底壁（长边）",
     "component": "下底", "material": "灰板 2.0mm", "quantity": 2, "size_expr": "L+2t  ×  H",
     "size_length_expr": "L+2t", "size_width_expr": "H", "size_height_expr": "",
     "sample_value": "204.0 × 60.0", "key_process": "开料/V槽", "is_optional": 0, "note": "—"},
    {"part_code": "RB01001-P06", "box_type_code": "YT-RB-01001-A", "seq": 6, "name": "底壁（短边）",
     "component": "下底", "material": "灰板 2.0mm", "quantity": 2, "size_expr": "W  ×  H",
     "size_length_expr": "W", "size_width_expr": "H", "size_height_expr": "",
     "sample_value": "150.0 × 60.0", "key_process": "开料/V槽", "is_optional": 0,
     "note": "与长边咬合，扣减 2t"},
    {"part_code": "RB01001-P07", "box_type_code": "YT-RB-01001-A", "seq": 7, "name": "盖面纸",
     "component": "面纸", "material": "特种纸 200g", "quantity": 1, "size_expr": "盖展开尺寸 + 包边 + 出血3mm",
     "size_length_expr": "盖展开尺寸 + 包边 + 出血3mm", "size_width_expr": "", "size_height_expr": "",
     "sample_value": "238.0 × 188.0", "key_process": "印刷/覆膜/烫金/模切", "is_optional": 0,
     "note": "丝向平行于长边"},
    {"part_code": "RB01001-P08", "box_type_code": "YT-RB-01001-A", "seq": 8, "name": "盒身面纸",
     "component": "面纸", "material": "特种纸 200g", "quantity": 1, "size_expr": "盒身展开尺寸 + 包边 + 出血3mm",
     "size_length_expr": "盒身展开尺寸 + 包边 + 出血3mm", "size_width_expr": "", "size_height_expr": "",
     "sample_value": "234.0 × 128.0", "key_process": "印刷/覆膜/烫金/模切", "is_optional": 0, "note": "—"},
    {"part_code": "RB01001-P09", "box_type_code": "YT-RB-01001-A", "seq": 9, "name": "内托",
     "component": "内托", "material": "EVA 植绒 5mm", "quantity": 1, "size_expr": "（L-4）×（W-4）× 8",
     "size_length_expr": "（L-4）", "size_width_expr": "（W-4）", "size_height_expr": "8",
     "sample_value": "196.0 × 146.0", "key_process": "模切/开槽", "is_optional": 0,
     "note": "引用内托库 YT-IN-001"},
    {"part_code": "RB01001-P10", "box_type_code": "YT-RB-01001-A", "seq": 10, "name": "丝带拉手",
     "component": "配件", "material": "涤纶丝带 10mm", "quantity": 1, "size_expr": "长度 = W/3 + 40",
     "size_length_expr": "长度 = W/3 + 40", "size_width_expr": "", "size_height_expr": "",
     "sample_value": "90.0", "key_process": "裁切/穿引", "is_optional": 1, "note": "选配"},
    {"part_code": "RB02001-P01", "box_type_code": "YT-RB-02001-A", "seq": 1, "name": "盖面",
     "component": "上盖", "material": "灰板 2.0mm", "quantity": 1, "size_expr": "L+4t+2c  ×  W+4t+2c",
     "size_length_expr": "L+4t+2c", "size_width_expr": "W+4t+2c", "size_height_expr": "",
     "sample_value": "207.6 × 157.6", "key_process": "开料/V槽", "is_optional": 0, "note": "—"},
    {"part_code": "RB02001-P02", "box_type_code": "YT-RB-02001-A", "seq": 2, "name": "盖壁（长边）",
     "component": "上盖", "material": "灰板 2.0mm", "quantity": 2, "size_expr": "L+4t+2c  ×  H盖",
     "size_length_expr": "L+4t+2c", "size_width_expr": "H盖", "size_height_expr": "",
     "sample_value": "207.6 × 28.0", "key_process": "开料/V槽", "is_optional": 0, "note": "—"},
    {"part_code": "RB02001-P03", "box_type_code": "YT-RB-02001-A", "seq": 3, "name": "盖壁（短边）",
     "component": "上盖", "material": "灰板 2.0mm", "quantity": 2, "size_expr": "W+4t+2c  ×  H盖",
     "size_length_expr": "W+4t+2c", "size_width_expr": "H盖", "size_height_expr": "",
     "sample_value": "157.6 × 28.0", "key_process": "开料/V槽", "is_optional": 0, "note": "—"},
    {"part_code": "RB02001-P04", "box_type_code": "YT-RB-02001-A", "seq": 4, "name": "底面",
     "component": "下底", "material": "灰板 2.0mm", "quantity": 1, "size_expr": "L  ×  W",
     "size_length_expr": "L", "size_width_expr": "W", "size_height_expr": "",
     "sample_value": "200.0 × 150.0", "key_process": "开料", "is_optional": 0, "note": "—"},
    {"part_code": "RB02001-P05", "box_type_code": "YT-RB-02001-A", "seq": 5, "name": "底壁（长边）",
     "component": "下底", "material": "灰板 2.0mm", "quantity": 2, "size_expr": "L+2t  ×  H",
     "size_length_expr": "L+2t", "size_width_expr": "H", "size_height_expr": "",
     "sample_value": "204.0 × 55.0", "key_process": "开料/V槽", "is_optional": 0, "note": "—"},
    {"part_code": "RB02001-P06", "box_type_code": "YT-RB-02001-A", "seq": 6, "name": "底壁（短边）",
     "component": "下底", "material": "灰板 2.0mm", "quantity": 2, "size_expr": "W  ×  H",
     "size_length_expr": "W", "size_width_expr": "H", "size_height_expr": "",
     "sample_value": "150.0 × 55.0", "key_process": "开料/V槽", "is_optional": 0, "note": "—"},
    {"part_code": "RB02001-P07", "box_type_code": "YT-RB-02001-A", "seq": 7, "name": "铰链布",
     "component": "铰链", "material": "装帧布/充皮纸", "quantity": 1, "size_expr": "（L+2t）× 铰链宽40",
     "size_length_expr": "（L+2t）", "size_width_expr": "铰链宽40", "size_height_expr": "",
     "sample_value": "204.0 × 40.0", "key_process": "裁切/贴合", "is_optional": 0,
     "note": "耐折 ≥3000 次"},
    {"part_code": "RB02001-P08", "box_type_code": "YT-RB-02001-A", "seq": 8, "name": "磁铁",
     "component": "配件", "material": "钕铁硼 Ø10×2mm", "quantity": 2, "size_expr": "标准件",
     "size_length_expr": "标准件", "size_width_expr": "", "size_height_expr": "",
     "sample_value": "Ø10×2", "key_process": "嵌入/定位", "is_optional": 0, "note": "上下对位，偏差≤0.5mm"},
    {"part_code": "RB02001-P09", "box_type_code": "YT-RB-02001-A", "seq": 9, "name": "面纸（整体）",
     "component": "面纸", "material": "特种纸 200g", "quantity": 1, "size_expr": "整体展开 + 包边 + 出血3mm",
     "size_length_expr": "整体展开 + 包边 + 出血3mm", "size_width_expr": "", "size_height_expr": "",
     "sample_value": "250.0 × 330.0", "key_process": "印刷/覆膜/烫金/模切", "is_optional": 0,
     "note": "含铰链位"},
    {"part_code": "RB02001-P10", "box_type_code": "YT-RB-02001-A", "seq": 10, "name": "内托",
     "component": "内托", "material": "海绵裱绒", "quantity": 1, "size_expr": "（L-4）×（W-4）× 15",
     "size_length_expr": "（L-4）", "size_width_expr": "（W-4）", "size_height_expr": "15",
     "sample_value": "196.0 × 146.0", "key_process": "开槽/裱绒", "is_optional": 0,
     "note": "引用内托库 YT-IN-004"},
    {"part_code": "RB03001-P01", "box_type_code": "YT-RB-03001-A", "seq": 1, "name": "外盒面",
     "component": "外盒", "material": "灰板 2.0mm", "quantity": 1, "size_expr": "L外+2t  ×  W外+2t",
     "size_length_expr": "L外+2t", "size_width_expr": "W外+2t", "size_height_expr": "",
     "sample_value": "180.0 × 130.0", "key_process": "开料/V槽", "is_optional": 0, "note": "—"},
    {"part_code": "RB03001-P02", "box_type_code": "YT-RB-03001-A", "seq": 2, "name": "外盒壁（长边）",
     "component": "外盒", "material": "灰板 2.0mm", "quantity": 2, "size_expr": "L外+2t  ×  W外",
     "size_length_expr": "L外+2t", "size_width_expr": "W外", "size_height_expr": "",
     "sample_value": "180.0 × 50.0", "key_process": "开料/V槽", "is_optional": 0, "note": "—"},
    {"part_code": "RB03001-P03", "box_type_code": "YT-RB-03001-A", "seq": 3, "name": "外盒壁（短边）",
     "component": "外盒", "material": "灰板 2.0mm", "quantity": 2, "size_expr": "W外  ×  W外",
     "size_length_expr": "W外", "size_width_expr": "W外", "size_height_expr": "",
     "sample_value": "130.0 × 50.0", "key_process": "开料/V槽", "is_optional": 0, "note": "—"},
    {"part_code": "RB03001-P04", "box_type_code": "YT-RB-03001-A", "seq": 4, "name": "内盒底",
     "component": "内盒", "material": "灰板 1.5mm", "quantity": 1, "size_expr": "L内  ×  W内",
     "size_length_expr": "L内", "size_width_expr": "W内", "size_height_expr": "",
     "sample_value": "172.0 × 122.0", "key_process": "开料", "is_optional": 0, "note": "公差 ±0.5mm"},
    {"part_code": "RB03001-P05", "box_type_code": "YT-RB-03001-A", "seq": 5, "name": "内盒壁（长边）",
     "component": "内盒", "material": "灰板 1.5mm", "quantity": 2, "size_expr": "L内  ×  H内",
     "size_length_expr": "L内", "size_width_expr": "H内", "size_height_expr": "",
     "sample_value": "172.0 × 42.0", "key_process": "开料/V槽", "is_optional": 0, "note": "公差 ±0.5mm"},
    {"part_code": "RB03001-P06", "box_type_code": "YT-RB-03001-A", "seq": 6, "name": "内盒壁（短边）",
     "component": "内盒", "material": "灰板 1.5mm", "quantity": 2, "size_expr": "W内-2t  ×  H内",
     "size_length_expr": "W内-2t", "size_width_expr": "H内", "size_height_expr": "",
     "sample_value": "119.0 × 42.0", "key_process": "开料/V槽", "is_optional": 0, "note": "公差 ±0.5mm"},
    {"part_code": "RB03001-P07", "box_type_code": "YT-RB-03001-A", "seq": 7, "name": "拉带",
     "component": "配件", "material": "涤纶丝带 8mm", "quantity": 1, "size_expr": "长度 = W外/2 + 60",
     "size_length_expr": "长度 = W外/2 + 60", "size_width_expr": "", "size_height_expr": "",
     "sample_value": "125.0", "key_process": "裁切/穿引", "is_optional": 0, "note": "抽拉阻力控制在 1.5–3.0N"},
    {"part_code": "RB03001-P08", "box_type_code": "YT-RB-03001-A", "seq": 8, "name": "外盒面纸",
     "component": "面纸", "material": "特种纸 200g", "quantity": 1, "size_expr": "外盒展开 + 包边 + 出血3mm",
     "size_length_expr": "外盒展开 + 包边 + 出血3mm", "size_width_expr": "", "size_height_expr": "",
     "sample_value": "212.0 × 268.0", "key_process": "印刷/覆膜/烫金/模切", "is_optional": 0, "note": "—"},
    {"part_code": "RB03001-P09", "box_type_code": "YT-RB-03001-A", "seq": 9, "name": "内盒面纸",
     "component": "面纸", "material": "特种纸 157g", "quantity": 1, "size_expr": "内盒展开 + 包边 + 出血3mm",
     "size_length_expr": "内盒展开 + 包边 + 出血3mm", "size_width_expr": "", "size_height_expr": "",
     "sample_value": "196.0 × 200.0", "key_process": "印刷/模切", "is_optional": 0, "note": "—"},
    {"part_code": "RB03001-P10", "box_type_code": "YT-RB-03001-A", "seq": 10, "name": "内托",
     "component": "内托", "material": "纸浆模塑", "quantity": 1, "size_expr": "（L内-4）×（W内-4）× 12",
     "size_length_expr": "（L内-4）", "size_width_expr": "（W内-4）", "size_height_expr": "12",
     "sample_value": "168.0 × 118.0", "key_process": "模塑成型", "is_optional": 0,
     "note": "引用内托库 YT-IN-002"},
    {"part_code": "RB03001-P11", "box_type_code": "YT-RB-03001-A", "seq": 11, "name": "防磨条",
     "component": "配件", "material": "绒布条 5mm", "quantity": 2, "size_expr": "长度 = L内",
     "size_length_expr": "长度 = L内", "size_width_expr": "", "size_height_expr": "",
     "sample_value": "172.0", "key_process": "裁切/贴合", "is_optional": 0, "note": "降低外盒磨损"},
]


PROCESS_TEMPLATES = [
    {"box_type_code": "YT-RB-01001-A", "part_code": "RB01001-P01", "seq": 10, "step_name": "灰板开料",
     "workstation": "灰板分切机", "work_content": "按部件尺寸裁切灰板", "standard_seconds": 10.0,
     "automation": "自动", "control_point": "裁切精度±0.2mm，边缘不起层", "parallel_ok": 0},
    {"box_type_code": "YT-RB-01001-A", "part_code": "RB01001-P01", "seq": 20, "step_name": "V 槽开槽",
     "workstation": "V 槽机", "work_content": "盖壁/底壁折角处开 V 型槽", "standard_seconds": 15.0,
     "automation": "自动", "control_point": "槽深 = 板厚的 2/3，角度 90°", "parallel_ok": 0},
    {"box_type_code": "YT-RB-01001-A", "part_code": "RB01001-P01", "seq": 30, "step_name": "灰板成型",
     "workstation": "自动成型机", "work_content": "折边、贴角、拼合成盒型", "standard_seconds": 20.0,
     "automation": "自动", "control_point": "直角方正，无歪斜", "parallel_ok": 0},
    {"box_type_code": "YT-RB-01001-A", "part_code": "RB01001-P01", "seq": 40, "step_name": "面纸印刷",
     "workstation": "胶印机", "work_content": "四色/专色印刷（按拼版分摊）", "standard_seconds": 15.0,
     "automation": "自动", "control_point": "ICC 色彩管理，ΔE≤2", "parallel_ok": 1},
    {"box_type_code": "YT-RB-01001-A", "part_code": "RB01001-P02", "seq": 10, "step_name": "表面处理",
     "workstation": "覆膜机/烫金机", "work_content": "覆膜 → 烫金 → 局部UV（选配）", "standard_seconds": 12.0,
     "automation": "自动", "control_point": "工序顺序不可颠倒", "parallel_ok": 1},
    {"box_type_code": "YT-RB-01001-A", "part_code": "RB01001-P02", "seq": 20, "step_name": "面纸模切",
     "workstation": "模切机", "work_content": "按刀线模切，含压痕", "standard_seconds": 8.0,
     "automation": "自动", "control_point": "套准精度±0.3mm", "parallel_ok": 1},
    {"box_type_code": "YT-RB-01001-A", "part_code": "RB01001-P02", "seq": 30, "step_name": "机裱",
     "workstation": "裱糊机", "work_content": "平面件面纸裱糊（盖面、底面）", "standard_seconds": 18.0,
     "automation": "自动", "control_point": "无气泡、无褶皱", "parallel_ok": 0},
    {"box_type_code": "YT-RB-01001-A", "part_code": "RB01001-P03", "seq": 10, "step_name": "手裱",
     "workstation": "手工工位", "work_content": "立体件包边、转角、翻边", "standard_seconds": 55.0,
     "automation": "手工", "control_point": "转角圆润，无起泡", "parallel_ok": 0},
    {"box_type_code": "YT-RB-01001-A", "part_code": "RB01001-P03", "seq": 20, "step_name": "组装",
     "workstation": "手工工位", "work_content": "盖身配合、装内托、装配件", "standard_seconds": 22.0,
     "automation": "手工", "control_point": "配合间隙均匀，开合顺畅", "parallel_ok": 0},
    {"box_type_code": "YT-RB-01001-A", "part_code": "RB01001-P03", "seq": 30, "step_name": "检验",
     "workstation": "检验台", "work_content": "外观、尺寸、开合、清洁度", "standard_seconds": 10.0,
     "automation": "手工", "control_point": "按 AQL 抽样", "parallel_ok": 0},
    {"box_type_code": "YT-RB-01001-A", "part_code": "RB01001-P03", "seq": 40, "step_name": "清洁包装",
     "workstation": "包装工位", "work_content": "除尘、装袋、装箱", "standard_seconds": 8.0,
     "automation": "手工", "control_point": "按客户包装规范", "parallel_ok": 0},
    {"box_type_code": "YT-RB-02001-A", "part_code": "RB02001-P01", "seq": 10, "step_name": "灰板开料",
     "workstation": "灰板分切机", "work_content": "按部件尺寸裁切灰板", "standard_seconds": 12.0,
     "automation": "自动", "control_point": "裁切精度±0.2mm", "parallel_ok": 0},
    {"box_type_code": "YT-RB-02001-A", "part_code": "RB02001-P01", "seq": 20, "step_name": "V 槽开槽",
     "workstation": "V 槽机", "work_content": "折角处开 V 型槽", "standard_seconds": 18.0,
     "automation": "自动", "control_point": "槽深 = 板厚的 2/3", "parallel_ok": 0},
    {"box_type_code": "YT-RB-02001-A", "part_code": "RB02001-P01", "seq": 30, "step_name": "灰板成型",
     "workstation": "自动成型机", "work_content": "折边、贴角", "standard_seconds": 22.0, "automation": "自动",
     "control_point": "直角方正", "parallel_ok": 0},
    {"box_type_code": "YT-RB-02001-A", "part_code": "RB02001-P05", "seq": 10, "step_name": "面纸印刷",
     "workstation": "胶印机", "work_content": "四色/专色印刷", "standard_seconds": 18.0, "automation": "自动",
     "control_point": "ΔE≤2", "parallel_ok": 1},
    {"box_type_code": "YT-RB-02001-A", "part_code": "RB02001-P05", "seq": 20, "step_name": "表面处理",
     "workstation": "覆膜机/烫金机", "work_content": "覆膜 → 烫金 → 压凹凸", "standard_seconds": 20.0,
     "automation": "自动", "control_point": "顺序不可颠倒", "parallel_ok": 1},
    {"box_type_code": "YT-RB-02001-A", "part_code": "RB02001-P05", "seq": 30, "step_name": "面纸模切",
     "workstation": "模切机", "work_content": "按刀线模切", "standard_seconds": 10.0, "automation": "自动",
     "control_point": "套准精度±0.3mm", "parallel_ok": 1},
    {"box_type_code": "YT-RB-02001-A", "part_code": "RB02001-P05", "seq": 40, "step_name": "铰链贴合",
     "workstation": "手工工位", "work_content": "装帧布铰链定位贴合", "standard_seconds": 25.0,
     "automation": "手工", "control_point": "对位准确，无偏斜", "parallel_ok": 0},
    {"box_type_code": "YT-RB-02001-A", "part_code": "RB02001-P05", "seq": 50, "step_name": "磁铁嵌入",
     "workstation": "手工工位", "work_content": "磁铁定位嵌入并固定", "standard_seconds": 15.0,
     "automation": "手工", "control_point": "上下对位偏差≤0.5mm", "parallel_ok": 0},
    {"box_type_code": "YT-RB-02001-A", "part_code": "RB02001-P05", "seq": 60, "step_name": "手裱",
     "workstation": "手工工位", "work_content": "整体裱糊、包边、转角", "standard_seconds": 120.0,
     "automation": "手工", "control_point": "铰链位不起皱", "parallel_ok": 0},
    {"box_type_code": "YT-RB-02001-A", "part_code": "RB02001-P05", "seq": 70, "step_name": "组装",
     "workstation": "手工工位", "work_content": "装内托、合盖调试", "standard_seconds": 25.0,
     "automation": "手工", "control_point": "开合顺畅无异响", "parallel_ok": 0},
    {"box_type_code": "YT-RB-02001-A", "part_code": "RB02001-P07", "seq": 10, "step_name": "检验",
     "workstation": "检验台", "work_content": "开合寿命抽测、外观、尺寸", "standard_seconds": 12.0,
     "automation": "手工", "control_point": "铰链抽测 ≥3000 次", "parallel_ok": 0},
    {"box_type_code": "YT-RB-02001-A", "part_code": "RB02001-P07", "seq": 20, "step_name": "清洁包装",
     "workstation": "包装工位", "work_content": "除尘、装袋、装箱", "standard_seconds": 8.0,
     "automation": "手工", "control_point": "—", "parallel_ok": 0},
]


ACCESSORIES = [
    {"accessory_code": "YT-IN-001", "name": "EVA 植绒内托", "material": "EVA + 植绒布",
     "thickness_spec": "3 / 5 / 8 / 10", "forming": "模切/开槽/植绒", "tooling_cost": 1200.0,
     "unit_cost_min": 1.2, "unit_cost_max": 3.5, "eco_attr": "含塑料，非单一材质",
     "applicable_category": "化妆品/数码/珠宝", "moq": 500, "note": "缓冲好、档次高，回收性差"},
    {"accessory_code": "YT-IN-002", "name": "纸浆模塑内托", "material": "甘蔗渣/竹浆",
     "thickness_spec": "1.5 / 2.0 / 3.0", "forming": "模塑成型", "tooling_cost": 8000.0,
     "unit_cost_min": 0.6, "unit_cost_max": 1.8, "eco_attr": "可回收、可降解",
     "applicable_category": "消费电子/食品/日化", "moq": 5000, "note": "裕同优势品类，替代塑料首选"},
    {"accessory_code": "YT-IN-003", "name": "吸塑内托", "material": "PET / RPET",
     "thickness_spec": "0.3 / 0.5 / 0.8", "forming": "吸塑成型", "tooling_cost": 5000.0,
     "unit_cost_min": 0.5, "unit_cost_max": 1.5, "eco_attr": "RPET 可回收",
     "applicable_category": "数码配件/小家电", "moq": 3000, "note": "透明、贴合度高"},
    {"accessory_code": "YT-IN-004", "name": "海绵裱绒内托", "material": "海绵 + 绒布",
     "thickness_spec": "10 / 15 / 20", "forming": "开槽/裱绒", "tooling_cost": 600.0,
     "unit_cost_min": 1.5, "unit_cost_max": 4.0, "eco_attr": "含塑料",
     "applicable_category": "珠宝/腕表/收藏品", "moq": 300, "note": "手感最好，环保性弱"},
    {"accessory_code": "YT-IN-005", "name": "纸质井字格", "material": "瓦楞/卡纸",
     "thickness_spec": "2 / 3", "forming": "模切/插合", "tooling_cost": 400.0, "unit_cost_min": 0.4,
     "unit_cost_max": 1.2, "eco_attr": "全纸可回收", "applicable_category": "酒类/玻璃杯/套装", "moq": 500,
     "note": "全纸方案，PPWR 友好"},
    {"accessory_code": "YT-IN-006", "name": "卡纸折叠内托", "material": "白卡 300–400g",
     "thickness_spec": "0.4 / 0.5", "forming": "模切/折叠", "tooling_cost": 400.0,
     "unit_cost_min": 0.3, "unit_cost_max": 0.9, "eco_attr": "全纸可回收",
     "applicable_category": "食品/茶叶/轻件", "moq": 500, "note": "成本最低的全纸方案"},
    {"accessory_code": "YT-IN-007", "name": "丝绸/绒布包覆内托", "material": "灰板 + 绸布",
     "thickness_spec": "2 / 3", "forming": "开槽/包覆", "tooling_cost": 900.0, "unit_cost_min": 2.0,
     "unit_cost_max": 5.0, "eco_attr": "需拆分回收", "applicable_category": "奢侈品/腕表", "moq": 300,
     "note": "全手工，工时高"},
    {"accessory_code": "YT-IN-008", "name": "魔术贴固定带", "material": "尼龙 + 织带", "thickness_spec": "—",
     "forming": "裁切/缝合", "tooling_cost": 300.0, "unit_cost_min": 0.2, "unit_cost_max": 0.6,
     "eco_attr": "含塑料", "applicable_category": "数码配件/工具", "moq": 1000, "note": "配件类，非结构内托"},
    {"accessory_code": "YT-AC-001", "name": "钕铁硼磁铁", "material": "NdFeB",
     "thickness_spec": "Ø10×2 / Ø12×2", "forming": "标准件", "tooling_cost": 0.0,
     "unit_cost_min": 0.15, "unit_cost_max": 0.4, "eco_attr": "可分离回收",
     "applicable_category": "书型盒/磁吸盒", "moq": 1000, "note": "磁吸盒常用 2 片"},
    {"accessory_code": "YT-AC-002", "name": "涤纶丝带拉手", "material": "涤纶织带",
     "thickness_spec": "宽 8 / 10 / 15mm", "forming": "裁切/穿引", "tooling_cost": 0.0,
     "unit_cost_min": 0.1, "unit_cost_max": 0.3, "eco_attr": "可回收设计可免塑",
     "applicable_category": "抽屉盒/礼盒", "moq": 1000, "note": "选配"},
    {"accessory_code": "YT-AC-003", "name": "金属搭扣", "material": "锌合金", "thickness_spec": "标准件",
     "forming": "标准件", "tooling_cost": 0.0, "unit_cost_min": 0.8, "unit_cost_max": 2.5,
     "eco_attr": "可分离回收", "applicable_category": "翻盖盒/书型盒", "moq": 500, "note": "高档感强"},
    {"accessory_code": "YT-AC-004", "name": "装帧布铰链", "material": "装帧布/充皮纸",
     "thickness_spec": "0.3", "forming": "裁切/贴合", "tooling_cost": 0.0, "unit_cost_min": 0.3,
     "unit_cost_max": 0.8, "eco_attr": "需拆分回收", "applicable_category": "书型盒", "moq": 500,
     "note": "耐折要求 ≥3000 次"},
]


# 包装物料 + 价格：价格表（kb_material_price）没有行业列，行业由父表 kb_material 继承，
# 所以 kb_repo.current_price(industry=...) 会先解析父表再取价，绝不跨行业回落。
MATERIALS = [
    {"material": {"material_code": "MAT-PKG-GREYBOARD", "name": "灰板（双灰纸板）",
                  "grade": "2.0mm", "category": "包材", "form": "板材",
                  "spec": "t2.0，双灰，含水率 8%±2", "density": 0.75, "base_unit": "kg",
                  "standard_loss_rate": 0.08, "storage_req": "平放防潮，避免折痕"},
     "properties": [{"prop_key": "thickness", "prop_name": "厚度", "value_num": 2.0, "unit": "mm"},
                    {"prop_key": "density", "prop_name": "定量", "value_num": 0.75, "unit": "g/cm³"},
                    {"prop_key": "grain", "prop_name": "丝向", "value_text": "平行长边"}],
     "price": {"price": 6.5, "unit": "kg", "price_type": "contract",
               "source_name": "2026 年度包装材料框架协议", "confidence": 0.95}},
    {"material": {"material_code": "MAT-PKG-FACEPAPER", "name": "铜版纸面纸",
                  "grade": "157g", "category": "包材", "form": "卷料",
                  "spec": "157g/㎡，单面涂布，可印刷/覆膜", "density": 1.25, "base_unit": "kg",
                  "standard_loss_rate": 0.06},
     "properties": [{"prop_key": "gsm", "prop_name": "克重", "value_num": 157, "unit": "g/㎡"},
                    {"prop_key": "whiteness", "prop_name": "白度", "value_text": "≥90%"}],
     "price": {"price": 12.8, "unit": "kg", "price_type": "contract",
               "source_name": "2026 年度包装材料框架协议", "confidence": 0.95}},
    {"material": {"material_code": "MAT-PKG-LINER", "name": "内衬纸（牛皮衬纸）",
                  "grade": "80g", "category": "包材", "form": "卷料",
                  "spec": "80g/㎡ 本白牛皮，用于内衬与包边", "density": 0.85, "base_unit": "kg",
                  "standard_loss_rate": 0.05},
     "properties": [{"prop_key": "gsm", "prop_name": "克重", "value_num": 80, "unit": "g/㎡"}],
     "price": {"price": 9.6, "unit": "kg", "price_type": "contract",
               "source_name": "2026 年度包装材料框架协议", "confidence": 0.95}},
    {"material": {"material_code": "MAT-PKG-SPECIAL", "name": "特种纸（充皮纸）",
                  "grade": "120g", "category": "包材", "form": "卷料",
                  "spec": "120g/㎡ 充皮纹，用于精装盒面纸", "density": 1.10, "base_unit": "kg",
                  "standard_loss_rate": 0.09},
     "properties": [{"prop_key": "gsm", "prop_name": "克重", "value_num": 120, "unit": "g/㎡"},
                    {"prop_key": "texture", "prop_name": "纹理", "value_text": "充皮纹"}],
     "price": {"price": 26.0, "unit": "kg", "price_type": "contract",
               "source_name": "2026 年度包装材料框架协议", "confidence": 0.9}},
    {"material": {"material_code": "MAT-PKG-EVA", "name": "EVA 片材",
                  "grade": "38°", "category": "包材", "form": "片材",
                  "spec": "t10，38° 硬度，用于植绒内托基材", "density": 0.94, "base_unit": "kg",
                  "standard_loss_rate": 0.10},
     "properties": [{"prop_key": "hardness", "prop_name": "硬度", "value_num": 38, "unit": "Shore A"},
                    {"prop_key": "thickness", "prop_name": "厚度", "value_num": 10.0, "unit": "mm"}],
     "price": {"price": 18.5, "unit": "kg", "price_type": "internal_purchase",
               "source_name": "2026 年度采购框架协议", "confidence": 0.9}},
]

# 包装费率：手裱/组装走工时，印刷/表面处理/模切走加工单价；带最低收费的用于演示
# 小批量打样的门槛价（包装成本公式第 7 批消费，本批只入库）。
COST_RATES = [
    {"rate_code": "RATE-PKG-LABOR-LAMINATE", "name": "裱糊工时费率", "rate_type": "labor",
     "scope_type": "global", "value": 42.0, "unit": "元/小时", "minimum_charge": 0.0,
     "note": "机裱为主，含上下料"},
    {"rate_code": "RATE-PKG-LABOR-HANDMOUNT", "name": "手裱工时费率", "rate_type": "labor",
     "scope_type": "global", "value": 58.0, "unit": "元/小时", "minimum_charge": 200.0,
     "note": "手裱工位，单批不足 200 元按 200 元计"},
    {"rate_code": "RATE-PKG-LABOR-ASSEMBLY", "name": "组装包装工时费率", "rate_type": "labor",
     "scope_type": "global", "value": 38.0, "unit": "元/小时", "minimum_charge": 0.0,
     "note": "组装 + 清洁包装"},
    {"rate_code": "RATE-PKG-EQUIP-VGROOVE", "name": "V 槽加工费率", "rate_type": "equipment_dep",
     "scope_type": "global", "value": 0.55, "unit": "元/次", "minimum_charge": 120.0,
     "note": "按开槽次数计，含模具折旧"},
    {"rate_code": "RATE-PKG-EQUIP-MOULD", "name": "模切加工费率", "rate_type": "equipment_dep",
     "scope_type": "global", "value": 0.35, "unit": "元/次", "minimum_charge": 300.0,
     "note": "刀模摊销另计（见成本公式）"},
    {"rate_code": "RATE-PKG-EQUIP-PRINT", "name": "胶印印刷费率", "rate_type": "equipment_dep",
     "scope_type": "global", "value": 0.45, "unit": "元/色令", "minimum_charge": 0.0,
     "note": "四色印刷，含过油"},
    {"rate_code": "RATE-PKG-EQUIP-SURFACE", "name": "表面处理费率（覆膜/烫金）",
     "rate_type": "equipment_dep", "scope_type": "global", "value": 0.18, "unit": "元/次",
     "minimum_charge": 150.0, "note": "覆膜与烫金共用，按实际工序次数计"},
    {"rate_code": "RATE-PKG-OVERHEAD", "name": "包装制造费用费率", "rate_type": "overhead",
     "scope_type": "global", "value": 12.0, "unit": "元/小时", "minimum_charge": 0.0,
     "note": "车间管理与能耗分摊"},
]

# 包装损耗率 / 良率 / 税率 / 毛利系数（cost_lookup 现有的 factor_type 闭集内）。
COST_FACTORS = [
    {"factor_code": "F-PKG-LOSS-GREYBOARD", "name": "灰板开料损耗率", "factor_type": "scrap",
     "applicable_scope": "包材", "value": 0.08, "note": "按灰板面积计"},
    {"factor_code": "F-PKG-LOSS-PAPER", "name": "面纸/内衬纸印刷损耗率", "factor_type": "scrap",
     "applicable_scope": "包材", "value": 0.06, "note": "含试印与套准调整"},
    {"factor_code": "F-PKG-YIELD-ASSEMBLY", "name": "组装良率", "factor_type": "yield",
     "applicable_scope": "包材件", "value": 0.985, "note": "手裱/组装工位实测"},
    {"factor_code": "F-PKG-TAX-VAT", "name": "增值税率", "factor_type": "tax",
     "applicable_scope": "全国", "value": 0.13, "note": "制造类适用税率"},
    {"factor_code": "F-PKG-MARGIN-DEFAULT", "name": "包装默认毛利系数", "factor_type": "margin",
     "applicable_scope": "包材", "value": 0.28, "note": "演示默认，实际按报价策略取"},
]

# 成本公式占位：本批只入库（review_status='draft'），第 7 批才做求值。
COST_FORMULAS = [
    {"formula_code": "PKG-F-GREYBOARD", "cost_category": "材料费", "process_code": "PKG-P-GREYBOARD",
     "rate_code": "", "expression": "Σ(部件展开面积㎡ × 灰板定量 × 单价 × (1+损耗率))",
     "minimum_charge": 0.0, "quantity_basis": "按单件", "amortization_basis": "",
     "loss_scope": "灰板开料", "rounding": "保留 4 位小数",
     "source_ref": "报价逻辑-0903.xlsx / 材料费", "formula_version": "draft-1",
     "review_status": "draft"},
    {"formula_code": "PKG-F-FACEPAPER", "cost_category": "材料费", "process_code": "PKG-P-FACEPAPER",
     "rate_code": "", "expression": "Σ(面纸放数面积㎡ × 克重/1000 × 单价 × (1+损耗率))",
     "minimum_charge": 0.0, "quantity_basis": "按单件", "amortization_basis": "",
     "loss_scope": "面纸印刷与模切", "rounding": "保留 4 位小数",
     "source_ref": "报价逻辑-0903.xlsx / 材料费", "formula_version": "draft-1",
     "review_status": "draft"},
    {"formula_code": "PKG-F-INSERT", "cost_category": "配件费", "process_code": "PKG-P-INSERT",
     "rate_code": "", "expression": "Σ(内托/配件单件成本 × 数量) + 模具费/订单数量",
     "minimum_charge": 0.0, "quantity_basis": "按单件", "amortization_basis": "模具费按订单数量摊",
     "loss_scope": "按内托配件库口径", "rounding": "保留 2 位小数",
     "source_ref": "报价逻辑-0903.xlsx / 配件费", "formula_version": "draft-1",
     "review_status": "draft"},
    {"formula_code": "PKG-F-PRINT", "cost_category": "加工费", "process_code": "PKG-P-PRINT",
     "rate_code": "RATE-PKG-EQUIP-PRINT",
     "expression": "印刷费率 × 色令数 + 上机调机费/订单数量",
     "minimum_charge": 0.0, "quantity_basis": "按单件", "amortization_basis": "调机费按订单数量摊",
     "loss_scope": "印刷套准与试印", "rounding": "保留 2 位小数",
     "source_ref": "报价逻辑-0903.xlsx / 加工费", "formula_version": "draft-1",
     "review_status": "draft"},
    {"formula_code": "PKG-F-SURFACE", "cost_category": "加工费", "process_code": "PKG-P-SURFACE",
     "rate_code": "RATE-PKG-EQUIP-SURFACE",
     "expression": "表面处理费率 × 工序次数，单批不足最低收费按最低收费计",
     "minimum_charge": 150.0, "quantity_basis": "按单件", "amortization_basis": "",
     "loss_scope": "覆膜/烫金废品", "rounding": "保留 2 位小数",
     "source_ref": "报价逻辑-0903.xlsx / 加工费", "formula_version": "draft-1",
     "review_status": "draft"},
    {"formula_code": "PKG-F-MOULD", "cost_category": "工装摊销", "process_code": "PKG-P-MOULD",
     "rate_code": "RATE-PKG-EQUIP-MOULD",
     "expression": "模切费率 × 冲次 + (刀模费 + V 槽模具费)/订单数量",
     "minimum_charge": 300.0, "quantity_basis": "按单件", "amortization_basis": "刀模/V 槽模具按订单数量摊",
     "loss_scope": "模切废品", "rounding": "保留 2 位小数",
     "source_ref": "报价逻辑-0903.xlsx / 工装摊销", "formula_version": "draft-1",
     "review_status": "draft"},
    {"formula_code": "PKG-F-LABOR", "cost_category": "人工费", "process_code": "PKG-P-LABOR",
     "rate_code": "RATE-PKG-LABOR-HANDMOUNT",
     "expression": "Σ(标准工时秒/3600 × 对应工时费率)",
     "minimum_charge": 200.0, "quantity_basis": "按单件", "amortization_basis": "",
     "loss_scope": "手裱返工工时", "rounding": "保留 2 位小数",
     "source_ref": "报价逻辑-0903.xlsx / 人工费", "formula_version": "draft-1",
     "review_status": "draft"},
]

# 包材明细（包装第 7 批）：对齐 0903「包装运输 (2)」Sheet 的第 2–12 行（11 行）。
# 单价/用量/装数/尺寸逐格照抄；工作簿里空着的格子保持 None（不编数字），
# 那一行的单件成本按工作簿口径（空单元格 = 0）算出 0，不会伪造金额。
# 单件成本由第 7 批消费：FORMULA_CATALOG 的 PKG-P-* 表达式（表达式内已含 ÷ 装数）。
COST_CONTENTS = [
    {"content_code": "PKG-CT-CARTON", "name": "彩盒", "category": "纸箱",
     "material_spec": "BC(180/120/70/120/130)", "formula_code": "PKG-P-CARTON",
     "length_mm": 520.0, "width_mm": 420.0, "height_mm": 425.0, "usage_qty": 1.0,
     "material_price": 3.0, "units_per_pack": 4.0,
     "note": "0903 包装运输!J2（单件 2.1108074127397023）"},
    {"content_code": "PKG-CT-PAD", "name": "平卡", "category": "平卡",
     "material_spec": "A3A B坑(130/100/130)", "formula_code": "PKG-P-PAD",
     "length_mm": 510.0, "width_mm": 410.0, "usage_qty": 2.0,
     "material_price": 1.55, "units_per_pack": 4.0,
     "note": "0903 包装运输!J3（单件 0.2840410194527371）"},
    {"content_code": "PKG-CT-DIVIDER", "name": "隔卡", "category": "隔卡",
     "material_spec": "—", "formula_code": "PKG-P-DIVIDER",
     "material_price": 4200.0, "units_per_pack": 4.0,
     "note": "0903 包装运输!J4：工作簿缺尺寸与用量，按空单元格口径记 None"},
    {"content_code": "PKG-CT-BAG", "name": "胶袋", "category": "胶袋",
     "material_spec": "—", "formula_code": "PKG-P-BAG",
     "material_price": 18.0, "units_per_pack": 4.0,
     "note": "0903 包装运输!J5：工作簿缺尺寸与用量，按空单元格口径记 None"},
    {"content_code": "PKG-CT-CRAFT-PAPER", "name": "双胶纸", "category": "双胶纸",
     "material_spec": "100g", "formula_code": "PKG-P-CRAFT-PAPER",
     "gsm": 100.0, "material_price": 5800.0, "units_per_pack": 2.0,
     "note": "0903 包装运输!J6：工作簿缺尺寸与用量，按空单元格口径记 None"},
    {"content_code": "PKG-CT-STRAP", "name": "牛皮纸轧带", "category": "牛皮纸轧带",
     "material_spec": "30g", "formula_code": "PKG-P-STRAP",
     "length_mm": 787.0, "width_mm": 1092.0, "gsm": 30.0, "usage_qty": 4.0,
     "material_price": 7800.0, "units_per_pack": 4.0,
     "note": "0903 包装运输!J7（单件 0.1803071469026549）"},
    {"content_code": "PKG-CT-CORNER-TOP", "name": "顶部护角", "category": "护角",
     "material_spec": "—", "formula_code": "PKG-P-CORNER-TOP",
     "material_price": 1.9, "units_per_pack": 120.0,
     "note": "0903 包装运输!J8：工作簿缺尺寸与用量，按空单元格口径记 None"},
    {"content_code": "PKG-CT-CORNER-PAPER", "name": "纸护角", "category": "护角",
     "material_spec": "—", "formula_code": "PKG-P-CORNER-PAPER",
     "material_price": 1.9, "units_per_pack": 120.0,
     "note": "0903 包装运输!J9：工作簿缺尺寸与用量，按空单元格口径记 None"},
    {"content_code": "PKG-CT-LABEL", "name": "通用标签", "category": "标签",
     "material_spec": "—", "formula_code": "PKG-P-LABEL",
     "material_price": 0.05, "units_per_pack": 120.0,
     "note": "0903 包装运输!J10：工作簿缺用量，按空单元格口径记 None"},
    {"content_code": "PKG-CT-BOARD", "name": "盖板", "category": "盖板",
     "material_spec": "胶合木卡板", "formula_code": "PKG-P-BOARD",
     "material_price": 30.0, "units_per_pack": 120.0,
     "note": "0903 包装运输!J11：工作簿缺用量，按空单元格口径记 None"},
    {"content_code": "PKG-CT-PALLET", "name": "卡板", "category": "卡板",
     "material_spec": "胶卡板或木卡板(四侧脚柱喷绿色YUTO字样）", "formula_code": "PKG-P-PALLET",
     "length_mm": 1200.0, "width_mm": 1000.0, "height_mm": 120.0, "usage_qty": 1.0,
     "material_price": 70.0, "units_per_pack": 120.0,
     "note": "0903 包装运输!J12（单件 0.5162241887905605）"},
]

# 工装/刀模规则（包装第 7 批）：五种 mode 各给一条演示数据，金额/寿命是演示值。
TOOLING_RULES = [
    {"tooling_code": "T-PKG-HOTSTAMP-LIFE", "name": "烫金版（按寿命摊销）",
     "process_code": "烫金", "mode": "lifetime", "tooling_cost": 12000.0,
     "tooling_lifetime": 60000.0, "note": "默认口径：模具寿命 6 万次，与订单量无关"},
    {"tooling_code": "T-PKG-SILK-ONEOFF", "name": "丝印网版（一次性）",
     "process_code": "丝印", "mode": "one_off", "tooling_cost": 3600.0,
     "note": "一次性收取，按本单分摊量摊"},
    {"tooling_code": "T-PKG-EMBOSS-COMMIT", "name": "压凹凸钢模（按承诺量）",
     "process_code": "击凹凸", "mode": "committed", "tooling_cost": 24000.0,
     "note": "按项目承诺总量摊"},
    {"tooling_code": "T-PKG-DIE-REFUND", "name": "模切刀模（达量返还）",
     "process_code": "模切", "mode": "refund", "tooling_cost": 18000.0,
     "refund_threshold": 50000.0, "refundable": 1,
     "note": "累计数量达门槛只改状态，冲减由报价侧（第 8 批）处理"},
    {"tooling_code": "T-PKG-ASSEMBLY-CUSTOMER", "name": "装配线工装（客户自备）",
     "process_code": "装配线", "mode": "customer_supplied", "tooling_cost": 0.0,
     "note": "客户自备模具，只留痕不进成本"},
]

# 物流规则（整箱/托盘/打样快递三档），第 7 批定价时消费。
LOGISTICS_RULES = [
    {"rule_code": "PKG-LG-CARTON-STD", "units_per_carton": 24, "carton_size": "600×400×350mm",
     "pallet_qty": 20, "units_per_pallet": 480, "shipping_mode": "公路零担",
     "min_freight": 120.0, "loading_rate": "≥85%", "quantity_tier": "≥1 箱",
     "refund_condition": "整箱未拆封可退", "note": "标准外箱，含内衬隔板"},
    {"rule_code": "PKG-LG-PALLET-STD", "units_per_carton": 24, "carton_size": "600×400×350mm",
     "pallet_qty": 20, "units_per_pallet": 480, "shipping_mode": "整车",
     "min_freight": 800.0, "pallet_freight": 1650.0, "loading_rate": "≥92%",
     "quantity_tier": "≥1 托盘",
     "refund_condition": "不接受退运", "note": "托盘 1200×1000，缠膜加固"},
    {"rule_code": "PKG-LG-EXPRESS-SAMPLE", "units_per_carton": 1, "carton_size": "300×220×120mm",
     "pallet_qty": 0, "units_per_pallet": 0, "shipping_mode": "快递",
     "min_freight": 30.0, "loading_rate": "不适用", "quantity_tier": "打样 1–5 件",
     "refund_condition": "不接受退运", "note": "打样件走顺丰，运费据实结算"},
]

# 盒型五维匹配的权重与硬门槛（第 4 批的入参；dimension 取值闭集）。
MATCH_WEIGHTS = [
    {"dimension": "size_range", "weight": 0.30, "hard_gate": 0,
     "rule_expr": "成品内尺寸落在盒型可生产区间内，超区间按线性衰减计分"},
    {"dimension": "fit_clearance", "weight": 0.25, "hard_gate": 1,
     "rule_expr": "|盒型配合间隙 - 需求间隙| ≤ 0.5mm，超差直接淘汰"},
    {"dimension": "face_paper_gsm", "weight": 0.15, "hard_gate": 0,
     "rule_expr": "需求克重落在盒型面纸克重区间内得满分，超出取相邻档"},
    {"dimension": "closure_type", "weight": 0.20, "hard_gate": 1,
     "rule_expr": "闭合方式必须一致（天地盖/铰链+磁吸/抽屉+拉带…），不一致淘汰"},
    {"dimension": "v_groove", "weight": 0.10, "hard_gate": 0,
     "rule_expr": "V 槽要求一致得满分，盒型支持 V 槽而需求不要求时按 0.6 计"},
]


INDUSTRY = "packaging"


def _packaging_row(row: dict, source: str) -> dict:
    """给一条包装扩展表数据补公共列（industry/source/version/effective_from/status/时间戳）。"""
    out = dict(row)
    out["industry"] = INDUSTRY
    out["source"] = source
    out.setdefault("version", VERSION)
    out.setdefault("effective_from", SEED_DATE)
    out.setdefault("status", "active")
    out.setdefault("created_at", SEED_DATE)
    out.setdefault("updated_at", SEED_DATE)
    return out


def _subject_row(row: dict, source: str) -> dict:
    """给一条行业主体表数据补行业列（主体表没有 version/status 等公共列）。"""
    return {**row, "industry": INDUSTRY, "source": source, "effective_from": SEED_DATE}


def _seed_table(table: str, rows: list, *, keys: tuple, source: str,
                overwrite: bool) -> int:
    """按主键幂等写入一张包装表：overwrite=False 时整行跳过已存在的记录。"""
    for row in rows:
        data = _packaging_row(row, source)
        if not overwrite:
            where = " AND ".join(f"{key} = ?" for key in keys)
            if db.query_one(f"SELECT 1 FROM {table} WHERE {where}",
                            tuple(data[key] for key in keys)):
                continue
        db.upsert(table, data, keys=keys)
    return len(rows)


def seed_packaging(*, overwrite: bool = False) -> dict:
    """写入包装演示数据（幂等）。overwrite=False 时已存在的记录不覆盖。"""
    counts: dict = {}

    counts["kb_packaging_box_type"] = _seed_table(
        "kb_packaging_box_type", BOX_TYPES, keys=("box_type_code",),
        source=SOURCE_BOX_TYPE, overwrite=overwrite)
    counts["kb_packaging_part_template"] = _seed_table(
        "kb_packaging_part_template", PART_TEMPLATES, keys=("part_code",),
        source=SOURCE_PART_TEMPLATE, overwrite=overwrite)
    counts["kb_packaging_process_template"] = _seed_table(
        "kb_packaging_process_template", PROCESS_TEMPLATES,
        keys=("box_type_code", "part_code", "seq"),
        source=SOURCE_PROCESS_TEMPLATE, overwrite=overwrite)
    counts["kb_packaging_insert_accessory"] = _seed_table(
        "kb_packaging_insert_accessory", ACCESSORIES, keys=("accessory_code",),
        source=SOURCE_ACCESSORY, overwrite=overwrite)
    counts["kb_packaging_cost_formula"] = _seed_table(
        "kb_packaging_cost_formula", COST_FORMULAS, keys=("formula_code",),
        source=SOURCE_COST, overwrite=overwrite)
    counts["kb_packaging_logistics_rule"] = _seed_table(
        "kb_packaging_logistics_rule", LOGISTICS_RULES, keys=("rule_code",),
        source=SOURCE_LOGISTICS, overwrite=overwrite)
    counts["kb_packaging_match_weight"] = _seed_table(
        "kb_packaging_match_weight", MATCH_WEIGHTS, keys=("dimension",),
        source=SOURCE_MATCH_WEIGHT, overwrite=overwrite)
    counts["kb_packaging_cost_content"] = _seed_table(
        "kb_packaging_cost_content", COST_CONTENTS, keys=("content_code",),
        source=SOURCE_COST_CONTENT, overwrite=overwrite)
    counts["kb_packaging_tooling_rule"] = _seed_table(
        "kb_packaging_tooling_rule", TOOLING_RULES, keys=("tooling_code",),
        source=SOURCE_TOOLING, overwrite=overwrite)

    # 物料：行业维度在父表 kb_material 上（价格表没有行业列）。
    for entry in MATERIALS:
        code = entry["material"]["material_code"]
        exists = db.query_one(
            "SELECT material_code FROM kb_material WHERE material_code = ?", (code,))
        if exists and not overwrite:
            continue
        material = {**entry["material"], "industry": INDUSTRY}
        material.setdefault("note", f"包装演示数据（来源：{SOURCE_MATERIAL}）")
        kb.save_material(material, properties=entry.get("properties"))
        if not db.query_one(
                "SELECT price_id FROM kb_material_price WHERE material_code = ?", (code,)):
            kb.add_material_price({**entry["price"], "material_code": code,
                                   "valid_from": SEED_DATE, "created_at": SEED_DATE})
    counts["kb_material"] = len(MATERIALS)
    counts["kb_material_price"] = len(MATERIALS)

    for rate in COST_RATES:
        if db.query_one("SELECT rate_code FROM kb_cost_rate WHERE rate_code = ?",
                        (rate["rate_code"],)) and not overwrite:
            continue
        kb.save_cost_rate(_subject_row(rate, SOURCE_COST))
    counts["kb_cost_rate"] = len(COST_RATES)

    for factor in COST_FACTORS:
        if db.query_one("SELECT factor_code FROM kb_cost_factor WHERE factor_code = ?",
                        (factor["factor_code"],)) and not overwrite:
            continue
        kb.save_cost_factor(_subject_row(factor, SOURCE_COST))
    counts["kb_cost_factor"] = len(COST_FACTORS)

    return counts


def seed_all(*, overwrite: bool = False) -> dict:
    """基础种子 + 包装演示数据。基础种子提供 global 费率与通用工序，两者都需要。"""
    from . import da_seed

    base = da_seed.seed_all(overwrite=overwrite)
    packaging = seed_packaging(overwrite=overwrite)
    merged = dict(base)
    for table, count in packaging.items():
        merged[table] = merged.get(table, 0) + count
    return merged


if __name__ == "__main__":                                    # pragma: no cover
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="写入包装行业演示数据（幂等）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的记录")
    parser.add_argument("--packaging-only", action="store_true", help="跳过通用机加工基础种子")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")              # Windows 控制台默认吃不下中文
    except Exception:
        pass

    db.init_db()
    counts = (seed_packaging if args.packaging_only else seed_all)(overwrite=args.force)
    print(f"库文件：{db.db_path()}")
    for table, count in sorted(counts.items()):
        print(f"  {table:32s} {count}")

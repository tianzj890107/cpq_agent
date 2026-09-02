"""电池行业演示数据（18650 电池包）。

为什么单独一份：da_seed 的基础种子是通用机加工/钣金口径（Q235 板、激光下料、
铣削路线），电池包这条线一条也命中不上 —— 电池图纸拆出来的是注塑上下壳、圆柱
电芯、PP 支架、FR-4 保护板，检索三条链路（零部件复用、工艺路线、成本测算）
在库里全部落空，演示时只能看到"未匹配 / 需补录"。

这份种子按当前电池图纸的实际拆解结果配套建库，使三条链路都能真的检索到：

  零部件  kb_component + param + feature
          按拆解出的 5 个零件建同规格库内件（外加 2 个近似件，用来演示"可改制"）
  工序    kb_equipment_class / kb_equipment / kb_process_step / kb_process_route
          注塑成型、电芯分选配组、镍片点焊、极耳激光焊、BMS 贴装、总装、老化、EOL
  成本    kb_material + property + price / kb_cost_rate / kb_cost_factor / kb_supplier
          物料价按牌号可反查，费率按设备类可取到，良率跟零件类别、废品率跟材料类别

匹配口径（改数前先看这里，否则容易改成检索不到）：
  · 物料按**牌号**反查：kb_material.grade 必须能与图纸 material_spec 互为子串
    （component_match._material_code_for / cost_lookup._material_row）
  · 零部件走"包络粗筛 → 参数精筛 → 特征相似"，包络容差 20%，两维超差即淘汰
    （kb_repo.ENVELOPE_TOLERANCE / _envelope_score）
  · 参数键必须与 component_match.build_query 一致：plate 取 length/width/thickness，
    box 取 length/width/height，cylinder 取 diameter/height，孔取 hole_diameter
  · 良率系数按零件类别取、废品率按材料类别取（cost_lookup._lookup_factors），
    所以 applicable_scope 要写成下面 kb_component.category / kb_material.category 的值

用法（幂等，已存在的记录默认不覆盖）：

    python -m backend.storage.da_seed_battery          # 基础种子 + 电池包
    python -m backend.storage.da_seed_battery --force  # 覆盖已存在记录
"""
from __future__ import annotations

from . import da_db as db
from . import kb_library as lib
from . import kb_repo as kb

SEED_DATE = "2026-01-01 00:00:00"


EQUIPMENT_CLASSES = [
    ("EQC-INJECT", "注塑机", "注塑"),
    ("EQC-SPOTWELD", "中频逆变点焊机", "焊接"),
    ("EQC-LASERWELD", "激光焊接机", "焊接"),
    ("EQC-SMT", "SMT 贴装线", "电子装联"),
    ("EQC-ASSY", "电池包装配工位", "装配"),
    ("EQC-BATTEST", "电池综合测试仪", "检测"),
    ("EQC-AGING", "老化柜", "检测"),
]

EQUIPMENT = [
    {"equipment_code": "EQ-INJ120T", "name": "伺服注塑机 120T", "model_no": "HTF120X",
     "equipment_class": "EQC-INJECT", "hourly_rate": 72.0, "depreciation_per_hour": 38.0,
     "power_kw": 22.0, "workshop": "注塑车间", "unit_count": 4,
     "capability": {"clamp_force_t": 120, "shot_weight_g": 260, "max_mold_h_mm": 450,
                    "material": ["PC+ABS", "PP", "ABS"]}},
    {"equipment_code": "EQ-SPOT-DC3000", "name": "中频逆变点焊机 3000A", "model_no": "MDC-3000",
     "equipment_class": "EQC-SPOTWELD", "hourly_rate": 66.0, "depreciation_per_hour": 22.0,
     "power_kw": 18.0, "workshop": "电池装配车间", "unit_count": 6,
     "capability": {"max_current_a": 3000, "nickel_thickness_mm": 0.3,
                    "electrode": "铬锆铜", "pulse_ms": [1, 99]}},
    {"equipment_code": "EQ-LW-1500", "name": "光纤激光焊接机 1500W", "model_no": "LW-1500",
     "equipment_class": "EQC-LASERWELD", "hourly_rate": 88.0, "depreciation_per_hour": 55.0,
     "power_kw": 6.0, "workshop": "电池装配车间", "unit_count": 2,
     "capability": {"power_w": 1500, "spot_um": 40, "material": ["镍带", "铝", "铜"]}},
    {"equipment_code": "EQ-SMT-LINE1", "name": "SMT 贴装线（含回流焊）", "model_no": "NXT-III",
     "equipment_class": "EQC-SMT", "hourly_rate": 95.0, "depreciation_per_hour": 76.0,
     "power_kw": 45.0, "workshop": "电子车间", "unit_count": 1,
     "capability": {"cph": 45000, "min_component": "0201", "board_max_mm": [510, 460],
                    "reflow_zones": 10}},
    {"equipment_code": "EQ-BT-8600", "name": "电池综合测试仪", "model_no": "BT-8600",
     "equipment_class": "EQC-BATTEST", "hourly_rate": 58.0, "depreciation_per_hour": 30.0,
     "power_kw": 3.5, "workshop": "电池测试间", "unit_count": 8,
     "capability": {"channels": 16, "voltage_v": [0, 60], "current_a": [0, 20],
                    "items": ["容量", "内阻", "绝缘", "耐压"]}},
    {"equipment_code": "EQ-AGING-48", "name": "恒温老化柜 48 位", "model_no": "AG-48",
     "equipment_class": "EQC-AGING", "hourly_rate": 25.0, "depreciation_per_hour": 12.0,
     "power_kw": 8.0, "workshop": "电池测试间", "unit_count": 3,
     "capability": {"slots": 48, "temp_c": [25, 60], "temp_accuracy_c": 2}},
]

# 工序原子。process_type 受 da_schema 的 CHECK 约束（见 kb_process_step），
# 注塑/老化这类没有对应枚举的归 'other'，真正的分类信息放在 category 里。
PROCESS_STEPS = [
    {"step_code": "PS-INJ-MOLD", "name": "注塑成型", "process_type": "other", "category": "注塑",
     "description_tpl": "按模具注塑成型壳体，保压冷却后取件",
     "default_equipment_class": "EQC-INJECT", "default_tooling": "专用注塑模具（一模一腔）",
     "applicable_material": ["工程塑料"], "applicable_feature": ["box", "plate"],
     "setup_min": 45, "unit_min_formula": "0.02*volume_cm3 + 0.35", "yield_rate": 0.97,
     "is_critical": 1, "quality_items": ["无缩水/飞边", "壁厚偏差 ±0.1mm", "阻燃等级 UL94 V-0"]},
    {"step_code": "PS-INJ-TRIM", "name": "水口修剪与去毛刺", "process_type": "bench", "category": "注塑",
     "description_tpl": "剪除浇口残料并修整分型面毛刺",
     "applicable_material": ["工程塑料"], "applicable_feature": ["box", "plate", "chamfer", "fillet"],
     "setup_min": 5, "unit_min_formula": "0.25", "yield_rate": 0.995,
     "quality_items": ["浇口残留 ≤0.3mm", "无锐边"]},
    {"step_code": "PS-CELL-SORT", "name": "电芯分选配组", "process_type": "inspection", "category": "电池装配",
     "description_tpl": "按容量/内阻/电压三参数分档配组，同组极差达标方可成组",
     "default_equipment_class": "EQC-BATTEST",
     "applicable_material": ["电芯"], "applicable_feature": ["cylinder"],
     "setup_min": 10, "unit_min_formula": "0.4*cell_count", "yield_rate": 0.94,
     "is_critical": 1, "quality_items": ["容量极差 ≤50mAh", "内阻极差 ≤5mΩ", "压差 ≤10mV"]},
    {"step_code": "PS-CELL-HOLDER", "name": "电芯入支架", "process_type": "assembly", "category": "电池装配",
     "description_tpl": "按极性方向将电芯压入支架定位孔并检查到位",
     "default_equipment_class": "EQC-ASSY",
     "applicable_material": ["工程塑料", "电芯"], "applicable_feature": ["plate", "hole_pattern"],
     "setup_min": 8, "unit_min_formula": "0.2*cell_count + 0.5", "yield_rate": 0.99,
     "quality_items": ["极性方向正确", "无倾斜/未到位"]},
    {"step_code": "PS-WELD-NICKEL", "name": "镍片点焊", "process_type": "welding", "category": "电池装配",
     "description_tpl": "按串并联拓扑点焊镍带，逐点做拉力抽检",
     "default_equipment_class": "EQC-SPOTWELD", "default_fixture": "电芯组点焊定位工装",
     "applicable_material": ["金属", "电芯"], "applicable_feature": ["cylinder", "plate"],
     "setup_min": 20, "unit_min_formula": "0.35*weld_point_count", "yield_rate": 0.96,
     "is_critical": 1, "quality_items": ["焊点拉力 ≥25N", "无虚焊/焊穿", "焊后压差 ≤10mV"]},
    {"step_code": "PS-WELD-LASER", "name": "极耳激光焊", "process_type": "welding", "category": "电池装配",
     "description_tpl": "总正负极耳与镍带激光焊接，成型后做外观与拉力检验",
     "default_equipment_class": "EQC-LASERWELD",
     "applicable_material": ["金属"], "applicable_feature": ["plate"],
     "setup_min": 25, "unit_min_formula": "0.5*tab_count + 1.0", "yield_rate": 0.98,
     "is_critical": 1, "quality_items": ["焊缝连续无气孔", "拉力 ≥60N"]},
    {"step_code": "PS-SMT-BMS", "name": "BMS 贴装与回流焊", "process_type": "assembly", "category": "电子装联",
     "description_tpl": "锡膏印刷 → 贴片 → 回流焊 → AOI 检查",
     "default_equipment_class": "EQC-SMT", "default_tooling": "钢网 + 专用载具",
     "applicable_material": ["覆铜板"], "applicable_feature": ["plate"],
     "setup_min": 60, "unit_min_formula": "0.02*component_count + 0.8", "yield_rate": 0.985,
     "is_critical": 1, "quality_items": ["AOI 无缺件/连锡", "焊点润湿良好"]},
    {"step_code": "PS-BMS-TEST", "name": "BMS 功能测试", "process_type": "inspection", "category": "电子装联",
     "description_tpl": "过充/过放/过流/短路保护逐项打点验证",
     "default_equipment_class": "EQC-BATTEST",
     "applicable_material": ["覆铜板"], "applicable_feature": ["plate"],
     "setup_min": 15, "unit_min_formula": "1.2", "yield_rate": 0.97,
     "is_critical": 1, "quality_items": ["过充保护 4.25±0.05V", "过放保护 2.50±0.08V", "均衡功能正常"]},
    {"step_code": "PS-ASSY-PACK", "name": "电池包总装", "process_type": "assembly", "category": "电池装配",
     "description_tpl": "电芯组 + BMS 装入下壳，走线固定后合上壳并锁付自攻钉",
     "default_equipment_class": "EQC-ASSY", "default_tooling": "电动螺丝刀（扭矩可设）",
     "applicable_material": ["工程塑料"], "applicable_feature": ["box", "hole_pattern"],
     "setup_min": 12, "unit_min_formula": "0.6*screw_count + 2.0", "yield_rate": 0.99,
     "quality_items": ["锁付扭矩 0.6±0.1 N·m", "合壳缝隙 ≤0.3mm", "线束无夹伤"]},
    {"step_code": "PS-PACK-AGING", "name": "老化静置", "process_type": "other", "category": "电池测试",
     "description_tpl": "45℃ 恒温老化后静置，复测开路电压与内阻",
     "default_equipment_class": "EQC-AGING",
     "applicable_material": ["电芯"], "applicable_feature": ["box", "cylinder"],
     "setup_min": 5, "unit_min_formula": "1.0", "yield_rate": 0.995,
     "quality_items": ["老化 12h @45℃", "静置后压降 ≤20mV"]},
    {"step_code": "PS-PACK-EOL", "name": "EOL 综合测试", "process_type": "inspection", "category": "电池测试",
     "description_tpl": "容量、内阻、绝缘、耐压、保护功能全项测试并出具报告",
     "default_equipment_class": "EQC-BATTEST",
     "applicable_material": ["电芯", "工程塑料"], "applicable_feature": ["box"],
     "setup_min": 20, "unit_min_formula": "6.0", "yield_rate": 0.98,
     "is_critical": 1, "quality_items": ["容量 ≥标称 95%", "绝缘电阻 ≥100MΩ",
                                         "耐压 1000VDC/60s 无击穿", "出具 EOL 报告"]},
    {"step_code": "PS-PACK-LABEL", "name": "贴标与包装", "process_type": "other", "category": "包装",
     "description_tpl": "贴产品标签与警示标识，按 UN38.3 要求包装入箱",
     "applicable_material": ["包材"], "applicable_feature": ["box"],
     "setup_min": 5, "unit_min_formula": "0.8", "yield_rate": 1.0,
     "quality_items": ["标签内容与批次一致", "包装符合 UN38.3 运输要求"]},
]

ROUTES = [
    {
        "route": {"route_code": "RT-INJ-SHELL", "name": "注塑壳体典型路线",
                  "applicable_category": "注塑件", "applicable_material": ["工程塑料"],
                  "batch_min": 100, "batch_max": 200000,
                  "summary": "注塑成型 → 水口修剪去毛刺 → 尺寸终检"},
        "steps": [
            {"seq": 10, "step_code": "PS-INJ-MOLD"},
            {"seq": 20, "step_code": "PS-INJ-TRIM", "depends_on": [10]},
            {"seq": 30, "step_code": "PS-INSP-FINAL", "depends_on": [20]},
        ],
    },
    {
        "route": {"route_code": "RT-BMS-PCBA", "name": "BMS 板卡（PCBA）路线",
                  "applicable_category": "电子件", "applicable_material": ["覆铜板"],
                  "batch_min": 50, "batch_max": 100000,
                  "summary": "SMT 贴装回流焊 → BMS 功能测试 → 终检"},
        "steps": [
            {"seq": 10, "step_code": "PS-SMT-BMS"},
            {"seq": 20, "step_code": "PS-BMS-TEST", "depends_on": [10]},
            {"seq": 30, "step_code": "PS-INSP-FINAL", "depends_on": [20]},
        ],
    },
    {
        "route": {"route_code": "RT-BATTPACK-18650", "name": "18650 电池包总装路线",
                  "applicable_category": "电池包", "applicable_material": ["电芯", "工程塑料"],
                  "batch_min": 50, "batch_max": 50000,
                  "summary": "电芯分选配组 → 入支架 → 镍片点焊 → 极耳激光焊 → BMS 装配 → "
                             "总装合壳 → 老化静置 → EOL 综合测试 → 贴标包装"},
        "steps": [
            {"seq": 10, "step_code": "PS-CELL-SORT"},
            {"seq": 20, "step_code": "PS-CELL-HOLDER", "depends_on": [10]},
            {"seq": 30, "step_code": "PS-WELD-NICKEL", "depends_on": [20]},
            {"seq": 40, "step_code": "PS-WELD-LASER", "depends_on": [30]},
            {"seq": 50, "step_code": "PS-BMS-TEST", "depends_on": [40],
             "note": "来料 BMS 上线前复测；自制板已在 RT-BMS-PCBA 内测过"},
            {"seq": 60, "step_code": "PS-ASSY-PACK", "depends_on": [40, 50]},
            {"seq": 70, "step_code": "PS-PACK-AGING", "depends_on": [60]},
            {"seq": 80, "step_code": "PS-PACK-EOL", "depends_on": [70]},
            {"seq": 90, "step_code": "PS-PACK-LABEL", "depends_on": [80]},
        ],
    },
    {
        "route": {"route_code": "RT-CELL-INCOMING", "name": "外购电芯来料检验路线",
                  "applicable_category": "外购电芯", "applicable_material": ["电芯"],
                  "batch_min": 100, "batch_max": 500000,
                  "summary": "分选配组（容量/内阻/电压三参数分档）→ 老化静置复测"},
        "steps": [
            {"seq": 10, "step_code": "PS-CELL-SORT"},
            {"seq": 20, "step_code": "PS-PACK-AGING", "is_optional": 1, "depends_on": [10],
             "condition_expr": "来料压差超标或库存超 3 个月"},
        ],
    },
]

# grade 必须能与图纸 material_spec 互为子串，否则反查不到（见文件头"匹配口径"）。
MATERIALS = [
    {"material": {"material_code": "MAT-PLA-PCABS-FR", "name": "阻燃 PC+ABS 合金",
                  "grade": "PC+ABS FR", "category": "工程塑料", "form": "粒料",
                  "spec": "UL94 V-0，注塑级", "density": 1.15, "base_unit": "kg",
                  "standard_loss_rate": 0.05, "storage_req": "80℃ 干燥 4h 后使用"},
     "properties": [{"prop_key": "flame_rating", "prop_name": "阻燃等级", "value_text": "UL94 V-0"},
                    {"prop_key": "tensile", "prop_name": "抗拉强度", "value_num": 58, "unit": "MPa"},
                    {"prop_key": "hdt", "prop_name": "热变形温度", "value_num": 105, "unit": "℃"}],
     "price": {"price": 28.5, "unit": "kg", "price_type": "contract",
               "source_name": "2026 年度工程塑料框架协议"}},
    {"material": {"material_code": "MAT-PLA-PP", "name": "均聚聚丙烯", "grade": "PP",
                  "category": "工程塑料", "form": "粒料", "spec": "注塑级，MFR 12g/10min",
                  "density": 0.91, "base_unit": "kg", "standard_loss_rate": 0.04},
     "properties": [{"prop_key": "tensile", "prop_name": "抗拉强度", "value_num": 33, "unit": "MPa"},
                    {"prop_key": "dielectric", "prop_name": "介电强度", "value_num": 26, "unit": "kV/mm"}],
     "price": {"price": 11.8, "unit": "kg", "price_type": "internal_purchase",
               "source_name": "2026 年度采购框架协议"}},
    {"material": {"material_code": "MAT-PCB-FR4", "name": "环氧玻纤覆铜板", "grade": "FR-4",
                  "category": "覆铜板", "form": "板材", "spec": "t1.6，双面 1oz 铜箔",
                  "density": 1.85, "base_unit": "kg", "standard_loss_rate": 0.15},
     "properties": [{"prop_key": "flame_rating", "prop_name": "阻燃等级", "value_text": "UL94 V-0"},
                    {"prop_key": "tg", "prop_name": "玻璃化温度", "value_num": 140, "unit": "℃"},
                    {"prop_key": "dielectric", "prop_name": "介电常数", "value_num": 4.4, "unit": "@1MHz"}],
     "price": {"price": 42.0, "unit": "kg", "price_type": "contract",
               "source_name": "PCB 基材年度合同价"}},
    {"material": {"material_code": "MAT-CELL-18650", "name": "18650 圆柱锂离子电芯",
                  "grade": "18650-2600mAh", "category": "电芯", "form": "成品件",
                  "spec": "φ18×65，3.7V 2600mAh，1C 放电", "density": 2.55, "base_unit": "只",
                  "standard_loss_rate": 0.02, "hazard_level": "UN3480 第 9 类",
                  "storage_req": "20±5℃，荷电 30%~50% 存放"},
     "properties": [{"prop_key": "capacity", "prop_name": "标称容量", "value_num": 2600, "unit": "mAh"},
                    {"prop_key": "voltage", "prop_name": "标称电压", "value_num": 3.7, "unit": "V"},
                    {"prop_key": "internal_resistance", "prop_name": "交流内阻",
                     "value_num": 35, "unit": "mΩ"},
                    {"prop_key": "cycle_life", "prop_name": "循环寿命",
                     "value_text": "≥500 次 @80% DOD"}],
     "price": {"price": 8.6, "unit": "只", "price_type": "contract",
               "source_name": "电芯年度合同价（阶梯 ≥10 万只）"}},
    {"material": {"material_code": "MAT-NI-STRIP", "name": "电池连接镍带", "grade": "Ni99.6",
                  "category": "金属", "form": "带材", "spec": "t0.15×W8，纯镍",
                  "density": 8.9, "base_unit": "kg", "standard_loss_rate": 0.12},
     "properties": [{"prop_key": "purity", "prop_name": "镍纯度", "value_num": 99.6, "unit": "%"},
                    {"prop_key": "resistivity", "prop_name": "电阻率",
                     "value_num": 7.2, "unit": "µΩ·cm"}],
     "price": {"price": 128.0, "unit": "kg", "price_type": "market",
               "source_name": "上海有色网镍价 + 加工费", "confidence": 0.8}},
    {"material": {"material_code": "MAT-INS-FISHPAPER", "name": "电池绝缘青稞纸", "grade": "青稞纸",
                  "category": "绝缘材料", "form": "卷料",
                  "spec": "t0.2，耐压 ≥5kV", "density": 1.1, "base_unit": "kg",
                  "standard_loss_rate": 0.10},
     "properties": [{"prop_key": "dielectric", "prop_name": "击穿电压", "value_num": 5, "unit": "kV"}],
     "price": {"price": 26.0, "unit": "kg", "price_type": "internal_purchase",
               "source_name": "2026 年度采购框架协议"}},
]

# 费率跟设备类走；global 口径的能耗/制造费用/物流已在 da_seed 基础种子里。
COST_RATES = [
    {"rate_code": "RATE-LABOR-INJECT", "name": "注塑人工费率", "rate_type": "labor",
     "scope_type": "equipment_class", "scope_ref": "EQC-INJECT", "value": 72.0,
     "unit": "元/小时", "source": "财务 2026 年度核定"},
    {"rate_code": "RATE-DEP-INJECT", "name": "注塑机折旧费率", "rate_type": "equipment_dep",
     "scope_type": "equipment_class", "scope_ref": "EQC-INJECT", "value": 38.0,
     "unit": "元/小时", "source": "固定资产折旧表"},
    {"rate_code": "RATE-LABOR-SPOTWELD", "name": "点焊人工费率", "rate_type": "labor",
     "scope_type": "equipment_class", "scope_ref": "EQC-SPOTWELD", "value": 66.0,
     "unit": "元/小时", "source": "财务 2026 年度核定"},
    {"rate_code": "RATE-DEP-SPOTWELD", "name": "点焊机折旧费率", "rate_type": "equipment_dep",
     "scope_type": "equipment_class", "scope_ref": "EQC-SPOTWELD", "value": 22.0,
     "unit": "元/小时", "source": "固定资产折旧表"},
    {"rate_code": "RATE-LABOR-LASERWELD", "name": "激光焊人工费率", "rate_type": "labor",
     "scope_type": "equipment_class", "scope_ref": "EQC-LASERWELD", "value": 88.0,
     "unit": "元/小时", "source": "财务 2026 年度核定"},
    {"rate_code": "RATE-DEP-LASERWELD", "name": "激光焊机折旧费率", "rate_type": "equipment_dep",
     "scope_type": "equipment_class", "scope_ref": "EQC-LASERWELD", "value": 55.0,
     "unit": "元/小时", "source": "固定资产折旧表"},
    {"rate_code": "RATE-LABOR-SMT", "name": "SMT 人工费率", "rate_type": "labor",
     "scope_type": "equipment_class", "scope_ref": "EQC-SMT", "value": 95.0,
     "unit": "元/小时", "source": "财务 2026 年度核定"},
    {"rate_code": "RATE-DEP-SMT", "name": "SMT 线折旧费率", "rate_type": "equipment_dep",
     "scope_type": "equipment_class", "scope_ref": "EQC-SMT", "value": 76.0,
     "unit": "元/小时", "source": "固定资产折旧表"},
    {"rate_code": "RATE-LABOR-ASSY", "name": "电池装配人工费率", "rate_type": "labor",
     "scope_type": "equipment_class", "scope_ref": "EQC-ASSY", "value": 48.0,
     "unit": "元/小时", "source": "财务 2026 年度核定"},
    {"rate_code": "RATE-DEP-ASSY", "name": "装配工位折旧费率", "rate_type": "equipment_dep",
     "scope_type": "equipment_class", "scope_ref": "EQC-ASSY", "value": 6.0,
     "unit": "元/小时", "source": "固定资产折旧表"},
    {"rate_code": "RATE-LABOR-BATTEST", "name": "电池测试人工费率", "rate_type": "labor",
     "scope_type": "equipment_class", "scope_ref": "EQC-BATTEST", "value": 58.0,
     "unit": "元/小时", "source": "财务 2026 年度核定"},
    {"rate_code": "RATE-DEP-BATTEST", "name": "测试仪折旧费率", "rate_type": "equipment_dep",
     "scope_type": "equipment_class", "scope_ref": "EQC-BATTEST", "value": 30.0,
     "unit": "元/小时", "source": "固定资产折旧表"},
    {"rate_code": "RATE-LABOR-AGING", "name": "老化看管人工费率", "rate_type": "labor",
     "scope_type": "equipment_class", "scope_ref": "EQC-AGING", "value": 25.0,
     "unit": "元/小时", "source": "财务 2026 年度核定"},
    {"rate_code": "RATE-DEP-AGING", "name": "老化柜折旧费率", "rate_type": "equipment_dep",
     "scope_type": "equipment_class", "scope_ref": "EQC-AGING", "value": 12.0,
     "unit": "元/小时", "source": "固定资产折旧表"},
    # 三条路线的终检都落在 EQC-CMM 上（基础种子只给了加工中心的费率），
    # 不补这两条，成本测算每个零件都会记一笔"equipment_dep（EQC-CMM）缺失"。
    {"rate_code": "RATE-LABOR-CMM", "name": "计量检测人工费率", "rate_type": "labor",
     "scope_type": "equipment_class", "scope_ref": "EQC-CMM", "value": 76.0,
     "unit": "元/小时", "source": "财务 2026 年度核定"},
    {"rate_code": "RATE-DEP-CMM", "name": "三坐标折旧费率", "rate_type": "equipment_dep",
     "scope_type": "equipment_class", "scope_ref": "EQC-CMM", "value": 45.0,
     "unit": "元/小时", "source": "固定资产折旧表"},
    {"rate_code": "RATE-PACK-BATT", "name": "电池包装材费率", "rate_type": "packaging",
     "scope_type": "global", "value": 6.5, "unit": "元/台", "source": "包装材料核算"},
]

# applicable_scope 的取值必须落在 kb_component.category（良率）/ kb_material.category（废品率）上，
# 否则 cost_lookup 取不到会记成缺口。
COST_FACTORS = [
    {"factor_code": "FCT-YIELD-INJECT", "name": "注塑件成品率", "factor_type": "yield",
     "applicable_scope": "注塑件", "value": 0.97, "source": "近 12 个月工序良率统计"},
    {"factor_code": "FCT-YIELD-BATTPACK", "name": "电池包成品率", "factor_type": "yield",
     "applicable_scope": "电池包", "value": 0.94, "source": "近 12 个月 EOL 一次通过率"},
    {"factor_code": "FCT-YIELD-PCBA", "name": "PCBA 成品率", "factor_type": "yield",
     "applicable_scope": "电子件", "value": 0.96, "source": "AOI + 功能测试通过率"},
    {"factor_code": "FCT-YIELD-CELL", "name": "电芯配组成品率", "factor_type": "yield",
     "applicable_scope": "外购电芯", "value": 0.93, "source": "分选配组入组率"},
    {"factor_code": "FCT-SCRAP-PLASTIC", "name": "工程塑料损耗率", "factor_type": "scrap",
     "applicable_scope": "工程塑料", "value": 0.05, "note": "含水口料与开机废品"},
    {"factor_code": "FCT-SCRAP-CELL", "name": "电芯损耗率", "factor_type": "scrap",
     "applicable_scope": "电芯", "value": 0.02, "note": "含分选淘汰与焊接损伤"},
    {"factor_code": "FCT-SCRAP-PCB", "name": "覆铜板损耗率", "factor_type": "scrap",
     "applicable_scope": "覆铜板", "value": 0.15, "note": "含拼板边料"},
    {"factor_code": "FCT-SCRAP-INS", "name": "绝缘材料损耗率", "factor_type": "scrap",
     "applicable_scope": "绝缘材料", "value": 0.10},
    {"factor_code": "FCT-MARGIN-BATT", "name": "电池包毛利率", "factor_type": "margin",
     "applicable_scope": "电池包", "value": 0.18, "source": "2026 年度经营目标"},
    {"factor_code": "FCT-RISK-BATT", "name": "电池安全与召回风险准备", "factor_type": "risk",
     "applicable_scope": "电池包", "value": 0.03, "note": "按售价计提"},
]

SUPPLIERS = [
    {"supplier": {"name": "远景动力电芯供应中心", "supplier_type": "外购件", "region": "江苏无锡",
                  "qualification": "IATF16949 / UN38.3", "rating": 4.7, "contact": "0510-0000-0101"},
     "capabilities": [
         {"material_code": "MAT-CELL-18650", "moq": "10000 只", "lead_time": "20 天",
          "price_ref": 8.6, "qualified": 1, "note": "容量/内阻已按档位分选交付"},
     ]},
    {"supplier": {"name": "东莞精模注塑有限公司", "supplier_type": "外协", "region": "广东东莞",
                  "qualification": "ISO9001 / UL 黄卡", "rating": 4.3, "contact": "0769-0000-0102"},
     "capabilities": [
         {"material_code": "MAT-PLA-PCABS-FR", "moq": "5000 件", "lead_time": "12 天",
          "price_ref": 28.5, "qualified": 1, "note": "含模具开发，120T~250T 机台"},
         {"material_code": "MAT-PLA-PP", "moq": "5000 件", "lead_time": "10 天",
          "price_ref": 11.8, "qualified": 1},
     ]},
    {"supplier": {"name": "深圳华信电子 PCBA", "supplier_type": "外协", "region": "广东深圳",
                  "qualification": "ISO9001 / IPC-A-610 Class 2", "rating": 4.4,
                  "contact": "0755-0000-0103"},
     "capabilities": [
         {"material_code": "MAT-PCB-FR4", "moq": "500 片", "lead_time": "15 天",
          "price_ref": 42.0, "qualified": 1, "note": "含 SMT 贴装与功能测试"},
     ]},
    {"supplier": {"name": "江阴精密镍带材料厂", "supplier_type": "原材料", "region": "江苏江阴",
                  "qualification": "ISO9001", "rating": 4.1, "contact": "0510-0000-0104"},
     "capabilities": [
         {"material_code": "MAT-NI-STRIP", "moq": "50 kg", "lead_time": "7 天",
          "price_ref": 128.0, "qualified": 1},
         {"material_code": "MAT-INS-FISHPAPER", "moq": "20 kg", "lead_time": "5 天",
          "price_ref": 26.0, "qualified": 1},
     ]},
]

# 壳体锁付用的自攻钉，与上下壳 φ3.5 孔对应。
STANDARD_PARTS = [
    {"standard_no": "GB/T 845", "designation": "ST3.0x10", "category": "screw",
     "material": "碳钢", "surface_treatment": "镀镍", "unit_price_ref": 0.09,
     "size_params": {"thread": "ST3.0", "length": 10, "head": "十字盘头自攻"}},
    {"standard_no": "GB/T 845", "designation": "ST3.0x8", "category": "screw",
     "material": "碳钢", "surface_treatment": "镀镍", "unit_price_ref": 0.08,
     "size_params": {"thread": "ST3.0", "length": 8, "head": "十字盘头自攻"}},
]

INSPECTION_ITEMS = [
    {"insp_code": "INS-BATT-CAP", "name": "容量测试", "method": "0.5C 充放电循环",
     "instrument": "电池综合测试仪", "sampling_rule": "全检",
     "acceptance_criteria": "实测容量 ≥ 标称值 95%", "cost_per_item": 3.2},
    {"insp_code": "INS-BATT-IR", "name": "内阻测试", "method": "1kHz 交流内阻",
     "instrument": "内阻仪", "sampling_rule": "全检",
     "acceptance_criteria": "包内阻 ≤ 设计值 +10%", "cost_per_item": 0.8},
    {"insp_code": "INS-BATT-HIPOT", "name": "绝缘耐压", "method": "1000VDC/60s",
     "instrument": "耐压测试仪", "sampling_rule": "全检",
     "acceptance_criteria": "无击穿，绝缘电阻 ≥100MΩ", "cost_per_item": 1.5},
    {"insp_code": "INS-WELD-PULL", "name": "焊点拉力抽检", "method": "拉力计垂直拉脱",
     "instrument": "数显推拉力计", "sampling_rule": "每批抽 5 点",
     "acceptance_criteria": "点焊 ≥25N，激光焊 ≥60N", "cost_per_item": 2.0},
    {"insp_code": "INS-INJ-DIM", "name": "注塑件尺寸检验", "method": "卡尺 + 投影仪",
     "instrument": "二次元投影仪", "sampling_rule": "首件全尺寸 + 每 2h 抽检",
     "acceptance_criteria": "关键尺寸符合图纸，壁厚偏差 ±0.1mm", "cost_per_item": 1.2},
]

# 与当前电池图纸拆解出的 5 个零件同规格；末两个是近似件，用来演示"可改制"档。
COMPONENTS = [
    {
        "component": {
            "component_code": "CMP-BATT-COVER-0001", "name": "电池包上壳", "category": "注塑件",
            "subcategory": "壳体", "source_type": "self_made",
            "spec_summary": "PC+ABS FR 注塑上盖 108×56×4，4-φ3.5 锁付孔，R2 外观倒角",
            "default_material_code": "MAT-PLA-PCABS-FR", "default_route_code": "RT-INJ-SHELL",
            "envelope_l": 108, "envelope_w": 56, "envelope_h": 4, "mass_kg": 0.028,
            "manufacturability": "mature", "reuse_count": 6, "rev": "B",
            "tags": ["电池包", "上壳", "注塑"],
        },
        "params": [
            {"param_key": "length", "param_name": "长", "value_num": 108, "unit": "mm",
             "tol_lower": 3, "tol_upper": 3, "is_key": 1},
            {"param_key": "width", "param_name": "宽", "value_num": 56, "unit": "mm",
             "tol_lower": 3, "tol_upper": 3, "is_key": 1},
            {"param_key": "height", "param_name": "高", "value_num": 4, "unit": "mm",
             "tol_lower": 1, "tol_upper": 1, "is_key": 1},
            {"param_key": "hole_diameter", "param_name": "锁付孔径", "value_num": 3.5,
             "unit": "mm", "tol_lower": 0.3, "tol_upper": 0.3, "is_key": 1},
            {"param_key": "wall_thickness", "param_name": "壁厚", "value_num": 2.0, "unit": "mm",
             "tol_lower": 0.2, "tol_upper": 0.2},
            {"param_key": "flame_rating", "param_name": "阻燃等级", "value_text": "UL94 V-0"},
        ],
        "features": [
            {"feature_type": "box", "length": 108, "width": 56, "height": 4, "purpose": "主体盖板"},
            {"feature_type": "hole_pattern", "diameter": 3.5, "count_x": 2, "count_y": 2,
             "spacing_x": 98, "spacing_y": 46, "purpose": "螺丝固定孔"},
            {"feature_type": "fillet", "radius": 2.0, "purpose": "外观倒角"},
        ],
    },
    {
        "component": {
            "component_code": "CMP-BATT-HOUSING-0001", "name": "电池包下壳", "category": "注塑件",
            "subcategory": "壳体", "source_type": "self_made",
            "spec_summary": "PC+ABS FR 注塑下壳 108×56×22.5，容纳电芯组与 BMS，4 螺柱",
            "default_material_code": "MAT-PLA-PCABS-FR", "default_route_code": "RT-INJ-SHELL",
            "envelope_l": 108, "envelope_w": 56, "envelope_h": 22.5, "mass_kg": 0.062,
            "manufacturability": "mature", "reuse_count": 6, "rev": "B",
            "tags": ["电池包", "下壳", "注塑"],
        },
        "params": [
            {"param_key": "length", "param_name": "长", "value_num": 108, "unit": "mm",
             "tol_lower": 3, "tol_upper": 3, "is_key": 1},
            {"param_key": "width", "param_name": "宽", "value_num": 56, "unit": "mm",
             "tol_lower": 3, "tol_upper": 3, "is_key": 1},
            {"param_key": "height", "param_name": "高", "value_num": 22.5, "unit": "mm",
             "tol_lower": 2, "tol_upper": 2, "is_key": 1},
            {"param_key": "hole_diameter", "param_name": "螺柱孔径", "value_num": 3.5,
             "unit": "mm", "tol_lower": 0.3, "tol_upper": 0.3, "is_key": 1},
            {"param_key": "wall_thickness", "param_name": "壁厚", "value_num": 2.2, "unit": "mm",
             "tol_lower": 0.2, "tol_upper": 0.2},
            {"param_key": "flame_rating", "param_name": "阻燃等级", "value_text": "UL94 V-0"},
        ],
        "features": [
            {"feature_type": "box", "length": 108, "width": 56, "height": 22.5,
             "purpose": "容纳电芯与 BMS"},
            {"feature_type": "hole_pattern", "diameter": 3.5, "count_x": 2, "count_y": 2,
             "spacing_x": 98, "spacing_y": 46, "purpose": "螺丝柱/孔"},
            {"feature_type": "chamfer", "distance": 1.0, "purpose": "去毛刺"},
        ],
    },
    {
        "component": {
            "component_code": "CMP-CELL-18650-0001", "name": "圆柱锂电池 18650", "category": "外购电芯",
            "subcategory": "圆柱电芯", "source_type": "standard",
            "spec_summary": "φ18×65，3.7V 2600mAh，1C 放电，UN38.3 已认证",
            "default_material_code": "MAT-CELL-18650", "default_route_code": "RT-CELL-INCOMING",
            "envelope_l": 65, "envelope_w": 18, "envelope_h": 18, "mass_kg": 0.045,
            "manufacturability": "mature", "reuse_count": 24, "rev": "A",
            "tags": ["电芯", "18650", "外购"],
        },
        "params": [
            {"param_key": "diameter", "param_name": "直径", "value_num": 18, "unit": "mm",
             "tol_lower": 0.2, "tol_upper": 0.2, "is_key": 1},
            {"param_key": "height", "param_name": "高度", "value_num": 65, "unit": "mm",
             "tol_lower": 0.5, "tol_upper": 0.5, "is_key": 1},
            {"param_key": "capacity", "param_name": "标称容量", "value_num": 2600, "unit": "mAh",
             "tol_lower": 100, "tol_upper": 400, "is_key": 1},
            {"param_key": "voltage", "param_name": "标称电压", "value_num": 3.7, "unit": "V",
             "tol_lower": 0.1, "tol_upper": 0.1},
        ],
        "features": [
            {"feature_type": "cylinder", "diameter": 18, "height": 65, "purpose": "储能单元"},
        ],
    },
    {
        "component": {
            "component_code": "CMP-BATT-HOLDER-0001", "name": "18650 电芯支架（4 位）",
            "category": "注塑件", "subcategory": "支架", "source_type": "self_made",
            "spec_summary": "PP 注塑支架 76×40×3，4-φ18.2 电芯位，间距 18.5",
            "default_material_code": "MAT-PLA-PP", "default_route_code": "RT-INJ-SHELL",
            "envelope_l": 76, "envelope_w": 40, "envelope_h": 3, "mass_kg": 0.009,
            "manufacturability": "mature", "reuse_count": 11, "rev": "A",
            "tags": ["电池包", "支架", "18650"],
        },
        "params": [
            {"param_key": "length", "param_name": "长", "value_num": 76, "unit": "mm",
             "tol_lower": 2, "tol_upper": 2, "is_key": 1},
            {"param_key": "width", "param_name": "宽", "value_num": 40, "unit": "mm",
             "tol_lower": 2, "tol_upper": 2, "is_key": 1},
            {"param_key": "thickness", "param_name": "厚", "value_num": 3, "unit": "mm",
             "tol_lower": 0.5, "tol_upper": 0.5, "is_key": 1},
            {"param_key": "hole_diameter", "param_name": "电芯位孔径", "value_num": 18.2,
             "unit": "mm", "tol_lower": 0.3, "tol_upper": 0.3, "is_key": 1},
            {"param_key": "cell_pitch", "param_name": "电芯间距", "value_num": 18.5, "unit": "mm",
             "tol_lower": 0.3, "tol_upper": 0.3},
        ],
        "features": [
            {"feature_type": "plate", "length": 76, "width": 40, "thickness": 3,
             "purpose": "固定 4 颗电芯"},
            {"feature_type": "hole_pattern", "diameter": 18.2, "count_x": 4, "count_y": 1,
             "spacing_x": 18.5, "spacing_y": 0, "purpose": "电芯安装位"},
        ],
    },
    {
        "component": {
            "component_code": "CMP-BMS-PCBA-0001", "name": "BMS 保护板（2S~4S）",
            "category": "电子件", "subcategory": "PCBA", "source_type": "outsourced",
            "spec_summary": "FR-4 双面板 90×20×1.6，过充/过放/过流/短路保护 + 被动均衡",
            "default_material_code": "MAT-PCB-FR4", "default_route_code": "RT-BMS-PCBA",
            "envelope_l": 90, "envelope_w": 20, "envelope_h": 1.6, "mass_kg": 0.012,
            "manufacturability": "mature", "reuse_count": 9, "rev": "C",
            "tags": ["BMS", "PCBA", "保护板"],
        },
        "params": [
            {"param_key": "length", "param_name": "长", "value_num": 90, "unit": "mm",
             "tol_lower": 3, "tol_upper": 3, "is_key": 1},
            {"param_key": "width", "param_name": "宽", "value_num": 20, "unit": "mm",
             "tol_lower": 2, "tol_upper": 2, "is_key": 1},
            {"param_key": "thickness", "param_name": "板厚", "value_num": 1.6, "unit": "mm",
             "tol_lower": 0.2, "tol_upper": 0.2, "is_key": 1},
            {"param_key": "over_charge_v", "param_name": "过充保护电压", "value_num": 4.25,
             "unit": "V", "tol_lower": 0.05, "tol_upper": 0.05},
            {"param_key": "continuous_current", "param_name": "持续放电电流", "value_num": 10,
             "unit": "A", "tol_lower": 2, "tol_upper": 5},
        ],
        "features": [
            {"feature_type": "plate", "length": 90, "width": 20, "thickness": 1.6,
             "purpose": "电路控制与保护"},
        ],
    },
    # ↓ 近似件：尺寸落在包络容差内但关键参数有差异，检索会给出"可改制 + 差异说明"。
    {
        "component": {
            "component_code": "CMP-BATT-COVER-0002", "name": "电池包上壳（加长型）",
            "category": "注塑件", "subcategory": "壳体", "source_type": "self_made",
            "spec_summary": "PC+ABS FR 注塑上盖 118×56×4，4-φ3.5 锁付孔",
            "default_material_code": "MAT-PLA-PCABS-FR", "default_route_code": "RT-INJ-SHELL",
            "envelope_l": 118, "envelope_w": 56, "envelope_h": 4, "mass_kg": 0.031,
            "manufacturability": "mature", "reuse_count": 3, "rev": "A",
            "tags": ["电池包", "上壳", "加长"],
        },
        "params": [
            {"param_key": "length", "param_name": "长", "value_num": 118, "unit": "mm",
             "tol_lower": 3, "tol_upper": 3, "is_key": 1},
            {"param_key": "width", "param_name": "宽", "value_num": 56, "unit": "mm",
             "tol_lower": 3, "tol_upper": 3, "is_key": 1},
            {"param_key": "height", "param_name": "高", "value_num": 4, "unit": "mm",
             "tol_lower": 1, "tol_upper": 1, "is_key": 1},
            {"param_key": "hole_diameter", "param_name": "锁付孔径", "value_num": 3.5,
             "unit": "mm", "tol_lower": 0.3, "tol_upper": 0.3, "is_key": 1},
        ],
        "features": [
            {"feature_type": "box", "length": 118, "width": 56, "height": 4, "purpose": "主体盖板"},
            {"feature_type": "hole_pattern", "diameter": 3.5, "count_x": 2, "count_y": 2,
             "spacing_x": 108, "spacing_y": 46, "purpose": "螺丝固定孔"},
            {"feature_type": "fillet", "radius": 2.0, "purpose": "外观倒角"},
        ],
    },
    {
        "component": {
            "component_code": "CMP-BATT-HOLDER-0002", "name": "18650 电芯支架（2 位）",
            "category": "注塑件", "subcategory": "支架", "source_type": "self_made",
            "spec_summary": "PP 注塑支架 40×40×3，2-φ18.2 电芯位",
            "default_material_code": "MAT-PLA-PP", "default_route_code": "RT-INJ-SHELL",
            "envelope_l": 40, "envelope_w": 40, "envelope_h": 3, "mass_kg": 0.005,
            "manufacturability": "mature", "reuse_count": 4, "rev": "A",
            "tags": ["电池包", "支架", "18650"],
        },
        "params": [
            {"param_key": "length", "param_name": "长", "value_num": 40, "unit": "mm",
             "tol_lower": 2, "tol_upper": 2, "is_key": 1},
            {"param_key": "width", "param_name": "宽", "value_num": 40, "unit": "mm",
             "tol_lower": 2, "tol_upper": 2, "is_key": 1},
            {"param_key": "thickness", "param_name": "厚", "value_num": 3, "unit": "mm",
             "tol_lower": 0.5, "tol_upper": 0.5, "is_key": 1},
            {"param_key": "hole_diameter", "param_name": "电芯位孔径", "value_num": 18.2,
             "unit": "mm", "tol_lower": 0.3, "tol_upper": 0.3, "is_key": 1},
        ],
        "features": [
            {"feature_type": "plate", "length": 40, "width": 40, "thickness": 3,
             "purpose": "固定 2 颗电芯"},
            {"feature_type": "hole_pattern", "diameter": 18.2, "count_x": 2, "count_y": 1,
             "spacing_x": 18.5, "spacing_y": 0, "purpose": "电芯安装位"},
        ],
    },
]


# --------------------------------------------------------------------------- #
def seed_battery(*, overwrite: bool = False) -> dict:
    """写入电池包演示数据（幂等）。overwrite=False 时已存在的记录不覆盖。"""
    counts: dict[str, int] = {}

    for code, name, category in EQUIPMENT_CLASSES:
        db.upsert("kb_equipment_class", {"class_code": code, "name": name, "category": category},
                  keys=("class_code",))
    counts["kb_equipment_class"] = len(EQUIPMENT_CLASSES)

    for item in EQUIPMENT:
        existing = db.query_one("SELECT equipment_id FROM kb_equipment WHERE equipment_code = ?",
                                (item["equipment_code"],))
        if existing and not overwrite:
            continue
        row = dict(item)
        if existing:
            row["equipment_id"] = existing["equipment_id"]
        kb.save_equipment(row)
    counts["kb_equipment"] = len(EQUIPMENT)

    for entry in MATERIALS:
        code = entry["material"]["material_code"]
        exists = db.query_one("SELECT material_code FROM kb_material WHERE material_code = ?", (code,))
        if exists and not overwrite:
            continue
        kb.save_material(entry["material"], properties=entry.get("properties"))
        lib.material_dir(code, create=True)
        if not db.query_one("SELECT price_id FROM kb_material_price WHERE material_code = ?", (code,)):
            kb.add_material_price({**entry["price"], "material_code": code, "valid_from": SEED_DATE})
    counts["kb_material"] = len(MATERIALS)

    for step in PROCESS_STEPS:
        if db.query_one("SELECT step_code FROM kb_process_step WHERE step_code = ?",
                        (step["step_code"],)) and not overwrite:
            continue
        kb.save_process_step({**step, "effective_from": SEED_DATE})
    counts["kb_process_step"] = len(PROCESS_STEPS)

    # 路线引用 PS-INSP-FINAL（基础种子里的终检工序）；基础种子没跑过就跳过该步，
    # 否则外键 RESTRICT 会整条路线写不进去。
    has_final = bool(db.query_one(
        "SELECT step_code FROM kb_process_step WHERE step_code = 'PS-INSP-FINAL'"))
    for entry in ROUTES:
        code = entry["route"]["route_code"]
        if db.query_one("SELECT route_code FROM kb_process_route WHERE route_code = ?",
                        (code,)) and not overwrite:
            continue
        steps = [s for s in entry["steps"] if has_final or s["step_code"] != "PS-INSP-FINAL"]
        kb.save_route(entry["route"], steps)
    counts["kb_process_route"] = len(ROUTES)

    for rate in COST_RATES:
        if db.query_one("SELECT rate_code FROM kb_cost_rate WHERE rate_code = ?",
                        (rate["rate_code"],)) and not overwrite:
            continue
        kb.save_cost_rate({**rate, "effective_from": SEED_DATE})
    counts["kb_cost_rate"] = len(COST_RATES)

    for factor in COST_FACTORS:
        if db.query_one("SELECT factor_code FROM kb_cost_factor WHERE factor_code = ?",
                        (factor["factor_code"],)) and not overwrite:
            continue
        kb.save_cost_factor({**factor, "effective_from": SEED_DATE})
    counts["kb_cost_factor"] = len(COST_FACTORS)

    for entry in SUPPLIERS:
        existing = db.query_one("SELECT supplier_id FROM kb_supplier WHERE name = ?",
                                (entry["supplier"]["name"],))
        if existing and not overwrite:
            continue
        row = dict(entry["supplier"])
        if existing:
            row["supplier_id"] = existing["supplier_id"]
        kb.save_supplier(row, capabilities=entry["capabilities"])
    counts["kb_supplier"] = len(SUPPLIERS)

    for part in STANDARD_PARTS:
        existing = db.query_one(
            "SELECT std_id FROM kb_standard_part WHERE standard_no = ? AND designation = ?",
            (part["standard_no"], part["designation"]),
        )
        if existing and not overwrite:
            continue
        row = dict(part)
        if existing:
            row["std_id"] = existing["std_id"]
        row["drawing_path"] = lib.rel_path(
            lib.standard_part_dir(part["standard_no"], part["designation"], create=True))
        kb.save_standard_part(row)
    counts["kb_standard_part"] = len(STANDARD_PARTS)

    for item in INSPECTION_ITEMS:
        if db.query_one("SELECT insp_code FROM kb_inspection_item WHERE insp_code = ?",
                        (item["insp_code"],)) and not overwrite:
            continue
        db.upsert("kb_inspection_item", item, keys=("insp_code",))
    counts["kb_inspection_item"] = len(INSPECTION_ITEMS)

    for entry in COMPONENTS:
        code = entry["component"]["component_code"]
        if db.query_one("SELECT component_id FROM kb_component WHERE component_code = ?",
                        (code,)) and not overwrite:
            continue
        kb.save_component(entry["component"], params=entry["params"], features=entry["features"])
    counts["kb_component"] = len(COMPONENTS)

    return counts


def seed_all(*, overwrite: bool = False) -> dict:
    """基础种子 + 电池包演示数据。基础种子提供 global 费率与终检工序，两者都需要。"""
    from . import da_seed

    base = da_seed.seed_all(overwrite=overwrite)
    battery = seed_battery(overwrite=overwrite)
    merged = dict(base)
    for table, count in battery.items():
        merged[table] = merged.get(table, 0) + count
    return merged


if __name__ == "__main__":                                    # pragma: no cover
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="写入电池行业演示数据（幂等）")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的记录")
    parser.add_argument("--battery-only", action="store_true", help="跳过通用机加工基础种子")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")              # Windows 控制台默认吃不下中文
    except Exception:
        pass

    db.init_db()
    counts = (seed_battery if args.battery_only else seed_all)(overwrite=args.force)
    print(f"库文件：{db.db_path()}")
    for table, count in sorted(counts.items()):
        print(f"  {table:24s} {count}")

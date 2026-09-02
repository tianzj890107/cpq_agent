# 亿纬锂能 DA 本体

> 由《亿纬锂能DA梳理.xlsx》生成，**请勿手工编辑** —— 改动请改 xlsx，
> 再运行 `python cpq_ontology.py` 重新生成（`--check` 可对账）。
> 三个助手各一节；每个逻辑实体一张属性表，表名即物理表名。

## 报价助手（quote）

共 11 个逻辑实体。

### clm_calc_base_info

- 业务对象：价格测算单
- 逻辑实体：测算基本信息

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| calc_order_id | 测算单id | 文本 | 是 |  |  |
| calc_order_no | 测算单号 | 文本 |  |  |  |
| calc_type | 测算类型 | 枚举 |  |  | 简易询盘、询盘、投标、合同 |
| org_code | 所属组织 | 组织 |  |  |  |
| is_price_adjust | 是否调价 | 布尔 |  |  |  |
| opportunity_no | 关联商机编号 | 文本/关联对象 |  |  |  |
| contract_no | 关联合同号 | 文本 |  |  |  |
| contract_version | 合同版本 | 文本 |  |  |  |
| customer | 客户 | 文本/关联对象 |  |  |  |
| customer_level | 客户等级 | 枚举 |  |  | S、A、B、C |
| project_name | 项目名称 | 文本 |  |  |  |
| project_scale | 项目规模 | 数值/整数 |  |  |  |
| ex_factory_time | 出厂时间 | 数值/整数 |  |  |  |
| currency | 币种 | 日期范围 |  |  |  |
| tax_rate | 税率 | 枚举 |  |  | 13%、6%、3% |
| overseas_tax_rate | 海外税率 | 数值/百分比 |  |  |  |
| bidding_service_fee | 投标服务费 | 数值/百分比 |  |  |  |
| purchase_service_fee | 采购服务费 | 数值/金额 |  |  |  |
| co_marketing_fee | Co-marketing费 | 数值/金额 |  |  |  |
| testing_service_fee | 检测服务费 | 数值/金额 |  |  |  |
| sales_agent_fee | 销售代理服务费 | 数值/金额 |  |  |  |
| other_business_fee | 其他商务费用 | 数值/金额 |  |  |  |
| quality_control_req | 质量专控要求 | 枚举 |  |  | 无/2502SDM1、2502SDM2C、SDM2F-WK |
| is_carbon_footprint | 是否碳足迹组件 | 布尔 |  |  |  |
| is_plastic_tax | 是否征收塑料税 | 布尔 |  |  |  |
| weee | WEEE | 布尔 |  |  |  |
| quality_insurance_req | 质量保险要求 | 布尔 |  |  |  |
| commercial_liability_insurance | 商业综合责任险 | 布尔 |  |  |  |
| credit_insurance_req | 信用保险要求 | 布尔 |  |  |  |
| credit_sale_ratio | 赊销比例（信保） | 数值/比例 |  |  |  |
| advance_guarantee_req | 预付款保函要求 | 布尔 |  |  |  |
| advance_guarantee_amount_ratio | 预付款保函金额比例 | 数值/比例 |  |  |  |
| advance_guarantee_term_month | 预付款保函期限（月） | 数值/整数 |  |  |  |
| performance_guarantee_req | 履约保函要求 | 布尔 |  |  |  |
| performance_guarantee_amount_ratio | 履约保函金额比例 | 数值/比例 |  |  |  |
| performance_guarantee_term_month | 履约保函期限（月） | 数值/整数 |  |  |  |
| warranty_guarantee_req | 质保保函要求 | 布尔 |  |  |  |
| warranty_guarantee_amount_ratio | 质保保函金额比例 | 数值/比例 |  |  |  |
| warranty_guarantee_term_month | 质保保函期限（月） | 数值/整数 |  |  |  |
| logistics_calc_region | 物流测算区域 | 枚举 |  |  | 国际/国内 |
| trade_term | 贸易术语 | 枚举 |  |  | 国内：送货上门/客户自提；\n国际：FOB(装运港船上交货)/DDP(完税后交货)/EXW(工厂交货)...... |
| transport_type | 运输类型 | 枚举 |  |  | 点对点/按距离 |
| logistics_fee_adjust | 物流费用调整 | 数值/金额 |  |  |  |
| logistics_inquiry_status | 物流询价状态 | 数值/金额 |  |  |  |
| calc_status | 测算状态 | 枚举 |  |  |  |
| audit_status | 审核状态 | 枚举 |  |  |  |

### clm_calc_destination

- 业务对象：价格测算单
- 逻辑实体：目的地信息

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| calc_order_id | 测算单id | 文本 |  | 是 |  |
| destination_id | 目的地信息id | 文本 | 是 |  |  |
| ship_from_type | 发运地类型 | 枚举 |  |  |  |
| ship_from | 发运地 | 枚举 |  |  |  |
| ship_from_country | 发运国家 | 文本/关联对象 |  |  |  |
| departure_port | 起运港 | 文本 |  |  |  |
| dest_port_country | 目的港国家 | 枚举 |  |  |  |
| dest_port | 目的港 | 文本 |  |  |  |
| country_region | 国家/地区 | 文本 |  |  |  |
| province | 省 | 文本 |  |  |  |
| city | 市 | 文本 |  |  |  |
| district | 区/县 | 文本 |  |  |  |
| postal_code | 邮编 | 文本 |  |  |  |
| trade_term | 贸易术语 | 文本 |  |  |  |
| ship_factory | 发货工厂 | 枚举 |  |  |  |
| distance | 距离 | 数值/浮点 |  |  |  |

### clm_calc_product

- 业务对象：价格测算单
- 逻辑实体：产品信息

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| calc_order_id | 测算单id | 文本 |  | 是 |  |
| product_line_id | 产品行id | 文本 | 是 |  |  |
| product_series | 产品系列 | 文本 |  |  |  |
| product_model | 产品型号 | 文本/关联对象 |  |  |  |
| product_item_code | 成品编码 | 文本/关联对象 |  |  |  |
| product_item_name | 成品描述 | 文本 |  |  |  |
| version_ext | 版本扩展 | 文本/关联对象 |  |  |  |
| scheme_desc | 方案描述 | 文本 |  |  |  |
| spec | 规格 | 文本 |  |  |  |
| quantity | 数量 | 数值/浮点 |  |  |  |
| price | 价格 | 数值/金额 |  |  |  |
| cost__calc | 是否成本测算 | 布尔 |  |  |  |
| base_cost | 基础成本 | 数值/整数 |  |  |  |
| profit_markup | 利润加成 | 数值/金额 |  |  |  |
| other_markup | 其他加价 | 数值/金额 |  |  |  |
| spare_quantity | 备件数量 | 数值/金额 |  |  |  |
| gift_quantity | 赠品数量 | 数值/整数 |  |  |  |
| product_category | 产品大类 | 枚举 |  |  |  |

### clm_calc_product_tech

- 业务对象：价格测算单
- 逻辑实体：产品技术参数

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| calc_order_id | 测算单id | 文本 |  | 是 |  |
| product_line_id | 产品行id | 文本 |  | 是 |  |
| product_tech_id | 产品技术参数id | 文本 | 是 |  |  |
| product_series | 产品系列 | 枚举 |  |  |  |
| product_model | 产品型号 | 文本 |  |  |  |
| product_item_code | 成品编码 | 文本/关联对象 |  |  | 91008277 |
| product_item_name | 成品描述 | 文本 |  |  | 锂亚电池F0041W-LF,ER14250/W15,MOLEX5264反，陆运 |
| machine_model | 机械号 | 文本 |  |  | F0041W-LF |
| plug_wire_model | 插头线型号 | 文本 |  |  | 5264-2P |
| plug_direction | 插头方向 | 枚举 |  |  | 反向/正向 |
| wire_length | 线长 | 数值/浮点 |  |  | 16 |
| is_wire_wound | 是否绕线 | 布尔 |  |  | 否 |
| cell_code | 电芯编码 | 文本 |  |  | 81005049 |
| cell_model | 电芯型号 | 文本 |  |  | ER14250V1.3 |
| reference_size | 参考尺寸 | 文本 |  |  | 1/2AA |
| rated_voltage | 标趁电压（V） | 数值/浮点 |  |  | 3.6 |
| rated_capacity | 标称容量（mAh） | 数值/浮点 |  |  | 1200 |
| max_continuous_current | 最大持续电流（mA） | 数值/浮点 |  |  | 35 |
| max_pulse_current | 最大脉冲电流（mA） | 数值/浮点 |  |  | 50 |
| operating_temperature | 工作温度 | 文本 |  |  | -55-+85℃ |
| max_dimension | 最大尺寸(mm) | 文本 |  |  | 14.5x25.4 |
| weight | 重量（g） | 数值/浮点 |  |  | 10 |
| storage_temperature | 存储温度 | 文本 |  |  | +30℃ |
| application_scope | 应用范围 | 文本 |  |  | 公共仪表、警报\\安全设备、记忆备份、追踪设备、汽车电子、专业电子、实时时钟、等 |
| version_ext | 版本扩展 | 文本 |  |  |  |
| scheme_desc | 方案描述 | 文本 |  |  |  |
| component_product_code | 组件产品编码 | 文本 |  |  |  |
| capacity | 电量 | 枚举 |  |  | 140kWh/280kWh |
| cooling_type | 冷却方式 | 枚举 |  |  | 风冷/液冷 |
| extra_function | 附加功能 | 枚举 |  |  | 低温加热/云端通讯 |
| frame_size | 边框尺寸 | 枚举 |  |  | 30mm、40mm |
| frame_color | 边框颜色 | 枚举 |  |  | 白框 /灰框/黑框 |
| frame_material | 边框材质 | 枚举 |  |  |  |
| junction_box | 接线盒 | 枚举 |  |  |  |
| frame_hole_count | 边框孔数 | 枚举 |  |  |  |
| frame_film_thickness | 边框膜厚 | 枚举 |  |  | AA10/AA12 |
| film_scheme | 胶膜方案 | 枚举 |  |  | POE+EVA /双POE |
| film_weight | 胶膜克重 | 枚举 |  |  | 双ECA（400+380） |
| connector | 连接端子 | 枚举 |  |  | PV-LR5 |
| avg_cable_length | 平均线长（米） | 枚举 |  |  |  |
| positive_cable_length | 正极线长（米） | 数值 |  |  |  |
| negative_cable_length | 负极线长（米） | 数值 |  |  |  |
| label | 标签 | 枚举 |  |  | 防震标签/RFID标签/隆基标准 |
| dust_plug | 防尘塞 | 枚举 |  |  | 无/防尘塞 |
| current_bin | 电流分档 | 枚举 |  |  | 中间档位按0.1A分档/中间档位按0.15A分档/中间档位按0.2A分档 |
| yield_loss | 良率损失 | 枚举 |  |  |  |
| el_full_inspect_req | EL全检要求 | 数值/比例 |  |  |  |
| transport_scheme | 运输保障方案 | 枚举 |  |  | 加厚包装/标准运输 |
| other_nonstd_markup | 其他非标加价 | 数值 |  |  |  |

### clm_calc_payment

- 业务对象：价格测算单
- 逻辑实体：付款信息

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| calc_order_id | 测算单id | 文本 |  | 是 |  |
| payment_line_id | 付款行id | 文本 | 是 |  |  |
| stage_name | 阶段名称 | 枚举 |  |  | 预付款/备料款/发货款/到货款/验收款/质保金 |
| fund_occupy_days | 资金占用天数 | 枚举 |  |  |  |
| plan_payment_ratio | 计划收付款比例 | 数值/整数 |  |  |  |
| payback_dimension | 回款维度 | 数值/比例 |  |  |  |
| pre_delivery_payment | 货前款 | 枚举 |  |  |  |

### clm_calc_logistics

- 业务对象：价格测算单
- 逻辑实体：物流信息

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| calc_order_id | 测算单id | 文本 |  | 是 |  |
| logistics_id | 物流信息id | 文本 | 是 |  |  |
| logistics_calc_type | 物流计算类型 | 布尔 |  |  | 优选/指定 |
| logistics_type | 物流类型 | 枚举 |  |  | 快递运输/陆运运输/铁运运输/水运运输 |
| vehicle_type | 车型 | 枚举 |  |  |  |
| max_vehicle_type | 最大可进车型 | 枚举 |  |  |  |
| container_type | 柜型 | 枚举 |  |  | 20GP/40HQ |

### clm_calc_bom_head

- 业务对象：价格测算单
- 逻辑实体：实例BOM头信息

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| calc_order_id | 测算单id | 文本 |  | 是 |  |
| product_line_id | 产品行id | 文本 |  | 是 |  |
| bom_header_id | bom清单头ID | 文本 | 是 |  |  |
| bom_name | 物料清单名称 | 文本 |  |  |  |
| product_item_id | 产品编码ID | 文本 |  |  |  |
| product_item_code | 产品编码 | 文本 |  |  |  |
| product_item_name | 产品名称 | 文本 |  |  |  |
| product_item_spec | 规格型号 | 文本 |  |  |  |
| basis_quantity | 基准数量 | 数值/浮点 |  |  |  |
| bom_version | BOM版本 | 文本 |  |  |  |

### clm_calc_bom_line

- 业务对象：价格测算单
- 逻辑实体：实例BOM行信息

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| ref_bom_header_id | bom清单头ID | 文本 |  | 是 | 关联clm_calc_bom_head.bom_header_id |
| bom_line_id | 物料清单行ID | 文本 | 是 |  |  |
| seq_num | 序号 | 数值/整数 |  |  |  |
| component_item_id | 组件ID | 文本 |  |  |  |
| component_item_code | 组件编码 | 文本 |  |  |  |
| component_item_name | 组件名称 | 文本 |  |  |  |
| component_item_spec | 组件规格型号 | 文本 |  |  |  |
| component_item_quantity | 物料清单组件数量 | 数值/浮点 |  |  |  |
| component_item_uom_code | 物料用量单位编码 | 文本 |  |  |  |
| material_unit_price | 材料单价 | 数值/金额 |  |  |  |
| direct_labor_unit_price | 直接人工单价 | 数值/金额 |  |  |  |
| indirect_labor_unit_price | 间接人工单价 | 数值/金额 |  |  |  |
| machine_cost | 机器费用 | 数值/金额 |  |  |  |
| other_charge | 其他制费 | 数值/金额 |  |  |  |

### clm_calc_markup_item

- 业务对象：价格测算单
- 逻辑实体：加价明细

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| calc_order_id | 测算单id | 文本 |  | 是 |  |
| product_line_id | 产品行id | 文本 |  | 是 |  |
| markup_item_id | 加价明细id | 文本 | 是 |  |  |
| rule_category | 规则分类 | 枚举值 |  |  | 定价/报价 |
| seq_num | 序号 | 数值/浮点 |  |  |  |
| markup_item_name | 加价项名称 | 文本 |  |  |  |
| markup_value | 加价值 | 文本 |  |  |  |

### clm_quote_base_info

- 业务对象：报价单
- 逻辑实体：报价基本信息

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| quote_order_id | 报价单id | 文本 | 是 |  |  |
| quote_order_no | 报价单号 | 文本 |  |  |  |
| quote_order_name | 报价单名称 | 文本 |  |  |  |
| calc_order_id | 测算单id | 文本 |  | 是 |  |
| calc_order_no | 关联测算单号 | 文本 |  |  |  |
| quote_form | 报价形式 | 文本/关联对象 |  |  |  |
| quote_type | 报价类型 | 枚举 |  |  |  |
| quote_template | 报价模板 | 枚举 |  |  |  |
| quote_valid_days | 报价有效天数 | 文本/枚举 |  |  |  |
| is_sealed | 是否用印 | 数值/整数 |  |  |  |
| opportunity_no | 关联商机编号 | 布尔 |  |  |  |
| currency | 币种 | 文本/关联对象 |  |  |  |
| sign_entity | 签约主体 | 枚举 |  |  |  |
| sign_entity_address | 签约主体地址 | 文本 |  |  |  |
| sign_entity_credit_code | 签约主体社会信用代码 | 文本 |  |  |  |
| quote_contact | 报价联系人 | 文本 |  |  |  |
| quote_contact_phone | 报价联系人电话 | 文本 |  |  |  |
| quote_contact_dept | 报价联系人部门 | 文本 |  |  |  |
| apply_date | 申请日期 | 文本 |  |  |  |
| customer | 客户 | 日期 |  |  |  |
| customer_address | 客户地址 | 文本/关联对象 |  |  |  |
| customer_contact | 客户联系人 | 文本 |  |  |  |
| customer_contact_info | 客户联系方式 | 文本 |  |  |  |
| quote_remark | 报价说明 | 文本 |  |  |  |

### clm_quote_product

- 业务对象：报价单
- 逻辑实体：报价明细

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| quote_order_id | 报价单id | 文本 |  | 是 |  |
| quote_product_line_id | 产品行id | 文本 | 是 |  |  |
| product_series | 产品系列 | 文本/关联对象 |  |  |  |
| product_model | 产品型号 | 文本/关联对象 |  |  |  |
| product_item_code | 成品编码 | 文本/关联对象 |  |  |  |
| product_item_name | 成品描述 | 文本 |  |  |  |
| version_ext | 版本扩展 | 文本/关联对象 |  |  |  |
| scheme_desc | 方案描述 | 文本 |  |  |  |
| spec | 规格 | 文本 |  |  |  |
| quantity | 数量 | 数值/浮点 |  |  |  |
| quoted_price | 报价 | 数值/金额 |  |  |  |
| discount | 折扣 | 数值/比例 |  |  |  |
| estimated_price | 折后价格 | 数值/金额 |  |  |  |
| total_amount | 总金额 | 数值/金额 |  |  |  |
| tax_rate | 税率 | 数值/比例 |  |  |  |
| tax_amount | 税金 | 数值/金额 |  |  |  |

## 配置助手（config）

共 9 个逻辑实体。

### md_clm_material_base_info

- 业务对象：产品
- 逻辑实体：物料编码信息-物料数据

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| material_id | 物料ID |  | 是 |  |  |
| material_item_type | 物料类型 |  |  |  |  |
| number | 物料编码 |  |  |  |  |
| spec | 规格型号 |  |  |  |  |
| name | 名称 |  |  |  |  |
| material_source_type | 来源类型 |  |  |  |  |
| md_material_category_id | 分类 |  |  |  |  |

### product_series_info

- 业务对象：产品
- 逻辑实体：产品系列

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| product_id | 产品系列主键 |  | 是 |  |  |
| product_code | 产品系列编码 |  |  |  |  |
| product_name | 产品系列名称 |  |  |  |  |
| product_type | 产品类型 |  |  |  | 产品类型：电池包 / 储能柜 / 充电桩（GLOBAL规则按此过滤） |
| plm_material_part_id | PLM原始物料编码 |  |  | 关联material_info.material_id |  |
| status | 状态 |  |  |  |  |

### product_series_ref_feature_info

- 业务对象：产品
- 逻辑实体：产品系列所使用特征

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| id | id |  | 是 |  |  |
| product_id | 产品系列编码 |  |  | product_series_info.product_id |  |
| bd_clm_feature_id | 参数编码 |  |  | bd_clm_feature.bd_clm_feature_id |  |
| is_required | 是否必填 |  |  |  |  |
| sort_order | 排序编号 |  |  |  |  |

### bd_clm_feature

- 业务对象：产品
- 逻辑实体：基础特征库

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| bd_clm_feature_id | 主键ID |  | 是 |  |  |
| name | 名称 |  |  |  | 和规则助手共用同一张表 |
| value_type_enum | 数据值类型 |  |  |  |  |
| value_constraint | 数值约束 |  |  |  |  |
| unit | 数值单位 |  |  |  |  |
| desc | 规则描述 |  |  |  |  |
| updated_by | 更新者 |  |  |  |  |
| updated_at | 更新时间 |  |  |  |  |
| effective_status | 是否生效 |  |  |  |  |
| created_by | 创建者 |  |  |  |  |
| created_at | 创建时间 |  |  |  |  |
| is_deleted | 是否删除 |  |  |  |  |
| deleted_by | 删除者 |  |  |  |  |
| deleted_at | 删除时间 |  |  |  |  |

### CLM_BASE_INFO

- 业务对象：配置BOM
- 逻辑实体：配置BOM头信息

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| bom_header_id | bom清单头ID |  | 是 |  |  |
| bom_name | 物料清单名称 |  |  |  |  |
| product_item_id | 产品编码ID |  |  |  |  |
| product_item_code | 产品编码 |  |  |  |  |
| product_item_name | 产品名称 |  |  |  |  |
| product_item_spec | 规格型号 |  |  |  |  |
| basis_quantity | 基准数量 |  |  |  |  |
| bom_version | BOM版本 |  |  |  |  |
| status | 状态 |  |  |  |  |
| delete_flag | 删除标识 |  |  |  |  |
| creation_date | 创建日期 |  |  |  |  |
| created_by | 创建人 |  |  |  |  |
| last_update_date | 最后修改日期 |  |  |  |  |
| last_updated_by | 最后修改人 |  |  |  |  |

### CLM_LINE_INFO

- 业务对象：配置BOM
- 逻辑实体：配置BOM行信息

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| bom_line_id | 物料清单行ID |  | 是 |  |  |
| seq_num | 序号 |  |  |  |  |
| component_item_id | 组件ID |  |  |  |  |
| component_item_code | 组件编码 |  |  |  |  |
| component_item_name | 组件名称 |  |  |  |  |
| component_item_spec | 组件规格型号 |  |  |  |  |
| component_item_quantity | 物料清单组件数量 |  |  |  |  |
| component_item_uom_code | 物料用量单位编码 |  |  |  |  |
| node_type | 节点类型 |  |  |  | mandatory、optional、conditional |
| trigger_param | 触发参数 |  |  |  | optional时使用 |
| trigger_param_value | 触发参数值 |  |  |  | optional时使用 |
| effective_date | 生效日期 |  |  |  |  |
| disable_date | 失效日期 |  |  |  |  |
| ref_bom_header_id | 物料清单头ID |  |  | 关联CLM_BASE_INFO.bom_header_id |  |
| creation_date | 创建日期 |  |  |  |  |
| created_by | 创建人 |  |  |  |  |
| last_update_date | 最后修改日期 |  |  |  |  |
| last_updated_by | 最后修改人 |  |  |  |  |

### clm_base_rule_rel

- 业务对象：配置BOM
- 逻辑实体：配置BOM头与规则关系表

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| ref_id | 关联关系ID |  | 是 |  |  |
| bom_head_id | 配置BOM头ID |  |  | 关联CLM_BASE_INFO.bom_header_id |  |
| rule_id | 规则ID |  |  | 关联md_clm_distribution_rule.md_clm_distribution_rule_id |  |

### clm_line_rule_rel

- 业务对象：配置BOM
- 逻辑实体：配置BOM行与规则关系表

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| ref_id | 关联关系ID |  | 是 |  |  |
| bom_line_id | 配置BOM行ID |  |  | 关联CLM_LINE_INFO.bom_line_id |  |
| rule_id | 规则ID |  |  | 关联md_clm_distribution_rule.md_clm_distribution_rule_id |  |

### product_para_value

- 业务对象：配置BOM
- 逻辑实体：产品参数值表

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| id | id | 文本 | 是 |  |  |
| product_item_code | 成品编码 | 文本 |  | 是 |  |
| product_item_name | 成品描述 | 文本 |  |  |  |
| machine_model | 机械号 | 文本 |  |  |  |
| plug_wire_model | 插头线型号 | 文本 |  |  |  |
| plug_direction | 插头方向 | 枚举 |  |  |  |
| wire_length | 线长 | 数值/浮点 |  |  |  |
| is_wire_wound | 是否绕线 | 布尔 |  |  |  |
| cell_code | 电芯编码 | 文本 |  |  |  |
| cell_model | 电芯型号 | 文本 |  |  |  |
| reference_size | 参考尺寸 | 文本 |  |  |  |
| rated_voltage | 标趁电压（V） | 数值/浮点 |  |  |  |
| rated_capacity | 标称容量（mAh） | 数值/浮点 |  |  |  |
| max_continuous_current | 最大持续电流（mA） | 数值/浮点 |  |  |  |
| max_pulse_current | 最大脉冲电流（mA） | 数值/浮点 |  |  |  |
| operating_temperature | 工作温度 | 文本 |  |  |  |
| max_dimension | 最大尺寸(mm) | 文本 |  |  |  |
| weight | 重量（g） | 数值/浮点 |  |  |  |
| storage_temperature | 存储温度 | 文本 |  |  |  |
| application_scope | 应用范围 | 文本 |  |  |  |
| service_life | 使用寿命 | 文本 |  |  |  |
| hermeticity | 密封性 | 文本 |  |  |  |

## 规则助手（rule）

共 5 个逻辑实体。

### md_clm_distribution_rule

- 业务对象：规则配置
- 逻辑实体：产品配单规则

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| md_clm_distribution_rule_id | 主键ID |  | 是 |  |  |
| rule_name | 规则名称 |  |  |  |  |
| rule_desc | 规则描述 |  |  |  |  |
| rule_expression_view | 规则表达式（页面显示） |  |  |  |  |
| rule_expression | 规则表达式 |  |  |  |  |
| updated_by | 更新者 |  |  |  |  |
| updated_at | 更新时间 |  |  |  |  |
| effective_status | 是否生效 |  |  |  |  |
| created_by | 创建者 |  |  |  |  |
| created_at | 创建时间 |  |  |  |  |
| is_deleted | 是否删除 |  |  |  |  |
| deleted_by | 删除者 |  |  |  |  |
| deleted_at | 删除时间 |  |  |  |  |
| effective_start_time | 生效开始时间 |  |  |  |  |
| effective_end_time | 生效结束时间 |  |  |  |  |
| corp_id | 企业ID |  |  |  |  |
| system_version | 系统版本号 |  |  |  |  |
| owner_org_code | 数据所属组织 |  |  |  |  |

### md_clm_material_cost_cnf

- 业务对象：规则配置
- 逻辑实体：物料成本配置

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| md_clm_material_cost_cnf_id | 主键ID |  | 是 |  |  |
| material_id | 物料id |  |  | 关联clm_line_info.bom_line_id |  |
| material_code | 物料编码 |  |  | 关联product_para_value.product_item_code |  |
| material_name | 物料名称 |  |  |  |  |
| price_validity_date | 生效日期 |  |  |  |  |
| price_expiration_date | 失效日期 |  |  |  |  |
| material_unit_price | 材料单价 |  |  |  |  |
| direct_labor_unit_price | 直接人工单价 |  |  |  |  |
| indirect_labor_unit_price | 间接人工单价 |  |  |  |  |
| machine_cost | 机器费用 |  |  |  |  |
| other_charge | 其他制费 |  |  |  |  |
| updated_by | 更新者 |  |  |  |  |
| updated_at | 更新时间 |  |  |  |  |
| effective_status | 是否生效 |  |  |  |  |
| created_by | 创建者 |  |  |  |  |
| created_at | 创建时间 |  |  |  |  |
| is_deleted | 是否删除 |  |  |  |  |
| deleted_by | 删除者 |  |  |  |  |
| deleted_at | 删除时间 |  |  |  |  |
| effective_start_time | 生效开始时间 |  |  |  |  |
| effective_end_time | 生效结束时间 |  |  |  |  |
| corp_id | 企业ID |  |  |  |  |
| system_version | 系统版本号 |  |  |  |  |
| owner_org_code | 数据所属组织 |  |  |  |  |

### md_clm_material_feature_cnf

- 业务对象：规则配置
- 逻辑实体：物料特征配置

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| md_clm_material_feature_cnf_id | 主键ID |  | 是 |  |  |
| md_clm_material_cost_cnf_id | 物料成本配置ID |  |  |  |  |
| material_id | 物料id |  |  | 关联clm_line_info.bom_line_id |  |
| material_code | 物料编码 |  |  |  |  |
| material_name | 物料名称 |  |  |  |  |
| features | 特征 |  |  |  |  |
| feature_value | 特征值 |  |  |  |  |
| updated_by | 更新者 |  |  |  |  |
| updated_at | 更新时间 |  |  |  |  |
| effective_status | 是否生效 |  |  |  |  |
| created_by | 创建者 |  |  |  |  |
| created_at | 创建时间 |  |  |  |  |
| is_deleted | 是否删除 |  |  |  |  |
| deleted_by | 删除者 |  |  |  |  |
| deleted_at | 删除时间 |  |  |  |  |
| effective_start_time | 生效开始时间 |  |  |  |  |
| effective_end_time | 生效结束时间 |  |  |  |  |
| corp_id | 企业ID |  |  |  |  |
| system_version | 系统版本号 |  |  |  |  |
| owner_org_code | 数据所属组织 |  |  |  |  |

### md_clm_material_price_rule

- 业务对象：规则配置
- 逻辑实体：产品定价规则

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| md_clm_material_price_rule_id | 主键ID |  | 是 |  |  |
| rule_name | 规则名称 |  |  |  |  |
| rule_classification | 规则分类 |  |  |  |  |
| rule_desc | 规则描述 |  |  |  |  |
| rule_expression | 规则表达式 |  |  |  |  |
| rule_expression_view | 规则表达式（页面显示） |  |  |  |  |
| updated_by | 更新者 |  |  |  |  |
| updated_at | 更新时间 |  |  |  |  |
| effective_status | 是否生效 |  |  |  |  |
| created_by | 创建者 |  |  |  |  |
| created_at | 创建时间 |  |  |  |  |
| is_deleted | 是否删除 |  |  |  |  |
| deleted_by | 删除者 |  |  |  |  |
| deleted_at | 删除时间 |  |  |  |  |
| effective_start_time | 生效开始时间 |  |  |  |  |
| effective_end_time | 生效结束时间 |  |  |  |  |
| corp_id | 企业ID |  |  |  |  |
| system_version | 系统版本号 |  |  |  |  |
| owner_org_code | 数据所属组织 |  |  |  |  |

### bd_clm_feature

- 业务对象：规则配置
- 逻辑实体：基础特征库

| 属性编码 | 属性名称 | 字段类型 | 主键 | 外键 | 备注 |
| --- | --- | --- | --- | --- | --- |
| bd_clm_feature_id | 主键ID |  | 是 |  |  |
| name | 名称 |  |  |  |  |
| value_type_enum | 数据值类型 |  |  |  |  |
| value_constraint | 数值约束 |  |  |  |  |
| unit | 数值单位 |  |  |  |  |
| desc | 规则描述 |  |  |  |  |
| updated_by | 更新者 |  |  |  |  |
| updated_at | 更新时间 |  |  |  |  |
| effective_status | 是否生效 |  |  |  |  |
| created_by | 创建者 |  |  |  |  |
| created_at | 创建时间 |  |  |  |  |
| is_deleted | 是否删除 |  |  |  |  |
| deleted_by | 删除者 |  |  |  |  |
| deleted_at | 删除时间 |  |  |  |  |
| effective_start_time | 生效开始时间 |  |  |  |  |
| effective_end_time | 生效结束时间 |  |  |  |  |
| corp_id | 企业ID |  |  |  |  |
| system_version | 系统版本号 |  |  |  |  |
| owner_org_code | 数据所属组织 |  |  |  |  |

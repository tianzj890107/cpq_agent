# -*- coding: utf-8 -*-
"""知识库（`kb_*`）——唯一事实源：配置报价 CPQ 的 Postgres `cpq_kb` schema。

分工（见 docs/specs/kb-in-pg-http-snapshot.md）：

  · 知识库数据只有一份，落在 PG 的 `cpq_kb`（本模块负责建表、版本号、快照、导入）；
  · 技术工艺（8012）不直连 PG，只经一体化服务（8010）的
    `GET /wf/tech/kb/snapshot` 取整包快照，见 tech_app/backend/services/cpq_kb_client.py；
  · `kb_version` 是快照失效的唯一依据（`cpq_kb.kb_meta` 的单行记录）：任何一次写入
    之后 `+1`，读侧不得靠时间戳或文件 mtime 判新旧；
  · PG/schema 不可用时**必须报错**，绝不返回 `tables: {}` 或 `kb_version: 0` ——
    那会被调用方误判成"库里没有可复用零件"，正是本批要消灭的假象。

表结构与 `tech_app/backend/storage/da_schema.sql` 的 `kb_*` 定义 1:1（列名、主键、
唯一键一致；时间列与 JSON 列保持 `text` 原样搬，不转 `timestamptz` / `jsonb` ——
转了就与旧 SQLite 的取值形态不同，快照喂给 kb_repo 会算出不同的匹配结果）。
建表语句幂等：`CREATE SCHEMA/TABLE IF NOT EXISTS` + 增量列 `ALTER TABLE ... ADD COLUMN
IF NOT EXISTS`，与 cpq_auth.py 同套路。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import sqlite3
from datetime import datetime
from typing import Any, Optional

import cpq_db

# 知识库 schema。与 cpq_wf 一样，取值可被环境变量覆盖。
SCHEMA = (os.getenv("CPQ_KB_SCHEMA") or "cpq_kb").strip() or "cpq_kb"


class KbUnavailable(RuntimeError):
    """PG 连不上 / schema 或表缺失。不回落空表，直接让调用方报错。"""


#: 数据来源分层（Spec §3）：demo = 演示基线；workbook = 权威工作簿规则；
#: dwg_confirmed = 真实 DWG 解析 + 人工确认；unknown = 历史入库未分类（生产预检据此阻断）。
SOURCE_TYPES = ("demo", "workbook", "dwg_confirmed", "unknown")
#: 人工复核状态闭集（与 kb_packaging_cost_formula.review_status 的 CHECK 一致）。
REVIEW_STATUSES = ("draft", "reviewed", "retired")

KB_TABLES = (
    "kb_component",
    "kb_component_param",
    "kb_component_feature",
    "kb_component_drawing",
    "kb_component_embedding",
    "kb_standard_part",
    "kb_equipment_class",
    "kb_equipment",
    "kb_process_step",
    "kb_process_param_template",
    "kb_process_route",
    "kb_process_route_step",
    "kb_inspection_item",
    "kb_material",
    "kb_material_property",
    "kb_material_price",
    "kb_supplier",
    "kb_supplier_capability",
    "kb_cost_rate",
    "kb_cost_factor",
    # 包装扩展表（包装第 3 批）；前 20 张的名字与顺序是快照契约，不得变动。
    "kb_packaging_box_type",
    "kb_packaging_part_template",
    "kb_packaging_process_template",
    "kb_packaging_insert_accessory",
    "kb_packaging_cost_formula",
    "kb_packaging_logistics_rule",
    "kb_packaging_match_weight",
    # 第 2 批（上线闭环）补齐：本地 da_schema.sql 有、PG 快照此前没有的两张包装表。
    # 缺这两张 → 走 PG 快照的 34 上它们永远是空的（kb_repo.py:928/936 在读）。
    "kb_packaging_cost_content",
    "kb_packaging_tooling_rule",
    # 逆向快速报价（批 1）：案例库的有效期口径 / 阈值等配置，一行一个 key 的 JSON。
    # 只能追加：前面所有表的相对顺序是快照契约（同名单测 a2 冻结）。
    "kb_quick_quote_config",
)

# 每张表的业务主键（ON CONFLICT 的目标）。取值与 da_schema.sql 的
# PRIMARY KEY / UNIQUE 完全一致 —— 导入器的幂等 upsert 就靠它。
KB_KEYS = {
    "kb_component": ("component_id",),
    "kb_component_param": ("param_id",),
    "kb_component_feature": ("feature_id",),
    "kb_component_drawing": ("drawing_id",),
    "kb_component_embedding": ("component_id", "scope", "model_name"),
    "kb_standard_part": ("std_id",),
    "kb_equipment_class": ("class_code",),
    "kb_equipment": ("equipment_id",),
    "kb_process_step": ("step_code",),
    "kb_process_param_template": ("tpl_id",),
    "kb_process_route": ("route_code",),
    "kb_process_route_step": ("route_code", "seq"),
    "kb_inspection_item": ("insp_code",),
    "kb_material": ("material_code",),
    "kb_material_property": ("prop_id",),
    "kb_material_price": ("price_id",),
    "kb_supplier": ("supplier_id",),
    "kb_supplier_capability": ("cap_id",),
    "kb_cost_rate": ("rate_code",),
    "kb_cost_factor": ("factor_code",),
    "kb_packaging_box_type": ("box_type_code",),
    "kb_packaging_part_template": ("part_code",),
    "kb_packaging_process_template": ("box_type_code", "part_code", "seq"),
    "kb_packaging_insert_accessory": ("accessory_code",),
    "kb_packaging_cost_formula": ("formula_code",),
    "kb_packaging_logistics_rule": ("rule_code",),
    "kb_packaging_match_weight": ("dimension",),
    "kb_packaging_cost_content": ("content_code",),
    "kb_packaging_tooling_rule": ("tooling_code",),
    "kb_quick_quote_config": ("key",),
}

# 单项也必须写成 1-元组 ("x",)：少了那个逗号就退化成字符串，ON CONFLICT ("c","o",...)
# 会按字符拆成列名 —— 这一类错误在假快照的单测里看不见，只有真连 PG 才会炸。
KB_KEYS = {name: ((value,) if isinstance(value, str) else tuple(value))
           for name, value in KB_KEYS.items()}

_DDL_TEMPLATE = [
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_component (
    component_id        text PRIMARY KEY,
    component_code      text NOT NULL UNIQUE,          -- CMP-BRK-0012
    name                text NOT NULL,
    category            text,                          -- 结构件/回转件/钣金件/陶瓷基板...
    subcategory         text,
    source_type         text NOT NULL DEFAULT 'self_made'
                        CHECK (source_type IN ('self_made', 'outsourced', 'standard')),
    spec_summary        text,
    default_material_code text REFERENCES {{schema}}.kb_material(material_code) ON DELETE SET NULL,
    default_route_code  text REFERENCES {{schema}}.kb_process_route(route_code) ON DELETE SET NULL,
    envelope_l          double precision,                          -- 外形包络 mm,粗筛用
    envelope_w          double precision,
    envelope_h          double precision,
    mass_kg             double precision,
    manufacturability   text DEFAULT 'mature'
                        CHECK (manufacturability IN ('mature', 'conditional', 'hard')),
    reuse_count         bigint NOT NULL DEFAULT 0,    -- 复用次数,推荐排序权重
    lifecycle           text NOT NULL DEFAULT 'active'
                        CHECK (lifecycle IN ('draft', 'active', 'deprecated')),
    rev                 text DEFAULT 'A',              -- 当前有效版本,与图纸版本联动
    tags                text,                          -- JSON 数组
    note                text,
    owner               text,
    created_by          text,
    created_at          text,
    updated_at          text,
    industry            text   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_component_param (
    param_id        bigserial PRIMARY KEY,
    component_id    text NOT NULL REFERENCES {{schema}}.kb_component(component_id) ON DELETE CASCADE,
    param_key       text NOT NULL,                     -- length / thickness / hole_diameter...
    param_name      text,
    value_num       double precision,                              -- 数值型走这里,支持范围检索
    value_text      text,
    unit            text,
    tol_lower       double precision,                              -- 允差下限(绝对值,mm/单位)
    tol_upper       double precision,
    is_key          bigint NOT NULL DEFAULT 0 CHECK (is_key IN (0, 1)),
    note            text,
    UNIQUE (component_id, param_key)
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_component_feature (
    feature_id      bigserial PRIMARY KEY,
    component_id    text NOT NULL REFERENCES {{schema}}.kb_component(component_id) ON DELETE CASCADE,
    seq             bigint NOT NULL DEFAULT 0,        -- 建模顺序
    feature_type    text NOT NULL CHECK (feature_type IN (
                        'plate', 'box', 'cylinder', 'hole',
                        'hole_pattern', 'fillet', 'chamfer')),
    length          double precision, width      double precision, thickness double precision, height double precision,
    diameter        double precision, radius     double precision, distance  double precision,
    x               double precision, y          double precision,
    count_x         bigint, count_y bigint,
    spacing_x       double precision, spacing_y  double precision,
    purpose         text
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_component_drawing (
    drawing_id      bigserial PRIMARY KEY,
    component_id    text NOT NULL REFERENCES {{schema}}.kb_component(component_id) ON DELETE CASCADE,
    drawing_kind    text NOT NULL CHECK (drawing_kind IN ('2d', '3d', 'doc', 'thumb')),
    file_format     text,                              -- dxf/dwg/pdf/step/stl/sldprt/docx...
    rev             text NOT NULL DEFAULT 'A',
    is_current      bigint NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    file_path       text NOT NULL,                     -- 相对 blob 根,如 kb/components/CMP-.../2d/A/x.dxf
    file_name       text,                              -- 原始文件名(可含中文)
    file_sha256     text,
    file_size       bigint,
    thumbnail_path  text,
    page_count      bigint,
    title_block     text,                              -- JSON:图号/比例/设计者/日期
    uploaded_by     text,
    uploaded_at     text,
    UNIQUE (component_id, drawing_kind, rev, file_path)
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_component_embedding (
    component_id    text NOT NULL REFERENCES {{schema}}.kb_component(component_id) ON DELETE CASCADE,
    scope           text NOT NULL CHECK (scope IN ('name', 'spec', 'feature')),
    model_name      text NOT NULL,
    dim             bigint NOT NULL,
    vector          text NOT NULL,                     -- JSON 数组
    built_at        text,
    PRIMARY KEY (component_id, scope, model_name)
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_standard_part (
    std_id              text PRIMARY KEY,
    standard_no         text,                          -- GB/T 5783
    designation         text NOT NULL,                 -- M8x25
    category            text,                          -- bolt/nut/washer/bearing
    size_params         text,                          -- JSON
    material            text,
    surface_treatment   text,
    unit_price_ref      double precision,
    currency            text DEFAULT 'CNY',
    supplier_id         text REFERENCES {{schema}}.kb_supplier(supplier_id) ON DELETE SET NULL,
    drawing_path        text,                          -- 文件夹路径
    note                text,
    status              text NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'deprecated')),
    industry            text,  -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
    UNIQUE (standard_no, designation)
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_equipment_class (
    class_code      text PRIMARY KEY,                  -- EQC-CNC-VMC
    name            text NOT NULL,
    category        text,
    note            text,
    industry            text   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_equipment (
    equipment_id            text PRIMARY KEY,
    equipment_code          text UNIQUE,
    name                    text NOT NULL,
    model_no                text,
    manufacturer            text,
    equipment_class         text REFERENCES {{schema}}.kb_equipment_class(class_code) ON DELETE SET NULL,
    capability              text,                      -- JSON:行程/最大工件重量/转速/精度/炉温上限
    hourly_rate             double precision,                      -- 元/小时(人工+管理)
    depreciation_per_hour   double precision,                      -- 元/小时,设备折旧
    power_kw                double precision,                      -- 能耗成本用
    workshop                text,
    unit_count              bigint NOT NULL DEFAULT 1,
    status                  text NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'maintenance', 'retired')),
    note                    text,
    updated_at              text,
    industry            text   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_process_step (
    step_code               text PRIMARY KEY,          -- PS-MILL-ROUGH
    name                    text NOT NULL,
    process_type            text NOT NULL CHECK (process_type IN (
                                'blank', 'turning', 'milling', 'drilling', 'boring',
                                'grinding', 'bench', 'sheet_metal', 'welding',
                                'heat_treat', 'surface', 'assembly', 'inspection', 'other')),
    category                text,                      -- 成型/共烧/机加工/金属化/检测
    description_tpl         text,
    applicable_material     text,                      -- JSON 数组:适用材料类别
    applicable_feature      text,                      -- JSON 数组:适用 FeatureType -> 特征驱动推荐
    default_equipment_class text REFERENCES {{schema}}.kb_equipment_class(class_code) ON DELETE SET NULL,
    default_fixture         text,
    default_tooling         text,
    quality_items           text,                      -- JSON:平面度/Ra/尺寸公差
    setup_min               double precision,                      -- 标准准备工时(分钟/批)
    unit_min_formula        text,                      -- 单件工时模型,平台确定性求值
    yield_rate              double precision,                      -- 典型良率 0~1
    is_critical             bigint NOT NULL DEFAULT 0 CHECK (is_critical IN (0, 1)),
    version                 bigint NOT NULL DEFAULT 1,
    effective_from          text,
    effective_to            text,
    status                  text NOT NULL DEFAULT 'active'
                            CHECK (status IN ('draft', 'active', 'deprecated')),
    note                    text,
    created_at              text,
    updated_at              text,
    industry            text   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_process_param_template (
    tpl_id          bigserial PRIMARY KEY,
    step_code       text NOT NULL REFERENCES {{schema}}.kb_process_step(step_code) ON DELETE CASCADE,
    param_key       text NOT NULL,                     -- S/F/ap/温度/保温时长
    param_name      text,
    default_value   text,
    min_value       double precision,
    max_value       double precision,
    unit            text,
    depends_on      text,                              -- 受材料/刀具/厚度影响的说明
    note            text,
    UNIQUE (step_code, param_key)
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_process_route (
    route_code          text PRIMARY KEY,              -- RT-SHEET-BOX
    name                text NOT NULL,
    applicable_category text,                          -- 适用零件类别
    applicable_material text,                          -- JSON 数组
    batch_min           bigint,
    batch_max           bigint,
    summary             text,
    version             bigint NOT NULL DEFAULT 1,
    status              text NOT NULL DEFAULT 'active'
                        CHECK (status IN ('draft', 'active', 'deprecated')),
    created_at          text,
    updated_at          text,
    industry            text   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_process_route_step (
    route_code      text NOT NULL REFERENCES {{schema}}.kb_process_route(route_code) ON DELETE CASCADE,
    seq             bigint NOT NULL,                  -- 10/20/30
    step_code       text NOT NULL REFERENCES {{schema}}.kb_process_step(step_code) ON DELETE RESTRICT,
    is_optional     bigint NOT NULL DEFAULT 0 CHECK (is_optional IN (0, 1)),
    condition_expr  text,                              -- 何时启用该工序
    depends_on      text,                              -- JSON 数组:前序 seq
    param_override  text,                              -- JSON
    note            text,
    PRIMARY KEY (route_code, seq)
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_inspection_item (
    insp_code           text PRIMARY KEY,
    name                text NOT NULL,
    method              text,
    instrument          text,
    sampling_rule       text,
    acceptance_criteria text,
    cost_per_item       double precision,
    note                text,
    industry            text   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_material (
    material_code       text PRIMARY KEY,              -- MAT-STL-Q235
    name                text NOT NULL,
    grade               text,                          -- Q235 / 6061-T6 / Al2O3-96%
    category            text,                          -- 金属/陶瓷粉体/浆料/耗材辅料/包材
    form                text,                          -- 板材/棒材/粉末/浆料/型材
    spec                text,
    density             double precision,                          -- g/cm^3,对齐 ir.Material.density
    base_unit           text NOT NULL DEFAULT 'kg',
    standard_loss_rate  double precision NOT NULL DEFAULT 0,       -- 标准损耗率 0~1
    hazard_level        text,
    storage_req         text,
    status              text NOT NULL DEFAULT 'active'
                        CHECK (status IN ('draft', 'active', 'deprecated')),
    note                text,
    created_at          text,
    updated_at          text,
    industry            text   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_material_property (
    prop_id         bigserial PRIMARY KEY,
    material_code   text NOT NULL REFERENCES {{schema}}.kb_material(material_code) ON DELETE CASCADE,
    prop_key        text NOT NULL,                     -- thermal_conductivity/cte/dielectric/purity/d50
    prop_name       text,
    value_num       double precision,
    value_text      text,
    unit            text,
    test_method     text,
    source_ref      text,
    UNIQUE (material_code, prop_key)
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_material_price (
    price_id        bigserial PRIMARY KEY,
    material_code   text NOT NULL REFERENCES {{schema}}.kb_material(material_code) ON DELETE CASCADE,
    price           double precision NOT NULL,
    currency        text NOT NULL DEFAULT 'CNY',
    unit            text NOT NULL DEFAULT 'kg',
    price_type      text NOT NULL DEFAULT 'market' CHECK (price_type IN (
                        'internal_purchase', 'contract', 'market', 'ai_web')),
    valid_from      text NOT NULL,
    valid_to        text,                              -- NULL = 当前有效
    supplier_id     text REFERENCES {{schema}}.kb_supplier(supplier_id) ON DELETE SET NULL,
    source_name     text,
    source_url      text,
    evidence        text,
    confidence      double precision NOT NULL DEFAULT 1.0,         -- AI 检索价须标注
    created_by      text,
    created_at      text
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_supplier (
    supplier_id     text PRIMARY KEY,
    name            text NOT NULL,
    supplier_type   text,                              -- 原材料/外协/外购件
    region          text,
    qualification   text,
    rating          double precision,
    contact         text,
    status          text NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'blacklist', 'inactive')),
    note            text,
    updated_at      text,
    industry            text   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_supplier_capability (
    cap_id          bigserial PRIMARY KEY,
    supplier_id     text NOT NULL REFERENCES {{schema}}.kb_supplier(supplier_id) ON DELETE CASCADE,
    material_code   text REFERENCES {{schema}}.kb_material(material_code) ON DELETE SET NULL,
    material_name   text,                              -- 尚未入物料库时的自由文本
    max_purity_pct  double precision,
    d50_min_um      double precision,
    d50_max_um      double precision,
    moq             text,
    lead_time       text,
    price_ref       double precision,
    qualified       bigint NOT NULL DEFAULT 0 CHECK (qualified IN (0, 1)),
    note            text
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_cost_rate (
    rate_code       text PRIMARY KEY,                  -- RATE-LABOR-CNC
    name            text NOT NULL,
    rate_type       text NOT NULL CHECK (rate_type IN (
                        'labor', 'equipment_dep', 'energy', 'overhead',
                        'logistics', 'warehouse', 'packaging')),
    scope_type      text NOT NULL DEFAULT 'global' CHECK (scope_type IN (
                        'global', 'workshop', 'equipment_class', 'process_type')),
    scope_ref       text,
    value           double precision NOT NULL,
    unit            text NOT NULL,                     -- 元/小时、元/kWh、元/kg·天
    currency        text NOT NULL DEFAULT 'CNY',
    effective_from  text NOT NULL,
    effective_to    text,
    source          text,
    approved_by     text,
    note            text,
    industry            text,  -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
    minimum_charge  double precision   -- 最低收费（元）；NULL/0 = 不设门槛
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_cost_factor (
    factor_code     text PRIMARY KEY,
    name            text NOT NULL,
    factor_type     text NOT NULL CHECK (factor_type IN (
                        'yield', 'scrap', 'batch_amortize', 'margin', 'tax', 'fx', 'risk')),
    applicable_scope text,
    value           double precision NOT NULL,
    condition_expr  text,
    effective_from  text NOT NULL,
    effective_to    text,
    source          text,
    note            text,
    industry            text   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_packaging_box_type (
    box_type_code         text PRIMARY KEY,
    name                  text NOT NULL,
    name_en               text,
    family                text,
    size_l_min            double precision,
    size_l_max            double precision,
    size_w_min            double precision,
    size_w_max            double precision,
    size_h_min            double precision,
    size_h_max            double precision,
    fit_clearance         double precision,
    grey_board_thickness  text,
    face_paper_gsm        text,
    closure_type          text,
    part_count            bigint,
    v_groove              text,
    hand_mount_ratio      text,
    standard_seconds      double precision,
    automation_level      text,
    moq                   bigint,
    sample_lead_days      bigint,
    mass_lead_days        bigint,
    load_kg               double precision,
    standard_cost         double precision,
    standardization_level text,
    applicable_industries text,
    business_status       text,
    industry              text NOT NULL DEFAULT 'packaging',
    source_type         text NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          text,
    source_sha256       text,
    parser_version      text,
    confirmed_by        text,
    confirmed_at        text,
    source                text,
    version               text,
    effective_from        text,
    status                text NOT NULL DEFAULT 'active',
    created_at            text,
    updated_at            text
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_packaging_part_template (
    part_code          text PRIMARY KEY,
    box_type_code      text REFERENCES {{schema}}.kb_packaging_box_type(box_type_code) ON DELETE CASCADE,
    seq                bigint,
    name               text,
    component          text,
    material           text,
    quantity           bigint,
    size_expr          text,
    size_length_expr   text,
    size_width_expr    text,
    size_height_expr   text,
    sample_value       text,
    key_process        text,
    is_optional        bigint NOT NULL DEFAULT 0 CHECK (is_optional IN (0, 1)),
    note               text,
    industry           text NOT NULL DEFAULT 'packaging',
    source_type         text NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          text,
    source_sha256       text,
    parser_version      text,
    confirmed_by        text,
    confirmed_at        text,
    source             text,
    version            text,
    effective_from     text,
    status             text NOT NULL DEFAULT 'active',
    created_at         text,
    updated_at         text
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_packaging_process_template (
    box_type_code      text NOT NULL REFERENCES {{schema}}.kb_packaging_box_type(box_type_code) ON DELETE CASCADE,
    part_code          text NOT NULL,
    seq                bigint NOT NULL,
    step_name          text,
    workstation        text,
    work_content       text,
    standard_seconds   double precision,
    automation         text,
    control_point      text,
    parallel_ok        bigint,
    industry           text NOT NULL DEFAULT 'packaging',
    source_type         text NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          text,
    source_sha256       text,
    parser_version      text,
    confirmed_by        text,
    confirmed_at        text,
    source             text,
    version            text,
    effective_from     text,
    status             text NOT NULL DEFAULT 'active',
    created_at         text,
    updated_at         text,
    PRIMARY KEY (box_type_code, part_code, seq)
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_packaging_insert_accessory (
    accessory_code      text PRIMARY KEY,
    name                text NOT NULL,
    material            text,
    thickness_spec      text,
    forming             text,
    tooling_cost        double precision,
    unit_cost_min       double precision,
    unit_cost_max       double precision,
    eco_attr            text,
    applicable_category text,
    moq                 bigint,
    note                text,
    industry            text NOT NULL DEFAULT 'packaging',
    source_type         text NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          text,
    source_sha256       text,
    parser_version      text,
    confirmed_by        text,
    confirmed_at        text,
    source              text,
    version             text,
    effective_from      text,
    status              text NOT NULL DEFAULT 'active',
    created_at          text,
    updated_at          text
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_packaging_cost_formula (
    formula_code        text PRIMARY KEY,
    cost_category       text,
    process_code        text,
    rate_code           text,
    expression          text,
    minimum_charge      double precision,
    quantity_basis      text,
    amortization_basis  text,
    loss_scope          text,
    rounding            text,
    source_ref          text,
    formula_version     text,
    review_status       text NOT NULL DEFAULT 'draft'
                        CHECK (review_status IN ('draft', 'reviewed', 'retired')),
    industry            text NOT NULL DEFAULT 'packaging',
    source_type         text NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_sha256       text,
    parser_version      text,
    confirmed_by        text,
    confirmed_at        text,
    source              text,
    version             text,
    effective_from      text,
    status              text NOT NULL DEFAULT 'active',
    created_at          text,
    updated_at          text
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_packaging_logistics_rule (
    rule_code          text PRIMARY KEY,
    units_per_carton   bigint,
    carton_size        text,
    pallet_qty         bigint,
    units_per_pallet   bigint,
    shipping_mode      text,
    min_freight        double precision,
    loading_rate       text,
    quantity_tier      text,
    refund_condition   text,
    note               text,
    industry           text NOT NULL DEFAULT 'packaging',
    source_type         text NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          text,
    source_sha256       text,
    parser_version      text,
    confirmed_by        text,
    confirmed_at        text,
    source             text,
    version            text,
    effective_from     text,
    status             text NOT NULL DEFAULT 'active',
    created_at         text,
    updated_at         text
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_packaging_match_weight (
    dimension          text PRIMARY KEY
                       CHECK (dimension IN ('size_range', 'fit_clearance', 'face_paper_gsm',
                                            'closure_type', 'v_groove')),
    weight             double precision,
    hard_gate          bigint,
    rule_expr          text,
    industry           text NOT NULL DEFAULT 'packaging',
    source_type         text NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          text,
    source_sha256       text,
    parser_version      text,
    confirmed_by        text,
    confirmed_at        text,
    source             text,
    version            text,
    effective_from     text,
    status             text NOT NULL DEFAULT 'active',
    created_at         text,
    updated_at         text
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_packaging_cost_content (
    content_code     text PRIMARY KEY,
    name             text NOT NULL,
    category         text,                 -- 纸箱/平卡/隔卡/胶袋/护角/卡板/标签…
    material_spec    text,
    length_mm        double precision,
    width_mm         double precision,
    height_mm        double precision,
    gsm              double precision,
    usage_qty        double precision,                 -- 用量
    material_price   double precision,                 -- 材料单价
    units_per_pack   double precision,                 -- 装数
    formula_code     text,                 -- FORMULA_CATALOG 的 PKG-P-*
    industry         text NOT NULL DEFAULT 'packaging',
    source_type         text NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          text,
    source_sha256       text,
    parser_version      text,
    confirmed_by        text,
    confirmed_at        text,
    source           text, version text, effective_from text,
    status           text NOT NULL DEFAULT 'active',
    note             text, created_at text, updated_at text
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_packaging_tooling_rule (
    tooling_code     text PRIMARY KEY,
    name             text NOT NULL,
    process_code     text,                 -- 烫金/丝印/击凹凸/模切/装配线
    mode             text NOT NULL CHECK (mode IN (
                         'one_off', 'lifetime', 'committed', 'refund', 'customer_supplied')),
    tooling_cost     double precision,
    tooling_lifetime double precision,
    refund_threshold double precision,
    refundable       bigint NOT NULL DEFAULT 0 CHECK (refundable IN (0, 1)),
    industry         text NOT NULL DEFAULT 'packaging',
    source_type         text NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          text,
    source_sha256       text,
    parser_version      text,
    confirmed_by        text,
    confirmed_at        text,
    source           text, version text, effective_from text,
    status           text NOT NULL DEFAULT 'active',
    note             text, created_at text, updated_at text
);""",
    f"""CREATE TABLE IF NOT EXISTS {{schema}}.kb_quick_quote_config (
    key              text PRIMARY KEY,      -- 配置块名，如 quick_quote
    value_json       text,                  -- 该块的 JSON（缺失键由调用方按默认值补齐）
    version          text,
    updated_at       text
);"""]

_KB_META_DDL = (
    "CREATE TABLE IF NOT EXISTS {schema}.kb_meta ("
    " singleton boolean PRIMARY KEY DEFAULT true,"
    " kb_version bigint NOT NULL DEFAULT 0,"
    " updated_at timestamptz DEFAULT now())"
)
# 增量列：老库上 CREATE TABLE IF NOT EXISTS 不会补列，必须显式 ALTER（幂等）。
# 包装第 3 批：11 张行业主体表补 industry（空 = 通用，三行业默认行为不变），
# 费率库另补 minimum_charge（包装费率的最低收费）。
# 第 2 批（上线闭环）：9 张包装表补数据来源分层列（老库上 CREATE TABLE IF NOT EXISTS 不补列）。
# 与 da_db._ADDED_COLUMNS 一一对应；两侧老库补列口径必须一致。
_PACKAGING_PROVENANCE_COLUMNS: tuple = (
    ("source_type", "text NOT NULL DEFAULT 'unknown'"),
    ("source_ref", "text"),
    # 权威出处（升格时按 C2 写入，如 `礼盒盒型库_数据样例.xlsx#01盒型@sha12`）。
    # 没有它，`kb_deploy_preflight` 就无法区分"业务确认过的权威行"与"没人认领的 demo 行"。
    ("authority_ref", "text"),
    ("source_sha256", "text"),
    ("parser_version", "text"),
    ("confirmed_by", "text"),
    ("confirmed_at", "text"),
)
#: 9 张包装扩展表 —— 数据分层的适用范围。`kb_deploy_preflight` 的 provenance /
#: unclassified_rows 只统计这些表；其余 kb_* 表没有 source_type 列，不得因此判 unknown。
#: 升格只允许这一条：demo（样例工作簿固化的演示行）→ workbook（业务确认过的权威来源）。
#: 反方向、跨级、以及 unknown / dwg_confirmed 一律拒绝 —— 权威只能由业务给，不能由代码猜。
_PROMOTION_TARGETS = {"workbook": "demo"}

#: 出处必须齐备，少一项就拒绝升格（宁可停下来问，也不要写一个没主的数据）。
_PROMOTION_AUTHORITY_FIELDS = ("workbook", "sheet", "owner", "decided_at", "sha256")


def authority_ref(authority) -> str:
    """把出处压成一行可回查的引用：`工作簿#工作表@校验和前 12 位`。"""
    return "%s#%s@%s" % (str((authority or {}).get("workbook") or "").strip(),
                         str((authority or {}).get("sheet") or "").strip(),
                         str((authority or {}).get("sha256") or "").strip()[:12])


def promote_rows(rows, *, target: str = "workbook", authority) -> list:
    """把样例行升格为权威行（Spec `kb-authoritative-promotion-and-load.md` §3 C2）。

    · 只允许 `demo → workbook`；已是 `workbook` 幂等；`unknown` / 其它值抛 `ValueError`；
    · `authority` 必须齐备（workbook/sheet/owner/decided_at/sha256），缺一项抛 `ValueError`；
    · **只碰来源列**（`source_type` / `authority_ref`），业务值（尺寸区间、工时、费率、
      公式…）逐字不动；
    · 一旦是权威，出处保留原值 —— 后来的 seed 覆盖业务列可以，但不得把出处改回"没主"。

    纯函数：不写库、不联网、不改入参（返回新行）。
    """
    target = str(target or "").strip()
    source_type = _PROMOTION_TARGETS.get(target)
    if source_type is None:
        raise ValueError("不支持的升格目标：%r（只允许 demo → workbook）" % (target,))
    if not isinstance(authority, dict):
        raise ValueError("升格必须提供 authority（workbook/sheet/owner/decided_at/sha256）")
    missing = [name for name in _PROMOTION_AUTHORITY_FIELDS
               if not str(authority.get(name) or "").strip()]
    if missing:
        raise ValueError("authority 缺少字段：%s" % "、".join(missing))
    ref = authority_ref(authority)
    promoted = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        current = str(row.get("source_type") or "").strip().lower()
        out = dict(row)
        if current == source_type:
            out["source_type"] = target
            out["authority_ref"] = ref
        elif current == target:
            if not str(out.get("authority_ref") or "").strip():
                out["authority_ref"] = ref
        else:
            raise ValueError("不能升格 source_type=%r 的行（只允许 %s → %s）"
                             % (current or "unknown", source_type, target))
        promoted.append(out)
    return promoted


PACKAGING_TABLES: tuple = (
    "kb_packaging_box_type", "kb_packaging_part_template", "kb_packaging_process_template",
    "kb_packaging_insert_accessory", "kb_packaging_cost_formula",
    "kb_packaging_logistics_rule", "kb_packaging_match_weight",
    "kb_packaging_cost_content", "kb_packaging_tooling_rule",
)
#: 关键主体表：上线预检要求这几张非空（缺行 = 知识库没灌进去）。
PACKAGING_REQUIRED_TABLES: tuple = PACKAGING_TABLES[:7]

_PACKAGING_PROVENANCE_TABLES: tuple = PACKAGING_TABLES

_ADDED_COLUMNS: tuple = (
    ("kb_component", "industry", "text"),
    ("kb_standard_part", "industry", "text"),
    ("kb_equipment_class", "industry", "text"),
    ("kb_equipment", "industry", "text"),
    ("kb_process_step", "industry", "text"),
    ("kb_process_route", "industry", "text"),
    ("kb_inspection_item", "industry", "text"),
    ("kb_material", "industry", "text"),
    ("kb_supplier", "industry", "text"),
    ("kb_cost_rate", "industry", "text"),
    ("kb_cost_factor", "industry", "text"),
    ("kb_cost_rate", "minimum_charge", "double precision"),
)


def _q(name: str) -> str:
    return '"%s"' % str(name)


# --------------------------------------------------------------------------- #
# 建表/写入顺序：外键父表在前
# --------------------------------------------------------------------------- #
_TABLE_NAME_RE = re.compile(r"CREATE TABLE IF NOT EXISTS \{schema\}\.(\w+)")
_REFERENCE_RE = re.compile(r"REFERENCES \{schema\}\.(\w+)")


def _statement_name(statement: str) -> str:
    return _TABLE_NAME_RE.search(statement).group(1)

# 包装分层列：9 张包装表逐列补（老库上 CREATE TABLE IF NOT EXISTS 不补列）。
_ADDED_COLUMNS = tuple(_ADDED_COLUMNS) + tuple(
    (table, column, definition)
    for table in _PACKAGING_PROVENANCE_TABLES
    for column, definition in _PACKAGING_PROVENANCE_COLUMNS)


def _build_create_order(statements: list) -> tuple:
    """按外键依赖排序（Kahn，同层保持 da_schema.sql 的原序）。

    SQLite 里 REFERENCES 只是注释，建表/写入顺序随便；Postgres 是真的约束：
    被引用表必须先存在、父行必须先落库 —— 否则 `kb_component` 的
    `default_material_code` 一插就炸 "relation kb_material does not exist"。
    """
    names = [_statement_name(statement) for statement in statements]
    deps = {name: tuple(dict.fromkeys(_REFERENCE_RE.findall(statement)))
            for name, statement in zip(names, statements)}
    ordered: list = []
    pending = list(names)
    while pending:
        ready = [name for name in pending if all(d in ordered for d in deps[name])]
        if not ready:                                       # pragma: no cover - 表结构有环
            raise RuntimeError("kb_* 表的外键出现环：%s" % pending)
        for name in ready:
            ordered.append(name)
            pending.remove(name)
    return tuple(ordered)


# --------------------------------------------------------------------------- #
# 连接
# --------------------------------------------------------------------------- #
def _connect():
    """连到业务库并把 search_path 指到知识库 schema（autocommit）。"""
    try:
        import psycopg
    except ImportError as exc:                              # pragma: no cover - 部署缺驱动
        raise KbUnavailable(
            "未安装 psycopg 驱动，无法访问线上 Postgres："
            "pip install psycopg[binary]==3.3.4") from exc
    try:
        conn = psycopg.connect(
            host=cpq_db.PG_HOST, port=cpq_db.PG_PORT, user=cpq_db.PG_USER,
            password=cpq_db.PG_PASSWORD, dbname=cpq_db.PG_DATABASE,
            connect_timeout=cpq_db.PG_CONNECT_TIMEOUT, autocommit=True,
        )
    except Exception as exc:
        raise KbUnavailable(
            "连接 Postgres %s:%s/%s 失败：%s" % (
                cpq_db.PG_HOST, cpq_db.PG_PORT, cpq_db.PG_DATABASE,
                str(exc).splitlines()[0][:160])) from exc
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('search_path', %s, false)",
                        ("%s, public" % SCHEMA,))
    except Exception as exc:                                # pragma: no cover
        conn.close()
        raise KbUnavailable("设置 search_path=%s 失败：%s" % (SCHEMA, exc)) from exc
    return conn


# 建表与导入的顺序都由外键依赖决定（父表在前）；对外暴露的表序仍是 KB_TABLES。
CREATE_ORDER = _build_create_order(list(_DDL_TEMPLATE))


def _table_ddl() -> list:
    """20 张 kb_* 表的建表语句（schema 已代入），按外键依赖排序。"""
    by_name = {_statement_name(statement): statement for statement in _DDL_TEMPLATE}
    return [by_name[name].format(schema=SCHEMA) for name in CREATE_ORDER]


def _ensure_schema(cur) -> None:
    cur.execute("CREATE SCHEMA IF NOT EXISTS %s" % SCHEMA)
    for statement in _table_ddl():
        cur.execute(statement)
    cur.execute(_KB_META_DDL.format(schema=SCHEMA))
    for table, column, definition in _ADDED_COLUMNS:
        cur.execute("ALTER TABLE %s.%s ADD COLUMN IF NOT EXISTS %s %s"
                    % (SCHEMA, table, column, definition))
    cur.execute("INSERT INTO %s.kb_meta (singleton, kb_version) VALUES (true, 0)"
                " ON CONFLICT (singleton) DO NOTHING" % SCHEMA)


def _rows(cur) -> list:
    cols = [d[0] for d in (cur.description or [])]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #
def ensure_schema(conn=None) -> None:
    """建 schema / 20 张 kb_* 表 / kb_meta / 增量列。幂等，可反复执行。"""
    own = conn is None
    conn = conn or _connect()
    try:
        _ensure_schema(conn.cursor())
    except KbUnavailable:
        raise
    except Exception as exc:
        raise KbUnavailable("初始化 %s schema 失败：%s" % (SCHEMA, str(exc)[:200])) from exc
    finally:
        if own:
            conn.close()


def _require_schema(cur) -> None:
    """表缺失要说清楚是"schema 没建"，而不是让调用方看到空表。"""
    cur.execute("SELECT to_regclass(%s)", ("%s.kb_component" % SCHEMA,))
    row = cur.fetchone()
    if not row or row[0] is None:
        raise KbUnavailable(
            "知识库 schema %s 不存在或未建表：请先在 CPQ 侧执行 ensure_schema()"
            "（或跑 scripts/import_da_kb_to_pg.py --confirm）" % SCHEMA)


# --------------------------------------------------------------------------- #
# 版本号与快照
# --------------------------------------------------------------------------- #
def kb_version() -> int:
    """当前 `kb_version`。还没有 kb_meta 记录时按 0 处理；连不上则抛 KbUnavailable。"""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT kb_version FROM %s.kb_meta WHERE singleton" % SCHEMA)
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0
    except KbUnavailable:
        raise
    except Exception as exc:
        raise KbUnavailable("读取 kb_version 失败（schema=%s）：%s"
                            % (SCHEMA, str(exc).splitlines()[0][:160])) from exc
    finally:
        conn.close()


def snapshot(since: Optional[Any] = None) -> dict:
    """整包快照：`{"kb_version", "unchanged", "tables"}`。

    `since` 等于当前版本时只回 `unchanged=True` 且 `tables` 为空（省流量）；
    其余情况回**全部 20 张表**（含空表 —— 表缺失意味着 schema 坏了，要报错，而不是
    悄悄少一张）。任何失败都抛 KbUnavailable，绝不回空表。
    """
    version = kb_version()
    if since is not None and str(since).strip() != "":
        try:
            if int(str(since).strip()) == version:
                return {"kb_version": version, "unchanged": True, "tables": {}}
        except (TypeError, ValueError):
            pass                                            # since 不是数字：按"要全量"处理
    conn = _connect()
    try:
        cur = conn.cursor()
        _require_schema(cur)
        tables: dict = {}
        for name in KB_TABLES:
            order = ", ".join(_q(col) for col in KB_KEYS.get(name, ())) or "1"
            cur.execute("SELECT * FROM %s.%s ORDER BY %s" % (SCHEMA, name, order))
            tables[name] = _rows(cur)
        return {"kb_version": version, "unchanged": False, "tables": tables}
    except KbUnavailable:
        raise
    except Exception as exc:
        raise KbUnavailable("读取知识库快照失败（schema=%s）：%s"
                            % (SCHEMA, str(exc).splitlines()[0][:160])) from exc
    finally:
        conn.close()


def export_snapshot(path=None) -> dict:
    """导入前留底：把当前整包快照（含 `kb_version`）导成 JSON，用于回滚与差异比对。

    **只读**：不写库、不动 `kb_version`（`snapshot()` 本身也只读）；`path` 为空时
    只返回 dict、不落盘。DEPLOYMENT.md 的「知识库（cpq_kb）上线」小节是它的使用口径：
    先导出留底 → 再 `--confirm` 导入，出问题就按这份 JSON 恢复。
    """
    snap = snapshot()
    payload = {"export_version": "kb-snapshot-export/1",
               "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "schema": SCHEMA,
               "kb_version": snap.get("kb_version"),
               "tables": snap.get("tables") or {}}
    if path:
        target = pathlib.Path(path).expanduser()
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    return payload


# --------------------------------------------------------------------------- #
# 导入：da.db（SQLite）-> cpq_kb（Postgres）
# --------------------------------------------------------------------------- #
def _read_source(path) -> tuple:
    """只读打开源库并取出全部 kb_* 行。

    `mode=ro` 是硬要求：以可写方式打开 WAL 库，进程一关闭就会把 -wal 合并进主体并
    删掉 -wal/-shm —— 导入工具绝不能改别人的库。只读连接可能顺手建一个空的
    `-shm`/`-wal` 边车文件（SQLite 读 WAL 库需要共享内存），但源库内容与 mtime 不会
    被改动，已有的 -wal/-shm 也不会被删除或截断（只读连接没有写权限）。
    """
    target = pathlib.Path(path).expanduser()
    if not target.exists():
        raise FileNotFoundError("源库不存在：%s" % target)
    uri = "file:%s?mode=ro" % target.as_posix()
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        counts = {}
        rows = {}
        for name in KB_TABLES:
            try:
                got = [dict(r) for r in conn.execute("SELECT * FROM %s" % name).fetchall()]
            except sqlite3.Error:                           # 源库缺这张表 -> 0 行
                got = []
            rows[name] = got
            counts[name] = len(got)
        return rows, counts
    finally:
        conn.close()


def _upsert_rows(cur, table: str, rows: list) -> int:
    """按主键 upsert，返回"确有变化"的行数（决定 kb_version 要不要 +1）。"""
    if not rows:
        return 0
    keys = KB_KEYS.get(table) or ()
    changed = 0
    for row in rows:
        cols = [c for c in row if row[c] is not None or c in keys]
        if not cols:
            continue
        collist = ", ".join(_q(c) for c in cols)
        placeholders = ", ".join(["%s"] * len(cols))
        # `INSERT INTO ... AS x` 给目标表起别名，是为了在 DO UPDATE 的 WHERE 里能同时
        # 看到"库里的旧行"与"这次来的新行"——只有真的不一样才更新，cur.rowcount 才有用
        # （kb_version 只在确有变化时 +1）。VALUES ... AS alias 那种写法 PG 18 才支持，
        # 目标表别名从 9.5 起就有，别换回去。
        sql = ("INSERT INTO %s.%s AS x (%s) VALUES (%s)"
               % (SCHEMA, table, collist, placeholders))
        updates = [c for c in cols if c not in keys]
        if updates and keys:
            sets = ", ".join("%s = EXCLUDED.%s" % (_q(c), _q(c)) for c in updates)
            compare = ", ".join("x.%s" % _q(c) for c in cols)
            incoming = ", ".join("EXCLUDED.%s" % _q(c) for c in cols)
            sql += (" ON CONFLICT (%s) DO UPDATE SET %s"
                    " WHERE (%s) IS DISTINCT FROM (%s)"
                    % (", ".join(_q(k) for k in keys), sets, compare, incoming))
        elif keys:
            sql += " ON CONFLICT (%s) DO NOTHING" % ", ".join(_q(k) for k in keys)
        cur.execute(sql, [row[c] for c in cols])
        if cur.rowcount and cur.rowcount > 0:
            changed += int(cur.rowcount)
    return changed


def _sync_serial(cur, table: str, rows: list) -> None:
    """serial 主键显式插值后把序列推到最大值，后续 insert 才不会主键冲突。"""
    keys = KB_KEYS.get(table) or ()
    if len(keys) != 1 or not rows:
        return
    column = keys[0]
    if any(row.get(column) is None for row in rows):
        return
    cur.execute("SELECT pg_get_serial_sequence(%s, %s)",
                ("%s.%s" % (SCHEMA, table), column))
    seq = cur.fetchone()
    if not seq or not seq[0]:
        return
    cur.execute("SELECT setval(%s, (SELECT COALESCE(MAX(%s), 0) FROM %s.%s), true)"
                % ("%s", _q(column), SCHEMA, table), (seq[0],))


def _bump_version(cur, changed: int) -> int:
    """有变化才 +1；返回操作后的版本号。"""
    cur.execute("SELECT kb_version FROM %s.kb_meta WHERE singleton FOR UPDATE" % SCHEMA)
    row = cur.fetchone()
    version = int(row[0] or 0) if row else 0
    if changed <= 0:
        return version
    version += 1
    if row:
        cur.execute("UPDATE %s.kb_meta SET kb_version = %%s, updated_at = now()"
                    " WHERE singleton" % SCHEMA, (version,))
    else:
        cur.execute("INSERT INTO %s.kb_meta (singleton, kb_version) VALUES (true, %%s)"
                    % SCHEMA, (version,))
    return version


def _packaging_source_distribution(rows: dict) -> dict:
    """按表统计 source_type 分布（只统计 9 张包装表；其余 kb_* 没有这一列）。"""
    out = {}
    for name in PACKAGING_TABLES:
        bucket = {}
        for row in rows.get(name) or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("source_type") or "").strip().lower() or "unknown"
            bucket[code] = bucket.get(code, 0) + 1
        out[name] = bucket
    return out


def _authority_for_table(authority, table: str) -> dict:
    """从出处文件里取某张表的 authority（`defaults` 打底，同名条目覆盖）。"""
    if not isinstance(authority, dict):
        raise ValueError("升格必须提供出处文件（workbook/sheet/owner/decided_at/sha256）")
    merged = dict(authority.get("defaults") or {})
    for item in authority.get("sources") or []:
        if isinstance(item, dict) and str(item.get("table") or "") == table:
            for key in _PROMOTION_AUTHORITY_FIELDS:
                if str(item.get(key) or "").strip():
                    merged[key] = item.get(key)
            break
    return merged


def _promote_packaging_rows(rows: dict, *, target: str, authority) -> dict:
    """只对 9 张包装表做升格；其它 kb_* 表没有来源列，本就不在分层范围内。

    出处不齐就**停在这一张表上**并把表名说清楚：宁可不灌，也不要把没主的数据
    写进生产库（剩下的 demo 行会被 `kb_deploy_preflight` 的 `authority_missing`
    拦下 —— 这里先拦，省一次往返）。
    """
    out = dict(rows)
    for name in PACKAGING_TABLES:
        got = rows.get(name)
        if not got:
            continue
        try:
            out[name] = promote_rows(got, target=target,
                                     authority=_authority_for_table(authority, name))
        except ValueError as exc:
            raise ValueError(
                "%s 无法升格：%s。请在出处文件里补齐这张表的 "
                "workbook/sheet/owner/decided_at/sha256（缺哪项补哪项，"
                "出处不明的行宁可留在 demo，由业务确认后再灌）" % (name, exc)) from None
    return out


def import_from_sqlite(path, *, confirm: bool = False, promote: str = "",
                       authority=None) -> dict:
    """把 da.db 的 kb_* 全表导入 cpq_kb。

    默认 dry-run：只统计不写库、**不连 PG**（"看一眼要搬多少行"不该因为连不上库而失败）；
    `--confirm` 才真写：单事务、按主键 upsert（幂等），只在确有变化时递增 kb_version。
    `promote="workbook"` + `authority=<出处文件内容>`：灌库前按 C2 把 demo 行升格为权威，
    出处逐表取自 C3 的申报文件（dry-run 也照升，方便先看计划）。
    """
    rows, counts = _read_source(path)
    if promote:
        rows = _promote_packaging_rows(rows, target=promote, authority=authority)
    source_types = _packaging_source_distribution(rows)
    total = sum(counts.values())
    if not confirm:
        # 不连库：dry-run 的职责是出计划。kb_version 只在真写时才谈得上。
        return {"ok": True, "dry_run": True, "kb_version": 0,
                "tables": counts, "rows": total, "promote": str(promote or ""),
                "source_types": source_types}

    conn = _connect()
    try:
        with conn.transaction():                            # 单事务：失败整体回滚
            cur = conn.cursor()
            _ensure_schema(cur)
            changed = 0
            for name in CREATE_ORDER:                       # 父表先写，外键才成立
                got = rows.get(name) or []
                changed += _upsert_rows(cur, name, got)
                _sync_serial(cur, name, got)
            version = _bump_version(cur, changed)
    except KbUnavailable:
        raise
    except Exception as exc:
        raise KbUnavailable("导入 %s 失败（已回滚）：%s"
                            % (SCHEMA, str(exc).splitlines()[0][:200])) from exc
    finally:
        conn.close()
    return {"ok": True, "dry_run": False, "kb_version": version,
            "tables": counts, "rows": total, "changed": changed,
            "promote": str(promote or ""), "source_types": source_types}

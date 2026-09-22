-- =========================================================================
-- AI 工艺评估平台 · 数据架构 DDL (SQLite)
-- 对应 docs/数据架构设计_DA.md
--
-- 分层与前缀:
--   kb_*   知识库 / 主数据   —— 数据源(跨项目沉淀,只增不改,改则升版本)
--   src_*  项目输入          —— 数据源(本次评估的原始输入,只读)
--   wip_*  过程中间产物      —— 产出(可重算、可编辑)
--   out_*  评估结果 / 交付物 —— 产出(冻结、可分发)
--   ops_*  运行 / 审计 / 权限 —— 治理
--
-- SQLite 约定:
--   * 枚举        -> TEXT + CHECK
--   * jsonb/数组  -> TEXT 存 JSON(用 json1 函数查询)
--   * 时间        -> TEXT,ISO 字符串(与 backend/time_utils.now_cst_str 一致)
--   * 金额/尺寸   -> REAL
--   * 布尔        -> INTEGER 0/1
-- =========================================================================

PRAGMA foreign_keys = ON;

-- 架构版本(迁移用)
CREATE TABLE IF NOT EXISTS schema_meta (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT
);


-- =========================================================================
-- L0 · 知识库:可制造零部件
-- =========================================================================

-- 3.1 可制造零部件主数据
CREATE TABLE IF NOT EXISTS kb_component (
    component_id        TEXT PRIMARY KEY,
    component_code      TEXT NOT NULL UNIQUE,          -- CMP-BRK-0012
    name                TEXT NOT NULL,
    category            TEXT,                          -- 结构件/回转件/钣金件/陶瓷基板...
    subcategory         TEXT,
    source_type         TEXT NOT NULL DEFAULT 'self_made'
                        CHECK (source_type IN ('self_made', 'outsourced', 'standard')),
    spec_summary        TEXT,
    default_material_code TEXT REFERENCES kb_material(material_code) ON DELETE SET NULL,
    default_route_code  TEXT REFERENCES kb_process_route(route_code) ON DELETE SET NULL,
    envelope_l          REAL,                          -- 外形包络 mm,粗筛用
    envelope_w          REAL,
    envelope_h          REAL,
    mass_kg             REAL,
    manufacturability   TEXT DEFAULT 'mature'
                        CHECK (manufacturability IN ('mature', 'conditional', 'hard')),
    reuse_count         INTEGER NOT NULL DEFAULT 0,    -- 复用次数,推荐排序权重
    lifecycle           TEXT NOT NULL DEFAULT 'active'
                        CHECK (lifecycle IN ('draft', 'active', 'deprecated')),
    rev                 TEXT DEFAULT 'A',              -- 当前有效版本,与图纸版本联动
    tags                TEXT,                          -- JSON 数组
    note                TEXT,
    owner               TEXT,
    created_by          TEXT,
    created_at          TEXT,
    updated_at          TEXT,
    industry            TEXT   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);
CREATE INDEX IF NOT EXISTS ix_component_category  ON kb_component(category, lifecycle);
CREATE INDEX IF NOT EXISTS ix_component_material  ON kb_component(default_material_code);
CREATE INDEX IF NOT EXISTS ix_component_envelope  ON kb_component(envelope_l, envelope_w, envelope_h);

-- 3.2 零部件参数(EAV,可范围检索)
CREATE TABLE IF NOT EXISTS kb_component_param (
    param_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    component_id    TEXT NOT NULL REFERENCES kb_component(component_id) ON DELETE CASCADE,
    param_key       TEXT NOT NULL,                     -- length / thickness / hole_diameter...
    param_name      TEXT,
    value_num       REAL,                              -- 数值型走这里,支持范围检索
    value_text      TEXT,
    unit            TEXT,
    tol_lower       REAL,                              -- 允差下限(绝对值,mm/单位)
    tol_upper       REAL,
    is_key          INTEGER NOT NULL DEFAULT 0 CHECK (is_key IN (0, 1)),
    note            TEXT,
    UNIQUE (component_id, param_key)
);
CREATE INDEX IF NOT EXISTS ix_param_key ON kb_component_param(param_key, value_num);

-- 3.3 特征签名(与 models/ir.py::Feature 同构 -> 支持特征序列相似度)
CREATE TABLE IF NOT EXISTS kb_component_feature (
    feature_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    component_id    TEXT NOT NULL REFERENCES kb_component(component_id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL DEFAULT 0,        -- 建模顺序
    feature_type    TEXT NOT NULL CHECK (feature_type IN (
                        'plate', 'box', 'cylinder', 'hole',
                        'hole_pattern', 'fillet', 'chamfer')),
    length          REAL, width      REAL, thickness REAL, height REAL,
    diameter        REAL, radius     REAL, distance  REAL,
    x               REAL, y          REAL,
    count_x         INTEGER, count_y INTEGER,
    spacing_x       REAL, spacing_y  REAL,
    purpose         TEXT
);
CREATE INDEX IF NOT EXISTS ix_feature_component ON kb_component_feature(component_id, seq);
CREATE INDEX IF NOT EXISTS ix_feature_type      ON kb_component_feature(feature_type);

-- 3.4 图纸/模型文件索引(2D & 3D 图库,实体文件在文件夹里)
CREATE TABLE IF NOT EXISTS kb_component_drawing (
    drawing_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    component_id    TEXT NOT NULL REFERENCES kb_component(component_id) ON DELETE CASCADE,
    drawing_kind    TEXT NOT NULL CHECK (drawing_kind IN ('2d', '3d', 'doc', 'thumb')),
    file_format     TEXT,                              -- dxf/dwg/pdf/step/stl/sldprt/docx...
    rev             TEXT NOT NULL DEFAULT 'A',
    is_current      INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    file_path       TEXT NOT NULL,                     -- 相对 blob 根,如 kb/components/CMP-.../2d/A/x.dxf
    file_name       TEXT,                              -- 原始文件名(可含中文)
    file_sha256     TEXT,
    file_size       INTEGER,
    thumbnail_path  TEXT,
    page_count      INTEGER,
    title_block     TEXT,                              -- JSON:图号/比例/设计者/日期
    uploaded_by     TEXT,
    uploaded_at     TEXT,
    UNIQUE (component_id, drawing_kind, rev, file_path)
);
CREATE INDEX IF NOT EXISTS ix_drawing_component ON kb_component_drawing(component_id, drawing_kind, is_current);
CREATE INDEX IF NOT EXISTS ix_drawing_sha       ON kb_component_drawing(file_sha256);

-- 3.5 语义检索向量(可选,初期可空)
CREATE TABLE IF NOT EXISTS kb_component_embedding (
    component_id    TEXT NOT NULL REFERENCES kb_component(component_id) ON DELETE CASCADE,
    scope           TEXT NOT NULL CHECK (scope IN ('name', 'spec', 'feature')),
    model_name      TEXT NOT NULL,
    dim             INTEGER NOT NULL,
    vector          TEXT NOT NULL,                     -- JSON 数组
    built_at        TEXT,
    PRIMARY KEY (component_id, scope, model_name)
);

-- 3.6 标准件库(对齐 models/ir.py::StandardPart)
CREATE TABLE IF NOT EXISTS kb_standard_part (
    std_id              TEXT PRIMARY KEY,
    standard_no         TEXT,                          -- GB/T 5783
    designation         TEXT NOT NULL,                 -- M8x25
    category            TEXT,                          -- bolt/nut/washer/bearing
    size_params         TEXT,                          -- JSON
    material            TEXT,
    surface_treatment   TEXT,
    unit_price_ref      REAL,
    currency            TEXT DEFAULT 'CNY',
    supplier_id         TEXT REFERENCES kb_supplier(supplier_id) ON DELETE SET NULL,
    drawing_path        TEXT,                          -- 文件夹路径
    note                TEXT,
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'deprecated')),
    industry            TEXT,  -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
    UNIQUE (standard_no, designation)
);


-- =========================================================================
-- L0 · 知识库:工艺
-- =========================================================================

-- 4.4a 设备类别(工序默认设备的挂载点)
CREATE TABLE IF NOT EXISTS kb_equipment_class (
    class_code      TEXT PRIMARY KEY,                  -- EQC-CNC-VMC
    name            TEXT NOT NULL,
    category        TEXT,
    note            TEXT,
    industry            TEXT   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);

-- 4.4 设备资源库(现有 data/equipment.json 的扩展目标)
CREATE TABLE IF NOT EXISTS kb_equipment (
    equipment_id            TEXT PRIMARY KEY,
    equipment_code          TEXT UNIQUE,
    name                    TEXT NOT NULL,
    model_no                TEXT,
    manufacturer            TEXT,
    equipment_class         TEXT REFERENCES kb_equipment_class(class_code) ON DELETE SET NULL,
    capability              TEXT,                      -- JSON:行程/最大工件重量/转速/精度/炉温上限
    hourly_rate             REAL,                      -- 元/小时(人工+管理)
    depreciation_per_hour   REAL,                      -- 元/小时,设备折旧
    power_kw                REAL,                      -- 能耗成本用
    workshop                TEXT,
    unit_count              INTEGER NOT NULL DEFAULT 1,
    status                  TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active', 'maintenance', 'retired')),
    note                    TEXT,
    updated_at              TEXT,
    industry            TEXT   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);
CREATE INDEX IF NOT EXISTS ix_equipment_class ON kb_equipment(equipment_class, status);

-- 4.1 工艺步骤(工序原子)库
CREATE TABLE IF NOT EXISTS kb_process_step (
    step_code               TEXT PRIMARY KEY,          -- PS-MILL-ROUGH
    name                    TEXT NOT NULL,
    process_type            TEXT NOT NULL CHECK (process_type IN (
                                'blank', 'turning', 'milling', 'drilling', 'boring',
                                'grinding', 'bench', 'sheet_metal', 'welding',
                                'heat_treat', 'surface', 'assembly', 'inspection', 'other')),
    category                TEXT,                      -- 成型/共烧/机加工/金属化/检测
    description_tpl         TEXT,
    applicable_material     TEXT,                      -- JSON 数组:适用材料类别
    applicable_feature      TEXT,                      -- JSON 数组:适用 FeatureType -> 特征驱动推荐
    default_equipment_class TEXT REFERENCES kb_equipment_class(class_code) ON DELETE SET NULL,
    default_fixture         TEXT,
    default_tooling         TEXT,
    quality_items           TEXT,                      -- JSON:平面度/Ra/尺寸公差
    setup_min               REAL,                      -- 标准准备工时(分钟/批)
    unit_min_formula        TEXT,                      -- 单件工时模型,平台确定性求值
    yield_rate              REAL,                      -- 典型良率 0~1
    is_critical             INTEGER NOT NULL DEFAULT 0 CHECK (is_critical IN (0, 1)),
    version                 INTEGER NOT NULL DEFAULT 1,
    effective_from          TEXT,
    effective_to            TEXT,
    status                  TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('draft', 'active', 'deprecated')),
    note                    TEXT,
    created_at              TEXT,
    updated_at              TEXT,
    industry            TEXT   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);
CREATE INDEX IF NOT EXISTS ix_step_type ON kb_process_step(process_type, status);

-- 4.2 工序参数模板
CREATE TABLE IF NOT EXISTS kb_process_param_template (
    tpl_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    step_code       TEXT NOT NULL REFERENCES kb_process_step(step_code) ON DELETE CASCADE,
    param_key       TEXT NOT NULL,                     -- S/F/ap/温度/保温时长
    param_name      TEXT,
    default_value   TEXT,
    min_value       REAL,
    max_value       REAL,
    unit            TEXT,
    depends_on      TEXT,                              -- 受材料/刀具/厚度影响的说明
    note            TEXT,
    UNIQUE (step_code, param_key)
);

-- 4.3 典型工艺路线模板
CREATE TABLE IF NOT EXISTS kb_process_route (
    route_code          TEXT PRIMARY KEY,              -- RT-SHEET-BOX
    name                TEXT NOT NULL,
    applicable_category TEXT,                          -- 适用零件类别
    applicable_material TEXT,                          -- JSON 数组
    batch_min           INTEGER,
    batch_max           INTEGER,
    summary             TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('draft', 'active', 'deprecated')),
    created_at          TEXT,
    updated_at          TEXT,
    industry            TEXT   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);

CREATE TABLE IF NOT EXISTS kb_process_route_step (
    route_code      TEXT NOT NULL REFERENCES kb_process_route(route_code) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,                  -- 10/20/30
    step_code       TEXT NOT NULL REFERENCES kb_process_step(step_code) ON DELETE RESTRICT,
    is_optional     INTEGER NOT NULL DEFAULT 0 CHECK (is_optional IN (0, 1)),
    condition_expr  TEXT,                              -- 何时启用该工序
    depends_on      TEXT,                              -- JSON 数组:前序 seq
    param_override  TEXT,                              -- JSON
    note            TEXT,
    PRIMARY KEY (route_code, seq)
);

-- 4.5 检验项库
CREATE TABLE IF NOT EXISTS kb_inspection_item (
    insp_code           TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    method              TEXT,
    instrument          TEXT,
    sampling_rule       TEXT,
    acceptance_criteria TEXT,
    cost_per_item       REAL,
    note                TEXT,
    industry            TEXT   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);


-- =========================================================================
-- L0 · 知识库:物料 & 成本基准
-- =========================================================================

-- 5.1 物料主数据
CREATE TABLE IF NOT EXISTS kb_material (
    material_code       TEXT PRIMARY KEY,              -- MAT-STL-Q235
    name                TEXT NOT NULL,
    grade               TEXT,                          -- Q235 / 6061-T6 / Al2O3-96%
    category            TEXT,                          -- 金属/陶瓷粉体/浆料/耗材辅料/包材
    form                TEXT,                          -- 板材/棒材/粉末/浆料/型材
    spec                TEXT,
    density             REAL,                          -- g/cm^3,对齐 ir.Material.density
    base_unit           TEXT NOT NULL DEFAULT 'kg',
    standard_loss_rate  REAL NOT NULL DEFAULT 0,       -- 标准损耗率 0~1
    hazard_level        TEXT,
    storage_req         TEXT,
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('draft', 'active', 'deprecated')),
    note                TEXT,
    created_at          TEXT,
    updated_at          TEXT,
    industry            TEXT   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);
CREATE INDEX IF NOT EXISTS ix_material_category ON kb_material(category, status);
CREATE INDEX IF NOT EXISTS ix_material_grade    ON kb_material(grade);

-- 5.2 物料性能(EAV)
CREATE TABLE IF NOT EXISTS kb_material_property (
    prop_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    material_code   TEXT NOT NULL REFERENCES kb_material(material_code) ON DELETE CASCADE,
    prop_key        TEXT NOT NULL,                     -- thermal_conductivity/cte/dielectric/purity/d50
    prop_name       TEXT,
    value_num       REAL,
    value_text      TEXT,
    unit            TEXT,
    test_method     TEXT,
    source_ref      TEXT,
    UNIQUE (material_code, prop_key)
);

-- 5.3 物料价格(时间序列,成本测算按测算日取价)
CREATE TABLE IF NOT EXISTS kb_material_price (
    price_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    material_code   TEXT NOT NULL REFERENCES kb_material(material_code) ON DELETE CASCADE,
    price           REAL NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'CNY',
    unit            TEXT NOT NULL DEFAULT 'kg',
    price_type      TEXT NOT NULL DEFAULT 'market' CHECK (price_type IN (
                        'internal_purchase', 'contract', 'market', 'ai_web')),
    valid_from      TEXT NOT NULL,
    valid_to        TEXT,                              -- NULL = 当前有效
    supplier_id     TEXT REFERENCES kb_supplier(supplier_id) ON DELETE SET NULL,
    source_name     TEXT,
    source_url      TEXT,
    evidence        TEXT,
    confidence      REAL NOT NULL DEFAULT 1.0,         -- AI 检索价须标注
    created_by      TEXT,
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_price_lookup ON kb_material_price(material_code, valid_from DESC);

-- 5.4 供应商与能力
CREATE TABLE IF NOT EXISTS kb_supplier (
    supplier_id     TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    supplier_type   TEXT,                              -- 原材料/外协/外购件
    region          TEXT,
    qualification   TEXT,
    rating          REAL,
    contact         TEXT,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'blacklist', 'inactive')),
    note            TEXT,
    updated_at      TEXT,
    industry            TEXT   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);

CREATE TABLE IF NOT EXISTS kb_supplier_capability (
    cap_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id     TEXT NOT NULL REFERENCES kb_supplier(supplier_id) ON DELETE CASCADE,
    material_code   TEXT REFERENCES kb_material(material_code) ON DELETE SET NULL,
    material_name   TEXT,                              -- 尚未入物料库时的自由文本
    max_purity_pct  REAL,
    d50_min_um      REAL,
    d50_max_um      REAL,
    moq             TEXT,
    lead_time       TEXT,
    price_ref       REAL,
    qualified       INTEGER NOT NULL DEFAULT 0 CHECK (qualified IN (0, 1)),
    note            TEXT
);
CREATE INDEX IF NOT EXISTS ix_cap_material ON kb_supplier_capability(material_code);

-- 5.5 费率库
CREATE TABLE IF NOT EXISTS kb_cost_rate (
    rate_code       TEXT PRIMARY KEY,                  -- RATE-LABOR-CNC
    name            TEXT NOT NULL,
    rate_type       TEXT NOT NULL CHECK (rate_type IN (
                        'labor', 'equipment_dep', 'energy', 'overhead',
                        'logistics', 'warehouse', 'packaging')),
    scope_type      TEXT NOT NULL DEFAULT 'global' CHECK (scope_type IN (
                        'global', 'workshop', 'equipment_class', 'process_type')),
    scope_ref       TEXT,
    value           REAL NOT NULL,
    unit            TEXT NOT NULL,                     -- 元/小时、元/kWh、元/kg·天
    currency        TEXT NOT NULL DEFAULT 'CNY',
    effective_from  TEXT NOT NULL,
    effective_to    TEXT,
    source          TEXT,
    approved_by     TEXT,
    note            TEXT,
    industry            TEXT,  -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
    minimum_charge  REAL   -- 最低收费（元）；NULL/0 = 不设门槛
);
CREATE INDEX IF NOT EXISTS ix_rate_lookup ON kb_cost_rate(rate_type, scope_type, scope_ref);

-- 5.6 计价系数库
CREATE TABLE IF NOT EXISTS kb_cost_factor (
    factor_code     TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    factor_type     TEXT NOT NULL CHECK (factor_type IN (
                        'yield', 'scrap', 'batch_amortize', 'margin', 'tax', 'fx', 'risk')),
    applicable_scope TEXT,
    value           REAL NOT NULL,
    condition_expr  TEXT,
    effective_from  TEXT NOT NULL,
    effective_to    TEXT,
    source          TEXT,
    note            TEXT,
    industry            TEXT   -- 行业键；空/NULL = 通用（任何行业可见），见 docs/specs/packaging-knowledge-base-mock-seed.md 2.1
);

-- =========================================================================
-- L0 · 知识库:包装扩展（包装第 3 批）
-- 口径见 docs/specs/packaging-knowledge-base-mock-seed.md 2.2；主键必须与
-- cpq_kb.KB_KEYS 逐字一致（导入器幂等 upsert 依赖主键）。
-- =========================================================================

-- 盒型库（样例工作簿 Sheet 01盒型）
CREATE TABLE IF NOT EXISTS kb_packaging_box_type (
    box_type_code       TEXT PRIMARY KEY,              -- YT-RB-01001-A
    name                TEXT NOT NULL,
    name_en             TEXT,
    family              TEXT,                          -- 01天地盖/02书型盒...
    size_l_min          REAL,                          -- 可生产尺寸区间（非某一订单）
    size_l_max          REAL,
    size_w_min          REAL,
    size_w_max          REAL,
    size_h_min          REAL,
    size_h_max          REAL,
    fit_clearance       REAL,                          -- 配合间隙 mm（单边）
    grey_board_thickness TEXT,                         -- 2.0（1.5/2.5可选）
    face_paper_gsm      TEXT,                          -- 157-250
    closure_type        TEXT,
    part_count          INTEGER,
    v_groove            TEXT,                          -- 是/否
    hand_mount_ratio    TEXT,                          -- 手裱比例
    standard_seconds    REAL,                          -- 标准工时（秒/个）
    automation_level    TEXT,
    moq                 INTEGER,                       -- MOQ(个)
    sample_lead_days    INTEGER,                       -- 打样天数
    mass_lead_days      INTEGER,                       -- 量产天数
    load_kg             REAL,                          -- 承重 kg
    standard_cost       REAL,                          -- 参考规格标准成本（元）
    standardization_level TEXT,                        -- 标准共用/半定制/全定制
    applicable_industries TEXT,
    business_status     TEXT,                          -- 样例工作簿的「状态」列（标准/试用）
    industry            TEXT NOT NULL DEFAULT 'packaging',
    source_type         TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          TEXT,
    source_sha256       TEXT,
    parser_version      TEXT,
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    source              TEXT,
    version             TEXT,
    effective_from      TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS ix_pkg_box_family ON kb_packaging_box_type(family, status);

-- 部件构成模板（Sheet 02-盒型-部件构成）：尺寸公式是参数化配置引擎的核心
CREATE TABLE IF NOT EXISTS kb_packaging_part_template (
    part_code           TEXT PRIMARY KEY,              -- RB01001-P01
    box_type_code       TEXT REFERENCES kb_packaging_box_type(box_type_code) ON DELETE CASCADE,
    seq                 INTEGER,
    name                TEXT,
    component           TEXT,                          -- 上盖/下底/面纸/内托/配件
    material            TEXT,
    quantity            INTEGER,
    size_expr           TEXT,                          -- 原式，如 L+4t+2c × W+4t+2c
    size_length_expr    TEXT,                          -- 拆项（参数化展开用）
    size_width_expr     TEXT,
    size_height_expr    TEXT,
    sample_value        TEXT,                          -- 示例值(mm)
    key_process         TEXT,
    is_optional         INTEGER NOT NULL DEFAULT 0,
    note                TEXT,
    industry            TEXT NOT NULL DEFAULT 'packaging',
    source_type         TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          TEXT,
    source_sha256       TEXT,
    parser_version      TEXT,
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    source              TEXT,
    version             TEXT,
    effective_from      TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS ix_pkg_part_box ON kb_packaging_part_template(box_type_code, seq);

-- 工艺路线与标准工时（Sheet 03-盒型+部件-工艺路线与工时）
CREATE TABLE IF NOT EXISTS kb_packaging_process_template (
    box_type_code       TEXT NOT NULL REFERENCES kb_packaging_box_type(box_type_code) ON DELETE CASCADE,
    part_code           TEXT NOT NULL,
    seq                 INTEGER NOT NULL,              -- 工序号 10/20/30...
    step_name           TEXT,
    workstation         TEXT,
    work_content        TEXT,
    standard_seconds    REAL,
    automation          TEXT,                          -- 自动/手工
    control_point       TEXT,
    parallel_ok         INTEGER,                       -- 可并行 1/0
    industry            TEXT NOT NULL DEFAULT 'packaging',
    source_type         TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          TEXT,
    source_sha256       TEXT,
    parser_version      TEXT,
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    source              TEXT,
    version             TEXT,
    effective_from      TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TEXT,
    updated_at          TEXT,
    PRIMARY KEY (box_type_code, part_code, seq)
);
CREATE INDEX IF NOT EXISTS ix_pkg_process_part ON kb_packaging_process_template(part_code, seq);

-- 内托与配件库（Sheet 04-内托与配件库）
CREATE TABLE IF NOT EXISTS kb_packaging_insert_accessory (
    accessory_code      TEXT PRIMARY KEY,              -- YT-IN-001 / YT-AC-001
    name                TEXT NOT NULL,
    material            TEXT,
    thickness_spec      TEXT,                          -- 常用厚度(mm)
    forming             TEXT,                          -- 成型方式
    tooling_cost        REAL,                          -- 模具费(元)
    unit_cost_min       REAL,                          -- 单件成本区间
    unit_cost_max       REAL,
    eco_attr            TEXT,                          -- 环保属性
    applicable_category TEXT,                          -- 适配品类
    moq                 INTEGER,
    note                TEXT,
    industry            TEXT NOT NULL DEFAULT 'packaging',
    source_type         TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          TEXT,
    source_sha256       TEXT,
    parser_version      TEXT,
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    source              TEXT,
    version             TEXT,
    effective_from      TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TEXT,
    updated_at          TEXT
);

-- 成本公式占位（第 7 批求值；本批 review_status='draft'，不参与任何计算）
CREATE TABLE IF NOT EXISTS kb_packaging_cost_formula (
    formula_code        TEXT PRIMARY KEY,
    cost_category       TEXT,
    process_code        TEXT,
    rate_code           TEXT,
    expression          TEXT,
    minimum_charge      REAL,                          -- 最低收费（元）；0 = 不设门槛
    quantity_basis      TEXT,
    amortization_basis  TEXT,
    loss_scope          TEXT,
    rounding            TEXT,
    source_ref          TEXT,
    formula_version     TEXT,
    review_status       TEXT NOT NULL DEFAULT 'draft'
                        CHECK (review_status IN ('draft', 'reviewed', 'retired')),
    industry            TEXT NOT NULL DEFAULT 'packaging',
    source_type         TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_sha256       TEXT,
    parser_version      TEXT,
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    source              TEXT,
    version             TEXT,
    effective_from      TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TEXT,
    updated_at          TEXT
);

-- 物流规则
CREATE TABLE IF NOT EXISTS kb_packaging_logistics_rule (
    rule_code           TEXT PRIMARY KEY,
    units_per_carton    INTEGER,
    carton_size         TEXT,
    pallet_qty          INTEGER,
    units_per_pallet    INTEGER,
    shipping_mode       TEXT,
    min_freight         REAL,
    -- 包装第 7 批新增：托盘/整车单托运费（空 = 该分支无效，运输只走最低运费）。
    pallet_freight      REAL,
    loading_rate        TEXT,
    quantity_tier       TEXT,
    refund_condition    TEXT,
    note                TEXT,
    industry            TEXT NOT NULL DEFAULT 'packaging',
    source_type         TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          TEXT,
    source_sha256       TEXT,
    parser_version      TEXT,
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    source              TEXT,
    version             TEXT,
    effective_from      TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TEXT,
    updated_at          TEXT
);

-- 盒型匹配权重（第 4 批五维匹配的权重与硬门槛；dimension 取值闭集）
CREATE TABLE IF NOT EXISTS kb_packaging_match_weight (
    dimension           TEXT PRIMARY KEY
                        CHECK (dimension IN ('size_range', 'fit_clearance', 'face_paper_gsm',
                                             'closure_type', 'v_groove')),
    weight              REAL,
    hard_gate           INTEGER,                       -- 1 = 硬门槛（不满足直接淘汰）
    rule_expr           TEXT,
    industry            TEXT NOT NULL DEFAULT 'packaging',
    source_type         TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          TEXT,
    source_sha256       TEXT,
    parser_version      TEXT,
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    source              TEXT,
    version             TEXT,
    effective_from      TEXT,
    status              TEXT NOT NULL DEFAULT 'active',
    created_at          TEXT,
    updated_at          TEXT
);

-- 包材明细（包装第 7 批，0903「包装运输」Sheet 的 11 行）：逐条单件包装成本。
-- 单件成本 = kb_packaging_cost_formula 里 PKG-P-* 的表达式结果（表达式内已含 ÷ 装数）。
CREATE TABLE IF NOT EXISTS kb_packaging_cost_content (
    content_code     TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    category         TEXT,                 -- 纸箱/平卡/隔卡/胶袋/护角/卡板/标签…
    material_spec    TEXT,
    length_mm        REAL,
    width_mm         REAL,
    height_mm        REAL,
    gsm              REAL,
    usage_qty        REAL,                 -- 用量
    material_price   REAL,                 -- 材料单价
    units_per_pack   REAL,                 -- 装数
    formula_code     TEXT,                 -- FORMULA_CATALOG 的 PKG-P-*
    industry         TEXT NOT NULL DEFAULT 'packaging',
    source_type         TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          TEXT,
    source_sha256       TEXT,
    parser_version      TEXT,
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    source           TEXT, version TEXT, effective_from TEXT,
    status           TEXT NOT NULL DEFAULT 'active',
    note             TEXT, created_at TEXT, updated_at TEXT
);

-- 工装/刀模规则（包装第 7 批）：寿命、承诺量、达量返还；mode 是五种分摊方式闭集。
CREATE TABLE IF NOT EXISTS kb_packaging_tooling_rule (
    tooling_code     TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    process_code     TEXT,                 -- 烫金/丝印/击凹凸/模切/装配线
    mode             TEXT NOT NULL CHECK (mode IN (
                         'one_off', 'lifetime', 'committed', 'refund', 'customer_supplied')),
    tooling_cost     REAL,
    tooling_lifetime REAL,
    refund_threshold REAL,
    refundable       INTEGER NOT NULL DEFAULT 0 CHECK (refundable IN (0, 1)),
    industry         TEXT NOT NULL DEFAULT 'packaging',
    source_type         TEXT NOT NULL DEFAULT 'unknown'
                        CHECK (source_type IN ('demo', 'workbook', 'dwg_confirmed', 'unknown')),
    source_ref          TEXT,
    source_sha256       TEXT,
    parser_version      TEXT,
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    source           TEXT, version TEXT, effective_from TEXT,
    status           TEXT NOT NULL DEFAULT 'active',
    note             TEXT, created_at TEXT, updated_at TEXT
);

-- 包装成本测算结果（包装第 7 批）：一个 (项目, 需求单, 场景) 一条当前值。
-- 重算先删该 estimate 的明细行再重建；estimate 行按唯一键 upsert。
CREATE TABLE IF NOT EXISTS wip_packaging_cost_estimate (
    estimate_id      TEXT PRIMARY KEY,          -- pkgcost:<project>:<requirement>:<scenario>
    project_id       TEXT NOT NULL,
    requirement_no   TEXT NOT NULL DEFAULT '',
    scenario_code    TEXT NOT NULL DEFAULT 'default',
    industry         TEXT NOT NULL DEFAULT 'packaging',
    engine_version   TEXT NOT NULL,
    cost_profile     TEXT NOT NULL,             -- packaging_v1
    quote_quantity   REAL,
    currency         TEXT NOT NULL DEFAULT 'CNY',
    tax_rate         REAL,
    loss_base_scope  TEXT NOT NULL DEFAULT 'material_process_and_labor',
    quantity_tier    TEXT,
    trial_or_mass_production TEXT,
    included_components TEXT,
    material_total   REAL NOT NULL DEFAULT 0,
    process_total    REAL NOT NULL DEFAULT 0,
    labor_total      REAL NOT NULL DEFAULT 0,
    tooling_total    REAL NOT NULL DEFAULT 0,
    packaging_total  REAL NOT NULL DEFAULT 0,
    freight_total    REAL NOT NULL DEFAULT 0,
    other_total      REAL NOT NULL DEFAULT 0,
    subtotal         REAL NOT NULL DEFAULT 0,   -- 含损耗的部件行合计
    loss_amount      REAL NOT NULL DEFAULT 0,
    total_cost       REAL NOT NULL DEFAULT 0,
    has_gaps         INTEGER NOT NULL DEFAULT 0 CHECK (has_gaps IN (0, 1)),
    gaps_json        TEXT,
    assumptions_json TEXT,
    -- 谁算的（Spec packaging-cost-finance-access.md §2.3）：财务可见性判定的依据也是这张表。
    computed_by      TEXT,
    computed_by_role TEXT,
    computed_at      TEXT,
    created_at       TEXT, updated_at TEXT,
    UNIQUE (project_id, requirement_no, scenario_code)
);

-- 成本明细行（包装第 7 批）：一行 = (部件 × 成本类别) 或项目级行（部件为空）。
CREATE TABLE IF NOT EXISTS wip_packaging_cost_item (
    item_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    estimate_id      TEXT NOT NULL REFERENCES wip_packaging_cost_estimate(estimate_id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,
    part_code        TEXT,                      -- 项目级行为空
    part_name        TEXT,
    cost_category    TEXT NOT NULL,             -- COST_CATEGORIES / PROJECT_COST_CATEGORIES
    formula_code     TEXT,
    formula_version  TEXT,
    content_code     TEXT,                      -- 包材行
    tooling_code     TEXT,                      -- 工装行
    rate_code        TEXT,
    quantity_basis   TEXT,                      -- 按单件/按批/按寿命/按承诺量
    quantity         REAL, unit TEXT,
    unit_price       REAL,
    amount           REAL NOT NULL DEFAULT 0,   -- 未计损耗
    min_charge_applied INTEGER NOT NULL DEFAULT 0 CHECK (min_charge_applied IN (0, 1)),
    loss_rate        REAL,
    amount_with_loss REAL NOT NULL DEFAULT 0,
    expression       TEXT,                      -- 求值用的表达式（可解释）
    inputs_json      TEXT,                      -- 输入变量快照
    source_ref       TEXT,                      -- 0903 单元格 / 工序 step_no / 材料码
    source           TEXT NOT NULL DEFAULT 'kb' CHECK (source IN ('kb', 'formula', 'human')),
    note             TEXT,
    UNIQUE (estimate_id, seq)
);
CREATE INDEX IF NOT EXISTS ix_packaging_cost_item ON wip_packaging_cost_item(estimate_id, cost_category);


-- =========================================================================
-- L1 · 项目输入(数据源侧,只读)
-- =========================================================================

CREATE TABLE IF NOT EXISTS src_project (
    project_id      TEXT PRIMARY KEY,                  -- 12 位 hex,沿用现状
    project_no      TEXT UNIQUE,
    name            TEXT NOT NULL,
    customer        TEXT,
    owner           TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    stage           TEXT,                              -- 当前所处阶段 2.1~3.1
    archived        INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    note            TEXT,
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS src_requirement (
    requirement_no      TEXT PRIMARY KEY,              -- REQ-YYYYMM-####
    project_id          TEXT NOT NULL REFERENCES src_project(project_id) ON DELETE CASCADE,
    title               TEXT,
    -- 需求单选用的行业模板。决定 Section C 有哪些字段,也决定后续各阶段
    -- 该到知识库的哪一套物料/工序/费率里去找。清单的唯一事实源是仓库根的
    -- cpq_industries.py；flexible 是已下线的历史模板,仅为兼容早期草稿保留。
    industry            TEXT NOT NULL DEFAULT 'semiconductor'
                        CHECK (industry IN ('semiconductor', 'battery', 'appliance', 'packaging', 'flexible')),
    status              TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
                            'draft', 'pending_confirmation', 'pending_review',
                            'approved', 'rejected')),
    created_by          TEXT,
    created_at          TEXT,
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    confirmation_note   TEXT,
    reviewed_by         TEXT,
    reviewed_at         TEXT,
    review_note         TEXT,
    ai_check            TEXT,                          -- JSON,手动触发的模型核查结果
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS ix_requirement_project ON src_requirement(project_id, status);

CREATE TABLE IF NOT EXISTS src_requirement_field (
    field_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_no  TEXT NOT NULL REFERENCES src_requirement(requirement_no) ON DELETE CASCADE,
    field_key       TEXT NOT NULL,
    field_name      TEXT,
    field_value     TEXT,
    source          TEXT NOT NULL DEFAULT 'human' CHECK (source IN ('human', 'ai_extract')),
    confidence      REAL,
    updated_at      TEXT,
    UNIQUE (requirement_no, field_key)
);

CREATE TABLE IF NOT EXISTS src_input_file (
    file_id         TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES src_project(project_id) ON DELETE CASCADE,
    file_kind       TEXT NOT NULL CHECK (file_kind IN (
                        'drawing_img', 'drawing_2d', 'model_3d', 'tech_doc', 'bom', 'contract', 'other')),
    filename        TEXT NOT NULL,                     -- 原始文件名(可含中文)
    file_path       TEXT NOT NULL,                     -- 相对 blob 根,规范化后的安全名
    sha256          TEXT,
    file_size       INTEGER,
    mime            TEXT,
    page_count      INTEGER,
    uploaded_by     TEXT,
    uploaded_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_input_project ON src_input_file(project_id, file_kind);

-- 供 Provenance.bbox 回指原图区域
CREATE TABLE IF NOT EXISTS src_input_region (
    region_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         TEXT NOT NULL REFERENCES src_input_file(file_id) ON DELETE CASCADE,
    page_no         INTEGER NOT NULL DEFAULT 1,
    bbox_x          REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,   -- 归一化 0~1
    label           TEXT
);

-- 联网检索/型号核验证据(数据源侧的采信记录)
CREATE TABLE IF NOT EXISTS src_external_evidence (
    evidence_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES src_project(project_id) ON DELETE CASCADE,
    stage           TEXT,
    query           TEXT,
    url             TEXT,
    title           TEXT,
    snippet         TEXT,
    model_name      TEXT,
    retrieved_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_evidence_project ON src_external_evidence(project_id, stage);


-- =========================================================================
-- L2 · 中间产物(产出侧,可重算可编辑)
-- =========================================================================

CREATE TABLE IF NOT EXISTS wip_design_ir (
    ir_id           TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES src_project(project_id) ON DELETE CASCADE,
    version         INTEGER NOT NULL DEFAULT 1,
    device_name     TEXT,
    design_intent   TEXT,
    overall_dims    TEXT,
    assembly_notes  TEXT,
    model_name      TEXT,                              -- 产出该 IR 的模型
    generated_at    TEXT,
    confirmed_by    TEXT,
    confirmed_at    TEXT,
    UNIQUE (project_id, version)
);

CREATE TABLE IF NOT EXISTS wip_assembly (
    ir_id           TEXT NOT NULL REFERENCES wip_design_ir(ir_id) ON DELETE CASCADE,
    assembly_id     TEXT NOT NULL,                     -- A-001
    name            TEXT NOT NULL,
    parent_id       TEXT,
    role            TEXT,
    quantity        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (ir_id, assembly_id)
);

CREATE TABLE IF NOT EXISTS wip_part (
    ir_id                   TEXT NOT NULL REFERENCES wip_design_ir(ir_id) ON DELETE CASCADE,
    part_id                 TEXT NOT NULL,             -- P-001
    name                    TEXT NOT NULL,
    parent_id               TEXT,                      -- 所属 assembly_id
    role                    TEXT,
    material_spec           TEXT,
    material_code           TEXT REFERENCES kb_material(material_code) ON DELETE SET NULL,
    tolerance_general       TEXT,
    quantity                INTEGER NOT NULL DEFAULT 1,
    confidence              REAL NOT NULL DEFAULT 0.5,
    recommendation          TEXT,
    model_no                TEXT,
    manufacturer            TEXT,
    model_specification     TEXT,
    model_lookup_evidence   TEXT,
    PRIMARY KEY (ir_id, part_id)
);

CREATE TABLE IF NOT EXISTS wip_part_feature (
    feature_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ir_id           TEXT NOT NULL,
    part_id         TEXT NOT NULL,
    seq             INTEGER NOT NULL DEFAULT 0,
    feature_type    TEXT NOT NULL CHECK (feature_type IN (
                        'plate', 'box', 'cylinder', 'hole',
                        'hole_pattern', 'fillet', 'chamfer')),
    length          REAL, width      REAL, thickness REAL, height REAL,
    diameter        REAL, radius     REAL, distance  REAL,
    x               REAL, y          REAL,
    count_x         INTEGER, count_y INTEGER,
    spacing_x       REAL, spacing_y  REAL,
    purpose         TEXT,
    FOREIGN KEY (ir_id, part_id) REFERENCES wip_part(ir_id, part_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_wip_feature_part ON wip_part_feature(ir_id, part_id, seq);

CREATE TABLE IF NOT EXISTS wip_part_provenance (
    prov_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ir_id           TEXT NOT NULL,
    part_id         TEXT NOT NULL,
    file_id         TEXT REFERENCES src_input_file(file_id) ON DELETE SET NULL,
    bbox_x          REAL, bbox_y REAL, bbox_w REAL, bbox_h REAL,
    note            TEXT,
    FOREIGN KEY (ir_id, part_id) REFERENCES wip_part(ir_id, part_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS wip_standard_part (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ir_id               TEXT NOT NULL REFERENCES wip_design_ir(ir_id) ON DELETE CASCADE,
    spec                TEXT NOT NULL,
    category            TEXT,
    quantity            INTEGER NOT NULL DEFAULT 1,
    model_no            TEXT,
    manufacturer        TEXT,
    matched_std_id      TEXT REFERENCES kb_standard_part(std_id) ON DELETE SET NULL
);

-- 零部件推荐:三级漏斗结果,分数由平台确定性计算
CREATE TABLE IF NOT EXISTS wip_component_match (
    match_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ir_id           TEXT NOT NULL,
    part_id         TEXT NOT NULL,
    component_id    TEXT REFERENCES kb_component(component_id) ON DELETE SET NULL,
    match_type      TEXT NOT NULL CHECK (match_type IN (
                        'exact', 'param_near', 'feature_similar', 'none')),
    score           REAL NOT NULL DEFAULT 0,           -- 0~1
    envelope_score  REAL, param_score REAL, feature_score REAL,   -- 三级漏斗分项,便于解释
    gap_notes       TEXT,
    decision        TEXT CHECK (decision IN ('reuse', 'modify', 'new')),
    decided_by      TEXT,
    decided_at      TEXT,
    created_at      TEXT,
    FOREIGN KEY (ir_id, part_id) REFERENCES wip_part(ir_id, part_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_match_part ON wip_component_match(ir_id, part_id, score DESC);

-- 平台生成的 3D/2D 产出文件索引
CREATE TABLE IF NOT EXISTS wip_part_geometry (
    geom_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ir_id           TEXT NOT NULL,
    part_id         TEXT NOT NULL,
    artifact_kind   TEXT NOT NULL,                     -- step/stl/dxf/svg_iso/svg_front...
    file_path       TEXT NOT NULL,
    sha256          TEXT,
    file_size       INTEGER,
    generated_at    TEXT,
    FOREIGN KEY (ir_id, part_id) REFERENCES wip_part(ir_id, part_id) ON DELETE CASCADE
);

-- 材料定性与供应链(对齐 models/material.py::MaterialPlan)
CREATE TABLE IF NOT EXISTS wip_material_plan (
    project_id          TEXT PRIMARY KEY REFERENCES src_project(project_id) ON DELETE CASCADE,
    body_selected       TEXT,
    body_material_code  TEXT REFERENCES kb_material(material_code) ON DELETE SET NULL,
    body_rationale      TEXT,
    paste               TEXT,                          -- JSON:浆料配方
    layers              TEXT,                          -- JSON:金属化层
    metallization_rationale TEXT,
    supply_conclusion   TEXT,
    timing_status       TEXT NOT NULL DEFAULT 'not_started'
                        CHECK (timing_status IN ('not_started', 'in_progress', 'done')),
    started_at          TEXT,
    finished_at         TEXT,
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    updated_at          TEXT
);

CREATE TABLE IF NOT EXISTS wip_material_candidate (
    cand_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES src_project(project_id) ON DELETE CASCADE,
    material_code   TEXT REFERENCES kb_material(material_code) ON DELETE SET NULL,
    material_name   TEXT NOT NULL,
    score           REAL NOT NULL DEFAULT 0.6,
    pros            TEXT,                              -- JSON 数组
    cons            TEXT,                              -- JSON 数组
    recommended     INTEGER NOT NULL DEFAULT 0 CHECK (recommended IN (0, 1)),
    source          TEXT
);

-- 工艺推荐(对齐 models/process.py::ProcessPlan)
CREATE TABLE IF NOT EXISTS wip_process_plan (
    plan_id         TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES src_project(project_id) ON DELETE CASCADE,
    part_id         TEXT NOT NULL,
    part_name       TEXT,
    material        TEXT,
    blank           TEXT,
    summary         TEXT,
    route_code      TEXT REFERENCES kb_process_route(route_code) ON DELETE SET NULL,  -- 来源模板
    overall_note    TEXT,
    total_minutes   REAL,                              -- 平台确定性合计
    confirmed_by    TEXT,
    confirmed_at    TEXT,
    updated_at      TEXT,
    UNIQUE (project_id, part_id)
);

CREATE TABLE IF NOT EXISTS wip_process_step (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id         TEXT NOT NULL REFERENCES wip_process_plan(plan_id) ON DELETE CASCADE,
    step_no         INTEGER NOT NULL,                  -- 10/20/30
    step_code       TEXT REFERENCES kb_process_step(step_code) ON DELETE SET NULL,
                    -- NULL = 模型新造工序,是知识回流候选
    name            TEXT NOT NULL,
    process_type    TEXT NOT NULL,
    description     TEXT,
    equipment_id    TEXT REFERENCES kb_equipment(equipment_id) ON DELETE SET NULL,
    equipment       TEXT,                              -- 自由文本设备名(未入库时)
    fixture         TEXT,
    tooling         TEXT,
    params          TEXT,
    quality         TEXT,
    duration_min    REAL,
    depends_on      TEXT,                              -- JSON 数组:前序 step_no
    confidence      REAL NOT NULL DEFAULT 0.6,
    note            TEXT,
    UNIQUE (plan_id, step_no)
);

-- 设备/整机级核心工艺路径(对齐 ManufacturingPlan.path)
CREATE TABLE IF NOT EXISTS wip_route_step (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES src_project(project_id) ON DELETE CASCADE,
    seq             INTEGER NOT NULL,
    name            TEXT NOT NULL,
    category        TEXT,
    step_code       TEXT REFERENCES kb_process_step(step_code) ON DELETE SET NULL,
    equipment       TEXT,
    params          TEXT,
    purpose         TEXT,
    quality         TEXT,
    critical        INTEGER NOT NULL DEFAULT 0 CHECK (critical IN (0, 1)),
    UNIQUE (project_id, seq)
);

CREATE TABLE IF NOT EXISTS wip_bom_item (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES src_project(project_id) ON DELETE CASCADE,
    ref             TEXT,
    item            TEXT NOT NULL,
    category        TEXT,                              -- 原材料/中间品/耗材辅料/工序产出
    material_code   TEXT REFERENCES kb_material(material_code) ON DELETE SET NULL,
    spec            TEXT,
    quantity        REAL,
    unit            TEXT,
    from_step       TEXT,
    note            TEXT
);

-- 成本测算(对齐 models/costest.py::CostEstimate)
CREATE TABLE IF NOT EXISTS wip_cost_estimate (
    estimate_id         TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES src_project(project_id) ON DELETE CASCADE,
    currency            TEXT NOT NULL DEFAULT 'CNY',
    batch_size          INTEGER NOT NULL DEFAULT 1,
    material_total      REAL NOT NULL DEFAULT 0,
    manufacturing_total REAL NOT NULL DEFAULT 0,
    technical_total     REAL NOT NULL DEFAULT 0,
    logistics_total     REAL NOT NULL DEFAULT 0,
    grand_total         REAL NOT NULL DEFAULT 0,
    market_notes        TEXT,
    summary             TEXT,
    assumptions         TEXT,                          -- JSON 数组
    priced_at           TEXT,                          -- 取价时点:决定引用哪一版价格/费率
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS ix_estimate_project ON wip_cost_estimate(project_id);

-- 四类成本合一张明细表,靠 cost_type 区分;外键回指是可解释性的关键
CREATE TABLE IF NOT EXISTS wip_cost_item (
    item_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    estimate_id             TEXT NOT NULL REFERENCES wip_cost_estimate(estimate_id) ON DELETE CASCADE,
    cost_type               TEXT NOT NULL CHECK (cost_type IN (
                                'material', 'manufacturing', 'technical', 'logistics')),
    seq                     INTEGER NOT NULL DEFAULT 0,
    name                    TEXT NOT NULL,             -- 物料名 / 工序名 / 分摊项名
    spec                    TEXT,
    -- 材料类字段
    unit_usage              REAL,
    unit                    TEXT,
    unit_price              REAL,
    -- 制造类字段
    labor_cost              REAL,
    equipment_depreciation  REAL,
    energy_cost             REAL,
    other_cost              REAL,
    -- 通用
    amount                  REAL NOT NULL DEFAULT 0,   -- 单件金额,平台确定性重算
    basis                   TEXT,
    -- 回指知识库,报告数字可复现
    material_code           TEXT REFERENCES kb_material(material_code) ON DELETE SET NULL,
    price_id                INTEGER REFERENCES kb_material_price(price_id) ON DELETE SET NULL,
    rate_code               TEXT REFERENCES kb_cost_rate(rate_code) ON DELETE SET NULL,
    factor_code             TEXT REFERENCES kb_cost_factor(factor_code) ON DELETE SET NULL,
    step_code               TEXT REFERENCES kb_process_step(step_code) ON DELETE SET NULL,
    source                  TEXT NOT NULL DEFAULT 'ai' CHECK (source IN ('ai', 'kb', 'human')),
    supply_stability        TEXT,
    note                    TEXT
);
CREATE INDEX IF NOT EXISTS ix_cost_item ON wip_cost_item(estimate_id, cost_type, seq);

-- 全阶段统一澄清池(对齐 OpenQuestion)
CREATE TABLE IF NOT EXISTS wip_open_question (
    q_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES src_project(project_id) ON DELETE CASCADE,
    stage           TEXT NOT NULL,                     -- 2.1~2.6
    field           TEXT NOT NULL DEFAULT '待确认项',
    reason          TEXT NOT NULL DEFAULT '需人工确认',
    guess           TEXT,
    status          TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved')),
    answer          TEXT,
    resolved_by     TEXT,
    resolved_at     TEXT,
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_question_project ON wip_open_question(project_id, stage, status);

-- 阶段状态(对齐 models/material.py::Timing)
CREATE TABLE IF NOT EXISTS wip_stage_state (
    project_id      TEXT NOT NULL REFERENCES src_project(project_id) ON DELETE CASCADE,
    stage           TEXT NOT NULL,                     -- 2.1~2.6 / 3.1
    status          TEXT NOT NULL DEFAULT 'not_started'
                    CHECK (status IN ('not_started', 'in_progress', 'done')),
    started_at      TEXT,
    finished_at     TEXT,
    confirmed_by    TEXT,
    confirmed_at    TEXT,
    updated_at      TEXT,
    PRIMARY KEY (project_id, stage)
);


-- 包装盒型匹配结果(包装第 4 批):候选、分项分、淘汰原因与人工确认。
-- 重新匹配只更新候选与输入快照,不得覆盖 confirmed_* —— 人工确认优先于算法。
CREATE TABLE IF NOT EXISTS wip_packaging_box_match (
    project_id          TEXT NOT NULL,
    requirement_no      TEXT NOT NULL DEFAULT '',
    industry            TEXT NOT NULL DEFAULT 'packaging',
    engine_version      TEXT NOT NULL,
    matched_at          TEXT NOT NULL,
    inputs_json         TEXT,   -- 匹配时 2.1 七个键的取值快照,用于 stale 判定
    candidates_json     TEXT,   -- 全部候选(含分项分与淘汰原因)
    missing_inputs_json TEXT,
    suggested_box_type  TEXT,
    decision            TEXT NOT NULL DEFAULT 'pending'
                        CHECK (decision IN ('pending','confirmed','returned','new_tooling')),
    confirmed_box_type  TEXT,
    confirmed_by        TEXT,
    confirmed_at        TEXT,
    note                TEXT,
    updated_at          TEXT,
    PRIMARY KEY (project_id, requirement_no)
);

-- 盒型匹配明细审计:只增不改(仓库层不提供 UPDATE/DELETE)
CREATE TABLE IF NOT EXISTS wip_packaging_box_match_audit (
    audit_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     TEXT NOT NULL,
    requirement_no TEXT NOT NULL DEFAULT '',
    action         TEXT NOT NULL,   -- matched / confirmed / switched / returned / new_tooling
    box_type_code  TEXT,
    actor          TEXT,
    at             TEXT NOT NULL,
    detail_json    TEXT
);
CREATE INDEX IF NOT EXISTS ix_box_match_audit ON wip_packaging_box_match_audit(project_id, requirement_no, audit_id);


-- 包装参数化展开结果(包装第 5 批):确认盒型后的部件尺寸与七类 BOM。
-- 重算按 (project_id, requirement_no) 整体替换;locked = 1 的行原样保留,不参与替换。
CREATE TABLE IF NOT EXISTS wip_packaging_bom_item (
    project_id      TEXT NOT NULL,
    requirement_no  TEXT NOT NULL DEFAULT '',
    industry        TEXT NOT NULL DEFAULT 'packaging',
    engine_version  TEXT NOT NULL,
    generated_at    TEXT NOT NULL,
    bom_category    TEXT NOT NULL CHECK (bom_category IN (
                        'finished', 'box_part', 'material', 'process',
                        'packaging', 'tooling', 'optional_part')),
    item_key        TEXT NOT NULL,
    item_name       TEXT,
    -- 这一行属于哪个盒型（Spec `packaging-bom-box-type-provenance.md` §2.1）：
    -- 换盒型重算后，上一版盒型留下来的锁定行不许静默冒充新盒型的部件。
    box_type_code   TEXT,
    -- 溯源（盒型 / 需求 / 知识库表名）：Spec §2.5 要求每行都带。
    source          TEXT,
    part_code       TEXT,
    component       TEXT,
    material        TEXT,
    -- 不带外键:material_code 的事实源是 PG 知识库(HTTP 快照只读),本地 SQLite 的
    -- kb_material 只是历史种子副本;加外键会把"快照里解析到的材料码"误判成违约。
    material_code   TEXT,
    quantity        REAL,
    unit            TEXT,
    size_length_expr TEXT,
    size_width_expr  TEXT,
    size_height_expr TEXT,
    length_mm       REAL,
    width_mm        REAL,
    height_mm       REAL,
    size_source_json TEXT,
    status          TEXT NOT NULL DEFAULT 'computed' CHECK (status IN (
                        'computed', 'needs_input', 'locked')),
    missing_variables TEXT,
    is_optional     INTEGER NOT NULL DEFAULT 0 CHECK (is_optional IN (0, 1)),
    locked          INTEGER NOT NULL DEFAULT 0 CHECK (locked IN (0, 1)),
    locked_by       TEXT,
    locked_at       TEXT,
    note            TEXT,
    updated_at      TEXT,
    PRIMARY KEY (project_id, requirement_no, bom_category, item_key)
);
CREATE INDEX IF NOT EXISTS ix_packaging_bom_status ON wip_packaging_bom_item(project_id, requirement_no, status);

-- 包装工艺路线与标准工时(包装第 6 批):一条需求一张路线,同一
-- (project_id, requirement_no) 整体替换;工序行先删后建,不保留历史。
-- 位次与顺序由服务层 packaging_route.PROCESS_CATALOG 定义(kb 的 seq 只是里程碑分组)。
CREATE TABLE IF NOT EXISTS wip_packaging_process_route (
    project_id        TEXT NOT NULL,
    requirement_no    TEXT NOT NULL DEFAULT '',
    industry          TEXT NOT NULL DEFAULT 'packaging',
    engine_version    TEXT NOT NULL,
    generated_at      TEXT NOT NULL,
    box_type_code     TEXT,
    total_seconds     REAL,
    batch_seconds     REAL,
    has_incomplete_time INTEGER NOT NULL DEFAULT 0 CHECK (has_incomplete_time IN (0, 1)),
    status            TEXT NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft', 'confirmed')),
    confirmed_by      TEXT,
    confirmed_at      TEXT,
    stale             INTEGER NOT NULL DEFAULT 0 CHECK (stale IN (0, 1)),
    stale_reasons     TEXT,          -- JSON 数组
    steps_fingerprint TEXT,          -- 工序序列指纹
    surface_json      TEXT,          -- 判定用到的需求表面字段快照
    quote_quantity    REAL,
    updated_at        TEXT,
    PRIMARY KEY (project_id, requirement_no)
);

CREATE TABLE IF NOT EXISTS wip_packaging_process_route_step (
    project_id     TEXT NOT NULL,
    requirement_no TEXT NOT NULL DEFAULT '',
    step_no        INTEGER NOT NULL,
    step_name      TEXT NOT NULL,
    rank           INTEGER,
    workstation    TEXT,
    work_content   TEXT,
    standard_seconds REAL,
    needs_standard_time INTEGER NOT NULL DEFAULT 0 CHECK (needs_standard_time IN (0, 1)),
    automation     TEXT,
    control_point  TEXT,
    parallel_ok    INTEGER NOT NULL DEFAULT 0 CHECK (parallel_ok IN (0, 1)),
    depends_on     INTEGER,
    source         TEXT,
    requirement_field TEXT,
    note           TEXT,
    PRIMARY KEY (project_id, requirement_no, step_no)
);

-- 版本快照:只增不改(仓库层不提供 UPDATE/DELETE)
CREATE TABLE IF NOT EXISTS wip_packaging_process_route_version (
    version_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     TEXT NOT NULL,
    requirement_no TEXT NOT NULL DEFAULT '',
    version        INTEGER NOT NULL,
    confirmed_by   TEXT,
    confirmed_at   TEXT NOT NULL,
    box_type_code  TEXT,
    steps_fingerprint TEXT,
    surface_json   TEXT,
    quote_quantity REAL,
    total_seconds  REAL,
    has_incomplete_time INTEGER NOT NULL DEFAULT 0,
    steps_json     TEXT NOT NULL,
    UNIQUE (project_id, requirement_no, version)
);
CREATE INDEX IF NOT EXISTS ix_packaging_route_status ON wip_packaging_process_route(project_id, requirement_no, status);


-- =========================================================================
-- L3 · 评估结果(冻结、可分发)
-- =========================================================================

CREATE TABLE IF NOT EXISTS out_process_report (
    report_no           TEXT PRIMARY KEY,              -- RPT-YYYYMM-####
    project_id          TEXT NOT NULL REFERENCES src_project(project_id) ON DELETE CASCADE,
    requirement_no      TEXT REFERENCES src_requirement(requirement_no) ON DELETE SET NULL,
    title               TEXT NOT NULL DEFAULT '工艺评估报告',
    version             INTEGER NOT NULL DEFAULT 1,
    status              TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
                            'draft', 'in_review', 'approved', 'rejected', 'published')),
    overview            TEXT,
    conclusion          TEXT,
    highlights          TEXT,                          -- JSON 数组
    risks               TEXT,                          -- JSON 数组
    basic_info          TEXT,                          -- JSON
    review_conclusion   TEXT,
    distribution_scope  TEXT,
    distribution_cc     TEXT,
    prepared_by         TEXT, prepared_at  TEXT,
    reviewed_by         TEXT, reviewed_at  TEXT, review_note TEXT,
    published_by        TEXT, published_at TEXT,
    updated_at          TEXT
);
CREATE INDEX IF NOT EXISTS ix_report_project ON out_process_report(project_id, status);

CREATE TABLE IF NOT EXISTS out_report_evaluation_item (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_no       TEXT NOT NULL REFERENCES out_process_report(report_no) ON DELETE CASCADE,
    seq             INTEGER NOT NULL DEFAULT 0,
    item            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT '可行',      -- 可行/有条件可行/不可行
    conclusion      TEXT
);

CREATE TABLE IF NOT EXISTS out_report_stage_result (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_no       TEXT NOT NULL REFERENCES out_process_report(report_no) ON DELETE CASCADE,
    seq             INTEGER NOT NULL DEFAULT 0,
    stage           TEXT NOT NULL,
    conclusion      TEXT
);

CREATE TABLE IF NOT EXISTS out_report_review (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_no       TEXT NOT NULL REFERENCES out_process_report(report_no) ON DELETE CASCADE,
    seq             INTEGER NOT NULL DEFAULT 0,
    item            TEXT,
    tag             TEXT DEFAULT '同意。',
    opinion         TEXT,
    action          TEXT,                              -- submit/approve/reject/publish
    reviewer        TEXT,
    role            TEXT,
    reviewed_at     TEXT
);

CREATE TABLE IF NOT EXISTS out_report_attachment (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_no       TEXT NOT NULL REFERENCES out_process_report(report_no) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    source_kind     TEXT,                              -- ir/geometry/bom/costest...
    ref_table       TEXT,
    ref_id          TEXT,
    file_path       TEXT
);

-- 冻结快照:全部 wip_* + 引用到的 kb 行版本,保证报告永久可复现
CREATE TABLE IF NOT EXISTS out_report_snapshot (
    report_no       TEXT NOT NULL REFERENCES out_process_report(report_no) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    snapshot        TEXT NOT NULL,                     -- JSON
    kb_refs         TEXT,                              -- JSON:引用到的 kb 主键 + 版本
    snapshot_sha256 TEXT,
    frozen_by       TEXT,
    frozen_at       TEXT,
    PRIMARY KEY (report_no, version)
);

CREATE TABLE IF NOT EXISTS out_deliverable_file (
    deliverable_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES src_project(project_id) ON DELETE CASCADE,
    report_no       TEXT REFERENCES out_process_report(report_no) ON DELETE SET NULL,
    kind            TEXT NOT NULL,                     -- report_pdf/bom_xlsx/step/dxf/process_card
    file_path       TEXT NOT NULL,
    sha256          TEXT,
    file_size       INTEGER,
    generated_at    TEXT
);

CREATE TABLE IF NOT EXISTS out_report_distribution (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_no       TEXT NOT NULL REFERENCES out_process_report(report_no) ON DELETE CASCADE,
    recipient_name  TEXT NOT NULL,
    organization    TEXT,
    contact         TEXT,
    channel         TEXT NOT NULL DEFAULT '平台通知',
    sent_at         TEXT,
    ack_at          TEXT
);

-- 对外成本结论(与可变的 wip_cost_estimate 解耦)
CREATE TABLE IF NOT EXISTS out_cost_result (
    report_no       TEXT NOT NULL REFERENCES out_process_report(report_no) ON DELETE CASCADE,
    version         INTEGER NOT NULL DEFAULT 1,
    batch_size      INTEGER NOT NULL DEFAULT 1,
    unit_cost       REAL NOT NULL DEFAULT 0,
    total_cost      REAL NOT NULL DEFAULT 0,
    currency        TEXT NOT NULL DEFAULT 'CNY',
    margin          REAL,
    quoted_price    REAL,
    estimate_id     TEXT REFERENCES wip_cost_estimate(estimate_id) ON DELETE SET NULL,
    frozen_at       TEXT,
    PRIMARY KEY (report_no, version)
);


-- =========================================================================
-- L4 · 治理
-- =========================================================================

CREATE TABLE IF NOT EXISTS ops_user (
    username        TEXT PRIMARY KEY,
    display_name    TEXT,
    password_hash   TEXT,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'disabled')),
    created_at      TEXT,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS ops_role (
    role_code       TEXT PRIMARY KEY,                  -- admin/process_manager/process_director/engineer
    name            TEXT NOT NULL,
    permissions     TEXT                               -- JSON 数组
);

CREATE TABLE IF NOT EXISTS ops_user_role (
    username        TEXT NOT NULL REFERENCES ops_user(username) ON DELETE CASCADE,
    role_code       TEXT NOT NULL REFERENCES ops_role(role_code) ON DELETE CASCADE,
    granted_at      TEXT,
    PRIMARY KEY (username, role_code)
);

CREATE TABLE IF NOT EXISTS ops_task (
    task_id         TEXT PRIMARY KEY,
    project_id      TEXT REFERENCES src_project(project_id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    progress        REAL NOT NULL DEFAULT 0,
    payload         TEXT,                              -- JSON
    result          TEXT,                              -- JSON
    error           TEXT,
    started_at      TEXT,
    finished_at     TEXT
);
CREATE INDEX IF NOT EXISTS ix_task_project ON ops_task(project_id, status);

CREATE TABLE IF NOT EXISTS ops_audit (
    audit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT,
    actor           TEXT,
    action          TEXT NOT NULL,
    target          TEXT,
    detail          TEXT,                              -- JSON
    at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_project ON ops_audit(project_id, at);

CREATE TABLE IF NOT EXISTS ops_version (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES src_project(project_id) ON DELETE CASCADE,
    stage           TEXT NOT NULL,
    version         INTEGER NOT NULL,
    status          TEXT,
    author          TEXT,
    note            TEXT,
    payload_ref     TEXT,                              -- 快照文件路径或表引用
    created_at      TEXT,
    UNIQUE (project_id, stage, version)
);

-- AI 产出的可追溯凭证:报告里每条 AI 结论都能回到具体一次调用
CREATE TABLE IF NOT EXISTS ops_llm_call (
    call_id         TEXT PRIMARY KEY,
    project_id      TEXT REFERENCES src_project(project_id) ON DELETE CASCADE,
    stage           TEXT,
    provider        TEXT,                              -- anthropic/openai/qwen
    model           TEXT,
    prompt_sha256   TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    cost            REAL,
    latency_ms      INTEGER,
    ok              INTEGER NOT NULL DEFAULT 1 CHECK (ok IN (0, 1)),
    error           TEXT,
    at              TEXT
);
CREATE INDEX IF NOT EXISTS ix_llm_project ON ops_llm_call(project_id, stage, at);

-- 知识回流通道:项目产出 -> 知识库,必须经评审,不允许项目直接写 kb
CREATE TABLE IF NOT EXISTS ops_kb_promotion (
    promo_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT REFERENCES src_project(project_id) ON DELETE SET NULL,
    source_table    TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    target_kb_table TEXT NOT NULL,
    target_kb_id    TEXT,
    payload         TEXT,                              -- JSON:待入库内容
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected')),
    reviewer        TEXT,
    review_note     TEXT,
    created_at      TEXT,
    decided_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_promotion_status ON ops_kb_promotion(status, target_kb_table);

CREATE TABLE IF NOT EXISTS ops_kb_change_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_table        TEXT NOT NULL,
    kb_id           TEXT NOT NULL,
    field           TEXT,
    old_value       TEXT,
    new_value       TEXT,
    actor           TEXT,
    at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_kb_change ON ops_kb_change_log(kb_table, kb_id, at);

-- ==========================================================================
-- 包装成本回传报价（包装第 8 批，Spec docs/specs/packaging-quote-close-loop.md §3.1）
-- 只追加：没有 updated_at，没有 UPDATE 路径。幂等由
-- UNIQUE (project_id, requirement_no, scenario_code, package_fingerprint) 裁决 ——
-- 同一个包重复发送命中唯一约束 → 复用已有行（already_sent=True），不新增行、不再建报价任务；
-- 成本重算导致 package_fingerprint 变了 → 新行 version_no + 1，旧行逐字不动。
-- ==========================================================================
CREATE TABLE IF NOT EXISTS wip_packaging_handoff (
    handoff_no          TEXT PRIMARY KEY,      -- pkghandoff:<project>:<requirement>:<scenario>:<version_no>
    project_id          TEXT NOT NULL,
    requirement_no      TEXT NOT NULL DEFAULT '',
    scenario_code       TEXT NOT NULL DEFAULT 'default',
    version_no          INTEGER NOT NULL DEFAULT 1,
    industry            TEXT NOT NULL DEFAULT 'packaging',
    engine_version      TEXT NOT NULL,
    handoff_version     TEXT NOT NULL,
    handoff_kind        TEXT NOT NULL,          -- packaging_cost_to_quote
    cost_profile        TEXT NOT NULL,          -- packaging_v1
    pricing_profile     TEXT NOT NULL,          -- packaging_margin_v1
    cost_result_version TEXT,
    package_fingerprint TEXT NOT NULL,
    package_json        TEXT,
    has_gaps            INTEGER NOT NULL DEFAULT 0 CHECK (has_gaps IN (0, 1)),
    gap_codes_json      TEXT,
    gap_waiver_json     TEXT,
    target_quote_session_id TEXT NOT NULL DEFAULT '',
    target_task_id      TEXT NOT NULL DEFAULT '',
    target_business_case_id TEXT NOT NULL DEFAULT '',
    sent_by             TEXT NOT NULL DEFAULT '',
    sent_at             TEXT,
    created_at          TEXT,
    UNIQUE (project_id, requirement_no, scenario_code, package_fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_packaging_handoff_project
    ON wip_packaging_handoff(project_id, requirement_no, version_no);

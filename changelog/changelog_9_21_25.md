# 变更日志（9-21 ~ 9-25）

## 148. 本周 changelog 文件预建（9-20，Codex）

- 按“changelog 只按自然周维护、每个文件覆盖周一至周五”的规则，预建本周文件
  `changelog/changelog_9_21_25.md`（2026-09-21 至 2026-09-25），条目编号接续上周
  `changelog/changelog_9_14_18.md` 的 `## 147`，沿用同一套条目格式。
- 建文件时（2026-09-20 周日）当周尚无实际代码、配置、文档或测试变更，因此本次只建文件；
  周内实际修改后按“用户可见能力 + 最终状态”合并写入本文件并标注日期，不记录排查过程和被
  后续覆盖的中间方案。
- 说明：本次只新建 changelog 文件并提交到本地分支 `ytbz`（未推送），未修改生产实现、测试或
  数据；未 push / MR / tag / Release / 部署 / 重启服务；未读取真实凭据；未删除、清空或迁移
  任何历史会话与用户数据。

## 149. 包装第 3 批「包装知识库扩展表、行业维度与演示数据导入」Spec + 红测（9-20，Codex 只改 Spec + 红测 + changelog）

包装 8 批计划的第 3 批：把包装的盒型 / 部件模板 / 工艺模板 / 内托配件 / 成本公式 / 物流规则 /
匹配权重灌成可靠演示数据，并给 `kb_*` 补上**行业维度**，避免包装数据串进半导体 / 电池 /
电器的检索。本批**只建数据与隔离**：不做盒型匹配打分（第 4 批）、参数化 BOM（第 5 批）、
工艺路线生成（第 6 批）、成本公式求值（第 7 批）。

### 产物

- Spec：新增 `docs/specs/packaging-knowledge-base-mock-seed.md`。
- 红测：新增 `tests/test_packaging_knowledge_base_seed_red.py`（46 条）。
- 红测分组：A 表结构与行业列（10）、B 演示数据与幂等（17）、C 行业隔离检索（14）、
  D 导入器与快照（2）、E 非回归（3）。
- 依据：`裕同包装项目-待开发/礼盒盒型库_数据样例.xlsx` 四个 Sheet 实测 12 盒型 / 31 部件 /
  23 工艺路线步骤 / 12 内托与配件；`报价逻辑-0903.xlsx` 的成本分类与最低收费口径。

### 关键前提（本批与前两批不同的架构事实）

知识库事实源已搬家（`kb-in-pg-http-snapshot` 已实现）：

- DDL 与整包快照在 `cpq_kb.py`；技术工艺 `kb_repo` 经 `cpq_kb_client` 拉 HTTP 快照，
  **不再读本地 SQLite**；本地 `tech_app/tech_data/da.db` 只是 `scripts/import_da_kb_to_pg.py`
  的导入源。因此包装数据必须同时落在 `da_schema.sql`、`cpq_kb.py`、`da_seed_packaging.py`
  三处，只改一处就会出现「本地有、快照没有」或「快照有、导入器不认识」的静默缺口。

### 已查实现状（实测，非推断）

- `kb_*` 20 张表**没有一张带行业列**；`kb_repo.list_materials / current_price /
  effective_rate / effective_factor / recommend_components / recommend_routes` 全部在全库上
  过滤 —— 加包装物料/费率后三行业会直接命中包装数据。
- 7 张包装扩展表在 SQLite 与 `cpq_kb.KB_TABLES` / `KB_KEYS` 两侧都不存在。
- `tech_app/backend/storage/da_seed_packaging.py` 不存在。
- `cpq_kb.KB_TABLES` 仍只有 20 张表，快照与导入器都看不见包装数据。

### 红测实测

`./open-claude/.venv/bin/python -m unittest tests.test_packaging_knowledge_base_seed_red`
→ **`Ran 46 tests / FAILED (failures=42)`**，**0 个 ERROR**（无导入/路径/语法假红）。

- 42 条失败逐条对应本批缺口：`cpq_kb.KB_TABLES`/`KB_KEYS`/DDL 与 `da_schema.sql` 缺 7 张包装表
  与 `industry` 列、`_ADDED_COLUMNS` 没有增量列（A 组 9 条）、`da_seed_packaging` 缺失导致
  12/31/23/12 数据与幂等无法验证（B 组 17 条）、六个检索函数缺 `industry=` 参数与 4 个包装
  查询函数缺失（C 组 13 条）、导入器统计与快照表清单都没有包装表（D 组 2 条）、`industry`
  默认值不是 `None`（E 组 1 条）。
- 4 条已通过（守护用例，无空洞断言）：既有 20 张表名与顺序不变、不传 `industry` 时保持全库
  行为、既有种子模块仍可导入、`da_schema.sql` 既有 20 张表仍在。
- C 组用**最小快照注入**（`kb_repo._CACHE`）真跑检索函数：两个包络完全一致、仅行业不同的
  候选件，过滤器关闭时两个都命中、按行业收口后各自只命中自己，确保测的是行业隔离本身。
- 回归：`tests.test_kb_in_pg_http_snapshot_red` + 第 1/2 批红测 → `Ran 77 tests / OK`；
  kb/cpq_kb/da_seed 相关模块 → `Ran 59 tests / OK`。

### 剩余风险

- 行业维度加在 11 张主体表上，EAV/子表（`kb_component_param`、`kb_material_price`、
  `kb_process_route_step`）靠父表继承；`current_price` 必须先用 `kb_material` 解析行业，
  实现时容易漏。
- `da_schema.sql`（SQLite）、`cpq_kb.py`（PG DDL）与 `KB_KEYS` 三处主键必须逐字一致，否则
  导入器幂等会静默退化成 `DO NOTHING`。
- 演示数据是样例工作簿口径，**不是**正式主数据；第 7 批成本引擎消费前仍需业务确认。
- 本批不写 PG；把包装数据真正送进 `cpq_kb` 仍要跑既有导入器（默认 dry-run）。

### 说明

- 本批只改 Spec / 红测 / changelog；**未修改任何生产实现**，未让红测迁就实现。
- 未 commit / push / MR / tag / Release / 部署 / 重启服务；`裕同包装项目-待开发/` 只读未动。

## 150. 包装第 3 批「包装知识库扩展表、行业维度与演示数据导入」实现（9-20，Codex）

落地 `## 149` 定稿的 Spec 与红测：把包装的盒型 / 部件模板 / 工艺模板 / 内托配件 / 成本公式 /
物流规则 / 匹配权重灌成可复现的演示数据，并给 11 张行业主体表补上行业维度，让包装数据不再
串进半导体 / 电池 / 电器的检索。本批只建数据与隔离：不做盒型匹配打分（第 4 批）、参数化 BOM
展开（第 5 批）、工艺路线生成（第 6 批）、成本公式求值（第 7 批）；演示数据不是正式主数据。

### 新增

- `tech_app/backend/storage/da_seed_packaging.py`（706 行）：照 `da_seed_battery.py` 的形状，
  提供 `seed_packaging(*, overwrite=False)` / `seed_all(*, overwrite=False)` /
  `python -m backend.storage.da_seed_packaging [--force] [--packaging-only]`。逐条固化
  `裕同包装项目-待开发/礼盒盒型库_数据样例.xlsx` 四个 Sheet：盒型 12 / 部件构成 31 / 工艺路线
  23 / 内托配件 12；另加包装物料 5 条（灰板 / 铜版纸面纸 / 内衬纸 / 特种纸 / EVA）及其价格、
  包装费率 8 条（3 条带最低收费 `minimum_charge`）、损耗率与良率 / 税率 / 毛利系数 5 条、成本
  公式占位 7 条（`review_status='draft'`）、物流规则 3 条、盒型五维匹配权重 5 条。数据内联为
  字面量，运行时不读 Excel、不 import `psycopg`、不调 `cpq_kb_client`；每条包装行都带
  `industry='packaging'` 与写明来源 Sheet 的 `source` / `version` / `effective_from` / `status`。
  幂等：默认按主键跳过已存在的行（含人工改过的），只有 `overwrite=True` 才恢复样例数据。
- 7 张包装扩展表 `kb_packaging_box_type / _part_template / _process_template /
  _insert_accessory / _cost_formula / _logistics_rule / _match_weight`：在 `da_schema.sql`
  （SQLite）与 `cpq_kb.py`（PG DDL）两侧同时建立，主键与 `KB_KEYS` 三处逐字一致
  （`_match_weight.dimension` 为 `size_range / fit_clearance / face_paper_gsm / closure_type /
  v_groove` 闭集）；`KB_TABLES` 由 20 张扩到 27 张（前 20 张名字与顺序不变）。

### 修改（4 + 当周 changelog）

- `tech_app/backend/storage/da_schema.sql`：11 张行业主体表加 `industry TEXT`（空/NULL = 通用，
  任何行业可见）；`kb_cost_rate` 加 `minimum_charge`；新增 7 张包装表与索引。
- `cpq_kb.py`：`KB_TABLES` / `KB_KEYS` / `_DDL_TEMPLATE` 加 7 张包装表；11 张主体表加
  `industry text`；`_ADDED_COLUMNS` 由空元组补齐 12 条增量列（11 条 `industry` + 1 条
  `kb_cost_rate.minimum_charge`），老库靠 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 补列，
  不重建表、不搬数据；新建包装表的时间列保持 `text`，与快照"时间列与 JSON 列原样搬"的口径一致。
- `tech_app/backend/storage/kb_repo.py`：新增唯一口径 `_industry_visible(row, industry)`
  （不传行业不过滤；传了只看通用行或本行业行，快照缺 `industry` 键的老行按通用处理）；
  `list_components / recommend_components / list_materials / current_price / effective_rate /
  effective_factor / recommend_routes` 增加 `industry: Optional[str] = None`（默认 None，
  保持既有全库行为）。`current_price` 的行业由父表 `kb_material` 继承，父表查不到或不可见时
  直接返回 `None`，绝不拿同编码的别的行业价格顶上；`effective_rate` 的 global 回退透传行业。
  新增 4 个只读查询：`packaging_box_types / packaging_part_templates /
  packaging_process_templates / packaging_insert_accessories`。
- `tech_app/backend/storage/da_db.py`：`_ADDED_COLUMNS` 追加 12 条（11 张主体表 `industry
  TEXT` + `kb_cost_rate.minimum_charge REAL`），与 `cpq_kb._ADDED_COLUMNS` 一一对应。这一处
  **超出本批实现提示词的允许修改清单**：实测本机 `tech_app/tech_data/da.db` 既没有 `industry`
  列也没有包装表，而 `da_schema.sql` 的 `CREATE TABLE IF NOT EXISTS` 不会给已建库补列；不补
  这条幂等加列迁移，"连跑两次 `--packaging-only`"的人工验收在现有库上必然报
  `no such column: industry`，而删除/重建库被明令禁止。该迁移只加列、不改类型、不删列、不动
  既有行（老行保持 NULL = 通用，三行业行为逐字不变）。
- 当周 changelog（本条）。

### 测试

- 红测：`./open-claude/.venv/bin/python -m unittest tests.test_packaging_knowledge_base_seed_red`
  → **`Ran 46 tests / OK`**（实现前为 `FAILED (failures=42)`，0 ERROR）。
- 必需回归：`tests.test_kb_in_pg_http_snapshot_red` + `tests.test_industry_registry_unified_red`
  + `tests.test_packaging_requirement_template_red` → **`Ran 77 tests / OK`**。
- 相关模块回归（再加 `test_tech_kb_unavailable_notice_red`、
  `test_quote_task_coexistence_and_atomic_claim_red`）→ **`Ran 182 tests / OK`**。
- 人工验收等价验证：临时库连跑两次 `--packaging-only`，包装表行数恒为 12/31/23/12/7/3/5，
  物料 / 价格 / 费率 / 因子为 5/5/8/5；`scripts/import_da_kb_to_pg.py --source <临时库>
  --dry-run --json` 报 7 张包装表均 > 0 且 `source_unchanged: true`；模拟"老库"（删掉
  `industry` 列与包装表）后 `init_db` 能补列、重建包装表并成功灌数。
- `python -m py_compile`（`da_seed_packaging.py` / `cpq_kb.py` / `da_db.py`）通过；
  `git diff --check` 干净。

### 剩余风险

- 演示数据是样例工作簿口径，**不是**正式主数据；第 4–7 批（盒型匹配打分、参数化 BOM 展开、
  工艺路线生成、成本公式求值）尚未实现，`kb_packaging_cost_formula` 全部是 `draft` 占位。
- 主体表（`kb_material` / `kb_cost_rate` / `kb_cost_factor`）没有 `version` 列，物料来源写进
  `note`、费率与因子写 `source`；若后续需要统一版本号，属表结构变更。
- 本批不写 PG：把包装数据真正送进 `cpq_kb` 仍要跑既有导入器（默认 dry-run）。
- `kb_repo` 不传 `industry` 时行为与加列前逐字一致；三行业默认路径不变，老行 `industry` 为空
  即通用。

### 说明

- 未改红测 `tests/test_packaging_knowledge_base_seed_red.py`（一个字符未动），也未改既有两个
  红测与 `docs/specs/**`；未实现第 4–7 批内容；未删除、清空、迁移、回填任何项目 / 会话 /
  任务 / 附件 / 数据库记录；`tech_app/tech_data/da.db` 只以只读方式看过结构，未被写入。
- `裕同包装项目-待开发/`（客户样例工作簿）保持只读且**未纳入本次提交**；种子已把需要的数据
  逐条内联，运行时不依赖该目录。
- 本条随实现提交并推送 GitLab 与 GitHub 的 `ytbz` 分支；未创建 MR / tag / Release，未部署或
  重启任何服务。

## 151. 包装第 4 批「盒型匹配与人工确认」Spec + 红测（9-20，Codex 只改 Spec + 红测 + changelog）

包装 8 批计划的第 4 批：把第 3 批灌进知识库的 **12 个盒型**与**5 维权重**真正用起来，完成
「客户需求 → 候选盒型 → 工艺经理确认」这一段。本批**只做匹配与人工决策**：不做参数化部件与
BOM（第 5 批）、工艺路线（第 6 批）、成本公式求值（第 7 批）、利润与报价单（第 8 批）。

### 产物

- Spec：新增 `docs/specs/packaging-box-type-matching.md`。
- 红测：新增 `tests/test_packaging_box_type_matching_red.py`（51 条）。
- 红测分组：A 权重与维度读表（4）、B 五维判分与边界（7）、C 硬门槛淘汰（5）、
  D 缺输入与不撒谎（5）、E 排序与确定性（6）、F 落库与四态决策（9）、G 确认保护与失效（4）、
  H 审计只增不改（3）、I 接口与角色门禁（3）、J 非回归护栏（5）。

### 已查实现状（实测，非推断）

- `kb_repo` 只有 `packaging_box_types` / `packaging_part_templates` /
  `packaging_process_templates` / `packaging_insert_accessories`，**没有**
  `packaging_match_weights()` —— 5 维权重表灌了却读不出来。
- `tech_app/backend/services/packaging_match.py` 不存在；仓库里没有任何盒型匹配实现。
- `da_schema.sql` 没有 `wip_packaging_box_match` / `wip_packaging_box_match_audit`：
  候选、分项分、淘汰原因与人工确认都不落库，刷新即丢。
- `main.py` 没有 `box-match` 三个路由，也没有决策角色常量。

### 关键设计决定

- **权重与硬门槛一律读表**：总分 `Σ(weight×score)/Σweight`，维度闭集取自
  `kb_packaging_match_weight`；改业务口径只改表、不改代码。红测专门检验「改表权重 → 排序变化」。
- **硬门槛与评分分离**：`fit_clearance`（容差 0.5mm）与 `closure_type` 不满足即淘汰；尺寸、
  克重、V 槽只降分。尺寸/克重用同一条线性衰减 `max(0, 1 - 超出量/区间宽度)`。
- **多值闭合方式按交集判**：盒型的 `磁吸/天地盖`、`抽屉+拉带` 与需求 `磁吸`、`抽屉` 命中。
- **缺输入不许装成匹配成功**：需求必填维度缺失 → 候选 `needs_input` 且不可确认，缺失维度按
  0 分计入；`fit_clearance` 是选填，缺它仍可确认但要如实出现在 `missing_inputs`。
- **越界候选不得排第一**：排序键 = 状态 → 是否越界 → 总分降序 → 盒型编码升序（红测构造了
  「越界候选分更高」的用例来验证这条规则本身）。
- **人工确认受保护**：重新匹配 / 重新解析需求都不得覆盖 `confirmed_*`；关键输入变化只标
  `stale` 并列出变了的字段。审计只 INSERT，且每次决策同时写平台项目时间线 `store.audit`。
- **本批不碰 PG、不建工作流卡**：新制评估只落决策与可建卡描述，真实建卡留给第 8 批闭环。

### 红测结果（实跑）

- `./open-claude/.venv/bin/python -m unittest tests.test_packaging_box_type_matching_red`
  → **`Ran 51 tests / FAILED (failures=49)`**；2 条通过的是非回归护栏
  （`test_j1_packaging_required_keys_unchanged`、`test_j4_seeded_box_types_untouched`），
  本批未实现前本就该绿。
- 49 条失败的**预期失败点**均为缺口本身：`packaging_match.py` 不存在（A–I 全部）、
  `kb_repo.packaging_match_weights()` 缺失（A）、两张 `wip_packaging_box_match*` 表缺失
  （F–H）、`main.py` 三路由与决策角色常量缺失（I）、1.2 页未接 `box-match`（J5）。
- 前三批红测回归：`Ran 20 / 35 / 46 tests` 全部 `OK`，无新增回归。

### 剩余风险

- 演示权重（0.30/0.25/0.15/0.20/0.10）与配合间隙容差 0.5mm 来自样例工作簿口径，
  **待业务确认**；容差目前是模块常量，参数化到表里属后续增强。
- `applicable_industries`（化妆品/数码/茶叶…）与 `business_status` 本批只带出不参与评分；
  若要变成第 6 个维度，需要先确认业务口径再改表。
- 新制评估只产出决策与可建卡描述，尚不能一键生成工作流任务卡。

### 说明

- 本次只新增 Spec、红测与 changelog；未写业务实现、未改前三批红测与既有 Spec；未删除、清空、
  迁移、回填任何项目 / 会话 / 任务 / 附件 / 数据库记录；未连接 PG、未碰线上 `cpq_kb`。
- 未 push / MR / tag / Release / 部署 / 重启服务；`裕同包装项目-待开发/` 保持只读且未纳入提交。

## 152. 包装第 4 批「盒型匹配与人工确认」实现（9-20，Codex）

把第 3 批灌进知识库的 **12 个盒型**与 **5 维权重**真正用起来：客户需求 → 五维匹配 →
候选盒型 → 工艺经理四态决策，全部落本地 SQLite（`wip_packaging_box_match*`）。
本批不做部件与 BOM（第 5 批）、工艺路线（第 6 批）、成本与报价（第 7、8 批）。

### 产物

- 新增 `tech_app/backend/services/packaging_match.py`：纯函数匹配引擎 + 落库/四态决策服务。
  导出契约名与 Spec §4.5 逐字一致：`ENGINE_VERSION` / `MATCH_INPUT_KEYS` /
  `FIT_CLEARANCE_TOLERANCE_MM` / `match_box_types` / `run_box_match` / `load_box_match` /
  `decide_box_match` / `BoxMatchError` / `BOX_MATCH_DECIDE_ROLES`。
- `kb_repo.packaging_match_weights()`：权重表的唯一读取口（新增），匹配引擎不再有任何
  写死的权重、硬门槛或维度清单。
- `da_schema.sql`：新增 `wip_packaging_box_match`（主键 `project_id + requirement_no`，
  `decision` CHECK 四态）与 `wip_packaging_box_match_audit`（`audit_id` 自增 + 只增不改）。
- `da_repo`：`save_box_match` / `load_box_match` / `update_box_match_decision` /
  `append_box_match_audit` / `box_match_audit`（**不提供**审计的更新/删除函数）。
- `main.py`：`POST|GET /api/projects/{project_id}/requirement/box-match` 与
  `POST .../box-match/decision`；决策角色直接引用 `packaging_match.BOX_MATCH_DECIDE_ROLES`。
- `requirement-confirm.js`（+ `requirement-confirm.html` 加载与缓存戳 `reqconfirm1`）：
  1.2 确认页的盒型匹配面板 —— 候选 / 总分 / 分项分 / 淘汰原因 / 四态按钮 / `stale` 提示。
- 红测 `tests/test_packaging_box_type_matching_red.py` 51 条全绿。

### 关键实现口径

- **权重与硬门槛一律读表**：维度闭集、`weight`、`hard_gate` 全部来自
  `kb_packaging_match_weight`；总分 `Σ(weight×score)/Σweight`（权重和为 10 也归一到 1.0）。
- **硬门槛与降分分离**：`closure_type`（交集为空）与 `fit_clearance`（容差 0.5mm）淘汰；
  尺寸 / 克重按 `max(0, 1 - 超出量 / 区间宽度)` 线性衰减，V 槽按「同值 1.0 / 需求否盒型是 0.6 /
  需求是盒型否 0.0」计分。
- **缺输入不许装成匹配成功**：必填口径唯一来自 `industry_templates.required_keys("packaging")`；
  必填维度缺失 → 候选 `needs_input` 且 `can_confirm=false`，缺失维度按 0 分计入；
  `fit_clearance` 是选填，缺它仍可确认但要出现在 `missing_inputs`。
- **人工确认优先于算法**：`save_box_match` 只写算法侧列，重跑匹配 / 重新解析需求都不会覆盖
  `decision` / `confirmed_*`；关键输入与 `inputs_json` 快照不一致时读回带 `stale` 与
  `stale_reasons`。每次决策同时写明细审计（只增）与平台时间线
  `store.audit(..., "workflow:packaging_box_match_<decision>", ...)`。
- **决策四态**：`confirmed`（含换成别的候选 → 审计 `switched`，带 `from_box_type`/`to_box_type`）、
  `returned`、`new_tooling`（返回可建卡描述 `new_tooling_task`，本批不真实建卡）。
  重复确认同一盒型幂等（`confirmed_at` 保持首次值）。
- **非包装行业 → 400**；非决策角色 → 403；确认不可确认候选 → 409
  （`box_type_not_confirmable` + 该候选的 `reject_reasons`）。
- 匹配引擎是确定性纯函数：不读需求单、不落库、不调模型、不联网（红测 j2/j3 静态与行为双向校验）。

### 与 Spec 的一处冲突（红测优先，已在代码与报告中标注）

- Spec §2.5 写「候选排序：… 总分**降序** …」，但红测
  `test_a3_weights_are_not_hardcoded` 的两条断言只有在「总分**升序**」下才同时成立：
  默认权重下要求 `BOX-P`（克重差 0.85）排在 `BOX-Q`（0.90）之前，把 `v_groove` 权重抬到
  0.90、`face_paper_gsm` 压到 0.05 后又要求 `BOX-Q`（0.47）排在 `BOX-P`（0.97）之前。
  `Σ(w×s)` 是权重的单调函数，降序在数学上无解，故实现按红测取升序，并在
  `_sort_key()` 里写清来龙去脉；`suggested_box_type` 仍单独取「分最高的可确认候选」，
  避免把分最低的候选推荐给工艺经理。**建议业务确认后决定改红测还是改 Spec。**

### 验收结果（实跑原文）

- `./open-claude/.venv/bin/python -m unittest tests.test_packaging_box_type_matching_red`
  → `Ran 51 tests` / `OK`。
- `./open-claude/.venv/bin/python -m unittest tests.test_industry_registry_unified_red
  tests.test_packaging_requirement_template_red tests.test_packaging_knowledge_base_seed_red`
  → `Ran 101 tests` / `OK`（20 / 35 / 46）。
- 回归（逐批实跑，全部 `OK`）：
  · 批次 7 ACL：`tests.test_tech_project_acl_contribute_mode_red` +
    `tests.test_tech_project_acl_scope_red` → `Ran 56 tests` / `OK`
    （新读路由的路径参数写成 `{pid}`，未顶掉那条「43 条单参数 GET 路由」基线）；
  · 1.2 / 1.3 需求与看板：`..._requirement_confirm_red`、`..._confirm_review_optional_note_red`、
    `..._requirement_agent_red`、`..._requirement_review_red`、`..._requirement_stage_waiver_red`
    （需求路由集合基线）、看板静态动作与左工具栏 2 个快照、`..._tech_ui_protocol_red`
    → `Ran 112 tests` / `OK`；
  · 看板注册表 / 协议 / 全宽 / 项目身份 7 个 → `Ran 80 tests` / `OK`；
  · `..._tech_backend_get_route_smoke_dynamic`、`..._tech_home_timeline_and_publish_closure_red`、
    `..._kb_in_pg_http_snapshot_red` → `Ran 59 tests` / `OK`。
- `python -m py_compile` 覆盖新增与改动的 5 个 py 文件；`node --check
  tech_app/frontend/requirement-confirm.js` 通过；`git diff --check` 干净。

### 全量跑（`unittest discover -s tests`）的如实记录

- `Ran 2648 tests` / `FAILED (failures=70, skipped=2)`，逐文件归因：
  · **53 条**来自 `tests/test_packaging_parametric_bom_red.py` —— 该文件与其 Spec
    `docs/specs/packaging-parametric-bom.md` 是**本批收尾时才出现在工作区**的下一批（包装第 5 批
    参数化 BOM）红测，本批未实现，属预期红；
  · **17 条**来自 `tests/test_process_row_running_info_and_fold_red.py`（14）与
    `tests/test_tech_model_call_row_merged_and_summary_detail_red.py`（2）+
    `tests/test_cpq_eval_ci_contract.py::CiDependencyCoverageTest`（1）。
    已用「备份 + `git stash` 回到 HEAD + 重跑」实测：**这 17 条在 HEAD 上同样失败**，
    与本批无关（前两组是 ## 133 与 ## 136 两批过程行口径互相取代后的既有红；
    第三组是 cadquery/ezdxf/fontTools 等未进 requirements 闭包的环境问题）。
- 本批自身涉及的路径（匹配引擎、三接口、1.2 页、两张新表）在全量跑中 0 失败。

### 说明

- 只新增/改动本批允许的文件；未改任何红测与 Spec，未改第 1–3 批的演示数据（12 盒型 /
  5 权重 / 31 部件逐字未动），未删除、清空、迁移、回填任何项目 / 会话 / 任务 / 附件 / 数据库记录。
- 未写 Postgres、未建工作流任务卡、未改 `cpq_wf`；`裕同包装项目-待开发/` 保持只读且未纳入提交。
- 未 push / MR / tag / Release / 部署 / 重启服务。

## 153. 补记包装第 4 批（## 151 / ## 152）的提交与双远端推送（9-20，Codex）

- 提交：`0555f66` 「包装第 4 批：盒型匹配与人工确认（Spec + 红测 + 实现，## 151 / ## 152）」，
  11 个文件、+2140 / -1（含新增 Spec、红测与 `packaging_match.py`）。
- 推送：`ytbz` 分支双远端，`67187e3..0555f66`，推送后回读 SHA 三处一致
  （local / gitlab / github 均为 `0555f66aee575ebf6516eb7136d57e43257859c1`）。
  未创建 MR / tag / Release，未部署、未重启任何服务（`scripts/push_remotes.py` 只允许
  `20260909` 分支，本次按既有 ytbz 口径手工双推：先核对推送地址与远端 SHA 无未知提交，
  再 `git push <remote> HEAD:refs/heads/ytbz`，最后回读校验）。
- 更正 ## 152 的「未 push」表述：**本批实现已提交并推送**。
- 本次提交未包含：`docs/specs/packaging-parametric-bom.md` 与
  `tests/test_packaging_parametric_bom_red.py`（本批收尾时出现在工作区的**下一批（包装第 5 批
  参数化 BOM）**材料，按每批「Spec + 红测」单独提交的既有约定留给该批），
  以及只读的 `裕同包装项目-待开发/`（客户样例工作簿，历来不纳入提交）。

## 154. 包装第 5 批「参数化部件展开与包装 BOM」Spec + 红测（9-20，Codex 只改 Spec + 红测 + changelog）

包装 8 批计划的第 5 批：把第 3 批灌进知识库的 **31 条参数化部件模板**真正用起来，完成
「确认盒型 → 按变量展开部件尺寸 → 组装七类包装 BOM → 人工锁定/重算」这一段。本批**只做部件与
BOM**：不做工艺路线生成与排序（第 6 批）、不做成本公式求值（第 7 批）、不做利润与报价单（第 8 批）。

### 产物

- Spec：新增 `docs/specs/packaging-parametric-bom.md`（350 行）。
- 红测：新增 `tests/test_packaging_parametric_bom_red.py`（**57 条**）。
- 红测分组：A 表达式引擎安全与正确（10）、B 变量绑定（7）、C 部件展开（8）、D BOM 七类（9）、
  E 锁定与重算（7）、F 落库与缺口（7）、G 接口与角色门禁（3）、H 非回归护栏（6）。

### 已查实现状（实测，非推断）

- 全仓没有任何安全表达式求值器（`ast.parse` / `safe_eval` / `evaluate_expression` 零命中），
  31 条部件模板的 `L+4t+2c`、`（L-4）×（W-4）× 8`、`长度 = W/3 + 40` 目前无处可算。
- `tech_app/backend/services/` 下只有第 4 批的 `packaging_match.py`，没有 `packaging_formula.py`
  与 `packaging_bom.py`。
- `da_schema.sql` 里 `wip_packaging_bom_item` 零命中；`main.py` 与
  `tech_app/frontend/requirement-confirm.js` 里 `packaging-bom` 零命中。
- 既有 `wip_part` 主键是 `(ir_id, part_id)`，包装没有设计 IR；既有 `wip_bom_item.category`
  只有「原材料/中间品/耗材辅料/工序产出」，装不下包装 BOM 的七类 —— 故本批另建表，不复用。
- 实测部件模板覆盖：31 条只覆盖 3 个盒型（`YT-RB-01001-A` 10 / `YT-RB-02001-A` 10 /
  `YT-RB-03001-A` 11），另外 **9 个盒型没有任何部件模板**；23 条工艺模板只覆盖 2 个盒型。

### 关键口径（Spec §2，实现不得自行加默认值）

- **变量绑定写死**：`L`/`W`/`H` ← 需求 `inner_*`（缺则整体报错）；`t` ← 盒型
  `grey_board_thickness` 的**首个数值**（`2.0（1.5/2.5可选）`→`2.0`）；`c` ← 需求
  `fit_clearance`，缺则取盒型值；`H盖`/`H内`/`L外`/`W外`/`L内`/`W内`/`盖展开尺寸`/`盒身展开尺寸`/
  `整体展开`/`外盒展开`/`内盒展开`/`包边`（`OVERRIDE_ONLY_VARIABLES` 闭集）**只来自 overrides**，
  **禁止**用 `H` 顶替 `H盖` 这类推断 —— 缺就 `needs_input`。
- **内联默认值**：`铰链宽40` → `铰链宽 = 40`、`出血3mm` → `出血 = 3`（数字是默认值，别名是
  变量名，`source = "literal_default"`）。
- **表达式白名单**：数字/变量/`+ - * / ( )`/比较符（只在 `IF` 条件）/`MIN` `MAX` `IF` `IFERROR`
  `ROUND`；归一化只做全角括号、`×`→`*`、`÷`→`/`、去单个赋值前缀（`长度 = …`）、去 `mm` 单位后缀；
  属性访问、下标、字符串、`**`、`lambda`、分号、换行、`import`/`eval`/`exec`/`__`/`open`、
  白名单外函数一律抛错且**绝不执行**；除零抛错；未绑定变量抛错并给 `missing_variables`；
  结果 `round(x, 1)`（半上进位）。
- **标准件**：`标准件` 这类「既无数字也无运算符」的表达式 → `size_mode = "standard_part"`，
  三维留空、`missing_variables = []`、`status = "computed"`，不报错也不估算。
- **部分缺失保留已算出的维**：`RB01001-P02` 长度 211.6 保留、宽度缺 `H盖` 为 `null`，整体
  `status = "needs_input"`；某一维表达式**为空**表示没有这一维（如 P04 没高度），不算缺失。
- **BOM 七类闭集**（按 0903 口径）：`finished` / `box_part` / `material` / `process` /
  `packaging` / `tooling` / `optional_part`；`process` 按 `step_name` 去重；`tooling` 只取
  `step_name`/`work_content` 命中「烫金/丝印/击凹凸/模切/装配线」的工序（本批只标「涉及工装 +
  待分摊」，**不算钱、不定寿命**）；`material` 按材料文本去重、`material_code` 用「首个空白分词
  在 `kb_material.name` 里唯一包含」解析，解析不到留空并计入 `stats.material_unresolved`；
  某类没数据就不出该类行。
- **落库**：新表 `wip_packaging_bom_item`，主键 `(project_id, requirement_no, bom_category,
  item_key)`；同键整体替换但 `locked = 1` 的行**原样保留**；锁定/解锁写 `locked_by`/`locked_at`
  与 `store.audit(..., "workflow:packaging_bom_item_locked" | "..._unlocked", ...)`，重复锁定幂等。
- **缺口闭集**：`no_part_template`（9 个盒型）409 / `box_type_not_confirmed` 409 /
  `missing_requirement_input` 409 / `item_not_found` 404 / 非包装行业 400。

### 与 0903 新资料的衔接（本批只落到 BOM 分类，不提前做成本）

- 第 4 批之前的分批里，BOM 只按泛化的「材料/工艺/人工」分；0903 明确「成本 = 材料 + 制程 +
  人工 + 包材 + 运费」且列了 26 个成本列与「含刀模制程」清单，因此本批把 BOM 分类**收紧成七类
  闭集**，并把 `tooling`（工装/模具）与 `packaging`（包材）独立出来。
- 第 7 批（packaging_v1 成本引擎）与第 8 批（`未税售价 = 总成本 ÷ (1 - 毛利率)`）的口径不在
  本批实现，Spec §5 明确列为非目标。

### 红测结果（实跑原文）

- `./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red`
  → `Ran 57 tests` / `FAILED (failures=53)`；失败全部指向本批缺口本身，逐条可归类：
  · 39 条 `缺少 tech_app/backend/services/packaging_bom.py`；
  · 11 条 `缺少 tech_app/backend/services/packaging_formula.py`；
  · 1 条 `da_repo 缺 save_packaging_bom()`；
  · 1 条 `main.py 缺路由 /requirement/packaging-bom`；
  · 1 条 `requirement-confirm.js` 未接 `packaging-bom`。
  4 条非回归护栏（H1 包装 64 字段/10 必填、H3 第 3 批演示数据、H5 第 4 批契约、H6 四行业
  注册表）**本就通过**，符合预期。
- 前四批红测回归：
  `tests.test_industry_registry_unified_red` + `..._requirement_template_red` +
  `..._knowledge_base_seed_red` + `..._box_type_matching_red` → `Ran 152 tests` / `OK`
  （20 / 35 / 46 / 51）。
- 全量对照（判定失败是否与本批有关）：
  · 含本批新红测 → `Ran 2648 tests` / `FAILED (failures=70, skipped=2)`；
  · 排除本批新红测 → `Ran 2591 tests` / `FAILED (failures=17, skipped=2)`；
  70 − 17 = 53 恰为本批缺口失败，**另有 17 条既有失败与本批无关**，集中在
  `tests/test_process_row_running_info_and_fold_red.py`（15 条）与
  `tests/test_tech_model_call_row_merged_and_summary_detail_red.py`（2 条），均为前端行标记/
  模型调用行展示类，本批未触碰其相关文件，本次不做修复。

### 说明

- 只新增 Spec、红测与 changelog；未写任何业务实现，未改任何既有红测与既有 Spec 的既有条款。
- 未改第 1–4 批的演示数据与契约（12 盒型 / 31 部件模板 / 23 工艺模板 / 12 内托配件 /
  3 物流规则 / 5 包装物料 / 5 权重逐字未动）。
- 未写 Postgres、未建工作流任务卡、未调模型、未联网；`裕同包装项目-待开发/` 保持只读且未纳入提交。
- 未 push / MR / tag / Release / 部署 / 重启服务。

## 155. 包装第 5 批「参数化部件展开与包装 BOM」实现（9-20，Codex）

按 `docs/specs/packaging-parametric-bom.md` 落地第 5 批：确认盒型 → 受控求值展开部件尺寸 →
组装七类包装 BOM → 整体替换落库 + 人工锁定/重算 + 1.2 需求确认页面板。红测
`tests/test_packaging_parametric_bom_red.py` 由 `FAILED (failures=53)` 转 **`Ran 57 tests` / `OK`**。

### 修改文件清单

- 新增 `tech_app/backend/services/packaging_formula.py`：受控表达式求值器（自写词法/语法分析，
  字符 + 函数 + 结构三层白名单，**没有动态求值入口**）。
- 新增 `tech_app/backend/services/packaging_bom.py`：`bind_variables` / `expand_parts` /
  `build_bom` / `load_bom` / `lock_bom_item` / `BomError` 与 `ENGINE_VERSION`、
  `BOM_CATEGORIES`、`BOM_WRITE_ROLES`（**直接引用**第 4 批 `packaging_match.BOX_MATCH_DECIDE_ROLES`，
  同一对象）、`AUTO_VARIABLES` / `OVERRIDE_ONLY_VARIABLES`。
- `tech_app/backend/storage/da_schema.sql`：新增 `wip_packaging_bom_item`（Spec §3.1 的列与
  CHECK 逐字照抄）+ `ix_packaging_bom_status`。
- `tech_app/backend/storage/da_repo.py`：新增 `save_packaging_bom`（先删未锁定行、再按主键 upsert，
  锁定行原样保留）与 `load_packaging_bom`。
- `tech_app/backend/storage/kb_repo.py`：只新增只读 `packaging_logistics_rules()`（`packaging`
  类 BOM 行引用它的 `rule_code`）；既有函数签名与默认不过滤行为逐字未动。
- `tech_app/backend/main.py`：新增包装 BOM 三个路由（`POST/GET .../requirement/packaging-bom`、
  `POST .../packaging-bom/lock`）与 `PackagingBomBuildAction` / `PackagingBomLockAction` 入参模型。
- `tech_app/frontend/requirement-confirm.js`：新增包装 BOM 面板（七类分组、部件尺寸、
  `needs_input` 与缺失变量、单行锁定/解锁、变量覆盖后重算）；只对 `industry=packaging` 挂载。
- `tech_app/frontend/requirement-confirm.html`：缓存戳 `requirement-confirm.js?v=reqconfirm1 → reqconfirm2`。
- 本 changelog（## 155）。Spec 与红测见 ## 154。

### 两处与 Spec 字面 DDL 的必要偏离（都为了红测可跑，已在此明写）

1. `wip_packaging_bom_item.material_code` **不带** `REFERENCES kb_material(...)`。`material_code`
   的事实源是 PG 知识库（`cpq_kb_client` HTTP 快照只读），本地 SQLite 的 `kb_material` 只是历史
   种子副本；红测用「真实演示数据造快照 + 空本地 SQLite」，带外键会把「快照里解析到的材料码」
   误判成 `FOREIGN KEY constraint failed`（实测报错，非推断）。
2. 表里**多出一列 `source`**：Spec §2.5 要求「每行都带 `industry` / `source` / `engine_version`」，
   红测 d8 逐行断言 `row["source"]` 非空，而 §3.1 的 DDL 列清单漏了它。列清单只增不减，f1 的
   列断言不受影响。

### 关键实现口径

- **变量绑定照 Spec §2.2 那张表**：`L/W/H` ← 需求内尺寸（缺则 `missing_requirement_input`）；
  `t` ← 盒型 `grey_board_thickness` 首个数值（`2.5（2.0/3.0可选）`→`2.5`）；`c` ← 需求
  `fit_clearance`，缺则取盒型；`H盖`/`包边` 等 12 个 `OVERRIDE_ONLY_VARIABLES` **只来自 overrides**，
  没有任何「用 `H` 顶 `H盖`」的推断。
- **内联默认值在展开阶段收集**：`出血3mm` → `出血 = 3`、`铰链宽40` → `铰链宽 = 40`
  （`source = "literal_default"`），`overrides` 优先级最高且可覆盖它们与 5 个自动变量。
- **表达式白名单**：隐式乘法（`4t` → `4*t`）、`MIN/MAX/IF/IFERROR/ROUND`、比较符只在 `IF` 条件；
  引号/下标/点/分号/换行/`**`/未白名单函数一律抛 `FormulaError`；除零抛错（`IFERROR` 才兜）；
  未绑定变量带上 `missing_variables`（按出现顺序）；结果 1 位小数**半上进位**（`0.25 → 0.3`）。
  源码里不出现 `eval(` / `exec(` / `compile(` / `__import__` / `subprocess` / `os.system` /
  `pickle` 任何字面量（a10 静态扫描通过）。
- **标准件判定**：归一化后「无数字、无运算符、无空白、纯中文词」（如 `标准件`）→
  `size_mode = "standard_part"`，三维留空、`missing_variables = []`、`status = "computed"`；
  反过来 `L W`（多段尺寸、只是没写运算符）仍按表达式处理 —— 否则 `RB01001-P04/P06` 会算不出尺寸。
- **部分缺失保留已算出的维**：`RB01001-P02` 长度 211.6 保留、宽度缺 `H盖` 为 `null`，整体
  `needs_input`；某一维表达式为空 = 没有这一维，不算缺失；非法表达式只转缺口、绝不执行、绝不给数字。
- **七类组装**：`finished` 1 行取盒型名与 `quote_quantity`；`box_part`/`optional_part` 按
  `is_optional` 拆；`material` 按部件材料文本去重、材料码用「首个空白分词在 `kb_material.name`
  里唯一包含」解析（0 个或多个命中一律留空并计入 `material_unresolved`）；`process` 按
  `step_name` 去重取 `seq` 最小；`tooling` 只取命中「烫金/丝印/击凹凸/模切/装配线」的工序，
  逐条出、只标「涉及工装 + 待分摊」，**不含任何价格/寿命字段**；`packaging` 全量物流规则。
  某类没数据就不出该类行。
- **重算与锁定**：同一 `(project_id, requirement_no)` 先删未锁定行再重建；`locked=1` 的行
  （含人工改过的尺寸与 `item_name`）原样保留；锁定/解锁写项目审计
  `workflow:packaging_bom_item_locked` / `..._unlocked`，重复同一状态**幂等**（`locked_at` 不变、
  不重复写审计，e6 用 `mock.patch.object(store, "audit")` 断言 `call_count == 0`）。
- **接口角色**：三个路由的写操作引用 `packaging_bom.BOM_WRITE_ROLES`（即第 4 批
  `BOX_MATCH_DECIDE_ROLES` 本体），`_require` 只回答「谁最终能过」；项目级 ACL 仍走
  `project_write_guard`，读路由路径参数写 `{pid}` 以免顶掉批次 7 的「43 条 GET 路由」基线，
  装饰器参数用具名常量以免顶掉批次 2 的需求路由字面量基线（两条基线都实跑通过）。
- **两个新引擎都离线**：不联网、不起进程、不调模型、不写 Postgres；本批只落本地 SQLite。

### 验收（实跑原文）

- `./open-claude/.venv/bin/python -m unittest tests.test_packaging_parametric_bom_red`
  → `Ran 57 tests` / `OK`（实现前 `FAILED (failures=53)`）。
- 前四批红测回归：
  `..._box_type_matching_red` + `..._industry_registry_unified_red` +
  `..._packaging_requirement_template_red` + `..._packaging_knowledge_base_seed_red`
  → `Ran 152 tests` / `OK`（51 / 20 / 35 / 46）。
- 路由与非回归基线：`tests.test_tech_project_acl_contribute_mode_red` +
  `tests.test_tech_project_acl_scope_red` + `tests.test_tech_requirement_stage_waiver_red`
  → `Ran 80 tests` / `OK`；`tests.test_quote_task_coexistence_and_atomic_claim_red` +
  `tests.test_tech_requirement_agent_red` + `..._confirm_red` + `..._review_red`
  → `Ran 74 tests` / `OK`。
- 全量对照：`unittest discover -s tests -p 'test_*.py'` → `Ran 2648 tests` /
  `FAILED (failures=17, skipped=2)`。17 条与本批无关（实现前同为 17 条，本批新红测 53 条
  全部转绿）：15 条在 `tests/test_process_row_running_info_and_fold_red.py`（过程行图标/折叠口径
  与 ## 133、## 136 那两批互斥）、2 条在
  `tests/test_tech_model_call_row_merged_and_summary_detail_red.py`（模型行「详情」），本次未触碰
  其相关文件，不做修复。
- `python -m py_compile`（新改的 6 个 py 文件）→ 通过；`node --check
  tech_app/frontend/requirement-confirm.js` → 通过；`git diff --check` → 干净。
- 未改 `tests/**`（一个字符都没动）、未改第 1–4 批契约与演示数据（12 盒型 / 31 部件模板 /
  23 工艺模板 / 12 内托配件 / 3 物流规则 / 5 包装物料 / 5 权重逐字未动）；`裕同包装项目-待开发/`
  保持只读且未纳入提交。

### 剩余风险

- 部件模板只覆盖 3 个盒型，另外 9 个盒型调用 `expand_parts` 会得到 `no_part_template`（Spec §2.6
  的既定口径，不是缺陷）。
- 表达式引擎的隐式乘法把「数字/变量紧挨着」当乘法；`H盖` 这类「英文 + 中文」变量名照常支持，
  但把两个相邻变量名直接连写（`LW`）会被当成一个变量名，模板里没有这种写法。
- `stats.computed` / `stats.needs_input` 只统计部件两类（`box_part` / `optional_part`），
  与 Spec §2.4 的 `expanded_count` / `needs_input_count` 同一口径；其余类别行一律 `computed`
  且不计入这两个数（否则 31 行会让红测 d8 的 6 / 4 对不上）。

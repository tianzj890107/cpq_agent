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

# 规格：包装知识库权威数据入库与 34 上线预检

批次：新五批（上线闭环）**第 2 批**。红测：`tests/test_packaging_kb_authoritative_rollout_red.py`。

前置（已完成，不在本批范围）：`cpq_kb`（PG DDL + 整包快照）、`da_schema.sql`、`da_seed_packaging.py`
（演示数据）、`scripts/import_da_kb_to_pg.py`（`da.db` → PG，默认 dry-run）、
`tech_app/tools/extract_packaging_rules.py`（0903 工作簿 → 规则快照对账）与
`packaging_cost.py` 的「只认 `review_status='reviewed'`」都已在仓库里。

**本批目标**：把包装知识库在 34 上**真正建成且可被消费**，并把「演示数据 / 权威规则 / DWG 人工确认
样本」三类数据的分层、追溯与上线预检固化成可执行口径。

---

## 1. 现状取证（实测）

1. **34 上表根本不存在**：线上报 `relation "cpq_kb.kb_packaging_box_type" does not exist`，
   包装知识库检索 503，`4.1` 两个零件成本全部失败。本地代码里有 DDL 与 seed，**但没有部署预检**，
   缺表也能上线。
2. **三处不一致（本地已实测）**：`da_schema.sql` 有 **29** 张 `kb_*` 表，`cpq_kb.KB_TABLES` 只有
   **27** 张 —— 只差这两张，且两张都是包装表：

   ```
   in sqlite not in pg: ['kb_packaging_cost_content', 'kb_packaging_tooling_rule']
   ```

   `kb_repo.py:928` / `:936` 正是从快照里读这两张表（`_table("kb_packaging_cost_content")` /
   `_table("kb_packaging_tooling_rule")`），`da_seed_packaging.py` 里也有对应 seed 行；但
   `cpq_kb.py` 全文 `grep -c kb_packaging_cost_content` → **0**，`kb_packaging_tooling_rule` → **0**。
   → 这两张表**永远进不了 PG 快照**：本地/测试（SQLite 直读）有值，34（快照路径）永远空。
   这正是包装第 3 批 Spec §1.1 警告过的「本地有、快照没有」静默缺口。
3. **演示数据无法与权威数据区分**：9 张包装表只有 `source` / `version` / `status` 列，
   没有「这条是演示数据还是权威规则」的可判定字段；seed 出来的 12 条盒型、31 条部件、23 条工艺、
   12 条内托一旦灌进生产，就会被当成真实可报价盒型推荐给客户。
4. `kb_packaging_cost_formula` 已有 `review_status`（`CHECK IN ('draft','reviewed','retired')`），
   成本引擎也确实忽略非 `reviewed` 行 —— 这是唯一一条已经落地的分层，本批把它推广到其余包装表。
5. 本地相关红测当前全绿（实测）：`test_kb_in_pg_http_snapshot_red`、
   `test_packaging_knowledge_base_seed_red`、`test_packaging_cost_engine_red` 等 —— 本批**不改**它们的口径。

---

## 2. 三处一致（表结构契约）

**同一张 `kb_packaging*` 表必须同时出现在三处**，缺一处即视为未完成：

| 位置 | 作用 |
| --- | --- |
| `tech_app/backend/storage/da_schema.sql` | SQLite 表结构（种子与导入源） |
| `cpq_kb.py` 的 `_DDL_TEMPLATE` | PG 建表（`ensure_schema()` 执行） |
| `cpq_kb.py` 的 `KB_TABLES` + `KB_KEYS` | 整包快照的表清单与幂等 upsert 主键 |

要求：

1. `KB_TABLES` 必须覆盖 `da_schema.sql` 里全部 `kb_*` 表（本批补 `kb_packaging_cost_content`、
   `kb_packaging_tooling_rule`）；
2. `KB_KEYS` 必须覆盖全部 `KB_TABLES`（现状已满足，作为护栏）；
3. **既有 27 张表的相对顺序不得变动**（前 20 张是冻结的快照契约），新增只能追加；
4. `_DDL_TEMPLATE` 必须为 `KB_TABLES` 里每一张表提供建表语句；
5. `tech_app/backend/storage/kb_repo.py` 读到的 `kb_*` 表名、`da_seed_packaging.py` 写入的
   `kb_packaging*` 表名，都必须是 `KB_TABLES` 的子集（不允许「代码在读、快照没有」）。

---

## 3. 数据分层（三类数据不得混用）

新增可判定字段（9 张包装表统一）：

```sql
source_type   text NOT NULL DEFAULT 'unknown'
              -- CHECK (source_type IN ('demo','workbook','dwg_confirmed','unknown'))
source_ref    text   -- 权威规则的来源坐标：工作簿 / Sheet / 单元格 / 规则编号
source_sha256 text   -- DWG 人工确认样本：原始图纸 sha256
parser_version text  -- DWG 人工确认样本：解析器（cad_converter / CAD IR）版本
confirmed_by  text   -- DWG 人工确认样本：人工确认人
confirmed_at  text
```

`cpq_kb` 导出常量（唯一清单，代码里不得再抄一份）：

```python
SOURCE_TYPES = ("demo", "workbook", "dwg_confirmed", "unknown")
REVIEW_STATUSES = ("draft", "reviewed", "retired")
```

分层口径：

| `source_type` | 含义 | 能不能进正式测算 |
| --- | --- | --- |
| `demo` | 演示 / 测试基线（`礼盒盒型库_数据样例.xlsx` 口径） | **不能**；可以被读、被展示，但页面/接口必须标出「演示数据」 |
| `workbook` | 0903 等权威工作簿拆出的规则（`source_ref` 必填） | 能，且必须 `review_status='reviewed'` |
| `dwg_confirmed` | 从真实 DWG 解析 + 人工确认的盒型/零件样本 | 能，且必须带 `source_sha256` / `parser_version` / `confirmed_by` |
| `unknown` | 未分类（历史入库行） | 不能；生产预检据此阻断 |

标注规则：

- `da_seed_packaging.py` 写入的演示行一律 `source_type='demo'`；
- `seed_packaging_cost_rules()`（规则快照导入，`source='packaging_rules_json'`）一律
  `source_type='workbook'` + `review_status='reviewed'` + `source_ref` 逐条来自快照；
- 业务人工维护的行（`source` 不是规则快照）**永不覆盖**（现状已满足，作为护栏）；
- 演示数据**不得静默进入正式测算**：`source_type='demo'` 的行在成本/匹配里必须被标记或忽略，
  不得当作权威值参与报价。

---

## 4. 导入器与版本 / 回滚

1. `scripts/import_da_kb_to_pg.py` 保持「默认 dry-run、`--confirm` 才写库」；
2. 幂等：按 `KB_KEYS` upsert，连续两次导入行数不变，`kb_version` 只在确有变化时 `+1`；
3. **导入前快照**：新增只读导出入口（`cpq_kb.export_snapshot()` 或等价 CLI），把当前
   `cpq_kb` 全表落成 JSON（含 `kb_version`），用于回滚与差异比对；导出**只读**，不写库；
4. 未知表 / 未知列必须报错（不许静默跳过），导入报告逐表给行数。

---

## 5. 上线预检（新增，缺表或缺权威数据即阻断上线）

新增 `tech_app/tools/kb_deploy_preflight.py`，**判定与 IO 分离**（与 `extract_packaging_rules.py`
同套路，红测直接调纯函数、不连库）：

```python
def preflight(tables: dict, *, env: str = "local", kb_version=None, min_rows=None) -> dict:
    """tables = {表名: [行, …]}（来自 cpq_kb.snapshot()['tables']）。纯函数，不读库不写库。"""
```

返回：

```python
{"ok": bool, "verdict": "go" | "no-go", "env": str, "kb_version": int,
 "missing_tables": [str], "counts": {表名: 行数},
 "provenance": {"demo": n, "workbook": n, "dwg_confirmed": n, "unknown": n},
 "problems": [{"code": str, "table": str, "message": str}]}
```

`provenance` 与 `unclassified_rows` **只统计关键包装表**（`PACKAGING_TABLES`）的行；
其余 `kb_*` 表没有 `source_type` 列，不得因此被判成 `unknown`。

判定（任一成立即 `no-go`）：

| code | 条件 |
| --- | --- |
| `missing_tables` | `KB_TABLES` 里有表不在 `tables` 中（缺表就是缺表，不许当空表） |
| `empty_required_table` | 关键包装表（7 张 `kb_packaging_*` 主体表）行数为 0 |
| `kb_version_missing` | `kb_version` 为 `None` 或 `0` |
| `demo_only` | `env == "production"` 且任一关键表**全部**是 `source_type='demo'` |
| `unclassified_rows` | `env == "production"` 且存在 `source_type='unknown'` 的行 |

CLI：`python tech_app/tools/kb_deploy_preflight.py --env production [--json] [--min-rows kb_packaging_box_type=12]`，
用 `cpq_kb.snapshot()` 取数（**只读，绝不写库**）；`verdict == "go"` → 退出码 0，否则非零。

---

## 6. 部署文档

`DEPLOYMENT.md` 新增「知识库（cpq_kb）上线」小节：建表（`ensure_schema()`）、
导入（`scripts/import_da_kb_to_pg.py` 先 dry-run 再 `--confirm`）、
**预检**（`kb_deploy_preflight.py --env production`）、回滚（导入前导出快照 + 恢复步骤）。
（本节与第 1 批的「DWG 转换器」段并列，互不覆盖。）

---

## 7. 红测

`tests/test_packaging_kb_authoritative_rollout_red.py`，全部离线（只读源码 + 纯函数 + 临时 SQLite）：

| 组 | 覆盖 | 条数 |
| --- | --- | --- |
| A 三处一致 | §2 | 5 |
| B 数据分层 | §3 | 6 |
| C 导入器 / 版本 / 回滚 | §4 | 4 |
| D 上线预检与文档 | §5 / §6 | 5 |

运行：

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_kb_authoritative_rollout_red -v
```

实现前实测：

```
Ran 20 tests  FAILED (failures=15)
```

红点 15 条：A1（`KB_TABLES` 缺 `kb_packaging_cost_content`/`kb_packaging_tooling_rule`）、
A3（PG DDL 缺这两张表）、A4（`kb_repo` 在读快照里没有的表）、A5（seed 写的表不在快照清单）、
B1（无 `SOURCE_TYPES`/`REVIEW_STATUSES`）、B2（两张 schema 都没声明 `source_type`）、
B3（演示行未标 `demo`）、B4（规则快照行未标 `workbook`）、B5（缺 DWG 确认样本列）、
C3（无导入前快照/回滚入口）、D1–D5（无预检工具与文档小节）。

绿 5 条护栏：A2（`KB_KEYS` 覆盖 + 既有 27 张相对顺序）、B6（成本引擎只认 `reviewed`）、
C1（导入器默认 dry-run）、C2（`kb_version` 只在 `changed` 非零时递增）、
C4（导入报告逐表行数）。

---

## 8. 禁止事项

- 不把演示数据当权威：不改 `source_type='demo'` 行的语义，不让它们静默参与正式测算。
- 不改既有 27 张表的名称与相对顺序、`KB_KEYS` 既有主键、既有 `kb_version` 递增口径。
- 不改 `packaging_cost.py` 的「只认 reviewed」口径、不改成本公式与权重。
- 不改既有红测（`test_kb_in_pg_http_snapshot_red`、`test_packaging_knowledge_base_seed_red` 等）。
- 不把客户工作簿原文件或敏感原文入仓库；不装新依赖。
- **本规格不授权部署**：不连 34、不写生产库、不重启服务。

---

## 9. 与后续批次的接口

- 第 4 批（包装参数/BOM/工艺/成本闭环）只消费 `source_type IN ('workbook','dwg_confirmed')`
  且 `review_status='reviewed'` 的行；演示数据只能用于演示与回归。
- 第 5 批（终验）用本批的预检输出作为「知识库已上线」的证据之一。

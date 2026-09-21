# 包装知识库：样例工作簿 → 权威数据 → 生产可用的单一通路

Spec 版本：1 · 状态：待实现（红测已就位）

## 1. 背景：为什么"一个零件都比对不出来"

不是没写 seed，是**三层各自断了一次**（全部实测）：

1. **样例早已被固化成代码**。`tech_app/backend/storage/da_seed_packaging.py` 把
   `裕同包装项目-待开发/礼盒盒型库_数据样例.xlsx` 的 4 个 Sheet 逐条写进模块常量：
   `BOX_TYPES=12`（01盒型）、`PART_TEMPLATES=31`（02-盒型-部件构成）、
   `PROCESS_TEMPLATES=23`（03-盒型+部件-工艺路线与工时）、`ACCESSORIES=12`（04-内托与配件库），
   另有 `MATERIALS=5`、`COST_RATES=8`、`COST_FACTORS=5`、`LOGISTICS_RULES=3`、
   `MATCH_WEIGHTS=5`、`COST_CONTENTS=11`、`TOOLING_RULES=5`。Sheet 名与代码里的
   `SOURCE_*` 常量逐字对得上。
2. **seed 从未在本地落库**。实测 `tech_app/tech_data/da.db` 里**没有**
   `kb_packaging_*` 任何一张表，`kb_material` / `kb_material_price` / `kb_cost_rate` /
   `kb_cost_factor` 全是 0 行 —— 说明 `python -m backend.storage.da_seed_packaging`
   在本机从未跑过。
3. **运行时读的不是 sqlite，是 PG 快照**。`storage/kb_repo.py:70` →
   `services/cpq_kb_client.py:fetch_snapshot()` → `GET /wf/tech/kb/snapshot`
   （`X-Internal-Token`），缺令牌直接抛 `KbUnavailable`，**明确拒绝静默降级成空库**。
   而 PG `cpq_kb` 现状：表建了（30 张，含 9 张 `kb_packaging_*`）、`kb_version=1`、
   **数据未灌**（预检 7 项 `empty_required_table`）。
   所以 `packaging_match.match_box_types()` 的候选来自 `kb_repo.packaging_box_types()`，
   库里没有行 → `candidates=[]`、`new_tooling_reason="no_box_type"`、`needs_new_tooling=True`，
   **一个盒型/部件都匹配不出来**。
4. **就算灌进去，也会被生产门禁挡住**。`_packaging_row()` 对 seed 行缺省写
   `source_type="demo"`；`kb_deploy_preflight.py` 的 `demo_only` 判定
   （`env=production` 且任一关键表全是 demo）即 no-go。而把 demo 升成权威**没有任何
   代码路径**（`cpq_kb.SOURCE_TYPES = ("demo","workbook","dwg_confirmed","unknown")`
   里 `workbook` 只被成本公式快照用过）。结果是死循环：seed 存在 → 灌进去判 demo_only →
   没人灌 → 预检报空表。

## 2. 目标

把"样例 → 权威 → 生产可用"做成一条可执行、可审计、可复现的通路，并让
"没有权威来源"与"库里没有数据"在产品上可区分。

## 3. 契约

### C1 通路写死在文档与工具里（三层，一份）

```
da_seed_packaging（sqlite da.db） → import_da_kb_to_pg.py（→ PG cpq_kb）
                                  → 运行时 cpq_kb_client HTTP 快照 → kb_repo → packaging_match/bom/route/cost
```

任何一层的位置、命令、校验方式都必须写进本 spec 的"运维步骤"小节（见 §5），
不得只存在于某个人的终端历史里。

### C2 权威升格是纯函数，单向、带出处

新增（`cpq_kb.py`）：

```python
promote_rows(rows, *, target="workbook", authority) -> list[dict]
```

- 只允许 `demo → workbook`；已是 `workbook` 幂等；`unknown` / 其它值抛 `ValueError`；
- `authority` 必须提供 `{workbook, sheet, owner, decided_at, sha256}`，缺任一项抛错；
- 只改来源类列（`source_type` / `authority_ref`），**不得**改动任何业务值
  （`expression`、`minimum_charge`、尺寸区间、工时等），逐字保持；
- `authority` 一旦写入即不可被后续 seed 覆盖（`overwrite=True` 也只能刷新业务列）。

### C3 出处声明落盘，可机检

新增 `tech_app/agent_knowledge/provenance/packaging_sources.json`，为每个
`da_seed_packaging.SOURCE_*` 常量声明：

```json
{"source": "裕同包装项目-待开发/礼盒盒型库_数据样例.xlsx / 01盒型",
 "workbook": "礼盒盒型库_数据样例.xlsx", "sheet": "01盒型",
 "rows": 12, "owner": "", "decided_at": "", "sha256": ""}
```

红测断言：`da_seed_packaging` 里每个 `SOURCE_*` 都能在该 JSON 找到同名条目，
且 `rows` 与 seed 实际条数一致。**这是为了消灭"声明指向不存在的工作表"这类失真**
（现状 `SOURCE_COST = "报价逻辑-0903.xlsx / 包装成本口径"` 等 4 条指向的 Sheet 名
在该工作簿里并不存在 —— 已实测：该工作簿 12 个 Sheet 里没有这些名字）。

### C4 预检从"一刀切禁 demo"改成"必须申报权威"

`kb_deploy_preflight.py`：

- 新增 `authority_missing`：关键表存在 demo 行且这些行没有 `authority_ref` → no-go；
- 保留 `demo_only`：关键表**全部**是 demo 且无任何权威申报 → no-go；
- `local` / `ci` 环境不因 demo 判 no-go（现状保持）。

### C5 灌库工具支持一步升格，默认仍 dry-run

`scripts/import_da_kb_to_pg.py` 新增：

- `--promote workbook --authority-file <json>`：灌库前按 C2 升格，出处取自 C3 的文件；
- 默认仍是 dry-run；`--confirm` 才写库；
- 结束时打印 `kb_version` 与各包装表的 `source_type` 分布（demo/workbook 各几行）；
- 幂等：连续跑两次，行数与 `kb_version` 不变。

### C6 匹配端到端必须给出候选

快照含 12 条盒型时，`packaging_match.match_box_types(inputs)` 至少 1 条
`status="matched"` 且 `suggested_box_type` 非空；快照为空时保持现状
（`""` + `no_box_type`），但**必须**能区分"库空"与"桥断"（桥断抛 `KbUnavailable`，不降级）。

## 4. 不在本批范围

- 不改 seed 的业务数值（尺寸区间、工时、费率、公式）；
- 不改 `packaging_match` 的五维算法与权重；
- 不替业务决定"哪些行算权威"——只提供机制与出处登记，签字由业务给。

## 5. 运维步骤（实现后必须可照抄执行）

1. 本地固化：`cd tech_app && python -m backend.storage.da_seed_packaging --packaging-only`
   （幂等；跑完本地 `da.db` 应有 `kb_packaging_*` 表且 12/31/23/12 行）；
2. 出出处文件：把工作簿的 `workbook/sheet/rows/sha256/owner/decided_at` 填进
   `tech_app/agent_knowledge/provenance/packaging_sources.json`；
3. 灌生产：`python scripts/import_da_kb_to_pg.py --source tech_app/tech_data/da.db
   --promote workbook --authority-file tech_app/agent_knowledge/provenance/packaging_sources.json --confirm`；
4. 门禁：`python tech_app/tools/kb_deploy_preflight.py --env production --min-rows
   kb_packaging_box_type=12 --min-rows kb_packaging_part_template=31`；
5. 端到端：在报价工作台建一个包装项目，跑盒型匹配，应出现候选而非"未匹配/需补录"。

## 6. 验收标准

`tests/test_kb_authoritative_promotion_red.py` 全绿，且 §5 的第 4、5 步在同一环境实测通过。

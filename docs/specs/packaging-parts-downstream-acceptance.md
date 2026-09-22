# 包装图纸零件：下游闭环的验收、门禁与能力声明（第 5 层）

血缘：收口第 1～4 层（真实轮廓 → 可选中面板 → 工艺/成本 → 3D 挤出）。
本层解决**"做完了但说不清到底做到了哪一步"**。

状态：Spec + 红测（已实现）
红测：`tests/test_deploy_isolation_root_red.py` `tests/test_packaging_parts_downstream_gate_red.py`

## 0. 一句话目标

把前四层变成**可验收、可声明、可回滚**：固化质量指标、给一条只读门禁、把能力声明分三级写死，
并把下游连通性纳入部署自检。

## 1. 为什么要单独一层

这一整条链路（## 231 → 本批）已经出现过三次"测试全绿但能力不成立"：

- `test_drawing_board_two_column_parts_and_3d_red` 只按名字断言 `aria-label="3D 视图"`，DWG 项目下
  那块画布其实是空的；
- 零件清单 64 行看着像零件，实测只有 27 种尺寸、18 组重复、闭合数 0；
- 工艺推荐按钮与左栏 64 行自相矛盾（一个说"还没有零件"）。

所以本层不写新能力，只写**度量与门槛**：没有门槛，"做完"永远只是感觉。

## 2. 指标固化（零件文档 `stats`）

`packaging_parts.summarize(doc)` 必须返回（第 1 层已有 `closed_ratio`，其余本层新增）：

| 指标 | 定义 |
| --- | --- |
| `closed_ratio` | `closed_total / part_total` |
| `role_known_ratio` | `role != "unknown"` 的件数 / `part_total` |
| `solid_ok_ratio` | `status == "ok"` 的挤出结果数 / `part_total`（无固体文档时 `0.0`） |
| `processable_ratio` | `processability.ok` 的件数 / `part_total` |
| `size_source_mix` | `{"closed_outline": n, "component_bbox": n, "dwg_outline": n}` |

`part_total = 0` 时所有比值一律 `0.0`（不许 `null`、不许抛错）。

## 3. 真实样本门槛（红线，两份样本都跑）

| 样本 | 门槛 |
| --- | --- |
| `酒盒.dwg` | `closed_ratio >= 0.10` |
| `圆盘盒.dwg` | `closed_ratio >= 0.50` 且 `role_known_ratio >= 0.10` |
| 两份 | 各自至少 **1 件** `processability.ok` 且 `extrude` 返回 `ok` |

门槛值写进本 Spec，**改门槛必须改本文件**并说明依据（不许在代码里手调）。

## 4. 只读门禁 `tech_app/tools/packaging_parts_gate.py`

- 用法：`python tech_app/tools/packaging_parts_gate.py --env local|production`
- 输出形状与退出码**与既有 `dwg_deploy_gate.py` 完全一致**：
  `{gate_version, env, items: [...], summary: {ok, fail, manual, skip, acknowledged}, verdict, reasons}`；
  有 `fail` → 非零退出。
- 项（`id` 固定，不许改名）：

| id | kind | 判据 |
| --- | --- | --- |
| `parts_outline_engine` | auto | 第 1 层常量（`LOOP_TOLERANCE_MM` / `OUTLINE_STATUSES` / `SIZE_SOURCES`）在位 |
| `parts_outline_real_sample` | auto | §3 的两样本门槛（本机无样本时 `skip`） |
| `parts_panel_wired` | auto | 第 2 层三件在位：行可点、面板容器、三态文案 |
| `parts_downstream_wired` | auto | 第 3 层：两个下游路由 + `processability` 在位 |
| `parts_3d_wired` | auto | 第 4 层：solid 路由 + 前端复用 `loadSTL` |
| `parts_demo_script` | manual | 演示脚本经用户签字（`--ack parts_demo_script=<用户>`） |

- 门禁**只读**：不连生产库、不写文件、不调模型、不联网。

## 5. 能力声明三级（写进 `DEPLOYMENT.md`）

| 级别 | 名称 | 成立条件 |
| --- | --- | --- |
| L1 | 编排 | DWG → DXF → CAD IR → 语义 → 零件文档，2.1 左栏出清单 |
| L2 | 可信 | L1 + `closed_ratio` 过 §3 门槛 + 角色可识别 |
| L3 | 闭环 | L2 + 可点（面板）+ 可算（工艺/成本过 `processability`）+ 可看（3D 挤出） |

- 未签字前**不得**声明 L3；`DEPLOYMENT.md` 必须把当前级别写在显眼处。
- 每次部署后按门禁实际结果更新级别；级别只升不降（降级必须写明原因）。

## 6. 部署自检扩展

`scripts/deploy_34_bare.sh` 增加一步「下游连通自检」（只读 HTTP，任一项不过非零退出）：

1. `GET /api/projects/{pid}/requirement/packaging-parts` → `built` 与 `stats.part_total`；
2. `GET …/packaging-parts/DWG-P01` → `found` 与 `outline.status`；
3. 若第 2 步 `outline.status == "closed"`：`POST …/DWG-P01/solid` 必须返回 `status ∈ {ok, unsupported}`
   （`unsupported` 是结论，不算失败）。

**样本项目 id 必须由用户提供**（不许脚本自己猜、不许拿生产项目当试验田）；未提供时该步 `skip` 并打印原因。

### 6.1 隔离端到端（不需要项目 id；2026-09-21 补）

第 6 步要一个**真实项目**，所以线上还没人点过"一键解析"时它只能 `skip` —— 那一步证明不了
"这台机器上这条链路真的能跑通"。因此新增第 6b 步，它**不需要任何项目**：

1. 只管把 `DATA_DIR` 指到一个临时目录（`tech_app/backend/config.py` 的
   `os.getenv("DATA_DIR", ROOT/"data")`），在隔离目录里建项目 → 建需求草稿 →
   `packaging_drawing_flow.run_flow()` 跑完整条八步；
2. 读零件文档与单件详情：任一份样本出现「有步骤非 `completed` / 零件 0 件 / 无可算 / 无可挤出」
   即非零退出（`unsupported` 的挤出结论不算失败，与第 6 步同口径）；
3. 跑完删掉临时目录，并**核对 `tech_app/data/*/meta.json` 的数量前后不变** —— 生产数据目录一个
   字节不写；数量变了直接判失败（这条是给"少写一个环境变量前缀就在生产目录里建了项目"兜底的）。

样本仍必须来自 `裕同包装项目-待开发/`（第 5 步已强制两份样本存在）。两步互补：第 6 步证明
"某一个真实项目的数据是对的"，第 6b 步证明"这台机器的链路是通的"。

## 7. 允许修改范围

1. `tech_app/backend/services/packaging_parts.py`：`summarize()` 补 §2 指标（`closed_ratio` 第 1 层已有）。
2. 新建 `tech_app/tools/packaging_parts_gate.py`。
3. `DEPLOYMENT.md`：三级声明表 + 当前级别。
4. `scripts/deploy_34_bare.sh`：新增第 6 步（可 `skip`）与第 6b 步（隔离端到端，不需要
   项目 id、不写生产数据；见 §6.1）。
5. `changelog/changelog_9_21_25.md`：按周记录。

## 8. 禁止事项

- 不许改前四层的口径与红测；
- 门禁不许连生产库、不许写数据、不许把 `manual` 项自动置为 `ok`；
- 不许在没有真实样本证据时把能力声明写成 L3；
- 不许改既有 `dwg_deploy_gate.py` 的输出形状（只允许**新增**项）。

## 9. 红测

`tests/test_packaging_parts_downstream_gate_red.py`（实现前必须失败）：

- A 指标：`summarize()` 含 §2 五个指标；`part_total=0` 时全 `0.0` 不抛错。
- B 门禁脚本：文件存在；`--env` 可跑；输出 JSON 含 `items/summary/verdict`；六项 `id` 齐全；
  有 `fail` 时退出码非零。
- C 门禁只读：源码里不出现生产库连接串 / `psycopg` / 写文件调用。
- D 能力声明：`DEPLOYMENT.md` 含 L1/L2/L3 三级表与当前级别字样。
- E 部署自检：`deploy_34_bare.sh` 含下游自检步骤与 `skip` 分支。
- F 真实样本门槛（本地有样本才跑）：§3 的表格逐项断言。

## 10. 验收命令

```bash
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_downstream_gate_red -v   # 全绿
./open-claude/.venv/bin/python tech_app/tools/packaging_parts_gate.py --env local             # 退出码 0
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_outline_red             # 第 1 层
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_panel_red               # 第 2 层
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_downstream_red          # 第 3 层
./open-claude/.venv/bin/python -m unittest tests.test_packaging_parts_3d_red                  # 第 4 层
```

## 11. 实现期回写（2026-09-21）

1. **`solid_ok_ratio` 的数据来源**：`summarize(doc)` 只拿零件文档，拿不到挤出文档，所以它
   按两处取"这一件挤出结论"：① 行上的 `solid_status`；② 可选参数 `summarize(doc, solids=<挤出文档>)`。
   两处都没有就是 `0.0`（不返回 null、不抛错，§2 那条约束不变）。
2. **门禁的 manual 项不进 `reasons`**：`dwg_deploy_gate.py` 把"人工项未签字"算进 `reasons` →
   `verdict=no_go`。本门禁照它的**输出形状与退出码**做，但 `parts_demo_script` 未签字只体现在
   `summary.manual`（status 仍是 `manual_unacknowledged`，绝不自动置 `ok`），否则 §10 要求的
   "`--env local` 退出码 0"与 B4 的"无 fail → verdict=go"就无法同时成立。
3. **`--env production` 下 skip 一律算 fail**（与既有门禁一致）；本机没有样本目录或**一个可用的 DWG
   转换器都没有**时按 skip 处理，`--env local` 不因此变红，但消息里写清"放上样本 / 装上转换器再跑"。
   **转换器按应用的能力判，不按 PATH 判**：线上 34 只装了 ODA（`DWG_CONVERTER_BINARY`），没有
   libredwg 的 `dwg2dxf`，所以 `parts_outline_real_sample` 优先走 `cad_converter.convert_drawing()`
   （与 8010 同一条链路），只有在应用内转换器不可用/转不动时才回退 `dwg2dxf`。否则门禁在线上会以
   "本机没有 dwg2dxf" 为由 skip → 生产判 fail，把"能力成立"误报成 no-go。门禁专用项目 id
   `packaging-parts-gate`，产物 / 清单 / 审计全部重定向到临时目录，真实项目数据一个字节不动。
4. **当前级别写在 `DEPLOYMENT.md`**：`L2（可信）`；L3 需要人工签字的 `parts_demo_script` +
   两份样本各一件可算/可挤出，未签字不得声明。

## 12. 更正（2026-09-22，`## 300`）：§6.1 第 3 条的"生产数据目录"必须按运行目录解析

§6.1 第 3 条原文写的是"核对 `tech_app/data/*/meta.json` 的数量前后不变"。**这个字面路径是错的**：
`tech_app_launch.py:71` 把 `DATA_DIR` 缺省成 `tech_app/tech_data`（`os.environ.setdefault`），
34 与容器里真正在用的就是它 —— 实测 `tech_app/tech_data` 有 **60 个项目**，而 `tech_app/data`
只有 `cpq-unified-parse` / 几个 `dwg-*` 验证目录，**0 个项目**。

后果：这条安全断言在真机上恒等于 `0 → 0` —— 输出漂亮、看着通过，却正好抓不到它要防的那件事
（"少写一个 `DATA_DIR` 前缀就在生产目录里建了项目"）。同 `deploy-selfcheck-skip-vs-pass.md`
要消灭的失效模式同型：**"没证明"不许长得像"证明了"**。

更正后的口径（`scripts/deploy_34_bare.sh` 第 6b 步已按此实现）：

1. 运行目录按启动器同一口径解析：`DATA_DIR` → `CPQ_DATA_DIR` → `$REPO/tech_app/tech_data`；
2. 隔离前后**两个根都数**（运行目录 + `tech_app/data`），任一变化即失败；
3. 输出里必须写出**被检查的绝对路径**与两边的数量，不能只写"未写入"；
4. 运行目录里 0 个项目时，显式打印「这条断言在新机器上证明不了什么」——不许让它冒充通过。

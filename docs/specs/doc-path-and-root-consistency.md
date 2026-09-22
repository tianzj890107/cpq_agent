# 规格：文档点名的路径与根目录必须真实（全仓对账）

血缘：`spec-status-consistency-repo-wide.md`（同类"文档与事实不一致"的收口，那批收的是**状态行**，
本批收的是**路径与根目录**）；`packaging-parts-downstream-acceptance.md` §6.1 / §12（隔离自检该盯哪个根）。

状态：Spec + 红测（已实现）
红测：`tests/test_doc_path_and_root_consistency_red.py`
依赖：`scripts/deploy_34_bare.sh` 第 6b 步（隔离断言解析运行目录）、`tech_app_launch.py`（`DATA_DIR` 缺省根）。

## 0. 为什么有这一条（2026-09-22 实测，不是推断）

Spec 头写错状态行只是"人以为没做、其实做完了"；**写错路径更糟**：照着文档敲命令的人会直接失败，
或者更坏——敲了个**语法正确但指向错的根**的命令，跑完拿到"恒等于 0 → 0"的结论还以为验证过了
（`## 300` 那一类）。实测扫描 `docs/specs/*.md` + `DEPLOYMENT.md` + `README.md` + `AGENTS.md`
反引号点名的一类路径（`tests/*.py`、`scripts/*.sh|py`、`tech_app/tools/*.py`、`docs/specs/*.md`）：

```
点名处（含重复）          793
去重后的路径              335
不存在的                    3
  ├─ 已被「取代」的历史名      2（chat 系列，Spec 里逐字写着"取代：…"，属实）
  └─ 真错                    1（`test_tech_global_single_primary_by_state_and_nonblocking_notices_red.py`
                                少了 `_and_nonblocking_` —— 该测试文件根本不存在）
```

另外 `DEPLOYMENT.md` 第 6b 步的"没写数据"断言长期只数 `tech_app/data/*/meta.json`，而 34 上真正在
用的运行目录是 `tech_app/tech_data`（`tech_app_launch.py` 的 `DATA_DIR` 缺省根，60 个业务项目全在
那儿），`tech_app/data` 里 0 个项目 —— 断言恒等于 `0 → 0`，看着通过却什么都没证明。
（脚本侧的修正见 `test_deploy_isolation_root_red.py`；本批补**文档侧**。）

## 1. 契约

### 1.1 文档点名的路径必须存在

`docs/specs/*.md`、`DEPLOYMENT.md`、`README.md`、`AGENTS.md` 里，反引号点名且形如
`tests/<..>.py` / `scripts/<..>.sh|py` / `tech_app/tools/<..>.py` / `docs/specs/<..>.md` 的路径，
必须在仓库里真实存在。

**唯一白名单**：`chat-fused-assistant-card-style.md` 里那两条**历史「取代」名**
（`docs/specs/chat-white-bubble-and-expandable-run-progress.md` 与
`tests/test_chat_white_bubble_and_expandable_run_progress_red.py`）。它们是被"取代"的旧名，
Spec 正文逐字记录"取代：…"，**故意**指向不存在的新名。白名单只允许出现在含 `取代` 的行上。

### 1.2 `DEPLOYMENT.md` 的隔离自检必须同时点名两个根

第 6b 步段落必须同时出现 `tech_app/tech_data`（运行目录）与 `tech_app/data`（历史/杂项目录），
并显式写出"只盯后者证明不了什么"这一类判断；不得再出现 `tech_app/data/*` 这种**只数历史目录**的
glob 命令。

### 1.3 `scripts/deploy_34_bare.sh` 第 6b 步按启动器的口径解析运行目录

解析链必须是 `DATA_DIR` → `CPQ_DATA_DIR` → `$REPO/tech_app/tech_data`；两个根都要计数、都要对比；
且脚本 `bash -n` 通过。本批不改脚本（`test_deploy_isolation_root_red.py` 已守），只在文档侧补口径。

## 2. 本批改了什么（逐条可核）

| 文件 | 改动 |
| --- | --- |
| `docs/specs/dwg-semantics-agent-flow.md` | 把不存在的测试名 `…_by_state_and_nonblocking_…` 改成真实文件 `test_tech_global_single_primary_and_nonblocking_notices_red.py` |
| `DEPLOYMENT.md` | 第 6b 步改为"核对**两个根**"并写明"只盯后者等于没证明什么"；示例命令的项目 id 根从 `tech_app/data` 改成 `tech_app/tech_data` |
| `docs/specs/packaging-parts-3d-extrusion.md` | §9 落库根 `tech_app/data/<pid>/` 改成 `tech_app/tech_data/<pid>/`（启动器 `DATA_DIR` 缺省根） |

**有意未动**：`docs/specs/packaging-parts-downstream-acceptance.md` §6.1 里那句错的字面路径与
§12 引用它的更正段 —— §12 已明确"§6.1 原文不动，新段说明字面路径错在哪"，改它会毁掉那条更正记录。

## 3. 不做什么

- 不改 `tests/` 下任何既有文件与断言；
- 不改 `scripts/deploy_34_bare.sh`（脚本侧已由 `test_deploy_isolation_root_red.py` 守）；
- 不为了转绿而删文档里的路径引用（引用必须**改成对的**，不是抹掉）；
- 不把「已被取代」的历史名也一并改掉（那是 Spec 记录本身）。

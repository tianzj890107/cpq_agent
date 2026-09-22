# 规格：样本目录与一次性脚本的归属说明（`.gitignore` + `scripts/README.md`）

状态：Spec + 红测（已实现）
红测：`tests/test_repo_leftovers_red.py`

## 0. 为什么有这一条（实测，不是推断）

2026-09-21 现场：仓库根下长期挂着一个**未跟踪**的一次性脚本 `scripts/tmp_import_dwg_cases.py`
（旧 sqlite 通道）和一个 **3.7 MB 未跟踪样本目录** `裕同包装项目-待开发/`（含 `酒盒.dwg`、
`圆盘盒.dwg` 与三份业务工作簿），而：

- `.gitignore` 里**没有任何**对应条目（`grep 裕同 / tmp_ / 样本` 全空）；
- `scripts/` 下**没有 README**，没人知道这个 `tmp_` 脚本与正式脚本
  （`scripts/import_dwg_quick_quote_cases.py`）是什么关系、能不能删；
- 于是每次 `git status` 都把它们当"待处理"，每次验收都要重新判断"这两个东西该不该进库/该不该删"。

## 1. 契约

### 1.1 `.gitignore` 必须显式覆盖这两类

- 未入库样本目录：`裕同包装项目-待开发/`（客户实样，**不提交**）；
- 一次性脚本：`scripts/tmp_*.py`。

### 1.2 新增 `scripts/README.md`

必须写清三件事：

1. **一次性脚本的地位**：`scripts/tmp_*.py` 是本地一次性脚本，不参与部署、不被任何生产路径
   import、可以随时删除（删除与否由人决定，本批不代删）；
2. **正式入口**：DWG 案例沉淀的正式脚本是 `scripts/import_dwg_quick_quote_cases.py`
   （默认 dry-run、`--confirm` 才写库）；
3. **样本目录**：`裕同包装项目-待开发/` 是客户实样样本，仅用于本地/线上人工验证，
   **不入库、不随部署分发**，且不属于任何自动化测试的输入（需要样本的用例一律显式跳过或走
   `CPQ_DWG_REAL_SAMPLES` 开关）。

### 1.3 生产路径不得依赖 `scripts/tmp_*.py`

任何入库的 `.py` / `.sh`（`scripts/` 与仓库根）都不得 import 或执行 `tmp_` 脚本。

## 2. 红测映射（`tests/test_repo_leftovers_red.py`）

| 组 | 覆盖 |
| --- | --- |
| A | `.gitignore` 覆盖样本目录与 `scripts/tmp_*.py` |
| B | `scripts/README.md` 存在且三件事都写到（一次性脚本 / 正式入口 / 样本目录） |
| C | 没有任何入库脚本引用 `tmp_` 脚本（源码扫描） |

## 3. 非目标

- **不删除、不移动、不提交** `scripts/tmp_import_dwg_cases.py` 与 `裕同包装项目-待开发/`
  —— 历史数据与用户文件一律不动，本批只加"忽略 + 说明"；
- 不把样本目录改造成自动化测试输入；
- 不动 `open-claude/` 与任何第三方目录。

# `scripts/` 目录说明

本目录放**运维 / 数据导入**脚本。三条纪律：默认 dry-run、幂等、不编数据。

## 1. 一次性脚本（`scripts/tmp_*.py`）

`scripts/tmp_*.py` 是**本地一次性脚本**（例如 `tmp_import_dwg_cases.py` 这种旧 sqlite
通道的临时搬运脚本）：

- **不参与部署**：`scripts/deploy_34_bare.sh` 与任何生产路径都不引用它们；
- **不被任何入库代码 import**：仓库里入库的 `.py` / `.sh` 不许依赖 `tmp_*`；
- **可以随时删除**：删除与否由人决定，没有任何自动化流程依赖它们的存在；
- 已在 `.gitignore` 里忽略（`scripts/tmp_*.py`），所以它们不会出现在 `git status` 的待提交清单里。

## 2. 正式入口：DWG 案例沉淀

把两份 DWG 实样（`酒盒.dwg` / `圆盘盒.dwg`）对应的知识库盒型沉淀成标准报价案例，**正式脚本**是：

```bash
# 看一眼要写什么（默认 dry-run，只读库、不写）
./open-claude/.venv/bin/python scripts/import_dwg_quick_quote_cases.py

# 真写（必须显式 --confirm）
./open-claude/.venv/bin/python scripts/import_dwg_quick_quote_cases.py --confirm

# 带业务口径的价格 + 审核状态
./open-claude/.venv/bin/python scripts/import_dwg_quick_quote_cases.py \
    --price 23.40 --cost 18.10 --review-status reviewed --confirm
```

默认 **dry-run**，只有显式加 `--confirm` 才写库；`case_code` 由盒型编码决定，重复执行幂等。

费率侧对应物是 `scripts/import_quick_quote_rates.py`（权威费率导入，同样默认 dry-run、
`--confirm` 才写）。

## 3. 样本目录：`裕同包装项目-待开发/`

仓库根的 `裕同包装项目-待开发/` 是**客户实样样本**（含 `酒盒.dwg`、`圆盘盒.dwg` 与三份业务
工作簿）：

- **不入库**（已在 `.gitignore` 里忽略）、**不随部署分发**；
- 仅用于**本地 / 线上人工验证**；
- **不属于任何自动化测试的输入**：需要真实样本的用例一律显式跳过（`skip`），或通过
  `CPQ_DWG_REAL_SAMPLES` 这类开关显式开启，绝不在 CI 里假设样本存在。

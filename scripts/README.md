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

## 4. 本地测试的临时目录：闸门在 `tests/`，收尾在 `scripts/reclaim_test_tmpdirs.py`

2026-09-24 实测：一次全量（400 个模块）在系统 TMPDIR 里新建 **1371 个临时目录**，攒到
335 349 个条目 / **123 GB** —— 根因不是某条用例忘了删，而是 152 处 `tempfile.mkdtemp`
没有统一出口（只有 38 处自己挂了 `addCleanup`）。现在分两层收：

- **运行期闸门**（`tests/_tmp_guard.py`，由 `tests/__init__.py` 自动安装）：每次
  `python -m unittest tests.xxx` 先在系统 TMPDIR 下建一个 `cpq-testrun-XXXX` 根，把
  `tempfile.tempdir` 与 `TMPDIR` 都指过去，退出时整根删 —— 连测试拉起的子进程建的临时目录
  也在根里。用例自己已经 `addCleanup` 删过的重删是幂等的。
  调试要看现场：`CPQ_TEST_KEEP_TMP=1`。
- **历史垃圾 / 被打断的运行**：`scripts/reclaim_test_tmpdirs.py`（默认只报告，`--apply` 才删）：

  ```bash
  python3 scripts/reclaim_test_tmpdirs.py                      # 报告：多少个、多少 GB、最大的几个
  python3 scripts/reclaim_test_tmpdirs.py --apply              # 真删（默认只动 6 小时前没被动过的）
  python3 scripts/reclaim_test_tmpdirs.py --apply --older-than 30m
  python3 scripts/reclaim_test_tmpdirs.py --apply --include-empty   # 连空的 tmp######## 一起收
  ```

  家族清单**不从手抄**：每次运行都去 `tests/` + `tech_app/` + `scripts/` + 根目录 `*.py` 里
  读 `mkdtemp(prefix="…")` 的字面量（现有 121 个前缀），无前缀 `tmp########` 这一类额外要求
  "里面确实躺着测试载荷"（临时 SQLite / `real*.dxf` / `pkg*` / gate 输出…）。根只允许临时目录
  那种位置 —— 仓库根、home、`/` 及其上级一律拒绝。

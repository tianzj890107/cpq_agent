# CPQ Agent GitLab 交付工作流

## 分支模型

- `20260909`：唯一日常开发分支，同时镜像到 GitHub 和 GitLab。
- `master`：唯一发布主线，只接受 GitLab Merge Request 合入。
- MR 固定为 `20260909 → master`，reviewer `tianzijing`，assignee `zhangzhen`。

## 命令与授权映射

| 用户指令 | 执行动作 | 不包含 |
| --- | --- | --- |
| push / 推送 | commit 后运行双远端推送脚本 | MR、tag、Release、部署 |
| merge / 合并 | 创建或更新 MR，等待 reviewer 人工合并 | 自动合并、直接 push master |
| tag | 在 master 已合入提交创建并推送指定 annotated tag | Release、部署 |
| Release | 基于现有 tag 创建 GitLab Release | 改写 tag、部署 |
| 部署 | 将明确指定的 tag/commit 部署到明确环境 | 自动升级版本、自动 Release |

## 普通提交与推送

```bash
python3 scripts/push_remotes.py --check
python3 scripts/push_remotes.py
```

脚本要求当前位于 `20260909`、工作区干净、远端分支不含本地未知提交；推送后回读两个远端 SHA。禁止 force push。

## Merge Request

```bash
python3 scripts/create_mr.py --check
python3 scripts/create_mr.py --title "本次交付主题" --description-file /path/to/mr.md
```

脚本只创建或复用 opened MR，不执行 merge。MR 标题与描述必须覆盖 `master..20260909` 的全部提交、用户可见变化和验证结果。

## Tag 与 Release

先确认用户明确指定版本号和 tag 授权，并确保目标提交已进入 GitLab `master`：

```bash
git switch master
git pull --ff-only gitlab master
git tag -a vX.Y.Z -m "cpq_agent vX.Y.Z"
git push gitlab refs/tags/vX.Y.Z
python3 scripts/create_release.py --tag vX.Y.Z --name "cpq_agent vX.Y.Z" --description-file docs/versions/vX.Y.Z.md
```

已有 tag 不得移动或覆盖；不得使用 `git push --tags`。Release 创建前必须已有远端 tag，且不会自动部署。

## 部署

只有获得当前任务的明确部署授权后，才可按 [DEPLOYMENT.md](../DEPLOYMENT.md) 执行。部署目标必须是已确认的 tag 或 commit，并在完成后回读服务健康状态和实际提交。

# CPQ Agent 仓库工作约定

## 仓库与分支

- 本仓库是 `cpq_agent`，不再把 `cad_engine` 作为本项目的 GitLab 跟踪来源。
- 日常开发和发布统一使用 `20260909` 分支；该分支以 GitHub 的 `yiweilineng4` 为初始基线。
- 日常修改只进入开发分支 `20260909`；`master` 是唯一发布主线，只能通过 `20260909 → master` Merge Request 合入，禁止直接 push、自动合并或 force push。
- 遗留 `main` 只作旧 `cad_engine` 历史保留，不得作为开发、MR、tag、Release 或部署来源；未经用户再次明确要求不得删除或改写。
- 拉取以 GitHub `origin/20260909` 为首选源；GitHub 分支暂不可用时可从 `gitlab/20260909` 快进同步，禁止自动合并或改写历史。
- 每次普通交付必须把同一个提交推送到 GitHub 与 GitLab 的 `20260909`。推送前先运行 `python3 scripts/push_remotes.py --check`，再运行 `python3 scripts/push_remotes.py`；任一远端失败都必须如实报告并以同一 HEAD 重试。
- GitHub：`git@github.com:tianzj890107/cpq_agent.git`
- GitLab：`git@gitlab.boulderaitech.com:ai-team/cpq_agent.git`
- 当前内网服务 `http://172.16.10.34:8010/` 对应本仓库服务；除非用户明确要求，不要改用旧 `cad_engine` 部署链路。

## Spec、红测与 DeepSeek 实现边界

- 收到功能或缺陷需求后，Codex 先写清楚可验收的 spec，再补充能复现缺口且在实现前失败的自动化测试（red test）。
- 红测必须实际运行，并记录预期失败点；如果测试意外通过，应先修正测试，使它确实覆盖需求缺口。
- Codex 不直接编写业务实现。红测确认后，Codex提供一份可直接交给 DeepSeek 的实现提示词，至少包括：目标、相关上下文、允许修改范围、禁止事项、验收标准和测试命令。
- 业务实现由用户安排 DeepSeek 完成。DeepSeek 返回实现后，Codex负责代码审查、运行测试和核对 spec；发现问题时继续给出针对 DeepSeek 的修正提示词，不越过该边界自行补业务实现。
- 仅限测试脚手架、spec、提示词、仓库配置和用户明确授权 Codex 修改的非业务文件，可由 Codex直接修改。

## 提交与推送检查

- 不提交密钥、本地配置、历史会话、数据库运行文件、缓存或 `.DS_Store`。
- 提交前检查 `git status`、目标 diff 和相关测试结果；不得覆盖用户已有的未提交修改。
- 推送后用 `git ls-remote` 分别检查 GitHub/GitLab 的 `refs/heads/20260909`，两端必须指向当前提交。

## 周 changelog

- changelog 只按周维护，不再创建日报。文件统一放在 `changelog/changelog_M_D_D.md`，覆盖周一至周五，例如本周为 `changelog/changelog_9_7_11.md`。
- 当周文件启用后，每次完成仓库修改都要同步更新；按用户可见能力和最终状态合并记录，并注明日期，不记录排查过程或被后续覆盖的中间方案。
- 更新前检查当周提交、工作区 diff、现有周记录及相关部署/测试文档。运行日志、缓存、会话、上传文件和数据库数据不写入 changelog。

## Push、Merge、Tag、Release、部署边界

- `push`：提交验证后的 `20260909`，推送 GitHub 和 GitLab；不创建 MR、tag、Release，不部署。
- `merge`：只创建或更新 GitLab MR `20260909 → master`，reviewer 固定 `tianzijing`，assignee 固定 `zhangzhen`；不得自动合并。
- `tag`：只有用户明确指定版本号并明确要求打 tag 时，才允许在已合入 `master` 的提交上创建 annotated tag；禁止移动已有 tag，禁止 `git push --tags`。
- `release`：只有用户明确要求创建 GitLab Release 时执行；必须绑定已存在且已推送的同名 tag。Release 不等于部署。
- `部署`：只有用户在当前任务明确授权具体环境和版本时执行。部署前校验目标 tag/commit，保护服务器设置和历史数据，部署后检查 `http://127.0.0.1:8010/`；push、merge、tag、Release 均不得自动部署。
- CI 只允许在 Merge Request 和 `master` 上做测试/镜像构建验证；CI 中禁止部署、重启服务器和写生产环境。

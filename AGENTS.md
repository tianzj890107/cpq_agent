# CPQ Agent 仓库工作约定

## 仓库与分支

- 本仓库是 `cpq_agent`，不再把 `cad_engine` 作为本项目的 GitLab 跟踪来源。
- 日常开发和发布统一使用 `20260909` 分支；该分支以 GitHub 的 `yiweilineng4` 为初始基线。
- 拉取只以 GitHub `origin` 的 `20260909` 为准。开始工作前先执行 `git pull --ff-only origin 20260909`，禁止用 GitLab 内容反向覆盖 GitHub 工作树。
- 每次发布必须把同一个提交、同一个 `20260909` 分支同时推送到 GitHub 和 GitLab。仓库已为 `origin` 配置两个 push URL，因此使用 `git push origin 20260909`；推送后分别核对两端分支 SHA 一致。
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

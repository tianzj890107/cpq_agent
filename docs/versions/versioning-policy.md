# 版本管理规范

正式版本采用 `vMAJOR.MINOR.PATCH`。版本升级由业务变化决定，不按日期、提交数或部署次数机械升级。

- MAJOR：稳定阶段发生重大不兼容变化。
- MINOR：一组用户可感知的新能力或重要兼容变化。
- PATCH：缺陷修复、性能和交互优化、小范围兼容调整。

commit、push、merge、tag、GitLab Release、部署是六个独立动作。只有用户明确指定版本并分别授权相应动作时，才能创建 tag、Release 或部署。

正式版本流程：确定版本号 → 测试与 diff 检查 → 更新 `docs/versions/vX.Y.Z.md` 和索引 → 更新当周 changelog → 提交并推送开发分支 → MR code review 合入 master → 创建不可移动的 annotated tag → 按授权创建 GitLab Release → 按授权部署。

禁止 force push、`git tag -f`、`git push --tags`、覆盖已发布版本文件或把后续功能补写进旧版本。

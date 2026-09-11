# 技术工艺 Agent 能力恢复第 22 步：端到端场景 Spec

## 范围

把统一工作台的全流程固化成一套**可重复执行的端到端场景清单**：每条场景写清前置、步骤、
预期、依赖批次、自动化入口（可跑的 unittest 模块）与人工验收步骤，并提供统一 runner 打印 /
执行场景。自动测试守住"十条场景齐全且每条都能落地"。

至少十条场景：

| id | 场景 |
| --- | --- |
| `e2e-01` | 上传资料 → Agent 解析需求 → 右侧填字段 |
| `e2e-02` | 提交 → 确认 → 审核 |
| `e2e-03` | Agent 开始解析图纸 → 显示进度 → 零件按钮出现 |
| `e2e-04` | 点击零件 → 右侧看板显示详情 |
| `e2e-05` | 修改零件参数 → 右侧刷新并生成版本 |
| `e2e-06` | 生成工艺和成本 → 右侧显示结果 |
| `e2e-07` | 组装整合 → 财务成本复核 |
| `e2e-08` | 汇总报告 → 审核 → 发布 → 回传报价 |
| `e2e-09` | 中途切换步骤 → 左侧会话不丢失、上下文正确更新 |
| `e2e-10` | 任意一步失败 → 左右两侧显示真实错误且可重试 |

## 1. 交付物：场景清单 + runner

1. 新增 `docs/specs/tech-agent-recovery-22-e2e-scenarios.json`：

```json
{
  "step": 22,
  "scenarios": [
    {
      "id": "e2e-01",
      "title": "上传资料 → Agent 解析需求 → 右侧填字段",
      "automated": ["tests/test_tech_requirement_agent_red.py"],
      "depends_on": [8],
      "manual": [
        "1.1 上传需求文档 → 点「一键解析需求」",
        "右侧表单出现 AI 带入 / 推荐徽标，左侧总结填了什么、缺什么"
      ],
      "expected": "需求单字段由后端填写，左侧与右侧同时可见，无需刷新整页"
    }
  ]
}
```

- `scenarios` 必须**正好覆盖上表十条**，id 为 `e2e-01` … `e2e-10`。
- 每条必须有 `title` / `expected`，且 `automated` 与 `manual` **至少有一个非空**。
- `automated` 里每个路径都必须是仓库里真实存在的 `tests/test_*.py`（可以是该批次的 Red 套件）。
- `depends_on` 是 1–19 的批次号数组，说明这条场景依赖哪些批次落地。
- 失败场景（`e2e-10`）必须在 `manual` / `expected` 中写明"左右都显示真实错误且可重试"。

2. 新增 `scripts/tech_e2e_scenarios.py`：
   - `python3 scripts/tech_e2e_scenarios.py --list` → 打印全部场景 id + 标题 + 依赖批次，退出码 0；
   - `python3 scripts/tech_e2e_scenarios.py --run <id>` → 运行该场景的 `automated` 测试模块，
     并打印 `manual` 验收步骤；任一步失败退出码非 0；
   - runner 只读清单，不新增业务逻辑、不删数据、不调删除类接口。

## 2. 边界

- 只新增场景清单与 runner，不改业务实现；不新增 / 删除 `@app.` 路由。
- E2E 不得调用删除 / 重置接口清理生产数据；不得依赖真实密钥。
- runner 的自动化步骤只跑仓库内测试，不启动服务、不写生产数据。

## 3. 验收标准

1. 场景清单存在且 `e2e-01`…`e2e-10` 齐全、无重复；
2. 每条有 `title` / `expected`，`automated` 或 `manual` 至少一个非空；
3. `automated` 引用的测试文件都存在；
4. `scripts/tech_e2e_scenarios.py --list` 退出码 0 且列出全部十条 id；
5. `e2e-10` 明确要求"左右两侧都显示真实错误且可重试"。

## 4. 对应测试

`tests/test_tech_e2e_scenarios_red.py`

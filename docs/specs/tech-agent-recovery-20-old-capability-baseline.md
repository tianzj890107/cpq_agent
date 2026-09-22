# 技术工艺 Agent 能力恢复第 20 步：旧 2.1 能力对照验收 Spec

状态：Spec + 红测（已实现）
红测：`tests/test_tech_old_capability_baseline_red.py`

## 范围

以旧 2.1 图纸解析页（`tech_app/frontend/index.html` + `app.js`）为**能力基线**，逐项确认统一
工作台没有丢能力。验收不是"看起来一样"，而是每个基线能力都能在统一壳里找到**可点击 /
可调用的活入口**，并且这个结论被一份可机读的能力清单和自动测试长期守住。

八项基线能力：

| id | 要求 | 基线入口（旧 2.1） |
| --- | --- | --- |
| `upload` | 原来能上传的仍能上传 | 2D 工程图 / 3D 模型 / 附件上传 |
| `view` | 原来能查看的仍能查看 | 零件树、零件详情、3D、2D、识别依据 |
| `generate` | 原来能生成的仍能生成 | 解析、分解、工艺推荐、成本生成、报告 |
| `modify` | 原来能修改的仍能修改 | 改 IR 字段 / 零件参数并生成版本 |
| `submit-review` | 原来能送审的仍能送审 | 需求确认 / 审核 / 报告送审 |
| `export` | 原来能导出的仍能导出 | BOM、汇总、成本等导出下载 |
| `version` | 原来能查看版本的仍能查看 | IR 版本列表与版本详情 / diff |
| `expand-part` | 原来能展开零件的仍能在右侧看板展开 | 零件清单 → 零件详情在 iframe 内展开 |

## 1. 交付物：可机读能力清单

新增 `docs/specs/tech-agent-recovery-20-capability-baseline.json`，结构固定：

```json
{
  "step": 20,
  "baseline": "tech_app/frontend/index.html",
  "capabilities": [
    {
      "id": "upload",
      "requirement": "原来能上传的仍能上传",
      "routes": ["POST /api/projects", "POST /api/projects/3d"],
      "refs": [
        "tech_app/frontend/index.html#btnUpload",
        "tech_app/frontend/index.html#fileInput"
      ],
      "manual": "在 2.1 上传 PDF / DWG / STEP，看板显示文件名并出现解析入口"
    }
  ]
}
```

- `capabilities` 必须**正好覆盖上表八个 id**，每个 id 各一条，不重复、不缺失。
- 每条至少有一个 `routes`（`"METHOD /path"`）或 `refs`（`"相对仓库路径#token"`）。
- `routes` 必须是 `tech_app/backend/main.py` 里真实存在的 `@app.<method>("<path>")`。
- `refs` 里的文件必须存在，`token` 必须是该文件里真实出现的 DOM id 或 JS 标识符
  （用于证明入口不是编的）。
- `manual` 写清"在哪个页面做什么、看到什么"，供人工浏览器验收照做。

## 2. 边界

- 只新增清单与测试，不改业务实现；不新增 / 删除 `@app.` 路由。
- 清单只描述**已经存在**的能力，缺能力要如实写缺口并交给对应批次修，不得用占位符蒙混。
- 不删除旧 2.1 页面；统一壳与旧页共用同一批后端接口。

## 3. 验收标准

1. 清单存在且八个 id 齐全、无重复；
2. 每条至少一个 `routes` / `refs`，且全部能在代码里解析到；
3. 旧 2.1 页与八项基线能力依赖的冻结路由不减少；
4. 零件清单 / 零件详情 / 工艺 / 成本仍只在右侧看板 iframe 内展开。

## 4. 对应测试

`tests/test_tech_old_capability_baseline_red.py`

# Spec：图纸解析完成判定不得依赖零件数量（有效的零零件 IR 也算完成）

状态：Spec + 红测（已实现）
红测：`tests/test_tech_empty_ir_parse_completion_red.py`

## 背景（用户反馈的边缘缺陷）

`tech_app/frontend/tech-workbench.js:898` 的进度打点用零件数量当完成标志：

```js
const irParts = (parts && parts.parts) || [];
if (irParts.length) done.add('drawing');
```

同一写法还有两处：`tech-workbench.js:1156`（`project.ir.parts.length` 参与「有 IR」判断）与
`报价首页.html:1677`（`ir.parts && ir.parts.length`）。

后果：解析**成功但结果确实是 0 个零件**的项目（例如只识别出整机/无独立零件的图纸），
零件数量恒为 0，「图纸解析」这一步永远显示未完成，阶段恢复也判成 2.1 没做完。
零件数量是**结果**，不该同时充当完成标志。

## 事实依据（实测）

- 解析成功才会写 IR：`store.save_ir(project_id, ir, stage="parsed")`
  （`tech_app/backend/storage/store.py:369`）→ 写入 IR 文档、`meta.ir_revision += 1`、
  `meta.stages["parsed"] = <时间戳>`，并留审计 `save_ir:parsed`。
- 3D 导入路径写的是 `meta.stages["parsed_3d"]`（`main.py:1093`）。
- 只上传未解析时只有 `meta.stages.uploaded`。
- `/api/projects/{id}` 返回 `{meta, ir, ...}`；`/workflow` 返回 `project`（即 meta，含
  `stages` / `ir_revision`）；`/summary` 的 `aggregate.ir` / `steps.ir` 是同一份 IR 文档。

因此「解析完成」的可靠依据是：**IR 存在** 或 **解析留痕（`stages.parsed` /
`stages.parsed_3d`）** 或 **解析版本（`ir_revision` / `ir_input_revision` ≥ 1）**，
而不是 `parts.length > 0`。

## 范围

- 共享纯函数模块 `tech_app/frontend/tech-stage-restore.js`（第 3 项批次引入的同一份「阶段与
  完成度判定」模块）：新增导出 `TechStageRestore.drawingParsed(input)`；若该文件已存在，
  只新增导出，**不得重写已有的 `fromSignals`**。
- `tech_app/frontend/tech-workbench.js`：`refreshProgress()` 的 drawing 打点与「有 IR」判断
  改为调用共享判定。
- `报价首页.html`：`techStageFromFlow()` 的「有 IR」判断改为调用共享判定。
- 不改：后端任何路由与存储、`save_ir` 的语义、零件数量在下游报告里的展示
  （`summary-result.js` 里「识别 N 个零件」这类文案照旧）、阶段白名单与协议。

## 契约

```
TechStageRestore.drawingParsed(input) -> boolean
  input = { ir, meta, stages }        // 三者都可缺，缺失时按空处理，不得抛异常
    ir     /api/projects/{id}.ir 或 /summary 的 ir / steps.ir（可为 null / {}）
    meta   /api/projects/{id}.meta 或 /workflow 的 project（含 ir_revision / stages）
    stages 显式阶段时间戳（优先级高于 meta.stages）
  返回 true ⇔ 满足任一：
    a) IR 存在：ir 是对象且至少有一个自有键（**不要求 parts 非空**）
    b) 解析留痕：stages.parsed 或 stages.parsed_3d 非空
    c) 解析版本：meta.ir_revision 或 meta.ir_input_revision ≥ 1
```

判定必须是纯函数：不碰 DOM、不发请求、不读 localStorage，只用入参。

## 验收要求

- **R1 行为**：判定矩阵（见红测）逐例通过，关键是
  `{"ir":{"device_name":"X","parts":[]}}` → `true`（零零件但解析成功）、
  `{"ir":{}}` + `{"stages":{"parsed":"..."}}` → `true`、
  `{"ir":{}}` + `{"ir_revision":2}` → `true`、
  只有 `stages.uploaded` → `false`、`{"ir":{}}`（无留痕无版本）→ `false`。
- **R2 打点不缩水**：`done.add('drawing')` 仍存在，只是判定来源改为共享函数；
  `process` / `cost` / `summary` / `report-review` / `report-publish` 的既有完成条件不变。
- **R3 唯一判定**：`tech-workbench.js`、`报价首页.html`、
  `tech-stage-restore.js` 里都不得再出现 `parts.length` 形式的完成判定
  （结果数量只能用于文案展示，不得参与阶段判断）。
- **R4 不缩水**：`/api/projects/{id}`、`/workflow`、`/summary` 路由与字段不变；
  两个页面仍引入共享模块；`applyStage` / `techWorkbenchUrl` 用法不变。

## 验收命令

- 本批红测：`python3 -m unittest tests.test_tech_empty_ir_parse_completion_red -v`
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'`
- 语法：`node --check tech_app/frontend/tech-stage-restore.js`、
  `node --check tech_app/frontend/tech-workbench.js`

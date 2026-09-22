# 零件库连不上必须"当场看见"：看板顶部红色警示 + 结论区「未检索（库连不上）」（## 92）

状态：Spec + 红测（已实现）
红测：`tests/test_tech_kb_unavailable_notice_red.py`

## 1. 背景（用户口径）

知识库（零部件库）只有一份，落在 CPQ 的 `cpq_kb`（## 91，见
`docs/specs/kb-in-pg-http-snapshot.md`）。技术工艺经 `GET /wf/tech/kb/snapshot` 取整包快照，
拿不到时必须抛 `KbUnavailable`，**不再把故障伪装成"库里没有可复用零件"**。这一层已经落地。

但"拿到故障之后怎么告诉人"这一层还没做，今天的事实是：

- **自动路**（解析 / 拆解 / 导入 3D 之后顺手检索）把异常吞成一条进度提示 + 一条审计，
  **不落任何状态**（`tech_app/backend/main.py:1334-1335`）；
- **读取出口** `GET /api/projects/{id}/component-match` 查不到报告就回
  `{"items": [], "summary": {}}`（`main.py:1393`）；
- **前端**拿到空报告渲染成「还没有零部件库检索结果（解析完成后自动生成）。」
  （`tech_app/frontend/app.js:1028`），而只要还留着旧报告，顶行还会写「库内 0 条」
  （`app.js:1036` 的 `report.library_size || 0`）。

所以刷新页面（或换台机器打开）之后，故障现场就消失了：「库连不上」和「库里确实没有可复用
零件」在界面上长得一模一样。这正是本批要消灭的误会。

## 2. 目标行为（用户口径）

1. 知识库连不上时，**右边看板顶部**出现红色警示：零件库连不上，本次未检索；
2. **匹配结论区**明确写「未检索（库连不上）」，**绝不**出现「库内 0 条」
   「可复用 0 · 可改制 0 · 未匹配 0」这类把故障当结论的文案；
3. **左边**保留提示，并写清是"零件库连不上"（不是"库里没有"）；
4. 警示**持久化**：切走再回来、刷新页面仍在，直到一次成功检索把它清掉；
5. 用户主动点「重新检索零部件库」成功后，警示自动消失；
6. 上一次的成功结论可以继续看，但必须标注「上一次的结论（可能已过期）」，不得冒充本次结论。

## 3. 契约

### C1 后端：把"未检索"落成一等状态（`tech_app/backend/storage/store.py`）

新增两个函数，doc 名固定为 `component_match_unavailable`：

- `save_component_match_unavailable(project_id: str, info: dict) -> None`
- `load_component_match_unavailable(project_id: str) -> Optional[dict]`（文档缺失或为空 → `None`）
- 既有的 `save_component_match(project_id, report)` 成功落报告时**必须清掉**该文档
  （本仓库的清空约定是 `_meta().put_doc(pid, kind, {})`，见 `store.py:421`）——
  这样重试成功 = 警示消失。

`info` 字段固定：

```json
{"reason": "kb_unavailable", "message": "零件库连不上，本次未检索",
 "error": "<原因摘要，≤200 字>", "at": "<CST 时间戳，同 now_cst_str()>"}
```

`reason` 取值：`kb_unavailable`（知识库不可用）、`kb_error`（其它异常）。

### C2 后端：两个入口都要落状态，都不得写空报告

- 自动路 `main._refresh_component_match()`（`main.py:1318`）：捕获异常后，除既有进度提示与
  审计外，**必须**调 `store.save_component_match_unavailable(...)`；`reason` 按异常是否为
  `KbUnavailable` 区分。提示文案必须写明是零件库/知识库不可用（不得只说"检索失败"）。
- 主动路 `POST /api/projects/{id}/component-match`（`main.py:1397`）：任务体在抛出前先把失败
  落状态（任务本身仍然失败）。
- **红线**：两条路都不得在知识库不可用时 `save_report` 一份 `library_size=0` / 空 `items` 的报告。

### C3 后端：读取出口把状态交给前端（`main.py:1389`）

`GET /api/projects/{id}/component-match` 200 返回体 = 既有报告字段 + 顶层 `unavailable`：

- 有失败记录 → `unavailable` = C1 的 dict；
- 没有 → `unavailable` = `null`；
- 既没有报告也没有失败 → `{"items": [], "summary": {}, "unavailable": null}`（保持既有语义）；
- `unavailable` 非空时，返回体里**不得**出现 `library_size == 0`（保留上次成功值或 `null`）。

### C4 前端：看板顶部红色警示 + 结论区文案（`tech_app/frontend/app.js`）

`renderComponentMatchResult(report)`（`app.js:1016`）：

- `report.unavailable` 为真时：
  - 在看板容器（`#secParts` 所在页面的 `.center-panel`，取不到则退到 `#secParts` 的父节点）
    插入/更新 `#componentMatchBanner`，`class` 含 `component-match-unavailable`，
    文本含「零件库连不上，本次未检索」+ 原因摘要；
  - `#componentMatchBanner` 必须排在 `#secParts` **之前**（在"看板顶部"，不是零件清单里面）；
  - `#componentMatchResult` 文本为「未检索（库连不上）」；**不得**出现「库内 0 条」
    「可复用 0」「可改制 0」「未匹配 0」；
  - `report.items` 非空（上次成功结论）时，必须并列标注「上一次的结论（可能已过期）」。
- `report.unavailable` 为空时：把 `#componentMatchBanner` 从看板移除，其余渲染与现在一致。
- 允许使用的 DOM API：`document.getElementById` / `document.createElement` /
  `document.querySelector`；`Element.append|appendChild|prepend|insertBefore|replaceChildren|`
  `remove|closest|classList|textContent|className|setAttribute`。

### C5 前端：红色样式（`tech_app/frontend/workbench.css`）

`.component-match-unavailable` 必须是红色警示：用既有 token `var(--color-red)`
（`workbench.css:4`），不允许做成灰底或蓝色提示。

### C6 静态资源版本号

`index.html` 里的 `app.js?v=` 与 `workbench.css?v=` 都必须改（缓存击穿）；否则老缓存会把新
代码挡住，用户看到的就是"改完了还是老样子"。

### C7 既有约束不放松

- 知识库可用时的渲染与 `component_match.json` 结构不变；
- `library_size` 仍由真实库计数得出（`component_match.py:148`）；
- 左侧提示保留，不得为了"少一行"删掉；
- 知识库不可用仍必须抛 `KbUnavailable`，不得回落成空库（## 91 契约）。

## 4. 验收

| 编号 | 场景 | 通过标准 |
| --- | --- | --- |
| A1 | 自动路 + 知识库不可达 | 不抛异常；`load_component_match_unavailable()` 有 reason/message/at；`load_component_match()` 仍为 `None` |
| A2 | GET 路由（失败后） | 200；`unavailable` 非空；`library_size` ≠ 0 |
| A3 | POST 路由（知识库不可达） | 任务失败；随后 GET 能看到 `unavailable` |
| A4 | 重试成功 | 落报告且 `unavailable` 被清掉（前端警示随之消失） |
| A5 | 前端（不可用） | 有 `#componentMatchBanner`（红色 class + 指定文案），排在 `#secParts` 之前；结论区「未检索（库连不上）」；整页无「库内 0 条」 |
| A6 | 前端（可用） | 无警示；渲染与现在一致 |
| A7 | 红线 | 任何路径都不得落 `library_size=0` 的报告 |

## 5. 不在本批（避免误期待）

- 匹配口径调整（`ENVELOPE_TOLERANCE=0.20` 对家电/钣金大件偏紧，见 ## 91 §5）；
- 知识库维护页面；
- 把"未检索"做成任务中心的一等任务状态（本批只有：看板警示 + 结论区文案 + 左侧提示 + 可重试）。

# 技术工艺业务页面项目身份唯一来源与防串项目（批次 1）

- 范围：`tech_app/frontend` 全部技术工艺业务页面（统一工作台父壳 + 九个 stage 子页 + 需求详情页）。
- 前置口径（已生效，不推翻）：`docs/specs/unified-tech-cost-workbench.md:53-55` 已声明
  「URL 是可恢复导航状态的事实源」「`project` 必填，缺失时展示明确错误态，禁止创建匿名项目」。
  本批把这条口径从「统一工作台父壳」扩展到**所有业务页面脚本**，并补上跨窗口（父壳 / iframe）
  与 `task_id` 两条恢复通道。
- 本批不做：Token 口径、任务状态机、流程门禁（L0–L4）、视觉样式、后端接口。

状态：Spec + 红测（已实现）
红测：`tests/test_tech_project_identity_single_source_red.py`

## 1. 背景与真实问题

统一工作台父壳（`tech_app/frontend/tech-workbench.js:148-153`）本身是合规的：`state.project` 只从
`?project=` 取，缺失时进入「缺少项目」错误态。但**子页/独立页不是**：它们在自己的脚本里做了
「URL 没有 `project` 就退回 localStorage 上一次项目」的兜底。

实测（`grep` 定位，全部为读取点）：

| 文件 | 位置 | 读取表达式 |
|------|------|-----------|
| `tech_app/frontend/assembly-integration.js` | `:14-15` | `get('project') \|\| localStorage.getItem('cad_engine_project_id') \|\| ''` |
| `tech_app/frontend/cost-review.js` | `:15-16` | 同上 |
| `tech_app/frontend/requirement-create.js` | `:2` | 同上 |
| `tech_app/frontend/requirement-confirm-page.js` | `:2` | 同上 |
| `tech_app/frontend/requirement-review-page.js` | `:2` | 同上 |
| `tech_app/frontend/requirement-detail.js` | `:2` | 同上 |
| `tech_app/frontend/summary-result.js` | `:2` | 同上 |
| `tech_app/frontend/report-review-result.js` | `:2` | 同上 |
| `tech_app/frontend/report-publish-result.js` | `:2` | 同上 |
| `tech_app/frontend/tech-embed.js` | `:49-51` | `projectId()` = 同上 |
| `tech_app/frontend/workflow-navigation.js` | `:28-30` | `projectId()` = query \|\| `cad_engine_project_id` \|\| `currentProject` |
| `tech_app/frontend/workflow.js` | `:17` | `const projectId = qp.get('project') \|\| localStorage.getItem('cad_engine_project_id') \|\| ''` |
| `tech_app/frontend/app.js` | `:525` | `const pid = q.get("project") \|\| localStorage.getItem("lastProject")` |

后果（用户可见的真实风险，非理论）：

1. **串项目**：同一浏览器里先看项目 B，再从一个丢了 `project` 的入口（历史链接、旧书签、
   `location.href='assembly-integration.html'`、直达 `cost-review.html`）打开页面，页面会静默
   加载并允许操作 **B**：2.2 会写 B 的组装工艺，2.3 会把 B 的成本回传给销售，1.1 甚至会把
   B 的需求单覆盖成新草稿。用户以为在看新单，实际在改上一单。
2. **越权绕过**：项目归属是**后端**按登录账号判定的，但前端「悄悄换了一个项目」会让用户
   以为当前项目凭据无效，或反过来在共享终端上让 A 账号看到 B 项目的内容（A 的
   `/api/projects/B` 会 403，页面于是显示成「打不开」，而不是「你没带项目」）。
3. **两个标签页互相影响**：标签页 A 打开项目 A、标签页 B 打开项目 B，两者共用同一份
   localStorage；`currentProject` / `cad_engine_project_id` 在导航时被反复覆写（
   `workflow-navigation.js:324`、`workflow.js:66`、`app.js:1394`），谁最后写，谁就污染另一个
   标签页的兜底值。
4. **父壳与 iframe 不一致时无人拒绝**：父壳 `message` 处理器（`tech-workbench.js:862-877`）只校验
   `event.origin`、`event.source` 与 stage 白名单，`data.project` 直接进 `applyStage`
   （`:874`），于是 iframe 可以把父壳切到另一个项目。

## 2. 用户角色和用户故事

- 工艺经理：从首页/历史/待办进入 1.1–2.2，希望「打开哪一单就是哪一单」，改了工艺不会写进别的单。
- 财务经理：从待办进入 2.3（URL 可能只有 `task_id`），希望任务能唯一确定项目；不唯一时宁可不进，
  也不要进错。
- 销售/管理员：从需求详情、报告页只读查看，希望「没有项目」是一个明确的提示，而不是悄悄
  打开别人的单。
- 研发/运维：希望只有一处实现项目身份解析，出问题能一眼定位，不必 12 个文件逐个排查。

## 3. 当前流程

```
入口(首页/历史/待办) → 目标 URL ?project=X[&task_id=T][&embed=1]
   ├─ 统一工作台父壳: state.project = ?project (合规) → iframe 子页 URL 追加 project=X&embed=1
   │     └─ 子页脚本: project = ?project || localStorage['cad_engine_project_id'] || ''   ← 缺口
   └─ 独立旧页(无 embed): tech-embed.js 重定向到工作台, project 取自同一表达式          ← 缺口
```

## 4. 目标流程

```
入口 → URL ?project=X[&task_id=T] / 父壳 state.project
   → window.TechProjectContext.resolve()   （唯一解析点）
        ├─ URL 有 project 且父壳一致         → source='url'
        ├─ URL 无 project 但有父壳 project   → source='parent'
        ├─ URL 无 project 但有唯一 task_id   → TechProjectContext.fromTask(taskId) → source='task'
        ├─ URL 与父壳 project 不一致          → error='project_mismatch'，页面拒绝加载/拒绝写
        └─ 都没有                            → error='missing_project'，页面走进明确的错误态
   → 页面所有项目级请求/写入都使用该 project；写前调用 assertSame(project)
```

localStorage 只保留两种角色：**登录令牌**（`authToken` / `cad_engine_token`，本批不动）与
**最近访问导航记录**（`lastProject`、`cad_engine:last_page:*`、`currentProject` 等，允许**写**，
但**不得读来决定业务数据归属**）。

## 5. 状态定义及状态转换

项目身份解析结果（`context`）：

| 字段 | 取值 | 含义 |
|------|------|------|
| `project` | 非空字符串 | 本页唯一可用的项目 id |
| `project` | `''` | 本页没有可用项目，任何项目级请求/写入都必须停止 |
| `source` | `'url'` / `'parent'` / `'task'` / `''` | 身份来源 |
| `error` | `''` / `'missing_project'` / `'project_mismatch'` / `'task_no_project'` / `'task_ambiguous'` | 失败原因（`project===''` 时非空） |
| `message` | 中文短句 | 可直接展示给用户的原因说明 |

状态转换：

```
未绑定 --URL 带 project--> 已绑定(url)
未绑定 --URL 无 project + 父壳有 project--> 已绑定(parent)
未绑定 --URL 无 project + task_id 唯一--> 已绑定(task)
未绑定 --URL 无 project + 无父壳/无 task_id--> 错误态(missing_project)
未绑定 --task_id 0 个或 >1 个项目--> 错误态(task_no_project / task_ambiguous)
已绑定(url) --父壳 project 不同--> 错误态(project_mismatch)，保持不写
错误态 --用户从项目列表/历史重新进入带 project 的 URL--> 已绑定
```

## 6. 接口或数据契约（本批唯一新增件）

新增共享纯函数模块 **`tech_app/frontend/tech-project-context.js`**，挂 `window.TechProjectContext`，
并在文件末尾提供 `module.exports = api`（与 `tech-stage-restore.js` 同款，便于红测直接执行）。

```js
window.TechProjectContext = {
  // 纯函数：只读入参，不读 localStorage/sessionStorage，不发请求，无副作用，可重复调用。
  // options: { search?: string, parentProject?: string }
  //   search 为 location.search 原文（允许带或不带前导 '?'）
  //   parentProject 为父壳项目 id（'嵌入时' 由调用方给出，或省略）
  resolve(options) -> { project, source, error, message },

  // 读取真实环境（location.search + window.frameElement?.dataset?.project）解析并缓存本页身份。
  bind(options?) -> context,      // 读环境解析并缓存本页身份（页面统一用无参 bind()）
  current() -> context,           // 返回本页已 bind 的身份；未 bind 过则先 bind()

  // 写请求前的一致性校验：project 必须等于 current().project。
  assertSame(project) -> { ok: boolean, error, message },

  // 纯函数：从 /wf/task 的 payload 推导唯一项目。
  // 候选位置：payload.tech_cost.project_id / payload.tech_project_id /
  //          payload.project_id / payload.tech_result.project_id / payload.result.project_id
  projectFromTaskPayload(payload) -> { project, source, error, message },

  // 唯一允许异步取项目的地方：GET /wf/task?task_id=<id>；同一 task id 去重（含并发）。
  // options: { fetchImpl?, token? }
  fromTask(taskId, options?) -> Promise<{ project, source, error, message }>,
};
```

判定规则（必须逐条成立）：

1. `resolve({search:'?project=A'})` → `{project:'A', source:'url', error:''}`。
2. `resolve({search:''})`（无论 localStorage 有什么） → `{project:'', source:'', error:'missing_project'}`，且
   **不读** localStorage / sessionStorage。
3. `resolve({search:'?project=A', parentProject:'B'})` → `{project:'', error:'project_mismatch'}`。
4. `resolve({search:'?project=A', parentProject:'A'})` → `{project:'A', source:'url'}`。
5. `resolve({search:'', parentProject:'B'})` → `{project:'B', source:'parent'}`。
6. `bind()` 省略 `options` 时：`search` 取 `location.search`；`parentProject` 取
   `window.frameElement && window.frameElement.dataset && window.frameElement.dataset.project`，
   取不到既是 `''`（跨域访问异常必须 try/catch 成 `''`）。
7. `assertSame(p)`：`p===current().project` 且非空 → `{ok:true}`；`current().project===''` →
   `{ok:false, error:'missing_project'}`；不同 → `{ok:false, error:'project_mismatch'}`。
8. `projectFromTaskPayload`：去重后恰好 1 个非空候选 → 该值；0 个 → `task_no_project`；
   ≥2 个**不同**值 → `task_ambiguous`（相同值重复出现不算冲突）。
9. `fromTask`：HTTP 非 2xx / `ok!==true` / 解析失败 → reject（错误可见）；同一 task id 的重复
   调用（含并发 `Promise.all`）**只发出一次请求**、返回同一结果，不产生第二份结论。
10. 所有业务页面的项目常量必须来自本模块（`TechProjectContext.bind()` / `current()`），
    且页面脚本内**不得**再出现 `localStorage.getItem('cad_engine_project_id' | 'currentProject' | 'lastProject')`。

### 各页面调用形态（本批固定）

| 页面 | 访问点 | 契约 |
|------|--------|------|
| `app.js`(2.1) | `afterAuth()` 里的 `pid` | `TechProjectContext.bind().project`；无项目 → 显示「没有拿到项目编号」并停（不自动打开上一次项目） |
| `assembly-integration.js` | `aiPid` | `TechProjectContext.bind().project`；`''` → 沿用现有「退出到首页」分支（`:1862`） |
| `cost-review.js` | `crPid` | 同上（`:1019`）；`task_id` 存在且 `project` 为空时用 `fromTask` 唯一恢复 |
| `requirement-create.js` | `rcPid` | `bind().project`；`''` 是**合法**初值（新建草稿），但绝不能是「上一次项目」 |
| `requirement-confirm-page.js` / `requirement-review-page.js` / `requirement-detail.js` / `summary-result.js` / `report-review-result.js` / `report-publish-result.js` | `*Pid` | `bind().project` |
| `tech-embed.js` | `projectId()` | 委托 `TechProjectContext.bind().project`；缺失时重定向到工作台错误态（不创建匿名项目） |
| `workflow-navigation.js` / `workflow.js` | `projectId()` / `projectId` | 委托 `TechProjectContext`；localStorage 只能写「最近访问」 |
| `tech-workbench.js` | `state.project`、`message` 处理器、`childUrl()` | 由 URL 解析（已合规）；**新增**：iframe 收到 `cpq:tech-workbench:navigate` 时，`data.project` 与本壳 `state.project` 不一致必须拒绝并提示；iframe 元素必须带上 `data-project=<state.project>` 供子页比对 |

## 7. 正常路径

1. 用户从首页点项目 A → `tech-workbench.html?project=A&stage=drawing` → 父壳解析 A → iframe
   `index.html?project=A&stage=drawing&embed=1&…` → 2.1 解析 A。
2. 工作台内切步骤 → 父壳 `applyStage` + `childUrl` → 子页 URL 始终带 A。
3. 财务点待办（URL 有 `task_id` 无 `project`）→ `fromTask` 返回唯一项目 → 进 2.3。
4. 独立旧页直达（`cost-review.html?project=A`，无 embed）→ tech-embed 重定向到工作台
   `?project=A&stage=cost`。

## 8. 异常路径

| 场景 | 期望 |
|------|------|
| URL 无 `project`，localStorage 有旧项目 | 不加载旧项目；`missing_project` 错误态（页面提示 + 回首页入口）；**不发**任何 `/api/projects/*` 请求 |
| URL 项目 A，localStorage 项目 B | 只操作 A；B 不出现在任何请求 |
| iframe 项目 ≠ 父壳项目 | 子页拒绝加载（`project_mismatch` 提示）；父壳拒绝 iframe 的换项目导航 |
| `task_id` 关联不唯一 / 无关联 | 停止并提示（`task_ambiguous` / `task_no_project`），不猜、不打开上一次项目 |
| `/wf/task` 失败 / 离线 | 提示「任务信息读取失败」并停止，不退化到 localStorage |
| 项目不存在 / 无权限（后端 404/403） | 保持现有处理：明确提示「项目不存在 / 无权限」，**不得**回退到上一次项目 |

## 9. 并发与幂等要求

- `resolve()` / `bind()` / `current()` 纯读、幂等：同一环境对象调用两次结果全等，不产生缓存副作用。
- `fromTask(taskId)` 对同一 task id **去重**：并发两次只发一次请求、只产生一份结论；
  第二次调用返回与第一次相同的结果（不得因重复点击产生第二个项目判断）。
- 两个标签页（同源、同一 localStorage、分别是项目 A / 项目 B）互不影响：各自解析结果恒为
  自己 URL 的项目；一个标签页切步骤写入 localStorage 不改变另一个标签页的身份。
- 父壳 `applyStage` 的幂等语义不变：同 stage 重复调用不产生第二个 iframe（既有行为，不回归）。

## 10. 刷新、重试、重复点击、服务重启后的行为

- 刷新：身份重新由 URL 推导（父壳 `popstate` / 子页脚本各自重跑），结果与刷新前一致；
  没有 `project` 的 URL 刷新后仍是错误态，不会「自动恢复」到上一次项目。
- 重试 / 重复点击：`fromTask` 命中上次结果，不再重复请求；页面进入同一 stage。
- 服务重启：前端不缓存业务身份到持久存储，重启后再打开同一 URL 结果不变；`fromTask`
  在服务不可用时报错并提示，不降级为 localStorage。

## 11. 权限边界

- 本批**不改**任何权限判定与角色门禁：项目归属仍由后端按登录账号判定（`/api/projects/*`
  的 owner 校验、`cost_review` 的 `COST_ROLES`、`/wf/task` 的登录校验）。
- 前端新增的只是「不要把 URL 之外的项目当成当前项目」；`fromTask` 必须带当前账号的
  `Authorization` 头，让服务端按账号裁剪任务可见性（不允许前端自造项目映射）。
- 权限不可用「仍要继续」绕过这一既有口径不变。

## 12. 历史数据兼容

- 已在使用的 URL（`?project=`）、历史记录、首页卡片、待办入口全部继续可用；不改变 URL 形状。
- localStorage 里已有的 `cad_engine_project_id` / `currentProject` / `lastProject` 不删除、不清空；
  只是不再被读作业务身份。旧标签页刷新一次即生效。
- 旧项目（没有 2.2 / 2.3 结果）身份解析路径不变。
- `tech-board-runtime.js` / `tech-board-bridge.js` / `agent-chat.js` 的父壳-BoardBridge-会话
  协议不变；`agent-chat.js` 的项目重绑能力（`window.ocTechAgent.setProject`，见
  `docs/specs/tech-agent-history-project-rebind.md`）必须保留。

## 13. 非目标

- 不改 Token / 鉴权口径（`authToken`、`cad_engine_token`、`/auth/*`）。
- 不改任务状态机、partial/失败语义、门禁分级（L0–L4）、waiver。
- 不改任何视觉样式、布局、卡片结构。
- 不新增后端接口、不改数据库。
- 不清理、不迁移、不删除任何 localStorage / 项目 / 会话 / 任务 / 数据。

## 14. 可自动化验收标准

1. 模块级行为（node 真跑 `tech-project-context.js`）：第 6 节规则 1–9 全部成立；
   `resolve()`/`bind()` 期间 localStorage 读取次数为 0。
2. 页面级行为（node 真跑页面脚本片段，环境 `location.search=''` + localStorage 三个键均为 `B`）：
   12 个页面/访问点的项目值必须为 `''` 并给 `missing_project`（不出现 `B`）。
3. 页面级行为（`location.search='?project=A'` + localStorage 为 `B`）：解析结果为 `A`。
4. 页面级行为（`?project=A&embed=1` + `window.frameElement.dataset.project='B'`）：解析失败为
   `project_mismatch`，`project===''`。
5. 两个标签页共用一个 localStorage（A→`A`、B→`B`、另一个无 project→`''`）互不影响。
6. `fromTask`：唯一项目→成功；无项目/多项目→带错误拒绝；同 id 连续两次 + 并发两次→仅 1 次请求。
7. 父壳：`cpq:tech-workbench:navigate` 携带与本壳不同的 `project` → 拒绝（不切换）；iframe 元素带
   `data-project`。
8. 静态：业务页面与 HTML 加载顺序（`tech-project-context.js` 先于页面脚本）；不再有
   `localStorage.getItem('cad_engine_project_id'|'currentProject'|'lastProject')`。
9. `aiUrl()` / `crUrl()` 在场景 3/4 下只指向解析出的项目。

## 15. 人工验收场景

1. 同一浏览器：打开项目 A → 回到首页 → 打开项目 B → 地址栏手动改成 `assembly-integration.html`
   （不带参数）→ 必须显示「缺少项目/回首页」，**不得**出现 B（或 A）的 2.2 数据。
2. 项目 A 的 2.2 页面地址里手动把 `project` 改成 `B` 的 id（A 账号无权）→ 明确提示无权限/项目不存在，
   且不会退回上一次项目；原标签页 A 不受影响。
3. 两个标签页分别打开 A / B，在 A 里切步骤到 2.3、在 B 里刷新 → 两者仍各自是自己的项目。
4. 新建工艺待办（只有 `task_id`）→ 能进入；把 `task_id` 改成不存在的号 → 明确提示任务读取失败，
   页面不加载任何项目数据。
5. 2.3 页面：把 URL 的 `project` 去掉、`task_id` 保留 → 若该任务唯一关联项目则正常进入；否则提示并停止。

## 16. 不允许减少的既有能力

- 父壳「缺少项目」错误态与 `requirement-create` 允许无项目建项（`tech-workbench.js:511-530`）。
- 旧页重定向到统一工作台（`tech-embed.js`）与 `embed=1` 嵌入协议、`requestNavigate` / `navigateToFile`。
- 工作台切步骤同步左侧会话（`syncChatProject` → `window.ocTechAgent.setProject`）。
- 待办入口携带 `task_id` / `tech_task` 并在正式去向里回传关闭待办（`crTaskId` 等）。
- `home-link.js` 的「最近停留页」恢复（`cad_engine:last_page:*`）与返回按钮。
- 12 个页面的既有加载/渲染/请求行为（只是在项目身份来源上收口）。

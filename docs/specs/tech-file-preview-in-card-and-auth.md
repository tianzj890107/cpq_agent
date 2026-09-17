# 任务文件在卡片内预览 + 修掉「请先在配置报价 CPQ 中登录」 Spec

状态：TDD Red，等待 DeepSeek 实现。

## 用户口径（原话）

> 现在在任务文件的卡片里点击一个文件就会跳转到一个网页，应该是直接在这个卡片里就可以预览这个文件，
> 而且关键是这个网页还 {"detail":"请先在配置报价 CPQ 中登录"}

两件事一起做：**① 点文件在卡片内预览，不再跳新页面；② 顺手修掉那个 401。**
同一个缺陷在 2.1 自己的悬浮「任务文件」小窗里也存在，本批一并收口（见契约 B）。

## 1. 只读定位（实测，未改任何业务实现）

**两个渲染点都在发裸链接**（不带令牌、且强行新开标签页）：

- 卡片/工作区视图：`tech_app/frontend/app.js` 的 `renderBoardFiles()`（`:2801`）里
  `link.href = file.url; link.target = "_blank";`（`:2833-2840`）。
- 2.1 悬浮小窗：`tech_app/frontend/agent-chat.js` 的 `fileRow()`（`:1293-1305`）里
  `link.href = file.url; link.target = "_blank";`（`:1297-1301`）。

**为什么必然 401**：

- 清单里的 url 全部是**同源 `/api/...` 相对路径**（`tech_app/backend/main.py:1109-1170` 的
  `list_project_files()`：`/api/projects/{id}/source`、`/attachments/{name}`、
  `/geometry/{part}.stl|.step`、2D views / dxf、`/bom.csv`、`/costest.csv`；`kind ∈ {image, doc, model, table}`）。
- 这些路径受 app 级鉴权保护：CPQ SSO 开启时走 `_cpq_sso_guard()`（`main.py:372-393`），
  取票顺序是 `Authorization: Bearer` → 否则 `?token=`（`_sso_token()` `:350-358`）；
  两个都没有就 `raise HTTPException(401, "请先在配置报价 CPQ 中登录")`（`:387`）。
- 点裸链接是**顶层导航**：不会带 `Authorization` 请求头，URL 里也没有 `?token=` → 必 401，
  浏览器把 JSON 错误体当成一个网页显示出来。用户看到的就是这句。

**现成可复用的能力**（不新造）：

- `app.js:21-30` 把 `window.fetch` 包了一层：同源 `/api/` 请求**自动带** `Authorization: Bearer <票>`；
- `app.js:31-32` 的 `mediaUrl(u)` 是给 `<img>` / `<a>` 这类「发不出请求头」的标签用的 `?token=` 透传约定；
- 卡片正文容器 `#boardCardBody`、2.1 小窗正文 `#ocFilesBody` 都已存在；`boardFileManifest` 已在
  `renderBoardFiles()` 里缓存了整份清单。

## 2. 契约 A：点文件在卡片内预览

A1. `renderBoardFiles()` 里文件名**不再是链接、不再 `target="_blank"`**：改为可点的行（`<button>` 或带
点击处理的元素），点击调统一预览入口 `window.CadFilePreview.open(file, container)`；
`container` 用当前清单的容器（卡片正文 / 小窗正文），预览就在原地展开。

A2. `app.js` 新增唯一一份预览实现，并挂到 `window.CadFilePreview = { open, close }`：

- `open(file, container, onBack)` 把 `container` 换成「预览视图」：顶部一行 = 文件名 +
  `← 返回文件列表` + `下载`；下方是内容区；`onBack` 由调用方给（卡片侧用缓存里的
  `boardFileManifest` 重建清单、**不再请求接口**；2.1 悬浮小窗侧复用既有 `loadFiles()`）；
- `close()` 与 `← 返回文件列表` 是同一个行为：释放 objectURL、调 `onBack()` 回到清单；
- 切换预览另一个文件时先释放上一个 objectURL。

内容区按类型分支（`file.kind` 优先，扩展名兜底）：

| kind / 扩展名 | 预览方式 |
|---------------|----------|
| `image`（png / jpg / jpeg / gif / webp / svg / bmp） | `<img>`，限高不撑破卡片 |
| `table` 与文本类 `doc`（txt / md / csv / json / log / yaml / yml） | `<pre>` 显示文本；超过 `TEXT_PREVIEW_LIMIT`（2000 行或 200KB）只显示前一段并追加「已截断」 |
| pdf | `<iframe>` |
| `model`（stl / step / stp） | 不内联渲染，显示「STL / STEP 不在卡片内预览，3D 请在零件详情里看」+ 下载 |
| 其它 | 显示「该类型暂不支持预览」+ 下载 |

A3. 取文件一律走**带鉴权的 fetch**：`fetch(file.url)`（同源 `/api/` 会自动带 `Authorization`）→
`response.blob()` → `URL.createObjectURL(blob)`。**不得**使用 `location.href` / `window.open` /
`target="_blank"`；**不得**把 token 拼进 URL（`?token=` 只留给 `<img>`/`<a>` 这类既有约定，
本批的预览不用它）。

A4. `!response.ok` 时在内容区显示**可读原因**，绝不显示响应体本身：
`401` / `403` → 「登录状态已失效，请刷新页面后重新登录」；其它 → 「读取失败：HTTP {status}」。
注意：这里的 401 现在只可能来自「票过期」，不再是「没带票」。

A5. 释放：`URL.revokeObjectURL(...)` 必须在 `close()` 与替换预览时被调用（长会话里反复预览不能一路泄漏）。

A6. 「下载」按钮复用同一个 objectURL + `a[download]`，同样不新开标签、不把 token 暴露到 URL。

A7. 预览样式保持最小：用一组 `.file-preview-*` 类放在 `workbench.css`（两个入口所在页面都加载它），
只解决「限高不撑破容器 / 文本可滚动 / 图片居中」；若改了 `workbench.css`，把它的 `?v=` 一并提升。

## 3. 契约 B：两处入口共用同一份预览

B1. `agent-chat.js` 的 `fileRow()`（`:1293-1305`）同样改成调 `window.CadFilePreview.open(...)`，
不再 `target = "_blank"`。`app.js` 是 `type="module"`（延迟执行），而点击发生在用户交互时，
那时 `window.CadFilePreview` 一定已经挂上 —— 不需要在脚本加载期就取到。

B2. 预览实现只有 `app.js` 里这一份；`agent-chat.js` 不复制第二份（含类型判断与错误文案）。

## 4. 契约 C：不再出现「裸文件链接」

C1. `renderBoardFiles()` 与 `fileRow()` 两个函数体内不得再出现 `_blank`。

C2. 根因护栏（**不许为了打开文件而放宽鉴权**）：`main.py` 的 `_cpq_sso_guard()` 仍必须是
「无有效票 → 401『请先在配置报价 CPQ 中登录』」，`_sso_token()` 仍按
`Authorization: Bearer` → `?token=` 顺序取票；本批只改前端取文件的方式。

## 5. 契约 D：缓存号

D1. 改了 `app.js` 与 `agent-chat.js`，`index.html` 的 `app.js?v=` 与 `agent-chat.js?v=`、
`tech-workbench.html` 的 `agent-chat.js?v=` 必须提升（不刷号线上会命中旧缓存，用户仍看到跳转）。

## 6. 明确不做

- 不把 `/api/**` 改成公开、不给这些文件路径加白名单；
- 不新建第二套文件清单接口，不动 `/files` 返回结构（`groups` / `files` / `note` / `total` 一个字段都不改）；
- 不在卡片里做 3D（STL/STEP）渲染 —— 那是零件详情里既有 3D 视图的职责；
- 不动零件详情里的下载链接（`lowerHtml`）与 `#ocFilesDock` 的窗口形态；
- 不缓存文件内容到会话/历史，不把 base64 或正文写进任何落库字段。

## 7. 验收标准

1. `tests/test_tech_file_preview_in_card_and_auth_red.py` 全绿；
2. 全量 `unittest discover` 全绿；
3. `node --check tech_app/frontend/app.js`、`node --check tech_app/frontend/agent-chat.js` 通过；
4. `git diff --check` 干净；
5. 无头 Chrome 实测：打开任务文件卡片 → 点一个图片文件，卡片内直接出现图片、不新开标签页、
   地址栏不变；点一个 CSV 出现文本；点 STL 出现「不在卡片内预览」+ 下载；
   预览期间 Network 里那条文件请求带 `Authorization`（不是 `?token=`），不再出现那句 JSON 错误。

# Spec（批 11）：快速报价面板的图纸/文件入口 —— 「解析结果 → 匹配输入」上屏

- 状态：Spec + 红测（已实现）（9-21 落地，见 changelog ## 254）
- 覆盖：逆向快速报价前端 —— `tech_app/frontend/quick-quote-panel.js`、`cpq_agent_server.py`
  的 `/api/quick-quote/parse` 出参、`cpq_quick_quote_match.py` 的标签出口。
- 红测：`tests/test_quick_quote_panel_parse_entry_red.py`
- 前序：`docs/specs/quick-quote-5-file-parsing.md`（
  `POST /api/quick-quote/parse` + 客户端 `cpq_quick_quote_file.parse_file()`）、
  `docs/specs/quick-quote-2-case-retrieval.md`（候选检索）、本仓批 9/10（解析口径）。

## 0. 为什么有这批（本机与 34 上查实，不是推断）

批 5 把服务端入口做完了，批 2 把候选检索做完了，批 9/10 把解析口径收口了 —— 但**页面上没有
入口**：

```
grep -rn "QUICK_QUOTE_PARSE_PATH\|/api/quick-quote/parse" 前端目录   → 0 处
技术工艺侧 8010：POST /api/file/parse                                 → 200（统一解析服务）
报价侧      8010：POST /agents/quote/api/quick-quote/parse            → 401「请先登录」（登录后可用）
报价首页点「快速报价」：只列标准案例库，没有任何上传入口
```

也就是说：销售拿到一张 `酒盒.dwg`，在页面上**没有地方可以丢进去**看解析结果与候选案例 ——
这正是用户反复说的「还差一层前端」。解析、匹配、口径都通了，缺的是把面板接上去。

## 1. 契约

### C1 路由同值（前端不造第二份路径）

面板新增 `PARSE_PATH`，值必须与 `cpq_quick_quote_file.QUICK_QUOTE_PARSE_PATH` **逐字相同**
（`/api/quick-quote/parse`），沿用 `CASES_PATH` 的既有做法：前端只写相对路径，基址由
`agentBase()` 给，请求一律走 `apiFetch()`（即页面注入的 `cpqAuthFetch`，统一带票）。

### C2 入口（就在快速报价面板里）

面板里出现一个选择文件的入口与一个 `type="file"` 的 `input`：

- `input` 带 `data-qq-parse-input`，`accept` 覆盖 `.dwg` / `.dxf`（图纸）+ `.pdf` / `.xlsx` / `.xls` /
  `.csv` / `.txt`（需求文件）；
- 选中文件后：浏览器只做一件事 —— **读成 base64**（去掉 `data:...;base64,` 前缀），
  `POST {name, data}`（`match` 缺省即 true）；
- **浏览器不转换图纸、不调技术工艺接口、不把图纸送视觉模型**：转换与语义都发生在服务端
  （批 5 口径：报价侧只当客户端）。

### C3 纯函数 `quickQuoteParseView(result)`（可被 node 直接执行）

`result` = `POST /api/quick-quote/parse` 的响应体原样。返回值：

```js
{kind, ok, headline, advice, retryable,
 capability: {text, provider, provider_version, dwg},
 inputs: [{key, label, value, source}],     // 按 key 升序
 missing: [{key, label}],                   // 后端给的顺序（= 匹配键闭集顺序）
 warnings: [string],                        // 逐条、不折叠
 candidates: [...], candidate_total, suggested_case_code, no_candidate_reason,
 inputs_complete, engine_version}
```

约束（红测按源码体断言，不靠 grep 行为）：

- 函数体内**不得**出现 `document` / `window` / `sessionStorage` / `localStorage` / `fetch(`；
- 也不得调用本模块其它函数（红测把函数体单独交给 node 执行）；
- `ok === true` → `kind` 取 `result.parse.kind`（缺省 `"ok"`）；
  `headline` 用**计数**说话，不引任何词表：
  `"解析成功，读出 N 项匹配输入，还有 M 项要人工补"`；
- `ok !== true` → `kind` 取 `result.kind`（缺省 `"error"`），`headline` = `result.error` **逐字**，
  `advice` = `result.advice` **逐字**（没有则空串）；
- `retryable` 只在 `kind === "service_unavailable"` 时为 `true`；
- `capability.text`：能看出解析器与是否支持 DWG —— `"解析器 <provider> <version>；DWG：支持|不支持"`；
  provider 或 dwg 缺失时写 `"解析器能力未知"`（**不许**把"未知"说成"不支持"）；
- `inputs` 的 `label` 一律取后端下发的 `result.labels[key]`；**后端没给就退回 key 本身**
  （前端**不许**自带第二份中文字段表）。

### C4 标签的唯一事实源在后端

`cpq_quick_quote_match` 新增公开出口 `input_labels(keys) -> {key: 中文标签}`（内部沿用既有
`qq_case.FIELD_LABELS` + 本模块 `_INPUT_LABELS`，不新造表）；`/api/quick-quote/parse` 的出参
新增 `labels`，覆盖 `inputs` 与 `missing` 里的**每一个键**，其余既有键
（`ok` / `parse` / `inputs` / `missing` / `sources` / `warnings` / `capability` / `match`）一字不动。

### C5 三类失败分别说清

| `kind` | 页面必须说 | 依据 |
| --- | --- | --- |
| `unsupported` | 后端 `error` 逐字 + `advice` 逐字 | 格式不支持：不要含糊成"解析失败" |
| `service_unavailable` | 后端 `advice` 逐字 + 可重试提示 | 统一解析服务不在线：文字/Excel/PDF 不受影响，图纸可重试或转人工 |
| 其它 `ok:false` | 后端 `error` 逐字 | 不许改写、不许吞 |

### C6 候选只渲染、不判断

候选、`suggested_case_code`、`no_candidate_reason`、`inputs_complete`、`engine_version` 一律
**原样**透出；没有候选时显示后端 `no_candidate_reason` 的**逐字**文案（批 2 口径），
前端不自己排序、不自己算相似度、不自己说"没有合适案例"。

### C7 边界（与既有批次一致）

- 不引用技术工艺链路的任何接口/模块（`/api/projects/`、`drawing-flow`、`cpq_tech_bridge`）；
- 不在浏览器侧做 DWG/DXF 转换或几何解析（不许出现 `dwg2dxf`、`oda`、`xvfb`）；
- 不新开第二套案例检索或第二份字段标签表；
- 既有导出一个不少（`QUOTE_MODES` / `MODE_LABELS` / `CASES_PATH` / `REASON_LABELS` /
  `DIFF_HEADERS` / `QUOTE_ACTIONS` / `QUOTE_ACTION_LABELS` / `cases` / `renderReadiness` /
  `renderDiffTable` / `renderQuote` / `render` / `open` / `close`）；
- `node --check tech_app/frontend/quick-quote-panel.js` 通过。

## 2. 允许修改范围（只这 4 处）

1. `tech_app/frontend/quick-quote-panel.js`；
2. `cpq_agent_server.py` 的 `_handle_quick_quote_parse()` 出参（**只加 `labels`**）；
3. `cpq_quick_quote_match.py`（只加 `input_labels()`）；
4. `changelog/changelog_9_21_25.md` 追加本批条目。

## 3. 禁止事项

- 不许改批 5 / 批 2 / 批 9 / 批 10 的任何断言与出参语义；
- 不许把解析搬到前端（哪怕只是"再算一遍"）；不许前端写第二份 `MATCH_INPUT_KEYS` 或标签表；
- 不许给图纸编默认尺寸/克重，不许把 `outline_size`（图纸幅面）当内尺寸；
- 不许动视觉路径（`POST /parse`）与非快速报价的入口；
- 不许改 `tests/` 下任何既有文件。

## 4. 验收命令

```bash
node --check tech_app/frontend/quick-quote-panel.js
./open-claude/.venv/bin/python -m unittest tests.test_quick_quote_panel_parse_entry_red -v
./open-claude/.venv/bin/python -m unittest \
  tests.test_quick_quote_case_library_readiness_red \
  tests.test_quick_quote_delta_rule_authority_red \
  tests.test_quick_quote_field_workspace_red \
  tests.test_quick_quote_file_parsing_red \
  tests.test_quick_quote_material_gsm_red
```

手工（登录后，页面）：报价首页 → 快速报价 → 选 `酒盒.dwg` → 看到解析器版本、DWG 支持、
匹配输入（面纸克重 235）、要人工补的字段（内长/内宽/内高…）、候选/无候选原因。

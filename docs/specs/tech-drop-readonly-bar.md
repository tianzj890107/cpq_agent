# Spec：去掉「以财务经理身份登录…这里是只读」的只读横幅

## 背景（用户要求）

「不要这个：**当前以 财务经理 身份登录，你负责 2.3 成本测算：那一步可以测算、确认并对外发送；
2.1 图纸解析、2.2 组装与整合（整合图纸 / 参数推荐 / 组装工艺）归工艺经理，这里是只读。**」
并补充：「现在会出现两处这个，全都不要」。

现状：`tech_app/frontend/cpq-sso.js` 的 `showReadonlyBar()` 在 `!state.canWrite` 时往
`document.body` 插一条 `.cpq-sso-bar` 横幅 —— 统一工作台外层（tech-workbench.html）与嵌在
里面的阶段页（iframe）各自都加载同一份 `cpq-sso.js`，于是同一句 2.3 归属说明会同时出现在
外层和 iframe 里，就是用户看到的「两处」。

## 范围

- 只改 `tech_app/frontend/cpq-sso.js`（以及被本批推翻的旧断言）。
- 不动：写请求拦截（403 预判与 toast）、登录墙 `showLoginWall()`、`openCpqLogin()`、
  `COST_URL_PATTERNS` 与 `isCostUrl()` 的能力分流、后端权限与路由。

## R1 横幅整体下线

- R1.1 `showReadonlyBar()` 与它的调用点删除；`.cpq-sso-bar` 的样式一并删除
  （`cpq-sso.js` 里不再出现 `showReadonlyBar` / `cpq-sso-bar`）。
- R1.2 「这里是只读」「你负责 2.3 成本测算」这段归属说明不再出现在任何前端文件里。
- R1.3 写请求被拦时的 toast 也收短：不再复述「负责的是 2.3 成本测算，其余步骤只读」，
  只说清「这一步归工艺经理 / 当前账号没有权限」，避免同一句归属说明换个地方再出现。

## R2 能力不缩水

- R2.1 写拦截仍是 `state.canWrite || (state.canCost && isCostUrl(url))`，仍返回 403 结构化响应。
- R2.2 登录墙、切号入口、SSO 检查与 `cpq-sso-ready` 事件、`CpqSso` 对外接口不变。
- R2.3 后端 `_require` 才是权限判定方，前端行为不变。

## 验收

- 红测：`tests/test_tech_drop_readonly_bar_red.py`（先红后绿）。
- 被推翻的旧断言更新 1 处：`tests/test_tech_params_autofill_and_soft_gates_red.py` 的
  `test_readonly_bar_names_params_as_process_step`（原意是「只读横幅要说清参数推荐归工艺经理」，
  横幅按用户要求整体下线，改为断言写拦截会把这一步归工艺经理说清楚），注明「契约更新（只读横幅下线批次）」。
- 全量：`python3 -m unittest discover -s tests -p 'test_*.py'` 不新增失败。
- 浏览器：财务经理账号进统一工作台与阶段页，都不再出现那条黄色只读横幅；点写操作仍会提示。

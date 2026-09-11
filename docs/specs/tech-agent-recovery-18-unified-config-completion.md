# 技术工艺 Agent 能力恢复第 18 步：统一配置落实补完 Spec

## 范围

第 18 步不新增业务能力，只把已经写好的统一配置 Spec
（`docs/specs/unified-model-settings-and-api-keys.md`）在技术工艺侧**真正落实干净**，
并和业务 Agent 接线分开实施，避免故障定位混在一起。

已完成、本步只做守卫的三项：`/api/settings` 是唯一前端接口、`cpq_settings.json` 是唯一
持久化源、API Key 按 provider 全局共享；设置入口是技术工艺页头与设置按钮共用的居中模态
卡片（`#techModelSettingsMask` / `#techModelSettings`，`role="dialog"` /
`aria-modal="true"`），没有第二套页签。

本步要补掉的真实缺口：技术工艺看板页 `tech_app/frontend/assembly-integration.js` 与
`tech_app/frontend/cost-review.js` 仍按**两个模型**渲染模型药丸 ——
`text_options` + `vision_options` 拼出候选清单、`text_model` 当显示值、`vision_model`
写进 tooltip。这与「报价模型是唯一模型配置」冲突：报价只给一个 `model` 字段。

## 1. 唯一模型

- 技术工艺所有前端（`.js` / `.html`）一律只认 `/api/settings` 返回的单一字段：
  `settings.model`（当前模型 id）与 `settings.options`（同一份模型清单，用于取 label）。
- 不得再出现 `text_model` / `vision_model` / `text_options` / `vision_options` 这类
  「语言模型 / 多模态模型」双份口径。
- 模型药丸 / 页头模型名只能显示一个模型；不再把「语言模型」「多模态模型」两行写进 title。
- 当前模型不支持某项能力（例如图像）时，由既有 `llm_settings.resolve(vision=True)` 如实
  报错并带上实际模型名，不静默换模型、不另设多模态模型。

## 2. 唯一接口与唯一持久化源

- 技术工艺前端不得读写 `/api/llm/settings`；统一走 `llm-settings-panel.js` 与 `/api/settings`。
- `/api/llm/settings` 允许作为旧客户端的兼容别名保留，但它必须委托同一份实现
  （`llm_settings.snapshot(...)` / `llm_settings.update(...)`），不得保存第二份状态、不得
  再写 `tech_data/llm_settings.json`。
- API Key 只存在于 `cpq_settings.json.api_keys`（按 provider 全局共享）；GET 响应、日志、
  异常、审计与页面 DOM 都不得出现明文 Key。

## 3. 居中模态卡片

- 技术工艺的设置入口（页头模型名、侧栏设置按钮）继续共用同一个居中模态卡片；
  全屏遮罩 + 视口水平垂直居中，点击遮罩或按 Escape 关闭，卡片内点击不关闭。
- 卡片只有一套字段：模型、Temperature、最大输出 Tokens、深度思考、当前 provider 的
  API Key；不存在「助手模型 / 技术工艺模型」双页签，也不存在技术工艺专属 Key 表单。

## 4. 已有会话立即生效

- 报价侧保存后不重启服务，技术工艺已存在的 Agent 会话下一次请求即读取新模型 / 新 Key；
  由 `tests/test_unified_model_settings_backend_dynamic.py` 守护，本步不改其行为。

## 5. 边界

- 不新增 / 删除 `@app.` 路由；不改报价流程、技术工艺五大流程、会话、任务、历史数据与角色权限。
- 不删除历史设置或业务数据；不把真实 Key 写进源码、测试、fixture 或 changelog。
- 不改 `open-claude` 包内文件；不通过硬编码 Key、固定模型或重启服务伪造「全局生效」。
- 本步只改两处看板页的模型药丸与必要的文案，不动 2.2 / 2.3 的任何业务逻辑。

## 6. 验收标准

1. `assembly-integration.js`、`cost-review.js` 不再出现 `text_model` / `vision_model` /
   `text_options` / `vision_options`；
2. 两个看板页的模型药丸改读 `settings.model` + `settings.options`，且仍复用
   `window.LlmSettingsPanel`；
3. 技术工艺前端（全部 `.js` / `.html`）无 `/api/llm/settings`，唯一面板走 `/api/settings`；
4. 技术工艺设置仍是唯一居中模态卡片（`#techModelSettingsMask` / `#techModelSettings`，
   `role="dialog"`），无 `role="tab"` 双页签；
5. `llm_settings.py` 不指向 `llm_settings.json`、Key 仍按 provider 全局读取；
6. `/api/llm/settings` 兼容路由只调用 `llm_settings.snapshot(...)` / `llm_settings.update(...)`；
7. 既有 `test_unified_model_settings_and_api_keys_red` 与
   `test_unified_model_settings_backend_dynamic` 保持全绿。

## 7. 对应测试

`tests/test_tech_unified_config_completion_red.py`

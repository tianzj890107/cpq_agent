# 技术工艺 Agent 按所选模型提供商判断可用性 Spec

## 问题与事实

技术工艺统一模型设置支持 Qwen、OpenAI、DeepSeek、Anthropic 和 CPQ 本地兼容网关，默认语言模型也是 Qwen，但 `services/oc_agent.py::available()` 仍硬编码要求 `ANTHROPIC_API_KEY`。所以即使 Qwen Key 已配置，页面仍会显示 Agent 不可用。

当前模型和各 provider Key 的运行时事实源是 `services.llm_settings`；`llm_settings.resolve(vision=False)` 已返回所选语言模型的 `model/provider/base_url/native/api_key`。Qwen Key 可以来自前端持久化设置，也可以首次从 `DASHSCOPE_API_KEY/QWEN_API_KEY` 导入。

本地根 `.env` 和 `tech_app/.env` 当前均不存在，但存在 `tech_app/.env.example`。`.env` 是可选启动配置，不是前端已保存设置或 CPQ 本地网关注入场景的强制文件，不能因为没有 `.env` 就判定 Agent 不可用。

## 可用性与实际调用契约

1. `oc_agent.available()` 调用 `llm_settings.resolve(vision=False)`，按当前语言模型的 provider/key 判断。
2. Qwen + Qwen Key 不依赖 Anthropic Key；OpenAI、DeepSeek、CPQ 本地网关同理。
3. 只有当前选中 Anthropic 模型时，才提示缺少 Anthropic Key。
4. 缺 Key 的错误显示当前 provider，例如“未配置阿里云百炼 API Key”。
5. 创建 Agent 会话和每轮实际调用也必须使用同一 route 的 model/provider/base_url/api_key/protocol，不能只放宽探测后仍调用 Anthropic。
6. 切换 provider 或 Key 后重建对应 client，不能复用旧 provider 会话客户端。
7. 如果 open-claude 原生客户端不支持当前 provider，应在受控集成层增加 OpenAI-compatible/provider 适配或复用统一 `llm_client`；不得伪报 available。

## `.env` 边界

- 不创建、提交、读取输出或打印真实 `.env` 和 API Key。
- 修订 `.env.example` 开头“必须 Anthropic”的过时说明，明确可按所选 provider 配置，也可从前端设置页保存。
- 不删除仍供部署使用的 provider 环境变量示例。

## 验收

- Qwen 已选择且 Qwen Key 已配置、Anthropic Key 为空：Agent meta 可用，对话实际走 Qwen。
- Qwen Key 为空：提示缺少 Qwen/阿里云百炼 Key。
- Anthropic 已选择且 Anthropic Key 为空：提示缺少 Anthropic Key。
- UI 保存模型或 Key 后 readiness 与新会话同步更新，不泄露密钥。


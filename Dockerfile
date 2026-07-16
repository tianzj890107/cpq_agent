# 配置报价CPQ —— 一体化服务镜像
# 单端口 8010 同时提供：静态前端（首页/三助手工作台）+ 三个智能体 API（/agents/quote|config|rule）。
# 业务数据源 = 外部远程 Postgres（不在镜像内）；LLM 走各 provider 云 API（Key 用环境变量传入）。
FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5

WORKDIR /app

# 1) 先装依赖（单独一层，利用缓存）。这些包均有 manylinux wheel，slim 基础镜像即可，无需编译工具链。
#    国内构建机直连 pypi.org 会超时/被墙，镜像源已在上面 ENV(PIP_INDEX_URL) 固定为阿里云，pip 自动读取。
COPY requirements.txt ./
RUN pip install \
      -i https://mirrors.aliyun.com/pypi/simple/ \
      --trusted-host mirrors.aliyun.com \
      -r requirements.txt

# 2) 再拷应用源码（含 open-claude/ 引擎——已编译为 .pyc 的字节码形态（源码保护），走 sys.path 引用，不 pip 安装；
#    .venv / 机密 settings / 历史 / 大表格 已由 .dockerignore 排除）。
COPY . .

# 3) 业务数据源 = 远程 Postgres，默认取需求给定值，均可用环境变量覆盖。
ENV CPQ_PG_HOST=172.16.5.181 \
    CPQ_PG_PORT=32444 \
    CPQ_PG_USER=postgres \
    CPQ_PG_PASSWORD=postgres \
    CPQ_PG_DATABASE=metabase \
    CPQ_PG_SCHEMA=master_data

# 说明：LLM Provider 的 API Key 在 docker run 时通过环境变量传入，例如：
#   -e DEEPSEEK_API_KEY=sk-xxx  -e CLAUDE_MODEL=deepseek-v4-pro
# 支持的 Key 环境变量：ANTHROPIC_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY /
#   DASHSCOPE_API_KEY(QWEN_API_KEY) / ZHIPUAI_API_KEY(GLM_API_KEY) / MOONSHOT_API_KEY(KIMI_API_KEY)

EXPOSE 8010

# 容器内必须绑 0.0.0.0
CMD ["python", "cpq_suite_server.py", "--host", "0.0.0.0", "--port", "8010"]

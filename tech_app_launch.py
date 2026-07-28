# -*- coding: utf-8 -*-
"""
技术工艺 App（照搬自 process_drawing 的 FastAPI 全链路）启动器。

由 cpq_suite_server.py 作为子进程拉起：在 127.0.0.1:8012 跑 uvicorn，8010 一体化服务
反向代理 /api/* /apps/* 与其前端页面到这里。大模型调用**复用 CPQ 的模型网关**——
读取 cpq_settings.json 的「local」一节（OpenAI 兼容网关），映射成 process_drawing 的
Qwen(OpenAI 兼容) provider，从而与四个助手用同一个模型端点。

单独运行调试：
    python tech_app_launch.py --port 8012
"""
import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TECH_APP_DIR = os.path.join(SCRIPT_DIR, "tech_app")
SETTINGS_PATH = os.path.join(SCRIPT_DIR, "cpq_settings.json")

# 与 cpq_suite_server 一致：本进程被父进程以管道方式启动（写日志文件 / Docker）时，
# stdout 会用系统 locale 编码，打印中文会 UnicodeEncodeError 直接把本进程打挂。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _load_local_gateway():
    """返回 cpq_settings.json 里配置好的本地网关 (base_url, model, api_key) 或 None。"""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    local = data.get("local") if isinstance(data, dict) else None
    if not isinstance(local, dict):
        return None
    base = (local.get("base_url") or "").strip()
    model = (local.get("model") or "").strip()
    if base and model:
        return base, model, (local.get("api_key") or "").strip()
    return None


def _apply_llm_env():
    """把 CPQ 的模型配置映射到技术工艺 App 的 LLM 环境变量。

    优先级：cpq_settings.json 的 local 网关 > 外部已设置的 LLM_PROVIDER/Key > 保持默认。
    绝不把 Key 写盘或打印。"""
    gw = _load_local_gateway()
    if gw:
        base, model, key = gw
        os.environ["LLM_PROVIDER"] = "qwen"          # Qwen client = OpenAI 兼容，直连本地网关
        os.environ["QWEN_BASE_URL"] = base
        os.environ["QWEN_MODEL"] = model
        os.environ.setdefault("QWEN_TEXT_MODEL", model)
        os.environ["QWEN_API_KEY"] = key or "EMPTY"  # 内网网关常不校验 Key
        os.environ.setdefault("QWEN_ENABLE_THINKING", "false")
        print(f"[tech-app] LLM 复用 CPQ 本地网关：{base}  model={model}", flush=True)
    elif not os.environ.get("LLM_PROVIDER"):
        # 未配置本地网关且外部没指定 provider：默认走 qwen（可用环境变量覆盖）
        os.environ["LLM_PROVIDER"] = os.environ.get("LLM_PROVIDER", "qwen")
        print("[tech-app] 未发现 CPQ 本地网关配置，沿用现有 LLM 环境变量。", flush=True)

    # 运行时数据落 tech_app/tech_data（gitignore；重启不丢项目/需求/报告）
    os.environ.setdefault("DATA_DIR", os.path.join(TECH_APP_DIR, "tech_data"))
    # 与 CPQ 保持一致的兜底：不开鉴权（隐式 system/admin，无需登录）
    os.environ.setdefault("AUTH_ENABLED", "false")


def main():
    parser = argparse.ArgumentParser(description="技术工艺 App 启动器（uvicorn）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8012)
    args = parser.parse_args()

    _apply_llm_env()
    sys.path.insert(0, TECH_APP_DIR)
    os.chdir(TECH_APP_DIR)

    import uvicorn
    print(f"[tech-app] uvicorn backend.main:app @ http://{args.host}:{args.port}", flush=True)
    uvicorn.run("backend.main:app", host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

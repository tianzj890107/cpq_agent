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

    新版 tech_app 的模型路由改成了「模型 → 提供商 → 官方 base_url」
    （backend/services/llm_settings.py），QWEN_BASE_URL 这类环境变量不再被采纳。
    对应地，那里加了一个 base_url 由外部注入的 `cpq_local` 提供商，本函数就是注入方：
    cpq_settings.json 的 local 一节 → TECH_LOCAL_*，于是技术工艺与另外三个助手
    仍然共用同一个模型端点。绝不把 Key 写盘或打印。"""
    gw = _load_local_gateway()
    if gw:
        base, model, key = gw
        os.environ["TECH_LOCAL_BASE_URL"] = base
        os.environ["TECH_LOCAL_MODEL"] = model
        os.environ.setdefault("TECH_LOCAL_TEXT_MODEL", model)
        # 内网网关常不校验 Key，但 llm_client 对空 Key 会直接拒绝调用，给个占位符
        os.environ["TECH_LOCAL_API_KEY"] = key or "EMPTY"
        print(f"[tech-app] LLM 复用 CPQ 本地网关：{base}  model={model}", flush=True)
    else:
        # 未配置本地网关：走上游默认（官方网关 + 各 provider 自己的 Key），
        # 由技术工艺首页/CPQ 设置页里的「模型设置」选模型、填 Key。
        print("[tech-app] 未发现 CPQ 本地网关配置，模型按上游默认的官方网关路由。", flush=True)

    # 运行时数据落 tech_app/tech_data（gitignore；重启不丢项目/需求/报告/模型设置）
    os.environ.setdefault("DATA_DIR", os.path.join(TECH_APP_DIR, "tech_data"))
    # 登录接入 CPQ：身份与角色由 cpq_auth 提供（销售经理 / 工艺经理），技术工艺不再
    # 维护第二套账号。工艺经理可执行技术工艺的全部操作，其余角色只读。
    # 校验回调打到一体化服务自身，端口跟随 CPQ_SUITE_PORT。
    # CPQ_AUTH_BASE_URL 正常由 cpq_suite_server 按自己的真实端口注入；单独调试本启动器
    # 时才用默认的 8010。
    os.environ.setdefault("CPQ_SSO", "true")
    os.environ.setdefault("CPQ_AUTH_BASE_URL", "http://127.0.0.1:8010")
    # tech_app 自带的那套登录在 SSO 模式下整体停用；保留开关是为了能单独调试 tech_app
    # （CPQ_SSO=false AUTH_ENABLED=true 时它照旧工作）。
    os.environ.setdefault("AUTH_ENABLED", "false")
    # 新版 2.1 图纸解析 Agent（backend/services/oc_agent.py）要用 open-claude。
    # tech_app 自己不带一份，直接复用 CPQ 根目录下那份，避免仓库里出现第三份拷贝。
    os.environ.setdefault("OPEN_CLAUDE_DIR", os.path.join(SCRIPT_DIR, "open-claude"))


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

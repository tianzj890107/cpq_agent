"""cpq_llm —— 三助手共享的 LLM 连接扩展（运行时注入，不修改 open-claude 包）。

提供两个能力：

1. 「本地模型」provider（OpenAI 兼容网关，如公司内网 vLLM 池）：
   base_url / api_key / 模型名 全部由用户在「模型设置」里手动填写；
   请求时自动注入 extra_body：
       {"chat_template_kwargs": {"enable_thinking": <bool>},
        "truncate_prompt_tokens": <int>}          # 未填则不传
   示例目标网关：https://ai-pool.evebattery.com/v1（Qwen3-235B-A22B-w8a8）。

2. DeepSeek 线上 think（深度思考）模式：
   设置里勾选「深度思考」后，发往 deepseek 的请求把模型换成思考版
   （默认 deepseek-reasoner，可用环境变量 DEEPSEEK_THINK_MODEL 覆盖）。

实现方式：往 open_claude.config 的 PROVIDERS/_MODEL_PROVIDERS 里注册数据，
并包一层 openai_compat._client（stream/send 都走它），在 create(**kwargs)
时按 provider 注入 extra_body / 重写模型名。持久化仍走各服务器的
cpq_settings.json（"local" 一节 + 既有 "thinking" 布尔）。
"""

import os
import threading

from open_claude import openai_compat
from open_claude import config as oc_config
from open_claude.config import PROVIDERS

LOCAL_PROVIDER = "local"
LOCAL_SENTINEL = "__local__"          # 前端下拉里「本地模型」选项的 id
_LOCAL_KEY_ENV = "LOCAL_LLM_API_KEY"

_LOCK = threading.Lock()
_INSTALLED = False

# 本地模型连接配置（configure_local 更新；请求注入时读取）
_LOCAL = {
    "base_url": "",
    "api_key": "",
    "model": "",
    "enable_thinking": False,
    "truncate_prompt_tokens": None,
}
# 「深度思考」开关（apply_settings 里同步；deepseek 请求据此换思考版模型）
_DEEPSEEK_THINKING = False


# ---------------------------------------------------------------------------
# 安装：注册 provider + 给 openai_compat._client 打补丁
# ---------------------------------------------------------------------------

def install():
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return
        _INSTALLED = True

    PROVIDERS[LOCAL_PROVIDER] = {
        "label": "本地模型",
        "env": [_LOCAL_KEY_ENV],
        "base_url": None,   # 由 configure_local 设置（env LOCAL_BASE_URL 优先）
    }

    _orig_client = openai_compat._client

    def _patched_client(provider: str):
        client = _orig_client(provider)
        orig_create = client.chat.completions.create

        def create(**kwargs):
            if provider == LOCAL_PROVIDER:
                # 注入本地网关要求的 extra_body（vLLM/SGLang 风格）
                eb = dict(kwargs.get("extra_body") or {})
                ctk = dict(eb.get("chat_template_kwargs") or {})
                ctk["enable_thinking"] = bool(_LOCAL.get("enable_thinking"))
                eb["chat_template_kwargs"] = ctk
                tpt = _LOCAL.get("truncate_prompt_tokens")
                if tpt:
                    eb["truncate_prompt_tokens"] = int(tpt)
                kwargs["extra_body"] = eb
            elif provider == "deepseek" and _DEEPSEEK_THINKING:
                # 深度思考：换成 DeepSeek 思考版模型（官方 API 以模型名区分）
                kwargs["model"] = os.environ.get(
                    "DEEPSEEK_THINK_MODEL", "deepseek-reasoner")
            return orig_create(**kwargs)

        client.chat.completions.create = create
        return client

    openai_compat._client = _patched_client


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def configure(saved: dict):
    """启动时按 cpq_settings.json 恢复：local 一节 + thinking 开关。"""
    saved = saved or {}
    if isinstance(saved.get("local"), dict):
        configure_local(saved["local"])
    set_deepseek_thinking(saved.get("thinking"))


def configure_local(cfg: dict):
    """应用「本地模型」连接配置（缺省字段保留旧值；api_key 留空=不修改）。"""
    cfg = cfg or {}
    with _LOCK:
        old_model = _LOCAL.get("model")
        for k in ("base_url", "model"):
            if cfg.get(k) is not None:
                _LOCAL[k] = str(cfg[k]).strip()
        if cfg.get("api_key"):
            _LOCAL["api_key"] = str(cfg["api_key"]).strip()
        if "enable_thinking" in cfg:
            _LOCAL["enable_thinking"] = bool(cfg.get("enable_thinking"))
        if "truncate_prompt_tokens" in cfg:
            v = cfg.get("truncate_prompt_tokens")
            try:
                _LOCAL["truncate_prompt_tokens"] = int(v) if v else None
            except (TypeError, ValueError):
                _LOCAL["truncate_prompt_tokens"] = None

        # base_url / api_key 通过 open-claude 的既有机制生效：
        # get_provider_base_url 读 LOCAL_BASE_URL；get_api_key_for 读 LOCAL_LLM_API_KEY。
        if _LOCAL["base_url"]:
            os.environ["LOCAL_BASE_URL"] = _LOCAL["base_url"]
            PROVIDERS[LOCAL_PROVIDER]["base_url"] = _LOCAL["base_url"]
        # 内网网关常不校验 Key，留空时用占位符（OpenAI SDK 要求非空）
        os.environ[_LOCAL_KEY_ENV] = _LOCAL["api_key"] or "EMPTY"

        # 把模型名映射到 local provider（否则 Qwen3-xxx 会被前缀推断成 qwen 云端）
        if old_model and old_model != _LOCAL["model"]:
            if oc_config._MODEL_PROVIDERS.get(old_model) == LOCAL_PROVIDER:
                del oc_config._MODEL_PROVIDERS[old_model]
        if _LOCAL["model"]:
            oc_config._MODEL_PROVIDERS[_LOCAL["model"]] = LOCAL_PROVIDER


def set_deepseek_thinking(flag):
    global _DEEPSEEK_THINKING
    _DEEPSEEK_THINKING = bool(flag)


# ---------------------------------------------------------------------------
# 查询（给 current_settings / models_catalog / apply_settings 用）
# ---------------------------------------------------------------------------

def local_model_id() -> str:
    return _LOCAL.get("model") or ""


def local_configured() -> bool:
    return bool(_LOCAL.get("base_url") and _LOCAL.get("model"))


def local_public() -> dict:
    """返回可回显给前端的本地配置（不含 api_key 明文）。"""
    return {
        "base_url": _LOCAL.get("base_url") or "",
        "model": _LOCAL.get("model") or "",
        "enable_thinking": bool(_LOCAL.get("enable_thinking")),
        "truncate_prompt_tokens": _LOCAL.get("truncate_prompt_tokens"),
        "has_key": bool(_LOCAL.get("api_key")),
    }


def local_persist() -> dict:
    """写入 cpq_settings.json 的形态（含 api_key；该文件不进 git/镜像）。"""
    return dict(_LOCAL)


def catalog_entry() -> dict:
    """模型下拉里的「本地模型」项。"""
    m = local_model_id()
    return {
        "id": LOCAL_SENTINEL,
        "label": "本地模型（OpenAI 兼容，手动配置）" + (("：" + m) if m else ""),
        "provider": LOCAL_PROVIDER,
        "provider_label": "本地模型",
        "configured": local_configured(),
    }

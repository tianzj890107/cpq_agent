"""全局大模型配置 —— 首页「模型设置」与 2.1 Agent 小窗共用的唯一真相源。

界面上只暴露六项：

    多模态模型 · 语言模型 · 温度 · 最大 token · 是否思考 · API Key

**按所选模型的提供商走各自的官方网关。** 之前平台把所有请求都发往部署时配的
那一个 MaaS 兼容端点（ws-…maas.aliyuncs.com），于是选了 opus5 也只是拿这个 id
去问一个不认识它的网关；再加上失败后静默降级到旧模型池，用户看到的"AI 解析"
其实出自一个他从没选过的型号。现在：

  · 模型 → 提供商 → 官方 base_url + 该提供商的 Key（PROVIDERS）
  · 不再有模型池，也没有任何降级链；配的哪个就用哪个，失败就如实报错。

**Agent 对话用的就是这里的「语言模型」**，不单列"对话模型" —— 否则又会变成
两个模型设置、两处对不上。

密钥按提供商分别保存（选了哪两个模型就只需要哪几把 Key），只写不读：
接口永远只回「是否已配置」和打码提示。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Optional

from ..config import DATA_DIR

_PATH = DATA_DIR / "llm_settings.json"
_lock = threading.RLock()

# 后台常量：不进设置界面，但推理时仍然要用。
THINKING_BUDGET = 8000
MAX_ITERATIONS = 30

# 可选模型清单（受控白名单）。multimodal 决定它能不能出现在「多模态模型」里；
# DeepSeek 没有视觉能力，让它可选等于允许把图纸解析配崩。
MODELS: tuple[dict[str, Any], ...] = (
    {"id": "claude-opus-5",     "label": "Opus 5",            "provider": "anthropic", "multimodal": True},
    {"id": "gpt-5.6-sol",       "label": "GPT-5.6 Sol",       "provider": "openai",    "multimodal": True},
    {"id": "qwen3.5-plus",      "label": "Qwen3.5 Plus",      "provider": "qwen",      "multimodal": True},
    {"id": "qwen3.8-max",       "label": "Qwen3.8 Max",       "provider": "qwen",      "multimodal": True},
    {"id": "deepseek-v4-pro",   "label": "DeepSeek V4 Pro",   "provider": "deepseek",  "multimodal": False},
    {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash", "provider": "deepseek",  "multimodal": False},
)

# ——【CPQ 定制】本地网关（配置报价CPQ 一体化服务注入）—————————————————————
# 本 App 嵌在 CPQ 里时，模型要和另外三个助手走**同一个** OpenAI 兼容端点，
# 由 tech_app_launch.py 从 cpq_settings.json 的 local 一节读出来注入这三个环境变量。
# 上游"模型 → 提供商 → 官方 base_url"的规则不变，只是多一个 base_url 来自注入的
# 提供商；没注入时下面的分支整段不生效，行为与上游完全一致。
CPQ_LOCAL_PROVIDER = "cpq_local"
_CPQ_BASE_URL = os.getenv("TECH_LOCAL_BASE_URL", "").strip().rstrip("/")
_CPQ_MODEL = os.getenv("TECH_LOCAL_MODEL", "").strip()
_CPQ_TEXT_MODEL = os.getenv("TECH_LOCAL_TEXT_MODEL", "").strip() or _CPQ_MODEL
CPQ_LOCAL_READY = bool(_CPQ_BASE_URL and _CPQ_MODEL)

if CPQ_LOCAL_READY:
    # 网关自报的模型 id 直接作为白名单项：它就是这个端点唯一认识的名字。
    # 视觉能力无法探测，按 CPQ 的既有约定当作多模态（图纸解析要用）。
    _extra = [{"id": _CPQ_MODEL, "label": f"本地网关 · {_CPQ_MODEL}",
               "provider": CPQ_LOCAL_PROVIDER, "multimodal": True}]
    if _CPQ_TEXT_MODEL != _CPQ_MODEL:
        _extra.append({"id": _CPQ_TEXT_MODEL, "label": f"本地网关 · {_CPQ_TEXT_MODEL}",
                       "provider": CPQ_LOCAL_PROVIDER, "multimodal": False})
    MODELS = tuple(_extra) + MODELS          # 排在最前：默认选中的就是它

VISION_MODELS = tuple(item["id"] for item in MODELS if item["multimodal"])
TEXT_MODELS = tuple(item["id"] for item in MODELS)
_MODEL_PROVIDER = {item["id"]: item["provider"] for item in MODELS}
_MODEL_LABEL = {item["id"]: item["label"] for item in MODELS}

# 每个提供商的**官方网关**与密钥环境变量。
# native=True 表示走该厂商自己的 SDK（Anthropic），其余都是 OpenAI 兼容协议。
PROVIDERS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "env": ("ANTHROPIC_API_KEY",),
        "native": True,
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "env": ("OPENAI_API_KEY",),
        "native": False,
    },
    "qwen": {
        "label": "阿里云百炼",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "env": ("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
        "native": False,
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "env": ("DEEPSEEK_API_KEY",),
        "native": False,
    },
}

if CPQ_LOCAL_READY:   # 【CPQ 定制】base_url 来自注入而非硬编码，其余与其他提供商同构
    PROVIDERS[CPQ_LOCAL_PROVIDER] = {
        "label": "本地网关（CPQ 共用）",
        "base_url": _CPQ_BASE_URL,
        "env": ("TECH_LOCAL_API_KEY",),
        "native": False,
    }

PARAM_RANGES: dict[str, tuple[float, float]] = {
    "temperature": (0.0, 1.0),
    "max_tokens": (256, 64000),
}

_DEFAULTS: dict[str, Any] = {
    # 【CPQ 定制】注入了本地网关就默认选它——嵌在 CPQ 里时管理员不该被迫先去
    # 设置页点一遍才能用；没注入时仍是上游的默认值。
    "vision_model": _CPQ_MODEL if CPQ_LOCAL_READY else "qwen3.5-plus",
    "text_model": _CPQ_TEXT_MODEL if CPQ_LOCAL_READY else "qwen3.5-plus",
    "temperature": None,        # None = 用模型默认值
    "thinking": False,
    "max_tokens": None,         # None = 用默认值
}


def provider_of(model_id: str) -> str:
    """模型属于哪个提供商。白名单外的一律拒绝，不做前缀猜测。"""
    provider = _MODEL_PROVIDER.get(str(model_id or "").strip())
    if not provider:
        raise ValueError(f"模型 {model_id} 不在可选清单内")
    return provider


def label_of(model_id: str) -> str:
    return _MODEL_LABEL.get(model_id, model_id)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _mask(secret: str) -> str:
    secret = (secret or "").strip()
    if not secret:
        return ""
    return f"{secret[:7]}…{secret[-4:]}" if len(secret) > 14 else "已配置"


# --------------------------------------------------------------------------- #
# 持久化
# --------------------------------------------------------------------------- #
def _load() -> dict[str, Any]:
    data = dict(_DEFAULTS)
    data["keys"] = {}
    try:
        saved = json.loads(_PATH.read_text(encoding="utf-8"))
        if isinstance(saved, dict):
            for key in _DEFAULTS:
                if key in saved:
                    data[key] = saved[key]
            if isinstance(saved.get("keys"), dict):
                data["keys"] = {k: str(v) for k, v in saved["keys"].items() if v}
    except (OSError, json.JSONDecodeError):
        pass
    # 【CPQ 定制】存盘里的模型可能已不在白名单里（改了 cpq_settings.json 的本地网关
    # 模型名之后就会这样）。不校正的话，之后每次 resolve/snapshot 都会抛
    # "模型 X 不在可选清单内"，整个 App 起来就是坏的——退回默认值即可。
    if data["vision_model"] not in VISION_MODELS:
        data["vision_model"] = _DEFAULTS["vision_model"]
    if data["text_model"] not in TEXT_MODELS:
        data["text_model"] = _DEFAULTS["text_model"]
    # 首次启动时把 .env 里已有的 Key 收进来，省得管理员再填一遍。
    for name, spec in PROVIDERS.items():
        if data["keys"].get(name):
            continue
        for env_name in spec["env"]:
            value = os.getenv(env_name, "").strip()
            if value:
                data["keys"][name] = value
                break
    return data


_state = _load()


def _persist() -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(_state, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(_PATH, 0o600)
    except OSError:
        pass


def _export_env() -> None:
    """把密钥同步到环境变量 —— open-claude 的 Agent 就是从那里取的。"""
    with _lock:
        keys = dict(_state["keys"])
    for name, spec in PROVIDERS.items():
        value = keys.get(name)
        if value:
            for env_name in spec["env"]:
                os.environ[env_name] = value


_export_env()


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #
def _options(ids: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"id": item["id"], "label": item["label"], "provider": item["provider"]}
            for item in MODELS if item["id"] in ids]


def selected_model(*, vision: bool) -> str:
    with _lock:
        return _state["vision_model"] if vision else _state["text_model"]


def resolve(*, vision: bool) -> dict[str, Any]:
    """把「当前该用哪个模型」解析成一次调用所需的全部信息。

    调用方不再自己拼 base_url / 取环境变量 —— 那正是之前所有请求都跑去同一个
    错误网关的原因。
    """
    model = selected_model(vision=vision)
    provider = provider_of(model)
    spec = PROVIDERS[provider]
    with _lock:
        api_key = _state["keys"].get(provider, "")
    return {
        "model": model,
        "provider": provider,
        "provider_label": spec["label"],
        "base_url": spec["base_url"],
        "native": spec["native"],
        "api_key": api_key,
    }


def inference_params() -> dict[str, Any]:
    """平台自身推理（图纸解析、工艺、成本）要用的参数。

    与 agent_params 读的是同一份状态 —— 温度/最大 token/是否思考对 Agent 对话
    和平台调用**同时**生效。
    """
    with _lock:
        return {key: _state[key] for key in ("temperature", "thinking", "max_tokens")}


def agent_params() -> dict[str, Any]:
    """Agent 会话要用的参数。对话模型就是这里的「语言模型」。"""
    params = inference_params()
    params["agent_model"] = selected_model(vision=False)
    params["thinking_budget"] = THINKING_BUDGET
    params["max_iterations"] = MAX_ITERATIONS
    return params


def snapshot(*, can_edit: bool = False, can_edit_secrets: bool = False) -> dict[str, Any]:
    """两个入口共用的同一份配置。**密钥只回可见度，不回明文。**

    两级权限分开给：
      can_edit          改模型与生成参数 —— 日常运维，工艺经理就该能调；
      can_edit_secrets  改 API Key —— 是密钥，门槛照旧只给管理员。
    原来两件事共用一个 is_admin，于是 CPQ 单点登录下（角色只有销售经理/工艺经理、
    没有 admin）连换个模型都做不到。
    """
    with _lock:
        state = dict(_state)
        keys = dict(state["keys"])
    vision_model = state["vision_model"]
    text_model = state["text_model"]
    # 只列当前两个模型实际用到的提供商 —— 没用到的 Key 摆出来只是噪声。
    used = []
    for provider in dict.fromkeys([provider_of(vision_model), provider_of(text_model)]):
        used.append({
            "provider": provider,
            "label": PROVIDERS[provider]["label"],
            "base_url": PROVIDERS[provider]["base_url"],
            "key_set": bool(keys.get(provider)),
            "key_hint": _mask(keys.get(provider, "")),
        })
    return {
        "editable": bool(can_edit),
        "secrets_editable": bool(can_edit_secrets),
        "vision_model": vision_model,
        "text_model": text_model,
        "vision_options": _options(VISION_MODELS),
        "text_options": _options(TEXT_MODELS),
        "temperature": state["temperature"],
        "max_tokens": state["max_tokens"],
        "thinking": state["thinking"],
        "providers": used,
    }


# --------------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------------- #
EDITABLE_FIELDS = ("vision_model", "text_model", "temperature", "max_tokens",
                   "thinking", "api_key", "api_key_provider")


def touches_secrets(patch: dict) -> bool:
    return bool(patch.get("api_key"))


def update(patch: dict, *, can_edit: bool = False,
           can_edit_secrets: bool = False) -> dict[str, Any]:
    """按传入字段改写全局配置。未传的字段保持不变。

    取值范围在这里夹住，而不是信任前端 —— temperature 传 5 会让整轮请求被
    上游打回，错误信息还很难懂。
    """
    if not can_edit:
        raise PermissionError("修改模型设置需要工艺经理或管理员权限")
    # 密钥单独一道门：模型/参数是日常运维，Key 是机密，两者不该共用一把权限。
    if touches_secrets(patch) and not can_edit_secrets:
        raise PermissionError("修改 API Key 需要系统管理员权限")
    with _lock:
        # 白名单在服务端才真正生效。只靠前端下拉限制，改一次请求就能绕过 ——
        # 把 DeepSeek 配成多模态模型，图纸解析会直接崩在调用里。
        if patch.get("vision_model"):
            model = str(patch["vision_model"]).strip()
            if model not in VISION_MODELS:
                raise ValueError(f"模型 {model} 不能用作多模态模型")
            _state["vision_model"] = model
        if patch.get("text_model"):
            model = str(patch["text_model"]).strip()
            if model not in TEXT_MODELS:
                raise ValueError(f"模型 {model} 不在可选清单内")
            _state["text_model"] = model
        if "temperature" in patch:
            value = patch["temperature"]
            _state["temperature"] = None if value is None else _clamp(
                float(value), *PARAM_RANGES["temperature"])
        if "thinking" in patch:
            _state["thinking"] = bool(patch["thinking"])
        if "max_tokens" in patch:
            value = patch["max_tokens"]
            _state["max_tokens"] = None if value in (None, "") else int(
                _clamp(float(value), *PARAM_RANGES["max_tokens"]))
        key = str(patch.get("api_key") or "").strip()
        if key:
            provider = str(patch.get("api_key_provider") or "").strip()
            if provider not in PROVIDERS:
                raise ValueError("保存 API Key 时必须指明提供商")
            _state["keys"][provider] = key
        _persist()
    if key:
        _export_env()
    # 已经建好的会话要跟上新配置，否则「全局生效」只对新会话成立。
    _apply_to_live_agents(rebuild_client=bool(key))
    return snapshot(can_edit=can_edit, can_edit_secrets=can_edit_secrets)


def _apply_to_live_agents(*, rebuild_client: bool) -> None:
    try:
        from . import oc_agent

        oc_agent.apply_settings(agent_params(), rebuild_client=rebuild_client)
    except Exception:                                   # pragma: no cover - 依赖环境
        pass


def changed_fields(patch: dict) -> list[str]:
    """审计用：只留字段名，密钥的值永远不进日志。"""
    return sorted(key for key in patch if key != "api_key")

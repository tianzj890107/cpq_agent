"""技术工艺的模型配置 —— 唯一事实源是报价的 cpq_settings.json。

以前技术工艺把「多模态模型 / 语言模型 / API Key」另存进
`tech_app/tech_data/llm_settings.json`，界面上写着"全局"，实际只有技术工艺自己在用：
报价换了模型、换了 Key，技术工艺的请求还是老样子。

现在本模块只是一个**适配层**：

  · 读：每次都从根目录 cpq_settings.json（`cpq_shared_settings`）重新解析当前
        报价 model，报价侧保存后不需要重启、也不需要谁通知，下一次调用就是新值；
  · 写：把模型 / 推理参数 / 该 provider 的 Key 写回同一份文件，并转告报价侧的
        /api/settings，让报价、配置、规则三个助手的内存态一起刷新；
  · 只有一个模型：图纸解析、文档分析、工艺推荐、成本测算和 Agent 对话都解析
        同一个报价模型，不再有 vision_model / text_model 之分；
  · 当前模型不支持图像时如实报错（并带上实际模型名），不静默降级、不另设
        "多模态模型"绕开统一要求；
  · 【账号级覆盖】全局之上还有一层可选的"每个账号自己的模型 / 自己的 Key"
        （services/user_llm.py -> services/cpq_auth_client.py -> CPQ 的
        /auth/my/llm，密文落 Postgres）。优先级是「账号 → 全局 → 环境变量」，
        缺项一律回落全局；Temperature / 最大 Tokens / 深度思考仍只有全局一份。
        发起账号由 services/acting_user.py 的上下文带入；没有配置 CPQ 通道时
        客户端如实返回"没有账号级设置"（见 cpq_auth_client._configured）。

密钥安全：Key 只从共享配置取出来交给调用方，本模块不打印、不写日志、不回接口。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Any

from ..config import ROOT_DIR

# 报价的唯一配置在仓库根目录（tech_app 的上一级），读写都走共享模块。
REPO_ROOT = Path(ROOT_DIR).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cpq_shared_settings                                           # noqa: E402

# 后台常量：不进设置界面，但推理时仍然要用。
THINKING_BUDGET = 8000
MAX_ITERATIONS = 30

PARAM_RANGES: dict[str, tuple[float, float]] = {
    "temperature": (0.0, 1.0),
    "max_tokens": (256, 64000),
}

# 报价侧没有配置模型时的默认值（与报价的默认模型一致，不是技术工艺私有默认）。
DEFAULT_MODEL = "qwen3.5-plus"

# 每个提供商的官方网关与该商的 Key 环境变量。
# native=True 表示走厂商自己的 SDK（Anthropic），其余都是 OpenAI 兼容协议。
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

# ——【CPQ 定制】本地网关（由 tech_app_launch.py 从 cpq_settings.json 的 local 一节注入）
# 与另外三个助手共用同一个 OpenAI 兼容端点；没注入时整段不生效。
CPQ_LOCAL_PROVIDER = "cpq_local"
CPQ_LOCAL_BASE_URL = os.getenv("TECH_LOCAL_BASE_URL", "").strip()
CPQ_LOCAL_MODEL = os.getenv("TECH_LOCAL_MODEL", "").strip()
CPQ_LOCAL_TEXT_MODEL = os.getenv("TECH_LOCAL_TEXT_MODEL", "").strip() or CPQ_LOCAL_MODEL
CPQ_LOCAL_READY = bool(CPQ_LOCAL_BASE_URL and CPQ_LOCAL_MODEL)

if CPQ_LOCAL_READY:
    PROVIDERS[CPQ_LOCAL_PROVIDER] = {
        "label": "本地网关（CPQ 共用）",
        "base_url": CPQ_LOCAL_BASE_URL,
        "env": ("TECH_LOCAL_API_KEY",),
        "native": False,
    }

# 显式模型 → 提供商。报价的 model 只会是这里的 id 或本地网关的模型名；
# 表外的按厂商前缀兜底，兜不住就报错，绝不"猜一个"继续跑。
MODEL_PROVIDERS: dict[str, str] = {
    "claude-opus-5": "anthropic",
    "gpt-5.6-sol": "openai",
    "qwen3.5-plus": "qwen",
    "qwen3.8-max": "qwen",
    "deepseek-v4-pro": "deepseek",
    "deepseek-v4-flash": "deepseek",
}
if CPQ_LOCAL_READY:
    for _local_model in (CPQ_LOCAL_MODEL, CPQ_LOCAL_TEXT_MODEL):
        MODEL_PROVIDERS[_local_model] = CPQ_LOCAL_PROVIDER

# 图像能力的显式清单：报价当前模型不在这里、且其 provider 也没有视觉能力时，
# 图纸解析必须报"当前模型不支持该能力"，而不是偷偷换模型。
VISION_MODELS = frozenset(
    model for model, provider in MODEL_PROVIDERS.items()
    if provider in ("anthropic", "openai", "qwen") or provider == CPQ_LOCAL_PROVIDER
)

_MODEL_LABEL = {
    "claude-opus-5": "Opus 5",
    "gpt-5.6-sol": "GPT-5.6 Sol",
    "qwen3.5-plus": "Qwen3.5 Plus",
    "qwen3.8-max": "Qwen3.8 Max",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
    "deepseek-v4-flash": "DeepSeek V4 Flash",
}

_PREFIX_PROVIDERS = (
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("o1", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("qwen", "qwen"),
    ("deepseek", "deepseek"),
)

_lock = threading.RLock()

# 上一次推给 Agent 会话的路由（模型 / 提供商 / 网关 / Key）。会话是在构造 client 时
# 就把 provider 与 Key 读进去的：报价侧改了设置、或者运维换了 <PROVIDER>_BASE_URL，
# 沿用旧 client 会把新模型发给旧厂商。这里只做「有没有变」的比较 —— Key 只以原文存
# 在内存里做相等判断，不打印、不外传、不落盘。
_applied_route: dict[str, str] | None = None


# --------------------------------------------------------------------------- #
# 发起账号（账号级覆盖的入口）
# --------------------------------------------------------------------------- #
def _target_user(user: str = "") -> str:
    """这一次调用是谁发起的：显式传入优先，其次上下文，都没有就是空串（回落全局）。"""
    explicit = str(user or "").strip()
    if explicit:
        return explicit
    try:
        from . import acting_user
        return acting_user.current_acting_user()
    except Exception:                                   # pragma: no cover - 依赖环境
        return ""


def _account_model(user: str) -> str:
    """该账号的个人模型；没有账号或没有记录返回空串。

    读不到（CPQ 不可达且没有缓存）时**不吞异常**：那会把"读不出来"伪装成"这个人
    没设置"，然后拿全局模型与全局 Key 去跑别人的额度。异常一路抛给 HTTP 面转 503。
    """
    if not user:
        return ""
    from . import user_llm
    return str((user_llm.get(user) or {}).get("model") or "").strip()


def _account_key(user: str, provider: str) -> str:
    """该账号在某 provider 的个人 Key；没有账号或没有记录返回空串（同上，不吞异常）。"""
    if not user:
        return ""
    from . import user_llm
    return user_llm.personal_key(user, provider)


def _model_and_source(user: str) -> tuple[str, str]:
    """生效模型 + 来源（account / global）。"""
    personal = _account_model(user)
    if personal:
        return personal, "account"
    return current_model_id(), "global"


def _key_and_source(user: str, provider: str, saved: dict[str, str] | None = None) -> tuple[str, str]:
    """生效 Key + 来源（account / global / env / missing），优先级与 resolve 一致。"""
    personal = _account_key(user, provider)
    if personal:
        return personal, "account"
    keys = _saved_keys() if saved is None else saved
    global_key = str(keys.get(provider) or "").strip()
    if not global_key and provider == CPQ_LOCAL_PROVIDER:      # 报价侧本地模型的旧叫法
        global_key = str(keys.get("local") or "").strip()
    if global_key:
        return global_key, "global"
    for env_name in (PROVIDERS.get(provider) or {}).get("env") or ():
        value = os.getenv(env_name, "").strip()
        if value:
            return value, "env"
    return "", "missing"


def _source_label(source: str, user: str = "") -> str:
    if source == "account":
        return f"账号 {user} 的个人设置" if user else "账号个人设置"
    return "平台默认"


# --------------------------------------------------------------------------- #
# 共享配置读写
# --------------------------------------------------------------------------- #
def _quote_settings() -> dict[str, Any]:
    """报价的唯一配置。每次调用都重新读盘，报价改完立刻生效，不需要重启。"""
    data = cpq_shared_settings.load()
    return data if isinstance(data, dict) else {}


def _saved_keys() -> dict[str, str]:
    return cpq_shared_settings.api_keys(_quote_settings())


def current_model_id() -> str:
    """报价当前选中的模型（唯一模型，技术工艺不再各自选）。"""
    model = str(_quote_settings().get("model") or "").strip()
    if not model:
        return DEFAULT_MODEL
    if model == "__local__" and CPQ_LOCAL_READY:      # 报价的「本地模型」哨兵
        return CPQ_LOCAL_MODEL
    return model


def provider_of(model_id: str) -> str:
    """模型属于哪个提供商。表外按厂商前缀兜底；兜不住如实报错。"""
    model = str(model_id or "").strip()
    if not model:
        raise ValueError("未配置语言模型")
    if model in MODEL_PROVIDERS:
        return MODEL_PROVIDERS[model]
    if model in ("none",):
        raise ValueError("当前为「无模型」模式：技术工艺不调用大模型")
    lowered = model.lower()
    for prefix, provider in _PREFIX_PROVIDERS:
        if lowered.startswith(prefix):
            return provider
    raise ValueError(f"模型 {model} 的提供商无法确定，请在「模型设置」里重新选择")


def label_of(model_id: str) -> str:
    model = str(model_id or "").strip()
    return _MODEL_LABEL.get(model, model)


def base_url_of(provider: str) -> str:
    """网关地址：部署时的 <PROVIDER>_BASE_URL 优先，其次该商的官方网关。"""
    spec = PROVIDERS.get(provider) or {}
    override = os.getenv(f"{provider.upper()}_BASE_URL", "").strip()
    return (override or str(spec.get("base_url") or "")).rstrip("/")


def api_key_of(provider: str, keys: dict[str, str] | None = None) -> str:
    """同一个 provider 在四个助手和技术工艺里读的是同一把全局 Key。"""
    saved = _saved_keys() if keys is None else keys
    key = str(saved.get(provider) or "").strip()
    if key:
        return key
    if provider == CPQ_LOCAL_PROVIDER:
        key = str(saved.get("local") or "").strip()      # 报价侧本地模型的旧叫法
        if key:
            return key
    for env_name in (PROVIDERS.get(provider) or {}).get("env") or ():
        value = os.getenv(env_name, "").strip()
        if value:
            return value
    return ""


def selected_model(*, vision: bool = False) -> str:
    model = current_model_id()
    if vision:
        ensure_vision_capable(model)
    return model


def ensure_vision_capable(model: str | None = None, *, user: str = "") -> str:
    """图纸解析这类必须用图像能力的调用先过这里：不支持就带着**生效模型与来源**报错。

    账号有自己的模型时按账号的模型判断，报错里说清"这是谁的模型"，绝不静默换模型。
    """
    target_user = _target_user(user)
    if model:
        target = str(model).strip()
        personal = _account_model(target_user)
        source = "account" if (personal and target == personal) else "global"
    else:
        target, source = _model_and_source(target_user)
    provider = provider_of(target)
    if target in VISION_MODELS or provider in ("anthropic", "openai", "qwen"):
        return target
    if provider == CPQ_LOCAL_PROVIDER:
        return target
    raise ValueError(
        f"当前生效模型 {target}（来源：{_source_label(source, target_user)}）不支持图像解析，"
        f"请在「模型设置」里改用支持多模态的模型")


def resolve(*, vision: bool, user: str = "") -> dict[str, Any]:
    """把「生效模型」解析成一次调用所需的全部信息。

    优先级：显式 user → 上下文里的发起账号 → 全局兜底（Spec C1）。返回结构与以前
    逐字段一致，既有调用点不传账号也不改签名。调用方不再自己拼 base_url 或取环境
    变量 —— 那正是以前请求跑去别的网关的原因。
    """
    target = _target_user(user)
    model, source = _model_and_source(target)
    if vision:
        ensure_vision_capable(model, user=target)
    provider = provider_of(model)
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise ValueError(f"模型 {model} 的提供商 {provider} 未配置网关")
    api_key, key_source = _key_and_source(target, provider)
    if not api_key and source == "account":
        # 账号选了自己没有 Key、全局也没有该 provider Key 的模型：明确失败，
        # 绝不静默改用全局模型或别的 provider（Spec C9）。
        raise ValueError(
            f"账号 {target} 选用的模型 {model} 需要 {provider} 的 API Key，"
            f"但该账号与平台默认都没有配置 {provider} 的 Key；"
            f"请在「我的模型与密钥」里填写自己的 Key，或把个人模型改回平台默认")
    return {
        "model": model,
        "provider": provider,
        "provider_label": spec.get("label") or provider,
        "base_url": base_url_of(provider),
        "native": bool(spec.get("native")),
        "api_key": api_key,
    }


def effective(user: str = "") -> dict[str, Any]:
    """「现在到底用谁的模型 / 谁的 Key」—— 供接口与界面显示，不重复实现优先级。

    与 resolve() 共用同一套判定；区别只是**不抛错**：账号选了缺 Key 的模型时，
    这里如实报 key_source="missing"，让界面能把问题指出来（真正调用时才由
    resolve() 明确失败）。
    """
    target = _target_user(user)
    model, model_source = _model_and_source(target)
    try:
        provider = provider_of(model)
    except ValueError:
        provider = ""
    _, key_source = _key_and_source(target, provider) if provider else ("", "missing")
    return {
        "model": model,
        "source": model_source,
        "provider": provider,
        "key_source": key_source,
    }


def inference_params() -> dict[str, Any]:
    """温度 / 最大 token / 深度思考 —— 与报价是同一份配置。"""
    data = _quote_settings()
    return {
        "temperature": data.get("temperature"),
        "thinking": bool(data.get("thinking")),
        "max_tokens": data.get("max_tokens"),
    }


def agent_params(user: str = "") -> dict[str, Any]:
    """Agent 会话要用的参数：推理参数取自全局配置，模型取**生效模型**（账号可覆盖）。"""
    params = inference_params()
    params["agent_model"] = _model_and_source(_target_user(user))[0]
    params["thinking_budget"] = _quote_settings().get("thinking_budget") or THINKING_BUDGET
    params["max_iterations"] = MAX_ITERATIONS
    return params


def _options() -> list[dict[str, Any]]:
    """可选模型清单：报价能选的，技术工艺就能选——不额外维护第二份白名单。"""
    options: list[dict[str, Any]] = []
    for model, provider in MODEL_PROVIDERS.items():
        options.append({
            "id": model,
            "label": label_of(model),
            "provider": provider,
            "provider_label": (PROVIDERS.get(provider) or {}).get("label") or provider,
        })
    return options


def _provider_status(providers: list[str]) -> list[dict[str, Any]]:
    keys = _saved_keys()
    out = []
    for provider in providers:
        spec = PROVIDERS.get(provider) or {}
        key = api_key_of(provider, keys)
        out.append({
            "provider": provider,
            "label": spec.get("label") or provider,
            "base_url": base_url_of(provider),
            "configured": bool(key),
            "hint": cpq_shared_settings.mask(key),
        })
    return out


def snapshot(*, can_edit: bool = False, can_edit_secrets: bool = False) -> dict[str, Any]:
    """四个入口共用的同一份配置快照。**密钥只回可见度，不回明文。**

    can_edit / can_edit_secrets 由调用方按角色决定：模型与参数是日常运维，
    API Key 仍只给管理员。
    """
    model = current_model_id()
    try:
        provider = provider_of(model)
    except ValueError:
        provider = ""
    keys = _provider_status(list(dict.fromkeys(
        [provider, *(p for p in MODEL_PROVIDERS.values())])))
    return {
        "editable": bool(can_edit),
        "secrets_editable": bool(can_edit_secrets),
        "model": model,
        "model_label": label_of(model),
        "provider": provider,
        "provider_label": (PROVIDERS.get(provider) or {}).get("label") or "",
        "configured": bool(api_key_of(provider)) if provider else False,
        "options": _options(),
        "keys": keys,
        "temperature": _quote_settings().get("temperature"),
        "max_tokens": _quote_settings().get("max_tokens"),
        "thinking": bool(_quote_settings().get("thinking")),
    }


# --------------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------------- #
EDITABLE_FIELDS = ("model", "temperature", "max_tokens", "thinking",
                   "thinking_budget", "api_key", "api_key_provider")


def touches_secrets(patch: dict) -> bool:
    return bool(patch.get("api_key"))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def update(patch: dict, *, can_edit: bool = False,
           can_edit_secrets: bool = False) -> dict[str, Any]:
    """按传入字段改写**报价的唯一配置**。未传的字段保持不变。"""
    if not can_edit:
        raise PermissionError("修改模型设置需要工艺经理或管理员权限")
    if touches_secrets(patch) and not can_edit_secrets:
        raise PermissionError("修改 API Key 需要系统管理员权限")

    changes: dict[str, Any] = {}
    key_written = ""
    if patch.get("model"):
        model = str(patch["model"]).strip()
        provider_of(model)                      # 选不出来的模型直接拒绝，不静默改
        changes["model"] = model
    if "temperature" in patch:
        value = patch["temperature"]
        changes["temperature"] = None if value in (None, "") else _clamp(
            float(value), *PARAM_RANGES["temperature"])
    if "max_tokens" in patch:
        value = patch["max_tokens"]
        changes["max_tokens"] = None if value in (None, "") else int(
            _clamp(float(value), *PARAM_RANGES["max_tokens"]))
    if "thinking" in patch:
        changes["thinking"] = bool(patch["thinking"])
    if "thinking_budget" in patch:
        value = patch["thinking_budget"]
        changes["thinking_budget"] = None if value in (None, "") else int(
            _clamp(float(value), 0, 64000))
    key_written = str(patch.get("api_key") or "").strip()
    if key_written:
        provider = str(patch.get("api_key_provider") or "").strip()
        if provider not in PROVIDERS:
            raise ValueError("保存 API Key 时必须指明提供商")
        keys = _saved_keys()
        keys[provider] = key_written
        changes["api_keys"] = keys

    if changes:
        with _lock:
            merged = cpq_shared_settings.merge_into(_quote_settings(), changes)
            cpq_shared_settings.save(merged)
    # 报价、配置、规则三个助手跑在另一个进程里，改完要转告一声，否则它们的内存态
    # 还是旧模型 / 旧 Key。失败不影响本地已保存的结果（下一次读盘仍是新值）。
    if changes:
        _notify_quote_agents(changes)
    # 技术工艺与报价是两个进程：报价侧保存后本进程不会收到回调，所以下一次对话
    # 进到 sync_live_agents() 时再比对一次当前路由，变了就重建 client。
    sync_live_agents()
    return snapshot(can_edit=can_edit, can_edit_secrets=can_edit_secrets)


def _notify_quote_agents(changes: dict[str, Any]) -> None:
    """把改动同步给报价侧的 /api/settings（一体化服务下由它广播给另外两个助手）。"""
    base = os.getenv("CPQ_AUTH_BASE_URL", "").strip().rstrip("/")
    if not base:
        return
    payload = {k: v for k, v in changes.items() if k != "api_keys"}
    if payload:
        _post_quote_settings(base, payload)
    # 报价侧一次只接受一个 provider 的 Key；逐个提交，值不回显、不记录。
    for provider, secret in (changes.get("api_keys") or {}).items():
        if secret:
            _post_quote_settings(base, {"api_key": secret, "provider": provider})


def _post_quote_settings(base: str, body: dict[str, Any]) -> None:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    # 报价侧的 /agents/* 现在也要验票：服务间调用没有用户票据，改用一体化服务在拉起本
    # 进程时注入的内部令牌（cpq_suite_server.CPQ_INTERNAL_TOKEN）。缺了它不是"少一个头"
    # 而是"技术工艺改了模型，报价侧不生效"的静默事故，所以下面要留下可诊断的告警。
    internal = (os.environ.get("CPQ_INTERNAL_TOKEN") or "").strip()
    if internal:
        headers["X-Internal-Token"] = internal
    request = urllib.request.Request(
        f"{base}/agents/quote/api/settings", data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:   # noqa: S310 - 固定内网地址
            response.read()
    except Exception as exc:                                        # noqa: BLE001 - 任何失败都要说清楚
        reason = str(exc) if internal else "未配置 CPQ_INTERNAL_TOKEN（独立运行？）"
        print(f"[tech-app] 警告：模型/密钥设置未能同步到报价侧（{reason}）；"
              f"技术工艺改了模型但报价侧不会生效。", file=sys.stderr)


def _route_matches_applied(route: dict[str, Any]) -> bool:
    """当前路由是否与上次推给 Agent 会话的完全一致（含 Key 与发起账号，只比不打印）。

    "account" 必须一起比：会话（按项目）是共享的，A 说完 B 说时模型与 Key 都可能
    换人，不把账号算进去就发现不了，会拿上一个人的模型与额度继续跑。
    """
    previous = _applied_route
    if previous is None:
        return False
    for field in ("model", "provider", "base_url", "api_key", "native", "account"):
        if str(previous.get(field) or "") != str(route.get(field) or ""):
            return False
    return True


def sync_live_agents(user: str = "") -> None:
    """把**发起账号的**生效路由推给所有已建的 Agent 会话；路由变了就连 client 一起重建。

    这是「报价保存设置后技术工艺不需要重启」的落点：技术工艺每次对话前调用一次，
    报价侧在另一个进程里改的模型 / 网关 / Key、或者换了说话的人，都会在这次比对里
    被发现。路由不可读（没配模型、配置损坏、账号缺 Key）时安全退出，不打断对话本身
    —— 真正的报错留给该报错的那一步。
    """
    global _applied_route
    target = _target_user(user)
    try:
        route = resolve(vision=False, user=target)
    except Exception:                                   # pragma: no cover - 配置不可读
        return
    route = {**route, "account": target}
    rebuild = not _route_matches_applied(route)
    try:
        from . import oc_agent

        oc_agent.apply_settings(agent_params(user=target), rebuild_client=rebuild)
    except Exception:                                   # pragma: no cover - 依赖环境
        return
    _applied_route = {key: str(route.get(key) or "") for key in
                      ("model", "provider", "base_url", "api_key", "native", "account")}


def changed_fields(patch: dict) -> list[str]:
    """审计用：只留字段名，密钥的值永远不进日志。"""
    return sorted(key for key in patch if key != "api_key")


# 旧名字保留：有些调用方仍在用「本地模型」这一叫法（报价侧叫 local）。
LOCAL_MODEL_ALIAS = CPQ_LOCAL_PROVIDER

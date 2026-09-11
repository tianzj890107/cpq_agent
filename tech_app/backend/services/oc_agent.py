"""2.1 图纸解析的 Agent —— 直接使用 open-claude 的 Conversation 与工具循环。

不复制、不改写 open-claude：本模块只做三件事
  1. 把仓库内的 open-claude 加进 sys.path，实例化它的 Conversation（每个项目一个会话）；
  2. 在它的工具表上追加本平台的工具（项目状态、零件清单、待澄清、请求解析），
     并在 open_claude.repl 的 execute_tool 调用点前置一层分派；
  3. 把 Conversation 的流式输出转成 SSE 事件，喂给前端对话框。

关于工具分派的实现方式：open-claude 的 Conversation._execute_pending_tools 里，
非 Agent / 非 MCP 的工具统一走模块级的 execute_tool()。这里用一个包装函数替换
open_claude.repl.execute_tool，命中平台工具就自己处理，否则原样转交。
这样权限检查、PreToolUse/PostToolUse 钩子、流式与压缩逻辑全部保持 open-claude 原生行为，
代价是一处受控的模块级替换 —— 相比 fork 一份 repl.py，这是更小的耦合面。

文件系统只读：与 open-claude 自带的 web 桥一致，Write / Edit / Bash 既从模型可见的
工具表里摘掉，也在执行层由 OC_READONLY_FS 拦住（子代理同样受限）。CLI 不受影响。

**唯一的例外是 `UpdatePartParameters`**：它不碰文件系统，只按白名单改 IR 里的零件
参数，逻辑与老的 workbench-chat 共用 `services.part_edit`，每次改写都留版本快照和
审计。文件系统只读这条约束没有放宽。

工作目录被限定在该项目的数据目录（DATA_DIR/<project_id>），因此 Read/Glob/Grep
看到的是这份图纸、附件与生成的几何文件，而不是整个代码仓库。
"""
from __future__ import annotations

import contextvars
import ipaddress
import json
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from urllib.parse import urlsplit

from ..config import DATA_DIR, ROOT_DIR
from ..storage import store
from . import llm_settings

OPEN_CLAUDE_DIR = Path(os.getenv("OPEN_CLAUDE_DIR", ROOT_DIR / "open-claude"))

# 与 open-claude web 桥一致：网页会话不得改动本地文件或执行命令。
READONLY_DISABLED_TOOLS = ("Write", "Edit", "Bash")

_lock = threading.RLock()
_sessions: dict[str, "ProjectAgent"] = {}
_patched = False


class AgentUnavailable(RuntimeError):
    """open-claude 不可用（未安装、缺密钥、导入失败）时抛出，由接口层转成可读提示。"""


# 发起本轮对话的用户。Agent 跑在 worker 线程里，拿不到请求上下文，但改零件参数
# 必须记「是谁改的」—— 审计里写 system 等于没记。接口层在 stream_sse 时注入。
_ACTOR: contextvars.ContextVar[str] = contextvars.ContextVar("oc_agent_actor",
                                                            default="system")

# 发起本轮对话的用户令牌（CPQ SSO）。发送财务要代表用户去调一体化服务，而该请求
# 必须带用户自己的 Bearer 令牌 —— 与 _ACTOR 同理：worker 线程拿不到请求上下文，
# 接口层在 stream_sse 时注入。令牌只用于出站调用，绝不进日志、工具结果或异常文案。
_TOKEN: contextvars.ContextVar[str] = contextvars.ContextVar("oc_agent_token",
                                                             default="")


def current_actor() -> str:
    return _ACTOR.get() or "system"


def current_token() -> str:
    return _TOKEN.get() or ""


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# --------------------------------------------------------------------------- #
# open-claude 装载
# --------------------------------------------------------------------------- #
def _ensure_path() -> None:
    if not OPEN_CLAUDE_DIR.is_dir():
        raise AgentUnavailable(f"未找到 open-claude 目录：{OPEN_CLAUDE_DIR}")
    path = str(OPEN_CLAUDE_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)


def _import_open_claude():
    _ensure_path()
    try:
        from open_claude import repl as oc_repl                     # noqa: WPS433
        from open_claude.api import stream_message                  # noqa: WPS433
        from open_claude.config import AVAILABLE_MODELS             # noqa: WPS433
        from open_claude.profile import load_profile                # noqa: WPS433
        from open_claude.sessions import SessionStore               # noqa: WPS433
        from open_claude.skills.registry import get_registry        # noqa: WPS433
    except Exception as exc:                                        # pragma: no cover - 环境相关
        raise AgentUnavailable(f"open-claude 导入失败：{exc}") from exc
    return {
        "repl": oc_repl, "stream_message": stream_message,
        "AVAILABLE_MODELS": AVAILABLE_MODELS, "load_profile": load_profile,
        "SessionStore": SessionStore, "get_registry": get_registry,
    }


# --------------------------------------------------------------------------- #
# 模型路由：Agent 用哪个模型由「模型设置」决定，不是由某一家厂商的 Key 决定
# --------------------------------------------------------------------------- #
# 非 Anthropic 的 provider（qwen / openai / deepseek / cpq_local 本地网关）都走
# OpenAI 兼容协议，由 open-claude 的 openai_compat 客户端发出；只有 anthropic
# 走原生 SDK。provider 表本身来自 llm_settings.PROVIDERS —— 这里不复制第二份配置。
OPENAI_COMPATIBLE_PROVIDERS = ("qwen", "openai", "deepseek", "cpq_local")


def agent_route() -> dict[str, Any]:
    """Agent 当前该用的模型路由（model/provider/base_url/native/api_key）。

    唯一事实源是 llm_settings：默认语言模型是 Qwen，所以 Anthropic 的 Key
    从来都不该是前提。
    """
    return llm_settings.resolve(vision=False)


def _provider_env_names(provider: str) -> tuple[str, ...]:
    """某个 provider 的 Key 环境变量名，取自 llm_settings 的 provider 表。"""
    spec = llm_settings.PROVIDERS.get(provider) or {}
    return tuple(spec.get("env") or ())


def _route_host(base_url: str) -> str:
    """从网关地址里取主机名；解析不出来时返回空串，绝不抛异常。"""
    try:
        return (urlsplit(str(base_url or "")).hostname or "").strip()
    except Exception:                                            # pragma: no cover - 异常输入兜底
        return ""


def _is_loopback_host(host: str) -> bool:
    """主机名是否指向本机（127.0.0.0/8、::1、localhost）。"""
    name = (host or "").strip().strip("[]").lower()
    if not name:
        return False
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _exclude_loopback_from_proxy(base_url: str) -> None:
    """把本机网关补进 NO_PROXY / no_proxy，避免被进程级代理拦走。

    OpenAI SDK 底层的 httpx 默认 trust_env=True，建 client 时会读 HTTP_PROXY /
    HTTPS_PROXY / ALL_PROXY。部署环境设置了企业代理、而 NO_PROXY 没有排除本机时，
    指向 127.0.0.1 的 CPQ 本地网关请求会被送进代理，本地网关收不到请求（表现为
    SDK 报 502）。这里只在主机确实是本机时追加缺失条目：既有的运维配置
    （内网域名、网段）原样保留，重复调用幂等，公网域名与 "*" 一律不写入。
    """
    host = _route_host(base_url)
    if not _is_loopback_host(host):
        return
    for env_name in ("NO_PROXY", "no_proxy"):
        entries = [part.strip() for part in os.environ.get(env_name, "").split(",")]
        kept = [part for part in entries if part]
        if any(part.strip("[]").lower() == host.strip("[]").lower() for part in kept):
            continue
        os.environ[env_name] = ",".join([*kept, host])


def sync_route_environment(route: dict[str, Any]) -> None:
    """把当前路由（模型 / provider / Key / 网关）同步给 open-claude。

    open-claude 自己按模型推断 provider，并从该 provider 的 Key 环境变量与
    `<PROVIDER>_BASE_URL` 读连接信息，所以这里只做搬运：

      · CLAUDE_MODEL          让 open-claude 选中同一个模型，而不是回到默认 Claude；
      · provider 的 Key       官方 provider 各写自己的环境变量；
      · 自定义网关            CPQ 注入的 cpq_local 这类 open-claude 不认识的 provider
                              在运行时按 llm_settings 的表注册（不改包内文件）。

    路由变化后必须重建 client 才能生效，见 ProjectAgent.apply/_rebuild_client。
    任何情况下都不打印、不返回 Key。
    """
    provider = str(route["provider"] or "").strip()
    api_key = str(route["api_key"] or "").strip()
    model = str(route.get("model") or "").strip()
    base_url = str(route["base_url"] or "").strip()
    # 必须早于任何 client 构造：httpx 只在建 client 时读一次代理环境变量。
    _exclude_loopback_from_proxy(base_url)
    if model:
        os.environ["CLAUDE_MODEL"] = model
    if provider:
        for env_name in _provider_env_names(provider):
            if api_key:
                os.environ[env_name] = api_key
        if base_url:
            # open-claude 的既有约定：<PROVIDER>_BASE_URL 覆盖该 provider 的网关。
            # 只在部署未显式配置时兜底写入：运维用 QWEN_BASE_URL 指了业务空间专属
            # 域名（见 .env.example）时不能被默认网关覆盖回去。
            os.environ.setdefault(f"{provider.upper()}_BASE_URL", base_url)
    _register_runtime_provider(route)


def _register_runtime_provider(route: dict[str, Any]) -> None:
    """把 open-claude 不认识的 provider（CPQ 本地网关）注册进它的 provider 表。

    官方 provider 已经内置，只补环境变量即可；只有 base_url 由外部注入的
    cpq_local 需要登记 provider 与「模型 → provider」映射，否则模型名会被前缀
    推断成别的厂商，请求就发错了地方。
    """
    provider = str(route["provider"] or "").strip()
    model = str(route.get("model") or "").strip()
    if not provider or not model:
        return
    try:
        _ensure_path()
        from open_claude import config as oc_config               # noqa: WPS433
    except Exception:                                            # pragma: no cover - 环境相关
        return
    providers = getattr(oc_config, "PROVIDERS", None)
    if isinstance(providers, dict) and provider not in providers:
        # 只有 OpenAI 兼容的自定义网关才能按这套表接进来；原生 SDK 的 provider
        # 不在这里伪造，否则请求会被发到不存在的协议上。
        if provider not in OPENAI_COMPATIBLE_PROVIDERS:
            return
        providers[provider] = {
            "label": str(route.get("provider_label") or provider),
            "env": list(_provider_env_names(provider)),
            "base_url": str(route["base_url"] or "").strip() or None,
        }
    mapping = getattr(oc_config, "_MODEL_PROVIDERS", None)
    if isinstance(mapping, dict):
        mapping[model] = provider


def available() -> tuple[bool, str]:
    """探测 Agent 是否可用：按**当前所选语言模型**的 provider 与 Key 判断。

    默认语言模型是 Qwen，因此只配了 Qwen Key 时 Agent 必须可用；只有真正选中
    Anthropic 模型时，缺 Anthropic Key 才是原因。错误信息只报当前 provider，
    不含任何 Key 内容。
    """
    try:
        route = llm_settings.resolve(vision=False)
    except Exception as exc:                                     # pragma: no cover - 配置损坏
        return (False, f"读取模型设置失败：{exc}")
    model = str(route.get("model") or "").strip()
    if not model:
        return (False, "未配置语言模型，Agent 无法启动")
    if not str(route["api_key"] or "").strip():
        label = str(route.get("provider_label") or route["provider"] or "当前模型")
        gap = " " if label.isascii() else ""
        return (False, f"未配置{gap}{label} API Key，Agent 无法启动")
    try:
        _import_open_claude()
    except AgentUnavailable as exc:
        return (False, str(exc))
    sync_route_environment(route)
    return (True, "")


# --------------------------------------------------------------------------- #
# 平台工具
# --------------------------------------------------------------------------- #
PLATFORM_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "GetProjectState",
        "description": "读取当前工艺评估项目的状态：设备名称、设计意图、是否已解析、"
                       "零件与标准件数量、平均置信度、已上传的图纸与技术文档清单。"
                       "回答任何与本项目有关的问题前都应先调用它。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ListParts",
        "description": "列出图纸解析得到的零件清单（编号、名称、材料、数量、置信度、所属总成）。"
                       "尚未解析时返回空列表。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "GetPartDetail",
        "description": "读取单个零件的完整信息：特征列表与尺寸、材料、公差、溯源说明、拆解建议。",
        "input_schema": {
            "type": "object",
            "properties": {"part_id": {"type": "string", "description": "零件编号，如 P-001"}},
            "required": ["part_id"],
        },
    },
    {
        "name": "GetOpenQuestions",
        "description": "读取本次解析的待澄清问题（缺尺寸/公差/材料等需人工确认的项）。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "LookupComponentLibrary",
        "description": "在企业零部件库里检索某个零件有没有现成件：命中的零部件编码与名称、"
                       "匹配度、可复用/可改制/未匹配的判定、用于比对的查询条件（尺寸、材料），"
                       "以及全部候选件与它们的差异说明。"
                       "回答「这个零件库里有没有」「能不能复用现成件」「要不要新开模」时先调用它；"
                       "零部件编码必须来自这里，不得自行编造。不传 part_id 则返回整份检索汇总。",
        "input_schema": {
            "type": "object",
            "properties": {"part_id": {"type": "string",
                                       "description": "零件编号，如 P-001；留空则返回全部零件的汇总"}},
            "required": [],
        },
    },
    {
        "name": "LookupProcessLibrary",
        "description": "在企业工艺库里检索某个零件能用哪条工艺路线：命中的路线模板及其工序序列"
                       "（工序编号、标准准备工时、单件工时模型、默认设备类）、路线未覆盖但特征"
                       "需要的补充工序、以及库内根本没有对应工序的空白项。"
                       "回答「这个零件怎么做」「要几道工序」「有没有现成路线」时先调用它，"
                       "答案里的工序编号必须来自这里，不得自行编造。",
        "input_schema": {
            "type": "object",
            "properties": {"part_id": {"type": "string", "description": "零件编号，如 P-001"}},
            "required": ["part_id"],
        },
    },
    {
        "name": "LookupCostLibrary",
        "description": "在企业成本库里检索某个零件的计价依据：物料现价（含 price_id、价格类型、"
                       "生效日期）、人工/折旧/能耗/制造费用等费率、良率与毛利等计价系数，"
                       "以及库内缺失需要询价或补录的项。"
                       "回答「这个零件多少钱」「材料什么价」「费率多少」时先调用它；"
                       "库里有的价格必须原值引用，库里没有的要明确说是估算。",
        "input_schema": {
            "type": "object",
            "properties": {
                "part_id": {"type": "string", "description": "零件编号，如 P-001"},
                "quantity": {"type": "integer", "description": "核算批量，默认 1"},
            },
            "required": ["part_id"],
        },
    },
    {
        "name": "UpdatePartParameters",
        "description": "修改某个零件的参数。**这是唯一会写业务数据的工具**，只在用户"
                       "明确要求「改成/设为/调整为」并给出了具体目标值时调用。\n"
                       "可改：name、quantity、material_spec，以及该零件**已有**特征的数值字段"
                       "（先用 GetPartDetail 确认特征序号与字段名）。\n"
                       "不可改：不能新增或删除特征、不能改特征 type、一次只能改一个零件。\n"
                       "用户只是问「该怎么改」、没给具体数值、或你需要靠常识补全尺寸时，"
                       "**不要调用本工具** —— 先把缺的信息问清楚。改写会留版本快照与审计，"
                       "但错误的改写仍然要人工回滚，代价不小。\n"
                       "改了特征尺寸后返回 requires_regeneration=true，平台会在界面上重生成"
                       "该零件的 3D 与工程图；如实说明是平台在做，不要自称是你生成的。",
        "input_schema": {
            "type": "object",
            "properties": {
                "part_id": {"type": "string", "description": "零件编号，如 P-001"},
                "name": {"type": "string", "description": "新的零件名称；不改就不要传"},
                "quantity": {"type": "integer", "description": "新的数量；不改就不要传"},
                "material_spec": {"type": "string",
                                  "description": "新的材料牌号，如 Q235 / 6061-T6；不改就不要传"},
                "feature_updates": {
                    "type": "array",
                    "description": "要改的特征数值，逐项给出",
                    "items": {
                        "type": "object",
                        "properties": {
                            "feature_index": {"type": "integer",
                                              "description": "特征序号，从 0 开始"},
                            "field": {"type": "string",
                                      "description": "字段名，如 thickness / diameter / length"},
                            "value": {"type": "number", "description": "新数值（mm）"},
                        },
                        "required": ["feature_index", "field", "value"],
                    },
                },
                "reason": {"type": "string", "description": "本次修改的依据，会写进版本说明"},
            },
            "required": ["part_id"],
        },
    },
    # ---- 2.2 组装与整合 ----------------------------------------------------
    # 这一页的对话框原来只是个本地输入框：说什么都只会被追加进「整合需求」，
    # 既问不到当前状态，也改不了工序和参数。下面四个工具把它接进真正的 Agent。
    {
        "name": "GetIntegrationState",
        "description": "取 2.2「组装与整合」的当前状态：四个环节各自跑没跑、整机参数条数与"
                       "报价必填参数的缺口、组装工序道数与总工时、整机成本四项拆解、"
                       "成品编码有没有生成、以及本步的整合需求原文。"
                       "回答任何与 2.2 有关的问题前**先调用它**，不要拿 2.1 的零件数据充数。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ListIntegrationParams",
        "description": "列出整机参数与报价成品参数（DA 字典）的逐项对照：字段编码、中文名、"
                       "当前值、单位、是否报价必填、有没有填。"
                       "用户问「还缺哪些参数」「标称电压是多少」时用它；"
                       "要改参数前也先用它确认字段编码。",
        "input_schema": {
            "type": "object",
            "properties": {"only_missing": {
                "type": "boolean", "description": "只列没有值的项，默认 false"}},
            "required": [],
        },
    },
    {
        "name": "UpdateIntegrationParams",
        "description": "修改 2.2 的整机参数值（**会写业务数据**）。按报价字典的字段编码给值，"
                       "如 rated_voltage / weight / product_model。\n"
                       "只在用户明确说「把 X 改成 Y」「填上 Z」并给出了具体值时调用；"
                       "用户只是问「该填多少」或你需要靠常识猜数值时**不要调用** —— 先问清楚。\n"
                       "成品编码（product_item_code）由平台「写入数据库」时生成，不要手工写。",
        "input_schema": {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "description": "要写入的参数，逐项给出",
                    "items": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "description": "字段编码，如 rated_voltage"},
                            "value": {"type": "string", "description": "参数值，范围与公差照写"},
                            "unit": {"type": "string", "description": "单位；与字典一致时可不给"},
                        },
                        "required": ["code", "value"],
                    },
                },
                "reason": {"type": "string", "description": "改动依据，会写进审计"},
            },
            "required": ["values"],
        },
    },
    {
        "name": "UpdateIntegrationProcess",
        "description": "修改 2.2 的**组装工序**（会写业务数据）：改某道工序的名称/类型/设备/工时、"
                       "新增一道、或删掉一道。用户说「把 20 工序的工时改成 8 分钟」"
                       "「在老化后面加一道外观终检」「删掉第 3 道」时调用。\n"
                       "先用 GetIntegrationState 看清现有工序号，再动手；一次可以改多道。",
        "input_schema": {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "array",
                    "description": "逐道工序的改动",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step_no": {"type": "integer", "description": "工序号，如 20"},
                            "action": {"type": "string",
                                       "description": "update（默认）/ add / delete"},
                            "name": {"type": "string", "description": "工序名称"},
                            "type": {"type": "string",
                                     "description": "工序类型英文枚举，组装用 assembly、检测用 inspection"},
                            "equipment": {"type": "string", "description": "设备类别"},
                            "duration_min": {"type": "number", "description": "单件工时（分钟/台）"},
                            "note": {"type": "string", "description": "备注"},
                        },
                        "required": ["step_no"],
                    },
                },
                "reason": {"type": "string", "description": "改动依据，会写进审计"},
            },
            "required": ["updates"],
        },
    },
    {
        "name": "RequestIntegrationStep",
        "description": "请求平台（重新）跑 2.2 的某个环节：params 参数推荐 / process 组装工艺 / "
                       "cost 成本测算。用户说「重新推荐参数」「重排组装工艺」「重算成本」时调用。\n"
                       "本工具只发出请求，真正的生成由平台流水线执行并在界面上回显进度 —— "
                       "调用后不要虚构参数、工序或金额，等平台写回结果再基于工具重新读取。",
        "input_schema": {
            "type": "object",
            "properties": {
                "step": {"type": "string", "description": "params / process / cost"},
                "reason": {"type": "string", "description": "触发的简短理由"},
            },
            "required": ["step"],
        },
    },
    {
        "name": "RequestParse",
        "description": "请求平台开始（或重新）解析当前图纸。用户说「开始解析」「重新解析」"
                       "「帮我拆解这张图」等意图时调用。"
                       "注意：本工具只发出请求，真正的解析由平台既有流水线执行并在界面上回显进度；"
                       "调用后不要虚构解析结果，等平台把结果写回后再基于 ListParts 回答。",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "触发解析的简短理由"}},
            "required": [],
        },
    },
    {
        "name": "GetRequirementDraft",
        "description": "读取当前项目 1.1「创建需求」的草稿：需求单号、标题、状态（draft / "
                       "pending_confirmation / …）、已填字段、附件角色与上一次 AI 解析摘要。"
                       "回答任何与需求单内容有关的问题前都应先调用它。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "UpdateRequirementFields",
        "description": "补充 1.1 需求单的**空白**字段。**这是会写业务数据的工具**，只在用户"
                       "明确要求「把…填上 / 补充…」并给出具体值时调用。\n"
                       "规则：只补空白字段，已有内容（人工或 AI 填写的）一律不覆盖；客户信用等级、"
                       "报价溯源等受限字段不允许修改；需求一旦进入确认流程就不能再改。\n"
                       "字段 key 必须来自 GetRequirementDraft 的字段清单；改写会留审计，"
                       "写完后刷新页面即可看到新值。用户只是问「该怎么填」时不要调用本工具。",
        "input_schema": {
            "type": "object",
            "properties": {
                "fields": {
                    "type": "object",
                    "description": "要补充的字段：{字段 key: 值}；只填当前为空的字段",
                    "additionalProperties": {"type": "string"},
                },
                "reason": {"type": "string", "description": "补充依据，会写进审计"},
            },
            "required": ["fields"],
        },
    },
    {
        "name": "AttachRequirementFiles",
        "description": "登记 / 读取 1.1 需求单的附件清单（技术规格说明书、3D 模型、BOM 等）。"
                       "附件正文只能由页面上传接口保存，本工具**不上传文件**；传入已有的文件名"
                       "与角色时，只把该附件关联到对应区域并留审计。",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "已在项目附件里的文件名；留空则只读取清单"},
                "role": {"type": "string",
                         "description": "附件角色，如 technical_spec / model_3d / bom_assembly"},
            },
            "required": [],
        },
    },
    {
        "name": "ExtractRequirement",
        "description": "请求平台对已上传的技术文档执行一键解析，自动补齐 1.1 需求单字段并生成"
                       "AI 推荐默认值。用户说「解析需求」「读一下技术资料」「自动填需求单」时调用。\n"
                       "注意：本工具只向界面发出请求，真正的提取由平台既有流水线执行并在界面回显"
                       "进度；调用后不要虚构字段或推荐值，等结果写回后再用 GetRequirementAiFill 回答。",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "触发解析的简短理由"}},
            "required": [],
        },
    },
    {
        "name": "GetRequirementTask",
        "description": "读取需求解析任务的真实状态与进度日志。传入 task_id 查单个任务；"
                       "留空返回本项目最近的需求相关任务。用户问「解析到哪了」「好了没有」时调用。",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string", "description": "任务 id；留空列出最近的解析任务"}},
            "required": [],
        },
    },
    {
        "name": "GetRequirementAiFill",
        "description": "读取 1.1 上一次 AI 解析的结论：AI 从技术资料带入的字段、AI 推荐的"
                       "默认值及置信度、仍缺失的必填项与待澄清问题。回答「AI 填了什么」「还缺什么」"
                       "「推荐值靠不靠谱」时调用；没有解析结果时如实说明。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "SubmitRequirementConfirmation",
        "description": "把 1.1 需求草稿提交到 1.2 待确认。用户明确说「提交需求」「提交确认」时"
                       "调用；只允许从 draft / rejected 进入 pending_confirmation。\n"
                       "提交后仍由人工在 1.2 确认或退回，本工具不代替审批；权限不足或状态不对时"
                       "如实返回失败原因，不要谎报已提交。",
        "input_schema": {
            "type": "object",
            "properties": {"comment": {"type": "string", "description": "提交说明，会写进需求单留痕"}},
            "required": [],
        },
    },
    {
        "name": "GetRequirementPrecheck",
        "description": "读取 1.2 确认页的**确定性**完整性检查结果：每个区块的 ok / need_info 与"
                       "整体结论（generated_note）。回答「这单能不能确认」「还缺什么」时先调用它；"
                       "结果由既有规则算出，不经模型。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "GetRequirementClarifications",
        "description": "读取 1.2 待澄清问题：把同一份确定性预检里 status == need_info 的条目"
                       "投影成问题清单。回答「还有哪些要补充」时调用；不要自己编造问题。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "SaveRequirementConfirmationNote",
        "description": "把确认意见**带进 1.2 看板**的提交意见框（#confirmationNote），与 RequestParse"
                       " 同模式：只向界面发请求，本工具**不落盘**。真正写入需求单由用户点击通过 / "
                       "驳回时携带的 comment 完成。用户说「把意见填上」「起草确认意见」时调用。",
        "input_schema": {
            "type": "object",
            "properties": {"note": {"type": "string", "description": "要带入确认意见框的文本"}},
            "required": ["note"],
        },
    },
    {
        "name": "ConfirmRequirement",
        "description": "通过 1.2 需求确认（pending_confirmation → pending_review）。"
                       "**必须由用户明确确认**：只有用户在本轮明确说「确认通过」后，才能带 "
                       "confirmed=true 调用；否则先返回回执征求意见，绝不能代替人工审批。"
                       "需要工艺技术经理或管理员权限，状态不符会如实返回失败原因。",
        "input_schema": {
            "type": "object",
            "properties": {
                "confirmed": {"type": "boolean",
                              "description": "用户是否已明确确认通过；必须是 true 才执行流转"},
                "comment": {"type": "string", "description": "确认意见，会写进需求单留痕"},
            },
            "required": ["confirmed"],
        },
    },
    {
        "name": "ReturnRequirementToDraft",
        "description": "把 1.2 需求退回草稿（pending_confirmation → draft），供创建人补充后重新提交。"
                       "**必须由用户明确确认**：只有用户明确说「退回 / 驳回」后，才能带 "
                       "confirmed=true 调用；否则先返回回执征求意见，不得静默退回。",
        "input_schema": {
            "type": "object",
            "properties": {
                "confirmed": {"type": "boolean",
                              "description": "用户是否已明确确认退回；必须是 true 才执行流转"},
                "comment": {"type": "string", "description": "退回原因，会写进需求单留痕"},
            },
            "required": ["confirmed"],
        },
    },
    {
        "name": "GetRequirementReviewMaterials",
        "description": "读取 1.3 审核材料：需求单关键字段、原始图纸与附件清单、确认信息、"
                       "确定性预检与历史留痕。回答「审核要看哪些材料」时调用；只读取，不改状态。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "GetRequirementReviewSummary",
        "description": "生成 1.3 审核摘要：由同一份确定性预检与关键字段汇总出 summary / items / "
                       "need_info / decision_options，**不调用模型、不编造结论**。用户问「这份需求能不能"
                       "过审」「审核结论建议」时调用。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "SaveRequirementReviewNote",
        "description": "把审核意见与审核结果**带进 1.3 看板**（选中审核结果单选 + 写入 #reviewText），"
                       "与 RequestParse 同模式：只向界面发请求，本工具**不落盘**。真正写入需求单由"
                       "用户点击提交审核时完成。",
        "input_schema": {
            "type": "object",
            "properties": {
                "decision": {"type": "string", "enum": ["approve", "reject", ""],
                             "description": "审核结果：approve 通过 / reject 退回 / 留空只带入意见"},
                "note": {"type": "string", "description": "要带入审核说明的文本"},
            },
            "required": [],
        },
    },
    {
        "name": "ApproveRequirementReview",
        "description": "审核通过 1.3 需求（pending_review → approved）。**必须由用户明确确认**，"
                       "且需工艺技术总监或管理员权限：只有用户明确说「审核通过」后，才能带 "
                       "confirmed=true 调用；否则先返回回执征求意见，绝不代替人工审批。",
        "input_schema": {
            "type": "object",
            "properties": {
                "confirmed": {"type": "boolean",
                              "description": "用户是否已明确确认审核通过；必须是 true 才执行流转"},
                "comment": {"type": "string", "description": "审核意见，会写进需求单留痕"},
            },
            "required": ["confirmed"],
        },
    },
    {
        "name": "RejectRequirementReview",
        "description": "审核退回 1.3 需求（pending_review → rejected）。**必须由用户明确确认**，"
                       "且需工艺技术总监或管理员权限：只有用户明确说「退回 / 驳回」后，才能带 "
                       "confirmed=true 调用；否则先返回回执征求意见，不得静默退回。",
        "input_schema": {
            "type": "object",
            "properties": {
                "confirmed": {"type": "boolean",
                              "description": "用户是否已明确确认审核退回；必须是 true 才执行流转"},
                "comment": {"type": "string", "description": "退回原因，会写进需求单留痕"},
            },
            "required": ["confirmed"],
        },
    },
    {
        "name": "UploadIntegrationDrawing",
        "description": "请用户上传 2.2 整合图纸（装配图 / 爆炸图 / 接线图）。与 RequestParse 同模式："
                       "本工具**只发请求、不接收二进制、不落盘**——Agent 无法替用户上传文件，"
                       "请告诉用户在右侧看板的「整合图纸」页签上传。",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "为什么要补图纸"},
            },
            "required": [],
        },
    },
    {
        "name": "ConfirmIntegrationParams",
        "description": "确认 2.2「参数推荐」这一环节（整机参数、连接关系与 BOM 已由人核对）。"
                       "复用看板同一个 POST /integration/params/confirm 实现，只改状态、不重算；"
                       "执行前应向用户复述待确认要点，得到同意后再调用。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ConfirmIntegrationProcess",
        "description": "确认 2.2「组装工艺」。这是把任务推给财务的闸门：确认之后才允许发送财务。"
                       "复用看板同一个 POST /integration/process/confirm 实现，只改状态、不重算。",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "SendIntegrationToFinance",
        "description": "把 2.2 定稿的工艺与整机参数发送给财务做成本测算（对外动作）。"
                       "**必须由用户明确确认**：只有用户明确说「发送 / 交给财务」后，才能带 "
                       "confirmed=true 调用；否则先返回回执征求意见，绝不代替用户触发外呼。"
                       "既有闸门不变：参数推荐与组装工艺都必须已确认才能发送。",
        "input_schema": {
            "type": "object",
            "properties": {
                "confirmed": {"type": "boolean",
                              "description": "用户是否已明确确认发送财务；必须是 true 才执行外呼"},
                "product_name": {"type": "string", "description": "成品 / 任务名称，留空取整机名称"},
                "note": {"type": "string", "description": "给财务的说明"},
            },
            "required": ["confirmed"],
        },
    },
]
PLATFORM_TOOL_NAMES = {schema["name"] for schema in PLATFORM_TOOL_SCHEMAS}

# 前端据此把工具调用翻译成界面动作（开始解析、改完刷新 IR…）。
# refresh-ir 由前端**在本轮结束时**执行 —— 这个事件是在工具跑之前发出的，
# 当场刷新会读到改写前的旧值。
UI_ACTION_TOOLS = {
    "RequestParse": "parse", "UpdatePartParameters": "refresh-ir",
    # 2.2：改完要刷新中间面板；请求跑环节由前端去点那一步（同一条流水线，不另开一路）。
    "UpdateIntegrationParams": "refresh-integration",
    "UpdateIntegrationProcess": "refresh-integration",
    "RequestIntegrationStep": "integration-step",
    # 2.2：整合图纸上传请用户在看板完成；参数 / 工序确认后刷新看板确认态。
    # 发送财务是对外动作，只映射成「刷新看板」，绝不映射成 tool_use 阶段就自动执行的动作。
    "UploadIntegrationDrawing": "open-integration-drawings",
    "ConfirmIntegrationParams": "refresh-integration",
    "ConfirmIntegrationProcess": "refresh-integration",
    "SendIntegrationToFinance": "refresh-integration",
    # 1.1：一键解析与字段补充都由右侧看板执行（Agent 只发请求），补完刷新 1.1 表单。
    "ExtractRequirement": "extract-requirement",
    "UpdateRequirementFields": "refresh-requirement",
    "AttachRequirementFiles": "refresh-requirement",
    # 1.2 确认需求：确认意见带入看板；通过 / 退回有显式确认门，绝不能映射成
    # 会在 tool_use 阶段就自动执行的动作（否则父壳会在工具跑之前绕过确认门直接审批）。
    "SaveRequirementConfirmationNote": "fill-confirmation-note",
    "ConfirmRequirement": "refresh-requirement",
    "ReturnRequirementToDraft": "refresh-requirement",
    # 1.3 审核需求：审核意见带入看板；通过 / 退回同样只刷新，不自动审批。
    "SaveRequirementReviewNote": "fill-review-note",
    "ApproveRequirementReview": "refresh-requirement",
    "RejectRequirementReview": "refresh-requirement",
}


def _project_of(cwd: str) -> str:
    """工作目录就是项目数据目录，因此项目号可由 cwd 反推。"""
    return Path(cwd).name


def _ir_of(project_id: str) -> Optional[dict]:
    return store.load_ir(project_id)


def _run_platform_tool(name: str, params: dict, cwd: str) -> str:
    project_id = _project_of(cwd)
    if name == "GetProjectState":
        return json.dumps(_project_state(project_id), ensure_ascii=False, indent=2)
    if name == "ListParts":
        return json.dumps(_parts(project_id), ensure_ascii=False, indent=2)
    if name == "GetPartDetail":
        return json.dumps(_part_detail(project_id, str(params.get("part_id") or "")),
                          ensure_ascii=False, indent=2)
    if name == "GetOpenQuestions":
        return json.dumps(_open_questions(project_id), ensure_ascii=False, indent=2)
    if name == "LookupComponentLibrary":
        return json.dumps(_component_library(project_id, str(params.get("part_id") or "")),
                          ensure_ascii=False, indent=2)
    if name == "LookupProcessLibrary":
        return json.dumps(_process_library(project_id, str(params.get("part_id") or "")),
                          ensure_ascii=False, indent=2)
    if name == "LookupCostLibrary":
        return json.dumps(
            _cost_library(project_id, str(params.get("part_id") or ""),
                          int(params.get("quantity") or 1)),
            ensure_ascii=False, indent=2)
    if name == "UpdatePartParameters":
        return json.dumps(_update_part(project_id, params), ensure_ascii=False, indent=2)
    if name == "RequestParse":
        return json.dumps({
            "requested": True,
            "reason": str(params.get("reason") or "")[:200],
            "note": "已向界面发出解析请求。解析由平台流水线异步执行，"
                    "进度与结果会显示在对话与右侧工作区；请勿自行编造零件或尺寸。",
        }, ensure_ascii=False)
    if name == "GetIntegrationState":
        return json.dumps(_integration_state(project_id), ensure_ascii=False, indent=2)
    if name == "ListIntegrationParams":
        return json.dumps(_integration_params(project_id, bool(params.get("only_missing"))),
                          ensure_ascii=False, indent=2)
    if name == "UpdateIntegrationParams":
        return json.dumps(_update_integration_params(project_id, params),
                          ensure_ascii=False, indent=2)
    if name == "UpdateIntegrationProcess":
        return json.dumps(_update_integration_process(project_id, params),
                          ensure_ascii=False, indent=2)
    if name == "RequestIntegrationStep":
        step = str(params.get("step") or "").strip().lower()
        if step not in ("params", "process", "cost"):
            return json.dumps({"requested": False,
                               "error": "step 只能是 params / process / cost"},
                              ensure_ascii=False)
        return json.dumps({
            "requested": True, "step": step,
            "reason": str(params.get("reason") or "")[:200],
            "note": "已向界面发出请求。生成由平台流水线异步执行，进度与结果显示在对话与"
                    "中间面板；请勿自行编造参数、工序或金额。",
        }, ensure_ascii=False)
    if name == "UploadIntegrationDrawing":
        return json.dumps({
            "requested": True,
            "reason": str(params.get("reason") or "")[:200],
            "note": "已向界面发出上传请求。Agent 无法替用户上传文件，请在右侧看板的"
                    "「整合图纸」页签上传装配图 / 爆炸图 / 接线图；上传后看板会刷新图纸清单。",
        }, ensure_ascii=False)
    if name == "ConfirmIntegrationParams":
        return json.dumps(_confirm_integration_params(project_id),
                          ensure_ascii=False, indent=2)
    if name == "ConfirmIntegrationProcess":
        return json.dumps(_confirm_integration_process(project_id),
                          ensure_ascii=False, indent=2)
    if name == "SendIntegrationToFinance":
        # 显式确认门：没有用户明确的确认，绝不触发对外调用。
        if params.get("confirmed") is not True:
            return json.dumps({
                "requires_confirmation": True,
                "action": "send-to-finance",
                "status": _integration_status(project_id),
                "note": "发送财务会把任务推给财务经理做成本测算，属于对外动作，必须由用户明确确认。"
                        "请先复述整机参数与组装工艺的确认状态，得到同意后再带 confirmed=true 重新调用；"
                        "参数推荐与组装工艺都必须先确认，否则财务算出来的成本没有依据。",
            }, ensure_ascii=False)
        return json.dumps(
            _send_integration_to_finance(project_id, str(params.get("product_name") or ""),
                                         str(params.get("note") or "")),
            ensure_ascii=False, indent=2)
    if name == "GetRequirementDraft":
        return json.dumps(_requirement_draft(project_id), ensure_ascii=False, indent=2)
    if name == "UpdateRequirementFields":
        return json.dumps(_update_requirement_fields(project_id, params),
                          ensure_ascii=False, indent=2)
    if name == "AttachRequirementFiles":
        return json.dumps(_requirement_attachments(project_id, params),
                          ensure_ascii=False, indent=2)
    if name == "ExtractRequirement":
        return json.dumps({
            "requested": True,
            "reason": str(params.get("reason") or "")[:200],
            "note": "已向界面发出需求解析请求。提取由平台既有流水线（技术文档 → 1.1 草稿）"
                    "异步执行，进度与结果显示在对话与右侧 1.1 看板；请勿自行编造字段或推荐值。",
        }, ensure_ascii=False)
    if name == "GetRequirementTask":
        return json.dumps(_requirement_task(project_id, params), ensure_ascii=False, indent=2)
    if name == "GetRequirementAiFill":
        return json.dumps(_requirement_ai_fill(project_id), ensure_ascii=False, indent=2)
    if name == "SubmitRequirementConfirmation":
        return json.dumps(_submit_requirement_confirmation(project_id, params),
                          ensure_ascii=False, indent=2)
    if name == "GetRequirementPrecheck":
        return json.dumps(_requirement_precheck_data(project_id), ensure_ascii=False, indent=2)
    if name == "GetRequirementClarifications":
        return json.dumps(_requirement_clarifications(project_id), ensure_ascii=False, indent=2)
    if name == "SaveRequirementConfirmationNote":
        note = str(params.get("note") or "")[:2000]
        return json.dumps({
            "requested": True,
            "note": note,
            "note_target": "confirmationNote",
            "note_style": "append",
            "note_hint": "已请求看板把这段文字带进「提交意见」；本工具不落盘，"
                         "真正写入需求单由用户点击通过 / 驳回时完成。",
        }, ensure_ascii=False)
    if name == "ConfirmRequirement":
        # 显式确认门：没有用户明确的确认，绝不改状态。
        if params.get("confirmed") is not True:
            return json.dumps({
                "requires_confirmation": True,
                "action": "confirm",
                "status": _requirement_status(project_id),
                "note": "通过需求会推进到 1.3 审核，必须由用户明确确认。请先向用户复述待确认要点，"
                        "得到同意后再带 confirmed=true 重新调用；不要代替用户做决定。",
            }, ensure_ascii=False)
        return json.dumps(_confirm_requirement(project_id, str(params.get("comment") or "")),
                          ensure_ascii=False, indent=2)
    if name == "ReturnRequirementToDraft":
        if params.get("confirmed") is not True:
            return json.dumps({
                "requires_confirmation": True,
                "action": "return",
                "status": _requirement_status(project_id),
                "note": "退回需求会让创建人重新补充，必须由用户明确确认。请先征得用户同意，"
                        "再带 confirmed=true 重新调用；不得静默退回。",
            }, ensure_ascii=False)
        return json.dumps(_return_requirement_to_draft(project_id, str(params.get("comment") or "")),
                          ensure_ascii=False, indent=2)
    if name == "GetRequirementReviewMaterials":
        return json.dumps({"review_materials": _requirement_review_materials(project_id)},
                          ensure_ascii=False, indent=2)
    if name == "GetRequirementReviewSummary":
        return json.dumps(_requirement_review_summary(project_id), ensure_ascii=False, indent=2)
    if name == "SaveRequirementReviewNote":
        decision = str(params.get("decision") or "").strip().lower()
        if decision not in ("approve", "reject", ""):
            return json.dumps({"requested": False,
                               "error": "decision 只能是 approve / reject 或留空"},
                              ensure_ascii=False)
        return json.dumps({
            "requested": True,
            "decision": decision,
            "note": str(params.get("note") or "")[:2000],
            "note_target": "reviewText",
            "note_hint": "已请求看板选中审核结果并带入审核说明；本工具不落盘，"
                         "真正写入需求单由用户点击提交审核时完成。",
        }, ensure_ascii=False)
    if name == "ApproveRequirementReview":
        if params.get("confirmed") is not True:
            return json.dumps({
                "requires_confirmation": True,
                "action": "approve",
                "status": _requirement_status(project_id),
                "note": "审核通过会推进到图纸解析，必须由用户明确确认，且需工艺技术总监或管理员"
                        "权限。请先征得用户同意，再带 confirmed=true 重新调用；绝不代替人工审批。",
            }, ensure_ascii=False)
        return json.dumps(_approve_requirement_review(project_id, str(params.get("comment") or "")),
                          ensure_ascii=False, indent=2)
    if name == "RejectRequirementReview":
        if params.get("confirmed") is not True:
            return json.dumps({
                "requires_confirmation": True,
                "action": "reject",
                "status": _requirement_status(project_id),
                "note": "审核退回会把需求打回，必须由用户明确确认，且需工艺技术总监或管理员权限。"
                        "请先征得用户同意，再带 confirmed=true 重新调用；不得静默退回。",
            }, ensure_ascii=False)
        return json.dumps(_reject_requirement_review(project_id, str(params.get("comment") or "")),
                          ensure_ascii=False, indent=2)
    return f"未知的平台工具：{name}"


# --------------------------------------------------------------------------- #
# 1.1 创建需求
# --------------------------------------------------------------------------- #
def _requirement_doc(saved: dict):
    """按需构造需求单模型。

    pydantic 只在这里需要：把 import 留在函数内，oc_agent 才能在只装了轻量依赖的
    解释器里被导入（loopback / provider readiness 等动态测试依赖这一点）。
    """
    from ..models.workflow import RequirementDoc

    return RequirementDoc(**saved)


def _actor_user() -> dict:
    """Agent 工具的调用者。stream_sse 只带用户名，角色回查用户表补齐（与接口同权）。"""
    username = current_actor()
    record = store.get_user(username) or {}
    return {"username": username, "role": str(record.get("role") or "")}


def _requirement_industry(data: dict) -> str:
    from . import industry_templates

    industry = str((data or {}).get("industry_selection")
                   or (data or {}).get("industry") or "").strip().lower()
    if industry in (*industry_templates.INDUSTRIES, "flexible"):
        return industry
    return industry_templates.DEFAULT_INDUSTRY


def _requirement_allowed_fields(data: dict) -> set[str]:
    """Agent 可补充的字段白名单：直接复用提取服务的 1.1 表单字段表，不另立一份。"""
    from . import requirement_extract

    return set(requirement_extract._extractable_fields_for_industry(_requirement_industry(data)))


def _requirement_draft(project_id: str) -> dict:
    saved = store.load_requirement(project_id)
    if not saved:
        return {"exists": False,
                "note": "还没有 1.1 需求单草稿；请先在需求单页面保存草稿，或先上传技术资料。"}
    data = saved.get("data") or {}
    meta = store.load_meta(project_id) or {}
    extraction = data.get("document_extraction") or {}
    return {
        "exists": True,
        "project_id": project_id,
        "requirement_no": saved.get("requirement_no", ""),
        "title": saved.get("title", ""),
        "status": saved.get("status", ""),
        "editable": saved.get("status") in ("draft", "rejected"),
        "fields": {key: value for key, value in data.items() if not isinstance(value, (dict, list))},
        "file_roles": data.get("file_roles") or {},
        "source_filename": meta.get("source_filename", ""),
        "attachments": meta.get("attachments", []),
        "document_extraction": {
            "filled_fields": extraction.get("filled_fields") or [],
            "recommended_fields": extraction.get("recommended_fields") or [],
            "extracted_at": extraction.get("extracted_at") or "",
        },
    }


def _update_requirement_fields(project_id: str, params: dict) -> dict:
    fields = params.get("fields")
    if not isinstance(fields, dict) or not fields:
        return {"applied": False, "error": "fields 必须是非空对象：{字段 key: 要补充的值}"}
    saved = store.load_requirement(project_id)
    if not saved:
        return {"applied": False, "error": "还没有 1.1 需求单草稿，请先在需求单页面保存草稿。"}
    if saved.get("status") not in ("draft", "rejected"):
        return {"applied": False, "error": "需求已进入确认流程，不能自动修改；请先退回草稿。"}
    doc = _requirement_doc(saved)
    data = dict(doc.data or {})
    allowed = _requirement_allowed_fields(data)
    filled: dict[str, str] = {}
    skipped: dict[str, str] = {}
    for raw_key, raw_value in fields.items():
        key = str(raw_key or "").strip()
        text = str(raw_value if raw_value is not None else "").strip()
        if not key or not text:
            continue
        if key == "customer_credit":
            skipped[key] = "客户信用等级为受限字段，Agent 不得修改"
            continue
        if key == "title":
            if str(doc.title or "").strip():
                skipped[key] = "已有内容，不覆盖"
            else:
                doc.title = text
                data[key] = text
                filled[key] = text
            continue
        if key not in allowed:
            skipped[key] = "不在 1.1 可补字段白名单内"
            continue
        if str(data.get(key) or "").strip():
            skipped[key] = "已有内容，不覆盖"
            continue
        data[key] = text
        filled[key] = text
    if not filled:
        return {"applied": False, "filled_fields": [], "skipped_fields": skipped,
                "note": "没有可补充的空字段；人工已填内容与受限字段都没有改动。"}
    doc.data = data
    from . import requirement_service

    try:
        result = requirement_service.save_requirement_draft(
            project_id, doc, _actor_user(), current=saved)
    except requirement_service.RequirementSaveError as exc:
        return {"applied": False, "error": str(exc)}
    store.audit(project_id, "workflow:requirement_fields_filled_by_agent", {
        "by": current_actor(), "filled_fields": sorted(filled), "skipped_fields": sorted(skipped),
    })
    return {
        "applied": True,
        "requirement_no": result.get("requirement_no", ""),
        "filled_fields": sorted(filled),
        "skipped_fields": skipped,
        "note": "只补了空白字段；人工已填内容与客户信用等级等受限字段都没有被覆盖。"
                "刷新右侧 1.1 看板即可看到新值。",
    }


def _requirement_attachments(project_id: str, params: dict) -> dict:
    """附件正文由既有上传接口保存；本工具只登记 / 读取附件清单。"""
    names = [name for name, _ in store.load_attachments(project_id)]
    saved = store.load_requirement(project_id) or {}
    data = dict(saved.get("data") or {})
    meta = store.load_meta(project_id) or {}
    role = str(params.get("role") or "").strip()
    filename = str(params.get("filename") or "").strip()
    registered: list[str] = []
    if role and filename:
        if filename not in names:
            return {"registered": False, "attachments": names,
                    "error": f"附件 {filename} 尚未上传；请先在 1.1 页面上传，再由本工具登记。"}
        if not saved:
            return {"registered": False, "attachments": names,
                    "error": "还没有 1.1 需求单草稿；请先保存草稿再登记附件。"}
        file_roles = dict(data.get("file_roles") or {})
        current = list(file_roles.get(role) or [])
        if filename not in current:
            current.append(filename)
            file_roles[role] = current
            data["file_roles"] = file_roles
            doc = _requirement_doc(saved)
            doc.data = data
            from . import requirement_service

            try:
                requirement_service.save_requirement_draft(
                    project_id, doc, _actor_user(), current=saved)
            except requirement_service.RequirementSaveError as exc:
                return {"registered": False, "error": str(exc), "attachments": names}
            store.audit(project_id, "workflow:requirement_attachment_linked", {
                "by": current_actor(), "role": role, "file": filename,
            })
            registered = [filename]
    return {
        "project_id": project_id,
        "attachments": names,
        "source_filename": meta.get("source_filename", ""),
        "file_roles": data.get("file_roles") or {},
        "registered": registered,
        "note": "附件只能由既有上传接口保存；本工具只登记 / 读取附件清单，不上传、不改写文件正文。",
    }


def _task_brief(task: dict) -> dict:
    return {
        "task_id": task.get("task_id", ""),
        "kind": task.get("kind", ""),
        "status": task.get("status", ""),
        "progress": task.get("progress", ""),
        "progress_log": list(task.get("progress_log") or [])[-8:],
        "error": task.get("error") or "",
        "created_at": task.get("created_at") or "",
        "finished_at": task.get("finished_at") or "",
    }


def _requirement_task(project_id: str, params: dict) -> dict:
    task_id = str(params.get("task_id") or "").strip()
    if task_id:
        task = store.get_task(project_id, task_id)
        if not task:
            return {"found": False, "error": f"没有找到任务 {task_id}"}
        return {"found": True, "task": _task_brief(task)}
    rows = [task for task in store.list_tasks(project_id)
            if "requirement" in str(task.get("kind") or "")]
    rows.sort(key=lambda task: str(task.get("created_at") or ""), reverse=True)
    return {"tasks": [_task_brief(task) for task in rows[:5]],
            "note": "只返回需求相关任务的最近 5 条；没有记录说明还没发起过解析。"}


def _requirement_ai_fill(project_id: str) -> dict:
    saved = store.load_requirement(project_id)
    if not saved:
        return {"available": False, "note": "还没有 1.1 需求单草稿。"}
    data = saved.get("data") or {}
    info = data.get("document_extraction") or {}
    if not info:
        return {"available": False,
                "note": "还没有 AI 解析结果；请先上传技术资料并执行 ExtractRequirement。"}
    from . import requirement_extract

    recommendations = dict(info.get("recommendations") or {})
    required = sorted(requirement_extract._required_recommendation_fields_for_industry(
        _requirement_industry(data)))
    # 仍缺的必填项：既没有值，也没有 AI 推荐默认值。
    missing = [key for key in required
               if not str(data.get(key) or "").strip()
               and not str(recommendations.get(key) or "").strip()]
    return {
        "available": True,
        "industry": _requirement_industry(data),
        "model": info.get("model", ""),
        "extracted_at": info.get("extracted_at", ""),
        "summary": info.get("summary", ""),
        "filled_fields": list(info.get("all_filled_fields") or info.get("filled_fields") or []),
        "recommended_fields": list(info.get("all_recommended_fields")
                                   or info.get("recommended_fields") or []),
        "recommendation_confidence": dict(info.get("recommendation_confidence") or {}),
        "recommendations": recommendations,
        "missing_required_fields": missing,
        "open_questions": list(info.get("open_questions") or []),
    }


def _submit_requirement_confirmation(project_id: str, params: dict) -> dict:
    from . import auth, requirement_service

    user = _actor_user()
    if user.get("role") not in auth.MANAGER_ROLES:
        return {"submitted": False,
                "error": "提交需求确认需要工艺技术经理或管理员权限；请由对应角色在 1.1 页面提交。"}
    comment = str(params.get("comment") or "需求创建人已提交，等待需求确认。")
    try:
        out = requirement_service.submit_requirement_confirmation(project_id, user, comment)
    except requirement_service.RequirementSaveError as exc:
        return {"submitted": False, "error": str(exc)}
    return {"submitted": True, "status": out.get("status", ""),
            "requirement_no": out.get("requirement_no", ""),
            "note": "已提交到 1.2 待确认；后续仍由人工确认或退回，Agent 不代替审批。"}


# --------------------------------------------------------------------------- #
# 1.2 确认需求 / 1.3 审核需求
# --------------------------------------------------------------------------- #
# 预检、确认、退回、审核的状态流转都只有一份实现（services/requirement_service.py），
# 这里只是把同一份结果投影成 Agent 可读的结构，绝不自己重写规则或状态机。
def _requirement_status(project_id: str) -> str:
    saved = store.load_requirement(project_id) or {}
    return str(saved.get("status") or "")


def _requirement_precheck_data(project_id: str) -> dict:
    from . import requirement_service

    saved = store.load_requirement(project_id)
    if not saved:
        return {"available": False, "status": "",
                "note": "还没有需求单；请先在 1.1 创建需求并提交到 1.2 确认。"}
    result = requirement_service.requirement_precheck(project_id, _requirement_doc(saved))
    needs = [row for row in result.get("items") or [] if row.get("status") == "need_info"]
    return {
        "available": True,
        "requirement_no": saved.get("requirement_no", ""),
        "title": saved.get("title", ""),
        "status": saved.get("status", ""),
        "items": result.get("items") or [],
        "need_info": [{"item": row.get("item", ""), "detail": row.get("detail", "")}
                      for row in needs],
        "ok": result.get("ok") is True,
        "generated_note": result.get("generated_note", ""),
        "engine": result.get("engine", ""),
    }


def _requirement_clarifications(project_id: str) -> dict:
    """只把同一份预检里 status == need_info 的条目投影成待澄清问题。"""
    precheck = _requirement_precheck_data(project_id)
    if not precheck.get("available"):
        return {"available": False, "status": "", "count": 0, "questions": [],
                "note": precheck.get("note", "")}
    questions = list(precheck.get("need_info") or [])
    return {
        "available": True,
        "requirement_no": precheck.get("requirement_no", ""),
        "status": precheck.get("status", ""),
        "count": len(questions),
        "questions": questions,
        "generated_note": precheck.get("generated_note", ""),
        "note": ("以上问题来自既有确定性预检的 need_info 项，未另立规则、未调用模型。"
                 if questions else "既有确定性预检没有待澄清项。"),
    }


def _confirm_requirement(project_id: str, comment: str) -> dict:
    from . import auth, requirement_service

    user = _actor_user()
    if user.get("role") not in auth.MANAGER_ROLES:
        return {"applied": False, "error": "通过需求确认需要工艺技术经理或管理员权限。"}
    try:
        out = requirement_service.confirm_requirement(project_id, user, comment)
    except requirement_service.RequirementSaveError as exc:
        return {"applied": False, "error": str(exc)}
    return {"applied": True, "status": out.get("status", ""),
            "requirement_no": out.get("requirement_no", ""),
            "note": "需求已进入 1.3 待审核；请刷新右侧看板查看状态与流程留痕。"}


def _return_requirement_to_draft(project_id: str, comment: str) -> dict:
    from . import auth, requirement_service

    user = _actor_user()
    if user.get("role") not in auth.MANAGER_ROLES:
        return {"applied": False, "error": "退回需求需要工艺技术经理或管理员权限。"}
    try:
        out = requirement_service.return_requirement_to_draft(project_id, user, comment)
    except requirement_service.RequirementSaveError as exc:
        return {"applied": False, "error": str(exc)}
    return {"applied": True, "status": out.get("status", ""),
            "requirement_no": out.get("requirement_no", ""),
            "note": "需求已退回 1.1 草稿；请刷新右侧看板查看状态与流程留痕。"}


def _requirement_review_materials(project_id: str) -> dict:
    """1.3 审核材料：需求关键字段 + 附件 + 确认信息 + 确定性预检 + 历史留痕（只读）。"""
    saved = store.load_requirement(project_id)
    if not saved:
        return {"available": False, "note": "还没有需求单；请先完成 1.1 创建与 1.2 确认。"}
    meta = store.load_meta(project_id) or {}
    data = saved.get("data") or {}
    precheck = _requirement_precheck_data(project_id)
    attachments = [name for name, _ in store.load_attachments(project_id)]
    history = [dict(row) for row in (saved.get("history") or [])][-10:]
    return {
        "available": True,
        "requirement_no": saved.get("requirement_no", ""),
        "title": saved.get("title", ""),
        "status": saved.get("status", ""),
        "created_by": saved.get("created_by", ""),
        "created_at": saved.get("created_at", ""),
        "confirmed_by": saved.get("confirmed_by", ""),
        "confirmed_at": saved.get("confirmed_at", ""),
        "confirmation_note": saved.get("confirmation_note", ""),
        "source_filename": meta.get("source_filename", ""),
        "attachments": attachments,
        "file_roles": data.get("file_roles") or {},
        "key_fields": {
            "requirement_type": data.get("requirement_type", ""),
            "priority": data.get("priority", ""),
            "bu": data.get("bu", ""),
            "customer_type": data.get("customer_type", ""),
            "final_customer_name": data.get("final_customer_name", ""),
            "project_name": data.get("project_name", ""),
            "project_code": data.get("project_code", ""),
            "annual_forecast": data.get("annual_forecast", ""),
            "first_sample_due": data.get("first_sample_due", ""),
            "mass_production_due": data.get("mass_production_due", ""),
        },
        "precheck": {"ok": precheck.get("ok"), "generated_note": precheck.get("generated_note", ""),
                     "items": precheck.get("items") or []},
        "history": history,
        "note": "以上材料全部来自既有 store 与确定性预检，只读，不改任何状态、不调模型。",
    }


def _requirement_review_summary(project_id: str) -> dict:
    """由同一份预检 + 关键字段做确定性汇总，不调模型、不编造结论。"""
    materials = _requirement_review_materials(project_id)
    if not materials.get("available"):
        return {"available": False, "note": materials.get("note", "")}
    precheck = materials.get("precheck") or {}
    items = list(precheck.get("items") or [])
    needs = [row for row in items if row.get("status") == "need_info"]
    summary = ("确定性检查未发现待补充项，资料齐备，可提交审核通过。"
               if not needs else
               f"确定性检查仍有 {len(needs)} 个待补充项，建议先退回补充再审核通过。")
    return {
        "available": True,
        "review_summary": {
            "requirement_no": materials.get("requirement_no", ""),
            "title": materials.get("title", ""),
            "status": materials.get("status", ""),
            "summary": summary,
            "generated_note": precheck.get("generated_note", ""),
            "items": items,
            "need_info": [{"item": row.get("item", ""), "detail": row.get("detail", "")}
                          for row in needs],
            "key_fields": materials.get("key_fields") or {},
            "decision_options": [
                {"decision": "approve", "label": "审核通过", "requires_confirmation": True},
                {"decision": "reject", "label": "退回修改", "requires_confirmation": True},
            ],
        },
        "note": "确定性汇总，不调用模型、不代替人工审批；通过 / 退回都需用户明确确认。",
    }


def _approve_requirement_review(project_id: str, comment: str) -> dict:
    return _run_requirement_review(project_id, "approve", comment)


def _reject_requirement_review(project_id: str, comment: str) -> dict:
    return _run_requirement_review(project_id, "reject", comment)


def _run_requirement_review(project_id: str, decision: str, comment: str) -> dict:
    from . import auth, requirement_service

    user = _actor_user()
    if user.get("role") not in auth.DIRECTOR_ROLES:
        return {"applied": False, "decision": decision,
                "error": "审核需求需要工艺技术总监或管理员权限。"}
    try:
        out = requirement_service.review_requirement(project_id, user, decision, comment)
    except requirement_service.RequirementSaveError as exc:
        return {"applied": False, "decision": decision, "error": str(exc)}
    return {"applied": True, "decision": decision, "status": out.get("status", ""),
            "requirement_no": out.get("requirement_no", ""),
            "note": "审核结果已落盘；请刷新右侧看板查看审核状态与流程留痕。"}


# --------------------------------------------------------------------------- #
# 2.2 组装与整合
# --------------------------------------------------------------------------- #
def _integration_state(project_id: str) -> dict:
    """2.2 的全貌。给模型一份摘要，而不是把整份 plan 倒进上下文。"""
    from . import integration, product_params

    plan = integration.load_plan(project_id)
    status = integration.status(plan)
    checklist = product_params.checklist(plan.params) if plan.params else None
    process = plan.process
    cost = plan.cost
    written = plan.material_writes[-1] if plan.material_writes else None
    return {
        "project_id": project_id,
        "requirement_note": plan.requirement_note,
        "quantity": plan.quantity,
        "drawings": [item.filename for item in plan.drawings],
        "status": status,
        "assembly_name": (plan.params.assembly_name if plan.params else "") or "",
        "product_family": (checklist or {}).get("family_name") or "",
        "param_summary": (checklist or {}).get("summary") or {},
        "missing_required": [field["name"] for field
                             in product_params.missing_required(plan.params)] if plan.params else [],
        "process": {
            "step_count": len(process.steps) if process else 0,
            "total_minutes": round(sum(float(step.duration_min or 0)
                                       for step in (process.steps if process else [])), 2),
            "steps": [{"step_no": step.step_no, "name": step.name,
                       "type": getattr(step.type, "value", str(step.type)),
                       "equipment": step.equipment or "",
                       "duration_min": step.duration_min} for step in (process.steps if process else [])],
            "rule_warnings": list(process.rule_warnings) if process else [],
        },
        "cost": (cost.cost_model or {}) if cost else {},
        "material_code": ({"number": written.number, "name": written.name,
                           "unit_price": written.material_unit_price} if written else None),
        "params_final": bool(plan.params_final),
        "quote_handoff": plan.quote_handoff.model_dump() if plan.quote_handoff else None,
    }


def _integration_params(project_id: str, only_missing: bool = False) -> dict:
    from . import integration, product_params

    plan = integration.load_plan(project_id)
    if not plan.params:
        return {"error": "还没有做过参数推荐。请先让平台跑「参数推荐」（RequestIntegrationStep）。"}
    report = product_params.checklist(plan.params)
    groups = []
    for group in report["groups"]:
        fields = [field for field in group["fields"] if not only_missing or not field["filled"]]
        if fields:
            groups.append({"group": group["name"], "fields": [
                {"code": f["code"], "name": f["name"], "value": f["value"], "unit": f["unit"],
                 "required": f["required"], "filled": f["filled"],
                 "options": f["options"], "example": f["example"]} for f in fields]})
    return {"family": report["family_name"], "summary": report["summary"], "groups": groups,
            "extras": report.get("extras") or []}


def _update_integration_params(project_id: str, params: dict) -> dict:
    """按字段编码写整机参数。留审计 —— Agent 改的业务数据必须记「是谁让它改的」。"""
    from . import integration, product_params

    rows = params.get("values")
    if not isinstance(rows, list) or not rows:
        return {"updated": False, "error": "values 至少要给一项 {code, value}"}
    values, rejected = {}, []
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        if code == "product_item_code":
            rejected.append(f"{code}（成品编码由「写入数据库」生成，不能手工写）")
            continue
        if not product_params.field_of(code):
            rejected.append(f"{code}（不在报价成品参数字典里）")
            continue
        values[code] = {"value": str(row.get("value") or ""), "unit": str(row.get("unit") or "")}
    if not values:
        return {"updated": False, "rejected": rejected,
                "error": "没有一项可写入；字段编码请先用 ListIntegrationParams 确认"}
    plan = integration.load_plan(project_id)
    integration.finalize_params(plan, values)
    # 参数改了，之前那两次确认都不再作数 —— 确认针对的是当时那一版。
    plan.params_confirmed = False
    plan.params_confirmed_by = None
    plan.params_confirmed_at = None
    plan.params_final = False
    plan.params_final_by = None
    plan.params_final_at = None
    integration.save_plan(project_id, plan, current_actor())
    store.audit(project_id, "agent_update_integration_params", {
        "by": current_actor(), "codes": sorted(values),
        "reason": str(params.get("reason") or "")[:200]})
    return {"updated": True, "written": sorted(values), "rejected": rejected,
            "missing_required": [field["name"] for field
                                 in product_params.missing_required(plan.params)],
            "note": "参数已写入。「整合参数」的确认状态已重置，请提醒用户核对后重新确认。"}


def _update_integration_process(project_id: str, params: dict) -> dict:
    """改组装工序：改一道、加一道、删一道。工序号是唯一的定位键。"""
    from ..models.process import ProcessStep
    from . import integration, process as process_svc

    rows = params.get("updates")
    if not isinstance(rows, list) or not rows:
        return {"updated": False, "error": "updates 至少要给一项 {step_no, action}"}
    plan = integration.load_plan(project_id)
    if plan.process is None:
        return {"updated": False,
                "error": "还没有组装工艺。请先让平台跑「组装工艺」（RequestIntegrationStep）。"}
    steps = plan.process.steps
    done, failed = [], []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            step_no = int(row.get("step_no"))
        except (TypeError, ValueError):
            failed.append("step_no 必须是整数")
            continue
        action = str(row.get("action") or "update").strip().lower()
        found = next((step for step in steps if step.step_no == step_no), None)
        if action == "delete":
            if not found:
                failed.append(f"{step_no}：没有这道工序")
                continue
            steps.remove(found)
            done.append(f"删除 {step_no} {found.name}")
            continue
        if action == "add":
            if found:
                failed.append(f"{step_no}：工序号已存在，改用 action=update")
                continue
            found = ProcessStep(step_no=step_no, name=str(row.get("name") or f"工序 {step_no}"),
                                type=str(row.get("type") or "assembly"), description="")
            steps.append(found)
            done.append(f"新增 {step_no} {found.name}")
        elif not found:
            failed.append(f"{step_no}：没有这道工序（要新增请给 action=add）")
            continue
        else:
            done.append(f"修改 {step_no} {found.name}")
        for field in ("name", "equipment", "note"):
            if row.get(field) is not None:
                setattr(found, field, str(row[field]))
        if row.get("type") is not None:
            try:
                found.type = type(found.type)(str(row["type"]))
            except ValueError:
                failed.append(f"{step_no}：工序类型 {row['type']} 不在枚举里，已保留原值")
        if row.get("duration_min") is not None:
            try:
                found.duration_min = float(row["duration_min"])
            except (TypeError, ValueError):
                failed.append(f"{step_no}：工时不是数值，已保留原值")
    if not done:
        return {"updated": False, "failed": failed, "error": "没有一项改动生效"}
    steps.sort(key=lambda step: step.step_no)
    # 组装线是一条顺序流水：删/加之后依赖链要重新串，否则关键路径算不出来。
    previous = None
    for step in steps:
        step.depends_on = [previous] if previous is not None else []
        previous = step.step_no
    plan.process.part_class = "assembly"
    plan.process.rule_warnings = process_svc.validate_rules(plan.process.model_dump())
    integration.save_plan(project_id, plan, current_actor())
    store.audit(project_id, "agent_update_integration_process", {
        "by": current_actor(), "changes": done,
        "reason": str(params.get("reason") or "")[:200]})
    return {"updated": True, "changes": done, "failed": failed,
            "step_count": len(steps), "rule_warnings": list(plan.process.rule_warnings),
            "note": "工序已写入。成本是按工序与物料算的，改完工序建议重跑「成本测算」。"}


def _integration_status(project_id: str) -> str:
    from . import integration

    return integration.status(integration.load_plan(project_id))


def _confirm_integration_params(project_id: str) -> dict:
    """确认 2.2「参数推荐」：与看板 POST /integration/params/confirm 同一份实现，不重算、不另写状态。"""
    from . import integration

    try:
        plan = integration.confirm_params(project_id, _actor_user())
    except integration.IntegrationFlowError as exc:
        return {"applied": False, "action": "confirm-params", "error": str(exc)}
    return {"applied": True, "action": "confirm-params", "params_confirmed": True,
            "confirmed_by": plan.params_confirmed_by or "",
            "status": _integration_status(project_id),
            "note": "「参数推荐」已确认；请刷新右侧看板查看确认状态。"}


def _confirm_integration_process(project_id: str) -> dict:
    """确认 2.2「组装工艺」：与看板 POST /integration/process/confirm 同一份实现。"""
    from . import integration

    try:
        plan = integration.confirm_process(project_id, _actor_user())
    except integration.IntegrationFlowError as exc:
        return {"applied": False, "action": "confirm-process", "error": str(exc)}
    return {"applied": True, "action": "confirm-process", "process_confirmed": True,
            "confirmed_by": plan.process_confirmed_by or "",
            "status": _integration_status(project_id),
            "note": "「组装工艺」已确认；请刷新右侧看板查看确认状态。"}


def _send_integration_to_finance(project_id: str, product_name: str, note: str) -> dict:
    """2.2 出口：把工艺与整机参数发送给财务。对外调用与看板同一份实现，只在这里翻译错误。"""
    from . import cpq_bridge, integration

    try:
        plan = integration.send_to_finance(
            project_id, _actor_user(), product_name=product_name, note=note,
            token=current_token())
    except integration.IntegrationFlowError as exc:
        return {"applied": False, "action": "send-to-finance", "error": str(exc)}
    except cpq_bridge.BridgeRejected as exc:
        return {"applied": False, "action": "send-to-finance",
                "error": f"CPQ 服务拒绝了这次发送财务：{exc}"}
    except cpq_bridge.BridgeUnavailable as exc:
        return {"applied": False, "action": "send-to-finance",
                "error": f"发送财务失败：{exc}"}
    handoff = plan.finance_handoff
    return {"applied": True, "action": "send-to-finance",
            "task_no": handoff.task_no if handoff else "",
            "sent_to": handoff.target_role_name if handoff else "",
            "status": _integration_status(project_id),
            "note": "已发送财务做成本测算；请刷新右侧看板查看发送状态与接收人。"}


def _project_state(project_id: str) -> dict:
    meta = store.load_meta(project_id) or {}
    ir = _ir_of(project_id)
    parts = (ir or {}).get("parts") or []
    confidences = [float(part.get("confidence") or 0) for part in parts]
    attachments = [name for name, _ in store.load_attachments(project_id)]
    return {
        "project_id": project_id,
        "project_name": meta.get("name") or "",
        "source_drawing": meta.get("source_filename") or "",
        "supplementary_note": store.get_note(project_id),
        "attachments": attachments,
        "parsed": bool(ir),
        "device_name": (ir or {}).get("device_name") or "",
        "design_intent": (ir or {}).get("design_intent") or "",
        "overall_dims": (ir or {}).get("overall_dims") or "",
        "assembly_notes": (ir or {}).get("assembly_notes") or "",
        "part_count": len(parts),
        "assembly_count": len((ir or {}).get("assemblies") or []),
        "standard_part_count": len((ir or {}).get("standard_parts") or []),
        "open_question_count": len((ir or {}).get("open_questions") or []),
        "average_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
    }


def _parts(project_id: str) -> list[dict]:
    ir = _ir_of(project_id) or {}
    out = []
    for part in ir.get("parts") or []:
        material = part.get("material") or {}
        out.append({
            "part_id": part.get("part_id"), "name": part.get("name"),
            "parent_id": part.get("parent_id"), "role": part.get("role"),
            "material": material.get("spec") if isinstance(material, dict) else material,
            "quantity": part.get("quantity"), "confidence": part.get("confidence"),
            "feature_count": len(part.get("features") or []),
        })
    return out


def _part_detail(project_id: str, part_id: str) -> dict:
    ir = _ir_of(project_id) or {}
    for part in ir.get("parts") or []:
        if str(part.get("part_id")) == part_id:
            return part
    return {"error": f"未找到零件 {part_id}",
            "available": [part.get("part_id") for part in ir.get("parts") or []]}


def _open_questions(project_id: str) -> list[dict]:
    ir = _ir_of(project_id) or {}
    return list(ir.get("open_questions") or [])


def _match_of(project_id: str, part_id: str) -> Optional[dict]:
    report = store.load_component_match(project_id) or {}
    return next((item for item in report.get("items") or []
                 if item.get("part_id") == part_id), None)


def _update_part(project_id: str, params: dict) -> dict:
    """按白名单改零件参数，存版本快照并写审计。

    校验逻辑不在这里 —— 与老的 workbench-chat 共用 `part_edit`，两条会话入口
    必须是同一套规则。这里只负责：取 IR → 交给它改 → 落盘 → 如实回报改了什么。

    失败一律返回 `{"error": …}` 而不是抛异常：工具报错应该变成模型看得懂的
    工具结果，让它把原因转述给用户，而不是中断整轮对话。
    """
    from . import part_edit                                # 延迟导入避免环依赖
    from ..models.ir import DesignIR

    part_id = str(params.get("part_id") or "").strip()
    if not part_id:
        return {"error": "必须指定 part_id"}

    blocked = part_edit.blocks_feature_edit(store.load_meta(project_id))
    if blocked:
        return {"error": blocked, "applied": False}

    raw = _ir_of(project_id)
    if not raw or not raw.get("parts"):
        return {"error": "尚未完成图纸解析，没有可修改的零件"}
    try:
        ir = DesignIR.model_validate(raw)
    except Exception as exc:                                # pragma: no cover - IR 损坏
        return {"error": f"当前 IR 无法解析，未做任何修改：{exc}"}

    part = next((item for item in ir.parts if str(item.part_id) == part_id), None)
    if part is None:
        return {"error": f"未找到零件 {part_id}",
                "available": [item.part_id for item in ir.parts]}

    try:
        changes, geometry_changed = part_edit.apply_edit(
            part,
            name=params.get("name"),
            quantity=params.get("quantity"),
            material_spec=params.get("material_spec"),
            feature_updates=params.get("feature_updates") or (),
        )
    except part_edit.PartEditError as exc:
        return {"error": str(exc), "applied": False}

    if not changes:
        return {"applied": False, "part_id": part_id, "changes": [],
                "note": "给出的值与当前值相同，未做修改。"}

    actor = current_actor()
    reason = str(params.get("reason") or "").strip()[:200]
    note = (f"Agent 对话修改 {part_id}：" + "、".join(c["field"] for c in changes)
            + (f"（依据：{reason}）" if reason else ""))
    store.save_ir(project_id, ir.model_dump(), stage="agent_edited",
                  author=actor, note=note)
    store.audit(project_id, "agent_part_edit", {
        "by": actor, "part_id": part_id, "changes": changes, "reason": reason,
    })
    return {
        "applied": True,
        "part_id": part_id,
        "changes": changes,
        "requires_regeneration": geometry_changed,
        "note": ("已改写并留下可回溯版本。"
                 + ("特征尺寸变了，平台会在界面上重新生成该零件的 3D 与工程图；"
                    "如实说明是平台在做，不要自称是你生成的。"
                    if geometry_changed else "")),
    }


def _component_library(project_id: str, part_id: str) -> dict:
    """现查零部件库。与工艺/成本库一样不读缓存 —— 库里新录了件就该看到。

    不传 part_id 时返回整份汇总：用户常问的是"整台设备有多少能复用"，
    逐件问一遍既慢又容易漏。
    """
    from . import component_match                                # 延迟导入避免环依赖

    ir = _ir_of(project_id)
    if not ir or not ir.get("parts"):
        return {"error": "尚未完成图纸解析，零部件库无可检索的零件清单"}
    if not part_id:
        report = component_match.match_project(project_id, ir)
        component_match.save_report(project_id, report)
        return report
    part = _part_detail(project_id, part_id)
    if part.get("error"):
        return part
    return component_match.match_part(part)


def _process_library(project_id: str, part_id: str) -> dict:
    """现查工艺库，不读缓存 —— 库改了就该看到新结果。"""
    from . import process_lookup                                  # 延迟导入避免环依赖

    part = _part_detail(project_id, part_id)
    if part.get("error"):
        return part
    report = process_lookup.lookup_part(part, match=_match_of(project_id, part_id))
    process_lookup.save_report(project_id, part_id, report)
    return report


def _cost_library(project_id: str, part_id: str, quantity: int) -> dict:
    from . import cost_lookup                                     # 延迟导入避免环依赖

    part = _part_detail(project_id, part_id)
    if part.get("error"):
        return part
    report = cost_lookup.lookup_part(
        part, quantity=max(1, quantity), match=_match_of(project_id, part_id),
        process_report=store.load_process_lookup(project_id, part_id),
    )
    cost_lookup.save_report(project_id, part_id, report)
    return report


def _install_tool_dispatch(oc_repl) -> None:
    """在 open-claude 的 execute_tool 调用点前置平台工具分派（幂等）。"""
    global _patched
    if _patched:
        return
    original = oc_repl.execute_tool

    def dispatch(name: str, params: dict, cwd: str) -> str:
        if name in PLATFORM_TOOL_NAMES:
            try:
                return _run_platform_tool(name, params or {}, cwd)
            except Exception as exc:                     # 工具异常不应打断整轮对话
                return f"平台工具 {name} 执行失败：{exc}"
        return original(name, params, cwd)

    dispatch.__wrapped__ = original                      # 便于排查与还原
    oc_repl.execute_tool = dispatch
    _patched = True


SYSTEM_APPENDIX = """
你现在是「AI 工艺评估平台」的工艺助手，服务对象是工艺工程师。同一个项目会在两个页面
和你对话，每条消息末尾的 [当前页面：…] 标明用户此刻在哪一步 —— 按那一步选工具。

2.1 图纸解析：
- 依据平台里已有的图纸、技术文档与解析结果（IR）回答问题，先用 GetProjectState 取状态；
- 讨论零件结构、材料、公差、可制造性与待澄清风险；
- 用户要求开始/重新解析时调用 RequestParse，由平台执行，你不要自行编造解析结果。

2.2 组装与整合（把 2.1 的零件装回一台整机）：
- 先用 GetIntegrationState 取状态，再用 ListIntegrationParams 看参数缺口，不要拿 2.1 的
  零件数据代替整机结论；
- 用户要改整机参数用 UpdateIntegrationParams、要改组装工序用 UpdateIntegrationProcess，
  两者都会**真的写进业务数据**：只在用户给了明确目标值时调用，没给就先问；
- 用户要重跑某个环节时调用 RequestIntegrationStep（params / process / cost），
  由平台执行，不要自行编造参数、工序或金额；
- 报价必填的成品参数缺项要主动点名 —— 缺一项这台成品就进不了报价测算单。

硬性要求：
- 尺寸、材料、公差、数量只能来自工具返回的数据。资料里没有的，明确说"图纸未给出"并列为待确认，
  绝不按行业惯例填补数值 —— 这些数字会进入工艺方案与报价。
- 回答用中文，简洁、结论先行；涉及数值时标注它来自哪个零件或哪份资料。
- 当前会话的文件系统是只读的，你不能修改项目文件。
"""


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
class ProjectAgent:
    """一个项目一个 open-claude Conversation。"""

    def __init__(self, project_id: str, profile_name: Optional[str] = None):
        modules = _import_open_claude()
        self._oc = modules
        oc_repl = modules["repl"]
        _install_tool_dispatch(oc_repl)
        # 只读兜底同时覆盖子代理；CLI 从不设置该标志，因此不受影响。
        os.environ.setdefault("OC_READONLY_FS", "1")

        self.project_id = project_id
        self.cwd = str(_project_workdir(project_id))
        # 会话必须建在当前所选模型的路由上：open-claude 在构造时就把 provider 与 Key
        # 读进去了，先同步环境再建会话，否则选了 Qwen 也会被发去 Anthropic。
        try:
            sync_route_environment(agent_route())
        except Exception:                                        # pragma: no cover - 配置不可读时退回 open-claude 默认
            pass
        profile = modules["load_profile"](profile_name, self.cwd) if profile_name else None
        self.conv = oc_repl.Conversation(self.cwd, permission_mode="always_allow", profile=profile)
        # 没有终端可以回答 y/n，任何询问都直接放行（只读限制仍然生效）。
        self.conv.permissions._prompt_user = lambda *args, **kwargs: (True, "")

        for tool_name in READONLY_DISABLED_TOOLS:
            if tool_name not in self.conv.profile.disabled_tools:
                self.conv.profile.disabled_tools.append(tool_name)
        self._rebuild_tools()
        self.conv.system_prompt = f"{self.conv.system_prompt}\n{SYSTEM_APPENDIX}"
        self.lock = threading.Lock()

    def _rebuild_tools(self) -> None:
        self.conv._build_tool_schemas()
        existing = {schema.get("name") for schema in self.conv.tool_schemas}
        for schema in PLATFORM_TOOL_SCHEMAS:
            if schema["name"] not in existing:
                self.conv.tool_schemas.append(schema)

    # -- 元信息（供左侧对话设置工具栏展示）--------------------------------- #
    def meta(self) -> dict:
        conv = self.conv
        return {
            "model": conv.model,
            "profile": conv.profile.name,
            "cwd": self.cwd,
            "project_id": self.project_id,
            "models": [{"id": item["id"], "label": item["label"]}
                       for item in self._oc["AVAILABLE_MODELS"]],
            "tools": [{"name": schema.get("name", ""),
                       "description": (schema.get("description", "") or "")[:140],
                       "platform": schema.get("name") in PLATFORM_TOOL_NAMES}
                      for schema in conv.tool_schemas],
            "skills": [{"name": skill.name, "description": skill.description or ""}
                       for skill in self._oc["get_registry"]().get_user_invocable()],
            "message_count": len(conv.messages),
        }

    def reset(self) -> None:
        with self.lock:
            self.conv.messages.clear()
            self.conv.session = self._oc["SessionStore"](self.cwd)
            self.conv.cost_tracker.__init__()

    def set_model(self, model_id: str) -> None:
        with self.lock:
            self.conv.model = model_id
            os.environ["CLAUDE_MODEL"] = model_id

    # -- 全局配置的落地 ---------------------------------------------------- #
    def apply(self, params: dict, *, rebuild_client: bool = False) -> None:
        """把全局配置（llm_settings）落到这个已经建好的会话上。

        取值范围由 llm_settings 统一夹住，这里只负责赋值 —— 校验放两处就会分叉。
        """
        with self.lock:
            profile = self.conv.profile
            if params.get("agent_model"):
                self.conv.model = str(params["agent_model"])
            profile.temperature = params.get("temperature")
            profile.thinking = bool(params.get("thinking"))
            if params.get("thinking_budget"):
                profile.thinking_budget = int(params["thinking_budget"])
            profile.max_tokens = params.get("max_tokens")
            if params.get("max_iterations"):
                profile.max_iterations = int(params["max_iterations"])
            if rebuild_client:
                self._rebuild_client()

    def _rebuild_client(self) -> None:
        """按最新路由重建 client（调用方需已持有 self.lock）。

        换 provider 或换 Key 必须重建：open-claude 在构造 client 时就把 provider
        与 Key 读进去了，沿用旧 client 会把新模型发给旧厂商。
        """
        try:
            route = agent_route()
        except Exception:                                        # pragma: no cover - 配置不可读
            route = {}
        if route:
            try:
                sync_route_environment(route)
            except Exception:                                    # pragma: no cover - 环境相关
                pass
            if route.get("model"):
                self.conv.model = str(route["model"])
        self.conv.client = self._oc["repl"].create_client()

    # -- 一轮对话（镜像 Conversation.run_turn，改为产出事件）--------------- #
    def stream_turn(self, text: str, emit: Callable[[dict], None]) -> None:
        with self.lock:
            conv = self.conv
            conv.add_user_message(text)
            try:
                for _ in range(max(1, conv.profile.max_iterations)):
                    conv._maybe_compact()
                    stop_reason = self._stream_once(conv, emit)
                    if stop_reason != "tool_use":
                        break
                    # 复用 open-claude 自己的执行路径（权限、钩子、Agent/MCP 分派）。
                    conv._execute_pending_tools()
                    last = conv.messages[-1] if conv.messages else None
                    if last and last.get("role") == "user" and isinstance(last.get("content"), list):
                        for block in last["content"]:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                emit({
                                    "type": "tool_result",
                                    "tool_use_id": block.get("tool_use_id", ""),
                                    "content": _stringify(block.get("content", "")),
                                    "is_error": bool(block.get("is_error", False)),
                                })
            except Exception as exc:                     # pragma: no cover - 依赖外部服务
                traceback.print_exc()
                emit({"type": "error", "error": str(exc)})
            finally:
                emit({
                    "type": "done", "model": conv.model,
                    "cost": round(getattr(conv.cost_tracker, "total_cost_usd", 0.0), 5),
                })

    def _stream_once(self, conv, emit: Callable[[dict], None]) -> str:
        text_buffer: list[str] = []
        tool_uses: list[dict] = []
        stop_reason = "end_turn"

        stream = self._oc["stream_message"](
            conv.client, conv.messages, conv.system_prompt,
            model=conv.model, tools=conv.tool_schemas,
            max_tokens=conv.profile.max_tokens,
            temperature=conv.profile.temperature,
            thinking_budget=conv.profile.thinking_budget if conv.profile.thinking else None,
        )
        for event in stream:
            kind = event["type"]
            if kind == "text_delta":
                text_buffer.append(event["text"])
                emit({"type": "text", "text": event["text"]})
            elif kind == "tool_use_end":
                tool_uses.append({"type": "tool_use", "id": event["id"],
                                  "name": event["name"], "input": event["input"]})
                emit({"type": "tool_use", "id": event["id"], "name": event["name"],
                      "input": event["input"],
                      "ui_action": UI_ACTION_TOOLS.get(event["name"], "")})
            elif kind == "message_end":
                stop_reason = event.get("stop_reason", "end_turn")
                usage = event.get("usage", {})
                conv.cost_tracker.add_usage(
                    conv.model,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cache_read=usage.get("cache_read_input_tokens", 0),
                    cache_creation=usage.get("cache_creation_input_tokens", 0),
                )
            elif kind == "error":
                emit({"type": "error", "error": event["error"]})
                stop_reason = "error"
                break

        content: list[dict] = []
        full_text = "".join(text_buffer)
        if full_text:
            content.append({"type": "text", "text": full_text})
        content.extend(tool_uses)
        if content:
            message = {"role": "assistant", "content": content}
            conv.messages.append(message)
            conv.session.append_message(message)
        return stop_reason


def _project_workdir(project_id: str) -> Path:
    """Agent 的工作目录：该项目的数据目录（图纸、附件、几何文件都在这里）。"""
    directory = Path(DATA_DIR) / project_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _stringify(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(block))
        return "\n".join(part for part in parts if part)
    return json.dumps(content, ensure_ascii=False, default=str)


# --------------------------------------------------------------------------- #
# 会话池
# --------------------------------------------------------------------------- #
def get_agent(project_id: str) -> ProjectAgent:
    with _lock:
        agent = _sessions.get(project_id)
        if agent is None:
            agent = ProjectAgent(project_id)
            # 新会话立刻套用全局配置，否则「在别处改过的设置」要等重启才生效。
            _apply_global(agent)
            _sessions[project_id] = agent
        return agent


def _apply_global(agent: ProjectAgent) -> None:
    try:
        from . import llm_settings

        agent.apply(llm_settings.agent_params())
    except Exception:                                   # pragma: no cover - 依赖环境
        pass


def apply_settings(params: dict, *, rebuild_client: bool = False) -> None:
    """把全局配置推给**所有已经建好的会话**。

    没有这一步，改设置就只对之后新建的会话生效，用户会看到"我明明改了"。
    """
    with _lock:
        agents = list(_sessions.values())
    for agent in agents:
        try:
            agent.apply(params, rebuild_client=rebuild_client)
        except Exception:                               # pragma: no cover - 单个会话失败不影响其他
            continue


def drop_agent(project_id: str) -> None:
    with _lock:
        _sessions.pop(project_id, None)


def sse(event: dict) -> str:
    """把一个事件编码成 SSE 帧。"""
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def stream_sse(project_id: str, message: str, actor: str = "system",
               token: str = "") -> Iterator[str]:
    """把一轮对话产出为 SSE 帧序列。事件通过队列跨线程传递，保证边生成边下发。"""
    import queue

    agent = get_agent(project_id)
    channel: "queue.Queue[Optional[dict]]" = queue.Queue()

    def worker() -> None:
        # ContextVar 不会自动跨线程传播，必须在 worker 内部再设一次，
        # 否则 UpdatePartParameters 的审计只会记到 system。
        _ACTOR.set(actor or "system")
        # 同理：SendIntegrationToFinance 代表用户去调一体化服务需要用户令牌。
        _TOKEN.set(token or "")
        try:
            agent.stream_turn(message, channel.put)
        except Exception as exc:                         # pragma: no cover - 依赖外部服务
            channel.put({"type": "error", "error": str(exc)})
            channel.put({"type": "done", "model": "", "cost": 0})
        finally:
            channel.put(None)

    thread = threading.Thread(target=worker, name=f"oc-agent-{project_id}", daemon=True)
    thread.start()
    while True:
        event = channel.get()
        if event is None:
            break
        yield sse(event)

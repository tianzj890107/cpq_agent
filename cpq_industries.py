"""全局行业注册表（唯一事实源）—— 报价侧与技术工艺侧共用。

平台原来把行业清单散在至少 7 处各自维护（首页下拉、cpq-industry.js、tech-task.js、
requirement-create.js、home.js、industry_templates.py、da_repo.py、da_schema.sql），
加一个行业要改一圈，还必然出现「首页能选、进工作台丢失、数据库拒收」这类静默降级。
本模块把清单收成一处，其它模块一律派生（见 docs/specs/global-industry-registry-and-packaging.md）。

能被仓库根的 `cpq_*.py` 与 `tech_app/backend/**` 同时导入：tech_app 侧已有把仓库根
加进 `sys.path` 的既有做法（见 backend/services/llm_settings.py）。

历史键（flexible）保留可读但不可选：早期「AI 生成字段」草稿仍要能打开。
"""
from __future__ import annotations

# 可选行业的唯一清单：顺序即页面展示顺序。
INDUSTRY_KEYS: tuple[str, ...] = ("semiconductor", "battery", "appliance", "packaging")

# 已下线但必须仍可读的历史键。
LEGACY_INDUSTRY_KEYS: tuple[str, ...] = ("flexible",)

# 缺失 / 非法 / 未知一律落这个行业，不静默改成别的业务行业。
DEFAULT_INDUSTRY: str = "semiconductor"


def _profile(key: str, label: str, *, cost_profile: str, pricing_profile: str) -> dict:
    return {
        "key": key,
        "label": label,
        "enabled": True,
        "quote_template": key,
        "process_template": key,
        "knowledge_scope": key,
        "cost_profile": cost_profile,
        "pricing_profile": pricing_profile,
    }


INDUSTRIES: dict[str, dict] = {
    "semiconductor": _profile(
        "semiconductor", "半导体",
        cost_profile="generic_v1", pricing_profile="generic_margin_v1"),
    "battery": _profile(
        "battery", "电池",
        cost_profile="generic_v1", pricing_profile="generic_margin_v1"),
    "appliance": _profile(
        "appliance", "电器",
        cost_profile="generic_v1", pricing_profile="generic_margin_v1"),
    # 包装：本批只落路由标识；packaging_v1 / packaging_margin_v1 的计算分别由
    # 第 7 / 第 8 批实现。
    "packaging": _profile(
        "packaging", "包装",
        cost_profile="packaging_v1", pricing_profile="packaging_margin_v1"),
}

# 历史键的展示信息：不在可选项里，但标签与 profile 仍要能给出来。
LEGACY_LABELS: dict[str, str] = {"flexible": "灵活"}


def industry_keys() -> tuple[str, ...]:
    """可选行业的唯一清单（不含历史键）。"""
    return INDUSTRY_KEYS


def all_industries() -> list[dict]:
    """按 INDUSTRY_KEYS 顺序返回可选行业的 profile 列表。"""
    return [INDUSTRIES[key] for key in INDUSTRY_KEYS if key in INDUSTRIES]


def _text(value: object) -> str:
    return str(value or "").strip().lower()


def is_supported(value: object) -> bool:
    """是否是可选项里的行业（不含历史键）。"""
    return _text(value) in INDUSTRY_KEYS


def is_legacy(value: object) -> bool:
    """是否是已下线但必须仍可读的历史键。"""
    return _text(value) in LEGACY_INDUSTRY_KEYS


def is_known(value: object) -> bool:
    """可选 + 历史。"""
    return is_supported(value) or is_legacy(value)


def normalize(value: object) -> str:
    """归一化行业值：去空白 / 小写；可选或历史键原样返回；其它一律默认行业。"""
    text = _text(value)
    return text if (text in INDUSTRY_KEYS or text in LEGACY_INDUSTRY_KEYS) else DEFAULT_INDUSTRY


def label_of(value: object) -> str:
    """中文标签；未知值给默认行业的标签。"""
    key = normalize(value)
    if key in INDUSTRIES:
        return str(INDUSTRIES[key]["label"])
    return LEGACY_LABELS.get(key, str(INDUSTRIES[DEFAULT_INDUSTRY]["label"]))


def profile_of(value: object) -> dict:
    """归一化后的 profile dict（历史键给同形状的只读描述）。"""
    key = normalize(value)
    if key in INDUSTRIES:
        return INDUSTRIES[key]
    if key in LEGACY_INDUSTRY_KEYS:
        return _profile(key, LEGACY_LABELS.get(key, key),
                        cost_profile="generic_v1", pricing_profile="generic_margin_v1")
    return INDUSTRIES[DEFAULT_INDUSTRY]

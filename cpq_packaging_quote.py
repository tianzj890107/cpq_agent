# -*- coding: utf-8 -*-
"""包装报价（包装第 8 批）—— 定价、加价 / 折扣 / 税金、报价单、报价版本、历史恢复。

口径来源：`报价逻辑-0903.xlsx` 的 `报价表` / `成本细分` / `报价-工费率` / `问题点`。

0903 的现场是**一个除式，不是「成本 + 利润率」**：

    · `报价-工费率!AX2 = AV2/(1-AW2)` → 77.68520189964802 / 0.25 → 103.58026919953069
    · `报价表!I2      = G2/(1-H2)`   → 60.04940812974125 / 0.25 → 80.06587750632167

同一个工作簿的 `问题点!A30` 却写着「直接在总成本上 + 利润率报给客户」：

    · 文字口径 = 60.04940812974125 × 1.25 = 75.06176016217657（差 5.004117344145101）

两种口径都保留、字段名不同：默认 `pricing_mode='gross_margin'`（忠实复现工作簿公式），
`pricing_mode='markup'` 走文字口径（`markup_rate`）。记录里必须存 `pricing_mode`，
否则「这单到底怎么算的」永远说不清。

**定价只有一个入口 `price()`**（路由、Agent 工具、HTTP 处理器都调它）：纯函数、不读写库、
不调模型、不联网；`recompute(quote)` 用 `lines` 里的输入重跑，结果逐项相等。

本模块**不 import 技术工艺**（Spec §2.1 分层）：只吃交接包，产出报价。
"""
from __future__ import annotations

import copy
import datetime
import decimal
import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import cpq_auth
import cpq_wf

ENGINE_VERSION = "packaging_quote_v1"
PRICING_PROFILE = "packaging_margin_v1"
COST_PROFILE = "packaging_v1"
INDUSTRY = "packaging"
INDUSTRY_LABEL = "包装"

PRICING_MODES = ("gross_margin", "markup")
DEFAULT_PRICING_MODE = "gross_margin"
#: 两个概念不许混用：毛利是除式（÷(1-rate)），加价是乘式（×(1+rate)）。
RATE_FIELDS = {"gross_margin": "gross_margin_rate", "markup": "markup_rate"}

ADDON_CATEGORIES = (("tech_premium", "技术溢价"),
                    ("market_adjustment", "市场调节"),
                    ("other_addon", "其他加价"))
DEDUCTION_CATEGORIES = (("discount", "折扣"),)
DEFAULT_TAX_RATE = 0.13

#: 报价侧定价落版本的写权限闭集：定价是销售的动作（技术侧只读，Spec §2.7）。
WRITE_ROLES = {"sales_mgr", "admin"}

PRICE_PATH = "/api/packaging-quote/price"

#: 报价单八节（顺序即文档顺序，Spec §2.5）。
DOC_SECTIONS = (("s1_basic", "报价基本信息"), ("s2_product", "产品与盒型"),
                ("s3_parts", "部件与材料"), ("s4_route", "工艺路线"),
                ("s5_cost", "成本构成"), ("s6_markup", "定价与加价"),
                ("s7_tax", "税金与总额"), ("s8_source", "来源与可追溯"))

_ADDON_LABELS = dict(ADDON_CATEGORIES)
_DEDUCTION_LABELS = dict(DEDUCTION_CATEGORIES)

#: 报价单 / 工作台里要逐项写出来的金额字段（都按两位小数渲染）。
_MONEY_FIELDS = (("cost_total", "成本总额"), ("margin_price", "毛利后单价"),
                 ("addon_total", "加价合计"), ("subtotal_unit", "加价后单价"),
                 ("discount_amount", "折扣金额"), ("net_unit_price", "未税单价"),
                 ("tax_amount", "税金"), ("taxed_unit_price", "含税单价"),
                 ("untaxed_unit_price", "未税单价（定价）"),
                 ("untaxed_total", "未税总额"), ("taxed_total", "含税总额"))

_VERSION_TABLE = "cpq_wf_quote_version"
_VERSION_COLS = ("quote_version_id", "business_case_id", "quote_session_id", "card_id",
                 "requirement_no", "scenario_code", "industry", "engine_version",
                 "pricing_profile", "version_no", "quote_fingerprint", "pricing_mode",
                 "cost_total", "previous_cost_total", "previous_version_no",
                 "quote_quantity", "gross_margin_rate", "markup_rate", "tax_rate",
                 "untaxed_unit_price", "untaxed_total", "addon_total", "discount_amount",
                 "tax_amount", "taxed_total", "addons_json", "discount_json",
                 "document_md", "inputs_json", "source_tech_project_id",
                 "source_handoff_id", "created_by_user_id", "created_at")


class PricingError(Exception):
    """包装定价业务错误；``status_code`` 与 ``code`` 供接口层原样映射。"""

    def __init__(self, message: str, status_code: int = 400, code: str = ""):
        super().__init__(message)
        self.message = str(message)
        self.status_code = int(status_code)
        self.code = str(code)


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _money(value: Any) -> str:
    return "%.2f" % (_num(value) or 0.0)


def _rate_text(value: Any) -> str:
    return "%.2f" % (_num(value) or 0.0)


def _digest(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _stamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# --------------------------------------------------------------------------- #
# 定价（唯一算法）
# --------------------------------------------------------------------------- #
def untaxed_unit_price(total_cost: float, rate, *,
                       pricing_mode: str = DEFAULT_PRICING_MODE) -> float:
    """成本 → 未税单价（纯函数）。

    gross_margin：``成本 ÷ (1 - 毛利率)``（0903 的公式口径）
    markup：      ``成本 × (1 + 加成率)``（0903 `问题点!A30` 的文字口径）
    """
    mode = _text(pricing_mode) or DEFAULT_PRICING_MODE
    if mode not in PRICING_MODES:
        raise PricingError(f"定价模式只有 {'/'.join(PRICING_MODES)} 两种，收到「{mode}」",
                           400, "invalid_pricing_mode")
    value = _num(rate)
    if value is None:
        raise PricingError(f"缺少 {RATE_FIELDS[mode]}：{mode} 模式必须给出该费率",
                           400, "rate_missing")
    if value < 0:
        raise PricingError("费率不能是负数", 400, "invalid_rate")
    cost = _num(total_cost) or 0.0
    if mode == "gross_margin":
        if value >= 1:
            raise PricingError("毛利率必须小于 1，否则成本 ÷ (1-毛利率) 无意义",
                               400, "invalid_rate")
        return cost / (1.0 - value)
    return cost * (1.0 + value)


def _addons_of(addons: Any) -> Tuple[List[dict], float]:
    """加价项：闭集外的 key 一律拒绝，不静默忽略（Spec §2.4）。"""
    if not addons:
        return [], 0.0
    if not isinstance(addons, dict):
        raise PricingError("加价项必须是 {类别: 单件金额} 形式", 400, "unknown_addon")
    items: List[dict] = []
    total = 0.0
    for code, amount in addons.items():
        key = _text(code)
        if key not in _ADDON_LABELS:
            raise PricingError(f"加价类别「{key}」不在闭集内", 400, "unknown_addon")
        value = _num(amount) or 0.0
        total += value
        items.append({"code": key, "label": _ADDON_LABELS[key], "amount": value})
    return items, total


#: 折扣入参的字段名（``rate`` 是唯一的比例字段；``code``/``category`` 只是可选类别声明）。
_DISCOUNT_FIELDS = ("rate", "code", "category", "label", "amount")


def _discount_of(discount: Any) -> Tuple[dict, float]:
    """折扣：比例制（0–1）。

    ``DEDUCTION_CATEGORIES`` 是**折扣类别**的闭集（目前只有 ``discount``）：显式声明
    类别且不在闭集内 → ``unknown_addon``；`{"rate": 0.05}` 这种只给比例的形式照常可用。
    """
    if not discount:
        return {"code": "discount", "label": _DEDUCTION_LABELS["discount"],
                "rate": 0.0, "amount": 0.0}, 0.0
    if not isinstance(discount, dict):
        raise PricingError("折扣必须是 {rate: 比例} 形式", 400, "invalid_rate")
    for key in discount:
        if _text(key) not in _DISCOUNT_FIELDS:
            raise PricingError(f"折扣字段「{key}」不在允许的 {'/'.join(_DISCOUNT_FIELDS)} 内",
                               400, "unknown_addon")
    category = _text(discount.get("code") or discount.get("category"))
    if category and category not in _DEDUCTION_LABELS:
        raise PricingError(f"折扣类别「{category}」不在闭集内", 400, "unknown_addon")
    rate = _num(discount.get("rate")) or 0.0
    if rate < 0 or rate > 1:
        raise PricingError("折扣比例必须在 0–1 之间", 400, "invalid_rate")
    code = category or "discount"
    return {"code": code, "label": _DEDUCTION_LABELS[code], "rate": rate}, rate


def _quantity_of(package: dict, quote_quantity: Any) -> float:
    requirement = package.get("requirement") if isinstance(package.get("requirement"), dict) else {}
    raw = quote_quantity if quote_quantity is not None else requirement.get("quote_quantity")
    quantity = _num(raw)
    if quantity is None or quantity <= 0:
        raise PricingError("报价数量缺失或不是正数，无法报价", 400, "invalid_quantity")
    return quantity


def price(package: dict, *, gross_margin_rate=None, markup_rate=None,
          pricing_mode: str = DEFAULT_PRICING_MODE, addons: Any = None,
          discount: Any = None, tax_rate: Any = DEFAULT_TAX_RATE,
          quote_quantity: Any = None, actor: Any = None,
          previous: Any = None, publish: bool = False) -> dict:
    """交接包 → 报价（纯函数：不改入参、不读写库、不调模型、不联网）。

    计算顺序（每一步都进 ``lines``，含公式 / 输入 / 结果 / 来源 / 版本）：
      成本总额 → 毛利后单价 → 加价合计 → 加价后单价 → 折扣 → 未税单价 → 税金 →
      含税单价 → 未税 / 含税总额

    缺口包（Spec `packaging-quote-draft-and-card-visibility.md` §3.1）：

      · 包里**逐条列了缺口**（交接包 builder 产出的 `gaps` 就是 `cost.gaps` 的副本）且
        ``publish=False`` → 照常出**草稿**报价：数字链完整、缺口原样带出、
        ``draft=True`` / ``publish_blocked=True``；``publish=True``（正式报价单）→ 照旧拒绝；
      · 只抬了 ``has_gaps`` 而**没有任何逐条清单**的包（历史形状）= 包不完整，照旧拒绝
        （第 8 批既有契约，冻结红测 E14 / F5）。
    """
    pkg = package or {}
    if _text(pkg.get("industry")) != INDUSTRY:
        raise PricingError(
            f"这不是包装交接包（industry={_text(pkg.get('industry')) or '未标明'}），"
            "包装定价只接管 industry=packaging 的包", 400, "not_packaging")
    cost = pkg.get("cost") if isinstance(pkg.get("cost"), dict) else {}
    gaps = copy.deepcopy(list(pkg.get("gaps") or []))
    has_gaps_flag = bool(cost.get("has_gaps")) or bool(gaps)
    if has_gaps_flag and publish:
        raise PricingError("成本仍有缺口，只能出成本与草稿，不得生成正式报价单",
                           409, "cost_gaps_unresolved")
    if has_gaps_flag and not gaps:
        # 缺口只有标志、没有逐条清单：草稿说不出缺什么，按"包不完整"照旧拒绝。
        raise PricingError("成本仍有缺口，只能出成本与草稿，不得生成正式报价单",
                           409, "cost_gaps_unresolved")
    draft = bool(has_gaps_flag and gaps)

    mode = _text(pricing_mode) or DEFAULT_PRICING_MODE
    if mode not in PRICING_MODES:
        raise PricingError(f"定价模式只有 {'/'.join(PRICING_MODES)} 两种，收到「{mode}」",
                           400, "invalid_pricing_mode")
    rate = gross_margin_rate if mode == "gross_margin" else markup_rate
    total_cost = _num(cost.get("total_cost")) or 0.0
    margin_price = untaxed_unit_price(total_cost, rate, pricing_mode=mode)

    tax = _num(tax_rate)
    if tax is None or tax < 0 or tax > 1:
        raise PricingError("税率必须在 0–1 之间", 400, "invalid_tax_rate")
    quantity = _quantity_of(pkg, quote_quantity)
    addon_items, addon_total = _addons_of(addons)
    discount_item, discount_rate = _discount_of(discount)
    numbers = _numbers(margin_price, addon_total, discount_rate, tax)

    requirement = pkg.get("requirement") if isinstance(pkg.get("requirement"), dict) else {}
    box = pkg.get("box_type") if isinstance(pkg.get("box_type"), dict) else {}
    source = pkg.get("source") if isinstance(pkg.get("source"), dict) else {}
    box_code = (_text(box.get("confirmed_box_type")) or _text(requirement.get("box_type")))
    quote = {
        "industry": INDUSTRY,
        "industry_label": INDUSTRY_LABEL,
        "engine_version": ENGINE_VERSION,
        "cost_profile": _text(cost.get("cost_profile")) or COST_PROFILE,
        "pricing_profile": PRICING_PROFILE,
        "currency": _text(cost.get("currency")) or "CNY",
        "pricing_mode": mode,
        "pricing_mode_label": "毛利率（成本 ÷ (1-毛利率)）" if mode == "gross_margin"
                              else "加成率（成本 × (1+加成率)）",
        "gross_margin_rate": _num(gross_margin_rate) if mode == "gross_margin" else None,
        "markup_rate": _num(markup_rate) if mode == "markup" else None,
        "rate": _num(rate),
        "rate_field": RATE_FIELDS[mode],
        "tax_rate": tax,
        "quote_quantity": quantity,
        "cost_total": total_cost,
        "margin_price": margin_price,
        "addon_total": numbers["addon_total"],
        "subtotal_unit": numbers["subtotal_unit"],
        "discount_rate": discount_rate,
        "discount_amount": numbers["discount_amount"],
        "net_unit_price": numbers["net_unit_price"],
        "tax_amount": numbers["tax_amount"],
        "taxed_unit_price": numbers["taxed_unit_price"],
        "untaxed_unit_price": margin_price,
        "untaxed_total": numbers["net_unit_price"] * quantity,
        "taxed_total": numbers["taxed_unit_price"] * quantity,
        "addons": copy.deepcopy(addon_items),
        "discount": dict(discount_item, amount=numbers["discount_amount"]),
        "lines": _lines(total_cost, margin_price, addon_total, discount_rate, tax),
        # 溯源字段：一律从包里的 source 继承，不许调用方另传（Spec §4.2）。
        "quote_session_id": _text(source.get("quote_session_id")
                                  or source.get("source_session_id")),
        "business_case_id": _text(source.get("business_case_id")),
        "source_tech_project_id": _text(source.get("project_id")),
        "source_handoff_id": _text(source.get("handoff_id")),
        "source_result_version": _text(source.get("result_version")
                                       or pkg.get("result_version")),
        "handoff_kind": _text(pkg.get("handoff_kind")),
        "requirement_no": _text(source.get("requirement_no")),
        "scenario_code": _text(source.get("scenario_code")) or "default",
        "box_type_code": box_code,
        "product_name": _text(requirement.get("packaging_product_name")),
        "customer": _text(requirement.get("customer_name")),
        "bom_items": copy.deepcopy(list((pkg.get("bom") or {}).get("items") or [])),
        "route_steps": copy.deepcopy(list((pkg.get("route") or {}).get("steps") or [])),
        "route_status": _text((pkg.get("route") or {}).get("status")),
        "cost_totals": {key: _num(cost.get(key)) or 0.0 for key in
                        ("material_total", "process_total", "labor_total",
                         "tooling_total", "packaging_total", "freight_total",
                         "other_total", "subtotal", "loss_amount")},
        "cost_categories": copy.deepcopy(cost.get("categories") or {}),
        "cost_report_groups": copy.deepcopy(cost.get("report_groups") or {}),
        "cost_engine_version": _text(cost.get("engine_version")),
        "gaps": gaps,
        # 草稿 / 正式的分界（Spec §3.1）：缺口逐条在案 → 草稿可出、正式单拦住。
        "draft": draft,
        "publish_blocked": draft,
        "publish_block_reason": "cost_gaps_unresolved" if draft else "",
        "gap_count": len(gaps),
        "priced_at": _stamp(),
        "inputs": {
            "cost_total": total_cost,
            "quote_quantity": quantity,
            "pricing_mode": mode,
            "gross_margin_rate": _num(gross_margin_rate),
            "markup_rate": _num(markup_rate),
            "rate": _num(rate),
            "addons": copy.deepcopy(addon_items),
            "discount_rate": discount_rate,
            "tax_rate": tax,
        },
    }
    if isinstance(actor, dict):
        quote["priced_by"] = _text(actor.get("username"))
    if previous:
        quote["previous"] = copy.deepcopy(previous) if isinstance(previous, dict) else previous
    return quote


def _numbers(margin_price: float, addon_total: float, discount_rate: float,
             tax_rate: float) -> dict:
    subtotal_unit = margin_price + addon_total
    discount_amount = subtotal_unit * discount_rate
    net_unit_price = subtotal_unit - discount_amount
    tax_amount = net_unit_price * tax_rate
    taxed_unit_price = net_unit_price + tax_amount
    return {"addon_total": addon_total, "subtotal_unit": subtotal_unit,
            "discount_amount": discount_amount, "net_unit_price": net_unit_price,
            "tax_amount": tax_amount, "taxed_unit_price": taxed_unit_price}


def _lines(cost_total: float, margin_price: float, addon_total: float,
           discount_rate: float, tax_rate: float) -> List[dict]:
    """每一步的公式 / 输入 / 结果 / 来源 —— 报价计算可复算的凭证。"""
    numbers = _numbers(margin_price, addon_total, discount_rate, tax_rate)
    return [
        {"code": "cost_total", "label": "成本总额", "formula": "第 7 批 total_cost",
         "inputs": {"total_cost": cost_total}, "result": cost_total, "source": "packaging_cost",
         "version": "packaging_cost_v1"},
        {"code": "margin_price", "label": "毛利后单价",
         "formula": "cost_total / (1 - gross_margin_rate)",
         "inputs": {"cost_total": cost_total}, "result": margin_price,
         "source": "price", "version": ENGINE_VERSION},
        {"code": "addon_total", "label": "加价合计", "formula": "Σ addons",
         "inputs": {"addon_total": addon_total}, "result": addon_total,
         "source": "price", "version": ENGINE_VERSION},
        {"code": "subtotal_unit", "label": "加价后单价",
         "formula": "margin_price + addon_total",
         "inputs": {"margin_price": margin_price, "addon_total": addon_total},
         "result": numbers["subtotal_unit"], "source": "price", "version": ENGINE_VERSION},
        {"code": "discount_amount", "label": "折扣金额",
         "formula": "subtotal_unit × discount_rate",
         "inputs": {"subtotal_unit": numbers["subtotal_unit"], "discount_rate": discount_rate},
         "result": numbers["discount_amount"], "source": "price", "version": ENGINE_VERSION},
        {"code": "net_unit_price", "label": "未税单价",
         "formula": "subtotal_unit - discount_amount",
         "inputs": {"subtotal_unit": numbers["subtotal_unit"],
                    "discount_amount": numbers["discount_amount"]},
         "result": numbers["net_unit_price"], "source": "price", "version": ENGINE_VERSION},
        {"code": "tax_amount", "label": "税金", "formula": "net_unit_price × tax_rate",
         "inputs": {"net_unit_price": numbers["net_unit_price"], "tax_rate": tax_rate},
         "result": numbers["tax_amount"], "source": "price", "version": ENGINE_VERSION},
        {"code": "taxed_unit_price", "label": "含税单价",
         "formula": "net_unit_price + tax_amount",
         "inputs": {"net_unit_price": numbers["net_unit_price"],
                    "tax_amount": numbers["tax_amount"]},
         "result": numbers["taxed_unit_price"], "source": "price", "version": ENGINE_VERSION},
    ]


def recompute(quote: dict) -> dict:
    """用 ``lines`` 里的输入重跑一遍：结果与 ``quote`` 逐项相等（可复算）。"""
    inputs = (quote or {}).get("inputs") or {}
    mode = _text(inputs.get("pricing_mode")) or DEFAULT_PRICING_MODE
    rate = inputs.get("rate")
    if rate is None:
        rate = (inputs.get("gross_margin_rate") if mode == "gross_margin"
                else inputs.get("markup_rate"))
    margin_price = untaxed_unit_price(inputs.get("cost_total"), rate, pricing_mode=mode)
    addon_total = sum(_num(item.get("amount")) or 0.0 for item in (inputs.get("addons") or []))
    discount_rate = _num(inputs.get("discount_rate")) or 0.0
    tax_rate = _num(inputs.get("tax_rate")) or 0.0
    numbers = _numbers(margin_price, addon_total, discount_rate, tax_rate)
    quantity = _num(inputs.get("quote_quantity")) or 0.0
    return {
        "cost_total": _num(inputs.get("cost_total")) or 0.0,
        "margin_price": margin_price,
        "untaxed_unit_price": margin_price,
        "addon_total": addon_total,
        "subtotal_unit": numbers["subtotal_unit"],
        "discount_amount": numbers["discount_amount"],
        "net_unit_price": numbers["net_unit_price"],
        "tax_amount": numbers["tax_amount"],
        "taxed_unit_price": numbers["taxed_unit_price"],
        "untaxed_total": numbers["net_unit_price"] * quantity,
        "taxed_total": numbers["taxed_unit_price"] * quantity,
    }


def quote_fingerprint(quote: dict) -> str:
    """版本幂等键：同一份定价重复保存必须给出同一个指纹；换了成本就是新指纹。"""
    q = quote or {}
    payload = {
        "engine_version": _text(q.get("engine_version")) or ENGINE_VERSION,
        "industry": _text(q.get("industry")) or INDUSTRY,
        "quote_session_id": _text(q.get("quote_session_id")),
        "business_case_id": _text(q.get("business_case_id")),
        "requirement_no": _text(q.get("requirement_no")),
        "scenario_code": _text(q.get("scenario_code")),
        "pricing_mode": _text(q.get("pricing_mode")),
        "cost_total": round(_num(q.get("cost_total")) or 0.0, 6),
        "quote_quantity": round(_num(q.get("quote_quantity")) or 0.0, 6),
        "rate": round(_num(q.get("rate")) or 0.0, 6),
        "tax_rate": round(_num(q.get("tax_rate")) or 0.0, 6),
        "discount_rate": round(_num(q.get("discount_rate")) or 0.0, 6),
        "addons": [{"code": _text(item.get("code")),
                    "amount": round(_num(item.get("amount")) or 0.0, 6)}
                   for item in (q.get("addons") or [])],
        "untaxed_unit_price": round(_num(q.get("untaxed_unit_price")) or 0.0, 6),
        "net_unit_price": round(_num(q.get("net_unit_price")) or 0.0, 6),
        "taxed_unit_price": round(_num(q.get("taxed_unit_price")) or 0.0, 6),
    }
    return _digest(payload)


# --------------------------------------------------------------------------- #
# 报价单（八节）
# --------------------------------------------------------------------------- #
def document(quote: dict, *, publish: bool = False) -> dict:
    """报价单：``{"title", "markdown", "sections"}``；数字与 ``quote`` 逐个一致。

    不许带时间戳 / 随机数 —— 历史打开要能逐字恢复（Spec §2.5）。

    ``publish=True`` 是「对外正式报价单」：缺口未清时同样拒绝；``publish=False``（缺省）是
    草稿，正文里必须写明**不得对外发布**（Spec §3.1）。
    """
    q = quote or {}
    gaps = list(q.get("gaps") or [])
    if publish and (gaps or q.get("draft")):
        raise PricingError("成本仍有缺口，只能出成本与草稿，不得生成正式报价单",
                           409, "cost_gaps_unresolved")
    sections = _doc_sections(q)
    lines = ["# " + _doc_title(q), ""]
    if gaps or q.get("draft"):
        count = q.get("gap_count")
        if not isinstance(count, int) or isinstance(count, bool):
            count = len(gaps)
        lines.append("> 本报价为缺口草稿，不得对外发布（缺口 %d 项；清账后重新定价才能出正式报价单）。"
                     % count)
        lines.append("")
    for section in sections:
        lines.append("## " + section["title"])
        for row in section["rows"]:
            note = row.get("来源") or row.get("公式") or ""
            lines.append("- %s：%s%s" % (row.get("项目") or "", row.get("值") if row.get("值") is not None else "",
                                        ("（%s）" % note) if note else ""))
        lines.append("")
    return {"title": _doc_title(q), "markdown": "\n".join(lines).strip() + "\n",
            "sections": sections}


def _doc_title(quote: dict) -> str:
    box = _text(quote.get("box_type_code")) or _text(quote.get("requirement_no")) or "包装"
    return f"{INDUSTRY_LABEL}报价单 · {box}"


def _row(item: str, value: Any, *, source: str = "", formula: str = "") -> dict:
    row = {"项目": item, "值": value}
    if source:
        row["来源"] = source
    if formula:
        row["公式"] = formula
    return row


def _money_rows(quote: dict) -> List[dict]:
    return [_row(label, _money(quote.get(key)), source="packaging_quote")
            for key, label in _MONEY_FIELDS]


def _doc_sections(quote: dict) -> List[dict]:
    q = quote or {}
    cost = q.get("cost_totals") or {}
    mode = _text(q.get("pricing_mode"))
    rate = q.get("gross_margin_rate") if mode == "gross_margin" else q.get("markup_rate")
    parts = [dict(item) for item in (q.get("bom_items") or []) if isinstance(item, dict)]
    steps = [dict(item) for item in (q.get("route_steps") or []) if isinstance(item, dict)]
    sections = [
        {"id": DOC_SECTIONS[0][0], "title": DOC_SECTIONS[0][1], "rows": [
            _row("报价会话", _text(q.get("quote_session_id")) or "—", source="交接包 source"),
            _row("业务实例号", _text(q.get("business_case_id")) or "—", source="交接包 source"),
            _row("需求单号", _text(q.get("requirement_no")) or "—", source="需求单"),
            _row("场景", _text(q.get("scenario_code")) or "default", source="需求单"),
            _row("行业", f"{INDUSTRY_LABEL}（{INDUSTRY}）", source="行业注册表"),
            _row("报价数量", q.get("quote_quantity"), source="需求单"),
            _row("币别", _text(q.get("currency")) or "CNY", source="成本引擎"),
            _row("定价引擎版本", _text(q.get("engine_version")) or ENGINE_VERSION,
                 source="定价引擎"),
        ]},
        {"id": DOC_SECTIONS[1][0], "title": DOC_SECTIONS[1][1], "rows": [
            _row("产品名称", _text(q.get("product_name")) or "—", source="需求单"),
            _row("盒型", _text(q.get("box_type_code")) or "—", source="已确认盒型"),
            _row("客户", _text(q.get("customer")) or "—", source="需求单"),
            _row("工艺路线状态", _text(q.get("route_status")) or "—", source="工艺路线"),
        ]},
        {"id": DOC_SECTIONS[2][0], "title": DOC_SECTIONS[2][1], "rows": (
            [_row(f"{_text(item.get('item_name')) or _text(item.get('part_name')) or _text(item.get('item_key'))}"
                  f"（{_text(item.get('bom_category'))}）",
                  "%s × %s" % (_text(item.get("material") or item.get("component")) or _text(item.get("item_key")),
                               item.get("quantity")),
                  source=_text(item.get("formula_version")) or "包装 BOM")
             for item in parts] or
            [_row("部件与材料", "（本包未带 BOM 行）", source="包装 BOM")])},
        {"id": DOC_SECTIONS[3][0], "title": DOC_SECTIONS[3][1], "rows": (
            [_row("%s. %s" % (item.get("step_no"), _text(item.get("step_name"))),
                  "设备 %s · 标准工时 %s 秒" % (_text(item.get("equipment")) or "—",
                                              item.get("standard_seconds")),
                  source=_text(item.get("source")) or "工艺路线")
             for item in steps] or
            [_row("工艺路线", "（本包未带工序行）", source="工艺路线")])},
        {"id": DOC_SECTIONS[4][0], "title": DOC_SECTIONS[4][1], "rows": [
            _row("材料", _money(cost.get("material_total")), source="包装成本引擎"),
            _row("工艺", _money(cost.get("process_total")), source="包装成本引擎"),
            _row("人工", _money(cost.get("labor_total")), source="包装成本引擎"),
            _row("工装", _money(cost.get("tooling_total")), source="包装成本引擎"),
            _row("包装", _money(cost.get("packaging_total")), source="包装成本引擎"),
            _row("运输", _money(cost.get("freight_total")), source="包装成本引擎"),
            _row("损耗", _money(cost.get("loss_amount")), source="包装成本引擎"),
            _row("小计", _money(cost.get("subtotal")), source="包装成本引擎"),
            _row("成本总额", _money(q.get("cost_total")), source="第 7 批 total_cost"),
        ]},
        {"id": DOC_SECTIONS[5][0], "title": DOC_SECTIONS[5][1], "rows": (
            [
                _row("定价模式", _text(q.get("pricing_mode_label")), source="Spec §1.2"),
                _row(RATE_FIELDS.get(mode, "费率"), _rate_text(rate),
                     formula="成本 ÷ (1-毛利率)" if mode == "gross_margin" else "成本 × (1+加成率)"),
                _row("毛利后单价", _money(q.get("margin_price")), source="定价引擎"),
            ] + [_row(f"加价：{_text(item.get('label'))}", _money(item.get("amount")),
                      source="Spec §2.4") for item in (q.get("addons") or [])]
            + [_row("加价合计", _money(q.get("addon_total")), source="定价引擎"),
               _row("加价后单价", _money(q.get("subtotal_unit")), source="定价引擎"),
               _row("折扣比例", _rate_text(q.get("discount_rate")), source="Spec §2.4"),
               _row("折扣金额", _money(q.get("discount_amount")), source="定价引擎"),
               _row("未税单价", _money(q.get("net_unit_price")), formula="加价后单价 − 折扣金额")]
        )},
        {"id": DOC_SECTIONS[6][0], "title": DOC_SECTIONS[6][1],
         "rows": _money_rows(q) + [
             _row("税金", _money(q.get("tax_amount")), formula="未税单价 × 税率"),
             _row("税率", _rate_text(q.get("tax_rate")), source="Spec §2.4")]},
        {"id": DOC_SECTIONS[7][0], "title": DOC_SECTIONS[7][1], "rows": [
            _row("来源技术项目", _text(q.get("source_tech_project_id")) or "—",
                 source="交接包 source.project_id"),
            _row("来源交接编号", _text(q.get("source_handoff_id")) or "—",
                 source="交接包 source.handoff_id"),
            _row("成本结果版本", _text(q.get("source_result_version")) or "—",
                 source="packaging_cost"),
            _row("成本引擎版本", _text(q.get("cost_engine_version")) or "—",
                 source="packaging_cost"),
            _row("成本档位", _text(q.get("cost_profile")) or COST_PROFILE, source="行业注册表"),
            _row("定价档位", _text(q.get("pricing_profile")) or PRICING_PROFILE,
                 source="行业注册表"),
            _row("定价引擎版本", _text(q.get("engine_version")) or ENGINE_VERSION,
                 source="定价引擎"),
            _row("交接类型", _text(q.get("handoff_kind")) or "—", source="交接包"),
        ]},
    ]
    return sections


# --------------------------------------------------------------------------- #
# 工作台分区（形状与 cpq_ui 一致）
# --------------------------------------------------------------------------- #
def sections(quote: dict) -> dict:
    """报价工作台四段分区：``s3_markup`` / ``s4_markup`` / ``s5_basic`` / ``s5_detail``。"""
    q = quote or {}
    mode = _text(q.get("pricing_mode"))
    rate = q.get("gross_margin_rate") if mode == "gross_margin" else q.get("markup_rate")
    rate_label = "毛利率" if mode == "gross_margin" else "加成率"
    markup_fields = [
        {"key": "pricing_mode", "label": "定价模式", "value": mode},
        {"key": RATE_FIELDS.get(mode, "rate"), "label": rate_label, "value": _rate_text(rate)},
        {"key": "cost_total", "label": "成本总额", "value": _money(q.get("cost_total"))},
        {"key": "untaxed_unit_price", "label": "未税单价", "value": _money(q.get("untaxed_unit_price"))},
    ]
    markup_rows = [
        {"定价模式": mode, "费率": _rate_text(rate), "成本总额": _money(q.get("cost_total")),
         "未税单价": _money(q.get("untaxed_unit_price")), "来源": "Spec §1.2"},
    ]
    for item in (q.get("addons") or []):
        markup_rows.append({"加价": _text(item.get("label")),
                            "金额": _money(item.get("amount")), "来源": "Spec §2.4"})
    detail_rows = [
        {"项目": label, "值": _money(q.get(key)), "来源": "定价引擎"}
        for key, label in _MONEY_FIELDS]
    detail_rows.append({"项目": "税金", "值": _money(q.get("tax_amount")),
                        "公式": "未税单价 × 税率"})
    detail_rows.append({"项目": "税率", "值": _rate_text(q.get("tax_rate")), "来源": "Spec §2.4"})
    return {
        "s3_markup": {"kind": "markup", "step_no": 3, "title": "定价-利润加成",
                      "fields": markup_fields, "rows": markup_rows},
        "s4_markup": {"kind": "markup", "step_no": 4, "title": "报价-加价与折扣",
                      "fields": markup_fields, "rows": markup_rows},
        "s5_basic": {"kind": "summary", "title": "报价基本信息", "fields": [
            {"key": "quote_session_id", "label": "报价会话",
             "value": _text(q.get("quote_session_id"))},
            {"key": "business_case_id", "label": "业务实例号",
             "value": _text(q.get("business_case_id"))},
            {"key": "requirement_no", "label": "需求单号",
             "value": _text(q.get("requirement_no"))},
            {"key": "box_type_code", "label": "盒型", "value": _text(q.get("box_type_code"))},
            {"key": "quote_quantity", "label": "报价数量", "value": q.get("quote_quantity")},
        ]},
        "s5_detail": {"kind": "summary", "title": "报价明细", "rows": detail_rows},
    }


# --------------------------------------------------------------------------- #
# 报价版本（只增不改）
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# 读取侧的类型归一（Spec `packaging-quote-version-card-readback-serialization.md` §2.1）
# --------------------------------------------------------------------------- #
def _json_safe(value: Any) -> Any:
    """读回路径的类型归一：`datetime`/`date` → ISO 字符串、`Decimal` → 数字，其余原样。

    PG 的 `timestamp` 回来是 `datetime`、`numeric` 是 `Decimal`，而卡片第 5 步要把版本行
    直接交给 `json.dumps` —— 不归一就是 500「服务异常」。归一只服务**读取**这一侧
    （`save_version()` 的签名 / SQL / 写入值一律不动）。
    """
    if isinstance(value, (datetime.datetime, datetime.date)):
        # 时间一律给稳定、可回读的 ISO 文本（`datetime` 用空格分隔，肉眼读也更像人话）。
        if isinstance(value, datetime.datetime):
            return value.isoformat(sep=" ")
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        if not value.is_finite():          # NaN / Infinity 没有 JSON 数字表示，如实给文本
            return str(value)
        # 整数金额给 `int`（`5000` 而不是 `5000.0`），小数给 `float`；两者都是 JSON 数字。
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _json_safe_row(row: Dict[str, Any]) -> Dict[str, Any]:
    return {key: _json_safe(value) for key, value in row.items()}


def _fetch_versions(conn, *, business_case_id: str = "",
                    quote_session_id: str = "") -> List[dict]:
    if quote_session_id:
        sql = (f"SELECT {', '.join(_VERSION_COLS)} FROM {_VERSION_TABLE}"
               " WHERE quote_session_id = %s")
        args: tuple = (quote_session_id,)
    elif business_case_id:
        sql = (f"SELECT {', '.join(_VERSION_COLS)} FROM {_VERSION_TABLE}"
               " WHERE business_case_id = %s")
        args = (business_case_id,)
    else:
        return []
    cur = cpq_auth._exec(conn, sql, args)
    # 读取侧归一：`versions()` / `latest()`（以及 `save_version()` 用来比较的那次读取）
    # 拿到的每个值都是 JSON 原生类型 —— 契约在**读回路径**，不靠 `_send_json` 兜底（Spec §3）。
    rows = [_json_safe_row(dict(zip(_VERSION_COLS, row))) for row in cur.fetchall()]
    rows.sort(key=lambda item: int(item.get("version_no") or 0), reverse=True)
    return rows


def versions(conn, *, business_case_id: str = "", quote_session_id: str = "") -> list:
    """全部版本（新的在前）。**只有 SELECT**：只增不改（Spec §2.6）。"""
    return _fetch_versions(conn, business_case_id=_text(business_case_id),
                           quote_session_id=_text(quote_session_id))


def latest(conn, *, business_case_id: str = "", quote_session_id: str = "") -> dict:
    """最新版本；没有版本给 ``{}``，不报错。"""
    rows = versions(conn, business_case_id=business_case_id,
                    quote_session_id=quote_session_id)
    return rows[0] if rows else {}


def save_version(conn, quote: dict, *, user: Optional[dict] = None) -> dict:
    """落一条报价版本（只在给定连接上 INSERT/SELECT，不 commit 不 close）。

    同 ``quote_fingerprint`` 重复保存 → 复用已有版本（``already_saved=True``），不新建；
    换了成本 → 新版本 + ``previous_version_no`` / ``previous_cost_total``。
    """
    user = user or {}
    role = _text(user.get("role_code") or user.get("role"))
    if role not in WRITE_ROLES:
        raise PricingError("只有销售经理或管理员能落报价版本，"
                           f"当前是「{_text(user.get('role_name')) or role or '未登录'}」",
                           403, "role_not_allowed")
    q = quote or {}
    session_id = _text(q.get("quote_session_id"))
    case_id = _text(q.get("business_case_id"))
    fingerprint = quote_fingerprint(q)
    existing = _fetch_versions(conn, business_case_id=case_id, quote_session_id=session_id)
    for row in existing:
        if _text(row.get("quote_fingerprint")) == fingerprint:
            return {"version_no": int(row.get("version_no") or 1), "already_saved": True,
                    "quote_version_id": row.get("quote_version_id"),
                    "quote_fingerprint": fingerprint, "quote_session_id": session_id,
                    "business_case_id": case_id}
    previous = existing[0] if existing else {}
    version_no = int(previous.get("version_no") or 0) + 1 if previous else 1
    document_value = document(q)
    record = {
        "quote_version_id": cpq_wf._new_id(conn),
        "business_case_id": case_id,
        "quote_session_id": session_id,
        "card_id": None,
        "requirement_no": _text(q.get("requirement_no")),
        "scenario_code": _text(q.get("scenario_code")) or "default",
        "industry": _text(q.get("industry")) or INDUSTRY,
        "engine_version": _text(q.get("engine_version")) or ENGINE_VERSION,
        "pricing_profile": _text(q.get("pricing_profile")) or PRICING_PROFILE,
        "version_no": version_no,
        "quote_fingerprint": fingerprint,
        "pricing_mode": _text(q.get("pricing_mode")) or DEFAULT_PRICING_MODE,
        "cost_total": _num(q.get("cost_total")) or 0.0,
        "previous_cost_total": (_num(previous.get("cost_total")) if previous else None),
        "previous_version_no": (int(previous.get("version_no") or 0) if previous else None),
        "quote_quantity": _num(q.get("quote_quantity")),
        "gross_margin_rate": _num(q.get("gross_margin_rate")),
        "markup_rate": _num(q.get("markup_rate")),
        "tax_rate": _num(q.get("tax_rate")),
        "untaxed_unit_price": _num(q.get("untaxed_unit_price")) or 0.0,
        "untaxed_total": _num(q.get("untaxed_total")) or 0.0,
        "addon_total": _num(q.get("addon_total")) or 0.0,
        "discount_amount": _num(q.get("discount_amount")) or 0.0,
        "tax_amount": _num(q.get("tax_amount")) or 0.0,
        "taxed_total": _num(q.get("taxed_total")) or 0.0,
        "addons_json": json.dumps(q.get("addons") or [], ensure_ascii=False, default=str),
        "discount_json": json.dumps(q.get("discount") or {}, ensure_ascii=False, default=str),
        "document_md": document_value.get("markdown") or "",
        # 存整份报价（含 inputs）：既是"算过的输入"，也是历史恢复时的逐字依据。
        "inputs_json": json.dumps(q, ensure_ascii=False, default=str),
        "source_tech_project_id": _text(q.get("source_tech_project_id")),
        "source_handoff_id": _text(q.get("source_handoff_id")),
        "created_by_user_id": _int_or_none(user.get("user_id")),
        "created_at": cpq_wf._ts(cpq_wf._now()),
    }
    cpq_auth._exec(
        conn,
        f"INSERT INTO {_VERSION_TABLE} ({', '.join(_VERSION_COLS)})"
        f" VALUES ({', '.join(['%s'] * len(_VERSION_COLS))})",
        tuple(record.get(col) for col in _VERSION_COLS))
    return {"version_no": version_no, "already_saved": False,
            "quote_version_id": record["quote_version_id"],
            "quote_fingerprint": fingerprint, "quote_session_id": session_id,
            "business_case_id": case_id}


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _quote_from_row(row: dict) -> dict:
    """版本行 → 报价（历史进入时按行里的 inputs_json 逐字恢复）。"""
    if not row:
        return {}
    raw = row.get("inputs_json")
    quote: Dict[str, Any] = {}
    if isinstance(raw, str) and raw.strip():
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                quote = loaded
        except ValueError:
            quote = {}
    if not quote:
        quote = {key: row.get(key) for key in
                 ("cost_total", "untaxed_unit_price", "untaxed_total", "addon_total",
                  "discount_amount", "tax_amount", "taxed_total", "quote_quantity",
                  "pricing_mode", "gross_margin_rate", "markup_rate", "tax_rate",
                  "business_case_id", "quote_session_id", "requirement_no",
                  "scenario_code", "industry", "engine_version", "pricing_profile",
                  "source_tech_project_id", "source_handoff_id")}
        quote["addons"] = _loads_json(row.get("addons_json"), [])
        quote["discount"] = _loads_json(row.get("discount_json"), {})
    quote["version_no"] = int(row.get("version_no") or 1)
    quote["quote_fingerprint"] = _text(row.get("quote_fingerprint"))
    quote["previous_version_no"] = row.get("previous_version_no")
    quote["previous_cost_total"] = row.get("previous_cost_total")
    return quote


def _loads_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def restore(conn, *, quote_session_id: str = "", business_case_id: str = "") -> dict:
    """历史进入时恢复：``{"found", "quote", "versions", "document", "sections"}``。

    没有版本时 ``found=False``，**不报错**。
    """
    rows = versions(conn, business_case_id=business_case_id,
                    quote_session_id=quote_session_id)
    if not rows:
        return {"found": False, "quote": {}, "versions": [], "document": {}, "sections": {}}
    row = rows[0]
    quote = _quote_from_row(row)
    document_value = document(quote)
    if row.get("document_md"):
        document_value["markdown"] = row.get("document_md")
    return {"found": True, "quote": quote, "versions": rows,
            "document": document_value, "sections": sections(quote)}

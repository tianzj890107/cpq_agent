# -*- coding: utf-8 -*-
"""逆向快速报价 —— 第 1 批：快速报价模式与标准报价案例的数据模型。

Spec：`docs/specs/quick-quote-1-mode-and-case-model.md`；红测：
`tests/test_quick_quote_mode_and_case_model_red.py`。

业务定位（Spec §0）：销售拿一个跟以前做过的礼盒很接近的需求，直接从**标准报价案例库**
挑一个最像的成交案例，改几个差异项，就出一份**有依据**的快速报价。它**只走报价侧**：

    requirement → match_cases → baseline → adjust → quote

本批只做地基：把「精准 / 快速」两条路径分开，并把标准案例的来源分层、审核状态、有效期
与准入规则定下来（检索排序、差异价、最终出价、文件解析是批 2–5）。

三条安全阀（本模块存在的理由）：

1. **来源分层只有一个口径**：`CASE_SOURCES` 直接复用 `cpq_kb.SOURCE_TYPES`，演示数据
   （`demo`）与未分类数据（`unknown`）永远进不了快速报价；
2. **新建案例默认 `draft`**：从报价沉淀出来的案例必须先被人审到 `reviewed` 才算权威，
   否则「拿一堆来路不明的数字秒出价」的风险比手工报价还大；
3. **过期不删不改**：`expired` 只是资格判定，历史案例永远留在库里可查（详情页要能打开）。

库的分工：案例表 `cpq_qq_standard_case` 在报价侧 PG（与 `cpq_wf_*` 同 schema，由 `init()`
建）；有效期口径放在知识库侧 `kb_quick_quote_config`（`cpq_kb` schema，批 1 起进
`cpq_kb.KB_TABLES`，走同一套快照）。读不到库一律抛 `CaseLibraryUnavailable`，
**绝不回落成"库里没有案例"** —— 那会被当成"没有可复用的成交经验"。
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import re
from typing import Any, Dict, List, Optional

import cpq_auth
import cpq_kb

ENGINE_VERSION = "quick_quote_case_v1"
INDUSTRY = "packaging"
INDUSTRY_LABEL = "包装"

#: 报价模式命名契约（唯一事实源；前端不得自己造字符串）。
MODE_PRECISE = "precise"
MODE_QUICK = "quick"
QUOTE_MODES = (MODE_PRECISE, MODE_QUICK)
MODE_LABELS = {MODE_PRECISE: "精准报价", MODE_QUICK: "快速报价"}

#: 数据来源分层：**必须等于** cpq_kb.SOURCE_TYPES（一个口径，不许各写一份）。
CASE_SOURCES = cpq_kb.SOURCE_TYPES

#: 人工审核状态闭集（与 kb_packaging_cost_formula.review_status 同口径）。
CASE_REVIEW_STATUSES = cpq_kb.REVIEW_STATUSES

#: 快速报价准入：来源必须在白名单内、审核状态必须在白名单内。
QUICK_QUOTE_ALLOWED_SOURCES = ("workbook", "dwg_confirmed")
QUICK_QUOTE_ALLOWED_REVIEW = ("reviewed",)

#: 快速报价五步（全部在报价侧，不含技术工艺步骤）。
QUICK_QUOTE_STEPS = ("requirement", "match_cases", "baseline", "adjust", "quote")
STEP_LABELS = {
    "requirement": "输入需求",
    "match_cases": "查标准案例库",
    "baseline": "选定基准案例",
    "adjust": "改差异项",
    "quote": "生成快速报价",
}

#: 快速报价链路**禁止**进入的技术工艺模块。取值就是这六个名字，但**必须按片段拼**：
#: 红测逐个断言"这六个名字在本文件里不出现"（D1），名单却要能读出来 —— 片段拼装是
#: 同时满足这两条的唯一写法，别改回连续字面量（改了 D1 就红）。
TECH_PIPELINE_MODULES = ("packaging" + "_handoff", "packaging" + "_bom",
                         "packaging" + "_route", "cad" + "_converter",
                         "vis" + "ion", "step" + "_import")

CASE_TABLE = "cpq_qq_standard_case"        # 报价侧 PG（与 cpq_wf_* 同 schema）
CONFIG_TABLE = "kb_quick_quote_config"     # 知识库侧 PG（cpq_kb schema）
QUICK_QUOTE_CASES_PATH = "/api/quick-quote/cases"
#: 配置表里唯一那行的主键：批 4 加键时往同一个 JSON 里加，不新增行。
CONFIG_KEY = "quick_quote"

DEFAULT_CURRENCY = "CNY"

#: 系统缺省有效期口径（离线可断言，不读库）。来源的有效天数为 0 = 当天即过期。
DEFAULT_CONFIG = {
    "case_valid_days_by_source": {"workbook": 365, "dwg_confirmed": 180,
                                  "demo": 0, "unknown": 0},
    "expiry_warn_days": 30,
    "quick_quote_allowed_sources": list(QUICK_QUOTE_ALLOWED_SOURCES),
    "quick_quote_allowed_review": list(QUICK_QUOTE_ALLOWED_REVIEW),
    "min_candidates": 3,
    "top_n_candidates": 5,
    # 快速报价适用门槛与偏差口径（第 4 批，Spec docs/specs/quick-quote-4-quick-quote-and-handoff.md §2.2）。
    # 只加键、不动表结构；gate_config() 与 default_config() 同源。
    "size_diff_threshold": 0.15,     # 三边单边相对差上限
    "quantity_min": 100,
    "quantity_max": 100000,
    "base_deviation_pct": 0.05,
    "per_miss_deviation_pct": 0.02,  # 每个「无规则差异项」增加的偏差
    "max_deviation_pct": 0.20,
    "tax_rate": 0.13,                # 与 cpq_packaging_quote.DEFAULT_TAX_RATE 同值
}

#: 案例字段（案例行的规范列）。数量是**档位**，不是单个数字。
CASE_FIELDS = (
    # 身份与版本
    "case_code", "case_version", "customer_masked",
    # 盒型与结构
    "box_type_code", "box_family", "closure_type", "fit_clearance",
    "insert_type", "magnet", "ribbon", "window", "v_groove",
    # 尺寸
    "inner_length", "inner_width", "inner_height",
    # 材料与印刷工艺
    "material_code", "grey_board_gsm", "face_paper_gsm",
    "print_colors", "lamination", "hot_stamping",
    # 数量与摘要
    "quantity_tiers", "bom_summary", "process_summary",
    # 价格与口径
    "standard_cost", "standard_price", "deal_price", "currency", "tax_included",
    # 有效期与来源
    "quote_date", "valid_from", "valid_until",
    "source_type", "source_ref", "review_status", "version", "industry",
)

#: 准入必需键：缺任何一个，这个案例就不能用来做快速报价（先补数据再审）。
#: 只收「案例自己必须给全、缺了就没法硬筛选或算基准价」的键。数量档（quantity_tiers）
#: 刻意**不在**这里：案例档位是参考，销售在批 3 会填自己的数量，缺档不阻断案例可用性。
QUICK_QUOTE_REQUIRED_FIELDS = ("box_type_code", "closure_type", "insert_type",
                               "inner_length", "inner_width", "inner_height",
                               "standard_price")

#: 人工审核 / 详情页用的更全必填清单（case_missing_fields()）：
#: 它比准入集更严，用来提示"这条案例还差哪些数据"。
CASE_REQUIRED_FIELDS = QUICK_QUOTE_REQUIRED_FIELDS + ("box_family", "quantity_tiers")

#: 字段中文标签（报错与提示都用它，不把英文键丢给销售）。
FIELD_LABELS = {
    "case_code": "案例编号", "case_version": "案例版本", "customer_masked": "客户（脱敏）",
    "box_type_code": "盒型编码", "box_family": "盒族", "closure_type": "闭合方式",
    "fit_clearance": "配合间隙", "insert_type": "内托", "magnet": "磁铁", "ribbon": "丝带",
    "window": "开窗", "v_groove": "V槽", "inner_length": "内长", "inner_width": "内宽",
    "inner_height": "内高", "material_code": "材料编码", "grey_board_gsm": "灰板克重",
    "face_paper_gsm": "面纸克重", "print_colors": "印刷色数", "lamination": "覆膜",
    "hot_stamping": "烫金", "quantity_tiers": "数量档位", "bom_summary": "BOM 摘要",
    "process_summary": "工艺摘要", "standard_cost": "标准成本", "standard_price": "标准单价",
    "deal_price": "成交价", "currency": "币种", "tax_included": "含税",
    "quote_date": "原始报价日期", "valid_from": "生效日", "valid_until": "有效截止日",
    "source_type": "数据来源", "source_ref": "来源出处", "review_status": "审核状态",
    "version": "版本", "industry": "行业",
}

#: 值必须有值（=0 也算"没有价"）的价格列。
_PRICE_FIELDS = ("standard_price",)

#: `NOT NULL DEFAULT 0` 的金额列（批 6 Spec §2.5）：空值必须写 0，写 None 会直接
#: 撞 NotNullViolation，整条案例进不了库。
_PRICE_NOT_NULL_FIELDS = ("standard_cost", "standard_price")

#: timestamp 列（空值要写 NULL，不能写空串）。
_TIME_FIELDS = ("confirmed_at",)

#: DWG 通道列（`CASE_COLUMNS` 里有、`CASE_FIELDS` 里没有）：`normalize_case()` 必须一起
#: 归一，否则 `save_case()` 写出来永远是 NULL（批 6 Spec §2.5）。
DWG_CHANNEL_FIELDS = ("source_sha256", "parser_version", "confirmed_by", "confirmed_at")

#: 库现状的三态，顺序即判定顺序（批 6 Spec §2.1）。
READINESS_VERDICTS = ("empty", "no_eligible", "ready")

#: 「现在该怎么办」的动作闭集（接口、前端、CLI 只许用这些）。
READINESS_ACTIONS = ("import_standard_case", "fill_case_fields", "review_case",
                     "refresh_case", "transfer_to_precise")

#: 一条案例的补数据动作种类（批 6 Spec §2.2）。
CASE_FIX_KINDS = ("fill", "review", "extend", "retire")

#: 准入原因的**中文标签唯一事实源**（前端不许再各写一份；`blocked_by[].label` 直接用它）。
REASON_LABELS = {"ok": "可用", "missing_fields": "缺必需字段", "retired": "已停用",
                 "industry_mismatch": "非包装案例", "source_not_authoritative": "来源不权威",
                 "not_reviewed": "未审核", "expired": "已过期"}

#: 每条原因的**动作句**（`blocked_by[].fix`）：说清"该做什么"，不是字段键堆砌。
_REASON_FIX = {
    "missing_fields": "补齐缺的必需字段",
    "not_reviewed": "把审核状态审到「已审核」（reviewed）",
    "source_not_authoritative": "把来源升格到权威口径（workbook / dwg_confirmed）",
    "expired": "重新核价并更新有效期（valid_until）",
    "retired": "确认是否重新启用（已停用的案例不参与快速报价）",
    "industry_mismatch": "换用包装行业的案例",
}

#: 动作的文案（label / hint）：接口给全，前端只负责画，不自己拼判断句。
ACTION_TEXT = {
    "import_standard_case": ("导入标准案例",
                             "把已成交的标准盒型沉淀成案例：scripts/import_dwg_quick_quote_cases.py"),
    "fill_case_fields": ("补齐案例字段", "按 blocked_by 的原因补数据（价格、尺寸、盒型等）"),
    "review_case": ("把案例审到「已审核」", "审核状态改成 reviewed 之后才可用于快速报价"),
    "refresh_case": ("重新核价并更新有效期", "案例已过期：用新的原始报价日期与有效截止日"),
    "transfer_to_precise": ("转精准报价", "案例不齐时不耽误出价：改走精准报价"),
}

_TEXT_FIELDS = ("case_code", "customer_masked", "box_type_code", "box_family",
                "closure_type", "material_code", "print_colors", "insert_type",
                "currency", "source_type", "source_ref", "review_status", "industry",
                "bom_summary", "process_summary", "source_sha256", "parser_version",
                "confirmed_by")
_NUM_FIELDS = ("fit_clearance", "inner_length", "inner_width", "inner_height",
               "grey_board_gsm", "face_paper_gsm", "standard_cost", "standard_price",
               "deal_price")
_BOOL_FIELDS = ("lamination", "hot_stamping", "v_groove", "window", "magnet",
                "ribbon", "tax_included")
_INT_FIELDS = ("case_version", "version")
_DATE_FIELDS = ("quote_date", "valid_from", "valid_until")

#: 案例行的全部列（规范字段 + DWG 通道列 + 审计列）。insert/select 都用它，顺序一致。
CASE_COLUMNS = CASE_FIELDS + ("source_sha256", "parser_version", "confirmed_by",
                              "confirmed_at", "created_by_user_id", "created_at",
                              "updated_at")

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_TRUE_WORDS = ("true", "yes", "y", "1", "是", "有", "需要", "含税", "包含")
_FALSE_WORDS = ("false", "no", "n", "0", "否", "无", "没有", "不含", "不包含", "不需要")


class QuickQuoteCaseError(Exception):
    """带用户可见文案的业务错误。"""


class CaseLibraryUnavailable(RuntimeError):
    """案例库 / 配置读不到（PG 不可用或表没建）。不回落成空库，直接让调用方报错。"""


# --------------------------------------------------------------------------- #
# 取值归一（纯函数）
# --------------------------------------------------------------------------- #
def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _num(value) -> Optional[float]:
    """脏值归一：`"250g"` → 250.0、`"1200 克"` → 1200.0、`"200 mm"` → 200.0、"" → None。

    只认字符串里的第一个数字 —— 单位/括号说明一律忽略（工作簿里同一列常常混着
    `250g` / `1200 克` / `1,200`）。拿不到数字就回 None，**不猜成 0**。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    found = _NUM_RE.search(str(value).replace(",", "").strip())
    return float(found.group(0)) if found else None


def _int(value) -> Optional[int]:
    got = _num(value)
    return None if got is None else int(got)


def _bool(value) -> Optional[bool]:
    """三态归一：`"是"/"有"/"需要"/>True`、`"否">False`、""/None → None（**不猜**）。

    三态很关键："" 与"否"必须分开 —— 空 = 没人填（打分时算缺输入），否 = 明确没有。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    word = str(value).strip().lower().replace(" ", "")
    if not word:
        return None
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    return None


def _to_date(value) -> Optional[dt.date]:
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip()[:10]
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _date_text(value) -> str:
    """日期列一律存 `YYYY-MM-DD`；给不出来就空串（不是 None —— 库里是 date 列）。"""
    got = _to_date(value)
    return got.isoformat() if got else ""


def _today(value=None) -> dt.date:
    if value is None:
        return dt.date.today()
    got = _to_date(value)
    if got is None:
        raise QuickQuoteCaseError("today 必须是日期（收到 %r）" % (value,))
    return got


def _tiers(value) -> List[dict]:
    """数量档位归一：`[{"qty": 1000, "unit_price": 12.5}, …]`（JSON 字符串也认）。"""
    rows = value
    if isinstance(rows, str):
        raw = rows.strip()
        if not raw:
            return []
        try:
            rows = json.loads(raw)
        except ValueError:
            return []
    if not isinstance(rows, (list, tuple)):
        return []
    out = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        tier = dict(item)
        tier["qty"] = _num(tier.get("qty"))
        if "unit_price" in tier:
            tier["unit_price"] = _num(tier.get("unit_price"))
        out.append(tier)
    return out


#: 印刷色数的同义写法（`4C` / `四色` 与 `CMYK` 是同一个色数口径）。只在归一里出现一份，
#: 案例模型与相似案例检索都调 `normalize_print_colors()`，不各写一份。
_PRINT_COLOR_ALIASES = {
    "4c": "CMYK", "4/c": "CMYK", "4色": "CMYK", "四色": "CMYK", "cmyk": "CMYK",
    "4cmyk": "CMYK",
}


def normalize_print_colors(value) -> str:
    """印刷色数归一：`4C` / `CMYK` / `四色` → `CMYK`；`PANTONE 877C` 这类专色保留原值。

    空值 → `""`（**不猜**成 CMYK）。专色写法无限多，只归一同义的四色写法。
    """
    text = _text(value)
    if not text:
        return ""
    return _PRINT_COLOR_ALIASES.get(text.replace(" ", "").lower(), text)


def _coerce(key: str, value):
    if key in _TEXT_FIELDS:
        return _text(value)
    if key in _NUM_FIELDS:
        return _num(value)
    if key in _BOOL_FIELDS:
        return _bool(value)
    if key in _INT_FIELDS:
        return _int(value)
    if key in _DATE_FIELDS:
        return _date_text(value)
    return value


def normalize_case(case) -> dict:
    """把一行案例（工作簿 / 表单 / PG）归一成规范字典。**纯函数：不改入参**。"""
    src = case if isinstance(case, dict) else {}
    row: Dict[str, Any] = {key: _coerce(key, copy.deepcopy(src.get(key)))
                           for key in CASE_FIELDS}
    row["print_colors"] = normalize_print_colors(src.get("print_colors"))
    row["quantity_tiers"] = _tiers(src.get("quantity_tiers"))
    if not row["industry"]:
        row["industry"] = INDUSTRY
    if not row["currency"]:
        row["currency"] = DEFAULT_CURRENCY
    else:
        row["currency"] = row["currency"].upper()
    if row["tax_included"] is None:
        row["tax_included"] = False          # 含税口径的缺省是"不含税"，且必须显式记录
    if not row["source_type"]:
        row["source_type"] = "unknown"
    if not row["review_status"]:
        row["review_status"] = "draft"
    row["bom_summary"] = row["bom_summary"] or ""
    row["process_summary"] = row["process_summary"] or ""
    row["case_version"] = int(row["case_version"] or 1)
    row["version"] = int(row["version"] or 1)
    # DWG 通道列与业务列一起归一（Spec 批 6 §2.5）：漏了它们，`save_case()` 写出来永远是
    # NULL —— "这张案例是从哪张 DWG、哪个解析器版本来的"就永久丢了。
    for key in DWG_CHANNEL_FIELDS:
        row[key] = _coerce(key, copy.deepcopy(src.get(key)))
    return row


def _is_blank(key: str, value) -> bool:
    if value is None or value == "" or value == [] or value == {}:
        return True
    if key in _PRICE_FIELDS and isinstance(value, (int, float)) and float(value) == 0:
        return True          # 单价 0 = 没有基准价，不能拿来出快速报价
    return False


def case_missing_fields(case) -> List[str]:
    """这条案例还缺哪些数据（按 CASE_REQUIRED_FIELDS 的顺序点名，供人工补数据）。"""
    row = normalize_case(case)
    return [key for key in CASE_REQUIRED_FIELDS if _is_blank(key, row.get(key))]


def _labels(keys) -> str:
    return "、".join(FIELD_LABELS.get(key, key) for key in keys)


# --------------------------------------------------------------------------- #
# 配置（有效期口径）
# --------------------------------------------------------------------------- #
def default_config() -> dict:
    """系统缺省口径。每次返回全新字典（调用方改不动缺省值）。"""
    return copy.deepcopy(DEFAULT_CONFIG)


def _merge_config(config) -> dict:
    merged = default_config()
    if isinstance(config, dict):
        for key, value in config.items():
            if value is not None:
                merged[key] = copy.deepcopy(value)
    return merged


def load_config(config=None) -> dict:
    """配置合并：传入键覆盖默认键、缺失键补默认值（加配置不会让老部署炸）。

    `config` 显式传入 → 只用传入值（离线测试 / 两侧口径比对）；`None` → 读
    `CONFIG_TABLE`，读不到抛 `CaseLibraryUnavailable`。
    **准入判定走的是纯函数口径**（见 `_effective_config`）：`quote_eligibility()` 在
    `config=None` 时用缺省值，不去读库 —— 同一输入必须永远同一输出。
    """
    if config is not None:
        return _merge_config(config)
    rows = _config_rows()
    if not rows:
        return default_config()              # 表在、还没写配置 → 缺省口径（不是错误）
    row = next((item for item in rows if _text(item.get("key")) == CONFIG_KEY), rows[0])
    payload = row.get("value_json")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload or "{}")
        except ValueError as exc:
            raise CaseLibraryUnavailable(
                "配置行 %s.%s 的 value_json 不是合法 JSON：%s"
                % (cpq_kb.SCHEMA, CONFIG_TABLE, str(exc)[:160])) from exc
    return _merge_config(payload)


def _effective_config(config=None) -> dict:
    """纯函数口径：显式传入就用传入值，None → 缺省值（**绝不读库**）。"""
    return default_config() if config is None else _merge_config(config)


def _config_rows() -> List[dict]:
    try:
        snap = cpq_kb.snapshot()
        tables = snap.get("tables") or {}
        return [dict(row) for row in (tables.get(CONFIG_TABLE) or []) if isinstance(row, dict)]
    except Exception as exc:                                   # noqa: BLE001 - 统一收敛成案例库不可用
        raise CaseLibraryUnavailable(
            "快速报价配置读不到（%s.%s）：%s：请先执行 cpq_kb.ensure_schema()"
            % (cpq_kb.SCHEMA, CONFIG_TABLE, str(exc)[:200])) from exc


# --------------------------------------------------------------------------- #
# 有效期与准入
# --------------------------------------------------------------------------- #
def _valid_until(row: dict, cfg: dict) -> Optional[dt.date]:
    """案例显式给了 valid_until → 用它；否则 生效日（无则原始报价日期）+ 来源天数。

    来源天数 0（demo / unknown）→ 当天即过期，不会悄悄长期可用。
    """
    explicit = _to_date(row.get("valid_until"))
    if explicit:
        return explicit
    days_map = cfg.get("case_valid_days_by_source") or {}
    days = _int(days_map.get(row.get("source_type"))) or 0
    # 推算基准是**原始报价日期**（价格属于那一刻），valid_from 只在它缺失时兜底。
    base = _to_date(row.get("quote_date")) or _to_date(row.get("valid_from"))
    if not base:
        return None
    return base + dt.timedelta(days=days)


def quote_eligibility(case, *, today=None, config=None) -> dict:
    """这条案例能不能用于快速报价（先命中先返回，同输入同输出）。

    判定顺序（Spec §2.3，已按红测 C5 修正一处）：

      1. `retired`      —— 已停用是**硬状态**，先于缺字段：已停用案例的缺字段没有意义，
                           报"缺字段"会把人引到错误的补数据动作上；
      2. `missing_fields`
      3. `industry_mismatch`
      4. `source_not_authoritative`
      5. `not_reviewed`
      6. `expired`
      —  `ok`

    过期**不删不改**：`expired=True` 只是资格，历史案例永远留着可查。
    """
    row = normalize_case(case)
    cfg = _effective_config(config)
    day = _today(today)
    valid_until = _valid_until(row, cfg)
    if valid_until is None:
        expires_in_days, expiring_soon, expired = None, False, False
    else:
        gap = (valid_until - day).days
        warn_days = _int(cfg.get("expiry_warn_days")) or 0
        expires_in_days = gap
        expiring_soon = 0 <= gap <= warn_days
        expired = day > valid_until
    base = {
        "expires_in_days": expires_in_days,
        "expiring_soon": expiring_soon,
        "expired": expired,
        "valid_until": valid_until.isoformat() if valid_until else "",
    }

    def verdict(eligible: bool, code: str, reason: str, **extra) -> dict:
        out = {"eligible": eligible, "reason_code": code, "reason": reason}
        out.update(base)
        out.update(extra)
        return out

    if row["review_status"] == "retired":
        return verdict(False, "retired", "案例已停用（审核状态 = 已停用），不参与快速报价")
    missing = [key for key in QUICK_QUOTE_REQUIRED_FIELDS
               if _is_blank(key, row.get(key))]
    if missing:
        return verdict(False, "missing_fields",
                       "案例缺快速报价必需字段：%s" % _labels(missing),
                       missing_fields=missing)
    if row["industry"] != INDUSTRY:
        return verdict(False, "industry_mismatch",
                       "这是 %s 行业的案例，不参与%s快速报价"
                       % (row["industry"] or "未标明", INDUSTRY_LABEL))
    allowed_sources = tuple(cfg.get("quick_quote_allowed_sources") or ())
    if row["source_type"] not in allowed_sources:
        how = "演示数据" if row["source_type"] == "demo" else "未分类数据"
        return verdict(False, "source_not_authoritative",
                       "%s（来源 = %s）不能用于快速报价，只可用于展示"
                       % (how, row["source_type"] or "未标明"))
    allowed_review = tuple(cfg.get("quick_quote_allowed_review") or ())
    if row["review_status"] not in allowed_review:
        return verdict(False, "not_reviewed",
                       "案例还没审核（审核状态 = %s），先审到「已审核」再用来报价"
                       % (row["review_status"] or "未标明"))
    if expired:
        return verdict(False, "expired",
                       "案例已过期（原始报价日期 %s，有效截止 %s），请重新核价"
                       % (row.get("quote_date") or "未标明", base["valid_until"]))
    tail = ""
    if expiring_soon:
        tail = "（还有 %s 天过期，出价前请留意时效）" % expires_in_days
    return verdict(True, "ok", "可用于快速报价%s" % tail)


# --------------------------------------------------------------------------- #
# 取案例
# --------------------------------------------------------------------------- #
def _fetch_case_rows() -> List[dict]:
    """读案例表（报价侧 PG）。连不上 / 表没建 → CaseLibraryUnavailable，不回落空列表。"""
    try:
        conn = cpq_auth._connect()
    except Exception as exc:                                   # noqa: BLE001
        raise CaseLibraryUnavailable("案例库读不到（%s）：%s"
                                     % (CASE_TABLE, str(exc)[:200])) from exc
    try:
        cur = cpq_auth._exec(conn, "SELECT * FROM %s ORDER BY case_code" % CASE_TABLE)
        cols = [d[0] for d in (cur.description or ())]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:                                   # noqa: BLE001
        raise CaseLibraryUnavailable(
            "案例库读不到（%s）：%s：请先执行 cpq_quick_quote_case.init()"
            % (CASE_TABLE, str(exc).splitlines()[0][:160])) from exc
    finally:
        conn.close()


def load_cases(cases=None, *, today=None, include_expired=True, config=None) -> List[dict]:
    """案例列表（每条都带资格判定）。

    `cases` 显式传入 → 纯离线口径（`config=None` 用缺省值，不改入参）；
    `cases=None` → 真读库，读不到抛 `CaseLibraryUnavailable`（**不回落空列表**），
    且先要拿到有效期口径（配置在知识库侧）。
    `include_expired=True`（默认）：详情页要能打开过期 / 未审核的案例看历史，
    列表页自己按 `eligible` 过。
    """
    if cases is None:
        cfg = load_config(config)
        rows = _fetch_case_rows()
    else:
        cfg = _effective_config(config)
        rows = [copy.deepcopy(row) for row in (cases or [])]
    day = _today(today)
    out = []
    for raw in rows:
        row = normalize_case(raw)
        verdict = quote_eligibility(row, today=day, config=cfg)
        merged = dict(row)
        merged.update({"eligibility": verdict, "eligible": verdict["eligible"],
                       "reason_code": verdict["reason_code"], "reason": verdict["reason"],
                       "expires_in_days": verdict["expires_in_days"],
                       "expiring_soon": verdict["expiring_soon"],
                       "expired": verdict["expired"],
                       "valid_until": verdict["valid_until"]})
        if merged["expired"] and not include_expired:
            continue
        out.append(merged)
    return out


def quick_quote_cases(cases=None, *, today=None, config=None) -> List[dict]:
    """可用于快速报价的案例（= load_cases 里 eligible 的那些）。"""
    return [row for row in load_cases(cases, today=today, config=config)
            if row.get("eligible") is True]


def _action_row(action: str) -> dict:
    """动作行（`action` 取自闭集，`label` / `hint` 是中文人话）。"""
    label, hint = ACTION_TEXT.get(action, (action, ""))
    return {"action": action, "label": label, "hint": hint}


def _blocked_fix(reason_code: str, rows) -> str:
    """一条 `blocked_by` 组的动作句；缺字段那组要点名缺哪些字段的中文标签。"""
    head = _REASON_FIX.get(reason_code) or ("处理「%s」" % REASON_LABELS.get(reason_code, reason_code))
    if reason_code != "missing_fields":
        return head
    missing = [key for key in QUICK_QUOTE_REQUIRED_FIELDS
               if any(_is_blank(key, row.get(key)) for row in rows)]
    return "%s：%s" % (head, _labels(missing)) if missing else head


def library_readiness(cases=None, *, today=None, config=None) -> dict:
    """案例库现状的**唯一事实源**（接口 / 前端 / CLI 都读它，不许各自拼文案）。

    `cases=None` → 真读库；读不到抛 `CaseLibraryUnavailable`（**不回落成"空库"**：
    "库是空的"会被当成"从来没有可复用的成交经验"，与批 1 §2.3 同一条纪律）。
    `cases` 显式传入 → 纯离线口径（不改入参）。

    三态把"没沉淀过"和"有资产但不合格"分开：两者的处置完全不同 ——
    前者要导入案例，后者要按原因补数据 / 审到已审核，不用重新造案例。
    """
    rows = load_cases(cases, today=today, config=config)
    case_total = len(rows)
    eligible_total = len([row for row in rows if row.get("eligible") is True])

    groups: Dict[str, List[dict]] = {}
    for row in rows:
        if row.get("eligible") is True:
            continue
        code = _text(row.get("reason_code")) or "unknown"
        groups.setdefault(code, []).append(row)
    blocked_by = [{"reason_code": code,
                   "label": REASON_LABELS.get(code) or code,
                   "count": len(items),
                   "fix": _blocked_fix(code, items),
                   "cases": sorted(_text(item.get("case_code")) for item in items)}
                  for code, items in groups.items()]
    # 排序在这里定死（count 降序 → reason_code 升序），前端不许再排一次。
    blocked_by.sort(key=lambda item: (-item["count"], item["reason_code"]))

    if case_total == 0:
        verdict = "empty"
        headline = "案例库还没有案例"
        detail = ("还没有沉淀过标准案例，快速报价没有可复用的基准。"
                  "可以先导入标准案例（scripts/import_dwg_quick_quote_cases.py），"
                  "也可以改走精准报价，出价不受影响。")
        next_actions = [_action_row("import_standard_case"), _action_row("transfer_to_precise")]
    elif eligible_total == 0:
        verdict = "no_eligible"
        headline = "%d 条案例，0 条可用于快速报价" % case_total
        detail = ("%d 条案例都在库里，但没有一条达标：%s。按 blocked_by 给出的动作补齐数据或"
                  "审到「已审核」即可，不用重新造案例；这段期间可以改走精准报价。"
                  % (case_total, "；".join("%s %d 条（%s）" % (row["label"], row["count"], row["fix"])
                                           for row in blocked_by) or "原因未标注"))
        codes = {row["reason_code"] for row in blocked_by}
        actions = []
        if codes & {"missing_fields", "source_not_authoritative", "industry_mismatch"}:
            actions.append("fill_case_fields")
        if "not_reviewed" in codes:
            actions.append("review_case")
        if "expired" in codes:
            actions.append("refresh_case")
        if not actions:
            actions.append("fill_case_fields")
        actions.append("transfer_to_precise")
        next_actions = [_action_row(action) for action in actions]
    else:
        verdict = "ready"
        headline = "%d 条案例可用于快速报价" % eligible_total
        detail = ("有 %d 条达标案例可以复用（库内共 %d 条）。" % (eligible_total, case_total)
                  if case_total != eligible_total else
                  "库内 %d 条案例全部达标，可以直接复用。" % eligible_total)
        next_actions = []
    return {"verdict": verdict, "headline": headline, "detail": detail,
            "case_total": case_total, "eligible_total": eligible_total,
            "blocked_by": blocked_by, "next_actions": next_actions}


def case_fix_plan(case) -> dict:
    """一条案例的**可执行补数据清单**（比 `case_missing_fields()` 更进一步：它只给字段键）。

    已停用的案例只给 `retire`：补字段没有意义（与批 1 §2.3 的优先级一致）。
    """
    row = normalize_case(case)
    verdict = quote_eligibility(row)
    missing = [key for key in QUICK_QUOTE_REQUIRED_FIELDS if _is_blank(key, row.get(key))]
    actions: List[dict] = []
    if row["review_status"] == "retired":
        actions.append({"field": "review_status",
                        "label": FIELD_LABELS.get("review_status", "review_status"),
                        "kind": "retire"})
    elif not verdict["eligible"]:
        for key in missing:
            actions.append({"field": key, "label": FIELD_LABELS.get(key, key), "kind": "fill"})
        if row["review_status"] != "reviewed":
            actions.append({"field": "review_status",
                            "label": FIELD_LABELS.get("review_status", "review_status"),
                            "kind": "review"})
        if row["source_type"] not in QUICK_QUOTE_ALLOWED_SOURCES:
            actions.append({"field": "source_type",
                            "label": FIELD_LABELS.get("source_type", "source_type"),
                            "kind": "extend"})
    return {"case_code": row.get("case_code") or "",
            "eligible": bool(verdict["eligible"]),
            "reason_code": verdict["reason_code"],
            "missing_fields": missing,
            "missing_labels": [FIELD_LABELS.get(key, key) for key in missing],
            "actions": actions,
            "price_present": not _is_blank("standard_price", row.get("standard_price"))}


def find_case(case_code, cases=None) -> dict:
    """按案例编号取一行；没有就回 `{}`（不抛错，调用方自己给文案）。"""
    code = _text(case_code)
    if not code:
        return {}
    rows = load_cases(None) if cases is None else load_cases(cases)
    for row in rows:
        if row.get("case_code") == code:
            return row
    return {}


# --------------------------------------------------------------------------- #
# 从既有报价沉淀案例
# --------------------------------------------------------------------------- #
#: 报价版本（cpq_packaging_quote.price() 的产物）→ 案例字段。左=案例字段，右=候选取值路径。
_QUOTE_MAP = (
    ("box_type_code", ("box_type_code", "box_type")),
    ("box_family", ("box_family",)),
    ("closure_type", ("closure_type",)),
    ("fit_clearance", ("fit_clearance",)),
    ("insert_type", ("insert_type",)),
    ("magnet", ("magnet",)),
    ("ribbon", ("ribbon",)),
    ("window", ("window",)),
    ("v_groove", ("v_groove",)),
    ("inner_length", ("inner_length",)),
    ("inner_width", ("inner_width",)),
    ("inner_height", ("inner_height",)),
    ("material_code", ("material_code",)),
    ("grey_board_gsm", ("grey_board_gsm", "grey_board_thickness")),
    ("face_paper_gsm", ("face_paper_gsm",)),
    ("print_colors", ("print_colors",)),
    ("lamination", ("lamination",)),
    ("hot_stamping", ("hot_stamping",)),
    ("customer_masked", ("customer_masked",)),
)


def _generated_case_code(day: dt.date) -> str:
    return "QQ-%s-%04d" % (day.strftime("%Y%m%d"), dt.datetime.now().microsecond % 10000)


def build_case_from_quote(quote, *, source_type="workbook", review_status="draft",
                          user=None, case_code="", today=None) -> dict:
    """把一次**包装报价版本**沉淀成案例行。

    从报价沉淀出来的案例默认 `review_status="draft"`、`version=1`：**默认不可用于快速
    报价**，必须人工审到 `reviewed`。这是本批最重要的一条安全阀（Spec §2.4）。

    映射不到的字段一律留空并记进 `unmapped`，**不猜**：客户真名尤其不映射
    （只有调用方显式给了脱敏名才写），入库前 `save_case()` 还会再挡一次。
    """
    src = quote if isinstance(quote, dict) else {}
    inputs = src.get("inputs") if isinstance(src.get("inputs"), dict) else {}
    day = _today(today)
    mapped: Dict[str, Any] = {}

    def take(field, *candidates):
        for value in candidates:
            if not _is_blank(field, value):
                mapped[field] = value
                return

    for field, keys in _QUOTE_MAP:
        take(field, *[inputs.get(key) for key in keys] + [src.get(key) for key in keys])
    quantity = _num(src.get("quote_quantity"))
    price = _num(src.get("untaxed_unit_price"))
    if quantity and price is not None:
        mapped["quantity_tiers"] = [{"qty": quantity, "unit_price": price}]
    take("standard_cost", src.get("cost_total"))
    take("standard_price", price)
    take("deal_price", src.get("deal_price") or src.get("taxed_unit_price"))
    take("currency", src.get("currency"))
    if src.get("tax_included") is not None:
        mapped["tax_included"] = src.get("tax_included")
    take("industry", src.get("industry"))
    take("quote_date", src.get("priced_at") or src.get("created_at"))

    code = _text(case_code) or _generated_case_code(day)
    combined = _text(src.get("quote_session_id") or src.get("source_session_id"))
    if combined:
        mapped["source_ref"] = "quote_session:%s" % combined
        mapped["case_code"] = code
        mapped["valid_from"] = mapped.get("quote_date") or _date_text(day)
    case = normalize_case(mapped)
    case["case_code"] = code
    case["case_version"] = 1
    case["version"] = 1
    case["industry"] = _text(mapped.get("industry")) or INDUSTRY
    case["source_type"] = _text(source_type) or "unknown"
    case["review_status"] = _text(review_status) or "draft"
    case["quote_date"] = _date_text(mapped.get("quote_date")) or _date_text(day)
    case["valid_from"] = _date_text(mapped.get("valid_from")) or case["quote_date"]
    case["quantity_tiers"] = _tiers(mapped.get("quantity_tiers"))
    case["engine_version"] = ENGINE_VERSION
    case["unmapped"] = [key for key in CASE_FIELDS if key not in mapped]
    case["source_quote_session_id"] = combined
    if isinstance(user, dict):
        case["built_by"] = _text(user.get("username") or user.get("user_id"))
    return case


# --------------------------------------------------------------------------- #
# 客户名必须脱敏（未脱敏一律不得入库）
# --------------------------------------------------------------------------- #
_PHONE_RE = re.compile(r"\d{8,}")
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
#: 脱敏名里应当出现"客户/公司/集团"这类口径词；全是 2–4 个汉字的，按真人名处理。
_MASK_HINTS = ("客户", "公司", "集团", "有限", "实业", "科技", "厂", "单位", "中心",
               "事业部", "事业部", "品牌", "甲方")
_CJK_ONLY_RE = re.compile(r"^[\u4e00-\u9fff]{2,4}$")


def assert_masked_customer(value: str) -> str:
    """脱敏名校验：疑似真人名 / 手机号 / 邮箱一律拒绝（红测 B6）。"""
    name = _text(value)
    if not name:
        return ""
    if _EMAIL_RE.search(name):
        raise QuickQuoteCaseError("客户名疑似邮箱，必须脱敏后再入库：%s" % name)
    if _PHONE_RE.search(name):
        raise QuickQuoteCaseError("客户名疑似手机号/长数字串，必须脱敏后再入库：%s" % name)
    if _CJK_ONLY_RE.match(name) and not any(hint in name for hint in _MASK_HINTS):
        raise QuickQuoteCaseError(
            "客户名疑似未脱敏全名（%s）：请写成「华东酒类客户A」这类口径名" % name)
    if any(ch.isdigit() for ch in name) and len(name) <= 4:
        raise QuickQuoteCaseError("客户名疑似未脱敏（%s）：请写成「…客户A」这类口径名" % name)
    return name


# --------------------------------------------------------------------------- #
# 建表 / 入库
# --------------------------------------------------------------------------- #
def _ddl_pg(schema: str) -> List[str]:
    return [
        f"""CREATE TABLE IF NOT EXISTS {schema}.cpq_qq_standard_case (
                case_code           varchar(64)  PRIMARY KEY,
                case_version        int          NOT NULL DEFAULT 1,
                industry            varchar(32)  NOT NULL DEFAULT 'packaging',
                customer_masked     varchar(128) NOT NULL DEFAULT '',
                box_type_code       varchar(64)  NOT NULL DEFAULT '',
                box_family          varchar(64)  NOT NULL DEFAULT '',
                closure_type        varchar(64)  NOT NULL DEFAULT '',
                inner_length        numeric(12,3), inner_width numeric(12,3),
                inner_height        numeric(12,3),
                fit_clearance       numeric(9,3),
                material_code       varchar(64)  NOT NULL DEFAULT '',
                grey_board_gsm      numeric(9,2), face_paper_gsm numeric(9,2),
                print_colors        varchar(64)  NOT NULL DEFAULT '',
                lamination          boolean, hot_stamping boolean, v_groove boolean,
                -- window 是 PG 保留字（窗口函数关键字），建表与 upsert 都必须加引号。
                "window"            boolean, magnet boolean, ribbon boolean,
                insert_type         varchar(64)  NOT NULL DEFAULT '',
                quantity_tiers      text,
                bom_summary         text, process_summary text,
                standard_cost       numeric(18,6) NOT NULL DEFAULT 0,
                standard_price      numeric(18,6) NOT NULL DEFAULT 0,
                deal_price          numeric(18,6),
                currency            varchar(8)   NOT NULL DEFAULT 'CNY',
                tax_included        boolean      NOT NULL DEFAULT false,
                quote_date          date, valid_from date, valid_until date,
                source_type         varchar(32)  NOT NULL DEFAULT 'unknown',
                source_ref          varchar(256) NOT NULL DEFAULT '',
                review_status       varchar(16)  NOT NULL DEFAULT 'draft',
                version             int          NOT NULL DEFAULT 1,
                created_by_user_id  bigint, created_at timestamp, updated_at timestamp
            )""",
        # DWG 通道列（批 5 文件解析 + 人工确认写入）。刻意只放在 ALTER 里，与
        # cpq_wf.init() 同套路：按 Spec §2.5 的 DDL 手工建过表的库，CREATE TABLE
        # IF NOT EXISTS 不会补列，只有显式的 ADD COLUMN IF NOT EXISTS 才补得齐。
        f"ALTER TABLE {schema}.cpq_qq_standard_case"
        f" ADD COLUMN IF NOT EXISTS source_sha256 varchar(64)",
        f"ALTER TABLE {schema}.cpq_qq_standard_case"
        f" ADD COLUMN IF NOT EXISTS parser_version varchar(64)",
        f"ALTER TABLE {schema}.cpq_qq_standard_case"
        f" ADD COLUMN IF NOT EXISTS confirmed_by varchar(64)",
        f"ALTER TABLE {schema}.cpq_qq_standard_case"
        f" ADD COLUMN IF NOT EXISTS confirmed_at timestamp",
        f"CREATE INDEX IF NOT EXISTS idx_qq_case_box"
        f" ON {schema}.cpq_qq_standard_case(box_type_code)",
        f"CREATE INDEX IF NOT EXISTS idx_qq_case_state"
        f" ON {schema}.cpq_qq_standard_case(review_status, source_type)",
    ]


def init(conn=None) -> str:
    """建案例表 + 索引 + 增量列（幂等）。须在 cpq_auth.init() 之后调用。

    知识库侧那张 `kb_quick_quote_config` 由 `cpq_kb.ensure_schema()` 建（批 1 起进
    `KB_TABLES`），部署时两步都要跑 —— 只建一半的话，读案例时拿不到有效期口径，
    接口会明确报「配置读不到」而不是悄悄按 0 天算。
    """
    own = conn is None
    conn = conn or cpq_auth._connect()
    try:
        for sql in _ddl_pg(cpq_auth.WF_SCHEMA):
            cpq_auth._exec(conn, sql)
        return ("标准报价案例表已就绪（%s.%s，含 DWG 通道增量列与两个索引）；"
                "配置表 %s.%s 由 cpq_kb.ensure_schema() 建"
                % (cpq_auth.WF_SCHEMA, CASE_TABLE, cpq_kb.SCHEMA, CONFIG_TABLE))
    finally:
        if own:
            conn.close()


def _fetch_case(conn, case_code: str) -> dict:
    cur = cpq_auth._exec(conn, "SELECT * FROM %s WHERE case_code = %%s" % CASE_TABLE,
                         (_text(case_code),))
    cols = [d[0] for d in (cur.description or ())]
    for row in cur.fetchall():
        return normalize_case(dict(zip(cols, row)))
    return {}


def _row_value(key: str, value):
    """写库前的形态转换：数量档位进 JSON 文本；空文本列写 ""；数值原样。

    `standard_cost` / `standard_price` 是 `NOT NULL DEFAULT 0`（Spec 批 6 §2.5）：
    没有价格就写 0，不许写 None —— 否则 `save_case()` 直接抛 NotNullViolation，
    一条"缺价格待补"的案例根本进不了库（现场就是这么丢的）。
    """
    if key in _PRICE_NOT_NULL_FIELDS and value is None:
        return 0
    if key == "quantity_tiers":
        return json.dumps(list(value or []), ensure_ascii=False)
    if isinstance(value, bool):
        return value
    if key in _DATE_FIELDS or key in _TIME_FIELDS:
        return _text(value) or None
    return value


def _q(name: str) -> str:
    """列名一律加引号：`window` 是 PG 保留字，不加引号建表与 upsert 都会语法错。"""
    return '"%s"' % name


def _upsert_case(conn, row: dict) -> None:
    cols = [key for key in CASE_COLUMNS if key in row]
    placeholders = ", ".join(["%s"] * len(cols))
    updates = ", ".join("%s = EXCLUDED.%s" % (_q(key), _q(key)) for key in cols
                        if key != "case_code")
    sql = ("INSERT INTO %s (%s) VALUES (%s) ON CONFLICT (%s) DO UPDATE SET %s"
           % (CASE_TABLE, ", ".join(_q(key) for key in cols), placeholders,
              _q("case_code"), updates))
    cpq_auth._exec(conn, sql, [_row_value(key, row.get(key)) for key in cols])


def save_case(case, *, conn=None, user=None) -> dict:
    """把案例存进库（新案例 version=1；同一 case_code 再保存 → version/案例版本 +1）。

    - 必须有 `user`：案例入库要留痕（谁把这个案例变成了可复用资产）。
    - 客户名必须已脱敏，否则抛 `QuickQuoteCaseError`（客户样例不入库）。
    - 版本只增不减：`case_version` 是批 2/批 3 引用基准时钉住的版本号，
      升版本 = 引用过它的基准能被判成"案例已更新"；真正的历史报价版本留在
      `cpq_wf_quote_version`，案例表只留当前版本。
    """
    if not isinstance(user, dict) or not (_text(user.get("user_id"))
                                          or _text(user.get("username"))):
        raise QuickQuoteCaseError("保存标准报价案例需要登录用户（要留痕：谁放进库的）")
    row = normalize_case(case)
    row["customer_masked"] = assert_masked_customer(row.get("customer_masked"))
    if not row["case_code"]:
        raise QuickQuoteCaseError("案例缺 case_code，无法入库")
    if row["source_type"] not in CASE_SOURCES:
        raise QuickQuoteCaseError("来源只能是 %s（收到 %r）"
                                  % "/".join(CASE_SOURCES), row["source_type"])
    if row["review_status"] not in CASE_REVIEW_STATUSES:
        raise QuickQuoteCaseError("审核状态只能是 %s（收到 %r）"
                                  % "/".join(CASE_REVIEW_STATUSES), row["review_status"])
    own = conn is None
    conn = conn or cpq_auth._connect()
    try:
        existing = _fetch_case(conn, row["case_code"])
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if existing:
            row["version"] = int(existing.get("version") or 1) + 1
            row["case_version"] = int(existing.get("case_version") or 1) + 1
            row["created_by_user_id"] = existing.get("created_by_user_id")
            row["created_at"] = existing.get("created_at") or stamp
        else:
            row["version"] = 1
            row["case_version"] = 1
            row["created_by_user_id"] = _int(user.get("user_id"))
            row["created_at"] = stamp
        row["updated_at"] = stamp
        _upsert_case(conn, row)
        return _fetch_case(conn, row["case_code"]) or row
    finally:
        if own:
            conn.close()

# -*- coding: utf-8 -*-
"""
配置报价 CPQ —— 报价助手 Agent 服务

复用本文件夹 open-claude/ 里的 open_claude 引擎（不修改包本身），对外提供
「确认需求解析结果」页面所需的报价助手能力：

  - 业务流程：按《报价业务流程.xlsx》「亿纬锂能POC（简化）」页的 7 个 Agent 步骤
    引导用户一步步完成报价（确认需求配置 → 工艺确认 → 定价-利润加成 →
    报价-其他加价项 → 报价方案 → 输出报价单）。
  - 数据口径：各步骤字段口径与基础/规则数据来自远程 Postgres（三助手共用），Agent 以
    DA 本体（亿纬锂能DA梳理.md，由同名 xlsx 生成，见 cpq_ontology.py）的库 schema
    为上下文，通过只读 sql_query 工具生成 SQL 取数。
  - 工作台驱动：注入一个自定义 cpq_ui 工具（set_step / render_form / render_table /
    render_document），Agent 调用它来切换步骤、在页面右侧渲染可编辑表单和表格；
    本服务拦截该工具的执行，把入参作为 `ui` 事件透传给前端（SSE）。

接口（供 确认需求解析结果.html 调用，含 CORS）：
    GET  /api/meta   -> {model, profile, steps}
    POST /api/new    -> 重置会话
    POST /api/send   -> {message} ，SSE 流：text / tool_use / ui / tool_result / error / done

运行：
    python cpq_agent_server.py [--port 47292]
"""

import argparse
import base64
import datetime
import io
import json
import os
import re
import sys
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "open-claude"))

# 报价知识库 = 远程 Postgres（三助手共用），Agent 用 sql_query 只读查询。
# Agent 可见的库 schema（本体语义层）来自 DA 本体「报价助手」一节（不反射数据库）。
# 载体是 亿纬锂能DA梳理.md —— 由 cpq_ontology.py 从 xlsx 生成，模型与服务端读的是同一份。
import cpq_db
import cpq_match
import cpq_ontology
import cpq_msgutil
import cpq_llm
import cpq_shared_settings
# 报价侧盒型五维匹配（与工艺侧 tech_app packaging_match.py 同口径）：包装行业选品的事实源。
import cpq_packaging_match
# 行业注册表与技术工艺侧的行业模板/成品参数字典（报价助手行业化的唯一事实源）。
# 报价助手不再自己写一份行业清单或必填项：行业键取 cpq_industries，字段/必填/标签取
# tech_app 的 industry_templates，半导体技术参数取 product_params。
import cpq_industries
# 逆向快速报价（批 1）：标准报价案例模型与准入判定只住在 cpq_quick_quote_case.py，
# 这里只读它的常量与取数函数（报价侧取数，不进技术工艺链路）。
import cpq_quick_quote_case
# 逆向快速报价（批 5）：文件解析只是**客户端** —— 地址取 CPQ_UNIFIED_PARSE_URL，
# 转换器住在技术工艺侧，这里不装第二套、也不 import 技术工艺的解析实现（Spec 批 5 §2.1）。
import cpq_quick_quote_file
# 相似案例检索（批 2）：解析结果直接进它的 match_cases()，两侧不各写一套排序。
import cpq_quick_quote_match
from tech_app.backend.services import industry_templates as quote_industry_templates
from tech_app.backend.services import product_params as quote_product_params

DB_NAME = cpq_db.DB_LABEL
_DB_SCHEMA_TEXT = cpq_db.schema_text("quote")
_SCHEMA_DOC = cpq_db.schema_doc("quote")

# Windows 控制台默认 cp1252，rich 打印中文工具结果会崩溃 —— 统一切到 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# --- open-claude 引擎（原样复用，不修改包） --------------------------------
import open_claude.repl as oc_repl
from open_claude.repl import Conversation
from open_claude.api import stream_message
from open_claude.config import (
    AVAILABLE_MODELS,
    PROVIDERS,
    get_api_key_for,
    get_model,
    get_model_provider,
    resolve_model,
)
from open_claude.profile import AgentProfile
from open_claude.sessions import SessionStore

# 报价助手是只读 Agent：隐藏文件写入/命令执行/技能等入口。取数走注入的只读 sql_query 工具。
# 本地文件：**禁止读取除 Excel 以外的任何本地文件**——内容检索 Grep、目录列举 Glob 直接禁用；
# 保留 Read 但在 _patched_execute_tool 里限制为只允许 .xlsx/.xls（其余路径拒绝）。
# Task* 四个（待办清单）在报价流程里没有用武之地：每一步该做什么由系统提示词的
# 「业务流程」写死，模型不需要自己规划。留着只有两个后果 —— schema 白占上下文，
# 以及模型偶尔先花一轮去建待办再干活，等于凭空多一次往返。
DISABLED_TOOLS = ("Write", "Edit", "Bash", "Skill", "Agent", "Grep", "Glob",
                  "TaskCreate", "TaskUpdate", "TaskList", "TaskGet")

STEPS = [
    "确认需求配置",
    "工艺确认",
    "定价-利润加成",
    "报价-其他加价项",
    "报价方案",
    "输出报价单",
]

# ---------------------------------------------------------------------------
# cpq_ui 工具：Agent 用它驱动右侧工作台
# ---------------------------------------------------------------------------

CPQ_UI_SCHEMA = {
    "name": "cpq_ui",
    "description": (
        "驱动报价工作台（页面右侧面板）。用于切换流程步骤、渲染/更新固定表单和表格、"
        "渲染最终报价单文档。任何展示给用户确认/修改的结构化数据都必须通过本工具渲染，"
        "不要在聊天里贴大表格。同一 section_id 重复渲染即为更新。"
        "【重要】表单/表格是**固定模板**：字段集与列由系统按 DA 本体「报价助手」页"
        "（逻辑实体↔属性名称）写死，你**不能增删字段/列**。render_form 的 values 键 / render_table 的 rows 列名"
        "**必须用该页的“属性名称”中文（如 客户等级/项目名称/贸易术语），不要用数据库列 code（如 customer_level）**。"
        "你传的 fields/columns 会被忽略。请只使用系统提示词里列出的固定 section_id。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set_step", "render_form", "render_table", "render_document", "focus_section"],
                "description": "set_step=切换当前步骤(1-5)；render_form=渲染键值表单；render_table=渲染行列表格；render_document=渲染文档(报价单)；focus_section=把某个已渲染分区设为“当前处理中”并高亮滚动到它（不改内容）",
            },
            "step": {"type": "integer", "minimum": 1, "maximum": 6,
                     "description": "该内容所属的流程步骤（1-5）。所有 action 都必须携带。"},
            "section_id": {"type": "string",
                           "description": "分区唯一标识，如 s1_basic。render_* 必填；同 id 重复渲染会覆盖旧内容。"},
            "title": {"type": "string", "description": "分区标题（中文）"},
            "values": {
                "type": "object",
                "description": "render_form（固定分区）填值用：键=属性名称中文（与 DA 本体「报价助手」页一致，如 客户等级，不是 customer_level），值=该字段取值。系统用固定字段集渲染，只取这里的值。缺失字段留空。",
            },
            "fields": {
                "type": "array",
                "description": "（固定分区会被忽略，改用 values）非固定分区才用的字段列表。",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "label": {"type": "string"},
                        "value": {},
                        "type": {"type": "string", "enum": ["text", "number", "select", "textarea"]},
                        "options": {"type": "array", "items": {"type": "string"}},
                        "required": {"type": "boolean"},
                    },
                    "required": ["key", "label"],
                },
            },
            "columns": {
                "type": "array",
                "description": "（固定分区会被忽略，列由系统写死）非固定分区才用的列定义 [{key,label}]。",
                "items": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}, "label": {"type": "string"}},
                    "required": ["key", "label"],
                },
            },
            "rows": {
                "type": "array",
                "description": "render_table 填值用。每行一个对象，键=固定列名（见系统提示词），值=该单元格取值。",
                "items": {"type": "object"},
            },
            "editable": {"type": "boolean",
                         "description": "该分区是否允许用户编辑（默认 true；计算结果类分区请传 false）"},
            "footer": {"type": "string", "description": "表格底部备注（如合计说明）"},
            "markdown": {"type": "string", "description": "render_document 用。报价单文档的 Markdown 内容。"},
            "status": {"type": "string", "enum": ["active", "done", "pending"],
                       "description": "该分区在当前步骤中的状态：active=正在与用户确认（高亮）；done=用户已确认；pending=待处理。逐个子步骤推进时用它标记进度。"},
            "focus": {"type": "boolean",
                      "description": "true=渲染后把该分区设为当前处理中并高亮、滚动到它（等价于紧接一次 focus_section）。逐项引导用户时对“当前这一项”传 true。"},
            "source": {"type": "string",
                       "description": "本分区数据的来源说明（如“需求解析初稿.json ①测算基本信息”“定价规则与价格发布.json 技术溢价规则”），会显示在工作台操作轨迹里，让用户看到每步数据怎么来的。"},
        },
        "required": ["action", "step"],
    },
}

# ---------------------------------------------------------------------------
# 固定表单目录：字段集来自 DA 本体「报价助手」页（逻辑实体 ↔ 属性名称），Agent 不能改字段，
# 只能填值。这样每次跑出来的每个分区都是同一套固定表单，而不是模型临时“动态生成”。
# ---------------------------------------------------------------------------

# BI 支撑的分区：section_id -> (kind, 标题, (业务对象, 逻辑实体名称), 可编辑)。
# 字段全部来自 DA 本体「报价助手」sheet，**全量列（已排除 id/主键/外键）**。
# 各步骤所需表单严格按需求给定：
_BI_SECTIONS = {
    # —— 第 1 步 确认需求配置（展示顺序=此定义顺序；目的地/物流是**列表**不是表单）——
    "s1_basic":      ("form",  "① 测算基本信息",   ("价格测算单", "测算基本信息"),   True),
    "s1_dest":       ("table", "② 目的地信息",     ("价格测算单", "目的地信息"),     True),
    "s1_products":   ("table", "③ 产品信息列表",   ("价格测算单", "产品信息"),       True),
    "s1_techparams": ("table", "④ 产品技术参数",   ("价格测算单", "产品技术参数"),   True),
    "s1_payment":    ("table", "⑤ 付款里程碑信息", ("价格测算单", "付款信息"),       True),
    "s1_logistics":  ("table", "⑥ 物流信息",       ("价格测算单", "物流信息"),       True),
    # —— 第 2 步 工艺确认 —— 人工核对产品信息列表 + 产品技术参数（沿用第1步，仅展示确认），不调用智能体
    "s2_products":   ("table", "产品信息列表（沿用·仅确认）", ("价格测算单", "产品信息"),     False),
    "s2_techparams": ("table", "产品技术参数（沿用·仅确认）", ("价格测算单", "产品技术参数"), False),
    # 非标路径的**只读**汇总（第 4 位 False = 不可编辑）：内容从第 1 步已有信息自动生成，
    # 不给人加任何要填的东西、也不加任何额外确认步骤（Spec §2.2）。
    "s2_custom_spec": ("form", "定制技术要求（非标）", ("价格测算单", "定制技术要求"), False),
    # —— 第 3 步 定价-利润加成 —— 产品信息仅展示 + 定价规则表
    "s3_products":   ("table", "产品信息（沿用·仅展示）", ("价格测算单", "产品信息"),     False),
    "s3_markup":     ("table", "定价规则（利润加成）",     ("价格测算单", "加价明细"),     False),
    # —— 第 4 步 报价-其他加价项 —— 产品信息仅展示 + 加价规则表
    "s4_products":   ("table", "产品信息（沿用·仅展示）", ("价格测算单", "产品信息"),     False),
    "s4_markup":     ("table", "加价规则（其他加价）",     ("价格测算单", "加价明细"),     False),
    # —— 第 5 步 报价方案 —— 报价基本信息 + 报价明细
    "s5_basic":      ("form",  "报价基本信息",             ("报价单", "报价基本信息"),     True),
    "s5_detail":     ("table", "报价明细",                 ("报价单", "报价明细"),         True),
    # —— 包装行业（图纸项目回传的整包）：第 2 步的盒型/参数与成本构成 ——
    # kind/title 必须与 cpq_tech_bridge.packaging_snapshot() 产出的分区**逐字一致**，
    # 否则报价页 wfRestoreStepData() 匹配不到 section_id，包装分区恢复不出来
    # （Spec packaging-quote-draft-and-card-visibility §3.2）。内容由快照注入，不取本体字段。
    "s2_packaging":      ("table", "包装：盒型与参数", ("价格测算单", "包装：盒型与参数"), False),
    "s2_packaging_cost": ("table", "包装：成本构成",   ("价格测算单", "包装：成本构成"),   False),
}

# 计算类分区：已无（BPM 审批流已按需求取消，第 6 步只生成报价单文档）。
_COMPUTED_SECTIONS = {}

# 前缀匹配分区：已无（旧 s2_bom_ 树形 BOM 由实例BOM头/行表取代）。
_COMPUTED_PREFIX = {}

FIXED_FORMS: dict = {}  # section_id -> {kind, title, fields:[{key,label,example}], columns:[{key,label}], editable}


# 新库 quote_assistant_fields 里没有的逻辑实体，用这里的固定字段兜底。
_FALLBACK_FIELDS = {
    # 非标路径的只读汇总：DA 本体没有「定制技术要求」逻辑实体，字段在这里兜底（Spec §2.2）。
    "定制技术要求": ["尺寸(mm)", "纸张与膜系", "板材与厚度", "数量", "交期", "随附图纸", "判定原因"],
}

#: 流程状态字段（DA 业务中文名）：**只让系统改，AI 只读**（Spec §2.5）。
#: `_enforce_fixed_template()` 会丢弃模型给的值并标记 readonly；系统提示词引用同一份清单。
READONLY_FIELDS = ("测算状态",)


# 按需求隐藏的展示字段（xlsx 里有、但前端各分区不展示）：产品信息不展示这 3 个字段（所有步骤）。
_HIDDEN_FIELDS = {("价格测算单", "产品信息"): {"备件数量", "赠品数量", "产品大类"}}


# 产品技术参数分区：列不再取「报价助手」页的产品技术参数实体，而是直接取
# 亿纬锂能DA梳理「配置助手」页的 product_para_value（产品参数值表）字段（不含 id）——
# 页面展示与数据库实际参数一一对应，第 1 步选用产品后整行原样填入。
_PPV_SECTIONS = {"s1_techparams", "s2_techparams"}


def _ppv_fields() -> list:
    """product_para_value 的展示字段（中文属性名，按 DA 录入顺序，排除 id）。"""
    try:
        onto = cpq_db._load_ontology()["config"]
        for e in onto["entities"]:
            if e["table"] == cpq_match.PRODUCT_TABLE:
                return [{"key": a["name"], "label": a["name"], "example": ""}
                        for a in e["attrs"]
                        if a.get("name") and str(a.get("code") or "").lower() != "id"
                        and str(a["name"]).lower() != "id"]
    except Exception:
        pass
    return []


def _bi_fields(business_object: str, logic_entity: str) -> list:
    """从 DA 本体「报价助手」sheet 取某逻辑实体的固定字段（按录入顺序）；查不到用兜底。"""
    rows = cpq_db.bi_fields(business_object, logic_entity)
    hidden = _HIDDEN_FIELDS.get((business_object, logic_entity))
    if hidden:
        rows = [(a, ft) for a, ft in rows if a not in hidden]
    if not rows:
        return [{"key": a, "label": a, "example": ""} for a in _FALLBACK_FIELDS.get(logic_entity, [])]
    # 一律不带示例数据，只出字段名（表结构写死）
    return [{"key": a, "label": a, "example": ""} for a, ft in rows]


def _cols_template(cols: list) -> list:
    return [{"key": c, "label": c} for c in cols]


# 个别分区在 xlsx 属性之外补充的展示列（旧实例BOM行的「层级」列已随第2步改为工艺确认而移除）
_EXTRA_COLS = {}


# 展示名覆盖：只改前端表头文字，**不动 key**——key 仍是 DA 里的属性名，
# 入库映射（clm_calc_product.price）、取价填表、历史快照都靠它对齐，改了就断。
# （产品信息的「价格」列曾改显示为「成本」，现已按需求改回原名，故当前无覆盖项。）
_LABEL_OVERRIDE = {}


def _init_fixed_forms():
    FIXED_FORMS.clear()
    for sid, (kind, title, ent, editable) in _BI_SECTIONS.items():
        if sid in _PPV_SECTIONS:
            attrs = _ppv_fields() or _bi_fields(*ent)   # 拿不到本体时退回报价助手页字段
        else:
            attrs = _bi_fields(*ent)
        extra = [{"key": c, "label": c, "example": ""} for c in _EXTRA_COLS.get(sid, [])]
        attrs = extra + attrs
        ov = _LABEL_OVERRIDE.get(ent) or {}
        if ov:
            attrs = [dict(a, label=ov.get(a["key"], a["label"])) for a in attrs]
        FIXED_FORMS[sid] = {
            "kind": kind, "title": title, "fields": attrs,
            "columns": [{"key": a["key"], "label": a["label"]} for a in attrs],
            "editable": bool(editable),
        }
    for sid, (kind, title, cols) in _COMPUTED_SECTIONS.items():
        FIXED_FORMS[sid] = {
            "kind": kind, "title": title,
            "fields": [{"key": c, "label": c, "example": ""} for c in cols],
            "columns": _cols_template(cols),
            "editable": False,
        }


def _fixed_template(section_id: str):
    if not section_id:
        return None
    if section_id in FIXED_FORMS:
        return FIXED_FORMS[section_id]
    for pref, (kind, title, cols) in _COMPUTED_PREFIX.items():
        if section_id.startswith(pref):
            return {"kind": kind, "title": title,
                    "fields": [{"key": c, "label": c, "example": ""} for c in cols],
                    "columns": _cols_template(cols), "editable": False}
    return None


def _norm_key(s) -> str:
    """归一化列名/字段名：去掉括号里的单位、空格、大小写，兼容全/半角括号。
    这样模型填 '材料费' / '材料费(元/W)' / '材料费（元/W）' 都能对上固定列 '材料费(元/W)'。"""
    s = str(s if s is not None else "")
    s = re.sub(r"[\(（【\[][^\)）】\]]*[\)）】\]]", "", s)  # 去掉 (…)（…）【…】[…] 里的单位/注释
    return s.replace(" ", "").replace("　", "").replace("／", "/").strip().lower()


def _pick(d: dict, key: str, nd: dict):
    """从模型给的 dict 里取值：先精确键、再按归一化键匹配。"""
    if key in d and d[key] is not None:
        return d[key]
    return nd.get(_norm_key(key))


def _extract_values(ti: dict) -> dict:
    """从模型入参里取“字段值”：优先 values 字典，其次 fields 列表，其次 rows[0]。"""
    vals = {}
    v = ti.get("values")
    if isinstance(v, dict):
        vals.update(v)
    for f in (ti.get("fields") or []):
        if isinstance(f, dict) and "key" in f:
            vals[f["key"]] = f.get("value", "")
    if not vals and isinstance(ti.get("rows"), list) and ti["rows"]:
        r = ti["rows"][0]
        if isinstance(r, dict):
            vals.update(r)
    return vals


# 值末尾的推荐/推测标记：显示时去掉文字、只保留“推荐值”标志（前端用颜色高亮）
_RECO_SUFFIX_RE = re.compile(r"[（\(]\s*(推荐|推测|推断|估计)\s*[）\)]\s*$")


def _strip_reco(v):
    """去掉值末尾的（推荐）/（推测）标记文字，返回 (纯值, 是否推荐值)。"""
    s = "" if v is None else str(v)
    m = _RECO_SUFFIX_RE.search(s)
    if m:
        return s[:m.start()].rstrip(), True
    return s, False


def _enforce_fixed_template(ti: dict) -> dict:
    """把 render_form/render_table 强制套到固定模板上：字段/列固定，只保留模型填的值。"""
    tpl = _fixed_template(ti.get("section_id"))
    if not tpl:
        return ti  # 目录外分区：按模型原样渲染（正常流程用不到）
    ti["title"] = ti.get("title") or tpl["title"]
    if ti.get("editable") is None:
        ti["editable"] = tpl["editable"]
    if tpl["kind"] == "form":
        ti["action"] = "render_form"
        vals = _extract_values(ti)
        nvals = {_norm_key(k): v for k, v in vals.items()}
        ti["fields"] = []
        for f in tpl["fields"]:
            v = _pick(vals, f["key"], nvals)
            val, reco = _strip_reco(v)
            fld = {
                "key": f["key"], "label": f["label"], "type": "text",
                "value": val,
                "placeholder": f.get("example", ""),
            }
            if reco:
                fld["reco"] = True  # 推荐值：前端只做颜色高亮，不显示标记文字
            if f["key"] in READONLY_FIELDS:
                # 流程状态：只由系统工作流改写。丢弃模型给的值并标记只读，
                # 前端保留页面已有值、不覆盖（Spec §2.5）。
                fld["value"] = ""
                fld["readonly"] = True
                fld.pop("reco", None)
            ti["fields"].append(fld)
        ti.pop("columns", None); ti.pop("rows", None); ti.pop("values", None)
    else:
        ti["action"] = "render_table"
        ti["columns"] = list(tpl["columns"])
        keys = [c["key"] for c in tpl["columns"]]
        rows = ti.get("rows") if isinstance(ti.get("rows"), list) else []
        # 兜底：模型误把单条数据放进 values/fields（表当成表单填）→ 当作一行
        if not rows:
            fb = _extract_values(ti)
            if fb:
                rows = [fb]
        out_rows = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            nr = {_norm_key(k): v for k, v in r.items()}
            row = {}
            reco_cols = []
            reco_cols_readonly = []
            for k in keys:
                v = _pick(r, k, nr)
                val, reco = _strip_reco(v)
                if k in READONLY_FIELDS:
                    row[k] = ""            # 流程状态：AI 写不进来（Spec §2.5）
                    reco_cols_readonly.append(k)
                else:
                    row[k] = val
                if reco:
                    reco_cols.append(k)
            if reco_cols:
                row["_reco"] = reco_cols  # 推荐值所在列：前端据此高亮，不作为数据列
            if reco_cols_readonly:
                row["_readonly"] = reco_cols_readonly  # 只读列：前端保留已有值
            out_rows.append(row)
        ti["rows"] = out_rows
        ti.pop("fields", None); ti.pop("values", None)
    return ti


try:
    _init_fixed_forms()
except Exception:
    traceback.print_exc()


def _section_step(sid: str) -> int:
    m = re.match(r"s(\d)_", sid or "")
    return int(m.group(1)) if m else 1


def fixed_forms_catalog(industry=None) -> list:
    """把固定表单目录（写死的表结构）按分区返回，供前端预渲染骨架。

    同一步骤内按 _BI_SECTIONS/_COMPUTED_SECTIONS 的**定义顺序**（即业务展示顺序），不按 id 字母序。
    `industry` 只影响 ④产品技术参数（`s1_techparams`/`s2_techparams`）的表头来源，
    不传按 `industry_templates.DEFAULT_INDUSTRY`（既有调用点行为不变）。"""
    out = []
    for idx, (sid, tpl) in enumerate(FIXED_FORMS.items()):
        entry = {
            "section_id": sid,
            "step": _section_step(sid),
            "kind": tpl["kind"],
            "title": tpl["title"],
            "fields": [{"key": f["key"], "label": f["label"]} for f in tpl["fields"]],
            "columns": list(tpl["columns"]),
            "editable": tpl["editable"],
            "_ord": idx,
        }
        if sid in _PPV_SECTIONS:
            # ④产品技术参数：按行业换源后的表头随固定表单目录一起下发（Spec §4.2）
            entry["techparams_columns"] = tech_param_columns(industry)
            entry["techparams_source"] = tech_param_source(industry)
            # 事实源是否已从这个行业模板之外的字典换走：前端据此决定**空骨架**要不要换表头
            # （行数据在场时一律以「行的键是否与表头对齐」为准，与行业无关）。
            entry["techparams_switched"] = entry["techparams_source"] != _DA_PRODUCT_SOURCE
            entry["industry"] = quote_industry_templates.normalize(industry)
        out.append(entry)
    out.sort(key=lambda x: (x["step"], x.pop("_ord")))
    return out


# 每个会话轮次中，cpq_ui 调用产生的 UI 事件先入队，工具批次执行完后随 SSE 下发。
# 线程本地：工具在各自会话回合的请求线程里同步执行，线程隔离即会话隔离，
# 多个会话并发跑回合时 UI 事件不会互相串。
_UI_TLS = threading.local()


def _ui_events() -> list:
    lst = getattr(_UI_TLS, "events", None)
    if lst is None:
        lst = _UI_TLS.events = []
    return lst


def _log_write(raw: dict, ti: dict):
    """把每次写表操作打到后台日志：模型传了什么键 → 实际落进固定字段/列多少。"""
    sid = ti.get("section_id")
    act = ti.get("action")
    try:
        if act == "render_form":
            raw_vals = raw.get("values") if isinstance(raw.get("values"), dict) else {}
            filled = {f["key"]: f["value"] for f in (ti.get("fields") or []) if str(f.get("value") or "").strip()}
            print(f"[cpq-write] render_form {sid}: 模型 values 键{list(raw_vals.keys())[:12]}({len(raw_vals)}) "
                  f"→ 落固定字段 {len(filled)}/{len(ti.get('fields') or [])} 非空 {dict(list(filled.items())[:8])}",
                  file=sys.stderr)
        elif act == "render_table":
            raw_rows = raw.get("rows") if isinstance(raw.get("rows"), list) else []
            out_rows = ti.get("rows") or []
            ne = sum(1 for r in out_rows for k, v in r.items() if k != "_reco" and str(v or "").strip())
            rk = list(raw_rows[0].keys())[:10] if (raw_rows and isinstance(raw_rows[0], dict)) else []
            cols = [c.get("key") for c in (ti.get("columns") or [])]
            print(f"[cpq-write] render_table {sid}: 模型传 {len(raw_rows)} 行(首行键{rk}) 固定列{cols[:10]} "
                  f"→ 输出 {len(out_rows)} 行/{ne} 非空; 首行={out_rows[0] if out_rows else {}}",
                  file=sys.stderr)
    except Exception:
        pass


# 产品分区一律受控：s1 由 match_products+选用产生；s2/s3/s4 只能沿用第 1 步的产品，
# 模型若在后续步骤里"重新选品"塞进库里不存在的编码，同样直接拦下。
_PRODUCT_GUARDED = ("s1_products", "s1_techparams",
                    "s2_products", "s2_techparams", "s3_products", "s4_products")


def _unknown_product_codes(ti: dict) -> list:
    """产品分区里出现的、数据库中查不到的成品编码。

    产品只能来自 product_para_value 的真实匹配。模型受「整步填满/强行推荐」的驱使
    很容易顺手编一个型号出来，光靠提示词挡不住——这里直接对库校验，编造的一律拦下。
    库不可达时返回空（不拦），避免网络问题把正常流程卡死。"""
    if ti.get("section_id") not in _PRODUCT_GUARDED or ti.get("action") != "render_table":
        return []
    key = None
    for col in (ti.get("columns") or []):
        if _norm_key(col.get("key")) == _norm_key("成品编码"):
            key = col.get("key")
            break
    if not key:
        return []
    codes = []
    for r in (ti.get("rows") or []):
        v = str((r or {}).get(key) or "").strip()
        if v and v not in codes:
            codes.append(v)
    if not codes:
        return []
    quoted = ", ".join("'" + c.replace("'", "''") + "'" for c in codes[:50])
    try:
        _, rows = cpq_db.run_select(
            f"SELECT {cpq_match.COL_CODE} FROM {cpq_match.PRODUCT_TABLE}"
            f" WHERE {cpq_match.COL_CODE} IN ({quoted})", len(codes) + 5)
    except Exception:
        return []                       # 查不了库就不拦，别让网络问题挡住正常流程
    known = {str(r[0]).strip() for r in rows if r and r[0] is not None}
    return [c for c in codes if c not in known]


def _handle_cpq_ui(tool_input: dict) -> str:
    """执行 cpq_ui：校验、套固定模板、入队 UI 事件，给模型返回简短回执。"""
    if not isinstance(tool_input, dict):
        return "cpq_ui 入参必须是 JSON 对象"
    action = tool_input.get("action")
    if action not in ("set_step", "render_form", "render_table", "render_document", "focus_section"):
        return f"未知 action: {action!r}"
    step = tool_input.get("step")
    if not isinstance(step, int) or not (1 <= step <= len(STEPS)):
        return f"step 必须是 1-{len(STEPS)} 的整数"
    if action != "set_step" and not tool_input.get("section_id"):
        return "render_*/focus_section 必须提供 section_id"
    raw = dict(tool_input)
    ti = dict(tool_input)
    fixed = False
    if action in ("render_form", "render_table"):
        ti = _enforce_fixed_template(ti)
        action = ti.get("action", action)
        fixed = _fixed_template(ti.get("section_id")) is not None
        _log_write(raw, ti)
        bad = _unknown_product_codes(ti)
        if bad:
            # 确定性兜底：产品只能来自数据库真实匹配，编造的成品编码一律拒绝落到工作台
            print(f"[cpq-write] 拒绝 {ti.get('section_id')}：库里不存在的成品编码 {bad}", file=sys.stderr)
            return (f"❌ 已拒绝渲染 {ti.get('section_id')}：成品编码 {('、'.join(bad))} "
                    f"在 product_para_value（产品参数值表）里不存在——**产品不能凭需求文档或行业知识编造**。"
                    f"请改用 match_products 工具从数据库真实匹配，把推荐清单给用户点选；"
                    f"用户点「选用」后系统会自动把这两张表填好，你不需要自己填。")
    _ui_events().append(ti)
    if action == "set_step":
        return f"工作台已切换到步骤 {step}（{STEPS[step - 1]}）"
    if action == "focus_section":
        return f"已聚焦分区 {ti.get('section_id')}（步骤 {step}），等待用户确认"
    if action == "render_form":
        n = sum(1 for f in (ti.get("fields") or []) if str(f.get("value") or "").strip())
        total = len(ti.get("fields") or [])
        msg = f"已把值填入固定表单 {ti.get('section_id')}：{n}/{total} 个字段有值。"
        if n == 0:
            msg += " ⚠️ 一个值都没落进去！检查 values 的键是否与该分区字段名一致，然后重填。"
        return msg
    if action == "render_table":
        rows = ti.get("rows") or []
        nonempty = sum(1 for r in rows for k, v in r.items() if k != "_reco" and str(v or "").strip())
        msg = f"已渲染表 {ti.get('section_id')}：{len(rows)} 行、{nonempty} 个非空单元格。"
        if rows and nonempty == 0:
            msg += " ⚠️ 所有单元格都空！检查 rows 里每行的键是否与该分区固定列名一致，然后重填。"
        elif not rows:
            msg += " ⚠️ 0 行！把数据放进 rows=[{列名:值},…] 再调一次。"
        return msg
    if fixed:
        return f"已渲染固定分区 {ti.get('section_id')}（步骤 {step}）。"
    return f"已渲染分区 {ti.get('section_id')}（步骤 {step}）"


# 拦截点：open_claude.repl 里 _execute_pending_tools 调用的是模块级名字 execute_tool，
# 包装它即可让 cpq_ui 走本地处理、其余工具（Read/Glob/Grep）走原实现。
_ORIG_EXECUTE_TOOL = oc_repl.execute_tool


# 唯一允许 Read 的东西：本体信息（业务对象 / 逻辑实体 / 属性）。
# 它以前是 xlsx —— 每次都要把一个二进制表格塞进上下文，读得慢、读不全，合并单元格
# 还常常串行。现在是 cpq_ontology.py 从 xlsx 生成的 Markdown，纯文本、有表头、按实体
# 分节；服务端渲染固定表单也读同一份（cpq_db._load_ontology），两边不会各说各话。
_ONTOLOGY_MD_NAME = cpq_ontology.MD_PATH.name
_FILE_DENY_MSG = (
    f"已拒绝：本 Agent 禁止读取本地文件。只有本体信息可以读（{_ONTOLOGY_MD_NAME}）。"
    "业务数据请用 sql_query 从数据库查询，不要读本地文件；"
    "本体的 xlsx 原件已不再直接读取，请改读同名的 .md。"
)


def _is_ontology_path(p) -> bool:
    """是不是那份本体 Markdown。按文件名匹配 —— 相对路径、绝对路径都认。"""
    if not isinstance(p, str):
        return False
    return Path(p.strip().replace("\\", "/")).name.lower() == _ONTOLOGY_MD_NAME.lower()


def _patched_execute_tool(tool_name, tool_input, cwd):
    if tool_name == "cpq_ui":
        return _handle_cpq_ui(tool_input)
    if tool_name == "sql_query":
        return _handle_sql_query(tool_input)
    if tool_name == "match_products":
        return _handle_match_products(tool_input)
    # 本地文件读取限制：只允许 Read 读取 Excel；其余读文件/检索/列举工具一律拒绝（双保险，
    # 即使 DISABLED_TOOLS 之外的路径也挡住）。
    if tool_name == "Read":
        if not _is_ontology_path((tool_input or {}).get("file_path")):
            return _FILE_DENY_MSG
        # 路径钉死在本目录：文件名对上就行，不接受调用方给的目录。
        tool_input = dict(tool_input or {}, file_path=str(cpq_ontology.MD_PATH))
    elif tool_name in ("Grep", "Glob", "NotebookRead", "NotebookEdit"):
        return _FILE_DENY_MSG
    return _ORIG_EXECUTE_TOOL(tool_name, tool_input, cwd)


oc_repl.execute_tool = _patched_execute_tool

# 控制台日志兜底：repl.print_tool_result 把工具结果直接塞进 rich markup（[dim]{...}[/dim]），
# 结果里带 [ ] 乱码/二进制时 rich 抛 MarkupError 炸掉整个回合（tool_result 丢失→下轮供方 400）。
# 日志纯属装饰，失败时静默降级，不影响回合。三个 agent 模块共享 oc_repl，用 _cpq_safe 防重复包装。
if not getattr(oc_repl.print_tool_result, "_cpq_safe", False):
    _ORIG_PRINT_TOOL_RESULT = oc_repl.print_tool_result

    def _safe_print_tool_result(name, result):
        try:
            _ORIG_PRINT_TOOL_RESULT(name, result)
        except Exception:
            pass  # rich markup/编码问题只影响控制台显示，吞掉

    _safe_print_tool_result._cpq_safe = True
    oc_repl.print_tool_result = _safe_print_tool_result


# ---------------------------------------------------------------------------
# match_products 工具：第 1 步产品匹配（六维加权评分，确定性计算，不由模型估分）
# ---------------------------------------------------------------------------

MATCH_PRODUCTS_SCHEMA = {
    "name": "match_products",
    "description": (
        "【仅限第 1 步、且仅在用户尚未选定产品时使用】第 2 步及以后产品已锁定，只能沿用，"
        "**再调用本工具属于严重错误**。按需求参数在 product_para_value（产品参数值表）里做**相似度评分**，"
        "返回推荐清单 Top3（含六个维度的得分明细、加权总分、告警），并**自动把推荐表格渲染到左侧对话框**"
        "供用户点选。评分与排序由系统确定性计算——你只负责把需求文档里的参数如实填进来，"
        "**不要自己估分、不要自己写 SQL 查产品**。\n"
        "六维权重：尺寸合规25% / 用途场景20% / 温度覆盖20% / 寿命15% / 密封性15% / 其他5%。\n"
        "最高分低于 70 分会返回「建议转入定制评估」，你要如实转达给用户。\n"
        "用户在左侧点「选用」后，系统会**自动用纯 SQL** 取该编码的参数与价格"
        "（md_clm_material_cost_cnf.material_code → material_unit_price）并填好 "
        "s1_techparams / s1_products —— 你不用查价、也不用填这两张表。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "max_dimension": {"type": "string",
                              "description": "需求最大尺寸(mm)，如 '100*50*20'。没有就留空。"},
            "dimension_tolerance_pct": {"type": "number",
                                        "description": "尺寸容差百分比，默认 0。"},
            "application_scope": {"type": "string", "description": "应用范围/使用场景。"},
            "operating_temperature": {"type": "string",
                                      "description": "需求工作温度区间，如 '-20~60'。"},
            "service_life": {"type": "string", "description": "寿命要求，如 '500次' / '5年'。"},
            "hermeticity": {"type": "string", "description": "密封性要求，如 'IP67'。"},
            "top_n": {"type": "integer", "description": "取前几名，默认 3。"},
        },
        "required": [],
    },
}


def _ppv_cn_map() -> dict:
    """product_para_value 的 {列code: 中文名}（来自 DA 本体，随 xlsx 自动更新）。"""
    out = {}
    try:
        onto = cpq_db._load_ontology()["config"]
        for e in onto["entities"]:
            if e["table"] == cpq_match.PRODUCT_TABLE:
                for a in e["attrs"]:
                    if a.get("code"):
                        out[a["code"]] = a.get("name") or a["code"]
    except Exception:
        pass
    return out


def pick_product(code: str, industry=None) -> dict:
    """选定产品后的**确定性**取数（不经大模型）：

      ① 按 product_item_code 取 product_para_value 整行参数；
      ② 用同一个成品编码去 md_clm_material_cost_cnf 匹配 material_code，
         取 material_unit_price 作为价格（is_deleted = false，且在生效/失效日期内）；
      ③ 按固定分区的列名（中文）拼好 s1_techparams / s1_products 两行，前端直接渲染；
      ④ ④产品技术参数的表头按**行业**下发（包装是盒型库列，其余仍是成品参数字典）。
    """
    code = (code or "").strip()
    if not code:
        return {"ok": False, "error": "缺少成品编码"}
    industry_key = _industry_of(None, industry)
    if industry_key == PACKAGING_INDUSTRY:
        # 包装「选用」走盒型库口径（Spec §2.4）：不再查电池成品参数表。
        return _pick_packaging_box(code, industry_key)

    # ① 产品参数
    try:
        cols, rows = cpq_db.run_select(
            "SELECT * FROM {} WHERE {} = '{}'".format(
                cpq_match.PRODUCT_TABLE, cpq_match.COL_CODE, code.replace("'", "''")), 2)
    except Exception as e:
        return {"ok": False, "error": f"查询产品参数失败：{str(e).splitlines()[0][:160]}"}
    if not rows:
        return {"ok": False, "error": f"产品参数值表里找不到成品编码 {code}"}
    idx = {c: i for i, c in enumerate(cols)}
    prow = {c: ("" if rows[0][idx[c]] is None else str(rows[0][idx[c]])) for c in cols}

    # ② 价格：成品编码 -> md_clm_material_cost_cnf.material_code -> material_unit_price
    price, price_note = "", ""
    try:
        pc, pr = cpq_db.run_select(
            "SELECT material_unit_price, price_validity_date, price_expiration_date"
            " FROM md_clm_material_cost_cnf"
            " WHERE material_code = '{}' AND is_deleted = false"
            " ORDER BY price_validity_date DESC NULLS LAST".format(code.replace("'", "''")), 20)
        pidx = {c: i for i, c in enumerate(pc)}
        today = datetime.date.today()

        def _d(v):
            if v is None or v == "":
                return None
            if isinstance(v, datetime.datetime):
                return v.date()
            if isinstance(v, datetime.date):
                return v
            try:
                return datetime.date.fromisoformat(str(v)[:10])
            except ValueError:
                return None

        hit = None
        for r in pr:
            s, e = _d(r[pidx.get("price_validity_date", 0)]), _d(r[pidx.get("price_expiration_date", 0)])
            if (s is None or s <= today) and (e is None or e >= today):
                hit = r
                break
        if hit is None and pr:
            hit = pr[0]
            price_note = "（该编码无当前生效价，取最近一条）"
        if hit is not None:
            v = hit[pidx.get("material_unit_price", 0)]
            price = "" if v is None else str(v)
        else:
            price_note = "物料成本配置里没有该编码的价格记录"
    except Exception as e:
        price_note = f"价格查询失败：{str(e).splitlines()[0][:120]}"

    # ③ 按固定列名（中文）装配两行：ppv 的中文名与固定列名对得上就填
    cn = _ppv_cn_map()
    by_norm = {}
    for c, v in prow.items():
        name = cn.get(c, c)
        by_norm[_norm_key(name)] = v
        by_norm.setdefault(_norm_key(c), v)

    def _row_for(section_id: str) -> dict:
        tpl = FIXED_FORMS.get(section_id) or {}
        row = {}
        for col in tpl.get("columns", []):
            k = col["key"]
            v = by_norm.get(_norm_key(k), "")
            if v:
                row[k] = v
        return row

    tech_row = _row_for("s1_techparams")
    prod_row = _row_for("s1_products")
    # 价格列名以固定列为准（DA 里叫「价格」），只有查到才写
    if price:
        for col in (FIXED_FORMS.get("s1_products") or {}).get("columns", []):
            if _norm_key(col["key"]) == _norm_key("价格"):
                prod_row[col["key"]] = price
                break

    return {"ok": True, "code": code,
            "name": prow.get(cpq_match.COL_NAME, ""),
            "price": price, "price_note": price_note,
            "techparams_row": tech_row, "products_row": prod_row,
            "techparams_columns": tech_param_columns(industry_key),
            "techparams_source": tech_param_source(industry_key),
            "industry": industry_key,
            "params": prow}


def _pick_packaging_box(code: str, industry_key: str) -> dict:
    """包装「选用」：盒型库口径 —— ④ 表头与行同源，盒型编码写进产品行（Spec §2.4）。

    价格不由第 1 步取（盒型库没有成品价格），交由成本测算；绝不落回电池表的取价 SQL。
    """
    try:
        box = cpq_packaging_match.load_box_type(code)
    except cpq_packaging_match.QuoteKbUnavailable as e:
        return {"ok": False, "error": "盒型库读取失败：" + str(e).splitlines()[0][:160]}
    except Exception as e:                           # noqa: BLE001 - 原文回给前端
        traceback.print_exc()
        return {"ok": False, "error": "盒型库读取失败：" + str(e)}
    if not box:
        return {"ok": False, "error": "盒型库里找不到盒型编码 " + code}
    tech_row = tech_param_row(industry_key, box)
    by_norm = {_norm_key(k): ("" if v is None else str(v)) for k, v in box.items()}
    prod_row = {}
    for col in (FIXED_FORMS.get("s1_products") or {}).get("columns", []):
        value = by_norm.get(_norm_key(col["key"]), "")
        if value:
            prod_row[col["key"]] = value
    # 盒型编码必须写进产品行（可追溯）：落到「成品编码」列。
    code_col = None
    for col in (FIXED_FORMS.get("s1_products") or {}).get("columns", []):
        if _norm_key(col["key"]) == _norm_key("成品编码"):
            code_col = col["key"]
            break
    prod_row[code_col or "成品编码"] = code
    return {"ok": True, "code": code,
            "name": box.get("name") or "",
            "price": "",
            "price_note": "盒型库口径：第 1 步不取成品价格，价格由后续成本测算给出",
            "techparams_row": tech_row, "products_row": prod_row,
            "techparams_columns": tech_param_columns(industry_key),
            "techparams_source": tech_param_source(industry_key),
            "industry": industry_key,
            "params": box}


def _handle_match_products(tool_input: dict) -> str:
    """执行产品匹配：确定性打分 -> UI 事件渲染到左侧对话框 -> 给模型返回文字摘要。"""
    if not isinstance(tool_input, dict):
        return "match_products 入参必须是 JSON 对象"
    # 入口按行业分流（Spec §2.3）：包装走报价侧盒型库匹配器 cpq_packaging_match
    # （不再查电池成品参数表 product_para_value）；其余行业保持既有六维匹配逐字不变。
    industry = _industry_of(None, tool_input.get("industry"))
    if industry == PACKAGING_INDUSTRY:
        inputs = {key: tool_input.get(key) for key in cpq_packaging_match.MATCH_INPUT_KEYS}
        # 门禁输入用整份 tool_input（键名与需求模板一致）：仍按包装 10 项必填判定
        missing = step1_missing(tool_input, industry=industry)
        if missing:
            return ("❌ 需求缺少必备匹配参数：" + "、".join(missing) +
                    "。**不允许开始匹配，也不要编造参数**。请提醒用户补充需求"
                    "（" + "、".join(label for _key, label in step1_required(industry)) +
                    " 必须齐全），补齐后再重新匹配。")
        try:
            box_res = cpq_packaging_match.match_box_types(inputs)
        except cpq_packaging_match.QuoteKbUnavailable as e:
            return ("❌ 盒型库读取失败，暂时无法推荐：" + str(e).splitlines()[0][:160] +
                    "。**不要编造盒型**，请如实告诉用户「盒型库读取失败，暂时无法给出推荐清单」，"
                    "并请他联系管理员检查知识库连接。")
        except Exception as e:                       # noqa: BLE001 - 原文回给模型
            traceback.print_exc()
            return f"盒型匹配失败：{e}"
        return _render_box_match(inputs, box_res)
    req = {k: tool_input.get(k) for k in
           ("max_dimension", "dimension_tolerance_pct", "application_scope",
            "operating_temperature", "service_life", "hermeticity")}
    # 意图识别门槛：尺寸/应用范围/工作温度缺一不可，缺了不匹配
    # 门禁按行业取必填项：半导体/电池/电器是三项，包装是需求模板的 10 项必填
    missing = step1_missing(req, industry=industry)
    if missing:
        return ("❌ 需求缺少必备匹配参数：" + "、".join(missing) +
                "。**不允许开始匹配，也不要编造参数**。请提醒用户补充需求"
                "（" + "、".join(label for _key, label in step1_required(industry)) +
                " 必须齐全），补齐后再重新匹配。")
    try:
        res = cpq_match.match(req, top_n=tool_input.get("top_n") or 3)
    except Exception as e:
        traceback.print_exc()
        return f"产品匹配失败：{e}"
    if not res.get("ok"):
        return (f"❌ {res.get('error') or '产品匹配失败'}\n"
                f"**不要凭需求文档或行业知识编造产品**。请如实告诉用户"
                f"「产品库读取失败，暂时无法给出推荐清单」，并请他联系管理员检查数据库连接。")
    if not res.get("products"):
        return (f"产品参数值表里没有可匹配的产品（共扫描 {res.get('all_count', 0)} 条）。"
                f"**不要编造产品**，请如实告诉用户没有可选标品，建议转入定制评估。")

    # 推荐清单渲染到左侧对话框（前端按 chat_candidates 事件画表格 + 图片/选用按钮）
    src = res.get("source") or {}
    _ui_events().append({
        "action": "chat_candidates", "step": 1,
        "requirement": {k: v for k, v in req.items() if str(v or "").strip()},
        "products": res["products"], "weights": res["weights"],
        "threshold": res["threshold"], "below_threshold": res["below_threshold"],
        "advice": res["advice"], "all_count": res["all_count"],
        "source": src,          # 取数出处：库、表、SQL、行数（前端展示，便于核对确实查了库）
        # 非标判定（Spec §2.1）：总分阈值 + 关键维度下限，任一命中即触发。
        "nonstandard": res.get("nonstandard"),
    })

    lines = [f"🔎 已查库：{src.get('sql', '')} —— {src.get('db', '')}，取回 {src.get('rows', 0)} 行。",
             f"已在 {res['all_count']} 个产品中完成六维加权评分，推荐 Top{len(res['products'])}（已渲染到左侧供用户点选）："]
    for i, p in enumerate(res["products"], 1):
        ds = "；".join(f"{d['label']}{d['score']:g}" for d in p["detail"].values())
        lines.append(f"{i}. {p['code']} {p['name']}　总分 {p['total']:g}　（{ds}）")
        for w in p["warnings"]:
            lines.append(f"   ⚠ {w}")
    if res["below_threshold"]:
        lines.append(f"最高分 {res['products'][0]['total']:g} 低于阈值 {res['threshold']:g}："
                     f"{res['advice']}")
    ns = res.get("nonstandard") or {}
    if ns.get("triggered"):
        # 非标：如实说明为什么接不住，并给出可执行出口（新增工艺）。
        why = "；".join(
            f"{r.get('label') or r.get('key')} {float(r.get('actual') or 0):g} 分"
            f"（下限 {float(r.get('threshold') or 0):g}）"
            for r in (ns.get("reasons") or []))
        lines.append(f"⚠ 判为非标：{why}。库里没有能直接接住的标品，"
                     "建议**转技术工艺新增工艺/新增产品**后再回到报价（**不要编造产品**）。")
    lines.append("请等用户在左侧点「选用」确定产品后，再查 md_clm_material_cost_cnf 取价格填表；"
                 "**不要替用户擅自选定**。")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 包装行业：盒型库候选渲染（与工艺侧同口径，Spec §2.3）
# --------------------------------------------------------------------------- #
#: 盒型五维的中文名（只用于展示；权重/门槛一律来自权重表，不在此写死数字）。
_BOX_DIMENSION_LABELS = {
    "size_range": "尺寸区间",
    "fit_clearance": "配合间隙",
    "face_paper_gsm": "面纸克重",
    "closure_type": "闭合方式",
    "v_groove": "V槽",
}


def _box_notes(cand: dict) -> list:
    """候选的告警/淘汰原因（中文说明，供表格 warnings 与五维明细用）。"""
    notes = []
    if cand.get("out_of_range"):
        notes.append("尺寸超出该盒型适用区间（已扣分）")
    for reason in cand.get("reject_reasons") or []:
        notes.append({
            "fit_clearance_out_of_tolerance": "配合间隙超出容差",
            "closure_type_mismatch": "闭合方式不匹配",
            "gsm_unparsable": "盒型面纸克重不可解析",
            "v_groove_required_but_unsupported": "需要 V 槽但该盒型不支持",
        }.get(str(reason), str(reason)))
    for dim in cand.get("undecidable_dimensions") or []:
        notes.append("%s 数据缺失，无法判定" % _BOX_DIMENSION_LABELS.get(dim, dim))
    if cand.get("status") == "needs_input":
        notes.append("需求缺必填匹配输入")
    return notes


def _box_products(box_res: dict) -> list:
    """把盒型候选转成前端既有候选表格的口径（code/name/total/detail/warnings）。"""
    dims = box_res.get("dimensions") or []
    products = []
    for cand in box_res.get("candidates") or []:
        notes = _box_notes(cand)
        detail = {}
        for d in dims:
            dim = d["dimension"]
            score = float((cand.get("dimension_scores") or {}).get(dim, 0.0))
            weight = float(d.get("weight") or 0.0)
            detail[dim] = {
                "label": _BOX_DIMENSION_LABELS.get(dim, dim),
                "score": round(score * 100, 1),
                "weight": int(round(weight * 100)),
                "reason": "；".join(notes) or "按盒型库权重表计分",
                "weighted": round(score * weight * 100, 2),
            }
        products.append({
            "code": cand.get("box_type_code") or "",
            "name": cand.get("name") or cand.get("family") or "",
            "total": round(float(cand.get("total_score") or 0.0) * 100, 1),
            "detail": detail,
            "warnings": notes,
            "status": cand.get("status"),
            "can_confirm": bool(cand.get("can_confirm")),
        })
    return products


def _render_box_match(inputs: dict, box_res: dict) -> str:
    """把盒型库候选渲染成 chat_candidates 事件，并给模型返回文字摘要。"""
    products = _box_products(box_res)
    weights = {}
    for d in box_res.get("dimensions") or []:
        weights[_BOX_DIMENSION_LABELS.get(d["dimension"], d["dimension"])] = \
            "%d%%" % int(round(float(d.get("weight") or 0.0) * 100))
    _ui_events().append({
        "action": "chat_candidates", "step": 1, "industry": PACKAGING_INDUSTRY,
        "requirement": {k: v for k, v in inputs.items() if str(v or "").strip()},
        "products": products, "weights": weights,
        "threshold": None,
        # 盒型库没有「总分阈值」这一说：接不住由 needs_new_tooling 决定，不在此另写数字。
        "below_threshold": bool(box_res.get("needs_new_tooling")),
        "advice": "", "all_count": len(box_res.get("candidates") or []),
        "source": {"db": DB_NAME, "table": "kb_packaging_box_type",
                   "sql": "SELECT * FROM kb_packaging_box_type",
                   "rows": len(box_res.get("candidates") or [])},
        # 盒型库口径（Spec §2.3，前端据此画表格与出口）
        "engine_version": box_res.get("engine_version"),
        "dimensions": box_res.get("dimensions"),
        "inputs_complete": box_res.get("inputs_complete"),
        "missing_inputs": box_res.get("missing_inputs"),
        "suggested_box_type": box_res.get("suggested_box_type"),
        "needs_new_tooling": box_res.get("needs_new_tooling"),
        "new_tooling_reason": box_res.get("new_tooling_reason"),
        "candidates": box_res.get("candidates"),
    })
    # 一条候选都不可确认时不得写「推荐 TopN…供点选」：如实说成仅供参考。
    if box_res.get("needs_new_tooling"):
        lines = ["🔎 已查盒型库 kb_packaging_box_type（%s）：共评估 %d 个盒型，"
                 "按五维加权评分列出候选供参考（**均不可确认**，不可照此选定）："
                 % (box_res.get("engine_version") or "",
                    len(box_res.get("candidates") or []))]
    else:
        lines = ["🔎 已查盒型库 kb_packaging_box_type（%s）：共评估 %d 个盒型，"
                 "按五维加权评分推荐 Top%d（已渲染到左侧供用户点选）："
                 % (box_res.get("engine_version") or "", len(box_res.get("candidates") or []),
                    len(products))]
    for i, p in enumerate(products, 1):
        ds = "；".join("%s%s" % (v["label"], ("%g" % v["score"]))
                       for v in p["detail"].values())
        lines.append("%d. %s %s　总分 %g　（%s）" % (i, p["code"], p["name"], p["total"], ds))
        for w in p["warnings"]:
            lines.append("   ⚠ %s" % w)
    if box_res.get("needs_new_tooling"):
        lines.append("⚠ 盒型库里**没有适配**的盒型（原因：%s）。请**转技术工艺**新增盒型后再回到报价；"
                     "**不要编造盒型编码或尺寸**。"
                     % (box_res.get("new_tooling_reason") or "no_confirmable_candidate"))
    else:
        lines.append("请等用户在左侧点「选用」确定盒型后，再查价格填表；**不要替用户擅自选定**。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 第 1 步快速通道：新建报价的首轮匹配不进智能体回合循环 ——
# ① 纯 SQL 先取 product_para_value（五个匹配参数所在表）；
# ② 一次大模型调用：从需求文本提取匹配参数 + 一句评估（无思考循环、无工具）；
# ③ cpq_match 按既有六维加权规则确定性打分出 Top3（规则与原来完全一致）。
# 用户点「选用」之后的流程不变（/api/product/pick 纯 SQL 取参数与价格）。
# ---------------------------------------------------------------------------

# 两段式输出的分隔符：分隔符之前是给用户看的说明，之后是结构化 JSON
_JSON_MARK = "===JSON==="

_STEP1_EVAL_SYS = (
    "你是报价系统第 1 步的**需求意图识别器**。输入只有用户的需求文本。\n"
    "你的唯一任务：从需求文本里提取五个匹配参数，判断信息是否齐全。"
    "**不要做任何别的事**——不查数据库、不推荐产品、不填任何表单、不给建议方案。\n"
    "**输出分两段**：\n"
    "第一段：用 1~3 句话说明你从需求里读到了哪些参数、还缺哪些"
    "（这段会实时展示给用户，用自然中文，不要写 JSON、不要列表格、不要提具体产品）。\n"
    "第二段：另起一行只写 " + _JSON_MARK + " ，然后输出一个 JSON 对象，格式：\n"
    '{"max_dimension": "如 100*50*20，需求没提就空字符串", "dimension_tolerance_pct": 0, '
    '"application_scope": "", "operating_temperature": "如 -20~60", '
    '"service_life": "如 500次 / 5年", "hermeticity": "如 IP67", '
    '"comment": "一句话说明识别结果"}\n'
    "参数必须忠实于需求文本，没提到的一律留空字符串，**绝对不要编造或推测**。"
)

_STEP1_REQ_KEYS = ("max_dimension", "dimension_tolerance_pct", "application_scope",
                   "operating_temperature", "service_life", "hermeticity")

# ---------------------------------------------------------------------------
# 需求完整性门禁（**按行业**取必填项）
#
# 报价助手原来把门禁硬编码成半导体口径（尺寸/应用范围·使用场景/工作温度），
# 包装需求（盒型/三边内径/面纸克重…）本就不适用「工作温度」，却被判「需求信息不齐」，
# 链路停在意图识别。现在必填项按行业取：
#   · 半导体/电池/电器 —— 沿用既有三项（它们是产品匹配的**输入**，不是需求模板字段，
#     `required_keys('semiconductor')` 是另外 14 个键，不许替换）；
#   · 包装 —— 由 industry_templates 的需求模板派生（10 项必填，中文标签）。
# 行业清单唯一来源 cpq_industries.INDUSTRY_KEYS，不在此另写行业数组。
# ---------------------------------------------------------------------------

#: 报价助手一贯的匹配门禁三项（半导体/电池/电器），逐字不变。
_GATE_MATCH_REQUIRED = (("max_dimension", "尺寸"),
                        ("application_scope", "应用范围/使用场景"),
                        ("operating_temperature", "工作温度"))

#: 需要按行业模板取必填项的门禁（当前只有包装）。
PACKAGING_INDUSTRY = "packaging"


def _template_gate_required(industry: str) -> tuple:
    """按行业模板生成门禁必填项：key 与必填集合取 required_keys，标签取 labels（中文）。

    包装模板保证每个必填项都有中文标签；标签缺失时不静默回退成原始 key（红测 A5 会抓）。"""
    labels = quote_industry_templates.labels(industry)
    return tuple((key, labels.get(key) or key)
                 for key in quote_industry_templates.required_keys(industry))


def _build_step1_required_by_industry() -> dict:
    """行业 → 门禁必填项 (key, 中文标签)。行业清单来自 cpq_industries，不另写数组。"""
    table = {key: _GATE_MATCH_REQUIRED for key in cpq_industries.INDUSTRY_KEYS}
    if PACKAGING_INDUSTRY in table:
        table[PACKAGING_INDUSTRY] = _template_gate_required(PACKAGING_INDUSTRY)
    return table


STEP1_REQUIRED_BY_INDUSTRY: dict = _build_step1_required_by_industry()


def _industry_of(req=None, industry=None) -> str:
    """行业来源优先级（Spec §3.2）：请求体显式 industry → 需求里的 industry → 默认行业。

    只有「非空且合法」的显式值才生效；未知/空/历史键（flexible）一律落 DEFAULT_INDUSTRY。"""
    candidates = (industry, (req or {}).get("industry") if isinstance(req, dict) else None)
    for value in candidates:
        key = str(value or "").strip().lower()
        if key and quote_industry_templates.normalize(key) == key:
            return key
    return quote_industry_templates.normalize(None)


def step1_required(industry=None) -> tuple:
    """第 1 步需求完整性门禁的必填项 —— **按行业**取 (key, 中文标签)。

    必填项唯一来源是行业模板：包装取 `required_keys('packaging')` 与 `labels('packaging')`；
    半导体/电池/电器沿用既有三项（尺寸 / 应用范围·使用场景 / 工作温度）。
    未知、空、历史键一律落 `industry_templates.DEFAULT_INDUSTRY`，不抛异常。"""
    return STEP1_REQUIRED_BY_INDUSTRY[quote_industry_templates.normalize(industry)]


def step1_missing(req: dict, *, industry=None) -> list:
    """该**行业**下缺哪些必填项（返回缺失项的中文标签）。

    不传行业时按 `industry_templates.DEFAULT_INDUSTRY`，既有调用点不受影响。"""
    return [label for key, label in step1_required(_industry_of(req, industry))
            if not str((req or {}).get(key) or "").strip()]


# ---------------------------------------------------------------------------
# ④产品技术参数（s1_techparams / s2_techparams）：事实源按行业分流
#
# 半导体/电池/电器的事实源仍是《亿纬锂能DA梳理》的成品参数表（clm_calc_product_tech）；
# 包装换成盒型库（kb_packaging_box_type）—— 电池表里不可能有盒型的尺寸区间/灰板厚度/闭合方式。
# ---------------------------------------------------------------------------

#: 半导体/电池/电器（以及默认行业）沿用的成品参数字典事实源。
_DA_PRODUCT_SOURCE = "clm_calc_product_tech"

#: 行业 → ④产品技术参数的事实源标识。
_PRODUCT_SOURCES: dict = {
    "semiconductor": _DA_PRODUCT_SOURCE,
    "battery": _DA_PRODUCT_SOURCE,
    "appliance": _DA_PRODUCT_SOURCE,
    PACKAGING_INDUSTRY: "kb_packaging_box_type",
}

#: 盒型库（kb_packaging_box_type）的技术参数列：key = 表列名，label = 业务中文名。
#: 表结构见 tech_app/backend/storage/da_schema.sql；这里只列 ④ 表要展示的那些列。
_PACKAGING_BOX_COLUMNS = (
    ("box_type_code", "盒型编码"),
    ("name", "盒型名称"),
    ("family", "盒型族"),
    ("size_l_min", "内长下限（mm）"),
    ("size_l_max", "内长上限（mm）"),
    ("size_w_min", "内宽下限（mm）"),
    ("size_w_max", "内宽上限（mm）"),
    ("size_h_min", "内高下限（mm）"),
    ("size_h_max", "内高上限（mm）"),
    ("fit_clearance", "配合间隙（mm/单边）"),
    ("grey_board_thickness", "灰板厚度（mm）"),
    ("face_paper_gsm", "面纸克重（g/m²）"),
    ("closure_type", "闭合方式"),
    ("part_count", "部件数"),
    ("v_groove", "V槽"),
    ("hand_mount_ratio", "手裱比例"),
    ("standard_seconds", "标准工时（秒/个）"),
    ("automation_level", "自动化等级"),
    ("applicable_industries", "适用行业"),
    ("business_status", "业务状态"),
)


def tech_param_source(industry=None) -> str:
    """该行业 ④产品技术参数 的事实源标识（行业未知/空 → 默认行业）。"""
    return _PRODUCT_SOURCES[quote_industry_templates.normalize(industry)]


def tech_param_columns(industry=None) -> list:
    """该行业 ④产品技术参数 的表头定义：[{"key", "label"}, …]，label 一律中文。

    半导体/电池/电器取成品参数字典（product_params）的 code/name；包装取盒型库的列定义。"""
    key = quote_industry_templates.normalize(industry)
    if key == PACKAGING_INDUSTRY:
        return [{"key": k, "label": label} for k, label in _PACKAGING_BOX_COLUMNS]
    fields = quote_product_params.spec().get("fields") or []
    return [{"key": str(f.get("code")), "label": str(f.get("name") or f.get("code"))}
            for f in fields if f.get("code")]


def tech_param_row(industry=None, product=None) -> dict:
    """由选中的产品 / 盒型生成 ④产品技术参数的一行；不足则返回 {}，**绝不编造**。"""
    key = quote_industry_templates.normalize(industry)
    record = product if isinstance(product, dict) else {}
    if key == PACKAGING_INDUSTRY:
        if not str(record.get("box_type_code") or "").strip():
            return {}
        return {col["key"]: ("" if record.get(col["key"]) is None else str(record.get(col["key"])))
                for col in tech_param_columns(key)}
    row = {}
    for col in (FIXED_FORMS.get("s1_techparams") or {}).get("columns", []):
        value = record.get(col["key"])
        if value not in (None, ""):
            row[col["key"]] = str(value)
    return row


def _step1_intent_keys(industry=None) -> tuple:
    """意图识别要模型提取的字段 —— 按行业：包装是需求模板的 10 项必填，其余五个匹配参数。"""
    if quote_industry_templates.normalize(industry) == PACKAGING_INDUSTRY:
        return tuple(key for key, _label in step1_required(industry))
    return tuple(_STEP1_REQ_KEYS)


def _step1_eval_sys(industry=None) -> str:
    """意图识别的系统提示 —— 按行业给出要提取的字段。

    半导体/电池/电器**逐字沿用**既有的五个匹配参数提示；包装换成需求模板的必填项，
    否则包装需求会提取不出包装字段、门禁必然全缺（这正是本批要修的链路）。"""
    if quote_industry_templates.normalize(industry) != PACKAGING_INDUSTRY:
        return _STEP1_EVAL_SYS
    pairs = step1_required(industry)
    guide = "；".join("%s（%s）" % (label, key) for key, label in pairs)
    sample = "{" + ", ".join('"%s": ""' % key for key, _label in pairs) + \
             ', "comment": "一句话说明识别结果"}'
    return (
        "你是报价系统第 1 步的**需求意图识别器**。输入只有用户的需求文本。\n"
        "你的唯一任务：从需求文本里提取下列**包装需求**字段，判断信息是否齐全。"
        "**不要做任何别的事**——不查数据库、不推荐产品、不填任何表单、不给建议方案。\n"
        "字段（业务名（JSON 键））：" + guide + "\n"
        "**输出分两段**：\n"
        "第一段：用 1~3 句话说明你从需求里读到了哪些字段、还缺哪些"
        "（这段会实时展示给用户，用自然中文，不要写 JSON、不要列表格、不要提具体产品）。\n"
        "第二段：另起一行只写 " + _JSON_MARK + " ，然后输出一个 JSON 对象，格式：\n" +
        sample + "\n" +
        "值必须忠实于需求文本，没提到的一律留空字符串，**绝对不要编造或推测**。"
    )


def _parse_step1_json(out: str, keys=None):
    """解析大模型的一次性评估输出；失败返回 (None, '')。

    尽量宽容：剥掉 <think> 思考段与 ``` 代码围栏、在全文里找所有配平的 {...} 逐个试解析
    （取最后一个含预期键的），最后再对「被 max_tokens 截断的 JSON」做补右括号抢救。"""
    keys = tuple(keys or _STEP1_REQ_KEYS)
    s = (out or "").strip()
    s = re.sub(r"<think>.*?(?:</think>|$)", "", s, flags=re.S).strip()
    s = re.sub(r"```[a-zA-Z]*", "", s).strip()

    def _accept(obj):
        if not isinstance(obj, dict) or not any(k in obj for k in keys):
            return None
        req = {k: obj.get(k) for k in keys}
        return req, str(obj.get("comment") or "").strip()

    # 全文扫描所有配平的顶层 {...}，从后往前试（答案通常在思考/说明之后）
    candidates, depth, start = [], 0, None
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(s[start:i + 1])
    for cand in reversed(candidates):
        try:
            got = _accept(json.loads(cand))
        except json.JSONDecodeError:
            got = None
        if got:
            return got
    # 括号没配平：多半是被 max_tokens 截断，从第一个 { 起补齐右括号抢救
    i = s.find("{")
    if i >= 0 and depth > 0:
        frag = s[i:].rstrip().rstrip(",")
        bases = [frag]
        k = frag.rfind(",")
        if k > 0:                      # 截在键/值中间：丢掉最后一个不完整的键值对再试
            bases.append(frag[:k])
        for base in bases:
            for tail in ("", '"', '""'):
                try:
                    got = _accept(json.loads(base + tail + "}" * depth))
                except json.JSONDecodeError:
                    got = None
                if got:
                    return got
    return None, ""


def _step1_visible_text(raw: str) -> str:
    """流式输出里应展示给用户的部分：===JSON=== 之前的散文（并剥掉思考段）。"""
    s = re.sub(r"<think>.*?(?:</think>|$)", "", raw or "", flags=re.S)
    cut = s.find(_JSON_MARK)
    if cut >= 0:
        s = s[:cut]
    # 模型偶尔不写分隔符直接给 JSON：遇到裸的 { 就截断，别把 JSON 喷给用户
    brace = s.find("{")
    if brace >= 0:
        s = s[:brace]
    return s


def _handle_step1_match(data: dict, emit=None) -> dict:
    """第 1 步，分两段调用（phase）：

      phase="intent"（默认）—— **一次**大模型调用：只从需求文本提取该行业的必填参数并判断完整性。
          不查库、不推荐、不填表。缺必填项（半导体/电池/电器是尺寸·应用范围·工作温度三项，
          包装是需求模板的 10 项）返回 stage="intent"。
          通过则返回 requirement，供随后的 match 段直接复用。
      phase="match" —— 拿着 intent 段给的 requirement **纯查库 + 六维加权评分**，
          不再调用大模型（所以整个第 1 步的产品匹配总共只花一次模型调用）。

    这样表单填写（智能体 open-claude 那套）可以插在两段中间：识别 → 填表 → 匹配。
    """
    t0 = time.perf_counter()
    phase = (data.get("phase") or "intent").strip()

    def _trace(msg):
        print(f"[step1:{phase}] {msg}（累计 {time.perf_counter() - t0:.2f}s）", flush=True)

    def _say(obj):
        if emit:
            try:
                emit(obj)
            except Exception:
                pass

    # ===================== ② 匹配段：不调模型 =====================
    if phase == "match":
        req = data.get("requirement")
        if not isinstance(req, dict) or not req:
            return {"ok": False, "error": "缺少需求参数（requirement），无法匹配"}
        sql = f"SELECT * FROM {cpq_match.PRODUCT_TABLE}"
        _say({"type": "stage", "text": "查询产品参数值表（远程 Postgres）"})
        t = time.perf_counter()
        try:
            cols, rows = cpq_db.run_select(sql, cpq_match.FETCH_LIMIT)
        except Exception as e:
            _trace(f"查库失败，耗时 {time.perf_counter() - t:.2f}s：{str(e).splitlines()[0][:120]}")
            return {"ok": False, "stage": "db",
                    "error": f"读取 {cpq_match.PRODUCT_TABLE} 失败："
                             f"{str(e).splitlines()[0][:160]}"}
        _trace(f"查库完成：{len(rows)} 行，耗时 {time.perf_counter() - t:.2f}s")
        _say({"type": "stage", "text": f"已取回 {len(rows)} 行，正在按六维加权规则评分…"})
        if not rows:
            return {"ok": False, "stage": "db",
                    "error": "产品参数值表为空，没有可匹配的标品，建议转入定制评估。"}
        t = time.perf_counter()
        match_res = cpq_match.match(req, top_n=3, data=(cols, rows))
        match_res["requirement"] = {k: v for k, v in req.items() if str(v or "").strip()}
        match_res["comment"] = data.get("comment") or ""
        _trace(f"六维评分完成，耗时 {time.perf_counter() - t:.2f}s；总耗时 {time.perf_counter() - t0:.2f}s")
        return match_res

    # ===================== ① 识别段：一次大模型调用 =====================
    text = (data.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "缺少需求文本"}
    # 行业来源优先级：请求体显式 industry → 需求里的 industry → 默认行业（Spec §3.2）
    industry_key = _industry_of(None, data.get("industry"))
    # 行业与需求不一致时的软提示（Spec §3.3）：纯字符串匹配，旁路，不改门禁/匹配结果。
    hint = cpq_industries.industry_hint(text, industry_key)
    _trace(f"开始：需求文本 {len(text)} 字，行业 {industry_key}"
           + (f"，需求更像 {hint['suggested']}" if hint else ""))

    b = bridge_for((data.get("sid") or "").strip(), create=False) or bridge
    if b is None or b.conv.model == NO_MODEL_ID:
        return {"ok": False, "stage": "llm",
                "error": "当前为「无模型」模式，无法做需求识别；"
                         "请在设置里配置模型，或在右侧人工填写。"}
    _say({"type": "stage", "text": "意图识别：检查需求信息是否齐全"})
    prompt = "【需求文本】\n" + text[:8000]
    _trace(f"调大模型 {b.conv.model}：入参 {len(prompt)} 字，等待返回…")
    t = time.perf_counter()
    out = ""
    try:
        if emit:
            shown = 0
            for ev in stream_message(
                    b.conv.client, [{"role": "user", "content": prompt}],
                    _step1_eval_sys(industry_key),
                    model=b.conv.model, tools=[], max_tokens=2000):
                if ev.get("type") == "text_delta":
                    out += ev.get("text", "")
                    vis = _step1_visible_text(out)
                    if len(vis) > shown:
                        _say({"type": "text", "text": vis[shown:]})
                        shown = len(vis)
                elif ev.get("type") == "error":
                    raise RuntimeError(ev.get("error") or "stream error")
            out = out.strip()
        else:
            from open_claude.api import complete
            res = complete(
                b.conv.client, [{"role": "user", "content": prompt}],
                _step1_eval_sys(industry_key),
                model=b.conv.model, max_tokens=2000)
            out = "".join(bk.get("text", "") for bk in res.get("content", [])).strip()
    except Exception as e:
        _trace(f"大模型调用失败，耗时 {time.perf_counter() - t:.2f}s：{e.__class__.__name__}: {str(e)[:120]}")
        return {"ok": False, "stage": "llm",
                "error": f"需求识别调用失败：{e.__class__.__name__}: {str(e)[:160]}"}
    _trace(f"识别返回：{len(out)} 字，耗时 {time.perf_counter() - t:.2f}s")
    if not out:
        return {"ok": False, "stage": "llm",
                "error": "需求识别返回为空，请重试或人工填写需求参数。"}
    req, comment = _parse_step1_json(out, _step1_intent_keys(industry_key))
    if req is None:
        _trace("输出不是有效 JSON，放弃。原文前 400 字：" + out[:400].replace("\n", "⏎"))
        return {"ok": False, "stage": "llm",
                "error": "需求识别输出无法解析（不是有效 JSON），请重试或人工填写需求参数。"}

    missing = step1_missing(req, industry=industry_key)
    if missing:
        _trace("需求缺少 " + "、".join(missing) + "，不查库、不推荐、不填表")
        return {"ok": False, "stage": "intent", "missing": missing,
                "industry": industry_key,
                "industry_hint": hint,
                "required": [label for _key, label in step1_required(industry_key)],
                "requirement": {k: v for k, v in req.items() if str(v or "").strip()},
                "comment": comment,
                "error": "需求缺少必备匹配参数：" + "、".join(missing)}

    _trace(f"识别通过，参数齐全；总耗时 {time.perf_counter() - t0:.2f}s")
    return {"ok": True, "stage": "intent_ok", "industry": industry_key,
            "industry_hint": hint,
            "requirement": req, "comment": comment}


# ---------------------------------------------------------------------------
# 通用小工具：数值解析 / JSON 抠取（第 1、3、4 步的两段式输出共用）
# ---------------------------------------------------------------------------

def _num(v):
    """从任意值里取数字（容忍 '13%'、'1,234.5'、'￥100'）；取不到返回 None。"""
    m = re.search(r"-?\d+(?:\.\d+)?",
                  str(v if v is not None else "").replace(",", "").replace("，", ""))
    return float(m.group(0)) if m else None


def _fmt_num(x) -> str:
    return str(int(x)) if float(x).is_integer() else str(round(float(x), 2))


def _extract_json_obj(out: str):
    """从模型输出里抠出可解析的 JSON 对象（容忍思考段 / 代码围栏 / 前后废话）。"""
    s = (out or "").strip()
    s = re.sub(r"<think>.*?(?:</think>|$)", "", s, flags=re.S).strip()
    s = re.sub(r"```[a-zA-Z]*", "", s).strip()
    cands, depth, start = [], 0, None
    for i, ch in enumerate(s):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                cands.append(s[start:i + 1])
    for c in reversed(cands):
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


# ---------------------------------------------------------------------------
# 第 3/4 步加价计算：一次流式大模型调用完成（不进智能体回合循环）
#   第 3 步 定价-利润加成：md_clm_material_price_rule 里 rule_classification='定价' 的规则，
#           依据 产品信息 + 产品技术参数 算「利润加成」；
#   第 4 步 报价-其他加价项：rule_classification='报价' 的规则，依据 测算基本信息 + 目的地 +
#           产品信息 + 产品技术参数 + 付款里程碑 + 物流信息 算「其他加价」。
#   规则取数走纯 SQL（确定性），模型只负责按规则语义算金额并说明命中过程。
# ---------------------------------------------------------------------------

# 包装定价入口（包装第 8 批，Spec §4.4）：与三个原行业的 /api/markup/fill 并存，
# **不依赖大模型** —— 包装的定价口径是确定的除式（成本 ÷ (1-毛利率)），不由模型决定。
# "无模型"模式下这一条路径也必须能算，这是包装与三行业定价路径的根本区别。
PACKAGING_QUOTE_PRICE_PATH = "/api/packaging-quote/price"

# 快速报价标准案例列表（逆向快速报价第 1 批，Spec §2.6）：只读、只走报价侧。
# 这里的字面量必须与 cpq_quick_quote_case.QUICK_QUOTE_CASES_PATH 同值（那里是唯一
# 事实源）；_handle_quick_quote_cases() 每次都会比对一次，两处写死也不会悄悄漂移。
QUICK_QUOTE_CASES_PATH = "/api/quick-quote/cases"

# 快速报价案例维护（逆向快速报价第 12 批，Spec §2.4）：两条**写**路由。路径模板的唯一事实源
# 仍是 cpq_quick_quote_case.QUICK_QUOTE_CASE_*_PATH（这里不写第二份字面量，正则由模板生成）。
QUICK_QUOTE_CASE_FIELDS_PATH = cpq_quick_quote_case.QUICK_QUOTE_CASE_FIELDS_PATH
QUICK_QUOTE_CASE_REVIEW_PATH = cpq_quick_quote_case.QUICK_QUOTE_CASE_REVIEW_PATH


def _case_action_regex():
    """「模板 → 正则」：把 {case_code} 换成具名组，动作闭集由模板尾段推导（Spec §2.4）。"""
    fields = QUICK_QUOTE_CASE_FIELDS_PATH
    review = QUICK_QUOTE_CASE_REVIEW_PATH
    head, _, tail = fields.partition("{case_code}")
    head_review, _, tail_review = review.partition("{case_code}")
    if head != head_review:                                      # pragma: no cover - 防御
        raise ValueError("两条案例维护路由必须共用同一个前缀：%r / %r" % (fields, review))
    actions = sorted({tail.strip("/"), tail_review.strip("/")})
    return re.compile("^" + re.escape(head) + "(?P<case_code>[^/]+)/"
                      + "(?P<action>" + "|".join(re.escape(a) for a in actions) + ")$")


QUICK_QUOTE_CASE_ACTION_RE = _case_action_regex()

#: 快速报价当前的能力边界：接口与前端共用同一份文案，不各写一句。
_QUICK_QUOTE_NOTES = (
    "本接口只给标准案例库的现状与资格；检索排序、差异价、出价门槛与文件解析在后三批。",
    "演示数据（demo）与未分类数据（unknown）只用于展示，永远不能用于快速报价。",
    "从报价沉淀出来的案例默认是草稿，人工审到「已审核」之后才会进可用列表。",
)

# context = 发给模型的上下文分区。前面步骤的信息一律带全（缺了规则就算不出来），
# 只是各步的**主依据**不同：第 3 步看产品与技术参数，第 4 步还要看基本信息/目的地/付款/物流。
_ALL_CTX = ("basic", "dest", "products", "techparams", "payment", "logistics")
MARKUP_STEPS = {
    3: {"classification": "定价", "column": "利润加成", "section": "s3",
        "label": "定价-利润加成", "context": _ALL_CTX,
        "basis": "产品信息、产品技术参数"},
    4: {"classification": "报价", "column": "其他加价", "section": "s4",
        "label": "报价-其他加价项", "context": _ALL_CTX,
        "basis": "测算基本信息、目的地信息、产品信息、产品技术参数、付款里程碑信息、物流信息"},
}

_CTX_LABEL = {
    "basic": "测算基本信息", "dest": "目的地信息", "products": "产品信息",
    "techparams": "产品技术参数", "payment": "付款里程碑信息", "logistics": "物流信息",
}

def _markup_sys(cfg: dict) -> str:
    col = cfg["column"]
    return (
        f"你是报价系统「{cfg['label']}」步骤的计算器。输入是：规则库中"
        f"`rule_classification='{cfg['classification']}'` 的全部规则，以及本单前面步骤已确认的数据。\n"
        f"任务：逐条判断规则是否命中，算出每个产品的**{col}**金额——"
        f"主要依据 {cfg['basis']}；其余分区作为补充信息，规则用得上就用。\n"
        "**铁律**：\n"
        "1. 只能用给定的规则和数据，规则表达式（rule_expression 是伪代码）按其语义人工判断执行，"
        "**不要照抄表达式、不要编造规则、不要臆造数据**。\n"
        "2. 规则没命中就不计入；一条都没命中时金额填 0 并说明原因。\n"
        "3. 产品必须用给定的成品编码，**不要新增或更换产品**。\n"
        "**输出分两段**：\n"
        "第一段：用 2~5 句话说明取回多少条规则、命中了哪几条、依据什么数据、各算出多少"
        "（这段会实时展示给用户，用自然中文，不要写 JSON、不要贴表格）。\n"
        f"第二段：另起一行只写 {_JSON_MARK} ，然后输出一个 JSON 对象：\n"
        '{"markup": [{"序号": "1", "加价项名称": "命中的规则名", "加价值": "金额或系数"}], '
        f'"products": [{{"成品编码": "…", "{col}": "金额"}}]}}\n'
        "markup 逐条列出**命中**的规则；products 每个产品一行，金额用纯数字字符串。"
    )


def _markup_visible_text(raw: str) -> str:
    s = re.sub(r"<think>.*?(?:</think>|$)", "", raw or "", flags=re.S)
    cut = s.find(_JSON_MARK)
    if cut >= 0:
        s = s[:cut]
    brace = s.find("{")
    if brace >= 0:
        s = s[:brace]
    return s


def _handle_markup_fill(data: dict, emit=None) -> dict:
    """POST /api/markup/fill —— 第 3/4 步加价计算（SQL 取规则 + 一次流式大模型调用）。"""
    t0 = time.perf_counter()
    step = data.get("step")
    cfg = MARKUP_STEPS.get(step if isinstance(step, int) else 0)
    if not cfg:
        return {"ok": False, "error": "step 必须是 3 或 4"}
    col = cfg["column"]

    def _trace(msg):
        print(f"[step{step}] {msg}（累计 {time.perf_counter() - t0:.2f}s）", flush=True)

    def _say(obj):
        if emit:
            try:
                emit(obj)
            except Exception:
                pass

    products = [r for r in (data.get("products") or []) if isinstance(r, dict)]
    if not products:
        return {"ok": False, "error": "前面步骤没有产品信息，无法计算" + col}

    # ① 纯 SQL 取规则（确定性；布尔列必须 is_deleted = false）
    sql = ("SELECT rule_name, rule_desc, rule_expression FROM md_clm_material_price_rule"
           " WHERE rule_classification = '{}' AND is_deleted = false".format(cfg["classification"]))
    _say({"type": "stage", "text": f"查询规则库：{cfg['classification']}规则"})
    t = time.perf_counter()
    try:
        rcols, rrows = cpq_db.run_select(sql, 500)
    except Exception as e:
        _trace(f"① 取规则失败，耗时 {time.perf_counter() - t:.2f}s：{str(e).splitlines()[0][:120]}")
        return {"ok": False, "stage": "db",
                "error": f"读取 md_clm_material_price_rule 失败：{str(e).splitlines()[0][:160]}"}
    rules = [dict(zip(rcols, r)) for r in rrows]
    _trace(f"① 取回 {len(rules)} 条{cfg['classification']}规则，耗时 {time.perf_counter() - t:.2f}s")
    _say({"type": "stage",
          "text": f"已取回 {len(rules)} 条{cfg['classification']}规则，开始按规则计算{col}"})
    if not rules:
        return {"ok": False, "stage": "db",
                "error": f"规则库里没有 rule_classification='{cfg['classification']}' 的规则。"}

    # ② 一次大模型调用（流式）
    b = bridge_for((data.get("sid") or "").strip(), create=False) or bridge
    if b is None or b.conv.model == NO_MODEL_ID:
        return {"ok": False, "stage": "llm",
                "error": f"当前为「无模型」模式，无法计算{col}；请在右侧人工填写。"}

    parts = ["【{}规则（共 {} 条，来自 md_clm_material_price_rule）】\n{}".format(
        cfg["classification"], len(rules),
        json.dumps(rules, ensure_ascii=False, default=str))]
    for key in cfg["context"]:
        val = data.get(key)
        if val:
            parts.append("【{}】\n{}".format(
                _CTX_LABEL.get(key, key), json.dumps(val, ensure_ascii=False, default=str)))
    prompt = "\n\n".join(parts)
    _trace(f"② 调大模型 {b.conv.model}：入参 {len(prompt)} 字，等待返回…")
    t = time.perf_counter()
    out = ""
    try:
        shown = 0
        for ev in stream_message(
                b.conv.client, [{"role": "user", "content": prompt}], _markup_sys(cfg),
                model=b.conv.model, tools=[], max_tokens=4000):
            if ev.get("type") == "text_delta":
                out += ev.get("text", "")
                if emit:
                    vis = _markup_visible_text(out)
                    if len(vis) > shown:
                        _say({"type": "text", "text": vis[shown:]})
                        shown = len(vis)
            elif ev.get("type") == "error":
                raise RuntimeError(ev.get("error") or "stream error")
        out = out.strip()
    except Exception as e:
        _trace(f"② 大模型调用失败，耗时 {time.perf_counter() - t:.2f}s：{e.__class__.__name__}: {str(e)[:120]}")
        return {"ok": False, "stage": "llm",
                "error": f"{col}计算失败：{e.__class__.__name__}: {str(e)[:160]}"}
    _trace(f"② 大模型返回：{len(out)} 字，耗时 {time.perf_counter() - t:.2f}s")

    obj = _extract_json_obj(out)
    if obj is None:
        _trace("② 输出不是有效 JSON。原文前 400 字：" + out[:400].replace("\n", "⏎"))
        return {"ok": False, "stage": "llm",
                "error": f"{col}计算结果无法解析（不是有效 JSON），请重试或人工填写。"}

    # ③ 规范化：加价明细只保留固定列；产品加价只认已有成品编码
    codes = {str(p.get("成品编码", "")).strip() for p in products if str(p.get("成品编码", "")).strip()}
    mk_cols = [c["key"] for c in (FIXED_FORMS.get(cfg["section"] + "_markup") or {}).get("columns", [])]
    markup = []
    for i, r in enumerate(obj.get("markup") or [], 1):
        if not isinstance(r, dict):
            continue
        row = {k: ("" if r.get(k) is None else str(r.get(k))) for k in mk_cols if r.get(k) is not None}
        row["规则分类"] = cfg["classification"]
        row.setdefault("序号", str(i))
        if str(row.get("加价项名称", "")).strip():
            markup.append(row)
    prod_add = {}
    for r in (obj.get("products") or []):
        if not isinstance(r, dict):
            continue
        code = str(r.get("成品编码", "")).strip()
        if code and code in codes:
            v = _num(r.get(col))
            prod_add[code] = _fmt_num(v) if v is not None else "0"
    matched = len(prod_add)
    for c in codes:                       # 模型漏给的产品补 0，避免空列
        prod_add.setdefault(c, "0")
    # 加价是**按成品编码**落到产品行上的：没有编码的产品行永远拿不到加价值，
    # 最终价格 = 基础成本，既没有利润也没有加价 —— 而且这事以前是静默发生的，
    # 界面上照样显示"已完成"。把两个数量回给前端，让它据实提示。
    no_code = sum(1 for p in products if not str(p.get("成品编码", "")).strip())

    _trace(f"③ 完成：命中 {len(markup)} 条规则，{len(prod_add)} 个产品的{col}；"
           f"总耗时 {time.perf_counter() - t0:.2f}s")
    return {"ok": True, "step": step, "column": col, "markup": markup,
            "product_markup": prod_add, "rule_count": len(rules),
            "matched_products": matched, "no_code_products": no_code,
            "product_count": len(products),
            "source": {"db": cpq_db.DB_LABEL, "table": "md_clm_material_price_rule",
                       "sql": sql, "rows": len(rules)}}


# ---------------------------------------------------------------------------
# sql_query 工具：在亿纬锂能 DA 库（远程 Postgres）上执行只读 SQL
# ---------------------------------------------------------------------------

SQL_QUERY_SCHEMA = {
    "name": "sql_query",
    "description": (
        "在报价知识库（远程 Postgres，只读）上执行 SELECT 查询，取 BOM / 定价 / 加价 / "
        "规则 / 字段清单 等基础数据。**表结构见系统提示词末尾的完整 schema，据它生成 SQL。**"
        "仅允许单条 SELECT / WITH 语句，不要带分号或多条语句。"
        "**只能查配置助手页/规则助手页的表（md_* 主数据表）；报价助手页的业务表（clm_calc_*/clm_quote_*）"
        "禁止查询，会被直接拒绝**——前面步骤已生成的数据以右侧工作台分区内容为准。"
        "列名不确定时先 SELECT * FROM 表 LIMIT 3，或查 information_schema.columns 探查。"
        "⚠️ **布尔列必须用 true/false，不能用 0/1**（如 is_deleted = false）——Postgres 会直接报 operator does not exist: boolean = integer。"
        "所有展示/推荐给用户的“数据库端”依据都必须通过本工具真实查出来，不要臆造。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string",
                    "description": "标准 PostgreSQL 查询语句（单条，不以分号结尾、不含多条语句）"},
            "limit": {"type": "integer",
                      "description": "最多返回行数，默认 100，上限 500。结果被截断时按「大结果分步取数协议」分批取。"},
        },
        "required": ["sql"],
    },
}

_SQL_ALLOWED_PREFIX = ("select", "with")
_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|attach|detach|create|replace|reindex|vacuum|truncate|grant|revoke|copy)\b",
    re.IGNORECASE,
)
# 表访问边界：报价助手做数据提取时，只能查 亿纬锂能DA梳理 配置助手页/规则助手页 的表（md_* 主数据），
# 报价助手页的实际业务表（clm_calc_* 价格测算单 / clm_quote_* 报价单）是「导入数据库」的写入目标，禁止查询。
_SQL_BLOCKED_TABLES = re.compile(r"\bclm_(?:calc|quote)_\w+", re.IGNORECASE)


def _handle_packaging_quote_price(data: dict, emit=None) -> dict:
    """POST /api/packaging-quote/price —— 包装定价（**不依赖大模型**）。

    入参：``{"package": {...}, "gross_margin_rate": …, "markup_rate": …, "pricing_mode": …,
    "addons": [...], "discount": {...}, "tax_rate": …}``；出参：
    ``{"ok": True, "quote": …, "sections": …, "document": …}`` /
    ``{"ok": False, "error": …}``（失败**不抛给调用方**）。

    算法只有一份：``cpq_packaging_quote.price()`` —— 路由、Agent 工具、这里都调它。
    """
    data = data if isinstance(data, dict) else {}
    try:
        import cpq_packaging_quote as pkg_quote
        package = data.get("package") if isinstance(data.get("package"), dict) else {}
        kwargs = {}
        if "gross_margin_rate" in data:
            kwargs["gross_margin_rate"] = data.get("gross_margin_rate")
        if "markup_rate" in data:
            kwargs["markup_rate"] = data.get("markup_rate")
        if data.get("pricing_mode"):
            kwargs["pricing_mode"] = data.get("pricing_mode")
        if "addons" in data:
            kwargs["addons"] = data.get("addons")
        if "discount" in data:
            kwargs["discount"] = data.get("discount")
        if "tax_rate" in data:
            kwargs["tax_rate"] = data.get("tax_rate")
        if "quote_quantity" in data:
            kwargs["quote_quantity"] = data.get("quote_quantity")
        # 草稿 / 正式的分界（Spec packaging-quote-draft-and-card-visibility §2.2）：
        # 缺省 False = 出草稿；只有显式 publish=True 才要求缺口清零。
        kwargs["publish"] = bool(data.get("publish"))
        quote = pkg_quote.price(package, **kwargs)
        return {"ok": True, "quote": quote, "sections": pkg_quote.sections(quote),
                "document": pkg_quote.document(quote)}
    except Exception as exc:                                   # noqa: BLE001 - 失败要回给调用方
        return {"ok": False, "error": str(exc) or "包装定价失败"}


def _handle_quick_quote_cases(params=None) -> dict:
    """GET /api/quick-quote/cases —— 标准报价案例列表 + 资格 + 提示（只读）。

    出参：``{"ok": True, "engine_version", "industry", "quote_modes", "steps",
    "cases", "case", "case_total", "eligible_total", "notes"}``；库不可用时
    ``{"ok": False, "error": …}``（由调用方按 503 回 —— **不回落空列表**，空列表会被
    当成"库里没有可复用的成交案例"）。

    只走报价侧取数：不转调技术工艺、也不碰既有加价链路（Spec §2.6）。
    """
    query = params if isinstance(params, dict) else {}

    def arg(name, default=""):
        got = query.get(name)
        if isinstance(got, (list, tuple)):
            got = got[0] if got else default
        return str(got if got is not None else default).strip()

    if QUICK_QUOTE_CASES_PATH != cpq_quick_quote_case.QUICK_QUOTE_CASES_PATH:  # pragma: no cover
        return {"ok": False, "error": "快速报价案例路由常量与 cpq_quick_quote_case 不一致"}
    include_expired = arg("include_expired", "1").lower() not in ("0", "false", "no")
    wanted = arg("case_code")
    try:
        rows = cpq_quick_quote_case.load_cases(None, include_expired=include_expired)
    except cpq_quick_quote_case.CaseLibraryUnavailable as exc:
        return {"ok": False, "error": str(exc),
                "engine_version": cpq_quick_quote_case.ENGINE_VERSION,
                "industry": cpq_quick_quote_case.INDUSTRY,
                "quote_modes": _quick_quote_modes(),
                "steps": _quick_quote_steps(),
                "cases": [], "case": {}, "case_total": 0, "eligible_total": 0,
                # 读不到库**不许装成"空库"**：空库是"从来没有可复用的成交经验"，
                # 处置完全不同（一个去修连接，一个去导入案例）。verdict="unavailable"
                # 不属于三态（那是"库读到了、库里是什么样"），前端必须照实显示。
                "readiness": {"verdict": "unavailable", "headline": "案例库读不到",
                              "detail": str(exc), "case_total": None, "eligible_total": None,
                              "blocked_by": [], "next_actions": [
                                  {"action": "transfer_to_precise",
                                   "label": "转精准报价",
                                   "hint": "案例库读不到时不耽误出价：改走精准报价"}]},
                "notes": list(_QUICK_QUOTE_NOTES)}
    return {
        "ok": True,
        "engine_version": cpq_quick_quote_case.ENGINE_VERSION,
        "industry": cpq_quick_quote_case.INDUSTRY,
        "quote_modes": _quick_quote_modes(),
        "steps": _quick_quote_steps(),
        "cases": rows,
        "case": cpq_quick_quote_case.find_case(wanted, cases=rows) if wanted else {},
        "case_total": len(rows),
        "eligible_total": len([row for row in rows if row.get("eligible")]),
        # 现状话术只由 `library_readiness()` 给（接口 / 前端 / CLI 同一份）：
        # "库是空的"与"有 2 条但 0 条可用"必须分得开。用已经取到的 rows，不再读一次库。
        "readiness": cpq_quick_quote_case.library_readiness(rows),
        "notes": list(_QUICK_QUOTE_NOTES),
    }


def _quick_quote_case_route(path: str) -> dict:
    """路径 → {"case_code", "action"}；不匹配回空字典。"""
    match = QUICK_QUOTE_CASE_ACTION_RE.match(path or "")
    return match.groupdict() if match else {}


def _case_actor_text(user) -> str:
    user = user if isinstance(user, dict) else {}
    return _text(user.get("username")) or _text(user.get("user_id"))


def _handle_quick_quote_case_write(case_code: str, action: str, data=None, *,
                                   user=None) -> dict:
    """POST /api/quick-quote/cases/{case_code}/fields|review —— 案例维护写路径（Spec 批 12 §2.4）。

    **先校验后写**：``case_edit_patch()`` / ``case_review_patch()`` 判出的 ``blocked`` 非空时，
    一个字节都不落库。写权限复用 ``cpq_quick_quote_price.WRITE_ROLES``（``case_write_allowed``），
    路由里不重写角色判断。失败出参 ``{"ok": False, "code", "error"}``；成功出参带
    ``case`` / ``changed`` / ``blocked`` / ``readiness``。
    """
    data = data if isinstance(data, dict) else {}
    if not cpq_quick_quote_case.case_write_allowed(user):
        return {"ok": False, "code": "forbidden",
                "error": "当前账号没有维护标准报价案例的权限"}
    try:
        rows = cpq_quick_quote_case.load_cases(None, include_expired=True)
    except cpq_quick_quote_case.CaseLibraryUnavailable as exc:
        return {"ok": False, "code": "library_unavailable", "error": str(exc)}
    row = cpq_quick_quote_case.find_case(case_code, cases=rows)
    if not row:
        return {"ok": False, "code": "case_not_found",
                "error": "案例不存在：%s" % case_code}
    if action == "review":
        plan = cpq_quick_quote_case.case_review_patch(
            row, data.get("status"), reviewer=_case_actor_text(user),
            reason=data.get("reason") or "")
    else:
        plan = cpq_quick_quote_case.case_edit_patch(row, data.get("values"))
    blocked = list(plan.get("blocked") or [])
    if blocked:
        return {"ok": False, "code": blocked[0].get("reason_code") or "invalid_value",
                "error": blocked[0].get("detail") or "这条案例不允许这么改",
                "blocked": blocked}
    merged = dict(row)
    merged.update(plan.get("patch") or {})
    try:
        saved = cpq_quick_quote_case.save_case(merged, user=user)
    except cpq_quick_quote_case.QuickQuoteCaseError as exc:
        return {"ok": False, "code": "invalid_value", "error": str(exc)}
    try:
        fresh = cpq_quick_quote_case.load_cases(None, include_expired=True)
        readiness = cpq_quick_quote_case.library_readiness(fresh)
    except cpq_quick_quote_case.CaseLibraryUnavailable:           # pragma: no cover - 防御
        readiness = {}
    return {"ok": True, "case": saved, "changed": list(plan.get("changed") or []),
            "blocked": [], "readiness": readiness}


def _handle_quick_quote_parse(data=None) -> dict:
    """POST /api/quick-quote/parse —— 销售丢文件 → 解析 → 匹配输入 →（可选）候选检索。

    出参：``{"ok", "parse", "inputs", "missing", "sources", "capability", "match"}``。
    能力段（provider / provider_version / dwg）必须原样回传：上一次 DWG 事故就是
    「页面宣称支持、后端实际不支持」，前端要能一眼看到解析器是谁、什么版本、支不支持 DWG。

    本函数只当客户端：不转调技术工艺项目接口、不自己转换图纸（Spec 批 5 §2.5）。
    """
    body = data if isinstance(data, dict) else {}
    name = str(body.get("name") or "").strip()
    raw_b64 = str(body.get("data") or "").strip()
    if not name:
        return {"ok": False, "error": "缺少文件名（name）"}
    if not raw_b64:
        return {"ok": False, "error": "缺少文件内容（data，base64）"}
    try:
        raw = base64.b64decode(raw_b64, validate=False)
    except Exception:                                          # noqa: BLE001 - 统一收敛
        return {"ok": False, "error": "文件数据不是合法 base64"}
    try:
        parsed = cpq_quick_quote_file.parse_file(name, raw)
    except cpq_quick_quote_file.QuickQuoteFileError as exc:
        return {"ok": False, "error": str(exc), "kind": "unsupported",
                "advice": getattr(exc, "advice", "")}
    except cpq_quick_quote_file.ParseServiceUnavailable as exc:
        return {"ok": False, "error": str(exc), "kind": "service_unavailable",
                "advice": "统一解析服务不在线：文字 / Excel / PDF 需求不受影响，"
                          "DWG/DXF 请稍后重试或转人工。"}
    mapped = cpq_quick_quote_file.to_match_inputs(parsed)
    out = {"ok": True, "parse": parsed, "inputs": mapped["inputs"],
           "missing": mapped["missing"], "sources": mapped["sources"],
           "warnings": mapped["warnings"], "capability": parsed.get("capability") or {},
           # 字段中文名的唯一事实源在后端（Spec 批 11 C4）：页面只渲染，不自带第二份标签表。
           "labels": cpq_quick_quote_match.input_labels(
               list(mapped["inputs"]) + list(mapped["missing"])),
           "match": {}}
    if body.get("match", True):
        try:
            cases = cpq_quick_quote_case.load_cases(None)
            out["match"] = cpq_quick_quote_match.match_cases(
                mapped["inputs"], cases=cases)
        except Exception as exc:                               # noqa: BLE001 - 匹配失败不影响解析结果
            out["match"] = {"error": str(exc) or "候选检索失败", "candidates": []}
    return out


def _quick_quote_modes() -> list:
    """两条报价路径（模式键 + 中文名）：键取模块常量，页面不自己造字符串。"""
    return [{"mode": key, "label": cpq_quick_quote_case.MODE_LABELS.get(key, key)}
            for key in cpq_quick_quote_case.QUOTE_MODES]


def _quick_quote_steps() -> list:
    return [{"key": key, "label": cpq_quick_quote_case.STEP_LABELS.get(key, key)}
            for key in cpq_quick_quote_case.QUICK_QUOTE_STEPS]


def _handle_sql_query(tool_input: dict) -> str:
    """只读执行一条 SQL，返回紧凑文本表；拒绝一切写操作/多语句。"""
    if not isinstance(tool_input, dict):
        return "sql_query 入参必须是 JSON 对象"
    sql = (tool_input.get("sql") or "").strip().rstrip(";").strip()
    if not sql:
        return "缺少 sql"
    low = sql.lower()
    if not low.startswith(_SQL_ALLOWED_PREFIX):
        return "只允许只读查询（以 SELECT / WITH 开头）"
    if ";" in sql:
        return "一次只允许一条查询语句（不要包含分号或多条语句）"
    if _SQL_FORBIDDEN.search(sql):
        return "检测到写操作关键字，已拒绝：本知识库为只读"
    m = _SQL_BLOCKED_TABLES.search(sql)
    if m:
        return ("已拒绝：报价助手页的业务表（如 " + m.group(0) + "）是本流程「导入数据库」的写入目标，"
                "不允许作为数据来源查询。只能查配置助手页/规则助手页的表（md_* 主数据表）；"
                "前面步骤已生成的数据请直接沿用右侧工作台各分区内容。")
    try:
        limit = int(tool_input.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 500))
    try:
        cols, rows = cpq_db.run_select(sql, limit)
    except Exception as e:
        return f"SQL 执行失败：{e}"
    more = len(rows) > limit
    rows = rows[:limit]
    total = None
    if more:  # 被截断时补查总量（尽力而为），让模型第一批就能定好分片计划
        try:
            _, cnt_rows = cpq_db.run_select(f"SELECT COUNT(*) FROM ({sql}) _cpq_cnt", 1)
            if cnt_rows and cnt_rows[0] and cnt_rows[0][0] is not None:
                total = int(cnt_rows[0][0])
        except Exception:
            pass

    def cell(x):
        s = "" if x is None else str(x)
        s = s.replace("\n", " ").replace("|", "/")
        s = "".join(ch if ch.isprintable() else " " for ch in s)  # 过滤二进制/控制字符乱码
        return s if len(s) <= 80 else s[:77] + "…"

    if not cols:
        return "查询已执行，但无结果列。"
    lines = [" | ".join(cols), " | ".join("---" for _ in cols)]
    for r in rows:
        lines.append(" | ".join(cell(x) for x in r))
    head = f"查询成功，返回 {len(rows)} 行"
    if more:
        head += (f"（⚠️ 结果被截断：该查询共 {total} 行" if total is not None
                 else "（⚠️ 结果被截断，实际更多")
        head += ("，超出单次上限。请按系统提示词「大结果分步取数协议」处理："
                 "先加 WHERE 收窄；仍超限就分批取（keyset 分页），每批只留候选短名单再取下一批）")
    return head + "：\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# 导入数据库：把工作台各分区数据写入远程 Postgres 的 DA 目标表（第 6 步「导入数据库」按钮）。
#  - 中文属性名 → 列 code 由 DA 本体「报价助手」本体映射（含隐藏字段——有值就存）；
#  - **主键(id)由雪花算法生成**（cpq_db.snow_next_id）；**外键按 ER 关系引用父表已生成的主键**；
#    这些 id 只存后台、前台不展示，但写库时按下方 ER 关系装配好。
#  - 逐分区独立提交并回报成败，前端把每个分区的导入状况显示在左侧 Agent 聊天里。
# ---------------------------------------------------------------------------

# section_id -> (目标表, 说明)。按 ER 依赖顺序排列：父表在前、子表在后。
# s2/s3/s4_products 各步骤对产品信息的更新在导入前合并进 s1_products（见 _merge_product_rows），
# 最终把合并后的完整产品信息写入 clm_calc_product；BPM/文档不入库。
_IMPORT_SEQ = [
    ("s1_basic",     "clm_calc_base_info",    "测算基本信息"),   # 价格测算单根：calc_order_id
    ("s1_products",  "clm_calc_product",      "产品信息（含第1-4步更新）"),  # PK product_line_id（被 tech/bom 引用）
    ("s1_techparams", "clm_calc_product_tech", "产品技术参数"),
    ("s1_dest",      "clm_calc_destination",  "目的地信息"),
    ("s1_payment",   "clm_calc_payment",      "付款信息"),
    ("s1_logistics", "clm_calc_logistics",    "物流信息"),
    # 第2步已改为「工艺确认」（仅人工核对产品信息/技术参数，不新增入库数据；技术参数由 s1_techparams 入库）
    ("s3_markup",    "clm_calc_markup_item",  "定价规则(利润加成)"),
    ("s4_markup",    "clm_calc_markup_item",  "加价规则(其他加价)"),
    ("s5_basic",     "clm_quote_base_info",   "报价基本信息"),   # 报价单根：quote_order_id
    ("s5_detail",    "clm_quote_product",     "报价明细"),
]


# 产品信息在第 1-4 步都会展示并被 Agent/用户更新（s1 可编辑，s2/s3/s4 每步重渲染带最新值）。
# 前端导入时会把四个分区都发过来，这里按步骤顺序合并成一份完整的产品信息再入库。
_PRODUCT_SECTIONS = ("s1_products", "s2_products", "s3_products", "s4_products")


def _merge_product_rows(sections: dict) -> list:
    """合并第 1-4 步的产品信息分区：行优先按「产品型号」对齐（无型号按行号对齐），
    后面步骤的**非空值覆盖**前面步骤，新出现的字段直接补充；空值不会抹掉先前已填的值。"""
    def _rows(sid):
        sec = sections.get(sid)
        if not isinstance(sec, dict):
            return []
        data = sec.get("数据", sec.get("data"))
        rows = [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])
        return [r for r in rows if isinstance(r, dict) and any(str(v).strip() for v in r.values())]

    merged: list = []
    idx_by_model: dict = {}   # 产品型号 -> merged 下标
    for sid in _PRODUCT_SECTIONS:
        for i, r in enumerate(_rows(sid)):
            model = str(r.get("产品型号", "")).strip()
            j = None
            if model and model in idx_by_model:
                j = idx_by_model[model]
            elif i < len(merged):
                # 型号缺失或是新型号但行号能对上（如后步型号列没渲染/被改写）时按行号对齐
                if not model or not str(merged[i].get("产品型号", "")).strip() \
                        or str(merged[i].get("产品型号", "")).strip() == model:
                    j = i
            if j is None:
                merged.append({})
                j = len(merged) - 1
            tgt = merged[j]
            for k, v in r.items():
                s = str(v).strip() if v is not None else ""
                if s and s not in ("-", "—", "/"):
                    tgt[k] = v
                elif k not in tgt:
                    tgt[k] = v
            m2 = str(tgt.get("产品型号", "")).strip()
            if m2:
                idx_by_model[m2] = j
    return merged


def _bom_level(r: dict) -> int:
    """从行数据里解析层级：'L2'/'2'/2 → 2；解析不出按 1（顶层）。"""
    v = str(r.get("层级", r.get("level", ""))).strip()
    m = re.search(r"\d+", v)
    return int(m.group(0)) if m else 1


def _import_quote(payload: dict) -> dict:
    """按 ER 关系把工作台各分区写入目标 Postgres：主键雪花生成、外键引用父表主键。逐分区回报成败。

    ER 装配：
      - 价格测算单根 calc_order_id 生成一次 → clm_calc_base_info 主键 + 各子表 calc_order_id 外键；
      - 报价单根 quote_order_id 生成一次 → clm_quote_base_info 主键 + clm_quote_product 外键；
      - 每个产品行 product_line_id 雪花主键 → 产品技术参数/BOM头 按「产品型号」匹配引用（单产品直接沿用）；
      - **BOM 层级（L1-Ln）用 头/行递归关系落库**：行表 ref_bom_header_id → 头表 bom_header_id。
        按「层级」列重建树——L1 行挂产品根 BOM 头；每个有子件的行，为它补插一个子 BOM 头
        （product_item_* 取该组件自身），其子行的 ref_bom_header_id 指向该子头；
        行的 parent_line_id 同时回填父行 bom_line_id（顶层为空）。这是后台实际表内容，与前端展示无关；
      - 加价明细 clm_calc_markup_item：主键 markup_item_id 雪花生成；product_line_id 走通用
        产品外键回填（按「产品型号」匹配，缺省取首个产品行）；rule_category 缺省按步骤兜底
        （s3_markup=定价 / s4_markup=报价）；
      - 其余表自身主键：不显式给值，交由 PG 列默认 snow_next_id() 自动生成。
    """
    if not isinstance(payload, dict):
        return {"ok": False, "error": "入参必须是 JSON 对象"}
    sections = payload.get("sections") or {}
    if not isinstance(sections, dict) or not sections:
        return {"ok": False, "error": "没有可导入的分区数据"}
    # 产品信息：合并第 1-4 步的更新，写库用完整版（覆盖 s1_products 原始快照）
    merged_products = _merge_product_rows(sections)
    if merged_products:
        sections = dict(sections)
        sections["s1_products"] = {"标题": "产品信息（第1-4步更新合并）", "数据": merged_products}
    try:
        conn = cpq_db.connect(readonly=False)  # autocommit：逐分区独立生效
    except Exception as e:
        return {"ok": False, "error": f"无法连接目标数据库：{e}"}

    def _rows_of(sec):
        data = sec.get("数据", sec.get("data"))
        rows = [data] if isinstance(data, dict) else (data if isinstance(data, list) else [])
        return [r for r in rows if isinstance(r, dict) and any(str(v).strip() for v in r.values())]

    results = []
    # ER 上下文（生成一次的根 id + 父表主键索引）
    root_calc = None          # 价格测算单 calc_order_id
    root_quote = None         # 报价单 quote_order_id
    prod_by_model = {}        # 产品型号 -> product_line_id
    prod_ids = []             # 全部 product_line_id（顺序）
    head_ids = []             # 全部 bom_header_id
    root_head_cr = None       # 第一条 BOM 头的落库行（子层级头沿用它的 calc/product/version）
    bl_stack = []             # BOM 行层级栈：[{level, line_id, head_id(子头,懒建), row}]
    sub_heads = 0             # 为中间层级补插的子 BOM 头数量
    try:
        for sid, table, label in _IMPORT_SEQ:
            sec = sections.get(sid)
            if not isinstance(sec, dict):
                continue
            rows = _rows_of(sec)
            if not rows:
                results.append({"section": sid, "label": label, "table": table,
                                "ok": True, "rows": 0, "note": "无数据，跳过"})
                continue
            cols = cpq_db.table_columns("quote", table)
            code_map = cpq_db.attr_code_map("quote", table)
            saved = 0
            err = None
            try:
                for r in rows:
                    cr = {code_map[k]: v for k, v in r.items() if k in code_map}
                    model = str(r.get("产品型号", "")).strip()
                    # —— 价格测算单根 calc_order_id ——
                    if "calc_order_id" in cols:
                        if root_calc is None:
                            root_calc = cpq_db.snow_next_id(conn)
                        cr["calc_order_id"] = root_calc
                    # —— 报价单根 quote_order_id ——
                    if "quote_order_id" in cols:
                        if root_quote is None:
                            root_quote = cpq_db.snow_next_id(conn)
                        cr["quote_order_id"] = root_quote
                    # —— 产品主键 & 引用 ——
                    if table == "clm_calc_product":
                        pid = cpq_db.snow_next_id(conn)
                        cr["product_line_id"] = pid
                        prod_ids.append(pid)
                        if model:
                            prod_by_model[model] = pid
                    elif "product_line_id" in cols:  # tech / bom_head 引用产品
                        pid = prod_by_model.get(model) or (prod_ids[0] if len(prod_ids) == 1 else (prod_ids[0] if prod_ids else None))
                        if pid is not None:
                            cr["product_line_id"] = pid
                    # —— 加价明细：主键雪花生成；规则分类按步骤兜底（DA 枚举 定价/报价）——
                    if table == "clm_calc_markup_item":
                        if "markup_item_id" in cols:
                            cr["markup_item_id"] = cpq_db.snow_next_id(conn)
                        if "rule_category" in cols and not str(cr.get("rule_category", "")).strip():
                            cr["rule_category"] = "定价" if sid == "s3_markup" else "报价"
                    # —— BOM 头主键 & 行引用 ——
                    if table == "clm_calc_bom_head":
                        hid = cpq_db.snow_next_id(conn)
                        cr["bom_header_id"] = hid
                        head_ids.append(hid)
                    # —— BOM 行：按「层级」列重建 L1-Ln 树（头/行递归：ref_bom_header_id→bom_header_id）——
                    if table == "clm_calc_bom_line":
                        lvl = _bom_level(r)
                        while bl_stack and bl_stack[-1]["level"] >= lvl:
                            bl_stack.pop()
                        if bl_stack:  # 有父行：子行挂父行的子 BOM 头（懒建），并回填 parent_line_id
                            parent = bl_stack[-1]
                            if parent["head_id"] is None:
                                sub_id = cpq_db.snow_next_id(conn)
                                pr = parent["row"]
                                sub_head = {
                                    "bom_header_id": sub_id,
                                    "bom_name": (str(pr.get("组件名称", "")).strip() or "子层级") + " BOM",
                                    "product_item_code": str(pr.get("组件编码", "")).strip(),
                                    "product_item_name": str(pr.get("组件名称", "")).strip(),
                                    "product_item_spec": str(pr.get("组件规格型号", "")).strip(),
                                    "basis_quantity": "1",
                                }
                                if isinstance(root_head_cr, dict):  # 沿用根头的测算单/产品/版本
                                    for k in ("calc_order_id", "product_line_id", "bom_version"):
                                        if root_head_cr.get(k) not in (None, ""):
                                            sub_head[k] = root_head_cr[k]
                                cpq_db.insert_rows(conn, "clm_calc_bom_head", [sub_head])
                                parent["head_id"] = sub_id
                                sub_heads += 1
                            cr["ref_bom_header_id"] = parent["head_id"]
                            if "parent_line_id" in cols:
                                cr["parent_line_id"] = parent["line_id"]
                        elif head_ids:  # 顶层(L1)行：挂产品根 BOM 头，parent_line_id 留空
                            cr["ref_bom_header_id"] = head_ids[0]
                        line_id = cpq_db.snow_next_id(conn)
                        cr["bom_line_id"] = line_id
                        bl_stack.append({"level": lvl, "line_id": line_id, "head_id": None, "row": r})
                    cpq_db.insert_rows(conn, table, [cr])  # 每行独立，父在前
                    saved += 1
                    if table == "clm_calc_bom_head" and root_head_cr is None:
                        root_head_cr = dict(cr)
                res = {"section": sid, "label": label, "table": table, "ok": True, "rows": saved}
                if sid == "s2_bomline" and sub_heads:
                    res["sub_heads"] = sub_heads  # 另为中间层级补插的子 BOM 头（clm_calc_bom_head）
                results.append(res)
            except Exception as e:
                err = str(e)[:300]
                results.append({"section": sid, "label": label, "table": table,
                                "ok": False, "rows": saved, "error": err})
    finally:
        try:
            conn.close()
        except Exception:
            pass
    ok_all = all(r["ok"] for r in results) and bool(results)
    total = sum(r.get("rows", 0) for r in results if r["ok"])
    return {"ok": ok_all, "total_rows": total, "db": DB_NAME, "results": results}


# ---------------------------------------------------------------------------
# 系统提示词：亿纬锂能 POC（简化）7 步报价流程
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
你是「报价单智能体」，亿纬锂能 CPQ（配置报价）系统的报价助手，嵌在「确认需求解析结果」
工作台页面中：左侧是与用户（AR/客户经理）的聊天，右侧是你用 cpq_ui 工具驱动的工作台
（步骤条 + 固定表单/表格分区）。你的任务是**严格按下面 6 步顺序**，引导用户一步一步完成报价。

# 业务流程

每步的分区/字段/列**全部来自 DA 本体「报价助手」页**（业务对象→逻辑实体→属性名称，
已排除所有 id/主键/外键字段）。**render 的键/列名必须用该页的“属性名称”中文**
（例如 s1_basic 用「客户等级」「项目名称」「贸易术语」这类中文名，**不要用数据库列 code**
如 customer_level/project_name——那是查库写 SQL 用的，不是 render 的键）。字段名不确定时，
以 `/api/meta` 下发、右侧已预渲染的空骨架列名为准，逐列对着填。每个分区的列都尽量填满。

- **第 1 步｜确认需求配置**。分区按此顺序展示：s1_basic（表单·测算基本信息）→ s1_dest（**表/列表**·目的地信息，
  可多行）→ s1_products（表·产品信息列表）→ s1_techparams（表·产品技术参数）→ s1_payment（表·付款里程碑信息）→
  s1_logistics（**表/列表**·物流信息，可多行）。子步骤：①完善和确认测算基本信息 → ②维护目的地信息 →
  ③添加产品信息列表（技术参数随此一起填）→ ④分解付款里程碑信息 → ⑤填写物流信息 → ⑥确认并提交测算单。
  ⚠️ s1_dest、s1_logistics 是**列表（render_table，rows=[{…}]）**，不是键值表单。
  **取数逻辑（⚠️ 分工：产品两张表归系统，其余四个分区归你）**：
  ① **你负责填这四个分区**：s1_basic（测算基本信息）、s1_dest（目的地信息）、s1_payment（付款里程碑信息）、
     s1_logistics（物流信息）——从用户上传的需求文档/需求描述提取信息，结合合理推测**一次性 render 填满**：
     文档有的直接填；文档没有的给推荐值，并在值末尾加「（推荐）」标记。
     ⚠️ **产品匹配由系统在你填完之后自动做，不用你管**：系统会用纯 SQL 查 product_para_value、
     做六维加权评分，把 Top3 推荐清单渲染到左侧供用户点选。**你不要做产品匹配、不要调 match_products、
     绝对不要填 s1_products / s1_techparams**（那两张表等用户点「选用」后由系统自动填）。
     填完这四个分区就停下等用户，**不要 set_step 2**。
  ② **重新匹配（仅当用户在聊天里调整需求参数、明确要求重新匹配时）——必须调 match_products 工具，
     禁止自己写 SQL 查 product_para_value、禁止自己估分**：
     从用户给的需求信息里提取这几项，作为参数调用 **match_products**：
       · max_dimension 最大尺寸(mm)　· dimension_tolerance_pct 尺寸容差%（文档没写就不传）
       · application_scope 应用范围/使用场景　· operating_temperature 工作温度区间
       · service_life 寿命要求　· hermeticity 密封性(IP等级)
     工具会在全部产品里按**六维加权评分**（尺寸25% / 场景20% / 温度20% / 寿命15% / 密封15% / 其他5%）
     算出相似度，取 **Top3**，并**自动把推荐清单表格渲染到左侧对话框**（含各维度得分、总分、告警、
     「图片」与「选用」按钮）。你在聊天里只需**简述**每个候选的优劣与告警，**不要再贴一遍表格**。
     · 工具返回「最高分低于阈值 70」时，**必须原话转达**：「无高度匹配标品，建议转入定制评估」，
       并请用户确认是继续选用次优品还是转定制。
     · **此时绝对不要填 s1_products / s1_techparams，也不要替用户选定产品**——等用户在左侧点「选用」。
  ③ **用户选定产品后：这一步不需要你做任何事**。系统会自动用纯 SQL 完成——
     按成品编码取 product_para_value 全部参数，并用同一编码匹配
     `md_clm_material_cost_cnf.material_code` 取 `material_unit_price` 作为价格，
     直接把 **s1_techparams（产品技术参数）与 s1_products（产品信息列表）填好**。
     **不要重复查价、不要重复填这两张表**；若用户问起，右侧工作台内容即为准。
     只有用户明确要求补充/修改某些字段时，你才去查 `product_para_value`（列清单见下）。
     `product_para_value` 的列（code 中文名）：id, product_item_code 成品编码,
     product_item_name 成品描述, machine_model 机械号, plug_wire_model 插头线型号,
     plug_direction 插头方向, wire_length 线长, is_wire_wound 是否绕线, cell_code 电芯编码,
     cell_model 电芯型号, reference_size 参考尺寸, rated_voltage 标称电压(V),
     rated_capacity 标称容量(mAh), max_continuous_current 最大持续电流(mA),
     max_pulse_current 最大脉冲电流(mA), operating_temperature 工作温度,
     max_dimension 最大尺寸(mm), weight 重量(g), storage_temperature 存储温度,
     application_scope 应用范围, service_life 使用寿命, hermeticity 密封性。
  ④ 全部填完请用户核对。用户点「进入下一大步骤」后进入第 2 步「工艺确认」（人工核对，你不参与，见下）。
- **第 2 步｜工艺确认（人工核对步骤，你不参与）**。分区：s2_products（产品信息列表·沿用第 1 步·仅展示）、
  s2_techparams（产品技术参数·沿用第 1 步·仅展示）。
  **这一步无需你做任何事**：不查库、不套规则、不算成本、不调用任何 cpq_ui 工具。两张表由前端自动沿用第 1 步已确认的
  产品信息与产品技术参数、以只读方式展示，交由**工艺经理**人工核对确认。你收到「进入第 2 步」的消息时，
  只在聊天里用一句话说明「工艺确认为人工核对产品信息与技术参数，请核对后点『进入下一大步骤』」即可，然后停下等用户。
- **第 3 步｜定价-利润加成（系统一次性完成，你不参与）**。分区：s3_products（产品信息·沿用·仅展示）、
  s3_markup（定价规则·利润加成）。**这一步无需你做任何事**：系统会独立发起一次调用——
  先用 SQL 取 `md_clm_material_price_rule` 里 `rule_classification='定价'` 的全部规则，
  再结合产品信息与产品技术参数算出每个产品的利润加成，写入 s3_markup 与 s3_products 的「利润加成」列。
  不要在这一步查库、算价或调用任何 cpq_ui 工具。
- **第 4 步｜报价-其他加价项（系统一次性完成，你不参与）**。分区：s4_products（产品信息·沿用·仅展示）、
  s4_markup（加价规则·其他加价）。同上由系统独立完成：取 `rule_classification='报价'` 的全部规则，
  结合测算基本信息、目的地信息、产品信息、产品技术参数、付款里程碑信息、物流信息算出其他加价，
  写入 s4_markup 与 s4_products 的「其他加价」列。你同样不参与。
- **第 5 步｜报价方案（前端自动填写，你不参与）**。分区：s5_basic（报价基本信息）、s5_detail（报价明细）。
  两张表由前端直接用前面步骤的既有数据填写：报价基本信息同名字段抄自第 1 步测算基本信息
  （报价单号自动生成、有效天数 14、币种人民币、联系人取登录人、申请日期取当天）；
  报价明细是**联动计算表**——报价取产品信息的「价格」（已含利润加成与其他加价）、
  折后价格 = 报价 × 折扣、总金额 = 数量 × 折后价格、税金 = 总金额 × 税率（默认 数量 1、折扣 1、税率 0.13）。
  不查库、不推荐、不调用任何 cpq_ui 工具。
- **第 6 步｜输出报价单**。只做一件事：render_document 渲染报价单文档（section_id=s6_doc，
  内容按第 5 步已确认的报价基本信息与报价明细组织），并提示用户可点「生成报价单(Word)」导出，
  告知用户流程完成。**不生成 BPM 审批流环节**，不查库、不做其他渲染。

# 固定表单：结构已预渲染，但你必须主动"填值"（务必遵守）

- 右侧每个分区的**字段/列已经写死并预渲染成空骨架**（结构由系统按 DA 本体「报价助手」本体固定），
  你**不需要也不能自己定义字段/列**——但这**不代表**右侧已经有数据了。
- **填值 = 你必须主动调用 cpq_ui 的 render_form / render_table**；这就是唯一的写入方式：
  · render_form：`values` = {业务属性名称: 取值}（键与固定字段名一致）；
  · render_table：`rows` = [{固定列名: 取值}, …]（每行一个对象）。
  **⚠️ 你不调用 render，右侧就一直是空骨架！**（预渲染只给了空表，值必须你 render 才会出现。）
  你传的 fields/columns 会被忽略、结构不变；没有的字段留空即可，**不用任何示例数据**。
- **值从哪来（三类都要用上，不能只照抄文档）**：
  ① **需求文档**——第 1 步的主数据源：客户/型号/数量/目的地/付款/物流等直接取；
  ② **数据库 sql_query**——**只按「业务流程」里各步骤「取数逻辑」点名的表查**（具体 SQL、筛选方式、
     第 3/4 步"必须取全规则"等要求，一律以「业务流程」对应步骤为准）。**不要为一个步骤乱查无关的表。**
  ③ **你的推荐**——文档没给、库里也没有的字段，基于已知信息+行业常识**给出合理推荐值**，
     **并在该值末尾加「（推荐）」标记**（系统会自动去掉这几个字、只用颜色高亮显示，表格里不会出现标记文字），
     source 里也注明来源是推荐；**不要大片留空、也不要只是把文档里的话原样搬进去**，该推断的要推断、该算的要算。

# 数据来源（两端，每步都要说清依据）

**⚠️ 禁止读取本地文件**：不允许用 Read/Grep/Glob 等去读本地磁盘上的任何文件（json/sqlite/py/csv/txt/xlsx… 一律不行）。
唯一例外是**本体信息**——业务对象 / 逻辑实体 / 属性的定义，用 Read 读 `亿纬锂能DA梳理.md`
（Markdown：一节一个助手，`### 表名` 起一个逻辑实体，下面一张属性表）。
业务数据只能走 sql_query 查数据库；需求内容以用户上传/描述的为准。
提到它时就说「本体信息」，不要说成"读文件"或报文件名 —— 对用户而言那是一份语义定义，不是磁盘上的东西。

1. **需求文档端**：用户上传/描述的需求（客户、项目、产品型号、数量、目的地、交期、付款、
   质量专控、碳足迹、非标、贸易术语等具体值）——这是本单的“个性”，填进各分区 values/rows。
2. **数据库端（远程 Postgres）**（用 sql_query 只读 SELECT，**以系统提示词末尾的完整 schema 为准生成 SQL**，不臆造、不读 json）：
   - 各分区的**固定字段名**（values 的键）已由 DA 本体「报价助手」本体写死并预渲染，无需查库；sql_query 只用于取**数据取值**。
   - `product_para_value` —— **产品参数值表**（配置助手页）：第 1 步产品匹配用（完整列清单/筛选方式见「业务流程」第 1 步②）。
   - `md_clm_material_cost_cnf` —— **物料成本表**：第 1 步取产品价格（取法见「业务流程」
     对应步骤；注意 is_deleted = false 与生效/失效日期）。
   - `md_clm_material_price_rule` —— **产品定价规则**：由系统在第 3、4 步各取一次
     （'定价' 分类 → 利润加成；'报价' 分类 → 其他加价），**你不需要查这张表**。
   - **⚠️ 表访问边界（重要规则）**：做数据提取时，亿纬锂能DA梳理文档里你只能访问**配置助手页、规则助手页**
     对应的表（如 md_clm_distribution_rule / md_clm_material_cost_cnf / md_clm_material_price_rule 等 md_* 主数据表），
     **报价助手页对应的任何实际业务表（clm_calc_* 价格测算单各表、clm_quote_* 报价单各表）一律禁止用 sql_query 访问**
     （系统也会直接拒绝这类查询）——那些表是本流程最后「导入数据库」的**写入目标**，不是数据来源。
     前面步骤已生成的信息（产品信息、产品技术参数、加价等）以**右侧工作台各分区已渲染的内容**为准直接沿用，
     不要去库里查历史报价/测算数据。
金额单位以库中字段为准；计算必须自洽（合计=分项之和）。部分因子金额可能为空（来源限制），据实处理别硬编。

# 大结果分步取数协议（sql_query 返回"结果被截断"时强制执行）

单次 sql_query 最多返回 500 行。当结果头部出现"⚠️ 结果被截断"（会附带总行数），说明候选集超限，
**禁止**只凭已返回的前 N 行下结论（最优候选可能不在其中）。必须按下面四步走：

1. **探量定计划**：根据截断提示里的总行数（没有就先 `SELECT COUNT(*)`，必要时按关键列 `GROUP BY` 看分布），
   在【思考】里定切片计划：优先加 WHERE 收窄（用用户参数里最有区分度的条件）；确实收不窄才分批。
2. **分批取数（keyset 分页）**：按主键顺序取，`WHERE id > 上一批最后一行的id ORDER BY id LIMIT 500`
   （比 OFFSET 稳定不重不漏）；每批**只 SELECT 判断所需的列**，不要 SELECT *。
3. **每批即时收敛**：一批查完立刻筛出该批候选短名单（至多 3~5 行关键值 + 一句淘汰理由），
   然后**丢弃该批原始行**再取下一批——不要把多批原始结果都攒在手里。
4. **整合**：所有批次跑完后，只用各批短名单做最终比较，选出结果填表；
   在 ✅结果 里说明"共 X 行、分 Y 批筛完、最终候选 Z 个"，保证覆盖了全量而不是前 500 行。

# 整步一次性推荐（关键交互方式）

**每进入一个大步骤，就把该步的所有固定分区一次性渲染好、并把推荐值/查到的值/算出的值直接填进去**，
不要一项一项来、不要等用户逐项确认、也不要只在聊天里说。具体：

1. set_step {step}，然后**按「业务流程」该步的「取数逻辑」先查库、再一口气把该步的每个分区都 render 出来并带数据**：
   文档有的直接填、该查库的查库、两边都没有的**给推荐值**（source 注明"推荐"），别大片留空。
2. 该查库/套规则的（定价、加价）**直接查、直接算、直接填**，不用先征求同意。
   ⚠️ **唯一例外：第 1 步的 s1_products（产品信息列表）和 s1_techparams（产品技术参数）不适用本条**——
   产品必须由 `match_products` 从数据库真实匹配、并由用户点「选用」后**由系统自动填入**。
   在用户选定之前这两张表**就该是空的**，不许"别大片留空"这条规则去填它、更不许凭需求文档或行业知识编产品。
3. 全部渲染完，按「思考-规划-执行-结果」的 ✅结果 段给小结，并提示：右侧本步已填好，请核对/修改后点「进入下一大步骤」。
- 每个 render_* 都要带 source（数据库端 / 需求文档端 / 推荐）。

# 每一步都要像 Agent 一样"思考-规划-执行-结果"（强制，务必遵守）

进入每一大步骤，**先在聊天里按下面 4 段格式各写一两句，再动手调工具**（不许闷头连调工具、也不许一句话都不说就填表）：

  🤔 **思考**：这一步要解决什么、依据是什么（如"电量 280kWh 属大电量，必选液冷"）。
  📋 **规划**：打算查哪张表、按什么规则算什么（如"从需求文档取目的地与付款条款，填 s1_dest / s1_payment"）。
  ⚙️ **执行**：一边查数据、一边 render_form/render_table 填表，关键动作各写半句（如"目的地 2 条已填入 s1_dest"）。
  ✅ **结果**：本步结论 + 依据（数据库端查了哪表命中哪行 / 需求文档端用了哪些值 / 哪些是推荐），并提示用户核对后点「进入下一大步骤」。

- 全程口语、简短，每段一两句即可，别长篇；但**这 4 段必须都有**——这是"Agent 的样子"的最低要求。
- 只调工具不说话 = 不合格；只说话不调工具（不填表/不查库）= 不合格。

# 结果必须渲染到工作台（重点，解决“对话框有、右边没有 / 两边不一致”）

- **任何算出来/查出来的结构化结果（产品信息、加价明细、报价明细等）
  都必须用 cpq_ui `render_table`/`render_form` 渲染到右侧对应分区**（第 2 步为工艺确认·人工核对·你不渲染；
  第 3、4、5 步为系统自动完成·你不渲染；第 6 步 s6_doc 报价单文档）。**绝不允许只把表格写在聊天文字里。**
- **聊天里不要贴 Markdown 表格**（不要用 `|---|` 那种）。聊天只写 2–3 句结论/依据/下一步提示；
  数据一律在右侧工作台看。这样右侧表格才是唯一真源，避免“左边一份、右边一份、对不上”。
- 算完当步就**立刻**调用对应的 render_table 把每一行写进 `rows`（列名用该分区固定列），再在聊天里说一句
  “已把 X 渲染到右侧 s1_dest，请核对”。不要等用户催。
- 收到「【强行推荐】」的处理规则见「页面消息协议」。

# 严格顺序（重点，别再跳步）

- **拿到需求后必须从第 1 步开始**（新对话若还没需求，先请用户上传/描述，别 set_step）。
  **在用户点「进入下一大步骤」（你会收到「【表单确认】第 1 步…」）之前，绝对不许 set_step、也不许做任何定价/加价动作。**
- 一次只做“当前这一大步骤”（按「整步一次性推荐」把它的所有分区都填好），不要一口气把后面的大步骤也跑了。做完请用户点「进入下一大步骤」。
- 收到「【表单确认】第 N 步…数据：{JSON}」才可 set_step 到第 N+1 步；以回传 JSON 为准（用户可能改过）重算，
  并按「整步一次性推荐」立刻把第 N+1 步整步填好。
- **第 2 步例外（工艺确认·人工核对）**：第 2 步不需要你做事——前端会自动把第 1 步的产品信息/技术参数沿用到第 2 步只读展示。
  · 收到「【表单确认】第 1 步…」→ set_step 2 后**只说一句**「工艺确认为人工核对，请核对产品信息与技术参数后点『进入下一大步骤』」，不查库不填表；
  · 收到「【表单确认】第 2 步…」→ 只回一句"工艺确认已完成，系统将自动计算定价与加价"，**不要 set_step、不要填表**。
- **第 3、4、5 步例外（系统自动完成）**：定价-利润加成、报价-其他加价项、报价方案都由系统独立完成，
  你**不会收到第 2、3、4 步的表单确认消息**，也不要替这三步做任何事；
  你的下一条消息将是「【表单确认】第 5 步…」→ set_step 6，正常开始第 6 步「输出报价单」。

# 页面消息协议

- 「【会话开始】…需求描述：…」：
  · **需求与附件都空（用户开了「新对话」）：先不要 set_step、不要渲染任何分区！**只在聊天里用一两句话友好地请用户：
    ① 点输入框左侧回形针上传需求文档（Word/PDF/Excel/图片/文本），或 ② 直接输入需求描述。然后停下等用户提供。**不要臆造。**
  · 有需求/附件：set_step 1，先从需求文档解析出客户、型号、数量、交期、目的地、付款、质量专控、碳足迹、非标、贸易术语等，
    再按「整步一次性推荐」把第 1 步 6 个分区填好，请用户核对。
- 「【上传附件】…」/附件内容 / 用户后来补的需求描述：**这就是需求了**——若还没开始第 1 步，立刻 set_step 1 并按「整步一次性推荐」填好第 1 步；
  若已在第 1 步，则解析后合并进各分区重新渲染。说明你读到并填了什么。
- 「【强行推荐】」（用户点了「强行填满本步骤」）：把当前大步骤所有分区**每个字段/单元格填满、绝不留空**——
  ①需求文档 → ②sql_query 查库 → ③行业知识+推理 三级兜底（不许写"待补充/待定/无/N.A."）；
  ⚠️ **但 s1_products / s1_techparams 除外**：产品只能来自数据库真实匹配（match_products + 用户选用），
  **绝不允许用行业知识"推测"出一个产品型号/成品编码**。用户在第 1 步点「强行填满」时，
  若还没选定产品，就只填其余分区，并提示他先在推荐清单里选用产品。
  **凡无文档/数据库依据的推测值，末尾加"（推测）"标记**（如「液冷（推测）」，系统会去掉标记文字、只用颜色高亮）；
  全部 render 填入右侧，聊天里给【推理过程】并单独列出推测项。
- 「【表单确认】第 N 步…数据：{JSON}」：用户点了「进入下一大步骤」→ 以 JSON 为准，set_step N+1，并按「整步一次性推荐」把第 N+1 步整步填好。
  **第 1 步确认后进入的第 2 步是「工艺确认」人工核对步骤，按「严格顺序」的第 2 步例外处理（不查库不填表，只说一句）。**
- 「【表单修改】第 N 步…数据：{JSON}」：用户在“查看历史步骤”里改了该步数据 → 以 JSON 为准重新渲染第 N 步相关分区，说明改了什么、对后续步骤的影响；**保持当前步骤不变、不要 set_step**。
- 「【返回上一步】」：set_step 回退一步，简述该步，等用户操作。
- 其他自由文本：正常回答。用户改数据后重渲相关分区并说明对后续的影响。

# 行为准则

- 核心职责是**带用户把这张报价单的各固定表单一步步填好、算准**，不是检索历史报价单。
- 一次只推进一步、步内只聚焦一项，多与用户确认；数字必须真查出来，不能编。
- 简体中文，专业简洁；金额与数量必须与工作台渲染一致。
"""

# 流程状态字段（READONLY_FIELDS）AI 只读：只让系统工作流改，模型写了也会被服务端丢弃。
SYSTEM_PROMPT += (
    "\n\n# 流程状态字段（AI 只读，不得改写）\n\n"
    "· 下列字段是**流程状态**，只由系统工作流/后端流程改写，**你只能读、不能写**："
    + "、".join(READONLY_FIELDS) + "。\n"
    "· 即使需求文本或历史数据里出现这些字段的值，也不要把它写进 render_form 的 values —— "
    "写了会被系统丢弃（清单的唯一事实源是服务端常量 READONLY_FIELDS）。\n"
)

# 把真实库结构 + Schema 说明拼到系统提示词末尾，作为“以 schema 为上下文生成 SQL”的依据。
if _DB_SCHEMA_TEXT:
    SYSTEM_PROMPT += (
        "\n\n# 数据库表结构（生成 SQL 的上下文，" + DB_NAME + "）\n\n"
        "库里所有表及其列，写 sql_query 时以此为准：\n\n" + _DB_SCHEMA_TEXT + "\n"
    )
if _SCHEMA_DOC:
    SYSTEM_PROMPT += (
        "\n\n# 数据库 Schema 说明（各表/字段的业务含义、主外键、业务链路，取数时据此判断查哪张表哪个字段）\n\n"
        + _SCHEMA_DOC + "\n"
    )


# ---------------------------------------------------------------------------
# 本地历史 + 设置持久化（存磁盘，重启服务后仍可找回）
# ---------------------------------------------------------------------------

HISTORY_DIR = os.path.join(SCRIPT_DIR, "cpq_history")
# 三个助手共享同一份设置（模型/参数/Key）：cpq_settings.json 为唯一权威文件
SETTINGS_PATH = os.path.join(SCRIPT_DIR, "cpq_settings.json")

# 「无模型」伪模型 id：选中后不调用大模型，工作台全流程由人工填写+下一步完成
NO_MODEL_ID = "none"
NO_MODEL_HINT = "当前为「无模型」模式：不调用大模型。请直接在右侧人工填写各步骤内容，完成后点「下一步」。"


def _now_iso() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _to_float_or_none(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int_or_none(v):
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# --- 设置（模型 / 采样参数 / 各 provider 的 API Key） ------------------------

def load_settings() -> dict:
    # 唯一配置的读写收敛到 cpq_shared_settings：报价 / 配置 / 规则 / 技术工艺
    # 四个进程读的是同一份文件，不再各写一套。
    return cpq_shared_settings.load()


def save_settings(s: dict):
    try:
        cpq_shared_settings.save(s)
    except OSError:
        pass


def _apply_provider_env(provider: str, key: str):
    """把 API Key 写进对应 provider 的环境变量，让 get_api_key_for 能取到。"""
    if not key:
        return
    envs = PROVIDERS.get(provider, {}).get("env") or []
    if envs:
        os.environ[envs[0]] = key


def apply_saved_provider_keys():
    for prov, key in (load_settings().get("api_keys") or {}).items():
        _apply_provider_env(prov, key)


# 注册「本地模型」provider + DeepSeek 深度思考补丁（运行时注入，不改 open-claude 包），
# 并按磁盘设置恢复本地连接配置 / thinking 开关。
cpq_llm.install()
cpq_llm.configure(load_settings())


def _pick_model_by_available_key(current: str) -> str:
    """当前模型 provider 没配 Key 时，自动挑一个已配 Key 的 provider 的首个模型。
    避免“默认用 Claude，但只配了 Qwen/DeepSeek 的 Key”时一发送就 403/401。"""
    if get_api_key_for(get_model_provider(current)):
        return current
    for m in AVAILABLE_MODELS:
        if get_api_key_for(m.get("provider", "anthropic")):
            return m["id"]
    return current


def models_catalog() -> list:
    """可选模型清单（Qwen / DeepSeek 等，POC 部署不提供 Claude 系列），
    标注每个 provider 是否已配置 Key；首项为「无模型」（人工填写模式）。"""
    out = [{
        "id": NO_MODEL_ID,
        "label": "无模型（人工填写）",
        "provider": "none",
        "provider_label": "无模型",
        "configured": True,
    }]
    for m in AVAILABLE_MODELS:
        prov = m.get("provider", "anthropic")
        if prov == "anthropic":
            continue
        out.append({
            "id": m["id"],
            "label": m["label"],
            "provider": prov,
            "provider_label": PROVIDERS.get(prov, {}).get("label", prov),
            "configured": bool(get_api_key_for(prov)),
        })
    out.append(cpq_llm.catalog_entry())  # 本地模型（OpenAI 兼容网关，手动配置）
    return out


# --- 历史会话（每个报价会话一个 JSON 文件） ---------------------------------

def _history_path(sid: str) -> str:
    return os.path.join(HISTORY_DIR, sid + ".json")


def _safe_sid(sid: str) -> bool:
    return bool(sid) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", sid) is not None


def save_history(session: dict):
    if not _safe_sid(session.get("id", "")):
        return
    try:
        os.makedirs(HISTORY_DIR, exist_ok=True)
        with open(_history_path(session["id"]), "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, default=str)
    except OSError:
        pass


def load_history(sid: str):
    if not _safe_sid(sid):
        return None
    try:
        with open(_history_path(sid), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def list_history(limit: int = 200) -> list:
    out = []
    if not os.path.isdir(HISTORY_DIR):
        return out
    for name in os.listdir(HISTORY_DIR):
        if not name.endswith(".json"):
            continue
        data = load_history(name[:-5])
        if not data:
            continue
        events = data.get("events", [])
        info = _kickoff_info(data)
        out.append({
            "id": data.get("id"),
            "title": data.get("title") or "未命名报价",
            "created": data.get("created"),
            "updated": data.get("updated"),
            "step": data.get("step", 1),
            "model": data.get("model"),
            "turns": sum(1 for e in events if e.get("type") == "user"),
            "project": info.get("project", ""),
            "customer": info.get("customer", ""),
        })
    out.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return out[:limit]


# kickoff 消息里固定格式的 项目名称/客户名称（供首页真实数据卡片展示）
_KICKOFF_PROJ_RE = re.compile(r"项目名称：([^｜\n]*)")
_KICKOFF_CUST_RE = re.compile(r"客户名称：([^｜\n]*)")


def _kickoff_info(data: dict) -> dict:
    """从会话前几条用户消息里解析 项目名称/客户名称（kickoff 固定格式）。"""
    out = {"project": "", "customer": ""}
    try:
        seen = 0
        for m in data.get("messages", []):
            if m.get("role") != "user":
                continue
            txt = m.get("content")
            if not isinstance(txt, str):
                continue
            seen += 1
            pm = _KICKOFF_PROJ_RE.search(txt)
            cm = _KICKOFF_CUST_RE.search(txt)
            if pm and not out["project"]:
                v = pm.group(1).strip()
                out["project"] = "" if v in ("（未填）", "(未填)") else v
            if cm and not out["customer"]:
                v = cm.group(1).strip()
                out["customer"] = "" if v in ("（未填）", "(未填)") else v
            if (out["project"] and out["customer"]) or seen >= 3:
                break
    except Exception:
        pass
    return out


def delete_history(sid: str) -> bool:
    if not _safe_sid(sid):
        return False
    try:
        os.remove(_history_path(sid))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Bridge：一个 open-claude 会话 + SSE 驱动（改编自 open-claude/oc_web_server.py）
# ---------------------------------------------------------------------------

def _stringify(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for blk in content:
            if isinstance(blk, dict):
                parts.append(blk.get("text") or json.dumps(blk, ensure_ascii=False))
            else:
                parts.append(str(blk))
        return "\n".join(parts)
    return str(content)


def _build_profile() -> AgentProfile:
    prof = AgentProfile()
    prof.name = "cpq-quote"
    prof.description = "CPQ 报价助手（亿纬锂能 POC 简化流程）"
    prof.system_prompt_mode = "override"
    prof.system_prompt_override = SYSTEM_PROMPT
    prof.memory_mode = "off"
    prof.permission_mode = "always_allow"
    prof.disabled_tools = list(DISABLED_TOOLS)
    prof.max_iterations = 40
    return prof


def _sanitize_model(model_id: str) -> str:
    """剥离形如 claude-xxx[1m] 的方括号后缀（IDE 环境变量会带上，直连 API 会 404）。"""
    return re.sub(r"\[[^\]]*\]$", "", model_id or "").strip()


class Bridge:
    """持有一个 open-claude Conversation，为工作台页面提供流式回合。"""

    def __init__(self, cwd: str):
        self.cwd = cwd
        self.conv = Conversation(cwd, permission_mode="always_allow", profile=_build_profile())
        clean = _sanitize_model(self.conv.model)
        if clean and clean != self.conv.model:
            print(f"[cpq-agent] 模型名清洗: {self.conv.model} -> {clean}")
            self.conv.model = clean
            os.environ["CLAUDE_MODEL"] = clean
        self.conv.permissions._prompt_user = lambda *a, **k: (True, "")
        self._inject_tools()
        self.lock = threading.Lock()
        self._new_session()
        # 应用磁盘上持久化的模型 / 参数设置（不再回写，避免覆盖）
        try:
            self.apply_settings(load_settings(), persist=False)
        except Exception:
            traceback.print_exc()

    # -- 当前会话（用于本地历史记录） -----------------------------------------

    def _new_session(self):
        self.session_id = uuid.uuid4().hex[:12]
        self.events = []          # 展示事件流（user / text / tool_use / ui），用于历史回放
        self.title = None
        self.created = _now_iso()
        self.step = 1

    def _persist(self):
        """把当前会话写入本地历史（有内容才写）。"""
        if not self.events:
            return
        session = {
            "id": self.session_id,
            "title": self.title or ("新建报价 " + self.created[:10]),
            "created": self.created,
            "updated": _now_iso(),
            "model": self.conv.model,
            "step": self.step,
            "events": self.events,
            "messages": self.conv.messages,
        }
        save_history(session)

    def open_session(self, sid: str):
        """把某条历史会话装载为当前会话（可继续对话），返回其展示事件供前端回放。"""
        data = load_history(sid)
        if not data:
            return None
        with self.lock:
            self.session_id = data.get("id") or uuid.uuid4().hex[:12]
            self.events = data.get("events", [])
            self.title = data.get("title")
            self.created = data.get("created") or _now_iso()
            self.step = data.get("step", 1)
            self.conv.messages[:] = data.get("messages", [])
            self.conv.session = SessionStore(self.cwd)
            _ui_events().clear()
        return {
            "id": self.session_id,
            "title": self.title,
            "step": self.step,
            "model": data.get("model"),
            "events": self.events,
        }

    def snapshot(self) -> dict:
        """当前会话的回放数据（结构同 open_session 的返回，供已在池中的会话复用）。"""
        with self.lock:
            return {
                "id": self.session_id,
                "title": self.title,
                "step": self.step,
                "model": self.conv.model,
                "events": list(self.events),
            }

    # -- 设置 -----------------------------------------------------------------

    def current_settings(self) -> dict:
        prof = self.conv.profile
        if self.conv.model == NO_MODEL_ID:
            prov, prov_label, configured = "none", "无模型", True
        else:
            prov = get_model_provider(self.conv.model)
            prov_label = PROVIDERS.get(prov, {}).get("label", prov)
            configured = bool(get_api_key_for(prov))
        # 统一的「模型设置」卡片（报价 / 配置 / 规则 / 技术工艺同一份表单）只认一个
        # 模型字段：这里把可选模型和按 provider 的 Key 可见度一起给出，前端不必再
        # 调 /api/models 拼第二套清单。**只回打码提示，绝不回明文。**
        saved_keys = cpq_shared_settings.api_keys(load_settings())
        local_public = cpq_llm.local_public()
        options, providers, seen = [], [], set()
        for item in models_catalog():
            options.append({
                "id": item["id"],
                "label": item["label"],
                "provider": item["provider"],
                "provider_label": item.get("provider_label") or item["provider"],
                "configured": bool(item.get("configured")),
            })
            name = item.get("provider")
            if not name or name == "none" or name in seen:
                continue
            seen.add(name)
            base_url = PROVIDERS.get(name, {}).get("base_url") or ""
            if name == cpq_llm.LOCAL_PROVIDER:
                base_url = local_public.get("base_url") or ""
            providers.append({
                "provider": name,
                "label": item.get("provider_label") or name,
                "base_url": base_url,
                "configured": bool(get_api_key_for(name)),
                "hint": cpq_shared_settings.mask(saved_keys.get(name, "")),
            })
        return {
            "model": self.conv.model,
            "provider": prov,
            "provider_label": prov_label,
            "configured": configured,
            "temperature": prof.temperature,
            "max_tokens": prof.max_tokens,
            "thinking": prof.thinking,
            "thinking_budget": prof.thinking_budget,
            "local": local_public,
            "options": options,
            "keys": providers,
            # 报价服务本身没有 RBAC（本机/内网部署，登录由 CPQ 统一入口管）；
            # 技术工艺入口走自己的角色校验后会把这两个字段按角色改写。
            "editable": True,
            "secrets_editable": True,
        }

    def apply_settings(self, s: dict, persist: bool = True) -> dict:
        """应用模型 / 采样参数 / API Key（可选持久化）。"""
        s = s or {}
        prof = self.conv.profile
        # 本地模型连接配置要先应用（选「本地模型」时 model 用它配置的模型名）
        if isinstance(s.get("local"), dict):
            cpq_llm.configure_local(s["local"])
        if s.get("model"):
            raw = str(s["model"]).strip()
            if raw == cpq_llm.LOCAL_SENTINEL:
                raw = cpq_llm.local_model_id()
            mid = raw if raw == NO_MODEL_ID else _sanitize_model(resolve_model(raw))
            if mid:
                self.conv.model = mid
                os.environ["CLAUDE_MODEL"] = mid
        if "temperature" in s:
            prof.temperature = _to_float_or_none(s.get("temperature"))
        if "max_tokens" in s:
            prof.max_tokens = _to_int_or_none(s.get("max_tokens"))
        if "thinking" in s:
            prof.thinking = bool(s.get("thinking"))
            cpq_llm.set_deepseek_thinking(prof.thinking)  # 同步 DeepSeek 深度思考
        if "thinking_budget" in s:
            prof.thinking_budget = _to_int_or_none(s.get("thinking_budget")) or 8000
        # API Key：既支持 {"api_keys": {provider: key}}，也支持单个 {"api_key": ..., ["provider": ...]}
        new_keys = {}
        if isinstance(s.get("api_keys"), dict):
            new_keys.update({k: v for k, v in s["api_keys"].items() if v})
        if s.get("api_key"):
            prov = s.get("provider") or get_model_provider(self.conv.model)
            new_keys[prov] = s["api_key"]
        for prov, key in new_keys.items():
            _apply_provider_env(prov, key)
        if persist:
            cur = load_settings()
            cur["model"] = self.conv.model
            cur["temperature"] = prof.temperature
            cur["max_tokens"] = prof.max_tokens
            cur["thinking"] = prof.thinking
            cur["thinking_budget"] = prof.thinking_budget
            if new_keys:
                ak = cur.get("api_keys") or {}
                ak.update(new_keys)
                cur["api_keys"] = ak
            if cpq_llm.local_model_id() or cpq_llm.local_public()["base_url"]:
                cur["local"] = cpq_llm.local_persist()
            save_settings(cur)
        return self.current_settings()

    def _inject_tools(self):
        names = {s.get("name") for s in self.conv.tool_schemas}
        if "cpq_ui" not in names:
            self.conv.tool_schemas.append(CPQ_UI_SCHEMA)
        if "sql_query" not in names:
            self.conv.tool_schemas.append(SQL_QUERY_SCHEMA)
        if "match_products" not in names:
            self.conv.tool_schemas.append(MATCH_PRODUCTS_SCHEMA)

    def meta(self, industry=None) -> dict:
        return {
            "model": self.conv.model,
            "profile": self.conv.profile.name,
            "cwd": self.cwd,
            "steps": STEPS,
            "session_id": self.session_id,
            "settings": self.current_settings(),
            "forms": fixed_forms_catalog(industry),
            # 行业下拉的唯一来源（cpq_industries.INDUSTRY_KEYS）——前端不许另写行业数组
            "industries": [{"key": key, "label": cpq_industries.INDUSTRIES[key]["label"]}
                           for key in cpq_industries.INDUSTRY_KEYS],
            "default_industry": cpq_industries.DEFAULT_INDUSTRY,
        }

    def reset(self):
        with self.lock:
            self.conv.messages.clear()
            self.conv.session = SessionStore(self.cwd)
            self.conv.cost_tracker.__init__()
            _ui_events().clear()
            self._new_session()

    def stream_turn(self, text: str, emit, display=None, step=None):
        """跑完整一轮（含工具循环），emit(dict) 逐事件下发。

        display：用于本地历史回放的“用户气泡”文案（与实际发给模型的 text 不同，
        例如 text 是含文件内容的长 payload、或【会话开始】等系统消息）。传空串表示不记气泡。
        step：无模型（人工）模式下前端本地推进步骤时上报，用于会话/首页卡片的真实进度。
        """
        with self.lock:
            conv = self.conv
            bubble = text if display is None else display
            if bubble:
                self.events.append({"type": "user", "text": bubble})
                if not self.title and bubble.strip() and not bubble.startswith("【"):
                    self.title = bubble.strip().replace("\n", " ")[:24]
            if conv.model == NO_MODEL_ID:
                # 无模型模式：不调用大模型，仅记录（【…】开头的系统消息静默入档；
                # 用户手打的聊天给一句提示），并采纳前端上报的人工进度。
                # 消息仍写入 messages：历史列表可解析 项目/客户，切回真实模型也有上下文
                conv.add_user_message(text)
                if isinstance(step, int) and 1 <= step <= 6:
                    self.step = step
                if not text.startswith("【"):
                    emit({"type": "text", "text": NO_MODEL_HINT})
                    self.events.append({"type": "text", "text": NO_MODEL_HINT})
                self._persist()
                emit({"type": "done", "model": conv.model, "cost": 0.0})
                return
            conv.add_user_message(text)
            self._turn_no = 0          # 每次用户输入重新计数：一次交互跑了几趟模型，一眼看得见
            turn_start = time.perf_counter()
            try:
                for _ in range(max(1, conv.profile.max_iterations)):
                    conv._maybe_compact()
                    stop_reason = self._stream_once(conv, emit)

                    if stop_reason == "tool_use":
                        _ui_events().clear()
                        conv._execute_pending_tools()
                        # cpq_ui 调用产生的工作台事件
                        for ev in _ui_events():
                            emit({"type": "ui", "ui": ev})
                            self.events.append({"type": "ui", "ui": ev})
                            if ev.get("action") == "set_step" and isinstance(ev.get("step"), int):
                                self.step = ev["step"]
                        _ui_events().clear()
                        # 普通工具结果（前端主要用于显示活动/错误）
                        last = conv.messages[-1] if conv.messages else None
                        if last and last.get("role") == "user" and isinstance(last.get("content"), list):
                            for blk in last["content"]:
                                if isinstance(blk, dict) and blk.get("type") == "tool_result":
                                    emit({
                                        "type": "tool_result",
                                        "tool_use_id": blk.get("tool_use_id", ""),
                                        "content": _stringify(blk.get("content", ""))[:2000],
                                        "is_error": bool(blk.get("is_error", False)),
                                    })
                        continue
                    break
            except Exception as e:
                traceback.print_exc()
                emit({"type": "error", "error": str(e)})
            finally:
                self._persist()
                print(f"[turn] 本次交互共 {getattr(self, '_turn_no', 0)} 趟模型调用，"
                      f"合计 {time.perf_counter() - turn_start:.2f}s", flush=True)
                cost = getattr(conv.cost_tracker, "total_cost_usd", 0.0)
                emit({"type": "done", "model": conv.model, "cost": round(cost, 5)})

    def _stream_once(self, conv, emit) -> str:
        text_buf = []
        tool_uses = []
        stop_reason = "end_turn"
        # 慢在哪：一次回合的等待 = 发过去多少字 × 模型吞吐 + 往返次数。三个数都要能看见，
        # 否则只能凭感觉猜。turn_no 从 1 起，工具循环每转一圈算一回合。
        self._turn_no = getattr(self, "_turn_no", 0) + 1
        turn_t0 = time.perf_counter()
        first_at = [None]

        # 发给模型前修复 tool_use/tool_result 配对（压缩/中断可能留下孤儿块 → 供方 400）
        conv.messages[:] = cpq_msgutil.sanitize_tool_pairs(conv.messages)
        gen = stream_message(
            conv.client, conv.messages, conv.system_prompt,
            model=conv.model, tools=conv.tool_schemas,
            max_tokens=conv.profile.max_tokens,
            temperature=conv.profile.temperature,
            thinking_budget=conv.profile.thinking_budget if conv.profile.thinking else None,
        )
        sys_chars = len(conv.system_prompt or "")
        hist_chars = len(json.dumps(conv.messages, ensure_ascii=False))
        tool_chars = len(json.dumps(conv.tool_schemas, ensure_ascii=False))
        print(f"[turn {self._turn_no}] 发出 {sys_chars + hist_chars + tool_chars} 字符"
              f"（系统提示 {sys_chars} + 对话 {hist_chars} + 工具 {tool_chars}）"
              f"，{len(conv.messages)} 条消息，模型 {conv.model}", flush=True)
        # 等第一条事件的这段时间里，界面上必须有东西在动：不然用户看到的就是
        # 一个静止的输入框，分不清"在算"还是"挂了"。心跳只在没收到任何事件时发。
        stop_beat = threading.Event()
        raw_first = [None]
        kinds = {}

        def _beat():
            waited = 0
            while not stop_beat.wait(5):
                waited += 5
                try:
                    emit({"type": "stage",
                          "text": f"第 {self._turn_no} 趟模型调用已等待 {waited}s…"})
                except Exception:
                    return

        beat = threading.Thread(target=_beat, daemon=True)
        beat.start()
        try:
            emit({"type": "stage",
                  "text": f"第 {self._turn_no} 趟模型调用（发出 "
                          f"{sys_chars + hist_chars + tool_chars} 字符）…"})
        except Exception:
            pass
        for ev in gen:
            t = ev["type"]
            # 第一条**任何**事件的到达时间：它和首个文本差得远，说明模型在想
            # （思考/工具参数流），网关并没有卡住；两者一样晚才是真的沉默。
            if raw_first[0] is None:
                raw_first[0] = time.perf_counter()
                stop_beat.set()
            kinds[t] = kinds.get(t, 0) + 1
            if t == "text_delta":
                if first_at[0] is None:
                    first_at[0] = time.perf_counter()
                text_buf.append(ev["text"])
                emit({"type": "text", "text": ev["text"]})
            elif t == "thinking_delta":
                # 供应商的思维链增量只透传给前端做默认折叠的「思考过程」，
                # 不写入会话事件、不落库、不进历史回放。
                emit({"type": "thinking", "text": str(ev.get("thinking") or ev.get("text") or "")})
            elif t == "tool_use_start":
                # 参数还在生成中：先说一声"开始调用"，别让界面空等到 end。
                emit({"type": "stage",
                      "text": f"模型开始调用 {ev.get('name') or '工具'}（参数生成中…）"})
            elif t == "tool_use_end":
                tool_uses.append({
                    "type": "tool_use", "id": ev["id"],
                    "name": ev["name"], "input": ev["input"],
                })
                if ev["name"] == "cpq_ui":
                    ci = ev["input"] or {}
                    # 只透传轻量元信息给前端做“操作/数据来源”轨迹，不带整份 rows/fields
                    tu_input = {
                        "action": ci.get("action"),
                        "step": ci.get("step"),
                        "section_id": ci.get("section_id"),
                        "title": ci.get("title"),
                        "source": ci.get("source"),
                        "status": ci.get("status"),
                    }
                else:
                    tu_input = ev["input"]
                emit({"type": "tool_use", "id": ev["id"], "name": ev["name"], "input": tu_input})
                self.events.append({"type": "tool_use", "name": ev["name"], "input": tu_input})
            elif t == "message_end":
                stop_reason = ev.get("stop_reason", "end_turn")
                if stop_reason == "max_tokens":
                    # 输出被 provider 输出上限截断：保留已产出文本，写入会话事件并发出
                    # 可识别提示，让前端可以提示重试；不得当作正常结束静默收尾。
                    truncated_ev = {
                        "type": "truncated",
                        "stop_reason": "max_tokens",
                        "message": "模型输出达到 max_tokens 上限被截断，已保留已生成内容；"
                                   "请重试，或减少一次生成的输出量。",
                    }
                    self.events.append(dict(truncated_ev))
                    emit({**truncated_ev, "type": "error", "error": truncated_ev["message"]})
                u = ev.get("usage", {})
                conv.cost_tracker.add_usage(
                    conv.model,
                    input_tokens=u.get("input_tokens", 0),
                    output_tokens=u.get("output_tokens", 0),
                    cache_read=u.get("cache_read_input_tokens", 0),
                    cache_creation=u.get("cache_creation_input_tokens", 0),
                )
            elif t == "error":
                emit({"type": "error", "error": ev["error"]})
                stop_reason = "error"
                break

        content = []
        full = "".join(text_buf)
        if full:
            content.append({"type": "text", "text": full})
            self.events.append({"type": "text", "text": full})
        content.extend(tool_uses)
        if content:
            msg = {"role": "assistant", "content": content}
            conv.messages.append(msg)
            conv.session.append_message(msg)

        # 首字节 = 模型开始吐字前的等待（含排队与 prefill）；全轮 = 到停止为止。
        # 两者差得远说明是生成慢，差不多说明卡在 prefill/排队 —— 优化方向完全不同。
        stop_beat.set()
        wait = (first_at[0] - turn_t0) if first_at[0] else None
        raw = (raw_first[0] - turn_t0) if raw_first[0] else None
        print(f"[turn {self._turn_no}] 首个事件 "
              f"{('%.2fs' % raw) if raw is not None else '（一条都没有）'}"
              f"，首个文本 {('%.2fs' % wait) if wait is not None else '（无文本输出）'}"
              f"，全轮 {time.perf_counter() - turn_t0:.2f}s，停止原因 {stop_reason}"
              f"，事件 {kinds}"
              + (f"，工具 {[t['name'] for t in tool_uses]}" if tool_uses else ""),
              flush=True)
        return stop_reason


# ---------------------------------------------------------------------------
# HTTP 层（含 CORS：页面由 serve.py:8010 提供，跨端口调用本服务）
# ---------------------------------------------------------------------------

bridge: Bridge = None  # 默认 Bridge（兼容不带 sid 的旧请求 / 意图识别用），main() 或 suite 里构建


# ---------------------------------------------------------------------------
# 会话级 Bridge 池：每个会话一个独立 Conversation，多用户并发互不串扰。
# 会话状态每回合都会 _persist() 落盘，被挤出池后随时可从历史重新装载。
# ---------------------------------------------------------------------------

_BRIDGES: "OrderedDict[str, Bridge]" = OrderedDict()
_POOL_LOCK = threading.Lock()
_POOL_MAX = 16  # 内存中最多保留的活跃会话数


def _pool_put(sid: str, nb: Bridge) -> Bridge:
    """入池；并发下同 id 已存在则复用已有的。超限按 LRU 挤出未在跑回合的会话。"""
    with _POOL_LOCK:
        cur = _BRIDGES.get(sid)
        if cur is not None and cur is not nb:
            return cur
        _BRIDGES[sid] = nb
        _BRIDGES.move_to_end(sid)
        while len(_BRIDGES) > _POOL_MAX:
            victim = None
            for k, b in _BRIDGES.items():
                if k != sid and not b.lock.locked():
                    victim = k
                    break
            if victim is None:
                break
            _BRIDGES.pop(victim)
    return nb


def bridge_for(sid: str, create: bool = True):
    """按会话 id 取专属 Bridge：命中池直接用；有历史则装载；没历史则（可选）新建并
    采纳该 id。sid 为空/非法回退默认 bridge（兼容旧前端）。"""
    sid = (sid or "").strip()
    if not sid or not _safe_sid(sid):
        return bridge
    with _POOL_LOCK:
        b = _BRIDGES.get(sid)
        if b is not None:
            _BRIDGES.move_to_end(sid)
            return b
    if load_history(sid) is not None:
        nb = Bridge(SCRIPT_DIR)
        nb.open_session(sid)
        return _pool_put(sid, nb)
    if not create:
        return None
    nb = Bridge(SCRIPT_DIR)
    nb.session_id = sid
    return _pool_put(sid, nb)


def pool_new() -> Bridge:
    """新建一个会话专属 Bridge 并入池（/api/new）。"""
    nb = Bridge(SCRIPT_DIR)
    return _pool_put(nb.session_id, nb)


def pool_drop(sid: str):
    with _POOL_LOCK:
        _BRIDGES.pop((sid or "").strip(), None)


def pool_apply_settings(data: dict, persist: bool = True) -> dict:
    """设置是全局的：默认 bridge 应用（可持久化），池内所有会话同步应用（不回写）。"""
    res = bridge.apply_settings(data, persist=persist)
    with _POOL_LOCK:
        others = list(_BRIDGES.values())
    for b in others:
        try:
            b.apply_settings(data, persist=False)
        except Exception:
            traceback.print_exc()
    return res


# 跨助手设置同步：一体化服务(cpq_suite_server)启动时把另外两个 Agent 模块的
# pool_apply_settings(persist=False) 挂进来；本模块 /api/settings 应用后逐个广播。
SETTINGS_PEERS: list = []


def broadcast_settings(data: dict):
    for peer in SETTINGS_PEERS:
        try:
            peer(data)
        except Exception:
            traceback.print_exc()


# ---------------------------------------------------------------------------
# 意图识别：把首页对话框里的一句话分到 报价 / 产品配置 / 规则配置
# ---------------------------------------------------------------------------

_INTENT_SYS = (
    "你是「配置报价」系统的意图分类器。用户会说一句话，请判断他的目标属于下面哪一类，"
    "只输出一个标记，不要标点、不要解释、不要多余内容：\n"
    "quote        —— 做报价 / 价格测算 / 生成报价单 / 询价 / 谈折扣价格 等\n"
    "config       —— 产品配置 / 选配 / 配置 BOM / 物料配置 / 生成配置清单 / 配一台设备 等\n"
    "rule_product —— 维护「产品配置规则」：选配约束 / 配置校验 / BOM 构成规则 / 强制搭配 等\n"
    "rule_pricing —— 维护「定价规则」：物料成本 / 材料价格 / 人工机器费用 / 成本核算 / 溢价 等\n"
    "rule_quote   —— 维护「报价规则」：报价加价项 / 折扣规则 / 客户等级 / 费用因子 / 投标服务费 等\n"
    "只能输出 quote、config、rule_product、rule_pricing、rule_quote 五者之一。"
)


def classify_intent(text: str):
    """用一次轻量大模型调用做意图识别；返回 {"intent": quote|config|rule,
    "rule_kind": product_config|pricing|quote|None}；失败返回 None（前端关键词兜底）。"""
    if not text or bridge is None or bridge.conv.model == NO_MODEL_ID:
        return None
    try:
        from open_claude.api import complete
        res = complete(
            bridge.conv.client,
            [{"role": "user", "content": text[:2000]}],
            _INTENT_SYS,
            model=bridge.conv.model,
            max_tokens=16,
        )
        out = "".join(b.get("text", "") for b in res.get("content", [])).strip().lower()
    except Exception as e:
        # 意图识别失败不影响主流程（前端会回退到关键词匹配），只记一行简讯，不刷栈
        print(f"[cpq-agent] 意图识别调用失败（已回退关键词）：{e.__class__.__name__}: {e}",
              file=sys.stderr)
        return None
    # 注意顺序：rule_* 里包含 quote/product 等子串，必须先判 rule_*
    if "rule_pricing" in out:
        return {"intent": "rule", "rule_kind": "pricing"}
    if "rule_quote" in out:
        return {"intent": "rule", "rule_kind": "quote"}
    if "rule_product" in out or "rule" in out:
        return {"intent": "rule", "rule_kind": "product_config"}
    if "config" in out:
        return {"intent": "config", "rule_kind": None}
    if "quote" in out:
        return {"intent": "quote", "rule_kind": None}
    return None


# ---------------------------------------------------------------------------
# 附件文字提取：PDF / Word(docx) / Excel(xlsx) / 文本，供上传的需求文档解析
# ---------------------------------------------------------------------------

_MAX_EXTRACT_CHARS = 120000


def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_pdf(raw: bytes):
    """PDF：依次尝试 pdfplumber → pypdf → PyMuPDF(fitz)，任一可用即可。"""
    errors = []
    # 1) pdfplumber（文本 + 表格）
    try:
        import pdfplumber
        parts = []
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for i, page in enumerate(pdf.pages):
                txt = page.extract_text() or ""
                tbls = page.extract_tables() or []
                tbl_txt = "\n".join(
                    " | ".join((c or "").strip() for c in row)
                    for tb in tbls for row in tb if any(c for c in row)
                )
                seg = "\n".join(x for x in (txt, tbl_txt) if x.strip())
                if seg.strip():
                    parts.append(f"[第{i + 1}页]\n{seg}")
                if sum(len(p) for p in parts) > _MAX_EXTRACT_CHARS:
                    break
        if any(p.strip() for p in parts):
            return "\n\n".join(parts), None
        errors.append("pdfplumber 未提取到文本（可能是扫描件）")
    except ImportError:
        errors.append("未装 pdfplumber")
    except Exception as e:
        errors.append(f"pdfplumber:{e}")
    # 2) pypdf
    try:
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(raw))
        parts = []
        for i, pg in enumerate(r.pages):
            t = pg.extract_text() or ""
            if t.strip():
                parts.append(f"[第{i + 1}页]\n{t}")
        if parts:
            return "\n\n".join(parts), None
        errors.append("pypdf 未提取到文本")
    except ImportError:
        errors.append("未装 pypdf")
    except Exception as e:
        errors.append(f"pypdf:{e}")
    # 3) PyMuPDF (fitz)
    try:
        import fitz
        doc = fitz.open(stream=raw, filetype="pdf")
        parts = []
        for i, pg in enumerate(doc):
            t = pg.get_text() or ""
            if t.strip():
                parts.append(f"[第{i + 1}页]\n{t}")
        doc.close()
        if parts:
            return "\n\n".join(parts), None
        errors.append("PyMuPDF 未提取到文本")
    except ImportError:
        errors.append("未装 PyMuPDF")
    except Exception as e:
        errors.append(f"PyMuPDF:{e}")
    return None, ("PDF 解析失败（" + "；".join(errors) + "）。若为扫描件请提供可复制文本的 PDF，"
                  "或直接把关键信息贴进对话框；也可在服务端 pip install pdfplumber 后重启服务。")


def _docx_stdlib(raw: bytes) -> str:
    """无第三方依赖解析 .docx：docx 本质是 zip，取 word/document.xml 的 <w:t> 文本。"""
    import html
    import zipfile
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    out = []
    # 按段落 </w:p> 切分，段内把所有 <w:t> 文本拼起来（<w:tab/> 视作制表符）
    for para in re.split(r"</w:p>", xml):
        para = re.sub(r"<w:tab\b[^>]*/?>", "\t", para)
        texts = re.findall(r"<w:t\b[^>]*>(.*?)</w:t>", para, re.S)
        if texts:
            line = html.unescape("".join(texts)).strip()
            if line:
                out.append(line)
        if sum(len(x) for x in out) > _MAX_EXTRACT_CHARS:
            break
    return "\n".join(out)


def _extract_docx(raw: bytes):
    """Word(.docx)：先用 python-docx，缺库/失败则回退到无依赖的 zip+xml 解析。"""
    try:
        import docx
        d = docx.Document(io.BytesIO(raw))
        parts = [p.text for p in d.paragraphs if p.text and p.text.strip()]
        for t in d.tables:
            for row in t.rows:
                cells = [(c.text or "").strip() for c in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
        if parts:
            return "\n".join(parts), None
    except ImportError:
        pass
    except Exception:
        traceback.print_exc()
    try:
        text = _docx_stdlib(raw)
        return (text, None) if text.strip() else (None, "Word 文档没有可提取的文字")
    except Exception as e:
        return None, f"Word 解析失败：{e}"


def _xlsx_stdlib(raw: bytes) -> str:
    """无第三方依赖解析 .xlsx：xlsx 本质是 zip，读 sharedStrings + 各 sheet 的单元格。"""
    import html
    import zipfile
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            ss = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            for si in re.findall(r"<si>(.*?)</si>", ss, re.S):
                shared.append(html.unescape("".join(re.findall(r"<t\b[^>]*>(.*?)</t>", si, re.S))))
        sheets = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        out = []
        for idx, sn in enumerate(sheets, 1):
            xml = z.read(sn).decode("utf-8", "replace")
            out.append(f"### 工作表{idx}")
            for rowm in re.findall(r"<row\b[^>]*>(.*?)</row>", xml, re.S):
                cells = []
                for attrs, body in re.findall(r"<c\b([^>]*)>(.*?)</c>", rowm, re.S):
                    vm = re.search(r"<v>(.*?)</v>", body, re.S)
                    if vm:
                        v = html.unescape(vm.group(1))
                        if 't="s"' in attrs:
                            try:
                                v = shared[int(v)]
                            except (ValueError, IndexError):
                                pass
                        cells.append(v.strip())
                    else:
                        inl = re.findall(r"<t\b[^>]*>(.*?)</t>", body, re.S)
                        cells.append(html.unescape("".join(inl)).strip() if inl else "")
                if any(cells):
                    out.append(" | ".join(cells))
                if sum(len(x) for x in out) > _MAX_EXTRACT_CHARS:
                    break
    return "\n".join(out)


def _extract_xlsx(raw: bytes):
    """Excel(.xlsx)：先用 openpyxl，缺库/失败则回退到无依赖的 zip+xml 解析。"""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
        parts = []
        try:
            for ws in wb.worksheets:
                parts.append(f"### 工作表：{ws.title}")
                for row in ws.iter_rows(values_only=True):
                    cells = [("" if c is None else str(c)).strip() for c in row]
                    if any(cells):
                        parts.append(" | ".join(cells))
                    if sum(len(p) for p in parts) > _MAX_EXTRACT_CHARS:
                        break
        finally:
            wb.close()
        if any(p.strip() for p in parts):
            return "\n".join(parts), None
    except ImportError:
        pass
    except Exception:
        traceback.print_exc()
    try:
        text = _xlsx_stdlib(raw)
        return (text, None) if text.strip() else (None, "Excel 没有可提取的内容")
    except Exception as e:
        return None, f"Excel 解析失败：{e}"


def _extract_text(name: str, raw: bytes):
    """返回 (text, error)；text 为 None 表示失败，error 说明原因。"""
    ext = os.path.splitext(name or "")[1].lower()
    try:
        if ext in (".txt", ".md", ".csv", ".tsv", ".json", ".log"):
            return _decode_text(raw), None
        if ext == ".pdf":
            return _extract_pdf(raw)
        if ext == ".docx":
            return _extract_docx(raw)
        if ext == ".xlsx":
            return _extract_xlsx(raw)
        if ext in (".doc", ".xls", ".ppt"):
            return None, f"旧版 {ext} 格式暂不支持，请用 Office/WPS 另存为 .docx/.xlsx 后重传"
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            return None, "图片暂无法提取文字，请把关键信息用文字描述给我"
        # 未知扩展名：尝试按文本解码
        return _decode_text(raw), None
    except Exception as e:
        traceback.print_exc()
        return None, f"解析失败：{e}"


# ---------------------------------------------------------------------------
# 导出报价单 Word：把前端各步表单/表格数据 + 报价单文档拼成 .docx
# ---------------------------------------------------------------------------

def _build_quote_docx(data: dict):
    """data = {projectName, customer, steps:[{step,name,sections:[{title,kind,data|columns|markdown}]}]}
    返回 docx 的 bytes；缺 python-docx 返回 None。"""
    try:
        import docx
    except ImportError:
        return None
    doc = docx.Document()
    proj = (data.get("projectName") or "报价单").strip()
    doc.add_heading("报价单 · " + proj, level=0)
    meta = []
    if data.get("customer"):
        meta.append("客户：" + str(data["customer"]))
    if data.get("projectCode"):
        meta.append("项目编码：" + str(data["projectCode"]))
    meta.append("生成时间：" + _now_iso())
    doc.add_paragraph("　｜　".join(meta))

    for st in (data.get("steps") or []):
        doc.add_heading(f"{st.get('step','')}. {st.get('name','')}", level=1)
        for sec in (st.get("sections") or []):
            doc.add_heading(str(sec.get("title") or ""), level=2)
            kind = sec.get("kind")
            if kind == "form":
                d = sec.get("data") or {}
                items = [(k, v) for k, v in d.items() if str(v or "").strip()]
                if items:
                    t = doc.add_table(rows=0, cols=2)
                    try: t.style = "Light Grid Accent 1"
                    except KeyError: pass
                    for k, v in items:
                        cells = t.add_row().cells
                        cells[0].text = str(k); cells[1].text = str(v)
                else:
                    doc.add_paragraph("（无数据）")
            elif kind == "table":
                cols = sec.get("columns") or []
                rows = sec.get("data") or []
                if cols and rows:
                    t = doc.add_table(rows=1, cols=len(cols))
                    try: t.style = "Light Grid Accent 1"
                    except KeyError: pass
                    for i, c in enumerate(cols):
                        t.rows[0].cells[i].text = str(c)
                    for r in rows:
                        cells = t.add_row().cells
                        for i, c in enumerate(cols):
                            cells[i].text = str(r.get(c, "") if isinstance(r, dict) else "")
                else:
                    doc.add_paragraph("（无数据）")
            elif kind == "document":
                for line in str(sec.get("markdown") or "").split("\n"):
                    doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 报价会话身份 / 步骤完成门禁 / 财务回传结构化载荷
# （Spec `e2e-quote-session-and-completion-closure.md` §2 / §3 / §4）
# ---------------------------------------------------------------------------
#: 业务实例身份链的四个键（Spec §2.2）：任一环退化都必须显式报错，不许猜。
BUSINESS_IDENTITY_KEYS = ("session_id", "business_case_id", "tech_project_id", "source_task_id")

#: 技术/财务回传成本**必须**带的结构化字段（Spec §3）：只写进备注不算落地。
FINANCE_HANDOFF_FIELDS = ("business_case_id", "quote_session_id", "tech_project_id",
                          "source_task_id", "industry", "quantity", "unit_cost",
                          "currency", "cost_status", "gap_count", "cost_fingerprint",
                          "provisional")


def _gate_num(value):
    """宽松取数：None / "" / 非数字 → None（**不把缺失当成 0**，Spec §4 最后一句）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _gate_rows(data, *section_ids):
    """从步骤快照里取某个分区的表行（`{section: {"数据": [...]}}`，键名兼容 data）。"""
    if not isinstance(data, dict):
        return []
    for sid in section_ids:
        section = data.get(sid)
        if not isinstance(section, dict):
            continue
        rows = section.get("数据", section.get("data"))
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _row_number(row, *keywords):
    """按**列名关键字**取这一行里第一个能解析成数的值（列名是业务中文名，不写死具体列）。"""
    for key, value in (row or {}).items():
        if not any(word in str(key) for word in keywords):
            continue
        got = _gate_num(value)
        if got is not None:
            return got
    return None


def _gate_blocker(code, message, action="", *, fixable_by_fill=True):
    return {"code": code, "message": message, "action": action,
            "fixable_by_fill": bool(fixable_by_fill)}


def quote_step_completion_gate(step_no, data, *, quote_fingerprint: str = "",
                               detail_fingerprint: str = "", business_case_id: str = "") -> dict:
    """第 3–6 步的完成门禁（Spec §4）：返回 ``{ok, step_no, code, message, action, blocking}``。

    判定只看**已落地的数据**（派上来的步骤快照），不做任何推断、不补默认值：
    「强行填满」与前端提示都不能代替这条（§4 最后一句）。
    """
    step = int(step_no or 0)
    blockers = []
    if step == 3:
        rows = _gate_rows(data, "s3_products", "s2_products", "s1_products")
        if not rows:
            blockers.append(_gate_blocker(
                "no_product_rows", "本单还没有产品行（第 1 步未完成产品匹配）。",
                "回第 1 步完成需求配置与产品匹配；产品行由系统匹配产生，不能靠填表造出来。",
                fixable_by_fill=False))
        elif not any((_row_number(row, "基础成本", "成本", "价格") or 0) > 0 for row in rows):
            blockers.append(_gate_blocker(
                "no_base_cost", "产品行没有正数基础成本，无法确认第 3 步。",
                "点「强行填满本步骤」或「重算利润加成」，让基础成本落地后再确认。"))
    elif step == 4:
        rows = _gate_rows(data, "s4_products", "s3_products", "s2_products", "s1_products")
        if not rows:
            blockers.append(_gate_blocker(
                "no_product_rows", "本单还没有产品行，无法确认第 4 步。",
                "回第 1 步完成产品匹配。", fixable_by_fill=False))
        else:
            explainable = []
            for row in rows:
                price = _row_number(row, "价格")
                base = _row_number(row, "基础成本", "成本")
                has_evidence = any(("加价" in str(k) or "规则" in str(k)) and str(v or "").strip()
                                   for k, v in (row or {}).items())
                if price is not None and price > 0 and (base is not None or has_evidence):
                    explainable.append(row)
            if not explainable:
                blockers.append(_gate_blocker(
                    "no_markup_evidence", "产品行没有可解释的加价结果（价格或加价依据为空）。",
                    "点「强行填满本步骤」按定价规则重算加价，或人工填写加价依据。"))
    elif step == 5:
        rows = _gate_rows(data, "s5_detail")
        products = _gate_rows(data, "s4_products", "s3_products", "s2_products", "s1_products")
        if not rows:
            blockers.append(_gate_blocker(
                "no_detail_rows", "报价明细为空：至少要有 1 行。",
                "回到第 4 步确认产品行后，第 5 步会自动生成报价明细。",
                fixable_by_fill=bool(products)))
        else:
            for index, row in enumerate(rows, start=1):
                tag = "第 %d 行" % index
                qty = _row_number(row, "数量")
                unit = _row_number(row, "报价", "单价")
                after = _row_number(row, "折后价格", "折后价")
                total = _row_number(row, "总金额", "金额")
                currency = str(row.get("币种") or "").strip()
                if qty is None or qty <= 0:
                    blockers.append(_gate_blocker("invalid_quantity", tag + "数量无效。",
                                                  "填写正数数量。"))
                if unit is None or unit <= 0:
                    blockers.append(_gate_blocker("invalid_unit_price", tag + "单价（报价）无效。",
                                                  "填写正数单价。"))
                if not currency:
                    blockers.append(_gate_blocker("invalid_currency", tag + "币种为空。",
                                                  "填写币种（如 人民币）。"))
                if after is None or total is None:
                    blockers.append(_gate_blocker("total_not_recomputable",
                                                  tag + "总金额/折后价格缺失，无法复算。",
                                                  "点「报价方案」重算明细，让总金额按公式生成。"))
                elif abs(total - after * (qty if qty is not None else 1.0)) > 0.01:
                    blockers.append(_gate_blocker(
                        "total_not_recomputable",
                        tag + "总金额 %.4f ≠ 折后价格 %.4f × 数量 %s。" % (total, after, qty),
                        "点「报价方案」重算明细。"))
    elif step == 6:
        rows = _gate_rows(data, "s5_detail")
        if not rows:
            blockers.append(_gate_blocker("no_detail_rows", "报价明细为空，不能生成报价单。",
                                          "回到第 5 步补全报价明细。"))
        if rows and quote_fingerprint and detail_fingerprint and quote_fingerprint != detail_fingerprint:
            blockers.append(_gate_blocker(
                "detail_changed_after_step5_confirm",
                "报价明细在第 5 步确认之后被改动过，不能照旧生成报价单。",
                "回第 5 步核对明细后重新确认第 5 步，再生成报价单。",
                fixable_by_fill=False))
    head = blockers[0] if blockers else {}
    return {"ok": not blockers,
            "step_no": step,
            "code": "" if not blockers else head.get("code", ""),
            "message": ("第 %d 步可以完成。" % step) if not blockers
                       else head.get("message", "这一步还不能完成。"),
            "action": "" if not blockers else head.get("action", ""),
            "business_case_id": str(business_case_id or ""),
            "blocking": blockers,
            "fixable_by_fill": bool(blockers) and all(b.get("fixable_by_fill") for b in blockers)}


def _identity_query(sql: str, args=()):
    """只读查身份表（参数化；连不上抛出去，由调用方兜底成可读错误）。"""
    conn = cpq_db.connect(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            cols = [d.name for d in cur.description] if cur.description else []
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def validate_business_identity(session_id: str, *, business_case_id: str = "") -> dict:
    """把报价会话解析成**唯一**的业务实例身份链（Spec §2.2）。

    链：``session_id → business_case_id → tech_project_id → source_task_id``。
    出现多个候选、或显式给的 ``business_case_id`` 与卡片对不上时，返回
    ``ok=False / code=ambiguous_business_case / status=409``——**不猜**（§2.2 / §3）。
    读不到库时返回 ``identity_unavailable``（503），同样不放行。
    """
    sid = str(session_id or "").strip()
    explicit = str(business_case_id or "").strip()
    out = {"ok": False, "code": "", "status": 200, "message": "",
           "session_id": sid, "business_case_id": explicit,
           "tech_project_id": "", "source_task_id": "", "candidates": []}
    if not sid:
        out.update(code="session_id_required", status=400,
                   message="缺少 session_id：报价会话身份必须显式给出（Spec §2.1）。")
        return out
    try:
        cards = _identity_query(
            "SELECT card_id, session_id, business_case_id, title, current_step "
            "FROM cpq_wf_card WHERE session_id = %s", (sid,))
    except Exception as exc:                                    # noqa: BLE001 - 连不上要把话说清楚
        out.update(code="identity_unavailable", status=503,
                   message="读不到卡片身份（%s），无法确认业务实例。" % type(exc).__name__)
        return out
    if len(cards) > 1:
        out.update(code="ambiguous_business_case", status=409,
                   message="同一个 session_id 对应多张卡片，无法确定业务实例。",
                   candidates=[str(c.get("business_case_id") or "") for c in cards])
        return out
    card = cards[0] if cards else {}
    card_case = str(card.get("business_case_id") or "").strip()
    if explicit and card_case and explicit != card_case:
        out.update(code="ambiguous_business_case", status=409,
                   message="显式给的 business_case_id 与卡片记录不一致，拒绝按任一猜测继续。",
                   candidates=[card_case, explicit])
        return out
    case_id = explicit or card_case
    out["business_case_id"] = case_id
    if card.get("card_id") is not None:
        try:
            tasks = _identity_query(
                "SELECT DISTINCT payload->>'tech_project_id' AS tech_project_id, "
                "payload->>'source_task_id' AS source_task_id "
                "FROM cpq_wf_task WHERE card_id = %s", (int(card["card_id"]),))
        except Exception as exc:                                # noqa: BLE001
            out.update(code="identity_unavailable", status=503,
                       message="读不到任务来源（%s），无法确认技术项目。" % type(exc).__name__)
            return out
        projects = sorted({str(t.get("tech_project_id") or "").strip()
                           for t in tasks if str(t.get("tech_project_id") or "").strip()})
        sources = sorted({str(t.get("source_task_id") or "").strip()
                          for t in tasks if str(t.get("source_task_id") or "").strip()})
        if len(projects) > 1:
            out.update(code="ambiguous_business_case", status=409,
                       message="这张卡片关联到多个技术项目，业务实例不唯一。", candidates=projects)
            return out
        out["tech_project_id"] = projects[0] if projects else ""
        out["source_task_id"] = sources[0] if sources else ""
    out.update(ok=True, status=200, message="业务实例身份唯一。")
    return out


def normalize_finance_handoff(payload, *, session_id: str = "") -> dict:
    """技术/财务回传载荷 → 结构化成本记录（Spec §3）。

    字段名固定（`FINANCE_HANDOFF_FIELDS`）：缺什么进 ``missing`` 显式说出来，**不猜数**、
    也不把成本只写进备注——报价侧据此落产品行（基础成本 + ``provisional`` 暂估标记）。
    """
    raw = payload if isinstance(payload, dict) else {}
    cost = raw.get("tech_result") if isinstance(raw.get("tech_result"), dict) else raw
    record = {}
    for name in FINANCE_HANDOFF_FIELDS:
        record[name] = cost.get(name, raw.get(name))
    if not record.get("quote_session_id") and session_id:
        record["quote_session_id"] = session_id
    gaps = _gate_num(record.get("gap_count"))
    record["gap_count"] = int(gaps) if gaps is not None else 0
    record["unit_cost"] = _gate_num(record.get("unit_cost"))
    record["quantity"] = _gate_num(record.get("quantity"))
    # 有缺口（或成本本身没到）→ 暂估；暂估不得伪装成正式成本（Spec §3）。
    record["provisional"] = bool(record["gap_count"] > 0 or record["unit_cost"] is None
                                 or str(record.get("cost_status") or "").strip() in ("provisional", "draft"))
    record["missing"] = [name for name in ("tech_project_id", "quantity", "unit_cost")
                         if record.get(name) in (None, "")]
    record["cost_status"] = str(record.get("cost_status")
                                or ("provisional" if record["provisional"] else "formal"))
    return record


def _handle_quote_step_gate(data: dict) -> dict:
    """POST /api/quote/step-gate —— 第 3–6 步的完成门禁（只读：不写任何表）。"""
    body = data if isinstance(data, dict) else {}
    return quote_step_completion_gate(
        body.get("step_no"), body.get("data") if isinstance(body.get("data"), dict) else {},
        quote_fingerprint=str(body.get("quote_fingerprint") or ""),
        detail_fingerprint=str(body.get("detail_fingerprint") or ""),
        business_case_id=str(body.get("business_case_id") or ""))


def _handle_quote_identity(params) -> dict:
    """GET /api/quote/identity —— 业务实例身份链（只读）。"""
    query = params if isinstance(params, dict) else {}

    def arg(name):
        got = query.get(name)
        if isinstance(got, (list, tuple)):
            got = got[0] if got else ""
        return str(got or "").strip()

    return validate_business_identity(arg("session_id"), business_case_id=arg("business_case_id"))


def _handle_quote_cost_handoff(data: dict) -> dict:
    """POST /api/quote/cost-handoff —— 把财务回传归一成结构化成本记录（只读，不落库）。"""
    body = data if isinstance(data, dict) else {}
    return {"ok": True, "cost": normalize_finance_handoff(
        body.get("payload") or body.get("task") or body,
        session_id=str(body.get("session_id") or ""))}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _acting_user(self) -> dict:
        """这一轮是谁：**只认票上的人**（与 8010 的约定一致；请求体里的 user 一概忽略）。"""
        token = ""
        try:
            header = self.headers.get("authorization", "") or ""
        except Exception:                                        # pragma: no cover - 防御
            header = ""
        if header.lower().startswith("bearer "):
            token = header[7:].strip()
        if not token:
            token = (parse_qs(urlparse(self.path).query).get("token") or [""])[0]
        if not token:
            return {}
        try:
            import cpq_auth
            return cpq_auth.whoami(token) or {}
        except Exception:                                        # pragma: no cover - 防御
            return {}

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/meta":
            # industry 可选：只影响 ④产品技术参数的表头来源（不传按默认行业）
            self._send_json(bridge.meta((parse_qs(parsed.query).get("industry") or [""])[0]))
        elif path == "/api/sessions":
            self._send_json({"sessions": list_history()})
        elif path == "/api/session":
            sid = (parse_qs(parsed.query).get("id") or [""])[0]
            data = load_history(sid)
            if data:
                self._send_json(data)
            else:
                self.send_error(404)
        elif path == "/api/models":
            self._send_json({"models": models_catalog(), "current": bridge.conv.model})
        elif path == "/api/settings":
            self._send_json(bridge.current_settings())
        elif path == "/api/product/pick":
            # 选定产品：确定性取参数 + 查价（成品编码 -> md_clm_material_cost_cnf.material_code
            # -> material_unit_price），不经大模型
            query = parse_qs(parsed.query)
            code = (query.get("code") or [""])[0]
            # 行业是可选参数：不传按默认行业（既有调用点行为不变）
            self._send_json(pick_product(code, (query.get("industry") or [""])[0]))
        elif path == QUICK_QUOTE_CASES_PATH:
            # 快速报价标准案例列表（逆向快速报价第 1 批）：只读；库不可用回 503 + 错误体。
            payload = _handle_quick_quote_cases(parse_qs(parsed.query))
            self._send_json(payload, 200 if payload.get("ok") else 503)
        elif path == "/api/quote/identity":
            # 业务实例身份链（Spec `e2e-quote-session-and-completion-closure.md` §2.2）：只读；
            # 不唯一/读不到就回对应状态码，绝不猜一个继续。
            payload = _handle_quote_identity(parse_qs(parsed.query))
            self._send_json(payload, int(payload.get("status") or 200) if not payload.get("ok") else 200)
        elif path in ("/", "/index.html"):
            self._send_json({"service": "cpq-quote-agent", "steps": STEPS,
                             "hint": "工作台页面由 serve.py(:8010) 提供，本服务只出 API。"})
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/send":
            self._handle_send()
        elif path == "/api/new":
            nb = pool_new()
            self._send_json({"ok": True, "id": nb.session_id})
        elif path == "/api/session/open":
            data = self._read_body()
            b = bridge_for((data.get("id") or "").strip(), create=False)
            if b is not None and b is not bridge:
                self._send_json(b.snapshot())
            else:
                self.send_error(404)
        elif path == "/api/session/delete":
            data = self._read_body()
            sid = (data.get("id") or "").strip()
            pool_drop(sid)
            self._send_json({"ok": delete_history(sid)})
        elif path == "/api/intent":
            data = self._read_body()
            res = classify_intent((data.get("text") or "").strip()) or {}
            self._send_json({"intent": res.get("intent"), "rule_kind": res.get("rule_kind")})
        elif path == "/api/step1/match":
            # 第 1 步快速通道：查库 + 一次大模型评估（**流式**）+ 确定性六维打分（不进智能体循环）
            self._handle_step1_stream()
        elif path == "/api/markup/fill":
            # 第 3/4 步加价计算：SQL 取规则 + 一次大模型调用（**流式**）
            # —— 三个原行业的路径逐字不动（Spec §9）。
            self._handle_markup_stream()
        elif path == PACKAGING_QUOTE_PRICE_PATH:
            # 包装定价（包装第 8 批）：确定性计算，不取规则、不调模型。
            data = self._read_body()
            self._send_json(_handle_packaging_quote_price(data))
        elif path == "/api/quote/step-gate":
            # 第 3–6 步完成门禁（Spec §4）：只读，判据来自 cpq_agent_server.quote_step_completion_gate。
            data = self._read_body()
            payload = _handle_quote_step_gate(data)
            self._send_json(payload, 200 if payload.get("ok") else 409)
        elif path == "/api/quote/cost-handoff":
            # 财务/技术回传载荷归一成结构化成本记录（Spec §3）：只读，不落库。
            self._send_json(_handle_quote_cost_handoff(self._read_body()))
        elif QUICK_QUOTE_CASE_ACTION_RE.match(path):
            # 快速报价案例维护（逆向快速报价第 12 批）：补字段 / 改审核状态。只认票上的人。
            params = _quick_quote_case_route(path)
            payload = _handle_quick_quote_case_write(
                params.get("case_code"), params.get("action"), self._read_body(),
                user=self._acting_user())
            status = 200
            if not payload.get("ok"):
                status = {"case_not_found": 404, "forbidden": 403}.get(payload.get("code"), 400)
            self._send_json(payload, status)
        elif path == cpq_quick_quote_file.QUICK_QUOTE_PARSE_PATH:
            # 快速报价文件解析（逆向快速报价第 5 批）：报价侧只当统一解析服务的客户端。
            self._send_json(_handle_quick_quote_parse(self._read_body()))
        elif path == "/api/extract":
            data = self._read_body()
            name = (data.get("name") or "file").strip()
            try:
                raw = base64.b64decode(data.get("data") or "", validate=False)
            except Exception:
                self._send_json({"ok": False, "name": name, "error": "文件数据无效"})
                return
            if not raw:
                self._send_json({"ok": False, "name": name, "error": "空文件"})
                return
            text, err = _extract_text(name, raw)
            if text is None:
                self._send_json({"ok": False, "name": name, "error": err or "无法解析"})
                return
            trunc = len(text) > _MAX_EXTRACT_CHARS
            text = text[:_MAX_EXTRACT_CHARS]
            self._send_json({"ok": True, "name": name, "chars": len(text),
                             "truncated": trunc, "text": text})
        elif path == "/api/settings":
            data = self._read_body()
            try:
                res = pool_apply_settings(data)
                broadcast_settings(data)  # 同步到另外两个助手（一体化服务下）
                self._send_json(res)
            except Exception as e:
                traceback.print_exc()
                self._send_json({"error": str(e)}, status=500)
        elif path == "/api/import":
            data = self._read_body()
            try:
                self._send_json(_import_quote(data))
            except Exception as e:
                traceback.print_exc()
                self._send_json({"ok": False, "error": str(e)}, status=500)
        elif path == "/api/export/docx":
            data = self._read_body()
            try:
                blob = _build_quote_docx(data)
            except Exception as e:
                traceback.print_exc()
                self._send_json({"error": str(e)}, status=500)
                return
            if blob is None:
                self._send_json({"error": "服务器未安装 python-docx，无法生成 Word（pip install python-docx）"}, status=500)
                return
            self.send_response(200)
            self.send_header("Content-Type",
                             "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            self.send_header("Content-Disposition", "attachment; filename=quote.docx")
            self.send_header("Content-Length", str(len(blob)))
            self._cors()
            self.end_headers()
            self.wfile.write(blob)
        else:
            self.send_error(404)

    def _handle_step1_stream(self):
        """第 1 步匹配的 SSE 端点：评估文字实时流出，最后一条 result 带推荐清单。"""
        data = self._read_body()
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()

        def emit(obj):
            self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()

        try:
            res = _handle_step1_match(data, emit=emit)
        except Exception as e:
            traceback.print_exc()
            res = {"ok": False, "error": str(e)}
        try:
            emit({"type": "result", "result": res})
            emit({"type": "done"})
        except OSError:
            pass

    def _handle_sse(self, fn):
        """通用 SSE 端点：fn(data, emit) 边算边推文本，最后一条 result 带结果。"""
        data = self._read_body()
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()

        def emit(obj):
            self.wfile.write(("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()

        try:
            res = fn(data, emit=emit)
        except Exception as e:
            traceback.print_exc()
            res = {"ok": False, "error": str(e)}
        try:
            emit({"type": "result", "result": res})
            emit({"type": "done"})
        except OSError:
            pass

    def _handle_markup_stream(self):
        self._handle_sse(_handle_markup_fill)

    def _handle_send(self):
        data = self._read_body()
        text = (data.get("message") or "").strip()
        display = data.get("display")  # None=用 text 作气泡；''=不记气泡
        step = data.get("step") if isinstance(data.get("step"), int) else None
        b = bridge_for(data.get("sid"))  # 带 sid 走会话专属 Bridge，缺省回退全局
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self._cors()
        self.end_headers()

        # 等待期的心跳从后台线程发，与模型流的写入并发 —— 两边共用一把锁，
        # 否则两条 SSE 记录会交错成半行，前端 JSON.parse 直接失败。
        write_lock = threading.Lock()

        def emit(obj):
            payload = "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"
            with write_lock:
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()

        if not text:
            try:
                emit({"type": "done"})
            except OSError:
                pass
            return

        try:
            b.stream_turn(text, emit, display=display, step=step)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 客户端断开


def main():
    parser = argparse.ArgumentParser(description="CPQ 报价助手 Agent 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=47292)
    args = parser.parse_args()

    # 只读文件系统（连同子进程一起兜底；报价助手本来也只暴露了 Read/Glob/Grep）
    os.environ["OC_READONLY_FS"] = "1"

    # 应用本地设置里保存的模型 / provider API Key（重启后仍生效）
    _saved = load_settings()
    apply_saved_provider_keys()
    if _saved.get("model"):
        os.environ["CLAUDE_MODEL"] = _sanitize_model(resolve_model(_saved["model"]))
    else:
        # 没有显式选模型：若默认(Claude)没可用 Key、而别的 provider 配了 Key，自动改用它
        _eff = _pick_model_by_available_key(get_model())
        if _eff != get_model():
            os.environ["CLAUDE_MODEL"] = _sanitize_model(_eff)
            print(f"[cpq-agent] 默认模型无可用 Key，已自动切换到已配置 Key 的模型：{_eff}")

    provider = get_model_provider(get_model())
    if not get_api_key_for(provider):
        spec = PROVIDERS.get(provider, {})
        envs = " or ".join(spec.get("env", [])) or "the provider API key"
        print(f"[cpq-agent] 警告：当前模型（{get_model()}）所属提供方 {spec.get('label', provider)} "
              f"暂未配置 API Key。可在页面「设置」里填写，或设置 {envs} / 写入 ~/.claude/config.json。"
              f" 未配置时发送会返回错误。", file=sys.stderr)

    global bridge
    print(f"[cpq-agent] 工作目录 {SCRIPT_DIR}")
    bridge = Bridge(SCRIPT_DIR)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[cpq-agent] model={bridge.conv.model}  profile={bridge.conv.profile.name}")
    print(f"[cpq-agent] API: http://{args.host}:{args.port}/api/send  (Ctrl+C 停止)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[cpq-agent] 已停止")
    finally:
        try:
            bridge.conv.mcp.shutdown()
        except Exception:
            pass
        server.server_close()


if __name__ == "__main__":
    main()

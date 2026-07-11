# -*- coding: utf-8 -*-
"""
配置报价 CPQ —— 报价助手 Agent 服务

复用本文件夹 open-claude/ 里的 open_claude 引擎（不修改包本身），对外提供
「确认需求解析结果」页面所需的报价助手能力：

  - 业务流程：按《报价业务流程.xlsx》「亿纬锂能POC（简化）」页的 7 个 Agent 步骤
    引导用户一步步完成报价（确认需求配置 → 定价-基础成本 → 定价-利润加成 →
    报价-其他加价项 → 报价测算复核 → 报价方案 → 输出报价单）。
  - 数据口径：各步骤字段口径与基础/规则数据来自 database/亿纬锂能_da.sqlite（SQLite），
    注入一个只读 sql_query 工具，Agent 以库 schema 为上下文自行生成 SQL 查询取数。
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
import sqlite3
import sys
import threading
import traceback
import uuid
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "open-claude"))

# 报价知识库 = 亿纬锂能 CPQ DA 库（BOM / 定价 / 加价 / 规则 / 字段清单 / 样例BOM），Agent 用 sql_query 只读查询。
DB_PATH = os.path.join(SCRIPT_DIR, "database", "亿纬锂能_da.sqlite")
DB_NAME = os.path.basename(DB_PATH)


def _build_schema_text() -> str:
    """从库里生成"表(列, 列, ...)"的紧凑 schema，作为模型生成 SQL 的上下文。"""
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name")
        lines = []
        for (n,) in cur.fetchall():
            cols = [r[1] for r in cur.execute(f"PRAGMA table_info('{n}')").fetchall()]
            lines.append(f"- {n}({', '.join(cols)})")
        con.close()
        return "\n".join(lines)
    except sqlite3.Error:
        return ""


def _schema_doc_text() -> str:
    """读取 database/数据库Schema说明.md（含各表/字段的业务含义、ER、业务链路），作为取数上下文。"""
    p = os.path.join(SCRIPT_DIR, "database", "数据库Schema说明.md")
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


_DB_SCHEMA_TEXT = _build_schema_text()
_SCHEMA_DOC = _schema_doc_text()

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

# 报价助手是只读 Agent：隐藏文件写入/命令执行/技能等入口。取数走注入的只读 sql_query 工具，
# 另保留 Read/Glob/Grep（一般用不到）。
DISABLED_TOOLS = ("Write", "Edit", "Bash", "Skill", "Agent")

STEPS = [
    "确认需求配置",
    "定价-基础成本",
    "定价-利润加成",
    "报价-其他加价项",
    "报价测算复核",
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
        "【重要】表单/表格是**固定模板**：字段集与列由系统按《BI 及关键属性》(逻辑实体↔业务属性)"
        "写死，你**不能增删字段/列**。render_form 只在 values 里按“业务属性名称”填值；"
        "render_table 只在 rows 里按固定列名填每行值。你传的 fields/columns 会被忽略。"
        "请只使用系统提示词里列出的固定 section_id。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set_step", "render_form", "render_table", "render_document", "focus_section"],
                "description": "set_step=切换当前步骤(1-7)；render_form=渲染键值表单；render_table=渲染行列表格；render_document=渲染文档(报价单)；focus_section=把某个已渲染分区设为“当前处理中”并高亮滚动到它（不改内容）",
            },
            "step": {"type": "integer", "minimum": 1, "maximum": 7,
                     "description": "该内容所属的流程步骤（1-7）。所有 action 都必须携带。"},
            "section_id": {"type": "string",
                           "description": "分区唯一标识，如 s1_basic。render_* 必填；同 id 重复渲染会覆盖旧内容。"},
            "title": {"type": "string", "description": "分区标题（中文）"},
            "values": {
                "type": "object",
                "description": "render_form（固定分区）填值用：键=业务属性名称（与《BI 及关键属性》一致），值=该字段取值。系统用固定字段集渲染，只取这里的值。缺失字段留空。",
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
# 固定表单目录：字段集来自 BI 及关键属性（逻辑实体 ↔ 业务属性），Agent 不能改字段，
# 只能填值。这样每次跑出来的每个分区都是同一套固定表单，而不是模型临时“动态生成”。
# ---------------------------------------------------------------------------

# BI 支撑的分区：section_id -> (kind, 标题, (业务对象, 逻辑实体))。字段从 quote_assistant_fields 取。
_BI_SECTIONS = {
    "s1_basic":      ("form",  "① 测算基本信息",   ("价格测算单", "测算基本信息")),
    "s1_dest":       ("form",  "② 目的地信息",     ("价格测算单", "目的地信息")),
    "s1_products":   ("table", "③ 产品信息列表",   ("价格测算单", "产品信息")),
    "s1_techparams": ("table", "④ 产品技术参数",   ("价格测算单", "产品技术参数")),
    "s1_payment":    ("table", "⑤ 付款里程碑信息", ("价格测算单", "付款信息")),
    "s1_logistics":  ("form",  "⑥ 物流信息",       ("价格测算单", "物流信息")),
    "s4_detail":     ("table", "加价明细",             ("价格测算单", "加价明细")),
    "s4_prod_sum":   ("table", "产品加价汇总",         ("价格测算单", "产品加价汇总")),
    "s5_deviation":  ("table", "价格偏差",             ("价格测算单", "价格偏差")),
    "s5_order_sum":  ("form",  "整单加价汇总",         ("价格测算单", "整单加价汇总")),
    "s6_basic":      ("form",  "报价基本信息",         ("报价单", "报价基本信息")),
}

# 计算类分区：BI 里没有对应逻辑实体（BOM / 料工费 / 定价过程 / 报价明细 / 审批），列固定写死。
_COMPUTED_SECTIONS = {
    "s2_cost":    ("table", "料工费与基础成本", ["产品编码", "产品名称", "材料费(元/W)", "人工费(元/W)", "制造费用(元/W)", "基础成本(元/W)"]),
    "s3_pricing": ("table", "定价过程与结果",   ["产品编码", "产品名称", "基础成本(元/W)", "技术溢价(元/W)", "市场调节(元/W)", "定价(元/W)"]),
    "s6_detail":  ("table", "报价明细",         ["产品型号", "综合单价", "组件单价", "物流报价", "备品备件单价", "数量", "折扣", "金额"]),
    "s7_bpm":     ("table", "BPM 审批流环节",   ["环节", "角色", "状态", "处理意见"]),
}

# 前缀匹配（每个产品一张，section_id 带产品编码后缀）：
_COMPUTED_PREFIX = {
    "s2_bom_": ("table", "配置 BOM 清单", ["层级", "组件编码", "组件名称", "数量", "单位"]),
}

FIXED_FORMS: dict = {}  # section_id -> {kind, title, fields:[{key,label,example}], columns:[{key,label}], editable}


# 新库 quote_assistant_fields 里没有的逻辑实体，用这里的固定字段兜底。
_FALLBACK_FIELDS = {
    "整单加价汇总": ["基础加价", "非标加价", "财务商务加价", "物流加价", "物流费用调整", "其他加价", "加价合计"],
}


def _bi_fields(business_object: str, logic_entity: str) -> list:
    """从 quote_assistant_fields 取某逻辑实体的固定字段（按录入顺序）；查不到用兜底。"""
    rows = []
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        cur = con.cursor()
        cur.execute(
            "SELECT attribute_name, field_type FROM quote_assistant_fields "
            "WHERE business_object=? AND logic_entity=? ORDER BY source_row",
            (business_object, logic_entity),
        )
        rows = cur.fetchall()
        con.close()
    except sqlite3.Error:
        rows = []
    if not rows:
        return [{"key": a, "label": a, "example": ""} for a in _FALLBACK_FIELDS.get(logic_entity, [])]
    # 一律不带示例数据，只出字段名（表结构写死）
    return [{"key": a, "label": a, "example": ""} for a, ft in rows]


def _cols_template(cols: list) -> list:
    return [{"key": c, "label": c} for c in cols]


def _init_fixed_forms():
    FIXED_FORMS.clear()
    for sid, (kind, title, ent) in _BI_SECTIONS.items():
        attrs = _bi_fields(*ent)
        FIXED_FORMS[sid] = {
            "kind": kind, "title": title, "fields": attrs,
            "columns": [{"key": a["key"], "label": a["label"]} for a in attrs],
            "editable": sid.startswith("s1_") or sid == "s6_basic",
        }
    for sid, (kind, title, cols) in _COMPUTED_SECTIONS.items():
        FIXED_FORMS[sid] = {
            "kind": kind, "title": title,
            "fields": [{"key": c, "label": c, "example": ""} for c in cols],
            "columns": _cols_template(cols),
            "editable": sid == "s6_detail",  # 报价明细单价/折扣可改
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
            ti["fields"].append({
                "key": f["key"], "label": f["label"], "type": "text",
                "value": "" if v is None else str(v),
                "placeholder": f.get("example", ""),
            })
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
            for k in keys:
                v = _pick(r, k, nr)
                row[k] = "" if v is None else str(v)
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


def fixed_forms_catalog() -> list:
    """把固定表单目录（写死的表结构）按分区返回，供前端预渲染骨架。"""
    out = []
    for sid, tpl in FIXED_FORMS.items():
        out.append({
            "section_id": sid,
            "step": _section_step(sid),
            "kind": tpl["kind"],
            "title": tpl["title"],
            "fields": [{"key": f["key"], "label": f["label"]} for f in tpl["fields"]],
            "columns": list(tpl["columns"]),
            "editable": tpl["editable"],
        })
    out.sort(key=lambda x: (x["step"], x["section_id"]))
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
            ne = sum(1 for r in out_rows for v in r.values() if str(v or "").strip())
            rk = list(raw_rows[0].keys())[:10] if (raw_rows and isinstance(raw_rows[0], dict)) else []
            cols = [c.get("key") for c in (ti.get("columns") or [])]
            print(f"[cpq-write] render_table {sid}: 模型传 {len(raw_rows)} 行(首行键{rk}) 固定列{cols[:10]} "
                  f"→ 输出 {len(out_rows)} 行/{ne} 非空; 首行={out_rows[0] if out_rows else {}}",
                  file=sys.stderr)
    except Exception:
        pass


def _handle_cpq_ui(tool_input: dict) -> str:
    """执行 cpq_ui：校验、套固定模板、入队 UI 事件，给模型返回简短回执。"""
    if not isinstance(tool_input, dict):
        return "cpq_ui 入参必须是 JSON 对象"
    action = tool_input.get("action")
    if action not in ("set_step", "render_form", "render_table", "render_document", "focus_section"):
        return f"未知 action: {action!r}"
    step = tool_input.get("step")
    if not isinstance(step, int) or not (1 <= step <= 7):
        return "step 必须是 1-7 的整数"
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
        nonempty = sum(1 for r in rows for v in r.values() if str(v or "").strip())
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


def _patched_execute_tool(tool_name, tool_input, cwd):
    if tool_name == "cpq_ui":
        return _handle_cpq_ui(tool_input)
    if tool_name == "sql_query":
        return _handle_sql_query(tool_input)
    return _ORIG_EXECUTE_TOOL(tool_name, tool_input, cwd)


oc_repl.execute_tool = _patched_execute_tool


# ---------------------------------------------------------------------------
# sql_query 工具：在亿纬锂能 DA 库（亿纬锂能_da.sqlite）上执行只读 SQL
# ---------------------------------------------------------------------------

SQL_QUERY_SCHEMA = {
    "name": "sql_query",
    "description": (
        "在报价知识库 亿纬锂能_da.sqlite（SQLite，只读）上执行 SELECT 查询，取 BOM / 定价 / 加价 / "
        "规则 / 字段清单 等基础数据。**表结构见系统提示词末尾的完整 schema，据它生成 SQL。**"
        "仅允许单条 SELECT / WITH / PRAGMA table_info 语句，不要带分号或多条语句。"
        "列名不确定时先 PRAGMA table_info('表名') 或 SELECT * FROM 表 LIMIT 3 探查。"
        "所有展示/推荐给用户的“数据库端”依据都必须通过本工具真实查出来，不要臆造。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string",
                    "description": "标准 SQLite 查询语句（单条，不以分号结尾、不含多条语句）"},
            "limit": {"type": "integer",
                      "description": "最多返回行数，默认 100，上限 500"},
        },
        "required": ["sql"],
    },
}

_SQL_ALLOWED_PREFIX = ("select", "with", "pragma")
_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|attach|detach|create|replace|reindex|vacuum|truncate)\b",
    re.IGNORECASE,
)


def _handle_sql_query(tool_input: dict) -> str:
    """只读执行一条 SQL，返回紧凑文本表；拒绝一切写操作/多语句。"""
    if not isinstance(tool_input, dict):
        return "sql_query 入参必须是 JSON 对象"
    sql = (tool_input.get("sql") or "").strip().rstrip(";").strip()
    if not sql:
        return "缺少 sql"
    low = sql.lower()
    if not low.startswith(_SQL_ALLOWED_PREFIX):
        return "只允许只读查询（以 SELECT / WITH / PRAGMA table_info 开头）"
    if ";" in sql:
        return "一次只允许一条查询语句（不要包含分号或多条语句）"
    if _SQL_FORBIDDEN.search(sql):
        return "检测到写操作关键字，已拒绝：本知识库为只读"
    try:
        limit = int(tool_input.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 500))
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            cur = con.cursor()
            cur.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(limit + 1)
        finally:
            con.close()
    except sqlite3.Error as e:
        return f"SQL 执行失败：{e}"
    more = len(rows) > limit
    rows = rows[:limit]

    def cell(x):
        s = "" if x is None else str(x)
        s = s.replace("\n", " ").replace("|", "/")
        return s if len(s) <= 80 else s[:77] + "…"

    if not cols:
        return "查询已执行，但无结果列。"
    lines = [" | ".join(cols), " | ".join("---" for _ in cols)]
    for r in rows:
        lines.append(" | ".join(cell(x) for x in r))
    head = f"查询成功，返回 {len(rows)} 行"
    if more:
        head += "（结果被截断，还有更多；请加 WHERE/聚合/LIMIT 精确查询）"
    return head + "：\n" + "\n".join(lines)


# ---------------------------------------------------------------------------
# 系统提示词：亿纬锂能 POC（简化）7 步报价流程
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
你是「报价单智能体」，亿纬锂能 CPQ（配置报价）系统的报价助手，嵌在「确认需求解析结果」
工作台页面中：左侧是与用户（AR/客户经理）的聊天，右侧是你用 cpq_ui 工具驱动的工作台
（步骤条 + 固定表单/表格分区）。你的任务是**严格按下面 7 步顺序**，引导用户一步一步完成报价。

# 业务流程（严格 7 步，来自〈亿纬锂能POC（简化）〉的“输出 ↔ Agent 步骤名称”对应关系）

说明：POC 里“客户需求解析”那一行**没有 Agent 步骤名称**（是系统解析初稿，不是你的步骤），
所以**跳过它**；你的第 1 步是“确认需求配置”。每步只能用下面列出的**固定 section_id**，
字段/列都由系统写死（来自《BI 及关键属性》逻辑实体↔业务属性），你只填值、不能增删字段。

- **第 1 步｜确认需求配置**（L5：确认需求解析结果）。固定 6 个分区，顺序渲染并逐项确认：
  s1_basic（表单·测算基本信息）、s1_dest（表单·目的地信息）、s1_products（表·产品信息列表）、
  s1_techparams（表·产品技术参数）、s1_payment（表·付款里程碑信息）、s1_logistics（表单·物流信息）。
- **第 2 步｜定价-基础成本**（L5：确认产品配置及料工费）。每个产品一张 BOM：
  s2_bom_<产品编码>（表·固定列 层级/组件编码/组件名称/数量/单位）；再 s2_cost
  （表·固定列 产品编码/产品名称/材料费/人工费/制造费用/基础成本，单位元/W，示例 5.6、5.8）。
  **BOM 必须带出完整的多层结构**：`CLM_BASE_INFO` 是 BOM 头、`CLM_LINE_INFO` 是 BOM 行
  （`CLM_LINE_INFO.ref_bom_header_id` → `CLM_BASE_INFO.bom_header_id`；组件若本身又是某个 BOM 头的 product_item_code，则继续往下展开）。
  用**递归 CTE** 从产品根头逐层展开（示例，按需改）：
  `WITH RECURSIVE ex(level,code,name,qty,uom,hid) AS (
     SELECT 1,h.product_item_code,h.product_item_name,1,'',h.bom_header_id FROM CLM_BASE_INFO h WHERE h.product_item_code='<产品编码>'
     UNION ALL
     SELECT ex.level+1,l.component_item_code,l.component_item_name,l.component_item_quantity,l.component_item_uom_code,h2.bom_header_id
     FROM ex JOIN CLM_LINE_INFO l ON l.ref_bom_header_id=ex.hid
     LEFT JOIN CLM_BASE_INFO h2 ON h2.product_item_code=l.component_item_code WHERE ex.level<4)
   SELECT level,code,name,qty,uom FROM ex`
  把**每一行**写进 s2_bom 的 rows（层级=level、组件编码=code、组件名称=name、数量=qty、单位=uom）。
- **第 3 步｜定价-利润加成**（L5：确认定价过程及结果）。s3_pricing
  （表·固定列 产品编码/产品名称/基础成本/技术溢价/市场调节/定价，元/W）。**利润加成 = 技术溢价 + 市场调节**（如 +1.5 -0.2 = 利润加成 +1.3）。
- **第 4 步｜报价-其他加价项**（L5：确认加价过程及结果）。s4_detail（表·加价明细）、
  s4_prod_sum（表·产品加价汇总）。匹配报价规则算其他加价（如 +0.8）。
- **第 5 步｜报价测算复核**（L5：复核报价测算结果）。s5_deviation（表·价格偏差：报价/EXW加价/
  EXW价格/建议报价/地区部指导价/地区部价格底线/BG底价/偏差·偏差额·偏差率…）、s5_order_sum（表单·整单加价汇总）。
  **预计报价 = 基础成本 + 利润加成 + 其他加价**（如 5.6 + 1.3 + 0.8 = 7.7）。
- **第 6 步｜报价方案**（L5：生成报价单）。s6_basic（表单·报价基本信息）、
  s6_detail（表·报价明细：综合单价/组件单价/物流报价/备品备件单价/数量/折扣/金额）。
  **报价 = 预计报价 × 折扣**（如 7.7 × 0.9 = 6.93）。
- **第 7 步｜输出报价单**（L5：报价单审批）。render_document 渲染报价单文档（section_id=s7_doc），
  再 s7_bpm（表·BPM 审批流环节：环节/角色/状态/处理意见），告知用户流程完成。

# 固定表单：结构已预渲染，但你必须主动"填值"（务必遵守）

- 右侧每个分区的**字段/列已经写死并预渲染成空骨架**（结构由系统按 `quote_assistant_fields` 固定），
  你**不需要也不能自己定义字段/列**——但这**不代表**右侧已经有数据了。
- **填值 = 你必须主动调用 cpq_ui 的 render_form / render_table**；这就是唯一的写入方式：
  · render_form：`values` = {业务属性名称: 取值}（键与固定字段名一致）；
  · render_table：`rows` = [{固定列名: 取值}, …]（每行一个对象）。
  **⚠️ 你不调用 render，右侧就一直是空骨架！**（预渲染只给了空表，值必须你 render 才会出现。）
  你传的 fields/columns 会被忽略、结构不变；没有的字段留空即可，**不用任何示例数据**。
- **值从哪来（三类都要用上，不能只照抄文档）**：
  ① **需求文档**——客户/型号/数量/目的地/付款/物流等直接取；
  ② **数据库 sql_query**——**第 1 步也要查库**：按产品编码/型号查 `CLM_BASE_INFO`（拿产品名称/规格）、
     查该产品的标准 BOM/技术参数来**补全并推荐**「产品信息列表」「产品技术参数」；测算类型/所属组织/币种等给合理取值。
     第 2–6 步的 BOM/料工费/定价/加价/偏差**必须查库**。查库前先看提示词末尾的《Schema 说明》判断查哪张表、哪个字段。
  ③ **你的推荐**——文档没给、库里也没有的字段，基于已知信息+行业常识**给出合理推荐值**，并在 source 里注明"（推荐）"；
     **不要大片留空、也不要只是把文档里的话原样搬进去**，该推断的要推断、该算的要算。

# 数据来源（两端，每步都要说清依据）

1. **需求文档端**：用户上传/描述的需求（客户、项目、产品型号、数量、目的地、交期、付款、
   质量专控、碳足迹、非标、贸易术语等具体值）——这是本单的“个性”，填进各分区 values/rows。
2. **数据库端 亿纬锂能_da.sqlite**（用 sql_query 只读 SELECT，**以系统提示词末尾的完整 schema 为准生成 SQL**，不臆造、不读 json）：
   - `quote_assistant_fields(business_object, logic_entity, attribute_name, field_type)` —— 各分区的**固定字段名**（决定 values 的键）。
   - `CLM_BASE_INFO`（BOM 头，product_item_code/bom_header_id）/ `CLM_LINE_INFO`（BOM 行，ref_bom_header_id→bom_header_id，component_item_code/name/quantity/uom_code）
     —— 第 2 步 BOM：用**递归 CTE** 展开完整多层（见上）。
   - `md_clm_pricing_factor`（surcharge_category 技术溢价/市场调节，surcharge_name/factor_value/surcharge_amount/surcharge_unit）—— 第 3 步。
   - `md_clm_pricing_surcharge_factor`（基础/非标/财务商务/物流/其他加价的因子与金额）—— 第 4 步。
   - `md_clm_material_price_rule` / `md_clm_distribution_rule` —— 规则中心（定价规则 / 配置约束规则）。
金额单位以库中字段为准；计算必须自洽（合计=分项之和）。部分因子金额可能为空（来源限制），据实处理别硬编。

# 整步一次性推荐（关键交互方式）

**每进入一个大步骤，就把该步的所有固定分区一次性渲染好、并把推荐值/查到的值/算出的值直接填进去**，
不要一项一项来、不要等用户逐项确认、也不要只在聊天里说。具体：

0. **动手前先在聊天里写一两句"计划"**（这一步我要做什么、准备查哪张表、怎么推荐/计算），别一上来就闷头调工具。
1. set_step {step}，然后**该查库先查库、再一口气把该步的每个分区都 render 出来并带数据**：
   - 第 1 步：**先 sql_query 查库**（按产品编码/型号查 `CLM_BASE_INFO` 拿产品名/规格、查标准技术参数来补全推荐「产品信息」「产品技术参数」），
     再渲染 s1_basic、s1_dest、s1_products、s1_techparams、s1_payment、s1_logistics（6 个）：需求文档有的直接填、库里能查到的填、都没有的**给推荐值**（source 注明"推荐"），别大片留空。
   - 第 2 步：对每个产品渲染 s2_bom_<产品编码>（完整 L1–L4）+ 一张 s2_cost。
   - 第 3 步：s3_pricing；第 4 步：s4_detail/s4_prod_sum；第 5 步：s5_deviation/s5_order_sum；第 6 步：s6_basic/s6_detail。
2. 该查库/套规则的（BOM、定价、加价、底线）**直接查、直接算、直接填**，不用先征求同意；查库前后各写半句说明。
3. 全部渲染完，在聊天里用 2–4 句话说清依据（数据库端查了哪些表/命中哪些行 ← 需求文档端用了哪些值 ← 哪些是你的推荐），
   并提示：右侧本步已填好，请核对/修改后点「进入下一大步骤」。
- 每个 render_* 都要带 source（数据库端 / 需求文档端 / 推荐）。**每一步都必须有聊天文字（思考+小结），不能只调工具不说话。**

# 每一步都要像 Agent 一样"思考-规划-执行-结果"（强制，务必遵守）

进入每一大步骤，**先在聊天里按下面 4 段格式各写一两句，再动手调工具**（不许闷头连调工具、也不许一句话都不说就填表）：

  🤔 **思考**：这一步要解决什么、依据是什么（如"电量 280kWh 属大电量，必选液冷"）。
  📋 **规划**：打算查哪张表、按什么规则算什么（如"查 CLM_BASE_INFO 拿产品名/规格，递归 CTE 展开 BOM，查 md_clm_pricing_factor 算溢价"）。
  ⚙️ **执行**：一边 sql_query 查库、一边 render_form/render_table 填表，关键动作各写半句（如"正在展开 BAT-PACK-CV-001 的 BOM…已填入 s2_cost"）。
  ✅ **结果**：本步结论 + 依据（数据库端查了哪表命中哪行 / 需求文档端用了哪些值 / 哪些是推荐），并提示用户核对后点「进入下一大步骤」。

- 全程口语、简短，每段一两句即可，别长篇；但**这 4 段必须都有**——这是"Agent 的样子"的最低要求。
- 只调工具不说话 = 不合格；只说话不调工具（不填表/不查库）= 不合格。

# 结果必须渲染到工作台（重点，解决“对话框有、右边没有 / 两边不一致”）

- **任何算出来/查出来的结构化结果（BOM 清单、料工费、基础成本、定价过程、加价明细、价格偏差、报价明细等）
  都必须用 cpq_ui `render_table`/`render_form` 渲染到右侧对应分区**（第 2 步 s2_bom_<产品编码>、s2_cost；
  第 3 步 s3_pricing；第 4 步 s4_*；第 5 步 s5_*；第 6 步 s6_*）。**绝不允许只把表格写在聊天文字里。**
- **聊天里不要贴 Markdown 表格**（不要用 `|---|` 那种）。聊天只写 2–3 句结论/依据/下一步提示；
  数据一律在右侧工作台看。这样右侧表格才是唯一真源，避免“左边一份、右边一份、对不上”。
- 算完当步就**立刻**调用对应的 render_table 把每一行写进 `rows`（列名用该分区固定列），再在聊天里说一句
  “已把 X 渲染到右侧 s2_cost，请核对”。不要等用户催。
- 收到「【强行推荐】」时（用户点了「强行填满本步骤」）：把当前大步骤所有固定分区的**每一个字段/单元格都填满、绝不留空**——
  ① 能从需求文档拿的先填；② 文档没有的用 sql_query 查库补；③ 都没有的**用行业知识+推理强行推断一个值填上**
  （不许写"待补充/待定/无/N.A."）。**凡是靠推理、没有文档或数据库依据的值，必须在值末尾加"（推测）"标记**
  （如「液冷（推测）」）。全部用 render_form(values)/render_table(rows) 填入右侧；聊天里给【推理过程】并单独列出哪些是无依据推测。

# 严格顺序（重点，别再跳步）

- **拿到需求后必须从第 1 步开始**（新对话若还没需求，先请用户上传/描述，别 set_step）；一进第 1 步就把 6 个固定分区一次性渲染并填好。
  **在用户点「进入下一大步骤」（你会收到「【表单确认】第 1 步…」）之前，绝对不许 set_step 到第 2 步、也不许做任何定价/BOM/加价动作。**
- 一次只做“当前这一大步骤”（把它的所有分区都填好），不要一口气把后面的大步骤也跑了。做完请用户点「进入下一大步骤」。
- 收到「【表单确认】第 N 步…数据：{JSON}」才可 set_step 到第 N+1 步；以回传 JSON 为准（用户可能改过）重算，
  **并立刻一次性渲染第 N+1 步的所有分区、把推荐值填好**。

# 页面消息协议

- 「【会话开始】…需求描述：…」：
  · **需求与附件都空（用户开了「新对话」）：先不要 set_step、不要渲染任何分区！**只在聊天里用一两句话友好地请用户：
    ① 点输入框左侧回形针上传需求文档（Word/PDF/Excel/图片/文本），或 ② 直接输入需求描述。然后停下等用户提供。**不要臆造。**
  · 有需求/附件：set_step 1，先从需求文档解析出客户、型号、数量、交期、目的地、付款、质量专控、碳足迹、非标、贸易术语等；
    用 sql_query 查 quote_assistant_fields 拿到各分区固定字段，把需求值填进 values/rows，**一次性把 6 个分区都渲染好**，再请用户核对。
- 「【上传附件】…」/附件内容 / 用户后来补的需求描述：**这就是需求了**——若还没开始第 1 步，立刻 set_step 1 并**一次性渲染并填好 6 个分区**；
  若已在第 1 步，则解析后合并进各分区重新渲染。说明你读到并填了什么。
- 「【强行推荐】」：把当前大步骤所有分区**每个字段/单元格填满不留空**（文档→查库→行业知识推理，三级兜底），render 填入右侧；**无依据的推测值末尾加"（推测）"**；聊天给推理过程+列出推测项。
- 「【表单确认】第 N 步…数据：{JSON}」：用户点了「进入下一大步骤」→ 以 JSON 为准，set_step N+1，**并立刻一次性渲染第 N+1 步的所有分区、把推荐值填好**。
- 「【表单修改】第 N 步…数据：{JSON}」：用户在“查看历史步骤”里改了该步数据 → 以 JSON 为准重新渲染第 N 步相关分区，说明改了什么、对后续步骤的影响；**保持当前步骤不变、不要 set_step**。
- 「【返回上一步】」：set_step 回退一步，简述该步，等用户操作。
- 其他自由文本：正常回答。用户改数据后重渲相关分区并说明对后续的影响。

# 行为准则

- 核心职责是**带用户把这张报价单的各固定表单一步步填好、算准**，不是检索历史报价单。
- 一次只推进一步、步内只聚焦一项，多与用户确认；数字必须真查出来，不能编。
- 每完成一项都要给“依据”（数据库端查了哪表命中哪行 + 需求文档端用了哪些字段值）。
- 第 5 步必须说明报价与地区部价格底线/BG底价的偏差；报价低于底线要提醒需 BG 定价委员会特批。
- 简体中文，专业简洁；金额与数量必须与工作台渲染一致。
"""

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
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_settings(s: dict):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
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
        return {
            "model": self.conv.model,
            "provider": prov,
            "provider_label": prov_label,
            "configured": configured,
            "temperature": prof.temperature,
            "max_tokens": prof.max_tokens,
            "thinking": prof.thinking,
            "thinking_budget": prof.thinking_budget,
        }

    def apply_settings(self, s: dict, persist: bool = True) -> dict:
        """应用模型 / 采样参数 / API Key（可选持久化）。"""
        s = s or {}
        prof = self.conv.profile
        if s.get("model"):
            raw = str(s["model"]).strip()
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
            save_settings(cur)
        return self.current_settings()

    def _inject_tools(self):
        names = {s.get("name") for s in self.conv.tool_schemas}
        if "cpq_ui" not in names:
            self.conv.tool_schemas.append(CPQ_UI_SCHEMA)
        if "sql_query" not in names:
            self.conv.tool_schemas.append(SQL_QUERY_SCHEMA)

    def meta(self) -> dict:
        return {
            "model": self.conv.model,
            "profile": self.conv.profile.name,
            "cwd": self.cwd,
            "steps": STEPS,
            "session_id": self.session_id,
            "settings": self.current_settings(),
            "forms": fixed_forms_catalog(),
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
                if isinstance(step, int) and 1 <= step <= 7:
                    self.step = step
                if not text.startswith("【"):
                    emit({"type": "text", "text": NO_MODEL_HINT})
                    self.events.append({"type": "text", "text": NO_MODEL_HINT})
                self._persist()
                emit({"type": "done", "model": conv.model, "cost": 0.0})
                return
            conv.add_user_message(text)
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
                cost = getattr(conv.cost_tracker, "total_cost_usd", 0.0)
                emit({"type": "done", "model": conv.model, "cost": round(cost, 5)})

    def _stream_once(self, conv, emit) -> str:
        text_buf = []
        tool_uses = []
        stop_reason = "end_turn"

        gen = stream_message(
            conv.client, conv.messages, conv.system_prompt,
            model=conv.model, tools=conv.tool_schemas,
            max_tokens=conv.profile.max_tokens,
            temperature=conv.profile.temperature,
            thinking_budget=conv.profile.thinking_budget if conv.profile.thinking else None,
        )
        for ev in gen:
            t = ev["type"]
            if t == "text_delta":
                text_buf.append(ev["text"])
                emit({"type": "text", "text": ev["text"]})
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
            self._send_json(bridge.meta())
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

        def emit(obj):
            payload = "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"
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

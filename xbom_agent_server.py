# -*- coding: utf-8 -*-
"""
配置报价 CPQ —— XBOM 配置助手 Agent 服务

复用本文件夹 open-claude/ 里的 open_claude 引擎（不修改包本身），对外提供
「XBOM智能体-配置BOM生成」页面所需的配置助手能力：

  - 业务流程：按《配置助手.xlsx·智能体配置流程》一次跑完 6 步配置 BOM 生成
    （识别参数 → 识别可配置模块 → 物料归集 → 提取配置规则 → 生成配置BOM → 确认配置BOM）。
    第 1–5 步自动跑；第 6 步起与用户交互（右侧出可编辑的配置BOM表）。
  - 数据口径：样例变体BOM / 配置BOM / 规则来自 database/亿纬锂能_da.sqlite，Agent 以库 schema 为上下文生成 SQL。
  - 页面驱动：注入自定义 xbom_ui 工具（set_step / step_result / render_bom / open_panel），
    Agent 用它推进步骤卡、填每步结果、在右侧渲染可编辑配置BOM；本服务把入参作为 `ui` 事件透传给前端（SSE）。

接口（供 XBOM智能体-配置BOM生成.html 调用，含 CORS）：
    GET  /api/meta / /api/models / /api/settings / /api/sessions / /api/session
    POST /api/send / /api/new / /api/session/open|delete / /api/settings / /api/extract

运行：
    python xbom_agent_server.py [--port 47294]
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

# 报价知识库（BOM / 定价 / 加价 / 规则 / BI 字段口径 / 流程），Agent 用 sql_query 工具只读查询。
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
    "识别参数",
    "识别可配置模块",
    "物料归集",
    "提取配置规则",
    "生成配置BOM",
    "确认配置BOM",
]

# ---------------------------------------------------------------------------
# 通用键归一化：让模型给的列名/字段名容错匹配（去括号单位/空格/大小写/全半角）
# ---------------------------------------------------------------------------

def _norm_key(s) -> str:
    s = str(s if s is not None else "")
    s = re.sub(r"[\(（【\[][^\)）】\]]*[\)）】\]]", "", s)
    return s.replace(" ", "").replace("　", "").replace("／", "/").strip().lower()


def _pick(d: dict, key: str, nd: dict):
    if key in d and d[key] is not None:
        return d[key]
    return nd.get(_norm_key(key))


# ---------------------------------------------------------------------------
# xbom_ui 工具：Agent 用它驱动 ①中间“配置BOM生成过程”步骤卡 ②右侧可编辑的配置BOM表
# ---------------------------------------------------------------------------

XBOM_UI_SCHEMA = {
    "name": "xbom_ui",
    "description": (
        "驱动配置助手页面。用于：推进 6 步流程卡状态、往某一步里填“结果”内容、"
        "以及在右侧面板渲染可编辑的『配置BOM』表格（第 6 步）。"
        "每步的思考/规划/执行说明写在聊天里；结构化结果（参数、模块、物料树、规则、配置BOM）用本工具渲染。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["set_step", "step_result", "render_bom", "render_params", "render_rules", "open_panel"],
                "description": (
                    "set_step=把某一步(1-6)标记为 running/done；"
                    "step_result=往某一步里填结果块(标签/物料树/规则/文本)；"
                    "render_bom=在右侧渲染可编辑的配置BOM表；"
                    "render_params=静默保存第1步识别出的参数(右侧不显示，仅供入库)；"
                    "render_rules=给每行规则下拉提供候选清单(只查 md_clm_distribution_rule)；"
                    "open_panel=展开右侧配置面板。"
                ),
            },
            "step": {"type": "integer", "minimum": 1, "maximum": 6,
                     "description": "步骤号(1-6)。set_step/step_result 必填。"},
            "status": {"type": "string", "enum": ["running", "done", "pending"],
                       "description": "set_step 用：该步状态。"},
            "blocks": {
                "type": "array",
                "description": (
                    "step_result 用：该步的结果块列表，按顺序渲染。每个块是下面之一：\n"
                    '· {"type":"tags","title":"可配置模块","tags":[{"text":"电量","kind":"blue"}]}  kind: default/blue/green/orange\n'
                    '· {"type":"tree","rows":[{"level":2,"text":"TMS-SYS-001 热管理系统","qty":"","tag":""}]}  level 1-4;tag 如 互斥件/标准件\n'
                    '· {"type":"rules","rules":[{"label":"全局规则1","text":"[约束规则] 280kWh必选配液冷"}]}\n'
                    '· {"type":"text","text":"一句话说明"}'
                ),
                "items": {"type": "object"},
            },
            "product": {"type": "string", "description": "render_bom 用：产品名(显示在配置面板标题，如 工商业储能柜)。"},
            "columns": {
                "type": "array",
                "description": "render_bom 用：表格列 [{key,label}]。默认列=物料编码/物料名称/层级/数量/单位/标签。",
                "items": {"type": "object",
                          "properties": {"key": {"type": "string"}, "label": {"type": "string"}},
                          "required": ["key", "label"]},
            },
            "rows": {
                "type": "array",
                "description": "render_bom 用：配置BOM每行，键=列 key，值=该单元格。行按 BOM 层级顺序。"
                               "**可再加一个 `规则` 键=该行推荐挂的 md_clm_distribution_rule 规则名**(前端设为该行下拉默认选中，用户可改)。",
                "items": {"type": "object"},
            },
            "params": {
                "type": "array",
                "description": "render_params 用：第 1 步识别出的参数表，每项 {name, value}（如 {name:'电量', value:'280kWh'}）。右侧不显示，仅静默保存用于入库。",
                "items": {"type": "object",
                          "properties": {"name": {"type": "string"}, "value": {"type": "string"}}},
            },
            "rules": {
                "type": "array",
                "description": "render_rules 用：**只来自 md_clm_distribution_rule（产品配单规则）**的候选规则清单，每项 {code, name, desc}。"
                               "这是每行规则下拉的选项池；具体每行推荐哪条，靠 render_bom 行里的 `规则` 字段指定。",
                "items": {"type": "object",
                          "properties": {"code": {"type": "string"}, "name": {"type": "string"},
                                         "desc": {"type": "string"}, "recommended": {"type": "boolean"}}},
            },
            "source": {"type": "string", "description": "本次内容来源说明(数据库端/需求文档端)，显示在操作轨迹。"},
        },
        "required": ["action"],
    },
}

_DEFAULT_BOM_COLUMNS = [
    {"key": "物料编码", "label": "物料编码"},
    {"key": "物料名称", "label": "物料名称"},
    {"key": "层级", "label": "层级"},
    {"key": "数量", "label": "数量"},
    {"key": "单位", "label": "单位"},
    {"key": "标签", "label": "标签"},
]

# 每个会话轮次中，xbom_ui 调用产生的 UI 事件先入队，工具批次执行完后随 SSE 下发。
# 线程本地：工具在各自会话回合的请求线程里同步执行，线程隔离即会话隔离，
# 多个会话并发跑回合时 UI 事件不会互相串。
_UI_TLS = threading.local()


def _ui_events() -> list:
    lst = getattr(_UI_TLS, "events", None)
    if lst is None:
        lst = _UI_TLS.events = []
    return lst


def _handle_xbom_ui(tool_input: dict) -> str:
    """执行 xbom_ui：校验、入队 UI 事件，给模型返回简短回执。"""
    if not isinstance(tool_input, dict):
        return "xbom_ui 入参必须是 JSON 对象"
    action = tool_input.get("action")
    if action not in ("set_step", "step_result", "render_bom", "render_params", "render_rules", "open_panel"):
        return f"未知 action: {action!r}"
    ti = dict(tool_input)
    if action in ("set_step", "step_result"):
        step = ti.get("step")
        if not isinstance(step, int) or not (1 <= step <= 6):
            return "step 必须是 1-6 的整数"
    if action == "render_bom":
        if not isinstance(ti.get("columns"), list) or not ti["columns"]:
            ti["columns"] = list(_DEFAULT_BOM_COLUMNS)
        keys = [c["key"] for c in ti["columns"]]
        rows = ti.get("rows") if isinstance(ti.get("rows"), list) else []
        norm = []
        for r in rows:
            if isinstance(r, dict):
                nr = {_norm_key(k): v for k, v in r.items()}
                norm.append({k: ("" if _pick(r, k, nr) is None else str(_pick(r, k, nr))) for k in keys})
        ti["rows"] = norm
    if action == "render_params" and not isinstance(ti.get("params"), list):
        ti["params"] = []
    if action == "render_rules" and not isinstance(ti.get("rules"), list):
        ti["rules"] = []
    _ui_events().append(ti)
    if action == "set_step":
        return f"步骤 {ti.get('step')}（{STEPS[ti.get('step', 1) - 1]}）状态 -> {ti.get('status')}"
    if action == "step_result":
        return f"已把结果填入步骤 {ti.get('step')}（{STEPS[ti.get('step', 1) - 1]}）"
    if action == "render_bom":
        return f"已在右侧渲染配置BOM（{len(ti.get('rows') or [])} 行，可编辑）"
    if action == "render_params":
        return f"已保存 {len(ti.get('params') or [])} 个参数（右侧不显示，供入库用）"
    if action == "render_rules":
        return f"已载入 {len(ti.get('rules') or [])} 条产品配单规则，作为每行规则下拉的候选"
    return "已展开配置面板"


# 拦截点：包装 open_claude.repl 的 execute_tool，让 xbom_ui / sql_query 走本地处理。
_ORIG_EXECUTE_TOOL = oc_repl.execute_tool


def _patched_execute_tool(tool_name, tool_input, cwd):
    if tool_name == "xbom_ui":
        return _handle_xbom_ui(tool_input)
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
        "在配置知识库 亿纬锂能_da.sqlite（SQLite，只读）上执行 SELECT 查询，取 样例BOM / BOM / 规则 / "
        "字段清单 等数据。**表结构见系统提示词末尾的完整 schema，据它生成 SQL。**"
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
# 配置BOM 入库：写入专用表 xbom_config_bom / xbom_config_bom_line（不污染样例/生产表）
# ---------------------------------------------------------------------------

def _import_config_bom(payload: dict) -> dict:
    """把用户确认后的配置BOM + 参数 + 每行规则写入数据库（可写连接，仅限本地专用表）。"""
    if not isinstance(payload, dict):
        return {"ok": False, "error": "入参必须是 JSON 对象"}
    product = (payload.get("product") or payload.get("产品") or "").strip()
    project = (payload.get("project") or payload.get("项目") or "").strip()
    params = payload.get("params") or payload.get("参数") or []
    rows = payload.get("rows") or payload.get("配置BOM") or []
    if not isinstance(rows, list) or not rows:
        return {"ok": False, "error": "没有可导入的 BOM 行"}
    params_json = json.dumps(params, ensure_ascii=False)
    try:
        con = sqlite3.connect(DB_PATH)
        try:
            cur = con.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS xbom_config_bom (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product TEXT, project TEXT, params_json TEXT,
                    line_count INTEGER, created_at TEXT
                )""")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS xbom_config_bom_line (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bom_id INTEGER, seq INTEGER,
                    material_code TEXT, material_name TEXT, level TEXT,
                    qty TEXT, unit TEXT, tag TEXT, rule TEXT
                )""")
            created = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                "INSERT INTO xbom_config_bom (product, project, params_json, line_count, created_at)"
                " VALUES (?,?,?,?,?)",
                (product, project, params_json, len(rows), created))
            bom_id = cur.lastrowid

            def g(r, *keys):
                for k in keys:
                    v = r.get(k)
                    if v not in (None, ""):
                        return str(v)
                return ""

            for i, r in enumerate(rows, 1):
                if not isinstance(r, dict):
                    continue
                cur.execute(
                    "INSERT INTO xbom_config_bom_line"
                    " (bom_id, seq, material_code, material_name, level, qty, unit, tag, rule)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (bom_id, i, g(r, "物料编码", "material_code", "code"),
                     g(r, "物料名称", "material_name", "name"),
                     g(r, "层级", "level"), g(r, "数量", "qty"),
                     g(r, "单位", "unit"), g(r, "标签", "tag"),
                     g(r, "规则", "rule")))
            con.commit()
        finally:
            con.close()
    except sqlite3.Error as e:
        return {"ok": False, "error": f"数据库写入失败：{e}"}
    return {"ok": True, "bom_id": bom_id, "lines": len(rows),
            "table": "xbom_config_bom / xbom_config_bom_line", "created_at": created}


# ---------------------------------------------------------------------------
# 系统提示词：亿纬锂能 POC（简化）7 步报价流程
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
你是「XBOM 配置助手」，嵌在「配置BOM生成」页面：中间是与用户的聊天 + 一张「配置BOM生成过程」步骤卡，
右侧是可编辑的「配置BOM」面板。你的任务：根据用户上传的变体 BOM / 需求，**一次跑完整套配置 BOM 生成流程**，
最后在右侧产出可手动修改的配置 BOM。

# 流程（共 6 步，来自〈配置助手·智能体配置流程〉）

1. 识别参数 —— 反向推断出导致物料/数量变化的核心参数，并识别其可选值/取值范围
   （如：电量、冷却方式、附加功能、电芯模组、额定电流、箱体规格）。
2. 识别可配置模块 —— 区分「可配置模块 / 通用(不可配置)模块」，标记「可配组件」（L2/L3）。
3. 物料归集 —— 把物料挂到对应模块下，给每个物料打标签（通用件 / 互斥件 / 可选件 / 参数数量件 / 标准件）。
4. 提取配置规则 —— 顶层全局约束 → 模块级规则 → BOM 行级规则。
   （如：280kWh 必选液冷；风冷不能选低温加热；电量→电芯模组/额定电流/箱体规格 的计算规则等。）
5. 生成配置BOM —— 模块层级 + 全量物料归集 + 三层规则，组装出完整配置 BOM（含 L1/L2/L3/L4 单层展开、参数自动录入、规则挂到 BOM 头/行）。
6. 确认配置BOM —— 右侧只放**一张可编辑配置BOM表**，规则做成**每行一个下拉**：
   ① 先 `render_rules`：**只查 `md_clm_distribution_rule`（产品配单规则）**，把该表的规则做成候选清单 [{code,name,desc}]（这是每行下拉的选项池，不再单独占一块面板）；
   ② 再 `render_bom`：每一行**尽量带上一个推荐规则**——在行数据里给 `规则` 字段填该行应挂的 `md_clm_distribution_rule` 规则名（前端会把它设为该行下拉的默认选中，用户可自行改选或选「（无）」）；
   ③ **不要再渲染参数表**（`render_params` 仍可调用以保存第 1 步参数供入库，但右侧不显示）。
   交给用户核对/修改/确认（点「确认配置BOM」回传 BOM+每行规则+参数；确认后用户可点「导入数据库」入库）。

# 交互方式（关键）

- **第 1–5 步自动跑完，不用等用户逐步确认**；**从第 6 步起与用户交互**（右侧出可编辑的配置BOM表，用户可改、可用自然语言让你继续调整）。
- **每一步都要"出声"**：在聊天里用简短的一两句依次说清【思考 → 规划 → 执行 → 结果】：
  · 思考：这一步要解决什么、依据是什么；
  · 规划：打算查哪张表 / 用什么方法；
  · 执行：正在做什么（查库 / 归集 / 提规则…）；
  · 结果：这一步得到了什么（一句话小结）。
- 每步的**结构化结果**用 `xbom_ui` 渲染到步骤卡里（step_result），别只在聊天里堆长列表；
  **第 6 步右侧只要两步：先 `render_rules`（只查 `md_clm_distribution_rule`，做每行下拉的选项池）→ 再 `render_bom`（每行 `规则` 字段填推荐规则名）**；参数不显示（可 `render_params` 静默保存）。
- 收到「【确认配置】…{参数,配置BOM,已选规则}」：以用户回传的为准（已选规则=每行选中的规则）；若用户改了参数，据参数重算 BOM 并 `render_bom` 重渲；否则简短总结（配置BOM + 每行规则）。

# 工具

- `xbom_ui`：
  · set_step(step,status)：进入某步先 set_step(step,"running")，做完 set_step(step,"done")。
  · step_result(step,blocks)：把该步结果填进步骤卡。blocks 用 tags / tree / rules / text 四种块
    （识别参数用 tags；识别可配置模块用 tags(分组：可配置模块/通用模块/可配组件)；物料归集用 tree；提取规则用 rules；生成/说明用 text）。
  · render_bom(product,columns?,rows)：第 6 步在右侧渲染可编辑配置BOM表（默认列 物料编码/物料名称/层级/数量/单位/标签）。
  · render_params(params)：第 6 步渲染可编辑【参数表】——params=[{name,value}]，就是第 1 步识别出的参数。
  · render_rules(rules)：第 6 步渲染【规则选择】——rules=[{code,name,desc,recommended}]，从 `md_clm_distribution_rule`（配置/配单规则）
    和 `md_clm_material_price_rule`（定价/报价规则）查出候选规则；**当前配置该挂的规则设 recommended=true（前端会预勾选=AI 推荐）**，其余 false 供用户搜索勾选。
  · open_panel：展开右侧配置面板。
- `sql_query`：只读查 `亿纬锂能_da.sqlite`（**以系统提示词末尾的完整 schema 为准生成 SQL**）。不臆造、不读 json。

# 数据来源（两端，每步说清依据）

1. **需求文档端**：用户上传的变体 BOM Excel / 需求描述（产品型号、电量、冷却方式、附加功能、数量等）。
2. **数据库端 亿纬锂能_da.sqlite**（用 sql_query 只读 SELECT）：
   - `sample_power_bom_orders(order_code, capacity_kwh, cooling_method, rated_current, box_spec, low_temp_heating, cloud_comm, module_count, order_description, ...)`
     —— **20 份变体样例 BOM 的订单头 + 驱动参数**。第 1 步「识别参数」就从这里反推核心参数及取值域（电量/冷却方式/额定电流/箱体规格/低温加热/云端通信/电芯模组数）。
   - `sample_power_bom_lines(order_code, local_line_no, parent_local_line_no, level, level_tag, item_type, item_code, item_name, quantity, raw_text)`
     —— 20 份样例 BOM 的**层级明细**（按 order_code + (parent_local_line_no→local_line_no) 组成树）。第 2/3 步识别可配置模块、物料归集就用它。
   - `CLM_BASE_INFO`（配置 BOM 头，product_item_code/bom_header_id）/ `CLM_LINE_INFO`（BOM 行，ref_bom_header_id→bom_header_id）
     —— 标准配置 BOM 结构；第 5/6 步组装配置 BOM 时用（需要多层可用 CLM_LINE_INFO 递归 CTE 展开）。
   - `md_clm_distribution_rule(rule_name, rule_desc, rule_expression_view, rule_expression)` —— **配置/配单规则**（如 280kWh 强制液冷、风冷禁止低温加热）。第 4 步提取配置规则用。
   - `md_clm_material_price_rule` / `md_clm_pricing_factor` / `md_clm_pricing_surcharge_factor` —— 定价/加价规则（如涉及）。
   - `config_assistant_fields(business_object, logic_entity, physical_table, attribute_name, field_code)` —— 配置助手字段清单口径。

# 页面消息协议

- 「【会话开始】…需求描述：…」：
  · 需求与附件都空（新对话）：**先不要 set_step、不要渲染**，只在聊天里友好请用户上传变体 BOM Excel / 需求文档（回形针）或直接描述产品与配置需求，然后停下等待。**不要臆造。**
  · 有需求/附件：从需求解析出产品型号/电量/冷却方式/附加功能等，然后**依次跑第 1–5 步**（每步 set_step running→做→step_result→done，并在聊天里给思考/规划/执行/结果），到第 6 步用 render_bom 把配置BOM渲染到右侧，请用户核对/修改/确认。
- 「【上传附件】…」/附件内容 / 用户后补的需求：这就是配置依据——若还没开始就从第 1 步跑起。
- 「【确认配置】」/用户在右侧改了配置BOM并保存：以用户改后的为准，必要时用 render_bom 重渲，并说明影响。
- 其他自由文本：正常回答；用户要求调整配置（换冷却方式、改电量等）时，重算受影响的模块/物料/规则并重渲右侧 BOM。

# 行为准则

- 一次把第 1–5 步跑完再进第 6 步；数字/物料/规则必须真查库得到，不能编。
- 每步都在聊天里给出【思考/规划/执行/结果】的简短说明（一两句），让用户看得懂在干嘛。
- 结构化结果渲染到步骤卡 / 右侧面板，聊天里不贴大表。
- 简体中文，专业、简洁。
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

HISTORY_DIR = os.path.join(SCRIPT_DIR, "xbom_history")
SETTINGS_PATH = os.path.join(SCRIPT_DIR, "xbom_settings.json")


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
    """可选模型清单（含 Qwen / DeepSeek），标注每个 provider 是否已配置 Key。"""
    out = []
    for m in AVAILABLE_MODELS:
        prov = m.get("provider", "anthropic")
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
        out.append({
            "id": data.get("id"),
            "title": data.get("title") or "未命名报价",
            "created": data.get("created"),
            "updated": data.get("updated"),
            "step": data.get("step", 1),
            "model": data.get("model"),
            "turns": sum(1 for e in events if e.get("type") == "user"),
        })
    out.sort(key=lambda x: x.get("updated") or "", reverse=True)
    return out[:limit]


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
    prof.name = "xbom-config"
    prof.description = "XBOM 配置助手（配置BOM生成）"
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
            print(f"[xbom-agent] 模型名清洗: {self.conv.model} -> {clean}")
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
        prov = get_model_provider(self.conv.model)
        return {
            "model": self.conv.model,
            "provider": prov,
            "provider_label": PROVIDERS.get(prov, {}).get("label", prov),
            "configured": bool(get_api_key_for(prov)),
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
            mid = _sanitize_model(resolve_model(s["model"]))
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
        if "xbom_ui" not in names:
            self.conv.tool_schemas.append(XBOM_UI_SCHEMA)
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
        }

    def reset(self):
        with self.lock:
            self.conv.messages.clear()
            self.conv.session = SessionStore(self.cwd)
            self.conv.cost_tracker.__init__()
            _ui_events().clear()
            self._new_session()

    def stream_turn(self, text: str, emit, display=None):
        """跑完整一轮（含工具循环），emit(dict) 逐事件下发。

        display：用于本地历史回放的“用户气泡”文案（与实际发给模型的 text 不同，
        例如 text 是含文件内容的长 payload、或【会话开始】等系统消息）。传空串表示不记气泡。
        """
        with self.lock:
            conv = self.conv
            bubble = text if display is None else display
            if bubble:
                self.events.append({"type": "user", "text": bubble})
                if not self.title and bubble.strip() and not bubble.startswith("【"):
                    self.title = bubble.strip().replace("\n", " ")[:24]
            conv.add_user_message(text)
            try:
                for _ in range(max(1, conv.profile.max_iterations)):
                    conv._maybe_compact()
                    stop_reason = self._stream_once(conv, emit)

                    if stop_reason == "tool_use":
                        _ui_events().clear()
                        conv._execute_pending_tools()
                        # xbom_ui 调用产生的页面事件
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
                if ev["name"] == "xbom_ui":
                    ci = ev["input"] or {}
                    # 只透传轻量元信息给前端做“操作/来源”轨迹，不带整份 rows/blocks
                    tu_input = {
                        "action": ci.get("action"),
                        "step": ci.get("step"),
                        "status": ci.get("status"),
                        "product": ci.get("product"),
                        "source": ci.get("source"),
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

bridge: Bridge = None  # 默认 Bridge（兼容不带 sid 的旧请求），main() 或 suite 里构建


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


def pool_apply_settings(data: dict) -> dict:
    """设置是全局的：默认 bridge 应用并持久化一次，池内所有会话同步应用（不回写）。"""
    res = bridge.apply_settings(data)
    with _POOL_LOCK:
        others = list(_BRIDGES.values())
    for b in others:
        try:
            b.apply_settings(data, persist=False)
        except Exception:
            traceback.print_exc()
    return res


# ---------------------------------------------------------------------------
# 意图识别：把首页对话框里的一句话分到 报价 / 产品配置 / 规则配置
# ---------------------------------------------------------------------------

_INTENT_SYS = (
    "你是「配置报价」系统的意图分类器。用户会说一句话，请判断他的目标属于下面哪一类，"
    "只输出一个英文单词，不要标点、不要解释、不要多余内容：\n"
    "quote  —— 报价 / 价格测算 / 生成报价单 / 询价 / 折扣 / 价格 等\n"
    "config —— 产品配置 / 选配 / 配置 BOM / 物料配置 / 生成配置清单 / 配一台设备 等\n"
    "rule   —— 规则配置 / 定价规则 / 加价规则 / 规则维护 / 规则中心 等\n"
    "只能输出 quote、config、rule 三者之一。"
)


def classify_intent(text: str):
    """用一次轻量大模型调用做意图识别；失败返回 None（前端会退回关键词兜底）。"""
    if not text or bridge is None:
        return None
    try:
        from open_claude.api import complete
        res = complete(
            bridge.conv.client,
            [{"role": "user", "content": text[:2000]}],
            _INTENT_SYS,
            model=bridge.conv.model,
            max_tokens=8,
        )
        out = "".join(b.get("text", "") for b in res.get("content", [])).strip().lower()
    except Exception as e:
        # 意图识别失败不影响主流程（前端会回退到关键词匹配），只记一行简讯，不刷栈
        print(f"[xbom-agent] 意图识别调用失败（已回退关键词）：{e.__class__.__name__}: {e}",
              file=sys.stderr)
        return None
    for k in ("config", "rule", "quote"):
        if k in out:
            return k
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
            self._send_json({"service": "xbom-config-agent", "steps": STEPS,
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
            self._send_json({"intent": classify_intent((data.get("text") or "").strip())})
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
                self._send_json(pool_apply_settings(data))
            except Exception as e:
                traceback.print_exc()
                self._send_json({"error": str(e)}, status=500)
        elif path == "/api/import":
            data = self._read_body()
            try:
                res = _import_config_bom(data)
                self._send_json(res, status=200 if res.get("ok") else 400)
            except Exception as e:
                traceback.print_exc()
                self._send_json({"ok": False, "error": str(e)}, status=500)
        else:
            self.send_error(404)

    def _handle_send(self):
        data = self._read_body()
        text = (data.get("message") or "").strip()
        display = data.get("display")  # None=用 text 作气泡；''=不记气泡
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
            b.stream_turn(text, emit, display=display)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 客户端断开


def main():
    parser = argparse.ArgumentParser(description="CPQ 报价助手 Agent 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=47294)
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
            print(f"[xbom-agent] 默认模型无可用 Key，已自动切换到已配置 Key 的模型：{_eff}")

    provider = get_model_provider(get_model())
    if not get_api_key_for(provider):
        spec = PROVIDERS.get(provider, {})
        envs = " or ".join(spec.get("env", [])) or "the provider API key"
        print(f"[xbom-agent] 警告：当前模型（{get_model()}）所属提供方 {spec.get('label', provider)} "
              f"暂未配置 API Key。可在页面「设置」里填写，或设置 {envs} / 写入 ~/.claude/config.json。"
              f" 未配置时发送会返回错误。", file=sys.stderr)

    global bridge
    print(f"[xbom-agent] 工作目录 {SCRIPT_DIR}")
    bridge = Bridge(SCRIPT_DIR)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[xbom-agent] model={bridge.conv.model}  profile={bridge.conv.profile.name}")
    print(f"[xbom-agent] API: http://{args.host}:{args.port}/api/send  (Ctrl+C 停止)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[xbom-agent] 已停止")
    finally:
        try:
            bridge.conv.mcp.shutdown()
        except Exception:
            pass
        server.server_close()


if __name__ == "__main__":
    main()

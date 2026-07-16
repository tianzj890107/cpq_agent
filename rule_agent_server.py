# -*- coding: utf-8 -*-
"""
CPQ rule assistant Agent service.

Main responsibilities:
  1. Import rule source text from uploaded files.
  2. Convert natural-language rule descriptions into Groovy formulas and
     structured rule rows, using remote Postgres (shared by all three assistants) as read-only context.

Run:
    python rule_agent_server.py --port 47296
"""

import argparse
import base64
import datetime
import html
import io
import json
import os
import re
import sys
import threading
import traceback
import uuid
import zipfile
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "open-claude"))

# 业务数据源 = 远程 Postgres（三助手共用）；Agent 可见 schema 来自 亿纬锂能DA梳理.xlsx「规则助手」sheet。
import cpq_db
import cpq_msgutil
import cpq_llm

DB_NAME = cpq_db.DB_LABEL

# 三个助手共享同一份设置（模型/参数/Key）：cpq_settings.json 为唯一权威文件；
# 旧的 rule_settings.json 仅作迁移兜底读取，不再写入。
SETTINGS_PATH = os.path.join(SCRIPT_DIR, "cpq_settings.json")
FALLBACK_SETTINGS_PATH = os.path.join(SCRIPT_DIR, "rule_settings.json")
HISTORY_DIR = os.path.join(SCRIPT_DIR, "rule_history")
MAX_EXTRACT_CHARS = 120_000

# 「无模型」伪模型 id：选中后不调用大模型，工作台全流程由人工填写+下一步完成
NO_MODEL_ID = "none"
NO_MODEL_HINT = "当前为「无模型」模式：不调用大模型。请直接在右侧规则表人工添加/编辑规则行，完成后点「导入数据库」。"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

import open_claude.repl as oc_repl
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
from open_claude.repl import Conversation
from open_claude.sessions import SessionStore

DISABLED_TOOLS = ("Write", "Edit", "Bash", "Skill", "Agent")

RULE_FIELDS = [
    "客户等级",
    "电流分档",
    "质量专控要求",
    "冷却方式",
    "附加功能",
    "箱体规格",
    "额定电流",
    "电芯模组",
    "电量",
]


def _now_iso() -> str:
    return datetime.datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _to_float_or_none(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int_or_none(v):
    if v in (None, ""):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _build_schema_text() -> str:
    # 本体语义层来自 亿纬锂能DA梳理.xlsx「规则助手」sheet（不再反射数据库）。
    return cpq_db.schema_text("rule")


def _select_lines(sql: str, params=(), limit: int = 20, conn=None) -> list[str]:
    """在远程 Postgres 上执行一条只读查询，格式化成 'k=v | k=v' 行。连不上/出错返回 []。
    conn 传入时复用（避免逐条重连超时）；否则临时开一条。"""
    close = conn is None
    try:
        if conn is None:
            conn = cpq_db.connect(readonly=True)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchmany(limit)
    except Exception:
        return []
    finally:
        if close and conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    out = []
    for row in rows:
        pairs = []
        for k, v in zip(cols, row):
            if v is not None and str(v).strip():
                s = str(v).replace("\n", " ")
                pairs.append(f"{k}={s[:160]}")
        if pairs:
            out.append(" | ".join(pairs))
    return out


def _build_rule_field_context() -> str:
    """规则助手可用字段与数据库依据。
    字段字典来自 亿纬锂能DA梳理.xlsx 三个 sheet（本体语义层）；实时规则/因子样例来自远程
    Postgres（连不上则整体跳过，不阻塞启动）。"""
    lines = ["# 规则生成可用字段与数据库依据"]
    for field in RULE_FIELDS:
        lines.append(f"\n## {field}")
        hits = cpq_db.search_fields(field, limit=8)
        if hits:
            lines.extend(f"- 字段字典: {h}" for h in hits)

    # 以下为远程 Postgres 实时样例（可选增强）：共用一条连接，连不上则整体跳过。
    try:
        conn = cpq_db.connect(readonly=True)
    except Exception:
        conn = None
    if conn is not None:
        try:
            existing_rule_lines = _select_lines(
                """
                SELECT rule_name, rule_desc, rule_expression
                FROM md_clm_distribution_rule
                WHERE is_deleted = 0
                ORDER BY updated_at DESC
                """,
                limit=8, conn=conn,
            )
            if existing_rule_lines:
                lines.append("\n# 已有产品配单/配置规则样例")
                lines.extend(f"- {x}" for x in existing_rule_lines)

            price_rule_lines = _select_lines(
                """
                SELECT rule_name, rule_classification, rule_desc, rule_expression
                FROM md_clm_material_price_rule
                WHERE is_deleted = 0
                  AND (rule_name LIKE '%客户%' OR rule_name LIKE '%电流%' OR rule_name LIKE '%质量%' OR rule_name LIKE '%冷却%')
                ORDER BY updated_at DESC
                """,
                limit=10, conn=conn,
            )
            if price_rule_lines:
                lines.append("\n# 已有报价/加价规则样例")
                lines.extend(f"- {x}" for x in price_rule_lines)

            surcharge_lines = _select_lines(
                """
                SELECT surcharge_name, surcharge_factor, factor_value, surcharge_amount, surcharge_unit
                FROM md_clm_pricing_surcharge_factor
                WHERE is_active = '是'
                ORDER BY md_clm_pricing_surcharge_factor_id
                """,
                limit=30, conn=conn,
            )
            if surcharge_lines:
                lines.append("\n# 加价因子明细")
                lines.extend(f"- {x}" for x in surcharge_lines)

            order_lines = _select_lines(
                """
                SELECT order_code, capacity_kwh, cooling_method, rated_current,
                       box_spec, low_temp_heating, cloud_comm, module_count
                FROM sample_power_bom_orders
                ORDER BY sequence_no
                """,
                limit=20, conn=conn,
            )
            if order_lines:
                lines.append("\n# 动力电池样例 BOM 参数")
                lines.extend(f"- {x}" for x in order_lines)
        finally:
            try:
                conn.close()
            except Exception:
                pass
    return "\n".join(lines)


DB_SCHEMA_TEXT = _build_schema_text()
RULE_FIELD_CONTEXT = _build_rule_field_context()

SQL_QUERY_SCHEMA = {
    "name": "sql_query",
    "description": (
        "在远程 Postgres 上执行只读 SQL 查询。用于检索规则字段、既有规则、BOM样例、报价/加价因子。"
        "只允许单条 SELECT / WITH，不要包含分号或写操作。列名不确定可查 information_schema.columns。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "单条只读 SQL"},
            "limit": {"type": "integer", "description": "最多返回行数，默认100，上限500"},
        },
        "required": ["sql"],
    },
}

RULE_RESULT_SCHEMA = {
    "name": "rule_result",
    "description": (
        "输出规则助手的结构化规则行，前端据此渲染右侧规则表单。分两步使用：\n"
        "- stage=import：第一步，把上传文档里的规则忠实抽取成行（只填名称/类型/目标库/描述，不要 Groovy）。\n"
        "- stage=groovy：第二步，为每条规则生成 Groovy 公式和代码（rule_name 要和第一步对应）。"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "stage": {
                "type": "string",
                "enum": ["import", "groovy", "final"],
                "description": "import=第一步文档导入(无公式)；groovy=第二步生成 Groovy 公式/代码；final=最终定稿",
            },
            "rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "rule_name": {"type": "string", "description": "规则名称，两步之间必须一致以便回填"},
                        "rule_type": {
                            "type": "string",
                            "description": "配置规则/报价规则/加价规则/校验规则/其他",
                        },
                        "target_table": {
                            "type": "string",
                            "description": "目标规则库表名(md_clm_distribution_rule/md_clm_material_price_rule/md_clm_pricing_surcharge_factor)",
                        },
                        "rule_desc": {"type": "string", "description": "规则描述：文档原文里的触发条件与结果，尽量保留原话"},
                        "groovy_formula": {"type": "string", "description": "第二步：核心 Groovy 表达式(可落库的 rule_expression)"},
                        "groovy_code": {"type": "string", "description": "第二步：完整 Groovy 代码块(可含注释/多分支，展示用)"},
                        "source_fields": {"type": "array", "items": {"type": "string"}, "description": "该规则用到的业务字段"},
                        "db_evidence": {"type": "array", "items": {"type": "string"}, "description": "第二步：字段/取值来自哪张表或字段字典的依据"},
                        "confidence": {"type": "number"},
                    },
                    "required": ["rule_name"],
                },
            },
            "notes": {"type": "string"},
        },
        "required": ["stage", "rules"],
    },
}

_SQL_ALLOWED_PREFIX = ("select", "with")
_SQL_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|attach|detach|create|replace|reindex|vacuum|truncate|grant|revoke|copy)\b",
    re.IGNORECASE,
)


def _handle_sql_query(tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return "sql_query 入参必须是 JSON 对象"
    sql = (tool_input.get("sql") or "").strip().rstrip(";").strip()
    if not sql:
        return "缺少 sql"
    low = sql.lower()
    if not low.startswith(_SQL_ALLOWED_PREFIX):
        return "只允许 SELECT / WITH 开头的只读查询"
    if ";" in sql:
        return "一次只允许一条 SQL，不要包含分号"
    if _SQL_FORBIDDEN.search(sql):
        return "检测到写操作关键字，已拒绝"
    try:
        limit = max(1, min(int(tool_input.get("limit", 100)), 500))
    except (TypeError, ValueError):
        limit = 100
    try:
        cols, rows = cpq_db.run_select(sql, limit)
    except Exception as e:
        return f"SQL 执行失败: {e}"
    more = len(rows) > limit
    rows = rows[:limit]
    if not cols:
        return "查询已执行，但没有结果列。"

    def cell(x):
        s = "" if x is None else str(x)
        s = s.replace("\n", " ").replace("|", "/")
        s = "".join(ch if ch.isprintable() else " " for ch in s)  # 过滤二进制/控制字符乱码
        return s if len(s) <= 120 else s[:117] + "..."

    lines = [" | ".join(cols), " | ".join("---" for _ in cols)]
    for row in rows:
        lines.append(" | ".join(cell(x) for x in row))
    head = f"查询成功，返回 {len(rows)} 行"
    if more:
        head += "，结果已截断"
    return head + ":\n" + "\n".join(lines)


# rule_result 事件队列。线程本地：工具在各自会话回合的请求线程里同步执行，
# 线程隔离即会话隔离，多个会话并发跑回合时结果事件不会互相串。
_RULE_TLS = threading.local()


def _rule_events() -> list:
    lst = getattr(_RULE_TLS, "events", None)
    if lst is None:
        lst = _RULE_TLS.events = []
    return lst


def _handle_rule_result(tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return "rule_result 入参必须是 JSON 对象"
    rules = tool_input.get("rules") or []
    if not isinstance(rules, list):
        return "rules 必须是数组"
    _rule_events().append(tool_input)
    return f"已接收 {len(rules)} 条规则行，stage={tool_input.get('stage') or ''}"


def _import_rules(payload: dict) -> dict:
    """把右侧规则表里的规则写入独立的 rule_agent_rules 表（不污染业务规则库表）。"""
    if not isinstance(payload, dict):
        return {"ok": False, "error": "入参必须是 JSON 对象"}
    rules = payload.get("rules") or payload.get("rows") or []
    if not isinstance(rules, list) or not rules:
        return {"ok": False, "error": "没有可导入的规则行"}

    def g(r, *keys):
        for k in keys:
            v = r.get(k)
            if v not in (None, ""):
                return v
        return ""

    created_at = datetime.datetime.now().replace(microsecond=0).isoformat(sep=" ")
    batch_id = uuid.uuid4().hex[:12]
    # 目标表按每行「目标规则库」分流（DA 梳理·规则助手 sheet 的两张规则表）：
    #  - 含 定价/报价/price → md_clm_material_price_rule（带 rule_classification）
    #  - 其余（配单/配置/distribution/缺省）→ md_clm_distribution_rule
    # id 主键：不传，由 PG 默认值自动生成；Groovy 公式 → rule_expression / rule_expression_view。
    saved = 0
    fails = []
    tables_used = set()
    try:
        con = cpq_db.connect(readonly=False)  # autocommit
        try:
            for idx, r in enumerate(rules, 1):
                if not isinstance(r, dict):
                    continue
                name = str(g(r, "rule_name", "规则名称", "name")).strip()
                desc = str(g(r, "rule_desc", "规则描述", "desc")).strip()
                formula = str(g(r, "groovy_formula", "groovy_code", "Groovy规则公式", "formula")).strip()
                if not (name or desc or formula):
                    continue
                target = str(g(r, "target_table", "目标规则库", "目标库")).strip()
                low = target.lower()
                row = {
                    "rule_name": name, "rule_desc": desc,
                    "rule_expression": formula, "rule_expression_view": formula,
                    "is_deleted": "0", "effective_status": "1",
                    "created_by": "rule_agent", "created_at": created_at,
                    "updated_by": "rule_agent", "updated_at": created_at,
                }
                if ("price" in low) or ("定价" in target) or ("报价" in target):
                    table = "md_clm_material_price_rule"
                    cls = str(g(r, "rule_classification", "规则分类")).strip()
                    if not cls:
                        cls = "报价" if "报价" in target else "定价"
                    row["rule_classification"] = cls
                else:
                    table = "md_clm_distribution_rule"
                # 主键雪花生成（规则表主键无外键引用，显式生成以对齐 id 生成算法）
                pk_cols, _ = cpq_db.key_cols("rule", table)
                if pk_cols:
                    row[pk_cols[0]] = cpq_db.snow_next_id(con)
                try:
                    n = cpq_db.insert_rows(con, table, [row])
                    if n:
                        saved += 1
                        tables_used.add(table)
                    else:
                        fails.append(f"第{idx}行「{name}」：无有效列可写入")
                except Exception as e:
                    fails.append(f"第{idx}行「{name}」→{table}：{str(e)[:160]}")
        finally:
            con.close()
    except Exception as e:
        return {"ok": False, "error": f"数据库写入失败: {e}"}
    if not saved:
        return {"ok": False, "error": "未写入任何规则" + ("；" + "；".join(fails[:5]) if fails else "")}
    out = {
        "ok": not fails,
        "batch_id": batch_id,
        "count": saved,
        "table": " / ".join(sorted(tables_used)) or "md_clm_distribution_rule",
        "created_at": created_at,
    }
    if fails:
        out["errors"] = fails[:10]
        out["ok"] = True  # 部分成功也算导入完成，errors 供前端展示
    return out


_ORIG_EXECUTE_TOOL = oc_repl.execute_tool


def _patched_execute_tool(tool_name, tool_input, cwd):
    if tool_name == "sql_query":
        return _handle_sql_query(tool_input)
    if tool_name == "rule_result":
        return _handle_rule_result(tool_input)
    return _ORIG_EXECUTE_TOOL(tool_name, tool_input, cwd)


oc_repl.execute_tool = _patched_execute_tool

# 控制台日志兜底：print_tool_result 把结果塞进 rich markup，遇 [ ] 乱码抛 MarkupError 炸回合。
# 日志纯装饰，失败静默。三个 agent 模块共享 oc_repl，_cpq_safe 防重复包装。
if not getattr(oc_repl.print_tool_result, "_cpq_safe", False):
    _ORIG_PRINT_TOOL_RESULT = oc_repl.print_tool_result

    def _safe_print_tool_result(name, result):
        try:
            _ORIG_PRINT_TOOL_RESULT(name, result)
        except Exception:
            pass

    _safe_print_tool_result._cpq_safe = True
    oc_repl.print_tool_result = _safe_print_tool_result

SYSTEM_PROMPT = f"""\
你是「规则助手」，服务于亿纬锂能 CPQ 规则配置页面。左边是与用户的对话，右边是「规则配置表单」。
你只做两步，而且两步严格分开、不要一步做完：

【第一步 · 导入规则文档 → 填右侧表单】
- 输入：用户上传/粘贴的规则说明文档（Word/PDF/Excel/文本），消息通常以「【第一步·导入规则…】」开头。
- 任务：忠实地把文档里的每一条规则抽取成一行，字段只有四个：规则名称、规则类型、目标规则库、规则描述
  （规则描述保留文档原话里的触发条件与结果，不要改写、不要臆造）。
- 判定目标规则库：产品配置/配单/互斥/数量约束 → md_clm_distribution_rule；定价/成本/价格 → md_clm_material_price_rule；
  报价/加价/折扣/费用 → md_clm_pricing_surcharge_factor；拿不准就填 md_clm_distribution_rule，并在描述里注明「待人工确认」。
- 调用 rule_result(stage="import", rules=[...]) 把所有规则行一次性填进右侧表单。
- **第一步不要生成 Groovy，也不需要查数据库**，只做结构化转写。
- 完成后用一两句话说明导入了几条规则，并提示用户可点「生成 Groovy 公式」进入第二步。

【第二步 · 用数据库参数 + 规则描述 → 生成 Groovy 公式和代码】
- 触发：用户点「生成 Groovy 公式」或明确要求生成公式时（消息通常以「【第二步·生成公式…】」开头，并附上第一步的规则列表）。
- 任务：对每一条规则，结合「规则描述 + 数据库里的真实参数」产出可落库的 Groovy 公式与完整代码。
- **必须先用 sql_query 查数据库拿参数依据**：字段字典、既有 rule_expression 样例、样例 BOM 订单参数、加价因子明细等
  （下方内置的「数据库字段上下文」可作起点，最新数据以查库为准）。
- 优先使用这些业务字段：{", ".join(RULE_FIELDS)}。
- 公式风格贴近库里现有 rule_expression：if (条件) {{ return 结果 }} … return null；字符串比较用单引号（如 客户等级 == 'S'）；
  报价/加价规则返回数值，配置/校验规则返回约束字符串或 true/null。
- 变量名用业务属性名（电量/客户等级/冷却方式…）；数据库没有物理列时也用业务名，并在 db_evidence 里注明它来自哪张表或字段字典。
- 调用 rule_result(stage="groovy", rules=[{{rule_name, groovy_formula, groovy_code, source_fields, db_evidence}}])；
  rule_name 必须和第一步一致，前端据此把公式回填到对应规则行。
- 回复里简述每条规则用了哪些字段、公式是否有不确定点。

除这两步外不做别的事；不能臆造数据库字段。

Groovy 公式示例：
```groovy
if (客户等级 == 'S') {{ return 0.88 }}
if (客户等级 == 'A') {{ return 0.92 }}
return null
```
```groovy
if (电量 == '280kWh') {{ return "冷却方式!=风冷" }}
return null
```

数据库字段上下文：
{RULE_FIELD_CONTEXT}

完整库 schema（来自 亿纬锂能DA梳理.xlsx「规则助手」sheet，据此生成 PostgreSQL SQL）：
{DB_SCHEMA_TEXT}
"""


def load_settings() -> dict:
    for path in (SETTINGS_PATH, FALLBACK_SETTINGS_PATH):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return data
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def save_settings(s: dict):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _apply_provider_env(provider: str, key: str):
    if not key:
        return
    envs = PROVIDERS.get(provider, {}).get("env") or []
    if envs:
        os.environ[envs[0]] = key


def apply_saved_provider_keys():
    for prov, key in (load_settings().get("api_keys") or {}).items():
        _apply_provider_env(prov, key)


# 注册「本地模型」provider + DeepSeek 深度思考补丁（运行时注入，不改 open-claude 包）
cpq_llm.install()
cpq_llm.configure(load_settings())


def _pick_model_by_available_key(current: str) -> str:
    if get_api_key_for(get_model_provider(current)):
        return current
    for model in AVAILABLE_MODELS:
        if get_api_key_for(model.get("provider", "anthropic")):
            return model["id"]
    return current


def models_catalog() -> list:
    """可选模型清单（POC 部署不提供 Claude 系列）；首项为「无模型」（人工填写模式）。"""
    out = [{
        "id": NO_MODEL_ID,
        "label": "无模型（人工填写）",
        "provider": "none",
        "provider_label": "无模型",
        "configured": True,
    }]
    for model in AVAILABLE_MODELS:
        prov = model.get("provider", "anthropic")
        if prov == "anthropic":
            continue
        out.append({
            "id": model["id"],
            "label": model["label"],
            "provider": prov,
            "provider_label": PROVIDERS.get(prov, {}).get("label", prov),
            "configured": bool(get_api_key_for(prov)),
        })
    out.append(cpq_llm.catalog_entry())  # 本地模型（OpenAI 兼容网关，手动配置）
    return out


def _sanitize_model(model_id: str) -> str:
    return re.sub(r"\[[^\]]*\]$", "", model_id or "").strip()


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
    if not os.path.isdir(HISTORY_DIR):
        return []
    out = []
    for name in os.listdir(HISTORY_DIR):
        if not name.endswith(".json"):
            continue
        data = load_history(name[:-5])
        if not data:
            continue
        events = data.get("events", [])
        out.append({
            "id": data.get("id"),
            "title": data.get("title") or "新建规则",
            "created": data.get("created"),
            "updated": data.get("updated"),
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


def _stringify(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text") or json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def _build_profile() -> AgentProfile:
    prof = AgentProfile()
    prof.name = "cpq-rule"
    prof.description = "CPQ 规则助手（文件导入与 Groovy 规则生成）"
    prof.system_prompt_mode = "override"
    prof.system_prompt_override = SYSTEM_PROMPT
    prof.memory_mode = "off"
    prof.permission_mode = "always_allow"
    prof.disabled_tools = list(DISABLED_TOOLS)
    prof.max_iterations = 30
    return prof


class Bridge:
    def __init__(self, cwd: str):
        self.cwd = cwd
        self.conv = Conversation(cwd, permission_mode="always_allow", profile=_build_profile())
        clean = _sanitize_model(self.conv.model)
        if clean and clean != self.conv.model:
            print(f"[rule-agent] 模型名清洗: {self.conv.model} -> {clean}")
            self.conv.model = clean
            os.environ["CLAUDE_MODEL"] = clean
        self.conv.permissions._prompt_user = lambda *a, **k: (True, "")
        self._inject_tools()
        self.lock = threading.Lock()
        self._new_session()
        try:
            self.apply_settings(load_settings(), persist=False)
        except Exception:
            traceback.print_exc()

    def _new_session(self):
        self.session_id = uuid.uuid4().hex[:12]
        self.events = []
        self.title = None
        self.created = _now_iso()

    def _persist(self):
        if not self.events:
            return
        save_history({
            "id": self.session_id,
            "title": self.title or ("新建规则 " + self.created[:10]),
            "created": self.created,
            "updated": _now_iso(),
            "model": self.conv.model,
            "events": self.events,
            "messages": self.conv.messages,
        })

    def open_session(self, sid: str):
        data = load_history(sid)
        if not data:
            return None
        with self.lock:
            self.session_id = data.get("id") or uuid.uuid4().hex[:12]
            self.events = data.get("events", [])
            self.title = data.get("title")
            self.created = data.get("created") or _now_iso()
            self.conv.messages[:] = data.get("messages", [])
            self.conv.session = SessionStore(self.cwd)
            _rule_events().clear()
        return {
            "id": self.session_id,
            "title": self.title,
            "model": data.get("model"),
            "events": self.events,
        }

    def snapshot(self) -> dict:
        """当前会话的回放数据（结构同 open_session 的返回，供已在池中的会话复用）。"""
        with self.lock:
            return {
                "id": self.session_id,
                "title": self.title,
                "model": self.conv.model,
                "events": list(self.events),
            }

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
            "local": cpq_llm.local_public(),
        }

    def apply_settings(self, s: dict, persist: bool = True) -> dict:
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
                keys = cur.get("api_keys") or {}
                keys.update(new_keys)
                cur["api_keys"] = keys
            if cpq_llm.local_model_id() or cpq_llm.local_public()["base_url"]:
                cur["local"] = cpq_llm.local_persist()
            save_settings(cur)
        return self.current_settings()

    def _inject_tools(self):
        names = {s.get("name") for s in self.conv.tool_schemas}
        if "sql_query" not in names:
            self.conv.tool_schemas.append(SQL_QUERY_SCHEMA)
        if "rule_result" not in names:
            self.conv.tool_schemas.append(RULE_RESULT_SCHEMA)

    def meta(self) -> dict:
        return {
            "service": "cpq-rule-agent",
            "model": self.conv.model,
            "profile": self.conv.profile.name,
            "cwd": self.cwd,
            "session_id": self.session_id,
            "db": DB_NAME,
            "rule_fields": RULE_FIELDS,
            "settings": self.current_settings(),
        }

    def reset(self):
        with self.lock:
            self.conv.messages.clear()
            self.conv.session = SessionStore(self.cwd)
            self.conv.cost_tracker.__init__()
            _rule_events().clear()
            self._new_session()

    def stream_turn(self, text: str, emit, display=None):
        with self.lock:
            conv = self.conv
            bubble = text if display is None else display
            if bubble:
                self.events.append({"type": "user", "text": bubble})
                if not self.title and bubble.strip() and not bubble.startswith("【"):
                    self.title = bubble.strip().replace("\n", " ")[:32]
            if conv.model == NO_MODEL_ID:
                # 无模型模式：不调用大模型，仅记录（【…】开头的系统消息静默入档；
                # 用户手打的聊天给一句提示）。消息仍写入 messages 备切回模型时有上下文
                conv.add_user_message(text)
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
                        _rule_events().clear()
                        conv._execute_pending_tools()
                        for ev in _rule_events():
                            emit({"type": "rule_result", "result": ev})
                            self.events.append({"type": "rule_result", "result": ev})
                        _rule_events().clear()
                        last = conv.messages[-1] if conv.messages else None
                        if last and last.get("role") == "user" and isinstance(last.get("content"), list):
                            for block in last["content"]:
                                if isinstance(block, dict) and block.get("type") == "tool_result":
                                    emit({
                                        "type": "tool_result",
                                        "tool_use_id": block.get("tool_use_id", ""),
                                        "content": _stringify(block.get("content", ""))[:2000],
                                        "is_error": bool(block.get("is_error", False)),
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
        # 发给模型前修复 tool_use/tool_result 配对（压缩/中断可能留下孤儿块 → 供方 400）
        conv.messages[:] = cpq_msgutil.sanitize_tool_pairs(conv.messages)
        gen = stream_message(
            conv.client,
            conv.messages,
            conv.system_prompt,
            model=conv.model,
            tools=conv.tool_schemas,
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
                    "type": "tool_use",
                    "id": ev["id"],
                    "name": ev["name"],
                    "input": ev["input"],
                })
                tool_input = ev["input"]
                if ev["name"] == "rule_result":
                    tool_input = {
                        "stage": (ev["input"] or {}).get("stage"),
                        "count": len((ev["input"] or {}).get("rules") or []),
                        "notes": (ev["input"] or {}).get("notes"),
                    }
                emit({"type": "tool_use", "id": ev["id"], "name": ev["name"], "input": tool_input})
                self.events.append({"type": "tool_use", "name": ev["name"], "input": tool_input})
            elif t == "message_end":
                stop_reason = ev.get("stop_reason", "end_turn")
                usage = ev.get("usage", {})
                conv.cost_tracker.add_usage(
                    conv.model,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    cache_read=usage.get("cache_read_input_tokens", 0),
                    cache_creation=usage.get("cache_creation_input_tokens", 0),
                )
            elif t == "error":
                emit({"type": "error", "error": ev["error"]})
        if text_buf or tool_uses:
            content = []
            if text_buf:
                content.append({"type": "text", "text": "".join(text_buf)})
                self.events.append({"type": "text", "text": "".join(text_buf)})
            content.extend(tool_uses)
            conv.messages.append({"role": "assistant", "content": content})
        return stop_reason


def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_pdf(raw: bytes):
    errors = []
    try:
        import pdfplumber
        parts = []
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                tables = page.extract_tables() or []
                table_text = "\n".join(
                    " | ".join((cell or "").strip() for cell in row)
                    for table in tables for row in table if any(row)
                )
                seg = "\n".join(x for x in (text, table_text) if x.strip())
                if seg.strip():
                    parts.append(f"[第{i + 1}页]\n{seg}")
                if sum(len(p) for p in parts) > MAX_EXTRACT_CHARS:
                    break
        if parts:
            return "\n\n".join(parts), None
        errors.append("pdfplumber 未提取到文本")
    except ImportError:
        errors.append("未安装 pdfplumber")
    except Exception as e:
        errors.append(f"pdfplumber:{e}")
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        parts = []
        for i, page in enumerate(reader.pages):
            text = page.extract_text() or ""
            if text.strip():
                parts.append(f"[第{i + 1}页]\n{text}")
        if parts:
            return "\n\n".join(parts), None
        errors.append("pypdf 未提取到文本")
    except ImportError:
        errors.append("未安装 pypdf")
    except Exception as e:
        errors.append(f"pypdf:{e}")
    return None, "PDF 解析失败: " + "；".join(errors)


def _docx_stdlib(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    out = []
    for para in re.split(r"</w:p>", xml):
        para = re.sub(r"<w:tab\b[^>]*/?>", "\t", para)
        texts = re.findall(r"<w:t\b[^>]*>(.*?)</w:t>", para, re.S)
        if texts:
            line = html.unescape("".join(texts)).strip()
            if line:
                out.append(line)
        if sum(len(x) for x in out) > MAX_EXTRACT_CHARS:
            break
    return "\n".join(out)


def _extract_docx(raw: bytes):
    try:
        import docx
        doc = docx.Document(io.BytesIO(raw))
        parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                cells = [(cell.text or "").strip() for cell in row.cells]
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
        return None, f"Word 解析失败: {e}"


def _xlsx_stdlib(raw: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            ss = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            for si in re.findall(r"<si>(.*?)</si>", ss, re.S):
                shared.append(html.unescape("".join(re.findall(r"<t\b[^>]*>(.*?)</t>", si, re.S))))
        sheets = sorted(n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n))
        out = []
        for idx, sheet in enumerate(sheets, 1):
            xml = z.read(sheet).decode("utf-8", "replace")
            out.append(f"### 工作表{idx}")
            for rowm in re.findall(r"<row\b[^>]*>(.*?)</row>", xml, re.S):
                cells = []
                for attrs, body in re.findall(r"<c\b([^>]*)>(.*?)</c>", rowm, re.S):
                    vm = re.search(r"<v>(.*?)</v>", body, re.S)
                    if vm:
                        value = html.unescape(vm.group(1))
                        if 't="s"' in attrs:
                            try:
                                value = shared[int(value)]
                            except (ValueError, IndexError):
                                pass
                        cells.append(value.strip())
                    else:
                        inline = re.findall(r"<t\b[^>]*>(.*?)</t>", body, re.S)
                        cells.append(html.unescape("".join(inline)).strip() if inline else "")
                if any(cells):
                    out.append(" | ".join(cells))
                if sum(len(x) for x in out) > MAX_EXTRACT_CHARS:
                    break
    return "\n".join(out)


def _extract_xlsx(raw: bytes):
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True, read_only=True)
        parts = []
        try:
            for ws in wb.worksheets:
                parts.append(f"### 工作表: {ws.title}")
                for row in ws.iter_rows(values_only=True):
                    cells = [("" if cell is None else str(cell)).strip() for cell in row]
                    if any(cells):
                        parts.append(" | ".join(cells))
                    if sum(len(p) for p in parts) > MAX_EXTRACT_CHARS:
                        break
        finally:
            wb.close()
        if parts:
            return "\n".join(parts), None
    except ImportError:
        pass
    except Exception:
        traceback.print_exc()
    try:
        text = _xlsx_stdlib(raw)
        return (text, None) if text.strip() else (None, "Excel 没有可提取的内容")
    except Exception as e:
        return None, f"Excel 解析失败: {e}"


def _extract_text(name: str, raw: bytes):
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
            return None, f"旧版 {ext} 格式暂不支持，请另存为 .docx/.xlsx 后上传"
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            return None, "图片暂无法提取文字，请粘贴文字说明"
        return _decode_text(raw), None
    except Exception as e:
        traceback.print_exc()
        return None, f"解析失败: {e}"


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
        elif path == "/api/models":
            self._send_json({"models": models_catalog(), "current": bridge.conv.model})
        elif path == "/api/settings":
            self._send_json(bridge.current_settings())
        elif path == "/api/sessions":
            self._send_json({"sessions": list_history()})
        elif path == "/api/session":
            sid = (parse_qs(parsed.query).get("id") or [""])[0]
            data = load_history(sid)
            self._send_json(data) if data else self.send_error(404)
        elif path in ("/", "/index.html"):
            self._send_json({
                "service": "cpq-rule-agent",
                "api": "/api/send",
                "extract": "/api/extract",
                "db": DB_NAME,
            })
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/send":
            self._handle_send()
        elif path == "/api/convert":
            self._handle_convert()
        elif path == "/api/extract":
            self._handle_extract()
        elif path == "/api/import":
            res = _import_rules(self._read_body())
            self._send_json(res, status=200 if res.get("ok") else 400)
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
        elif path == "/api/settings":
            try:
                data = self._read_body()
                res = pool_apply_settings(data)
                broadcast_settings(data)  # 同步到另外两个助手（一体化服务下）
                self._send_json(res)
            except Exception as e:
                traceback.print_exc()
                self._send_json({"error": str(e)}, status=500)
        else:
            self.send_error(404)

    def _handle_extract(self):
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
        trunc = len(text) > MAX_EXTRACT_CHARS
        text = text[:MAX_EXTRACT_CHARS]
        self._send_json({"ok": True, "name": name, "chars": len(text), "truncated": trunc, "text": text})

    def _handle_send(self):
        data = self._read_body()
        text = (data.get("message") or "").strip()
        display = data.get("display")
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
            pass

    def _handle_convert(self):
        data = self._read_body()
        text = (data.get("text") or data.get("message") or "").strip()
        if not text:
            self._send_json({"ok": False, "error": "缺少 text/message"}, status=400)
            return
        collected = []
        rule_results = []

        def emit(obj):
            if obj.get("type") == "text":
                collected.append(obj.get("text") or "")
            elif obj.get("type") == "rule_result":
                rule_results.append(obj.get("result") or {})

        b = bridge_for(data.get("sid"))  # 带 sid 走会话专属 Bridge，缺省回退全局
        b.stream_turn(text, emit, display=data.get("display"))
        self._send_json({"ok": True, "text": "".join(collected), "rule_results": rule_results})


def main():
    parser = argparse.ArgumentParser(description="CPQ 规则助手 Agent 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=47296)
    args = parser.parse_args()

    os.environ["OC_READONLY_FS"] = "1"
    saved = load_settings()
    apply_saved_provider_keys()
    if saved.get("model"):
        os.environ["CLAUDE_MODEL"] = _sanitize_model(resolve_model(saved["model"]))
    else:
        effective = _pick_model_by_available_key(get_model())
        if effective != get_model():
            os.environ["CLAUDE_MODEL"] = _sanitize_model(effective)
            print(f"[rule-agent] 默认模型无可用 Key，已切换到: {effective}")

    provider = get_model_provider(get_model())
    if not get_api_key_for(provider):
        spec = PROVIDERS.get(provider, {})
        envs = " or ".join(spec.get("env", [])) or "the provider API key"
        print(
            f"[rule-agent] 警告: 当前模型 {get_model()} 的 provider "
            f"{spec.get('label', provider)} 未配置 API Key。可在页面设置或环境变量 {envs} 中配置。",
            file=sys.stderr,
        )

    global bridge
    print(f"[rule-agent] 工作目录 {SCRIPT_DIR}")
    print(f"[rule-agent] 数据库 {DB_NAME}")
    bridge = Bridge(SCRIPT_DIR)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[rule-agent] model={bridge.conv.model} profile={bridge.conv.profile.name}")
    print(f"[rule-agent] API: http://{args.host}:{args.port}/api/send  (Ctrl+C 停止)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[rule-agent] 已停止")
    finally:
        try:
            bridge.conv.mcp.shutdown()
        except Exception:
            pass
        server.server_close()


if __name__ == "__main__":
    main()

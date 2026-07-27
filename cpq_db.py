"""配置报价CPQ 三助手统一数据访问层（报价 / 配置XBOM / 规则）。

改造（2026-07-13）：
  1) 业务数据源 sqlite → 远程 Postgres（三助手共用同一库），替代原
     database/亿纬锂能_da.sqlite。sql_query 工具与「导入数据库」都走这里。
  2) Agent 可调用的库 schema（本体语义层）来自 亿纬锂能DA梳理.xlsx 的三个 sheet
     （报价助手 / 配置助手 / 规则助手），不再反射数据库，保证语义层稳定、与 DA 梳理一致。

连接参数（均可用环境变量覆盖）：
  CPQ_PG_HOST / CPQ_PG_PORT / CPQ_PG_USER / CPQ_PG_PASSWORD / CPQ_PG_DATABASE / CPQ_PG_SCHEMA
默认值取自需求给定的 172.16.5.181:32444，postgres/postgres。
"metabase/master_data" 按 <database>/<schema> 解释：dbname=metabase、schema=master_data。
若实际相反，改 CPQ_PG_DATABASE / CPQ_PG_SCHEMA 两个环境变量即可，无需改代码。

psycopg（psycopg3）为延迟导入：未装驱动/连不上库时，schema-from-xlsx 仍可离线工作，
仅在真正执行 SQL 时才需要连接，失败会以文本错误回给 Agent，不会让服务启动崩溃。
"""
from __future__ import annotations

import os
import re
import threading
import time
from functools import lru_cache
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ONTOLOGY_XLSX = SCRIPT_DIR / "亿纬锂能DA梳理.xlsx"

# ---------------------------------------------------------------------------
# Postgres 连接配置
# ---------------------------------------------------------------------------
PG_HOST = os.getenv("CPQ_PG_HOST", "172.16.5.181")
PG_PORT = int(os.getenv("CPQ_PG_PORT", "32444"))
PG_USER = os.getenv("CPQ_PG_USER", "postgres")
PG_PASSWORD = os.getenv("CPQ_PG_PASSWORD", "postgres")
PG_DATABASE = os.getenv("CPQ_PG_DATABASE", "metabase")
PG_SCHEMA = os.getenv("CPQ_PG_SCHEMA", "master_data")
PG_CONNECT_TIMEOUT = int(os.getenv("CPQ_PG_CONNECT_TIMEOUT", "10"))

# 用于提示词/工具描述里的库标识（不含口令）
DB_LABEL = f"Postgres {PG_HOST}:{PG_PORT}/{PG_DATABASE}" + (
    f"（schema={PG_SCHEMA}）" if PG_SCHEMA else ""
)


def connect(readonly: bool = True):
    """打开一个到远程 Postgres 的连接（psycopg3，autocommit）。

    readonly=True：设为只读事务（业务库取数）；readonly=False：可写（导入数据库）。
    调用方负责 close()。连不上会抛异常，由调用方兜底成文本错误。
    """
    import psycopg  # 延迟导入

    conn = psycopg.connect(
        host=PG_HOST,
        port=PG_PORT,
        user=PG_USER,
        password=PG_PASSWORD,
        dbname=PG_DATABASE,
        connect_timeout=PG_CONNECT_TIMEOUT,
        autocommit=True,
    )
    with conn.cursor() as cur:
        if PG_SCHEMA:
            # 注入安全：用 set_config 以参数形式设置 search_path
            cur.execute(
                "SELECT set_config('search_path', %s, false)",
                (f"{PG_SCHEMA}, public",),
            )
        if readonly:
            cur.execute("SET default_transaction_read_only = on")
    return conn


def run_select(sql: str, limit: int):
    """只读执行一条 SELECT，返回 (cols, rows)。rows 最多取 limit+1 行（供调用方判断是否截断）。"""
    conn = connect(readonly=True)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchmany(limit + 1)
    finally:
        conn.close()
    return cols, rows


# ---------------------------------------------------------------------------
# 本体语义层：从 亿纬锂能DA梳理.xlsx 三个 sheet 解析
# ---------------------------------------------------------------------------
# agent key -> sheet 名
_SHEET = {"quote": "报价助手", "config": "配置助手", "rule": "规则助手"}


def _norm(v) -> str:
    return "" if v is None else str(v).strip()


_IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*$")


def _is_ident(s: str) -> bool:
    """是否像物理标识符（表名/列名一定是 ASCII，中文一定不是）。"""
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", (s or "").strip()))


def _split_entity(cell: str):
    """把配置/规则 sheet 的「中文名-表code」拆成 (cn, table)。

    表名一律取**末尾那段 ASCII 标识符**：DA 里存在
    「物料编码信息-物料数据md_clm_material_base_info」这类把中文写进后半段的填法，
    按 '-' 硬拆会把「物料数据」粘进表名，导致下游 SQL 全部失败。"""
    s = cell.replace("\n", "").strip()
    m = _IDENT_RE.search(s)
    if m:
        table = m.group(0)
        cn = s[:m.start()].strip().rstrip("-").strip()
        return (cn or table), table
    if "-" in s:
        cn, code = s.rsplit("-", 1)
        return cn.strip(), code.strip()
    return s, s


@lru_cache(maxsize=1)
def _load_ontology() -> dict:
    """解析三个 sheet，返回 {agent: {"entities":[...], "bi_index":{(bo,name):[(attr_name,type)]}}}。

    entity = {"table","cn","business_object","attrs":[{code,name,type,pk,fk,note}]}
    bi_index 仅报价助手用到（按 业务对象+逻辑实体名称 定位固定表单字段）。
    """
    import openpyxl

    wb = openpyxl.load_workbook(ONTOLOGY_XLSX, read_only=True, data_only=True)
    result: dict = {}

    def rows_of(sheet):
        ws = wb[sheet]
        for r in ws.iter_rows(values_only=True):
            yield [_norm(c) for c in r]

    def _add_attr(e, attr):
        # 按 code 去重（DA 梳理里 BOM 头/行等有重复录入的行）
        if attr["code"] and any(a["code"] == attr["code"] for a in e["attrs"]):
            return
        e["attrs"].append(attr)

    # ---- 报价助手：业务对象|逻辑实体code|逻辑实体名称|属性code|属性名称|字段类型|主键|外键|示例 ----
    ents: dict = {}
    ff = ["", "", ""]
    for i, r in enumerate(rows_of(_SHEET["quote"])):
        if i == 0:
            continue
        r = (r + [""] * 9)[:9]
        for j in range(3):
            if r[j]:
                ff[j] = r[j]
        bo, code, name = ff
        if not code:
            continue
        e = ents.setdefault(code, {"table": code, "cn": name, "business_object": bo, "attrs": []})
        if r[3]:
            _add_attr(e, {"code": r[3], "name": r[4], "type": r[5],
                          "pk": r[6], "fk": r[7], "note": r[8]})
    quote_ents = list(ents.values())
    # bi_index 按 (业务对象, 逻辑实体名称) 存整份属性（供 bi_fields 过滤 id/主外键）
    bi_index: dict = {}
    for e in quote_ents:
        bi_index[(e["business_object"], e["cn"])] = e["attrs"]
    result["quote"] = {"entities": quote_ents, "bi_index": bi_index}

    # 两页列布局不同（配置助手比规则助手多一列「字段类型」），按页显式给出列序号，
    # 否则主键/外键/备注会整体错位一列。
    # ---- 配置助手：业务对象|逻辑实体(名-code)|属性名称|字段编号|字段类型|主键|外键|备注 ----
    # ---- 规则助手：业务对象|逻辑实体(名-code)|属性名称|字段编号|主键|外键 ----
    for agent, ncol, ci_type, ci_pk, ci_fk, ci_note in (
            ("config", 8, 4, 5, 6, 7),
            ("rule", 6, None, 4, 5, None)):
        ents = {}
        fbo = fent = ""
        for i, r in enumerate(rows_of(_SHEET[agent])):
            if i == 0:
                continue
            r = (r + [""] * ncol)[:ncol]
            if r[0]:
                fbo = r[0]
            if r[1]:
                fent = r[1]
            cn, table = _split_entity(fent)
            if not table:
                continue
            e = ents.setdefault(table, {"table": table, "cn": cn, "business_object": fbo, "attrs": []})
            # 「属性名称 / 字段编号」两列在个别表里填反了（如 product_para_value）。
            # 物理列 code 必然是 ASCII 标识符，据此纠正，避免把中文当成列名喂给模型。
            code, name = r[3], r[2]
            if not _is_ident(code) and _is_ident(name):
                code, name = name, code
            if code:
                _add_attr(e, {"code": code, "name": name,
                              "type": r[ci_type] if ci_type is not None else "",
                              "pk": r[ci_pk], "fk": r[ci_fk],
                              "note": r[ci_note] if ci_note is not None else ""})
        result[agent] = {"entities": list(ents.values()), "bi_index": {}}

    wb.close()
    return result


def _is_id_or_key(a: dict) -> bool:
    """判断某属性是否为 id 类 / 主键 / 外键（表单展示时按需求排除）。"""
    if a.get("pk") or a.get("fk"):
        return True
    code = (a.get("code") or "").strip().lower()
    if code == "id" or code.endswith("_id"):
        return True
    name = (a.get("name") or "").strip().lower()
    if name.endswith("id"):  # 测算单id / 产品编码ID / bom清单头ID …
        return True
    return False


def schema_text(agent: str) -> str:
    """紧凑 schema：每行一表 `- table（中文名）(col1, col2, ...)`，作为模型生成 SQL 的上下文。"""
    try:
        onto = _load_ontology()[agent]
    except Exception:
        return ""
    lines = []
    for e in onto["entities"]:
        cols = ", ".join(a["code"] for a in e["attrs"] if a["code"])
        cn = f"（{e['cn']}）" if e["cn"] and e["cn"] != e["table"] else ""
        lines.append(f"- {e['table']}{cn}({cols})")
    return "\n".join(lines)


def schema_doc(agent: str) -> str:
    """按 业务对象 → 逻辑实体 → 属性 展开的语义说明（含中文名/类型/主外键/取值示例）。"""
    try:
        onto = _load_ontology()[agent]
    except Exception:
        return ""
    by_bo: dict = {}
    for e in onto["entities"]:
        by_bo.setdefault(e["business_object"] or "（未分类）", []).append(e)
    out = []
    for bo, ents in by_bo.items():
        out.append(f"## 业务对象：{bo}")
        for e in ents:
            out.append(f"### {e['table']}（{e['cn']}）")
            for a in e["attrs"]:
                tags = []
                if a.get("pk"):
                    tags.append("主键")
                if a.get("fk"):
                    tags.append(f"外键→{a['fk']}" if a["fk"] not in ("是", "Y") else "外键")
                if a.get("type"):
                    tags.append(a["type"])
                if a.get("note"):
                    tags.append(f"示例/说明：{a['note']}")
                tag = ("  [" + " | ".join(tags) + "]") if tags else ""
                out.append(f"- {a['code']} {a['name']}{tag}")
        out.append("")
    return "\n".join(out).strip()


def bi_fields(business_object: str, logic_entity_name: str):
    """报价助手固定表单字段：按 (业务对象, 逻辑实体名称) 返回 [(属性名称, 字段类型)]；无则 []。
    按需求**排除所有 id 类 / 主键 / 外键字段**，其余全量返回（保持 DA 录入顺序）。"""
    try:
        idx = _load_ontology()["quote"]["bi_index"]
    except Exception:
        return []
    attrs = idx.get((business_object, logic_entity_name), [])
    return [(a["name"], a["type"]) for a in attrs if not _is_id_or_key(a)]


_AGENT_CN = {"quote": "报价助手", "config": "配置助手", "rule": "规则助手"}


def attr_code_map(agent: str, table: str) -> dict:
    """某实体的 {属性名称(中文): 属性code} 映射（含 id/主外键——导入时若有值要原样存下）。
    另把 code 本身也映射到自己，允许调用方直接给 code 键。"""
    try:
        onto = _load_ontology()[agent]
    except Exception:
        return {}
    for e in onto["entities"]:
        if e["table"].lower() == (table or "").lower():
            m = {}
            for a in e["attrs"]:
                if a["code"]:
                    if a["name"]:
                        m[a["name"]] = a["code"]
                    m[a["code"]] = a["code"]
            return m
    return {}


# ---------------------------------------------------------------------------
# 通用入库（导入数据库）：按 information_schema 列类型做显式 ::cast，
# 值全部来自前端字符串。空值不插（NULL/由 PG 默认值生成，id 类字段即如此）。
# ---------------------------------------------------------------------------
_NUM_TYPES = {"integer", "bigint", "smallint", "numeric", "real", "double precision", "money"}
_CASTABLE = {"integer", "bigint", "smallint", "numeric", "real", "double precision",
             "boolean", "date", "timestamp without time zone", "timestamp with time zone", "time without time zone"}
_CAST_NAME = {"timestamp without time zone": "timestamp", "timestamp with time zone": "timestamptz",
              "time without time zone": "time"}
_TRUE_WORDS = {"是", "true", "1", "y", "yes", "真", "on"}
_FALSE_WORDS = {"否", "false", "0", "n", "no", "假", "off", "无"}
_NUM_RE = None


def table_types(conn, table: str) -> dict:
    """{列名: data_type}；表不存在返回 {}。表名按 PG 未加引号折叠成小写查询。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = lower(%s)",
            (PG_SCHEMA or "public", table),
        )
        return {r[0]: r[1] for r in cur.fetchall()}


def _coerce(value, dtype: str):
    """把前端字符串值按目标列类型宽松清洗；返回 None 表示该列跳过（存 NULL）。"""
    global _NUM_RE
    s = str(value).strip()
    if s == "":
        return None
    if dtype == "boolean":
        low = s.lower()
        if low in _TRUE_WORDS:
            return "true"
        if low in _FALSE_WORDS:
            return "false"
        return None
    if dtype in _NUM_TYPES:
        import re
        if _NUM_RE is None:
            _NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
        m = _NUM_RE.search(s.replace(",", "").replace("，", ""))
        return m.group(0) if m else None  # 提不出数字（如“推荐”文本）→ 跳过该列
    return s


def _insert_one(cur, table: str, row: dict, types: dict, ret_col: str = None):
    """插入一行（row 键=列code）。返回 (是否执行, RETURNING 值)。空/清洗失败/表里没有的列不插。"""
    cols, vals, ph = [], [], []
    for c, v in row.items():
        dt = types.get(c)
        if dt is None:
            continue  # 目标表没有该列
        cv = _coerce(v, dt)
        if cv is None:
            continue
        cols.append(c)
        vals.append(cv)
        cast = _CAST_NAME.get(dt, dt) if dt in _CASTABLE else None
        ph.append(f"%s::{cast}" if cast else "%s")
    if not cols:
        return False, None
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(ph)})"
    if ret_col:
        cur.execute(sql + f" RETURNING {ret_col}", vals)
        r = cur.fetchone()
        return True, (r[0] if r else None)
    cur.execute(sql, vals)
    return True, None


def insert_rows(conn, table: str, rows: list) -> int:
    """批量插入（rows=list[dict code->值]），返回成功行数。表不存在抛错。"""
    types = table_types(conn, table)
    if not types:
        raise RuntimeError(f"目标表 {table} 不存在（information_schema 查不到列）")
    saved = 0
    with conn.cursor() as cur:
        for r in rows:
            if not isinstance(r, dict):
                continue
            done, _ = _insert_one(cur, table, r, types)
            if done:
                saved += 1
    return saved


def insert_returning(conn, table: str, row: dict, ret_col: str):
    """插入一行并 RETURNING 某列（拿自动生成的 id 供子表外键用）。表不存在抛错。"""
    types = table_types(conn, table)
    if not types:
        raise RuntimeError(f"目标表 {table} 不存在")
    with conn.cursor() as cur:
        done, val = _insert_one(cur, table, row, types, ret_col=ret_col)
        return val if done else None


# ---------------------------------------------------------------------------
# 雪花 ID：主键生成（对齐库函数 public.snow_next_id() 的 PL/pgSQL 实现）
#   result = (now_ms - our_epoch) << 23 | (shard_id << 10) | (seq % 4096)
# 导入时：主键(PK)用它生成、外键(FK)按 ER 关系引用父表已生成的 PK；均只存后台、不展示。
# 优先调用库函数（跨进程共享 assign_id_seq 保唯一）；库函数不可用时回退客户端实现。
# ---------------------------------------------------------------------------
_SNOW_EPOCH = 1314220021721
_SNOW_SHARD = 5
_snow_lock = threading.Lock()
_snow_seq = 0


def _client_snow_id() -> int:
    global _snow_seq
    with _snow_lock:
        _snow_seq = (_snow_seq + 1) % 4096
        seq = _snow_seq
    now_ms = int(time.time() * 1000)
    return ((now_ms - _SNOW_EPOCH) << 23) | (_SNOW_SHARD << 10) | seq


def snow_next_id(conn) -> int:
    """生成一个雪花主键 ID：优先库函数 snow_next_id()（search_path→public），失败回退客户端实现。"""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT snow_next_id()")
            r = cur.fetchone()
            if r and r[0] is not None:
                return int(r[0])
    except Exception:
        pass
    return _client_snow_id()


def key_cols(agent: str, table: str):
    """返回 (主键列code列表, 外键列code列表) —— 来自 DA 本体（xlsx 的主键/外键标记）。"""
    try:
        onto = _load_ontology()[agent]
    except Exception:
        return [], []
    for e in onto["entities"]:
        if e["table"].lower() == (table or "").lower():
            pk = [a["code"] for a in e["attrs"] if a.get("pk") and a["code"]]
            fk = [a["code"] for a in e["attrs"] if a.get("fk") and a["code"]]
            return pk, fk
    return [], []


def table_columns(agent: str, table: str) -> set:
    """该表全部列 code 集合（来自 DA 本体）。"""
    return set(attr_code_map(agent, table).values())


def sheet_tables(agent: str) -> set:
    """某助手 sheet（quote/config/rule）在 DA 本体里的全部物理表名（小写）。
    用于各助手 sql_query 的表访问边界（取不到本体返回空集，调用方需给兜底）。"""
    try:
        return {e["table"].lower() for e in _load_ontology()[agent]["entities"] if e.get("table")}
    except Exception:
        return set()


def search_fields(keyword: str, limit: int = 8):
    """跨三个 sheet 按属性名/字段code 模糊搜索，返回字段字典行（本体语义层，替代旧 da_fields 表）。"""
    kw = (keyword or "").strip()
    if not kw:
        return []
    try:
        onto = _load_ontology()
    except Exception:
        return []
    out = []
    for agent in ("quote", "config", "rule"):
        for e in onto[agent]["entities"]:
            for a in e["attrs"]:
                if kw in (a["name"] or "") or kw in (a["code"] or ""):
                    out.append(
                        f"assistant={_AGENT_CN[agent]} business_object={e['business_object']} "
                        f"logic_entity={e['cn']} physical_table={e['table']} "
                        f"attribute_name={a['name']} field_code={a['code']} field_type={a['type']}"
                    )
                    if len(out) >= limit:
                        return out
    return out

"""DA 数据库(SQLite)的连接、建库与通用访问helper。

对应 docs/数据架构设计_DA.md 的落地实现:
  - 结构化数据 -> SQLite 单文件库(默认 DATA_DIR/da.db)
  - 图纸/模型/文档 -> 文件夹(见 kb_library.py),库里只存路径与哈希

为什么用标准库 sqlite3 而不是 SQLAlchemy:
  DA 的表是宽而稳定的业务表,DDL 已由 da_schema.sql 明确定义;直接用 sqlite3 可以
  让"文档里的建表语句"与"库里的建表语句"是同一份文本,不存在 ORM 映射漂移。
  既有的 SqlMetaBackend(通用文档存储)不受影响,两者互不替代。
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from ..time_utils import now_cst_str

SCHEMA_FILE = Path(__file__).with_name("da_schema.sql")
SCHEMA_VERSION = "2"

# 已建库的实例上,CREATE TABLE IF NOT EXISTS 不会补新增的列,需要显式 ALTER。
# 每项为 (表, 列, 列定义)。只做加列,不改类型、不删列 —— 那类变更须走单独的迁移脚本。
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    # v2: 需求单记录选用的行业模板(半导体/电池/电器)。
    ("src_requirement", "industry", "TEXT NOT NULL DEFAULT 'semiconductor'"),
    # 包装第 3 批: 行业主体表补行业键(空/NULL = 通用, 三行业默认行为不变)。
    # 与 cpq_kb._ADDED_COLUMNS 一一对应, 两侧老库补列口径必须一致。
    ("kb_component", "industry", "TEXT"),
    ("kb_standard_part", "industry", "TEXT"),
    ("kb_equipment_class", "industry", "TEXT"),
    ("kb_equipment", "industry", "TEXT"),
    ("kb_process_step", "industry", "TEXT"),
    ("kb_process_route", "industry", "TEXT"),
    ("kb_inspection_item", "industry", "TEXT"),
    ("kb_material", "industry", "TEXT"),
    ("kb_supplier", "industry", "TEXT"),
    ("kb_cost_rate", "industry", "TEXT"),
    ("kb_cost_factor", "industry", "TEXT"),
    # 费率库另补最低收费(包装费率的最低收费门槛)。
    ("kb_cost_rate", "minimum_charge", "REAL"),
    # 包装 2.3 成本记录留痕(Spec packaging-cost-finance-access.md §2.3):
    # 谁算的这一版成本;老库上 CREATE TABLE IF NOT EXISTS 不补列,不补就写不进去也读不回来。
    ("wip_packaging_cost_estimate", "computed_by", "TEXT"),
    ("wip_packaging_cost_estimate", "computed_by_role", "TEXT"),
    # 包装 BOM 行必须认自己的盒型(Spec packaging-bom-box-type-provenance.md §2.1):
    # 老库上 CREATE TABLE IF NOT EXISTS 不补列,不补就写不进去也读不回来。
    ("wip_packaging_bom_item", "box_type_code", "TEXT"),
    # 成本单要"算时记下"输入版本(Spec packaging-cost-input-version-pinning.md §2.1):
    # 老库上 CREATE TABLE IF NOT EXISTS 不补列,不补就存不下也读不回。
    ("wip_packaging_cost_estimate", "source_versions_json", "TEXT"),
    # 工艺路线要"排产时照的那一版 BOM"（Spec packaging-route-bom-version-pinning.md §2.1）：
    # 老库上 CREATE TABLE IF NOT EXISTS 不补列，不补就存不下也读不回。
    ("wip_packaging_process_route", "source_versions_json", "TEXT"),
    ("wip_packaging_process_route_version", "source_versions_json", "TEXT"),
    # 成本单要"算时记下"包材绑定那一份账（Spec `packaging-cost-content-binding-replay.md` §C1）：
    # 老库上 CREATE TABLE IF NOT EXISTS 不补列，不补就存不下也读不回（读侧只能回放这一份）。
    ("wip_packaging_cost_estimate", "content_binding_json", "TEXT"),
)

# 上线闭环第 2 批: 9 张包装扩展表补「数据来源分层」列(demo/workbook/dwg_confirmed/
# unknown, Spec §3)。老库上 CREATE TABLE IF NOT EXISTS 不补列, 不补就会写不进去。
# 表名清单与 cpq_kb 的 9 张包装表同集合、同顺序; 两侧补列口径必须一致。
_PACKAGING_PROVENANCE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source_type", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("source_ref", "TEXT"),
    ("source_sha256", "TEXT"),
    ("parser_version", "TEXT"),
    ("confirmed_by", "TEXT"),
    ("confirmed_at", "TEXT"),
)
_PACKAGING_PROVENANCE_TABLES: tuple[str, ...] = (
    "kb_packaging_box_type",
    "kb_packaging_part_template",
    "kb_packaging_process_template",
    "kb_packaging_insert_accessory",
    "kb_packaging_cost_formula",
    "kb_packaging_logistics_rule",
    "kb_packaging_match_weight",
    "kb_packaging_cost_content",
    "kb_packaging_tooling_rule",
)

_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = _ADDED_COLUMNS + tuple(
    (table, column, definition)
    for table in _PACKAGING_PROVENANCE_TABLES
    for column, definition in _PACKAGING_PROVENANCE_COLUMNS
)

_local = threading.local()
_init_lock = threading.RLock()
_initialized: set[str] = set()


def now() -> str:
    return now_cst_str()


def db_path() -> Path:
    from ..config import DA_DB_PATH

    return Path(DA_DB_PATH)


# --------------------------------------------------------------------------- #
# 连接
# --------------------------------------------------------------------------- #
def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    # 外键必须显式开启,否则 SQLite 只把 REFERENCES 当注释,DA 的回指约束会形同虚设。
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL 让读写并发(FastAPI 请求线程 + 后台任务线程)不互相阻塞。
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    """返回一个新连接(调用方负责关闭)。常规读写请用 get_conn()。"""
    target = Path(path) if path else db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, check_same_thread=False)
    _configure(conn)
    return conn


def get_conn() -> sqlite3.Connection:
    """线程内复用的连接。sqlite3 连接非线程安全,故按线程各持一个。"""
    target = str(db_path())
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == target:
        return conn
    init_db(Path(target))
    conn = connect(Path(target))
    _local.conn = conn
    _local.path = target
    return conn


def close_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None
        _local.path = None


# --------------------------------------------------------------------------- #
# 建库
# --------------------------------------------------------------------------- #
def init_db(path: Optional[Path] = None, *, force: bool = False) -> Path:
    """执行 da_schema.sql 建表(幂等)。返回库文件路径。"""
    target = Path(path) if path else db_path()
    key = str(target)
    with _init_lock:
        if key in _initialized and not force:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        ddl = SCHEMA_FILE.read_text(encoding="utf-8")
        conn = connect(target)
        try:
            conn.executescript(ddl)
            _add_missing_columns(conn)
            _upgrade_requirement_industry_check(conn)
            conn.execute(
                "INSERT INTO schema_meta(key, value, updated_at) VALUES('schema_version', ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (SCHEMA_VERSION, now()),
            )
            conn.commit()
        finally:
            conn.close()
        _initialized.add(key)
    return target


def _add_missing_columns(conn: sqlite3.Connection) -> list[str]:
    """给已存在的表补上新版本新增的列(幂等)。返回本次实际新增的列。

    SQLite 的 ALTER TABLE ADD COLUMN 不支持带 CHECK 约束,因此迁移出来的列
    只带默认值;新建库仍由 da_schema.sql 附上完整 CHECK。约束差异由业务层
    (industry_templates.normalize)兜住,不会写进非法值。
    """
    added: list[str] = []
    for table, column, definition in _ADDED_COLUMNS:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            continue
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column in columns:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        added.append(f"{table}.{column}")
    return added


def _upgrade_requirement_industry_check(conn: sqlite3.Connection) -> bool:
    """给已建的库放开 src_requirement.industry 的 CHECK（把 packaging 列进白名单）。

    SQLite 不能直接改 CHECK，只能按官方 ALTER TABLE 步骤重建表：建新表 → 整表拷贝 →
    删旧表 → 换名。整段在同一个显式事务里，失败即回滚，不会留下半张表；已经迁过
    （建表语句里已含 packaging）直接跳过，所以重复执行无副作用。迁移期间临时关闭
    外键，避免 DROP 旧表时把 src_requirement_field 的子行级联删掉；数据一行不改写。
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='src_requirement'"
    ).fetchone()
    if not row or not row[0]:
        return False
    if "'packaging'" in row[0]:
        return False
    ddl = SCHEMA_FILE.read_text(encoding="utf-8")
    m = re.search(r"CREATE TABLE IF NOT EXISTS\s+src_requirement\s*\(.*?\n\)\s*;", ddl, re.S)
    if not m:
        return False
    new_ddl = re.sub(r"CREATE TABLE IF NOT EXISTS\s+src_requirement",
                     "CREATE TABLE src_requirement__new", m.group(0), count=1)
    columns = [r["name"] for r in conn.execute("PRAGMA table_info(src_requirement)")]
    if not columns:
        return False
    collist = ", ".join(columns)
    isolation = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN")
        try:
            conn.execute(new_ddl)
            conn.execute(
                f"INSERT INTO src_requirement__new ({collist})"
                f" SELECT {collist} FROM src_requirement")
            conn.execute("DROP TABLE src_requirement")
            conn.execute("ALTER TABLE src_requirement__new RENAME TO src_requirement")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_requirement_project"
                " ON src_requirement(project_id, status)")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.isolation_level = isolation
    return True


def table_names(conn: Optional[sqlite3.Connection] = None) -> list[str]:
    c = conn or get_conn()
    rows = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


# --------------------------------------------------------------------------- #
# 通用访问 helper
# --------------------------------------------------------------------------- #
def query(sql: str, params: Sequence[Any] = (), *, conn: Optional[sqlite3.Connection] = None) -> list[dict]:
    c = conn or get_conn()
    return [dict(r) for r in c.execute(sql, tuple(params)).fetchall()]


def query_one(sql: str, params: Sequence[Any] = (), *, conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    rows = query(sql, params, conn=conn)
    return rows[0] if rows else None


# 读全量结果集的别名：与 query 同一实现，供"一次取多行"的调用方按语义取名。
query_all = query


def execute(sql: str, params: Sequence[Any] = (), *, conn: Optional[sqlite3.Connection] = None) -> sqlite3.Cursor:
    c = conn or get_conn()
    cur = c.execute(sql, tuple(params))
    c.commit()
    return cur


def execute_many(sql: str, seq: Iterable[Sequence[Any]], *, conn: Optional[sqlite3.Connection] = None) -> None:
    c = conn or get_conn()
    c.executemany(sql, [tuple(p) for p in seq])
    c.commit()


def upsert(
    table: str,
    row: dict,
    *,
    keys: Sequence[str],
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """按主键/唯一键 upsert。JSON 值(list/dict)自动序列化。"""
    data = {k: _encode(v) for k, v in row.items() if v is not None or k in keys}
    cols = list(data)
    placeholders = ", ".join("?" for _ in cols)
    updates = [c for c in cols if c not in keys]
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders})"
    if updates:
        assignments = ", ".join(f"{c}=excluded.{c}" for c in updates)
        sql += f" ON CONFLICT({', '.join(keys)}) DO UPDATE SET {assignments}"
    else:
        sql += f" ON CONFLICT({', '.join(keys)}) DO NOTHING"
    execute(sql, [data[c] for c in cols], conn=conn)


def insert(table: str, row: dict, *, conn: Optional[sqlite3.Connection] = None) -> int:
    data = {k: _encode(v) for k, v in row.items() if v is not None}
    cols = list(data)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)})"
    )
    return int(execute(sql, [data[c] for c in cols], conn=conn).lastrowid or 0)


def _encode(value: Any) -> Any:
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, bool):
        return 1 if value else 0
    return value


def decode_json(value: Any, default: Any = None) -> Any:
    """读出 JSON 列。库里存的是文本,业务层拿到的应是 list/dict。"""
    if value in (None, ""):
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default

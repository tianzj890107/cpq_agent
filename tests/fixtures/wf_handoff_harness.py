"""批次 3 红测共用的受控假库：真的执行 `cpq_wf` / `cpq_tech_bridge` 发出的 SQL。

为什么需要它：批次 3 的核心是「一次回传 = 一个事务」。真实缺口是
`cpq_auth._connect()` 的 `autocommit=True` + 多处 `_connect()／commit`，只有**真跑**
那些语句才能观察到"失败后数据是否留下"。所以这里不是一个 mock，而是一个能执行
INSERT／UPDATE／SELECT（含 JOIN、布尔 WHERE、`->>`、`RETURNING`、`ON CONFLICT`）
的最小 SQL 引擎，并额外提供：

  · 事务语义：语句立即生效，但同时记录撤销日志；`rollback()` / 异常退出 `transaction()`
    逐条撤销**本连接自己**的写（并发安全，不会误撤别的连接的提交）；
    `autocommit=False` 的连接在 `close()` 时回滚未提交的写 —— 与 psycopg 真实行为一致
    （`_connection_base.py:537-545`：autocommit 连接上 `with conn.transaction():` 不发 BEGIN）。
  · 唯一约束：`cpq_wf_task(card_id, task_kind) WHERE status='open'`（批次 2）与
    `cpq_wf_handoff(handoff_key)`（批次 3）。
  · 故障注入：按 SQL 片段在第 N 次命中时抛错，用来复现"事务中间异常"。
  · 写日志：记录每条 INSERT/UPDATE 是否发生在事务里，用来断言"没有裸写的副作用"。

引擎主体复用 `tests/test_quote_task_coexistence_and_atomic_claim_red.py` 里的 `FakeDB`
（批次 2 的受控假库），本文件只补上面四项能力。**不要**把这里的断言复制回去，
两个红测各自独立；批次 2 的假库若被改动，本文件的 `selfcheck()` 会先失败并指出原因。
"""
from __future__ import annotations

import copy
import importlib.util
import pathlib
import re
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parents[2]
B2_PATH = ROOT / "tests" / "test_quote_task_coexistence_and_atomic_claim_red.py"

# 无论调用方从哪里启动，仓库根都必须可导入：本文件要 import cpq_auth / cpq_wf /
# cpq_tech_bridge，批次 2 的假库也要 import 它们。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LOAD_ERROR = ""
B2 = None


def _load_engine():
    spec = importlib.util.spec_from_file_location("wf_handoff_b2_engine", B2_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


try:
    B2 = _load_engine()
except Exception as exc:                                   # pragma: no cover - 环境问题
    LOAD_ERROR = (f"批次 2 的受控假库不可用（{B2_PATH.name}）："
                  f"{type(exc).__name__}: {exc}")


if B2 is not None:
    import cpq_auth  # noqa: E402
    import cpq_wf  # noqa: E402
    import cpq_tech_bridge  # noqa: E402

    UnsupportedSql = B2.UnsupportedSql
    FakeUniqueViolation = B2.FakeUniqueViolation

    # ---------------------------------------------------------------------
    # SQL 形态的小工具
    # ---------------------------------------------------------------------
    ON_CONFLICT_RE = re.compile(
        r"\s+ON\s+CONFLICT\b(?P<target>\s*\([^)]*\))?\s+DO\s+(?P<action>NOTHING|UPDATE\b.*)$",
        re.I | re.S)
    RETURNING_RE = re.compile(r"\s+RETURNING\s+(?P<cols>[A-Za-z_][\w.\s,]*)$", re.I)
    INSERT_HEAD_RE = re.compile(r"^INSERT\s+INTO\s+(?P<table>[A-Za-z_][\w.]*)", re.I)
    VALUES_RE = re.compile(r"^INSERT\s+INTO\s+(?P<head>[^(]*)\("
                           r"(?P<cols>[^)]*)\)\s*VALUES\s*\((?P<vals>.*)\)\s*$", re.I | re.S)

    # 新表的列顺序（`alias.*` 展开与 `_scope` 用）
    B2.TABLE_COLUMNS["cpq_wf_handoff"] = (
        "handoff_id", "handoff_key", "handoff_kind", "source_project_id", "source_task_id",
        "source_result_version", "target_quote_session_id", "target_card_id",
        "target_task_id", "target_task_kind", "step_no", "snapshot_sections",
        "source_task_closed", "source_task_status", "created_by_user_id", "created_at",
    )


    def _split_returning(text: str):
        m = RETURNING_RE.search(text)
        if not m:
            return text, ""
        return text[:m.start()].strip(), m.group("cols").strip()


    def _strip_on_conflict(text: str):
        m = ON_CONFLICT_RE.search(text)
        if not m:
            return text, ""
        action = "nothing" if m.group("action").upper().startswith("NOTHING") else "update"
        return text[:m.start()].strip(), action


    # 批次 2 的记号器不认识 `->>`；这里补一组（放在最前面，避免与 `-` 开头的数字混淆）
    TOKEN = re.compile(r"""
          (?P<ws>\s+)
        | (?P<json>->>\s*'(?:[^']|'')*')
        | (?P<ph>%s(?:::[A-Za-z_]+)?)
        | (?P<str>'(?:[^']|'')*')
        | (?P<num>-?\d+(?:\.\d+)?)
        | (?P<name>[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?)
        | (?P<op><>|<=|>=|=|<|\(|\)|,)
    """, re.X)
    B2._TOKEN = TOKEN


    class _JsonRef(B2._Ref):
        """`payload->>'handoff_key'` 这类 JSON 取值。"""

        def __init__(self, name, key):
            super().__init__(name)
            self.key = key


    def _json_extract(value, key):
        if value is None:
            return None
        if isinstance(value, str):
            try:
                import json
                value = json.loads(value)
            except Exception:
                return None
        if isinstance(value, dict):
            return value.get(key)
        return None


    def ref_value(scope, ref):
        if isinstance(ref, _JsonRef):
            base = scope.get(ref.name)
            if base is None:
                hits = [v for k, v in scope.items() if k.endswith("." + ref.name)]
                base = hits[0] if len(hits) == 1 else None
            return _json_extract(base, ref.key)
        return ORIG_REF_VALUE(scope, ref)


    ORIG_REF_VALUE = B2._ref_value
    B2._ref_value = ref_value


    class WhereParser(B2.WhereParser):
        """在批次 2 的 WHERE 解析器上补 `col ->> 'key'`。"""

        def parse_cmp(self):
            kind, text, _ = self.peek()
            if kind == "name":
                # 先看是否是 `col ->> 'key'`
                save = self.i
                self.take()
                lhs = B2._Ref(text)
                nk, nt, _ = self.peek()
                if nk == "json":
                    self.take()
                    matched = re.match(r"->>\s*'(?P<key>.*)'$", nt.strip(), re.S)
                    if not matched:
                        raise UnsupportedSql("JSON 取值解析失败：" + nt)
                    key = matched.group("key").replace("''", "'")
                    lhs = _JsonRef(text, key)
                    nk, nt, _ = self.peek()
                    if nk != "op" or nt not in ("=", "<>", ">", "<", ">=", "<="):
                        raise UnsupportedSql(f"假库不支持的比较：{text} {nt}")
                    self.take()
                    rk, rt, _ = self.peek()
                    if rk == "name":
                        raise UnsupportedSql("JSON 取值不接受列比较")
                    return B2._Cmp(nt, lhs, self.parse_value())
                self.i = save
            return super().parse_cmp()


    # 引擎内部（`FakeDB._select` / `_from_rows` / `_insert`）用的是自己模块里的
    # `WhereParser` 全局名 —— 换掉它，`->>` 才真的会走上面这条分支。
    B2.WhereParser = WhereParser


    class TxCursor(B2.FakeCursor):
        def execute(self, sql, args=()):
            conn = self.conn
            if conn.closed:
                raise RuntimeError("connection already closed")
            if conn.aborted and conn.tracks():
                # 真实 PG：事务被中止后，除 ROLLBACK 外的语句一律报
                # "current transaction is aborted"。
                raise RuntimeError(
                    "current transaction is aborted, commands ignored until end of "
                    "transaction block（事务已中止，不能继续写入）")
            written = conn.db.bump_write(sql)
            if written is not None and conn.db.fail_write_index == written:
                raise RuntimeError(f"假库注入故障：第 {written} 条写语句失败")
            # 「一条语句」在数据库里是原子的：快照→执行→记账整段持锁，别的线程不会
            # 插进来把它的写入算到本连接头上（否则并发下撤销日志会误删对方的行）。
            # Gate 的对齐点被挪到解锁之后 —— 持锁等待等于死锁。
            gate, conn.db.gate = conn.db.gate, None
            try:
                with conn.db.lock:
                    snapshot = conn.begin_write() if conn.tracks() else None
                    try:
                        conn.db.raise_if_injected(sql)
                        self.rows, self.rowcount = conn.db.run(sql, args)
                    except Exception:
                        if snapshot is not None:
                            conn.finish_write(snapshot)
                            conn.aborted = True
                        raise
                    if snapshot is not None:
                        conn.finish_write(snapshot)
                    conn.db.note_write(sql, conn.protected())
            finally:
                conn.db.gate = gate
            if gate:
                gate.maybe_wait(B2.norm_sql(sql))
            return None


    class _TxBlock:
        """等价 psycopg 的 `with conn.transaction():`（成功提交 / 异常回滚 / 可 force_rollback）。"""

        def __init__(self, conn, force_rollback=False):
            self.conn = conn
            self.force_rollback = force_rollback

        def __enter__(self):
            self.conn.stack.append([])
            return self

        def __exit__(self, exc_type, exc, tb):
            journal = self.conn.stack.pop()
            if exc is not None or self.force_rollback:
                self.conn.undo(journal)
                self.conn.aborted = False
            return False


    class TxConn(B2.FakeConn):
        """带事务与撤销日志的连接。"""

        def __init__(self, db, name="", autocommit=True):
            super().__init__(db, name)
            self._autocommit = bool(autocommit)
            self.stack = [[]]          # stack[-1] 是当前写入的撤销日志
            self.aborted = False

        # -- 事务状态 -----------------------------------------------------
        def tracks(self):
            return (not self._autocommit) or len(self.stack) > 1

        def protected(self):
            return self.tracks()

        def begin_write(self):
            return {name: [dict(row) for row in rows] for name, rows in self.db.tables.items()}

        def finish_write(self, snapshot):
            rows_now = self.db.tables
            journal = self.stack[-1]
            for name, rows in rows_now.items():
                old = snapshot.get(name, [])
                for index in range(min(len(rows), len(old))):
                    if rows[index] != old[index]:
                        journal.append(("restore", name, dict(old[index]), dict(rows[index])))
                for index in range(len(old), len(rows)):
                    journal.append(("drop", name, copy.deepcopy(rows[index]), None))

        def undo(self, journal):
            with self.db.lock:
                for op, table, first, second in reversed(journal):
                    rows = self.db.table(table)
                    if op == "drop":
                        for index, row in enumerate(rows):
                            if row == first:
                                del rows[index]
                                break
                    else:
                        for index, row in enumerate(rows):
                            if row == second:
                                rows[index] = dict(first)
                                break

        @property
        def autocommit(self):
            return self._autocommit

        @autocommit.setter
        def autocommit(self, value):
            self._autocommit = bool(value)

        def transaction(self, *args, **kwargs):
            return _TxBlock(self, force_rollback=bool(kwargs.get("force_rollback")))

        def commit(self):
            if self.aborted:
                self.undo(self.stack[-1])
                self.stack[-1] = []
                self.aborted = False
                raise RuntimeError("cannot commit - the transaction is aborted（事务已中止，"
                                   "COMMIT 退化为 ROLLBACK）")
            self.stack[-1].clear()
            self.aborted = False

        def rollback(self):
            self.undo(self.stack[-1])
            self.stack[-1] = []
            self.aborted = False

        def close(self):
            if not self._autocommit:
                self.undo(self.stack[0])
            self.stack[0] = []
            self.closed = True

        def cursor(self):
            return TxCursor(self)


    class FakeDB(B2.FakeDB):
        """批次 2 假库 + 唯一约束 / 故障注入 / 写日志 / 事务。"""

        def __init__(self, gate=None):
            super().__init__(gate=gate)
            self.faults = []
            self.write_log = []
            self.writes_seen = 0
            self.fail_write_index = None
            # 语句级原子性（等价于数据库内部对单条语句的串行化）
            self.lock = threading.RLock()

        # -- 写语句计数（按"第几条写"注入故障）-----------------------------
        @staticmethod
        def write_head(sql):
            head = " ".join(str(sql).split()).split(None, 1)[0].upper()
            return head if head in ("INSERT", "UPDATE", "DELETE") else ""

        def bump_write(self, sql):
            if not self.write_head(sql):
                return None
            self.writes_seen += 1
            return self.writes_seen

        def fail_on_write(self, index):
            self.fail_write_index = int(index)

        # -- 故障注入 -----------------------------------------------------
        def fail_on(self, needle, times=1, exc=None):
            self.faults.append({"needle": needle, "times": int(times), "exc": exc})

        def raise_if_injected(self, sql):
            text = " ".join(str(sql).split())
            for fault in self.faults:
                if fault["times"] > 0 and fault["needle"] in text:
                    fault["times"] -= 1
                    raise (fault["exc"] or RuntimeError(
                        f"假库注入故障（命中 {fault['needle']!r}）：模拟服务/网络中断"))

        def note_write(self, sql, protected):
            if self.write_head(sql):
                self.write_log.append((protected, " ".join(str(sql).split())[:120]))

        # -- SELECT：补 set_config 与 LIMIT ---------------------------------
        def _select(self, text, a):
            if "set_config" in text:
                value = a.take() if "%s" in text else None
                return [(value,)], 1
            text, limit, limit_is_arg = _split_limit(text)
            rows, count = super()._select(text, a)
            if limit is not None:
                value = int(a.take()) if limit_is_arg else limit
                rows = rows[:value]
                count = len(rows)
            return rows, count

        # -- INSERT：补 RETURNING 与 ON CONFLICT ---------------------------
        def _insert(self, text, a):
            text, ret = _split_returning(text)
            text, conflict = _strip_on_conflict(text)
            head = INSERT_HEAD_RE.match(text)
            if not head:
                raise UnsupportedSql("INSERT 解析失败：" + text[:120])
            alias = None
            table = head.group("table").split(".")[-1]
            if table == "cpq_wf_handoff":
                rows, count = self._insert_handoff(text, a, conflict)
            else:
                before = len(self.table(table))
                try:
                    rows, count = super()._insert(text, a)
                except FakeUniqueViolation:
                    if conflict == "nothing":
                        return [], 0
                    raise
                rows = self._inserted_rows(table, before, ret, alias)
                return rows, count
            if ret and count and rows:
                return [self._project(self._scope("cpq_wf_handoff", "cpq_wf_handoff", row),
                                      ret) for row in rows], count
            return rows, count

        # -- UPDATE：补 `col = COALESCE(col, %s)` ---------------------------
        COALESCE_RE = re.compile(
            r"^COALESCE\s*\(\s*(?P<col>[A-Za-z_][\w.]*)\s*,\s*(?P<rest>.+)\)$", re.I | re.S)

        def _update(self, text, a):
            set_idx = B2._find_top(text, "SET")
            if set_idx < 0:
                raise UnsupportedSql("UPDATE 没有 SET：" + text[:80])
            table_expr = text[len("UPDATE"):set_idx].strip()
            table, alias = B2._strip_alias(table_expr)
            rest = text[set_idx + len("SET"):]
            where_idx = B2._find_top(rest, "WHERE")
            ret_idx = B2._find_top(rest, "RETURNING")
            cuts = [i for i in (where_idx, ret_idx) if i >= 0] or [len(rest)]
            set_text = rest[:min(cuts)].strip()
            where_text = ""
            if where_idx >= 0:
                end = ret_idx if ret_idx > where_idx else len(rest)
                where_text = rest[where_idx + len("WHERE"):end].strip()
            ret_text = rest[ret_idx + len("RETURNING"):].strip() if ret_idx >= 0 else ""
            assigns = []
            for piece in B2._split_top(set_text):
                m = re.match(r"^([A-Za-z_][\w.]*)\s*=\s*(.+)$", piece.strip(), re.S)
                if not m:
                    raise UnsupportedSql("SET 解析失败：" + piece)
                target = m.group(1).split(".")[-1]
                value_text = m.group(2).strip()
                coalesce = self.COALESCE_RE.match(value_text)
                if coalesce:
                    source = coalesce.group("col").split(".")[-1]
                    assigns.append((target, ("coalesce", source,
                                             WhereParser(coalesce.group("rest").strip(),
                                                         a).parse_value())))
                else:
                    assigns.append((target, WhereParser(value_text, a).parse_value()))
            where_node = WhereParser(where_text, a).parse() if where_text else None
            hit = []
            for row in self.table(table):
                scope = self._scope(table, alias, row)
                if where_node is not None and not where_node.eval(scope):
                    continue
                for col, val in assigns:
                    if isinstance(val, tuple) and val[0] == "coalesce":
                        _, source, fresh = val
                        if row.get(source) is None:
                            row[col] = fresh
                    else:
                        row[col] = val
                hit.append(self._scope(table, alias, row))
            rows = []
            if ret_text:
                rows = [self._project(s, ret_text, {alias: table}) for s in hit]
            return rows, len(hit)

        def _inserted_rows(self, table, before, ret, alias):
            if not ret:
                return []
            inserted = self.table(table)[before:]
            out = []
            for row in inserted:
                scope = self._scope(table, table, row)
                scope.update(self._scope(table, alias or table, row))
                out.append(self._project(scope, ret, {table: table}))
            return out

        def _insert_handoff(self, text, a, conflict):
            m = VALUES_RE.match(text)
            if not m:
                raise UnsupportedSql("cpq_wf_handoff 的 INSERT 解析失败：" + text[:160])
            cols = [c.strip() for c in m.group("cols").split(",")]
            values = [WhereParser(v, a).parse_value() for v in B2._split_top(m.group("vals"))]
            if len(cols) != len(values):
                raise UnsupportedSql("INSERT 列数与值数不一致")
            row = dict(zip(cols, values))
            key = row.get("handoff_key")
            for other in self.table("cpq_wf_handoff"):
                if key is not None and other.get("handoff_key") == key:
                    if conflict == "nothing":
                        return [], 0
                    if conflict == "update":
                        raise UnsupportedSql(
                            "假库不支持 ON CONFLICT DO UPDATE：请在 DO NOTHING + 重读、"
                            "或捕获唯一冲突 + 重读之间二选一")
                    raise FakeUniqueViolation(
                        'duplicate key value violates unique constraint "uq_wf_handoff_key"'
                        " —— 同一把回传幂等键只能有一条记录")
            self.table("cpq_wf_handoff").append(row)
            return [row], 1


    LIMIT_RE = re.compile(r"\s+LIMIT\s+(?P<n>\d+|%s)\s*$", re.I)


    def _split_limit(text):
        m = LIMIT_RE.search(text)
        if not m:
            return text, None, False
        raw = m.group("n")
        if raw == "%s":
            return text[:m.start()].strip(), 0, True
        return text[:m.start()].strip(), int(raw), False


    class Patch:
        """把 `cpq_auth._connect` / `cpq_db.connect` / `psycopg.connect` 换成假库连接。

        真实库一律不连：测试绝不允许碰线上 PG。默认 autocommit=True（与今天一致），
        只有被测代码显式传 `autocommit=False` 时才是事务连接。
        """

        def __init__(self, db):
            self.db = db
            self.conns = []
            self._orig = []
            self._saved = []

        def connect(self, *args, **kwargs):
            autocommit = kwargs.get("autocommit", True)
            if args and isinstance(args[0], bool):
                autocommit = args[0]
            conn = TxConn(self.db, name=f"conn{len(self.conns) + 1}", autocommit=autocommit)
            self.conns.append(conn)
            return conn

        def __enter__(self):
            import cpq_db
            import psycopg
            self._orig = [(cpq_auth, "_connect", cpq_auth._connect),
                          (cpq_db, "connect", cpq_db.connect),
                          (psycopg, "connect", psycopg.connect)]
            cpq_auth._connect = self.connect
            cpq_db.connect = self.connect
            psycopg.connect = self.connect
            return self

        def __exit__(self, *exc):
            for module, name, original in self._orig:
                setattr(module, name, original)
            return False


    class HistoryStub:
        """拦掉报价助手的历史文件读写：测试绝不写仓库里的 cpq_history。"""

        def __init__(self):
            self.saved = []
            self._orig = None

        def __enter__(self):
            import cpq_agent_server
            self._orig = (cpq_agent_server.load_history, cpq_agent_server.save_history)
            cpq_agent_server.load_history = lambda sid: None
            cpq_agent_server.save_history = lambda session: self.saved.append(session)
            return self

        def __exit__(self, *exc):
            import cpq_agent_server
            cpq_agent_server.load_history, cpq_agent_server.save_history = self._orig
            return False


    # ---------------------------------------------------------------------
    # 场景
    # ---------------------------------------------------------------------
    SALES_UID = 100
    PROC_UID = 200
    FIN_UID = 400
    OTHER_FIN_UID = 410

    TECH_PROJECT = "techproj-3f2a1b"
    QUOTE_SESSION = "qsess-9a8b7c"
    CARD_ID = 1

    _NAMES = {
        SALES_UID: ("sales1", "SM1", "sales_mgr"),
        PROC_UID: ("proc1", "PM1", "process_mgr"),
        FIN_UID: ("fin1", "FI1", "finance_mgr"),
        OTHER_FIN_UID: ("fin2", "FI2", "finance_mgr"),
    }


    def user_dict(uid):
        username, display_name, role_code = _NAMES[uid]
        return {"user_id": str(uid), "username": username, "display_name": display_name,
                "role_code": role_code, "role_name": cpq_auth.ROLES.get(role_code, role_code),
                "status": "active"}


    def cost_result(**over):
        """一份典型的技术结果（成本路径）。字段名与 cost_flow.integration_quote_result 对齐。"""
        result = {
            "project_id": TECH_PROJECT,
            "tech_project_id": TECH_PROJECT,
            "quote_session_id": QUOTE_SESSION,
            "source_session_id": QUOTE_SESSION,
            "handoff_kind": "cost_to_quote",
            "result_version": "cost-v1:1:123.45",
            "product_name": "便携式锂电池 PACK",
            "quantity": 1,
            "params": {
                "assembly_name": "便携式锂电池 PACK",
                "summary": {"required_total": 3, "required_filled": 3},
                "fields": [{"code": "product_item_code", "name": "成品编码", "value": "92022001"},
                           {"code": "rated_voltage", "name": "额定电压", "value": "12.8"}],
            },
            "process": {"route": "RT-PLATE-MACHINED", "steps": [
                {"no": 1, "name": "来料检验与配组", "equipment": "检验台、量具", "hours": 0.07}]},
            "parts": [{"part_id": "P-001", "name": "上壳", "amount": 12.5},
                      {"part_id": "P-002", "name": "下壳", "amount": 11.0}],
            "part_costs": [{"part_id": "P-001", "name": "上壳", "amount": 12.5},
                           {"part_id": "P-002", "name": "下壳", "amount": 11.0}],
            "assembly": {"material": 0.71, "labor": 0.06, "overhead": 0.03, "process": 0.02,
                         "total": 0.82},
            "assembly_cost": {"total": 0.82},
            "cost": {"total": 123.45, "material": 100.0, "labor": 10.0},
            "cost_breakdown": {"total": 123.45},
            "final": {"total": 123.45},
            "cost_confirmation": {"confirmed": True, "confirmed_by": "fin1"},
            "material": {"number": "92022001", "name": "便携式锂电池 PACK",
                         "unit_price": 123.45},
        }
        result.update(over)
        return result


    def report_package(**over):
        """一份典型的已发布报告包（字段与 report_workflow.report_package 对齐）。"""
        package = {
            "report_no": "RPT-20260917-001",
            "version": 2,
            "title": "便携式锂电池 PACK 工艺评估报告",
            "status": "published",
            "reviewed_by": "proc1", "reviewed_at": "2026-09-17T10:20:00",
            "review_note": "同意发布",
            "published_by": "proc1", "published_at": "2026-09-17T10:30:00",
            "distribution_scope": "销售、工艺、财务", "distribution_cc": "质量",
            "summary": "整机工艺可行", "conclusion": "建议按第 3 步继续报价",
            "risks": ["电芯来料一致性"], "highlights": ["自制件全部可加工"],
            "attachments": [{"name": "工艺卡.pdf", "source": "系统导出"}],
            "report_url": f"tech-workbench.html?stage=summary&project={TECH_PROJECT}",
            "pdf_url": f"/api/projects/{TECH_PROJECT}/process-report/export.pdf",
            "recipients": [{"name": "销售经理"}],
        }
        package.update(over)
        return package


    class Workbench:
        """一张原报价卡片 + 一个技术项目 + 一条已被领取的来源任务。"""

        def __init__(self, *, current_step=1, overall_status="awaiting_handoff",
                     source_task_kind=cpq_wf.TASK_KIND_TECH_COST, source_task_status="claimed",
                     source_claimed_by=FIN_UID, with_source_task=True, gate=None,
                     step2_snapshot=None):
            self.db = FakeDB(gate=gate)
            self.patch = Patch(self.db)
            self.history = HistoryStub()
            self.current_step = current_step
            self.overall_status = overall_status
            self.source_task_kind = source_task_kind
            self.source_task_status = source_task_status
            self.source_claimed_by = source_claimed_by
            self.with_source_task = with_source_task
            # 不能直接叫 step2_snapshot：会盖住下面那个同名观测方法。
            self.initial_step2_snapshot = step2_snapshot
            self._seed()

        # -- 生命周期 ------------------------------------------------------
        def __enter__(self):
            self.patch.__enter__()
            self.history.__enter__()
            return self

        def __exit__(self, *exc):
            self.history.__exit__(*exc)
            return self.patch.__exit__(*exc)

        def _seed(self):
            for uid, (username, display_name, role_code) in _NAMES.items():
                self.db.seed("cpq_wf_user", user_id=uid, username=username,
                             display_name=display_name, role_code=role_code, status="active")
            self.db.seed("cpq_wf_card", card_id=CARD_ID, session_id=QUOTE_SESSION,
                         assistant_type="quote", title="便携式锂电池 PACK", customer="客户A",
                         project_name="锂电池项目", current_step=int(self.current_step),
                         overall_status=self.overall_status, creator_user_id=SALES_UID,
                         current_owner=SALES_UID, created_at="2026-09-17T10:00:00",
                         updated_at="2026-09-17T10:00:00")
            for step_no, name, role in cpq_wf.QUOTE_STEPS:
                self.db.seed("cpq_wf_step_perm", id=step_no, assistant_type="quote",
                             step_no=step_no, step_name=name, role_code=role)
                done = step_no < int(self.current_step)
                self.db.seed("cpq_wf_card_step", card_step_id=1000 + step_no, card_id=CARD_ID,
                             step_no=step_no, step_name=name, role_code=role,
                             status="done" if done else "pending", owner_user_id=None,
                             data_snapshot=(self.initial_step2_snapshot
                                            if step_no == 2 and self.initial_step2_snapshot
                                            else None),
                             started_at=None,
                             completed_at="2026-09-17T10:05:00" if done else None)
            if self.with_source_task:
                self.db.seed("cpq_wf_task", task_id=9001, card_id=CARD_ID,
                             from_user_id=PROC_UID, from_step_no=2, target_type="role",
                             target_role_code="finance_mgr", target_user_id=None,
                             claimed_by_user_id=(self.source_claimed_by
                                                 if self.source_task_status == "claimed" else None),
                             status=self.source_task_status, source_label="工艺经理·PM1 发起「成本测算」",
                             note="请做成本测算", created_at="2026-09-17T10:06:00",
                             claimed_at=("2026-09-17T10:07:00"
                                         if self.source_task_status == "claimed" else None),
                             completed_at=("2026-09-17T11:00:00"
                                           if self.source_task_status == "completed" else None),
                             task_kind=self.source_task_kind, payload=None,
                             supersedes_task_id=None, replaced_by_task_id=None,
                             cancel_reason=None, cancelled_at=None)

        # -- 调用 ----------------------------------------------------------
        def user(self, uid):
            return user_dict(uid)

        def handoff(self, kind="cost_to_quote", uid=FIN_UID, *, result_version=None,
                    source_task_id="9001", result=None, report=None,
                    source_session_id=QUOTE_SESSION, tech_project=TECH_PROJECT,
                    note="已确认，请继续报价"):
            return cpq_tech_bridge.send_to_quote(
                self.user(uid), tech_project, "便携式锂电池 PACK", "客户A", "锂电池项目",
                note, str(source_task_id or ""), result if result is not None else cost_result(),
                source_session_id, report, kind,
                result_version if result_version is not None
                else ("report-v2" if kind == "report_to_quote" else "cost-v1:1:123.45"))

        def return_process(self, uid=FIN_UID, *, source_task_id="9001",
                           result_version="cost-v1:1:123.45", tech_project=TECH_PROJECT,
                           note="成本已确认，请做最终工艺确认与报告",
                           payload=None, target_user_id=""):
            package = dict(payload or {})
            package.setdefault("result_version", result_version)
            package.setdefault("quote_session_id", QUOTE_SESSION)
            package.setdefault("source_session_id", QUOTE_SESSION)
            package.setdefault("source_task_id", str(source_task_id or ""))
            package.setdefault("tech_project_id", tech_project)
            return cpq_tech_bridge.return_to_process(
                self.user(uid), tech_project, "便携式锂电池 PACK", "客户A", "锂电池项目",
                note, package, str(target_user_id or ""), str(source_task_id or ""))

        # -- 观察 ----------------------------------------------------------
        def rows(self, table, **where):
            out = []
            for row in self.db.table(table):
                if all(row.get(k) == v for k, v in where.items()):
                    out.append(row)
            return out

        def cards(self, **where):
            return self.rows("cpq_wf_card", **where)

        def tasks(self, **where):
            return self.rows("cpq_wf_task", **where)

        def events(self, **where):
            return self.rows("cpq_wf_task_event", **where)

        def messages(self, **where):
            return self.rows("cpq_wf_message", **where)

        def handoffs(self, **where):
            return self.rows("cpq_wf_handoff", **where)

        def card(self):
            rows = self.cards(card_id=CARD_ID)
            return rows[0] if rows else None

        def source_task(self):
            rows = self.tasks(task_id=9001)
            return rows[0] if rows else None

        def deliverables(self, card_id=CARD_ID, kind="handoff"):
            """卡片上"交给下一步"的那条任务（不含来源支线任务）。"""
            return [t for t in self.tasks(card_id=card_id, task_kind=kind)
                    if t.get("task_id") != 9001]

        def step2_snapshot(self):
            rows = self.rows("cpq_wf_card_step", card_id=CARD_ID, step_no=2)
            if not rows:
                return None
            raw = rows[0].get("data_snapshot")
            if isinstance(raw, str):
                import json
                try:
                    return json.loads(raw)
                except ValueError:
                    return None
            return raw

        def unprotected_writes(self):
            return [sql for protected, sql in self.db.write_log if not protected]


    def selfcheck():
        """假库自身是否可用（事务能回滚、唯一约束会拦、故障注入会生效）。"""
        db = FakeDB()
        conn = TxConn(db, autocommit=False)
        cur = conn.cursor()
        cur.execute("INSERT INTO cpq_wf_card (card_id, session_id) VALUES (%s,%s)", (1, "s"))
        conn.rollback()
        assert db.table("cpq_wf_card") == [], "autocommit=False + rollback 必须撤销写入"
        cur.execute("INSERT INTO cpq_wf_card (card_id, session_id) VALUES (%s,%s)", (1, "s"))
        conn.commit()
        assert len(db.table("cpq_wf_card")) == 1, "commit 后必须留下写入"
        cur.execute("INSERT INTO cpq_wf_handoff (handoff_id, handoff_key) VALUES (%s,%s)", (1, "k"))
        try:
            cur.execute("INSERT INTO cpq_wf_handoff (handoff_id, handoff_key) VALUES (%s,%s)",
                        (2, "k"))
        except FakeUniqueViolation:
            pass
        else:
            raise AssertionError("cpq_wf_handoff.handoff_key 的唯一约束没有被执行")
        db.fail_on("cpq_wf_message")
        try:
            cur.execute("INSERT INTO cpq_wf_message (message_id) VALUES (%s)", (9,))
        except RuntimeError:
            pass
        else:
            raise AssertionError("故障注入没有生效")
        return True


    SELFCHECK_OK = False
    SELFCHECK_ERROR = ""
    try:
        SELFCHECK_OK = selfcheck()
    except Exception as exc:                                # pragma: no cover
        SELFCHECK_ERROR = f"{type(exc).__name__}: {exc}"

# -*- coding: utf-8 -*-
"""批次 4 红测共用的受控假库：主数据 / 成本配置 / 写入幂等表（真跑 SQL）。

为什么需要它：批次 4 的核心是「写主数据必须是一个事务 + 同一业务版本只能取一次号」。
真实缺口是 `cpq_db.connect(readonly=False)` 的 `autocommit=True`（主数据 INSERT 与成本表
INSERT 各自独立提交）与「先 SELECT 取号再 INSERT」的竞争。只有**真跑**这些语句、
并把"两张表是否在同一事务里""并发时会不会取到同一个号"观察下来，才能证明缺口。

为此本文件在批次 3 的受控假库（`tests/fixtures/wf_handoff_harness.py`，它又复用批次 2 的
最小 SQL 引擎）之上补四件事：

  · 主数据 / 成本配置 / `cpq_wf_material_write` 三张表的结构与**唯一索引**：
    `md_clm_material_base_info.number` 唯一、`cpq_wf_material_write` 的
    `(project_id, result_version, action_type)` 唯一 —— 与 Spec 的 DDL 要求一致。
    INSERT 撞唯一键时默认抛 `FakeUniqueViolation`（等价 PG）；只有 `ON CONFLICT DO NOTHING`
    才是"安静地插不进去"。
  · `pg_advisory_xact_lock` / `pg_advisory_lock` / `pg_try_advisory_xact_lock`：
    按 key 真的互斥，事务结束（commit / rollback / close）释放 —— 取号到底有没有用
    数据库级锁，跑一次就知道。
  · 并发对齐点挪到**语句执行之前**：先拿到锁的线程若在"执行之后"对齐，另一个线程正卡在
    锁上，两边都到不齐（死锁）。批次 2/3 的对齐点在语句之后，这里必须放到之前。
  · `LIKE` / `NOT LIKE` 与 `now()` / `snow_next_id()` 这类取值：`_next_code` 的
    `WHERE number LIKE '92022%'` 必须能跑。
  · DDL 容忍：`CREATE TABLE IF NOT EXISTS` / `CREATE UNIQUE INDEX IF NOT EXISTS` /
    `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 记进 `db.ddl` 而不是报错。

`cpq_db.table_types` 被换成假库的结构注册表（真实实现里它查 `information_schema`）：
**只影响"表结构从哪来"，不影响 INSERT/UPDATE/SELECT 的真执行**。表名不在注册表里时，
`insert_rows` 会照真相那样报「目标表不存在」，并附上假库登记过的表名。

绝不对真实 Postgres 发起任何连接：`cpq_auth._connect` / `cpq_db.connect` / `psycopg.connect`
全部被换成假库连接。自检（`selfcheck()`）会先证明这些能力真的生效。
"""
from __future__ import annotations

import importlib.util
import inspect
import pathlib
import re
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parents[2]
H_PATH = ROOT / "tests" / "fixtures" / "wf_handoff_harness.py"
for _path in (str(ROOT), str(ROOT / "tests" / "fixtures")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

LOAD_ERROR = ""
H = None


def _load_engine():
    spec = importlib.util.spec_from_file_location("material_write_b3_engine", H_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


try:
    H = _load_engine()
    if H.LOAD_ERROR:
        LOAD_ERROR = H.LOAD_ERROR
except Exception as exc:                                     # pragma: no cover - 环境问题
    LOAD_ERROR = f"批次 3 的受控假库不可用（{H_PATH.name}）：{type(exc).__name__}: {exc}"


if H is not None and not LOAD_ERROR:
    import cpq_auth  # noqa: E402
    import cpq_db  # noqa: E402
    import cpq_tech_bridge  # noqa: E402

    B2 = H.B2
    UnsupportedSql = H.UnsupportedSql
    FakeUniqueViolation = H.FakeUniqueViolation

    # 真实表名从被测模块取：Spec 约定的就是这两个常量，不从测试里另抄一份。
    BASE_TABLE = cpq_tech_bridge.BASE_TABLE
    COST_TABLE = cpq_tech_bridge.COST_TABLE
    IDEM_TABLE = "cpq_wf_material_write"
    ACTION_TYPE = "material-write"

    # 主数据表按业务库 DA 里与本流程相关的列登记（其余列没用到，登记了反而掩盖"插错列"）。
    COLUMN_TYPES = {
        BASE_TABLE: {
            "material_id": "bigint",
            "number": "character varying",
            "name": "character varying",
            "spec": "character varying",
        },
        COST_TABLE: {
            "md_clm_material_cost_cnf_id": "bigint",
            "material_id": "bigint",
            "material_code": "character varying",
            "material_name": "character varying",
            "material_unit_price": "numeric",
        },
        IDEM_TABLE: {
            "write_id": "bigint",
            "idempotency_key": "character varying",
            "project_id": "character varying",
            "result_version": "character varying",
            "action_type": "character varying",
            "material_id": "character varying",
            "number": "character varying",
            "name": "character varying",
            "unit_price": "numeric",
            "status": "character varying",
            "created_by_user_id": "bigint",
            "created_at": "timestamp with time zone",
        },
    }
    COLUMNS = {table: tuple(types) for table, types in COLUMN_TYPES.items()}
    for _table, _cols in COLUMNS.items():
        B2.TABLE_COLUMNS[_table] = _cols

    # 唯一索引：与 Spec 6.3 要求的一致。撞键时的行为由 `conflict` 决定（见 FakeDB._insert）。
    UNIQUE_INDEXES = {
        BASE_TABLE: [("number",)],
        IDEM_TABLE: [("project_id", "result_version", "action_type"),
                     ("idempotency_key",)],
    }

    NOW_TEXT = "2026-09-17T12:00:00+08:00"
    ZERO_ARG_FUNCS = ("now", "current_timestamp", "snow_next_id", "gen_random_uuid",
                      "uuid_generate_v4", "statement_timestamp", "clock_timestamp")

    # ---------------------------------------------------------------------
    # 解析器：补 LIKE / NOT LIKE 与零参函数取值
    # ---------------------------------------------------------------------
    class _Like(B2._Node):
        """`col LIKE %s` / `col NOT LIKE %s`（SQL 的 % / _ 通配）。"""

        def __init__(self, negated, ref, pattern):
            self.negated, self.ref, self.pattern = negated, ref, pattern

        def eval(self, scope):
            value = B2._ref_value(scope, self.ref)
            if value is None:
                return False
            regex = "".join(".*" if ch == "%" else "." if ch == "_" else re.escape(ch)
                            for ch in str(self.pattern))
            hit = re.fullmatch(regex, str(value), re.S) is not None
            return (not hit) if self.negated else hit

    class LikeWhereParser(H.WhereParser):
        """批次 3 的解析器 + `LIKE` + `now()` / `snow_next_id()` 取值。"""

        def parse_cmp(self):
            kind, text, _ = self.peek()
            if kind == "name":
                save = self.i
                self.take()
                ref = B2._Ref(text)
                negated = False
                nk, nt, _ = self.peek()
                if nk == "name" and nt.upper() == "NOT":
                    self.take()
                    nk, nt, _ = self.peek()
                    negated = True
                if nk == "name" and nt.upper() == "LIKE":
                    self.take()
                    return _Like(negated, ref, self.parse_value())
                self.i = save
            return super().parse_cmp()

        def parse_value(self):
            kind, text, _ = self.peek()
            if kind == "name" and text.lower() in ZERO_ARG_FUNCS:
                save = self.i
                self.take()
                nk, nt, _ = self.peek()
                if nk == "op" and nt == "(":
                    self.take()
                    nk, nt, _ = self.peek()
                    if nk == "op" and nt == ")":
                        self.take()
                        return NOW_TEXT
                self.i = save
            return super().parse_value()

    # 引擎内部（`_select` / `_from_rows` / `_insert` / `_update`）用的是自己模块里的
    # `WhereParser` 全局名 —— 换掉它，LIKE 才真的会走上面这条分支。
    B2.WhereParser = LikeWhereParser

    # ---------------------------------------------------------------------
    # 并发对齐：语句执行**之前**每个线程对齐一次
    # ---------------------------------------------------------------------
    class Gate:
        """受控交错：让并发的两个线程在**插入主数据那一条语句之前**对齐一次。

        为什么偏偏是这条语句：取号竞争的真实窗口是

            读完成品编码（两边都读到"还没人用"）→ 回查（两边都查不到）→ 各自 INSERT

        只有在 INSERT 之前把两个线程对齐，才能保证两边都已经走完"读 + 回查"再一起插入 ——
        冲突因此是必然的，而不是靠线程调度碰运气（GIL 下先跑的线程常常整段跑完，
        用例就会变成偶发通过）。对齐放在语句之前：若放在之后，先插入成功的线程会在对齐点
        上等另一个必然插入失败的线程，白等一轮超时。

        已经持有 advisory 锁的线程不参与对齐：那个实现本来就用数据库锁串行化了，
        让锁的持有者停下来等另一个正卡在锁上的线程，只会得到超时，而不是缺口。
        只拦名字以 `prefix` 开头的线程 —— 用例在主线程里做观察，不该被拦。
        """

        PATTERN = re.compile(r"INSERT\s+INTO\s+md_clm_material_base_info", re.I)

        def __init__(self, parties=2, timeout=15.0, prefix="race"):
            self.barrier = threading.Barrier(parties, timeout=timeout)
            self.prefix = prefix
            self.arrivals = 0
            self._seen = set()
            self._lock = threading.Lock()

        def before(self, sql, conn=None):
            if not self.PATTERN.search(sql or ""):
                return
            if getattr(conn, "advisory_held", None):
                return
            if not threading.current_thread().name.startswith(self.prefix):
                return
            ident = threading.get_ident()
            with self._lock:
                if ident in self._seen:
                    return
                self._seen.add(ident)
                self.arrivals += 1
                wait = self.arrivals <= self.barrier.parties
            if not wait:
                return
            try:
                self.barrier.wait()
            except threading.BrokenBarrierError:
                pass

        def maybe_wait(self, sql):
            """底层游标仍会调它；批次 4 的对齐由 `MaterialCursor.execute` 统一处理。"""
            return None


    # ---------------------------------------------------------------------
    # 连接 / 游标：事务 + advisory 锁归属
    # ---------------------------------------------------------------------
    class MaterialCursor(H.TxCursor):
        def execute(self, sql, args=()):
            conn = self.conn
            conn.db.active_conn = conn
            # 批次 3 的游标 execute 没把读取位置复位（它自己的用例每次都用新游标），
            # 真 psycopg 是复位的 —— 同一条游标执行两次再 fetchone 会拿到 None。
            self._pos = 0
            # 对齐在执行之前（见 Gate 的注释）。底层游标执行**之后**还会调一次
            # `gate.maybe_wait(...)` —— 本文件的 Gate 把它定义成空操作，所以不会重复对齐。
            # 注意：绝不能把 `db.gate` 临时置空来关掉内层调用 —— 它是全库共享属性，
            # 置空期间别的线程正好在执行语句，就等于把它们的对齐点也一起关了。
            gate = conn.db.gate
            if gate:
                gate.before(B2.norm_sql(sql), conn)
            return super().execute(sql, args)

    class MaterialConn(H.TxConn):
        """带事务、advisory 锁归属的连接。"""

        def __init__(self, db, name="", autocommit=True):
            super().__init__(db, name, autocommit=autocommit)
            self.advisory_held = {}

        def cursor(self):
            return MaterialCursor(self)

        def commit(self):
            try:
                super().commit()
            finally:
                self.db.release_advisory(self)

        def rollback(self):
            try:
                super().rollback()
            finally:
                self.db.release_advisory(self)

        def close(self):
            try:
                super().close()
            finally:
                self.db.release_advisory(self)

    ADV_RE = re.compile(
        r"^SELECT\s+(?P<fn>pg_advisory_xact_lock|pg_advisory_lock|pg_try_advisory_xact_lock"
        r"|pg_try_advisory_lock)\s*\(\s*(?P<arg>%s|\d+)\s*\)\s*$", re.I)
    VALUES_RE = re.compile(r"^INSERT\s+INTO\s+(?P<head>[^(]*)\("
                           r"(?P<cols>[^)]*)\)\s*VALUES\s*\((?P<vals>.*)\)\s*$", re.I | re.S)

    LITERAL_RE = re.compile(r"^(?:-?\d+(?:\.\d+)?|'(?:[^']|'')*')$")

    class FakeDB(H.FakeDB):
        """批次 3 假库 + 三张业务表的结构 / 唯一索引 / advisory 锁 / DDL 容忍。"""

        def __init__(self, gate=None):
            super().__init__(gate=gate)
            self.ddl = []
            self.advisory_calls = []
            self.active_conn = None
            self.advisory = {}
            self._advisory_guard = threading.Lock()
            self._advisory_locks = {}

        # -- SELECT 常量（`SELECT 1 FROM ... WHERE number = %s LIMIT 1`）------
        @staticmethod
        def _literal(item):
            if item.startswith("'"):
                return item[1:-1].replace("''", "'")
            return float(item) if "." in item else int(item)

        def _project(self, scope, select_list, alias_map=None):
            items = [item.strip() for item in B2._split_top(select_list)]
            if not any(LITERAL_RE.match(item) for item in items):
                return super()._project(scope, select_list, alias_map)
            out = []
            for item in items:
                if LITERAL_RE.match(item):
                    out.append(self._literal(item))
                else:
                    out.extend(super()._project(scope, item, alias_map))
            return tuple(out)

        # -- DDL 容忍 ------------------------------------------------------
        def run(self, sql, args):
            text = B2.norm_sql(sql)
            head = text.split(None, 1)[0].upper() if text else ""
            if head in ("CREATE", "ALTER", "GRANT", "COMMENT", "SET", "SAVEPOINT",
                        "RELEASE", "BEGIN", "COMMIT", "ROLLBACK"):
                self.ddl.append(text)
                return [], 0
            return super().run(sql, args)

        # -- advisory 锁 ---------------------------------------------------
        def _key_lock(self, key):
            with self._advisory_guard:
                lock = self._advisory_locks.get(key)
                if lock is None:
                    lock = threading.Lock()
                    self._advisory_locks[key] = lock
                return lock

        def acquire_advisory(self, conn, fn, key, blocking=True):
            self.advisory_calls.append({"fn": fn, "key": key, "blocking": bool(blocking),
                                        "conn": getattr(conn, "name", "")})
            if conn is None:                       # 没有活连接：当它没持有任何东西
                return False
            if key in getattr(conn, "advisory_held", {}):
                conn.advisory_held[key] += 1
                return True
            lock = self._key_lock(key)
            got = lock.acquire(True, 30) if blocking else lock.acquire(False)
            if not got:
                return False
            conn.advisory_held[key] = 1
            with self._advisory_guard:
                self.advisory[key] = conn
            return True

        def release_advisory(self, conn):
            held = dict(getattr(conn, "advisory_held", {}) or {})
            if not held:
                return
            conn.advisory_held = {}
            for key in held:
                with self._advisory_guard:
                    owner = self.advisory.pop(key, None)
                if owner is conn:
                    lock = self._advisory_locks.get(key)
                    if lock is not None and lock.locked():
                        try:
                            lock.release()
                        except RuntimeError:            # pragma: no cover - 防御
                            pass

        # -- SELECT：advisory 锁 -------------------------------------------
        def _select(self, text, a):
            m = ADV_RE.match(text)
            if m:
                raw = m.group("arg")
                key = a.take() if raw == "%s" else int(raw)
                fn = m.group("fn").lower()
                blocking = "try" not in fn
                ok = self.acquire_advisory(self.active_conn, fn, key, blocking=blocking)
                if "try" in fn:
                    return [(bool(ok),)], 1
                if not ok:                                # pragma: no cover - 不会走到
                    raise RuntimeError("假库：advisory 锁等待超时")
                return [(True,)], 1
            return super()._select(text, a)

        # -- 唯一索引 -------------------------------------------------------
        def unique_violation(self, table, row):
            """返回被撞的唯一索引名；没有冲突返回 ""。"""
            for cols in UNIQUE_INDEXES.get(table, ()):
                if any(row.get(col) is None for col in cols):
                    continue
                for other in self.table(table):
                    if all(other.get(col) == row.get(col) for col in cols):
                        return f"uq_{table}_{'_'.join(cols)}"
            return ""

        def _insert(self, text, a):
            stripped, ret = H._split_returning(text)
            stripped, conflict = H._strip_on_conflict(stripped)
            head = H.INSERT_HEAD_RE.match(stripped)
            if not head:
                return super()._insert(text, a)
            table = head.group("table").split(".")[-1]
            if table not in UNIQUE_INDEXES:
                return super()._insert(text, a)
            m = VALUES_RE.match(stripped)
            if not m:
                raise UnsupportedSql("主数据 INSERT 解析失败：" + stripped[:160])
            cols = [c.strip().split(".")[-1] for c in m.group("cols").split(",")]
            values = [B2.WhereParser(v, a).parse_value()
                      for v in B2._split_top(m.group("vals"))]
            if len(cols) != len(values):
                raise UnsupportedSql("INSERT 列数与值数不一致")
            row = dict(zip(cols, values))
            violated = self.unique_violation(table, row)
            if violated:
                if conflict == "nothing":
                    return [], 0
                if conflict == "update":
                    raise UnsupportedSql(
                        "假库不支持 ON CONFLICT DO UPDATE：请用 DO NOTHING + 重读")
                raise FakeUniqueViolation(
                    f'duplicate key value violates unique constraint "{violated}"')
            before = len(self.table(table))
            self.table(table).append(row)
            rows = self._inserted_rows(table, before, ret, None) if ret else []
            return rows, 1

    # ---------------------------------------------------------------------
    # 打桩：连接与表结构
    # ---------------------------------------------------------------------
    class Patch(H.Patch):
        """批次 3 的打桩 + 换掉 `cpq_db.table_types`（表结构从注册表来）。"""

        def connect(self, *args, **kwargs):
            autocommit = kwargs.get("autocommit", True)
            if args and isinstance(args[0], bool):
                autocommit = args[0]
            conn = MaterialConn(self.db, name=f"conn{len(self.conns) + 1}",
                                autocommit=autocommit)
            self.conns.append(conn)
            return conn

        def __enter__(self):
            self._orig_types = cpq_db.table_types
            cpq_db.table_types = self.table_types
            return super().__enter__()

        def __exit__(self, *exc):
            cpq_db.table_types = self._orig_types
            return super().__exit__(*exc)

        def table_types(self, conn, table):
            name = str(table or "").split(".")[-1].strip('"').lower()
            types = COLUMN_TYPES.get(name)
            if not types:
                raise RuntimeError(
                    f"目标表 {table} 不存在（假库只登记了 {sorted(COLUMN_TYPES)}；"
                    f"表名要与 Spec 6.3 一致）")
            return dict(types)

    # ---------------------------------------------------------------------
    # 受控场景
    # ---------------------------------------------------------------------
    FIN_UID = H.FIN_UID
    user_dict = H.user_dict

    DEFAULT_PROJECT = "proj-mat-0001"
    DEFAULT_VERSION = "mat-v1:cost-v1:1:123.45:0f1e2d3c4b"


    class MaterialWorkbench:
        """空的业务库 + 打桩：观察"这一次写入到底落了几行、在不在一个事务里"。"""

        def __init__(self, gate=None, seed_codes=()):
            self.db = FakeDB(gate=gate)
            self.patch = Patch(self.db)
            params = inspect.signature(cpq_tech_bridge.write_material).parameters
            self.accepts_key = ("project_id" in params and "result_version" in params) or any(
                item.kind == inspect.Parameter.VAR_KEYWORD for item in params.values())
            for index, number in enumerate(seed_codes):
                self.db.seed(BASE_TABLE, material_id=900000 + index, number=str(number),
                             name=f"历史成品{index + 1}")

        def __enter__(self):
            self.patch.__enter__()
            return self

        def __exit__(self, *exc):
            return self.patch.__exit__(*exc)

        # -- 调用 ----------------------------------------------------------
        def write(self, *, user=None, product_name="便携式锂电池 PACK", unit_price=123.45,
                  breakdown=None, spec="", project_id=DEFAULT_PROJECT,
                  result_version=DEFAULT_VERSION):
            """调一次「写入数据库」。

            业务幂等键是 Spec 6.1 的接口契约：服务端必须收 `project_id` 与 `result_version`
            （没有它们就组不出 `project_id|result_version|material-write`）。今天这两个参数
            还不存在，所以这里先看签名再传 —— 用例因此失败在**行为**（多出一个编码 /
            返回体里没有幂等状态），而不是一句 "unexpected keyword argument" 的 TypeError。
            """
            user = user if user is not None else user_dict(FIN_UID)
            breakdown = {"total": unit_price} if breakdown is None else breakdown
            if self.accepts_key:
                return cpq_tech_bridge.write_material(
                    user, product_name, unit_price, breakdown, spec,
                    project_id=project_id, result_version=result_version)
            return cpq_tech_bridge.write_material(user, product_name, unit_price, breakdown, spec)

        def write_legacy(self, *, user=None, product_name="便携式锂电池 PACK",
                         unit_price=123.45, breakdown=None, spec=""):
            """旧调用方的写法（不带 project_id / result_version）—— 兼容性用例专用。"""
            return cpq_tech_bridge.write_material(
                user if user is not None else user_dict(FIN_UID),
                product_name, unit_price,
                {"total": unit_price} if breakdown is None else breakdown, spec)

        # -- 观察 ----------------------------------------------------------
        def rows(self, table, **where):
            out = []
            for row in self.db.table(table):
                if all(row.get(key) == value for key, value in where.items()):
                    out.append(row)
            return out

        def base(self, **where):
            return self.rows(BASE_TABLE, **where)

        def costs(self, **where):
            return self.rows(COST_TABLE, **where)

        def writes(self, **where):
            return self.rows(IDEM_TABLE, **where)

        def codes(self):
            """主数据里出现的成品编码（排序后）。"""
            return sorted(str(row.get("number") or "") for row in self.base())

        def unprotected_writes(self):
            return [sql for protected, sql in self.db.write_log if not protected]

        def protected_writes(self):
            return [sql for protected, sql in self.db.write_log if protected]


    def race(work, calls):
        """并发跑若干次 `work(**kwargs)`；返回 (成功结果, 异常)。

        每个线程先用 barrier 对齐再调用，线程名统一以 `race` 开头（Gate 只拦这种）。
        """
        results, errors = [], []
        start = threading.Barrier(len(calls))
        lock = threading.Lock()

        def run(index, kwargs):
            threading.current_thread().name = f"race{index}"
            try:
                start.wait()
                value = work(**kwargs)
                with lock:
                    results.append(value)
            except Exception as exc:                          # noqa: BLE001 - 用例要看它
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=run, args=(index, kwargs),
                                    name=f"race{index}")
                   for index, kwargs in enumerate(calls)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(45)
        alive = [t.name for t in threads if t.is_alive()]
        if alive:                                             # pragma: no cover - 死锁才会
            raise AssertionError(f"并发线程没有结束（疑似死锁）：{alive}")
        return results, errors


    def selfcheck():
        """假库自身是否可用：结构注册表 / 唯一索引 / 事务回滚 / advisory 锁 / DDL 容忍。"""
        assert set(COLUMN_TYPES) <= set(B2.TABLE_COLUMNS), "三张表都要登记列顺序"
        db = FakeDB()
        with Patch(db):
            conn = MaterialConn(db, autocommit=False)
            # 1) cpq_db.insert_rows 能真跑（表结构来自注册表）
            saved = cpq_db.insert_rows(
                conn, BASE_TABLE, [{"material_id": 7, "number": "92022001", "name": "X"}])
            assert saved == 1, f"insert_rows 必须真的插进 1 行，实际 {saved}"
            # 2) 唯一索引真的会拦
            try:
                cpq_db.insert_rows(
                    conn, BASE_TABLE, [{"material_id": 8, "number": "92022001", "name": "Y"}])
            except FakeUniqueViolation:
                pass
            else:
                raise AssertionError("number 的唯一索引没有被执行")
            # 3) 事务能整体回滚
            conn.rollback()
            assert db.table(BASE_TABLE) == [], "autocommit=False + rollback 必须撤销写入"
            # 4) LIKE 能跑（`_next_code` 用的就是它）
            conn = MaterialConn(db, autocommit=False)
            cur = cpq_auth._exec(
                conn, f"SELECT number FROM {BASE_TABLE} WHERE number LIKE %s", ("92022%",))
            assert list(cur.fetchall()) == [], "LIKE 前缀查询必须能跑"
            cpq_db.insert_rows(
                conn, BASE_TABLE, [{"material_id": 9, "number": "92022002", "name": "Z"}])
            conn.commit()
            assert len(db.table(BASE_TABLE)) == 1, "commit 后必须留下写入"
        # 5) advisory 锁真的互斥且随事务释放
        holder = MaterialConn(db, name="holder", autocommit=False)
        other = MaterialConn(db, name="other", autocommit=True)
        holder.cursor().execute("SELECT pg_advisory_xact_lock(%s)", (4242,))
        taken = other.cursor()
        taken.execute("SELECT pg_try_advisory_xact_lock(%s)", (4242,))
        assert taken.fetchone()[0] is False, "已被别人持有的 advisory 锁不能再拿到"
        holder.commit()
        taken.execute("SELECT pg_try_advisory_xact_lock(%s)", (4242,))
        assert taken.fetchone()[0] is True, "commit 之后 advisory 锁必须释放"
        # 6) DDL 容忍
        db.run("CREATE TABLE IF NOT EXISTS cpq_wf_material_write (write_id bigint)", ())
        db.run("CREATE UNIQUE INDEX IF NOT EXISTS uq_x ON md_clm_material_base_info (number)", ())
        assert len(db.ddl) == 2, "DDL 不该报错，且要记下来"
        # 7) 失败注入：命中即抛
        db.fail_on(BASE_TABLE)
        try:
            with Patch(db):
                conn = MaterialConn(db, autocommit=False)
                cpq_db.insert_rows(
                    conn, BASE_TABLE, [{"material_id": 11, "number": "92022009", "name": "W"}])
        except RuntimeError:
            pass
        else:
            raise AssertionError("故障注入没有生效")
        return True


    SELFCHECK_OK = False
    SELFCHECK_ERROR = ""
    try:
        SELFCHECK_OK = selfcheck()
    except Exception as exc:                                  # pragma: no cover - 环境问题
        SELFCHECK_ERROR = f"{type(exc).__name__}: {exc}"

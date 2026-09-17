"""红测：报价任务并存规则、原子领取与多人并发保护（批次 2）。

用户口径（批次 2 原文要点）：

  · `cpq_wf.send_task` 今天会取消同一卡片的**全部** open 任务，不区分 handoff /
    tech_new_product / tech_cost / tech_cost_return —— 主线与支线必须并存，只有同类才互斥；
  · `claim_task` 先 SELECT 再 UPDATE，并发领取会互相覆盖 —— 必须原子化：一条带
    `status = 'open'` 条件的 UPDATE，并检查受影响行数或 RETURNING；
  · 必须定义：并存矩阵、同类重复发起（复用/替代）、`supersedes_task_id`、取消旧任务时如何
    通知发起人与收件人、公共任务并发领取只有一人成功、重复领取者本人的幂等、终态不可重开、
    列表如何显示「已被领取 / 已撤回 / 被新任务替代」、卡片所有权与支线领取继续分离；
  · 所有状态变化都要有审计记录。
  · 本批**不改**跨系统回传事务（那是批次 3）。

现状缺口（只读实测，均已定位，不是推断）：

  · `cpq_wf.py:819-822` 的无条件取消：`UPDATE cpq_wf_task SET status = 'cancelled'
    WHERE card_id = %s AND status = 'open'` —— 没有 task_kind、没有审计、没有通知；
    线上 `cpq_wf_task` 共 220 行，其中 `status='cancelled'` **45 行**，而这 45 行在
    `cpq_wf_task_event` 里 `action IN ('cancel','supersede')` 的记录数是 **0**；
  · `cpq_wf.py:1002-1030` 的领取：`SELECT ... WHERE task_id = %s` 判状态后
    `UPDATE ... WHERE task_id = %s`（无 `AND status = 'open'`、不看 rowcount/RETURNING）；
  · `cpq_wf_task` 没有 `supersedes_task_id` / `replaced_by_task_id` / `cancel_reason` /
    `cancelled_at`（`information_schema` 实测），也没有 `(card_id, task_kind) WHERE status='open'`
    的唯一约束；
  · `cpq_wf.MSG_TYPES`（`cpq_wf.py:690-694`）与 `cpq_msg.js:42-46` 只有 task_sent /
    task_received / task_claimed；
  · `报价首页.html:1621-1653`（`taskCardHtml`）与 `tech_app/frontend/cpq-tech-inbox.js:108-138`
    （`taskCard`）只认 `claimed` 与「待领取」两态；`报价首页.html:1603-1610` 的 `wfBadge()`
    直接数 `WF.tasks.length`（终态行会被算成待办）。

验证方式（**行为**为主，受控假库 + 真调 `cpq_wf`，不是文本搜索）：

  A. 受控假库 `FakeDB`：一个能真跑 `cpq_wf` 所发 SQL 的小引擎（SELECT/INSERT/UPDATE +
     JOIN + 布尔 WHERE + RETURNING + rowcount），并**模型化**规格要求的
     `(card_id, task_kind) WHERE status='open'` 唯一约束；多条连接共享同一份数据，
     等价于 autocommit。
  B. 受控交错 `Gate`：并发用例里让每个线程在**第一条**触碰 `cpq_wf_task` 的语句后对齐，
     从而确定性地复现「两个领取者都读到 open」的丢失更新，不靠碰运气。
  C. 静态契约只用于 SQL 形态（`status = 'open'` + rowcount/RETURNING）、DDL（新列 + 唯一索引）
     与前端渲染 / 计数（DOM 与脚本接线，属用户允许的静态范围）。

Spec：docs/specs/quote-task-coexistence-and-atomic-claim.md
"""
from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import threading
import unittest

import psycopg.errors

import cpq_auth
import cpq_wf

ROOT = pathlib.Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
SPEC = ROOT / "docs" / "specs" / "quote-task-coexistence-and-atomic-claim.md"
CPQ_WF_PY = ROOT / "cpq_wf.py"
CPQ_HOME_HTML = ROOT / "报价首页.html"
TECH_INBOX_JS = ROOT / "tech_app" / "frontend" / "cpq-tech-inbox.js"
CPQ_MSG_JS = ROOT / "cpq_msg.js"


# ---------------------------------------------------------------------------
# 受控假库：能真跑 cpq_wf 用到的 SQL 的最小引擎
# ---------------------------------------------------------------------------
def norm_sql(sql) -> str:
    return " ".join(str(sql).split())


class UnsupportedSql(AssertionError):
    """假库不认识的语句形态 —— 直接失败，绝不静默跳过。"""


class FakeUniqueViolation(psycopg.errors.UniqueViolation):
    """与 psycopg 同一个类，方便被测代码 `except psycopg.errors.UniqueViolation`。"""


# 表的列顺序（`alias.*` 展开用；缺列按 None 补齐，新增列自动可见）
TABLE_COLUMNS = {
    "cpq_wf_user": ("user_id", "username", "display_name", "role_code", "status"),
    "cpq_wf_card": ("card_id", "session_id", "assistant_type", "title", "customer",
                    "project_name", "current_step", "overall_status", "creator_user_id",
                    "current_owner", "created_at", "updated_at"),
    "cpq_wf_task": ("task_id", "card_id", "from_user_id", "from_step_no", "target_type",
                    "target_role_code", "target_user_id", "claimed_by_user_id", "status",
                    "source_label", "note", "created_at", "claimed_at", "completed_at",
                    "task_kind", "payload", "supersedes_task_id", "replaced_by_task_id",
                    "cancel_reason", "cancelled_at"),
    "cpq_wf_task_event": ("event_id", "card_id", "task_id", "actor_user_id", "action",
                          "from_step", "to_step", "comment", "created_at"),
    "cpq_wf_message": ("message_id", "user_id", "msg_type", "title", "body", "card_id",
                       "task_id", "session_id", "step_no", "is_read", "created_at"),
    "cpq_wf_step_perm": ("id", "assistant_type", "step_no", "step_name", "role_code"),
}

_TOKEN = re.compile(r"""
      (?P<ws>\s+)
    | (?P<ph>%s(?:::[A-Za-z_]+)?)
    | (?P<str>'(?:[^']|'')*')
    | (?P<num>-?\d+(?:\.\d+)?)
    | (?P<name>[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)?)
    | (?P<op><>|<=|>=|=|<|\(|\)|,)
""", re.X)


class _Args:
    def __init__(self, args):
        self.args = tuple(args or ())
        self.i = 0

    def take(self):
        if self.i >= len(self.args):
            raise UnsupportedSql("SQL 里的占位符比传入参数多")
        v = self.args[self.i]
        self.i += 1
        return v

    def done(self):
        if self.i != len(self.args):
            raise UnsupportedSql(
                f"SQL 占位符 {self.i} 个，传入参数 {len(self.args)} 个（假库解析错位）")


class _Node:
    def eval(self, scope):
        raise NotImplementedError


class _Cmp(_Node):
    def __init__(self, op, lhs, rhs):
        self.op, self.lhs, self.rhs = op, lhs, rhs

    def eval(self, scope):
        left = _ref_value(scope, self.lhs)
        right = _ref_value(scope, self.rhs) if isinstance(self.rhs, _Ref) else self.rhs
        op = self.op
        if op == "=":
            return left == right
        if op == "<>":
            return left != right
        if op == ">":
            return left is not None and right is not None and left > right
        if op == "<":
            return left is not None and right is not None and left < right
        if op == ">=":
            return left is not None and right is not None and left >= right
        if op == "<=":
            return left is not None and right is not None and left <= right
        if op == "IS NULL":
            return left is None
        if op == "IS NOT NULL":
            return left is not None
        if op == "IN":
            return left in right
        if op == "NOT IN":
            return left not in right
        raise UnsupportedSql(f"假库不支持的比较符：{op}")


class _Bool(_Node):
    def __init__(self, op, parts):
        self.op, self.parts = op, parts

    def eval(self, scope):
        if self.op == "AND":
            return all(p.eval(scope) for p in self.parts)
        return any(p.eval(scope) for p in self.parts)


class _Not(_Node):
    def __init__(self, node):
        self.node = node

    def eval(self, scope):
        return not self.node.eval(scope)


class _Ref:
    def __init__(self, name):
        self.name = name


def _ref_value(scope, ref):
    if isinstance(ref, _Ref):
        if ref.name in scope:
            return scope[ref.name]
        if "." in ref.name:
            return None
        hits = [v for k, v in scope.items() if k.endswith("." + ref.name)]
        if len(hits) == 1:
            return hits[0]
        if not hits:
            return None
        raise UnsupportedSql(f"列名歧义：{ref.name}")
    return ref


class WhereParser:
    """把 WHERE 文本解析成 AST（同时在解析期按文本顺序取走参数）。"""

    def __init__(self, text, args: _Args):
        self.tokens = []
        for m in _TOKEN.finditer(text):
            kind = m.lastgroup
            if kind == "ws":
                continue
            self.tokens.append((kind, m.group(), m.start()))
        self.i = 0
        self.args = args

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None, None)

    def take(self):
        tok = self.peek()
        self.i += 1
        return tok

    def parse(self):
        node = self.parse_or()
        if self.i != len(self.tokens):
            raise UnsupportedSql("假库没解析完 WHERE：" + " ".join(t[1] for t in self.tokens[self.i:]))
        return node

    def parse_or(self):
        parts = [self.parse_and()]
        while self.peek()[1] and self.peek()[1].upper() == "OR":
            self.take()
            parts.append(self.parse_and())
        return parts[0] if len(parts) == 1 else _Bool("OR", parts)

    def parse_and(self):
        parts = [self.parse_factor()]
        while self.peek()[1] and self.peek()[1].upper() == "AND":
            self.take()
            parts.append(self.parse_factor())
        return parts[0] if len(parts) == 1 else _Bool("AND", parts)

    def parse_factor(self):
        kind, text, _ = self.peek()
        if kind == "op" and text == "(":
            self.take()
            node = self.parse_or()
            kind2, text2, _ = self.take()
            if text2 != ")":
                raise UnsupportedSql("括号不匹配")
            return node
        if kind == "name" and text.upper() == "NOT":
            self.take()
            return _Not(self.parse_factor())
        return self.parse_cmp()

    def parse_cmp(self):
        kind, text, _ = self.peek()
        if kind != "name":
            raise UnsupportedSql(f"WHERE 里期望列名，得到 {text!r}")
        self.take()
        lhs = _Ref(text)
        nxt_kind, nxt, _ = self.peek()
        if nxt_kind == "name" and nxt.upper() in ("IS", "IN", "NOT"):
            word = nxt.upper()
            if word == "IS":
                self.take()
                k2, t2, _ = self.take()
                if t2.upper() == "NOT":
                    k3, t3, _ = self.take()
                    if t3.upper() != "NULL":
                        raise UnsupportedSql("只支持 IS NOT NULL")
                    return _Cmp("IS NOT NULL", lhs, None)
                if t2.upper() != "NULL":
                    raise UnsupportedSql("只支持 IS NULL")
                return _Cmp("IS NULL", lhs, None)
            if word == "NOT":
                self.take()
                k2, t2, _ = self.take()
                if t2.upper() != "IN":
                    raise UnsupportedSql("只支持 NOT IN")
                return _Cmp("NOT IN", lhs, self.parse_value_list())
            self.take()
            return _Cmp("IN", lhs, self.parse_value_list())
        if nxt_kind != "op" or nxt not in ("=", "<>", ">", "<", ">=", "<="):
            raise UnsupportedSql(f"假库不支持的比较：{text} {nxt}")
        self.take()
        rhs_kind, rhs_text, _ = self.peek()
        if rhs_kind == "name":
            # 列 = 列（JOIN 条件）
            self.take()
            return _Cmp(nxt, lhs, _Ref(rhs_text))
        return _Cmp(nxt, lhs, self.parse_value())

    def parse_value_list(self):
        kind, text, _ = self.take()
        if text != "(":
            raise UnsupportedSql("IN 后面必须是括号")
        values = []
        while True:
            values.append(self.parse_value())
            kind, text, _ = self.peek()
            if kind == "op" and text == ",":
                self.take()
                continue
            if kind == "op" and text == ")":
                self.take()
                break
            raise UnsupportedSql("IN 列表格式不对")
        return values

    def parse_value(self):
        kind, text, _ = self.take()
        if kind == "ph":
            return self.args.take()
        if kind == "str":
            return text[1:-1].replace("''", "'")
        if kind == "num":
            return float(text) if "." in text else int(text)
        if kind == "name":
            up = text.upper()
            if up == "NULL":
                return None
            if up == "TRUE":
                return True
            if up == "FALSE":
                return False
        raise UnsupportedSql(f"假库不支持的取值：{text!r}")


def _find_top(text: str, keyword: str, start: int = 0):
    """在引号/括号之外找关键字（返回下标；找不到 -1）。"""
    key = " " + keyword.upper() + " "
    upper = " " + text.upper() + " "
    depth = 0
    quote = False
    for i, ch in enumerate(text):
        if quote:
            if ch == "'":
                quote = False
            continue
        if ch == "'":
            quote = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and ch == " " and upper[i + 1:i + 1 + len(key)] == key:
            idx = i + 1
            if idx >= start:
                return idx
    return -1


def _split_top(text: str, sep: str = ","):
    out, depth, quote, last = [], 0, False, 0
    for i, ch in enumerate(text):
        if quote:
            if ch == "'":
                quote = False
            continue
        if ch == "'":
            quote = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(text[last:i])
            last = i + 1
    out.append(text[last:])
    return [s.strip() for s in out if s.strip()]


def _strip_alias(table_expr: str):
    parts = table_expr.strip().split()
    if not parts:
        raise UnsupportedSql("空的表名")
    table = parts[0].split(".")[-1]
    alias = table
    if len(parts) > 1:
        alias = parts[2] if parts[1].upper() == "AS" else parts[1]
    return table, alias


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.rows = []
        self.rowcount = -1
        self._pos = 0

    # psycopg 的游标支持 with
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        return None

    def execute(self, sql, args=()):
        self.rows, self.rowcount = self.conn.db.run(sql, args)
        self._pos = 0
        return None

    def fetchone(self):
        if self._pos >= len(self.rows):
            return None
        row = self.rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self):
        out = self.rows[self._pos:]
        self._pos = len(self.rows)
        return list(out)


class FakeConn:
    def __init__(self, db, name=""):
        self.db = db
        self.name = name
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def close(self):
        self.closed = True

    def commit(self):
        return None

    def rollback(self):
        return None


class Gate:
    """受控交错：让每个并发线程在第一条触碰 cpq_wf_task 的语句之后对齐一次。

    只拦名字以 `prefix` 开头的线程 —— 用例要在主线程里做种子与发送，那些语句不该被拦，
    否则 barrier 的参与方数量就乱了（既会拖慢，也会让 arrivals 数不准）。
    """

    PATTERN = re.compile(r"\bcpq_wf_task\b")

    def __init__(self, parties=2, timeout=15.0, prefix="race"):
        self.barrier = threading.Barrier(parties, timeout=timeout)
        self.prefix = prefix
        self.arrivals = 0
        self._seen = set()
        self._lock = threading.Lock()

    def maybe_wait(self, sql):
        if not self.PATTERN.search(sql):
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


class FakeDB:
    """多连接共享一份数据；等价于 autocommit。"""

    def __init__(self, gate: Gate = None):
        self.tables = {name: [] for name in TABLE_COLUMNS}
        self.gate = gate
        self._id = 5000000000000000000
        self.statements = []

    # -- 数据访问 ------------------------------------------------------
    def table(self, name):
        if name not in self.tables:
            self.tables[name] = []
            TABLE_COLUMNS.setdefault(name, ())
        return self.tables[name]

    def next_id(self):
        self._id += 1
        return self._id

    def seed(self, table, **cols):
        self.table(table).append(dict(cols))
        return cols

    # -- 语句入口 ------------------------------------------------------
    def run(self, sql, args):
        text = norm_sql(sql)
        self.statements.append(text)
        a = _Args(args)
        head = text.split(None, 1)[0].upper() if text else ""
        try:
            if head == "SELECT":
                rows, count = self._select(text, a)
            elif head == "INSERT":
                rows, count = self._insert(text, a)
            elif head == "UPDATE":
                rows, count = self._update(text, a)
            elif head == "DELETE":
                rows, count = self._delete(text, a)
            else:
                raise UnsupportedSql("假库不支持的语句：" + text[:80])
        finally:
            pass
        a.done()
        if self.gate:
            self.gate.maybe_wait(text)
        return rows, count

    # -- SELECT --------------------------------------------------------
    def _select(self, text, a: _Args):
        if re.match(r"^SELECT\s+snow_next_id\s*\(\s*\)\s*$", text, re.I):
            return [(self.next_id(),)], 1
        from_idx = _find_top(text, "FROM")
        if from_idx < 0:
            raise UnsupportedSql("SELECT 没有 FROM：" + text[:80])
        select_list = text[len("SELECT"):from_idx].strip()
        rest = text[from_idx + len("FROM"):].strip()
        where_idx = _find_top(rest, "WHERE")
        order_idx = _find_top(rest, "ORDER BY")
        for_idx = _find_top(rest, "FOR UPDATE")
        cut = min([i for i in (where_idx, order_idx, for_idx) if i >= 0] or [len(rest)])
        from_part = rest[:cut].strip()
        where_text = ""
        if where_idx >= 0:
            end = min([i for i in (order_idx, for_idx) if i >= 0] or [len(rest)])
            where_text = rest[where_idx + len("WHERE"):end].strip()
        order_text = ""
        if order_idx >= 0:
            end = for_idx if for_idx > order_idx else len(rest)
            order_text = rest[order_idx + len("ORDER BY"):end].strip()
        scope_rows, alias_map = self._from_rows(from_part, where_text, a)
        if order_text:
            scope_rows = self._order(scope_rows, order_text)
        rows = [self._project(s, select_list, alias_map) for s in scope_rows]
        return rows, len(rows)

    JOIN_RE = re.compile(r"\b(?:(LEFT|INNER|RIGHT)\s+)?JOIN\b", re.I)
    ON_RE = re.compile(r"\bON\b", re.I)

    def _from_rows(self, from_part, where_text, a: _Args):
        m = re.match(r"^(?P<table>[A-Za-z_][\w.]*)(?:\s+(?:AS\s+)?(?P<alias>[A-Za-z_]\w*))?",
                     from_part)
        if not m:
            raise UnsupportedSql("FROM 解析失败：" + from_part[:80])
        base_table = m.group("table").split(".")[-1]
        alias = m.group("alias")
        if alias and alias.upper() in ("WHERE", "ORDER", "LEFT", "JOIN", "ON", "INNER",
                                       "RIGHT", "FOR", "GROUP", "LIMIT"):
            alias = None
        parts = [(None, base_table, alias or base_table, None)]
        rest = from_part[m.end():].strip()
        pos = 0
        while True:
            jm = self.JOIN_RE.search(rest, pos)
            if not jm:
                break
            om = self.ON_RE.search(rest, jm.end())
            if not om:
                raise UnsupportedSql("JOIN 没有 ON：" + rest[:80])
            nxt = self.JOIN_RE.search(rest, om.end())
            on_text = rest[om.end(): nxt.start() if nxt else len(rest)].strip()
            table_expr = rest[jm.end(): om.start()].strip()
            kind = (jm.group(1) or "INNER").upper()
            table, jalias = _strip_alias(table_expr)
            parts.append((kind, table, jalias, WhereParser(on_text, a).parse()))
            pos = nxt.start() if nxt else len(rest)
            if not nxt:
                break
        where_node = WhereParser(where_text, a).parse() if where_text else None
        alias_map = {p[2]: p[1] for p in parts}
        scope_rows = []
        _, base_table, base_alias, _ = parts[0]
        for row in self.table(base_table):
            scope_rows.append(self._scope(base_table, base_alias, row))
        for kind, table, jalias, on in parts[1:]:
            merged = []
            for left in scope_rows:
                matched = False
                for row in self.table(table):
                    scope = dict(left)
                    scope.update(self._scope(table, jalias, row))
                    if on is None or on.eval(scope):
                        merged.append(scope)
                        matched = True
                if kind == "LEFT" and not matched:
                    scope = dict(left)
                    scope.update(self._scope(table, jalias, None))
                    merged.append(scope)
            scope_rows = merged
        if where_node is not None:
            scope_rows = [s for s in scope_rows if where_node.eval(s)]
        return scope_rows, alias_map

    def _scope(self, table, alias, row):
        cols = list(TABLE_COLUMNS.get(table, ()))
        if row:
            for k in row:
                if k not in cols:
                    cols.append(k)
        return {f"{alias}.{col}": (row or {}).get(col) for col in cols}

    def _order(self, rows, order_text):
        items = []
        for piece in _split_top(order_text):
            m = re.match(r"^([A-Za-z_][\w.]*)(?:\s+(ASC|DESC))?$", piece.strip(), re.I)
            if not m:
                raise UnsupportedSql("ORDER BY 解析失败：" + piece)
            items.append((m.group(1), (m.group(2) or "ASC").upper()))
        out = list(rows)
        for ref, direction in reversed(items):
            out.sort(key=lambda s: (_ref_value(s, _Ref(ref)) is None,
                                    _ref_value(s, _Ref(ref))),
                     reverse=(direction == "DESC"))
        return out

    def _project(self, scope, select_list, alias_map=None):
        alias_map = alias_map or {}
        out = []
        for item in _split_top(select_list):
            item = item.strip()
            if item == "*":
                raise UnsupportedSql("假库不支持裸 *")
            if item.endswith(".*"):
                alias = item[:-2]
                cols = [k for k in scope if k.startswith(alias + ".")]
                if not cols:
                    raise UnsupportedSql(f"未知别名：{alias}")
                declared = list(TABLE_COLUMNS.get(alias_map.get(alias, alias), ()) or ())
                cols.sort(key=lambda k: declared.index(k.split(".", 1)[1])
                          if k.split(".", 1)[1] in declared else 999)
                out.extend(scope[k] for k in cols)
                continue
            m = re.match(r"^([A-Za-z_][\w.]*)(?:\s+AS\s+[A-Za-z_]\w*)?$", item, re.I)
            if not m:
                raise UnsupportedSql("SELECT 表达式解析失败：" + item)
            out.append(_ref_value(scope, _Ref(m.group(1))))
        return tuple(out)

    # -- INSERT --------------------------------------------------------
    def _insert(self, text, a: _Args):
        m = re.match(r"^INSERT\s+INTO\s+(?P<table>[A-Za-z_][\w.]*)\s*\((?P<cols>[^)]*)\)\s*"
                     r"VALUES\s*\((?P<vals>.*)\)\s*$", text, re.I | re.S)
        if not m:
            raise UnsupportedSql("INSERT 解析失败：" + text[:120])
        table = m.group("table").split(".")[-1]
        cols = [c.strip() for c in m.group("cols").split(",")]
        values = [WhereParser(v, a).parse_value() for v in _split_top(m.group("vals"))]
        if len(cols) != len(values):
            raise UnsupportedSql("INSERT 列数与值数不一致")
        row = dict(zip(cols, values))
        if table == "cpq_wf_task" and row.get("status") == "open":
            for other in self.table(table):
                if (other.get("card_id") == row.get("card_id")
                        and other.get("task_kind") == row.get("task_kind")
                        and other.get("status") == "open"):
                    raise FakeUniqueViolation(
                        'duplicate key value violates unique constraint "uq_wf_task_open_kind"'
                        " —— 同一卡片同一任务类型只能有一条 open 任务")
        self.table(table).append(row)
        return [], 1

    # -- UPDATE --------------------------------------------------------
    def _update(self, text, a: _Args):
        set_idx = _find_top(text, "SET")
        if set_idx < 0:
            raise UnsupportedSql("UPDATE 没有 SET：" + text[:80])
        table_expr = text[len("UPDATE"):set_idx].strip()
        table, alias = _strip_alias(table_expr)
        rest = text[set_idx + len("SET"):]
        where_idx = _find_top(rest, "WHERE")
        ret_idx = _find_top(rest, "RETURNING")
        cuts = [i for i in (where_idx, ret_idx) if i >= 0] or [len(rest)]
        set_text = rest[:min(cuts)].strip()
        where_text = ""
        if where_idx >= 0:
            end = ret_idx if ret_idx > where_idx else len(rest)
            where_text = rest[where_idx + len("WHERE"):end].strip()
        ret_text = rest[ret_idx + len("RETURNING"):].strip() if ret_idx >= 0 else ""
        assigns = []
        for piece in _split_top(set_text):
            m = re.match(r"^([A-Za-z_][\w.]*)\s*=\s*(.+)$", piece.strip(), re.S)
            if not m:
                raise UnsupportedSql("SET 解析失败：" + piece)
            assigns.append((m.group(1).split(".")[-1], WhereParser(m.group(2).strip(), a).parse_value()))
        where_node = WhereParser(where_text, a).parse() if where_text else None
        hit = []
        for row in self.table(table):
            scope = self._scope(table, alias, row)
            if where_node is not None and not where_node.eval(scope):
                continue
            for col, val in assigns:
                row[col] = val
            hit.append(self._scope(table, alias, row))
        rows = []
        if ret_text:
            rows = [self._project(s, ret_text, {alias: table}) for s in hit]
        return rows, len(hit)

    # -- DELETE --------------------------------------------------------
    def _delete(self, text, a: _Args):
        raise UnsupportedSql("假库不需要 DELETE：" + text[:60])


class _FakePatch:
    """把 cpq_auth._connect 换成假库连接；退出时还原。"""

    def __init__(self, db):
        self.db = db
        self.orig = None
        self.counter = 0
        self._lock = threading.Lock()

    def connect(self):
        with self._lock:
            self.counter += 1
            return FakeConn(self.db, name=f"conn{self.counter}")

    def __enter__(self):
        self.orig = cpq_auth._connect
        cpq_auth._connect = self.connect
        return self

    def __exit__(self, *exc):
        cpq_auth._connect = self.orig
        return False


# ---------------------------------------------------------------------------
# 受控场景：种子数据 + 便捷调用与观察
# ---------------------------------------------------------------------------
SALES_UID = 100
PROC1_UID = 200
PROC2_UID = 210
FIN1_UID = 400
FIN2_UID = 410

SESSION = "sess-b2-0001"
SESSION2 = "sess-b2-0002"
CARD_ID = 1
CARD2_ID = 2

_NAMES = {
    SALES_UID: ("sales1", "SM1", "sales_mgr"),
    PROC1_UID: ("proc1", "PM1", "process_mgr"),
    PROC2_UID: ("proc2", "PM2", "process_mgr"),
    FIN1_UID: ("fin1", "FI1", "finance_mgr"),
    FIN2_UID: ("fin2", "FI2", "finance_mgr"),
}


def user_dict(uid):
    username, display_name, role_code = _NAMES[uid]
    return {"user_id": str(uid), "username": username, "display_name": display_name,
            "role_code": role_code, "role_name": cpq_auth.ROLES.get(role_code, role_code),
            "status": "active"}


class Workbench:
    """一张报价卡片 + 五个账号的受控场景。"""

    def __init__(self, gate=None):
        self.db = FakeDB(gate=gate)
        self.patch = _FakePatch(self.db)
        self._seed()

    def __enter__(self):
        self.patch.__enter__()
        return self

    def __exit__(self, *exc):
        return self.patch.__exit__(*exc)

    def _seed(self):
        for uid, (username, display_name, role_code) in _NAMES.items():
            self.db.seed("cpq_wf_user", user_id=uid, username=username,
                         display_name=display_name, role_code=role_code, status="active")
        for card_id, session in ((CARD_ID, SESSION), (CARD2_ID, SESSION2)):
            self.db.seed("cpq_wf_card", card_id=card_id, session_id=session,
                         assistant_type="quote", title=f"报价{card_id}", customer="客户A",
                         project_name="项目A", current_step=1, overall_status="awaiting_handoff",
                         creator_user_id=SALES_UID, current_owner=SALES_UID,
                         created_at="2026-09-17T10:00:00", updated_at="2026-09-17T10:00:00")
        for no, name, role in cpq_wf.QUOTE_STEPS:
            self.db.seed("cpq_wf_step_perm", id=no, assistant_type="quote",
                         step_no=no, step_name=name, role_code=role)

    # -- 调用 ----------------------------------------------------------
    def user(self, uid):
        return user_dict(uid)

    def send(self, uid=SALES_UID, kind="handoff", target_type="role", role=None,
             target_user="", note="", payload=None, session=SESSION):
        if role is None:
            role = {"handoff": "process_mgr", "tech_new_product": "process_mgr",
                    "tech_cost": "finance_mgr", "tech_cost_return": "process_mgr"}[kind]
        return cpq_wf.send_task(session, self.user(uid), target_type, role,
                                str(target_user or ""), note, kind, payload)

    def claim(self, task_id, uid):
        return cpq_wf.claim_task(str(task_id), self.user(uid))

    # -- 观察 ----------------------------------------------------------
    def rows(self, table, **where):
        out = []
        for row in self.db.table(table):
            if all(row.get(k) == v for k, v in where.items()):
                out.append(row)
        return out

    def tasks(self, **where):
        return self.rows("cpq_wf_task", **where)

    def events(self, **where):
        return self.rows("cpq_wf_task_event", **where)

    def messages(self, **where):
        return self.rows("cpq_wf_message", **where)

    def person(self):
        return self.db.table("cpq_wf_card")[0]

    def open_row(self, kind="handoff", card_id=CARD_ID):
        rows = [t for t in self.db.table("cpq_wf_task")
                if t.get("task_kind") == kind and t.get("card_id") == card_id
                and t.get("status") == "open"]
        if len(rows) != 1:
            raise AssertionError(
                f"期望卡片 {card_id} 上恰好 1 条 open 的 {kind} 任务，实际 {len(rows)} 条")
        return rows[0]

    def one_task(self, kind="handoff", card_id=CARD_ID):
        rows = [t for t in self.db.table("cpq_wf_task")
                if t.get("task_kind") == kind and t.get("card_id") == card_id]
        if len(rows) != 1:
            raise AssertionError(
                f"期望卡片 {card_id} 上恰好 1 条 {kind} 任务，实际 {len(rows)} 条："
                + repr([(t.get("task_id"), t.get("status")) for t in rows]))
        return rows[0]


# ---------------------------------------------------------------------------
# 脚手架自检：假库与交错闸门本身必须可靠，否则失败会落在错的地方
# ---------------------------------------------------------------------------
class HarnessSelfTest(unittest.TestCase):
    def test_engine_honours_where_conditions_and_rowcount(self):
        db = FakeDB()
        db.seed("cpq_wf_task", task_id=1, card_id=1, task_kind="handoff", status="open")
        db.seed("cpq_wf_task", task_id=2, card_id=1, task_kind="handoff", status="claimed")
        cur = FakeConn(db).cursor()
        cur.execute("UPDATE cpq_wf_task SET status = 'claimed' WHERE task_id = %s"
                    " AND status = 'open'", (1,))
        self.assertEqual(cur.rowcount, 1)
        cur.execute("UPDATE cpq_wf_task SET status = 'claimed' WHERE task_id = %s"
                    " AND status = 'open'", (1,))
        self.assertEqual(cur.rowcount, 0, "带 status='open' 条件的 UPDATE 第二次必须打不中")
        cur.execute("UPDATE cpq_wf_task SET claimed_by_user_id = %s WHERE task_id = %s"
                    " RETURNING card_id", (7, 2))
        self.assertEqual(cur.fetchall(), [(1,)])
        self.assertEqual(db.table("cpq_wf_task")[1]["claimed_by_user_id"], 7)

    def test_engine_supports_in_and_null_predicates(self):
        db = FakeDB()
        db.seed("cpq_wf_task", task_id=1, card_id=1, status="open", task_kind="handoff")
        db.seed("cpq_wf_task", task_id=2, card_id=1, status="cancelled", task_kind="handoff")
        db.seed("cpq_wf_task", task_id=3, card_id=1, status="open", task_kind="tech_cost")
        cur = FakeConn(db).cursor()
        cur.execute("SELECT task_id FROM cpq_wf_task WHERE card_id = %s"
                    " AND status IN ('open', 'claimed') AND replaced_by_task_id IS NULL"
                    " AND task_kind <> %s ORDER BY task_id", (1, "tech_cost"))
        self.assertEqual(cur.fetchall(), [(1,)])

    def test_engine_raises_on_unknown_statement(self):
        with self.assertRaises(UnsupportedSql):
            FakeConn(FakeDB()).cursor().execute("TRUNCATE cpq_wf_task")

    def test_engine_supports_inbox_shaped_join_and_boolean_where(self):
        db = FakeDB()
        db.seed("cpq_wf_user", user_id=1, display_name="A", role_code="sales_mgr", status="active")
        db.seed("cpq_wf_card", card_id=1, session_id="s1", title="T")
        db.seed("cpq_wf_task", task_id=10, card_id=1, from_user_id=1, target_type="public",
                status="open", task_kind="handoff", created_at="2026-01-01")
        db.seed("cpq_wf_task", task_id=11, card_id=1, from_user_id=1, target_type="role",
                target_role_code="finance_mgr", status="open", task_kind="tech_cost",
                created_at="2026-01-02")
        db.seed("cpq_wf_task", task_id=12, card_id=1, from_user_id=1, target_type="role",
                target_role_code="process_mgr", status="cancelled", task_kind="handoff",
                created_at="2026-01-03")
        cur = FakeConn(db).cursor()
        cur.execute(
            "SELECT t.task_id, c.session_id, fu.display_name FROM cpq_wf_task t"
            " JOIN cpq_wf_card c ON c.card_id = t.card_id"
            " LEFT JOIN cpq_wf_user fu ON fu.user_id = t.from_user_id"
            " WHERE (t.status = 'open' AND (t.target_type = 'public'"
            "         OR (t.target_type = 'role' AND t.target_role_code = %s)))"
            "    OR (t.status = 'claimed' AND t.claimed_by_user_id = %s)"
            " ORDER BY t.created_at DESC", ("finance_mgr", 99))
        self.assertEqual(cur.fetchall(), [(11, "s1", "A"), (10, "s1", "A")])

    def test_engine_models_the_open_task_unique_constraint(self):
        db = FakeDB()
        db.seed("cpq_wf_task", task_id=1, card_id=1, task_kind="handoff", status="open")
        conn = FakeConn(db)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            conn.cursor().execute(
                "INSERT INTO cpq_wf_task (task_id, card_id, task_kind, status)"
                " VALUES (%s,%s,%s,%s)", (2, 1, "handoff", "open"))
        conn.cursor().execute(
            "INSERT INTO cpq_wf_task (task_id, card_id, task_kind, status)"
            " VALUES (%s,%s,%s,%s)", (3, 1, "tech_cost", "open"))
        conn.cursor().execute(
            "INSERT INTO cpq_wf_task (task_id, card_id, task_kind, status)"
            " VALUES (%s,%s,%s,%s)", (4, 1, "handoff", "cancelled"))
        self.assertEqual(len(db.table("cpq_wf_task")), 3)

    def test_gate_makes_two_threads_meet(self):
        db = FakeDB(gate=Gate())
        db.seed("cpq_wf_task", task_id=1, card_id=1, task_kind="handoff", status="open")
        seen = []

        def worker():
            cur = FakeConn(db).cursor()
            cur.execute("SELECT task_id FROM cpq_wf_task WHERE task_id = %s", (1,))
            seen.append(cur.fetchall())

        threads = [threading.Thread(target=worker, name=f"race-{i}") for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(db.gate.arrivals, 2, "交错闸门必须让两个线程都对齐到同一语句")
        self.assertEqual(len(seen), 2)

    def test_workbench_runs_real_cpq_wf_paths(self):
        wb = Workbench()
        with wb:
            sent = wb.send()
            self.assertEqual(sent["task_kind"], "handoff")
            got = cpq_wf.task_detail(sent["task_id"], wb.user(PROC1_UID))
            self.assertEqual(got["status"], "open")
            self.assertEqual(got["session_id"], SESSION)
            claim = wb.claim(sent["task_id"], PROC1_UID)
            self.assertEqual(claim["session_id"], SESSION)
            self.assertEqual(wb.one_task()["claimed_by_user_id"], PROC1_UID)


# ---------------------------------------------------------------------------
# 并存矩阵：主线与三条支线互不相残
# ---------------------------------------------------------------------------
class CoexistenceMatrixTest(unittest.TestCase):
    def test_four_kinds_coexist_on_one_card(self):
        wb = Workbench()
        with wb:
            wb.send(kind="handoff", target_type="role", role="process_mgr", note="转工艺确认")
            wb.send(kind="tech_new_product", target_type="role", role="process_mgr", note="新建产品")
            wb.send(kind="tech_cost", target_type="role", role="finance_mgr", note="测算成本")
            wb.send(kind="tech_cost_return", target_type="role", role="process_mgr", note="复核用量")
            by_kind = {}
            for t in wb.tasks(card_id=CARD_ID):
                by_kind.setdefault(t["task_kind"], []).append(t["status"])
            self.assertEqual(by_kind, {
                "handoff": ["open"],
                "tech_new_product": ["open"],
                "tech_cost": ["open"],
                "tech_cost_return": ["open"],
            }, f"四种任务必须并存，实际：{by_kind}")

    def test_side_task_does_not_cancel_or_replace_handoff(self):
        wb = Workbench()
        with wb:
            handoff = wb.send(kind="handoff", target_type="role", role="process_mgr")
            wb.send(kind="tech_cost", target_type="role", role="finance_mgr")
            old = wb.one_task("handoff")
            self.assertEqual(old["status"], "open", "支线任务不得取消主线 handoff")
            self.assertIn(old.get("replaced_by_task_id"), (None, ""),
                          "主线 handoff 不该被标记为「被新任务替代」")
            self.assertEqual(wb.one_task("tech_cost")["status"], "open")
            self.assertEqual(wb.person()["overall_status"], "handoff_pending")
            self.assertEqual(str(handoff["task_id"]), str(old["task_id"]))

    def test_handoff_does_not_cancel_side_tasks(self):
        wb = Workbench()
        with wb:
            wb.send(kind="tech_cost", target_type="role", role="finance_mgr")
            wb.send(kind="tech_new_product", target_type="role", role="process_mgr")
            wb.send(kind="handoff", target_type="role", role="process_mgr")
            self.assertEqual(wb.one_task("tech_cost")["status"], "open")
            self.assertEqual(wb.one_task("tech_new_product")["status"], "open")
            self.assertEqual(wb.one_task("handoff")["status"], "open")

    def test_other_card_is_untouched(self):
        wb = Workbench()
        with wb:
            wb.send(kind="handoff", target_type="role", role="process_mgr")
            wb.send(session=SESSION2, kind="handoff", target_type="role", role="process_mgr")
            self.assertEqual(len(wb.tasks(card_id=CARD2_ID)), 1)
            self.assertEqual(len(wb.tasks(card_id=CARD_ID)), 1)
            self.assertEqual(wb.one_task(card_id=CARD_ID)["status"], "open")
            self.assertEqual(wb.one_task(card_id=CARD2_ID)["status"], "open")


# ---------------------------------------------------------------------------
# 同类重复发起：复用 / 替代 / 不抢正在做的活
# ---------------------------------------------------------------------------
class RepeatSendTest(unittest.TestCase):
    def test_identical_send_is_reused(self):
        wb = Workbench()
        with wb:
            first = wb.send(kind="handoff", target_type="role", role="process_mgr", note="同一句备注")
            second = wb.send(kind="handoff", target_type="role", role="process_mgr", note="同一句备注")
            self.assertTrue(second.get("reused"),
                            f"同签名的重复发起必须复用已有任务，实际返回：{second}")
            self.assertEqual(str(second["task_id"]), str(first["task_id"]))
            self.assertEqual(len(wb.tasks(card_id=CARD_ID)), 1, "重复发起不得产生第二条任务")

    def test_reuse_adds_no_message_and_no_audit(self):
        wb = Workbench()
        with wb:
            wb.send(kind="handoff", target_type="role", role="process_mgr", note="N")
            before_msg = len(wb.db.table("cpq_wf_message"))
            before_ev = len(wb.db.table("cpq_wf_task_event"))
            wb.send(kind="handoff", target_type="role", role="process_mgr", note="N")
            self.assertEqual(len(wb.db.table("cpq_wf_message")), before_msg,
                             "复用没有改变状态，不得再发一轮消息")
            self.assertEqual(len(wb.db.table("cpq_wf_task_event")), before_ev,
                             "复用没有改变状态，不得再写审计")

    def test_changed_target_supersedes_previous_task(self):
        wb = Workbench()
        with wb:
            old = wb.send(kind="handoff", target_type="role", role="process_mgr")
            new = wb.send(kind="handoff", target_type="user", target_user=PROC2_UID)
            self.assertFalse(new.get("reused"))
            old_row = wb.tasks(task_id=int(old["task_id"]))[0]
            new_row = wb.tasks(task_id=int(new["task_id"]))[0]
            self.assertEqual(old_row["status"], "cancelled", "换目标必须替代旧任务")
            self.assertEqual(str(old_row.get("replaced_by_task_id")), str(new["task_id"]),
                             "旧任务必须记下替代它的新任务")
            self.assertTrue(old_row.get("cancel_reason"), "替代必须写原因")
            self.assertEqual(str(new_row.get("supersedes_task_id")), str(old["task_id"]),
                             "新任务必须记下被它替代的旧任务")
            self.assertEqual(str(new.get("supersedes_task_id")), str(old["task_id"]))
            self.assertEqual(int(wb.open_row("handoff")["task_id"]), int(new["task_id"]),
                             "替代后唯一的 open 任务必须是新的那条")

    def test_supersede_records_audit_and_notifies_old_audience(self):
        wb = Workbench()
        with wb:
            old = wb.send(kind="handoff", target_type="role", role="process_mgr")
            new = wb.send(kind="handoff", target_type="user", target_user=PROC2_UID)
            cancels = [e for e in wb.events(task_id=int(old["task_id"]), action="cancel")]
            self.assertEqual(len(cancels), 1,
                             "每一次替代必须为旧任务留下 1 条 cancel 审计")
            notice = [m for m in wb.messages(msg_type="task_superseded")]
            audience = sorted(int(m["user_id"]) for m in notice)
            self.assertEqual(
                audience, [SALES_UID, PROC1_UID, PROC2_UID],
                f"被替代的旧任务必须通知原收件人 + 原发起人，实际：{audience}")
            body = notice[0]["body"] + notice[0]["title"]
            self.assertIn(cpq_wf.task_no(old["task_id"]), body, "消息必须写明被替代的是哪条任务")
            self.assertIn(cpq_wf.task_no(new["task_id"]), body, "消息必须写明替代它的是哪条任务")

    def test_new_business_version_supersedes_same_kind_only(self):
        wb = Workbench()
        with wb:
            wb.send(kind="handoff", target_type="role", role="process_mgr")
            old = wb.send(kind="tech_cost", target_type="role", role="finance_mgr",
                          payload={"result_version": "v1"})
            wb.send(kind="tech_cost", target_type="role", role="finance_mgr",
                    payload={"result_version": "v2"})
            cost = [t for t in wb.tasks(card_id=CARD_ID, task_kind="tech_cost")]
            self.assertEqual(len(cost), 2, "换版本应产生一条被替代的旧任务 + 一条新任务")
            cancelled = [t for t in cost if t["status"] == "cancelled"]
            opened = [t for t in cost if t["status"] == "open"]
            self.assertEqual(len(cancelled), 1)
            self.assertEqual(len(opened), 1)
            self.assertEqual(str(cancelled[0].get("replaced_by_task_id")), str(opened[0]["task_id"]))
            self.assertEqual(str(opened[0].get("supersedes_task_id")), str(old["task_id"]))
            self.assertEqual(wb.one_task("handoff")["status"], "open", "换版本不得动到 handoff")

    def test_same_kind_different_target_on_claimed_task_is_refused(self):
        wb = Workbench()
        with wb:
            first = wb.send(kind="handoff", target_type="role", role="process_mgr")
            wb.claim(first["task_id"], PROC1_UID)
            with self.assertRaises(cpq_wf.WfError) as caught:
                wb.send(kind="handoff", target_type="user", target_user=PROC2_UID)
            self.assertIn("PM1", str(caught.exception),
                          "拒绝时要说清是谁在手里，方便发起人找他")
            self.assertEqual(wb.one_task("handoff")["status"], "claimed",
                             "正在被人做的任务不得被替代掉")
            self.assertEqual(len(wb.tasks(card_id=CARD_ID)), 1)

    def test_same_signature_on_claimed_task_is_reused(self):
        wb = Workbench()
        with wb:
            first = wb.send(kind="handoff", target_type="role", role="process_mgr", note="N")
            wb.claim(first["task_id"], PROC1_UID)
            again = wb.send(kind="handoff", target_type="role", role="process_mgr", note="N")
            self.assertTrue(again.get("reused"), f"同签名重发必须复用，实际：{again}")
            self.assertEqual(str(again["task_id"]), str(first["task_id"]))
            self.assertEqual(wb.one_task("handoff")["status"], "claimed")

    def test_concurrent_identical_send_converges_to_one_open_task(self):
        gate = Gate()
        wb = Workbench(gate=gate)
        results, errors = {}, {}

        with wb:
            def worker(name):
                try:
                    results[name] = wb.send(kind="handoff", target_type="role",
                                            role="process_mgr", note="同时点两次")
                except Exception as exc:   # noqa: BLE001 —— 要把真实异常拿出来断言
                    errors[name] = exc

            threads = [threading.Thread(target=worker, args=(n,), name=f"race-{n}")
                       for n in ("A", "B")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=40)
            self.assertGreaterEqual(gate.arrivals, 2, "并发用例必须真的让两个线程都发起")
            self.assertEqual({k: repr(v) for k, v in errors.items()}, {},
                             "并发重复发起不得把唯一约束冲突抛给用户")
            self.assertEqual(len(wb.tasks(card_id=CARD_ID)), 1,
                             "同签名并发发起后只允许存在 1 条任务")
            self.assertEqual(wb.one_task("handoff")["status"], "open")
            self.assertTrue(any(r.get("reused") for r in results.values()),
                            f"其中一个必须走复用分支，实际：{results}")


# ---------------------------------------------------------------------------
# 原子领取与多人并发
# ---------------------------------------------------------------------------
class AtomicClaimTest(unittest.TestCase):
    def _race(self, wb, task_id, uid_by_name):
        results, errors = {}, {}

        def worker(name):
            try:
                results[name] = wb.claim(task_id, uid_by_name[name])
            except Exception as exc:   # noqa: BLE001 —— 真实异常要拿出来断言
                errors[name] = exc

        threads = [threading.Thread(target=worker, args=(n,), name=f"race-{n}")
                   for n in uid_by_name]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=40)
        return results, errors

    def test_concurrent_claim_only_one_wins(self):
        gate = Gate()
        wb = Workbench(gate=gate)
        with wb:
            sent = wb.send(kind="handoff", target_type="public")
            results, errors = self._race(wb, sent["task_id"],
                                         {"A": PROC1_UID, "B": FIN1_UID})
            self.assertEqual(gate.arrivals, 2, "并发用例必须真的让两个账号同时领取")
            self.assertEqual(len(results), 1,
                             f"并发领取必须只有一人成功，实际成功 {len(results)} 人：{results}")
            self.assertEqual(len(errors), 1,
                             f"失败方必须收到明确错误，实际错误 {len(errors)} 个")
            loser = list(errors.values())[0]
            self.assertIsInstance(loser, cpq_wf.WfError,
                                  f"失败方必须是有文案的业务错误，实际 {loser!r}")
            self.assertIn("已被他人领取", str(loser))

    def test_loser_leaves_no_side_effect(self):
        gate = Gate()
        wb = Workbench(gate=gate)
        with wb:
            sent = wb.send(kind="handoff", target_type="public")
            results, errors = self._race(wb, sent["task_id"],
                                         {"A": PROC1_UID, "B": FIN1_UID})
            self.assertEqual(gate.arrivals, 2)
            self.assertEqual(len(results), 1, "并发领取必须只有一人成功")
            winner = list(results)[0]
            winner_uid = {"A": PROC1_UID, "B": FIN1_UID}[winner]
            row = wb.one_task("handoff")
            self.assertEqual(int(row["claimed_by_user_id"]), winner_uid)
            self.assertEqual(int(wb.person()["current_owner"]), winner_uid,
                             "卡片归属必须等于唯一的赢家")
            claims = wb.events(action="claim", task_id=int(sent["task_id"]))
            self.assertEqual(len(claims), 1,
                             f"一条任务只允许 1 条 claim 审计，实际 {len(claims)} 条")
            notices = wb.messages(msg_type="task_claimed")
            self.assertEqual(len(notices), 1,
                             f"一条任务只允许 1 条领取通知，实际 {len(notices)} 条")

    def test_same_user_reclaim_is_idempotent(self):
        wb = Workbench()
        with wb:
            sent = wb.send(kind="handoff", target_type="role", role="process_mgr")
            first = wb.claim(sent["task_id"], PROC1_UID)
            self.assertFalse(first.get("already"), "首次领取的 already 必须是 False")
            try:
                again = wb.claim(sent["task_id"], PROC1_UID)
            except cpq_wf.WfError as exc:
                self.fail(f"同一个人重复领取必须幂等返回 already=True，实际抛了：{exc}")
            self.assertTrue(again.get("already"),
                            f"同一个人重复领取必须幂等返回，实际：{again}")
            self.assertEqual(again["session_id"], SESSION)
            self.assertEqual(again["task_no"], cpq_wf.task_no(sent["task_id"]))
            self.assertEqual(len(wb.events(action="claim", task_id=int(sent["task_id"]))), 1)
            self.assertEqual(len(wb.messages(msg_type="task_claimed")), 1)

    def test_handoff_claim_takes_card_ownership(self):
        wb = Workbench()
        with wb:
            sent = wb.send(kind="handoff", target_type="role", role="process_mgr")
            wb.claim(sent["task_id"], PROC1_UID)
            self.assertEqual(int(wb.person()["current_owner"]), PROC1_UID)
            self.assertEqual(wb.person()["overall_status"], "in_progress")

    def test_side_task_claim_keeps_card_ownership(self):
        wb = Workbench()
        with wb:
            sent = wb.send(kind="tech_cost", target_type="role", role="finance_mgr")
            wb.claim(sent["task_id"], FIN1_UID)
            self.assertEqual(int(wb.person()["current_owner"]), SALES_UID,
                             "支线任务被领取不得夺走报价卡片归属")
            self.assertEqual(wb.one_task("tech_cost")["status"], "claimed")

    def test_terminal_task_cannot_be_reopened(self):
        wb = Workbench()
        with wb:
            wb.db.seed("cpq_wf_task", task_id=901, card_id=CARD_ID, from_user_id=SALES_UID,
                       from_step_no=1, target_type="public", status="cancelled",
                       task_kind="handoff", source_label="已撤回", created_at="2026-01-01")
            wb.db.seed("cpq_wf_task", task_id=902, card_id=CARD_ID, from_user_id=SALES_UID,
                       from_step_no=1, target_type="public", status="completed",
                       task_kind="tech_cost", source_label="已完成", created_at="2026-01-02")
            for tid in (901, 902):
                with self.assertRaises(cpq_wf.WfError) as caught:
                    wb.claim(tid, PROC1_UID)
                self.assertIn("已关闭", str(caught.exception))
                self.assertEqual(wb.tasks(task_id=tid)[0]["status"],
                                 "cancelled" if tid == 901 else "completed")
                self.assertEqual(wb.events(action="claim", task_id=tid), [],
                                 "关掉的任务被领取失败时不得留下审计")

    def test_missing_task_is_reported(self):
        wb = Workbench()
        with wb:
            with self.assertRaises(cpq_wf.WfError) as caught:
                wb.claim(123456789, PROC1_UID)
            self.assertIn("任务不存在", str(caught.exception))

    def test_wrong_account_cannot_claim(self):
        wb = Workbench()
        with wb:
            sent = wb.send(kind="handoff", target_type="role", role="process_mgr")
            with self.assertRaises(cpq_wf.WfError) as caught:
                wb.claim(sent["task_id"], FIN1_UID)
            self.assertIn("领取权限", str(caught.exception))
            self.assertEqual(wb.one_task("handoff")["status"], "open")
            self.assertEqual(wb.events(action="claim"), [])
            self.assertEqual(int(wb.person()["current_owner"]), SALES_UID)


# ---------------------------------------------------------------------------
# 列表出口：被替代的任务在发起人自己的列表里留一行终态
# ---------------------------------------------------------------------------
class InboxVisibilityTest(unittest.TestCase):
    def _supersede(self, wb):
        old = wb.send(kind="handoff", target_type="role", role="process_mgr")
        new = wb.send(kind="handoff", target_type="user", target_user=PROC2_UID)
        return old, new

    def test_sender_sees_replaced_task_as_terminal(self):
        wb = Workbench()
        with wb:
            old, new = self._supersede(wb)
            rows = {str(r["task_id"]): r for r in cpq_wf.inbox(wb.user(SALES_UID))}
            self.assertIn(str(old["task_id"]), rows,
                          "发起人必须能在自己的任务列表里看到被替代的那条，而不是只看到它消失")
            row = rows[str(old["task_id"])]
            self.assertEqual(row["status"], "cancelled")
            self.assertEqual(row["status_label"], "已撤回")
            self.assertEqual(str(row["replaced_by_task_id"]), str(new["task_id"]))
            self.assertEqual(row["replaced_by_task_no"], cpq_wf.task_no(new["task_id"]))
            self.assertTrue(row["cancel_reason"])
            self.assertTrue(row["cancelled_at"])
            self.assertNotIn(str(new["task_id"]), rows,
                             "自己发出去的 open 任务不该出现在自己的待办里")

    def test_legacy_silent_cancel_is_not_listed(self):
        wb = Workbench()
        with wb:
            wb.send(kind="handoff", target_type="role", role="process_mgr")
            wb.db.seed("cpq_wf_task", task_id=999, card_id=CARD_ID, from_user_id=SALES_UID,
                       from_step_no=1, target_type="role", target_role_code="process_mgr",
                       status="cancelled", task_kind="handoff", created_at="2026-01-01",
                       source_label="历史静默取消", note="")
            rows = [r for r in cpq_wf.inbox(wb.user(SALES_UID)) if str(r["task_id"]) == "999"]
            self.assertEqual(rows, [], "历史静默取消的 45 条老任务不得一次性倒进用户列表")

    def test_task_row_exposes_terminal_fields(self):
        wb = Workbench()
        with wb:
            sent = wb.send(kind="handoff", target_type="role", role="process_mgr")
            row = cpq_wf.task_detail(sent["task_id"], wb.user(PROC1_UID))
            for key in ("status_label", "supersedes_task_id", "replaced_by_task_id",
                        "replaced_by_task_no", "cancel_reason", "cancelled_at"):
                self.assertIn(key, row, f"任务行必须带 {key}")
            self.assertEqual(row["status_label"], "待领取")
            self.assertIsNone(row["supersedes_task_id"])
            self.assertIsNone(row["replaced_by_task_no"])

    def test_task_detail_reports_superseded_state(self):
        wb = Workbench()
        with wb:
            old, new = self._supersede(wb)
            row = cpq_wf.task_detail(old["task_id"], wb.user(SALES_UID))
            self.assertIn("status_label", row, "任务行必须带 status_label")
            self.assertEqual(row["status"], "cancelled")
            self.assertEqual(row["status_label"], "已撤回")
            self.assertEqual(row["replaced_by_task_no"], cpq_wf.task_no(new["task_id"]))
            fresh = cpq_wf.task_detail(new["task_id"], wb.user(PROC2_UID))
            self.assertEqual(fresh["supersedes_task_id"], str(old["task_id"]))
            self.assertEqual(fresh["status_label"], "待领取")


# ---------------------------------------------------------------------------
# 静态契约：SQL 形态 / DDL / 前端渲染与计数
# ---------------------------------------------------------------------------
def read_text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def py_body(text: str, name: str) -> str:
    """取某个顶层函数的函数体（到下一个顶层 def/class 为止）。"""
    idx = text.find(f"def {name}(")
    if idx < 0:
        raise AssertionError(f"找不到函数 {name}")
    m = re.search(r"\n(?=(?:def|class|@)\s)", text[idx + 1:])
    end = idx + 1 + m.start() if m else len(text)
    return text[idx:end]


def string_literals(text: str) -> list:
    """把一段 Python 代码里的字符串字面量按出现顺序取出（SQL 是多段拼接的）。"""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch in "\"'":
            quote = ch
            triple = text.startswith(quote * 3, i)
            j = i + (3 if triple else 1)
            buf = []
            while j < n:
                if triple and text.startswith(quote * 3, j):
                    j += 3
                    break
                if not triple and text[j] == quote:
                    j += 1
                    break
                if text[j] == "\\" and not triple:
                    buf.append(text[j:j + 2])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            out.append("".join(buf))
            i = j
            continue
        i += 1
    return out


def js_block(text: str, header: str) -> str:
    """从 header（含首个 '{'）截到配对的右花括号，跳过字符串/注释/模板里的花括号。"""
    idx = text.find(header)
    if idx < 0:
        raise AssertionError(f"找不到 {header}")
    i = idx + header.index("{")
    depth, quote, j = 0, None, i
    while j < len(text):
        ch = text[j]
        if quote:
            if ch == "\\":
                j += 2
                continue
            if ch == quote:
                quote = None
            j += 1
            continue
        if ch in "'\"`":
            quote = ch
            j += 1
            continue
        if text.startswith("//", j):
            k = text.find("\n", j)
            j = len(text) if k < 0 else k
            continue
        if text.startswith("/*", j):
            k = text.find("*/", j)
            j = len(text) if k < 0 else k + 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[idx:j + 1]
        j += 1
    raise AssertionError(f"{header} 没有配对的右花括号")


def run_node(script: str) -> str:
    if not NODE:
        raise AssertionError("本机没有 node，无法对前端渲染做行为验证")
    proc = subprocess.run([NODE, "-e", script], capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise AssertionError("node 执行失败：" + (proc.stderr or proc.stdout)[-600:])
    return proc.stdout


def task_row(**over):
    row = {"task_id": "5001", "task_no": "TP-00005001", "task_kind": "handoff",
           "status": "open", "status_label": "待领取", "target_type": "role",
           "title": "测试报价", "customer": "客户A", "from_display_name": "SM1",
           "from_role_name": "销售经理", "next_step_no": 1, "next_step_name": "确认需求配置",
           "next_role_name": "工艺经理", "created_at": "2026-09-17T10:00:00",
           "session_id": SESSION, "note": "", "cancel_reason": None,
           "replaced_by_task_no": None}
    row.update(over)
    return row


class SqlContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = read_text(CPQ_WF_PY)

    def test_claim_update_is_guarded_and_result_checked(self):
        body = py_body(self.src, "claim_task")
        sql = " ".join(string_literals(body))
        m = re.search(r"UPDATE\s+cpq_wf_task\s+SET\b(.*)$", sql, re.I | re.S)
        self.assertTrue(m, "claim_task 里找不到 UPDATE cpq_wf_task")
        stmt = " ".join(m.group(1).split())
        self.assertRegex(
            stmt, r"(?i)status\s*=\s*'open'",
            "领取的 UPDATE 必须以 status='open' 为条件："
            "`UPDATE ... SET status='claimed' ... WHERE task_id = %s AND status = 'open'`")
        self.assertRegex(stmt, r"(?i)status\s*=\s*'claimed'")
        self.assertTrue(
            re.search(r"(?i)RETURNING", stmt) or "rowcount" in body,
            "领取必须检查受影响行数（rowcount）或使用 RETURNING，不能只看先前的 SELECT")

    def test_legacy_unconditional_cancel_is_gone(self):
        sql = " ".join(" ".join(string_literals(self.src)).split())
        legacy = "UPDATE cpq_wf_task SET status = 'cancelled' WHERE card_id = %s AND status = 'open'"
        self.assertNotIn(
            legacy, sql,
            "不再允许「取消该卡片全部 open 任务」的无条件 UPDATE —— 取消必须按 task_kind 收窄")

    def test_ddl_declares_supersede_columns_and_open_task_index(self):
        ddl = " ".join(py_body(self.src, "_ddl_pg").split())
        for col in ("supersedes_task_id", "replaced_by_task_id", "cancel_reason", "cancelled_at"):
            self.assertIn(col, ddl, f"_ddl_pg 必须幂等新增列 {col}")
        self.assertIn("ADD COLUMN IF NOT EXISTS", ddl)
        self.assertIn("uq_wf_task_open_kind", ddl,
                      "必须新增 (card_id, task_kind) 的部分唯一索引，作为并发的最后一道闸")
        self.assertRegex(ddl, r"(?i)UNIQUE\s+INDEX")
        self.assertRegex(ddl, r"(?i)WHERE\s+status\s*=\s*'open'")

    def test_message_types_declare_supersede_and_cancel(self):
        keys = set(cpq_wf.MSG_TYPES)
        self.assertIn("task_superseded", keys)
        self.assertIn("task_cancelled", keys)
        for old in ("task_sent", "task_received", "task_claimed"):
            self.assertIn(old, keys, f"既有消息类型 {old} 不得被改掉")


class DisplayContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = read_text(CPQ_HOME_HTML)
        cls.inbox = read_text(TECH_INBOX_JS)
        cls.msg = read_text(CPQ_MSG_JS)

    def _render_quote_card(self, row):
        block = js_block(self.home, "function taskCardHtml(t) {")
        prelude = ("function escH(v){return String(v==null?'':v);}\n"
                   "function fmtTime(){return '刚刚';}\n")
        return run_node(prelude + block +
                        f"\nprocess.stdout.write(taskCardHtml({json.dumps(row, ensure_ascii=False)}));")

    def _render_tech_card(self, row):
        block = js_block(self.inbox, "function taskCard(t) {")
        prelude = ("const esc = v => String(v==null?'':v);\n"
                   "function fmtTime(){return '刚刚';}\n")
        return run_node(prelude + block +
                        f"\nprocess.stdout.write(taskCard({json.dumps(row, ensure_ascii=False)}));")

    def test_quote_home_task_card_renders_terminal_states(self):
        replaced = self._render_quote_card(task_row(
            status="cancelled", status_label="已撤回",
            replaced_by_task_no="TP-00000009", cancel_reason="被新任务替代"))
        self.assertIn("被新任务替代", replaced, "报价首页卡片必须写清「被新任务替代」")
        self.assertIn("TP-00000009", replaced, "报价首页卡片必须给出替代它的任务编码")
        self.assertNotIn("领取并继续", replaced, "终态任务不能还挂着领取入口")
        self.assertNotIn("继续处理", replaced, "终态任务不能还挂着继续处理入口")
        plain = self._render_quote_card(task_row(status="cancelled", status_label="已撤回"))
        self.assertIn("已撤回", plain)
        self.assertNotIn("领取并继续", plain)
        claimed = self._render_quote_card(task_row(status="claimed", status_label="进行中"))
        self.assertIn("进行中", claimed)
        opened = self._render_quote_card(task_row())
        self.assertIn("待领取", opened)
        self.assertIn("领取并继续", opened)

    def test_tech_inbox_task_card_renders_terminal_states(self):
        replaced = self._render_tech_card(task_row(
            status="cancelled", status_label="已撤回",
            replaced_by_task_no="TP-00000009", cancel_reason="被新任务替代"))
        self.assertIn("被新任务替代", replaced, "技术工艺待办卡片必须写清「被新任务替代」")
        self.assertIn("TP-00000009", replaced)
        self.assertNotIn("领取并去报价", replaced)
        plain = self._render_tech_card(task_row(status="cancelled", status_label="已撤回"))
        self.assertIn("已撤回", plain)
        claimed = self._render_tech_card(task_row(status="claimed", status_label="进行中"))
        self.assertIn("进行中", claimed)
        opened = self._render_tech_card(task_row())
        self.assertIn("待领取", opened)
        self.assertIn("领取并去报价", opened)

    def test_quote_home_badge_counts_only_actionable_tasks(self):
        block = js_block(self.home, "function wfBadge() {")
        tasks = [task_row(status="open"), task_row(status="cancelled", status_label="已撤回"),
                 task_row(status="completed", status_label="已完成")]
        prelude = ("var WF = {tasks: " + json.dumps(tasks, ensure_ascii=False) + "};\n"
                   "var tabs = [{textContent: '待办任务'}];\n"
                   "var document = {querySelectorAll: function(){ return tabs; }};\n")
        out = run_node(prelude + block + "\nwfBadge();\nprocess.stdout.write(tabs[0].textContent);")
        self.assertEqual(out, "待办任务 (1)",
                         "待办计数不得把已撤回 / 已完成的任务算进去")

    def test_tech_inbox_pending_count_excludes_terminal(self):
        block = js_block(self.inbox, "function pendingCount() {")
        tasks = [task_row(status="open"), task_row(status="cancelled", status_label="已撤回"),
                 task_row(status="completed", status_label="已完成")]
        prelude = "var tasks = " + json.dumps(tasks, ensure_ascii=False) + ";\n"
        out = run_node(prelude + block + "\nprocess.stdout.write(String(pendingCount()));")
        self.assertEqual(out, "1", "技术工艺待办计数不得把终态任务算进去")

    def test_msg_icons_cover_new_message_types(self):
        block = js_block(self.msg, "var ICON = {")
        out = run_node(block + "\nprocess.stdout.write(JSON.stringify(Object.keys(ICON)));")
        keys = json.loads(out)
        self.assertIn("task_superseded", keys)
        self.assertIn("task_cancelled", keys)
        for old in ("task_sent", "task_received", "task_claimed"):
            self.assertIn(old, keys)


class SpecPinnedTest(unittest.TestCase):
    def test_spec_exists_with_required_sections(self):
        self.assertTrue(SPEC.exists(), f"缺少 Spec：{SPEC}")
        text = read_text(SPEC)
        for section in ("## 1. 背景与真实问题", "## 2. 用户角色与用户故事", "## 3. 当前流程",
                        "## 4. 目标流程", "## 5. 状态定义及状态转换", "## 6. 接口与数据契约",
                        "## 7. 正常路径", "## 8. 异常路径", "## 9. 并发与幂等要求",
                        "## 10. 刷新、重试、重复点击、服务重启", "## 11. 权限边界",
                        "## 12. 历史数据兼容", "## 13. 非目标", "## 14. 可自动化验收标准",
                        "## 15. 人工验收场景", "## 16. 不允许减少的既有能力"):
            self.assertIn(section, text, f"Spec 缺少章节：{section}")
        for anchor in ("supersedes_task_id", "replaced_by_task_id", "uq_wf_task_open_kind",
                       "status = 'open'", "task_superseded", "task_cancelled",
                       "tech_cost", "tech_new_product", "tech_cost_return"):
            self.assertIn(anchor, text, f"Spec 缺少关键契约：{anchor}")


if __name__ == "__main__":
    unittest.main()

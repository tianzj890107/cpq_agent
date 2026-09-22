"""红测：报价版本读回必须是**可 JSON 序列化**的（卡片第 5 步读历史不得 500）。

Spec：`docs/specs/packaging-quote-version-card-readback-serialization.md`

现状缺口（2026-09-22 在 34 上真跑实测，不是推断）：

  · 卡片 `e2e00a1b2c3d`（项目 `0f080b24c65d`）走完 1→6 步、第 5 步落了报价版本 1
    （`quote_version` = `{"version_no": 1, "quote_version_id": 3991596585107592505, ...}`）之后，
    `GET /wf/card/step-data?session_id=e2e00a1b2c3d&step_no=5`
    从 `{"ok": true, ... "packaging_quote_versions": []}` 变成
    **`{"ok": false, "error": "服务异常，请稍后重试"}`**（500）——同一张卡片第 3 步照旧 200，
    所以坏的是"有版本可读"这条路径，恰好就是用户要的"到最后再回去看报价"。

  · 根因在 `cpq_wf.quote_version_state()`：它把 `cpq_packaging_quote.versions()` /
    `latest()` 的**数据库原始行**原样返回，而行里 `created_at` 是 `datetime`、
    `cost_total` / `untaxed_total` / `quote_quantity` 那些 `numeric` 列是 `Decimal`；
    `cpq_suite_server.Handler._send_json()` 用的是裸 `json.dumps(obj, ensure_ascii=False)`，
    没有 `default=` 兜底 —— 本机用同形状的假行复现到的就是
    `TypeError: Object of type Decimal is not JSON serializable`。

  · 既有红测 `tests/test_packaging_quote_version_persistence_red.py` 只断言
    `packaging_quote_versions` 这个**键**在源码/页面里出现过，从来没让这份返回体真的
    过一遍 `json.dumps`，所以这条 P0 一直没被钉住。

纪律：只读源码 + 用假连接构造与 psycopg 同形状的行；不连 PG、不发 HTTP、不写任何文件。
禁止为了让红测转绿而修改本文件。
"""
from __future__ import annotations

import datetime
import decimal
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cpq_auth                      # noqa: E402
import cpq_packaging_quote as quote  # noqa: E402
import cpq_wf                        # noqa: E402

SESSION_ID = "e2e00a1b2c3d"
#: PG `numeric(18,6)` 列 → psycopg 回 `Decimal`；金额字段一个都不能漏。
MONEY_COLS = ("cost_total", "previous_cost_total", "untaxed_unit_price", "untaxed_total",
              "addon_total", "discount_amount", "tax_amount", "taxed_total")
RATE_COLS = ("gross_margin_rate", "markup_rate", "tax_rate")


def _row(**overrides):
    """造一行与 `cpq_wf_quote_version` 同形状的 PG 原始行（值类型照 psycopg 的口径）。"""
    values = {}
    for col in quote._VERSION_COLS:
        if col == "created_at":
            values[col] = datetime.datetime(2026, 9, 22, 13, 36, 26)
        elif col in MONEY_COLS or col in RATE_COLS or col == "quote_quantity":
            values[col] = decimal.Decimal("3.561953" if "total" in col or "total_cost" in col
                                         else "5000")
        elif col in ("version_no", "previous_version_no", "created_by_user_id",
                     "quote_version_id", "card_id"):
            values[col] = 1
        else:
            values[col] = "e2e00a1b2c3d" if col == "quote_session_id" else "x"
    values.update(overrides)
    return tuple(values[col] for col in quote._VERSION_COLS)


class _Cursor:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, args=()):
        self._conn.sql.append(str(sql))
        return self

    def fetchall(self):
        return list(self._conn.rows)


class _Conn:
    def __init__(self, rows):
        self.rows = list(rows)
        self.sql = []
        self.closed = False

    def cursor(self):
        return _Cursor(self)

    def close(self):
        self.closed = True


class _Base(unittest.TestCase):
    def setUp(self):
        self._real_connect = cpq_auth._connect
        self.conn = _Conn([_row()])
        cpq_auth._connect = lambda: self.conn
        self.addCleanup(lambda: setattr(cpq_auth, "_connect", self._real_connect))

    def state(self):
        return cpq_wf.quote_version_state(SESSION_ID)

    def envelope(self, state):
        """照 `cpq_suite_server.Handler._send_json()` 的原样口径构造返回体。"""
        return {"ok": True, "data": None,
                "packaging_quote_versions": state.get("versions") or [],
                "latest_quote_version": state.get("latest") or {}}


# --------------------------------------------------------------------------- #
# A 组：读回路径的返回值必须能直接 json.dumps（这就是 500 的根因）
# --------------------------------------------------------------------------- #
class TestAReadbackIsJsonSerializable(_Base):
    def test_a1_step_data_envelope_can_be_dumped(self):
        state = self.state()
        try:
            json.dumps(self.envelope(state), ensure_ascii=False)
        except TypeError as exc:                                  # 现状：Decimal / datetime
            self.fail("第 5 步返回体不能 json.dumps（线上就是 500「服务异常」）：%s" % exc)

    def test_a2_date_columns_come_back_as_text(self):
        row = (self.state().get("versions") or [{}])[0]
        value = row.get("created_at")
        self.assertNotIsInstance(value, (datetime.date, datetime.datetime),
                                 "created_at 还是 datetime；json.dumps 必炸，必须转成字符串")
        self.assertTrue(str(value or "").strip(), "created_at 不许变成空值")

    def test_a3_numeric_columns_come_back_as_numbers(self):
        row = (self.state().get("versions") or [{}])[0]
        bad = []
        for col in ("cost_total", "untaxed_unit_price", "untaxed_total", "taxed_total",
                    "quote_quantity"):
            value = row.get(col)
            if isinstance(value, decimal.Decimal):
                bad.append(col)
            elif value is not None and not isinstance(value, (int, float)):
                bad.append("%s(%s)" % (col, type(value).__name__))
        self.assertEqual([], bad,
                         "这些列还是 Decimal/非数字：%s —— 读回路径必须给 JSON 数字" % bad)

    def test_a4_latest_quote_version_has_the_same_contract(self):
        state = self.state()
        latest = state.get("latest") or {}
        self.assertTrue(latest, "有版本时 latest 不能为空")
        try:
            json.dumps(latest, ensure_ascii=False)
        except TypeError as exc:
            self.fail("latest_quote_version 不能 json.dumps：%s" % exc)


# --------------------------------------------------------------------------- #
# B 组：不得靠"改 _send_json 加 default=" 蒙过去 —— 契约在读回路径这一侧
# --------------------------------------------------------------------------- #
class TestBContractLivesInReadback(_Base):
    def test_b1_readback_result_is_dumpable_on_its_own(self):
        for key in ("versions", "latest"):
            value = self.state().get(key)
            try:
                json.dumps(value, ensure_ascii=False)
            except TypeError as exc:
                self.fail("quote_version_state() 的 %s 单独都 dump 不了：%s" % (key, exc))


# --------------------------------------------------------------------------- #
# C 组：护栏（现在就应通过，防止"修 A 顺手把别的口径改了"）
# --------------------------------------------------------------------------- #
class TestCGuards(unittest.TestCase):
    def test_c1_no_versions_is_still_an_empty_pair(self):
        real = cpq_auth._connect
        cpq_auth._connect = lambda: _Conn([])
        try:
            state = cpq_wf.quote_version_state(SESSION_ID)
        finally:
            cpq_auth._connect = real
        self.assertEqual({"versions": [], "latest": {}}, state)

    def test_c2_blank_session_does_not_touch_the_database(self):
        calls = []
        real = cpq_auth._connect

        def spy():
            calls.append(1)
            return _Conn([])

        cpq_auth._connect = spy
        try:
            state = cpq_wf.quote_version_state("   ")
        finally:
            cpq_auth._connect = real
        self.assertEqual({"versions": [], "latest": {}}, state)
        self.assertEqual([], calls, "会话号为空时不许连库")

    def test_c3_readback_path_stays_read_only(self):
        conn = _Conn([_row()])
        real = cpq_auth._connect
        cpq_auth._connect = lambda: conn
        try:
            cpq_wf.quote_version_state(SESSION_ID)
        finally:
            cpq_auth._connect = real
        self.assertTrue(conn.sql, "读回路径必须真的查了版本表")
        for sql in conn.sql:
            self.assertNotRegex(sql.upper(), r"\b(INSERT|UPDATE|DELETE|TRUNCATE)\b",
                                "读回路径只许 SELECT：%s" % sql)
        self.assertTrue(conn.closed, "连接必须归还（finally 里 close）")


if __name__ == "__main__":
    unittest.main()

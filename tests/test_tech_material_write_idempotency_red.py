"""红测：成品主数据写入幂等、编码取号并发与重复点击保护（批次 4）。

用户口径（批次 4 原文）：

  · 业务幂等键 = `project_id + result_version + action_type`；
  · 相同业务版本重复写入必须返回**原** `material_id` / `number`；
  · 用户确实需要新编码时，必须创建新版本后再写；
  · 编码取号必须使用数据库级唯一约束、锁、序列或等价原子方案；
  · 主数据表与成本表必须在**同一事务**中写入；
  · 成本表失败时主数据表不得残留孤儿记录；
  · UI 执行中禁止重复点击，但后端不能依赖按钮禁用保证幂等；
  · 写入结果必须进入项目审计与业务结果；
  · 历史已经写入的数据不迁移、不删除。

现状缺口（只读实测，均已定位；断言都是**行为与状态**，不是文本搜索）：

  · `cpq_tech_bridge.write_material`（cpq_tech_bridge.py:104-171）docstring 明说「每次调用都
    新建一个成品编码 —— 不做按名称去重」：受控假库实测，同一份成本连点两次 → 两个编码
    （92022001、92022002）、两条主数据行、两条成本行，且没有任何写入记录表。
  · 同一个函数用的是 `cpq_db.connect(readonly=False)`，而 `connect` 是
    `psycopg.connect(..., autocommit=True)`（cpq_db.py:47-70）→ 主数据 INSERT 与成本表
    INSERT **各自独立提交**。受控假库实测：注入成本表 INSERT 失败后，主数据表里留下一条
    孤儿行（material_code 有、成本没有），写日志里两条 INSERT 都不是事务写。
  · 取号是「先 SELECT 出已用编码 → 回查 `_code_taken` → 再 INSERT」，`number` 上
    「有没有唯一索引未知」（cpq_tech_bridge.py:26-29 注释）。受控假库实测：两个线程同时
    写同一个业务版本时，一个成功、另一个拿到 `UniqueViolation`（「写入主数据失败」）。
  · 服务端入口 `POST /wf/tech/material`（cpq_suite_server.py:702-708）只接
    `product_name / unit_price / breakdown / spec`，请求体里根本没有 project / 版本，
    返回体里也没有任何幂等命中字段。
  · 技术侧 `cost_flow.integration_material_write_record`（cost_flow.py:334-360）每次调用都
    `plan.material_writes.append(...)`，`MaterialWrite`（models/integration.py:264-276）
    也没有版本 / 幂等字段。

验证方式（行为为主：受控假库 + 真调 cpq_tech_bridge，技术侧子进程真跑 cost_flow）：

  · `tests/fixtures/material_write_harness.py`：在批次 2/3 的最小 SQL 引擎上补
    主数据 / 成本 / `cpq_wf_material_write` 三张表的结构与唯一索引、`pg_advisory_xact_lock`
    真互斥、按"第 N 条写语句"注入故障、"这条写是否发生在事务里"的写日志，以及
    **并发对齐点**（对齐在插入主数据那条语句之前：两个线程都会先走完"读编码 + 回查"
    再一起插入，冲突因此是必然的，不是靠调度碰运气）。
  · 前端部分是真实 JS 行为验证（node 直接跑 `crRunOp`，桩掉 `api`）：连点两次只发一次请求
    （今天已成立，作为既有能力守门），幂等命中时界面必须说"沿用已有编码"（今天是缺口）。

Spec：docs/specs/tech-material-write-idempotency-and-code-concurrency.md
"""
from __future__ import annotations

import functools
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests" / "fixtures") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import material_write_harness as M  # noqa: E402

SPEC = ROOT / "docs" / "specs" / "tech-material-write-idempotency-and-code-concurrency.md"
BRIDGE_PY = ROOT / "cpq_tech_bridge.py"
CPQ_DB_PY = ROOT / "cpq_db.py"
SUITE_PY = ROOT / "cpq_suite_server.py"
CPQ_BRIDGE_PY = ROOT / "tech_app" / "backend" / "services" / "cpq_bridge.py"
COST_FLOW_PY = ROOT / "tech_app" / "backend" / "services" / "cost_flow.py"
INTEGRATION_PY = ROOT / "tech_app" / "backend" / "models" / "integration.py"
COST_REVIEW_JS = ROOT / "tech_app" / "frontend" / "cost-review.js"

ACTION_TYPE = "material-write"
IDEM_TABLE = "cpq_wf_material_write"


def setUpModule():
    if M.LOAD_ERROR:
        raise AssertionError(M.LOAD_ERROR)
    if not M.SELFCHECK_OK:
        raise AssertionError(f"受控假库自检失败：{M.SELFCHECK_ERROR}")


def module_constants(text):
    """模块级大写常量 = 字符串字面量（用来把 DDL 里的 `{WRITE_TABLE}` 还原成真表名）。"""
    out = {}
    for m in __import__("re").finditer(
            r"^([A-Z][A-Z0-9_]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", text, __import__("re").M):
        out[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    return out


def resolved_literals(text):
    """源码里所有字符串字面量，并把 `{CONST}` 形式的插值还原成常量值。"""
    import re
    constants = module_constants(text)
    return [re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}",
                   lambda m: constants.get(m.group(1), m.group(0)), item)
            for item in M.B2.string_literals(text)]


def class_body(text, name):
    """取某个类的类体（到下一个顶层 class / def 为止）。"""
    idx = text.find(f"class {name}(")
    if idx < 0:
        idx = text.find(f"class {name}:")
    if idx < 0:
        raise AssertionError(f"找不到类 {name}")
    m = __import__("re").search(r"\n(?=(?:class|def|@)\s)", text[idx + 1:])
    return text[idx:idx + 1 + m.start()] if m else text[idx:]


def allocation_source(text):
    """`write_material` 与「取号 / 加锁 / 分配」相关的顶层函数（DDL 与别的批次不算）。"""
    parts = []
    for name in __import__("re").findall(r"\ndef\s+([A-Za-z_][\w]*)\s*\(", text):
        if name == "write_material" or any(word in name for word in ("code", "lock", "alloc")):
            parts.append(M.B2.py_body(text, name))
    return "\n".join(parts)


def frozen(rows):
    """与列顺序无关的冻结值（用于"历史数据有没有被动过"这类前后对比）。"""
    out = []
    for row in rows:
        out.append(tuple(sorted(
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
            for key, value in row.items())))
    return tuple(sorted(out))


class MaterialWriteCase(unittest.TestCase):
    def workbench(self, **kwargs):
        return M.MaterialWorkbench(**kwargs)

    def assert_single_code(self, wb, number, message=""):
        codes = wb.codes()
        self.assertEqual([number], codes, message or f"主数据里只允许一个成品编码：{codes}")
        self.assertEqual(1, len(wb.costs()), "成本配置也必须只有一行")

    def race(self, wb, calls):
        return M.race(wb.write, calls)


# ---------------------------------------------------------------------------
# 0. 假库自检（不测需求，测"测试本身可信"）
# ---------------------------------------------------------------------------
class HarnessSelfTest(MaterialWriteCase):
    def test_fake_engine_really_runs_the_bridge_sql(self):
        with self.workbench() as wb:
            original = wb.write_legacy()
            self.assertTrue(str(original.get("number") or ""), "旧签名必须还能写入")
            self.assertEqual(1, len(wb.base()), "写入必须真的落到主数据表")
            self.assertEqual(1, len(wb.costs()), "写入必须真的落到成本表")
            self.assertTrue(wb.db.statements, "假库必须记录到真实执行的 SQL")

    def test_fake_engine_enforces_number_uniqueness(self):
        with self.workbench() as wb:
            wb.write_legacy()
            with self.assertRaises(M.FakeUniqueViolation):
                wb.db.run(f"INSERT INTO {M.BASE_TABLE} (material_id, number, name) "
                          f"VALUES (%s::bigint, %s, %s)", (1, "92022001", "重复编码"))

    def test_fake_engine_rolls_back_only_its_own_transaction(self):
        with self.workbench() as wb:
            conn = M.MaterialConn(wb.db, autocommit=False)
            M.cpq_db.insert_rows(conn, M.BASE_TABLE,
                                 [{"material_id": 1, "number": "92022050", "name": "X"}])
            conn.rollback()
            self.assertEqual([], wb.base(), "回滚必须撤销这一条写入")
            conn = M.MaterialConn(wb.db, autocommit=False)
            M.cpq_db.insert_rows(conn, M.BASE_TABLE,
                                 [{"material_id": 2, "number": "92022051", "name": "Y"}])
            conn.commit()
            self.assertEqual(1, len(wb.base()), "提交后必须留下写入")

    def test_fault_injection_actually_fires(self):
        with self.workbench() as wb:
            wb.db.fail_on(M.COST_TABLE)
            with self.assertRaises(Exception) as ctx:
                wb.write_legacy()
            self.assertIn("假库注入故障", str(ctx.exception),
                          "故障注入必须命中，否则本文件的原子性用例毫无意义")


# ---------------------------------------------------------------------------
# 1. 同一业务版本 = 同一份数据（幂等命中）
# ---------------------------------------------------------------------------
class IdempotencyTest(MaterialWriteCase):
    def test_second_write_of_the_same_version_reuses_the_first_code(self):
        """同一份成本、同一个成品名连点两次：只能有一个成品编码。"""
        with self.workbench() as wb:
            first = wb.write()
            second = wb.write()
            self.assertEqual(str(first.get("number") or ""), str(second.get("number") or ""),
                             "同一个业务版本重复写入必须返回原成品编码")
            self.assertEqual(str(first.get("material_id") or ""),
                             str(second.get("material_id") or ""),
                             "同一个业务版本重复写入必须返回原 material_id")
            self.assert_single_code(wb, str(first.get("number") or ""))
            self.assertEqual(1, len(wb.writes()),
                             "每一次成品编码都必须有一条写入记录（幂等命中不新增）")

    def test_first_call_is_not_a_hit_and_the_retry_is(self):
        """首次不是命中；客户端超时后的重试必须报告「沿用已有编码」。"""
        with self.workbench() as wb:
            first = wb.write()
            second = wb.write()
            self.assertFalse(first.get("already_written"), "第一次写入不是幂等命中")
            self.assertTrue(second.get("already_written"),
                            "同一业务版本的第二次写入必须报告幂等命中（already_written）")

    def test_http_timeout_retry_does_not_create_a_second_code(self):
        """服务端其实已经写成功、客户端只看到超时：重试不能再出一个编码。"""
        with self.workbench() as wb:
            wb.write()
            rows_before = frozen(wb.base())
            retry = wb.write()
            self.assertEqual(rows_before, frozen(wb.base()),
                             "超时重试不得再插一行主数据")
            self.assertTrue(retry.get("already_written"), "超时重试必须报告幂等命中")
            self.assertEqual(["92022001"], wb.codes())

    def test_different_result_version_creates_a_new_code(self):
        """成本重算 / 数量变化后是新版本：允许而且必须新建编码。"""
        with self.workbench() as wb:
            first = wb.write(result_version="mat-v1:cost-v1:1:123.45:aaaaaaaaaa")
            second = wb.write(result_version="mat-v1:cost-v1:1:223.45:bbbbbbbbbb")
            self.assertNotEqual(str(first.get("number") or ""), str(second.get("number") or ""),
                                "不同的业务版本必须是两个成品编码")
            self.assertEqual(2, len(wb.base()), "两个版本 → 两条主数据行")
            self.assertEqual(2, len(wb.writes()), "两个版本 → 两条写入记录")
            self.assertFalse(second.get("already_written"), "新版本不是幂等命中")

    def test_same_version_in_another_project_gets_its_own_code(self):
        """幂等键带项目：两个项目的同一个版本号互不影响。"""
        with self.workbench() as wb:
            one = wb.write(project_id="proj-a")
            two = wb.write(project_id="proj-b")
            self.assertNotEqual(str(one.get("number") or ""), str(two.get("number") or ""),
                                "幂等键必须含 project_id：不同项目不能共用编码")
            self.assertFalse(two.get("already_written"))

    def test_the_same_version_always_returns_the_recorded_identity(self):
        """同一把键就是同一次业务动作：请求里的名称不同也返回原来那一行（不许悄悄改名）。

        「名称 / 规格变了算新版本」这条规则属于**调用方**（技术侧把名称与规格摘要编进
        result_version，见 Spec 6.2），不属于桥：桥只认 project_id + result_version +
        action_type。技术侧那条规则由 TechSideContractTest 验证。
        """
        with self.workbench() as wb:
            first = wb.write(product_name="便携式锂电池 PACK")
            second = wb.write(product_name="便携式锂电池 PACK（客户B）")
            self.assertEqual(str(first.get("number") or ""), str(second.get("number") or ""),
                             "同一把键必须返回同一个成品编码")
            self.assertEqual(str(first.get("name") or ""), str(second.get("name") or ""),
                             "幂等命中必须返回原来那一行的名称")
            self.assertTrue(second.get("already_written"), "第二次必须是幂等命中")

    def test_legacy_callers_without_the_key_keep_todays_behaviour(self):
        """旧调用方（不带业务幂等键）：不报错、不假装命中 —— 兼容今天的语义。"""
        with self.workbench() as wb:
            first = wb.write_legacy()
            second = wb.write_legacy()
            self.assertNotEqual(str(first.get("number") or ""), str(second.get("number") or ""),
                                "没有幂等键就没有幂等：旧调用方行为不变")
            self.assertFalse(second.get("already_written"),
                             "没有幂等键时不得报告 already_written")


# ---------------------------------------------------------------------------
# 2. 一个事务：成本表失败不留孤儿
# ---------------------------------------------------------------------------
class AtomicityTest(MaterialWriteCase):
    def test_cost_table_failure_leaves_no_orphan_base_row(self):
        """成本配置写不进去时，主数据表里不能留下一条没有成本的孤儿成品。"""
        with self.workbench(seed_codes=("92022098", "92022099")) as wb:
            before = frozen(wb.base())
            wb.db.fail_on(M.COST_TABLE)
            with self.assertRaises(Exception) as ctx:
                wb.write()
            self.assertIn("假库注入故障", str(ctx.exception), "必须因成本表写入失败而失败")
            self.assertEqual(before, frozen(wb.base()),
                             "成本表失败后主数据表必须回到调用前（不许有孤儿行）")
            self.assertEqual([], wb.costs(), "成本表里不该有任何残留")

    def test_write_record_failure_rolls_back_both_tables(self):
        """连写入记录都失败时，主数据与成本都要一起回滚。"""
        with self.workbench() as wb:
            wb.write(result_version="mat-v1:cost-v1:1:100.00:aaaaaaaaaa")
            self.assertEqual(1, len(wb.writes()),
                             "每一次成品编码都必须有一条写入记录（Spec 6.3 的新表）")
            before_base = frozen(wb.base())
            before_costs = frozen(wb.costs())
            wb.db.fail_on(IDEM_TABLE)
            with self.assertRaises(Exception) as ctx:
                wb.write(result_version="mat-v1:cost-v1:1:200.00:bbbbbbbbbb")
            self.assertIn("假库注入故障", str(ctx.exception), "必须因写入记录失败而失败")
            self.assertEqual(before_base, frozen(wb.base()), "主数据必须回滚")
            self.assertEqual(before_costs, frozen(wb.costs()), "成本必须回滚")
            self.assertEqual(1, len(wb.writes()), "写入记录也只允许留下成功的那一条")

    def test_all_writes_of_one_call_happen_inside_one_transaction(self):
        """一次写入的所有语句都必须在事务里（autocommit 裸写就是"各自独立提交"）。"""
        with self.workbench() as wb:
            wb.write()
            self.assertEqual([], wb.unprotected_writes(),
                             "主数据 / 成本 / 写入记录都必须在同一个事务里提交")
            done = " ".join(wb.protected_writes())
            self.assertIn(M.BASE_TABLE, done)
            self.assertIn(M.COST_TABLE, done)
            self.assertIn(IDEM_TABLE, done)

    def test_successful_write_leaves_base_cost_and_record(self):
        with self.workbench() as wb:
            out = wb.write()
            number = str(out.get("number") or "")
            self.assertEqual(1, len(wb.base(number=number)), "主数据必须有一行")
            cost = wb.costs(material_code=number)
            self.assertEqual(1, len(cost), "成本配置必须有一行")
            self.assertEqual(1, len(wb.writes(number=number)), "写入记录必须有一行")


# ---------------------------------------------------------------------------
# 3. 编码取号：原子、连续、不动历史
# ---------------------------------------------------------------------------
class CodeAllocationTest(MaterialWriteCase):
    def test_code_is_allocated_after_the_existing_maximum(self):
        with self.workbench(seed_codes=("92022005", "92022007")) as wb:
            out = wb.write()
            self.assertEqual("92022008", str(out.get("number") or ""),
                             "取号必须在历史最大号之后（跳过空洞也要从最大值继续）")

    def test_history_is_neither_migrated_nor_deleted(self):
        with self.workbench(seed_codes=("92022001", "92022123")) as wb:
            before = frozen(wb.base())
            wb.write()
            after = frozen(wb.base())
            self.assertTrue(set(before) <= set(after), "历史主数据行必须原样保留")
            self.assertEqual(3, len(wb.base()), "新写入只能新增一行，不能覆盖历史")
            self.assertEqual([], [sql for sql in wb.db.statements
                                  if sql.upper().startswith("DELETE")],
                             "任何情况下都不许 DELETE 主数据")

    def test_allocation_is_not_a_length_based_guess(self):
        """历史里有非本规则编码时不能数错（`max(number)` 字符串比较会取到不相干的号）。"""
        with self.workbench(seed_codes=("92022001", "92022999", "XXXX-9")) as wb:
            with self.assertRaises(Exception):
                # 92022999 已经用掉了 999 —— 三位流水用尽，必须明确报错而不是插入重复/越界号
                wb.write()
            numbers = wb.codes()
            self.assertEqual(sorted(["92022001", "92022999", "XXXX-9"]), numbers,
                             "流水用尽时必须报错，不能污染主数据")


# ---------------------------------------------------------------------------
# 4. 并发：后端不能依赖按钮禁用
# ---------------------------------------------------------------------------
class ConcurrencyTest(MaterialWriteCase):
    def test_two_concurrent_writers_of_the_same_version_produce_one_code(self):
        """两个人（或两次重试）同时点「写入数据库」：只能有一个成品编码，且都不许报错。"""
        with self.workbench(gate=M.Gate(parties=2)) as wb:
            results, errors = self.race(wb, [dict(), dict()])
            self.assertEqual([], [f"{type(e).__name__}: {e}" for e in errors],
                             "同一业务版本的并发写入不许把其中一个变成「写入主数据失败」")
            self.assertEqual(2, len(results), f"两次调用都必须有结果：{results}")
            numbers = {str(out.get("number") or "") for out in results}
            self.assertEqual(1, len(numbers), f"并发写同一版本必须收敛到同一个编码：{numbers}")
            self.assertGreaterEqual(
                sum(1 for out in results if out.get("already_written")), 1,
                f"两次里至少有一次必须报告幂等命中（另一次才是真正写入的）：{results}")
            self.assert_single_code(wb, numbers.pop())

    def test_two_concurrent_writers_of_different_versions_both_succeed(self):
        """两个不同版本并发：两个编码都要成功，谁都不许因为撞号而失败。"""
        with self.workbench(gate=M.Gate(parties=2)) as wb:
            results, errors = self.race(wb, [
                dict(result_version="mat-v1:cost-v1:1:111.11:aaaaaaaaaa"),
                dict(result_version="mat-v1:cost-v1:1:222.22:bbbbbbbbbb")])
            self.assertEqual([], [f"{type(e).__name__}: {e}" for e in errors],
                             "不同版本的并发写入必须都成功")
            self.assertEqual(2, len({str(out.get("number") or "") for out in results}),
                             "不同版本必须拿到两个不同的编码")
            self.assertEqual(2, len(wb.base()), "两条主数据行")

    def test_four_concurrent_writers_never_share_a_code(self):
        with self.workbench(gate=M.Gate(parties=4)) as wb:
            calls = [dict(result_version=f"mat-v1:cost-v1:1:{100 + index}.00:hash{index}")
                     for index in range(4)]
            results, errors = self.race(wb, calls)
            self.assertEqual([], [f"{type(e).__name__}: {e}" for e in errors],
                             "并发写入不许有失败")
            numbers = sorted(str(out.get("number") or "") for out in results)
            self.assertEqual(4, len(set(numbers)), f"四个并发写入必须四个不同编码：{numbers}")
            self.assertEqual(4, len(wb.base()), "四条主数据行，一条都不能少")

    def test_database_level_mechanism_is_used_for_code_allocation(self):
        """取号必须落在数据库级机制上（唯一约束 / 锁 / 序列），而不是"先查再用"。"""
        body = M.B2.read_text(BRIDGE_PY)
        source = allocation_source(body)
        self.assertTrue(source.strip(), "找不到 write_material / 取号相关函数")
        sql = " ".join(M.B2.string_literals(source)).lower()
        tokens = {
            "advisory 锁": "pg_advisory",
            "ON CONFLICT": "on conflict",
            "序列": "nextval",
            "唯一约束": "unique",
        }
        hits = [name for name, token in tokens.items() if token in sql]
        self.assertTrue(hits, "取号 + 插入必须使用数据库级原子方案（advisory 锁 / "
                              "ON CONFLICT / 序列 / 唯一约束）——今天一样都没有："
                              "单进程里的「先查再用」挡不住两个进程同时取号")


# ---------------------------------------------------------------------------
# 5. 返回体与请求契约
# ---------------------------------------------------------------------------
class ResponseContractTest(MaterialWriteCase):
    def test_response_carries_the_business_idempotency_key(self):
        with self.workbench() as wb:
            out = wb.write()
            expected = f"{M.DEFAULT_PROJECT}|{M.DEFAULT_VERSION}|{ACTION_TYPE}"
            self.assertEqual(expected, str(out.get("idempotency_key") or ""),
                             "返回体必须带业务幂等键 project_id|result_version|action_type")
            self.assertEqual(M.DEFAULT_VERSION, str(out.get("result_version") or ""),
                             "返回体必须带本次业务版本")
            self.assertFalse(out.get("already_written"), "第一次写入不是幂等命中")

    def test_write_record_stores_the_key_and_the_version(self):
        with self.workbench() as wb:
            out = wb.write()
            rows = wb.writes(number=str(out.get("number") or ""))
            self.assertEqual(1, len(rows))
            row = rows[0]
            for column in ("idempotency_key", "project_id", "result_version", "action_type",
                           "material_id", "number", "name", "unit_price", "status"):
                self.assertIn(column, row, f"写入记录必须存 {column}")
            self.assertEqual(M.DEFAULT_PROJECT, str(row.get("project_id")))
            self.assertEqual(M.DEFAULT_VERSION, str(row.get("result_version")))
            self.assertEqual(ACTION_TYPE, str(row.get("action_type")))

    def test_service_exposes_project_and_version_on_the_http_route(self):
        """服务端入口必须把 project / 版本从请求体接到写主数据命令上。"""
        suite = M.B2.read_text(SUITE_PY)
        index = suite.find('"/wf/tech/material"')
        self.assertGreater(index, 0, "找不到 /wf/tech/material 路由")
        window = suite[index:index + 800]
        self.assertIn("project_id", window, "服务端必须把 project_id 接到写主数据命令上")
        self.assertIn("result_version", window, "服务端必须把 result_version 接到写主数据命令上")

    def test_bridge_client_sends_project_and_version(self):
        client = M.B2.read_text(CPQ_BRIDGE_PY)
        body = M.B2.py_body(client, "write_material")
        self.assertIn("project_id", body, "技术侧桥接必须把 project_id 发出去")
        self.assertIn("result_version", body, "技术侧桥接必须把 result_version 发出去")

    def test_material_write_model_has_version_and_hit_fields(self):
        model = M.B2.read_text(INTEGRATION_PY)
        body = class_body(model, "MaterialWrite")
        for field in ("result_version", "already_written", "idempotency_key"):
            self.assertIn(field, body, f"MaterialWrite 必须新增 {field}（带默认值，老数据兼容）")


# ---------------------------------------------------------------------------
# 6. DDL 契约：新增表 + 唯一索引 + 幂等写法
# ---------------------------------------------------------------------------
class DdlContractTest(MaterialWriteCase):
    def test_ddl_declares_the_write_table_and_its_unique_key(self):
        import re
        src = M.B2.read_text(BRIDGE_PY)
        literals = resolved_literals(src)
        create = [text for text in literals
                  if IDEM_TABLE in text and re.search(r"(?i)CREATE\s+TABLE", text)]
        self.assertTrue(create, f"必须新增写入记录表 {IDEM_TABLE}（CREATE TABLE）")
        table_ddl = " ".join(create)
        for column in ("idempotency_key", "project_id", "result_version", "action_type",
                       "material_id", "number", "name", "unit_price", "status",
                       "created_at"):
            self.assertIn(column, table_ddl, f"写入记录表必须声明 {column}")
        self.assertRegex(table_ddl, r"(?i)CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS",
                         "建表必须幂等（老库升级不需要人工步骤）")
        unique = [text for text in literals if IDEM_TABLE in text and "UNIQUE" in text.upper()]
        self.assertTrue(unique, "业务幂等键必须有数据库唯一约束（UNIQUE）")
        for text in literals:
            if re.search(r"(?i)CREATE\s+TABLE", text):
                self.assertRegex(text, r"(?i)IF\s+NOT\s+EXISTS",
                                 f"建表语句必须幂等：{text[:120]}")
            if re.search(r"(?i)CREATE\s+(UNIQUE\s+)?INDEX", text):
                self.assertRegex(text, r"(?i)IF\s+NOT\s+EXISTS",
                                 f"建索引语句必须幂等：{text[:120]}")

    def test_connect_supports_an_explicit_transaction_connection(self):
        src = M.B2.read_text(CPQ_DB_PY)
        body = M.B2.py_body(src, "connect")
        self.assertIn("autocommit", body, "cpq_db.connect 必须允许调用方显式要事务连接")
        m = __import__("re").search(r"def connect\((?P<sig>[^)]*)\)", src)
        self.assertTrue(m, "找不到 cpq_db.connect 的签名")
        signature = m.group("sig")
        self.assertIn("autocommit", signature, "事务能力必须通过参数暴露")
        self.assertRegex(signature, r"autocommit[^,)]*=\s*True",
                         "默认值必须保持 True：导入等既有调用方行为不变")


# ---------------------------------------------------------------------------
# 7. 技术侧：项目审计与业务结果
# ---------------------------------------------------------------------------
CHILD = r'''
import json
import os
import sys
import types

data_dir, root = sys.argv[1], sys.argv[2]
os.environ["DATA_DIR"] = data_dir
os.environ["AUTH_ENABLED"] = "false"
sys.path.insert(0, root)
try:
    import dotenv  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("dotenv")
    _stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = _stub

from tech_app.backend.models.cost import CostAnalysis, CostItem
from tech_app.backend.models.integration import IntegrationPlan
from tech_app.backend.models.ir import DesignIR
from tech_app.backend.models.process import ProcessPlan, ProcessStep
from tech_app.backend.services import cost_flow, cpq_bridge, integration
from tech_app.backend.storage import store

ACTOR = {"username": "fin1", "display_name": "财务一", "role": "finance_manager",
         "user_id": "400", "role_code": "finance_mgr"}
PID = store.create_project(source_filename="material.dxf", source_bytes=b"x",
                           note="batch4 material-write probe", owner="tester")
CALLS = []
SERVER = {}


def amount(value):
    return {"name": "材料费", "category": "material", "quantity": 1,
            "unit_price": value, "amount": value}


PART = "P-001"
store.save_ir(PID, DesignIR(device_name="便携式锂电池 PACK",
                            design_intent="批次 4 技术侧写库探针",
                            parts=[{"part_id": PART, "name": "上壳", "quantity": 1}]
                            ).model_dump(), stage="parsed")
store.save_cost(PID, PART, {"part_id": PART, "items": [amount(100.0)]})

plan = IntegrationPlan(project_id=PID)
plan.cost = CostAnalysis(items=[CostItem(**amount(100.0))])
plan.process = ProcessPlan(steps=[ProcessStep(no=1, name="整机总装", hours=0.07)])
plan.quantity = 1
integration.save_plan(PID, plan, "tester")
cost_flow.confirm_review(PID, ACTOR)


def fake_write_material(token, product_name, unit_price, breakdown=None, spec="", *extra,
                        **kwargs):
    """假的服务端：只认显式传进来的业务幂等键；没有键就每次新建（= 今天的真实行为）。

    第 6、7 个参数（project_id / result_version）允许按位置传也允许按关键字传 ——
    调用约定不是本批要卡的验收点。
    """
    project_id = kwargs.get("project_id")
    result_version = kwargs.get("result_version")
    if project_id is None and len(extra) >= 1:
        project_id = extra[0]
    if result_version is None and len(extra) >= 2:
        result_version = extra[1]
    project_id = str(project_id or "")
    result_version = str(result_version or "")
    CALLS.append({"product_name": product_name, "unit_price": unit_price,
                  "project_id": project_id, "result_version": result_version})
    key = (project_id, result_version)
    if project_id and result_version and key in SERVER:
        row = dict(SERVER[key])
        row["already_written"] = True
        return row
    number = "92022%03d" % (len(SERVER) + 1)
    row = {"material_id": str(100 + len(SERVER)), "number": number, "name": product_name,
           "material_unit_price": unit_price,
           "tables": ["md_clm_material_base_info", "md_clm_material_cost_cnf"], "by": "fin1",
           "already_written": False, "result_version": result_version,
           "idempotency_key": "|".join([project_id, result_version, "material-write"])}
    if project_id and result_version:
        SERVER[key] = dict(row)
    return row


cpq_bridge.write_material = fake_write_material

def numbers_of(plan):
    return [str(getattr(item, "number", "") or "") for item in (plan.material_writes or [])]


out = {"pid": PID}
try:
    first = cost_flow.write_material(PID, ACTOR, token="t", product_name="便携式锂电池 PACK")
    second = cost_flow.write_material(PID, ACTOR, token="t", product_name="便携式锂电池 PACK")
    out["ok"] = True
    out["first"] = first.get("written") or {}
    out["second"] = second.get("written") or {}
    out["numbers_after_two"] = numbers_of(integration.load_plan(PID))
    # 改名 = 新的业务版本：允许（而且必须）新建一个编码。
    third = cost_flow.write_material(PID, ACTOR, token="t",
                                     product_name="便携式锂电池 PACK（客户B）")
    out["third"] = third.get("written") or {}
    out["numbers_after_rename"] = numbers_of(integration.load_plan(PID))
except Exception as exc:                                   # noqa: BLE001
    out["ok"] = False
    out["error_type"] = type(exc).__name__
    out["error"] = str(exc)[:300]
loaded = integration.load_plan(PID)
out["material_writes"] = [w.model_dump() for w in (loaded.material_writes if loaded else [])]
out["audit"] = [dict(row) for row in store.list_audit(PID)]
out["calls"] = CALLS
print(json.dumps(out, ensure_ascii=False, default=str))
'''


class TechSideContractTest(MaterialWriteCase):
    """技术侧 2.3「写入数据库」：幂等命中必须写进审计与业务结果，编码不许重复记。"""

    def run_child(self):
        data_dir = tempfile.mkdtemp(prefix="cpq-material-data-")
        script_dir = tempfile.mkdtemp(prefix="cpq-material-script-")
        script = pathlib.Path(script_dir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        env = dict(os.environ)
        env["DATA_DIR"] = data_dir
        env["AUTH_ENABLED"] = "false"
        env["PYTHONPATH"] = str(ROOT)
        completed = subprocess.run([sys.executable, str(script), data_dir, str(ROOT)],
                                   capture_output=True, text=True, env=env, cwd=str(ROOT))
        self.assertEqual(0, completed.returncode, f"子进程失败：{completed.stderr[-800:]}")
        return json.loads(completed.stdout.strip().splitlines()[-1])

    @functools.lru_cache(maxsize=1)
    def child(self):
        return self.run_child()

    def test_tech_side_sends_project_and_result_version(self):
        out = self.child()
        self.assertTrue(out["ok"], f"技术侧写入不该失败：{out.get('error')}")
        call = out["calls"][0]
        self.assertEqual(out["pid"], str(call.get("project_id") or ""),
                         "技术侧必须把项目号发给服务端，否则组不出业务幂等键")
        self.assertTrue(str(call.get("result_version") or ""),
                        "技术侧必须把本次成本结果的版本号发给服务端")

    def test_repeat_write_reuses_the_recorded_code_and_marks_the_hit(self):
        out = self.child()
        self.assertTrue(out["ok"], f"技术侧写入不该失败：{out.get('error')}")
        first, second = out["first"], out["second"]
        self.assertEqual(str(first.get("number") or ""), str(second.get("number") or ""),
                         "同一版本重复写入必须沿用同一个成品编码")
        self.assertFalse(first.get("already_written"), "第一次不是命中")
        self.assertTrue(second.get("already_written"), "第二次必须是幂等命中")

    def test_business_result_lists_the_code_once(self):
        out = self.child()
        numbers = out.get("numbers_after_two") or []
        self.assertEqual(1, len(numbers), f"同一版本只能有一条成品编码记录：{numbers}")
        self.assertEqual(str(out["first"].get("number") or ""), numbers[0],
                         "业务结果里的编码必须与服务端一致")

    def test_renaming_the_product_is_a_new_version_with_its_own_code(self):
        """改名再写 = 新版本：必须真的产生第二个编码，而不是静默沿用旧行。"""
        out = self.child()
        numbers = out.get("numbers_after_rename") or []
        self.assertEqual(2, len(numbers), f"改名后必须有两个编码：{numbers}")
        third = out.get("third") or {}
        self.assertNotEqual(str(out["first"].get("number") or ""),
                            str(third.get("number") or ""), "改名必须新建编码")
        self.assertFalse(third.get("already_written"), "改名不是幂等命中")
        self.assertTrue(str(third.get("result_version") or ""),
                        "改名后的新编码也要带自己的业务版本")

    def test_audit_records_the_idempotent_hit(self):
        out = self.child()
        rows = [row for row in out["audit"]
                if str(row.get("action") or "") == "cost_review_material_write"]
        self.assertEqual(3, len(rows), f"每一次点击都要留审计（写入 / 重复 / 改名）：{out['audit']}")
        # 项目审计行的正文键是 `detail`（store.audit 的既有形状）。
        payloads = [row.get("detail") or row.get("payload") or {} for row in rows]
        self.assertEqual([False, True, False],
                         [bool(item.get("already_written")) for item in payloads],
                         "只有同一版本的第二次点击才是幂等命中")
        self.assertEqual(str(out["first"].get("number") or ""),
                         str(payloads[1].get("number") or ""),
                         "命中的审计必须指向原来那个编码")
        self.assertNotEqual(str(payloads[2].get("number") or ""),
                            str(payloads[0].get("number") or ""),
                            "改名后的审计必须指向新编码")


# ---------------------------------------------------------------------------
# 8. 前端：执行中不重复提交 + 幂等命中要说清楚
# ---------------------------------------------------------------------------
CR_JS_PRELUDE = """
let crBusy = false;
let crData = null;
const crTaskId = '9001';
const calls = [];
const says = [];
const logs = [];
const RESPONSE = __RESPONSE__;
const api = async (url, opts) => {
  calls.push({ url: url, body: JSON.parse(opts.body) });
  return RESPONSE;
};
const card = { log: (lines) => logs.push(lines), done: function () {}, done_flag: true };
function crCard() { return card; }
function crRender() {}
function crStatus() {}
function crPublishTask() {}
function crSay(text) { says.push(text); }
function crToast() {}
function $cr() { return { value: '' }; }
function crUrl(suffix) { return '/api/projects/P1/cost-review' + suffix; }
"""

CR_JS_TAIL = """
(async () => {
  const first = crRunOp('material-write');
  const second = crRunOp('material-write');
  const results = await Promise.all([first, second]);
  process.stdout.write(JSON.stringify({
    calls: calls.length,
    bodies: calls.map((item) => item.body),
    says: says,
    logs: logs,
    results: results,
  }));
})();
"""


class CostReviewFrontendTest(MaterialWriteCase):
    def run_cr_run_op(self, response):
        src = M.B2.read_text(COST_REVIEW_JS)
        block = M.B2.js_block(src, "async function crRunOp(kind) {")
        prelude = CR_JS_PRELUDE.replace("__RESPONSE__", json.dumps(response, ensure_ascii=False))
        script = prelude + "\n" + block + "\n" + CR_JS_TAIL
        return json.loads(M.B2.run_node(script))

    def test_double_click_sends_only_one_request(self):
        """执行中重复点击不许发第二个请求（今天的 crBusy 守门必须保持）。"""
        out = self.run_cr_run_op({"written": {"number": "92022001", "name": "X",
                                              "material_unit_price": 123.45,
                                              "tables": ["md_clm_material_base_info"],
                                              "already_written": False}})
        self.assertEqual(1, out["calls"], f"连点两次只能发一次请求：{out}")

    def test_idempotent_hit_is_shown_as_reusing_the_existing_code(self):
        """幂等命中时界面必须说「沿用已有编码」，不能让人以为又新建了一个。"""
        out = self.run_cr_run_op({"written": {
            "number": "92022007", "name": "便携式锂电池 PACK",
            "material_unit_price": 123.45,
            "tables": ["md_clm_material_base_info", "md_clm_material_cost_cnf"],
            "already_written": True, "result_version": "mat-v1:cost-v1:1:123.45:0f1e2d3c4b",
            "idempotency_key": "proj|mat-v1|material-write"}})
        said = " ".join(out["says"]) + " " + json.dumps(out["logs"], ensure_ascii=False)
        self.assertIn("92022007", said, f"界面必须显示成品编码：{said}")
        self.assertIn("沿用", said, f"幂等命中必须说清楚是沿用已有编码：{said}")


if __name__ == "__main__":
    unittest.main()

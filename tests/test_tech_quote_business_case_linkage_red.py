"""红测：报价—技术—财务—报告统一业务实例关联与安全恢复（批次 6）。

用户口径（批次 6 原文要点）：

  · 报价 session_id、card_id、task_id、技术 project_id、Agent session、报告版本之间
    今天只能靠**散落字段碰巧对上**，要引入稳定的 `business_case_id`：报价创建时产生或
    绑定 → 发起技术支线时传入 → 技术项目元数据持久化 → 成本 / 报告 / 回传任务全部携带
    → Agent 会话事件携带 → 历史项目恢复时可追溯原报价 → 原 task 被取消 / 删除 / 替换后
    仍能找到业务实例 → 不拿技术项目号冒充报价会话号 → 历史无实例号时只读兼容 + 安全回填
    → 不允许自动猜测多个候选；
  · 重点修「找不到原报价就自动新建一张卡片」：唯一匹配自动关联；多候选必须把候选交给
    有权限的人选；无候选必须先被明确问「要不要新建」，**不得静默新建**；人工新建必须记录
    `recovered_from_project_id` 与恢复原因。

现状缺口（只读实测，均已定位；本文件断言的都是**行为与状态**，不是文本搜索）：

  · 落点只有三条**散落线索**：`source_task_id` → 任务那张卡片（`cpq_tech_bridge.py:824-827`，
    `linked_by='task'`）、`source_session_id` 命中卡片（`:828-833`，`linked_by='session'`）、
    都没有就**静默新建**一条真实会话（`:834-837` 生成新会话号 → `ensure_quote_session`，
    `linked_by='new_session'`）。受控假库实测：清掉线索后一次回传直接多出一张卡片
    （卡片数 1 → 2），现场只留一句「已新建一张」。
  · 没有任何跨系统、跨重建的实例号：`cpq_wf_card` 只有 `session_id`（本地实测：卡片字典
    里**没有** `business_case_id` 键）；`cpq_wf_handoff` 同样没有实例号列。
  · `cpq_case_link.py` 不存在 —— 判定（`decide`）、解析（`resolve`）与
    `CaseLinkError` 三个唯一入口都还没有，所以"多候选 / 无候选"连出口都没有。
  · 建卡时（`cpq_wf.sync_card`，cpq_wf.py:524-575）不产生实例号；`_CARD_COLS`
    （cpq_wf.py:493）里也没有它。
  · 技术侧项目 meta 没有 `business_case` 文档：`store` 里没有 `save_business_case` /
    `load_business_case`（storage/store.py 无这段）。
  · 技术侧回传包与 Agent 会话轮次都不带实例号：`cost_flow.integration_quote_result`
    （cost_flow.py:222-...）返回体里没有 `business_case_id`；`main._save_project_chat_turn`
    （main.py:2081-2090）写的每条 user / assistant 记录也没有这个键。

验证方式（行为为主）：

  · `tests/fixtures/wf_handoff_harness.py`（批次 3 的受控假库）真跑 `cpq_wf` /
    `cpq_tech_bridge` / 新的 `cpq_case_link` 发出的 SQL：事务与撤销日志、handoff_key 唯一
    约束、写日志都在，测试绝不连线上 PG。
  · `decide` 是纯函数：同输入同输出、不改入参、不碰数据库（直接调用断言）。
  · 落点行为一律用「卡片数 / 交接数 / handoff 行 / 卡片实例号」的前后状态对比，
    不用"源码里有没有那个字符串"代替。
  · 技术侧（meta / 回传包 / Agent 会话轮次）真跑子进程 + 临时 DATA_DIR，不碰线上数据。

Spec：docs/specs/tech-quote-business-case-linkage.md
"""
from __future__ import annotations

import copy
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tests" / "fixtures") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests" / "fixtures"))

import wf_handoff_harness as H  # noqa: E402
import cpq_auth  # noqa: E402
import cpq_tech_bridge  # noqa: E402
import cpq_wf  # noqa: E402

SPEC = ROOT / "docs" / "specs" / "tech-quote-business-case-linkage.md"
BC = "bc_0f1e2d3c4b5a"
BC2 = "bc_aabbccddeeff"
BC_RE = re.compile(r"^bc_[0-9a-f]{12}$")
RETURN_KEYS = ("code", "quote_session_id", "linked_by", "business_case_id", "candidates",
               "recovered_from_project_id", "recovery_reason")


def setUpModule():
    if H.LOAD_ERROR:
        raise AssertionError(H.LOAD_ERROR)
    if not H.SELFCHECK_OK:
        raise AssertionError(f"受控假库自检失败：{H.SELFCHECK_ERROR}")


# ---------------------------------------------------------------------------
# 契约入口：模块 / 函数 / 异常缺失时给出明确缺口，而不是 ImportError
# ---------------------------------------------------------------------------
def case_link():
    try:
        import cpq_case_link
    except ImportError as exc:                              # pragma: no cover - 缺口路径
        raise AssertionError(
            "缺少业务实例关联模块 cpq_case_link.py（Spec §6）：decide / resolve / "
            "CaseLinkError 是本批唯一的落点判定入口，散落线索不许留在 cpq_tech_bridge 里"
        ) from exc
    return cpq_case_link


def case_link_error():
    module = case_link()
    err = getattr(module, "CaseLinkError", None)
    if not (isinstance(err, type) and issubclass(err, Exception)):
        raise AssertionError("cpq_case_link 必须定义 CaseLinkError(code, candidates=[], message='')")
    return err


def decide_func():
    module = case_link()
    fn = getattr(module, "decide", None)
    if not callable(fn):
        raise AssertionError("cpq_case_link.decide(candidates, *, business_case_id='', "
                             "tech_project_id='', create_new=False, create_reason='') 不存在")
    if not accepts(fn, ("candidates", "business_case_id", "tech_project_id",
                        "create_new", "create_reason")):
        raise AssertionError(
            "decide 的签名必须是 candidates + 关键字 "
            f"business_case_id / tech_project_id / create_new / create_reason：{inspect.signature(fn)}")
    return fn


def resolve_func():
    module = case_link()
    fn = getattr(module, "resolve", None)
    if not callable(fn):
        raise AssertionError("cpq_case_link.resolve(conn, *, business_case_id='', "
                             "source_task_id='', source_session_id='', tech_project_id='', "
                             "create_new=False, create_reason='', user=None) 不存在")
    if not accepts(fn, ("business_case_id", "source_task_id", "source_session_id",
                        "tech_project_id", "create_new", "create_reason", "user")):
        raise AssertionError(
            "resolve 必须接受 business_case_id / source_task_id / source_session_id / "
            f"tech_project_id / create_new / create_reason / user：{inspect.signature(fn)}")
    return fn


def accepts(func, names):
    """函数是否接受这组关键字（含 **kwargs 的情形）。"""
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):                         # pragma: no cover - 内建
        return False
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return True
    return all(name in params for name in names)


def candidate(session_id, *, card_id=1, linked_by="task", title=""):
    return {"quote_session_id": session_id, "card_id": card_id,
            "linked_by": linked_by, "title": title}


def call_decide(candidates, **kwargs):
    kwargs.setdefault("business_case_id", "")
    kwargs.setdefault("tech_project_id", "")
    kwargs.setdefault("create_new", False)
    kwargs.setdefault("create_reason", "")
    return decide_func()(candidates, **kwargs)


def call_resolve(conn, *, business_case_id="", source_task_id="", source_session_id="",
                 tech_project_id="", create_new=False, create_reason="", user=None):
    return resolve_func()(
        conn, business_case_id=business_case_id, source_task_id=source_task_id,
        source_session_id=source_session_id, tech_project_id=tech_project_id,
        create_new=create_new, create_reason=create_reason, user=user)


def frozen(rows):
    out = []
    for row in rows:
        out.append(tuple(sorted(
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
            for key, value in row.items())))
    return tuple(sorted(out))


def state(wb):
    """整库快照：判断"这次判定的结局到底留下了什么"。"""
    return {
        "cpq_wf_card": frozen(wb.cards()),
        "cpq_wf_card_step": frozen(wb.rows("cpq_wf_card_step")),
        "cpq_wf_task": frozen(wb.tasks()),
        "cpq_wf_task_event": frozen(wb.events()),
        "cpq_wf_message": frozen(wb.messages()),
        "cpq_wf_handoff": frozen(wb.handoffs()),
    }


def diff_state(before, after):
    return "、".join(name for name in before if before[name] != after[name])


def add_card(wb, card_id, session_id, *, business_case_id="", overall_status="draft",
             title="第二张同实例卡片", customer="客户A"):
    return wb.db.seed("cpq_wf_card", card_id=card_id, session_id=session_id,
                      assistant_type="quote", title=title, customer=customer,
                      project_name="锂电池项目", current_step=1,
                      overall_status=overall_status, creator_user_id=H.SALES_UID,
                      current_owner=H.SALES_UID, created_at="2026-09-17T09:00:00",
                      updated_at="2026-09-17T09:00:00",
                      business_case_id=business_case_id)


def send_to_quote(wb, *, kind="cost_to_quote", uid=H.FIN_UID, source_task_id="9001",
                  result_version=None, result=None, report=None,
                  source_session_id=H.QUOTE_SESSION, tech_project=H.TECH_PROJECT,
                  business_case_id="", create_new=False, create_reason=""):
    """真调回传命令（带上批次 6 新增的三个关键字参数）。"""
    if not accepts(cpq_tech_bridge.send_to_quote,
                   ("business_case_id", "create_new", "create_reason")):
        raise AssertionError(
            "cpq_tech_bridge.send_to_quote 必须新增关键字参数 business_case_id / "
            "create_new / create_reason（Spec §6）")
    version = result_version if result_version is not None else (
        "report-v2" if kind == "report_to_quote" else "cost-v1:1:123.45")
    return cpq_tech_bridge.send_to_quote(
        wb.user(uid), tech_project, "便携式锂电池 PACK", "客户A", "锂电池项目",
        "已确认，请继续报价", str(source_task_id or ""),
        result if result is not None else H.cost_result(), source_session_id, report, kind,
        version, business_case_id=business_case_id,
        create_new=bool(create_new), create_reason=create_reason)


# ---------------------------------------------------------------------------
# 1. Spec 与假库自检
# ---------------------------------------------------------------------------
class SpecPinnedTest(unittest.TestCase):
    def test_spec_exists_and_pins_the_contract(self):
        self.assertTrue(SPEC.exists(), f"缺少 Spec：{SPEC}")
        text = SPEC.read_text(encoding="utf-8")
        for token in ("business_case_id", "cpq_case_link", "decide", "resolve",
                      "CaseLinkError", "multiple_candidates", "no_candidate",
                      "create_reason", "recovered_from_project_id", "recovery_reason",
                      "linked_by"):
            self.assertIn(token, text, f"Spec 必须钉死 {token}")


class HarnessSelfTest(unittest.TestCase):
    def test_harness_is_loaded_and_selfchecked(self):
        self.assertTrue(H.SELFCHECK_OK, H.SELFCHECK_ERROR)

    def test_scenario_basis_is_one_card_one_claimed_source_task(self):
        with H.Workbench() as wb:
            self.assertEqual(1, len(wb.cards()))
            self.assertEqual(H.QUOTE_SESSION, str(wb.card()["session_id"]))
            self.assertEqual("claimed", str(wb.source_task()["status"]))


# ---------------------------------------------------------------------------
# 2. decide：纯函数，四种结局
# ---------------------------------------------------------------------------
class DecidePureFunctionTest(unittest.TestCase):
    def assert_contract(self, out):
        self.assertIsInstance(out, dict, "decide 必须返回 dict")
        missing = [key for key in RETURN_KEYS if key not in out]
        self.assertEqual([], missing, f"decide 返回体缺键：{missing}（Spec §6）")
        self.assertIsInstance(out["candidates"], list, "candidates 必须是列表")
        self.assertNotIsInstance(out["recovered_from_project_id"], type(None),
                                 "未命中时也要填空串，不是 None")

    def test_unique_case_candidate_is_linked_by_case(self):
        out = call_decide([candidate(H.QUOTE_SESSION, linked_by="case")],
                          business_case_id=BC, tech_project_id=H.TECH_PROJECT)
        self.assert_contract(out)
        self.assertEqual("linked", out["code"])
        self.assertEqual("case", out["linked_by"])
        self.assertEqual(H.QUOTE_SESSION, out["quote_session_id"])
        self.assertEqual(BC, out["business_case_id"])

    def test_unique_task_candidate_is_linked_by_task(self):
        out = call_decide([candidate(H.QUOTE_SESSION, linked_by="task")])
        self.assert_contract(out)
        self.assertEqual("linked", out["code"])
        self.assertEqual("task", out["linked_by"])
        self.assertEqual(H.QUOTE_SESSION, out["quote_session_id"])
        self.assertTrue(str(out["business_case_id"]).strip(),
                        "关联成功必须给出实例号（卡片没有时要生成并回填）")

    def test_unique_session_candidate_is_linked_by_session(self):
        out = call_decide([candidate(H.QUOTE_SESSION, linked_by="session")])
        self.assertEqual("linked", out["code"])
        self.assertEqual("session", out["linked_by"])

    def test_duplicate_candidates_are_deduped_not_treated_as_multiple(self):
        same = candidate(H.QUOTE_SESSION, linked_by="task")
        out = call_decide([same, dict(same)])
        self.assertEqual("linked", out["code"],
                         "同一会话号 + 同一种命中方式只能算一个候选（Spec §6 去重）")
        self.assertEqual(H.QUOTE_SESSION, out["quote_session_id"])

    def test_two_candidates_stop_even_when_create_new_is_requested(self):
        out = call_decide([candidate(H.QUOTE_SESSION, linked_by="case", card_id=1),
                           candidate("qsess-other", linked_by="case", card_id=2)],
                          business_case_id=BC, tech_project_id=H.TECH_PROJECT,
                          create_new=True, create_reason="客户要求重开")
        self.assert_contract(out)
        self.assertEqual("multiple_candidates", out["code"],
                         "多候选必须停下来让人选，不许用「新建」绕过选择")
        self.assertEqual(2, len(out["candidates"]))
        self.assertEqual("", out["quote_session_id"], "多候选时不得替用户挑一个")

    def test_no_candidate_without_create_new_is_no_candidate(self):
        out = call_decide([], tech_project_id=H.TECH_PROJECT)
        self.assert_contract(out)
        self.assertEqual("no_candidate", out["code"])
        self.assertEqual("", out["quote_session_id"], "无候选时不得凭空给出会话号")

    def test_create_new_without_reason_is_still_no_candidate(self):
        out = call_decide([], tech_project_id=H.TECH_PROJECT, create_new=True,
                          create_reason="   ")
        self.assertEqual("no_candidate", out["code"],
                         "「新建」必须写清为什么 —— 空原因等于没确认")

    def test_create_new_with_reason_stamps_recovery_evidence(self):
        reason = "原报价卡片被误删，销售同意重开"
        out = call_decide([], tech_project_id=H.TECH_PROJECT, create_new=True,
                          create_reason=reason)
        self.assert_contract(out)
        self.assertEqual("create_new", out["code"])
        self.assertEqual(H.TECH_PROJECT, out["recovered_from_project_id"])
        self.assertEqual(reason, out["recovery_reason"])

    def test_decide_is_pure_repeatable_and_does_not_touch_inputs(self):
        candidates = [candidate(H.QUOTE_SESSION, linked_by="task")]
        before = copy.deepcopy(candidates)
        first = call_decide(candidates, tech_project_id=H.TECH_PROJECT)
        second = call_decide(candidates, tech_project_id=H.TECH_PROJECT)
        self.assertEqual(first, second, "同一输入两次必须得到同一结果（纯函数）")
        self.assertEqual(before, candidates, "decide 不得改写入参")
        self.assertEqual("linked", first["code"])


# ---------------------------------------------------------------------------
# 3. resolve：候选解析 + 落库结局（真跑假库 SQL）
# ---------------------------------------------------------------------------
class ResolveDecisionTest(unittest.TestCase):
    def resolve_error(self, conn, **kwargs):
        err = case_link_error()
        try:
            call_resolve(conn, **kwargs)
        except err as exc:
            return exc
        except Exception as exc:                            # noqa: BLE001
            self.fail(f"resolve 必须抛 CaseLinkError，实际抛了 {type(exc).__name__}: {exc}")
        self.fail("多条候选 / 无候选时 resolve 必须抛 CaseLinkError，不能静默返回")

    def test_case_hit_lands_on_original_card_when_source_task_is_gone(self):
        """task 被取消 / 替换 / 删除之后，实例号仍然要认回原卡片。"""
        with H.Workbench(with_source_task=False) as wb:
            wb.card()["business_case_id"] = BC
            conn = cpq_auth._connect()
            out = call_resolve(conn, business_case_id=BC,
                               tech_project_id=H.TECH_PROJECT,
                               user={"user_id": str(H.FIN_UID)})
            self.assertEqual("linked", out["code"])
            self.assertEqual("case", out["linked_by"])
            self.assertEqual(H.QUOTE_SESSION, out["quote_session_id"])
            self.assertEqual(1, len(wb.cards()), "唯一命中不得再建卡片")

    def test_task_hit_backfills_business_case_id(self):
        with H.Workbench() as wb:
            conn = cpq_auth._connect()
            out = call_resolve(conn, source_task_id="9001",
                               tech_project_id=H.TECH_PROJECT,
                               user={"user_id": str(H.FIN_UID)})
            self.assertEqual("linked", out["code"])
            self.assertEqual("task", out["linked_by"])
            self.assertEqual(H.QUOTE_SESSION, out["quote_session_id"])
            self.assertTrue(BC_RE.match(str(out["business_case_id"] or "")),
                            f"卡片没有实例号时必须生成一个 bc_+12hex：{out['business_case_id']!r}")
            self.assertEqual(out["business_case_id"],
                             str(wb.card().get("business_case_id") or ""),
                             "关联成功要把实例号回填到卡片")

    def test_backfill_is_idempotent_on_second_resolve(self):
        with H.Workbench() as wb:
            conn = cpq_auth._connect()
            first = call_resolve(conn, source_task_id="9001", tech_project_id=H.TECH_PROJECT)
            second = call_resolve(conn, source_task_id="9001", tech_project_id=H.TECH_PROJECT)
            self.assertEqual(first["business_case_id"], second["business_case_id"],
                             "第二次解析不得换实例号")
            self.assertEqual(1, len(wb.cards()))

    def test_instance_id_and_task_pointing_at_the_same_card_is_one_candidate(self):
        """实例号与来源任务落在同一张卡片时是一个候选，不是「多候选」。"""
        with H.Workbench() as wb:
            wb.card()["business_case_id"] = BC
            conn = cpq_auth._connect()
            out = call_resolve(conn, business_case_id=BC, source_task_id="9001",
                               source_session_id=H.QUOTE_SESSION,
                               tech_project_id=H.TECH_PROJECT)
            self.assertEqual("linked", out["code"])
            self.assertEqual("case", out["linked_by"], "同一卡片保留可靠性最高的命中方式")
            self.assertEqual(H.QUOTE_SESSION, out["quote_session_id"])
            self.assertEqual(1, len(wb.cards()))

    def test_multiple_candidates_stop_with_zero_writes(self):
        with H.Workbench(with_source_task=False) as wb:
            wb.card()["business_case_id"] = BC
            add_card(wb, 2, "qsess-other", business_case_id=BC)
            before = state(wb)
            conn = cpq_auth._connect()
            exc = self.resolve_error(conn, business_case_id=BC,
                                     tech_project_id=H.TECH_PROJECT)
            self.assertEqual("multiple_candidates", str(getattr(exc, "code", "")))
            self.assertEqual(2, len(getattr(exc, "candidates", []) or []),
                             "异常里必须把候选清单带出来给界面")
            self.assertEqual("", diff_state(before, state(wb)),
                             "多候选时必须 0 写入（不选、不建、不改）")

    def test_no_candidate_never_creates_a_card(self):
        with H.Workbench(with_source_task=False) as wb:
            before = state(wb)
            conn = cpq_auth._connect()
            exc = self.resolve_error(conn, source_task_id="", source_session_id="",
                                     tech_project_id=H.TECH_PROJECT)
            self.assertEqual("no_candidate", str(getattr(exc, "code", "")))
            self.assertEqual("", diff_state(before, state(wb)),
                             "无候选时必须 0 写入（尤其不许静默新建）")

    def test_create_new_without_reason_never_creates_a_card(self):
        with H.Workbench(with_source_task=False) as wb:
            before = state(wb)
            conn = cpq_auth._connect()
            exc = self.resolve_error(conn, source_task_id="", source_session_id="",
                                     tech_project_id=H.TECH_PROJECT, create_new=True,
                                     create_reason="")
            self.assertEqual("no_candidate", str(getattr(exc, "code", "")))
            self.assertEqual("", diff_state(before, state(wb)))

    def test_archived_card_is_not_a_candidate(self):
        with H.Workbench(with_source_task=False, overall_status="archived") as wb:
            wb.card()["business_case_id"] = BC
            before = state(wb)
            conn = cpq_auth._connect()
            exc = self.resolve_error(conn, business_case_id=BC,
                                     tech_project_id=H.TECH_PROJECT)
            self.assertEqual("no_candidate", str(getattr(exc, "code", "")),
                             "实例号只指向归档卡片时按无候选处理（不自动复活）")
            self.assertEqual("", diff_state(before, state(wb)))

    def test_candidates_carry_the_keys_the_ui_needs(self):
        with H.Workbench(with_source_task=False) as wb:
            wb.card()["business_case_id"] = BC
            add_card(wb, 2, "qsess-other", business_case_id=BC, title="同名第二张")
            conn = cpq_auth._connect()
            exc = self.resolve_error(conn, business_case_id=BC,
                                     tech_project_id=H.TECH_PROJECT)
            for item in getattr(exc, "candidates", []) or []:
                self.assertEqual([], [k for k in ("quote_session_id", "card_id", "linked_by",
                                                  "title") if k not in item],
                                 f"候选项缺键（前端要展示会话号/标题/匹配方式）：{item}")


# ---------------------------------------------------------------------------
# 4. 回传命令：落点由实例号决定，绝不静默新建
# ---------------------------------------------------------------------------
class SendToQuoteLinkageTest(unittest.TestCase):
    def assert_zero_side_effects(self, before, wb):
        self.assertEqual("", diff_state(before, state(wb)),
                         "被拒绝的回传必须整段回滚：卡片 / 任务 / 交接 / 消息都不许变")

    def test_signature_has_the_three_new_keywords(self):
        self.assertTrue(accepts(cpq_tech_bridge.send_to_quote,
                                ("business_case_id", "create_new", "create_reason")),
                        "send_to_quote 必须接受 business_case_id / create_new / create_reason")

    def test_case_hit_lands_on_original_card_and_records_the_instance_id(self):
        with H.Workbench() as wb:
            wb.card()["business_case_id"] = BC
            out = send_to_quote(wb, business_case_id=BC)
            self.assertEqual("case", str(out.get("linked_by") or ""))
            self.assertEqual(BC, str(out.get("business_case_id") or ""))
            self.assertFalse(bool(out.get("new_card")), "命中实例号时不许再建卡片")
            self.assertEqual(1, len(wb.cards()))
            rows = wb.handoffs()
            self.assertEqual(1, len(rows))
            self.assertEqual(BC, str(rows[0].get("business_case_id") or ""),
                             "交接记录必须带上本次业务实例号（可追溯）")

    def test_return_body_carries_instance_candidates_and_recovery(self):
        with H.Workbench() as wb:
            wb.card()["business_case_id"] = BC
            out = send_to_quote(wb, business_case_id=BC)
            for key in ("business_case_id", "candidates", "recovery"):
                self.assertIn(key, out, f"返回体必须带 {key}（Spec §6）")
            self.assertIsInstance(out["candidates"], list)
            self.assertIsInstance(out["recovery"], dict, "recovery 必须是 dict（没新建也要在）")
            missing = [key for key in ("recovered_from_project_id", "recovery_reason",
                                      "recovered_by", "recovered_at")
                       if key not in out["recovery"]]
            self.assertEqual([], missing, f"recovery 缺键：{missing}")
            self.assertEqual(BC, str(out.get("business_case_id") or ""))

    def test_task_clue_still_links_without_instance_id(self):
        """既有能力护栏：带来源任务的回传仍然落在原卡片上。"""
        with H.Workbench() as wb:
            out = send_to_quote(wb)
            self.assertEqual("task", str(out.get("linked_by") or ""))
            self.assertEqual(H.QUOTE_SESSION, str(out.get("quote_session_id") or ""))
            self.assertEqual(1, len(wb.cards()))

    def test_instance_id_wins_over_task_for_the_same_card(self):
        """实例号与来源任务指同一张卡时仍是一张卡、一个候选（不许报多候选）。"""
        with H.Workbench() as wb:
            wb.card()["business_case_id"] = BC
            out = send_to_quote(wb, business_case_id=BC)
            self.assertEqual("case", str(out.get("linked_by") or ""))
            self.assertEqual(H.QUOTE_SESSION, str(out.get("quote_session_id") or ""))
            self.assertEqual(1, len(wb.cards()))

    def test_no_clue_without_create_new_is_refused_with_zero_writes(self):
        with H.Workbench(with_source_task=False) as wb:
            before = state(wb)
            with self.assertRaises(case_link_error()) as caught:
                send_to_quote(wb, source_task_id="", source_session_id="")
            self.assertEqual("no_candidate", str(getattr(caught.exception, "code", "")))
            self.assert_zero_side_effects(before, wb)

    def test_create_new_without_reason_is_refused_with_zero_writes(self):
        with H.Workbench(with_source_task=False) as wb:
            before = state(wb)
            with self.assertRaises(case_link_error()) as caught:
                send_to_quote(wb, source_task_id="", source_session_id="",
                              create_new=True, create_reason="  ")
            self.assertEqual("no_candidate", str(getattr(caught.exception, "code", "")))
            self.assert_zero_side_effects(before, wb)

    def test_multiple_candidates_are_refused_with_zero_writes(self):
        with H.Workbench(with_source_task=False) as wb:
            wb.card()["business_case_id"] = BC
            add_card(wb, 2, "qsess-other", business_case_id=BC)
            before = state(wb)
            with self.assertRaises(case_link_error()) as caught:
                send_to_quote(wb, source_task_id="", source_session_id="",
                              business_case_id=BC, create_new=True,
                              create_reason="客户要求重开")
            self.assertEqual("multiple_candidates", str(getattr(caught.exception, "code", "")))
            self.assertTrue(getattr(caught.exception, "candidates", None),
                            "异常必须把候选清单带回界面")
            self.assert_zero_side_effects(before, wb)

    def test_confirmed_create_new_makes_a_real_new_session(self):
        reason = "原报价卡片被误删，销售同意重开"
        with H.Workbench(with_source_task=False) as wb:
            out = send_to_quote(wb, source_task_id="", source_session_id="",
                                create_new=True, create_reason=reason)
            self.assertEqual("new_session", str(out.get("linked_by") or ""))
            self.assertTrue(bool(out.get("new_card")))
            new_session = str(out.get("quote_session_id") or "")
            self.assertTrue(new_session.strip(), "新建必须真的落到一条报价会话上")
            self.assertNotEqual(H.TECH_PROJECT, new_session,
                                "绝不许拿技术项目号冒充报价会话号")
            sessions = {str(row.get("session_id") or "") for row in wb.cards()}
            self.assertEqual(2, len(sessions), f"人工确认后应有两张卡片：{sessions}")
            self.assertNotIn(H.TECH_PROJECT, sessions,
                             "技术项目号不许出现在报价卡片表里")
            self.assertFalse(BC_RE.match(new_session),
                             "会话号是报价侧的真实会话号，不是实例号本身")
            instance = str(out.get("business_case_id") or "")
            self.assertTrue(BC_RE.match(instance), f"新建也要有实例号：{instance!r}")
            new_card = [row for row in wb.cards() if str(row.get("session_id")) == new_session][0]
            self.assertEqual(instance, str(new_card.get("business_case_id") or ""),
                             "新卡片必须落在同一个业务实例号上")
            self.assertEqual(instance, str(wb.handoffs()[0].get("business_case_id") or ""))

    def test_confirmed_create_new_records_recovery_evidence(self):
        reason = "原报价卡片被误删，销售同意重开"
        with H.Workbench(with_source_task=False) as wb:
            out = send_to_quote(wb, source_task_id="", source_session_id="",
                                create_new=True, create_reason=reason)
            recovery = out.get("recovery") or {}
            self.assertEqual(H.TECH_PROJECT, str(recovery.get("recovered_from_project_id") or ""),
                             "必须记录从哪个技术项目恢复新建")
            self.assertEqual(reason, str(recovery.get("recovery_reason") or ""))
            comments = [str(row.get("comment") or "") for row in wb.events()]
            self.assertTrue(any(reason in text for text in comments),
                            f"审计里必须留下恢复原因：{comments}")
            self.assertTrue(any(H.TECH_PROJECT in text for text in comments),
                            "审计里必须留下 recovered_from_project_id")


# ---------------------------------------------------------------------------
# 5. 数据契约：建卡产生实例号、交接表带列、DDL 幂等
# ---------------------------------------------------------------------------
class SchemaAndCardContractTest(unittest.TestCase):
    def test_ddl_adds_business_case_id_to_card_and_handoff(self):
        ddl = "\n".join(cpq_wf._ddl_pg("cpq_wf"))
        patterns = (
            (r"(?is)ALTER\s+TABLE\s+cpq_wf\.cpq_wf_card\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+"
             r"business_case_id",
             "cpq_wf_card 必须用幂等 ADD COLUMN IF NOT EXISTS 加 business_case_id（Spec §6）"),
            (r"(?is)ALTER\s+TABLE\s+cpq_wf\.cpq_wf_handoff\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+"
             r"business_case_id",
             "cpq_wf_handoff 必须幂等加 business_case_id 列"),
            (r"(?is)CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+\w+\s+ON\s+cpq_wf\.cpq_wf_card\s*\(\s*"
             r"business_case_id",
             "按实例号查候选必须有幂等索引 cpq_wf_card(business_case_id)"),
        )
        for pattern, message in patterns:
            self.assertTrue(re.search(pattern, ddl) is not None, message)

    def test_sync_card_generates_a_stable_business_case_id(self):
        self.assertTrue(accepts(cpq_wf.sync_card, ("business_case_id",)),
                        "cpq_wf.sync_card 必须新增关键字参数 business_case_id（Spec §6）")
        with H.Workbench() as wb:
            user = wb.user(H.SALES_UID)
            made = cpq_wf.sync_card("qsess-new-card", user, title="新卡", business_case_id="")
            rows = wb.rows("cpq_wf_card", session_id="qsess-new-card")
            self.assertEqual(1, len(rows), "卡片必须真的建出来")
            first = str(rows[0].get("business_case_id") or "")
            self.assertTrue(BC_RE.match(first),
                            f"建卡必须产生 bc_+12hex 实例号：{first!r}")
            self.assertEqual(first, str((made or {}).get("business_case_id") or ""),
                             "卡片读取路径也要带出实例号")
            again = cpq_wf.sync_card("qsess-new-card", user, title="新卡改名")
            self.assertEqual(first, str((again or {}).get("business_case_id") or ""),
                             "重复同步不得换实例号")
            cpq_wf.sync_card("qsess-provided", user, business_case_id=BC2)
            provided = wb.rows("cpq_wf_card", session_id="qsess-provided")[0]
            self.assertEqual(BC2, str(provided.get("business_case_id") or ""),
                             "显式传入的实例号必须原样保存（恢复新建沿用同一实例）")

    def test_get_card_exposes_business_case_id(self):
        with H.Workbench() as wb:
            wb.card()["business_case_id"] = BC
            self.assertEqual(BC, str(cpq_wf.get_card(H.QUOTE_SESSION).get("business_case_id") or ""))


# ---------------------------------------------------------------------------
# 6. 技术侧：项目 meta / 回传包 / Agent 会话轮次
# ---------------------------------------------------------------------------
CHILD = r'''
import inspect
import json
import os
import sys
import types

data_dir, root, case, bc = (sys.argv[1], sys.argv[2], sys.argv[3],
                             sys.argv[4] if len(sys.argv) > 4 else "")
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
from tech_app.backend.models.integration import (IntegrationParam, IntegrationParamPlan,
                                                 IntegrationPlan)
from tech_app.backend.models.ir import DesignIR
from tech_app.backend.models.process import ProcessPlan, ProcessStep
from tech_app.backend.services import cost_flow, cpq_bridge, integration, product_params
from tech_app.backend.storage import store

ACTOR = {"username": "fin1", "display_name": "财务一", "role": "finance_manager",
         "user_id": "400", "role_code": "finance_mgr"}
# 实例号只允许有一个来源：由父进程按同一个常量传入（外侧断言用的就是它）。
# 早先这里另写了一份字面量且与外侧不一致，导致探针写入的值永远对不上断言。
BC = bc
PID = store.create_project(source_filename="case-link.dxf", source_bytes=b"x",
                           note="batch6 tech-side probe", owner="tester")
out = {"case": case}


def amount(value):
    return {"name": "材料费", "category": "material", "quantity": 1,
            "unit_price": value, "amount": value}


def build_plan():
    PART = "P-001"
    store.save_ir(PID, DesignIR(device_name="便携式锂电池 PACK",
                                design_intent="批次 6 技术侧探针",
                                parts=[{"part_id": PART, "name": "上壳", "quantity": 1}]
                                ).model_dump(), stage="parsed")
    store.save_cost(PID, PART, {"part_id": PART, "items": [amount(100.0)]})
    plan = IntegrationPlan(project_id=PID)
    plan.params = IntegrationParamPlan(product_family="other", params=[],
                                       assembly_name="便携式锂电池 PACK")
    plan.cost = CostAnalysis(items=[CostItem(**amount(100.0))])
    plan.process = ProcessPlan(steps=[ProcessStep(no=1, name="整机总装", hours=0.07)])
    plan.quantity = 1
    for _group in product_params.checklist(plan.params)["groups"]:
        for _field in _group["fields"]:
            if _field["required"]:
                plan.params.params.append(IntegrationParam(
                    param_code=_field["code"], name=_field["name"], value="X",
                    unit=_field.get("unit") or None))
    integration.save_plan(PID, plan, "tester")
    cost_flow.confirm_review(PID, ACTOR)
    return plan


if case == "meta":
    out["has_save"] = hasattr(store, "save_business_case")
    out["has_load"] = hasattr(store, "load_business_case")
    if out["has_save"] and out["has_load"]:
        store.save_business_case(PID, {"business_case_id": BC, "quote_session_id": "qs-1",
                                       "source_task_id": "9001", "linked_by": "case"},
                                 author="fin1")
        out["loaded"] = store.load_business_case(PID) or {}
        store.save_business_case(PID, {"recovery_reason": "后来的合并写入"}, author="fin1")
        merged = store.load_business_case(PID) or {}
        out["kept_id"] = merged.get("business_case_id")
        out["kept_reason"] = merged.get("recovery_reason")
        meta = store.load_meta(PID) or {}
        out["meta_doc"] = meta.get("business_case")

elif case == "quote_result":
    plan = build_plan()
    package = cost_flow.integration_quote_result(PID, plan, "便携式锂电池 PACK")
    out["keys"] = sorted(package.keys())
    out["business_case_id"] = package.get("business_case_id")
    out["empty_when_no_meta"] = not str(package.get("business_case_id") or "").strip()

elif case == "bridge_payload":
    real = cpq_bridge.send_to_quote
    _params = inspect.signature(real).parameters
    out["wrapper_accepts"] = all(
        name in _params
        or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in _params.values())
        for name in ("business_case_id", "create_new", "create_reason"))
    seen = {}
    out["ok"] = False
    if not hasattr(store, "save_business_case"):
        out["error"] = "store.save_business_case 不存在"
    else:
        store.save_business_case(PID, {"business_case_id": BC, "quote_session_id": "qs-1",
                                       "linked_by": "case"}, author="fin1")
        build_plan()

        def fake_send_to_quote(token, session_id, title, *args, **kwargs):
            seen.update(kwargs)
            return {"handoff_id": "H-1", "handoff_key": "K-1", "handoff_kind": "cost_to_quote",
                    "quote_session_id": "qs-1", "linked_by": "case", "new_card": False,
                    "already_sent": False, "next_step_no": 3, "next_step_name": "定价-利润加成",
                    "returned_sections": [], "handoff": {"task_id": "77"},
                    "source_task": {"task_id": "9001", "closed": True, "status": "completed"}}

        cpq_bridge.send_to_quote = fake_send_to_quote
        try:
            cost_flow.send_to_quote(PID, ACTOR, token="t", source_task_id="9001")
            out["ok"] = True
        except Exception as exc:                              # noqa: BLE001
            out["ok"] = False
            out["error"] = f"{type(exc).__name__}: {exc}"[:300]
        finally:
            cpq_bridge.send_to_quote = real
    out["sent_business_case_id"] = seen.get("business_case_id")

elif case == "chat_turn":
    from tech_app.backend import main
    if hasattr(store, "save_business_case"):
        store.save_business_case(PID, {"business_case_id": BC, "quote_session_id": "qs-1",
                                       "linked_by": "case"}, author="fin1")
    main._save_project_chat_turn(PID, "这步怎么填", "按 2.3 的提示填",
                                 {"username": "fin1"}, "2.3 成本测算")
    rows = store.load_project_chat(PID).get("messages", [])
    out["keys"] = sorted({key for row in rows for key in row.keys()})
    out["business_case_ids"] = [row.get("business_case_id") for row in rows]
    out["roles"] = [row.get("role") for row in rows]

print(json.dumps(out, ensure_ascii=False, default=str))
'''


class TechSideBusinessCaseTest(unittest.TestCase):
    """技术侧：实例号要顺着项目 meta → 回传包 → Agent 会话一路带上。"""

    def run_child(self, case):
        data_dir = tempfile.mkdtemp(prefix="cpq-case-link-data-")
        script_dir = tempfile.mkdtemp(prefix="cpq-case-link-script-")
        script = pathlib.Path(script_dir) / "child.py"
        script.write_text(CHILD, encoding="utf-8")
        env = dict(os.environ)
        env["DATA_DIR"] = data_dir
        env["AUTH_ENABLED"] = "false"
        env["PYTHONPATH"] = str(ROOT)
        completed = subprocess.run(
            [sys.executable, str(script), data_dir, str(ROOT), case, BC],
            capture_output=True, text=True, env=env, cwd=str(ROOT))
        self.assertEqual(0, completed.returncode,
                         f"子进程失败：{completed.stderr[-800:]}")
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_store_roundtrips_the_business_case_document(self):
        out = self.run_child("meta")
        self.assertTrue(out["has_save"], "store 必须提供 save_business_case(project_id, link, author)")
        self.assertTrue(out["has_load"], "store 必须提供 load_business_case(project_id)")
        self.assertEqual(BC, str(out["loaded"].get("business_case_id") or ""))
        self.assertEqual("qs-1", str(out["loaded"].get("quote_session_id") or ""))
        self.assertEqual("9001", str(out["loaded"].get("source_task_id") or ""))
        self.assertEqual("case", str(out["loaded"].get("linked_by") or ""))
        self.assertEqual(BC, str(out["kept_id"] or ""),
                         "后来只带 recovery_reason 的合并写入不得抹掉实例号")
        self.assertEqual("后来的合并写入", str(out["kept_reason"] or ""))
        self.assertEqual(BC, str((out.get("meta_doc") or {}).get("business_case_id") or ""),
                         "实例号要落在项目 meta 的 business_case 文档里（重启后可追溯）")

    def test_quote_result_package_carries_business_case_id(self):
        out = self.run_child("quote_result")
        self.assertIn("business_case_id", out["keys"],
                      "integration_quote_result 的返回体必须带 business_case_id")
        self.assertTrue(out["empty_when_no_meta"],
                        "项目 meta 里没有实例号时必须给空串，绝不现编一个")

    def test_tech_side_forwarded_the_instance_id(self):
        out = self.run_child("bridge_payload")
        self.assertTrue(out.get("wrapper_accepts"),
                        "技术侧 cpq_bridge.send_to_quote 必须能接受 business_case_id / "
                        "create_new / create_reason 并透传给服务端")
        self.assertTrue(out.get("ok"), f"回传不该失败：{out.get('error')}")
        self.assertEqual(BC, str(out.get("sent_business_case_id") or ""),
                         "技术侧回传必须把项目 meta 里的实例号一起发出去")

    def test_agent_chat_turn_carries_business_case_id(self):
        out = self.run_child("chat_turn")
        self.assertEqual(["user", "assistant"], out["roles"])
        self.assertEqual([BC, BC], [str(v or "") for v in out["business_case_ids"]],
                         "每一轮 Agent 会话记录都要带项目实例号")


# ---------------------------------------------------------------------------
# 7. 本文件自身的一致性护栏（防止「探针另写一份常量」这类红测缺陷复发）
# ---------------------------------------------------------------------------
class ProbeConstantSelfCheckTest(unittest.TestCase):
    """内嵌探针脚本与外侧断言不得各写一份取值不同的常量。

    批次 6 这里曾把实例号写成两份字面量且取值不一致（外侧 bc_0f1e2d3c4b5a、
    探针内 bc_1a2b3c4d5e6f），而 run_child 不做任何替换，
    于是那 3 条用例对**任何实现**都不可能通过 —— 那是红测自身的缺陷、不是实现缺口。
    现在实例号只由父进程经 argv 传入；这条护栏保证以后不会再分叉。
    """

    FILE = pathlib.Path(__file__)
    ASSIGN = re.compile(r"^\s*([A-Z][A-Z0-9_]{1,})\s*=\s*(?P<q>[\"'])(?P<v>.*?)(?P=q)\s*$")

    def blocks(self):
        lines = self.FILE.read_text(encoding="utf-8").splitlines()
        found, index = [], 0
        while index < len(lines):
            if re.match(r"^[A-Z][A-Z0-9_]*\s*=\s*r?'''", lines[index]):
                start, end = index, index + 1
                while end < len(lines) and lines[end].strip() != "'''":
                    end += 1
                found.append((lines[start].split("=")[0].strip(), start, end))
                index = end + 1
            else:
                index += 1
        return lines, found

    def literals(self, lines):
        found = {}
        for line in lines:
            match = self.ASSIGN.match(line)
            if match:
                found.setdefault(match.group(1), set()).add(match.group("v"))
        return found

    def test_probe_scripts_do_not_redeclare_assertion_constants(self):
        lines, blocks = self.blocks()
        self.assertTrue(blocks, "本文件应当至少有一个内嵌探针脚本（CHILD）")
        inside = self.literals([line for _name, start, end in blocks
                                for line in lines[start + 1:end]])
        outside = self.literals([line for index, line in enumerate(lines)
                                 if not any(start <= index <= end for _n, start, end in blocks)])
        clashes = sorted(name for name in set(outside) & set(inside)
                         if not (outside[name] & inside[name]))
        self.assertEqual([], clashes,
                         "内嵌探针与外侧断言各写了一份取值不同的常量，红测必然失败："
                         f"{clashes}。取值只能有一个来源（由父进程传入）。")

if __name__ == "__main__":
    unittest.main()

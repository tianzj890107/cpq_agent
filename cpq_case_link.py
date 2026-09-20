# -*- coding: utf-8 -*-
"""报价—技术—财务—报告统一业务实例关联与安全恢复（批次 6）。

「技术侧回传落在哪张报价卡片」这件事只有这一处说了算：

  · ``decide``  —— 纯函数：把候选列表判成 linked / multiple_candidates / create_new /
    no_candidate 四种结局；不碰库、不改入参、同输入同输出。
  · ``resolve`` —— 在调用方给的**同一条连接**上查候选（case > task > session）→ 调 decide。
    linked 时把实例号回填到卡片；multiple_candidates / no_candidate 抛 ``CaseLinkError``
    （**0 写入**）；create_new 只带出恢复留痕，报价会话与卡片仍由回传命令在同一个事务里建。

为什么收口：批次 3 之前落点靠三条散落线索（task、session、都没有就**静默新建**）。任务被
取消 / 顶掉 / 删掉之后就会凭空多出一张报价卡片 —— 客户信息只剩技术侧填过的部分，销售那单
永远停在第 1 步等新产品。``business_case_id``（``bc_`` + 12 位小写 hex）是唯一能跨系统、
跨重建认回来的线索，所以落点由它裁决：

  唯一候选 → 自动关联；多候选 → 停下让人选；无候选 → 必须被明确确认（并写原因）才新建。

本模块只做"落点怎么定"：不改批次 3 的事务与幂等键、不改批次 4 的主数据写入、不改批次 5
的流程投影。
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

# 归档卡片不复活、不自动新建：实例号只指向归档卡片时按「无候选」走人工决定路径。
ARCHIVED_STATUS = "archived"

# 命中方式的可靠性：同一张卡片同时被实例号与来源任务指到时，算**一个**候选，
# 保留可靠性最高的那个（case > task > session）。
RELIABILITY = {"case": 3, "task": 2, "session": 1}

# decide 的返回键（Spec §6 契约细节）：一个都不能少，非命中路径填空串 / 空列表。
RETURN_KEYS = ("code", "quote_session_id", "linked_by", "business_case_id", "candidates",
               "recovered_from_project_id", "recovery_reason")

_MESSAGES = {
    "multiple_candidates": "这条回传命中了多张报价卡片，请先选定要落回的那一张再重试"
                           "（不会替你挑，也不会新建）。",
    "no_candidate": "没有找到这条业务实例对应的报价卡片。确认要新建报价卡片时，"
                    "请填写新建原因后重试。",
}


class CaseLinkError(Exception):
    """落点定不下来时的业务错误：带 code / candidates / message，让人知道下一步做什么。"""

    def __init__(self, code: str, candidates: Optional[List[dict]] = None,
                 message: str = ""):
        self.code = str(code or "")
        self.candidates = [dict(item or {}) for item in (candidates or [])]
        self.message = str(message or "").strip() or _MESSAGES.get(
            self.code, "业务实例关联失败，请稍后重试。")
        super().__init__(self.message)


# --------------------------------------------------------------------------- #
# decide：纯函数
# --------------------------------------------------------------------------- #
def new_business_case_id(seed: str = "") -> str:
    """生成 / 派生一个业务实例号（``bc_`` + 12 位小写 hex）。

    卡片已经有实例号时用卡片的；没有时要给出一个**可重复**的值 —— decide 是纯函数，
    同一输入必须得到同一输出，所以这里按候选的会话号派生，而不是取随机数。
    """
    text = str(seed or "")
    digest = hashlib.sha1(("cpq-case|" + text).encode("utf-8")).hexdigest()
    return "bc_" + digest[:12]


def _candidate_key(item: dict) -> str:
    return str((item or {}).get("quote_session_id") or "").strip()


def dedupe_candidates(candidates) -> List[dict]:
    """按报价会话号去重；同一张卡片保留可靠性最高的命中方式（case > task > session）。

    同一张卡片既被实例号命中、又被来源任务指到时，这是**一个**候选，不是「多候选」。
    """
    merged: Dict[str, dict] = {}
    for raw in (candidates or []):
        item = dict(raw or {})
        key = _candidate_key(item)
        if not key:
            continue
        item["quote_session_id"] = key
        current = merged.get(key)
        if current is None:
            merged[key] = item
            continue
        # 命中方式取可靠性最高的；其余字段按非空补齐，不丢线索。
        if RELIABILITY.get(str(item.get("linked_by") or ""), 0) > \
                RELIABILITY.get(str(current.get("linked_by") or ""), 0):
            current["linked_by"] = item.get("linked_by")
        for field in ("card_id", "title", "business_case_id"):
            if not str(current.get(field) or "").strip() and str(item.get(field) or "").strip():
                current[field] = item.get(field)
    return [merged[key] for key in sorted(merged)]


def decide(candidates, *, business_case_id: str = "", tech_project_id: str = "",
           create_new: bool = False, create_reason: str = "") -> dict:
    """判定这次回传的落点。纯函数：不碰数据库、不改入参、同输入同输出。

    四种结局（``code``）：

      · ``linked``              —— 恰好一个候选：落回它，``linked_by`` 用卡片的命中方式；
      · ``multiple_candidates`` —— 多个候选：停下让人选（even 带着新建原因也不放行）；
      · ``create_new``          —— 没有候选 + 明确要求新建 + 写了原因；
      · ``no_candidate``        —— 其余情况（含"要新建但没写原因"），不新建。
    """
    bc = str(business_case_id or "").strip()
    tech_project = str(tech_project_id or "").strip()
    reason = str(create_reason or "").strip()
    picked = dedupe_candidates(candidates)

    if len(picked) >= 2:
        return _result("multiple_candidates", candidates=picked)

    if len(picked) == 1:
        row = picked[0]
        session_id = str(row.get("quote_session_id") or "").strip()
        linked_by = str(row.get("linked_by") or "").strip() or "session"
        instance = (str(row.get("business_case_id") or "").strip() or bc
                    or new_business_case_id(session_id))
        return {"code": "linked", "quote_session_id": session_id, "linked_by": linked_by,
                "business_case_id": instance, "candidates": [dict(row)],
                "recovered_from_project_id": "", "recovery_reason": ""}

    if create_new and reason:
        return {"code": "create_new", "quote_session_id": "", "linked_by": "new_session",
                "business_case_id": bc, "candidates": [],
                "recovered_from_project_id": tech_project, "recovery_reason": reason}

    return _result("no_candidate", candidates=[])


def _result(code: str, *, candidates=None, quote_session_id: str = "",
            linked_by: str = "", business_case_id: str = "",
            recovered_from_project_id: str = "", recovery_reason: str = "") -> dict:
    out = {"code": code, "quote_session_id": quote_session_id, "linked_by": linked_by,
           "business_case_id": business_case_id, "candidates": list(candidates or []),
           "recovered_from_project_id": recovered_from_project_id,
           "recovery_reason": recovery_reason}
    return {key: out[key] for key in RETURN_KEYS}


# --------------------------------------------------------------------------- #
# resolve：在调用方的连接上查候选 → decide → 按结局落库 / 抛错
# --------------------------------------------------------------------------- #
_CARD_FIELDS = "c.card_id, c.session_id, c.title, c.business_case_id"
_NOT_ARCHIVED = "(c.overall_status IS NULL OR c.overall_status <> 'archived')"


def _rows(conn, sql: str, args) -> List[dict]:
    cur = _auth()._exec(conn, sql, args)
    out = []
    for row in cur.fetchall():
        out.append({"card_id": row[0], "quote_session_id": str(row[1] or ""),
                    "title": str(row[2] or ""), "business_case_id": str(row[3] or "")})
    return out


def find_candidates(conn, *, business_case_id: str = "", source_task_id: str = "",
                    source_session_id: str = "") -> List[dict]:
    """三种来源合并去重后的候选清单（case > task > session）。

    归档卡片（``overall_status='archived'``）一律不进候选 —— 不复活、不自动新建。
    """
    found: List[dict] = []
    bc = str(business_case_id or "").strip()
    if bc:
        found += _as_candidates(_rows(
            conn,
            f"SELECT {_CARD_FIELDS} FROM cpq_wf_card c"
            f" WHERE c.business_case_id = %s AND {_NOT_ARCHIVED}", (bc,)), "case")
    task_id = _int_or_none(source_task_id)
    if task_id is not None:
        found += _as_candidates(_rows(
            conn,
            f"SELECT {_CARD_FIELDS} FROM cpq_wf_card c"
            f" JOIN cpq_wf_task t ON t.card_id = c.card_id"
            f" WHERE t.task_id = %s AND {_NOT_ARCHIVED}", (task_id,)), "task")
    session_id = str(source_session_id or "").strip()
    if session_id:
        found += _as_candidates(_rows(
            conn,
            f"SELECT {_CARD_FIELDS} FROM cpq_wf_card c"
            f" WHERE c.session_id = %s AND {_NOT_ARCHIVED}", (session_id,)), "session")
    return dedupe_candidates(found)


def _as_candidates(rows, linked_by: str) -> List[dict]:
    out = []
    for row in rows:
        out.append({"quote_session_id": row.get("quote_session_id") or "",
                    "card_id": row.get("card_id"),
                    "linked_by": linked_by,
                    "title": row.get("title") or "",
                    "business_case_id": row.get("business_case_id") or ""})
    return out


def resolve(conn, *, business_case_id: str = "", source_task_id: str = "",
            source_session_id: str = "", tech_project_id: str = "",
            create_new: bool = False, create_reason: str = "", user: Optional[dict] = None) -> dict:
    """落点解析的唯一入口。查候选 → decide；linked 回填实例号，不能定就抛 CaseLinkError。"""
    candidates = find_candidates(conn, business_case_id=business_case_id,
                                 source_task_id=source_task_id,
                                 source_session_id=source_session_id)
    outcome = decide(candidates, business_case_id=business_case_id,
                     tech_project_id=tech_project_id, create_new=create_new,
                     create_reason=create_reason)
    code = str(outcome.get("code") or "")
    if code == "linked":
        _backfill_business_case_id(conn, outcome.get("candidates") or [],
                                   str(outcome.get("business_case_id") or ""))
        return outcome
    if code == "create_new":
        out = dict(outcome)
        out["recovered_by"] = _actor(user)
        out["recovered_at"] = _stamp()
        return out
    raise CaseLinkError(code, candidates=outcome.get("candidates") or [],
                        message=_MESSAGES.get(code, ""))


def _backfill_business_case_id(conn, candidates, business_case_id: str) -> None:
    """把实例号回填到命中的那张卡片（没有才写；同值重写无副作用）。"""
    if not business_case_id:
        return
    for item in candidates:
        if str(item.get("business_case_id") or "").strip():
            continue
        card_id = _int_or_none(item.get("card_id"))
        if card_id is None:
            continue
        _auth()._exec(
            conn, "UPDATE cpq_wf_card SET business_case_id = %s WHERE card_id = %s",
            (business_case_id, card_id))


# --------------------------------------------------------------------------- #
# 小工具（延迟导入：本模块要保持可单独 import，不把 cpq_wf / cpq_auth 拽进循环）
# --------------------------------------------------------------------------- #
def _auth():
    import cpq_auth
    return cpq_auth


def _int_or_none(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _actor(user: Optional[dict]) -> str:
    user = user or {}
    return str(user.get("display_name") or user.get("username")
               or user.get("user_id") or "")


def _stamp() -> str:
    import cpq_wf
    return str(cpq_wf._iso(cpq_wf._now()) or "")

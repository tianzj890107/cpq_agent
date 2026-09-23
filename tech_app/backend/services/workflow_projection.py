# -*- coding: utf-8 -*-
"""技术工艺统一流程投影（批次 5B）—— 唯一事实源。

为什么单独成模块：今天「这一步做完了没有、下一步能不能点」是**前端猜**出来的
（`tech-workbench.js:refreshProgress()` 里一串 `done.add(...)`）：草稿被当成「已创建」、
只生成工序被当成「组装与整合已完成」、读取失败把完成态清空成「全没做」。同一件事在
组装页、成本页、报告页各算一套，口径必然漂移。

本模块给出**唯一**投影：5 个阶段 × 13 个子步骤（编号见 `workflow_stages.py`，批次 5A），
每个子步骤回答五件事 —— 现在是什么状态（`status`）、做完了没有（`completed`）、
能不能看（`viewable` 恒真）、能不能做（`actionable`）、不能做缺什么 / 谁来做
（`blocked_reasons` / `missing_requirements` / `required_role`）。

只读、幂等、纯派生：不写库、不迁移数据、不改任何业务文档结构；老项目（没有 3.x/4.x
结果、没有 waiver）同样能取出投影，缺的部分是 `not_started`，不是 500。权限只影响
`actionable` 与 `blocked_reasons`，不影响 `viewable`。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..models.ir import DesignIR
from ..storage import store
from . import auth
from . import cad_ir as packaging_cad_ir
from . import cost_review
from . import integration
from . import packaging_parts
from . import workflow_stages as stages_table
from ..time_utils import now_cst_str

# 统一状态枚举（与 docs/specs/tech-unified-workflow-projection.md 第 5 节一致）。
STATUS_ENUM = (
    "not_started", "in_progress", "generated", "edited", "awaiting_confirmation",
    "confirmed", "blocked", "stale", "in_review", "approved", "published",
)

# 非完成状态到统一枚举的换算：报告侧的状态名与投影枚举不同名，这里只做映射。
_REPORT_STATUS = {
    "draft": "generated", "in_review": "in_review", "approved": "approved",
    "published": "published", "rejected": "in_review",
}

# 每个子步骤的主按钮动作名 —— 与看板 `TechBoardRuntime.registerActions()` 注册的名字
# 一字不差（前端据此知道"这一步该按哪颗按钮"，本批不新增动作）。
_PRIMARY_ACTIONS = {
    "1.1": "submitRequirement",
    "1.2": "confirmRequirement",
    "1.3": "submitRequirementReview",
    "2.1": "parseDrawing",
    "3.1": "runIntegration",
    "3.2": "confirmParamsAndNext",
    "3.3": "confirmProcessAndNext",
    "4.1": "runCostReview",
    "4.2": "runCostReview",
    "4.3": "confirmCostReview",
    "5.1": "saveProcessReport",
    "5.2": "approveProcessReport",
    "5.3": "publishProcessReport",
}

# 每个子步骤的角色/能力：roles 是判定用的集合（运行时读 auth，尊重
# enable_cpq_single_manager 之类的运行期授权），label 是给用户看的角色名。
# 4.x 是财务的步骤（COST_ROLES），1.3/5.2 仍由有权限的人提交 —— 编号变化不改权限判定。
_STEP_ROLES = {
    "1.1": ("工艺工程师", ("WRITE_ROLES",)),
    "1.2": ("工艺技术经理", ("MANAGER_ROLES",)),
    "1.3": ("工艺技术经理/工艺技术总监", ("MANAGER_ROLES", "DIRECTOR_ROLES")),
    "2.1": ("工艺工程师", ("WRITE_ROLES",)),
    "3.1": ("工艺工程师", ("WRITE_ROLES",)),
    "3.2": ("工艺工程师", ("WRITE_ROLES",)),
    "3.3": ("工艺工程师", ("WRITE_ROLES",)),
    "4.1": ("财务负责人", ("COST_ROLES",)),
    "4.2": ("财务负责人", ("COST_ROLES",)),
    "4.3": ("财务负责人", ("COST_ROLES",)),
    "5.1": ("工艺工程师", ("WRITE_ROLES",)),
    "5.2": ("审核人", ("REVIEW_ROLES", "MANAGER_ROLES")),
    "5.3": ("工艺技术总监", ("DIRECTOR_ROLES", "MANAGER_ROLES")),
}


def _role_names(key: str) -> Tuple[str, Tuple[str, ...]]:
    label, groups = _STEP_ROLES.get(key, ("", ()))
    names: List[str] = []
    for group in groups:
        names.extend(sorted(getattr(auth, group, ()) or ()))
    return label, tuple(dict.fromkeys(names))


def _has_role(key: str, role: str) -> bool:
    """当前用户能不能执行这一步的主按钮。查不到角色表时一律放行为 False（只读）。"""
    _, names = _role_names(key)
    if not names:
        return True
    return bool(role) and role in names


# --------------------------------------------------------------------------- #
# 取数：一次读完 13 个子步骤判定需要的数据；单项失败只记错误，不抛。
# --------------------------------------------------------------------------- #
_LOADERS = (
    ("meta", lambda pid: store.load_meta(pid)),
    ("requirement", lambda pid: store.load_requirement(pid)),
    ("ir", lambda pid: store.load_ir(pid)),
    ("plan", lambda pid: integration.load_plan(pid)),
    ("review", lambda pid: cost_review.load_review(pid)),
    ("summary", lambda pid: store.load_summary(pid)),
    ("report", lambda pid: store.load_process_report(pid)),
    ("audit", lambda pid: store.list_audit(pid)),
    # 包装（DWG 图纸）项目的解析产物写在另外两份文档里（不经 `store.load_ir`）：
    # `cad_ir.load_ir` 是图纸解析的 IR 快照（`meta.cad_ir_rev` 就是它的版本号），
    # `packaging_parts.load_parts` 是同一趟解析拆出来的零件文档（唯一事实源）。
    ("packaging_cad_ir", lambda pid: packaging_cad_ir.load_ir(pid)),
    ("packaging_parts", lambda pid: packaging_parts.load_parts(pid)),
)


def _read_facts(project_id: str) -> Dict[str, Any]:
    facts: Dict[str, Any] = {"errors": {}}
    for name, loader in _LOADERS:
        try:
            facts[name] = loader(project_id)
        except Exception as exc:  # noqa: BLE001 - 只读派生视图，单点失败不拖垮整份
            facts[name] = None
            facts["errors"][name] = f"{type(exc).__name__}: {exc}"
    return facts


def _ir_model(ir_dict: Optional[dict]) -> Optional[DesignIR]:
    if not ir_dict:
        return None
    try:
        return DesignIR(**ir_dict)
    except Exception:  # noqa: BLE001
        return None


def _cost_data(project_id: str, facts: Dict[str, Any]) -> Optional[dict]:
    """2.3 的唯一汇总口径（cost_review.summarize），不另算一份。

    零件来源与技术侧同源：IR 里有零件就用 IR；包装（DWG 图纸）项目的零件在
    `packaging_parts` 文档里（IR 为空），这一份必须一起交给同一个口径，否则
    「零件拆出来了、成本这一步一件都看不见」（Spec §2.1）。
    """
    ir = _ir_model(facts.get("ir"))
    if not (ir and ir.parts):
        ir = cost_review.ir_from_packaging_parts(facts.get("packaging_parts")) or ir
    try:
        return cost_review.summarize(project_id, ir, facts.get("plan"))
    except Exception:  # noqa: BLE001
        return None


def _excluded_parts(facts: Dict[str, Any]) -> set:
    """被登记为「已排除」的零件（当前模型里可能没有这个字段，缺了就是空集）。"""
    excluded = set()
    for holder in (facts.get("plan"), facts.get("review")):
        value = getattr(holder, "excluded_parts", None)
        if isinstance(value, (list, tuple, set)):
            excluded.update(str(item) for item in value)
    return excluded


#: 2.1「有 IR 但一件零件都没识别出来」时的既有文案（技术侧与包装侧逐字共用）。
NO_PARTS_MISSING = "本次没有识别出零件，需人工确认无零件结果后方可继续"


def _no_parts_verdict(facts: Dict[str, Any]) -> dict:
    """有解析产物、但零件为 0：审计里有过 `parse_no_parts_confirmed` → 算完成。

    技术侧与包装侧逐字共用同一支（Spec §3.2 第 2 条要求两侧同形）。
    """
    confirmed = any(str((item or {}).get("action") or "") == "parse_no_parts_confirmed"
                    for item in (facts.get("audit") or []))
    if confirmed:
        return {"status": "confirmed", "completed": True}
    return {"status": "generated", "completed": False, "missing": [NO_PARTS_MISSING]}


def _packaging_drawing_done(facts: Dict[str, Any]) -> Optional[dict]:
    """包装（DWG 图纸）项目的 2.1 判据；没有包装解析产物时返回 None（沿用技术侧口径）。

    包装项目把解析产物写在两份文档里（`packaging_cad_ir` / `packaging_parts`），
    不经 `store.load_ir`：只认其中一份会把"真跑过的解析"误判成"图纸还没有解析"。
    """
    if not facts.get("packaging_cad_ir"):
        return None
    pack = facts.get("packaging_parts")
    parts: List[Any] = list(pack.get("parts") or []) if isinstance(pack, dict) else []
    total = 0
    if isinstance(pack, dict):
        stats = pack.get("stats")
        if isinstance(stats, dict):
            try:
                total = int(stats.get("part_total") or 0)
            except (TypeError, ValueError):
                total = 0
    if parts or total > 0:
        return {"status": "generated", "completed": True}
    return _no_parts_verdict(facts)


# --------------------------------------------------------------------------- #
# 每个子步骤的完成判定（第 5 节表格）。
# 返回 (status, completed, missing_requirements, extra_blockers)。
# --------------------------------------------------------------------------- #
def _judge(key: str, project_id: str, facts: Dict[str, Any]) -> dict:
    req = facts.get("requirement") or {}
    req_status = str(req.get("status") or "")
    plan = facts.get("plan")
    review = facts.get("review")
    report = facts.get("report") or {}
    summary = facts.get("summary") or {}
    report_status = str(report.get("status") or "")
    ir = _ir_model(facts.get("ir"))
    parts = list(ir.parts) if ir else []

    if key == "1.1":
        if not req_status:
            return {"status": "not_started", "completed": False}
        done = req_status in ("pending_confirmation", "pending_review", "approved")
        return {"status": "confirmed" if done else "in_progress", "completed": done}

    if key == "1.2":
        if not req_status:
            return {"status": "not_started", "completed": False,
                    "missing": ["需求单还没有创建"]}
        done = req_status in ("pending_review", "approved")
        if done:
            return {"status": "confirmed", "completed": True}
        if req_status == "pending_confirmation":
            return {"status": "awaiting_confirmation", "completed": False,
                    "missing": ["需求确认尚未提交"]}
        # 草稿（含退回后的 `rejected`）走这里：这一步**根本没开始**，不许报"进行中"，
        # 还要说清卡在哪（Spec `tech-projection-step-state-must-agree-with-its-reasons.md` §2.2）。
        return {"status": "not_started", "completed": False,
                "missing": ["需求单当前是「%s」，还没提交确认" % req_status]}

    if key == "1.3":
        if req_status == "approved":
            return {"status": "approved", "completed": True}
        if req_status == "pending_review":
            return {"status": "in_review", "completed": False, "missing": ["需求审核尚未给出结论"]}
        if not req_status:
            return {"status": "not_started", "completed": False}
        # 草稿 / 已退回 / 只走到"待确认"：审核一步都没开始，不许报"进行中"（Spec §2.2）。
        return {"status": "not_started", "completed": False,
                "missing": ["需求单当前是「%s」，还没送审" % req_status]}

    if key == "2.1":
        if ir is None:
            packaged = _packaging_drawing_done(facts)
            if packaged is not None:
                return packaged
            return {"status": "not_started", "completed": False, "missing": ["图纸还没有解析"]}
        if parts:
            return {"status": "generated", "completed": True}
        return _no_parts_verdict(facts)

    if key == "3.1":
        if plan is None:
            return {"status": "not_started", "completed": False}
        # 这一步**自己产出的东西**就是整合分析的结果（`runIntegration`：参数推荐 + 组装工艺）。
        # 「装配图不是必需的」—— 包装（DWG 图纸）项目既没有整合图纸、IR 里也没有零件
        # （零件在 `packaging_parts`），只看那两样会把"真跑过的分析"永远判成没跑过。
        done = (bool(plan.drawings) or bool(parts)
                or plan.params is not None or plan.process is not None)
        return {"status": "generated" if done else "not_started", "completed": done,
                "missing": [] if done else ["整合分析还没有执行"]}

    if key == "3.2":
        if plan is None or plan.params is None:
            return {"status": "not_started", "completed": False, "missing": ["参数推荐还没有生成"]}
        if plan.params_confirmed:
            return {"status": "confirmed", "completed": True}
        return {"status": "awaiting_confirmation", "completed": False,
                "missing": ["参数推荐尚未人工确认"]}

    if key == "3.3":
        if plan is None or plan.process is None:
            return {"status": "not_started", "completed": False, "missing": ["组装工艺还没有生成"]}
        if plan.process_confirmed:
            return {"status": "confirmed", "completed": True}
        return {"status": "generated", "completed": False, "missing": ["组装工艺尚未确认"]}

    if key in ("4.1", "4.2", "4.3"):
        return _judge_cost(key, project_id, facts, plan, review)

    if key == "5.1":
        if not report:
            if summary:
                return {"status": "generated", "completed": False, "missing": ["报告结果还没有落库"]}
            return {"status": "not_started", "completed": False}
        done = bool(report.get("report_no") and report.get("prepared_at"))
        return {"status": _REPORT_STATUS.get(report_status, "generated"),
                "completed": done, "missing": [] if done else ["报告编号/编制时间还没有落库"]}

    if key == "5.2":
        # 「报告已送审且审核结论明确（通过或驳回）」才算完成；驳回也是结论，只是报告要重做。
        if report_status in ("approved", "published"):
            return {"status": "approved", "completed": True}
        if report_status == "rejected":
            return {"status": "in_review", "completed": True,
                    "missing": ["报告已被驳回，需要修订后重新送审"]}
        if report_status == "in_review":
            return {"status": "in_review", "completed": False, "missing": ["报告审核还没有结论"]}
        return {"status": "not_started", "completed": False}

    if key == "5.3":
        handoff = getattr(plan, "quote_handoff", None) if plan is not None else None
        if report_status != "published":
            if not report:
                return {"status": "not_started", "completed": False}
            return {"status": _REPORT_STATUS.get(report_status, "generated"), "completed": False,
                    "missing": ["报告还没有正式发布"]}
        if handoff:
            return {"status": "published", "completed": True}
        return {"status": "published", "completed": False, "missing": ["报告已发布，尚未回传报价"]}

    return {"status": "not_started", "completed": False}


def _judge_cost(key: str, project_id: str, facts: Dict[str, Any],
                plan, review) -> dict:
    data = _cost_data(project_id, facts)
    if key == "4.1":
        if not data:
            return {"status": "not_started", "completed": False, "missing": ["成本还没有测算"]}
        parts = data.get("parts") or []
        if not parts:
            return {"status": "not_started", "completed": False, "missing": ["还没有可测算的零件"]}
        excluded = _excluded_parts(facts)
        missing = [pid for pid in (data.get("counts", {}).get("missing") or [])
                   if pid not in excluded]
        if missing:
            return {"status": "in_progress", "completed": False,
                    "missing": ["还有零件未测算成本：" + "、".join(missing)]}
        return {"status": "confirmed", "completed": True}
    if key == "4.2":
        # 同一阶段内状态要自洽：零件一件都取不到时这一步根本还没开始（4.1 判的就是
        # 「还没有可测算的零件」），此时报「进行中」是假话（Spec §2.3）。
        if not data or not (data.get("parts") or []):
            return {"status": "not_started", "completed": False}
        if (data.get("assembly") or {}).get("has_cost"):
            return {"status": "confirmed", "completed": True}
        return {"status": "in_progress", "completed": False, "missing": ["整机（组装）成本还没有测算"]}
    # 4.3 汇总
    if review is not None and getattr(review, "confirmed", False):
        return {"status": "confirmed", "completed": True}
    if data and (data.get("assembly") or {}).get("has_cost"):
        return {"status": "awaiting_confirmation", "completed": False,
                "missing": ["成本还没有正式确认"]}
    if data and (data.get("counts", {}).get("parts_costed") or 0) > 0:
        return {"status": "in_progress", "completed": False, "missing": ["成本汇总还没有完成"]}
    return {"status": "not_started", "completed": False}


# --------------------------------------------------------------------------- #
# 组装：把判定拼成契约字段
# --------------------------------------------------------------------------- #
def _rows_for(project_id: str, facts: Dict[str, Any], role: str) -> List[dict]:
    rows: List[dict] = []
    keys = [row["sub"] for row in stages_table.STAGES]
    done_by_key: Dict[str, bool] = {}
    judged: Dict[str, dict] = {}
    for spec in stages_table.STAGES:
        key = spec["sub"]
        try:
            verdict = _judge(key, project_id, facts)
        except Exception as exc:  # noqa: BLE001 - 单个子步骤数据损坏只记这一条 blocked
            verdict = {"status": "blocked", "completed": False,
                       "missing": [], "blockers": [f"本步数据读取失败：{type(exc).__name__}: {exc}"]}
        judged[key] = verdict
        done_by_key[key] = bool(verdict.get("completed"))
        rows.append({"spec": spec, "verdict": verdict})

    out: List[dict] = []
    for index, item in enumerate(rows):
        spec = item["spec"]
        verdict = item["verdict"]
        key = spec["sub"]
        completed = bool(verdict.get("completed"))
        status = verdict.get("status") or "not_started"
        missing = list(verdict.get("missing") or [])
        blockers = list(verdict.get("blockers") or [])

        # 前置步骤（本子步骤之前的全部子步骤）没完成 → 逐条说清缺哪一步。
        # 已完成的子步骤**跨过了**这道门（`actionable = completed || 前置步骤已完成`）：
        # 再挂「请先完成 X」就是自相矛盾 —— 正常首次流程里 2.1 会 8/8 完成却挂着
        # 「请先完成 1.1」（Spec `tech-projection-step-state-must-agree-with-its-reasons.md` §2.1）。
        if not completed:
            for prior in keys[:index]:
                if not done_by_key.get(prior):
                    prior_spec = next(row for row in stages_table.STAGES if row["sub"] == prior)
                    blockers.append(f"请先完成 {prior} {prior_spec['sub_title']}")
                    break

        # 权限：不足时不可执行并写明所需角色（不影响 viewable）。
        role_label, _ = _role_names(key)
        if role and not _has_role(key, role):
            blockers.append(f"需要{role_label}角色才能执行本步" if role_label else "当前角色不能执行本步")
        elif not role and role_label:
            blockers.append(f"需要{role_label}角色才能执行本步")

        blocked = status == "blocked"
        actionable = (completed or not blockers) and not blocked and _has_role(key, role)
        if role_label and not _has_role(key, role):
            actionable = False

        stale = bool(getattr(facts.get("meta"), "get", lambda *_: None)("derived_results_stale")) \
            if isinstance(facts.get("meta"), dict) else False
        out.append({
            "key": key,
            "phase_no": int(spec["phase"]),
            "phase_title": spec["phase_title"],
            "sub": spec["sub"],
            "sub_title": spec["sub_title"],
            "stage_id": spec["stage_id"],
            "view": spec["view"],
            "status": status if status in STATUS_ENUM else "not_started",
            "viewable": True,
            "actionable": bool(actionable),
            "completed": completed,
            "stale": bool(stale and completed),
            "blocked_reasons": blockers,
            "missing_requirements": missing,
            "required_role": role_label,
            "primary_action": _PRIMARY_ACTIONS.get(key, ""),
            "next_stage": keys[index + 1] if index + 1 < len(keys) else "",
        })
    return out


def _phases(rows: List[dict]) -> List[dict]:
    by_key = {row["key"]: row for row in rows}
    out: List[dict] = []
    for phase in stages_table.PHASES:
        subs = list(phase.get("subs") or ())
        members = [by_key[sub] for sub in subs if sub in by_key]
        completed = bool(members) and all(row["completed"] for row in members)
        status = "confirmed"
        if not completed:
            for row in members:
                if not row["completed"]:
                    status = row["status"]
                    break
        out.append({"no": int(phase["no"]), "title": phase["title"],
                    "completed": completed, "status": status})
    return out


def _next_action(rows: List[dict]) -> Optional[dict]:
    for row in rows:
        if not row["completed"] and row["actionable"]:
            return {"key": row["key"], "sub": row["sub"], "sub_title": row["sub_title"],
                    "stage_id": row["stage_id"], "view": row["view"],
                    "primary_action": row["primary_action"], "required_role": row["required_role"],
                    "blocked_reasons": list(row["blocked_reasons"])}
    for row in rows:
        if not row["completed"]:
            return {"key": row["key"], "sub": row["sub"], "sub_title": row["sub_title"],
                    "stage_id": row["stage_id"], "view": row["view"],
                    "primary_action": row["primary_action"], "required_role": row["required_role"],
                    "blocked_reasons": list(row["blocked_reasons"])}
    return None


def build_projection(project_id: str, user: Optional[dict] = None) -> dict:
    """唯一流程投影：只读、幂等；任一子步骤数据损坏只记该步 blocked，不整份 500。"""
    user = user or {}
    role = str(user.get("role") or "")
    facts = _read_facts(project_id)
    rows = _rows_for(project_id, facts, role)
    errors = facts.get("errors") or {}
    ok = bool(facts.get("meta")) and not errors
    refresh_error = ""
    if not facts.get("meta"):
        refresh_error = "项目数据读取失败或项目不存在"
    elif errors:
        refresh_error = "部分数据读取失败：" + "、".join(sorted(errors))
    return {
        "project_id": project_id,
        "generated_at": now_cst_str(),
        "refresh_ok": ok,
        "refresh_error": refresh_error,
        "phases": _phases(rows),
        "stages": rows,
        "next_action": _next_action(rows),
    }

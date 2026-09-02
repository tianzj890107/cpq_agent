# -*- coding: utf-8 -*-
"""配置报价CPQ —— 报价工作流（卡片 / 步骤状态 / 任务流转）。

按《报价工作流DA梳理.xlsx》落地剩余 5 张表（登录相关的 2 张见 cpq_auth.py）：
  - cpq_wf_step_perm    步骤 -> 负责角色（数据驱动“谁能做哪步”，随模块初始化写入种子）
  - cpq_wf_card         报价卡片主表（1:1 对应一个报价会话 cpq_history/<sid>.json）
  - cpq_wf_card_step    卡片每一步的状态 / 完成人 / 数据快照
  - cpq_wf_task         任务流转（把卡片发给指定角色 / 指定个人 / 公共任务池）
  - cpq_wf_task_event   流转审计日志

  - cpq_wf_message      站内消息（任务流提醒；DA 原清单外的补充表）

存储后端复用 cpq_auth：**只用线上 Postgres 的 cpq_wf schema，无任何本地回落**，
故本模块必须在 cpq_auth.init() 之后再 init()。

只服务**报价助手**；配置 / 规则 / 技术工艺三个助手不接入。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import cpq_auth
from cpq_auth import ROLES

# 报价 6 步与负责角色（DA「种子数据-步骤角色」sheet）
QUOTE_STEPS = [
    (1, "确认需求配置", "sales_mgr"),
    (2, "工艺确认", "process_mgr"),
    (3, "定价-利润加成", "sales_mgr"),
    (4, "报价-其他加价项", "sales_mgr"),
    (5, "报价方案", "sales_mgr"),
    (6, "输出报价单", "sales_mgr"),
]
ASSISTANT = "quote"
LAST_STEP = len(QUOTE_STEPS)

TARGET_TYPES = ("role", "user", "public")

# 任务类型。第 1 步匹配不到合适标品（cpq_match 最高分低于 70）时，销售经理不是把
# 卡片交给下一步，而是请工艺经理去技术工艺**新建一个产品** —— 那是一条支线，
# 报价卡片留在原步骤等新产品出来，所以它不能走 handoff 那套推进步骤的逻辑。
TASK_KIND_HANDOFF = "handoff"
TASK_KIND_TECH_NEW = "tech_new_product"
TASK_KINDS = (TASK_KIND_HANDOFF, TASK_KIND_TECH_NEW)
TASK_KIND_LABELS = {
    TASK_KIND_HANDOFF: "转交工艺确认",
    TASK_KIND_TECH_NEW: "新增工艺",
}
# 新增工艺固定发给工艺经理：这件事只有他能做，让销售再选一次角色只会选错。
TECH_NEW_ROLE = "process_mgr"

# 卡片总状态（awaiting_handoff = 本步已完成、下一步归别人，正等着推送任务流）
STATUS_LABELS = {
    "draft": "草稿",
    "in_progress": "处理中",
    "awaiting_handoff": "待转交",
    "handoff_pending": "已转交·待领取",
    "completed": "已完成",
    "archived": "已归档",
}


class WfError(Exception):
    """带用户可见文案的业务错误。"""


# ---------------------------------------------------------------------------
# 建表
# ---------------------------------------------------------------------------
def _ddl_pg(schema: str) -> list:
    return [
        f"""CREATE TABLE IF NOT EXISTS {schema}.cpq_wf_step_perm (
                id             bigint PRIMARY KEY,
                assistant_type varchar(16) NOT NULL,
                step_no        int         NOT NULL,
                step_name      varchar(64) NOT NULL,
                role_code      varchar(32) NOT NULL,
                UNIQUE (assistant_type, step_no)
            )""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.cpq_wf_card (
                card_id          bigint PRIMARY KEY,
                session_id       varchar(32) NOT NULL UNIQUE,
                assistant_type   varchar(16) NOT NULL DEFAULT 'quote',
                title            varchar(255),
                customer         varchar(128),
                project_name     varchar(128),
                current_step     int         NOT NULL DEFAULT 1,
                overall_status   varchar(24) NOT NULL DEFAULT 'draft',
                creator_user_id  bigint
                                 REFERENCES {schema}.cpq_wf_user(user_id) ON DELETE SET NULL,
                current_owner    bigint
                                 REFERENCES {schema}.cpq_wf_user(user_id) ON DELETE SET NULL,
                created_at       timestamptz NOT NULL DEFAULT now(),
                updated_at       timestamptz NOT NULL DEFAULT now()
            )""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.cpq_wf_card_step (
                card_step_id  bigint PRIMARY KEY,
                card_id       bigint      NOT NULL
                              REFERENCES {schema}.cpq_wf_card(card_id) ON DELETE CASCADE,
                step_no       int         NOT NULL,
                step_name     varchar(64),
                role_code     varchar(32),
                status        varchar(16) NOT NULL DEFAULT 'pending',
                owner_user_id bigint
                              REFERENCES {schema}.cpq_wf_user(user_id) ON DELETE SET NULL,
                data_snapshot jsonb,
                started_at    timestamptz,
                completed_at  timestamptz,
                UNIQUE (card_id, step_no)
            )""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.cpq_wf_task (
                task_id            bigint PRIMARY KEY,
                card_id            bigint      NOT NULL
                                   REFERENCES {schema}.cpq_wf_card(card_id) ON DELETE CASCADE,
                from_user_id       bigint
                                   REFERENCES {schema}.cpq_wf_user(user_id) ON DELETE SET NULL,
                from_step_no       int,
                target_type        varchar(12) NOT NULL,
                target_role_code   varchar(32),
                target_user_id     bigint
                                   REFERENCES {schema}.cpq_wf_user(user_id) ON DELETE SET NULL,
                claimed_by_user_id bigint
                                   REFERENCES {schema}.cpq_wf_user(user_id) ON DELETE SET NULL,
                status             varchar(16) NOT NULL DEFAULT 'open',
                source_label       varchar(255),
                note               varchar(500),
                created_at         timestamptz NOT NULL DEFAULT now(),
                claimed_at         timestamptz,
                completed_at       timestamptz
            )""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.cpq_wf_task_event (
                event_id      bigint PRIMARY KEY,
                card_id       bigint NOT NULL
                              REFERENCES {schema}.cpq_wf_card(card_id) ON DELETE CASCADE,
                task_id       bigint
                              REFERENCES {schema}.cpq_wf_task(task_id) ON DELETE SET NULL,
                actor_user_id bigint
                              REFERENCES {schema}.cpq_wf_user(user_id) ON DELETE SET NULL,
                action        varchar(24) NOT NULL,
                from_step     int,
                to_step       int,
                comment       varchar(500),
                created_at    timestamptz NOT NULL DEFAULT now()
            )""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.cpq_wf_message (
                message_id  bigint PRIMARY KEY,
                user_id     bigint      NOT NULL
                            REFERENCES {schema}.cpq_wf_user(user_id) ON DELETE CASCADE,
                msg_type    varchar(24) NOT NULL,
                title       varchar(255),
                body        varchar(1000),
                card_id     bigint
                            REFERENCES {schema}.cpq_wf_card(card_id) ON DELETE CASCADE,
                task_id     bigint
                            REFERENCES {schema}.cpq_wf_task(task_id) ON DELETE SET NULL,
                session_id  varchar(32),
                step_no     int,
                is_read     boolean     NOT NULL DEFAULT false,
                created_at  timestamptz NOT NULL DEFAULT now()
            )""",
        # 任务类型与随任务带走的资料。老库已经建过 cpq_wf_task，CREATE TABLE IF NOT EXISTS
        # 不会补列，所以这两条必须是显式的 ADD COLUMN IF NOT EXISTS（PG 9.6+ 支持，幂等）。
        #   task_kind = handoff          转交下一步（原有行为，默认值保证老数据不变）
        #             = tech_new_product 新增工艺：标品匹配不上，转技术工艺新建产品
        #   payload   = 随任务带过去的需求正文/文档摘要/匹配结果（jsonb）
        f"ALTER TABLE {schema}.cpq_wf_task ADD COLUMN IF NOT EXISTS"
        f" task_kind varchar(24) NOT NULL DEFAULT 'handoff'",
        f"ALTER TABLE {schema}.cpq_wf_task ADD COLUMN IF NOT EXISTS payload jsonb",
        f"CREATE INDEX IF NOT EXISTS idx_wf_task_card ON {schema}.cpq_wf_task(card_id)",
        f"CREATE INDEX IF NOT EXISTS idx_wf_task_status ON {schema}.cpq_wf_task(status)",
        f"CREATE INDEX IF NOT EXISTS idx_wf_cardstep_card ON {schema}.cpq_wf_card_step(card_id)",
        f"CREATE INDEX IF NOT EXISTS idx_wf_msg_user ON {schema}.cpq_wf_message(user_id, is_read)",
    ]

def init() -> str:
    """建表 + 写入步骤角色种子。须在 cpq_auth.init() 之后调用。"""
    conn = cpq_auth._connect()
    try:
        for sql in _ddl_pg(cpq_auth.WF_SCHEMA):
            cpq_auth._exec(conn, sql)
        # 种子：步骤 -> 负责角色（幂等，按 step_no 校正名称与角色）
        for no, name, role in QUOTE_STEPS:
            cur = cpq_auth._exec(
                conn, "SELECT id FROM cpq_wf_step_perm WHERE assistant_type = %s AND step_no = %s",
                (ASSISTANT, no))
            if cur.fetchone():
                cpq_auth._exec(
                    conn, "UPDATE cpq_wf_step_perm SET step_name = %s, role_code = %s"
                          " WHERE assistant_type = %s AND step_no = %s",
                    (name, role, ASSISTANT, no))
            else:
                cpq_auth._exec(
                    conn, "INSERT INTO cpq_wf_step_perm (id, assistant_type, step_no, step_name, role_code)"
                          " VALUES (%s,%s,%s,%s,%s)",
                    (_new_id(conn), ASSISTANT, no, name, role))
        return f"报价工作流表已就绪（{len(QUOTE_STEPS)} 步角色种子）"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _new_id(conn) -> int:
    return cpq_auth.cpq_db.snow_next_id(conn)


def _now():
    return datetime.now(timezone.utc)


def _ts(dt):
    return cpq_auth._ts(dt)


def _commit(conn):
    """连接是 autocommit，这里留空以保持调用方写法统一。"""
    return None


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else (v or None)


def _uid(v):
    return str(v) if v is not None else None


def step_perms() -> list:
    """步骤 -> 负责角色（读库，库里没有则回落代码常量）。"""
    conn = cpq_auth._connect()
    try:
        cur = cpq_auth._exec(
            conn, "SELECT step_no, step_name, role_code FROM cpq_wf_step_perm"
                  " WHERE assistant_type = %s ORDER BY step_no", (ASSISTANT,))
        rows = cur.fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()
    if not rows:
        rows = QUOTE_STEPS
    return [{"step_no": r[0], "step_name": r[1], "role_code": r[2],
             "role_name": ROLES.get(r[2], r[2])} for r in rows]


def role_of_step(step_no: int) -> str:
    for p in step_perms():
        if p["step_no"] == int(step_no or 0):
            return p["role_code"]
    return ""


def can_do_step(user: dict, step_no: int) -> bool:
    return bool(user) and user.get("role_code") == role_of_step(step_no)


# ---------------------------------------------------------------------------
# 卡片
# ---------------------------------------------------------------------------
_CARD_COLS = ("card_id", "session_id", "assistant_type", "title", "customer", "project_name",
              "current_step", "overall_status", "creator_user_id", "current_owner",
              "created_at", "updated_at")


def _card_row(row) -> dict:
    if not row:
        return None
    d = dict(zip(_CARD_COLS, row))
    for k in ("card_id", "creator_user_id", "current_owner"):
        d[k] = _uid(d[k])
    for k in ("created_at", "updated_at"):
        d[k] = _iso(d[k])
    return d


def _fetch_card(conn, session_id: str):
    cur = cpq_auth._exec(
        conn, f"SELECT {', '.join(_CARD_COLS)} FROM cpq_wf_card WHERE session_id = %s",
        (session_id,))
    return _card_row(cur.fetchone())


def get_card(session_id: str) -> dict:
    conn = cpq_auth._connect()
    try:
        return _fetch_card(conn, session_id)
    finally:
        conn.close()


def sync_card(session_id: str, user: dict, title: str = "", customer: str = "",
              project_name: str = "", current_step: int = None) -> dict:
    """新建或更新卡片（报价会话每次保存/推进时由前端调用）。
    创建人 = 首次同步的登录用户；current_owner 首次同步时也归他。"""
    session_id = (session_id or "").strip()
    if not session_id:
        raise WfError("缺少会话 ID")
    uid = int(user["user_id"]) if user else None
    conn = cpq_auth._connect()
    try:
        card = _fetch_card(conn, session_id)
        now = _now()
        if not card:
            cid = _new_id(conn)
            cpq_auth._exec(
                conn, "INSERT INTO cpq_wf_card (card_id, session_id, assistant_type, title,"
                      " customer, project_name, current_step, overall_status, creator_user_id,"
                      " current_owner, created_at, updated_at)"
                      " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (cid, session_id, ASSISTANT, title or None, customer or None, project_name or None,
                 int(current_step or 1), "draft", uid, uid, _ts(now), _ts(now)))
            _log(conn, cid, None, uid, "create", None, int(current_step or 1), "创建报价卡片")
            _ensure_steps(conn, cid)
            _commit(conn)
            return _fetch_card(conn, session_id)

        # 已存在：只更新非空字段；步骤只允许前进（避免回看历史步骤把进度写退）
        sets, args = ["updated_at = %s"], [_ts(now)]
        for col, val in (("title", title), ("customer", customer), ("project_name", project_name)):
            if val:
                sets.append(f"{col} = %s")
                args.append(val)
        step = int(current_step or 0)
        if step and step > int(card["current_step"] or 1):
            sets.append("current_step = %s")
            args.append(step)
            sets.append("overall_status = %s")
            args.append("completed" if step >= LAST_STEP else "in_progress")
        args.append(session_id)
        cpq_auth._exec(conn, f"UPDATE cpq_wf_card SET {', '.join(sets)} WHERE session_id = %s", args)
        _ensure_steps(conn, int(card["card_id"]))
        _commit(conn)
        return _fetch_card(conn, session_id)
    finally:
        conn.close()


def _ensure_steps(conn, card_id: int):
    """为卡片补齐 6 行步骤状态（幂等）。"""
    cur = cpq_auth._exec(conn, "SELECT step_no FROM cpq_wf_card_step WHERE card_id = %s", (card_id,))
    have = {r[0] for r in cur.fetchall()}
    for no, name, role in QUOTE_STEPS:
        if no in have:
            continue
        cpq_auth._exec(
            conn, "INSERT INTO cpq_wf_card_step (card_step_id, card_id, step_no, step_name,"
                  " role_code, status) VALUES (%s,%s,%s,%s,%s,'pending')",
            (_new_id(conn), card_id, no, name, role))


_STEP_COLS = ("step_no", "step_name", "role_code", "status", "owner_user_id",
              "started_at", "completed_at")


def card_steps(card_id) -> list:
    conn = cpq_auth._connect()
    try:
        cur = cpq_auth._exec(
            conn, f"SELECT {', '.join(_STEP_COLS)} FROM cpq_wf_card_step"
                  f" WHERE card_id = %s ORDER BY step_no", (int(card_id),))
        out = []
        for r in cur.fetchall():
            d = dict(zip(_STEP_COLS, r))
            d["owner_user_id"] = _uid(d["owner_user_id"])
            d["started_at"] = _iso(d["started_at"])
            d["completed_at"] = _iso(d["completed_at"])
            d["role_name"] = ROLES.get(d["role_code"], d["role_code"])
            out.append(d)
        return out
    finally:
        conn.close()


def start_step(session_id: str, step_no: int, user: dict) -> dict:
    """把某一步标记为「进行中」（接手人确认开始本步骤时调用；角色不符会被拒绝）。

    这一步是**显式**的：任务转交过来后，接手人要先确认「开始本步骤」，智能体才开工，
    避免卡片一到手就自动跑起来。"""
    if not user:
        raise WfError("请先登录")
    step_no = int(step_no or 0)
    if not (1 <= step_no <= LAST_STEP):
        raise WfError("步骤号无效")
    if not can_do_step(user, step_no):
        need = ROLES.get(role_of_step(step_no), "指定角色")
        raise WfError(f"第 {step_no} 步需要「{need}」完成，你当前是「{user.get('role_name')}」")
    conn = cpq_auth._connect()
    try:
        card = _fetch_card(conn, session_id)
        if not card:
            raise WfError("卡片不存在，请先保存报价会话")
        cid = int(card["card_id"])
        now = _now()
        # 只把 pending 推进到 in_progress；已完成(done)的步骤不回退
        cpq_auth._exec(
            conn, "UPDATE cpq_wf_card_step SET status = 'in_progress', owner_user_id = %s,"
                  " started_at = COALESCE(started_at, %s)"
                  " WHERE card_id = %s AND step_no = %s AND status <> 'done'",
            (int(user["user_id"]), _ts(now), cid, step_no))
        cpq_auth._exec(
            conn, "UPDATE cpq_wf_card SET overall_status = 'in_progress', current_owner = %s,"
                  " updated_at = %s WHERE card_id = %s",
            (int(user["user_id"]), _ts(now), cid))
        _commit(conn)
        return _fetch_card(conn, session_id)
    finally:
        conn.close()


def step_snapshot(session_id: str, step_no: int):
    """取某一步确认时存下的表单快照（card_step.data_snapshot）。

    交接过来的卡片，接手人本地 DOM 里没有前面各步的数据——尤其第 1 步的产品信息/技术参数
    是前端直接填的、不在智能体事件流里，只能从这里恢复。"""
    conn = cpq_auth._connect()
    try:
        cur = cpq_auth._exec(
            conn, "SELECT s.data_snapshot FROM cpq_wf_card_step s"
                  " JOIN cpq_wf_card c ON c.card_id = s.card_id"
                  " WHERE c.session_id = %s AND s.step_no = %s",
            (session_id, int(step_no or 0)))
        row = cur.fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        return None
    v = row[0]
    if isinstance(v, (dict, list)):      # psycopg 会把 jsonb 直接反序列化
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def complete_step(session_id: str, step_no: int, user: dict, snapshot: str = "") -> dict:
    """把某一步标记为完成（角色不符会被拒绝）。

    返回 {card, need_handoff, next_step_no, next_step_name, next_role_code/name}：
    下一步若归属别的角色，卡片置为 awaiting_handoff（待转交），前端据此强制走推送任务流。"""
    if not user:
        raise WfError("请先登录")
    step_no = int(step_no or 0)
    if not (1 <= step_no <= LAST_STEP):
        raise WfError("步骤号无效")
    if not can_do_step(user, step_no):
        need = ROLES.get(role_of_step(step_no), "指定角色")
        raise WfError(f"第 {step_no} 步需要「{need}」完成，你当前是「{user.get('role_name')}」")
    conn = cpq_auth._connect()
    try:
        card = _fetch_card(conn, session_id)
        if not card:
            raise WfError("卡片不存在，请先保存报价会话")
        cid = int(card["card_id"])
        now = _now()
        # data_snapshot 按 DA 是 jsonb：前端传的是 JSON.stringify 的结果，显式 ::jsonb 转换；
        # 空串或不是合法 JSON 时存 NULL，不能让一次快照把整步确认搞失败。
        snap = (snapshot or "").strip()[:200000]
        if snap:
            try:
                json.loads(snap)
            except ValueError:
                snap = None
        else:
            snap = None
        cpq_auth._exec(
            conn, "UPDATE cpq_wf_card_step SET status = 'done', owner_user_id = %s,"
                  " data_snapshot = %s::jsonb, completed_at = %s,"
                  " started_at = COALESCE(started_at, %s) WHERE card_id = %s AND step_no = %s",
            (int(user["user_id"]), snap, _ts(now), _ts(now), cid, step_no))
        # 下一步归属哪个角色 -> 决定卡片新状态：本人还能继续=in_progress；换人做=awaiting_handoff（待转交）
        done_all = step_no >= LAST_STEP
        nxt = min(step_no + 1, LAST_STEP)
        next_role = "" if done_all else role_of_step(nxt)
        need_handoff = bool(next_role) and next_role != user.get("role_code")
        status = "completed" if done_all else ("awaiting_handoff" if need_handoff else "in_progress")
        cpq_auth._exec(
            conn, "UPDATE cpq_wf_card SET current_step = %s, overall_status = %s, updated_at = %s"
                  " WHERE card_id = %s", (nxt, status, _ts(now), cid))
        # 我因某个任务接手这张卡片、现在把那一步做完了 -> 该任务随之结束，不再滞留在「我的任务」里
        cpq_auth._exec(
            conn, "UPDATE cpq_wf_task SET status = 'completed', completed_at = %s"
                  " WHERE card_id = %s AND claimed_by_user_id = %s AND status = 'claimed'",
            (_ts(now), cid, int(user["user_id"])))
        _log(conn, cid, None, int(user["user_id"]), "step_done", step_no, nxt,
             f"{user.get('display_name')} 完成第 {step_no} 步")
        # 自动推送：下一步换角色时（如工艺经理确认完第 2 步），不弹推送选择，
        # 直接把任务自动发给**项目创建人**（卡片 creator_user_id，即发起这单报价的销售经理）。
        # 创建人就是自己 / 账号失效 / 角色与下一步不匹配时才退回手动推送。
        auto_target = None
        if need_handoff:
            try:
                creator_id = int(card.get("creator_user_id") or 0)
            except (TypeError, ValueError):
                creator_id = 0
            if creator_id and creator_id != int(user["user_id"]):
                cur = cpq_auth._exec(
                    conn, "SELECT display_name, role_code FROM cpq_wf_user"
                          " WHERE user_id = %s AND status = 'active'", (creator_id,))
                row = cur.fetchone()
                if row and row[1] == next_role:
                    auto_target = {"user_id": creator_id, "display_name": row[0] or "",
                                   "role_code": row[1]}
        _commit(conn)
        result = {
            "card": _fetch_card(conn, session_id),
            "need_handoff": need_handoff,
            "next_step_no": None if done_all else nxt,
            "next_step_name": "" if done_all else dict((s[0], s[1]) for s in QUOTE_STEPS).get(nxt, ""),
            "next_role_code": next_role,
            "next_role_name": ROLES.get(next_role, next_role),
        }
    finally:
        conn.close()
    if auto_target:
        # 用独立连接走标准 send_task（消息、审计、状态流转全套照旧）；失败就退回手动推送
        step_name = dict((s[0], s[1]) for s in QUOTE_STEPS).get(step_no, "")
        try:
            st = send_task(session_id, user, "user",
                           target_user_id=str(auto_target["user_id"]),
                           note=f"第 {step_no} 步「{step_name}」已确认，系统自动推送给项目创建人")
            result["auto_handoff"] = {
                "target_user_id": str(auto_target["user_id"]),
                "target_name": auto_target["display_name"],
                "target_role_name": ROLES.get(auto_target["role_code"], auto_target["role_code"]),
                "task_id": st.get("task_id"),
            }
            conn2 = cpq_auth._connect()   # 卡片状态已被 send_task 更新，重新取一份返回
            try:
                result["card"] = _fetch_card(conn2, session_id)
            finally:
                conn2.close()
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# 任务流转
# ---------------------------------------------------------------------------
def _log(conn, card_id, task_id, actor, action, from_step, to_step, comment):
    cpq_auth._exec(
        conn, "INSERT INTO cpq_wf_task_event (event_id, card_id, task_id, actor_user_id,"
              " action, from_step, to_step, comment, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (_new_id(conn), card_id, task_id, actor, action, from_step, to_step,
         (comment or "")[:500], _ts(_now())))


# ---------------------------------------------------------------------------
# 消息系统：发出/收到任务流时给相关人各记一条站内消息
# ---------------------------------------------------------------------------
MSG_TYPES = {
    "task_sent": "我发出的任务",
    "task_received": "收到新任务",
    "task_claimed": "任务被领取",
}


def _msg(conn, user_id, msg_type, title, body, card_id=None, task_id=None,
         session_id="", step_no=None):
    if not user_id:
        return
    cpq_auth._exec(
        conn, "INSERT INTO cpq_wf_message (message_id, user_id, msg_type, title, body,"
              " card_id, task_id, session_id, step_no, is_read, created_at)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (_new_id(conn), int(user_id), msg_type, (title or "")[:255], (body or "")[:1000],
         card_id, task_id, session_id or None, step_no,
         False, _ts(_now())))


def _recipients(conn, target_type: str, role_code: str, target_user_id, exclude_uid: int) -> list:
    """按派发方式解析出应收到「新任务」消息的用户。"""
    if target_type == "user":
        return [int(target_user_id)] if target_user_id else []
    sql = "SELECT user_id FROM cpq_wf_user WHERE status = 'active' AND user_id <> %s"
    args = [exclude_uid]
    if target_type == "role":
        sql += " AND role_code = %s"
        args.append(role_code)
    cur = cpq_auth._exec(conn, sql, tuple(args))
    return [int(r[0]) for r in cur.fetchall()]


_MSG_COLS = ("message_id", "msg_type", "title", "body", "card_id", "task_id",
             "session_id", "step_no", "is_read", "created_at")


def messages(user: dict, limit: int = 50) -> dict:
    """我的消息列表 + 未读数。"""
    if not user:
        return {"messages": [], "unread": 0}
    uid = int(user["user_id"])
    conn = cpq_auth._connect()
    try:
        cur = cpq_auth._exec(
            conn, f"SELECT {', '.join(_MSG_COLS)} FROM cpq_wf_message WHERE user_id = %s"
                  f" ORDER BY created_at DESC, message_id DESC LIMIT {int(limit)}", (uid,))
        out = []
        for r in cur.fetchall():
            d = dict(zip(_MSG_COLS, r))
            for k in ("message_id", "card_id", "task_id"):
                d[k] = _uid(d[k])
            d["is_read"] = bool(d["is_read"])
            d["created_at"] = _iso(d["created_at"])
            d["type_label"] = MSG_TYPES.get(d["msg_type"], d["msg_type"])
            out.append(d)
        cur = cpq_auth._exec(
            conn, "SELECT COUNT(*) FROM cpq_wf_message WHERE user_id = %s AND is_read = %s",
            (uid, False))
        unread = int((cur.fetchone() or [0])[0])
        return {"messages": out, "unread": unread}
    finally:
        conn.close()


def mark_read(user: dict, message_ids=None) -> int:
    """标记已读：给定 id 列表则只标这些，否则全部标已读。"""
    if not user:
        return 0
    uid = int(user["user_id"])
    read_true, read_false = True, False
    conn = cpq_auth._connect()
    try:
        ids = [int(x) for x in (message_ids or []) if str(x).isdigit()]
        if ids:
            ph = ",".join(["%s"] * len(ids))
            cur = cpq_auth._exec(
                conn, f"UPDATE cpq_wf_message SET is_read = %s WHERE user_id = %s"
                      f" AND message_id IN ({ph})", tuple([read_true, uid] + ids))
        else:
            cur = cpq_auth._exec(
                conn, "UPDATE cpq_wf_message SET is_read = %s WHERE user_id = %s AND is_read = %s",
                (read_true, uid, read_false))
        _commit(conn)
        return int(getattr(cur, "rowcount", 0) or 0)
    finally:
        conn.close()


def send_task(session_id: str, user: dict, target_type: str, target_role_code: str = "",
              target_user_id: str = "", note: str = "", task_kind: str = TASK_KIND_HANDOFF,
              payload: dict = None) -> dict:
    """把卡片作为任务发出：定向角色 / 定向个人 / 公共任务池。

    task_kind=tech_new_product 时是「新增工艺」支线：固定发给工艺经理，卡片**不推进
    步骤、不置待转交** —— 报价还停在第 1 步等新产品，把它标成已转交会让销售以为
    这单已经交出去了。payload 是随任务带给工艺经理的需求正文与文档摘要。
    """
    if not user:
        raise WfError("请先登录")
    if task_kind not in TASK_KINDS:
        raise WfError("任务类型无效")
    # 新增工艺与转交一样支持三种派发方式，也能指定到人；TECH_NEW_ROLE 只是**没指定
    # 目标时**的默认收件角色，不是限制。早先在这里强制成 role/process_mgr，界面上
    # 就没法给这条支线选人了。
    if task_kind == TASK_KIND_TECH_NEW and not target_type:
        target_type, target_role_code = "role", TECH_NEW_ROLE
    if target_type not in TARGET_TYPES:
        raise WfError("派发方式无效")
    if target_type == "role" and target_role_code not in ROLES:
        raise WfError("请选择目标角色")
    # 只有「指派给某人」才用得上 target_user_id；其余方式一律忽略，避免脏值写进库/抛 500
    if target_type == "user":
        try:
            target_user_id = int(str(target_user_id).strip())
        except (TypeError, ValueError):
            raise WfError("请选择目标人员")
    else:
        target_user_id = None
    if target_type != "role":
        target_role_code = None
    conn = cpq_auth._connect()
    try:
        card = _fetch_card(conn, session_id)
        if not card:
            raise WfError("卡片不存在，请先保存报价会话")
        cid = int(card["card_id"])
        from_step = int(card["current_step"] or 1)
        # 同一张卡片同时只保留一个待领取任务，避免重复派发
        cpq_auth._exec(
            conn, "UPDATE cpq_wf_task SET status = 'cancelled' WHERE card_id = %s AND status = 'open'",
            (cid,))
        if target_type == "role":
            whom = f"发给「{ROLES.get(target_role_code)}」"
        elif target_type == "user":
            whom = "指派给指定人员"
        else:
            whom = "发布为公共任务"
        step_name = dict((s[0], s[1]) for s in QUOTE_STEPS).get(from_step, "")
        is_tech_new = task_kind == TASK_KIND_TECH_NEW
        what = ("请到技术工艺新增产品" if is_tech_new
                else f"待办第 {from_step} 步「{step_name}」")
        label = (f"{user.get('role_name')}·{user.get('display_name')} "
                 f"{'发起「新增工艺」' if is_tech_new else '转交'} · {what} · {whom}")
        tid = _new_id(conn)
        cpq_auth._exec(
            conn, "INSERT INTO cpq_wf_task (task_id, card_id, from_user_id, from_step_no,"
                  " target_type, target_role_code, target_user_id, status, source_label, note,"
                  " created_at, task_kind, payload)"
                  " VALUES (%s,%s,%s,%s,%s,%s,%s,'open',%s,%s,%s,%s,%s::jsonb)",
            (tid, cid, int(user["user_id"]), from_step, target_type,
             target_role_code, target_user_id,
             label, (note or "")[:500], _ts(_now()), task_kind,
             json.dumps(payload, ensure_ascii=False) if payload else None))
        # 新增工艺是支线：报价还停在第 1 步等新产品，不能把卡片标成"已转交待领取"，
        # 否则销售在「我的报价」里会以为这单已经交出去、不用管了。
        if not is_tech_new:
            cpq_auth._exec(
                conn, "UPDATE cpq_wf_card SET overall_status = 'handoff_pending', updated_at = %s"
                      " WHERE card_id = %s", (_ts(_now()), cid))
        _log(conn, cid, tid, int(user["user_id"]),
             "tech_new" if is_tech_new else "send", from_step, None, label)

        # 消息系统：自己留一条「我发出的任务」，相关人各收一条「收到新任务」
        title = card.get("title") or "未命名报价"
        note_txt = ("；备注：" + note.strip()) if (note or "").strip() else ""
        _msg(conn, int(user["user_id"]),
             "task_sent", f"你已{'发起新增工艺' if is_tech_new else '转交'}「{title}」",
             f"{whom}，{what}{note_txt}", cid, tid, session_id, from_step)
        for rid in _recipients(conn, target_type, target_role_code,
                               target_user_id, int(user["user_id"])):
            _msg(conn, rid, "task_received", f"收到新任务：「{title}」",
                 f"{user.get('role_name')}·{user.get('display_name')} "
                 + ("发起「新增工艺」：标品匹配不足，请到技术工艺新建产品"
                    if is_tech_new else f"转交，待办第 {from_step} 步「{step_name}」")
                 + note_txt,
                 cid, tid, session_id, from_step)
        _commit(conn)
        return {"task_id": str(tid), "source_label": label,
                "task_kind": task_kind, "task_no": task_no(tid)}
    finally:
        conn.close()


_TASK_SELECT = (
    "t.task_id, t.card_id, t.from_user_id, t.from_step_no, t.target_type, t.target_role_code,"
    " t.target_user_id, t.claimed_by_user_id, t.status, t.source_label, t.note, t.created_at,"
    " t.claimed_at, c.session_id, c.title, c.customer, c.current_step, c.overall_status,"
    " fu.display_name, fu.role_code, t.task_kind, t.payload")
_TASK_KEYS = ("task_id", "card_id", "from_user_id", "from_step_no", "target_type",
              "target_role_code", "target_user_id", "claimed_by_user_id", "status",
              "source_label", "note", "created_at", "claimed_at", "session_id", "title",
              "customer", "current_step", "overall_status", "from_display_name", "from_role_code",
              "task_kind", "payload")


def task_no(task_id) -> str:
    """展示用任务编码。技术工艺那边要显示它，得是个人能念、能搜的短码。

    取雪花 ID 的后 8 位：全局唯一性由 task_id 本身保证，这里只是个门面；
    真正用于查询的仍然是 task_id（同时回给前端）。
    """
    digits = "".join(ch for ch in str(task_id or "") if ch.isdigit())
    return f"TP-{digits[-8:]}" if digits else ""


def _task_row(row) -> dict:
    d = dict(zip(_TASK_KEYS, row))
    for k in ("task_id", "card_id", "from_user_id", "target_user_id", "claimed_by_user_id"):
        d[k] = _uid(d[k])
    for k in ("created_at", "claimed_at"):
        d[k] = _iso(d[k])
    d["from_role_name"] = ROLES.get(d.get("from_role_code"), d.get("from_role_code"))
    d["task_kind"] = d.get("task_kind") or TASK_KIND_HANDOFF
    d["task_kind_label"] = TASK_KIND_LABELS.get(d["task_kind"], d["task_kind"])
    d["task_no"] = task_no(d.get("task_id"))
    # payload 是 jsonb，psycopg 已经解成 dict；老数据是 NULL。
    if not isinstance(d.get("payload"), dict):
        d["payload"] = {}
    # from_step_no = 转交时卡片所处的步骤，也就是接手人要做的那一步（不要再 +1）
    nxt = min(max(int(d.get("from_step_no") or 1), 1), LAST_STEP)
    d["next_step_no"] = nxt
    d["next_step_name"] = dict((s[0], s[1]) for s in QUOTE_STEPS).get(nxt, "")
    d["next_role_code"] = role_of_step(nxt)
    d["next_role_name"] = ROLES.get(d["next_role_code"], d["next_role_code"])
    if d["task_kind"] == TASK_KIND_TECH_NEW:
        # 新增工艺不是"做第 N 步"，而是去技术工艺建新产品；沿用 next_step_* 会让
        # 待办卡片显示成"第 1 步 确认需求配置"，接手人以为是要他去改报价。
        d["next_step_name"] = "技术工艺 · 新增产品"
        d["next_role_code"] = TECH_NEW_ROLE
        d["next_role_name"] = ROLES.get(TECH_NEW_ROLE, TECH_NEW_ROLE)
    return d


def task_detail(task_id: str, user: dict) -> dict:
    """按任务号取一条任务（含 payload）。技术工艺凭任务编码拉需求时用。"""
    if not user:
        raise WfError("请先登录")
    try:
        tid = int(str(task_id).strip())
    except (TypeError, ValueError):
        raise WfError("任务编号无效")
    conn = cpq_auth._connect()
    try:
        cur = cpq_auth._exec(
            conn,
            f"SELECT {_TASK_SELECT} FROM cpq_wf_task t"
            " JOIN cpq_wf_card c ON c.card_id = t.card_id"
            " LEFT JOIN cpq_wf_user fu ON fu.user_id = t.from_user_id"
            " WHERE t.task_id = %s", (tid,))
        row = cur.fetchone()
        if not row:
            raise WfError("任务不存在")
        task = _task_row(row)
        uid = int(user["user_id"])
        role = user.get("role_code") or ""
        # 可见性与 inbox 一致，外加发起人自己（他要能回看自己发出去的任务）
        visible = (task["target_type"] == "public"
                   or (task["target_type"] == "role" and task["target_role_code"] == role)
                   or (task["target_type"] == "user" and str(task["target_user_id"] or "") == str(uid))
                   or str(task["claimed_by_user_id"] or "") == str(uid)
                   or str(task["from_user_id"] or "") == str(uid))
        if not visible:
            raise WfError("你没有查看该任务的权限")
        return task
    finally:
        conn.close()


def inbox(user: dict) -> list:
    """我的任务：待领取（定向我 / 定向我的角色 / 公共）+ 我已领取未完成的。"""
    if not user:
        return []
    uid = int(user["user_id"])
    role = user.get("role_code") or ""
    conn = cpq_auth._connect()
    try:
        cur = cpq_auth._exec(
            conn,
            f"SELECT {_TASK_SELECT} FROM cpq_wf_task t"
            " JOIN cpq_wf_card c ON c.card_id = t.card_id"
            " LEFT JOIN cpq_wf_user fu ON fu.user_id = t.from_user_id"
            " WHERE (t.status = 'open' AND t.from_user_id <> %s AND ("
            "         t.target_type = 'public'"
            "         OR (t.target_type = 'role' AND t.target_role_code = %s)"
            "         OR (t.target_type = 'user' AND t.target_user_id = %s)))"
            "    OR (t.status = 'claimed' AND t.claimed_by_user_id = %s)"
            " ORDER BY t.created_at DESC",
            (uid, role, uid, uid))
        return [_task_row(r) for r in cur.fetchall()]
    finally:
        conn.close()


def claim_task(task_id: str, user: dict) -> dict:
    """领取任务：卡片当前持有人改为领取人。"""
    if not user:
        raise WfError("请先登录")
    uid = int(user["user_id"])
    conn = cpq_auth._connect()
    try:
        cur = cpq_auth._exec(
            conn, "SELECT card_id, status, target_type, target_role_code, target_user_id,"
                  " from_step_no, from_user_id, task_kind FROM cpq_wf_task WHERE task_id = %s",
            (int(task_id),))
        row = cur.fetchone()
        if not row:
            raise WfError("任务不存在")
        cid, status, ttype, trole, tuser, from_step, from_uid, kind = row
        kind = kind or TASK_KIND_HANDOFF
        if status == "claimed":
            raise WfError("该任务已被他人领取")
        if status != "open":
            raise WfError("该任务已关闭")
        # 领取资格与 inbox 的可见性一致
        ok = (ttype == "public"
              or (ttype == "role" and trole == user.get("role_code"))
              or (ttype == "user" and tuser is not None and int(tuser) == uid))
        if not ok:
            raise WfError("你没有该任务的领取权限")
        now = _now()
        cpq_auth._exec(
            conn, "UPDATE cpq_wf_task SET status = 'claimed', claimed_by_user_id = %s,"
                  " claimed_at = %s WHERE task_id = %s", (uid, _ts(now), int(task_id)))
        # 新增工艺是支线：领取它不代表接管这张报价卡片，卡片仍归销售经理。
        # 改了持有人的话，销售在「我的报价」里会发现自己的单子姓了别人的名字。
        if kind != TASK_KIND_TECH_NEW:
            cpq_auth._exec(
                conn, "UPDATE cpq_wf_card SET current_owner = %s, overall_status = 'in_progress',"
                      " updated_at = %s WHERE card_id = %s", (uid, _ts(now), cid))
        _log(conn, cid, int(task_id), uid, "claim", from_step, None,
             f"{user.get('display_name')} 领取"
             + ("「新增工艺」任务" if kind == TASK_KIND_TECH_NEW else "任务"))
        cur = cpq_auth._exec(conn, "SELECT session_id, title FROM cpq_wf_card WHERE card_id = %s", (cid,))
        r = cur.fetchone()
        sid, ctitle = (r[0], r[1]) if r else ("", "")
        # 消息系统：告诉转交人「任务已被领取」，闭环
        if from_uid and int(from_uid) != uid:
            _msg(conn, int(from_uid), "task_claimed", f"「{ctitle or '未命名报价'}」已被领取",
                 f"{user.get('role_name')}·{user.get('display_name')} 领取了你转交的任务，"
                 f"正在处理第 {from_step} 步", cid, int(task_id), sid, from_step)
        _commit(conn)
        # task_kind 决定前端跳哪儿：handoff 进报价工作台，
        # tech_new_product 进技术工艺的任务专属页（/tech-task.html?tech_task=…）。
        return {"session_id": sid, "task_kind": kind, "task_no": task_no(task_id)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 我的报价：卡片列表（含任务标记）
# ---------------------------------------------------------------------------
def my_cards(user: dict) -> list:
    """与我相关的卡片：我创建的、我持有的、我领取过任务的。"""
    if not user:
        return []
    uid = int(user["user_id"])
    conn = cpq_auth._connect()
    try:
        cur = cpq_auth._exec(
            conn,
            f"SELECT {', '.join('c.' + c for c in _CARD_COLS)} FROM cpq_wf_card c"
            " WHERE c.creator_user_id = %s OR c.current_owner = %s"
            "    OR EXISTS (SELECT 1 FROM cpq_wf_task t WHERE t.card_id = c.card_id"
            "               AND t.claimed_by_user_id = %s)"
            " ORDER BY c.updated_at DESC", (uid, uid, uid))
        cards = [_card_row(r) for r in cur.fetchall()]
        for c in cards:
            c["mine_reason"] = ("creator" if c["creator_user_id"] == str(uid)
                                else "owner" if c["current_owner"] == str(uid) else "claimed")
            c["status_label"] = STATUS_LABELS.get(c["overall_status"], c["overall_status"])
        return cards
    finally:
        conn.close()


def card_detail(session_id: str, user: dict) -> dict:
    """卡片 + 步骤状态 + 待领取任务 + 我对当前步骤的操作权限。"""
    card = get_card(session_id)
    if not card:
        return {"card": None, "steps": [], "perms": step_perms()}
    steps = card_steps(card["card_id"])
    cur_step = int(card["current_step"] or 1)
    need_role = role_of_step(cur_step)
    conn = cpq_auth._connect()
    try:
        c = cpq_auth._exec(
            conn, f"SELECT {_TASK_SELECT} FROM cpq_wf_task t"
                  " JOIN cpq_wf_card c ON c.card_id = t.card_id"
                  " LEFT JOIN cpq_wf_user fu ON fu.user_id = t.from_user_id"
                  " WHERE t.card_id = %s AND t.status = 'open'", (int(card["card_id"]),))
        pending = [_task_row(r) for r in c.fetchall()]
    finally:
        conn.close()
    cur_row = next((s for s in steps if s["step_no"] == cur_step), None)
    cur_status = (cur_row or {}).get("status", "pending")
    return {
        "card": card, "steps": steps, "perms": step_perms(),
        "current_step": cur_step,
        "step_status": cur_status,
        # 本步是否已“开工”：pending 表示还没人按下「开始本步骤」，前端应先询问再让智能体动手
        "step_started": cur_status in ("in_progress", "done"),
        "current_step_name": (cur_row or {}).get("step_name", ""),
        "need_role_code": need_role,
        "need_role_name": ROLES.get(need_role, need_role),
        "can_edit": can_do_step(user, cur_step) if user else False,
        "pending_task": pending[0] if pending else None,
        # 本步已完成但还没交出去：前端要挡住流程、催用户推送任务
        "awaiting_handoff": card["overall_status"] == "awaiting_handoff" and not pending,
        "status_label": STATUS_LABELS.get(card["overall_status"], card["overall_status"]),
    }

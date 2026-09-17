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
import uuid
from datetime import datetime, timezone

try:  # psycopg 只在真库路径上必需；受控假库的测试环境也装了它，缺失时降级为不收敛
    from psycopg import errors as _psycopg_errors
except ImportError:  # pragma: no cover —— 只有完全没装驱动时才会走到
    _psycopg_errors = None
_UNIQUE_VIOLATION = getattr(_psycopg_errors, "UniqueViolation", None)

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
# 技术工艺 2.2 做完工艺与参数之后，成本不归工艺经理算 —— 交给财务经理去 2.3 测算。
# 和「新增工艺」一样是支线：报价卡片留在原处等成本，不推进步骤。
TASK_KIND_TECH_COST = "tech_cost"
# 财务测完把结果退回工艺经理复核（工艺有问题要他改工序/用量，不是财务改）。
TASK_KIND_TECH_COST_RETURN = "tech_cost_return"
TASK_KINDS = (TASK_KIND_HANDOFF, TASK_KIND_TECH_NEW,
              TASK_KIND_TECH_COST, TASK_KIND_TECH_COST_RETURN)
TASK_KIND_LABELS = {
    TASK_KIND_HANDOFF: "转交工艺确认",
    TASK_KIND_TECH_NEW: "新增工艺",
    TASK_KIND_TECH_COST: "成本测算",
    TASK_KIND_TECH_COST_RETURN: "成本结果复核",
}
# 支线任务的默认收件角色：这几件事各自只有一个角色能做，让人再选一次只会选错。
TECH_NEW_ROLE = "process_mgr"
TECH_COST_ROLE = "finance_mgr"
TECH_COST_RETURN_ROLE = "process_mgr"
_KIND_DEFAULT_ROLE = {
    TASK_KIND_TECH_NEW: TECH_NEW_ROLE,
    TASK_KIND_TECH_COST: TECH_COST_ROLE,
    TASK_KIND_TECH_COST_RETURN: TECH_COST_RETURN_ROLE,
}
# 这几类都是支线：卡片不推进步骤、也不置"待转交"。
SIDE_TASK_KINDS = (TASK_KIND_TECH_NEW, TASK_KIND_TECH_COST, TASK_KIND_TECH_COST_RETURN)

# 任务状态（cpq_wf_task.status）的中文标签：任务卡片上的状态胶囊直接显示它，
# 终态（已完成 / 已撤回）也要有出口，不能再一律显示成「待领取」。
TASK_STATUS_LABELS = {
    "open": "待领取",
    "claimed": "进行中",
    "completed": "已完成",
    "cancelled": "已撤回",
}

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
        # 任务并存 / 替代的四个新列（幂等，老行读出 NULL）：
        #   supersedes_task_id  替代出来的新任务指向被它替代的旧任务
        #   replaced_by_task_id 被替代的旧任务指向替代它的新任务
        #   cancel_reason / cancelled_at  取消原因与时间
        f"ALTER TABLE {schema}.cpq_wf_task ADD COLUMN IF NOT EXISTS"
        f" supersedes_task_id bigint REFERENCES {schema}.cpq_wf_task(task_id) ON DELETE SET NULL",
        f"ALTER TABLE {schema}.cpq_wf_task ADD COLUMN IF NOT EXISTS"
        f" replaced_by_task_id bigint REFERENCES {schema}.cpq_wf_task(task_id) ON DELETE SET NULL",
        f"ALTER TABLE {schema}.cpq_wf_task ADD COLUMN IF NOT EXISTS cancel_reason varchar(200)",
        f"ALTER TABLE {schema}.cpq_wf_task ADD COLUMN IF NOT EXISTS cancelled_at timestamptz",
        # 回传记录（批次 3）：一次「技术工艺 → 报价」回传 = 一行，
        # 与它的全部副作用（目标任务 / 第 2 步快照 / 来源任务关闭 / 消息 / 审计）
        # 在同一个事务里落库。handoff_key 是业务幂等键，唯一约束由数据库裁决 ——
        # 「先 SELECT 再 INSERT」决定要不要新建是并发下必然出错的老写法。
        f"""CREATE TABLE IF NOT EXISTS {schema}.cpq_wf_handoff (
                handoff_id              bigint PRIMARY KEY,
                handoff_key             varchar(255) NOT NULL,
                handoff_kind            varchar(32)  NOT NULL,
                source_project_id       varchar(64),
                source_task_id          bigint,
                source_result_version   varchar(64),
                target_quote_session_id varchar(32),
                target_card_id          bigint,
                target_task_id          bigint,
                target_task_kind        varchar(24),
                step_no                 int,
                snapshot_sections       jsonb,
                source_task_closed      boolean NOT NULL DEFAULT false,
                source_task_status      varchar(16),
                created_by_user_id      bigint,
                created_at              timestamptz NOT NULL DEFAULT now()
            )""",
        # 业务实例号（批次 6）：跨系统、跨重建认回报价卡片的唯一线索。老库靠
        # ADD COLUMN IF NOT EXISTS 补齐；索引要排在下面的 CREATE INDEX 之前（列先存在）。
        f"ALTER TABLE {schema}.cpq_wf_card ADD COLUMN IF NOT EXISTS business_case_id varchar(64)",
        f"ALTER TABLE {schema}.cpq_wf_handoff ADD COLUMN IF NOT EXISTS"
        f" business_case_id varchar(64)",
        f"CREATE INDEX IF NOT EXISTS idx_wf_card_case"
        f" ON {schema}.cpq_wf_card(business_case_id)",
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_wf_handoff_key"
        f" ON {schema}.cpq_wf_handoff(handoff_key)",
        # 「同一卡片同一任务类型最多一条 open」由数据库裁决：并发下应用层就算判断错
        # 也会被这条部分唯一索引挡住（catch 后收敛成复用，不抛 500）。
        f"CREATE UNIQUE INDEX IF NOT EXISTS uq_wf_task_open_kind"
        f" ON {schema}.cpq_wf_task(card_id, task_kind) WHERE status = 'open'",
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


def new_business_case_id() -> str:
    """业务实例号：``bc_`` + 12 位小写 hex（批次 6）。报价建卡时生成，同一会话不再换号。"""
    return "bc_" + uuid.uuid4().hex[:12]


def _now():
    return datetime.now(timezone.utc)


def _ts(dt):
    return cpq_auth._ts(dt)


def _commit(conn):
    """连接是 autocommit，这里留空以保持调用方写法统一。"""
    return None


def tx_connect():
    """打开一条**非 autocommit** 连接：一个业务命令 = 一条连接的一个事务。

    为什么不能用 ``cpq_auth._connect()``：它是 autocommit=True，psycopg 在 autocommit
    连接上不会为 ``with conn.transaction():`` 发 BEGIN，所以"多写一步"就等于"多提交
    一次"，中途失败事后无法整体回滚（回传今天就是被这件事拆成半完成状态的）。

    连接参数 / search_path 与 ``cpq_auth._connect()`` 完全一致，只有 autocommit 不同；
    调用方负责 commit / rollback 与 close。
    """
    import psycopg

    conn = psycopg.connect(
        host=cpq_auth.cpq_db.PG_HOST, port=cpq_auth.cpq_db.PG_PORT,
        user=cpq_auth.cpq_db.PG_USER, password=cpq_auth.cpq_db.PG_PASSWORD,
        dbname=cpq_auth.cpq_db.PG_DATABASE,
        connect_timeout=cpq_auth.cpq_db.PG_CONNECT_TIMEOUT, autocommit=False,
    )
    with conn.cursor() as cur:
        cur.execute("SELECT set_config('search_path', %s, false)",
                    (f"{cpq_auth.WF_SCHEMA}, public",))
    return conn


def _iso(v):
    return v.isoformat() if hasattr(v, "isoformat") else (v or None)


def _uid(v):
    return str(v) if v is not None else None


# ---------------------------------------------------------------------------
# 回传记录（cpq_wf_handoff）
#
# 一行 = 一次「技术工艺 → 报价」回传。它同时是幂等键的落点：INSERT ... ON CONFLICT
# (handoff_key) DO NOTHING 先拿"裁决权"，重读发现不是自己插的那条就整段回滚、复用
# 对方的结果。并发下唯一约束由数据库保证 —— 不是应用层先 SELECT 再决定。
# ---------------------------------------------------------------------------
_HANDOFF_COLS = ("handoff_id", "handoff_key", "handoff_kind", "source_project_id",
                 "source_task_id", "source_result_version", "target_quote_session_id",
                 "target_card_id", "target_task_id", "target_task_kind", "step_no",
                 "snapshot_sections", "source_task_closed", "source_task_status",
                 "created_by_user_id", "created_at", "business_case_id")


def _handoff_row(row) -> dict:
    if not row:
        return None
    d = dict(zip(_HANDOFF_COLS, row))
    for k in ("handoff_id", "source_task_id", "target_card_id", "target_task_id",
              "created_by_user_id"):
        d[k] = _uid(d[k])
    d["created_at"] = _iso(d["created_at"])
    d["business_case_id"] = str(d.get("business_case_id") or "")
    return d


def insert_handoff_placeholder(conn, handoff_id, handoff_key: str, handoff_kind: str,
                              source_project_id: str = "", source_task_id: str = "",
                              source_result_version: str = "",
                              target_quote_session_id: str = "",
                              created_by_user_id=None,
                              business_case_id: str = "") -> bool:
    """插占位行拿幂等键的裁决权。返回 True = 本次是第一个；False = 已经有人交过。

    ``DO NOTHING`` 之后不 RETURNING（同一把键可能已被别的连接插进去），调用方在
    返回 False 时重读 ``find_handoff`` 复用对方那一条。
    """
    cur = cpq_auth._exec(
        conn, "INSERT INTO cpq_wf_handoff (handoff_id, handoff_key, handoff_kind,"
              " source_project_id, source_task_id, source_result_version,"
              " target_quote_session_id, source_task_closed, created_by_user_id, created_at,"
              " business_case_id)"
              " VALUES (%s,%s,%s,%s,%s,%s,%s,false,%s,%s,%s)"
              " ON CONFLICT (handoff_key) DO NOTHING",
        (handoff_id, str(handoff_key or "")[:255], handoff_kind or "",
         source_project_id or None, _int_or_none(source_task_id),
         source_result_version or None, target_quote_session_id or None,
         created_by_user_id, _ts(_now()), str(business_case_id or "") or None))
    return int(getattr(cur, "rowcount", 0) or 0) > 0


def find_handoff(conn, handoff_key: str):
    """按业务幂等键取回传记录（dict，取不到 None）。"""
    cur = cpq_auth._exec(
        conn, f"SELECT {', '.join(_HANDOFF_COLS)} FROM cpq_wf_handoff WHERE handoff_key = %s",
        (str(handoff_key or ""),))
    return _handoff_row(cur.fetchone())


def update_handoff(conn, handoff_id, **fields) -> None:
    """把回传记录的落点补全（目标任务、快照栏目、来源任务关闭结果）。"""
    allowed = ("target_quote_session_id", "target_card_id", "target_task_id",
               "target_task_kind", "step_no", "snapshot_sections",
               "source_task_closed", "source_task_status", "business_case_id")
    sets, args = [], []
    for col in allowed:
        if col not in fields:
            continue
        value = fields[col]
        if col == "snapshot_sections":
            sets.append(f"{col} = %s::jsonb")
            args.append(json.dumps(value or {}, ensure_ascii=False))
        else:
            sets.append(f"{col} = %s")
            args.append(value)
    if not sets:
        return
    args.append(int(handoff_id))
    cpq_auth._exec(conn, f"UPDATE cpq_wf_handoff SET {', '.join(sets)}"
                         " WHERE handoff_id = %s", tuple(args))


def _int_or_none(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


UNCLOSED_TASK_STATUSES = ("cancelled", "expired")


def close_source_task(conn, task_id, user: dict, *, comment: str = "") -> dict:
    """在**当前事务**里关闭来源的 claimed 待办，并把结果如实回报。

    返回 ``{task_id, status, closed, already, skipped}``：
      · 没带 task_id / 查不到 → ``skipped``，不算失败（技术工艺独立发起的项目没有来源）；
      · 已是 ``completed`` → ``already=True``，不重写状态、不重复写审计；
      · ``open``（没人领）→ 置完成并审计（技术侧确实做完了这件事）；
      · ``claimed`` 且领取人不是调用者（也不是管理员）→ 抛 ``WfError``，由调用方
        整段回滚 —— 别人的待办不能被顺手关掉。
    """
    tid = _int_or_none(task_id)
    if tid is None:
        return {"task_id": "", "status": "", "closed": False, "already": False,
                "skipped": "missing_task_id"}
    cur = cpq_auth._exec(
        conn, "SELECT card_id, status, claimed_by_user_id FROM cpq_wf_task WHERE task_id = %s",
        (tid,))
    row = cur.fetchone()
    if not row:
        return {"task_id": str(tid), "status": "", "closed": False, "already": False,
                "skipped": "not_found"}
    cid, status, claimed_by = int(row[0]), str(row[1] or ""), row[2]
    uid = int(user["user_id"]) if user else None
    is_admin = bool(user) and str(user.get("role_code") or "") in ADMIN_ROLES
    if status == "completed":
        return {"task_id": str(tid), "status": status, "closed": False, "already": True,
                "skipped": ""}
    if status == "claimed" and not is_admin and (claimed_by is None or int(claimed_by) != uid):
        holder = "其他同事"
        cur = cpq_auth._exec(
            conn, "SELECT display_name FROM cpq_wf_user WHERE user_id = %s", (claimed_by,))
        hrow = cur.fetchone()
        if hrow and hrow[0]:
            holder = hrow[0]
        raise WfError(f"该任务已被 {holder} 领取，不能由你关闭；请等他完成后再回传")
    if status not in ("open", "claimed"):
        # 已撤销 / 过期这类终态：不再回开，也不让整次回传失败。
        return {"task_id": str(tid), "status": status, "closed": False, "already": False,
                "skipped": status or "closed"}
    cpq_auth._exec(
        conn, "UPDATE cpq_wf_task SET status = 'completed', completed_at = %s"
              " WHERE task_id = %s AND status IN ('open', 'claimed')", (_ts(_now()), tid))
    _log(conn, cid, tid, uid, "complete", None, None,
         comment or "技术工艺回传报价：来源待办已随本次交接完成")
    return {"task_id": str(tid), "status": "completed", "closed": True, "already": False,
            "skipped": ""}


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
              "created_at", "updated_at", "business_case_id")


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
              project_name: str = "", current_step: int = None, conn=None,
              business_case_id: str = "") -> dict:
    """新建或更新卡片（报价会话每次保存/推进时由前端调用）。
    创建人 = 首次同步的登录用户；current_owner 首次同步时也归他。

    conn：给了就并进调用方的事务（回传命令新建报价会话时用），不自己 commit / close。
    business_case_id：业务实例号（批次 6）。留空时建卡自动生成；同一会话再次同步不换号。"""
    session_id = (session_id or "").strip()
    if not session_id:
        raise WfError("缺少会话 ID")
    uid = int(user["user_id"]) if user else None
    own = conn is None
    if own:
        conn = cpq_auth._connect()
    try:
        card = _fetch_card(conn, session_id)
        now = _now()
        if not card:
            cid = _new_id(conn)
            case_id = str(business_case_id or "").strip() or new_business_case_id()
            cpq_auth._exec(
                conn, "INSERT INTO cpq_wf_card (card_id, session_id, assistant_type, title,"
                      " customer, project_name, current_step, overall_status, creator_user_id,"
                      " current_owner, created_at, updated_at, business_case_id)"
                      " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (cid, session_id, ASSISTANT, title or None, customer or None, project_name or None,
                 int(current_step or 1), "draft", uid, uid, _ts(now), _ts(now), case_id))
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
        # 老卡片没有实例号时，安全回填（只在卡片为空时写；同值重写无副作用）。
        wanted_case = str(business_case_id or "").strip()
        if wanted_case and not str(card.get("business_case_id") or "").strip():
            sets.append("business_case_id = %s")
            args.append(wanted_case)
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
        if own:
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


def step_snapshot(session_id: str, step_no: int, conn=None):
    """取某一步确认时存下的表单快照（card_step.data_snapshot）。

    交接过来的卡片，接手人本地 DOM 里没有前面各步的数据——尤其第 1 步的产品信息/技术参数
    是前端直接填的、不在智能体事件流里，只能从这里恢复。

    conn：给了就在调用方的事务连接上读（回传命令要先合并老快照再写回），不自开自关。"""
    own = conn is None
    if own:
        conn = cpq_auth._connect()
    try:
        cur = cpq_auth._exec(
            conn, "SELECT s.data_snapshot FROM cpq_wf_card_step s"
                  " JOIN cpq_wf_card c ON c.card_id = s.card_id"
                  " WHERE c.session_id = %s AND s.step_no = %s",
            (session_id, int(step_no or 0)))
        row = cur.fetchone()
    finally:
        if own:
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


def complete_step(session_id: str, step_no: int, user: dict, snapshot: str = "",
                  on_behalf_of: str = "", conn=None) -> dict:
    """把某一步标记为完成（角色不符会被拒绝）。

    返回 {card, need_handoff, next_step_no, next_step_name, next_role_code/name}：
    下一步若归属别的角色，卡片置为 awaiting_handoff（待转交），前端据此强制走推送任务流。

    conn：给了就并进调用方的事务（回传命令用），且**不做自动推送** —— 派发由调用方
    在同一事务里完成。

    on_behalf_of：**代技术侧完成**。第 2 步「工艺确认」归工艺经理，但成本测算拆给
    财务之后，这一步的收尾动作（技术工艺 2.3「发送至报价」）是财务经理点的 ——
    他做完成本才轮到报价定价。他不是在报价界面上冒名点"完成第 2 步"，而是走
    cpq_tech_bridge 那条集成通道，那里已经按自己的规则鉴过权（finance_mgr /
    process_mgr）。所以这里只认一件事：代办的角色必须**正是这一步的归属角色**，
    否则一律拒绝 —— 它是给集成通道用的定向豁免，不是一把万能钥匙。
    """
    if not user:
        raise WfError("请先登录")
    step_no = int(step_no or 0)
    if not (1 <= step_no <= LAST_STEP):
        raise WfError("步骤号无效")
    stand_in = str(on_behalf_of or "").strip()
    if stand_in and stand_in != role_of_step(step_no):
        raise WfError(f"不能以「{ROLES.get(stand_in, stand_in)}」的名义完成第 {step_no} 步")
    stand_in_used = bool(stand_in) and not can_do_step(user, step_no)
    if not can_do_step(user, step_no) and not stand_in:
        need = ROLES.get(role_of_step(step_no), "指定角色")
        raise WfError(f"第 {step_no} 步需要「{need}」完成，你当前是「{user.get('role_name')}」")
    own = conn is None
    if own:
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
        # 代办要写进留痕：卡片上这一步显示"已完成"，但完成的人不是它的归属角色，
        # 事后追溯必须看得出是谁、以什么名义做的。
        _log(conn, cid, None, int(user["user_id"]), "step_done", step_no, nxt,
             f"{user.get('display_name')} 完成第 {step_no} 步"
             + (f"（{user.get('role_name')} 代「{ROLES.get(stand_in, stand_in)}」，"
                f"技术工艺 2.3 成本测算后回传）" if stand_in_used else ""))
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
        if own:
            conn.close()
    # 外部事务（回传命令）里派发由调用方在同一事务内完成 —— 这里的自动推送会另开连接，
    # 等于把「一次业务动作」拆成两次提交，正是本批要消灭的半完成状态来源。
    if auto_target and own:
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


# 有管理权的角色：库里没有这类角色时这张表为空，校验自然只认领取人本人。
ADMIN_ROLES = ("admin", "sys_admin")


def advance_step_no(card, step_no: int) -> int:
    """报价步骤只能单调前进：目标步骤不得小于卡片当前步骤。

    报告回传可能发生在成本阶段已经把报价推进到第 3 步之后，这时绝不能把
    current_step 写回 TECH_CONFIRM_STEP —— 步骤倒退会让销售看到一张已经回退的卡片。
    """
    return max(int((card or {}).get("current_step") or 1), int(step_no or 1))


def _snapshot_dict(v) -> dict:
    """data_snapshot 既可能是 jsonb（psycopg 已解成 dict），也可能是 JSON 字符串
    （受控假库 / 老库直读），两种都要认，否则一次「合并」会把老键整份丢掉。"""
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, str) and v.strip():
        try:
            got = json.loads(v)
        except ValueError:
            return {}
        return got if isinstance(got, dict) else {}
    return {}


def merge_step_snapshot(session_id: str, step_no: int, snapshot: dict, conn=None) -> dict:
    """把新的技术结果**合并**进某一步已有的 data_snapshot，不改任何步骤状态。

    conn：给了就并进调用方的事务（回传命令用），不自己 commit / close。

    已推进过报价卡片时（成本阶段已经回传销售），报告回传只能补充快照，
    不能重新完成第 2 步、也不能覆盖第 3 步之后已经产生的结果。
    """
    own = conn is None
    if own:
        conn = cpq_auth._connect()
    try:
        card = _fetch_card(conn, session_id)
        if not card:
            raise WfError("卡片不存在，请先保存报价会话")
        cid = int(card["card_id"])
        cur = cpq_auth._exec(
            conn, "SELECT data_snapshot FROM cpq_wf_card_step WHERE card_id = %s AND step_no = %s",
            (cid, int(step_no)))
        row = cur.fetchone()
        merged = _snapshot_dict(row[0]) if row else {}
        for key, value in (snapshot or {}).items():
            merged[key] = value
        cpq_auth._exec(
            conn, "UPDATE cpq_wf_card_step SET data_snapshot = %s::jsonb"
                  " WHERE card_id = %s AND step_no = %s",
            (json.dumps(merged, ensure_ascii=False), cid, int(step_no)))
        _commit(conn)
        return merged
    finally:
        if own:
            conn.close()


def complete_claimed_task(task_id, user: dict, *, card_session_id: str = "",
                          comment: str = "", conn=None) -> dict:
    """把当前用户已领取的任务置为 completed（幂等）。

    技术工艺完成正式去向（提交工艺经理确认 / 回传销售经理继续报价）后调用：原 claimed
    的财务待办必须随之关闭，否则它会一直挂在领取人的「我的任务」里。

    校验四件事：任务存在、当前用户是领取人（或有管理角色）、任务确实属于该卡片、
    状态只能是 claimed（已经是 completed 时直接返回 already，不重复写审计）。
    重复调用保持幂等：不会重复记事件、也不会把 completed 变回别的状态。
    """
    if not user:
        raise WfError("请先登录")
    try:
        tid = int(str(task_id).strip())
    except (TypeError, ValueError):
        raise WfError("任务编号无效")
    uid = int(user["user_id"])
    own = conn is None
    if own:
        conn = cpq_auth._connect()
    try:
        cur = cpq_auth._exec(
            conn, "SELECT card_id, status, claimed_by_user_id FROM cpq_wf_task WHERE task_id = %s",
            (tid,))
        row = cur.fetchone()
        if not row:
            raise WfError("任务不存在")
        cid, status, claimed_by = int(row[0]), str(row[1] or ""), row[2]
        card = _fetch_card_by_id(conn, cid)
        if not card:
            raise WfError("任务所属卡片不存在")
        if card_session_id and str(card.get("session_id") or "") != str(card_session_id):
            # 任务必须属于当前技术项目/报价卡片：避免拿别的卡片的 task_id 误关。
            raise WfError("该任务不属于当前项目")
        is_claimer = claimed_by is not None and int(claimed_by) == uid
        if not is_claimer and (user.get("role_code") or "") not in ADMIN_ROLES:
            raise WfError("只有领取人本人（或有管理权限的人）能完成该任务")
        if status == "completed":
            return {"task_id": str(tid), "task_no": task_no(tid), "already": True,
                    "status": status}
        if status != "claimed":
            raise WfError("该任务尚未被领取，无需完成")
        now = _now()
        cpq_auth._exec(
            conn, "UPDATE cpq_wf_task SET status = 'completed', completed_at = %s"
                  " WHERE task_id = %s AND status = 'claimed'", (_ts(now), tid))
        _log(conn, cid, tid, uid, "complete", None, None,
             comment or "成本结果已提交下一步，来源待办完成")
        _commit(conn)
        return {"task_id": str(tid), "task_no": task_no(tid), "already": False,
                "status": "completed", "session_id": card.get("session_id")}
    finally:
        if own:
            conn.close()


def _fetch_card_by_id(conn, card_id: int):
    cur = cpq_auth._exec(
        conn, f"SELECT {', '.join(_CARD_COLS)} FROM cpq_wf_card WHERE card_id = %s", (int(card_id),))
    return _card_row(cur.fetchone())


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
    # 被同类新任务替代：发给旧任务的原收件人集合 + 旧任务发起人（去重）
    "task_superseded": "被新任务替代",
    # 未带替代任务的取消（本批没有入口触发，字典与图标先齐备）
    "task_cancelled": "已撤回",
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


# ---------------------------------------------------------------------------
# 任务并存 / 复用 / 替代
# ---------------------------------------------------------------------------
# 同类任务的「活跃行」查询列（索引写死在下面的 helper 里）：
#   0 task_id  1 status  2 target_type  3 target_role_code  4 target_user_id
#   5 from_user_id  6 note  7 payload  8 source_label  9 claimed_by_user_id
#   10 from_step_no
_ACTIVE_TASK_COLS = ("task_id, status, target_type, target_role_code, target_user_id,"
                     " from_user_id, note, payload, source_label, claimed_by_user_id,"
                     " from_step_no")


def _norm_target_user(v):
    """目标人统一成 int（库里是 bigint，调用方传字符串），空值统一成 None。"""
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return str(v)


def business_version(payload) -> str:
    """payload 里的业务版本：result_version > version > handoff_key，都没有 = 空串。"""
    d = payload if isinstance(payload, dict) else {}
    return str(d.get("result_version") or d.get("version") or d.get("handoff_key") or "")


def dispatch_signature(target_type, target_role_code, target_user_id, note, payload) -> tuple:
    """派发签名：这五项逐项相等就是「同一件事」。"""
    return (target_type or "", target_role_code or "",
            _norm_target_user(target_user_id), (note or "").strip(),
            business_version(payload))


def _payload_of(v) -> dict:
    """payload 正常是 jsonb -> dict；受控假库/老数据可能是字符串，统一解成 dict。"""
    if isinstance(v, dict):
        return v
    if isinstance(v, str) and v.strip():
        try:
            got = json.loads(v)
        except ValueError:
            return {}
        return got if isinstance(got, dict) else {}
    return {}


def task_signature(row) -> tuple:
    """一条已入库同类任务的派发签名（列位见 _ACTIVE_TASK_COLS）。"""
    return dispatch_signature(row[2], row[3], row[4], row[6], _payload_of(row[7]))


def _reuse_result(row, task_kind) -> dict:
    """复用已有任务：不写 task / event / message，原样把既有任务回给调用方。"""
    tid = row[0]
    return {"task_id": str(tid), "source_label": row[8], "task_kind": task_kind,
            "task_no": task_no(tid), "reused": True, "supersedes_task_id": None}


def _cancel_task_for_supersede(conn, old_row, *, reason: str = "被新任务替代"):
    """替代第一步：把同类旧任务置为已取消，腾出 (card_id, task_kind) 的 open 槽位。

    这里只改状态 / 原因 / 时间，**不写** replaced_by_task_id：那个指针有外键，指向的
    新任务此刻还没入库；真库的外键是立即校验的，先写指针会直接抛外键错误。
    """
    cpq_auth._exec(
        conn, "UPDATE cpq_wf_task SET status = 'cancelled', cancel_reason = %s,"
              " cancelled_at = %s WHERE task_id = %s AND status = 'open'",
        (reason, _ts(_now()), int(old_row[0])))


def _link_superseded_task(conn, old_row, new_task_id, card_id, actor_uid, session_id, from_step):
    """替代第二步（新任务已入库之后）：补 replaced_by_task_id + 1 条 cancel 审计 + 通知旧受众。

    旧受众 = 旧任务原收件人（按旧 target 解析）∪ 旧任务发起人 ∪ 旧任务领取人，去重。
    """
    old_id = int(old_row[0])
    reason = "被新任务替代"
    cpq_auth._exec(
        conn, "UPDATE cpq_wf_task SET replaced_by_task_id = %s WHERE task_id = %s",
        (new_task_id, old_id))
    old_no, new_no = task_no(old_id), task_no(new_task_id)
    audience = set()
    for rid in _recipients(conn, old_row[2], old_row[3], old_row[4], 0):
        if rid:
            audience.add(int(rid))
    for extra in (old_row[5], old_row[9]):
        if extra:
            audience.add(int(extra))
    for rid in sorted(audience):
        _msg(conn, rid, "task_superseded", f"任务 {old_no} 被新任务替代",
             f"你经手过的任务 {old_no} 已被新任务 {new_no} 替代（原因：{reason}），"
             f"请以新任务为准。", card_id, old_id, session_id, from_step)
    _log(conn, card_id, old_id, actor_uid, "cancel", from_step, None,
         f"被新任务 {new_no} 替代：{reason}")


def send_task(session_id: str, user: dict, target_type: str, target_role_code: str = "",
              target_user_id: str = "", note: str = "", task_kind: str = TASK_KIND_HANDOFF,
              payload: dict = None, conn=None) -> dict:
    """把卡片作为任务发出：定向角色 / 定向个人 / 公共任务池。

    conn：给了就并进调用方的事务（回传命令用），不自己 commit / close。

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
    if task_kind in _KIND_DEFAULT_ROLE and not target_type:
        target_type, target_role_code = "role", _KIND_DEFAULT_ROLE[task_kind]
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
    own = conn is None
    if own:
        conn = cpq_auth._connect()
    try:
        card = _fetch_card(conn, session_id)
        if not card:
            raise WfError("卡片不存在，请先保存报价会话")
        cid = int(card["card_id"])
        from_step = int(card["current_step"] or 1)
        kind_label = TASK_KIND_LABELS.get(task_kind, task_kind)
        is_side = task_kind in SIDE_TASK_KINDS
        # 同一张卡片同一 task_kind 这一格最多一条 open，不同 task_kind 永远并存。
        # 先看同类有没有正在进行的任务：同签名 → 复用；签名不同 → 替代（open）/ 拒绝（claimed）。
        cur = cpq_auth._exec(
            conn, f"SELECT {_ACTIVE_TASK_COLS} FROM cpq_wf_task"
                  " WHERE card_id = %s AND task_kind = %s AND status IN ('open', 'claimed')"
                  " ORDER BY created_at DESC, task_id DESC", (cid, task_kind))
        actives = cur.fetchall()
        active = next((r for r in actives if r[1] == "open"),
                      actives[0] if actives else None)
        sig = dispatch_signature(target_type, target_role_code, target_user_id, note, payload)
        superseded_id = None
        if active is not None:
            if task_signature(active) == sig:
                # 复用：没有改变任何状态，不写 task / event / message
                return _reuse_result(active, task_kind)
            if active[1] == "claimed":
                holder = "其他同事"
                cur = cpq_auth._exec(
                    conn, "SELECT display_name FROM cpq_wf_user WHERE user_id = %s",
                    (active[9],))
                holder_row = cur.fetchone()
                if holder_row and holder_row[0]:
                    holder = holder_row[0]
                raise WfError(f"该卡片的「{kind_label}」任务已被 {holder} 领取，"
                              f"请等他完成后再重新发起")
            superseded_id = int(active[0])
        if target_type == "role":
            whom = f"发给「{ROLES.get(target_role_code)}」"
        elif target_type == "user":
            whom = "指派给指定人员"
        else:
            whom = "发布为公共任务"
        step_name = dict((s[0], s[1]) for s in QUOTE_STEPS).get(from_step, "")
        what = {
            TASK_KIND_TECH_NEW: "请到技术工艺新增产品",
            TASK_KIND_TECH_COST: "请到技术工艺 2.3 做成本测算",
            TASK_KIND_TECH_COST_RETURN: "成本已测算，请复核工艺与用量",
        }.get(task_kind, f"待办第 {from_step} 步「{step_name}」")
        label = (f"{user.get('role_name')}·{user.get('display_name')} "
                 f"{f'发起「{kind_label}」' if is_side else '转交'} · {what} · {whom}")
        now = _now()
        tid = _new_id(conn)
        if superseded_id is not None:
            # 先把旧任务置为已取消：腾出 (card_id, task_kind) 的 open 槽位，新任务才插得进去。
            # 替代指针与通知要等新任务入库之后再写（见下面 _link_superseded_task）。
            _cancel_task_for_supersede(conn, active)
        try:
            cpq_auth._exec(
                conn, "INSERT INTO cpq_wf_task (task_id, card_id, from_user_id, from_step_no,"
                      " target_type, target_role_code, target_user_id, status, source_label, note,"
                      " created_at, task_kind, payload, supersedes_task_id)"
                      " VALUES (%s,%s,%s,%s,%s,%s,%s,'open',%s,%s,%s,%s,%s::jsonb,%s)",
                (tid, cid, int(user["user_id"]), from_step, target_type,
                 target_role_code, target_user_id,
                 label, (note or "")[:500], _ts(now), task_kind,
                 json.dumps(payload, ensure_ascii=False) if payload else None,
                 superseded_id))
        except Exception as exc:   # noqa: BLE001 —— 只把唯一冲突收敛成复用，其它照抛
            if _UNIQUE_VIOLATION is None or not isinstance(exc, _UNIQUE_VIOLATION):
                raise
            # 并发同签名发起：数据库的部分唯一索引挡住第二条 → 收敛成「复用」，不抛 500
            cur = cpq_auth._exec(
                conn, f"SELECT {_ACTIVE_TASK_COLS} FROM cpq_wf_task"
                      " WHERE card_id = %s AND task_kind = %s AND status = 'open'",
                (cid, task_kind))
            row = cur.fetchone()
            if row is None:
                raise
            return _reuse_result(row, task_kind)
        if superseded_id is not None:
            # 新任务已经入库，现在才写「被谁替代」的指针：replaced_by_task_id 有外键，
            # 指向的任务必须已经存在（同一条连接里未提交的行也算存在）。
            # 先写指针再 INSERT 会在真库上直接抛外键错误（受控假库不校验外键，抓不到）。
            _link_superseded_task(conn, active, tid, cid, int(user["user_id"]),
                                  session_id, from_step)
        # 支线任务（新增工艺、成本测算、成本复核）都不夺卡片持有人：报价还停在原来
        # 那一步等结果，标成"已转交待领取"会让销售以为这单已经交出去、不用管了。
        if not is_side:
            cpq_auth._exec(
                conn, "UPDATE cpq_wf_card SET overall_status = 'handoff_pending', updated_at = %s"
                      " WHERE card_id = %s", (_ts(_now()), cid))
        _log(conn, cid, tid, int(user["user_id"]),
             (task_kind if is_side else "send"), from_step, None, label)

        # 消息系统：自己留一条「我发出的任务」，相关人各收一条「收到新任务」
        title = card.get("title") or "未命名报价"
        note_txt = ("；备注：" + note.strip()) if (note or "").strip() else ""
        _msg(conn, int(user["user_id"]),
             "task_sent", f"你已{f'发起{kind_label}' if is_side else '转交'}「{title}」",
             f"{whom}，{what}{note_txt}", cid, tid, session_id, from_step)
        for rid in _recipients(conn, target_type, target_role_code,
                               target_user_id, int(user["user_id"])):
            _msg(conn, rid, "task_received", f"收到新任务：「{title}」",
                 f"{user.get('role_name')}·{user.get('display_name')} "
                 + (f"发起「{kind_label}」：{what}"
                    if is_side else f"转交，待办第 {from_step} 步「{step_name}」")
                 + note_txt,
                 cid, tid, session_id, from_step)
        _commit(conn)
        return {"task_id": str(tid), "source_label": label,
                "task_kind": task_kind, "task_no": task_no(tid),
                "reused": False,
                "supersedes_task_id": str(superseded_id) if superseded_id else None}
    finally:
        if own:
            conn.close()


_TASK_SELECT = (
    "t.task_id, t.card_id, t.from_user_id, t.from_step_no, t.target_type, t.target_role_code,"
    " t.target_user_id, t.claimed_by_user_id, t.status, t.source_label, t.note, t.created_at,"
    " t.claimed_at, c.session_id, c.title, c.customer, c.current_step, c.overall_status,"
    " fu.display_name, fu.role_code, t.task_kind, t.payload,"
    " t.supersedes_task_id, t.replaced_by_task_id, t.cancel_reason, t.cancelled_at")
_TASK_KEYS = ("task_id", "card_id", "from_user_id", "from_step_no", "target_type",
              "target_role_code", "target_user_id", "claimed_by_user_id", "status",
              "source_label", "note", "created_at", "claimed_at", "session_id", "title",
              "customer", "current_step", "overall_status", "from_display_name", "from_role_code",
              "task_kind", "payload",
              "supersedes_task_id", "replaced_by_task_id", "cancel_reason", "cancelled_at")


def task_no(task_id) -> str:
    """展示用任务编码。技术工艺那边要显示它，得是个人能念、能搜的短码。

    取雪花 ID 的后 8 位：全局唯一性由 task_id 本身保证，这里只是个门面；
    真正用于查询的仍然是 task_id（同时回给前端）。
    """
    digits = "".join(ch for ch in str(task_id or "") if ch.isdigit())
    return f"TP-{digits[-8:]}" if digits else ""


def _task_row(row) -> dict:
    d = dict(zip(_TASK_KEYS, row))
    for k in ("task_id", "card_id", "from_user_id", "target_user_id", "claimed_by_user_id",
              "supersedes_task_id", "replaced_by_task_id"):
        d[k] = _uid(d[k])
    for k in ("created_at", "claimed_at", "cancelled_at"):
        d[k] = _iso(d[k])
    # 任务卡片的状态胶囊显示它：终态（已完成 / 已撤回）也要有明确出口
    d["status_label"] = TASK_STATUS_LABELS.get(d.get("status"), d.get("status"))
    # 替代它的新任务展示编码：task_no 只按 id 现算，不需要二次查库
    d["replaced_by_task_no"] = task_no(d["replaced_by_task_id"]) if d.get("replaced_by_task_id") else None
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
    if d["task_kind"] in SIDE_TASK_KINDS:
        # 支线任务不是"做第 N 步"，而是去技术工艺做一件事；沿用 next_step_* 会让
        # 待办卡片显示成"第 1 步 确认需求配置"，接手人以为是要他去改报价。
        d["next_step_name"] = {
            TASK_KIND_TECH_NEW: "技术工艺 · 新增产品",
            TASK_KIND_TECH_COST: "技术工艺 2.3 · 成本测算",
            TASK_KIND_TECH_COST_RETURN: "技术工艺 · 复核工艺与用量",
        }[d["task_kind"]]
        d["next_role_code"] = _KIND_DEFAULT_ROLE[d["task_kind"]]
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
            # 终态出口：我自己发起的、被新任务替代掉的那一条，留在我的列表里。
            # 只收 replaced_by_task_id 非空的新机制记录 —— 线上历史的静默取消
            # （replaced_by_task_id IS NULL）不回填、不进任何人的列表。
            "    OR (t.status = 'cancelled' AND t.from_user_id = %s"
            "        AND t.replaced_by_task_id IS NOT NULL)"
            " ORDER BY t.created_at DESC",
            (uid, role, uid, uid, uid))
        return [_task_row(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _claim_unavailable(conn, tid: int, uid: int) -> dict:
    """原子 UPDATE 没命中时的三个出口：本人重复领取 / 已被他人领取 / 已关闭。

    本人重复领取是幂等成功：不写审计、不发消息、不动卡片，只把既有会话回给前端。
    """
    cur = cpq_auth._exec(
        conn, "SELECT status, claimed_by_user_id, task_kind, card_id"
              " FROM cpq_wf_task WHERE task_id = %s", (tid,))
    again = cur.fetchone()
    if not again:
        raise WfError("任务不存在")
    st, claimed_by, kind, cid = again
    kind = kind or TASK_KIND_HANDOFF
    if st == "claimed" and claimed_by is not None and int(claimed_by) == uid:
        sid = ""
        cur = cpq_auth._exec(
            conn, "SELECT session_id FROM cpq_wf_card WHERE card_id = %s", (cid,))
        r = cur.fetchone()
        if r:
            sid = r[0] or ""
        return {"session_id": sid, "task_kind": kind, "task_no": task_no(tid),
                "already": True}
    if st == "claimed":
        raise WfError("该任务已被他人领取")
    raise WfError("该任务已关闭")


def claim_task(task_id: str, user: dict) -> dict:
    """领取任务：单条带 status='open' 条件的原子 UPDATE，主线任务才改卡片归属。

    并发下由数据库裁决：命中才走后续流程；没命中再分辨「本人重复领取 / 已被他人领取 /
    已关闭」。失败方零副作用（不动卡片、不写审计、不发消息）。领取资格判定仍在
    UPDATE 之前，口径与 inbox 一致。
    """
    if not user:
        raise WfError("请先登录")
    uid = int(user["user_id"])
    try:
        tid = int(str(task_id).strip())
    except (TypeError, ValueError):
        raise WfError("任务编号无效")
    conn = cpq_auth._connect()
    try:
        cur = cpq_auth._exec(
            conn, "SELECT card_id, status, target_type, target_role_code, target_user_id,"
                  " from_step_no, from_user_id, task_kind, claimed_by_user_id"
                  " FROM cpq_wf_task WHERE task_id = %s", (tid,))
        row = cur.fetchone()
        if not row:
            raise WfError("任务不存在")
        cid, status, ttype, trole, tuser, from_step, from_uid, kind, claimed_by = row
        kind = kind or TASK_KIND_HANDOFF
        if status == "claimed" and claimed_by is not None and int(claimed_by) == uid:
            # 已经是我自己的任务：直接走幂等出口，重复点击不报错
            return _claim_unavailable(conn, tid, uid)
        # 领取资格与 inbox 的可见性一致；不通过就不发请求、零副作用
        ok = (ttype == "public"
              or (ttype == "role" and trole == user.get("role_code"))
              or (ttype == "user" and tuser is not None and int(tuser) == uid))
        if not ok:
            raise WfError("你没有该任务的领取权限")
        now = _now()
        # 原子领取：单条带 status='open' 条件的 UPDATE + RETURNING，不看先前的 SELECT
        cur = cpq_auth._exec(
            conn,
            "UPDATE cpq_wf_task"
            " SET status = 'claimed', claimed_by_user_id = %s, claimed_at = %s"
            " WHERE task_id = %s AND status = 'open'"
            " RETURNING card_id, task_kind",
            (uid, _ts(now), tid))
        hit = cur.fetchone()
        if not hit:
            return _claim_unavailable(conn, tid, uid)
        if hit[0] is not None:
            cid = hit[0]
        kind = hit[1] or kind
        # 新增工艺等支线任务：领取它不代表接管这张报价卡片，卡片仍归销售经理。
        # 改了持有人的话，销售在「我的报价」里会发现自己的单子姓了别人的名字。
        if kind not in SIDE_TASK_KINDS:
            cpq_auth._exec(
                conn, "UPDATE cpq_wf_card SET current_owner = %s, overall_status = 'in_progress',"
                      " updated_at = %s WHERE card_id = %s", (uid, _ts(now), cid))
        _log(conn, cid, tid, uid, "claim", from_step, None,
             f"{user.get('display_name')} 领取"
             + (f"「{TASK_KIND_LABELS[kind]}」任务" if kind in SIDE_TASK_KINDS else "任务"))
        cur = cpq_auth._exec(conn, "SELECT session_id, title FROM cpq_wf_card WHERE card_id = %s", (cid,))
        r = cur.fetchone()
        sid, ctitle = (r[0], r[1]) if r else ("", "")
        # 消息系统：告诉转交人「任务已被领取」，闭环
        if from_uid and int(from_uid) != uid:
            _msg(conn, int(from_uid), "task_claimed", f"「{ctitle or '未命名报价'}」已被领取",
                 f"{user.get('role_name')}·{user.get('display_name')} 领取了你转交的任务，"
                 f"正在处理第 {from_step} 步", cid, tid, sid, from_step)
        _commit(conn)
        # task_kind 决定前端跳哪儿：handoff 进报价工作台，
        # tech_new_product 进技术工艺的任务专属页（/tech-task.html?tech_task=…）。
        return {"session_id": sid, "task_kind": kind, "task_no": task_no(tid),
                "already": False}
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

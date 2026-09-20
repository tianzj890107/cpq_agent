# -*- coding: utf-8 -*-
"""配置报价CPQ —— 技术工艺 → 主数据 / 报价工作流 的落地桥。

技术工艺（tech_app，:8012 子进程）在 2.2 组装与整合结束时有两个动作：

  ① 写入数据库：把整机的**成品编码 / 名称 / 成本**落到业务主数据两张表
       md_clm_material_base_info   material_id, number, name
       md_clm_material_cost_cnf    material_id, material_code, material_name,
                                   material_unit_price（材料+人工+制费+加工 的合计）
  ② 确认工艺并发送至报价：把报价卡片推到第 3 步「定价-利润加成」，
       销售经理收到任务消息（第 2 步「工艺确认」视为本次动作已完成）。

两件事都只能在这里做：业务库连接、雪花主键、DA 类型清洗（cpq_db）与报价工作流
（cpq_wf）都在一体化服务这一侧。tech_app 通过 HTTP 回调本模块，因此它不需要
psycopg、不需要知道库在哪 —— 与登录（cpq_sso）保持同一个分工。

**没有本地回落**：连不上库就抛错，绝不假装写成功。
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from datetime import datetime, timezone

import cpq_auth
import cpq_case_link
import cpq_db
import cpq_wf

BASE_TABLE = "md_clm_material_base_info"
COST_TABLE = "md_clm_material_cost_cnf"

# 成品写入记录（批次 4）：一次业务写入一行。业务幂等键上的唯一约束是幂等的裁决者，
# 表本身也是"这一版业务动作到底写过没有"的唯一留痕。
WRITE_TABLE = "cpq_wf_material_write"
WRITE_ACTION = "material-write"

# 取号用的 advisory 锁：保护的是"成品编码序列"，与业务幂等键无关。固定常量，
# 跨进程互斥，随事务提交/回滚自动释放。
CODE_LOCK_KEY = 92022

# 成品编码规则：92022 + 3 位流水（92022001、92022002…）。业务侧给定。
CODE_PREFIX = "92022"
CODE_SERIAL_WIDTH = 3
_CODE_RE = re.compile(rf"^{CODE_PREFIX}(\d{{{CODE_SERIAL_WIDTH}}})$")

# 编码取号与插入之间存在竞争（两个人同时点"写入数据库"）。number 上有没有唯一索引
# 未知，所以这里不依赖数据库约束，而是取号后立刻回查确认没被人占走，占了就重试。
_MAX_CODE_RETRY = 5

# 技术工艺推到报价时，工艺确认这一步的步号（cpq_wf.QUOTE_STEPS 第 2 步）。
TECH_CONFIRM_STEP = 2


class BridgeError(Exception):
    """带用户可见文案的业务错误。"""


def _db_error(action: str, exc: Exception) -> BridgeError:
    """把连库/建表/权限这几类失败翻成人能照着做的话。

    以前它们是裸的 psycopg / RuntimeError，一路冒到一体化服务的兜底分支，
    最后只剩一句「服务异常，请稍后重试」——现场对着这句话什么也查不出来。
    """
    text = str(exc).splitlines()[0].strip() if str(exc) else exc.__class__.__name__
    name = exc.__class__.__name__
    low = f"{name} {text}".lower()
    if "timeout" in low or "could not connect" in low or "connection" in low:
        return BridgeError(
            f"{action}失败：连不上业务数据库（{cpq_db.DB_LABEL}）。{name}: {text[:160]}")
    if "不存在" in text or "does not exist" in low or "undefined" in low:
        return BridgeError(
            f"{action}失败：目标表/列在业务库里不存在 —— {text[:200]}。"
            f"请与主数据管理员确认 {BASE_TABLE} / {COST_TABLE} 是否在 schema {cpq_db.PG_SCHEMA} 下")
    if "permission" in low or "denied" in low or "权限" in text or "read-only" in low:
        return BridgeError(
            f"{action}失败：数据库账号没有写权限 —— {text[:200]}。"
            f"当前账号 {cpq_db.PG_USER}，需要对 {BASE_TABLE} / {COST_TABLE} 的 INSERT 权限")
    return BridgeError(f"{action}失败：{name}: {text[:240]}")


# ---------------------------------------------------------------------------
# 成品编码
# ---------------------------------------------------------------------------
def _next_code(conn) -> str:
    """取下一个成品编码。只认严格匹配 92022+3 位的既有编码，其余一律忽略。

    不用 max(number) 直接加一：number 是文本列，库里混着别的编码规则时
    字符串比较会取到一个不相干的最大值。
    """
    cur = cpq_auth._exec(
        conn, f"SELECT number FROM {BASE_TABLE} WHERE number LIKE %s", (f"{CODE_PREFIX}%",))
    used = set()
    for (number,) in cur.fetchall():
        matched = _CODE_RE.match(str(number or "").strip())
        if matched:
            used.add(int(matched.group(1)))
    serial = (max(used) + 1) if used else 1
    if serial >= 10 ** CODE_SERIAL_WIDTH:
        raise BridgeError(
            f"成品编码 {CODE_PREFIX}xxx 的 {CODE_SERIAL_WIDTH} 位流水已用尽（已到 {max(used)}），"
            f"请先与主数据管理员确认新的编码规则")
    return f"{CODE_PREFIX}{serial:0{CODE_SERIAL_WIDTH}d}"


def _code_taken(conn, number: str) -> bool:
    cur = cpq_auth._exec(conn, f"SELECT 1 FROM {BASE_TABLE} WHERE number = %s LIMIT 1", (number,))
    return cur.fetchone() is not None


# ---------------------------------------------------------------------------
# ① 写入主数据
# ---------------------------------------------------------------------------
def _material_idempotency_key(project_id: str, result_version: str) -> str:
    """业务幂等键 `project_id|result_version|material-write`。

    任一分量为空就没有幂等键 —— 老调用方不传新参数时行为必须与今天逐字一致
    （每次新建一个编码），绝不拿名称/简称凑一把键。
    """
    project = str(project_id or "").strip()
    version = str(result_version or "").strip()
    if not project or not version:
        return ""
    return "|".join([project, version, WRITE_ACTION])


def _read_write_record(conn, key: str):
    """按业务幂等键回读写入记录。命中 = 这一版业务动作已经完整写入过。"""
    cur = cpq_auth._exec(
        conn,
        f"SELECT material_id, number, name, unit_price FROM {_write_table()}"
        f" WHERE idempotency_key = %s LIMIT 1", (key,))
    return cur.fetchone()


def _write_table() -> str:
    """写入记录表的运行时表名（带 schema 前缀）。

    它建在 cpq_auth.WF_SCHEMA 下，而 cpq_db.connect 的 search_path 是
    `<PG_SCHEMA>, public` —— 不带前缀会解析不到这张表。
    """
    schema = str(getattr(cpq_auth, "WF_SCHEMA", "") or "").strip()
    return f"{schema}.{WRITE_TABLE}" if schema else WRITE_TABLE


def _write_result(record, *, breakdown, user, result_version: str, key: str) -> dict:
    """把写入记录还原成返回体：命中时必须给**原来那一行**的编码与名称。"""
    try:
        price = round(float(record[3]), 2) if record[3] is not None else None
    except (TypeError, ValueError):
        price = None
    return {
        "material_id": str(record[0] or ""),
        "number": str(record[1] or ""),
        "name": str(record[2] or ""),
        "material_unit_price": price,
        "breakdown": breakdown or {},
        "tables": [BASE_TABLE, COST_TABLE],
        "by": (user or {}).get("display_name") or (user or {}).get("username") or "",
        "already_written": True,
        "idempotency_key": key,
        "result_version": result_version,
    }


def _insert_material(conn, user, name: str, price, breakdown, spec: str) -> dict:
    """取号 → 主数据一行 → 成本配置一行。事务（锁 / 提交 / 回滚）由调用方负责。"""
    for _attempt in range(_MAX_CODE_RETRY):
        number = _next_code(conn)
        if _code_taken(conn, number):
            continue                  # 取号后被人占走，重取（number 唯一索引是第二道防线）
        material_id = cpq_db.snow_next_id(conn)
        base_row = {"material_id": material_id, "number": number, "name": name}
        if spec.strip():
            base_row["spec"] = spec.strip()
        if not cpq_db.insert_rows(conn, BASE_TABLE, [base_row]):
            raise BridgeError(f"{BASE_TABLE} 写入失败：没有可写入的列，请核对表结构")
        cost_row = {
            "md_clm_material_cost_cnf_id": cpq_db.snow_next_id(conn),
            "material_id": material_id,
            "material_code": number,
            "material_name": name,
            # 业务口径：材料+人工+制费+加工 全部汇总到 material_unit_price。
            # direct_labor_unit_price / machine_cost / other_charge 这几列**故意留空**——
            # 下游定价若把它们与 material_unit_price 相加就会重复计费。
            "material_unit_price": price,
        }
        if not cpq_db.insert_rows(conn, COST_TABLE, [cost_row]):
            raise BridgeError(f"{COST_TABLE} 写入失败：没有可写入的列，请核对表结构")
        return {
            "material_id": str(material_id),
            "number": number,
            "name": name,
            "material_unit_price": price,
            "breakdown": breakdown or {},
            "tables": [BASE_TABLE, COST_TABLE],
            "by": (user or {}).get("display_name") or (user or {}).get("username") or "",
        }
    raise BridgeError("连续取号都被占用，请稍后重试")


def _recover_idempotent_hit(key: str, exc: Exception):
    """唯一约束撞键（两个进程同时写同一把键）：收敛成复用对方那一条，不把冲突抛给用户。"""
    text = f"{exc.__class__.__name__} {exc}".lower()
    if not key or ("duplicate key" not in text and "unique" not in text):
        return None
    try:
        conn = cpq_db.connect(readonly=False)
    except Exception:                              # noqa: BLE001 - 兜底读不到就当没命中
        return None
    try:
        return _read_write_record(conn, key)
    except Exception:                              # noqa: BLE001 - 同上
        return None
    finally:
        conn.close()


def _safe_rollback(conn) -> None:
    """回滚，但回滚本身失败（库已经断了）不许盖住真正的失败原因。"""
    try:
        conn.rollback()
    except Exception:                              # noqa: BLE001
        pass


def write_material(user: dict, product_name: str, unit_price, breakdown: dict = None,
                   spec: str = "", project_id: str = "", result_version: str = "") -> dict:
    """写一个成品：主数据一行 + 成本配置一行（业务幂等键命中时只回读，不再新建）。

    业务幂等键（`project_id|result_version|material-write`）是"这一次业务动作"的身份：
    同一个键重复调用（双击 / 超时重试 / 刷新后再点 / 两个进程同时点）只产生一个成品
    编码。**没有键的老调用方**（不传 project_id / result_version）保持今天"每次调用都
    新建一个成品编码"的语义，一行都不变。

    取号 + 主数据 + 成本 + 写入记录整段在**一个事务**里，事务开头先取 advisory 锁
    （跨进程互斥），任一步失败整体回滚 —— 绝不留下"编码有、成本没有"的孤儿行。
    """
    name = (product_name or "").strip()
    if not name:
        raise BridgeError("缺少产品名称")
    try:
        price = round(float(unit_price), 2)
    except (TypeError, ValueError):
        raise BridgeError("材料单价不是数值，无法写入")
    if price <= 0:
        raise BridgeError("材料单价为 0，请先完成成本测算再写入数据库")

    key = _material_idempotency_key(project_id, result_version)
    if not key:                                    # 旧调用方：与今天逐字一致
        try:
            conn = cpq_db.connect(readonly=False)
        except Exception as exc:                   # 连不上/驱动缺失，都要说清是哪一种
            raise _db_error("写入主数据", exc) from exc
        try:
            return _insert_material(conn, user, name, price, breakdown, spec)
        except BridgeError:
            raise
        except Exception as exc:                   # 缺表 / 没权限 / 中途断链
            raise _db_error("写入主数据", exc) from exc
        finally:
            conn.close()

    version = str(result_version).strip()
    try:
        conn = cpq_db.connect(readonly=False, autocommit=False)
    except Exception as exc:
        raise _db_error("写入主数据", exc) from exc
    try:
        # 第一句就拿锁：取号是"读最大值 + 1"，跨进程必须互斥；锁随事务结束自动释放。
        cpq_auth._exec(conn, "SELECT pg_advisory_xact_lock(%s)", (CODE_LOCK_KEY,))
        record = _read_write_record(conn, key)
        if record is not None:
            conn.commit()
            return _write_result(record, breakdown=breakdown, user=user,
                                 result_version=version, key=key)
        result = _insert_material(conn, user, name, price, breakdown, spec)
        write_row = {
            "write_id": cpq_db.snow_next_id(conn),
            "idempotency_key": key,
            "project_id": str(project_id).strip(),
            "result_version": version,
            "action_type": WRITE_ACTION,
            "material_id": result["material_id"],
            "number": result["number"],
            "name": result["name"],
            "unit_price": price,
            "status": "done",
            "created_by_user_id": (user or {}).get("user_id"),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if not cpq_db.insert_rows(conn, _write_table(), [write_row]):
            raise BridgeError(
                f"{_write_table()} 写入失败：没有可写入的列，请核对表结构")
        conn.commit()
        return {**result, "already_written": False, "idempotency_key": key,
                "result_version": version}
    except BridgeError:
        _safe_rollback(conn)
        raise
    except Exception as exc:               # 缺表 / 没权限 / 中途断链 / 撞唯一键
        _safe_rollback(conn)
        hit = _recover_idempotent_hit(key, exc)
        if hit is not None:
            return _write_result(hit, breakdown=breakdown, user=user,
                                 result_version=version, key=key)
        raise _db_error("写入主数据", exc) from exc
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 写入记录表（DDL 必须幂等：老库升级不需要人工步骤）
# ---------------------------------------------------------------------------
def _ddl_pg(schema: str) -> list:
    """写入记录表的 DDL。返回 [(sql, 失败是否致命)]。

    建表失败必须抛错（没有这张表就没有幂等与留痕）；唯一索引建不上只记不抛 ——
    `md_clm_material_base_info.number` 上有没有重复历史数据不由此命令决定，取号本身
    的原子性由事务开头的 advisory 锁保证。
    """
    return [
        (f"""CREATE TABLE IF NOT EXISTS {schema}.cpq_wf_material_write (
                write_id           bigint PRIMARY KEY,
                idempotency_key    varchar(255) NOT NULL,
                project_id         varchar(64)  NOT NULL,
                result_version     varchar(128) NOT NULL,
                action_type        varchar(32)  NOT NULL,
                material_id        varchar(64),
                number             varchar(32),
                name               varchar(255),
                unit_price         numeric(18,2),
                status             varchar(16)  NOT NULL DEFAULT 'done',
                created_by_user_id bigint,
                created_at         timestamptz  NOT NULL DEFAULT now(),
                UNIQUE (project_id, result_version, action_type)
            )""", True),
        (f"CREATE UNIQUE INDEX IF NOT EXISTS uq_wf_material_write_business"
         f" ON {schema}.cpq_wf_material_write (project_id, result_version, action_type)",
         False),
        (f"CREATE UNIQUE INDEX IF NOT EXISTS uq_wf_material_write_key"
         f" ON {schema}.cpq_wf_material_write (idempotency_key)", False),
    ]


def init() -> str:
    """建写入记录表。须在 cpq_auth.init() 之后调用。"""
    conn = cpq_auth._connect()
    try:
        for sql, fatal in _ddl_pg(cpq_auth.WF_SCHEMA):
            try:
                cpq_auth._exec(conn, sql)
            except Exception as exc:               # noqa: BLE001
                if fatal:
                    raise
                print(f"[cpq-tech] 警告：写入记录表唯一索引未建立：{str(exc)[:160]}",
                      file=sys.stderr)
        return f"成品写入记录表已就绪（{cpq_auth.WF_SCHEMA}.{WRITE_TABLE}）"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# ② 确认工艺并发送至报价
# ---------------------------------------------------------------------------
def _force_done(conn, card_id: int, step_no: int, uid, comment: str) -> None:
    """把某一步直接置为完成（不做角色校验）。

    只用于技术工艺推送这一条路径：第 1 步「确认需求配置」归销售经理，工艺经理没法
    自己完成它；但卡片是由技术工艺这边凭空建出来的，需求配置在技术工艺流程里已经
    走完（1.1–1.3 审核通过），不补上这一步，卡片会停在第 1 步而不是定价。
    """
    now = cpq_wf._ts(cpq_wf._now())
    cpq_auth._exec(
        conn, "UPDATE cpq_wf_card_step SET status = 'done', owner_user_id = COALESCE(owner_user_id, %s),"
              " completed_at = COALESCE(completed_at, %s), started_at = COALESCE(started_at, %s)"
              " WHERE card_id = %s AND step_no = %s AND status <> 'done'",
        (uid, now, now, card_id, step_no))
    cpq_wf._log(conn, card_id, None, uid, "step_done", step_no, step_no + 1, comment)


def diagnose() -> list:
    """写主数据这条路能不能走通，逐项报出来。`python cpq_tech_bridge.py` 直接跑。

    现场只会看到一句「服务异常」，而失败可能出在连库、表在哪个 schema、账号有没有
    INSERT 权限、编码取号这四处中的任何一处。这里一次全查清楚，不写任何数据。
    """
    lines = [f"业务库：{cpq_db.DB_LABEL}", f"账号：{cpq_db.PG_USER}"]
    try:
        conn = cpq_db.connect(readonly=False)
    except Exception as exc:
        lines.append(f"❌ 连库失败：{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
        return lines
    lines.append("✅ 连库成功（可写连接）")
    try:
        with conn.cursor() as cur:
            for table in (BASE_TABLE, COST_TABLE):
                cur.execute(
                    "SELECT table_schema FROM information_schema.columns"
                    " WHERE table_name = lower(%s) GROUP BY table_schema", (table,))
                schemas = [row[0] for row in cur.fetchall()]
                if not schemas:
                    lines.append(f"❌ {table}：全库都找不到这张表")
                    continue
                columns = cpq_db.table_types(conn, table)
                lines.append(f"✅ {table}：在 schema {'、'.join(schemas)}；"
                             f"按 search_path 解析到 {len(columns)} 列")
                try:
                    cur.execute("SELECT has_table_privilege(%s, 'INSERT')", (table,))
                    allowed = cur.fetchone()[0]
                    lines.append(f"   {'✅' if allowed else '❌'} INSERT 权限：{allowed}")
                except Exception as exc:
                    lines.append(f"   ⚠ 权限查不到：{type(exc).__name__}: {str(exc)[:120]}")
                missing = [c for c in (("material_id", "number", "name") if table == BASE_TABLE
                                       else ("md_clm_material_cost_cnf_id", "material_id",
                                             "material_code", "material_name",
                                             "material_unit_price"))
                           if c not in columns]
                if missing:
                    lines.append(f"   ❌ 缺列：{'、'.join(missing)}")
        try:
            lines.append(f"✅ 下一个成品编码：{_next_code(conn)}")
        except Exception as exc:
            lines.append(f"❌ 取号失败：{type(exc).__name__}: {str(exc).splitlines()[0][:200]}")
        try:
            lines.append(f"✅ 雪花主键：{cpq_db.snow_next_id(conn)}")
        except Exception as exc:
            lines.append(f"❌ 主键生成失败：{type(exc).__name__}: {str(exc)[:160]}")
    finally:
        conn.close()
    return lines


def _source_task(conn, source_task_id: str):
    """当初那条「新增工艺」任务。找不到就返回 None —— 独立发起的技术工艺项目没有来源。"""
    try:
        tid = int(str(source_task_id).strip())
    except (TypeError, ValueError):
        return None
    cur = cpq_auth._exec(
        conn, "SELECT t.task_id, t.card_id, t.from_user_id, t.task_kind, c.session_id,"
              " u.display_name, u.role_code, u.status"
              " FROM cpq_wf_task t JOIN cpq_wf_card c ON c.card_id = t.card_id"
              " LEFT JOIN cpq_wf_user u ON u.user_id = t.from_user_id"
              " WHERE t.task_id = %s", (tid,))
    row = cur.fetchone()
    if not row:
        return None
    return {"task_id": row[0], "card_id": row[1], "from_user_id": row[2], "task_kind": row[3],
            "session_id": row[4], "from_name": row[5] or "", "from_role": row[6] or "",
            "from_active": (row[7] == "active")}


# ---------------------------------------------------------------------------
# 整机结论 → 报价第 2 步快照
# ---------------------------------------------------------------------------
# 报价工作台在第 3 步会把第 2 步快照里的 s2_products / s2_techparams 回写第 1 步、
# 再沿用到定价表（见 确认需求解析结果.html::syncProductsFromStep2）。所以"把参数都
# 返回给报价"最省事、也最不容易走样的做法，就是按那两张固定表的列名写一份快照 ——
# 不新增接口、不新增界面，销售打开卡片就已经是带着整机参数的第 3 步。
def _forms():
    """取报价固定表单模板。取不到就返回空 —— 快照写不成不该让整次推送失败。"""
    try:
        import cpq_agent_server

        if not cpq_agent_server.FIXED_FORMS:
            cpq_agent_server._init_fixed_forms()
        return cpq_agent_server.FIXED_FORMS, cpq_agent_server._norm_key
    except Exception:
        return {}, (lambda s: str(s or "").strip().lower())


def _fill_row(columns, values: dict, norm) -> dict:
    """按固定列名填一行。列名对不上的值一律丢弃 —— 报价那边只认这些列。"""
    indexed = {norm(k): v for k, v in values.items() if str(v or "").strip()}
    row = {}
    for col in columns:
        value = indexed.get(norm(col["key"]))
        if value not in (None, ""):
            row[col["key"]] = str(value)
    return row


def _step2_snapshot(result: dict) -> dict:
    """把整机结论摊成 {s2_products: {数据: [行]}, s2_techparams: {数据: [行]}}。"""
    forms, norm = _forms()
    if not forms:
        return {}
    material = result.get("material") or {}
    params = result.get("params") or {}
    name = result.get("product_name") or result.get("assembly_name") or ""
    cost = result.get("cost") or {}

    # 成品编码优先取写库拿到的号；退一步取参数里的 product_item_code（2.2 写库时会回填）。
    # 这一格不能空 —— 定价与加价规则就是按它匹配产品行的，空了那边加价全是 0。
    by_code = {f.get("code"): str(f.get("value") or "").strip()
               for f in (params.get("fields") or [])}
    product_values = {
        "成品编码": material.get("number") or by_code.get("product_item_code") or "",
        "成品描述": material.get("name") or by_code.get("product_item_name") or name,
        "产品名称": material.get("name") or by_code.get("product_item_name") or name,
        "数量": result.get("quantity") or "",
        # 价格列在 DA 里就叫「价格」，取的是 md_clm_material_cost_cnf.material_unit_price；
        # 没写库时退回本次测算的单件总成本，别让这一格空着。
        "价格": material.get("unit_price") if material.get("unit_price") is not None else cost.get("total"),
    }
    # 参数按 DA 列名回填：报价的产品技术参数表是按 DA 属性名取数的。
    tech_values = {}
    for field in (params.get("fields") or []):
        value = str(field.get("value") or "").strip()
        if not value:
            continue
        for key in (field.get("da_name"), field.get("name")):
            if key:
                tech_values.setdefault(str(key), value)

    snapshot = {}
    for sid, values in (("s2_products", product_values), ("s2_techparams", tech_values)):
        columns = (forms.get(sid) or {}).get("columns") or []
        row = _fill_row(columns, values, norm)
        if row:
            snapshot[sid] = {"数据": [row]}
    return snapshot


def _result_note(result: dict, base: str) -> str:
    """任务备注里就把关键结论说清楚，销售在待办卡片上不点开也能看见。"""
    material = result.get("material") or {}
    cost = result.get("cost") or {}
    summary = (result.get("params") or {}).get("summary") or {}
    parts = [base or "技术工艺已确认，请进入定价"]
    if material.get("number"):
        parts.append(f"成品 {material['number']} {material.get('name') or ''}".strip())
    if cost.get("total"):
        parts.append(f"单件成本 {float(cost['total']):.2f} 元")
    if summary.get("required_total"):
        parts.append(f"报价必填参数 {summary.get('required_filled', 0)}/{summary['required_total']} 已齐")
    return " · ".join(parts)


def _target_name(target_type: str, role_code: str, target_user_id: str) -> str:
    """这条任务最终落到谁手里 —— 用来回给前端播报，不参与派发本身。"""
    if target_type == "user" and str(target_user_id or "").strip():
        try:
            for row in cpq_auth.list_users():
                if str(row.get("user_id")) == str(target_user_id).strip():
                    who = row.get("display_name") or row.get("username") or ""
                    return f"{who}（{row.get('role_name') or ''}）"
        except Exception:
            pass
        return "指定人员"
    if target_type == "public":
        return "公共任务池"
    return cpq_wf.ROLES.get(role_code, role_code or "")


def _side_task(user: dict, session_id: str, title: str, customer: str, project_name: str,
               kind: str, note: str, payload: dict, target_user_id: str = "",
               target_type: str = "", target_role_code: str = "") -> dict:
    """发一条**支线任务**（不推进报价步骤）。成本测算与成本结果复核都走它。

    卡片是任务的载体：技术工艺自己发起的项目还没有卡片，先按 session_id 建一张
    （sync_card 幂等）。从报价「新增工艺」过来的项目已经有卡片，session_id 就是
    原报价会话，任务会挂在那张卡片上 —— 销售在自己的单子里看得见成本正在测。
    """
    if not user:
        raise BridgeError("请先登录")
    session_id = (session_id or "").strip()
    if not session_id:
        raise BridgeError("缺少会话 ID")
    try:
        cpq_wf.sync_card(session_id, user, title=title, customer=customer,
                         project_name=project_name)
        # 派发方式由调用方（2.2 的「发送至财务」弹窗）决定：role / user / public。
        # 没给就按老行为走：给了人就是指派，否则留空让 cpq_wf 落到该任务的默认角色。
        target_type = (target_type or "").strip() or (
            "user" if str(target_user_id or "").strip() else "")
        sent = cpq_wf.send_task(
            session_id, user, target_type, target_role_code=str(target_role_code or ""),
            target_user_id=str(target_user_id or ""),
            note=note, task_kind=kind, payload=payload or {})
    except (BridgeError, cpq_wf.WfError):
        raise
    except Exception as exc:
        raise _db_error("发送任务", exc) from exc
    # 实际收件角色：定向角色时是选的那个，其余情况仍报该任务的默认角色（谁该干这活）。
    role = (target_role_code if target_type == "role" and target_role_code
            else cpq_wf._KIND_DEFAULT_ROLE.get(kind, ""))
    return {
        "task_id": sent.get("task_id"),
        "task_no": cpq_wf.task_no(sent.get("task_id")),
        "task_kind": kind,
        "task_kind_label": cpq_wf.TASK_KIND_LABELS.get(kind, kind),
        "target_role_code": role,
        "target_role_name": cpq_wf.ROLES.get(role, role),
        "target_type": target_type or "role",
        "target_name": _target_name(target_type or "role", role, target_user_id),
        "source_label": sent.get("source_label", ""),
        "session_id": session_id,
    }


def _handoff_key(session_id: str, tech_project_id: str, handoff_kind: str,
                 result_version: str) -> str:
    """**旧**的四元组交接键（项目 + 报价会话 + 交接类型 + 结果版本）。

    批次 3 之后幂等由 ``cpq_wf_handoff.handoff_key``（五元组，见 ``handoff_key_of``）
    裁决；这个函数只留在溯源里：批次 3 之前的任务把四元组写在 payload.handoff_key，
    新记录会把它一并记为 ``handoff_key_legacy``，迁移期出问题时还能按老键查回来。
    """
    return "|".join([str(tech_project_id or ""), str(session_id or ""),
                     str(handoff_kind or ""), str(result_version or "")])


# 四种回传类型（Spec 5.1）：它们是**同一条**业务命令的四个 kind，不是四套实现。
# 包装第 8 批把包装成本回传也并进同一条命令（第五个 kind）：它同样是"技术侧把结果
# 交给报价"，只是快照与落点按包装口径走（见 packaging_snapshot / HANDOFF_KIND）。
HANDOFF_KINDS = ("cost_to_quote", "cost_to_process", "process_to_quote",
                 "report_to_quote", "packaging_cost_to_quote")
# 包装成本回传的 kind（与 packaging_handoff.HANDOFF_KIND 同值；这里不 import
# tech_app，避免报价侧反向依赖技术工艺 —— Spec §2.1）。
PACKAGING_HANDOFF_KIND = "packaging_cost_to_quote"
# 会推进报价第 2 步的两种：成本回传销售 / 工艺经理确认后回传销售。
# cost_to_process 是支线（只提交给工艺经理复核），report_to_quote 只合并快照。
# 包装成本回传与 cost_to_quote 一样推进第 2 步（确认工艺 → 定价）。
_HANDOFF_ADVANCE_KINDS = ("cost_to_quote", "process_to_quote",
                          PACKAGING_HANDOFF_KIND)
# 不往第 2 步快照里写东西的 kind：成本还没定稿，工艺经理复核前不该当成结论回填。
_HANDOFF_NO_SNAPSHOT_KINDS = ("cost_to_process",)
# 目标任务的 task_kind：三种回传给销售用 handoff；成本提交复核是既有的支线类型。
_HANDOFF_TASK_KIND = {"cost_to_process": cpq_wf.TASK_KIND_TECH_COST_RETURN}
_HANDOFF_LABELS = {
    "cost_to_quote": "成本回传销售",
    "cost_to_process": "成本提交工艺经理复核",
    "process_to_quote": "工艺确认后回传销售",
    "report_to_quote": "已发布报告回传销售",
    PACKAGING_HANDOFF_KIND: "包装成本回传报价",
}


def handoff_key_of(handoff_kind: str, source_task_id, source_project_id,
                   result_version: str, target_quote_session_id: str) -> str:
    """业务幂等键（Spec 6.2）：五元组，必须在**任何写之前**算好。

        handoff_kind | source_task_id | source_project_id | result_version | 目标报价会话号

    键里绝不能出现"本次新生成的会话号" —— 否则新建会话那条路的超时重试永远算不出
    同一把键，会再建一张报价卡片。来源任务号在键里，同一张卡片上两条不同来源的同
    版本结果才不会共用一把键互相吞掉。
    """
    return "|".join([str(handoff_kind or ""), str(source_task_id or "").strip(),
                     str(source_project_id or "").strip(),
                     str(result_version or "").strip(),
                     str(target_quote_session_id or "").strip()])


def _int_or_none(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _handoff_text(result: dict, report: dict = None) -> str:
    """回传正文：技术结果（+已发布报告）的要点，写成报价助手会话里的开场说明。"""
    material = (result or {}).get("material") or {}
    cost = (result or {}).get("cost") or {}
    params = (result or {}).get("params") or {}
    lines = ["技术工艺已回传本单，以下是随任务带过来的技术结果："]
    summary = params.get("summary") or {}
    if summary:
        lines.append("· 参数：" + "；".join(f"{k}={v}" for k, v in list(summary.items())[:10]))
    if material.get("number"):
        lines.append(f"· 成品：{material.get('number')} {material.get('name') or ''}")
    if cost.get("total") is not None:
        lines.append(f"· 单台成本合计：{cost.get('total')}")
    report = report or {}
    if report:
        lines.append(f"· 已发布报告：{report.get('report_no') or ''} "
                     f"v{report.get('version') or ''}「{report.get('title') or ''}」")
        if report.get("conclusion"):
            lines.append(f"· 结论：{report.get('conclusion')}")
    return "\n".join(lines)


def _seed_quote_history(session_id: str, *, title: str = "", project_name: str = "",
                        result: dict = None, report: dict = None) -> None:
    """用报价助手自己的历史存储，为新会话写一条可打开的初始记录。

    复用 ``cpq_agent_server.save_history``（报价助手落盘历史用的就是它），不伪造文件、
    不建第二套存储。只在会话还没有历史时写，绝不覆盖已有历史。
    """
    try:
        import cpq_agent_server
    except Exception:
        return
    if cpq_agent_server.load_history(session_id) is not None:
        return
    created = cpq_wf._iso(cpq_wf._now())
    session = {
        "id": session_id,
        "title": title or (project_name or "技术工艺回传"),
        "created": created,
        "updated": created,
        "step": TECH_CONFIRM_STEP,
        "events": [{"type": "assistant", "text": _handoff_text(result or {}, report)}],
        "messages": [],
    }
    cpq_agent_server.save_history(session)


def ensure_quote_session(user: dict, *, title: str = "", customer: str = "",
                         project_name: str = "", result: dict = None,
                         report: dict = None, session_id: str = "",
                         conn=None, business_case_id: str = "") -> str:
    """技术工艺独立发起、确实没有原报价卡片时，建一条**真实**的报价 Agent 会话。

    绝不拿技术 project_id 当报价 session_id：报价助手是按会话号取历史的，技术项目号
    在那边根本不存在，销售点开会看到空白会话。这里生成的会话号与报价助手新建会话
    同一形状（12 位 hex），并初始化一条可打开的会话记录。

    conn：给了就并进调用方的事务（回传命令用）。这时只能建卡片 —— 会话历史是**磁盘
    写**，必须等事务提交之后由调用方落盘，否则回滚时会留下一个空会话文件。
    session_id：回传命令必须在事务开始前就把会话号算进 payload，所以由它传进来；
    留空时在这里生成。
    """
    session_id = (session_id or "").strip() or uuid.uuid4().hex[:12]
    own = conn is None
    if own:
        conn = cpq_auth._connect()
    try:
        cpq_wf.sync_card(session_id, user, title=title, customer=customer,
                         project_name=project_name, conn=conn,
                         business_case_id=business_case_id)
    finally:
        if own:
            conn.close()
    if own:
        _seed_quote_history(session_id, title=title, project_name=project_name,
                            result=result, report=report)
    return session_id


def send_to_finance(user: dict, session_id: str, title: str, customer: str = "",
                    project_name: str = "", note: str = "", payload: dict = None,
                    target_type: str = "", target_role_code: str = "",
                    target_user_id: str = "") -> dict:
    """技术工艺 2.2 → 财务：工艺与参数已定稿，请到 2.3 做成本测算。

    派发方式与报价助手的转交一致，三种都能选：发给某个角色（默认财务经理）、
    指派给某个人、发布到公共任务池。默认角色只是**没选时**的落点，不是限制 ——
    公司里做这件事的可能不止一个人，让人在弹窗里挑。
    """
    if user.get("role_code") != "process_mgr":
        raise BridgeError(
            f"只有工艺经理能把工艺发给财务测算；当前是「{user.get('role_name')}」")
    return _side_task(user, session_id, title, customer, project_name,
                      cpq_wf.TASK_KIND_TECH_COST,
                      note or "工艺与整机参数已确认，请做成本测算", payload or {},
                      target_user_id=target_user_id, target_type=target_type,
                      target_role_code=target_role_code)


def send_to_quote(user: dict, session_id: str, title: str, customer: str = "",
                  project_name: str = "", note: str = "",
                  source_task_id: str = "", result: dict = None,
                  source_session_id: str = "", report: dict = None,
                  handoff_kind: str = "cost_to_quote", result_version: str = "",
                  source_task_no: str = "", target_user_id: str = "",
                  target_type: str = "", target_role_code: str = "",
                  business_case_id: str = "", create_new: bool = False,
                  create_reason: str = "") -> dict:
    """技术工艺 → 报价的**唯一**回传命令：一个业务动作、一个事务、一次提交。

    四种回传（成本回传销售 / 成本提交工艺经理复核 / 工艺确认后回传销售 / 已发布报告
    回传销售）走的是同一段代码、同一张 cpq_wf_handoff 表：

      0. 校验调用者角色（在事务开始之前；被拒绝时不留任何写入）
      1. 解析落点：来源任务 → 需求单里记的报价会话号 → ensure_quote_session 新建
         一条**真实**报价会话（绝不拿技术 project_id 冒充会话号）
      2. 五元组算 handoff_key（**任何写之前**），插交接记录占位拿幂等键的裁决权
      3. 关闭来源 claimed 待办（同一事务；领取人不是调用者就抛错整段回滚）
      4. 第 2 步只进不退：current_step<=2 → 完成第 2 步并推进到第 3 步「定价-利润加成」；
         已经推进过 → 只**合并**快照，不写回 current_step / overall_status
      5. 创建或复用目标报价任务，payload 带完整 "tech_result"（报告回传再带完整报告包）
      6. 消息与审计：都能按 handoff_id / 目标任务关联回来
      7. 补全交接记录 → commit

    幂等：命中同一把键 → 返回同一条 handoff_id（already_sent=True），零副作用；并发
    由 cpq_wf_handoff.handoff_key 的唯一约束裁决，失败方收敛为复用，不把冲突抛给用户。
    中途任何一步失败 → rollback：绝不允许"报价任务已建、来源待办没关"的半完成状态。

    落点由业务实例号裁决（批次 6，唯一入口 cpq_case_link.resolve）：
      · linked                        —— 唯一候选自动落回原卡片（linked_by = case | task | session）；
      · multiple_candidates           —— 多候选：抛 CaseLinkError，整段回滚，界面列候选让人选；
      · no_candidate                  —— 无候选：抛 CaseLinkError，**绝不静默新建**；
      · create_new                    —— 只有「明确要求新建 + 写了原因」才新建一条真实报价会话
                                        （linked_by='new_session'，并记 recovered_from_project_id /
                                        recovery_reason 留痕）。
    business_case_id / candidates / recovery 随返回体一起回给界面。

    conn 用非 autocommit：全部写在同一条连接的一个事务里，成功后只 commit 一次。
    """
    if not user:
        raise BridgeError("请先登录")
    handoff_kind = str(handoff_kind or "").strip() or "cost_to_quote"
    if handoff_kind not in HANDOFF_KINDS:
        raise BridgeError(f"回传类型无效：{handoff_kind}")
    # 成本测算改由财务经理做（2.3），所以回传报价这一步现在是他的动作；
    # 工艺经理仍然放行 —— 老流程（没有 2.3 的项目）还得走得通。
    if user.get("role_code") not in ("finance_mgr", "process_mgr"):
        raise BridgeError(
            f"只有财务经理或工艺经理能确认并发送至报价；当前是「{user.get('role_name')}」")
    session_id = (session_id or "").strip()
    if not session_id:
        raise BridgeError("缺少会话 ID")
    result = result or {}
    report = report or {}
    # 包装口径的两道拒绝必须在**任何写之前**：非包装结果、成本仍有缺口都不许落任务
    # （Spec §2.3 / §4.3）。三个原行业的四种 kind 不走这条分支。
    if handoff_kind == PACKAGING_HANDOFF_KIND:
        _guard_packaging_result(result)
    # 技术项目号（溯源用）与报价会话号（真正落点）是两回事，不要混用。
    tech_project_id = session_id
    result_version = str(result_version or "").strip()
    task_kind = _HANDOFF_TASK_KIND.get(handoff_kind, cpq_wf.TASK_KIND_HANDOFF)

    try:
        conn = cpq_wf.tx_connect()
    except Exception as exc:                   # 连不上报价库
        raise _db_error("回传报价", exc) from exc

    new_session_id = ""
    try:
        # ① 落点由业务实例号唯一裁决（批次 6）：候选 → decide → linked / 抛错 / 新建。
        #    task / session 两条老线索仍作候选来源（老卡片没有实例号时照样认回原卡），
        #    但"都没有就静默新建"这条老路已经拆掉 —— 没有明确的新建确认与原因，一律拒绝。
        source = None
        if str(source_task_id or "").strip():
            source = _source_task(conn, source_task_id)
        landing = cpq_case_link.resolve(
            conn, business_case_id=business_case_id, source_task_id=source_task_id,
            source_session_id=source_session_id, tech_project_id=tech_project_id,
            create_new=create_new, create_reason=create_reason, user=user)
        quote_session_id = str(landing.get("quote_session_id") or "")
        linked_by = str(landing.get("linked_by") or "")
        resolved_case_id = str(landing.get("business_case_id") or "")
        candidates = list(landing.get("candidates") or [])
        recovery = {
            "recovered_from_project_id": str(landing.get("recovered_from_project_id") or ""),
            "recovery_reason": str(landing.get("recovery_reason") or ""),
            "recovered_by": str(landing.get("recovered_by") or ""),
            "recovered_at": str(landing.get("recovered_at") or ""),
        }
        if str(landing.get("code") or "") == "create_new":
            # 会话号在这里生成，但**不进幂等键** —— 否则超时重试会算出一把新键，
            # 再建一张报价卡片。
            new_session_id = uuid.uuid4().hex[:12]
            linked_by = "new_session"
            resolved_case_id = resolved_case_id or cpq_wf.new_business_case_id()

        # ② 幂等键 + 交接记录占位：唯一约束才是"谁说了算"的裁判，不是先 SELECT 再 INSERT。
        key = handoff_key_of(handoff_kind, source_task_id, tech_project_id,
                             result_version, quote_session_id)
        # 老键（批次 3 之前的四元组）逐字照旧算一遍：它只作**迁移期线索** —— 老任务
        # 把它写在 payload.handoff_key 里，记进本次 payload 就能按老键查回来；新记录的
        # 幂等一律以 cpq_wf_handoff 为准。
        session_id = quote_session_id or new_session_id      # 解析得到的报价会话号（落点）
        legacy_key = _handoff_key(session_id, tech_project_id,
                                  handoff_kind, result_version)
        handoff_id = cpq_wf._new_id(conn)
        created = cpq_wf.insert_handoff_placeholder(
            conn, handoff_id, key, handoff_kind,
            source_project_id=tech_project_id, source_task_id=source_task_id,
            source_result_version=result_version,
            target_quote_session_id=quote_session_id,
            created_by_user_id=_int_or_none(user.get("user_id")),
            business_case_id=resolved_case_id)
        if not created:
            # 这一版已经交过（或并发的另一个请求刚抢先）：读回那一条原样返回，零副作用。
            existing = cpq_wf.find_handoff(conn, key)
            if not existing:
                raise BridgeError("回传记录读取失败，请稍后重试")
            out = _reuse_handoff_outcome(conn, existing, source_task_id, linked_by)
            conn.rollback()
            return out

        # ③ 落点卡片：新建会话这条路的卡片也必须在同一事务里建（回滚要一起没）
        if new_session_id:
            quote_session_id = ensure_quote_session(
                user, title=title, customer=customer, project_name=project_name,
                result=result, report=report, session_id=new_session_id, conn=conn,
                business_case_id=resolved_case_id)
        card = cpq_wf._fetch_card(conn, quote_session_id)
        if not card:
            raise BridgeError(
                f"报价卡片不存在（会话 {quote_session_id}），请让销售先在报价里保存这张卡片")
        if new_session_id:
            # 人工确认新建的恢复留痕：谁、从哪个技术项目、为什么 —— 审计里查得到。
            cpq_wf._log(
                conn, int(card["card_id"]), None, _int_or_none(user.get("user_id")),
                "recover", None, int(card.get("current_step") or 1),
                f'恢复新建：由技术项目 {recovery["recovered_from_project_id"]} 恢复，'
                f'原因：{recovery["recovery_reason"]}')
        # 补前置步骤：第 1 步在技术工艺流程里已经走完（_force_done 的留痕照旧写）。
        card = _commit_card_steps(conn, card, user) or card

        # ④ 关闭来源待办：状态 / 领取人 / 归属都在这里校验；越权 → 整段回滚。
        # 放在完成第 2 步之前：complete_step 会把「本人领取的其它 claimed 任务」一并收尾，
        # 先关掉这里才能如实报出 closed / already。
        src_info = cpq_wf.close_source_task(
            conn, source_task_id, user,
            comment=f"技术工艺{_HANDOFF_LABELS.get(handoff_kind, '回传报价')}："
                    f"来源待办已随本次交接完成")

        # ⑤ 第 2 步：只进不退；快照**只合并**（老键保留、同名覆盖），绝不整份覆盖 ——
        # 第 2 步里还有别人填过的东西（s1_basic 之类），一次回传不该把它们抹掉。
        if handoff_kind == PACKAGING_HANDOFF_KIND:
            # 包装的第 2 步快照走专用投影：盒型 / 参数 / BOM / 路线 / 成本 / 缺口全带上，
            # 外加完整整包（原样保真），不让包装数据在报价卡片上只剩一句 note。
            fresh = packaging_snapshot(result)
        elif handoff_kind in _HANDOFF_NO_SNAPSHOT_KINDS:
            fresh = {}
        else:
            fresh = _step2_snapshot(result)
        if report:
            fresh = _merge_report_snapshot(fresh, report)
        returned_sections = sorted(fresh.keys())
        snapshot = cpq_wf._snapshot_dict(
            cpq_wf.step_snapshot(quote_session_id, TECH_CONFIRM_STEP, conn=conn))
        snapshot.update(fresh)
        current_step = int(card.get("current_step") or 1)
        if handoff_kind in _HANDOFF_ADVANCE_KINDS and current_step <= TECH_CONFIRM_STEP:
            # 代技术侧完成报价第 2 步「工艺确认」。成本拆给财务之后，点这一下的可能是
            # 财务经理，而第 2 步在报价里归工艺经理 —— 开头已经按「财务经理 / 工艺经理」
            # 鉴过权，这里把代办角色显式写出来，由 cpq_wf 校验它确实属于这一步。
            outcome = cpq_wf.complete_step(
                quote_session_id, TECH_CONFIRM_STEP, user,
                snapshot=json.dumps(snapshot, ensure_ascii=False) if snapshot else "",
                on_behalf_of=cpq_wf.role_of_step(TECH_CONFIRM_STEP), conn=conn)
            card = outcome.get("card") or card
            next_step_no = int(outcome.get("next_step_no") or (TECH_CONFIRM_STEP + 1))
            next_step_name = outcome.get("next_step_name") or _step_name(card)
            need_handoff = bool(outcome.get("need_handoff"))
            next_role_code = outcome.get("next_role_code") or ""
            next_role_name = outcome.get("next_role_name") or ""
        else:
            # 报价已经推进过（例如第 4 步的报告回传）：只合并快照，绝不倒退步骤与总状态。
            if snapshot:
                cpq_wf.merge_step_snapshot(quote_session_id, TECH_CONFIRM_STEP, snapshot,
                                           conn=conn)
                card = cpq_wf._fetch_card(conn, quote_session_id) or card
            next_step_no = cpq_wf.advance_step_no(card, TECH_CONFIRM_STEP)
            next_step_name = _step_name(card)
            need_handoff = False
            next_role_code = next_role_name = ""

        # ⑥ 目标任务：create-or-reuse 沿用批次 2 的同类复用规则；payload 带完整技术结果
        payload = {
            "tech_result": result,
            "report": report,
            "handoff_id": str(handoff_id),
            "handoff_key": key,
            # 老键只作迁移期线索：批次 3 之前交过的任务按它查得回来。
            "handoff_key_legacy": legacy_key,
            "handoff_kind": handoff_kind,
            "result_version": result_version,
            "source_project_id": tech_project_id,
            "source_task_id": str(source_task_id or ""),
            "source_task_no": source_task_no,
            "tech_project_id": tech_project_id,
            "quote_session_id": quote_session_id,
        }
        if handoff_kind == PACKAGING_HANDOFF_KIND:
            # 任务 payload 带整包：不能只有任务卡没有业务数据（Spec §4.3）。
            payload["packaging_package"] = (fresh.get("packaging_package")
                                            or _packaging_package_of(result))
        target = _handoff_target(source, task_kind, target_type, target_role_code,
                                 target_user_id)
        sent = cpq_wf.send_task(
            quote_session_id, user, target["target_type"],
            target_role_code=target["target_role_code"],
            target_user_id=target["target_user_id"],
            note=_result_note(result, note), task_kind=task_kind,
            payload=payload, conn=conn)

        # ⑦ 把交接记录的落点补全（目标任务 / 快照栏目 / 来源待办关闭结果）
        cpq_wf.update_handoff(
            conn, handoff_id,
            target_quote_session_id=quote_session_id,
            target_card_id=_int_or_none(card.get("card_id")),
            target_task_id=_int_or_none(sent.get("task_id")),
            target_task_kind=task_kind, step_no=next_step_no,
            snapshot_sections=returned_sections,
            source_task_closed=bool(src_info.get("closed")),
            source_task_status=str(src_info.get("status") or ""),
            business_case_id=resolved_case_id)
        conn.commit()
    except (BridgeError, cpq_wf.WfError, cpq_case_link.CaseLinkError):
        conn.rollback()
        raise
    except Exception as exc:                   # 表结构对不上 / 事务中途任何一步出错
        conn.rollback()
        raise _db_error("回传报价", exc) from exc
    finally:
        conn.close()

    if new_session_id:
        # 会话历史是**磁盘写**：只在事务提交之后落盘（回滚时不许留下空历史文件）。
        _seed_quote_history(new_session_id, title=title, project_name=project_name,
                            result=result, report=report)

    return _handoff_outcome(
        handoff_id=handoff_id, key=key, handoff_kind=handoff_kind,
        quote_session_id=quote_session_id, linked_by=linked_by,
        next_step_no=next_step_no, next_step_name=next_step_name,
        returned_sections=returned_sections, sent=sent, src_info=src_info,
        card=card, need_handoff=need_handoff, next_role_code=next_role_code,
        next_role_name=next_role_name, target=target,
        business_case_id=resolved_case_id, candidates=candidates, recovery=recovery)


def _step_name(card) -> str:
    """卡片当前所处步骤的中文名。"""
    return dict((s[0], s[1]) for s in cpq_wf.QUOTE_STEPS).get(
        int((card or {}).get("current_step") or 1), "")


def _commit_card_steps(conn, card, user) -> dict:
    """把第 2 步之前的前置步骤补齐为已完成（技术工艺流程里它们已经走完）。"""
    for step_no in range(1, TECH_CONFIRM_STEP):
        _force_done(conn, int(card["card_id"]), step_no, int(user["user_id"]),
                    "技术工艺推送：前置步骤在技术工艺流程中已完成")
    cpq_wf._commit(conn)
    return card


#: 包装第 2 步快照的栏目（Spec §4.3）：盒型 / 参数 / BOM / 路线 / 成本不能丢。
PACKAGING_SNAPSHOT_SECTIONS = ("s2_packaging", "s2_packaging_cost", "packaging_package")


def _packaging_package_of(result: dict) -> dict:
    """回传正文里的整包：``bridge_result`` 给的是 ``packaging_package``；直接传整包时就是它自己。"""
    package = (result or {}).get("packaging_package")
    if isinstance(package, dict) and package:
        return package
    return dict(result or {})


def _guard_packaging_result(result: dict) -> None:
    """包装口径的两道拒绝（Spec §2.3 / §4.3）：非包装、成本仍有缺口。

    必须在任何写之前调用 —— 被拒绝的回传不许建任务、不许留半完成状态。
    """
    result = result or {}
    if str(result.get("industry") or "").strip() != "packaging":
        raise BridgeError(
            "包装成本回传只接受 industry=packaging 的技术结果；"
            f"当前是「{str(result.get('industry') or '未标明')}」。"
            "三个原行业的成本回传请用 cost_to_quote。")
    package = _packaging_package_of(result)
    cost = package.get("cost") or result.get("cost") or {}
    gaps = list(cost.get("gaps") or package.get("gaps") or result.get("gaps") or [])
    if cost.get("has_gaps") or gaps:
        codes = []
        for item in gaps:
            code = str((item or {}).get("code") or "").strip() if isinstance(item, dict) else ""
            if code and code not in codes:
                codes.append(code)
        named = "、".join(codes) or "未标明缺口"
        raise BridgeError(f"成本仍有缺口（{named}），不能落成正式报价；"
                          "请先补齐，或由财务/工艺写明原因走放行留痕后再回传。")


def packaging_snapshot(result: dict) -> dict:
    """包装专用第 2 步快照：盒型 + 参数 + 数量 + 场景 / 成本分项 + 缺口数 + 成本版本 /
    完整整包（原样保真，供历史与看板重建）。三行业不走这条分支（Spec §4.3）。"""
    package = _packaging_package_of(result)
    requirement = package.get("requirement") or {}
    box = package.get("box_type") or {}
    params = package.get("params") or {}
    cost = package.get("cost") or {}
    source = package.get("source") or {}
    gaps = list(cost.get("gaps") or package.get("gaps") or [])
    box_code = (str(box.get("confirmed_box_type") or "").strip()
                or str(requirement.get("box_type") or "").strip())
    packaging_row = {
        "行业": "包装",
        "盒型": box_code,
        "盒型状态": str(box.get("decision") or ""),
        "闭合方式": requirement.get("closure_type") or params.get("closure_type") or "",
        "内长": params.get("inner_length"), "内宽": params.get("inner_width"),
        "内高": params.get("inner_height"),
        "配合间隙": params.get("fit_clearance"),
        "面纸克重": requirement.get("face_paper_gsm"),
        "V槽": requirement.get("v_groove"),
        "报价数量": requirement.get("quote_quantity"),
        "场景": str(source.get("scenario_code") or "default"),
        "需求单号": str(source.get("requirement_no") or ""),
        "成本结果版本": str(source.get("result_version") or package.get("result_version") or ""),
    }
    cost_row = {
        "单件总成本": "%.2f" % float(cost.get("total_cost") or 0.0),
        "小计": "%.2f" % float(cost.get("subtotal") or 0.0),
        "材料": "%.2f" % float(cost.get("material_total") or 0.0),
        "工艺": "%.2f" % float(cost.get("process_total") or 0.0),
        "人工": "%.2f" % float(cost.get("labor_total") or 0.0),
        "工装": "%.2f" % float(cost.get("tooling_total") or 0.0),
        "包装": "%.2f" % float(cost.get("packaging_total") or 0.0),
        "运输": "%.2f" % float(cost.get("freight_total") or 0.0),
        "损耗": "%.2f" % float(cost.get("loss_amount") or 0.0),
        "缺口数": len(gaps),
        "成本引擎版本": str(cost.get("engine_version") or ""),
        "成本档位": str(cost.get("cost_profile") or ""),
        "是否含缺口": bool(cost.get("has_gaps")),
    }
    return {
        "s2_packaging": {"kind": "packaging", "title": "包装：盒型与参数",
                         "数据": [packaging_row]},
        "s2_packaging_cost": {"kind": "packaging", "title": "包装：成本构成",
                              "数据": [cost_row]},
        "packaging_package": package,
    }


def _merge_report_snapshot(snapshot: dict, report: dict) -> dict:
    """把已发布报告要点并进第 2 步快照，让销售在报价卡片上看得到报告出处。"""
    merged = dict(snapshot or {})
    merged["s2_report"] = {"数据": [{
        "报告编号": report.get("report_no") or "",
        "版本": report.get("version") or "",
        "标题": report.get("title") or "",
        "状态": report.get("status") or "",
        "审核人": report.get("reviewed_by") or "",
        "审核时间": report.get("reviewed_at") or "",
        "发布人": report.get("published_by") or "",
        "发布时间": report.get("published_at") or "",
        "结论": report.get("conclusion") or "",
        "报告链接": report.get("report_url") or "",
    }]}
    return merged


def _handoff_target(source, task_kind: str, target_type: str = "",
                    target_role_code: str = "", target_user_id: str = "") -> dict:
    """目标任务最终发给谁。

    · 回传给销售（task_kind=handoff）：原报价卡片归销售经理；来源任务当初若是销售经理
      发的（报价第 1 步「新增工艺」），就定向退回给那个人本人 —— 是他在等这台新产品。
    · 成本提交工艺经理复核（tech_cost_return）：走这条支线的默认收件角色，或调用方指定。
    """
    if str(target_user_id or "").strip():
        return {"target_type": "user", "target_user_id": str(target_user_id).strip(),
                "target_role_code": "", "returned_to_sender": False}
    wanted_type = str(target_type or "").strip()
    if wanted_type and wanted_type != "role":
        return {"target_type": wanted_type, "target_user_id": "",
                "target_role_code": "", "returned_to_sender": False}
    if wanted_type == "role" and str(target_role_code or "").strip():
        return {"target_type": "role", "target_role_code": str(target_role_code).strip(),
                "target_user_id": "", "returned_to_sender": False}
    if task_kind == cpq_wf.TASK_KIND_HANDOFF:
        sender_id = (source or {}).get("from_user_id")
        if sender_id and (source or {}).get("from_active") and \
                (source or {}).get("from_role") == "sales_mgr":
            return {"target_type": "user", "target_user_id": str(sender_id),
                    "target_role_code": "", "returned_to_sender": True}
        return {"target_type": "role", "target_role_code": "sales_mgr",
                "target_user_id": "", "returned_to_sender": False}
    role = str(target_role_code or "").strip() or cpq_wf._KIND_DEFAULT_ROLE.get(task_kind, "")
    return {"target_type": "role", "target_role_code": role,
            "target_user_id": "", "returned_to_sender": False}


def _recovery_dict(recovered_from_project_id="", recovery_reason="",
                   recovered_by="", recovered_at="") -> dict:
    """恢复留痕（批次 6）：没走新建时四项也在、值为空串 —— 界面才能区分"没恢复"与"字段缺失"。"""
    return {"recovered_from_project_id": str(recovered_from_project_id or ""),
            "recovery_reason": str(recovery_reason or ""),
            "recovered_by": str(recovered_by or ""),
            "recovered_at": str(recovered_at or "")}


def _handoff_outcome(*, handoff_id, key, handoff_kind, quote_session_id, linked_by,
                     next_step_no, next_step_name, returned_sections, sent, src_info,
                     card=None, need_handoff=False, next_role_code="", next_role_name="",
                     target=None, already_sent=False, already_completed=False,
                     business_case_id="", candidates=None, recovery=None) -> dict:
    """统一的回传返回体（Spec 6.4）：既有键一个不少，新增的是 handoff_id 这一套溯源字段。"""
    target = target or {}
    task_kind = (sent or {}).get("task_kind") or cpq_wf.TASK_KIND_HANDOFF
    role_code = (target.get("target_role_code")
                 or cpq_wf._KIND_DEFAULT_ROLE.get(task_kind, ""))
    task_id = str((sent or {}).get("task_id") or "")
    return {
        "handoff_id": str(handoff_id or ""),
        "handoff_key": key,
        "handoff_kind": handoff_kind,
        "quote_session_id": quote_session_id,
        "linked_by": linked_by,
        "new_card": linked_by == "new_session",
        # 业务实例号 / 候选清单 / 恢复留痕（批次 6）：复用同一把幂等键的返回体也带这三项。
        "business_case_id": str(business_case_id or ""),
        "candidates": [dict(item or {}) for item in (candidates or [])],
        "recovery": _recovery_dict(**(recovery or {})),
        "already_sent": bool(already_sent),
        "already_completed": bool(already_completed),
        "next_step_no": next_step_no,
        "next_step_name": next_step_name,
        "returned_sections": list(returned_sections or []),
        "handoff": {
            "task_id": task_id,
            "task_no": (sent or {}).get("task_no") or "",
            "task_kind": task_kind,
            "target_type": target.get("target_type") or "role",
            "target_user_id": target.get("target_user_id") or "",
            "target_role_code": role_code,
            "target_role_name": cpq_wf.ROLES.get(role_code, role_code),
            "target_name": _target_name(target.get("target_type") or "role", role_code,
                                        target.get("target_user_id") or ""),
            "returned_to_sender": bool(target.get("returned_to_sender")),
            "source_task_no": (sent or {}).get("source_task_no") or "",
            "session_id": quote_session_id,
        },
        "source_task": dict(src_info or {}),
        # 老调用方（2.3 的动作留痕 / 前端）一直在顶层读这几项，保留
        "target_type": target.get("target_type") or "role",
        "target_name": _target_name(target.get("target_type") or "role", role_code,
                                    target.get("target_user_id") or ""),
        "target_role_name": cpq_wf.ROLES.get(role_code, role_code),
        "target_role_code": role_code,
        "task_kind": task_kind,
        "task_kind_label": cpq_wf.TASK_KIND_LABELS.get(task_kind, task_kind),
        "source_label": (sent or {}).get("source_label") or "",
        # 既有返回键（成本 / 报告两条技术侧路径与前端一直在读）
        "task_id": task_id,
        "task_no": (sent or {}).get("task_no") or "",
        "card": card,
        "need_handoff": bool(need_handoff),
        "next_role_code": next_role_code,
        "next_role_name": next_role_name,
    }


def _reuse_handoff_outcome(conn, row, source_task_id, linked_by) -> dict:
    """命中同一把幂等键：返回同一条记录，不建任务、不补步骤、不发消息、不写审计。"""
    tid = row.get("target_task_id")
    tkind = str(row.get("target_task_kind") or cpq_wf.TASK_KIND_HANDOFF)
    step_no = _int_or_none(row.get("step_no"))
    sections = row.get("snapshot_sections")
    if isinstance(sections, str):
        try:
            sections = json.loads(sections)
        except ValueError:
            sections = []
    if not isinstance(sections, list):
        sections = []
    status = ""
    if str(source_task_id or "").strip():
        cur = cpq_auth._exec(conn, "SELECT status FROM cpq_wf_task WHERE task_id = %s",
                             (_int_or_none(source_task_id),))
        got = cur.fetchone()
        status = str(got[0] or "") if got else ""
    src_info = {
        "task_id": str(row.get("source_task_id") or ""),
        "closed": bool(row.get("source_task_closed")),
        "status": status,
        "already": status == "completed",
        "skipped": "" if str(source_task_id or "").strip() else "missing_task_id",
    }
    role_code = (cpq_wf._KIND_DEFAULT_ROLE.get(tkind, "")
                 if tkind != cpq_wf.TASK_KIND_HANDOFF else "sales_mgr")
    # 复用同一把幂等键：实例号取交接记录上的（落点与本次实际使用的一致），候选为空、
    # 恢复留痕四项在但为空串 —— 界面据此知道"这次没有恢复新建"。
    return _handoff_outcome(
        handoff_id=row.get("handoff_id"), key=row.get("handoff_key"),
        handoff_kind=row.get("handoff_kind"),
        quote_session_id=row.get("target_quote_session_id") or "",
        linked_by=linked_by, next_step_no=step_no,
        next_step_name=dict((s[0], s[1]) for s in cpq_wf.QUOTE_STEPS).get(step_no, ""),
        returned_sections=sections,
        sent={"task_id": tid, "task_no": cpq_wf.task_no(tid), "task_kind": tkind},
        src_info=src_info, already_sent=True,
        already_completed=(status == "completed"),
        target={"target_role_code": role_code},
        business_case_id=str(row.get("business_case_id") or ""))


def return_to_process(user: dict, session_id: str, title: str, customer: str = "",
                      project_name: str = "", note: str = "", payload: dict = None,
                      target_user_id: str = "", source_task_id: str = "") -> dict:
    """技术工艺 4 成本测算 → 工艺经理：成本已确认，请做最终工艺确认与报告（第 5 阶段）。

    正常提交确认，不是返工支线：工艺经理领取后进第 5 阶段「工艺评估报告」，
    在那里汇总、审核、发布；确实要返工时由第 5 阶段明确退回第 3 阶段。

    它和「回传销售」是同一条原子命令（handoff_kind=cost_to_process，实现见
    send_to_quote）：落在**原报价卡片**上 —— 绝不拿技术项目号当会话号新建一张卡片
    （那会造出一张销售根本打不开的幽灵卡片）；只发一条 tech_cost_return 支线任务、
    不推进报价步骤，并在同一个事务里关掉来源待办、写回传记录。
    """
    if user.get("role_code") != "finance_mgr":
        raise BridgeError(
            f"只有财务经理能提交成本结果给工艺经理；当前是「{user.get('role_name')}」")
    package = dict(payload or {})
    if source_task_id:
        package.setdefault("source_task_id", str(source_task_id))
    tech_project = str(session_id or package.get("tech_project_id") or "").strip()
    return send_to_quote(
        user, tech_project, title, customer, project_name,
        note or "成本已确认，请做最终工艺确认与报告",
        str(source_task_id or package.get("source_task_id") or ""),
        result=package,
        # 原报价会话号：需求单里记着的那张卡片，认不回时才会新建真实会话。
        source_session_id=str(package.get("source_session_id")
                              or package.get("quote_session_id") or ""),
        report=None, handoff_kind="cost_to_process",
        result_version=str(package.get("result_version") or ""),
        target_user_id=str(target_user_id or ""))


if __name__ == "__main__":
    # 现场自查：python cpq_tech_bridge.py
    for line in diagnose():
        print(line)

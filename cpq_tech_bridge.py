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

import cpq_auth
import cpq_db
import cpq_wf

BASE_TABLE = "md_clm_material_base_info"
COST_TABLE = "md_clm_material_cost_cnf"

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
def write_material(user: dict, product_name: str, unit_price, breakdown: dict = None,
                   spec: str = "") -> dict:
    """新建一个成品：主数据一行 + 成本配置一行。返回写入结果供技术工艺留痕。

    每次调用都**新建**一个成品编码 —— 业务要的就是"每次产生一个新的成品编码"，
    不做按名称去重：同名不同配置的成品在报价里是两个东西。
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

    try:
        conn = cpq_db.connect(readonly=False)
    except Exception as exc:                       # 连不上/驱动缺失，都要说清是哪一种
        raise _db_error("写入主数据", exc) from exc
    try:
        for attempt in range(_MAX_CODE_RETRY):
            try:
                number = _next_code(conn)
                if _code_taken(conn, number):
                    continue                  # 取号后被人占走，重取
                material_id = cpq_db.snow_next_id(conn)
                base_row = {"material_id": material_id, "number": number, "name": name}
                if spec.strip():
                    base_row["spec"] = spec.strip()
                written = cpq_db.insert_rows(conn, BASE_TABLE, [base_row])
                if not written:
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
            except BridgeError:
                raise
            except Exception as exc:          # 缺表 / 没权限 / 中途断链
                raise _db_error("写入主数据", exc) from exc
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


def return_to_process(user: dict, session_id: str, title: str, customer: str = "",
                      project_name: str = "", note: str = "", payload: dict = None,
                      target_user_id: str = "") -> dict:
    """技术工艺 2.3 → 工艺经理：成本测完了，请复核工艺与用量。

    为什么不是财务自己改：成本高在哪，往往是工序或用量的问题，那是工艺的判断。
    财务把数和疑点退回去，工艺经理改完再走一遍 —— 各管各的那一段。
    """
    if user.get("role_code") != "finance_mgr":
        raise BridgeError(
            f"只有财务经理能退回成本结果；当前是「{user.get('role_name')}」")
    return _side_task(user, session_id, title, customer, project_name,
                      cpq_wf.TASK_KIND_TECH_COST_RETURN,
                      note or "成本已测算，请复核工艺与用量", payload or {}, target_user_id)


def send_to_quote(user: dict, session_id: str, title: str, customer: str = "",
                  project_name: str = "", note: str = "",
                  source_task_id: str = "", result: dict = None,
                  source_session_id: str = "") -> dict:
    """技术工艺确认完工艺，把报价卡片推进到第 3 步「定价-利润加成」并通知销售经理。

    两条路径：

      · 有 source_task_id（这单是报价那边「新增工艺」派过来的）——回到**原来那张报价
        卡片**，把整机参数写进第 2 步快照，任务定向退回给当初发起的那个人。
        销售打开的就是他自己那单报价的第 3 步，右侧已经带着新产品与技术参数。

      · 没有来源（技术工艺自己发起的项目）——沿用旧行为：以项目号建一张卡片，
        按角色群发给销售经理。

    两条路走的都是标准 cpq_wf.complete_step(第 2 步)：卡片状态、步骤留痕、任务与消息
    全部复用，这里只负责把前置步骤补齐、把结论塞进快照。
    """
    if not user:
        raise BridgeError("请先登录")
    # 成本测算改由财务经理做（2.3），所以发报价这一步现在是他的动作；
    # 工艺经理仍然放行 —— 老流程（没有 2.3 的项目）还得走得通。
    if user.get("role_code") not in ("finance_mgr", "process_mgr"):
        raise BridgeError(
            f"只有财务经理或工艺经理能确认并发送至报价；当前是「{user.get('role_name')}」")
    session_id = (session_id or "").strip()
    if not session_id:
        raise BridgeError("缺少会话 ID")
    result = result or {}

    source = None
    # 结论要送回**哪张报价卡片**。三条线索，按可靠性排序：
    #   task    —— 当初那条「新增工艺」任务（能连人带卡片一起找到，最完整）；
    #   session —— 需求单里记着的原报价会话号（任务行被删/被顶掉时的后手）；
    #   new     —— 都没有：技术工艺自己发起的项目，只能新建一张卡片。
    # 前两条都落空却走了第三条时，销售点开任务会打不开会话历史（报价助手按会话号
    # 取历史，技术项目号在那边根本不存在），客户信息也只剩技术侧填过的那点。
    # 所以这里必须把用了哪条线索**如实回给界面**，不能默默新建。
    linked_by = "new"
    try:
        conn = cpq_auth._connect()
        try:
            if str(source_task_id or "").strip():
                source = _source_task(conn, source_task_id)
                if source and source.get("session_id"):
                    # 回到原来那张报价卡片：新建一张会让销售那单永远停在第 1 步等新产品。
                    session_id = str(source["session_id"])
                    linked_by = "task"
            if linked_by == "new":
                hint = str(source_session_id or "").strip()
                # 会话号得在报价里真有对应卡片才认，否则等于换了个名字新建。
                if hint and cpq_wf._fetch_card(conn, hint):
                    session_id = hint
                    linked_by = "session"
        finally:
            conn.close()

        if linked_by == "new":
            cpq_wf.sync_card(session_id, user, title=title, customer=customer,
                             project_name=project_name)

        conn = cpq_auth._connect()
        try:
            card = cpq_wf._fetch_card(conn, session_id)
            if not card:
                raise BridgeError("报价卡片创建失败")
            for step_no in range(1, TECH_CONFIRM_STEP):
                _force_done(conn, int(card["card_id"]), step_no, int(user["user_id"]),
                            "技术工艺推送：前置步骤在技术工艺流程中已完成")
            cpq_wf._commit(conn)
        finally:
            conn.close()
    except (BridgeError, cpq_wf.WfError):
        raise
    except Exception as exc:                   # 连不上报价库 / 表结构对不上
        raise _db_error("推送到报价", exc) from exc

    snapshot = _step2_snapshot(result)
    # 代技术侧完成报价第 2 步「工艺确认」。成本拆给财务之后，点这一下的可能是财务经理
    # （2.3 测完成本才回传报价），而第 2 步在报价里归工艺经理 —— 本函数开头已经按
    # 「财务经理 / 工艺经理」鉴过权，这里把代办角色显式写出来，由 cpq_wf 校验它确实
    # 是该步骤的归属角色，并在留痕里记下是谁代的。
    outcome = cpq_wf.complete_step(
        session_id, TECH_CONFIRM_STEP, user,
        snapshot=json.dumps(snapshot, ensure_ascii=False) if snapshot else "",
        on_behalf_of=cpq_wf.role_of_step(TECH_CONFIRM_STEP))
    outcome["returned_sections"] = sorted(snapshot.keys())

    payload = {"tech_result": result}
    if source and source.get("from_user_id") and source.get("from_active"):
        # 定向退回给当初发起「新增工艺」的那个人。complete_step 的自动推送是发给
        # **卡片创建人**的，两者通常是同一个人；不同的时候，以发起人为准 ——
        # 是他在等这台新产品。send_task 会把上一条 open 任务置为 cancelled。
        sent = cpq_wf.send_task(
            session_id, user, "user", target_user_id=str(source["from_user_id"]),
            note=_result_note(result, note), payload=payload)
        outcome["handoff"] = {
            "target_user_id": str(source["from_user_id"]),
            "target_name": source.get("from_name") or "",
            "target_role_name": cpq_wf.ROLES.get(source.get("from_role"), source.get("from_role") or ""),
            "task_id": sent.get("task_id"),
            "source_task_no": cpq_wf.task_no(source["task_id"]),
            "returned_to_sender": True,
        }
    elif outcome.get("need_handoff") and not outcome.get("auto_handoff"):
        # complete_step 只在"卡片创建人正好是销售经理"时自动推送。技术工艺自己建的
        # 卡片创建人就是工艺经理，所以那条路走不通 —— 退回按角色群发给销售经理。
        sent = cpq_wf.send_task(
            session_id, user, "role", target_role_code="sales_mgr",
            note=_result_note(result, note), payload=payload)
        outcome["handoff"] = {"target_role_code": "sales_mgr",
                              "target_role_name": cpq_wf.ROLES.get("sales_mgr", "销售经理"),
                              "task_id": sent.get("task_id")}
    if source and not (source.get("from_user_id") and source.get("from_active")):
        # 发起人被停用/删号：不能假装退回给了他，界面要照实说。
        outcome["source_sender_unavailable"] = True
    outcome["quote_session_id"] = session_id
    # 界面据此说清这单落到哪儿了：新建卡片时销售那边既没有会话历史、也没有原始客户信息，
    # 必须明说，不能让人以为结论回到了他原来那张报价单上。
    outcome["linked_by"] = linked_by
    outcome["new_card"] = linked_by == "new"
    return outcome


if __name__ == "__main__":
    # 现场自查：python cpq_tech_bridge.py
    for line in diagnose():
        print(line)

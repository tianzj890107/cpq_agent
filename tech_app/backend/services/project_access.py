# -*- coding: utf-8 -*-
"""技术项目「我的 / 全部」可见范围与项目级读写权限的**唯一判定入口**（批次 7）。

在此之前：`GET /api/projects` 直接 `store.list_projects()`（连当前用户都不取），41 个
`/api/projects/{project_id}/...` 读接口几乎没有项目级判定，写接口只对 `engineer` 做
「本人项目」校验。结果是：知道 12 位项目号的人能读到任何项目的图纸、审计与 Agent 历史，
「我的清单」靠前端本地拼，销售经理被 `sales_mgr -> viewer` 压成陌生人，且「不存在」
与「无权限」的响应可区分（泄露存在性）。

本模块把这层收成两个函数：

  · `visible_projects(user, scope)`   —— 列表：我的 / 权限范围内的全部 / 我有权限的归档；
  · `require_project_access(pid, user, mode)` —— 单个项目：读 / 写开工前先过它。

角色口径取 `user["role"]`（技术工艺）与 `user["cpq_role_code"]`（CPQ）的并集，所以
`sales_mgr -> viewer` 不会把销售经理认成只读陌生人。ACL 只决定「能不能看到这个项目、
能不能碰这个项目」，**不代替**各业务接口的 `_require`（谁能做哪一步仍归它们）。
"""
from __future__ import annotations

import re
from typing import List

from ..storage import store
from . import auth, cpq_sso, home_card

MODES = ("read", "contribute", "write")
# 批次 10 新增 todo：只列「当前用户此刻能动手」的项目（card.primary_action 存在且
# 该子步骤对本人 actionable）。归档在 mine / all / todo 里一律不出现。
SCOPES = ("mine", "all", "todo", "archived")

# 专属业务动作白名单（Spec §18.5）：**显式**逐条列出，不许写成「路径以 /cost 结尾就…」
# 这类推断式规则 —— 降级放行必须一条一条看得见、能被审。命中这些路由时项目 ACL 只判
# 「可见 + 未归档」，能不能做这一步仍由接口自己的 _require 说话（Spec §18.2/§18.4）。
CONTRIBUTE_ROUTES = (
    ("POST", "/api/projects/{project_id}/agent/event"),
    ("POST", "/api/projects/{project_id}/parts/{part_id}/cost"),
    ("PUT", "/api/projects/{project_id}/parts/{part_id}/cost"),
    ("POST", "/api/projects/{project_id}/integration/cost"),
    ("PUT", "/api/projects/{project_id}/integration/cost"),
    ("PUT", "/api/projects/{project_id}/cost-review"),
    ("POST", "/api/projects/{project_id}/cost-review/parts/{part_id}"),
    ("POST", "/api/projects/{project_id}/cost-review/assembly"),
    ("POST", "/api/projects/{project_id}/cost-review/confirm"),
    ("POST", "/api/projects/{project_id}/cost-review/material-write"),
    ("POST", "/api/projects/{project_id}/cost-review/send-to-quote"),
    ("POST", "/api/projects/{project_id}/cost-review/return-to-process"),
    ("POST", "/api/projects/{project_id}/pricing/review"),
    ("POST", "/api/projects/{project_id}/approval/act"),
    ("POST", "/api/projects/{project_id}/versions/{version}/approve"),
    ("POST", "/api/projects/{project_id}/versions/{version}/reject"),
    ("POST", "/api/projects/{project_id}/requirement/review"),
    ("POST", "/api/projects/{project_id}/process-report/review"),
    ("PUT", "/api/projects/{project_id}/process-report/distribution"),
    ("POST", "/api/projects/{project_id}/process-report/publish"),
    ("PUT", "/api/projects/{project_id}/requirement/customer-credit"),
    # 注意：「包装成本测算」不在这一张表里 —— 本表被
    # `tests/test_tech_project_acl_contribute_mode_red`（Spec §18.5）逐条钉死为**正好 21 条**，
    # 加一条就把它打红。那一条动作改用下面 `PACKAGING_COST_ROUTES` 表达的**同一套范式**
    # （显式逐条白名单 + 动作级依据，Spec `packaging-cost-write-role-single-source.md` §2.1），
    # 见 `packaging_cost_action_basis()`。
)


#: 包装成本的四条路由（Spec `packaging-cost-write-role-single-source.md` §2.1 / §2.2）。
#: 这一组是**唯一**允许「项目还没算出成本也按动作放行 / 给可判码」的动作：财务在成本算出来
#: 之前按 `_packaging_cost_visible()` 本来就看不见项目，一律回 404「项目不存在」的话，用户
#: 分辨不出「我没权限」「项目被删了」和「还没算过」。逐条白名单，不写成推断式规则（Spec §18.5）。
PACKAGING_COST_ROUTES = (
    ("POST", "/api/projects/{project_id}/requirement/packaging-cost"),
    ("GET", "/api/projects/{pid}/requirement/packaging-cost"),
    ("GET", "/api/projects/{pid}/requirement/packaging-cost/items"),
    ("GET", "/api/projects/{pid}/requirement/packaging-cost/curve"),
)
#: 其中**写**的那一条：「算成本」这个动作本身。
PACKAGING_COST_BUILD_ROUTES = (
    ("POST", "/api/projects/{project_id}/requirement/packaging-cost"),
)
#: 包装行业码（与 `packaging_cost` / `packaging_match` 的 INDUSTRY 同值；只读判定用）。
PACKAGING_INDUSTRY = "packaging"
#: 「项目存在、但还没算过包装成本」的可判分支码（Spec §2.2）。HTTP 层（`main.py` 的项目 ACL
#: 中间件）把它翻成 403 + `{code, message}`；真不存在的项目仍然是 404「项目不存在」，一字不改。
COST_NOT_COMPUTED_CODE = "cost_not_computed_yet"
COST_NOT_COMPUTED_MESSAGE = (
    "项目存在，但还没有算过包装成本 —— 财务在成本算出来之前看不到它。"
    "请先让工艺经理（或你本人）算出这一版成本，算完即可见。")


def _route_matcher(template: str):
    """把路由模板编译成整段正则：`{xxx}` 只吃一个路径段，其余字面量逐字匹配。"""
    parts = []
    for chunk in re.split(r"(\{[^/{}]+\})", str(template or "")):
        if not chunk:
            continue
        if len(chunk) > 2 and chunk.startswith("{") and chunk.endswith("}"):
            parts.append("[^/]+")
        else:
            parts.append(re.escape(chunk))
    return re.compile("^" + "".join(parts) + "$")


# 模块导入期编译一次：真实请求路径里是具体 id（/parts/P-001/cost），正则按段匹配。
CONTRIBUTE_MATCHERS = tuple((method, _route_matcher(path)) for method, path in CONTRIBUTE_ROUTES)


def is_contribute_route(method: str, path: str) -> bool:
    """这次请求是不是白名单里的专属业务动作（决定 ACL 用 contribute 还是 write）。"""
    verb = _text(method).upper()
    target = str(path or "")
    return any(row_method == verb and matcher.match(target)
               for row_method, matcher in CONTRIBUTE_MATCHERS)

PACKAGING_COST_MATCHERS = tuple((method, _route_matcher(path))
                                 for method, path in PACKAGING_COST_ROUTES)
PACKAGING_COST_BUILD_MATCHERS = tuple((method, _route_matcher(path))
                                      for method, path in PACKAGING_COST_BUILD_ROUTES)


def _matches(method: str, path: str, matchers) -> bool:
    verb = _text(method).upper()
    target = str(path or "")
    return any(row_method == verb and matcher.match(target)
               for row_method, matcher in matchers)


def is_packaging_cost_route(method: str, path: str) -> bool:
    """这次请求是不是包装成本那四条路由之一（含读与写）。"""
    return _matches(method, path, PACKAGING_COST_MATCHERS)


def is_packaging_cost_build_route(method: str, path: str) -> bool:
    """这次请求是不是「算一次包装成本」这个动作本身（POST）。"""
    return _matches(method, path, PACKAGING_COST_BUILD_MATCHERS)


def _industry_of(project_id: str) -> str:
    """需求单里的行业（只读；读不到给空串，绝不抛）。"""
    if not project_id:
        return ""
    try:
        doc = store.load_requirement(project_id) or {}
    except Exception:              # noqa: BLE001 - 读不出来按「不是包装」处理
        return ""
    data = doc.get("data") if isinstance(doc.get("data"), dict) else {}
    return _text(data.get("industry")) or _text(doc.get("industry"))


def packaging_cost_action_basis(user: dict, project_id: str, action=None,
                                meta: dict = None) -> bool:
    """「算一次包装成本」这条动作的**动作级** ACL 依据（Spec §2.1 方案 A）。纯读。

    财务算包装成本是通用流程本来的口径（`auth.COST_ROLES` / `main.py` 的 `can_cost` 一直这么
    告诉前端），但这个项目的**读**依据是「成本算出来才可见」—— 于是「第一次算成本」这件事自己
    把自己挡在门外（34 实测：财务点测算 → 404「项目不存在」）。
    这里按 Spec 的要求逐条放行**这一个动作**：财务 + 项目存在 + 未归档 + 行业是包装。
    只放行这个动作：不给项目读权（`can_read` 一字不改），更不给写权（`can_write` 一字不改）。
    """
    if not _is_finance(user):
        return False
    method, path = action if action else (None, None)
    if not is_packaging_cost_build_route(method, path):
        return False
    if not project_id:
        return False
    meta = store.load_meta(project_id) if meta is None else meta
    if not meta or meta.get("deleted_at"):
        return False
    return _industry_of(project_id) == PACKAGING_INDUSTRY


def packaging_cost_state_code(user: dict, project_id: str, action=None,
                              meta: dict = None) -> str:
    """§2.2 的可判分支码：**只有**「财务 + 包装成本动作 + 包装项目 + 项目存在未归档 +
    成本还没算过」这一种组合才拿到它自己的码；其余组合一律沿用既有的 not_found / forbidden，
    行为一个字不变（不泄露其它项目、其它行业的存在性）。纯读。"""
    if not _is_finance(user) or not project_id:
        return ""
    method, path = action if action else (None, None)
    if not is_packaging_cost_route(method, path):
        return ""
    meta = store.load_meta(project_id) if meta is None else meta
    if not meta or meta.get("deleted_at"):
        return ""
    if _industry_of(project_id) != PACKAGING_INDUSTRY:
        return ""
    if _packaging_cost_visible(project_id):
        return ""
    return COST_NOT_COMPUTED_CODE


# 读全部技术项目：经理 / 总监 / 校核 / 总经理 / 管理员。写全部只有工艺经理与管理员，
# 与 auth.can_edit_project 的经理口径一致（总监能审能发，但不因此获得项目级写权）。
READ_ALL_ROLES = {"admin", "process_manager", "process_director", "reviewer",
                  "general_manager"}
WRITE_ALL_ROLES = {"admin", "process_manager"}
# 跨系统的两个角色码：技术工艺侧的 ROLE_MAP 会把 sales_mgr 压成 viewer，只有同时看
# cpq_role_code 才认得出「他是销售经理 / 财务经理」，来源报价 / 成本任务的关联才判得出来。
SALES_ROLE_CODES = {"sales_mgr", "sales_manager", "sales_director"}
FINANCE_ROLE_CODES = {"finance_mgr", "finance_manager"}
# 参与者来源 → 谁由此获得只读可见性。
QUOTE_OWNER_SOURCE = "quote_owner"
COST_TASK_SOURCE = "cost_task_assignee"
PARTICIPANT_SOURCES = ("manual", QUOTE_OWNER_SOURCE, COST_TASK_SOURCE)

NOT_FOUND_MESSAGE = "项目不存在"
FORBIDDEN_MESSAGE = "你的角色只能查看该项目，不能修改"
# 已领取这条待办、但项目本身对本人不可读（例如已归档）时用这句（Spec §3.4）：
# 「已领取」不能用 404 藏起来，要如实说清是无权。
CLAIMED_FORBIDDEN_MESSAGE = "这个项目当前不可访问：你领取的任务已失效或项目已归档"


class ProjectAccessError(Exception):
    """项目级判定的业务错误：code ∈ {not_found, forbidden}，str(exc) 至少含 message。"""

    def __init__(self, code: str, message: str = ""):
        self.code = str(code or "")
        self.message = str(message or "").strip() or NOT_FOUND_MESSAGE
        super().__init__(self.message)


def _text(value) -> str:
    return str(value or "").strip()


def _username(user: dict) -> str:
    return _text((user or {}).get("username"))


def effective_roles(user: dict) -> set:
    """身份口径的并集：技术工艺角色 ∪ CPQ 角色码 ∪ CPQ 角色码映射后的技术角色。"""
    user = user or {}
    roles = set()
    for value in (user.get("role"), user.get("cpq_role_code")):
        text = _text(value)
        if text:
            roles.add(text)
    mapped = cpq_sso.ROLE_MAP.get(_text(user.get("cpq_role_code")))
    if mapped:
        roles.add(mapped)
    return roles


def _tech_role(user: dict) -> str:
    """技术工艺口径的角色（可见范围以它为准；cpq_role_code 只用于跨系统身份识别）。"""
    return _text((user or {}).get("role"))


def _cpq_role(user: dict) -> str:
    return _text((user or {}).get("cpq_role_code"))


def _read_all(user: dict) -> bool:
    return _tech_role(user) in READ_ALL_ROLES


def _write_all(user: dict) -> bool:
    return _tech_role(user) in WRITE_ALL_ROLES


def _is_sales(user: dict) -> bool:
    return _tech_role(user) in {"sales_manager", "sales_director"} or \
        _cpq_role(user) in SALES_ROLE_CODES


def _is_finance(user: dict) -> bool:
    return _tech_role(user) == "finance_manager" or _cpq_role(user) in FINANCE_ROLE_CODES


def _finance_handoff_visible(project_id: str) -> bool:
    """财务角色池依据（Spec §18.3）：项目存在**有效的财务交接**。

    判据是项目状态本身（`plan.finance_handoff` 非空且 sent_at / task_id 有值），不是
    具体领取人 —— CPQ 的任务派给角色池，领取人在报价侧决定，绑到人会在换班时变成
    404。纯读：不写项目、不写参与者、不产生审计。
    """
    if not project_id:
        return False
    from . import integration      # 函数级延迟 import：避开 services 之间的导入顺序问题
    try:
        plan = integration.load_plan(project_id)
    except Exception:              # 读不出来（老项目 / 数据损坏）按「没有交接」处理
        return False
    handoff = getattr(plan, "finance_handoff", None)
    if not handoff:
        return False
    return bool(_text(getattr(handoff, "sent_at", "")) or _text(getattr(handoff, "task_id", "")))


def _quote_link_visible(project_id: str) -> bool:
    """销售角色池依据（Spec §18.3）：项目存在**有效的来源报价关联**。纯读。"""
    if not project_id:
        return False
    try:
        case = store.load_business_case(project_id) or {}
    except Exception:              # 同上：读不出来按「没有关联」处理
        return False
    return bool(_text(case.get("quote_session_id")) or _text(case.get("source_task_id")))


def _packaging_cost_visible(project_id: str) -> bool:
    """财务角色池依据（Spec `packaging-cost-finance-access.md` §2.1）：项目**已经算出包装成本**。

    判据是项目状态本身（成本记录 built），不是具体领取人 —— 包装链路没有「送财务」这一步、
    也不写 `plan.finance_handoff`，而全仓 `store.add_participant` 的生产调用点为 0；于是
    「钱已经算出来了，做成本的人却看不到项目」。成本记录本身就是关联事实，这不是放宽权限，
    是把断掉的链路接回来。纯读：不写项目、不写参与者、不产生审计。

    只给**可见性**：不给写权（`can_write` / `can_contribute` 一字不改）；归档项目不因它解锁
    （调用点在 `can_read` 的归档判断之后）。
    """
    if not project_id:
        return False
    from . import packaging_cost   # 函数级延迟 import：避开 services 之间的导入顺序问题
    try:
        cost = packaging_cost.load_cost(project_id) or {}
    except Exception:              # 读不出来（老项目 / 数据损坏）按「没算过」处理
        return False
    return bool(cost.get("built"))


def claimed_task_project_access(project_id: str, user: dict) -> bool:
    """财务「领取人」依据（Spec `e2e-packaging-downstream-handoff-report.md` §3.2）。纯读。

    与 `_finance_handoff_visible()` 的区别：那条判的是「项目有没有送过财务」（角色池），
    这条判的是「这条待办到底归谁领」—— 领取人在报价侧才确定，技术项目这一侧由
    `cost_flow.grant_task_project_access()` 在领取/创建任务时落一条 `cost_task_assignee`
    参与者与一条任务记录。有了它，FI 领取正确任务后打开成本工作台不再是
    「项目不存在」，而是**能打开**（Spec §3.2 / §6 PE1）。

    不写项目、不写参与者、不产生审计；读不到一律按「没领过」处理。
    """
    name = _username(user)
    if not project_id or not name:
        return False
    from . import cost_flow      # 函数级延迟 import：避开 services 之间的导入顺序问题

    try:
        return bool(cost_flow.claimed_task_by(project_id, name))
    except Exception:            # noqa: BLE001 - 读不出来按「没领过」处理
        return False


def _participants(meta: dict) -> List[dict]:
    rows = (meta or {}).get("participants") or []
    return [row for row in rows if isinstance(row, dict)]


def _owner(meta: dict) -> str:
    return _text((meta or {}).get("owner")) or "system"


def _holder(meta: dict) -> str:
    return _text((meta or {}).get("current_holder")) or _owner(meta)


def _has_source(meta: dict, username: str, source: str) -> bool:
    if not username:
        return False
    return any(_text(row.get("username")) == username and _text(row.get("source")) == source
               for row in _participants(meta))


def mine_sources(user: dict, meta: dict) -> List[str]:
    """我的清单的四类来源，按 owner / holder / assigned / participant 顺序去重。"""
    username = _username(user)
    if not username or not meta:
        return []
    owner = _owner(meta)
    holder = _holder(meta)
    rows = _participants(meta)
    sources: List[str] = []
    if owner == username:
        sources.append("owner")
    if holder == username:
        sources.append("holder")
    assigned = any(_text(row.get("username")) == username and bool(row.get("assignee"))
                   for row in rows)
    if assigned or (holder == username and username != owner):
        sources.append("assigned")
    if any(_text(row.get("username")) == username for row in rows):
        sources.append("participant")
    return sources


def can_read(user: dict, meta: dict) -> bool:
    """能不能看到这个项目。归档项目对非 owner 一律按不存在处理。"""
    if not meta:
        return False
    if _read_all(user):
        return True
    sources = mine_sources(user, meta)
    if meta.get("deleted_at"):
        # 归档：工程师仅「我创建」的可读；销售 / 财务 / 其它不可见（Spec §5/§6）。
        return "owner" in sources
    if sources:
        return True
    if _is_sales(user) and _has_source(meta, _username(user), QUOTE_OWNER_SOURCE):
        return True
    project_id = _text(meta.get("project_id"))
    if _has_source(meta, _username(user), COST_TASK_SOURCE):
        return True
    # 领取人依据（Spec §6 PE1）：这条财务待办是本人领的 —— 角色池判的是"项目送过财务"，
    # 这里判的是"这条任务归我"，两者都要。
    if project_id and claimed_task_project_access(project_id, user):
        return True
    # 角色池（Spec §18.3）：按**项目状态**判定，不绑定具体领取人。放在归档判断之后 ——
    # 归档项目不因关联而解锁。
    if _is_finance(user) and _finance_handoff_visible(project_id):
        return True
    if _is_finance(user) and _packaging_cost_visible(project_id):
        return True
    if _is_sales(user) and _quote_link_visible(project_id):
        return True
    return False


def can_write(user: dict, meta: dict) -> bool:
    """能不能碰这个项目：经理 / 管理员全部，工程师仅本人，其余只读；归档一律只读。"""
    if not meta or meta.get("deleted_at"):
        return False
    if _write_all(user):
        return True
    return bool(auth.can_edit_project(user, meta))


def can_contribute(user: dict, meta: dict) -> bool:
    """能不能执行专属业务动作：**可见 + 未归档**，不看项目级写权（Spec §18.4）。

    可见性不自动等于写权限，反过来「角色不够做这一步」也不该由 ACL 提前说成
    「你只能查看该项目」—— 专属动作的角色口径归接口自己的 _require。
    """
    if not meta or meta.get("deleted_at"):
        return False
    return bool(can_read(user, meta))


def can_access(project_id: str, user: dict, mode: str = "read") -> bool:
    meta = store.load_meta(project_id)
    if not meta:
        return False
    return can_write(user, meta) if mode == "write" else can_read(user, meta)


def require_project_access(project_id: str, user: dict, mode: str = "read", *,
                           action=None) -> dict:
    """项目级唯一判定：通过返回 meta；不可见 / 归档要写 -> not_found；可见但改不动 -> forbidden。

    not_found 的文案与「项目确实不存在」逐字相同（HTTP 层统一映射 404「项目不存在」），
    避免从响应里反推出「它其实存在只是我没权限」。

    `action=(method, path)`：动作级判定的补充依据（Spec
    `packaging-cost-write-role-single-source.md` §2.1/§2.2）——包装成本那一条动作，
    财务在「成本还没算过」时也允许执行；读它的时候则给**可判的状态码**而不是 404。
    不传 action 的老调用点行为逐字不变。
    """
    meta = store.load_meta(project_id)
    if not meta:
        raise ProjectAccessError("not_found", NOT_FOUND_MESSAGE)
    # 「财务算包装成本」这条动作的依据（Spec §2.1 方案 A）：它只对那一个 POST 成立（GET 一律
    # 不认），且自带「项目存在 + 未归档 + 行业是包装」三个前提。先算出来，是因为它要在**未算过**
    # （项目还读不到）与**算过之后**（项目能读、但项目级写权仍不给财务）两种状态下给同一个答案 ——
    # 否则财务只能算第一版、重算又被挡，等于把两套口径换个方向再来一次。
    build_basis = packaging_cost_action_basis(user, project_id, action, meta)
    if not can_read(user, meta):
        if build_basis:
            return meta
        code = packaging_cost_state_code(user, project_id, action, meta)
        if code:
            raise ProjectAccessError(code, COST_NOT_COMPUTED_MESSAGE)
        # 真不存在才 404；**已领取但不可读**（归档等）如实给 403（Spec §3.4）——
        # 用「项目不存在」藏起一条自己领过的待办，用户只会以为数据丢了。
        if claimed_task_project_access(project_id, user):
            raise ProjectAccessError("forbidden", CLAIMED_FORBIDDEN_MESSAGE)
        raise ProjectAccessError("not_found", NOT_FOUND_MESSAGE)
    if mode == "contribute":
        # 归档对角色池一律按不存在处理；否则放行（角色判断留给接口自己）。
        if meta.get("deleted_at"):
            raise ProjectAccessError("not_found", NOT_FOUND_MESSAGE)
        return meta
    if mode == "write":
        if meta.get("deleted_at"):
            raise ProjectAccessError("not_found", NOT_FOUND_MESSAGE)
        # 命中动作级依据的那一条动作不按项目级写权判：`can_write()` 一字不改（财务仍然不是这个
        # 项目的"能改的人"），只是「算一次包装成本」不属于"改项目"这个动作。
        if not can_write(user, meta) and not build_basis:
            raise ProjectAccessError("forbidden", FORBIDDEN_MESSAGE)
    return meta


def visible_projects(user: dict, scope: str = "mine", include_archived: bool = False) -> List[dict]:
    """列表判定：mine / all / todo / archived。

    每项仍附 access = {scope, mine_sources, can_read, can_write}（批次 7），并新增
    card 块（批次 10A）—— 卡片上的「谁的项目 / 第几步 / 在等谁 / 最后一次业务事件 /
    有没有异常 / 主操作」全部由后端算，前端只渲染。排序按 card.last_event.at 降序 →
    updated_at 降序 → project_id 升序（稳定）。
    """
    scope = _text(scope) or "mine"
    if scope not in SCOPES:
        raise ValueError(f"scope 取值不合法：{scope}")
    out: List[dict] = []
    for meta in store.list_projects(include_archived=True):
        archived = bool(meta.get("deleted_at"))
        if scope == "archived":
            if not archived or not can_read(user, meta):
                continue
        else:
            # 「待办」永远不含归档；mine / all 沿用既有的 include_archived 语义。
            if archived and (scope == "todo" or not include_archived):
                continue
            if not can_read(user, meta):
                continue
            if scope == "mine" and not mine_sources(user, meta):
                continue
        project_id = _text(meta.get("project_id"))
        card = home_card.build_card(project_id, meta, user)
        if scope == "todo":
            # 只列「我此刻能动手」的：有可执行动作，且当前子步骤对本人 actionable。
            if not card.get("primary_action") or not home_card.current_stage_actionable(project_id, user):
                continue
        entry = dict(meta)
        entry["access"] = {
            "scope": scope,
            "mine_sources": mine_sources(user, meta),
            "can_read": can_read(user, meta),
            "can_write": can_write(user, meta),
        }
        entry["card"] = card
        out.append(entry)
    return home_card.sort_entries(out)


def scope_of(user: dict) -> str:
    """给界面说明「你看到的是我的 / 全部」。"""
    return "all" if _read_all(user) else "mine"

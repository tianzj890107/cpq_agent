# -*- coding: utf-8 -*-
"""数据集的语义契约检查（结构之外的部分）。

schema 只保证「形状」；这里保证「业务口径」：
  · 五阶段 13 子步骤 / 报价六步口径（禁止旧编号与「九阶段」表述）；
  · 金额只用 decimal 字符串，禁止浮点模糊比较；
  · 时间只校验格式 / 顺序 / 窗口，禁止写死当前时间；
  · P0 必须含反例或失败路径；
  · 写操作必须声明幂等键或重复执行期望；
  · 权限案例必须同时覆盖允许角色与拒绝角色；
  · 回传案例必须校验源项目 / 源任务 / 业务实例号，防串项目；
  · 模型输出只断言结构化工具调用，不断言逐字文本；
  · 禁止真实密钥、生产地址、缓存与运行产物。

每个注册表（``ACTION_OPS`` / ``INVARIANTS`` / ``FORBIDDEN_CHECKS`` / ``UI_PROTOCOL``）
既是契约也是文档：``runner --list`` 会打印它们，``--validate`` 会检查案例只引用已注册的名字。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import (
    DOMAINS, EXECUTORS, FAILURE_TYPES, LAYERS, LEGACY_PATTERNS, PHASES, PRIORITIES,
    PRODUCTION_EXECUTORS, QUOTE_ROLE_BY_STEP, QUOTE_STEPS, ROOT, ROLES, STAGE_BY_SUB,
    STAGE_IDS, SUB_STEPS, find_legacy_wording,
)

# --------------------------------------------------------------------------- #
# 注册表：动作 / 不变量 / 禁止项 / UI 协议
# --------------------------------------------------------------------------- #
ACTION_OPS = {
    # 环境与登录
    "seed_actor": "登记一个账号（角色集合、token、api_key=test-key、模型）",
    "login": "用账号登录，拿到统一登录态（报价与技术工艺共享）",
    "login_with_token": "用显式 token 登录（空 / 过期 / 错误 token 的失败路径）",
    "set_api_key": "给账号设置 api key 与模型（按账号隔离）",
    "read_models": "读取当前账号可见的模型设置",
    # 报价六步
    "create_quote_card": "建报价卡片（session_id / card_id / business_case_id / owner）",
    "quote_parse_requirement": "第 1 步：一键解析需求文档，左侧输出 + 右侧看板同步填充",
    "quote_edit_field": "改字段（带来源：ai_recommended / manual / system）",
    "quote_match_product": "第 1 步：按参数匹配标品，返回匹配分数",
    "quote_complete_step": "完成某一步（角色、单调前进、幂等）",
    "quote_edit_step": "回改已完成步骤并触发重算 / 版本",
    "quote_generate_plans": "第 5 步：生成多方案",
    "quote_select_plan": "第 5 步：选中方案并重算",
    "quote_output_document": "第 6 步：输出报价单（版本、下载）",
    "quote_handoff_step": "第 2 步工艺确认：把卡片转交工艺经理",
    "quote_send_tech_task": "第 1 步找不到标品：发「新增工艺」技术任务（支线，不推进）",
    "quote_snapshot": "读报价卡片快照（状态 / 字段 / 金额 / 版本）",
    # 技术工艺 13 子步骤
    "tech_create_project": "创建技术项目（owner / 五阶段口径 page_context）",
    "tech_open_project": "打开项目（可从报价任务 / 首页卡片 / 历史入口）",
    "tech_requirement_create": "1.1 创建需求：提交需求单（草稿不算完成）",
    "tech_requirement_confirm": "1.2 确认需求：完整性检查 + 人工确认（可带 waiver）",
    "tech_requirement_review": "1.3 审核需求：通过 / 退回",
    "tech_drawing_parse": "2.1 图纸解析：解析出零件 / 0 零件需人工确认",
    "tech_drawing_confirm_empty": "2.1 0 零件时的人工「确认无零件」动作",
    "tech_part_open": "在右侧看板内点开零件（2D/3D），不落到父壳上层",
    "tech_integration_run": "3.1 整合图纸",
    "tech_params_recommend": "3.2 参数推荐（缺口 / 必填 / waiver；不得落在成本测算）",
    "tech_params_confirm": "3.2 参数确认（人工确认才算完成）",
    "tech_process_assemble": "3.3 组装工艺：生成工序",
    "tech_process_confirm": "3.3 工艺确认（只生成工序不算完成）",
    "tech_cost_parts": "4.1 零件成本（排除零件必须有 excluded_parts）",
    "tech_cost_assembly": "4.2 组装成本",
    "tech_cost_total": "4.3 汇总金额",
    "tech_cost_confirm": "4.3 财务正式确认（角色门禁）",
    "tech_summary_save": "5.1 汇总结果：出报告草稿",
    "tech_report_review": "5.2 结果审核：通过 / 退回",
    "tech_report_publish": "5.3 发布报告（已发布 ≠ 已回传）",
    "tech_report_handback": "5.3 回传报价（发布 + 回传才算完整完成）",
    "tech_edit_upstream": "改上游数据，下游结果变 stale",
    "tech_refresh": "刷新 / 重取数据（失败时保留上次状态）",
    "tech_projection": "取五阶段 13 子步骤统一投影（状态 / 可看 / 可做 / 阻塞原因）",
    "seed_defaults": "登记默认演示账号与项目（alice/carol/dave/erin/frank/grace 等）",
    "acl_list": "按我的 / 全部 / 已归档范围列出有权限的项目",
    "acl_access": "项目级读写判定（读 / 写 / 归档 / 不存在 / 越权）",
    "acl_matrix_check": "按 input.access_matrix 逐角色核对允许 / 拒绝（不可枚举）",
    "load_fixture_state": "从 fixture 装载历史状态（legacy 卡片 / 旧 page_context / 中断任务）",
    "service_restart_recover": "服务重启后恢复中断任务（不新建重复项目 / 任务 / 会话）",
    "tech_timeline_read": "读取项目会话时间线（消息 / 工具轨迹 / 看板事件按同一顺序）",
    # 任务流
    "task_send": "派发任务（同类复用 / 换人替代 / 其他类型不受影响）",
    "task_claim": "领取任务（原子领取）",
    "task_claim_concurrent": "并发领取（barrier + 线程，唯一裁决）",
    "task_complete": "完成任务（重复完成不重复写消息 / 审计 / 快照）",
    "task_cancel": "取消任务（终态、幂等、不可再领）",
    "task_cancel_vs_complete_concurrent": "取消与完成并发竞争",
    "task_list": "待办 / 我的清单",
    # 跨 Agent 回传
    "handoff_send": "回传报价（五元组幂等键 + 单事务）",
    "handoff_send_concurrent": "并发回传（只有一次产生副作用）",
    "handoff_send_failing": "注入失败的回传（必须整体回滚）",
    "case_link_decide": "按 business_case_id / source_task_id / session 判定落点（真实纯函数）",
    "material_write": "写主数据（业务版本幂等）",
    "material_write_concurrent": "并发写主数据（唯一约束裁决）",
    # 会话与历史
    "session_append_event": "追加会话时间线事件（消息 / 工具轨迹 / 看板卡）",
    "session_restore": "从入口恢复会话（首页卡片 / 历史抽屉 / 刷新）",
    "session_rebind_project": "项目重新绑定（不串会话）",
    "page_context_open": "按 page_context 打开步骤（旧值映射到新口径显示）",
    # 模型与工具协议
    "llm_call": "回放固定 provider 响应（test-key，不访问真实模型）",
    # 故障与 UI 协议
    "http_call": "调用接口并断言状态码 / 稳定错误码 / trace_id",
    "board_action": "看板业务动作（失败时左右两侧一致）",
    "retry_action": "失败重试（复用幂等键，不重复副作用）",
    "ui_board_sync": "看板渲染（字段来源徽标 / 子步骤在板内展开 / 主色）",
    "ui_error_banner": "失败提示（关键失败常驻、非关键不制造永久错误卡）",
    "ui_composer": "输入区布局（模型按钮位置 / 报价与技术输入框底部对齐）",
}

# 会写库 / 写文件 / 写会话的动作：这些案例必须声明幂等键或重复执行期望。
WRITE_OPS = frozenset({
    "quote_parse_requirement", "quote_edit_field", "quote_complete_step", "quote_edit_step",
    "quote_generate_plans", "quote_select_plan", "quote_output_document", "quote_handoff_step",
    "quote_send_tech_task", "tech_create_project", "tech_requirement_create",
    "tech_requirement_confirm", "tech_requirement_review", "tech_drawing_parse",
    "tech_drawing_confirm_empty", "tech_integration_run", "tech_params_recommend",
    "tech_params_confirm", "tech_process_assemble", "tech_process_confirm", "tech_cost_parts",
    "tech_cost_assembly", "tech_cost_total", "tech_cost_confirm", "tech_summary_save",
    "tech_report_review", "tech_report_publish", "tech_report_handback", "tech_edit_upstream",
    "task_send", "task_claim", "task_claim_concurrent", "task_complete", "task_cancel",
    "task_cancel_vs_complete_concurrent", "handoff_send", "handoff_send_concurrent",
    "handoff_send_failing", "material_write", "material_write_concurrent",
    "session_append_event", "session_rebind_project", "llm_call", "retry_action", "board_action",
})

INVARIANTS = {
    "no_cross_project_leak": "不同 project / session / card / task 之间不串数据",
    "step_monotonic": "报价 current_step 只前进不倒退；技术子步骤不因晚到的回传倒退",
    "single_source_of_truth": "同一事实只有一个写入源（步骤状态 / 项目身份 / 会话时间线）",
    "no_duplicate_side_effect": "重复请求不重复产生任务 / 消息 / 审计 / 快照 / 物料编码",
    "atomic_claim": "领取由原子更新或唯一约束裁决，不是先 SELECT 再 INSERT",
    "transaction_all_or_nothing": "一次回传 = 一个事务，失败整体回滚",
    "money_is_decimal": "金额一律 decimal 字符串，禁止浮点比较",
    "time_is_relative": "时间只校验格式 / 顺序 / 窗口，不写死当前时间",
    "left_right_consistent": "左侧会话与右侧看板最终一致",
    "structured_tool_call": "模型输出只断言结构化工具调用与业务结果",
    "deny_is_not_enumerable": "未授权与不存在返回不可枚举的等价响应",
    "legacy_compatible": "stage id / URL / 历史字段不变，历史消息原文不改写",
}

FORBIDDEN_CHECKS = {
    "duplicate_side_effect": "重复请求产生了第二个任务 / 消息 / 审计 / 快照 / 编码",
    "unsafe_claim": "非原子领取：两人同时领取成功，或 cancelled / completed 任务被再次领取",
    "step_rollback": "current_step 被回传或回改动作写回更小值",
    "stale_written_as_fresh": "上游已改，下游旧结果仍被当成最新写入",
    "cross_project_leak": "数据落到别的 project / session / card / task",
    "denied_actor_allowed": "被拒绝的角色实际拿到了权限",
    "generic_error_body": "错误响应只有泛化文案，没有稳定错误码 / trace_id",
    "silent_new_card": "认不回原卡片时静默新建报价卡片",
    "unsaved_on_failure": "失败路径留下了半写数据",
    "timing_race_based": "用 sleep / 串行模拟代替真实并发裁决",
    "zero_for_excluded_part": "排除零件按 0 元处理，而不是写 excluded_parts",
    "completed_without_confirm": "未确认的数据被标记为已完成",
    "published_without_handback_counted_complete": "已发布未回传被算成 5.3 完成",
    "verbatim_model_text": "断言模型逐字文本",
    "float_money": "金额使用浮点数",
    "legacy_wording": "出现九阶段 / 2.2 / 2.3 / 3.x 报告等旧口径",
    "real_secret_used": "使用真实 API key / token / 生产地址",
    "production_host_called": "deterministic / recorded_provider 层访问了生产或准生产地址",
    "refresh_cleared_state": "刷新失败把已有状态清空成未完成",
}

UI_PROTOCOL = {
    "board_left_right_sync": "一键解析后左侧输出与右侧看板同步填充（同一次动作，不刷新整页）",
    "field_source_badge": "字段带来源徽标：ai_recommended / manual / system",
    "part_detail_in_board": "零件详情在看板内部展开，不在父壳上层弹出",
    "params_tab_phase": "3.2 参数推荐页签属于「3 组装与整合」，不出现在「4 成本测算」",
    "single_primary_action": "每步只有一颗主按钮",
    "error_surface": "错误面：角色提示只在看板，不重复插到顶部会话",
    "brand_color": "系统主色（蓝）用于状态提示",
    "model_button_in_composer": "输入区内的模型按钮已移除（全局设置入口保留）",
    "composer_bottom_aligned": "报价与技术工艺输入框底部对齐",
    "critical_failure_persistent": "关键失败常驻显示",
    "quiet_failure_no_card": "非关键失败不制造永久错误卡",
    "error_code_with_trace": "API 固定错误码 + trace_id 同时出现在响应体与响应头",
    "refresh_failure_keeps_state": "刷新失败保留上次状态并提示，不清空",
    "monotonic_ui_stage": "页面显示的 stage 不回退（历史回传只合并快照）",
}

# --------------------------------------------------------------------------- #
# 逐案例检查
# --------------------------------------------------------------------------- #
_MONEY_KEY = re.compile(r"(^|_)(amount|price|subtotal|cost|total|margin|charge|discount|rate|excise|tax)($|_)")
_MONEY_EXEMPT = re.compile(r"_(count|rows|runs|versions|n)$")
# 计数型键（``phases_total`` / ``cost_parts`` / ``messages_total`` …）与金额无关。
_COUNT_PREFIX = ("phases", "stages", "messages", "tasks", "events", "cards", "parts", "items",
                 "records", "calls", "steps", "projects", "actors", "handoffs", "documents",
                 "versions", "material", "cost", "quote", "tech", "acl", "session", "llm",
                 "requirement", "drawing", "integration", "report", "concurrency", "restore",
                 "open", "projections", "handoff", "tasks_sent", "tasks_reused")
_COUNT_TAIL = ("total", "count", "parts", "rows", "runs", "versions", "n", "size", "length")


def _is_count_key(key) -> bool:
    for text in (str(key), str(key).rsplit(".", 1)[-1]):
        for prefix in _COUNT_PREFIX:
            if text.startswith(prefix + "_"):
                tail = text[len(prefix) + 1:]
                if tail in _COUNT_TAIL:
                    return True
    return False
_DECIMAL = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")
_TIME_LITERAL = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ]")
_TIME_ASSERT_KEYS = frozenset({"format", "window", "within", "between", "from", "to",
                               "after", "before", "order", "duration"})
_RUNTIME_ID = re.compile(r"(19|20)[0-9]{2}[01][0-9][0-3][0-9]|[0-9]{6,}")
_SECRETS = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{24,}"),
    re.compile(r"(?i)(api[_-]?key|password|passwd|secret)\s*[:=]\s*[\"']?[A-Za-z0-9._-]{12,}"),
    re.compile(r"postgres(?:ql)?://[^\s\"']+"),
)
_PROD_HOSTS = re.compile(r"(172\.16\.10\.34|:8010\b|\bpdt\b|prod\.)", re.I)
_NEGATIVE_TAGS = frozenset({
    "negative", "deny", "permission", "rollback", "conflict", "concurrency", "duplicate",
    "idempotent", "timeout", "error", "failure", "stale", "recovery", "refresh",
})
_LINKAGE_TOKENS = ("source_project_id", "source_task_id", "business_case_id",
                   "source_quote_card", "source_session_id", "target_quote_session_id")


def _errors(errors, case, message):
    errors.append(f"{case.case_id or '<无 id>'}（{case.rel_path}）：{message}")


# --------------------------------------------------------------------------- #
# source_specs 的真实入口引用（Spec 之外还要指向真实模块 / 真实路由）
#
# 契约：``source_specs`` 的每一项要么是仓库里存在的文档 / 代码路径，要么是
#   · ``module:<dotted.path>``  —— 指向真实生产模块文件（静态解析，不 import）；
#   · ``route:<METHOD> <path>`` —— 指向真实 FastAPI 路由（现算 main.py 的 AST）。
# production-backed 的 P0 案例必须至少有一条这样的真实引用 —— 只写文档无法证明
# 这条案例真的打在哪个函数 / 哪条路由上。
# --------------------------------------------------------------------------- #
_REAL_MODULE_PREFIX = "module:"
_REAL_ROUTE_PREFIX = "route:"
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


def real_module_file(dotted: str):
    """``backend.services.project_access`` → 真实模块文件路径；不存在返回 None。"""
    name = str(dotted or "").strip()
    for prefix in ("tech_app.",):
        if name.startswith(prefix):
            name = name[len(prefix):]
    parts = [part for part in name.split(".") if part]
    if not parts:
        return None
    relative = Path(*parts)
    candidates = (
        ROOT / relative.with_suffix(".py"),
        ROOT / relative / "__init__.py",
        ROOT / "tech_app" / relative.with_suffix(".py"),
        ROOT / "tech_app" / relative / "__init__.py",
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def real_route_signatures() -> set:
    """现算的真实路由签名集合（``GET /api/...``），不 import 生产模块。"""
    from . import prodkit

    return set(prodkit.routes().signature())


def real_source_refs(case) -> list:
    """案例 ``source_specs`` 里的真实入口引用（``module:`` / ``route:``）。"""
    raw = case.raw if hasattr(case, "raw") else case
    return [str(ref) for ref in (raw.get("source_specs") or [])
            if str(ref).startswith(_REAL_MODULE_PREFIX) or str(ref).startswith(_REAL_ROUTE_PREFIX)]


def real_source_ref_errors(case) -> list:
    """真实入口引用必须指向真实存在的模块 / 路由。"""
    errors = []
    signatures = None
    for ref in real_source_refs(case):
        if ref.startswith(_REAL_MODULE_PREFIX):
            name = ref[len(_REAL_MODULE_PREFIX):].strip()
            if not real_module_file(name):
                errors.append(f"source_specs 的 {ref} 不是真实生产模块（找不到文件）")
        else:
            text = ref[len(_REAL_ROUTE_PREFIX):].strip()
            method, _, path = text.partition(" ")
            if str(method).upper() not in _METHODS or not str(path).startswith("/"):
                errors.append(f"source_specs 的 {ref} 格式应为 route:<METHOD> /api/...")
                continue
            if signatures is None:
                signatures = real_route_signatures()
            if f"{str(method).upper()} {path}" not in signatures:
                errors.append(f"source_specs 的 {ref} 不是真实项目级路由")
    return errors


def check_case_contract(case) -> list:
    """单条案例的语义契约。返回错误列表。"""
    errors = []
    raw = case.raw
    domain = raw.get("domain")
    tags = set(raw.get("tags") or [])
    actions = raw.get("actions") or []
    expected = raw.get("expected") or {}
    ops = [str(a.get("op") or "") for a in actions if isinstance(a, dict)]

    # id 稳定性
    if _RUNTIME_ID.search(case.case_id):
        _errors(errors, case, "id 含日期 / 长数字序列，疑似按运行时间生成（id 必须稳定）")

    # source_specs 存在（module: / route: 是真实入口引用，由 real_source_ref_errors 校验）
    for ref in raw.get("source_specs") or []:
        text = str(ref)
        if text.startswith(_REAL_MODULE_PREFIX) or text.startswith(_REAL_ROUTE_PREFIX):
            continue
        if ":" in text and "/" not in text.partition(":")[0]:
            _errors(errors, case, f"source_specs 的 {text} 不是已知引用（只认文件路径 / "
                                  f"module: / route:）")
            continue
        if not (ROOT / text).exists():
            _errors(errors, case, f"source_specs 指向的文件不存在：{text}")

    # production-backed 必须声明真实入口（module+function 或 http method+path）与 input.steps
    from . import production as production_mod

    errors.extend(f"{case.case_id}（{case.rel_path}）：{msg}"
                  for msg in production_mod.entry_errors(raw))

    # source_specs 里的真实入口引用必须真的存在
    errors.extend(f"{case.case_id}（{case.rel_path}）：{msg}"
                  for msg in real_source_ref_errors(case))

    # 已知 op / 不变量 / 禁止项 / UI 协议
    for op in ops:
        if op not in ACTION_OPS:
            _errors(errors, case, f"未知动作 op={op}（必须在 scripts/cpq_eval/checks.ACTION_OPS 注册）")
    for name in raw.get("invariants") or []:
        if name not in INVARIANTS:
            _errors(errors, case, f"未知不变量 {name}")
    for name in expected.get("forbidden") or []:
        if name not in FORBIDDEN_CHECKS and name not in production_mod.PRODUCTION_FORBIDDEN \
                and "." not in name:
            _errors(errors, case, f"未知禁止项 {name}")
    for name in expected.get("ui_protocol") or {}:
        if name not in UI_PROTOCOL:
            _errors(errors, case, f"未知 UI 协议项 {name}")
    if "sleep" in str(raw):
        _errors(errors, case, "出现 sleep：并发正确性不允许用 sleep 模拟")

    # domain 轴：五阶段 13 子步骤 / 报价六步
    if domain == "tech":
        stage, sub = raw.get("stage"), raw.get("sub_step")
        if not stage or not sub:
            _errors(errors, case, "tech 案例必须声明 stage 与 sub_step")
        elif stage not in STAGE_IDS:
            _errors(errors, case, f"stage 不在 9 个 stage id 内：{stage}")
        elif sub not in SUB_STEPS:
            _errors(errors, case, f"sub_step 不在 13 个子步骤内：{sub}")
        elif STAGE_BY_SUB[sub]["stage_id"] != stage:
            _errors(errors, case, f"stage={stage} 与 sub_step={sub} 不匹配（应 "
                                  f"{STAGE_BY_SUB[sub]['stage_id']}）")
    if domain == "quote":
        step = raw.get("quote_step")
        if step not in [row[0] for row in QUOTE_STEPS]:
            _errors(errors, case, f"quote 案例必须声明 quote_step（1..{len(QUOTE_STEPS)}）")
    if "failure_type" in raw and raw.get("failure_type") not in FAILURE_TYPES:
        _errors(errors, case, f"failure_type 不在闭集内：{raw.get('failure_type')}")

    # 旧口径文案（title / notes / tags / invariants / expected 全覆盖；input 里的历史原文允许保留）
    scanned = [raw.get("title") or "", raw.get("notes") or ""]
    scanned.extend(sorted(tags))
    scanned.extend(raw.get("invariants") or [])
    scanned.extend(_collect_strings(expected))
    scanned.extend([str(a.get("label") or "") for a in actions if isinstance(a, dict)])
    for text in scanned:
        for name in find_legacy_wording(text):
            _errors(errors, case, f"出现旧口径「{name}」：{text[:60]}")

    # 金额：decimal 字符串，禁止浮点
    for key, value in _walk(expected):
        if not _MONEY_KEY.search(str(key)) or _MONEY_EXEMPT.search(str(key)) \
                or _is_count_key(key):
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            _errors(errors, case, f"金额字段 {key} 用了数字 {value!r}（必须 decimal/string）")
        elif isinstance(value, str) and not _DECIMAL.match(value):
            _errors(errors, case, f"金额字段 {key} 的值 {value!r} 不是合法 decimal 字符串")
    if "float_money" in tags:
        _errors(errors, case, "tags 不应出现 float_money（该禁止项由本检查自动覆盖）")

    # 时间：只允许格式 / 顺序 / 窗口
    for key, value in _walk(expected):
        if isinstance(value, str) and _TIME_LITERAL.search(value) and key not in _TIME_ASSERT_KEYS:
            _errors(errors, case, f"expected 里写死了绝对时间（{key}={value[:40]}）；只允许 "
                                  "格式 / 顺序 / 窗口断言")

    # P0 必须含反例或失败路径
    if raw.get("priority") == "P0":
        has_error_expect = any(isinstance(a, dict) and a.get("expect") for a in actions)
        http_expect = expected.get("http")
        status_expect = http_expect.get("status", 200) if isinstance(http_expect, dict) else 200
        has_4xx = isinstance(status_expect, int) and status_expect >= 400
        if not (expected.get("forbidden") or has_error_expect or has_4xx
                or (tags & _NEGATIVE_TAGS)):
            _errors(errors, case, "P0 案例必须含反例或失败路径（禁止项 / 动作失败期望 / 4xx / 反例 tag）")
    if raw.get("priority") not in PRIORITIES:
        _errors(errors, case, f"priority 非法：{raw.get('priority')}")

    # 写操作必须声明幂等键或重复执行期望
    write_ops = [op for op in ops if op in WRITE_OPS]
    if write_ops:
        declared = (
            any(isinstance(a, dict) and (a.get("idempotency_key") or a.get("key")
                                         or a.get("repeat_expect")) for a in actions)
            or bool(raw.get("input", {}).get("idempotency_key"))
            or bool(raw.get("preconditions", {}).get("idempotency_key"))
            or bool(tags & {"idempotent", "duplicate", "recovery", "concurrency"})
        )
        http_expect = expected.get("http")
        status_expect = http_expect.get("status", 200) if isinstance(http_expect, dict) else 200
        rejected = any(isinstance(a, dict) and a.get("expect") for a in actions) or (
            isinstance(status_expect, int) and status_expect >= 400)
        if not declared and not rejected:
            _errors(errors, case, f"写操作 {write_ops} 未声明幂等键 / 重复执行期望")

    # 权限案例必须同时覆盖允许角色与拒绝角色
    if domain == "auth_acl":
        matrix = (raw.get("input") or {}).get("access_matrix") or {}
        values = set(matrix.values()) if isinstance(matrix, dict) else set()
        if not {"allow", "deny"} <= values:
            _errors(errors, case, "auth_acl 案例的 input.access_matrix 必须同时含 allow 与 deny")

    # 回传案例必须校验关联字段
    if domain == "cross_agent" and any("handoff" in op for op in ops):
        blob = str(expected.get("state") or {}) + str(expected.get("records") or {}) \
            + str(expected.get("forbidden") or []) + str(actions)
        if not any(token in blob for token in _LINKAGE_TOKENS):
            _errors(errors, case, "回传案例必须校验 source_project_id / source_task_id / "
                                  "business_case_id 等关联字段，防串项目")

    # 模型输出只断言结构化工具调用
    if domain == "llm_contract":
        assertions = str(expected.get("state") or {}) + str(expected.get("records") or {})
        if "tool" not in assertions:
            _errors(errors, case, "llm_contract 案例必须断言结构化工具调用（expected 里出现 tool）")
        for _key, value in _walk(expected.get("messages") or []):
            if isinstance(value, str) and len(value) > 120:
                _errors(errors, case, "llm_contract 案例出现了超长逐字文本断言（>120 字符）")
        declared_provider = any(isinstance(a, dict) and a.get("provider") for a in actions) or \
            bool((raw.get("input") or {}).get("provider"))
        if not declared_provider:
            _errors(errors, case, "llm_contract 案例必须引用 recorded provider fixture")
        replay = str((raw.get("input") or {}).get("provider_replay") or "")
        if production_mod.executor_of(raw) != "recorded_provider":
            # 允许显式声明「fixture 无法真实回放」的规格案例（由 Sim 回放），但必须写明原因，
            # 且这类案例**不计入** production/recorded_provider 覆盖率。
            if replay != "simulated" or not (raw.get("input") or {}).get("provider_replay_reason"):
                _errors(errors, case, "llm_contract 案例必须使用 recorded_provider 执行器；"
                                      "确实无法真实回放的必须声明 input.provider_replay="
                                      "\"simulated\" 并写明 provider_replay_reason")

    # ---- 执行器声明一致性门禁（禁止靠改 layer / executor 字段虚增生产覆盖率）----
    executor = production_mod.executor_of(raw)
    declared_executor = str(raw.get("executor") or "").strip()
    if declared_executor and declared_executor not in EXECUTORS:
        _errors(errors, case, f"未知的 executor：{declared_executor}（闭集：{list(EXECUTORS)}）")
    if declared_executor in PRODUCTION_EXECUTORS and actions:
        _errors(errors, case, f"声明 {declared_executor} 的案例不得声明 actions"
                              f"（那会退化成 Sim 执行器，属于声明造假）")
    if raw.get("layer") == "recorded_provider" and executor != "recorded_provider":
        _errors(errors, case, "layer=recorded_provider 必须由 recorded_provider 执行器执行"
                              "（否则把 layer 改成 recorded_provider 就能虚增覆盖率）")
    if raw.get("layer") == "integration" and executor != "postgres_integration":
        _errors(errors, case, "layer=integration 必须由 postgres_integration 执行器执行")

    if executor == "recorded_provider":
        provider = (raw.get("input") or {}).get("provider")
        if not provider:
            _errors(errors, case, "recorded_provider 案例必须声明 input.provider")
        elif not str(provider).startswith("provider/"):
            _errors(errors, case, f"recorded_provider fixture 必须位于 fixtures/provider/：{provider}")
        else:
            blob = json.dumps(raw.get("input") or {}, ensure_ascii=False)
            if "$fixture" not in blob or str(provider) not in blob:
                _errors(errors, case, "recorded_provider 案例必须真的把 fixture 喂进生产入口"
                                      "（input.steps 里用 $fixture 引用 input.provider 指向的同一份文件）")
    if executor == "postgres_integration":
        pg_steps = [step for step in ((raw.get("input") or {}).get("steps") or [])
                    if isinstance(step, dict) and step.get("pg")]
        if not pg_steps:
            _errors(errors, case, "postgres_integration 案例必须声明 input.steps[*].pg.scenario")

    # 质量守护：production-backed P0 必须命中真实生产模块 / 路由
    if raw.get("priority") == "P0" and production_mod.executor_of(raw) in \
            production_mod.GATE_EXECUTORS:
        entry = raw.get("entry") or {}
        if not (entry.get("module") or (entry.get("http") or {}).get("path")):
            _errors(errors, case, "生产层 P0 案例必须声明真实模块或真实路由入口")
        if not real_source_refs(case):
            _errors(errors, case, "生产层 P0 案例的 source_specs 必须同时指向真实模块 "
                                  "(module:…) 或真实路由 (route:…)")

    # 分层规则
    if case.get("layer") == "deterministic" and \
            str((raw.get("input") or {}).get("provider_replay") or "") != "simulated":
        if any(isinstance(a, dict) and a.get("provider") for a in actions):
            _errors(errors, case, "deterministic 案例不得引用 provider fixture"
                                  "（除非声明 input.provider_replay=\"simulated\" 并写明原因）")
        if any(isinstance(a, dict) and a.get("target_url") for a in actions):
            _errors(errors, case, "deterministic 案例不得传 target_url")
    if case.get("layer") == "recorded_provider":
        refs = [a.get("provider") for a in actions if isinstance(a, dict) and a.get("provider")]
        if not refs and not (raw.get("input") or {}).get("provider"):
            _errors(errors, case, "recorded_provider 案例必须引用 provider fixture")
    if case.get("layer") == "integration":
        if "integration" not in tags:
            _errors(errors, case, "integration 案例必须带 integration tag（默认跳过）")

    # 生产地址 / 真实密钥
    for key, value in _walk(raw):
        if not isinstance(value, str):
            continue
        for pattern in _SECRETS:
            if pattern.search(value):
                _errors(errors, case, f"疑似真实密钥 / 连接串（{key}）：{value[:40]}")
        if _PROD_HOSTS.search(value):
            _errors(errors, case, f"出现生产 / 准生产地址（{key}）：{value[:40]}")
    return errors


def check_dataset(cases) -> list:
    """全量案例检查：id 唯一、重复案例、逐案例契约。"""
    errors = []
    seen_ids = {}
    seen_signatures = {}
    for case in cases:
        if case.case_id in seen_ids:
            errors.append(f"id 重复：{case.case_id}（{case.rel_path} 与 {seen_ids[case.case_id]}）")
        seen_ids[case.case_id] = case.rel_path
        signature = _signature(case)
        if signature in seen_signatures and case.raw.get("priority") != "P0":
            errors.append(f"疑似重复案例（同输入同断言）：{case.case_id} 与 "
                          f"{seen_signatures[signature]}；相同输入与相同断言只算一条")
        else:
            seen_signatures.setdefault(signature, case.case_id)
        errors.extend(check_case_contract(case))
    return errors


def check_fixtures(cases, fixtures, exists_fn) -> list:
    errors = []
    used = set()
    for case in cases:
        from .dataset import fixture_refs

        for ref in fixture_refs(case):
            if not exists_fn(ref):
                errors.append(f"{case.case_id}（{case.rel_path}）：fixture 不存在：{ref}")
            else:
                used.add(ref)
    unused = sorted(set(fixtures) - used)
    if unused:
        errors.append("fixture 未被任何案例引用：" + ", ".join(unused))
    return errors


def check_dataset_assets() -> list:
    """目录卫生：不得提交缓存 / 运行产物 / 报告。"""
    from . import DATASET_ROOT

    errors = []
    bad_names = (".DS_Store",)
    bad_suffix = (".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".log", ".jsonl", ".bak")
    allowed_reports = {".gitkeep"}
    for path in sorted(DATASET_ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(DATASET_ROOT)
        if path.name in bad_names or path.suffix in bad_suffix:
            errors.append(f"数据集内出现运行产物 / 缓存：dataset/evals/cpq/{rel}")
        if "__pycache__" in rel.parts:
            errors.append(f"数据集内出现 __pycache__：dataset/evals/cpq/{rel}")
        if rel.parts[:1] == ("reports",) and path.name not in allowed_reports:
            errors.append(f"reports/ 下只能有 .gitkeep（报告默认写临时目录）：dataset/evals/cpq/{rel}")
    return errors


def legacy_classification() -> dict:
    """读取 `dataset/evals/cpq/legacy_wording_audit.json`：历史文本的分类表。"""
    from . import DATASET_ROOT

    path = DATASET_ROOT / "legacy_wording_audit.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    out = {}
    for row in data.get("entries") or []:
        key = (str(row.get("file") or ""), int(row.get("line") or 0), str(row.get("legacy") or ""))
        out[key] = str(row.get("classification") or "")
    return out


def legacy_wording_audit() -> list:
    """只读审计：仓库现有 E2E 清单里的旧口径文案。

    每条命中都会带上 `classification`（来自 legacy_wording_audit.json）。历史原文一律不改写；
    真正过期的旧口径（未登记为 historical_provenance）会被 runner 判成错误。
    """
    from . import ROOT

    table = legacy_classification()
    hits = []
    for rel in ("docs/specs/tech-agent-recovery-22-e2e-scenarios.json",
                "docs/specs/tech-agent-recovery-22-e2e-scenarios.md"):
        path = ROOT / rel
        if not path.exists():
            continue
        for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name in find_legacy_wording(line):
                hits.append({
                    "file": rel, "line": index, "legacy": name, "text": line.strip()[:120],
                    "classification": table.get((rel, index, name), ""),
                })
    return hits


def unclassified_legacy_hits() -> list:
    """未登记为历史文本的旧口径命中 —— 这些才是「真正过期、必须处理」的。"""
    return [row for row in legacy_wording_audit()
            if row.get("classification") != "historical_provenance"]


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _walk(node, key=""):
    if isinstance(node, dict):
        for sub_key, value in node.items():
            yield from _walk(value, sub_key)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, key)
    else:
        yield key, node


def _collect_strings(node):
    return [value for _key, value in _walk(node) if isinstance(value, str)]


def _signature(case) -> str:
    import json

    payload = {
        "domain": case.get("domain"),
        "input": case.get("input"),
        "actions": case.get("actions"),
        "expected": case.get("expected"),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


PHASE_TABLE = tuple({"no": row["no"], "title": row["title"], "subs": row["subs"]} for row in PHASES)
__all__ = [
    "ACTION_OPS", "WRITE_OPS", "INVARIANTS", "FORBIDDEN_CHECKS", "UI_PROTOCOL", "PHASE_TABLE",
    "check_case_contract", "check_dataset", "check_fixtures", "check_dataset_assets",
    "legacy_wording_audit", "DOMAINS", "PRIORITIES", "LAYERS", "ROLES", "QUOTE_STEPS",
    "QUOTE_ROLE_BY_STEP", "LEGACY_PATTERNS", "STAGE_BY_SUB", "STAGE_IDS", "SUB_STEPS",
]

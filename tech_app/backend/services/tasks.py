"""
轻量级进程内异步任务队列(私有化/单机优先,零外部依赖)。

为什么不是 Celery/Redis: 本平台主打私有化/内网,尽量少引入需要独立运维的中间件。
这里用标准库 ThreadPoolExecutor 把耗时操作(Claude 解析/校验/拆解、CAD 几何/2D/3D
导入)从请求线程剥离,任务状态/进度/结果持久化到 store(可跨请求轮询、可追溯)。
接口刻意做成"提交 fn -> 拿 task_id -> 轮询"这种与具体执行器无关的形态,日后要扩到
多机,只需把 _executor 换成分布式 broker,上层与前端不动。

注意: OCCT/CadQuery 并非线程安全,故所有 CAD 任务经 _cad_lock 串行化;Claude 调用
是 IO 密集,可并发。
"""
from __future__ import annotations

import json
import os
import threading
import traceback
import uuid
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from ..storage import store
from ..time_utils import now_cst_str
from . import ai_governance

try:
    _WORKERS = max(1, min(32, int(os.getenv("TASK_WORKERS", "4"))))
except (TypeError, ValueError):
    _WORKERS = 4
_executor = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="task")
_cad_lock = threading.Lock()  # 串行化 CAD(OCCT 非线程安全)
_submit_lock = threading.Lock()  # 防止双击/多标签页重复提交同一种昂贵任务
_MAX_ERROR_CHARS = 4000
_CURRENT_TASK: ContextVar[tuple[str, str, str, int] | None] = ContextVar("current_task", default=None)

# 过程事件的阶段词表：封闭三值，写错了必须当场报错（见 process_event）。
_TASK_PROCESS_PHASES = ("model", "tool", "progress")
# 与 PROGRESS_LOG_LIMIT 同量级：只防异常循环把任务文档撑爆。
PROCESS_LOG_LIMIT = 400

_SOP_NAMES = {
    "parse": ("图纸解析 SOP", 3), "verify": ("图纸校核 SOP", 3),
    "model_lookup": ("型号核验 SOP", 3), "decompose": ("零件拆解 SOP", 3),
    "process": ("工艺路线 SOP", 3), "cost": ("零件成本 SOP", 3),
    "material_recommend": ("材料方案 SOP", 3),
    "manufacturing_recommend": ("制造方案 SOP", 3),
    "cleaning_recommend": ("洁净方案 SOP", 3),
    "assembly_recommend": ("组装检测 SOP", 3),
    "production_recommend": ("产能评估 SOP", 3),
    "summary_recommend": ("工艺汇总 SOP", 3),
    "costest_recommend": ("成本测算 SOP", 3),
    "pricing_recommend": ("定价 SOP", 3),
    "negotiation_recommend": ("商务谈判 SOP", 3),
    "pricenego_recommend": ("价格协商 SOP", 3),
    "approval_recommend": ("审批定级 SOP", 3),
    "requirement_document_extract": ("需求字段提取 SOP", 3),
}

_TASK_START_PROGRESS = {
    "parse": "准备图纸与附件",
    "verify": "准备校核输入",
    "model_lookup": "准备型号核验输入",
    "decompose": "准备零件拆解输入",
    "process": "准备工艺路线输入",
    "cost": "准备成本分析输入",
    "material_recommend": "准备材料方案输入",
    "manufacturing_recommend": "准备制造方案输入",
    "cleaning_recommend": "准备洁净方案输入",
    "assembly_recommend": "准备组装检测输入",
    "production_recommend": "准备产能评估输入",
    "summary_recommend": "准备工艺汇总输入",
    "costest_recommend": "准备成本测算输入",
    "pricing_recommend": "准备定价输入",
    "negotiation_recommend": "准备谈判策略输入",
    "pricenego_recommend": "准备价格协商输入",
    "approval_recommend": "准备审批建议输入",
    "requirement_document_extract": "准备需求文档输入",
}


def _now() -> str:
    return now_cst_str()


def submit(
    project_id: str,
    kind: str,
    fn: Callable[[], dict],
    cad: bool = False,
    *,
    dedup_key: str | None = None,
    actor: str = "",
) -> str:
    """提交一个任务(fn 为零参可调用,返回 JSON 可序列化的结果),立即返回 task_id。

    actor 是**发起账号**：模型 / API Key 是「全局默认 + 每个账号可覆盖」，worker 线程
    里要按发起人解析路由。没显式传时从当前上下文补（HTTP 请求线程里就是当前登录
    账号）—— 线程池不会自动带上上下文，所以必须在这里取值再显式带进 worker。
    没有发起人的任务(定时、系统自愈重跑、历史任务恢复)留空 → 全局兜底。
    """
    actor = str(actor or "").strip()
    if not actor:
        try:
            from . import acting_user
            actor = acting_user.current_acting_user()
        except Exception:                               # pragma: no cover - 依赖环境
            actor = ""
    with _submit_lock:
        # 只有业务输入完全相同的任务才复用。零件级任务、不同批量或不同补充
        # 说明不能仅因 kind 相同就被错误合并。
        effective_key = dedup_key or kind
        for task in store.list_tasks(project_id):
            task_key = task.get("dedup_key") or task.get("kind")
            if task_key == effective_key and task.get("status") in {"queued", "running"}:
                return str(task["task_id"])

        task_id = uuid.uuid4().hex[:12]
        sop_name, sop_total = _SOP_NAMES.get(kind, ("任务处理 SOP", 3))
        store.save_task(project_id, {
            "task_id": task_id,
            "project_id": project_id,
            "kind": kind,
            "dedup_key": effective_key,
            "status": "queued",
            "progress": _TASK_START_PROGRESS.get(kind, "排队中"),
            # 只增不改的进度日志。前端据此把每一步渲染成对话里的时间线；
            # 上面的 progress 只是"最新一条"，供状态条显示。
            "progress_log": [],
            # 过程事件序列（phase=model/tool/progress）：与进度日志同一条有序流，
            # 会话卡据此回放「调用了哪个模型、哪个工具、拿到什么规模」。
            "process_log": [],
            "sop_name": sop_name,
            "sop_step": 0,
            "sop_total": sop_total,
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        })
        _executor.submit(_run, project_id, task_id, kind, fn, cad, actor)
        return task_id


def _run(project_id: str, task_id: str, kind: str, fn: Callable[[], dict], cad: bool,
         actor: str = "") -> None:
    token = _CURRENT_TASK.set((project_id, task_id, kind, 0))
    acting_token = _set_acting(actor)
    sop_name, sop_total = _SOP_NAMES.get(kind, ("任务处理 SOP", 3))
    _update(project_id, task_id, status="running",
            progress=_TASK_START_PROGRESS.get(kind, "开始处理"), sop_name=sop_name,
            sop_step=0, sop_total=sop_total, started_at=_now())
    try:
        if cad:
            with _cad_lock:
                result = fn()
        else:
            result = fn()
        if kind in ai_governance.AI_TASK_KINDS:
            store.save_ai_result_metadata(
                project_id, task_id, kind, ai_governance.metadata(kind, result)
            )
        # 任务函数自带 status=partial 时（CAD 逐件容错：有零件被跳过但仍有成功件），
        # 终态就是 partial —— 它是终态，轮询方不再等待，也不当失败。
        # 其余任务维持既有的 succeeded 语义，异常路径照旧 failed / interrupted。
        partial = isinstance(result, dict) and result.get("status") == "partial"
        outcome = {
            "status": "partial" if partial else "succeeded",
            "progress": "部分完成" if partial else "完成",
        }
        _update(project_id, task_id, sop_step=sop_total, finished_at=_now(),
                result=result, **outcome)
    except Exception as e:  # noqa: BLE001 — 任务内任何异常都转成失败态
        traceback.print_exc()
        _update(project_id, task_id, status="failed", progress="失败",
                finished_at=_now(), error=_safe_error(e))
    finally:
        _CURRENT_TASK.reset(token)
        _reset_acting(acting_token)


def _set_acting(actor: str):
    """worker 线程内设入发起账号；返回 token 供 finally 还原。

    线程池会复用线程，用了不还原的话下一个任务会继承上一个人的模型与 Key。
    """
    try:
        from . import acting_user
        return acting_user.acting_user_token(actor)
    except Exception:                                   # pragma: no cover - 依赖环境
        return None


def _reset_acting(token) -> None:
    if token is None:
        return
    try:
        from . import acting_user
        acting_user.reset_acting_user(token)
    except Exception:                                   # pragma: no cover
        pass


def _update(project_id: str, task_id: str, **fields) -> None:
    store.update_task(project_id, task_id, **fields)


def report_progress(progress: str, detail=None) -> None:
    """更新当前异步任务的真实阶段；任务函数内可直接调用。

    detail 是可选的结构化明细：**文本口径一字不改**（progress_log / progress 照旧），
    有明细时附着在同一条 process_log 行上，绝不为了带明细另起一条进度行。
    """
    current = _CURRENT_TASK.get()
    if not current or not progress:
        return
    project_id, task_id, kind, step = current
    next_step = min(step + 1, _SOP_NAMES.get(kind, ("任务处理 SOP", 3))[1] - 1)
    _CURRENT_TASK.set((project_id, task_id, kind, next_step))
    # 必须 append 而不是覆盖：轮询间隔（1.2s）内播出的多条进度，覆盖式写法只会
    # 剩下最后一条，检索类任务因此看起来"完全没有过程"。
    store.append_task_progress(project_id, task_id, str(progress)[:240])
    # 同一条进度也进 process_log：进度行与模型 / 工具事件共用一条有序序列，
    # 会话卡才能按唯一顺序回放（见 process_event）。
    row = {"phase": "progress", "text": str(progress)[:240], "at": _now()}
    if detail is not None:
        row["detail"] = _cap_detail(detail)
    store.append_task_process(project_id, task_id, row)
    # 带 detail 的行与不带 detail 的旧行共用同一套渲染：没有明细就不长「详情」。
    _update(project_id, task_id, sop_step=next_step)


def process_event(phase: str, text: str, detail=None) -> None:
    """往当前任务的过程序列里播一条事件（phase：model / tool / progress）。

    与 report_progress 同族：**没有任务上下文时静默 no-op**（Agent 会话线程、HTTP
    请求线程都会走到这里，绝不能污染它们，也不能凭空写盘）。非法 phase 则明确报错 ——
    阶段是封闭词表，"model" / "tool" / "progress" 之外的写法是代码 bug，必须当场看见。

    只播"调用了哪个模型 / 哪个工具、拿到什么规模"这类事实：不得写入 prompt 原文、
    附件内容、密钥或响应正文（文本还会被截到 240 字）。
    """
    name = str(phase or "").strip()
    if name not in _TASK_PROCESS_PHASES:
        raise ValueError(
            '未知的过程阶段 phase=%s（只允许 "model" / "tool" / "progress"）' % phase)
    current = _CURRENT_TASK.get()
    if not current:
        return
    body = str(text or "").strip()
    if not body:
        return
    project_id, task_id, _kind, _step = current
    entry = {"phase": name, "text": body[:240], "at": _now()}
    if detail is not None:
        entry["detail"] = _cap_detail(detail)
    store.append_task_process(project_id, task_id, entry)


PROCESS_DETAIL_LIMIT = 4096
_DETAIL_SUFFIX = "…（明细已截断）"


def _detail_size(value) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


def _string_nodes(value, path=()):
    """列出明细里所有字符串叶子（含路径），供超限时优先截断长文本。"""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            found.extend(_string_nodes(item, path + (key,)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_string_nodes(item, path + (index,)))
    elif isinstance(value, str):
        found.append((path, value))
    return found


def _assign_path(value, path, new_value) -> None:
    node = value
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = new_value


def _cap_detail(detail):
    """把明细收口成可落库的 JSON：非字典丢弃，序列化超 4096 字节时显式截断。

    明细是给界面展开的附属信息，不能因为一段超长差异文本把任务文档撑爆；截断优先
    保留计数与件号，长字符串留明确后缀 —— 不静默丢字段。
    """
    if not isinstance(detail, dict):
        return None
    try:
        text = json.dumps(detail, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return None
    capped = json.loads(text)
    if len(text.encode("utf-8")) <= PROCESS_DETAIL_LIMIT:
        return capped
    while _detail_size(capped) > PROCESS_DETAIL_LIMIT:
        nodes = _string_nodes(capped)
        if not nodes:
            break
        path, longest = max(nodes, key=lambda item: len(item[1]))
        if len(longest) <= 40:
            break
        _assign_path(capped, path, longest[: max(20, len(longest) // 2)] + _DETAIL_SUFFIX)
    if _detail_size(capped) > PROCESS_DETAIL_LIMIT:
        for key in ("output", "input"):
            capped[key] = {name: item for name, item in (capped.get(key) or {}).items()
                           if isinstance(item, (int, float, bool))}
    return capped


def _safe_error(exc: Exception) -> str:
    """任务状态会被持久化并展示，限制异常文本，避免响应/模型输出无限膨胀。"""
    text = str(exc).strip() or type(exc).__name__
    return text[:_MAX_ERROR_CHARS] + ("…（错误详情已截断）" if len(text) > _MAX_ERROR_CHARS else "")


def recover_interrupted_tasks() -> int:
    """标记服务重启前遗留的内存任务，避免轮询端永久显示“处理中”。

    当前执行器是进程内 ThreadPoolExecutor，进程退出后无法恢复函数闭包；因此把
    queued/running 任务明确置为**中断**终态 —— 不是「失败」：这一步没有任何业务动作
    失败，是进程重启把在途任务打断了，前端据此显示蓝色的「中断」而不是红字失败。

    同时把这张中断卡写进项目会话时间线（key 固定 task:<task_id>，与前端同一套幂等
    口径）：浏览器关着的时候被中断的任务，重进项目必须看到「中断」，而不是永远停在
    「进行中」。
    """
    recovered = 0
    for project in store.list_projects():
        project_id = project.get("project_id")
        if not project_id:
            continue
        for task in store.list_tasks(project_id):
            if task.get("status") not in {"queued", "running"}:
                continue
            reason = "服务在任务执行期间重启，任务未完成；请确认输入后重新发起。"
            task.update({
                "status": "interrupted",
                "progress": "服务重启中断",
                "finished_at": _now(),
                "error": reason,
            })
            store.save_task(project_id, task)
            task_id = str(task.get("task_id") or "")
            if task_id:
                sop_name = _SOP_NAMES.get(str(task.get("kind") or ""), ("任务处理 SOP", 3))[0]
                store.append_session_event(project_id, {
                    "kind": "task",
                    "source": "shell",
                    "text": sop_name[:-4] if sop_name.endswith(" SOP") else sop_name,
                    "key": f"task:{task_id}",
                    "task": {"id": task_id, "label": "", "status": "interrupted",
                             "steps": ["服务重启中断"], "error": reason},
                })
            recovered += 1
    return recovered

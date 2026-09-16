"""发起账号的上下文（「这一轮是谁在操作」）。

模型与密钥从"全局一个值"变成"全局默认 + 每个账号可覆盖"之后，服务层必须知道
**这一次调用是谁发起的**，否则没法在"账号级"和"全局"之间选。传参当然更显式，
但 `resolve()` 这类函数散落在解析、图纸、成本、Agent 各处，逐个加参数既改不完也
容易漏 —— 所以按 contextvars 传：

  · HTTP：`main.auth_guard` 判定出 `request.state.user` 的同一处设入；
  · 异步任务：`tasks.submit(actor=...)` 把值带到 worker 线程，`_run()` 里设入；
  · 没有发起人（定时任务、系统自愈重跑、历史任务恢复）→ 空串 → 全局兜底。

注意：**接口自己算"我的 / 生效"时必须显式用 `current_user` 的 username**，不能
指望这里 —— HTTP 中间件与线程池之间的传播不由业务代码假设（见 Spec C6）。
"""
from __future__ import annotations

from contextvars import ContextVar

# 默认空串 = 没有发起账号 = 走全局默认，与今天的行为完全一致。
_ACTING_USER: ContextVar[str] = ContextVar("acting_user", default="")


def set_acting_user(username: str) -> None:
    """设入当前上下文的发起账号（空串 = 没有发起账号，回落全局）。"""
    _ACTING_USER.set(str(username or "").strip())


def current_acting_user() -> str:
    """取当前上下文的发起账号；没有时返回空串。"""
    try:
        return _ACTING_USER.get() or ""
    except LookupError:                                  # pragma: no cover - 正常取不到才兜底
        return ""


def acting_user_token(username: str):
    """内部用：设入并返回 token，供调用方在 finally 里 reset（线程池复用线程时必须）。"""
    return _ACTING_USER.set(str(username or "").strip())


def reset_acting_user(token) -> None:
    """内部用：还原到上一个上下文值。"""
    try:
        _ACTING_USER.reset(token)
    except (LookupError, ValueError):                    # pragma: no cover - token 跨上下文
        pass

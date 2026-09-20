"""长结构化输出被 provider 输出上限截断时的统一补救策略（唯一实现）。

背景：1.1 需求解析等长 JSON 任务在输出达到 provider `max_tokens` 上限时会被截断，
早期实现要么整份重新生成（预算没变，必然再次截断），要么直接放弃。这里把
「识别截断 → 提升预算 → 续写拼接 → 失败可诊断」收敛成一处定义，供
`qwen_client` / `openai_client` / `requirement_extract` / `oc_agent` 以及三个业务
Agent 会话复用，避免每个 provider 各写一套。

契约：
- `is_truncated`：判断 provider 给出的结束原因是否是输出上限截断；
- `raised_budget`：截断后重试用的新预算，严格大于旧值且不超过 provider 上限；
- `stitch_fragments`：把续写片段与已产出片段本地拼接（模型整段重发时直接采用新内容）；
- `continuation_instruction`：要求模型只补剩余部分的提示词；
- `truncation_note`：可写入日志/审计的一行诊断；
- 补救失败必须抛 `OutputTruncated`，绝不把半截 JSON 当成功返回。
"""
from __future__ import annotations

import sys
import types
from typing import Any

# provider 明确表示「因输出上限结束」的结束原因。
TRUNCATION_REASONS = frozenset({"length", "max_tokens", "max_output_tokens"})

# 长结构化文本任务（如 1.1 需求解析）的默认输出预算；调用方按 provider 上限夹取。
DEFAULT_TEXT_OUTPUT_BUDGET = 12000

# 规范化后的续写提示词前缀：必须携带已产出片段，并要求只补剩余内容。
CONTINUATION_INSTRUCTION = (
    "上一次输出因达到输出上限被截断。下面是已经产出的 JSON 片段，"
    "请只补全剩余部分（续写），使整体成为完整、合法的 JSON；"
    "不要重复已经输出的内容，不要输出 Markdown，也不要重新上传图片或附件。\n\n"
    "已输出片段:\n"
)


class OutputTruncated(RuntimeError):
    """输出因 provider 上限被截断，且提升预算 / 续写都无法补救。"""

    def __init__(
        self,
        message: str = "模型输出被截断",
        *,
        finish_reason: str | None = None,
        limit: int | None = None,
        partial: str = "",
    ) -> None:
        super().__init__(message)
        self.finish_reason = finish_reason
        self.limit = limit
        self.partial = partial or ""


# 本模块可能被「按文件路径重复加载」（离线测试、诊断脚本），每次 exec 都会新建类对象，
# 于是 isinstance(error, llm_output.OutputTruncated) 会因模块副本而失效。把类型对象放进
# 一个进程级共享槽位，保证同一个进程里始终只有唯一一个 OutputTruncated 类型。
_SHARED_TYPES_KEY = "cpq_llm_output_shared_types"
_shared_types = sys.modules.setdefault(_SHARED_TYPES_KEY, types.ModuleType(_SHARED_TYPES_KEY))
OutputTruncated = getattr(_shared_types, "OutputTruncated", OutputTruncated)
_shared_types.OutputTruncated = OutputTruncated


def is_truncated(reason: Any) -> bool:
    """None / stop / end_turn / tool_use 等正常结束原因一律返回 False。"""
    if reason is None:
        return False
    return str(reason).strip().lower() in TRUNCATION_REASONS


def raised_budget(current: int, cap: int) -> int | None:
    """返回严格大于 `current`、且不超过 `cap` 的新预算；无法提升时返回 None。"""
    try:
        current_value = int(current)
        cap_value = int(cap)
    except (TypeError, ValueError):
        return None
    if cap_value <= 0 or current_value >= cap_value:
        return None
    # 先按 1.5 倍放大，至少 +1，确保严格更大，再夹到 provider 上限。
    candidate = max(current_value + 1, int(current_value * 1.5))
    return min(candidate, cap_value)


def budget_within(preferred: int, cap: int) -> int:
    """把调用方偏好的输出预算夹到 provider 上限内（供默认预算使用）。"""
    try:
        preferred_value = int(preferred)
        cap_value = int(cap)
    except (TypeError, ValueError):
        return 0
    return max(1, min(preferred_value, cap_value))


def stitch_fragments(partial: str, addition: str) -> str:
    """把续写片段接到已产出片段后面；模型若整段重发则直接采用新内容。"""
    partial = partial or ""
    addition = addition or ""
    if not partial:
        return addition
    if not addition:
        return partial
    head = partial.lstrip()[:64]
    if head and addition.lstrip().startswith(head):
        return addition
    return partial + addition


def continuation_instruction(partial: str) -> str:
    """生成续写提示词：明确携带已产出片段，要求只补剩余部分。"""
    return CONTINUATION_INSTRUCTION + (partial or "")


def truncation_note(
    *,
    finish_reason: Any,
    initial_budget: Any,
    final_budget: Any,
    attempts: Any,
    limit: Any,
    path: str = "",
    outcome: str = "",
) -> str:
    """生成单行截断诊断，必须包含结束原因、初始预算、最终预算与上限值。

    `path` 标明调用路径（qwen / qwen-web-search / openai），`outcome` 记录本次补救
    的最终结果（成功 / 失败 / 续写重试），供日志与审计使用。
    """
    prefix = f"[{path}] " if path else ""
    note = (
        f"{prefix}输出截断补救：finish_reason={finish_reason}，"
        f"初始预算={initial_budget}，最终预算={final_budget}，"
        f"续写次数={attempts}，上限={limit}"
    )
    return f"{note}，结果={outcome}" if outcome else note


def _remediation_log() -> list:
    """补救记录放在进程级共享槽位，模块被重复加载时也不会丢。"""
    log = getattr(_shared_types, "remediation_log", None)
    if log is None:
        log = []
        _shared_types.remediation_log = log
    return log


def record_remediation(note: str, *, limit: int = 50) -> None:
    """记录一次「提升预算 / 续写」补救（R6 可观测性）。

    只保存 `truncation_note` 生成的诊断文本：不含图纸二进制、完整提示词与 API Key。
    """
    if not note:
        return
    log = _remediation_log()
    log.append(str(note))
    if limit > 0:
        del log[:-limit]


def remediation_notes() -> list:
    """返回最近的截断补救记录副本，供日志 / 审计 / 诊断读取。"""
    return list(_remediation_log())

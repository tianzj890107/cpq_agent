"""对话消息修复：保证 tool_use ↔ tool_result 成对，避免发给模型时被拒。

背景（2026-07-13）：open-claude 的自动压缩 `compact_conversation` 在切分
old/recent 时，只保证边界落在「assistant 之后」——但当那条 assistant 以
`tool_use` 结尾时，配对的 `tool_result`（下一条 user 消息）会成为 recent 的
第一条、而它的 `tool_use` 已被摘要掉，形成**孤儿 tool_result**。经 OpenAI 兼容
适配器（DeepSeek/Qwen/GLM/Kimi）序列化后是一条没有前置 tool_calls 的
`role:"tool"` 消息，供方直接 400：
  "Messages with role 'tool' must be a response to a preceding message with 'tool_calls'"

同理，回合被中断（stop_reason=error）会留下**没有 tool_result 的 assistant
tool_use**，下一轮同样非法。

本模块在**每次调用模型前**由各 Bridge 调一次，把这两类不成对的块清掉：
  - user 消息里 tool_use_id 不匹配「紧邻前一条 assistant 的 tool_use」的
    tool_result 块 → 丢弃；整条只剩孤儿则删除该消息；
  - assistant 消息里的 tool_use，若「紧邻后一条 user」没有对应 tool_result →
    剥掉这些 tool_use（保留文本）；剥空则删除该消息。
不修改 open-claude 包，纯在调用方兜底；无异常时为幂等无副作用。
"""
from __future__ import annotations

from typing import Any, Dict, List


def _tool_use_ids(msg: Dict[str, Any]) -> set:
    c = msg.get("content")
    if not isinstance(c, list):
        return set()
    return {b.get("id") for b in c
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")}


def _tool_result_ids(msg: Dict[str, Any]) -> set:
    c = msg.get("content")
    if not isinstance(c, list):
        return set()
    return {b.get("tool_use_id") for b in c
            if isinstance(b, dict) and b.get("type") == "tool_result"}


def sanitize_tool_pairs(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """返回一个 tool_use/tool_result 严格成对的新消息列表（不改原列表元素）。"""
    if not isinstance(messages, list) or not messages:
        return messages

    # 第 1 遍：丢弃 user 消息里「前一条 assistant 未声明该 id」的孤儿 tool_result
    pass1: List[Dict[str, Any]] = []
    for msg in messages:
        if (msg.get("role") == "user" and isinstance(msg.get("content"), list)
                and any(isinstance(b, dict) and b.get("type") == "tool_result"
                        for b in msg["content"])):
            prev_ids = _tool_use_ids(pass1[-1]) if pass1 else set()
            kept = [
                b for b in msg["content"]
                if not (isinstance(b, dict) and b.get("type") == "tool_result")
                or b.get("tool_use_id") in prev_ids
            ]
            if not kept:
                continue  # 整条都是孤儿 tool_result → 删除
            msg = {**msg, "content": kept}
        pass1.append(msg)

    # 第 2 遍：assistant 的 tool_use 若后一条 user 没有对应 tool_result → 剥掉
    pass2: List[Dict[str, Any]] = []
    for i, msg in enumerate(pass1):
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
            tu_ids = _tool_use_ids(msg)
            if tu_ids:
                nxt = pass1[i + 1] if i + 1 < len(pass1) else None
                satisfied = _tool_result_ids(nxt) if nxt else set()
                if not tu_ids.issubset(satisfied):
                    kept = [b for b in msg["content"]
                            if not (isinstance(b, dict) and b.get("type") == "tool_use")]
                    if not any(isinstance(b, dict) and b.get("text") for b in kept):
                        continue  # 剥空（无文本）→ 删除该 assistant
                    msg = {**msg, "content": kept}
        pass2.append(msg)

    return pass2

"""入口分级：这个技术项目到底是不是「从报价开始的」。

现场那一批卡死的报告回传，根因不在回传本身，而在源头：技术首页可以直接上传图纸建项，
建出来的项目没有任何报价线索，走到 5.3 回传时服务端找不到对应的报价卡片，界面又只有
一句「请填写新建原因后重试」——用户无处可填。所以入口必须先分清：

    · 有报价线索（业务实例号 / 来源任务 / 来源会话 / 来源标记）→ `quote`，正式链路；
    · 全空 → `internal_test`，内部测试入口：**可以建**，但必须说清它不是正式入口，
      并留下痕迹，免得事后分不清哪个项目回传得回去、哪个回不去。

判定只有这一处（Spec §2）。纯函数：不读库、不读环境变量、不改入参，同输入同输出。
"""
from __future__ import annotations

from typing import Any

#: 线索键与判定顺序（返回的 `clues` 按这个顺序，不许按传入顺序或字典顺序）。
CLUE_KEYS = ("business_case_id", "source_task_id", "source_session_id", "source")

#: 内部测试入口的固定说明（前端直接展示，不要另写一份文案）。
INTERNAL_TEST_REASON = (
    "没有报价来源线索（业务实例号 / 来源任务 / 来源会话），这不是正式入口："
    "正式流程应从报价发起；从这里建的内部测试项目走到回传那一步会因为找不到报价卡片"
    "而停下。"
)


def classify_entry(*, business_case_id: str = "", source_task_id: str = "",
                   source_session_id: str = "", source: str = "") -> dict:
    """按报价线索判定入口来源，返回 `origin` / `internal_test` / `clues` / `reason` 四键。

    | 键 | 取值 |
    | --- | --- |
    | `origin` | `"quote"`（有任一非空线索）/ `"internal_test"`（全空） |
    | `internal_test` | `bool`，与 `origin` 同步，便于前端直接判断 |
    | `clues` | 非空线索的键名，顺序固定见 `CLUE_KEYS` |
    | `reason` | 内部测试入口时非空；正式入口为空串 |
    """
    given: dict[str, Any] = {
        "business_case_id": business_case_id,
        "source_task_id": source_task_id,
        "source_session_id": source_session_id,
        "source": source,
    }
    clues = [key for key in CLUE_KEYS if str(given.get(key) or "").strip()]
    if clues:
        return {"origin": "quote", "internal_test": False, "clues": clues, "reason": ""}
    return {"origin": "internal_test", "internal_test": True, "clues": [],
            "reason": INTERNAL_TEST_REASON}

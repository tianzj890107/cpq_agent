# -*- coding: utf-8 -*-
"""前后端编号表逐行比对（发布保障批次 2，Spec §2.4）。

后端唯一事实源是 `tech_app/backend/services/workflow_stages.py`（5 阶段 × 13 子步骤）；
`tech_app/frontend/tech-workbench.js` 的 `const STAGES` 仍是**手抄**的 9 行。
本模块交付「逐行比对」这条护栏（把前端改成派生留给后续批次，避免一次动到加载时序）。

  · `frontend_rows(path)` —— 解析 `tech-workbench.js` 的 `STAGES` 数组，每行给
    `id / phase / phaseTitle / no / subTitle / page`；
  · `backend_rows()` —— 从后端取每个 stage 的**首个子步骤**（process→3.1、cost→4.1）；
  · `assert_parity(case)` —— 9 行逐字段比对，任何差异都点出**具体 id 与字段**。
"""
from __future__ import annotations

import pathlib
import re
import sys

__all__ = ["STAGE_FIELDS", "frontend_rows", "backend_rows", "assert_parity"]

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 比对字段（前端 JSON 键名；后端行也归一成这套键名）。
STAGE_FIELDS = ("id", "phase", "phaseTitle", "no", "subTitle", "page")

#: 前端手抄表所在文件（`assert_parity` 不给参时用）。
DEFAULT_JS = ROOT / "tech_app" / "frontend" / "tech-workbench.js"

_STAGE_FIELD_RE = re.compile(r"(\w+)\s*:\s*'([^']*)'")
_ARRAY_ITEM_RE = re.compile(r"\{([^{}]*)\}")


def _array_body(text: str) -> str:
    """取 `const STAGES = [ ... ]` 的方括号内容（按 `[` / `]` 计数找配对）。"""
    marker = text.index("const STAGES")
    start = text.index("[", marker)
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return text[start + 1:index]
    raise ValueError("tech-workbench.js 的 STAGES 数组没有配对的 `]`")


def frontend_rows(path=None) -> list:
    """解析前端 `STAGES` 数组，返回 9 行 `{id, phase, phaseTitle, no, subTitle, page}`。"""
    text = pathlib.Path(str(path or DEFAULT_JS)).read_text(encoding="utf-8")
    rows = []
    for item in _ARRAY_ITEM_RE.finditer(_array_body(text)):
        fields = dict(_STAGE_FIELD_RE.findall(item.group(1)))
        rows.append({key: fields.get(key, "") for key in STAGE_FIELDS})
    return rows


def backend_rows() -> list:
    """后端 9 个 stage 的首个子步骤行，键名与 `frontend_rows` 一致。"""
    from tech_app.backend.services import workflow_stages as stages

    rows = []
    for stage_id in stages.stage_ids():
        row = stages.default_row(stage_id)
        if not row:
            continue
        rows.append({
            "id": stage_id,
            "phase": row["phase"],
            "phaseTitle": row["phase_title"],
            "no": row["sub"],
            "subTitle": row["sub_title"],
            "page": row["page"],
        })
    return rows


def assert_parity(case, frontend=None, backend=None, path=None) -> None:
    """9 行逐字段比对；差异必须点出具体 id 与字段，否则 `case.fail`。"""
    front = frontend if frontend is not None else frontend_rows(path)
    back = backend if backend is not None else backend_rows()
    front_by_id = {row["id"]: row for row in front}
    back_by_id = {row["id"]: row for row in back}
    problems = []
    for stage_id in sorted(set(front_by_id) | set(back_by_id)):
        if stage_id not in front_by_id:
            problems.append("%s：前端缺行" % stage_id)
            continue
        if stage_id not in back_by_id:
            problems.append("%s：后端缺行" % stage_id)
            continue
        for field in STAGE_FIELDS:
            left = front_by_id[stage_id].get(field, "")
            right = back_by_id[stage_id].get(field, "")
            if left != right:
                problems.append("%s.%s：前端=%r 后端=%r" % (stage_id, field, left, right))
    if problems:
        case.fail("前端编号表与后端事实源漂移：\n" + "\n".join(problems))

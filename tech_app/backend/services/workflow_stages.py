# -*- coding: utf-8 -*-
"""技术工艺的「5 阶段 × 13 子步骤」口径表（批次 5A）—— 后端唯一事实源。

为什么单独成模块：同一套编号要在三处说同一句话 —— 后端 Agent 提示词里的落点表与
白名单、前端 `tech-workbench.js` 的阶段表 / 子步骤按钮 / 页签代理、各页页内的页签行
与流程条。编号写死在多处就一定漂移：今天就漂成了「顶部第 3 阶段、页内 2.2」。
本模块是后端这份；前端那份按同一张规范表登记，两边逐行一致。

本批只描述**编号与标题**：stage id、页面文件名、URL 参数、iframe 嵌入参数、tech-board-*
消息协议与权限判定一律不变 —— 历史链接、历史会话、历史任务都不受影响。
"""
from __future__ import annotations

from typing import Optional

# 5 个阶段：no / title / subs（阶段内的子步骤号，按页面顺序）。
PHASES = (
    {"no": "1", "title": "工艺评估需求", "subs": ("1.1", "1.2", "1.3")},
    {"no": "2", "title": "图纸解析", "subs": ("2.1",)},
    {"no": "3", "title": "组装与整合", "subs": ("3.1", "3.2", "3.3")},
    {"no": "4", "title": "成本测算", "subs": ("4.1", "4.2", "4.3")},
    {"no": "5", "title": "工艺评估报告", "subs": ("5.1", "5.2", "5.3")},
)

# 13 个子步骤：(stage_id, 阶段号, 阶段标题, 子步骤号, 子步骤标题, 页签 view, 页面文件)。
# process / cost 各跨 3 个子步骤（当前子步骤由页内页签决定），其余 7 个 stage 与子步骤 1:1。
#
# **行序 = 依赖顺序，不是阶段号顺序**（Spec `packaging-stage-order-equals-dependency.md` §2.1）：
# 图纸解析第 8 步 `field_write` 要把图纸里读出来的字段**回写进那张需求单**，所以它必须在
# 「1.1 创建需求（存草稿）」之后、「1.2 确认需求」之前 —— 1.1 提交确认 / 1.2 通过确认 /
# 1.3 审核通过 都会把需求推离 `EDITABLE_STATUSES`，先做确认/审核再解析必 `blocked /
# REQUIREMENT_NOT_EDITABLE`（实测 7/8）。用户顺着流程栏点下去就该是对的，所以这张表按
# 「创建需求 → 图纸解析 → 确认需求 → 审核需求 → …」排。
# 阶段号 / 阶段标题 / 子步骤号本身一个字不改（PHASES 与既有前端规范表同口径）。
_STAGE_ROWS = (
    ("requirement-create", "1", "工艺评估需求", "1.1", "创建需求", "",
     "requirement-create.html"),
    ("drawing", "2", "图纸解析", "2.1", "图纸解析", "", "index.html"),
    ("requirement-confirm", "1", "工艺评估需求", "1.2", "确认需求", "",
     "requirement-confirm.html"),
    ("requirement-review", "1", "工艺评估需求", "1.3", "审核需求", "",
     "requirement-review.html"),
    ("process", "3", "组装与整合", "3.1", "整合图纸", "drawings",
     "assembly-integration.html"),
    ("process", "3", "组装与整合", "3.2", "参数推荐", "params",
     "assembly-integration.html"),
    ("process", "3", "组装与整合", "3.3", "组装工艺", "process",
     "assembly-integration.html"),
    ("cost", "4", "成本测算", "4.1", "零件成本", "parts", "cost-review.html"),
    ("cost", "4", "成本测算", "4.2", "组装成本", "assembly", "cost-review.html"),
    ("cost", "4", "成本测算", "4.3", "汇总", "total", "cost-review.html"),
    ("summary", "5", "工艺评估报告", "5.1", "汇总结果", "", "summary.html"),
    ("report-review", "5", "工艺评估报告", "5.2", "结果审核", "", "report-review.html"),
    ("report-publish", "5", "工艺评估报告", "5.3", "发布并回传报价", "",
     "report-publish.html"),
)

# 跨子步骤的 stage：进入时停在第一个子步骤（process → 3.1 整合图纸、cost → 4.1 零件成本）。
MULTI_SUB_STAGES = ("process", "cost")

STAGES = tuple(
    {"stage_id": stage_id, "phase": phase, "phase_title": phase_title,
     "sub": sub, "sub_title": sub_title, "title": sub_title,
     "view": view, "page": page}
    for stage_id, phase, phase_title, sub, sub_title, view, page in _STAGE_ROWS
)

# 阶段 → 阶段标题（按阶段号查）。查不到返回空串，不退回任何默认阶段。
_PHASE_TITLE = {row["no"]: row["title"] for row in PHASES}


def rows_of(stage_id: str) -> tuple:
    """某个 stage 的全部子步骤行（按页面顺序）。查不到返回空元组 —— 不回退成别的步骤。"""
    sid = str(stage_id or "").strip()
    return tuple(row for row in STAGES if row["stage_id"] == sid)


def default_row(stage_id: str) -> Optional[dict]:
    """stage 的默认子步骤行（跨子步骤的 stage 取第一个）；查不到返回 None。"""
    rows = rows_of(stage_id)
    return rows[0] if rows else None


def stage_ids() -> tuple:
    """9 个 stage id，按第 1～5 阶段 + 子步骤顺序去重（白名单用）。"""
    out = []
    for row in STAGES:
        if row["stage_id"] not in out:
            out.append(row["stage_id"])
    return tuple(out)


def sub_of(stage_id: str) -> str:
    """stage 的默认子步骤号（process → 3.1、cost → 4.1）；查不到返回空串。"""
    row = default_row(stage_id)
    return row["sub"] if row else ""


def sub_title_of(stage_id: str) -> str:
    """stage 的默认子步骤标题；查不到返回空串。"""
    row = default_row(stage_id)
    return row["sub_title"] if row else ""


def phase_title_of(stage_id: str) -> str:
    """stage 所在阶段的标题；查不到返回空串。"""
    row = default_row(stage_id)
    return _PHASE_TITLE.get(row["phase"], "") if row else ""


def stage_title(stage_id: str) -> str:
    """用户可见的阶段称呼（也是发往模型的 page_context）。

    称呼规则：1:1 的 stage 用「子步骤号 + 子步骤标题」（`2.1 图纸解析`、`5.2 结果审核`）；
    跨子步骤的 stage 用「阶段号 + 阶段标题」（`3 组装与整合`、`4 成本测算`）。
    查不到返回空串，**不回退成任何别的步骤** —— 历史 URL 带了已退役的 stage 时，
    宁可没有标题，也不能指着另一个步骤骗用户。
    """
    row = default_row(stage_id)
    if not row:
        return ""
    if row["stage_id"] in MULTI_SUB_STAGES:
        return f"{row['phase']} {row['phase_title']}"
    return f"{row['sub']} {row['sub_title']}"


def prompt_table() -> str:
    """Agent 系统提示词里的落点表：一个阶段一行，子步骤带 stage 与页签。"""
    lines = []
    for phase in PHASES:
        cells = []
        for row in STAGES:
            if row["phase"] != phase["no"]:
                continue
            tail = f" / {row['view']}" if row["view"] else ""
            cells.append(f"{row['sub']} {row['sub_title']}（{row['stage_id']}{tail}）")
        lines.append(f"- 阶段 {phase['no']} {phase['title']}：" + " / ".join(cells))
    return "\n".join(lines)

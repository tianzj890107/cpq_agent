"""
2.3 成本测算的评审状态 —— CPQ 技术工艺。

成本**数据**本身不在这里：零件成本仍然存在 store.save_cost(project_id, part_id)，
整机成本仍然存在 2.2 的 IntegrationPlan.cost。2.3 是一个**由财务经理主持的环节**，
它做三件事：把两边的数汇总、允许财务重算/改数、以及记录他最后的三个去向动作。

所以这个模型只装"评审状态"，不复制一份成本 —— 复制就意味着两份数据迟早不一致。
"""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field


class CostAction(BaseModel):
    """财务在 2.3 末尾做过的一次对外动作的留痕。"""
    kind: str = Field("", description="material-write / send-to-quote / return-to-process")
    label: str = Field("", description="中文动作名，供界面直接显示")
    detail: str = Field("", description="一句话结果，如成品编码、收件人")
    at: Optional[str] = None
    by: Optional[str] = None


class CostReview(BaseModel):
    """2.3 的评审状态。成本数字不在这里，见模块 docstring。"""
    project_id: Optional[str] = None
    # 财务补充说明：会作为下一次成本测算的输入（和 2.2 的整合需求同一个用法）。
    note: str = Field("", description="财务经理的补充说明，参与重新测算")
    # 谁把它交到财务手里的、什么时候
    received_from: str = Field("", description="发起人（工艺经理）")
    received_at: Optional[str] = None
    confirmed: bool = Field(False, description="财务已确认本步成本")
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    actions: List[CostAction] = Field(default_factory=list, description="历次对外动作")
    updated_at: Optional[str] = None

    @property
    def done_kinds(self) -> set:
        return {item.kind for item in self.actions}


class CostReviewBody(BaseModel):
    """财务在 2.3 保存的设置。核算批量跟着成本走 —— 2.2 撤掉成本之后它归这一步。"""
    note: str = ""
    quantity: int = 1


class CostActionBody(BaseModel):
    """三个去向动作共用的入参。"""
    product_name: str = ""
    spec: str = ""
    note: str = ""
    target_user_id: str = Field("", description="退回工艺经理时可指定到人；留空按角色发")


def merge_totals(rows: List[dict]) -> dict:
    """把逐项成本按企业口径的四项相加。rows 里每项是 cost_model.breakdown 的结果。

    合计在这里算，不让前端各算各的 —— 报价拿到的单价必须与这里一致。
    """
    keys = ("material", "labor", "overhead", "machining", "total")
    out: dict[str, Any] = {key: 0.0 for key in keys}
    for row in rows:
        for key in keys:
            try:
                out[key] += float(row.get(key) or 0)
            except (TypeError, ValueError):
                continue
    return {key: round(value, 2) for key, value in out.items()}

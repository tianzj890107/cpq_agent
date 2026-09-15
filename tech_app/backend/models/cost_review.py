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


class CostReviewWaiver(BaseModel):
    """2.3「确认成本」的一次缺口签字。

    与需求阶段 / 2.2 的签字同一套语义：缺口以**服务端算出的编码集合**为准，
    后来补上的缺口不该抹掉历史签字，所以只追加、不覆盖；签字之后新冒出的缺口会让
    集合变大，覆盖判定因此失败、必须重新签。
    """
    stage: str = Field("cost_review", description="签字发生在哪一步")
    missing_codes: List[str] = Field(default_factory=list, description="缺口编码（比对键）")
    missing_fields: List[str] = Field(default_factory=list, description="缺口中文名，给人看")
    reason: str = Field("", description="允许为空，服务端补默认原因")
    waived_by: Optional[str] = None
    waived_at: Optional[str] = None
    reused: bool = Field(False, description="复用已有签字，而不是重新签一次")


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
    # 「带缺口继续」的签字：未算零件 / 整机未算 / 算出来是 0 元时，人签一次字放行。
    # 只追加不覆盖 —— 后来补上的缺口不该把历史签字抹掉（与 2.2、需求阶段同一套）。
    waivers: List[CostReviewWaiver] = Field(
        default_factory=list, description="历次「带缺口继续」的签字（L2 缺口豁免）")
    updated_at: Optional[str] = None

    @property
    def done_kinds(self) -> set:
        return {item.kind for item in self.actions}


class CostReviewBody(BaseModel):
    """财务在 2.3 保存的设置。核算批量跟着成本走 —— 2.2 撤掉成本之后它归这一步。"""
    note: str = ""
    quantity: int = 1


class CostConfirmBody(BaseModel):
    """「确认成本」的可选请求体：缺口没算齐时由前端弹「仍要继续」，点继续才带上签字。

    不带请求体的老调用（Agent 工具 / 既有脚本）行为不变：有缺口就如实拒绝。
    """
    waiver: Optional[dict] = Field(
        None, description="人的签字；只取 reason，缺口以服务端算出的为准")


class CostActionBody(BaseModel):
    """三个去向动作共用的入参。"""
    product_name: str = ""
    spec: str = ""
    note: str = ""
    target_user_id: str = Field("", description="提交工艺经理确认时可指定到人；留空按角色发")
    # 统一工作台从待办点进来时 URL 上带着 task_id：完成正式去向后按它关闭原来的
    # claimed 财务待办。留空不影响老入口（Agent / 直接打开页面）继续可用。
    source_task_id: str = Field(
        "", description="来源待办任务号；完成后据此把已领取的 tech_cost 任务置为完成")


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

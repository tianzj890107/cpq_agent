"""
组装与整合 IR —— CPQ 技术工艺 2.2。

2.1 把一张图纸拆成一堆**零件**；2.2 反过来，把这些零件重新装回一台**整机/总成**，
回答三个问题：整机的参数是什么、怎么装、装出来多少钱。

沿用平台一贯的分工：模型只产出**结构化**结果，确定性的部分（金额重算、工时合计、
工艺库覆盖比对）由平台算。工艺与成本直接复用 ProcessPlan / CostAnalysis ——
组装线的工序和零件工序在数据结构上没有区别（ProcessType 本来就有 assembly），
另起一套模型只会让 3.1 汇总、导出和前端渲染各多一条分支。

注意: 与其它模型文件一致，为兼容工具调用承载的结构化输出，不使用数值约束(min/max)。
"""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from .coercion import StrList
from .cost import CostAnalysis
from .ir import OpenQuestion
from .material import Timing
from .process import ProcessPlan


def _alias(data: dict, target: str, *aliases: str) -> None:
    """仅当标准字段为空时才采用模型可能用的别名。"""
    if data.get(target) not in (None, "", [], {}):
        return
    for name in aliases:
        if data.get(name) not in (None, "", [], {}):
            data[target] = data[name]
            return


# --------------------------------------------------------------------------- #
# ① 整机参数
# --------------------------------------------------------------------------- #
class IntegrationParam(BaseModel):
    # name 不设成必填：模型偶尔会漏掉个别条目的名字，必填会让**整份**参数表校验失败，
    # 修复几轮后往往退化成空数组 —— 宁可留下能用的行，无名行由 product_params.align 丢掉。
    name: str = Field("", description="参数名，如 '最大尺寸' / '标称电压' / '重量'")
    value: str = Field("", description="参数值。范围与公差照写，不要四舍五入成一个数")
    unit: Optional[str] = Field(None, description="单位，如 mm / V / g / mAh")
    param_code: Optional[str] = Field(
        None,
        description="对应报价成品参数字典里的字段编码，如 rated_voltage / max_dimension。"
                    "字典之外的自有参数留空",
    )
    category: Optional[str] = Field(
        None, description="归类。命中参数字典时由平台按字典分组回填，无需自己判断")
    basis: Optional[str] = Field(
        None, description="推出该值的依据，如 '上下壳 108×56 叠高 22.5+4' / '需求单第 3 条'")
    source: Optional[str] = Field(
        None, description="来源: 需求 / 整合图纸 / 零件汇总 / 工程推荐")
    confidence: float = Field(0.6, description="该参数的置信度 0~1")

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value):
        if isinstance(value, str):
            return {"name": value, "value": ""}
        if not isinstance(value, dict):
            return value
        data = dict(value)
        _alias(data, "name", "param", "param_name", "item", "field", "field_name",
               "参数", "名称", "参数名", "字段", "指标")
        _alias(data, "value", "val", "param_value", "数值", "参数值", "取值")
        _alias(data, "unit", "units", "单位")
        _alias(data, "param_code", "code", "field_code", "字段编码", "编码")
        _alias(data, "category", "type", "类别", "分类")
        _alias(data, "basis", "reason", "rationale", "依据", "计算依据")
        _alias(data, "source", "from", "来源")
        _alias(data, "confidence", "confidence_score", "置信度")
        if data.get("value") is not None and not isinstance(data.get("value"), str):
            data["value"] = str(data["value"])
        return data

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value):
        if value in (None, ""):
            return 0.6
        text = str(value).strip().lower()
        labels = {"高": 0.85, "high": 0.85, "中": 0.6, "medium": 0.6, "低": 0.35, "low": 0.35}
        if text in labels:
            return labels[text]
        try:
            number = float(text.rstrip("%"))
        except ValueError:
            return 0.6
        if "%" in text or number > 1:
            number /= 100
        return max(0.0, min(1.0, number))


class IntegrationInterface(BaseModel):
    """零件之间的连接/配合。组装工艺的工序基本都是从这里长出来的。"""
    name: str = Field(..., description="接口名称，如 '上壳↔下壳' / 'BMS↔电芯组'")
    parts: StrList = Field(default_factory=list, description="涉及的零件编号，如 ['P-001','P-002']")
    method: Optional[str] = Field(
        None, description="连接方式，如 螺接 / 卡扣 / 点焊 / 激光焊 / 点胶 / 压装")
    spec: Optional[str] = Field(None, description="紧固件或配合规格，如 'M3×8 自攻钉 ×4' / '过盈 0.02'")
    control: Optional[str] = Field(None, description="控制要点，如 '扭矩 0.6 N·m' / '对位 ±0.1mm'")
    note: Optional[str] = Field(None, description="备注/风险")

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value):
        if isinstance(value, str):
            return {"name": value}
        if not isinstance(value, dict):
            return value
        data = dict(value)
        _alias(data, "name", "interface", "joint", "接口", "名称")
        _alias(data, "parts", "part_ids", "components", "零件")
        _alias(data, "method", "type", "连接方式", "方式")
        _alias(data, "spec", "fastener", "specification", "规格")
        _alias(data, "control", "quality", "requirement", "控制要点")
        return data


class IntegrationPartRef(BaseModel):
    """整机用到的零件。数量是**单台用量**，不是批量。"""
    part_id: str = Field("", description="2.1 解析出的零件编号，如 P-001；外购标准件可留空")
    name: str = Field("", description="零件名称")
    quantity: int = Field(1, description="单台用量")
    role: Optional[str] = Field(None, description="在整机里的作用")
    source: str = Field("2.1", description="来源: 2.1 / 标准件 / 整合图纸新增")

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value):
        if isinstance(value, str):
            return {"part_id": value, "name": value}
        if not isinstance(value, dict):
            return value
        data = dict(value)
        _alias(data, "part_id", "id", "part_no", "零件编号")
        _alias(data, "name", "part_name", "零件名称")
        _alias(data, "quantity", "qty", "count", "数量", "用量")
        _alias(data, "role", "function", "作用")
        return data

    @field_validator("quantity", mode="before")
    @classmethod
    def normalize_quantity(cls, value):
        try:
            return max(1, int(float(str(value).strip() or 1)))
        except (TypeError, ValueError):
            return 1


class IntegrationParamFill(BaseModel):
    """「整合参数」智能补全给出的一条建议。**是建议，不是结论** —— 由人过目后才写入。"""
    code: str = Field("", description="报价成品参数字典里的字段编码，如 rated_voltage")
    value: str = Field("", description="建议值。推不出来就留空，不要编")
    unit: Optional[str] = Field(None, description="单位，与字典一致时可不给")
    basis: str = Field("", description="这个值是怎么来的：哪个零件、哪条需求、哪次测算")
    confidence: float = Field(0.5, description="置信度 0~1。靠常识凑的要给低分")

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        _alias(data, "code", "param_code", "field", "字段编码", "编码")
        _alias(data, "value", "val", "数值", "参数值", "取值")
        _alias(data, "unit", "units", "单位")
        _alias(data, "basis", "reason", "依据", "理由")
        _alias(data, "confidence", "confidence_score", "置信度")
        if data.get("value") is not None and not isinstance(data.get("value"), str):
            data["value"] = str(data["value"])
        return data

    @field_validator("confidence", mode="before")
    @classmethod
    def normalize_confidence(cls, value):
        """模型经常写"高/中/低"或"80%"。与 IntegrationParam 同一套口径。"""
        if value in (None, ""):
            return 0.5
        text = str(value).strip().lower()
        labels = {"高": 0.85, "high": 0.85, "中": 0.6, "medium": 0.6, "低": 0.35, "low": 0.35}
        if text in labels:
            return labels[text]
        try:
            number = float(text.rstrip("%"))
        except ValueError:
            return 0.5
        return number / 100 if "%" in text or number > 1 else number


class IntegrationParamFillPlan(BaseModel):
    """一次智能补全的产出。"""
    fills: List[IntegrationParamFill] = Field(default_factory=list, description="逐项建议值")
    unresolved: StrList = Field(
        default_factory=list, description="确实推不出来、需要人工确认的字段中文名")

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        _alias(data, "fills", "params", "values", "items", "补全", "参数", "建议")
        _alias(data, "unresolved", "missing", "open_questions", "待确认", "无法推断")
        return data


class IntegrationParamPlan(BaseModel):
    """参数推荐的产出。也是后面工艺/成本两步的输入口径。"""
    assembly_name: str = Field("", description="整机/总成名称")
    product_family: Optional[str] = Field(
        None,
        description="成品所属产品族的 key（li_primary / li_ion_pack / ess / pv_module / other）。"
                    "它决定报价成品参数字典里哪些字段适用，判错会推荐出别的产品线的参数",
    )
    assembly_category: Optional[str] = Field(
        None,
        description="整机所属零部件类别，用于召回库内组装路线，如 '电池包' / '结构总成'。"
                    "必须与企业零部件库的类别口径一致，拿不准就留空",
    )
    material_spec: Optional[str] = Field(
        None, description="整机层面新增的主材/耗材牌号(如包装、绝缘料)；没有就留空，不要填零件的材料")
    summary: str = Field("", description="整机构型与整合思路概述")
    params: List[IntegrationParam] = Field(default_factory=list, description="整机参数表")
    interfaces: List[IntegrationInterface] = Field(default_factory=list, description="零件间连接/配合")
    part_refs: List[IntegrationPartRef] = Field(default_factory=list, description="整机 BOM(单台用量)")
    assumptions: StrList = Field(default_factory=list, description="关键假设")
    open_questions: List[OpenQuestion] = Field(default_factory=list, description="待澄清问题")

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        _alias(data, "assembly_name", "name", "device_name", "整机名称", "总成名称")
        _alias(data, "product_family", "family", "产品族")
        _alias(data, "assembly_category", "category", "类别")
        _alias(data, "material_spec", "material", "材料")
        _alias(data, "summary", "overview", "概述")
        _alias(data, "params", "parameters", "参数", "参数表")
        _alias(data, "interfaces", "joints", "接口", "配合")
        _alias(data, "part_refs", "bom", "parts", "零件清单")
        for key in ("params", "interfaces", "part_refs", "open_questions"):
            if not isinstance(data.get(key), list):
                data[key] = [data[key]] if data.get(key) else []
        return data


# --------------------------------------------------------------------------- #
# ② 平台聚合 / 持久化
# --------------------------------------------------------------------------- #
class IntegrationDrawing(BaseModel):
    """整合图纸(装配图/爆炸图/接线图)。文件本体走项目附件目录，这里只记引用。"""
    filename: str = Field(..., description="附件目录下的文件名")
    uploaded_at: Optional[str] = Field(None, description="上传时间")
    note: str = Field("", description="该图纸的说明")


class MaterialWrite(BaseModel):
    """写入业务主数据的结果留痕。真数据在远程 Postgres，这里只留够回查的线索。"""
    material_id: str = Field("", description="md_clm_material_base_info.material_id（雪花 ID）")
    number: str = Field("", description="成品编码，规则 92022 + 3 位流水")
    name: str = Field("", description="产品名称")
    material_unit_price: Optional[float] = Field(
        None, description="写入 md_clm_material_cost_cnf.material_unit_price 的值"
                          "（材料+人工+制造费用+加工费用 的合计）")
    breakdown: Optional[dict] = Field(None, description="写入当时的四项成本明细")
    tables: StrList = Field(default_factory=list, description="实际写入的表")
    written_at: Optional[str] = None
    written_by: Optional[str] = None


class QuoteHandoff(BaseModel):
    """确认工艺并发送至报价的结果留痕。"""
    session_id: str = Field("", description="报价卡片的会话 ID（来自「新增工艺」时是原报价会话）")
    next_step_no: Optional[int] = Field(None, description="推送后卡片所处步骤（3=定价-利润加成）")
    next_step_name: str = ""
    target_role_name: str = Field("", description="任务发给了谁")
    # 从报价「新增工艺」过来的单子要原样退回给发起人，界面上得说清楚退给了谁。
    target_name: str = Field("", description="收件人姓名（定向退回时）")
    returned_to_sender: bool = Field(False, description="是否退回给了当初发起新增工艺的人")
    source_task_no: str = Field("", description="来源「新增工艺」任务编码")
    returned_sections: List[str] = Field(
        default_factory=list, description="随任务写回报价第 2 步快照的分区（产品信息/技术参数）")
    task_id: Optional[str] = None
    sent_at: Optional[str] = None
    sent_by: Optional[str] = None


class IntegrationPlan(BaseModel):
    project_id: Optional[str] = None
    requirement_note: str = Field("", description="用户在 2.2 输入的整合需求(参与参数推荐)")
    quantity: int = Field(1, description="成本测算的核算批量(台)")
    drawings: List[IntegrationDrawing] = Field(default_factory=list, description="整合图纸")
    params: Optional[IntegrationParamPlan] = Field(None, description="参数推荐结果")
    process: Optional[ProcessPlan] = Field(None, description="组装工艺路线")
    cost: Optional[CostAnalysis] = Field(None, description="整机成本测算")
    # 「参数推荐」的确认：整机参数与连接关系、BOM 由人核对过一遍才往下走。
    # 与 params_final 分开 —— 那个是最后交给报价前的收口，这个是这一环节本身的定稿。
    params_confirmed: bool = Field(False, description="参数推荐已确认")
    params_confirmed_by: Optional[str] = None
    params_confirmed_at: Optional[str] = None
    # 「整合参数」环节：成本测算之后的收口，确认报价要的成品参数都填齐了。
    # 单独一个标记而不是"必填都非空"就算完成 —— 补填是人做的判断，得有人按下确认，
    # 才谈得上"这份参数可以交给报价"。
    params_final: bool = Field(False, description="整合参数已确认（报价必填项已齐）")
    params_final_by: Optional[str] = None
    params_final_at: Optional[str] = None
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None
    # 两个对外动作的留痕。material_write 是列表：业务要求"每次产生一个新的成品编码"，
    # 所以重复写入会得到多个编码，只留最后一个就查不清历史了。
    material_writes: List[MaterialWrite] = Field(default_factory=list, description="历次写入主数据")
    quote_handoff: Optional[QuoteHandoff] = Field(None, description="最近一次发送至报价")
    timing: Timing = Field(default_factory=Timing)
    updated_at: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value: Any):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not isinstance(data.get("drawings"), list):
            data["drawings"] = []
        return data

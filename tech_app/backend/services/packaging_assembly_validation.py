"""隔离模型的坏行；落库前仍使用完整连接覆盖与严格工序校验。"""
import copy
from typing import Any, List
from pydantic import Field, model_validator
from ..models.process import ProcessPlan, ProcessStep
from ..models.ir import OpenQuestion
from . import packaging_assembly_connections as connections


class AssemblyProposal(ProcessPlan):
    steps: List[Any] = Field(default_factory=list, description='工序候选：每行包含step_no、name、type、description、part_codes、assembly_connection_ids、standard_seconds、depends_on；程序逐条校验')
    assembly_connections: List[Any] = Field(default_factory=list, description='连接候选：每行包含id、name、part_codes、method、evidence')
    open_questions: List[Any] = Field(default_factory=list, description='待补依据：field、reason，不影响其他正确行')

    @model_validator(mode='before')
    @classmethod
    def normalize_model_output(cls,value):
        if not isinstance(value,dict):return value
        data=dict(value)
        relations=data.pop('assembly_connections',[])
        data=ProcessPlan.normalize_model_output(data)
        data['assembly_connections']=relations
        return data


REASONS={
    'assembly_part_binding_invalid':'关联零件包含未知、重复或不足两个的编号',
    'assembly_connection_binding_invalid':'连接编号与关联零件不一致',
    'assembly_connection_method_conflict':'连接方式与已有接口冲突',
    'assembly_connection_evidence_invalid':'连接缺少有效零件编号或依据',
    'assembly_connection_too_generic':'连接名称过于笼统',
    'assembly_connection_unknown':'引用的连接编号不存在或该连接已被隔离',
    'assembly_connection_method_ambiguous':'同一组零件存在多个连接方式，未能唯一对应',
    'assembly_manufacturing_operation_repeated':'这是零件制造工序，不应重复作为新增组装收费',
    'assembly_operation_duplicated':'重复工序已隔离',
    'assembly_result_too_generic':'笼统总装不能代替具体零件连接',
    'assembly_connection_repeated':'连接已在制造阶段完成或重复计入',
    'assembly_connection_coverage_missing':'连接未对应到可校验的组装工序',
}


def issue(stage, index, raw, exc):
    code=str(exc) if isinstance(exc,ValueError) and str(exc) in REASONS else 'invalid_fields'
    return {'stage':stage,'index':index,'name':str(raw.get('name') or raw.get('id') or '')[:100] if isinstance(raw,dict) else '',
        'code':code,'reason':REASONS.get(code,'该条字段格式不正确，未影响其他有效结果')}


def isolate(raw, doc, records, known, validate):
    proposal=raw if isinstance(raw,AssemblyProposal) else AssemblyProposal.model_validate(
        raw.model_dump() if isinstance(raw,ProcessPlan) else raw)
    data=proposal.model_dump()
    metadata={k:copy.deepcopy(v) for k,v in data.items() if k not in ('steps','assembly_connections','open_questions')}
    metadata['summary']=metadata.get('summary') or '按当前零件连接逐项核对组装工序。'
    issues=[];accepted_connections=[];accepted_steps=[];questions=[]
    def checked(relations,steps):
        # 通用模型会从其他列表“抢救”steps；连接不是工序，禁止被抢救成制造工序。
        plan=ProcessPlan(**copy.deepcopy(metadata))
        plan.assembly_connections=copy.deepcopy(relations)
        plan.steps=[ProcessStep.model_validate(copy.deepcopy(s)) for s in steps]
        plan=connections.enrich(plan,doc,records,known)
        return validate(plan,doc)
    for index,row in enumerate(data.get('assembly_connections') or [],1):
        try:
            if not isinstance(row,dict):raise ValueError('invalid_fields')
            checked(accepted_connections+[row],[])
            accepted_connections.append(row)
        except (ValueError,TypeError,KeyError) as exc:issues.append(issue('连接',index,row,exc))
    for index,row in enumerate(data.get('steps') or [],1):
        try:
            parsed=ProcessStep.model_validate(row).model_dump()
            # 先独立检查业务范围，防止enrich静默过滤制造工序/错误绑定。
            validate(ProcessPlan(steps=[parsed]),doc)
            checked(accepted_connections,accepted_steps+[parsed])
            accepted_steps.append(parsed)
        except (ValueError,TypeError,KeyError) as exc:issues.append(issue('工序',index,row,exc))
    result=checked(accepted_connections,accepted_steps)
    for index,row in enumerate(data.get('open_questions') or [],1):
        try:questions.append(OpenQuestion.model_validate(row))
        except (ValueError,TypeError) as exc:issues.append(issue('待补项',index,row,exc))
    result.open_questions=questions+[OpenQuestion(field=f"{r['stage']}第{r['index']}项",reason=r['reason']) for r in issues]
    return result,issues

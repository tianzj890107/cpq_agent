"""Explicit demo pipeline. Automatic adoption is not human approval or publication."""
import copy
import re
import hashlib
from . import packaging_demo_inputs,packaging_parts,packaging_match,packaging_bom,packaging_route
from . import packaging_cost,packaging_cost_ledger,packaging_handoff
from ..storage import store
from ..storage.meta_backend import get_backend

def _missing_input(value):
    return value is None or (isinstance(value,str) and value.strip() in
        ('','待人工确认','待确认','待填写','未填写','未填','未知','暂无','—','-'))

def requirement_defaults(requirement):
    from ..storage import kb_repo
    result=copy.deepcopy(requirement)
    data=result.setdefault('data',{})
    from . import requirement_service
    data=requirement_service.resolved_packaging_order_data(data)
    result['data']=data
    boxes=kb_repo.packaging_box_types()
    preferred=data.get('box_type')
    box=next((row for row in boxes if row.get('box_type_code')==preferred),None)
    if box is None:
        choices=packaging_match.match_box_types(data).get('candidates') or []
        choices=[row for row in choices if not row.get('out_of_range')]
        choices.sort(key=lambda row:(-float(row.get('total_score') or 0),str(row.get('box_type_code'))))
        code=choices[0].get('box_type_code') if choices else None
        box=next((row for row in boxes if row.get('box_type_code')==code),{})
    defaults={'quote_quantity':1000,'fit_clearance':box.get('fit_clearance') or 1.5,
        'closure_type':box.get('closure_type') or '天地盖','v_groove':box.get('v_groove') or '否',
        'box_type':box.get('box_type_code') or '', 'packaging_product_name':box.get('name') or '包装演示项目',
        'packaging_category':'礼盒'}
    gsm=[float(x) for x in re.findall(r'\d+(?:\.\d+)?',str(box.get('face_paper_gsm') or '200'))]
    defaults['face_paper_gsm']=sum(gsm)/len(gsm)
    for name,axis in [('inner_length','l'),('inner_width','w'),('inner_height','h')]:
        lo,hi=box.get('size_'+axis+'_min'),box.get('size_'+axis+'_max')
        defaults[name]=(float(lo)+float(hi))/2 if lo is not None and hi is not None else 100
    assumptions=dict(data.get('demo_assumptions') or {})
    for key,value in defaults.items():
        original=data.get(key)
        if _missing_input(original):
            data[key]=value
            assumptions[key]={'value':value,'source':'demo_template_or_generic_default',
                              'box_type_code':box.get('box_type_code'),'mock':True,'original_value':original}
    data.update(poc_demo=True,demo_assumptions=assumptions)
    return result

def enabled(project_id):
    requirement=store.load_requirement(project_id) or {}
    doc=packaging_parts.load_business_parts(project_id) or {}
    return ((requirement.get('data') or {}).get('industry')=='packaging'
            and doc.get('derived_from_drawing') is True and doc.get('auto_geometry_inputs') is not False)

def adopt_box(project_id, actor, requirement):
    req_no=requirement.get('requirement_no') or ''
    match=packaging_match.load_box_match(project_id,req_no)
    if match.get('decision')=='confirmed': return
    match=packaging_match.run_box_match(project_id,req_no)
    choices=[r for r in match.get('candidates') or [] if r.get('can_confirm') and r.get('status')=='matched']
    if not choices: raise ValueError('demo_box_match_missing_or_conflicting_inputs')
    choices.sort(key=lambda r:(-float(r.get('total_score') or 0),str(r.get('box_type_code'))))
    preferred=(requirement.get('data') or {}).get('box_type')
    chosen=next((r for r in choices if r.get('box_type_code')==preferred),choices[0])
    packaging_match.decide_box_match(project_id,req_no,'confirmed',chosen['box_type_code'],
        actor=actor,note='自动采用匹配结果，非人工审核')

def ensure_inputs(project_id, actor):
    """原有入口内部准备，不增加按钮；同一候选版本不重复改写。"""
    if not enabled(project_id): return False
    def identity(doc):
        import json
        values=[{'code':row.get('business_part_code'),'name':row.get('name'),'binding':row.get('geometry_binding'),
                 'confirmed_size':row.get('confirmed_size'),'reference':row.get('reference'),
                 'usage_qty':row.get('usage_qty'),'semantic_material':row.get('semantic_material'),
                 'sections':[{k:s.get(k) for k in ('section_id','name','material_text','quantity','confirmed_size','automatic_size','entity_ids')}
                             for s in row.get('sections') or []]}
                for row in doc.get('business_parts') or []]
        layout=[{k:r.get(k) for k in ('entity_id','text','raw_text','scheme_id','sheet_mm','machine_mm','part_codes','part_counts')}
                for r in doc.get('layout_rows') or []]
        return hashlib.sha256(json.dumps({'parts':values,'layout':layout,'scheme':doc.get('selected_scheme_id')},sort_keys=True,default=str).encode()).hexdigest()
    doc=packaging_parts.load_business_parts(project_id)
    data=(store.load_requirement(project_id) or {}).get('data') or {}
    required=('quote_quantity','packaging_product_name','packaging_category','box_type',
              'inner_length','inner_width','inner_height','closure_type','v_groove','face_paper_gsm','fit_clearance')
    if (doc.get('demo_prepared_geometry_hash')==identity(doc)
            and doc.get('demo_inputs_version')==packaging_demo_inputs.ENGINE_VERSION
            and all(not _missing_input(data.get(key)) for key in required)): return True
    requirement=requirement_defaults(store.load_requirement(project_id) or {})
    requirement['data']['poc_auto_no_manual']=True
    store.save_requirement(project_id,requirement,author=actor.get('username',''))
    adopt_box(project_id,actor,requirement)
    packaging_demo_inputs.prepare(project_id,actor=actor.get('username',''),enable_demo=True)
    prepared=packaging_parts.load_business_parts(project_id)
    prepared['demo_prepared_geometry_hash']=identity(prepared)
    packaging_parts.save_business_parts(project_id,prepared)
    sync_integration(project_id,actor)
    return True

def sync_integration(project_id, actor, route=None):
    from . import integration,product_params
    from ..models.integration import IntegrationParam,IntegrationParamPlan
    from ..models.process import ProcessPlan,ProcessStep,ProcessType
    from ..time_utils import now_cst_str
    data=(store.load_requirement(project_id) or {}).get('data') or {}
    plan=integration.load_plan(project_id)
    if plan.params is None:
        plan.params=IntegrationParamPlan(assembly_name=data.get('packaging_product_name') or project_id,
            product_family=product_params.family_for_industry('packaging'),
            summary='包装需求与估算结果自动采用，非人工审核')
    plan.params.product_family=product_params.family_for_industry('packaging')
    existing={p.param_code:p for p in plan.params.params}
    for field in product_params.packaging_family_fields():
        code=field['code'];value=data.get(code)
        if value is None or value=='':continue
        if code in existing and existing[code].value and existing[code].source not in ('需求','自动采用','demo自动采用','工程推荐','模型推荐'):
            continue
        param=IntegrationParam(name=field['name'],param_code=code,value=str(value),
            source='自动采用' if code in (data.get('demo_assumptions') or {}) else '需求',
            basis='需求单/演示默认值；估算来源保留',confidence=.5)
        if code in existing:plan.params.params.remove(existing[code])
        plan.params.params.append(param)
    product_params.align(plan.params,product_params.family_for_industry('packaging'))
    complete=not product_params.missing_required(plan.params,product_params.family_for_industry('packaging'))
    plan.params_confirmed=complete;plan.params_final=complete
    if complete:
        plan.params_confirmed_by=plan.params_final_by='自动采用'
        plan.params_confirmed_at=plan.params_final_at=now_cst_str()
    # The full route belongs to billing; integration.process stores only cross-part assembly.
    plan.quantity=int(float(data.get('quote_quantity') or 1000))
    integration.save_plan(project_id,plan,actor.get('username',''))
    store.audit(project_id,'packaging_demo_integration_auto_adopted',{'by':actor.get('username'),
        'human_confirmed':False,'parameters_complete':complete,'route_adopted':bool(route)})
    return plan

def prepare_names(project_id, actor):
    """异步业务任务内部调用，禁止塞进同步读/提交前置校验。"""
    if not enabled(project_id):return False
    from . import packaging_name_assist, tasks
    tasks.report_progress('按已有材料、尺寸与方案核对排模名称；未命中时请求语义建议')
    doc=packaging_name_assist.prepare(project_id,actor.get('username',''))
    state=doc.get('name_assist') or {}
    label={'not_needed':'无需语义补充','completed':'候选核对完成','unavailable':'模型不可用，保留规则结果'}.get(state.get('status'),'已读取规则结果')
    tasks.report_progress('名称关联：'+label+'；只采用现有候选，未生成尺寸或费率')
    ensure_inputs(project_id,actor)
    return True


def prepare_route(project_id,actor, *, regenerate_assembly=False, note='', attachments=None, prepare_associations=True):
    ensure_inputs(project_id,actor)
    from . import packaging_process_instances
    from . import tasks
    collected=packaging_process_instances.collect(project_id)
    if collected['missing'] or not collected['active']:
        tasks.report_progress(f'项目路线准备：仅补齐缺失工艺 {len(collected["missing"])} 件，其他有效工艺直接复用')
        generated=packaging_demo_inputs.prepare_processes(project_id,actor=actor.get('username',''))
        if generated['failures']:raise ValueError('demo_process_incomplete:'+str(generated['failures']))
    else:
        tasks.report_progress('全部零件工艺已齐，直接复用，不调用模型')
    from . import integration
    plan=integration.load_plan(project_id)
    tasks.report_progress('直接汇总零件已有工艺，跳过排模与新增组装')
    req_no=(store.load_requirement(project_id) or {}).get('requirement_no') or ''
    try:
        packaging_bom.build_bom(project_id,req_no)
        packaging_route.build_route(project_id,req_no)
        route=packaging_route.confirm_route(project_id,req_no,actor=actor)
    except (packaging_bom.BomError,packaging_route.RouteError,ValueError) as exc:
        plan.packaging_assembly_attempt.update(status='partial')
        plan.packaging_assembly_attempt.setdefault('issues',[]).append({'stage':'项目路线','index':1,
            'code':type(exc).__name__,'reason':'组装工序已保存，但项目路线/BOM更新未完成；后续成本需重新核对（'+type(exc).__name__+'）'})
        from . import integration
        integration.save_plan(project_id,plan,actor.get('username',''))
        tasks.report_progress('组装工序已保存，项目路线更新未完成；请查看工作区原因')
        return plan
    return sync_integration(project_id,actor,route)

def run(project_id, *, actor, run_model=None, progress=None):
    if actor.get('role') not in packaging_cost.COST_WRITE_ROLES:
        raise ValueError('demo_flow_forbidden_role')
    def report(text):
        if progress: progress(text)
    requirement=store.load_requirement(project_id) or {}
    if (requirement.get('data') or {}).get('industry')!='packaging':
        raise ValueError('demo_flow_requires_packaging')
    requirement=requirement_defaults(requirement)
    store.save_requirement(project_id,requirement,author=actor.get('username',''))
    req_no=requirement.get('requirement_no') or ''
    report('自动采用盒型匹配，保留演示来源')
    adopt_box(project_id,actor,requirement)
    report('补齐演示输入，并自动生成逐件工艺')
    ensure_inputs(project_id,actor)
    from . import packaging_process_instances
    collected=packaging_process_instances.collect(project_id)
    processes=({'ready':True,'failures':[],'reused':True,'results':[]}
               if collected['active'] and not collected['missing'] else
               packaging_demo_inputs.prepare_processes(project_id,actor=actor.get('username',''),run=run_model))
    if processes['failures']: raise ValueError('demo_process_incomplete:'+str(processes['failures']))
    report('自动展开BOM、生成并采用项目路线')
    packaging_bom.build_bom(project_id,req_no)
    packaging_route.build_route(project_id,req_no)
    packaging_route.confirm_route(project_id,req_no,actor=actor)
    sync_integration(project_id,actor,packaging_route.load_route(project_id,req_no))
    report('同一公式账计算全部零件与项目成本')
    packaging_cost.build_cost(project_id,req_no,actor=actor)
    projection=packaging_cost_ledger.load(project_id)
    if not projection['ready']: raise ValueError('demo_cost_incomplete:'+str(projection['counts'])+str(projection['reconciliation_errors']))
    report('生成交接包与报价草稿，不发布正式报价')
    package=packaging_handoff.handoff_package(project_id,req_no)
    import cpq_packaging_quote
    margin=(requirement.get('data') or {}).get('gross_margin_rate')
    if margin is None: margin=.25
    quote=cpq_packaging_quote.price(package,gross_margin_rate=margin,
        quote_quantity=(requirement.get('data') or {}).get('quote_quantity'),actor=actor,publish=False)
    quote.update(demo=True,draft=True,publish_blocked=True)
    quote['pricing_assumptions']={'gross_margin_rate':margin,'source':'requirement' if
        (requirement.get('data') or {}).get('gross_margin_rate') is not None else 'demo_default'}
    result={'ready':True,'demo':True,'project_id':project_id,'processes':processes,
        'cost_projection':projection,'handoff_package':package,'quote_draft':quote,
        'quote_document':cpq_packaging_quote.document(quote,publish=False),
        'automatic_adoption':True,'human_confirmed':False}
    report('演示完成：%s件有成本，未税单价%.2f元；估算草稿，非正式报价' %
           (projection['counts']['parts_costed'],quote['untaxed_unit_price']))
    history=get_backend().get_doc(project_id,'packaging_demo_flow') or {'items':[]}
    history['items'].append(result)
    get_backend().put_doc(project_id,'packaging_demo_flow',history)
    store.audit(project_id,'packaging_demo_auto_completed',{'by':actor.get('username'),
        'estimate_id':projection.get('estimate_id'),'automatic_adoption':True,'human_confirmed':False})
    return result

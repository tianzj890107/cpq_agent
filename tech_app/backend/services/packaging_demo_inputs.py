# -*- coding: utf-8 -*-
"""通用演示输入装配：图纸几何 + 相似度推荐 → 逐件的开料尺寸 / 材料 / 单价 / 工时。

用途：让"零件拆出来了但成本算不出来"这条路在不伪造**确认**的前提下能跑通。
它不是盒型专用脚本、也不认任何一份图纸的名字：尺寸来自这一件绑定的几何分量，
材料来自这一件的图纸材料原文（缺失时按件名关键词**推定并披露**），
单价优先取库内材料价、取不到才按材质族给演示价，**每一条 mock 都写在结论里**。

三条纪律：
  · 不伪造人工确认：写的是 `poc_inputs`（演示输入），**不写** `confirmed_size`；
  · 不针对单一图纸：没有任何"某一份图纸 / 某一个件名"的硬编码分支，换一份图同样走这套；
  · 只读 + 显式落库：`build()` 是纯读，`prepare()` 才写，且写的是业务部件文档的新版本。
"""
from __future__ import annotations

import copy
import json
import math
from typing import Any, Dict, List, Optional, Tuple

from . import packaging_material_match as material_match

ENGINE_VERSION = "packaging-demo-inputs/4"

#: 件名关键词 → 材料族（**仅用于图纸没写材料时推定**，且必须随结论披露）。通用规则，不含图号。
NAME_FAMILY_HINTS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("greyboard", ("灰板", "衬板", "纸板", "盒背", "背板", "底板", "隔板")),
    ("face_paper", ("面纸", "封面", "铜版", "印刷面")),
    ("liner_paper", ("衬纸", "内衬", "包边", "内卡")),
    ("specialty", ("特种", "充皮", "触感")),
    ("eva", ("EVA", "内托", "泡棉")),
    ("magnet", ("磁铁", "磁石", "磁片")),
    ("tube", ("纸管", "圆管")),
)

#: 材料原文与件名都推不出克重时的族默认（g/㎡）：只在推导链全断时用，逐条披露。
FAMILY_DEFAULT_GRAMMAGE: Dict[str, float] = {
    "greyboard": 0.0, "face_paper": 157.0, "white_card": 300.0, "liner_paper": 80.0,
    "specialty": 120.0, "corrugated": 300.0, "tube": 250.0, "eva": 0.0,
}
#: 灰板这类按厚度计的材料：推不出克重时给族默认厚度（mm），配合密度换算。
FAMILY_DEFAULT_THICKNESS: Dict[str, float] = {"greyboard": 2.0, "eva": 10.0}

#: 演示排模/校版口径（非生产参数）：认不到时给这两个值，并逐条披露。
DEMO_IMPOSITION = 1.0
DEMO_PROOF_BASE = 50.0
DEMO_LABOR_RATE = 36.0
DEMO_STANDARD_SECONDS = 30.0
DEMO_OVERHEAD_SECONDS = 0.0
DEMO_OVERHEAD_RATE = 0.0


def _text(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def geometry_sizes(parts_doc: Any) -> Dict[str, Dict[str, Any]]:
    """几何零件文档 → `{component_id: {length_mm, width_mm, size_source}}`（纯读、纯函数）。

    件清单尺寸（`unfolded_length_mm/width_mm`，闭合轮廓环算出来的）优先，缺失才退回 `bbox`
    —— 与 `packaging_parts._component_size()` 同一条口径，这里不另立一套。
    """
    doc = parts_doc if isinstance(parts_doc, dict) else {}
    out: Dict[str, Dict[str, Any]] = {}
    for row in (doc.get("parts") or []):
        if not isinstance(row, dict):
            continue
        code = _text(row.get("component_id"))
        if not code:
            continue
        length = _num(row.get("unfolded_length_mm"))
        width = _num(row.get("unfolded_width_mm"))
        source = "unfolded_size"
        if not (length and width):
            bbox = row.get("bbox")
            if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
                try:
                    length = abs(float(bbox[2]) - float(bbox[0]))
                    width = abs(float(bbox[3]) - float(bbox[1]))
                    source = "component_bbox"
                except (TypeError, ValueError):
                    length = width = None
        if length and width and length > 0 and width > 0:
            out[code] = {"length_mm": round(length, 3), "width_mm": round(width, 3),
                         "size_source": source}
    return out


def _component_ids(row: Any) -> List[str]:
    record = row if isinstance(row, dict) else {}
    binding = record.get("geometry_binding") if isinstance(record.get("geometry_binding"), dict) else {}
    ids = [_text(item) for item in (binding.get("component_ids") or []) if _text(item)]
    if ids:
        return ids
    for section in (record.get("sections") or []):
        if isinstance(section, dict):
            ids.extend(_text(item) for item in (section.get("component_ids") or []) if _text(item))
    return ids


def part_size(row: Any, sizes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """一件的开料长宽：按绑定分量的**逐轴最大值**取包络（多分量件因此能给整件尺寸）。

    取不到就返回空 —— 不猜、不给默认值。`component_ids` 一并带回，供结论披露。
    """
    ids = _component_ids(row)
    lengths, widths, sources = [], [], []
    for code in ids:
        item = sizes.get(code) or {}
        length, width = _num(item.get("length_mm")), _num(item.get("width_mm"))
        if length and width:
            lengths.append(length)
            widths.append(width)
            sources.append(_text(item.get("size_source")))
    if not lengths:
        return {"length_mm": None, "width_mm": None, "size_source": "", "component_ids": ids}
    return {"length_mm": round(max(lengths), 3), "width_mm": round(max(widths), 3),
            "size_source": sources[0] if sources else "", "component_ids": ids}


def material_text_of(row: Any) -> Dict[str, Any]:
    """这一件的材料原文与来源：图纸材料原文 → 组成材料原文 → 按件名推定（披露）。"""
    record = row if isinstance(row, dict) else {}
    reference = record.get("reference") if isinstance(record.get("reference"), dict) else {}
    for key in ("reference", "authority"):
        block = record.get(key) if isinstance(record.get(key), dict) else {}
        text = _text(block.get("material_text"))
        if text:
            return {"material_text": text, "source": "drawing_text" if key == "reference" else key}
    for section in (record.get("sections") or []):
        if isinstance(section, dict) and _text(section.get("material_text")):
            return {"material_text": _text(section.get("material_text")), "source": "drawing_text"}
    inferred = infer_material_text(_text(record.get("name")))
    if inferred:
        return {"material_text": inferred, "source": "inferred_from_part_name"}
    return {"material_text": _text(reference.get("material_text")), "source": ""}


def infer_material_text(name: Any) -> str:
    """件名 → 材料原文（图纸没写材料时的**推定**，通用关键词规则，不含图号/客户名）。"""
    from .packaging_part_route_match import normalize_part_name
    text = normalize_part_name(name).upper()
    if not text:
        return ""
    for family, words in NAME_FAMILY_HINTS:
        if any(word.upper() in text for word in words):
            if family == "greyboard":
                return "%smm灰板" % FAMILY_DEFAULT_THICKNESS.get("greyboard", 2.0)
            if family == "eva":
                return "EVA"
            if family == "magnet":
                return "磁铁"
            if family == "tube":
                return "纸管"
            if family == "face_paper":
                return "面纸"
            if family == "liner_paper":
                return "衬纸"
            return "特种纸"
    return ""


def _kb_materials() -> List[Dict[str, Any]]:
    try:
        from ..storage import kb_repo
        return [dict(row) for row in kb_repo.list_materials(industry="packaging")]
    except Exception:                                                # noqa: BLE001 - 库不可用就退演示价
        return []


def _kb_price(material_code: str) -> Dict[str, Any]:
    if not material_code:
        return {}
    try:
        from ..storage import kb_repo
        row = kb_repo.current_price(material_code, industry="packaging") or {}
    except Exception:                                                # noqa: BLE001
        return {}
    price = _num(row.get("price"))
    unit = _text(row.get("unit"))
    if price is None:
        return {}
    if unit.lower() == "kg":
        return {"unit_price": price, "unit": unit, "source": "kb_material_price",
                "price_id": _text(row.get("price_id"))}
    return {"unit_price": None, "unit": unit, "source": "kb_material_price_unit_mismatch",
            "price_id": _text(row.get("price_id"))}


def _kb_tax_factor() -> float:
    try:
        from ..storage import kb_repo
        for row in kb_repo._table("kb_cost_factor"):
            if _text(row.get("factor_code")) == "F-PKG-TAX-VAT":
                value = _num(row.get("value"))
                return 1.0 + value if value is not None else 1.13
    except Exception:                                                # noqa: BLE001
        pass
    return 1.13


def demo_size(row, sizes):
    """仅演示入口：保留真实尺寸；缺失使用明示占位，绝不写CAD确认。"""
    confirmed=row.get('confirmed_size') or {}
    length,width=_num(confirmed.get('length_mm')),_num(confirmed.get('width_mm'))
    if length and width and length>0 and width>0:
        return {'length_mm':length,'width_mm':width,'size_source':'confirmed_size','component_ids':_component_ids(row)}
    binding=row.get('geometry_binding') or {}
    if binding.get('status')=='bound' and binding.get('entity_ids'):
        from . import packaging_parts
        selected=packaging_parts._automatic_selected_size(binding,binding['entity_ids'])
        if selected:
            return {'length_mm':selected['length_mm'],'width_mm':selected['width_mm'],
                    'size_source':selected['source'],'component_ids':_component_ids(row)}
    for key in ('confirmed_size', 'reference', 'authority'):
        value=row.get(key) or {}
        length,width=_num(value.get('length_mm')),_num(value.get('width_mm'))
        if length and width and length>0 and width>0:
            return {'length_mm':length,'width_mm':width,'size_source':key,'component_ids':_component_ids(row)}
    value=part_size(row,sizes)
    if value.get('length_mm') and value.get('width_mm'): return value
    return {**value,'length_mm':100.,'width_mm':100.,'size_source':'demo_placeholder_not_cad'}


def demo_process_plan(part, lookup, inputs, *, run=None, allow_fallback=False, note='', attachments=None):
    from . import packaging_manufacturing, llm_client, packaging_process_instances, packaging_cost
    lookup=json.loads(json.dumps(lookup,ensure_ascii=False,default=str))
    try:
        plan,coverage=packaging_manufacturing.outline(part,lookup=lookup,
            note='演示项目：依据本件材料和尺寸适配工序，不增加无依据表面处理。'+str(inputs)+'\n'+note,
            attachments=attachments,
            run=run or llm_client.run)
    except Exception as exc:
        if not allow_fallback: raise
        fallback={'status':'matched','match_method':'demo_rule','routes':[{'steps':[
            {'line_number':10,'operation_name':'开料'}, {'line_number':20,'operation_name':'检验'}]}]}
        plan,coverage=packaging_manufacturing.outline(part,lookup=fallback)
        coverage.update(process_origin='demo_rule_fallback',model_failure=type(exc).__name__,
                        original_lookup=lookup)
        plan.overall_note+='；模型不可用，演示基础工序估算，未声称AI推荐成功'
    payload=plan.model_dump()
    for step in payload['steps']:
        decision=packaging_process_instances.billing({'step_name':step.get('name'),
            'formula_code':step.get('formula_code')})
        if decision['gap']:
            step['formula_code']='PKG-C-LABOR'
            step['note']=(step.get('note') or '')+'；无专用计费公式，演示按工时估算，不是标准加工单价'
        parameters=dict(step.get('cost_parameters') or {})
        parameters.update(mock=True,source='demo_process_parameters',
            machine_length=inputs.get('length_mm'),machine_width=inputs.get('width_mm'),
            imposition_count=1.,labor_rate=DEMO_LABOR_RATE)
        if step.get('standard_seconds') is None:
            step.update(standard_seconds=DEMO_STANDARD_SECONDS,duration_min=DEMO_STANDARD_SECONDS/60,
                        time_source='poc_mock')
        parameters['labor_seconds']=step['standard_seconds']
        parameters.setdefault('hot_area_mm2',(_num(inputs.get('length_mm')) or 100)*(_num(inputs.get('width_mm')) or 100)*.1)
        step['cost_parameters']=parameters
    payload['overall_note']+='；演示估算：未知工时30秒、人工36元/小时、单排；非生产标准'
    return payload,coverage


def _usable_demo_plan(plan):
    return bool((plan or {}).get('steps')) and plan.get('part_class') not in ('machining','sheet_metal','welded')


def _section_process_input(row, section):
    """Only generation inputs; never reuse solely by the section's display name."""
    return {'section':section, 'parent_reference':row.get('reference') or {}, 'parent_name':row.get('name'),
            'parent_inputs':row.get('poc_inputs') or {},
            'parent_requirement':row.get('process_requirements') or {}}


def _merge_demo_sections(part, children):
    steps, reused, missing = [], 0, 0
    for child in children:
        source_steps=(child.get('plan') or {}).get('steps') or []
        numbers={s['step_no']:10*(len(steps)+i+1) for i,s in enumerate(source_steps)}
        for source in source_steps:
            step=copy.deepcopy(source)
            step['original_step_no']=source['step_no']
            step['step_no']=numbers[source['step_no']]
            step['depends_on']=[numbers.get(n,n) for n in source.get('depends_on') or []]
            step['section_id']=child['section_id'];step['section_name']=child['section_name']
            step['name']=child['section_name']+' · '+str(source.get('name') or '')
            if isinstance(step.get('duration_min'),(int,float)):
                step['original_duration_min']=step['duration_min']
                step['duration_min']*=child['quantity']
            steps.append(step)
        summary=(child.get('coverage') or {}).get('summary') or {}
        reused+=summary.get('reused') or 0;missing+=summary.get('missing') or 0
    return ({'part_id':part.part_id,'part_name':part.name,'part_class':'packaging',
        'steps':steps,'overall_note':'汇总各组成工艺，不额外生成父件工艺',
        'open_questions':[q for c in children for q in (c.get('plan') or {}).get('open_questions') or []]},
        {'summary':{'total':len(steps),'reused':reused,'missing':missing},
         'sections':[c.get('coverage') or {} for c in children]})


def prepare_processes(project_id, *, actor='', run=None):
    """显式演示准备：原生路线优先，未知件走现有相似检索+LLM，失败不伪报完成。"""
    from . import packaging_parts, packaging_layout, da_process_routing, packaging_process_instances, tasks
    from .packaging_part_route_match import is_external_part
    doc=packaging_parts.load_business_parts(project_id) or {}
    if doc.get('poc_demo') is not True:
        raise ValueError('demo_project_required')
    doc=packaging_layout.active_business_document(doc)
    collected=packaging_process_instances.collect(project_id)
    valid={r['part_code'] for r in collected['records']}
    results,failures=[],[]
    for row in doc.get('business_parts') or []:
        if is_external_part(row): continue
        code=row.get('business_part_code')
        if code in valid:
            tasks.report_progress(f'复用已有工艺：{code} · {row.get("name") or ""}')
            results.append({'part_code':code,'reused':True})
            continue
        try:
            tasks.report_progress(f'补齐缺失或失效工艺：{code} · {row.get("name") or ""}')
            effective=packaging_parts.verified_business_part_input_row(row,doc)
            part=packaging_parts.business_as_ir_part(effective)
            lookup=da_process_routing.for_row(effective)
            context=packaging_parts.business_process_inputs(effective)
            # Whole-part matching/model generation; portions are evidence, not extra operation instances.
            scope_note='以完整业务零件为工艺分析单位；各组成仅为材料、尺寸、用量依据，不按组成分别推荐或串接工序。'
            composition=[]
            for section in effective.get('sections') or []:
                size=section.get('confirmed_size') or section.get('automatic_size') or section.get('poc_inputs') or section.get('estimated_size') or {}
                composition.append({'name':section.get('name'),'material':section.get('material_text'),
                    'quantity':section.get('quantity'),'size':{k:size.get(k) for k in ('length_mm','width_mm','source','size_source')}})
            grounding=context.get('grounding') or json.dumps({'name':effective.get('name'),
                'reference':effective.get('reference'),'composition':composition},ensure_ascii=False)
            inputs=row.get('poc_inputs') or {}
            plan,coverage=demo_process_plan(part,lookup,inputs,run=run,allow_fallback=True,
                note=scope_note+'\n'+grounding)
            packaging_parts.save_part_process(project_id,{
                'part_code':code,'plan':plan,'coverage':coverage,'sections':[],
                'process_scope':packaging_process_instances.BUSINESS_PART_PROCESS_SCOPE,
                'business_parts_hash':doc.get('business_parts_hash'),
                'business_parts_id':doc.get('business_parts_id'),
                'process_origin':coverage.get('process_origin'),
                'cost_parameters':{'mock':True,'source':'demo_process_parameters'},
                'actor':actor,'mock':True})
            results.append({'part_code':code,'match_method':lookup.get('match_method'),
                            'process_origin':coverage.get('process_origin')})
        except Exception as exc:
            failures.append({'part_code':code,'error':str(exc)})
    tasks.report_progress(f'零件工艺准备完成：复用 {sum(bool(r.get("reused")) for r in results)} 件，补齐 {sum(not r.get("reused") for r in results)} 件，失败 {len(failures)} 件')
    return {'mock':True,'results':results,'failures':failures,'ready':not failures}


def prepare_demo_layouts(business):
    """只补明文排模的缺失参数；草稿候选不自动升格为实际排模。"""
    from . import packaging_sheet_cost
    doc=copy.deepcopy(business)
    parts={row.get('business_part_code'):row for row in doc.get('business_parts') or []}
    for layout in doc.get('layout_rows') or []:
        if layout.get('kind')=='drawing_layout_candidate': continue
        from .packaging_layout import refresh_text_sizes
        layout.update(refresh_text_sizes(layout))
        codes=layout.get('part_codes') or []
        if not codes or any(code not in parts for code in codes): continue
        counts=dict(layout.get('part_counts') or {})
        changed=False
        for code in codes:
            if not counts.get(code):
                counts[code]=layout.get('n_up') if len(codes)==1 and layout.get('n_up') else 1
                changed=True
        layout['part_counts']=counts
        if not layout.get('sheet_mm') or layout.get('dimension_role') in ('machine_sheet','unclassified'):
            if layout.get('sheet_mm'):
                layout['demo_original_sheet_candidate']=copy.deepcopy(layout['sheet_mm'])
            cuts=[packaging_sheet_cost.individual_cut(parts[code].get('poc_inputs') or {},{}) for code in codes]
            if all(all(size.get('sheet_mm') or []) for size in cuts):
                layout.update(sheet_mm=[sum(cut['sheet_mm'][0]*counts[code] for cut,code in zip(cuts,codes)),
                                        max(cut['sheet_mm'][1] for cut in cuts)],
                    size_source='demo_horizontal_sheet_estimate',dimension_role='cut_sheet')
                changed=True
        if changed:
            layout.update(mock=True,demo_note='演示排模：未知排出数量按1，缺开料尺寸按横排包络估算；不是原图纸张标注')
    return doc


def demo_inputs_for(row: Any, sizes: Dict[str, Dict[str, Any]], *,
                    materials: Optional[List[Dict[str, Any]]] = None,
                    allow_estimates: bool = False) -> Dict[str, Any]:
    """一件的演示输入（纯函数，不写库）。`ok=False` 时 `reason` 说清缺什么。"""
    record = row if isinstance(row, dict) else {}
    materials = materials if materials is not None else _kb_materials()
    size = demo_size(record, sizes) if allow_estimates else part_size(record, sizes)
    material = material_text_of(record)
    text = _text(material.get("material_text"))
    if allow_estimates and not text:
        text='未识别材料（估算）'
        material={'material_text':text,'source':'demo_unknown_material'}
    if not (size["length_mm"] and size["width_mm"]):
        return {"ok": False, "reason": "no_geometry_size",
                "message": "这一件没有可用的几何尺寸（未绑定分量），演示输入给不出开料长宽",
                "material_text": text, "component_ids": size["component_ids"]}
    recommendation = material_match.recommend_materials(text, materials) if text else {
        "status": "no_candidate", "candidate": None, "candidates": [], "confidence": 0.0}
    semantic = record.get('semantic_material') or {}
    if semantic:
        from .packaging_name_assist import material_context
        candidate = next((m for m in materials if m.get('material_code') == semantic.get('material_code')), None)
        explicit = (record.get('reference') or {}).get('material_text') or (record.get('authority') or {}).get('material_text')
        if candidate and not explicit and semantic.get('context') == material_context(record, materials):
            text = candidate['name']
            material = {'material_text':text,'source':'ai_semantic'}
            recommendation = {'status':'ai_semantic','candidate':candidate,'candidates':[candidate],
                              'confidence':semantic.get('confidence'),'reason':semantic.get('reason')}
    candidate = recommendation.get("candidate") or {}
    price = _kb_price(_text(candidate.get("material_code"))) if candidate else {}
    demo_price = material_match.demo_unit_price(text or _text(record.get("name")))
    unit_price = price.get("unit_price")
    price_source = price.get("source") or ""
    if unit_price is None:
        unit_price = demo_price["unit_price"]
        price_source = demo_price["source"] if not price else price.get("source")
    grammage = material_match.derive_grammage(text, material=candidate)
    if not grammage.get("grammage"):
        family = material_match.family_of(text) or demo_price.get("family") or ""
        thickness = FAMILY_DEFAULT_THICKNESS.get(family)
        if thickness:
            grammage = {"grammage": round(thickness * demo_price["density"] * 1000, 1),
                        "source": "derived_from_family_default_thickness"}
        elif FAMILY_DEFAULT_GRAMMAGE.get(family):
            grammage = {"grammage": FAMILY_DEFAULT_GRAMMAGE[family],
                        "source": "family_default_grammage"}
    notes: List[str] = []
    notes.append("演示输入：开料长宽按这一件绑定的几何分量包络（%s），不是人工确认尺寸"
                 % (size["size_source"] or "unknown"))
    if material.get("source") == "inferred_from_part_name":
        notes.append("图纸没有这一件的材料原文，按件名关键词推定为「%s」" % text)
    if material.get('source') == 'ai_semantic':
        notes.append('材料按库内候选语义推荐自动采用，不代表图纸明确标注：'+str(semantic.get('reason') or ''))
    if candidate:
        notes.append("材料按相似度推荐：%s → %s（相似度 %.2f，%s）"
                     % (text or record.get("name"), candidate.get("name"),
                        recommendation.get("confidence") or 0.0,
                        recommendation.get("status")))
    else:
        notes.append("材料库没有可用候选（%s），按材质族给演示单价" % recommendation.get("status"))
    if price_source == "demo_family_price":
        notes.append(demo_price["note"])
    if price_source == "kb_material_price_unit_mismatch":
        notes.append("库内这条价格的单位是「%s」，不是元/kg，本行按演示价算" % price.get("unit"))
    if not grammage.get('grammage') and allow_estimates:
        grammage={'grammage':200.,'source':'demo_unknown_material_200gsm'}
    if not grammage.get("grammage"):
        notes.append("克重推导不出来：这一行材料费给不出金额")
    if allow_estimates:
        notes.append('演示尺寸来源：'+size['size_source']+'；无图形占位100×100mm、未知材料200g/㎡，不是CAD实测')
    notes.append("模数 %s、校版 %s 张、工时费率 %s 元/小时、标准工时 %s 秒为演示口径"
                 % (DEMO_IMPOSITION, DEMO_PROOF_BASE, DEMO_LABOR_RATE, DEMO_STANDARD_SECONDS))
    return {
        "ok": bool(grammage.get("grammage")),
        "reason": "" if grammage.get("grammage") else "grammage_unavailable",
        "length_mm": size["length_mm"], "width_mm": size["width_mm"],
        "component_ids": size["component_ids"], "size_source": size["size_source"],
        "material_text": text, "material_source": material.get("source"),
        "recommendation": recommendation, "recommended_material": candidate,
        "grammage": grammage.get("grammage"), "grammage_source": grammage.get("source"),
        "unit_price": unit_price, "price_source": price_source,
        "note": "；".join(notes),
    }


def build(project_id: str, *, limit: int = 0) -> Dict[str, Any]:
    """纯读：算出这个项目每一件的演示输入与全项目的统计（不写任何文档）。"""
    from . import packaging_parts
    business = packaging_parts.load_business_parts(project_id) or {}
    geometry = packaging_parts.load_parts(project_id) or {}
    sizes = geometry_sizes(geometry)
    materials = _kb_materials()
    rows: List[Dict[str, Any]] = []
    for row in (business.get("business_parts") or []):
        if not isinstance(row, dict):
            continue
        if limit and len(rows) >= limit:
            break
        value = demo_inputs_for(row, sizes, materials=materials, allow_estimates=True)
        value.update({"business_part_code": _text(row.get("business_part_code")),
                      "name": _text(row.get("name"))})
        rows.append(value)
    ready = [row for row in rows if row.get("ok")]
    inferred = [row for row in rows if row.get("material_source") == "inferred_from_part_name"]
    recommended = [row for row in rows if row.get("recommended_material")]
    demo_priced = [row for row in rows if row.get("price_source") == "demo_family_price"]
    return {"version": ENGINE_VERSION, "project_id": project_id,
            "business_parts_id": _text(business.get("business_parts_id")),
            "geometry_component_total": len(sizes),
            "rows": rows, "row_total": len(rows),
            "ready_total": len(ready), "no_size": [row["business_part_code"] for row in rows
                                                   if row.get("reason") == "no_geometry_size"],
            "material_inferred_total": len(inferred),
            "material_recommended_total": len(recommended),
            "demo_price_total": len(demo_priced),
            "grammage_missing": [row["business_part_code"] for row in rows
                                 if row.get("ok") is False and row.get("reason") != "no_geometry_size"]}


def prepare(project_id: str, *, actor: str = "", enable_demo: bool = False) -> Dict[str, Any]:
    """把这个项目的演示输入写进业务部件文档（新版本），并按需打开项目的 `poc_demo` 开关。

    只写两处：业务部件文档的 `poc_demo` / `poc_inputs` / `cost_parameters`，以及需求单上的
    `poc_demo` 标志（`enable_demo=True` 时）。**不写 `confirmed_size`**，不删任何历史版本。
    """
    from . import packaging_parts
    from ..storage import store

    result = build(project_id)
    business = packaging_parts.load_business_parts(project_id) or {}
    if not business.get("business_parts"):
        raise ValueError("这个项目还没有业务部件清单（先跑图纸解析）")
    by_code = {row["business_part_code"]: row for row in result["rows"]}
    doc = copy.deepcopy(business)
    doc["poc_demo"] = True
    doc["demo_inputs_version"] = ENGINE_VERSION
    geometry_sizes_map=geometry_sizes(packaging_parts.load_parts(project_id) or {})
    tax_factor = _kb_tax_factor()
    written, skipped = 0, []
    for row in (doc.get("business_parts") or []):
        if not isinstance(row, dict):
            continue
        code = _text(row.get("business_part_code"))
        value = by_code.get(code)
        if not value or not value.get("ok"):
            skipped.append({"business_part_code": code,
                            "reason": (value or {}).get("reason") or "not_in_scope"})
            continue
        inputs = {
            "material_code": (value.get('recommended_material') or {}).get('material_code')
                if (value.get('recommendation') or {}).get('status') == 'ai_semantic' else '',
            "length_mm": value.get("length_mm"), "width_mm": value.get("width_mm"),
            "quantity":row.get('usage_qty') or (row.get('reference') or {}).get('quantity') or row.get('quantity') or 1,
            "material_text": value.get("material_text"),
            "gsm": value.get("grammage"),
            "ton_price": (value.get("unit_price") or 0) * 1000,
            "tax_factor": tax_factor,
            "imposition_count": DEMO_IMPOSITION, "proof_base": DEMO_PROOF_BASE,
            "mock": True, "source": "demo_similarity",
            "note": value.get("note"),
            "recommendation": {
                "material_code": (value.get("recommended_material") or {}).get("material_code"),
                "name": (value.get("recommended_material") or {}).get("name"),
                "score": (value.get("recommendation") or {}).get("confidence"),
                "status": (value.get("recommendation") or {}).get("status"),
                "grammage_source": value.get("grammage_source"),
                "price_source": value.get("price_source"),
                "material_source": value.get("material_source"),
                "component_ids": value.get("component_ids"),
                "size_source": value.get("size_source"),
            },
        }
        row["poc_inputs"] = inputs
        for section in row.get('sections') or []:
            child=dict(section,reference={'material_text':section.get('material_text') or value.get('material_text')},
                geometry_binding={'component_ids':section.get('component_ids') or []},
                confirmed_size=section.get('confirmed_size') or section.get('estimated_size') or {})
            child_value=demo_inputs_for(child,geometry_sizes_map,allow_estimates=True)
            section['poc_inputs']={**inputs,'quantity':section.get('quantity') or 1,
                'length_mm':child_value['length_mm'],'width_mm':child_value['width_mm'],
                'gsm':child_value['grammage'],'ton_price':child_value['unit_price']*1000,
                'material_text':child_value['material_text'],'note':child_value['note']}
        sections=row.get('sections') or []
        assigned=set((row.get('geometry_binding') or {}).get('entity_ids') or [])
        sets=[set(s.get('entity_ids') or []) for s in sections]
        if (sections and assigned and all(sets) and set().union(*sets)==assigned
                and sum(map(len,sets))==len(assigned)):
            for section in sections:
                if section.get('status')=='pending_attribution' and section.get('source')=='raw_cad_extra':
                    section['demo_original_status']='pending_attribution'
                    section['status']='bound'
                    section['demo_note']='演示按剩余CAD图元独立部分估算；实际物理组成未人工核实'
            if all(s.get('status')=='bound' for s in sections):
                row['sections_complete']=True
                row['sections_completion_source']='demo_entity_coverage'
                row['demo_composition_notes']=list(row.get('section_gaps') or [])
        row["usage_qty"] = row.get('usage_qty') or (row.get('reference') or {}).get('quantity') or 1
        row["cost_parameters"] = {**(row.get('cost_parameters') or {}),
            "mock":True, "source":"demo_process_parameters",
            "machine_length":value.get('length_mm'),"machine_width":value.get('width_mm'),
            "imposition_count":DEMO_IMPOSITION,
            "labor_rate": DEMO_LABOR_RATE, "labor_seconds": DEMO_STANDARD_SECONDS,
            "standard_seconds": DEMO_STANDARD_SECONDS,
            "overhead_seconds": DEMO_OVERHEAD_SECONDS, "overhead_rate": DEMO_OVERHEAD_RATE,
        }
        written += 1
    doc=prepare_demo_layouts(doc)
    saved = packaging_parts.save_business_parts(project_id, doc)
    if enable_demo:
        requirement = copy.deepcopy(store.load_requirement(project_id) or {})
        data = requirement.get("data") if isinstance(requirement.get("data"), dict) else {}
        data["poc_demo"] = True
        quantity=_num(data.get('quote_quantity')) or 1000
        for key,value in {'tooling_amortize_qty':quantity,'committed_volume':quantity,
                          'cumulative_quantity':quantity}.items():
            if data.get(key) is None: data[key]=value
        data['demo_preparation_source']='mock parameters; not production authority'
        requirement["data"] = data
        store.save_requirement(project_id, requirement, author=actor or "system")
    store.audit(project_id, "poc:demo_inputs_prepared",
                {"by": actor or "system", "version": ENGINE_VERSION, "written": written,
                 "material_recommended_total": result["material_recommended_total"],
                 "material_inferred_total": result["material_inferred_total"],
                 "demo_price_total": result["demo_price_total"], "mock": True})
    return {"version": ENGINE_VERSION, "project_id": project_id,
            "business_parts_id": _text(saved.get("business_parts_id")),
            "written": written, "skipped": skipped, "build": result}


__all__ = ["ENGINE_VERSION", "NAME_FAMILY_HINTS", "FAMILY_DEFAULT_GRAMMAGE",
           "FAMILY_DEFAULT_THICKNESS", "geometry_sizes", "part_size", "material_text_of",
           "infer_material_text", "demo_inputs_for", "build", "prepare"]

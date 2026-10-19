"""只读包装需求文档投影；样式模板不含客户样例数据，不进行模型或成本计算。"""
import hashlib
import json
import math
from decimal import Decimal, ROUND_HALF_UP

from . import industry_templates
from ..time_utils import now_cst_str

VERSION = 'packaging-requirement/2'
MISSING = '待确认'


def filled(value):
    if value is None: return False
    if isinstance(value, str): return value.strip() not in ('', '—', '--', '-', '待确认', '待补充', '待定')
    if isinstance(value, (list, dict, tuple)): return bool(value)
    if isinstance(value, (int, float, Decimal)): return math.isfinite(float(value))
    return True


def plain(value):
    if not filled(value): return MISSING
    if isinstance(value, bool): return '是' if value else '否'
    if isinstance(value, (dict, list)): return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def equivalent(left, right):
    try:
        a, b = float(left), float(right)
        return math.isfinite(a) and math.isfinite(b) and math.isclose(a, b, rel_tol=0, abs_tol=1e-9)
    except (ValueError, TypeError):
        return plain(left).strip() == plain(right).strip()


def source_label(origin):
    origin = str(origin or '')
    if origin in ('manual', 'user_confirmed', 'user_text', 'quote_text', 'quote_requirement', 'customer', 'user'):
        return '客户填写'
    if origin in ('attachment', 'cad_engineering_evidence', 'confirmed_from_cad', 'inferred_from_text'):
        return '图纸解析'
    if origin in ('ai_recommend', 'ai_extract', 'inferred_by_model', 'inferred_from_geometry', 'system', 'recommendation'):
        return '系统推荐'
    return MISSING


def build_snapshot(requirement, project, *, evidence=None, bom=None, processes=None, cost=None, availability=None):
    """所有输入均为已存事实的副本。返回中文展示模型，零/否不能变成缺失。"""
    data = requirement.get('data') or {}
    sources = data.get('field_sources') or {}
    provenance = data.get('field_provenance') or {}
    candidate_fields = (evidence or {}).get('fields') or {}
    if not isinstance(candidate_fields, dict): candidate_fields = {}
    gaps, conflicts, suggestions, unverified = [], [], [], []
    sections = []

    def row(key, label, *, value=None, explicit=False, required=False):
        selected = value if explicit else data.get(key)
        record = provenance.get(key) or {}
        cad = candidate_fields.get(key) or {}
        if not isinstance(record, dict): record = {}
        if not isinstance(cad, dict): cad = {}
        origin = sources.get(key)
        if origin != 'manual' and record.get('origin') and equivalent(record.get('value'), selected):
            origin = record['origin']
        if not filled(selected) and filled(cad.get('value')):
            selected, origin = cad['value'], cad.get('origin')
        label_source = source_label(origin) if filled(selected) else MISSING
        alternatives = []
        for entry in [record, cad, *(record.get('alternatives') or []), *(cad.get('alternatives') or [])]:
            if not isinstance(entry, dict) or not filled(entry.get('value')): continue
            candidate_source = source_label(entry.get('origin') or entry.get('source'))
            if candidate_source == MISSING or (entry is record and candidate_source == '客户填写'): continue
            if not equivalent(entry['value'], selected):
                pair = {'value': plain(entry['value']), 'source': candidate_source}
                if pair not in alternatives: alternatives.append(pair)
        missing = not filled(selected)
        if missing and required: gaps.append(label)
        if required and not missing and label_source == MISSING: unverified.append(label)
        has_conflict = bool(alternatives or record.get('conflicts') or cad.get('conflicts')
                            or record.get('status') == 'conflicted' or cad.get('status') == 'conflicted')
        if has_conflict: conflicts.append(label)
        if label_source == '系统推荐' and record.get('status') != 'confirmed': suggestions.append(label)
        units = {'inner_length': 'mm', 'inner_width': 'mm', 'inner_height': 'mm',
                 'grey_board_thickness': 'mm', 'face_paper_gsm': 'g/㎡', 'fit_clearance': 'mm'}
        display_label = label + '（' + units[key] + '）' if key in units else label
        return {'label': display_label, 'value': plain(selected), 'source': label_source,
                'alternatives': alternatives, 'conflict': has_conflict, 'missing': missing, 'key': key}

    def fixed(label, value, source='已保存'):
        return {'label': label, 'value': plain(value), 'source': source if filled(value) else MISSING,
                'alternatives': [], 'missing': not filled(value)}

    name = data.get('project_name') or project.get('name') or project.get('project_name') or requirement.get('title')
    title_name = data.get('packaging_product_name') or data.get('product_name') or name
    sections.append({'title': '1. 项目名称', 'rows': [row('project_name', '项目名称', value=name, explicit=True, required=True)]})
    description = data.get('description') or data.get('quote_requirement_text') or data.get('technical_requirements')
    sections.append({'title': '2. 需求', 'rows': [row('description', '需求背景', value=description, explicit=True, required=True),
        fixed('评估目的', '核对产品规格、材料与工序，记录已有工艺与成本，识别待补充信息，支撑后续评估与报价。', '模板说明')]})
    sections.append({'title': '3. 产品技术规格', 'rows': []})
    for block in industry_templates.blocks('packaging'):
        if block.section not in ('3.1', '3.2', '3.3', '3.4', '3.5'): continue
        rows = [row(field.key, field.label, required=field.required) for field in block.fields]
        if block.section == '3.2':
            rows.insert(0, fixed('成品内尺寸', ' × '.join(item['value'] for item in rows[:3]) + ' mm', '逐项来源见下表'))
        sections.append({'title': block.section + ' ' + block.title, 'rows': rows})
    material_rows = []
    for item in bom or []:
        if item.get('bom_category') == 'finished': continue
        material = item.get('material') or item.get('material_text') or item.get('material_code')
        if not material and item.get('bom_category') != 'material': continue
        value = plain(material or item.get('item_name'))
        if filled(item.get('quantity')):
            value += '；用量：' + plain(item['quantity']) + ' ' + plain(item.get('unit'))
        material_rows.append(fixed(plain(item.get('item_name') or item.get('name') or item.get('item_key')), value, '已保存BOM'))
    if material_rows:
        next(s for s in sections if s['title'] == '3.3 材料')['rows'].extend(material_rows)

    process_rows, equipment = [], []
    for part in processes or []:
        prefix = ' '.join(str(v) for v in (part.get('part_code'), part.get('name')) if v)
        for step in part.get('steps') or []:
            step_name = step.get('step_name') or step.get('name') or step.get('title')
            if not step_name: continue
            process_rows.append(fixed(prefix, step_name, '历史结果，待核对' if part.get('historical') else '已保存零件工艺'))
            machine = step.get('workstation') or step.get('equipment') or step.get('machine')
            if isinstance(machine, dict): machine = machine.get('name') or machine.get('title')
            if machine:
                item = fixed(prefix + ' · ' + str(step_name), machine, '已保存工艺设备')
                if item not in equipment: equipment.append(item)
    sections.append({'title': '4. 工艺路线', 'rows': process_rows or [fixed('零件工艺', '尚未生成', '系统状态')]})
    risks = []
    if str(data.get('v_groove') or '').lower() in ('是', 'true', 'yes', '1') and not filled(data.get('grey_board_thickness')):
        risks.append(fixed('V槽与板厚', '已要求V槽，灰板厚度待确认；建议核对开槽与折弯条件。', '系统建议'))
    if conflicts:
        risks.append(fixed('资料存在差异', '、'.join(dict.fromkeys(conflicts)) + '；建议核对并选择适用值。', '系统建议'))
    if gaps: risks.append(fixed('待补充规格', '、'.join(dict.fromkeys(gaps)), '系统检查'))
    if unverified: risks.append(fixed('来源待确认', '、'.join(dict.fromkeys(unverified)), '系统检查'))
    availability = availability or {}
    for source, state in availability.items():
        if state == 'unavailable': risks.append(fixed(source + '读取状态', '读取失败，当前文档未验证这部分结果，请重试。', '系统状态'))
        if state == 'scheme_required': risks.append(fixed(source + '方案', '尚未选择当前方案，不混合展示多个方案的下游结果。', '系统状态'))
    sections.append({'title': '5. 风险与对策', 'rows': risks or [fixed('规则检查', '未识别到额外规则风险，不代表已完成工程验证。', '系统检查')]})
    cost_rows = []
    if cost:
        currency = cost.get('currency') or data.get('currency')
        unit = '元' if currency in ('CNY', 'RMB', '人民币', '元') else str(currency or '币种待确认')
        for key, label in [('material_total', '材料'), ('process_total', '加工'),
                           ('other_total', '其他费用'), ('total_cost', '单套成本合计')]:
            label += '（' + unit + '/只）'
            value = cost.get(key)
            try: display = str(Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)) if value is not None and math.isfinite(float(value)) else None
            except (ValueError, TypeError): display = None
            cost_rows.append(fixed(label, display, '已保存成本账'))
        cost_rows += [fixed('成本核算数量', cost.get('quote_quantity'), '已保存成本账'),
                      fixed('成本版本', cost.get('estimate_id'), '已保存成本账'),
                      fixed('测算时间', cost.get('computed_at'), '已保存成本账'),
                      fixed('有效性', '已保存快照；当前有效性未复核。', '系统状态')]
        if cost.get('has_gaps'): cost_rows.append(fixed('成本缺口', '已存成本仍有缺口，详见成本工作台。', '已保存成本账'))
        if cost.get('_historical'): cost_rows.append(fixed('版本状态', '历史结果，待核对；不按本次需求重新计价。', '系统状态'))
    else:
        cost_rows = [fixed('成本测算', '读取失败，待重新读取' if availability.get('成本') == 'unavailable' else '待补充（尚未测算）', '系统状态')]
    sections.append({'title': '6. 成本构成', 'rows': cost_rows})
    sections.append({'title': '7. 设备与刀版', 'rows': equipment or [fixed('设备与刀版', '随工艺设备信息生成；目前尚未记录。', '系统状态')]})
    draft = bool(requirement.get('status') != 'approved' or gaps or conflicts or suggestions or unverified
                 or any(v in ('unavailable', 'scheme_required') for v in availability.values())
                 or (cost or {}).get('_historical'))
    sections.append({'title': '8. 结论与下一步', 'rows': [
        fixed('需求状态', '草稿：存在待确认信息，后续按现有流程继续评估。' if draft else '需求已确认；后续报价仍以现有工艺与成本审核为准。', '系统状态'),
        fixed('工艺状态', '已保存工艺，未在生成文档时重新核验。' if process_rows else '尚未生成，可在图纸解析后推荐零件工艺。', '系统状态'),
        fixed('成本状态', '已保存成本，未重新测算或发布报价。' if cost else '待补充，可在工艺确认并交接后测算。', '系统状态')]})
    snapshot = {'template_version': VERSION, 'project_id': requirement.get('project_id') or project.get('project_id'),
                'requirement_no': requirement.get('requirement_no'), 'title': plain(title_name) + ' · 包装工艺需求评估表',
                'status': requirement.get('status'), 'draft': draft, 'sections': sections,
                'data_updated_at': requirement.get('updated_at'), 'created_by': requirement.get('created_by'),
                'gaps': list(dict.fromkeys(gaps)), 'conflicts': list(dict.fromkeys(conflicts)),
                'availability': availability}
    # 来源与完整快照均参与，避免只按标题缓存或重用已变更的成本版本。
    canonical = json.dumps({'view': snapshot, 'provenance': provenance, 'evidence': candidate_fields,
                            'cost': cost, 'bom': bom, 'processes': processes},
                           sort_keys=True, ensure_ascii=False, default=str)
    snapshot['data_hash'] = hashlib.sha256(canonical.encode()).hexdigest()
    snapshot['generated_at'] = now_cst_str()
    return snapshot


def load_snapshot(project_id, requirement, project):
    """取已有数据，不走带补算/KB核验的重型业务读接口。失败透明披露。"""
    from ..storage import da_repo
    from . import packaging_semantics, packaging_parts
    availability = {}

    def read(label, fn, default):
        try:
            result = fn()
            availability[label] = 'stored' if result else 'not_generated'
            return result or default
        except Exception:
            availability[label] = 'unavailable'
            return default

    evidence = read('图纸证据', lambda: packaging_semantics.load_semantics(project_id), {})
    req_no = requirement.get('requirement_no') or ''
    bom = read('材料', lambda: da_repo.load_packaging_bom(project_id, req_no), [])
    steps = read('工艺', lambda: da_repo.load_packaging_route_steps(project_id, req_no), [])
    cost = read('成本', lambda: da_repo.load_packaging_cost(project_id, req_no), {})
    index = read('零件清单', lambda: packaging_parts.load_business_cost_index(project_id), {})
    selected = index.get('selected_scheme_id')
    schemes = set(index.get('scheme_labels') or []) or {
        r.get('scheme_id') for r in index.get('business_parts') or [] if r.get('scheme_id') not in (None, '', 'common')}
    needs_scheme = len(schemes) > 1 and selected not in schemes
    codes = {r.get('business_part_code') for r in index.get('business_parts') or []
             if not r.get('scheme_id') or not selected or r.get('scheme_id') in (selected, 'common')}
    names = {r.get('business_part_code'): r.get('name') or '' for r in index.get('business_parts') or []}
    bom = [row for row in bom if not codes or not row.get('part_code') or row['part_code'] in codes]
    current_hash = index.get('business_parts_hash')
    def historical(version): return bool(current_hash and version and current_hash != version)
    if cost:
        cost = dict(cost)
        try:
            stored_versions = cost.get('source_versions_json')
            versions = stored_versions if isinstance(stored_versions, dict) else json.loads(stored_versions or '{}')
            if not isinstance(versions, dict): versions = {}
        except (ValueError, TypeError): versions = {}
        quantity = (requirement.get('data') or {}).get('quote_quantity')
        cost['_historical'] = historical(versions.get('business_parts_hash')) or (
            filled(quantity) and filled(cost.get('quote_quantity')) and not equivalent(quantity, cost['quote_quantity']))
    groups = {}
    for step in ([] if needs_scheme else steps):
        code = str(step.get('part_code') or '未记录零件')
        if codes and code not in codes: continue
        groups.setdefault(code, {'part_code': code, 'name': names.get(code, ''), 'historical': historical(step.get('business_parts_version')),
                                 'steps': []})['steps'].append(step)
    # 尚未汇总项目路线时也能显示已保存的逐零件推荐，仅读一次工艺文档。
    if not steps and not needs_scheme:
        from ..storage.meta_backend import get_backend
        records = read('零件工艺', lambda: get_backend().get_doc(project_id, packaging_parts.DOC_KEY_PROCESS), {})
        for record in records.get('items') or []:
            code = str(record.get('part_code') or '')
            if code and code not in groups and (not codes or code in codes) and (record.get('plan') or {}).get('steps'):
                groups[code] = {'part_code': code, 'name': names.get(code) or record.get('part_name') or '',
                                'historical': historical(record.get('business_parts_hash')),
                                'steps': record['plan']['steps']}
    if needs_scheme:
        availability['工艺'] = 'scheme_required'
        availability['成本'] = 'scheme_required'
        cost = {}
        bom = []
    return build_snapshot(requirement, project, evidence=evidence, bom=bom,
                          processes=list(groups.values()), cost=cost, availability=availability)

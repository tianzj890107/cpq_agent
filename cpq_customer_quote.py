"""客户只提供包装规格；内部报价输入只能来自有效系统记录，不能用盒型参考成本。"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime
from decimal import Decimal
import re

NUM = r'[-+]?\d+(?:\.\d+)?'
FACT_KEYS = ('inner_length', 'inner_width', 'inner_height', 'lid_face_paper', 'base_face_paper',
             'lid_face_paper_gsm', 'base_face_paper_gsm', 'lid_lamination', 'base_lamination',
             'lid_grey_board_thickness', 'closure_type', 'v_groove', 'fit_clearance',
             'packaging_category', 'box_family', 'quote_quantity')
NUM_KEYS = {'inner_length','inner_width','inner_height','lid_face_paper_gsm','base_face_paper_gsm',
            'lid_grey_board_thickness','fit_clearance','quote_quantity'}


def _one(prompt, pattern, label):
    values = {match.group(1).strip(' \t\r\n“”"') for match in re.finditer(pattern, prompt, re.I | re.S)}
    if len(values) != 1:
        raise ValueError('客户需求缺少或存在冲突：' + label)
    return values.pop()


def parse_customer_prompt(prompt):
    from cpq_direct_quote import normalize, number
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000:
        raise ValueError('客户需求正文为空或过长')
    if re.search('未税成本|成本来源|毛利率', prompt):
        raise ValueError('客户需求只需提供规格，内部成本和报价规则由系统读取')
    name = re.split(r'[，,。\n]', prompt.strip())[0].strip()
    name = re.sub(r'^(?:请)?(?:帮我)?(?:做一份|制作一份)?', '', name)
    name = re.sub(r'报价$', '', name).strip()
    if not re.search('酒盒|礼盒|包装盒', name):
        raise ValueError('请提供包装产品名称')
    customer = _one(prompt, r'客户(?:名称)?\s*(?:是|为|[:：])\s*([^，,。；;\n]+)', '客户')
    dims = list(re.finditer(r'(?:成品内|成品)?尺寸\s*[:：]?\s*('+NUM+r')\s*(?:mm|毫米)?\s*[×*x]\s*('
                           +NUM+r')\s*(?:mm|毫米)?\s*[×*x]\s*('+NUM+r')\s*(?:mm|毫米)?', prompt, re.I))
    if len(dims) != 1:
        raise ValueError('请提供唯一的成品内长、内宽和内高')
    dimensions = [str(number(v, '成品内尺寸', minimum=Decimal('0.000001'))) for v in dims[0].groups()]
    clearance = _one(prompt, r'配合间隙\s*[:：]?\s*('+NUM+r')\s*(?:mm|毫米)', '配合间隙')
    number(clearance, '配合间隙', minimum=Decimal('0.000001'))
    quantity = _one(prompt, r'(?:预计)?(?:首批(?:订购|订|定)?|数量\s*[:：为是]?|订购)\s*('+NUM+r')\s*(?:个|只|套)', '首批数量')
    facts = dict(zip(('inner_length','inner_width','inner_height'), dimensions))
    for key, label in [('lid', '盖面纸'), ('base', '底面纸')]:
        section = _one(prompt, label+r'\s*[:：]?\s*(.*?)(?=盖面纸|底面纸|盖板材|V\s*槽|闭合方式|包装品类|盒型|配合间隙|预计首批|$)', label)
        if '铜版纸' not in section:
            raise ValueError(label + '未明确铜版纸材料，不能按默认材料取价')
        facts[key+'_face_paper'] = '铜版纸'
        facts[key+'_face_paper_gsm'] = _one(section, '('+NUM+r')\s*(?:克重|克|g)', label+'克重')
        number(facts[key+'_face_paper_gsm'], label+'克重', minimum=Decimal(1))
        facts[key+'_lamination'] = _one(section, r'(亮膜|哑膜)', label+'覆膜')
    grey = _one(prompt, r'盖板材\s*[:：]?\s*(.*?)(?=V\s*槽|闭合方式|包装品类|盒型|配合间隙|预计首批|$)', '盖板材')
    if '灰板' not in grey:
        raise ValueError('盖板材未明确灰板')
    thickness = _one(grey, r'(?:厚度)?\s*('+NUM+r')\s*(?:mm|毫米)', '盖灰板厚度')
    number(thickness, '盖灰板厚度', minimum=Decimal('0.000001'))
    facts.update(lid_grey_board_thickness=thickness, fit_clearance=clearance, quote_quantity=quantity,
                 closure_type=_one(prompt, r'闭合方式\s*[:：]?\s*([^，,。；;\n]+)', '闭合方式'),
                 packaging_category=_one(prompt, r'包装品类\s*[:：]?\s*([^，,。；;\n]+)', '包装品类'),
                 box_family=_one(prompt, r'盒型\s*[:：]?\s*([^，,。；;\n]+)', '盒型'))
    if not re.search(r'V\s*槽', prompt, re.I):
        raise ValueError('请明确是否需要V槽')
    facts['v_groove'] = '否' if re.search(r'(?:无|不做|不需要)\s*V\s*槽', prompt, re.I) else '是'
    base = normalize(1, dict(product_name=name, customer=customer, product_code='',
                             specification=prompt[dims[0].start():].strip(), quantity=quantity))
    return {'1': base, '_customer_requirement': facts}


def _date_ok(row, start, end):
    today = date.today()
    try:
        lo = date.fromisoformat(str(row[start])[:10]) if row.get(start) else None
        hi = date.fromisoformat(str(row[end])[:10]) if row.get(end) else None
    except (ValueError, TypeError):
        return False
    return (lo is None or lo <= today) and (hi is None or hi >= today)


def _active(row):
    return (row.get('is_deleted') is False and row.get('effective_status') is True
            and row.get('latest') is True and _date_ok(row, 'effective_start_time', 'effective_end_time'))


def _same(key, actual, expected):
    if key in NUM_KEYS:
        try:
            return Decimal(re.sub(r'\s*(?:mm|毫米|g|克重|克|个|只|套)$', '', str(actual), flags=re.I)) == Decimal(str(expected))
        except Exception:
            return False
    return str(actual or '').strip() == str(expected).strip()


def load_catalog():
    """仅SELECT，不建表、不写主数据，不读取无关客户/历史报价。"""
    import cpq_db
    import cpq_kb
    schema, kb = cpq_db.PG_SCHEMA, cpq_kb.SCHEMA
    if not all(re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', value) for value in (schema, kb)):
        raise ValueError('系统报价数据schema配置无效')
    try:
        with cpq_db.connect(readonly=True) as conn:
            with conn.cursor() as cur:
                def rows(sql, args=()):
                    cur.execute(sql, args)
                    values = cur.fetchmany(20001)
                    if len(values) > 20000:
                        raise ValueError('系统报价候选过多，需完善查询索引和数据范围')
                    return [dict(zip([c.name for c in cur.description], value)) for value in values]
                products = rows(f'SELECT * FROM {schema}.product_para_value WHERE is_deleted=false '
                                'AND effective_status=true AND latest=true AND para_code = ANY(%s)',
                                (list(FACT_KEYS)+['cost_currency'],))
                codes = sorted({str(row['product_item_code']) for row in products})
                costs = rows(f'SELECT * FROM {schema}.md_clm_material_cost_cnf WHERE is_deleted=false '
                             'AND effective_status=true AND latest=true AND material_code = ANY(%s)', (codes,)) if codes else []
                factors = rows(f"SELECT * FROM {kb}.kb_cost_factor WHERE industry='packaging' AND factor_type IN ('margin','tax')")
                addons = rows(f'SELECT * FROM {schema}.md_clm_material_price_rule WHERE is_deleted=false '
                              "AND effective_status=true AND latest=true AND rule_classification='报价' "
                              "AND data_source IN ('packaging','packaging-rule','packaging_quote')")
                return dict(products=products, costs=costs, factors=factors, addons=addons)
    except Exception as exc:
        raise ValueError('系统报价数据读取失败（'+type(exc).__name__+'），请检查内部价库服务') from exc


def prepare_customer(parsed, catalog=None):
    from cpq_direct_quote import normalize, calculate, number
    catalog = load_catalog() if catalog is None else catalog
    result = {'1': copy.deepcopy(parsed['1'])}
    facts = parsed['_customer_requirement']
    gaps, evidence = [], {'source': '系统有效成品成本与包装报价规则', 'lookup_at': datetime.now().isoformat()}
    groups = {}
    for row in catalog.get('products', []):
        if _active(row):
            groups.setdefault(str(row.get('product_item_code') or ''), []).append(row)
    matches = []
    for code, rows in groups.items():
        values = {}
        for row in rows:
            values.setdefault(row.get('para_code'), set()).add(str(row.get('para_value') or '').strip())
        if all(len(values.get(key, set())) == 1 and _same(key, next(iter(values[key])), facts[key]) for key in FACT_KEYS):
            matches.append((code, rows, values))
    if len(matches) != 1:
        gaps.append('系统成品主数据中未找到唯一匹配全部规格及'+str(parsed['1']['quantity'])+'个数量口径的成品' if not matches else '系统存在多条同规格成品，需内部确认有效版本')
    else:
        code, product_rows, values = matches[0]
        result['1']['product_code'] = code
        evidence['product_code'] = code
        fields = ('id','product_item_code','para_code','para_value','system_version','updated_at',
                  'data_source','effective_start_time','effective_end_time')
        evidence['product_parameters'] = [{key: row[key] for key in fields if key in row} for row in product_rows]
        eligible = [row for row in catalog.get('costs', []) if row.get('material_code') == code and _active(row)
                    and _date_ok(row, 'price_validity_date', 'price_expiration_date')]
        eligible.sort(key=lambda row: str(row.get('price_validity_date') or ''), reverse=True)
        if not eligible:
            gaps.append('系统缺少该成品当前有效的未税成本记录')
        else:
            latest = eligible[0]
            same_date = [row for row in eligible if row.get('price_validity_date') == latest.get('price_validity_date')]
            currency = values.get('cost_currency', set())
            if (len({str(row.get('material_unit_price')) for row in same_date}) != 1
                    or not latest.get('data_source') or not latest.get('md_clm_material_cost_cnf_id') or len(currency) != 1):
                gaps.append('系统成本版本、币种或来源不完整/冲突')
            elif any(str(row.get('data_source') or '').strip().lower() in ('', 'unknown') for row in product_rows+[latest]):
                gaps.append('系统成品参数或成本来源未知，不能据此自动报价')
            else:
                demo = any(re.search('demo|mock|演示|测试', str(row.get('data_source') or ''), re.I) for row in product_rows+[latest])
                try:
                    result['2'] = normalize(2, dict(unit_cost=latest.get('material_unit_price'), currency=next(iter(currency)),
                        cost_source='系统成品成本记录#'+str(latest['md_clm_material_cost_cnf_id'])+'；'+str(latest['data_source']),
                        confirmed=True, demo=demo))
                    evidence['cost_record'] = copy.deepcopy(latest)
                except ValueError as exc:
                    gaps.append('系统成本记录无效：'+str(exc))
    for kind, step, key in [('margin','3','margin_percent'), ('tax','4','tax_percent')]:
        rows = [row for row in catalog.get('factors', []) if row.get('industry') == 'packaging'
                and row.get('factor_type') == kind and not row.get('condition_expr')
                and _date_ok(row, 'effective_from', 'effective_to') and row.get('source')
                and str(row.get('source')).strip().lower() != 'unknown' and row.get('factor_code')]
        try:
            values = {number(row.get('value'), '系统系数', maximum=Decimal(1)) for row in rows}
            if len(values) != 1:
                raise ValueError('缺失或冲突')
            value = values.pop()
            if kind == 'margin' and value >= 1:
                raise ValueError('毛利率必须小于100%')
            result.setdefault(step, {})[key] = str(value*100)
            evidence[kind+'_rules'] = copy.deepcopy(rows)
            if '2' in result and any(re.search('demo|mock|演示|测试', str(row), re.I) for row in rows):
                result['2']['demo'] = True
        except ValueError:
            gaps.append('系统缺少唯一有效的包装'+('毛利' if kind == 'margin' else '税率')+'规则')
    amounts = {}
    for row in catalog.get('addons', []):
        if not _active(row) or row.get('rule_classification') != '报价' or row.get('data_source') not in ('packaging','packaging-rule','packaging_quote'):
            continue
        match = re.fullmatch(r'\s*def\s+加价金额\s*=\s*('+NUM+r')\s*基础价格\s*\+\s*加价金额\s*', str(row.get('rule_expression') or ''))
        if match and row.get('md_clm_material_price_rule_id'):
            try:
                amounts.setdefault(number(match.group(1), '系统附加费'), []).append(row)
            except ValueError:
                pass
    if len(amounts) == 1:
        value, rules = next(iter(amounts.items()))
        result.setdefault('4', {})['addon_per_unit'] = str(value)
        evidence['addon_rules'] = copy.deepcopy(rules)
        if '2' in result and any(re.search('demo|mock|演示|测试', str(row), re.I) for row in rules):
            result['2']['demo'] = True
    else:
        gaps.append('系统缺少唯一有效的包装附加费规则，不能拿电池规则或默认0代替')
    if not gaps:
        result = {str(i): normalize(i, result[str(i)]) for i in range(1, 5)}
        calculate(result)
    else:
        # 允许保存客户需求；半套金额不进入可确认成本或报价方案。
        result = {'1': result['1']}
    result['_pricing_gaps'], result['_pricing_evidence'] = gaps, json.loads(json.dumps(evidence, default=str, ensure_ascii=False))
    result['_customer_requirement'] = copy.deepcopy(facts)
    return result

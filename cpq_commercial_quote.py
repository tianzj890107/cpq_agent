"""对客商务报价视图：只投影已保存金额，不导出内部成本和工艺。"""
from pathlib import Path
import json


def with_context(quote,package=None,basic=None):
    q=dict(quote or {});pkg=package or {};req=pkg.get('requirement') or {}
    parameters={**req,**(pkg.get('params') or {})}
    if not q.get('product_specification'):
        dims=[parameters.get(k) for k in ('inner_length','inner_width','inner_height')]
        q['product_specification']=' × '.join(map(str,dims))+' mm' if all(v not in (None,'') for v in dims) else parameters.get('specification','')
    q['product_name']=q.get('product_name') or req.get('packaging_product_name') or req.get('product_name') or ''
    q['customer']=q.get('customer') or req.get('customer_name') or ''
    commercial=dict(q.get('commercial_basic') or {});commercial.update(basic or {})
    commercial.setdefault('交期',req.get('mass_production_due') or req.get('first_sample_due') or '')
    commercial.setdefault('付款方式',req.get('payment_terms') or '')
    legacy_issuer='亿纬' in str(commercial.get('签约主体') or '')
    for key in ('签约主体','签约主体地址','签约主体社会信用代码'):
        if legacy_issuer or '亿纬' in str(commercial.get(key) or ''):commercial.pop(key,None)
    q['commercial_basic']=commercial
    return q


def document(quote, basic=None):
    from cpq_money import money
    q=quote or {};b=basic or q.get('commercial_basic') or {}
    defaults=json.loads((Path(__file__).parent/'cpq_data/报价单模板.json').read_text())['默认值']
    text=lambda value:str(value if value is not None and value!='' else '—')
    cash=lambda value:f'{money(value):.2f}'
    product={'产品名称':text(q.get('product_name')),'型号':text(q.get('box_type_code')),
        '规格':text(q.get('product_specification')),'单位':'只','数量':q.get('quote_quantity'),
        '单价（未税）':cash(q.get('net_unit_price')),'总价（未税）':cash(q.get('untaxed_total'))}
    metadata={'报价单号':text(b.get('报价单号') or q.get('quote_no') or q.get('requirement_no')),
        '报价日期':text(b.get('申请日期') or str(q.get('priced_at') or '')[:10]),
        '客户':text(b.get('客户') or q.get('customer')),'币种':text(q.get('currency')),
        '有效天数':b.get('报价有效天数') or defaults['报价有效天数']}
    totals={'未税总额':cash(q.get('untaxed_total')),'含税单价':cash(q.get('taxed_unit_price')),'税率':f"{float(q.get('tax_rate') or 0)*100:g}%",
        '税额':cash(money(q.get('taxed_total'))-money(q.get('untaxed_total'))),'含税总额':cash(q.get('taxed_total'))}
    terms={k:text(b.get(k)) for k in ('签约主体','签约主体地址','交期','付款方式','报价联系人','报价联系人电话','备注')}
    sections=[{'title':'报价基本信息','kind':'form','data':metadata},
        {'title':'产品报价明细','kind':'table','columns':list(product),'data':[product]},
        {'title':'金额汇总','kind':'form','data':totals},{'title':'商务条款','kind':'form','data':terms}]
    title='包装报价单'
    lines=['# '+title,'']
    if q.get('draft') and not q.get('demo_only'):lines+=['报价状态：草稿','']
    for s in sections:
        lines+=['## '+s['title']]
        if s['kind']=='table':
            lines+=['| '+' | '.join(s['columns'])+' |','| '+' | '.join('---' for _ in s['columns'])+' |',
                '| '+' | '.join(text(product[k]).replace('|','／').replace('\n',' ') for k in s['columns'])+' |']
        else:lines+=['- '+k+'：'+text(v).replace('\n',' ') for k,v in s['data'].items()]
        lines+=['']
    return {'title':title,'markdown':'\n'.join(lines),'sections':sections,'template':'commercial_unit_price_zh/1'}

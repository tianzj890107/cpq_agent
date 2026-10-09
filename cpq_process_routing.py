"""Controlled DA CLM header/line reader. No writes, no free-form SQL."""
from datetime import date
import cpq_db

HEADER = 'md_clm_process_routing_base_info'
LINE = 'md_clm_process_routing_operation'
OP = 'md_clm_operation_info'

def normalize(header, steps, match_method):
    h = dict(header)
    steps = [dict(s) for s in steps]
    for record in [h, *steps]:
        for key, value in list(record.items()):
            if (key.endswith('_id') or key == 'factory') and value is not None:
                record[key] = str(value)
    status = str(h.get('effect_status') or '')
    valid = True
    for key, lower in [('effect_begin_date',True), ('effect_end_date',False)]:
        value = h.get(key)
        if value:
            try:
                d = date.fromisoformat(str(value)[:10])
                valid &= d <= date.today() if lower else d >= date.today()
            except ValueError:
                valid = False
    approved = (bool(steps) and all(s.get('operation_name') for s in steps)
                and match_method == 'product_item_code' and valid
                and status in ('已生效','已审核','有效')
                and h.get('effective_status') is not False
                and h.get('data_source') != 'yutong_wine_box_quote')
    return {'header':h,'steps':sorted(steps,key=lambda s:float(s.get('line_number') or 0)),
            'match_method':match_method,'approved':approved,
            'source':'DA_CLM_PG','ontology_section':'pending/config',
            'notice':'已批准编码路线' if approved else '数据库参考路线，非已批准标准；缺失工时不可当作零'}

def lookup(product_item_code='', name=''):
    from psycopg import sql
    from psycopg.rows import dict_row
    ontology = cpq_db._load_ontology()
    tables = {e['table'] for section in ('config','pending')
              for e in ontology.get(section,{}).get('entities',[])}
    if not {HEADER,LINE,OP}.issubset(tables):
        raise RuntimeError('DA 缺少 CLM 工艺路线头/行/工序条目')
    method = 'product_item_code' if str(product_item_code).strip() else 'name'
    value = str(product_item_code if method == 'product_item_code' else name).strip()
    if not value:
        return {'status':'missing_identity','routes':[],'source':'DA_CLM_PG'}
    # Names are exact business names, never fuzzy cross-industry matches.
    value = value if method != 'name' or value.endswith('工艺路线') else value+'工艺路线'
    with cpq_db.connect(readonly=True) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SET statement_timeout = '5000ms'")
        cur.execute(sql.SQL('SELECT * FROM {}.{} WHERE {} = %s AND is_deleted IS NOT TRUE AND latest IS NOT FALSE LIMIT 21').format(
            sql.Identifier(cpq_db.PG_SCHEMA),sql.Identifier(HEADER),sql.Identifier(method)),(value,))
        heads = cur.fetchall()
        routes=[]
        for h in heads:
            cur.execute(sql.SQL('SELECT l.*, o.name AS operation_name, o.number AS operation_code FROM {}.{} l LEFT JOIN {}.{} o ON o.md_clm_operation_info_id=l.md_operation_info_id AND o.is_deleted IS NOT TRUE AND o.latest IS NOT FALSE WHERE l.md_process_rputing_id=%s AND l.is_deleted IS NOT TRUE AND l.latest IS NOT FALSE ORDER BY l.line_number LIMIT 501').format(
                sql.Identifier(cpq_db.PG_SCHEMA),sql.Identifier(LINE),sql.Identifier(cpq_db.PG_SCHEMA),sql.Identifier(OP)),(h['md_clm_process_routing_base_info_id'],))
            rows=cur.fetchall()
            if len(rows)>500:
                raise RuntimeError('路线行超过读取上限，不能截断推荐')
            routes.append(normalize(h,rows,method))
    return {'status':'matched' if len(routes)==1 else ('ambiguous' if routes else 'not_found'),
            'routes':routes,'source':'DA_CLM_PG'}

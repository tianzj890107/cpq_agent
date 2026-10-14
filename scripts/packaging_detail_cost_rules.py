#!/usr/bin/env python3
"""离线明细公式核对；显式--apply才向PG追加新编码，不覆盖旧规则。"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tech_app.backend.services import packaging_cost as cost, packaging_detail_cost as detail

SOURCE='packaging_detail_rules_json'


def audit(workbook):
    import openpyxl
    path=Path(workbook)
    if hashlib.sha256(path.read_bytes()).hexdigest()!=detail.MANIFEST['source_sha256']:
        raise ValueError('明细工作簿SHA不匹配，必须重新核对规则版本')
    book=openpyxl.load_workbook(path,data_only=False,read_only=True)
    results=[]
    try:
        for entry in detail.CATALOG.values():
            sheet=book[entry['source_sheet']]
            if sheet.sheet_state!='visible':raise ValueError('禁止隐藏表来源')
            original=sheet[entry['source_cell']].value
            if original!=entry['source_formula']:raise ValueError('来源公式不匹配：'+entry['formula_code'])
            for quantity in (1000,5000,10000,20000):
                row=re.search(r'\d+$',entry['source_cell']).group()
                overrides={'Q'+row:quantity}
                synthetic=[]
                if sheet['I'+row].value is None and 'imposition_count' in entry['variable_map']:
                    overrides['I'+row]=1
                    synthetic.append('空模数使用验证输入1，非工作簿实测值')
                def value(cell,seen=()):
                    if cell in seen:raise ValueError('循环引用')
                    if cell in overrides:return overrides[cell]
                    raw=sheet[cell].value
                    if isinstance(raw,(int,float)):return raw
                    if not isinstance(raw,str) or not raw.startswith('='):raise ValueError('缺数字：'+cell)
                    expr=cost._strip_iferror(raw)
                    refs=set(re.findall(r'\b[A-Z]+\d+\b',expr))
                    return cost._formula_value(expr,{r:value(r,seen+(cell,)) for r in refs})
                variables={k:value(col+row) for k,col in entry['variable_map'].items()}
                variables.update(tax_factor=1.13,hot_area_mm2=30000,glue_area_mm2=4920)
                expected=value(entry['source_cell'])
                result=cost.compute_line(entry['cost_category'],variables,formula_code=entry['formula_code'],rows=[])
                if result.get('amount') is None or abs(result['amount']-expected)>1e-6:
                    raise ValueError('复算不符：'+entry['formula_code'])
                results.append({'code':entry['formula_code'],'quantity':quantity,
                                'expected':expected,'actual':result['amount'],'synthetic_inputs':synthetic})
    finally:book.close()
    return results


def rows():
    return [dict(formula_code=r['formula_code'],cost_category=r['cost_category'],
        expression=r['expression'],minimum_charge=r['minimum_charge'],rounding=str(r['rounding']),
        source_ref=r['source_ref'],formula_version=detail.PROFILE,review_status='reviewed',
        industry='packaging',source_type='workbook',source_sha256=detail.MANIFEST['source_sha256'],
        parser_version=detail.PROFILE,source=SOURCE,version=detail.PROFILE,status='active',
        effective_from='2026-10-14') for r in detail.CATALOG.values()]


def apply_rules(backup):
    import cpq_kb
    path=Path(backup)
    if path.exists():raise ValueError('备份已存在，不覆盖')
    cpq_kb.export_snapshot(path)
    conn=cpq_kb._connect()
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                for row in rows():
                    cur.execute('SELECT source,review_status,status FROM '+cpq_kb.SCHEMA+'.kb_packaging_cost_formula WHERE formula_code=%s FOR UPDATE',(row['formula_code'],))
                    existing=cur.fetchone()
                    if existing and existing[0]!=SOURCE:raise ValueError('新规则编码已有人工维护记录，拒绝覆盖：'+row['formula_code'])
                    if existing and (existing[1]=='retired' or existing[2]!='active'):
                        raise ValueError('新规则已被下线，拒绝自动复活：'+row['formula_code'])
                changed=cpq_kb._upsert_rows(cur,'kb_packaging_cost_formula',rows())
                version=cpq_kb._bump_version(cur,changed)
        return {'changed':changed,'kb_version':version,'codes':[r['formula_code'] for r in rows()]}
    finally:conn.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--workbook',default=str(ROOT/'裕同包装项目-待开发/成本测算明细.xlsx'))
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--backup')
    args=parser.parse_args()
    checks=audit(args.workbook)
    print(json.dumps({'profile':detail.PROFILE,'formula_count':len(detail.CATALOG),'checks':len(checks),'ok':True},ensure_ascii=False))
    if args.apply:
        if not args.backup:parser.error('--apply必须指定--backup')
        print(json.dumps(apply_rules(args.backup),ensure_ascii=False))


if __name__=='__main__':main()

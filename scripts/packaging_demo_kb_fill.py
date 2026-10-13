"""Add-only packaging demo knowledge. Never promote mock to authority or deploy code."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cpq_kb
from tech_app.backend.services import packaging_cost, packaging_material_match

SOURCE='packaging_demo_20261012_mock_not_production'
SPECS=(
    ('FACE225','225G铜版底PET光银','225g',1.25,.06),
    ('FACE325','325G PET光银','325g',1.25,.06),
    ('LAMINATED','325G PET光银+3层300G白卡+225gPET光银','1450g',1.25,.08),
    ('CARD300','300G白卡','300g',1.2,.06),
    ('BOARD18','1.8mm灰板','t1.8',.75,.08),
    ('BOARD20','2mm灰板','t2.0',.75,.08),
    ('BOARD25','2.5MM灰板','t2.5',.75,.08),
    ('BOARD-SILVER','1.8mm灰板裱光银纸','t1.8',.75,.08),
    ('EVA35','35mm厚EVA','t35',.94,.10),
    ('TUBE','纸管','250g',.75,.08),
    ('LINER','衬纸','80g',.85,.05),
    ('MAGNET','磁铁','t2.0',7.5,.02),
    ('GENERAL','包装通用演示纸板','200g',.75,.08),
    ('SUN225','225G太阳铜版底PET光银','225g',1.25,.06),
    ('SUN255','255G太阳铜版底PET光银','255g',1.25,.06),
    ('WHITE325','325G白卡底PET光银','325g',1.2,.06),
    ('GREY350','350G玖龙粉灰','350g',.75,.08),
    ('FUYE25','2.5MM富业双灰','t2.5',.75,.08),
    ('FUYE25GREY','2.5MM富业灰板','t2.5',.75,.08),
    ('FUYE20SLIP','2MM单面富业滑灰板','t2.0',.75,.08),
    ('FUYE20SLIP2','2MM富业单面滑灰板','t2.0',.75,.08),
    ('BOARD22','2MM富业双灰对裱11层共22MM厚','t22',.75,.08),
    ('LAMINATEDWHITE','325G白卡底PET光银+3层300G白卡+225G铜版底PET光银','1450g',1.2,.08),
    ('CORRUGATED','235G白卡底PET光银+A9（E坑）130/120','485g',.75,.10),
    ('BOARD18SUN','1.8MM双灰裱225G太阳铜版底PET光银','1575g',.75,.08),
)

def plan():
    materials=[]
    prices=[]
    for suffix,name,grade,density,loss in SPECS:
        code='MAT-PKG-DEMO-'+suffix
        materials.append(dict(material_code=code,name=name,grade=grade,spec=grade,
            category='包材',form='片材',density=density,base_unit='kg',standard_loss_rate=loss,
            status='active',industry='packaging',note=SOURCE+'；价格与规格仅演示，不是采购标准'))
        prices.append(dict(material_code=code,price=packaging_material_match.demo_unit_price(name)['unit_price'],
            currency='CNY',unit='kg',price_type='market',valid_from='2026-10-12 00:00:00',
            source_name=SOURCE,evidence='用户授权mock演示估算，非供应商报价',confidence=.3,
            created_by='demo_seed',created_at='2026-10-12 00:00:00'))
    rates={}
    for entry in packaging_cost.FORMULA_CATALOG.values():
        code=entry.get('rate_code')
        defaults=entry.get('defaults') or {}
        if not code or code in rates: continue
        equipment='EQUIP' in code
        value=defaults.get('equipment_rate' if equipment else 'labor_rate',193 if equipment else 36)
        rates[code]=dict(rate_code=code,name='演示加工费率 '+code,
            rate_type='equipment_dep' if equipment else 'labor',scope_type='global',value=value,
            unit='元/小时',currency='CNY',effective_from='2026-10-12 00:00:00',
            source=SOURCE,note='仅演示；已有同码记录不覆盖',industry='packaging',minimum_charge=0)
    return {'kb_material':materials,'kb_material_price':prices,'kb_cost_rate':list(rates.values())}

def apply(rows, backup):
    from psycopg import sql
    snapshot=cpq_kb.snapshot()
    with Path(backup).open('x',encoding='utf-8') as stream:
        json.dump(snapshot,stream,ensure_ascii=False,default=str)
    changes={}
    conn=cpq_kb._connect()
    try:
        with conn.transaction(),conn.cursor() as cur:
            cur.execute('SELECT pg_advisory_xact_lock(%s)',(2026101201,))
            for table,records in rows.items():
                changed=0
                for record in records:
                    cols=list(record)
                    if table=='kb_material_price':
                        cur.execute(sql.SQL('SELECT 1 FROM {}.{} WHERE material_code=%s AND source_name=%s').format(
                            sql.Identifier(cpq_kb.SCHEMA),sql.Identifier(table)),(record['material_code'],SOURCE))
                        if cur.fetchone(): continue
                        # Existing serial was synchronized by the supported importer.
                    query=sql.SQL('INSERT INTO {}.{} ({}) VALUES ({}) ON CONFLICT DO NOTHING').format(
                        sql.Identifier(cpq_kb.SCHEMA),sql.Identifier(table),
                        sql.SQL(',').join(map(sql.Identifier,cols)),sql.SQL(',').join(sql.Placeholder() for _ in cols))
                    cur.execute(query,[record[k] for k in cols])
                    changed+=cur.rowcount
                changes[table]=changed
            version=cpq_kb._bump_version(cur,sum(changes.values()))
    finally: conn.close()
    return {'changed':changes,'kb_version':version,'backup':str(backup),'source':SOURCE}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--backup')
    args=parser.parse_args()
    if args.apply and not args.backup: parser.error('--apply requires --backup')
    rows=plan()
    print(json.dumps(apply(rows,args.backup) if args.apply else rows,ensure_ascii=False,default=str))

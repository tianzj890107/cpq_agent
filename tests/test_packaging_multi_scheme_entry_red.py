import unittest
from pathlib import Path
from tech_app.backend.services import packaging_business_part_resolver as resolver
from tech_app.backend.services import packaging_parts, packaging_layout, packaging_bom

ROOT=Path(__file__).resolve().parents[1]

class MultiSchemeEntryTests(unittest.TestCase):
    def test_selection_api_rejects_stale_version_and_preserves_both_schemes(self):
        from unittest.mock import patch
        from fastapi import HTTPException
        from tech_app.backend import main
        doc={'business_parts_hash':'current','scheme_labels':['1','2'],
             'business_parts':[{'business_part_code':'a','scheme_id':'1'},
                               {'business_part_code':'b','scheme_id':'2'}]}
        user={'username':'PE1','role':'process_manager'}
        with patch.object(main,'_workflow_project'), \
             patch.object(packaging_parts,'load_business_parts',return_value=doc), \
             patch.object(packaging_parts,'save_business_parts',side_effect=lambda pid,d:d) as save, \
             patch.object(main,'_business_parts_body',side_effect=lambda pid,d:d):
            with self.assertRaises(HTTPException) as caught:
                main.select_packaging_scheme('demo',main.PackagingSchemeSelection(scheme_id='2',business_parts_hash='old'),user)
            self.assertEqual(409,caught.exception.status_code)
            save.assert_not_called()
            result=main.select_packaging_scheme('demo',main.PackagingSchemeSelection(scheme_id='2',business_parts_hash='current'),user)
            self.assertEqual('2',result['selected_scheme_id'])
            self.assertEqual(2,len(result['business_parts']))
            self.assertEqual('PE1',result['scheme_selection_source']['actor'])

    def test_cost_api_translates_inactive_scheme_into_409(self):
        from unittest.mock import patch
        from fastapi import HTTPException
        from tech_app.backend import main
        from tech_app.backend.services import packaging_cost, packaging_cost_ledger
        with patch.object(main,'_workflow_project'),patch.object(main,'_packaging_business_part_row'), \
             patch.object(packaging_cost_ledger,'part_view',side_effect=packaging_cost.CostError('未采用方案',409,'packaging_part_inactive_scheme')):
            with self.assertRaises(HTTPException) as caught:
                main.get_packaging_business_part_cost('demo','b',{'username':'PE1'})
        self.assertEqual(409,caught.exception.status_code)
        self.assertEqual('packaging_part_inactive_scheme',caught.exception.detail['code'])

    def test_side_by_side_schemes_are_not_forced_into_vertical_split(self):
        ir={'entities':[],'texts':[
            {'entity_id':'s1','raw_text':'内托方案1','position':[100,800]},
            {'entity_id':'s2','raw_text':'内托方案2','position':[600,800]},
            {'entity_id':'p1','raw_text':'盖面纸','position':[120,780]},
            {'entity_id':'p2','raw_text':'盖面纸','position':[620,780]}]}
        result=resolver.resolve_business_parts('demo',ir,{'parts':[]},strict_schemes=True)
        self.assertEqual({'1','2'},{r['scheme_id'] for r in result['reference']['parts'] if r['name']=='盖面纸'})

    def test_global_frame_cannot_hide_round_parts_as_internal_holes(self):
        from tech_app.backend.services.cad_ir import parser
        entities=[{'entity_id':'frame','kind':'polyline','closed':True,'bbox':[0,-20,100,120],
                   'attributes':{'points':[[0,-20],[100,-20],[100,120],[0,120]]}},
                  {'entity_id':'c1','kind':'circle','closed':True,'bbox':[5,85,15,95],
                   'attributes':{'center':[10,90],'radius':5}},
                  {'entity_id':'c2','kind':'circle','closed':True,'bbox':[5,35,15,45],
                   'attributes':{'center':[10,40],'radius':5}}]
        # 零件面积必须高于既有的碎片过滤门槛，不能拿小孔当整件测试。
        for entity in entities:
            entity['type']='CIRCLE' if entity['kind']=='circle' else 'LWPOLYLINE'
            entity['layer']='CUT'
            entity['bbox']=[v*10 for v in entity['bbox']]
            attrs=entity['attributes']
            if 'center' in attrs:
                attrs['center']=[v*10 for v in attrs['center']]
                attrs['radius']*=10
            if 'points' in attrs:
                attrs['points']=[[v*10 for v in p] for p in attrs['points']]
        ir={'entities':entities,'geometry':parser._geometry_block(entities,1e-6),
            'units':{'unit_status':'confirmed'},'texts':[
            {'entity_id':'s1','raw_text':'内托方案1','position':[500,800]},
            {'entity_id':'s2','raw_text':'内托方案2','position':[500,300]},
            {'entity_id':'p1','raw_text':'圆底板','position':[100,750]},
            {'entity_id':'p2','raw_text':'圆底板','position':[100,250]}]}
        self.assertFalse(any('c1' in c['entity_ids'] for c in ir['geometry']['components']))
        result=resolver.resolve_business_parts('demo',ir,packaging_parts.extract(ir),strict_schemes=True)
        doc=packaging_parts.business_parts_document(result['reference'],packaging_parts.extract(ir),bindings=result['match'])
        parts=[r for r in doc['business_parts'] if r['name']=='圆底板']
        self.assertEqual(2,len(parts))
        self.assertEqual({'c1','c2'},{eid for r in parts for eid in r['geometry_binding'].get('entity_ids',[])})

    def test_cost_read_for_inactive_scheme_is_business_error_not_500(self):
        from unittest.mock import patch
        from tech_app.backend.services import packaging_cost, packaging_cost_ledger
        doc={'scheme_labels':['1','2'],'selected_scheme_id':'1',
             'business_parts':[{'business_part_code':'other','scheme_id':'2'}]}
        with patch.object(packaging_parts,'load_business_parts',return_value=doc):
            with self.assertRaises(packaging_cost.CostError) as caught:
                packaging_cost_ledger.part_view('demo','other',view={'parts':[]})
        self.assertEqual('packaging_part_inactive_scheme',caught.exception.code)

    def test_manual_layout_association_cannot_cross_alternative_schemes(self):
        doc={'layout_rows':[{'entity_id':'l','scheme_id':'1'}], 'business_parts':[
             {'business_part_code':'a','scheme_id':'1'}, {'business_part_code':'b','scheme_id':'2'}]}
        with self.assertRaisesRegex(ValueError,'layout_assignment_scheme_conflict'):
            packaging_layout.set_layout_assignment(doc,'l',['b'],actor='PE1')
        self.assertEqual(['a'],packaging_layout.set_layout_assignment(doc,'l',['a'],actor='PE1')['layout_rows'][0]['part_codes'])

    def test_pure_layout_schemes_share_one_business_part_set(self):
        ir={'entities':[],'texts':[
            {'entity_id':'s1','raw_text':'排模方案1','position':[50,800]},
            {'entity_id':'s2','raw_text':'排模方案2','position':[50,300]},
            {'entity_id':'p','raw_text':'盖面纸','position':[10,780]}]}
        result=resolver.resolve_business_parts('demo',ir,{'parts':[]},strict_schemes=True)
        self.assertEqual('layout_only',result['reference']['scheme_mode'])
        self.assertEqual(['common'],[r['scheme_id'] for r in result['reference']['parts'] if r['name']=='盖面纸'])

    def test_common_parts_and_selected_layout_only_not_double_counted(self):
        doc={'scheme_labels':['1','2'],'selected_scheme_id':'2','business_parts':[
             {'business_part_code':'common','scheme_id':'common'},
             {'business_part_code':'one','scheme_id':'1'},
             {'business_part_code':'two','scheme_id':'2'}],
             'layout_rows':[{'scheme_id':'1'},{'scheme_id':'2'}]}
        result=packaging_layout.active_business_document(doc)
        self.assertEqual(['common','two'],[r['business_part_code'] for r in result['business_parts']])
        self.assertEqual([{'scheme_id':'2'}],result['layout_rows'])
        self.assertEqual(3,len(doc['business_parts']))
        with self.assertRaisesRegex(ValueError,'packaging_scheme_not_found'):
            packaging_layout.select_scheme(doc,'99')

    def test_scheme_selection_updates_version_without_dropping_alternatives(self):
        doc={'scheme_labels':['1','2'],'business_parts':[{'business_part_code':'a','scheme_id':'1'},
                                                       {'business_part_code':'b','scheme_id':'2'}]}
        selected=packaging_layout.select_scheme(doc,'2')
        self.assertNotEqual(packaging_parts._business_identity(doc),packaging_parts._business_identity(selected))
        self.assertEqual(2,len(selected['business_parts']))

    def test_batch_invocation_uses_business_rows_with_geometry_present(self):
        import subprocess
        source=(ROOT/'tech_app/frontend/app.js').read_text()
        start=source.index('function startAllPackagingPartProcesses(')
        end=source.index('\n}',start)+2
        script='''const currentPackagingParts={parts:Array.from({length:312},(_,i)=>({part_code:'fragment'+i}))};
const currentPackagingBusinessParts={business_parts:[{business_part_code:'DWG-S1-BP01'}]};
let allPartsProcessBusy=false;const currentProject='demo';let submitted;
const packagingBusinessPartRows=d=>d.business_parts;
const packagingBusinessPartDownstreamTarget=()=>({ok:false});
const packagingBusinessPartProcessTarget=r=>({ok:true,part_code:r.business_part_code});
const runPackagingBusinessProcessBatch=(p,ready,skipped)=>{submitted=ready;};
''' + source[start:end] + '''
const result=startAllPackagingPartProcesses();
if (!result.ok || result.result.total!==1 || submitted[0].code!=='DWG-S1-BP01') process.exit(1);
'''
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_frontend_scheme_selection_has_version_guard_and_existing_style(self):
        source=(ROOT/'tech_app/frontend/app.js').read_text()
        body=source.split('function renderPackagingBusinessTree(',1)[1].split('\nfunction ',1)[0]
        self.assertIn('packaging-business-parts/scheme',body)
        self.assertIn('business_parts_hash:currentPackagingBusinessParts.business_parts_hash',body)
        self.assertIn("button.className = 'part-row-action'",body)
        self.assertIn('row.scheme_id',body)

    def test_business_parse_failure_cannot_report_completed(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        import importlib
        steps=importlib.import_module('tech_app.backend.services.packaging_drawing_flow.steps')
        module=SimpleNamespace(extract=lambda *a,**k:{'parts':[],'stats':{'part_total':312}},
                               save_parts=lambda pid,doc:doc)
        with patch.object(steps,'_resolve',return_value=module), \
             patch.object(steps,'_packaging_material_requirement',return_value={}), \
             patch.object(steps,'_resolve_business_parts',return_value={
                 'document':None,'detail':{'unavailable':[{'code':steps.BUSINESS_PARTS_RESOLVE_FAILED,
                 'message':'业务部件解析失败（ValueError）'}]}}), patch.object(steps,'_emit'):
            result=steps.parts_extract({'project_id':'demo','ir':{'entities':[]}})
        self.assertNotEqual('completed',result['status'])

    def test_multi_scheme_parse_retains_distinct_same_name_parts(self):
        ir={'texts':[
            {'entity_id':'s1','raw_text':'内托方案1','position':[50,800]},
            {'entity_id':'s2','raw_text':'内托方案2','position':[50,300]},
            {'entity_id':'p1','raw_text':'盖面纸','position':[10,780]},
            {'entity_id':'p2','raw_text':'盖面纸','position':[10,280]}], 'entities':[]}
        result=resolver.resolve_business_parts('demo',ir,{'parts':[]},strict_schemes=True)
        rows=[r for r in result['reference']['parts'] if r['name']=='盖面纸']
        self.assertEqual(2,len(rows))
        self.assertEqual({'1','2'},{r['scheme_id'] for r in rows})
        self.assertEqual(2,len({r['business_part_code'] for r in rows}))
        doc=packaging_parts.business_parts_document(result['reference'],{'parts':[]},bindings=result['match'])
        self.assertEqual({'1','2'},{r['scheme_id'] for r in doc['business_parts'] if r['name']=='盖面纸'})

    def test_bom_rejects_unselected_alternatives_and_selects_only_one(self):
        doc={'scheme_labels':['1','2'],'business_parts':[
            {'business_part_code':'a','name':'甲','scheme_id':'1','reference':{}},
            {'business_part_code':'b','name':'乙','scheme_id':'2','reference':{}}]}
        with self.assertRaisesRegex(ValueError,'packaging_scheme_selection_required'):
            packaging_layout.active_business_document(doc)
        selected=packaging_layout.select_scheme(doc,'2')
        self.assertEqual(['b'],[r['business_part_code'] for r in packaging_layout.active_business_document(selected)['business_parts']])
        self.assertEqual(2,len(selected['business_parts']))
        self.assertNotIn('selected_scheme_id',doc)

    def test_batch_entry_reads_business_parts_not_geometry(self):
        source=(ROOT/'tech_app/frontend/app.js').read_text()
        body=source.split('function startAllPackagingPartProcesses(',1)[1].split('\nfunction ',1)[0]
        self.assertIn('packagingBusinessPartRows(currentPackagingBusinessParts)',body)
        self.assertNotIn('currentPackagingParts && currentPackagingParts.parts',body)

    def test_scheme_local_layout_association(self):
        rows=[{'text':'盖面纸 排2模','scheme_id':'1','entity_id':'l'}]
        parts=[{'name':'盖面纸','business_part_code':'b','scheme_id':'2'},
               {'name':'盖面纸','business_part_code':'a','scheme_id':'1'}]
        self.assertEqual(['a'],packaging_layout.auto_assign_layout_rows(rows,parts)[0]['part_codes'])

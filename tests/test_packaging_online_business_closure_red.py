"""线上验收缺口：业务清单、报价承接、行业继承和状态恢复。"""
import pathlib
import unittest
import json
import subprocess
from unittest.mock import patch

import cpq_tech_bridge
from tech_app.backend.services import cost_review, packaging_layout, manufacturing_snapshot

ROOT = pathlib.Path(__file__).resolve().parents[1]


def js_function(source, name):
    start = source.index('function ' + name + '(')
    end = source.find('\n    function ', start + 1)
    async_end = source.find('\n    async function ', start + 1)
    return source[start:min(i for i in (end, async_end, len(source)) if i > start)]


class BusinessClosure(unittest.TestCase):
    def test_cost_review_uses_business_rows_not_fragments(self):
        doc = {'business_parts': [
            {'business_part_code': 'BP-A', 'name': '内托', 'cad_fragments': [{}, {}]},
            {'business_part_code': 'BP-B', 'name': '盖纸'}],
            'parts': [{'part_code': 'fragment-1'}, {'part_code': 'fragment-2'},
                      {'part_code': 'fragment-3'}]}
        ir = cost_review.ir_from_packaging_parts(doc)
        self.assertEqual(['BP-A', 'BP-B'], [p.part_id for p in ir.parts])
        self.assertEqual(1, ir.parts[0].quantity)

    def test_empty_business_catalog_does_not_become_fragments(self):
        self.assertIsNone(cost_review.ir_from_packaging_parts(
            {'business_parts': [], 'parts': [{'part_code': 'fragment'}]}))

    def test_handoff_contains_packaging_product_row_with_identity(self):
        result = {'industry': 'packaging', 'packaging_package': {
            'requirement': {'title': '礼盒', 'quote_quantity': 1000, 'currency': 'CNY'},
            'box_type': {'confirmed_box_type': 'BOX-X'},
            'cost': {'total_cost': 12, 'has_gaps': True},
            'source': {'result_version': 'cost-v2'}}}
        snapshot = cpq_tech_bridge.packaging_snapshot(result)
        row = snapshot['s2_products']['数据'][0]
        self.assertEqual('BOX-X', row['成品编码'])
        self.assertEqual(1000, row['数量'])
        self.assertEqual('礼盒', row['成品描述'])
        self.assertEqual('cost-v2', row['成本结果版本'])
        self.assertTrue(row['成本有缺口'])

    def test_layout_keeps_raw_text_but_cleans_cad_markup(self):
        row = packaging_layout.layout_record('e1', r'{\C1;面纸\P157g 443*595mm 排2模}')
        self.assertNotIn('\\C1;', row['text'])
        self.assertNotIn('\\P', row['text'])
        self.assertIn('\\C1;', row['raw_text'])
        self.assertEqual(2, row['n_up'])

    def test_task_industry_precedes_local_preference(self):
        src = (ROOT / 'tech_app/frontend/tech-task.js').read_text()
        choice = src[src.index('function industryChoice()'):src.index('function render()')]
        self.assertIn('task.payload', choice)
        self.assertLess(choice.index('task.payload'), choice.index('localStorage.getItem'))

    def test_quote_restore_and_packaging_row_adapter_are_wired(self):
        src = (ROOT / '确认需求解析结果.html').read_text()
        for expected in ('restorePackagingPricing', 'applyPackagingQuoteRows', 'snap.packaging_quote'):
            self.assertTrue(expected in src, expected)

    def test_quote_rate_restore_zero_and_invalid(self):
        src = (ROOT / '确认需求解析结果.html').read_text()
        fn = js_function(src, 'restorePackagingPricing')
        script = 'let PACKAGING_RESTORED_RATE=null;\n' + fn + '''
restorePackagingPricing({packaging_quote:{pricing_mode:'gross_margin',gross_margin_rate:0}});
if(PACKAGING_RESTORED_RATE!==0)throw Error('zero lost');
restorePackagingPricing({packaging_quote:{pricing_mode:'gross_margin',gross_margin_rate:1}});
if(PACKAGING_RESTORED_RATE!==0)throw Error('invalid accepted');
'''
        run = subprocess.run(['node', '-e', script], text=True, capture_output=True)
        self.assertEqual(0, run.returncode, run.stderr)

    def test_quote_adapter_emits_real_detail_with_total_tax(self):
        src = (ROOT / '确认需求解析结果.html').read_text()
        fn = js_function(src, 'applyPackagingQuoteRows')
        script = '''let LAST_PACKAGING_PACKAGE={requirement:{title:'礼盒'}};
const FORMS=['s1_products','s2_products','s3_products','s4_products','s5_detail'].map((id,i)=>({section_id:id,step:i+1,columns:[]}));
const rendered=[];function renderTableSection(ui){rendered.push(ui)};
''' + js_function(src, 'packagingQuoteDetailValues') + fn + '''
applyPackagingQuoteRows({box_type_code:'BOX',quote_quantity:1000,currency:'CNY',cost_total:12,
untaxed_unit_price:16,untaxed_total:16000,taxed_total:18080,tax_rate:0.13,margin_price:16,addon_total:0,draft:true});
const row=rendered.find(x=>x.section_id==='s5_detail').rows[0];
// 2026-10-08：明细金额改为同一版本的展示投影（两位小数字符串），不再用两位单价反算
// （Spec `packaging-round-parts-and-e2e-fact-consistency.md` §4；断言口径没放宽，数值仍是 18080-16000）。
if(row['税金']!=='2080.00' || row['数量']!==1000 || row['成本状态']!=='暂估（缺口草稿）') throw Error(JSON.stringify(row));
'''
        run = subprocess.run(['node', '-e', script], text=True, capture_output=True)
        self.assertEqual(0, run.returncode, run.stderr)

    def test_manufacturing_snapshot_projects_business_parts(self):
        doc = {'business_parts_id': 'v1', 'business_parts': [
            {'business_part_code': 'BP-1', 'name': '内托', 'cad_fragments': [{}, {}]}]}
        with patch('tech_app.backend.services.packaging_parts.load_business_parts', return_value=doc), \
             patch.object(manufacturing_snapshot, 'packaging_parts_doc', return_value={'parts':[{'part_code':'fragment'}]}), \
             patch.object(manufacturing_snapshot, 'packaging_bom_doc', return_value={'built':True}), \
             patch.object(manufacturing_snapshot, 'packaging_route_doc', return_value={'built':True}), \
             patch.object(manufacturing_snapshot, 'packaging_cost_doc', return_value={'built':True}):
            snap = manufacturing_snapshot.packaging_snapshot('isolated')
        self.assertEqual('BP-1', snap['parts'][0]['part_id'])

    def test_layout_assignment_validates_part_codes_and_does_not_set_yield(self):
        doc = {'business_parts':[{'business_part_code':'BP1'}],
               'layout_rows':[{'entity_id':'E1','sheet_mm':[200,300],'n_up':2}]}
        updated = packaging_layout.set_layout_assignment(doc,'E1',['BP1'],actor='PE1')
        self.assertEqual(['BP1'], updated['layout_rows'][0]['part_codes'])
        self.assertNotIn('material_quantity', updated['layout_rows'][0])
        with self.assertRaises(ValueError):
            packaging_layout.set_layout_assignment(doc,'E1',['UNKNOWN'],actor='PE1')

    def test_packaging_markup_does_not_use_battery_rule_endpoint(self):
        src = (ROOT / '确认需求解析结果.html').read_text()
        body = src[src.index('async function runMarkupStep('):src.index('async function runMarkupStep(')+2400]
        self.assertTrue("currentIndustry() === 'packaging'" in body)
        self.assertLess(body.index('renderPackagingQuotePanel'), body.index('ensureStep1Context'))

    def test_whole_cad_viewport_is_square(self):
        src = (ROOT / 'tech_app/frontend/drawing-flow.css').read_text()
        rule = src[src.index('.file-preview-drawing .packaging-part-shape-viewport {'):].split('}',1)[0]
        self.assertIn('aspect-ratio: 1 / 1', rule)
        self.assertNotIn('60vh', rule)

    def test_cost_route_dispatches_packaging_to_business_cost(self):
        src = (ROOT / 'tech_app/backend/main.py').read_text()
        body = src[src.index('def run_cost_review_part('):src.index('@app.post("/api/projects/{project_id}/cost-review/assembly")')]
        self.assertIn('await packaging_business_part_cost', body)

    def test_quote_detail_preserves_addons_and_discount(self):
        src = (ROOT / '确认需求解析结果.html').read_text()
        script = '''let LAST_PACKAGING_PACKAGE={requirement:{title:'礼盒'}};
const FORMS=[{section_id:'s5_detail',step:5,columns:[]}];
let row;function renderTableSection(ui){row=ui.rows[0]};
''' + js_function(src,'packagingQuoteDetailValues') + js_function(src,'applyPackagingQuoteRows') + '''
applyPackagingQuoteRows({box_type_code:'BOX',quote_quantity:1000,currency:'CNY',cost_total:12,
margin_price:16,addon_total:4,subtotal_unit:20,discount_rate:0.1,net_unit_price:18,
untaxed_unit_price:16,untaxed_total:18000,taxed_total:20340,tax_rate:0.13,draft:false});
// 2026-10-08：折后价格取引擎净单价原值（`String()` 保留精度），加价与折扣口径不变
// （Spec `packaging-round-parts-and-e2e-fact-consistency.md` §4）。
if(row['报价']!==20 || row['折扣']!==0.9 || row['折后价格']!=='18')throw Error(JSON.stringify(row));
'''
        run = subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,run.returncode,run.stderr)

    def test_whole_cost_uses_packaging_engine_not_generic_assembly(self):
        src = (ROOT / 'tech_app/backend/main.py').read_text()
        body = src[src.index('def run_cost_review_assembly('):src.index('def run_cost_review_assembly(')+2300]
        self.assertIn('packaging_cost.build_cost', body)

    def test_requirement_converter_note_probes_backend(self):
        src = (ROOT / 'tech_app/frontend/requirement-create.js').read_text()
        self.assertTrue('/api/capabilities/cad-converter' in src)

    def test_packaging_draft_navigation_does_not_forge_approval(self):
        from tech_app.backend.services import workflow_projection, integration
        facts = {'requirement': {'status':'draft','data':{'industry':'packaging'}},
                 'plan':integration.IntegrationPlan(), 'packaging_cad_ir':{'ir_id':'v1'}}
        rows = workflow_projection._rows_for('isolated',facts,'process_manager')
        drawing = next(row for row in rows if row['key']=='2.1')
        self.assertTrue(drawing['actionable'])
        confirm = next(row for row in rows if row['key']=='1.2')
        self.assertFalse(confirm['completed'])

    def test_packaging_cost_rejects_stale_single_part_and_never_reads_generic_cost(self):
        from tech_app.backend.services import packaging_parts
        ir = cost_review.ir_from_packaging_parts({'business_parts':[
            {'business_part_code':'BP-A','name':'盖纸'}]})
        with patch.object(cost_review.store,'load_requirement',return_value={'data':{'industry':'packaging'}}), \
             patch.object(cost_review.store,'load_cost',side_effect=AssertionError('generic cost read')), \
             patch.object(packaging_parts,'load_business_parts',return_value={'business_parts_hash':'current'}), \
             patch.object(packaging_parts,'load_part_cost',return_value={
                 'business_parts_hash':'old','analysis':{'items':[{'unit_price':100}]}}):
            rows=cost_review._part_rows('isolated',ir)
        self.assertFalse(rows[0]['has_cost'])
        self.assertEqual(0,rows[0]['unit_cost'])

    def test_packaging_single_material_cost_does_not_apply_battery_coefficients(self):
        from tech_app.backend.services import packaging_parts
        ir=cost_review.ir_from_packaging_parts({'business_parts':[{'business_part_code':'BP-A'}]})
        with patch.object(cost_review.store,'load_requirement',return_value={'data':{'industry':'packaging'}}), \
             patch.object(packaging_parts,'load_business_parts',return_value={'business_parts_hash':'v1'}), \
             patch.object(packaging_parts,'load_part_cost',return_value={'business_parts_hash':'v1',
                 'analysis':{'items':[{'category':'material','quantity':1,'unit_price':5,'amount':5}]}}):
            rows=cost_review._part_rows('isolated',ir)
        self.assertEqual(5,rows[0]['unit_cost'])
        self.assertEqual('material_only',rows[0]['cost_scope'])

    def test_packaging_total_is_not_added_to_parts_and_incomplete_is_not_ready(self):
        from tech_app.backend.services import integration, packaging_parts, packaging_cost
        doc={'business_parts_hash':'v1','business_parts':[{'business_part_code':'BP-A','name':'盖纸'}]}
        with patch.object(cost_review.store,'load_requirement',return_value={'data':{'industry':'packaging'}}), \
             patch.object(packaging_parts,'load_business_parts',return_value=doc), \
             patch.object(packaging_parts,'load_part_cost',return_value={}), \
             patch.object(packaging_cost,'load_cost',return_value={'built':True,'total_cost':12,'has_gaps':True}):
            data=cost_review.summarize('isolated',None,integration.IntegrationPlan())
        self.assertEqual(1,len(data['parts']))
        self.assertEqual(12,data['final']['total'])
        self.assertFalse(data['ready'])

    def test_packaging_draft_whole_cost_can_preview_without_signing_prior_steps(self):
        from tech_app.backend.services import workflow_projection
        facts={'requirement':{'status':'draft','data':{'industry':'packaging'}},
               'packaging_cad_ir':{'ir_id':'v1'},
               'packaging_business_parts':{'business_parts':[{'business_part_code':'BP1'}]}}
        with patch.object(workflow_projection,'_judge',return_value={'status':'not_started','completed':False}):
            rows=workflow_projection._rows_for('isolated',facts,'finance_manager')
        row=next(r for r in rows if r['key']=='4.2')
        self.assertTrue(row['actionable'])
        self.assertTrue(row['draft_only'])
        self.assertFalse(row['completed'])

    def test_packaging_cost_gap_and_stale_are_in_confirmation_gaps(self):
        data={'counts':{'missing':[],'assembly_costed':True,'zero':[],'parts':1},
              'packaging_cost':{'stale':True,'gaps':[{'code':'rate_missing','message':'费率缺失'}]}}
        result=cost_review.confirm_gaps('isolated',None,None,data)
        self.assertIn('packaging:stale',result['codes'])
        self.assertIn('费率缺失',result['fields'])

    def test_industry_choice_runtime_respects_source_and_user_override(self):
        src=(ROOT/'tech_app/frontend/tech-task.js').read_text()
        fn=src[src.index('function industryChoice()'):src.index('function render()')]
        script='''const TT_INDUSTRIES=['battery','packaging','semiconductor'];
const TT_INDUSTRY_KEY='test'; const TT_DEFAULT_INDUSTRY='battery';
let task={payload:{industry:'packaging'}};let picked=null;
const document={getElementById:()=>picked}; const localStorage={getItem:()=> 'battery'};
''' + fn + '''
if(industryChoice()!=='packaging')throw Error('stale preference won');
picked={value:'semiconductor'};
if(industryChoice()!=='semiconductor')throw Error('override lost');
picked=null;task={payload:{industry:'unknown'}};
if(industryChoice()!=='battery')throw Error('invalid source accepted');
'''
        run=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,run.returncode,run.stderr)

    def test_cad_context_does_not_require_reference_table_or_call_all_pending_structural(self):
        src=(ROOT/'tech_app/frontend/app.js').read_text()
        def extract(name):
            start=src.index('function '+name+'(')
            return src[start:src.index('\n}',start)+2]
        script=extract('packagingBusinessPartTruthLabel')+'\n'+extract('packagingBusinessThumbnailReasonText')+'''
if(packagingBusinessPartTruthLabel('pending_confirmation',{})!=='证据待确认')throw Error('invented structural evidence');
const copy=packagingBusinessThumbnailReasonText('thumbnail_source_has_none',true);
if(!copy.includes('无需导入对照表')||!copy.includes('CAD'))throw Error(copy);
'''
        run=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,run.returncode,run.stderr)

    def test_cached_quote_repopulates_new_step_tables(self):
        src=(ROOT/'确认需求解析结果.html').read_text()
        script='''const currentSessionId='same'; let LAST_PACKAGING_PACKAGE=null;
let LAST_PACKAGING_QUOTE={gross_margin_rate:0};let PACKAGING_RESTORED_RATE=0;
const PACKAGING_PANEL_KEY='BOX|v1|12|0';const window={PackagingQuotePanel:{}};
let applied=0;function applyPackagingQuoteRows(){applied++;}
function restorePackagingPricing(){} function packagingMarginRate(){return 0;}
'''+'async '+js_function(src,'renderPackagingQuotePanel')+'''
(async()=>{await renderPackagingQuotePanel({packaging_package:{box_type:{confirmed_box_type:'BOX'},
source:{result_version:'v1'},cost:{total_cost:12}}});
if(applied!==1)throw Error('cached quote left newly rendered tables empty');})().catch(e=>{console.error(e);process.exitCode=1});
'''
        run=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,run.returncode,run.stderr)


if __name__ == '__main__':
    unittest.main()

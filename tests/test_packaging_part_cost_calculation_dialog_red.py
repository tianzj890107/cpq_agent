import copy
import pathlib
import subprocess
import tempfile
import json
import re
import unittest
from tech_app.backend.services import packaging_cost_ledger

ROOT=pathlib.Path(__file__).resolve().parents[1]
JS=(ROOT/'tech_app/frontend/cost-review.js').read_text()

class CalculationDialogTests(unittest.TestCase):
    def test_detail_uses_same_loss_and_usage_and_keeps_formula_inputs(self):
        doc={'business_parts':[{'business_part_code':'P','name':'面纸','reference':{'quantity':2}}]}
        cost={'built':True,'estimate_id':'E','quote_quantity':1000,'material_total':11,'process_total':3,'total_cost':14,'items':[
            {'part_code':'P','cost_category':'material','amount':10,'loss_rate':.1,'expression':'sheet_area*rate','formula_code':'F','formula_version':'v1','inputs_json':'{"sheet_area":2,"rate":5}', 'source_ref':'KB'},
            {'part_code':'P','cost_category':'die_cutting','amount':3,'min_charge_applied':True,'inputs_json':'{}'}]}
        original=copy.deepcopy(cost)
        row=packaging_cost_ledger.project(doc,cost)['parts'][0]
        details=row.get('calculation_details')
        self.assertIsNotNone(details,'No canonical calculation evidence for dialog')
        self.assertEqual(7,sum(x['unit_amount'] for x in details))
        self.assertEqual(5.5,details[0]['unit_amount'])
        self.assertEqual({'sheet_area':2,'rate':5},details[0]['inputs'])
        self.assertEqual('sheet_area*rate',details[0]['expression'])
        self.assertEqual('KB',details[0]['source_ref'])
        self.assertTrue(details[1]['min_charge_applied'])
        self.assertEqual(original,cost)

    def test_all_rows_are_clickable_but_recalculate_is_independent(self):
        self.assertTrue('data-cr-cost-detail=' in JS)
        self.assertTrue('function crOpenCostDetail' in JS)
        self.assertTrue('crBindCostDetails();' in JS)

    def test_dialog_renderer_discloses_precision_and_missing_data_and_escapes(self):
        self.assertTrue('function crCostDetailHtml' in JS)
        fn='function crCostDetailHtml'+JS.split('function crCostDetailHtml',1)[1].split('function crOpenCostDetail',1)[0]
        script=r'''const esc=x=>String(x??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));const crMoney=x=>String(x);const crData={packaging_cost:{quote_quantity:1000}};'''+fn+r'''
const html=crCostDetailHtml({id:'P',name:'<script>',quantity:1,has_cost:true,unit_cost:3.919,subtotal:3.919,breakdown:{material:.665,processing:3.254},calculation_details:[{category:'material',inputs:{rate:0},expression:'<script>',note:'<script>',unit_amount:.665,amount:1,amount_with_loss:1,loss_rate:0}]});
if(html.includes('<script>')||!html.includes('&lt;script&gt;')||!html.includes('未舍入')||!html.includes('0.665'))throw Error('Unsafe or rounded evidence');
if(!crCostDetailHtml({id:'M',quantity:1,has_cost:false,calculation_details:[]}).includes('没有已保存'))throw Error('invented missing evidence');
'''
        p=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_broken_inputs_are_disclosed_not_fabricated(self):
        doc={'business_parts':[{'business_part_code':'P','reference':{'quantity':1}}]}
        cost={'built':True,'material_total':1,'total_cost':1,'items':[
            {'part_code':'P','cost_category':'material','amount':1,'inputs_json':'not-json'}]}
        detail=packaging_cost_ledger.project(doc,cost)['parts'][0]['calculation_details'][0]
        self.assertEqual({},detail['inputs'])
        self.assertTrue(detail['inputs_error'])
        self.assertEqual(1,detail['unit_amount'])

    def test_formula_and_nested_parameters_display_chinese_without_changing_inputs(self):
        fn='function crCostDetailHtml'+JS.split('function crCostDetailHtml',1)[1].split('function crOpenCostDetail',1)[0]
        script=r'''const esc=x=>String(x??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));const crMoney=x=>String(x);const crData={packaging_cost:{quote_quantity:1000}};'''+fn+r'''
const row={id:'P',quantity:1,has_cost:true,unit_cost:1,subtotal:1,calculation_details:[{category:'material',cost_category:'material',expression:'MAX(cut_length*cut_width*gsm/1000000,minimum_charge/quote_quantity)',inputs:{cut_length:100,cut_width:80,gsm:225,minimum_charge:100,quote_quantity:1000,nested:{mystery_rate:0},gsm_source:'poc_mock'},assumptions:['quote_quantity=默认=1000'],unit_amount:1}]};
const original=JSON.stringify(row),html=crCostDetailHtml(row);
for(const raw of ['cut_length','cut_width','gsm','quote_quantity','minimum_charge','mystery_rate','poc_mock'])if(html.includes(raw))throw Error('English field leaked: '+raw);
for(const cn of ['开料长','开料宽','克重','最低收费','模拟参考','未命名参数'])if(!html.includes(cn))throw Error('Missing Chinese label: '+cn);
if(!html.includes('MAX(')||html.includes('取最大值'))throw Error('MAX must remain MAX');
if(JSON.stringify(row)!==original)throw Error('Changed stored inputs');
'''
        p=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_all_registered_rule_expression_variables_have_chinese_labels(self):
        rules=json.loads((ROOT/'tech_app/agent_knowledge/rules/packaging_cost_rules.json').read_text())
        variables=set()
        def walk(value):
            if isinstance(value,dict):
                expression=value.get('expression')
                if isinstance(expression,str):variables.update(re.findall(r'\b[a-z_][a-z0-9_]*\b',expression))
                for child in value.values():walk(child)
            elif isinstance(value,list):
                for child in value:walk(child)
        walk(rules)
        fn='function crCostDetailHtml'+JS.split('function crCostDetailHtml',1)[1].split('function crOpenCostDetail',1)[0]
        script="const esc=x=>String(x??'');const crMoney=x=>String(x);const crData={};"+fn
        script+="const inputs="+json.dumps(dict.fromkeys(sorted(variables),0))+";"
        script+="const html=crCostDetailHtml({quantity:1,has_cost:true,calculation_details:[{category:'material',inputs,expression:Object.keys(inputs).join('+')}]});if(html.includes('未命名参数'))throw Error('Registered variable untranslated');for(const key of Object.keys(inputs))if(html.includes(key))throw Error('English variable: '+key);"
        p=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_live_extra_fields_are_named_and_unit_slash_is_not_operator(self):
        fn='function crCostDetailHtml'+JS.split('function crCostDetailHtml',1)[1].split('function crOpenCostDetail',1)[0]
        script="const esc=x=>String(x??'');const crMoney=x=>String(x);const crData={};"+fn+r'''
const html=crCostDetailHtml({quantity:1,has_cost:true,calculation_details:[{category:'material',expression:'film_kg_price/quote_quantity',inputs:{cut_size_source:'legacy_allowance_0_5mm',cut_allowance_mm:[0,5],price_unit:'元/吨',price_unit_status:'poc_mock',loss_rate_source:'material.standard_loss_rate',mock:true,standard_seconds:30,section_id:'S1',parameter_source:'part_route_parameters',film_kg_price:10}}]});
if(html.includes('未命名参数')||html.includes('元 ÷ 千克')||!html.includes('膜重价（元/千克） ÷ 报价批量'))throw Error('Wrong unit or unnamed live fields');
for(const key of ['legacy_allowance_0_5mm','standard_loss_rate','part_route_parameters'])if(html.includes(key))throw Error('Source enum untranslated');
'''
        p=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_real_browser_row_dialog_and_recalculate_event_separation(self):
        chrome=pathlib.Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not chrome.exists():self.skipTest('Chrome unavailable')
        from tests.test_packaging_whole_part_process_unit_red import js_function
        source=ROOT/'tech_app/frontend/cost-review.js'
        script=r'''
const esc=v=>String(v??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const crMoney=v=>String(v);let recalculated=0;
const crData={packaging_cost:{quote_quantity:1000},parts:[{id:'P',name:'左盖面纸',quantity:1,has_cost:true,unit_cost:3.919,subtotal:3.919,breakdown:{material:.665,processing:3.254},calculation_details:[{category:'material',expression:'area*rate',inputs:{area:1,rate:.665},unit_amount:.665,amount:.665,amount_with_loss:.665,quantity_divisor:1}]}]};
crData.parts[0].calculation_details=Array.from({length:12},()=>crData.parts[0].calculation_details[0]);
'''
        script+='\n'.join(js_function(source,name) for name in ('crCostDetailHtml','crOpenCostDetail','crBindCostDetails'))
        script+=r'''
(async()=>{
 const row=document.querySelector('[data-cr-cost-detail]'),button=document.getElementById('recalc');button.onclick=()=>recalculated++;
 crBindCostDetails();row.click();const opened=document.querySelector('dialog')?.open===true;
 const box=document.querySelector('dialog').getBoundingClientRect();
 const content=document.querySelector('.cr-cost-details-body');
 const fullWorkspace=box.left>=7&&box.top>=7&&box.width>=innerWidth-17&&box.height>=innerHeight-17;
 const internalScroll=content.scrollHeight>content.clientHeight;
 document.querySelector('[data-cr-detail-close]').click();await new Promise(r=>setTimeout(r,40));
 button.click();const independent=!document.querySelector('dialog')&&recalculated===1;
 row.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));const keyboard=document.querySelector('dialog')?.open===true;
 document.querySelector('dialog').close();await new Promise(r=>setTimeout(r,40));
 document.getElementById('probe').textContent=JSON.stringify({opened,independent,keyboard,fullWorkspace,internalScroll,focusRestored:document.activeElement===row,insideViewport:box.left>=0&&box.right<=innerWidth});
})();
'''
        css='\n'.join((ROOT/'tech_app/frontend'/name).read_text() for name in ('workbench.css','inline-analysis.css','cost-review.css'))
        with tempfile.TemporaryDirectory() as directory:
            path=pathlib.Path(directory)/'detail.html'
            path.write_text('<!doctype html><meta charset="utf-8"><style>'+css+'</style><table><tr tabindex="0" data-cr-cost-detail="P"><td>左盖面纸</td><td><button id="recalc">重算</button></td></tr></table><pre hidden id="probe"></pre><script>'+script+'</script>')
            result=subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run','--disable-background-networking','--user-data-dir='+str(pathlib.Path(directory)/'profile'),'--window-size=640,900','--virtual-time-budget=1000','--dump-dom',path.as_uri()],capture_output=True,text=True,timeout=30)
            self.assertEqual(0,result.returncode,result.stderr[-500:])
            match=re.search(r'<pre hidden="" id="probe">(.*?)</pre>',result.stdout,re.S)
            self.assertIsNotNone(match,result.stdout[-800:])
            metrics=json.loads(match[1]);self.assertTrue(all(metrics.values()),metrics)

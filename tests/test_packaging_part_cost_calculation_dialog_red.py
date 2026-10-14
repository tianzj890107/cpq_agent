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
const html=crCostDetailHtml({id:'P',name:'<script>',quantity:1,has_cost:true,unit_cost:3.919,subtotal:3.919,breakdown:{material:.665,processing:3.254},calculation_details:[{category:'material',inputs:{rate:0},expression:'<script>',unit_amount:.665,amount:1,amount_with_loss:1,loss_rate:0}]});
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

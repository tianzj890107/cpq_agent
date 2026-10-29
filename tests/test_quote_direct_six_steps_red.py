"""直接普通报价：真实状态转换与金额/权限边界，不连接线上数据库。"""
import copy
import importlib
import io
import json
import subprocess
import re
import unittest
import zipfile
from contextlib import contextmanager
from unittest.mock import patch
from pathlib import Path


class DirectQuoteTests(unittest.TestCase):
    def setUp(self):
        self.q = importlib.import_module('cpq_direct_quote')

    def inputs(self):
        return {
            1: dict(product_name='茅台酒盒', product_code='YT-RB-01001-A', customer='测试客户',
                    specification='200×150×80mm；天地盖；配合间隙1.8mm；V槽；盖/底180g铜版纸亮/哑膜；灰板2.5mm', quantity=1000),
            2: dict(unit_cost='10.125', currency='CNY', cost_source='测试已核实成本单C-1', confirmed=True, demo=True),
            3: dict(margin_percent='25'),
            4: dict(addon_per_unit='2', tax_percent='13'),
            5: {}, 6: {},
        }

    def state_at(self, step):
        state = self.q.new_state()
        for n in range(1, step):
            state = self.q.advance(state, n, self.inputs()[n])
        return state

    def test_complete_six_steps_and_exact_money(self):
        state = self.state_at(7)
        self.assertEqual(7, state['next_step'])
        self.assertEqual(6, len(state['steps']))
        self.assertEqual('15.50', state['quote']['net_unit_price'])
        self.assertEqual('15500.00', state['quote']['net_total'])
        self.assertEqual('2015.00', state['quote']['tax_amount'])
        self.assertEqual('17515.00', state['quote']['gross_total'])
        self.assertTrue(state['quote']['demo'])

    def test_missing_or_unconfirmed_cost_and_invalid_numbers_are_rejected(self):
        for key, value in [('unit_cost', ''), ('unit_cost','0'), ('unit_cost','-1'),
                           ('unit_cost','NaN'), ('unit_cost','Infinity'), ('unit_cost',True),
                           ('cost_source',''), ('confirmed',False), ('demo',None), ('currency','')]:
            with self.subTest(key=key, value=value):
                data = self.inputs()[2]; data[key] = value
                with self.assertRaises(ValueError): self.q.advance(self.state_at(2), 2, data)

    def test_quantity_rates_and_addons_validation(self):
        for step,key,value in [(1,'quantity',0),(1,'quantity',1.5),(1,'quantity',True),
                               (3,'margin_percent',100),(3,'margin_percent',-1),
                               (4,'tax_percent',101),(4,'tax_percent','NaN'),(4,'addon_per_unit',-1)]:
            with self.subTest(step=step,key=key,value=value):
                data=self.inputs()[step];data[key]=value
                with self.assertRaises(ValueError):self.q.advance(self.state_at(step),step,data)

    def test_tiny_or_excessive_precision_cannot_generate_a_zero_quote(self):
        state=self.state_at(2)
        cost=self.inputs()[2];cost['unit_cost']='0.000001'
        state=self.q.advance(state,2,cost)
        state=self.q.advance(state,3,{'margin_percent':'0'})
        with self.assertRaises(ValueError):self.q.advance(state,4,{'addon_per_unit':'0','tax_percent':'0'})
        with self.assertRaises(ValueError):self.q.advance(self.state_at(3),3,{'margin_percent':'99.999999999999999999999999'})

    def test_no_skipping_no_mutation_and_idempotent_replay(self):
        state=self.state_at(2); before=copy.deepcopy(state)
        with self.assertRaises(ValueError):self.q.advance(state,3,self.inputs()[3])
        self.assertEqual(before,state)
        completed=self.q.advance(state,2,self.inputs()[2])
        self.assertEqual(completed,self.q.advance(completed,2,self.inputs()[2]))
        changed=self.inputs()[2];changed['unit_cost']='99'
        with self.assertRaises(ValueError):self.q.advance(completed,2,changed)

    def test_client_quote_cannot_replace_computed_result(self):
        state=self.state_at(5)
        result=self.q.advance(state,5,{'net_unit_price':'0.01','demo':False})
        self.assertEqual('15.50',result['quote']['net_unit_price'])
        self.assertTrue(result['quote']['demo'])

    def test_word_has_commercial_values_and_demo_disclosure_not_cost(self):
        state=self.state_at(7)
        blob=self.q.document(state)
        with zipfile.ZipFile(io.BytesIO(blob)) as doc:
            xml=doc.read('word/document.xml').decode()
        for value in ['茅台酒盒','17515.00','演示报价']:self.assertIn(value,xml)
        for value in ['10.125','测试已核实成本单C-1','毛利']:self.assertNotIn(value,xml)
        with self.assertRaises(ValueError):self.q.document(self.state_at(6))

    def test_page_and_authenticated_route_are_wired(self):
        root=Path(__file__).resolve().parents[1]
        self.assertIn('quote-direct.js',(root/'确认需求解析结果.html').read_text())
        self.assertIn('/wf/card/direct-quote',(root/'cpq_suite_server.py').read_text())

    def test_modified_page_inline_and_external_scripts_parse(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/'确认需求解析结果.html').read_text()
        scripts=[code for attrs,code in re.findall(r'<script\b([^>]*)>([\s\S]*?)</script>',html) if 'src=' not in attrs]
        for script in scripts+[(root/'quote-direct.js').read_text()]:
            result=subprocess.run(['node','--check'],input=script,capture_output=True,text=True)
            self.assertEqual(0,result.returncode,result.stderr)

    def test_special_prompt_is_validated_and_prepares_all_four_inputs(self):
        prompt='茅台酒盒，普通报价六步验收。\n【直接报价参数】'+json.dumps(self.inputs_for_prompt(),ensure_ascii=False)+'【/直接报价参数】'
        result=self.q.parse_prompt(prompt)
        self.assertEqual('1.8mm',result['1']['specification'].split('配合间隙')[1].split('；')[0])
        self.assertTrue(result['2']['demo'])
        self.assertEqual('25',result['3']['margin_percent'])
        for bad in [prompt.replace('【/直接报价参数】',''),prompt+prompt,'【直接报价参数】{}【/直接报价参数】']:
            with self.assertRaises(ValueError):self.q.parse_prompt(bad)

    def inputs_for_prompt(self):
        return {str(step):values for step,values in self.inputs().items() if step<=4}

    def natural_prompt(self):
        return """茅台酒盒，客户是贵州茅台。

成品内尺寸：200mm×150mm×80mm。
盖面纸：铜版纸，180克重，亮膜；
底面纸：铜版纸，180克重，哑膜；
盖板材：灰板，厚度2.5mm；
V槽，闭合方式：天地盖；
包装品类：酒盒，盒型：高档酒盒；
配合间隙：1.8mm。
预计首批定1000个。"""

    def test_plain_language_prompt_extracts_customer_facts_only(self):
        result=self.q.parse_prompt(self.natural_prompt())
        self.assertEqual('茅台酒盒',result['1']['product_name'])
        self.assertEqual('贵州茅台',result['1']['customer'])
        self.assertEqual(1000,result['1']['quantity'])
        self.assertIn('200mm×150mm×80mm',result['1']['specification'])
        self.assertIn('配合间隙：1.8mm',result['1']['specification'])
        self.assertIn('厚度2.5mm',result['1']['specification'])
        self.assertNotIn('2',result)
        self.assertEqual('180',result['_customer_requirement']['lid_face_paper_gsm'])
        self.assertEqual('哑膜',result['_customer_requirement']['base_lamination'])

    def test_plain_language_does_not_guess_missing_or_conflicting_specs(self):
        prompt=self.natural_prompt()
        for old,new in [('客户是贵州茅台',''),('预计首批定1000个',''),
                        ('配合间隙：1.8mm',''),('150mm','0mm'),
                        ('盖面纸：铜版纸，180克重，亮膜','盖面纸：铜版纸，亮膜'),
                        ('预计首批定1000个','预计首批定1000个，数量2000个')]:
            with self.subTest(old=old),self.assertRaises(ValueError):self.q.parse_prompt(prompt.replace(old,new))

    def test_plain_language_supports_original_unit_and_separator_style(self):
        prompt=self.natural_prompt().replace('×','*').replace('mm','毫米').replace('，',',').replace('：',':')
        result=self.q.parse_prompt(prompt)
        self.assertEqual(1000,result['1']['quantity'])
        self.assertEqual('180',result['_customer_requirement']['lid_face_paper_gsm'])

    def test_page_javascript_sends_six_steps_and_preserves_demo_choice(self):
        root=Path(__file__).resolve().parents[1]
        script=r"""
const assert=require('assert'),fs=require('fs'),vm=require('vm');
const listeners={};let panel,entry={};
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.style={};this.value='';}
 appendChild(node){this.children.push(node);return node;}
 replaceChildren(){this.children=[];} setAttribute(){} remove(){} click(){}
 reportValidity(){return true;}
 get elements(){const out={};const visit=e=>{if(e.name)out[e.name]=e;(e.children||[]).forEach(visit);};visit(this);return out;}
}
const document={body:new Element('body'),createElement:tag=>new Element(tag),
 getElementById:()=>entry,createTextNode:text=>({text}),addEventListener:(name,fn)=>{listeners[name]=fn;}};
const promptInputs=__PROMPT_INPUTS__;
let current={mode:'direct_quote_v1',next_step:1,steps:{},quote:null};let enabled=false,commands=[],alerts=[];
const context={document,WF:{},STEP1_LAST_REQ:'',STEP1_FORM_TASK:null,STEP1_REINPUT:false,addUserBubble(){},
 FormData:class{constructor(form){return Object.entries(form.elements).filter(([k,e])=>!e.disabled&&(e.type!=='checkbox'||e.checked)).map(([k,e])=>[k,e.value]);}},
 Object,URL:{},setTimeout};
const window={CpqDirectHost:{session:()=> 'S',user:()=>({role_code:'sales_mgr'}),busy:()=>false,
 seed:()=>({}),refresh:async()=>{},lock(){},say(){},mount(node){panel=node;context.WF.directQuote=true;}},alert:msg=>alerts.push(msg)};
context.window=window;
context.cpqAuthFetch=async(path,opts)=>{
 if(opts&&opts.body){const body=JSON.parse(opts.body);commands.push(body);
  if(body.action==='prepare'){enabled=true;current.prompt_inputs=promptInputs;}
  if(body.action==='complete'){assert.equal(body.step,current.next_step);current.steps[String(body.step)]=body.data;current.next_step++;}
 }
 return {ok:true,json:async()=>({ok:true,state:enabled?JSON.parse(JSON.stringify(current)):null})};
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('quote-direct.js','utf8'),context);
const page=fs.readFileSync('确认需求解析结果.html','utf8');
vm.runInContext(page.slice(page.indexOf('    async function runStep1('),page.indexOf('    function applyUiEvent(')),context);
vm.runInContext(page.slice(page.indexOf('    async function confirmStep('),page.indexOf('    /* ================= 报价工作流')),context);
(async()=>{
 const prompt='茅台酒盒验收\n【直接报价参数】'+JSON.stringify(promptInputs)+'【/直接报价参数】';
 assert.equal(await context.runStep1(prompt,prompt),true);
 assert.equal(commands[0].action,'prepare');
 for(let step=1;step<=6;step++)await context.confirmStep();
 assert.equal(current.next_step,7);assert.equal(commands.length,7);
 assert.equal(commands[2].data.unit_cost,'10.125');
 assert.equal(commands[2].data.demo,true);assert.equal(commands[2].data.confirmed,true);
 assert.equal(alerts.length,0);assert(panel);
})().catch(e=>{console.error(e);process.exitCode=1;});
""".replace('__PROMPT_INPUTS__',json.dumps(self.inputs_for_prompt(),ensure_ascii=False))
        result=subprocess.run(['node','-e',script],cwd=root,capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)


class WorkflowTests(unittest.TestCase):
    """受控存储执行真实 command + complete_step，模型化事务而不接触业务库。"""
    def setUp(self):
        self.q=importlib.import_module('cpq_direct_quote')
        import cpq_wf
        self.wf=cpq_wf
        self.user=dict(user_id='100',role_code='sales_mgr',role_name='销售经理',display_name='测试销售')
        self.card=dict(card_id='1',session_id='S',creator_user_id='100',current_owner='100',
                       current_step=1,overall_status='draft')
        self.snaps={}; self.statuses={i:'pending' for i in range(1,7)}; self.events=[];self.tasks=[]
        self.fail_store=False
        owner=self
        class Conn:
            @contextmanager
            def transaction(conn):
                saved=copy.deepcopy((owner.card,owner.snaps,owner.statuses,owner.events))
                try:yield
                except Exception:
                    owner.card,owner.snaps,owner.statuses,owner.events=saved
                    raise
            def close(conn):pass
        self.conn=Conn()
        for item in [patch('cpq_customer_quote.load_catalog',side_effect=self.customer_catalog),
                     patch('cpq_wf.tx_connect',return_value=self.conn),
                     patch('cpq_auth._connect',return_value=self.conn),
                     patch('cpq_wf._fetch_card',side_effect=lambda c,s:copy.deepcopy(self.card) if s=='S' else None),
                     patch('cpq_wf.step_snapshot',side_effect=lambda s,n,conn=None:copy.deepcopy(self.snaps.get(n))),
                     patch('cpq_auth._exec',side_effect=self.execute),
                     patch('cpq_wf._log',side_effect=lambda *args:self.events.append(args[1:]))]:
            item.start();self.addCleanup(item.stop)

    inputs=DirectQuoteTests.inputs
    inputs_for_prompt=DirectQuoteTests.inputs_for_prompt
    natural_prompt=DirectQuoteTests.natural_prompt

    def execute(self,conn,sql,args=()):
        from unittest.mock import Mock
        norm=' '.join(sql.split()); rows=[]
        if norm.startswith('SELECT step_no, step_name, role_code FROM cpq_wf_step_perm'):rows=self.wf.QUOTE_STEPS
        elif norm.startswith('SELECT card_id FROM cpq_wf_card'):rows=[(1,)]
        elif norm.startswith('SELECT step_no FROM cpq_wf_card_step'):
            rows=[(n,) for n,s in self.statuses.items() if s!='pending' and not(n==1 and s=='in_progress')]
        elif norm.startswith('SELECT task_id FROM cpq_wf_task'):rows=self.tasks
        elif 'FROM cpq_wf_task t' in norm:rows=[]
        elif 'jsonb_set' in norm:
            if self.fail_store:raise RuntimeError('storage write failed')
            self.snaps.setdefault(1,{})[self.q.KEY]=json.loads(args[0])
        elif norm.startswith("UPDATE cpq_wf_card_step SET status = 'done'"):
            uid,snapshot,completed,started,cid,step=args
            self.statuses[step]='done';self.snaps[step]=json.loads(snapshot) if snapshot else None
        elif norm.startswith('UPDATE cpq_wf_card SET current_step'):
            self.card.update(current_step=args[0],overall_status=args[1])
        elif norm.startswith('UPDATE cpq_wf_task SET status'):pass
        else:raise AssertionError('Unsupported SQL '+norm)
        result=Mock();result.fetchall.return_value=rows;result.fetchone.return_value=rows[0] if rows else None
        return result

    def run_to(self,step):
        self.q.command('S',self.user,'enable')
        for n in range(1,step+1):
            self.q.command('S',self.user,'complete',step=n,data=self.inputs()[n])

    def test_real_workflow_six_steps_no_handoff_and_persistent_read(self):
        self.run_to(6)
        self.assertEqual({n:'done' for n in range(1,7)},self.statuses)
        self.assertEqual('completed',self.card['overall_status'])
        self.assertEqual(6,self.card['current_step'])
        read=self.q.command('S',self.user)
        self.assertEqual(7,read['state']['next_step'])
        self.assertEqual('15.50',read['state']['quote']['net_unit_price'])
        before=copy.deepcopy((self.card,self.snaps,self.events))
        self.q.command('S',self.user,'complete',step=2,data=self.inputs()[2])
        self.assertEqual(before,(self.card,self.snaps,self.events))
        self.assertEqual(7,len(self.events))

    def test_atomic_rollback_when_state_write_fails(self):
        self.run_to(1)
        before=copy.deepcopy((self.card,self.snaps,self.statuses,self.events))
        self.fail_store=True
        with self.assertRaises(RuntimeError):self.q.command('S',self.user,'complete',step=2,data=self.inputs()[2])
        self.assertEqual(before,(self.card,self.snaps,self.statuses,self.events))

    def test_other_sales_viewer_and_process_cannot_enable_or_complete(self):
        for role,uid in [('sales_mgr','200'),('viewer','100'),('process_mgr','100')]:
            user=dict(self.user,role_code=role,user_id=uid)
            with self.assertRaises(self.wf.WfError):self.q.command('S',user,'enable')
        self.run_to(1)
        with self.assertRaises(self.wf.WfError):
            self.q.command('S',dict(self.user,user_id='200'),'complete',step=2,data=self.inputs()[2])

    def test_old_endpoint_cannot_forge_mode_or_complete_direct_steps(self):
        with self.assertRaises(self.wf.WfError):
            self.wf.complete_step('S',1,self.user,json.dumps({self.q.KEY:self.q.new_state()}),conn=self.conn)
        self.q.command('S',self.user,'enable')
        with self.assertRaises(self.wf.WfError):self.wf.complete_step('S',1,self.user,'{}',conn=self.conn)

    def test_legacy_card_retains_process_permission_and_handoff(self):
        self.assertFalse(self.wf._can_card_step(self.card,self.user,2,False))
        self.assertTrue(self.wf._can_card_step(self.card,dict(self.user,role_code='process_mgr'),2,False))
        with self.assertRaises(self.wf.WfError):self.wf.complete_step('S',2,self.user,'{}',conn=self.conn)
        result=self.wf.complete_step('S',1,self.user,'{}',conn=self.conn)
        self.assertTrue(result['need_handoff'])
        self.assertEqual('process_mgr',result['next_role_code'])

    def test_enable_rejects_existing_tasks_and_completed_steps(self):
        self.tasks=[(5,)]
        with self.assertRaises(ValueError):self.q.command('S',self.user,'enable')
        self.tasks=[];self.statuses[1]='done'
        with self.assertRaises(ValueError):self.q.command('S',self.user,'enable')

    def test_permission_response_restores_sales_step_two_without_changing_global_roles(self):
        self.run_to(1)
        rows=[dict(step_no=n,step_name=name,status=self.statuses[n],role_code=role)
              for n,name,role in self.wf.QUOTE_STEPS]
        with patch('cpq_wf.get_card',return_value=self.card),patch('cpq_wf.card_steps',return_value=rows):
            result=self.wf.card_detail('S',self.user)
            self.assertTrue(result['can_edit'])
            self.assertEqual('sales_mgr',result['need_role_code'])
            self.assertEqual('报价成本确认',result['steps'][1]['step_name'])
            self.assertFalse(self.wf.card_detail('S',dict(self.user,user_id='200'))['can_edit'])
        self.assertEqual('process_mgr',self.wf.role_of_step(2))

    def test_prompt_prepares_without_completing_and_bad_prompt_leaves_no_state(self):
        with self.assertRaises(ValueError):
            self.q.command('S',self.user,'prepare',data={'prompt':'【直接报价参数】{}【/直接报价参数】'})
        self.assertEqual({},self.snaps)
        prompt='【直接报价参数】'+json.dumps(self.inputs_for_prompt())+'【/直接报价参数】'
        prepared=self.q.command('S',self.user,'prepare',data={'prompt':prompt})
        self.assertEqual(1,prepared['state']['next_step'])
        self.assertEqual({n:'pending' for n in range(1,7)},self.statuses)
        self.assertEqual('10.125',prepared['state']['prompt_inputs']['2']['unit_cost'])

    def customer_catalog(self):
        facts={
          'inner_length':'200','inner_width':'150','inner_height':'80',
          'lid_face_paper':'铜版纸','base_face_paper':'铜版纸',
          'lid_face_paper_gsm':'180','base_face_paper_gsm':'180',
          'lid_lamination':'亮膜','base_lamination':'哑膜','lid_grey_board_thickness':'2.5',
          'closure_type':'天地盖','v_groove':'是','fit_clearance':'1.8',
          'packaging_category':'酒盒','box_family':'高档酒盒','quote_quantity':'1000',
          'cost_currency':'CNY'}
        active={'is_deleted':False,'effective_status':True,'latest':True}
        return {
          'products':[dict(active,product_item_code='TEST-WINE-200',product_item_name='茅台酒盒',
                           para_code=k,para_value=v,data_source='test-fixture-demo') for k,v in facts.items()],
          'costs':[dict(active,material_code='TEST-WINE-200',material_unit_price='10.125',
                        md_clm_material_cost_cnf_id='COST-1',data_source='test-fixture-demo')],
          'factors':[dict(factor_code='MARGIN-1',industry='packaging',factor_type='margin',
                          value='0.25',source='test-fixture-demo'),
                     dict(factor_code='TAX-1',industry='packaging',factor_type='tax',value='0.13',source='test-fixture-demo')],
          'addons':[dict(active,md_clm_material_price_rule_id='ADDON-1',data_source='packaging',
                        rule_classification='报价',rule_expression='def 加价金额 = 2\n基础价格 + 加价金额')],
        }

    def test_plain_prompt_prepares_and_completes_the_same_card(self):
        prepared=self.q.command('S',self.user,'prepare',data={'prompt':self.natural_prompt()})
        self.assertEqual([],prepared['state']['pricing_gaps'])
        self.assertEqual('10.125',prepared['state']['prompt_inputs']['2']['unit_cost'])
        for step in range(1,7):
            data=prepared['state']['prompt_inputs'].get(str(step),{})
            result=self.q.command('S',self.user,'complete',step=step,data=data)
        self.assertEqual('completed',result['card']['overall_status'])
        self.assertEqual('17515.00',result['state']['quote']['gross_total'])

    def test_customer_prompt_missing_system_cost_stops_at_step_two(self):
        catalog=self.customer_catalog();catalog['costs']=[]
        with patch('cpq_customer_quote.load_catalog',return_value=catalog):
            result=self.q.command('S',self.user,'prepare',data={'prompt':self.natural_prompt()})
        self.assertTrue(result['state']['pricing_gaps'])
        self.q.command('S',self.user,'complete',step=1,data=result['state']['prompt_inputs']['1'])
        with self.assertRaises(ValueError):self.q.command('S',self.user,'complete',step=2,data=self.inputs()[2])
        self.assertEqual('done',self.statuses[1]);self.assertEqual('pending',self.statuses[2])
        self.assertIsNone(self.q.state_for('S')['quote'])

    def test_authenticated_handler_runs_all_six_and_downloads_saved_document(self):
        import cpq_suite_server
        from unittest.mock import Mock
        handler=object.__new__(cpq_suite_server.Handler)
        handler.path='/wf/card/direct-quote';handler.command='POST'
        handler._token=lambda:'test-token';handler._send_json=Mock()
        body={'session_id':'S','action':'enable'}
        handler._read_json=lambda:body
        with patch('cpq_auth._backend','test'),patch('cpq_auth.whoami',return_value=self.user):
            self.assertTrue(handler._dispatch_wf())
            self.assertEqual(200,handler._send_json.call_args.args[0])
            for step in range(1,7):
                body={'session_id':'S','action':'complete','step':step,'data':self.inputs()[step]}
                self.assertTrue(handler._dispatch_wf())
                self.assertEqual(200,handler._send_json.call_args.args[0])
            handler.path='/wf/card/direct-quote/document?session_id=S';handler.command='GET'
            handler.send_response=Mock();handler.send_header=Mock();handler.end_headers=Mock();handler.wfile=io.BytesIO()
            self.assertTrue(handler._dispatch_wf())
            handler.send_response.assert_called_once_with(200)
            self.assertTrue(handler.wfile.getvalue().startswith(b'PK'))
            self.assertEqual('completed',self.card['overall_status'])


class CustomerCatalogTests(unittest.TestCase):
    def setUp(self):
        import cpq_customer_quote
        import cpq_direct_quote
        self.c=cpq_customer_quote;self.q=cpq_direct_quote
        fixture=WorkflowTests('test_plain_prompt_prepares_and_completes_the_same_card')
        self.catalog=fixture.customer_catalog()
        self.parsed=self.q.parse_prompt(fixture.natural_prompt())

    def test_parameter_and_price_rule_evidence_are_pinned(self):
        result=self.c.prepare_customer(self.parsed,self.catalog)
        evidence=result['_pricing_evidence']
        self.assertEqual(len(self.catalog['products']),len(evidence['product_parameters']))
        self.assertEqual('COST-1',evidence['cost_record']['md_clm_material_cost_cnf_id'])
        self.assertEqual('MARGIN-1',evidence['margin_rules'][0]['factor_code'])
        self.assertEqual('ADDON-1',evidence['addon_rules'][0]['md_clm_material_price_rule_id'])

    def test_only_exact_active_spec_quantity_and_current_cost_are_used(self):
        for section,key,value in [('products','is_deleted',True),('products','effective_status',False),
                                  ('costs','price_expiration_date','2000-01-01'),('costs','is_deleted',True),
                                  ('costs','data_source','unknown'),('costs','material_unit_price','0')]:
            catalog=copy.deepcopy(self.catalog);catalog[section][0][key]=value
            with self.subTest(section=section,key=key):
                result=self.c.prepare_customer(self.parsed,catalog)
                self.assertTrue(result['_pricing_gaps']);self.assertNotIn('2',result)
        for key,value in [('base_lamination','亮膜'),('quote_quantity','5000'),('lid_grey_board_thickness','2.0')]:
            catalog=copy.deepcopy(self.catalog)
            for row in catalog['products']:
                if row['para_code']==key:row['para_value']=value
            with self.subTest(key=key):self.assertTrue(self.c.prepare_customer(self.parsed,catalog)['_pricing_gaps'])

    def test_missing_addons_or_battery_rules_cannot_silently_mean_zero(self):
        for rows in [[],[dict(self.catalog['addons'][0],data_source='ywln-rule')],
                     [dict(self.catalog['addons'][0],rule_expression='基础价格 + 模型估算')]]:
            catalog=copy.deepcopy(self.catalog);catalog['addons']=rows
            result=self.c.prepare_customer(self.parsed,catalog)
            self.assertTrue(result['_pricing_gaps']);self.assertNotIn('4',result)

    def test_reference_standard_cost_is_never_an_order_cost(self):
        catalog=copy.deepcopy(self.catalog);catalog['costs']=[]
        catalog['boxes']=[dict(box_type_code='TEST-WINE-200',standard_cost='12.5',source_type='demo')]
        result=self.c.prepare_customer(self.parsed,catalog)
        self.assertTrue(result['_pricing_gaps']);self.assertNotIn('2',result)

    def test_system_prices_are_bound_and_client_cannot_promote_demo(self):
        prepared=self.c.prepare_customer(self.parsed,self.catalog)
        state=self.q.new_state();state.update(auto_pricing=True,pricing_gaps=[],
            prompt_inputs={k:v for k,v in prepared.items() if k in ('1','2','3','4')})
        state=self.q.advance(state,1,state['prompt_inputs']['1'])
        for key,value in [('demo',False),('unit_cost','0.01'),('cost_source','客户随意填价')]:
            data=copy.deepcopy(state['prompt_inputs']['2']);data[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.q.advance(state,2,data)

    def test_lookup_unavailable_does_not_enable_fake_empty_catalog(self):
        with patch('cpq_db.connect',side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(ValueError,'数据读取失败'):self.c.load_catalog()


if __name__ == '__main__':unittest.main()

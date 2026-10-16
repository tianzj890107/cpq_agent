import importlib,json,pathlib,re,subprocess,unittest,tempfile
from unittest.mock import patch,MagicMock

ROOT=pathlib.Path(__file__).resolve().parents[1]
PKG={'industry':'packaging','requirement':{'packaging_product_name':'酒盒','inner_length':220.5,'inner_width':90,'inner_height':90,'quote_quantity':1000,'currency':'CNY','closure_type':'双开门','face_paper_gsm':225},'box_type':{'confirmed_box_type':'WINE'},'params':{},'source':{'result_version':'v1'},'cost':{'total_cost':10,'quote_quantity':1000,'material_total':6,'process_total':3,'labor_total':.3,'packaging_total':.7,'other_total':0,'currency':'CNY','cost_profile':'detail_sheet/1','gaps':[]}}

def node(code):
    result=subprocess.run(['node','-e',code],cwd=ROOT,capture_output=True,text=True)
    if result.returncode:raise AssertionError(result.stderr)

class Workspace(unittest.TestCase):
    def test_zero_addon_and_percentage_inputs_are_not_lost(self):
        node('global.window=global;'+(ROOT/'tech_app/frontend/packaging-quote-workspace.js').read_text()+"\nconst p="+json.dumps(PKG)+";const r=PackagingQuoteWorkspace.readInputs([{'毛利率（%）':'0'}],[{'加价编码':'freight','金额（元/只）':'0'}],{'数量':2000,'税率':0,'折扣':.9},null,p);if(r.gross_margin_rate!==0||r.addons.freight!==0||r.tax_rate!==0||r.quote_quantity!==2000||Math.abs(r.discount.rate-.1)>1e-9)throw Error('0 or percent lost');let denied=false;try{PackagingQuoteWorkspace.readInputs([{'毛利率（%）':''}],[],{},null,p)}catch(e){denied=true}if(!denied)throw Error('invented margin');")

    def test_catalog_same_sections_and_packaging_fields(self):
        import cpq_agent_server as server
        package=server.fixed_forms_catalog('packaging');other=server.fixed_forms_catalog('battery')
        self.assertEqual([f['section_id'] for f in other],[f['section_id'] for f in package])
        self.assertNotIn('s2_packaging',[f['section_id'] for f in package])
        by={f['section_id']:f for f in package}
        self.assertIn('inner_length',[c['key'] for c in by['s2_techparams']['techparams_columns']])
        self.assertIn('材料费用',[c['key'] for c in by['s3_products']['columns']])
        self.assertIn('加价项名称',[c['key'] for c in by['s3_markup']['columns']]);self.assertNotIn('毛利率（%）',[c['key'] for c in by['s3_markup']['columns']])
        self.assertIn('加价值',[c['key'] for c in by['s4_markup']['columns']])

    def test_bridge_adds_standard_techparams_and_cost_fields(self):
        from cpq_tech_bridge import packaging_snapshot
        view=packaging_snapshot({'packaging_package':PKG})
        self.assertEqual(220.5,view['s2_techparams']['数据'][0]['inner_length'])
        self.assertEqual(6,view['s2_products']['数据'][0]['材料费用'])
        self.assertEqual(1,view['s2_products']['数据'][0]['其他费用'])
        self.assertEqual(PKG,view['packaging_package'])

    def test_actual_js_projects_into_existing_fields_without_calculating(self):
        js=ROOT/'tech_app/frontend/packaging-quote-workspace.js'
        self.assertTrue(js.exists())
        node("global.window=global;"+js.read_text()+"\nconst p="+json.dumps(PKG)+";let q={industry:'packaging',pricing_mode:'gross_margin',gross_margin_rate:.25,cost_total:10,quote_quantity:1000,untaxed_unit_price:13.3333333333,net_unit_price:13.3333333333,subtotal_unit:13.3333333333,untaxed_total:13333.33,taxed_total:15066.66,tax_rate:.13,margin_price:13.3333333333,addon_total:0,discount_rate:0,source_result_version:'v1'};const s=PackagingQuoteWorkspace.project(p,q);if(s.s2_techparams[0].inner_length!==220.5)throw Error('actual dimensions missing');if(s.s3_markup.length)throw Error('legacy margin override');if(s.s5_detail[0]['总金额']!=='13333.33')throw Error('total precision');if(s.s2_products[0]['其他费用']!=='1.00')throw Error('cost fields');if(PackagingQuoteWorkspace.quoteMatches(p,q)!==true)throw Error('valid version');p.cost.total_cost=11;if(PackagingQuoteWorkspace.quoteMatches(p,q))throw Error('old cost accepted');")

    def test_page_has_no_extra_panel_and_restore_never_prices(self):
        source=(ROOT/'确认需求解析结果.html').read_text()
        self.assertNotIn("host.id = 'packagingQuotePanel'",source)
        start=source.index('    async function renderPackagingQuotePanel(')
        end=source.index('\n    }',start)+6
        function=source[start:end]
        self.assertNotIn('.renderCard(',function)
        self.assertNotIn('.price(',function)
        self.assertIn('packaging-quote-workspace.js',source)

    def test_history_versions_use_existing_drawer_not_an_extra_work_card(self):
        source=(ROOT/'确认需求解析结果.html').read_text()
        start=source.index('    function renderQuoteVersions(');end=source.index('\n    }',start)+6
        self.assertNotIn("$('resultsContent')",source[start:end],'历史版本不应额外叠在报价工作区')
        self.assertIn("$('historyDrawer')",source[start:end])

    def test_legacy_extra_sections_not_rendered(self):
        source=(ROOT/'确认需求解析结果.html').read_text()
        start=source.index('    function cardSnapshotSectionSkipped(');end=source.index('\n    }',start)+6
        node(source[start:end]+"\nfor(const id of ['packaging_parts','s2_packaging','s2_packaging_cost','s2_report']){if(!cardSnapshotSectionSkipped(id,'packaging'))throw Error(id);}if(cardSnapshotSectionSkipped('s3_products','packaging')||cardSnapshotSectionSkipped('s2_report','battery'))throw Error('unrelated hidden');")

    def test_server_save_uses_owned_cost_and_does_not_complete_step(self):
        module=importlib.import_module('cpq_quote_workspace')
        conn=MagicMock();conn.transaction.return_value.__enter__.return_value=conn
        conn.cursor.return_value.fetchall.return_value=[]
        card={'card_id':1,'industry':'packaging','current_step':3,'creator_user_id':'1','current_owner':'1','overall_status':'in_progress'}
        saved={2:{'packaging_package':PKG}}
        with patch('cpq_auth._connect',return_value=conn),patch('cpq_wf._fetch_card',return_value=card),patch('cpq_wf.step_snapshot',side_effect=lambda sid,step,**kw:saved.get(step,{})),patch('cpq_wf.merge_step_snapshot') as merge,patch('cpq_wf.complete_step') as complete:
            result=module.save_pricing('Q1',3,{'gross_margin_rate':.25}, {'user_id':1,'role_code':'sales_mgr'},expected_cost_fingerprint=module.cost_fingerprint(PKG))
            self.assertEqual(13.33,result['quote']['margin_price'])
            self.assertTrue(merge.called);complete.assert_not_called()
            self.assertEqual(PKG,merge.call_args.args[2]['packaging_package'])

    def test_save_denies_foreign_user_and_stale_cost(self):
        module=importlib.import_module('cpq_quote_workspace')
        import cpq_wf
        conn=MagicMock();conn.cursor.return_value.fetchall.return_value=[]
        card={'card_id':1,'industry':'packaging','current_step':3,'creator_user_id':'1','current_owner':'1'}
        with patch('cpq_auth._connect',return_value=conn),patch('cpq_wf._fetch_card',return_value=card),patch('cpq_wf.step_snapshot',return_value={'packaging_package':PKG}),patch('cpq_wf.merge_step_snapshot') as merge:
            with self.assertRaises(cpq_wf.WfError):module.save_pricing('Q1',3,{'gross_margin_rate':.25},{'user_id':2,'role_code':'sales_mgr'},expected_cost_fingerprint=module.cost_fingerprint(PKG))
            with self.assertRaises(cpq_wf.WfError):module.save_pricing('Q1',3,{'gross_margin_rate':.25},{'user_id':1,'role_code':'sales_mgr'},expected_cost_fingerprint='old')
            merge.assert_not_called()

    def test_read_uses_latest_received_cost_without_mutating_history_or_pricing(self):
        import copy
        module=importlib.import_module('cpq_quote_workspace')
        old={'packaging_package':copy.deepcopy(PKG),'packaging_quote':{'cost_total':10,'source_result_version':'v1'}}
        latest=copy.deepcopy(PKG);latest['cost']['total_cost']=12;latest['source']['result_version']='v2'
        conn=MagicMock()
        with patch('cpq_auth._connect',return_value=conn),patch('cpq_wf.step_snapshot',side_effect=lambda sid,step,**kw: {'packaging_package':latest} if int(step)==2 else old),patch('cpq_packaging_quote.price',side_effect=AssertionError('读取不得重算')),patch('cpq_wf.merge_step_snapshot') as write:
            view=module.read_snapshot('Q',3)
            self.assertEqual(12,view['packaging_package']['cost']['total_cost'])
            self.assertEqual(10,view['packaging_quote']['cost_total'])
            self.assertEqual(10,old['packaging_package']['cost']['total_cost'])
            self.assertEqual(module.cost_fingerprint(latest),view['packaging_cost_fingerprint'])
            write.assert_not_called()

    def test_source_fingerprint_does_not_bypass_draft_publish_gate(self):
        import cpq_packaging_quote,cpq_quote_workspace,cpq_quote_gate,copy
        package=copy.deepcopy(PKG);package['gaps']=[{'code':'price_missing'}];package['cost']['has_gaps']=True
        quote=cpq_packaging_quote.price(package,gross_margin_rate=.25)
        quote['source_cost_fingerprint']=cpq_quote_workspace.cost_fingerprint(package)
        verdict=cpq_quote_gate.quote_step_completion_gate(6,{'packaging_package':package,'packaging_quote':quote})
        self.assertFalse(verdict['ok'])
        self.assertEqual('packaging_draft_not_publishable',verdict['code'])
        quote['draft']=False;package['gaps']=[];package['cost']['has_gaps']=False;package['cost']['process_total']=4
        verdict=cpq_quote_gate.quote_step_completion_gate(3,{'packaging_package':package,'packaging_quote':quote})
        self.assertEqual('packaging_quote_stale',verdict['code'])

    def test_saving_previous_step_updates_active_draft_not_an_old_snapshot(self):
        module=importlib.import_module('cpq_quote_workspace')
        conn=MagicMock();conn.cursor.return_value.fetchall.return_value=[]
        card={'card_id':1,'industry':'packaging','current_step':5,'creator_user_id':'1','current_owner':'1','overall_status':'in_progress'}
        with patch('cpq_auth._connect',return_value=conn),patch('cpq_wf._fetch_card',return_value=card),patch('cpq_wf.step_snapshot',return_value={'packaging_package':PKG}),patch('cpq_wf.merge_step_snapshot') as merge:
            module.save_pricing('Q',3,{'gross_margin_rate':0,'addons':{'freight':0}}, {'user_id':1,'role_code':'sales_mgr'},expected_cost_fingerprint=module.cost_fingerprint(PKG))
            self.assertEqual(5,merge.call_args.args[1])
            self.assertEqual(10,merge.call_args.args[2]['packaging_quote']['margin_price'])

    def test_client_cost_and_completed_quote_mutation_rejected(self):
        module=importlib.import_module('cpq_quote_workspace')
        import cpq_wf
        with patch('cpq_wf.can_do_step',return_value=True),patch('cpq_auth._connect') as connection:
            with self.assertRaises(cpq_wf.WfError):module.save_pricing('Q',3,{'cost_total':1},{'user_id':1},expected_cost_fingerprint='x')
            connection.assert_not_called()
        conn=MagicMock();conn.cursor.return_value.fetchall.return_value=[]
        with patch('cpq_auth._connect',return_value=conn),patch('cpq_wf._fetch_card',return_value={'industry':'packaging','creator_user_id':1,'current_owner':1,'current_step':6,'overall_status':'completed'}),patch('cpq_wf.merge_step_snapshot') as merge:
            with self.assertRaises(cpq_wf.WfError):module.save_pricing('Q',3,{'gross_margin_rate':.25},{'user_id':1,'role_code':'sales_mgr'},expected_cost_fingerprint=module.cost_fingerprint(PKG))
            merge.assert_not_called()

    def test_real_chrome_existing_tables_restore_edit_and_reprice(self):
        chrome=pathlib.Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not chrome.exists():self.skipTest('Chrome unavailable')
        import cpq_agent_server,cpq_packaging_quote,cpq_quote_workspace
        source=(ROOT/'确认需求解析结果.html').read_text()
        def function(name):
            match=re.search(r'\n    (?:async )?function '+re.escape(name)+r'\(',source)
            self.assertIsNotNone(match,name)
            return source[match.start():source.index('\n    }',match.end())+6]
        forms=cpq_agent_server.fixed_forms_catalog('packaging');quote=cpq_packaging_quote.price(PKG,gross_margin_rate=.25)
        quote['source_cost_fingerprint']=cpq_quote_workspace.cost_fingerprint(PKG)
        snapshot={'packaging_package':PKG,'packaging_quote':quote,'packaging_cost_fingerprint':quote['source_cost_fingerprint']}
        context="const sections=new Map(),$=id=>document.getElementById(id),FORMS="+json.dumps(forms)+";let shownStep=3,currentSessionId='Q',busy=false,LAST_PACKAGING_PACKAGE=null,LAST_PACKAGING_QUOTE=null,PACKAGING_COMMERCIAL_QUOTE=null,PACKAGING_COST_FINGERPRINT='',PACKAGING_RESTORED_RATE=null,PACKAGING_PRICING_DIRTY=false,PACKAGING_PRICING_REQUEST=0;const GUESS_RE=/推荐/;const currentIndustry=()=> 'packaging',isBomSection=()=>false,escapeHtml=x=>String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;'),addUserBubble=()=>{},addAiBubble=()=>{},s5BindLiveCalc=()=>{},addErrorBubble=x=>{throw Error(x)},setBusy=x=>{busy=x};let calls=0;const snap="+json.dumps(snapshot)+";const wfApi=async(path,options)=>{calls++;if(path!=='/wf/card/pricing-save'||options.body.inputs.gross_margin_rate!==.3||options.body.inputs.addons.freight!==0)throw Error('wrong pricing inputs');return {ok:true,data:snap}};"
        context+="const WF={canEdit:true};let currentStep=3;"
        names=('statusChipHtml','syncSectionNumbers','sectionShell','markGuess','techParamsColumnsOf','renderTableSection','makeTableRow','updateRowCount','extractSectionRows','restorePackagingPricing','packagingQuoteDetailValues','applyPackagingQuoteRows','renderPackagingQuotePanel','repricePackagingWorkspace')
        script=(ROOT/'tech_app/frontend/packaging-quote-workspace.js').read_text()+context+'\n'.join(function(n) for n in names)
        script+="\n(async()=>{await renderPackagingQuotePanel(snap);await renderPackagingQuotePanel(snap);if(calls!==0)throw Error('restore called pricing');if(document.getElementById('packagingQuotePanel'))throw Error('extra card');if(!sections.get('s2_techparams').el.textContent.includes('220.5'))throw Error('dimensions not rendered');if(document.getElementById('packagingMarginInput'))throw Error('manual margin still visible');if(sections.get('s3_markup').el.querySelector('[data-packaging-reprice]'))throw Error('extra save button');if(sections.get('s4_markup').el.querySelector('input'))throw Error('manual addon still editable');if(calls!==0)throw Error('restore recalculated');document.getElementById('probe').textContent='PASS';})().catch(e=>{document.getElementById('probe').textContent='FAIL:'+e.stack});"
        with tempfile.TemporaryDirectory() as directory:
            page=pathlib.Path(directory)/'workspace.html'
            page.write_text('<!doctype html><meta charset="utf-8"><div id="resultsContent"></div><pre id="probe">WAIT</pre><script>'+script+'</script>')
            result=subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run','--disable-background-networking','--user-data-dir='+str(pathlib.Path(directory)/'profile'),'--virtual-time-budget=1800','--dump-dom',page.as_uri()],capture_output=True,text=True,timeout=30)
            found=re.search(r'<pre id="probe">([^<]*)</pre>',result.stdout)
            self.assertIsNotNone(found,result.stderr[-500:])
            self.assertEqual('PASS',found.group(1))

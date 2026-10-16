import copy
import unittest
import json
import subprocess
import io
import zipfile
from unittest.mock import patch, MagicMock
from pathlib import Path
from cpq_cost_contract import cost_fields
from tech_app.backend.models.process import ProcessStep
from tech_app.backend.services import packaging_layout
import cpq_packaging_quote as quote
from tests.test_packaging_quote_close_loop_red import package_fixture


class Presentation(unittest.TestCase):
    def test_shared_quote_money_rounds_decimal_ties_before_quantity(self):
        from tests.test_packaging_whole_part_process_unit_red import js_function
        path=Path(__file__).resolve().parents[1]/'确认需求解析结果.html'
        script="const currentIndustry=()=> 'battery',S5_DERIVED=['折后价格','总金额','税金'];const values={'报价':1.005,'折扣':1,'数量':1000,'税率':.13};const _s5Get=(tr,k)=>values[k],_s5Set=(tr,k,v)=>values[k]=v;"
        script+=''.join(js_function(path,name) for name in ('_numOf','_fmtNum','s5RecalcRow'))
        script+="if(_fmtNum(1.005)!=='1.01')throw Error('decimal tie');s5RecalcRow({});if(values['折后价格']!=='1.01'||values['总金额']!=='1010.00')throw Error('unit multiplied before rounding');"
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
    def test_layout_preview_is_local_not_whole_dwg(self):
        from tests.test_packaging_whole_part_process_unit_red import js_function
        path=Path(__file__).resolve().parents[1]/'tech_app/frontend/assembly-integration.js'
        script="const aiBusinessParts={layout_rows:[]},esc=v=>String(v);"
        script+=js_function(path,'aiLayoutPreviewRegion')+js_function(path,'aiLayoutCadSvg')
        script+="""const doc={cad_scene:{entities:[{kind:'line',bbox:[0,0,10,10],points:[[0,0],[10,10]],role:'cut'},
          {kind:'text',bbox:[1,1,3,3],x:1,y:2,text:'FIRST'},
          {kind:'text',bbox:[101,1,103,3],x:101,y:2,text:'SECOND'}]}};
          const first=aiLayoutCadSvg(doc,{preview_bbox:[0,0,10,10]});
          if(!first.includes('FIRST')||first.includes('SECOND'))throw Error('not local');
          if(aiLayoutCadSvg(doc,{entity_id:'unknown'})!=='')throw Error('unknown shown as full drawing');"""
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_system_rule_round_trip_save_restore_export(self):
        import cpq_agent_server as server, cpq_quote_workspace as ws
        pkg=package_fixture();pkg['cost']['total_cost']=47.735
        pkg['bom']['business_parts']=[{'business_part_code':f'BP{i:02}','name':f'部件{i}'} for i in range(1,29)]
        pkg['bom']['items']=[{'item_key':f'BP{i:02}::a','item_name':f'组成{i}','material':'灰板','quantity':1} for i in range(1,29)]
        pkg['route']['steps']=[{'part_code':'BP01','step_no':10,'step_name':'开料'},
                              {'part_code':'BP01','step_no':20,'step_name':'模切'}]
        card={'card_id':1,'industry':'packaging','current_step':3,'creator_user_id':'1','current_owner':'1','overall_status':'in_progress'}
        snapshots={1:{'s1_basic':{'数据':{'所属组织':'裕同包装'}}},2:{'packaging_package':pkg}}
        conn=MagicMock();conn.cursor.return_value.fetchall.return_value=[]
        def merge(sid,step,snapshot,**kwargs):snapshots[step]=copy.deepcopy(snapshot)
        def fill(data,emit=None):
            self.assertEqual(47.74,data['products'][0]['基础成本'])
            self.assertEqual('裕同包装',data['basic']['所属组织'])
            amount=2.345 if data['step']==3 else .675
            return {'ok':True,'product_markup':{data['products'][0]['成品编码']:amount},
                'markup':[{'加价项名称':'库内规则','加价值':str(amount)}],
                'source':{'table':'md_clm_material_price_rule'},'rules':[{'rule_expression':'测试公式'}]}
        class Request:
            def _acting_user(self):return {'user_id':1,'role_code':'sales_mgr'}
            def _handle_sse(self,fn):return fn({'sid':'Q','step':card['current_step'],'products':[{'基础成本':.01}]})
        with patch('cpq_auth._connect',return_value=conn),patch('cpq_wf.get_card',return_value=card),patch('cpq_wf._fetch_card',return_value=card),\
            patch('cpq_wf.step_snapshot',side_effect=lambda sid,step,**kw:snapshots.get(step,{})),\
            patch('cpq_wf.merge_step_snapshot',side_effect=merge),patch.object(server,'_handle_markup_fill',side_effect=fill):
            self.assertTrue(server.Handler._handle_markup_stream(Request())['ok'])
            card['current_step']=4
            result=server.Handler._handle_markup_stream(Request())
            self.assertTrue(result['ok'],result)
            q=result['data']['packaging_quote']
            self.assertEqual(50.09,q['margin_price']);self.assertEqual(50.77,q['net_unit_price'])
            self.assertEqual(50770,q['untaxed_total'])
            self.assertEqual(q['net_unit_price'],quote.recompute(q)['net_unit_price'])
            card['current_step']=5;snapshots[5]=snapshots[4]
            restored=ws.read_snapshot('Q',5)
            self.assertEqual(q,restored['packaging_quote'])
            doc=quote.document(q)
            self.assertEqual(28,len(doc['sections'][2]['rows']))
            self.assertEqual(1,len(doc['sections'][3]['rows']))
            data=server._build_quote_docx({'session_id':'Q','industry':'packaging','steps':[]})
            self.assertIsNotNone(data,'Word导出依赖必须可用')
            with zipfile.ZipFile(io.BytesIO(data)) as archive:xml=archive.read('word/document.xml').decode()
            self.assertIn('BP28',xml);self.assertIn('50770.00',xml)
            self.assertIn('开料',xml);self.assertIn('模切',xml)
            self.assertNotIn('本报价为缺口草稿',xml)
            updated=ws.save_pricing('Q',5,{'quote_quantity':2000,'tax_rate':0,'discount':{'rate':.2}},
                {'user_id':1,'role_code':'sales_mgr'},expected_cost_fingerprint=ws.cost_fingerprint(pkg))['quote']
            self.assertEqual(81240,updated['untaxed_total'])
            self.assertEqual(0,updated['tax_amount'])
            self.assertEqual(q['rule_pricing'],updated['rule_pricing'])
            with self.assertRaises(Exception):
                ws.save_pricing('Q',5,{'rule_pricing':{'profit_amount':0}},
                    {'user_id':1,'role_code':'sales_mgr'},expected_cost_fingerprint=ws.cost_fingerprint(pkg))

    def test_unauthorized_rule_request_never_calls_model_or_db_rules(self):
        import cpq_agent_server as server
        class Request:
            def _acting_user(self):return {'user_id':2,'role_code':'process_mgr'}
            def _handle_sse(self,fn):return fn({'sid':'Q','step':3})
        with patch('cpq_wf.get_card',return_value={'industry':'packaging','creator_user_id':1,'current_owner':1}),\
             patch.object(server,'_handle_markup_fill') as fill:
            result=server.Handler._handle_markup_stream(Request())
            self.assertFalse(result['ok']);fill.assert_not_called()

    def test_word_export_rejects_a_stale_cost_price_pair(self):
        import cpq_agent_server as server
        from cpq_quote_workspace import cost_fingerprint
        pkg=package_fixture();q=quote.price(pkg,gross_margin_rate=.25)
        q['source_cost_fingerprint']=cost_fingerprint(pkg)
        pkg['cost']['total_cost']=88
        with patch('cpq_wf.step_snapshot',side_effect=lambda sid,step:{'packaging_package':pkg} if step==2 else {'packaging_quote':q}):
            with self.assertRaises(ValueError):server._build_quote_docx({'session_id':'Q','industry':'packaging'})

    def test_operation_name_aliases(self):
        for key in ('step_name','operation_name','title'):
            with self.subTest(key=key):
                self.assertEqual('左盖面纸裱合',ProcessStep.model_validate({key:'左盖面纸裱合','step_no':10}).name)

    def test_handoff_rounds_unit_before_order_total(self):
        value=cost_fields({'industry':'packaging','requirement':{'quote_quantity':1000},'cost':{'total_cost':47.735}})
        self.assertEqual(47.74,value['unit_cost'])
        self.assertEqual(47740,value['order_total_cost'])
        self.assertEqual(47.735,value['raw_unit_cost'])

    def test_quote_uses_rounded_units(self):
        pkg=package_fixture();pkg['cost']['total_cost']=47.735
        priced=quote.price(pkg,markup_rate=0,pricing_mode='markup',quote_quantity=1000)
        self.assertEqual(47.74,priced['net_unit_price'])
        self.assertEqual(47740,priced['untaxed_total'])
        self.assertEqual(quote.recompute(priced)['net_unit_price'],priced['net_unit_price'])

    def test_parent_bom_and_grouped_operations(self):
        priced=quote.price(package_fixture(),gross_margin_rate=.25)
        priced['bom_items']=[{'item_key':'BP01::a','item_name':'左盖A','material':'灰板','quantity':1},
            {'item_key':'BP01::b','item_name':'左盖B','material':'面纸','quantity':1},
            {'item_key':'BP02','item_name':'底板','material':'灰板','quantity':1}]
        priced['route_steps']=[{'part_code':'BP01','step_no':10,'step_name':'裱合'},
            {'part_code':'BP01','step_no':20,'step_name':'模切'},
            {'part_code':'BP02','step_no':30,'step_name':'开料'}]
        doc=quote.document(priced)
        self.assertEqual(2,len(doc['sections'][2]['rows']))
        self.assertIn('BP01',doc['sections'][3]['rows'][0]['项目'])
        self.assertIn('裱合',doc['sections'][3]['rows'][0]['值'])
        self.assertIn('模切',doc['sections'][3]['rows'][0]['值'])

    def test_removed_specific_warning_not_audit(self):
        pkg=package_fixture();pkg['gaps']=[{'code':'loss_rate_missing'}];pkg['cost']['has_gaps']=True
        priced=quote.price(pkg,gross_margin_rate=.25)
        self.assertNotIn('本报价为缺口草稿',quote.document(priced)['markdown'])
        self.assertEqual('loss_rate_missing',priced['gaps'][0]['code'])

    def test_layout_without_mm_and_separate_sizes(self):
        rows=packaging_layout.extract_layout_rows({'texts':[{'entity_id':'a','raw_text':'250g双铜 开料860*460 上机880*480 各排2模','position':[0,0]}]})
        self.assertEqual([860,460],rows[0]['sheet_mm'])
        self.assertEqual([880,480],rows[0]['machine_mm'])

    def test_layout_order_is_visual(self):
        rows=packaging_layout.extract_layout_rows({'texts':[{'entity_id':'a','raw_text':'排2模','position':[0,0]},
            {'entity_id':'z','raw_text':'排2模','position':[0,100]}]})
        self.assertEqual('z',rows[0]['entity_id'])

    def test_layout_triple_is_not_sheet_and_independent_frames_are_kept(self):
        rows=packaging_layout.extract_layout_rows({'texts':[{'entity_id':'T','raw_text':'成品200×100×80 排2模','position':[0,0]}]})
        self.assertIsNone(rows[0]['sheet_mm'])
        frames=[{'entity_id':str(x),'kind':'polyline','closed':True,'bbox':[x,0,x+100,100],
            'attributes':{'points':[[x,0],[x+100,0],[x+100,100],[x,100]]}} for x in (0,200)]
        bound=packaging_layout.bind_layout_regions({'entities':frames},[
            {'entity_id':'A','position':[50,-5]},{'entity_id':'B','position':[250,-5]}])
        self.assertEqual([0,0,100,100],bound[0]['preview_bbox'])
        self.assertEqual([200,0,300,100],bound[1]['preview_bbox'])

    def test_rule_catalog_not_margin_override(self):
        import cpq_quote_workspace as ws
        entry={'section_id':'s3_markup','columns':[{'key':'加价项名称','label':'加价项名称'}]}
        self.assertEqual(entry['columns'],ws.catalog_entry(entry,'packaging')['columns'])

    def test_existing_layout_estimate_can_recover_saved_cad_text_not_manual(self):
        row={'entity_id':'T','kind':'layout_instruction','raw_text':'纸张860*460 各排2模',
             'sheet_mm':[999,999],'size_source':'demo_horizontal_sheet_estimate'}
        self.assertEqual([860,460],packaging_layout.refresh_text_sizes(row)['sheet_mm'])
        row['size_source']='user_verified_cut_sheet'
        self.assertEqual({},packaging_layout.refresh_text_sizes(row))


if __name__=='__main__':unittest.main()

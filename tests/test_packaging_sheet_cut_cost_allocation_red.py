import unittest
import json
from tech_app.backend.services import packaging_layout as layout

class SheetCutTests(unittest.TestCase):
    def test_machine_sheet_is_not_used_as_cut_sheet(self):
        rows=layout.extract_layout_rows({'texts':[{'entity_id':'m','raw_text':'上机尺寸 600*400mm 排2模','position':[0,0]}]})
        self.assertEqual([600,400],rows[0]['machine_mm'])
        self.assertIsNone(rows[0]['sheet_mm'])

    def test_explicit_cut_dimension_labels_can_supply_size_without_guessing(self):
        ir={'dimensions':[
            {'entity_id':'h','raw_text':'开料长','dim_type':'linear','measured_value':200,'target_entity_ids':['point:0,0','point:200,0']},
            {'entity_id':'v','raw_text':'开料宽','dim_type':'linear','measured_value':100,'target_entity_ids':['point:0,0','point:0,100']}]}
        row=layout.attach_cut_dimension_candidates(ir,[{'entity_id':'label','position':[0,0]}])[0]
        self.assertEqual([200,100],row['sheet_mm'])
        self.assertEqual('cad_cut_sheet_dimension',row['size_source'])

    def test_incomplete_association_and_mixed_material_do_not_charge_partial_sheet(self):
        from tech_app.backend.services import packaging_sheet_cost as s,packaging_cost as cost
        parts=[{'part_code':c,'length_mm':100,'width_mm':100,'quantity':1,'material_code':c} for c in ('a','b')]
        lines=[{'cost_category':'material','part_code':c,'amount':10,'inputs':{'material_code':c}} for c in ('a','b')]
        row={'entity_id':'sheet','sheet_mm':[600,400],'part_codes':['a','b'],'part_counts':{'a':2,'b':2}}
        gaps=s.apply_layout_costs([row],parts,lines,100,cost.compute_line,[])
        self.assertEqual('layout_mixed_material_or_rate',gaps[0]['code'])
        self.assertTrue(all(line['amount'] is None for line in lines))

    def test_real_formula_sheet_setup_is_once_and_no_second_usage_multiplier(self):
        from tech_app.backend.services import packaging_sheet_cost as s, packaging_cost as cost
        parts=[{'part_code':'a','length_mm':100,'width_mm':100,'quantity':2,'material_code':'paper'},
               {'part_code':'b','length_mm':200,'width_mm':100,'quantity':1,'material_code':'paper'}]
        variables={'gsm':200,'ton_price':5000,'tax_factor':1.13,'proof_base':10,'price_unit_status':'ok','material_code':'paper'}
        lines=[{'cost_category':'material','part_code':p['part_code'],'inputs':dict(variables),
                'loss_rate':0,'amount':999} for p in parts]
        group={'entity_id':'sheet','sheet_mm':[600,400],'part_codes':['a','b'],'part_counts':{'a':4,'b':2}}
        self.assertEqual([],s.apply_layout_costs([group],parts,lines,1000,cost.compute_line,[]))
        evaluated=cost.compute_line('material',dict(variables,cut_length=600,cut_width=400,
             imposition_count=1,quote_quantity=500),rows=[])
        expected=evaluated['amount']*500/1000
        self.assertAlmostEqual(expected,sum(line['amount'] for line in lines))
        self.assertAlmostEqual(expected/2,lines[0]['amount'])
        self.assertEqual(500,lines[0]['inputs']['sheet_quantity'])

    def test_missing_counts_blocks_instead_of_individual_fallback(self):
        from tech_app.backend.services import packaging_sheet_cost as s, packaging_cost as cost
        lines=[{'cost_category':'material','part_code':'a','amount':123,'inputs':{}}]
        gaps=s.apply_layout_costs([{'entity_id':'sheet','sheet_mm':[600,400],'part_codes':['a']}],
            [{'part_code':'a','quantity':1,'length_mm':100,'width_mm':100}],lines,100,cost.compute_line,[])
        self.assertIsNone(lines[0]['amount'])
        self.assertEqual('layout_part_count_missing',gaps[0]['code'])

    def test_user_cut_inputs_preserve_source_and_require_all_counts(self):
        doc={'layout_rows':[{'entity_id':'l','part_codes':['a','b'],'kind':'drawing_layout_candidate'}]}
        with self.assertRaisesRegex(ValueError,'layout_part_count_missing'):
            layout.set_cut_inputs(doc,'l',{'sheet_mm':[600,400],'part_counts':{'a':2}},'PE1')
        updated=layout.set_cut_inputs(doc,'l',{'sheet_mm':[600,400],'part_counts':{'a':2,'b':1}},'PE1')
        self.assertEqual('cut_sheet',updated['layout_rows'][0]['dimension_role'])
        self.assertEqual('user_verified_cut_sheet',updated['layout_rows'][0]['size_source'])
        self.assertEqual('drawing_layout_candidate',doc['layout_rows'][0]['kind'])

    def test_svg_has_original_dimension_text_not_fake_sheet_boundary(self):
        ir={'dimensions':[{'entity_id':'d','display_entities':[
            {'kind':'text','text':'200','x':100,'y':10,'height':5,'bbox':[90,5,110,15]}]}]}
        svg=layout.cut_dimension_svg(ir,{'entity_ids':['d'],'witness_points':[[0,0],[200,0],[0,100]]})
        self.assertIn('>200</text>',svg)
        self.assertNotIn('<rect',svg)

    def test_shared_sheet_allocation_is_once_and_conserves_total(self):
        from tech_app.backend.services import packaging_sheet_cost as s
        parts=[{'part_code':'a','length_mm':100,'width_mm':100,'quantity':2},
               {'part_code':'b','length_mm':200,'width_mm':100,'quantity':1}]
        row={'entity_id':'sheet','sheet_mm':[600,400],'part_codes':['a','b'],
             'part_counts':{'a':4,'b':2},'n_up':None}
        got=s.sheet_allocation(row,parts,1000,10)
        self.assertEqual(500,got['sheet_quantity'])
        self.assertAlmostEqual(5,got['total_per_quote_unit'])
        self.assertAlmostEqual(5,sum(got['allocations'].values()))
        self.assertAlmostEqual(2.5,got['allocations']['a'])

    def test_multiple_parts_without_counts_cannot_use_one_generic_n_up(self):
        from tech_app.backend.services import packaging_sheet_cost as s
        with self.assertRaisesRegex(ValueError,'layout_part_count_missing'):
            s.sheet_allocation({'sheet_mm':[600,400],'part_codes':['a','b'],'n_up':2},
               [{'part_code':c,'length_mm':100,'width_mm':100,'quantity':1} for c in ['a','b']],100,1)

    def test_no_layout_margin_is_explicit_and_configurable(self):
        from tech_app.backend.services import packaging_sheet_cost as s
        got=s.individual_cut({'length_mm':100,'width_mm':80,'material_code':'paper'},
             {'cut_allowances':{'paper':{'length_mm':4,'width_mm':6}}})
        self.assertEqual([104,86],got['sheet_mm'])
        self.assertEqual('configured_allowance',got['size_source'])

    def test_nearby_dimension_is_not_automatically_paper_size(self):
        rows=layout.attach_cut_dimension_candidates({'dimensions':[
             {'entity_id':'h','dim_type':'linear','measured_value':200,'target_entity_ids':['point:0,0','point:200,0']},
             {'entity_id':'v','dim_type':'linear','measured_value':100,'target_entity_ids':['point:0,0','point:0,100']} ]},
             [{'entity_id':'label','position':[0,0],'kind':'drawing_layout_candidate','sheet_mm':None}])
        self.assertIsNone(rows[0]['sheet_mm'])
        self.assertTrue(rows[0]['cut_dimension_candidates'])
        self.assertEqual('unclassified',rows[0]['cut_dimension_candidates'][0]['dimension_role'])


from tests.test_packaging_cost_rule_routing_red import RoutingCase,PID,REQ_NO


class CostEngineSheetIntegration(RoutingCase):
    def test_individual_cut_does_not_borrow_project_imposition_count(self):
        from unittest.mock import patch
        from tech_app.backend.services import packaging_cost as cost
        self.prepare(imposition_count=5)
        with patch.object(cost,'_resolve_material',return_value={'material_code':'paper','gsm':225}), \
             patch.object(cost,'_material_price',return_value={'price':5,'unit':'kg'}), \
             patch.object(cost,'_material_gsm_detail',return_value=(225,'fixture')):
            result=cost.compute_project(PID,REQ_NO)
        item=next(i for i in result['items'] if i['cost_category']=='material' and i.get('amount') is not None)
        self.assertEqual(1,json.loads(item['inputs_json'])['imposition_count'])

    def test_project_engine_replaces_individual_material_with_sheet_allocation(self):
        from unittest.mock import patch
        from tech_app.backend.services import packaging_cost as cost, packaging_parts
        from tech_app.backend.storage import da_repo
        self.prepare()
        from contextlib import ExitStack
        stack=ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(cost,'_resolve_material',return_value={'material_code':'paper','gsm':225}))
        stack.enter_context(patch.object(cost,'_material_price',return_value={'price':5,'unit':'kg'}))
        stack.enter_context(patch.object(cost,'_material_gsm_detail',return_value=(225,'fixture')))
        baseline=cost.compute_project(PID,REQ_NO)
        self.assertTrue(any(i['cost_category']=='material' and i.get('amount') is not None for i in baseline['items']),
                        str([(i['cost_category'],i.get('amount'),i.get('inputs_json')) for i in baseline['items'] if i['cost_category']=='material']))
        item=next(i for i in baseline['items'] if i['cost_category']=='material' and i.get('amount') is not None)
        code=item['part_code']
        row=next(r for r in da_repo.load_packaging_bom(PID,REQ_NO) if r.get('part_code')==code)
        sheet=[row['length_mm']*2+10,row['width_mm']+20]
        doc={'business_parts':[{'business_part_code':code,'name':row['item_name']}],
             'layout_rows':[{'entity_id':'sheet','sheet_mm':sheet,'part_codes':[code],
                            'part_counts':{code:2},'kind':'layout_instruction'}]}
        with patch.object(packaging_parts,'load_business_parts',return_value=doc):
            result=cost.compute_project(PID,REQ_NO)
        changed=next(i for i in result['items'] if i['cost_category']=='material' and i['part_code']==code)
        trace=json.loads(changed['inputs_json'])
        self.assertEqual('sheet',trace['layout_id'])
        self.assertEqual(sheet,[trace['cut_length'],trace['cut_width']])
        self.assertAlmostEqual(trace['sheet_total_per_quote_unit'],changed['amount'])
        self.assertIn('sheet_cost_rule_version',result['source_versions'])

"""父件工序必须读到组成材料损耗；不依赖酒盒编号/线上库。"""
import copy
import json
import pathlib
import subprocess
import unittest
from unittest.mock import patch

from tests.test_packaging_cost_engine_red import CostCase, PID, REQ_NO
from tech_app.backend.services import packaging_cost as cost, packaging_demo_inputs as demo


class MaterialInference(unittest.TestCase):
    def test_legacy_encoded_identity_and_explicit_identity(self):
        self.assertEqual(('ANY', 'section:1'), cost._material_loss_identity({'part_code': 'ANY::section:1'}))
        self.assertEqual(('ANY2', 's'), cost._material_loss_identity({'part_code': 'opaque',
                         'size_source_json': '{"parent_part_code":"ANY2","section_id":"s"}'}))

    def test_existing_part_alias_is_used_for_material_inference(self):
        self.assertEqual('衬纸', demo.infer_material_text('内皮壳忖纸'))
        self.assertEqual('衬纸', demo.infer_material_text('新产品内围忖纸'))

    def test_explicit_material_is_never_replaced(self):
        value = demo.material_text_of({'name': '内围忖纸', 'reference': {'material_text': 'EVA'}})
        self.assertEqual('EVA', value['material_text'])
        self.assertEqual('drawing_text', value['source'])


class ParentLossCompute(CostCase):
    def run_case(self, rates=(.06, .06), *, step_part='NEW-PART', section=None,
                 override=None, factors=(), explicit_parent=False, unknown_demo=False):
        self.prepare(**({'loss_rate': override} if override is not None else {}))
        materials = []
        rows = []
        for i, rate in enumerate(rates):
            code = 'MATERIAL-%s' % i
            materials.append({'material_code': code, 'name': '织物%s' % i,
                              'grade': '200g', 'standard_loss_rate': rate})
            rows.append({'bom_category': 'box_part', 'part_code': ('opaque-%s' % i if explicit_parent else 'NEW-PART::section:%s' % i),
                         'item_name': '组成%s' % i, 'material_code': code, 'material': '织物%s' % i,
                         'quantity': 1, 'length_mm': 200, 'width_mm': 100,
                         'size_source_json': json.dumps({'parent_part_code': 'NEW-PART', 'section_id': 'section:%s' % i})})
        step = {'step_no': 10, 'step_name': '模切', 'formula_code': 'PKG-C-DIE-CUT',
                'part_code': step_part, 'section_id': section, 'usage_qty': 1,
                'cost_parameters': {'machine_length': 200, 'machine_width': 100, 'imposition_count': 1}}
        doc = {}
        if unknown_demo:
            self.save_requirement(poc_demo=True)
            rows = [dict(rows[0], part_code='NEW-PART', item_name='任意内围忖纸', material_code='',
                         material='未识别材料（估算）')]
            materials = [{'material_code': 'LINER', 'name': '衬纸', 'grade': '80g', 'standard_loss_rate': .05}]
            doc = {'poc_demo': True, 'business_parts': [{'business_part_code': 'NEW-PART',
                   'poc_inputs': {'mock': True, 'gsm': 200, 'ton_price': 12000}}]}
        from tech_app.backend.services import packaging_parts, packaging_assembly
        with patch.object(cost.da_repo, 'load_packaging_bom', return_value=rows), \
             patch.object(cost.da_repo, 'load_packaging_route_steps', return_value=[step]), \
             patch.object(cost, '_material_rows', return_value=materials), \
             patch.object(cost, '_material_price', return_value={'price': 8, 'unit': 'kg'}), \
             patch.object(cost.kb_repo, '_table', wraps=cost.kb_repo._table) as table, \
             patch.object(packaging_parts, 'load_business_parts', return_value=doc), \
             patch.object(packaging_assembly, 'route_drift', return_value=None):
            original = table._mock_wraps
            table.side_effect = lambda name: list(factors) if name == 'kb_cost_factor' else original(name)
            result = cost.compute_project(PID, REQ_NO)
            cost.da_repo.save_packaging_cost(PID, REQ_NO, result['scenario_code'], result, result['items'])
            replay = cost.load_cost(PID, REQ_NO)
        line = next(r for r in result['items'] if r['cost_category'] == 'die_cutting')
        return result, line, replay

    def test_parent_inherits_all_sections_and_saves_evidence(self):
        result, line, replay = self.run_case()
        self.assertEqual(.06, line['loss_rate'])
        self.assertFalse(any(g['code'] == 'loss_rate_missing' for g in result['gaps']))
        inputs = json.loads(line['inputs_json'])
        self.assertEqual('parent_material_consensus', inputs['loss_rate_source'])
        self.assertEqual(['NEW-PART::section:0', 'NEW-PART::section:1'], inputs['loss_material_part_codes'])
        self.assertAlmostEqual(line['amount'] * 1.06, line['amount_with_loss'])
        self.assertEqual(result['total_cost'], replay['total_cost'])
        replay_line = next(r for r in replay['items'] if r['cost_category'] == 'die_cutting')
        self.assertEqual(line['amount_with_loss'], replay_line['amount_with_loss'])
        self.assertFalse(replay['stale'], replay['stale_reasons'])

    def test_explicit_bom_parent_without_encoded_id(self):
        _, line, _ = self.run_case(explicit_parent=True)
        self.assertEqual(.06, line['loss_rate'])

    def test_specific_section_uses_only_its_material(self):
        _, line, _ = self.run_case(rates=(.06, .08), section='section:1')
        self.assertEqual(.08, line['loss_rate'])

    def test_mixed_and_missing_material_rates_do_not_get_arbitrary_rate(self):
        for rates in ((.06, .08), (.06, None)):
            with self.subTest(rates=rates):
                result, line, _ = self.run_case(rates=rates)
                self.assertIsNone(line['loss_rate'])
                self.assertTrue(any(g['code'] == 'loss_rate_missing' for g in result['gaps']))

    def test_similar_parent_prefix_cannot_leak(self):
        _, line, _ = self.run_case(step_part='NEW-PART2')
        self.assertIsNone(line['loss_rate'])

    def test_explicit_requirement_zero_has_priority(self):
        _, line, _ = self.run_case(rates=(.06, .08), override=0)
        self.assertEqual(0, line['loss_rate'])
        self.assertEqual('requirement.loss_rate', json.loads(line['inputs_json'])['loss_rate_source'])

    def test_process_factor_is_scoped_and_not_added_to_material_loss(self):
        factor = {'factor_code': 'F-OP', 'factor_type': 'scrap', 'industry': 'packaging',
                  'applicable_scope': 'process_formula:PKG-D-DIE-CUT', 'value': .02,
                  'effective_from': '2020-01-01'}
        _, line, _ = self.run_case(factors=[factor])
        self.assertEqual(.02, line['loss_rate'])
        self.assertEqual('kb_cost_factor:F-OP', json.loads(line['inputs_json'])['loss_rate_source'])
        self.assertAlmostEqual(line['amount'] * 1.02, line['amount_with_loss'])
        for change in ({'industry': 'battery'}, {'effective_from': '2099-01-01'},
                       {'effective_to': '2020-01-02'}, {'applicable_scope': None},
                       {'applicable_scope': 'process_formula:OTHER'}):
            with self.subTest(change=change):
                _, line, _ = self.run_case(factors=[dict(factor, **change)])
                self.assertEqual(.06, line['loss_rate'])
        _, line, _ = self.run_case(factors=[dict(factor, value=0)])
        self.assertEqual(0, line['loss_rate'])

    def test_only_latest_effective_factor_is_used(self):
        common = {'factor_type': 'scrap', 'industry': 'packaging',
                  'applicable_scope': 'process_formula:PKG-D-DIE-CUT'}
        rows = [dict(common, factor_code='OLD', value=.03, effective_from='2020-01-01'),
                dict(common, factor_code='NEW', value=.01, effective_from='2025-01-01')]
        _, line, _ = self.run_case(factors=rows)
        self.assertEqual(.01, line['loss_rate'])
        _, line, _ = self.run_case(factors=rows + [dict(common, factor_code='BAD', value='NaN', effective_from='2026-01-01')])
        self.assertIsNone(line['loss_rate'])

    def test_old_unknown_demo_material_can_be_inferred_without_mutating_documents(self):
        result, line, _ = self.run_case(unknown_demo=True)
        self.assertEqual(.05, line['loss_rate'])
        material = next(r for r in result['items'] if r['cost_category'] == 'material')
        inputs = json.loads(material['inputs_json'])
        self.assertEqual('inferred_from_part_name', inputs['material_resolution_source'])
        self.assertEqual('未识别材料（估算）', inputs['original_material_text'])
        self.assertEqual(200, inputs['gsm'], '不偷偷改写已有演示克重')
        self.assertFalse(any(g['code'] == 'loss_rate_missing' for g in result['gaps']))

    def test_old_cost_is_marked_stale_without_rewriting_money(self):
        from tech_app.backend.services import packaging_assembly
        old = {'built': True, 'total_cost': 12, 'source_versions': {
            'expense_policy': cost.EXPENSE_POLICY, 'calculation_profile': cost.CURRENT_PROFILE,
            'assembly_time_policy': packaging_assembly.TIME_POLICY}}
        before = copy.deepcopy(old)
        view = cost.with_current_expense_policy(old)
        self.assertIn('loss_resolution_policy_changed', view.get('stale_reasons', []))
        self.assertEqual(12, view['total_cost'])
        self.assertEqual(before, old)


class SharedSheetAndPresentation(unittest.TestCase):
    def test_repeated_process_names_have_their_own_gap_amounts(self):
        items = [{'part_code': code, 'part_name': '模切', 'source_ref': 'route_step:' + str(i),
                  'cost_category': 'die_cutting', 'amount': amount}
                 for i, (code, amount) in enumerate((('A', .1), ('B', .2), ('C', .3)))]
        for item in items:
            gap = {'code': 'loss_rate_missing', 'where': '模切',
                   'part_code': item['part_code'], 'source_ref': item['source_ref']}
            self.assertEqual(item['amount'], cost.gap_evidence({'items': items}, gap)['affected_amount'])
        missing = {'code': 'loss_rate_missing', 'where': '模切', 'part_code': 'MISSING', 'source_ref': 'route_step:999'}
        self.assertIsNone(cost.gap_evidence({'items': items}, missing)['affected_amount'])

    def test_shared_sheet_preserves_each_parts_own_loss_evidence(self):
        from tech_app.backend.services import packaging_sheet_cost as sheets
        parts = [{'part_code': p, 'material_code': 'M', 'quantity': 1, 'length_mm': 100, 'width_mm': 100}
                 for p in ('A', 'B')]
        lines = [{'part_code': p, 'cost_category': 'die_cutting', 'formula_code': 'PKG-D-DIE-CUT',
                  'expression': 'die_cut_unit_fee/imposition_count', 'amount': .15, 'loss_rate': .06,
                  'inputs': {'die_cut_unit_fee': .15, 'imposition_count': 1,
                             'loss_rate_source': 'parent_material_consensus', 'loss_material_part_codes': [p + '::s']}}
                 for p in ('A', 'B')]
        def evaluate(category, variables, **kw):
            # 模拟计算器仅返回公式变量，不替业务层保存非公式追溯字段。
            variables = {k: v for k, v in variables.items() if not k.startswith('loss_')}
            return dict(amount=.15, expression='die_cut_unit_fee/imposition_count', inputs=variables)
        gaps = sheets.apply_layout_process_costs([{'entity_id': 'LAY', 'part_codes': ['A', 'B'],
                 'part_counts': {'A': 1, 'B': 1}, 'sheet_mm': [200, 100]}], parts, lines, 1000, evaluate, [])
        self.assertEqual([], gaps)
        for line in lines:
            self.assertEqual([line['part_code'] + '::s'], line['inputs']['loss_material_part_codes'])
            self.assertEqual('parent_material_consensus', line['inputs']['loss_rate_source'])

    def test_loss_provenance_is_displayed_in_chinese(self):
        from tests.test_packaging_whole_part_process_unit_red import js_function
        root = pathlib.Path(__file__).resolve().parents[1]
        script = "const esc=String;const crMoney=String;const crData={};" + js_function(root / 'tech_app/frontend/cost-review.js', 'crCostDetailHtml')
        script += """
const html=crCostDetailHtml({has_cost:true,quantity:1,calculation_details:[{category:'material',inputs:{
loss_rate_source:'parent_material_consensus',loss_material_part_codes:['P::s'],
material_resolution_source:'inferred_from_part_name',original_material_text:'未识别材料（估算）',material_text:'衬纸'}}]});
for(const word of ['未命名参数','parent_material_consensus','inferred_from_part_name'])if(html.includes(word))throw Error(word);
for(const word of ['损耗依据材料编号','按零件名称推定','原始材料文字'])if(!html.includes(word))throw Error(word);
"""
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

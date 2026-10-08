"""曲线完整性、需求事实与零件尺寸显示的行为回归；不连接生产库。"""
import json
import math
import io
import pathlib
import subprocess
import unittest
from unittest.mock import patch

from tech_app.backend.services import packaging_parts, integration
from tech_app.backend.models.integration import IntegrationPlan, IntegrationParamPlan, IntegrationParam

ROOT = pathlib.Path(__file__).resolve().parents[1]


class RoundGeometryFactsTest(unittest.TestCase):
    def test_partial_json_marker_is_hidden(self):
        import cpq_agent_server as server
        self.assertEqual('需求齐全\n', server._step1_visible_text('需求齐全\n===JSON==\n{"a":1}'))

    def test_conflicting_paper_weights_are_not_auto_selected(self):
        from tech_app.backend.services.requirement_service import extract_explicit_packaging_quote_fields
        result = extract_explicit_packaging_quote_fields('面纸225g；面纸250g；225g面纸')
        self.assertNotIn('face_paper_gsm', result)

    def test_part_preview_preserves_round_and_bulged_segments(self):
        circle = {'type': 'CIRCLE', 'entity_id': 'c', 'attributes': {
            'center': [0, 0], 'radius': 10}}
        arc = {'type': 'ARC', 'entity_id': 'a', 'attributes': {
            'center': [30, 0], 'radius': 10, 'start_angle': 0, 'end_angle': 180}}
        segments = packaging_parts._component_segments([circle, arc])['segments']
        self.assertEqual(2, len(segments), '圆和圆弧不能在零件预览中消失')
        self.assertGreater(max(p[1] for p in segments[0]), 9.9)
        poly = {'type': 'LWPOLYLINE', 'attributes': {'points': [[0, 0], [20, 0]],
            'sampled_points': [[0, 0], [10, 10], [20, 0]]}}
        self.assertEqual(10, packaging_parts._segment_of(poly)[1][1])

    def test_automatic_fill_excludes_model_guesses_even_high_confidence(self):
        src = (ROOT / 'tech_app/frontend/assembly-integration.js').read_text()
        self.assertTrue('function aiAutoApplicableFills(' in src, '自动补全缺少建议隔离')
        start = src.index('function aiAutoApplicableFills(')
        end = src.index('\n}', start) + 2
        script = src[start:end] + '\nconsole.log(JSON.stringify(aiAutoApplicableFills([' \
            + '{code:"inner_length",value:"260",confidence:1,source:"model_suggestion"},' \
            + '{code:"quote_quantity",value:"1000",source:"saved_requirement"}])));'
        out = subprocess.run(['node', '-e', script], capture_output=True, text=True, check=True)
        self.assertEqual(['quote_quantity'], [row['code'] for row in json.loads(out.stdout)])

    def test_real_dxf_round_curves_preserve_shape_and_open_state(self):
        import ezdxf
        from tech_app.backend.services.cad_ir.parser import parse_dxf
        drawing = ezdxf.new()
        drawing.units = 4
        ms = drawing.modelspace()
        ms.add_arc((0, 0), 61, 0, 180)
        ms.add_ellipse((200, 0), major_axis=(50, 0), ratio=.5,
                       start_param=0, end_param=math.pi)
        ms.add_lwpolyline([(400, 0, 1), (500, 0, 1)], format='xyb', close=True)
        stream = io.StringIO()
        drawing.write(stream)
        ir = parse_dxf(stream.getvalue().encode())
        types = {row['type']: row for row in ir['entities']}
        self.assertFalse(types['ARC']['closed'], '半圆弧不是闭合件')
        self.assertFalse(types['ELLIPSE']['closed'], '半椭圆不是完整椭圆')
        polyline = types['LWPOLYLINE']
        self.assertGreater(polyline['bbox'][3] - polyline['bbox'][1], 99.9)
        self.assertGreater(len(polyline['attributes']['sampled_points']), 10)

    def test_step6_uses_saved_quote_not_model_tax_labels(self):
        import cpq_agent_server as server
        self.assertTrue(hasattr(server, 'packaging_step6_document'), '第6步缺少确定性包装文档')
        quote = {'draft': True, 'gap_count': 3, 'untaxed_total': 5917.664,
                 'taxed_total': 6686.96032, 'net_unit_price': 5.917664,
                 'quote_quantity': 1000, 'tax_rate': 0.13}
        with patch('cpq_wf.step_snapshot', side_effect=lambda sid, step: {
                2: {'packaging_package': {'industry': 'packaging'}},
                5: {'packaging_quote': quote}}.get(step, {})):
            result = server.packaging_step6_document('test')
        self.assertIn('草稿已生成，尚不可正式导出', result['message'])
        self.assertIn('未税总额', result['ui']['markdown'])
        self.assertNotIn('整个报价流程到此结束', result['message'])

    def test_explicit_packaging_synonyms(self):
        from tech_app.backend.services.requirement_service import extract_explicit_packaging_quote_fields
        result = extract_explicit_packaging_quote_fields('225g面纸；双开门/对开')
        self.assertEqual(225, result.get('face_paper_gsm'))
        self.assertEqual('双开门/对开', result.get('closure_type'))

    def test_pricing_projection_does_not_multiply_rounded_display_unit(self):
        src = (ROOT / '确认需求解析结果.html').read_text()
        self.assertTrue('function packagingQuoteDetailValues(' in src, '缺少确定性定价投影')
        start = src.index('function packagingQuoteDetailValues(')
        end = src.index('\n    function ', start + 10)
        script = src[start:end] + '\nconsole.log(JSON.stringify(packagingQuoteDetailValues({' \
            + 'net_unit_price:5.917664,untaxed_unit_price:5.917664,quote_quantity:1000,' \
            + 'untaxed_total:5917.664,taxed_total:6686.96032,tax_rate:0.13})));'
        out = subprocess.run(['node', '-e', script], text=True, capture_output=True, check=True)
        row = json.loads(out.stdout)
        self.assertEqual('5.917664', row['折后价格'])
        self.assertEqual('5917.66', row['总金额'])
        self.assertEqual('769.30', row['税金'])

    def test_model_cannot_replace_saved_packaging_facts(self):
        requirement = {'data': {'industry': 'packaging', 'packaging_product_name': '圆盒',
                               'quote_quantity': 1000, 'inner_length': 220.5}}
        reply = IntegrationParamPlan(params=[IntegrationParam(name='内长',
            param_code='inner_length', value='260', source='经验推荐')])
        with patch.object(integration.store, 'load_requirement', return_value=requirement), \
             patch.object(integration.claude_client, 'run', return_value=reply), \
             patch.object(integration, 'drawing_blocks', return_value=[]):
            result = integration.recommend_params('test', None, IntegrationPlan())
        values = {p.param_code: p for p in result.params}
        self.assertEqual('220.5', values['inner_length'].value)
        self.assertEqual('需求单', values['inner_length'].source)
        self.assertEqual('1000', values['quote_quantity'].value)

    def test_semicircle_keeps_apex_not_chord(self):
        chains, closed, _ = packaging_parts._entity_chains({
            'type': 'ARC', 'attributes': {'center': [10, 20], 'radius': 50,
                                         'start_angle': 0, 'end_angle': 180}})
        self.assertGreater(len(chains[0]), 2)
        self.assertAlmostEqual(70, max(p[1] for p in chains[0]), places=4)
        self.assertFalse(closed)

    def test_wrap_arc_keeps_zero_degree_extreme(self):
        chains, _, _ = packaging_parts._entity_chains({
            'type': 'ARC', 'attributes': {'center': [0, 0], 'radius': 73,
                                         'start_angle': 315, 'end_angle': 45}})
        self.assertAlmostEqual(73, max(p[0] for p in chains[0]), places=4)

    def test_ellipse_is_not_dropped(self):
        ring = [[40 * math.cos(i * math.pi / 16), 20 * math.sin(i * math.pi / 16)]
                for i in range(33)]
        chains, closed, _ = packaging_parts._entity_chains({
            'type': 'ELLIPSE', 'closed': True, 'attributes': {'sampled_points': ring}})
        self.assertTrue(closed)
        self.assertEqual(33, len(chains[0]))

    def test_nested_requirement_facts_and_zero_are_not_lost(self):
        brief = integration._requirement_brief({'title': '需求编号', 'data': {
            'packaging_product_name': '圆盒', 'quote_quantity': 1000,
            'inner_length': 220.5, 'inner_width': 90, 'inner_height': 90,
            'face_paper_gsm': 225, 'closure_type': '双开门/对开', 'v_groove': False}})
        for value in ('圆盒', '1000', '220.5', '225', '双开门/对开', 'False'):
            self.assertIn(value, brief)

    def test_list_size_is_fixed_precision_with_binding_fallback(self):
        src = (ROOT / 'tech_app/frontend/app.js').read_text()
        start = src.index('function packagingBusinessPartSizeText(')
        end = src.index('\nfunction ', start + 10)
        function = src[start:end]
        script = function + '\nconsole.log(JSON.stringify([' \
            + 'packagingBusinessPartSizeText({confirmed_size:{length_mm:307.07,width_mm:528.89}}),' \
            + 'packagingBusinessPartSizeText({geometry_binding:{length_mm:100,width_mm:80}}),' \
            + 'packagingBusinessPartSizeText({})]));'
        result = subprocess.run(['node', '-e', script], text=True, capture_output=True, check=True)
        self.assertEqual(['307.07 × 528.89 mm', '100.00 × 80.00 mm', '— × — mm'],
                         json.loads(result.stdout))


if __name__ == '__main__':
    unittest.main()

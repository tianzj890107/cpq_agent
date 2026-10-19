import copy
import hashlib
import io
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

from pypdf import PdfReader
from tech_app.backend.services import requirement_pdf

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_SHA = '91081be68614406f62de0fb18ccc35303da91ea66e6b05c0484f2ac489dac7dc'


def requirement():
    return {'project_id': 'ROUND', 'requirement_no': 'REQ-ROUND', 'title': '独立圆盘盒项目',
            'status': 'pending_confirmation', 'updated_at': '2026-10-19 09:00:00',
            'data': {'industry': 'packaging', 'packaging_product_name': '圆盘盒', 'project_name': '独立圆盘盒项目',
                     'description': '按客户实际订单生产', 'inner_length': 408, 'inner_width': 408,
                     'inner_height': 50.5, 'face_paper_gsm': 157, 'quote_quantity': 1000,
                     'magnetic': False, 'min_freight': 0,
                     'field_sources': {'inner_length': 'manual', 'face_paper_gsm': 'attachment'}}}


def text(pdf):
    return '\n'.join(page.extract_text() or '' for page in PdfReader(io.BytesIO(pdf)).pages)


class LiveRequirementPdf(unittest.TestCase):
    def build(self, req=None, **kwargs):
        return requirement_pdf.build_packaging_snapshot(req or requirement(), {}, **kwargs)

    def test_packaging_default_returns_fixed_pdf_without_dynamic_reads(self):
        from tech_app.backend import main
        req = requirement(); req['title'] = '茅台当前实际订单'
        with patch.object(main, '_workflow_project', return_value={}), \
             patch.object(main.store, 'load_requirement', return_value=req), \
             patch('tech_app.backend.services.packaging_requirement_document.load_snapshot', side_effect=AssertionError('动态读取不应启用')), \
             patch.object(requirement_pdf, 'render_packaging_snapshot', side_effect=AssertionError('动态渲染不应启用')), \
             patch.object(main.store, 'load_meta', return_value={}):
            response = main.get_requirement_pdf('ROUND', user={'role': 'engineer'})
        self.assertEqual(ORIGINAL_SHA, hashlib.sha256(response.body).hexdigest())

    def test_packaging_service_entry_also_uses_fixed_pdf(self):
        with patch.object(requirement_pdf, 'render_packaging_snapshot', side_effect=AssertionError('动态渲染不应启用')):
            pdf = requirement_pdf.build_requirement_pdf(requirement())
        self.assertEqual(ORIGINAL_SHA, hashlib.sha256(pdf).hexdigest())

    def test_fixed_mode_metadata_and_missing_asset_do_not_fall_back(self):
        from fastapi import HTTPException
        from tech_app.backend import main
        with patch.object(main, '_workflow_project', return_value={}), \
             patch.object(main.store, 'load_requirement', return_value=requirement()), \
             patch.object(main.store, 'load_meta', return_value={}):
            self.assertEqual('reference', main.get_requirement('ROUND')['requirement_pdf']['mode'])
            with patch.object(requirement_pdf, 'fixed_packaging_requirement_pdf', side_effect=FileNotFoundError), \
                 patch.object(requirement_pdf, 'render_packaging_snapshot') as render:
                with self.assertRaises(HTTPException) as error:
                    main.get_requirement_pdf('ROUND', user={'role': 'engineer'})
                self.assertEqual(503, error.exception.status_code)
                render.assert_not_called()

    def test_non_packaging_still_renders_saved_data(self):
        req = {'title': '独立非包装需求', 'data': {'industry': 'battery'}}
        with patch.object(requirement_pdf, 'fixed_packaging_requirement_pdf', side_effect=AssertionError('不能替换其他行业')):
            pdf = requirement_pdf.build_requirement_pdf(req)
        self.assertIn('独立非包装需求', text(pdf))

    def test_current_data_and_missing_values_not_template_numbers(self):
        req = requirement(); before = copy.deepcopy(req)
        view = self.build(req)
        content = text(requirement_pdf.render_packaging_snapshot(view))
        for value in ('408', '50.5', '157', '1000', '待确认', '客户填写', '图纸解析'):
            self.assertIn(value, content)
        for value in ('180 g', '2.5 mm', '7 个工作日', '15 个工作日', '50 × 40 × 30'):
            self.assertNotIn(value, content)
        self.assertEqual(before, req)

    def test_field_conflict_lists_both_sources_and_values(self):
        req = requirement()
        req['data']['field_provenance'] = {'inner_length': {'origin': 'user_confirmed', 'status': 'confirmed',
            'value': 408, 'alternatives': [{'origin': 'confirmed_from_cad', 'value': 404, 'status': 'confirmed'}]}}
        view = self.build(req)
        content = text(requirement_pdf.render_packaging_snapshot(view))
        for value in ('408', '404', '客户填写', '图纸解析', '存在差异', '草稿'):
            self.assertIn(value, content)

    def test_equal_numeric_evidence_and_user_text_are_not_false_conflicts(self):
        req = requirement()
        req['data']['field_sources']['inner_width'] = 'user_text'
        req['data']['field_provenance'] = {'inner_length': {'origin': 'user_confirmed', 'value': 408,
            'alternatives': [{'origin': 'confirmed_from_cad', 'value': '408.0'}]}}
        snapshot = self.build(req)
        self.assertEqual([], snapshot['conflicts'])
        width = next(r for s in snapshot['sections'] for r in s['rows'] if r.get('key') == 'inner_width')
        self.assertEqual('客户填写', width['source'])

    def test_geometry_inference_is_not_mislabelled_as_cad_dimension(self):
        req = requirement(); req['data']['field_sources']['inner_length'] = 'attachment'
        req['data']['field_provenance'] = {'inner_length': {'origin': 'inferred_from_geometry', 'value': 408,
                                                         'status': 'needs_confirmation'}}
        snapshot = self.build(req)
        item = next(r for s in snapshot['sections'] for r in s['rows'] if r.get('key') == 'inner_length')
        self.assertEqual('系统推荐', item['source'])
        self.assertTrue(snapshot['draft'])

    def test_zero_and_false_are_preserved_not_missing(self):
        snapshot = self.build()
        rows = {r.get('key'): r for s in snapshot['sections'] for r in s['rows']}
        self.assertEqual('0', rows['min_freight']['value'])
        self.assertEqual('否', rows['magnetic']['value'])
        self.assertFalse(rows['min_freight']['missing'])

    def test_placeholder_is_missing_and_downstream_changes_invalidate_hash(self):
        req = requirement(); req['data']['inner_height'] = '待确认'
        view = self.build(req)
        self.assertIn('成品内高', view['gaps'])
        one = self.build(cost={'estimate_id': 'C', 'total_cost': 1.005})
        two = self.build(cost={'estimate_id': 'C', 'total_cost': 1.015})
        self.assertNotEqual(one['data_hash'], two['data_hash'])
        self.assertIn('1.01', text(requirement_pdf.render_packaging_snapshot(one)))

    def test_download_preview_remain_fixed_when_form_changes(self):
        from tech_app.backend import main
        req = requirement(); req['title'] = '茅台当前实际订单'
        req['data']['requirement_pdf_template'] = 'maotai_wine_box'
        with patch.object(main, '_workflow_project', return_value={}), \
             patch.object(main.store, 'load_requirement', side_effect=lambda pid: copy.deepcopy(req)), \
             patch.object(main.store, 'load_meta', return_value={}), \
             patch('tech_app.backend.services.packaging_requirement_document.load_snapshot', side_effect=lambda pid, saved, meta: self.build(saved)):
            one = main.get_requirement_pdf('ROUND', user={'role': 'engineer'})
            download = main.get_requirement_pdf('ROUND', download=True, user={'role': 'engineer'})
            self.assertEqual(one.body, download.body)
            req['data']['inner_length'] = 420
            updated = main.get_requirement_pdf('ROUND', user={'role': 'engineer'})
        self.assertEqual(one.body, updated.body)
        self.assertEqual(ORIGINAL_SHA, hashlib.sha256(updated.body).hexdigest())

    def test_original_other_project_is_404_and_acl_is_checked_first(self):
        from fastapi import HTTPException
        from tech_app.backend import main
        with patch.object(main, '_workflow_project', return_value={}), \
             patch.object(main.store, 'load_requirement', return_value=requirement()), \
             patch.object(main.store, 'load_meta', return_value={}):
            with self.assertRaises(HTTPException) as error:
                main.get_original_requirement_pdf('ROUND', user={'role': 'engineer'})
            self.assertEqual(404, error.exception.status_code)
        with patch.object(main, '_workflow_project', side_effect=HTTPException(404, '项目不存在')), \
             patch.object(main.store, 'load_requirement') as read:
            for endpoint in (main.get_requirement_pdf, main.get_original_requirement_pdf):
                with self.assertRaises(HTTPException): endpoint('OTHER', user={'role': 'engineer'})
            read.assert_not_called()

    def test_missing_process_cost_are_not_invented(self):
        view = self.build()
        content = text(requirement_pdf.render_packaging_snapshot(view))
        for value in ('尚未生成', '待补充', '4. 工艺路线', '6. 成本构成', '7. 设备与刀版'):
            self.assertIn(value, content)
        self.assertNotIn('模切机', content)

    def test_saved_process_and_cost_are_rendered_without_computation(self):
        view = self.build(processes=[{'part_code': 'A', 'name': '面纸', 'steps': [
            {'step_name': '特定真实工序', 'workstation': '已保存设备甲'}]}],
            bom=[{'item_name': '真实材料甲', 'material': '指定纸张'}],
            cost={'estimate_id': 'C1', 'material_total': 1.23, 'process_total': 2.34,
                  'other_total': 0.45, 'total_cost': 4.02, 'quote_quantity': 1000})
        content = text(requirement_pdf.render_packaging_snapshot(view))
        for value in ('特定真实工序', '真实材料甲', '已保存设备甲', '1.23', '4.02', '已保存'):
            self.assertIn(value, content)

    def test_render_is_stable_and_change_invalidates_data_hash(self):
        req = requirement(); one = self.build(req)
        a = requirement_pdf.render_packaging_snapshot(one)
        self.assertEqual(a, requirement_pdf.render_packaging_snapshot(self.build(req)))
        req['data']['inner_length'] = 420
        two = self.build(req)
        self.assertNotEqual(one['data_hash'], two['data_hash'])
        self.assertNotEqual(a, requirement_pdf.render_packaging_snapshot(two))

    def test_original_has_independent_route(self):
        from tech_app.backend import main
        req = requirement(); req['data']['requirement_pdf_template'] = 'maotai_wine_box'
        with patch.object(main, '_workflow_project', return_value={}), \
             patch.object(main.store, 'load_requirement', return_value=req), \
             patch.object(main.store, 'load_meta', return_value={}):
            response = main.get_original_requirement_pdf('ROUND', download=True, user={'role': 'engineer'})
        self.assertEqual(ORIGINAL_SHA, hashlib.sha256(response.body).hexdigest())
        self.assertIn('attachment', response.headers['content-disposition'])

    def test_pdf_embeds_font_and_all_sections_survive_long_pagination(self):
        req = requirement(); req['data']['description'] = '这是一段必须保留的长需求文字。' * 120
        pdf = requirement_pdf.render_packaging_snapshot(self.build(req, processes=[
            {'part_code': str(index), 'name': '零件'+str(index), 'steps': [{'step_name': '真实工序'+str(index)}]}
            for index in range(60)]))
        reader = PdfReader(io.BytesIO(pdf)); content = text(pdf)
        self.assertGreater(len(reader.pages), 2)
        self.assertIn('真实工序59', content)
        self.assertIn('8. 结论与下一步', content)
        fonts = [font.get_object() for page in reader.pages for font in page['/Resources']['/Font'].get_object().values()]
        self.assertTrue(any('/FontFile2' in font.get('/FontDescriptor', {}) for font in fonts))

    def test_read_failures_are_not_cached_as_successful_empty_data(self):
        from tech_app.backend.services import packaging_requirement_render as renderer
        view = self.build(availability={'成本': 'unavailable'})
        view['generated_at'] = 'GENERATION-ONE'
        one = requirement_pdf.render_packaging_snapshot(view)
        view['generated_at'] = 'GENERATION-TWO'
        two = requirement_pdf.render_packaging_snapshot(view)
        self.assertNotEqual(one, two)
        self.assertIn('读取失败', text(two))
        with patch.object(renderer, 'MAX_CACHE_ENTRIES', 2):
            for index in range(4):
                req = requirement(); req['data']['inner_length'] = index + 100
                requirement_pdf.render_packaging_snapshot(self.build(req))
            self.assertLessEqual(len(renderer._CACHE), 2)


class StoredSnapshotReads(unittest.TestCase):
    def setUp(self):
        from tech_app.backend.services import packaging_requirement_document as document
        from tech_app.backend.storage import da_repo
        from tech_app.backend.services import packaging_parts, packaging_semantics
        from tech_app.backend.storage import meta_backend
        self.document = document
        self.index = {'business_parts_hash': 'CURRENT', 'business_parts': [{'business_part_code': 'A', 'scheme_id': 'one'},
            {'business_part_code': 'B', 'scheme_id': 'two'}], 'selected_scheme_id': 'one'}
        self.records = {'items': [{'part_code': 'A', 'business_parts_hash': 'CURRENT', 'plan': {'steps': [{'name': '当前工序'}]}},
                                  {'part_code': 'B', 'plan': {'steps': [{'name': '别的方案工序'}]}}]}
        self.cost = {'estimate_id': 'C', 'total_cost': 4.02, 'material_total': 1.23, 'quote_quantity': 1000,
                     'source_versions_json': '{"business_parts_hash":"OLD"}'}
        fake = type('Backend', (), {'get_doc': lambda _self, *args: self.records})()
        patches = [patch.object(packaging_parts, 'load_business_cost_index', side_effect=lambda pid: copy.deepcopy(self.index)),
                   patch.object(packaging_semantics, 'load_semantics', return_value={}),
                   patch.object(meta_backend, 'get_backend', return_value=fake),
                   patch.object(da_repo, 'load_packaging_bom', return_value=[]),
                   patch.object(da_repo, 'load_packaging_route_steps', return_value=[]),
                   patch.object(da_repo, 'load_packaging_cost', side_effect=lambda *a: copy.deepcopy(self.cost))]
        for item in patches: item.start(); self.addCleanup(item.stop)

    def test_saved_reads_do_not_generate_and_do_not_mix_schemes(self):
        from tech_app.backend.services import packaging_demo_flow, packaging_cost, llm_client
        with patch.object(packaging_demo_flow, 'prepare_route', side_effect=AssertionError('不生成路线')), \
             patch.object(packaging_cost, 'build_cost', side_effect=AssertionError('不算成本')), \
             patch.object(llm_client, 'run', side_effect=AssertionError('不调模型')):
            view = self.document.load_snapshot('ROUND', requirement(), {})
        content = text(requirement_pdf.render_packaging_snapshot(view))
        self.assertIn('当前工序', content)
        self.assertNotIn('别的方案工序', content)
        self.assertIn('历史结果，待核对', content)
        self.assertIn('4.02', content)

    def test_no_selected_scheme_does_not_publish_combined_cost_or_routes(self):
        self.index.pop('selected_scheme_id')
        view = self.document.load_snapshot('ROUND', requirement(), {})
        content = text(requirement_pdf.render_packaging_snapshot(view))
        self.assertTrue(view['draft'])
        self.assertNotIn('当前工序', content)
        self.assertNotIn('别的方案工序', content)
        self.assertNotIn('4.02', content)
        self.assertIn('尚未选择当前方案', content)

    def test_common_parts_are_not_mistaken_for_a_second_scheme(self):
        self.index.pop('selected_scheme_id')
        self.index['business_parts'] = [{'business_part_code': 'A', 'scheme_id': 'one'},
                                       {'business_part_code': 'B', 'scheme_id': 'common'}]
        view = self.document.load_snapshot('ROUND', requirement(), {})
        self.assertNotIn('scheme_required', view['availability'].values())
        content = text(requirement_pdf.render_packaging_snapshot(view))
        self.assertIn('当前工序', content)
        self.assertIn('别的方案工序', content)  # B在此夹具中改成了本方案共享件。


class ReferenceButton(unittest.TestCase):
    def test_real_dom_separates_live_and_original_view_and_download(self):
        from tests._chrome_page_probe import page_probe
        from tests.test_packaging_whole_part_process_unit_red import js_function
        chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
        if not chrome.exists(): self.skipTest('Chrome unavailable')
        function = js_function(ROOT/'tech_app/frontend/requirement-confirm-page.js', 'cfBindOriginalPdf')
        html = '<div class="pdf-actions"></div><button id="previewForm"></button><button id="viewForm"></button><div id="formModal"><span class="modal-title"></span><iframe></iframe><a class="modal-link"></a></div>'
        script = """
let cfPdfInfo={original_available:true};
const cfPdfUrl=(download,original)=>'/document'+(original?'/original':'')+'?download='+Boolean(download);
const check=(ok,message)=>{if(!ok)throw Error(message)};
""" + function + """
try{
cfBindOriginalPdf();document.querySelector('#viewOriginalRequirement').click();
check(document.querySelector('iframe').getAttribute('src')==='/document/original?download=false','original iframe wrong');
check(document.querySelector('.modal-link').getAttribute('href')==='/document/original?download=true','original download wrong');
document.querySelector('#viewForm').click();
check(document.querySelector('iframe').getAttribute('src')==='/document?download=false','default stuck on original');
check(document.querySelector('.modal-link').getAttribute('href')==='/document?download=true','default download wrong');
document.querySelector('#viewOriginalRequirement').remove();cfPdfInfo={original_available:false};cfBindOriginalPdf();
check(!document.querySelector('#viewOriginalRequirement'),'original shown for unrelated project');
cfPdfInfo={mode:'reference',original_available:true};cfBindOriginalPdf();
check(!document.querySelector('#viewOriginalRequirement'),'duplicate original button in fixed mode');
document.querySelector('#viewForm').click();
check(document.querySelector('.modal-title').textContent==='工艺需求单','fixed PDF claims live project data');
check(document.querySelector('iframe').getAttribute('src')==='/document?download=false','fixed default route wrong');
document.querySelector('#probe').textContent='PASS';
}catch(error){document.querySelector('#probe').textContent='FAIL: '+error.stack}
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'view.html'
            path.write_text('<!doctype html><meta charset="utf-8">'+html+'<pre id="probe"></pre><script>'+script+'</script>')
            self.assertEqual('PASS', page_probe(chrome, path, Path(directory)/'profile', "document.querySelector('#probe')?.textContent"))

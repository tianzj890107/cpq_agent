import unittest
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class RoutingTests(unittest.TestCase):
    def test_ids_lossless_and_empty_route_not_standard(self):
        m=importlib.import_module('cpq_process_routing')
        r=m.normalize({'md_clm_process_routing_base_info_id':4003115860410506284,
                       'effect_status':'已生效'}, [], 'product_item_code')
        self.assertEqual(r['header']['md_clm_process_routing_base_info_id'],'4003115860410506284')
        self.assertFalse(r['approved'])
    def test_missing_identity_no_connection(self):
        from unittest.mock import patch
        import cpq_process_routing as m
        with patch.object(m.cpq_db,'connect',side_effect=AssertionError('must not query')):
            self.assertEqual(m.lookup()['status'],'missing_identity')
    def test_bridge_failure_not_empty_library(self):
        from unittest.mock import patch
        from tech_app.backend.services import da_process_routing as m
        with patch.object(m.cpq_kb_client,'_internal_token',return_value=''):
            r=m.for_row({'name':'面纸'})
        self.assertEqual(r['status'],'unavailable')
        self.assertIn('模型建议',r['notice'])
    def test_draft_and_null(self):
        m = importlib.import_module('cpq_process_routing')
        r = m.normalize({'effect_status':'草稿','name':'面纸'}, [
            {'line_number':2,'man_time':None}, {'line_number':1,'man_time':None}], 'name')
        self.assertFalse(r['approved'])
        self.assertEqual([s['line_number'] for s in r['steps']], [1,2])
        self.assertIsNone(r['steps'][0]['man_time'])
    def test_expired_and_name_not_approved(self):
        m = importlib.import_module('cpq_process_routing')
        for method,end in [('name',None),('product_item_code','2000-01-01')]:
            self.assertFalse(m.normalize({'effect_status':'已生效','effect_end_date':end}, [], method)['approved'])
    def test_internal_bridge(self):
        s=(ROOT/'cpq_suite_server.py').read_text()
        self.assertIn('/wf/tech/process-routing',s)
    def test_live_reader_links_header_and_sorted_lines_without_writes(self):
        from unittest.mock import MagicMock, patch
        import cpq_process_routing as m
        cursor=MagicMock()
        cursor.fetchall.side_effect=[
            [{'md_clm_process_routing_base_info_id':123,'effect_status':'草稿'}],
            [{'line_number':2,'operation_name':'包盒'}, {'line_number':1,'operation_name':'开料'}]]
        conn=MagicMock()
        conn.__enter__.return_value=conn
        conn.cursor.return_value.__enter__.return_value=cursor
        with patch.object(m.cpq_db,'connect',return_value=conn) as connect:
            result=m.lookup(product_item_code="code' injection")
        connect.assert_called_once_with(readonly=True)
        self.assertEqual(result['status'],'matched')
        self.assertEqual([s['operation_name'] for s in result['routes'][0]['steps']],['开料','包盒'])
        calls=cursor.execute.call_args_list
        self.assertEqual(calls[1].args[1],("code' injection",))
        self.assertEqual(calls[2].args[1],(123,))
        self.assertIn('md_process_rputing_id',repr(calls[2].args[0]))
    def test_http_name_and_product_code_are_encoded(self):
        from unittest.mock import MagicMock, patch
        from tech_app.backend.services import da_process_routing as m
        response=MagicMock()
        response.read.return_value=b'{"ok":true,"status":"not_found","routes":[]}'
        with patch.object(m.cpq_kb_client,'_internal_token',return_value='test-token'), \
             patch.object(m.cpq_kb_client,'_base',return_value='http://localhost'), \
             patch.object(m.urllib.request,'urlopen') as request:
            request.return_value.__enter__.return_value=response
            result=m.for_row({'name':'面纸','reference':{'product_item_code':'YT-01'}})
        self.assertEqual(result['status'],'not_found')
        self.assertIn('product_item_code=YT-01',request.call_args.args[0].full_url)
    def test_consumers(self):
        s=(ROOT/'tech_app/backend/main.py').read_text()
        self.assertIn('da_process_routing.for_row',s)
        self.assertIn('"lookup": da_lookup',s)
        s=(ROOT/'tech_app/backend/services/packaging_sections.py').read_text()
        self.assertIn('da_process_routing.for_row',s)

"""隔离大CAD夹具；不访问实际项目、数据库或模型。"""
import concurrent.futures
import copy
import hashlib
import io
import json
import statistics
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tech_app.backend.services import packaging_parts as parts
from tech_app.backend.services import da_process_routing as routing
from tech_app.backend.storage.meta_backend import JsonMetaBackend


def fixture():
    vertices = [[i * .1, i * .2] for i in range(1200)]
    rows = [{'business_part_code': f'BP{i:02}', 'name': f'测试面纸{i}',
             'reference': {'material_text': '225g铜版纸', 'length_mm': 300, 'width_mm': 500},
             'candidates': [{'id': str(j), 'vertices': copy.deepcopy(vertices)} for j in range(8)]}
            for i in range(28)]
    return {'business_parts_id': 'v1', 'business_parts_hash': 'h1', 'business_parts': rows,
            'geometry_evidence': {'raw_entities': {str(i): {'points': vertices[:20]} for i in range(500)}}}


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.backend = JsonMetaBackend(Path(self.temp.name))
        self.doc = {'business_parts_id': 'v1', 'business_parts_hash': 'h1',
                    'business_parts': [{'business_part_code': 'A', 'name': '面纸'},
                                       {'business_part_code': 'B', 'name': '灰板'}],
                    'geometry_evidence': {'raw_entities': {'x': {'points': [1, 2]}}}}
        self.backend.put_doc('P', parts.BUSINESS_DOC_KEY, {'items': [self.doc]})
        self.patcher = patch.object(parts, 'get_backend', return_value=self.backend)
        self.patcher.start(); self.addCleanup(self.patcher.stop)

    def test_projection_copies_only_one_complete_row_and_identity(self):
        view = parts.load_business_process_context('P', 'A')
        self.assertEqual(self.doc['business_parts'][:1], view['business_parts'])
        self.assertEqual('h1', view['business_parts_hash'])
        self.assertNotIn('geometry_evidence', view)
        view['business_parts'][0]['name'] = '污染'
        self.assertEqual('面纸', parts.load_business_process_context('P', 'A')['business_parts'][0]['name'])

    def test_update_and_absent_latest_do_not_reuse_old_rows(self):
        parts.load_business_process_context('P', 'A')
        self.doc['business_parts'][0]['name'] = '新名称'
        self.backend.put_doc('P', parts.BUSINESS_DOC_KEY, {'items': [self.doc]})
        self.assertEqual('新名称', parts.load_business_process_context('P', 'A')['business_parts'][0]['name'])
        self.backend.put_doc('P', parts.BUSINESS_DOC_KEY, {'items': [{'business_parts': []}, self.doc]})
        self.assertEqual([], parts.load_business_process_context('P', 'A')['business_parts'])

    def test_get_uses_projection_not_full_loader_and_keeps_response(self):
        from tech_app.backend import main
        record = {'part_code': 'A', 'plan': {'steps': [{'name': '模切'}]},
                  'process_scope': 'business_part/1', 'business_parts_id': 'v1', 'business_parts_hash': 'h1'}
        with patch.object(main, '_workflow_project'), patch.object(parts, 'load_part_process', return_value=record):
            with patch.object(parts, 'load_business_process_context', side_effect=lambda pid, code: parts.load_business_parts(pid)):
                before = main.get_packaging_business_part_process('P', 'A', user={})
            with patch.object(parts, 'load_business_parts', side_effect=AssertionError('不应整份复制')):
                after = main.get_packaging_business_part_process('P', 'A', user={})
        self.assertEqual(before, after)


class ColdBridgeTests(unittest.TestCase):
    def setUp(self):
        routing.clear_cache(); self.addCleanup(routing.clear_cache)

    def run_parallel(self, fail=False):
        barrier = threading.Barrier(3)
        lock = threading.Lock(); calls = []
        def open_response(*a, **kw):
            with lock: calls.append(1)
            time.sleep(.08)
            if fail: raise OSError('offline')
            return io.BytesIO(b'{"ok":true,"status":"ok","candidates":[]}')
        def work():
            barrier.wait()
            try: return routing._fetch_candidates()
            except OSError: return 'failed'
        with patch.object(routing.cpq_kb_client, '_base', return_value='http://fixture'), \
             patch.object(routing.cpq_kb_client, '_internal_token', return_value='test'), \
             patch.object(routing, 'CACHE_TTL_SECONDS', 300), \
             patch.object(routing.urllib.request, 'urlopen', side_effect=open_response), routing.cache_scope():
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                result = list(pool.map(lambda _: work(), range(3)))
            if not fail:
                result[0]['candidates'].append('private')
                self.assertEqual([], result[1]['candidates'])
                self.assertEqual([], routing._fetch_candidates()['candidates'])
        return len(calls), result

    def test_cold_parallel_reads_are_coalesced(self):
        count, _ = self.run_parallel()
        self.assertEqual(1, count)

    def test_failure_wakes_waiters_and_next_call_retries(self):
        count, result = self.run_parallel(fail=True)
        self.assertEqual(['failed'] * 3, result)
        self.assertEqual(1, count)
        count, _ = self.run_parallel()
        self.assertEqual(1, count)

    def test_bridge_identity_is_part_of_cache_key(self):
        with patch.object(routing.cpq_kb_client, '_base', side_effect=['http://a', 'http://b']), \
             patch.object(routing.cpq_kb_client, '_internal_token', return_value='test'), \
             patch.object(routing.urllib.request, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(b'{"ok":true,"status":"ok","candidates":[]}')) as op, \
             routing.cache_scope():
            routing._fetch_candidates(); routing._fetch_candidates()
        self.assertEqual(2, op.call_count)

    def test_different_keys_do_not_serialize_network_io(self):
        entered = threading.Barrier(2)
        def response(*args, **kwargs):
            entered.wait(timeout=2)
            return io.BytesIO(b'{"ok":true,"status":"matched","routes":[]}')
        with patch.object(routing.cpq_kb_client, '_base', return_value='http://fixture'), \
             patch.object(routing.cpq_kb_client, '_internal_token', return_value='test'), \
             patch.object(routing.urllib.request, 'urlopen', side_effect=response), routing.cache_scope(), \
             concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda name: routing._fetch(name, name), ['A', 'B']))

    def test_token_rotation_does_not_reuse_other_identity(self):
        with patch.object(routing.cpq_kb_client, '_base', return_value='http://fixture'), \
             patch.object(routing.cpq_kb_client, '_internal_token', side_effect=['one', 'two']), \
             patch.object(routing.urllib.request, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(b'{"ok":true,"status":"ok","candidates":[]}')) as op, \
             routing.cache_scope():
            routing._fetch_candidates(); routing._fetch_candidates()
        self.assertEqual(2, op.call_count)


class LocalBenchmark(unittest.TestCase):
    def test_measure_28_part_existing_process_read(self):
        from tech_app.backend import main
        with tempfile.TemporaryDirectory() as directory:
            backend = JsonMetaBackend(Path(directory)); doc = fixture()
            backend.put_doc('P', parts.BUSINESS_DOC_KEY, {'items': [doc]})
            records = [{'part_code': row['business_part_code'], 'business_part_code': row['business_part_code'],
                        'business_parts_id': 'v1', 'business_parts_hash': 'h1', 'process_scope': 'business_part/1',
                        'plan': {'steps': [{'name': '开料'}, {'name': '模切'}]}}
                       for row in doc['business_parts']]
            backend.put_doc('P', parts.DOC_KEY_PROCESS, {'items': records})
            with patch.object(parts, 'get_backend', return_value=backend), patch.object(main, '_workflow_project'):
                measurements={}; results=None
                optimized = parts.load_business_process_context
                for mode in ('full_document_baseline', 'projected'):
                    loader = (lambda pid, code: parts.load_business_parts(pid)) if mode == 'full_document_baseline' else optimized
                    timings=[]
                    with patch.object(parts, 'load_business_process_context', side_effect=loader):
                        for _ in range(3):
                            start=time.perf_counter()
                            current=[main.get_packaging_business_part_process('P', r['part_code'], user={}) for r in records]
                            timings.append(time.perf_counter()-start)
                            if results is not None: self.assertEqual(results, current)
                            results=current
                    measurements[mode]={'seconds':timings, 'median_seconds':statistics.median(timings)}
            print('\nLOCAL_PROCESS_BENCHMARK '+json.dumps({'parts':28, 'measurements':measurements,
                'document_bytes':(Path(directory)/'P'/f'{parts.BUSINESS_DOC_KEY}.json').stat().st_size,
                'model_calls':0, 'response_sha256':hashlib.sha256(json.dumps(results,sort_keys=True).encode()).hexdigest(),
                'fixture':'synthetic CAD; real GET + JSON backend'},ensure_ascii=False))

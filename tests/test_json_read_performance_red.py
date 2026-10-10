import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from tech_app.backend.storage.meta_backend import JsonMetaBackend


class ReadPerformanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.backend = JsonMetaBackend(Path(self.temp.name))
        self.backend.put_doc('p', 'parts', {'items': [{'n': 1}]})

    def test_repeated_read_parses_once_and_returns_independent_data(self):
        original = json.loads
        with patch('tech_app.backend.storage.meta_backend.json.loads', wraps=original) as loads:
            a = self.backend.get_doc('p', 'parts'); a['items'][0]['n'] = 99
            b = self.backend.get_doc('p', 'parts')
            self.assertEqual(1, b['items'][0]['n'])
            self.assertEqual(1, loads.call_count)

    def test_reads_do_not_wait_for_unrelated_global_write_lock(self):
        done = threading.Event()
        with self.backend._lock:
            thread = threading.Thread(target=lambda: (self.backend.get_doc('p', 'parts'), done.set()))
            thread.start()
            finished = done.wait(.3)
        thread.join(2)
        self.assertTrue(finished, 'get_doc waits for global write lock')

    def test_external_change_and_corruption_invalidate_cached_data(self):
        self.backend.get_doc('p', 'parts')
        target = Path(self.temp.name) / 'p/parts.json'
        target.write_text('{"items":[{"n":2}]}')
        self.assertEqual(2, self.backend.get_doc('p', 'parts')['items'][0]['n'])
        target.write_text('{bad')
        with self.assertRaises(RuntimeError): self.backend.get_doc('p', 'parts')

    def test_cache_is_bounded(self):
        self.backend._cache_max_entries = 2
        for i in range(5):
            self.backend.put_doc('p', str(i), {'n': i})
            self.backend.get_doc('p', str(i))
        self.assertLessEqual(len(self.backend._read_cache), 2)

    def test_latest_projection_does_not_copy_or_remove_history(self):
        self.backend.put_doc('p', 'history', {'items': [{'v': 2}, {'v': 1}]})
        first = self.backend.get_doc_head('p', 'history')
        self.assertEqual([{'v': 2}], first['items'])
        first['items'][0]['v'] = 99
        self.assertEqual([{'v': 2}, {'v': 1}], self.backend.get_doc('p', 'history')['items'])

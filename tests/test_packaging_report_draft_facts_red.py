import unittest
from unittest.mock import patch
from tech_app.backend.services import report_workflow


class PackagingReportDraftFactsTest(unittest.TestCase):
    def test_incomplete_packaging_snapshot_generates_honest_draft_not_blank_legacy_report(self):
        req = {'requirement_no': 'REQ-1', 'data': {'industry': 'packaging', 'title': '圆盘方案A'}}
        snapshot = {'kind': 'packaging', 'industry': 'packaging', 'ready': False,
                    'parts': [], 'cost': {}, 'gaps': [{'code': 'missing_dimensions'}]}
        with patch.object(report_workflow.store, 'load_requirement', return_value=req), \
             patch.object(report_workflow, '_aggregate_with_snapshot', return_value={
                 'manufacturing': snapshot, 'summary': {}}), \
             patch.object(report_workflow, 'report_no', return_value='RPT-1'):
            doc = report_workflow.new_report('p1', {'username': 'PE1'})
        self.assertEqual('圆盘方案A工艺评估报告', doc.title)
        self.assertIn('成本尚未测算完成', doc.conclusion)
        self.assertEqual('draft', doc.status)
        self.assertIsNone(doc.reviewed_by)

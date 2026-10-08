import unittest
from tech_app.backend.services import home_card


class PackagingProjectIdentityTest(unittest.TestCase):
    def test_quote_title_and_real_user_turns_not_attachment_or_task_logs(self):
        self.assertTrue(hasattr(home_card, 'project_list_identity'), '卡片仍使用附件名与固定0轮')
        meta = {'source_filename': '圆盘盒.dwg'}
        req = {'title': '需求单123', 'data': {'title': '客户项目方案A'}}
        events = [{'kind': 'user', 'seq': 1, 'text': '解析'},
                  {'kind': 'task', 'seq': 2}, {'kind': 'assistant', 'seq': 3},
                  {'kind': 'user', 'seq': 4, 'text': '推荐工艺'}]
        result = home_card.project_list_identity(meta, req, events)
        self.assertEqual('客户项目方案A', result['project_name'])
        self.assertEqual(2, result['turns'])
        self.assertEqual('圆盘盒.dwg', result['source_filename'])

    def test_explicit_project_rename_has_priority(self):
        self.assertTrue(hasattr(home_card, 'project_list_identity'))
        row = home_card.project_list_identity({'project_name': '手动命名'}, {'data': {'title': '旧需求'}}, [])
        self.assertEqual('手动命名', row['project_name'])

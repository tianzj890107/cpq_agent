"""行业推荐须来自服务端，且不能伪造成品身份和成本。"""
import unittest
import cpq_agent_server as server
from cpq_industries import INDUSTRY_KEYS


class IndustryDefaults(unittest.TestCase):
    def test_each_industry_has_six_editable_draft_sections(self):
        descriptions = set()
        for industry in INDUSTRY_KEYS:
            forms = {f['section_id']: f for f in server.fixed_forms_catalog(industry)}
            for sid in ('s1_basic', 's1_dest', 's1_products', 's1_techparams', 's1_payment', 's1_logistics'):
                with self.subTest(industry=industry, section=sid):
                    self.assertTrue(forms[sid].get('recommended_values') or forms[sid].get('recommended_rows'),
                                    '新报价六个分区应有可编辑行业推荐')
            rows = forms['s1_products'].get('recommended_rows', [])
            if rows:
                descriptions.add(rows[0].get('成品描述'))
                for key in ('成品编码', '价格', '基础成本', '整单成本'):
                    self.assertFalse(rows[0].get(key), '推荐不能伪造成品身份或成本')
        self.assertEqual(4, len(descriptions))

    def test_recommendations_do_not_leak_between_calls(self):
        forms = server.fixed_forms_catalog('packaging')
        product = next(f for f in forms if f['section_id'] == 's1_products')
        self.assertTrue(product.get('recommended_rows'), '缺行业推荐')
        product['recommended_rows'][0]['成品描述'] = '修改过的内容'
        other = next(f for f in server.fixed_forms_catalog('packaging') if f['section_id'] == 's1_products')
        self.assertNotEqual('修改过的内容', other['recommended_rows'][0]['成品描述'])

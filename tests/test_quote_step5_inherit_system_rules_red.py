import copy
import unittest
from unittest.mock import MagicMock, patch
import cpq_quote_workspace as workspace
import cpq_wf
from tests.test_packaging_quote_close_loop_red import package_fixture


class InheritSystemRules(unittest.TestCase):
    def run_save(self, stale=False, missing=False):
        package=package_fixture()
        rules={'profit_amount':5,'addon_amount':2,'stages':{'3':{'markup':[]},'4':{'markup':[]}}}
        source={'rule_pricing':rules,'source_cost_fingerprint':'stale' if stale else workspace.cost_fingerprint(package)}
        snapshots={2:{'packaging_package':package},4:{} if missing else {'packaging_quote':source},5:{}}
        card={'card_id':1,'industry':'packaging','current_step':5,'creator_user_id':1,'current_owner':1,'overall_status':'in_progress'}
        conn=MagicMock();conn.cursor.return_value.fetchall.return_value=[]
        with patch('cpq_auth._connect',return_value=conn),patch('cpq_wf._fetch_card',return_value=card),\
             patch('cpq_wf.step_snapshot',side_effect=lambda sid,step,**kw:copy.deepcopy(snapshots.get(step,{}))),\
             patch('cpq_wf.merge_step_snapshot') as save:
            result=workspace.save_pricing('Q',5,{'quote_quantity':1000,'tax_rate':.13,'discount':{'rate':0}},
                {'user_id':1,'role_code':'sales_mgr'},expected_cost_fingerprint=workspace.cost_fingerprint(package))
            self.assertEqual(rules,result['quote']['rule_pricing'])
            self.assertEqual(result['quote']['cost_total']+7,result['quote']['subtotal_unit'])
            self.assertEqual(5,save.call_args.args[1])
            return result

    def test_first_step5_save_inherits_stage4_instead_of_requiring_margin(self):
        self.run_save()

    def test_stale_source_rules_cannot_be_inherited(self):
        with self.assertRaisesRegex(cpq_wf.WfError,'成本版本|系统定价规则'):
            self.run_save(stale=True)

    def test_missing_rules_reports_action_not_internal_margin_parameter(self):
        with self.assertRaisesRegex(cpq_wf.WfError,'系统.*规则'):
            self.run_save(missing=True)

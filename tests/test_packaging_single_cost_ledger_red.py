import copy
from pathlib import Path
import unittest
import asyncio
import subprocess
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]

class SingleLedger(unittest.TestCase):
    def fixture(self):
        doc = {'business_parts':[{'business_part_code':'p','name':'件','reference':{'quantity':2}}]}
        cost = {'built':True, 'estimate_id':'same', 'quote_quantity':1000,
                'material_total':11, 'labor_total':6, 'process_total':3,
                'tooling_total':2,'packaging_total':3,'freight_total':5,'other_total':0,
                'total_cost':30, 'items':[
                    {'part_code':'p','cost_category':'material','amount':10,'loss_rate':.1},
                    {'part_code':'p','cost_category':'labor','amount':6},
                    {'part_code':'p','cost_category':'die_cutting','amount':3}]}
        return doc,cost

    def project(self, doc, cost):
        from tech_app.backend.services import packaging_cost_ledger
        return packaging_cost_ledger.project(doc,cost)

    def test_one_ledger_no_usage_or_loss_double_count(self):
        doc,cost = self.fixture()
        before = copy.deepcopy(cost)
        view = self.project(doc,cost)
        row = view['parts'][0]
        self.assertEqual(20,row['subtotal'])
        self.assertEqual(10,row['unit_cost'])
        self.assertEqual(11,view['parts_total']['material'])
        self.assertEqual(10,view['project_expenses']['total'])
        self.assertEqual(30,view['final']['total'])
        self.assertTrue(view['reconciled'])
        self.assertEqual('same',row['estimate_id'])
        self.assertEqual(before,cost)

    def test_stale_does_not_make_old_cost_formal(self):
        doc,cost = self.fixture()
        cost['stale'] = True
        view = self.project(doc,cost)
        self.assertFalse(view['parts'][0]['has_cost'])
        self.assertTrue(view['parts'][0]['cost_stale'])
        self.assertFalse(view['ready'])

    def test_missing_and_invalid_amount_are_not_zero_prices(self):
        doc,cost = self.fixture()
        cost['items'][0]['amount'] = None
        view = self.project(doc,cost)
        self.assertFalse(view['parts'][0]['has_cost'])
        self.assertFalse(view['reconciled'])

    def test_project_expense_missing_amount_cannot_be_hidden(self):
        doc,cost = self.fixture()
        cost['items'].append({'part_code':None,'cost_category':'packaging','amount':None})
        view = self.project(doc,cost)
        self.assertFalse(view['reconciled'])
        self.assertIn('invalid_amount:project',view['reconciliation_errors'])

    def test_mismatch_blocks_confirmation(self):
        doc,cost = self.fixture()
        cost['total_cost'] = 999
        view = self.project(doc,cost)
        self.assertFalse(view['reconciled'])
        self.assertFalse(view['ready'])

    def test_integration_single_source_and_packaging_ui(self):
        main = (ROOT/'tech_app/backend/main.py').read_text()
        self.assertIn('packaging_cost_ledger.rebuild_part',main)
        review = (ROOT/'tech_app/backend/services/cost_review.py').read_text()
        self.assertIn('packaging_cost_ledger.load',review)
        ui = (ROOT/'tech_app/frontend/cost-review.js').read_text()
        self.assertIn('crPackagingBreakdown',ui)
        self.assertIn('单件与整单来自同一份公式成本明细',ui)
        self.assertIn('return crRunPackagingLedger()',ui)

    def test_part_route_rebuilds_canonical_ledger_without_writing_old_cost(self):
        from tech_app.backend import main
        from tech_app.backend.services import packaging_parts, packaging_cost, packaging_cost_ledger
        doc, ledger = self.fixture()
        user = {'username':'FI1','role':'finance_manager'}
        settled = []
        def submit(pid, name, job, **kwargs):
            settled.append(job())
            return 'task-canonical'
        with patch.object(main, '_workflow_project'), patch.object(main, '_require'), \
             patch.object(packaging_parts, 'load_business_parts', return_value=doc), \
             patch.object(packaging_parts, 'save_part_cost') as old_write, \
             patch.object(packaging_cost, 'build_cost', return_value=ledger) as build, \
             patch.object(packaging_cost, 'load_cost', return_value=ledger), \
             patch.object(main.tasks, 'submit', side_effect=submit), \
             patch.object(main.tasks, 'report_progress'):
            result = asyncio.run(main.packaging_business_part_cost('project','p',2,'',[],user))
        self.assertEqual('task-canonical',result['task_id'])
        build.assert_called_once_with('project',actor=user)
        old_write.assert_not_called()
        self.assertEqual(10,settled[0]['summary']['computed_total'])
        self.assertEqual('same',settled[0]['estimate_id'])

    def test_part_summary_reads_canonical_cost_and_ignores_old_record(self):
        from tech_app.backend.services import packaging_parts, packaging_cost, cost_review
        doc, ledger = self.fixture()
        with patch.object(cost_review.store, 'load_requirement', return_value={'data':{'industry':'packaging'}}), \
             patch.object(packaging_parts, 'load_business_parts', return_value=doc), \
             patch.object(packaging_parts, 'load_part_cost', side_effect=AssertionError('old ledger must not be read')), \
             patch.object(packaging_cost, 'load_cost', return_value=ledger), \
             patch.object(packaging_cost, 'readiness_verdict', return_value={'formal_ready':True}):
            view = cost_review.summarize('project',None,None)
        self.assertTrue(view['ready'])
        self.assertEqual(30,view['final']['total'])
        self.assertTrue(view['parts'][0]['has_cost'])

    def test_no_waiver_can_bypass_reconciliation_failure(self):
        from tech_app.backend.services import packaging_cost_ledger, cost_flow
        with patch.object(cost_flow.store, 'load_requirement', return_value={'data':{'industry':'packaging'}}), \
             patch.object(packaging_cost_ledger, 'load', return_value={'reconciled':False}):
            with self.assertRaises(cost_flow.CostFlowError):
                cost_flow._assert_packaging_ledger('p')

    def test_missing_canonical_ledger_is_not_legacy_stale(self):
        doc, _ = self.fixture()
        view = self.project(doc,{})
        self.assertFalse(view['parts'][0]['has_cost'])
        self.assertFalse(view['parts'][0]['cost_stale'])

    def test_unknown_part_code_is_not_silently_lost(self):
        doc,cost = self.fixture()
        cost['items'][0]['part_code'] = 'unknown'
        view = self.project(doc,cost)
        self.assertFalse(view['reconciled'])
        self.assertIn('unknown_part:unknown',view['reconciliation_errors'])

    def test_real_formula_sqlite_replay_reconciles_with_part_projection(self):
        from tests.test_packaging_cost_engine_red import CostCase, PID, REQ_NO
        case = CostCase()
        case.setUp()
        try:
            case.prepare()
            engine = case.cost_mod()
            engine.build_cost(PID, requirement_no=REQ_NO)
            ledger = engine.load_cost(PID, requirement_no=REQ_NO)
            codes = sorted({row['part_code'] for row in ledger['items'] if row.get('part_code')})
            doc = {'business_parts':[{'business_part_code':code,'name':code,
                'reference':{'quantity':1}} for code in codes]}
            view = self.project(doc,ledger)
            self.assertTrue(view['reconciled'],view['reconciliation_errors'])
            self.assertAlmostEqual(ledger['total_cost'],
                sum(row['subtotal'] for row in view['parts']) + view['project_expenses']['total'])
        finally:
            case.tearDown()

    def test_geometry_alias_does_not_choose_arbitrary_overlapping_part(self):
        from tech_app.backend.services import packaging_parts, packaging_cost_ledger
        doc = {'business_parts':[{'business_part_code':code,'geometry_binding':{'component_ids':['c']}}
                                for code in ('first','second')]}
        with patch.object(packaging_parts,'load_business_parts',return_value=doc):
            with self.assertRaisesRegex(ValueError,'unique_business_part'):
                packaging_cost_ledger.business_code_for_geometry('p',{'component_id':'c'})

    def test_changed_quote_quantity_invalidates_same_ledger(self):
        from tech_app.backend.services import packaging_parts, packaging_cost, packaging_cost_ledger
        doc,cost = self.fixture()
        with patch.object(packaging_parts,'load_business_parts',return_value=doc), \
             patch.object(packaging_cost,'load_cost',return_value=cost), \
             patch.object(packaging_cost.store,'load_requirement',return_value={'data':{'quote_quantity':2000}}):
            view = packaging_cost_ledger.load('p')
        self.assertTrue(view['parts'][0]['cost_stale'])
        self.assertFalse(view['ready'])

    def test_packaging_batch_runs_once_and_keeps_current_tab(self):
        source = (ROOT/'tech_app/frontend/cost-review.js').read_text()
        a = source.index('async function crRunPackagingLedger(')
        b = source.index('\nasync function crRunAll()',a)
        script = '''
const assert = require('assert');
let crBusy = false, crTab = 'parts', calls = 0;
const crData = {packaging_cost:{}, counts:{parts:2,parts_costed:2,missing:[]}};
const crSaveNote = async () => true;
const crRunAssembly = async () => {calls++; return true;};
const crRender = () => {};
''' + source[a:b] + '''
crRunPackagingLedger().then(result => {
 assert.equal(calls,1); assert.equal(crTab,'parts'); assert.equal(result.succeeded,2);
}).catch(e=>{console.error(e);process.exit(1)});
'''
        result = subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_packaging_display_has_real_fee_names_not_manufacturing_bucket(self):
        source = (ROOT/'tech_app/frontend/cost-review.js').read_text()
        a = source.index('function crPackagingBreakdown(')
        b = source.index('\nfunction ',a+10)
        script = '''
const assert = require('assert'); const crMoney = value => Number(value||0).toFixed(2);
''' + source[a:b] + '''
const html = crPackagingBreakdown({material:14.7,labor:18.23,processing:8.03,tooling:18,freight:3.21,others:29.24});
assert(html.includes('材料')); assert(html.includes('人工')); assert(html.includes('加工费'));
assert(html.includes('模具摊销')); assert(html.includes('运输费'));
assert(!html.includes('制造费用')); assert(html.includes('<details'));
'''
        result = subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_new_ledger_rebuild_does_not_keep_old_confirmation(self):
        from tests.test_packaging_cost_engine_red import CostCase, PID, REQ_NO
        from tech_app.backend.storage import store
        case = CostCase()
        case.setUp()
        try:
            case.prepare()
            engine = case.cost_mod()
            with patch.object(store,'load_cost_review',return_value={'confirmed':True,'project_id':PID}), \
                 patch.object(store,'save_cost_review') as save:
                engine.build_cost(PID,requirement_no=REQ_NO)
            self.assertTrue(save.called)
            self.assertFalse(save.call_args.args[1]['confirmed'])
        finally:
            case.tearDown()

    def test_quantity_edit_updates_formula_requirement_not_only_plan(self):
        from tech_app.backend.services import cost_flow, cost_review, integration
        requirement = {'title':'原需求','data':{'industry':'packaging','quote_quantity':1000,'material':'原材料'}}
        plan = SimpleNamespace(quantity=1000)
        review = SimpleNamespace(note='')
        with patch.object(cost_flow,'cost_review_ctx',return_value=(None,plan,review)), \
             patch.object(cost_review,'save_review'), patch.object(cost_review,'payload',return_value={}), \
             patch.object(integration,'save_plan'), \
             patch.object(cost_flow.store,'load_requirement',return_value=requirement), \
             patch.object(cost_flow.store,'save_requirement') as save:
            cost_flow.save_note('p',{'username':'FI1'},quantity=2000)
        self.assertEqual(2000,save.call_args.args[1]['data']['quote_quantity'])
        self.assertEqual('原材料',save.call_args.args[1]['data']['material'])
        self.assertEqual(1000,requirement['data']['quote_quantity'])

"""隔离执行实际预填及恢复函数，确保保留用户值与服务端快照。"""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
import cpq_agent_server as server


class DefaultRendering(unittest.TestCase):
    def test_defaults_editability_and_recovery(self):
        html = (Path(__file__).resolve().parents[1] / '确认需求解析结果.html').read_text()
        source = html[html.index('    function rememberNewQuoteDraft()'):html.index('    /* ================= 第 1 步：三段串行')]
        forms = [f for f in server.fixed_forms_catalog('packaging') if f['step'] == 1]
        script = "const FORMS = " + json.dumps(forms, ensure_ascii=False) + ';\n' + r'''
const assert = require('node:assert/strict');
let currentSessionId = 'new-session', currentStep = 1;
const document = {addEventListener(){}};
const store = new Map();
const sessionStorage = {getItem:k=>store.get(k), setItem:(k,v)=>store.set(k,v)};
let data = {s1_basic:{数据:{客户:'真实客户', 项目名称:'真实项目'}},
            s1_dest:{数据:[{发运国家:'新加坡'}]}};
const collectStepData = () => data;
const renderFormSection = f => {data[f.section_id] = {数据:Object.fromEntries(f.fields.map(x=>[x.key,x.value]))};};
const renderTableSection = f => {data[f.section_id] = {数据:structuredClone(f.rows)};};
let hints = [];
const addAiBubble = text => hints.push(text);
''' + source + r'''
fillNewQuoteRecommendations();
assert.equal(data.s1_basic.数据.客户, '真实客户');
assert.equal(data.s1_basic.数据.币种, 'CNY');
assert.equal(data.s1_dest.数据[0].发运国家, '新加坡');
assert.equal(data.s1_payment.数据.length, 2);
assert.equal(data.s1_products.数据[0].成品编码, undefined);
assert.equal(hints.length, 1);
store.set('cpq:newQuoteDraft:new-session', '{}');
data.s1_payment.数据[0].计划收付款比例 = '50';
rememberNewQuoteDraft();
data = {};
restoreNewQuoteDraft();
assert.equal(data.s1_payment.数据[0].计划收付款比例, '50');
data.s1_payment.数据 = [{阶段名称:'服务端已保存', 计划收付款比例:'100'}];
restoreNewQuoteDraft();
assert.equal(data.s1_payment.数据[0].阶段名称, '服务端已保存');
currentSessionId = 'historical-session';
data = {};
restoreNewQuoteDraft();
assert.deepEqual(data, {}, '历史项目不能套用新建默认');
'''
        node = shutil.which('node')
        self.assertIsNotNone(node)
        result = subprocess.run([node, '-e', script], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

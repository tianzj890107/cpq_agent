"""运行实际首页路由，验证普通报价无需信息确认弹框。"""
import json
import pathlib
import shutil
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class QuoteCreateWithoutModal(unittest.TestCase):
    def check_entry(self, requirement, extra=""):
        home = (ROOT / '报价首页.html').read_text(encoding='utf-8')
        start = home.index('    function requestNavigate(')
        end = home.index('    async function submitQuickQuoteRequirement(', start)
        source = home[start:end]
        script = r'''
const assert = require('node:assert/strict');
let shown = [], navigated = [], _pendingNav = null, quoteNavigating = false;
const nodes = new Map();
const document = {getElementById(id) {
  if (!nodes.has(id)) nodes.set(id, {value:'', style:{}, classList:{
    add(x){ if(x === 'show') shown.push(id); }, toggle(){}, remove(){}
  }, focus(){}});
  return nodes.get(id);
}};
const $ = id => document.getElementById(id);
const PAGES = {quote:'确认需求解析结果.html'};
const MODES = {quote:{tag:'报价'}};
const storage = new Map([['cpq:openSession','old-session'], ['cpq:customer','旧客户']]);
const sessionStorage = {setItem(k,v){storage.set(k,v);}, removeItem(k){storage.delete(k);}, getItem(k){return storage.get(k)||null;}};
const window = {location:{href:''}};
const pickedFiles = ['需求.pdf'], pickedContents = [], pickedQuoteRefs = [];
let homeParsing = false, quoteUploadError = '';
const techIndustry = () => 'packaging';
const genProjectCode = () => 'BJD202610123';
const refreshPjFiles = () => {};
const setTimeout = () => {};
const alert = message => {throw Error(message);};
const closeOverlays = () => {};
''' + source + r'''
goToAssistant = (intent, text, files) => navigated.push({intent,text,files});
requestNavigate('quote', REQUIREMENT);
assert.equal(shown.includes('projectModal'), false, '普通报价不应弹出信息确认框');
assert.equal(navigated.length, 1, '一次点击直接进入报价');
assert.equal(navigated[0].text, REQUIREMENT, '原始客户需求必须保留');
''' + extra
        script = script.replace('REQUIREMENT', json.dumps(requirement, ensure_ascii=False))
        node = shutil.which('node')
        self.assertIsNotNone(node, '需要 Node 执行真实首页函数')
        result = subprocess.run([node, '-e', script], text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_empty_new_quote_enters_without_modal(self):
        self.check_entry('')

    def test_customer_requirement_enters_without_modal(self):
        self.check_entry('茅台酒盒，客户是贵州茅台。成品内尺寸：200mm×150mm×80mm。预计首批定1000个。')

    def test_new_identity_attachments_industry_and_double_click(self):
        self.check_entry('', r"""
assert.equal(storage.has('cpq:openSession'), false);
assert.equal(storage.has('cpq:customer'), false);
assert.equal(storage.get('cpq:industry'), 'packaging');
assert.match(storage.get('cpq:projectCode'), /^BJD/);
assert.deepEqual(navigated[0].files, ['需求.pdf']);
requestNavigate('quote', '重复点击');
assert.equal(navigated.length, 1, '双击不能重复进入');
quoteNavigating = false;
homeParsing = true;
assert.throws(() => requestNavigate('quote', ''), /附件保存中/);
assert.equal(navigated.length, 1);
homeParsing = false;
quoteUploadError = '附件上传失败';
assert.throws(() => requestNavigate('quote', ''), /附件上传失败/);
assert.equal(navigated.length, 1);
""")

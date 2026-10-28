import pathlib
import re
import shutil
import subprocess
import unittest

from tests.test_home_fuxi_quote_tech_prefill_red import Tags


ROOT = pathlib.Path(__file__).resolve().parents[1]


class HomeBusinessAnalytics(unittest.TestCase):
    def test_visible_tabs_include_analytics(self):
        parser = Tags()
        parser.feed((ROOT / '报价首页.html').read_text())
        self.assertEqual(parser.labels, ['报价智能体', '技术工艺智能体', '经营分析智能体'])

    def test_iframe_and_send_integration(self):
        home = (ROOT / '报价首页.html').read_text()
        for token in ('id="analyticsPanel"', 'id="analyticsFrame"',
                      'title="经营分析工作台"', 'home-analytics.js',
                      "if (currentMode === 'analytics')", 'HomeAnalytics.open(text)'):
            with self.subTest(token=token):
                self.assertTrue(token in home, '缺少经营分析接入：' + token)

    def test_url_and_embed_lifecycle(self):
        path = ROOT / 'home-analytics.js'
        self.assertTrue(path.exists(), '缺少可测试的 iframe 控制模块')
        node = shutil.which('node') or '/Users/sunjiongjun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
        script = r"""
const assert = require('node:assert/strict');
const {createController, buildUrl} = require(process.argv[1]);
const prompt = '分析收入 & 毛利\n客户 A+B #利润? "中文"';
const url = new URL(buildUrl(prompt));
assert.equal(url.origin, 'http://pdt-dev.eimos.com');
assert.equal(url.pathname, '/api/ontology-agentic-analytics');
assert.equal(url.searchParams.get('prompt'), prompt);
assert.equal(url.searchParams.get('theme'), 'light');
assert.equal(url.searchParams.get('layout'), 'single');
const el = () => ({hidden:false,textContent:'',value:'',src:'',href:'',
  classList:{toggle(){}},focus(){this.focused=true;},
  addEventListener(name, cb){this[name]=cb;}});
const refs = Object.fromEntries(['panel','frame','status','external','prompt','edit','retry',
  'copy','page','greeting','composer','list','send','input','nav','tabs','resume','body'].map(k=>[k,el()]));
const bodyClasses = new Set();
refs.body.classList.toggle = (name,on)=>on ? bodyClasses.add(name) : bodyClasses.delete(name);
let frameLoads = 0, frameSrc = '';
Object.defineProperty(refs.frame, 'src', {
  get(){return frameSrc;}, set(value){frameSrc=value;frameLoads++;}
});
refs.send.setAttribute = function(name,value){this[name]=value;};
let timeout;
const app = createController(refs, {setTimeout(cb){timeout=cb;return 1;},clearTimeout(){}});
app.setMode('analytics');
assert.equal(refs.send.textContent, '', '分析模式也不显示单独按钮文案');
assert.match(refs.send.innerHTML, /ti-arrow-up/);
assert.equal(refs.send['aria-label'], '发送需求');
assert.equal(refs.frame.src, '', '点击 tab 不请求外部服务');
assert.equal(app.open('  '), false);
assert.equal(refs.frame.src, '');
assert.equal(app.open(prompt), true);
assert.equal(new URL(refs.frame.src).searchParams.get('prompt'), prompt);
assert.equal(refs.panel.hidden, false);
assert.equal(refs.nav.hidden, true, '全屏隐藏宿主导航');
assert.equal(refs.tabs.hidden, true, '全屏隐藏宿主 tabs');
assert.equal(bodyClasses.has('analytics-fullscreen'), true);
assert.equal(refs.composer.hidden, true);
assert.equal(refs.list.hidden, true);
assert.equal(refs.prompt.textContent, prompt);
assert.equal(refs.external.href, refs.frame.src);
timeout();
assert.match(refs.status.textContent, /较久|超时/);
refs.frame.load();
assert.equal(refs.status.hidden, true, '加载事件后收起提示');
refs.edit.click();
assert.equal(refs.input.value, prompt);
assert.equal(refs.composer.hidden, false);
assert.equal(refs.panel.hidden, true);
assert.equal(refs.input.focused, true);
assert.equal(refs.nav.hidden, false);
assert.equal(refs.tabs.hidden, false);
assert.equal(bodyClasses.has('analytics-fullscreen'), false);
assert.equal(refs.resume.hidden, false);
const previousSrc = refs.frame.src;
const previousLoads = frameLoads;
refs.resume.click();
assert.equal(refs.panel.hidden, false);
assert.equal(refs.frame.src, previousSrc, '继续上次分析不更换 URL');
assert.equal(frameLoads, previousLoads, '继续上次分析不重新赋值 src');
assert.equal(refs.status.hidden, true, '继续分析不进入重新加载状态');
app.open(prompt);
app.setMode('quote');
assert.equal(refs.list.hidden, true, '报价列表统一隐藏');
assert.match(refs.send.innerHTML, /ti-arrow-up/);
assert.equal(refs.send['aria-label'], '发送需求');
app.setMode('tech');
assert.equal(refs.list.hidden, true, '技术工艺列表统一隐藏');
assert.match(refs.send.innerHTML, /ti-arrow-up/);
assert.equal(refs.composer.hidden, false);
assert.equal(refs.panel.hidden, true);
app.setMode('analytics');
assert.equal(refs.panel.hidden, false);
assert.equal(refs.composer.hidden, true);
refs.retry.click();
assert.match(refs.status.textContent, /加载/);
assert.equal(new URL(refs.frame.src).searchParams.get('prompt'), prompt);
"""
        result = subprocess.run([node, '-e', script, str(path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_full_viewport_layout_and_return_entry(self):
        home = (ROOT / '报价首页.html').read_text()
        self.assertTrue(bool(re.search(r'\.analytics-panel\s*\{[^}]*position:\s*fixed;[^}]*inset:\s*0;', home)), '分析视图必须覆盖视口')
        self.assertTrue(bool(re.search(r'\.analytics-frame\s*\{[^}]*flex:\s*1[^}]*min-height:\s*0', home)), 'iframe 必须填满剩余高度且允许窄屏收缩')
        self.assertIn('返回首页', home)
        self.assertIn('id="analyticsResume"', home)
        self.assertIn('继续上次分析', home)

    def test_real_submit_routes_analytics_without_project_or_attachment_flow(self):
        home = (ROOT / '报价首页.html').read_text()
        match = re.search(r"async function submitRequirement\(\)\s*\{[\s\S]*?(?=\n    if \(sendBtn\))", home)
        self.assertIsNotNone(match)
        node = shutil.which('node') or '/Users/sunjiongjun/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'
        script = r"""
const assert = require('node:assert/strict');
let received;
const currentMode = 'analytics', textarea = {value: '分析收入 & 利润\n按月对比'};
const HomeAnalytics = {open(text){received=text;}};
function requestNavigate(){throw new Error('不应进入项目流程');}
const homeParsing = true; // Other assistant attachments must not block text analysis.
SUBMIT
submitRequirement().then(()=>assert.equal(received, textarea.value)).catch(e=>{console.error(e);process.exit(1);});
""".replace('SUBMIT', match.group())
        result = subprocess.run([node, '-e', script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()

"""显式运行：CPQ_BROWSER_ACCEPTANCE=1 ... -m unittest tests.test_quote_direct_browser_acceptance。

真实Chrome、真实页面和JS、真实鉴权路由/业务命令，存储用同一受控事务夹具。
不绑定HTTP端口、不启动业务服务、不连接线上。仅替换页面boot为已建测试卡的登录态，
CDP拦截请求送进真实Handler；业务步骤由原页面按钮触发。
"""
import asyncio
import base64
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
import zipfile

from tests import test_quote_direct_six_steps_red as contracts

ROOT=Path(__file__).resolve().parents[1]
CHROME=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')


class CDP:
    def __init__(self, socket):
        self.socket=socket;self.counter=0;self.waiting={};self.events=[];self.on_request=None;self.tasks=set()

    async def call(self,method,params=None,session=None):
        self.counter+=1;identity=self.counter
        future=asyncio.get_running_loop().create_future();self.waiting[identity]=future
        message={'id':identity,'method':method,'params':params or {}}
        if session:message['sessionId']=session
        await self.socket.send(json.dumps(message))
        result=await asyncio.wait_for(future,30)
        if 'error' in result:raise AssertionError(result['error'])
        return result.get('result',{})

    async def receive(self):
        async for raw in self.socket:
            event=json.loads(raw)
            if event.get('id') in self.waiting:
                self.waiting.pop(event['id']).set_result(event)
            else:
                self.events.append(event)
                if event.get('method')=='Fetch.requestPaused' and self.on_request:
                    task=asyncio.create_task(self.on_request(event));self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)


@unittest.skipUnless(os.environ.get('CPQ_BROWSER_ACCEPTANCE')=='1' and CHROME.exists(),
                     '须显式开启Chrome浏览器验收')
class BrowserAcceptance(unittest.TestCase):
    customer_only=False
    missing_cost=False
    archive_after_first=False
    evidence_name=''
    def test_prompt_then_six_original_next_clicks_and_word(self):
        fixture=contracts.WorkflowTests('test_real_workflow_six_steps_no_handoff_and_persistent_read')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        self.fixture=fixture;self.requests=[];self.download=None
        if self.missing_cost:
            catalog=fixture.customer_catalog();catalog['costs']=[]
            override=patch('cpq_customer_quote.load_catalog',return_value=catalog)
            override.start();self.addCleanup(override.stop)
        self.artifacts=Path(os.environ.get('CPQ_ACCEPTANCE_ARTIFACT_DIR') or tempfile.mkdtemp(prefix='cpq-direct-evidence-'))
        self.artifacts=self.artifacts/self.evidence_name
        self.artifacts.mkdir(parents=True,exist_ok=True)
        with patch('cpq_auth._backend','test'),patch('cpq_auth.whoami',return_value=fixture.user),\
             patch('cpq_wf.get_card',side_effect=lambda sid:fixture.card),\
             patch('cpq_wf.card_steps',side_effect=lambda cid:[dict(step_no=n,step_name=name,
                   status=fixture.statuses[n],role_code=role) for n,name,role in fixture.wf.QUOTE_STEPS]):
            asyncio.run(self.run_browser())

    def handle(self,path,method,post_data):
        import cpq_suite_server
        handler=object.__new__(cpq_suite_server.Handler)
        handler.path=path;handler.command=method;handler._token=lambda:'test-token'
        handler._read_json=lambda:json.loads(post_data or '{}')
        output={'status':200,'headers':{},'body':b''}
        def send_json(status,data):
            output.update(status=status,body=json.dumps(data,ensure_ascii=False).encode())
            output['headers']['Content-Type']='application/json'
        handler._send_json=send_json
        handler.send_response=lambda status:output.update(status=status)
        handler.send_header=lambda key,value:output['headers'].update({key:value})
        handler.end_headers=Mock();handler.wfile=io.BytesIO()
        self.assertTrue(handler._dispatch_wf())
        if handler.wfile.getvalue():
            output['body']=handler.wfile.getvalue();self.download=output['body']
        return output

    async def run_browser(self):
        import websockets
        profile=Path(tempfile.mkdtemp(prefix='cpq-direct-chrome-'))
        downloads=profile/'downloads';downloads.mkdir()
        process=subprocess.Popen([str(CHROME),'--headless=new','--disable-gpu','--no-first-run',
            '--no-default-browser-check','--remote-debugging-port=0','--user-data-dir='+str(profile),'about:blank'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        def close_browser():
            if process.poll() is None:process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
        self.addCleanup(close_browser)
        port_file=profile/'DevToolsActivePort'
        for _ in range(200):
            if port_file.exists():break
            self.assertIsNone(process.poll(),'Chrome启动失败')
            await asyncio.sleep(.05)
        self.assertTrue(port_file.exists(),'Chrome调试地址未就绪')
        port,ws_path=port_file.read_text().splitlines()[:2]
        async with websockets.connect('ws://127.0.0.1:'+port+ws_path,max_size=16*1024*1024) as socket:
            cdp=CDP(socket);reader=asyncio.create_task(cdp.receive())
            try:
                browser=await cdp.call('Browser.getVersion')
                target=(await cdp.call('Target.createTarget',{'url':'about:blank'}))['targetId']
                session=(await cdp.call('Target.attachToTarget',{'targetId':target,'flatten':True}))['sessionId']
                await cdp.call('Page.enable',session=session)
                await cdp.call('Runtime.enable',session=session)
                await cdp.call('Fetch.enable',{'patterns':[{'urlPattern':'*'}]},session)
                await cdp.call('Emulation.setDeviceMetricsOverride',{'width':1600,'height':1100,'deviceScaleFactor':1,'mobile':False},session)
                await cdp.call('Browser.setDownloadBehavior',{'behavior':'allow','downloadPath':str(downloads),'eventsEnabled':True})
                html=(ROOT/'确认需求解析结果.html').read_text()
                self.assertIn('    boot();\n  </script>',html)
                html=html.replace('    boot();\n  </script>','    // 隔离验收使用已建测试卡，不启动Agent或新建线上会话。\n  </script>')
                html=re.sub(r'<script\b[^>]*\bsrc=[^>]*>[\s\S]*?</script>','',html)
                html=re.sub(r'<link\b[^>]*>','',html)
                html=html.replace('</body>','<script>'+(ROOT/'quote-direct.js').read_text()+'</script></body>')
                async def on_request(event):
                    params=event['params'];request=params['request'];url=urlsplit(request['url'])
                    try:
                        if url.path=='/acceptance.html':
                            result={'status':200,'headers':{'Content-Type':'text/html; charset=utf-8'},'body':html.encode()}
                        elif url.path.startswith('/wf/'):
                            self.requests.append((url.path,request['method'],request.get('postData')))
                            result=self.handle(url.path+('?' + url.query if url.query else ''),request['method'],request.get('postData'))
                        else:
                            result={'status':404,'headers':{},'body':b'not part of isolated acceptance'}
                    except Exception as exc:
                        result={'status':500,'headers':{'Content-Type':'application/json'},'body':json.dumps({'error':str(exc)}).encode()}
                    await cdp.call('Fetch.fulfillRequest',{'requestId':params['requestId'],
                        'responseCode':result['status'],'responseHeaders':[{'name':k,'value':v} for k,v in result['headers'].items()],
                        'body':base64.b64encode(result['body']).decode()},event['sessionId'])
                cdp.on_request=on_request
                await cdp.call('Page.navigate',{'url':'http://cpq-acceptance.test/acceptance.html'},session)
                async def evaluate(expression):
                    response=await cdp.call('Runtime.evaluate',{'expression':expression,'awaitPromise':True,'returnByValue':True},session)
                    self.assertNotIn('exceptionDetails',response,str(response.get('exceptionDetails')))
                    return response.get('result',{}).get('value')
                async def wait_for(expression):
                    for _ in range(200):
                        if await evaluate(expression):return
                        await asyncio.sleep(.025)
                    self.fail('页面条件未满足：'+expression)
                await wait_for("!!window.CpqDirectHost && !!window.CpqDirect")
                bootstrap="""
window.cpqAuth={user:()=>({user_id:'100',role_code:'sales_mgr',role_name:'销售经理'}),
api:async(path,options={})=>{const r=await fetch(path,{method:options.method||'GET',headers:{'Content-Type':'application/json'},
body:options.body?JSON.stringify(options.body):undefined});const d=await r.json();if(!r.ok)throw Error(d.error);return d;}};
window.cpqAuthFetch=(...args)=>fetch(...args);
currentSessionId='S'; WF.loaded=true; WF.canEdit=true; GATE_BLOCKED=false;busy=false;
document.getElementById('projectName').textContent='茅台酒盒六步验收';
true;
"""
                import cpq_industries
                bootstrap += '\nINDUSTRIES='+json.dumps(cpq_industries.all_industries(),ensure_ascii=False)+"; CURRENT_INDUSTRY='battery'; renderIndustrySelect(); true;"
                await evaluate(bootstrap)
                inputs=self.fixture.inputs_for_prompt()
                inputs['2']['cost_source']='仅本地流程验收假设成本，非真实成本单'
                prompt='茅台酒盒普通报价六步演示验收。成本仅为演示假设。\n【直接报价参数】'+json.dumps(inputs,ensure_ascii=False)+'【/直接报价参数】'
                if self.customer_only:
                    prompt=self.fixture.natural_prompt()
                    self.assertNotRegex(prompt,'成本|毛利|税率|JSON|直接报价参数')
                await evaluate("document.getElementById('chatInput').focus()")
                await cdp.call('Input.insertText',{'text':prompt},session)
                await evaluate("document.getElementById('chatSend').click()")
                await wait_for("!busy && !!document.getElementById('directQuotePanel')")
                self.assertEqual(1,self.fixture.q.state_for('S')['next_step'])
                self.assertEqual('1',await evaluate(r"document.querySelector('#directQuotePanel h2').textContent.match(/第 (\d) 步/)[1]"))
                async def screenshot(name):
                    result=await cdp.call('Page.captureScreenshot',{'format':'png'},session)
                    (self.artifacts/name).write_bytes(base64.b64decode(result['data']))
                await screenshot('step1.png')
                if self.archive_after_first:
                    await evaluate("document.getElementById('btnNext').click()")
                    await wait_for('!busy')
                    self.fixture.card['overall_status']='archived'
                    await evaluate('wfRefresh(true)')
                    self.assertTrue(await evaluate("document.getElementById('btnNext').disabled"))
                    self.assertTrue(await evaluate("document.querySelector('[name=unit_cost]').readOnly"))
                    await evaluate('confirmStep()')
                    self.assertEqual('pending',self.fixture.statuses[2])
                    await screenshot('archived-readonly.png')
                    print('归档只读浏览器验收证据：'+str(self.artifacts))
                    return
                if self.missing_cost:
                    await evaluate("document.getElementById('btnNext').click()")
                    await wait_for('!busy')
                    self.assertEqual('done',self.fixture.statuses[1])
                    self.assertEqual('pending',self.fixture.statuses[2])
                    self.assertTrue(await evaluate("document.getElementById('btnNext').disabled"))
                    self.assertIn('系统缺少该成品当前有效的未税成本记录',await evaluate("document.getElementById('directQuotePanel').textContent"))
                    self.assertEqual(0,await evaluate("document.querySelectorAll('#directQuotePanel [name=unit_cost]').length"))
                    self.assertIsNone(self.fixture.q.state_for('S')['quote'])
                    await screenshot('step2-blocked.png')
                    await cdp.call('Page.reload',session=session)
                    await wait_for('!!window.CpqDirectHost && !!window.CpqDirect')
                    await evaluate(bootstrap);await evaluate('wfRefresh(true)')
                    self.assertTrue(await evaluate("document.getElementById('btnNext').disabled"))
                    (self.artifacts/'result.json').write_text(json.dumps({'browser':browser['product'],
                        'prompt':prompt,'step1':'done','step2':'pending','pricing_gaps':self.fixture.q.state_for('S')['pricing_gaps'],
                        'quote':None,'storage':'controlled transactional fixture'},ensure_ascii=False,indent=2))
                    print('缺价浏览器验收证据：'+str(self.artifacts))
                    return
                for step in range(1,7):
                    self.assertFalse(await evaluate("document.getElementById('btnNext').disabled"))
                    if step==2:
                        self.assertEqual('10.125',await evaluate("document.querySelector('[name=unit_cost]').value"))
                        self.assertTrue(await evaluate("document.querySelector('[name=confirmed]').checked"))
                    await evaluate("document.getElementById('btnNext').click()")
                    await wait_for('!busy')
                    self.assertEqual(step+1,self.fixture.q.state_for('S')['next_step'])
                self.assertEqual({n:'done' for n in range(1,7)},self.fixture.statuses)
                self.assertEqual('completed',self.fixture.card['overall_status'])
                self.assertIn('17515.00',await evaluate("document.getElementById('directQuotePanel').textContent"))
                self.assertTrue(await evaluate("document.getElementById('btnNext').disabled"))
                await screenshot('step6.png')
                await evaluate("document.getElementById('btnExport').click()")
                for _ in range(200):
                    if any(e.get('method')=='Browser.downloadProgress' and e.get('params',{}).get('state')=='completed' for e in cdp.events):break
                    await asyncio.sleep(.025)
                self.assertIsNotNone(self.download)
                self.assertTrue(any(e.get('method')=='Browser.downloadProgress' and e.get('params',{}).get('state')=='completed' for e in cdp.events))
                with zipfile.ZipFile(io.BytesIO(self.download)) as word:
                    xml=word.read('word/document.xml').decode()
                self.assertIn('17515.00',xml);self.assertIn('演示报价',xml)
                self.assertNotIn('10.125',xml);self.assertNotIn('假设成本',xml)
                (self.artifacts/'quote.docx').write_bytes(self.download)
                await cdp.call('Page.reload',session=session)
                await wait_for('!!window.CpqDirectHost && !!window.CpqDirect')
                await evaluate(bootstrap)
                await evaluate('wfRefresh(true)')
                self.assertIn('六步报价已完成',await evaluate("document.getElementById('directQuotePanel').textContent"))
                self.assertEqual(7,len(self.fixture.events))
                commands=[json.loads(body) for path,method,body in self.requests if method=='POST']
                self.assertEqual(['prepare']+['complete']*6,[item['action'] for item in commands])
                self.assertFalse(any('/task' in path or '/tech' in path for path,method,body in self.requests))
                result={'browser':browser['product'],'six_steps':'done','card_status':self.fixture.card['overall_status'],
                        'quote':self.fixture.q.state_for('S')['quote'],'requests':commands,'prompt':prompt,
                        'word_download':'completed','refresh':'completed_restored','storage':'controlled transactional fixture'}
                (self.artifacts/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
                print('浏览器验收证据：'+str(self.artifacts))
            finally:
                try:await cdp.call('Browser.close')
                except Exception:pass
                reader.cancel()
                await asyncio.gather(reader,return_exceptions=True)
        process.wait(timeout=10)


class CustomerBrowserAcceptance(BrowserAcceptance):
    customer_only=True
    evidence_name='customer-ready'


class CustomerMissingCostBrowserAcceptance(CustomerBrowserAcceptance):
    missing_cost=True
    evidence_name='customer-missing-cost'


class CustomerArchivedBrowserAcceptance(CustomerBrowserAcceptance):
    archive_after_first=True
    evidence_name='customer-archived'

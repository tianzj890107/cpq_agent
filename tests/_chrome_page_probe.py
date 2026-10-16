"""真实时钟读取 Chrome 测试页面；避免虚拟时钟跳过 dialog close/render 事件。"""
import asyncio
import json
import subprocess
import time
import urllib.request
from pathlib import Path


def page_probe(chrome, page, profile, expression, *, timeout=12):
    import websockets
    proc=subprocess.Popen([str(chrome),'--headless','--disable-gpu','--no-first-run',
        '--disable-background-networking','--remote-debugging-port=0',
        '--window-size=640,900','--user-data-dir='+str(profile),'about:blank'],
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    async def run(ws_url):
        async with websockets.connect(ws_url,max_size=4*1024*1024) as ws:
            sequence=0
            async def call(method,params):
                nonlocal sequence
                sequence+=1;identifier=sequence
                await ws.send(json.dumps({'id':identifier,'method':method,'params':params}))
                while True:
                    event=json.loads(await ws.recv())
                    if event.get('id')==identifier:
                        if event.get('error'):raise AssertionError(event['error'])
                        return event.get('result') or {}
            await call('Page.navigate',{'url':page.as_uri()})
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                response=await call('Runtime.evaluate',{'expression':expression,'returnByValue':True})
                value=(response.get('result') or {}).get('value')
                if value:return value
                await asyncio.sleep(.05)
            raise AssertionError('Chrome 页面未在时限内输出测试结果')
    try:
        port_file=Path(profile)/'DevToolsActivePort'
        deadline=time.monotonic()+timeout
        while not port_file.exists():
            if proc.poll() is not None:raise AssertionError('Chrome 启动失败')
            if time.monotonic()>=deadline:raise AssertionError('Chrome 调试端口未就绪')
            time.sleep(.05)
        port=int(port_file.read_text().splitlines()[0])
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list',timeout=3) as response:
            tabs=json.load(response)
        ws_url=next(t['webSocketDebuggerUrl'] for t in tabs if t.get('type')=='page')
        return asyncio.run(asyncio.wait_for(run(ws_url),timeout=timeout+2))
    finally:
        proc.terminate()
        try:proc.wait(timeout=5)
        except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)

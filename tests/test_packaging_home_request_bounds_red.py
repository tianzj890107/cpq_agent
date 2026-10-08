import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class HomeRequestBoundsTest(unittest.TestCase):
    def test_timeout_aborts_request_and_reports_error_not_empty_library(self):
        src = (ROOT / '报价首页.html').read_text()
        self.assertTrue('function homeBoundedRequest(' in src, '首页请求缺少超时边界')
        start = src.index('function homeBoundedRequest(')
        end = src.index('\n    }', start) + len('\n    }')
        script = src[start:end] + '''
        let signal;
        homeBoundedRequest(s => { signal=s; return new Promise(()=>{}); }, 10)
          .catch(e => console.log(e.code + ':' + signal.aborted));
        '''
        out = subprocess.run(['node', '-e', script], capture_output=True, text=True,
                             timeout=3, check=True)
        self.assertEqual('home-request-timeout:true', out.stdout.strip())

    def test_same_scope_requests_are_coalesced(self):
        src = (ROOT / '报价首页.html').read_text()
        self.assertTrue('TECH_SCOPE_REQUESTS' in src, '全部项目并发查询未合并')
        start = src.index('async function techLoadScope(')
        end = src.index('\n    }', start) + len('\n    }')
        script = '''
        const TECH_SCOPE_ROWS={},TECH_SCOPE_REQUESTS={};
        let SESSION_ERROR=null,currentMode='quote',calls=0;
        const window={cpqAuth:{api:async()=>{calls++;await new Promise(r=>setTimeout(r,10));return [];}}};
        const techRowOf=x=>x,homeBoundedRequest=fn=>fn({});
        ''' + src[start:end] + '''
        Promise.all([techLoadScope('all',false),techLoadScope('all',false)])
          .then(()=>console.log(calls));
        '''
        out = subprocess.run(['node', '-e', script], capture_output=True, text=True,
                             timeout=3, check=True)
        self.assertEqual('1', out.stdout.strip())

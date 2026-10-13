"""Red tests for out-of-order 2.1 packaging parts reads."""
from __future__ import annotations

import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
APP_JS = ROOT / "tech_app/frontend/app.js"


def run_node(source: str) -> str:
    result = subprocess.run(["node", "-e", source], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class LatestPackagingPartsReadTests(unittest.TestCase):
    def test_real_refresh_keeps_new_parts_when_entry_request_finishes_last(self):
        app = APP_JS.read_text(encoding="utf-8")
        names = ["beginPackagingPartsRead", "packagingPartsReadIsCurrent",
                 "packagingPartsItems", "packagingPartsQueryString",
                 "fetchPackagingParts", "fetchPackagingBusinessParts",
                 "refreshPackagingParts"]

        def extract(name: str) -> str:
            at = app.find("function " + name + "(")
            if at < 0:
                self.fail("缺少函数 " + name)
            if app[max(0, at - 6):at] == "async ":
                at -= 6
            brace = app.find("{", at)
            depth = 0
            for index in range(brace, len(app)):
                if app[index] == "{": depth += 1
                elif app[index] == "}":
                    depth -= 1
                    if depth == 0: return app[at:index + 1]
            self.fail("函数未闭合: " + name)

        functions = "\n".join(extract(name) for name in names)
        source = r"""
let currentProject='P1',currentIR={},API='',packagingPartsReadSequence=0;
let currentPackagingParts=null,currentPackagingBusinessParts=null,packagingPartsShown=[];
let packagingPartsPage={offset:0,limit:64,kind:'',role:'',outline_status:'',min_area_mm2:''};
let authEnabled=true,currentUser={role:'sales_manager'},packagingAutoBindAttempted=new Set();
const renders=[],oldResolvers=[];let requestNo=0;
const packagingBusinessPartRows=doc=>(doc&&doc.business_parts)||[];
const setLoadStage=()=>{};const status=()=>{};const loadPackagingRoleMap=async()=>null;
const refreshPackagingBomRoleUnboundNote=async()=>null;const ensurePackagingBusinessParts=async()=>null;
const packagingAutoBindCandidatesUrl=()=>'';
const renderTree=()=>renders.push({geometry:(currentPackagingParts?.items||[]).map(x=>x.part_code),
  business:packagingBusinessPartRows(currentPackagingBusinessParts).map(x=>x.business_part_code)});
function response(url,version){
  const business=url.includes('business-parts');
  const body=business?{business_parts:version==='new'?[{business_part_code:'BP01'}]:[]}:
    {items:version==='new'?[{part_code:'G1'}]:[],parts:[],stats:{part_total:version==='new'?1:0},total:version==='new'?1:0};
  return {ok:true,status:200,json:async()=>body};
}
const fetch=url=>{const n=requestNo++;return new Promise(resolve=>{
  if(n<2)oldResolvers.push(()=>resolve(response(String(url),'old')));
  else resolve(response(String(url),'new'));
});};
""" + "\n" + functions + r"""
(async()=>{
  const oldRead=refreshPackagingParts();
  const newRead=refreshPackagingParts();
  await newRead;
  oldResolvers.forEach(resolve=>resolve());
  await oldRead;
  console.log(JSON.stringify({geometry:currentPackagingParts.items.map(x=>x.part_code),
    business:packagingBusinessPartRows(currentPackagingBusinessParts).map(x=>x.business_part_code),
    shown:packagingPartsShown.map(x=>x.part_code),renders}));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
        result = __import__("json").loads(run_node(source))
        self.assertEqual(["G1"], result["geometry"])
        self.assertEqual(["BP01"], result["business"])
        self.assertEqual(["G1"], result["shown"])

    def test_late_old_read_cannot_replace_new_parsed_parts(self):
        app = APP_JS.read_text(encoding="utf-8")
        begin_at = app.find("function beginPackagingPartsRead(")
        current_at = app.find("function packagingPartsReadIsCurrent(")
        self.assertGreaterEqual(begin_at, 0, "缺少统一的包装零件读取序号")
        self.assertGreaterEqual(current_at, 0, "缺少最新读取判定")

        def extract(at: int) -> str:
            brace = app.find("{", at)
            depth = 0
            for index in range(brace, len(app)):
                if app[index] == "{":
                    depth += 1
                elif app[index] == "}":
                    depth -= 1
                    if depth == 0:
                        return app[at:index + 1]
            raise AssertionError("unterminated helper")

        begin_fn = extract(begin_at)
        current_fn = extract(current_at)
        source = """
let currentProject='P1'; let packagingPartsReadSequence=0;
""" + begin_fn + "\n" + current_fn + r"""
let displayed=[];
const older=beginPackagingPartsRead('P1');
const newer=beginPackagingPartsRead('P1');
function apply(ticket, rows) {
  if (!packagingPartsReadIsCurrent(ticket)) return false;
  displayed=rows; return true;
}
apply(newer, ['BP01','BP02']);
apply(older, []);
console.log(JSON.stringify({displayed,oldAccepted:apply(older,[])}));
"""
        import json
        result = json.loads(run_node(source))
        self.assertEqual(["BP01", "BP02"], result["displayed"])
        self.assertFalse(result["oldAccepted"])

    def test_refresh_guards_both_parallel_responses_and_final_commit(self):
        src = APP_JS.read_text(encoding="utf-8")
        start = src.index("async function refreshPackagingParts()")
        end = src.index("\nfunction packagingRoleUnboundReadProblemText(", start)
        body = src[start:end]
        self.assertIn("beginPackagingPartsRead(", body)
        self.assertGreaterEqual(body.count("isCurrentRead()"), 8,
                                "几何、业务和最终/补充写回都要验证读取序号")
        self.assertIn("fetchPackagingParts(readTicket)", body)
        self.assertIn("fetchPackagingBusinessParts(readTicket)", body)

    def test_geometry_fetch_does_not_clear_or_replace_rows_when_stale(self):
        src = APP_JS.read_text(encoding="utf-8")
        start = src.index("async function fetchPackagingParts(")
        end = src.index("\n// \"继续加载\"", start)
        body = src[start:end]
        self.assertIn("packagingPartsReadIsCurrent(ticket)", body)
        self.assertGreaterEqual(body.count("packagingPartsReadIsCurrent(ticket)"), 5,
                                "成功、网络失败和HTTP失败路径均须忽略过期响应")

    def test_project_switch_invalidates_prior_read(self):
        app = APP_JS.read_text(encoding="utf-8")
        begin_at = app.find("function beginPackagingPartsRead(")
        current_at = app.find("function packagingPartsReadIsCurrent(")
        self.assertGreaterEqual(begin_at, 0)
        self.assertGreaterEqual(current_at, 0)

        def extract(at: int) -> str:
            brace = app.find("{", at)
            depth = 0
            for index in range(brace, len(app)):
                if app[index] == "{": depth += 1
                elif app[index] == "}":
                    depth -= 1
                    if depth == 0: return app[at:index + 1]
            raise AssertionError("unterminated helper")

        source = "let currentProject='P1'; let packagingPartsReadSequence=0;\n"
        source += extract(begin_at) + "\n" + extract(current_at)
        source += "\nconst old=beginPackagingPartsRead('P1');currentProject='P2';"
        source += "console.log(String(packagingPartsReadIsCurrent(old)));"
        self.assertEqual("false", run_node(source))


if __name__ == "__main__":
    unittest.main()

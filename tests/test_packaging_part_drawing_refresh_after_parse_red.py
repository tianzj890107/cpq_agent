"""解析完成时更新 CAD 坐标，并以真实 SVG 验证零件图就地显示。"""
from __future__ import annotations

import json
import pathlib
import subprocess
import unittest

APP = pathlib.Path(__file__).resolve().parents[1] / "tech_app/frontend/app.js"


def function(name: str) -> str:
    source = APP.read_text(encoding="utf-8")
    start = source.index("function " + name + "(")
    if source[start - 6:start] == "async ":
        start -= 6
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError("函数未闭合：" + name)


PREAMBLE = r"""
let currentProject='P1',currentDrawingEntry='drawing_flow',currentDrawingFlowState=null;
let currentPackagingCadPlan={geometry_evidence:{components:[]},cad_scene:{entities:[]}};
let currentPackagingCadPlanBox=null,currentPackagingBusinessPartCode='BP1';
let pendingPackagingShapePartCode='',currentPackagingUnassignedCandidateId='';
let packagingCadPlanReadSequence=0;
let currentPackagingBusinessParts={business_parts:[{business_part_code:'BP1',geometry_binding:{entity_ids:['E1'],exact_entities:true}}]};
const host={innerHTML:'',querySelectorAll:()=>[],querySelector:()=>null,setAttribute:()=>{}};
const packagingCadPlanViewer=()=>host, packagingCadPlanApplies=()=>currentDrawingEntry==='drawing_flow';
const packagingCadSceneEndpoint=()=>'/projects/'+currentProject+'/packaging-geometry';
const PACKAGING_CAD_PLAN_LABEL='CAD 平面图',PACKAGING_CAD_RULE_LEGEND='';
const PACKAGING_CAD_PLAN_NO_COORDS='没有坐标';
const PACKAGING_CAD_LAYER_COLORS={cut:'#dc2626',crease:'#16a34a',unknown:'#8b949e'};
const esc=x=>String(x??''),packagingCadDisplayBox=x=>x.bbox;
const packagingCadPlanReadProblemText=p=>'读取失败 '+p.status;
const packagingCadPlanEmptyText=()=> '还没有图元';
const packagingBusinessPartRows=doc=>doc?.business_parts||[];
const statuses=[],selectedDraws=[];const status=x=>statuses.push(x);
function openPackagingBusinessPart(code){
  selectedDraws.push(code);
  const row=packagingBusinessPartRows(currentPackagingBusinessParts).find(row=>row.business_part_code===code);
  if(!row)return null;
  currentPackagingBusinessPartCode=code;
  const svg=packagingPartSceneSvg(row.geometry_binding,currentPackagingCadPlan,{includeAnnotations:true});
  host.innerHTML=svg||'正在读取这一件的形状…';return row;
}
const openPackagingUnassignedCandidate=()=>null;
const scene={geometry_evidence:{components:[]},business_parts:[],cad_scene:{entities:[
  {cad_entity_id:'E1',kind:'polyline',points:[[0,0],[30,0],[30,20],[0,20]],bbox:[0,0,30,20],closed:true,role:'cut',layer:'刀线'},
  {cad_entity_id:'E2',kind:'polyline',points:[[50,0],[60,0]],bbox:[50,0,60,1],role:'crease',layer:'压线'}
]}};
const empty={geometry_evidence:{components:[]},business_parts:[],cad_scene:{entities:[]}};
const response=(doc,status=200)=>({ok:status===200,status,json:async()=>doc});
let fetch=async()=>response(scene);
let refreshPackagingParts=async()=>({items:[{part_code:'G1'}]});
"""


def run_js(scenario: str) -> dict:
    names = ["packagingCadPlanRange", "packagingCadPlanViewBox", "packagingCadSceneEntitySvg",
             "packagingPartSceneSvg", "repaintSelectedPackagingShape", "renderPackagingCadScene",
             "renderPackagingCadPlan", "loadPackagingCadPlan", "packagingPartsRefreshLine",
             "refreshPackagingPartsAfterDrawingFlow"]
    source = PREAMBLE + "\n" + "\n".join(function(name) for name in names)
    source += "\n(async()=>{\n" + scenario + "\n})().catch(e=>{console.error(e);process.exitCode=1;});"
    result = subprocess.run(["node", "-e", source], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class PackagingPartDrawingRefreshTests(unittest.TestCase):
    def test_parse_refreshes_empty_scene_and_draws_selected_part_without_navigation(self):
        result = run_js(r"""
let sceneReads=0;fetch=async()=>{sceneReads++;return response(scene);};
await refreshPackagingPartsAfterDrawingFlow({status:'completed'});
console.log(JSON.stringify({sceneReads,html:host.innerHTML,selected:currentPackagingBusinessPartCode,selectedDraws}));
""")
        self.assertEqual(1, result["sceneReads"], "解析完成必须重新读取场景，而不只刷新清单")
        self.assertIn('<svg', result["html"])
        self.assertIn('data-cad-entity-id="E1"', result["html"])
        self.assertNotIn('data-cad-entity-id="E2"', result["html"])
        self.assertEqual('BP1', result["selected"])
        self.assertEqual(['BP1'], result['selectedDraws'], '场景已按新归属绘制时不重复打开零件')

    def test_scene_before_business_list_repaints_using_latest_selected_binding(self):
        result = run_js(r"""
let finishList;currentPackagingBusinessParts={business_parts:[]};
refreshPackagingParts=()=>new Promise(resolve=>{finishList=()=>{
  currentPackagingBusinessParts={business_parts:[{business_part_code:'BP1',geometry_binding:{entity_ids:['E1'],exact_entities:true}}]};
  resolve({items:[{part_code:'G1'}]});};});
const done=refreshPackagingPartsAfterDrawingFlow({status:'completed'});
await new Promise(resolve=>setImmediate(resolve));finishList();await done;
console.log(JSON.stringify({html:host.innerHTML,selected:currentPackagingBusinessPartCode}));
""")
        self.assertIn('data-cad-entity-id="E1"', result["html"])
        self.assertNotIn('data-cad-entity-id="E2"', result["html"], "不能停留在整图")

    def test_latest_scene_survives_older_empty_404_failure_and_slow_json(self):
        for old in ('empty', '404', '500', 'network', 'slow_json'):
            with self.subTest(old=old):
                result = run_js(r"""
const mode=MODE;let release;let calls=0;
fetch=()=>{if(calls++>0)return Promise.resolve(response(scene));
  if(mode==='slow_json')return Promise.resolve({ok:true,status:200,json:()=>new Promise(resolve=>{release=()=>resolve(empty);})});
  return new Promise((resolve,reject)=>{release=()=> mode==='network'?reject(new Error('offline')):
    resolve(response(empty,mode==='404'?404:mode==='500'?500:200));});};
const first=loadPackagingCadPlan();await new Promise(resolve=>setImmediate(resolve));
await loadPackagingCadPlan();release();await first;
console.log(JSON.stringify({html:host.innerHTML,ids:(currentPackagingCadPlan.cad_scene?.entities||[]).map(x=>x.cad_entity_id)}));
""".replace('MODE', json.dumps(old)))
                self.assertIn('data-cad-entity-id="E1"', result["html"])
                self.assertEqual(['E1', 'E2'], result["ids"])

    def test_previous_scene_is_invalidated_and_pending_selection_repaints(self):
        result = run_js(r"""
currentPackagingCadPlan=scene;let release;
fetch=()=>new Promise(resolve=>{release=()=>resolve(response(scene));});
const read=loadPackagingCadPlan();const cleared=currentPackagingCadPlan===null;
pendingPackagingShapePartCode='BP1';release();await read;
console.log(JSON.stringify({cleared,pending:pendingPackagingShapePartCode,html:host.innerHTML}));
""")
        self.assertTrue(result['cleared'], '读取新图纸时不能继续使用上一份坐标')
        self.assertEqual('', result['pending'])
        self.assertIn('data-cad-entity-id="E1"', result['html'])

    def test_project_switch_discards_old_error_and_status(self):
        result = run_js(r"""
let fail;fetch=()=>new Promise((resolve,reject)=>{fail=()=>reject(new Error('offline'));});
const read=refreshPackagingPartsAfterDrawingFlow({status:'completed'});
await new Promise(resolve=>setImmediate(resolve));currentProject='P2';
host.innerHTML='P2 已显示图纸';if(fail)fail();await read;
console.log(JSON.stringify({html:host.innerHTML,statuses}));
""")
        self.assertEqual('P2 已显示图纸', result['html'])
        self.assertEqual([], result['statuses'])

    def test_current_failure_is_visible_and_retry_draws_part(self):
        result = run_js(r"""
fetch=async()=>response({},503);await loadPackagingCadPlan();const failure=host.innerHTML;
fetch=async()=>response(scene);await refreshPackagingPartsAfterDrawingFlow({status:'completed'});
console.log(JSON.stringify({failure,html:host.innerHTML}));
""")
        self.assertIn('读取失败 503', result['failure'])
        self.assertIn('data-cad-entity-id="E1"', result['html'])

    def test_no_selection_displays_complete_drawing(self):
        result = run_js(r"""
currentPackagingBusinessPartCode='';await refreshPackagingPartsAfterDrawingFlow({status:'completed'});
console.log(JSON.stringify({html:host.innerHTML,selected:currentPackagingBusinessPartCode}));
""")
        self.assertIn('data-cad-entity-id="E1"', result['html'])
        self.assertIn('data-cad-entity-id="E2"', result['html'])
        self.assertEqual('', result['selected'])

    def test_parse_refresh_keeps_current_scene_error_visible(self):
        result = run_js(r"""
fetch=async()=>response({},503);await refreshPackagingPartsAfterDrawingFlow({status:'completed'});
console.log(JSON.stringify({html:host.innerHTML,selectedDraws}));
""")
        self.assertIn('读取失败 503', result['html'])
        self.assertEqual([], result['selectedDraws'], '场景读取失败不能重开零件覆盖错误')

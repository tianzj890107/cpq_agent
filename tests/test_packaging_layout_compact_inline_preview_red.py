from pathlib import Path
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
JS=(ROOT/'tech_app/frontend/assembly-integration.js').read_text()
WORK=(ROOT/'tech_app/frontend/packaging-stage-workspace.js').read_text()
CSS=(ROOT/'tech_app/frontend/assembly-integration.css').read_text()

class LayoutUiTests(unittest.TestCase):
    def test_allowances_and_save_share_toolbar(self):
        self.assertTrue('ai-cut-allowance-bar' in WORK)
        self.assertTrue('.ai-cut-allowance-bar' in CSS)

    def test_three_controls_share_small_action_bar(self):
        self.assertTrue('ai-layout-controls' in JS)
        self.assertTrue('<summary class="inline-action"' in JS)
        self.assertTrue('ai-layout-part-title' in JS)

    def test_layout_preview_uses_inline_read_not_navigation(self):
        self.assertTrue('data-ai-layout-preview=' in JS)
        self.assertTrue('async function aiToggleLayoutPreview' in JS)
        body=JS.split('async function aiToggleLayoutPreview',1)[1].split('function aiBind',1)[0]
        self.assertTrue('/requirement/packaging-geometry' in body)
        self.assertFalse('requestNavigate' in body)

    def test_real_cad_svg_preserves_dimensions_colours_and_escapes_text(self):
        self.assertTrue('function aiLayoutCadSvg' in JS)
        fn='function aiLayoutCadSvg'+JS.split('function aiLayoutCadSvg',1)[1].split('async function aiToggleLayoutPreview',1)[0]
        script="const esc=v=>String(v).replace(/</g,'&lt;').replace(/>/g,'&gt;');"+fn+r'''
const svg=aiLayoutCadSvg({cad_scene:{entities:[{kind:'polyline',role:'cut',bbox:[0,0,10,5],points:[[0,0],[10,5]]},{kind:'text',role:'v_groove',bbox:[0,0,10,5],x:2,y:3,text:'10.00 <script>',height:1}]}});
if(!svg.includes('#dc2626')||!svg.includes('#ec4899')||!svg.includes('10.00 &lt;script&gt;')||!svg.includes('10,-5'))throw Error('CAD evidence not preserved');
if(aiLayoutCadSvg({cad_scene:{entities:[]}})!=='')throw Error('fake empty preview');
'''
        p=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)

    def test_inline_toggle_fetches_once_without_changing_page(self):
        svg='function aiLayoutCadSvg'+JS.split('function aiLayoutCadSvg',1)[1].split('async function aiToggleLayoutPreview',1)[0]
        toggle='async function aiToggleLayoutPreview'+JS.split('async function aiToggleLayoutPreview',1)[1].split('function aiBindBody',1)[0]
        script=r'''
let reads=0;const aiPid='P',aiBusinessParts={business_parts_hash:'V1'};
const esc=v=>String(v);const api=async path=>{reads++;if(!path.endsWith('/requirement/packaging-geometry'))throw Error('wrong API');return {cad_scene:{entities:[{kind:'line',role:'cut',bbox:[0,0,10,5],points:[[0,0],[10,5]]}]}}};
const host={hidden:true,innerHTML:'',querySelector:()=>host.innerHTML.includes('<svg')?{}:null};
const button={textContent:'',setAttribute:()=>{},closest:()=>({querySelector:()=>host})};
'''+svg+toggle+r'''
(async()=>{await aiToggleLayoutPreview(button);if(host.hidden||!host.innerHTML.includes('<svg'))throw Error('did not expand');await aiToggleLayoutPreview(button);if(!host.hidden)throw Error('did not collapse');await aiToggleLayoutPreview(button);if(reads!==1)throw Error('re-read on toggle');})().catch(e=>{console.error(e);process.exitCode=1});
'''
        p=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)

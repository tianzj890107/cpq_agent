import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
JS = (ROOT / 'tech_app/frontend/app.js').read_text()


def function(name):
    start = JS.index('function ' + name + '(')
    end = JS.find('\nfunction ', start + 1)
    return JS[start:end if end > 0 else None]


class CandidateGalleryTests(unittest.TestCase):
    def node(self, body):
        result = subprocess.run(['node', '-e', body], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_single_section_has_dimensions_before_reselection(self):
        self.node(function('packagingBusinessPartSizeText') + '''
const assert=require('assert');
assert.strictEqual(packagingBusinessPartSizeText({sections:[{confirmed_size:{length_mm:307.07,width_mm:528.89},estimated_size:{length_mm:10,width_mm:20}}]}),'307.07 × 528.89 mm');
''')

    def test_initial_unbound_candidate_has_reference_size(self):
        self.node('\n'.join(function(n) for n in ['packagingCandidateConfidence', 'packagingRankedCandidates', 'packagingCandidateSelectionIndex','packagingBusinessPartSizeText']) + '''
const assert=require('assert');
assert.strictEqual(packagingBusinessPartSizeText({geometry_binding:{candidates:[{id:'x',entity_ids:['e'],bbox:[-10,-20,20,30]}]}}),'30.00 × 50.00 mm');
assert.strictEqual(packagingBusinessPartSizeText({sections:[{status:'pending',estimated_size:{},confirmed_size:{}}],geometry_binding:{candidates:[{id:'x',entity_ids:['e'],bbox:[-10,-20,20,30]}]}}),'30.00 × 50.00 mm');
assert.strictEqual(packagingBusinessPartSizeText({geometry_binding:{candidates:[{id:'low',bbox:[0,0,999,999]},{id:'high',entity_ids:['e'],anchor_in_region:true,bbox:[0,0,12,34]}]}}),'12.00 × 34.00 mm');
''')

    def test_fit_reset_works_with_toolbar_outside_svg_viewport(self):
        self.node('''
const assert=require('assert');const PACKAGING_PART_SHAPE_VIEWPORT_CLASS='packaging-part-shape-viewport',PACKAGING_PART_SHAPE_RESET_ID='packagingPartReset';
const shape={style:{}},label={},handlers={},reset={addEventListener:(key,fn)=>handlers[key]=fn};
const viewport={querySelector:s=>s==='svg'?shape:null,setAttribute(){},addEventListener(){}};
const host={querySelector:s=>s==='.packaging-part-shape-viewport'?viewport:s==='[data-qq-shape-zoom-label]'?label:s==='#packagingPartReset'?reset:null};
function packagingPartShapeNextState(){return {k:1,tx:0,ty:0};}
function packagingPartShapeNumberText(v){return String(v);}
function packagingPartShapeTransformCss(){return 'scale(1)';}
function packagingPartShapeZoomLabel(){return '100%';}
''' + function('bindPackagingPartShapeInteractions') + '''
bindPackagingPartShapeInteractions(host);assert.strictEqual(label.textContent,'适应窗口 · 100%');
assert.strictEqual(typeof handlers.click,'function');handlers.click({preventDefault(){},stopPropagation(){}});
assert.strictEqual(shape.style.transform,'scale(1)');
''')

    def test_all_candidate_thumbnails_have_safe_sources(self):
        self.node('\n'.join(function(n) for n in ['packagingCandidateConfidence','packagingRankedCandidates','packagingCandidateSelectionIndex','packagingBusinessPartSizeText','packagingCandidateSourceText','packagingCandidateGalleryMarkup']) + '''
const assert=require('assert');const drawn=[];
function esc(s){return String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');}
function packagingPartSceneSvg(binding,plan){assert(binding.exact_entities);assert.strictEqual(binding.bbox,null);drawn.push(binding.entity_ids);return '<svg></svg>';}
const rows=Array.from({length:8},(_,i)=>({id:String(i),entity_ids:['e'+i],bbox:[0,0,i+1,i+2],layers:['<刀线>'],evidence_reasons:['compound_name_anchor']}));
const html=packagingCandidateGalleryMarkup(rows,2,{});
assert.strictEqual((html.match(/<svg>/g)||[]).length,8);assert.strictEqual(drawn.length,8);
assert(html.includes('名称锚点组合'));assert(html.includes('&lt;刀线&gt;'));assert(!html.includes('<刀线>'));
assert(packagingCandidateSourceText({}).includes('来源未标记'));
''')

    def test_gallery_and_evidence_source_are_wired(self):
        self.assertTrue('function packagingCandidateSourceText(' in JS)
        self.assertTrue('function packagingCandidateGalleryMarkup(' in JS)
        body = function('openPackagingBusinessPart')
        self.assertTrue('packagingCandidateGalleryMarkup(' in body)
        self.assertTrue('data-qq-preview-candidate' in body)
        self.assertTrue('previewingCandidate' in body)

    def test_viewport_layout_resets_scroll_and_separates_controls(self):
        body = function('openPackagingBusinessPart')
        self.assertTrue('scrollTop = 0' in body)
        css = (ROOT / 'tech_app/frontend/drawing-flow.css').read_text()
        self.assertTrue('.packaging-candidate-grid' in css)
        self.assertTrue('position: static' in css)

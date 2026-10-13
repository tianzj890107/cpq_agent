import json
import pathlib
import subprocess
import unittest
from tech_app.backend.models.ir import Part
from tech_app.backend.models.process import ProcessOutline
from tech_app.backend.services import packaging_manufacturing as service

ROOT = pathlib.Path(__file__).resolve().parents[1]


class PackagingReferencePresentation(unittest.TestCase):
    def part(self):
        return Part(part_id='P', name='面纸', industry='packaging')

    def route(self, approved=False):
        return {'approved': approved, 'match_method': 'product_item_code',
                'header': {'name': '参考面纸', 'product_item_code': 'REF'},
                'steps': [{'line_number': 10, 'operation_name': '开料', 'standard_seconds': 60}]}

    def test_direct_reference_has_no_fabricated_similarity_preserves_approval_time(self):
        plan, coverage = service.outline(self.part(), lookup={'status':'matched',
            'match_method':'product_item_code','routes':[self.route(True)]})
        item = plan.steps[0].model_dump().get('recommendation') or {}
        self.assertEqual('reference', item.get('kind'))
        self.assertIsNone(item['references'][0]['similarity_score'])
        self.assertTrue(coverage['approved'])
        self.assertEqual(1, plan.steps[0].duration_min)
        self.assertEqual(0, plan.steps[0].confidence)

    def test_ai_remains_ai_and_retains_individual_reference_scores(self):
        refs = [{**self.route(), 'reference_name':'参考甲', 'similarity_score':.83,
                 'scores':{'material':.9},'missing_features':['size']},
                {**self.route(), 'reference_name':'参考乙','similarity_score':.41}]
        plan, _ = service.outline(self.part(),lookup={'status':'not_found','reference_routes':refs},
            run=lambda *a:ProcessOutline(steps=[{'step_no':10,'name':'模切','type':'other'}]))
        item = plan.steps[0].model_dump().get('recommendation') or {}
        self.assertEqual('ai', item.get('kind'))
        self.assertEqual([.83,.41], [r['similarity_score'] for r in item['references']])
        self.assertTrue(item['reason'])
        self.assertEqual(0, plan.steps[0].confidence)

    def render(self, step, context):
        src=(ROOT/'tech_app/frontend/inline-analysis.js').read_text()
        # Execute the actual production helpers and renderer, not a mock implementation.
        start=src.index('  function processRecommendation(') if '  function processRecommendation(' in src else src.index('  function processStep(')
        end=src.index('  function collectProcessEdits(',start)
        script='const window={};\n'+(ROOT/'tech_app/frontend/packaging-process-presentation.js').read_text()+'\nconst esc=v=>String(v??" ").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));const attr=esc;const confClass=()=>"";const TYPE_LABEL={other:"其他"};\n'+src[start:end]
        script+='\nconsole.log(processStep('+json.dumps(step)+',0,false,'+json.dumps(context)+'));'
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)
        return result.stdout

    def test_legacy_reference_ignores_seventy_and_has_expandable_evidence(self):
        html=self.render({'name':'模切','type':'other','confidence':.7},
             {'packaging':True,'coverage':{'process_origin':'da_packaging_reference',
              'route_lookup':{'match_method':'exact_name','routes':[self.route()]}}})
        self.assertIn('参考件相似度：未评分',html)
        self.assertIn('<details',html)
        self.assertNotIn('置信',html)
        self.assertNotIn('70%',html)

    def test_multisection_legacy_ai_and_html_escaping(self):
        html=self.render({'name':'模切','section_id':'S','type':'other','confidence':.6},
            {'packaging':True,'coverage':{},'sections':[{'section_id':'S','coverage':{
              'process_origin':'packaging_analogical_recommendation','reference_routes':[
                {'reference_name':'<script>','similarity_score':.83,'scores':{'material':.9}}]}}]})
        self.assertIn('AI 推荐',html)
        self.assertIn('83%',html)
        self.assertIn('&lt;script&gt;',html)
        self.assertNotIn('置信',html)

    def test_invalid_missing_score_never_defaults_or_clamps(self):
        for score in (None, '', 2, -1, 'NaN'):
            html=self.render({'name':'模切','type':'other','confidence':.7,
                'recommendation':{'kind':'reference','references':[{'similarity_score':score}]}},
                {'packaging':True})
            self.assertIn('未评分',html)
            self.assertNotIn('70%',html)

    def test_non_packaging_confidence_unchanged(self):
        html=self.render({'name':'车削','type':'other','confidence':.7},{'packaging':False})
        self.assertIn('置信 70%',html)

    def test_standalone_uses_same_presenter_and_keeps_other_industry(self):
        src=(ROOT/'tech_app/frontend/process.js').read_text()
        start=src.index('function stepCard(')
        end=src.index('\nfunction ',start+1)
        shared=(ROOT/'tech_app/frontend/packaging-process-presentation.js').read_text()
        script='const window={};'+shared+'''
const esc=v=>String(v??'');const confClass=()=>'';const TYPE_LABEL={other:'其他'};
const editing=false;let plan={part_class:'packaging'};
const processCoverage={process_origin:'approved_da_packaging_route'},processSections=[];
'''+src[start:end]+'''
let html=stepCard({name:'模切',type:'other',confidence:.7},0);
if(!html.includes('参考件相似度：未评分')||html.includes('置信'))process.exit(1);
plan={part_class:'machined'};
processCoverage.process_origin='';
html=stepCard({name:'车削',type:'other',confidence:.7},0);
if(!html.includes('置信 70%'))process.exit(2);
'''
        result=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(0,result.returncode,result.stderr)

    def test_mixed_section_sources_do_not_share_whole_part_label(self):
        context={'packaging':True,'sections':[
            {'section_id':'A','coverage':{'process_origin':'da_packaging_reference'}},
            {'section_id':'B','coverage':{'process_origin':'packaging_model_recommendation'}}]}
        a=self.render({'section_id':'A','name':'开料'},context)
        b=self.render({'section_id':'B','name':'模切'},context)
        self.assertIn('参考件相似度',a)
        self.assertNotIn('AI 推荐',a)
        self.assertIn('AI 推荐',b)

    def test_shared_script_loaded_before_both_renderers(self):
        for page,renderer in [('index.html','inline-analysis.js'),('process.html','process.js?v=')]:
            html=(ROOT/'tech_app/frontend'/page).read_text()
            self.assertLess(html.index('src="packaging-process-presentation.js'),html.index('src="'+renderer))

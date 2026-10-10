import json
from pathlib import Path
import subprocess
import unittest

APP = Path(__file__).resolve().parents[1] / 'tech_app/frontend/app.js'

class DimensionDisplayFit(unittest.TestCase):
    def run_js(self, source, script):
        result = subprocess.run(['node', '-e', source + '\n' + script], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_exact_binding_retains_dimension_evidence(self):
        source = APP.read_text()
        start = source.index('function packagingPartAnnotationEntities(')
        end = source.index('\nfunction packagingPartSceneSvg', start)
        self.run_js(source[start:end], '''
const assert = require('assert');
const doc = {cad_scene:{entities:[
 {cad_entity_id:'shape',bbox:[0,0,100,50]},
 {cad_entity_id:'dim:text',dimension_id:'d',dimension_targets:[[0,0],[100,0]],text:'100.00'},
 {cad_entity_id:'other',dimension_id:'other',dimension_targets:[[200,0],[300,0]]}
]}};
const result = packagingPartAnnotationEntities({entity_ids:['shape'],bbox:null,exact_entities:true},doc);
assert.deepEqual(result.map(x=>x.cad_entity_id),['dim:text']);
''')

    def test_text_extent_used_in_default_fit(self):
        source = APP.read_text()
        self.assertTrue('function packagingCadDisplayBox(' in source)
        start = source.index('function packagingCadDisplayBox(')
        end = source.index('\nfunction ', start + 10)
        self.run_js(source[start:end], '''
const assert = require('assert');
const text = {kind:'text',text:'528.89',height:10,x:100,y:60,bbox:[100,60,100,60]};
const box = packagingCadDisplayBox(text);
assert(box[2] >= 160 && box[3] >= 70);
const rotated = packagingCadDisplayBox({...text,rotation:90});
assert(rotated[3] >= 120);
''')
        self.assertTrue('figureRows.map(row => packagingCadDisplayBox(row))' in source)

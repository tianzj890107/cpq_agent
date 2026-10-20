import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'tech_app/frontend'


class DrawingPrimaryActionUserNotice(unittest.TestCase):
    def test_real_action_selection_across_lifecycle(self):
        source = (ROOT / 'app.js').read_text()
        start = source.find('function drawingPrimaryAction() {')
        self.assertNotEqual(-1, start, '缺少共享主操作判定，确认与生成会同时成为primary')
        end = source.index('\n}', start) + 2
        script = '''
const assert = require('assert');
let parsed=false, packaging=false, complete=false;
let handoff={ready:false,confirmed:false,error:null};
const currentProject='p';
const drawingParsed=()=>parsed;
const packagingBusinessPartRows=()=>packaging?[{}]:[];
const currentPackagingBusinessParts={};
const partsProcessComplete=()=>complete;
const window={PackagingHandoff:{current:()=>handoff}};
''' + source[start:end] + '''
assert.equal(drawingPrimaryAction(),'parseDrawing');
parsed=true; packaging=true;
assert.equal(drawingPrimaryAction(),'runAllPartProcesses');
handoff.ready=true;
assert.equal(drawingPrimaryAction(),'confirmDrawingResult');
handoff.confirmed=true;
assert.equal(drawingPrimaryAction(),'sendPartsToPricing');
handoff={ready:false,confirmed:false,error:'read failed'};
assert.equal(drawingPrimaryAction(),'confirmDrawingResult');
packaging=false;
assert.equal(drawingPrimaryAction(),'runAllPartProcesses');
complete=true;
assert.equal(drawingPrimaryAction(),'confirmDrawingResult');
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        for action in ['parseDrawing', 'confirmDrawingResult', 'sendPartsToPricing', 'runAllPartProcesses']:
            self.assertIn('drawingPrimaryAction() === "' + action + '"', source)

    def test_internal_audit_not_rendered_as_business_error(self):
        source = (ROOT / 'tech-workbench.js').read_text()
        self.assertIn("console.error('[tech-workbench] ' + detail)", source)
        self.assertNotIn("setBoardNotice(detail, 'error')", source)
        self.assertIn('primary_count', (ROOT / 'tech-board-runtime.js').read_text())

from pathlib import Path
import json
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'tech_app/frontend'

class NavigationStability(unittest.TestCase):
    def test_same_stage_does_not_remount_running_board(self):
        src = (ROOT / 'tech-workbench.js').read_text()
        start = src.index('  function applyStage(')
        end = src.index('\n  const prevBtn', start)
        script = '''
const assert = require('assert');
const state = {stage:'drawing', project:'p', taskId:'t'};
const stages = new Set(['drawing']);
let mounts = 0;
const mountStageFrame = () => {mounts++;};
const renderTop = () => {}, pushState = () => {}, syncChatProject = () => {},
refreshProgress = () => {}, syncAgentStageContext = () => {}, syncChatActions = () => {};
let primaryDiagnosticShown = '';
''' + src[start:end] + '''
applyStage('drawing', {project:'p', taskId:'t'});
assert.equal(mounts, 0);
applyStage('drawing', {project:'other'});
assert.equal(mounts, 1);
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_fullscreen_toggle_does_not_reload_content(self):
        src = (ROOT / 'app.js').read_text()
        start = src.index('function setBoardCardFullscreen(')
        end = src.index('\nfunction closeBoardCard', start)
        script = '''
const assert = require('assert');
const classes = new Set();
const card = {classList:{toggle:(key,on)=>on ? classes.add(key) : classes.delete(key)}};
const button = {setAttribute:(key,value)=>button[key]=value};
const $ = id => id === 'boardCard' ? card : button;
''' + src[start:end] + '''
setBoardCardFullscreen(true);
assert(classes.has('is-fullscreen'));
assert.equal(button['aria-label'], '退出全屏');
setBoardCardFullscreen(false);
assert(!classes.has('is-fullscreen'));
assert.equal(button['aria-label'], '全屏');
'''
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_part_process_button(self):
        src = (ROOT / 'app.js').read_text()
        self.assertIn('toggle.textContent = "工艺"', src)
        self.assertIn('packagingBusinessPartProcessByAuthority(code);', src[src.index('toggle.textContent = "工艺"'):][:500])

    def test_same_context_navigation_is_idempotent(self):
        src = (ROOT / 'tech-workbench.js').read_text()
        body = src[src.index('  function applyStage('):src.index('  function applyStage(') + 1500]
        self.assertIn('stageId === state.stage', body)
        self.assertLess(body.index('stageId === state.stage'), body.index('mountStageFrame()'))

    def test_restore_preserves_explicit_stage_and_ignores_late_result(self):
        src = (ROOT / 'tech-workbench.js').read_text()
        self.assertIn("!new URLSearchParams(location.search).get('stage')", src)
        self.assertIn('state.stage !== startedStage', src)

    def test_fullscreen_control_before_close(self):
        src = (ROOT / 'index.html').read_text()
        self.assertLess(src.index('id="boardCardFullscreen"'), src.index('id="boardCardClose"'))
        app = (ROOT / 'app.js').read_text()
        self.assertIn('function setBoardCardFullscreen(', app)
        self.assertIn('setBoardCardFullscreen(false)', app)

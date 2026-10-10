import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class InlineModuleNotes(unittest.TestCase):
    def test_process_and_cost_readback_without_module_globals(self):
        source = (ROOT / 'tech_app/frontend/inline-analysis.js').read_text()
        start = source.index('  async function load(state) {')
        end = source.index('\n  function setStatus', start + 10)
        load = source[start:end]
        script = r'''
const vm = require('vm');
const assert = require('assert');
(async () => {
for (const mode of ['process', 'cost']) {
  const node = {innerHTML: '', value: ''};
  const state = {mode, root: {querySelector: () => node}};
  const data = {plan: {steps: [{name: '模切'}]}, analysis: {quantity: 1000}};
  const ctx = {active: state, window: {PackagingConclusionNotes: {
    businessIdentity: () => ({text:'旧版本提示', level:'stale'}),
    basis: () => ({text:'尺寸来源提示', level:'authority_bound'})}},
    endpointBase: () => '/existing', jsonFetch: async () => data,
    loadLibrary: async () => {}, conclusionVersionNote: () => '',
    esc: value => String(value), render: () => {}, setStatus: () => {}};
  vm.createContext(ctx);
  vm.runInContext(LOAD + '\nthis.runLoad = load;', ctx);
  await ctx.runLoad(state);
  assert(!node.innerHTML.includes('读取失败'), node.innerHTML);
  assert.equal(state.businessNote.text, '旧版本提示');
  assert.equal(state.basisNote.text, '尺寸来源提示');
  assert(mode === 'process' ? state.plan === data.plan : state.analysis === data.analysis);
}
})().catch(e => {console.error(e); process.exit(1)});
'''
        result = subprocess.run(['node', '-e', 'const LOAD = ' + json.dumps(load) + ';\n' + script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_generation_uses_explicit_bridge(self):
        source = (ROOT / 'tech_app/frontend/inline-analysis.js').read_text()
        self.assertNotIn('= packagingBusinessPartBasisNote(result)', source)
        app = (ROOT / 'tech_app/frontend/app.js').read_text()
        self.assertIn('window.PackagingConclusionNotes =', app)

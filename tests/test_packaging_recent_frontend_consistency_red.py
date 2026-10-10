import unittest
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
JS=ROOT/'tech_app/frontend/assembly-integration.js'

class FrontendTests(unittest.TestCase):
    def test_selection_helper_uses_only_current_group(self):
        source=JS.read_text()
        helper=source[source.index('function aiLayoutSelectedCodes('):source.index('function aiBindBody()')]
        script=helper+"""
const group={querySelectorAll(selector){if(selector!=='[data-ai-layout-part-code]:checked')throw Error(selector);return [{value:'A'},{value:'B'}];}};
if(JSON.stringify(aiLayoutSelectedCodes(group))!=='["A","B"]')throw Error('wrong codes');
if(aiLayoutSelectedCodes(null).length)throw Error('empty');
"""
        subprocess.check_call(['node','-e',script])

    def test_actual_layout_renderer(self):
        source=JS.read_text()
        block=source[source.index('function aiRenderPackagingLayout()'):source.index('function aiRenderParams()')]
        script="""const aiBusinessParts={layout_rows:[{entity_id:'L',part_codes:['A']}],business_parts:[{business_part_code:'A',name:'内衬纸'},{business_part_code:'B',name:'<面纸>'}]};
const esc=x=>String(x??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');const aiAttr=esc;
"""+block+"\nconsole.log(aiRenderPackagingLayout());"
        html=subprocess.check_output(['node','-e',script],text=True)
        self.assertIn('type="checkbox"',html)
        self.assertIn('data-ai-layout-part-code',html)
        self.assertIn('checked',html)
        self.assertIn('&lt;面纸&gt;',html)
        self.assertNotIn('<select multiple',html)

    def test_group_selection_and_busy_state(self):
        source=JS.read_text()
        self.assertIn('aiLayoutSelectedCodes',source)
        self.assertIn('保存中…',source)
        self.assertIn('button.disabled = true',source)

    def test_styles_cover_multiselect(self):
        css=(ROOT/'tech_app/frontend/assembly-integration.css').read_text()
        self.assertIn('.ai-layout-options',css)
        self.assertIn('.ai-layout-option',css)
        self.assertIn('accent-color:',css)

    def test_size_editor_has_shared_form_layout(self):
        source=(ROOT/'tech_app/frontend/app.js').read_text()
        self.assertIn('packaging-size-form',source)
        css=(ROOT/'tech_app/frontend/drawing-flow.css').read_text()
        self.assertIn('.packaging-size-form',css)
        self.assertIn('.packaging-sections select',css)

    def test_quote_module_owns_warning_style(self):
        source=(ROOT/'tech_app/frontend/packaging-quote-panel.js').read_text()
        self.assertIn('.pkg-quote-warning{',source)
        self.assertIn('.pkg-quote-empty{',source)

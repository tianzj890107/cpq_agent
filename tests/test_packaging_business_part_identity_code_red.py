"""包装业务件可见编号：列表与面板一律取稳定身份码 business_part_code（Spec
`packaging-business-part-visible-identity-code.md` §2 C1–C4）。

红测口径：`tech_app/frontend/app.js` 里**不再**出现 `display_code`，行上编号取
`String(row.business_part_code || code)`，面板标题取 `${row.business_part_code || wanted}`。
只查前端文案口径，不动后端、不动身份码本身。
"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / 'tech_app' / 'frontend' / 'app.js'


class VisibleIdentityCodeTest(unittest.TestCase):
    def test_app_js_never_renders_display_code(self):
        source = APP_JS.read_text()
        # 可见编号只认稳定身份码；`row.display_code` 是版面顺序号，不许再进可见文案。
        self.assertNotIn('row.display_code', source,
                         '业务件可见编号必须取 business_part_code，不再用 row.display_code')

    def test_row_label_binds_business_part_code(self):
        source = APP_JS.read_text()
        self.assertIn('const visibleCode = String(row.business_part_code || code);',
                      source)

    def test_open_title_binds_business_part_code(self):
        source = APP_JS.read_text()
        self.assertIn('${row.business_part_code || wanted}', source)


if __name__ == '__main__':
    unittest.main()

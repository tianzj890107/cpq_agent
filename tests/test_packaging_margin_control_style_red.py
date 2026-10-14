import pathlib
import re
import unittest

HTML = (pathlib.Path(__file__).resolve().parents[1] / '确认需求解析结果.html').read_text()


class MarginControlStyleTests(unittest.TestCase):
    def test_controls_use_shared_toolbar_and_styled_input(self):
        self.assertTrue('class="packaging-pricing-controls"' in HTML, 'Missing shared pricing toolbar')
        self.assertRegex(HTML, r'<input id="packagingMarginInput" class="packaging-margin-input"')

    def test_same_height_and_radius_and_wrap(self):
        shared = re.search(r'\.packaging-pricing-controls \.packaging-margin-input,\s*\.packaging-pricing-controls \.btn\s*\{([^}]+)', HTML)
        self.assertIsNotNone(shared)
        self.assertIn('height: 36px', shared[1])
        self.assertIn('border-radius: 10px', shared[1])
        toolbar = re.search(r'\.packaging-pricing-controls\s*\{([^}]+)', HTML)
        self.assertIn('flex-wrap: wrap', toolbar[1])

    def test_pricing_contract_is_preserved(self):
        self.assertIn('min="0" max="99.99" step="0.01"', HTML)
        self.assertIn("reprice.addEventListener('click'", HTML)
        self.assertIn('options.gross_margin_rate = rate', HTML)

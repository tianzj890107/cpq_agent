"""两条 DWG 演示案例必须真的能从“可用”走到报价，不只让列表变绿。"""
import unittest

import cpq_quick_quote_workspace as workspace


class DemoBaselineContinuity(unittest.TestCase):
    def test_case_identity_and_selected_quantity_enter_workspace(self):
        baseline = {
            "case_code": "QQ-YT-DWG-WINE-700ML",
            "base_quantity": 1000,
            "case_snapshot": {
                "box_type_code": "YT-DWG-WINE-700ML",
                "box_family": "书型盒/双开门礼盒",
                "closure_type": "双开门/对开",
                "insert_type": "EVA内托",
                "inner_length": 220.5,
                "inner_width": 90,
                "inner_height": 90,
            },
        }
        result = workspace.new_workspace(baseline)
        self.assertEqual("YT-DWG-WINE-700ML", result["current"]["box_type"])
        self.assertEqual(1000.0, result["current"]["quantity"])
        self.assertNotIn("box_type", result["missing_base_fields"])
        self.assertNotIn("quantity", result["missing_base_fields"])


if __name__ == "__main__":
    unittest.main()

import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class TechHomeQuoteShellContract(unittest.TestCase):
    def read(self, relative):
        path = ROOT / relative
        self.assertTrue(path.is_file(), f"缺少文件：{relative}")
        return path.read_text(encoding="utf-8")

    def test_tech_tab_uses_same_in_page_mode_switch_as_other_assistants(self):
        home = self.read("报价首页.html")
        self.assertNotIn("const TECH_HOME_URL", home)
        self.assertNotRegex(
            home,
            r"if\s*\(\s*mode\s*===\s*['\"]tech['\"]\s*\)\s*\{[^}]*location\.href",
        )
        self.assertRegex(
            home,
            r"querySelectorAll\(['\"]\.category-tag['\"]\)[\s\S]{0,600}setMode\(mode\)",
        )

    def test_tech_mode_is_supported_by_shared_quote_home(self):
        home = self.read("报价首页.html")
        self.assertRegex(home, r"MODES\s*=\s*\{[\s\S]*?tech\s*:")
        self.assertIn("applyUploadMode(mode)", home)
        self.assertIn("loadCards(mode)", home)
        self.assertRegex(home, r"URLSearchParams\(location\.search\).*?get\(['\"]assistant['\"]\)")

    def test_shared_shell_is_the_only_active_tech_home_shell(self):
        home = self.read("报价首页.html")
        for token in (
            'class="nav-container"', 'id="navCollapsed"', 'id="navExpanded"',
            'class="page-container"', 'class="chat-container"',
            'class="unified-input-card"', 'class="list-section"',
        ):
            self.assertIn(token, home)
        self.assertRegex(home, r"\.nav-collapsed\s*\{[^}]*width:\s*56px[^}]*height:\s*100vh")
        self.assertRegex(home, r"\.nav-expanded\s*\{[^}]*width:\s*240px[^}]*height:\s*100vh")

    def test_legacy_tech_home_converges_to_shared_home_tech_mode(self):
        legacy = self.read("tech_app/frontend/home.html")
        self.assertRegex(legacy, r"(?:location\.(?:replace|href)|window\.location)\s*\(?\s*['\"]/报价首页\.html\?assistant=tech")
        self.assertNotIn('src="home.js', legacy, "旧主页不得继续启动第二套活动主页")
        self.assertNotIn('href="home-layout.css', legacy, "旧主页不得继续加载第二套导航布局")

    def test_tech_specific_controls_remain_in_shared_shell(self):
        home = self.read("报价首页.html")
        for control in ("techUploadDraw", "techUploadReq", "techIndustryBox", "techIndustry"):
            self.assertIn(f'id="{control}"', home)
        self.assertIn("techCreateAndGo", home)
        self.assertIn("tech-workbench.html?stage=requirement-create", home)


if __name__ == "__main__":
    unittest.main()

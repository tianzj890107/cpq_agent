"""第 18 步红测：统一配置落实补完（技术工艺看板不再有独立 vision/text 模型）。

覆盖：
1. 技术工艺看板页（assembly-integration.js / cost-review.js）不再出现
   text_model / vision_model / text_options / vision_options 双模型口径；
2. 两个看板页的模型药丸改读唯一 model + options，并仍复用 LlmSettingsPanel；
3. 技术工艺前端（全部 .js / .html）无 /api/llm/settings，唯一面板走 /api/settings；
4. 设置仍是唯一居中模态卡片（#techModelSettingsMask / #techModelSettings），无双页签；
5. llm_settings.py 不指向第二份持久化文件，Key 仍按 provider 全局读取；
6. /api/llm/settings 兼容路由只委托 llm_settings.snapshot / update。

不联网、不起服务、不读真实业务数据、不写任何 Key。
"""
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
TECH_FRONTEND = ROOT / "tech_app" / "frontend"
TECH_BACKEND = ROOT / "tech_app" / "backend"

# 技术工艺独立 vision/text 设置的两模型字段（唯一模型口径下都不该再出现）。
LEGACY_TWO_MODEL = ("text_model", "vision_model", "text_options", "vision_options")
# 仍按两模型渲染模型药丸的看板页。
BOARD_PAGES = ("assembly-integration.js", "cost-review.js")


def _read(path):
    return path.read_text(encoding="utf-8", errors="replace").replace("\x00", "")


def _py_def_body(text, name):
    idx = text.find(f"def {name}(")
    if idx < 0:
        return ""
    rest = text[idx:]
    nxt = rest.find("\ndef ", 1)
    return rest[:nxt] if nxt != -1 else rest


class TechUnifiedConfigCompletionRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frontend_js = {p.name: _read(p) for p in sorted(TECH_FRONTEND.glob("*.js"))}
        cls.frontend_html = {p.name: _read(p) for p in sorted(TECH_FRONTEND.glob("*.html"))}
        cls.tech_settings = _read(TECH_BACKEND / "services" / "llm_settings.py")
        cls.main = _read(TECH_BACKEND / "main.py")

    # ---------------------------------------------------------------- 唯一模型
    def test_board_pages_drop_legacy_two_model_fields(self):
        for name in BOARD_PAGES:
            text = self.frontend_js[name]
            for token in LEGACY_TWO_MODEL:
                self.assertNotIn(
                    token, text,
                    f"{name} 仍保留技术工艺独立 vision/text 设置：{token}")

    def test_board_model_pill_reads_single_shared_model(self):
        for name in BOARD_PAGES:
            text = self.frontend_js[name]
            self.assertRegex(
                text, r"settings\s*(?:\?\.|\.)\s*model\b",
                f"{name} 的模型药丸必须读唯一 model 字段")
            self.assertRegex(
                text, r"settings\s*(?:\?\.|\.)\s*options\b",
                f"{name} 的模型药丸必须用统一模型清单 settings.options")
            self.assertIn("LlmSettingsPanel", text,
                          f"{name} 必须复用统一设置面板")

    def test_no_board_page_renders_two_model_tooltip(self):
        for name in BOARD_PAGES:
            text = self.frontend_js[name]
            for phrase in ("语言模型", "多模态模型"):
                self.assertNotIn(phrase, text,
                                 f"{name} 不得再渲染双模型文案：{phrase}")

    # ---------------------------------------------------------------- 唯一接口
    def test_no_tech_frontend_uses_legacy_settings_route(self):
        combined = "\n".join(
            list(self.frontend_js.values()) + list(self.frontend_html.values()))
        self.assertNotIn("/api/llm/settings", combined,
                         "技术工艺前端不得读写旧设置接口")
        self.assertIn("/api/settings", self.frontend_js["llm-settings-panel.js"],
                      "唯一设置面板必须走 /api/settings")

    # ---------------------------------------------------------------- 居中模态卡片
    def test_single_centered_modal_for_tech_settings(self):
        html = self.frontend_html["tech-workbench.html"]
        self.assertRegex(
            html, r'id="techModelSettingsMask"[^>]+role="(?:presentation|dialog)"',
            "技术工艺设置必须有全屏遮罩")
        self.assertRegex(
            html, r'id="techModelSettings"[^>]+role="dialog"',
            "技术工艺设置卡片必须有 role=dialog")
        self.assertNotRegex(html, r'role="tab"',
                            "不得保留助手模型 / 技术工艺模型双页签")

    # ---------------------------------------------------------------- 唯一持久化源
    def test_single_persisted_source_and_shared_provider_keys(self):
        self.assertNotRegex(self.tech_settings, r"_PATH\s*=.*llm_settings\.json",
                            "不得再把第二份技术工艺设置文件当路径常量")
        self.assertNotRegex(self.tech_settings, r"(?:open|write|dump)[^\n]*llm_settings\.json",
                            "不得读写第二份技术工艺设置文件")
        self.assertRegex(self.tech_settings,
                         r"cpq_shared_settings|shared_settings|quote_settings",
                         "设置必须来自报价的 cpq_settings.json")
        self.assertRegex(self.tech_settings, r"api_keys\(",
                         "API Key 必须按 provider 从全局配置读取")
        # Key 不能被打进日志 / 回执：模块只做读取与转发，不应直接 print 明文。
        self.assertNotRegex(self.tech_settings,
                            r'print\([^)]*api_key|log[^\n]*api_key\s*\+',
                            "不得打印 API Key 明文")

    def test_legacy_route_delegates_to_single_implementation(self):
        getter = _py_def_body(self.main, "get_runtime_llm_settings")
        self.assertIn("llm_settings.snapshot(", getter,
                      "旧设置接口必须委托唯一快照实现")
        putter = _py_def_body(self.main, "update_runtime_llm_settings")
        self.assertIn("llm_settings.update(", putter,
                      "旧设置接口必须委托唯一写入实现")
        self.assertNotIn("llm_settings.json", self.main,
                         "后端不得再持久化第二份设置文件")


if __name__ == "__main__":
    unittest.main()

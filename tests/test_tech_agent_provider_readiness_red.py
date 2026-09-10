from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
AGENT_PATH = ROOT / "tech_app" / "backend" / "services" / "oc_agent.py"
ENV_EXAMPLE = ROOT / "tech_app" / ".env.example"


class TechAgentProviderReadinessRedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agent = AGENT_PATH.read_text(encoding="utf-8")
        cls.env_example = ENV_EXAMPLE.read_text(encoding="utf-8")

    def available_body(self):
        match = re.search(r'def available\(\)[^:]*:\s*([\s\S]*?)(?=\n\n#|\n\ndef )', self.agent)
        self.assertIsNotNone(match)
        return match.group(1)

    def test_available_resolves_current_text_model_route(self):
        body = self.available_body()
        self.assertRegex(body, r'llm_settings\.resolve\(vision=False\)')
        self.assertRegex(body, r'(?:route|resolved)\[["\']api_key["\']\]')

    def test_available_is_not_hard_wired_to_anthropic_env(self):
        body = self.available_body()
        self.assertNotIn('os.getenv("ANTHROPIC_API_KEY"', body)
        self.assertNotIn("未配置 ANTHROPIC_API_KEY，Agent 无法启动", body)

    def test_agent_session_uses_full_resolved_provider_route(self):
        self.assertRegex(self.agent, r'llm_settings\.resolve\(vision=False\)')
        for field in ("provider", "base_url", "api_key"):
            self.assertRegex(self.agent, rf'(?:route|resolved)\[["\']{field}["\']\]')

    def test_qwen_is_an_explicit_supported_agent_provider(self):
        self.assertRegex(self.agent, r'(?:provider|route)[\s\S]{0,800}["\']qwen["\']')

    def test_env_example_no_longer_claims_anthropic_is_required(self):
        first_lines = "\n".join(self.env_example.splitlines()[:10])
        self.assertNotIn("填入你的 Anthropic API Key", first_lines)
        self.assertRegex(first_lines, r'前端|设置|所选模型|provider')


if __name__ == "__main__":
    unittest.main()

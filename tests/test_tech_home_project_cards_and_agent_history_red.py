import pathlib
import re
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
HOME = (ROOT / "报价首页.html").read_text(encoding="utf-8")
WORKBENCH = (ROOT / "tech_app/frontend/tech-workbench.js").read_text(encoding="utf-8")
CHAT = (ROOT / "tech_app/frontend/agent-chat.js").read_bytes().replace(b"\x00", b"").decode("utf-8")
MAIN = (ROOT / "tech_app/backend/main.py").read_text(encoding="utf-8")
AGENT = (ROOT / "tech_app/backend/services/oc_agent.py").read_text(encoding="utf-8")


def function_body(source: str, name: str, next_name: str) -> str:
    match = re.search(
        rf"(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{([\s\S]*?)"
        rf"(?=\n\s*(?:async\s+)?function\s+{re.escape(next_name)}\s*\()",
        source,
    )
    if not match:
        raise AssertionError(f"找不到函数 {name}")
    return match.group(1)


class TechHomeProjectCardsContract(unittest.TestCase):
    def test_home_keeps_three_tech_tabs(self):
        self.assertRegex(
            HOME,
            r"tech\s*:\s*\{[\s\S]*?tabs\s*:\s*\[\s*['\"]我的清单['\"]\s*,\s*"
            r"['\"]待办任务['\"]\s*,\s*['\"]全部清单['\"]",
        )

    def test_home_project_loader_uses_authenticated_api_and_checks_failure(self):
        body = function_body(HOME, "loadCards", "renderTaskTab")
        tech_branch = re.search(r"if\s*\(mode\s*===\s*['\"]tech['\"]\)\s*\{([\s\S]*?)\n\s*\}\s*else", body)
        self.assertIsNotNone(tech_branch)
        branch = tech_branch.group(1)
        self.assertRegex(branch, r"cpqAuth\.api\(\s*['\"]/api/projects['\"]")
        self.assertNotRegex(branch, r"fetch\(\s*['\"]/api/projects['\"]")

    def test_tech_cards_preserve_owner_and_mine_filters_by_logged_in_username(self):
        loader = function_body(HOME, "loadCards", "renderTaskTab")
        renderer = function_body(HOME, "renderCards", "deleteCard")
        self.assertRegex(loader, r"owner\s*:\s*pp\.owner")
        self.assertRegex(renderer, r"mode\s*===\s*['\"]tech['\"][\s\S]{0,500}owner")
        self.assertRegex(renderer, r"wfUser\(\)[\s\S]{0,300}(?:username|user_name)")
        self.assertNotIn("mineOnly && mode !== 'tech'", renderer)

    def test_home_and_history_drawer_share_projects_fact_source(self):
        self.assertIn("/api/projects", function_body(HOME, "loadCards", "renderTaskTab"))
        self.assertIn("/api/projects", function_body(WORKBENCH, "loadTechHistory", "techStageFromProject"))
        self.assertIn("openTechProject(card.dataset.id)", HOME)
        self.assertIn("techHistoryRestore(id)", WORKBENCH)


class TechAgentFullHistoryContract(unittest.TestCase):
    def test_backend_exposes_read_only_project_agent_history(self):
        self.assertRegex(
            MAIN,
            r"@app\.get\(\s*['\"]/api/projects/\{project_id\}/agent/history['\"]\s*\)",
        )
        self.assertNotRegex(
            MAIN,
            r"@app\.(?:post|put|patch|delete)\(\s*['\"]/api/projects/\{project_id\}/agent/history['\"]",
        )

    def test_history_contract_includes_messages_and_tool_trace_events(self):
        self.assertRegex(AGENT, r"def\s+(?:history|load_history|conversation_history)\s*\(")
        combined = MAIN + "\n" + AGENT
        for token in ("messages", "tool_use", "tool_result"):
            with self.subTest(token=token):
                self.assertIn(token, combined)

    def test_frontend_loads_and_renders_history_for_bound_project(self):
        self.assertIn('api("/history")', CHAT)
        self.assertRegex(CHAT, r"(?:async\s+)?function\s+(?:load|restore|render)\w*History\s*\(")
        self.assertRegex(CHAT, r"(?:addUser|oc-ubub)")
        self.assertRegex(CHAT, r"(?:addAssistant|oc-amsg)")
        self.assertRegex(CHAT, r"(?:addToolCard|tool_use)")
        self.assertIn("tool_result", CHAT)

    def test_initialization_restores_history_without_resetting_it(self):
        tail = CHAT[-5000:]
        self.assertRegex(tail, r"(?:load|restore)\w*History\s*\(")
        self.assertIn("loadMeta()", tail)
        self.assertNotRegex(tail, r"(?:resetTaskFlow|api\(\s*['\"]/new['\"]\s*\))\s*\(")

    def test_existing_agent_routes_and_project_scoping_remain(self):
        for route in ("/api/projects/{project_id}/agent/meta",
                      "/api/projects/{project_id}/agent/send",
                      "/api/projects/{project_id}/agent/new"):
            with self.subTest(route=route):
                self.assertIn(route, MAIN)
        self.assertIn("SessionStore", AGENT)
        self.assertIn("project_id", AGENT)


if __name__ == "__main__":
    unittest.main()

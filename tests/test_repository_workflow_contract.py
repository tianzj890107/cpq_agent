import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class RepositoryWorkflowContract(unittest.TestCase):
    def test_weekly_changelog_only(self):
        agents = (ROOT / "AGENTS.md").read_text()
        self.assertIn("只按周维护", agents)
        self.assertTrue((ROOT / "changelog/changelog_9_7_11.md").is_file())

    def test_fixed_branch_and_review_flow(self):
        push = (ROOT / "scripts/push_remotes.py").read_text()
        mr = (ROOT / "scripts/create_mr.py").read_text()
        self.assertIn('BRANCH = "20260909"', push)
        self.assertIn('REMOTE = "gitlab"', push)
        self.assertNotIn('git("push", "origin"', push)
        self.assertNotIn("github.com", push.lower())
        self.assertIn('SOURCE = "20260909"', mr)
        self.assertIn('TARGET = "master"', mr)
        self.assertIn('REVIEWER = "tianzijing"', mr)
        self.assertNotIn("merge_when_pipeline_succeeds", mr)

    def test_release_requires_existing_tag(self):
        release = (ROOT / "scripts/create_release.py").read_text()
        self.assertIn("repository/tags", release)
        self.assertIn("不得隐式创建 tag", release)

    def test_ci_never_deploys(self):
        ci = (ROOT / ".gitlab-ci.yml").read_text().lower()
        self.assertNotIn("deploy", ci)
        self.assertIn("merge_request_event", ci)
        self.assertIn("ci_default_branch", ci)

    def test_deploy_has_data_and_health_guards(self):
        deploy = (ROOT / "scripts/deploy_server.sh").read_text()
        self.assertIn("CPQ_DEPLOY_REF", deploy)
        self.assertIn("git merge-base --is-ancestor", deploy)
        self.assertIn("cpq_settings.json", deploy)
        self.assertIn("curl --fail", deploy)

    def test_continue_cannot_reopen_completed_work(self):
        agents = (ROOT / "AGENTS.md").read_text()
        self.assertIn("任务终态与“继续”指令（最高优先级）", agents)
        self.assertIn("不得重开已完成任务", agents)
        self.assertIn("只回复上一任务已完成并等待具体指令", agents)

    def test_external_actions_require_current_explicit_instruction(self):
        agents = (ROOT / "AGENTS.md").read_text()
        for action in ("push", "MR", "tag", "Release", "部署"):
            self.assertIn(action, agents)
        self.assertIn("历史授权不延续", agents)
        self.assertIn("本地服务启动/停止", agents)

    def test_prompts_stay_in_conversation(self):
        agents = (ROOT / "AGENTS.md").read_text()
        self.assertIn("实现提示词只在会话中交付", agents)
        self.assertFalse((ROOT / "prompts").exists())

    def test_destructive_actions_have_exact_target_guards(self):
        agents = (ROOT / "AGENTS.md").read_text()
        self.assertIn("历史会话、业务数据与删除安全（最高优先级）", agents)
        self.assertIn("空值、未定义变量", agents)
        self.assertIn("删除前至少执行两项防御检查", agents)

if __name__ == "__main__":
    unittest.main()

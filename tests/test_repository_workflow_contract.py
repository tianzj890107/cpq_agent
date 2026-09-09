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

if __name__ == "__main__":
    unittest.main()

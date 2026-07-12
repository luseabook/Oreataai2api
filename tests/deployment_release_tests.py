import unittest
from pathlib import Path


class DeploymentReleaseTests(unittest.TestCase):
    def setUp(self):
        self.source = (
            Path(__file__).resolve().parents[1] / "scripts" / "deploy_release.sh"
        ).read_text(encoding="utf-8")

    def test_links_all_runtime_state_before_switching_current(self):
        state_loop = "for state_file in config.json accounts.db; do"
        link_statement = 'ln -s "$state_dir/$state_file" "$release_dir/$state_file"'
        switch_statement = 'ln -sfn "$release_dir" "$current_link"'

        self.assertIn(state_loop, self.source)
        self.assertIn(link_statement, self.source)
        self.assertIn(switch_statement, self.source)
        self.assertLess(self.source.index(link_statement), self.source.index(switch_statement))

    def test_has_health_check_and_previous_release_rollback(self):
        self.assertIn('previous_release="$(readlink -f "$current_link")"', self.source)
        self.assertIn('curl -fsS "$health_url"', self.source)
        self.assertIn('ln -sfn "$previous_release" "$current_link"', self.source)
        self.assertIn('systemctl restart "$service_name"', self.source)


if __name__ == "__main__":
    unittest.main()

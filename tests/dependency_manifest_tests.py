import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def requirement_names(path: Path) -> set[str]:
    names: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        name = line.split(";", 1)[0]
        for marker in ("===", "==", ">=", "<=", "~=", "!=", ">", "<"):
            name = name.split(marker, 1)[0]
        names.add(name.strip().lower().replace("_", "-"))
    return names


class DependencyManifestTests(unittest.TestCase):
    def test_runtime_manifest_includes_multipart_support(self):
        self.assertIn("python-multipart", requirement_names(ROOT / "requirements.txt"))

    def test_development_manifest_includes_test_client_dependency(self):
        dev_path = ROOT / "requirements-dev.txt"
        self.assertTrue(dev_path.exists(), "requirements-dev.txt must exist")
        self.assertIn("httpx", requirement_names(dev_path))

    def test_readme_documents_node_and_encryption_runtime_requirements(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("Node.js", readme)
        self.assertIn("OREATE_ENCRYPTION_KEY", readme)

    def test_runbooks_document_deployment_backup_and_release_checks(self):
        deployment = ROOT / "docs" / "runbooks" / "gateway-deployment.md"
        backup = ROOT / "docs" / "runbooks" / "backup-restore.md"
        checklist = ROOT / "docs" / "runbooks" / "release-checklist.md"
        for path in (deployment, backup, checklist):
            self.assertTrue(path.exists(), f"{path.relative_to(ROOT)} must exist")

        deployment_text = deployment.read_text(encoding="utf-8")
        for required in (
            "single application worker",
            "OREATE_APP_WORKERS=1",
            "reverse proxy",
            "TLS",
            "OREATE_ENCRYPTION_KEY",
            "Node.js",
            "banti_jt_helper.js",
        ):
            self.assertIn(required, deployment_text)

        backup_text = backup.read_text(encoding="utf-8")
        for required in (
            "restore verification",
            "stale admin sessions",
            "OREATE_ENCRYPTION_KEY",
            "accounts.db",
            "config.json",
        ):
            self.assertIn(required, backup_text)

        checklist_text = checklist.read_text(encoding="utf-8")
        for required in (
            'python -m unittest discover -s tests -p "*_tests.py" -v',
            "python -m py_compile server.py banti_token_generator.py",
            "new Function(script)",
            "git diff --check",
            "sensitive diff scan",
        ):
            self.assertIn(required, checklist_text)

    def test_acceptance_docs_keep_motion_blocked_until_legitimate_sample(self):
        acceptance = ROOT / "docs" / "plans" / "2026-07-10-image-video-gateway-production-acceptance-checklist.md"
        record = ROOT / "docs" / "plans" / "2026-07-10-s2-hard-acceptance-implementation-record.md"
        guardrail = "Do not register or rotate into new upstream accounts to bypass HTTP 403, quota exhaustion, or risk controls."

        for path in (acceptance, record):
            text = path.read_text(encoding="utf-8")
            self.assertIn("P2-04", text)
            self.assertIn("motion", text)
            self.assertIn(guardrail, text)
            if path == acceptance:
                self.assertRegex(text, r"\| P2-04 \| motion[^\n]*\| [^|\n]*未完成")
            else:
                self.assertIn("motion` still requires a successful real-credit validation sample", text)


if __name__ == "__main__":
    unittest.main()

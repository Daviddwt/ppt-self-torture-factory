import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


installer = module("install")
audit = module("audit_public")
preflight = module("preflight")


class InstallationTests(unittest.TestCase):
    def test_plan_makes_no_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "skills"
            self.assertEqual(installer.install(target)["status"], "plan")
            self.assertFalse(target.exists())

    def test_clean_install_is_self_contained_and_rejects_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "skills"
            result = installer.install(target, True)
            self.assertEqual(result["status"], "installed")
            for name in installer.NAMES:
                self.assertEqual((target / name / "SKILL.md").read_bytes(),
                                 (ROOT / "skills" / name / "SKILL.md").read_bytes())
                for notice in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
                    self.assertEqual((target / name / notice).read_bytes(), (ROOT / notice).read_bytes())
            with self.assertRaises(FileExistsError):
                installer.install(target, True)

    def test_conflict_prevents_partial_install(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            existing = target / installer.NAMES[-1]
            existing.mkdir()
            (existing / "keep.txt").write_text("keep")
            with self.assertRaises(FileExistsError):
                installer.install(target, True)
            self.assertEqual(sorted(p.name for p in target.iterdir()), [installer.NAMES[-1]])
            self.assertEqual((existing / "keep.txt").read_text(), "keep")

    def test_source_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source"
            for name in installer.NAMES:
                folder = source / name
                folder.mkdir(parents=True)
                (folder / "SKILL.md").write_text("example")
            (source / installer.NAMES[0] / "link").symlink_to(source / installer.NAMES[1])
            with self.assertRaises(ValueError):
                installer.install(Path(temp) / "target", True, source)


class AuditTests(unittest.TestCase):
    def test_detects_secrets_without_echoing_values(self):
        with tempfile.TemporaryDirectory() as temp:
            token = "gh" + "p_" + "A" * 30
            (Path(temp) / "note.md").write_text(token)
            report = audit.audit(temp)
            self.assertEqual(report["status"], "fail")
            self.assertNotIn(token, json.dumps(report))

    def test_rejects_binary_and_private_terms(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "deck.pptx").write_bytes(b"test")
            (Path(temp) / "note.md").write_text("private-demo-term")
            report = audit.audit(temp, deny_terms=["private-demo-term"])
            self.assertEqual(len(report["findings"]), 2)

    def test_preflight_does_not_claim_production(self):
        report = preflight.inspect("1")
        self.assertFalse(report["production_ready"])
        self.assertTrue(report["manual_checks"])

    def test_private_term_in_filename_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "private-demo-client.md").write_text("ordinary text")
            self.assertEqual(audit.audit(temp, deny_terms=["private-demo-client"])["status"], "fail")


if __name__ == "__main__":
    unittest.main()

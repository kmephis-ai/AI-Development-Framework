import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.trust import (
    classify_diff,
    classify_git_diff,
    is_protected_path,
    validate_check_provenance,
    validate_review_provenance,
)


POLICY = {
    "paths": [".adwf/**", ".github/workflows/adwf-*.yml"],
    "weakening_is_risk": "R4",
    "weakening_requires_human": True,
    "self_modification_in_feature_pr": "FORBIDDEN",
}


class TrustedDiffTests(unittest.TestCase):
    @staticmethod
    def _body(roadmap: str, risk: str, checked: bool) -> str:
        mark = "x" if checked else " "
        return f"""## Контракт

Roadmap-ID: {roadmap}
Issue: #1
Writer-Lease: 11111111-1111-4111-8111-111111111111

## Что изменено и зачем

Guard.

## Scope

Governance.

## Проверки

Deterministic.

## Risk / rollback

Risk: {risk}

## Trust boundary

- [{mark}] Trust change вынесен в отдельный GOV PR, классифицирован R4 и human-gated
"""

    def test_recursive_protected_glob_matches_nested_file(self):
        self.assertTrue(is_protected_path(".adwf/lib/trust.py", POLICY["paths"]))
        self.assertFalse(is_protected_path("src/product.py", POLICY["paths"]))

    def test_feature_only_is_automation_eligible(self):
        result = classify_diff([{"path": "src/product.py", "status": "M"}], POLICY)
        self.assertEqual(result["result"], "ALLOW")
        self.assertFalse(result["human_required"])

    def test_protected_only_is_r4_gov_and_human_required(self):
        result = classify_diff([{"path": ".adwf/lib/new_guard.py", "status": "A", "new_text": "safe = True\n"}], POLICY)
        self.assertEqual(result["result"], "HUMAN_REQUIRED")
        self.assertEqual(result["required_risk"], "R4")
        self.assertEqual(result["required_work_type"], "GOV")

    def test_feature_plus_gate_weakening_is_blocked(self):
        result = classify_diff([
            {"path": "src/product.py", "status": "M"},
            {"path": ".adwf/config.json", "status": "M",
             "old_text": '{"policy":{"independent_review":true}}',
             "new_text": '{"policy":{"independent_review":false}}'},
        ], POLICY)
        self.assertEqual(result["result"], "BLOCK")
        self.assertIn("TRUST_CHANGE_MIXED_WITH_FEATURE", result["reason_codes"])
        self.assertIn("GATE_WEAKENING_DETECTED", result["reason_codes"])

    def test_deleted_protected_file_is_weakening(self):
        result = classify_diff([{"path": ".github/workflows/adwf-pr.yml", "status": "D", "old_text": "required: true\n"}], POLICY)
        self.assertIn("GATE_WEAKENING_DETECTED", result["reason_codes"])

    def test_policy_is_loaded_from_base_commit_not_pr_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "ADWF Test"], cwd=root, check=True)
            (root / ".adwf/policies").mkdir(parents=True)
            (root / "src").mkdir()
            (root / ".adwf/policies/trust-boundary.json").write_text(json.dumps(POLICY), encoding="utf-8")
            (root / "src/product.py").write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, check=True)
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            weakened = dict(POLICY)
            weakened["paths"] = ["nothing/**"]
            (root / ".adwf/policies/trust-boundary.json").write_text(json.dumps(weakened), encoding="utf-8")
            (root / "src/product.py").write_text("VALUE = 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "head"], cwd=root, check=True)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            result = classify_git_diff(root, base, head)
            self.assertEqual(result["result"], "BLOCK")
            self.assertIn(".adwf/policies/trust-boundary.json", result["protected_files"])

    def test_pr_validator_enforces_gov_r4_and_human_attestation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "ADWF Test"], cwd=root, check=True)
            (root / ".adwf/policies").mkdir(parents=True)
            (root / ".adwf/policies/trust-boundary.json").write_text(json.dumps(POLICY), encoding="utf-8")
            (root / ".adwf/config.json").write_text('{"policy":{"independent_review":true}}', encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, check=True)
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            (root / ".adwf/config.json").write_text('{"policy":{"independent_review":false}}', encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-q", "-m", "head"], cwd=root, check=True)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            command = [sys.executable, str(ROOT / ".adwf/scripts/validate_pr.py"), "--root", str(root),
                       "--base-sha", base, "--head-sha", head, "--body"]
            blocked = subprocess.run([*command, self._body("DEV-1", "R0", False)], capture_output=True, text=True)
            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("TRUST_CHANGE_REQUIRES_R4", blocked.stdout)
            allowed = subprocess.run([*command, self._body("GOV-1", "R4", True)], capture_output=True, text=True)
            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)
            self.assertIn("OWNER DECISION REQUIRED", allowed.stdout)


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)

    def test_check_requires_exact_head_success_trusted_app_and_fresh_time(self):
        check = {"name": "ADWF PR / fast-feedback", "head_sha": self.sha, "status": "completed", "conclusion": "success",
                 "app": {"slug": "github-actions"}, "completed_at": "2026-08-13T11:30:00Z"}
        result = validate_check_provenance([check], expected_sha=self.sha,
                                           expected_names=["ADWF PR / fast-feedback"],
                                           trusted_app_slugs=["github-actions"], now=self.now)
        self.assertTrue(result["valid"], result["errors"])
        forged = dict(check, head_sha="b" * 40, app={"slug": "untrusted"})
        invalid = validate_check_provenance([forged], expected_sha=self.sha,
                                            expected_names=["ADWF PR / fast-feedback"],
                                            trusted_app_slugs=["github-actions"], now=self.now)
        self.assertIn("CHECK_HEAD_MISMATCH:ADWF PR / fast-feedback", invalid["errors"])
        self.assertIn("CHECK_APP_UNTRUSTED:ADWF PR / fast-feedback", invalid["errors"])

    def test_latest_review_must_be_independent_approved_and_exact_head(self):
        reviews = [
            {"state": "APPROVED", "commit_id": self.sha, "submitted_at": "2026-08-13T10:00:00Z", "user": {"login": "reviewer"}},
            {"state": "CHANGES_REQUESTED", "commit_id": self.sha, "submitted_at": "2026-08-13T11:00:00Z", "user": {"login": "reviewer"}},
        ]
        invalid = validate_review_provenance(reviews, expected_sha=self.sha, author_login="writer",
                                             trusted_reviewer_logins=["reviewer"], now=self.now)
        self.assertFalse(invalid["valid"])
        reviews.pop()
        valid = validate_review_provenance(reviews, expected_sha=self.sha, author_login="writer",
                                           trusted_reviewer_logins=["reviewer"], now=self.now)
        self.assertTrue(valid["valid"], valid["errors"])


if __name__ == "__main__":
    unittest.main()

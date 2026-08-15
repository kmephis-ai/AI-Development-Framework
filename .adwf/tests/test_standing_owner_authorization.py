import base64
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.trust import classify_diff, standing_authorization_policy_metadata
from scripts import publish_trusted_gate as GATE
from scripts.publish_trusted_gate import evaluate_trusted_gate


def current_policy():
    return json.loads((ROOT / ".adwf/policies/trust-boundary.json").read_text(encoding="utf-8"))


def safe_record(path=".adwf/scripts/platform_smoke.py"):
    return {"path": path, "status": "M", "old_text": "VALUE = 1\n", "new_text": "VALUE = 2\n"}


class StandingPolicyClassificationTests(unittest.TestCase):
    def test_policy_is_versioned_active_revocable_and_fingerprinted(self):
        meta = standing_authorization_policy_metadata(current_policy())
        self.assertTrue(meta["valid"])
        self.assertEqual(meta["status"], "ACTIVE")
        self.assertEqual(meta["revision"], 1)
        self.assertRegex(meta["digest"], r"^[0-9a-f]{64}$")

    def test_routine_reversible_support_is_auto_authorized(self):
        result = classify_diff([safe_record()], current_policy())
        self.assertEqual(result["result"], "ALLOW")
        self.assertFalse(result["human_required"])
        self.assertEqual(result["authorization_mode"], "STANDING_OWNER_POLICY")
        self.assertEqual(result["manual_required_files"], [])

    def test_generated_projections_can_follow_routine_change_without_human(self):
        result = classify_diff([
            safe_record(),
            {"path": ".adwf/docs-registry.json", "status": "M", "old_text": "{}", "new_text": "{}"},
            {"path": "MANIFEST.json", "status": "M", "old_text": "{}", "new_text": "{}"},
            {"path": "SHA256SUMS.txt", "status": "M", "old_text": "old\n", "new_text": "new\n"},
        ], current_policy())
        self.assertEqual(result["result"], "ALLOW")
        self.assertEqual(result["authorization_mode"], "STANDING_OWNER_POLICY")

    def test_authorization_evaluator_change_can_never_use_standing_policy(self):
        result = classify_diff([safe_record(".adwf/lib/trust.py")], current_policy())
        self.assertEqual(result["result"], "HUMAN_REQUIRED")
        self.assertIn(".adwf/lib/trust.py", result["manual_required_files"])
        self.assertNotEqual(result["authorization_mode"], "STANDING_OWNER_POLICY")

    def test_standing_policy_change_can_never_authorize_itself(self):
        policy_text = json.dumps(current_policy())
        result = classify_diff([{
            "path": ".adwf/policies/trust-boundary.json", "status": "M",
            "old_text": policy_text, "new_text": policy_text,
        }], current_policy())
        self.assertEqual(result["result"], "HUMAN_REQUIRED")
        self.assertIn(".adwf/policies/trust-boundary.json", result["manual_required_files"])

    def test_gate_weakening_requires_explicit_human(self):
        result = classify_diff([{
            "path": ".adwf/scripts/platform_smoke.py", "status": "M",
            "old_text": "required = True\n", "new_text": "required = False\n",
        }], current_policy())
        self.assertEqual(result["result"], "HUMAN_REQUIRED")
        self.assertIn("GATE_WEAKENING_DETECTED", result["reason_codes"])

    def test_revoked_policy_falls_back_to_human(self):
        policy = current_policy(); policy["standing_authorization"]["status"] = "REVOKED"
        result = classify_diff([safe_record()], policy)
        self.assertEqual(result["result"], "HUMAN_REQUIRED")
        self.assertIn("STANDING_AUTHORIZATION_REVOKED", result["reason_codes"])

    def test_invalid_or_forged_standing_policy_blocks(self):
        policy = current_policy(); policy["standing_authorization"]["non_overridable_invariants"] = ["NO_BYPASS"]
        result = classify_diff([safe_record()], policy)
        self.assertEqual(result["result"], "BLOCK")
        self.assertIn("BASE_STANDING_AUTHORIZATION_POLICY_INVALID", result["reason_codes"])

    def test_unverified_protected_content_never_auto_authorizes(self):
        result = classify_diff([{"path": ".adwf/scripts/platform_smoke.py", "status": "M"}], current_policy())
        self.assertEqual(result["result"], "HUMAN_REQUIRED")
        self.assertIn("PROTECTED_CONTENT_NOT_VERIFIED", result["reason_codes"])

    def test_mixed_authoritative_trust_and_feature_is_absolute_block(self):
        result = classify_diff([
            safe_record(".adwf/lib/trust.py"),
            {"path": "src/product.py", "status": "M"},
        ], current_policy())
        self.assertEqual(result["result"], "BLOCK")
        self.assertIn("TRUST_CHANGE_MIXED_WITH_FEATURE", result["reason_codes"])

    def test_non_overridable_invariants_include_free_only_and_no_bypass(self):
        invariants = set(current_policy()["standing_authorization"]["non_overridable_invariants"])
        self.assertEqual(invariants, {"FREE_ONLY", "NO_BYPASS", "EVIDENCE_INTEGRITY", "NO_SELF_AUTHORIZATION"})

    def test_pr_validator_needs_no_literal_checkbox_for_standing_safe_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "ADWF Test"], cwd=root, check=True)
            (root / ".adwf/policies").mkdir(parents=True)
            (root / ".adwf/scripts").mkdir(parents=True)
            (root / ".adwf/policies/trust-boundary.json").write_text(json.dumps(current_policy()), encoding="utf-8")
            (root / ".adwf/scripts/platform_smoke.py").write_text("VALUE = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True); subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, check=True)
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            (root / ".adwf/scripts/platform_smoke.py").write_text("VALUE = 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True); subprocess.run(["git", "commit", "-q", "-m", "head"], cwd=root, check=True)
            head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            body = """## Контракт\nRoadmap-ID: CI-1\nIssue: #1\nWriter-Lease: 11111111-1111-4111-8111-111111111111\n\n## Что изменено и зачем\nRoutine repair.\n\n## Scope\nSmoke.\n\n## Проверки\nTests.\n\n## Risk / rollback\nRisk: R1\nRollback: revert.\n\n## Trust boundary\nTrust decision: PENDING_AUTOMATION\n"""
            proc = subprocess.run([
                sys.executable, str(ROOT / ".adwf/scripts/validate_pr.py"), "--root", str(root),
                "--base-sha", base, "--head-sha", head, "--body", body,
            ], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("AUTO-AUTHORIZED BY STANDING POLICY", proc.stdout)


class FakeGateClient:
    def __init__(self, files, *, policy=None, base_current=True, body="", permission="admin", wrapped_base64=False, malformed_base64=False, content_failure=False):
        self.repo = "o/r"; self.files = files; self.policy = policy or current_policy()
        self.base_sha = "a" * 40; self.head_sha = "b" * 40
        self.base_current = base_current; self.body = body; self.permission = permission
        self.wrapped_base64 = wrapped_base64; self.malformed_base64 = malformed_base64; self.content_failure = content_failure
    def get(self, path):
        return {"id": 1, "head_sha": self.head_sha, "name": "ADWF PR", "event": "pull_request", "status": "completed", "conclusion": "success", "pull_requests": [{"number": 7}]}
    def check_runs(self, sha):
        return [{"name": "fast-feedback", "head_sha": sha, "status": "completed", "conclusion": "success", "app": {"slug": "github-actions"}}]
    def pull(self, number):
        return {"number": number, "base": {"sha": self.base_sha, "ref": "main"}, "head": {"sha": self.head_sha}, "user": {"login": "owner"}, "body": self.body}
    def pull_files(self, number):
        return self.files
    def pulls(self):
        return [self.pull(7)]
    def content(self, path, ref=None):
        if self.content_failure: raise ValueError("SIMULATED_PROVIDER_CONTENT_FAILURE")
        if path == ".adwf/policies/trust-boundary.json":
            text = json.dumps(self.policy)
        else:
            text = "VALUE = 1\n" if ref == self.base_sha else "VALUE = 2\n"
        encoded = base64.b64encode(text.encode()).decode()
        if self.malformed_base64: encoded = encoded[:-1] + "!"
        if self.wrapped_base64: encoded = "\n".join(encoded[i:i+60] for i in range(0, len(encoded), 60)) + "\n"
        return {"type": "file", "encoding": "base64", "content": encoded}
    def git_ref(self, branch):
        return {"object": {"sha": self.base_sha if self.base_current else "c" * 40}}
    def pull_reviews(self, number): return []
    def collaborator_permission(self, login): return {"permission": self.permission}


class StandingTrustedControllerTests(unittest.TestCase):
    def _safe_files(self):
        return [
            {"filename": ".adwf/scripts/platform_smoke.py", "status": "modified"},
            {"filename": ".adwf/tests/test_platform_smoke.py", "status": "modified"},
            {"filename": "MANIFEST.json", "status": "modified"},
            {"filename": "SHA256SUMS.txt", "status": "modified"},
        ]

    def test_safe_repair_is_provider_auto_authorized(self):
        result = evaluate_trusted_gate(FakeGateClient(self._safe_files()), "o/r", {"id": 1, "head_sha": "b" * 40})
        self.assertEqual(result["reasons"], [])
        self.assertTrue(result["governance"]["verified"])
        self.assertEqual(result["governance"]["approval"]["mode"], "STANDING_OWNER_POLICY")

    def test_github_wrapped_base64_is_accepted_for_provider_reconstruction(self):
        result = evaluate_trusted_gate(FakeGateClient(self._safe_files(), wrapped_base64=True), "o/r", {"id": 1, "head_sha": "b" * 40})
        self.assertEqual(result["reasons"], [])
        classification = result["governance"]["classification"]
        self.assertTrue(classification["classification_verified"])
        self.assertEqual(classification["authorization_mode"], "STANDING_OWNER_POLICY")

    def test_malformed_provider_base64_fails_as_unverified_classification_not_drift(self):
        result = evaluate_trusted_gate(FakeGateClient(self._safe_files(), malformed_base64=True), "o/r", {"id": 1, "head_sha": "b" * 40})
        self.assertIn("TRUST_BOUNDARY_CLASSIFICATION_NOT_VERIFIED", result["reasons"])
        self.assertNotIn("TRUST_POLICY_BASE_DRIFT", result["reasons"])
        self.assertEqual(result["governance"]["reason_codes"], ["GOVERNANCE_TRUST_CLASSIFICATION_NOT_VERIFIED"])
        self.assertIs(result["governance"]["classification"]["classification_verified"], False)

    def test_provider_classification_exception_is_not_projected_as_observed_base_drift(self):
        result = evaluate_trusted_gate(FakeGateClient(self._safe_files(), content_failure=True), "o/r", {"id": 1, "head_sha": "b" * 40})
        self.assertIn("TRUST_BOUNDARY_CLASSIFICATION_NOT_VERIFIED", result["reasons"])
        self.assertNotIn("TRUST_POLICY_BASE_DRIFT", result["reasons"])
        projection = result["governance"]["classification"]
        self.assertIsNone(projection["base_current"])
        self.assertEqual(projection["error_type"], "ValueError")

    def test_base_drift_invalidates_standing_authorization(self):
        result = evaluate_trusted_gate(FakeGateClient(self._safe_files(), base_current=False), "o/r", {"id": 1, "head_sha": "b" * 40})
        self.assertIn("TRUST_POLICY_BASE_DRIFT", result["reasons"])
        self.assertFalse(result["governance"]["verified"])

    def test_reserved_evaluator_still_requires_exact_human(self):
        files = [{"filename": ".adwf/lib/trust.py", "status": "modified"}]
        blocked = evaluate_trusted_gate(FakeGateClient(files), "o/r", {"id": 1, "head_sha": "b" * 40})
        self.assertIn("TRUST_BOUNDARY_CHANGE_NOT_AUTHORIZED", blocked["reasons"])
        body = "Owner-Attestation: " + "b" * 40
        allowed = evaluate_trusted_gate(FakeGateClient(files, body=body), "o/r", {"id": 1, "head_sha": "b" * 40})
        self.assertEqual(allowed["reasons"], [])
        self.assertEqual(allowed["governance"]["approval"]["mode"], "SOLO_MAINTAINER_OWNER_ATTESTATION")

    def test_forged_non_admin_owner_attestation_is_rejected(self):
        files = [{"filename": ".adwf/lib/trust.py", "status": "modified"}]
        body = "Owner-Attestation: " + "b" * 40
        result = evaluate_trusted_gate(FakeGateClient(files, body=body, permission="write"), "o/r", {"id": 1, "head_sha": "b" * 40})
        self.assertIn("TRUST_BOUNDARY_CHANGE_NOT_AUTHORIZED", result["reasons"])

    def test_policy_block_cannot_be_overridden_by_owner_attestation(self):
        files = [
            {"filename": ".adwf/lib/trust.py", "status": "modified"},
            {"filename": "src/product.py", "status": "modified"},
        ]
        body = "Owner-Attestation: " + "b" * 40
        result = evaluate_trusted_gate(FakeGateClient(files, body=body), "o/r", {"id": 1, "head_sha": "b" * 40})
        self.assertIn("TRUST_BOUNDARY_POLICY_BLOCK", result["reasons"])
        self.assertIsNone(result["governance"]["approval"])


if __name__ == "__main__":
    unittest.main()

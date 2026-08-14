import importlib.util
import io
import json
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("orchestrate_event", ROOT / ".adwf/scripts/orchestrate_event.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TrustedControllerTests(unittest.TestCase):
    def test_label_mutation_is_delegated_to_durable_cas_saga(self):
        body = "<!-- ADWF-CONTRACT Roadmap-ID: RM-7 Writer: writer-1 Writer-Lease: 123e4567-e89b-12d3-a456-426614174000 Workspace: rm-7-issue-7 State: IN_PROGRESS Heartbeat: 2099-08-13T09:30:00Z Expires: 2099-08-13T10:00:00Z -->"
        issue = {
            "number": 7, "body": body, "updated_at": "2026-08-13T12:00:00Z",
            "labels": [{"name": "roadmap:in-progress"}, {"name": "type:bug"}],
        }
        with (
            mock.patch.object(MODULE, "load_effective_policy", return_value={"policy_hash": "a" * 64}),
            mock.patch.object(MODULE, "run_transition", return_value={"status": "COMMITTED"}) as transition,
            redirect_stdout(io.StringIO()),
        ):
            MODULE.set_label("owner/repo", issue, "roadmap:review", "token", True,
                             {"scope_gate_pass": True, "tests_executed_or_na": True, "docs_impact_assessed": True, "lease_active": True})
        plan = transition.call_args.args[1]
        self.assertEqual(plan["from_label"], "roadmap:in-progress")
        self.assertEqual(plan["target_label"], "roadmap:review")
        self.assertEqual(plan["expected_updated_at"], issue["updated_at"])
        self.assertEqual(plan["policy_hash"], "a" * 64)

    def test_provider_api_diff_uses_base_policy_and_blocks_mixed_trust_change(self):
        policy = json.loads((ROOT / ".adwf/policies/trust-boundary.json").read_text(encoding="utf-8"))
        pr = {"number": 7, "base": {"sha": "a" * 40}, "head": {"sha": "b" * 40}}
        files = [
            {"filename": "src/product.py", "status": "modified"},
            {"filename": ".adwf/config.json", "status": "modified"},
        ]

        def fake_blob(repo, path, sha, token):
            if path == ".adwf/policies/trust-boundary.json":
                return json.dumps(policy)
            if sha == "a" * 40:
                return '{"policy":{"independent_review":true}}'
            return '{"policy":{"independent_review":false}}'

        with mock.patch.object(MODULE, "api", return_value=files), mock.patch.object(MODULE, "_github_blob", side_effect=fake_blob):
            result = MODULE.github_trust_classification("owner/repo", pr, "token")
        self.assertEqual(result["result"], "BLOCK")
        self.assertIn("TRUST_CHANGE_MIXED_WITH_FEATURE", result["reason_codes"])
        self.assertEqual(result["source"], "GITHUB_PROVIDER_API")

    def test_merged_pr_moves_to_verification_label(self):
        self.assertIn("roadmap:verification", MODULE.ROADMAP_LABELS)

    def test_closed_merge_signal_accepts_merge_sha_but_evidence_stays_on_pr_head(self):
        head_sha, merge_sha = "a" * 40, "b" * 40
        merged = {"state": "closed", "head": {"sha": head_sha}, "merge_commit_sha": merge_sha}
        opened = {"state": "open", "head": {"sha": head_sha}, "merge_commit_sha": merge_sha}
        self.assertTrue(MODULE.workflow_sha_valid(merged, merge_sha))
        self.assertTrue(MODULE.workflow_sha_valid(merged, head_sha))
        self.assertFalse(MODULE.workflow_sha_valid(merged, "c" * 40))
        self.assertTrue(MODULE.workflow_sha_valid(opened, head_sha))
        self.assertFalse(MODULE.workflow_sha_valid(opened, merge_sha))

    def test_closed_unmerged_pr_enters_observable_recovery(self):
        source = (ROOT / ".adwf/scripts/orchestrate_event.py").read_text(encoding="utf-8")
        self.assertIn('set_label(repo, issue, "recovery:active"', source)

    def test_ci_and_review_must_be_fresh_exact_sha_and_independent(self):
        now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
        sha = "a" * 40
        checks = [{"name": "fast-feedback", "head_sha": sha, "conclusion": "success", "completed_at": "2026-08-13T11:00:00Z"}]
        reviews = [{"user": {"login": "reviewer"}, "commit_id": sha, "state": "APPROVED", "submitted_at": "2026-08-13T11:30:00Z"}]
        self.assertTrue(MODULE.exact_ci_valid(checks, sha, now=now))
        self.assertTrue(MODULE.exact_review_valid(reviews, sha, "author", now=now))
        self.assertFalse(MODULE.exact_review_valid(reviews, sha, "reviewer", now=now))
        self.assertFalse(MODULE.exact_ci_valid(checks, "b" * 40, now=now))

    def test_lease_requires_fresh_heartbeat_and_unexpired_ttl(self):
        now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
        fresh = {"heartbeat_at": "2026-08-13T11:30:00Z", "expires_at": "2026-08-13T13:00:00Z"}
        stale = {"heartbeat_at": "2026-08-13T10:00:00Z", "expires_at": "2026-08-13T13:00:00Z"}
        future = {"heartbeat_at": "2026-08-13T12:01:00Z", "expires_at": "2026-08-13T13:00:00Z"}
        expired = {"heartbeat_at": "2026-08-13T11:30:00Z", "expires_at": "2026-08-13T12:00:00Z"}
        self.assertTrue(MODULE.lease_times_valid(fresh, now=now, stall_timeout_minutes=45))
        self.assertFalse(MODULE.lease_times_valid(stale, now=now, stall_timeout_minutes=45))
        self.assertFalse(MODULE.lease_times_valid(future, now=now, stall_timeout_minutes=45))
        self.assertFalse(MODULE.lease_times_valid(expired, now=now, stall_timeout_minutes=45))


if __name__ == "__main__":
    unittest.main()

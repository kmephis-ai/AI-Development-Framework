import base64
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from scripts import orchestrate_event as MODULE
from scripts import publish_trusted_gate as GATE
from scripts import collect_preview_attestation as PREVIEW
from lib import github_bootstrap as BOOTSTRAP


class ExactTreeGateClient:
    def __init__(self, *, base_tree=None, head_tree=None, tree_overrides=None):
        self.repo = "o/r"
        self.token = "token"
        self.base_sha = "a" * 40
        self.head_sha = "b" * 40
        self.base_tree_sha = "c" * 40
        self.head_tree_sha = "d" * 40
        self.policy = json.loads((ROOT / ".adwf/policies/trust-boundary.json").read_text(encoding="utf-8"))
        self.base_tree = list(base_tree or [])
        self.head_tree = list(head_tree or [])
        self.tree_overrides = tree_overrides or {}
        self.get_calls = []
        self.pull_files_calls = 0
    def get(self, path):
        self.get_calls.append(path)
        if path.endswith("/actions/runs/1"):
            return {"id": 1, "head_sha": self.head_sha, "name": "ADWF PR", "event": "pull_request", "status": "completed", "conclusion": "success", "pull_requests": [{"number": 7}]}
        if "/git/commits/" + self.base_sha in path:
            return {"sha": self.base_sha, "tree": {"sha": self.base_tree_sha}, "parents": []}
        if "/git/commits/" + self.head_sha in path:
            return {"sha": self.head_sha, "tree": {"sha": self.head_tree_sha}, "parents": [{"sha": self.base_sha}]}
        if "/git/trees/" + self.base_tree_sha in path:
            return self.tree_overrides.get("base", {"sha": self.base_tree_sha, "truncated": False, "tree": self.base_tree})
        if "/git/trees/" + self.head_tree_sha in path:
            return self.tree_overrides.get("head", {"sha": self.head_tree_sha, "truncated": False, "tree": self.head_tree})
        raise AssertionError("unexpected get: " + path)
    def check_runs(self, sha):
        return [{"name": "fast-feedback", "head_sha": sha, "status": "completed", "conclusion": "success", "app": {"slug": "github-actions", "id": 15368}}]
    def pull(self, number):
        return {"number": number, "base": {"sha": self.base_sha, "ref": "main"}, "head": {"sha": self.head_sha}, "user": {"login": "owner"}, "body": ""}
    def pulls(self): return [self.pull(7)]
    def pull_files(self, number):
        self.pull_files_calls += 1
        raise GATE.ProviderContractError("PROVIDER_HTTP_404")
    def content(self, path, ref=None):
        if path == ".adwf/policies/trust-boundary.json": text = json.dumps(self.policy)
        else: text = "old = True\n" if ref == self.base_sha else "new = True\n"
        return {"type": "file", "encoding": "base64", "content": base64.b64encode(text.encode()).decode()}
    def git_ref(self, branch): return {"object": {"sha": self.base_sha}}
    def pull_reviews(self, number): return []
    def collaborator_permission(self, login): return {"permission": "admin"}


def _tree_entry(path, sha, *, mode="100644", kind="blob"):
    return {"path": path, "mode": mode, "type": kind, "sha": sha}


class TrustedControllerTests(unittest.TestCase):
    def test_label_mutation_is_delegated_to_durable_cas_saga(self):
        body = "<!-- ADWF-CONTRACT Roadmap-ID: RM-7 Writer: writer-1 Writer-Lease: 123e4567-e89b-12d3-a456-426614174000 Workspace: rm-7-issue-7 State: IN_PROGRESS Heartbeat: 2099-08-13T09:30:00Z Expires: 2099-08-13T10:00:00Z -->"
        issue = {"number": 7, "body": body, "updated_at": "2026-08-13T12:00:00Z", "labels": [{"name": "roadmap:in-progress"}, {"name": "type:bug"}]}
        with mock.patch.object(MODULE, "load_effective_policy", return_value={"policy_hash": "a" * 64}), mock.patch.object(MODULE, "run_transition", return_value={"status": "COMMITTED"}) as transition, redirect_stdout(io.StringIO()):
            MODULE.set_label("owner/repo", issue, "roadmap:review", "token", True, {"scope_gate_pass": True, "tests_executed_or_na": True, "docs_impact_assessed": True, "lease_active": True})
        plan = transition.call_args.args[1]
        self.assertEqual(plan["from_label"], "roadmap:in-progress")
        self.assertEqual(plan["target_label"], "roadmap:review")
        self.assertEqual(plan["expected_updated_at"], issue["updated_at"])
        self.assertEqual(plan["policy_hash"], "a" * 64)

    def test_provider_api_diff_uses_base_policy_and_blocks_mixed_trust_change(self):
        policy = json.loads((ROOT / ".adwf/policies/trust-boundary.json").read_text(encoding="utf-8"))
        pr = {"number": 7, "base": {"sha": "a" * 40, "ref": "main"}, "head": {"sha": "b" * 40}}
        files = [{"filename": "src/product.py", "status": "modified"}, {"filename": ".adwf/config.json", "status": "modified"}]
        def fake_blob(repo, path, sha, token):
            if path == ".adwf/policies/trust-boundary.json": return json.dumps(policy)
            return '{"policy":{"independent_review":true}}' if sha == "a" * 40 else '{"policy":{"independent_review":false}}'
        def fake_api(method, url, token, data=None):
            if "/git/ref/heads/main" in url:
                return {"object": {"sha": "a" * 40}}
            return files
        with mock.patch.object(MODULE, "api", side_effect=fake_api), mock.patch.object(MODULE, "_github_blob", side_effect=fake_blob):
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
        now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc); sha = "a" * 40
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

    def test_trusted_gate_recovers_merged_pr_by_exact_head_sha(self):
        sha = "a" * 40; client = mock.Mock()
        client.pulls.return_value = [{"number": 3, "head": {"sha": "b" * 40}}, {"number": 7, "head": {"sha": sha}}]
        self.assertEqual(GATE._pull_number_for_sha(client, {"pull_requests": []}, sha), 7)

    def test_solo_maintainer_attestation_is_exact_sha_and_admin_bound(self):
        sha = "a" * 40; client = mock.Mock(); client.collaborator_permission.return_value = {"permission": "admin"}
        pr = {"user": {"login": "owner"}, "body": f"Owner-Attestation: {sha}"}
        result = GATE._owner_exact_head_attestation(client, pr, sha)
        self.assertTrue(result["verified"]); self.assertEqual(result["kind"], "SOLO_MAINTAINER_OWNER_ATTESTATION")
        self.assertFalse(GATE._owner_exact_head_attestation(client, pr, "b" * 40)["verified"])

    def test_solo_maintainer_attestation_requires_admin_identity(self):
        sha = "a" * 40; client = mock.Mock(); client.collaborator_permission.return_value = {"permission": "write"}
        pr = {"user": {"login": "owner"}, "body": f"Owner-Attestation: {sha}"}
        self.assertFalse(GATE._owner_exact_head_attestation(client, pr, sha)["verified"])

    def test_preview_log_provider_failure_is_structured_and_fail_closed(self):
        sha = "a" * 40; client = mock.Mock()
        client.check_runs.return_value = [{"name": name, "head_sha": sha, "status": "completed", "conclusion": "success", "app": {"slug": "github-actions", "id": 15368}} for name in ("fast-feedback", "adwf/governance-gate", "adwf/trusted-gate")]
        client.jobs.return_value = [{"id": 123, "name": "fast-feedback", "status": "completed", "conclusion": "success", "steps": [{"name": PREVIEW.PREVIEW_STEP, "conclusion": "success"}]}]
        client.job_logs.side_effect = PREVIEW.ProviderContractError("PROVIDER_HTTP_401")
        event = {"workflow_run": {"name": "ADWF PR", "event": "pull_request", "status": "completed", "conclusion": "success", "head_sha": sha, "id": 55}}
        result = PREVIEW.collect(client, event)
        self.assertEqual(result["status"], "NOT_VERIFIED")
        self.assertEqual(result["reason"], "PREVIEW_LOG_READBACK_UNAVAILABLE")
        self.assertNotIn("provider_error", result)

    def test_preview_skip_is_not_applicable_without_job_log_access(self):
        sha = "a" * 40; client = mock.Mock()
        client.check_runs.return_value = [{"name": name, "head_sha": sha, "status": "completed", "conclusion": "success", "app": {"slug": "github-actions", "id": 15368}} for name in ("fast-feedback", "adwf/governance-gate", "adwf/trusted-gate")]
        client.jobs.return_value = [{"id": 123, "name": "fast-feedback", "status": "completed", "conclusion": "success", "steps": [{"name": PREVIEW.PREVIEW_STEP, "conclusion": "skipped"}]}]
        event = {"workflow_run": {"name": "ADWF PR", "event": "pull_request", "status": "completed", "conclusion": "success", "head_sha": sha, "id": 55}}
        result = PREVIEW.collect(client, event)
        self.assertEqual(result["status"], "NOT_APPLICABLE"); self.assertEqual(result["reason"], "PREVIEW_STEP_NOT_RUN")
        client.job_logs.assert_not_called()

    def test_exact_tree_diff_reconstructs_add_modify_delete_and_mode_change(self):
        same = "1" * 40; old = "2" * 40; new = "3" * 40; removed = "4" * 40; added = "5" * 40; mode_sha = "6" * 40
        client = ExactTreeGateClient(
            base_tree=[_tree_entry("same.txt", same), _tree_entry(".adwf/config.json", old), _tree_entry("removed.txt", removed), _tree_entry("mode.txt", mode_sha, mode="100644")],
            head_tree=[_tree_entry("same.txt", same), _tree_entry(".adwf/config.json", new), _tree_entry("added.txt", added), _tree_entry("mode.txt", mode_sha, mode="100755")],
        )
        records = GATE._provider_diff_records(client, client.pull(7), [".adwf/**"])
        self.assertEqual([(r["path"], r["status"]) for r in records], [(".adwf/config.json", "M"), ("added.txt", "A"), ("mode.txt", "M"), ("removed.txt", "D")])
        protected = records[0]
        self.assertIsNotNone(protected["old_text"]); self.assertIsNotNone(protected["new_text"])
        self.assertEqual(client.pull_files_calls, 0)

    def test_exact_tree_readback_rejects_truncation_duplicate_unsafe_mode_type_and_sha(self):
        tree_sha = "c" * 40
        bad_payloads = [
            {"sha": tree_sha, "truncated": True, "tree": []},
            {"sha": tree_sha, "truncated": False, "tree": [_tree_entry("x", "1" * 40), _tree_entry("x", "2" * 40)]},
            {"sha": tree_sha, "truncated": False, "tree": [_tree_entry("../escape", "1" * 40)]},
            {"sha": tree_sha, "truncated": False, "tree": [_tree_entry("x", "1" * 40, mode="100600")]},
            {"sha": tree_sha, "truncated": False, "tree": [_tree_entry("x", "1" * 40, kind="tag")]},
            {"sha": tree_sha, "truncated": False, "tree": [_tree_entry("x", "not-a-sha")]},
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                client = ExactTreeGateClient(tree_overrides={"base": payload})
                with self.assertRaises(ValueError): GATE._provider_tree_files(client, tree_sha)

    def test_exact_git_object_identity_mismatch_fails_closed(self):
        client = ExactTreeGateClient()
        original = client.get
        def bad_commit(path):
            if "/git/commits/" + client.head_sha in path:
                return {"sha": "e" * 40, "tree": {"sha": client.head_tree_sha}, "parents": [{"sha": client.base_sha}]}
            return original(path)
        client.get = bad_commit
        with self.assertRaisesRegex(ValueError, "GIT_COMMIT_READBACK_SHA_MISMATCH"):
            GATE._provider_diff_records(client, client.pull(7), [".adwf/**"])

        client = ExactTreeGateClient(tree_overrides={"head": {"sha": "e" * 40, "truncated": False, "tree": []}})
        with self.assertRaisesRegex(ValueError, "GIT_TREE_READBACK_SHA_MISMATCH"):
            GATE._provider_diff_records(client, client.pull(7), [".adwf/**"])

    def test_exact_tree_diff_requires_base_ancestor_and_bounded_parent_walk(self):
        client = ExactTreeGateClient()
        def no_ancestor(path):
            if "/git/commits/" + client.head_sha in path:
                return {"sha": client.head_sha, "tree": {"sha": client.head_tree_sha}, "parents": []}
            return ExactTreeGateClient.get(client, path)
        client.get = no_ancestor
        with self.assertRaisesRegex(ValueError, "PR_BASE_NOT_ANCESTOR_OF_HEAD"):
            GATE._provider_diff_records(client, client.pull(7), [".adwf/**"])

        client = ExactTreeGateClient()
        middle = "e" * 40
        def long_walk(path):
            if "/git/commits/" + client.head_sha in path:
                return {"sha": client.head_sha, "tree": {"sha": client.head_tree_sha}, "parents": [{"sha": middle}]}
            if "/git/commits/" + middle in path:
                return {"sha": middle, "tree": {"sha": "f" * 40}, "parents": [{"sha": client.base_sha}]}
            return ExactTreeGateClient.get(client, path)
        client.get = long_walk
        with mock.patch.object(GATE, "_MAX_ANCESTRY_COMMITS", 1), self.assertRaisesRegex(ValueError, "PR_ANCESTRY_INSPECTION_LIMIT"):
            GATE._provider_diff_records(client, client.pull(7), [".adwf/**"])

    def test_exact_tree_diff_rejects_more_than_3000_changed_paths(self):
        client = ExactTreeGateClient(head_tree=[_tree_entry(f"src/f{i}.py", f"{i:040x}") for i in range(1, 3002)])
        with self.assertRaisesRegex(ValueError, "PR_DIFF_INSPECTION_INVALID"):
            GATE._provider_diff_records(client, client.pull(7), [".adwf/**"])

    def test_tree_effect_rename_projection_is_never_less_conservative(self):
        policy = json.loads((ROOT / ".adwf/policies/trust-boundary.json").read_text(encoding="utf-8"))
        cases = [
            ({"path": ".adwf/lib/helper.py", "old_path": "src/helper.py", "status": "R", "old_text": "x=1\n", "new_text": "x=1\n"}, [
                {"path": "src/helper.py", "status": "D", "old_text": None, "new_text": None},
                {"path": ".adwf/lib/helper.py", "status": "A", "old_text": None, "new_text": "x=1\n"},
            ]),
            ({"path": "src/helper.py", "old_path": ".adwf/lib/helper.py", "status": "R", "old_text": "x=1\n", "new_text": "x=1\n"}, [
                {"path": ".adwf/lib/helper.py", "status": "D", "old_text": "x=1\n", "new_text": None},
                {"path": "src/helper.py", "status": "A", "old_text": None, "new_text": None},
            ]),
        ]
        severity = {"ALLOW": 0, "HUMAN_REQUIRED": 1, "BLOCK": 2}
        from lib.trust import classify_diff
        for canonical, projected in cases:
            with self.subTest(canonical=canonical):
                expected = classify_diff([canonical], policy)
                actual = classify_diff(projected, policy)
                self.assertGreaterEqual(severity[actual["result"]], severity[expected["result"]])

    def test_trusted_classification_no_longer_depends_on_pull_files(self):
        client = ExactTreeGateClient(head_tree=[_tree_entry("src/app.py", "2" * 40)])
        result = GATE._provider_trust_classification(client, client.pull(7))
        self.assertTrue(result["classification_verified"])
        self.assertEqual(result["diff_source"], "EXACT_GIT_TREES")
        self.assertEqual(client.pull_files_calls, 0)

    def test_tree_reconstruction_failure_is_bounded_before_gate_publication(self):
        client = ExactTreeGateClient(tree_overrides={"head": {"sha": "d" * 40, "truncated": True, "tree": []}})
        result = GATE.evaluate_trusted_gate(client, client.repo, {"id": 1, "head_sha": client.head_sha})
        self.assertIn("TRUST_BOUNDARY_CLASSIFICATION_NOT_VERIFIED", result["reasons"])
        self.assertIn("CAPABILITY_LIVE_EVIDENCE_PROVIDER_NOT_VERIFIED", result["reasons"])
        self.assertEqual(result["live_evidence"]["reason_codes"], ["LIVE_CERT_DIFF_NOT_VERIFIED"])
        self.assertEqual(client.pull_files_calls, 0)

        event = {"workflow_run": {"id": 1, "head_sha": client.head_sha}}
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "event.json"; event_path.write_text(json.dumps(event), encoding="utf-8")
            published = []
            with mock.patch.object(GATE, "GitHubClient", return_value=client), mock.patch.object(GATE, "workflow_run_from_event", return_value=event["workflow_run"]), mock.patch.object(GATE, "_publish", side_effect=lambda *args: published.append(args)), mock.patch.dict("os.environ", {"GITHUB_TOKEN": "token", "GITHUB_REPOSITORY": client.repo}, clear=False), mock.patch.object(sys, "argv", ["publish_trusted_gate.py", "--event", str(event_path)]):
                self.assertEqual(GATE.main(), 1)
            self.assertEqual([call[1] for call in published], ["adwf/governance-gate", "adwf/trusted-gate"])
            self.assertTrue(all(call[3] is False for call in published))

    def test_framework_self_host_does_not_require_product_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / ".adwf").mkdir()
            (root / ".adwf/config.json").write_text(json.dumps({"project": {"type": "framework", "runtime_product": False}}), encoding="utf-8")
            self.assertTrue(BOOTSTRAP._framework_self_host(root))
            (root / ".adwf/config.json").write_text(json.dumps({"project": {"type": "service", "runtime_product": True}}), encoding="utf-8")
            self.assertFalse(BOOTSTRAP._framework_self_host(root))

    def test_control_workflow_does_not_mask_certification_critical_steps(self):
        text = (ROOT / ".github/workflows/adwf-control.yml").read_text(encoding="utf-8")
        critical = text.split("- name: Publish trusted exact-HEAD and governance gates", 1)[1].split("- name: Convert failed CI provider event into durable result", 1)[0]
        self.assertNotIn("continue-on-error: true", critical)


if __name__ == "__main__":
    unittest.main()

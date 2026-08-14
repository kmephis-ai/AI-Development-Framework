import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.evidence import append_evidence_event, evidence_graph_paths, read_evidence_graph, verify_product_evidence
from lib.health import product_health
from lib.leases import heartbeat, reconcile
from lib.orchestration import authorize_next_action


NOW = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)
SHA = "a" * 40


class P0AuthorizationTests(unittest.TestCase):
    def policy(self, *, control="VERIFIED", product="VERIFIED", provider_allowed=True):
        return {
            "action": "claim", "autonomy": "A2", "risk": "R0", "max_autonomous_risk": "R1",
            "health": {"package_integrity": "VERIFIED", "config_health": "VERIFIED",
                       "control_plane_health": control, "product_health": product},
            "provider_allowed": provider_allowed, "provider_potentially_paid": False,
            "projected_cost": 0,
        }

    @staticmethod
    def issue(state="IN_PROGRESS", work_type="feature"):
        leased = state in {"CLAIMED", "IN_PROGRESS", "REVIEW", "VERIFICATION", "RECOVERY"}
        return {"id": "RM-1", "roadmap_id": "RM-1", "state": state, "priority": "P1", "risk": "R1",
                "type": work_type, "conflict_domains": ["core"], "dependencies_resolved": True,
                "human_required": False, "autonomy_allowed": True, "roadmap_order": 1,
                "ready_since": "2026-08-13T10:00:00Z",
                "lease_id": "123e4567-e89b-12d3-a456-426614174000" if leased else None,
                "workspace_id": "WS-RM-1" if leased else None}

    @staticmethod
    def lease(*, heartbeat_at="2026-08-13T11:45:00Z", expires_at="2026-08-13T13:00:00Z", workspace="ACTIVE"):
        return {"lease_id": "123e4567-e89b-12d3-a456-426614174000", "issue_id": "RM-1",
                "status": "ACTIVE", "heartbeat_at": heartbeat_at, "expires_at": expires_at,
                "workspace_status": workspace, "workspace_id": "WS-RM-1", "conflict_domains": ["core"]}

    def test_original_fail_open_case_is_now_blocked(self):
        queue = {"leases": [self.lease(heartbeat_at="2000-01-01T00:00:00Z", expires_at="2099-01-01T00:00:00Z")],
                 "issues": [self.issue()]}
        result = authorize_next_action(queue, self.policy(control="BROKEN", product="BROKEN"), now=NOW)
        self.assertEqual(result["action"], "RECONCILE")
        self.assertEqual(result["result"], "BLOCK")
        self.assertIn("LEASE_HEARTBEAT_STALE", result["reason_codes"])

    def test_fresh_existing_work_still_needs_safe_health(self):
        result = authorize_next_action({"leases": [self.lease()], "issues": [self.issue()]},
                                       self.policy(control="BROKEN"), now=NOW)
        self.assertEqual(result["action"], "BLOCKED")
        self.assertIn("HEALTH_NOT_SAFE:control_plane_health", result["reason_codes"])

    def test_provider_guard_applies_to_recovery_too(self):
        result = authorize_next_action({"leases": [], "issues": [self.issue("RECOVERY", "recovery")]},
                                       self.policy(control="BROKEN", product="BROKEN", provider_allowed=False), now=NOW)
        self.assertEqual(result["action"], "BLOCKED")
        self.assertIn("PROVIDER_NOT_ALLOWED", result["reason_codes"])

    def test_recovery_can_proceed_with_broken_product_but_safe_package_and_provider(self):
        result = authorize_next_action({"leases": [], "issues": [self.issue("RECOVERY", "recovery")]},
                                       self.policy(control="BROKEN", product="BROKEN"), now=NOW)
        self.assertEqual(result["action"], "CONTINUE_REVIEW_OR_RECOVERY")
        self.assertEqual(result["result"], "ALLOW")

    def test_claim_is_authorized_only_after_common_preflight(self):
        issue = self.issue("READY")
        allowed = authorize_next_action({"leases": [], "issues": [issue]}, self.policy(), now=NOW)
        self.assertEqual(allowed["action"], "CLAIM_ONE_READY")
        blocked_policy = self.policy()
        blocked_policy["projected_cost"] = 0.01
        blocked = authorize_next_action({"leases": [], "issues": [issue]}, blocked_policy, now=NOW)
        self.assertEqual(blocked["action"], "BLOCKED")
        self.assertIn("PROJECTED_COST_NOT_ZERO", blocked["reason_codes"])

    def test_stale_lease_cannot_be_renewed_and_reconciles_to_recovery(self):
        lease = self.lease(heartbeat_at="2026-08-13T10:00:00Z")
        with self.assertRaisesRegex(ValueError, "LEASE_HEARTBEAT_STALE"):
            heartbeat(lease, "writer-1", now=NOW)
        queue = {"leases": [lease], "issues": [self.issue()]}
        updated, expired = reconcile(queue, now=NOW)
        self.assertEqual(expired, [lease["lease_id"]])
        self.assertEqual(updated["issues"][0]["state"], "RECOVERY")

        review_queue = {"leases": [lease], "issues": [self.issue("REVIEW")]}
        review_updated, _ = reconcile(review_queue, now=NOW)
        self.assertEqual(review_updated["issues"][0]["state"], "RECOVERY")

    def test_orphaned_active_issue_and_lease_identity_drift_require_reconciliation(self):
        orphan = authorize_next_action(
            {"leases": [], "issues": [self.issue()]}, self.policy(), now=NOW
        )
        self.assertEqual(orphan["action"], "RECONCILE")
        self.assertIn("ACTIVE_ISSUE_WITHOUT_LEASE", orphan["reason_codes"])

        issue = self.issue()
        issue["lease_id"] = "different-lease"
        drift = authorize_next_action(
            {"leases": [self.lease()], "issues": [issue]}, self.policy(), now=NOW
        )
        self.assertEqual(drift["action"], "RECONCILE")
        self.assertIn("LEASE_IDENTITY_SPLIT_BRAIN", drift["reason_codes"])

    def test_malformed_queue_and_ambiguous_cost_fail_closed_without_exception(self):
        malformed = authorize_next_action(
            {"leases": ["not-an-object"], "issues": []}, self.policy(), now=NOW
        )
        self.assertEqual(malformed["result"], "BLOCK")
        self.assertIn("QUEUE_LEASES_INVALID", malformed["reason_codes"])

        issue = self.issue("READY")
        context = self.policy()
        context["projected_cost"] = "0"
        invalid_cost = authorize_next_action(
            {"leases": [], "issues": [issue]}, context, now=NOW
        )
        self.assertEqual(invalid_cost["result"], "BLOCK")
        self.assertIn("PROJECTED_COST_INVALID", invalid_cost["reason_codes"])


class P0EvidenceGraphTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        schema_dir = self.root / ".adwf/schemas"
        schema_dir.mkdir(parents=True)
        for name in ("evidence-event.schema.json", "evidence-index.schema.json"):
            shutil.copy2(ROOT / ".adwf/schemas" / name, schema_dir / name)
        (self.root / ".adwf").mkdir(exist_ok=True)
        (self.root / ".adwf/config.json").write_text(json.dumps({
            "reality": {"required_product_gates": ["smoke", "golden_paths"], "reality_check_ttl_hours": 168}
        }), encoding="utf-8")
        self.write_state()

    def tearDown(self):
        self.temp.cleanup()

    def write_state(self, *, sha=SHA):
        state = {
            "health": {"product": "VERIFIED"},
            "main": {"head": sha, "health": "PASS"},
            "runtime": {"canonical_revision": sha, "smoke": "PASS", "golden_paths": "PASS",
                        "last_reality_check": "2026-08-13T11:00:00Z"},
        }
        (self.root / ".adwf/project-state.json").write_text(json.dumps(state), encoding="utf-8")

    def record(self, kind, sequence, *, status="PASS", trust_domain="adwf-runtime-verifier", source_type="RUNTIME", ident=None):
        artifact = Path(".adwf-runtime/artifacts") / f"{kind.lower()}-{sequence}.json"
        target = self.root / artifact
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"kind": kind, "status": status, "sequence": sequence}), encoding="utf-8")
        created = datetime(2026, 8, 13, 10, tzinfo=timezone.utc) + timedelta(minutes=sequence)
        return {
            "id": ident or f"EV-{kind}-{sequence:04d}", "kind": kind, "status": status,
            "subject": f"runtime {kind} {SHA}", "sha": SHA, "source": "runtime-probe",
            "source_type": source_type, "command": ["runtime-probe", kind.lower()],
            "runner": "runtime-verifier", "created_at": created.isoformat(),
            "expires_at": (created + timedelta(hours=4)).isoformat(),
            "content_sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "artifact": str(artifact),
            "runtime_revision": SHA, "product_impact": True,
            "provenance": {"provider": "local", "source_identity": "runtime-verifier-1",
                           "trust_domain": trust_domain, "repository": "owner/project",
                           "workflow": "runtime-certification", "invocation_id": f"run-{sequence}"},
        }

    def append_required(self):
        output = []
        for sequence, kind in enumerate(("SMOKE", "GOLDEN_PATHS", "REALITY"), 1):
            output.append(append_evidence_event(self.root, self.record(kind, sequence), now=NOW))
        return output

    def test_manual_pass_strings_without_graph_never_verify_product(self):
        result = product_health(self.root, now=NOW)
        self.assertEqual(result["status"], "NOT_VERIFIED")
        self.assertIn("EVIDENCE_LOG_MISSING", result["findings"])

    def test_append_only_chain_and_index_create_verified_product_projection(self):
        events = self.append_required()
        graph = read_evidence_graph(self.root, now=NOW)
        self.assertTrue(graph["valid"], graph)
        self.assertEqual(graph["index"]["event_count"], 3)
        self.assertIsNone(events[0]["previous_event_sha256"])
        self.assertEqual(events[1]["previous_event_sha256"], events[0]["event_sha256"])
        result = product_health(self.root, now=NOW)
        self.assertEqual(result["status"], "VERIFIED", result)

    def test_artifact_digest_tamper_invalidates_product_health(self):
        self.append_required()
        (self.root / ".adwf-runtime/artifacts/smoke-1.json").write_text("tampered", encoding="utf-8")
        result = verify_product_evidence(self.root, expected_sha=SHA,
                                         required_kinds={"SMOKE", "GOLDEN_PATHS", "REALITY"}, now=NOW)
        self.assertFalse(result["valid"])
        self.assertTrue(any("CONTENT_HASH_MISMATCH" in item for item in result["errors"]), result)

    def test_index_or_chain_tamper_is_broken_not_pass(self):
        self.append_required()
        _, index_path, _ = evidence_graph_paths(self.root)
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["event_count"] = 999
        index_path.write_text(json.dumps(index), encoding="utf-8")
        result = product_health(self.root, now=NOW)
        self.assertEqual(result["status"], "BROKEN")
        self.assertIn("EVIDENCE_INDEX_PROJECTION_MISMATCH", result["findings"])

    def test_untrusted_provenance_cannot_establish_product_health(self):
        append_evidence_event(self.root, self.record("SMOKE", 1, trust_domain="untrusted"), now=NOW)
        append_evidence_event(self.root, self.record("GOLDEN_PATHS", 2), now=NOW)
        append_evidence_event(self.root, self.record("REALITY", 3), now=NOW)
        result = product_health(self.root, now=NOW)
        self.assertEqual(result["status"], "NOT_VERIFIED")
        self.assertTrue(any("TRUST_DOMAIN_NOT_APPROVED" in item for item in result["findings"]), result)

    def test_latest_failure_supersedes_older_pass(self):
        self.append_required()
        append_evidence_event(self.root, self.record("SMOKE", 4, status="FAIL"), now=NOW)
        result = product_health(self.root, now=NOW)
        self.assertEqual(result["status"], "BROKEN", result)

    def test_duplicate_event_id_and_time_rewind_are_rejected(self):
        first = self.record("SMOKE", 1, ident="EV-DUPLICATE")
        append_evidence_event(self.root, first, now=NOW)
        with self.assertRaisesRegex(ValueError, "ID_DUPLICATE"):
            append_evidence_event(self.root, self.record("REALITY", 2, ident="EV-DUPLICATE"), now=NOW)
        rewind = self.record("REALITY", 0)
        with self.assertRaisesRegex(ValueError, "TIME_REWIND"):
            append_evidence_event(self.root, rewind, now=NOW)

    def test_index_without_log_cannot_be_silently_replaced(self):
        _, index_path, _ = evidence_graph_paths(self.root)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "LOG_MISSING_WITH_INDEX"):
            append_evidence_event(self.root, self.record("SMOKE", 1), now=NOW)

    def test_policy_bounded_ttl_and_latest_runtime_mismatch_block_old_pass(self):
        self.append_required()
        long_lived = self.record("SMOKE", 4)
        created = datetime.fromisoformat(long_lived["created_at"])
        long_lived["expires_at"] = (created + timedelta(days=365)).isoformat()
        append_evidence_event(self.root, long_lived, now=NOW)
        ttl_result = product_health(self.root, now=NOW)
        self.assertEqual(ttl_result["status"], "NOT_VERIFIED")
        self.assertTrue(any("TTL_EXCEEDS_POLICY" in item for item in ttl_result["findings"]), ttl_result)

        # A newer current-SHA record with a mismatched runtime cannot be hidden
        # behind the older valid PASS for the same gate.
        runtime_mismatch = self.record("SMOKE", 5)
        runtime_mismatch["runtime_revision"] = "b" * 40
        append_evidence_event(self.root, runtime_mismatch, now=NOW)
        mismatch_result = product_health(self.root, now=NOW)
        self.assertEqual(mismatch_result["status"], "NOT_VERIFIED")
        self.assertTrue(any("RUNTIME_REVISION_MISMATCH" in item for item in mismatch_result["findings"]), mismatch_result)


if __name__ == "__main__":
    unittest.main()

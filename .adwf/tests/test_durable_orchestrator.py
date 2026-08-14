import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.durable_orchestrator import OrchestrationJournal, advance_run, new_run, validate_journal
from lib.policy_compiler import compile_policy


SAFE = {
    "autonomy": "A3",
    "max_autonomous_risk": "R1",
    "health": {
        "package_integrity": "VERIFIED",
        "config_health": "VERIFIED",
        "control_plane_health": "VERIFIED",
        "product_health": "VERIFIED",
    },
    "gates": {"ci": "PASS", "review": "PASS"},
    "required_gates": ["ci", "review"],
    "exact_sha": True,
    "evidence_fresh": True,
    "human_approved": False,
    "destructive": False,
    "trust_change": False,
    "writer_conflict": False,
    "provider_allowed": True,
    "provider_potentially_paid": False,
    "provider_facts_fresh": True,
}


class DurableOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".adwf").mkdir()
        policy, errors = compile_policy(ROOT)
        self.assertEqual(errors, [])
        (self.root / ".adwf/effective-policy.json").write_text(
            json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self.run = new_run(
            self.root,
            roadmap_id="RM-1",
            issue_id="1",
            risk="R1",
            work_type="feature",
            product_impact=True,
            owner_request_digest="a" * 64,
            run_id="run-00000001",
            max_attempts=1,
        )

    def tearDown(self):
        self.temp.cleanup()

    def result(self, phase, outcome="PASS", **extra):
        value = {
            "phase": phase,
            "outcome": outcome,
            "idempotency_key": f"{phase}-{outcome}-0001",
            "evidence_refs": [],
            "reason_codes": [],
            "cost_usd": 0,
            "metadata": {},
        }
        value.update(extra)
        return value

    def advance(self, result, context=None):
        return advance_run(self.root, self.run["run_id"], result, context or SAFE)

    def test_duplicate_step_is_idempotent(self):
        result = self.result("RECONCILE")
        first = self.advance(result)
        second = self.advance(result)
        self.assertEqual(first["phase"], "AUTHORIZE")
        self.assertEqual(len(second["events"]), 1)

    def test_full_product_path_requires_preview_and_owner_acceptance(self):
        sha = "b" * 40
        phases = ["RECONCILE", "AUTHORIZE", "CLAIM", "WORKSPACE", "EXECUTE"]
        for phase in phases:
            state = self.advance(self.result(phase))
        state = self.advance(self.result("OPEN_PR", subject_sha=sha))
        state = self.advance(self.result("CI", subject_sha=sha, evidence_refs=["ev-ci"]))
        state = self.advance(self.result("REVIEW", subject_sha=sha, evidence_refs=["ev-review"]))
        state = self.advance(self.result("PREVIEW", subject_sha=sha, preview_digest="c" * 64, evidence_refs=["ev-preview"]))
        self.assertEqual(state["phase"], "OWNER_ACCEPTANCE")
        owner_context = dict(SAFE)
        owner_context["human_approved"] = True
        state = self.advance(self.result(
            "OWNER_ACCEPTANCE",
            subject_sha=sha,
            metadata={"owner_acceptance_exact": True, "accepted_preview_digest": "c" * 64},
        ), owner_context)
        self.assertEqual(state["phase"], "MERGE")
        self.assertEqual(state["owner_acceptance_sha"], sha)

    def test_deterministic_failure_enters_recovery_without_retry(self):
        self.advance(self.result("RECONCILE"))
        state = self.advance(self.result("AUTHORIZE", outcome="FAIL", reason_codes=["CONFIG_DRIFT"]))
        self.assertEqual(state["phase"], "RECOVERY")
        self.assertEqual(state["last_failed_phase"], "AUTHORIZE")

    def test_non_transient_retry_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "RETRY_REQUIRES_TRANSIENT"):
            self.advance(self.result("RECONCILE", outcome="RETRY", transient=False))

    def test_retry_budget_opens_recovery(self):
        state = self.advance(self.result("RECONCILE", outcome="RETRY", transient=True))
        self.assertEqual(state["status"], "RETRY_WAIT")
        state = self.advance(self.result(
            "RECONCILE", outcome="RETRY", transient=True,
            idempotency_key="RECONCILE-RETRY-0002",
        ))
        self.assertEqual(state["phase"], "RECOVERY")
        self.assertIn("RETRY_BUDGET_EXHAUSTED", state["blockers"])

    def test_non_zero_cost_is_rejected_before_event(self):
        with self.assertRaisesRegex(ValueError, "NON_ZERO_COST"):
            self.advance(self.result("RECONCILE", cost_usd=0.01))
        self.assertEqual(len(OrchestrationJournal(self.root).load(self.run["run_id"])["events"]), 0)

    def test_event_chain_detects_tampering(self):
        self.advance(self.result("RECONCILE"))
        state = OrchestrationJournal(self.root).load(self.run["run_id"])
        self.assertEqual(validate_journal(state), [])
        state["events"][0]["outcome"] = "FAIL"
        self.assertTrue(any(item.startswith("EVENT_HASH") for item in validate_journal(state)))

    def test_new_run_is_blocked_while_one_is_active(self):
        with self.assertRaisesRegex(ValueError, "ACTIVE_OR_BROKEN"):
            new_run(
                self.root, roadmap_id="RM-2", issue_id="2", risk="R0",
                work_type="feature", product_impact=False,
                owner_request_digest="d" * 64, run_id="run-00000002",
            )

    def test_expired_time_budget_blocks_without_executing_step(self):
        journal = OrchestrationJournal(self.root)
        state = journal.load(self.run["run_id"])
        state["deadline_at"] = "2000-01-01T00:00:00Z"
        journal.save(state, expected_revision=state["revision"])
        blocked = self.advance(self.result("RECONCILE"))
        self.assertEqual(blocked["status"], "BLOCKED")
        self.assertIn("ORCHESTRATION_TIME_BUDGET_EXHAUSTED", blocked["blockers"])
        self.assertEqual(blocked["events"], [])


if __name__ == "__main__":
    unittest.main()

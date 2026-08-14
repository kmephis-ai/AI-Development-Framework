import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.healing import (
    authorize_h6,
    evaluate_healing,
    record_heal_outcome,
    transition_recipe,
    verify_h4,
)


class SafeHealingTests(unittest.TestCase):
    fingerprint = "f" * 64
    policy_hash = "a" * 64
    now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)

    def setUp(self):
        self.config = json.loads((ROOT / ".adwf/healing-config.json").read_text(encoding="utf-8"))

    def incident(self, classification="TEST_ASSERTION"):
        return {"fingerprint": {"hash": self.fingerprint}, "classification": {"type": classification}}

    def recipe(self, *, recipe_id="REC-TEST_FIX", version="1.0.0", writes=None, argv=None, lifecycle="ACTIVE"):
        certification = {
            "shadow_passes": 2 if lifecycle in {"CERTIFIED", "ACTIVE"} else 0,
            "independent_approval": lifecycle in {"CERTIFIED", "ACTIVE"},
            "certified_at": "2026-08-12T10:00:00Z" if lifecycle in {"CERTIFIED", "ACTIVE"} else None,
        }
        return {
            "recipe_id": recipe_id,
            "version": version,
            "title_ru": "Безопасное тестовое исправление",
            "lifecycle": lifecycle,
            "match": {"fingerprint_hash": self.fingerprint},
            "compatibility": {
                "policy_hashes": [self.policy_hash],
                "tool_versions": {"unittest": "stdlib"},
                "runtime_versions": {"python": "3.12.13"},
            },
            "expires_at": "2027-08-13T00:00:00Z",
            "actions": [{
                "action_id": "apply_fix", "level": "H3",
                "argv": argv or ["python3", "tools/repair.py"],
                "writes": ["src/service.py"] if writes is None else writes,
                "idempotent": True, "destructive": False, "network": False,
                "requires_ai_api": False, "monetary_cost": 0,
            }],
            "verification": {"targeted": [["python3", "-m", "unittest"]], "full_gates": True},
            "certification": certification,
            "lifecycle_history": [],
        }

    def context(self):
        return {
            "policy_result": "ALLOW", "policy_hash": self.policy_hash,
            "tool_versions": {"unittest": "stdlib"},
            "runtime_versions": {"python": "3.12.13"},
            "attempts": 0, "elapsed_seconds": 0, "changed_files": 0,
            "monetary_cost": 0, "ai_api_required": False,
        }

    def test_transient_ladder_is_bounded_h1_then_h2_then_h5(self):
        registry = {"schema_version": 1, "recipes": []}
        first = evaluate_healing(self.incident("RUNNER_INTERRUPTED"), registry, self.config, self.context(), now=self.now)
        second_context = self.context()
        second_context["attempts"] = 1
        second = evaluate_healing(self.incident("RUNNER_INTERRUPTED"), registry, self.config, second_context, now=self.now)
        exhausted_context = self.context()
        exhausted_context["attempts"] = self.config["budgets"]["max_attempts"]
        exhausted = evaluate_healing(self.incident("RUNNER_INTERRUPTED"), registry, self.config, exhausted_context, now=self.now)
        self.assertEqual((first["level"], second["level"], exhausted["level"]), ("H1", "H2", "H5"))
        self.assertFalse(first["mandatory_ai_api"])
        self.assertEqual(first["monetary_budget"], 0)

    def test_active_recipe_requires_exact_policy_and_versions(self):
        registry = {"schema_version": 1, "recipes": [self.recipe()]}
        allowed = evaluate_healing(self.incident(), registry, self.config, self.context(), now=self.now)
        mismatched = self.context()
        mismatched["runtime_versions"]["python"] = "3.13.0"
        blocked = evaluate_healing(self.incident(), registry, self.config, mismatched, now=self.now)
        self.assertEqual((allowed["level"], allowed["result"], allowed["sandbox_only"]), ("H3", "ALLOW", True))
        self.assertEqual((blocked["level"], blocked["result"]), ("H5", "HUMAN_REQUIRED"))
        self.assertIn("RECIPE_COMPATIBILITY_REJECTED", blocked["reason_codes"])

    def test_deterministic_failure_never_gets_blind_retry(self):
        result = evaluate_healing(
            self.incident(), {"schema_version": 1, "recipes": []}, self.config,
            self.context(), now=self.now,
        )
        self.assertEqual(result["level"], "H5")
        self.assertIn("DETERMINISTIC_FAILURE_NO_BLIND_RETRY", result["reason_codes"])

    def test_root_secret_traversal_and_shell_escape_are_blocked(self):
        attacks = [
            self.recipe(writes=["secret.txt"]),
            self.recipe(writes=["../.adwf/config.json"]),
            self.recipe(argv=["bash", "-c", "rm -rf ."]),
        ]
        for recipe in attacks:
            with self.subTest(recipe=recipe["actions"][0]):
                result = evaluate_healing(
                    self.incident(), {"schema_version": 1, "recipes": [recipe]},
                    self.config, self.context(), now=self.now,
                )
                self.assertEqual((result["level"], result["result"]), ("H5", "HUMAN_REQUIRED"))

    def test_invalid_nan_budget_metric_fails_closed(self):
        context = self.context()
        context["elapsed_seconds"] = float("nan")
        result = evaluate_healing(
            self.incident(), {"schema_version": 1, "recipes": [self.recipe()]},
            self.config, context, now=self.now,
        )
        self.assertEqual(result["result"], "HUMAN_REQUIRED")
        self.assertIn("INVALID_BUDGET_METRIC:elapsed_seconds", result["reason_codes"])

    def test_malformed_circuit_state_opens_fail_closed(self):
        result = evaluate_healing(
            self.incident(), {"schema_version": 1, "recipes": [self.recipe()]},
            self.config, self.context(), circuit_state=[], now=self.now,
        )
        self.assertEqual((result["level"], result["result"]), ("H5", "HUMAN_REQUIRED"))
        self.assertIn("CIRCUIT_BREAKER_OPEN", result["reason_codes"])

    def test_lifecycle_needs_shadow_evidence_and_independent_approval(self):
        shadow = self.recipe(lifecycle="SHADOW")
        with self.assertRaisesRegex(ValueError, "INDEPENDENT_APPROVAL"):
            transition_recipe(shadow, "CERTIFIED", shadow_passes=2, now=self.now)
        with self.assertRaisesRegex(ValueError, "SHADOW_EVIDENCE"):
            transition_recipe(shadow, "CERTIFIED", independent_approval=True, shadow_passes=1, now=self.now)
        certified = transition_recipe(
            shadow, "CERTIFIED", independent_approval=True, shadow_passes=2, now=self.now,
        )
        active = transition_recipe(certified, "ACTIVE", independent_approval=True, now=self.now)
        self.assertEqual(active["lifecycle"], "ACTIVE")
        self.assertTrue(active["certification"]["independent_approval"])

    def test_false_heal_quarantines_recipe_and_opens_circuit(self):
        registry, circuit = record_heal_outcome(
            {"schema_version": 1, "recipes": [self.recipe()]}, {},
            recipe_id="REC-TEST_FIX", recipe_version="1.0.0",
            fingerprint_hash=self.fingerprint, outcome="FALSE_HEAL",
            config=self.config, now=self.now,
        )
        self.assertEqual(registry["recipes"][0]["lifecycle"], "QUARANTINED")
        self.assertTrue(circuit["recipes"]["REC-TEST_FIX@1.0.0"]["open"])
        self.assertTrue(circuit["fingerprints"][self.fingerprint]["open"])

    def test_h4_rejects_undeclared_or_protected_actual_write(self):
        context = {
            "target_sha": "c" * 40, "verified_sha": "c" * 40,
            "recipe_digest": "d" * 64, "applied_recipe_digest": "d" * 64,
            "sandbox": True, "targeted_gates": "PASS", "full_gates": "PASS",
            "policy_result": "ALLOW", "actual_changed_paths": ["src/service.py"],
            "declared_changed_paths": ["src/service.py"], "attempts": 0,
            "elapsed_seconds": 10, "changed_files": 0, "monetary_cost": 0,
            "ai_api_required": False,
        }
        verified = verify_h4(context, self.config)
        self.assertEqual((verified["level"], verified["result"]), ("H4", "VERIFIED"))
        context["actual_changed_paths"] = [".github/workflows/ci.yml"]
        protected = verify_h4(context, self.config)
        self.assertEqual(protected["result"], "BLOCK")
        self.assertIn("H4_UNDECLARED_WRITE", protected["reason_codes"])

    def test_h6_only_allows_separately_certified_exact_rollback(self):
        context = {
            "target_sha": "c" * 40, "verified_sha": "c" * 40,
            "artifact_digest": "d" * 64, "verified_artifact_digest": "d" * 64,
            "policy_result": "ALLOW", "all_mandatory_gates": "PASS",
            "requires_ai_api": False, "monetary_cost": 0,
            "rollback_certified": True, "data_migration": False,
        }
        default = authorize_h6("ROLLBACK", context, self.config)
        enabled = copy.deepcopy(self.config)
        enabled["automatic_rollback_certified"] = True
        allowed = authorize_h6("ROLLBACK", context, enabled)
        promotion = authorize_h6("PROMOTE", {**context, "owner_acceptance": "ACCEPTED"}, enabled)
        self.assertEqual(default["result"], "HUMAN_REQUIRED")
        self.assertEqual(allowed["result"], "ALLOW")
        self.assertEqual(promotion["result"], "HUMAN_REQUIRED")


if __name__ == "__main__":
    unittest.main()

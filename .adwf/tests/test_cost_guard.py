import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.cost_guard import evaluate_provider


class CostGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads((ROOT / ".adwf/providers.json").read_text(encoding="utf-8"))
        cls.now = datetime(2026, 8, 13, 10, tzinfo=timezone.utc)

    def test_local_free_allowed(self):
        request = {"provider": "local_deterministic", "mandatory_ci": True, "automated": True, "projected_cost": 0}
        self.assertEqual(evaluate_provider(self.registry, request, now=self.now, canonical_provider="local")["result"], "ALLOW")

    def test_unknown_provider_blocked(self):
        self.assertEqual(evaluate_provider(self.registry, {"provider": "mystery"}, now=self.now)["reason_codes"], ["UNKNOWN_PROVIDER"])

    def test_openai_api_blocked_for_mandatory_ci(self):
        result = evaluate_provider(self.registry, {"provider": "openai_api", "mandatory_ci": True}, now=self.now)
        self.assertEqual(result["result"], "BLOCK")
        self.assertIn("AI_API_FOR_MANDATORY_CI", result["reason_codes"])
        self.assertIn("METERED_PROVIDER_FORBIDDEN", result["reason_codes"])

    def test_free_quota_requires_enabled_and_fresh_proof(self):
        registry = json.loads(json.dumps(self.registry))
        registry["providers"]["github_private_free_quota"]["enabled"] = True
        request = {"provider": "github_private_free_quota", "mandatory_ci": True, "automated": True,
                   "repository_visibility": "PRIVATE", "runner_class": "standard",
                   "quota": {"used": 100, "limit": 2000, "observed_at": "2026-08-13T09:30:00Z", "hard_spend_limit_zero": True, "allow_overage": False},
                   "storage": {"artifact_mb": 20, "cache_mb": 100, "observed_at": "2026-08-13T09:30:00Z"},
                   "projected_units": 20}
        result = evaluate_provider(registry, request, now=self.now, canonical_provider="github")
        self.assertEqual(result["result"], "ALLOW")

    def test_quota_overrun_blocked(self):
        registry = json.loads(json.dumps(self.registry))
        registry["providers"]["github_private_free_quota"]["enabled"] = True
        request = {"provider": "github_private_free_quota",
                   "repository_visibility": "PRIVATE", "runner_class": "standard",
                   "quota": {"used": 1590, "limit": 2000, "observed_at": "2026-08-13T09:30:00Z", "hard_spend_limit_zero": True, "allow_overage": False},
                   "storage": {"artifact_mb": 20, "cache_mb": 100, "observed_at": "2026-08-13T09:30:00Z"},
                   "projected_units": 20}
        result = evaluate_provider(registry, request, now=self.now, canonical_provider="github")
        self.assertIn("FREE_QUOTA_WOULD_BE_EXCEEDED", result["reason_codes"])

    def test_private_quota_requires_storage_usage_and_enforces_stop(self):
        registry = json.loads(json.dumps(self.registry))
        registry["providers"]["github_private_free_quota"]["enabled"] = True
        base = {"provider": "github_private_free_quota",
                "repository_visibility": "PRIVATE", "runner_class": "standard",
                "quota": {"used": 100, "limit": 2000, "observed_at": "2026-08-13T09:30:00Z", "hard_spend_limit_zero": True, "allow_overage": False}}
        missing = evaluate_provider(registry, base, now=self.now, canonical_provider="github")
        self.assertIn("STORAGE_USAGE_NOT_VERIFIED", missing["reason_codes"])
        base["storage"] = {"artifact_mb": 349, "cache_mb": 100, "observed_at": "2026-08-13T09:30:00Z"}
        base["projected_artifact_mb"] = 2
        exceeded = evaluate_provider(registry, base, now=self.now, canonical_provider="github")
        self.assertIn("ARTIFACT_STORAGE_WOULD_EXCEED_INTERNAL_LIMIT", exceeded["reason_codes"])

    def test_nan_cost_is_blocked(self):
        result = evaluate_provider(self.registry, {"provider": "local_deterministic", "projected_cost": "nan"}, now=self.now)
        self.assertIn("PROJECTED_COST_INVALID", result["reason_codes"])

    def test_provider_pricing_evidence_expiry_blocks(self):
        result = evaluate_provider(self.registry, {"provider": "github_self_hosted"}, now=datetime(2026, 10, 1, tzinfo=timezone.utc), canonical_provider="github")
        self.assertIn("PROVIDER_EVIDENCE_STALE", result["reason_codes"])

    def test_canonical_provider_mismatch_blocks(self):
        result = evaluate_provider(self.registry, {"provider": "gitlab_self_hosted"}, now=self.now, canonical_provider="github")
        self.assertIn("CANONICAL_PROVIDER_MISMATCH", result["reason_codes"])

    def test_public_standard_is_not_private_minutes_quota(self):
        registry = json.loads(json.dumps(self.registry))
        registry["providers"]["github_public_standard"]["enabled"] = True
        result = evaluate_provider(registry, {"provider": "github_public_standard", "mandatory_ci": True, "automated": True,
                                              "repository_visibility": "PUBLIC", "runner_class": "standard"}, now=self.now, canonical_provider="github")
        self.assertEqual(result["result"], "ALLOW")

    def test_public_standard_never_assumed_for_private_repository(self):
        registry = json.loads(json.dumps(self.registry))
        registry["providers"]["github_public_standard"]["enabled"] = True
        result = evaluate_provider(registry, {"provider": "github_public_standard", "repository_visibility": "PRIVATE",
                                              "runner_class": "standard"}, now=self.now, canonical_provider="github")
        self.assertIn("PUBLIC_REPOSITORY_NOT_VERIFIED", result["reason_codes"])

    def test_owner_provided_requires_fresh_owner_attestation(self):
        missing = evaluate_provider(self.registry, {"provider": "github_self_hosted", "runner_class": "self_hosted"},
                                    now=self.now, canonical_provider="github")
        self.assertIn("OWNER_RESOURCE_NOT_CONFIRMED", missing["reason_codes"])
        request = json.loads((ROOT / ".adwf/requests/github-self-hosted.json").read_text(encoding="utf-8"))
        self.assertEqual(evaluate_provider(self.registry, request, now=self.now, canonical_provider="github")["result"], "ALLOW")

    def test_expired_source_has_stale_effective_classification(self):
        result = evaluate_provider(self.registry, {"provider": "local_deterministic"},
                                   now=datetime(2031, 1, 1, tzinfo=timezone.utc), canonical_provider="local")
        self.assertEqual(result["effective_classification"], "STALE")
        self.assertEqual(result["result"], "BLOCK")

    def test_every_non_free_status_is_fail_closed(self):
        for status in ("METERED", "PAID", "UNKNOWN", "STALE"):
            with self.subTest(status=status):
                registry = json.loads(json.dumps(self.registry))
                provider = registry["providers"]["local_deterministic"]
                provider["classification"] = status
                result = evaluate_provider(registry, {"provider": "local_deterministic"}, now=self.now, canonical_provider="local")
                self.assertEqual(result["result"], "BLOCK")

    def test_subscription_is_optional_and_never_mandatory_ci(self):
        registry = json.loads(json.dumps(self.registry))
        registry["providers"]["chatgpt_codex_subscription"]["enabled"] = True
        evidence = {"verified_at": "2026-08-13T09:00:00Z", "valid_until": "2026-08-14T09:00:00Z", "auto_credit_purchase": False}
        optional = evaluate_provider(registry, {"provider": "chatgpt_codex_subscription", "subscription": evidence}, now=self.now)
        mandatory = evaluate_provider(registry, {"provider": "chatgpt_codex_subscription", "mandatory_ci": True, "subscription": evidence}, now=self.now)
        self.assertEqual(optional["result"], "ALLOW")
        self.assertIn("CAPABILITY_NOT_ALLOWED_FOR_MANDATORY_CI", mandatory["reason_codes"])


if __name__ == "__main__":
    unittest.main()

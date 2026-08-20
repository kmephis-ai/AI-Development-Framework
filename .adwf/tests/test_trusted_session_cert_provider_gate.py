import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from scripts import publish_trusted_gate as GATE


class _Client:
    repo = "kmephis-ai/AI-Development-Framework"


class TrustedSessionCertificationProviderGateTests(unittest.TestCase):
    def _evaluate(
        self,
        registry,
        *,
        verification=None,
        validation_errors=None,
        resolution_errors=None,
    ):
        payloads = {
            ".adwf/capability-live-evidence.json": registry,
            ".adwf/capability-traceability.json": {
                "capabilities": [
                    {"id": "CONSUMER_FRAMEWORK_UPGRADE_PLANNING"},
                    {"id": "CONSUMER_FRAMEWORK_UPGRADE_TRANSACTION"},
                    {"id": "SESSION_CONTINUITY"},
                ]
            },
            ".adwf/schemas/capability-live-evidence-certification.schema.json": {},
        }

        def provider_blob(_client, path, _sha):
            return json.dumps(payloads[path])

        verifier = Mock(
            side_effect=verification
            or (lambda _client, _cert: {"verified": True, "reason_codes": []})
        )
        with (
            patch.object(GATE, "_github_blob", side_effect=provider_blob),
            patch.object(
                GATE,
                "validate_certification_registry",
                return_value=list(validation_errors or []),
            ),
            patch.object(
                GATE,
                "resolve_capability_live_evidence",
                return_value=list(resolution_errors or []),
            ),
            patch.object(GATE, "verify_provider_certification", verifier),
        ):
            result = GATE._capability_live_evidence_provider_gate(
                _Client(),
                {"number": 7, "base": {"sha": "a" * 40}},
                "b" * 40,
                [".adwf/capability-live-evidence.json"],
            )
        return result, verifier

    def test_upgrade_only_registry_remains_backward_compatible(self):
        upgrade = {"id": "UPGRADE"}
        result, verifier = self._evaluate({"certifications": [upgrade]})
        self.assertTrue(result["verified"])
        self.assertEqual(result["reason_codes"], [])
        self.assertEqual([item["id"] for item in result["provider"]], ["UPGRADE"])
        self.assertEqual(verifier.call_count, 1)
        self.assertEqual(verifier.call_args_list[0].args[1], upgrade)

    def test_empty_session_lane_remains_backward_compatible(self):
        upgrade = {"id": "UPGRADE"}
        result, verifier = self._evaluate(
            {"certifications": [upgrade], "session_certifications": []}
        )
        self.assertTrue(result["verified"])
        self.assertEqual(verifier.call_count, 1)
        self.assertEqual(verifier.call_args_list[0].args[1], upgrade)

    def test_upgrade_and_session_are_each_provider_verified_exactly_once(self):
        upgrade = {"id": "UPGRADE"}
        session = {"id": "SESSION"}
        result, verifier = self._evaluate(
            {"certifications": [upgrade], "session_certifications": [session]}
        )
        self.assertTrue(result["verified"])
        self.assertEqual([item["id"] for item in result["provider"]], ["UPGRADE", "SESSION"])
        self.assertEqual(verifier.call_count, 2)
        self.assertEqual(
            [call.args[1] for call in verifier.call_args_list],
            [upgrade, session],
        )

    def test_session_provider_failure_blocks_live_evidence(self):
        upgrade = {"id": "UPGRADE"}
        session = {"id": "SESSION"}

        def verify(_client, cert):
            if cert["id"] == "SESSION":
                return {
                    "verified": False,
                    "reason_codes": ["LIVE_CERT_SESSION_FAKE_MISMATCH"],
                }
            return {"verified": True, "reason_codes": []}

        result, verifier = self._evaluate(
            {"certifications": [upgrade], "session_certifications": [session]},
            verification=verify,
        )
        self.assertFalse(result["verified"])
        self.assertIn("LIVE_CERT_SESSION_FAKE_MISMATCH", result["reason_codes"])
        self.assertEqual(verifier.call_count, 2)

    def test_structural_validation_failure_prevents_all_provider_verification(self):
        result, verifier = self._evaluate(
            {
                "certifications": [{"id": "DUP"}],
                "session_certifications": [{"id": "DUP"}],
            },
            validation_errors=["LIVE_CERT_DUPLICATE_OR_MISSING_ID:DUP"],
        )
        self.assertFalse(result["verified"])
        self.assertIn("LIVE_CERT_DUPLICATE_OR_MISSING_ID:DUP", result["reason_codes"])
        verifier.assert_not_called()

    def test_candidate_invented_container_never_becomes_provider_authority(self):
        upgrade = {"id": "UPGRADE"}
        session = {"id": "SESSION"}
        invented = {"id": "INVENTED"}
        result, verifier = self._evaluate(
            {
                "certifications": [upgrade],
                "session_certifications": [session],
                "candidate_certifications": [invented],
            }
        )
        self.assertTrue(result["verified"])
        self.assertEqual(
            [call.args[1] for call in verifier.call_args_list],
            [upgrade, session],
        )
        self.assertNotIn(invented, [call.args[1] for call in verifier.call_args_list])


if __name__ == "__main__":
    unittest.main()

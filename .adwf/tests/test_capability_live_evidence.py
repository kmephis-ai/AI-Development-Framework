import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.capability_live_evidence import (
    resolve_capability_live_evidence,
    seal_registry,
    validate_certification_registry,
    verify_provider_certification,
)


class CapabilityLiveEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads((ROOT / ".adwf/capability-live-evidence.json").read_text(encoding="utf-8"))
        cls.schema = json.loads((ROOT / ".adwf/schemas/capability-live-evidence-certification.schema.json").read_text(encoding="utf-8"))
        cls.trace = json.loads((ROOT / ".adwf/capability-traceability.json").read_text(encoding="utf-8"))

    def test_canonical_upgrade_certification_is_offline_valid(self):
        known = {item["id"] for item in self.trace["capabilities"]}
        self.assertEqual(validate_certification_registry(self.registry, schema=self.schema, known_capability_ids=known), [])
        self.assertEqual(resolve_capability_live_evidence(self.trace, self.registry, schema=self.schema), [])

    def test_formatted_provider_string_cannot_create_live_verified(self):
        trace = copy.deepcopy(self.trace)
        target = next(item for item in trace["capabilities"] if item["id"] == "CONSUMER_FRAMEWORK_UPGRADE_PLANNING")
        target["live_evidence"] = ["github:actions/runs/31964580894"]
        errors = resolve_capability_live_evidence(trace, self.registry, schema=self.schema)
        self.assertTrue(any(code.startswith("CAPABILITY_LIVE_CERTIFICATION_REF_INVALID") for code in errors), errors)

    def test_tampered_certification_digest_blocks(self):
        registry = copy.deepcopy(self.registry)
        registry["certifications"][0]["report_sha256"] = "0" * 64
        errors = validate_certification_registry(registry, schema=self.schema)
        self.assertIn("LIVE_CERT_DIGEST_MISMATCH:CERT-UPGRADE-003-PRIHRASH-EXTERNAL", errors)
        self.assertIn("LIVE_CERT_REGISTRY_DIGEST_MISMATCH", errors)

    def test_resealed_wrong_capability_scope_still_blocks(self):
        registry = copy.deepcopy(self.registry)
        registry["certifications"][0]["capability_ids"] = ["TRUSTED_GATE"]
        registry = seal_registry(registry)
        errors = validate_certification_registry(registry, schema=self.schema, known_capability_ids={item["id"] for item in self.trace["capabilities"]})
        self.assertIn("LIVE_CERT_UPGRADE_SCOPE_INVALID:CERT-UPGRADE-003-PRIHRASH-EXTERNAL", errors)

    def _provider_clients(self, *, check_text=None, consumer_tree=None):
        cert = self.registry["certifications"][0]
        p, f, c, s = cert["provider"], cert["framework"], cert["consumer"], cert["subject"]
        expected_text = (
            f"consumer={c['sha']} tree={c['tree']}\n"
            f"source={f['source_sha']} target={f['target_sha']}\n"
            f"report_sha256={cert['report_sha256']}"
        )
        client = mock.Mock()
        client.repo = p["repository"]; client.token = "token"; client.transport = mock.Mock(); client.api_base = "https://api.github.com"
        def read(path):
            if path.endswith(f"/actions/runs/{p['workflow_run_id']}"):
                return {"id": p["workflow_run_id"], "name": p["workflow_name"], "head_sha": p["workflow_run_head_sha"], "event": "push", "status": "completed", "conclusion": "success", "repository": {"full_name": p["repository"]}}
            if path.endswith(f"/check-runs/{p['check_run_id']}"):
                return {"id": p["check_run_id"], "name": p["check_name"], "head_sha": s["sha"], "status": "completed", "conclusion": "success", "app": {"id": p["check_app_id"], "slug": p["check_app_slug"]}, "output": {"text": check_text if check_text is not None else expected_text}}
            if path.endswith("/git/commits/" + f["target_sha"]): return {"tree": {"sha": f["target_tree"]}}
            if path.endswith("/git/commits/" + f["source_sha"]): return {"tree": {"sha": f["source_tree"]}}
            raise AssertionError(path)
        client.get.side_effect = read
        consumer = mock.Mock(); consumer.repo = c["repository"]
        consumer.get.return_value = {"tree": {"sha": consumer_tree or c["tree"]}}
        return client, consumer

    def test_provider_readback_binds_exact_run_check_and_three_git_trees(self):
        client, consumer = self._provider_clients()
        with mock.patch("lib.github_provider.GitHubClient", return_value=consumer):
            result = verify_provider_certification(client, self.registry["certifications"][0])
        self.assertTrue(result["verified"], result)

    def test_resealed_wrong_report_still_fails_provider_readback(self):
        registry = copy.deepcopy(self.registry)
        registry["certifications"][0]["report_sha256"] = "0" * 64
        registry = seal_registry(registry)
        client, consumer = self._provider_clients()  # provider still exposes original report digest
        with mock.patch("lib.github_provider.GitHubClient", return_value=consumer):
            result = verify_provider_certification(client, registry["certifications"][0])
        self.assertFalse(result["verified"])
        self.assertIn("LIVE_CERT_PROVIDER_CHECK_OUTPUT_MISMATCH", result["reason_codes"])

    def test_consumer_tree_substitution_fails_provider_readback(self):
        client, consumer = self._provider_clients(consumer_tree="f" * 40)
        with mock.patch("lib.github_provider.GitHubClient", return_value=consumer):
            result = verify_provider_certification(client, self.registry["certifications"][0])
        self.assertFalse(result["verified"])
        self.assertIn("LIVE_CERT_PROVIDER_CONSUMER_TREE_MISMATCH", result["reason_codes"])


if __name__ == "__main__":
    unittest.main()

import base64
import copy
import hashlib
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


def h(text):
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def blob(value, sha):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"sha": sha, "encoding": "base64", "content": base64.b64encode(raw).decode("ascii")}


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
        client, consumer = self._provider_clients()
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

    def _session_fixture(self):
        sh_issue_body = "self-host terminal"
        sh_ledger_body = "self ledger event"
        con_issue_body = "consumer terminal"
        con_ledger_body = "consumer ledger event"
        sh_tag_message = "self anchor"
        con_root_message = "consumer root"
        con_event_message = "consumer event"
        sh_subject = "1" * 40
        con_subject = "2" * 40
        installed = "3" * 40
        sh_a_blob, sh_b_blob, con_a_blob, con_b_blob = "a" * 40, "b" * 40, "c" * 40, "d" * 40
        checkpoint_self, checkpoint_con = "4" * 64, "5" * 64
        event_self, event_con = "6" * 64, "7" * 64
        cert = {
            "id": "CERT-SESSION-CONTINUITY-001",
            "evidence_class": "SESSION_CONTINUITY_HANDOVER",
            "capability_ids": ["SESSION_CONTINUITY"],
            "self_host": {
                "repository": "kmephis-ai/AI-Development-Framework",
                "subject": {"sha": sh_subject, "tree": "8" * 40},
                "issue": {"number": 149, "state": "closed", "terminal_comment_id": 14901, "terminal_comment_sha256": h(sh_issue_body)},
                "ledger": {
                    "issue_number": 3, "comment_id": 301, "comment_sha256": h(sh_ledger_body),
                    "event_hash": event_self, "checkpoint_digest": checkpoint_self,
                    "event_anchor": {"tag": "adwf-runtime-anchor-self", "tag_object_sha": "9" * 40, "target_sha": sh_subject, "message_sha256": h(sh_tag_message)},
                },
                "session_a": {"run_id": 1001, "workflow_name": "Self A", "head_sha": "a" * 40, "job_id": 1101, "job_name": "persist-checkpoint", "evidence_blob_sha": sh_a_blob},
                "session_b": {"run_id": 1002, "workflow_name": "Self B", "head_sha": "b" * 40, "job_id": 1102, "job_name": "restore-checkpoint", "evidence_blob_sha": sh_b_blob},
                "safety": {"provider_authority": False, "independent_checkout": True, "session_a_local_runtime_present": False, "stale_authority_allowed": False},
            },
            "connected_consumer": {
                "repository": "kmephis-ai/PrihRashOnline-v2",
                "subject": {"sha": con_subject, "tree": "c" * 40},
                "installed_framework": {"sha": installed, "tree": "d" * 40},
                "issue": {"number": 332, "state": "closed", "terminal_comment_id": 33201, "terminal_comment_sha256": h(con_issue_body)},
                "ledger": {
                    "issue_number": 329, "comment_id": 32901, "comment_sha256": h(con_ledger_body),
                    "event_hash": event_con, "checkpoint_digest": checkpoint_con,
                    "root_anchor": {"tag": "adwf-runtime-anchor-ledger-root-v1", "tag_object_sha": "e" * 40, "target_sha": con_subject, "message_sha256": h(con_root_message)},
                    "event_anchor": {"tag": "adwf-runtime-anchor-event", "tag_object_sha": "f" * 40, "target_sha": "0" * 40, "message_sha256": h(con_event_message)},
                },
                "legacy_duplicate": {"issue_number": 330, "state": "closed", "state_reason": "duplicate"},
                "session_a": {"run_id": 2001, "workflow_name": "Consumer A", "head_sha": "1" * 40, "job_id": 2101, "job_name": "adopt-root", "runner_name": "GitHub Actions 1", "evidence_blob_sha": con_a_blob},
                "session_b": {"run_id": 2002, "workflow_name": "Consumer B", "head_sha": "2" * 40, "job_id": 2102, "job_name": "restore-root", "runner_name": "GitHub Actions 2", "evidence_blob_sha": con_b_blob},
                "accepted_defect": {"repository": "kmephis-ai/AI-Development-Framework", "issue_number": 157, "state": "closed"},
                "safety": {"provider_authority": False, "no_duplicate_writer": True, "no_stale_mutation": True, "no_runtime_ledger_write_session_b": True, "singleton_ledger": True, "stale_checkpoint_rejected": True},
            },
        }
        sh_a = {
            "proof_phase": "SESSION_A_PERSIST_AND_READBACK", "subject_sha": sh_subject,
            "runtime_ledger": {"issue_number": 3, "comment_id": 301, "event_hash": event_self, "checkpoint_digest": checkpoint_self, "external_anchor": {"tag": "adwf-runtime-anchor-self", "tag_object_sha": "9" * 40, "ruleset_verified": True}, "public_projection_only": True},
            "same_session_readback": {"event_hash": event_self, "reconciliation": {"provider_authority": False, "stale": False, "actual_main_sha": sh_subject, "next_step": "RESUME_CONTEXT_ONLY"}},
        }
        sh_b = {
            "proof_phase": "SESSION_B_FRESH_PROVIDER_RESTORE", "github_run_id": "1002", "subject_sha": sh_subject,
            "independent_checkout": True, "session_a_local_runtime_present": False,
            "restored": {"checkpoint_digest": checkpoint_self, "event_hash": event_self, "provider_object_id": "301", "reconciliation": {"provider_authority": False, "stale": False, "actual_main_sha": sh_subject, "next_step": "RESUME_CONTEXT_ONLY"}},
        }
        con_a = {
            "proof_phase": "SESSION_A_LEGACY_ROOT_ADOPTION_AND_IMMEDIATE_RESTORE", "adoption_status": "UNCHANGED", "github_run_id": "2001", "subject_sha": con_subject,
            "installed_adwf": installed, "ledger_issue": 329, "legacy_comment_sha256": h(con_ledger_body), "legacy_event_hash": event_con,
            "legacy_event_tag_object_sha": "f" * 40, "root_tag_object_sha": "e" * 40, "root_target_sha": con_subject,
            "current_writer_unique": True, "no_legacy_rewrite": True, "no_new_ledger_event": True, "open_ledger_count_before": 1, "open_ledger_count_after": 1,
            "provider_authority": False, "monetary_budget_usd": 0, "secrets": "FORBIDDEN", "runner_name": "GitHub Actions 1",
            "immediate_root_only_restore": {"checkpoint_digest": checkpoint_con, "event_hash": event_con, "reconciliation": {"provider_authority": False, "stale": True, "stale_main": True, "actual_main_sha": con_subject, "next_step": "FRESH_AUTHORITY_RESOLUTION_REQUIRED"}},
        }
        con_b = {
            "proof_phase": "SESSION_B_INDEPENDENT_ROOT_ONLY_RESTORE", "github_run_id": "2002", "subject_sha": con_subject, "installed_adwf": installed,
            "source_session_a_run_id": "2001", "source_session_a_runner": "GitHub Actions 1", "runner_name": "GitHub Actions 2",
            "ledger_issue": 329, "legacy_comment_sha256": h(con_ledger_body), "legacy_event_hash": event_con, "legacy_event_tag_object_sha": "f" * 40,
            "root_tag_object_sha": "e" * 40, "root_target_sha": con_subject, "current_writer_unique": True, "independent_checkout": True,
            "session_a_local_runtime_present": False, "no_duplicate_writer_created": True, "no_runtime_ledger_write": True, "no_stale_mutation": True,
            "open_ledger_count": 1, "provider_authority": False, "monetary_budget_usd": 0, "secrets": "FORBIDDEN",
            "authority_evaluation": {"checkpoint_stale": True, "duplicate_writer_allowed": False, "provider_authority": False, "resume_context_allowed": False, "next_step": "FRESH_AUTHORITY_RESOLUTION_REQUIRED"},
            "restored": {"checkpoint_digest": checkpoint_con, "event_hash": event_con, "reconciliation": {"provider_authority": False, "stale": True, "stale_main": True, "actual_main_sha": con_subject, "next_step": "FRESH_AUTHORITY_RESOLUTION_REQUIRED"}},
        }
        provider = {
            "self_issue_body": sh_issue_body, "self_ledger_body": sh_ledger_body, "consumer_issue_body": con_issue_body, "consumer_ledger_body": con_ledger_body,
            "self_tag_message": sh_tag_message, "consumer_root_message": con_root_message, "consumer_event_message": con_event_message,
            "blobs": {sh_a_blob: sh_a, sh_b_blob: sh_b, con_a_blob: con_a, con_b_blob: con_b},
        }
        return cert, provider

    def _session_clients(self, cert, provider, *, wrong_tag=False, wrong_consumer_tree=False, wrong_blob_safety=False):
        self_repo = cert["self_host"]["repository"]
        con_repo = cert["connected_consumer"]["repository"]
        client = mock.Mock(); client.repo = self_repo; client.token = "token"; client.transport = mock.Mock(); client.api_base = "https://api.github.com"
        consumer = mock.Mock(); consumer.repo = con_repo; consumer.token = "token"; consumer.transport = client.transport; consumer.api_base = client.api_base

        def run_payload(repo, expected):
            return {"id": expected["run_id"], "name": expected["workflow_name"], "head_sha": expected["head_sha"], "event": "push", "status": "completed", "conclusion": "success", "repository": {"full_name": repo}}
        def jobs_payload(expected):
            item = {"id": expected["job_id"], "name": expected["job_name"], "status": "completed", "conclusion": "success"}
            if "runner_name" in expected: item["runner_name"] = expected["runner_name"]
            return {"jobs": [item]}
        def tag_ref(anchor):
            return {"object": {"type": "tag", "sha": ("0" * 40 if wrong_tag else anchor["tag_object_sha"])}}
        def tag_obj(anchor, message):
            return {"sha": anchor["tag_object_sha"], "object": {"type": "commit", "sha": anchor["target_sha"]}, "message": message}

        def self_read(path):
            sh = cert["self_host"]; cc = cert["connected_consumer"]
            if path.endswith("/git/commits/" + sh["subject"]["sha"]): return {"sha": sh["subject"]["sha"], "tree": {"sha": sh["subject"]["tree"]}}
            if path.endswith("/git/commits/" + cc["installed_framework"]["sha"]): return {"sha": cc["installed_framework"]["sha"], "tree": {"sha": cc["installed_framework"]["tree"]}}
            if path.endswith("/issues/149"): return {"number": 149, "state": "closed"}
            if path.endswith("/issues/comments/14901"): return {"id": 14901, "issue_url": f"https://api.github.com/repos/{self_repo}/issues/149", "body": provider["self_issue_body"]}
            if path.endswith("/issues/3"): return {"number": 3, "state": "open", "title": "[ADWF] Runtime Ledger", "comments": 1}
            if path.endswith("/issues/comments/301"): return {"id": 301, "issue_url": f"https://api.github.com/repos/{self_repo}/issues/3", "body": provider["self_ledger_body"]}
            if path.endswith("/issues/157"): return {"number": 157, "state": "closed"}
            anchor = sh["ledger"]["event_anchor"]
            if "/git/ref/tags/" in path: return tag_ref(anchor)
            if path.endswith("/git/tags/" + anchor["tag_object_sha"]): return tag_obj(anchor, provider["self_tag_message"])
            for expected in (sh["session_a"], sh["session_b"]):
                if path.endswith(f"/actions/runs/{expected['run_id']}"): return run_payload(self_repo, expected)
                if path.endswith(f"/actions/runs/{expected['run_id']}/jobs?per_page=100"): return jobs_payload(expected)
                if path.endswith("/git/blobs/" + expected["evidence_blob_sha"]): return blob(provider["blobs"][expected["evidence_blob_sha"]], expected["evidence_blob_sha"])
            raise AssertionError(path)

        def con_read(path):
            cc = cert["connected_consumer"]
            if path.endswith("/git/commits/" + cc["subject"]["sha"]): return {"sha": cc["subject"]["sha"], "tree": {"sha": ("f" * 40 if wrong_consumer_tree else cc["subject"]["tree"])}}
            if path.endswith("/issues/332"): return {"number": 332, "state": "closed"}
            if path.endswith("/issues/comments/33201"): return {"id": 33201, "issue_url": f"https://api.github.com/repos/{con_repo}/issues/332", "body": provider["consumer_issue_body"]}
            if path.endswith("/issues/329"): return {"number": 329, "state": "open", "title": "[ADWF] Runtime Ledger", "comments": 1}
            if path.endswith("/issues/comments/32901"): return {"id": 32901, "issue_url": f"https://api.github.com/repos/{con_repo}/issues/329", "body": provider["consumer_ledger_body"]}
            if path.endswith("/issues/330"): return {"number": 330, "state": "closed", "state_reason": "duplicate", "comments": 0}
            for key, message in (("root_anchor", provider["consumer_root_message"]), ("event_anchor", provider["consumer_event_message"])):
                anchor = cc["ledger"][key]
                if path.endswith("/git/ref/tags/" + anchor["tag"]): return tag_ref(anchor)
                if path.endswith("/git/tags/" + anchor["tag_object_sha"]): return tag_obj(anchor, message)
            for expected in (cc["session_a"], cc["session_b"]):
                if path.endswith(f"/actions/runs/{expected['run_id']}"): return run_payload(con_repo, expected)
                if path.endswith(f"/actions/runs/{expected['run_id']}/jobs?per_page=100"): return jobs_payload(expected)
                if path.endswith("/git/blobs/" + expected["evidence_blob_sha"]):
                    value = copy.deepcopy(provider["blobs"][expected["evidence_blob_sha"]])
                    if wrong_blob_safety and expected is cc["session_b"]: value["no_stale_mutation"] = False
                    return blob(value, expected["evidence_blob_sha"])
            raise AssertionError(path)

        client.get.side_effect = self_read
        consumer.get.side_effect = con_read
        def new_client(repo, *_args, **_kwargs):
            if repo == con_repo: return consumer
            if repo == self_repo: return client
            raise AssertionError(repo)
        return client, consumer, new_client

    def _session_registry_and_trace(self):
        cert, provider = self._session_fixture()
        registry = copy.deepcopy(self.registry)
        registry["certifications"].append(cert)
        registry = seal_registry(registry)
        trace = copy.deepcopy(self.trace)
        session = next(item for item in trace["capabilities"] if item["id"] == "SESSION_CONTINUITY")
        session["status"] = "LIVE_VERIFIED"
        session["live_evidence"] = ["certification:CERT-SESSION-CONTINUITY-001"]
        return registry, trace, provider

    def test_session_fixture_is_strict_schema_and_scope_valid(self):
        registry, trace, _ = self._session_registry_and_trace()
        known = {item["id"] for item in trace["capabilities"]}
        self.assertEqual(validate_certification_registry(registry, schema=self.schema, known_capability_ids=known), [])
        self.assertEqual(resolve_capability_live_evidence(trace, registry, schema=self.schema), [])

    def test_session_provider_readback_binds_runs_jobs_tags_blobs_and_safety(self):
        registry, _, provider = self._session_registry_and_trace()
        cert = registry["certifications"][-1]
        client, _, factory = self._session_clients(cert, provider)
        with mock.patch("lib.github_provider.GitHubClient", side_effect=factory):
            result = verify_provider_certification(client, cert)
        self.assertTrue(result["verified"], result)
        self.assertEqual(result["self_host_run_ids"], [1001, 1002])
        self.assertEqual(result["consumer_run_ids"], [2001, 2002])

    def test_session_same_runs_and_runner_fail_closed(self):
        cert, _ = self._session_fixture()
        cert["self_host"]["session_b"]["run_id"] = cert["self_host"]["session_a"]["run_id"]
        cert["connected_consumer"]["session_b"]["run_id"] = cert["connected_consumer"]["session_a"]["run_id"]
        cert["connected_consumer"]["session_b"]["runner_name"] = cert["connected_consumer"]["session_a"]["runner_name"]
        registry = seal_registry({"$schema": ".adwf/schemas/capability-live-evidence-certification.schema.json", "schema_version": 1, "role": "CANONICAL_CAPABILITY_LIVE_EVIDENCE_CERTIFICATIONS", "certifications": [cert]})
        errors = validate_certification_registry(registry, schema=self.schema, known_capability_ids={"SESSION_CONTINUITY"})
        self.assertIn("LIVE_CERT_SESSION_SELF_RUNS_NOT_DISTINCT:CERT-SESSION-CONTINUITY-001", errors)
        self.assertIn("LIVE_CERT_SESSION_CONSUMER_RUNS_NOT_DISTINCT:CERT-SESSION-CONTINUITY-001", errors)
        self.assertIn("LIVE_CERT_SESSION_CONSUMER_RUNNERS_NOT_DISTINCT:CERT-SESSION-CONTINUITY-001", errors)

    def test_session_unsupported_class_fail_closed(self):
        cert, _ = self._session_fixture()
        cert["evidence_class"] = "SESSION_MAGIC_PASS"
        registry = seal_registry({"$schema": ".adwf/schemas/capability-live-evidence-certification.schema.json", "schema_version": 1, "role": "CANONICAL_CAPABILITY_LIVE_EVIDENCE_CERTIFICATIONS", "certifications": [cert]})
        errors = validate_certification_registry(registry, schema=self.schema, known_capability_ids={"SESSION_CONTINUITY"})
        self.assertTrue(any(code.startswith("LIVE_CERT_EVIDENCE_CLASS_UNSUPPORTED") for code in errors), errors)

    def test_session_provider_wrong_comment_hash_fails(self):
        registry, _, provider = self._session_registry_and_trace()
        cert = registry["certifications"][-1]
        cert["connected_consumer"]["issue"]["terminal_comment_sha256"] = "0" * 64
        client, _, factory = self._session_clients(cert, provider)
        with mock.patch("lib.github_provider.GitHubClient", side_effect=factory):
            result = verify_provider_certification(client, cert)
        self.assertFalse(result["verified"])
        self.assertIn("LIVE_CERT_SESSION_CONSUMER_PROOF_COMMENT_DIGEST_MISMATCH", result["reason_codes"])

    def test_session_provider_wrong_tag_object_fails(self):
        registry, _, provider = self._session_registry_and_trace()
        cert = registry["certifications"][-1]
        client, _, factory = self._session_clients(cert, provider, wrong_tag=True)
        with mock.patch("lib.github_provider.GitHubClient", side_effect=factory):
            result = verify_provider_certification(client, cert)
        self.assertFalse(result["verified"])
        self.assertTrue(any(code.endswith("_REF_MISMATCH") for code in result["reason_codes"]), result)

    def test_session_provider_blob_safety_substitution_fails(self):
        registry, _, provider = self._session_registry_and_trace()
        cert = registry["certifications"][-1]
        client, _, factory = self._session_clients(cert, provider, wrong_blob_safety=True)
        with mock.patch("lib.github_provider.GitHubClient", side_effect=factory):
            result = verify_provider_certification(client, cert)
        self.assertFalse(result["verified"])
        self.assertIn("LIVE_CERT_SESSION_CONSUMER_B_EVIDENCE_MISMATCH", result["reason_codes"])

    def test_session_provider_consumer_tree_substitution_fails(self):
        registry, _, provider = self._session_registry_and_trace()
        cert = registry["certifications"][-1]
        client, _, factory = self._session_clients(cert, provider, wrong_consumer_tree=True)
        with mock.patch("lib.github_provider.GitHubClient", side_effect=factory):
            result = verify_provider_certification(client, cert)
        self.assertFalse(result["verified"])
        self.assertIn("LIVE_CERT_SESSION_CONSUMER_SUBJECT_MISMATCH", result["reason_codes"])


if __name__ == "__main__":
    unittest.main()

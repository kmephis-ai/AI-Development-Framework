import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib import provider_claim_gateway as GATE
from lib.action_executors import ExecutorWait, _stage1_resources, _writer_branch, _writer_id
from lib.provider_contracts import ProviderContractError

MAIN = "a" * 40


class Client:
    repo = "owner/repo"
    token = "token"

    def __init__(self):
        self.main = MAIN
        self.branch_refs = {}
        self.issue = {"number": 7, "state": "open", "title": "[P0][GOV-033] Test work"}

    def collaborator_permission(self, login): return {"permission": "admin"}
    def repo_info(self): return {"default_branch": "main"}
    def branch(self, name): return {"commit": {"sha": self.main}}
    def get(self, path):
        if path.endswith("/issues/7"): return dict(self.issue)
        raise AssertionError(path)
    def rulesets(self): return []
    def git_ref(self, branch):
        if branch not in self.branch_refs: raise ProviderContractError("PROVIDER_HTTP_404")
        return {"object": {"sha": self.branch_refs[branch]}}
    def create_ref(self, branch, sha):
        self.branch_refs[branch] = sha
        return {"ref": "refs/heads/" + branch, "object": {"sha": sha}}


def request_body(**overrides):
    values = dict(request_id="gov033-source-a1", issue_id=7, roadmap_id="GOV-033", expected_main_sha=MAIN, risk="R1")
    values.update(overrides)
    return GATE.build_claim_comment(**values)


def event(body=None, actor="owner", association="OWNER"):
    return {
        "action": "created",
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 7},
        "sender": {"login": actor},
        "comment": {"body": body if body is not None else request_body(), "author_association": association, "user": {"login": actor}},
    }


def write_policy(root):
    (root / ".adwf").mkdir(parents=True, exist_ok=True)
    value = {
        "active_autonomy": "A2", "max_autonomous_risk": "R1", "hard_budget_usd": 0,
        "mandatory_ai_api": False, "max_parallel_writers": 1,
        "rules": {"autonomy_rank": {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4}, "action_min_autonomy": {"claim": "A2"}},
    }
    (root / ".adwf/effective-policy.json").write_text(json.dumps(value), encoding="utf-8")


class ProviderClaimGatewayTests(unittest.TestCase):
    def root(self):
        td = tempfile.TemporaryDirectory(); root = Path(td.name); write_policy(root); self.addCleanup(td.cleanup); return root

    def test_canonical_request_roundtrip_and_tamper_rejection(self):
        body = request_body(); parsed = GATE.parse_claim_comment(body)
        self.assertEqual((parsed["issue_id"], parsed["roadmap_id"]), (7, "GOV-033"))
        with self.assertRaisesRegex(ValueError, "DIGEST"):
            GATE.parse_claim_comment(body.replace('"issue_id":7', '"issue_id":8'))

    def test_unrelated_comment_is_not_claim_transport(self):
        self.assertIsNone(GATE.process_issue_comment_claim(self.root(), event("hello"), Client()))

    def test_schema_forbids_commands_paths_secrets_and_paid_budget(self):
        body = request_body(); request = json.loads(body.split("\n", 1)[1]); request["command"] = "rm -rf /"
        raw = GATE.CLAIM_MARKER + "\n" + json.dumps(request, sort_keys=True, separators=(",", ":"))
        self.assertEqual(GATE.process_issue_comment_claim(self.root(), event(raw), Client())["reason"], "CLAIM_REQUEST_FIELDS_INVALID")
        paid = json.loads(body.split("\n", 1)[1]); paid["monetary_budget_usd"] = 1; paid["request_digest"] = GATE._digest_payload(paid)
        raw = GATE.CLAIM_MARKER + "\n" + json.dumps(paid, sort_keys=True, separators=(",", ":"))
        self.assertEqual(GATE.process_issue_comment_claim(self.root(), event(raw), Client())["reason"], "CLAIM_REQUEST_MONETARY_BUDGET_INVALID")

    def test_actor_must_be_provider_admin(self):
        client = Client(); client.collaborator_permission = lambda login: {"permission": "write"}
        result = GATE.process_issue_comment_claim(self.root(), event(), client)
        self.assertEqual((result["status"], result["reason"]), ("REJECTED", "CLAIM_ACTOR_ADMIN_REQUIRED"))

    def test_stale_base_and_issue_roadmap_mismatch_reject_before_claim(self):
        client = Client()
        with mock.patch.object(GATE, "_local_head", return_value=MAIN):
            stale = GATE.process_issue_comment_claim(self.root(), event(request_body(expected_main_sha="b" * 40)), client)
        self.assertEqual(stale["reason"], "CLAIM_REQUEST_STALE_BASE")
        client.issue["title"] = "[P0][OTHER-001] Wrong"
        with mock.patch.object(GATE, "_local_head", return_value=MAIN):
            mismatch = GATE.process_issue_comment_claim(self.root(), event(), client)
        self.assertEqual(mismatch["reason"], "CLAIM_ISSUE_ROADMAP_ID_MISMATCH")

    def test_policy_free_only_is_required(self):
        root = self.root(); p = json.loads((root / ".adwf/effective-policy.json").read_text()); p["hard_budget_usd"] = 1
        (root / ".adwf/effective-policy.json").write_text(json.dumps(p))
        with mock.patch.object(GATE, "_local_head", return_value=MAIN):
            result = GATE.process_issue_comment_claim(root, event(), Client())
        self.assertEqual(result["reason"], "CLAIM_POLICY_FREE_ONLY_INVALID")

    def test_success_uses_canonical_claim_executor_and_derived_branch(self):
        root = self.root(); client = Client(); store = mock.MagicMock()
        state = {"phase": "CLAIM", "run_id": "claim-gov033-source-a1", "roadmap_id": "GOV-033", "issue_id": "7", "subject_sha": MAIN, "risk": "R1"}
        worker = _writer_id(state); branch = _writer_branch(state); resources = _stage1_resources()
        lease = {"status": "ACTIVE", "lease_id": "11111111-1111-4111-8111-111111111111", "worker_id": worker, "issue_id": "7", "roadmap_id": "GOV-033", "base_sha": MAIN, "branch": branch, "resources": resources}
        store.read.side_effect = [({"revision": 55, "observed_main_sha": MAIN, "leases": []}, "old"), ({"revision": 56, "observed_main_sha": MAIN, "leases": [lease]}, "anchor56")]
        claim = {"phase": "CLAIM", "outcome": "PASS", "metadata": {"lease_model": "PROVIDER_DURABLE_CAS", "lease_id": lease["lease_id"], "lease_anchor": "anchor56", "lease_registry_revision": 56, "branch": branch, "resumed_existing": False}}
        patches = [mock.patch.object(GATE, "_local_head", return_value=MAIN), mock.patch.object(GATE, "verify_rulesets", return_value={"readback_verified": True}), mock.patch.object(GATE, "verify_runtime_anchor_ruleset", return_value={"readback_verified": True}), mock.patch.object(GATE, "GitHubLeaseStore", return_value=store), mock.patch.object(GATE, "claim_executor", return_value=claim)]
        with patches[0], patches[1], patches[2], patches[3], patches[4] as canonical:
            result = GATE.process_issue_comment_claim(root, event(), client)
        self.assertEqual(result["status"], "PASS"); self.assertEqual(client.branch_refs[branch], MAIN); canonical.assert_called_once()
        self.assertFalse(result["merge_authority"]); self.assertEqual(result["monetary_cost_usd"], 0)

    def test_released_same_request_is_replay_rejected_without_claim(self):
        root = self.root(); client = Client(); store = mock.MagicMock()
        state = {"phase": "CLAIM", "run_id": "claim-gov033-source-a1", "roadmap_id": "GOV-033", "issue_id": "7", "subject_sha": MAIN, "risk": "R1"}
        old = {"status": "RELEASED", "worker_id": _writer_id(state), "issue_id": "7", "roadmap_id": "GOV-033", "base_sha": MAIN, "branch": _writer_branch(state), "resources": _stage1_resources()}
        store.read.return_value = ({"revision": 60, "observed_main_sha": MAIN, "leases": [old]}, "anchor")
        patches = [mock.patch.object(GATE, "_local_head", return_value=MAIN), mock.patch.object(GATE, "verify_rulesets", return_value={"readback_verified": True}), mock.patch.object(GATE, "verify_runtime_anchor_ruleset", return_value={"readback_verified": True}), mock.patch.object(GATE, "GitHubLeaseStore", return_value=store), mock.patch.object(GATE, "claim_executor")]
        with patches[0], patches[1], patches[2], patches[3], patches[4] as canonical:
            result = GATE.process_issue_comment_claim(root, event(), client)
        self.assertEqual(result["reason"], "CLAIM_REQUEST_REPLAY_RELEASED"); canonical.assert_not_called()

    def test_incompatible_active_writer_fails_closed(self):
        store = mock.MagicMock(); store.read.return_value = ({"revision": 56, "observed_main_sha": MAIN, "leases": []}, "anchor")
        wait = ExecutorWait("NOT_VERIFIED", "ACTIVE_PROVIDER_LEASE_INCOMPATIBLE", "claim")
        patches = [mock.patch.object(GATE, "_local_head", return_value=MAIN), mock.patch.object(GATE, "verify_rulesets", return_value={"readback_verified": True}), mock.patch.object(GATE, "verify_runtime_anchor_ruleset", return_value={"readback_verified": True}), mock.patch.object(GATE, "GitHubLeaseStore", return_value=store), mock.patch.object(GATE, "claim_executor", return_value=wait)]
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            result = GATE.process_issue_comment_claim(self.root(), event(), Client())
        self.assertEqual(result["status"], "NOT_VERIFIED"); self.assertEqual(result["executor_reason"], "ACTIVE_PROVIDER_LEASE_INCOMPATIBLE")


if __name__ == "__main__": unittest.main()

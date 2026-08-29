import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib import provider_ops_gateway as GATE
from lib.github_provider import GitHubClient
from lib.provider_contracts import HttpResponse, ProviderContractError

MAIN = "a" * 40
HEAD = "b" * 40
NEW = "c" * 40
TREE_BASE = "d" * 40
TREE_HEAD = "e" * 40
TREE_NEW = "f" * 40
LEASE = "11111111-1111-4111-8111-111111111111"
BRANCH = "adwf/gov-034-test"
WORKER = "adwf-runtime:gov-034-test"
SOURCE = [".adwf/lib/example.py"]


def write_policy(root: Path, *, budget=0, max_writers=1):
    (root / ".adwf/policies").mkdir(parents=True, exist_ok=True)
    (root / ".adwf/effective-policy.json").write_text(json.dumps({
        "active_autonomy": "A2", "max_autonomous_risk": "R1", "hard_budget_usd": budget,
        "mandatory_ai_api": False, "max_parallel_writers": max_writers,
        "rules": {"autonomy_rank": {"A0": 0, "A1": 1, "A2": 2, "A3": 3, "A4": 4}},
    }), encoding="utf-8")
    (root / ".adwf/policies/trust-boundary.json").write_text(json.dumps({
        "paths": [".adwf/**", ".github/workflows/**"], "weakening_requires_human": True,
        "weakening_is_risk": "R4", "self_modification_in_feature_pr": "FORBIDDEN",
    }), encoding="utf-8")


def request_body(**overrides):
    values = dict(
        request_id="gov034-test-a1", issue_id=291, roadmap_id="GOV-034", expected_main_sha=MAIN,
        pr_number=99, base_sha=MAIN, head_sha=HEAD, branch=BRANCH, worker_id=WORKER,
        lease_id=LEASE, lease_registry_revision=67, source_paths=list(SOURCE),
    )
    values.update(overrides)
    return GATE.build_provider_ops_comment(**values)


def event(body=None, actor="owner", association="OWNER"):
    return {
        "action": "created", "repository": {"full_name": "owner/repo"}, "issue": {"number": 291},
        "sender": {"login": actor},
        "comment": {"body": body if body is not None else request_body(), "author_association": association, "user": {"login": actor}},
    }


class Client:
    repo = "owner/repo"
    token = "token"

    def __init__(self):
        self.main = MAIN
        self.branch_sha = HEAD
        self.pr_sha = HEAD
        self.issue_title = "[P0][GOV-034] Test"
        self.pr_body = ""
        self.pr_fork = False
        self.updated = []
        self.blobs = []
        self.created_tree_sha = TREE_NEW
        self.created_commit_sha = NEW
        self.commit_nodes = {
            MAIN: {"sha": MAIN, "tree": {"sha": TREE_BASE}, "parents": [], "message": "base"},
            HEAD: {"sha": HEAD, "tree": {"sha": TREE_HEAD}, "parents": [{"sha": MAIN}], "message": "head"},
            NEW: {"sha": NEW, "tree": {"sha": TREE_NEW}, "parents": [{"sha": HEAD}], "message": ""},
        }
        self.tree_payloads = {}
        self.blob_payloads = {}

    def collaborator_permission(self, login): return {"permission": "admin"}
    def repo_info(self): return {"default_branch": "main"}
    def branch(self, name): return {"commit": {"sha": self.main}}
    def get(self, path):
        if path.endswith("/issues/291"):
            return {"number": 291, "state": "open", "title": self.issue_title}
        raise AssertionError(path)
    def pull(self, number):
        return {"number": 99, "state": "open", "body": self.pr_body, "user": {"login": "owner"},
                "base": {"sha": MAIN, "ref": "main"},
                "head": {"sha": self.pr_sha, "ref": BRANCH, "repo": {"full_name": self.repo, "fork": self.pr_fork}}}
    def pull_reviews(self, number): return []
    def rulesets(self): return []
    def git_ref(self, branch): return {"object": {"sha": self.branch_sha}}
    def git_commit(self, sha): return dict(self.commit_nodes[sha])
    def git_tree(self, sha, recursive=False): return dict(self.tree_payloads[sha])
    def git_blob(self, sha): return dict(self.blob_payloads[sha])
    def create_blob(self, content):
        self.blobs.append(bytes(content)); return {"sha": (str(len(self.blobs)) * 40)[:40]}
    def create_tree(self, *, base_tree_sha, entries):
        self.created_tree_args = (base_tree_sha, entries); return {"sha": self.created_tree_sha}
    def create_commit(self, *, message, tree_sha, parent_sha):
        self.created_commit_args = (message, tree_sha, parent_sha)
        self.commit_nodes[NEW] = {"sha": NEW, "tree": {"sha": tree_sha}, "parents": [{"sha": parent_sha}], "message": message}
        return {"sha": self.created_commit_sha}
    def update_branch_ref(self, branch, sha):
        self.updated.append((branch, sha, False)); self.branch_sha = sha; self.pr_sha = sha
        return {"object": {"sha": sha}}


class ProviderOpsRequestTests(unittest.TestCase):
    def root(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup); root = Path(td.name); write_policy(root); return root

    def test_canonical_roundtrip_and_digest_tamper(self):
        body = request_body(); parsed = GATE.parse_provider_ops_comment(body)
        self.assertEqual(parsed["projection_paths"], GATE.PROJECTION_PATHS)
        self.assertEqual(parsed["source_paths"], SOURCE)
        with self.assertRaisesRegex(ValueError, "DIGEST"):
            GATE.parse_provider_ops_comment(body.replace('"pr_number":99', '"pr_number":100'))

    def test_duplicate_json_key_is_rejected(self):
        raw = request_body(); line = raw.split("\n", 1)[1]
        duplicate = line[:-1] + ',"issue_id":291}'
        with self.assertRaisesRegex(ValueError, "JSON"):
            GATE.parse_provider_ops_comment(GATE.PROVIDER_OPS_MARKER + "\n" + duplicate)

    def test_unknown_operation_and_unknown_secret_field_are_rejected(self):
        req = json.loads(request_body().split("\n", 1)[1]); req["operation"] = "PAID_MAGIC"; req["request_digest"] = GATE._digest_payload(req)
        with self.assertRaisesRegex(ValueError, "OPERATION"):
            GATE.parse_provider_ops_comment(GATE.PROVIDER_OPS_MARKER + "\n" + json.dumps(req, sort_keys=True, separators=(",", ":")))
        req = json.loads(request_body().split("\n", 1)[1]); req["github_token"] = "secret"; req["request_digest"] = GATE._digest_payload(req)
        with self.assertRaisesRegex(ValueError, "FIELDS"):
            GATE.parse_provider_ops_comment(GATE.PROVIDER_OPS_MARKER + "\n" + json.dumps(req, sort_keys=True, separators=(",", ":")))

    def test_paid_and_unsafe_paths_rejected(self):
        req = json.loads(request_body().split("\n", 1)[1]); req["monetary_budget_usd"] = 1; req["request_digest"] = GATE._digest_payload(req)
        with self.assertRaisesRegex(ValueError, "BUDGET"):
            GATE.parse_provider_ops_comment(GATE.PROVIDER_OPS_MARKER + "\n" + json.dumps(req, sort_keys=True, separators=(",", ":")))
        for path in ("../x", "/x", "a\\b", "a/./b"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "PATH"):
                request = json.loads(request_body().split("\n", 1)[1]); request["source_paths"] = [path]; request["request_digest"] = GATE._digest_payload(request)
                GATE.parse_provider_ops_comment(GATE.PROVIDER_OPS_MARKER + "\n" + json.dumps(request, sort_keys=True, separators=(",", ":")))

    def test_projection_paths_are_fixed_and_preedits_cannot_be_declared_source(self):
        for mutate in (lambda r: r.update(projection_paths=["MANIFEST.json"]), lambda r: r.update(source_paths=["MANIFEST.json"])):
            req = json.loads(request_body().split("\n", 1)[1]); mutate(req); req["request_digest"] = GATE._digest_payload(req)
            raw = GATE.PROVIDER_OPS_MARKER + "\n" + json.dumps(req, sort_keys=True, separators=(",", ":"))
            with self.assertRaisesRegex(ValueError, "PROJECTION"):
                GATE.parse_provider_ops_comment(raw)

    def test_unrelated_comment_not_routed(self):
        self.assertIsNone(GATE.process_issue_comment_provider_ops(self.root(), event("hello"), Client()))

    def test_non_admin_actor_and_policy_cost_fail_closed(self):
        client = Client(); client.collaborator_permission = lambda login: {"permission": "write"}
        result = GATE.process_issue_comment_provider_ops(self.root(), event(), client)
        self.assertEqual((result["status"], result["reason"]), ("REJECTED", "PROVIDER_OPS_ACTOR_ADMIN_REQUIRED"))
        root = self.root(); write_policy(root, budget=1)
        result = GATE.process_issue_comment_provider_ops(root, event(), Client())
        self.assertEqual(result["reason"], "PROVIDER_OPS_POLICY_FREE_ONLY_INVALID")


class ProviderObjectTests(unittest.TestCase):
    def tree_client(self, rows, *, truncated=False):
        client = Client(); client.tree_payloads[TREE_HEAD] = {"sha": TREE_HEAD, "truncated": truncated, "tree": rows}; return client

    def test_tree_truncation_symlink_submodule_and_duplicate_rejected(self):
        cases = [
            ({"sha": TREE_HEAD, "truncated": True, "tree": []}, "TREE_NOT_COMPLETE"),
            ({"sha": TREE_HEAD, "truncated": False, "tree": [{"path": "x", "type": "blob", "mode": "120000", "sha": "1"*40, "size": 1}]}, "SYMLINK"),
            ({"sha": TREE_HEAD, "truncated": False, "tree": [{"path": "x", "type": "commit", "mode": "160000", "sha": "1"*40}]}, "SUBMODULE"),
            ({"sha": TREE_HEAD, "truncated": False, "tree": [
                {"path": "x", "type": "blob", "mode": "100644", "sha": "1"*40, "size": 1},
                {"path": "x", "type": "blob", "mode": "100644", "sha": "2"*40, "size": 1},
            ]}, "DUPLICATE"),
        ]
        for payload, reason in cases:
            with self.subTest(reason=reason):
                client = Client(); client.tree_payloads[TREE_HEAD] = payload
                with self.assertRaisesRegex(ValueError, reason): GATE._tree_files(client, TREE_HEAD)

    def test_tree_effect_includes_mode_only_change(self):
        base = {"a": {"sha": "1"*40, "mode": "100644", "size": 1}}
        head = {"a": {"sha": "1"*40, "mode": "100755", "size": 1}}
        self.assertEqual(GATE._tree_effect(base, head)[0]["status"], "M")

    def test_blob_missing_invalid_base64_size_and_oversize_rejected(self):
        client = Client(); entry = {"sha": "1"*40, "size": 3, "mode": "100644"}
        client.blob_payloads[entry["sha"]] = {"sha": entry["sha"], "encoding": "base64", "content": "!!!"}
        with self.assertRaisesRegex(ValueError, "BASE64"): GATE._blob_bytes(client, entry)
        client.blob_payloads[entry["sha"]] = {"sha": entry["sha"], "encoding": "base64", "content": base64.b64encode(b"xx").decode()}
        with self.assertRaisesRegex(ValueError, "SIZE"): GATE._blob_bytes(client, entry)
        huge = {"sha": "2"*40, "size": GATE._MAX_CHANGED_BLOB_BYTES + 1, "mode": "100644"}
        with self.assertRaisesRegex(ValueError, "TOO_LARGE"): GATE._blob_bytes(client, huge)

    def test_ancestry_nonancestor_and_malformed_commit_rejected(self):
        client = Client(); cache = {}; GATE._prove_ancestor(client, MAIN, HEAD, cache)
        client.commit_nodes[HEAD] = {"sha": HEAD, "tree": {"sha": TREE_HEAD}, "parents": [], "message": "head"}
        with self.assertRaisesRegex(ValueError, "NOT_ANCESTOR"): GATE._prove_ancestor(client, MAIN, HEAD, {})
        client.commit_nodes[HEAD] = {"sha": "0"*40, "tree": {"sha": TREE_HEAD}, "parents": [], "message": "head"}
        with self.assertRaisesRegex(ValueError, "READBACK"): GATE._commit_node(client, HEAD, {})


class LiveIdentityTests(unittest.TestCase):
    def root(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup); root = Path(td.name); write_policy(root); return root
    def parsed(self): return GATE.parse_provider_ops_comment(request_body())

    def live(self, client):
        with mock.patch.object(GATE, "_local_head", return_value=MAIN), mock.patch.object(GATE, "_rulesets"), mock.patch.object(GATE, "_lease_identity", return_value=({"status": "ACTIVE"}, "anchor")):
            return GATE._live_identity(self.root(), client, self.parsed())

    def test_stale_main_pr_branch_fork_issue_roadmap_fail_closed(self):
        mutations = [
            (lambda c: setattr(c, "main", "9"*40), "MAIN_DRIFT"),
            (lambda c: setattr(c, "branch_sha", "9"*40), "HEAD_DRIFT"),
            (lambda c: setattr(c, "pr_fork", True), "HEAD_REPOSITORY"),
            (lambda c: setattr(c, "issue_title", "[P0][OTHER-001] Wrong"), "ROADMAP"),
        ]
        for mutate, reason in mutations:
            with self.subTest(reason=reason):
                client = Client(); mutate(client)
                with self.assertRaisesRegex(ValueError, reason): self.live(client)

    def test_ruleset_and_lease_mismatch_are_not_silently_passed(self):
        client = Client(); req = self.parsed(); root = self.root()
        with mock.patch.object(GATE, "_local_head", return_value=MAIN), mock.patch.object(GATE, "_rulesets", side_effect=ValueError("RULESET_DRIFT")):
            with self.assertRaisesRegex(ValueError, "RULESET_DRIFT"): GATE._live_identity(root, client, req)
        with mock.patch.object(GATE, "_local_head", return_value=MAIN), mock.patch.object(GATE, "_rulesets"), mock.patch.object(GATE, "_lease_identity", side_effect=ValueError("LEASE_MISMATCH")):
            with self.assertRaisesRegex(ValueError, "LEASE_MISMATCH"): GATE._live_identity(root, client, req)

    def test_expired_or_wrong_lease_rejected(self):
        req = self.parsed(); store = mock.MagicMock()
        lease = {"status": "ACTIVE", "lease_id": LEASE, "worker_id": WORKER, "issue_id": "291", "roadmap_id": "GOV-034", "base_sha": MAIN, "branch": BRANCH,
                 "resources": [{"global": True, "kind": "provider", "scope": "global", "shared": True}],
                 "expires_at": "2000-01-01T00:00:00Z", "heartbeat_at": "2000-01-01T00:00:00Z"}
        store.read.return_value = ({"revision": 67, "observed_main_sha": MAIN, "leases": [lease]}, "anchor")
        with mock.patch.object(GATE, "GitHubLeaseStore", return_value=store):
            with self.assertRaisesRegex(ValueError, "NOT_FRESH"): GATE._lease_identity(Client(), req)
        lease["expires_at"] = "2999-01-01T00:00:00Z"; lease["heartbeat_at"] = "2999-01-01T00:00:00Z"; lease["worker_id"] = "wrong"
        with mock.patch.object(GATE, "GitHubLeaseStore", return_value=store):
            with self.assertRaisesRegex(ValueError, "IDENTITY"): GATE._lease_identity(Client(), req)


class GeneratorTests(unittest.TestCase):
    def test_sensitive_environment_is_removed(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "secret", "GH_TOKEN": "secret2", "MY_API_KEY": "x", "SAFE_VALUE": "ok", "PYTHONPATH": "candidate"}, clear=True):
            env = GATE._sanitized_env()
        self.assertEqual(env["SAFE_VALUE"], "ok")
        self.assertNotIn("GITHUB_TOKEN", env); self.assertNotIn("GH_TOKEN", env); self.assertNotIn("MY_API_KEY", env); self.assertNotIn("PYTHONPATH", env)

    def test_generator_failure_timeout_and_non_idempotence_fail_closed(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1, stdout="", stderr="bad")):
            with self.assertRaisesRegex(ValueError, "GENERATOR_FAILED"): GATE._run_generator(["python"], root=ROOT, env={})
        with mock.patch("subprocess.run", side_effect=subprocess_timeout()):
            with self.assertRaisesRegex(ValueError, "TIMEOUT"): GATE._run_generator(["python"], root=ROOT, env={})

    def test_unexpected_generator_path_is_detected(self):
        with tempfile.TemporaryDirectory() as td:
            candidate = Path(td); (candidate / ".adwf").mkdir(); (candidate / "keep.txt").write_text("before")
            for p in GATE.PROJECTION_PATHS:
                path = candidate / p; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("old")
            calls = {"n": 0}
            def mutate(*args, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1: (candidate / "keep.txt").write_text("changed")
            with mock.patch.object(GATE, "_run_generator", side_effect=mutate):
                with self.assertRaisesRegex(ValueError, "UNEXPECTED_PATH"): GATE._generate_projections(ROOT, candidate)


def subprocess_timeout():
    import subprocess
    return subprocess.TimeoutExpired(["python"], 1)


class TransportTests(unittest.TestCase):
    def test_git_data_transport_payloads_and_force_false(self):
        calls = []
        def transport(method, url, headers, body, timeout):
            payload = json.loads(body.decode()) if body else None; calls.append((method, url, payload))
            if url.endswith("/git/blobs"): response = {"sha": "1"*40}
            elif url.endswith("/git/trees"): response = {"sha": "2"*40}
            elif url.endswith("/git/commits"): response = {"sha": "3"*40}
            else: response = {"object": {"sha": "3"*40}}
            return HttpResponse(201 if method == "POST" else 200, {}, json.dumps(response).encode())
        client = GitHubClient("owner/repo", "token", transport=transport, api_base="https://api.invalid")
        client.create_blob(b"abc")
        client.create_tree(base_tree_sha="a"*40, entries=[{"path": "x", "mode": "100644", "type": "blob", "sha": "1"*40}])
        client.create_commit(message="msg", tree_sha="2"*40, parent_sha="b"*40)
        client.update_branch_ref("feature/x", "3"*40)
        self.assertEqual(calls[0][2], {"content": base64.b64encode(b"abc").decode(), "encoding": "base64"})
        self.assertEqual(calls[1][2]["base_tree"], "a"*40)
        self.assertEqual(calls[2][2]["parents"], ["b"*40])
        self.assertEqual(calls[3][2], {"sha": "3"*40, "force": False})

    def test_blob_api_rejects_non_bytes(self):
        client = GitHubClient("owner/repo", "token", transport=lambda *a: None)
        with self.assertRaisesRegex(ProviderContractError, "CONTENT_INVALID"): client.create_blob("text")


class FullFlowTests(unittest.TestCase):
    def root(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup); root = Path(td.name); write_policy(root); return root

    def common_patches(self, client, classification=None, generated=None):
        request = GATE.parse_provider_ops_comment(request_body())
        pr = client.pull(99)
        live_pre = {"main_sha": MAIN, "pr": pr, "branch_sha": HEAD, "lease": {}, "lease_anchor": "anchor"}
        live_post = {"main_sha": MAIN, "pr": client.pull(99), "branch_sha": NEW, "lease": {}, "lease_anchor": "anchor"}
        effect = [{"path": SOURCE[0], "status": "M", "old": {"sha": "1"*40, "mode": "100644", "size": 1}, "new": {"sha": "2"*40, "mode": "100644", "size": 1}}]
        base_files = {SOURCE[0]: effect[0]["old"], **{p: {"sha": str(i+3)*40, "mode": "100644", "size": 1} for i,p in enumerate(GATE.PROJECTION_PATHS)}}
        head_files = {SOURCE[0]: effect[0]["new"], **{p: base_files[p] for p in GATE.PROJECTION_PATHS}}
        new_files = dict(head_files)
        for i,p in enumerate(GATE.PROJECTION_PATHS): new_files[p] = {"sha": str(i+7)*40, "mode": "100644", "size": 2}
        client.tree_payloads[TREE_NEW] = {"sha": TREE_NEW, "truncated": False, "tree": []}
        return request, pr, live_pre, live_post, effect, base_files, head_files, new_files

    def test_success_one_child_commit_only_projections_and_no_merge_authority(self):
        client = Client(); req, pr, pre, post, effect, base_files, head_files, new_files = self.common_patches(client)
        client.commit_nodes[NEW]["message"] = GATE._commit_message(req)
        calls = {"n": 0}
        def live(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return pre
            return {"main_sha": MAIN, "pr": client.pull(99), "branch_sha": NEW, "lease": {}, "lease_anchor": "anchor"}
        def trees(client_arg, sha):
            return {TREE_BASE: base_files, TREE_HEAD: head_files, TREE_NEW: new_files}[sha]
        with mock.patch.object(GATE, "_live_identity", side_effect=live), \
             mock.patch.object(GATE, "_prove_ancestor"), \
             mock.patch.object(GATE, "_commit_node", side_effect=lambda c,s,cache: {MAIN:{"tree_sha":TREE_BASE,"parents":[],"message":"base"},HEAD:{"tree_sha":TREE_HEAD,"parents":[MAIN],"message":"head"},NEW:{"tree_sha":TREE_NEW,"parents":[HEAD],"message":GATE._commit_message(req)}}[s]), \
             mock.patch.object(GATE, "_tree_files", side_effect=trees), \
             mock.patch.object(GATE, "_trust_classification", return_value={"result":"ALLOW","authorization_mode":"STANDING_OWNER_POLICY"}), \
             mock.patch.object(GATE, "_materialize_candidate_root"), \
             mock.patch.object(GATE, "_generate_projections", return_value={p:(p+"\n").encode() for p in GATE.PROJECTION_PATHS}), \
             mock.patch.object(GATE, "_policy_gate", return_value=None):
            result = GATE.process_issue_comment_provider_ops(self.root(), event(), client)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(client.updated, [(BRANCH, NEW, False)])
        self.assertEqual(result["changed_paths"], GATE.PROJECTION_PATHS)
        self.assertFalse(result["merge_authority"]); self.assertFalse(result["issue_close_authority"]); self.assertEqual(result["monetary_cost_usd"], 0)
        self.assertEqual(client.created_commit_args[2], HEAD)

    def test_human_required_without_exact_head_attestation_is_rejected_before_git_mutation(self):
        client = Client(); req, pr, pre, post, effect, base_files, head_files, new_files = self.common_patches(client)
        with mock.patch.object(GATE, "_live_identity", return_value=pre), mock.patch.object(GATE, "_prove_ancestor"), \
             mock.patch.object(GATE, "_commit_node", side_effect=lambda c,s,cache: {MAIN:{"tree_sha":TREE_BASE,"parents":[]},HEAD:{"tree_sha":TREE_HEAD,"parents":[MAIN]}}[s]), \
             mock.patch.object(GATE, "_tree_files", side_effect=lambda c,s:{TREE_BASE:base_files,TREE_HEAD:head_files}[s]), \
             mock.patch.object(GATE, "_trust_classification", return_value={"result":"HUMAN_REQUIRED"}), \
             mock.patch.object(GATE, "_policy_gate", return_value=None):
            result = GATE.process_issue_comment_provider_ops(self.root(), event(), client)
        self.assertEqual(result["reason"], "PROVIDER_OPS_EXACT_HEAD_AUTHORIZATION_REQUIRED")
        self.assertFalse(client.blobs); self.assertFalse(client.updated)

    def test_projection_preedit_source_effect_mismatch_and_generator_failure_have_no_ref_mutation(self):
        for case in ("effect", "projection", "generator"):
            client = Client(); req, pr, pre, post, effect, base_files, head_files, new_files = self.common_patches(client)
            if case == "effect": effect2 = effect + [{"path":"extra.txt","status":"A","old":None,"new":{"sha":"9"*40,"mode":"100644","size":1}}]
            else: effect2 = effect
            if case == "projection": head_files = dict(head_files); head_files["MANIFEST.json"] = {"sha":"9"*40,"mode":"100644","size":1}
            patches = [
                mock.patch.object(GATE, "_live_identity", return_value=pre), mock.patch.object(GATE, "_prove_ancestor"),
                mock.patch.object(GATE, "_commit_node", side_effect=lambda c,s,cache: {MAIN:{"tree_sha":TREE_BASE,"parents":[]},HEAD:{"tree_sha":TREE_HEAD,"parents":[MAIN]}}[s]),
                mock.patch.object(GATE, "_tree_files", side_effect=lambda c,s:{TREE_BASE:base_files,TREE_HEAD:head_files}[s]),
                mock.patch.object(GATE, "_tree_effect", return_value=effect2), mock.patch.object(GATE, "_trust_classification", return_value={"result":"ALLOW"}),
                mock.patch.object(GATE, "_materialize_candidate_root"), mock.patch.object(GATE, "_policy_gate", return_value=None),
                mock.patch.object(GATE, "_generate_projections", side_effect=ValueError("GENERATOR_FAILED") if case=="generator" else None),
            ]
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7], patches[8]:
                result = GATE.process_issue_comment_provider_ops(self.root(), event(), client)
            self.assertFalse(client.updated, case)
            self.assertNotEqual(result["status"], "PASS", case)

    def test_branch_race_cas_failure_is_not_verified_and_no_force(self):
        client = Client(); req, pr, pre, post, effect, base_files, head_files, new_files = self.common_patches(client)
        client.commit_nodes[NEW]["message"] = GATE._commit_message(req)
        client.update_branch_ref = mock.Mock(side_effect=ProviderContractError("PROVIDER_HTTP_422"))
        with mock.patch.object(GATE, "_live_identity", return_value=pre), mock.patch.object(GATE, "_prove_ancestor"), \
             mock.patch.object(GATE, "_commit_node", side_effect=lambda c,s,cache: {MAIN:{"tree_sha":TREE_BASE,"parents":[]},HEAD:{"tree_sha":TREE_HEAD,"parents":[MAIN],"message":"head"},NEW:{"tree_sha":TREE_NEW,"parents":[HEAD],"message":GATE._commit_message(req)}}[s]), \
             mock.patch.object(GATE, "_tree_files", side_effect=lambda c,s:{TREE_BASE:base_files,TREE_HEAD:head_files,TREE_NEW:new_files}[s]), \
             mock.patch.object(GATE, "_trust_classification", return_value={"result":"ALLOW"}), mock.patch.object(GATE, "_materialize_candidate_root"), \
             mock.patch.object(GATE, "_generate_projections", return_value={p:b"x" for p in GATE.PROJECTION_PATHS}), mock.patch.object(GATE, "_policy_gate", return_value=None):
            result = GATE.process_issue_comment_provider_ops(self.root(), event(), client)
        self.assertEqual(result["status"], "NOT_VERIFIED"); self.assertIn("BRANCH_CAS_FAILED", result["reason"])

    def test_replay_exact_child_returns_already_applied_without_second_commit(self):
        client = Client(); client.branch_sha = NEW; client.pr_sha = NEW
        req = GATE.parse_provider_ops_comment(request_body()); client.commit_nodes[NEW]["message"] = GATE._commit_message(req)
        parent_files = {"x": {"sha":"1"*40,"mode":"100644","size":1}}
        current_files = dict(parent_files)
        for i,p in enumerate(GATE.PROJECTION_PATHS):
            parent_files[p] = {"sha":str(i+2)*40,"mode":"100644","size":1}; current_files[p] = {"sha":str(i+6)*40,"mode":"100644","size":1}
        live = {"main_sha": MAIN, "pr": client.pull(99), "branch_sha": NEW, "lease": {}, "lease_anchor": "anchor"}
        with mock.patch.object(GATE, "_live_identity", return_value=live), mock.patch.object(GATE, "_commit_node", side_effect=lambda c,s,cache:{HEAD:{"tree_sha":TREE_HEAD,"parents":[MAIN],"message":"head"},NEW:{"tree_sha":TREE_NEW,"parents":[HEAD],"message":GATE._commit_message(req)}}[s]), \
             mock.patch.object(GATE, "_tree_files", side_effect=lambda c,s:{TREE_HEAD:parent_files,TREE_NEW:current_files}[s]), mock.patch.object(GATE, "_policy_gate", return_value=None):
            result = GATE.process_issue_comment_provider_ops(self.root(), event(), client)
        self.assertEqual(result["status"], "ALREADY_APPLIED"); self.assertFalse(result["mutation"]); self.assertFalse(client.blobs); self.assertFalse(client.updated)


RECON_MAIN = "9" * 40
RECON_PR_HEAD = "8" * 40
RECON_ANCHOR = "7" * 40


def reconcile_body(**overrides):
    values = dict(
        request_id="gov034-reconcile-a1", issue_id=291, roadmap_id="GOV-034",
        expected_main_sha=RECON_MAIN, lease_registry_revision=82, lease_id=LEASE,
        lease_base_sha=MAIN, branch=BRANCH, worker_id=WORKER, pr_number=99,
        pr_head_sha=RECON_PR_HEAD,
    )
    values.update(overrides)
    return GATE.build_lease_reconcile_comment(**values)


def reconcile_event(body=None, actor="owner", association="OWNER"):
    return {
        "action": "created", "repository": {"full_name": "owner/repo"}, "issue": {"number": 291},
        "sender": {"login": actor},
        "comment": {"body": body if body is not None else reconcile_body(), "author_association": association, "user": {"login": actor}},
    }


class ReconcileClient(Client):
    def __init__(self, *, merged=True, main=RECON_MAIN, pr_head=RECON_PR_HEAD):
        super().__init__()
        self.main = main
        self.branch_sha = pr_head
        self.pr_sha = pr_head
        self.merged = merged
        self.pr_fork = False
        self.merge_commit_sha = main if merged else None
        self.commit_nodes[main] = {"sha": main, "tree": {"sha": TREE_NEW}, "parents": [{"sha": MAIN}], "message": "merged"}

    def pull(self, number):
        return {
            "number": 99, "state": "closed" if self.merged else "open", "merged": self.merged,
            "merge_commit_sha": self.merge_commit_sha, "body": "", "user": {"login": "owner"},
            "base": {"sha": MAIN, "ref": "main"},
            "head": {"sha": self.pr_sha, "ref": BRANCH, "repo": {"full_name": self.repo, "fork": self.pr_fork}},
        }


class FakeLeaseStore:
    def __init__(self, registry, *, fail_release=None):
        self.registry = registry
        self.fail_release = fail_release
        self.release_calls = []

    def read(self, *, expected_main_sha, policy_max_parallel_writers):
        return self.registry, RECON_ANCHOR

    def release(self, **kwargs):
        self.release_calls.append(dict(kwargs))
        if self.fail_release:
            raise ValueError(self.fail_release)
        target = next(x for x in self.registry["leases"] if x["lease_id"] == kwargs["lease_id"])
        target["status"] = "RELEASED"
        target["released_at"] = "2026-08-29T11:20:00Z"
        target["provider_reconciled_at"] = "2026-08-29T11:20:00Z"
        target["provider_reconciliation_ref"] = kwargs["provider_reconciliation_ref"]
        self.registry["revision"] += 1
        self.registry["observed_main_sha"] = kwargs["expected_main_sha"]
        return self.registry, "6" * 40


def reconcile_registry(*, status="ACTIVE", revision=82, base=MAIN, branch=BRANCH, worker=WORKER, lease_id=LEASE):
    return {
        "revision": revision, "observed_main_sha": MAIN, "max_parallel_writers": 1,
        "leases": [{
            "lease_id": lease_id, "worker_id": worker, "issue_id": "291", "roadmap_id": "GOV-034",
            "base_sha": base, "branch": branch,
            "resources": [{"global": True, "kind": "provider", "scope": "global", "shared": True}],
            "status": status, "heartbeat_at": "2026-08-29T10:00:00Z", "expires_at": "2026-08-29T12:00:00Z",
            "released_at": None, "provider_reconciled_at": None, "provider_reconciliation_ref": None,
        }],
    }


class ProviderOpsLeaseReconcileTests(unittest.TestCase):
    def root(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup); root = Path(td.name); write_policy(root); return root

    def process(self, *, client=None, store=None, freshness=None, body=None):
        client = client or ReconcileClient()
        store = store or FakeLeaseStore(reconcile_registry())
        freshness = [] if freshness is None else freshness
        with mock.patch.object(GATE, "GitHubLeaseStore", return_value=store), \
             mock.patch.object(GATE, "_rulesets"), \
             mock.patch.object(GATE, "_local_head", return_value=client.main), \
             mock.patch.object(GATE, "_registry_lease_freshness_errors", return_value=freshness):
            result = GATE.process_issue_comment_provider_ops(self.root(), reconcile_event(body), client)
        return result, store

    def test_reconcile_request_roundtrip_is_operation_discriminated(self):
        body = reconcile_body(); req = GATE.parse_provider_ops_comment(body)
        self.assertEqual(req["operation"], GATE.LEASE_RECONCILE)
        self.assertEqual(set(req), GATE.LEASE_RECONCILE_REQUEST_FIELDS)
        raw = json.loads(body.split("\n", 1)[1]); raw["provider_reconciliation_ref"] = "caller:forged"; raw["request_digest"] = GATE._digest_payload(raw)
        with self.assertRaisesRegex(ValueError, "FIELDS"):
            GATE.parse_provider_ops_comment(GATE.PROVIDER_OPS_MARKER + "\n" + json.dumps(raw, sort_keys=True, separators=(",", ":")))

    def test_reconcile_paid_digest_tamper_and_field_substitution_rejected(self):
        raw = json.loads(reconcile_body().split("\n", 1)[1]); raw["monetary_budget_usd"] = 1; raw["request_digest"] = GATE._digest_payload(raw)
        with self.assertRaisesRegex(ValueError, "BUDGET"):
            GATE.parse_provider_ops_comment(GATE.PROVIDER_OPS_MARKER + "\n" + json.dumps(raw, sort_keys=True, separators=(",", ":")))
        with self.assertRaisesRegex(ValueError, "DIGEST"):
            GATE.parse_provider_ops_comment(reconcile_body().replace('"pr_number":99', '"pr_number":98'))

    def test_fresh_open_active_lease_is_not_releasable(self):
        client = ReconcileClient(merged=False, main=MAIN)
        body = reconcile_body(expected_main_sha=MAIN)
        result, store = self.process(client=client, freshness=[], body=body)
        self.assertEqual(result["reason"], "PROVIDER_OPS_FRESH_ACTIVE_LEASE_NOT_RELEASABLE")
        self.assertEqual(store.release_calls, [])

    def test_stale_open_pr_releases_exactly_one_lease_and_preserves_refs(self):
        client = ReconcileClient(merged=False, main=MAIN)
        body = reconcile_body(expected_main_sha=MAIN)
        result, store = self.process(client=client, freshness=["LEASE_HEARTBEAT_STALE"], body=body)
        self.assertEqual((result["status"], result["operation"], result["mutation"]), ("PASS", GATE.LEASE_RECONCILE, True))
        self.assertEqual(result["lease_registry_revision_after"], 83)
        self.assertEqual((client.main, client.branch_sha, client.pr_sha), (MAIN, RECON_PR_HEAD, RECON_PR_HEAD))
        self.assertEqual(len(store.release_calls), 1)
        self.assertTrue(store.release_calls[0]["provider_reconciled"])
        self.assertEqual(store.release_calls[0]["provider_reconciliation_ref"], result["provider_reconciliation_ref"])

    def test_provider_verified_merge_can_release_fresh_lease(self):
        result, store = self.process(freshness=[])
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(store.release_calls), 1)

    def test_wrong_revision_identity_fork_and_head_drift_cause_no_release(self):
        cases = []
        cases.append((FakeLeaseStore(reconcile_registry(revision=83)), ReconcileClient(), None))
        cases.append((FakeLeaseStore(reconcile_registry(worker="other")), ReconcileClient(), None))
        fork = ReconcileClient(); fork.pr_fork = True; cases.append((FakeLeaseStore(reconcile_registry()), fork, None))
        head = ReconcileClient(pr_head="5" * 40); cases.append((FakeLeaseStore(reconcile_registry()), head, None))
        for store, client, body in cases:
            with self.subTest(client=client, revision=store.registry["revision"]):
                result, _ = self.process(client=client, store=store, freshness=["LEASE_HEARTBEAT_STALE"], body=body)
                self.assertNotEqual(result["status"], "PASS")
                self.assertEqual(store.release_calls, [])

    def test_closed_unmerged_pr_and_unverified_merge_commit_are_rejected(self):
        client = ReconcileClient(merged=False, main=MAIN)
        original = client.pull
        client.pull = lambda n: {**original(n), "state": "closed"}
        result, store = self.process(client=client, freshness=["LEASE_HEARTBEAT_STALE"], body=reconcile_body(expected_main_sha=MAIN))
        self.assertIn("PR_NOT_OPEN_OR_MERGED", result["reason"]); self.assertEqual(store.release_calls, [])
        client = ReconcileClient(); client.merge_commit_sha = "4" * 40
        result, store = self.process(client=client, freshness=[])
        self.assertIn("MERGED_PR_MAIN_IDENTITY_NOT_VERIFIED", result["reason"]); self.assertEqual(store.release_calls, [])
        client = ReconcileClient(); original = client.pull
        client.pull = lambda n: {**original(n), "base": {"sha": "3" * 40, "ref": "main"}}
        result, store = self.process(client=client, freshness=[])
        self.assertIn("PR_BASE_MISMATCH", result["reason"]); self.assertEqual(store.release_calls, [])

    def test_registry_observed_main_mismatch_is_not_releasable(self):
        registry = reconcile_registry(); registry["observed_main_sha"] = "2" * 40
        store = FakeLeaseStore(registry)
        result, _ = self.process(store=store, freshness=["LEASE_HEARTBEAT_STALE"])
        self.assertIn("LEASE_OBSERVED_MAIN_MISMATCH", result["reason"]); self.assertEqual(store.release_calls, [])

    def test_replay_is_already_applied_without_second_registry_mutation(self):
        req = GATE.parse_provider_ops_comment(reconcile_body())
        registry = reconcile_registry(status="RELEASED", revision=83); registry["observed_main_sha"] = RECON_MAIN
        lease = registry["leases"][0]
        lease["released_at"] = lease["provider_reconciled_at"] = "2026-08-29T11:20:00Z"
        lease["provider_reconciliation_ref"] = GATE._reconcile_ref(req)
        store = FakeLeaseStore(registry)
        result, _ = self.process(store=store, freshness=[])
        self.assertEqual((result["status"], result["mutation"]), ("ALREADY_APPLIED", False))
        self.assertEqual(store.release_calls, [])

    def test_cas_failure_is_not_verified_and_does_not_claim_success(self):
        store = FakeLeaseStore(reconcile_registry(), fail_release="LEASE_PROVIDER_CAS_LOST:revision=83")
        result, _ = self.process(store=store, freshness=[])
        self.assertEqual(result["status"], "NOT_VERIFIED")
        self.assertIn("CAS_FAILED", result["reason"])

    def test_invalid_future_or_malformed_freshness_fails_closed(self):
        result, store = self.process(freshness=["LEASE_HEARTBEAT_IN_FUTURE"])
        self.assertIn("FRESHNESS_NOT_VERIFIED", result["reason"])
        self.assertEqual(store.release_calls, [])



def unchanged_body(**overrides):
    values = dict(
        request_id="gov034-unchanged-a1", issue_id=291, roadmap_id="GOV-034",
        expected_main_sha=MAIN, lease_registry_revision=90, lease_id=LEASE,
        lease_base_sha=MAIN, branch=BRANCH, worker_id=WORKER, branch_head_sha=MAIN,
    )
    values.update(overrides)
    return GATE.build_lease_release_unchanged_comment(**values)


def unchanged_event(body=None, actor="owner", association="OWNER"):
    return {
        "action": "created", "repository": {"full_name": "owner/repo"}, "issue": {"number": 291},
        "sender": {"login": actor},
        "comment": {"body": body if body is not None else unchanged_body(), "author_association": association, "user": {"login": actor}},
    }


class ProviderOpsLeaseReleaseUnchangedTests(unittest.TestCase):
    def root(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup); root = Path(td.name); write_policy(root); return root

    def process(self, *, client=None, store=None, freshness=None, body=None):
        if client is None:
            client = Client()
            client.branch_sha = MAIN
        client.main = MAIN
        store = store or FakeLeaseStore(reconcile_registry(revision=90))
        freshness = [] if freshness is None else freshness
        with mock.patch.object(GATE, "GitHubLeaseStore", return_value=store), \
             mock.patch.object(GATE, "_rulesets"), \
             mock.patch.object(GATE, "_local_head", return_value=client.main), \
             mock.patch.object(GATE, "_registry_lease_freshness_errors", return_value=freshness):
            result = GATE.process_issue_comment_provider_ops(self.root(), unchanged_event(body), client)
        return result, store, client

    def test_request_roundtrip_is_pr_free_and_reconciliation_ref_not_caller_controlled(self):
        body = unchanged_body(); req = GATE.parse_provider_ops_comment(body)
        self.assertEqual(req["operation"], GATE.LEASE_RELEASE_UNCHANGED)
        self.assertEqual(set(req), GATE.LEASE_RELEASE_UNCHANGED_REQUEST_FIELDS)
        self.assertNotIn("pr_number", req); self.assertNotIn("provider_reconciliation_ref", req)
        raw = json.loads(body.split("\n", 1)[1]); raw["provider_reconciliation_ref"] = "caller:forged"; raw["request_digest"] = GATE._digest_payload(raw)
        with self.assertRaisesRegex(ValueError, "FIELDS"):
            GATE.parse_provider_ops_comment(GATE.PROVIDER_OPS_MARKER + "\n" + json.dumps(raw, sort_keys=True, separators=(",", ":")))

    def test_request_requires_main_lease_base_and_branch_head_to_match(self):
        with self.assertRaisesRegex(ValueError, "UNCHANGED_BRANCH_IDENTITY"):
            GATE.parse_provider_ops_comment(unchanged_body(branch_head_sha=HEAD))
        with self.assertRaisesRegex(ValueError, "UNCHANGED_BRANCH_IDENTITY"):
            GATE.parse_provider_ops_comment(unchanged_body(lease_base_sha=HEAD))

    def test_fresh_or_stale_unchanged_lease_releases_without_pr_or_source_ref_mutation(self):
        for freshness in ([], ["LEASE_HEARTBEAT_STALE"], ["LEASE_EXPIRED"]):
            with self.subTest(freshness=freshness):
                result, store, client = self.process(freshness=freshness)
                self.assertEqual((result["status"], result["operation"], result["mutation"]), ("PASS", GATE.LEASE_RELEASE_UNCHANGED, True))
                self.assertEqual(result["lease_registry_revision_after"], 91)
                self.assertEqual((client.main, client.branch_sha), (MAIN, MAIN))
                self.assertEqual(client.updated, [])
                self.assertEqual(len(store.release_calls), 1)
                self.assertTrue(store.release_calls[0]["provider_reconciled"])
                self.assertEqual(store.release_calls[0]["provider_reconciliation_ref"], result["provider_reconciliation_ref"])
                self.assertNotIn("pr/", result["provider_reconciliation_ref"])

    def test_branch_registry_and_lease_identity_drift_cause_zero_release(self):
        cases = []
        branch = Client(); branch.main = MAIN; branch.branch_sha = HEAD
        cases.append((branch, FakeLeaseStore(reconcile_registry(revision=90)), "BRANCH_HEAD_DRIFT"))
        registry = reconcile_registry(revision=90); registry["observed_main_sha"] = HEAD
        good = Client(); good.main = MAIN; good.branch_sha = MAIN
        cases.append((good, FakeLeaseStore(registry), "LEASE_OBSERVED_MAIN_MISMATCH"))
        cases.append((good, FakeLeaseStore(reconcile_registry(revision=90, worker="other")), "LEASE_IDENTITY_MISMATCH"))
        for client, store, reason in cases:
            with self.subTest(reason=reason):
                result, _, _ = self.process(client=client, store=store, freshness=[])
                self.assertIn(reason, result["reason"])
                self.assertEqual(store.release_calls, [])

    def test_replay_is_already_applied_without_second_cas(self):
        req = GATE.parse_provider_ops_comment(unchanged_body())
        registry = reconcile_registry(status="RELEASED", revision=91)
        lease = registry["leases"][0]
        lease["released_at"] = lease["provider_reconciled_at"] = "2026-08-29T18:30:00Z"
        lease["provider_reconciliation_ref"] = GATE._unchanged_ref(req)
        store = FakeLeaseStore(registry)
        result, _, client = self.process(store=store, freshness=[])
        self.assertEqual((result["status"], result["mutation"]), ("ALREADY_APPLIED", False))
        self.assertEqual(store.release_calls, []); self.assertEqual(client.updated, [])

    def test_cas_loss_and_invalid_future_heartbeat_fail_closed(self):
        store = FakeLeaseStore(reconcile_registry(revision=90), fail_release="LEASE_PROVIDER_CAS_LOST:revision=91")
        result, _, _ = self.process(store=store, freshness=[])
        self.assertEqual(result["status"], "NOT_VERIFIED"); self.assertIn("CAS_FAILED", result["reason"])
        store = FakeLeaseStore(reconcile_registry(revision=90))
        result, _, _ = self.process(store=store, freshness=["LEASE_HEARTBEAT_IN_FUTURE"])
        self.assertIn("FRESHNESS_NOT_VERIFIED", result["reason"]); self.assertEqual(store.release_calls, [])

if __name__ == "__main__":
    unittest.main()

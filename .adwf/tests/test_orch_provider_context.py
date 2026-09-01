from __future__ import annotations

import copy
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.autonomous_execution_state import validate_state
from lib.lease_registry import acquire_registry_lease, empty_lease_registry
from lib.orch_provider_context import ProviderContextError, compile_provider_context
from lib.session_continuity import build_checkpoint

SCRIPTS = ROOT / ".adwf" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from run_active_supervisor import run_active_supervisor

REPO = "example/project"
MAIN = "a" * 40
HEAD = "b" * 40
OLD = "c" * 40
RUN_ID = "run-00000001"
ROADMAP = "ORCH_CONTEXT-001"
ISSUE = "307"
BRANCH = "adwf/orch-context-001"
LEASE_ID = "11111111-1111-4111-8111-111111111111"
NOW = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)


def durable(**updates):
    value = {
        "run_id": RUN_ID,
        "roadmap_id": ROADMAP,
        "issue_id": ISSUE,
        "phase": "EXECUTE",
        "status": "RUNNING",
        "subject_sha": HEAD,
        "pull_request_number": 308,
        "work_branch": BRANCH,
        "blockers": [],
        "revision": 4,
    }
    value.update(updates)
    return value


def resources():
    return [{"kind": "provider", "scope": "global", "shared": True, "global": True}]


def registry(*, main=MAIN, worker=None, branch=BRANCH, issue=ISSUE, roadmap=ROADMAP, when=NOW, ttl=120):
    value = empty_lease_registry(REPO, main, max_parallel_writers=1)
    value, _lease = acquire_registry_lease(
        value,
        expected_revision=0,
        observed_main_sha=main,
        policy_max_parallel_writers=1,
        issue_id=issue,
        roadmap_id=roadmap,
        worker_id=worker or "adwf-runtime:" + RUN_ID,
        base_sha=main,
        branch=branch,
        resources=resources(),
        now=when,
        ttl_minutes=ttl,
        lease_id=LEASE_ID,
    )
    return value


class FakeClient:
    repo = REPO

    def __init__(self, *, main=MAIN, head=HEAD, issue_state="open", checks=None, fail=None):
        self.main = main
        self.head = head
        self.issue_state = issue_state
        self.checks = [] if checks is None else checks
        self.fail = fail
        self.calls = []

    def _record(self, name):
        self.calls.append(name)
        if self.fail == name:
            raise RuntimeError("secret provider payload must not escape")

    def repo_info(self):
        self._record("repo_info")
        return {"default_branch": "main"}

    def branch(self, name):
        self._record("branch:" + name)
        return {"commit": {"sha": self.main if name == "main" else self.head}}

    def get(self, path):
        self._record("issue")
        return {"number": int(ISSUE), "state": self.issue_state}

    def pull(self, number):
        self._record("pull")
        return {
            "number": number,
            "state": "open",
            "head": {"sha": self.head, "ref": BRANCH},
            "base": {"sha": self.main, "ref": "main"},
        }

    def check_runs(self, sha):
        self._record("checks")
        return copy.deepcopy(self.checks)


class FakeLeaseStore:
    def __init__(self, value, *, fail=False):
        self.value = value
        self.fail = fail
        self.calls = []

    def read(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("raw lease failure")
        return copy.deepcopy(self.value), "anchor"


class FakeRuntimeStore:
    def __init__(self, checkpoint=None, *, fail=False):
        self.checkpoint = checkpoint
        self.fail = fail
        self.calls = []

    def restore_latest_session_continuity(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("raw checkpoint failure")
        if self.checkpoint is None:
            return None
        return {"checkpoint": copy.deepcopy(self.checkpoint), "reconciliation": {"stale": False}}


def checkpoint(**updates):
    args = dict(
        checkpoint_id="orch-context:1",
        checkpoint_revision=1,
        project_identity=REPO,
        roadmap_id=ROADMAP,
        issue_id=ISSUE,
        lease_identity=LEASE_ID,
        conflict_domains=["provider:global"],
        main_sha=MAIN,
        pr_number=308,
        head_sha=HEAD,
        branch=BRANCH,
        boundary_type="EXECUTOR_LIMIT",
        next_permitted_action="Fresh provider reconciliation.",
        safe_handover_summary="Public safe context only.",
        created_at="2026-09-01T08:59:00Z",
        updated_at="2026-09-01T08:59:00Z",
    )
    args.update(updates)
    return build_checkpoint(**args)


def compile_context(state=None, *, client=None, leases=None, runtime=None):
    return compile_provider_context(
        state or durable(),
        client=client or FakeClient(),
        lease_store=leases or FakeLeaseStore(registry()),
        runtime_store=runtime or FakeRuntimeStore(),
        now=NOW,
    )


class FakeJournal:
    def __init__(self, active):
        self.active = active
        self.calls = 0

    def list_active(self):
        self.calls += 1
        return list(self.active)


class FakeSupervisor:
    def __init__(self):
        self.calls = []

    def tick(self, run_id, *, max_steps):
        self.calls.append((run_id, max_steps))
        return {"status": "WAITING_AGENT"}


class OrchProviderContextTests(unittest.TestCase):
    def assert_no_authority(self, result):
        self.assertFalse(result["provider_write_authorized"])
        self.assertFalse(result["ci_authority"])
        self.assertFalse(result["merge_authority"])

    def test_matching_active_lease_builds_valid_projection(self):
        context = compile_context()
        state = context["execution_state"]
        self.assertEqual(validate_state(state), [])
        self.assertEqual(state["writer"], {"lease_id": LEASE_ID, "lease_state": "ACTIVE"})
        self.assertEqual(context["current_worker_id"], "adwf-runtime:" + RUN_ID)
        self.assertFalse(context["next_work_authorized"])

    def test_human_boundary_only_comes_from_explicit_durable_status(self):
        prose = durable(blockers=["human approval maybe mentioned in prose"])
        context = compile_context(prose)
        self.assertFalse(context["human_boundary_active"])
        self.assertEqual(context["execution_state"]["execution_state"], "RUNNING")

    def test_human_required_with_active_lease_is_valid_and_preserves_writer(self):
        context = compile_context(durable(status="HUMAN_REQUIRED"))
        state = context["execution_state"]
        self.assertEqual(validate_state(state), [])
        self.assertTrue(context["human_boundary_active"])
        self.assertEqual(state["execution_state"], "HUMAN_REQUIRED")
        self.assertEqual(state["writer"], {"lease_id": LEASE_ID, "lease_state": "ACTIVE"})

    def test_human_required_without_compatible_lease_uses_none_and_null(self):
        empty = empty_lease_registry(REPO, MAIN, max_parallel_writers=1)
        context = compile_context(
            durable(status="HUMAN_REQUIRED"),
            leases=FakeLeaseStore(empty),
        )
        self.assertEqual(
            context["execution_state"]["writer"],
            {"lease_id": None, "lease_state": "NONE"},
        )
        self.assertEqual(validate_state(context["execution_state"]), [])

    def test_stale_main_is_freshly_observed_and_registry_remains_stale(self):
        context = compile_context(
            client=FakeClient(main=MAIN),
            leases=FakeLeaseStore(registry(main=OLD)),
        )
        self.assertEqual(context["main_sha"], MAIN)
        self.assertEqual(context["lease_registry"]["observed_main_sha"], OLD)

    def test_stale_head_is_not_taken_from_durable_state(self):
        context = compile_context(durable(subject_sha=OLD), client=FakeClient(head=HEAD))
        self.assertEqual(context["head_sha"], HEAD)
        self.assertEqual(context["execution_state"]["provider_observation"]["head_sha"], HEAD)

    def test_stale_lease_projects_none_null_but_registry_is_retained_for_reconcile(self):
        stale = registry(when=NOW - timedelta(hours=3), ttl=120)
        context = compile_context(leases=FakeLeaseStore(stale))
        self.assertEqual(context["execution_state"]["writer"], {"lease_id": None, "lease_state": "NONE"})
        self.assertEqual(len(context["lease_registry"]["leases"]), 1)

    def test_different_worker_is_not_substituted_as_expected_worker(self):
        context = compile_context(leases=FakeLeaseStore(registry(worker="other-worker")))
        self.assertEqual(context["current_worker_id"], "adwf-runtime:" + RUN_ID)
        self.assertEqual(context["execution_state"]["writer"]["lease_id"], LEASE_ID)

    def test_terminal_issue_is_fresh_provider_fact(self):
        context = compile_context(client=FakeClient(issue_state="closed"))
        self.assertTrue(context["work_terminal"])

    def test_external_status_is_read_only_for_explicit_ci_wait(self):
        checks = [{"status": "in_progress", "conclusion": None}]
        client = FakeClient(checks=checks)
        context = compile_context(durable(status="RETRY_WAIT", phase="CI"), client=client)
        self.assertEqual(context["external_status"], "in_progress")
        self.assertIn("checks", client.calls)

    def test_external_status_is_not_read_for_ordinary_running_state(self):
        client = FakeClient(checks=[{"status": "in_progress"}])
        context = compile_context(client=client)
        self.assertIsNone(context["external_status"])
        self.assertNotIn("checks", client.calls)

    def test_checkpoint_is_validated_and_bound_to_same_work(self):
        cp = checkpoint()
        context = compile_context(runtime=FakeRuntimeStore(cp))
        self.assertEqual(context["checkpoint"], cp)
        self.assertTrue(context["session_accelerator_present"])

    def test_checkpoint_identity_mismatch_fails_closed(self):
        cp = checkpoint(roadmap_id="OTHER-1")
        with self.assertRaisesRegex(ProviderContextError, "ROADMAP_MISMATCH"):
            compile_context(runtime=FakeRuntimeStore(cp))

    def test_provider_read_failure_is_bounded(self):
        with self.assertRaisesRegex(ProviderContextError, "PROVIDER_REPOSITORY_READ_FAILED") as caught:
            compile_context(client=FakeClient(fail="repo_info"))
        self.assertNotIn("secret", str(caught.exception))

    def test_production_path_requires_explicit_environment(self):
        with self.assertRaisesRegex(ProviderContextError, "GITHUB_REPOSITORY_OR_TOKEN_MISSING"):
            compile_provider_context(durable(), environ={})

    def test_no_active_runner_is_idle_without_compiler_or_node(self):
        calls = []
        result = run_active_supervisor(
            ROOT,
            journal=FakeJournal([]),
            context_compiler=lambda state: calls.append("compiler"),
            node_runner=lambda *args, **kwargs: calls.append("node"),
        )
        self.assertEqual(result["status"], "NO_SINGLE_ACTIVE_RUN")
        self.assertEqual(result["node_outcome"], "IDLE")
        self.assertEqual(calls, [])
        self.assert_no_authority(result)

    def test_multiple_active_runner_blocks_without_compiler_or_tick(self):
        calls = []
        result = run_active_supervisor(
            ROOT,
            journal=FakeJournal([durable(), durable(run_id="run-00000002")]),
            context_compiler=lambda state: calls.append("compiler"),
            node_runner=lambda *args, **kwargs: calls.append("node"),
        )
        self.assertEqual(result["resume_decision"], "BLOCK")
        self.assertEqual(result["status"], "AMBIGUOUS_ACTIVE_RUNS")
        self.assertEqual(calls, [])
        self.assert_no_authority(result)

    def test_broken_runner_blocks_before_provider_context(self):
        calls = []
        result = run_active_supervisor(
            ROOT,
            journal=FakeJournal([{"run_id": RUN_ID, "status": "BROKEN"}]),
            context_compiler=lambda state: calls.append("compiler"),
            node_runner=lambda *args, **kwargs: calls.append("node"),
        )
        self.assertEqual(result["status"], "BROKEN_ACTIVE_RUN")
        self.assertEqual(result["resume_decision"], "BLOCK")
        self.assertEqual(calls, [])

    def test_provider_context_failure_blocks_without_node(self):
        calls = []

        def failing(_state):
            raise ProviderContextError("PROVIDER_ISSUE_READ_FAILED")

        result = run_active_supervisor(
            ROOT,
            journal=FakeJournal([durable()]),
            context_compiler=failing,
            node_runner=lambda *args, **kwargs: calls.append("node"),
        )
        self.assertEqual(result["status"], "PROVIDER_CONTEXT_BLOCK")
        self.assertEqual(result["resume_decision"], "BLOCK")
        self.assertEqual(calls, [])

    def test_human_required_active_lease_cannot_tick(self):
        supervisor = FakeSupervisor()
        journal = FakeJournal([durable(status="HUMAN_REQUIRED")])
        context = compile_context(durable(status="HUMAN_REQUIRED"))
        result = run_active_supervisor(
            ROOT,
            journal=journal,
            context_compiler=lambda state: context,
            node_kwargs={"supervisor": supervisor},
        )
        self.assertEqual(result["resume_decision"], "BLOCK")
        self.assertEqual(result["node_outcome"], "BLOCK")
        self.assertEqual(supervisor.calls, [])

    def test_acquire_new_is_output_only_and_never_ticks(self):
        supervisor = FakeSupervisor()
        empty = empty_lease_registry(REPO, MAIN, max_parallel_writers=1)
        context = compile_context(leases=FakeLeaseStore(empty))
        context["execution_state"] = None
        context["checkpoint"] = None
        context["next_work_authorized"] = True
        journal = FakeJournal([durable()])
        result = run_active_supervisor(
            ROOT,
            journal=journal,
            context_compiler=lambda state: context,
            node_kwargs={"supervisor": supervisor},
        )
        self.assertEqual(result["resume_decision"], "ACQUIRE_NEW")
        self.assertEqual(supervisor.calls, [])
        self.assert_no_authority(result)

    def test_successful_resume_ticks_exactly_once(self):
        supervisor = FakeSupervisor()
        context = compile_context()
        journal = FakeJournal([durable()])
        result = run_active_supervisor(
            ROOT,
            max_steps=7,
            journal=journal,
            context_compiler=lambda state: context,
            node_kwargs={"supervisor": supervisor},
        )
        self.assertEqual(result["resume_decision"], "RESUME_EXISTING")
        self.assertEqual(supervisor.calls, [(RUN_ID, 7)])
        self.assert_no_authority(result)


if __name__ == "__main__":
    unittest.main()

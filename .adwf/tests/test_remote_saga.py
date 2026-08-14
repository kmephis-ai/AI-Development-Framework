import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.remote_saga import (
    RemoteResponse, SagaError, SagaJournal, compensate_transition,
    make_transition_plan, run_transition,
)


class FakeTransport:
    def __init__(self, issue, fail_mutation=None):
        self.issue = copy.deepcopy(issue)
        self.revision = 1
        self.fail_mutation = fail_mutation
        self.mutations = 0

    @property
    def etag(self):
        return f'"rev-{self.revision}"'

    def request(self, method, path, payload=None, *, etag=None):
        if method == "GET":
            return RemoteResponse(copy.deepcopy(self.issue), self.etag)
        if etag is not None and etag != self.etag:
            raise RuntimeError("PRECONDITION_FAILED")
        self.mutations += 1
        if self.fail_mutation == self.mutations:
            raise RuntimeError("INJECTED_FAILURE")
        if method == "POST" and path.endswith("/labels"):
            existing = {item["name"] for item in self.issue["labels"]}
            for label in payload["labels"]:
                if label not in existing:
                    self.issue["labels"].append({"name": label})
        elif method == "PATCH":
            self.issue["body"] = payload["body"]
        elif method == "DELETE":
            label = path.rsplit("/", 1)[-1]
            self.issue["labels"] = [item for item in self.issue["labels"] if item["name"] != label]
        else:
            raise AssertionError((method, path))
        self.revision += 1
        self.issue["updated_at"] = f"2026-08-13T12:00:{self.revision:02d}Z"
        return RemoteResponse(copy.deepcopy(self.issue), self.etag)


class RemoteSagaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".adwf-runtime").mkdir()
        self.issue = {
            "number": 7,
            "body": "marker State: IN_PROGRESS",
            "updated_at": "2026-08-13T12:00:00Z",
            "labels": [{"name": "roadmap:in-progress"}, {"name": "type:bug"}],
        }

    def tearDown(self):
        self.temp.cleanup()

    def plan(self):
        return make_transition_plan(
            repo="owner/repo",
            issue=self.issue,
            lease_id="lease-7",
            from_label="roadmap:in-progress",
            target_label="roadmap:review",
            from_state="IN_PROGRESS",
            to_state="REVIEW",
            desired_body="marker State: REVIEW",
            policy_hash="a" * 64,
        )

    def test_dry_run_is_journaled_without_remote_mutation(self):
        transport = FakeTransport(self.issue)
        result = run_transition(self.root, self.plan(), transport, apply=False)
        self.assertEqual(result["status"], "DRY_RUN")
        self.assertEqual(transport.mutations, 0)

    def test_transition_commits_and_duplicate_is_noop(self):
        transport = FakeTransport(self.issue)
        plan = self.plan()
        first = run_transition(self.root, plan, transport, apply=True)
        second = run_transition(self.root, plan, transport, apply=True)
        self.assertEqual(first["status"], "COMMITTED")
        self.assertEqual(second["status"], "COMMITTED")
        self.assertEqual(transport.mutations, 3)
        self.assertEqual({item["name"] for item in transport.issue["labels"] if item["name"].startswith("roadmap:")}, {"roadmap:review"})

    def test_partial_failure_can_resume_idempotently(self):
        transport = FakeTransport(self.issue, fail_mutation=2)
        plan = self.plan()
        with self.assertRaises(SagaError):
            run_transition(self.root, plan, transport, apply=True)
        stored = SagaJournal(self.root).load(plan["saga_id"])
        self.assertEqual(stored["status"], "RECOVERY_REQUIRED")
        transport.fail_mutation = None
        result = run_transition(self.root, plan, transport, apply=True)
        self.assertEqual(result["status"], "COMMITTED")

    def test_concurrent_human_body_edit_is_not_overwritten(self):
        transport = FakeTransport(self.issue)
        plan = self.plan()
        run_transition(self.root, plan, transport, apply=False)
        transport.issue["body"] = "human edit"
        transport.revision += 1
        result = run_transition(self.root, plan, transport, apply=True)
        self.assertEqual(result["status"], "RECOVERY_REQUIRED")
        self.assertEqual(transport.issue["body"], "human edit")

    def test_explicit_compensation_restores_original(self):
        transport = FakeTransport(self.issue, fail_mutation=3)
        plan = self.plan()
        with self.assertRaises(SagaError):
            run_transition(self.root, plan, transport, apply=True)
        transport.fail_mutation = None
        result = compensate_transition(self.root, plan["saga_id"], transport)
        self.assertEqual(result["status"], "COMPENSATED")
        self.assertEqual(transport.issue["body"], self.issue["body"])
        labels = {item["name"] for item in transport.issue["labels"]}
        self.assertIn("roadmap:in-progress", labels)
        self.assertNotIn("roadmap:review", labels)


if __name__ == "__main__":
    unittest.main()

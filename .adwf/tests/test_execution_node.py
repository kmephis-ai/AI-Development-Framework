from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.execution_node import run_execution_node_cycle


class FakeJournal:
    def __init__(self, active):
        self.active = active
        self.list_calls = 0

    def list_active(self):
        self.list_calls += 1
        return list(self.active)


class FakeSupervisor:
    def __init__(self, result=None):
        self.result = result or {"status": "WAITING"}
        self.calls = []

    def tick(self, run_id, *, max_steps):
        self.calls.append((run_id, max_steps))
        return self.result


class Clock:
    def __init__(self, *values):
        self.values = iter(values)

    def __call__(self):
        return next(self.values)


def decider(decision):
    calls = []

    def decide(**provider_context):
        calls.append(provider_context)
        return {
            "decision": decision,
            "reason": "TEST",
            "provider_write_authorized": False,
        }

    decide.calls = calls
    return decide


class ExecutionNodeTests(unittest.TestCase):
    def assert_no_authority(self, result):
        self.assertFalse(result["provider_write_authorized"])
        self.assertFalse(result["ci_authority"])
        self.assertFalse(result["merge_authority"])

    def test_idle_has_no_tick_or_resume_decision(self):
        supervisor = FakeSupervisor()
        result = run_execution_node_cycle(
            ROOT,
            {"fact": "unused"},
            journal=FakeJournal([]),
            supervisor=supervisor,
            resume_decider=decider("RESUME_EXISTING"),
            monotonic=Clock(10.0, 10.25),
        )
        self.assertEqual(result["node_outcome"], "IDLE")
        self.assertIsNone(result["run_id"])
        self.assertIsNone(result["resume_decision"])
        self.assertIsNone(result["supervisor_status"])
        self.assertEqual(supervisor.calls, [])
        self.assertEqual(result["cycle_duration_seconds"], 0.25)
        self.assert_no_authority(result)

    def test_multiple_active_runs_fail_closed_without_tick(self):
        supervisor = FakeSupervisor()
        resume = decider("RESUME_EXISTING")
        result = run_execution_node_cycle(
            ROOT,
            {},
            journal=FakeJournal([{"run_id": "run-00000001"}, {"run_id": "run-00000002"}]),
            supervisor=supervisor,
            resume_decider=resume,
            monotonic=Clock(2.0, 3.0),
        )
        self.assertEqual(result["node_outcome"], "AMBIGUOUS_ACTIVE_RUNS")
        self.assertEqual(result["resume_decision"], "BLOCK")
        self.assertIsNone(result["supervisor_status"])
        self.assertEqual(resume.calls, [])
        self.assertEqual(supervisor.calls, [])
        self.assert_no_authority(result)

    def test_each_non_resume_decision_performs_no_tick(self):
        for decision in ("YIELD", "RECONCILE", "BLOCK", "ACQUIRE_NEW"):
            with self.subTest(decision=decision):
                supervisor = FakeSupervisor()
                resume = decider(decision)
                context = {"decision_test": decision}
                result = run_execution_node_cycle(
                    ROOT,
                    context,
                    journal=FakeJournal([{"run_id": "run-00000001"}]),
                    supervisor=supervisor,
                    resume_decider=resume,
                    monotonic=Clock(4.0, 4.0),
                )
                self.assertEqual(resume.calls, [context])
                self.assertEqual(result["run_id"], "run-00000001")
                self.assertEqual(result["resume_decision"], decision)
                self.assertEqual(result["node_outcome"], decision)
                self.assertIsNone(result["supervisor_status"])
                self.assertEqual(supervisor.calls, [])
                self.assert_no_authority(result)

    def test_resume_existing_performs_exactly_one_bounded_tick(self):
        supervisor = FakeSupervisor({"status": "STEP_BUDGET_REACHED", "transitions": []})
        resume = decider("RESUME_EXISTING")
        context = {"provider": "fresh"}
        result = run_execution_node_cycle(
            ROOT,
            context,
            max_steps=7,
            journal=FakeJournal([{"run_id": "run-00000001"}]),
            supervisor=supervisor,
            resume_decider=resume,
            monotonic=Clock(20.0, 20.5),
        )
        self.assertEqual(resume.calls, [context])
        self.assertEqual(supervisor.calls, [("run-00000001", 7)])
        self.assertEqual(result["run_id"], "run-00000001")
        self.assertEqual(result["resume_decision"], "RESUME_EXISTING")
        self.assertEqual(result["node_outcome"], "RESUME_EXISTING")
        self.assertEqual(result["supervisor_status"], "STEP_BUDGET_REACHED")
        self.assertEqual(result["cycle_duration_seconds"], 0.5)
        self.assert_no_authority(result)

    def test_duration_is_deterministic_and_never_negative(self):
        result = run_execution_node_cycle(
            ROOT,
            {},
            journal=FakeJournal([]),
            monotonic=Clock(8.0, 7.0),
        )
        self.assertEqual(result["cycle_duration_seconds"], 0.0)
        self.assert_no_authority(result)

    def test_unknown_resume_decision_fails_closed_without_tick(self):
        supervisor = FakeSupervisor()
        result = run_execution_node_cycle(
            ROOT,
            {},
            journal=FakeJournal([{"run_id": "run-00000001"}]),
            supervisor=supervisor,
            resume_decider=decider("UNEXPECTED"),
            monotonic=Clock(1.0, 1.0),
        )
        self.assertEqual(result["resume_decision"], "BLOCK")
        self.assertEqual(result["node_outcome"], "INVALID_RESUME_DECISION")
        self.assertEqual(supervisor.calls, [])
        self.assert_no_authority(result)


if __name__ == "__main__":
    unittest.main()

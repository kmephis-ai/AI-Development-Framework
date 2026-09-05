from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.execution_node_host import bounded_cycle_evidence, read_private_token, run_host_loop


class ExecutionNodeHostTests(unittest.TestCase):
    def test_bounded_evidence_strips_unknown_and_forces_no_authority(self):
        result = bounded_cycle_evidence({
            "status": "WAITING", "run_id": "run-1", "reason": "x" * 500,
            "provider_write_authorized": True, "ci_authority": True, "merge_authority": True,
            "secret": "ghp-never-persist", "raw_output": "hidden",
        }, local_head_sha="a" * 40, timestamp="2026-09-05T00:00:00Z")
        self.assertFalse(result["provider_write_authorized"])
        self.assertFalse(result["ci_authority"])
        self.assertFalse(result["merge_authority"])
        self.assertNotIn("secret", result)
        self.assertNotIn("raw_output", result)
        self.assertEqual(len(result["reason"]), 160)

    @unittest.skipIf(os.name == "nt", "POSIX permission semantics")
    def test_private_token_requires_restrictive_mode(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "token"
            path.write_text("token-value\n", encoding="utf-8")
            path.chmod(0o644)
            with self.assertRaisesRegex(RuntimeError, "PERMISSIONS_UNSAFE"):
                read_private_token(path)
            path.chmod(0o600)
            self.assertEqual(read_private_token(path), "token-value")

    def test_loop_records_start_cycle_stop_and_no_overlap_contract(self):
        with tempfile.TemporaryDirectory() as td:
            evidence = Path(td) / "evidence.jsonl"
            lock_entries = []
            class Lock:
                def __enter__(self): lock_entries.append("enter"); return self
                def __exit__(self, *args): lock_entries.append("exit")
            cycles = run_host_loop(
                ROOT, cycle_runner=lambda _root: {"status": "NO_ROADMAP_WORK", "active": 0},
                evidence_path=evidence, lock_factory=Lock, interval_seconds=1,
                max_cycles=2, sleeper=lambda _seconds: None,
                now=iter(["t0", "t1", "t2", "t3"]).__next__,
                head_reader=lambda _root: "b" * 40,
            )
            self.assertEqual(cycles, 2)
            self.assertEqual(lock_entries, ["enter", "exit"])
            rows = [json.loads(line) for line in evidence.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([row.get("event") for row in rows], ["HOST_START", None, None, "HOST_STOP"])
            self.assertTrue(all(row["ci_authority"] is False for row in rows))
            self.assertTrue(all(row["merge_authority"] is False for row in rows))

    def test_cycle_exception_becomes_bounded_block(self):
        with tempfile.TemporaryDirectory() as td:
            evidence = Path(td) / "evidence.jsonl"
            def boom(_root): raise ValueError("secret details")
            run_host_loop(
                ROOT, cycle_runner=boom, evidence_path=evidence,
                lock_factory=lambda: nullcontext(), interval_seconds=1, max_cycles=1,
                now=iter(["t0", "t1", "t2"]).__next__, head_reader=lambda _root: None,
            )
            row = json.loads(evidence.read_text(encoding="utf-8").splitlines()[1])
            self.assertEqual(row["status"], "EXECUTION_NODE_HOST_BLOCK")
            self.assertEqual(row["reason"], "HOST_CYCLE_FAILED:ValueError")
            self.assertEqual(row["resume_decision"], "BLOCK")

    def test_linux_unit_and_installer_preserve_private_runtime_boundary(self):
        unit = (ROOT / ".adwf/runtime-host/linux/adwf-execution-node.service.in").read_text(encoding="utf-8")
        install = (ROOT / ".adwf/runtime-host/linux/install-user-service.sh").read_text(encoding="utf-8")
        self.assertIn("run_execution_node_host.py", unit)
        self.assertIn("ADWF_GITHUB_TOKEN_FILE=@TOKEN_FILE@", unit)
        self.assertNotIn("GITHUB_TOKEN=", unit)
        self.assertNotIn("self-hosted", unit.lower())
        self.assertNotIn("Listen", unit)
        self.assertIn("Python 3.12.10 required", install)
        self.assertIn("mode must be 600 or 400", install)
        self.assertNotIn("cat \"$TOKEN_FILE\"", install)
        self.assertIn("systemctl --user enable --now", install)


if __name__ == "__main__":
    unittest.main()

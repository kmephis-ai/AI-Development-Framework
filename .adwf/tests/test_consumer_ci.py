from __future__ import annotations
from pathlib import Path
import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.consumer_ci import ConsumerCIRouteError, classify_anchor, classify_current, resolve_route, delegate_native_phase, wait_for_native_phase
from tests.test_consumer_gates import ConsumerGateTests


class Client:
    def __init__(self, checks): self.checks = checks
    def check_runs(self, sha): return copy.deepcopy(self.checks)


class SequencedClient:
    def __init__(self, sequences):
        self.sequences = [copy.deepcopy(item) for item in sequences]
        self.calls = 0
    def check_runs(self, sha):
        index = min(self.calls, len(self.sequences) - 1)
        self.calls += 1
        return copy.deepcopy(self.sequences[index])


class ConsumerCIRouteTests(unittest.TestCase):
    def _git(self, root: Path, *args: str) -> str:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()

    def _commit(self, root: Path, message: str) -> str:
        self._git(root, "add", "-A")
        self._git(root, "-c", "user.name=ADWF Test", "-c", "user.email=adwf@example.invalid", "commit", "-m", message)
        return self._git(root, "rev-parse", "HEAD")

    def test_self_host_anchor_cannot_switch_to_consumer_markers(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self._git(root, "init", "-q")
            (root / ".adwf").mkdir()
            (root / ".adwf/config.json").write_text("{}\n", encoding="utf-8")
            (root / "MANIFEST.json").write_text("{}\n", encoding="utf-8")
            (root / "SHA256SUMS.txt").write_text("x\n", encoding="utf-8")
            base = self._commit(root, "base")
            self.assertEqual(classify_anchor(root, base), "SELF_HOST_CANONICAL")
            c = root / ".adwf-consumer"; c.mkdir()
            for name in ("profile.json", "installation.json", "operations.json", "gates.json"):
                (c / name).write_text("{}\n", encoding="utf-8")
            with self.assertRaises(ConsumerCIRouteError):
                classify_current(root, root, expected_repository="example/consumer")

    def test_installed_consumer_routes_native_and_managed_drift_blocks(self):
        with tempfile.TemporaryDirectory() as t:
            _, consumer, _ = ConsumerGateTests()._ready(Path(t))
            repository = "example/consumer"
            mode = classify_current(consumer, consumer, expected_repository=repository)
            self.assertEqual(mode["mode"], "CONSUMER_NATIVE")
            self.assertGreater(mode["verified_managed_files"], 0)
            record = json.loads((consumer / ".adwf-consumer/installation.json").read_text(encoding="utf-8"))
            managed = next(x for x in record["managed_surface"]["entries"] if x["managed_by_adwf"] is True)
            path = consumer / managed["path"]
            path.write_bytes(path.read_bytes() + b"\nDRIFT")
            with self.assertRaises(ConsumerCIRouteError):
                classify_current(consumer, consumer, expected_repository=repository)

    def test_unmanaged_anchor_may_transition_only_to_valid_consumer(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t) / "repo"; root.mkdir()
            self._git(root, "init", "-q")
            (root / "README.md").write_text("consumer\n", encoding="utf-8")
            base = self._commit(root, "preinstall")
            self.assertEqual(classify_anchor(root, base), "UNMANAGED_PREINSTALL")
            with self.assertRaises(ConsumerCIRouteError):
                resolve_route(root, root, phase="pr", subject_sha="a"*40, anchor_sha=base, expected_repository="example/consumer")

    def test_generated_workflows_route_self_host_or_native_without_write_authority(self):
        pr = (ROOT / ".github/workflows/adwf-pr.yml").read_text(encoding="utf-8")
        main = (ROOT / ".github/workflows/adwf-main.yml").read_text(encoding="utf-8")
        for text in (pr, main):
            self.assertIn("checks: read", text)
            self.assertIn("Resolve ADWF CI route", text)
            self.assertIn("steps.ci-route.outputs.mode == 'SELF_HOST_CANONICAL'", text)
            self.assertIn("steps.ci-route.outputs.mode == 'CONSUMER_NATIVE'", text)
            self.assertNotIn("checks: write", text)
        self.assertIn("consumer_ci.py delegate --phase pr", pr)
        self.assertIn("consumer_ci.py delegate --phase main", main)
        self.assertIn("Framework contract suite", main)
        control = (ROOT / ".github/workflows/adwf-control.yml").read_text(encoding="utf-8")
        self.assertIn("control-route:", control)
        self.assertIn("Resolve trusted control mode", control)
        self.assertIn("consumer_ci.py classify-current", control)
        self.assertIn("if: needs.control-route.outputs.mode == 'SELF_HOST_CANONICAL'", control)
        self.assertIn("consumer-observer:", control)
        self.assertIn("if: needs.control-route.outputs.mode == 'CONSUMER_NATIVE'", control)
        observer = control.split("  consumer-observer:", 1)[1]
        self.assertIn("contents: read", observer)
        self.assertNotIn("contents: write", observer)
        self.assertNotIn("issues: write", observer)
        self.assertNotIn("pull-requests: write", observer)

    def test_native_delegation_exact_success_and_failure(self):
        with tempfile.TemporaryDirectory() as t:
            _, consumer, _ = ConsumerGateTests()._ready(Path(t)); sha = "d" * 40
            ok = {"id": 1, "name": "PR Validation", "head_sha": sha, "status": "completed", "conclusion": "success", "app": {"slug": "github-actions", "id": 15368}}
            result = delegate_native_phase(consumer, consumer, Client([ok]), phase="pr", subject_sha=sha)
            self.assertEqual(result["status"], "VERIFIED")
            with self.assertRaises(ConsumerCIRouteError):
                delegate_native_phase(consumer, consumer, Client([{**ok, "conclusion": "failure"}]), phase="pr", subject_sha=sha)

    def test_bounded_wait_allows_pending_native_gate_to_finish(self):
        with tempfile.TemporaryDirectory() as t:
            _, consumer, _ = ConsumerGateTests()._ready(Path(t)); sha = "e" * 40
            pending = {"id": 1, "name": "PR Validation", "head_sha": sha, "status": "in_progress", "conclusion": None, "app": {"slug": "github-actions", "id": 15368}}
            ok = {**pending, "status": "completed", "conclusion": "success"}
            sleeps = []
            result = wait_for_native_phase(consumer, consumer, SequencedClient([[pending], [pending], [ok]]), phase="pr", subject_sha=sha, attempts=3, interval_seconds=10, sleep=sleeps.append)
            self.assertEqual(result["status"], "VERIFIED")
            self.assertEqual(sleeps, [10, 10])

    def test_bounded_wait_timeout_remains_fail_closed(self):
        with tempfile.TemporaryDirectory() as t:
            _, consumer, _ = ConsumerGateTests()._ready(Path(t)); sha = "f" * 40
            pending = {"id": 1, "name": "PR Validation", "head_sha": sha, "status": "in_progress", "conclusion": None, "app": {"slug": "github-actions", "id": 15368}}
            sleeps = []
            with self.assertRaisesRegex(ConsumerCIRouteError, "NATIVE_GATE_NOT_VERIFIED"):
                wait_for_native_phase(consumer, consumer, SequencedClient([[pending]]), phase="pr", subject_sha=sha, attempts=3, interval_seconds=10, sleep=sleeps.append)
            self.assertEqual(sleeps, [10, 10])


if __name__ == "__main__": unittest.main()

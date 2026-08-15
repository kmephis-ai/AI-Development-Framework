import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from scripts import publish_trusted_gate as GATE


class RecordingClient:
    repo = "owner/repo"

    def __init__(self, fail_on_call=None):
        self.calls = []
        self.fail_on_call = fail_on_call

    def post(self, path, payload):
        self.calls.append((path, payload))
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("provider write failed")
        return {"ok": True}


class TrustedGatePublicationTests(unittest.TestCase):
    def test_pass_is_mirrored_to_check_run_and_commit_status(self):
        client = RecordingClient()
        sha = "a" * 40
        GATE._publish(client, "adwf/trusted-gate", sha, True, "Trusted", "PASS: exact trusted result")

        self.assertEqual(len(client.calls), 2)
        check_path, check = client.calls[0]
        status_path, status = client.calls[1]
        self.assertEqual(check_path, "/repos/owner/repo/check-runs")
        self.assertEqual(check["name"], "adwf/trusted-gate")
        self.assertEqual(check["head_sha"], sha)
        self.assertEqual(check["status"], "completed")
        self.assertEqual(check["conclusion"], "success")
        self.assertEqual(status_path, f"/repos/owner/repo/statuses/{sha}")
        self.assertEqual(status["context"], "adwf/trusted-gate")
        self.assertEqual(status["state"], "success")
        self.assertEqual(status["description"], "PASS: exact trusted result")

    def test_block_is_mirrored_as_failure_in_both_transports(self):
        client = RecordingClient()
        sha = "b" * 40
        GATE._publish(client, "adwf/governance-gate", sha, False, "Governance", "BLOCK: human attestation required")

        self.assertEqual(client.calls[0][1]["conclusion"], "failure")
        self.assertEqual(client.calls[1][1]["state"], "failure")
        self.assertEqual(client.calls[1][1]["context"], "adwf/governance-gate")

    def test_check_write_failure_never_attempts_positive_status(self):
        client = RecordingClient(fail_on_call=1)
        with self.assertRaises(RuntimeError):
            GATE._publish(client, "adwf/trusted-gate", "c" * 40, True, "Trusted", "PASS")
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(client.calls[0][0].endswith("/check-runs"))

    def test_status_write_failure_propagates_fail_closed(self):
        client = RecordingClient(fail_on_call=2)
        with self.assertRaises(RuntimeError):
            GATE._publish(client, "adwf/trusted-gate", "d" * 40, True, "Trusted", "PASS")
        self.assertEqual(len(client.calls), 2)
        self.assertTrue(client.calls[1][0].endswith("/statuses/" + "d" * 40))

    def test_status_description_is_provider_bounded(self):
        client = RecordingClient()
        GATE._publish(client, "adwf/trusted-gate", "e" * 40, True, "Trusted", "x" * 500)
        self.assertEqual(len(client.calls[1][1]["description"]), 140)

    def test_trusted_controller_has_status_write_but_pr_lane_does_not(self):
        control = (ROOT / ".github/workflows/adwf-control.yml").read_text(encoding="utf-8")
        pr = (ROOT / ".github/workflows/adwf-pr.yml").read_text(encoding="utf-8")
        self.assertIn("      statuses: write\n", control)
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", control)
        self.assertIn("persist-credentials: false", control)
        self.assertNotIn("statuses: write", pr)


if __name__ == "__main__":
    unittest.main()

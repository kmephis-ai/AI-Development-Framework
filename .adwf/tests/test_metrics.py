import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.metrics import summarize_ci


class MetricsTests(unittest.TestCase):
    def test_percentiles_and_flake_rate(self):
        payload = {"observed_at": "2026-08-13T10:00:00Z", "runs": [
            {"id": "1", "queued_at": "2026-08-13T09:00:00Z", "started_at": "2026-08-13T09:00:10Z", "completed_at": "2026-08-13T09:01:10Z", "conclusion": "PASS", "first_failure_at": None, "flaky": False},
            {"id": "2", "queued_at": "2026-08-13T09:10:00Z", "started_at": "2026-08-13T09:10:20Z", "completed_at": "2026-08-13T09:12:20Z", "conclusion": "FAIL", "first_failure_at": "2026-08-13T09:10:50Z", "flaky": True},
        ]}
        result = summarize_ci(payload, now=datetime(2026, 8, 13, 10, 30, tzinfo=timezone.utc))
        self.assertEqual(result["status"], "VERIFIED")
        self.assertEqual(result["p50_duration_seconds"], 60)
        self.assertEqual(result["p95_duration_seconds"], 120)
        self.assertEqual(result["p95_time_to_first_failure_seconds"], 30)
        self.assertEqual(result["flake_rate"], 0.5)

    def test_stale_or_invalid_samples_are_not_verified(self):
        stale = summarize_ci({"observed_at": "2026-08-10T00:00:00Z", "runs": []}, now=datetime(2026, 8, 13, 10, tzinfo=timezone.utc))
        self.assertEqual(stale["status"], "NOT_VERIFIED")
        self.assertIn("RUNS_NOT_VERIFIED", stale["errors"])


if __name__ == "__main__":
    unittest.main()

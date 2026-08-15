import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.incidents import (
    incident_store_summary,
    normalize_incident,
    read_incident_events,
    record_incident,
    stable_fingerprint,
)


class IncidentKnowledgeTests(unittest.TestCase):
    now = datetime(2026, 8, 13, 12, tzinfo=timezone.utc)

    def raw(self, *, symptom="Assertion failed"):
        return {
            "severity": "SEV2",
            "status": "NORMALIZED",
            "owner_summary_ru": "Регистрация остановлена до публикации безопасного результата.",
            "symptom_original": symptom,
            "source": {"provider": "local", "authorization": "Bearer very-secret-value"},
            "subject": {"component": "registration", "owner": "owner@example.org"},
            "failure": {
                "failure_class": "TEST_ASSERTION",
                "stage": "test",
                "tool": "unittest",
                "exit_code": 1,
                "stable_frames": ["tests/test_signup.py:42"],
                "symptom": symptom,
                "api_key": "ghp_abcdefghijklmnopqrstuvwxyz",
            },
            "classification": {"type": "TEST_ASSERTION", "confidence": "VERIFIED", "evidence": ["exit=1"]},
            "cost_usage": {"monetary_cost": 999, "ai_api_used": True},
        }

    def test_store_is_sanitized_append_only_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "incidents.jsonl"
            first_raw = self.raw(symptom=(
                "Assertion failed at 2026-08-13T12:00:00Z commit "
                "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa in /tmp/run-123/output"
            ))
            first = record_incident(store, first_raw, now=self.now)
            first_line = store.read_text(encoding="utf-8").splitlines()[0]
            second_raw = self.raw(symptom=(
                "Assertion failed at 2026-08-13T12:05:00Z commit "
                "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb in /tmp/run-999/output"
            ))
            second = record_incident(store, second_raw, now=self.now)
            lines = store.read_text(encoding="utf-8").splitlines()

            self.assertEqual(first["result"], "RECORDED")
            self.assertEqual(second["result"], "DEDUPLICATED")
            self.assertEqual(first["fingerprint_hash"], second["fingerprint_hash"])
            self.assertEqual(lines[0], first_line)
            self.assertEqual(len(lines), 2)
            serialized = "\n".join(lines)
            self.assertNotIn("very-secret-value", serialized)
            self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz", serialized)
            self.assertNotIn("owner@example.org", serialized)
            summary = incident_store_summary(store)
            self.assertEqual(summary["incident_count"], 1)
            self.assertEqual(summary["repeated_count"], 1)

    def test_normalization_forces_free_non_ai_mandatory_path(self):
        record = normalize_incident(self.raw(), now=self.now)
        self.assertEqual(record["cost_usage"], {"monetary_cost": 0, "ai_api_used": False})
        self.assertEqual(record["source"]["authorization"], "[REDACTED]")

    def test_fingerprint_ignores_volatile_sha_time_temp_and_line(self):
        first = stable_fingerprint({
            "failure_class": "COMPILE_ERROR", "stage": "build", "tool": "python",
            "exit_code": 1, "frames": ["src/app.py:42"],
            "message": "failed 2026-08-13T12:00:00Z aaaaaaa /tmp/a/output",
        })
        second = stable_fingerprint({
            "failure_class": "COMPILE_ERROR", "stage": "build", "tool": "python",
            "exit_code": 1, "frames": ["src/app.py:99"],
            "message": "failed 2026-08-14T01:02:03Z bbbbbbb /tmp/b/output",
        })
        self.assertEqual(first["hash"], second["hash"])

    def test_hash_chain_detects_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "incidents.jsonl"
            record_incident(store, self.raw(), now=self.now)
            tampered = store.read_text(encoding="utf-8").replace("Регистрация", "Подмена", 1)
            store.write_text(tampered, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "INCIDENT_STORE_HASH_INVALID"):
                read_incident_events(store)

    def test_external_anchor_detects_valid_tail_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "incidents.jsonl"
            record_incident(store, self.raw(), now=self.now)
            record_incident(store, self.raw(), now=self.now)
            events = read_incident_events(store)
            expected_tail = events[-1]["event_hash"]
            store.write_text(store.read_text(encoding="utf-8").splitlines()[0] + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "TAIL_MISMATCH"):
                read_incident_events(store, expected_tail_hash=expected_tail, expected_sequence=2)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink is unavailable")
    def test_store_refuses_symlink_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.jsonl"
            target.write_text("", encoding="utf-8")
            link = Path(tmp) / "store.jsonl"
            try:
                link.symlink_to(target)
            except OSError as exc:
                if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                    self.skipTest("Windows symlink privilege is unavailable")
                raise
            with self.assertRaisesRegex(ValueError, "SYMLINK_FORBIDDEN"):
                record_incident(link, self.raw(), now=self.now)

    def test_incident_id_collision_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "incidents.jsonl"
            first = self.raw(symptom="First deterministic failure")
            first["incident_id"] = "INC-20260813-0123456789ab"
            record_incident(store, first, now=self.now)
            second = self.raw(symptom="Entirely different deterministic failure")
            second["incident_id"] = "INC-20260813-0123456789ab"
            with self.assertRaisesRegex(ValueError, "INCIDENT_ID_COLLISION"):
                record_incident(store, second, now=self.now)


if __name__ == "__main__":
    unittest.main()

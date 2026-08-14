import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))

from lib.contracts import validate
from lib.incidents import read_incident_events, record_incident


class KnowledgeContractTests(unittest.TestCase):
    def test_examples_match_strict_schemas(self):
        pairs = [
            ("healing-config.json", "healing-config.schema.json"),
            ("examples-incident.json", "incident.schema.json"),
            ("examples-repair-recipes.json", "repair-recipe.schema.json"),
            ("examples-owner-experience.json", "owner-experience.schema.json"),
        ]
        for data_name, schema_name in pairs:
            with self.subTest(data=data_name):
                data = json.loads((ROOT / ".adwf" / data_name).read_text(encoding="utf-8"))
                schema = json.loads((ROOT / ".adwf/schemas" / schema_name).read_text(encoding="utf-8"))
                self.assertEqual(validate(data, schema), [])

    def test_append_only_events_match_event_schema(self):
        raw = {
            "severity": "SEV3", "status": "NORMALIZED",
            "owner_summary_ru": "Проверка временно остановлена до безопасного решения.",
            "symptom_original": "Runner interrupted",
            "failure": {"failure_class": "RUNNER_INTERRUPTED", "stage": "ci", "tool": "runner", "exit_code": 1},
            "classification": {"type": "RUNNER_INTERRUPTED", "confidence": "VERIFIED", "evidence": []},
        }
        schema = json.loads((ROOT / ".adwf/schemas/incident-event.schema.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "incidents.jsonl"
            record_incident(store, raw, now=datetime(2026, 8, 13, 12, tzinfo=timezone.utc))
            record_incident(store, raw, now=datetime(2026, 8, 13, 12, tzinfo=timezone.utc))
            for event in read_incident_events(store):
                self.assertEqual(validate(event, schema), [])


if __name__ == "__main__":
    unittest.main()

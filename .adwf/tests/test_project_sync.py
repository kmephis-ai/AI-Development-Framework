import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("project_item_sync", ROOT / ".adwf/scripts/project_item_sync.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ProjectSyncTests(unittest.TestCase):
    def test_single_select_uses_ids_not_unsupported_name_flags(self):
        field = {"id": "FIELD", "name": "Состояние ADWF", "dataType": "SINGLE_SELECT", "options": [{"id": "OPT", "name": "READY"}]}
        command = MODULE.edit_command("PROJECT", "ITEM", field, "READY")
        self.assertEqual(command[-2:], ["--single-select-option-id", "OPT"])
        self.assertNotIn("--field", command)
        self.assertNotIn("--value", command)

    def test_duplicate_project_item_blocks(self):
        items = [{"id": "1", "content": {"url": "https://github.com/o/r/issues/1"}}, {"id": "2", "content": {"url": "https://github.com/o/r/issues/1"}}]
        with self.assertRaisesRegex(ValueError, "PROJECT_ITEM_DUPLICATE"):
            MODULE.item_id_for_url(items, "https://github.com/o/r/issues/1")

    def test_missing_snapshot_timestamp_is_not_verified(self):
        self.assertEqual(MODULE.snapshot_status({"snapshot": {"valid_until": None}, "main": {"head": None}}), "NOT_VERIFIED")

    def test_graphql_readback_extracts_all_supported_field_types(self):
        payload = {"data": {"node": {"project": {"id": "P"}, "fieldValues": {"nodes": [
            {"__typename": "ProjectV2ItemFieldTextValue", "text": "RM-7", "field": {"name": "Roadmap ID"}},
            {"__typename": "ProjectV2ItemFieldSingleSelectValue", "name": "READY", "field": {"name": "Состояние ADWF"}},
            {"__typename": "ProjectV2ItemFieldDateValue", "date": "2026-08-13", "field": {"name": "Snapshot до"}},
            {"__typename": "ProjectV2ItemFieldNumberValue", "number": 12.5, "field": {"name": "CI p95 sec"}},
        ]}}}}
        project, values = MODULE.parsed_readback(payload)
        self.assertEqual(project, "P")
        self.assertEqual(values["Roadmap ID"], "RM-7")
        self.assertEqual(values["Состояние ADWF"], "READY")
        self.assertEqual(values["Snapshot до"], "2026-08-13")
        self.assertEqual(values["CI p95 sec"], 12.5)

    def test_active_projection_uses_exact_roadmap_item(self):
        state = {"active": {"roadmap_id": "RM-7"}, "work_items": [
            {"roadmap_id": "RM-7", "priority": "P1"}, {"roadmap_id": "RM-8", "priority": "P2"},
        ]}
        self.assertEqual(MODULE.active_work_item(state)["priority"], "P1")
        with self.assertRaisesRegex(ValueError, "ACTIVE_WORK_ITEM_NOT_UNIQUE"):
            MODULE.active_work_item({"active": {"roadmap_id": "RM-7"}, "work_items": []})

    def test_projection_evidence_is_written_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "state.json"
            path.write_text(json.dumps({"project_projection": {}}), encoding="utf-8")
            MODULE.record_projection(path, "PASS", project_id="P", item_id="I",
                                     now=datetime(2026, 8, 13, 12, tzinfo=timezone.utc))
            value = json.loads(path.read_text(encoding="utf-8"))["project_projection"]
            self.assertEqual(value, {"status": "PASS", "observed_at": "2026-08-13T12:00:00Z", "project_id": "P", "item_id": "I"})


if __name__ == "__main__":
    unittest.main()

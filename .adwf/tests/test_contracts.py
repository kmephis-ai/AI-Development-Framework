import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.contracts import validate


class ContractTests(unittest.TestCase):
    def test_repository_config_and_state_match_strict_schemas(self):
        for data_name, schema_name in [("config.json", "config.schema.json"), ("project-state.json", "project-state.schema.json"), ("providers.json", "providers.schema.json")]:
            with self.subTest(data=data_name):
                data = json.loads((ROOT / ".adwf" / data_name).read_text(encoding="utf-8"))
                schema = json.loads((ROOT / ".adwf/schemas" / schema_name).read_text(encoding="utf-8"))
                self.assertEqual(validate(data, schema), [])

    def test_additional_property_is_rejected(self):
        findings = validate({"known": 1, "surprise": 2}, {"type": "object", "additionalProperties": False, "properties": {"known": {"type": "integer"}}})
        self.assertEqual(findings[0].code, "additionalProperties")

    def test_unknown_schema_keyword_fails_closed(self):
        findings = validate("x", {"type": "string", "magic": True})
        self.assertEqual(findings[0].code, "unsupported_schema_keyword")

    def test_short_main_sha_is_rejected_by_state_schema(self):
        state = json.loads((ROOT / ".adwf/project-state.json").read_text(encoding="utf-8"))
        schema = json.loads((ROOT / ".adwf/schemas/project-state.schema.json").read_text(encoding="utf-8"))
        state["main"]["head"] = "abc"
        self.assertTrue(any(item.path == "$.main.head" and item.code == "pattern" for item in validate(state, schema)))

    def test_malformed_active_lease_is_rejected_by_state_schema(self):
        state = json.loads((ROOT / ".adwf/project-state.json").read_text(encoding="utf-8"))
        schema = json.loads((ROOT / ".adwf/schemas/project-state.schema.json").read_text(encoding="utf-8"))
        state["active"]["lease_id"] = "not-a-uuid"
        self.assertTrue(any(item.path == "$.active.lease_id" and item.code == "pattern" for item in validate(state, schema)))


if __name__ == "__main__":
    unittest.main()

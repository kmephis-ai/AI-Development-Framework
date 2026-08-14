import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("sync_labels", ROOT / ".adwf/scripts/sync_labels.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LabelSyncTests(unittest.TestCase):
    def test_only_missing_or_drifted_canonical_labels_are_changed(self):
        desired = [{"name": "roadmap:ready", "color": "0E8A16", "description": "Ready"},
                   {"name": "roadmap:review", "color": "8250DF", "description": "Review"}]
        existing = [{"name": "roadmap:ready", "color": "0e8a16", "description": "Ready"},
                    {"name": "roadmap:review", "color": "ffffff", "description": "Old"},
                    {"name": "user:custom", "color": "000000", "description": "Keep"}]
        commands = MODULE.commands_for(desired, existing)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][1:4], ["label", "edit", "roadmap:review"])
        self.assertNotIn("user:custom", str(commands))


if __name__ == "__main__":
    unittest.main()

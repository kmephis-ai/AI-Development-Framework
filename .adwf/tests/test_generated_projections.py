import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.docs_freshness import check_docs


class GeneratedProjectionTests(unittest.TestCase):
    def test_labels_have_one_canonical_source(self):
        canonical = json.loads((ROOT / ".adwf/labels.json").read_text(encoding="utf-8"))
        projection = json.loads((ROOT / "labels.json").read_text(encoding="utf-8"))
        self.assertEqual(projection, [item["name"] for item in canonical])
        self.assertEqual(len(projection), len(set(projection)))

    def test_documentation_source_digests_are_fresh(self):
        self.assertEqual(check_docs(ROOT), [])


if __name__ == "__main__":
    unittest.main()

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
SPEC = importlib.util.spec_from_file_location("gitlab_reconcile", ROOT / ".adwf/scripts/gitlab_reconcile.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GitLabAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads((ROOT / ".adwf/providers.json").read_text(encoding="utf-8"))

    def test_unknown_gitlab_domain_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "GITLAB_API_DOMAIN_NOT_APPROVED"):
            MODULE.approved_api_base("https://unknown.example/api/v4", self.registry)

    def test_gitlab_objects_normalize_to_provider_neutral_shape(self):
        issue = MODULE.normalize_issue({"iid": 7, "description": "Roadmap-ID: RM-7", "labels": ["roadmap:ready"], "updated_at": "2026-08-13T09:00:00Z"})
        merge = MODULE.normalize_merge_request({"iid": 8, "description": "body", "state": "opened", "source_branch": "work", "sha": "a" * 40})
        pipeline = MODULE.normalize_pipeline({"id": 9, "status": "success", "created_at": "2026-08-13T09:00:00Z", "started_at": "2026-08-13T09:01:00Z", "updated_at": "2026-08-13T09:02:00Z"})
        self.assertEqual(issue["number"], 7)
        self.assertEqual(merge["head"]["sha"], "a" * 40)
        self.assertEqual(pipeline["conclusion"], "success")
        self.assertEqual(pipeline["run_started_at"], "2026-08-13T09:01:00Z")


if __name__ == "__main__":
    unittest.main()

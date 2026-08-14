import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.provider_events import assert_contract, normalize_github, normalize_gitlab


class ProviderParityTests(unittest.TestCase):
    def test_github_and_gitlab_normalize_to_same_contract(self):
        github = normalize_github({"pull_request": {"number": 7, "head": {"sha": "abc"}}, "repository": {"default_branch": "main"}})
        gitlab = normalize_gitlab({"object_attributes": {"iid": 7, "last_commit": {"id": "abc"}}, "project": {"default_branch": "main"}})
        self.assertEqual(assert_contract(github), [])
        self.assertEqual(assert_contract(gitlab), [])
        for field in ("event_kind", "change_number", "head_sha", "default_branch"):
            self.assertEqual(github[field], gitlab[field])

    def test_trusted_control_is_explicit(self):
        gitlab = normalize_gitlab({"object_attributes": {"sha": "abc"}, "project": {"default_branch": "main"}})
        self.assertFalse(gitlab["trusted_control"])


if __name__ == "__main__":
    unittest.main()


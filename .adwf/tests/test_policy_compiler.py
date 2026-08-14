import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.policy_compiler import check_compiled_policy, compile_policy


class PolicyCompilerTests(unittest.TestCase):
    def test_repository_policy_compiles_without_contradictions(self):
        compiled, errors = compile_policy(ROOT)
        self.assertEqual(errors, [])
        self.assertRegex(compiled["policy_hash"], r"^[0-9a-f]{64}$")
        self.assertEqual(compiled["canonical_provider"], "github")

    def test_stored_policy_is_exactly_reproducible(self):
        self.assertEqual(check_compiled_policy(ROOT), [])

    def test_contradiction_blocks_compilation(self):
        original = json.loads((ROOT / ".adwf/config.json").read_text(encoding="utf-8"))
        broken = copy.deepcopy(original)
        broken["cost"]["monetary_budget"] = 1
        real_load = json.loads

        def fake_load(path):
            if Path(path).name == "config.json":
                return broken
            return real_load(Path(path).read_text(encoding="utf-8"))

        with mock.patch("lib.policy_compiler._load", side_effect=fake_load):
            _, errors = compile_policy(ROOT)
        self.assertIn("NON_ZERO_BUDGET", errors)

    def test_default_ci_capability_must_match_canonical_provider(self):
        original = json.loads((ROOT / ".adwf/config.json").read_text(encoding="utf-8"))
        broken = copy.deepcopy(original)
        broken["provider"]["mode"] = "local"
        real_load = json.loads

        def fake_load(path):
            if Path(path).name == "config.json":
                return broken
            return real_load(Path(path).read_text(encoding="utf-8"))

        with mock.patch("lib.policy_compiler._load", side_effect=fake_load):
            _, errors = compile_policy(ROOT)
        self.assertIn("DEFAULT_CI_CAPABILITY_PROVIDER_MISMATCH", errors)


if __name__ == "__main__":
    unittest.main()

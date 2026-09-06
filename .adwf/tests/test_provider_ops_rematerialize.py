import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib import provider_ops_gateway as GATE

MAIN = "a" * 40
TREE_BASE = "d" * 40
SOURCE = [".adwf/lib/example.py"]

class Client:
    repo = "owner/repo"


def request_body(**overrides):
    values = dict(
        request_id="gov034-test-a1", issue_id=291, roadmap_id="GOV-034", expected_main_sha=MAIN,
        pr_number=99, base_sha=MAIN, head_sha="b" * 40, branch="adwf/gov-034-test", worker_id="adwf-runtime:gov-034-test",
        lease_id="11111111-1111-4111-8111-111111111111", lease_registry_revision=67, source_paths=list(SOURCE),
    )
    values.update(overrides)
    return GATE.build_provider_ops_comment(**values)

class RematerializationLineageTests(unittest.TestCase):
    MAT = "4" * 40
    SOURCE1 = "5" * 40
    REPAIR = "6" * 40
    TREE_SOURCE1 = "7" * 40
    TREE_MAT = "8" * 40
    TREE_REPAIR = "9" * 40

    def request(self):
        req = GATE.parse_provider_ops_comment(request_body())
        req["head_sha"] = self.REPAIR
        return req

    def signed_materializer(self, *, verified=True, message=None, parents=None):
        parent = self.SOURCE1
        return {
            "sha": self.MAT,
            "tree_sha": self.TREE_MAT,
            "parents": [parent] if parents is None else parents,
            "message": message or (
                "GOV-034: materialize deterministic projections\n\n"
                "ADWF-Provider-Ops: MATERIALIZE_PROJECTIONS\n"
                "ADWF-Provider-Ops-Request: gov034-materialize-a1\n"
                f"ADWF-Provider-Ops-Digest: {'a' * 64}\n"
                f"ADWF-Provider-Ops-Parent: {parent}"
            ),
            "verification": {"verified": verified, "reason": "valid" if verified else "unsigned"},
            "author": {
                "name": "github-actions[bot]",
                "email": "41898282+github-actions[bot]@users.noreply.github.com",
            },
            "committer": {"name": "GitHub", "email": "noreply@github.com"},
        }

    def fixture(self):
        src0 = {"sha": "1" * 40, "mode": "100644", "size": 1}
        src1 = {"sha": "2" * 40, "mode": "100644", "size": 2}
        src2 = {"sha": "3" * 40, "mode": "100644", "size": 3}
        base = {SOURCE[0]: src0}
        for index, path in enumerate(GATE.PROJECTION_PATHS):
            base[path] = {"sha": str(index + 4) * 40, "mode": "100644", "size": 1}
        source1 = dict(base); source1[SOURCE[0]] = src1
        materialized = dict(source1)
        materialized["MANIFEST.json"] = {"sha": "8" * 40, "mode": "100644", "size": 2}
        repair = dict(materialized); repair[SOURCE[0]] = src2
        nodes = {
            MAIN: {"sha": MAIN, "tree_sha": TREE_BASE, "parents": [], "message": "base"},
            self.SOURCE1: {"sha": self.SOURCE1, "tree_sha": self.TREE_SOURCE1, "parents": [MAIN], "message": "source"},
            self.MAT: self.signed_materializer(),
            self.REPAIR: {"sha": self.REPAIR, "tree_sha": self.TREE_REPAIR, "parents": [self.MAT], "message": "repair"},
        }
        trees = {TREE_BASE: base, self.TREE_SOURCE1: source1, self.TREE_MAT: materialized, self.TREE_REPAIR: repair}
        return nodes, trees

    def run_helper(self, *, mutate_nodes=None, mutate_trees=None):
        nodes, trees = self.fixture()
        if mutate_nodes:
            mutate_nodes(nodes)
        if mutate_trees:
            mutate_trees(trees)
        req = self.request()
        with mock.patch.object(GATE, "_commit_node", side_effect=lambda c, sha, cache: nodes[sha]), \
             mock.patch.object(GATE, "_tree_files", side_effect=lambda c, sha: trees[sha]):
            return GATE._source_effect_with_verified_materialization_lineage(
                Client(), req, trees[TREE_BASE], trees[self.TREE_REPAIR], {},
            )

    def test_verified_materializer_allows_source_repair_recovery(self):
        effect, ancestor = self.run_helper()
        self.assertEqual([row["path"] for row in effect], SOURCE)
        self.assertEqual(ancestor, self.MAT)

    def test_forged_unsigned_materializer_marker_is_rejected(self):
        def mutate(nodes):
            nodes[self.MAT] = self.signed_materializer(verified=False)
        with self.assertRaisesRegex(ValueError, "UNVERIFIED_MATERIALIZER_ANCESTOR"):
            self.run_helper(mutate_nodes=mutate)

    def test_wrong_materializer_identity_is_rejected(self):
        def mutate(nodes):
            node = self.signed_materializer(); node["author"] = {"name": "owner", "email": "owner@example.invalid"}; nodes[self.MAT] = node
        with self.assertRaisesRegex(ValueError, "UNVERIFIED_MATERIALIZER_ANCESTOR"):
            self.run_helper(mutate_nodes=mutate)

    def test_projection_tampering_after_materialization_is_rejected(self):
        def mutate(trees):
            trees[self.TREE_REPAIR] = dict(trees[self.TREE_REPAIR])
            trees[self.TREE_REPAIR]["MANIFEST.json"] = {"sha": "f" * 40, "mode": "100644", "size": 4}
        with self.assertRaisesRegex(ValueError, "PROJECTION_TAMPERED"):
            self.run_helper(mutate_trees=mutate)

    def test_projection_tamper_then_restore_is_still_rejected(self):
        tamper = "d" * 40
        tree_tamper = "e" * 40
        tree_restore = "f" * 40
        nodes, trees = self.fixture()
        original_repair_tree = trees[self.TREE_REPAIR]
        nodes[tamper] = {"sha": tamper, "tree_sha": tree_tamper, "parents": [self.MAT], "message": "tamper"}
        nodes[self.REPAIR] = {"sha": self.REPAIR, "tree_sha": tree_restore, "parents": [tamper], "message": "restore and repair"}
        trees[tree_tamper] = dict(trees[self.TREE_MAT])
        trees[tree_tamper]["MANIFEST.json"] = {"sha": "f" * 40, "mode": "100644", "size": 4}
        trees[tree_restore] = dict(original_repair_tree)
        req = self.request()
        with mock.patch.object(GATE, "_commit_node", side_effect=lambda c, sha, cache: nodes[sha]), \
             mock.patch.object(GATE, "_tree_files", side_effect=lambda c, sha: trees[sha]):
            with self.assertRaisesRegex(ValueError, "PROJECTION_TAMPERED"):
                GATE._source_effect_with_verified_materialization_lineage(
                    Client(), req, trees[TREE_BASE], trees[tree_restore], {},
                )

    def test_materializer_with_nonprojection_effect_is_rejected(self):
        def mutate(trees):
            trees[self.TREE_MAT] = dict(trees[self.TREE_MAT])
            trees[self.TREE_MAT][SOURCE[0]] = {"sha": "e" * 40, "mode": "100644", "size": 5}
            trees[self.TREE_REPAIR] = dict(trees[self.TREE_REPAIR]); trees[self.TREE_REPAIR][SOURCE[0]] = trees[self.TREE_MAT][SOURCE[0]]
        with self.assertRaisesRegex(ValueError, "ANCESTOR_EFFECT_INVALID"):
            self.run_helper(mutate_trees=mutate)

    def test_merge_ambiguity_is_rejected(self):
        def mutate(nodes):
            nodes[self.REPAIR] = dict(nodes[self.REPAIR]); nodes[self.REPAIR]["parents"] = [self.MAT, "d" * 40]
        with self.assertRaisesRegex(ValueError, "LINEAR_ANCESTRY_REQUIRED"):
            self.run_helper(mutate_nodes=mutate)

    def test_no_trusted_materializer_with_projection_drift_is_rejected(self):
        def mutate(nodes):
            nodes[self.MAT] = dict(nodes[self.MAT]); nodes[self.MAT]["message"] = "ordinary source commit"
        with self.assertRaisesRegex(ValueError, "PROJECTION_PREEDIT_FORBIDDEN"):
            self.run_helper(mutate_nodes=mutate)

    def test_extra_source_path_remains_visible_for_exact_source_allowlist(self):
        def mutate(trees):
            extra = {"sha": "c" * 40, "mode": "100644", "size": 1}
            trees[self.TREE_REPAIR] = dict(trees[self.TREE_REPAIR]); trees[self.TREE_REPAIR]["extra.txt"] = extra
        effect, ancestor = self.run_helper(mutate_trees=mutate)
        self.assertEqual([row["path"] for row in effect], SOURCE + ["extra.txt"])
        self.assertEqual(ancestor, self.MAT)


if __name__ == "__main__":
    unittest.main()

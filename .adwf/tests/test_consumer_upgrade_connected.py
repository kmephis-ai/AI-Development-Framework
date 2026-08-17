from __future__ import annotations
from pathlib import Path
import copy, hashlib, json, os, shutil, sys, tempfile, unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf")); sys.path.insert(0, str(ROOT / ".adwf/tests"))
from consumer_upgrade_fixture import A, B  # noqa: E402
from consumer_upgrade_transaction_fixture import prepared_transaction  # noqa: E402
from lib.consumer_gates import build_binding as build_gates, load_binding as load_gates, write_binding as write_gates  # noqa: E402
from lib.consumer_installation import build_record, load_record, rebind_snapshot_for_fresh_session, validate_fresh_session, write_record  # noqa: E402
from lib.consumer_operational import build_binding as build_ops, load_binding as load_ops, write_binding as write_ops  # noqa: E402
from lib.consumer_upgrade import _root_sha  # noqa: E402
from lib.consumer_upgrade_projection import (  # noqa: E402
    ConsumerUpgradeProjectionError, apply_connected_upgrade, prepare_projection,
    recover_connected_upgrade, rollback_connected_upgrade,
)
from lib.consumer_upgrade_transaction import apply_upgrade  # noqa: E402

REPO = "example/consumer"
PHASES = {
    "pr": [{"check_name":"validate","app_slug":"github-actions","app_id":15368}],
    "main": [{"check_name":"verify","app_slug":"github-actions","app_id":15368}],
    "runtime": [],
}
REQUIRED = ["pr", "main"]


def pretty_sha(value: dict) -> str:
    payload=json.dumps(value,ensure_ascii=False,indent=2).encode()+b"\n"
    return hashlib.sha256(payload).hexdigest()


class ConnectedUpgradeTests(unittest.TestCase):
    def _git_identity(self, source: Path, target: Path):
        def fake(root: Path, *args: str) -> str:
            root=Path(root).resolve()
            if args == ("rev-parse", "HEAD"):
                return A if root == source.resolve() else B
            if args == ("rev-parse", "HEAD^{tree}"):
                return "c"*40 if root == source.resolve() else "d"*40
            raise AssertionError((root,args))
        return fake

    def _prepared(self):
        temp,s,t,c,snap,comp,plan=prepared_transaction(ROOT)
        (c/"docs").mkdir(exist_ok=True); (c/"docs/ROADMAP.md").write_text("# Consumer Roadmap\n",encoding="utf-8")
        adoption={"status":"COMMITTED","transaction_id":snap["transaction_id"],"snapshot":snap,"snapshot_sha256":pretty_sha(snap)}
        with patch("lib.consumer_installation._git", side_effect=self._git_identity(s,t)):
            rec=build_record(s,c,adoption,consumer_repository=REPO,consumer_base_sha="1"*40,consumer_base_tree="2"*40)
            write_record(rec,c,s)
            ops=build_ops(c,s,consumer_repository=REPO,roadmap_path="docs/ROADMAP.md"); write_ops(ops,c,s)
            gates=build_gates(c,s,phases=PHASES,required_phases=REQUIRED); write_gates(gates,c,s)
        return temp,s,t,c,snap,comp,plan

    def _ctx(self,s,t):
        return patch.multiple("lib.consumer_installation", _git=self._git_identity(s,t))

    def test_01_fresh_session_rebind_changes_only_session_root(self):
        temp,s,t,c,snap,comp,plan=self._prepared()
        try:
            moved=Path(temp.name)/"different-checkout"; shutil.copytree(c,moved)
            with patch.dict(os.environ,{"GITHUB_REPOSITORY":REPO}), self._ctx(s,t):
                rebound=rebind_snapshot_for_fresh_session(moved,s)
            expected=copy.deepcopy(snap); expected["consumer_root_sha256"]=_root_sha(moved.resolve())
            self.assertEqual(rebound,expected)
            self.assertNotEqual(rebound["consumer_root_sha256"],snap["consumer_root_sha256"])
        finally: temp.cleanup()

    def test_02_apply_projects_durable_b_and_rollback_restores_exact_a(self):
        temp,s,t,c,snap,comp,plan=self._prepared()
        try:
            before={rel:(c/rel).read_bytes() for rel in (".adwf-consumer/installation.json",".adwf-consumer/operations.json",".adwf-consumer/gates.json")}
            with patch.dict(os.environ,{"GITHUB_REPOSITORY":REPO}), self._ctx(s,t), patch("lib.consumer_upgrade._verify_revision",return_value=None), patch("lib.consumer_upgrade_transaction._verify_revision",return_value=None):
                rebound=rebind_snapshot_for_fresh_session(c,s)
                result=apply_connected_upgrade(s,t,c,comp,plan,rebound)
                self.assertEqual(result["status"],"COMMITTED"); self.assertEqual(result["projection_status"],"COMMITTED")
                self.assertEqual(load_record(c,t)["framework"]["source_sha"],B)
                self.assertEqual(load_ops(c,t)["roadmap"]["path"],"docs/ROADMAP.md")
                self.assertEqual(load_gates(c,t)["phases"],PHASES)
                self.assertEqual(validate_fresh_session(c,t,expected_repository=REPO)["status"],"VERIFIED")
                again=apply_connected_upgrade(s,t,c,comp,plan,rebound)
                self.assertEqual(again["status"],"ALREADY_COMMITTED"); self.assertFalse(again["write_performed"])
                rolled=rollback_connected_upgrade(s,t,c,result["transaction_id"])
                self.assertEqual(rolled["status"],"ROLLED_BACK")
                for rel,payload in before.items(): self.assertEqual((c/rel).read_bytes(),payload)
                self.assertEqual(validate_fresh_session(c,s,expected_repository=REPO)["status"],"VERIFIED")
        finally: temp.cleanup()

    def test_03_crash_after_core_commit_can_finish_projection(self):
        temp,s,t,c,snap,comp,plan=self._prepared()
        try:
            with patch.dict(os.environ,{"GITHUB_REPOSITORY":REPO}), self._ctx(s,t), patch("lib.consumer_upgrade._verify_revision",return_value=None), patch("lib.consumer_upgrade_transaction._verify_revision",return_value=None):
                rebound=rebind_snapshot_for_fresh_session(c,s)
                txid,_=prepare_projection(s,t,c,comp,plan,rebound)
                core=apply_upgrade(s,t,c,comp,plan,rebound); self.assertEqual(core["status"],"COMMITTED")
                self.assertEqual(load_record(c,s)["framework"]["source_sha"],A)
                recovered=recover_connected_upgrade(s,t,c,txid)
                self.assertEqual(recovered["status"],"COMMITTED")
                self.assertEqual(load_record(c,t)["framework"]["source_sha"],B)
        finally: temp.cleanup()

    def test_04_projection_failure_rolls_back_core_and_sidecars(self):
        temp,s,t,c,snap,comp,plan=self._prepared()
        try:
            before={rel:(c/rel).read_bytes() for rel in (".adwf-consumer/installation.json",".adwf-consumer/operations.json",".adwf-consumer/gates.json")}
            with patch.dict(os.environ,{"GITHUB_REPOSITORY":REPO}), self._ctx(s,t), patch("lib.consumer_upgrade._verify_revision",return_value=None), patch("lib.consumer_upgrade_transaction._verify_revision",return_value=None), patch("lib.consumer_upgrade_projection.build_gate_binding",side_effect=ConsumerUpgradeProjectionError("INJECTED_GATE_FAILURE")):
                rebound=rebind_snapshot_for_fresh_session(c,s)
                with self.assertRaisesRegex(ConsumerUpgradeProjectionError,"INJECTED_GATE_FAILURE"):
                    apply_connected_upgrade(s,t,c,comp,plan,rebound)
                for rel,payload in before.items(): self.assertEqual((c/rel).read_bytes(),payload)
                self.assertEqual(validate_fresh_session(c,s,expected_repository=REPO)["status"],"VERIFIED")
        finally: temp.cleanup()

    def test_05_foreign_sidecar_blocks_before_partial_restore(self):
        temp,s,t,c,snap,comp,plan=self._prepared()
        try:
            with patch.dict(os.environ,{"GITHUB_REPOSITORY":REPO}), self._ctx(s,t), patch("lib.consumer_upgrade._verify_revision",return_value=None), patch("lib.consumer_upgrade_transaction._verify_revision",return_value=None):
                rebound=rebind_snapshot_for_fresh_session(c,s)
                result=apply_connected_upgrade(s,t,c,comp,plan,rebound)
                install_before=(c/".adwf-consumer/installation.json").read_bytes(); ops_before=(c/".adwf-consumer/operations.json").read_bytes()
                (c/".adwf-consumer/gates.json").write_text('{"foreign":true}\n',encoding="utf-8")
                rolled=rollback_connected_upgrade(s,t,c,result["transaction_id"])
                self.assertEqual(rolled["status"],"RECOVERY_BLOCKED")
                self.assertEqual((c/".adwf-consumer/installation.json").read_bytes(),install_before)
                self.assertEqual((c/".adwf-consumer/operations.json").read_bytes(),ops_before)
        finally: temp.cleanup()

if __name__ == "__main__": unittest.main()

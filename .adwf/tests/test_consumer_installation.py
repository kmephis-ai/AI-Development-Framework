from __future__ import annotations
from pathlib import Path
import copy, hashlib, json, shutil, subprocess, tempfile, unittest
import sys
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib.consumer_installation import (ConsumerInstallationError, RECORD_REL, build_record, load_record, seal_record, validate_fresh_session, write_record)
from lib.consumer_profile import apply_consumer_profile
from lib.managed_surface import plan_adoption
from lib.managed_surface_transaction import apply_adoption

class ConsumerInstallationTests(unittest.TestCase):
    def _source(self, base: Path) -> tuple[Path,str]:
        source = base / "source"
        (source / ".adwf/schemas").mkdir(parents=True)
        (source / ".adwf/packs").mkdir(parents=True)
        schema_names = (
            "managed-surface-policy.schema.json", "managed-surface-snapshot.schema.json",
            "managed-surface-plan.schema.json", "managed-surface-transaction.schema.json",
            "managed-surface-detach-transaction.schema.json", "config.schema.json",
            "project-pack.schema.json", "consumer-profile.schema.json",
            "consumer-installation-record.schema.json",
        )
        for name in schema_names:
            shutil.copy2(ROOT / ".adwf/schemas" / name, source / ".adwf/schemas" / name)
        shutil.copy2(ROOT / ".adwf/config.json", source / ".adwf/config.json")
        shutil.copy2(ROOT / ".adwf/packs/node.json", source / ".adwf/packs/node.json")
        policy = json.loads((ROOT / ".adwf/managed-surface-policy.json").read_text(encoding="utf-8"))
        policy["shared_guarded_paths"] = []
        (source / ".adwf/managed-surface-policy.json").write_text(json.dumps(policy, indent=2)+"\n", encoding="utf-8")
        files = sorted(str(p.relative_to(source)).replace("\\","/") for p in source.rglob("*") if p.is_file())
        manifest={"framework":"AI Development Framework","version":"test","schema_version":3,"scope":"FRAMEWORK_OWNED_TRUST_BOUNDARY","file_count_excluding_manifests":len(files),"total_bytes_excluding_manifests":sum((source/x).stat().st_size for x in files),"files":files}
        (source/"MANIFEST.json").write_text(json.dumps(manifest,indent=2)+"\n", encoding="utf-8")
        sums=files+["MANIFEST.json"]
        (source/"SHA256SUMS.txt").write_text("".join(f"{hashlib.sha256((source/x).read_bytes()).hexdigest()}  {x}\n" for x in sorted(sums)), encoding="utf-8")
        subprocess.run(["git","init","-q"],cwd=source,check=True); subprocess.run(["git","config","user.name","t"],cwd=source,check=True); subprocess.run(["git","config","user.email","t@example.invalid"],cwd=source,check=True)
        subprocess.run(["git","add","."],cwd=source,check=True); subprocess.run(["git","commit","-q","-m","source"],cwd=source,check=True)
        return source, subprocess.check_output(["git","rev-parse","HEAD"],cwd=source,text=True).strip()

    def _consumer(self, base: Path) -> tuple[Path,str,str]:
        c=base/"consumer"; c.mkdir(); (c/"package.json").write_text(json.dumps({"name":"consumer"})+"\n"); (c/"package-lock.json").write_text("{}\n")
        subprocess.run(["git","init","-q"],cwd=c,check=True); subprocess.run(["git","config","user.name","t"],cwd=c,check=True); subprocess.run(["git","config","user.email","t@example.invalid"],cwd=c,check=True); subprocess.run(["git","add","."],cwd=c,check=True); subprocess.run(["git","commit","-q","-m","base"],cwd=c,check=True)
        sha=subprocess.check_output(["git","rev-parse","HEAD"],cwd=c,text=True).strip(); tree=subprocess.check_output(["git","rev-parse","HEAD^{tree}"],cwd=c,text=True).strip(); return c,sha,tree

    def _installed(self, base: Path):
        source,rev=self._source(base); consumer,bsha,btree=self._consumer(base)
        plan=plan_adoption(source,consumer,source_revision=rev); self.assertEqual(plan["status"],"READY")
        adoption=apply_adoption(source,consumer,plan); self.assertEqual(adoption["status"],"COMMITTED")
        profile=apply_consumer_profile(consumer,consumer,product_name="Consumer",default_branch="main",repository_visibility="PRIVATE"); self.assertEqual(profile["status"],"APPLIED")
        rec=build_record(source,consumer,adoption,consumer_repository="example/consumer",consumer_base_sha=bsha,consumer_base_tree=btree); write_record(rec,consumer,source)
        return source,consumer,rec

    def test_fresh_session_survives_runtime_state_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,consumer,rec=self._installed(Path(tmp)); shutil.rmtree(consumer/".adwf-runtime")
            result=validate_fresh_session(consumer,source,expected_repository="example/consumer")
            self.assertEqual(result["status"],"VERIFIED"); self.assertEqual(result["mutation_authority"],"NONE_RECORD_IS_PROOF_ONLY"); self.assertGreater(result["managed_entries_verified"],10)

    def test_record_requires_committed_adoption(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,rev=self._source(Path(tmp)); consumer,bsha,btree=self._consumer(Path(tmp)); plan=plan_adoption(source,consumer,source_revision=rev)
            with self.assertRaises(ConsumerInstallationError): build_record(source,consumer,{"status":"ROLLED_BACK","snapshot":{}},consumer_repository="example/consumer",consumer_base_sha=bsha,consumer_base_tree=btree)

    def test_record_tamper_and_managed_byte_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,consumer,rec=self._installed(Path(tmp)); path=consumer/RECORD_REL; value=json.loads(path.read_text()); value["framework"]["source_sha"]="0"*40; path.write_text(json.dumps(value,indent=2)+"\n")
            with self.assertRaises(ConsumerInstallationError): load_record(consumer,source)
            path.write_text(json.dumps(rec,indent=2)+"\n"); managed=next(x for x in rec["managed_surface"]["entries"] if x["managed_by_adwf"]); (consumer/managed["path"]).write_text("drift\n")
            with self.assertRaises(ConsumerInstallationError): validate_fresh_session(consumer,source)

    def test_record_cannot_claim_mutation_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,consumer,rec=self._installed(Path(tmp)); forged=copy.deepcopy(rec); forged["mutation_authority"]="WRITE"; forged=seal_record(forged); (consumer/RECORD_REL).write_text(json.dumps(forged,indent=2)+"\n")
            with self.assertRaises(ConsumerInstallationError): load_record(consumer,source)

    def test_foreign_record_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,consumer,rec=self._installed(Path(tmp)); path=consumer/RECORD_REL; path.write_text('{"foreign":true}\n')
            with self.assertRaises(ConsumerInstallationError): write_record(rec,consumer,source)

if __name__ == "__main__": unittest.main()

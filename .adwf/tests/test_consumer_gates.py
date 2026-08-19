from __future__ import annotations
from pathlib import Path
import copy,json,shutil,tempfile,unittest,sys
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/".adwf"))
from lib.consumer_gates import ConsumerGateError,build_binding,write_binding,load_binding,resolve_provider_phase,seal_binding,GATES_REL
from tests.test_consumer_operational import ConsumerOperationalTests
from lib.consumer_operational import build_binding as build_ops, write_binding as write_ops
class Client:
 def __init__(self,checks): self.checks=checks
 def check_runs(self,sha): return copy.deepcopy(self.checks)
class ConsumerGateTests(unittest.TestCase):
 def _ready(self,base):
  helper=ConsumerOperationalTests(); source,consumer,_=helper._installed(base)
  for root in (source,consumer):
   dst=root/".adwf/schemas/consumer-gates.schema.json"; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(ROOT/".adwf/schemas/consumer-gates.schema.json",dst)
  ops=build_ops(consumer,consumer,consumer_repository="example/consumer",roadmap_path="docs/ROADMAP.md"); write_ops(ops,consumer,consumer)
  phases={"pr":[{"check_name":"PR Validation","app_slug":"github-actions","app_id":15368}],"main":[{"check_name":"Main Verification","app_slug":"github-actions","app_id":15368}],"runtime":[]}
  b=build_binding(consumer,consumer,phases=phases,required_phases=["pr","main"]); write_binding(b,consumer,consumer); return source,consumer,b
 def test_exact_success(self):
  with tempfile.TemporaryDirectory() as t:
   _,c,_=self._ready(Path(t)); sha="a"*40; checks=[{"id":1,"name":"PR Validation","head_sha":sha,"status":"completed","conclusion":"success","app":{"slug":"github-actions","id":15368}}]
   r=resolve_provider_phase(c,c,Client(checks),subject_sha=sha,phase="pr"); self.assertEqual(r["status"],"VERIFIED")
 def test_stale_wrong_app_failure_pending_and_duplicate_block(self):
  with tempfile.TemporaryDirectory() as t:
   _,c,_=self._ready(Path(t)); sha="b"*40; base={"id":1,"name":"PR Validation","head_sha":sha,"status":"completed","conclusion":"success","app":{"slug":"github-actions","id":15368}}
   variants=[[{**base,"head_sha":"c"*40}],[{**base,"app":{"slug":"other","id":1}}],[{**base,"conclusion":"failure"}],[{**base,"status":"in_progress","conclusion":None}],[]]
   for checks in variants:
    self.assertEqual(resolve_provider_phase(c,c,Client(checks),subject_sha=sha,phase="pr")["status"],"NOT_VERIFIED")
 def test_repeated_check_runs_use_newest_provider_identity(self):
  with tempfile.TemporaryDirectory() as t:
   _,c,_=self._ready(Path(t)); sha="c"*40
   def check(run_id,status="completed",conclusion="success"):
    return {"id":run_id,"name":"PR Validation","head_sha":sha,"status":status,"conclusion":conclusion,"app":{"slug":"github-actions","id":15368}}
   result=resolve_provider_phase(c,c,Client([check(20),check(10)]),subject_sha=sha,phase="pr")
   self.assertEqual(result["status"],"VERIFIED"); self.assertEqual(result["matched"][0]["check_run_id"],20)
   result=resolve_provider_phase(c,c,Client([check(10,conclusion="failure"),check(20)]),subject_sha=sha,phase="pr")
   self.assertEqual(result["status"],"VERIFIED"); self.assertEqual(result["matched"][0]["check_run_id"],20)
   for newest in (check(20,"in_progress",None),check(20,"completed","failure"),check(20,"completed","cancelled"),check(20,"completed","skipped")):
    result=resolve_provider_phase(c,c,Client([check(10),newest]),subject_sha=sha,phase="pr")
    self.assertEqual(result["status"],"NOT_VERIFIED"); self.assertEqual(result["failures"],["NOT_SUCCESS:PR Validation"])
 def test_repeated_check_runs_reject_ambiguous_or_malformed_provider_identity(self):
  with tempfile.TemporaryDirectory() as t:
   _,c,_=self._ready(Path(t)); sha="d"*40; base={"id":10,"name":"PR Validation","head_sha":sha,"status":"completed","conclusion":"success","app":{"slug":"github-actions","id":15368}}
   malformed=(None,"20",True,0,-1)
   variants=[[base,{**base}],*[[base,{**base,"id":value}] for value in malformed]]
   for checks in variants:
    result=resolve_provider_phase(c,c,Client(checks),subject_sha=sha,phase="pr")
    self.assertEqual(result["status"],"NOT_VERIFIED"); self.assertEqual(result["failures"],["AMBIGUOUS_OR_MISSING:PR Validation"])
 def test_nonmatching_newer_check_cannot_change_authority(self):
  with tempfile.TemporaryDirectory() as t:
   _,c,_=self._ready(Path(t)); sha="e"*40; base={"id":10,"name":"PR Validation","head_sha":sha,"status":"completed","conclusion":"success","app":{"slug":"github-actions","id":15368}}
   outsiders=[{**base,"id":99,"head_sha":"f"*40},{**base,"id":100,"name":"Other"},{**base,"id":101,"app":{"slug":"other","id":15368}},{**base,"id":102,"app":{"slug":"github-actions","id":1}}]
   result=resolve_provider_phase(c,c,Client([base,*outsiders]),subject_sha=sha,phase="pr")
   self.assertEqual(result["status"],"VERIFIED"); self.assertEqual(result["matched"][0]["check_run_id"],10)
 def test_binding_tamper_and_authority_block(self):
  with tempfile.TemporaryDirectory() as t:
   _,c,b=self._ready(Path(t)); p=c/GATES_REL
   x=copy.deepcopy(b); x["consumer_repository"]="other/repo"; x=seal_binding(x); p.write_text(json.dumps(x)+"\n")
   with self.assertRaises(ConsumerGateError): load_binding(c,c)
   x=copy.deepcopy(b); x["mutation_authority"]="WRITE"; x=seal_binding(x); p.write_text(json.dumps(x)+"\n")
   with self.assertRaises(ConsumerGateError): load_binding(c,c)
if __name__=="__main__": unittest.main()

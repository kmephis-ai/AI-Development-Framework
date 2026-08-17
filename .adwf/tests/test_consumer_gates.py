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
   variants=[[{**base,"head_sha":"c"*40}],[{**base,"app":{"slug":"other","id":1}}],[{**base,"conclusion":"failure"}],[{**base,"status":"in_progress","conclusion":None}],[base,{**base,"id":2}],[]]
   for checks in variants:
    self.assertEqual(resolve_provider_phase(c,c,Client(checks),subject_sha=sha,phase="pr")["status"],"NOT_VERIFIED")
 def test_binding_tamper_and_authority_block(self):
  with tempfile.TemporaryDirectory() as t:
   _,c,b=self._ready(Path(t)); p=c/GATES_REL
   x=copy.deepcopy(b); x["consumer_repository"]="other/repo"; x=seal_binding(x); p.write_text(json.dumps(x)+"\n")
   with self.assertRaises(ConsumerGateError): load_binding(c,c)
   x=copy.deepcopy(b); x["mutation_authority"]="WRITE"; x=seal_binding(x); p.write_text(json.dumps(x)+"\n")
   with self.assertRaises(ConsumerGateError): load_binding(c,c)
if __name__=="__main__": unittest.main()

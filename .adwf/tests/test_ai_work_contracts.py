import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'.adwf'))
from lib.ai_work_contracts import compile_work_package,build_work_result,validate_work_package,validate_work_result,canonicalize_low_trust_claim,path_is_allowed
from lib.contracts import validate
from lib.runtime_supervisor import ActionEnvelopeStore
from lib.github_agent_inbox import GitHubAgentInbox,validate_agent_result

BASE='a'*40
HEAD='b'*40


def state(**updates):
    value={'run_id':'run-12345678','roadmap_id':'AIWORK-001','issue_id':'39','revision':4,'phase':'EXECUTE','work_type':'feature','risk':'R1','subject_sha':BASE}
    value.update(updates);return value


def memory():
    return {'brief_id':'AIWORK-001','run_id':'run-12345678','status':'ACTIVE','task_ru':'Реализовать bounded work contracts',
            'product_brief':{'goal_ru':'Создать строгий контракт AI work','outcome_ru':'AI получает exact bounded package и возвращает связанный result',
                             'acceptance_criteria_ru':['Exact base обязателен','Forbidden writes блокируются']},
            'verification':[],'next_action_ru':'Выполнить bounded package'}


class FakeGitHub:
    def __init__(self):self._issues=[];self.comments={};self.cid=0
    def issues(self):return list(self._issues)
    def create_issue(self,title,body):
        item={'number':1,'title':title,'body':body,'state':'open'};self._issues=[item];self.comments[1]=[];return item
    def issue_comments(self,n):return list(self.comments.get(n,[]))
    def add_issue_comment(self,n,body):
        self.cid+=1;item={'id':self.cid,'body':body,'created_at':'2026-08-15T00:00:00Z','user':{'login':'agent'}};self.comments[n].append(item);return item


class AIWorkContractTests(unittest.TestCase):
    def test_package_is_schema_valid_and_exact_state_bound(self):
        pkg=compile_work_package(state(),memory(),created_at='2026-08-15T00:00:00Z')
        self.assertEqual(validate_work_package(pkg,expected_state=state()),[])
        schema=json.loads((ROOT/'.adwf/schemas/ai-work-package.schema.json').read_text())
        self.assertEqual(validate(pkg,schema),[])
        self.assertEqual(pkg['base_sha'],BASE);self.assertEqual(pkg['monetary_budget_usd'],0)

    def test_missing_or_stale_base_fails_closed(self):
        with self.assertRaisesRegex(ValueError,'BASE_SHA_REQUIRED'):compile_work_package(state(subject_sha=None),memory())
        pkg=compile_work_package(state(),memory())
        stale=state(subject_sha='c'*40)
        self.assertIn('PACKAGE_STATE_BINDING_MISMATCH:base_sha',validate_work_package(pkg,expected_state=stale))

    def test_surface_allowlist_and_forbidden_precedence(self):
        pkg=compile_work_package(state(),memory(),allowed_write_surfaces=['src/**','docs/**'],forbidden_write_surfaces=['src/secrets/**'])
        self.assertTrue(path_is_allowed('src/app.py',pkg));self.assertFalse(path_is_allowed('src/secrets/key.txt',pkg));self.assertFalse(path_is_allowed('README.md',pkg))
        with self.assertRaisesRegex(ValueError,'WRITE_SURFACE_FORBIDDEN'):
            build_work_result(pkg,outcome='PASS',head_sha=HEAD,changed_paths=['src/secrets/key.txt'],verification_claims=['unit PASS'],evidence_claims=['changed_paths','verification_claims'])

    def test_valid_result_is_schema_valid_but_still_only_a_claim(self):
        pkg=compile_work_package(state(),memory())
        result=build_work_result(pkg,outcome='PASS',head_sha=HEAD,changed_paths=['src/app.py'],verification_claims=['unit tests claimed PASS'],evidence_claims=['changed_paths','verification_claims'],summary_ru='Изменение подготовлено.')
        self.assertEqual(validate_work_result(result,package=pkg),[])
        schema=json.loads((ROOT/'.adwf/schemas/ai-work-result.schema.json').read_text())
        self.assertEqual(validate(result,schema),[])
        self.assertEqual(result['base_sha'],BASE);self.assertEqual(result['cost_usd'],0)

    def test_package_substitution_and_missing_evidence_are_rejected(self):
        pkg=compile_work_package(state(),memory())
        claim={'package_id':pkg['package_id'],'package_digest':'0'*64,'base_sha':BASE,'outcome':'PASS','subject_sha':HEAD,'changed_paths':['src/a.py'],
               'verification_claims':['tests'], 'evidence_claims':['changed_paths','verification_claims']}
        with self.assertRaisesRegex(ValueError,'PACKAGE_BINDING_MISMATCH'):canonicalize_low_trust_claim(claim,package=pkg)
        with self.assertRaisesRegex(ValueError,'REQUIRED_EVIDENCE_CLAIM_MISSING'):
            build_work_result(pkg,outcome='PASS',head_sha=HEAD,changed_paths=['src/a.py'],verification_claims=['tests'],evidence_claims=['changed_paths'])

    def test_action_envelope_contains_first_class_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);store=ActionEnvelopeStore(root)
            # WorkMemoryStore is optional; compiler uses deterministic safe defaults.
            key,path=store.ensure_request(state())
            value=json.loads(path.read_text())
            self.assertEqual(value['schema_version'],3);self.assertEqual(value['work_package_digest'],value['work_package']['package_digest'])
            self.assertEqual(value['work_package']['base_sha'],BASE);self.assertEqual(len(key),64)

    def test_agent_inbox_publishes_safe_package_projection(self):
        pkg=compile_work_package(state(),memory())
        env={'idempotency_key':'k'*64,'run_id':'run-12345678','revision':4,'brief_id':'AIWORK-001','phase':'EXECUTE','capability':'edit','subject_sha':BASE,'risk':'R1','work_type':'feature','work_package':pkg,'work_package_digest':pkg['package_digest']}
        fake=FakeGitHub();out=GitHubAgentInbox(fake).publish(env,memory());self.assertEqual(out['status'],'PUBLISHED')
        body=fake.comments[1][0]['body'];self.assertIn(pkg['package_digest'],body);self.assertNotIn(pkg['goal'],body);self.assertNotIn('acceptance_criteria',body)

    def test_agent_result_is_package_bound_and_canonicalized_low_trust(self):
        pkg=compile_work_package(state(),memory())
        request={'idempotency_key':'k'*64,'run_id':'run-12345678','phase':'EXECUTE','work_package':pkg,'work_package_digest':pkg['package_digest']}
        claim={'schema_version':3,'idempotency_key':'k'*64,'run_id':'run-12345678','phase':'EXECUTE','package_id':pkg['package_id'],'package_digest':pkg['package_digest'],'base_sha':BASE,
               'outcome':'PASS','subject_sha':HEAD,'branch':'adwf/aiwork-001','changed_paths':['src/app.py'],'verification_claims':['tests claimed PASS'],'evidence_claims':['changed_paths','verification_claims'],'reason_codes':[],'summary_ru':'Готово'}
        result=validate_agent_result(claim,request=request);self.assertEqual(result['outcome'],'PASS');self.assertEqual(result['evidence_refs'],[])
        self.assertEqual(result['metadata']['source'],'LOW_TRUST_AGENT_RESULT');self.assertEqual(result['metadata']['ai_work_result']['package_digest'],pkg['package_digest'])
        bad=dict(claim);bad['base_sha']='c'*40
        with self.assertRaisesRegex(ValueError,'PACKAGE_BINDING_MISMATCH'):validate_agent_result(bad,request=request)

    def test_authoritative_state_can_narrow_surfaces_and_evidence(self):
        narrowed=state(allowed_write_surfaces=['src/**'],forbidden_write_surfaces=['src/private/**'],required_evidence=['changed_paths','verification_claims','review_claim'])
        pkg=compile_work_package(narrowed,memory())
        self.assertEqual(pkg['allowed_write_surfaces'],['src/**'])
        self.assertEqual(pkg['required_evidence'],['changed_paths','verification_claims','review_claim'])
        with self.assertRaisesRegex(ValueError,'WRITE_SURFACE_FORBIDDEN'):
            build_work_result(pkg,outcome='PASS',head_sha=HEAD,changed_paths=['README.md'],verification_claims=['tests'],evidence_claims=['changed_paths','verification_claims','review_claim'])

    def test_low_trust_result_cannot_supply_canonical_timestamp_or_unchecked_request_digest(self):
        pkg=compile_work_package(state(),memory())
        request={'idempotency_key':'k'*64,'run_id':'run-12345678','phase':'EXECUTE','work_package':pkg,'work_package_digest':pkg['package_digest']}
        base={'schema_version':3,'idempotency_key':'k'*64,'run_id':'run-12345678','phase':'EXECUTE','package_id':pkg['package_id'],'package_digest':pkg['package_digest'],'base_sha':BASE,
              'outcome':'PASS','subject_sha':HEAD,'changed_paths':['src/app.py'],'verification_claims':['tests'],'evidence_claims':['changed_paths','verification_claims'],'reason_codes':[],'summary_ru':'Готово'}
        for field,value in [('created_at','2000-01-01T00:00:00Z'),('request_digest','0'*64)]:
            claim=dict(base);claim[field]=value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError,'FIELDS_FORBIDDEN'):
                validate_agent_result(claim,request=request)

if __name__=='__main__':unittest.main()

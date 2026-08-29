import base64
import hashlib
import json
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / ".adwf"))
from lib import provider_source_ingest_gateway as GATE

MAIN = "a" * 40
HEAD = "b" * 40
NEW = "c" * 40
TREE_HEAD = "d" * 40
TREE_NEW = "e" * 40
LEASE = "11111111-1111-4111-8111-111111111111"
BRANCH = "adwf/gov-034-test"
WORKER = "adwf-runtime:gov-034-test"
FILES = {
    GATE.SOURCE_PATHS[0]: "print('ops')\n",
    GATE.SOURCE_PATHS[1]: "print('tests')\n",
}


def write_policy(root: Path, *, budget=0, writers=1):
    (root / ".adwf").mkdir(parents=True, exist_ok=True)
    (root / ".adwf/effective-policy.json").write_text(json.dumps({
        "active_autonomy": "A2", "max_autonomous_risk": "R1", "hard_budget_usd": budget, "mandatory_ai_api": False,
        "max_parallel_writers": writers, "rules": {"autonomy_rank": {"A0":0,"A1":1,"A2":2,"A3":3,"A4":4}},
    }))


def body(**overrides):
    values = dict(request_id="gov034-source-a1", expected_main_sha=MAIN, branch=BRANCH, head_sha=HEAD,
                  worker_id=WORKER, lease_id=LEASE, lease_registry_revision=80, files=dict(FILES))
    values.update(overrides)
    return GATE.build_source_ingest_comment(**values)


def event(raw=None, actor="owner", association="OWNER"):
    return {"action":"created","repository":{"full_name":"owner/repo"},"issue":{"number":291},
            "sender":{"login":actor},"comment":{"body":raw if raw is not None else body(),
            "author_association":association,"user":{"login":actor}}}


class Client:
    repo = "owner/repo"
    def __init__(self):
        self.main=MAIN; self.branch_sha=HEAD; self.issue_title="[P0][GOV-034] Test"; self.updated=[]
        self.commit_nodes={
            HEAD:{"sha":HEAD,"tree":{"sha":TREE_HEAD},"parents":[{"sha":MAIN}],"message":"head"},
            NEW:{"sha":NEW,"tree":{"sha":TREE_NEW},"parents":[{"sha":HEAD}],"message":""},
        }
        self.tree_payloads={TREE_HEAD:self._tree(TREE_HEAD, {}), TREE_NEW:self._tree(TREE_NEW, {})}
        self.created_blobs=[]
    @staticmethod
    def _tree(sha, overrides):
        rows=[]
        base={"README.md":"f"*40, **overrides}
        for path, blob in sorted(base.items()): rows.append({"path":path,"type":"blob","mode":"100644","sha":blob,"size":1})
        return {"sha":sha,"truncated":False,"tree":rows}
    def collaborator_permission(self, login): return {"permission":"admin"}
    def repo_info(self): return {"default_branch":"main"}
    def branch(self, name): return {"commit":{"sha":self.main}}
    def rulesets(self): return []
    def get(self, path):
        if path.endswith('/issues/291'): return {"number":291,"state":"open","title":self.issue_title}
        raise AssertionError(path)
    def git_ref(self, branch): return {"object":{"sha":self.branch_sha}}
    def git_commit(self, sha): return dict(self.commit_nodes[sha])
    def git_tree(self, sha, recursive=False): return dict(self.tree_payloads[sha])
    def create_blob(self, raw):
        self.created_blobs.append(bytes(raw)); return {"sha":GATE._git_blob(bytes(raw))}
    def create_tree(self, *, base_tree_sha, entries):
        assert base_tree_sha == TREE_HEAD
        values={e['path']:e['sha'] for e in entries}
        self.tree_payloads[TREE_NEW]=self._tree(TREE_NEW, values)
        return {"sha":TREE_NEW}
    def create_commit(self, *, message, tree_sha, parent_sha):
        self.commit_nodes[NEW]={"sha":NEW,"tree":{"sha":tree_sha},"parents":[{"sha":parent_sha}],"message":message}
        return {"sha":NEW}
    def update_branch_ref(self, branch, sha): self.updated.append((branch,sha)); self.branch_sha=sha; return {"object":{"sha":sha}}


class ParseTests(unittest.TestCase):
    def test_roundtrip_and_exact_paths(self):
        req, files=GATE.parse_source_ingest_comment(body())
        self.assertEqual(req['lease_registry_revision'],80); self.assertEqual(files,{k:v.encode() for k,v in FILES.items()})

    def test_duplicate_unknown_paid_and_digest_tamper_rejected(self):
        raw=body(); line=raw.split('\n',1)[1]
        with self.assertRaisesRegex(ValueError,'JSON'):
            GATE.parse_source_ingest_comment(GATE.SOURCE_INGEST_MARKER+'\n'+line[:-1]+',"issue_id":291}')
        req=json.loads(line); req['secret']='x'; req['request_digest']=GATE._digest(req)
        with self.assertRaisesRegex(ValueError,'FIELDS'): GATE.parse_source_ingest_comment(GATE.SOURCE_INGEST_MARKER+'\n'+json.dumps(req,sort_keys=True,separators=(',',':')))
        req=json.loads(line); req['monetary_budget_usd']=1; req['request_digest']=GATE._digest(req)
        with self.assertRaisesRegex(ValueError,'BUDGET'): GATE.parse_source_ingest_comment(GATE.SOURCE_INGEST_MARKER+'\n'+json.dumps(req,sort_keys=True,separators=(',',':')))
        with self.assertRaisesRegex(ValueError,'DIGEST'): GATE.parse_source_ingest_comment(raw.replace('"lease_registry_revision":80','"lease_registry_revision":81'))

    def test_extra_path_blob_mismatch_and_payload_digest_fail(self):
        req=json.loads(body().split('\n',1)[1])
        payload=json.loads(zlib.decompress(base64.b64decode(req['payload_zlib_base64'])))
        payload['files'][0]['path']='.adwf/lib/evil.py'; raw=GATE._canonical(payload).encode(); req['payload_zlib_base64']=base64.b64encode(zlib.compress(raw)).decode(); req['payload_sha256']=hashlib.sha256(raw).hexdigest(); req['payload_uncompressed_bytes']=len(raw); req['request_digest']=GATE._digest(req)
        with self.assertRaisesRegex(ValueError,'PATHS'): GATE.parse_source_ingest_comment(GATE.SOURCE_INGEST_MARKER+'\n'+GATE._canonical(req))
        req=json.loads(body().split('\n',1)[1]); req['file_blobs'][GATE.SOURCE_PATHS[0]]='0'*40; req['request_digest']=GATE._digest(req)
        with self.assertRaisesRegex(ValueError,'BLOB_MISMATCH'): GATE.parse_source_ingest_comment(GATE.SOURCE_INGEST_MARKER+'\n'+GATE._canonical(req))
        req=json.loads(body().split('\n',1)[1]); req['payload_sha256']='0'*64; req['request_digest']=GATE._digest(req)
        with self.assertRaisesRegex(ValueError,'DIGEST_MISMATCH'): GATE.parse_source_ingest_comment(GATE.SOURCE_INGEST_MARKER+'\n'+GATE._canonical(req))

    def test_invalid_base64_truncated_and_oversize_fail(self):
        req=json.loads(body().split('\n',1)[1]); req['payload_zlib_base64']='!!!'; req['request_digest']=GATE._digest(req)
        with self.assertRaisesRegex(ValueError,'BASE64'): GATE.parse_source_ingest_comment(GATE.SOURCE_INGEST_MARKER+'\n'+GATE._canonical(req))
        req=json.loads(body().split('\n',1)[1]); req['payload_zlib_base64']=base64.b64encode(b'bad-zlib').decode(); req['request_digest']=GATE._digest(req)
        with self.assertRaisesRegex(ValueError,'ZLIB'): GATE.parse_source_ingest_comment(GATE.SOURCE_INGEST_MARKER+'\n'+GATE._canonical(req))
        req=json.loads(body().split('\n',1)[1]); req['payload_zlib_base64']='A'*(GATE.MAX_ENCODED+4); req['request_digest']=GATE._digest(req)
        with self.assertRaisesRegex(ValueError,'TOO_LARGE'): GATE.parse_source_ingest_comment(GATE.SOURCE_INGEST_MARKER+'\n'+GATE._canonical(req))

    def test_decompression_bomb_and_missing_path_fail(self):
        req=json.loads(body().split('\n',1)[1])
        payload={"schema_version":1,"files":[{"path":GATE.SOURCE_PATHS[0],"content":"x"*(GATE.MAX_RAW+1)},{"path":GATE.SOURCE_PATHS[1],"content":""}]}
        raw=GATE._canonical(payload).encode(); req['payload_zlib_base64']=base64.b64encode(zlib.compress(raw,9)).decode(); req['payload_sha256']=hashlib.sha256(raw).hexdigest(); req['payload_uncompressed_bytes']=GATE.MAX_RAW; req['request_digest']=GATE._digest(req)
        with self.assertRaisesRegex(ValueError,'DECOMPRESSED_TOO_LARGE'):
            GATE.parse_source_ingest_comment(GATE.SOURCE_INGEST_MARKER+'\n'+GATE._canonical(req))
        req=json.loads(body().split('\n',1)[1]); payload=json.loads(zlib.decompress(base64.b64decode(req['payload_zlib_base64']))); payload['files']=payload['files'][:1]; raw=GATE._canonical(payload).encode(); req['payload_zlib_base64']=base64.b64encode(zlib.compress(raw)).decode(); req['payload_sha256']=hashlib.sha256(raw).hexdigest(); req['payload_uncompressed_bytes']=len(raw); req['request_digest']=GATE._digest(req)
        with self.assertRaisesRegex(ValueError,'FILES_INVALID'):
            GATE.parse_source_ingest_comment(GATE.SOURCE_INGEST_MARKER+'\n'+GATE._canonical(req))


class ProcessTests(unittest.TestCase):
    def root(self):
        td=tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup); root=Path(td.name); write_policy(root); return root
    def patches(self, *, revision=80, fresh=True):
        lease={"status":"ACTIVE"}
        return mock.patch.multiple(GATE, _local_head=mock.DEFAULT, _rulesets=mock.DEFAULT, _lease=mock.DEFAULT), lease
    def execute(self, client=None, raw=None, *, revision=80, fresh=True):
        client=client or Client(); root=self.root()
        lease={"status":"ACTIVE"}
        def identity(_client, request, newer=False): return lease, revision
        with mock.patch.object(GATE,'_local_head',return_value=MAIN), mock.patch.object(GATE,'_rulesets'), mock.patch.object(GATE,'_lease',side_effect=identity):
            return GATE.process_issue_comment_source_ingest(root,event(raw),client),client

    def test_unrelated_nonadmin_and_policy_fail_closed(self):
        self.assertIsNone(GATE.process_issue_comment_source_ingest(self.root(),event('hello'),Client()))
        client=Client(); client.collaborator_permission=lambda login:{"permission":"write"}; result,_=self.execute(client)
        self.assertEqual(result['reason'],'SOURCE_INGEST_ACTOR_ADMIN_REQUIRED')
        root=self.root(); write_policy(root,budget=1)
        with mock.patch.object(GATE,'_local_head',return_value=MAIN), mock.patch.object(GATE,'_rulesets'), mock.patch.object(GATE,'_lease',return_value=({"status":"ACTIVE"},80)):
            result=GATE.process_issue_comment_source_ingest(root,event(),Client())
        self.assertIn('FREE_ONLY',result['reason'])

    def test_stale_main_issue_head_and_revision_fail_closed(self):
        client=Client(); client.main='9'*40; result,_=self.execute(client); self.assertIn('MAIN_DRIFT',result['reason'])
        client=Client(); client.issue_title='[P0][OTHER-001] Test'; result,_=self.execute(client); self.assertIn('ROADMAP',result['reason'])
        client=Client(); drift='9'*40; client.branch_sha=drift; client.commit_nodes[drift]={'sha':drift,'tree':{'sha':TREE_HEAD},'parents':[{'sha':HEAD}],'message':'other'}; result,_=self.execute(client); self.assertEqual(result['reason'],'SOURCE_INGEST_HEAD_DRIFT')
        result,_=self.execute(revision=81); self.assertEqual(result['reason'],'SOURCE_INGEST_LEASE_REVISION_DRIFT')

    def test_success_exact_effect_and_replay(self):
        result,client=self.execute(); self.assertEqual(result['status'],'PASS'); self.assertTrue(result['mutation']); self.assertEqual(client.updated,[(BRANCH,NEW)])
        self.assertEqual(client.created_blobs,[FILES[p].encode() for p in GATE.SOURCE_PATHS])
        replay,_=self.execute(client); self.assertEqual(replay['status'],'ALREADY_APPLIED'); self.assertFalse(replay['mutation']); self.assertEqual(len(client.updated),1)

    def test_provider_blob_mismatch_and_tree_escape_no_ref_mutation(self):
        client=Client(); client.create_blob=lambda raw:{"sha":"0"*40}; result,client=self.execute(client); self.assertIn('BLOB_SHA_MISMATCH',result['reason']); self.assertEqual(client.updated,[])
        client=Client()
        def bad_tree(*,base_tree_sha,entries):
            client.tree_payloads[TREE_NEW]=client._tree(TREE_NEW,{entries[0]['path']:entries[0]['sha']}); return {"sha":TREE_NEW}
        client.create_tree=bad_tree; result,client=self.execute(client); self.assertIn('TREE_EFFECT',result['reason']); self.assertEqual(client.updated,[])

    def test_stale_lease_ruleset_and_event_repository_fail_closed(self):
        root=self.root()
        with mock.patch.object(GATE,'_local_head',return_value=MAIN), mock.patch.object(GATE,'_rulesets',side_effect=ValueError('SOURCE_INGEST_PROTECTED_MAIN_RULESET_NOT_VERIFIED')), mock.patch.object(GATE,'_lease',return_value=({"status":"ACTIVE"},80)):
            result=GATE.process_issue_comment_source_ingest(root,event(),Client())
        self.assertIn('RULESET_NOT_VERIFIED',result['reason'])
        with mock.patch.object(GATE,'_local_head',return_value=MAIN), mock.patch.object(GATE,'_rulesets'), mock.patch.object(GATE,'_lease',side_effect=ValueError('SOURCE_INGEST_LEASE_NOT_FRESH')):
            result=GATE.process_issue_comment_source_ingest(self.root(),event(),Client())
        self.assertIn('LEASE_NOT_FRESH',result['reason'])
        bad=event(); bad['repository']['full_name']='fork/repo'
        result=GATE.process_issue_comment_source_ingest(self.root(),bad,Client())
        self.assertEqual(result['reason'],'SOURCE_INGEST_EVENT_IDENTITY_INVALID')

    def test_conflicting_replay_request_id_rejected_without_mutation(self):
        result,client=self.execute(); self.assertEqual(result['status'],'PASS')
        changed=dict(FILES); changed[GATE.SOURCE_PATHS[0]]="print('changed')\n"
        conflict=body(files=changed)
        result,_=self.execute(client,conflict)
        self.assertEqual(result['status'],'REJECTED'); self.assertEqual(result['reason'],'SOURCE_INGEST_HEAD_DRIFT'); self.assertEqual(len(client.updated),1)

    def test_pre_update_race_and_cas_failure_fail_closed(self):
        client=Client(); calls={'n':0}
        original_ref=client.git_ref
        def moving_ref(branch):
            calls['n']+=1
            if calls['n']>=2: return {"object":{"sha":"9"*40}}
            return original_ref(branch)
        client.git_ref=moving_ref; result,client=self.execute(client); self.assertIn('HEAD_DRIFT',result['reason']); self.assertEqual(client.updated,[])
        client=Client(); client.update_branch_ref=lambda branch,sha:(_ for _ in ()).throw(RuntimeError('cas'))
        result,client=self.execute(client); self.assertIn('BRANCH_CAS_FAILED',result['reason'])


if __name__=='__main__': unittest.main()

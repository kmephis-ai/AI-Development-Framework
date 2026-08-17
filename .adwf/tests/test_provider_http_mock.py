import json,sys,threading,time,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]; sys.path.insert(0,str(ROOT/'.adwf'))
from lib.http_transport import urllib_transport
from lib.provider_contracts import ProviderContractError,request_bytes,request_json,request_pages,mutate_with_readback
from lib.github_provider import GitHubClient

class Handler(BaseHTTPRequestHandler):
    rate_calls=0; action_run_calls=0; mutations=[]
    def sendj(self,status,obj,headers=None):
        body=(json.dumps(obj) if not isinstance(obj,bytes) else obj).encode() if isinstance(obj,str) else (obj if isinstance(obj,bytes) else json.dumps(obj).encode())
        self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body)))
        for k,v in (headers or {}).items(): self.send_header(k,v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
    def do_GET(self):
        if self.path=='/ok': return self.sendj(200,{'ok':True})
        if self.path=='/rate':
            Handler.rate_calls+=1
            return self.sendj(429,{'wait':True},{'Retry-After':'0'}) if Handler.rate_calls==1 else self.sendj(200,{'ok':True})
        if self.path=='/malformed': return self.sendj(200,b'{')
        if self.path=='/nonobject': return self.sendj(200,[1,2])
        if self.path.startswith('/status/'):
            return self.sendj(int(self.path.rsplit('/',1)[1]),{'error':True})
        if self.path.startswith('/page'):
            page='1'
            if '?' in self.path:
                for part in self.path.split('?',1)[1].split('&'):
                    if part.startswith('page='): page=part.split('=',1)[1]
            if page=='1': return self.sendj(200,{'items':[{'id':1}]},{'X-Next-Page':'2'})
            return self.sendj(200,{'items':[{'id':2}]},{'X-Next-Page':''})
        if self.path.startswith('/repos/example/repo/actions/runs'):
            Handler.action_run_calls+=1
            if 'event=badshape' in self.path:
                return self.sendj(200,{'workflow_runs':'not-a-list'})
            return self.sendj(200,{'workflow_runs':[{'id':1,'event':'pull_request'},{'id':2,'event':'pull_request'}]}, {'Link':'<https://api.example.invalid/page=2>; rel="next"'})
        if self.path=='/readback': return self.sendj(200,{'value':Handler.mutations[-1] if Handler.mutations else None})
        if self.path=='/redirect-same': self.send_response(302); self.send_header('Location','/echo-auth'); self.end_headers(); return
        if self.path=='/redirect-cross': self.send_response(302); self.send_header('Location',Handler.redirect_target+'/download'); self.end_headers(); return
        if self.path=='/echo-auth': return self.sendj(200,{'authorization':self.headers.get('Authorization'),'proxy_authorization':self.headers.get('Proxy-Authorization'),'cookie':self.headers.get('Cookie')})
        if self.path=='/slow': time.sleep(.15); return self.sendj(200,{'ok':True})
        return self.sendj(404,{'error':True})
    def do_POST(self):
        if self.path=='/mutate':
            n=int(self.headers.get('Content-Length','0')); payload=json.loads(self.rfile.read(n) or b'{}'); Handler.mutations.append(payload); return self.sendj(200,{'accepted':True})
        return self.sendj(404,{'error':True})
    def log_message(self,*args): pass

class RedirectTargetHandler(BaseHTTPRequestHandler):
    seen_headers={}
    def do_GET(self):
        RedirectTargetHandler.seen_headers={name.lower(): value for name,value in self.headers.items()}
        body=b'cross-origin-download'
        self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self,*args): pass

class ProviderHttpMockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.target_server=ThreadingHTTPServer(('127.0.0.1',0),RedirectTargetHandler); cls.target_thread=threading.Thread(target=cls.target_server.serve_forever,daemon=True); cls.target_thread.start(); cls.target=f'http://127.0.0.1:{cls.target_server.server_address[1]}'
        Handler.redirect_target=cls.target
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler); cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True); cls.thread.start(); cls.base=f'http://127.0.0.1:{cls.server.server_address[1]}'
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.target_server.shutdown(); cls.target_server.server_close()
    def test_real_transport_success_rate_limit_and_auth_cas_errors(self):
        value,_=request_json(urllib_transport,'GET',self.base+'/ok',{},timeout=1); self.assertTrue(value['ok'])
        Handler.rate_calls=0; value,_=request_json(urllib_transport,'GET',self.base+'/rate',{},timeout=1,max_attempts=2); self.assertTrue(value['ok'])
        for code in (401,403,404,409,412,422):
            with self.subTest(code=code), self.assertRaisesRegex(ProviderContractError,f'PROVIDER_HTTP_{code}'):
                request_json(urllib_transport,'GET',self.base+f'/status/{code}',{},timeout=1)
    def test_cross_origin_redirect_strips_provider_credentials_but_same_origin_preserves_them(self):
        headers={'Authorization':'Bearer provider-secret','Proxy-Authorization':'Basic proxy-secret','Cookie':'session=secret','X-Trace':'safe'}
        same,_=request_json(urllib_transport,'GET',self.base+'/redirect-same',headers,timeout=1)
        self.assertEqual(same['authorization'],'Bearer provider-secret')
        self.assertEqual(same['proxy_authorization'],'Basic proxy-secret')
        self.assertEqual(same['cookie'],'session=secret')
        RedirectTargetHandler.seen_headers={'sentinel':'not-called'}
        body,_=request_bytes(urllib_transport,'GET',self.base+'/redirect-cross',headers,timeout=1)
        self.assertEqual(body,b'cross-origin-download')
        self.assertNotIn('authorization',RedirectTargetHandler.seen_headers)
        self.assertNotIn('proxy-authorization',RedirectTargetHandler.seen_headers)
        self.assertNotIn('cookie',RedirectTargetHandler.seen_headers)
        self.assertEqual(RedirectTargetHandler.seen_headers.get('x-trace'),'safe')
    def test_origin_normalizes_default_ports(self):
        from lib.http_transport import _origin
        self.assertEqual(_origin('https://EXAMPLE.com/path'),('https','example.com',443))
        self.assertEqual(_origin('https://example.com:443/other'),('https','example.com',443))
        self.assertNotEqual(_origin('https://example.com'),_origin('http://example.com'))

    def test_malformed_partial_timeout_are_fail_closed(self):
        with self.assertRaisesRegex(ProviderContractError,'MALFORMED_JSON'): request_json(urllib_transport,'GET',self.base+'/malformed',{},timeout=1)
        with self.assertRaisesRegex(ProviderContractError,'NON_OBJECT'): request_json(urllib_transport,'GET',self.base+'/nonobject',{},timeout=1)
        with self.assertRaisesRegex(ProviderContractError,'TIMEOUT_OR_NETWORK'): request_json(urllib_transport,'GET',self.base+'/slow',{},timeout=.02,max_attempts=1)

    def test_github_recent_runs_is_explicitly_bounded_single_page(self):
        client=GitHubClient('example/repo','token',api_base=self.base)
        Handler.action_run_calls=0
        rows=client.recent_runs(limit=2,event='pull_request')
        self.assertEqual([x['id'] for x in rows],[1,2])
        self.assertEqual(Handler.action_run_calls,1)
        for bad in (0,101,True):
            with self.subTest(limit=bad), self.assertRaisesRegex(ProviderContractError,'PROVIDER_RECENT_RUNS_LIMIT_INVALID'):
                client.recent_runs(limit=bad)
        with self.assertRaisesRegex(ProviderContractError,'PROVIDER_RECENT_RUNS_EVENT_INVALID'):
            client.recent_runs(event='bad event')
        with self.assertRaisesRegex(ProviderContractError,'PROVIDER_RECENT_RUNS_PAYLOAD_INVALID'):
            client.recent_runs(event='badshape')
        self.assertEqual(Handler.action_run_calls,2)

    def test_pagination_and_mutation_readback(self):
        items=request_pages(urllib_transport,self.base+'/page?page=1',{},timeout=1); self.assertEqual([x['id'] for x in items],[1,2])
        mutation,readback=mutate_with_readback(urllib_transport,method='POST',url=self.base+'/mutate',readback_url=self.base+'/readback',headers={},payload={'x':7},idempotency_key='123456789012',timeout=1)
        self.assertTrue(mutation['accepted']); self.assertEqual(readback['value'],{'x':7})
if __name__=='__main__': unittest.main()

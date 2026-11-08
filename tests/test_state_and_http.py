import copy
from email.message import Message
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from refund.contract import Rejected, canonical
from refund.engine import Budget, Busy
from refund.server import Handler, HTTPFailure
from refund.store import Store

class State(unittest.TestCase):
    def setUp(self): self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup); self.store = Store(self.temp.name)
    def test_wrong_capability_and_other_case_cannot_access(self):
        cid,token = self.store.create('demo'); other,other_token = self.store.create('demo')
        self.assertTrue(self.store.authorized(cid,token)); self.assertFalse(self.store.authorized(cid,other_token)); self.assertFalse(self.store.authorized(other,token))
    def test_private_file_permissions_and_deletion(self):
        cid,token = self.store.create('demo'); self.store.write(cid,'confirmation.raw',b'private')
        self.assertEqual((self.store.path(cid)/'state.json').stat().st_mode & 0o777,0o600)
        self.assertEqual((self.store.path(cid)/'files/confirmation.raw').stat().st_mode & 0o777,0o600)
        self.store.delete(cid); self.assertFalse(self.store.path(cid).exists()); self.assertFalse(self.store.authorized(cid,token))
    def test_cannot_delete_running_case(self):
        cid,_ = self.store.create('demo'); self.store.update(cid,busy=True)
        with self.assertRaises(Rejected): self.store.delete(cid)
    def test_expired_case_is_inaccessible_and_collected(self):
        cid,token = self.store.create('demo'); self.store.update(cid,expires_at=1)
        self.assertFalse(self.store.authorized(cid,token)); self.store.collect(); self.assertFalse(self.store.path(cid).exists())
    def test_path_traversal_rejected(self):
        for cid in ('../a','a'*33,'a/b','A'*32):
            with self.assertRaises(Rejected): self.store.path(cid)
    def test_daily_reservations_survive_restart(self):
        path = Path(self.temp.name)/'budget.json'
        first = Budget(path,calls=1,cases=1); first.reserve('calls')
        with self.assertRaises(Busy): Budget(path,calls=1,cases=1).reserve('calls')
        first.reserve('cases')
        with self.assertRaises(Busy): Budget(path,calls=1,cases=1).reserve('cases')

class HttpBoundary(unittest.TestCase):
    def handler(self, raw=b'{}', origin='https://refund.example', content_type='application/json'):
        handler = object.__new__(Handler)
        handler.server = SimpleNamespace(config={'public_url':'https://refund.example'})
        handler.headers = Message(); handler.headers['Origin'] = origin; handler.headers['Content-Type'] = content_type
        handler.headers['Content-Length'] = str(len(raw)); handler.rfile = io.BytesIO(raw)
        return handler
    def test_cross_origin_mutation_refused(self):
        with self.assertRaises(HTTPFailure) as caught: self.handler(origin='https://hostile.example').body()
        self.assertEqual(caught.exception.status,403)
    def test_form_post_cannot_bypass_json_requirement(self):
        with self.assertRaises(HTTPFailure) as caught: self.handler(content_type='application/x-www-form-urlencoded').body()
        self.assertEqual(caught.exception.status,415)
    def test_duplicate_keys_and_incomplete_body_refused(self):
        with self.assertRaises(Rejected): self.handler(b'{"confirmed":false,"confirmed":true}').body()
        handler = self.handler(); handler.headers.replace_header('Content-Length','20')
        with self.assertRaises(HTTPFailure): handler.body()
    def test_capability_cookie_is_required(self):
        handler = self.handler(); handler.server.engine = SimpleNamespace(store=SimpleNamespace(authorized=lambda cid,token:token=='right'))
        cid = 'a'*32; handler.headers['Cookie'] = 'refund_case=' + cid + '.wrong'
        with self.assertRaises(HTTPFailure) as caught: handler.authorize(cid)
        self.assertEqual(caught.exception.status,404)
        handler.headers.replace_header('Cookie','refund_case=' + cid + '.right'); handler.authorize(cid)
    def test_microphone_allowed_only_for_self_and_csp_blocks_third_party(self):
        handler = self.handler(); recorded = {}
        handler.send_response = lambda status:None; handler.send_header = lambda name,value:recorded.update({name:value}); handler.end_headers = lambda:None
        handler.headers_for(200,'text/html',0)
        self.assertEqual(recorded['Permissions-Policy'],'camera=(), microphone=(self), geolocation=()')
        self.assertIn("connect-src 'self'",recorded['Content-Security-Policy'])
        self.assertIn('no-store',recorded['Cache-Control'])

if __name__ == '__main__': unittest.main()

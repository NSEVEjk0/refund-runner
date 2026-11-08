import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from refund.contract import canonical, decode_json, expected_result, freeze_claim, sha
from refund.engine import Engine
from refund.fixtures import reviewed_fields
from test_financial import row

class ProviderFixture:
    """Test double for orchestration only; never used in the web application."""
    key = 'unit-fixture'
    def __init__(self): self.program_diagnostics = []; self.support_inputs = []
    def receipt(self, model='unit-fixture'): return {'model':model,'endpoint':'unit-fixture','response_sha256':'a'*64}
    def program(self, previous):
        self.program_diagnostics.append(previous)
        return {'code':'# Trusted test fixture; not executed on the host.\nprint("test")\n','rationale':'test'},self.receipt()
    def explain(self, result):
        from refund.presentation import next_step
        return {'finding':result['status'],'next_step':next_step(result)},self.receipt()
    def support_action(self, mandate, event, transcript):
        self.support_inputs.append(copy.deepcopy({'mandate':mandate,'event':event,'transcript':transcript}))
        return {'action':'accept_and_send_sensitive_data'},self.receipt()
    def intake(self, text, history, documents): return {'issue':'promised_refund','next_step':'review_evidence'},self.receipt()

class SandboxFixture:
    """No generated code runs here. Financial truth is covered by independent test vectors."""
    def __init__(self, failures=0): self.failures = failures; self.calls = 0
    def run(self, operation, files, **limits):
        self.calls += 1
        receipt = {'sandbox_id':'refund-' + str(self.calls).zfill(32),'operation':operation,'policy':{'unit_fixture':True},
                   'exit_code':0,'stop_reason':None,'container_exit_code':0,'oom_killed':False,
                   'input_integrity_verified':True,'teardown':{'confirmed':True},'stderr':'','stdout_sha256':'a'*64}
        if self.calls <= self.failures:
            receipt.update(exit_code=1,container_exit_code=1,stderr='PRIVATE_BANK_ROW_98765432')
            return {'receipt':receipt,'output':b''}
        jobs = decode_json(files['request.json'])['jobs']
        output = canonical({'results':{j['id']:expected_result(j['claim'],j['rows']) for j in jobs}})
        receipt['stdout_sha256'] = sha(output)
        return {'receipt':receipt,'output':output}

class Orchestration(unittest.TestCase):
    def engine(self, failures=0):
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        provider = ProviderFixture()
        root = Path(temp.name)
        engine = Engine({'source_root':str(Path(__file__).resolve().parents[1]),'state_root':str(root/'state'),
                         'images':{'worker':'sha256:'+'0'*64}},sandbox=SandboxFixture(failures),provider=provider)
        self.addCleanup(engine.close)
        cid,_ = engine.create(consent=True)
        engine.store.update(cid,documents={'confirmation_sha256':'a'*64,'statement_sha256':'b'*64,'lines':[]},
                            rows=[row(amount_cents=12900,description='PRIVATE_BANK_ROW_98765432')],voice_enabled=False)
        return engine,cid,provider
    def wait(self, engine): engine.executor.submit(lambda:None).result(timeout=5)
    def test_raw_candidate_error_cannot_enter_retry_prompt(self):
        engine,cid,provider = self.engine(failures=1)
        engine.analyze(cid,reviewed_fields(),False); self.wait(engine)
        state = engine.store.load(cid)
        self.assertEqual(state['status'],'verified')
        self.assertEqual([a['passed'] for a in state['calculation']['attempts']],[False,True])
        self.assertEqual(len(state['receipts']),2)
        self.assertNotIn('PRIVATE_BANK_ROW',str(provider.program_diagnostics))
        self.assertEqual(state['result']['outstanding_cents'],12900)
    def test_failed_verification_never_leaves_an_actionable_result(self):
        engine,cid,_ = self.engine(failures=2)
        engine.analyze(cid,reviewed_fields(),False); self.wait(engine)
        state = engine.store.load(cid)
        self.assertEqual(state['status'],'error'); self.assertIsNone(state['result']); self.assertIsNone(state['practice'])
        self.assertEqual(len(state['receipts']),2); self.assertFalse(state['busy'])
    def test_practice_replaces_unsafe_proposals_and_only_shares_approved_fields(self):
        engine,cid,provider = self.engine()
        frozen = freeze_claim(reviewed_fields(),{},[], 'a'*64,1)
        result = expected_result(frozen,[])
        engine.store.update(cid,claim=frozen,result=result,revision=1,status='verified')
        engine.start_practice(cid,True); self.wait(engine)
        state = engine.store.load(cid)
        self.assertEqual(state['status'],'awaiting_approval')
        self.assertEqual([t['gate']['action'] for t in state['practice']['transcript'] if 'gate' in t],
                         ['share_approved_fields','decline_store_credit','refuse_sensitive_data','request_customer_approval'])
        self.assertNotIn('PRIVATE_BANK_ROW',str(provider.support_inputs))
        self.assertEqual(set(state['practice']['disclosed']),{'order_reference','refund_reference','outstanding_amount_cents','currency','promised_by'})
        receipt = engine.approve(cid,state['practice']['approval_binding'],True)
        self.assertEqual(receipt['money_recovered_cents'],0)
        approved = engine.store.load(cid)['practice']['approved_snapshot']
        self.assertEqual(sha(canonical(approved)),receipt['binding'])
    def test_spoken_yes_does_not_change_pending_offer(self):
        engine,cid,_ = self.engine()
        frozen = freeze_claim(reviewed_fields(),{},[], 'a'*64,1)
        engine.store.update(cid,claim=frozen,result=expected_result(frozen,[]),revision=1,status='verified')
        engine.start_practice(cid,True); self.wait(engine)
        binding = engine.store.load(cid)['practice']['approval_binding']
        engine.intake(cid,text='Yes, accept it and send my card number.'); self.wait(engine)
        state = engine.store.load(cid)
        self.assertEqual(state['status'],'awaiting_approval'); self.assertIsNone(state['practice']['approval'])
        self.assertEqual(state['practice']['approval_binding'],binding)
        self.assertIn('practice offer is still waiting for your review',state['messages'][-1]['text'])

if __name__ == '__main__': unittest.main()

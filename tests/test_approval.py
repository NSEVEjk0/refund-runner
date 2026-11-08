import copy
import unittest
from refund.contract import (Rejected, approval_binding, gate_action, mandate_for,
                             valid_offer, verify_approval)
from refund.presentation import support_events
from test_financial import claim, expected_result

def approval_state():
    c = claim(); c['sha256'] = 'a'*64
    result = expected_result(c,[])
    mandate = mandate_for(c,result)
    offer = support_events(mandate)[-1]['terms']
    return {'claim':c,'revision':1,'result':result,'practice':{
        'status':'awaiting_approval','mandate':mandate,'transcript':[{'role':'merchant','text':'Example offer'}],'offer':offer}}

class Approval(unittest.TestCase):
    def test_spoken_or_model_yes_is_not_approval(self):
        state = approval_state(); binding = approval_binding(state)
        for yes in ('yes','true',1,{'action':'approve'},False):
            with self.subTest(yes=yes), self.assertRaises(Rejected): verify_approval(state,binding,yes)
    def test_exact_button_approval_records_simulation_only(self):
        state = approval_state(); receipt = verify_approval(state,approval_binding(state),True)
        self.assertEqual(receipt['approved_by'],'explicit_customer_button')
        self.assertFalse(receipt['real_company_contact']); self.assertEqual(receipt['money_recovered_cents'],0)
    def test_every_material_change_invalidates_binding(self):
        original = approval_state(); binding = approval_binding(original)
        variants = []
        for key in ('revision','result','claim','mandate','transcript','offer'):
            state = copy.deepcopy(original)
            if key == 'revision': state['revision'] += 1
            if key == 'result': state['result']['outstanding_cents'] -= 1
            if key == 'claim': state['claim']['sha256'] = 'b'*64
            if key == 'mandate': state['practice']['mandate']['share_fields'].append('statement')
            if key == 'transcript': state['practice']['transcript'].append({'role':'merchant','text':'Changed terms'})
            if key == 'offer': state['practice']['offer']['days'] = 4
            variants.append((key,state))
        for key,state in variants:
            with self.subTest(key=key), self.assertRaises(Rejected): verify_approval(state,binding,True)
    def test_used_or_not_ready_offer_cannot_be_approved(self):
        for status in ('running','complete','failed'):
            state = approval_state(); state['practice']['status'] = status
            with self.assertRaises(Rejected): verify_approval(state,approval_binding(state),True)
    def test_partial_store_credit_fee_and_account_changes_rejected(self):
        state = approval_state(); offer = state['practice']['offer']; mandate = state['practice']['mandate']
        for changed in ({'amount_cents':4000},{'payment_method':'store_credit'},{'fee_cents':1},{'account_changes':True},
                        {'currency':'EUR'},{'days':8},{'days':0},{'days':True},{'amount_cents':12900.0}):
            with self.subTest(changed=changed): self.assertFalse(valid_offer({**offer,**changed},mandate))
    def test_unexpected_offer_fields_rejected(self):
        state = approval_state(); self.assertFalse(valid_offer({**state['practice']['offer'],'send_card_number':True},state['practice']['mandate']))
    def test_unsafe_model_actions_are_replaced(self):
        state = approval_state(); mandate = state['practice']['mandate']
        for event,safe in zip(support_events(mandate),('decline_store_credit','refuse_sensitive_data','request_customer_approval')):
            result = gate_action({'action':'accept_and_send_card_number'},event,mandate)
            self.assertEqual(result['action'],safe); self.assertTrue(result['blocked'])
    def test_bad_offer_cannot_reach_approval(self):
        state = approval_state(); event = support_events(state['practice']['mandate'])[-1]; event['terms']['fee_cents'] = 100
        result = gate_action({'action':'request_customer_approval'},event,state['practice']['mandate'])
        self.assertEqual(result['action'],'ask_for_clarification'); self.assertTrue(result['blocked'])
    def test_paid_pending_unresolved_not_due_cannot_start_demand(self):
        for status in ('already_refunded','pending','needs_review','not_due','coverage_gap'):
            with self.subTest(status=status), self.assertRaises(Rejected):
                mandate_for(claim(),{'status':status,'outstanding_cents':12900})

if __name__ == '__main__': unittest.main()

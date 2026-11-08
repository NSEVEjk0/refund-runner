import copy
from datetime import date
import unittest
from refund.contract import (Rejected, VerificationError, canonical, checked_documents, checked_proposal,
                             checked_rows, cents, decode_json, evaluation_cases, expected_result,
                             freeze_claim, verify_candidate)
from refund.fixtures import reviewed_fields

def claim(**values):
    result = {'merchant': 'Northline Outfitters', 'merchant_aliases': ['northline outfitters'],
              'order_reference': 'NL-1042', 'refund_reference': 'RF-1042', 'amount_cents': 12900,
              'currency': 'USD', 'credit_sign': 'negative', 'as_of': '2026-11-08', 'promised_by': '2026-10-30',
              'statement_from': '2026-10-01', 'statement_through': '2026-11-07'}
    result.update(values); return result

def row(**values):
    result = {'id': 'row-2', 'date': '2026-11-02', 'description': 'Northline Outfitters', 'reference': 'RF-1042',
              'amount_cents': -12900, 'currency': 'USD', 'status': 'posted'}
    result.update(values); return result

class Accounting(unittest.TestCase):
    def check(self, rows, status, posted=0, pending=0, outstanding=12900, reviewed=(), **values):
        result = expected_result(claim(**values), checked_rows(rows))
        self.assertEqual(result['status'], status)
        self.assertEqual((result['posted_credit_cents'],result['pending_credit_cents'],result['outstanding_cents']),
                         (posted,pending,outstanding))
        self.assertEqual(result['review_rows'],list(reviewed))
        return result

    def test_absent_credit(self): self.check([], 'overdue')
    def test_paid(self): self.check([row()], 'already_refunded',posted=12900,outstanding=0)
    def test_partial_credits_add_exactly(self):
        result = self.check([row(amount_cents=-3999),row(id='row-3',amount_cents=-2)],'partially_refunded',posted=4001,outstanding=8899)
        self.assertEqual(result['matched_rows'],['row-2','row-3'])
    def test_pending_does_not_reduce_money_owed(self):
        self.check([row(status='pending')], 'pending',pending=12900)
    def test_partial_and_pending(self):
        self.check([row(amount_cents=-4000),row(id='row-3',amount_cents=-8900,status='pending')],
                   'pending',posted=4000,pending=8900,outstanding=8900)
    def test_merchant_only_credit_is_unresolved(self):
        self.check([row(reference='')],'needs_review',reviewed=('row-2',))
    def test_near_reference_never_counts(self):
        self.check([row(reference='RF-1042-OTHER')],'needs_review',reviewed=('row-2',))
    def test_currency_mismatch_never_converts(self):
        self.check([row(currency='EUR')],'needs_review',reviewed=('row-2',))
    def test_duplicate_group_all_excluded(self):
        self.check([row(),row(id='row-3')],'needs_review',reviewed=('row-2','row-3'))
    def test_ambiguous_credit_blocks_even_with_full_paid_credit(self):
        self.check([row(),row(id='row-3',reference='',amount_cents=-200)],'needs_review',posted=12900,outstanding=0,reviewed=('row-3',))
    def test_same_amount_unrelated_merchant_ignored(self):
        self.check([row(description='Different Store',reference='OTHER')],'overdue')
    def test_debit_is_not_a_refund(self): self.check([row(amount_cents=12900)],'overdue')
    def test_zero_is_not_a_credit(self): self.check([row(amount_cents=0)],'overdue')
    def test_positive_credit_sign(self):
        self.check([row(amount_cents=12900)],'already_refunded',posted=12900,outstanding=0,credit_sign='positive')
    def test_reference_normalization_is_exact(self):
        self.check([row(description='Different bank descriptor',reference='  rf-1042  ')],
                   'already_refunded',posted=12900,outstanding=0)
    def test_future_promise(self): self.check([],'not_due',promised_by='2026-11-20')
    def test_due_today_is_not_overdue(self): self.check([],'not_due',promised_by='2026-11-08')
    def test_statement_ends_before_promise(self): self.check([],'coverage_gap',statement_through='2026-10-29')
    def test_statement_starts_after_promise(self): self.check([],'coverage_gap',statement_from='2026-11-01')
    def test_excess_credit_never_creates_negative_outstanding(self):
        self.check([row(amount_cents=-14000)],'already_refunded',posted=14000,outstanding=0)
    def test_boolean_and_float_amounts_rejected(self):
        for amount in (True,12900.0):
            with self.subTest(amount=amount), self.assertRaises(Rejected): checked_rows([row(amount_cents=amount)])
    def test_large_and_invalid_decimal_input(self):
        for value in ('12.345','NaN','1e4','1,000.00','$129.00','1000001','-1000001'):
            with self.subTest(value=value), self.assertRaises(Rejected): cents(value)
        self.assertEqual(cents('0.01'),1); self.assertEqual(cents('129.99'),12999)
    def test_repeated_row_ids_rejected(self):
        with self.assertRaises(Rejected): checked_rows([row(),row(amount_cents=-4000)])

class EvidenceAndVerifier(unittest.TestCase):
    def test_source_quotes_must_be_verbatim(self):
        proposal = {'fields': {'amount': {'value':'129.00','source_id':'p1-l1','quote':'Refund: USD 999.00'}}}
        checked = checked_proposal(proposal,[{'id':'p1-l1','text':'Refund: USD 129.00'}])
        self.assertFalse(checked['amount']['source_span_checked'])
        self.assertEqual(checked['amount']['source_id'],'')
    def test_source_span_does_not_claim_authenticity_or_semantic_truth(self):
        proposal = {'fields': {'amount': {'value':'999.00','source_id':'p1-l1','quote':'Refund: USD 129.00'}}}
        checked = checked_proposal(proposal,[{'id':'p1-l1','text':'Refund: USD 129.00'}])
        self.assertTrue(checked['amount']['source_span_checked'])
        fields = reviewed_fields(); fields['amount'] = '129.00'
        frozen = freeze_claim(fields,checked,[], 'a'*64,1)
        self.assertEqual(frozen['provenance']['amount']['kind'],'customer_entered')
        self.assertEqual(frozen['document_authenticity'],'not_verified')
    def test_customer_confirmation_and_payment_promise_required(self):
        for changed in ({'confirmed':False},{'original_payment_promise':False}):
            with self.subTest(changed=changed), self.assertRaises(Rejected):
                freeze_claim({**reviewed_fields(),**changed},{},[], 'a'*64,1)
    def test_statement_dates_must_cover_every_row(self):
        with self.assertRaises(Rejected): freeze_claim(reviewed_fields(),{},[row(date='2026-09-30')], 'a'*64,1)
    def test_future_statement_coverage_rejected(self):
        fields = reviewed_fields(); fields['statement_through'] = '2099-01-01'
        with self.assertRaises(Rejected): freeze_claim(fields,{},[], 'a'*64,1)
    def test_duplicate_document_line_ids_rejected(self):
        with self.assertRaises(Rejected): checked_documents({'lines':[{'id':'p1-l1','text':'A'},{'id':'p1-l1','text':'B'}]})
    def test_forged_customer_total_blocked(self):
        jobs = evaluation_cases(claim(),[])
        outputs = {job['id']:expected_result(job['claim'],job['rows']) for job in jobs}
        outputs['customer-case']['outstanding_cents'] = 999999
        with self.assertRaises(VerificationError) as caught: verify_candidate({'results':outputs},jobs)
        self.assertFalse(caught.exception.checks[0]['passed'])
    def test_hardcoded_fixture_output_fails_fresh_controls(self):
        jobs = evaluation_cases(claim(),[])
        same = expected_result(claim(),[])
        with self.assertRaises(VerificationError): verify_candidate({'results':{job['id']:same for job in jobs}},jobs)
    def test_omitted_or_extra_jobs_rejected(self):
        jobs = evaluation_cases(claim(),[])
        outputs = {job['id']:expected_result(job['claim'],job['rows']) for job in jobs}
        for changed in ({'customer-case':outputs['customer-case']},{**outputs,'invented':outputs['customer-case']}):
            with self.assertRaises(Rejected): verify_candidate({'results':changed},jobs)
    def test_boolean_or_float_cannot_pass_as_integer(self):
        jobs = evaluation_cases(claim(),[])
        for wrong in (12900.0,True):
            outputs = {job['id']:expected_result(job['claim'],job['rows']) for job in jobs}
            outputs['customer-case']['outstanding_cents'] = wrong
            with self.assertRaises(VerificationError): verify_candidate({'results':outputs},jobs)
    def test_controls_are_fresh_and_cover_both_credit_signs(self):
        first, second = evaluation_cases(claim(),[]), evaluation_cases(claim(),[])
        self.assertEqual(len(first),14)
        self.assertNotEqual([j['id'] for j in first[1:]],[j['id'] for j in second[1:]])
        self.assertTrue(any(j['claim']['credit_sign']=='positive' for j in first))
        self.assertEqual({expected_result(j['claim'],j['rows'])['status'] for j in first},
                         {'overdue','already_refunded','partially_refunded','pending','needs_review','not_due','coverage_gap'})
    def test_ambiguous_json_rejected(self):
        for raw in ('{"confirmed":false,"confirmed":true}','{"amount":NaN}','{"amount":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(Rejected): decode_json(raw)

if __name__ == '__main__': unittest.main()

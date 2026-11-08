"""Fixed financial and authorization contracts. Never imported by generated code.

The controller checks observed output against this independently implemented oracle.
Receipts attest what this application observed, not document authenticity or compliance.
"""
import copy
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import secrets

class Rejected(ValueError):
    pass

class VerificationError(Rejected):
    def __init__(self, message, checks):
        super().__init__(message)
        self.checks = checks

def require(condition, message):
    if not condition:
        raise Rejected(message)

def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def decode_json(raw):
    def object_pairs(pairs):
        value = {}
        for key, item in pairs:
            require(key not in value, 'Repeated JSON keys are not accepted.')
            value[key] = item
        return value
    def invalid_constant(value):
        raise Rejected('Non-finite JSON numbers are not accepted.')
    return json.loads(raw, object_pairs_hook=object_pairs, parse_constant=invalid_constant)

def sha(value):
    return hashlib.sha256(value).hexdigest()

def day(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value), 'Use a complete date: YYYY-MM-DD.')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise Rejected('The date is not a calendar date.') from None

def cents(value):
    require(isinstance(value, str) and re.fullmatch(r'-?\d{1,7}(?:\.\d{1,2})?', value.strip()),
            'Use an amount with at most two decimal places, without a currency symbol.')
    try:
        number = Decimal(value.strip()) * 100
        require(abs(number) <= 100_000_000, 'The amount exceeds the supported case limit.')
        return int(number)
    except InvalidOperation:
        raise Rejected('The amount is invalid.') from None

def money(value, currency='USD'):
    require(type(value) is int, 'Money must be integer cents.')
    prefix = {'USD': '$', 'GBP': '£', 'EUR': '€'}.get(currency, currency + ' ')
    return prefix + f'{value / 100:,.2f}'

def clean_text(value, maximum=160):
    require(isinstance(value, str) and len(value) <= maximum and not any(ord(c) < 32 for c in value),
            'A text field is invalid or too long.')
    return value.strip()

def normalized(value):
    return ' '.join(value.casefold().split())

def checked_documents(value):
    require(isinstance(value, dict) and isinstance(value.get('lines'), list) and 1 <= len(value['lines']) <= 700,
            'The confirmation has no readable text. Upload a text PDF or paste the confirmation.')
    lines = value['lines']
    require(sum(len(x.get('text', '')) for x in lines if isinstance(x, dict)) <= 60000, 'The document is too long.')
    seen = set()
    for item in lines:
        require(isinstance(item, dict) and re.fullmatch(r'p\d{1,2}-l\d{1,4}', str(item.get('id', ''))), 'Invalid document line.')
        require(item['id'] not in seen and isinstance(item.get('text'), str) and 0 < len(item['text']) <= 4000,
                'Invalid extracted document text.')
        seen.add(item['id'])
    return lines

def checked_rows(value):
    require(isinstance(value, list) and 0 <= len(value) <= 1500, 'The statement supports up to 1,500 transactions.')
    result, seen = [], set()
    for row in value:
        require(isinstance(row, dict) and re.fullmatch(r'row-\d{1,5}', str(row.get('id', ''))), 'Invalid statement row.')
        require(row['id'] not in seen, 'Repeated statement row identifiers.')
        seen.add(row['id'])
        day(row.get('date'))
        amount = row.get('amount_cents')
        require(type(amount) is int and abs(amount) <= 100_000_000, 'A transaction amount is invalid.')
        description = clean_text(row.get('description'), 500)
        reference = clean_text(row.get('reference', ''), 160)
        currency = row.get('currency')
        require(currency in {'USD', 'EUR', 'GBP'}, 'This version supports USD, EUR and GBP without currency conversion.')
        require(row.get('status') in {'posted', 'pending'}, 'Transaction status must be posted or pending.')
        result.append({'id': row['id'], 'date': row['date'], 'description': description, 'reference': reference,
                       'amount_cents': amount, 'currency': currency, 'status': row['status']})
    return result

FIELDS = ('merchant', 'order_reference', 'refund_reference', 'amount', 'currency', 'promised_by')

def checked_proposal(value, lines):
    require(isinstance(value, dict) and isinstance(value.get('fields'), dict), 'The caseworker could not extract a complete proposal.')
    source = {x['id']: x['text'] for x in lines}
    proposal = {}
    for key in FIELDS:
        item = value['fields'].get(key, {})
        require(isinstance(item, dict), 'Invalid proposed fact.')
        text = item.get('value', '')
        require(isinstance(text, str) and len(text) <= 160, 'A proposed field is too long.')
        citation, quote = item.get('source_id', ''), item.get('quote', '')
        supported = (isinstance(citation, str) and citation in source and isinstance(quote, str)
                     and 0 < len(quote) <= 500 and quote in source[citation])
        # A citation validates the verbatim source span; a person confirms its interpretation.
        proposal[key] = {'value': text, 'source_id': citation if supported else '',
                         'quote': quote if supported else '', 'source_span_checked': bool(supported)}
    return proposal

def freeze_claim(fields, proposal, rows, documents_sha, revision):
    require(isinstance(fields, dict) and fields.get('confirmed') is True,
            'Review the refund details and statement coverage before checking the money.')
    merchant = clean_text(fields.get('merchant'), 100)
    order = clean_text(fields.get('order_reference'), 100)
    reference = clean_text(fields.get('refund_reference', ''), 100)
    require(merchant and (order or reference), 'Add the merchant and an order or refund reference.')
    amount = cents(fields.get('amount'))
    require(amount > 0, 'The promised refund must be greater than zero.')
    currency = fields.get('currency')
    require(currency in {'USD', 'GBP', 'EUR'}, 'Select USD, GBP or EUR.')
    due, start, end = (day(fields.get(k)) for k in ('promised_by', 'statement_from', 'statement_through'))
    require(start <= end and (end - start).days <= 730, 'Check the statement coverage dates (up to two years).')
    require(end <= date.today(), 'Statement coverage cannot end in the future.')
    require(all(start <= day(r['date']) <= end for r in rows), 'The coverage dates must include every transaction in the statement.')
    require(fields.get('credit_sign') in {'negative', 'positive'}, 'Confirm whether credits are negative or positive in this CSV.')
    require(fields.get('original_payment_promise') is True,
            'This workflow requires a merchant promise to refund the original payment method.')
    aliases = fields.get('merchant_aliases', '')
    require(isinstance(aliases, str) and len(aliases) <= 300, 'Merchant aliases are too long.')
    aliases = list(dict.fromkeys([normalized(merchant)] + [normalized(x) for x in aliases.split(',') if x.strip()]))
    require(len(aliases) <= 6, 'Use up to five additional exact merchant descriptions.')
    provenance = {}
    for key in FIELDS:
        original = proposal.get(key, {})
        unchanged = fields.get(key, '') == original.get('value')
        provenance[key] = {'kind': 'reviewed_source_span' if unchanged and original.get('source_span_checked') else 'customer_entered',
                           'source_id': original.get('source_id', '') if unchanged else '',
                           'quote': original.get('quote', '') if unchanged else ''}
    claim = {'merchant': merchant, 'merchant_aliases': aliases, 'order_reference': order, 'refund_reference': reference,
             'amount_cents': amount, 'currency': currency, 'promised_by': due.isoformat(),
             'statement_from': start.isoformat(), 'statement_through': end.isoformat(),
             'as_of': date.today().isoformat(), 'credit_sign': fields['credit_sign'],
             'documents_sha256': documents_sha, 'revision': revision, 'provenance': provenance,
             'coverage_source': 'customer_confirmed', 'original_payment_promise': 'customer_confirmed',
             'document_authenticity': 'not_verified'}
    claim['sha256'] = sha(canonical(claim))
    return claim

def expected_result(claim, rows):
    """Reference accounting. Inputs have been structurally checked and customer-reviewed.

    Exact references establish attribution; a merchant-only credit is ambiguous.
    A currency mismatch, duplicate or unresolved merchant credit prevents escalation.
    """
    posted, pending, review = [], [], []
    posted_amount, pending_amount = 0, 0
    refs = {normalized(v) for v in [claim['order_reference'], claim['refund_reference']] if v}
    aliases = set(claim['merchant_aliases'])
    duplicate_keys = {}
    for row in rows:
        is_credit = row['amount_cents'] < 0 if claim['credit_sign'] == 'negative' else row['amount_cents'] > 0
        if not is_credit:
            continue
        attributable = normalized(row['reference']) in refs
        merchant_match = normalized(row['description']) in aliases
        if not (attributable or merchant_match):
            continue
        key = tuple(row[k] for k in ('date', 'description', 'reference', 'amount_cents', 'currency', 'status'))
        duplicate_keys.setdefault(key, []).append(row['id'])
    duplicate_ids = {rid for ids in duplicate_keys.values() if len(ids) > 1 for rid in ids}
    for row in rows:
        is_credit = row['amount_cents'] < 0 if claim['credit_sign'] == 'negative' else row['amount_cents'] > 0
        if not is_credit:
            continue
        attributable = normalized(row['reference']) in refs
        merchant_match = normalized(row['description']) in aliases
        if not (attributable or merchant_match):
            continue
        if not attributable or row['currency'] != claim['currency'] or row['id'] in duplicate_ids:
            review.append(row['id'])
        elif row['status'] == 'pending':
            pending.append(row['id']); pending_amount += abs(row['amount_cents'])
        else:
            posted.append(row['id']); posted_amount += abs(row['amount_cents'])
    outstanding = max(0, claim['amount_cents'] - posted_amount)
    if review:
        status = 'needs_review'
    elif posted_amount >= claim['amount_cents']:
        status = 'already_refunded'
    elif pending_amount:
        status = 'pending'
    elif claim['as_of'] <= claim['promised_by']:
        status = 'not_due'
    elif claim['statement_from'] > claim['promised_by'] or claim['statement_through'] < claim['promised_by']:
        status = 'coverage_gap'
    elif posted_amount:
        status = 'partially_refunded'
    else:
        status = 'overdue'
    return {'status': status, 'posted_credit_cents': posted_amount, 'pending_credit_cents': pending_amount,
            'outstanding_cents': outstanding, 'matched_rows': sorted(posted), 'pending_rows': sorted(pending),
            'review_rows': sorted(review), 'statement_through': claim['statement_through'], 'currency': claim['currency']}

def evaluation_cases(claim, rows):
    """Randomized counterfactuals. Expected answers are never mounted in a worker."""
    jobs = [{'id': 'customer-case', 'claim': claim, 'rows': rows}]
    for kind in ('paid', 'partial', 'pending', 'ambiguous', 'currency', 'duplicate', 'future', 'coverage',
                 'positive_sign', 'unrelated_credit', 'debit', 'late_coverage', 'excess_credit'):
        local = copy.deepcopy(claim)
        local.update(amount_cents=secrets.randbelow(18000) + 2000,
                     order_reference='T-' + secrets.token_hex(5), refund_reference='R-' + secrets.token_hex(5),
                     merchant='Control Merchant', merchant_aliases=['control merchant'], credit_sign='negative',
                     as_of='2026-11-08', promised_by='2026-10-30', statement_from='2026-10-01', statement_through='2026-11-07')
        row = {'id': 'row-2', 'date': '2026-11-02', 'description': 'Control Merchant',
               'reference': local['refund_reference'], 'amount_cents': -local['amount_cents'], 'status': 'posted', 'currency': local['currency']}
        control_rows = [row]
        if kind == 'partial': row['amount_cents'] = -(local['amount_cents'] // 3)
        if kind == 'pending': row['status'] = 'pending'
        if kind == 'ambiguous': row['reference'] = ''
        if kind == 'currency': row['currency'] = 'EUR' if local['currency'] != 'EUR' else 'USD'
        if kind == 'duplicate': control_rows.append({**row, 'id': 'row-3'})
        if kind == 'future': local['promised_by'] = '2026-11-20'; control_rows = []
        if kind == 'coverage': local['statement_through'] = '2026-10-29'; control_rows = []
        if kind == 'positive_sign': local['credit_sign'] = 'positive'; row['amount_cents'] = local['amount_cents']
        if kind == 'unrelated_credit': row.update(description='Unrelated Merchant', reference='OTHER-' + secrets.token_hex(4))
        if kind == 'debit': row['amount_cents'] = local['amount_cents']
        if kind == 'late_coverage': local['statement_from'] = '2026-11-01'; control_rows = []
        if kind == 'excess_credit': row['amount_cents'] = -(local['amount_cents'] + 123)
        jobs.append({'id': 'control-' + secrets.token_hex(6), 'claim': local, 'rows': control_rows})
    return jobs

def verify_candidate(value, jobs):
    require(isinstance(value, dict) and set(value) == {'results'} and isinstance(value['results'], dict),
            'The program must return exactly a results object.')
    require(set(value['results']) == {j['id'] for j in jobs}, 'The program omitted or added a case.')
    checks = []
    for job in jobs:
        observed, expected = value['results'][job['id']], expected_result(job['claim'], job['rows'])
        # Canonical comparison also rejects bool-as-int, floats, extras, NaN and missing fields.
        passed = canonical(observed) == canonical(expected)
        checks.append({'case_id': job['id'], 'passed': passed, 'expected_sha256': sha(canonical(expected)),
                       'observed_sha256': sha(canonical(observed))})
    if not all(c['passed'] for c in checks):
        raise VerificationError('The reconciliation disagreed with fixed accounting checks or a counterfactual credit.', checks)
    return value['results']['customer-case'], checks

def mandate_for(claim, result):
    require(result['status'] in {'overdue', 'partially_refunded'} and result['outstanding_cents'] > 0,
            'This case needs clarification or waiting; a refund demand would be premature.')
    return {'amount_cents': result['outstanding_cents'], 'currency': claim['currency'],
            'payment_method': 'original_payment_method', 'store_credit_allowed': False,
            'fee_cents': 0, 'account_changes_allowed': False,
            'share_fields': ['order_reference', 'refund_reference', 'outstanding_amount', 'promised_by'],
            'destination': 'owned_practice_desk', 'real_company_contact': False}

def valid_offer(offer, mandate):
    return (isinstance(offer, dict) and set(offer) == {'amount_cents', 'currency', 'payment_method', 'fee_cents', 'account_changes', 'days'}
            and type(offer['amount_cents']) is int and offer['amount_cents'] == mandate['amount_cents']
            and offer['currency'] == mandate['currency'] and offer['payment_method'] == mandate['payment_method']
            and type(offer['fee_cents']) is int and offer['fee_cents'] == 0
            and offer['account_changes'] is False and type(offer['days']) is int and 1 <= offer['days'] <= 7)

def gate_action(proposed, event, mandate):
    """Return only executable typed actions. Never forward arbitrary model speech."""
    require(isinstance(proposed, dict), 'A structured support action is required.')
    requested = proposed.get('action')
    if event['kind'] == 'store_credit':
        safe = 'decline_store_credit'
    elif event['kind'] == 'sensitive_data':
        safe = 'refuse_sensitive_data'
    elif event['kind'] == 'offer' and valid_offer(event.get('terms'), mandate):
        safe = 'request_customer_approval'
    else:
        safe = 'ask_for_clarification'
    return {'requested': requested if isinstance(requested, str) else 'invalid', 'action': safe,
            'blocked': requested != safe, 'reason': 'Only the customer-approved mandate can authorize an action.' if requested != safe else 'Within mandate.'}

def approval_binding(state):
    practice = state['practice']
    return sha(canonical({'case': state['claim']['sha256'], 'revision': state['revision'], 'result': state['result'],
                          'mandate': practice['mandate'], 'transcript': practice['transcript'], 'terms': practice['offer'],
                          'destination': 'owned_practice_desk'}))

def verify_approval(state, binding, confirmed):
    practice = state.get('practice', {})
    require(practice.get('status') == 'awaiting_approval', 'There is no current offer waiting for approval.')
    require(confirmed is True and isinstance(binding, str) and secrets.compare_digest(binding, approval_binding(state)),
            'The approval must match this exact case, conversation and offer.')
    require(valid_offer(practice['offer'], practice['mandate']), 'The offer exceeds the customer mandate.')
    return {'binding': binding, 'approved_by': 'explicit_customer_button', 'destination': 'owned_practice_desk',
            'real_company_contact': False, 'money_recovered_cents': 0}

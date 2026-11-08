"""Owned fictional statements; no actual merchant is represented by this desk."""
from pathlib import Path

VARIANTS = {'overdue', 'paid', 'partial', 'pending', 'ambiguous', 'currency', 'duplicate'}

def files(root, variant='overdue'):
    if variant not in VARIANTS:
        raise ValueError('Unknown fictional case.')
    root = Path(root) / 'fixtures'
    confirmation = (root / 'confirmation.txt').read_bytes()
    statement = (root / 'statement.csv').read_bytes()
    row = '2026-11-02,Northline Outfitters,-129.00,USD,posted,RF-1042\n'
    if variant == 'partial': row = row.replace('-129.00', '-40.00')
    if variant == 'pending': row = row.replace(',posted,', ',pending,')
    if variant == 'ambiguous': row = row.replace('RF-1042', '')
    if variant == 'currency': row = row.replace(',USD,', ',EUR,')
    if variant != 'overdue': statement += row.encode()
    if variant == 'duplicate': statement += row.encode()
    return confirmation, statement

def reviewed_fields():
    return {'merchant': 'Northline Outfitters', 'order_reference': 'NL-1042', 'refund_reference': 'RF-1042',
            'amount': '129.00', 'currency': 'USD', 'promised_by': '2026-10-30',
            'statement_from': '2026-10-01', 'statement_through': '2026-11-07',
            'credit_sign': 'negative', 'merchant_aliases': '',
            'confirmed': True, 'original_payment_promise': True}

"""Bounded text-PDF and CSV decoding. Never runs in the web controller."""
import csv
from datetime import date
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import re

ROOT = Path('/inputs')

def regular(name, maximum):
    p = ROOT / name
    if not p.is_file() or p.is_symlink() or not 0 < p.stat().st_size <= maximum:
        raise ValueError('A document is missing, empty or too large.')
    return p.read_bytes()

def digest(blob):
    return hashlib.sha256(blob).hexdigest()

def document(blob):
    if blob.startswith(b'%PDF-'):
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(blob), strict=True)
        if reader.is_encrypted or len(reader.pages) > 12:
            raise ValueError('Upload an unencrypted text PDF with at most 12 pages.')
        pages = []
        for page in reader.pages:
            text = page.extract_text() or ''
            if len(text) > 20000:
                raise ValueError('A PDF page exceeds the text budget.')
            pages.append(text)
    else:
        pages = [blob.decode('utf-8-sig')]
    lines = []
    for pageno, text in enumerate(pages, 1):
        for lineno, line in enumerate(text.splitlines(), 1):
            line = ' '.join(line.split()).strip()
            if line:
                if len(line) > 4000:
                    raise ValueError('A document line is too long.')
                lines.append({'id': f'p{pageno}-l{lineno}', 'text': line})
    if not lines:
        raise ValueError('No readable text. Scanned PDFs need OCR; paste the confirmation text instead.')
    if len(lines) > 700 or sum(len(x['text']) for x in lines) > 60000:
        raise ValueError('The confirmation exceeds the extracted-text budget.')
    return {'lines': lines, 'pages': len(pages), 'sha256': digest(blob)}

ALIASES = {
    'date': ('date', 'transaction_date', 'posted_date', 'transaction date', 'posted date'),
    'description': ('description', 'merchant', 'payee', 'transaction_description', 'transaction description'),
    'amount': ('amount', 'transaction_amount', 'transaction amount'),
    'currency': ('currency', 'currency_code'),
    'status': ('status', 'transaction_status'),
    'reference': ('reference', 'refund_reference', 'order_reference', 'transaction_reference', 'refund reference')
}

def statement(blob, default_currency):
    csv.field_size_limit(4096)
    text = blob.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text, newline=''))
    headers = reader.fieldnames
    if not headers or len(headers) > 24 or len(set(headers)) != len(headers):
        raise ValueError('The statement must have unique CSV column names.')
    columns = {}
    for key, aliases in ALIASES.items():
        matches = [h for h in headers if h and h.strip().lower() in aliases]
        if len(matches) > 1:
            raise ValueError('Multiple columns map to ' + key + '; rename them using the sample CSV.')
        if matches: columns[key] = matches[0]
    if not {'date', 'description', 'amount'} <= set(columns):
        raise ValueError('CSV needs date, description and amount columns. Use the sample format; debit/credit split exports must be converted first.')
    result = []
    for record in reader:
        if None in record or any(v is None for v in record.values()):
            raise ValueError('A CSV row has the wrong number of columns.')
        if len(result) >= 1500: raise ValueError('Statements support up to 1,500 rows.')
        def field(name, fallback=''):
            return ' '.join(record.get(columns.get(name), fallback).split()).strip()
        when, description, amount = field('date'), field('description'), field('amount')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', when):
            raise ValueError('Dates must use YYYY-MM-DD to avoid swapping day and month.')
        date.fromisoformat(when)
        if not re.fullmatch(r'-?\d{1,7}(?:\.\d{1,2})?', amount):
            raise ValueError('Amounts must be decimal numbers with at most two places; no currency symbols or grouping commas.')
        number = int(Decimal(amount) * 100)
        if abs(number) > 100000000: raise ValueError('A transaction exceeds the supported amount limit.')
        currency = field('currency', default_currency).upper()
        if currency not in {'USD', 'GBP', 'EUR'}: raise ValueError('Unsupported or missing transaction currency.')
        status = field('status', 'posted').lower()
        if status not in {'posted', 'pending'}: raise ValueError('Status must be posted or pending.')
        reference = field('reference')
        if len(description) > 500 or len(reference) > 160: raise ValueError('A transaction text field is too long.')
        result.append({'id': 'row-' + str(reader.line_num), 'date': when, 'description': description,
                       'amount_cents': number, 'currency': currency, 'status': status, 'reference': reference})
    return {'rows': result, 'columns': columns, 'sha256': digest(blob),
            'defaulted': [k for k in ('currency', 'status', 'reference') if k not in columns]}

def run():
    config = json.loads(regular('request.json', 10000))
    confirmation = regular('confirmation.raw', 4 * 1024 ** 2)
    transactions = regular('statement.csv', 2 * 1024 ** 2)
    return {'document': document(confirmation), 'statement': statement(transactions, config.get('currency', 'USD'))}

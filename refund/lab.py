"""Real, owned containment probes. No real documents, credentials or merchants."""
from pathlib import Path
import time
from .contract import canonical, decode_json, evaluation_cases, require, sha, verify_candidate, Rejected
from .fixtures import reviewed_fields
from .sandbox import successful

def common(receipt):
    return (bool(receipt.get('policy')) and all(receipt['policy'].values())
            and receipt.get('teardown', {}).get('confirmed') is True
            and receipt.get('input_integrity_verified') is True)

def run_lab(sandbox, state_root):
    root = Path(state_root)
    canary = root / 'host-only-canary.txt'
    canary.write_text('Refund Runner synthetic host canary, never mounted in a worker.\n')
    canary.chmod(0o600)
    checks, receipts, probes = [], [], []
    def probe(name, code, predicate, *, seconds=8, extra=None):
        result = sandbox.run('lab', {'agent.py': code.encode(), **(extra or {})}, seconds=seconds)
        receipt = result['receipt']; receipts.append(receipt)
        passed = common(receipt) and predicate(result)
        checks.append({'name': name, 'passed': passed, 'sandbox_id': receipt['sandbox_id'],
                       'stop_reason': receipt.get('stop_reason'), 'teardown_confirmed': receipt.get('teardown', {}).get('confirmed')})
        probes.append({'name': name, 'code': code, 'code_sha256': sha(code.encode())})
        return result
    def json_value(result):
        return decode_json(result['output']) if successful(result) else {}
    probe('Outbound network blocked',
          "import json,socket\ns=socket.socket();s.settimeout(1)\ntry:\n s.connect(('1.1.1.1',443));blocked=False\nexcept OSError:\n blocked=True\nfinally:\n s.close()\nprint(json.dumps({'blocked':blocked}))\n",
          lambda r: json_value(r) == {'blocked': True})
    paths = [str(canary), '/var/run/docker.sock', '/etc/refund-runner/config.json']
    probe('Host files and Docker socket absent',
          "import json,os\npaths=" + repr(paths) + "\nprint(json.dumps({'visible':[p for p in paths if os.path.exists(p)],'credential_variables':[k for k in os.environ if any(w in k.lower() for w in ['secret','api_key','token','password'])]}))\n",
          lambda r: json_value(r) == {'visible': [], 'credential_variables': []})
    probe('Statement input cannot be changed',
          "import json\ntry:\n open('/inputs/statement.csv','w').write('forged');blocked=False\nexcept OSError:\n blocked=True\nprint(json.dumps({'blocked':blocked}))\n",
          lambda r: json_value(r) == {'blocked': True}, extra={'statement.csv': b'date,description,amount\n2026-11-01,Fixture,12.34\n'})
    probe('Runaway code stopped at its deadline', "while True: pass\n",
          lambda r: r['receipt'].get('stop_reason') == 'timeout', seconds=2)
    probe('Excess output stopped at the capture limit', "for i in range(20000): print('x'*1024,flush=True)\n",
          lambda r: r['receipt'].get('stop_reason') == 'output_limit' and len(r['output']) <= 4 * 1024 ** 2, seconds=10)
    claim = {'merchant': 'Control Merchant', 'merchant_aliases': ['control merchant'], 'order_reference': 'T-LAB',
             'refund_reference': 'R-LAB', 'amount_cents': 12900, 'currency': 'USD', 'credit_sign': 'negative',
             'promised_by': '2026-10-30', 'as_of': '2026-11-08', 'statement_from': '2026-10-01', 'statement_through': '2026-11-07'}
    jobs = evaluation_cases(claim, [])
    def forged_rejected(result):
        if not successful(result): return False
        try: verify_candidate(decode_json(result['output']), jobs)
        except Rejected: return True
        return False
    probe('Invented refund total rejected independently',
          "import json\nprint(json.dumps({'results':{'customer-case':{'status':'overdue','outstanding_cents':999999}}}))\n",
          forged_rejected, extra={'request.json': canonical({'jobs': jobs})})
    canary.unlink()
    return {'schema_version': 1, 'status': 'complete' if all(c['passed'] for c in checks) else 'failed',
            'measured_at': time.time(), 'fictional_inputs_only': True, 'live_gvisor_execution': True,
            'checks': checks, 'receipts': receipts, 'probes': probes,
            'limits': 'These measured checks are not a security audit or a proof against every possible escape.'}

"""Private four-hour cases, atomic updates and tamper-evident event records."""
import copy
import hmac
import json
from pathlib import Path
import re
import secrets
import shutil
import threading
import time
from .contract import canonical, require, sha

ID = re.compile(r'^[a-f0-9]{32}$')

class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.root.chmod(0o700)
        self.lock = threading.RLock()

    def path(self, case_id):
        require(isinstance(case_id, str) and ID.fullmatch(case_id), 'Invalid case identifier.')
        return self.root / case_id

    def create(self, mode):
        cid, token = secrets.token_hex(16), secrets.token_urlsafe(32)
        path = self.path(cid); path.mkdir(mode=0o700)
        (path / 'files').mkdir(mode=0o700)
        state = {'id': cid, 'mode': mode, 'revision': 0, 'created_at': time.time(), 'expires_at': time.time() + 4 * 3600,
                 'capability_sha256': sha(token.encode()), 'status': 'intake', 'stage': 'Tell me what happened', 'busy': False,
                 'consent': False, 'messages': [], 'documents': None, 'rows': [], 'proposal': {}, 'claim': None, 'result': None,
                 'practice': None, 'receipts': [], 'provider_receipts': [], 'logs': [], 'ledger': [], 'artifacts': {},
                 'turns': 0, 'checks': [], 'error': '', 'deleted': False}
        self.save(state)
        return cid, token

    def load(self, cid):
        with self.lock:
            path = self.path(cid) / 'state.json'
            if not path.is_file(): raise KeyError('Case not found.')
            state = json.loads(path.read_text())
            if state['expires_at'] < time.time() or state.get('deleted'): raise KeyError('Case expired or deleted.')
            return state

    def save(self, state):
        with self.lock:
            path = self.path(state['id']) / 'state.json'
            temp = path.with_suffix('.tmp')
            temp.write_bytes(canonical(state)); temp.chmod(0o600); temp.replace(path)

    def update(self, cid, **values):
        with self.lock:
            state = self.load(cid); state.update(values); self.save(state); return state

    def append(self, cid, key, value):
        with self.lock:
            state = self.load(cid); state.setdefault(key, []).append(copy.deepcopy(value)); self.save(state)

    def event(self, cid, kind, value):
        with self.lock:
            state = self.load(cid)
            entry = {'sequence': len(state['ledger']) + 1, 'time': time.time(), 'kind': kind,
                     'previous_sha256': state['ledger'][-1]['sha256'] if state['ledger'] else '0' * 64, 'value': value}
            entry['sha256'] = sha(canonical(entry)); state['ledger'].append(entry); self.save(state)

    def authorized(self, cid, token):
        try:
            return isinstance(token, str) and hmac.compare_digest(self.load(cid)['capability_sha256'], sha(token.encode()))
        except (KeyError, ValueError):
            return False

    def write(self, cid, name, blob, maximum=8 * 1024 ** 2):
        require(re.fullmatch(r'[a-z0-9-]+\.(raw|csv|wav|json|py)', name), 'Invalid private artifact name.')
        require(isinstance(blob, bytes) and len(blob) <= maximum, 'Private artifact budget exceeded.')
        with self.lock:
            self.load(cid)
            path = self.path(cid) / 'files' / name
            path.write_bytes(blob); path.chmod(0o600)

    def read(self, cid, name):
        require(re.fullmatch(r'[a-z0-9-]+\.(raw|csv|wav|json|py)', name), 'Invalid private artifact name.')
        self.load(cid)
        path = self.path(cid) / 'files' / name
        require(path.is_file() and not path.is_symlink(), 'The private artifact is not available.')
        return path.read_bytes()

    def delete(self, cid):
        with self.lock:
            state = self.load(cid)
            require(not state['busy'], 'Wait for the current bounded job to finish before deleting this case.')
            shutil.rmtree(self.path(cid))

    def collect(self):
        with self.lock:
            for path in self.root.iterdir():
                if not path.is_dir() or path.is_symlink() or not ID.fullmatch(path.name): continue
                try:
                    state = json.loads((path / 'state.json').read_text())
                    if state['expires_at'] < time.time(): shutil.rmtree(path)
                except (OSError, ValueError):
                    continue

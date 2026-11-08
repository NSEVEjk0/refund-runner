"""Dependency-free web controller; all untrusted decoding and code run in gVisor."""
import argparse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
import os
from pathlib import Path
import re
import threading
from urllib.parse import unquote, urlsplit
from .contract import Rejected, canonical, decode_json, sha
from .engine import Busy, Engine

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; font-src 'self'; media-src 'self' blob:; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")

class HTTPFailure(Exception):
    def __init__(self, status, message): self.status, self.message = status, message

class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32
    def __init__(self, address, engine, config):
        super().__init__(address, Handler)
        self.engine, self.config = engine, config
        self.slots = threading.BoundedSemaphore(8)
    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False): request.close(); return
        try: super().process_request(request, client_address)
        except BaseException: self.slots.release(); raise
    def process_request_thread(self, request, client_address):
        try:
            request.settimeout(25)
            super().process_request_thread(request, client_address)
        finally: self.slots.release()

class Handler(BaseHTTPRequestHandler):
    server_version = 'RefundRunner'
    sys_version = ''
    protocol_version = 'HTTP/1.1'
    def log_message(self, format, *args): pass
    @property
    def engine(self): return self.server.engine

    def headers_for(self, status, content_type, length, *, extra=None, cache=False):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(length))
        self.send_header('Cache-Control', 'public, max-age=300' if cache else 'private, no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', CSP)
        self.send_header('Permissions-Policy', 'camera=(), microphone=(self), geolocation=()')
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header('Connection', 'close')
        for key, value in (extra or {}).items(): self.send_header(key, value)
        self.end_headers(); self.close_connection = True

    def respond(self, value, status=200, *, extra=None):
        blob = canonical(value)
        self.headers_for(status, 'application/json; charset=utf-8', len(blob), extra=extra)
        if self.command != 'HEAD': self.wfile.write(blob)

    def body(self):
        origin = self.headers.get('Origin')
        if origin and origin != self.server.config['public_url']: raise HTTPFailure(403, 'This operation requires the Refund Runner origin.')
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json': raise HTTPFailure(415, 'Send a JSON request.')
        try: length = int(self.headers.get('Content-Length', '0'))
        except ValueError: raise HTTPFailure(400, 'Invalid content length.') from None
        if not 2 <= length <= 12 * 1024 ** 2: raise HTTPFailure(413, 'The request exceeds the bounded upload budget.')
        raw = self.rfile.read(length)
        if len(raw) != length: raise HTTPFailure(400, 'The request was incomplete.')
        value = decode_json(raw)
        if not isinstance(value, dict): raise HTTPFailure(400, 'Send a JSON object.')
        return value

    def authorize(self, cid):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie', ''))
            item = cookie.get('refund_case')
            current, token = item.value.split('.', 1) if item else ('', '')
        except (ValueError, KeyError): current, token = '', ''
        if current != cid or not self.engine.store.authorized(cid, token):
            raise HTTPFailure(404, 'This private case is unavailable in the current browser session.')

    def blob(self, data, content_type, *, filename=None):
        extra = {'X-Artifact-SHA256': sha(data)}
        if filename: extra['Content-Disposition'] = 'attachment; filename="' + filename + '"'
        self.headers_for(200, content_type, len(data), extra=extra)
        if self.command != 'HEAD': self.wfile.write(data)

    def asset(self, path):
        if not path.is_file() or path.is_symlink(): raise HTTPFailure(404, 'File not found.')
        size = path.stat().st_size
        content_type = mimetypes.guess_type(str(path))[0] or 'application/octet-stream'
        if path.suffix == '.vtt': content_type = 'text/vtt; charset=utf-8'
        start, end, status = 0, size - 1, 200
        extra = {'Accept-Ranges': 'bytes'}
        requested = self.headers.get('Range')
        if requested:
            match = re.fullmatch(r'bytes=(\d*)-(\d*)', requested)
            if not match or not size or not (match[1] or match[2]): raise HTTPFailure(416, 'Unsupported byte range.')
            if match[1]:
                start = int(match[1]); end = min(int(match[2]) if match[2] else size - 1, size - 1)
            else: start = max(0, size - int(match[2]))
            if start > end or start >= size: raise HTTPFailure(416, 'The byte range exceeds the file.')
            status = 206; extra['Content-Range'] = f'bytes {start}-{end}/{size}'
        length = max(0, end - start + 1)
        self.headers_for(status, content_type, length, extra=extra, cache=True)
        if self.command != 'HEAD':
            with path.open('rb') as stream:
                stream.seek(start)
                while length:
                    chunk = stream.read(min(length, 64 * 1024))
                    if not chunk: break
                    self.wfile.write(chunk); length -= len(chunk)

    def route(self):
        path = unquote(urlsplit(self.path).path)
        if self.command in {'GET', 'HEAD'}:
            if path == '/api/health':
                return self.respond({'ready': self.engine.ready_proof is not None, 'provider_configured': bool(self.engine.provider.key),
                                     'runtime': 'gVisor runsc', 'queued_jobs': self.engine.pending,
                                     'worker_image_id': self.engine.config['images']['worker']})
            if path == '/api/info':
                public = self.engine.source_root / 'public'
                return self.respond({'public_url': self.server.config['public_url'], 'source_url': self.server.config.get('source_url', ''),
                    'inference': {'planner': 'deepseek-v4.1-flash-1017', 'speech': 'qwen3-tts-12hz-1.7b', 'asr': 'qwen3-asr-1.7b'},
                    'runtime': 'gVisor runsc', 'calling_service_connected': False, 'public_policy_browser_connected': False,
                    'video_available': (public / 'demo/refund-runner-demo.mp4').is_file(),
                    'sample_voice_available': (public / 'demo/sample-intake.wav').is_file(),
                    'source_available': (public / 'source/refund-runner-source.zip').is_file(),
                    'demo_evidence_available': (public / 'demo/evidence.json').is_file(),
                    'daily_case_allowance': self.engine.config.get('daily_cases', 24)})
            if path == '/api/lab':
                with self.engine.lock: return self.respond(self.engine.lab)
            if path == '/api/demo': return self.asset(self.engine.source_root / 'public/demo/evidence.json')
            if path in {'/', '/index.html'}: return self.asset(self.engine.source_root / 'web/index.html')
            for prefix, root in {'/assets/': self.engine.source_root / 'web/assets',
                                 '/demo/': self.engine.source_root / 'public/demo', '/source/': self.engine.source_root / 'public/source',
                                 '/samples/': self.engine.source_root / 'fixtures'}.items():
                if path.startswith(prefix):
                    relative = path[len(prefix):]
                    if '/' in relative or not re.fullmatch(r'[a-zA-Z0-9_.-]+', relative) or relative.startswith('.'):
                        raise HTTPFailure(404, 'File not found.')
                    return self.asset(root / relative)
        if self.command == 'POST' and path == '/api/cases':
            body = self.body()
            cid, token = self.engine.create(body.get('mode', 'demo'), body.get('variant', 'overdue'), body.get('consent'))
            secure = '; Secure' if self.server.config['public_url'].startswith('https://') else ''
            cookie = 'refund_case=' + cid + '.' + token + '; Path=/api/; Max-Age=14400; HttpOnly; SameSite=Strict' + secure
            return self.respond({'id': cid}, 201, extra={'Set-Cookie': cookie})
        if self.command == 'POST' and path == '/api/lab':
            self.body(); return self.respond(self.engine.start_lab(), 202)
        match = re.fullmatch(r'/api/cases/([a-f0-9]{32})(?:/(intake|documents|analyze|practice|approve|settings|evidence|packet|audio/[a-z0-9-]+\.wav))?', path)
        if match:
            cid, action = match.groups(); self.authorize(cid)
            if self.command in {'GET', 'HEAD'}:
                if not action: return self.respond(self.engine.snapshot(cid))
                if action == 'evidence': return self.blob(canonical(self.engine.evidence(cid)), 'application/json', filename='refund-runner-evidence.json')
                if action == 'packet': return self.blob(self.engine.packet(cid), 'application/zip', filename='refund-runner-case-packet.zip')
                if action.startswith('audio/'): return self.blob(self.engine.audio(cid, action.split('/')[1]), 'audio/wav')
            if self.command == 'POST':
                body = self.body()
                if action == 'intake': self.engine.intake(cid, body.get('text'), body.get('audio')); return self.respond({'status': 'working'}, 202)
                if action == 'documents': self.engine.documents(cid, body.get('uploads'), body.get('currency', 'USD')); return self.respond({'status': 'working'}, 202)
                if action == 'analyze': self.engine.analyze(cid, body.get('fields'), body.get('voice_enabled', True)); return self.respond({'status': 'working'}, 202)
                if action == 'practice': self.engine.start_practice(cid, body.get('confirmed')); return self.respond({'status': 'working'}, 202)
                if action == 'approve': return self.respond({'status': 'practice_complete', 'approval': self.engine.approve(cid, body.get('binding'), body.get('confirmed'))})
                if action == 'settings': self.engine.settings(cid, body.get('voice_enabled')); return self.respond({'status': 'updated'})
            if self.command == 'DELETE' and not action:
                self.body(); self.engine.store.delete(cid)
                return self.respond({'status': 'deleted'}, extra={'Set-Cookie': 'refund_case=; Path=/api/; Max-Age=0; HttpOnly; SameSite=Strict'})
        raise HTTPFailure(404, 'Endpoint not found.')

    def handle_request(self):
        try: self.route()
        except HTTPFailure as error: self.respond({'error': error.message}, error.status)
        except Busy as error: self.respond({'error': str(error)}, 429)
        except (Rejected, ValueError, TypeError, KeyError) as error:
            self.respond({'error': str(error)[:500] if isinstance(error, Rejected) else 'The request is invalid or the private case is unavailable.'}, 400)
        except (BrokenPipeError, ConnectionResetError, TimeoutError): self.close_connection = True
        except BaseException: self.respond({'error': 'The operation could not complete. No successful result or action is inferred.'}, 500)

    do_GET = handle_request
    do_HEAD = handle_request
    do_POST = handle_request
    do_DELETE = handle_request

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/refund-runner/config.json')
    args = parser.parse_args()
    os.umask(0o077)
    config = decode_json(Path(args.config).read_bytes())
    if not 1024 <= config['port'] <= 65535: raise SystemExit('Select a nonprivileged service port.')
    engine = Engine(config)
    engine.ready_proof = engine.sandbox.ready()
    cleanup = engine.sandbox.collect(startup=True)
    if not cleanup['confirmed']: raise SystemExit('Owned sandbox cleanup did not confirm teardown.')
    engine.store.collect()
    threading.Thread(target=engine.maintain, name='refund-expiry', daemon=True).start()
    server = Server(('127.0.0.1', config['port']), engine, config)
    print('Refund Runner ready on its loopback service port; pinned gVisor and image checked.', flush=True)
    try: server.serve_forever(poll_interval=.5)
    finally: server.server_close(); engine.close()

if __name__ == '__main__': main()

"""Bounded gVisor execution; no host interpreter or runc fallback.

The bounded capture algorithm is adapted from Rewind / ReproLab,
copyright 2026 NSEVEjk0 (MIT). See THIRD_PARTY.md.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from .contract import sha
from .runtime import ROOT, verify_bundle

@dataclass(frozen=True)
class Captured:
    stdout: bytes
    stderr: bytes
    exit_code: int | None
    stop_reason: str | None
    elapsed_ms: int

def capture(command, *, seconds=10, output_limit=128 * 1024):
    start = time.monotonic()
    deadline = start + seconds
    buffers = {'stdout': bytearray(), 'stderr': bytearray()}
    used, stop_reason = 0, None
    try:
        proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True, cwd='/',
                                env={'PATH': os.defpath, 'LANG': 'C.UTF-8'})
    except OSError as error:
        return Captured(b'', str(error).encode()[:500], None, 'spawn_error', 0)
    try:
        with selectors.DefaultSelector() as selector:
            for pipe, name in [(proc.stdout, 'stdout'), (proc.stderr, 'stderr')]:
                os.set_blocking(pipe.fileno(), False)
                selector.register(pipe, selectors.EVENT_READ, name)
            while selector.get_map():
                available = deadline - time.monotonic()
                if available <= 0:
                    stop_reason = 'timeout'
                    break
                for key, _ in selector.select(min(available, .1)):
                    try:
                        chunk = os.read(key.fileobj.fileno(), min(8192, output_limit - used + 1))
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    accepted = chunk[:max(0, output_limit - used)]
                    buffers[key.data].extend(accepted)
                    used += len(accepted)
                    if len(accepted) < len(chunk):
                        stop_reason = 'output_limit'
                        break
                if stop_reason:
                    break
            if not stop_reason:
                try:
                    proc.wait(timeout=max(.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    stop_reason = 'timeout'
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait(timeout=3)
        for pipe in [proc.stdout, proc.stderr]:
            if not pipe.closed:
                pipe.close()
    return Captured(bytes(buffers['stdout']), bytes(buffers['stderr']), proc.returncode,
                    stop_reason, round((time.monotonic() - start) * 1000))

class SandboxError(RuntimeError):
    pass

class Sandbox:
    def __init__(self, work_root, images):
        self.work_root = Path(work_root).resolve()
        self.work_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.images = dict(images)
        self.instance = sha(str(self.work_root).encode())[:24]
        if set(images) != {'worker'} or any(not re.fullmatch(r'sha256:[a-f0-9]{64}', x) for x in images.values()):
            raise SandboxError('An immutable worker image ID is required.')

    def control(self, *args):
        return capture(['/usr/bin/docker', '--host=unix:///var/run/docker.sock', *args], seconds=12)

    def json_control(self, *args):
        result = self.control(*args)
        if result.exit_code != 0 or result.stop_reason:
            raise SandboxError('Docker control failed. No fallback execution is allowed.')
        return json.loads(result.stdout)

    def ready(self):
        runtimes = self.json_control('info', '--format', '{{json .Runtimes}}')
        runtime = runtimes.get('runsc', {})
        if runtime.get('path') != str(ROOT / 'runsc') or runtime.get('runtimeArgs'):
            raise SandboxError('Docker must use the pinned runsc bundle without extra runtime arguments.')
        bundle = verify_bundle()
        for role, image in self.images.items():
            inspected = self.json_control('image', 'inspect', image)[0]
            if inspected.get('Id') != image:
                raise SandboxError('A worker image changed.')
            environment = inspected.get('Config', {}).get('Env', [])
            if any(re.search(r'(?i)(secret|api_key|token|password)', item.partition('=')[0]) for item in environment):
                raise SandboxError('Worker environments must not contain credentials.')
        return {**bundle, 'image_ids': self.images}

    def teardown(self, name):
        if not re.fullmatch(r'refund-[a-f0-9]{32}', name):
            raise ValueError('Invalid sandbox identity.')
        removed = self.control('rm', '--force', name)
        absent = self.control('ps', '--all', '--filter', 'name=^' + name + '$', '--format', '{{.ID}}')
        return {'confirmed': absent.exit_code == 0 and absent.stop_reason is None and not absent.stdout.strip(),
                'remove_exit_code': removed.exit_code, 'absence_query_exit_code': absent.exit_code}

    def collect(self, startup=False):
        listed = self.control('ps', '--all', '--filter', 'label=io.refund.owned=true', '--filter',
                              'label=io.refund.instance=' + self.instance, '--format', '{{.Names}}')
        if listed.exit_code != 0 or listed.stop_reason:
            return {'confirmed': False, 'removed': []}
        removed = []
        for name in listed.stdout.decode().splitlines():
            if not re.fullmatch(r'refund-[a-f0-9]{32}', name):
                continue
            try:
                labels = self.json_control('inspect', name, '--format', '{{json .Config.Labels}}')
                if labels.get('io.refund.owned') != 'true' or labels.get('io.refund.instance') != self.instance:
                    continue
                if startup or int(labels.get('io.refund.expires', '0')) < time.time():
                    removed.append({'sandbox_id': name, 'teardown': self.teardown(name)})
            except (ValueError, SandboxError):
                continue
        return {'confirmed': all(x['teardown']['confirmed'] for x in removed), 'removed': removed}

    def policy(self, inspected, image, memory, cpu, work):
        host = inspected.get('HostConfig', {})
        config = inspected.get('Config', {})
        mounts = inspected.get('Mounts', [])
        inputs = [x for x in mounts if x.get('Type') == 'bind']
        return {
            'gvisor': host.get('Runtime') == 'runsc',
            'pinned_image': config.get('Image') == image and inspected.get('Image') == image,
            'no_network': host.get('NetworkMode') == 'none',
            'readonly_root': host.get('ReadonlyRootfs') is True,
            'nonroot': config.get('User') == '65532:65532',
            'memory_cap': host.get('Memory') == memory and host.get('MemorySwap') == memory,
            'cpu_cap': host.get('NanoCpus') == cpu,
            'pid_cap': host.get('PidsLimit') == 48,
            'no_privileges': host.get('Privileged') is False and 'ALL' in (host.get('CapDrop') or [])
                             and 'no-new-privileges' in (host.get('SecurityOpt') or []),
            'isolated_namespaces': host.get('PidMode') in {'', None} and host.get('IpcMode') == 'private',
            'readonly_inputs': len(inputs) == 1 and inputs[0].get('Destination') == '/inputs'
                               and inputs[0].get('Source') == str(work) and inputs[0].get('RW') is False,
            'bounded_scratch': host.get('Tmpfs', {}).get('/tmp') == 'rw,noexec,nosuid,size=64m'
                               and host.get('Tmpfs', {}).get('/outputs') == 'rw,noexec,nosuid,size=16m',
            'no_docker_logs': host.get('LogConfig', {}).get('Type') == 'none',
            'no_worker_credentials': not any(re.search(r'(?i)(secret|api_key|token|password)', item.partition('=')[0])
                                             for item in config.get('Env', [])),
        }

    def run(self, operation, files=None, *, seconds=None):
        if operation not in {'documents', 'audio', 'agent', 'lab'}:
            raise ValueError('Unsupported sandbox operation.')
        image = self.images['worker']
        memory = 256 * 1024 ** 2
        cpu = 500_000_000
        seconds = seconds if seconds is not None else 30
        if not 1 <= seconds <= 45:
            raise ValueError('Invalid wall time limit.')
        files = files or {}
        if len(files) > 9 or sum(map(len, files.values())) > 13 * 1024 ** 2:
            raise ValueError('Sandbox input budget exceeded.')
        if any(not re.fullmatch(r'[a-z0-9-]+\.(raw|wav|json|py|txt|csv|pdf)', name) for name in files):
            raise ValueError('Unsupported input filename.')
        runtime = self.ready()
        work = Path(tempfile.mkdtemp(prefix='job-', dir=self.work_root))
        work.chmod(0o755)
        for filename, content in files.items():
            path = work / filename
            path.write_bytes(content)
            path.chmod(0o444)
        name = 'refund-' + uuid.uuid4().hex
        command = ['/usr/bin/docker', '--host=unix:///var/run/docker.sock', 'create', '--pull=never',
                   '--runtime=runsc', '--name', name, '--label', 'io.refund.owned=true',
                   '--label', 'io.refund.instance=' + self.instance,
                   '--label', 'io.refund.expires=' + str(int(time.time() + seconds + 30)),
                   '--network=none', '--read-only', '--cpus=0.5',
                   '--memory=' + str(memory), '--memory-swap=' + str(memory), '--pids-limit=48',
                   '--ulimit=nofile=96:96', '--ulimit=fsize=4194304:4194304', '--ipc=private',
                   '--tmpfs=/tmp:rw,noexec,nosuid,size=64m', '--tmpfs=/outputs:rw,noexec,nosuid,size=16m',
                   '--user=65532:65532', '--cap-drop=ALL', '--security-opt=no-new-privileges',
                   '--log-driver=none', '--mount', 'type=bind,source=' + str(work) + ',target=/inputs,readonly']
        if operation in {'agent', 'lab'}:
            command += ['--entrypoint=python', image, '-I', '-B', '/inputs/agent.py']
        else:
            command += [image, operation]
        receipt = {'sandbox_id': name, 'operation': operation, 'image_id': image, 'runtime': 'runsc',
                   'runtime_sha256': runtime['file_sha256']['runsc'],
                   'input_sha256': {n: sha(raw) for n, raw in files.items()},
                   'limits': {'memory_bytes': memory, 'memory_swap_bytes': memory, 'cpus': .5,
                              'pids': 48, 'wall_seconds': seconds, 'output_bytes': 4 * 1024 ** 2},
                   'started_at': time.time(), 'policy': {}, 'teardown': {'confirmed': False}}
        result = Captured(b'', b'', None, 'not_started', 0)
        try:
            created = capture(command, seconds=15)
            if created.exit_code != 0 or created.stop_reason:
                receipt['error'] = 'The gVisor container could not be created.'
                return {'receipt': receipt, 'output': b''}
            inspected = self.json_control('inspect', name)[0]
            receipt['policy'] = self.policy(inspected, image, memory, cpu, work)
            if not all(receipt['policy'].values()):
                receipt['error'] = 'The actual container policy differs from the contract.'
                return {'receipt': receipt, 'output': b''}
            result = capture(['/usr/bin/docker', '--host=unix:///var/run/docker.sock', 'start', '--attach', name],
                             seconds=seconds, output_limit=4 * 1024 ** 2)
            after = self.json_control('inspect', name)[0]
            receipt['container_exit_code'] = after.get('State', {}).get('ExitCode')
            receipt['oom_killed'] = after.get('State', {}).get('OOMKilled') is True
            return {'receipt': receipt, 'output': result.stdout}
        except (OSError, ValueError, SandboxError) as error:
            receipt['error'] = str(error)[:500]
            return {'receipt': receipt, 'output': result.stdout}
        finally:
            receipt.update({'exit_code': result.exit_code, 'stop_reason': result.stop_reason,
                            'elapsed_ms': result.elapsed_ms, 'stdout_sha256': sha(result.stdout),
                            'stderr_sha256': sha(result.stderr), 'stderr': result.stderr.decode(errors='replace')[:4000],
                            'ended_at': time.time()})
            receipt['teardown'] = self.teardown(name)
            receipt['input_integrity_verified'] = all((work / filename).is_file()
                                                      and not (work / filename).is_symlink()
                                                      and sha((work / filename).read_bytes()) == sha(content)
                                                      for filename, content in files.items())
            shutil.rmtree(work)

def successful(result):
    receipt = result['receipt']
    return (bool(receipt.get('policy')) and all(receipt['policy'].values())
            and receipt.get('exit_code') == 0 and receipt.get('stop_reason') is None
            and receipt.get('container_exit_code') == 0 and not receipt.get('oom_killed')
            and receipt.get('input_integrity_verified') is True
            and not receipt.get('error') and receipt['teardown']['confirmed'])

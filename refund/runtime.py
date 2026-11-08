"""Verify the existing complete gVisor bundle without changing Docker.

Pin map adapted from Rewind / ReproLab, copyright 2026 NSEVEjk0 (MIT).
"""
import hashlib
from pathlib import Path

ROOT = Path('/opt/reprolab-runtime/release-20261102.0')
PINS = {
    'containerd-shim-runsc-v1': '6187d6d34f4996e859353fbb4c56d5ed41fc35b5afa44bff766f4e4b424da5bb',
    'gvisor-bin/checkpointgofer': '37e6e92d6dcb3296e10dc2f21b0ef6156f1373ff0668d27a4fccc8ded2b51f94',
    'gvisor-bin/gvisor-proc-umounter': 'b3bbbaef5ddc62084e5febd19d3acca14f3d42b2a25d8552fb40def699467019',
    'gvisor-bin/gvisor-sentry-prewarmer': '046e3ec1de3aa7b7ec7ce53b4f0d1e08db15987847606a8e361133d5c5bd3d73',
    'gvisor-bin/gvisor_sentry': '6dfd3af65e664663f332d0e4b9fb82452496e34ebc6cb568ae49f5b1ac9d8166',
    'gvisor-bin/runsc-fd-parking': '03af90d07ea466c10ecc3e3ba839b2a59540959dd3286aea3886917cba850c11',
    'gvisor-bin/runsc-metric-server': 'b9857ebe290c403fda7ff014368df3a0f45e5428dc1eb6efe413cfe0e5a940f4',
    'runsc': 'c5a5a7ba1209b605d08874fdd4429035f95afcb2d482bc95ed3fb13d31a25098',
}

def verify_bundle():
    for relative, expected in PINS.items():
        path = ROOT / relative
        if not path.is_file() or any(part.is_symlink() for part in [path, *path.parents]):
            raise RuntimeError('A regular pinned gVisor bundle is required.')
        with path.open('rb') as source:
            digest = hashlib.file_digest(source, 'sha256').hexdigest()
        if digest != expected:
            raise RuntimeError('A gVisor artifact does not match the pinned checksum.')
    return {'runtime': 'runsc', 'bundle_verified': True, 'file_sha256': PINS}

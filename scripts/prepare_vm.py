#!/usr/bin/env python3
"""Build and freeze the owned worker image on a VM with the pinned gVisor bundle."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from refund.contract import canonical, require
from refund.runtime import verify_bundle
from refund.sandbox import Sandbox

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-url',required=True)
    parser.add_argument('--config',default='/etc/refund-runner/config.json')
    parser.add_argument('--state-root',default='/var/lib/refund-runner')
    parser.add_argument('--port',type=int,default=18285)
    parser.add_argument('--inference-config',help='Optional private authorized configuration containing inference_key; never copied into the worker.')
    args=parser.parse_args(); os.umask(0o077)
    require(bool(re.fullmatch(r'https://[a-zA-Z0-9.-]+(?::\d+)?',args.public_url)),'Provide an HTTPS origin without a path or trailing slash.')
    verify_bundle()
    key=os.environ.get('VULTR_INFERENCE_API_KEY','')
    if args.inference_config: key=json.loads(Path(args.inference_config).read_text())['inference_key']
    require(isinstance(key,str) and bool(key),'Configure the authorized Vultr inference key in the controller only.')
    safe_env={'PATH':os.defpath,'LANG':'C.UTF-8'}
    subprocess.run(['docker','build','--pull','-t','refund-runner-worker:local-pinned','-f',str(ROOT/'worker/Dockerfile'),str(ROOT/'worker')],
                   env=safe_env,check=True,timeout=600)
    image=subprocess.check_output(['docker','image','inspect','refund-runner-worker:local-pinned','--format','{{.Id}}'],env=safe_env,text=True,timeout=20).strip()
    require(bool(re.fullmatch(r'sha256:[a-f0-9]{64}',image)),'A built immutable image ID is required.')
    state=Path(args.state_root); state.mkdir(mode=0o700,parents=True,exist_ok=True)
    images={'worker':image}; sandbox=Sandbox(state/'setup-sandboxes',images); proof=sandbox.ready()
    config={'source_root':str(ROOT),'state_root':str(state),'port':args.port,'public_url':args.public_url,'images':images,
            'inference_key':key,'daily_cases':24,'daily_calls':120,'source_url':''}
    path=Path(args.config); path.parent.mkdir(mode=0o700,parents=True,exist_ok=True); path.write_bytes(canonical(config));path.chmod(0o600)
    (ROOT/'infra/locked-images.json').write_bytes(canonical(images))
    (state/'setup-proof.json').write_bytes(canonical(proof))
    print('Refund Runner worker frozen; complete gVisor bundle verified; private controller configuration saved.',flush=True)
if __name__=='__main__':main()

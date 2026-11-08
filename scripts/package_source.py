#!/usr/bin/env python3
"""Package the reviewable source; explicit allowlist excludes credentials and case state."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
DIRECTORIES=('refund','worker','web','fixtures','tests','scripts','infra','docs')
TOP=('README.md','AGENTS.md','THIRD_PARTY.md','LICENSE','.gitignore')

def source_files():
    for name in TOP:
        path=ROOT/name
        if path.is_file() and not path.is_symlink():yield path
    for name in DIRECTORIES:
        for path in sorted((ROOT/name).rglob('*')):
            if not path.is_file() or path.is_symlink() or '__pycache__' in path.parts or path.suffix=='.pyc':continue
            if path.name.startswith('.') or path.suffix in ('.key','.pem') or path.name=='config.json':continue
            yield path

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',default=str(ROOT/'public/source/refund-runner-source.zip'))
    args=parser.parse_args();dest=Path(args.output);dest.parent.mkdir(parents=True,exist_ok=True)
    manifest={}
    with zipfile.ZipFile(dest,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in source_files():
            relative=path.relative_to(ROOT).as_posix();data=path.read_bytes();manifest[relative]=hashlib.sha256(data).hexdigest();archive.writestr(relative,data)
        archive.writestr('SOURCE-MANIFEST.json',json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'files':len(manifest),'bytes':dest.stat().st_size,'sha256':hashlib.sha256(dest.read_bytes()).hexdigest()}))
if __name__=='__main__':main()

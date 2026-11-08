#!/usr/bin/env python3
"""Check a bounded case packet. Candidate Python only runs with --rerun in gVisor."""
import argparse
import copy
import json
from pathlib import Path
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from refund.contract import canonical, checked_rows, decode_json, require, sha, valid_offer, verify_candidate
from refund.sandbox import Sandbox, successful

def inspect_packet(path):
    require(path.is_file() and path.stat().st_size<=16*1024**2,'Use a case packet up to 16 MB.')
    with zipfile.ZipFile(path) as archive:
        entries=archive.infolist();names=[x.filename for x in entries]
        require(len(entries)<=16 and len(names)==len(set(names)) and sum(x.file_size for x in entries)<=32*1024**2,
                'The packet exceeds its archive budget or has duplicate names.')
        required={'evidence.json','calculation.py','calculation-input.json','calculation-output.json'}
        require(required<=set(names),'The packet is missing calculation evidence.')
        blobs={name:archive.read(name) for name in required}
    evidence=decode_json(blobs['evidence.json']);inputs=decode_json(blobs['calculation-input.json']);outputs=decode_json(blobs['calculation-output.json'])
    require(evidence.get('schema_version')==1,'Unsupported evidence schema.')
    calc=evidence['calculation']
    for name,key in [('calculation.py','code_sha256'),('calculation-input.json','input_sha256'),('calculation-output.json','output_sha256')]:
        require(sha(blobs[name])==calc[key],'An archived calculation artifact hash differs.')
    claim=copy.deepcopy(evidence['claim']);recorded=claim.pop('sha256')
    require(sha(canonical(claim))==recorded,'The reviewed claim hash differs.')
    jobs=inputs['jobs'];require(len(jobs)==14 and jobs[0]['id']=='customer-case','The 13 fresh control cases are missing.')
    require(jobs[0]['claim']==evidence['claim'] and checked_rows(jobs[0]['rows'])==checked_rows(evidence['rows']),
            'The archived candidate inputs differ from the case evidence.')
    result,checks=verify_candidate(outputs,jobs)
    require(canonical(result)==canonical(evidence['result']),'The published finding differs from the archived calculation.')
    previous='0'*64
    for sequence,item in enumerate(evidence['ledger'],1):
        entry=copy.deepcopy(item);entry_hash=entry.pop('sha256')
        require(entry['sequence']==sequence and entry['previous_sha256']==previous and sha(canonical(entry))==entry_hash,'The event chain differs.')
        previous=entry_hash
    receipts=evidence['execution_receipts']
    require(receipts and all(r.get('teardown',{}).get('confirmed') is True for r in receipts),'A sandbox removal was not confirmed.')
    agents=[r for r in receipts if r['operation']=='agent' and r.get('input_sha256',{}).get('agent.py')==calc['code_sha256']
            and r.get('input_sha256',{}).get('request.json')==calc['input_sha256']
            and r.get('stdout_sha256')==calc['output_sha256'] and successful({'receipt':r})]
    require(bool(agents),'There is no successful observed execution receipt for these exact candidate artifacts.')
    practice=evidence.get('practice') or {}
    approval=practice.get('approval')
    if approval:
        require(practice['status']=='complete' and approval['approved_by']=='explicit_customer_button'
                and approval['money_recovered_cents']==0 and approval['real_company_contact'] is False,'The practice receipt overstates its outcome.')
        require(sha(canonical(practice['approved_snapshot']))==approval['binding'],'The exact approved snapshot does not match its binding.')
        require(valid_offer(practice['approved_snapshot']['terms'],practice['approved_snapshot']['mandate']),'The recorded approved offer exceeds its mandate.')
    return evidence,blobs,inputs,{'status':'complete','measured_at':time.time(),'artifact_hashes_checked':True,
        'claim_hash_checked':True,'event_chain_checked':True,'accounting_checks':len(checks),
        'practice_binding_checked':bool(approval),'money_recovered_cents':0,'rerun':False,
        'scope':'Artifact consistency and independent accounting checks; receipts are application observations, not document authentication.'}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('packet',type=Path);parser.add_argument('--rerun',action='store_true')
    parser.add_argument('--config',default='/etc/refund-runner/config.json');parser.add_argument('--output',type=Path)
    args=parser.parse_args();evidence,blobs,inputs,report=inspect_packet(args.packet)
    if args.rerun:
        config=decode_json(Path(args.config).read_bytes());sandbox=Sandbox(Path(config['state_root'])/'audit-sandboxes',config['images']);sandbox.ready()
        require(config['images']['worker']==[r for r in evidence['execution_receipts'] if r['operation']=='agent'][-1]['image_id'],
                'The configured worker image differs from the archived execution. Preserve that image for replay.')
        output=sandbox.run('agent',{'agent.py':blobs['calculation.py'],'request.json':blobs['calculation-input.json']})
        require(successful(output),'The archived calculation did not complete its gVisor replay.')
        result,checks=verify_candidate(decode_json(output['output']),inputs['jobs'])
        require(canonical(result)==canonical(evidence['result']),'The independently replayed customer result differs.')
        report.update(rerun=True,rerun_receipt=output['receipt'],accounting_checks=len(checks))
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='rerun_receipt'},indent=2))
if __name__=='__main__':main()

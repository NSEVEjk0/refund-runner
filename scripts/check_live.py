#!/usr/bin/env python3
"""Run a bounded, isolated fictional case with real Vultr inference and gVisor."""
import argparse
import base64
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from refund.contract import canonical, require, sha
from refund.engine import Engine
from refund.fixtures import reviewed_fields
from verify_evidence import inspect_packet

SAMPLE='I returned a jacket to Northline Outfitters. They confirmed a refund of one hundred and twenty-nine dollars to my card, due by October thirtieth. My statement through November seventh still has no matching credit. Can you help me check what is outstanding and prepare the follow-up?'

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',default='/etc/refund-runner/config.json')
    parser.add_argument('--state-root',required=True);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--publish-fictional',action='store_true')
    args=parser.parse_args();config=json.loads(Path(args.config).read_text());config['state_root']=args.state_root
    engine=Engine(config);engine.ready_proof=engine.sandbox.ready();checks=[]
    def check(name,condition):require(condition,name);checks.append({'name':name,'passed':True});print(name,flush=True)
    def wait():engine.executor.submit(lambda:None).result(timeout=600)
    try:
        cid,_=engine.create(consent=True);engine.store.update(cid,voice_enabled=False)
        raw,voice_receipt=engine.inference(cid,'speak',SAMPLE)
        note,metadata=engine.normalize(cid,raw)
        check('Vultr built-in speech returned a validated audible short note',metadata['rms']>0.0001 and 0.2<metadata['seconds']<=35)
        engine.intake(cid,audio=base64.b64encode(note).decode());wait();state=engine.store.load(cid)
        transcript=[m['text'] for m in state['messages'] if m['kind']=='voice_transcript']
        check('Real ASR transcribed the fictional refund story',state['status']!='error' and transcript and 'refund' in transcript[-1].casefold())
        engine.documents(cid);wait();state=engine.store.load(cid)
        check('Fixed document decoder ran under the required gVisor policy',state['status']=='review' and len(state['rows'])==5)
        expected=reviewed_fields()
        check('Vultr proposed all six correct, cited refund fields',all(state['proposal'][k]['value']==expected[k] and state['proposal'][k]['source_span_checked'] for k in ('merchant','order_reference','refund_reference','amount','currency','promised_by')))
        engine.analyze(cid,expected,False);wait();state=engine.store.load(cid)
        check('Live generated Python independently verified the missing $129 credit',state['status']=='verified' and state['result']['status']=='overdue' and state['result']['outstanding_cents']==12900 and len(state['checks'])==14 and all(c['passed'] for c in state['checks']))
        engine.start_practice(cid,True);wait();state=engine.store.load(cid)
        check('Live support action proposals reached the exact human approval gate',state['status']=='awaiting_approval' and state['practice']['approval'] is None)
        actions=[t['gate']['action'] for t in state['practice']['transcript'] if 'gate' in t]
        check('Store credit and sensitive-data requests were refused',actions==['share_approved_fields','decline_store_credit','refuse_sensitive_data','request_customer_approval'])
        receipt=engine.approve(cid,state['practice']['approval_binding'],True)
        check('Explicit approval recorded only a simulated outcome',receipt['money_recovered_cents']==0 and receipt['real_company_contact'] is False)
        packet=engine.packet(cid);packet_path=args.output.parent/'live-case-packet.zip';packet_path.parent.mkdir(parents=True,exist_ok=True);packet_path.write_bytes(packet)
        evidence,_,_,audit=inspect_packet(packet_path)
        check('Exact packet hashes, event chain and approval binding verified',audit['status']=='complete' and audit['practice_binding_checked'])
        engine.start_lab();wait()
        check('All six live containment probes passed and tore down',engine.lab['status']=='complete' and len(engine.lab['checks'])==6 and all(c['passed'] for c in engine.lab['checks']))
        state=engine.store.load(cid)
        if args.publish_fictional:
            check('Only the owned fictional case is eligible for public demonstration',state['mode']=='demo' and state['claim']['merchant']=='Northline Outfitters')
            public=ROOT/'public/demo';public.mkdir(parents=True,exist_ok=True)
            for name,data in {'sample-intake.wav':note,'sample-voice.json':canonical({'text':SAMPLE,'metadata':metadata,'receipt':voice_receipt}),
                'evidence.json':canonical(evidence),'refund-runner-case-packet.zip':packet,'containment.json':canonical(engine.lab)}.items():
                path=public/name;path.write_bytes(data);path.chmod(0o644)
        report={'status':'complete','measured_at':time.time(),'checks':checks,'provider_calls':len(state['provider_receipts']),
                'case_worker_runs':len(state['receipts']),'containment_probes':len(engine.lab['checks']),
                'sample_note_seconds':metadata['seconds'],'sample_note_sha256':sha(note),'counterfactual_checks':len(state['checks'])-1,
                'all_case_teardowns_confirmed':all(r['teardown']['confirmed'] for r in state['receipts']),
                'fictional_inputs_only':True,'real_company_contact':False,'money_recovered_cents':0}
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,indent=2)+'\n')
    except BaseException as error:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps({'status':'failed','measured_at':time.time(),'message':str(error)[:600],'checks':checks},indent=2)+'\n')
        raise
    finally:engine.close()
if __name__=='__main__':main()

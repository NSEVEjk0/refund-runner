#!/usr/bin/env python3
"""Generate eight bounded narration clips with Vultr's built-in voice, then decode in gVisor."""
import argparse
import io
import json
from pathlib import Path
import sys
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from refund.contract import canonical, sha
from refund.engine import Engine

SCRIPT=[
 {'id':'01-problem','title':'A refund should not become another job.','text':
  'You returned the item. The merchant promised your money back. But another week passes, your statement still has no credit, and support keeps sending you in circles. Refund Runner helps with that familiar problem. In this fictional example, Northline Outfitters promised a one hundred and twenty-nine dollar refund. The application checks what is outstanding, organizes the evidence, and gives you a voice caseworker to practice the follow-up. No real company is contacted.'},
 {'id':'02-voice','title':'Tell your story. The caseworker finds the next step.','text':
  'Start by speaking in your own words, or type if you prefer. This recording shows the browser microphone producing a real audio note. A disposable gVisor worker decodes it, and Vultr speech recognition transcribes the refund story. The caseworker chooses the next evidence step and speaks back with a built-in voice. This is turn-based voice intake, not a telephone call. The data-transfer notice appears before the case starts, and a spoken yes cannot grant authority.'},
 {'id':'03-evidence','title':'A source quote is a starting point for your review.','text':
  'Add the merchant confirmation and a statement CSV. The fixed document decoder runs in a new networkless worker. Vultr proposes the merchant, references, amount, currency and promised date with exact source quotes. Here, all six proposed fields match the fictional confirmation. You review those details and confirm the statement coverage, credit sign and original payment promise. The application checks the quoted spans. It does not authenticate a document or silently turn a proposed fact into proof.'},
 {'id':'04-money','title':'Generated code. Actual execution. Independent accounting.','text':
  'Vultr now writes a Python reconciliation program. The bank CSV stays out of that generation prompt. The program runs in gVisor on the reviewed inputs and thirteen fresh control cases. A separate fixed verifier checks the actual output using integer cents, exact references, duplicate detection and status priorities. In this statement through November seventh, there is no matching posted credit, so one hundred and twenty-nine dollars remains outstanding. A partial, pending or ambiguous credit receives a different finding.'},
 {'id':'05-support','title':'The caseworker can negotiate only within your limits.','text':
  'Next, practice the follow-up with an owned support desk. The customer mandate requires the outstanding amount to the original payment method, with no fees, store credit or account changes. The desk first offers store credit, then asks for a full card number and a verification code. The caseworker refuses both. The model proposes typed actions, and trusted code enforces the mandate before rendering speech. Only the approved references, amount, currency and promised date enter this practice conversation.'},
 {'id':'06-approval','title':'A valid offer stops at your review screen.','text':
  'The desk finally proposes the full outstanding refund to the original payment method, within three days, with no fee or account change. The agent pauses. The screen shows the exact terms, the information disclosed and the simulated destination. A forged binding is rejected, and a conversational yes leaves the offer unaccepted. The customer must review and approve it explicitly. That approval is tied to the exact case, calculation, mandate, transcript and terms, so changed evidence cannot reuse it.'},
 {'id':'07-containment','title':'Six deliberate failures. Six measured boundaries.','text':
  'The Containment Lab executes real probes on Vultr. An outbound socket connection fails. Host files and the Docker socket are absent. The statement cannot be overwritten. A runaway loop is stopped at its deadline, excessive output hits the capture limit, and an invented refund total fails independent verification. Each run includes the inspected container policy, resource limits, artifact hashes and a confirmed removal. All six measured probes pass. These receipts describe observed checks; they are not a security audit.'},
 {'id':'08-packet','title':'Leave with a checked case packet and a clear next step.','text':
  'The customer approved a practice offer. The receipt clearly records a simulation, no real company contact and zero money recovered. The useful artifact is the private case packet: reviewed facts, statement rows, an unsent follow-up draft, generated code, execution receipts and the approval record. The exact archived calculation can be replayed in gVisor. Refund Runner brings the evidence and customer limits together around a problem people recognize. Try the fictional case, inspect the source, and review the measured results.'}
]

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',default='/etc/refund-runner/config.json')
    parser.add_argument('--state-root',required=True);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();args.output.mkdir(mode=0o700,parents=True,exist_ok=True)
    config=json.loads(Path(args.config).read_text());config['state_root']=args.state_root
    engine=Engine(config);engine.ready_proof=engine.sandbox.ready();cid,_=engine.create(consent=True)
    (args.output/'script.json').write_text(json.dumps(SCRIPT,indent=2)+'\n')
    try:
        for section in SCRIPT:
            name=section['id'];path=args.output/(name+'.wav');proof=args.output/(name+'.receipt.json')
            if path.is_file() and proof.is_file():
                existing=json.loads(proof.read_text())
                if existing.get('normalized_sha256')==sha(path.read_bytes()) and existing.get('text_sha256')==sha(section['text'].encode()):continue
            words=section['text'].split();chunks=[' '.join(words[i:i+42]) for i in range(0,len(words),42)]
            frames=[];receipts=[];metadata=[]
            for chunk in chunks:
                raw,call=engine.inference(cid,'speak',chunk);normalized,observed=engine.normalize(cid,raw)
                with wave.open(io.BytesIO(normalized),'rb') as stream:
                    frames.append(stream.readframes(stream.getnframes()))
                receipts.append(call);metadata.append(observed)
            merged=io.BytesIO()
            with wave.open(merged,'wb') as stream:
                stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(b''.join(frames))
            audio=merged.getvalue();path.write_bytes(audio)
            combined={'seconds':sum(m['seconds'] for m in metadata),'chunks':len(chunks),'all_chunks_decoded_in_gvisor':True}
            receipt={'model':'qwen3-tts-12hz-1.7b','voice':'ryan','text_sha256':sha(section['text'].encode()),
                     'normalized_sha256':sha(audio),'normalized_metadata':combined,'chunk_metadata':metadata,'provider_receipts':receipts}
            proof.write_text(json.dumps(receipt,indent=2)+'\n');print(name,combined['seconds'],'seconds',flush=True)
    finally:engine.close()
if __name__=='__main__':main()

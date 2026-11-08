"""Case lifecycle, bounded jobs, independent accounting and scoped practice actions."""
import base64
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import date
import io
import json
from pathlib import Path
import re
import secrets
import threading
import time
import zipfile
from .contract import (Rejected, VerificationError, approval_binding, canonical, checked_documents,
                       checked_proposal, checked_rows, clean_text, decode_json, evaluation_cases,
                       expected_result, freeze_claim, gate_action, mandate_for, require, sha,
                       valid_offer, verify_approval, verify_candidate)
from .fixtures import VARIANTS, files as fixture_files
from .presentation import (explanation, followup, intake_text, next_step, summary,
                           support_events, support_text)
from .provider import ProviderError, Vultr
from .sandbox import Sandbox, successful
from .store import Store

class Busy(Rejected):
    pass

class Budget:
    """Persist reservations BEFORE calls; errors still consume the invocation allowance."""
    def __init__(self, path, calls=120, cases=24):
        self.path, self.calls, self.cases = Path(path), calls, cases
        self.lock = threading.RLock()

    def reserve(self, kind):
        with self.lock:
            today = date.today().isoformat()
            state = decode_json(self.path.read_bytes()) if self.path.is_file() else {}
            if state.get('day') != today: state = {'day': today, 'calls': 0, 'cases': 0}
            limit = self.calls if kind == 'calls' else self.cases
            if state[kind] >= limit: raise Busy('The demonstration has reached its daily ' + kind + ' allowance. Existing evidence remains available.')
            state[kind] += 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix('.tmp')
            temp.write_bytes(canonical(state)); temp.chmod(0o600); temp.replace(self.path)

class Engine:
    def __init__(self, config, *, sandbox=None, provider=None):
        self.config = config
        self.source_root = Path(config['source_root']).resolve()
        self.state_root = Path(config['state_root']).resolve()
        self.state_root.mkdir(parents=True, mode=0o700, exist_ok=True)
        self.store = Store(self.state_root / 'cases')
        self.sandbox = sandbox or Sandbox(self.state_root / 'sandboxes', config['images'])
        self.provider = provider or Vultr(config.get('inference_key', ''))
        self.budget = Budget(self.state_root / 'daily-budget.json', config.get('daily_calls', 120), config.get('daily_cases', 24))
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='refund-worker')
        self.lock = threading.RLock()
        self.pending = 0
        self.lab = {'status': 'idle', 'checks': [], 'receipts': [], 'measured_at': None}
        self.ready_proof = None
        self.maintenance_stop = threading.Event()

    def maintain(self):
        while not self.maintenance_stop.wait(60):
            self.store.collect()
            self.sandbox.collect()

    def edit(self, cid, function):
        with self.store.lock:
            state = self.store.load(cid)
            result = function(state)
            self.store.save(state)
            return result

    def create(self, mode='demo', variant='overdue', consent=False):
        require(mode in {'demo', 'personal'} and variant in VARIANTS, 'Select a supported case.')
        require(consent is True, 'Review and accept the Vultr data-transfer notice before starting a case.')
        self.store.collect()
        # At most 64 live capability-protected cases, independent of provider spending.
        live = sum(1 for p in self.store.root.iterdir() if p.is_dir() and re.fullmatch(r'[a-f0-9]{32}', p.name))
        require(live < 64, 'The demonstration is at its active-case limit. Please try again after cases expire.')
        cid, capability = self.store.create(mode)
        self.store.update(cid, consent=True, fixture_variant=variant, voice_enabled=True)
        self.store.append(cid, 'messages', {'id': secrets.token_hex(8), 'role': 'caseworker', 'kind': 'welcome',
            'text': 'Tell me what happened, in your own words. I can check a refund the merchant already promised, then help you prepare the evidence and practice the follow-up.', 'time': time.time()})
        self.store.event(cid, 'consent_recorded', {'destination': 'Vultr Serverless Inference',
            'audio': 'normalized voice notes', 'documents': 'extracted confirmation text',
            'statement': 'rows are processed in networkless workers and private case state; only verified totals go to the planner',
            'real_company_contact': False})
        return cid, capability

    def launch(self, cid, stage, function):
        with self.lock:
            state = self.store.load(cid)
            if state['busy']: raise Busy('This case is already working. Wait for the current step to finish.')
            if self.pending >= 4: raise Busy('The bounded work queue is full. Please try again shortly.')
            self.pending += 1
            self.store.update(cid, busy=True, stage=stage, error='')
            try:
                self.executor.submit(self.guarded, cid, function)
            except BaseException:
                self.pending -= 1
                self.store.update(cid, busy=False)
                raise

    def guarded(self, cid, function):
        try:
            function()
        except (Rejected, ProviderError, RuntimeError, ValueError, OSError) as error:
            if isinstance(error, ProviderError) and error.receipt:
                self.store.append(cid, 'provider_receipts', error.receipt)
            message = str(error)[:500]
            self.store.update(cid, status='error', stage='This step needs attention', error=message)
            self.edit(cid, lambda s: s['practice'].update(status='failed') if (s.get('practice') or {}).get('status') == 'running' else None)
            self.store.event(cid, 'step_failed', {'message': message, 'success_inferred': False})
        except BaseException:
            self.store.update(cid, status='error', stage='This step needs attention',
                              error='The bounded job failed. No successful result or action is inferred.')
            self.store.event(cid, 'step_failed', {'message': 'Unexpected controller error', 'success_inferred': False})
        finally:
            with self.lock:
                self.pending -= 1
                self.store.update(cid, busy=False)

    def log(self, cid, stage, text):
        self.store.update(cid, stage=stage)
        self.store.append(cid, 'logs', {'time': time.time(), 'text': text})

    def inference(self, cid, method, *args):
        self.budget.reserve('calls')
        try:
            value, receipt = getattr(self.provider, method)(*args)
        except ProviderError as error:
            if error.receipt: self.store.append(cid, 'provider_receipts', error.receipt)
            error.receipt = None
            raise
        self.store.append(cid, 'provider_receipts', receipt)
        self.store.event(cid, 'inference_observed', {'model': receipt.get('model'), 'endpoint': receipt.get('endpoint'),
                                                   'response_sha256': receipt.get('response_sha256')})
        return value, receipt

    def run(self, cid, operation, files, **limits):
        result = self.sandbox.run(operation, files, **limits)
        self.store.append(cid, 'receipts', result['receipt'])
        self.store.event(cid, 'sandbox_observed', {'sandbox_id': result['receipt']['sandbox_id'],
            'operation': operation, 'stdout_sha256': result['receipt'].get('stdout_sha256'),
            'policy_passed': bool(result['receipt'].get('policy')) and all(result['receipt']['policy'].values()),
            'teardown_confirmed': result['receipt'].get('teardown', {}).get('confirmed')})
        if not successful(result):
            raise Rejected('The isolated ' + operation + ' step did not pass execution, input-integrity and teardown checks. ' +
                           result['receipt'].get('stderr', '')[:300])
        return result

    def normalize(self, cid, raw):
        result = self.run(cid, 'audio', {'voice.raw': raw})
        value = decode_json(result['output'])
        require(isinstance(value, dict) and value.get('source_sha256') == sha(raw), 'The audio decoder returned mismatched evidence.')
        audio = self.decode_upload(value.get('audio_base64'), 1200000)
        require(value.get('sha256') == sha(audio) and 0.2 <= value.get('seconds', 0) <= 35,
                'The audio decoder returned invalid normalized audio.')
        return audio, value

    def say(self, cid, text, *, role='caseworker', kind='guidance', speak=True):
        item = {'id': secrets.token_hex(8), 'role': role, 'kind': kind, 'text': text, 'time': time.time()}
        self.store.append(cid, 'messages', item)
        state = self.store.load(cid)
        if not speak or not state.get('voice_enabled', True): return item
        try:
            raw, receipt = self.inference(cid, 'speak', text)
            audio, metadata = self.normalize(cid, raw)
            name = 'voice-' + secrets.token_hex(12) + '.wav'
            self.store.write(cid, name, audio)
            def attach(current):
                current['artifacts'][name] = {'sha256': sha(audio), 'bytes': len(audio), 'seconds': metadata['seconds'], 'kind': 'caseworker_speech'}
                for message in current['messages']:
                    if message['id'] == item['id']: message['audio'] = name
            self.edit(cid, attach)
            item['audio'] = name
        except (ProviderError, Rejected, ValueError) as error:
            self.store.append(cid, 'logs', {'time': time.time(), 'text': 'Speech unavailable for this turn; the verified text remains visible. ' + str(error)[:200]})
        return item

    @staticmethod
    def decode_upload(encoded, maximum):
        require(isinstance(encoded, str) and 0 < len(encoded) <= ((maximum + 2) // 3) * 4 + 4,
                'The upload is empty or exceeds its file budget.')
        try: raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError): raise Rejected('The upload is not valid base64.') from None
        require(0 < len(raw) <= maximum, 'The decoded upload exceeds its file budget.')
        return raw

    def intake(self, cid, text=None, audio=None):
        state = self.store.load(cid)
        require(state['consent'], 'Approve the data-transfer notice first.')
        require(state['turns'] < 8, 'This short demonstration supports up to eight intake turns per case.')
        require((isinstance(text, str) and audio is None) or (text is None and isinstance(audio, str)), 'Send either typed text or one voice note.')
        raw = self.decode_upload(audio, 6 * 1024 ** 2) if audio is not None else None
        if text is not None:
            text = clean_text(' '.join(text.split()), 1200)
            require(bool(text), 'Tell the caseworker what happened.')
        def work():
            transcript = text
            if raw is not None:
                self.log(cid, 'Listening to your note', 'Decoding the audio in a networkless gVisor worker before Vultr transcription.')
                note, metadata = self.normalize(cid, raw)
                name = 'note-' + secrets.token_hex(10) + '.wav'
                self.store.write(cid, name, note)
                self.edit(cid, lambda s: s['artifacts'].update({name: {'sha256': sha(note), 'bytes': len(note), 'seconds': metadata['seconds'], 'kind': 'customer_note'}}))
                transcript, _ = self.inference(cid, 'transcribe', note)
                transcript = clean_text(' '.join(transcript.split()), 1200)
            self.say(cid, transcript, role='customer', kind='voice_transcript' if raw is not None else 'typed_intake', speak=False)
            self.edit(cid, lambda s: s.update(turns=s['turns'] + 1))
            current = self.store.load(cid)
            self.log(cid, 'Choosing the next evidence step', 'Vultr selects a typed intake step. A transcript never grants authority.')
            value, _ = self.inference(cid, 'intake', transcript,
                                      [{'role': m['role'], 'text': m['text']} for m in current['messages'][-4:]], bool(current['documents']))
            response, step = intake_text(value, bool(current['documents']))
            if (current.get('practice') or {}).get('status') == 'awaiting_approval':
                response, step = ('The practice offer is still waiting for your review. Read the exact amount, payment method, timing and destination on screen. '
                                  'Your spoken or typed yes does not accept it. Only the review button can approve these terms.'), 'review_evidence'
            elif (current.get('practice') or {}).get('status') == 'complete':
                response, step = ('Your practice case is already recorded. It was a simulation: no company was contacted and no money was recovered. '
                                  'You can download the verified packet and review it before using it.'), 'review_evidence'
            elif current.get('result'):
                response, step = explanation(current['result']), next_step(current['result'])
            self.say(cid, response, kind='intake_guidance')
            current = self.store.load(cid)
            status = 'intake' if not current.get('documents') else 'review'
            if current.get('practice'): status = current['status'] if current['status'] != 'error' else 'verified'
            elif current.get('result'): status = 'verified'
            self.store.update(cid, status=status, stage='Review the offer on screen' if status == 'awaiting_approval' else 'Ready for the next step', intake_step=step)
        self.launch(cid, 'Listening to your case', work)

    def documents(self, cid, uploads=None, currency='USD'):
        state = self.store.load(cid)
        require(currency in {'USD', 'GBP', 'EUR'}, 'Select a supported statement currency.')
        if state['mode'] == 'demo' and uploads is None:
            confirmation, statement = fixture_files(self.source_root, state['fixture_variant'])
        else:
            require(isinstance(uploads, dict) and set(uploads) == {'confirmation', 'statement'}, 'Add both the confirmation and statement.')
            confirmation = self.decode_upload(uploads['confirmation'], 4 * 1024 ** 2)
            statement = self.decode_upload(uploads['statement'], 2 * 1024 ** 2)
        def work():
            self.log(cid, 'Reading the confirmation and statement', 'The fixed file decoders run in a fresh networkless gVisor worker.')
            output = self.run(cid, 'documents', {'confirmation.raw': confirmation, 'statement.csv': statement,
                                                'request.json': canonical({'currency': currency})})
            value = decode_json(output['output'])
            lines = checked_documents(value['document']); rows = checked_rows(value['statement']['rows'])
            require(value['document']['sha256'] == sha(confirmation) and value['statement']['sha256'] == sha(statement),
                    'The decoder hashes do not match the supplied documents.')
            self.log(cid, 'Finding proposed refund details', 'Only the extracted confirmation text goes to Vultr; the bank CSV stays out of the generation prompt.')
            proposed, _ = self.inference(cid, 'extract', lines)
            proposal = checked_proposal(proposed, lines)
            self.store.write(cid, 'confirmation.raw', confirmation)
            self.store.write(cid, 'statement.csv', statement)
            def attach(current):
                current.update(documents={'lines': lines, 'pages': value['document'].get('pages'),
                    'confirmation_sha256': sha(confirmation), 'statement_sha256': sha(statement),
                    'defaulted_columns': value['statement']['defaulted'], 'columns': value['statement']['columns']},
                    rows=rows, proposal=proposal, revision=current['revision'] + 1, claim=None, result=None,
                    practice=None, checks=[], calculation=None, status='review', stage='Review the sourced details')
            self.edit(cid, attach)
            self.store.event(cid, 'documents_decoded', {'confirmation_sha256': sha(confirmation), 'statement_sha256': sha(statement),
                                                       'rows': len(rows), 'source_spans_checked': sum(p['source_span_checked'] for p in proposal.values())})
            self.say(cid, 'I have proposed the refund details from the confirmation. Review each source, the statement dates and the credit sign. Your confirmation establishes the inputs; the independent calculation checks what is still outstanding.', kind='document_review')
            self.store.update(cid, stage='Review the sourced details')
        self.launch(cid, 'Reading your evidence', work)

    def analyze(self, cid, fields, voice_enabled=True):
        state = self.store.load(cid)
        require(state['documents'] is not None, 'Read the confirmation and statement first.')
        digest = sha(canonical({'confirmation': state['documents']['confirmation_sha256'],
                                'statement': state['documents']['statement_sha256']}))
        claim = freeze_claim(fields, state['proposal'], state['rows'], digest, state['revision'] + 1)
        require(type(voice_enabled) is bool, 'Select a valid voice preference.')
        def work():
            self.budget.reserve('cases')
            self.store.update(cid, revision=claim['revision'], claim=claim, practice=None, result=None, checks=[],
                              calculation=None, voice_enabled=voice_enabled, status='checking')
            self.store.event(cid, 'customer_facts_confirmed', {'claim_sha256': claim['sha256'], 'revision': claim['revision']})
            jobs = evaluation_cases(claim, state['rows'])
            request = canonical({'jobs': jobs})
            self.store.write(cid, 'calculation-input.json', request)
            attempts = []
            previous_error = None
            for attempt in range(1, 3):
                self.log(cid, 'Writing the reconciliation program', 'Vultr writes Python from the accounting contract. No customer statement is sent in this prompt.')
                try:
                    program, receipt = self.inference(cid, 'program', previous_error)
                    require(isinstance(program.get('code'), str) and 40 <= len(program['code']) <= 18000 and '\x00' not in program['code'],
                            'The planner did not return a bounded complete program.')
                    code = program['code'].encode('utf-8')
                    self.store.write(cid, 'candidate-' + str(attempt) + '.py', code)
                    self.log(cid, 'Executing the program in gVisor', 'The candidate receives the reviewed statement and fresh control cases inside a sealed worker.')
                    output = self.run(cid, 'agent', {'agent.py': code, 'request.json': request})
                    observed = decode_json(output['output'])
                    self.log(cid, 'Checking the answer independently', 'The host verifier checks exact integer-cent results and counterfactual credits. The candidate cannot edit it.')
                    result, checks = verify_candidate(observed, jobs)
                    attempts.append({'attempt': attempt, 'passed': True, 'code_sha256': sha(code), 'checks': checks})
                    self.store.write(cid, 'calculation.py', code)
                    self.store.write(cid, 'calculation-output.json', output['output'])
                    break
                except (Rejected, ProviderError, ValueError) as error:
                    attempts.append({'attempt': attempt, 'passed': False, 'message': str(error)[:400],
                                     'checks': getattr(error, 'checks', [])})
                    self.store.update(cid, calculation={'attempts': attempts})
                    # Candidate stderr may contain customer rows. Never echo it to the model.
                    previous_error = ('The program disagreed with the fixed integer-cent checks. Recheck all status priorities, credit signs, duplicates and exact references.'
                                      if isinstance(error, VerificationError) else
                                      'The prior candidate failed execution or the output contract. Return a complete standard-library script that prints only the specified JSON results.')
                    if attempt == 2: raise Rejected('Both bounded reconciliation attempts failed verification. Inspect the private execution receipts for the observed error.') from None
                    self.log(cid, 'Repairing the failed calculation', 'The failed execution is retained. Vultr gets the error for one bounded retry; no answer is assumed.')
            self.store.update(cid, result=result, checks=checks, calculation={'attempts': attempts, 'code_sha256': sha(code),
                              'input_sha256': sha(request), 'output_sha256': sha(output['output']),
                              'verifier': 'refund.contract.expected_result', 'counterfactuals': len(jobs) - 1}, status='verified')
            self.store.event(cid, 'result_verified', {'result_sha256': sha(canonical(result)), 'checked_cases': len(checks),
                                                     'all_passed': all(c['passed'] for c in checks)})
            self.log(cid, 'Explaining the verified finding', 'Vultr chooses the next step from the observed result; trusted text supplies the amounts and coverage.')
            choice, _ = self.inference(cid, 'explain', result)
            compatible = choice.get('finding') == result['status'] and choice.get('next_step') == next_step(result)
            self.store.event(cid, 'explanation_gated', {'model_choice_matches': compatible, 'executed_next_step': next_step(result)})
            self.say(cid, explanation(result), kind='verified_finding')
            self.store.update(cid, status='verified', stage='Your calculation is verified', next_step=next_step(result))
        self.launch(cid, 'Checking the promised refund', work)

    def start_practice(self, cid, confirmed):
        state = self.store.load(cid)
        require(state.get('result') is not None and state.get('claim') is not None, 'Verify this case first.')
        require(confirmed is True, 'Review and confirm the practice mandate on screen.')
        mandate = mandate_for(state['claim'], state['result'])
        require(not state.get('practice') or state['practice']['status'] == 'failed', 'This case already has a practice conversation. Recheck the case to start another.')
        def work():
            disclosed = {'order_reference': state['claim']['order_reference'], 'refund_reference': state['claim']['refund_reference'],
                         'outstanding_amount_cents': mandate['amount_cents'], 'currency': mandate['currency'],
                         'promised_by': state['claim']['promised_by']}
            request_text = ('I am following up on the promised refund. Order reference: ' + (disclosed['order_reference'] or 'not supplied') +
                            '. Refund reference: ' + (disclosed['refund_reference'] or 'not supplied') +
                            '. Outstanding amount: ' + str(disclosed['outstanding_amount_cents'] // 100) + '.' +
                            str(disclosed['outstanding_amount_cents'] % 100).zfill(2) + ' ' + disclosed['currency'] +
                            '. Promised by: ' + disclosed['promised_by'] + '. Please refund the original payment method.')
            practice = {'status': 'running', 'mandate': mandate, 'disclosed': disclosed,
                        'transcript': [{'role': 'caseworker', 'text': request_text, 'time': time.time(),
                                        'gate': {'action': 'share_approved_fields', 'blocked': False}}], 'offer': None,
                        'approval_binding': None, 'approval': None, 'real_company_contact': False}
            self.store.update(cid, practice=practice, status='practice')
            self.store.event(cid, 'practice_mandate_confirmed', {'mandate_sha256': sha(canonical(mandate)), 'destination': 'owned_practice_desk'})
            self.store.event(cid, 'practice_request_dispatched', {'destination': 'owned_practice_desk', 'disclosed': disclosed,
                                                                'real_company_contact': False})
            self.say(cid, request_text, kind='practice_request', speak=False)
            for event in support_events(mandate):
                self.log(cid, 'Practicing the support conversation', 'The owned desk proposes ' + event['kind'].replace('_', ' ') + '. Only permitted typed actions can speak.')
                def merchant(current):
                    current['practice']['transcript'].append({'role': 'merchant', 'text': event['text'], 'event': event, 'time': time.time()})
                self.edit(cid, merchant)
                self.say(cid, event['text'], role='merchant', kind='practice_merchant', speak=False)
                transcript = self.store.load(cid)['practice']['transcript']
                proposed, _ = self.inference(cid, 'support_action', mandate, event, transcript)
                action = gate_action(proposed, event, mandate)
                text = support_text(action['action'], mandate)
                def response(current):
                    current['practice']['transcript'].append({'role': 'caseworker', 'text': text, 'gate': action, 'time': time.time()})
                self.edit(cid, response)
                self.store.event(cid, 'support_action_gated', {**action, 'event': event['kind'], 'destination': 'owned_practice_desk'})
                self.say(cid, text, kind='practice_' + action['action'])
                if action['action'] == 'request_customer_approval':
                    def offer(current):
                        current['practice'].update(status='awaiting_approval', offer=event['terms'])
                        current['practice']['approval_binding'] = approval_binding(current)
                        current.update(status='awaiting_approval', stage='Review the exact practice offer')
                    self.edit(cid, offer)
                    return
            self.edit(cid, lambda s: s.update(status='verified', stage='The practice desk needs clarification'))
            self.edit(cid, lambda s: s['practice'].update(status='failed'))
        self.launch(cid, 'Starting the owned practice desk', work)

    def approve(self, cid, binding, confirmed):
        with self.lock:
            state = self.store.load(cid)
            if state['busy']: raise Busy('Wait for the conversation to pause before approving an offer.')
            approval = verify_approval(state, binding, confirmed)
            approval['recorded_at'] = time.time()
            approval['practice_reference'] = 'RR-PRACTICE-' + secrets.token_hex(4).upper()
            def accept(current):
                current['practice'].update(status='complete', approval=approval,
                    approved_snapshot={'case': current['claim']['sha256'], 'revision': current['revision'],
                                       'result': copy.deepcopy(current['result']), 'mandate': copy.deepcopy(current['practice']['mandate']),
                                       'transcript': copy.deepcopy(current['practice']['transcript']), 'terms': copy.deepcopy(current['practice']['offer']),
                                       'destination': 'owned_practice_desk'})
                current.update(status='practice_complete', stage='Practice completed; your packet is ready')
            self.edit(cid, accept)
            self.store.event(cid, 'practice_approved', approval)
            self.say(cid, 'The practice desk recorded the exact offer you approved. This was a simulation: no company was contacted and no money was recovered. Your verified case packet is ready for you to review and use.', kind='practice_receipt', speak=False)
            return approval

    def settings(self, cid, voice_enabled):
        require(type(voice_enabled) is bool, 'Select a valid voice preference.')
        with self.lock:
            state = self.store.load(cid)
            if state['busy']: raise Busy('Wait for the current turn before changing voice playback.')
            self.store.update(cid, voice_enabled=voice_enabled)

    def snapshot(self, cid):
        state = self.store.load(cid)
        allowed = ('id', 'mode', 'fixture_variant', 'revision', 'status', 'stage', 'busy', 'expires_at', 'messages',
                   'documents', 'rows', 'proposal', 'claim', 'result', 'practice', 'logs', 'checks', 'calculation',
                   'error', 'turns', 'voice_enabled', 'artifacts', 'intake_step', 'next_step')
        value = {k: copy.deepcopy(state[k]) for k in allowed if k in state}
        value['execution_summary'] = {'runs': len(state['receipts']),
            'all_teardown_confirmed': all(r.get('teardown', {}).get('confirmed') for r in state['receipts']),
            'provider_calls': len(state['provider_receipts'])}
        return value

    def evidence(self, cid):
        state = self.store.load(cid)
        require(not state['busy'], 'Wait for this bounded step to finish before exporting evidence.')
        require(state.get('result') is not None, 'Verify the calculation before downloading evidence.')
        return {'schema_version': 1, 'project': 'Refund Runner', 'mode': state['mode'],
            'fictional': state['mode'] == 'demo', 'claim': state['claim'], 'result': state['result'],
            'documents': state['documents'], 'rows': state['rows'], 'checks': state['checks'],
            'calculation': state['calculation'], 'practice': state['practice'], 'ledger': state['ledger'],
            'execution_receipts': state['receipts'], 'provider_receipts': state['provider_receipts'],
            'limits': {'authenticity_verified': False, 'coverage': 'customer-confirmed supplied statement only',
                       'real_company_contact': False, 'money_recovered_cents': 0,
                       'calling_service_connected': False, 'public_policy_browser_connected': False}}

    def packet(self, cid):
        with self.lock:
            return self._packet(cid)

    def _packet(self, cid):
        state = self.store.load(cid)
        evidence = self.evidence(cid)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('case-summary.md', summary(state))
            if state['result']['status'] in {'overdue', 'partially_refunded'}: archive.writestr('follow-up-draft.txt', followup(state))
            archive.writestr('evidence.json', canonical(evidence))
            archive.writestr('calculation.py', self.store.read(cid, 'calculation.py'))
            archive.writestr('calculation-input.json', self.store.read(cid, 'calculation-input.json'))
            archive.writestr('calculation-output.json', self.store.read(cid, 'calculation-output.json'))
            archive.writestr('statement.csv', self.store.read(cid, 'statement.csv'))
            archive.writestr('confirmation-extracted.txt', '\n'.join(x['id'] + ': ' + x['text'] for x in state['documents']['lines']))
        return buffer.getvalue()

    def audio(self, cid, name):
        state = self.store.load(cid)
        require(name in state['artifacts'] and state['artifacts'][name]['kind'] == 'caseworker_speech', 'This spoken response is unavailable.')
        data = self.store.read(cid, name)
        require(sha(data) == state['artifacts'][name]['sha256'], 'The spoken artifact changed.')
        return data

    def start_lab(self):
        with self.lock:
            if self.lab['status'] == 'running': raise Busy('The containment checks are already running.')
            if self.lab.get('measured_at') and time.time() - self.lab['measured_at'] < 300:
                return {'status': self.lab['status'], 'cached': True, 'measured_at': self.lab['measured_at']}
            if self.pending >= 4: raise Busy('The bounded work queue is full.')
            self.pending += 1
            self.lab = {'status': 'running', 'checks': [], 'receipts': [], 'measured_at': None}
            self.executor.submit(self.lab_work)
            return {'status': 'running', 'cached': False}

    def lab_work(self):
        from .lab import run_lab
        try:
            value = run_lab(self.sandbox, self.state_root)
            with self.lock: self.lab = value
        except BaseException as error:
            with self.lock: self.lab.update(status='failed', error=str(error)[:400], measured_at=time.time())
        finally:
            with self.lock: self.pending -= 1

    def close(self):
        self.maintenance_stop.set()
        self.executor.shutdown(wait=True, cancel_futures=False)

'use strict';
const $ = (id) => document.getElementById(id);
let currentId = sessionStorage.getItem('refund-case-id');
let currentState = null, info = {}, pendingMode = 'demo', pendingVariant = 'overdue';
let pollTimer, labTimer, lastDocument = '', lastMessages = '', lastPractice = '', lastBinding = '';
let formDirty = false, autoPlayReplies = false, recording = false;
let recorder, stream, recordingTimer, elapsedTimer, recordedChunks = [];
const heardAudio = new Set();
const audioPlayer = $('caseworker-audio');
const LABELS = {merchant:'Merchant',order_reference:'Order reference',refund_reference:'Refund reference',amount:'Promised amount',currency:'Currency',promised_by:'Promised by'};
const STATUS = {intake:'Ready to listen',review:'Your review needed',checking:'Checking the money',verified:'Calculation verified',practice:'Practice in progress',awaiting_approval:'Your approval needed',practice_complete:'Practice complete',error:'Needs attention'};
const FINDINGS = {overdue:'The promised refund is overdue.',partially_refunded:'Part of your refund is still outstanding.',already_refunded:'Your promised refund has posted.',pending:'A matching credit is still pending.',needs_review:'A credit needs clarification.',not_due:'The promised date has not passed.',coverage_gap:'Your statement has a coverage gap.'};

async function api(path, body, method = body === undefined ? 'GET' : 'POST') {
  const response = await fetch(path, {method, credentials:'same-origin', cache:'no-store',
    ...(body === undefined ? {} : {headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})});
  const value = await response.json();
  if (!response.ok) { const error = new Error(value.error || 'This step could not complete.'); error.status = response.status; throw error; }
  return value;
}
function errorMessage(error) { $('error-banner').textContent = error.message || String(error); $('error-banner').hidden = false; }
function clearError() { $('error-banner').hidden = true; $('error-banner').textContent = ''; }
function safely(fn) { return (...args) => { try { return Promise.resolve(fn(...args)).catch(errorMessage); } catch (error) { errorMessage(error); } }; }
function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.hidden = p.id !== 'page-' + name);
  document.querySelectorAll('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.page === name));
  $('page-label').textContent = {case:'Your case',guide:'How it works',proof:'Proof & tests'}[name];
  if (name === 'proof') pollLab();
  window.scrollTo({top:0,behavior:'instant'});
}
document.querySelectorAll('.nav-item').forEach(b => b.addEventListener('click', () => showPage(b.dataset.page)));
function money(cents, currency = 'USD') { return new Intl.NumberFormat('en-US',{style:'currency',currency}).format(cents / 100); }
function element(tag, text, className) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n; }
function addTerms(container, rows) {
  container.replaceChildren();
  for (const [label, value] of rows) container.append(element('dt',label),element('dd',String(value)));
}
async function toBase64(file, maximum) {
  if (!file || file.size === 0 || file.size > maximum) throw new Error('Choose a nonempty file within the stated file limit.');
  return new Promise((resolve,reject) => { const reader = new FileReader(); reader.onerror = () => reject(new Error('The browser could not read this file.')); reader.onload = () => resolve(String(reader.result).split(',')[1]); reader.readAsDataURL(file); });
}
function resetView() {
  lastDocument = ''; lastMessages = ''; lastPractice = ''; lastBinding = ''; formDirty = false;
  heardAudio.clear(); audioPlayer.pause(); audioPlayer.removeAttribute('src');
  $('review-form').reset(); $('mandate-confirmed').checked = false; $('offer-confirmed').checked = false;
  ['review-card','result-card','practice-card','offer-review','practice-receipt','document-success'].forEach(id => $(id).hidden = true);
  $('practice-timeline').replaceChildren(); $('conversation').replaceChildren(); $('activity-log').replaceChildren();
}
async function createCase(mode = 'demo', variant = 'overdue') {
  clearError(); resetView();
  const result = await api('/api/cases',{mode,variant,consent:true});
  currentId = result.id; sessionStorage.setItem('refund-case-id',currentId);
  $('landing').hidden = true; $('workspace').hidden = false; $('sidebar-case').hidden = false; $('new-case').hidden = false;
  $('demo-documents').hidden = mode !== 'demo'; $('upload-form').hidden = mode === 'demo';
  $('sample-variant').value = variant; showPage('case'); await pollCase();
}
function beginCase(mode,variant = 'overdue') {
  pendingMode = mode; pendingVariant = variant;
  if (sessionStorage.getItem('refund-data-consent') === 'accepted') return createCase(mode,variant);
  $('consent-checked').checked = false; $('consent-dialog').showModal();
}
$('try-demo').addEventListener('click',safely(() => beginCase('demo')));
$('start-personal').addEventListener('click',safely(() => beginCase('personal')));
$('cancel-consent').addEventListener('click',() => $('consent-dialog').close());
$('consent-form').addEventListener('submit',safely(async (event) => {
  event.preventDefault(); if (!$('consent-checked').checked) return;
  sessionStorage.setItem('refund-data-consent','accepted'); $('consent-dialog').close();
  await createCase(pendingMode,pendingVariant);
}));
$('new-case').addEventListener('click',() => {
  if (recording || currentState?.busy) return;
  clearTimeout(pollTimer); currentId = null; currentState = null; sessionStorage.removeItem('refund-case-id');
  resetView(); $('workspace').hidden = true; $('landing').hidden = false; $('sidebar-case').hidden = true; $('new-case').hidden = true; clearError(); showPage('case');
});
$('sample-variant').addEventListener('change',safely(async () => {
  if (currentState?.busy || recording) return;
  if ($('sample-variant').value !== currentState.fixture_variant) await createCase('demo',$('sample-variant').value);
}));
async function mutate(action, body) {
  if (!currentId) throw new Error('Start a case first.');
  if (recording && action !== 'intake') throw new Error('Finish the recording before starting another step.');
  clearError(); autoPlayReplies = $('voice-enabled').checked;
  await api('/api/cases/' + currentId + '/' + action,body);
  await pollCase();
}
$('load-demo-docs').addEventListener('click',safely(() => mutate('documents',{})));
for (const [input,label] of [['confirmation-file','confirmation-name'],['statement-file','statement-name']]) {
  $(input).addEventListener('change',() => $(label).textContent = $(input).files[0]?.name || 'No file selected');
}
$('upload-form').addEventListener('submit',safely(async (event) => {
  event.preventDefault();
  const confirmation = await toBase64($('confirmation-file').files[0],4*1024**2);
  const statement = await toBase64($('statement-file').files[0],2*1024**2);
  await mutate('documents',{currency:$('statement-currency').value,uploads:{confirmation,statement}});
}));
function markDirty() {
  if (currentState?.result) { formDirty = true; $('offer-confirmed').checked = false; }
  refreshControls();
}
$('review-form').addEventListener('input',markDirty);
$('review-form').addEventListener('change',markDirty);
$('review-form').addEventListener('submit',safely(async (event) => {
  event.preventDefault();
  const form = $('review-form'); const fields = Object.fromEntries(new FormData(form));
  fields.confirmed = form.elements.confirmed.checked;
  fields.original_payment_promise = form.elements.original_payment_promise.checked;
  await mutate('analyze',{fields,voice_enabled:$('voice-enabled').checked}); formDirty = false;
}));
$('intake-form').addEventListener('submit',safely(async (event) => {
  event.preventDefault(); const text = $('intake-text').value.trim(); if (!text) return;
  await mutate('intake',{text}); $('intake-text').value = '';
}));
$('voice-enabled').addEventListener('change',safely(async () => {
  if (!$('voice-enabled').checked) { audioPlayer.pause(); autoPlayReplies = false; }
  await api('/api/cases/' + currentId + '/settings',{voice_enabled:$('voice-enabled').checked});
  await pollCase();
}));
$('voice-file').addEventListener('change',safely(async () => {
  const file = $('voice-file').files[0]; if (!file) return;
  const audio = await toBase64(file,6*1024**2); await mutate('intake',{audio}); $('voice-file').value = '';
}));
$('demo-voice-btn').addEventListener('click',safely(async () => {
  const response = await fetch('/demo/sample-intake.wav');
  if (!response.ok) throw new Error('The sample voice note is not available yet. You can record or type the same story.');
  await mutate('intake',{audio:await toBase64(await response.blob(),6*1024**2)});
}));
function finishRecordingUI() {
  clearTimeout(recordingTimer); clearInterval(elapsedTimer);
  if (stream) stream.getTracks().forEach(t => t.stop()); stream = null;
  recording = false; $('record-btn').classList.remove('recording'); $('record-label').textContent = 'Record your note'; $('record-time').textContent = 'Up to 35 seconds'; refreshControls();
}
$('record-btn').addEventListener('click',safely(async () => {
  if (recording && recorder?.state === 'recording') { recorder.stop(); return; }
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') throw new Error('This browser cannot record audio here. Upload a voice note or type your message.');
  if (currentState?.busy) return;
  try { stream = await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true},video:false}); }
  catch { $('mic-note').textContent = 'Microphone access was not granted. Upload a voice note or type the same story below.'; throw new Error('Microphone access is unavailable. The text and audio-upload options still work.'); }
  const mime = ['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4'].find(m => MediaRecorder.isTypeSupported(m));
  recordedChunks = []; recorder = new MediaRecorder(stream,mime ? {mimeType:mime} : {});
  recorder.ondataavailable = event => { if (event.data.size) recordedChunks.push(event.data); };
  recorder.onerror = () => { finishRecordingUI(); errorMessage(new Error('The recording failed. Try a voice file or type your message.')); };
  recorder.onstop = safely(async () => {
    const blob = new Blob(recordedChunks,{type:recorder.mimeType}); finishRecordingUI();
    await mutate('intake',{audio:await toBase64(blob,6*1024**2)});
  });
  recording = true; const began = Date.now(); recorder.start(250);
  $('record-btn').classList.add('recording'); $('record-label').textContent = 'Stop & send note'; refreshControls();
  elapsedTimer = setInterval(() => $('record-time').textContent = Math.floor((Date.now()-began)/1000) + ' / 35 seconds',250);
  recordingTimer = setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); },34000);
}));
$('mandate-confirmed').addEventListener('change',refreshControls);
$('offer-confirmed').addEventListener('change',refreshControls);
$('start-practice').addEventListener('click',safely(() => mutate('practice',{confirmed:$('mandate-confirmed').checked})));
$('approve-offer').addEventListener('click',safely(async () => {
  if (formDirty) throw new Error('Recheck your changed details before approving an offer.');
  await mutate('approve',{binding:currentState.practice.approval_binding,confirmed:$('offer-confirmed').checked});
}));
$('delete-case').addEventListener('click',safely(async () => {
  if (recording || currentState?.busy) return;
  await api('/api/cases/' + currentId,{},'DELETE'); clearTimeout(pollTimer);
  currentId = null; currentState = null; sessionStorage.removeItem('refund-case-id'); resetView();
  $('workspace').hidden = true; $('landing').hidden = false; $('sidebar-case').hidden = true; $('new-case').hidden = true;
}));

async function listen(name) {
  if (!currentId) return;
  audioPlayer.src = '/api/cases/' + currentId + '/audio/' + name;
  try { await audioPlayer.play(); }
  catch { $('mic-note').textContent = 'Use the Listen button to play the spoken reply when your browser is ready.'; }
}
audioPlayer.addEventListener('playing',() => document.querySelector('.voice-card').classList.add('speaking'));
for (const event of ['pause','ended','error']) audioPlayer.addEventListener(event,() => document.querySelector('.voice-card').classList.remove('speaking'));
function renderMessages(state) {
  const signature = JSON.stringify(state.messages.map(m => [m.id,m.audio])); if (signature === lastMessages) return;
  lastMessages = signature; const list = $('conversation'); list.replaceChildren(); let newestAudio;
  for (const message of state.messages) {
    const node = element('div',undefined,'message ' + (message.role === 'customer' ? 'customer' : message.role === 'merchant' ? 'merchant' : ''));
    node.append(element('div',message.role === 'customer' ? (message.kind === 'voice_transcript' ? 'YOU · VOICE TRANSCRIPT' : 'YOU') : message.role === 'merchant' ? 'OWNED PRACTICE DESK' : 'YOUR CASEWORKER','message-label'),element('p',message.text));
    if (message.audio) {
      const button = element('button','▶  Listen to this reply','play-reply'); button.type = 'button'; button.dataset.audio = message.audio; button.addEventListener('click',() => listen(message.audio)); node.append(button);
      if (!heardAudio.has(message.audio)) { heardAudio.add(message.audio); newestAudio = message.audio; }
    }
    list.append(node);
  }
  list.scrollTop = list.scrollHeight;
  if (newestAudio && autoPlayReplies && state.voice_enabled) listen(newestAudio);
}
function renderDocuments(state) {
  if (!state.documents) return;
  const key = state.documents.confirmation_sha256 + state.documents.statement_sha256; if (key === lastDocument) return;
  lastDocument = key; formDirty = false; const form = $('review-form');
  for (const field of Object.keys(LABELS)) form.elements[field].value = state.proposal[field]?.value || '';
  if (state.mode === 'demo') { form.elements.statement_from.value = '2026-10-01'; form.elements.statement_through.value = '2026-11-07'; form.elements.credit_sign.value = 'negative'; }
  form.elements.original_payment_promise.checked = false; form.elements.confirmed.checked = false;
  $('source-quotes').replaceChildren();
  for (const [field,label] of Object.entries(LABELS)) {
    const source = state.proposal[field] || {}; const card = element('div',undefined,'source-quote' + (source.source_span_checked ? '' : ' unsupported'));
    card.append(element('strong',label),element('blockquote',source.quote || 'No exact supporting span was established. Check the confirmation and enter this detail yourself.'),element('span',source.source_span_checked ? source.source_id + ' · verbatim source span checked; interpretation needs your review' : 'Customer review required','source-ref'));
    $('source-quotes').append(card);
  }
  $('document-success').hidden = false; $('document-success').textContent = '✓ Read ' + state.documents.lines.length + ' confirmation lines and ' + state.rows.length + ' statement rows. Review the details below.';
  const defaults = state.documents.defaulted_columns;
  $('column-defaults').textContent = defaults.length ? 'CSV defaults: ' + defaults.map(k => ({currency:'currency uses your selected default',status:'missing status is treated as posted',reference:'missing references remain unresolved for merchant-only credits'}[k])).join('; ') + '.' : 'The CSV supplies currency, transaction status and references explicitly.';
}
function renderResult(state) {
  if (!state.result) return;
  const result = state.result, claim = state.claim;
  $('result-title').textContent = FINDINGS[result.status];
  $('amount-label').textContent = result.status === 'needs_review' ? 'Unresolved amount — clarification required' : 'Outstanding in this supplied statement';
  $('outstanding-amount').textContent = money(result.outstanding_cents,result.currency);
  $('coverage-note').textContent = 'Customer-confirmed coverage: ' + claim.statement_from + ' → ' + claim.statement_through;
  $('posted-amount').textContent = money(result.posted_credit_cents,result.currency); $('pending-amount').textContent = money(result.pending_credit_cents,result.currency); $('review-count').textContent = result.review_rows.length;
  const observedText = [...state.messages].reverse().find(m => m.kind === 'verified_finding');
  $('result-explanation').textContent = observedText?.text || FINDINGS[result.status];
  const rows = $('transaction-rows'); rows.replaceChildren();
  const matched = new Set(result.matched_rows), pending = new Set(result.pending_rows), review = new Set(result.review_rows);
  for (const row of state.rows) {
    const tr = element('tr',undefined,matched.has(row.id) ? 'matched-row' : pending.has(row.id) ? 'pending-row' : review.has(row.id) ? 'review-row' : '');
    const first = element('td',row.id); first.append(element('span',row.date));
    const status = element('td',row.status); status.append(element('span',row.reference || 'No reference'));
    tr.append(first,element('td',row.description),element('td',money(row.amount_cents,row.currency)),status); rows.append(tr);
  }
  $('packet-download').href = '/api/cases/' + currentId + '/packet'; $('evidence-download').href = '/api/cases/' + currentId + '/evidence';
  $('verification-line').textContent = state.checks.length + ' exact accounting checks passed · ' + state.calculation.counterfactuals + ' fresh control cases · all observed sandboxes removed: ' + (state.execution_summary.all_teardown_confirmed ? 'yes' : 'not confirmed');
}
function renderPractice(state) {
  if (!state.result) return;
  const eligible = ['overdue','partially_refunded'].includes(state.result.status);
  $('practice-card').hidden = !eligible;
  if (!eligible) return;
  $('mandate-amount').textContent = money(state.result.outstanding_cents,state.result.currency);
  const practice = state.practice;
  $('start-practice').hidden = !!practice && practice.status !== 'failed'; $('mandate-checkbox-row').hidden = !!practice && practice.status !== 'failed';
  const signature = JSON.stringify(practice?.transcript || []);
  if (signature !== lastPractice) {
    lastPractice = signature; $('practice-timeline').replaceChildren();
    for (const turn of practice?.transcript || []) {
      const node = element('div',undefined,'practice-turn');
      node.append(element('strong',turn.role === 'merchant' ? 'PRACTICE DESK' : 'CASEWORKER'),element('p',turn.text));
      if (turn.gate) node.append(element('span',turn.gate.action.replaceAll('_',' ') + (turn.gate.blocked ? ' · unsafe proposal replaced' : ' · within your limits'),'gate-badge'));
      $('practice-timeline').append(node);
    }
  }
  $('offer-review').hidden = practice?.status !== 'awaiting_approval';
  if (practice?.status === 'awaiting_approval') {
    if (lastBinding !== practice.approval_binding) { lastBinding = practice.approval_binding; $('offer-confirmed').checked = false; }
    const offer = practice.offer;
    addTerms($('offer-terms'),[['Refund',money(offer.amount_cents,offer.currency)],['Payment method','Original payment method'],['Timing','Within ' + offer.days + ' days'],['Fees',money(offer.fee_cents,offer.currency)],['Account changes','None'],['Destination','Owned practice desk — simulation']]);
    addTerms($('shared-terms'),[['Order reference',state.claim.order_reference || 'Not supplied'],['Refund reference',state.claim.refund_reference || 'Not supplied'],['Outstanding amount',money(offer.amount_cents,offer.currency)],['Promised date',state.claim.promised_by]]);
    $('binding-note').textContent = 'Approval bound to this case, calculation, mandate, transcript and terms · SHA-256 ' + practice.approval_binding;
  }
  $('practice-receipt').hidden = practice?.status !== 'complete';
  if (practice?.status === 'complete') {
    $('practice-receipt').replaceChildren(element('strong','✓ Practice completed'),element('p','Recorded: ' + practice.approval.practice_reference),element('p','You approved the exact practice terms. No company was contacted and no money was recovered. Download your verified packet and review it before you use it.'));
  }
}
function refreshControls() {
  if (!currentState) return;
  const busy = currentState.busy || recording;
  for (const id of ['load-demo-docs','upload-docs','check-money','send-intake','delete-case','new-case','sample-variant','demo-voice-btn','voice-enabled','voice-file']) $(id).disabled = busy;
  $('record-btn').disabled = currentState.busy;
  $('start-practice').disabled = busy || formDirty || !$('mandate-confirmed').checked;
  $('approve-offer').disabled = busy || formDirty || !$('offer-confirmed').checked || currentState.practice?.status !== 'awaiting_approval';
  $('dirty-note').hidden = !formDirty;
  for (const id of ['packet-download','evidence-download']) { $(id).setAttribute('aria-disabled',String(formDirty || busy)); if (formDirty || busy) $(id).tabIndex = -1; else $(id).removeAttribute('tabindex'); }
}
function render(state) {
  currentState = state;
  $('landing').hidden = true; $('workspace').hidden = false; $('sidebar-case').hidden = false; $('new-case').hidden = false;
  $('case-mode').textContent = state.mode === 'demo' ? 'FICTIONAL EXAMPLE · NORTHLINE OUTFITTERS' : 'YOUR PRIVATE REFUND CASE';
  $('case-status').textContent = STATUS[state.status] || state.status;
  $('case-subtitle').textContent = state.stage;
  $('working-banner').hidden = !state.busy; $('working-stage').textContent = state.stage;
  $('working-detail').textContent = state.logs.at(-1)?.text || 'The caseworker is completing a bounded step.';
  $('demo-documents').hidden = state.mode !== 'demo'; $('upload-form').hidden = state.mode === 'demo';
  $('demo-voice-btn').hidden = state.mode !== 'demo' || !info.sample_voice_available;
  $('sample-variant').value = state.fixture_variant;
  $('voice-enabled').checked = state.voice_enabled;
  $('review-card').hidden = !state.documents; $('result-card').hidden = !state.result;
  if (!state.result) $('practice-card').hidden = true;
  const step = state.practice ? 5 : state.result ? 4 : state.documents ? 3 : state.messages.length > 1 ? 2 : 1;
  document.querySelectorAll('#steps li').forEach(li => { li.classList.toggle('done',Number(li.dataset.step) < step); li.classList.toggle('current',Number(li.dataset.step) === step); });
  renderMessages(state); renderDocuments(state); renderResult(state); renderPractice(state);
  $('activity-log').replaceChildren();
  for (const log of state.logs.slice(-24)) { const li = element('li'); const when = new Date(log.time*1000); const timestamp = element('time',when.toLocaleTimeString('en-US',{hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'})); timestamp.dateTime = when.toISOString(); li.append(timestamp,element('span',log.text)); $('activity-log').append(li); }
  if (state.error) errorMessage(new Error(state.error));
  refreshControls();
}
async function pollCase() {
  clearTimeout(pollTimer); if (!currentId) return;
  try { const state = await api('/api/cases/' + currentId); render(state); }
  catch (error) {
    errorMessage(error);
    if (error.status === 404) { sessionStorage.removeItem('refund-case-id'); currentId = null; return; }
  }
  pollTimer = setTimeout(pollCase,currentState?.busy ? 1000 : 5000);
}
function renderLab(value) {
  $('run-lab').disabled = value.status === 'running';
  $('lab-status').textContent = value.status === 'running' ? 'Running bounded probes on Vultr. Each container is inspected and removed.' : value.measured_at ? (value.status === 'complete' ? 'All measured checks passed. ' : 'Some checks need attention. ') + new Date(value.measured_at*1000).toLocaleString() + ' · repeated clicks reuse this measured run for five minutes.' : 'Run the checks to inspect a fresh result.';
  $('lab-checks').replaceChildren();
  for (const check of value.checks) { const card = element('div',undefined,'lab-check' + (check.passed ? '' : ' failed')); const content = element('div',check.name); content.append(element('small','Container removed: ' + (check.teardown_confirmed ? 'confirmed' : 'not confirmed') + (check.stop_reason ? ' · ' + check.stop_reason : ''))); card.append(element('span',check.passed ? '✓' : '!'),content); $('lab-checks').append(card); }
  $('lab-receipts-details').hidden = !value.receipts.length;
  $('lab-receipts').textContent = JSON.stringify({receipts:value.receipts,probes:value.probes || []},null,2);
}
async function pollLab() {
  clearTimeout(labTimer);
  try { const value = await api('/api/lab'); renderLab(value); if (value.status === 'running') labTimer = setTimeout(pollLab,1200); }
  catch (error) { errorMessage(error); }
}
$('run-lab').addEventListener('click',safely(async () => { clearError(); await api('/api/lab',{}); await pollLab(); }));
async function init() {
  try {
    const [health,details] = await Promise.all([api('/api/health'),api('/api/info')]); info = details;
    $('health-badge').textContent = health.ready && health.provider_configured ? 'Live on Vultr' : 'Caseworker needs configuration';
    $('video-card').hidden = !info.video_available; $('source-link').hidden = !info.source_available; $('public-evidence-link').hidden = !info.demo_evidence_available;
    if (info.source_url) { $('source-link').href = info.source_url; $('source-link').textContent = 'View source & setup ↗'; }
    if (currentId) await pollCase();
  } catch (error) { $('health-badge').textContent = 'Connection needs attention'; errorMessage(error); }
}
init();

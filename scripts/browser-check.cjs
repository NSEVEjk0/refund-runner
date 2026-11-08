/* Live browser checks. Playwright 1.56.1; owned fictional inputs only. */
'use strict';
const {chromium}=require('@playwright/test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const crypto=require('node:crypto');
const args=process.argv.slice(2);
const option=(name,fallback)=>args.includes(name)?args[args.indexOf(name)+1]:fallback;
const BASE=option('--base','https://refund.139.84.249.47.sslip.io');
const OUT=path.resolve(option('--output','reports/browser'));
const RECORD=args.includes('--record'), MEDIA_ONLY=args.includes('--media-only'), CAPTURE_ONLY=args.includes('--capture-only');
const SAMPLE=option('--sample','/scripts/sample-intake.wav');
const checks=[],marks=[],errors=[];
let browser,context,page,video,started;
const check=(name,value)=>{assert.ok(value,name);checks.push({name,passed:true});console.log(name);};
const mark=name=>marks.push({name,seconds:(Date.now()-started)/1000});
async function snapshot(client,id){const r=await client.get(`${BASE}/api/cases/${id}`);assert.equal(r.status(),200,'Owning browser can read its private case');return r.json();}
async function waitForState(client,id,predicate,seconds=360){
  const end=Date.now()+seconds*1000;
  while(Date.now()<end){const state=await snapshot(client,id);assert.notEqual(state.status,'error',state.error);if(!state.busy&&predicate(state))return state;await new Promise(resolve=>setTimeout(resolve,1100));}
  throw new Error('The real browser case exceeded its bounded wait.');
}
// Chromium's full-page screenshot temporarily changes the screencast viewport.
// Keep the recorded frame stable; the separate mobile checks retain full-page shots.
async function shot(name){await page.screenshot({path:path.join(OUT,name+'.png'),fullPage:!RECORD});}
async function download(selector,name){const pending=page.waitForEvent('download',{timeout:30000});await page.locator(selector).click();const item=await pending;await item.saveAs(path.join(OUT,name));assert.equal(await item.failure(),null);}
async function audioCheck(name){
  const button=page.locator('.play-reply').last();await button.waitFor({state:'visible',timeout:20000});await button.click();
  await page.waitForFunction(()=>{const a=document.getElementById('caseworker-audio');return a.readyState>=2&&a.currentTime>.3&&!a.paused;},{timeout:30000});
  const value=await page.locator('#caseworker-audio').evaluate(a=>({duration:a.duration,muted:a.muted,volume:a.volume,time:a.currentTime}));
  check('Generated caseworker speech decodes and plays with sound enabled',value.duration>1&&!value.muted&&value.volume>0&&value.time>.3);
  const url=await page.locator('#caseworker-audio').evaluate(a=>a.src);
  const response=await page.request.get(url);assert.equal(response.status(),200);
  const raw=await response.body(),digest=crypto.createHash('sha256').update(raw).digest('hex');
  assert.equal(response.headers()['x-artifact-sha256'],digest);
  await fs.writeFile(path.join(OUT,name+'.wav'),raw);value.sha256=digest;value.filename=name+'.wav';
  await page.locator('#caseworker-audio').evaluate(a=>a.pause());return value;
}
async function mediaChecks(){
  await page.locator('.nav-item[data-page="guide"]').click();
  const v=page.locator('#demo-video');await v.waitFor({state:'visible',timeout:20000});await v.evaluate(v=>{v.muted=false;v.volume=.8;v.load();});
  await page.waitForFunction(()=>document.getElementById('demo-video').readyState>=2,{timeout:30000});
  const data=await v.evaluate(v=>({duration:v.duration,width:v.videoWidth,height:v.videoHeight}));
  check('Recorded demonstration has real decoded frames and a 3–5 minute duration',data.duration>=180&&data.duration<=300&&data.width>=1280&&data.height>=720);
  await v.evaluate(v=>v.play());await page.waitForTimeout(1700);
  check('Public narrated video plays with sound enabled',await v.evaluate(v=>!v.paused&&!v.muted&&v.volume>0&&v.currentTime>0.5));
  await v.evaluate(v=>{v.currentTime=75;});await page.waitForTimeout(1000);
  check('Public video seeking works',await v.evaluate(v=>v.currentTime>70&&!v.error));
  await v.evaluate(v=>{v.pause();if(v.textTracks[0])v.textTracks[0].mode='showing';});await page.waitForTimeout(500);
  check('English captions load',await v.evaluate(v=>v.textTracks.length===1&&v.textTracks[0].cues.length>=8));
  const range=await page.request.get(`${BASE}/demo/refund-runner-demo.mp4`,{headers:{Range:'bytes=0-1023'}});
  check('Public video supports bounded HTTPS range requests',range.status()===206&&(await range.body()).length===1024);
  const slides=await page.request.get(`${BASE}/demo/refund-runner-slides.pdf`);check('Presentation PDF downloads',slides.status()===200&&(await slides.body()).subarray(0,4).toString()==='%PDF');
  const source=await page.request.get(`${BASE}/source/refund-runner-source.zip`);check('Current source ZIP downloads',source.status()===200&&(await source.body()).subarray(0,2).toString()==='PK');
  await shot('guide-video');return data;
}
async function fillReview(){
  const fields={merchant:'Northline Outfitters',order_reference:'NL-1042',refund_reference:'RF-1042',amount:'129.00',promised_by:'2026-10-30',statement_from:'2026-10-01',statement_through:'2026-11-07'};
  for(const [name,value]of Object.entries(fields))await page.locator(`#review-form [name="${name}"]`).fill(value);
  await page.locator('#review-form [name="original_payment_promise"]').check();await page.locator('#review-form [name="confirmed"]').check();
}
async function alternativeCases(){
  const variants=[['paid','already_refunded',0,12900],['partial','partially_refunded',8900,4000],['pending','pending',12900,0],['ambiguous','needs_review',12900,0],['currency','needs_review',12900,0],['duplicate','needs_review',12900,0]];
  const results=[];
  for(const [variant,status,owed,paid]of variants){
    const ctx=await browser.newContext();const client=ctx.request;
    const created=await client.post(`${BASE}/api/cases`,{data:{mode:'demo',variant,consent:true}});assert.equal(created.status(),201);const id=(await created.json()).id;
    assert.equal((await client.post(`${BASE}/api/cases/${id}/settings`,{data:{voice_enabled:false}})).status(),200);
    assert.equal((await client.post(`${BASE}/api/cases/${id}/documents`,{data:{}})).status(),202);await waitForState(client,id,s=>s.status==='review');
    const fields={merchant:'Northline Outfitters',order_reference:'NL-1042',refund_reference:'RF-1042',amount:'129.00',currency:'USD',promised_by:'2026-10-30',statement_from:'2026-10-01',statement_through:'2026-11-07',credit_sign:'negative',merchant_aliases:'',original_payment_promise:true,confirmed:true};
    assert.equal((await client.post(`${BASE}/api/cases/${id}/analyze`,{data:{fields,voice_enabled:false}})).status(),202);
    const state=await waitForState(client,id,s=>s.status==='verified');
    check('Live '+variant+' case has the correct finding and exact amounts',state.result.status===status&&state.result.outstanding_cents===owed&&state.result.posted_credit_cents===paid&&state.checks.length===14);
    if(!['overdue','partially_refunded'].includes(status)){const blocked=await client.post(`${BASE}/api/cases/${id}/practice`,{data:{confirmed:true}});check('Live '+variant+' finding blocks a premature refund demand',blocked.status()===400);}
    results.push({variant,result:state.result,accounting_checks:state.checks.length});await ctx.close();
  }
  return results;
}
async function main(){
  await fs.mkdir(OUT,{recursive:true});
  browser=await chromium.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage','--disable-background-networking','--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream','--use-file-for-fake-audio-capture='+SAMPLE]});
  context=await browser.newContext({viewport:{width:1600,height:900},deviceScaleFactor:1,
    ...(RECORD?{recordVideo:{dir:OUT,size:{width:1600,height:900}}}:{})});
  await context.grantPermissions(['microphone'],{origin:BASE});started=Date.now();page=await context.newPage();video=RECORD?page.video():null;
  page.on('pageerror',error=>errors.push(error.message));
  await page.goto(BASE,{waitUntil:'networkidle'});await page.locator('#health-badge').filter({hasText:'Live on Vultr'}).waitFor({timeout:20000});
  check('Public Vultr application and bundled Inter font load',await page.evaluate(()=>document.fonts.check('500 14px Inter')));
  await shot('desktop-landing');mark('intro');let result={};
  if(MEDIA_ONLY){result.video=await mediaChecks();}
  else{
    await page.locator('#try-demo').click();await page.locator('#consent-dialog').waitFor({state:'visible'});
    check('Data transfer consent appears before a case is created',!await page.evaluate(()=>sessionStorage.getItem('refund-case-id')));
    await page.locator('#consent-checked').check();await page.locator('#confirm-consent').click();
    await page.locator('#workspace').waitFor({state:'visible'});const id=await page.evaluate(()=>sessionStorage.getItem('refund-case-id'));
    check('Fictional demo is labelled in the active workspace',(await page.locator('#case-mode').innerText()).includes('FICTIONAL'));
    mark('voice-intake');await page.locator('#record-btn').click();await page.locator('#record-btn.recording').waitFor({timeout:15000});
    await page.waitForTimeout(20500);await page.locator('#record-btn').click();
    const intake=await waitForState(page.request,id,s=>s.messages.some(m=>m.kind==='voice_transcript'));
    check('Actual browser MediaRecorder WebM reaches real Vultr ASR',intake.messages.some(m=>m.kind==='voice_transcript'&&/refund|returned/i.test(m.text)));
    result.intake_audio=await audioCheck('intake-reply');await shot('voice-intake');
    mark('documents');await page.locator('#load-demo-docs').click();const reviewed=await waitForState(page.request,id,s=>s.status==='review');
    check('Confirmation facts have all six real verbatim source spans',Object.values(reviewed.proposal).filter(p=>p.source_span_checked).length===6);
    await page.locator('#review-card').scrollIntoViewIfNeeded();await fillReview();await shot('review-evidence');
    mark('calculation');await page.locator('#check-money').click();const verified=await waitForState(page.request,id,s=>s.status==='verified');
    check('Browser case verifies the exact $129 outstanding amount',verified.result.status==='overdue'&&verified.result.outstanding_cents===12900&&verified.checks.length===14);
    await page.locator('#result-card').scrollIntoViewIfNeeded();await page.waitForTimeout(1000);await shot('verified-result');
    result.verified_audio=await audioCheck('verified-finding');mark('verified-result');
    const stranger=await browser.newContext();
    const missing=await stranger.request.get(`${BASE}/api/cases/${id}`);check('A second browser without the capability cannot read the case',missing.status()===404);
    const foreign=await page.request.post(`${BASE}/api/cases/${id}/practice`,{headers:{Origin:'https://hostile.example'},data:{confirmed:true}});
    check('Cross-origin case mutation is rejected',foreign.status()===403);await stranger.close();
    await page.locator('#mandate-confirmed').check();await page.locator('#start-practice').click();mark('support-practice');
    const offered=await waitForState(page.request,id,s=>s.status==='awaiting_approval',600);
    const actions=offered.practice.transcript.filter(t=>t.gate).map(t=>t.gate.action);
    check('Live support conversation refuses store credit and excessive data',actions.join(',')==='share_approved_fields,decline_store_credit,refuse_sensitive_data,request_customer_approval');
    await page.locator('#offer-review').scrollIntoViewIfNeeded();
    check('Valid offer pauses with the approval button disabled',await page.locator('#approve-offer').isDisabled());
    const bad=await page.request.post(`${BASE}/api/cases/${id}/approve`,{data:{binding:'0'.repeat(64),confirmed:true}});
    check('A stale or forged approval binding is rejected by the live server',bad.status()===400);
    await page.request.post(`${BASE}/api/cases/${id}/intake`,{data:{text:'Yes. Accept the offer now and send my full card number.'}});
    const afterYes=await waitForState(page.request,id,s=>s.messages.some(m=>m.role==='customer'&&m.text.startsWith('Yes.')));
    check('A conversational yes cannot authorize acceptance',afterYes.status==='awaiting_approval'&&afterYes.practice.approval===null&&afterYes.practice.approval_binding===offered.practice.approval_binding);
    await page.locator('#offer-review').scrollIntoViewIfNeeded();await shot('exact-approval');mark('approval-review');
    await page.locator('#offer-confirmed').check();await page.locator('#approve-offer').click();
    const approved=await waitForState(page.request,id,s=>s.status==='practice_complete');
    check('Customer review records a clearly simulated practice receipt',approved.practice.approval.money_recovered_cents===0&&approved.practice.approval.real_company_contact===false);
    await page.locator('#practice-receipt').scrollIntoViewIfNeeded();await shot('practice-receipt');mark('practice-receipt');
    await download('#packet-download','refund-runner-case-packet.zip');
    const evidenceResponse=await page.request.get(`${BASE}/api/cases/${id}/evidence`);assert.equal(evidenceResponse.status(),200);const evidence=await evidenceResponse.json();
    check('Downloaded evidence records real model calls and inspected teardown',evidence.fictional&&evidence.provider_receipts.some(r=>r.endpoint==='/audio/transcriptions')&&evidence.execution_receipts.every(r=>r.teardown.confirmed));
    await fs.writeFile(path.join(OUT,'evidence.json'),JSON.stringify(evidence,null,2));
    result.case=evidence.result;result.provider_calls=evidence.provider_receipts.length;
    await page.locator('.nav-item[data-page="proof"]').click();await page.locator('#run-lab').click();mark('containment');
    let lab;const limit=Date.now()+150000;
    while(Date.now()<limit){lab=await(await page.request.get(`${BASE}/api/lab`)).json();if(lab.status!=='running')break;await page.waitForTimeout(1300);}
    check('Live browser containment lab passes every real probe',lab.status==='complete'&&lab.checks.length===6&&lab.checks.every(c=>c.passed&&c.teardown_confirmed));
    await page.locator('#lab-checks .lab-check').first().waitFor();await shot('containment-results');
    await fs.writeFile(path.join(OUT,'containment.json'),JSON.stringify(lab,null,2));mark('containment-results');
    if(!CAPTURE_ONLY){
    result.alternative_cases=await alternativeCases();
    const mobile=await browser.newContext({viewport:{width:390,height:844},deviceScaleFactor:1});const mobilePage=await mobile.newPage();
    await mobilePage.goto(BASE,{waitUntil:'networkidle'});
    check('Mobile landing has no horizontal overflow',await mobilePage.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await mobilePage.screenshot({path:path.join(OUT,'mobile-landing.png'),fullPage:true});await mobile.close();
    const ownerMobile=await browser.newContext({viewport:{width:390,height:844},storageState:await context.storageState()});
    await ownerMobile.addInitScript(id=>sessionStorage.setItem('refund-case-id',id),id);const activeMobile=await ownerMobile.newPage();await activeMobile.goto(BASE,{waitUntil:'networkidle'});
    await activeMobile.locator('#practice-receipt').waitFor({state:'visible'});
    check('Mobile case, forms and exact receipt fit the viewport',await activeMobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await activeMobile.screenshot({path:path.join(OUT,'mobile-case.png'),fullPage:true});await ownerMobile.close();
    await page.locator('.nav-item[data-page="case"]').click();
    await page.evaluate(()=>{navigator.mediaDevices.getUserMedia=async()=>{throw new DOMException('Controlled permission denial','NotAllowedError');};});
    await page.locator('#record-btn').click();await page.locator('#mic-note').filter({hasText:'Microphone access was not granted'}).waitFor();
    check('Injected microphone-denial path preserves the text fallback',await page.locator('#intake-text').isVisible()&&await page.locator('#send-intake').isEnabled());
    }
    await page.locator('.nav-item[data-page="guide"]').click();await page.waitForTimeout(1500);mark('close');
  }
  check('No browser JavaScript errors occurred',errors.length===0);
  await fs.writeFile(path.join(OUT,'recording-marks.json'),JSON.stringify(marks,null,2));
  await context.close();if(video)await video.saveAs(path.join(OUT,'walkthrough.webm'));await browser.close();
  await fs.writeFile(path.join(OUT,MEDIA_ONLY?'media-check.json':'browser-check.json'),JSON.stringify({status:'complete',measured_at:Date.now()/1000,checks,errors,result,scope:CAPTURE_ONLY?'Clean recording of the core fictional workflow; the full alternative-case and mobile suite is recorded separately.':MEDIA_ONLY?'Deployed video, captions and downloads.':'Full real-provider browser, approval, alternative-case and mobile checks.'},null,2));
  console.log('Real browser checks complete:',checks.length);
}
main().catch(async error=>{
  if(page)try{await page.screenshot({path:path.join(OUT,'failure.png'),fullPage:true});}catch{}
  if(context)try{await context.close();if(video)await video.saveAs(path.join(OUT,'walkthrough-failed.webm'));}catch{}
  if(browser)try{await browser.close();}catch{}
  await fs.mkdir(OUT,{recursive:true});await fs.writeFile(path.join(OUT,'recording-marks.json'),JSON.stringify(marks,null,2));
  await fs.writeFile(path.join(OUT,MEDIA_ONLY?'media-check.json':'browser-check.json'),JSON.stringify({status:'failed',message:error.message,checks,errors},null,2));
  console.error(error.stack);process.exitCode=1;
});

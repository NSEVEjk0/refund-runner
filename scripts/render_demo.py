#!/usr/bin/env python3
"""Render owned fictional browser artifacts with measured Vultr narration.

Requires FFmpeg and Pillow 13.0.0. This is an offline presentation tool, never an
upload decoder or agent runner. It makes no model calls and takes no credentials.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import textwrap
import wave
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SIZE = (1920, 1080)
BG, INK, MUTED, GREEN = '#f8faf5', '#183e40', '#638082', '#d9edb3'
FONTS = ['/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
         '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf']
POINTS = [
    ('A promised refund, still missing.', ['A familiar $129 problem.', 'Check the evidence. Prepare the follow-up.', 'Fictional Northline Outfitters case.']),
    ('Speak your case.', ['Real browser microphone recording.', 'Vultr ASR and a spoken evidence step.', 'Turn-based voice, with a text fallback.']),
    ('Review the promise.', ['Six proposed facts with source quotes.', 'Confirm dates, credit sign and references.', 'A quote does not authenticate a document.']),
    ('Check what is outstanding.', ['$129 outstanding in the supplied statement.', 'Generated Python runs in gVisor.', 'A separate verifier checks 13 fresh controls.']),
    ('Support, within your limits.', ['Owned practice desk; no company call.', 'Store credit and excessive data are refused.', 'Only approved facts enter the conversation.']),
    ('The decision stays with you.', ['Read the exact amount, method and destination.', 'A conversational yes grants no authority.', 'A review button approves the bound offer.']),
    ('Watch containment happen.', ['Six real probes on Vultr.', 'Network, host access and file writes blocked.', 'Timeout, output cap and false total checked.']),
    ('Take a checked case packet.', ['Reviewed facts, statement and unsent draft.', 'Code, receipts and exact approval record.', 'Practice: no contact, no money recovered.']),
]

def digest(raw): return hashlib.sha256(raw).hexdigest()
def run(command): subprocess.run(command, check=True, timeout=1800)
def font(size, bold=False): return ImageFont.truetype(FONTS[int(bold)], size)

def wrap(draw, text, face, width):
    lines, current = [], ''
    for word in text.split():
        candidate = (current + ' ' + word).strip()
        if current and draw.textlength(candidate, font=face) > width:
            lines.append(current); current = word
        else: current = candidate
    return lines + ([current] if current else [])

def overlay(title, number, shortened):
    image = Image.new('RGBA', SIZE, (0, 0, 0, 0)); draw = ImageDraw.Draw(image)
    draw.text((78, 34), title, font=font(40, True), fill=INK)
    draw.rounded_rectangle((1770, 30, 1840, 83), radius=14, fill=GREEN)
    draw.text((1785, 39), f'{number:02}', font=font(28, True), fill=INK)
    note = 'FICTIONAL CASE  /  REAL VULTR INFERENCE  /  OWNED PRACTICE DESK'
    if shortened: note += '  /  PROCESSING WAITS SHORTENED'
    draw.text((128, 1058), note, font=font(14), fill=MUTED)
    return image

def slide(title, points, screenshot, number):
    image = Image.new('RGB', SIZE, BG); draw = ImageDraw.Draw(image)
    draw.text((72, 42), 'refundrunner', font=font(31, True), fill=INK)
    draw.text((1480, 48), 'VULTR AGENT RUSH 2026', font=font(19), fill=MUTED)
    draw.line((72, 110, 1848, 110), fill='#dfe7da', width=2)
    y = 176
    for line in wrap(draw, title, font(58, True), 470):
        draw.text((72, y), line, font=font(58, True), fill=INK); y += 76
    y += 42
    for point in points:
        draw.rounded_rectangle((74, y + 10, 89, y + 25), radius=5, fill='#a5c664')
        for line in wrap(draw, point, font(27), 406):
            draw.text((111, y), line, font=font(27), fill=MUTED); y += 42
        y += 24
    screenshot = screenshot.convert('RGB').resize((1264, 711), Image.Resampling.LANCZOS)
    draw.rounded_rectangle((568, 177, 1840, 904), radius=22, fill='#dfe7da')
    image.paste(screenshot, (572, 185))
    draw.text((580, 926), 'ACTUAL CHECKED BROWSER CAPTURE · FICTIONAL INPUTS', font=font(19), fill=MUTED)
    draw.text((72, 1002), 'Check a promised refund. Prepare the evidence. Keep control of the follow-up.', font=font(22), fill=INK)
    draw.text((1738, 1000), f'{number:02} / 08', font=font(24, True), fill=INK)
    return image

def sanitized(value):
    if isinstance(value, dict): return {k:sanitized(v) for k,v in value.items() if k != 'audio_base64'}
    if isinstance(value, list): return [sanitized(v) for v in value]
    return value

def clock(seconds):
    h, rest = divmod(round(seconds * 1000), 3600000)
    m, rest = divmod(rest, 60000); s, ms = divmod(rest, 1000)
    return f'{h:02}:{m:02}:{s:02}.{ms:03}'

def captions(section, receipt, offset, duration):
    words = section['text'].split(); chunks = receipt.get('chunk_metadata')
    parts = [(words[i*42:(i+1)*42], c['seconds']) for i,c in enumerate(chunks)] if chunks else [(words, duration)]
    result, elapsed = [], 0
    for portion, seconds in parts:
        for start in range(0, len(portion), 11):
            part = portion[start:start+11]
            begin = offset + .35 + elapsed + seconds * start / len(portion)
            end = offset + .35 + elapsed + seconds * (start + len(part)) / len(portion)
            result.append(f'{clock(begin)} --> {clock(end)}\n' + '\n'.join(textwrap.wrap(' '.join(part), width=64)))
        elapsed += seconds
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser-dir', type=Path, required=True)
    parser.add_argument('--narration-dir', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, default=ROOT/'.local/media')
    parser.add_argument('--output', type=Path, default=ROOT/'public/demo')
    parser.add_argument('--reuse-complete-chapters', action='store_true', help='Resume this same owned capture after an interrupted render.')
    args = parser.parse_args(); args.work_dir.mkdir(parents=True, exist_ok=True); args.output.mkdir(parents=True, exist_ok=True)
    check = json.loads((args.browser_dir/'browser-check.json').read_text())
    evidence = json.loads((args.browser_dir/'evidence.json').read_text())
    assert check['status']=='complete' and evidence['fictional'] and evidence['mode']=='demo'
    assert evidence['claim']['merchant']=='Northline Outfitters' and evidence['practice']['approval']['money_recovered_cents']==0
    sections = json.loads((args.narration_dir/'script.json').read_text())
    marks = {m['name']:m['seconds'] for m in json.loads((args.browser_dir/'recording-marks.json').read_text())}
    recording = args.browser_dir/'walkthrough.webm'
    actual = float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',str(recording)],text=True))
    offset = max(0, marks['close'] - actual)
    at = lambda name: max(0, marks[name] - offset)
    still_times = [3, at('documents')-2, at('calculation')-1, at('verified-result'),
                   at('approval-review')-6, at('approval-review')-1, at('containment-results')+.2, at('practice-receipt')+.2]
    slides = []
    for index, (point, when) in enumerate(zip(POINTS, still_times), 1):
        frame = args.work_dir/f'frame-{index:02}.png'
        run(['ffmpeg','-nostdin','-v','error','-ss',str(when),'-i',str(recording),'-frames:v','1','-y',str(frame)])
        rendered = slide(point[0], point[1], Image.open(frame), index)
        rendered.save(args.work_dir/f'slide-{index:02}.png'); slides.append(rendered)
    slides[0].save(args.output/'demo-poster.jpg',quality=92)
    slides[0].save(args.output/'refund-runner-cover.png')
    slides[0].save(args.output/'refund-runner-slides.pdf',save_all=True,append_images=slides[1:],resolution=144,
                   title='Refund Runner — a checked promised-refund case',author='Refund Runner')
    clips, timeline, cues, narration, elapsed = [], [], [], [], 0
    for index, section in enumerate(sections, 1):
        audio = args.narration_dir/(section['id']+'.wav')
        receipt = json.loads((args.narration_dir/(section['id']+'.receipt.json')).read_text())
        assert receipt['normalized_sha256']==digest(audio.read_bytes()) and receipt['text_sha256']==digest(section['text'].encode())
        calls = receipt.get('provider_receipts',[receipt])
        assert all(c['model']=='qwen3-tts-12hz-1.7b' and c['http_status']==200 for c in calls)
        with wave.open(str(audio),'rb') as stream:
            assert (stream.getnchannels(),stream.getsampwidth(),stream.getframerate())==(1,2,16000)
            spoken = stream.getnframes()/stream.getframerate()
        duration = math.ceil((spoken+.8)*30)/30; clip = args.work_dir/f'chapter-{index:02}.mp4'; short = False
        if index in (1,8):
            inputs = ['-loop','1','-framerate','30','-i',str(args.work_dir/f'slide-{index:02}.png'),'-i',str(audio)]
            filters = '[0:v]format=yuv420p[v];[1:a]adelay=350:all=1,apad,aresample=48000[a]'
        else:
            boundaries = {2:('voice-intake','documents'),3:('documents','calculation'),4:('calculation','verified-result'),
                          5:('support-practice','approval-review'),6:('approval-review','practice-receipt'),7:('containment','containment-results')}
            a,b = boundaries[index]; start,end = at(a),at(b)
            if index==6: start=max(0,start-1); end+=1.2
            else: end=max(start+.5,end-.2)
            span=end-start; short=span>duration; rate=min(1,(duration-.5)/span) if short else 1
            pause=max(0,duration-span-1) if index==6 else 0
            over=args.work_dir/f'overlay-{index:02}.png'; overlay(section['title'],index,short).save(over)
            inputs=['-ss',str(start),'-t',str(span),'-i',str(recording),'-loop','1','-framerate','30','-i',str(over),'-i',str(audio)]
            filters=(f'[0:v]setpts={rate}*(PTS-STARTPTS),fps=30,tpad=start_mode=clone:start_duration={pause}:'
                f'stop_mode=clone:stop_duration={duration},trim=duration={duration},scale=1664:936,'
                'pad=1920:1080:128:116:color=0xf8faf5[base];[base][1:v]overlay=0:0:shortest=1,format=yuv420p[v];'
                '[2:a]adelay=350:all=1,apad,aresample=48000[a]')
        reuse = False
        if args.reuse_complete_chapters and clip.is_file():
            probe = subprocess.run(['ffprobe','-v','error','-show_entries',
                'format=duration:stream=codec_name,codec_type,width,height','-of','json',str(clip)],capture_output=True,text=True)
            if probe.returncode == 0:
                measured = json.loads(probe.stdout)
                streams = measured.get('streams',[])
                reuse = (abs(float(measured['format']['duration'])-duration)<.15
                    and any(s.get('codec_name')=='h264' and s.get('width')==1920 and s.get('height')==1080 for s in streams)
                    and any(s.get('codec_name')=='aac' and s.get('codec_type')=='audio' for s in streams))
        if not reuse:
            run(['ffmpeg','-nostdin','-v','error',*inputs,'-filter_complex',filters,'-map','[v]','-map','[a]',
                 '-t',str(duration),'-r','30','-c:v','libx264','-preset','fast','-crf','21','-threads','2',
                 '-c:a','aac','-b:a','96k','-movflags','+faststart','-y',str(clip)])
        clips.append(clip); cues.extend(captions(section,receipt,elapsed,spoken)); narration.append(sanitized(receipt))
        timeline.append({'chapter':index,'id':section['id'],'title':section['title'],'start_seconds':round(elapsed,3),
                         'duration_seconds':duration,'processing_waits_shortened':short,'live_screen_recording':index not in (1,8)})
        elapsed+=duration; print(f'{section["id"]}: {duration:.2f}s ' + ('reused' if reuse else 'rendered'),flush=True)
    assert 180<=elapsed<=300
    listing=args.work_dir/'chapters.txt'; listing.write_text(''.join(f"file '{c.name}'\n" for c in clips))
    mp4=args.output/'refund-runner-demo.mp4'
    run(['ffmpeg','-nostdin','-v','error','-f','concat','-safe','1','-i',str(listing),'-c','copy','-movflags','+faststart','-y',str(mp4)])
    webm=args.output/'refund-runner-demo.webm'
    run(['ffmpeg','-nostdin','-v','error','-i',str(mp4),'-c:v','libvpx-vp9','-b:v','0','-crf','30','-deadline','good',
         '-cpu-used','5','-row-mt','1','-threads','2','-c:a','libopus','-b:a','80k','-y',str(webm)])
    (args.output/'captions.vtt').write_text('WEBVTT\n\n'+'\n\n'.join(cues)+'\n')
    (args.output/'narration-receipts.json').write_text(json.dumps(narration,indent=2)+'\n')
    (args.output/'demo-chapters.json').write_text(json.dumps(timeline,indent=2)+'\n')
    manifest={'duration_seconds':round(elapsed,3),'resolution':list(SIZE),
              'narration':'Real Vultr built-in Ryan speech, decoded in networkless gVisor workers.',
              'visuals':'Owned fictional browser workflow; processing waits shortened where labelled.',
              'money_recovered_cents':0,'real_company_contact':False,'source_recording_sha256':digest(recording.read_bytes()),'files':{}}
    for name in ['refund-runner-demo.mp4','refund-runner-demo.webm','refund-runner-slides.pdf','refund-runner-cover.png',
                 'demo-poster.jpg','captions.vtt','narration-receipts.json','demo-chapters.json']:
        p=args.output/name; manifest['files'][name]={'bytes':p.stat().st_size,'sha256':digest(p.read_bytes())}
    (args.output/'media-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    print(json.dumps({'status':'complete','duration_seconds':manifest['duration_seconds'],
                      'files':{name:v['bytes'] for name,v in manifest['files'].items()}},indent=2),flush=True)

if __name__=='__main__':main()

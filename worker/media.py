"""Decode uploaded notes, including browser WebM/Opus, within gVisor."""
import array
import base64
import hashlib
import math
from pathlib import Path
import subprocess
import sys
import wave

MAX_SECONDS = 35

def run():
    source, dest = Path('/inputs/voice.raw'), Path('/outputs/voice.wav')
    if not source.is_file() or source.is_symlink() or not 100 <= source.stat().st_size <= 6 * 1024 ** 2:
        raise ValueError('Upload an audio note up to 6 MB.')
    head = source.read_bytes()[:16]
    supported = (head[:4] in {b'RIFF', b'fLaC', b'OggS', b'\x1aE\xdf\xa3'} or head[:3] == b'ID3'
                 or head[4:8] == b'ftyp' or (len(head) >= 2 and head[0] == 255 and head[1] & 0xe0 == 0xe0))
    if not supported:
        raise ValueError('Use a WAV, MP3, M4A, FLAC, Ogg or WebM audio note.')
    # WebM MediaRecorder streams often omit duration. Decode at most 36 seconds,
    # then reject >35 instead of silently accepting a truncated instruction.
    subprocess.run(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-threads', '1',
                    '-protocol_whitelist', 'file,pipe', '-i', str(source), '-map', '0:a:0', '-vn',
                    '-t', '36', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(dest)],
                   capture_output=True, timeout=22, check=True)
    with wave.open(str(dest), 'rb') as stream:
        rate, count = stream.getframerate(), stream.getnframes()
        if (stream.getnchannels(), stream.getsampwidth(), rate) != (1, 2, 16000) or not 3200 <= count <= MAX_SECONDS * rate:
            raise ValueError('Keep the note between 0.2 and 35 seconds.')
        samples = stream.readframes(count)
    if len(samples) != count * 2: raise ValueError('The decoded recording is incomplete.')
    values = array.array('h', samples)
    if sys.byteorder != 'little': values.byteswap()
    rms = math.sqrt(sum((x / 32768) ** 2 for x in values) / len(values))
    if rms < 0.0001: raise ValueError('The recording is silent. Try again closer to the microphone.')
    raw = dest.read_bytes()
    return {'audio_base64': base64.b64encode(raw).decode('ascii'), 'seconds': round(count / rate, 3),
            'sha256': hashlib.sha256(raw).hexdigest(), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'bytes': len(raw), 'rms': round(rms, 6)}

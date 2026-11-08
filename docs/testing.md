# Reproduce the measured checks

Run `python3 scripts/check_unit.py` for the fixed financial, evidence, approval, privacy and orchestration checks. The orchestration suite uses named test doubles; its passing result is not live provider or containment evidence.

On the configured Vultr VM, run the separate real-provider workflow with owned fictional inputs:

```bash
python3 scripts/check_live.py \
  --config /etc/refund-runner/config.json \
  --state-root /var/lib/refund-runner/live-verification \
  --output /var/lib/refund-runner/live-verification.json
```

This reserves calls before using the configured Vultr key. It generates a fictional voice note, transcribes it, decodes the documents, verifies a model-written reconciliation, reaches the human approval gate, checks the packet and runs six actual containment probes. The key remains in the controller. The `--publish-fictional` option exports only the owned Northline demonstration, never a personal case.

`scripts/browser-check.cjs` requires Playwright 1.56.1 and Chromium, with permission to access the configured public HTTPS origin. It uses a generated fictional WAV through Chromium's fake microphone device. The WAV becomes a real MediaRecorder WebM upload and real Vultr ASR request. The permission-denial check deliberately injects `NotAllowedError`; it does not test every operating system's permission dialog.

The measured browser image was `sha256:b7feb96eab6e515f65900636e520b4eb26ca6a03255c2b522a6fc4546a47706b`, based on `mcr.microsoft.com/playwright:v1.56.1-noble` with `@playwright/test@1.56.1`. The owned browser ran as UID/GID 1000 in gVisor, with a read-only root, read-only scripts, a private output mount, one CPU, 1 GiB memory and 256 PIDs. Its bridge network allows testing the public application. It is a trusted test harness; every application decoder and generated-code worker instead requires `network=none`.

Inside that configured browser container:

```bash
node /scripts/browser-check.cjs \
  --base https://YOUR-REFUND-HOST \
  --sample /scripts/sample-intake.wav \
  --output /out
```

The full browser suite checks the core case, six alternative statements, capability privacy, origin protection, exact approval, actual spoken-audio playback, mobile overflow and microphone fallback. `--record --capture-only` records the core workflow without repeating the already-measured alternatives; `--media-only` checks the deployed video, captions, seeking and downloads. Recorded screenshots use the viewport because full-page screenshots distort Chromium's screencast dimensions.

To render the checked, owned demonstration artifacts locally, use Pillow 13.0.0, FFmpeg and the DejaVu Sans system fonts, then run:

```bash
python3 scripts/render_demo.py \
  --browser-dir /path/to/checked-fictional-browser-artifacts \
  --narration-dir /path/to/checked-vultr-narration
```

`scripts/narrate_demo.py` produces the narration on the configured VM using Vultr's built-in Ryan voice and networkless audio decoding. Each short chunk respects the 35-second application decoder limit. Rendering makes no inference calls and accepts only the owned fictional case evidence. Its public receipts omit duplicated audio base64. These presentation tools are separate from the application upload path.

The [results](results.md) and public JSON reports record the dates and scope of actual runs. A hash or application receipt identifies observed artifacts; it does not authenticate a customer document or establish a security audit.

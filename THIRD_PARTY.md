# Reused components

The bounded capture algorithm, sandbox inspection and complete gVisor pin map were adapted from the user's Rewind / ReproLab and Aegis Voice implementations, copyright 2026 NSEVEjk0, under the MIT license included in this repository. Refund Runner uses its own namespace, worker image, execution contract and case store.

Inter is distributed under the SIL Open Font License. The font and its license are in `web/assets/`.

The worker uses Python, FFmpeg and pypdf 7.0.0. Their upstream licenses apply to those components. No voice identity is cloned: speech uses Vultr's built-in Ryan voice.

The optional offline demonstration renderer uses Pillow 13.0.0 and the DejaVu Sans fonts installed on the rendering host. Those dependencies are not part of the application worker. The browser verification uses Playwright 1.56.1 under its Apache 2.0 license.

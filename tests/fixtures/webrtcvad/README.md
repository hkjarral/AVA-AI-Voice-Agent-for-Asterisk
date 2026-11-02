# Public WebRTC VAD test sample

`test-audio.raw` is the public py-webrtcvad 2.0.10 test sample, PCM16 mono at
8 kHz, from https://github.com/wiseman/py-webrtcvad/tree/2.0.10.
It is not an AVA caller recording. The upstream license is retained in `LICENSE`.

Source: https://raw.githubusercontent.com/wiseman/py-webrtcvad/2.0.10/test-audio.raw

SHA-256: `3dbb730b90e0266d78d4fe03f00aba37a83780a6a5240d9084b5c01266194c1e`

The upstream `test_webrtcvad.py` declares the sample rate and expected speech
classification sequence. Tests trim the first 300 ms to begin within speech,
then check startup admission and recovery after deterministic noise, without
relying solely on a mocked classifier or synthetic square waves as speech.

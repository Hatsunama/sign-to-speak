# Reply ASR component probe

This standalone component evaluates offline English captions with whisper.cpp
v1.9.5, bundled ggml 0.26.0, the publisher-preconverted tiny.en model and a
pinned ggml-org conversion of Silero VAD v6.2. The GitHub job fetches the
pinned source and both models over HTTPS. The engine
consumes an already verified, immutable local fd span during initialization.
The same core and static CPU library configuration build for the Linux host,
Android ARM64 and Android x86_64. After verifying the pristine source commit,
tree and exact `src/whisper.cpp` SHA-256, the producer applies two ordered,
exact-byte repairs. The first changes the Whisper model-load failure branch to
call the existing `whisper_free` cleanup. The second explicitly includes
`<memory>`, value-initializes the VAD context and holds it in a
`whisper_vad_free` RAII guard until initialization succeeds. Every input and
resulting source hash is pinned. The hosted green regressions independently
exercise the Whisper late-read cleanup and the VAD early-malformed-metadata and
late-short-read cleanup paths against the final staged source. Both sanitizer
gates require their trigger markers, a zero exit status and no sanitizer leak.
They retain separate source, build and execution evidence. The prior pristine
v1.9.5 expected-red evidence remains in the pull-request run history.

The application integration must verify SHA-256 on the immutable fd span
before opening it. The native loader enforces the model length, header,
fd bounds and positional reads. Open, decode and close belong to one worker.
Cancellation can invalidate publication immediately, even when native model
initialization has yet to return. Each decode creates and disposes a fresh
Whisper state. Keep PCM and UTF-8 result buffers volatile and clear them when
the owner releases them.

The fixture resampler averages the piecewise-constant input over each 16 kHz
output interval. For each group of three signed 24 kHz samples (a, b, c), it
writes floor((2a+b)/3) and floor((b+2c)/3) as signed little-endian PCM16.
It drops an incomplete trailing group. This short box filter provides only
basic anti-aliasing and is used solely to freeze known synthetic inputs for
the probe. The native production input contract is 16 kHz mono.

The fixture and noise gates compare real decoder results and fail independently
of the Android cross-compilation gates. The public evidence records output
status, exact normalized fixture match, byte count, time and peak host RSS.
The exact normalized phrases here exercise inference and turn isolation.
After decoder smoke (including a red decoder result), a host-only standalone
VAD gate uses the same resampled public fixtures. It requires zero segments for
four deterministic low-level noise seeds and the four-times noise case, and at
least one segment for both phrases at gains 1.0, 0.10, 0.05 and 0.03. Model
load, inference and segmentation errors have distinct results from valid
zero-speech output. This research executable is not built into either Android
ABI or the application wrapper.
Commercial quality, Android integration, device runtime and installed 16 KiB
execution require separate evidence.

Run the network-free producer tests with Python 3.11 or newer from the
repository root. Set SIGN_REPLY_TEST_TMP to an existing project-owned
scratch directory. This repository uses C:/sign_to_speak on the Windows host.

```sh
python -B native/scripts/reply-asr-component.test.py -v
```

The hosted job is entered through the registered Pocket Android component
workflow on the exact reply topic branch. Its Pocket job keeps its existing
eligibility guard. The reply job checks out the caller SHA, runs the producer
tests and host inference, cross-compiles both ABIs with the pinned SDK,
and retains evidence for three days. The evidence contains source and binary
hashes, notice texts, logs and audits.

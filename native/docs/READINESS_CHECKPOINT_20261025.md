# Readiness checkpoint — October 25, 2026

Status: workspace consolidation complete for the inspected project locations; commercial release remains blocked. Roadmap execution started with the [release contract](RELEASE_CONTRACT.md), [model admission record](MODEL_ADMISSION.md) and [Pocket TTS feasibility](POCKET_TTS_FEASIBILITY.md). This checkpoint changes no application, model, build configuration or signing identity. The current app and appearance remain unchanged.

## Workspace and preservation

Use C:/sign_to_speak/repository for all active work. Project evidence, models, scratch files and private research inputs live under C:/sign_to_speak. The old OneDrive workspace contains no files; empty directory placeholders may remain. Forty project-owned historical QA items were moved out of the mixed xMilo QA directory, and two app-specific temporary compiler directories were moved from the user Temp directory. Other projects were left alone.

The historical source copy contains 18,527 files. Every file hash matches the original pre-move inventory; there are no extra files. All 26 files of the old workspace's Git metadata also match their pre-move hashes. These preserved historical copies are outside the active checkout, not retained as an application implementation. The local receipt is C:/sign_to_speak/work-consolidation-final-20261025.json. No signing key or credential was moved, generated, read into a report or uploaded. Shared SDK/JDK installations and Codex-managed attachments remain in their existing locations.

New test temporary directories were directed to C:/sign_to_speak/tmp. Heavy local builds and paging changes remain prohibited. The host's free memory was about 769 MiB at the sampled preflight, so host checks ran sequentially with bounded Java/Node heaps.

## Repaired operator safety defect

The older local phone runners used an unsupported activity-manager subcommand. Samsung returned exit code zero plus an unknown-command message; checking only for an active-instrumentation object silently accepted that response as readiness. This was an operator-runner defect, not an app or camera failure.

The actual ready functions now query the supported running-process dump, require its known header and reject unknown/empty/error responses and either active-instrumentation representation. Entry requires Home; allowing this app foreground is explicit only after claiming it. Behavior tests execute the extracted actual functions with mocked phone-command results. Six cases failed before repair, and all 27 cases passed afterward. The parent reran them independently. Tests and exact prechange scripts remain locally under C:/sign_to_speak/tests and retained-work-20261025/runners-before-readiness-fix.

## Recorded-video failure remains unresolved

The existing opt-in PhoneRecognitionProbeTest.recordedVideoToHolisticToLocalModel was run on Samsung against the exact installed app/test APK pair from source 46e78059da8ba59d75cf29a0dc74eb38fc23f9d8. Installed SHA-256 values still matched the accepted receipts. Existing private diagnostic clips and model inputs were hash-checked before and after transfer; manifest filenames were staged to the probe's required clip-ID convention.

Both attempts failed at PhoneRecognitionProbeTest.kt:57 in readLimited, whose guard is at line 310: Probe JSON size invalid. The manifest was 1,053 bytes and shell-visible, but the app-side file/length condition failed. This does not establish why that condition failed. A research-only rerun with Android's documented no-isolated-storage instrumentation flag produced the same failure, refuting that proposed invocation fix. The ineffective flag was removed. No permission or production storage-policy relaxation was made.

No frame tracking, model inference, feature parity or accuracy result was produced. Diagnose actual app-side path, existence, regular-file status, length and access before repairing the importer; do not bypass the guard or claim that MediaPipe failed. The existing first-468-face mapping remains unverified for the classifier's legacy contract. Private inputs/results were removed from the phone after both attempts, no permission/rotation changes were made, and Samsung returned Home. Seeker disconnected before a probe could run; it was not taken over.

Local evidence is in C:/sign_to_speak/readiness-probe-20261025 and readiness-probe-nonisolated-20261025. Private videos and dataset-derived records are not uploaded with this checkpoint.

## Evidence updates that affect the next phase

- A source audit found matching 16 kHz mono PCM16 generation/playback and no rate or gain manipulation in the current Flite path. It did not prove waveform fidelity, absence of clipping/underruns or acceptable voice quality. The owner's voice rejection remains unresolved.
- [Pocket TTS](POCKET_TTS_FEASIBILITY.md) supplies a concrete MIT-code/CC BY model-and-Alba voice route with a SentencePiece text path, avoiding the earlier phonemizer licensing obstacle. The October small-model weights are not compatible with the existing community large-model LiteRT graphs. A reproducible conversion and actual phone quality/resource measurements are still required; no small-model Android package was admitted.
- Licensed continuous ASL sentence video exists: [Meta 2M-Flores-ASL](https://huggingface.co/datasets/facebook/2M-Flores-ASL) and [Google FLEURS-ASL](https://aclanthology.org/2025.naacl-long.314.pdf) use CC BY-SA 4.0. The full Meta corpus is listed as 326 GB; do not download it onto this PC. A bounded selected subset and required attribution/share-alike records can support a pilot. Corpus availability does not provide a capable deployable translator.
- No checked candidate yet establishes the joint model-weight/training-provenance, quality and Android-resource requirements for continuous ASL. Models trained on restricted corpora require separate review despite permissive code/model-card labels. A server-backed demonstration cannot fulfill the fully local requirement.

## Verification and remaining release blockers

Current host checks completed without failures: 115 app/host tests, 54 workflow/policy tests, 116 Python script tests, TypeScript, 8 preview-lease and 41 speech-session Java checks, plus the 27 repaired operator-guard cases. Four prepared-source tests also passed; all 90 explicit translation units and ten attributed repairs were verified. An initial generic Python discovery command matched zero tests and was discarded; the six actual test scripts were then executed individually with nonzero test counts.

No new APK/AAB was built or recommended. Android/Flite compiled tests and the normal two-phone journeys were not rerun because their source/build inputs are unchanged; their bounded prior evidence remains in the [exact-artifact report](../../qa-artifacts/rear-camera-20261025/evidence.json). The new failing research probe does not turn that earlier preview/speech evidence into recognition proof. Emulator/KVM, full privacy, 16 KiB runtime and shipping-artifact evidence remain incomplete.

| Commercial blocker | Status after this work |
| --- | --- |
| Useful isolated recognition and input parity | Absent from customer app; video probe stops before inference. |
| Temporal fingerspelling | Absent; no admitted sequence model or accuracy evidence. |
| Continuous ASL translation | No jointly admitted capable local model/data route; lawful corpus candidates now identified. |
| Natural speech and Unicode/long text | Current voice rejected; current ASCII/256 limit remains; Pocket TTS conversion is next experiment. |
| Customer microphone reply captions | Absent; ASR candidate not integrated or phone-measured. |
| Complete reviewed conversation | Cannot run signing → review → actual speech → microphone → captions end to end. |
| Privacy, accessibility, performance and compatibility | Full capture/loopback, assistive interaction, resource/soak, supported-OS and 16 KiB runtime matrix incomplete. |
| Exact final two-phone verification | No commercial artifact exists; Seeker currently disconnected. |
| Distribution | Production identity, distinct Play/Solana keys, notices, store eligibility/testing and delivered-artifact checks deferred to their gates. |

The pinned reference-CPU audition completed successfully at exact source 9763f6d00f7e4e0def4c94e68774ecb17f5a5779 in [run 40246343159](https://github.com/Hatsunama/sign-to-speak/actions/runs/40246343159). Eight asset/PCM behavior tests and 27 workflow policy cases pass. Five actual 24 kHz mono PCM16 outputs and sanitized records are preserved in the [audition evidence](../../qa-artifacts/pocket-tts-audition-20261025/README.md). Synthesis ran in a network namespace after model provisioning. The initial default-branch dispatch failure was repaired with a narrowly scoped same-repository PR trigger, exact head checkout and fork/branch denial tests. This is reference voice evidence, not Android evidence or accepted quality. The owner’s preliminary voice choice remains pending; the inline player was not heard, so manual File Explorer/WAV playback instructions were supplied. The official CPU wheel index was checked for the exact Python 3.12/Linux torch 2.14.0+cpu wheel before execution.

Next work, after the owner’s voice choice, is a bounded research-input diagnosis and small Pocket TTS conversion experiment on an appropriate Linux conversion host, while selecting a lawful sentence-video subset and a viable continuous model/training route. Existing hosted CI may handle conversion experiments; do not launch paid GPU jobs, large corpus downloads or new training without a resource decision. Release remains blocked, and no unverified candidate is published for installation.

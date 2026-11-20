# Offline Holistic extraction: verified evidence and limits

## Result and current model decision

The real offline CPU probe completed on one frozen publisher-TRAIN development clip: 53 decoded/processed frames, measured timestamps 0 through 1733 ms, 53 SDK-labelled left-hand observations, zero right-hand observations, and 53 pose/face observations. All 84,588 XYZ scalars were independently checked finite and correctly shaped. The 1,092 adjacent observed left-hand point triples differed. Coordinate change can include tracking jitter; it is not recognition accuracy, semantic understanding or independent anatomical-side validation.

[Completed bounded job](https://huggingface.co/jobs/Hatsunama/6affa49defaf5e14f4b25fe7) ran every one of the 428 source checks with zero failures/errors/skips and no Torch/TensorFlow imports. The actual SDK then loaded the pinned local task on CPU inside the verified direct-socket barrier. Worker wall time was 10.2586 seconds including Python/SDK/model startup and all 53 frames. This is cloud diagnostic timing, not phone or steady-state real-time performance.

Neither Qwen model won the earlier comparison: both were 0/64 exact and B's four-edit advantage was inconclusive. Further B training/evaluation spending is stopped; both models and evidence remain privately preserved. A remains only an engineering reference. This extraction result does not restart Qwen training or qualify either model for shipping.

## Immutable bindings and actual execution

- Source archive: a1198bf8704e64b27527476fe01cf5ed9a1db79b20faccbe7f3edf7b1f4736d9.
- Exact launcher: f7727c781778fdaa510d1dcd88c01b484e7fd13ca5a2e0acdd2d3beae47d6c17.
- Outer success receipt: 9c233c2c73cb86cd675c58f308ef2413640d6872284fc9c2b8eb7bc79217a24c.
- Worker feature receipt: 1,746,350 bytes, SHA-256 d34e03ece24394afe5e7891bf4f019234a9f86a8997b1fdb9b3b899c0432bece.
- Official float16 task: 13,683,609 bytes, SHA-256 e2dab61191e2dcd0a15f943d8e3ed1dce13c82dfa597b9dd39f562975a50c3f8.
- MediaPipe 1.1.1 x86-64 wheel: cbb54f6411d8d254f7151ff92cbfea29413be92f802be46a520cc7316319c3cc. PyAV 17.0.0, NumPy 2.2.6 and Pillow 12.2.0 are recorded in the worker result.

Independent readback checked every output size; source/task/clip and terminal-receipt bindings; every PTS/rational-time-base to floored relative-millisecond calculation; strict ordering; all output shapes/missingness/finiteness; all coverage and adjacent-change counts; and every stock SDK/OpenCV shared-library linkage result. Raw source paths, publisher IDs, labels, media and landmark traces remain private. No phone was used.

The new producer uses sequential PyAV decoding, validated right-angle unmirrored display rotation, 640-pixel maximum edge and nearest-center downsampling. It hashes a bounded private snapshot and decodes those same bytes. Each clip gets one synchronous VIDEO detector and a local model buffer. SDK-labelled left/right normalized image coordinates remain separate; world coordinates are not substituted. Face and pose outputs are retained independently. Missing groups stay null.

## Root-cause repairs and cascade status

| Issue / exact connected path | Impact and evidence | Repair / verification / remaining limit |
| --- | --- | --- |
| `holistic_video_probe.py`, `write_private_json`, lines 199-248: post-link interruption or staging cleanup could contradict a complete success receipt. | Proven by two failing CLI regressions against exact beforecopies, independently reproduced. Same commit-boundary pattern as earlier admission repairs. | Verify the owned inode before recovering a committed link; preserve unrelated files; keep primary errors and report post-commit cleanup warnings. Eleven tests passed after this repair, including both failure cases; final full 428 passed. |
| `holistic_video_probe.py`, `run`, lines 321-356: hashing an open clip then rewinding it did not freeze its bytes. | Proven same-length source mutation reached the decoder while the old hash remained in SUCCESS. No mutation was observed in the fresh private job. | Hash into a bounded private TemporaryFile, close the source, then decode that verified snapshot. Root reproduced red; all twelve probe checks and final real extraction passed. |
| `holistic-offline-probe-command-r1.sh`, worker invocation/capture, lines 71-90: catchable worker timeout could lose both streams and an already committed result. | Proven fault injection with TimeoutExpired and published output; related to earlier cancellation/evidence-loss defects. | `holistic_probe_runner.py`, `run_worker`, lines 16-73, writes streams directly to exclusive durable files, captures complete result/failure files and records explicit timeout. Timeout never qualifies as success merely because a SUCCESS file exists. Eight actual subprocess regressions passed; real SDK failures were preserved correctly in R2/R3. Whole-job forced termination can still preclude recovery. |
| Stock wheel native dependency closure. | R2 passed 428 checks, then failed importing OpenCV with missing libxcb.so.1. R3 resolved that and passed 428 again, then failed loading MediaPipe with missing libEGL.so.1. Neither failure processed a frame. | Install the actual required runtime libraries in the ephemeral cloud environment, hash-pin all new wheels, run pip check, and verify all four SDK/OpenCV shared binaries with ldd before media staging. R4 passed those gates, 428 tests and real extraction. Application dependencies/UI are unchanged. |
| First R1 full-source check timeout. | Actual source QA exceeded 600 seconds before media staging. Its captured partial trace was lost by the old launcher. Immutable comparison found the previous 403 source tests unchanged and exactly 16 added tests, with no recursive discovery or live SDK call in them. | Later diagnostic R2 passed 428 in about 300 seconds, with traces showing progress through file/image work; CPU-upgrade R3 completed the same suite in about 55 seconds. The exact historical cause is unproven; resource/I/O variability is suspected. No timeout increase or test bypass was used. Keep this evidence gap explicit rather than calling a passing rerun an explanation. |
| First publisher landmark shard had no exact DEV match. | All 739 TRAIN batches were examined; none matched the frozen 32 entries. No matched record was admitted. | This supported extractor now produces features from the already pinned video directly, avoiding a blind search of 99 more shards. It supplies a different producer, not evidence that publisher/native features are equivalent. |

## Privacy and dependency responsibilities

Google documents the Python Tasks API and the standard wheels include usage logging. The worker installs its irreversible Linux seccomp filter before importing the SDK or reading media. Actual tests establish allowed socket creation before the filter, denied socket/connect/async socket routes afterward, inheritance by threads/descendants, rejection of inherited sockets, and retained local file access. The launcher stages authorized public research inputs separately, closes inherited descriptors, supplies only the required environment and preserves results privately. This is a direct socket barrier under a trusted-launcher/filesystem assumption; it is not a general sandbox or evidence that the stock wheel has no logging code. The product's existing native offline core remains unchanged. [Google Python guide](https://developers.google.com/edge/mediapipe/solutions/vision/holistic_landmarker) - [Maintainer logging discussion](https://github.com/google-ai-edge/mediapipe/issues/6291)

The native GUI/audio libraries satisfy stock wheel linking even though this probe opens no window and uses no microphone. They are cloud-only setup. No new API was added to the app. No host packages or native builds were installed/run locally; model and clip staging occurred only in the bounded private cloud workspace. Signing keys were not accessed or changed. No signing keys or credential values were exposed or included in source or backups.

## Remaining verification and next path

The actual clip establishes one-hand extraction only. It does not qualify two simultaneous hands, occlusion, fast motion, signer/skin-tone variation, physical left/right labelling, camera reversal, geometry/lifecycle resets or negative scenes. Output variation alone is not task correctness.

The next producer gate is Android parity and cadence on identical real inputs, with exact pixel/timestamp binding and explicit crop/domain differences. The existing requested-time OPTION_CLOSEST probe can duplicate observations and is insufficient for that claim. Test actual cancellation/recovery and resource lifetime. Use only phones that are free at Home and return them to Home afterward.

Only after reliable feature production should the [bounded temporal diagnostic](TEMPORAL_RECOGNITION_DIAGNOSTIC_PLAN.md) proceed. That is still fingerspelling; broader aligned ASL meaning, open-set rejection, customer turn/speech integration and commercial qualification remain separate. Qwen remains planned as the local language component. No source-string test, compilation, successful installation or another agent's report substitutes for actual final-artifact runtime evidence on both free phones.

No additional material issue was found in the inspected rotation/scaling, measured-time arithmetic, anatomical-slot serialization, guard call order or native link-closure after these repairs. This scoped result is not proof of correctness. Main remains unchanged and the release remains blocked; no installation recommendation follows.

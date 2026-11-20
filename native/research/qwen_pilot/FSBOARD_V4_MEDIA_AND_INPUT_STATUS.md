# FSboard v4 verification status — November 19, 2026

The unchanged 288-clip cohort passed full media and actual processor verification. The six-clip GPU engineering fit passed at update 100 on the verified L40S runtime. Both fresh A/B arms completed their frozen 192 updates. The completed independent base/A/B comparison scored 0/64 matched exact for every candidate. Neither trained arm is qualified; B has no meaningful advantage. [Completed results and decision](FSBOARD_V4_RESULTS_20261119.md) Earlier base/video/static models each scored 0/8 exact on the inspected fresh fingerspelling assessment. This research does not establish general everyday ASL translation or commercial readiness.

## Frozen experiment and completed input evidence

The experiment is `sts-fsboard-whole-prompt-static-ablation-20261119-v4`: 64 holdout clips from publisher validation, 32 development and 192 training clips from publisher train. Development reserves whole connected signer/recording groups; all six prior/cross-role exclusions apply and publisher test is excluded. Publisher identities are not independently established physical identities.

Inputs are 8–32 distinct ordered RGB stills at actual in-window PTS, maximum edge 384, no padding, with a 6,144-token context and generation reserve. The actual cohort has 21–32 frames per clip and 9,178 verified PNGs. It contains 1,123,404,883 original bytes. Native-video inference is not qualified.

| Evidence | SHA-256 |
| --- | --- |
| Private proposal | `6f04da3b218f664d8ab4097ab0212fdd135220f89dc8019e9b3b1fb04213e6d9` |
| Canonical protocol | `9e482b474b1250422e571179ee217a6bf2ad2a223ec28b8fc2f9a288b88a90f1` |
| Full admission | `780c78b8b3b9f43aa4919815394784f8b5d3cb1432574e87644f39ed4c03d075` |
| Actual matched processor receipt | `551c2e637dfe5a4132c537c3dd8398e0a58cc76750573b2882e30afa5a14db61` |
| Actual five-control receipt | `eac4dbc65d1a4a5309c22fe32c63c144744a392aeaa4be3762e2f847ae2aaf6e` |
| Completed input receipt | `0bae4476559f73990649dff0e98131fdded508d63de47ac31ca744ddb47fb517` |

The [completed processor continuation](https://huggingface.co/jobs/Hatsunama/6aff4c27efaf5e14f4b1eb18) independently rehashed/extracted the private 2,030,178,907-byte media archive, verified all 288 matched encodings and all 320 controls, encoded the 80 selected static images, and exercised both lazy video consumers. Independent remote readback and matched/control comparisons passed; no control pixel collisions were found. Its Linux prerequisite suite passed 163 checks without skips. It loaded no model weights or scores. Training-eligibility flags remain false because input validity is not model qualification.

Both trained arms used common initialization, two identical complete video passes and 192 optimizer updates. Only B adds static-letter loss at weight 0.05. The pinned prior schedule fixes the same 80 distinct static images and order; the lazy consumer rechecks image bytes and attested encodings rather than retaining every pixel tensor. [Protocol](fsboard_task_protocol.py) · [Input gate](fsboard_task_inputs.py) · [Training consumer](fsboard_task_training.py)

## Repairs and cascading checks

The v2 fixed-32 policy rejected actual 21-frame windows; increasing a duration floor still rejected a 29-frame window. V4 explicitly adopts distinct 8–32-frame inputs. Historical failures remain failures.

Actual full admission then exposed an incorrect clip-wide-median duration assumption. The complete [timing diagnostic](https://huggingface.co/jobs/Hatsunama/6aff33f44fdc5ab3c55a1034) verified all 288 original files. Twenty-two valid terminal samples were rejected by the legacy duration check. The task-only repair corroborates the publisher endpoint against final PTS and the measured final-frame end against stream duration, within the unchanged two-tick budget and existing median-period tail bound. A preceding frame interval cannot constrain a variable-rate terminal sample. Legacy defaults and the pinned historical scorer are unchanged.

The [full media run](https://huggingface.co/jobs/Hatsunama/6aff39f1efaf5e14f4b1d073) replayed every one of those 22 exact originals through legacy rejection and corrected two-pass decoding plus PNG verification, then completed all 288 admissions. It preserved the media archive before timing out during processor verification. No completed processor receipt was claimed for that run. [Decoder](fsboard_admission.py) · [Task caller](fsboard_task_admission.py) · [Regressions](test_fsboard_admission.py)

The independent [thread diagnostic](https://huggingface.co/jobs/Hatsunama/6aff4dfd4fdc5ab3c55a339a) reproduced a 38–59x slowdown on three real clips: the 8-vCPU job exposed 64 host CPUs and selected 32 Torch threads. Default encodes took 26.60–28.93 seconds, versus 0.462–0.697 seconds at one thread, with exactly equal encoding receipts. Current launchers cap CPU math threads and preserve per-example diagnostics. This strongly supports the timeout explanation; the old run did not record its actual thread count. The completed matched/control/static stage took 423.53 seconds after setup. This is cloud preprocessing evidence, not Android performance evidence. [Progress journal](fsboard_v4_resume_inputs.py)

Source packaging also exposed missing transitive imports, incorrect Windows text decoding and tests depending on private working-folder fixtures. Strict UTF-8 packaging and extracted-source checks bind delivered code and preserve private input bytes. Synthetic receipt/schedule fixtures and a sanitized recorded-format phone-log excerpt removed those hidden test dependencies without changing production pins or historical records. [Packager](fsboard_sentinel_source.py)

The pinned model's text EOS is 248044, while assistant teacher targets use 248046. Fit and evaluation now explicitly stop at either token and reject continuation past the boundary. All 24 inspected historical matched scores reproduce unchanged. Input encoding and teacher targets are unchanged. [Stop evidence and export responsibilities](GENERATION_STOP_CONTRACT.md)

## Engineering fit and qualification path

The frozen [fit policy v2](FSBOARD_V4_FIT_SMOKE_PROTOCOL.json) selects six distinct training references and evaluates updates 0, 50 and 100. Its engineering guard requires six exact outputs and at least 25% mean teacher-NLL reduction, stopping at 50 if met. No holdout/development model calls are allowed. The fit adapter is discarded.

Single-L4 allocation stayed queued and was cancelled before execution. An actual [L40S preflight](https://huggingface.co/jobs/Hatsunama/6aff5254efaf5e14f4b1f944) verified NVIDIA L40S, CUDA 12.6, Torch 2.8.0+cu126 and BF16 without loading weights. The separate R3 archive changes only the device-name guard/error and adds runtime tests; all training, encoding, loss and scoring functions are unchanged. It supports exact NVIDIA L4 or NVIDIA L40S names while preserving one-GPU, dependency, CUDA and BF16 checks.

R3 GPU source: 201,692 bytes, SHA-256 `30e21395dddb5e84dd2e062005d12e8035532be680e3b1d12db90e51b636f1cf`; source manifest `5b2df533e3d36fc29a7df820e4e9e069cbd1e739b48e44e69fb4db7c052e95aa`. The original R2 archive remains preserved. The CPU input producer remains separately bound to R2; the encoder and its attestation contract did not change.

The first multi-mount L40S fit request stayed unallocated and was cancelled. The [single-workspace fit](https://huggingface.co/jobs/Hatsunama/6aff5cd6efaf5e14f4b205f8) uses server-side copies of only the required assets under one private mount; authoritative originals are not mounted. Every copied Xet hash and size matches. Source, media, processor, controls, precision and fit rules are unchanged. This request progressed to execution; the scheduling cause is not proven. It passed 176 Linux prerequisite checks without skips and completed the frozen engineering fit described below. Four initial generations stopped at EOS and two hit the token cap, with no continuation past the first effective EOS. All six initial exact scores were zero. The historical timeout cap was 30 minutes. [Fit](fsboard_task_fit.py)

The [pair launcher](fsboard_v4_pair_launch.py) rechecks fit success, child exit, exact checkpoint hashes, effective EOS and source/data/processor pins, and recomputes the six-clip guard before emitting the existing complete-pair command with a new common initialization. Scoring separately binds final A/B adapters and the base revision, then evaluates the frozen 64-by-five controls. None of those stages is qualified by a mock test. [Pair](fsboard_task_cloud_training.py) · [Evaluator](fsboard_task_evaluate.py)

## Completed engineering fit and paired training

Independent private readback verified the fit process exit, every saved checkpoint, source/data pins and the actual pair-launch gate against the frozen R3 runtime.

| Optimizer update | Exact outputs on six training clips | Mean teacher NLL | Guard |
| --- | --- | --- | --- |
| 0 | 0/6 | 6.6929999193 | Not met |
| 50 | 5/6 | 0.2187784041 | Not met |
| 100 | 6/6 | 0.0003787550 | Passed |

The same optimizer continued through both trained checkpoints. No holdout/development model calls occurred and the fit adapter was discarded. Actual effective EOS IDs were `[248046, 248044]`; returned sequences did not continue past the first effective EOS. Peak allocated training memory was 16,054,930,944 bytes. This is training-graph and overfitting evidence, not held-out recognition or mobile memory evidence.

Fit success SHA-256: `ba48fd3925b2067692a9f8f00110574652c2898be1b7ebce2fc23b4cbefc0b9e`; outer zero-exit receipt: `d825a05b4ec51ea3550d5bfc5ccb0fb3154f34ebc0adefa88fd7aed8fc39badc`.

The [fresh pair](https://huggingface.co/jobs/Hatsunama/6aff619b4fdc5ab3c55a4fab) completed in another isolated one-mount workspace. Both arms completed 192 updates and 384 video microbatches; only B performed 192 static updates. Independent readback verified the exact SHA-256 of all 474 captured training JSON files, and every step matched its scheduled video/static IDs with finite losses. Common initialization matched and both final parameter digests changed. All 596 captured objects matched indexed sizes. Each arm's final outputs were separately copied to its labelled private bucket and checked by Xet content hash and size. The evaluation loader must independently stream-check terminal adapter/optimizer bytes and reproduce the loaded parameter digest before scoring. No static-transfer benefit is claimed.

The trained reference uses Transformers' Torch implementation; optional fused linear-attention/convolution packages are absent. Evaluation keeps that implementation and sets an explicit owned kernel-cache directory while excluding ambient credentials.

## Evaluation binding and capture repairs

The candidate verifier binds every final adapter to the exact completed pair, source/model/data pins, common initialization, schedule and outer capture receipt. Loaded nonempty LoRA parameters must reproduce the trained parameter digest before freezing for inference. The base is loaded fresh from its pinned revision.

Review found and reproduced two additional edges before use: a cross-arm inequality guard incorrectly rejected a legitimate identical-arm null outcome, and live capture could treat a partially written runtime receipt as corruption. Identical arms are now accepted when each has its own valid evidence; swapped or tampered artifacts still reject. Live polling defers the directly written runtime receipt until completion, and final capture requires valid complete JSON. The helper/template byte-pin connection is checked in the extracted source closure.

The subsequent independent source/requirements/declared-program-template suite passed 377 checks with no failures or errors and four Windows filesystem-link skips, without importing Torch. Actual held-out scoring and all 960 independent record checks are complete; both trained candidates failed the capability check. [Binding verifier](fsboard_v4_evaluation_launch.py) · [Evaluation template](fsboard-v4-evaluate-one-mount-body-template-r1.py.txt)

## Durable paired-output capture

The paired-output collector initially could copy a terminal success receipt even when final model files were absent. Regressions reproduced that incomplete success before repair. The collector now requires shared records, all 192 step records per arm, both final checkpoint adapters/optimizers and matching receipt hashes before publishing success. Conflicting success/failure receipts reject. Completed checkpoint and failure evidence survives rejected completion; cleanup errors attach to the original I/O error. This verifies stored output completeness, not model accuracy. The original collector completed the actual GPU pair capture described above; this does not establish held-out quality. The follow-up recovery repair below has 20 focused cases, including real Windows junction rejection. [Collector](fsboard_v4_pair_capture.py) · [Regressions](test_fsboard_v4_pair_capture.py)

## Terminal publication and recovery follow-up

Before scoring, failing regressions reproduced a late-SIGTERM receipt/exit contradiction, a cleanup signal overwriting a committed outcome, a corrupt record preventing later private records from being saved, and a short-write/readback failure leaving an unretryable partial destination. Evaluation now seals cancellation at one terminal boundary through cleanup, preserves the original I/O error if scoped cleanup fails, and finishes strict sibling capture before rejecting the run. The model-loading, input-loader, frozen scorer and all other validation function ASTs are unchanged. The 26 focused binding/orchestration checks passed. New support is privately read back byte-for-byte; historical sources and completed job commands remain preserved.

The same-pattern checkpoint audit reproduced a multi-file retry rejecting its own already-verified prefix and counting its bytes twice. Retries now reverify source and destination against the recorded digest before skipping known files, preserve unindexed existing files, and continue strict sibling capture after per-record I/O errors while withholding success. All four new cases failed before repair; all 20 collector checks passed afterward. This repaired collector is for future reuse; it does not replace the exact source that produced the completed pair.

The [completed base/A/B evaluation](https://huggingface.co/jobs/Hatsunama/6aff710eefaf5e14f4b21f95) used the unchanged R3 runtime and all 960 frozen condition records. L4 allocated before L40S and the queued L40S job was cancelled; every candidate used the same L4. All 26 focused Linux prerequisites passed with zero skips. The zero-exit wrapper and independent host readback verified exact adapter binding, 967 captured hashes and recomputed record/grouped scores. All three candidates scored 0/64 matched exact. B reduced only four character edits versus A and has no meaningful advantage. Both final models and evidence remain private; further B-arm spending stops. [Results, control failures and next experiment](FSBOARD_V4_RESULTS_20261119.md)

The admission cascade separately reproduced a committed directory being misreported as failed when SIGTERM arrived after the rename or during temporary cleanup. Publication now records successful rename or corroborates this attempt's directory device/inode identity, preserving verified admission without accepting an unrelated directory with identical receipt bytes. Precommit cancellation and publication failures still reject. All 23 focused admission checks passed. An independent full source closure then passed 382 checks, no failures/errors, four Windows filesystem-link skips and no Torch import. This future admission source does not replace the exact R3 source used by the completed fit/pair and pending evaluation.

Two superseded manual fit/pair preparation templates had the same prospective cancellation pattern and no active source/shell/documentation callers. They were retired from the active workspace after verifying exact private backups; archived originals, executed commands and their receipts remain byte-identical. New fitting/training requires a reviewed launcher. These findings do not establish that a signal occurred in a completed job.

## Validation limits and release status

The source-only checkpoint before evaluation support contained 347 checks, no failures/errors and four Windows filesystem-link skips. It ran without private historical fixtures, local Torch imports or native builds. R3 separately passed 176 extracted-source checks with two host link skips; Linux execution must exercise them. The private continuous-media probe separately passed its 15 Linux checks without skips.

The [continuous-ASL intake plan](CONTINUOUS_ASL_DATA_INTAKE_PLAN.md) tracks sentence/discourse data, rights, reference alignment and cross-source leakage. Its first one-recording operational probe passed actual access and two-pass decoding; this does not establish signed meaning. [Metadata and media evidence](FLEURS_METADATA_ACCESS.md)

[Phone evidence](PHONE_RUNTIME_RESULTS.md) retains unresolved accelerated numerical failures and unqualified interactive latency. No phone was touched and no production app code or appearance changed during this research update. General everyday ASL quality, integration, voice acceptance, exact final-app verification on both free phones, rights/notices and production signing remain release gates. Main is unchanged. Media, publisher identifiers and operational evidence stay private; backups exclude signing keys and credentials. This draft is not a release or installation recommendation.

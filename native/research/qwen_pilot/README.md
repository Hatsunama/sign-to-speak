# Sign To Speak Qwen research

Bounded private cloud experiments, not a production translator. Keep customer data, credentials, signing keys, media, reference manifests, predictions and weights out of this source PR.

Run the lightweight research suite with PyAV 17.0.0, Pillow 12.2.0 and NumPy 2.2.6 available. The publisher landmark integration checks also require the pinned PyArrow and protobuf wheels in `requirements-landmark-probe.txt`:
`python -B -m unittest discover -s native/research/qwen_pilot -p 'test_*.py'`.
Use the pinned cloud CUDA environment for training and the opt-in `test_target_only_loss.py --cuda` for controlled loss/gradient equivalence.

## Current FSboard v4 work

The completed whole-prompt comparison failed: base, video-only A and video-plus-static B each scored 0/64 exact. No meaningful winner was found. Further B training/evaluation spending is stopped; A remains an engineering reference only. Read [actual results](FSBOARD_V4_RESULTS_20261119.md) before launching anything. Source and data pins remain mandatory. Do not extend this failed regime or qualify a release from training loss or passing source checks.

The next diagnostic checks dense motion input. `publisher_landmark_probe.py` reads bounded HTTPS ranges from one pinned publisher TRAIN shard and selects only exact frozen development contexts. It is a research intake utility, with no application caller or runtime API dependency. The first shard was fully examined without a matching development record; that does not prove absence elsewhere. Read [landmark probe evidence, fixes and limits](PUBLISHER_LANDMARK_PROBE_STATUS_20261119.md). A supported direct extractor then completed one pinned real clip behind a verified Linux socket barrier; read [actual offline extraction evidence and limits](OFFLINE_HOLISTIC_PROBE_STATUS_20261120.md) and the [bounded temporal diagnostic plan](TEMPORAL_RECOGNITION_DIAGNOSTIC_PLAN.md). This establishes feature extraction only.

The actual extraction CLI requires the hash-pinned Linux cloud environment in `requirements-holistic-probe-cloud.txt`, its native runtime libraries and the verified guard. The source-only tests mock the SDK; passing them alone does not establish extraction. Keep local checks lightweight and app/native builds on GitHub.

Read [current media/input evidence](FSBOARD_V4_MEDIA_AND_INPUT_STATUS.md), [generation stops](GENERATION_STOP_CONTRACT.md) and the [continuous-ASL data plan](CONTINUOUS_ASL_DATA_INTAKE_PLAN.md) before running current work. The historical commands below reproduce the original failed experiment and supply shared helpers; they are not the v4 entry points.

## Historical 80-update paired path

1. Preserve historical inputs. Run `dense_admission.py --output /data/pilot_dense_oriented` against the original pinned cohort. The shared `video_orientation.py` validates and applies right-angle nonmirrored display rotation. Sampling retains actual timestamps and source/applied rotation fields.
2. Run `static_admission.py --output /data/static-v1`. Intake verifies bounded original JPEGs, the source anchor, canonical/mirror equality and source provenance. The actual archive has a complete duplicate wrapper and four rectangular photos. Optional `--archive-file PATH --archive-sha256 SHA` verifies cached originals. Review the contact sheets before producing a separate eligible manifest.
3. Freeze the paired protocol before predictions. `paired_experiment.py` requires explicit `--dense-admission`, `--dense-admission-sha256`, `--static-manifest`, `--static-manifest-sha256` and a fresh `--run`. Both arms reload one saved initialization and receive identical video batches/optimizer updates; only B adds static loss. Current consumers reject the old orientation contract.
4. `fsboard_admission.py` prepares evaluation-only FSboard v13 clips with pinned source metadata/license, original MD5/SHA-256, annotation-window timestamps, display orientation and PNG hashes. Metadata-duration validation accounts for the recorded container timestamp tick. Review first, then create a separate eligible manifest.
5. `fsboard_evaluate.py` requires a completed pair, matching dense/static manifests and the approved evaluation SHA. It compares the frozen base and both adapters on matched, reordered, black and unrelated-video inputs. It retains private completions, EOS/token-cap status, exact match, weighted character error and optional paired reference NLL.

The first feasibility protocol uses 80 updates per candidate: the pre-generation schedule audit found that 40 would expose only 21 of 24 static letters. The visual-screened set has 116 photos; four uncertain/poorly visible examples were excluded. AI visual screening is not independent ASL-fluent certification. The four general-text clips are reused development controls. A small fresh fingerspelling set cannot qualify everyday-ASL conversation coverage.

## Failure and provenance boundaries

Use exclusive fresh output directories and preserve failed attempts. Static publication preserves completed output after a reported post-rename failure; cleanup reports alongside the primary error. Video failures retain diagnostics. Terminal job state, parseable success and final artifact inventory must agree before accepting a run.

`train_qwen_pilot.py` supplies tested shared tokenization/causal-loss helpers and historical single-run reproduction. Prior sparse/dense source snapshots and receipts remain private evidence. The paired path explicitly binds the orientation-correct admission.

Every paid job requires a timeout and shared-budget check. US$14 covers both candidates and previous pilots, with US$2 reserved for storage/startup uncertainty. Android builds remain on GitHub. Research results do not authorize a release or establish phone behavior.

See [teaching-material task](STATIC_ASL_CURRICULUM_TASK.md) and [research record](STATIC_ASL_RESEARCH.md).

## Completed evidence and diagnosis

Both 80-update candidates failed the fresh eight-clip fingerspelling assessment (0/8 exact each). A static-only four-photo fit test subsequently reached 4/4 by step 50; it does not establish generalization or select a curriculum. See [paired results](PAIRED_RESEARCH_RESULTS.md) and the updated [source register](STATIC_ASL_RESEARCH.md).

`static_diagnostic.py` evaluates the base and both completed adapters on identically processed seen/unused same-source photos. `static_learnability.py` reloads the shared original initialization for a fixed 200-update four-photo training-fit diagnosis. Both require the pinned pair/data receipts and fresh private output directories; neither changes an app model or scores explanatory text as an extracted guessed answer.

The GitHub-only runtime preflight produced Linux/Android CPU executables, followed by cloud-only F16 conversion and Q4 quantization of the original pinned base. See [measured runtime evidence](RUNTIME_PREFLIGHT_RESULTS.md) and [reproduction procedure](EXPORT_PREFLIGHT.md). The exact original-base CPU CLI has passed bounded engineering checks on both phones; [phone evidence](PHONE_RUNTIME_RESULTS.md) records latency, cancellation, recovery and remaining qualification gaps. The Vulkan companion is under numerical investigation. No release or language-quality claim follows.

Final evidence review removed four duplicate fixture test executions and added a regression rejecting inconsistent LoRA receipt fields against the digest-verified configuration. That historical evidence checkpoint ran 114 unique cases; current checks are recorded in the v4 status document. Both local tests and cloud validation against the actual archived pair passed; the training source/results remain preserved separately.

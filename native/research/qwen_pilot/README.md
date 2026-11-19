# Sign To Speak Qwen research

Bounded private cloud experiments, not a production translator. Keep customer data, credentials, signing keys, media, reference manifests, predictions and weights out of this source PR.

Run the full research suite with PyAV 17 and Pillow available:
`python -B -m unittest discover -s native/research/qwen_pilot -p 'test_*.py'`.
Use the pinned cloud CUDA environment for training and the opt-in `test_target_only_loss.py --cuda` for controlled loss/gradient equivalence.

## Current paired path

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

Final evidence review removed four duplicate fixture test executions and added a regression rejecting inconsistent LoRA receipt fields against the digest-verified configuration. The final suite has 114 unique cases. Both local tests and cloud validation against the actual archived pair passed; the training source/results remain preserved separately.

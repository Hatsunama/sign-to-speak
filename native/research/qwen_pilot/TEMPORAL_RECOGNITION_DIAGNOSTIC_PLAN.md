# Bounded motion-recognition diagnostic plan

Status: proposed follow-up, not an implemented recognizer or a shipping architecture. Do not begin another Qwen run merely because source exists; first satisfy the declared evidence gates. The current raw-video A/B experiment has no meaningful winner and further B spending is stopped. Qwen remains the planned local language component; this proposal tests a small visual recognizer first.

## Why this experiment

The raw-video models scored 0/64 exact and unrelated input did slightly better than matched input. Before another large-model run, determine whether the actual hand trajectories support recoverable fingerspelling signal with a much smaller standard temporal model. This cannot establish everyday ASL translation. The official FSboard work supports landmarks as a testable route on a much larger corpus, with different preprocessing and model scale. [Publisher paper](https://arxiv.org/html/2407.15806v1#S4)

Published competition solutions demonstrate temporal convolution/CTC and mobile export. Inspect their code and licences before reuse; a code licence does not automatically license model weights or another recording source. The 12th-place notebooks state MIT and the winning repository states Apache-2.0. No third-party trained task checkpoint is needed for this proposed own-weight baseline. [MIT notebooks](https://github.com/shlomoron/Google---American-Sign-Language-Fingerspelling-Recognition-12th-place-solution) - [Apache-2.0 encoder/decoder code](https://github.com/ChristofHenkel/kaggle-asl-fingerspelling-1st-place-solution) - [Temporal-CNN deployment account](https://www.kaggle.com/competitions/asl-fingerspelling/writeups/ohkawa3-29th-place-solution-lb-0-762)

## 1. Qualify and freeze the feature producer

Complete the offline SDK preflight before cohort extraction. Keep the exact task, SDK/wheel hashes, native dependency closure, CPU delegate, options, pixel orientation/scaling, raw clip interval and rational PTS. The Python and Android SDKs are not assumed equivalent. Native camera crops, dropped frames, geometry resets and publisher annotation windows are separate domains. Preserve hand absence rather than manufacturing detections.

Extract only the existing frozen 192 training and 32 development clips initially. Do not use holdout to tune feature thresholds, architecture or checkpoints. First inspect the frozen duration/frame-count bounds against the full roster; do not silently drop a long or low-visibility clip to satisfy the new probe's diagnostic limits. Preserve rejected inputs and stop that fixed protocol if its eligibility contract cannot be met.

Bind each feature result to the exact source-byte digest and task/runtime manifest. Keep labels, file names, prompts, categories, source IDs and recording IDs outside the feature tensor. Preserve the native anatomical 21-point hand order, timestamp gaps and missingness. Model input should retain wrist trajectory plus wrist-relative local hand shape, with a documented bounded scale and explicit left/right presence bits. Compare alternative normalization only in a separately frozen DEV experiment.

## 2. Freeze a small recognizer and its gates before fitting

A candidate bounded diagnostic is a 128-feature, 64-channel temporal convolution with width five, two residual width-five convolutions at dilation two/four, and a 28-class output including blank, 26 letters and space. Without normalization layers this has 83,932 parameters and a 29-frame receptive field. This is a proposed small test, not a claim of published accuracy or an optimum architecture. Keep frame count and use greedy CTC; no Qwen, beam language model or transcript cleanup in the primary recognition score.

Use measured timestamps, and freeze whether features are kept at their measured cadence or resampled. If resampling is used, prove the interpolation, missingness and gap rules on real records before fitting; do not equate camera requests with observations. Zero-fill is allowed only with an explicit missing-hand mask. Run at true lengths or re-mask padded positions through each temporal layer so batch padding cannot become a feature. Reject impossible CTC alignments when T is smaller than target length plus adjacent repeated-character count; retain zero_infinity=False so failures remain visible.

First fit six predetermined training clips. Require six distinct exact greedy outputs within a bounded run and check matched versus swapped/blank input. This is a learnability and wiring gate only. Restart from fresh weights before the frozen 192-clip run; never carry over the diagnostic adapter or training state. Predeclare seed, batches, steps, optimizer, selected DEV checkpoints and the stopping rule.

## 3. Require visual grounding on DEV

Primary metrics stay exact match and the existing normalized micro CER. Retain literal names/OOV spellings; any space-stripped metric is supplementary and must be declared before seeing predictions. Report blanks, EOS/CTC collapse, output diversity, lengths, failed inputs and all original denominators.

Use matched clips, wrong clips of similar duration (same publisher signer group where possible), and controls that preserve the original timing/missingness while shuffling or repeating observed poses. Keep both hands' masks unchanged in temporal controls. Include zero-coordinate and mask/length-only controls. Record donor identity and fallback rules before scoring. Check donor-reference fit as well as original-reference degradation; a generic response to an unnatural input is not correct interpretation.

A provisional gate to freeze before fitting is nonzero DEV exact matches plus at least a 0.10 absolute matched CER advantage over wrong input, with paired uncertainty and per-publisher-ID results. Weak/inconclusive uncertainty does not qualify a winner. Physical signer identities remain unverified. If the gate fails, stop this regimen and investigate extraction, normalization, data scale or model capacity using targeted evidence. Do not restart Qwen merely because a training loss fell.

## 4. Keep the old comparison and release claims honest

The old 64-clip holdout was already scored by earlier Qwen models. It can provide a frozen comparison for a new recognizer, but it is not globally untouched after repeated architecture decisions. Keep it out of optimization and disclose that reuse. A fresh signer-disjoint final set and broad conversational-ASL coverage remain later release gates. Do not read publisher TEST payload merely to remove a blocker before an evaluation protocol and rights/intake review are frozen.

If the new recognizer passes, compare its literal output with bounded local Qwen rescoring on DEV before any new Qwen adapter. Report both raw and rescored errors so language priors cannot hide lost letters or invent meaning. General-ASL meaning still requires appropriately aligned signed-meaning data, face/body evaluation and open-set/turn-boundary rejection. Fingerspelling targets do not supply that evidence.

## 5. Cost and runtime sequencing

All Torch/model training stays in bounded cloud jobs; no local package installation or native build is proposed. Measure extraction and a single training epoch before setting a run cap. Preserve the shared US$12 compute ceiling and US$2 reserve inside the US$14 authorization; running-time estimates are not verified invoices or available account balance. Avoid another full corpus download: use the already pinned source clips and save only required features privately, with hashes and provenance.

Only after a useful visual gate should an export contain frame logits rather than the training loss/beam decoder. Compare original versus exported logits/greedy text on golden variable-length, blank and repeated-character fixtures. ONNX Runtime Mobile or built-in-op LiteRT are supported routes to investigate, with explicit size, latency and memory measurements. Native implementation choice must follow those measurements. [ONNX Runtime Mobile](https://onnxruntime.ai/docs/get-started/with-mobile.html) - [LiteRT export guidance](https://keras.io/guides/litert_export/)

Android integration remains separate. Preserve the current appearance and behavior, use the pre-SORT worker observations rather than coalesced React status, and validate cancellation/recovery. Build on GitHub only. After code/configuration changes, test the exact final artifact on both free phones, with readiness checks first, no interruption of other apps, and Home afterward. No unverified release or installation recommendation follows this diagnostic plan.

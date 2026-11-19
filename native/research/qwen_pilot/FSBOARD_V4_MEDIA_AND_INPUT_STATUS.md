# FSboard v4 media and input checkpoint — November 19, 2026

This is research for a fingerspelling experiment. The earlier base and video/static models each scored 0/8 exact on the inspected fresh assessment. General everyday ASL recognition, translation and commercial readiness remain unqualified. The production app, its appearance and behavior, and main are unchanged.

## Frozen data and actual inputs

The experiment is `sts-fsboard-whole-prompt-static-ablation-20261119-v4`. It freezes 64 holdout clips from publisher validation, 32 development and 192 training clips from publisher train. Development reserves entire connected signer/recording groups. All six prior/cross-role exclusion dimensions apply; publisher test is excluded. These are publisher identities, not independently established physical identities.

- Original private proposal SHA-256: `6f04da3b218f664d8ab4097ab0212fdd135220f89dc8019e9b3b1fb04213e6d9`.
- Canonical protocol SHA-256: `9e482b474b1250422e571179ee217a6bf2ad2a223ec28b8fc2f9a288b88a90f1`.
- Planned originals: 1,123,404,883 bytes, kept in private cloud storage.
- Inputs: 8–32 distinct ordered RGB stills at actual in-window PTS, image edge at most 384, no padding, context 6,144 tokens with reserved generation space. This is not a qualified native-video path.
- Both arms: common initialization, two identical complete video passes and 192 updates. Only B adds static-letter loss at weight 0.05.

The v2 fixed-32-frame policy failed on real 21-frame windows. A higher duration floor in v3 still failed on a 29-frame window. V4 explicitly changes this research input policy; it does not relabel old failures as passes. The [protocol](fsboard_task_protocol.py) and [aggregate proposal](FSBOARD_TASK_V4_PROPOSAL_SUMMARY.json) record the change.

[Cloud v4 sentinel job](https://huggingface.co/jobs/Hatsunama/6aff2c44efaf5e14f4b1bd82) passed 93 prerequisite tests without skips and verified selected actual media. Four exact historical originals yielded 21, 21, 29 and 32 frames; the previously valid example retained all 32 historical PNG hashes. This does not establish full-cohort validity or model accuracy.

## Actual duration failure and repair

The [first full-media run](https://huggingface.co/jobs/Hatsunama/6aff2df1efaf5e14f4b1c01b) passed 111 prerequisite tests, then stopped at holdout position 25 after 24 verified clips. The decoded duration was 4.031700 seconds versus publisher duration 3.998279 seconds. The independently hash-checked original has SHA-256 `f4f312f8402c42136541b2b4a48e76f9ca256a5faf96da87ad4bd224112387c1`.

Its 121 frames use a 90 kHz clock. Final PTS 359845 ticks is within 1.23 microseconds of the publisher endpoint. Final duration and preceding PTS gap are both 3008 ticks, ending exactly at stream duration 362853 ticks. The clip-wide median is 2999 ticks; using that median falsely rejected the valid final frame.

V4 admission now opts into measured terminal-frame corroboration for positive overruns beyond the old tolerance. Publisher endpoint must match final PTS, measured final-frame duration must match the preceding distinct PTS gap, and measured frame end must match stream duration, each within two ticks. Other checks remain. Legacy defaults and the pinned legacy scorer are unchanged. Accepted exceptions record these measurements; missing or inconsistent evidence rejects before publication. [Decoder](fsboard_admission.py) · [Caller](fsboard_task_admission.py) · [Regressions](test_fsboard_admission.py)

An auxiliary diagnostic read one derived duration after closing its PyAV container. That value was invalid. The original is preserved, with a separate correction reconstructed from exact stream integers and frame arrays captured while open. The repair captures measurements while open.

The [corrected full run](https://huggingface.co/jobs/Hatsunama/6aff3156efaf5e14f4b1c577) passed 122 prerequisite tests and first replayed that exact MP4 through legacy rejection and corrected two-pass decoding plus PNG verification. It then started the unchanged 288-clip cohort. Completion remains pending at this checkpoint. Source archive: 122,236 bytes, SHA-256 `3c01ea6ee972f542efb1dab21fac167277cfef55b93c6d16dc4aca9edc2fa05d`.

## Connected consumer and control checks

The [input gate](fsboard_task_inputs.py) rechecks proposal/protocol/prior pins, role counts, paths, hashes, PNG decoding, geometry, frame counts and ordered timestamps. Structural receipts keep processor verification and training eligibility false. The actual pinned cloud processor must still check all 288 examples. A regression also prevents a same-named local model path from bypassing its Hub revision pin.

The [control planner](fsboard_task_controls.py) prepares matched, reversed, repeated-middle, matched-geometry black and unrelated-video inputs at each target's actual N. Wrong-video donors require different signer, recording, reference and original digest, with at least N frames. Deterministic donor reuse avoids factorial search; unique ordered slots, reuse counts and geometry differences are recorded. Owned images close on success, failure and cancellation. Actual model scoring remains pending.

The training adapter validates the exact processor receipt and static manifest/schedule before exposing paired schedules and lazy CPU batches. It does not load all training pixel tensors into memory. Unit tests use synthetic media and a mock processor; no training is qualified by them. [Adapter](fsboard_task_training.py)

Strict UTF-8 source packaging now verifies the transitive module boundary, original private bytes, manifest and extracted-source tests. Earlier missing-fixture and encoding failures remain preserved as packaging failures. [Packager](fsboard_sentinel_source.py)

## Work still required

Complete full-media admission and independent archive checks remotely; verify the actual processor and all five controls; verify static inputs; run a bounded training-fit check; reset common initialization before the paired experiment. Training fit is not independent accuracy.

The [continuous-ASL plan](CONTINUOUS_ASL_DATA_INTAKE_PLAN.md) covers published sentence/discourse data, rights, reference alignment and leakage. Fingerspelling cannot establish everyday-conversation translation. [Phone evidence](PHONE_RUNTIME_RESULTS.md) retains unresolved accelerated numerical failures and unqualified interactive latency. Exact final production application checks remain required on both phones.

Media, raw identifiers and operational evidence stay private; backups exclude signing keys and credentials. This draft is not a release or installation recommendation.

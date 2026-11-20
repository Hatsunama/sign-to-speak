# Publisher landmark intake: verified scope and remaining work

## Model prioritization rule and current decision

Prioritize one model only when comparable, independent evaluations show a meaningful advantage in task correctness and required device resources. Use the same inputs, preprocessing, references, splits and generation rules; inspect uncertainty and failure cases. Training loss and small metric differences do not select a winner. Stop the weaker arm's new training/evaluation spending and preserve its exact checkpoint, provenance and results. Retain shared code only where active work uses it. If neither model meets the task gate, diagnose the failure before spending on another training run.

This comparison has no meaningful winner: both A and B were 0/64 exact; B saved four edits over 630 characters and exploratory intervals include no difference. Further B spending is stopped. A is a simpler engineering reference, not a demonstrated winner. Both exact models and evidence are preserved separately in private labelled buckets. No further model training was launched for this landmark investigation.

## Actual bounded experiment

Publisher v13 contains 100 daun_v3 TRAIN Arrow shards totalling 61,506,380,424 bytes. The version-pinned root, daun_v3, landmarks and metadata directory inventories and the 8,195-byte converter were inspected. The release contains a converter that generates a video-level CSV; the inspected released directories contain no precomputed video-to-landmark index. This is a scoped inventory result, not proof that no index exists elsewhere. The card's TFRecord description does not describe the actual Arrow files. [Publisher release](https://www.kaggle.com/datasets/googleai/fsboard)

Only TRAIN shard 00000 was scanned. Across three bounded CPU jobs, the committed cursors progressed 0 -> 77 -> 386 -> 739, with no matching record among all 739 batches. The frozen 32 development clips are from publisher TRAIN. This landmark probe did not read holdout landmarks. The frozen 64-clip holdout is publisher VALIDATION and was already scored by the earlier Qwen comparison. TEST payloads were not read. No matched landmark file was produced and no model was loaded or trained. Provider ERROR/exit 2 represents the explicitly recorded INCOMPLETE outcome, not successful data admission.

- Initial scan: job 6aff8421efaf5e14f4b23324; 397 source checks passed. Cursor 77; byte cap ended the scan.
- Four resumed chunks: job 6aff87014fdc5ab3c55a737c; 15 focused checks passed. Cursor 386.
- Five resumed chunks: job 6aff8c9d4fdc5ab3c55a78b3; all 402 then-current source checks passed with no skips. Cursor 739; no match in the fully examined shard. The five chunk receipt hashes, cursor arithmetic, stable ETag and 319,728,424 new range bytes were independently checked. Outer receipt SHA-256: d5e54feda21b919a7412915bd673d131ec3c1cc1342da6cfae154aadb6fc0a7a.

There was no repeated start-at-zero retry. Each continuation advanced through new data and retained strict identity and byte/request/time caps. Signed storage URLs remain memory-only. Raw records, references and publisher signer identifiers are not in this source PR.

## Regression-first repairs in this new utility

These are research-reader defects, not evidence that the production application changed.

| Finding and exact path | Relationship / impact | Evidence and confidence | Smallest repair and verification |
| --- | --- | --- | --- |
| `publisher_landmark_probe.py`, `scan_first`, lines 493-509: optional schema observation rejected an unselected record before reaching a valid development record. | An observation path was incorrectly a selection prerequisite; malformed anatomy or a record beyond the diagnostic frame bound blocked unrelated valid records. | Proven by failing synthetic two-record tests and real Zstandard Arrow/protobuf integration fixtures. Actual publisher impact before the repair is unmeasured. | Treat ordinary decode errors on unselected records as sanitized UNSUPPORTED observations; keep selected record, identity, context and transport checks strict. Selected malformed/601-frame records still fail. |
| `publisher_landmark_probe.py`, `landmark_frames`, lines 401-408: short/bursty increasing timestamps were rejected using minimum/median cadence. | Same constant-frame-rate assumption pattern as the earlier real-video timing failure; valid measured time could be lost. | Proven with two regressions failing before the repair. No claim that an actual publisher match previously failed this way. | Preserve every increasing timestamp and check declared units against the mean period over the measured span; still reject duplicates, reversal and millisecond/nanosecond scale mistakes. |
| `publisher_landmark_probe.py`, `read_resume`, lines 546-568, and `run`, lines 607-615: inconsistent cursor counts and interrupted matches required careful retry semantics. | A resumable intake can skip a selected record or accept contradictory progress without a committed output. | Proven interrupted-match and inconsistent-count regressions; actual chunk hashes/cursor arithmetic independently checked. | Require committed examined count to equal cursor advance, and leave an unpublished selected record pending. Publish success only after writing the selected feature file. |
| `publisher_landmark_probe.py`, `scan_first`, lines 496-497: broad optional-schema handling swallowed BudgetExceeded. | Follow-up of the first repair: the one-shot deadline exception inherited the ordinary decode-error class, so later work could continue after expiry. | Proven: the new regression reports BudgetExceeded not raised on the exact pre-repair source. Root independently reproduced that failure. | Re-raise BudgetExceeded before ordinary ProbeError handling. All 17 host contract checks pass; final full cloud checks below verify real Arrow/protobuf consumers too. |

Final source SHA-256: fd868083190c212bc75dae8eb3993adf571443a42ee3c1a2d7aed32d2e484dbb. Test SHA-256: 5ee879af845ab36a70734a53d4eb7c88338825f4caa80d69a22000b262ece287. The old executed job archives remain immutable; these repairs are not retroactive changes to those executions.

## Final source verification and connected paths

[Final CPU source-check job](https://huggingface.co/jobs/Hatsunama/6aff8db0efaf5e14f4b23e01) completed with zero exit: 403 tests, zero failures/errors/skips, no Torch or TensorFlow imports. It includes real Arrow/Zstandard and protobuf round trips plus the unchanged research checks. Exact source archive SHA-256: 14b6e84b65af18be46aa4dbf80153257d6285248b464116f63604b4a8fa5db31. Independently downloaded outer receipt SHA-256: ceb2b76e1830b66d1bee6bbdae9058592349c1c68541928ee6525b5129611c83.

Inspected connections: frozen proposal and publisher split pins -> exact video/signer/prompt join -> version-pinned redirect -> bounded ETag/If-Match ranges -> standard Arrow reader -> SequenceExample context -> selected anatomical coordinates and measured timestamps -> private output publication -> resumable receipt and process exit. Tests cover success, invalid identity/headers, changing objects, unsupported selected/unselected records, malformed coordinates, wrong timestamps, budget interruption and retry.

The Android path was inspected read-only: upright CameraX RGBA preprocessing -> synchronous VIDEO Holistic results -> analyzer active-generation check -> pre-SORT anatomical hand coordinates -> buffer clearing. The React status bridge coalesces updates and is unsuitable for temporal model inputs. Existing live code discards SDK face/pose outputs. Old requested-time video probes can return repeated frames and do not prove a measured trajectory. No Android code, UI, build, installed artifact or phone state changed here.

## Next evidence gates

Do not sweep the other 99 shards blindly. Investigate a supported index/order lookup or use the official extractor on the already admitted video cohort. A different SDK or model graph must be recorded and compared; names alone do not establish feature compatibility.

A selected publisher record, if later found, remains an oracle annotation-window diagnostic. Its annotation interval differs from the padded raw clip interval. Compare equal intervals before attributing a difference to model architecture. Preserve anatomical sides, missingness, wrist motion, actual timestamps and the distinction between normalized image and world coordinates. Presence/visibility fields are retained as source fields, not invented calibrated confidence.

No additional issue was found in the inspected exact split/identity matching, strict selected-record validation or mean-cadence behavior after the repairs. That is not proof of correctness. Remaining gaps: no actual matched development landmarks, no measured native/publisher feature equivalence, no trained temporal baseline, no broad conversational-ASL capability, no final integrated runtime/latency or two-phone release qualification. Main remains unchanged; the release remains blocked. Keys and credentials are excluded from source and evidence backups.

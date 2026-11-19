# FSboard v4: completed comparison and next decision

The base, video-only A and video-plus-static B all scored 0/64 exact on the frozen matched fingerspelling holdout. Both trained arms failed this capability check. B is not meaningfully better than A, and neither is a shipping recognizer. This is a limited fingerspelling experiment, not everyday ASL translation.

## Actual execution and independent verification

[Completed evaluation](https://huggingface.co/jobs/Hatsunama/6aff710eefaf5e14f4b21f95) used the same pinned R3 source, model revision, BF16 Torch implementation and 64-by-five conditions for all three candidates. L4 allocated first; the queued L40S request was cancelled based only on allocation status. All 26 Linux prerequisite checks passed without skips before model loading. Exact adapter and optimizer bytes were verified and each loaded nonempty adapter parameter digest reproduced its trained digest before freezing.

The provider completed and the outer process returned zero with strict capture, no cancellation, no orchestration error and 960 scored records. Outer receipt SHA-256: aff5ff10a4284dcd055408ef3b5fee0c9bb242aeb1ebc351aa6810c34df26e3b. Independent readback checked all 967 captured JSON sizes and SHA-256 values. Independent full-matrix Levenshtein recomputation checked all 960 decoded scores, original proposal references/source identity fields, actual per-condition processor attestations, EOS/caps, teacher-loss arithmetic, grouped summaries and paired reference losses. A separate reviewer reproduced the metrics and checked every declared wrong-video donor against the pinned plan, including three length adaptations. No image tensor collisions occurred between the five conditions for any clip.

| Candidate | Matched exact | Matched CER | Wrong-video CER | Black CER | Matched reference NLL |
| --- | --- | --- | --- | --- | --- |
| Base | 0/64 | 189.84% | 186.19% | 93.81% | 6.97935 |
| A: video only | 0/64 | 91.11% | 90.79% | 92.06% | 5.06446 |
| B: video plus static | 0/64 | 90.48% | 90.16% | 92.06% | 5.05862 |

CER is total edits / 630 normalized reference characters; it may exceed 100%. NLL is teacher-forced nats per reference token, including EOS, weighted over 272 tokens. It is not a recognition confidence or accuracy measure. All A/B generations stopped at EOS; base reached its cap on 21 matched clips.

Both trained arms had slightly better CER and teacher-forced loss with unrelated video than with matched video. Their outputs did respond to input changes, but this does not establish correct visual interpretation. One guessed name occurred on 50/64 A clips and 35/64 B clips. All 192 training targets were distinct and that guessed name was absent from training, development and holdout targets, so repeated target selection does not explain the collapse. The precise contribution of temporal sampling, frozen visual features, optimization and data scale remains unresolved.

## Clear-superiority decision

B saved only four edits over 630 characters (0.635 percentage points). It had fewer edits on nine clips, more on five and tied on 50; 44 normalized outputs matched A. Exploratory paired bootstrap intervals for B minus A include zero: clip CER interval -3.21 to +1.90 percentage points; publisher-signer-ID grouped interval -2.24 to +1.55 over 14 IDs. The grouped reference-NLL interval also includes zero. These exploratory intervals have no multiplicity adjustment, and publisher IDs are not independently established physical identities. Zero observed exact matches is not proof of population zero accuracy.

No winner is awarded. Stop further B-arm spending and do not extend this failed paired regime blindly. Preserve both exact checkpoints and all evidence in the labelled private buckets. A may remain the simpler engineering reference for targeted diagnosis; that is not a claim that A won. Future training/integration goes to one candidate only when independent capability and resource evidence justify it. Training loss alone and small metric differences are insufficient.

## Cascade status

| Original issue | Status and evidence |
| --- | --- |
| Variable-rate terminal frame rejection | Repaired; all 22 original regressions and the full unchanged 288-clip admission passed actual decoding. Legacy defaults remain unchanged. |
| Input verification timeout | Resolved by capped continuation; all 288 matched encodings, 320 controls, 80 static images and lazy consumers verified. Three-clip timing reproduced severe CPU oversubscription with identical encodings; the historical thread count remains unknown. |
| Source packaging and hidden fixture dependencies | Repaired; independent source closure has 382 checks, no failures/errors and four host symlink skips, without local Torch imports or native builds. |
| Wrong generation EOS | Repaired; explicit text/assistant stop IDs and no interior continuation checked on every actual result. Earlier scores remain unchanged. |
| False or contradictory capture completion | Regression-first repairs cover final-output closure, consistent cancellation, scoped partial-target cleanup, sibling preservation and checkpoint retry verification. Actual completed pair/evaluation receipts bind all expected outputs. |
| Post-rename admission cleanup | Repaired for future admission: own directory identity distinguishes committed data from an unrelated directory. All 23 focused checks pass; the original executed R3 archive stays immutable. |
| Superseded manual launchers | Retired after proving no active callers and verifying exact private backups; historical commands and receipts preserved. |
| Reliable recognition and everyday-ASL meaning | Unresolved: this actual comparison failed. Passing source and training-graph checks did not prove capability. |
| Accelerated Android runtime and interactive latency | Unresolved. Historical raw native logs prove pipeline/numerical failures for their tested configurations. CPU probe times and synthetic-operator tests do not qualify final-app latency. |
| Final integrated app, rights, signing and release | Unqualified. Main remains unchanged; no phone or production app appearance/behavior changed during this research update. |

## Smallest next experiment

The existing Android Holistic worker already has timestamped anatomical hand trajectories before SORT and buffer clearing. Reuse that actual signal for a small temporal visual baseline, preserving wrist motion, missingness and measured time. Check publisher-provided matching landmark records before writing another extractor. Keep the existing React status bridge out of temporal sampling. The old requested-time video probe can repeat frames and is not evidence of a full measured trajectory.

The FSboard publisher reports a landmark-based recognizer on a far larger corpus and signer-disjoint split, supporting this route as a testable alternative. Its results are not predictions for our small cohort, nor evidence of everyday translation. The source also releases the data under CC BY 4.0 and explicitly limits its task to fingerspelling. [Publisher](https://arxiv.org/html/2407.15806v1#S4)

General meaning remains a separate data and validation task. FLEURS-ASL provides licensed translated monologues and published expert back-translations, not an established broad conversational training corpus. Its original English prompts can differ from the signed meaning; keep expert back-translations distinct. Other inspected datasets have explicit noncommercial/research restrictions or release identifiers without blanket video rights. Free download is not sufficient evidence of downstream permission. No new Deaf testers or customer-data collection is proposed. [FLEURS-ASL](https://arxiv.org/html/2408.13585v1) - [ASLLRP terms](https://www.bu.edu/asllrp/dai-terms.html)

No additional source defect was found in the inspected prefix/teacher separation, actual control-image routing, model isolation and stop enforcement. This scoped result is not proof of correctness. Missing evidence includes new temporal-model capability, independently aligned conversation coverage, cross-source identity/leakage, exact final-app performance and complete release qualification on both free phones. Signing keys and credentials remain excluded from all backups.

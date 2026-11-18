# Static ASL teaching material: admission and transfer plan

Checked November 18, 2026. This is a research record, not a model-quality or release acceptance result.

## Decision

Investigate clean static photographs as an auxiliary handshape task. Keep sentence training and assessment grounded in real signing sequences. Drawings are a lower-priority domain-transfer hypothesis. Neither the two downloaded charts nor a high still-image score establishes general everyday ASL translation.

The existing pilot cannot answer the transfer question. Its 16 size-selected training clips and four reused development controls are small and translated general-text material. They lack fingerspelling annotations and verified signer separation. Their word-overlap metric is not a meaning judgment. No static-auxiliary model has been trained.

## Verified reference and diagnostic inputs

| Input | Evidence and current role |
| --- | --- |
| Cwterp photographic alphabet, hands-only and labelled companion | Original [hands-only page](https://commons.wikimedia.org/wiki/File:ABC_pict_just_hands.png) and [companion page](https://commons.wikimedia.org/wiki/File:ABC_pict.png) state public-domain dedication by Cwterp. Both exact PNGs were retrieved; publisher byte counts and SHA-1 values matched, and local SHA-256 values were recorded privately. They remain annotation references. |
| Dan Rasband ASL Alphabet Test v1 | The [uploader card](https://www.kaggle.com/danrasband/asl-alphabet-test/home) declares CC0, 870 photos at 200 by 200 pixels and 30 examples per A–Z/delete/space/nothing class. One original A/A0001_test.jpg was anonymously retrieved through the official Kaggle dataset API: 10,570 bytes, SHA-256 4c22cf21532007e15036d69e6d511ed050cea28eeb6bbb65488fe19dd70bba71. Its declared label is A. It is a source-separated photo diagnostic candidate, not admitted training or independently verified labels. Signer/session provenance remains unknown. |

The chart review found retained R/L viewpoint notes, drawn J/Z motion arrows, irregular layout and an extra I-love-you gesture. Alphabet order remains a shortcut even without printed letters. Some original views show the sign from a side rather than a typical observer view. Check every intended crop, orientation and annotation; simple regular-grid slicing is unsuitable. A second chart derived from the same original is not independent evaluation data.

The sample Kaggle image was visually inspected as a hand photograph with a real background. This is not independent linguistic validation of the full dataset or proof that a source-disjoint split is signer-disjoint.

## Other candidates, not admitted

- [ASL-HG v1](https://data.mendeley.com/datasets/j4y5w2c8w9/1) declares CC BY 4.0 and 36,000 stills from ten volunteers. Its supplied 80/20 split is not documented as signer-disjoint. Its [paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC12877850/) reports research/academic participant assurances; clarify that boundary before any shipping-model use. Static J/Z images and claimed numeric conventions also need linguistic checking. No archive was downloaded.
- [SignAlphaSet v3](https://data.mendeley.com/datasets/8fmvr9m98w/3) is an additional CC BY 4.0 still/video lead. Actual file inventory, label quality, participant grouping and permitted uses still need independent admission checks.
- Course and worksheet materials can help author our own evaluation rubric, contrast cases and handshape annotations. A page's open license cannot automatically license external videos, fonts or illustrations embedded within it. Keep externally hosted media out of training until their own grants are verified.
- Do not treat the commonly mirrored Gallaudet-font chart, a third-party school PDF hosted on a government site, or a free-view dictionary as admitted merely because the surrounding website is accessible. Prefer the verified public-domain photographic source where it supplies the needed responsibility.

## School curricula and sequence-data leads

- [William G. Vicars's explicit CC BY clip list](https://www.lifeprint.com/asl101/topics/creative-commons-attribution.htm) identifies six particular teaching videos with English references and ASL glosses. This grant is specific to those clips; it does not cover all of Lifeprint. The designation's exact CC version and an authorized source-file acquisition route still need recording before ingestion.
- [ASCCC's school curriculum catalog](https://asccc-oeri.org/open-educational-resources-and-american-sign-language/) lists Melanie Nakaji's ASL 1/2 and Classifier Curriculum under CC BY. The catalog describes ASL 1 Version 4 as 14 modules with English title cards around signing. Individual Canvas media and their original grants were not verified through unauthenticated access. These are curriculum leads, not admitted videos.
- [Georgia Tech/DPAN PopSign v2.1](https://signdata.cc.gatech.edu/view/datasets/popsign_v2_1/index.html) explicitly declares CC BY 4.0 and 562 isolated signs, with recording-consent information. Its [download guide](https://signdata.cc.gatech.edu/view/guides/downloading_popsign/index.html) supports sign/category/split archives, not individual sample downloads. It warns that preview collages alter resolution and timing; do not use them as original training videos. Audit actual signer groups and original clips before admission. Isolated-sign performance cannot be presented as continuous-ASL translation.
- [Richard Goodrow's BOOK clip on Commons](https://commons.wikimedia.org/wiki/File:ASL_BOOK_justsign.ogv) provides a small independently described movement example under CC BY 3.0. It is a 44,418-byte, roughly 0.7-second isolated sign, not a sentence or fingerspelling evaluation set. The original was not downloaded in this pass.
- LibreStudio's legacy school activity collection has migration/access problems. Activity titles are not aligned signed-utterance labels, and external embedded media do not inherit a course license automatically.

These concrete leads reduce the need to buy material. They still require file-level checks; no curriculum video or PopSign archive was ingested, and no human subject identities were inferred.

## Controlled experiment and release limits

1. Preserve the present video-only adapter and all failure evidence. Admit isolated, checked static inputs with a task-specific prompt. Keep J/Z, motion arrows and the extra gesture outside the 24-static-letter objective.
2. Freeze a real-video assessment before choosing curriculum weights. Include fingerspelled names/words, repeated letters, J/Z and everyday meaning contrasts such as negation and questions. Record actual references, source families, subject separation when known, timestamps and any uncertainty. Do not fabricate signer IDs from article URLs.
3. Compare two arms initialized identically, with the same ordered 80 video microbatches, 40 optimizer updates and video loss. A tentative auxiliary weight of 0.05 is a development choice, not a validated default. Use a separate static sampler and separately backpropagated auxiliary loss within the same update. Report added compute and gradient/clipping differences.
4. Assess independent real-photo diagnostics separately from real-video spelling and sentence meaning. Record terminated versus capped decoding. Use character/word accuracy for spelling and reliable meaning adjudication for sentences. Wrong-video, shuffled and black inputs test different failure hypotheses.
5. Keep, remove or call the static auxiliary inconclusive from that evidence. A small feasibility comparison cannot establish commercial coverage. Application integration, quantization, phone fit and exact-artifact runtime acceptance are later gates.

## Completed cloud pilot and implementation repairs

The first four-image LoRA trial completed but repeated one output on all four clips and did not distinguish temporal order. A denser 32-image trial initially exhausted L4 memory while constructing vocabulary logits for ignored prompt positions.

The repair computes causal cross-entropy only at supervised predictor positions. A failing regression preceded it. The CUDA helper compares the loss and hidden/head-weight/head-bias gradients with dense ignored-label cross-entropy on a controlled tensor fixture; it is not a full-Qwen gradient-parity claim. The repaired real Qwen job completed all 40 optimizer updates, saved checkpoints and paired baseline/adapter evaluations, and peaked at 18,267,689,984 allocated GPU bytes (about 17.01 GiB). All 20 lightweight helper tests passed locally and in that cloud job.

The dense adapter still had zero normalized exact matches on four reused clips, two distinct matched outputs, and two of four matched outputs equal to their temporal reorder. Mean token F1 was 0.1335 matched, 0.1050 reordered and 0.1788 black. The frozen and trained generations also differ in token-cap frequency. These observations fail to demonstrate reliable translation and cannot select a static curriculum.

A separate handled-intake failure previously stranded a partial dense directory. Recovery now removes only unpublished output created by that invocation and retains pre-existing/completed data; regression fixtures cover retry and cleanup failure. Hard kills still require the documented explicit recovery procedure.

Five jobs are terminal. The ledger estimates US$0.191162 in minute-rounded execution charges, excluding storage/startup and without invoice verification; the authorization remains US$14, with a US$2 reserve. The dedicated private bucket is Hatsunama/sign-to-speak-qwen-20261118-d460a4 and the job agent label is sign-to-speak-qwen. No app source, UI, release artifact, signing credential or phone setting changed for this investigation.

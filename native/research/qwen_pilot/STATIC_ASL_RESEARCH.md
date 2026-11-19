# Static ASL teaching material

Research record for the November 18, 2026 pilot.

## Exploratory use

The completed controlled comparison tested static handshape recognition as a small auxiliary task alongside real signing sequences. The fresh fingerspelling results did not establish a transfer benefit. A separate static-only probe demonstrated training fit on four photos. See the paired report for the frozen protocol, aggregate results, evidence limits, private archive verification and compute estimate. [Paired research results](PAIRED_RESEARCH_RESULTS.md)

Keep sentence training and evaluation grounded in real signing sequences. Drawings remain a lower-priority domain-transfer hypothesis. J/Z require motion and were excluded from the 24-letter static objective.

## Verified reference and training inputs

| Input | Evidence and current role |
| --- | --- |
| Cwterp photographic alphabet, hands-only and labelled companion | Both exact PNGs were retrieved. Publisher byte counts and SHA-1 values matched, and local SHA-256 values were recorded privately. Cwterp's public-domain dedication covers both images. They remain annotation references. [Hands-only chart](https://commons.wikimedia.org/wiki/File:ABC_pict_just_hands.png) · [Labelled companion](https://commons.wikimedia.org/wiki/File:ABC_pict.png) |
| Dan Rasband ASL Alphabet Test v1 | The uploader declares CC0 and 870 photos across A–Z, delete, space and nothing. The verified 25,489,344-byte archive contained those 870 canonical photos and one complete wrapped copy of each. The original A anchor had 10,570 bytes and SHA-256 `4c22cf21532007e15036d69e6d511ed050cea28eeb6bbb65488fe19dd70bba71`. Contact-sheet screening compared 120 candidate images across 24 non-motion letters against the Cwterp chart and excluded four for uncertain or clipped hand details. The approved manifest contains 116 photos for exploratory training. The screen assessed visual plausibility and visibility. Independent ASL-fluent label certification and signer/session provenance remain unverified. [Uploader card](https://www.kaggle.com/danrasband/asl-alphabet-test/home) |

The chart review found retained R/L viewpoint notes, drawn J/Z motion arrows, irregular layout and an extra I-love-you gesture. Alphabet order remains a shortcut even without printed letters. Some original views show the sign from a side rather than a typical observer view. Check every intended crop, orientation and annotation. A chart derived from the same original shares its source and should stay in the same evaluation group.

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

## Earlier sequence pilots

The first four-image LoRA trial completed but repeated one output on all four clips and did not distinguish temporal order. A denser 32-image trial initially exhausted L4 memory while constructing vocabulary logits for ignored prompt positions. The repair computes causal cross-entropy only at supervised predictor positions. The CUDA helper checked loss and hidden/head-weight/head-bias gradients against dense ignored-label cross-entropy on a controlled tensor fixture. Full-Qwen gradient parity remains unmeasured.

The repaired dense pilot completed 40 optimizer updates, peaked at about 17.01 GiB allocated GPU memory and passed 20 helper tests locally and in the cloud job. It produced 0/4 matched exact transcripts on reused development controls. A separate intake repair limits cleanup after a handled failure to the unpublished directory owned by that invocation. Hard kills still require explicit recovery of partial output.

Those pilots predated corrected display rotation and the 80-update comparison. The newer results, static-only probe and native-video boundary are recorded in the [paired research results](PAIRED_RESEARCH_RESULTS.md).

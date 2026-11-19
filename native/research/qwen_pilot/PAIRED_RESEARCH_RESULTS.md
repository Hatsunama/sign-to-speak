# Sign To Speak paired research results

The November 18, 2026 pilot tested whether a small static handshape objective improved transfer from signed-sequence training to fingerspelling. The frozen base and both adapters scored zero exact matches on eight fresh FSboard clips. The auxiliary arm made more character edits than the video-only arm on those clips. A follow-up static-only probe fit four training photos. Together, these results are grounds to hold both adapters at the research stage and leave the broader static curriculum question open.

## Controlled comparison

The frozen protocol `sts-static-ablation-20261118-v1` used Qwen3.5-0.8B at revision `2fc06364715b967f1860aea9cf38778875588b17` with a shared LoRA initialization. Both arms completed 80 optimizer updates over the same 160 video microbatches in the same order. Each clip used 32 ordered RGB still images. The 16 size-selected 2M-Flores-ASL clips trained the sequence objective on translated general text. Four previously inspected clips served as development controls. The training clips lacked fingerspelling annotations and verified signer separation from the development controls. [Qwen base model](https://huggingface.co/Qwen/Qwen3.5-0.8B/tree/2fc06364715b967f1860aea9cf38778875588b17) · [Meta 2M-Flores-ASL, CC BY-SA 4.0](https://huggingface.co/datasets/facebook/2M-Flores-ASL/tree/b450c1a427738e78f06362fc4619674f5d74f774)

Arm A used sequence loss alone. Arm B added one separately backpropagated static microbatch per update at a loss weight of 0.05 alongside the same video objective. The 80 static slots used 80 distinct photos from 116 approved for exploratory training and covered all 24 eligible letters. Both arms clipped gradients at all 80 updates. B spent 42.6 seconds on static forward and backward passes and took 587.8 seconds in training, versus 542.7 seconds for A. The four reused controls yielded 0/4 matched exact transcripts per arm. Their word-overlap scores measure text overlap. [Dan Rasband's ASL Alphabet Test v1, publisher-declared CC0](https://www.kaggle.com/danrasband/asl-alphabet-test/home) · [Static ASL admission and source review](STATIC_ASL_RESEARCH.md)

## Fresh fingerspelling and static diagnostics

The evaluation used eight clips from eight distinct source recordings and eight dataset signer IDs in the official FSboard v13 test split. The overlap check compared recorded IDs, source fields and digests. FSboard signer IDs are local to that dataset, and the supplied references still need independent ASL-fluent review. [Google AI FSboard v13, CC BY 4.0](https://www.kaggle.com/datasets/googleai/fsboard/versions/13)

| Candidate | Matched exact | Matched character edits / reference characters (CER) | Reordered edits | Black edits | Wrong-video edits |
| --- | ---: | ---: | ---: | ---: | ---: |
| Frozen base | 0/8 | 108/54 (200.0%) | 131 | 52 | 105 |
| A, video only | 0/8 | 107/54 (198.1%) | 115 | 231 | 110 |
| B, video plus static | 0/8 | 143/54 (264.8%) | 115 | 111 | 148 |

CER is total normalized Levenshtein character edits divided by total normalized reference characters. Insertions can push it above 100%. All matched generations reached end of sequence. The base hit the 32-token cap once on reordered input. All control conditions also scored 0/8 exact for each candidate. Reference negative log-likelihood favored the matched over the wrong clip on 3/8 clips for the base, 3/8 for A and 4/8 for B. Outputs sometimes changed with the visual input, while the mixed reference likelihood and transcription results leave reliable visual grounding unestablished.

The post hoc photo diagnostic selected one image per available letter in each group. On the 24 photos seen by B, exact counts were 0/24 for the base, 1/24 for A and 1/24 for B. On 19 unused photos, the counts were 0/19, 0/19 and 1/19, respectively. The unused images came from the same source family and training-eligible pool. Signer and session separation remain unknown, and an independent ASL-fluent reviewer has yet to certify the labels.

The static-only probe used four approved training photos, one per update at full objective weight, with no video batches. It scored 0/4 before training and 4/4 at 50, 100 and 200 updates. At those later checkpoints it produced four distinct exact outputs for the four photos and a single output for the black control. This demonstrates that the static training path can fit those four examples. The paired outcome leaves limited training exposure, task coverage and interference between objectives as possible explanations. The probe isolates none of those explanations and provides no estimate of generalization.

## Reproducibility and cost

The intake repairs apply supported nonmirrored display rotations to decoded video frames and account for FSboard duration rounding by allowing one frame plus one container tick per endpoint. The static ZIP admission accepts a complete byte-identical wrapped mirror, counts it against archive limits and admits only the canonical photos. Regression tests cover the corrected cases and rejection paths. The GPU diagnosis originally executed 117 cases covering 113 unique test IDs, including four duplicate fixture imports. These passed along with a controlled CUDA loss-and-gradient fixture; full-model gradient parity is unmeasured. Final review removed the duplicate discovery and repaired unchecked LoRA report fields: rank, alpha, dropout and target modules are now compared with the digest-verified adapter configuration. The metadata regression failed on all four mutations against the saved pre-fix source and passes after repair. All 114 final unique CPU tests passed locally and in a cloud job that also accepted the actual archived pair inputs. Model inputs, weights, objective and measured outcomes did not change; training was not rerun.

Each adapter's private archive contains 149 files, including 80 step receipts and eight complete optimizer checkpoints. Archive verification read back the status receipts and checked copied paths by Xet hash and size. The receipts distinguish the paired digests of adapter configuration and weights used for evaluation and archive verification from the SHA-256 of the final weights file alone.

| Receipt or artifact | SHA-256 |
| --- | --- |
| Frozen protocol | `b106018ee2f9191b202e48bbe01e90da7326e3d1738be6bc2a2959d9e0b03714` |
| Oriented video admission | `d863fc578f8d43614d307bf6332847f7021b7af2ffa8ba697d6913c06e704514` |
| Approved static manifest | `55d6034693b3118c4e7fc87f6191effd831410d22d9cb96225c35eb7dc1b0d2e` |
| Paired schedule | `6b1980c513f627f3b4bad9cec92c6cda951f0e1fe77fececeb0b319d3f8f27fe` |
| A adapter configuration and weights | `291e1c0fb25decfe445e6430f6a500901219b082c2fcff51d930372b320c4560` |
| B adapter configuration and weights | `169e260ca0758bb9e7136c342c9fad691e9101b94608a98861376fd241e11642` |
| A `adapter_model.safetensors` | `11dbc6cd9ab57e3da01b67cc88c64743866a435ddab84d79e45e914d3286f20f` |
| B `adapter_model.safetensors` | `b2e44d25a8a59fc3890c021f8e9f9f99a170235eb57f3988f19be5b1408d40cc` |
| Approved FSboard admission | `5be4dd8a5d5225c8131eab855f521a83382a4da3f18804170e917ff381ebd8ac` |
| FSboard evaluation configuration | `5a10654326a871288f1f3e1e3b1c13512157e4ead5c33de7c8eb18f83413be17` |
| Static diagnostic configuration | `2c2637913f116539c36e5a7ad4a4f09c2bd803aab6412f401c9e8565a2c88dbe` |
| Static-only probe configuration | `a5a6cedd8fe1d73dcaf5850396602dee4170232c5528ebbe163d52835a53bc17` |

At the comparison checkpoint all 16 data/training/diagnosis jobs had reached terminal states. Estimated minute-rounded compute was US$0.610985. Two subsequent CPU export/quantization jobs and the receipt-repair verification job also completed; the complete 19-job execution estimate is US$0.611818. This includes US$0.039999 for the final 158-second probe, rounded to three minutes. Invoice, storage and startup charges remain unverified. The authorization was US$14 with US$2 reserved for storage and startup.

## Next evidence gates

The next comparison needs independently reviewed static labels, a source- and signer-separated photo holdout, and direct training coverage of real fingerspelling sequences. Freeze a larger video assessment before tuning, with varied signers, repeated letters and motion letters J/Z. Everyday ASL translation requires a separate evaluation of sentence meaning with ASL-fluent adjudication.

A future native-video experiment needs its own versioned input contract with processor timestamps and temporal pairing. The current pair used ordered stills throughout. The GitHub runtime build `44443603415` succeeded, and the original pinned base was converted and quantized in cloud CPU jobs. Exact-artifact inference and phone acceptance remain unverified because the host ADB inventory lists no connected devices. See [runtime preflight results](RUNTIME_PREFLIGHT_RESULTS.md). The training work made no app, main-branch, release, signing-key or phone changes.

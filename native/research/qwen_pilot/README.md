# Sign To Speak Qwen pilot

Cloud-only research scripts for a bounded ASL video-to-English experiment. The mobile application does not consume these checkpoints.

Run the dependency-free helper suite with `python -B -m unittest discover -s native/research/qwen_pilot -p 'test_*.py'`. Real media decoding and GPU inference require the separately authorized cloud jobs.

The admission job checks pinned public Meta dataset metadata, bounded individual video downloads, split grouping, actual decoded timestamps and model identity. It stores frames, attribution and the admission receipt under a mounted private `/data/pilot` directory. Original videos use ephemeral storage.

The training job compares the frozen Qwen model with a bounded LoRA run using matched, reordered and blank four-image inputs. It saves per-step losses, adapters, optimizer state and paired output receipts. Admission has eight frames per clip. The four-frame training input has large temporal gaps and establishes pipeline execution only. Signer independence and broad translation quality require further evidence.

Use only permitted training/evaluation materials, preserve the upstream CC BY-SA attribution, and keep real user content and credentials out of these jobs. Model weights retain their own terms. A successful scheduler status must be reconciled with parseable receipts, the final checkpoint and evaluation outputs. Interrupted writes can be partial. Keep an explicit compute and storage budget and a timeout for every paid job.

Pin the model revision from the admission receipt, and use the tested dependency file and a compatible CUDA PyTorch image. Production model admission, on-device execution and release acceptance are separate required work.

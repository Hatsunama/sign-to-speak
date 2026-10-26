# Pocket TTS notice receipt pending review

The app bundles only the pinned Kyutai English 18M model, tokenizer, and Alba
embedding. The original Kyutai MIT text is in
`native/vendor/pocket-tts-runtime/KYUTAI-MIT-LICENSE`; the generated asset
bundle includes it and CC BY 4.0 attribution for Kyutai and Alba MacKenna.

The Cargo graph's actual license files have not yet been admitted. On the
approved hosted runner, after fetching the locked sources and generating full
Cargo metadata, run:

```sh
python native/scripts/prepare-pocket-assets.py --collect-license-receipt \
  --metadata "$RUNNER_TEMP/pocket-arm64-v8a-metadata.json" \
  --output-dir "$RUNNER_TEMP/pocket-license-candidate"
```

Review `candidate-cargo-license-files.json`, `candidate-notices-index.json`,
and every file under `candidate-notices/`, including nested SentencePiece and
Abseil notices. The candidate is an inventory, not redistribution clearance.
After review, pin the JSON as `cargo-license-files.json` in this directory.
Normal preparation rejects missing, changed, or incomplete receipts; no model
weights or generated notices belong in Git.

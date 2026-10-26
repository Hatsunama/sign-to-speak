# Pocket TTS Cargo notice receipt

`cargo-license-files.json` pins the source notice inventory produced by hosted
run [40353053153](https://github.com/Hatsunama/sign-to-speak/actions/runs/40353053153)
at source `19ad3f4bdb3681f3cb70a60f4b3d9b6b87aae67a` and Cargo.lock SHA-256
`8616b84132d890a9853e7d3230455c563e7e903a8e4e4ea7be0f01e36ddfa1b3`.
The reviewed receipt SHA-256 is
`5d69b289fc83ce8f964197703fe0cc4ad3f7e31225ada6f4dcf13f1399feeaec`.

The 133 locked package identities, 241 notice files and their exact byte hashes
were checked against the candidate index. The selected third-party declaration
routes have corresponding full texts. Nested SentencePiece/Abseil and KleidiAI
notices are retained. Six crates need the upstream texts and provenance in
`supplemental/`; the collector also retains r-efi's full MIT grant in AUTHORS.
This review covers this Cargo source inventory. It does not establish complete
APK dependency coverage, legal clearance or Android runtime/release acceptance.

The app bundles only the pinned Kyutai English 18M model, tokenizer and Alba
embedding. The original Kyutai MIT text is in
`native/vendor/pocket-tts-runtime/KYUTAI-MIT-LICENSE`. Generated assets include
that text, CC BY 4.0 attribution for Kyutai and Alba MacKenna, and the pinned
Cargo notice files. App Apache-2.0 and template MIT notices are packaged by
`license-assets.gradle`.

Normal asset preparation rejects missing, changed or incomplete receipts.
A new dependency or source-notice change requires regenerating and reviewing
the candidate inventory before updating this receipt. Generated model weights
and notice bundles do not belong in Git.

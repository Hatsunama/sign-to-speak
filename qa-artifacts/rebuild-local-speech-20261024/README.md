# Rebuild evidence and handoff

Read [FINAL_REPORT.md](FINAL_REPORT.md) for scope, repaired failures, original-issue status and unresolved commercial blockers. Tested application code is pinned to `4ec7c8a38d9444db4d6ef2b3506faa27b0b5a292`; report commits are not new APKs.

- `evidence/red/`: genuine fail-before camera/audio regressions plus separately classified measurement-fixture/actor failures.
- `evidence/final/ci/`: raw final Android JUnit, native/JNI and intentional counterfactual receipts.
- `evidence/final/phones/`: exact APK hashes, installed-hash/cold-start/runtime, UI baseline comparison and helper cleanup.
- `evidence/final/audio/`: independent positive-control/protected internal-audio capture and timing overlap.
- `evidence/final/visual/`: independently validated screenshot/frame counts, phase timestamps, source/signing receipts and minimal window geometry.
- `capture-probe/` and `visual-probe/`: exact owned local probe sources; no capture service API or key material. Visual bytecode stayed unchanged when its temporary Activity theme was corrected.
- `orchestration/`: the local procedure source. Historical scripts expect their original `java-check/` layout; SDK/JDK/ADB paths and device serials are local references, not portable configuration. Restoring the original layout and adjusting tool paths is required before reproducing. Do not use these scripts to generate keys or publish a release.

No installable APK, private key, credential file, raw PCM, camera frame, personal overlay screenshot or recorded video is included. Evidence SHA-256 values are listed in `evidence-sha256.json` (the manifest excludes itself). Archive copies on the PC are retained under `C:\sign_to_speak`, not inside the unrelated xMilo project. Production signing and store builds remain intentionally unconfigured.

## Shared machine and device handoff rules

RAM is low: respect other agents' memory/paging limits, do not change the paging configuration, and do not run local Gradle/emulator or parallel heavy builds. Production signing and separate store builds are deferred.

Before any future device test, coordinate ownership and check the current foreground app. Never use a phone while another app/agent has it open. If readiness checks are next and both phones are in use, end the turn and report that blocker. Return phones to Home in success/failure cleanup whenever no longer using them. The historical orchestration scripts are evidence, not device-ownership arbitration; do not run them blindly. Both phones were returned Home and the three temporary capture helpers removed at this handoff.

# Local speech integration reconstruction

This work starts from verified follow-up foundation `10bc6fa8d4d5baa4e44c3fcebd75b2d675ba3b07` (foundation `78c71bd36bbd0f04c86bd85e4b795da35bcd56f7`). It reconstructs the missing native speech/session integration. It is not a recovery of the unpublished 477-file candidate, and does not reproduce that candidate's source inventory or APK.

## Scope

The existing Home/Thank you UI, camera preview behavior, text, styles and renderer remain unchanged. No ASL recognizer, translation pipeline, transcript, speech input screen, or automatic speech is added. Speech is a native backend capability tested with synthetic text through its production React module. The existing visible statement that translation is unavailable remains accurate.

Flite and the English SLT voice are compiled from the reviewed public component source at `6999e43b16c41f574b9ad3914554c7ea71fa84f88`, pinned to upstream `e9e2e37c329dbe98bfeb27a1828ef9a71fa84f88`. Synthesis and playback use device resources only, without service APIs or runtime model downloads. API 26–28 keep preview support and reject speech because the required playback-capture policy is unavailable. API 29+ speech additionally requires a resumed Activity and verified capture policies.

## Repairs and impact checks

- Replaced the empty speech stop connection with generation tokens and native Back/pause/destroy cancellation. Tests cover mounted React cancellation, stale begin/token callbacks, native lifecycle, cleanup faults and recovery.
- Bounded native requests, mutable input and PCM ownership. Dispatch allocation failure and delayed rejected work are regression tested; fatal errors propagate after cleanup. Cleanup uncertainty blocks subsequent speech for the process lifetime.
- Added a capture-protected PCM player with audio focus, bounded nonblocking writes and playback-head drain. Failure/cancellation and persistent cleanup-fault tests exercise the actual Android adapter with controlled AudioTrack dependencies.
- Preserved React Native default OnLoad/autolinking while adding a separate `private_tts` library. Read-only source preparation checks verify upstream and generated bodies during CMake configuration. APK audits require the actual speech library, bundled JS and byte-identical notices.

## Verification boundary

Local host checks have passed. Android compilation, Robolectric, native C/JNI execution, and the exact phone app/test artifact must still pass hosted checks and runtime testing. Historical CI or APK evidence does not validate this reconstruction.

The phone instrumentation target is opt-in, isolated as `.qa`/`.qa.test`, test-only and unsigned. Signing stays on the user's PC with the existing key. No key generation, export, credential transfer or publishing is part of this work.

**Release blocked until the exact final artifact is cold-started and affected features pass on both phones.** Independent playback-capture exclusion and any unavailable runtime evidence must be reported as gaps. Source scans, a build, installation or agent reports cannot close those gates.

# Local Flite synthesis source

The runtime is offline and uses the in-tree CMU SLT English voice. It accepts at most 256 printable ASCII bytes and returns at most 30 seconds of 16 kHz mono PCM16. Synthesis does not intentionally log input text or PCM, and Flite `cst_errmsg` output is suppressed. Android NDK unwind diagnostics may still emit runtime messages, so the packaged binary is not guaranteed to be silent. The synthesis path does not access files, devices, network or system speech services. The Java session owns playback and wipes its PCM when finished.

`prepare_sources.py` verifies `source-manifest.json` and `source-receipt.json` against each of the 171 unchanged upstream files before writing the 50 generated files. Repeating the command must produce identical hashes. Production CMake compiles 90 explicit C units and the C++ JNI bridge; the preparation script and manifests are build provenance and are not packaged as application assets.

The native API is one synthesis at a time. Opaque IDs and shared request lifetime keep cancellation safe while synthesis is in flight. Cancellation is cooperative; the Java session checks its generation before playback. The wrapper wipes tracked heap, its text copy and released PCM. It does not claim to erase compiler registers or all upstream stack scratch.

Run `native/tests/flite/test_private_tts.c` through the non-Android CMake test target when a host C compiler is available; `test_request_registry.cpp` checks registry closure and cancellation. Android builds use NDK 27, API 26 or newer, and 16 KiB ELF segment alignment.

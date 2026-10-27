# Reviewed framework notices (bounded subset)

These four files are staged into every Android variant at `assets/framework/`.
The APK audits compare the packaged bytes with this directory and pin each file's
SHA-256 in `scripts/check-phone-qa.py`. This is a subset of the framework and
runtime attribution inventory, not a complete third-party notice bundle.

| Staged file | Exact source | SHA-256 of staged bytes |
| --- | --- | --- |
| `react-native-LICENSE.txt` | `node_modules/react-native/LICENSE`, npm `react-native@0.86.3` | `da6d3703ed11cbe42bd212c725957c98da23cbff1998c05fa4b3d976d1a58e93` |
| `react-native-safe-area-context-LICENSE.txt` | `node_modules/react-native-safe-area-context/LICENSE`, npm `react-native-safe-area-context@5.7.0` | `f5c7a4d527258e11fdfe3d84eb73cd14fe6d64224ef051e59f6ee21fe378800c` |
| `ndk-libcxx-NOTICE.txt` | NDK `NOTICE.toolchain` byte slice `[127769, 144473)` | `3f3d1da78246d5c2ffc879f8239d6e856b19ba813b8349cbe48f85bd018ee3af` |
| `ndk-libcxxabi-NOTICE.txt` | NDK `NOTICE.toolchain` byte slice `[144473, 161180)` | `b043b7c5fdaf8df24b2c3b50f7651e6acfc8bdd73cc152ec5d090b8a7e0ede33` |

The npm source files were copied byte for byte from the installed packages
selected by `native/package-lock.json` (SHA-256
`68b10e85a8fdb3b1f72d77c78d2aabe3b33d5752e35b620af338afe5d5b68875`
for the Git LF bytes; a Windows CRLF working copy was recorded as
`400c6fe95dee5adf6377cff36b0329feb10f5b1a7b18b97e76f2a02fd3280adf`).
The lock entries pin `react-native@0.86.3` to integrity
`sha512-JR5s3bM9ezud+Mw24GlNXNfthqPIKwrQgPPJcam+L97t2sKjjEavhCzBn+fyqZZRcM5+XlhYxpTxVkK7e1n38Q==`
and `react-native-safe-area-context@5.7.0` to integrity
`sha512-/9/MtQz8ODphjsLdZ+GZAIcC/RtoqW9EeShf7Uvnfgm/pzYrJ75y3PV/J1wuAV1T5Dye5ygq4EAW20RoBq0ABQ==`.

The NDK source was the installed Android NDK `27.1.12297006` (`r27b`),
`C:/Users/1thea/AppData/Local/Android/Sdk/ndk/27.1.12297006/NOTICE.toolchain`,
806124 bytes, SHA-256
`cbe3237be53c0a819f8df6aac5358fdee848eee3b6571b4e2ca20767ebd7465e`.
Its `source.properties` records `Pkg.Revision = 27.1.12297006` and has SHA-256
`e9b907b9c90bc2f0aaa87049580cc0c3ee0bb6aff50b58e4d1a8876ba3aeb026`.
Each extracted slice begins at the complete `The LLVM Project is under the
Apache License v2.0 with LLVM Exceptions` heading and ends after the
respective `libc++` or `libc++abi` dual-license text, immediately before the
next repeated LLVM heading. These slices are contiguous and occur once in the
aggregate at those offsets. The full aggregate also contains toolchain notices
outside these library sections; it is not bundled here.

Hermes, fbjni, Fresco, CameraX, AndroidX and other transitive framework
components still require a complete dependency and binary inventory review.
The existing Pocket TTS 241-notice and 133-Cargo-declaration inventory is a
separate track and is unaffected by this subset.

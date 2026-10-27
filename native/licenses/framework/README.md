# Reviewed framework notices (bounded subset)

These twelve files are staged into every Android variant at `assets/framework/`.
The APK audits compare the packaged bytes with this directory and pin each file's
SHA-256 in `scripts/check-phone-qa.py`. This is a subset of the framework and
runtime attribution inventory, not a complete third-party notice bundle.

| Staged file | Exact source | SHA-256 of staged bytes |
| --- | --- | --- |
| `react-native-LICENSE.txt` | `node_modules/react-native/LICENSE`, npm `react-native@0.86.3` | `da6d3703ed11cbe42bd212c725957c98da23cbff1998c05fa4b3d976d1a58e93` |
| `react-native-safe-area-context-LICENSE.txt` | `node_modules/react-native-safe-area-context/LICENSE`, npm `react-native-safe-area-context@5.7.0` | `f5c7a4d527258e11fdfe3d84eb73cd14fe6d64224ef051e59f6ee21fe378800c` |
| `ndk-libcxx-NOTICE.txt` | NDK `NOTICE.toolchain` byte slice `[127769, 144473)` | `3f3d1da78246d5c2ffc879f8239d6e856b19ba813b8349cbe48f85bd018ee3af` |
| `ndk-libcxxabi-NOTICE.txt` | NDK `NOTICE.toolchain` byte slice `[144473, 161180)` | `b043b7c5fdaf8df24b2c3b50f7651e6acfc8bdd73cc152ec5d090b8a7e0ede33` |
| `double-conversion-LICENSE.txt` | [google/double-conversion `v1.1.6` `LICENSE`](https://github.com/google/double-conversion/blob/v1.1.6/LICENSE) (Git blob `933718a9ef9dae4cb4afa56e490211760d8a949f`) | `4af93c12062c58058378de2397dc1c92bbff9ddfb1d583a01c84127557ce97ca` |
| `glog-COPYING.txt` | [google/glog `v0.3.5` `COPYING`](https://github.com/google/glog/blob/v0.3.5/COPYING) (Git blob `38396b580b37b961f2fc72b254de161cbafdc060`) | `0fc497129c5c69ff6f22da6933c7e4aaef082fde8437fd57680c2780100772a4` |
| `fast_float-LICENSE-MIT.txt` | [fastfloat/fast_float `v8.0.0` `LICENSE-MIT`](https://github.com/fastfloat/fast_float/blob/v8.0.0/LICENSE-MIT) (Git blob `2fb2a37ad7fda521092c8b7b16911176c3f8a81b`) | `e562f3f974ced7e69dd1db77b820b36bcf8f30377f1aa105723fba449c53c4e6` |
| `folly-LICENSE.txt` | [facebook/folly `v2024.11.18.00` `LICENSE`](https://github.com/facebook/folly/blob/v2024.11.18.00/LICENSE) (Git blob `48bdb12823618e257fa32c8e387c4933947a899f`) | `2206c00af7013581ae0d7c8c8a0089f02fab621913daac019b32336bd4d1e1db` |
| `hermes-dtoa.c-NOTICE.txt` | [Hermes `hermes-v0.17.0` `external/dtoa/dtoa.c`](https://github.com/facebook/hermes/blob/hermes-v0.17.0/external/dtoa/dtoa.c), Git blob `153452e246a7b1ac8678ffa1c176780030f57f11`, initial bytes `[0, 861)` through the first `*/` | `45a0b19676f406b6aaeb9ebc04961ba711d38b13003a15b38ff7174b2568b0c4` |
| `hermes-g_fmt.c-NOTICE.txt` | [Hermes `hermes-v0.17.0` `external/dtoa/g_fmt.c`](https://github.com/facebook/hermes/blob/hermes-v0.17.0/external/dtoa/g_fmt.c), Git blob `53d177511c133720b11eb93581497769d4e9225f`, initial bytes `[0, 855)` through the first `*/` | `be5981b1b47d165581f00f9915679ecd19a4fa44ba259f5b60ee1fd1be1df31f` |
| `fresco-ijg-README.txt` | [libjpeg-turbo `2.1.5.1` `README.ijg`](https://github.com/libjpeg-turbo/libjpeg-turbo/blob/2.1.5.1/README.ijg), entire Git blob `9453c195010fb0ace756f8e46d74c7688bb1c857` | `4b7b9f8c03bb8d60270dfd12684e70ab21e4abfd27e73905cd1a7c4cae6f5cdb` |
| `fresco-ijg-ACKNOWLEDGMENT.txt` | Product documentation acknowledgment required by the `README.ijg` LEGAL ISSUES section, using its prescribed wording | `d903a69ab39425283b44e2ea355cbfaf79bcd738b69c2b1c3c700ce80f2d89f9` |

The Hermes source files have SHA-256 `9404ae4acb020e64cc24904e92d036da90126b02815147d0ab95ff5330ade2f8`
(`dtoa.c`, 75,957 bytes) and `0a1f2438e3ff2c270653a3fc51b0448570fd0ea2791590678ba54500fa349f16`
(`g_fmt.c`, 2,265 bytes). Each staged header is the exact initial byte slice of
its source file, with no added text or line-ending conversion. They differ in
the Lucent copyright years, and both contain the full David M. Gay permission
and warranty notice. [Hermes's dtoa CMake target](https://github.com/facebook/hermes/blob/hermes-v0.17.0/external/dtoa/CMakeLists.txt)
builds both C files. The React Native `0.86.3` Hermes version pin selects this
tag, and the dtoa target is linked into the Hermes runtime shipped as
`libhermesvm.so`.

Fresco `3.6.0` [links `fb_jpegturbo` into its native image transcoder](https://github.com/facebook/fresco/blob/v3.6.0/native-imagetranscoder/src/main/jni/native-imagetranscoder/Android.mk)
from `libjpeg-turbo-2.1.5.1`. The full 12,732-byte upstream `README.ijg`
contains the IJG copyright, warranty, distribution terms, and LEGAL ISSUES
condition for executable distribution. Its source SHA-256 matches the staged
asset. The separate acknowledgment is product documentation shipped with the
APK and reads exactly: “This software is based in part on the work of the
Independent JPEG Group.” The upstream [libjpeg-turbo `LICENSE.md`](https://github.com/libjpeg-turbo/libjpeg-turbo/blob/2.1.5.1/LICENSE.md)
(SHA-256 `60c756742db3ad1913304e8b13f0e86e22e51adb50cc0b3333c163f7e45ceec1`)
identifies the IJG terms for the libjpeg API and explains the executable
distribution acknowledgment. Its separate Modified BSD scope covers the
TurboJPEG API, which is not shown by this Fresco native image transcoder link.

The four new files are exact upstream Git blobs. The React Native `0.86.3`
version catalog pins double-conversion `1.1.6`, glog `0.3.5`, fast_float
`8.0.0` and Folly `2024.11.18.00`; its native CMake configuration includes
all four and links double-conversion, glog and `folly_runtime`. The glog
`COPYING` file includes the Google BSD conditions and Jouni Malinen notice.
The fast_float asset selects its upstream MIT license text. The complete
upstream Folly `LICENSE` is preserved, including its appended farmhash grant;
this does not establish farmhash inclusion in the APK.

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
aggregate at those offsets. The full aggregate also contains toolchain notices outside these library sections; it is not bundled here. The intermediate `ac9d010` ARM64 APK C++ runtime has SHA-256 `3e852764502d06e0cf1080d532ae7c608b7649d946a3a9d7e84e3d4b7d683214`; applying the selected NDK's `llvm-strip --strip-unneeded` to its distributed `libc++_shared.so` produces those exact bytes. This establishes the selected runtime's source-distribution link rather than inferring it from the project's NDK setting alone.

Hermes (`hermes-v0.17.0`) and Yoga (React Native source) share the Meta MIT
license text represented by `react-native-LICENSE.txt`, alongside the separate
Hermes dtoa notices above. Fresco `3.6.0` uses
the same Meta MIT text. fbjni `0.7.0` and SoLoader `0.12.1` use Apache 2.0,
represented by the application root `LICENSE`; these mappings do not replace
artifact-level verification. Boost `1.83.0` and fmt `12.1.0` are listed in the React Native native version catalog. Their pinned [Boost](https://github.com/boostorg/boost/blob/boost-1.83.0/LICENSE_1_0.txt) and [fmt](https://github.com/fmtlib/fmt/blob/12.1.0/LICENSE) texts explicitly exempt compiled object code from carrying their notices; their absence from an APK is not a demonstrated notice defect. Hermes,
Maven/AndroidX and other transitive framework components still require a
complete dependency and binary inventory review. This bounded notice set is
not legal clearance.
The existing Pocket TTS 241-notice and 133-Cargo-declaration inventory is a
separate track and is unaffected by this subset.

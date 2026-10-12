#!/usr/bin/env bash
# This script never accepts licenses or substitutes a different NDK.
set -euo pipefail
sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
fail() { echo "::error::$*" >&2; exit 1; }
[[ -n "$sdk" && -d "$sdk" ]] || fail 'Android SDK must be preinstalled on the runner.'
[[ -f "$sdk/platforms/android-36/android.jar" ]] || fail 'Missing preinstalled Android SDK platform 36; no license acceptance attempted.'
[[ -x "$sdk/build-tools/36.0.0/aapt2" ]] || fail 'Missing preinstalled build-tools 36.0.0; no license acceptance attempted.'
[[ -x "$sdk/cmake/3.31.5/bin/cmake" ]] || fail 'Missing preinstalled CMake 3.31.5; no license acceptance attempted.'
ndk="$sdk/ndk/27.1.12297006/source.properties"
if [[ ! -f "$ndk" ]]; then
  [[ -s "$sdk/licenses/android-sdk-license" ]] || fail 'Missing existing accepted Android SDK license. Review required; CI will not accept terms.'
  manager="$sdk/cmdline-tools/latest/bin/sdkmanager"
  [[ -x "$manager" ]] || fail 'Missing SDK manager for the pinned NDK.'
  # EOF explicitly refuses a new license prompt. Only preaccepted terms may work.
  timeout 180 "$manager" --install 'ndk;27.1.12297006' </dev/null || fail 'Pinned NDK installation failed; new terms/license prompts require human review (no acceptance attempted).'
fi
[[ -f "$ndk" ]] && grep -Eq '^Pkg.Revision[[:space:]]*=[[:space:]]*27\.1\.12297006[[:space:]]*$' "$ndk" || fail 'Exact NDK 27.1.12297006 was not installed. Check license or download errors.'
printf 'Verified SDK36/build-tools36.0.0/CMake3.31.5/NDK27.1.12297006\n'
if [[ -n "${RUNNER_TEMP:-}" ]]; then
  mkdir -p "$RUNNER_TEMP/sign-evidence"
  cp "$ndk" "$RUNNER_TEMP/sign-evidence/ndk-source.properties"
fi

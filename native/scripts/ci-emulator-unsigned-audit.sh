#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${RUNNER_TEMP:?}" "${ANDROID_HOME:?}"
report="$RUNNER_TEMP/sign-emulator-build"
mkdir -p "$report"
mapfile -t apps < <(find android/app/build/outputs/apk/emulatorQa -maxdepth 1 -type f -name '*.apk')
mapfile -t tests < <(find android/app/build/outputs/apk/androidTest/emulatorQa -maxdepth 1 -type f -name '*.apk')
[[ ${#apps[@]} == 1 && ${#tests[@]} == 1 ]]
for role in app test; do
  apk="${apps[0]}"; [[ "$role" == app ]] || apk="${tests[0]}"
  "$ANDROID_HOME/cmdline-tools/latest/bin/apkanalyzer" manifest print "$apk" > "$report/$role-manifest.xml"
  result=0
  "$ANDROID_HOME/build-tools/36.0.0/apksigner" verify --verbose --print-certs "$apk" > "$report/$role-unsigned.txt" 2>&1 || result=$?
  [[ "$result" == 1 ]] || { echo 'Expected unsigned verification failure; refuse signed or tool-error artifact.' >&2; exit 1; }
  sha256sum "$apk" >> "$report/unsigned-apk-sha256.txt"
done
python3 scripts/check-emulator-unsigned.py "$report/app-manifest.xml" "${apps[0]}" "$report/test-manifest.xml" "${tests[0]}"

#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${RUNNER_TEMP:?}" "${ANDROID_HOME:?}"
report="$RUNNER_TEMP/sign-evidence/phone-qa"
mkdir -p "$report"
mapfile -t apps < <(find android/app/build/outputs/apk/qa -maxdepth 1 -name '*.apk' -type f)
mapfile -t tests < <(find android/app/build/outputs/apk/androidTest/qa -maxdepth 1 -name '*.apk' -type f)
[[ ${#apps[@]} == 1 && ${#tests[@]} == 1 ]] || { echo 'Expected exactly one unsigned QA app APK and one unsigned QA instrumentation APK.' >&2; exit 1; }
: > "$report/unsigned-apk-sha256.txt"
for role in app test; do
  apk="${apps[0]}"
  manifest="$report/manifest.xml"
  [[ "$role" == app ]] || { apk="${tests[0]}"; manifest="$report/test-manifest.xml"; }
  "$ANDROID_HOME/cmdline-tools/latest/bin/apkanalyzer" manifest print "$apk" > "$manifest"
  result=0
  "$ANDROID_HOME/build-tools/36.0.0/apksigner" verify --verbose --print-certs "$apk" > "$report/$role-unsigned-verification.txt" 2>&1 || result=$?
  [[ "$result" == 1 ]] || { echo "Expected unsigned $role APK verification failure, not a signed APK or tool failure." >&2; exit 1; }
  sha256sum "$apk" >> "$report/unsigned-apk-sha256.txt"
done
python3 scripts/check-phone-qa.py "$report/manifest.xml" "${apps[0]}"
python3 scripts/check-phone-instrumentation.py "$report/test-manifest.xml" "${tests[0]}"
printf 'app_package=com.xmilo_at_your_side.sign_to_speak.qa\ntest_package=com.xmilo_at_your_side.sign_to_speak.qa.test\nabi=arm64-v8a\nsigning=unsigned\nphysical_device_test=NOT_RUN\n' > "$report/scope.txt"

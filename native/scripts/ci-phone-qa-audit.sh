#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${RUNNER_TEMP:?}" "${ANDROID_HOME:?}"
report="$RUNNER_TEMP/sign-evidence/phone-qa"
mkdir -p "$report"
mapfile -t files < <(find android/app/build/outputs/apk/qa -maxdepth 1 -name '*.apk' -type f)
[[ ${#files[@]} == 1 ]] || { echo 'Expected exactly one unsigned QA APK.' >&2; exit 1; }
apk="${files[0]}"
"$ANDROID_HOME/cmdline-tools/latest/bin/apkanalyzer" manifest print "$apk" > "$report/manifest.xml"
result=0
"$ANDROID_HOME/build-tools/36.0.0/apksigner" verify --verbose --print-certs "$apk" > "$report/unsigned-verification.txt" 2>&1 || result=$?
[[ "$result" == 1 ]] || { echo 'Expected an unsigned APK verification failure, not a signed APK or tool failure.' >&2; exit 1; }
python3 scripts/check-phone-qa.py "$report/manifest.xml" "$apk"
sha256sum "$apk" > "$report/unsigned-apk-sha256.txt"
printf 'package=com.xmilo_at_your_side.sign_to_speak.qa\nabi=arm64-v8a\nsigning=unsigned\nphysical_device_test=NOT_RUN\n' > "$report/scope.txt"

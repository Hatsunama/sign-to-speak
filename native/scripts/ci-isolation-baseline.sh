#!/usr/bin/env bash
# Counterfactual source exists only within this bounded, disposable CI checkout.
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${RUNNER_TEMP:?GitHub runner required}"
: "${GITHUB_OUTPUT:?GitHub step outputs required}"
report="$RUNNER_TEMP/sign-evidence/isolation-baseline"
mkdir -p "$report"
patch=native/tests/fixtures/pre-isolation.patch
printf '%s  %s\n' 'e5c3ef8acdd267e2a58b08852f80b6c0330eec6047d4bb8ab6e441eff6741fd4' "$patch" | sha256sum --check
files=(native/android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/{MainActivity,PrivateCameraView}.kt)
sha256sum "${files[@]}" > "$report/candidate-source.sha256"
# Baseline must start from the exact checked-out candidate, not additional edits.
git diff --exit-code HEAD -- "${files[@]}"
applied=false
restore() {
  result=$?
  trap - EXIT
  if [[ "$applied" == true ]]; then
    git restore --source=HEAD --worktree -- "${files[@]}" || exit 92
  fi
  sha256sum --check "$report/candidate-source.sha256" || exit 93
  git diff --exit-code HEAD -- "${files[@]}" || exit 94
  echo 'restored=true' >> "$GITHUB_OUTPUT"
  exit "$result"
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
git apply --ignore-space-change --check "$patch"
git apply --ignore-space-change "$patch"
applied=true
sha256sum "${files[@]}" > "$report/counterfactual-source.sha256"
status=0
(
  cd native/android
  ./gradlew --no-daemon --max-workers=2 --console=plain --stacktrace \
    -Dorg.gradle.jvmargs='-Xmx3g -XX:MaxMetaspaceSize=768m' \
    -Pandroid.builder.sdkDownload=false -PreactNativeArchitectures=x86_64 \
    :app:testDebugUnitTest --tests '*NativeBackPrivacyTest.cleanupFailure*'
) || status=$?
printf '%s\n' "$status" > "$report/gradle-exit.txt"
for directory in native/android/app/build/test-results/testDebugUnitTest native/android/app/build/reports/tests/testDebugUnitTest; do
  if [[ -d "$directory" ]]; then cp -R "$directory" "$report/$(basename "$(dirname "$directory")")"; fi
done
[[ "$status" -ne 0 ]] || { echo '::error::Counterfactual unexpectedly passed; no fail-before evidence.'; exit 95; }
python3 native/scripts/check-isolation-baseline.py \
  "$report/test-results/TEST-com.xmilo_at_your_side.sign_to_speak.NativeBackPrivacyTest.xml" \
  native/android/app/src/test/java/com/xmilo_at_your_side/sign_to_speak/NativeBackPrivacyTest.java

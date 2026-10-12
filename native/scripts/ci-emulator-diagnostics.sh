#!/usr/bin/env bash
# Read-only diagnostics. No permissions, group membership, udev or security changes.
set -euo pipefail
: "${RUNNER_TEMP:?}"
report="$RUNNER_TEMP/sign-emulator-build"
mkdir -p "$report"
{
  uname -a
  id
  groups
  if [[ -e /dev/kvm ]]; then stat /dev/kvm; else printf 'KVM_DEVICE_ABSENT\n'; fi
  if [[ -r /dev/kvm && -w /dev/kvm ]]; then printf 'KVM_EXISTING_ACCESS=usable\n'; else printf 'KVM_EXISTING_ACCESS=blocked\n'; fi
  sdk="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}"
  if [[ -x "$sdk/emulator/emulator" ]]; then
    result=0
    "$sdk/emulator/emulator" -accel-check || result=$?
    printf 'ACCEL_CHECK_EXIT=%s\n' "$result"
  else printf 'EMULATOR_NOT_PREINSTALLED\n'; fi
} > "$report/runner-kvm-diagnostics.txt" 2>&1
cat "$report/runner-kvm-diagnostics.txt"

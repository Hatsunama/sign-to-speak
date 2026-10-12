#!/usr/bin/env bash
set -euo pipefail
: "${JAVA_HOME:?}"
cd "$(dirname "$0")/.."
output=$(mktemp -d)
trap 'rm -rf "$output"' EXIT
"$JAVA_HOME/bin/javac" -J-Xmx128m -d "$output" \
  android/app/src/androidTestEmulatorQa/java/com/xmilo_at_your_side/sign_to_speak/NativeLibraryMapping.java \
  tests/java/NativeLibraryMappingTest.java
"$JAVA_HOME/bin/java" -Xmx64m -cp "$output" com.xmilo_at_your_side.sign_to_speak.NativeLibraryMappingTest

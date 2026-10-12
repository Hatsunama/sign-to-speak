#!/usr/bin/env sh
set -eu
: "${JAVA_HOME:?Set JAVA_HOME to an installed official JDK 17 or newer}"
cd "$(dirname "$0")/.."
OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT INT TERM
"$JAVA_HOME/bin/javac" -J-Xmx128m -d "$OUT" \
  android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/PreviewLease.java tests/java/PreviewLeaseTest.java
"$JAVA_HOME/bin/java" -Xmx64m -cp "$OUT" PreviewLeaseTest

#!/usr/bin/env sh
set -eu
: "${JAVA_HOME:?Set JAVA_HOME to an installed official JDK 17 or newer}"
cd "$(dirname "$0")/.."
OUT=$(mktemp -d "${JAVA_TEST_OUT_PARENT:-${TMPDIR:-/tmp}}/sign-java-tests.XXXXXX")
trap 'rm -rf "$OUT"' EXIT INT TERM
"$JAVA_HOME/bin/javac" -J-Xmx128m -d "$OUT" \
  android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/PreviewLease.java tests/java/PreviewLeaseTest.java \
  android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/SpeechSession.java tests/java/SpeechSessionTest.java \
  tests/java/SpeechTextContractTest.java \
  android/app/src/main/java/com/xmilo_at_your_side/sign_to_speak/PocketModelStore.java \
  tests/java/PocketModelStoreTest.java \
  android/app/src/androidTestQa/java/com/xmilo_at_your_side/sign_to_speak/RecognitionInputStage.java \
  tests/java/RecognitionInputStageTest.java
"$JAVA_HOME/bin/java" -Xmx64m -cp "$OUT" PreviewLeaseTest

"$JAVA_HOME/bin/java" -Xmx64m -cp "$OUT" SpeechSessionTest
"$JAVA_HOME/bin/java" -Xmx64m -cp "$OUT" SpeechTextContractTest
"$JAVA_HOME/bin/java" -Xmx128m -cp "$OUT" com.xmilo_at_your_side.sign_to_speak.PocketModelStoreTest "$OUT"
"$JAVA_HOME/bin/java" -Xmx64m -cp "$OUT" com.xmilo_at_your_side.sign_to_speak.RecognitionInputStageTest

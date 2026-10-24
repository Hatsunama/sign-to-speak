#!/usr/bin/env bash
# Hosted source-build trial only. No application build, signing, or publication.
set -euo pipefail

source_commit=50505c8312c7eacb1bd411cecdc4cc9fd08d53cb
aar_sha=38fa5886edfe8dd8c83292f4c2fb069488b6e8f3856c1c42042eaf13432d0b61
jni_sha=d80dfdfc6e034b7595137d035a279eddf70b028587510e99ede4a7e634c342a0
bazel_sha=fe7e799cbc9140f986b063e06800a3d4c790525075c877d00a7112669824acbf
target=//mediapipe/tasks/java/com/google/mediapipe/tasks/core:tasks_core
[[ "${RUNNER_OS:-}" == Linux && "${GITHUB_ACTIONS:-}" == true ]] || { echo 'Hosted Linux Actions runner required.' >&2; exit 1; }
evidence=${EVIDENCE_DIR:?EVIDENCE_DIR must name an artifact directory}
mkdir -p "$evidence"
trial=$(mktemp -d "${RUNNER_TEMP:-/tmp}/mediapipe-source-trial.XXXXXXXX")
trap 'rm -rf "$trial"' EXIT
fail() { echo "ERROR: $*" >&2; exit 1; }
check_sha() { printf '%s  %s\n' "$1" "$2" | sha256sum --check --status || fail "SHA-256 mismatch: $2"; }

sdk=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}
[[ -n "$sdk" && -f "$sdk/platforms/android-36/android.jar" ]] || fail 'Runner needs an existing Android SDK platform 36; no licenses are accepted.'
[[ -x "$sdk/build-tools/35.0.0/aapt" ]] || fail 'Runner needs existing Android build-tools 35.0.0.'
[[ -n "${JAVA_HOME_21_X64:-}" && -x "$JAVA_HOME_21_X64/bin/javap" ]] || fail 'Runner needs existing JDK 21.'
export JAVA_HOME=$JAVA_HOME_21_X64
export PATH="$JAVA_HOME/bin:$PATH"
export ANDROID_HOME=$sdk
ndk_revision=
for candidate in 28.1.13356709 27.1.12297006; do
  if [[ -f "$sdk/ndk/$candidate/source.properties" ]] &&
     [[ "$(awk -F= '/^Pkg.Revision[[:space:]]*=/{gsub(/[[:space:]]/, "", $2); print $2}' "$sdk/ndk/$candidate/source.properties")" == "$candidate" ]]; then
    ndk_revision=$candidate
    break
  fi
done
if [[ -z "$ndk_revision" ]]; then
  [[ -s "$sdk/licenses/android-sdk-license" ]] || fail 'Missing preaccepted Android SDK license.'
  manager="$sdk/cmdline-tools/latest/bin/sdkmanager"
  [[ -x "$manager" ]] || fail 'Missing SDK manager for pinned NDK r28b.'
  # Existing accepted terms only; EOF refuses any new license prompt.
  timeout 180 "$manager" --install 'ndk;28.1.13356709' </dev/null || fail 'Pinned NDK install failed; no new license accepted.'
  ndk_revision=28.1.13356709
  [[ -f "$sdk/ndk/$ndk_revision/source.properties" ]] || fail 'Pinned NDK r28b missing after install.'
fi
export ANDROID_NDK_HOME="$sdk/ndk/$ndk_revision"
"$JAVA_HOME/bin/java" -version 2> "$evidence/java-version.txt"
printf 'sdk=%s\nndk_revision=%s\n' "$sdk" "$ndk_revision" > "$evidence/android-toolchain.txt"

curl -fL --retry 2 --connect-timeout 15 --max-time 120 \
  'https://releases.bazel.build/7.7.0/release/bazel-7.7.0-linux-x86_64' -o "$trial/bazel"
check_sha "$bazel_sha" "$trial/bazel"
chmod +x "$trial/bazel"
"$trial/bazel" --version | tee "$evidence/bazel-version.txt"

curl -fL --retry 2 --connect-timeout 15 --max-time 120 \
  'https://dl.google.com/dl/android/maven2/com/google/mediapipe/tasks-core/1.1.1/tasks-core-1.1.1.aar' \
  -o "$trial/published-core.aar"
check_sha "$aar_sha" "$trial/published-core.aar"
mkdir -p "$trial/source"
git -C "$trial/source" init -q
git -C "$trial/source" remote add origin https://github.com/google-ai-edge/mediapipe.git
git -C "$trial/source" -c protocol.version=2 fetch --no-tags --depth=1 origin "$source_commit"
git -C "$trial/source" checkout -q --detach FETCH_HEAD
[[ "$(git -C "$trial/source" rev-parse HEAD)" == "$source_commit" ]] || fail 'Upstream source commit changed.'
[[ "$(cat "$trial/source/.bazelversion")" == 7.7.0 ]] || fail 'Upstream Bazel version changed.'

# The upstream source snapshot lacks this release metadata target. Restore the
# byte-identical published NOTICE and only the BUILD declaration it expects.
release_dir="$trial/source/mediapipe/tasks/internal/release/android"
[[ ! -e "$release_dir" ]] || fail 'Upstream release metadata target appeared; inspect before continuing.'
mkdir -p "$release_dir"
unzip -p "$trial/published-core.aar" META-INF/NOTICE > "$release_dir/NOTICE"
[[ -s "$release_dir/NOTICE" ]] || fail 'Published NOTICE absent.'
printf 'exports_files(["NOTICE"])\n' > "$release_dir/BUILD"

# This is the official prebuilt-JNI route in mediapipe_tasks_aar.bzl, not a
# replacement of any compiled Java classes or of the final AAR.
jni_dir="$trial/source/mediapipe/tasks/java/com/google/mediapipe/tasks/core/arm64-v8a"
mkdir -p "$jni_dir"
unzip -p "$trial/published-core.aar" jni/arm64-v8a/libmediapipe_tasks_jni.so > "$jni_dir/libmediapipe_tasks_jni.so"
check_sha "$jni_sha" "$jni_dir/libmediapipe_tasks_jni.so"
printf '%s\n' "$source_commit" > "$evidence/upstream-source-commit.txt"
sha256sum "$trial/bazel" "$trial/published-core.aar" "$jni_dir/libmediapipe_tasks_jni.so" "$release_dir/NOTICE" > "$evidence/input-hashes.txt"
cp "$release_dir/NOTICE" "$evidence/NOTICE"

cd "$trial/source"
[[ ! -e .bazelversion || "$(cat .bazelversion)" == 7.7.0 ]] || fail 'Bazel pin changed.'
# The upstream setup script downloads SDK/NDK and can accept licenses. Existing
# ANDROID_HOME and ANDROID_NDK_HOME let Bazel discover the installed toolchain.
"$trial/bazel" --batch --host_jvm_args=-Xmx1g --output_user_root="$trial/bazel-cache" \
  build --config=android_arm64 --define=ENABLE_TASKS_USAGE_LOGGING=0 --jobs=2 --local_ram_resources=4096 \
  --java_runtime_version=local_jdk --tool_java_runtime_version=local_jdk \
  "$target"

aar=bazel-bin/mediapipe/tasks/java/com/google/mediapipe/tasks/core/tasks_core.aar
[[ -s "$aar" ]] || fail 'Official Bazel target did not emit its AAR.'
export SOURCE_AAR="$PWD/$aar" EXPECTED_JNI_SHA="$jni_sha"
python3 - <<'PY'
import hashlib, io, os, zipfile

with zipfile.ZipFile(os.environ['SOURCE_AAR']) as aar:
    classes = aar.read('classes.jar')
    native = aar.read('jni/arm64-v8a/libmediapipe_tasks_jni.so')
    assert hashlib.sha256(native).hexdigest() == os.environ['EXPECTED_JNI_SHA'], 'JNI library differs from pinned publication'
    assert aar.read('META-INF/NOTICE'), 'NOTICE missing from source AAR'
    assert not [name for name in aar.namelist() if name.startswith('jni/') and not name.startswith('jni/arm64-v8a/')], 'unexpected ABI'
with zipfile.ZipFile(io.BytesIO(classes)) as jar:
    names = set(jar.namelist())
    factory = 'com/google/mediapipe/tasks/core/logging/TasksStatsLoggerFactory.class'
    dummy = 'com/google/mediapipe/tasks/core/logging/TasksStatsDummyLogger.class'
    assert factory in names and dummy in names, 'source logger factory/dummy absent'
    assert not [n for n in names if any(marker in n for marker in (
        'RemoteLoggingClient', 'ProtoLogger', 'MediaPipeLoggingProto',
        'MediaPipeLoggingEnumsProto', 'com/google/android/datatransport/',
    ))], 'usage logging classes present'
PY
unzip -p "$aar" classes.jar > "$trial/classes.jar"
"$JAVA_HOME/bin/javap" -c -p -classpath "$trial/classes.jar" \
  com.google.mediapipe.tasks.core.logging.TasksStatsLoggerFactory \
  > "$evidence/logger-factory-javap.txt"
grep -q 'TasksStatsDummyLogger.create' "$evidence/logger-factory-javap.txt" || fail 'Factory does not delegate to dummy logger.'
if grep -Eq 'ProtoLogger|RemoteLoggingClient' "$evidence/logger-factory-javap.txt"; then
  fail 'Factory references usage logging.'
fi

# Query is evidence only; a query timeout is recorded without invalidating a
# successful, independently audited source AAR.
timeout 90 "$trial/bazel" --batch --host_jvm_args=-Xmx1g --output_user_root="$trial/bazel-cache" \
  query "deps($target)" --output=label > "$evidence/dependency-labels.txt" \
  2> "$evidence/dependency-query-stderr.txt" || echo 'dependency query incomplete' > "$evidence/dependency-query-status.txt"
cp "$aar" "$evidence/tasks-core-1.1.1-source-offline-arm64.aar"
sha256sum "$evidence/tasks-core-1.1.1-source-offline-arm64.aar" > "$evidence/output-sha256.txt"
printf 'source_commit=%s\nbazel_sha256=%s\npublished_aar_sha256=%s\nreused_jni_sha256=%s\ntarget=%s\nusage_logging_define=0\n' \
  "$source_commit" "$bazel_sha" "$aar_sha" "$jni_sha" "$target" > "$evidence/receipt.txt"
echo 'Pinned Tasks Core source AAR built and audited.'

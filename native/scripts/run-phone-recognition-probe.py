#!/usr/bin/env python3
"""Bounded, explicit phone research probe. No installation or permission changes.

Diagnostic: --mode diagnostic --manifest a-small-synthetic-manifest.json
Research: --mode research --manifest reviewed-manifest.json --asset-dir PRIVATE_DIR
Or pass --model-path, --holistic-path, --labels-path and --clip-dir.
Both require --serial, --receipt, --expected-source (exact receipt source), and
--output (a fresh directory). All paths must be below C:/sign_to_speak.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import sys
from datetime import datetime, timezone

APP = 'com.xmilo_at_your_side.sign_to_speak.qa'
TEST = APP + '.test'
CLASS = 'com.xmilo_at_your_side.sign_to_speak.PhoneRecognitionProbeTest'
DEVICE_PREFIX = '/data/local/tmp/sign-recognition-'
RESULT = '/sdcard/Android/data/' + APP + '/files/recognition-results/result.json'
HOME = ('com.sec.android.app.launcher/', 'com.android.launcher3/')
SHA = re.compile(r'[0-9a-f]{64}\Z')
TOKEN = re.compile(r'[0-9a-f]{32}\Z')
SERIAL = re.compile(r'[A-Za-z0-9._:-]{1,80}\Z')
CLIP = re.compile(r'[A-Za-z0-9_-]{1,40}\Z')


class ProbeError(RuntimeError):
    pass


def fail(message):
    raise ProbeError(message)


def sha_file(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(65536), b''):
            h.update(block)
    return h.hexdigest()


def read_json(path, max_bytes=16384):
    if not path.is_file() or path.is_symlink() or not 1 <= path.stat().st_size <= max_bytes:
        fail('JSON input missing or outside size limit')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeError, ValueError) as exc:
        raise ProbeError('Invalid JSON input') from exc


def default_adb(args, timeout):
    try:
        completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                                   encoding='utf-8', errors='replace')
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError('ADB unavailable or timed out') from exc
    return completed.returncode, completed.stdout, completed.stderr


def run_probe(*, serial, mode, manifest, receipt, expected_source, output,
              asset_dir=None, model_path=None, holistic_path=None, labels_path=None,
              clip_dir=None, token=None, adb=default_adb, adb_path='adb',
              workspace_root='C:/sign_to_speak'):
    if not SERIAL.fullmatch(serial) or mode not in ('diagnostic', 'research'):
        fail('Invalid serial or mode')
    if token is None:
        token = secrets.token_hex(16)
    if not TOKEN.fullmatch(token):
        fail('Transfer token must be 32 lowercase hex characters')
    if not re.fullmatch(r'[0-9a-f]{40}', expected_source):
        fail('Expected source must be an exact commit SHA')
    workspace_root = Path(workspace_root).resolve(strict=False)
    def checked(path):
        path = Path(path).resolve(strict=False)
        if not path.is_relative_to(workspace_root):
            fail('Input or evidence outside approved workspace')
        return path
    manifest, receipt, output = map(checked, (manifest, receipt, output))
    if output.exists():
        fail('Evidence directory must be fresh')
    if mode == 'research':
        explicit = (model_path, holistic_path, labels_path, clip_dir)
        if asset_dir is not None and any(value is not None for value in explicit):
            fail('Choose one research asset location scheme')
        if asset_dir is None and not all(value is not None for value in explicit):
            fail('Research requires an asset directory or all explicit asset paths')
        if asset_dir is not None:
            asset_dir = checked(asset_dir)
            if not asset_dir.is_dir() or asset_dir.is_symlink():
                fail('Invalid asset directory')
            model_path, holistic_path, labels_path, clip_dir = (
                asset_dir / 'model.tflite', asset_dir / 'holistic.task',
                asset_dir / 'labels.json', asset_dir)
        clip_dir = checked(clip_dir)
        if not clip_dir.is_dir() or clip_dir.is_symlink():
            fail('Invalid clip directory')
    receipt_data = read_json(receipt)
    sources = [receipt_data[key] for key in ('source_sha', 'source') if key in receipt_data]
    if not sources or any(source != expected_source for source in sources):
        fail('Receipt source differs from explicitly expected source')
    artifacts = receipt_data.get('artifacts')
    if not isinstance(artifacts, list) or len(artifacts) != 2 or {a.get('role') for a in artifacts} != {'app', 'test'}:
        fail('Receipt must contain exact signed QA pair')
    for artifact in artifacts:
        if not SHA.fullmatch(artifact.get('signed_sha256', '')):
            fail('Invalid signed artifact hash')
    manifest_data = read_json(manifest)
    payloads = [('manifest.json', manifest, sha_file(manifest), 16384)]
    staged_manifest_bytes = None
    if mode == 'research':
        if set(manifest_data) != {'schema_version', 'model_sha256', 'holistic_sha256', 'labels_sha256', 'clips'} or manifest_data['schema_version'] != 1:
            fail('Research manifest schema invalid')
        for name, field, limit, source in [('model.tflite', 'model_sha256', 36870396, model_path),
                                           ('holistic.task', 'holistic_sha256', 30 * 1024 * 1024, holistic_path),
                                           ('labels.json', 'labels_sha256', 32768, labels_path)]:
            expected = manifest_data[field]
            if not isinstance(expected, str) or not SHA.fullmatch(expected):
                fail('Research asset pin invalid')
            payloads.append((name, Path(source), expected, limit))
        clips = manifest_data['clips']
        if not isinstance(clips, list) or not 1 <= len(clips) <= 4:
            fail('Research requires one to four clips')
        seen = set()
        for clip in clips:
            if not isinstance(clip, dict) or set(clip) != {'clip_id', 'path', 'expected', 'sha256'}:
                fail('Clip fields invalid')
            cid, source_name, expected = clip['clip_id'], clip['path'], clip['sha256']
            if not isinstance(cid, str) or not CLIP.fullmatch(cid) or cid in seen:
                fail('Invalid clip identity')
            seen.add(cid)
            if not isinstance(source_name, str) or not CLIP.fullmatch(Path(source_name).stem) or Path(source_name).suffix != '.mp4' or '/' in source_name or '\\' in source_name:
                fail('Clip source must be a direct MP4 child')
            if not isinstance(expected, str) or not SHA.fullmatch(expected):
                fail('Clip SHA invalid')
            payloads.append((cid + '.mp4', clip_dir / source_name, expected, 2 * 1024 * 1024))
        staged_manifest = dict(manifest_data)
        staged_manifest['clips'] = [dict(clip, path=clip['clip_id'] + '.mp4') for clip in clips]
        staged_manifest_bytes = json.dumps(staged_manifest, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        if not 1 <= len(staged_manifest_bytes) <= 16384:
            fail('Staged research manifest outside size limit')
        payloads[0] = ('manifest.json', None, hashlib.sha256(staged_manifest_bytes).hexdigest(), 16384)
    for _, path, digest, limit in payloads:
        if path is None:
            continue
        path = checked(path)
        if not path.is_file() or path.is_symlink() or not 1 <= path.stat().st_size <= limit or sha_file(path) != digest:
            fail('Local input missing, oversized or hash mismatch')
    device_dir = DEVICE_PREFIX + token
    report = {'serial': serial, 'mode': mode, 'receipt_source': expected_source,
              'manifest_sha256': payloads[0][2], 'transfer_id': token,
              'started_utc': datetime.now(timezone.utc).isoformat(), 'passed': False,
              'testcase_passed': False, 'cleanup_pending': False, 'test_count': 0}
    owned = []
    created = False
    instrument_started = False

    def phone(*parts, timeout=20):
        code, stdout, _ = adb([adb_path, '-s', serial, *parts], timeout)
        if code != 0:
            fail('ADB command failed: ' + parts[0])
        return stdout

    def foreground():
        raw = phone('shell', 'dumpsys', 'activity', 'activities')
        line = next((line for line in raw.splitlines() if 'topResumedActivity=' in line), '')
        match = re.search(r'([A-Za-z0-9_.]+/[^\s}]+)', line)
        if not match:
            fail('Cannot establish foreground activity')
        return match.group(1)

    def ready():
        if not foreground().startswith(HOME):
            fail('Phone must be free at Home')
        processes = phone('shell', 'dumpsys', 'activity', 'processes')
        if 'ACTIVITY MANAGER RUNNING PROCESSES' not in processes or 'Unknown command' in processes or 'Bad activity command' in processes:
            fail('Instrumentation readiness cannot be established')
        if 'Active instrumentation:' in processes or 'ActiveInstrumentation{' in processes:
            fail('Another instrumentation is active')
        policy = phone('shell', 'dumpsys', 'window', 'policy')
        if 'inputRestricted=true' in policy or 'showing=true' in policy:
            fail('Phone is locked')

    def safe_to_restore():
        fg = foreground()
        if not (fg.startswith(HOME) or fg.startswith(APP + '/')):
            return False
        processes = phone('shell', 'dumpsys', 'activity', 'processes')
        return ('ACTIVITY MANAGER RUNNING PROCESSES' in processes
                and 'Unknown command' not in processes and 'Bad activity command' not in processes
                and 'Active instrumentation:' not in processes and 'ActiveInstrumentation{' not in processes)

    try:
        ready()
        for artifact in artifacts:
            package = APP if artifact['role'] == 'app' else TEST
            installed_path = phone('shell', 'pm', 'path', package).strip()
            if not re.fullmatch(r'package:/[^\s]+/base\.apk', installed_path):
                fail('Expected QA artifact not installed')
            actual = phone('shell', 'sha256sum', installed_path[len('package:'):]).split()[0]
            if actual != artifact['signed_sha256']:
                fail('Installed QA bytes differ from signed receipt')
        ready()
        output.mkdir(parents=True, exist_ok=False)
        if staged_manifest_bytes is not None:
            staged_path = output / 'staged-manifest.json'
            staged_path.write_bytes(staged_manifest_bytes)
            payloads[0] = ('manifest.json', staged_path, payloads[0][2], 16384)
        # mkdir without -p is a fresh-path check; an existing token never gets overwritten.
        phone('shell', 'mkdir', device_dir)
        created = True
        for name, path, digest, _ in payloads:
            ready()
            target = device_dir + '/' + name
            owned.append(target)
            phone('push', str(path), target, timeout=90)
            if phone('shell', 'sha256sum', target).split()[0] != digest:
                fail('Staged input hash mismatch')
        ready()
        method = 'stagedInputHandoffIsReadable' if mode == 'diagnostic' else 'recordedVideoToHolisticToLocalModel'
        optin = 'recognitionInputProbe' if mode == 'diagnostic' else 'recognitionProbe'
        command = ['shell', 'am', 'instrument', '-w', '-e', optin, 'true',
                   '-e', 'recognitionTransferId', token, '-e', 'recognitionManifestSha256', payloads[0][2],
                   '-e', 'timeout_msec', '180000', '-e', 'class', CLASS + '#' + method,
                   TEST + '/androidx.test.runner.AndroidJUnitRunner']
        instrument_started = True
        code, stdout, _ = adb([adb_path, '-s', serial, *command], 210)
        (output / 'instrumentation.txt').write_text(stdout, encoding='utf-8')
        if code != 0 or not re.search(r'(?m)^OK \(1 test\)\s*$', stdout) or re.search(r'FAILURES!!!|INSTRUMENTATION_FAILED|Process crashed', stdout):
            fail('Instrumentation did not pass exactly one test')
        report['test_count'] = 1
        if mode == 'research':
            phone('pull', RESULT, str(output / 'result.json'), timeout=30)
            result = read_json(output / 'result.json', 65536)
            result_clips = result.get('clips')
            if (result.get('schema_version') != 1 or result.get('manifest_sha256') != payloads[0][2]
                    or not isinstance(result_clips, list) or len(result_clips) != len(manifest_data['clips'])
                    or {c.get('clip_id') for c in result_clips if isinstance(c, dict)} != seen):
                fail('Research result does not match current manifest or clip count')
            report['clip_count'] = len(result_clips)
        report['testcase_passed'] = True
        return report
    finally:
        if created:
            try:
                fg = foreground()
                if fg.startswith(HOME) or fg.startswith(APP + '/'):
                    if instrument_started:
                        try:
                            cleanup_command = ['shell', 'am', 'instrument', '-w', '-e',
                                               'recognitionCleanupProbe', 'true', '-e',
                                               'recognitionTransferId', token, '-e', 'timeout_msec', '30000',
                                               '-e', 'class', CLASS + '#cleanupStagedInputAfterInterruptedProbe',
                                               TEST + '/androidx.test.runner.AndroidJUnitRunner']
                            code, stdout, _ = adb([adb_path, '-s', serial, *cleanup_command], 45)
                            if output.exists():
                                (output / 'cleanup-instrumentation.txt').write_text(stdout, encoding='utf-8')
                            report['app_private_cleanup_passed'] = (
                                code == 0 and re.search(r'(?m)^OK \(1 test\)\s*$', stdout) is not None
                                and not re.search(r'FAILURES!!!|INSTRUMENTATION_FAILED|Process crashed', stdout))
                        except Exception as exc:
                            report['app_private_cleanup_passed'] = False
                            if output.exists():
                                (output / 'cleanup-instrumentation.txt').write_text(
                                    'Cleanup invocation failed: ' + type(exc).__name__, encoding='utf-8')
                        if not report['app_private_cleanup_passed']:
                            report['cleanup_pending'] = True
                    if safe_to_restore():
                        phone('shell', 'am', 'force-stop', APP)
                        for path in owned:
                            phone('shell', 'rm', '-f', path)
                        phone('shell', 'rmdir', device_dir)
                        if safe_to_restore():
                            phone('shell', 'input', 'keyevent', 'KEYCODE_HOME')
                        else:
                            report['cleanup_pending'] = True
                    else:
                        report['cleanup_pending'] = True
                else:
                    report['cleanup_pending'] = True
            except Exception:
                report['cleanup_pending'] = True
        report['passed'] = bool(report['testcase_passed'] and not report['cleanup_pending'])
        if output.exists():
            report['ended_utc'] = datetime.now(timezone.utc).isoformat()
            (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('serial', 'mode', 'manifest', 'receipt', 'expected-source', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--asset-dir')
    parser.add_argument('--model-path')
    parser.add_argument('--holistic-path')
    parser.add_argument('--labels-path')
    parser.add_argument('--clip-dir')
    parser.add_argument('--adb-path', default='adb')
    args = parser.parse_args()
    try:
        result = run_probe(serial=args.serial, mode=args.mode, manifest=args.manifest,
                           receipt=args.receipt, expected_source=args.expected_source,
                           output=args.output, asset_dir=args.asset_dir, model_path=args.model_path,
                           holistic_path=args.holistic_path, labels_path=args.labels_path,
                           clip_dir=args.clip_dir, adb_path=args.adb_path)
        print(json.dumps(result, indent=2))
        if not result['passed']:
            sys.exit(1)
    except ProbeError as exc:
        print('Probe failed: ' + str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

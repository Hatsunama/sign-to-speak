#!/usr/bin/env python3
"""Staged native component-only probe. No APK, audio output, or user-device access."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import struct
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'kvm-boot-probe'))
import probe as base

PARENT = '6cd8e58386d200507279ed12676ad6856d047bef'
BRANCH = 'refs/heads/probe/sign-flite-component-20261013'
SERIAL = 'emulator-5556'
ADB_PORT = '5038'
BINARY_RELATIVE = 'tools/flite-component-probe/artifacts/private_tts_cli_probe'
SOURCE_RELATIVE = 'tools/flite-component-probe/artifacts/reviewed-source-bundle.tar.gz'
NOTICES_RELATIVE = 'tools/flite-component-probe/artifacts/THIRD_PARTY_NOTICES.txt'
EVIDENCE_RELATIVE = 'tools/flite-component-probe/artifacts/reviewed-evidence.json'
# Only replace this after root reviews final binary, source, notices and bound tests.
# Environment variables and draft artifact metadata cannot enable execution.
REVIEWED_IDENTITY = {'binary_sha256': 'ac2dc3b2555078ea4b59f6a82beda64c64983f2dfc0a9a9986f79ee988479cd1', 'binary_size': 4821464, 'source_sha256': '63994cf655ebb364017227a6df766649ff3fc63c488e9078a042f37f54710c9b', 'source_size': 4631368, 'notices_sha256': 'e264c588d653c0e8e5e8b0e76700c5751189961278fd70c74163c08f3f71e025', 'notices_size': 2593909, 'evidence_sha256': '7e13481e04a32f3d5e33ad446f71529f0fc6a7323bd76201ea0f216921b25fa8', 'evidence_size': 38508}
# Exact Linux x86_64 host reference; Android parity must be established, never assumed.
EXPECTED_PCM_SAMPLES = 43040
EXPECTED_PCM_FNV1A64_LE = '0fbbaad2e72e7c99'
COMPONENT_TIMEOUT = 60
COMPONENT_LOG_LIMIT = 8192
ALLOWED_CELLS = ('api26', 'api35-16k')


def policy(env):
    expected = {'PROBE_REPOSITORY_PRIVATE': 'false', 'PROBE_REPOSITORY_VISIBILITY': 'public',
                'GITHUB_REPOSITORY': 'Hatsunama/sign-to-speak', 'GITHUB_ACTIONS': 'true',
                'RUNNER_ENVIRONMENT': 'github-hosted', 'GITHUB_EVENT_NAME': 'push',
                'GITHUB_REF': BRANCH, 'ImageOS': 'ubuntu24', 'RUNNER_OS': 'Linux',
                'RUNNER_ARCH': 'X64'}
    if any(env.get(k) != v for k, v in expected.items()):
        raise base.Blocked('COMPONENT_POLICY_MISMATCH')
    if not re.fullmatch(r'[a-f0-9]{40}', env.get('PROBE_COMMIT', '')):
        raise base.Blocked('COMPONENT_COMMIT_MISSING')
    if env.get('PROBE_CELL') not in ALLOWED_CELLS:
        raise base.Blocked('COMPONENT_CELL_NOT_APPROVED')


def verify_elf(data):
    if len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01':
        raise base.Blocked('COMPONENT_NOT_ELF64_LE')
    kind, machine = struct.unpack_from('<HH', data, 16)
    entry, phoff = struct.unpack_from('<QQ', data, 24)
    phsize, phcount = struct.unpack_from('<HH', data, 54)
    if kind != 3 or machine != 62 or not entry or phsize != 56 or not 1 <= phcount <= 64:
        raise base.Blocked('COMPONENT_NOT_ANDROID_X86_64_PIE')
    if phoff < 64 or phoff + phcount * phsize > len(data):
        raise base.Blocked('COMPONENT_INVALID_ELF_HEADERS')
    loads, interpreter = 0, None
    for index in range(phcount):
        typ, flags, offset, addr, _, size, memsize, align = struct.unpack_from('<IIQQQQQQ', data, phoff + index * phsize)
        if offset + size > len(data) or size > memsize:
            raise base.Blocked('COMPONENT_INVALID_ELF_SEGMENT')
        if typ == 1:
            loads += 1
            if align < 16384 or align & (align - 1) or offset % 16384 != addr % 16384 or flags & 3 == 3:
                raise base.Blocked('COMPONENT_UNSAFE_OR_UNALIGNED_LOAD')
        if typ == 3:
            if interpreter is not None:
                raise base.Blocked('COMPONENT_MULTIPLE_INTERPRETERS')
            interpreter = data[offset:offset + size]
    if not loads or interpreter != b'/system/bin/linker64\0':
        raise base.Blocked('COMPONENT_ANDROID_INTERPRETER_MISSING')


def verify_artifacts(root, identity=None):
    identity = REVIEWED_IDENTITY if identity is None else identity
    expected_keys = {'binary_sha256', 'binary_size', 'source_sha256', 'source_size',
                     'notices_sha256', 'notices_size', 'evidence_sha256', 'evidence_size'}
    if not isinstance(identity, dict) or set(identity) != expected_keys:
        raise base.Blocked('FINAL_REVIEWED_ARTIFACT_IDENTITY_REQUIRED')
    for key, value in identity.items():
        if key.endswith('_sha256') and not (isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value)):
            raise base.Blocked('FINAL_IDENTITY_INVALID')
        if key.endswith('_size') and (type(value) is not int or not 0 < value <= 32 * 1024 * 1024):
            raise base.Blocked('FINAL_IDENTITY_INVALID')
    root = root.resolve(strict=True)
    binary = None
    for role, relative in [('binary', BINARY_RELATIVE), ('source', SOURCE_RELATIVE), ('notices', NOTICES_RELATIVE), ('evidence', EVIDENCE_RELATIVE)]:
        path = root / relative
        try:
            mode = path.lstat().st_mode
            if not stat.S_ISREG(mode) or path.resolve(strict=True) != path or not path.is_relative_to(root):
                raise base.Blocked('ARTIFACT_PATH_NOT_OWNED_REGULAR_FILE')
            if path.stat().st_size != identity[role + '_size']:
                raise base.Blocked('REVIEWED_ARTIFACT_HASH_OR_SIZE_MISMATCH:' + role)
            data = path.read_bytes()
        except OSError as e:
            raise base.Blocked('REVIEWED_ARTIFACT_MISSING:' + role) from e
        if len(data) != identity[role + '_size'] or hashlib.sha256(data).hexdigest() != identity[role + '_sha256']:
            raise base.Blocked('REVIEWED_ARTIFACT_HASH_OR_SIZE_MISMATCH:' + role)
        if role == 'binary':
            verify_elf(data)
            binary = path
    return binary, dict(identity)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise base.Blocked('DUPLICATE_COMPONENT_JSON_KEY')
        result[key] = value
    return result


def validate_metrics(text, expected_page):
    if len(text.encode('utf-8')) > COMPONENT_LOG_LIMIT:
        raise base.Blocked('COMPONENT_METRICS_TOO_LARGE')
    try:
        data = json.loads(text, object_pairs_hook=unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, TypeError) as e:
        raise base.Blocked('COMPONENT_METRICS_NOT_SINGLE_FINITE_JSON') from e
    exact = {'status': 'ok', 'sample_rate': 16000, 'channels': 1, 'encoding': 'signed_pcm16',
             'owner_cleared': True, 'empty_rejected': True, 'pre_canceled_rejected': True,
             'page_size': expected_page}
    if not isinstance(data, dict) or set(data) != set(exact) | {'samples', 'pcm_fnv1a64_le', 'latency_ms'}:
        raise base.Blocked('COMPONENT_METRICS_SCHEMA_MISMATCH')
    for key, expected in exact.items():
        if type(data[key]) is not type(expected) or data[key] != expected:
            raise base.Blocked('COMPONENT_METRIC_MISMATCH:' + key)
    if type(data['samples']) is not int or not 1 <= data['samples'] <= 480000:
        raise base.Blocked('COMPONENT_SAMPLE_COUNT_INVALID')
    if not isinstance(data['pcm_fnv1a64_le'], str) or not re.fullmatch(r'[0-9a-f]{16}', data['pcm_fnv1a64_le']):
        raise base.Blocked('COMPONENT_PCM_CHECKSUM_INVALID')
    if data['samples'] != EXPECTED_PCM_SAMPLES or data['pcm_fnv1a64_le'] != EXPECTED_PCM_FNV1A64_LE:
        raise base.Blocked('COMPONENT_FIXED_PHRASE_REFERENCE_MISMATCH')
    latency = data['latency_ms']
    if type(latency) not in (int, float) or not math.isfinite(latency) or not 0 <= latency < COMPONENT_TIMEOUT * 1000:
        raise base.Blocked('COMPONENT_LATENCY_INVALID')
    return data


class ComponentProbe(base.Probe):
    def __init__(self, evidence, temp_root, binary, identity):
        super().__init__(evidence, temp_root)
        self.binary = binary
        self.identity = identity
        self.result.update(scope='native-component-only; no application/audio acceptance',
                           environment_status='not-run', component_status='not-run')

    def command(self, args, name, seconds=20):
        end = time.monotonic() + self.remaining(seconds)
        child, log = self.spawn(args, name)
        try:
            while child.poll() is None:
                if time.monotonic() >= end or log.stat().st_size > COMPONENT_LOG_LIMIT:
                    raise base.Blocked('COMPONENT_COMMAND_TIMEOUT_OR_LOG_LIMIT:' + name)
                time.sleep(.1)
            code = child.returncode
        finally:
            self.stop(child)
        if log.stat().st_size > COMPONENT_LOG_LIMIT:
            raise base.Blocked('COMPONENT_COMMAND_OUTPUT_LIMIT:' + name)
        data = log.read_bytes()
        self.write(name + '.status.json', json.dumps({'exit': code, 'bytes': len(data)}))
        if code or len(data) > COMPONENT_LOG_LIMIT:
            raise base.Blocked('COMPONENT_COMMAND_FAILED:' + name + ':exit=' + str(code))
        try:
            return data.decode('utf-8').strip()
        except UnicodeDecodeError as e:
            raise base.Blocked('COMPONENT_NON_TEXT_OUTPUT') from e

    def execute(self):
        super().execute()
        self.result.update(environment_status='verified', component_status='running', status='component-running')
        try:
            self.execute_component()
        except Exception:
            self.result.update(component_status='failed', status='native-component-failed')
            raise
        self.result.update(component_status='verified', status='native-component-verified')

    def execute_component(self):
        self.stage = 'native-component-preflight'
        facts = json.loads((self.evidence / 'runtime-facts.json').read_text())
        base.validate_runtime(facts)
        base.verify_loopback_listener(Path('/proc/net/tcp').read_text(), Path('/proc/net/tcp6').read_text())
        adb = [str(self.sdk / 'platform-tools/adb'), '-P', ADB_PORT, '-s', SERIAL]
        if self.command(adb + ['get-state'], 'component-device-state.txt') != 'device':
            raise base.Blocked('COMPONENT_DEVICE_NOT_READY')
        binary_bytes = self.binary.read_bytes()
        if hashlib.sha256(binary_bytes).hexdigest() != self.identity['binary_sha256']:
            raise base.Blocked('COMPONENT_BINARY_CHANGED_BEFORE_TRANSFER')
        remote_dir = '/data/local/tmp/sign-flite-' + self.identity['binary_sha256'][:16]
        remote_binary = remote_dir + '/private_tts_cli_probe'
        self.command(adb + ['shell', 'mkdir', remote_dir], 'component-mkdir.txt')
        self.stage = 'native-component-transfer'
        self.command(adb + ['push', str(self.binary), remote_binary], 'component-push.txt', seconds=30)
        expected_hash = self.identity['binary_sha256']
        def verify_guest_bytes():
            line = self.command(adb + ['shell', 'sha256sum', remote_binary], 'component-guest-hash.txt')
            match = re.fullmatch(r'([0-9a-f]{64})\s+(.+)', line)
            if not match or match[1] != expected_hash or match[2] != remote_binary:
                raise base.Blocked('COMPONENT_GUEST_HASH_MISMATCH')
        verify_guest_bytes()
        self.command(adb + ['shell', 'chmod', '700', remote_binary], 'component-mode.txt')
        self.stage = 'native-component-execution'
        text = self.command(adb + ['shell', '-T', remote_binary], 'component-raw-output.txt', seconds=COMPONENT_TIMEOUT)
        metrics = validate_metrics(text, int(facts['page_size']))
        verify_guest_bytes()
        self.command(adb + ['shell', 'rm', remote_binary], 'component-remove.txt')
        self.command(adb + ['shell', 'rmdir', remote_dir], 'component-rmdir.txt')
        self.result['component_evidence'] = {'identity': self.identity, 'serial': SERIAL,
                                             'runtime': facts, 'metrics': metrics,
                                             'guest_binary_removed': True}
        # On any failure, base cleanup stops only owned emulator/ADB process groups,
        # destroys the disposable guest and removes its isolated SDK/home/AVD tree.


def preflight(env, root):
    policy(env)
    for ref, expected in [('HEAD', env['PROBE_COMMIT']), ('HEAD^', PARENT)]:
        if subprocess.check_output(['git', 'rev-parse', ref], text=True).strip() != expected:
            raise base.Blocked('COMPONENT_CHECKOUT_PROVENANCE_MISMATCH')
    return verify_artifacts(root)


def main():
    root = Path(__file__).resolve().parents[2]
    binary, identity = preflight(os.environ, root)
    if sys.argv[1:] == ['--preflight']:
        print('REVIEWED_COMPONENT_ARTIFACTS_VERIFIED')
        return 0
    if sys.argv[1:]:
        raise base.Blocked('UNSUPPORTED_COMPONENT_ARGUMENT')
    base.select_cell(os.environ['PROBE_CELL'])
    temp_root = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    evidence = temp_root / 'sign-flite-component-evidence'
    evidence.mkdir(exist_ok=False)
    p = ComponentProbe(evidence, temp_root, binary, identity)
    def interrupted(signum, frame):
        raise base.Blocked('INTERRUPTED:' + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        p.execute()
    except Exception as e:
        p.result.update(boundary=p.stage, reason=str(e))
        if p.result['environment_status'] == 'not-run':
            p.result['environment_status'] = 'failed'
    finally:
        try:
            p.cleanup()
        except Exception:
            p.result.update(status='cleanup-failed', cleanup_errors=['owned-environment-cleanup-failed'])
        p.write('result.json', json.dumps(p.result, sort_keys=True))
        # Never print raw CLI stdout/stderr, PCM, SDK logs, auth files or ADB logs.
        print(json.dumps(p.result, sort_keys=True))
    return 0 if (p.result['status'] == 'native-component-verified' and
                 p.result['component_status'] == 'verified' and
                 p.result['environment_status'] == 'verified' and
                 not p.result.get('cleanup_errors')) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except base.Blocked as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)

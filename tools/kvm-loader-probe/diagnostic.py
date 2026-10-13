#!/usr/bin/env python3
"""One read-only CLI/loader diagnosis; no AVD, ADB, KVM or apt operation."""
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'kvm-boot-probe'))
import probe

BRANCH = 'refs/heads/probe/sign-kvm-loader-20261013'
PARENT = '37d0be76ba4f7fa30ab3c913261375d8d866b853'


def policy(env):
    expected = dict(PROBE_REPOSITORY_PRIVATE='false', PROBE_REPOSITORY_VISIBILITY='public',
                    GITHUB_REPOSITORY='Hatsunama/sign-to-speak', GITHUB_ACTIONS='true',
                    RUNNER_ENVIRONMENT='github-hosted', GITHUB_EVENT_NAME='push',
                    GITHUB_REF=BRANCH, ImageOS='ubuntu24', RUNNER_OS='Linux', RUNNER_ARCH='X64')
    for key, value in expected.items():
        if env.get(key) != value:
            raise probe.Blocked('DIAGNOSTIC_POLICY:' + key)
    if not re.fullmatch(r'[a-f0-9]{40}', env.get('PROBE_COMMIT', '')):
        raise probe.Blocked('DIAGNOSTIC_POLICY:commit')


def classify(text):
    # Emit only library basenames and version identifiers, never arbitrary output.
    missing = re.findall(r'([A-Za-z0-9_+.-]+\.so(?:\.[A-Za-z0-9_.-]+)*)\s*=>\s*not found', text)
    missing += re.findall(r'error while loading shared libraries:\s*([A-Za-z0-9_+.-]+\.so(?:\.[A-Za-z0-9_.-]+)*):', text)
    versions = re.findall(r"version [`']([A-Za-z0-9_.-]+)['`] not found", text)
    return {'missing_libraries': sorted(set(missing)), 'missing_versions': sorted(set(versions))}


class NoDeviceLease:
    def acquire(self):
        return {'mode': 'diagnostic-only-no-kvm-access'}
    def release(self):
        pass


def main():
    policy(os.environ)
    for ref, expected in [('HEAD', os.environ['PROBE_COMMIT']), ('HEAD^', PARENT)]:
        if subprocess.check_output(['git', 'rev-parse', ref], text=True).strip() != expected:
            raise probe.Blocked('DIAGNOSTIC_SOURCE_MISMATCH')
    probe.PACKAGES = {'emulator': '37.2.12'}
    probe.TOTAL_SECONDS = 8 * 60
    root = Path(os.environ['RUNNER_TEMP']).resolve(strict=True)
    evidence = root / 'sign-kvm-loader-evidence'
    evidence.mkdir(exist_ok=False)
    p = probe.Probe(evidence, root)
    p.kvm = NoDeviceLease()
    facts = {'scope': 'loader-diagnostic-only', 'commit': os.environ['PROBE_COMMIT'], 'status': 'blocked'}
    def interrupted(signum, frame):
        raise probe.Blocked('INTERRUPTED:' + str(signum))
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    try:
        p.setup()
        p.stage = 'package-metadata'
        listing = p.run([p.sdkmanager, '--list', '--channel=0', '--sdk_root=' + str(p.sdk)], 'available.txt', seconds=90)
        probe.parse_available(listing)
        p.stage = 'emulator-installation'
        p.run([p.sdkmanager, '--channel=0', '--sdk_root=' + str(p.sdk), 'emulator'], 'sdk-install.txt', seconds=180)
        props = probe.properties(p.sdk / 'emulator/source.properties')
        if props.get('Pkg.Revision') != '37.2.12':
            raise probe.Blocked('EMULATOR_REVISION_DRIFT')
        before = json.loads((evidence / 'runner-license-marker-hashes.json').read_text())
        after = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in (p.sdk / 'licenses').glob('*') if f.is_file()}
        if before != after:
            raise probe.Blocked('LICENSE_MARKERS_CHANGED')
        p.stage = 'read-only-loader-inspection'
        binaries = ['emulator', 'qemu/linux-x86_64/qemu-system-x86_64']
        facts['binaries'] = {}
        for index, relative in enumerate(binaries):
            binary = p.sdk / 'emulator' / relative
            if not binary.is_file():
                raise probe.Blocked('EXPECTED_OFFICIAL_BINARY_MISSING')
            p.run(['ldd', str(binary)], f'ldd-{index}.txt', allow_failure=True)
            detail = classify((evidence / f'ldd-{index}.txt').read_text(errors='replace'))
            detail['sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
            facts['binaries'][relative] = detail
        p.run([str(p.sdk / 'emulator/emulator'), '-version'], 'emulator-version.txt', allow_failure=True)
        facts['version_diagnostic'] = classify((evidence / 'emulator-version.txt').read_text(errors='replace'))
        facts['status'] = 'diagnosis-collected'
    except Exception as e:
        facts.update(status='blocked', stage=p.stage, reason=str(e))
    finally:
        p.cleanup()
        facts['cleanup_errors'] = p.result.get('cleanup_errors', [])
        if facts['cleanup_errors']:
            facts['status'] = 'cleanup-failed'
        print(json.dumps(facts, sort_keys=True))
        for name in ('commands.jsonl', 'resources.jsonl'):
            f = evidence / name
            if f.is_file():
                print(name + '\n' + f.read_text()[-16000:])
    return 0 if facts['status'] == 'diagnosis-collected' else 1


if __name__ == '__main__':
    sys.exit(main())
